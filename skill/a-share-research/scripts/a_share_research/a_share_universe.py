"""Deterministic projection of prepared as-of A-share universe evidence."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Protocol, cast

from .universe_input import (
    ExchangeListOutcome,
    IdentityBatchOutcome,
    ListingState,
    SourceResult,
    UniverseInputError,
    UniverseRequest,
    _PreparedExchangeListOutcome,
    _PreparedExchangeListRecord,
    _PreparedIdentityBatchOutcome,
    _PreparedUniverseIdentity,
    _PreparedUniverseRequest,
    prepare_exchange_list_outcome,
    prepare_identity_outcome,
    prepare_universe_request,
)

EXCHANGES = ("SSE", "SZSE", "BSE")
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_.:@+-]+$")
_UNSAFE_REFERENCE_MARKERS = (
    "bearer",
    "credential",
    "http",
    "password",
    "secret",
    "token",
)


class UniverseContractError(ValueError):
    """A stable sanitized failure at the Universe Module Interface."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExchangeListAdapter(Protocol):
    exchange: str
    operation_id: str

    def acquire(
        self, request: object, timeout_seconds: float
    ) -> ExchangeListOutcome: ...


class IdentityAdapter(Protocol):
    exchange: str
    operation_id: str

    def resolve(
        self,
        request: object,
        listings: tuple[object, ...],
        timeout_seconds: float,
    ) -> IdentityBatchOutcome: ...


@dataclass(frozen=True, slots=True)
class UniverseSecurity:
    exchange: str
    code: str
    name: str
    security_category: str
    listing_state: ListingState | None
    effective_from: str | None
    effective_to: str | None
    universe_state: str
    unranked_reasons: tuple[str, ...]
    list_operation_id: str
    list_provenance_uri: str
    list_effective_at: str
    list_available_at: str
    list_retrieved_at: str
    identity_operation_id: str | None
    identity_provenance_uri: str | None
    identity_observed_at: str | None
    identity_available_at: str | None
    identity_retrieved_at: str | None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "canonical_security_id": f"{self.exchange}:{self.code}",
            "exchange": self.exchange,
            "code": self.code,
            "name": self.name,
            "security_category": self.security_category,
            "listing_state": self.listing_state,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "universe_state": self.universe_state,
            "unranked_reasons": list(self.unranked_reasons),
            "provenance": {
                "exchange_list": {
                    "operation_id": self.list_operation_id,
                    "uri": self.list_provenance_uri,
                    "effective_at": self.list_effective_at,
                    "available_at": self.list_available_at,
                    "retrieved_at": self.list_retrieved_at,
                },
                "identity": {
                    "operation_id": self.identity_operation_id,
                    "uri": self.identity_provenance_uri,
                    "observed_at": self.identity_observed_at,
                    "available_at": self.identity_available_at,
                    "retrieved_at": self.identity_retrieved_at,
                },
            },
        }


@dataclass(frozen=True, slots=True)
class UniverseSourceOutcome:
    operation_id: str
    attempted: bool
    source_result: SourceResult
    expected_count: int | None
    observed_count: int
    pages_expected: int | None = None
    pages_received: int | None = None

    def to_public_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "operation_id": self.operation_id,
            "attempted": self.attempted,
            "source_result": self.source_result,
            "expected_count": self.expected_count,
            "observed_count": self.observed_count,
        }
        # Identity outcomes omit pagination; list totals may themselves be unknown.
        if self.pages_received is not None:
            value["pages_expected"] = self.pages_expected
            value["pages_received"] = self.pages_received
        return value


