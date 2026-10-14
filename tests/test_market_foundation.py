from __future__ import annotations

import unittest
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from decimal import localcontext
from pathlib import Path

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.market_foundation import (  # noqa: E402
    OperationRequest,
    build_default_market_foundation_operations,
    fold_market_foundation,
)
from a_share_research.provider_lane import (  # noqa: E402
    ProductionInvocationAdapter,
)


class ReportTransport:
    """Inject an acquired report stage, not a fabricated provider wire body."""

    def __init__(self, report: dict[str, object]) -> None:
        self.report = report
        self.calls = 0

    def acquire(self, selector, request, timeout, invocation):
        def fetch():
            self.calls += 1
            return self.report

        return invocation.call(ProductionInvocationAdapter(fetch))


def turnover_report(exchange: str, fields: dict[str, str]) -> dict[str, object]:
    unit = "hundred_million_CNY" if exchange == "SSE" else "ten_thousand_CNY"
    return {
        "exchange": exchange,
        "asset_scope": "exchange_daily_A_share_stock_turnover",
        "report_date": "2026-09-30",
        "published_at": "2026-09-30T18:00:00+08:00",
        "retrieved_at": "2026-09-30T19:00:00+08:00",
        "source_fields": fields,
        "source_units": {field: unit for field in fields},
        "query_complete": True,
        "row_count": 2 if exchange == "SSE" else 1,
    }


def margin_report(exchange: str) -> dict[str, object]:
    quantities = {"SECURITIES_LENDING_SELL_QUANTITY", "SECURITIES_LENDING_QUANTITY"}
    fields = {
        "FINANCING_BALANCE": "1205000",
        "FINANCING_BUY_AMOUNT": "102500",
        "SECURITIES_LENDING_SELL_QUANTITY": "20000",
        "SECURITIES_LENDING_QUANTITY": "80000",
        "SECURITIES_LENDING_BALANCE": "307500",
        "MARGIN_TOTAL_BALANCE": "1512500",
    }
    if exchange == "BSE":
        fields = dict(
            zip(fields, ("120.50", "10.25", "2.00", "8.00", "30.75", "151.25"))
        )
    scope = {
        "SSE": "exchange_wide_official_margin_trading_summary",
        "SZSE": "exchange_wide_official_margin_trading_total",
        "BSE": "exchange_wide_official_margin_trading_aggregate",
    }
    return {
        "exchange": exchange,
        "asset_scope": scope[exchange],
        "report_date": "2026-09-30",
        "published_at": "2026-09-30T18:00:00+08:00",
        "retrieved_at": "2026-09-30T19:00:00+08:00",
        "source_fields": fields,
        "source_units": {
            field: ("ten_thousand_" if exchange == "BSE" else "")
            + ("shares" if field in quantities else "CNY")
            for field in fields
        },
        "query_complete": True,
        "row_count": 1,
    }


