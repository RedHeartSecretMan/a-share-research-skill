from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures.p0_validation import (
    CORE_PROFILE,
    INSTALLED_PROFILE,
    P0_TASKS,
    REPRESENTATIVE_TASKS,
    assert_evidence_boundary,
    assert_result_contract,
    build_core_requests,
    build_representative_requests,
    install_copied_skill,
    run_copied_request,
    validate_profile_dependencies,
)


class P0ValidationProfileTests(unittest.TestCase):
    """The P0 validation profiles are an independent, runnable test seam."""

    def test_core_profile_contains_only_p0_public_paths(self) -> None:
        self.assertEqual(CORE_PROFILE.name, "core")
        self.assertEqual(
            CORE_PROFILE.tasks,
            (
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
        self.assertEqual(set(CORE_PROFILE.tasks), P0_TASKS)
        self.assertEqual(validate_profile_dependencies(CORE_PROFILE), ())

    def test_installed_profile_is_a_copy_of_core_without_p1_or_p2_edges(self) -> None:
        self.assertEqual(INSTALLED_PROFILE.name, "installed")
        self.assertEqual(INSTALLED_PROFILE.tasks, CORE_PROFILE.tasks)
        self.assertEqual(validate_profile_dependencies(INSTALLED_PROFILE), ())
        self.assertNotIn("market_screen_research", INSTALLED_PROFILE.tasks)
        self.assertNotIn("etf_options", INSTALLED_PROFILE.tasks)
        self.assertNotIn("intraday_replay", INSTALLED_PROFILE.tasks)

    def test_representative_requests_are_public_schema_requests(self) -> None:
        requests = build_core_requests()
        self.assertEqual(set(requests), P0_TASKS)
        for task_type, request in requests.items():
            with self.subTest(task_type=task_type):
                self.assertEqual(request["schema_version"], "1.0")
                expected_task_type = (
                    "research_workflow"
                    if task_type
                    in {"single_security_research", "market_overview_research"}
                    else task_type
                )
                self.assertEqual(request["task_type"], expected_task_type)
                self.assertIn("source_policy", request)
                self.assertFalse(request["source_policy"]["allow_credentials"])

    def test_installed_smoke_is_a_bounded_representative_subset(self) -> None:
        self.assertEqual(set(build_representative_requests()), REPRESENTATIVE_TASKS)

    def test_shared_contract_validator_keeps_evidence_levels_explicit(self) -> None:
        sample = {
            "schema_version": "1.0",
            "status": "limited",
            "research_anchor": {"as_of": "2026-08-03"},
            "plan": {"task_type": "etf_market"},
            "capability_registry": {},
            "coverage": {},
            "observations": [],
            "claims": [],
            "calculations": [],
            "conflicts": [],
            "limitations": [
                {"code": "source_not_checked", "evidence_level": "offline"}
            ],
            "source_errors": [],
            "task_views": {},
            "briefs": [],
        }
        assert_result_contract(sample, expected_task_type="etf_market")
        for status in ("supported", "limited", "blocked"):
            sample["status"] = status
            assert_result_contract(sample, expected_task_type="etf_market")
        for status in ("complete", "failed"):
            sample["status"] = status
            with self.assertRaises(AssertionError):
                assert_result_contract(sample, expected_task_type="etf_market")

    def test_profile_fixture_does_not_import_test_modules(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "p0_validation.py"
        tree = ast.parse(fixture.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(
            any(
                module == "tests" or module.startswith("tests.test_")
                for module in imported
            )
        )

    @unittest.skipUnless(
        sys.version_info >= (3, 12), "the distributable Skill requires Python 3.12"
    )
    def test_installed_profile_copies_checks_health_and_invokes_public_requests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            installed, health, processes = install_copied_skill(root)
            self.assertEqual(processes["install"].stderr, "")
            self.assertEqual(processes["health"].returncode, 0)
            self.assertEqual(health["status"], "complete")
            self.assertEqual(
                health["source_policy"],
                {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            )
            self.assertFalse((installed / ".state").exists())

            requests = build_core_requests()
            for capability, request in requests.items():
                with self.subTest(capability=capability):
                    result, completed = run_copied_request(installed, root, request)
                    expected_task_type = (
                        "research_workflow"
                        if capability
                        in {"single_security_research", "market_overview_research"}
                        else capability
                    )
                    assert_result_contract(
                        result, expected_task_type=expected_task_type
                    )
                    # A blocked source attempt is deliberately retained as a
                    # result limitation/error; it is not a live-source claim.
                    self.assertTrue(
                        result["source_errors"] or result["limitations"],
                        completed.stdout,
                    )
                    assert_evidence_boundary(health, result)
                    if capability == "market_overview_research":
                        inventory = result["capability_registry"]["workflow"][
                            "capability_inventory"
                        ]
                        self.assertEqual(len(inventory), 15)
                        by_id = {item["capability_id"]: item for item in inventory}
                        self.assertEqual(
                            {
                                item["state"]
                                for item in by_id.values()
                                if item["capability_id"]
                                in {
                                    "whole_market_turnover",
                                    "whole_market_margin",
                                    "market_wide_intraday",
                                }
                            },
                            {"unsupported"},
                        )
                        for capability_id in (
                            "whole_market_turnover",
                            "whole_market_margin",
                            "market_wide_intraday",
                        ):
                            item = by_id[capability_id]
                            self.assertEqual(item["step_ids"], [])
                            self.assertNotIn(capability_id, result["coverage"])
                            self.assertFalse(
                                any(
                                    record.get("kind", record.get("capability_id"))
                                    == capability_id
                                    or (
                                        isinstance(record.get("payload"), dict)
                                        and record["payload"].get("capability_id")
                                        == capability_id
                                    )
                                    for field in (
                                        "observations",
                                        "claims",
                                        "calculations",
                                    )
                                    for record in result[field]
                                )
                            )

            forged = dict(next(iter(requests.values())))
            forged["evidence_level"] = "live"
            with self.assertRaises(AssertionError):
                assert_evidence_boundary(health, forged)


if __name__ == "__main__":
    unittest.main()
