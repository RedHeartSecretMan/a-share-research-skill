from __future__ import annotations

from copy import deepcopy
import unittest

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.operation_release_matrix import derive_release_source_result  # noqa: E402
from tests.release_probe_core_operations import sanitize_public_result  # noqa: E402
from tests.test_market_foundation import ReportTransport, turnover_report  # noqa: E402
from tests.test_market_foundation import margin_report  # noqa: E402
from a_share_research.market_foundation import (  # noqa: E402
    OperationRequest,
    build_default_market_foundation_operations,
)


def report_operation(value: str = "0") -> dict[str, object]:
    return {
        "capability_id": "whole_market_turnover",
        "operation_id": "sse_a_share_turnover@1",
        "execution_state": "succeeded",
        "state": "complete",
        "query_complete": True,
        "source_error_codes": [],
        "market_foundation_report_facts": {
            "exchange": "SSE",
            "asset_scope": "exchange_daily_A_share_stock_turnover",
            "report_date": "2026-09-30",
            "published_at": "2026-09-30T18:00:00+08:00",
            "row_count": 2,
            "metrics": {"turnover_amount_CNY": value},
            "units": {"turnover_amount_CNY": "CNY"},
        },
    }


class MarketFoundationReleaseTests(unittest.TestCase):
    def test_qualified_success_cannot_omit_the_report_proof(self):
        operation = report_operation()
        del operation["market_foundation_report_facts"]
        with self.assertRaises(ValueError):
            derive_release_source_result((operation,))

    def test_release_capture_derives_report_facts_from_owned_observation_references(
        self,
    ):
        result = self._public_report("0")
        safe, evidence = sanitize_public_result(
            request_profile="market-foundation-zero",
            request_as_of="2026-09-30",
            result=result,
            verified_at="2026-10-01T10:00:00+08:00",
        )
        self.assertEqual(evidence[0]["source_result"], "observed_empty")
        self.assertTrue(evidence[0]["attempted"])
        operation = safe["result"]["coverage"]["whole_market_turnover"]["operations"][
            "sse_a_share_turnover@1"
        ]
        self.assertEqual(operation["state"], "complete")
        self.assertEqual(
            operation["market_foundation_report_facts"]["metrics"],
            {"turnover_amount_CNY": "0"},
        )
        nonzero = self._public_report("10")
        nonzero["coverage"]["whole_market_turnover"]["operations"][
            "sse_a_share_turnover@1"
        ]["diagnostics"] = {
            "source_result": "observed_empty",
            "message": "secret=/Users/private",
        }
        safe, evidence = sanitize_public_result(
            request_profile="market-foundation-nonzero",
            request_as_of="2026-09-30",
            result=nonzero,
            verified_at="2026-10-01T10:00:00+08:00",
        )
        self.assertEqual(evidence[0]["source_result"], "complete")
        self.assertNotIn("secret", str(safe))

    def test_forged_contradictory_wrong_owner_and_missing_fields_are_rejected(self):
        cases = []
        for key, value in (
            ("source_result", "observed_empty"),
            ("row_count", 0),
            ("exchange", "BSE"),
            ("metrics", {}),
            ("units", {"turnover_amount_CNY": "shares"}),
        ):
            operation = report_operation()
            operation["market_foundation_report_facts"][key] = value
            cases.append(operation)
        wrong_owner = report_operation()
        wrong_owner["operation_id"] = "cls_market_flash@1"
        cases.append(wrong_owner)
        contradiction = report_operation()
        contradiction["execution_state"] = "failed"
        contradiction["query_complete"] = None
        cases.append(contradiction)
        for case in cases:
            with self.subTest(operation=case):
                with self.assertRaises(ValueError):
                    derive_release_source_result((case,))
        result = self._public_report("0")
        for mutation in ("missing", "foreign", "duplicate"):
            changed = deepcopy(result)
            if mutation == "missing":
                changed["observations"] = []
            elif mutation == "foreign":
                changed["observations"][0]["operation_id"] = "szse_a_share_turnover@1"
            else:
                changed["observations"].append(deepcopy(changed["observations"][0]))
            with self.subTest(reference=mutation), self.assertRaises(ValueError):
                sanitize_public_result(
                    request_profile="market-foundation-invalid",
                    request_as_of="2026-09-30",
                    result=changed,
                    verified_at="2026-10-01T10:00:00+08:00",
                )

    def test_legacy_operation_source_results_remain_unchanged(self):
        for state, execution, complete, expected in (
            ("complete", "succeeded", True, "complete"),
            ("observed_empty", "succeeded", True, "observed_empty"),
            ("partial", "succeeded", False, "partial"),
            ("failed", "failed", None, "unavailable"),
        ):
            operation = {
                "operation_id": "cls_market_flash@1",
                "capability_id": "research_content_materials",
                "execution_state": execution,
                "state": state,
                "query_complete": complete,
                "source_error_codes": [],
            }
            self.assertEqual(derive_release_source_result((operation,)), expected)

    def test_all_six_exact_owners_use_the_same_source_result_authority(self):
        from a_share_research.market_foundation import fold_market_foundation
        from a_share_research.operation_release_matrix import (
            market_foundation_report_facts,
        )

        transports = {}
        for exchange in ("SSE", "SZSE", "BSE"):
            fields = (
                {"TRADE_AMT_01": "0", "TRADE_AMT_03": "0"}
                if exchange == "SSE"
                else {
                    "A_SHARE_TURNOVER_HUNDRED_MILLION_CNY"
                    if exchange == "SZSE"
                    else "A_SHARE_TURNOVER_CNY": "0"
                }
            )
            transports[f"{exchange.lower()}_a_share_turnover@1"] = ReportTransport(
                turnover_report(exchange, fields)
            )
            report = margin_report(exchange)
            report["row_count"] = 0
            report["source_fields"] = {}
            report["source_units"] = {}
            transports[f"{exchange.lower()}_market_margin@1"] = ReportTransport(report)
        operations = build_default_market_foundation_operations(transports=transports)
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcomes = tuple(operation.execute(request, 1) for operation in operations)
        result = fold_market_foundation(outcomes, request, operations=operations)
        self.assertEqual(
            [item["source_result"] for item in result["operations"]],
            ["observed_empty"] * 6,
        )
        self.assertEqual(result["turnover"]["total"]["value"], "0")
        self.assertTrue(
            all(value is None for value in result["margin"]["totals"].values())
        )
        for outcome in outcomes:
            payload = outcome.to_dict()["observations"][0]
            operation = outcome.to_coverage_dict(
                observation_ids=(payload["id"],), source_error_ids=()
            )
            operation["source_error_codes"] = []
            operation["market_foundation_report_facts"] = (
                market_foundation_report_facts(operation, (payload,))
            )
            self.assertEqual(
                derive_release_source_result((operation,)), "observed_empty"
            )
            self.assertEqual(outcome.coverage, "complete")

    def _public_report(self, value: str):
        report = turnover_report("SSE", {"TRADE_AMT_01": value, "TRADE_AMT_03": "0"})
        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": ReportTransport(report)}
        )
        outcome = operations[0].execute(
            OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00"), 1
        )
        payload = outcome.to_dict()["observations"][0]
        coverage = outcome.to_coverage_dict(
            observation_ids=(payload["id"],), source_error_ids=()
        )
        return {
            "schema_version": "1.0",
            "research_anchor": {"as_of": "2026-09-30", "timezone": "Asia/Shanghai"},
            "plan": {
                "id": "market-foundation-qualification",
                "version": "1.0",
                "task_type": "market_foundation_qualification",
                "capabilities": ["whole_market_turnover"],
                "research_dependencies": [],
                "operation_obligations": [
                    {
                        "capability_id": "whole_market_turnover",
                        "operation_id": outcome.operation_id,
                        "producer_operation_id": outcome.operation_id,
                        "operation_kind": "external_source",
                        "source_role": "market_observation",
                        "experimental": False,
                        "exchange": "SSE",
                        "evidence_uses": [
                            {"use": "whole_market_turnover", "role": "supporting"}
                        ],
                    }
                ],
            },
            "coverage": {
                "whole_market_turnover": {
                    "operations": {outcome.operation_id: coverage}
                }
            },
            "observations": [
                {
                    "id": payload["id"],
                    "capability_id": "whole_market_turnover",
                    "operation_id": outcome.operation_id,
                    "payload": payload,
                }
            ],
            "source_errors": [],
        }

    def test_qualified_zero_report_maps_to_empty_without_changing_runtime_coverage(
        self,
    ):
        operation = report_operation()
        self.assertEqual(derive_release_source_result((operation,)), "observed_empty")
        self.assertEqual(operation["state"], "complete")
        self.assertEqual(
            derive_release_source_result((report_operation("10"),)), "complete"
        )


if __name__ == "__main__":
    unittest.main()