class MarketFoundationTests(unittest.TestCase):
    def test_failed_owner_keeps_declared_fields_and_safe_error_projection(self):
        report = turnover_report("SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"})
        report["source_units"]["TRADE_AMT_01"] = "shares"
        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": ReportTransport(report)}
        )
        outcome = operations[0].execute(
            OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00"), 1
        )
        self.assertEqual(outcome.execution.state, "failed")
        self.assertEqual(
            {item.field for item in outcome.field_dispositions},
            {"TRADE_AMT_01", "TRADE_AMT_03"},
        )
        self.assertEqual(
            outcome.to_dict()["source_errors"][0]["message"],
            "The market source did not provide a qualified report.",
        )

    def test_real_leaf_calls_cannot_exceed_the_qualified_owner_budget(self):
        class OverBudgetTransport:
            def __init__(self):
                self.calls = 0

            def acquire(self, selector, request, timeout, invocation):
                def fetch():
                    self.calls += 1
                    return turnover_report(
                        "SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"}
                    )

                invocation.call(ProductionInvocationAdapter(fetch))
                return invocation.call(ProductionInvocationAdapter(fetch))

        transport = OverBudgetTransport()
        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": transport}
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcome = operations[0].execute(request, 1)
        result = fold_market_foundation((outcome,), request, operations=operations)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(result["operations"][0]["attempts"], 1)
        self.assertEqual(result["operations"][0]["source_result"], "unavailable")

    def test_missing_real_transport_stays_an_explicit_unclosed_source_contract(self):
        from a_share_research.market_foundation import UnprovedMarketFoundationSource

        operations = build_default_market_foundation_operations()
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        for operation in operations:
            if operation.operation_id == "bse_market_margin@1":
                # Its H/I-backed default HTTP path is tested with an owned fake
                # in test_market_foundation_bse_margin; do not invoke it here.
                continue
            with self.subTest(owner=operation.operation_id):
                with self.assertRaises(UnprovedMarketFoundationSource):
                    operation.execute(request, 1)

    def test_qualified_field_destination_is_present_in_the_owned_projection(self):
        operations = build_default_market_foundation_operations(
            transports={
                "sse_a_share_turnover@1": ReportTransport(
                    turnover_report("SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"})
                )
            }
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcome = operations[0].execute(request, 1)
        payload = outcome.to_dict()["observations"][0]
        self.assertEqual(
            payload["observation"]["metrics"]["turnover_amount_CNY"], "3500000000"
        )
        self.assertEqual(payload["source_operation"], "sse_a_share_turnover@1")
        self.assertEqual(
            {item.destination for item in outcome.field_dispositions},
            {"observations.payload.observation.metrics.turnover_amount_CNY"},
        )

    def test_margin_field_denominators_cannot_be_shrunk_by_caller_text(self):
        transports = {
            f"{exchange.lower()}_market_margin@1": ReportTransport(
                margin_report(exchange)
            )
            for exchange in ("SSE", "SZSE", "BSE")
        }
        operations = build_default_market_foundation_operations(transports=transports)
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcomes = tuple(operation.execute(request, 1) for operation in operations[3:])
        result = fold_market_foundation(outcomes, request, operations=operations)
        totals = result["margin"]["totals"]
        self.assertEqual(totals["financing_balance_CNY"]["value"], "3615000")
        self.assertEqual(
            totals["securities_lending_quantity_shares"]["value"], "240000"
        )
        self.assertEqual(totals["margin_total_balance_CNY"]["value"], "4537500")
        self.assertEqual(len(totals["financing_balance_CNY"]["operands"]), 3)
        report = transports["bse_market_margin@1"].report
        report["source_fields"]["SECURITIES_LENDING_BALANCE"] = "not_applicable"
        invalid = operations[5].execute(request, 1)
        limited = fold_market_foundation(
            (*outcomes[:2], invalid), request, operations=operations
        )
        self.assertTrue(
            all(total is None for total in limited["margin"]["totals"].values())
        )
        self.assertEqual(
            limited["margin"]["partitions"][2]["source_result"], "unavailable"
        )

    def test_immutable_outcome_and_projection_ignore_later_caller_mutation(self):
        report = turnover_report("SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"})
        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": ReportTransport(report)}
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcome = operations[0].execute(request, 1)
        first = fold_market_foundation((outcome,), request, operations=operations)
        report["source_fields"]["TRADE_AMT_01"] = "999"
        projected = outcome.to_dict()
        projected["observations"][0]["observation"]["metrics"][
            "turnover_amount_CNY"
        ] = "999"
        first["turnover"]["partitions"][0]["observation"]["metrics"][
            "turnover_amount_CNY"
        ] = "999"
        again = fold_market_foundation((outcome,), request, operations=operations)
        self.assertEqual(
            again["turnover"]["partitions"][0]["observation"]["metrics"][
                "turnover_amount_CNY"
            ],
            "3500000000",
        )

    def test_long_decimals_normalize_and_fold_independently_of_decimal_context(self):
        transports = {
            "sse_a_share_turnover@1": ReportTransport(
                turnover_report(
                    "SSE",
                    {
                        "TRADE_AMT_01": "12345678901234567890123456789.1",
                        "TRADE_AMT_03": "0.8",
                    },
                )
            )
        }
        operations = build_default_market_foundation_operations(transports=transports)
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        with localcontext() as context:
            context.prec = 6
            context.Emax = 9
            context.Emin = -9
            outcome = operations[0].execute(request, 1)
            result = fold_market_foundation((outcome,), request, operations=operations)
        value = result["turnover"]["partitions"][0]["observation"]["metrics"][
            "turnover_amount_CNY"
        ]
        self.assertEqual(value, "1234567890123456789012345678990000000")

    def test_installed_reference_preserves_the_exact_six_accepted_records(self):
        root = Path(__file__).resolve().parents[1]
        original = json.loads(
            (root / "docs/research/v0.4.0-operation-qualification.json").read_text()
        )
        installed = json.loads(
            (
                root
                / "skill/a-share-research/references/market-foundation-owner-qualification.json"
            ).read_text()
        )
        expected = [
            row
            for row in original["operations"]
            if row.get("source_contract") is not None
        ]
        self.assertEqual(installed["operations"], expected)
        self.assertEqual(len(expected), 6)

    def test_copied_install_loads_six_owners_and_projects_the_acquisition_stage(self):
        root = Path(__file__).resolve().parents[1]
        reports = {
            f"{exchange.lower()}_market_margin@1": margin_report(exchange)
            for exchange in ("SSE", "SZSE", "BSE")
        }
        reports.update(
            {
                "sse_a_share_turnover@1": turnover_report(
                    "SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"}
                ),
                "szse_a_share_turnover@1": turnover_report(
                    "SZSE", {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "225000.75"}
                ),
                "bse_a_share_turnover@1": turnover_report(
                    "BSE", {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "125000.50"}
                ),
            }
        )
        script = """
import json, sys
from a_share_research.market_foundation import OperationRequest, build_default_market_foundation_operations, fold_market_foundation
from a_share_research.provider_lane import ProductionInvocationAdapter
class Transport:
    def __init__(self, report): self.report = report
    def acquire(self, selector, request, timeout, invocation):
        return invocation.call(ProductionInvocationAdapter(lambda: self.report))
transports = {owner: Transport(report) for owner, report in json.load(sys.stdin).items()}
operations = build_default_market_foundation_operations(transports=transports)
request = OperationRequest('2026-09-30', '2026-09-30T19:00:00+08:00')
outcomes = tuple(operation.execute(request, 1) for operation in operations)
print(json.dumps(fold_market_foundation(outcomes, request, operations=operations)))
"""
        with tempfile.TemporaryDirectory(
            prefix="asr-v040-112-installed-", dir="/private/tmp"
        ) as directory:
            installed = Path(directory) / "a-share-research"
            shutil.copytree(
                root / "skill/a-share-research",
                installed,
                ignore=shutil.ignore_patterns("__pycache__", ".runtime", ".state"),
            )
            env = dict(
                os.environ,
                PYTHONPATH=str(installed / "scripts"),
                PYTHONDONTWRITEBYTECODE="1",
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                input=json.dumps(reports),
                text=True,
                capture_output=True,
                cwd=directory,
                env=env,
                check=True,
            )
        result = json.loads(completed.stdout)
        self.assertEqual(len(result["operations"]), 6)
        self.assertEqual([item["attempts"] for item in result["operations"]], [1] * 6)
        self.assertEqual(result["turnover"]["total"]["value"], "7000012500")
        self.assertEqual(
            result["margin"]["totals"]["financing_balance_CNY"]["value"], "3615000"
        )

    def test_complete_duplicate_or_missing_report_rows_are_unavailable(self):
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        for count in (0, 1, 3):
            with self.subTest(row_count=count):
                report = turnover_report(
                    "SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"}
                )
                report["row_count"] = count
                operations = build_default_market_foundation_operations(
                    transports={"sse_a_share_turnover@1": ReportTransport(report)}
                )
                outcome = operations[0].execute(request, 1)
                result = fold_market_foundation(
                    (outcome,), request, operations=operations
                )
                self.assertEqual(
                    result["operations"][0]["source_result"], "unavailable"
                )
                self.assertEqual(result["operations"][0]["attempts"], 1)

    def test_source_results_remain_separate_from_actual_execution(self):
        from a_share_research.market_foundation import MarketFoundationPermissionDenied

        class DeniedTransport:
            def acquire(self, selector, request, timeout, invocation):
                def denied():
                    raise MarketFoundationPermissionDenied("secret=/Users/private")

                return invocation.call(ProductionInvocationAdapter(denied))

        partial = turnover_report("SZSE", {})
        partial["query_complete"] = False
        partial["row_count"] = 0
        empty = turnover_report("SSE", {"TRADE_AMT_01": "0", "TRADE_AMT_03": "0"})
        unavailable = turnover_report(
            "BSE", {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "10"}
        )
        unavailable["report_date"] = "2026-09-29"
        transports = {
            "sse_a_share_turnover@1": ReportTransport(empty),
            "szse_a_share_turnover@1": ReportTransport(partial),
            "bse_a_share_turnover@1": ReportTransport(unavailable),
            "sse_market_margin@1": DeniedTransport(),
        }
        operations = build_default_market_foundation_operations(transports=transports)
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcomes = tuple(operation.execute(request, 1) for operation in operations[:4])
        result = fold_market_foundation(outcomes, request, operations=operations)
        self.assertEqual(
            [item["source_result"] for item in result["operations"][:4]],
            ["observed_empty", "partial", "unavailable", "credential_blocked"],
        )
        self.assertEqual(
            [item["execution"] for item in result["operations"][:4]],
            ["succeeded", "succeeded", "failed", "failed"],
        )
        self.assertEqual(
            [item["attempts"] for item in result["operations"][:4]], [1, 1, 1, 1]
        )
        self.assertIsNone(result["turnover"]["total"])
        self.assertNotIn("secret", str(result))

    def test_forged_locator_diagnostics_and_attempts_cannot_enter_the_fold(self):
        transport = ReportTransport(
            turnover_report("SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"})
        )
        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": transport}
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcome = operations[0].execute(request, 1)
        observation = outcome.to_dict()["observations"][0]
        observation["source_locator"] = "file:///Users/private/credential"
        forged = replace(
            outcome,
            observations=(observation,),
            diagnostics={"attempts": 99, "message": "secret=/Users/private"},
        )
        result = fold_market_foundation((forged,), request, operations=operations)
        self.assertEqual(result["operations"][0]["source_result"], "unavailable")
        self.assertEqual(result["operations"][0]["attempts"], 0)
        self.assertIsNone(result["turnover"]["total"])
        self.assertNotIn("private", str(result))

    def test_caller_cannot_replace_an_accepted_owner_selector_or_units(self):
        from a_share_research.market_foundation import MarketFoundationOperationAdapter

        with self.assertRaisesRegex(ValueError, "qualification"):
            MarketFoundationOperationAdapter(
                "sse_a_share_turnover@1", {"provider": "forged"}, None, None
            )

    def test_three_mandatory_turnover_partitions_use_exact_qualified_units(self):
        transports = {
            "sse_a_share_turnover@1": ReportTransport(
                turnover_report("SSE", {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"})
            ),
            "szse_a_share_turnover@1": ReportTransport(
                turnover_report(
                    "SZSE", {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "225000.75"}
                )
            ),
            "bse_a_share_turnover@1": ReportTransport(
                turnover_report(
                    "BSE", {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "125000.50"}
                )
            ),
        }
        operations = build_default_market_foundation_operations(transports=transports)
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcomes = tuple(
            operation.execute(request, 1)
            for operation in operations
            if operation.operation_id in transports
        )
        result = fold_market_foundation(outcomes, request, operations=operations)
        self.assertEqual(result["turnover"]["total"]["value"], "7000012500")
        self.assertEqual(result["turnover"]["total"]["unit"], "CNY")
        self.assertEqual(len(result["turnover"]["partitions"]), 3)
        self.assertEqual(
            [item["attempts"] for item in result["operations"]], [1, 1, 1, 0, 0, 0]
        )
        self.assertTrue(all(transport.calls == 1 for transport in transports.values()))


if __name__ == "__main__":
    unittest.main()
