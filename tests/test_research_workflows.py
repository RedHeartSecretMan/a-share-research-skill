from __future__ import annotations

import copy
import unittest
from collections.abc import Iterable
from typing import Any

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

POLICY = {
    "allow_experimental": True,
    "allow_credentials": False,
    "allow_fallback": True,
}


def workflow_request(
    workflow_id: str,
    *,
    subjects: list[dict[str, Any]] | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": [] if subjects is None else subjects,
        "as_of": "2026-08-03",
        "window": None,
        "parameters": {
            "workflow": {"id": workflow_id, "version": "1.0"},
            "inputs": {} if inputs is None else inputs,
        },
        "source_policy": copy.deepcopy(POLICY),
    }


def leaf_result(
    task_type: str,
    status: str = "supported",
    *,
    subjects: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    source_errors: list[dict[str, Any]] | None = None,
    degradations: list[dict[str, Any]] | None = None,
    limitations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": task_type,
        "status": status,
        "subjects": [] if subjects is None else subjects,
        "evidence": [] if evidence is None else evidence,
        "conflicts": [] if conflicts is None else conflicts,
        "source_errors": [] if source_errors is None else source_errors,
        "degradations": [] if degradations is None else degradations,
        "limitations": [] if limitations is None else limitations,
    }


def identity_result(status: str = "limited") -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    if status != "blocked":
        candidates = [
            {
                "security": {
                    "exchange": "SSE",
                    "code": "601138",
                    "type": "A_SHARE",
                },
                "name": "工业富联",
                "issuer": {"name": "工业富联"},
            }
        ]
    return {
        **leaf_result(
            "security_identity",
            status,
            evidence=[{"id": "identity-601138"}] if candidates else [],
            limitations=(
                [{"code": "experimental_identity_sources"}]
                if candidates
                else [{"code": "identity_not_resolved"}]
            ),
        ),
        "candidates": candidates,
    }


class RecordingRuntime(ResearchRuntime):
    def __init__(self, results: Iterable[dict[str, Any]]) -> None:
        super().__init__()
        self._results = iter(results)
        self.calls: list[dict[str, Any]] = []

    def _run_leaf_task(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(request))
        return copy.deepcopy(next(self._results))


