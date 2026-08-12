"""Fail-closed health facts for the installed core runtime dependencies."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.metadata
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, cast

from _runtime_path import bundled_runtime_path

DependencyStatus = Literal["complete", "missing", "version_mismatch", "load_failed"]


@dataclass(frozen=True)
class RuntimeDependency:
    """One locked dependency shared by released core source operations."""

    distribution: str
    required_version: str
    modules: tuple[str, ...]
    operation_ids: tuple[str, ...]


MOOTDX_RUNTIME_DEPENDENCY = RuntimeDependency(
    distribution="mootdx",
    required_version="0.11.7",
    modules=("mootdx.quotes",),
    operation_ids=(
        "mootdx_f10@1",
        "mootdx_intraday_replay@1",
        "tongdaxin_intraday_snapshot@1",
    ),
)
_CORE_OPERATION_LOADS = {
    "mootdx_f10@1": (
        "a_share_research.communication_sources",
        "MootdxF10Operation",
    ),
    "mootdx_intraday_replay@1": (
        "a_share_research.intraday_replay_mootdx",
        "MootdxIntradayReplayOperation",
    ),
    "tongdaxin_intraday_snapshot@1": (
        "a_share_research.intraday_sources",
        "TongdaxinIntradayOperation",
    ),
}
_PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)"
    r'(?:\s*;\s*sys_platform\s*==\s*"([^"]+)")?$'
)


def check_installation(
    *,
    version: Callable[[str], str] | None = None,
    load_module: Callable[[str], object] | None = None,
) -> dict[str, object]:
    """Return bounded, non-secret dependency-load facts without probing sources."""

    version_lookup = version or _local_runtime_version
    dependency_loader = load_module or _load_local_runtime_module
    operation_loader = load_module or _load_bundled_operation_module
    dependency_facts: list[dict[str, object]] = []
    operation_facts: list[dict[str, str]] = []
    complete = True
    try:
        requirements = _locked_runtime_dependencies()
    except (OSError, ValueError):
        requirements = ()
        complete = False
    for requirement in requirements:
        installed_version: str | None
        status: DependencyStatus
        try:
            installed_version = version_lookup(requirement.distribution)
        except importlib.metadata.PackageNotFoundError:
            installed_version = None
            status = "missing"
        except Exception:
            installed_version = None
            status = "load_failed"
        else:
            installed_version = _safe_version(installed_version)
            if installed_version != requirement.required_version:
                status = "version_mismatch"
            else:
                try:
                    if version is None:
                        _verify_local_runtime_distribution(
                            requirement.distribution,
                            dependency_loader,
                        )
                    for module in requirement.modules:
                        dependency_loader(module)
                except Exception:
                    status = "load_failed"
                else:
                    status = "complete"
        if status != "complete":
            complete = False
        dependency_facts.append(
            {
                "distribution": requirement.distribution,
                "required_version": requirement.required_version,
                "installed_version": installed_version,
                "status": status,
                "modules": list(requirement.modules),
                "operation_ids": list(requirement.operation_ids),
            }
        )
    dependency_complete = complete
    for operation_id, (module, class_name) in _CORE_OPERATION_LOADS.items():
        if not dependency_complete:
            operation_status = "installation_incomplete"
        else:
            try:
                loaded = operation_loader(module)
                operation = getattr(loaded, class_name)
                if getattr(operation, "operation_id", None) != operation_id:
                    raise ValueError(
                        "operation identity does not match health manifest"
                    )
            except Exception:
                operation_status = "load_failed"
                complete = False
            else:
                operation_status = "complete"
        operation_facts.append(
            {"operation_id": operation_id, "status": operation_status}
        )
    return {
        "schema_version": "1.0",
        "status": "complete" if complete else "installation_incomplete",
        "dependencies": dependency_facts,
        "operations": operation_facts,
        "source_policy": {
            "source_accessibility": "not_checked",
            "credentials": "not_checked",
            "evidence_adoption": "not_checked",
        },
    }


def dependency_available(distribution: str, required_version: str) -> bool:
    """Return whether one declared dependency has its locked version and loads."""

    health = check_installation()
    if health["status"] != "complete":
        return False
    facts = cast(list[dict[str, object]], health["dependencies"])
    for fact in facts:
        if (
            fact["distribution"] == distribution
            and fact["required_version"] == required_version
        ):
            return bool(fact["status"] == "complete")
    return False


def _safe_version(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if not value.isascii() or any(character.isspace() for character in value):
        return None
    return value


def _local_runtime_version(distribution: str) -> str:
    return _local_runtime_distribution(distribution).version


def _local_runtime_distribution(
    distribution: str,
) -> importlib.metadata.Distribution:
    runtime = _local_runtime_path()
    normalized_name = _normalized_distribution_name(distribution)
    for installed in importlib.metadata.distributions(path=[str(runtime)]):
        installed_name = installed.metadata.get("Name")
        if (
            isinstance(installed_name, str)
            and _normalized_distribution_name(installed_name) == normalized_name
        ):
            return installed
    raise importlib.metadata.PackageNotFoundError(distribution)


def _verify_local_runtime_distribution(
    distribution: str,
    load_module: Callable[[str], object],
) -> None:
    runtime = _local_runtime_path()
    installed = _local_runtime_distribution(distribution)
    files = installed.files
    if not files:
        raise ModuleNotFoundError(distribution)
    import_roots: set[str] = set()
    for package_path in files:
        relative = Path(str(package_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ModuleNotFoundError(distribution)
        located = Path(str(installed.locate_file(package_path)))
        if not _is_within(located, runtime):
            raise ModuleNotFoundError(distribution)
        if relative.suffix != ".pyc" and "__pycache__" not in relative.parts:
            if not located.exists():
                raise ModuleNotFoundError(distribution)
        if (
            len(relative.parts) == 1
            and relative.suffix == ".py"
            and relative.stem.isidentifier()
        ):
            import_roots.add(relative.stem)
        elif (
            len(relative.parts) >= 2
            and relative.parts[1] == "__init__.py"
            and relative.parts[0].isidentifier()
        ):
            import_roots.add(relative.parts[0])
    declared_roots = installed.read_text("top_level.txt")
    if declared_roots is not None:
        for line in declared_roots.splitlines():
            root = line.strip()
            if root:
                if not root.isidentifier():
                    raise ModuleNotFoundError(distribution)
                import_roots.add(root)
    if not import_roots:
        raise ModuleNotFoundError(distribution)
    for module in sorted(import_roots):
        load_module(module)


def _load_local_runtime_module(module: str) -> object:
    runtime = _local_runtime_path()
    parts = module.split(".")
    search_path: list[str] = [str(runtime)]
    spec = None
    for index in range(len(parts)):
        qualified_name = ".".join(parts[: index + 1])
        spec = importlib.machinery.PathFinder.find_spec(qualified_name, search_path)
        if spec is None:
            raise ModuleNotFoundError(qualified_name)
        if index < len(parts) - 1:
            locations = spec.submodule_search_locations
            if locations is None:
                raise ModuleNotFoundError(module)
            search_path = list(locations)
    loaded = importlib.import_module(module)
    origin = getattr(loaded, "__file__", None)
    if not isinstance(origin, str) or not _is_within(Path(origin), runtime):
        raise ModuleNotFoundError(module)
    return loaded


def _load_bundled_operation_module(module: str) -> object:
    loaded = importlib.import_module(module)
    origin = getattr(loaded, "__file__", None)
    scripts = Path(__file__).resolve().parents[1]
    if not isinstance(origin, str) or not _is_within(Path(origin), scripts):
        raise ModuleNotFoundError(module)
    return loaded


def _local_runtime_path() -> Path:
    return bundled_runtime_path()


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _locked_runtime_dependencies() -> tuple[RuntimeDependency, ...]:
    lock = Path(__file__).resolve().parents[2] / "requirements.lock"
    requirements: list[RuntimeDependency] = []
    for raw_line in lock.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PINNED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise ValueError("runtime lock contains an unpinned requirement")
        distribution, required_version, required_platform = match.groups()
        if required_platform not in {None, "darwin", "linux", "win32"}:
            raise ValueError("runtime lock contains an unsupported platform marker")
        if required_platform is not None and required_platform != sys.platform:
            continue
        requirements.append(
            RuntimeDependency(
                distribution=distribution,
                required_version=required_version,
                modules=(
                    MOOTDX_RUNTIME_DEPENDENCY.modules
                    if distribution == MOOTDX_RUNTIME_DEPENDENCY.distribution
                    else ()
                ),
                operation_ids=(
                    MOOTDX_RUNTIME_DEPENDENCY.operation_ids
                    if distribution == MOOTDX_RUNTIME_DEPENDENCY.distribution
                    else ()
                ),
            )
        )
    if not requirements:
        raise ValueError("runtime lock is empty")
    if (
        sum(
            item.distribution == MOOTDX_RUNTIME_DEPENDENCY.distribution
            for item in requirements
        )
        != 1
    ):
        raise ValueError("runtime lock must contain the formal mootdx dependency once")
    return tuple(requirements)
