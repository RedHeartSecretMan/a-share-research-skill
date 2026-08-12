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
UNIFIED_RESULT_KEYS = {
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
        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["plan"]["task_type"], "research_workflow")
        self.assertEqual(result["plan"]["workflow_id"], "single_security_valuation")
        steps = result["task_views"]["research_workflow"]["steps"]
        self.assertEqual([step["step_id"] for step in steps], ["valuation"])
        self.assertEqual(steps[0]["task_type"], "security_valuation")
        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "security_valuation_metrics"
        )
        self.assertEqual(
            claim["payload"]["subjects"][0]["security"]["code"],
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
        self.assertEqual(result["plan"]["workflow_id"], "valuation_comparison")
        step = result["task_views"]["research_workflow"]["steps"][0]
        self.assertEqual(step["task_type"], "valuation_compare")
        comparison_payload = next(
            item["payload"]
            for item in result["claims"]
            if item["kind"] == "valuation_compare_members"
        )
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
        self.assertEqual(result["plan"]["workflow_id"], "theme_report_research")
        step = result["task_views"]["research_workflow"]["steps"][0]
        self.assertEqual(step["task_type"], "research_content")
        materials = [
            item
            for item in result["observations"]
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
            {(item["operation_id"], item["code"]) for item in result["source_errors"]},
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
        step = result["task_views"]["research_workflow"]["steps"][0]
        self.assertEqual(step["state"], "limited")
        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            {item["operation_id"] for item in materials},
            {"eastmoney_reports@1", "iwencai_content_search@1"},
        )
        self.assertNotIn(
            "missing_credential",
            {item["code"] for item in result["source_errors"]},
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
        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
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
        steps = result["task_views"]["research_workflow"]["steps"]
        self.assertEqual([step["step_id"] for step in steps], expected_steps)
        self.assertEqual(len(steps), 8)
        self.assertTrue(all(len(step["brief_ids"]) == 1 for step in steps))
        self.assertTrue(all("result" not in step for step in steps))
        self.assertTrue(all("research_task" not in step for step in steps))
        identity_claim = next(
            item for item in result["claims"] if item["kind"] == "security_identity"
        )
        self.assertEqual(
            {
                key: identity_claim["payload"]["security"][key]
                for key in ("exchange", "code", "type")
            },
            {"exchange": "SSE", "code": "601138", "type": "A_SHARE"},
        )
        top_level_ids = {
            item["id"]
            for field in (
                "observations",
                "claims",
                "calculations",
                "conflicts",
                "limitations",
                "source_errors",
            )
            for item in result[field]
        }
        for step in steps:
            referenced = {
                reference
                for field in (
                    "observation_ids",
                    "claim_ids",
                    "calculation_ids",
                    "conflict_ids",
                    "limitation_ids",
                    "source_error_ids",
                    "workflow_limitation_ids",
                )
                for reference in step[field]
            }
            self.assertTrue(referenced.issubset(top_level_ids))
        board_step = next(
            step for step in steps if step["step_id"] == "board_membership"
        )
        board_claims = [
            item for item in result["claims"] if item["id"] in board_step["claim_ids"]
        ]
        self.assertTrue(
            any(item["kind"] == "market_signal_observation" for item in board_claims)
        )
        self.assertTrue(result["limitations"])

    def test_noncritical_leaf_block_keeps_completed_public_siblings(self) -> None:
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
            ),
            extra_environment={"FIXTURE_WORKFLOW_REPORT_BLOCKED": "1"},
        )

        self.assert_successful_process(completed)
        steps = result["task_views"]["research_workflow"]["steps"]
        report = next(
            item for item in steps if item["step_id"] == "institutional_coverage"
        )
        self.assertEqual(report["state"], "blocked")
        self.assertTrue(report["source_error_ids"] or report["limitation_ids"])
        for step in steps:
            if step["step_id"] == "institutional_coverage":
                continue
            self.assertNotEqual(step["state"], "skipped_research_dependency")
            self.assertEqual(len(step["brief_ids"]), 1)
        retained = {
            record_id
            for step in steps
            if step["step_id"] not in {"identity", "institutional_coverage"}
            for record_id in step["observation_ids"]
        }
        self.assertTrue(retained)
        self.assertTrue(
            retained.issubset({item["id"] for item in result["observations"]})
        )
        self.assertEqual(result["status"], "limited")


if __name__ == "__main__":
    unittest.main()
