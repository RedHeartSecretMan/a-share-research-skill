from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from tests.test_direct_research_workflow_runtime import (
    _rehash_step_leaf_plan,
    _rehash_workflow,
    _validate_serialized,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
ISSUE_66_BOOTSTRAP = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "research_workflows"
    / "issue_66_sitecustomize.py"
).read_text(encoding="utf-8")


def market_overview_request(
    *,
    inputs: dict[str, Any] | None = None,
    allow_experimental: bool = False,
    allow_fallback: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": [],
        "as_of": "2026-08-03",
        "window": None,
        "parameters": {
            "workflow": {"id": "market_overview_research", "version": "2.0"},
            "inputs": {} if inputs is None else inputs,
        },
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": allow_fallback,
        },
    }


def invoke_copied_installed(
    request: dict[str, Any],
    *,
    bootstrap: str | None = None,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        installed = root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        environment = {
            **os.environ,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        foundation_bootstrap = (
            REPOSITORY_ROOT
            / "tests"
            / "fixtures"
            / "research_workflows"
            / "issue_96_sitecustomize.py"
        ).read_text(encoding="utf-8")
        (root / "sitecustomize.py").write_text(
            (bootstrap or "") + "\n" + foundation_bootstrap, encoding="utf-8"
        )
        environment["PYTHONPATH"] = str(root)
        environment.update(environment_overrides or {})
        return subprocess.run(
            [
                sys.executable,
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )


def _canonical_stdout_bytes(stdout: str) -> int:
    return len(
        json.dumps(
            json.loads(stdout),
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _market_overview_leaf_requests() -> dict[str, dict[str, Any]]:
    source_policy = {
        "allow_experimental": True,
        "allow_credentials": False,
        "allow_fallback": False,
    }
    observed_window = {
        "observed_from": "2026-08-03",
        "observed_to": "2026-08-03",
    }

    def request(
        task_type: str,
        *,
        window: dict[str, str],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "task_type": task_type,
            "subjects": [],
            "as_of": "2026-08-03",
            "window": window,
            "parameters": parameters,
            "source_policy": source_policy,
        }

    return {
        "market_core_signals": request(
            "market_signals",
            window=observed_window,
            parameters={
                "signal_types": ["industry_rotation", "limit_state"],
                "limit": 100,
            },
        ),
        "market_signal_enhancements": request(
            "market_signals",
            window=observed_window,
            parameters={
                "signal_types": [
                    "strong_stock_theme",
                    "focus_monitoring",
                    "severe_abnormal_movement",
                    "market_heat",
                ],
                "limit": 100,
            },
        ),
        "market_capital_enhancements": request(
            "capital_events",
            window=observed_window,
            parameters={
                "data_types": [
                    "board_fund_flow",
                    "market_dragon_tiger",
                    "northbound_flow",
                ],
                "board_type": "industry",
                "period": "today",
                "limit": 100,
            },
        ),
        "market_flash": request(
            "research_content",
            window={
                "published_from": "2026-08-03",
                "published_to": "2026-08-03",
            },
            parameters={
                "material_types": ["market_flash"],
                "query": [],
                "limit": 100,
                "verify_documents": False,
            },
        ),
    }


def _append_workflow_step_record(
    result: dict[str, Any],
    *,
    step_id: str,
    collection: str,
    record: dict[str, Any],
) -> None:
    reference_field = f"{collection[:-1]}_ids"
    record_id = record["id"]
    result[collection].append(record)
    workflow_view = result["task_views"]["research_workflow"]
    step = next(item for item in workflow_view["steps"] if item["step_id"] == step_id)
    step[reference_field].append(record_id)
    result["coverage"]["steps"][step_id][reference_field].append(record_id)
    leaf_brief = next(
        item for item in result["briefs"] if item["id"] == step["brief_ids"][0]
    )
    leaf_brief[reference_field].append(record_id)
    workflow_view[reference_field].append(record_id)
    main_brief = next(
        item for item in result["briefs"] if item["id"] == workflow_view["brief_id"]
    )
    main_brief[reference_field].append(record_id)


class MarketOverviewResearchTests(unittest.TestCase):
    def test_public_docs_route_market_overview_and_preserve_fixed_boundary(
        self,
    ) -> None:
        chinese = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        english = (REPOSITORY_ROOT / "README_en.md").read_text(encoding="utf-8")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        contract = (SKILL_ROOT / "references" / "workflow-contracts.md").read_text(
            encoding="utf-8"
        )
        examples_index = (REPOSITORY_ROOT / "examples" / "README.md").read_text(
            encoding="utf-8"
        )

        for document in (chinese, english, skill, contract):
            with self.subTest(document=document[:32]):
                self.assertIn("market_overview_research", document)
        for token in (
            "15 项固定能力",
            "5 项 core",
            "9 项 standard",
            "market_wide_intraday",
        ):
            self.assertIn(token, chinese)
        for token in (
            "15 fixed capabilities",
            "5 core",
            "9 standard",
            "market_wide_intraday",
        ):
            self.assertIn(token, english)
        self.assertIn("completion additionally requires empty subjects", skill)
        self.assertIn("Completion for this branch", contract)
        self.assertIn("requests/workflow-market-overview-research.json", examples_index)
        for capability_id in (
            "industry_board_breadth",
            "limit_pools",
            "industry_theme_rotation",
            "market_heat",
            "focus_monitoring",
            "severe_abnormal_movement",
            "board_fund_flow",
            "market_dragon_tiger",
            "northbound_disclosure",
            "market_flash",
            "market_trading_session",
            "major_broad_indices",
            "whole_market_turnover",
            "whole_market_margin",
            "market_wide_intraday",
            "free_anonymous_intraday_unproved",
            "future_scope",
        ):
            with self.subTest(capability_id=capability_id):
                self.assertIn(capability_id, contract)

        example = json.loads(
            (
                REPOSITORY_ROOT
                / "examples"
                / "requests"
                / "workflow-market-overview-research.json"
            ).read_text(encoding="utf-8")
        )
        expected_example = market_overview_request(
            inputs={"completeness_mode": "standard"},
            allow_experimental=True,
            allow_fallback=True,
        )
        expected_example["as_of"] = "2026-08-18"
        self.assertEqual(example, expected_example)

    def test_standard_declares_fixed_capability_inventory_without_fake_sources(
        self,
    ) -> None:
        completed = invoke_copied_installed(market_overview_request())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["plan"]["workflow_id"], "market_overview_research")
        self.assertEqual(result["plan"]["version"], "2.0")
        self.assertEqual(
            [
                (step["step_id"], step["task_type"], step["criticality"])
                for step in result["plan"]["steps"]
            ],
            [
                ("market_core_signals", "market_signals", "core"),
                (
                    "market_signal_enhancements",
                    "market_signals",
                    "standard_enrichment",
                ),
                (
                    "market_capital_enhancements",
                    "capital_events",
                    "standard_enrichment",
                ),
                ("market_flash", "research_content", "standard_enrichment"),
                ("market_trading_session", "overview_session", "core"),
                ("major_broad_indices", "overview_indices", "core"),
                ("whole_market_turnover", "overview_turnover", "core"),
                ("whole_market_margin", "overview_margin", "standard_enrichment"),
            ],
        )
        inventory = result["capability_registry"]["workflow"]["capability_inventory"]
        self.assertEqual(
            inventory,
            [
                {
                    "capability_id": "industry_board_breadth",
                    "tier": "core",
                    "state": "supported",
                    "step_ids": ["market_core_signals"],
                },
                {
                    "capability_id": "limit_pools",
                    "tier": "core",
                    "state": "supported",
                    "step_ids": ["market_core_signals"],
                },
                {
                    "capability_id": "industry_theme_rotation",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_signal_enhancements"],
                },
                {
                    "capability_id": "market_heat",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_signal_enhancements"],
                },
                {
                    "capability_id": "focus_monitoring",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_signal_enhancements"],
                },
                {
                    "capability_id": "severe_abnormal_movement",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_signal_enhancements"],
                },
                {
                    "capability_id": "board_fund_flow",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_capital_enhancements"],
                },
                {
                    "capability_id": "market_dragon_tiger",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_capital_enhancements"],
                },
                {
                    "capability_id": "northbound_disclosure",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_capital_enhancements"],
                },
                {
                    "capability_id": "market_flash",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["market_flash"],
                },
                {
                    "capability_id": "market_trading_session",
                    "tier": "core",
                    "state": "supported",
                    "step_ids": ["market_trading_session"],
                },
                {
                    "capability_id": "major_broad_indices",
                    "tier": "core",
                    "state": "supported",
                    "step_ids": ["major_broad_indices"],
                },
                {
                    "capability_id": "whole_market_turnover",
                    "tier": "core",
                    "state": "supported",
                    "step_ids": ["whole_market_turnover"],
                },
                {
                    "capability_id": "whole_market_margin",
                    "tier": "standard",
                    "state": "supported",
                    "step_ids": ["whole_market_margin"],
                },
                {
                    "capability_id": "market_wide_intraday",
                    "tier": "deep",
                    "state": "unsupported",
                    "reason_code": "free_anonymous_intraday_unproved",
                    "gap_route": "future_scope",
                    "step_ids": [],
                },
            ],
        )

        without_inventory = deepcopy(result)
        del without_inventory["capability_registry"]["workflow"]["capability_inventory"]
        serialized_facts = json.dumps(without_inventory, ensure_ascii=False)
        for capability_id in ("market_wide_intraday",):
            self.assertNotIn(capability_id, serialized_facts)

    def test_standard_core_preserves_formal_board_and_limit_pool_facts(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(allow_experimental=True),
            bootstrap=ISSUE_66_BOOTSTRAP,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        workflow_view = result["task_views"]["research_workflow"]
        core_step = next(
            item
            for item in workflow_view["steps"]
            if item["step_id"] == "market_core_signals"
        )
        core_observation_ids = set(core_step["observation_ids"])
        industry_rows = [
            item
            for item in result["observations"]
            if item["operation_id"] == "eastmoney_industry_rotation@1"
        ]
        self.assertEqual(len(industry_rows), 2)
        self.assertTrue(
            {item["id"] for item in industry_rows}.issubset(core_observation_ids)
        )
        self.assertEqual(
            [
                (
                    item["payload"]["dimensions"]["board_name"],
                    item["payload"]["metrics"]["change_rate"],
                    item["payload"]["metrics"]["up_count"],
                    item["payload"]["metrics"]["down_count"],
                    item["payload"]["dimensions"]["market_scope"],
                )
                for item in industry_rows
            ],
            [
                (
                    "软件开发",
                    "3.50",
                    "12",
                    "3",
                    "eastmoney_industry_boards",
                ),
                (
                    "煤炭开采",
                    "-1.25",
                    "4",
                    "10",
                    "eastmoney_industry_boards",
                ),
            ],
        )
        industry_facts = json.dumps(industry_rows, ensure_ascii=False).casefold()
        for forbidden in ("all_a_share", "all a-share", "全a股", "全市场个股"):
            self.assertNotIn(forbidden, industry_facts)

        operations = result["coverage"]["steps"]["market_core_signals"]["leaf"][
            "market_signal_observations"
        ]["operations"]
        self.assertEqual(
            {
                operation_id: (
                    operation["execution_state"],
                    operation["state"],
                )
                for operation_id, operation in operations.items()
            },
            {
                "ths_strong_stock_theme@1": ("not_requested", "not_requested"),
                "eastmoney_security_board_membership@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_industry_rotation@1": ("succeeded", "complete"),
                "eastmoney_limit_state@1": ("failed", "partial"),
                "ths_limit_reason@1": ("succeeded", "observed_empty"),
                "eastmoney_focus_monitoring@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_severe_abnormal_movement@1": (
                    "not_requested",
                    "not_requested",
                ),
                "ths_market_heat@1": ("not_requested", "not_requested"),
            },
        )
        self.assertEqual(
            operations["eastmoney_industry_rotation@1"]["diagnostics"][
                "signal_coverage"
            ]["industry_rotation"],
            {
                "state": "observed_nonempty",
                "provider_total": 2,
                "provider_total_status": "reported",
                "pages_collected": 1,
                "pages_expected": 1,
                "retained_count": 2,
                "truncated": False,
                "source_error_codes": [],
            },
        )
        eastmoney_limit = operations["eastmoney_limit_state@1"]["diagnostics"][
            "signal_coverage"
        ]["limit_state"]
        self.assertEqual(
            eastmoney_limit,
            {
                "state": "partial",
                "provider_total": 1,
                "provider_total_status": "reported",
                "pages_collected": 3,
                "pages_expected": 4,
                "retained_count": 1,
                "truncated": False,
                "source_error_codes": ["non_trading_day_or_invalid_date"],
                "pool_states": [
                    "limit_up",
                    "limit_break",
                    "limit_down",
                    "previous_limit_up",
                ],
                "completed_pool_states": [
                    "limit_break",
                    "limit_up",
                    "previous_limit_up",
                ],
                "failed_pool_states": ["limit_down"],
                "pool_errors": [
                    {
                        "pool_state": "limit_down",
                        "error_code": "non_trading_day_or_invalid_date",
                    }
                ],
                "duplicate_count": 0,
            },
        )
        self.assertEqual(
            operations["ths_limit_reason@1"]["diagnostics"]["signal_coverage"][
                "limit_state"
            ],
            {
                "state": "observed_empty",
                "provider_total": 0,
                "provider_total_status": "reported",
                "pages_collected": 1,
                "pages_expected": 1,
                "retained_count": 0,
                "truncated": False,
                "source_error_codes": [],
            },
        )
        limit_rows = [
            item
            for item in result["observations"]
            if item["operation_id"] == "eastmoney_limit_state@1"
        ]
        self.assertEqual(len(limit_rows), 1)
        self.assertIn(limit_rows[0]["id"], core_observation_ids)
        self.assertEqual(
            limit_rows[0]["payload"]["dimensions"]["pool_state"], "limit_up"
        )

    def test_standard_signal_enhancements_use_four_market_wide_owners(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(allow_experimental=True),
            bootstrap=ISSUE_66_BOOTSTRAP,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        enhancement_step = next(
            item
            for item in result["task_views"]["research_workflow"]["steps"]
            if item["step_id"] == "market_signal_enhancements"
        )
        operations = result["coverage"]["steps"]["market_signal_enhancements"]["leaf"][
            "market_signal_observations"
        ]["operations"]
        self.assertEqual(
            {
                operation_id: (
                    operation["execution_state"],
                    operation["state"],
                )
                for operation_id, operation in operations.items()
            },
            {
                "ths_strong_stock_theme@1": ("succeeded", "complete"),
                "eastmoney_security_board_membership@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_industry_rotation@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_limit_state@1": (
                    "not_requested",
                    "not_requested",
                ),
                "ths_limit_reason@1": ("not_requested", "not_requested"),
                "eastmoney_focus_monitoring@1": ("succeeded", "complete"),
                "eastmoney_severe_abnormal_movement@1": (
                    "succeeded",
                    "complete",
                ),
                "ths_market_heat@1": ("succeeded", "complete"),
            },
        )
        expected_owner_types = {
            "ths_strong_stock_theme@1": "strong_stock_theme",
            "eastmoney_focus_monitoring@1": "focus_monitoring",
            "eastmoney_severe_abnormal_movement@1": ("severe_abnormal_movement"),
            "ths_market_heat@1": "market_heat",
        }
        records = [
            item
            for item in result["observations"]
            if item["operation_id"] in expected_owner_types
        ]
        self.assertEqual(len(records), 4)
        self.assertEqual(
            {item["operation_id"]: item["payload"]["signal_type"] for item in records},
            expected_owner_types,
        )
        self.assertTrue(
            {item["id"] for item in records}.issubset(
                set(enhancement_step["observation_ids"])
            )
        )
        for record in records:
            self.assertIsNone(record["payload"]["subject"])
            self.assertFalse(record["payload"]["issuer_fact"])
            self.assertFalse(record["payload"]["certain_causality"])

        expected_coverage = {
            "ths_strong_stock_theme@1": (
                "strong_stock_theme",
                1,
                "reported",
            ),
            "eastmoney_focus_monitoring@1": (
                "focus_monitoring",
                1,
                "reported",
            ),
            "eastmoney_severe_abnormal_movement@1": (
                "severe_abnormal_movement",
                None,
                "not_exposed",
            ),
            "ths_market_heat@1": ("market_heat", None, "not_exposed"),
        }
        for owner, (
            signal_type,
            provider_total,
            provider_total_status,
        ) in expected_coverage.items():
            material = operations[owner]["diagnostics"]["signal_coverage"][signal_type]
            self.assertEqual(material["state"], "observed_nonempty")
            self.assertEqual(material["provider_total"], provider_total)
            self.assertEqual(material["provider_total_status"], provider_total_status)
            self.assertEqual(material["pages_collected"], 1)
            self.assertEqual(material["pages_expected"], 1)
            self.assertEqual(material["retained_count"], 1)
            self.assertFalse(material["truncated"])

    def test_standard_capital_enhancements_preserve_market_owner_truth(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(allow_experimental=True),
            bootstrap=ISSUE_66_BOOTSTRAP,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        leaf_request_scope = result["capability_registry"]["steps"][
            "market_capital_enhancements"
        ]["leaf_request_scope"]
        self.assertEqual(
            leaf_request_scope["parameters"],
            {
                "data_types": [
                    "board_fund_flow",
                    "market_dragon_tiger",
                    "northbound_flow",
                ],
                "board_type": "industry",
                "period": "today",
                "limit": 100,
            },
        )
        capital_step = next(
            item
            for item in result["task_views"]["research_workflow"]["steps"]
            if item["step_id"] == "market_capital_enhancements"
        )
        operations = result["coverage"]["steps"]["market_capital_enhancements"]["leaf"][
            "capital_event_observations"
        ]["operations"]
        self.assertEqual(
            {
                operation_id: (
                    operation["execution_state"],
                    operation["state"],
                )
                for operation_id, operation in operations.items()
            },
            {
                "eastmoney_northbound_flow@1": ("failed", "failed"),
                "eastmoney_stock_fund_flow@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_board_fund_flow@1": ("succeeded", "complete"),
                "eastmoney_trading_events@1": ("succeeded", "complete"),
                "eastmoney_margin_trading@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_shareholder_count@1": (
                    "not_requested",
                    "not_requested",
                ),
                "eastmoney_dividend@1": ("not_requested", "not_requested"),
            },
        )
        expected_owner_types = {
            "eastmoney_board_fund_flow@1": "board_fund_flow",
            "eastmoney_trading_events@1": "market_dragon_tiger",
        }
        records = [
            item
            for item in result["observations"]
            if item["operation_id"] in expected_owner_types
        ]
        self.assertEqual(
            {
                owner: len([item for item in records if item["operation_id"] == owner])
                for owner in expected_owner_types
            },
            {
                "eastmoney_board_fund_flow@1": 2,
                "eastmoney_trading_events@1": 1,
            },
        )
        self.assertEqual(
            {item["operation_id"]: item["payload"]["data_type"] for item in records},
            expected_owner_types,
        )
        self.assertTrue(
            {item["id"] for item in records}.issubset(
                set(capital_step["observation_ids"])
            )
        )
        self.assertFalse(
            any(
                item["operation_id"] == "eastmoney_northbound_flow@1"
                for item in result["observations"]
            )
        )
        northbound_errors = [
            item
            for item in result["source_errors"]
            if item["operation_id"] == "eastmoney_northbound_flow@1"
        ]
        self.assertEqual(len(northbound_errors), 1)
        self.assertEqual(
            (
                northbound_errors[0]["code"],
                northbound_errors[0]["message"],
            ),
            (
                "northbound_net_buy_disclosure_unavailable",
                "Daily northbound net-buy amounts are unavailable at or after "
                "the disclosure boundary.",
            ),
        )
        self.assertIn(northbound_errors[0]["id"], capital_step["source_error_ids"])
        calculations = {
            item["payload"]["data_type"]: item["payload"]
            for item in result["calculations"]
            if item["kind"] == "capital_event_coverage"
            and item["id"] in capital_step["calculation_ids"]
        }
        self.assertEqual(
            set(calculations), set(expected_owner_types.values()) | {"northbound_flow"}
        )
        self.assertEqual(calculations["board_fund_flow"]["state"], "observed_nonempty")
        self.assertEqual(calculations["board_fund_flow"]["observation_count"], 2)
        self.assertEqual(
            calculations["market_dragon_tiger"]["state"], "observed_nonempty"
        )
        self.assertEqual(calculations["market_dragon_tiger"]["observation_count"], 1)
        self.assertEqual(calculations["northbound_flow"]["state"], "indeterminate")
        self.assertEqual(calculations["northbound_flow"]["observation_count"], 0)

    def test_standard_market_flash_uses_controlled_default_fallback(self) -> None:
        completed_by_scenario = {
            "primary": invoke_copied_installed(
                market_overview_request(
                    allow_experimental=True,
                    allow_fallback=False,
                ),
                bootstrap=ISSUE_66_BOOTSTRAP,
                environment_overrides={"A_SHARE_ISSUE66_FLASH": "primary"},
            ),
            "fallback": invoke_copied_installed(
                market_overview_request(
                    allow_experimental=True,
                    allow_fallback=True,
                ),
                bootstrap=ISSUE_66_BOOTSTRAP,
                environment_overrides={"A_SHARE_ISSUE66_FLASH": "fallback"},
            ),
        }
        results: dict[str, dict[str, Any]] = {}
        for scenario, completed in completed_by_scenario.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                results[scenario] = json.loads(completed.stdout)

        primary_content_owner_ids = {
            "eastmoney_reports@1",
            "ths_consensus_material@1",
            "eastmoney_stock_news@1",
            "cninfo_announcement@1",
            "sse_announcement@1",
            "szse_announcement@1",
            "cls_market_flash@1",
            "cninfo_investor_qa@1",
            "mootdx_f10@1",
        }
        content_owner_ids = {
            "primary": primary_content_owner_ids,
            "fallback": primary_content_owner_ids | {"eastmoney_market_flash@1"},
        }
        expected_flash_states = {
            "primary": {
                "cls_market_flash@1": ("succeeded", "partial"),
            },
            "fallback": {
                "cls_market_flash@1": ("failed", "failed"),
                "eastmoney_market_flash@1": ("succeeded", "partial"),
            },
        }
        for scenario, result in results.items():
            with self.subTest(scenario=scenario):
                operations = result["coverage"]["steps"]["market_flash"]["leaf"]
                operations = operations["research_content_materials"]["operations"]
                self.assertEqual(set(operations), content_owner_ids[scenario])
                leaf_registry = result["capability_registry"]["steps"]["market_flash"][
                    "capability_registry"
                ]["research_content_materials"]
                self.assertEqual(
                    {item["id"] for item in leaf_registry["operations"]},
                    content_owner_ids[scenario],
                )
                leaf_plan = result["capability_registry"]["steps"]["market_flash"]
                leaf_plan = leaf_plan["leaf_plan"]
                self.assertEqual(
                    {
                        item["producer_operation_id"]
                        for item in leaf_plan["operation_obligations"]
                    },
                    content_owner_ids[scenario],
                )
                self.assertEqual(
                    {
                        owner: (
                            operations[owner]["execution_state"],
                            operations[owner]["state"],
                        )
                        for owner in expected_flash_states[scenario]
                    },
                    expected_flash_states[scenario],
                )
                self.assertTrue(
                    all(
                        operation["execution_state"] == "not_requested"
                        and operation["state"] == "not_requested"
                        for owner, operation in operations.items()
                        if owner
                        not in {
                            "cls_market_flash@1",
                            "eastmoney_market_flash@1",
                        }
                    )
                )
                expected_owner = (
                    "cls_market_flash@1"
                    if scenario == "primary"
                    else "eastmoney_market_flash@1"
                )
                materials = [
                    item
                    for item in result["observations"]
                    if item["operation_id"]
                    in {"cls_market_flash@1", "eastmoney_market_flash@1"}
                ]
                self.assertEqual(len(materials), 1)
                self.assertEqual(materials[0]["operation_id"], expected_owner)
                self.assertEqual(
                    materials[0]["payload"]["material_type"], "market_flash"
                )
                self.assertEqual(
                    materials[0]["payload"]["source_role"], "market_signal"
                )
                flash_step = next(
                    item
                    for item in result["task_views"]["research_workflow"]["steps"]
                    if item["step_id"] == "market_flash"
                )
                self.assertIn(materials[0]["id"], flash_step["observation_ids"])
                for sibling in result["task_views"]["research_workflow"]["steps"]:
                    if sibling["step_id"] != "market_flash":
                        self.assertNotIn(materials[0]["id"], sibling["observation_ids"])

        fallback = results["fallback"]
        fallback_step = next(
            item
            for item in fallback["task_views"]["research_workflow"]["steps"]
            if item["step_id"] == "market_flash"
        )
        fallback_limitations = [
            item for item in fallback["limitations"] if item["code"] == "fallback_used"
        ]
        self.assertEqual(
            fallback_limitations,
            [
                {
                    "id": fallback_limitations[0]["id"],
                    "code": "fallback_used",
                    "message": "The material carries a source-specific limitation.",
                    "references": [],
                }
            ],
        )
        self.assertIn(fallback_limitations[0]["id"], fallback_step["limitation_ids"])
        cls_errors = [
            item
            for item in fallback["source_errors"]
            if item["operation_id"] == "cls_market_flash@1"
        ]
        self.assertEqual(
            [(item["code"], item["message"]) for item in cls_errors],
            [("empty_response", "The CLS rolling feed returned no market flashes.")],
        )
        self.assertIn(cls_errors[0]["id"], fallback_step["source_error_ids"])
        for sibling in fallback["task_views"]["research_workflow"]["steps"]:
            if sibling["step_id"] != "market_flash":
                self.assertNotIn(
                    fallback_limitations[0]["id"], sibling["limitation_ids"]
                )
                self.assertNotIn(cls_errors[0]["id"], sibling["source_error_ids"])

    def test_fast_executes_only_core_and_fails_closed_on_beautification(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(
                inputs={"completeness_mode": "fast"},
                allow_experimental=True,
            ),
            bootstrap=ISSUE_66_BOOTSTRAP,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        inventory = {
            item["capability_id"]: item
            for item in result["capability_registry"]["workflow"][
                "capability_inventory"
            ]
        }
        self.assertEqual(
            {
                capability_id: inventory[capability_id]["state"]
                for capability_id in (
                    "industry_board_breadth",
                    "limit_pools",
                )
            },
            {
                "industry_board_breadth": "supported",
                "limit_pools": "supported",
            },
        )
        standard_capability_ids = {
            "industry_theme_rotation",
            "market_heat",
            "focus_monitoring",
            "severe_abnormal_movement",
            "board_fund_flow",
            "market_dragon_tiger",
            "northbound_disclosure",
            "market_flash",
        }
        self.assertEqual(
            {
                capability_id: inventory[capability_id]["state"]
                for capability_id in standard_capability_ids
            },
            {
                capability_id: "not_requested"
                for capability_id in standard_capability_ids
            },
        )
        unsupported_capability_ids = {"market_wide_intraday"}
        self.assertEqual(
            {
                capability_id: (
                    inventory[capability_id]["state"],
                    inventory[capability_id]["reason_code"],
                    inventory[capability_id]["gap_route"],
                    inventory[capability_id]["step_ids"],
                )
                for capability_id in unsupported_capability_ids
            },
            {
                capability_id: (
                    "unsupported",
                    "free_anonymous_intraday_unproved",
                    "future_scope",
                    [],
                )
                for capability_id in unsupported_capability_ids
            },
        )

        steps = {
            item["step_id"]: item
            for item in result["task_views"]["research_workflow"]["steps"]
        }
        self.assertNotEqual(steps["market_core_signals"]["state"], "not_requested")
        core_operations = result["coverage"]["steps"]["market_core_signals"]["leaf"][
            "market_signal_observations"
        ]["operations"]
        self.assertEqual(
            core_operations["eastmoney_industry_rotation@1"]["execution_state"],
            "succeeded",
        )
        self.assertEqual(core_operations["eastmoney_limit_state@1"]["state"], "partial")
        empty_reference_fields = (
            "observation_ids",
            "claim_ids",
            "calculation_ids",
            "conflict_ids",
            "limitation_ids",
            "source_error_ids",
            "workflow_limitation_ids",
            "brief_ids",
        )
        for step_id in (
            "market_signal_enhancements",
            "market_capital_enhancements",
            "market_flash",
        ):
            with self.subTest(step_id=step_id):
                step = steps[step_id]
                self.assertEqual(step["state"], "not_requested")
                self.assertTrue(
                    all(step[field] == [] for field in empty_reference_fields)
                )
                self.assertEqual(
                    result["coverage"]["steps"][step_id],
                    {
                        "state": "not_requested",
                        "leaf": {},
                        **{field: [] for field in empty_reference_fields},
                    },
                )
                registered = result["capability_registry"]["steps"][step_id]
                self.assertIsNone(registered["leaf_request_scope"])
                self.assertIsNone(registered["leaf_plan"])
                self.assertEqual(registered["capability_registry"], {})

        forged_capability = deepcopy(result)
        next(
            item
            for item in forged_capability["capability_registry"]["workflow"][
                "capability_inventory"
            ]
            if item["capability_id"] == "market_heat"
        )["state"] = "supported"
        with self.assertRaises(ValueError):
            _validate_serialized(_rehash_workflow(forged_capability))

        forged_step = deepcopy(result)
        forged_step_ref = next(
            item
            for item in forged_step["task_views"]["research_workflow"]["steps"]
            if item["step_id"] == "market_signal_enhancements"
        )
        forged_step_ref["state"] = "supported"
        forged_step["coverage"]["steps"]["market_signal_enhancements"]["state"] = (
            "complete"
        )
        with self.assertRaises(ValueError):
            _validate_serialized(_rehash_workflow(forged_step))

    def test_explicit_unsupported_deep_is_visible_without_execution(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(
                inputs={"deep_extensions": ["market_wide_intraday"]}
            )
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["plan"]["request_scope"]["parameters"]["inputs"],
            {"deep_extensions": ["market_wide_intraday"]},
        )
        self.assertEqual(
            [item["step_id"] for item in result["plan"]["steps"]],
            [
                "market_core_signals",
                "market_signal_enhancements",
                "market_capital_enhancements",
                "market_flash",
                "market_trading_session",
                "major_broad_indices",
                "whole_market_turnover",
                "whole_market_margin",
            ],
        )
        inventory = result["capability_registry"]["workflow"]["capability_inventory"]
        self.assertEqual(len(inventory), 15)
        deep_capability = next(
            item
            for item in inventory
            if item["capability_id"] == "market_wide_intraday"
        )
        self.assertEqual(
            deep_capability,
            {
                "capability_id": "market_wide_intraday",
                "tier": "deep",
                "state": "unsupported",
                "reason_code": "free_anonymous_intraday_unproved",
                "gap_route": "future_scope",
                "step_ids": [],
            },
        )
        self.assertEqual(
            json.dumps(result, ensure_ascii=False).count("market_wide_intraday"),
            2,
        )
        self.assertFalse(
            any(
                "intraday" in item["producer_operation_id"]
                for step in result["capability_registry"]["steps"].values()
                for item in (
                    step["leaf_plan"]["operation_obligations"]
                    if step["leaf_plan"] is not None
                    else []
                )
            )
        )

        invalid_cases = {
            "non_string_mode": {
                "completeness_mode": [],
            },
            "unknown": {
                "deep_extensions": ["five_level_order_book"],
            },
            "duplicate": {
                "deep_extensions": [
                    "market_wide_intraday",
                    "market_wide_intraday",
                ],
            },
            "old_mapping_shape": {
                "deep_extensions": {"market_wide_intraday": True},
            },
        }
        for label, inputs in invalid_cases.items():
            with self.subTest(label=label):
                invalid = invoke_copied_installed(
                    market_overview_request(inputs=inputs)
                )
                self.assertEqual(invalid.returncode, 2)
                self.assertEqual(invalid.stdout, "")
                if label == "non_string_mode":
                    self.assertEqual(
                        invalid.stderr,
                        "error: invalid research task: market_overview_research "
                        "completeness_mode must be standard or fast\n",
                    )
                else:
                    self.assertTrue(
                        invalid.stderr.startswith("error: invalid research task:")
                    )

        forged_missing = deepcopy(result)
        forged_missing["capability_registry"]["workflow"]["capability_inventory"] = [
            item
            for item in forged_missing["capability_registry"]["workflow"][
                "capability_inventory"
            ]
            if item["capability_id"] != "market_wide_intraday"
        ]
        with self.assertRaises(ValueError):
            _validate_serialized(_rehash_workflow(forged_missing))

        forged_gap = deepcopy(result)
        next(
            item
            for item in forged_gap["capability_registry"]["workflow"][
                "capability_inventory"
            ]
            if item["capability_id"] == "market_wide_intraday"
        )["gap_route"] = "v0.5.0_spec"
        with self.assertRaises(ValueError):
            _validate_serialized(_rehash_workflow(forged_gap))

        forged_step = deepcopy(result)
        next(
            item
            for item in forged_step["capability_registry"]["workflow"][
                "capability_inventory"
            ]
            if item["capability_id"] == "market_wide_intraday"
        )["step_ids"] = ["market_core_signals"]
        with self.assertRaises(ValueError):
            _validate_serialized(_rehash_workflow(forged_step))

    def test_standard_keeps_partial_empty_failure_and_sibling_lineage(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(allow_experimental=True),
            bootstrap=ISSUE_66_BOOTSTRAP,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["task_views"]["research_workflow"]["status"], "limited")
        steps = {
            item["step_id"]: item
            for item in result["task_views"]["research_workflow"]["steps"]
        }
        self.assertEqual(
            {step_id: step["state"] for step_id, step in steps.items()},
            {
                "market_core_signals": "limited",
                "market_signal_enhancements": "limited",
                "market_capital_enhancements": "limited",
                "market_flash": "limited",
                "market_trading_session": "supported",
                "major_broad_indices": "supported",
                "whole_market_turnover": "supported",
                "whole_market_margin": "supported",
            },
        )
        owner_steps = {
            "eastmoney_industry_rotation@1": "market_core_signals",
            "eastmoney_limit_state@1": "market_core_signals",
            "ths_strong_stock_theme@1": "market_signal_enhancements",
            "eastmoney_focus_monitoring@1": "market_signal_enhancements",
            "eastmoney_severe_abnormal_movement@1": ("market_signal_enhancements"),
            "ths_market_heat@1": "market_signal_enhancements",
            "eastmoney_board_fund_flow@1": "market_capital_enhancements",
            "eastmoney_trading_events@1": "market_capital_enhancements",
            "cls_market_flash@1": "market_flash",
        }
        observations_by_owner = {
            owner: [
                item for item in result["observations"] if item["operation_id"] == owner
            ]
            for owner in owner_steps
        }
        self.assertEqual(
            {owner: len(records) for owner, records in observations_by_owner.items()},
            {
                "eastmoney_industry_rotation@1": 2,
                "eastmoney_limit_state@1": 1,
                "ths_strong_stock_theme@1": 1,
                "eastmoney_focus_monitoring@1": 1,
                "eastmoney_severe_abnormal_movement@1": 1,
                "ths_market_heat@1": 1,
                "eastmoney_board_fund_flow@1": 2,
                "eastmoney_trading_events@1": 1,
                "cls_market_flash@1": 1,
            },
        )
        for owner, step_id in owner_steps.items():
            for record in observations_by_owner[owner]:
                self.assertIn(record["id"], steps[step_id]["observation_ids"])
                for sibling_id, sibling in steps.items():
                    if sibling_id != step_id:
                        self.assertNotIn(record["id"], sibling["observation_ids"])
        workflow_observation_ids = set(
            result["task_views"]["research_workflow"]["observation_ids"]
        )
        self.assertEqual(
            workflow_observation_ids,
            {
                record["id"]
                for records in observations_by_owner.values()
                for record in records
            }
            | {
                record["id"]
                for record in result["observations"]
                if record["capability_id"]
                in {
                    "market_trading_session",
                    "major_broad_indices",
                    "whole_market_turnover",
                    "whole_market_margin",
                }
            },
        )

        core_operations = result["coverage"]["steps"]["market_core_signals"]["leaf"][
            "market_signal_observations"
        ]["operations"]
        self.assertEqual(core_operations["eastmoney_limit_state@1"]["state"], "partial")
        self.assertEqual(
            core_operations["ths_limit_reason@1"]["state"], "observed_empty"
        )
        capital_operations = result["coverage"]["steps"]["market_capital_enhancements"][
            "leaf"
        ]["capital_event_observations"]["operations"]
        self.assertEqual(
            capital_operations["eastmoney_northbound_flow@1"]["state"], "failed"
        )
        owner_errors = {
            item["operation_id"]: item["id"] for item in result["source_errors"]
        }
        self.assertIn(
            owner_errors["eastmoney_limit_state@1"],
            steps["market_core_signals"]["source_error_ids"],
        )
        self.assertIn(
            owner_errors["eastmoney_northbound_flow@1"],
            steps["market_capital_enhancements"]["source_error_ids"],
        )
        self.assertNotIn(
            owner_errors["eastmoney_limit_state@1"],
            steps["market_capital_enhancements"]["source_error_ids"],
        )
        self.assertNotIn(
            owner_errors["eastmoney_northbound_flow@1"],
            steps["market_core_signals"]["source_error_ids"],
        )

        forged = deepcopy(result)
        step_id = "market_signal_enhancements"
        omitted_owner = "eastmoney_security_board_membership@1"
        step_registry = forged["capability_registry"]["steps"][step_id]
        leaf_plan = step_registry["leaf_plan"]
        leaf_plan["operation_obligations"] = [
            item
            for item in leaf_plan["operation_obligations"]
            if item["producer_operation_id"] != omitted_owner
        ]
        leaf_registry = step_registry["capability_registry"][
            "market_signal_observations"
        ]
        leaf_registry["operations"] = [
            item for item in leaf_registry["operations"] if item["id"] != omitted_owner
        ]
        del forged["coverage"]["steps"][step_id]["leaf"]["market_signal_observations"][
            "operations"
        ][omitted_owner]
        _rehash_step_leaf_plan(forged, step_id)
        with self.assertRaisesRegex(
            ValueError,
            "workflow default operation inventory contradicts its leaf",
        ):
            _validate_serialized(_rehash_workflow(forged))

    def test_max_cardinality_standard_preserves_all_leaf_facts_within_envelope(
        self,
    ) -> None:
        environment = {"A_SHARE_ISSUE66_ROWS": "75"}
        direct_results: dict[str, dict[str, Any]] = {}
        leaf_bytes: dict[str, int] = {}
        expected_observation_counts = {
            "market_core_signals": 150,
            "market_signal_enhancements": 226,
            "market_capital_enhancements": 150,
            "market_flash": 75,
        }
        for step_id, request in _market_overview_leaf_requests().items():
            with self.subTest(route=step_id):
                completed = invoke_copied_installed(
                    request,
                    bootstrap=ISSUE_66_BOOTSTRAP,
                    environment_overrides=environment,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                result = json.loads(completed.stdout)
                self.assertEqual(
                    len(result["observations"]),
                    expected_observation_counts[step_id],
                )
                direct_results[step_id] = result
                leaf_bytes[step_id] = _canonical_stdout_bytes(completed.stdout)
                self.assertLessEqual(leaf_bytes[step_id], 2 * 1024 * 1024)

        oversized_direct = invoke_copied_installed(
            _market_overview_leaf_requests()["market_core_signals"],
            bootstrap=ISSUE_66_BOOTSTRAP,
            environment_overrides={
                "A_SHARE_ISSUE66_ROWS": "1",
                "A_SHARE_ISSUE66_PAD": "600000",
            },
        )
        self.assertEqual(oversized_direct.returncode, 2)
        self.assertEqual(oversized_direct.stdout, "")
        self.assertEqual(
            oversized_direct.stderr,
            "error: invalid research task: research result exceeds its bounded JSON "
            "size\n",
        )

        overview = invoke_copied_installed(
            market_overview_request(allow_experimental=True),
            bootstrap=ISSUE_66_BOOTSTRAP,
            environment_overrides=environment,
        )
        direct_bytes = {
            "leaf_canonical_bytes": leaf_bytes,
            "direct_leaf_aggregate_bytes": sum(leaf_bytes.values()),
            "overview_stdout_bytes": len(overview.stdout.encode("utf-8")),
        }
        self.assertEqual(
            overview.returncode,
            0,
            f"{json.dumps(direct_bytes, sort_keys=True)}; {overview.stderr}",
        )
        self.assertEqual(overview.stderr, "")
        aggregate = json.loads(overview.stdout)
        self.assertEqual(aggregate["plan"]["workflow_id"], "market_overview_research")
        self.assertEqual(
            [item["step_id"] for item in aggregate["plan"]["steps"]],
            [
                "market_core_signals",
                "market_signal_enhancements",
                "market_capital_enhancements",
                "market_flash",
                "market_trading_session",
                "major_broad_indices",
                "whole_market_turnover",
                "whole_market_margin",
            ],
        )
        aggregate_bytes = _canonical_stdout_bytes(overview.stdout)
        self.assertGreater(aggregate_bytes, 2_509_404)
        self.assertLessEqual(aggregate_bytes, 32 * 1024 * 1024)
        expected_owner_counts = {
            "eastmoney_industry_rotation@1": 75,
            "eastmoney_limit_state@1": 75,
            "ths_strong_stock_theme@1": 75,
            "eastmoney_focus_monitoring@1": 75,
            "eastmoney_severe_abnormal_movement@1": 1,
            "ths_market_heat@1": 75,
            "eastmoney_board_fund_flow@1": 75,
            "eastmoney_trading_events@1": 75,
            "cls_market_flash@1": 75,
        }
        self.assertEqual(
            {
                owner: sum(
                    item["operation_id"] == owner for item in aggregate["observations"]
                )
                for owner in expected_owner_counts
            },
            expected_owner_counts,
        )
        overview_steps = {
            item["step_id"]: item
            for item in aggregate["task_views"]["research_workflow"]["steps"]
        }
        for step_id, direct in direct_results.items():
            leaf_view = direct["task_views"][direct["plan"]["task_type"]]
            for reference_field in (
                "observation_ids",
                "claim_ids",
                "calculation_ids",
                "conflict_ids",
                "limitation_ids",
                "source_error_ids",
            ):
                with self.subTest(step_id=step_id, field=reference_field):
                    self.assertEqual(
                        overview_steps[step_id][reference_field],
                        leaf_view[reference_field],
                    )

    def test_unsupported_capability_ids_cannot_escape_into_formal_facts(self) -> None:
        completed = invoke_copied_installed(
            market_overview_request(allow_experimental=True),
            bootstrap=ISSUE_66_BOOTSTRAP,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        step_id = "market_core_signals"
        step = next(
            item
            for item in result["task_views"]["research_workflow"]["steps"]
            if item["step_id"] == step_id
        )
        unsupported_id = "market_wide_intraday"
        source_claim = next(
            item for item in result["claims"] if item["id"] in step["claim_ids"]
        )
        source_calculation = next(
            item
            for item in result["calculations"]
            if item["id"] in step["calculation_ids"]
        )
        cases = {
            "claim_kind": (
                "claims",
                {
                    **deepcopy(source_claim),
                    "id": "forged-unsupported-claim",
                    "kind": unsupported_id,
                    "payload": {
                        "capability_id": unsupported_id,
                        "market_scope": "mainland_a_share",
                        "turnover": "1000000000000",
                        "unit": "CNY",
                    },
                },
            ),
            "calculation_kind": (
                "calculations",
                {
                    **deepcopy(source_calculation),
                    "id": "forged-unsupported-calculation",
                    "kind": unsupported_id,
                    "payload": {
                        "capability_id": unsupported_id,
                        "market_scope": "mainland_a_share",
                        "turnover": "1000000000000",
                        "unit": "CNY",
                    },
                },
            ),
            "payload_capability_id": (
                "calculations",
                {
                    **deepcopy(source_calculation),
                    "id": "forged-unsupported-payload-calculation",
                    "kind": "workflow_metric",
                    "payload": {
                        "capability_id": unsupported_id,
                        "market_scope": "mainland_a_share",
                        "turnover": "1000000000000",
                        "unit": "CNY",
                    },
                },
            ),
        }
        for label, (collection, record) in cases.items():
            with self.subTest(record_kind=label):
                forged = deepcopy(result)
                _append_workflow_step_record(
                    forged,
                    step_id=step_id,
                    collection=collection,
                    record=record,
                )
                fully_rehashed = _rehash_workflow(forged)
                self.assertIn(
                    unsupported_id,
                    json.dumps(fully_rehashed[collection], sort_keys=True),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "unsupported market overview capability escaped into formal facts",
                ):
                    _validate_serialized(fully_rehashed)

        malformed = deepcopy(result)
        malformed_record = {
            **deepcopy(source_calculation),
            "id": "forged-malformed-calculation-kind",
            "kind": [],
        }
        _append_workflow_step_record(
            malformed,
            step_id=step_id,
            collection="calculations",
            record=malformed_record,
        )
        with self.assertRaisesRegex(
            ValueError,
            "workflow formal fact kind must be a string",
        ):
            _validate_serialized(_rehash_workflow(malformed))

    def test_direct_market_tasks_remain_narrow_leaf_routes(self) -> None:
        source_policy = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        }
        cases = {
            "market_signals": {
                "schema_version": "1.0",
                "task_type": "market_signals",
                "subjects": [],
                "as_of": "2026-08-03",
                "window": {
                    "observed_from": "2026-08-03",
                    "observed_to": "2026-08-03",
                },
                "parameters": {
                    "signal_types": ["industry_rotation"],
                    "limit": 100,
                },
                "source_policy": source_policy,
            },
            "capital_events": {
                "schema_version": "1.0",
                "task_type": "capital_events",
                "subjects": [],
                "as_of": "2026-08-03",
                "window": {
                    "observed_from": "2026-08-03",
                    "observed_to": "2026-08-03",
                },
                "parameters": {
                    "data_types": ["board_fund_flow"],
                    "board_type": "industry",
                    "period": "today",
                    "limit": 100,
                },
                "source_policy": source_policy,
            },
            "research_content": {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [],
                "as_of": "2026-08-03",
                "window": {
                    "published_from": "2026-08-03",
                    "published_to": "2026-08-03",
                },
                "parameters": {
                    "material_types": ["market_flash"],
                    "query": [],
                    "limit": 100,
                    "verify_documents": False,
                },
                "source_policy": source_policy,
            },
        }
        expected_owners = {
            "market_signals": {"eastmoney_industry_rotation@1"},
            "capital_events": {"eastmoney_board_fund_flow@1"},
            "research_content": {"cls_market_flash@1"},
        }
        for task_type, request in cases.items():
            with self.subTest(task_type=task_type):
                completed = invoke_copied_installed(
                    request,
                    bootstrap=ISSUE_66_BOOTSTRAP,
                    environment_overrides={"A_SHARE_ISSUE66_FLASH": "primary"},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                result = json.loads(completed.stdout)
                self.assertEqual(result["plan"]["task_type"], task_type)
                self.assertEqual(set(result["task_views"]), {task_type})
                self.assertNotIn("workflow", result["capability_registry"])
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertNotIn("market_overview_research", serialized)
                self.assertNotIn("capability_inventory", serialized)
                executed_owners = {
                    operation_id
                    for capability in result["coverage"].values()
                    for operation_id, operation in capability["operations"].items()
                    if operation["execution_state"]
                    not in {"not_requested", "skipped_policy"}
                }
                self.assertEqual(executed_owners, expected_owners[task_type])

    def test_policy_blocked_core_rejects_coherent_owner_and_limitation_forgery(
        self,
    ) -> None:
        completed = invoke_copied_installed(market_overview_request())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)

        forged_owner = deepcopy(result)
        operation = forged_owner["coverage"]["steps"]["market_core_signals"]["leaf"][
            "market_signal_observations"
        ]["operations"]["eastmoney_industry_rotation@1"]
        operation["execution_state"] = "not_requested"
        operation["state"] = "not_requested"
        with self.assertRaisesRegex(
            ValueError, "workflow leaf coverage contradicts its request scope"
        ):
            _validate_serialized(_rehash_workflow(forged_owner))

        forged_limitation = deepcopy(result)
        limitation_id = next(
            item["id"]
            for item in forged_limitation["limitations"]
            if item["code"] == "source_policy_not_satisfied"
            and item["message"].startswith("market_signals ")
        )
        forged_limitation["limitations"] = [
            item
            for item in forged_limitation["limitations"]
            if item["id"] != limitation_id
        ]
        self._remove_reference(forged_limitation, limitation_id)
        with self.assertRaisesRegex(
            ValueError, "workflow leaf coverage contradicts its request scope"
        ):
            _validate_serialized(_rehash_workflow(forged_limitation))

    @classmethod
    def _remove_reference(cls, value: object, target: str) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                cls._remove_reference(nested, target)
        elif isinstance(value, list):
            value[:] = [item for item in value if item != target]
            for nested in value:
                cls._remove_reference(nested, target)


if __name__ == "__main__":
    unittest.main()
