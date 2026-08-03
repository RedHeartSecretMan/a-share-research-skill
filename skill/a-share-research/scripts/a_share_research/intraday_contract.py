"""Typed internal seam for research-grade intraday source operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Protocol


@dataclass(frozen=True)
class IntradayQuery:
    """One canonical current-date A-share observation request."""

    security: str
    exchange: str
    code: str
    as_of: date
    retrieved_at: datetime


SESSION_STATES = frozenset(
    {
        "opening_auction",
        "continuous",
        "midday_break",
        "closing_auction",
    }
)


def session_at(value: datetime) -> str | None:
    """Classify a source timestamp into an applicable SSE/SZSE session.

    The timestamp is only one part of the later adjudication: the ResearchTask
    still has to prove that both independent sources describe the same trading
    date and that the retrieval itself is in a compatible session.
    """

    observed_time = value.timetz().replace(tzinfo=None)
    if time(9, 15) <= observed_time <= time(9, 25):
        return "opening_auction"
    if time(9, 30) <= observed_time <= time(11, 30):
        return "continuous"
    if time(11, 30) < observed_time < time(13, 0):
        return "midday_break"
    if time(13, 0) <= observed_time < time(14, 57):
        return "continuous"
    if time(14, 57) <= observed_time <= time(15, 0):
        return "closing_auction"
    return None


@dataclass(frozen=True)
class IntradayObservation:
    """One source operation's normalized intraday observation."""

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
    # A source timestamp proves when a quote was observed; an unknown cache
    # state must never be silently treated as fresh by the adjudicator.
    cache_state: str | None = None
    observation_boundary: str | None = None
    previous_close_comparability: str | None = None
    corporate_action: dict[str, str] | None = None
    no_trade_confirmed: bool = False


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
