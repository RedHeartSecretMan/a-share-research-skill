from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"


class SkillDistributionTests(unittest.TestCase):
    def test_repository_publishes_only_the_nested_installable_skill(self) -> None:
        self.assertFalse(Path(REPOSITORY_ROOT, "SKILL.md").exists())

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

    def test_entry_routes_every_public_cli_workflow_with_the_exact_signature(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")

        expected_commands = (
            "resolve --query <security-clue> --as-of <YYYY-MM-DD>",
            "close --security <SSE:code|SZSE:code> --as-of <YYYY-MM-DD>",
            "validate-bundle --bundle <bundle-directory>",
            "valuation --bundle <bundle-directory> --as-of <YYYY-MM-DD>",
        )
        for command in expected_commands:
            with self.subTest(command=command):
                self.assertIn(command, entry)

    def test_entry_prevents_the_agent_from_inventing_absent_result_fields(
        self,
    ) -> None:
        entry = Path(SKILL_ROOT, "SKILL.md").read_text(encoding="utf-8")

        self.assertIn(
            "After `valuation`, confirm its `research.question` matches", entry
        )
        self.assertIn(
            "Do not expect `research.question` from `resolve`, `close`, or "
            "`validate-bundle`",
            entry,
        )
        self.assertIn(
            "Never invent a metric status or field that is absent from the CLI JSON",
            entry,
        )

    def test_cli_reference_explains_platform_neutral_invocation(self) -> None:
        reference = Path(SKILL_ROOT, "references", "cli-contract.md").read_text(
            encoding="utf-8"
        )

        for instruction in (
            "Windows: `py -3.12`",
            "macOS: `python3`",
            "Linux: `python3`",
            "Resolve `<skill-root>` from the loaded `SKILL.md` location",
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
            bundle = temporary_root / "caller-bundle"
            bundle.mkdir()
            Path(bundle, "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "subject": {
                            "security": {
                                "exchange": "SSE",
                                "code": "600519",
                                "type": "A_SHARE",
                            },
                            "issuer": {"name": "贵州茅台酒股份有限公司"},
                        },
                        "as_of": "2026-08-01",
                        "question": "current_valuation",
                        "evidence": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(installed / "scripts" / "a_share_research.py"),
                    "validate-bundle",
                    "--bundle",
                    str(bundle),
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
        self.assertEqual(result["validation"]["structure"], "valid")
        self.assertEqual(result["validation"]["source_verification"], "unverified")


if __name__ == "__main__":
    unittest.main()
