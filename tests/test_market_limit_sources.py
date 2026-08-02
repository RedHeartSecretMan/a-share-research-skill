from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse, TransportError  # noqa: E402
from a_share_research.market_limit_sources import (  # noqa: E402
    EastmoneyLimitStateOperation,
    ThsLimitReasonOperation,
)
from a_share_research.market_signal_contract import MarketSignalQuery  # noqa: E402
from a_share_research.source_throttle import RequestGateDiagnostic  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 10, 30, tzinfo=CHINA_STANDARD_TIME)
T = TypeVar("T")


class FixedTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.get_calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.get_calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("limit-state operations use GET")


class DiagnosticGate:
    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), (RequestGateDiagnostic("source_request_paced", 1.2),)


def response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def query(*, states: list[str] | None = None) -> MarketSignalQuery:
    parameters: dict[str, object] = {}
    if states is not None:
        parameters["limit_states"] = states
    return MarketSignalQuery(
        signal_types=("limit_state",),
        as_of="2026-08-02",
        observed_from="2026-07-31",
        observed_to="2026-07-31",
        limit=20,
        subject=None,
        parameters=parameters,
    )


def eastmoney_row() -> dict[str, object]:
    return {
        "c": "300058",
        "n": "蓝色光标",
        "p": 12340,
        "zdp": "10.01",
        "amount": "200000000.12",
        "ltsz": "9000000000",
        "hs": "7.80",
        "lbc": 2,
        "fbt": 92500,
        "lbt": 145959,
        "fund": "123456789",
        "zbc": 1,
        "hybk": "文化传媒",
        "zttj": {"days": 3, "ct": 2},
    }


class EastmoneyLimitStateTests(unittest.TestCase):
    def test_normalizes_limit_up_with_explicit_units_date_and_completeness(
        self,
    ) -> None:
        transport = FixedTransport(
            [response({"data": {"tc": 1, "pool": [eastmoney_row()]}})]
        )

        batch = EastmoneyLimitStateOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query(states=["limit_up"]))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        self.assertEqual(batch.coverage["limit_state"].state, "observed_nonempty")
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2026-07-31")
        self.assertEqual(item.dimensions["pool_state"], "limit_up")
        self.assertEqual(item.metrics["price"], "12.34")
        self.assertEqual(item.units["seal_fund"], "CNY")
        self.assertEqual(item.dimensions["first_seal_time"], "09:25:00+08:00")
        params = parse_qs(urlsplit(transport.get_calls[0]).query)
        self.assertEqual(params["date"], ["20260731"])

    def test_business_complete_zero_is_observed_empty(self) -> None:
        transport = FixedTransport([response({"data": {"tc": 0, "pool": []}})])

        batch = EastmoneyLimitStateOperation(transport).collect(
            query(states=["limit_down"])
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.coverage["limit_state"].state, "observed_empty")

    def test_null_data_is_not_misreported_as_empty_pool(self) -> None:
        batch = EastmoneyLimitStateOperation(
            FixedTransport([response({"data": None})])
        ).collect(query(states=["limit_up"]))

        self.assertEqual(batch.coverage["limit_state"].state, "indeterminate")
        self.assertEqual(batch.source_errors[0].code, "non_trading_day_or_invalid_date")

    def test_provider_total_mismatch_fails_closed(self) -> None:
        batch = EastmoneyLimitStateOperation(
            FixedTransport([response({"data": {"tc": 2, "pool": [eastmoney_row()]}})])
        ).collect(query(states=["limit_up"]))

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.coverage["limit_state"].state, "indeterminate")
        self.assertEqual(batch.source_errors[0].code, "pagination_incomplete")

    def test_transport_failure_is_distinct_from_empty(self) -> None:
        batch = EastmoneyLimitStateOperation(
            FixedTransport([TransportError("upstream_unavailable", "offline")])
        ).collect(query(states=["limit_up"]))

        self.assertEqual(batch.source_errors[0].code, "upstream_unavailable")
        self.assertEqual(batch.coverage["limit_state"].state, "indeterminate")

    def test_duplicate_security_is_deduplicated_or_conflicted(self) -> None:
        row = eastmoney_row()
        exact = EastmoneyLimitStateOperation(
            FixedTransport([response({"data": {"tc": 2, "pool": [row, row]}})]),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))
        changed = {**row, "p": 13000}
        conflict = EastmoneyLimitStateOperation(
            FixedTransport([response({"data": {"tc": 2, "pool": [row, changed]}})]),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))

        self.assertEqual(len(exact.observations), 1)
        self.assertIn("exact_duplicate_rows_removed", exact.limitations)
        self.assertEqual(conflict.observations, ())
        self.assertEqual(conflict.source_errors[0].code, "duplicate_source_conflict")


