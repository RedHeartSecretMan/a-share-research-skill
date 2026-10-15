from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
_LOCKED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)"
    r'(?:\s*;\s*sys_platform\s*==\s*"([^"]+)")?$'
)
_WORKFLOW_SNAPSHOT_MAX_DEPTH = 32
_WORKFLOW_SNAPSHOT_MAX_ITEMS = 10_000
_FUTURE_SCREEN_STRUCTURAL_PROBE_FIXED_ITEMS = 23


def _applicable_locked_requirements(
    skill_root: Path,
    *,
    platform: str = sys.platform,
) -> list[tuple[str, str]]:
    requirements: list[tuple[str, str]] = []
    for raw_line in (
        (skill_root / "requirements.lock").read_text(encoding="utf-8").splitlines()
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCKED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise AssertionError(f"unexpected test lock syntax: {line}")
        distribution, version, required_platform = match.groups()
        if required_platform is None or required_platform == platform:
            requirements.append((distribution, version))
    return requirements


def _write_fake_locked_wheelhouse(
    skill_root: Path,
    wheelhouse: Path,
    *,
    module_sources: dict[str, str] | None = None,
) -> None:
    module_sources = module_sources or {}
    wheelhouse.mkdir()
    for distribution, version in _applicable_locked_requirements(skill_root):
        normalized = distribution.replace("-", "_")
        import_root = {
            "python-dateutil": "dateutil",
            "py-mini-racer": "py_mini_racer",
            "typing-extensions": "typing_extensions",
        }.get(distribution, normalized)
        package_source = module_sources.get(distribution, "")
        if distribution == "py-mini-racer" and distribution not in module_sources:
            package_source = "class MiniRacer:\n    pass\n"
        files = {
            f"{import_root}/__init__.py": package_source,
        }
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
        with zipfile.ZipFile(wheel, "w") as archive:
            for path, contents in files.items():
                archive.writestr(path, contents)
            archive.writestr(f"{dist_info}/RECORD", record)


def _install_fake_locked_runtime(
    installed: Path,
    temporary_root: Path,
    *,
    module_sources: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    wheelhouse = temporary_root / "wheelhouse"
    _write_fake_locked_wheelhouse(
        installed,
        wheelhouse,
        module_sources=module_sources,
    )
    return subprocess.run(
        [sys.executable, "-I", str(installed / "scripts" / "install.py")],
        cwd=temporary_root,
        env={
            "PATH": str(Path(sys.executable).parent),
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(wheelhouse),
        },
        check=False,
        capture_output=True,
        text=True,
    )


class SkillDistributionTests(unittest.TestCase):
    def test_domain_docs_separate_installation_health_from_source_facts(self) -> None:
        context = Path(REPOSITORY_ROOT, "CONTEXT.md").read_text(encoding="utf-8")
        adr = Path(
            REPOSITORY_ROOT,
            "docs",
            "adr",
            "0018-default-install-released-core-dependencies.md",
        ).read_text(encoding="utf-8")

        self.assertIn("**安装完整性**:", context)
        for distinction in ("来源可访问", "凭据", "evidence adoption"):
            with self.subTest(distinction=distinction):
                self.assertIn(distinction, context)
        self.assertIn("status: accepted", adr)
        self.assertIn("supersedes: ADR-0016", adr)
        self.assertIn("installation_incomplete", adr)
        self.assertIn("候选或未发布 operation", adr)

    def test_installer_consumes_a_fully_pinned_runtime_lock(self) -> None:
        lock_path = SKILL_ROOT / "requirements.lock"
        locked_lines = [
            line.strip()
            for line in lock_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertTrue(locked_lines)
        for requirement in locked_lines:
            with self.subTest(requirement=requirement):
                self.assertIsNotNone(_LOCKED_REQUIREMENT.fullmatch(requirement))
        self.assertIn("mootdx==0.11.7", locked_lines)
        installer = (SKILL_ROOT / "scripts" / "install.py").read_text(encoding="utf-8")
        self.assertIn('skill_root / "requirements.lock"', installer)
        self.assertNotIn('skill_root / "requirements.txt"', installer)

        health = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(SKILL_ROOT / "scripts" / "installation_health.py"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        facts = json.loads(health.stdout)
        self.assertEqual(
            [
                (item["distribution"], item["required_version"])
                for item in facts["dependencies"]
            ],
            _applicable_locked_requirements(SKILL_ROOT),
        )

    def test_copied_install_bootstraps_locked_core_dependency_and_checks_health(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            completed = _install_fake_locked_runtime(installed, temporary_root)

            checked = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )
            installed_requirements = (installed / "requirements.lock").read_text(
                encoding="utf-8"
            )
            request = temporary_root / "source-failure-request.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "task_type": "security_identity",
                        "subjects": [{"clue": "600519"}],
                        "as_of": "2026-08-01",
                        "window": None,
                        "parameters": {},
                        "source_policy": {
                            "allow_experimental": True,
                            "allow_credentials": False,
                            "allow_fallback": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            external_failure = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(installed / "scripts" / "entrypoint.py"),
                    "run",
                    "--request",
                    str(request),
                ],
                cwd=temporary_root,
                env={
                    "PATH": str(Path(sys.executable).parent),
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                    "ALL_PROXY": "http://127.0.0.1:1",
                    "NO_PROXY": "",
                },
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(checked.stderr, "")
        self.assertEqual(completed.stdout, checked.stdout)
        health = json.loads(checked.stdout)
        self.assertEqual(health["status"], "complete")
        self.assertEqual(
            health["operations"],
            [
                {"operation_id": "mootdx_f10@1", "status": "complete"},
                {
                    "operation_id": "mootdx_intraday_replay@1",
                    "status": "complete",
                },
                {
                    "operation_id": "tongdaxin_intraday_snapshot@1",
                    "status": "complete",
                },
            ],
        )
        self.assertIn("mootdx==0.11.7\n", installed_requirements)
        self.assertNotIn(str(REPOSITORY_ROOT), checked.stdout)
        self.assertEqual(external_failure.returncode, 0, external_failure.stderr)
        self.assertEqual(external_failure.stderr, "")
        source_failure_result = json.loads(external_failure.stdout)
        self.assertEqual(source_failure_result["schema_version"], "1.0")
        self.assertEqual(source_failure_result["status"], "blocked")
        self.assertTrue(source_failure_result["source_errors"])

    def test_copied_install_reports_incomplete_core_runtime_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        health = json.loads(completed.stdout)
        self.assertEqual(health["schema_version"], "1.0")
        self.assertEqual(health["status"], "installation_incomplete")
        self.assertEqual(
            len(health["dependencies"]),
            len(_applicable_locked_requirements(SKILL_ROOT)),
        )
        self.assertEqual(
            {item["status"] for item in health["dependencies"]},
            {"missing"},
        )
        self.assertEqual(
            next(
                item
                for item in health["dependencies"]
                if item["distribution"] == "mootdx"
            ),
            {
                "distribution": "mootdx",
                "required_version": "0.11.7",
                "installed_version": None,
                "status": "missing",
                "modules": ["mootdx.quotes"],
                "operation_ids": [
                    "mootdx_f10@1",
                    "mootdx_intraday_replay@1",
                    "tongdaxin_intraday_snapshot@1",
                ],
            },
        )
        self.assertEqual(
            health["source_policy"],
            {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        )

    def test_copied_health_rejects_exact_dependencies_outside_local_runtime(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            external_site = temporary_root / "external-site"
            external_site.mkdir()
            lock_lines = [
                line.strip()
                for line in (installed / "requirements.lock")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            for requirement in lock_lines:
                distribution, version = requirement.split("==", 1)
                normalized = distribution.replace("-", "_")
                dist_info = external_site / f"{normalized}-{version}.dist-info"
                dist_info.mkdir()
                (dist_info / "METADATA").write_text(
                    "Metadata-Version: 2.1\n"
                    f"Name: {distribution}\n"
                    f"Version: {version}\n",
                    encoding="utf-8",
                )
            mootdx = external_site / "mootdx"
            mootdx.mkdir()
            (mootdx / "__init__.py").write_text("", encoding="utf-8")
            (mootdx / "quotes.py").write_text(
                "class Quotes:\n    pass\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                env={
                    "PATH": str(Path(sys.executable).parent),
                    "PYTHONPATH": str(external_site),
                },
                check=False,
                capture_output=True,
                text=True,
            )

        health = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(health["status"], "installation_incomplete")
        self.assertEqual(
            {item["status"] for item in health["dependencies"]},
            {"missing"},
        )

    def test_copied_health_rejects_runtime_symlinked_outside_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            installation = _install_fake_locked_runtime(installed, temporary_root)
            runtime = installed / ".runtime"
            external_runtime = temporary_root / "external-runtime"
            runtime.rename(external_runtime)
            runtime.symlink_to(external_runtime, target_is_directory=True)

            command = "\n".join(
                (
                    "import runpy, sys",
                    f"sys.path.insert(0, {str(external_runtime)!r})",
                    "import mootdx.quotes",
                    (
                        "runpy.run_path("
                        f"{str(installed / 'scripts' / 'installation_health.py')!r}, "
                        "run_name='__main__')"
                    ),
                )
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-c", command],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installation.returncode, 0, installation.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout)["status"],
            "installation_incomplete",
        )

    def test_copied_installer_rejects_runtime_symlink_without_external_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            external_runtime = temporary_root / "external-runtime"
            external_runtime.mkdir()
            (installed / ".runtime").symlink_to(
                external_runtime,
                target_is_directory=True,
            )

            completed = _install_fake_locked_runtime(installed, temporary_root)
            externally_written = list(external_runtime.iterdir())

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout)["status"],
            "installation_incomplete",
        )
        self.assertEqual(externally_written, [])

    def test_copied_health_rejects_preloaded_external_operation_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            installation = _install_fake_locked_runtime(installed, temporary_root)
            external_module = temporary_root / "external-operation.py"
            external_module.write_text("external operation", encoding="utf-8")
            command = "\n".join(
                (
                    "import runpy, sys, types",
                    f"sys.path.insert(0, {str(installed / 'scripts')!r})",
                    "from _runtime_path import add_bundled_runtime",
                    "add_bundled_runtime()",
                    "import a_share_research.installation_health",
                    (
                        "module = types.ModuleType("
                        "'a_share_research.communication_sources')"
                    ),
                    f"module.__file__ = {str(external_module)!r}",
                    (
                        "module.MootdxF10Operation = type("
                        "'MootdxF10Operation', (), "
                        "{'operation_id': 'mootdx_f10@1'})"
                    ),
                    ("sys.modules['a_share_research.communication_sources'] = module"),
                    (
                        "runpy.run_path("
                        f"{str(installed / 'scripts' / 'installation_health.py')!r}, "
                        "run_name='__main__')"
                    ),
                )
            )

            completed = subprocess.run(
                [sys.executable, "-I", "-S", "-c", command],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installation.returncode, 0, installation.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        health = json.loads(completed.stdout)
        self.assertEqual(health["status"], "installation_incomplete")
        self.assertEqual(health["operations"][0]["status"], "load_failed")

    def test_copied_health_rejects_locked_distribution_missing_installed_code(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            installation = _install_fake_locked_runtime(installed, temporary_root)
            shutil.rmtree(installed / ".runtime" / "httpx")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installation.returncode, 0, installation.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        health = json.loads(completed.stdout)
        self.assertEqual(health["status"], "installation_incomplete")
        httpx = next(
            item for item in health["dependencies"] if item["distribution"] == "httpx"
        )
        self.assertEqual(httpx["status"], "load_failed")

    def test_copied_health_rejects_locked_file_symlinked_outside_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            installation = _install_fake_locked_runtime(installed, temporary_root)
            module = installed / ".runtime" / "httpx" / "__init__.py"
            external_module = temporary_root / "external-httpx.py"
            external_module.write_text("external code", encoding="utf-8")
            module.unlink()
            module.symlink_to(external_module)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installation.returncode, 0, installation.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        health = json.loads(completed.stdout)
        self.assertEqual(health["status"], "installation_incomplete")
        httpx = next(
            item for item in health["dependencies"] if item["distribution"] == "httpx"
        )
        self.assertEqual(httpx["status"], "load_failed")

    def test_copied_health_sanitizes_bundled_operation_import_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            installation = _install_fake_locked_runtime(installed, temporary_root)
            (
                installed / "scripts" / "a_share_research" / "communication_sources.py"
            ).write_text(
                "raise RuntimeError('private bundled operation path')\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installation.returncode, 0, installation.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        health = json.loads(completed.stdout)
        self.assertEqual(health["status"], "installation_incomplete")
        self.assertNotIn("private bundled operation path", completed.stdout)

    def test_formal_lock_excludes_mootdx_dependencies_not_loaded_by_core_operations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            installation = _install_fake_locked_runtime(installed, temporary_root)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(installation.returncode, 0, installation.stderr)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        health = json.loads(completed.stdout)
        self.assertEqual(health["status"], "complete")
        locked = {item["distribution"] for item in health["dependencies"]}
        self.assertTrue({"py-mini-racer", "prettytable", "wcwidth"}.isdisjoint(locked))

    def test_lock_covers_windows_python312_closure_without_runtime_resolution(
        self,
    ) -> None:
        lock = (SKILL_ROOT / "requirements.lock").read_text(encoding="utf-8")
        installer = (SKILL_ROOT / "scripts" / "install.py").read_text(encoding="utf-8")

        self.assertIn(
            'colorama==0.4.6 ; sys_platform == "win32"',
            lock,
        )
        self.assertIn(
            'tzdata==2026.3 ; sys_platform == "win32"',
            lock,
        )
        self.assertIn('"--no-deps"', installer)
        self.assertEqual(len(_applicable_locked_requirements(SKILL_ROOT)), 18)
        self.assertEqual(
            len(
                _applicable_locked_requirements(
                    SKILL_ROOT,
                    platform="win32",
                )
            ),
            20,
        )
        locked_facts: list[tuple[str, str | None]] = []
        for raw_line in lock.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _LOCKED_REQUIREMENT.fullmatch(line)
            self.assertIsNotNone(match, line)
            assert match is not None
            distribution, _, marker = match.groups()
            locked_facts.append((distribution.casefold(), marker))
        self.assertEqual(len(locked_facts), len(set(locked_facts)))

    def test_copied_health_rejects_unknown_lock_marker_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            with (installed / "requirements.lock").open(
                "a",
                encoding="utf-8",
            ) as lock:
                lock.write('future-package==1.0 ; sys_platform == "plan9"\n')

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(installed / "scripts" / "installation_health.py"),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout)["status"],
            "installation_incomplete",
        )

    def test_repository_publishes_only_the_nested_installable_skill(self) -> None:
        self.assertFalse(Path(REPOSITORY_ROOT, "SKILL.md").exists())

        scripts = Path(SKILL_ROOT, "scripts")
        self.assertTrue(Path(scripts, "entrypoint.py").is_file())
        self.assertFalse(Path(scripts, "a_share_research.py").exists())

        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn("skill/a-share-research", readme)
                self.assertIn("Python 3.12", readme)

        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: a-share-research\n", entry)

        metadata = Path(SKILL_ROOT, "agents", "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "A股研究技能"', metadata)
        self.assertIn("$a-share-research ", metadata)
        self.assertNotIn("$a-share-research-skill", metadata)
        self.assertIn("narrowest supported task", metadata)
        self.assertIn("evidence-backed results and limitations", metadata)

    def test_installation_uses_the_v030_release_tag(self) -> None:
        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn(
                    "git clone --depth 1 --branch v0.3.0 --single-branch ",
                    readme,
                )
                self.assertIn(
                    "https://github.com/RedHeartSecretMan/a-share-research-skill.git",
                    readme,
                )
                self.assertIn("releases/tag/v0.3.0", readme)
                self.assertIn("Release: v0.3.0", readme)
                self.assertNotIn("--branch main --single-branch", readme)

        chinese_readme = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english_readme = Path(REPOSITORY_ROOT, "README_en.md").read_text(
            encoding="utf-8"
        )
        for readme in (chinese_readme, english_readme):
            self.assertIn("<skill-root>/scripts/install.py", readme)
            self.assertNotIn("mootdx==", readme)
            self.assertNotIn("$HOME/.agents/skills", readme)
            self.assertNotIn("Codex", readme)

    def test_v030_readmes_publish_closed_evidence_without_overstating_sources(
        self,
    ) -> None:
        evidence_path = Path(
            REPOSITORY_ROOT,
            "docs",
            "research",
            "v0.3.0-release-probe-evidence.json",
        )
        matrix_path = Path(
            REPOSITORY_ROOT,
            "docs",
            "research",
            "v0.3.0-operation-release-matrix.json",
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

        self.assertEqual(len(evidence["plan_snapshots"]), 27)
        self.assertEqual(len(evidence["live_probes"]), 44)
        self.assertTrue(all(item["attempted"] for item in evidence["live_probes"]))
        self.assertEqual(len(matrix["operations"]), 44)
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"],
            {"denominator": 740, "numerator": 740, "rate": "1.000000"},
        )

        audit = Path(
            REPOSITORY_ROOT,
            "docs",
            "research",
            "v0.3.0-release-audit-2026-08-18.md",
        ).read_text(encoding="utf-8")
        for required in (
            "27/27",
            "44/44",
            "740/740",
            "observed_empty",
            "credential_blocked",
            "83a088111f61c2aa3d29ba70fbe194a152283e65",
            "e9b4fb83fac61ff8b1a22b898a58ae49faf9debb",
            "releases/tag/v0.3.0",
        ):
            self.assertIn(required, audit)

        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn("v0.3.0-release-audit-2026-08-18.md", readme)
                self.assertIn("releases/tag/v0.3.0", readme)
                self.assertNotIn("v0.3.0-release-probe-evidence.json", readme)
                self.assertNotIn("v0.3.0-operation-release-matrix.json", readme)

        self.assertNotIn(
            "不等于“来源成功返回数据”",
            Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "does not mean “the source returned data successfully”",
            Path(REPOSITORY_ROOT, "README_en.md").read_text(encoding="utf-8"),
        )

    def test_release_entry_docs_keep_bilingual_structure_and_valid_local_links(
        self,
    ) -> None:
        chinese_path = Path(REPOSITORY_ROOT, "README.md")
        english_path = Path(REPOSITORY_ROOT, "README_en.md")
        chinese = chinese_path.read_text(encoding="utf-8")
        english = english_path.read_text(encoding="utf-8")
        heading_pattern = re.compile(r"^(#{2,3}) ", re.MULTILINE)
        self.assertEqual(
            heading_pattern.findall(chinese),
            heading_pattern.findall(english),
        )

        link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        documents = (
            chinese_path,
            english_path,
            Path(REPOSITORY_ROOT, "docs", "README.md"),
            Path(SKILL_ROOT, "SKILL.md"),
            *Path(SKILL_ROOT, "references").glob("*.md"),
            *Path(REPOSITORY_ROOT, "examples").rglob("*.md"),
            *Path(REPOSITORY_ROOT, "docs", "research").rglob("*.md"),
        )
        for document in documents:
            content = document.read_text(encoding="utf-8")
            for target in link_pattern.findall(content):
                with self.subTest(document=document.name, target=target):
                    if target.startswith(("https://", "http://", "#")):
                        continue
                    relative_target = target.split("#", 1)[0]
                    self.assertTrue((document.parent / relative_target).exists())

    def test_v030_examples_include_the_intraday_public_seams(self) -> None:
        examples_readme = Path(REPOSITORY_ROOT, "examples", "README.md").read_text(
            encoding="utf-8"
        )
        first_nonblank = next(
            line for line in examples_readme.splitlines() if line.strip()
        )
        self.assertEqual(first_nonblank, "# v0.3.0 中文案例与请求模板")
        self.assertIn("[盘中行情快照](intraday-snapshot.md)", examples_readme)
        self.assertIn(
            "[`intraday-market-snapshot.json`](requests/intraday-market-snapshot.json)",
            examples_readme,
        )

        case = Path(REPOSITORY_ROOT, "examples", "intraday-snapshot.md").read_text(
            encoding="utf-8"
        )
        self.assertTrue(case.startswith("# "))
        for required_text in (
            "entrypoint.py run --request",
            "当前北京时间交易日",
            "limited",
            "blocked",
            "requirements.lock",
            "installation_health.py",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, case)

        request = json.loads(
            Path(
                REPOSITORY_ROOT,
                "examples",
                "requests",
                "intraday-market-snapshot.json",
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(request["schema_version"], "1.0")
        self.assertEqual(request["task_type"], "intraday_market_signal")
        self.assertEqual(request["subjects"], [{"security": "SSE:600519"}])
        self.assertIsNone(request["window"])
        self.assertEqual(request["parameters"], {})
        self.assertTrue(request["source_policy"]["allow_experimental"])
        self.assertFalse(request["source_policy"]["allow_fallback"])

    def test_real_run_example_preserves_actual_public_result_and_boundaries(
        self,
    ) -> None:
        example_root = Path(REPOSITORY_ROOT, "examples", "real-runs")
        request_path = example_root / "market-focus-monitoring-2026-08-19.request.json"
        result_path = example_root / "market-focus-monitoring-2026-08-19.result.json"
        case_path = example_root / "market-focus-monitoring-2026-08-19.md"
        examples_readme = Path(REPOSITORY_ROOT, "examples", "README.md").read_text(
            encoding="utf-8"
        )

        for path in (request_path, result_path, case_path):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertIn(f"real-runs/{path.name}", examples_readme)

        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(request["schema_version"], "1.0")
        self.assertEqual(request["task_type"], "market_signals")
        self.assertEqual(request["subjects"], [])
        self.assertEqual(request["as_of"], "2026-08-19")
        self.assertEqual(request["parameters"]["signal_types"], ["focus_monitoring"])
        self.assertFalse(request["source_policy"]["allow_credentials"])

        raw_result = result_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw_result).hexdigest(),
            "37b30eca9d6058f32378afb7671b89e5e0e20b67ecf4d0e9e80450259c06c34e",
        )
        result = json.loads(raw_result)
        self.assertEqual(
            set(result),
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
            },
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["research_anchor"]["as_of"], "2026-08-19")
        self.assertEqual(len(result["observations"]), 22)
        self.assertEqual(len(result["claims"]), 22)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])

        operations = result["coverage"]["market_signal_observations"]["operations"]
        focus = operations["eastmoney_focus_monitoring@1"]
        self.assertEqual(
            (focus["execution_state"], focus["state"]),
            ("succeeded", "complete"),
        )
        self.assertTrue(
            all(
                operation_id == "eastmoney_focus_monitoring@1"
                or (material["execution_state"], material["state"])
                == ("not_requested", "not_requested")
                for operation_id, material in operations.items()
            )
        )

        coverage = next(
            item
            for item in result["calculations"]
            if item["kind"] == "market_signal_coverage"
        )
        self.assertEqual(
            coverage["payload"],
            {
                "signal_type": "focus_monitoring",
                "state": "observed_nonempty",
                "observation_count": 22,
                "sources": [
                    {
                        "source_operation": "eastmoney_focus_monitoring@1",
                        "state": "observed_nonempty",
                        "provider_total": 22,
                        "pages_collected": 1,
                        "pages_expected": 1,
                    }
                ],
            },
        )
        limitation_codes = {item["code"] for item in result["limitations"]}
        self.assertEqual(
            limitation_codes,
            {
                "availability_time_unknown",
                "experimental_market_signal_sources",
                "monitoring_reason_unverified",
                "provider_watchlist_not_official",
                "security_exchange_unverified",
                "security_type_unverified",
                "no_qualified_independent_fallback",
            },
        )

        lowered_result = raw_result.lower()
        for secret_marker in (
            b"api_key",
            b"authorization",
            b"cookie",
            b"password",
            b"localhost",
            b"127.0.0.1",
        ):
            with self.subTest(secret_marker=secret_marker):
                self.assertNotIn(secret_marker, lowered_result)

        case = case_path.read_text(encoding="utf-8")
        for required_text in (
            "不是离线夹具",
            "实际运行生成",
            "`succeeded`",
            "`complete`",
            "`limited`",
            "22 条",
            "Agent 推断",
            "provider_watchlist_not_official",
            "不是评级、目标价或投资行动建议",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, case)

    def test_historical_v020_release_audit_is_linked_from_both_readmes(self) -> None:
        audit_relative_path = "docs/research/history/v0.2.0-release-audit-2026-08-03.md"
        audit = Path(REPOSITORY_ROOT, audit_relative_path)
        self.assertTrue(audit.is_file())
        self.assertTrue(
            audit.read_text(encoding="utf-8").startswith("# v0.2.0 发布审计")
        )

        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn(audit_relative_path, readme)

    def test_research_docs_separate_current_release_files_from_versioned_history(
        self,
    ) -> None:
        research_root = Path(REPOSITORY_ROOT, "docs", "research")
        current_files = {
            path.name for path in research_root.iterdir() if path.is_file()
        }
        self.assertEqual(
            current_files,
            {
                "README.md",
                "v0.3.0-operation-qualification.json",
                "v0.3.0-operation-release-matrix.json",
                "v0.3.0-operation-release-matrix.md",
                "v0.3.0-release-audit-2026-08-18.md",
                "v0.3.0-release-probe-evidence.json",
                "v0.4.0-operation-qualification.json",
            },
        )

        history_files = {
            path.name
            for path in Path(research_root, "history").iterdir()
            if path.is_file()
        }
        self.assertEqual(len(history_files), 8)
        self.assertTrue(
            all(re.fullmatch(r"v\d+\.\d+\.\d+-.+", name) for name in history_files)
        )
        research_index = Path(research_root, "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "[`v0.4.0-operation-qualification.json`](v0.4.0-operation-qualification.json)",
            research_index,
        )
        self.assertIn("不构成 v0.4.0 发布或联网验证证明", research_index)

        self.assertFalse(any(name.startswith("core-v0.3.0-") for name in current_files))

    def test_examples_index_every_versioned_request_exactly_once(self) -> None:
        request_names = {
            path.name
            for path in Path(REPOSITORY_ROOT, "examples", "requests").glob("*.json")
        }
        examples_readme = Path(REPOSITORY_ROOT, "examples", "README.md").read_text(
            encoding="utf-8"
        )
        indexed_names = re.findall(r"\(requests/([^)]+\.json)\)", examples_readme)

        self.assertEqual(len(request_names), 43)
        self.assertEqual(len(indexed_names), 43)
        self.assertEqual(set(indexed_names), request_names)
        self.assertEqual(len(indexed_names), len(set(indexed_names)))
        self.assertIn("案例标题、问题和解释均使用中文", examples_readme)

        task_types = {
            json.loads(path.read_text(encoding="utf-8"))["task_type"]
            for path in Path(REPOSITORY_ROOT, "examples", "requests").glob("*.json")
        }
        self.assertEqual(
            task_types,
            {
                "security_identity",
                "market_trend",
                "etf_market",
                "etf_options",
                "intraday_market_signal",
                "intraday_replay",
                "security_valuation",
                "valuation_compare",
                "research_content",
                "capital_events",
                "market_signals",
                "research_workflow",
            },
        )

        for case_path in Path(REPOSITORY_ROOT, "examples").glob("*.md"):
            with self.subTest(case=case_path.name):
                case = case_path.read_text(encoding="utf-8")
                self.assertNotIn("mootdx==", case)
                headings = re.findall(r"^#{1,3} (.+)$", case, re.MULTILINE)
                self.assertTrue(headings)
                self.assertTrue(
                    all(re.search(r"[\u4e00-\u9fff]", heading) for heading in headings)
                )
                for stale_heading in (
                    "Offline intraday replay case",
                    "Evidence-shaped output",
                    "Prediction and refusal boundary",
                    "Horizon: next",
                ):
                    self.assertNotIn(stale_heading, case)

    def test_release_demos_present_agent_judgment_and_conditional_triggers(
        self,
    ) -> None:
        readme = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english_readme = Path(REPOSITORY_ROOT, "README_en.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("analysis-boundary.md", readme)
        self.assertIn("analysis-boundary.md", english_readme)

        for demo_name in ("bluefocus.md", "industrial-fulian.md"):
            with self.subTest(demo=demo_name):
                demo = Path(REPOSITORY_ROOT, "examples", demo_name).read_text(
                    encoding="utf-8"
                )
                first_nonblank = next(
                    line for line in demo.splitlines() if line.strip()
                )
                self.assertTrue(first_nonblank.startswith("# "))
                self.assertIn("## 研究判断（Agent 推断）", demo)
                self.assertIn("**条件触发位**", demo)
                self.assertIn("不是买入点、卖出点", demo)

    def test_entry_documents_only_the_public_research_task_invocation(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        cli_contract = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("scripts/entrypoint.py", entry)
        self.assertNotIn("scripts/a_share_research.py", entry)
        self.assertIn("references/cli-contract.md", entry)

        self.assertIn("run --request <research-task.json>", cli_contract)
        for historical_command in (
            "resolve --query",
            "close --security",
            "validate-bundle --bundle",
            "valuation --bundle",
        ):
            with self.subTest(command=historical_command):
                self.assertNotIn(historical_command, entry)
                self.assertNotIn(historical_command, cli_contract)

    def test_readmes_present_presets_outside_the_capability_table(self) -> None:
        chinese = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english = Path(REPOSITORY_ROOT, "README_en.md").read_text(encoding="utf-8")

        self.assertIn("### 预置研究方案", chinese)
        self.assertIn("### Preset research plans", english)
        self.assertNotIn("| 四套研究流程 |", chinese)
        self.assertNotIn("| Four research workflows |", english)
        self.assertIn("唯一受支持的公共调用形式", chinese)
        self.assertIn("the only supported public invocation", english)

    def test_entry_prevents_the_agent_from_inventing_absent_result_fields(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Confirm the returned `task_type` and request scope", entry)
        self.assertIn(
            "No absent result field or wider capability may be inferred", entry
        )
        self.assertIn(
            "Never invent a metric status or field absent from the CLI JSON",
            entry,
        )

    def test_installed_skill_separates_research_judgment_from_action_advice(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        boundary = Path(SKILL_ROOT, "references", "analysis-boundary.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("references/analysis-boundary.md", entry)
        self.assertIn("provide a useful research judgment", boundary)
        self.assertIn("A conditional trigger level is allowed only when", boundary)
        self.assertIn("label it as an **Agent calculation**", boundary)
        self.assertIn(
            "Do not generate a project or Agent rating, price target, direct "
            "buy/sell/hold instruction",
            boundary,
        )
        self.assertNotIn("Do not generate a project or Agent rating", entry)
        self.assertNotIn("Do not label a security cheap or expensive", boundary)

    def test_installed_skill_documents_intraday_contract_and_agent_boundary(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        intraday_contract = Path(
            SKILL_ROOT, "references", "intraday-contracts.md"
        ).read_text(encoding="utf-8")
        boundary = Path(SKILL_ROOT, "references", "analysis-boundary.md").read_text(
            encoding="utf-8"
        )

        for document in (entry, intraday_contract, boundary):
            with self.subTest(document=document[:24]):
                self.assertIn("intraday_market_signal", document)
                self.assertIn("limited", document)
                self.assertIn("blocked", document)

        self.assertIn("current China Standard Time trading date", intraday_contract)
        self.assertIn("canonical SSE/SZSE A-share", intraday_contract)
        self.assertIn("Agent analysis", boundary)
        self.assertIn("research judgment", boundary)

    def test_installed_skill_routes_only_explicit_replay_prediction_requests(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        boundary = Path(SKILL_ROOT, "references", "analysis-boundary.md").read_text(
            encoding="utf-8"
        )
        intraday_contract = Path(
            SKILL_ROOT, "references", "intraday-contracts.md"
        ).read_text(encoding="utf-8")
        case = Path(
            REPOSITORY_ROOT, "examples", "intraday-replay-prediction.md"
        ).read_text(encoding="utf-8")

        for document in (entry, boundary):
            with self.subTest(document=document[:32]):
                self.assertIn("intraday_replay", document)
                self.assertIn("explicit", document.lower())
                self.assertIn("next trading day", document)
                self.assertIn("next 5 trading days", document)
                self.assertIn("continuation", document)
                self.assertIn("range", document)
                self.assertIn("reversal", document)
                self.assertIn("upside", document)
                self.assertIn("downside", document)
                self.assertIn("invalidation", document)
                self.assertIn("uncertainty", document)

        for required_text in (
            "intraday_replay",
            "明确要求未来判断",
            "下一个交易日",
            "未来 5 个交易日",
            "延续",
            "震荡",
            "反转",
            "上行",
            "下行",
            "失效条件",
            "不确定性",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, case)

        for required_gate in (
            "canonical identity",
            "20 complete daily trading sessions",
            "usable close",
            "unadjusted",
            "unresolved core source conflict",
            "coverage is indeterminate",
            "whole morning",
            "whole afternoon",
            "confirmed suspension",
            "limited",
        ):
            with self.subTest(required_gate=required_gate):
                self.assertIn(required_gate, boundary)

        self.assertIn("does not add prediction fields", intraday_contract)
        self.assertIn("historical query or replay request", intraday_contract)
        self.assertIn("optional context", boundary)
        self.assertIn("exact probabilities", boundary)
        self.assertIn("position sizing", boundary)
        self.assertIn("automatic trading", boundary)

    def test_prediction_case_covers_refusal_partial_context_and_action_boundaries(
        self,
    ) -> None:
        case = Path(
            REPOSITORY_ROOT, "examples", "intraday-replay-prediction.md"
        ).read_text(encoding="utf-8")

        for required_path in (
            "正常预测",
            "证据不足拒绝",
            "部分证据",
            "可选上下文缺失",
            "禁止投资行动",
        ):
            with self.subTest(required_path=required_path):
                self.assertIn(required_path, case)

        self.assertIn("正式 observations/claims", case)
        self.assertIn("calculations", case)
        self.assertIn("复盘分析", case)
        self.assertIn("预测", case)

    def test_intraday_replay_delivery_cases_cover_sse_and_szse(self) -> None:
        examples_readme = Path(REPOSITORY_ROOT, "examples", "README.md").read_text(
            encoding="utf-8"
        )

        for security, case_name, request_name in (
            (
                "SSE:600519",
                "intraday-replay-sse.md",
                "intraday-replay-sse.json",
            ),
            (
                "SZSE:000001",
                "intraday-replay-szse.md",
                "intraday-replay-szse.json",
            ),
        ):
            with self.subTest(security=security):
                self.assertIn(f"({case_name})", examples_readme)
                self.assertIn(request_name, examples_readme)

                case = Path(REPOSITORY_ROOT, "examples", case_name).read_text(
                    encoding="utf-8"
                )
                for required_text in (
                    security,
                    '"task_type": "intraday_replay"',
                    "coverage",
                    "records",
                    "summary",
                    "规范化",
                    "证据谱系",
                    "限制",
                    "unavailable_fields",
                    "fixture-only",
                    "installation_incomplete",
                    "security_identity",
                    "下一个交易日",
                    "未来 5 个交易日",
                    "主情景",
                    "上行",
                    "下行",
                    "blocked",
                ):
                    self.assertIn(required_text, case)

                request = json.loads(
                    Path(
                        REPOSITORY_ROOT, "examples", "requests", request_name
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(request["schema_version"], "1.0")
                self.assertEqual(request["task_type"], "intraday_replay")
                self.assertEqual(request["subjects"], [{"security": security}])
                self.assertEqual(request["window"]["observed_from"], "2026-08-14")
                self.assertEqual(request["window"]["observed_to"], "2026-08-14")
                self.assertTrue(request["source_policy"]["allow_experimental"])
                self.assertFalse(request["source_policy"]["allow_credentials"])
                self.assertFalse(request["source_policy"]["allow_fallback"])

    def test_readmes_and_installed_skill_explain_replay_delivery_boundaries(
        self,
    ) -> None:
        chinese = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english = Path(REPOSITORY_ROOT, "README_en.md").read_text(encoding="utf-8")
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        cli_contract = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )
        intraday_contract = Path(
            SKILL_ROOT, "references", "intraday-contracts.md"
        ).read_text(encoding="utf-8")

        for document in (chinese, english, intraday_contract):
            with self.subTest(document=document[:32]):
                self.assertIn("intraday_replay", document)
                self.assertIn("limited", document)
                self.assertIn("blocked", document)
        self.assertIn("mootdx==0.11.7", intraday_contract)
        for document in (chinese, english, entry, cli_contract):
            with self.subTest(document=document[:32]):
                self.assertIn("--output", document)

        for probe_text in (
            "tests/live_probe_intraday_replay.py",
            "--replay-date YYYY-MM-DD",
            "one SSE and one SZSE",
            "never update fixtures",
            "production",
        ):
            with self.subTest(probe_text=probe_text):
                self.assertIn(probe_text, intraday_contract)

        for term in (
            "current-session snapshot",
            "completed-session minute series",
            "deterministic replay summary",
            "future scenarios",
        ):
            with self.subTest(term=term):
                self.assertIn(term, intraday_contract)

    def test_installation_docs_default_locked_dependency_and_separate_health(
        self,
    ) -> None:
        for readme_name in ("README.md", "README_en.md"):
            readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
            with self.subTest(readme=readme_name):
                self.assertIn("requirements.lock", readme)
                self.assertIn("<skill-root>/scripts/install.py", readme)
                self.assertIn("scripts/install.py", readme)
                self.assertIn("installation_health.py", readme)
                self.assertIn("installation_incomplete", readme)
                self.assertIn("intraday", readme)
                self.assertIn("Agent", readme)
                self.assertNotIn("mootdx==", readme)
                self.assertNotIn("Codex", readme)
                self.assertNotIn(".agents/skills", readme)
                if readme_name == "README.md":
                    self.assertIn("silent source switch", readme)
                else:
                    self.assertIn("never triggers a silent switch", readme)

        cli_contract = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("upstream `pip check`", cli_contract)

        chinese = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english = Path(REPOSITORY_ROOT, "README_en.md").read_text(encoding="utf-8")
        for readme in (chinese, english):
            self.assertIn("installation_incomplete", readme)
            self.assertNotIn("optional `mootdx` dependency", readme)

        examples_index = Path(REPOSITORY_ROOT, "examples", "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("bluefocus-f10.json", examples_index)

    def test_skill_entry_remains_a_concise_router(self) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")

        self.assertLess(len(entry.split()), 900)
        self.assertIn("## Task routing", entry)
        self.assertNotIn("## Intraday interpretation boundary", entry)
        self.assertNotIn("`parameters.market_heat_period` is `hour` by default", entry)
        self.assertNotIn("Gamma/Theta/Vega use unverified provider-native units", entry)
        for reference_name in (
            "market-contracts.md",
            "intraday-contracts.md",
            "research-contracts.md",
            "workflow-contracts.md",
            "persistence-contract.md",
        ):
            self.assertIn(f"references/{reference_name}", entry)

    def test_installed_request_assets_cover_every_public_task_type(self) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        asset_paths = sorted(Path(SKILL_ROOT, "assets", "requests").glob("*.json"))

        self.assertEqual(len(asset_paths), 13)
        task_types: set[str] = set()
        for asset_path in asset_paths:
            with self.subTest(asset=asset_path.name):
                content = asset_path.read_text(encoding="utf-8")
                request = json.loads(content)
                self.assertEqual(request["schema_version"], "1.0")
                self.assertIn("REPLACE_", content)
                self.assertIn(f"assets/requests/{asset_path.name}", entry)
                task_types.add(request["task_type"])

        self.assertEqual(
            task_types,
            {
                "security_identity",
                "market_trend",
                "etf_market",
                "etf_options",
                "intraday_market_signal",
                "intraday_replay",
                "security_valuation",
                "valuation_compare",
                "research_content",
                "capital_events",
                "market_signals",
                "research_workflow",
            },
        )
        self.assertIn("no `REPLACE_*` value remains", entry)

    def test_cli_reference_explains_platform_neutral_invocation(self) -> None:
        reference = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )

        for instruction in (
            "Windows: `py -3.12`",
            "macOS: prefer `python3.12`; use `python3` only when it reports Python 3.12 or later",
            "Linux: prefer `python3.12`; use `python3` only when it reports Python 3.12 or later",
            "Resolve `<skill-root>` from the loaded `SKILL.md` location",
            "only public runtime entry point is `<skill-root>/scripts/entrypoint.py`",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, reference)

    def test_cli_reference_keeps_intraday_probe_out_of_normal_ci(self) -> None:
        reference = Path(SKILL_ROOT, "references", "intraday-contracts.md").read_text(
            encoding="utf-8"
        )

        for instruction in (
            "tests/live_probe_intraday.py",
            "--confirm-live --as-of YYYY-MM-DD",
            "one SSE and one SZSE A-share",
            "never update fixtures",
            "ordinary CI dependency",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, reference)

    def test_installed_copy_runs_without_repository_imports_or_dependencies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            request = temporary_root / "research-task.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "task_type": "security_identity",
                        "subjects": [{"clue": "600519"}],
                        "as_of": "2026-08-01",
                        "window": None,
                        "parameters": {},
                        "source_policy": {
                            "allow_experimental": False,
                            "allow_credentials": False,
                            "allow_fallback": False,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(installed / "scripts" / "entrypoint.py"),
                    "run",
                    "--request",
                    str(request),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["plan"]["task_type"], "security_identity")
        self.assertNotIn("task_type", result)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "source_policy_not_satisfied"
        )

    def test_installed_copy_keeps_workflow_cutover_contracts_unregistered(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            requests = {
                "current": {
                    "schema_version": "1.0",
                    "task_type": "research_workflow",
                    "subjects": [],
                    "as_of": "2026-08-21",
                    "window": None,
                    "parameters": {
                        "workflow": {
                            "id": "market_overview_research",
                            "version": "1.0",
                        },
                        "inputs": {"completeness_mode": "fast"},
                    },
                    "source_policy": {
                        "allow_experimental": False,
                        "allow_credentials": False,
                        "allow_fallback": True,
                    },
                },
                "future_overview": {
                    "schema_version": "1.0",
                    "task_type": "research_workflow",
                    "subjects": [],
                    "as_of": "2026-08-21",
                    "window": None,
                    "parameters": {
                        "workflow": {
                            "id": "market_overview_research",
                            "version": "2.0",
                        },
                        "inputs": {"completeness_mode": "fast"},
                    },
                    "source_policy": {
                        "allow_experimental": False,
                        "allow_credentials": False,
                        "allow_fallback": True,
                    },
                },
                "future_screen": {
                    "schema_version": "1.0",
                    "task_type": "research_workflow",
                    "subjects": [],
                    "as_of": "2026-08-21",
                    "window": None,
                    "parameters": {
                        "workflow": {
                            "id": "market_screen_research",
                            "version": "1.0",
                        },
                        "inputs": {
                            "screening_plan": {
                                "id": "fundamental_research_priority",
                                "version": "1.0",
                            },
                            "completeness_mode": "fast",
                            "result_artifact": {
                                "path": str(temporary_root / "screen-result"),
                                "mode": "create",
                            },
                        },
                    },
                    "source_policy": {
                        "allow_experimental": False,
                        "allow_credentials": False,
                        "allow_fallback": True,
                    },
                },
                "future_screen_malformed": {
                    "schema_version": "1.0",
                    "task_type": "research_workflow",
                    "subjects": [],
                    "as_of": "2026-08-21",
                    "window": None,
                    "parameters": {
                        "workflow": {
                            "id": "market_screen_research",
                            "version": "1.0",
                        },
                        "inputs": {
                            "screening_plan": {
                                "id": "caller_formula",
                                "version": "9.0",
                            },
                            "completeness_mode": "standard",
                            "candidate_limit": 999,
                            "result_artifact": {
                                "path": "relative/secret-future-path",
                                "mode": "overwrite",
                            },
                        },
                    },
                    "source_policy": {
                        "allow_experimental": False,
                        "allow_credentials": False,
                        "allow_fallback": True,
                    },
                },
            }
            completed: dict[str, subprocess.CompletedProcess[str]] = {}
            for name, payload in requests.items():
                request_path = temporary_root / f"{name}.json"
                request_path.write_text(json.dumps(payload), encoding="utf-8")
                completed[name] = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(installed / "scripts" / "entrypoint.py"),
                        "run",
                        "--request",
                        str(request_path),
                    ],
                    cwd=temporary_root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            self.assertTrue(temporary_root.exists())
            self.assertFalse((temporary_root / "screen-result").exists())

        current = completed["current"]
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(current.stderr, "")
        current_result = json.loads(current.stdout)
        self.assertEqual(current_result["plan"]["version"], "1.0")
        self.assertEqual(
            current_result["capability_registry"]["workflow"]["version"], "1.0"
        )
        for name in (
            "future_overview",
            "future_screen",
            "future_screen_malformed",
        ):
            with self.subTest(name=name):
                rejected = completed[name]
                self.assertEqual(rejected.returncode, 2, rejected.stdout)
                self.assertEqual(rejected.stdout, "")
                self.assertIn("not supported", rejected.stderr)
                self.assertNotIn("implementation_pending", rejected.stderr)
                self.assertNotIn(str(temporary_root), rejected.stderr)
                self.assertNotIn("secret-future-path", rejected.stderr)

    def test_installed_copy_sanitizes_workflow_snapshot_structure_bounds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "candidate-install" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            artifact_path = temporary_root / "future-screen-artifact"
            entrypoint = installed / "scripts" / "entrypoint.py"
            recursion_probe = """
import runpy
import sys

entrypoint = sys.argv[2]
request_path = sys.argv[3]
runpy.run_path(entrypoint, run_name="__recursion_probe_preload__")
sys.setrecursionlimit(int(sys.argv[1]))
sys.argv = [entrypoint, "run", "--request", request_path]
runpy.run_path(entrypoint, run_name="__main__")
"""

            def nested_probe(deepest_request_depth: int) -> object:
                nested: object = None
                # Root -> parameters -> inputs -> structural_probe is depth 3.
                for _ in range(deepest_request_depth - 3):
                    nested = [nested]
                return nested

            def future_screen_request(probe: object) -> dict[str, Any]:
                return {
                    "schema_version": "1.0",
                    "task_type": "research_workflow",
                    "subjects": [],
                    "as_of": "2026-08-21",
                    "window": None,
                    "parameters": {
                        "workflow": {
                            "id": "market_screen_research",
                            "version": "1.0",
                        },
                        "inputs": {
                            "screening_plan": {
                                "id": "fundamental_research_priority",
                                "version": "1.0",
                            },
                            "completeness_mode": "fast",
                            "result_artifact": {
                                "path": str(artifact_path),
                                "mode": "create",
                            },
                            "structural_probe": probe,
                        },
                    },
                    "source_policy": {
                        "allow_experimental": False,
                        "allow_credentials": False,
                        "allow_fallback": True,
                    },
                }

            cases = {
                "depth_at_limit": (
                    future_screen_request(nested_probe(_WORKFLOW_SNAPSHOT_MAX_DEPTH)),
                    "error: invalid research task: research_workflow id is not supported\n",
                ),
                "depth_one_over": (
                    future_screen_request(
                        nested_probe(_WORKFLOW_SNAPSHOT_MAX_DEPTH + 1)
                    ),
                    "error: invalid research task: research_workflow request exceeds its structural bounds\n",
                ),
                "items_at_limit": (
                    future_screen_request(
                        [
                            None
                            for _ in range(
                                _WORKFLOW_SNAPSHOT_MAX_ITEMS
                                - _FUTURE_SCREEN_STRUCTURAL_PROBE_FIXED_ITEMS
                            )
                        ]
                    ),
                    "error: invalid research task: research_workflow id is not supported\n",
                ),
                "items_one_over": (
                    future_screen_request(
                        [
                            None
                            for _ in range(
                                _WORKFLOW_SNAPSHOT_MAX_ITEMS
                                - _FUTURE_SCREEN_STRUCTURAL_PROBE_FIXED_ITEMS
                                + 1
                            )
                        ]
                    ),
                    "error: invalid research task: research_workflow request exceeds its structural bounds\n",
                ),
                "deep_acyclic": (
                    future_screen_request(
                        nested_probe(_WORKFLOW_SNAPSHOT_MAX_DEPTH * 16)
                    ),
                    "error: invalid research task: research_workflow request exceeds its structural bounds\n",
                ),
            }

            for name, (payload, expected_error) in cases.items():
                request_path = temporary_root / f"{name}.json"
                request_path.write_text(json.dumps(payload), encoding="utf-8")
                for recursion_limit in (60, 80):
                    with self.subTest(
                        name=name,
                        recursion_limit=recursion_limit,
                    ):
                        completed = subprocess.run(
                            [
                                sys.executable,
                                "-I",
                                "-c",
                                recursion_probe,
                                str(recursion_limit),
                                str(entrypoint),
                                str(request_path),
                            ],
                            cwd=temporary_root,
                            check=False,
                            capture_output=True,
                            text=True,
                        )

                        self.assertEqual(completed.returncode, 2, completed.stdout)
                        self.assertEqual(completed.stdout, "")
                        self.assertEqual(completed.stderr, expected_error)
                        for forbidden in (
                            "Traceback",
                            "RecursionError",
                            str(temporary_root),
                            str(installed),
                            str(request_path),
                            str(artifact_path),
                        ):
                            self.assertNotIn(forbidden, completed.stderr)
                        self.assertFalse(artifact_path.exists())

    def test_installed_copy_rejects_repository_maintenance_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )

            for command in ("close", "validate-bundle", "valuation"):
                with self.subTest(command=command):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            str(installed / "scripts" / "entrypoint.py"),
                            command,
                            "--help",
                        ],
                        cwd=temporary_root,
                        check=False,
                        capture_output=True,
                        text=True,
                    )

                    self.assertEqual(completed.returncode, 2, completed.stdout)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn(f"invalid choice: '{command}'", completed.stderr)
                    self.assertNotIn(str(REPOSITORY_ROOT), completed.stderr)

    def test_installed_copy_rejects_abbreviated_public_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            request = temporary_root / "research-task.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "task_type": "security_identity",
                        "subjects": [{"clue": "600519"}],
                        "as_of": "2026-08-01",
                        "window": None,
                        "parameters": {},
                        "source_policy": {
                            "allow_experimental": False,
                            "allow_credentials": False,
                            "allow_fallback": False,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = temporary_root / "result.json"
            cases = (
                ("--req", "/no/such/path"),
                ("--r", str(request)),
                ("--out", str(output), "--request", str(request)),
            )

            for arguments in cases:
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            str(installed / "scripts" / "entrypoint.py"),
                            "run",
                            *arguments,
                        ],
                        cwd=temporary_root,
                        check=False,
                        capture_output=True,
                        text=True,
                    )

                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn("error:", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertNotIn(str(REPOSITORY_ROOT), completed.stderr)
                    self.assertFalse(output.exists())

    def test_installed_copy_reports_output_write_failure_with_nonzero_exit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(
                SKILL_ROOT, installed, ignore=shutil.ignore_patterns(".state")
            )
            request = temporary_root / "research-task.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "task_type": "security_identity",
                        "subjects": [{"clue": "600519"}],
                        "as_of": "2026-08-01",
                        "window": None,
                        "parameters": {},
                        "source_policy": {
                            "allow_experimental": False,
                            "allow_credentials": False,
                            "allow_fallback": False,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-I",
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request),
            ]
            stdout_only = subprocess.run(
                command,
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )
            successful_output = temporary_root / "result.json"
            persisted = subprocess.run(
                [*command, "--output", str(successful_output)],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )
            output = temporary_root / "existing-directory"
            output.mkdir()

            completed = subprocess.run(
                [*command, "--output", str(output)],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(stdout_only.returncode, 0, stdout_only.stderr)
            self.assertEqual(stdout_only.stderr, "")
            expected_result = json.loads(stdout_only.stdout)
            self.assertEqual(persisted.returncode, 0, persisted.stderr)
            self.assertEqual(persisted.stderr, "")
            self.assertEqual(persisted.stdout, stdout_only.stdout)
            self.assertEqual(
                json.loads(successful_output.read_text(encoding="utf-8")),
                expected_result,
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertEqual(completed.stdout.count("\n"), 1)
            self.assertEqual(completed.stdout, stdout_only.stdout)
            self.assertEqual(json.loads(completed.stdout), expected_result)
            self.assertEqual(
                set(expected_result),
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
                },
            )
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])
            self.assertTrue(completed.stderr.startswith("error: cannot write result:"))
            self.assertNotIn("Traceback", completed.stderr)
            self.assertNotIn(str(REPOSITORY_ROOT), completed.stderr)


if __name__ == "__main__":
    unittest.main()
