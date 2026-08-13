from __future__ import annotations

import json
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.operation_release_matrix import (  # noqa: E402
    InstallationLoadEvidence,
    LiveProbeEvidence,
    OperationQualificationEvidence,
    ReleasePlanSnapshot,
    build_operation_release_matrix,
    formal_producer_operation_ids,
    load_operation_qualification_manifest,
)
from a_share_research.research_result import (  # noqa: E402
    EvidenceUsePolicy,
    OperationObligation,
)

from tests.test_direct_intraday_runtime import (  # noqa: E402
    intraday_request,
    invoke_copied_installed,
)
from tests.test_direct_market_valuation_runtime import (  # noqa: E402
    invoke_copied_installed as invoke_copied_market,
)
from tests.test_direct_research_content_runtime import (  # noqa: E402
    invoke_copied_installed as invoke_copied_content,
)
from tests.test_direct_research_workflow_runtime import (  # noqa: E402
    comparison_workflow_request,
)
from tests.test_research_workflows import fixture_runtime  # noqa: E402
from tests.test_unified_identity_runtime import (  # noqa: E402
    identity_request,
    invoke_installed_identity,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_PATH = (
    REPOSITORY_ROOT / "docs" / "research" / "core-v0.3.0-operation-qualification.json"
)


class OperationReleaseMatrixTests(unittest.TestCase):
    def test_issue_61_valuation_owners_have_installed_public_qualification(
        self,
    ) -> None:
        as_of = "2026-08-02"
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": as_of,
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        result = invoke_copied_market(
            request,
            valuation_fixtures=True,
            fixture_research_now="2026-08-02T18:30:00+08:00",
        )
        expected_owners = {
            "eastmoney_stock_info@1",
            "sina_financial_statements@1",
            "ths_consensus_eps@1",
        }

        self.assertEqual(result["status"], "limited")
        obligations = {
            item["producer_operation_id"]: item
            for item in result["plan"]["operation_obligations"]
            if item["producer_operation_id"] in expected_owners
        }
        self.assertEqual(set(obligations), expected_owners)
        self.assertTrue(all(item["experimental"] for item in obligations.values()))
        self.assertEqual(
            {
                owner: (
                    item["operation_kind"],
                    item["source_role"],
                    item["evidence_uses"],
                )
                for owner, item in obligations.items()
            },
            {
                "eastmoney_stock_info@1": (
                    "external_source",
                    "market_observation",
                    [{"use": "security_valuation", "role": "supporting"}],
                ),
                "sina_financial_statements@1": (
                    "external_source",
                    "market_observation",
                    [{"use": "security_valuation", "role": "supporting"}],
                ),
                "ths_consensus_eps@1": (
                    "external_source",
                    "signed_opinion",
                    [{"use": "security_valuation", "role": "supporting"}],
                ),
            },
        )
        operations = result["coverage"]["security_valuation_metrics"]["operations"]
        self.assertTrue(
            all(
                operations[owner]["execution_state"] == "succeeded"
                and operations[owner]["state"] == "complete"
                for owner in expected_owners
            )
        )
        owned = {
            owner: [
                item for item in result["observations"] if item["operation_id"] == owner
            ]
            for owner in expected_owners
        }
        self.assertTrue(all(owned.values()))
        for owner in expected_owners:
            self.assertEqual(
                {item["id"] for item in owned[owner]},
                set(operations[owner]["observation_ids"]),
            )

        stock = owned["eastmoney_stock_info@1"][0]["payload"]
        self.assertEqual(stock["subject"]["security"], "SSE:601138")
        self.assertEqual(stock["source_role"], "market_observation")
        self.assertEqual(stock["basis"], "current_stock_information_snapshot")
        self.assertEqual(
            stock["observed_value"]["unit"],
            {
                "effective_total_shares": "shares",
                "provider_market_cap": "CNY",
                "provider_price": "CNY/share",
            },
        )
        self.assertEqual(stock["evidence_time"], "2026-08-02T18:30:00+08:00")
        self.assertEqual(stock["available_at"], stock["evidence_time"])
        self.assertEqual(stock["retrieved_at"], stock["evidence_time"])
        market_cap = next(
            item
            for item in result["calculations"]
            if item["kind"] == "market_capitalization"
        )
        self.assertEqual(
            market_cap["payload"],
            {
                "formula": "completed_unadjusted_close * effective_total_shares",
                "operands": {"price": "56.700", "shares": "19844092284"},
            },
        )
        self.assertIn(owned["eastmoney_stock_info@1"][0]["id"], market_cap["input_ids"])

        statements = owned["sina_financial_statements@1"]
        self.assertEqual(
            {item["payload"]["basis"] for item in statements},
            {
                "reported_income_statement",
                "reported_balance_statement",
                "reported_cashflow_statement",
            },
        )
        for item in statements:
            payload = item["payload"]
            report = payload["report"]
            self.assertEqual(payload["subject"]["security"], "SSE:601138")
            self.assertEqual(payload["source_role"], "market_observation")
            self.assertLessEqual(report["period"], as_of)
            self.assertLessEqual(report["publication_date"], as_of)
            self.assertEqual(report["scope"], "consolidated")
            self.assertIn(report["audit_status"], {"audited", "unaudited"})
            self.assertTrue(report["data_source"])
            self.assertTrue(report["source_update_time"])
            self.assertEqual(report["version_identifier"], report["source_update_time"])
            self.assertEqual(
                report["version_relationship"],
                "unique_period_snapshot_from_source_response",
            )
            self.assertEqual(payload["observed_value"]["unit"], "CNY")
            self.assertEqual(payload["available_at"], report["publication_date"])
            self.assertEqual(payload["retrieved_at"], "2026-08-02T18:30:00+08:00")

        consensus = owned["ths_consensus_eps@1"][0]["payload"]
        self.assertEqual(consensus["subject"]["security"], "SSE:601138")
        self.assertEqual(consensus["source_role"], "signed_opinion")
        self.assertEqual(consensus["basis"], "source_aggregated_annual_consensus_eps")
        self.assertIsNone(consensus["available_at"])
        self.assertEqual(consensus["retrieved_at"], "2026-08-02T18:30:00+08:00")
        forecasts = consensus["observed_value"]["value"]
        self.assertEqual([item["year"] for item in forecasts], [2026, 2027, 2028])
        self.assertEqual([item["institutions"] for item in forecasts], [20, 20, 18])
        self.assertTrue(
            all(
                set(item) == {"year", "institutions", "minimum", "mean", "maximum"}
                for item in forecasts
            )
        )
        self.assertEqual(consensus["observed_value"]["unit"], "CNY/share")

        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "security_valuation_metrics"
        )
        self.assertEqual(
            claim["payload"]["subjects"][0]["issuer"],
            {
                "name": "富士康工业互联网股份有限公司",
                "identifier": None,
                "security_relationship": "verified",
            },
        )

        conflict = invoke_copied_market(
            request,
            valuation_fixtures=True,
            fixture_scenario="provider_mcap_conflict",
            fixture_research_now="2026-08-02T18:30:00+08:00",
        )
        conflict_stock = next(
            item
            for item in conflict["observations"]
            if item["operation_id"] == "eastmoney_stock_info@1"
        )
        conflict_calculation = next(
            item
            for item in conflict["calculations"]
            if item["kind"] == "market_capitalization"
        )
        self.assertEqual(
            conflict_stock["payload"]["observed_value"]["value"]["provider_market_cap"],
            "1",
        )
        self.assertEqual(conflict_calculation["payload"], market_cap["payload"])
        self.assertIn(
            "provider_market_cap_conflict",
            {item["code"] for item in conflict["conflicts"]},
        )

        for scenario, failed_owner, retained_owner, retained_calculations in (
            (
                "missing_forecast",
                "ths_consensus_eps@1",
                "sina_financial_statements@1",
                {"market_capitalization", "pe_ttm", "pb_mrq"},
            ),
            (
                "missing_financials",
                "sina_financial_statements@1",
                "ths_consensus_eps@1",
                {
                    "market_capitalization",
                    "forward_pe",
                    "forecast_eps_growth",
                    "peg",
                    "pe_digestion_years",
                },
            ),
        ):
            with self.subTest(scenario=scenario):
                partial = invoke_copied_market(
                    request,
                    valuation_fixtures=True,
                    fixture_scenario=scenario,
                    fixture_research_now="2026-08-02T18:30:00+08:00",
                )
                partial_operations = partial["coverage"]["security_valuation_metrics"][
                    "operations"
                ]
                self.assertEqual(partial_operations[failed_owner]["state"], "failed")
                self.assertEqual(
                    partial_operations[retained_owner]["state"], "complete"
                )
                self.assertIn(
                    retained_owner,
                    {item["operation_id"] for item in partial["observations"]},
                )
                self.assertEqual(
                    {item["kind"] for item in partial["calculations"]},
                    retained_calculations,
                )

        missing_shares = invoke_copied_market(
            request,
            valuation_fixtures=True,
            fixture_scenario="missing_shares",
            fixture_research_now="2026-08-02T18:30:00+08:00",
        )
        missing_share_operations = missing_shares["coverage"][
            "security_valuation_metrics"
        ]["operations"]
        self.assertEqual(missing_shares["status"], "limited")
        self.assertEqual(
            missing_share_operations["eastmoney_stock_info@1"]["state"], "failed"
        )
        self.assertEqual(
            missing_share_operations["sina_financial_statements@1"]["state"],
            "complete",
        )
        self.assertEqual(
            missing_share_operations["ths_consensus_eps@1"]["state"], "complete"
        )
        self.assertTrue(
            {
                "sina_financial_statements@1",
                "ths_consensus_eps@1",
            }.issubset(
                {item["operation_id"] for item in missing_shares["observations"]}
            )
        )
        self.assertEqual(
            {item["kind"] for item in missing_shares["calculations"]},
            {
                "forward_pe",
                "forecast_eps_growth",
                "peg",
                "pe_digestion_years",
            },
        )
        missing_share_claim = next(
            item
            for item in missing_shares["claims"]
            if item["kind"] == "security_valuation_metrics"
        )
        self.assertEqual(
            missing_share_claim["payload"]["valuation_basis"]["effective_total_shares"],
            {
                "status": "not_calculable",
                "reason": "effective_total_shares_unavailable",
            },
        )
        for metric in ("market_capitalization", "pe_ttm", "pb_mrq"):
            self.assertEqual(
                missing_share_claim["payload"]["metrics"][metric],
                {
                    "status": "not_calculable",
                    "reason": "effective_total_shares_unavailable",
                },
            )
        self.assertNotIn(
            "effective_share_start_time_unverified",
            {item["code"] for item in missing_shares["limitations"]},
        )
        self.assertEqual(
            {
                (item["operation_id"], item["code"])
                for item in missing_shares["source_errors"]
            },
            {("eastmoney_stock_info@1", "unknown_schema")},
        )
        retained_ids = {item["id"] for item in missing_shares["observations"]}
        self.assertTrue(
            all(
                calculation["input_ids"]
                and set(calculation["input_ids"]).issubset(retained_ids)
                for calculation in missing_shares["calculations"]
            )
        )
        self.assertFalse(
            any(
                item["operation_id"] == "eastmoney_stock_info@1"
                for item in missing_shares["observations"]
            )
        )

    def test_issue_61_formal_mootdx_f10_owner_has_installed_public_qualification(
        self,
    ) -> None:
        result = invoke_copied_content(
            {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["issuer_profile"],
                    "query": ["issue-61-formal-mootdx"],
                    "limit": 20,
                    "profile_categories": ["公司概况"],
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                },
            },
            fixture=True,
            formal_mootdx_f10=True,
        )
        owner = "mootdx_f10@1"

        self.assertEqual(result["status"], "limited")
        obligation = next(
            item
            for item in result["plan"]["operation_obligations"]
            if item["producer_operation_id"] == owner
        )
        self.assertEqual(obligation["operation_id"], owner)
        self.assertEqual(obligation["operation_kind"], "external_source")
        self.assertTrue(obligation["experimental"])
        self.assertEqual(obligation["source_role"], "declared_per_observation")
        self.assertEqual(
            obligation["evidence_uses"],
            [{"use": "research_content_material", "role": "supporting"}],
        )
        operation = result["coverage"]["research_content_materials"]["operations"][
            owner
        ]
        self.assertEqual(
            (operation["execution_state"], operation["state"]),
            ("succeeded", "complete"),
        )
        self.assertEqual(
            {
                item["field"]: (item["disposition"], item["destination"])
                for item in operation["field_dispositions"]
            },
            {
                "category": (
                    "normalized",
                    "observations[].payload.attributes.category",
                ),
                "content": ("normalized", "observations[].payload.content"),
            },
        )
        owned = [
            item for item in result["observations"] if item["operation_id"] == owner
        ]
        self.assertEqual(len(owned), 1)
        self.assertEqual(operation["observation_ids"], [owned[0]["id"]])
        payload = owned[0]["payload"]
        self.assertEqual(payload["source_operation"], owner)
        self.assertEqual(payload["source_role"], "market_observation")
        self.assertEqual(payload["material_type"], "issuer_profile")
        self.assertEqual(payload["published_at"], None)
        self.assertEqual(payload["available_at"], "2026-08-02T19:30:00+08:00")
        self.assertEqual(payload["retrieved_at"], "2026-08-02T19:30:00+08:00")
        self.assertEqual(
            payload["subject"]["security"],
            {"exchange": "SZSE", "code": "300058", "type": "A_SHARE"},
        )
        self.assertEqual(payload["attributes"]["category"], "公司概况")
        self.assertEqual(payload["content"], "蓝色光标公司概况公开聚合文本")
        self.assertEqual(
            set(payload["limitations"]),
            {
                "publication_time_unknown",
                "source_document_id_unknown",
                "f10_version_semantics_unverified",
            },
        )
        self.assertEqual(payload["license_boundary"]["raw_payload"], "excluded")
        self.assertEqual(
            payload["query_scope"],
            {
                "as_of": "2026-08-02",
                "published_from": "2026-07-01",
                "published_to": "2026-08-02",
                "material_types": ["issuer_profile"],
                "query": ["issue-61-formal-mootdx"],
                "limit": 20,
                "verify_documents": False,
            },
        )

    def test_issue_61_closes_exact_reviewed_domain_gaps(self) -> None:
        issue_61_owners = {
            "eastmoney_stock_info@1",
            "mootdx_f10@1",
            "sina_financial_statements@1",
            "ths_consensus_eps@1",
        }
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        operations = {
            item["producer_operation_id"]: item for item in payload["operations"]
        }

        self.assertEqual(len(operations), 44)
        self.assertEqual(issue_61_owners & set(operations), issue_61_owners)
        self.assertFalse(any(61 in item["gap_issues"] for item in operations.values()))
        self.assertEqual(
            {
                operation_id
                for operation_id, item in operations.items()
                if item["rate_limit_policy"]["qualification"] == "qualified_by_issue_61"
            },
            issue_61_owners,
        )
        expected_selectors = {
            "eastmoney_stock_info@1": (
                "tests/test_operation_release_matrix.py#"
                "test_issue_61_valuation_owners_have_installed_public_qualification"
            ),
            "sina_financial_statements@1": (
                "tests/test_operation_release_matrix.py#"
                "test_issue_61_valuation_owners_have_installed_public_qualification"
            ),
            "ths_consensus_eps@1": (
                "tests/test_operation_release_matrix.py#"
                "test_issue_61_valuation_owners_have_installed_public_qualification"
            ),
            "mootdx_f10@1": (
                "tests/test_operation_release_matrix.py#"
                "test_issue_61_formal_mootdx_f10_owner_has_installed_public_qualification"
            ),
        }
        self.assertEqual(
            {
                owner: {
                    "disposition": operations[owner]["disposition"],
                    "qualification": operations[owner]["rate_limit_policy"][
                        "qualification"
                    ],
                    "selector": operations[owner]["offline_contract_selector"],
                    "gap_issues": operations[owner]["gap_issues"],
                }
                for owner in issue_61_owners
            },
            {
                owner: {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_61",
                    "selector": expected_selectors[owner],
                    "gap_issues": [],
                }
                for owner in issue_61_owners
            },
        )

    def test_issue_62_closes_exact_reviewed_research_material_gaps(self) -> None:
        issue_62_owners = {
            "cls_market_flash@1",
            "cninfo_announcement@1",
            "cninfo_investor_qa@1",
            "eastmoney_market_flash@1",
            "eastmoney_reports@1",
            "eastmoney_stock_news@1",
            "iwencai_content_search@1",
            "sse_announcement@1",
            "szse_announcement@1",
            "ths_consensus_material@1",
        }
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        operations = {
            item["producer_operation_id"]: item for item in payload["operations"]
        }

        self.assertEqual(len(operations), 44)
        self.assertEqual(issue_62_owners & set(operations), issue_62_owners)
        self.assertFalse(any(62 in item["gap_issues"] for item in operations.values()))
        self.assertEqual(
            {
                operation_id
                for operation_id, item in operations.items()
                if item["rate_limit_policy"]["qualification"] == "qualified_by_issue_62"
            },
            issue_62_owners,
        )
        direct_selector = "tests/test_direct_research_content_runtime.py#"
        expected_selectors = {
            "eastmoney_stock_news@1": (
                direct_selector
                + "test_issue_62_copied_installed_bluefocus_qualifies_four_formal_owners"
            ),
            "cninfo_announcement@1": (
                direct_selector
                + "test_issue_62_copied_installed_bluefocus_qualifies_four_formal_owners"
            ),
            "szse_announcement@1": (
                direct_selector
                + "test_issue_62_copied_installed_bluefocus_qualifies_four_formal_owners"
            ),
            "sse_announcement@1": (
                direct_selector
                + "test_issue_62_copied_installed_sse_material_survives_cninfo_failure"
            ),
            "eastmoney_reports@1": (
                direct_selector
                + "test_issue_62_copied_installed_reports_qualify_three_formal_owners"
            ),
            "iwencai_content_search@1": (
                direct_selector
                + "test_issue_62_copied_installed_reports_qualify_three_formal_owners"
            ),
            "ths_consensus_material@1": (
                direct_selector
                + "test_issue_62_copied_installed_reports_qualify_three_formal_owners"
            ),
            "cninfo_investor_qa@1": (
                direct_selector
                + "test_issue_62_copied_installed_investor_qa_qualifies_formal_owner"
            ),
            "cls_market_flash@1": (
                direct_selector
                + "test_issue_62_copied_installed_market_flash_qualifies_controlled_fallback"
            ),
            "eastmoney_market_flash@1": (
                direct_selector
                + "test_issue_62_copied_installed_market_flash_qualifies_controlled_fallback"
            ),
        }
        self.assertEqual(
            {
                owner: {
                    "disposition": operations[owner]["disposition"],
                    "qualification": operations[owner]["rate_limit_policy"][
                        "qualification"
                    ],
                    "selector": operations[owner]["offline_contract_selector"],
                    "gap_issues": operations[owner]["gap_issues"],
                }
                for owner in issue_62_owners
            },
            {
                owner: {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_62",
                    "selector": expected_selectors[owner],
                    "gap_issues": [],
                }
                for owner in issue_62_owners
            },
        )

    def test_issue_63_closes_exact_reviewed_capital_event_gaps(self) -> None:
        issue_63_owners = {
            "eastmoney_board_fund_flow@1",
            "eastmoney_dividend@1",
            "eastmoney_margin_trading@1",
            "eastmoney_shareholder_count@1",
            "eastmoney_stock_fund_flow@1",
            "eastmoney_trading_events@1",
        }
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        ordered_owners = [
            item["producer_operation_id"] for item in payload["operations"]
        ]
        operations = {
            item["producer_operation_id"]: item for item in payload["operations"]
        }

        self.assertEqual(len(ordered_owners), 44)
        self.assertEqual(len(operations), 44)
        self.assertEqual(ordered_owners, sorted(ordered_owners))
        self.assertEqual(issue_63_owners & set(operations), issue_63_owners)
        self.assertFalse(any(63 in item["gap_issues"] for item in operations.values()))
        self.assertEqual(
            {
                operation_id
                for operation_id, item in operations.items()
                if item["rate_limit_policy"]["qualification"] == "qualified_by_issue_63"
            },
            issue_63_owners,
        )
        direct_selector = "tests/test_direct_capital_market_signals_runtime.py#"
        expected_selectors = {
            "eastmoney_stock_fund_flow@1": (
                direct_selector
                + "test_issue_63_stock_flow_public_contract_rejects_direction_forgery"
            ),
            "eastmoney_board_fund_flow@1": (
                direct_selector
                + "test_issue_63_board_flow_public_contract_rejects_market_scope_forgery"
            ),
            "eastmoney_margin_trading@1": (
                direct_selector
                + "test_issue_63_margin_public_contract_rejects_unit_forgery"
            ),
            "eastmoney_shareholder_count@1": (
                direct_selector
                + "test_issue_63_company_capital_public_contract_rejects_role_forgery"
            ),
            "eastmoney_dividend@1": (
                direct_selector
                + "test_issue_63_company_capital_public_contract_rejects_role_forgery"
            ),
            "eastmoney_trading_events@1": (
                direct_selector
                + "test_issue_63_trading_events_public_contract_preserves_partial_and_empty"
            ),
        }
        expected_lower_evidence = {
            "eastmoney_stock_fund_flow@1": (
                "tests/test_capital_flow_sources.py#StockFundFlowTests"
            ),
            "eastmoney_board_fund_flow@1": (
                "tests/test_capital_flow_sources.py#BoardFundFlowTests"
            ),
            "eastmoney_margin_trading@1": (
                "tests/test_company_capital_sources.py#CompanyCapitalSourceTests"
            ),
            "eastmoney_shareholder_count@1": (
                "tests/test_company_capital_sources.py#CompanyCapitalSourceTests"
            ),
            "eastmoney_dividend@1": (
                "tests/test_company_capital_sources.py#CompanyCapitalSourceTests"
            ),
            "eastmoney_trading_events@1": (
                "tests/test_trading_event_sources.py#EastmoneyTradingEventOperationTests"
            ),
        }
        for owner in issue_63_owners:
            with self.subTest(owner=owner):
                item = operations[owner]
                self.assertEqual(item["disposition"], "integrated")
                self.assertEqual(
                    item["rate_limit_policy"]["qualification"],
                    "qualified_by_issue_63",
                )
                self.assertEqual(
                    item["offline_contract_selector"], expected_selectors[owner]
                )
                self.assertEqual(item["gap_issues"], [])
                self.assertIn(
                    "skill/a-share-research/scripts/a_share_research/"
                    "capital_contract.py#CapitalObservationAuthority",
                    item["evidence_references"],
                )
                self.assertIn(expected_selectors[owner], item["evidence_references"])
                self.assertIn(
                    expected_lower_evidence[owner], item["evidence_references"]
                )
                self.assertIn(
                    "docs/specs/0007-implementation-amendment-a.md#A-08a",
                    item["evidence_references"],
                )

    def test_issue_59_closes_exact_reviewed_owner_projection_without_index_invention(
        self,
    ) -> None:
        issue_59_owners = {
            "sse_daily_line@1",
            "szse_daily_line@1",
            "tencent_daily_line@1",
            "eastmoney_forward_adjusted_daily_line@1",
            "tencent_forward_adjusted_daily_line@1",
            "sse_etf_list@1",
            "sse_etf_snapshot@1",
            "sina_etf_option_snapshot@1",
            "mootdx_intraday_replay@1",
            "exchange_intraday_replay_daily@1",
        }
        expected_issue_59_qualified = issue_59_owners - {"mootdx_intraday_replay@1"}
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        operations = {
            item["producer_operation_id"]: item for item in payload["operations"]
        }

        self.assertEqual(len(operations), 44)
        self.assertEqual(issue_59_owners & set(operations), issue_59_owners)
        self.assertFalse(
            {
                operation_id
                for operation_id in operations
                if "index" in operation_id.casefold()
            }
        )
        self.assertFalse(any(59 in item["gap_issues"] for item in operations.values()))
        self.assertEqual(
            {
                operation_id
                for operation_id, item in operations.items()
                if item["rate_limit_policy"]["qualification"] == "qualified_by_issue_59"
            },
            expected_issue_59_qualified,
        )
        self.assertEqual(
            {
                operation_id
                for operation_id in issue_59_owners
                if operations[operation_id]["rate_limit_policy"]["qualification"]
                == "qualified_by_issue_58"
            },
            {"mootdx_intraday_replay@1"},
        )
        self.assertTrue(
            all(
                operations[operation_id]["disposition"] == "integrated"
                and operations[operation_id]["gap_issues"] == []
                for operation_id in issue_59_owners
            )
        )
        sina = operations["sina_etf_option_snapshot@1"]
        self.assertEqual(
            sina["offline_contract_selector"],
            (
                "tests/test_direct_etf_options_runtime.py#"
                "test_copied_installed_success_retains_all_acquisition_facts_by_owner"
            ),
        )
        self.assertTrue(
            {
                "tests/test_etf_option_sources.py#test_collects_gbk_call_put_quotes_and_provider_analytics",
                "tests/test_etf_option_sources.py#SinaEtfOptionSnapshotTests",
                (
                    "tests/test_direct_etf_options_runtime.py#"
                    "test_copied_installed_success_retains_all_acquisition_facts_by_owner"
                ),
                "docs/specs/0007-implementation-amendment-a.md#A-08a",
            }.issubset(sina["evidence_references"])
        )
        mootdx = operations["mootdx_intraday_replay@1"]
        self.assertEqual(
            mootdx["offline_contract_selector"],
            (
                "tests/test_intraday_replay_mootdx.py#"
                "test_qualified_pages_normalize_units_and_keep_recent_calendar"
            ),
        )
        self.assertIn(
            (
                "tests/test_direct_intraday_runtime.py#"
                "test_copied_installed_replay_exercises_formal_mootdx_owner_contract"
            ),
            mootdx["evidence_references"],
        )

    def test_issue_59_daily_and_etf_owners_have_installed_public_qualification(
        self,
    ) -> None:
        as_of = "2026-08-02"
        policy = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        }
        unadjusted = invoke_copied_market(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": as_of,
                "window": {"trading_days": 10},
                "parameters": {"adjustment": "unadjusted"},
                "source_policy": policy,
            },
            market_fixtures=True,
            fixture_research_now="2026-08-02T10:30:00+08:00",
        )
        etf = invoke_copied_market(
            {
                "schema_version": "1.0",
                "task_type": "etf_market",
                "subjects": [{"clue": "510050"}],
                "as_of": as_of,
                "window": None,
                "parameters": {},
                "source_policy": policy,
            },
            market_fixtures=True,
            fixture_research_now="2026-08-02T10:30:00+08:00",
        )
        valuation = invoke_copied_market(
            {
                "schema_version": "1.0",
                "task_type": "security_valuation",
                "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
                "as_of": as_of,
                "window": None,
                "parameters": {"target_pe": "30"},
                "source_policy": {**policy, "allow_fallback": True},
            },
            valuation_fixtures=True,
            fixture_research_now="2026-08-02T18:30:00+08:00",
        )
        for result in (unadjusted, etf, valuation):
            self.assertEqual(result["status"], "limited")

        owner_proofs = {
            "sse_daily_line@1": (valuation, "valuation_close"),
            "szse_daily_line@1": (unadjusted, "market_trend_series"),
            "tencent_daily_line@1": (unadjusted, "market_trend_series"),
            "sse_etf_list@1": (etf, "etf_market_quote"),
            "sse_etf_snapshot@1": (etf, "etf_market_quote"),
        }
        expected_plan_roles = {
            "sse_daily_line@1": ("market_observation", "supporting"),
            "szse_daily_line@1": ("authoritative_disclosure", "supporting"),
            "tencent_daily_line@1": ("market_observation", "corroborating"),
            "sse_etf_list@1": ("authoritative_disclosure", "supporting"),
            "sse_etf_snapshot@1": ("market_observation", "supporting"),
        }
        expected_fields = {
            "sse_daily_line@1": {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            },
            "szse_daily_line@1": {f"row[{index}]" for index in range(9)},
            "tencent_daily_line@1": {
                *(f"day[{index}]" for index in range(7)),
                "qt[2]",
                "qt[3]",
                "qt[4]",
                "qt[6]",
                "qt[30]",
                "qt[31]",
                "qt[32]",
                "qt[33]",
                "qt[34]",
                "qt[39]",
                "qt[44]",
                "qt[45]",
                "qt[46]",
            },
            "sse_etf_list@1": {
                "fundCode",
                "fundAbbr",
                "secNameFull",
                "companyName",
                "listingDate",
                "fundType",
                "subClass",
            },
            "sse_etf_snapshot@1": {
                "name",
                "cpxxextendname",
                "open",
                "high",
                "low",
                "last",
                "prev_close",
                "chg_rate",
                "volume",
                "amount",
                "tradephase",
                "code",
                "date",
                "time",
            },
        }
        observations_by_owner = {}
        for owner, (result, capability) in owner_proofs.items():
            obligation = next(
                item
                for item in result["plan"]["operation_obligations"]
                if item["producer_operation_id"] == owner
                and item["capability_id"] == capability
            )
            self.assertEqual(obligation["operation_kind"], "external_source")
            self.assertEqual(
                (
                    obligation["source_role"],
                    obligation["evidence_uses"][0]["role"],
                ),
                expected_plan_roles[owner],
            )
            operation = result["coverage"][capability]["operations"][owner]
            self.assertEqual(
                (operation["execution_state"], operation["state"]),
                ("succeeded", "complete"),
            )
            self.assertEqual(
                {item["field"] for item in operation["field_dispositions"]},
                expected_fields[owner],
            )
            owned = [
                item for item in result["observations"] if item["operation_id"] == owner
            ]
            self.assertTrue(owned)
            self.assertEqual(
                {item["id"] for item in owned}, set(operation["observation_ids"])
            )
            observations_by_owner[owner] = owned

        for owner in (
            "sse_daily_line@1",
            "szse_daily_line@1",
            "tencent_daily_line@1",
        ):
            for observation in observations_by_owner[owner]:
                payload = observation["payload"]
                self.assertLessEqual(payload["observation"]["trading_date"], as_of)
                self.assertTrue(payload["retrieved_at"].endswith("+08:00"))
                if owner == "sse_daily_line@1":
                    self.assertEqual(payload["basis"], "unadjusted_close")
                    self.assertEqual(payload["observed_value"]["unit"], "CNY/share")
                else:
                    self.assertEqual(payload["basis"], "unadjusted_daily_ohlcv")
                    self.assertEqual(
                        payload["observed_value"]["unit"],
                        {"price": "CNY/share", "volume": "shares"},
                    )

        etf_identity = observations_by_owner["sse_etf_list@1"][0]["payload"]
        self.assertEqual(etf_identity["subject"]["security"], "SSE:510050")
        self.assertEqual(etf_identity["basis"], "current_sse_etf_list_membership")
        self.assertEqual(etf_identity["observed_value"]["unit"], "ETF identity")
        self.assertEqual(etf_identity["evidence_time"], "2005-02-23")
        etf_snapshot = observations_by_owner["sse_etf_snapshot@1"][0]["payload"]
        self.assertEqual(etf_snapshot["subject"]["security"], "SSE:510050")
        self.assertEqual(etf_snapshot["basis"], "sse_etf_market_snapshot")
        self.assertEqual(
            etf_snapshot["observed_value"]["unit"],
            {
                "price": "CNY/share",
                "change_rate": "percent",
                "volume": "shares",
                "amount": "CNY",
            },
        )
        self.assertLessEqual(etf_snapshot["observation"]["trading_date"], as_of)
        self.assertTrue(etf_snapshot["evidence_time"].endswith("+08:00"))

        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        qualified = {
            item["producer_operation_id"]: item
            for item in payload["operations"]
            if item["producer_operation_id"] in owner_proofs
        }
        self.assertEqual(
            {
                owner: {
                    "disposition": item["disposition"],
                    "qualification": item["rate_limit_policy"]["qualification"],
                    "selector": item["offline_contract_selector"],
                    "gap_issues": item["gap_issues"],
                }
                for owner, item in qualified.items()
            },
            {
                owner: {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_59",
                    "selector": (
                        "tests/test_operation_release_matrix.py#"
                        "test_issue_59_daily_and_etf_owners_have_installed_public_qualification"
                    ),
                    "gap_issues": [],
                }
                for owner in owner_proofs
            },
        )

    def test_issue_59_forward_adjusted_owners_have_installed_public_qualification(
        self,
    ) -> None:
        as_of = "2026-08-02"
        result = invoke_copied_market(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": as_of,
                "window": {"trading_days": 10},
                "parameters": {"adjustment": "forward_adjusted"},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": False,
                },
            },
            market_fixtures=True,
            fixture_research_now="2026-08-02T10:30:00+08:00",
        )
        expected_owners = {
            "eastmoney_forward_adjusted_daily_line@1",
            "tencent_forward_adjusted_daily_line@1",
        }

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {
                item["producer_operation_id"]
                for item in result["plan"]["operation_obligations"]
                if item["capability_id"] == "market_trend_series"
            },
            expected_owners,
        )
        operations = result["coverage"]["market_trend_series"]["operations"]
        self.assertEqual(set(operations), expected_owners)
        self.assertTrue(
            all(
                operation["execution_state"] == "succeeded"
                and operation["state"] == "complete"
                for operation in operations.values()
            )
        )
        source_observations = [
            item
            for item in result["observations"]
            if item["operation_id"] in expected_owners
        ]
        self.assertEqual(
            {item["operation_id"] for item in source_observations}, expected_owners
        )
        self.assertTrue(
            all(
                item["payload"]["observation"]["adjustment"] == "forward_adjusted"
                and item["payload"]["observation"]["trading_date"] <= as_of
                and item["payload"]["observed_value"]["unit"]
                == {"price": "CNY/share", "volume": "shares"}
                for item in source_observations
            )
        )

        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        qualified = {
            item["producer_operation_id"]: item
            for item in payload["operations"]
            if item["producer_operation_id"] in expected_owners
        }
        self.assertEqual(
            {
                owner: {
                    "disposition": item["disposition"],
                    "qualification": item["rate_limit_policy"]["qualification"],
                    "selector": item["offline_contract_selector"],
                    "gap_issues": item["gap_issues"],
                }
                for owner, item in qualified.items()
            },
            {
                owner: {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_59",
                    "selector": (
                        "tests/test_operation_release_matrix.py#"
                        "test_issue_59_forward_adjusted_owners_have_installed_public_qualification"
                    ),
                    "gap_issues": [],
                }
                for owner in expected_owners
            },
        )

    def test_issue_58_qualification_closes_only_its_reviewed_domain_gaps(
        self,
    ) -> None:
        identity = json.loads(
            invoke_installed_identity(identity_request(allow_experimental=False)).stdout
        )
        snapshot = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=False)
        )
        replay = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=False)
        )
        for result in (identity, snapshot, replay):
            self.assertEqual(result["status"], "blocked")
            external_obligations = [
                item
                for item in result["plan"]["operation_obligations"]
                if item["operation_kind"] == "external_source"
            ]
            self.assertTrue(external_obligations)
            self.assertTrue(all(item["experimental"] for item in external_obligations))
            self.assertTrue(
                all(
                    operation["execution_state"] == "skipped_policy"
                    for capability in result["coverage"].values()
                    for operation in capability["operations"].values()
                )
            )

        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        issue_58_owners = {
            "cninfo_security_dictionary@1",
            "sse_stock_list@1",
            "szse_stock_list@1",
            "tencent_intraday_snapshot@1",
            "tongdaxin_intraday_snapshot@1",
            "mootdx_intraday_replay@1",
        }
        integrated_issue_58_owners = issue_58_owners
        self.assertFalse(
            any(58 in item["gap_issues"] for item in payload["operations"])
        )
        self.assertEqual(
            {
                item["producer_operation_id"]
                for item in payload["operations"]
                if item["rate_limit_policy"].get("qualification")
                == "qualified_by_issue_58"
            },
            issue_58_owners,
        )
        self.assertEqual(
            {
                item["producer_operation_id"]
                for item in payload["operations"]
                if item["producer_operation_id"] in issue_58_owners
                and item["disposition"] == "integrated"
            },
            integrated_issue_58_owners,
        )
        entries = {
            item["producer_operation_id"]: item
            for item in payload["operations"]
            if item["producer_operation_id"] in issue_58_owners
        }
        self.assertEqual(
            {
                producer: {
                    "disposition": item["disposition"],
                    "qualification": item["rate_limit_policy"]["qualification"],
                    "selector": item["offline_contract_selector"],
                    "gap_issues": item["gap_issues"],
                }
                for producer, item in entries.items()
            },
            {
                "cninfo_security_dictionary@1": {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_58",
                    "selector": (
                        "tests/test_identity_sources.py#"
                        "test_cninfo_dictionary_normalizes_identity_observation"
                    ),
                    "gap_issues": [],
                },
                "mootdx_intraday_replay@1": {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_58",
                    "selector": (
                        "tests/test_intraday_replay_mootdx.py#"
                        "test_qualified_pages_normalize_units_and_keep_recent_calendar"
                    ),
                    "gap_issues": [],
                },
                "sse_stock_list@1": {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_58",
                    "selector": (
                        "tests/test_identity_sources.py#"
                        "test_sse_stock_list_normalizes_identity_observation"
                    ),
                    "gap_issues": [],
                },
                "szse_stock_list@1": {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_58",
                    "selector": (
                        "tests/test_identity_sources.py#"
                        "test_szse_stock_list_normalizes_identity_observation"
                    ),
                    "gap_issues": [],
                },
                "tencent_intraday_snapshot@1": {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_58",
                    "selector": (
                        "tests/test_direct_intraday_runtime.py#"
                        "test_copied_installed_snapshot_fixture_is_exact_formal_result"
                    ),
                    "gap_issues": [],
                },
                "tongdaxin_intraday_snapshot@1": {
                    "disposition": "integrated",
                    "qualification": "qualified_by_issue_58",
                    "selector": (
                        "tests/test_direct_intraday_runtime.py#"
                        "test_copied_installed_snapshot_fixture_is_exact_formal_result"
                    ),
                    "gap_issues": [],
                },
            },
        )

    def test_qualification_manifest_strictly_joins_plan_denominator(self) -> None:
        payload = {
            "schema_version": "1.0",
            "operations": [
                {
                    "producer_operation_id": "alpha_source@1",
                    "disposition": "experimental",
                    "temporal_semantics": {"basis": "observed_at"},
                    "unit_semantics": {"value": "source_declared_unit"},
                    "failure_semantics": "operation_local_failure",
                    "empty_result_semantics": "complete_query_zero_records",
                    "acquisition_role": "primary",
                    "credential_policy": "not_required",
                    "rate_limit_policy": {"scope": "owner_operation"},
                    "offline_contract_selector": "tests.test_identity_sources",
                    "offline_contract_result": "passed",
                    "evidence_references": [
                        "skill/a-share-research/scripts/a_share_research/identity_sources.py",
                        "docs/specs/0007-implementation-amendment-a.md#testing-seams",
                    ],
                    "gap_issues": [58],
                }
            ],
        }

        evidence = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=("alpha_source@1",),
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            evidence[0].evidence_references,
            (
                "skill/a-share-research/scripts/a_share_research/identity_sources.py",
                "docs/specs/0007-implementation-amendment-a.md#testing-seams",
            ),
        )
        for mutation in (
            {**payload, "unknown": True},
            {**payload, "operations": []},
            {
                **payload,
                "operations": [{**payload["operations"][0], "unknown": "field"}],
            },
        ):
            with self.assertRaises(ValueError):
                load_operation_qualification_manifest(
                    mutation,
                    expected_producer_operation_ids=("alpha_source@1",),
                )

    def test_workflow_public_leaf_plans_add_applications_without_new_denominator(
        self,
    ) -> None:
        public_result = (
            fixture_runtime()
            .research(comparison_workflow_request(["工业富联", "平安银行"]))
            .to_dict()
        )
        step_registry = public_result["capability_registry"]["steps"]["comparison"]
        obligations = step_registry["leaf_plan"]["operation_obligations"]
        producer_ids = sorted({item["producer_operation_id"] for item in obligations})
        snapshot = ReleasePlanSnapshot(
            result=public_result,
            request_profile="workflow-valuation-comparison",
        )
        qualifications = tuple(
            OperationQualificationEvidence(
                producer_operation_id=producer_id,
                disposition="experimental",
                temporal_semantics={"basis": "owner_contract"},
                unit_semantics={"basis": "owner_contract"},
                failure_semantics="operation_local_failure",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="workflow_leaf",
                credential_policy="none",
                rate_limit_policy={"scope": "provider", "bounded": True},
                offline_contract_selector="tests.test_research_workflows",
                offline_contract_result="passed",
                evidence_references=("tests.test_research_workflows",),
            )
            for producer_id in producer_ids
        )
        installations = tuple(
            InstallationLoadEvidence(
                producer_operation_id=producer_id,
                origin="copied_install_plan",
                status="complete",
                selector="workflow-valuation-comparison",
            )
            for producer_id in producer_ids
        )
        probes = tuple(
            LiveProbeEvidence(
                producer_operation_id=producer_id,
                probe="tests/release_probe_core_operations.py",
                probe_status="passed",
                attempted=True,
                source_result="complete",
                verified_at="2026-08-12T09:30:00+08:00",
            )
            for producer_id in producer_ids
        )

        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=qualifications,
            installation_loads=installations,
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=probes,
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], len(producer_ids))
        for operation in matrix["operations"]:
            self.assertEqual(len(operation["applications"]), 2)
            self.assertTrue(
                all(
                    application["workflow_use"]
                    == {
                        "plan_id": public_result["plan"]["id"],
                        "plan_version": "1.0",
                        "workflow_id": "valuation_comparison",
                        "step_id": "comparison",
                        "step_version": "1.0",
                    }
                    for application in operation["applications"]
                )
            )

    def _stable_matrix_inputs(self) -> dict[str, object]:
        producer_ids = ("alpha_source@1", "beta_source@1")
        snapshots: list[ReleasePlanSnapshot] = []
        for index, producer_id in enumerate(producer_ids):
            capability_id = f"capability-{index}"
            snapshots.append(
                ReleasePlanSnapshot(
                    result={
                        "schema_version": "1.0",
                        "plan": {
                            "id": f"plan-{index}",
                            "version": "1.0",
                            "task_type": f"task-{index}",
                            "capabilities": [capability_id],
                            "operation_obligations": [
                                {
                                    "capability_id": capability_id,
                                    "operation_id": producer_id,
                                    "producer_operation_id": producer_id,
                                    "operation_kind": "external_source",
                                    "source_role": "market_observation",
                                    "experimental": True,
                                    "exchange": None,
                                    "evidence_uses": [
                                        {"use": "test_use", "role": "supporting"}
                                    ],
                                }
                            ],
                            "research_dependencies": [],
                        },
                        "coverage": {
                            capability_id: {
                                "status": "observed_empty",
                                "operations": {
                                    producer_id: {
                                        "capability_id": capability_id,
                                        "operation_id": producer_id,
                                        "execution_state": "succeeded",
                                        "state": "observed_empty",
                                        "query_complete": True,
                                        "observation_ids": [],
                                        "source_error_ids": [],
                                        "field_dispositions": [
                                            {
                                                "field": f"field-{index}",
                                                "disposition": "normalized",
                                                "destination": (
                                                    "observations[].payload.value"
                                                ),
                                            }
                                        ],
                                        "diagnostics": {},
                                    }
                                },
                            }
                        },
                    },
                    request_profile=f"profile-{index}",
                )
            )
        qualifications = tuple(
            OperationQualificationEvidence(
                producer_operation_id=producer_id,
                disposition="experimental",
                temporal_semantics={"basis": "observed_at"},
                unit_semantics={"value": "source_declared_unit"},
                failure_semantics="failed_with_source_error",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="complementary",
                credential_policy="none",
                rate_limit_policy={"scope": "provider", "max_requests": 1},
                offline_contract_selector=f"tests.contracts.operation_{index}",
                offline_contract_result="passed",
                evidence_references=(f"tests.contracts.operation_{index}",),
            )
            for index, producer_id in enumerate(producer_ids)
        )
        installations = tuple(
            InstallationLoadEvidence(
                producer_operation_id=producer_id,
                origin="copied_install_plan",
                status="complete",
                selector=f"tests.install.operation_{index}",
            )
            for index, producer_id in enumerate(producer_ids)
        )
        probes = tuple(
            LiveProbeEvidence(
                producer_operation_id=producer_id,
                probe="tests/release_probe_core_operations.py",
                probe_status="passed",
                attempted=True,
                source_result="observed_empty",
                verified_at="2026-08-12T09:30:00+08:00",
            )
            for producer_id in producer_ids
        )
        return {
            "plan_snapshots": tuple(snapshots),
            "qualification_evidence": qualifications,
            "installation_loads": installations,
            "installation_health": {
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [
                    {
                        "distribution": "anyio",
                        "required_version": "4.14.2",
                        "installed_version": "4.14.2",
                        "status": "complete",
                        "modules": [],
                        "operation_ids": [],
                    }
                ],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            "live_probes": probes,
        }

    def test_matrix_bytes_are_order_independent_and_applications_are_deduplicated(
        self,
    ) -> None:
        inputs = self._stable_matrix_inputs()
        snapshots = inputs["plan_snapshots"]
        qualifications = inputs["qualification_evidence"]
        installations = inputs["installation_loads"]
        probes = inputs["live_probes"]
        assert isinstance(snapshots, tuple)
        assert isinstance(qualifications, tuple)
        assert isinstance(installations, tuple)
        assert isinstance(probes, tuple)

        forward = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (snapshots[0], snapshots[0], snapshots[1]),
            }
        )
        reversed_inputs = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (snapshots[1], snapshots[0], snapshots[0]),
                "qualification_evidence": tuple(reversed(qualifications)),
                "installation_loads": tuple(reversed(installations)),
                "live_probes": tuple(reversed(probes)),
            }
        )

        self.assertEqual(forward.to_json_bytes(), reversed_inputs.to_json_bytes())
        matrix = forward.to_dict()
        alpha = matrix["operations"][0]
        self.assertEqual(len(alpha["applications"]), 1)
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"],
            {"numerator": 2, "denominator": 2, "rate": "1.000000"},
        )

    def test_matrix_summarizes_same_candidate_attempt_provenance(self) -> None:
        inputs = self._stable_matrix_inputs()
        candidate_sha = "a" * 40
        digests = ("b" * 64, "c" * 64)
        probes = tuple(
            replace(
                probe,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                candidate_sha=candidate_sha,
                request_profile=f"profile-{index}",
                attempt_digest=digests[index],
            )
            for index, probe in enumerate(inputs["live_probes"])
        )
        snapshots = tuple(
            replace(
                snapshot,
                candidate_sha=candidate_sha,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                attempt_digest=digests[index],
            )
            for index, snapshot in enumerate(inputs["plan_snapshots"])
        )

        matrix = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": snapshots,
                "live_probes": probes,
            }
        ).to_dict()

        self.assertEqual(
            matrix["release_evidence"],
            {
                "candidate_sha": candidate_sha,
                "attempt_digests": list(digests),
                "verified_from": "2026-08-12T09:30:00+08:00",
                "verified_through": "2026-08-13T09:30:00+08:00",
            },
        )

    def test_matrix_keeps_an_attempt_that_contributes_only_skipped_applications(
        self,
    ) -> None:
        inputs = self._stable_matrix_inputs()
        candidate_sha = "a" * 40
        digests = ("b" * 64, "c" * 64, "d" * 64)
        snapshots = tuple(
            replace(
                snapshot,
                candidate_sha=candidate_sha,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                attempt_digest=digests[index],
            )
            for index, snapshot in enumerate(inputs["plan_snapshots"])
        )
        skipped_result = deepcopy(snapshots[0].result)
        skipped_operation = skipped_result["coverage"]["capability-0"]["operations"][
            "alpha_source@1"
        ]
        skipped_operation["execution_state"] = "not_requested"
        skipped_operation["state"] = "not_requested"
        skipped_operation["query_complete"] = None
        skipped = ReleasePlanSnapshot(
            result=skipped_result,
            request_profile="profile-skipped",
            candidate_sha=candidate_sha,
            verified_at="2026-08-14T09:30:00+08:00",
            attempt_digest=digests[2],
        )
        probes = tuple(
            replace(
                probe,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                candidate_sha=candidate_sha,
                request_profile=f"profile-{index}",
                attempt_digest=digests[index],
            )
            for index, probe in enumerate(inputs["live_probes"])
        )

        matrix = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (*snapshots, skipped),
                "live_probes": probes,
            }
        ).to_dict()

        self.assertEqual(matrix["release_evidence"]["attempt_digests"], list(digests))

    def test_live_attempt_truth_must_match_plan_application_execution(self) -> None:
        inputs = self._stable_matrix_inputs()
        snapshots = deepcopy(inputs["plan_snapshots"])
        first = snapshots[0].result
        capability = first["coverage"]["capability-0"]
        operation = capability["operations"]["alpha_source@1"]
        operation["execution_state"] = "not_requested"
        operation["state"] = "not_requested"
        operation["query_complete"] = None

        with self.assertRaisesRegex(ValueError, "attempt.*execution"):
            build_operation_release_matrix(**{**inputs, "plan_snapshots": snapshots})

    def test_failed_application_destination_must_use_the_public_contract(self) -> None:
        inputs = self._stable_matrix_inputs()
        snapshots = deepcopy(inputs["plan_snapshots"])
        first = snapshots[0].result
        operation = first["coverage"]["capability-0"]["operations"]["alpha_source@1"]
        operation["execution_state"] = "failed"
        operation["state"] = "failed"
        operation["query_complete"] = None
        operation["field_dispositions"][0]["destination"] = (
            "totally.invalid.destination"
        )
        probes = tuple(inputs["live_probes"])
        probes = (replace(probes[0], source_result="unavailable"), probes[1])

        with self.assertRaisesRegex(ValueError, "destination is unsupported"):
            build_operation_release_matrix(
                **{
                    **inputs,
                    "plan_snapshots": snapshots,
                    "live_probes": probes,
                }
            )

    def test_release_metadata_rejects_secret_or_server_shaped_values(self) -> None:
        base = self._stable_matrix_inputs()
        snapshots = base["plan_snapshots"]
        qualifications = base["qualification_evidence"]
        installations = base["installation_loads"]
        probes = base["live_probes"]
        assert isinstance(snapshots, tuple)
        assert isinstance(qualifications, tuple)
        assert isinstance(installations, tuple)
        assert isinstance(probes, tuple)
        cases = {
            "request URL": {
                "plan_snapshots": (
                    replace(snapshots[0], request_profile="https://10.0.0.1/private"),
                    snapshots[1],
                )
            },
            "offline absolute path": {
                "qualification_evidence": (
                    replace(
                        qualifications[0],
                        offline_contract_selector="/Users/private/test.py",
                    ),
                    qualifications[1],
                )
            },
            "rate credential key": {
                "qualification_evidence": (
                    replace(
                        qualifications[0],
                        rate_limit_policy={"access_token": "secret-value"},
                    ),
                    qualifications[1],
                )
            },
            "install absolute path": {
                "installation_loads": (
                    replace(installations[0], selector="/private/tmp/private-install"),
                    installations[1],
                )
            },
            "probe server URL": {
                "live_probes": (
                    replace(probes[0], probe="https://provider.example/private-probe"),
                    probes[1],
                )
            },
        }
        for name, mutation in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "unsafe release metadata"),
            ):
                build_operation_release_matrix(**{**base, **mutation})

    def test_operation_evidence_is_joined_per_producer_and_rates_are_counted(
        self,
    ) -> None:
        capability_id = "research_content_materials"
        producer_ids = ("public_material@1", "credentialed_material@1")
        obligations = [
            {
                "capability_id": capability_id,
                "operation_id": producer_id,
                "producer_operation_id": producer_id,
                "operation_kind": "external_source",
                "source_role": "declared_per_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [
                    {"use": "research_content_material", "role": "supporting"}
                ],
            }
            for producer_id in producer_ids
        ]
        operations = {
            producer_id: {
                "capability_id": capability_id,
                "operation_id": producer_id,
                "execution_state": "succeeded",
                "state": "observed_empty",
                "query_complete": True,
                "observation_ids": [],
                "source_error_ids": [],
                "field_dispositions": [
                    {
                        "field": f"{producer_id}.field",
                        "disposition": "normalized",
                        "destination": "observations[].payload.content",
                    }
                ],
                "diagnostics": {},
            }
            for producer_id in producer_ids
        }
        operations[producer_ids[1]]["execution_state"] = "failed"
        operations[producer_ids[1]]["state"] = "failed"
        operations[producer_ids[1]]["query_complete"] = None
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "plan": {
                    "id": "plan-two-content-producers",
                    "version": "1.0",
                    "task_type": "research_content",
                    "capabilities": [capability_id],
                    "operation_obligations": obligations,
                    "research_dependencies": [],
                },
                "coverage": {
                    capability_id: {
                        "status": "observed_empty",
                        "operations": operations,
                    }
                },
            },
            request_profile="two-content-producers",
        )
        qualification = (
            OperationQualificationEvidence(
                producer_operation_id=producer_ids[0],
                disposition="experimental",
                temporal_semantics={"basis": "published_at", "timezone": "source"},
                unit_semantics={"content": "unicode_text"},
                failure_semantics="failed_with_source_error",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="complementary",
                credential_policy="none",
                rate_limit_policy={"scope": "provider", "max_requests": 1},
                offline_contract_selector="tests.contracts.public_material",
                offline_contract_result="passed",
                evidence_references=("tests.contracts.public_material",),
                gap_issues=(62,),
            ),
            OperationQualificationEvidence(
                producer_operation_id=producer_ids[1],
                disposition="experimental",
                temporal_semantics={
                    "basis": "available_at",
                    "timezone": "Asia/Shanghai",
                },
                unit_semantics={"content": "licensed_document_text"},
                failure_semantics="credential_failure_is_operation_local",
                empty_result_semantics="complete_search_zero_documents",
                acquisition_role="fallback",
                credential_policy="local_reference_required",
                rate_limit_policy={"scope": "account", "max_requests": 2},
                offline_contract_selector="tests.contracts.credentialed_material",
                offline_contract_result="passed",
                evidence_references=("tests.contracts.credentialed_material",),
                gap_issues=(),
            ),
        )
        install_loads = tuple(
            InstallationLoadEvidence(
                producer_operation_id=producer_id,
                origin="copied_install_plan",
                status="complete",
                selector="tests.test_skill_distribution",
            )
            for producer_id in producer_ids
        )
        live_probes = tuple(
            LiveProbeEvidence(
                producer_operation_id=producer_id,
                probe="tests/release_probe_core_operations.py",
                probe_status="passed",
                attempted=True,
                source_result=(
                    "observed_empty" if index == 0 else "credential_blocked"
                ),
                verified_at="2026-08-12T09:30:00+08:00",
            )
            for index, producer_id in enumerate(producer_ids)
        )

        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=qualification,
            installation_loads=install_loads,
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=live_probes,
        ).to_dict()

        by_id = {item["producer_operation_id"]: item for item in matrix["operations"]}
        self.assertEqual(by_id[producer_ids[0]]["credential_policy"], "none")
        self.assertEqual(
            by_id[producer_ids[1]]["credential_policy"],
            "local_reference_required",
        )
        self.assertNotEqual(
            by_id[producer_ids[0]]["temporal_semantics"],
            by_id[producer_ids[1]]["temporal_semantics"],
        )
        self.assertNotEqual(
            by_id[producer_ids[0]]["unit_semantics"],
            by_id[producer_ids[1]]["unit_semantics"],
        )
        self.assertEqual(
            matrix["summary"],
            {
                "formal_operation_disposition_rate": {
                    "numerator": 2,
                    "denominator": 2,
                    "rate": "1.000000",
                },
                "active_request_field_destination_rate": {
                    "numerator": 2,
                    "denominator": 2,
                    "rate": "1.000000",
                },
                "default_install_load_rate": {
                    "numerator": 2,
                    "denominator": 2,
                    "rate": "1.000000",
                },
            },
        )

    def test_ordinary_plan_obligation_exposes_its_producer_identity(self) -> None:
        obligation = OperationObligation(
            capability_id="research_content",
            operation_id="mootdx_f10@1",
            source_role="declared_per_observation",
            experimental=True,
            exchange=None,
            evidence_uses=(
                EvidenceUsePolicy("research_content_material", "supporting"),
            ),
        )

        self.assertEqual(
            obligation.to_dict(),
            {
                "capability_id": "research_content",
                "operation_id": "mootdx_f10@1",
                "producer_operation_id": "mootdx_f10@1",
                "operation_kind": "external_source",
                "source_role": "declared_per_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [
                    {"use": "research_content_material", "role": "supporting"}
                ],
            },
        )

    def test_deterministic_coordinator_is_not_a_formal_source_denominator(self) -> None:
        from a_share_research.market_signals import COORDINATOR_OPERATION_ID

        inputs = self._stable_matrix_inputs()
        snapshots = inputs["plan_snapshots"]
        assert isinstance(snapshots, tuple)
        result = deepcopy(snapshots[0].result)
        result["plan"]["task_type"] = "market_signals"
        coordinator_id = COORDINATOR_OPERATION_ID
        result["plan"]["operation_obligations"].append(
            {
                "capability_id": "capability-0",
                "operation_id": coordinator_id,
                "producer_operation_id": coordinator_id,
                "operation_kind": "deterministic_coordinator",
                "source_role": "market_signal",
                "experimental": False,
                "exchange": None,
                "evidence_uses": [{"use": "test_use", "role": "supporting"}],
            }
        )
        result["coverage"]["capability-0"]["operations"][coordinator_id] = {
            "capability_id": "capability-0",
            "operation_id": coordinator_id,
            "execution_state": "succeeded",
            "state": "observed_empty",
            "query_complete": True,
            "field_dispositions": [
                {
                    "field": "derived_value",
                    "disposition": "normalized",
                    "destination": "observations[].payload.derived_value",
                }
            ],
        }

        matrix = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (
                    replace(snapshots[0], result=result),
                    snapshots[1],
                ),
            }
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 2)
        self.assertNotIn(
            coordinator_id,
            {item["producer_operation_id"] for item in matrix["operations"]},
        )

    def test_member_applications_share_one_formal_producer_denominator(self) -> None:
        producer_operation_id = "sina_financial_statements@1"
        capability_id = "valuation_compare_metrics"
        application_ids = (
            f"member-0::{producer_operation_id}",
            f"member-1::{producer_operation_id}",
        )
        obligations = [
            {
                "capability_id": capability_id,
                "operation_id": application_id,
                "producer_operation_id": producer_operation_id,
                "operation_kind": "external_source",
                "source_role": "market_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [{"use": "valuation_compare", "role": "supporting"}],
            }
            for application_id in application_ids
        ]
        operations = {
            application_id: {
                "capability_id": capability_id,
                "operation_id": application_id,
                "execution_state": "succeeded",
                "state": "observed_empty",
                "query_complete": True,
                "observation_ids": [],
                "source_error_ids": [],
                "field_dispositions": [
                    {
                        "field": "report_date",
                        "disposition": "normalized",
                        "destination": "observations[].payload.report.period_end",
                    }
                ],
                "diagnostics": {},
            }
            for application_id in application_ids
        }
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "plan": {
                    "id": "plan-comparison",
                    "version": "1.0",
                    "task_type": "valuation_compare",
                    "capabilities": [capability_id],
                    "operation_obligations": obligations,
                    "research_dependencies": [],
                },
                "coverage": {
                    capability_id: {
                        "status": "observed_empty",
                        "operations": operations,
                    }
                },
            },
            request_profile="two-member-comparison",
        )
        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=(
                OperationQualificationEvidence(
                    producer_operation_id=producer_operation_id,
                    disposition="experimental",
                    temporal_semantics={"basis": "report_period_end"},
                    unit_semantics={"financial_values": "reported_currency"},
                    failure_semantics="member_local_failure",
                    empty_result_semantics="complete_query_zero_reports",
                    acquisition_role="supporting",
                    credential_policy="none",
                    rate_limit_policy={"scope": "provider", "max_requests": 2},
                    offline_contract_selector=(
                        "tests.test_direct_market_valuation_runtime"
                    ),
                    offline_contract_result="passed",
                    evidence_references=("tests.test_direct_market_valuation_runtime",),
                    gap_issues=(61,),
                ),
            ),
            installation_loads=(
                InstallationLoadEvidence(
                    producer_operation_id=producer_operation_id,
                    origin="copied_install_plan",
                    status="complete",
                    selector="tests.test_skill_distribution",
                ),
            ),
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=(
                LiveProbeEvidence(
                    producer_operation_id=producer_operation_id,
                    probe="tests/release_probe_core_operations.py",
                    probe_status="passed",
                    attempted=True,
                    source_result="observed_empty",
                    verified_at="2026-08-12T09:30:00+08:00",
                ),
            ),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 1)
        operation = matrix["operations"][0]
        self.assertEqual(operation["producer_operation_id"], producer_operation_id)
        self.assertEqual(
            [item["operation_id"] for item in operation["applications"]],
            list(application_ids),
        )
        forged = deepcopy(snapshot.result)
        forged["plan"]["operation_obligations"][0]["producer_operation_id"] = (
            "tencent_daily_line@1"
        )
        with self.assertRaisesRegex(ValueError, "producer operation"):
            formal_producer_operation_ids(
                (
                    ReleasePlanSnapshot(
                        result=forged,
                        request_profile="forged-member-comparison",
                    ),
                )
            )

    def test_one_producer_preserves_task_local_field_contracts_per_application(
        self,
    ) -> None:
        base = self._stable_matrix_inputs()
        snapshots = base["plan_snapshots"]
        assert isinstance(snapshots, tuple)
        producer_id = "shared_source@1"
        first = deepcopy(snapshots[0].result)
        second = deepcopy(snapshots[0].result)
        for result, profile, destination in (
            (first, "task-a", "observations[].payload.subject.security"),
            (second, "task-b", "observations[].payload.subject.security.code"),
        ):
            obligation = result["plan"]["operation_obligations"][0]
            old_id = obligation["operation_id"]
            obligation["operation_id"] = producer_id
            obligation["producer_operation_id"] = producer_id
            result["plan"]["id"] = f"plan-{profile}"
            result["plan"]["task_type"] = profile
            operation = result["coverage"]["capability-0"]["operations"].pop(old_id)
            operation["operation_id"] = producer_id
            operation["field_dispositions"][0]["destination"] = destination
            result["coverage"]["capability-0"]["operations"][producer_id] = operation
        qualification = replace(
            base["qualification_evidence"][0],
            producer_operation_id=producer_id,
        )
        installation = replace(
            base["installation_loads"][0], producer_operation_id=producer_id
        )
        live = replace(base["live_probes"][0], producer_operation_id=producer_id)

        matrix = build_operation_release_matrix(
            plan_snapshots=(
                ReleasePlanSnapshot(result=first, request_profile="task-a"),
                ReleasePlanSnapshot(result=second, request_profile="task-b"),
            ),
            qualification_evidence=(qualification,),
            installation_loads=(installation,),
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=(live,),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 1)
        operation = matrix["operations"][0]
        self.assertEqual(len(operation["applications"]), 2)
        self.assertEqual(
            {
                application["request_profile"]: application["field_dispositions"]
                for application in operation["applications"]
            },
            {
                "task-a": [
                    {
                        "field": "field-0",
                        "disposition": "normalized",
                        "destination": "observations[].payload.subject.security",
                    }
                ],
                "task-b": [
                    {
                        "field": "field-0",
                        "disposition": "normalized",
                        "destination": "observations[].payload.subject.security.code",
                    }
                ],
            },
        )
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"],
            {"numerator": 2, "denominator": 2, "rate": "1.000000"},
        )

    def test_one_plan_owned_operation_requires_all_three_release_evidence_kinds(
        self,
    ) -> None:
        owner = ("research_content", "mootdx_f10@1")
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "task_type": "research_content",
                "research_anchor": {
                    "as_of": "2026-08-12",
                    "timezone": "Asia/Shanghai",
                },
                "plan": {
                    "id": "plan-content-f10",
                    "version": "1.0",
                    "task_type": "research_content",
                    "capabilities": [owner[0]],
                    "operation_obligations": [
                        {
                            "capability_id": owner[0],
                            "operation_id": owner[1],
                            "producer_operation_id": owner[1],
                            "operation_kind": "external_source",
                            "source_role": "declared_per_observation",
                            "experimental": True,
                            "exchange": None,
                            "evidence_uses": [
                                {
                                    "use": "research_content_material",
                                    "role": "supporting",
                                }
                            ],
                        }
                    ],
                    "research_dependencies": [
                        {
                            "consumer_id": "research_content_material_group",
                            "evidence_use": "research_content_material",
                            "minimum_supporting_observations": 1,
                            "core": True,
                        }
                    ],
                },
                "coverage": {
                    owner[0]: {
                        "status": "complete",
                        "operations": {
                            owner[1]: {
                                "capability_id": owner[0],
                                "operation_id": owner[1],
                                "execution_state": "succeeded",
                                "state": "observed_empty",
                                "query_complete": True,
                                "observation_ids": [],
                                "source_error_ids": [],
                                "field_dispositions": [
                                    {
                                        "field": "content_material",
                                        "disposition": "retained",
                                        "destination": "observations[].payload",
                                    }
                                ],
                                "diagnostics": {},
                            }
                        },
                    }
                },
            },
            request_profile="content-f10-fallback",
        )
        qualification = OperationQualificationEvidence(
            producer_operation_id=owner[1],
            disposition="experimental",
            temporal_semantics={"basis": "available_at"},
            unit_semantics={"content_material": "normalized_material"},
            failure_semantics="failed_with_source_error",
            empty_result_semantics="complete_query_zero_records",
            acquisition_role="fallback",
            credential_policy="none",
            rate_limit_policy={"scope": "provider", "max_requests": 1},
            offline_contract_selector=(
                "tests.test_operation_release_matrix."
                "OperationReleaseMatrixTests."
                "test_one_plan_owned_operation_requires_all_three_release_evidence_kinds"
            ),
            offline_contract_result="passed",
            evidence_references=("tests.test_operation_release_matrix",),
            gap_issues=(61,),
        )
        live = LiveProbeEvidence(
            producer_operation_id=owner[1],
            probe="tests/release_probe_core_operations.py",
            probe_status="passed",
            attempted=True,
            source_result="observed_empty",
            verified_at="2026-08-12T09:30:00+08:00",
        )
        installation_health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [
                {
                    "distribution": "mootdx",
                    "required_version": "0.11.7",
                    "installed_version": "0.11.7",
                    "status": "complete",
                    "modules": ["mootdx.quotes"],
                    "operation_ids": [owner[1]],
                }
            ],
            "operations": [{"operation_id": owner[1], "status": "complete"}],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }

        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=(qualification,),
            installation_loads=(
                InstallationLoadEvidence(
                    producer_operation_id=owner[1],
                    origin="installation_health",
                    status="complete",
                    selector="tests.test_installation_health",
                ),
            ),
            installation_health=installation_health,
            live_probes=(live,),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 1)
        self.assertEqual(
            matrix["denominator"]["operation_owner_sha256"],
            "341719cc3d5706dd177a5a62692ebb7cbed03fcd89845eedfca9ceedcf982161",
        )
        self.assertEqual(
            matrix["summary"]["formal_operation_disposition_rate"]["rate"],
            "1.000000",
        )
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"]["rate"],
            "1.000000",
        )
        self.assertEqual(
            matrix["summary"]["default_install_load_rate"]["rate"],
            "1.000000",
        )
        operation = matrix["operations"][0]
        self.assertEqual(operation["producer_operation_id"], owner[1])
        self.assertEqual(
            operation["applicability"],
            {
                "capability_ids": [owner[0]],
                "exchanges": [],
                "plan_ids": ["plan-content-f10"],
                "plan_versions": ["1.0"],
                "request_profiles": ["content-f10-fallback"],
                "task_types": ["research_content"],
                "workflow_ids": [],
            },
        )
        self.assertEqual(operation["verified_at"], live.verified_at)
        self.assertEqual(operation["offline_contract"]["result"], "passed")
        self.assertEqual(operation["live_probe"]["probe_status"], "passed")
        self.assertEqual(operation["install_load"]["status"], "complete")
        self.assertEqual(
            operation["applications"][0]["field_dispositions"],
            [
                {
                    "field": "content_material",
                    "disposition": "retained",
                    "destination": "observations[].payload",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
