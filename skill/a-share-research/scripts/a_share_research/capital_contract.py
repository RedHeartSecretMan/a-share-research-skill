"""Versioned internal seam for capital-flow and corporate-event sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .content_contract import ContentHttpTransport

CAPITAL_DATA_TYPES = frozenset(
    {
        "northbound_flow",
        "stock_fund_flow",
        "board_fund_flow",
        "dragon_tiger",
        "market_dragon_tiger",
        "lockup",
        "margin_trading",
        "block_trade",
        "shareholder_count",
        "dividend",
    }
)

CAPITAL_SOURCE_ROLES = frozenset(
    {
        "authoritative_disclosure",
        "market_observation",
        "market_signal",
    }
)


@dataclass(frozen=True)
class CapitalQuery:
    """Normalized request passed to one capital-event source operation."""

    data_types: tuple[str, ...]
    as_of: str
    observed_from: str
    observed_to: str
    limit: int
    subject: dict[str, Any] | None
    parameters: dict[str, Any]
    allow_fallback: bool = True


@dataclass(frozen=True)
class CapitalObservation:
    """One source-indexed observation with explicit units and direction semantics."""

    data_type: str
    source_operation: str
    source_role: str
    subject: dict[str, Any] | None
    observed_on: str
    available_at: str | None
    retrieved_at: datetime
    period: dict[str, str | None]
    metrics: dict[str, str | None]
    units: dict[str, str]
    directions: dict[str, str]
    dimensions: dict[str, Any]
    locator_uri: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapitalSourceFailure:
    """Fail-closed, non-sensitive diagnosis returned by a source operation."""

    source_operation: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        return {
            "source_operation": self.source_operation,
            "code": self.code,
            "message": self.message,
            **self.details,
        }


@dataclass(frozen=True)
class CapitalSourceBatch:
    """One operation's bounded observations and explicit acquisition state."""

    operation_id: str
    observations: tuple[CapitalObservation, ...] = ()
    source_errors: tuple[CapitalSourceFailure, ...] = ()
    degradations: tuple[CapitalSourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()
    complete: bool = True


class CapitalSourceOperation(Protocol):
    """Adapter operation seam hidden behind the capital-events module."""

    operation_id: str
    supported_data_types: frozenset[str]

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch: ...


CapitalHttpTransport = ContentHttpTransport
