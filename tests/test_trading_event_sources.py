from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capital_contract import (  # noqa: E402
    CapitalObservation,
    CapitalQuery,
)
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateDiagnostic,
    SerialRequestGate,
)
from a_share_research.trading_event_sources import (  # noqa: E402
    EastmoneyTradingEventOperation,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "trading_events"
T = TypeVar("T")


class FixedTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.calls.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("Eastmoney trading-event sources use GET requests")


class DiagnosticRequestGate:
    def __init__(
        self,
        diagnostics: tuple[RequestGateDiagnostic, ...] = (),
    ) -> None:
        self.diagnostics = diagnostics
        self.calls = 0

    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        self.calls += 1
        return request(), self.diagnostics


def no_wait_gate() -> SerialRequestGate:
    return SerialRequestGate(
        minimum_interval_seconds=0,
        jitter_bounds=(0, 0),
        rate_limit_backoffs=(),
    )


def response(fixture: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=(FIXTURES / fixture).read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


def inline_response(
    payload: dict[str, object],
    *,
    status: int = 200,
    content_type: str = "application/json",
) -> HttpResponse:
    return HttpResponse(
        status=status,
        content_type=content_type,
        body=json.dumps(payload, ensure_ascii=False).encode(),
        retrieved_at=RETRIEVED_AT,
    )


def fixture_payload(fixture: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / fixture).read_bytes())
    assert isinstance(value, dict)
    return value


def query(
    data_type: str,
    *,
    observed_from: str = "2026-07-01",
    observed_to: str = "2026-08-02",
    subject: dict[str, object] | None = None,
    limit: int = 20,
) -> CapitalQuery:
    if subject is None and data_type != "market_dragon_tiger":
        subject = {
            "security": {
                "exchange": "SZSE",
                "code": "300058",
                "type": "A_SHARE",
            },
            "name": "蓝色光标",
        }
    return CapitalQuery(
        data_types=(data_type,),
        as_of="2026-08-02",
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        subject=subject,
        parameters={},
    )


def multi_mode_query(data_types: tuple[str, ...]) -> CapitalQuery:
    base = query("block_trade")
    return CapitalQuery(
        data_types=data_types,
        as_of=base.as_of,
        observed_from=base.observed_from,
        observed_to=base.observed_to,
        limit=base.limit,
        subject=base.subject,
        parameters=base.parameters,
    )


class EastmoneyTradingEventOperationTests(unittest.TestCase):
    def test_later_lockup_http_failure_preserves_completed_block_trade_mode(
        self,
    ) -> None:
        batch = EastmoneyTradingEventOperation(
            FixedTransport(
                [
                    response("block_trade.json"),
                    inline_response({}, status=500),
                ]
            ),
            request_gate=no_wait_gate(),
        ).collect(multi_mode_query(("block_trade", "lockup")))

        block_trade = batch.mode_acquisitions["block_trade"]
        lockup = batch.mode_acquisitions["lockup"]
        self.assertEqual(block_trade.state, "observed_nonempty")
        self.assertTrue(block_trade.complete)
        self.assertEqual(len(block_trade.observations), 2)
        self.assertEqual(block_trade.source_errors, ())
        self.assertEqual(lockup.state, "indeterminate")
        self.assertFalse(lockup.complete)
        self.assertEqual(lockup.observations, ())
        self.assertEqual(
            [item.code for item in lockup.source_errors],
            ["upstream_http_error"],
        )
        self.assertEqual(len(batch.observations), 2)

    def test_failed_mode_does_not_stop_or_rewrite_later_sibling(self) -> None:
        batch = EastmoneyTradingEventOperation(
            FixedTransport(
                [
                    response("block_trade.json"),
                    inline_response({}, status=500),
                    response("dragon_records.json"),
                    response("dragon_buy_seats.json"),
                    response("dragon_sell_seats.json"),
                ]
            ),
            request_gate=no_wait_gate(),
        ).collect(multi_mode_query(("block_trade", "lockup", "dragon_tiger")))

        self.assertEqual(
            {
                data_type: acquisition.state
                for data_type, acquisition in batch.mode_acquisitions.items()
            },
            {
                "block_trade": "observed_nonempty",
                "lockup": "indeterminate",
                "dragon_tiger": "observed_nonempty",
            },
        )
        self.assertEqual(len(batch.mode_acquisitions["block_trade"].observations), 2)
        self.assertEqual(len(batch.mode_acquisitions["dragon_tiger"].observations), 2)
        self.assertEqual(len(batch.observations), 4)

    def test_late_dragon_tiger_seat_http_failure_keeps_primary_records(self) -> None:
        cases = (
            (
                "buy_details",
                [response("dragon_records.json"), inline_response({}, status=500)],
            ),
            (
                "sell_details",
                [
                    response("dragon_records.json"),
                    response("dragon_buy_seats.json"),
                    inline_response({}, status=500),
                ],
            ),
        )
        for failed_stage, responses in cases:
            with self.subTest(failed_stage=failed_stage):
                batch = EastmoneyTradingEventOperation(
                    FixedTransport(responses),
                    request_gate=no_wait_gate(),
                ).collect(query("dragon_tiger"))

                acquisition = batch.mode_acquisitions["dragon_tiger"]
                self.assertEqual(acquisition.state, "partial")
                self.assertFalse(acquisition.complete)
                self.assertEqual(len(acquisition.observations), 2)
                self.assertEqual(
                    [item.code for item in acquisition.source_errors],
                    ["upstream_http_error"],
                )
                for observation in acquisition.observations:
                    self.assertEqual(observation.dimensions["buy_seats"], [])
                    self.assertEqual(observation.dimensions["sell_seats"], [])
                    self.assertIsNone(observation.metrics["institution_buy_amount"])
                    self.assertIsNone(observation.metrics["institution_sell_amount"])
                    self.assertIsNone(observation.metrics["institution_net_amount"])
                    self.assertIn(
                        "dragon_tiger_seat_and_institution_details_not_collected",
                        observation.limitations,
                    )

    def test_late_page_row_schema_failure_keeps_valid_earlier_page(self) -> None:
        block_rows = fixture_payload("block_trade.json")["result"]["data"]
        dragon_rows = fixture_payload("dragon_records.json")["result"]["data"]
        lockup_rows = fixture_payload("lockup.json")["result"]["data"]
        market_page_1 = fixture_payload("market_dragon_page_1.json")
        market_page_2 = fixture_payload("market_dragon_page_2.json")
        cases = (
            (
                "block_trade",
                block_rows[0],
                block_rows[1],
                "BUYER_NAME",
                None,
                "2026-07-01",
                "2026-08-02",
            ),
            (
                "dragon_tiger",
                dragon_rows[0],
                dragon_rows[1],
                "EXPLANATION",
                None,
                "2026-07-01",
                "2026-08-02",
            ),
            (
                "lockup",
                lockup_rows[0],
                lockup_rows[1],
                "FREE_SHARES_TYPE",
                None,
                "2026-07-01",
                "2026-10-31",
            ),
            (
                "market_dragon_tiger",
                market_page_1["result"]["data"][0],
                market_page_2["result"]["data"][0],
                "EXPLANATION",
                None,
                "2026-08-01",
                "2026-08-01",
            ),
            (
                "block_trade",
                block_rows[0],
                block_rows[1],
                "DEAL_PRICE",
                "0",
                "2026-07-01",
                "2026-08-02",
            ),
            (
                "block_trade",
                block_rows[0],
                block_rows[1],
                "DEAL_VOLUME",
                "-1",
                "2026-07-01",
                "2026-08-02",
            ),
            (
                "lockup",
                lockup_rows[0],
                lockup_rows[1],
                "FREE_SHARES",
                "-1",
                "2026-07-01",
                "2026-10-31",
            ),
            (
                "lockup",
                lockup_rows[0],
                lockup_rows[1],
                "FREE_RATIO",
                "2",
                "2026-07-01",
                "2026-10-31",
            ),
        )
        for (
            data_type,
            first_row,
            invalid_row,
            field,
            invalid_value,
            start,
            end,
        ) in cases:
            with self.subTest(data_type=data_type, field=field):
                invalid_row = dict(invalid_row)
                if invalid_value is None:
                    invalid_row.pop(field)
                else:
                    invalid_row[field] = invalid_value
                pages = [
                    {
                        "success": True,
                        "code": 0,
                        "result": {"pages": 2, "count": 2, "data": [first_row]},
                    },
                    {
                        "success": True,
                        "code": 0,
                        "result": {"pages": 2, "count": 2, "data": [invalid_row]},
                    },
                ]
                batch = EastmoneyTradingEventOperation(
                    FixedTransport([inline_response(page) for page in pages]),
                    request_gate=no_wait_gate(),
                ).collect(query(data_type, observed_from=start, observed_to=end))

                acquisition = batch.mode_acquisitions[data_type]
                self.assertEqual(acquisition.state, "partial")
                self.assertFalse(acquisition.complete)
                self.assertEqual(len(acquisition.observations), 1)
                self.assertEqual(
                    [item.code for item in acquisition.source_errors],
                    ["unknown_schema"],
                )

    def test_provider_proven_zero_rows_is_complete_for_each_mode(self) -> None:
        empty_payload = {
            "success": True,
            "code": 0,
            "result": {"pages": 1, "count": 0, "data": []},
        }
        for data_type in (
            "block_trade",
            "dragon_tiger",
            "lockup",
            "market_dragon_tiger",
        ):
            with self.subTest(data_type=data_type):
                research_query = query(
                    data_type,
                    observed_from=(
                        "2026-08-02"
                        if data_type == "market_dragon_tiger"
                        else "2026-07-01"
                    ),
                    observed_to="2026-08-02",
                )
                batch = EastmoneyTradingEventOperation(
                    FixedTransport([inline_response(empty_payload)]),
                    request_gate=no_wait_gate(),
                ).collect(research_query)

                acquisition = batch.mode_acquisitions[data_type]
                self.assertEqual(acquisition.state, "observed_empty")
                self.assertTrue(acquisition.complete)
                self.assertEqual(acquisition.observations, ())
                self.assertEqual(acquisition.source_errors, ())

    def test_late_page_failure_keeps_valid_rows_as_partial_mode(self) -> None:
        batch = EastmoneyTradingEventOperation(
            FixedTransport(
                [
                    response("market_dragon_page_1.json"),
                    inline_response({}, status=500),
                ]
            ),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        acquisition = batch.mode_acquisitions["market_dragon_tiger"]
        self.assertEqual(acquisition.state, "partial")
        self.assertFalse(acquisition.complete)
        self.assertEqual(len(acquisition.observations), 1)
        self.assertEqual(acquisition.observations[0].data_type, "market_dragon_tiger")
        self.assertEqual(
            [item.code for item in acquisition.source_errors],
            ["upstream_http_error"],
        )

    def test_late_page_schema_failure_keeps_valid_rows_as_partial_mode(self) -> None:
        invalid_json = HttpResponse(
            status=200,
            content_type="application/json",
            body=b"{",
            retrieved_at=RETRIEVED_AT,
        )
        batch = EastmoneyTradingEventOperation(
            FixedTransport(
                [
                    response("market_dragon_page_1.json"),
                    invalid_json,
                ]
            ),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        acquisition = batch.mode_acquisitions["market_dragon_tiger"]
        self.assertEqual(acquisition.state, "partial")
        self.assertEqual(len(acquisition.observations), 1)
        self.assertEqual(
            [item.code for item in acquisition.source_errors],
            ["unknown_schema"],
        )

    def test_each_mode_retains_valid_first_page_on_late_transport_failure(self) -> None:
        cases = (
            ("block_trade", "block_trade.json", "2026-07-01", "2026-08-02", 2),
            ("dragon_tiger", "dragon_records.json", "2026-07-01", "2026-08-02", 2),
            ("lockup", "lockup.json", "2026-07-01", "2026-10-31", 2),
            (
                "market_dragon_tiger",
                "market_dragon_page_1.json",
                "2026-08-01",
                "2026-08-01",
                1,
            ),
        )
        for data_type, fixture, observed_from, observed_to, row_count in cases:
            with self.subTest(data_type=data_type):
                payload = fixture_payload(fixture)
                payload["result"]["pages"] = 2  # type: ignore[index]
                payload["result"]["count"] = row_count + 1  # type: ignore[index]
                batch = EastmoneyTradingEventOperation(
                    FixedTransport(
                        [inline_response(payload), inline_response({}, status=500)]
                    ),
                    request_gate=no_wait_gate(),
                ).collect(
                    query(
                        data_type,
                        observed_from=observed_from,
                        observed_to=observed_to,
                    )
                )

                acquisition = batch.mode_acquisitions[data_type]
                self.assertEqual(acquisition.state, "partial")
                self.assertFalse(acquisition.complete)
                self.assertEqual(len(acquisition.observations), row_count)
                self.assertEqual(
                    [item.code for item in acquisition.source_errors],
                    ["upstream_http_error"],
                )

    def test_each_mode_schema_failure_is_indeterminate_and_mode_local(self) -> None:
        invalid_json = HttpResponse(
            status=200,
            content_type="application/json",
            body=b"{",
            retrieved_at=RETRIEVED_AT,
        )
        for data_type in (
            "block_trade",
            "dragon_tiger",
            "lockup",
            "market_dragon_tiger",
        ):
            with self.subTest(data_type=data_type):
                batch = EastmoneyTradingEventOperation(
                    FixedTransport([invalid_json]),
                    request_gate=no_wait_gate(),
                ).collect(
                    query(
                        data_type,
                        observed_from=(
                            "2026-08-01"
                            if data_type == "market_dragon_tiger"
                            else "2026-07-01"
                        ),
                        observed_to=(
                            "2026-10-31" if data_type == "lockup" else "2026-08-01"
                        ),
                    )
                )

                acquisition = batch.mode_acquisitions[data_type]
                self.assertEqual(acquisition.state, "indeterminate")
                self.assertFalse(acquisition.complete)
                self.assertEqual(acquisition.observations, ())
                self.assertEqual(
                    [item.code for item in acquisition.source_errors],
                    ["unknown_schema"],
                )

    def test_semantic_duplicate_conflict_fails_closed_for_that_mode(self) -> None:
        payload = fixture_payload("market_dragon_page_1.json")
        first = payload["result"]["data"][0]  # type: ignore[index]
        conflicting = dict(first)
        conflicting["BILLBOARD_NET_AMT"] = "49000000"
        payload["result"]["pages"] = 1  # type: ignore[index]
        payload["result"]["count"] = 2  # type: ignore[index]
        payload["result"]["data"] = [first, conflicting]  # type: ignore[index]

        batch = EastmoneyTradingEventOperation(
            FixedTransport([inline_response(payload)]),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        acquisition = batch.mode_acquisitions["market_dragon_tiger"]
        self.assertEqual(acquisition.state, "indeterminate")
        self.assertEqual(acquisition.observations, ())
        self.assertEqual(
            [item.code for item in acquisition.source_errors],
            ["duplicate_source_conflict"],
        )

    def assert_time_contract(
        self, observations: tuple[CapitalObservation, ...]
    ) -> None:
        for observation in observations:
            self.assertEqual(set(observation.period), {"start", "end", "frequency"})
            if observation.available_at is None:
                self.assertIn(
                    "availability_time_unknown",
                    observation.limitations,
                )

    def test_stock_dragon_tiger_keeps_records_top_five_seats_and_institutions(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                response("dragon_records.json"),
                response("dragon_buy_seats.json"),
                response("dragon_sell_seats.json"),
            ]
        )
        gate = DiagnosticRequestGate(
            (
                RequestGateDiagnostic(
                    code="source_request_paced",
                    delay_seconds=1.25,
                ),
            )
        )

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=gate,
        ).collect(query("dragon_tiger"))

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            batch.mode_acquisitions["dragon_tiger"].state,
            "observed_nonempty",
        )
        self.assertEqual(gate.calls, 3)
        self.assertEqual(len(batch.degradations), 3)
        self.assertEqual(len(batch.observations), 2)
        self.assert_time_contract(batch.observations)
        latest = batch.observations[0]
        self.assertEqual(latest.data_type, "dragon_tiger")
        self.assertEqual(latest.source_role, "market_signal")
        self.assertEqual(latest.observed_on, "2026-07-31")
        self.assertEqual(latest.subject, query("dragon_tiger").subject)
        self.assertEqual(
            latest.metrics,
            {
                "net_buy_amount": "125000000.123456789012345678",
                "turnover_rate": "8.25",
                "institution_buy_amount": "37000000",
                "institution_sell_amount": "12000000",
                "institution_net_amount": "25000000",
            },
        )
        self.assertEqual(
            latest.units,
            {
                "net_buy_amount": "CNY",
                "turnover_rate": "percent",
                "institution_buy_amount": "CNY",
                "institution_sell_amount": "CNY",
                "institution_net_amount": "CNY",
            },
        )
        self.assertEqual(
            latest.directions,
            {
                "net_buy_amount": "positive_is_net_buy",
                "turnover_rate": "not_directional",
                "institution_buy_amount": "positive_is_buy",
                "institution_sell_amount": "positive_is_sell",
                "institution_net_amount": "positive_is_net_buy",
            },
        )
        self.assertEqual(latest.dimensions["reason"], "日涨幅达到15%的前5只证券")
        self.assertEqual(len(latest.dimensions["buy_seats"]), 5)
        self.assertEqual(len(latest.dimensions["sell_seats"]), 3)
        self.assertEqual(latest.dimensions["buy_seats"][0]["rank"], 1)
        self.assertTrue(latest.dimensions["buy_seats"][1]["institution"])
        self.assertEqual(latest.dimensions["seat_amount_unit"], "CNY")
        self.assertEqual(
            latest.dimensions["seat_amount_directions"],
            {
                "buy_amount": "positive_is_buy",
                "sell_amount": "positive_is_sell",
                "net_amount": "positive_is_net_buy",
            },
        )
        self.assertNotIn(
            "dragon_tiger_seat_and_institution_details_not_collected",
            latest.limitations,
        )
        historical = batch.observations[1]
        self.assertEqual(historical.dimensions["buy_seats"], [])
        self.assertEqual(historical.dimensions["sell_seats"], [])
        self.assertIsNone(historical.metrics["institution_buy_amount"])
        self.assertIsNone(historical.metrics["institution_sell_amount"])
        self.assertIsNone(historical.metrics["institution_net_amount"])
        self.assertIn(
            "dragon_tiger_seat_and_institution_details_not_collected",
            historical.limitations,
        )
        reports = [
            parse_qs(urlsplit(url).query)["reportName"][0]
            for url, _headers in transport.calls
        ]
        self.assertEqual(
            reports,
            [
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                "RPT_BILLBOARD_DAILYDETAILSBUY",
                "RPT_BILLBOARD_DAILYDETAILSSELL",
            ],
        )

    def test_market_dragon_tiger_ranks_one_exact_market_date(self) -> None:
        transport = FixedTransport(
            [
                response("market_dragon_page_1.json"),
                response("market_dragon_page_2.json"),
            ]
        )

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            batch.mode_acquisitions["market_dragon_tiger"].state,
            "observed_nonempty",
        )
        self.assertEqual(len(batch.observations), 2)
        self.assert_time_contract(batch.observations)
        first, second = batch.observations
        self.assertEqual(first.data_type, "market_dragon_tiger")
        self.assertTrue(all(item.subject is None for item in batch.observations))
        self.assertTrue(
            all(
                item.dimensions["market_scope"] == "eastmoney_all_market_billboard"
                for item in batch.observations
            )
        )
        self.assertTrue(
            all(
                "security_exchange_unverified" in item.limitations
                for item in batch.observations
            )
        )
        self.assertEqual(first.dimensions["provider_security_code"], "300058")
        self.assertEqual(first.dimensions["provider_security_name"], "蓝色光标")
        self.assertIn("security_exchange_unverified", first.limitations)
        self.assertEqual(first.observed_on, "2026-08-01")
        self.assertEqual(first.dimensions["net_buy_rank"], 1)
        self.assertEqual(second.dimensions["net_buy_rank"], 2)
        self.assertEqual(
            first.metrics,
            {
                "close_price": "12.34",
                "change_rate": "16.25",
                "net_buy_amount": "50000000",
                "buy_amount": "150000000",
                "sell_amount": "100000000",
                "turnover_rate": "9.5",
            },
        )
        self.assertEqual(
            first.units,
            {
                "close_price": "CNY/share",
                "change_rate": "percent",
                "net_buy_amount": "CNY",
                "buy_amount": "CNY",
                "sell_amount": "CNY",
                "turnover_rate": "percent",
            },
        )
        self.assertEqual(
            first.directions,
            {
                "close_price": "not_directional",
                "change_rate": "positive_is_price_increase",
                "net_buy_amount": "positive_is_net_buy",
                "buy_amount": "positive_is_buy",
                "sell_amount": "positive_is_sell",
                "turnover_rate": "not_directional",
            },
        )
        request_query = parse_qs(urlsplit(transport.calls[0][0]).query)
        self.assertEqual(request_query["reportName"], ["RPT_DAILYBILLBOARD_DETAILSNEW"])
        self.assertIn("TRADE_DATE>='2026-08-01'", request_query["filter"][0])
        self.assertNotIn("SECURITY_CODE", request_query["filter"][0])
        self.assertEqual(
            [
                parse_qs(urlsplit(url).query)["pageNumber"][0]
                for url, _headers in transport.calls
            ],
            ["1", "2"],
        )

    def test_datacenter_accepts_json_body_served_as_text_plain(self) -> None:
        payload = fixture_payload("market_dragon_page_1.json")
        payload["result"]["pages"] = 1  # type: ignore[index]
        payload["result"]["count"] = 1  # type: ignore[index]
        batch = EastmoneyTradingEventOperation(
            FixedTransport([inline_response(payload, content_type="text/plain")]),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 1)

    def test_datacenter_rejects_non_json_media_type(self) -> None:
        batch = EastmoneyTradingEventOperation(
            FixedTransport(
                [
                    inline_response(
                        fixture_payload("market_dragon_page_1.json"),
                        content_type="text/html",
                    )
                ]
            ),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        self.assertFalse(batch.complete)
        self.assertEqual(batch.source_errors[0].code, "unexpected_content_type")

    def test_market_ranking_preserves_missing_turnover_as_explicit_null(self) -> None:
        payload = fixture_payload("market_dragon_page_1.json")
        payload["result"]["pages"] = 1  # type: ignore[index]
        payload["result"]["count"] = 1  # type: ignore[index]
        payload["result"]["data"][0]["TURNOVERRATE"] = None  # type: ignore[index]
        batch = EastmoneyTradingEventOperation(
            FixedTransport([inline_response(payload)]),
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        self.assertTrue(batch.complete)
        self.assertIsNone(batch.observations[0].metrics["turnover_rate"])
        self.assertIn("source_value_missing", batch.observations[0].limitations)

    def test_lockup_keeps_history_and_only_the_next_ninety_days(self) -> None:
        transport = FixedTransport([response("lockup.json")])

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "lockup",
                observed_from="2026-07-01",
                observed_to="2026-10-31",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            batch.mode_acquisitions["lockup"].state,
            "observed_nonempty",
        )
        self.assertEqual(
            [item.observed_on for item in batch.observations],
            ["2026-07-15", "2026-08-20"],
        )
        self.assert_time_contract(batch.observations)
        history, upcoming = batch.observations
        self.assertEqual(history.dimensions["event_phase"], "history")
        self.assertEqual(upcoming.dimensions["event_phase"], "upcoming_90_days")
        self.assertEqual(
            upcoming.metrics,
            {
                "released_shares": "5000",
                "tradable_shares": "4800",
                "total_share_ratio": "0.12",
            },
        )
        self.assertEqual(
            upcoming.units,
            {
                "released_shares": "10k_shares",
                "tradable_shares": "10k_shares",
                "total_share_ratio": "ratio",
            },
        )
        self.assertEqual(
            upcoming.directions,
            {
                "released_shares": "positive_is_more_shares_released",
                "tradable_shares": "positive_is_more_tradable_shares",
                "total_share_ratio": "positive_is_larger_share_base_fraction",
            },
        )
        self.assertEqual(
            upcoming.dimensions["lockup_type"],
            "定向增发机构配售股份",
        )
        request_query = parse_qs(urlsplit(transport.calls[0][0]).query)
        self.assertEqual(request_query["reportName"], ["RPT_LIFT_STAGE"])
        self.assertIn("FREE_DATE>='2026-07-01'", request_query["filter"][0])
        self.assertIn("FREE_DATE<='2026-10-31'", request_query["filter"][0])

    def test_lockup_accepts_a_future_only_window_through_the_ninetieth_day(
        self,
    ) -> None:
        payload = fixture_payload("lockup.json")
        upcoming = payload["result"]["data"][1]  # type: ignore[index]
        payload["result"]["data"] = [upcoming]  # type: ignore[index]
        payload["result"]["count"] = 1  # type: ignore[index]
        transport = FixedTransport([inline_response(payload)])

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(
            query(
                "lockup",
                observed_from="2026-08-03",
                observed_to="2026-10-31",
            )
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            batch.mode_acquisitions["lockup"].state,
            "observed_nonempty",
        )
        self.assertEqual(
            [item.observed_on for item in batch.observations],
            ["2026-08-20"],
        )
        self.assertEqual(
            batch.observations[0].dimensions["event_phase"],
            "upcoming_90_days",
        )
        request_query = parse_qs(urlsplit(transport.calls[0][0]).query)
        self.assertIn("FREE_DATE>='2026-08-03'", request_query["filter"][0])
        self.assertIn("FREE_DATE<='2026-10-31'", request_query["filter"][0])

    def test_block_trade_keeps_counterparties_and_computed_premium(self) -> None:
        transport = FixedTransport([response("block_trade.json")])

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(query("block_trade"))

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            batch.mode_acquisitions["block_trade"].state,
            "observed_nonempty",
        )
        self.assertEqual(len(batch.observations), 2)
        self.assert_time_contract(batch.observations)
        first = batch.observations[0]
        self.assertEqual(first.data_type, "block_trade")
        self.assertEqual(first.observed_on, "2026-08-01")
        self.assertEqual(
            first.metrics,
            {
                "deal_price": "10.5",
                "close_price": "10",
                "deal_volume": "200",
                "deal_amount": "2100",
                "premium_rate": "5",
            },
        )
        self.assertEqual(
            first.units,
            {
                "deal_price": "CNY/share",
                "close_price": "CNY/share",
                "deal_volume": "share",
                "deal_amount": "CNY",
                "premium_rate": "percent",
            },
        )
        self.assertEqual(
            Decimal(first.metrics["deal_price"])
            * Decimal(first.metrics["deal_volume"]),
            Decimal(first.metrics["deal_amount"]),
        )
        self.assertEqual(
            first.directions,
            {
                "deal_price": "not_directional",
                "close_price": "not_directional",
                "deal_volume": "positive_is_more_volume",
                "deal_amount": "positive_is_more_value",
                "premium_rate": "positive_is_premium_negative_is_discount",
            },
        )
        self.assertEqual(
            first.dimensions,
            {
                "buyer_department": "买方营业部",
                "seller_department": "卖方营业部",
                "provider_raw_units": {
                    "DEAL_VOLUME": "share",
                    "DEAL_AMT": "CNY",
                },
                "provider_display_scale_power_of_ten": "-4",
                "unit_definition_uri": (
                    "https://data.eastmoney.com/dzjy/detail/300058.html"
                ),
            },
        )
        self.assertEqual(
            first.period,
            {"start": "2026-08-01", "end": "2026-08-01", "frequency": "event"},
        )
        self.assertIn("availability_time_unknown", first.limitations)
        request_query = parse_qs(urlsplit(transport.calls[0][0]).query)
        self.assertEqual(request_query["reportName"], ["RPT_DATA_BLOCKTRADE"])

    def test_invalid_subjects_and_windows_fail_before_network(self) -> None:
        canonical_subject = query("dragon_tiger").subject
        assert canonical_subject is not None
        cases = (
            (
                query(
                    "dragon_tiger",
                    subject={
                        "security": {
                            "exchange": "SSE",
                            "code": "300058",
                            "type": "A_SHARE",
                        }
                    },
                ),
                "invalid_subject",
            ),
            (
                query("market_dragon_tiger", subject=canonical_subject),
                "invalid_subject",
            ),
            (
                query(
                    "market_dragon_tiger",
                    observed_from="2026-08-01",
                    observed_to="2026-08-02",
                ),
                "invalid_window",
            ),
            (
                query(
                    "lockup",
                    observed_from="2026-07-01",
                    observed_to="2026-11-01",
                ),
                "future_window",
            ),
            (
                query(
                    "lockup",
                    observed_from="2026-08-20",
                    observed_to="2026-08-19",
                ),
                "invalid_window",
            ),
            (
                query("block_trade", observed_from="2026-7-01"),
                "invalid_date",
            ),
        )
        for research_query, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                transport = FixedTransport([])

                batch = EastmoneyTradingEventOperation(
                    transport,
                    request_gate=no_wait_gate(),
                ).collect(research_query)

                self.assertFalse(batch.complete)
                self.assertEqual(batch.observations, ())
                self.assertEqual(batch.source_errors[0].code, expected_code)
                self.assertEqual(transport.calls, [])

    def test_security_date_schema_and_empty_payloads_fail_closed(self) -> None:
        wrong_security = fixture_payload("dragon_records.json")
        wrong_security["result"]["data"][0]["SECURITY_CODE"] = "000001"  # type: ignore[index]
        wrong_date = fixture_payload("dragon_records.json")
        wrong_date["result"]["data"][0]["TRADE_DATE"] = "2026-08-03 00:00:00"  # type: ignore[index]
        missing_count = fixture_payload("dragon_records.json")
        del missing_count["result"]["count"]  # type: ignore[index]
        empty = {
            "success": True,
            "code": 0,
            "result": {"pages": 1, "count": 0, "data": []},
        }
        provider_empty = {
            "success": False,
            "code": 9201,
            "message": "返回数据为空",
            "result": None,
        }
        cases = (
            (wrong_security, "wrong_security_payload"),
            (wrong_date, "wrong_date_payload"),
            (missing_count, "unknown_schema"),
            (empty, None),
            (provider_empty, "empty_response"),
        )
        for payload, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                batch = EastmoneyTradingEventOperation(
                    FixedTransport([inline_response(payload)]),
                    request_gate=no_wait_gate(),
                ).collect(query("dragon_tiger"))

                self.assertEqual(batch.observations, ())
                if expected_code is None:
                    self.assertTrue(batch.complete)
                    self.assertEqual(batch.source_errors, ())
                else:
                    self.assertFalse(batch.complete)
                    self.assertEqual(batch.source_errors[0].code, expected_code)

    def test_dragon_tiger_seat_security_and_date_must_match_latest_record(
        self,
    ) -> None:
        wrong_seat = fixture_payload("dragon_buy_seats.json")
        wrong_seat["result"]["data"][0]["SECURITY_CODE"] = "000001"  # type: ignore[index]
        transport = FixedTransport(
            [
                response("dragon_records.json"),
                inline_response(wrong_seat),
                response("dragon_sell_seats.json"),
            ]
        )

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(query("dragon_tiger"))

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "wrong_security_payload")

    def test_bounded_pagination_fails_when_source_has_more_pages(self) -> None:
        batch = EastmoneyTradingEventOperation(
            FixedTransport([response("market_dragon_page_1.json")]),
            request_gate=no_wait_gate(),
            max_pages=1,
        ).collect(
            query(
                "market_dragon_tiger",
                observed_from="2026-08-01",
                observed_to="2026-08-01",
            )
        )

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(
            batch.mode_acquisitions["market_dragon_tiger"].state,
            "partial",
        )
        self.assertEqual(batch.source_errors[0].code, "pagination_incomplete")

    def test_terminal_429_keeps_structured_backoff_diagnostic(self) -> None:
        delays: list[float] = []
        gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(0.5,),
            sleeper=delays.append,
        )
        transport = FixedTransport(
            [inline_response({}, status=429), inline_response({}, status=429)]
        )

        batch = EastmoneyTradingEventOperation(
            transport,
            request_gate=gate,
        ).collect(query("dragon_tiger"))

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "rate_limited")
        self.assertEqual(
            [item.code for item in batch.degradations], ["rate_limit_backoff"]
        )
        self.assertEqual(
            batch.degradations[0].details, {"delay_seconds": "0.500", "attempt": "1"}
        )
        self.assertEqual(delays, [0.5])

    def test_block_trade_missing_counterparty_is_not_treated_as_no_trade(self) -> None:
        payload = fixture_payload("block_trade.json")
        payload["result"]["data"][0]["BUYER_NAME"] = ""  # type: ignore[index]

        batch = EastmoneyTradingEventOperation(
            FixedTransport([inline_response(payload)]),
            request_gate=no_wait_gate(),
        ).collect(query("block_trade"))

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "unknown_schema")


if __name__ == "__main__":
    unittest.main()
