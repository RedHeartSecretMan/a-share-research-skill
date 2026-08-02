from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capital_contract import CapitalQuery  # noqa: E402
from a_share_research.company_capital_sources import (  # noqa: E402
    EastmoneyDividendOperation,
    EastmoneyMarginTradingOperation,
    EastmoneyShareholderCountOperation,
)
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateDiagnostic,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)
FIXTURES = Path(__file__).parent / "fixtures" / "company_capital"
T = TypeVar("T")


class FixtureTransport:
    def __init__(
        self,
        pages: dict[int, str] | None = None,
        *,
        responses: list[HttpResponse] | None = None,
    ) -> None:
        self.pages = dict(pages or {})
        self.responses = list(responses or [])
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        if self.responses:
            return self.responses.pop(0)
        page = int(parse_qs(urlsplit(url).query)["pageNumber"][0])
        return fixture_response(self.pages[page])

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        raise AssertionError("Eastmoney datacenter operations must use GET")


class DiagnosticGate:
    def run(
        self,
        request: Callable[[], T],
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), (RequestGateDiagnostic("source_request_paced", 1.25),)


def no_wait_gate(*, backoffs: tuple[float, ...] = ()) -> SerialRequestGate:
    return SerialRequestGate(
        minimum_interval_seconds=0,
        jitter_bounds=(0, 0),
        rate_limit_backoffs=backoffs,
        sleeper=lambda _delay: None,
    )


