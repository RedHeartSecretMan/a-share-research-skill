from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "research_workflows" / "fixture_cli.py"
)

SOURCE_POLICY = {
    "allow_experimental": True,
    "allow_credentials": False,
    "allow_fallback": False,
}


def workflow_request(
    workflow_id: str,
    *,
    subjects: list[dict[str, Any]],
    inputs: dict[str, Any],
    allow_credentials: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": subjects,
        "as_of": "2026-08-02",
        "window": None,
        "parameters": {
            "workflow": {"id": workflow_id, "version": "1.0"},
            "inputs": inputs,
        },
        "source_policy": {
            **SOURCE_POLICY,
            "allow_credentials": allow_credentials,
        },
    }


class ResearchWorkflowsCliE2ETests(unittest.TestCase):
    def run_workflow(
        self,
        request: dict[str, Any],
        *,
        extra_environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "workflow-request.json")
            request_path.write_text(
                json.dumps(request, ensure_ascii=False),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    **(extra_environment or {}),
                },
                check=False,
                capture_output=True,
                text=True,
            )
        result = json.loads(completed.stdout)
        return completed, result

    def assert_successful_process(
        self, completed: subprocess.CompletedProcess[str]
    ) -> None:
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)

    def test_single_security_valuation_runs_through_public_cli(self) -> None:
        completed, result = self.run_workflow(
            workflow_request(
                "single_security_valuation",
                subjects=[{"clue": "工业富联", "issuer_security_class_count": 1}],
                inputs={"target_pe": "30"},
            )
        )

        self.assert_successful_process(completed)
        self.assertEqual(result["task_type"], "research_workflow")
        self.assertEqual(result["workflow"]["id"], "single_security_valuation")
        self.assertEqual([step["step_id"] for step in result["steps"]], ["valuation"])
        leaf = result["steps"][0]["result"]
        self.assertEqual(leaf["plan"]["task_type"], "security_valuation")
        self.assertEqual(
            leaf["claims"][0]["payload"]["subjects"][0]["security"]["code"],
            "601138",
        )
        self.assertEqual(result["status"], "limited")

    def test_valuation_comparison_runs_five_fixture_securities(self) -> None:
        subjects = [
            {"clue": clue, "issuer_security_class_count": 1}
            for clue in ("工业富联", "贵州茅台", "中国平安", "蓝色光标", "平安银行")
        ]
        completed, result = self.run_workflow(
            workflow_request(
                "valuation_comparison",
                subjects=subjects,
                inputs={"target_pe": "30"},
            )
        )

        self.assert_successful_process(completed)
        self.assertEqual(result["workflow"]["id"], "valuation_comparison")
        comparison = result["steps"][0]["result"]
        self.assertEqual(comparison["plan"]["task_type"], "valuation_compare")
        comparison_payload = comparison["claims"][0]["payload"]
        self.assertEqual(len(comparison_payload["rows"]), 5)
        self.assertEqual(
            {row["security"] for row in comparison_payload["rows"]},
            {
                "SSE:601138",
                "SSE:600519",
                "SSE:601318",
                "SZSE:300058",
                "SZSE:000001",
            },
        )
        self.assertIn(result["status"], {"limited", "supported"})

    def test_theme_report_research_uses_free_baseline_when_credential_is_missing(
        self,
    ) -> None:
        completed, result = self.run_workflow(
            workflow_request(
                "theme_report_research",
                subjects=[],
                inputs={
                    "query": ["AI服务器", "算力产业链"],
                    "published_from": "2026-05-05",
                    "published_to": "2026-08-02",
                    "limit": 20,
                    "verify_documents": False,
                },
                allow_credentials=True,
            )
        )

        self.assert_successful_process(completed)
        self.assertEqual(result["workflow"]["id"], "theme_report_research")
        content = result["steps"][0]["result"]
        self.assertEqual(content["plan"]["task_type"], "research_content")
        materials = [
            item
            for item in content["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [item["payload"]["material_type"] for item in materials],
            ["research_report", "research_report"],
        )
        self.assertEqual(
            {item["operation_id"] for item in materials},
            {"eastmoney_reports@1"},
        )
        self.assertIn(
            ("iwencai_content_search@1", "missing_credential"),
            {(item["operation_id"], item["code"]) for item in content["source_errors"]},
        )
        self.assertEqual(result["status"], "limited")

    def test_theme_report_research_keeps_iwencai_as_optional_enhancement(
        self,
    ) -> None:
        completed, result = self.run_workflow(
            workflow_request(
                "theme_report_research",
                subjects=[],
                inputs={
                    "query": ["AI服务器", "算力产业链"],
                    "published_from": "2026-05-05",
                    "published_to": "2026-08-02",
                    "limit": 20,
                    "verify_documents": False,
                },
                allow_credentials=True,
            ),
            extra_environment={
                "FIXTURE_IWENCAI_API_KEY": "fixture-only-key",
                "FIXTURE_IWENCAI_BASE_URL": "https://iwencai.fixture.test",
            },
        )

        self.assert_successful_process(completed)
        content = result["steps"][0]["result"]
        self.assertEqual(content["status"], "limited")
        materials = [
            item
            for item in content["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            {item["operation_id"] for item in materials},
            {"eastmoney_reports@1", "iwencai_content_search@1"},
        )
        self.assertNotIn(
            "missing_credential",
            {item["code"] for item in content["source_errors"]},
        )

    def test_industrial_fulian_new_security_runs_all_eight_steps(self) -> None:
        completed, result = self.run_workflow(
            workflow_request(
                "new_security_research",
                subjects=[{"clue": "工业富联", "issuer_security_class_count": 1}],
                inputs={
                    "target_pe": "30",
                    "report_window": {
                        "published_from": "2026-05-05",
                        "published_to": "2026-08-02",
                    },
                    "market_window": {
                        "observed_from": "2026-07-01",
                        "observed_to": "2026-08-02",
                    },
                    "lockup_window": {
                        "observed_from": "2026-08-02",
                        "observed_to": "2026-10-31",
                    },
                    "fund_flow_period": "5d",
                    "limit": 20,
                    "verify_documents": False,
                },
            )
        )

        self.assert_successful_process(completed)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["subjects"][0]["security"],
            {"exchange": "SSE", "code": "601138", "type": "A_SHARE"},
        )
        expected_steps = [
            "identity",
            "institutional_coverage",
            "valuation",
            "board_membership",
            "fund_flow",
            "dragon_tiger",
            "lockup",
            "margin_trading",
        ]
        self.assertEqual([step["step_id"] for step in result["steps"]], expected_steps)
        self.assertEqual(result["brief"]["executed_step_count"], 8)
        self.assertEqual(len(result["steps"]), 8)
        for step in result["steps"]:
            self.assertIsInstance(step["research_task"], dict)
            self.assertIsInstance(step["result"], dict)
        identity_step = result["steps"][0]
        self.assertEqual(
            identity_step["research_task"]["subjects"], [{"clue": "工业富联"}]
        )
        self.assertEqual(
            identity_step["result"]["plan"]["task_type"], "security_identity"
        )
        self.assertIn("observations", identity_step["result"])
        self.assertNotIn("evidence", identity_step["result"])
        valuation_step = next(
            step for step in result["steps"] if step["step_id"] == "valuation"
        )
        self.assertEqual(
            set(valuation_step["result"]),
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
        for step in result["steps"][1:]:
            if step["step_id"] == "valuation":
                continue
            if step["step_id"] == "institutional_coverage":
                self.assertEqual(
                    set(step["result"]),
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
                self.assertNotIn("evidence", step["result"])
                continue
            if step["step_id"] in {
                "board_membership",
                "fund_flow",
                "dragon_tiger",
                "lockup",
                "margin_trading",
            }:
                self.assertEqual(
                    set(step["result"]),
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
                expected_task_type = (
                    "market_signals"
                    if step["step_id"] == "board_membership"
                    else "capital_events"
                )
                expected_capability = (
                    "market_signal_observations"
                    if step["step_id"] == "board_membership"
                    else "capital_event_observations"
                )
                expected_claim = (
                    "market_signal_observation"
                    if step["step_id"] == "board_membership"
                    else "capital_event_observation"
                )
                self.assertEqual(
                    step["result"]["plan"]["task_type"], expected_task_type
                )
                owned_observation_ids = {
                    item["id"]
                    for item in step["result"]["observations"]
                    if item["capability_id"] == expected_capability
                }
                self.assertTrue(owned_observation_ids)
                task_claims = [
                    item
                    for item in step["result"]["claims"]
                    if item["kind"] == expected_claim
                ]
                self.assertTrue(task_claims)
                self.assertTrue(
                    all(
                        claim["observation_ids"]
                        and set(claim["observation_ids"]).issubset(
                            owned_observation_ids
                        )
                        for claim in task_claims
                    )
                )
                self.assertIsInstance(step["result"]["source_errors"], list)
                continue
            for field in (
                "evidence",
                "conflicts",
                "source_errors",
                "degradations",
                "limitations",
            ):
                self.assertIn(field, step["result"])
        for step in result["steps"][1:]:
            self.assertEqual(
                step["research_task"]["subjects"][0]["clue"],
                "SSE:601138",
            )
        board_step = next(
            step for step in result["steps"] if step["step_id"] == "board_membership"
        )
        self.assertEqual(
            board_step["research_task"]["window"],
            {"observed_from": "2026-08-02", "observed_to": "2026-08-02"},
        )
        for field in (
            "evidence",
            "conflicts",
            "source_errors",
            "degradations",
            "limitations",
        ):
            self.assertIsInstance(result[field], list)
            for projected in result[field]:
                self.assertEqual(set(projected), {"step_id", "item"})
        self.assertEqual(result["evidence"], [])
        self.assertTrue(result["limitations"])
        self.assertTrue(
            any(
                item["step_id"] == "institutional_coverage"
                for item in result["limitations"]
            )
        )


if __name__ == "__main__":
    unittest.main()