class ThsLimitReasonTests(unittest.TestCase):
    def test_only_limit_down_is_explicitly_not_applicable(self) -> None:
        operation = ThsLimitReasonOperation(
            FixedTransport([]), request_gate=DiagnosticGate()
        )

        self.assertFalse(operation.is_applicable(query(states=["limit_down"])))
        self.assertTrue(operation.is_applicable(query(states=["limit_up"])))

    def test_editorial_reason_is_attributed_and_timestamp_is_china_time(self) -> None:
        timestamp = int(
            datetime(2026, 7, 31, 9, 30, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        batch = ThsLimitReasonOperation(
            FixedTransport(
                [
                    response(
                        {
                            "status_code": 0,
                            "data": {
                                "total": 1,
                                "info": [
                                    {
                                        "code": "300058",
                                        "name": "蓝色光标",
                                        "latest": "12.34",
                                        "change_rate": "10.01",
                                        "reason_type": "AI营销+算力",
                                        "limit_up_type": "换手板",
                                        "limit_up_suc_rate": "0.8",
                                        "open_num": 1,
                                        "order_amount": "10000000",
                                        "high_days": "3天2板",
                                        "first_limit_up_time": timestamp,
                                        "is_again_limit": 1,
                                    }
                                ],
                            },
                        }
                    )
                ]
            ),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        item = batch.observations[0]
        self.assertEqual(item.attributions[0].provenance, "editorial_annotation")
        self.assertEqual(item.attributions[0].text, "AI营销+算力")
        self.assertEqual(item.dimensions["first_seal_time"], "09:30:00+08:00")

    def test_provider_error_and_schema_empty_are_not_legal_empty_pool(self) -> None:
        provider_error = ThsLimitReasonOperation(
            FixedTransport([response({"status_code": 1001, "msg": "denied"})]),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))
        missing_info = ThsLimitReasonOperation(
            FixedTransport([response({"status_code": 0, "data": {}})]),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))

        self.assertEqual(provider_error.source_errors[0].code, "provider_error")
        self.assertEqual(missing_info.source_errors[0].code, "unknown_schema")

    def test_total_mismatch_is_partial_and_timestamp_must_match_trading_day(
        self,
    ) -> None:
        timestamp = int(
            datetime(2026, 7, 30, 9, 30, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        row = {
            "code": "300058",
            "name": "蓝色光标",
            "latest": "12.34",
            "change_rate": "10.01",
            "reason_type": "AI营销+算力",
            "first_limit_up_time": timestamp,
        }
        wrong_time = ThsLimitReasonOperation(
            FixedTransport(
                [response({"status_code": 0, "data": {"total": 1, "info": [row]}})]
            ),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))
        row["first_limit_up_time"] = int(
            datetime(2026, 7, 31, 9, 30, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        incomplete = ThsLimitReasonOperation(
            FixedTransport(
                [response({"status_code": 0, "data": {"total": 2, "info": [row]}})]
            ),
            request_gate=DiagnosticGate(),
        ).collect(query(states=["limit_up"]))

        self.assertEqual(wrong_time.source_errors[0].code, "unknown_schema")
        self.assertEqual(incomplete.coverage["limit_state"].state, "partial")
        self.assertIn("pagination_incomplete", incomplete.limitations)


if __name__ == "__main__":
    unittest.main()
