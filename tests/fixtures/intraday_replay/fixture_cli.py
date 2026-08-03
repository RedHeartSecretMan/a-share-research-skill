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
    IntradayReplayDailySourceBatch,
    IntradayReplaySourceBatch,
    IntradayReplaySourceError,
    IntradayReplaySourceRow,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 4, 16, 0, tzinfo=CHINA_STANDARD_TIME)


def _research_now() -> datetime:
    value = os.environ.get("A_SHARE_INTRADAY_REPLAY_NOW")
    if not value:
        return RETRIEVED_AT
    return datetime.fromisoformat(value)


def _completed_dates(replay_date: date) -> tuple[date, ...]:
    dates: list[date] = []
    candidate = replay_date
    while len(dates) < 20:
        if candidate.weekday() < 5:
            dates.append(candidate)
        candidate -= timedelta(days=1)
    return tuple(dates)


def _complete_rows(trading_date: date) -> tuple[IntradayReplaySourceRow, ...]:
    rows: list[IntradayReplaySourceRow] = [
        IntradayReplaySourceRow(
            source_timestamp=f"{trading_date.isoformat()}T09:25:00+08:00",
            timestamp_semantics="interval_start",
            trading_phase="opening_auction",
            trade_state="traded",
            open_price="10.00",
            high_price="10.00",
            low_price="10.00",
            close_price="10.00",
            volume="100",
            amount="1000.00",
            evidence_locator="fixture:auction:opening",
        )
    ]
    for phase, start_time, end_time in (
        ("continuous_morning", (9, 30), (11, 30)),
        ("continuous_afternoon", (13, 0), (14, 57)),
    ):
        cursor = datetime(
            trading_date.year,
            trading_date.month,
            trading_date.day,
            start_time[0],
            start_time[1],
            tzinfo=CHINA_STANDARD_TIME,
        )
        end = datetime(
            trading_date.year,
            trading_date.month,
            trading_date.day,
            end_time[0],
            end_time[1],
            tzinfo=CHINA_STANDARD_TIME,
        )
        while cursor < end:
            label = cursor.strftime("%H%M")
            rows.append(
                IntradayReplaySourceRow(
                    source_timestamp=cursor.isoformat(),
                    timestamp_semantics="interval_start",
                    trading_phase=phase,
                    trade_state="traded",
                    open_price="10.00",
                    high_price="10.01",
                    low_price="9.99",
                    close_price="10.00",
                    volume="100",
                    amount="1000.00",
                    evidence_locator=f"fixture:row:{label}",
                )
            )
            cursor += timedelta(minutes=1)
    rows.append(
        IntradayReplaySourceRow(
            source_timestamp=f"{trading_date.isoformat()}T15:00:00+08:00",
            timestamp_semantics="interval_end",
            trading_phase="closing_auction",
            trade_state="traded",
            open_price="10.00",
            high_price="10.00",
            low_price="10.00",
            close_price="10.00",
            volume="100",
            amount="1000.00",
            evidence_locator="fixture:auction:closing",
        )
    )
    return tuple(rows)


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
        trading_status = (
            "suspended"
            if scenario
            in {
                "single_source_suspension",
                "suspension_confirmed",
            }
            else "traded"
        )
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
        elif scenario == "complete":
            rows = _complete_rows(replay_date)
        elif scenario == "subinterval_partial":
            complete_rows = _complete_rows(replay_date)
            rows = (
                *complete_rows[:-1],
                replace(
                    complete_rows[-1],
                    source_timestamp=f"{replay_date.isoformat()}T14:58:00+08:00",
                    timestamp_semantics="interval_end",
                    auction_interval_start=(
                        f"{replay_date.isoformat()}T14:57:00+08:00"
                    ),
                    auction_interval_end=(f"{replay_date.isoformat()}T14:58:00+08:00"),
                    evidence_locator="fixture:auction:closing:subinterval",
                ),
            )
        elif scenario == "partial_no_trade":
            rows = (
                replace(
                    rows[1],
                    trading_phase="continuous_morning",
                    evidence_locator="fixture:partial:0930",
                ),
                replace(
                    rows[1],
                    source_timestamp="2026-08-03T09:31:00+08:00",
                    trading_phase="continuous_morning",
                    trade_state="no_trade",
                    open_price=None,
                    high_price=None,
                    low_price=None,
                    close_price=None,
                    volume="0",
                    amount="0.00",
                    evidence_locator="fixture:partial:no-trade-0931",
                ),
                replace(
                    rows[0],
                    source_timestamp="2026-08-03T13:00:00+08:00",
                    trading_phase="continuous_afternoon",
                    evidence_locator="fixture:partial:1300",
                ),
            )
        elif scenario == "zero_volume":
            rows = (
                replace(rows[0], volume="0", amount="0.00"),
                replace(rows[1], volume="0", amount="0.00"),
            )
        if trading_status == "suspended":
            rows = ()
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
            session_contract=(
                None if scenario == "unknown_session" else "cn_a_share_regular_v1"
            ),
            coverage_bound=(
                "indeterminate" if scenario == "indeterminate" else "bounded"
            ),
            closing_auction_semantics=(
                None
                if scenario == "unknown_auction"
                else (
                    "subinterval_transactions"
                    if scenario == "subinterval_partial"
                    else "final_match_14:57_15:00"
                )
            ),
            trading_status=trading_status,
        )