class ResearchWorkflowContractTests(unittest.TestCase):
    def test_single_security_valuation_compiles_one_leaf_task(self) -> None:
        subject = {"clue": "工业富联", "issuer_security_class_count": 1}
        runtime = RecordingRuntime(
            [leaf_result("security_valuation", "limited", subjects=[subject])]
        )

        result = runtime.research(
            workflow_request(
                "single_security_valuation",
                subjects=[subject],
                inputs={"target_pe": "30"},
            )
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["workflow"]["id"], "single_security_valuation")
        self.assertEqual(result["workflow"]["executed_version"], "1.0")
        self.assertEqual(
            result["research"],
            {"as_of": "2026-08-03", "timezone": "Asia/Shanghai"},
        )
        self.assertEqual(result["plan"][0]["criticality"], "required_evidence")
        self.assertIs(result["plan"][0]["selected"], True)
        self.assertEqual([step["step_id"] for step in result["steps"]], ["valuation"])
        self.assertEqual(
            result["steps"][0]["result"]["task_type"], "security_valuation"
        )
        self.assertEqual(
            result["steps"][0]["research_task"]["task_type"], "security_valuation"
        )
        self.assertNotIn("request", result["steps"][0])
        self.assertEqual(
            runtime.calls,
            [
                {
                    "schema_version": "1.0",
                    "task_type": "security_valuation",
                    "subjects": [subject],
                    "as_of": "2026-08-03",
                    "window": None,
                    "parameters": {"target_pe": "30"},
                    "source_policy": POLICY,
                }
            ],
        )

    def test_comparison_and_theme_compile_existing_leaf_tasks(self) -> None:
        comparison_subjects = [
            {"clue": "工业富联", "issuer_security_class_count": 1},
            {"clue": "贵州茅台", "issuer_security_class_count": 1},
        ]
        cases = [
            (
                workflow_request(
                    "valuation_comparison",
                    subjects=comparison_subjects,
                    inputs={"target_pe": "30"},
                ),
                leaf_result("valuation_compare"),
                "valuation_compare",
                None,
                {"target_pe": "30"},
            ),
            (
                workflow_request(
                    "theme_report_research",
                    inputs={
                        "query": ["人形机器人", "丝杠", "减速器"],
                        "published_from": "2026-05-01",
                        "published_to": "2026-08-03",
                        "limit": 20,
                        "verify_documents": True,
                    },
                ),
                leaf_result("research_content", "limited"),
                "research_content",
                {"published_from": "2026-05-01", "published_to": "2026-08-03"},
                {
                    "material_types": ["research_report"],
                    "query": ["人形机器人", "丝杠", "减速器"],
                    "limit": 20,
                    "verify_documents": True,
                },
            ),
        ]
        for request, child_result, task_type, window, parameters in cases:
            with self.subTest(task_type=task_type):
                runtime = RecordingRuntime([child_result])
                result = runtime.research(request)
                self.assertEqual(runtime.calls[0]["task_type"], task_type)
                self.assertEqual(runtime.calls[0]["window"], window)
                self.assertEqual(runtime.calls[0]["parameters"], parameters)
                self.assertEqual(result["status"], child_result["status"])

    def test_workflow_rejects_unknown_version_and_caller_steps(self) -> None:
        invalid_version = workflow_request(
            "single_security_valuation",
            subjects=[{"clue": "工业富联", "issuer_security_class_count": 1}],
            inputs={"target_pe": "30"},
        )
        invalid_version["parameters"]["workflow"]["version"] = "2.0"
        arbitrary_steps = workflow_request(
            "single_security_valuation",
            subjects=[{"clue": "工业富联", "issuer_security_class_count": 1}],
            inputs={"target_pe": "30"},
        )
        arbitrary_steps["parameters"]["steps"] = [{"task_type": "capital_events"}]
        for request in (invalid_version, arbitrary_steps):
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    RecordingRuntime([]).research(request)

    def test_new_security_research_runs_fixed_steps_with_canonical_identity(
        self,
    ) -> None:
        results = [identity_result()]
        results.extend(
            leaf_result(task_type, "limited")
            for task_type in (
                "research_content",
                "security_valuation",
                "market_signals",
                "capital_events",
                "capital_events",
                "capital_events",
                "capital_events",
            )
        )
        runtime = RecordingRuntime(results)

        result = runtime.research(self._new_security_request())

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
        self.assertEqual(len(runtime.calls), 8)
        self.assertEqual(runtime.calls[0]["subjects"][0]["clue"], "工业富联")
        for child in runtime.calls[1:]:
            self.assertEqual(child["subjects"][0]["clue"], "SSE:601138")
            self.assertEqual(child["source_policy"], POLICY)
            self.assertNotEqual(child["task_type"], "research_workflow")
        self.assertEqual(
            runtime.calls[2]["subjects"][0]["issuer_security_class_count"], 1
        )
        self.assertEqual(
            runtime.calls[3]["window"],
            {"observed_from": "2026-08-03", "observed_to": "2026-08-03"},
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["brief"]["executed_step_count"], 8)
        self.assertEqual(result["brief"]["selected_step_count"], 8)
        self.assertIs(result["brief"]["minimum_evidence_satisfied"], True)
        self.assertEqual(result["plan"][0]["criticality"], "gate")

    def test_identity_block_skips_all_dependent_steps(self) -> None:
        runtime = RecordingRuntime([identity_result("blocked")])

        result = runtime.research(self._new_security_request())

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(result["steps"][0]["state"], "blocked")
        self.assertEqual(
            {step["state"] for step in result["steps"][1:]},
            {"skipped_dependency"},
        )
        self.assertEqual(result["brief"]["executed_step_count"], 1)
        self.assertEqual(len(result["brief"]["missing_required_steps"]), 7)
        self.assertIs(result["brief"]["minimum_evidence_satisfied"], False)
        for step in result["steps"][1:]:
            self.assertIsNone(step["research_task"])
            self.assertEqual(step["skip"]["blocked_by"], ["identity"])

    def test_noncanonical_identity_candidate_has_explicit_workflow_limitation(
        self,
    ) -> None:
        invalid_identity = identity_result()
        invalid_identity["candidates"][0]["issuer"] = None
        runtime = RecordingRuntime([invalid_identity])

        result = runtime.research(self._new_security_request())

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["steps"][0]["state"], "blocked")
        self.assertEqual(result["steps"][0]["result"]["status"], "limited")
        limitation = {
            "code": "workflow_identity_not_canonical",
            "message": (
                "The identity step did not establish one canonical A-share "
                "security for dependent workflow steps."
            ),
        }
        self.assertIn(limitation, result["steps"][0]["limitations"])
        self.assertIn(
            {"step_id": "identity", "item": limitation},
            result["limitations"],
        )

    def test_noncritical_block_does_not_stop_siblings_and_projects_diagnostics(
        self,
    ) -> None:
        report_error = {"code": "missing_credential", "message": "not configured"}
        results = [
            identity_result(),
            leaf_result(
                "research_content",
                "blocked",
                source_errors=[report_error],
                limitations=[{"code": "content_unavailable"}],
            ),
            leaf_result("security_valuation", evidence=[{"id": "valuation-evidence"}]),
            leaf_result("market_signals"),
            leaf_result("capital_events"),
            leaf_result("capital_events"),
            leaf_result("capital_events"),
            leaf_result("capital_events"),
        ]
        runtime = RecordingRuntime(results)

        result = runtime.research(self._new_security_request())

        self.assertEqual(len(runtime.calls), 8)
        self.assertEqual(result["status"], "limited")
        report_step = result["steps"][1]
        self.assertEqual(report_step["state"], "blocked")
        self.assertEqual(report_step["result"]["source_errors"], [report_error])
        self.assertIn(
            {"step_id": "institutional_coverage", "item": report_error},
            result["source_errors"],
        )
        self.assertIn(
            {
                "step_id": "valuation",
                "item": {"id": "valuation-evidence"},
            },
            result["evidence"],
        )

    def _new_security_request(self) -> dict[str, Any]:
        return workflow_request(
            "new_security_research",
            subjects=[{"clue": "工业富联", "issuer_security_class_count": 1}],
            inputs={
                "target_pe": "30",
                "report_window": {
                    "published_from": "2026-05-01",
                    "published_to": "2026-08-03",
                },
                "market_window": {
                    "observed_from": "2026-07-27",
                    "observed_to": "2026-08-03",
                },
                "lockup_window": {
                    "observed_from": "2026-08-03",
                    "observed_to": "2026-11-01",
                },
                "fund_flow_period": "5d",
                "limit": 20,
                "verify_documents": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
