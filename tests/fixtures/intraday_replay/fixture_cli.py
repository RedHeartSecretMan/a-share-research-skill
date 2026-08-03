#!/usr/bin/env python3
"""Offline public-CLI harness for the intraday replay tracer."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[2]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402
from a_share_research.intraday_replay_contract import (  # noqa: E402
    IntradayReplaySourceBatch,
    IntradayReplaySourceRow,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 4, 16, 0, tzinfo=CHINA_STANDARD_TIME)


def _completed_dates(replay_date: date) -> tuple[date, ...]:
    dates: list[date] = []
    candidate = replay_date
    while len(dates) < 20:
        if candidate.weekday() < 5:
            dates.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(dates)


class FixtureIntradayReplayOperation:
    operation_id = "fixture_intraday_replay@1"

    def collect(self, query: object) -> IntradayReplaySourceBatch:
        replay_query = query
        replay_date = replay_query.replay_date  # type: ignore[attr-defined]
        security = replay_query.security  # type: ignore[attr-defined]
        rows = (
            IntradayReplaySourceRow(
                source_timestamp="2026-08-03T09:31:00+08:00",
                timestamp_semantics="interval_start",
                trading_phase="continuous",
                trade_state="traded",
                open_price="10.20",
                high_price="10.25",
                low_price="10.19",
                close_price="10.22",
                volume="200",
                amount="2044.00",
                evidence_locator="fixture:row:0931",
            ),
            IntradayReplaySourceRow(
                source_timestamp="2026-08-03T09:30:00+08:00",
                timestamp_semantics="interval_start",
                trading_phase="continuous",
                trade_state="traded",
                open_price="10.10",
                high_price="10.21",
                low_price="10.09",
                close_price="10.20",
                volume="100",
                amount="1015.00",
                evidence_locator="fixture:row:0930",
            ),
        )
        scenario = os.environ.get("A_SHARE_INTRADAY_REPLAY_SCENARIO")
        volume_unit = "shares"
        volume_lot_size = None
        price_adjustment = "unadjusted"
        experimental = scenario != "qualified"
        if scenario == "float_noise":
            rows = (
                replace(rows[0], open_price=10.2000000001, amount=2044.0000000001),
                replace(rows[1], close_price=10.2000000001),
            )
        elif scenario == "duplicate":
            rows = (*rows, replace(rows[1], evidence_locator="fixture:duplicate:0930"))
        elif scenario == "duplicate_reversed":
            rows = (
                replace(rows[1], evidence_locator="fixture:duplicate:0930"),
                rows[0],
                rows[1],
            )
        elif scenario == "unknown_timestamp":
            rows = (replace(rows[0], timestamp_semantics="provider_default"), *rows[1:])
        elif scenario == "interval_end":
            rows = (
                replace(
                    rows[0],
                    source_timestamp="2026-08-03T09:32:00+08:00",
                    timestamp_semantics="interval_end",
                ),
                rows[1],
            )
        elif scenario == "hands":
            volume_unit = "hands"
            volume_lot_size = "100"
            rows = (replace(rows[0], volume=2), replace(rows[1], volume=1))
        elif scenario == "forward_adjusted":
            price_adjustment = "forward_adjusted"
        elif scenario == "no_trade_nonzero":
            rows = (
                replace(
                    rows[0],
                    trade_state="no_trade",
                    open_price=None,
                    high_price=None,
                    low_price=None,
                    close_price=None,
                    volume="1",
                    amount="1.00",
                ),
                rows[1],
            )
        completed_trading_dates = _completed_dates(replay_date)
        if scenario == "missing_calendar":
            completed_trading_dates = ()
        return IntradayReplaySourceBatch(
            operation_id=self.operation_id,
            contract_version="1.0",
            security=security,
            trading_date=replay_date,
            retrieved_at=RETRIEVED_AT,
            experimental=experimental,
            price_adjustment=price_adjustment,
            price_unit="CNY/share",
            price_precision="0.01",
            volume_unit=volume_unit,
            amount_unit="CNY",
            volume_lot_size=volume_lot_size,
            completed_trading_dates=completed_trading_dates,
            rows=rows,
        )


if __name__ == "__main__":
    raise SystemExit(
        main(
            sys.argv[1:],
            research_now=RETRIEVED_AT,
            intraday_replay_operations=(FixtureIntradayReplayOperation(),),
        )
    )
