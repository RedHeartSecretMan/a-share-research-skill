from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capital_contract import CapitalQuery  # noqa: E402
from a_share_research.capital_flow_sources import (  # noqa: E402
    EastmoneyBoardFundFlowOperation,
    EastmoneyNorthboundFlowOperation,
    EastmoneyStockFundFlowOperation,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateDiagnostic,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 20, 45, tzinfo=CHINA_STANDARD_TIME)
T = TypeVar("T")


class FixedTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.get_calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.get_calls.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("capital-flow sources must use GET")


class DiagnosticGate:
    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), (RequestGateDiagnostic("source_request_paced", 1.125),)


def response(
    payload: object, *, content_type: str = "application/json"
) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def raw_response(body: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=body.encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def subject(*, exchange: str = "SSE", code: str = "601138") -> dict[str, object]:
    return {
        "security": {"exchange": exchange, "code": code, "type": "A_SHARE"},
        "name": "工业富联",
    }


def query(
    data_type: str,
    *,
    observed_from: str,
    observed_to: str,
    limit: int = 10,
    query_subject: dict[str, object] | None = None,
    parameters: dict[str, object] | None = None,
) -> CapitalQuery:
    return CapitalQuery(
        data_types=(data_type,),
        as_of=observed_to,
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        subject=query_subject,
        parameters=dict(parameters or {}),
    )


def stock_payload(days: int, *, code: str = "601138") -> dict[str, object]:
    dates = [f"2026-07-{day:02d}" for day in range(21, 21 + days)]
    return {
        "data": {
            "code": code,
            "market": 1,
            "klines": [
                f"{day},{1000 + index},-200,300,400,600,1.5"
                for index, day in enumerate(dates)
            ],
        }
    }


class NorthboundFlowTests(unittest.TestCase):
    def test_post_august_2024_net_buy_request_fails_closed_without_http(self) -> None:
        transport = FixedTransport([])

        batch = EastmoneyNorthboundFlowOperation(transport).collect(
            query(
                "northbound_flow",
                observed_from="2024-08-19",
                observed_to="2024-08-19",
                query_subject=None,
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["northbound_net_buy_disclosure_unavailable"],
        )
        self.assertEqual(transport.get_calls, [])

    def test_pre_boundary_flow_preserves_unit_direction_and_missing_value(self) -> None:
        transport = FixedTransport(
            [
                response(
                    {
                        "success": True,
                        "result": {
                            "pages": 1,
                            "data": [
                                {
                                    "TRADE_DATE": "2024-08-16 00:00:00",
                                    "MUTUAL_TYPE": "001",
                                    "NET_DEAL_AMT": "12.34",
                                }
                            ],
                        },
                    }
                )
            ]
        )

        batch = EastmoneyNorthboundFlowOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "northbound_flow",
                observed_from="2024-08-16",
                observed_to="2024-08-16",
                query_subject=None,
            )
        )

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2024-08-16")
        self.assertEqual(item.metrics["net_buy_amount"], "12.34")
        self.assertEqual(item.units["net_buy_amount"], "CNY_100_MILLION")
        self.assertEqual(item.directions["net_buy_amount"], "positive_is_net_inflow")
        self.assertEqual(
            item.dimensions["market_scope"],
            "mainland_hong_kong_stock_connect_northbound",
        )

        missing_transport = FixedTransport(
            [
                response(
                    {
                        "success": True,
                        "result": {
                            "pages": 1,
                            "data": [
                                {
                                    "TRADE_DATE": "2024-08-16 00:00:00",
                                    "MUTUAL_TYPE": "001",
                                    "NET_DEAL_AMT": None,
                                }
                            ],
                        },
                    }
                )
            ]
        )
        missing = EastmoneyNorthboundFlowOperation(
            missing_transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "northbound_flow",
                observed_from="2024-08-16",
                observed_to="2024-08-16",
                query_subject=None,
            )
        )

        self.assertEqual(missing.observations, ())
        self.assertEqual(missing.source_errors[0].code, "unknown_schema")

    def test_datacenter_accepts_json_body_served_as_text_plain(self) -> None:
        transport = FixedTransport(
            [
                response(
                    {
                        "success": True,
                        "result": {
                            "pages": 1,
                            "data": [
                                {
                                    "TRADE_DATE": "2024-08-16 00:00:00",
                                    "MUTUAL_TYPE": "001",
                                    "NET_DEAL_AMT": "12.34",
                                }
                            ],
                        },
                    },
                    content_type="text/plain",
                )
            ]
        )

        batch = EastmoneyNorthboundFlowOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "northbound_flow",
                observed_from="2024-08-16",
                observed_to="2024-08-16",
                query_subject=None,
            )
        )

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)