class FixtureIntradayReplayDailyOperation:
    def __init__(self, operation_id: str = "fixture_daily_boundary@1") -> None:
        self.operation_id = operation_id

    def collect(self, query: object) -> IntradayReplayDailySourceBatch:
        replay_query = query
        scenario = os.environ.get("A_SHARE_INTRADAY_REPLAY_SCENARIO")
        if scenario == "daily_source_error":
            raise IntradayReplaySourceError(
                self.operation_id,
                "daily_source_unavailable",
                "Synthetic daily source unavailable.",
            )
        daily_status = "suspended" if scenario == "suspension_confirmed" else "traded"
        close_price = "10.23" if scenario == "daily_conflict" else "10.22"
        previous_close = "9.90" if scenario == "ex_right_only" else "10.00"
        previous_close_basis = (
            "ex_right_reference" if scenario == "ex_right_only" else "actual_unadjusted"
        )
        daily_volume_unit = (
            "hands" if scenario == "daily_unit_normalization" else "shares"
        )
        daily_volume = (
            "3"
            if scenario == "daily_unit_normalization"
            else "0"
            if scenario == "zero_volume"
            else "300"
        )
        daily_amount_unit = (
            "CNY_thousand" if scenario == "daily_unit_normalization" else "CNY"
        )
        daily_amount = (
            "3.059"
            if scenario == "daily_unit_normalization"
            else "0.00"
            if scenario == "zero_volume"
            else "3059.00"
        )
        daily_open = "10.11" if scenario == "daily_auction_explained" else "10.10"
        comparison_explanations = (
            (("open", "auction_bucketing"),)
            if scenario == "daily_auction_explained"
            else ()
        )
        daily_security = (
            "SZSE:000001"
            if scenario == "daily_security_mismatch"
            else replay_query.security  # type: ignore[attr-defined]
        )
        daily_date = (
            replay_query.replay_date - timedelta(days=1)  # type: ignore[attr-defined]
            if scenario == "daily_date_mismatch"
            else replay_query.replay_date  # type: ignore[attr-defined]
        )
        return IntradayReplayDailySourceBatch(
            operation_id=self.operation_id,
            contract_version="1.0",
            security=daily_security,
            trading_date=daily_date,
            retrieved_at=RETRIEVED_AT,
            experimental=scenario != "daily_qualified",
            price_adjustment="unadjusted",
            price_unit="CNY/share",
            price_precision="0.01",
            price_minimum_tick="0.01",
            volume_unit=daily_volume_unit,
            amount_unit=daily_amount_unit,
            open_price=None if daily_status == "suspended" else daily_open,
            high_price=None if daily_status == "suspended" else "10.25",
            low_price=None if daily_status == "suspended" else "10.09",
            close_price=None if daily_status == "suspended" else close_price,
            actual_close_price=None if daily_status == "suspended" else close_price,
            volume="0" if daily_status == "suspended" else daily_volume,
            amount="0.00" if daily_status == "suspended" else daily_amount,
            volume_lot_size="100" if daily_volume_unit == "hands" else None,
            amount_scale="1000" if daily_amount_unit == "CNY_thousand" else "1",
            trading_status=daily_status,
            previous_trading_date=date(2026, 7, 31),
            previous_close=previous_close,
            previous_close_basis=previous_close_basis,
            ex_right_reference=None if scenario == "ex_right_only" else "9.90",
            ex_right_reference_date=None
            if scenario == "ex_right_only"
            else date(2026, 7, 31),
            evidence_locator="fixture:daily:20260803",
            comparison_explanations=comparison_explanations,
        )


if __name__ == "__main__":
    scenario = os.environ.get("A_SHARE_INTRADAY_REPLAY_SCENARIO")
    daily_operations = (
        (
            FixtureIntradayReplayDailyOperation(
                operation_id=(
                    "fixture_intraday_replay@1"
                    if scenario == "daily_same_operation"
                    else "fixture_daily_boundary@1"
                )
            ),
        )
        if scenario
        in {
            "daily_agreement",
            "daily_conflict",
            "daily_date_mismatch",
            "daily_qualified",
            "daily_security_mismatch",
            "daily_unit_normalization",
            "daily_auction_explained",
            "daily_source_error",
            "daily_same_operation",
            "zero_volume",
            "ex_right_only",
            "single_source_suspension",
            "suspension_confirmed",
        }
        else ()
    )
    raise SystemExit(
        main(
            sys.argv[1:],
            research_now=_research_now(),
            intraday_replay_operations=(FixtureIntradayReplayOperation(),),
            intraday_replay_daily_operations=daily_operations,
        )
    )
