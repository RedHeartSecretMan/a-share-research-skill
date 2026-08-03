from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.intraday_replay_contract import (  # noqa: E402
    IntradayReplayQuery,
    IntradayReplaySourceError,
)
from a_share_research.intraday_replay_mootdx import (  # noqa: E402
    MootdxIntradayReplayOperation,
    MootdxReplayContract,
)

CHINA = timezone(timedelta(hours=8))


def _query() -> IntradayReplayQuery:
    return IntradayReplayQuery(
        security="SSE:600519",
        exchange="SSE",
        code="600519",
        as_of=date(2026, 8, 4),
        replay_date=date(2026, 8, 3),
        research_boundary=datetime(2026, 8, 4, 23, 59, tzinfo=CHINA),
        retrieved_at=datetime(2026, 8, 4, 16, 0, tzinfo=CHINA),
    )


def _contract() -> MootdxReplayContract:
    return MootdxReplayContract(
        timestamp_semantics="interval_start",
        timestamp_timezone="Asia/Shanghai",
        price_adjustment="unadjusted",
        price_unit="CNY/share",
        price_scale="0.01",
        price_minimum_tick="0.01",
        volume_unit="hands",
        volume_lot_size="100",
        amount_unit="CNY_thousand",
        amount_scale="1000",
        session_contract="cn_a_share_regular_v1",
        coverage_bound="bounded",
        closing_auction_semantics="final_match_14:57_15:00",
        completed_calendar_basis="source_verified_completed_trading_dates",
    )


def _row(trading_date: date, *, code: str = "600519") -> dict[str, object]:
    return {
        "code": code,
        "datetime": f"{trading_date.isoformat()}T09:30:00+08:00",
        "trading_phase": "continuous_morning",
        "trade_state": "traded",
        "open": "1000",
        "high": "1010",
        "low": "990",
        "close": "1005",
        "vol": "2",
        "amount": "1.2",
    }


class _Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        if orient != "records":
            raise AssertionError(orient)
        return self._rows


class _Client:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.offsets: list[int] = []
        self.closed = False

    def bars(self, *, symbol: str, frequency: int, start: int, offset: int) -> _Frame:
        if symbol != "600519" or frequency != 8 or start != 0:
            raise AssertionError((symbol, frequency, start))
        self.offsets.append(offset)
        return _Frame(self.rows[offset : offset + 2])

    def close(self) -> None:
        self.closed = True


class MootdxReplayAdapterTests(unittest.TestCase):
    def test_qualified_pages_normalize_units_and_keep_recent_calendar(self) -> None:
        dates: list[date] = []
        candidate = date(2026, 8, 3)
        while len(dates) < 20:
            if candidate.weekday() < 5:
                dates.append(candidate)
            candidate -= timedelta(days=1)
        client = _Client([_row(item) for item in reversed(dates)])
        operation = MootdxIntradayReplayOperation(
            lambda **_: client,
            contract=_contract(),
            page_size=2,
            max_pages=12,
        )

        batch = operation.collect(_query())

        self.assertEqual(batch.security, "SSE:600519")
        self.assertEqual(batch.completed_trading_dates, tuple(sorted(dates)))
        self.assertEqual(len(batch.rows), 1)
        self.assertEqual(batch.rows[0].open_price, "10.00")
        self.assertEqual(batch.rows[0].volume, "200")
        self.assertEqual(batch.rows[0].amount, "1200.00")
        self.assertEqual(client.offsets[-1], 20)
        self.assertTrue(client.closed)

    def test_unqualified_timestamp_semantics_fail_closed_before_rows_are_admitted(
        self,
    ) -> None:
        client = _Client([_row(date(2026, 8, 3))])

        with self.assertRaises(IntradayReplaySourceError) as raised:
            MootdxIntradayReplayOperation(
                lambda **_: client, page_size=2, max_pages=1
            ).collect(_query())

        self.assertEqual(raised.exception.code, "timestamp_semantics_unverified")
        self.assertTrue(client.closed)

    def test_observed_dates_are_not_calendar_proof_without_contract_qualification(
        self,
    ) -> None:
        client = _Client([_row(date(2026, 8, 3))])
        contract = replace(_contract(), completed_calendar_basis=None)

        with self.assertRaises(IntradayReplaySourceError) as raised:
            MootdxIntradayReplayOperation(
                lambda **_: client,
                contract=contract,
                page_size=2,
                max_pages=1,
            ).collect(_query())

        self.assertEqual(raised.exception.code, "completed_trading_calendar_unverified")
        self.assertTrue(client.closed)

    def test_share_contract_does_not_require_an_irrelevant_lot_or_tick_field(
        self,
    ) -> None:
        client = _Client([_row(date(2026, 8, 3))])
        contract = replace(
            _contract(),
            price_minimum_tick=None,
            volume_unit="shares",
            volume_lot_size=None,
            amount_unit="CNY",
            amount_scale="1",
        )

        batch = MootdxIntradayReplayOperation(
            lambda **_: client,
            contract=contract,
            page_size=2,
            max_pages=1,
        ).collect(_query())

        self.assertIsNone(batch.price_minimum_tick)
        self.assertIsNone(batch.volume_lot_size)
        self.assertEqual(batch.rows[0].volume, "2")
        self.assertEqual(batch.rows[0].amount, "1.20")

    def test_wrong_security_is_not_silently_filtered(self) -> None:
        client = _Client([_row(date(2026, 8, 3), code="000001")])
        operation = MootdxIntradayReplayOperation(
            lambda **_: client,
            contract=_contract(),
            page_size=2,
            max_pages=1,
        )

        with self.assertRaises(IntradayReplaySourceError) as raised:
            operation.collect(_query())

        self.assertEqual(raised.exception.code, "source_security_mismatch")


if __name__ == "__main__":
    unittest.main()