class StockFundFlowTests(unittest.TestCase):
    def test_five_day_flow_has_canonical_identity_period_units_and_direction(
        self,
    ) -> None:
        transport = FixedTransport([response(stock_payload(5))])

        batch = EastmoneyStockFundFlowOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "stock_fund_flow",
                observed_from="2026-07-21",
                observed_to="2026-07-25",
                limit=5,
                query_subject=subject(),
                parameters={"period": "5d"},
            )
        )

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 5)
        item = batch.observations[0]
        self.assertEqual(
            [observation.observed_on for observation in batch.observations],
            [
                "2026-07-25",
                "2026-07-24",
                "2026-07-23",
                "2026-07-22",
                "2026-07-21",
            ],
        )
        self.assertEqual(
            item.period,
            {
                "start": "2026-07-21",
                "end": "2026-07-25",
                "frequency": "trading_day",
                "trading_days": "5",
            },
        )
        self.assertEqual(item.metrics["main_net_inflow"], "1004")
        self.assertEqual(item.units["main_net_inflow"], "CNY")
        self.assertEqual(item.directions["main_net_inflow"], "positive_is_net_inflow")
        params = parse_qs(urlsplit(transport.get_calls[0][0]).query)
        self.assertEqual(params["secid"], ["1.601138"])
        self.assertEqual(params["lmt"], ["5"])

    def test_ten_day_period_is_supported(self) -> None:
        transport = FixedTransport([response(stock_payload(10))])

        batch = EastmoneyStockFundFlowOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "stock_fund_flow",
                observed_from="2026-07-21",
                observed_to="2026-07-30",
                limit=10,
                query_subject=subject(),
                parameters={"period": "10d"},
            )
        )

        self.assertEqual(len(batch.observations), 10)
        self.assertEqual(batch.source_errors, ())

    def test_wrong_security_and_shifted_dates_fail_closed(self) -> None:
        wrong_transport = FixedTransport([])
        wrong = EastmoneyStockFundFlowOperation(wrong_transport).collect(
            query(
                "stock_fund_flow",
                observed_from="2026-07-21",
                observed_to="2026-07-25",
                limit=5,
                query_subject=subject(exchange="SSE", code="000001"),
                parameters={"period": "5d"},
            )
        )
        self.assertEqual(wrong.source_errors[0].code, "invalid_subject")
        self.assertEqual(wrong_transport.get_calls, [])

        shifted_payload = stock_payload(5)
        shifted_payload["data"]["klines"][0] = "2026-07-20,1,2,3,4,5,6"
        shifted = EastmoneyStockFundFlowOperation(
            FixedTransport([response(shifted_payload)]),
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "stock_fund_flow",
                observed_from="2026-07-21",
                observed_to="2026-07-25",
                limit=5,
                query_subject=subject(),
                parameters={"period": "5d"},
            )
        )
        self.assertEqual(shifted.source_errors[0].code, "date_mismatch")

    def test_final_rate_limit_keeps_backoff_diagnostic(self) -> None:
        sleeps: list[float] = []
        transport = FixedTransport(
            [
                TransportError("rate_limited", "sanitized rate limit"),
                TransportError("rate_limited", "sanitized rate limit"),
            ]
        )
        gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(0.5,),
            sleeper=sleeps.append,
            jitter=lambda lower, upper: lower,
        )

        batch = EastmoneyStockFundFlowOperation(transport, request_gate=gate).collect(
            query(
                "stock_fund_flow",
                observed_from="2026-07-21",
                observed_to="2026-07-25",
                limit=5,
                query_subject=subject(),
                parameters={"period": "5d"},
            )
        )

        self.assertEqual(batch.source_errors[0].code, "rate_limited")
        self.assertEqual(batch.degradations[0].code, "rate_limit_backoff")
        self.assertEqual(sleeps, [0.5])


