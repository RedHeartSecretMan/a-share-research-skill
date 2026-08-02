from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_monitoring_sources import (  # noqa: E402
    EastmoneyFocusMonitoringOperation,
    EastmoneySevereAbnormalMovementOperation,
)
from a_share_research.market_signal_contract import MarketSignalQuery  # noqa: E402
from a_share_research.market_signals import build_market_signals_result  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 23, 10, tzinfo=CHINA_STANDARD_TIME)
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
        raise AssertionError("market-monitoring sources must use GET")


class ImmediateGate:
    def run(self, request: Callable[[], T]) -> tuple[T, tuple[Any, ...]]:
        return request(), ()


class NoIdentityTransport:
    def get(self, url: str, headers: dict[str, str]) -> Any:
        raise AssertionError(f"market-wide task must not resolve identity: {url}")


def response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def query(
    signal_type: str,
    *,
    day: str = "2026-08-02",
    as_of: str | None = None,
) -> MarketSignalQuery:
    return MarketSignalQuery(
        signal_types=(signal_type,),
        as_of=as_of or day,
        observed_from=day,
        observed_to=day,
        limit=20,
        subject=None,
        parameters={},
    )


def anomaly_payload(
    rows: list[dict[str, object]],
    *,
    pages: int,
    trade_date: int = 20260731,
    result: int = 0,
) -> dict[str, object]:
    return {
        "result": result,
        "msg": "" if result == 0 else "provider rejected request",
        "pages": pages,
        "date": trade_date,
        "open": 0,
        "count": len(rows),
        "data": rows,
    }


def anomaly_row(
    code: str,
    *,
    board: int = 4,
    rule_code: int = 4,
    deviation: float = 70.11,
) -> dict[str, object]:
    return {
        "m": 0,
        "c": code,
        "n": f"证券{code}",
        "s": board,
        "e": rule_code,
        "x": deviation,
        "d": 9,
        "t": 17.59,
        "a": 5.03,
        "o": 2,
    }


