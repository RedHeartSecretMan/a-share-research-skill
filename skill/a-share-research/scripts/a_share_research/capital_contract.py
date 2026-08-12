"""Versioned internal seam for capital-flow and corporate-event sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
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
class CapitalModeAcquisition:
    """One trading-event mode's fixed source-acquisition material."""

    data_type: str
    operation_id: str
    observations: tuple[CapitalObservation, ...] = ()
    source_errors: tuple[CapitalSourceFailure, ...] = ()
    degradations: tuple[CapitalSourceFailure, ...] = ()
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "source_errors", tuple(self.source_errors))
        object.__setattr__(self, "degradations", tuple(self.degradations))
        if self.data_type not in CAPITAL_DATA_TYPES:
            raise ValueError("capital acquisition data type is unknown")
        if any(
            item.data_type != self.data_type
            or item.source_operation != self.operation_id
            for item in self.observations
        ):
            raise ValueError("capital acquisition observation owner is inconsistent")
        if any(
            item.source_operation != self.operation_id
            for item in (*self.source_errors, *self.degradations)
        ):
            raise ValueError("capital acquisition error owner is inconsistent")

    @property
    def state(self) -> str:
        """Derive one mode's four-state acquisition result."""

        if self.complete:
            return "observed_nonempty" if self.observations else "observed_empty"
        return "partial" if self.observations else "indeterminate"


@dataclass(frozen=True)
class CapitalSourceBatch:
    """One operation's bounded observations and explicit acquisition state."""

    operation_id: str
    observations: tuple[CapitalObservation, ...] = ()
    source_errors: tuple[CapitalSourceFailure, ...] = ()
    degradations: tuple[CapitalSourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()
    complete: bool = True
    mode_acquisitions: Mapping[str, CapitalModeAcquisition] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "source_errors", tuple(self.source_errors))
        object.__setattr__(self, "degradations", tuple(self.degradations))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        acquisitions = MappingProxyType(dict(self.mode_acquisitions))
        object.__setattr__(self, "mode_acquisitions", acquisitions)
        if not acquisitions:
            return
        if any(
            key != item.data_type or item.operation_id != self.operation_id
            for key, item in acquisitions.items()
        ):
            raise ValueError("capital mode acquisition owner is inconsistent")
        expected_observations = tuple(
            observation
            for acquisition in acquisitions.values()
            for observation in acquisition.observations
        )
        expected_errors = tuple(
            error
            for acquisition in acquisitions.values()
            for error in acquisition.source_errors
        )
        expected_degradations = tuple(
            degradation
            for acquisition in acquisitions.values()
            for degradation in acquisition.degradations
        )
        if (
            self.observations != expected_observations
            or self.source_errors != expected_errors
            or self.degradations != expected_degradations
            or self.complete != all(item.complete for item in acquisitions.values())
        ):
            raise ValueError("capital batch disagrees with mode acquisition material")

    @classmethod
    def from_mode_acquisitions(
        cls,
        operation_id: str,
        acquisitions: Mapping[str, CapitalModeAcquisition],
        *,
        limitations: tuple[str, ...] = (),
    ) -> CapitalSourceBatch:
        """Build aggregate batch fields from the typed source material."""

        fixed = tuple(acquisitions.values())
        return cls(
            operation_id=operation_id,
            observations=tuple(
                observation
                for acquisition in fixed
                for observation in acquisition.observations
            ),
            source_errors=tuple(
                error for acquisition in fixed for error in acquisition.source_errors
            ),
            degradations=tuple(
                degradation
                for acquisition in fixed
                for degradation in acquisition.degradations
            ),
            limitations=limitations,
            complete=all(item.complete for item in fixed),
            mode_acquisitions=acquisitions,
        )


class CapitalSourceOperation(Protocol):
    """Adapter operation seam hidden behind the capital-events module."""

    operation_id: str
    supported_data_types: frozenset[str]
    raw_fields: tuple[str, ...]

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch: ...


CapitalHttpTransport = ContentHttpTransport
