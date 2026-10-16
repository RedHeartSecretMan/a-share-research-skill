from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "a-share-research"


class V040DistributionTests(unittest.TestCase):
    def test_clean_installed_overview_preserves_modes_dates_and_failure_boundaries(
        self,
    ) -> None:
        # Wheel stubs isolate the installer mechanism. Real locked dependencies
        # are checked separately by the offline clean-install acceptance audit.
        from tests.test_skill_distribution import _install_fake_locked_runtime
        from tests.test_market_overview_research import market_overview_request

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            installed = work / "a-share-research"
            shutil.copytree(
                SKILL,
                installed,
                ignore=shutil.ignore_patterns(
                    ".state", ".runtime", "__pycache__", "*.pyc"
                ),
            )
            installation = _install_fake_locked_runtime(installed, work)
            self.assertEqual(installation.returncode, 0, installation.stderr)
            self.assertEqual(json.loads(installation.stdout)["status"], "complete")
            fixtures = ROOT / "tests/fixtures/research_workflows"
            (work / "sitecustomize.py").write_text(
                (fixtures / "issue_66_sitecustomize.py").read_text()
                + "\n"
                + (fixtures / "issue_96_sitecustomize.py").read_text()
            )
            cases = (
                ("standard", "2026-08-03", "complete"),
                ("fast", "2026-08-03", "complete"),
                ("standard", "2026-08-01", "complete"),
                ("standard", "2026-08-03", "missing_bse_index"),
                ("standard", "2026-08-03", "missing_bse_session"),
            )
            for mode, as_of, scenario in cases:
                with self.subTest(mode=mode, as_of=as_of, scenario=scenario):
                    request = market_overview_request(
                        inputs={
                            "completeness_mode": mode,
                            "deep_extensions": ["market_wide_intraday"],
                        }
                    )
                    request["as_of"] = as_of
                    request_path = work / "request.json"
                    request_path.write_text(json.dumps(request))
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(installed / "scripts/entrypoint.py"),
                            "run",
                            "--request",
                            str(request_path),
                        ],
                        cwd=work,
                        env={
                            "PATH": str(Path(sys.executable).parent),
                            "PYTHONPATH": str(work),
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "ASR96_SCENARIO": scenario,
                        },
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stderr, "")
                    self.assertLess(len(completed.stdout.encode()), 1_048_576)
                    result = json.loads(
                        completed.stdout, parse_constant=lambda value: self.fail(value)
                    )
                    self.assertEqual(result["schema_version"], "1.0")
                    self.assertEqual(result["plan"]["version"], "2.0")
                    inventory = {
                        item["capability_id"]: item
                        for item in result["capability_registry"]["workflow"][
                            "capability_inventory"
                        ]
                    }
                    self.assertEqual(len(inventory), 15)
                    for capability in (
                        "whole_market_turnover",
                        "whole_market_margin",
                        "market_wide_intraday",
                    ):
                        self.assertEqual(inventory[capability]["state"], "unsupported")
                        self.assertEqual(inventory[capability]["step_ids"], [])
                        self.assertNotIn(capability, result["coverage"]["steps"])
                        self.assertFalse(
                            any(
                                item.get("kind", item.get("capability_id"))
                                == capability
                                for field in ("observations", "claims", "calculations")
                                for item in result[field]
                            )
                        )
                    indices = [
                        item["payload"]["payload"]["observation"]
                        for item in result["observations"]
                        if item["capability_id"] == "major_broad_indices"
                    ]
                    self.assertEqual(
                        len(indices),
                        0
                        if scenario == "missing_bse_session"
                        else 3
                        if scenario == "missing_bse_index"
                        else 4,
                    )
                    self.assertTrue(
                        all(
                            item["price_semantics"] == "completed_close"
                            for item in indices
                        )
                    )
                    self.assertEqual(
                        {item["session_date"] for item in indices},
                        set()
                        if scenario == "missing_bse_session"
                        else {"2026-07-31" if as_of == "2026-08-01" else "2026-08-03"},
                    )
                    if scenario in {"missing_bse_index", "missing_bse_session"}:
                        self.assertTrue(result["source_errors"])
                    self.assertNotIn(str(ROOT), completed.stdout)
                    self.assertNotIn(str(installed), completed.stdout)
                    self.assertFalse((installed / ".state").exists())

    def test_copied_version_and_routes_describe_retained_candidate(self) -> None:
        self.assertEqual((SKILL / "VERSION").read_text().strip(), "0.4.0")
        entry = (SKILL / "SKILL.md").read_text()
        self.assertIn("market_overview_research@2.0", entry)
        self.assertNotIn("market_overview_research@1.0", entry)
        self.assertIn("15-entry inventory", entry)
        for capability in (
            "whole_market_turnover",
            "whole_market_margin",
            "market_wide_intraday",
        ):
            self.assertIn(capability, entry)
        reference = (SKILL / "references" / "cli-contract.md").read_text()
        self.assertIn("46 public producers", reference)
        self.assertIn("live attestation", reference)
        for relative in (
            "assets/requests/workflow-market-overview.json",
            "../../examples/requests/workflow-market-overview-research.json",
        ):
            request = json.loads((SKILL / relative).read_text())
            self.assertEqual(request["schema_version"], "1.0")
            self.assertEqual(request["subjects"], [])
            self.assertEqual(
                request["parameters"]["workflow"],
                {"id": "market_overview_research", "version": "2.0"},
            )
            self.assertFalse(request["source_policy"]["allow_credentials"])

    def test_current_entry_docs_preserve_scope_and_historical_evidence(self) -> None:
        chinese = (ROOT / "README.md").read_text()
        english = (ROOT / "README_en.md").read_text()
        for document in (chinese, english):
            self.assertIn("market_overview_research@2.0", document)
            self.assertIn("market_wide_intraday", document)
            self.assertIn("market_screen_research", document)
            self.assertIn("intraday_market_signal", document)
            self.assertIn("intraday_replay", document)
            self.assertIn("v0.3.0-release-audit-2026-08-18.md", document)
            self.assertNotIn("market_overview_research@1.0", document)
        example = (ROOT / "examples/market-overview-v2.md").read_text()
        for expected in (
            "15",
            "12",
            "free_anonymous_turnover_unproved",
            "free_anonymous_margin_unproved",
            "free_anonymous_intraday_unproved",
            "SSE",
            "SZSE",
            "BSE",
            "离线",
            "#98",
        ):
            self.assertIn(expected, example)
        context = (ROOT / "CONTEXT.md").read_text()
        self.assertIn("market_overview_research@2.0", context)
        self.assertIn("内部", context)
        self.assertNotIn("market_overview_research@1.0", context)

    def test_installable_tree_excludes_runtime_state_and_machine_paths(self) -> None:
        forbidden_parts = {"tests", ".state", ".runtime", "__pycache__", ".git"}
        delivered = (
            subprocess.check_output(
                ["git", "ls-files", "-z", "--", "skill/a-share-research"], cwd=ROOT
            )
            .decode()
            .split("\0")
        )
        for name in filter(None, delivered):
            path = ROOT / name
            relative = path.relative_to(SKILL)
            with self.subTest(path=str(relative)):
                self.assertTrue(forbidden_parts.isdisjoint(relative.parts))
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertNotIn(b"/Users/", path.read_bytes())
                    self.assertNotIn(b"/private/tmp/", path.read_bytes())
                    self.assertNotIn("release-probe-evidence", path.name)


if __name__ == "__main__":
    unittest.main()
