from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.intraday_replay_contract import (  # noqa: E402
    IntradayReplayQuery,
    IntradayReplaySourceError,
)
from a_share_research.intraday_replay_registry import (  # noqa: E402
    ExchangeReplayDailyBoundaryOperation,
)

CHINA = timezone(timedelta(hours=8))


class _Transport:
    def __init__(self, payload: object, content_type: str = "application/json") -> None:
        self._body = json.dumps(payload).encode()
        self._content_type = content_type

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        del url, headers
        return HttpResponse(
            status=200,
            content_type=self._content_type,
            body=self._body,
            retrieved_at=datetime(2026, 8, 4, 16, 0, tzinfo=CHINA),
        )


def _query(security: str, exchange: str, code: str) -> IntradayReplayQuery:
    return IntradayReplayQuery(
        security=security,
        exchange=exchange,
        code=code,
        as_of=date(2026, 8, 4),
        replay_date=date(2026, 8, 3),
        research_boundary=datetime(2026, 8, 4, 23, 59, tzinfo=CHINA),
        retrieved_at=datetime(2026, 8, 4, 16, 0, tzinfo=CHINA),
    )


class ExchangeReplayDailyBoundaryTests(unittest.TestCase):
    def test_szse_daily_amount_and_volume_are_independent_boundary_operands(
        self,
    ) -> None:
        payload = {
            "code": "0",
            "data": {
                "code": "000001",
                "picupdata": [
                    [
                        "2026-08-02",
                        "10.00",
                        "10.10",
                        "9.90",
                        "10.20",
                        "0.10",
                        "1.0",
                        10,
                        1000.00,
                    ],
                    [
                        "2026-08-03",
                        "10.10",
                        "10.20",
                        "10.00",
                        "10.30",
                        "0.10",
                        "1.0",
                        20,
                        2500.00,
                    ],
                ],
            },
        }

        batch = ExchangeReplayDailyBoundaryOperation(_Transport(payload)).collect(
            _query("SZSE:000001", "SZSE", "000001")
        )

        self.assertEqual(batch.operation_id, "exchange_intraday_replay_daily@1")
        self.assertEqual(batch.amount, "2500.00")
        self.assertEqual(batch.volume, "2000")
        self.assertEqual(batch.previous_close, "10.10")
        self.assertEqual(batch.previous_close_basis, "actual_unadjusted")

    def test_sse_daily_amount_is_optional_at_old_schema_and_blocks_the_cross_check(
        self,
    ) -> None:
        payload = {
            "code": "600519",
            "kline": [[20260803, 100, 101, 99, 100.5, 200]],
        }

        with self.assertRaises(IntradayReplaySourceError) as raised:
            ExchangeReplayDailyBoundaryOperation(_Transport(payload)).collect(
                _query("SSE:600519", "SSE", "600519")
            )

        self.assertEqual(raised.exception.code, "daily_amount_unavailable")

    def test_sse_daily_amount_is_admitted_only_when_the_source_exposes_it(self) -> None:
        payload = {
            "code": "600519",
            "kline": [
                [20260802, 99, 100, 98, 99.5, 100, 900.00],
                [20260803, 100, 101, 99, 100.5, 200, 2000.00],
            ],
        }

        batch = ExchangeReplayDailyBoundaryOperation(_Transport(payload)).collect(
            _query("SSE:600519", "SSE", "600519")
        )

        self.assertEqual(batch.amount, "2000.00")
        self.assertEqual(batch.volume, "200")


if __name__ == "__main__":
    unittest.main()
