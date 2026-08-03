"""Typed seam for deterministic complete intraday replay source operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

_SAFE_SOURCE_OPERATION = re.compile(r"^[A-Za-z0-9_.:@-]+$")


@dataclass(frozen=True)
class IntradayReplayQuery:
    """One canonical security and one bounded replay date."""

    security: str
    exchange: str
    code: str
    as_of: date
    replay_date: date
    research_boundary: datetime
    retrieved_at: datetime


@dataclass(frozen=True)
class IntradayReplaySourceRow:
    """One source row before the tracer normalizes its values and interval."""

    source_timestamp: str | datetime
    timestamp_semantics: str
    trading_phase: str
    trade_state: str
    open_price: object | None
    high_price: object | None
    low_price: object | None
    close_price: object | None
    volume: object
    amount: object
    evidence_locator: str | None = None
    trading_date: date | None = None
    price_adjustment: str | None = None
    auction_interval_start: str | datetime | None = None
    auction_interval_end: str | datetime | None = None


@dataclass(frozen=True)
class IntradayReplaySourceBatch:
    """One coherent operation's rows and its versioned source contract."""

    operation_id: str
    contract_version: str
    security: str
    trading_date: date
    retrieved_at: datetime
    experimental: bool
    price_adjustment: str
    price_unit: str
    price_precision: str
    volume_unit: str
    amount_unit: str
    rows: tuple[IntradayReplaySourceRow, ...]
    completed_trading_dates: tuple[date, ...] = ()
    source_role: str = "market_observation"
    timestamp_timezone: str = "Asia/Shanghai"
    volume_lot_size: str | None = None
    amount_precision: str = "0.01"
    session_contract: str | None = None
    coverage_bound: str | None = None
    closing_auction_semantics: str | None = None
    trading_status: str = "traded"
    price_minimum_tick: str | None = None
    available_at: datetime | None = None


class IntradayReplaySourceError(Exception):
    """Safe, non-sensitive diagnosis from one replay source operation."""

    def __init__(self, source_operation: str, code: str, message: str) -> None:
        super().__init__(message)
        self.source_operation = source_operation
        self.code = code


class IntradayReplaySourceOperation(Protocol):
    """Internal source operation hidden behind the public ResearchTask seam."""

    operation_id: str

    def collect(self, query: IntradayReplayQuery) -> IntradayReplaySourceBatch: ...


@dataclass(frozen=True)
class IntradayReplayDailySourceBatch:
    """One independent daily-boundary observation for the replay date."""

    operation_id: str
    contract_version: str
    security: str
    trading_date: date
    retrieved_at: datetime
    experimental: bool
    price_adjustment: str
    price_unit: str
    price_precision: str
    volume_unit: str
    amount_unit: str
    open_price: object | None
    high_price: object | None
    low_price: object | None
    close_price: object | None
    volume: object | None
    amount: object | None
    actual_close_price: object | None = None
    trading_status: str = "traded"
    source_role: str = "daily_boundary_cross_check"
    timestamp_timezone: str = "Asia/Shanghai"
    volume_lot_size: str | None = None
    amount_scale: str = "1"
    amount_precision: str = "0.01"
    price_minimum_tick: str | None = None
    evidence_locator: str | None = None
    previous_trading_date: date | None = None
    previous_close: object | None = None
    previous_close_basis: str | None = None
    ex_right_reference: object | None = None
    ex_right_reference_date: date | None = None
    comparison_explanations: tuple[tuple[str, str], ...] = ()
    available_at: datetime | None = None


class IntradayReplayDailySourceOperation(Protocol):
    """Independent daily operation used only at the replay boundary."""

    operation_id: str

    def collect(self, query: IntradayReplayQuery) -> IntradayReplayDailySourceBatch: ...


def source_error_result(error: IntradayReplaySourceError) -> dict[str, Any]:
    """Project a source failure without exposing provider diagnostics."""

    source_operation = error.source_operation
    if (
        not isinstance(source_operation, str)
        or _SAFE_SOURCE_OPERATION.fullmatch(source_operation) is None
    ):
        source_operation = "intraday_replay"
    return {
        "source_operation": source_operation,
        "code": error.code,
        "message": "The replay source rejected the request under its safe contract.",
    }
