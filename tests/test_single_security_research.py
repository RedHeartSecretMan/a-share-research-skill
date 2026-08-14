from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures"
BOOTSTRAP = FIXTURES / "single_security_research" / "sitecustomize.py"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from a_share_research.research_result import (  # noqa: E402
    UnifiedResearchResult,
    stable_result_id,
)


def invoke_copied_installed(
    request: dict[str, Any], *, scenario: str = "normal"
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        installed = root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        shutil.copytree(FIXTURES, root / "fixtures")
        shutil.copy2(BOOTSTRAP, root / "sitecustomize.py")
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "A_SHARE_RESEARCH_INSTALLED_SCRIPTS": str(installed / "scripts"),
                "A_SHARE_RESEARCH_FIXTURES": str(root / "fixtures"),
                "A_SHARE_RESEARCH_TEST_SCENARIO": (
                    "single_security_history_conflict"
                    if scenario == "valuation_conflict"
                    else (
                        "single_security_history"
                        if request["task_type"] == "research_workflow"
                        else "default"
                    )
                ),
                "A_SHARE_SINGLE_SECURITY_SCENARIO": scenario,
            },
            check=False,
            capture_output=True,
            text=True,
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise AssertionError("copied-installed workflow did not return an object")
    return result


def operation_states(value: object) -> dict[str, str]:
    states: dict[str, str] = {}

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            operations = item.get("operations")
            if isinstance(operations, Mapping):
                for operation_id, operation in operations.items():
                    if isinstance(operation_id, str) and isinstance(operation, Mapping):
                        execution_state = operation.get("execution_state")
                        if isinstance(execution_state, str):
                            states[operation_id] = execution_state
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return states


def rehash_workflow_result(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)

    def replace_references(item: object, replacements: dict[str, str]) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if isinstance(nested, str) and nested in replacements:
                    item[key] = replacements[nested]
                else:
                    replace_references(nested, replacements)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                if isinstance(nested, str) and nested in replacements:
                    item[index] = replacements[nested]
                else:
                    replace_references(nested, replacements)

    replacements: dict[str, str] = {}
    for observation in result["observations"]:
        old_id = observation["id"]
        observation["id"] = stable_result_id(
            "observation",
            {key: nested for key, nested in observation.items() if key != "id"},
        )
        replacements[old_id] = observation["id"]
    replace_references(result, replacements)
    for field, prefix in (
        ("claims", "claim"),
        ("calculations", "calculation"),
        ("conflicts", "conflict"),
        ("limitations", "limitation"),
    ):
        replacements = {}
        for record in result[field]:
            old_id = record["id"]
            record["id"] = stable_result_id(
                prefix,
                {key: nested for key, nested in record.items() if key != "id"},
            )
            replacements[old_id] = record["id"]
        replace_references(result, replacements)
    replacements = {}
    for brief in result["briefs"]:
        old_id = brief["id"]
        brief["id"] = stable_result_id(
            "brief",
            {key: nested for key, nested in brief.items() if key != "id"},
        )
        replacements[old_id] = brief["id"]
    replace_references(result, replacements)
    return result


def validate_serialized(result: dict[str, Any]) -> None:
    UnifiedResearchResult(
        status=result["status"],
        research_anchor=result["research_anchor"],
        plan=result["plan"],
        capability_registry=result["capability_registry"],
        coverage=result["coverage"],
        observations=tuple(result["observations"]),
        claims=tuple(result["claims"]),
        calculations=tuple(result["calculations"]),
        conflicts=tuple(result["conflicts"]),
        limitations=tuple(result["limitations"]),
        source_errors=tuple(result["source_errors"]),
        task_views=result["task_views"],
        briefs=tuple(result["briefs"]),
    ).to_dict()


def single_security_request(*, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": [
            {"clue": "工业富联", "issuer_security_class_count": 1},
        ],
        "as_of": "2026-08-02",
        "window": None,
        "parameters": {
            "workflow": {"id": "single_security_research", "version": "1.0"},
            "inputs": {"target_pe": "30"} if inputs is None else inputs,
        },
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def direct_valuation_request() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "security_valuation",
        "subjects": [
            {"clue": "工业富联", "issuer_security_class_count": 1},
        ],
        "as_of": "2026-08-02",
        "window": None,
        "parameters": {"target_pe": "30"},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def direct_announcement_request() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_content",
        "subjects": [{"clue": "工业富联"}],
        "as_of": "2026-08-02",
        "window": {
            "published_from": "2026-07-01",
            "published_to": "2026-08-02",
        },
        "parameters": {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
            "verify_documents": False,
        },
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def direct_intraday_request() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "intraday_market_signal",
        "subjects": [{"security": "SSE:600519"}],
        "as_of": "2026-08-03",
        "window": None,
        "parameters": {},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


