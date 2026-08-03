"""Typed internal seam for research-grade intraday source operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class IntradayQuery:
    """One canonical current-date A-share observation request."""

    security: str
    exchange: str
    code: str
    as_of: date
    retrieved_at: datetime


@dataclass(frozen=True)
class IntradayObservation:
    """One source operation's normalized continuous-auction observation."""

    source_operation: str
    security: str
    trading_date: date
    observed_at: datetime
    retrieved_at: datetime
    session_state: str
    trading_status: str
    price_type: str
    latest_price: str
    open_price: str
    high_price: str
    low_price: str
    previous_close: str | None
    previous_close_basis: str | None
    evidence: tuple[dict[str, Any], ...]
    field_sources: dict[str, tuple[str, ...]]
    cumulative_volume_shares: str | None = None
    cumulative_amount_cny: str | None = None


class IntradaySourceError(Exception):
    """Fail-closed, non-sensitive diagnosis from one intraday operation."""

    def __init__(self, source_operation: str, code: str, message: str) -> None:
        super().__init__(message)
        self.source_operation = source_operation
        self.code = code


class IntradaySourceOperation(Protocol):
    """Internal Adapter operation hidden behind the ResearchTask Interface."""

    operation_id: str

    def collect(self, query: IntradayQuery) -> IntradayObservation: ...
