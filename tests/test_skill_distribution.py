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

    def test_installation_is_pinned_to_current_main_delivery(self) -> None:
        for readme_name in ("README.md", "README_en.md"):
            with self.subTest(readme=readme_name):
                readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
                self.assertIn(
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

        chinese_readme = Path(REPOSITORY_ROOT, "README.md").read_text(encoding="utf-8")
        english_readme = Path(REPOSITORY_ROOT, "README_en.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("mootdx", chinese_readme)
        self.assertIn("mootdx", english_readme)

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

    def test_installation_docs_scope_mootdx_to_intraday_capability(self) -> None:
        for readme_name in ("README.md", "README_en.md"):
            readme = Path(REPOSITORY_ROOT, readme_name).read_text(encoding="utf-8")
            with self.subTest(readme=readme_name):
                self.assertIn("mootdx==0.11.7", readme)
                self.assertIn("capability-scoped", readme)
                self.assertIn("intraday", readme)
                if readme_name == "README.md":
                    self.assertIn("silent source switch", readme)
                else:
                    self.assertIn("never silently switches source", readme)

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
        self.assertEqual(result["task_type"], "security_identity")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "source_policy_not_satisfied"
        )


if __name__ == "__main__":
    unittest.main()