class BoardFundFlowTests(unittest.TestCase):
    def test_decimal_metrics_do_not_pass_through_binary_float(self) -> None:
        transport = FixedTransport(
            [
                raw_response(
                    '{"data":{"total":1,"diff":[{"f12":"BK1","f14":"精度板块",'
                    '"f124":1785427200,"f62":0.123456789012345678901,'
                    '"f184":1.25,"f3":0.5}]}}'
                )
            ]
        )

        batch = EastmoneyBoardFundFlowOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "board_fund_flow",
                observed_from="2026-07-31",
                observed_to="2026-07-31",
                limit=1,
                query_subject=None,
                parameters={"board_type": "industry", "period": "today"},
            )
        )

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            batch.observations[0].metrics["main_net_inflow"],
            "0.123456789012345678901",
        )

    def test_board_types_map_to_explicit_current_snapshot_metrics(self) -> None:
        cases = (
            ("industry", "m:90+t:2"),
            ("concept", "m:90+t:3"),
            ("region", "m:90+t:1"),
        )
        for board_type, expected_fs in cases:
            with self.subTest(board_type=board_type):
                row = {
                    "f12": "BK0001",
                    "f14": "测试板块",
                    "f124": 1785427200,
                    "f62": "123456789",
                    "f184": "8.5",
                    "f3": "2.1",
                }
                transport = FixedTransport(
                    [response({"data": {"total": 1, "diff": [row]}})]
                )

                batch = EastmoneyBoardFundFlowOperation(
                    transport, request_gate=DiagnosticGate()
                ).collect(
                    query(
                        "board_fund_flow",
                        observed_from="2026-07-31",
                        observed_to="2026-07-31",
                        limit=1,
                        query_subject=None,
                        parameters={"board_type": board_type, "period": "today"},
                    )
                )

                self.assertEqual(batch.source_errors, ())
                item = batch.observations[0]
                self.assertEqual(item.metrics["main_net_inflow"], "123456789")
                self.assertEqual(item.units["main_net_inflow"], "CNY")
                self.assertEqual(item.dimensions["board_type"], board_type)
                self.assertEqual(
                    item.dimensions["market_scope"], "a_share_board_market"
                )
                self.assertIsNone(item.available_at)
                self.assertIn("availability_time_unknown", item.limitations)
                self.assertIn("session_completeness_unverified", item.limitations)
                self.assertIn("source_value_missing", item.limitations)
                self.assertEqual(item.observed_on, "2026-07-31")
                self.assertEqual(item.period["start"], "2026-07-31")
                self.assertEqual(item.period["end"], "2026-07-31")
                self.assertEqual(item.period["frequency"], "trading_day_snapshot")
                params = parse_qs(urlsplit(transport.get_calls[0][0]).query)
                self.assertEqual(params["fs"], [expected_fs])
                self.assertEqual(params["fid"], ["f62"])

    def test_rolling_board_flow_preserves_unknown_start_and_explicit_lookback(
        self,
    ) -> None:
        for period in ("5d", "10d"):
            with self.subTest(period=period):
                row = {
                    "f12": "BK1",
                    "f14": "测试板块",
                    "f124": 1785427200,
                    "f164": "100",
                    "f165": "1.5",
                    "f109": "0.5",
                    "f174": "200",
                    "f175": "2.5",
                    "f160": "1.5",
                }
                batch = EastmoneyBoardFundFlowOperation(
                    FixedTransport([response({"data": {"total": 1, "diff": [row]}})]),
                    request_gate=DiagnosticGate(),
                ).collect(
                    query(
                        "board_fund_flow",
                        observed_from="2026-07-01",
                        observed_to="2026-07-31",
                        limit=1,
                        query_subject=None,
                        parameters={"board_type": "industry", "period": period},
                    )
                )

                self.assertEqual(batch.source_errors, ())
                self.assertEqual(len(batch.observations), 1)
                item = batch.observations[0]
                self.assertEqual(
                    item.period,
                    {
                        "start": None,
                        "end": "2026-07-31",
                        "frequency": f"rolling_{period[:-1]}_trading_days",
                        "lookback_trading_days": period[:-1],
                    },
                )
                self.assertIn("period_start_not_exposed", item.limitations)
                self.assertIn("trading_day_alignment_unverified", item.limitations)
                self.assertIn("session_completeness_unverified", item.limitations)

    def test_board_flow_paginates_only_until_limit(self) -> None:
        def row(code: str) -> dict[str, object]:
            return {
                "f12": code,
                "f14": f"板块{code}",
                "f124": 1785427200,
                "f62": "100",
                "f184": "1.5",
                "f3": "0.5",
            }

        transport = FixedTransport(
            [
                response({"data": {"total": 3, "diff": [row("BK1"), row("BK2")]}}),
                response({"data": {"total": 3, "diff": [row("BK3")]}}),
            ]
        )

        batch = EastmoneyBoardFundFlowOperation(
            transport,
            page_size=2,
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "board_fund_flow",
                observed_from="2026-07-31",
                observed_to="2026-07-31",
                limit=3,
                query_subject=None,
                parameters={"board_type": "industry", "period": "today"},
            )
        )

        self.assertEqual(len(batch.observations), 3)
        self.assertEqual(len(transport.get_calls), 2)
        self.assertEqual(
            [parse_qs(urlsplit(url).query)["pn"][0] for url, _ in transport.get_calls],
            ["1", "2"],
        )

    def test_empty_or_invalid_board_schema_is_explicit(self) -> None:
        for payload, expected_code in (
            ({"data": {"total": 0, "diff": []}}, "empty_response"),
            (
                {"data": {"total": 1, "diff": [{"f12": "BK1", "f14": "板块"}]}},
                "unknown_schema",
            ),
        ):
            with self.subTest(expected_code=expected_code):
                batch = EastmoneyBoardFundFlowOperation(
                    FixedTransport([response(payload)]),
                    request_gate=DiagnosticGate(),
                ).collect(
                    query(
                        "board_fund_flow",
                        observed_from="2026-07-31",
                        observed_to="2026-07-31",
                        limit=1,
                        query_subject=None,
                        parameters={"board_type": "industry", "period": "today"},
                    )
                )
                self.assertEqual(batch.source_errors[0].code, expected_code)

    def test_board_flow_rejects_a_provider_date_mismatch(self) -> None:
        transport = FixedTransport(
            [
                response(
                    {
                        "data": {
                            "total": 1,
                            "diff": [
                                {
                                    "f12": "BK1",
                                    "f14": "测试板块",
                                    "f124": 1785340800,
                                    "f62": "100",
                                    "f184": "1.5",
                                    "f3": "0.5",
                                }
                            ],
                        }
                    }
                )
            ]
        )

        batch = EastmoneyBoardFundFlowOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "board_fund_flow",
                observed_from="2026-07-31",
                observed_to="2026-07-31",
                limit=1,
                query_subject=None,
                parameters={"board_type": "industry", "period": "today"},
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "date_mismatch")


if __name__ == "__main__":
    unittest.main()
