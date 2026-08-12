from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
_LOCKED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)"
    r'(?:\s*;\s*sys_platform\s*==\s*"([^"]+)")?$'
)


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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)

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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
        self.assertEqual(len(_applicable_locked_requirements(SKILL_ROOT)), 17)
        self.assertEqual(
            len(
                _applicable_locked_requirements(
                    SKILL_ROOT,
                    platform="win32",
                )
            ),
            19,
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
            shutil.copytree(SKILL_ROOT, installed)
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
        self.assertIn("when the question calls for interpretation", metadata)
        self.assertIn("the result is not blocked", metadata)

    def test_installation_is_pinned_to_v020_release(self) -> None:
        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn(
                    "git clone --depth 1 --branch v0.2.0 --single-branch ",
                    readme,
                )
                self.assertNotIn(
                    "git clone --depth 1 --branch main --single-branch ",
                    readme,
                )
                self.assertIn(
                    "https://github.com/RedHeartSecretMan/a-share-research-skill.git",
                    readme,
                )
                self.assertIn(
                    (
                        "v0.2.0 交付基线"
                        if readme_name == "README.md"
                        else "v0.2.0 delivery baseline"
                    ),
                    readme,
                )
                self.assertIn(
                    "https://github.com/RedHeartSecretMan/a-share-research-skill/tree/v0.2.0",
                    readme,
                )

        chinese_readme = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english_readme = Path(REPOSITORY_ROOT, "README_en.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("mootdx", chinese_readme)
        self.assertIn("mootdx", english_readme)

    def test_v020_examples_include_the_intraday_public_seam(self) -> None:
        examples_readme = Path(REPOSITORY_ROOT, "examples", "README.md").read_text(
            encoding="utf-8"
        )
        first_nonblank = next(
            line for line in examples_readme.splitlines() if line.strip()
        )
        self.assertEqual(first_nonblank, "# v0.2.0 真实案例")
        self.assertIn("[盘中行情快照](intraday-snapshot.md)", examples_readme)
        self.assertIn(
            "[盘中请求](requests/intraday-market-snapshot.json)", examples_readme
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
            "mootdx==0.11.7",
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

    def test_v020_release_audit_is_linked_from_both_readmes(self) -> None:
        audit_relative_path = "docs/research/v0.2.0-release-audit-2026-08-03.md"
        audit = Path(REPOSITORY_ROOT, audit_relative_path)
        self.assertTrue(audit.is_file())
        self.assertTrue(
            audit.read_text(encoding="utf-8").startswith("# v0.2.0 发布审计")
        )

        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn(audit_relative_path, readme)

    def test_release_demos_present_agent_judgment_and_conditional_triggers(
        self,
    ) -> None:
        readme = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english_readme = Path(REPOSITORY_ROOT, "README_en.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## 分析边界", readme)
        self.assertIn("## Analysis boundary", english_readme)

        for demo_name in ("bluefocus.md", "industrial-fulian.md"):
            with self.subTest(demo=demo_name):
                demo = Path(REPOSITORY_ROOT, "examples", demo_name).read_text(
                    encoding="utf-8"
                )
                first_nonblank = next(
                    line for line in demo.splitlines() if line.strip()
                )
                self.assertTrue(first_nonblank.startswith("# "))
                self.assertIn("## Agent 研究判断", demo)
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
        self.assertIn("`run --request` 是唯一受支持的公共调用形式", chinese)
        self.assertIn(
            "`run --request` is the only supported public invocation", english
        )

    def test_entry_prevents_the_agent_from_inventing_absent_result_fields(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Confirm the returned `task_type` and scope", entry)
        self.assertIn(
            "no absent result field or wider capability has been inferred", entry
        )
        self.assertIn(
            "Never invent a metric status or field that is absent from the CLI JSON",
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
        cli_contract = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )
        boundary = Path(SKILL_ROOT, "references", "analysis-boundary.md").read_text(
            encoding="utf-8"
        )

        for document in (entry, cli_contract, boundary):
            with self.subTest(document=document[:24]):
                self.assertIn("intraday_market_signal", document)
                self.assertIn("limited", document)
                self.assertIn("blocked", document)

        self.assertIn("current China Standard Time trading date", cli_contract)
        self.assertIn("canonical SSE/SZSE A-share", cli_contract)
        self.assertIn("Agent analysis", boundary)
        self.assertIn("research judgment", boundary)

    def test_installed_skill_routes_only_explicit_replay_prediction_requests(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        boundary = Path(SKILL_ROOT, "references", "analysis-boundary.md").read_text(
            encoding="utf-8"
        )
        case = Path(
            REPOSITORY_ROOT, "examples", "intraday-replay-prediction.md"
        ).read_text(encoding="utf-8")

        for document in (entry, boundary, case):
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

        self.assertIn("does not add prediction fields", entry)
        self.assertIn("historical query or replay request", entry)
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

        self.assertIn("facts", case)
        self.assertIn("calculations", case)
        self.assertIn("replay analysis", case)
        self.assertIn("prediction", case)

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
                    "normalized",
                    "summary",
                    "evidence lineage",
                    "limitations",
                    "unavailable_fields",
                    "fixture-only",
                    "installation_incomplete",
                    "security_identity",
                    "next trading day",
                    "next 5 trading days",
                    "primary",
                    "upside",
                    "downside",
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
                self.assertEqual(request["window"]["observed_from"], "2026-08-03")
                self.assertEqual(request["window"]["observed_to"], "2026-08-03")
                self.assertTrue(request["source_policy"]["allow_experimental"])
                self.assertFalse(request["source_policy"]["allow_credentials"])
                self.assertFalse(request["source_policy"]["allow_fallback"])

    def test_readmes_and_installed_skill_explain_replay_delivery_boundaries(
        self,
    ) -> None:
        chinese = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english = Path(REPOSITORY_ROOT, "README_en.md").read_text(encoding="utf-8")
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")
        contract = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )

        for document in (chinese, english, entry, contract):
            with self.subTest(document=document[:32]):
                self.assertIn("intraday_replay", document)
                self.assertIn("mootdx==0.11.7", document)
                self.assertIn("limited", document)
                self.assertIn("blocked", document)
                self.assertIn("--output", document)

        for probe_text in (
            "tests/live_probe_intraday_replay.py",
            "--replay-date YYYY-MM-DD",
            "one SSE and one SZSE",
            "never update fixtures",
            "production",
        ):
            with self.subTest(probe_text=probe_text):
                self.assertIn(probe_text, english)
                self.assertIn(probe_text, contract)

        for term in (
            "A complete intraday trading-day series",
            "intraday replay summary",
            "intraday replay analysis",
            "evidence-constrained scenario prediction",
        ):
            with self.subTest(term=term):
                self.assertIn(term, entry)

    def test_installation_docs_default_locked_dependency_and_separate_health(
        self,
    ) -> None:
        for readme_name in ("README.md", "README_en.md"):
            readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
            with self.subTest(readme=readme_name):
                self.assertIn("mootdx==0.11.7", readme)
                self.assertIn("scripts/install.py", readme)
                self.assertIn("installation_health.py", readme)
                self.assertIn("installation_incomplete", readme)
                self.assertIn("intraday", readme)
                self.assertIn("pip check", readme)
                if readme_name == "README.md":
                    self.assertIn("最小实际运行闭包", readme)
                    self.assertIn("silent source switch", readme)
                else:
                    self.assertIn("minimum actual runtime closure", readme)
                    self.assertIn("never triggers a silent switch", readme)

        chinese = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english = Path(REPOSITORY_ROOT, "README_en.md").read_text(encoding="utf-8")
        for readme in (chinese, english):
            self.assertIn("bluefocus-f10.json", readme)
            self.assertIn("installation_incomplete", readme)
            self.assertNotIn("optional `mootdx` dependency", readme)

    def test_skill_entry_remains_a_concise_router(self) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")

        self.assertLess(len(entry.split()), 1500)
        self.assertNotIn("`parameters.market_heat_period` is `hour` by default", entry)
        self.assertNotIn("Gamma/Theta/Vega use unverified provider-native units", entry)

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
        reference = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
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
            shutil.copytree(SKILL_ROOT, installed)
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

    def test_installed_copy_rejects_repository_maintenance_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(SKILL_ROOT, installed)

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
            shutil.copytree(SKILL_ROOT, installed)
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
            shutil.copytree(SKILL_ROOT, installed)
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
