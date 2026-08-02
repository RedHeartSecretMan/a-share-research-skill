"""Versioned internal seam for market-signal source operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .content_contract import ContentHttpTransport

MARKET_SIGNAL_TYPES = frozenset(
    {
        "strong_stock_theme",
        "security_board_membership",
        "industry_rotation",
        "limit_state",
        "focus_monitoring",
        "severe_abnormal_movement",
        "monitoring_intersection",
        "market_heat",
    }
)

SIGNAL_COVERAGE_STATES = frozenset(
    {"observed_nonempty", "observed_empty", "partial", "indeterminate"}
)

ATTRIBUTION_PROVENANCE = frozenset(
    {"editorial_annotation", "market_signal", "model_inference"}
)

MARKET_SIGNAL_SOURCE_ROLES = frozenset({"market_signal"})


@dataclass(frozen=True)
class MarketSignalQuery:
    """Normalized bounded request passed to one market-signal operation."""

    signal_types: tuple[str, ...]
    as_of: str
    observed_from: str
    observed_to: str
    limit: int
    subject: dict[str, Any] | None
    parameters: dict[str, Any]
    allow_fallback: bool = True


@dataclass(frozen=True)
class ThemeAttribution:
    """A reason label whose provenance remains explicit in public evidence."""

    text: str
    provenance: str
    source_operation: str
    source_document_id: str | None
    locator_uri: str | None
    basis_evidence_ids: tuple[str, ...] = ()
    method_id: str | None = None


@dataclass(frozen=True)
class MarketSignalObservation:
    """One source-indexed market signal before coordinator reconciliation."""

    signal_type: str
    source_operation: str
    source_role: str
    subject: dict[str, Any] | None
    source_document_id: str | None
    observed_on: str
    observed_at: str | None
    available_at: str | None
    retrieved_at: datetime
    period: dict[str, str | None]
    metrics: dict[str, str | None]
    units: dict[str, str]
    directions: dict[str, str]
    rule: dict[str, Any] | None
    attributions: tuple[ThemeAttribution, ...]
    dimensions: dict[str, Any]
    locator_uri: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignalCoverage:
    """One operation's explicit coverage state for one requested signal type."""

    state: str
    provider_total: int | None = None
    pages_collected: int | None = None
    pages_expected: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.state}
        if self.provider_total is not None:
            result["provider_total"] = self.provider_total
        if self.pages_collected is not None:
            result["pages_collected"] = self.pages_collected
        if self.pages_expected is not None:
            result["pages_expected"] = self.pages_expected
        result.update(self.details)
        return result


@dataclass(frozen=True)
class SignalSourceFailure:
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
class SignalSourceBatch:
    """One operation's observations and per-signal acquisition coverage."""

    operation_id: str
    observations: tuple[MarketSignalObservation, ...] = ()
    coverage: dict[str, SignalCoverage] = field(default_factory=dict)
    source_errors: tuple[SignalSourceFailure, ...] = ()
    degradations: tuple[SignalSourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()


class MarketSignalSourceOperation(Protocol):
    """Adapter operation seam hidden behind the market-signals module."""

    operation_id: str
    supported_signal_types: frozenset[str]

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch: ...


@runtime_checkable
class ParameterAwareMarketSignalSourceOperation(Protocol):
    """Optional seam for operations whose applicability depends on sub-parameters."""

    def is_applicable(self, query: MarketSignalQuery) -> bool: ...


MarketSignalHttpTransport = ContentHttpTransport