def public_request(
    signal_type: str,
    *,
    day: str,
    as_of: str = "2026-08-02",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "market_signals",
        "subjects": [],
        "as_of": as_of,
        "window": {"observed_from": day, "observed_to": day},
        "parameters": {"signal_types": [signal_type], "limit": 20},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


class FocusMonitoringSourceTests(unittest.TestCase):
    def test_current_snapshot_preserves_active_and_scheduled_windows(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                response(
                    [
                        {
                            "MARKET": "1",
                            "STKCODE": "605255",
                            "STKNAME": "天普股份",
                            "VALIDATESTARTDATE": "2026-07-30",
                            "VALIDATEENDDATE": "2026-08-12",
                            "LINK_URL": "https://wap.18.cn/app/detail/818/3265",
                        },
                        {
                            "MARKET": "B",
                            "STKCODE": "920575",
                            "STKNAME": "*ST康乐",
                            "VALIDATESTARTDATE": "2026-08-03",
                            "VALIDATEENDDATE": "2026-08-07",
                            "LINK_URL": "",
                        },
                    ]
                )
            ]
        )

        batch = EastmoneyFocusMonitoringOperation(
            transport, request_gate=ImmediateGate()
        ).collect(query("focus_monitoring"))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.coverage["focus_monitoring"].state, "observed_nonempty")
        self.assertEqual(batch.coverage["focus_monitoring"].provider_total, 2)
        self.assertEqual(len(batch.observations), 2)
        active, scheduled = batch.observations
        self.assertIsNone(active.subject)
        self.assertEqual(active.dimensions["provider_market_code"], "1")
        self.assertEqual(active.dimensions["provider_security_code"], "605255")
        self.assertIsNone(active.dimensions["provider_security_type"])
        self.assertEqual(active.dimensions["monitoring_state"], "active")
        self.assertEqual(scheduled.dimensions["provider_market_code"], "B")
        self.assertEqual(scheduled.dimensions["monitoring_state"], "scheduled")
        self.assertIsNone(scheduled.dimensions["provider_detail_url"])
        self.assertEqual(
            scheduled.period,
            {
                "start": "2026-08-03",
                "end": "2026-08-07",
                "frequency": "calendar_date_window",
            },
        )
        self.assertIn("provider_watchlist_not_official", active.limitations)
        self.assertIn("security_exchange_unverified", active.limitations)
        self.assertIn("security_type_unverified", active.limitations)

    def test_exact_duplicates_are_removed_before_limit_is_applied(self) -> None:
        first = {
            "MARKET": "1",
            "STKCODE": "605255",
            "STKNAME": "天普股份",
            "VALIDATESTARTDATE": "2026-07-30",
            "VALIDATEENDDATE": "2026-08-12",
        }
        second = {
            "MARKET": "0",
            "STKCODE": "300058",
            "STKNAME": "蓝色光标",
            "VALIDATESTARTDATE": "2026-07-31",
            "VALIDATEENDDATE": "2026-08-13",
        }
        operation = EastmoneyFocusMonitoringOperation(
            FixedTransport([response([first, first, second])]),
            request_gate=ImmediateGate(),
        )

        batch = operation.collect(replace(query("focus_monitoring"), limit=2))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.dimensions["provider_security_code"] for item in batch.observations],
            ["605255", "300058"],
        )
        self.assertEqual(batch.coverage["focus_monitoring"].provider_total, 2)
        self.assertIn("exact_duplicate_rows_removed", batch.limitations)

    def test_duplicate_identity_with_conflicting_content_fails_closed(self) -> None:
        first = {
            "MARKET": "1",
            "STKCODE": "605255",
            "STKNAME": "天普股份",
            "VALIDATESTARTDATE": "2026-07-30",
            "VALIDATEENDDATE": "2026-08-12",
        }
        conflicting = {**first, "STKNAME": "冲突名称"}
        operation = EastmoneyFocusMonitoringOperation(
            FixedTransport([response([first, conflicting])]),
            request_gate=ImmediateGate(),
        )

        batch = operation.collect(query("focus_monitoring"))

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.coverage["focus_monitoring"].state, "indeterminate")
        self.assertEqual(batch.source_errors[0].code, "duplicate_source_conflict")

    def test_empty_static_monitor_payload_is_not_treated_as_an_empty_pool(self) -> None:
        batch = EastmoneyFocusMonitoringOperation(
            FixedTransport([response([])]), request_gate=ImmediateGate()
        ).collect(query("focus_monitoring"))

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.coverage["focus_monitoring"].state, "indeterminate")
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["empty_response_unverified"],
        )

    def test_current_static_snapshot_cannot_answer_a_historical_request(self) -> None:
        transport = FixedTransport(
            [
                response(
                    [
                        {
                            "MARKET": "1",
                            "STKCODE": "605255",
                            "STKNAME": "天普股份",
                            "VALIDATESTARTDATE": "2026-07-30",
                            "VALIDATEENDDATE": "2026-08-12",
                        }
                    ]
                )
            ]
        )

        batch = EastmoneyFocusMonitoringOperation(
            transport, request_gate=ImmediateGate()
        ).collect(query("focus_monitoring", day="2026-07-31"))

        self.assertEqual(batch.observations, ())
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["historical_snapshot_unavailable"],
        )

    def test_public_coordinator_retains_provider_watchlist_observation(self) -> None:
        operation = EastmoneyFocusMonitoringOperation(
            FixedTransport(
                [
                    response(
                        [
                            {
                                "MARKET": "1",
                                "STKCODE": "605255",
                                "STKNAME": "天普股份",
                                "VALIDATESTARTDATE": "2026-07-30",
                                "VALIDATEENDDATE": "2026-08-12",
                            }
                        ]
                    )
                ]
            ),
            request_gate=ImmediateGate(),
        )

        result = build_market_signals_result(
            public_request("focus_monitoring", day="2026-08-02"),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertNotIn(
            "unknown_schema", [item["code"] for item in result["source_errors"]]
        )
        self.assertEqual(
            result["observations"][0]["metrics"]["watchlist_membership"], "1"
        )


class SevereAbnormalMovementSourceTests(unittest.TestCase):
    def test_pagination_deduplicates_and_preserves_raw_rule_codes(self) -> None:
        first = anomaly_row("688001", board=6, rule_code=4)
        second = anomaly_row("300688")
        third = anomaly_row("603221", board=1, rule_code=1)
        transport = FixedTransport(
            [
                response(anomaly_payload([first, second], pages=3)),
                response(anomaly_payload([first, third], pages=3)),
                response(anomaly_payload([], pages=3)),
            ]
        )

        batch = EastmoneySevereAbnormalMovementOperation(
            transport,
            page_size=2,
            request_gate=ImmediateGate(),
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 3)
        self.assertEqual(
            [
                parse_qs(urlsplit(url).query)["pageNo"][0]
                for url, _headers in transport.get_calls
            ],
            ["1", "2", "3"],
        )
        coverage = batch.coverage["severe_abnormal_movement"]
        self.assertEqual(coverage.state, "observed_nonempty")
        self.assertIsNone(coverage.provider_total)
        self.assertEqual(coverage.pages_collected, 3)
        self.assertEqual(coverage.pages_expected, 3)
        star = batch.observations[0]
        self.assertEqual(star.observed_on, "2026-07-31")
        self.assertEqual(star.dimensions["provider_market_code"], "0")
        self.assertEqual(star.dimensions["provider_board_code"], "6")
        self.assertEqual(star.dimensions["provider_market_open_code"], "0")
        self.assertEqual(
            star.rule,
            {"scheme": "eastmoney_price_anomaly.e", "code": "4"},
        )
        self.assertIn("provider_rule_semantics_unverified", star.limitations)
        self.assertNotIn("150", json.dumps(star.rule))

    def test_short_page_before_declared_end_fails_closed(self) -> None:
        transport = FixedTransport(
            [
                response(
                    anomaly_payload(
                        [anomaly_row("300001"), anomaly_row("300002")], pages=4
                    )
                ),
                response(anomaly_payload([anomaly_row("300003")], pages=4)),
            ]
        )

        batch = EastmoneySevereAbnormalMovementOperation(
            transport, page_size=2, request_gate=ImmediateGate()
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.source_errors[0].code, "pagination_incomplete")
        self.assertEqual(len(transport.get_calls), 2)
        self.assertEqual(batch.observations, ())
        coverage = batch.coverage["severe_abnormal_movement"]
        self.assertEqual(coverage.state, "indeterminate")

    def test_early_empty_page_is_incomplete_not_a_sentinel(self) -> None:
        batch = EastmoneySevereAbnormalMovementOperation(
            FixedTransport(
                [
                    response(
                        anomaly_payload(
                            [anomaly_row("300001"), anomaly_row("300002")],
                            pages=3,
                        )
                    ),
                    response(anomaly_payload([], pages=3)),
                ]
            ),
            page_size=2,
            request_gate=ImmediateGate(),
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "pagination_incomplete")

    def test_successful_empty_page_is_an_explicit_provider_empty_pool(self) -> None:
        batch = EastmoneySevereAbnormalMovementOperation(
            FixedTransport([response(anomaly_payload([], pages=1))]),
            page_size=2,
            request_gate=ImmediateGate(),
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors, ())
        coverage = batch.coverage["severe_abnormal_movement"]
        self.assertEqual(coverage.state, "observed_empty")
        self.assertEqual(coverage.provider_total, 0)
        self.assertEqual(coverage.pages_collected, 1)
        self.assertIn("provider_empty_not_market_absence", batch.limitations)

    def test_nonzero_provider_result_is_a_source_error_not_an_empty_pool(self) -> None:
        batch = EastmoneySevereAbnormalMovementOperation(
            FixedTransport([response(anomaly_payload([], pages=1, result=1001))]),
            page_size=2,
            request_gate=ImmediateGate(),
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(
            batch.coverage["severe_abnormal_movement"].state,
            "indeterminate",
        )
        self.assertEqual(batch.source_errors[0].code, "provider_error")
        self.assertEqual(batch.source_errors[0].details["provider_result"], 1001)

    def test_duplicate_identity_with_different_metrics_fails_closed(self) -> None:
        transport = FixedTransport(
            [
                response(anomaly_payload([anomaly_row("300688")], pages=2)),
                response(
                    anomaly_payload([anomaly_row("300688", deviation=71.25)], pages=2)
                ),
            ]
        )

        batch = EastmoneySevereAbnormalMovementOperation(
            transport, page_size=1, request_gate=ImmediateGate()
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(
            batch.coverage["severe_abnormal_movement"].state,
            "indeterminate",
        )
        self.assertEqual(batch.source_errors[0].code, "duplicate_conflict")

    def test_response_trade_date_must_remain_fixed_across_pages(self) -> None:
        transport = FixedTransport(
            [
                response(anomaly_payload([anomaly_row("300001")], pages=2)),
                response(
                    anomaly_payload(
                        [anomaly_row("300002")],
                        pages=2,
                        trade_date=20260801,
                    )
                ),
            ]
        )

        batch = EastmoneySevereAbnormalMovementOperation(
            transport, page_size=1, request_gate=ImmediateGate()
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "response_date_changed")

    def test_public_coordinator_retains_raw_provider_anomaly_rule(self) -> None:
        operation = EastmoneySevereAbnormalMovementOperation(
            FixedTransport(
                [
                    response(
                        anomaly_payload(
                            [anomaly_row("688001", board=6, rule_code=4)],
                            pages=1,
                        )
                    )
                ]
            ),
            page_size=2,
            request_gate=ImmediateGate(),
        )

        result = build_market_signals_result(
            public_request("severe_abnormal_movement", day="2026-07-31"),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        item = result["observations"][0]
        self.assertEqual(
            item["period"],
            {
                "start": "2026-07-31",
                "end": "2026-07-31",
                "frequency": "trading_day",
            },
        )
        self.assertEqual(
            item["rule"],
            {"scheme": "eastmoney_price_anomaly.e", "code": "4"},
        )
        self.assertEqual(item["dimensions"]["statistics_trading_days"], "9")

    def test_latest_anomaly_pool_cannot_substitute_for_a_historical_date(self) -> None:
        batch = EastmoneySevereAbnormalMovementOperation(
            FixedTransport(
                [response(anomaly_payload([anomaly_row("300688")], pages=1))]
            ),
            page_size=2,
            request_gate=ImmediateGate(),
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-30",
                as_of="2026-08-02",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "historical_snapshot_unavailable")

    def test_later_retrieval_cannot_backfill_historical_trade_date(self) -> None:
        batch = EastmoneySevereAbnormalMovementOperation(
            FixedTransport(
                [response(anomaly_payload([anomaly_row("300688")], pages=1))]
            ),
            page_size=2,
            request_gate=ImmediateGate(),
        ).collect(
            query(
                "severe_abnormal_movement",
                day="2026-07-31",
                as_of="2026-07-31",
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "historical_snapshot_unavailable")


if __name__ == "__main__":
    unittest.main()