@dataclass(frozen=True, slots=True)
class UniversePartition:
    exchange: str
    complete: bool
    attempted: bool
    source_result: SourceResult
    operation_id: str | None
    expected: int | None
    resolved: int
    failed: int | None
    securities: tuple[UniverseSecurity, ...]
    limitation_codes: tuple[str, ...]
    list_outcome: UniverseSourceOutcome | None
    identity_outcome: UniverseSourceOutcome | None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "complete": self.complete,
            "attempted": self.attempted,
            "source_result": self.source_result,
            "operation_id": self.operation_id,
            "reconciliation": {
                "expected": self.expected,
                "resolved": self.resolved,
                "failed": self.failed,
            },
            "securities": [security.to_public_dict() for security in self.securities],
            "limitation_codes": list(self.limitation_codes),
            "source_outcomes": {
                "exchange_list": (
                    self.list_outcome.to_public_dict()
                    if self.list_outcome is not None
                    else None
                ),
                "identity": (
                    self.identity_outcome.to_public_dict()
                    if self.identity_outcome is not None
                    else None
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class AShareUniverse:
    screen_date: str
    information_cutoff: str
    full_a_share_universe: bool
    partitions: tuple[UniversePartition, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "screen_date": self.screen_date,
            "information_cutoff": self.information_cutoff,
            "full_a_share_universe": self.full_a_share_universe,
            "partitions": {
                partition.exchange: partition.to_public_dict()
                for partition in self.partitions
            },
        }


@dataclass(frozen=True, slots=True)
class _ListOwner:
    exchange: str
    operation_id: str
    invoke: Callable[[object, float], object]


@dataclass(frozen=True, slots=True)
class _IdentityOwner:
    exchange: str
    operation_id: str
    invoke: Callable[[object, tuple[object, ...], float], object]


@dataclass(frozen=True, slots=True)
class _AdapterRequestView:
    screen_date: str
    information_cutoff: str


@dataclass(frozen=True, slots=True)
class _AdapterListRecordView:
    exchange: str
    code: str
    canonical_security_id: str
    name: str
    security_category: str
    effective_at: str
    available_at: str
    retrieved_at: str
    provenance_uri: str


def _error(code: str, message: str) -> UniverseContractError:
    return UniverseContractError(code, message)


def _safe_operation_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or _SAFE_OPERATION_ID.fullmatch(value) is None
        or any(marker in value.casefold() for marker in _UNSAFE_REFERENCE_MARKERS)
    ):
        raise _error(
            "invalid_adapter_identity",
            "A universe Adapter has an invalid identity.",
        )
    return value


def _snapshot_list_owners(adapters: tuple[object, ...]) -> dict[str, _ListOwner]:
    owners: dict[str, _ListOwner] = {}
    for adapter in adapters:
        snapshot: tuple[object, object, object] | None = None
        candidate = cast(ExchangeListAdapter, adapter)
        try:
            exchange = candidate.exchange
            operation_id = candidate.operation_id
            invoke = candidate.acquire
            snapshot = (exchange, operation_id, invoke)
        except Exception:
            pass
        if snapshot is None:
            raise _error(
                "adapter_snapshot_failed",
                "A universe Adapter ownership snapshot could not be read.",
            )
        exchange_value, operation_value, invoke_value = snapshot
        if (
            type(exchange_value) is not str
            or exchange_value not in EXCHANGES
            or not callable(invoke_value)
        ):
            raise _error(
                "invalid_adapter_identity",
                "A universe Adapter has an invalid identity.",
            )
        safe_operation_id = _safe_operation_id(operation_value)
        if exchange_value in owners:
            raise _error(
                "duplicate_adapter",
                "A universe exchange has more than one Adapter owner.",
            )
        owners[exchange_value] = _ListOwner(
            exchange_value,
            safe_operation_id,
            cast(Callable[[object, float], object], invoke_value),
        )
    return owners


def _snapshot_identity_owners(
    adapters: tuple[object, ...],
) -> dict[str, _IdentityOwner]:
    owners: dict[str, _IdentityOwner] = {}
    for adapter in adapters:
        snapshot: tuple[object, object, object] | None = None
        candidate = cast(IdentityAdapter, adapter)
        try:
            exchange = candidate.exchange
            operation_id = candidate.operation_id
            invoke = candidate.resolve
            snapshot = (exchange, operation_id, invoke)
        except Exception:
            pass
        if snapshot is None:
            raise _error(
                "adapter_snapshot_failed",
                "A universe Adapter ownership snapshot could not be read.",
            )
        exchange_value, operation_value, invoke_value = snapshot
        if (
            type(exchange_value) is not str
            or exchange_value not in EXCHANGES
            or not callable(invoke_value)
        ):
            raise _error(
                "invalid_adapter_identity",
                "A universe Adapter has an invalid identity.",
            )
        safe_operation_id = _safe_operation_id(operation_value)
        if exchange_value in owners:
            raise _error(
                "duplicate_adapter",
                "A universe exchange has more than one Adapter owner.",
            )
        owners[exchange_value] = _IdentityOwner(
            exchange_value,
            safe_operation_id,
            cast(
                Callable[[object, tuple[object, ...], float], object],
                invoke_value,
            ),
        )
    return owners


def _prepare_request(
    request: UniverseRequest, research_now: datetime
) -> _PreparedUniverseRequest:
    prepared: _PreparedUniverseRequest | None = None
    failure: tuple[str, str] | None = None
    try:
        prepared = prepare_universe_request(request, research_now=research_now)
    except UniverseInputError as error:
        failure = (error.code, str(error))
    if failure is not None:
        raise _error(*failure)
    if prepared is None:
        raise _error(
            "input_snapshot_failed", "Universe input snapshot could not be read."
        )
    return prepared


def _adapter_request_view(request: _PreparedUniverseRequest) -> _AdapterRequestView:
    return _AdapterRequestView(
        screen_date=request.screen_date,
        information_cutoff=request.information_cutoff,
    )


def _adapter_list_record_view(
    record: _PreparedExchangeListRecord,
) -> _AdapterListRecordView:
    return _AdapterListRecordView(
        exchange=record.exchange,
        code=record.code,
        canonical_security_id=record.canonical_security_id,
        name=record.name,
        security_category=record.security_category,
        effective_at=record.effective_at,
        available_at=record.available_at,
        retrieved_at=record.retrieved_at,
        provenance_uri=record.provenance_uri,
    )


def _invoke_list(
    owner: _ListOwner,
    request: _PreparedUniverseRequest,
    timeout_seconds: float,
) -> _PreparedExchangeListOutcome:
    raw_outcome: object | None = None
    invocation_failed = False
    try:
        raw_outcome = owner.invoke(_adapter_request_view(request), timeout_seconds)
    except Exception:
        invocation_failed = True
    if invocation_failed:
        raise _error(
            "adapter_invocation_failed",
            "A universe Adapter invocation failed.",
        )
    outcome: _PreparedExchangeListOutcome | None = None
    failure: tuple[str, str] | None = None
    try:
        outcome = prepare_exchange_list_outcome(cast(ExchangeListOutcome, raw_outcome))
    except UniverseInputError as error:
        failure = (error.code, str(error))
    if failure is not None:
        raise _error(*failure)
    if outcome is None:
        raise _error(
            "input_snapshot_failed", "Universe input snapshot could not be read."
        )
    if outcome.exchange != owner.exchange:
        raise _error(
            "cross_exchange_identity_conflict",
            "A universe outcome conflicts with its exchange partition.",
        )
    if outcome.operation_id != owner.operation_id:
        raise _error(
            "operation_identity_mismatch",
            "A universe outcome does not match its Adapter owner.",
        )
    if outcome.screen_date != request.screen_date:
        raise _error(
            "historical_list_scope_mismatch",
            "An exchange-list outcome does not prove the requested screen date.",
        )
    cutoff = datetime.fromisoformat(request.information_cutoff)
    if any(
        datetime.fromisoformat(record.available_at) > cutoff
        for record in outcome.records
    ):
        raise _error(
            "historical_list_as_of_unproven",
            "An exchange-list record was not available by the information cutoff.",
        )
    return outcome


def _invoke_identity(
    owner: _IdentityOwner,
    request: _PreparedUniverseRequest,
    listings: tuple[_PreparedExchangeListRecord, ...],
    timeout_seconds: float,
) -> _PreparedIdentityBatchOutcome:
    raw_outcome: object | None = None
    invocation_failed = False
    try:
        raw_outcome = owner.invoke(
            _adapter_request_view(request),
            cast(
                tuple[object, ...],
                tuple(_adapter_list_record_view(record) for record in listings),
            ),
            timeout_seconds,
        )
    except Exception:
        invocation_failed = True
    if invocation_failed:
        raise _error(
            "adapter_invocation_failed",
            "A universe Adapter invocation failed.",
        )
    outcome: _PreparedIdentityBatchOutcome | None = None
    failure: tuple[str, str] | None = None
    try:
        outcome = prepare_identity_outcome(cast(IdentityBatchOutcome, raw_outcome))
    except UniverseInputError as error:
        failure = (error.code, str(error))
    if failure is not None:
        raise _error(*failure)
    if outcome is None:
        raise _error(
            "input_snapshot_failed", "Universe input snapshot could not be read."
        )
    if outcome.exchange != owner.exchange:
        raise _error(
            "cross_exchange_identity_conflict",
            "A universe outcome conflicts with its exchange partition.",
        )
    if outcome.operation_id != owner.operation_id:
        raise _error(
            "operation_identity_mismatch",
            "A universe outcome does not match its Adapter owner.",
        )
    return outcome


def _classify_identity(
    identity: _PreparedUniverseIdentity,
    request: _PreparedUniverseRequest,
) -> tuple[str, tuple[str, ...]]:
    cutoff = datetime.fromisoformat(request.information_cutoff)
    if (
        identity.available_at is None
        or datetime.fromisoformat(identity.available_at) > cutoff
        or datetime.fromisoformat(identity.observed_at) > cutoff
    ):
        late_reasons = (
            [] if identity.listing_state is not None else ["listing_state_unproven"]
        )
        late_reasons.append("as_of_unproven")
        return "expected_universe", tuple(late_reasons)
    screen_date = request.screen_date
    if (
        identity.effective_from is not None and identity.effective_from > screen_date
    ) or (identity.effective_to is not None and identity.effective_to < screen_date):
        return "excluded_universe", ("outside_listing_interval",)
    reasons: list[str] = []
    if identity.listing_state is None:
        reasons.append("listing_state_unproven")
    if identity.effective_from is None:
        reasons.append("as_of_unproven")
    return "expected_universe", tuple(reasons)


def _build_security(
    request: _PreparedUniverseRequest,
    list_outcome: _PreparedExchangeListOutcome,
    identity_outcome: _PreparedIdentityBatchOutcome,
    list_record: _PreparedExchangeListRecord,
    identity: _PreparedUniverseIdentity,
) -> UniverseSecurity:
    universe_state, reasons = _classify_identity(identity, request)
    return UniverseSecurity(
        exchange=identity.exchange,
        code=identity.code,
        name=identity.name,
        security_category=identity.security_category,
        listing_state=identity.listing_state,
        effective_from=identity.effective_from,
        effective_to=identity.effective_to,
        universe_state=universe_state,
        unranked_reasons=reasons,
        list_operation_id=list_outcome.operation_id,
        list_provenance_uri=list_record.provenance_uri,
        list_effective_at=list_record.effective_at,
        list_available_at=list_record.available_at,
        list_retrieved_at=list_record.retrieved_at,
        identity_operation_id=identity_outcome.operation_id,
        identity_provenance_uri=identity.provenance_uri,
        identity_observed_at=identity.observed_at,
        identity_available_at=identity.available_at,
        identity_retrieved_at=identity.retrieved_at,
    )


def _build_unresolved_security(
    list_outcome: _PreparedExchangeListOutcome,
    identity_operation_id: str | None,
    list_record: _PreparedExchangeListRecord,
) -> UniverseSecurity:
    return UniverseSecurity(
        exchange=list_record.exchange,
        code=list_record.code,
        name=list_record.name,
        security_category=list_record.security_category,
        listing_state=None,
        effective_from=None,
        effective_to=None,
        universe_state="expected_universe",
        unranked_reasons=(
            "listing_state_unproven",
            "as_of_unproven",
            "source_failed",
        ),
        list_operation_id=list_outcome.operation_id,
        list_provenance_uri=list_record.provenance_uri,
        list_effective_at=list_record.effective_at,
        list_available_at=list_record.available_at,
        list_retrieved_at=list_record.retrieved_at,
        identity_operation_id=identity_operation_id,
        identity_provenance_uri=None,
        identity_observed_at=None,
        identity_available_at=None,
        identity_retrieved_at=None,
    )


def _source_list_outcome(
    outcome: _PreparedExchangeListOutcome,
) -> UniverseSourceOutcome:
    return UniverseSourceOutcome(
        operation_id=outcome.operation_id,
        attempted=outcome.attempted,
        source_result=outcome.source_result,
        expected_count=outcome.expected_count,
        observed_count=len(outcome.records),
        pages_expected=outcome.pages_expected,
        pages_received=outcome.pages_received,
    )


def _source_identity_outcome(
    outcome: _PreparedIdentityBatchOutcome,
) -> UniverseSourceOutcome:
    return UniverseSourceOutcome(
        operation_id=outcome.operation_id,
        attempted=outcome.attempted,
        source_result=outcome.source_result,
        expected_count=outcome.expected_count,
        observed_count=len(outcome.records),
    )


def _list_is_complete(outcome: _PreparedExchangeListOutcome) -> bool:
    return (
        outcome.expected_count is not None
        and outcome.source_result in {"complete", "observed_empty"}
        and outcome.pages_expected is not None
        and outcome.pages_expected == outcome.pages_received
        and len(outcome.records) == outcome.expected_count
    )


def _incomplete_list_partition(
    outcome: _PreparedExchangeListOutcome,
) -> UniversePartition:
    if outcome.expected_count is None:
        limitation = "exchange_list_total_unknown"
    elif (
        outcome.pages_expected != outcome.pages_received
        or len(outcome.records) != outcome.expected_count
    ):
        limitation = "incomplete_pagination"
    else:
        limitation = "exchange_list_incomplete"
    return UniversePartition(
        exchange=outcome.exchange,
        complete=False,
        attempted=outcome.attempted,
        source_result=outcome.source_result,
        operation_id=outcome.operation_id,
        expected=outcome.expected_count,
        resolved=0,
        failed=outcome.expected_count,
        securities=tuple(
            _build_unresolved_security(outcome, None, record)
            for record in sorted(outcome.records, key=lambda item: item.code)
        ),
        limitation_codes=(limitation,),
        list_outcome=_source_list_outcome(outcome),
        identity_outcome=None,
    )


def _missing_list_partition(exchange: str) -> UniversePartition:
    return UniversePartition(
        exchange=exchange,
        complete=False,
        attempted=False,
        source_result="unavailable",
        operation_id=None,
        expected=None,
        resolved=0,
        failed=None,
        securities=(),
        limitation_codes=("missing_exchange_list_adapter",),
        list_outcome=None,
        identity_outcome=None,
    )


def _missing_identity_partition(
    list_outcome: _PreparedExchangeListOutcome,
) -> UniversePartition:
    return UniversePartition(
        exchange=list_outcome.exchange,
        complete=False,
        attempted=False,
        source_result="unavailable",
        operation_id=None,
        expected=list_outcome.expected_count,
        resolved=0,
        failed=list_outcome.expected_count,
        securities=tuple(
            _build_unresolved_security(list_outcome, None, record)
            for record in sorted(list_outcome.records, key=lambda item: item.code)
        ),
        limitation_codes=("missing_identity_adapter",),
        list_outcome=_source_list_outcome(list_outcome),
        identity_outcome=None,
    )


def _reconcile_partition(
    request: _PreparedUniverseRequest,
    list_outcome: _PreparedExchangeListOutcome,
    identity_outcome: _PreparedIdentityBatchOutcome,
) -> UniversePartition:
    if (
        list_outcome.expected_count is None
        or identity_outcome.expected_count != list_outcome.expected_count
    ):
        raise _error(
            "identity_denominator_mismatch",
            "An identity outcome does not reconcile to the exchange-list denominator.",
        )
    listings = {record.canonical_security_id: record for record in list_outcome.records}
    identities = {
        record.canonical_security_id: record for record in identity_outcome.records
    }
    unexpected = set(identities).difference(listings)
    if unexpected:
        raise _error(
            "unexpected_resolved_identity",
            "An identity outcome contains a security outside the expected list.",
        )
    for canonical_id, identity in identities.items():
        if identity.name != listings[canonical_id].name:
            raise _error(
                "comparable_identity_conflict",
                "Comparable exchange-list and identity fields conflict.",
            )
    expected = list_outcome.expected_count
    resolved = len(identity_outcome.records)
    securities = tuple(
        (
            _build_security(
                request,
                list_outcome,
                identity_outcome,
                list_record,
                identities[list_record.canonical_security_id],
            )
            if list_record.canonical_security_id in identities
            else _build_unresolved_security(
                list_outcome,
                identity_outcome.operation_id,
                list_record,
            )
        )
        for list_record in sorted(list_outcome.records, key=lambda record: record.code)
    )
    has_unproven_expected_identity = any(
        security.universe_state == "expected_universe"
        and any(
            reason in {"listing_state_unproven", "as_of_unproven"}
            for reason in security.unranked_reasons
        )
        for security in securities
    )
    identity_is_complete = (
        identity_outcome.source_result in {"complete", "observed_empty"}
        and resolved == expected
        and not has_unproven_expected_identity
    )
    return UniversePartition(
        exchange=list_outcome.exchange,
        complete=identity_is_complete,
        attempted=identity_outcome.attempted,
        source_result=identity_outcome.source_result,
        operation_id=identity_outcome.operation_id,
        expected=expected,
        resolved=resolved,
        failed=expected - resolved,
        securities=securities,
        limitation_codes=() if identity_is_complete else ("identity_incomplete",),
        list_outcome=_source_list_outcome(list_outcome),
        identity_outcome=_source_identity_outcome(identity_outcome),
    )


def build_as_of_a_share_universe(
    request: UniverseRequest,
    *,
    research_now: datetime,
    exchange_list_adapters: tuple[ExchangeListAdapter, ...],
    identity_adapters: tuple[IdentityAdapter, ...],
    timeout_seconds: float = 20.0,
) -> AShareUniverse:
    """Prepare, acquire and project the fixed three-exchange universe."""

    prepared_request = _prepare_request(request, research_now)
    timeout_type = type(timeout_seconds)
    if (
        timeout_type not in {int, float}
        or timeout_seconds <= 0
        or (timeout_type is float and not isfinite(timeout_seconds))
    ):
        raise _error(
            "invalid_timeout",
            "Universe Adapter timeout must be positive and finite.",
        )
    if (
        type(exchange_list_adapters) is not tuple
        or type(identity_adapters) is not tuple
    ):
        raise _error(
            "invalid_adapters",
            "Universe Adapter owners must use immutable containers.",
        )
    list_owners = _snapshot_list_owners(
        cast(tuple[object, ...], exchange_list_adapters)
    )
    identity_owners = _snapshot_identity_owners(
        cast(tuple[object, ...], identity_adapters)
    )
    partitions: list[UniversePartition] = []
    for exchange in EXCHANGES:
        list_owner = list_owners.get(exchange)
        if list_owner is None:
            partitions.append(_missing_list_partition(exchange))
            continue
        list_outcome = _invoke_list(list_owner, prepared_request, timeout_seconds)
        if not _list_is_complete(list_outcome):
            partitions.append(_incomplete_list_partition(list_outcome))
            continue
        identity_owner = identity_owners.get(exchange)
        if identity_owner is None:
            partitions.append(_missing_identity_partition(list_outcome))
            continue
        identity_outcome = _invoke_identity(
            identity_owner,
            prepared_request,
            list_outcome.records,
            timeout_seconds,
        )
        partitions.append(
            _reconcile_partition(
                prepared_request,
                list_outcome,
                identity_outcome,
            )
        )
    return AShareUniverse(
        screen_date=prepared_request.screen_date,
        information_cutoff=prepared_request.information_cutoff,
        full_a_share_universe=all(partition.complete for partition in partitions),
        partitions=tuple(partitions),
    )


__all__ = [
    "AShareUniverse",
    "ExchangeListAdapter",
    "IdentityAdapter",
    "UniverseContractError",
    "UniversePartition",
    "UniverseSecurity",
    "UniverseSourceOutcome",
    "build_as_of_a_share_universe",
]