def fixture_response(name: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=(FIXTURES / name).read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


def inline_response(
    payload: object | None = None,
    *,
    body: bytes | None = None,
    status: int = 200,
    content_type: str = "application/json",
) -> HttpResponse:
    encoded = json.dumps(payload).encode() if body is None else body
    return HttpResponse(
        status=status,
        content_type=content_type,
        body=encoded,
        retrieved_at=RETRIEVED_AT,
    )


def query(
    data_type: str,
    *,
    observed_from: str = "2026-01-01",
    observed_to: str = "2026-08-02",
    limit: int = 20,
) -> CapitalQuery:
    return CapitalQuery(
        data_types=(data_type,),
        as_of="2026-08-02",
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        subject={
            "security": {
                "exchange": "SSE",
                "code": "600519",
                "type": "A_SHARE",
            },
            "name": "贵州茅台",
        },
        parameters={},
    )


class CompanyCapitalSourceTests(unittest.TestCase):
    def test_margin_trading_maps_daily_metrics_with_precision_and_pagination(
        self,
    ) -> None:
        transport = FixtureTransport({1: "margin_page_1.json", 2: "margin_page_2.json"})
        batch = EastmoneyMarginTradingOperation(
            transport,
            page_size=2,
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "margin_trading",
                observed_from="2026-07-29",
                observed_to="2026-07-31",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 3)
        first = batch.observations[0]
        self.assertEqual(first.data_type, "margin_trading")
        self.assertEqual(first.source_role, "market_observation")
        self.assertEqual(first.observed_on, "2026-07-31")
        self.assertIsNone(first.available_at)
        self.assertEqual(first.period["kind"], "trading_day")
        self.assertEqual(first.period["frequency"], "daily")
        self.assertEqual(
            first.metrics,
            {
                "financing_balance": "18000000000.1200",
                "financing_buy_amount": "210000000.50",
                "financing_repayment_amount": "190000000.25",
                "securities_lending_balance": "9800000.00",
                "securities_lending_sell_volume": "1200",
                "securities_lending_repayment_volume": "900",
                "margin_balance": "18009800000.1200",
            },
        )
        self.assertEqual(first.units["financing_balance"], "CNY")
        self.assertEqual(first.units["securities_lending_sell_volume"], "share")
        self.assertEqual(first.directions["financing_buy_amount"], "inflow")
        self.assertIn(
            "availability_time_unknown",
            first.limitations,
        )
        self.assertEqual(len(transport.urls), 2)
        parsed = parse_qs(urlsplit(transport.urls[0]).query)
        self.assertEqual(parsed["reportName"], ["RPTA_WEB_RZRQ_GGMX"])
        self.assertEqual(parsed["filter"], ['(SCODE="600519")'])
        self.assertEqual(len(batch.degradations), 2)

    def test_shareholder_count_maps_period_change_and_average_holding(self) -> None:
        transport = FixtureTransport({1: "shareholder_count.json"})
        batch = EastmoneyShareholderCountOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "shareholder_count",
                observed_from="2026-04-01",
                observed_to="2026-07-01",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2026-06-30")
        self.assertEqual(item.period["kind"], "reporting_period_end")
        self.assertEqual(item.period["frequency"], "quarterly")
        self.assertEqual(item.dimensions["previous_period_end"], "2026-03-31")
        self.assertEqual(
            item.metrics,
            {
                "shareholder_count": "150000",
                "shareholder_count_change": "-5000",
                "shareholder_count_change_ratio": "-3.2258",
                "average_shares_per_holder": "833.3300",
            },
        )
        self.assertEqual(item.units["shareholder_count"], "account")
        self.assertEqual(item.units["shareholder_count_change_ratio"], "percent")
        self.assertEqual(
            item.directions["average_shares_per_holder"],
            "higher_is_more_concentrated",
        )

    def test_dividend_maps_per_ten_share_terms_status_and_key_dates(self) -> None:
        transport = FixtureTransport({1: "dividend.json"})
        batch = EastmoneyDividendOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(query("dividend"))

        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2026-06-19")
        self.assertEqual(item.period["frequency"], "event")
        self.assertEqual(
            item.metrics,
            {
                "cash_dividend_per_10_shares_before_tax": "27.6100",
                "bonus_shares_per_10_shares": "0",
                "transfer_shares_per_10_shares": "0",
            },
        )
        self.assertEqual(
            item.units["cash_dividend_per_10_shares_before_tax"],
            "CNY_per_10_shares",
        )
        self.assertEqual(item.dimensions["implementation_status"], "实施")
        self.assertEqual(item.dimensions["report_period_end"], "2025-12-31")
        self.assertEqual(item.dimensions["plan_notice_date"], "2026-04-02")
        self.assertEqual(item.dimensions["record_date"], "2026-06-18")
        self.assertEqual(item.dimensions["ex_dividend_date"], "2026-06-19")
        self.assertEqual(item.dimensions["cash_payment_date"], "2026-06-19")
        parsed = parse_qs(urlsplit(transport.urls[0]).query)
        self.assertEqual(parsed["reportName"], ["RPT_SHAREBONUS_DET"])
        self.assertEqual(parsed["sortColumns"], ["EX_DIVIDEND_DATE"])

    def test_dividend_order_allows_pending_plan_without_ex_date(self) -> None:
        implemented = json.loads((FIXTURES / "dividend.json").read_bytes())["result"][
            "data"
        ][0]
        implemented["REPORT_DATE"] = "2024-12-31 00:00:00"
        implemented["PLAN_NOTICE_DATE"] = "2025-04-02 00:00:00"
        implemented["EX_DIVIDEND_DATE"] = "2025-06-19 00:00:00"
        pending = dict(implemented)
        pending.update(
            {
                "REPORT_DATE": "2026-06-30 00:00:00",
                "PLAN_NOTICE_DATE": "2026-07-01 00:00:00",
                "EQUITY_RECORD_DATE": None,
                "EX_DIVIDEND_DATE": None,
                "PAY_CASH_DATE": None,
                "ASSIGN_PROGRESS": "预案",
            }
        )
        payload = {
            "success": True,
            "result": {"pages": 1, "count": 2, "data": [implemented, pending]},
        }

        batch = EastmoneyDividendOperation(
            FixtureTransport(responses=[inline_response(payload)]),
            request_gate=no_wait_gate(),
        ).collect(query("dividend", observed_from="2025-01-01"))

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.observed_on for item in batch.observations],
            ["2026-07-01", "2025-06-19"],
        )
        self.assertIn(
            "event_date_uses_plan_or_report_date",
            batch.observations[0].limitations,
        )

    def test_dividend_checks_later_pages_for_pending_plan_without_ex_date(self) -> None:
        implemented = json.loads((FIXTURES / "dividend.json").read_bytes())["result"][
            "data"
        ][0]
        implemented["REPORT_DATE"] = "2024-12-31 00:00:00"
        implemented["PLAN_NOTICE_DATE"] = "2025-04-02 00:00:00"
        implemented["EX_DIVIDEND_DATE"] = "2025-06-19 00:00:00"
        pending = dict(implemented)
        pending.update(
            {
                "REPORT_DATE": "2026-06-30 00:00:00",
                "PLAN_NOTICE_DATE": "2026-07-01 00:00:00",
                "EQUITY_RECORD_DATE": None,
                "EX_DIVIDEND_DATE": None,
                "PAY_CASH_DATE": None,
                "ASSIGN_PROGRESS": "预案",
            }
        )

        def page(row: dict[str, object]) -> HttpResponse:
            return inline_response(
                {
                    "success": True,
                    "result": {"pages": 2, "count": 2, "data": [row]},
                }
            )

        transport = FixtureTransport(responses=[page(implemented), page(pending)])
        batch = EastmoneyDividendOperation(
            transport,
            page_size=1,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "dividend",
                observed_from="2026-01-01",
                observed_to="2026-08-02",
                limit=1,
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(len(transport.urls), 2)
        self.assertEqual(
            [item.observed_on for item in batch.observations], ["2026-07-01"]
        )

    def test_invalid_subject_and_wrong_row_security_fail_closed(self) -> None:
        transport = FixtureTransport({1: "margin_page_1.json"})
        invalid = query("margin_trading")
        assert invalid.subject is not None
        invalid.subject["security"]["type"] = "ETF"

        invalid_batch = EastmoneyMarginTradingOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(invalid)

        self.assertEqual(transport.urls, [])
        self.assertEqual(invalid_batch.source_errors[0].code, "invalid_subject")

        mismatch_payload = {
            "success": True,
            "result": {
                "pages": 1,
                "count": 1,
                "data": [
                    {
                        "SCODE": "000001",
                        "DATE": "2026-07-31",
                        "RZYE": 1,
                        "RZMRE": 1,
                        "RZCHE": 1,
                        "RQYE": 1,
                        "RQMCL": 1,
                        "RQCHL": 1,
                        "RZRQYE": 2,
                    }
                ],
            },
        }
        mismatch = EastmoneyMarginTradingOperation(
            FixtureTransport(responses=[inline_response(mismatch_payload)]),
            request_gate=no_wait_gate(),
        ).collect(query("margin_trading"))
        self.assertEqual(mismatch.observations, ())
        self.assertEqual(mismatch.source_errors[0].code, "identity_mismatch")

    def test_empty_null_and_unknown_schema_are_explicit(self) -> None:
        cases = (
            (inline_response(body=b""), "empty_response"),
            (inline_response({"success": True, "result": None}), "empty_response"),
            (
                inline_response(
                    {
                        "success": False,
                        "code": 9201,
                        "message": "返回数据为空",
                        "result": None,
                    }
                ),
                "empty_response",
            ),
            (
                inline_response(
                    {"success": True, "result": {"pages": 1, "count": 1, "data": {}}}
                ),
                "unknown_schema",
            ),
        )
        for response, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                batch = EastmoneyMarginTradingOperation(
                    FixtureTransport(responses=[response]),
                    request_gate=no_wait_gate(),
                ).collect(query("margin_trading"))
                self.assertEqual(batch.observations, ())
                self.assertFalse(batch.complete)
                self.assertEqual(batch.source_errors[0].code, expected_code)

    def test_null_numeric_value_is_preserved_without_becoming_zero(self) -> None:
        payload = {
            "success": True,
            "result": {
                "pages": 1,
                "count": 1,
                "data": [
                    {
                        "SCODE": "600519",
                        "DATE": "2026-07-31",
                        "RZYE": None,
                        "RZMRE": 1,
                        "RZCHE": 1,
                        "RQYE": 1,
                        "RQMCL": 1,
                        "RQCHL": 1,
                        "RZRQYE": 2,
                    }
                ],
            },
        }
        batch = EastmoneyMarginTradingOperation(
            FixtureTransport(responses=[inline_response(payload)]),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "margin_trading",
                observed_from="2026-07-31",
                observed_to="2026-07-31",
            )
        )

        self.assertTrue(batch.complete)
        self.assertIsNone(batch.observations[0].metrics["financing_balance"])
        self.assertIn("source_value_missing", batch.observations[0].limitations)

    def test_datacenter_accepts_json_body_served_as_text_plain(self) -> None:
        payload = json.loads((FIXTURES / "margin_page_2.json").read_bytes())
        payload["result"]["pages"] = 1
        payload["result"]["count"] = 1
        batch = EastmoneyMarginTradingOperation(
            FixtureTransport(
                responses=[
                    inline_response(
                        payload,
                        content_type="text/plain",
                    )
                ]
            ),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "margin_trading",
                observed_from="2026-07-29",
                observed_to="2026-07-29",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 1)

    def test_bounded_pagination_failure_is_explicit(self) -> None:
        batch = EastmoneyMarginTradingOperation(
            FixtureTransport({1: "margin_page_1.json"}),
            page_size=2,
            max_pages=1,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "margin_trading",
                observed_from="2026-07-01",
                observed_to="2026-07-31",
            )
        )

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 2)
        self.assertEqual(batch.source_errors[0].code, "pagination_incomplete")

    def test_limit_truncation_does_not_claim_complete_window_coverage(self) -> None:
        transport = FixtureTransport({1: "margin_page_1.json", 2: "margin_page_2.json"})
        batch = EastmoneyMarginTradingOperation(
            transport,
            page_size=2,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "margin_trading",
                observed_from="2026-07-29",
                observed_to="2026-07-31",
                limit=1,
            )
        )

        self.assertEqual(len(batch.observations), 1)
        self.assertFalse(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertIn("result_truncated_to_limit", batch.limitations)
        self.assertEqual(len(transport.urls), 1)

    def test_rows_outside_window_are_not_silently_reported_as_complete(self) -> None:
        batch = EastmoneyShareholderCountOperation(
            FixtureTransport({1: "shareholder_count.json"}),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "shareholder_count",
                observed_from="2025-01-01",
                observed_to="2025-02-01",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertFalse(batch.complete)
        self.assertEqual(
            batch.source_errors[0].code,
            "no_observations_in_window",
        )

    def test_early_window_stop_requires_descending_source_dates(self) -> None:
        def row(day: str) -> dict[str, object]:
            return {
                "SCODE": "600519",
                "DATE": day,
                "RZYE": 1,
                "RZMRE": 1,
                "RZCHE": 1,
                "RQYE": 1,
                "RQMCL": 1,
                "RQCHL": 1,
                "RZRQYE": 2,
            }

        payload = {
            "success": True,
            "result": {
                "pages": 1,
                "count": 2,
                "data": [row("2026-07-28"), row("2026-07-30")],
            },
        }
        batch = EastmoneyMarginTradingOperation(
            FixtureTransport(responses=[inline_response(payload)]),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "margin_trading",
                observed_from="2026-07-29",
                observed_to="2026-07-31",
            )
        )

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "source_order_mismatch")

    def test_terminal_429_preserves_gate_backoff_diagnostic(self) -> None:
        transport = FixtureTransport(
            responses=[
                inline_response({}, status=429),
                inline_response({}, status=429),
            ]
        )
        batch = EastmoneyMarginTradingOperation(
            transport,
            request_gate=no_wait_gate(backoffs=(0.25,)),
        ).collect(query("margin_trading"))

        self.assertFalse(batch.complete)
        self.assertEqual(batch.source_errors[0].code, "rate_limited")
        self.assertEqual(len(transport.urls), 2)
        self.assertEqual(batch.degradations[0].code, "rate_limit_backoff")
        self.assertEqual(
            batch.degradations[0].details,
            {"delay_seconds": "0.250", "attempt": "1"},
        )


if __name__ == "__main__":
    unittest.main()