class SingleSecurityResearchPublicSeamTests(unittest.TestCase):
    def test_public_docs_describe_the_versioned_single_security_contract(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        cli_contract = (SKILL_ROOT / "references" / "cli-contract.md").read_text(
            encoding="utf-8"
        )
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for document in (readme, cli_contract):
            self.assertIn("single_security_research", document)
            self.assertIn("completeness_mode", document)
            self.assertIn("deep_extensions", document)
            self.assertIn("large_materials", document)
            self.assertIn("long_history", document)
        self.assertNotIn("`research_workflow` 提供四个", readme)
        self.assertIn("documented narrow task", skill)
        self.assertIn("five-level order-book or tick endpoint", skill)
        example = json.loads(
            (
                REPOSITORY_ROOT
                / "examples"
                / "requests"
                / "workflow-industrial-fulian-single-security.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            example["parameters"],
            {
                "workflow": {"id": "single_security_research", "version": "1.0"},
                "inputs": {"target_pe": "30"},
            },
        )

    def test_explicit_leaf_requests_remain_narrow(self) -> None:
        cases = (
            (direct_valuation_request(), "security_valuation"),
            (direct_announcement_request(), "research_content"),
            (direct_intraday_request(), "intraday_market_signal"),
        )
        for request, task_type in cases:
            with self.subTest(task_type=task_type):
                result = invoke_copied_installed(request)
                self.assertEqual(result["plan"]["task_type"], task_type)
                self.assertNotIn("research_workflow", result["task_views"])
                self.assertNotIn("steps", result["capability_registry"])
                self.assertNotIn("steps", result["coverage"])

    def test_standard_default_attempts_core_and_standard_enrichment(self) -> None:
        result = invoke_copied_installed(single_security_request())

        expected_steps = [
            "identity",
            "market_trend",
            "valuation",
            "materials",
            "capital_activity",
            "lockup_events",
            "market_context",
        ]
        plan_steps = result["plan"]["steps"]
        self.assertEqual(
            [step["step_id"] for step in plan_steps],
            expected_steps,
        )
        self.assertEqual(
            [step["criticality"] for step in plan_steps],
            [
                "gate",
                "core",
                "standard_enrichment",
                "standard_enrichment",
                "standard_enrichment",
                "standard_enrichment",
                "standard_enrichment",
            ],
        )
        view_steps = result["task_views"]["research_workflow"]["steps"]
        self.assertEqual(
            [step["step_id"] for step in view_steps],
            expected_steps,
        )
        self.assertTrue(
            all(step["state"] in {"supported", "limited"} for step in view_steps)
        )
        self.assertTrue(
            all(
                "result" not in step and "research_task" not in step
                for step in view_steps
            )
        )
        coverage = result["coverage"]["steps"]
        self.assertEqual(set(coverage), set(expected_steps))
        self.assertTrue(
            all(coverage[step_id]["state"] == "complete" for step_id in expected_steps)
        )
        self.assertTrue(all(coverage[step_id]["leaf"] for step_id in expected_steps))

    def test_standard_success_exercises_formal_subject_owners(self) -> None:
        result = invoke_copied_installed(single_security_request())

        identity = {
            "sse_stock_list@1",
            "szse_stock_list@1",
            "cninfo_security_dictionary@1",
        }
        daily = {
            "sse_daily_line@1",
            "tencent_daily_line@1",
            "szse_daily_line@1",
        }
        expected = {
            "identity": identity,
            "market_trend": identity | daily,
            "valuation": identity
            | daily
            | {
                "eastmoney_stock_info@1",
                "sina_financial_statements@1",
                "ths_consensus_eps@1",
            },
            "materials": identity
            | {
                "eastmoney_reports@1",
                "mootdx_f10@1",
                "ths_consensus_material@1",
                "eastmoney_stock_news@1",
                "cninfo_announcement@1",
                "sse_announcement@1",
                "szse_announcement@1",
                "cninfo_investor_qa@1",
                "cls_market_flash@1",
            },
            "capital_activity": identity
            | {
                "eastmoney_northbound_flow@1",
                "eastmoney_stock_fund_flow@1",
                "eastmoney_board_fund_flow@1",
                "eastmoney_trading_events@1",
                "eastmoney_margin_trading@1",
                "eastmoney_shareholder_count@1",
                "eastmoney_dividend@1",
            },
            "lockup_events": identity
            | {
                "eastmoney_northbound_flow@1",
                "eastmoney_stock_fund_flow@1",
                "eastmoney_board_fund_flow@1",
                "eastmoney_trading_events@1",
                "eastmoney_margin_trading@1",
                "eastmoney_shareholder_count@1",
                "eastmoney_dividend@1",
            },
            "market_context": identity
            | {
                "ths_strong_stock_theme@1",
                "eastmoney_security_board_membership@1",
                "eastmoney_industry_rotation@1",
                "eastmoney_limit_state@1",
                "ths_limit_reason@1",
                "eastmoney_focus_monitoring@1",
                "eastmoney_severe_abnormal_movement@1",
                "ths_market_heat@1",
            },
        }
        for step_id, expected_owners in expected.items():
            with self.subTest(step_id=step_id):
                registered = result["capability_registry"]["steps"][step_id]
                obligations = {
                    item["producer_operation_id"]
                    for item in registered["leaf_plan"]["operation_obligations"]
                }
                self.assertEqual(obligations, expected_owners)
                states = operation_states(result["coverage"]["steps"][step_id]["leaf"])
                self.assertEqual(set(states), expected_owners)

        for step_id, owners in {
            "identity": identity,
            "market_trend": identity | {"sse_daily_line@1", "tencent_daily_line@1"},
            "valuation": identity
            | {
                "sse_daily_line@1",
                "tencent_daily_line@1",
                "eastmoney_stock_info@1",
                "sina_financial_statements@1",
                "ths_consensus_eps@1",
            },
            "materials": expected["materials"] - {"cls_market_flash@1"},
            "capital_activity": expected["capital_activity"]
            - {"eastmoney_northbound_flow@1", "eastmoney_board_fund_flow@1"},
            "lockup_events": identity | {"eastmoney_trading_events@1"},
            "market_context": identity | {"eastmoney_security_board_membership@1"},
        }.items():
            states = operation_states(result["coverage"]["steps"][step_id]["leaf"])
            self.assertEqual(
                {owner: states[owner] for owner in owners},
                {owner: "succeeded" for owner in owners},
            )

    def test_standard_default_registry_records_every_formal_owner(self) -> None:
        result = invoke_copied_installed(
            single_security_request(),
            scenario="default_inventory",
        )

        inventory = {
            "materials": (
                "research_content_materials",
                {
                    "eastmoney_reports@1",
                    "mootdx_f10@1",
                    "ths_consensus_material@1",
                    "eastmoney_stock_news@1",
                    "cninfo_announcement@1",
                    "sse_announcement@1",
                    "szse_announcement@1",
                    "cninfo_investor_qa@1",
                },
                {"cls_market_flash@1"},
            ),
            "capital_activity": (
                "capital_event_observations",
                {
                    "eastmoney_stock_fund_flow@1",
                    "eastmoney_trading_events@1",
                    "eastmoney_margin_trading@1",
                    "eastmoney_shareholder_count@1",
                    "eastmoney_dividend@1",
                },
                {
                    "eastmoney_northbound_flow@1",
                    "eastmoney_board_fund_flow@1",
                },
            ),
            "lockup_events": (
                "capital_event_observations",
                {"eastmoney_trading_events@1"},
                {
                    "eastmoney_northbound_flow@1",
                    "eastmoney_stock_fund_flow@1",
                    "eastmoney_board_fund_flow@1",
                    "eastmoney_margin_trading@1",
                    "eastmoney_shareholder_count@1",
                    "eastmoney_dividend@1",
                },
            ),
            "market_context": (
                "market_signal_observations",
                {"eastmoney_security_board_membership@1"},
                {
                    "ths_strong_stock_theme@1",
                    "eastmoney_industry_rotation@1",
                    "eastmoney_limit_state@1",
                    "ths_limit_reason@1",
                    "eastmoney_focus_monitoring@1",
                    "eastmoney_severe_abnormal_movement@1",
                    "ths_market_heat@1",
                },
            ),
        }
        for step_id, (leaf_key, attempted, not_requested) in inventory.items():
            with self.subTest(step_id=step_id):
                operations = result["coverage"]["steps"][step_id]["leaf"][leaf_key][
                    "operations"
                ]
                self.assertEqual(set(operations), attempted | not_requested)
                self.assertTrue(
                    all(
                        operations[owner]["execution_state"] in {"succeeded", "failed"}
                        for owner in attempted
                    )
                )
                self.assertEqual(
                    {
                        owner: operations[owner]["execution_state"]
                        for owner in not_requested
                    },
                    {owner: "not_requested" for owner in not_requested},
                )

    def test_one_material_source_failure_preserves_independent_domains(self) -> None:
        result = invoke_copied_installed(
            single_security_request(),
            scenario="materials_failure",
        )

        self.assertEqual(result["status"], "limited")
        materials = result["coverage"]["steps"]["materials"]["leaf"][
            "research_content_materials"
        ]["operations"]
        self.assertEqual(materials["eastmoney_reports@1"]["execution_state"], "failed")
        self.assertTrue(
            all(
                materials[owner]["execution_state"] == "succeeded"
                for owner in (
                    "mootdx_f10@1",
                    "ths_consensus_material@1",
                    "eastmoney_stock_news@1",
                    "cninfo_announcement@1",
                    "sse_announcement@1",
                    "szse_announcement@1",
                    "cninfo_investor_qa@1",
                )
            )
        )
        self.assertTrue(
            any(
                item["operation_id"] == "eastmoney_reports@1"
                and item["code"] == "upstream_unavailable"
                for item in result["source_errors"]
            )
        )
        unified_ids = {
            field: {item["id"] for item in result[field]}
            for field in (
                "observations",
                "calculations",
                "briefs",
            )
        }
        views = {
            step["step_id"]: step
            for step in result["task_views"]["research_workflow"]["steps"]
        }
        for step_id in ("valuation", "capital_activity", "market_context"):
            with self.subTest(step_id=step_id):
                step = views[step_id]
                self.assertTrue(step["observation_ids"])
                self.assertTrue(step["calculation_ids"])
                self.assertTrue(step["brief_ids"])
                self.assertLessEqual(
                    set(step["observation_ids"]), unified_ids["observations"]
                )
                self.assertLessEqual(
                    set(step["calculation_ids"]), unified_ids["calculations"]
                )
                self.assertLessEqual(set(step["brief_ids"]), unified_ids["briefs"])
                self.assertNotIn("result", step)
                self.assertNotIn("research_task", step)

    def test_identity_is_the_only_workflow_gate(self) -> None:
        request = single_security_request()
        request["source_policy"]["allow_experimental"] = False

        result = invoke_copied_installed(request)

        states = {
            step["step_id"]: step["state"]
            for step in result["task_views"]["research_workflow"]["steps"]
        }
        self.assertEqual(states["identity"], "blocked")
        self.assertEqual(
            {step_id: states[step_id] for step_id in states if step_id != "identity"},
            {
                "market_trend": "skipped_research_dependency",
                "valuation": "skipped_research_dependency",
                "materials": "skipped_research_dependency",
                "capital_activity": "skipped_research_dependency",
                "lockup_events": "skipped_research_dependency",
                "market_context": "skipped_research_dependency",
            },
        )

    def test_leaf_conflict_survives_with_independent_sibling_domains(self) -> None:
        result = invoke_copied_installed(
            single_security_request(),
            scenario="valuation_conflict",
        )

        self.assertEqual(result["status"], "limited")
        conflict = next(
            item
            for item in result["conflicts"]
            if item["code"] == "provider_market_cap_conflict"
        )
        views = {
            step["step_id"]: step
            for step in result["task_views"]["research_workflow"]["steps"]
        }
        self.assertIn(conflict["id"], views["valuation"]["conflict_ids"])
        self.assertTrue(views["materials"]["observation_ids"])
        self.assertTrue(views["capital_activity"]["calculation_ids"])
        self.assertTrue(views["market_context"]["brief_ids"])

    def test_standard_adds_subject_research_materials(self) -> None:
        result = invoke_copied_installed(single_security_request())

        steps = result["task_views"]["research_workflow"]["steps"]
        step_ids = [step["step_id"] for step in steps]
        self.assertIn("materials", step_ids)
        materials_step = next(step for step in steps if step["step_id"] == "materials")
        self.assertIn(materials_step["state"], {"supported", "limited"})
        registered = result["capability_registry"]["steps"]["materials"]
        self.assertEqual(registered["leaf_plan"]["task_type"], "research_content")
        self.assertIn(
            "research_report",
            registered["leaf_request_scope"]["parameters"]["material_types"],
        )
        materials = [
            item
            for item in result["observations"]
            if item["id"] in materials_step["observation_ids"]
            and item["payload"].get("kind") == "research_content_material"
        ]
        self.assertTrue(materials)
        self.assertIn(
            "research_report",
            {item["payload"]["material_type"] for item in materials},
        )

    def test_standard_material_scope_matches_subject_owner_contracts(self) -> None:
        result = invoke_copied_installed(single_security_request())

        materials_step = next(
            step
            for step in result["task_views"]["research_workflow"]["steps"]
            if step["step_id"] == "materials"
        )
        requested = result["capability_registry"]["steps"]["materials"][
            "leaf_request_scope"
        ]["parameters"]["material_types"]
        expected = [
            "research_report",
            "issuer_profile",
            "consensus_material",
            "stock_news",
            "announcement",
            "investor_qa",
        ]
        self.assertEqual(requested, expected)
        retained = {
            item["payload"]["material_type"]
            for item in result["observations"]
            if item["id"] in materials_step["observation_ids"]
            and item["payload"].get("kind") == "research_content_material"
        }
        self.assertEqual(retained, set(expected))

    def test_standard_materials_exercises_formal_mootdx_f10_owner(self) -> None:
        result = invoke_copied_installed(single_security_request())

        registered = result["capability_registry"]["steps"]["materials"]
        parameters = registered["leaf_request_scope"]["parameters"]
        self.assertIn("issuer_profile", parameters["material_types"])
        self.assertEqual(parameters["profile_categories"], ["公司概况"])
        operation = result["coverage"]["steps"]["materials"]["leaf"][
            "research_content_materials"
        ]["operations"]["mootdx_f10@1"]
        self.assertEqual(
            (operation["execution_state"], operation["state"]),
            ("succeeded", "complete"),
        )
        owned = [
            item
            for item in result["observations"]
            if item["operation_id"] == "mootdx_f10@1"
        ]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]["payload"]["material_type"], "issuer_profile")

    def test_standard_adds_subject_fund_flow(self) -> None:
        result = invoke_copied_installed(single_security_request())

        steps = result["task_views"]["research_workflow"]["steps"]
        step_ids = [step["step_id"] for step in steps]
        self.assertIn("capital_activity", step_ids)
        capital_step = next(
            step for step in steps if step["step_id"] == "capital_activity"
        )
        registered = result["capability_registry"]["steps"]["capital_activity"]
        self.assertEqual(registered["leaf_plan"]["task_type"], "capital_events")
        parameters = registered["leaf_request_scope"]["parameters"]
        self.assertIn("stock_fund_flow", parameters["data_types"])
        self.assertEqual(parameters["period"], "5d")
        self.assertEqual(parameters["limit"], 20)
        capital_observations = [
            item
            for item in result["observations"]
            if item["id"] in capital_step["observation_ids"]
            and item["payload"].get("kind") == "capital_event_observation"
        ]
        self.assertTrue(capital_observations)
        self.assertIn(
            "stock_fund_flow",
            {item["payload"]["data_type"] for item in capital_observations},
        )

    def test_standard_capital_scope_matches_past_subject_owner_contracts(
        self,
    ) -> None:
        result = invoke_copied_installed(single_security_request())

        capital_step = next(
            step
            for step in result["task_views"]["research_workflow"]["steps"]
            if step["step_id"] == "capital_activity"
        )
        request_scope = result["capability_registry"]["steps"]["capital_activity"][
            "leaf_request_scope"
        ]
        expected = [
            "stock_fund_flow",
            "dragon_tiger",
            "margin_trading",
            "block_trade",
            "shareholder_count",
            "dividend",
        ]
        self.assertEqual(request_scope["parameters"]["data_types"], expected)
        self.assertEqual(
            request_scope["window"],
            {"observed_from": "2026-05-04", "observed_to": "2026-08-02"},
        )
        retained = {
            item["payload"]["data_type"]
            for item in result["observations"]
            if item["id"] in capital_step["observation_ids"]
            and item["payload"].get("kind") == "capital_event_observation"
        }
        self.assertEqual(retained, set(expected))

    def test_standard_uses_a_separate_bounded_future_lockup_request(self) -> None:
        result = invoke_copied_installed(single_security_request())

        steps = result["task_views"]["research_workflow"]["steps"]
        self.assertIn("lockup_events", [step["step_id"] for step in steps])
        request_scope = result["capability_registry"]["steps"]["lockup_events"][
            "leaf_request_scope"
        ]
        self.assertEqual(
            request_scope["parameters"], {"data_types": ["lockup"], "limit": 20}
        )
        self.assertEqual(
            request_scope["window"],
            {"observed_from": "2026-08-02", "observed_to": "2026-10-31"},
        )
        lockup_step = next(step for step in steps if step["step_id"] == "lockup_events")
        retained = {
            item["payload"]["data_type"]
            for item in result["observations"]
            if item["id"] in lockup_step["observation_ids"]
            and item["payload"].get("kind") == "capital_event_observation"
        }
        self.assertEqual(retained, {"lockup"})

    def test_standard_adds_subject_board_membership(self) -> None:
        result = invoke_copied_installed(single_security_request())

        steps = result["task_views"]["research_workflow"]["steps"]
        step_ids = [step["step_id"] for step in steps]
        self.assertIn("market_context", step_ids)
        signal_step = next(
            step for step in steps if step["step_id"] == "market_context"
        )
        registered = result["capability_registry"]["steps"]["market_context"]
        self.assertEqual(registered["leaf_plan"]["task_type"], "market_signals")
        self.assertEqual(
            registered["leaf_request_scope"]["parameters"],
            {"signal_types": ["security_board_membership"], "limit": 20},
        )
        signal_observations = [
            item
            for item in result["observations"]
            if item["id"] in signal_step["observation_ids"]
            and item["payload"].get("kind") == "market_signal_observation"
        ]
        self.assertTrue(signal_observations)
        self.assertEqual(
            {item["payload"]["signal_type"] for item in signal_observations},
            {"security_board_membership"},
        )

    def test_fast_runs_core_only_and_marks_enhancements_not_requested(self) -> None:
        result = invoke_copied_installed(
            single_security_request(
                inputs={"target_pe": "30", "completeness_mode": "fast"}
            )
        )

        steps = result["task_views"]["research_workflow"]["steps"]
        states = {step["step_id"]: step["state"] for step in steps}
        self.assertIn(states["identity"], {"supported", "limited"})
        self.assertIn(states["market_trend"], {"supported", "limited"})
        self.assertEqual(
            {
                step_id: states[step_id]
                for step_id in (
                    "valuation",
                    "materials",
                    "capital_activity",
                    "lockup_events",
                    "market_context",
                )
            },
            {
                "valuation": "not_requested",
                "materials": "not_requested",
                "capital_activity": "not_requested",
                "lockup_events": "not_requested",
                "market_context": "not_requested",
            },
        )
        coverage = result["coverage"]["steps"]
        registry = result["capability_registry"]["steps"]
        for step_id in (
            "valuation",
            "materials",
            "capital_activity",
            "lockup_events",
            "market_context",
        ):
            self.assertEqual(coverage[step_id]["state"], "not_requested")
            self.assertEqual(coverage[step_id]["leaf"], {})
            self.assertIsNone(registry[step_id]["leaf_request_scope"])
            self.assertIsNone(registry[step_id]["leaf_plan"])
            self.assertEqual(registry[step_id]["capability_registry"], {})
            self.assertFalse(
                any(
                    step_id_record
                    for key, step_id_record in next(
                        step for step in steps if step["step_id"] == step_id
                    ).items()
                    if key.endswith("_ids")
                )
            )

    def test_fast_rejects_the_contradictory_large_materials_extension(self) -> None:
        with self.assertRaisesRegex(
            AssertionError,
            "fast cannot be combined with large_materials",
        ):
            invoke_copied_installed(
                single_security_request(
                    inputs={
                        "target_pe": "30",
                        "completeness_mode": "fast",
                        "deep_extensions": ["large_materials"],
                    }
                )
            )

    def test_inputs_reject_unknown_extensions_and_old_mode_shape(self) -> None:
        cases = (
            (
                {
                    "target_pe": "30",
                    "deep_extensions": ["intraday"],
                },
                "deep_extensions contains an unsupported value",
            ),
            (
                {"target_pe": "30", "mode": "fast"},
                "inputs do not match the workflow contract",
            ),
            (
                {"target_pe": "30", "completeness_mode": []},
                "completeness_mode must be standard or fast",
            ),
            (
                {"target_pe": "30", "deep_extensions": [["long_history"]]},
                "deep_extensions contains an unsupported value",
            ),
        )
        for inputs, diagnostic in cases:
            with self.subTest(inputs=inputs):
                with self.assertRaisesRegex(AssertionError, diagnostic):
                    invoke_copied_installed(single_security_request(inputs=inputs))

    def test_serializer_rejects_fully_rehashed_standard_as_fast_drift(self) -> None:
        forged = invoke_copied_installed(single_security_request())
        request_scope = forged["plan"]["request_scope"]
        request_scope["parameters"]["inputs"]["completeness_mode"] = "fast"
        plan = forged["plan"]
        plan["id"] = stable_result_id(
            "plan",
            {key: nested for key, nested in plan.items() if key != "id"},
        )

        with self.assertRaisesRegex(
            ValueError,
            "workflow fast mode executed an enhancement",
        ):
            validate_serialized(rehash_workflow_result(forged))

    def test_fast_allows_long_history_as_a_deepened_core_acquisition(self) -> None:
        result = invoke_copied_installed(
            single_security_request(
                inputs={
                    "target_pe": "30",
                    "completeness_mode": "fast",
                    "deep_extensions": ["long_history"],
                }
            )
        )

        trend_scope = result["capability_registry"]["steps"]["market_trend"][
            "leaf_request_scope"
        ]
        self.assertEqual(trend_scope["window"], {"trading_days": 250})
        states = {
            step["step_id"]: step["state"]
            for step in result["task_views"]["research_workflow"]["steps"]
        }
        self.assertTrue(
            all(
                states[step_id] == "not_requested"
                for step_id in (
                    "valuation",
                    "materials",
                    "capital_activity",
                    "lockup_events",
                    "market_context",
                )
            )
        )

    def test_explicit_long_history_deepens_the_single_trend_acquisition(self) -> None:
        result = invoke_copied_installed(
            single_security_request(
                inputs={"target_pe": "30", "deep_extensions": ["long_history"]}
            )
        )

        plan_steps = result["plan"]["steps"]
        self.assertEqual(
            [step["step_id"] for step in plan_steps].count("market_trend"),
            1,
        )
        registry = result["capability_registry"]["steps"]["market_trend"]
        self.assertEqual(
            registry["leaf_request_scope"]["window"],
            {"trading_days": 250},
        )
        trend_view = next(
            step
            for step in result["task_views"]["research_workflow"]["steps"]
            if step["step_id"] == "market_trend"
        )
        self.assertIn(trend_view["state"], {"supported", "limited"})

    def test_standard_and_long_history_retain_exact_requested_daily_facts(
        self,
    ) -> None:
        cases = (
            ({"target_pe": "30"}, 20),
            ({"target_pe": "30", "deep_extensions": ["long_history"]}, 250),
        )
        for inputs, expected_sessions in cases:
            with self.subTest(expected_sessions=expected_sessions):
                result = invoke_copied_installed(single_security_request(inputs=inputs))
                trend_ids = set(
                    next(
                        step
                        for step in result["task_views"]["research_workflow"]["steps"]
                        if step["step_id"] == "market_trend"
                    )["observation_ids"]
                )
                for owner in ("sse_daily_line@1", "tencent_daily_line@1"):
                    observations = [
                        item
                        for item in result["observations"]
                        if item["id"] in trend_ids and item["operation_id"] == owner
                    ]
                    dates = {
                        item["payload"]["observation"]["trading_date"]
                        for item in observations
                        if item["payload"]["observation"]["trading_status"] == "traded"
                    }
                    self.assertEqual(len(observations), expected_sessions)
                    self.assertEqual(len(dates), expected_sessions)

    def test_serializer_rejects_rehashed_250_day_scope_with_twenty_day_leaf_facts(
        self,
    ) -> None:
        forged = invoke_copied_installed(single_security_request())
        plan = forged["plan"]
        plan["request_scope"]["parameters"]["inputs"]["deep_extensions"] = [
            "long_history"
        ]
        plan["id"] = stable_result_id(
            "plan",
            {key: nested for key, nested in plan.items() if key != "id"},
        )
        forged["capability_registry"]["steps"]["market_trend"]["leaf_request_scope"][
            "window"
        ] = {"trading_days": 250}

        with self.assertRaisesRegex(ValueError, "market .*window|market .*scope"):
            validate_serialized(rehash_workflow_result(forged))

    def test_serializer_rejects_rehashed_market_summary_authority_drift(
        self,
    ) -> None:
        baseline = invoke_copied_installed(single_security_request())
        for authority, replacement in (
            (
                "subject",
                [
                    {
                        "security": {
                            "exchange": "SZSE",
                            "code": "000001",
                            "type": "A_SHARE",
                        },
                        "name": "平安银行",
                        "issuer": {
                            "name": "平安银行股份有限公司",
                            "identifier": None,
                            "security_relationship": "verified",
                        },
                    }
                ],
            ),
            ("as_of", "2026-08-01"),
            ("timezone", "UTC"),
            ("adjustment", "forward_adjusted"),
            ("retrieved_at", "2099-01-01T00:00:00+08:00"),
        ):
            with self.subTest(authority=authority):
                forged = deepcopy(baseline)
                summary = next(
                    item
                    for item in forged["claims"]
                    if item["kind"] == "market_trend_summary"
                )["payload"]
                if authority == "subject":
                    summary["subjects"] = replacement
                else:
                    summary["research"][authority] = replacement
                with self.assertRaisesRegex(
                    ValueError,
                    "market .*identity|market .*research|market .*subject",
                ):
                    validate_serialized(rehash_workflow_result(forged))

    def test_serializer_rejects_rehashed_default_leaf_owner_omission(self) -> None:
        forged = invoke_copied_installed(
            single_security_request(),
            scenario="default_inventory",
        )
        registered = forged["capability_registry"]["steps"]["materials"]
        leaf_plan = registered["leaf_plan"]
        leaf_plan["operation_obligations"] = [
            item
            for item in leaf_plan["operation_obligations"]
            if item["operation_id"] != "cls_market_flash@1"
        ]
        content_registry = registered["capability_registry"][
            "research_content_materials"
        ]
        content_registry["operations"] = [
            item
            for item in content_registry["operations"]
            if item["id"] != "cls_market_flash@1"
        ]
        aggregate = forged["coverage"]["steps"]["materials"]["leaf"][
            "research_content_materials"
        ]
        del aggregate["operations"]["cls_market_flash@1"]
        aggregate["state"] = "complete"
        leaf_coverage = forged["coverage"]["steps"]["materials"]["leaf"]
        leaf_plan["id"] = stable_result_id(
            "plan",
            {
                "version": leaf_plan["version"],
                "task_type": leaf_plan["task_type"],
                "operation_obligations": leaf_plan["operation_obligations"],
                "research_dependencies": leaf_plan["research_dependencies"],
                "field_dispositions": [
                    {
                        "capability_id": capability_id,
                        "operation_id": operation_id,
                        "fields": operation["field_dispositions"],
                    }
                    for capability_id, capability in leaf_coverage.items()
                    for operation_id, operation in capability["operations"].items()
                ],
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "workflow .*operation|workflow .*registry|workflow .*owner",
        ):
            validate_serialized(rehash_workflow_result(forged))

    def test_serializer_rejects_rehashed_f10_category_outside_workflow_scope(
        self,
    ) -> None:
        forged = invoke_copied_installed(single_security_request())
        observation = next(
            item
            for item in forged["observations"]
            if item["operation_id"] == "mootdx_f10@1"
        )["payload"]
        content = "工业富联公司大事公开聚合文本"
        observation["title"] = "工业富联 公司大事"
        observation["content"] = content
        observation["attributes"] = {
            "category": "公司大事",
            "character_count": len(content),
            "sha256": sha256(content.encode("utf-8")).hexdigest(),
        }

        with self.assertRaisesRegex(
            ValueError,
            "profile categor|issuer.profile|F10",
        ):
            validate_serialized(rehash_workflow_result(forged))

    def test_explicit_large_materials_deepens_the_single_material_acquisition(
        self,
    ) -> None:
        result = invoke_copied_installed(
            single_security_request(
                inputs={"target_pe": "30", "deep_extensions": ["large_materials"]}
            )
        )

        plan_steps = result["plan"]["steps"]
        self.assertEqual(
            [step["step_id"] for step in plan_steps].count("materials"),
            1,
        )
        request_scope = result["capability_registry"]["steps"]["materials"][
            "leaf_request_scope"
        ]
        self.assertEqual(
            request_scope["window"],
            {"published_from": "2025-08-02", "published_to": "2026-08-02"},
        )
        self.assertEqual(request_scope["parameters"]["limit"], 100)


if __name__ == "__main__":
    unittest.main()
