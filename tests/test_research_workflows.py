from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path
from typing import Any

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = REPOSITORY_ROOT / "tests/fixtures/research_workflows/fixture_cli.py"


def fixture_runtime() -> ResearchRuntime:
    spec = importlib.util.spec_from_file_location("_workflow_fixture", FIXTURE_CLI)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load workflow fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return ResearchRuntime(
        identity_transport=module.ValuationFixtureTransport(),
        research_now=module.RESEARCH_NOW,
        available_optional_dependencies=set(),
        content_operations=module._content_operations(),
        capital_operations=[module.WorkflowCapitalOperation()],
        market_signal_operations=[module.WorkflowBoardMembershipOperation()],
    )


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
        "as_of": "2026-08-02",
        "window": None,
        "parameters": {
            "workflow": {"id": workflow_id, "version": "1.0"},
            "inputs": {} if inputs is None else inputs,
        },
        "source_policy": copy.deepcopy(POLICY),
    }


class RecordingRuntime(ResearchRuntime):
    def __init__(self, delegate: ResearchRuntime | None = None) -> None:
        super().__init__()
        self._delegate = delegate or fixture_runtime()
        self.calls: list[dict[str, Any]] = []

    def _run_leaf_task(self, request: dict[str, Any]) -> Any:
        self.calls.append(copy.deepcopy(request))
        return self._delegate._run_leaf_task(copy.deepcopy(request))


