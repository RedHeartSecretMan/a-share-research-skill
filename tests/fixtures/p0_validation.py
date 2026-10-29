"""Independent P0 validation profiles.

This module is deliberately a test fixture seam: it owns request builders and
contract assertions without importing any ``test_*`` module.  The profiles use
public request envelopes and classify source access separately from offline
installation or fixture evidence.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
_LOCKED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)"
    r'(?:\s*;\s*sys_platform\s*==\s*"([^"]+)")?$'
)

P0_TASKS = {
    "security_identity",
    "market_trend",
    "security_valuation",
    "valuation_compare",
    "research_content",
    "capital_events",
    "market_signals",
    "single_security_research",
    "market_overview_research",
    "etf_market",
}

REPRESENTATIVE_TASKS = {
    "security_identity",
    "single_security_research",
    "market_overview_research",
    "etf_market",
}

_P1_OR_P2_TASKS = {
    "market_screen_research",
    "etf_options",
    "intraday_market_signal",
    "intraday_replay",
}

UNIFIED_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "research_anchor",
        "plan",
        "capability_registry",
        "coverage",
        "observations",
        "claims",
        "calculations",
        "conflicts",
        "limitations",
        "source_errors",
        "task_views",
        "briefs",
    }
)
EVIDENCE_LEVELS = frozenset({"offline", "installation", "live"})


@dataclass(frozen=True)
class ValidationProfile:
    """Small profile interface shared by core and copied-installed checks."""

    name: str
    tasks: tuple[str, ...]
    hard_dependencies: tuple[tuple[str, str], ...] = ()


CORE_PROFILE = ValidationProfile(
    name="core",
    tasks=(
        "security_identity",
        "market_trend",
        "security_valuation",
        "valuation_compare",
        "research_content",
        "capital_events",
        "market_signals",
        "single_security_research",
        "market_overview_research",
        "etf_market",
    ),
)

# The installed profile intentionally repeats only the P0 public paths.  It is
# a copied Skill check, not a dependency carrier for screening or extensions.
INSTALLED_PROFILE = ValidationProfile(name="installed", tasks=CORE_PROFILE.tasks)


def validate_profile_dependencies(profile: ValidationProfile) -> tuple[str, ...]:
    """Return stable violations instead of silently importing P1/P2 profiles."""

    violations: list[str] = []
    if not set(profile.tasks).issubset(P0_TASKS):
        violations.append("profile contains a non-P0 task")
    if set(profile.tasks) & _P1_OR_P2_TASKS:
        violations.append("profile hard-depends on a P1/P2 task")
    for owner, dependency in profile.hard_dependencies:
        if owner not in profile.tasks:
            violations.append(f"dependency owner is outside profile: {owner}")
        if dependency not in profile.tasks:
            violations.append(f"dependency target is outside profile: {dependency}")
        if dependency in _P1_OR_P2_TASKS:
            violations.append(f"P1/P2 dependency is forbidden: {dependency}")
    return tuple(violations)


def _policy(*, allow_experimental: bool = True) -> dict[str, bool]:
    return {
        "allow_experimental": allow_experimental,
        "allow_credentials": False,
        "allow_fallback": False,
    }


def build_core_requests() -> dict[str, dict[str, Any]]:
    """Build one public request for every P0 profile path without source calls."""

    common_subject = {"clue": "工业富联", "issuer_security_class_count": 1}
    return {
        "security_identity": {
            "schema_version": "1.0",
            "task_type": "security_identity",
            "subjects": [{"clue": "600519"}],
            "as_of": "2026-08-03",
            "window": None,
            "parameters": {},
            "source_policy": _policy(),
        },
        "single_security_research": {
            "schema_version": "1.0",
            "task_type": "research_workflow",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": "2026-08-03",
            "window": None,
            "parameters": {
                "workflow": {"id": "single_security_research", "version": "1.0"},
                "inputs": {"target_pe": "30"},
            },
            "source_policy": _policy(),
        },
        "market_trend": {
            "schema_version": "1.0",
            "task_type": "market_trend",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": "2026-08-03",
            "window": {"trading_days": 10},
            "parameters": {"adjustment": "unadjusted"},
            "source_policy": _policy(),
        },
        "security_valuation": {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [common_subject],
            "as_of": "2026-08-03",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": _policy(),
        },
        "valuation_compare": {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                common_subject,
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
            ],
            "as_of": "2026-08-03",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": _policy(),
        },
        "research_content": {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [],
            "as_of": "2026-08-03",
            "window": {
                "published_from": "2026-07-01",
                "published_to": "2026-08-03",
            },
            "parameters": {
                "material_types": ["research_report"],
                "query": ["研究"],
                "limit": 20,
            },
            "source_policy": _policy(),
        },
        "capital_events": {
            "schema_version": "1.0",
            "task_type": "capital_events",
            "subjects": [common_subject],
            "as_of": "2026-08-03",
            "window": {
                "observed_from": "2026-07-01",
                "observed_to": "2026-08-03",
            },
            "parameters": {
                "data_types": ["margin_trading", "shareholder_count", "dividend"],
                "limit": 20,
            },
            "source_policy": _policy(),
        },
        "market_signals": {
            "schema_version": "1.0",
            "task_type": "market_signals",
            "subjects": [],
            "as_of": "2026-08-03",
            "window": {
                "observed_from": "2026-08-01",
                "observed_to": "2026-08-03",
            },
            "parameters": {"signal_types": ["market_heat"], "limit": 20},
            "source_policy": _policy(),
        },
        "market_overview_research": {
            "schema_version": "1.0",
            "task_type": "research_workflow",
            "subjects": [],
            "as_of": "2026-08-03",
            "window": None,
            "parameters": {
                "workflow": {"id": "market_overview_research", "version": "2.0"},
                "inputs": {"completeness_mode": "fast"},
            },
            "source_policy": _policy(),
        },
        "etf_market": {
            "schema_version": "1.0",
            "task_type": "etf_market",
            "subjects": [{"clue": "510050"}],
            "as_of": "2026-08-03",
            "window": None,
            "parameters": {},
            "source_policy": _policy(allow_experimental=True),
        },
    }


def build_representative_requests() -> dict[str, dict[str, Any]]:
    """Select bounded public requests for the copied-installed smoke path."""

    return {
        task: request
        for task, request in build_core_requests().items()
        if task in REPRESENTATIVE_TASKS
    }


def assert_result_contract(
    result: Mapping[str, Any], *, expected_task_type: str | None = None
) -> None:
    """Validate the shared result envelope at the public serialization seam."""

    if set(result) != set(UNIFIED_RESULT_KEYS):
        raise AssertionError(
            f"result keys do not match schema: {sorted(set(result) ^ set(UNIFIED_RESULT_KEYS))}"
        )
    if result.get("schema_version") != "1.0":
        raise AssertionError("result schema_version must be 1.0")
    if result.get("status") not in {"supported", "limited", "blocked"}:
        raise AssertionError("result status must disclose its execution state")
    plan = result.get("plan")
    if not isinstance(plan, Mapping):
        raise AssertionError("result plan must be an object")
    if expected_task_type is not None and plan.get("task_type") != expected_task_type:
        raise AssertionError("result plan task_type does not match request")
    for field in (
        "observations",
        "claims",
        "calculations",
        "conflicts",
        "limitations",
        "source_errors",
        "briefs",
    ):
        if not isinstance(result.get(field), list):
            raise AssertionError(f"result {field} must be an array")
    for field in ("coverage", "capability_registry", "task_views"):
        if not isinstance(result.get(field), Mapping):
            raise AssertionError(f"result {field} must be an object")


def assert_evidence_boundary(
    installation_health: Mapping[str, Any], result: Mapping[str, Any]
) -> None:
    """Keep installation, offline and live evidence distinct at the seam."""

    policy = installation_health.get("source_policy")
    if not isinstance(policy, Mapping) or any(
        policy.get(key) != "not_checked"
        for key in ("source_accessibility", "credentials", "evidence_adoption")
    ):
        raise AssertionError("installation health must not claim source evidence")

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            level = value.get("evidence_level")
            if level is not None and level not in EVIDENCE_LEVELS:
                raise AssertionError(f"unknown evidence level: {level}")
            if level == "live":
                raise AssertionError("offline profile must not claim live evidence")
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(result)


def copy_skill(destination: Path) -> Path:
    """Copy only the distributable Skill tree into a clean caller directory."""

    destination = destination.resolve()
    shutil.copytree(
        SKILL_ROOT,
        destination,
        ignore=shutil.ignore_patterns(".state", ".runtime", "__pycache__", "*.pyc"),
    )
    return destination


def _locked_requirements() -> list[tuple[str, str]]:
    requirements: list[tuple[str, str]] = []
    for raw in (
        (SKILL_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCKED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise AssertionError(f"unexpected lock syntax: {line}")
        distribution, version, platform = match.groups()
        if platform is None or platform == sys.platform:
            requirements.append((distribution, version))
    return requirements


def _fake_wheelhouse(root: Path) -> Path:
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir()
    import_roots = {
        "python-dateutil": "dateutil",
        "py-mini-racer": "py_mini_racer",
        "typing-extensions": "typing_extensions",
    }
    for distribution, version in _locked_requirements():
        normalized = distribution.replace("-", "_")
        import_root = import_roots.get(distribution, normalized)
        files = {f"{import_root}/__init__.py": ""}
        if distribution == "mootdx":
            files["mootdx/quotes.py"] = (
                "class Quotes:\n"
                "    @staticmethod\n"
                "    def factory(*args, **kwargs):\n"
                "        return object()\n"
            )
        dist_info = f"{normalized}-{version}.dist-info"
        files[f"{dist_info}/METADATA"] = (
            f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n"
        )
        files[f"{dist_info}/WHEEL"] = (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        files[f"{dist_info}/top_level.txt"] = f"{import_root}\n"
        record = "".join(f"{path},,\n" for path in files)
        record += f"{dist_info}/RECORD,,\n"
        wheel = wheelhouse / f"{normalized}-{version}-py3-none-any.whl"
        with ZipFile(wheel, "w") as archive:
            for path, contents in files.items():
                archive.writestr(path, contents)
            archive.writestr(f"{dist_info}/RECORD", record)
    return wheelhouse


def install_copied_skill(root: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Install a copied Skill with an offline lock and read health back."""

    installed = copy_skill(root / "installed" / "a-share-research")
    wheelhouse = _fake_wheelhouse(root)
    completed = subprocess.run(
        [sys.executable, "-I", str(installed / "scripts" / "install.py")],
        cwd=root,
        env={
            "PATH": str(Path(sys.executable).parent),
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(wheelhouse),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    health_process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(installed / "scripts" / "installation_health.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    health = json.loads(health_process.stdout)
    return installed, health, {"install": completed, "health": health_process}


def run_copied_request(
    installed: Path, root: Path, request: Mapping[str, Any]
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
    """Invoke one public request through the copied Skill entrypoint."""

    request_path = root / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(installed / "scripts" / "entrypoint.py"),
            "run",
            "--request",
            str(request_path),
        ],
        cwd=root,
        env={
            "PATH": str(Path(sys.executable).parent),
            # The profile intentionally exercises the public request seam with
            # source access blocked; this cannot be mistaken for live evidence.
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise AssertionError("public request did not return an object")
    return result, completed
