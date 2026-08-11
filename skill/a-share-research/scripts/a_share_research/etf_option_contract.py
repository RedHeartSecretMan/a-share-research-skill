"""Typed internal seam for ETF-option source operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol, TypeVar

OPTION_COVERAGE_STATES = frozenset(
    {"observed_nonempty", "observed_empty", "partial", "indeterminate"}
)
OPTION_ANALYTIC_NAMES = frozenset(
    {
        "delta",
        "gamma",
        "theta",
        "vega",
        "implied_volatility",
        "theoretical_value",
    }
)
_ValueT = TypeVar("_ValueT")


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, _ValueT]) -> Mapping[str, _ValueT]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def json_safe_copy(value: Any) -> Any:
    """Return a detached JSON-container copy of frozen acquisition material."""

    if isinstance(value, Mapping):
        return {str(key): json_safe_copy(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [json_safe_copy(item) for item in value]
    return value


@dataclass(frozen=True)
class OptionQuery:
    """Normalized request passed to one ETF-option source operation."""

    subject_clue: str
    as_of: str
    observed_on: str
    view: str
    expiry_mode: str
    expiry_date: str | None
    quote_mode: str


@dataclass(frozen=True)
class EtfOptionSubject:
    """Canonical ETF identity established by a source operation."""

    exchange: str
    code: str
    name: str
    identity_source_operation: str | None = None
    identity_evidence_id: str | None = None
    identity_locator_uri: str | None = None
    identity_retrieved_at: datetime | None = None
    identity_observed_on: str | None = None

    def to_result(self) -> dict[str, Any]:
        result = {
            "security": {
                "exchange": self.exchange,
                "code": self.code,
                "type": "ETF",
            },
            "name": self.name,
        }
        if self.identity_evidence_id is not None:
            result["evidence_ids"] = [self.identity_evidence_id]
        return result


@dataclass(frozen=True)
class OptionSession:
    """Underlying reference price and trading-session observation."""

    trading_date: str
    observed_at: str
    market_state: str
    reference_price: str
    reference_price_kind: str
    reference_evidence_id: str
    locator_uri: str
    retrieved_at: datetime
    reference_observed_at: str | None = None
    reference_source_operation: str | None = None
    reference_retrieved_at: datetime | None = None


@dataclass(frozen=True)
class OptionAnalytic:
    """One provider-reported Greek or implied-volatility value."""

    value: str
    unit: str
    origin: str = "provider_reported"

    def to_result(self, evidence_id: str) -> dict[str, Any]:
        return {
            "status": "observed",
            "value": self.value,
            "unit": self.unit,
            "origin": self.origin,
            "evidence_ids": [evidence_id],
        }


@dataclass(frozen=True)
class OptionContractQuote:
    """Canonical contract metadata, quote state, and provider analytics."""

    security: Mapping[str, str]
    option_type: str
    strike: str
    contract_month: str
    expiry_date: str
    series: str
    quote_state: str
    last: str | None
    bid: str | None
    ask: str | None
    observed_at: str
    analytics: Mapping[str, OptionAnalytic]
    source_operation: str
    evidence_id: str
    locator_uri: str
    limitations: tuple[str, ...] = ()
    bid_size: str | None = None
    ask_size: str | None = None
    volume: str | None = None
    open_interest: str | None = None
    analytics_evidence_id: str | None = None
    analytics_locator_uri: str | None = None
    quote_retrieved_at: datetime | None = None
    analytics_retrieved_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "security", _freeze_mapping(self.security))
        object.__setattr__(self, "analytics", _freeze_mapping(self.analytics))
        object.__setattr__(self, "limitations", tuple(self.limitations))


@dataclass(frozen=True)
class OptionCoverage:
    """Acquisition coverage for one bounded ETF-option result dimension."""

    state: str
    expected_count: int | None = None
    observed_count: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in OPTION_COVERAGE_STATES:
            raise ValueError("ETF-option coverage state is invalid")
        if self.expected_count is not None and self.expected_count < 0:
            raise ValueError("ETF-option expected coverage count must be nonnegative")
        if self.observed_count is not None and self.observed_count < 0:
            raise ValueError("ETF-option observed coverage count must be nonnegative")
        if {
            "state",
            "expected_count",
            "observed_count",
        }.intersection(self.details):
            raise ValueError("ETF-option coverage details use a reserved field")
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.state}
        if self.expected_count is not None:
            result["expected_count"] = self.expected_count
        if self.observed_count is not None:
            result["observed_count"] = self.observed_count
        result.update(json_safe_copy(self.details))
        return result


@dataclass(frozen=True)
class OptionSourceFailure:
    """Fail-closed source diagnosis without raw provider payloads."""

    source_operation: str
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if {"source_operation", "code", "message"}.intersection(self.details):
            raise ValueError("ETF-option source failure details use a reserved field")
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    def to_result(self) -> dict[str, Any]:
        return {
            "source_operation": self.source_operation,
            "code": self.code,
            "message": self.message,
            **json_safe_copy(self.details),
        }


@dataclass(frozen=True)
class OptionContractListingEvidence:
    """One call/put contract-list response that bounds the observed set."""

    source_operation: str
    evidence_id: str
    option_type: str
    contract_month: str
    observed_count: int
    locator_uri: str
    retrieved_at: datetime
    contract_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "contract_codes", tuple(self.contract_codes))

    def to_evidence(self, subject: EtfOptionSubject) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "market_observation",
            "source_operation": self.source_operation,
            "experimental": True,
            "subject": subject.to_result(),
            "observation": {
                "kind": "ETF option contract listing",
                "option_type": self.option_type,
                "contract_month": self.contract_month,
                "observed_count": self.observed_count,
                "authoritative_total_available": False,
            },
            "evidence_time": None,
            "available_at": None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {"uri": self.locator_uri},
            "limitations": [
                "availability_time_unknown",
                "authoritative_contract_total_unavailable",
            ],
        }


@dataclass(frozen=True)
class OptionContractMonthEvidence:
    """The provider month-list response used to choose one expiry month."""

    source_operation: str
    evidence_id: str
    observed_months: tuple[str, ...]
    identity_status: str
    locator_uri: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_months", tuple(self.observed_months))

    def to_evidence(self, subject: EtfOptionSubject | None) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "market_observation",
            "source_operation": self.source_operation,
            "experimental": True,
            "subject": None if subject is None else subject.to_result(),
            "observation": {
                "kind": "ETF option contract months",
                "observed_months": list(self.observed_months),
                "identity_status": self.identity_status,
            },
            "evidence_time": None,
            "available_at": None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {"uri": self.locator_uri},
            "limitations": [
                "availability_time_unknown",
                "authoritative_contract_total_unavailable",
            ],
        }


@dataclass(frozen=True)
class OptionAcquisitionOwners:
    """Code-bound owners for each child kind in typed acquisition material."""

    executor_operation: str
    identity_operation: str
    reference_operation: str
    option_operation: str


@dataclass(frozen=True)
class OptionSourceBatch:
    """One operation's canonical ETF-option snapshot."""

    operation_id: str
    owners: OptionAcquisitionOwners | None = None
    subject: EtfOptionSubject | None = None
    session: OptionSession | None = None
    contracts: tuple[OptionContractQuote, ...] = ()
    coverage: Mapping[str, OptionCoverage] = field(default_factory=dict)
    source_errors: tuple[OptionSourceFailure, ...] = ()
    degradations: tuple[OptionSourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()
    listing_evidence: tuple[OptionContractListingEvidence, ...] = ()
    month_evidence: OptionContractMonthEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "contracts", tuple(self.contracts))
        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage))
        object.__setattr__(self, "source_errors", tuple(self.source_errors))
        object.__setattr__(self, "degradations", tuple(self.degradations))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "listing_evidence", tuple(self.listing_evidence))
        owners = self.owners
        if owners is None:
            return
        if self.operation_id != owners.executor_operation:
            raise ValueError("ETF-option acquisition executor owner is inconsistent")
        if (
            self.subject is not None
            and self.subject.identity_source_operation != owners.identity_operation
        ):
            raise ValueError(
                "ETF-option acquisition identity child owner is inconsistent"
            )
        if (
            self.session is not None
            and self.session.reference_source_operation != owners.reference_operation
        ):
            raise ValueError(
                "ETF-option acquisition reference child owner is inconsistent"
            )
        option_children = (
            *self.contracts,
            *self.listing_evidence,
            *(() if self.month_evidence is None else (self.month_evidence,)),
        )
        if any(
            item.source_operation != owners.option_operation for item in option_children
        ):
            raise ValueError(
                "ETF-option acquisition option child owner is inconsistent"
            )
        allowed_error_owners = {
            owners.identity_operation,
            owners.reference_operation,
            owners.option_operation,
        }
        if any(
            item.source_operation not in allowed_error_owners
            for item in (*self.source_errors, *self.degradations)
        ):
            raise ValueError("ETF-option acquisition error owner is inconsistent")

    @property
    def acquisition_state(self) -> str:
        """Derive the four-state acquisition result from fixed facts and errors."""

        if self.contracts:
            return "partial" if self.source_errors else "success"
        if any(
            item.code
            in {"empty_contract_list_unverified", "empty_contract_months_unverified"}
            for item in self.source_errors
        ):
            return "empty"
        return "failure"


class OptionSourceOperation(Protocol):
    """Adapter seam hidden behind the ETF-options research module."""

    operation_id: str
    acquisition_owners: OptionAcquisitionOwners
    raw_fields: tuple[str, ...]

    def collect(self, query: OptionQuery) -> OptionSourceBatch: ...