class ResearchWorkflowContractTests(unittest.TestCase):
    def test_single_security_valuation_compiles_one_leaf_task(self) -> None:
        subject = {"clue": "工业富联", "issuer_security_class_count": 1}
        runtime = RecordingRuntime()

        result = runtime.research(
            workflow_request(
                "single_security_valuation",
                subjects=[subject],
                inputs={"target_pe": "30"},
            )
        )

        public = result.to_dict()
        self.assertEqual(public["status"], "limited")
        self.assertEqual(public["plan"]["workflow_id"], "single_security_valuation")
        self.assertEqual(public["plan"]["version"], "1.0")
        self.assertEqual(
            public["research_anchor"],
            {"as_of": "2026-08-02", "timezone": "Asia/Shanghai"},
        )
        self.assertEqual(public["plan"]["steps"][0]["criticality"], "required_evidence")
        steps = public["task_views"]["research_workflow"]["steps"]
        self.assertEqual([step["step_id"] for step in steps], ["valuation"])
        self.assertEqual(steps[0]["task_type"], "security_valuation")
        self.assertNotIn("result", steps[0])
        self.assertNotIn("research_task", steps[0])
        self.assertEqual(
            runtime.calls,
            [
                {
                    "schema_version": "1.0",
                    "task_type": "security_valuation",
                    "subjects": [subject],
                    "as_of": "2026-08-02",
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
                        "published_to": "2026-08-02",
                        "limit": 20,
                        "verify_documents": True,
                    },
                ),
                "research_content",
                {"published_from": "2026-05-01", "published_to": "2026-08-02"},
                {
                    "material_types": ["research_report"],
                    "query": ["人形机器人", "丝杠", "减速器"],
                    "limit": 20,
                    "verify_documents": True,
                },
            ),
        ]
        for request, task_type, window, parameters in cases:
            with self.subTest(task_type=task_type):
                runtime = RecordingRuntime()
                result = runtime.research(request).to_dict()
                self.assertEqual(runtime.calls[0]["task_type"], task_type)
                self.assertEqual(runtime.calls[0]["window"], window)
                self.assertEqual(runtime.calls[0]["parameters"], parameters)
                step = result["task_views"]["research_workflow"]["steps"][0]
                self.assertEqual(step["task_type"], task_type)
                self.assertEqual(result["status"], step["state"])

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
                    RecordingRuntime().research(request)

    def test_new_security_research_runs_fixed_steps_with_canonical_identity(
        self,
    ) -> None:
        runtime = RecordingRuntime()

        result = runtime.research(self._new_security_request()).to_dict()

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
            {"observed_from": "2026-08-02", "observed_to": "2026-08-02"},
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(sum(bool(step["brief_ids"]) for step in steps), 8)
        self.assertEqual(result["plan"]["steps"][0]["criticality"], "gate")
        self.assertEqual(
            set(result["capability_registry"]["steps"]), set(expected_steps)
        )
        self.assertEqual(set(result["coverage"]["steps"]), set(expected_steps))
        capital_steps = [
            step
            for step in steps
            if step["step_id"]
            in {"fund_flow", "dragon_tiger", "lockup", "margin_trading"}
        ]
        for step in capital_steps:
            registry = result["capability_registry"]["steps"][step["step_id"]]
            coverage = result["coverage"]["steps"][step["step_id"]]
            self.assertEqual(registry["leaf_plan"]["task_type"], "capital_events")
            self.assertIn(
                "fixed_workflow_capital@1",
                coverage["leaf"]["capital_event_observations"]["operations"],
            )
            self.assertEqual(coverage["observation_ids"], step["observation_ids"])
        self.assertEqual(
            len({tuple(step["observation_ids"]) for step in capital_steps}),
            len(capital_steps),
        )

    def test_identity_block_skips_all_dependent_steps(self) -> None:
        blocked_delegate = ResearchRuntime(
            identity_transport=fixture_runtime()._identity_transport
        )
        runtime = RecordingRuntime(blocked_delegate)
        request = self._new_security_request()
        request["source_policy"]["allow_experimental"] = False

        result = runtime.research(request).to_dict()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(runtime.calls), 1)
        steps = result["task_views"]["research_workflow"]["steps"]
        self.assertEqual(steps[0]["state"], "blocked")
        self.assertEqual(
            {step["state"] for step in steps[1:]},
            {"skipped_research_dependency"},
        )
        self.assertEqual(sum(bool(step["brief_ids"]) for step in steps), 1)
        for step in steps[1:]:
            self.assertFalse(step["brief_ids"])
            self.assertTrue(step["workflow_limitation_ids"])

    def test_noncanonical_identity_candidate_has_explicit_workflow_limitation(
        self,
    ) -> None:
        class NoncanonicalRuntime(RecordingRuntime):
            def _run_leaf_task(self, request: dict[str, Any]) -> Any:
                result = super()._run_leaf_task(request)
                if request["task_type"] == "security_identity":
                    result.claims[0]["payload"]["issuer"] = None
                return result

        runtime = NoncanonicalRuntime()
        with self.assertRaisesRegex(
            ValueError, "workflow claim ID contradicts its public owned facts"
        ):
            runtime.research(self._new_security_request()).to_dict()

    def test_noncritical_block_does_not_stop_siblings_and_projects_diagnostics(
        self,
    ) -> None:
        class ReportBlockedRuntime(RecordingRuntime):
            def _run_leaf_task(self, request: dict[str, Any]) -> Any:
                self.calls.append(copy.deepcopy(request))
                if request["task_type"] == "research_content":
                    blocked_request = copy.deepcopy(request)
                    blocked_request["source_policy"]["allow_experimental"] = False
                    return self._delegate._run_leaf_task(blocked_request)
                return self._delegate._run_leaf_task(copy.deepcopy(request))

        runtime = ReportBlockedRuntime()
        result = runtime.research(self._new_security_request()).to_dict()
        steps = result["task_views"]["research_workflow"]["steps"]

        self.assertEqual(len(runtime.calls), 8)
        self.assertEqual(result["status"], "limited")
        report_step = steps[1]
        self.assertEqual(report_step["state"], "blocked")
        self.assertTrue(report_step["limitation_ids"])
        self.assertTrue(steps[2]["observation_ids"])
        self.assertEqual(len(steps), 8)

    def _new_security_request(self) -> dict[str, Any]:
        return workflow_request(
            "new_security_research",
            subjects=[{"clue": "工业富联", "issuer_security_class_count": 1}],
            inputs={
                "target_pe": "30",
                "report_window": {
                    "published_from": "2026-05-01",
                    "published_to": "2026-08-02",
                },
                "market_window": {
                    "observed_from": "2026-07-27",
                    "observed_to": "2026-08-02",
                },
                "lockup_window": {
                    "observed_from": "2026-08-02",
                    "observed_to": "2026-10-31",
                },
                "fund_flow_period": "5d",
                "limit": 20,
                "verify_documents": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
