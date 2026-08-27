"""Pure, detached inputs for the as-of A-share universe Module."""

from __future__ import annotations

import re
from dataclasses import InitVar, dataclass
from datetime import date, datetime, time
from typing import Literal, Sequence, cast
from unicodedata import category as unicode_category
from zoneinfo import ZoneInfo

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGES = frozenset({"SSE", "SZSE", "BSE"})
SOURCE_RESULTS = frozenset(
    {"complete", "observed_empty", "partial", "credential_blocked", "unavailable"}
)
SourceResult = Literal[
    "complete", "observed_empty", "partial", "credential_blocked", "unavailable"
]
ListingState = Literal[
    "listed", "paused_listing", "terminated_listing", "trading_halt", "current"
]
LISTING_STATES = frozenset(
    {"listed", "paused_listing", "terminated_listing", "trading_halt", "current"}
)
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_.:@+-]+$")
_SAFE_PROVENANCE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.+-]+)*$")
_UNSAFE_REFERENCE_MARKERS = (
    "bearer",
    "credential",
    "http",
    "password",
    "secret",
    "token",
)
_PREPARE_AUTHORITY = object()


class UniverseInputError(ValueError):
    """A stable sanitized failure at the Universe Input Module Interface."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class UniverseRequest:
    """Caller-owned time authority to detach before universe work begins."""

    screen_date: str
    information_cutoff: str


@dataclass(frozen=True, slots=True)
class ExchangeListRecord:
    """One caller-owned exchange-list observation."""

    exchange: str
    code: str
    name: str
    security_category: str
    effective_at: str
    available_at: str
    retrieved_at: str
    provenance_uri: str


@dataclass(frozen=True, slots=True)
class ExchangeListOutcome:
    """One caller-owned exchange-list execution outcome."""

    operation_id: str
    exchange: str
    screen_date: str
    attempted: bool
    source_result: SourceResult
    expected_count: int
    pages_expected: int
    pages_received: int
    records: Sequence[ExchangeListRecord]


@dataclass(frozen=True, slots=True)
class UniverseIdentity:
    """One caller-owned exchange security identity observation."""

    exchange: str
    code: str
    name: str
    security_category: str
    listing_state: str | None
    effective_from: str | None
    effective_to: str | None
    observed_at: str
    available_at: str | None
    retrieved_at: str
    provenance_uri: str


@dataclass(frozen=True, slots=True)
class IdentityBatchOutcome:
    """One caller-owned identity execution outcome."""

    operation_id: str
    exchange: str
    attempted: bool
    source_result: SourceResult
    expected_count: int
    records: Sequence[UniverseIdentity]


@dataclass(frozen=True, slots=True)
class _PreparedUniverseRequest:
    screen_date: str
    information_cutoff: str
    _authority: InitVar[object]

    def __post_init__(self, _authority: object) -> None:
        if _authority is not _PREPARE_AUTHORITY:
            raise TypeError("Prepared universe values have no public constructor.")


@dataclass(frozen=True, slots=True)
class _PreparedExchangeListRecord:
    exchange: str
    code: str
    canonical_security_id: str
    name: str
    security_category: str
    effective_at: str
    available_at: str
    retrieved_at: str
    provenance_uri: str
    _authority: InitVar[object]

    def __post_init__(self, _authority: object) -> None:
        if _authority is not _PREPARE_AUTHORITY:
            raise TypeError("Prepared universe values have no public constructor.")


@dataclass(frozen=True, slots=True)
class _PreparedExchangeListOutcome:
    operation_id: str
    exchange: str
    screen_date: str
    attempted: bool
    source_result: SourceResult
    expected_count: int
    pages_expected: int
    pages_received: int
    records: tuple[_PreparedExchangeListRecord, ...]
    _authority: InitVar[object]

    def __post_init__(self, _authority: object) -> None:
        if _authority is not _PREPARE_AUTHORITY:
            raise TypeError("Prepared universe values have no public constructor.")


@dataclass(frozen=True, slots=True)
class _PreparedUniverseIdentity:
    exchange: str
    code: str
    canonical_security_id: str
    name: str
    security_category: str
    listing_state: ListingState | None
    effective_from: str | None
    effective_to: str | None
    observed_at: str
    available_at: str | None
    retrieved_at: str
    provenance_uri: str
    _authority: InitVar[object]

    def __post_init__(self, _authority: object) -> None:
        if _authority is not _PREPARE_AUTHORITY:
            raise TypeError("Prepared universe values have no public constructor.")


@dataclass(frozen=True, slots=True)
class _PreparedIdentityBatchOutcome:
    operation_id: str
    exchange: str
    attempted: bool
    source_result: SourceResult
    expected_count: int
    records: tuple[_PreparedUniverseIdentity, ...]
    _authority: InitVar[object]

    def __post_init__(self, _authority: object) -> None:
        if _authority is not _PREPARE_AUTHORITY:
            raise TypeError("Prepared universe values have no public constructor.")


def _parse_date(value: object) -> date | None:
    if type(value) is not str:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _parse_aware_datetime(value: object) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _invalid_time() -> UniverseInputError:
    return UniverseInputError(
        "invalid_research_time",
        "Universe request time authority is invalid.",
    )


def _error(code: str, message: str) -> UniverseInputError:
    return UniverseInputError(code, message)


def _snapshot_error() -> UniverseInputError:
    return _error(
        "input_snapshot_failed",
        "Universe input snapshot could not be read.",
    )


def _snapshot_properties(
    value: object,
    names: tuple[str, ...],
) -> tuple[object, ...] | None:
    try:
        return tuple(getattr(value, name) for name in names)
    except Exception:
        return None


def _has_runtime_type(value: object, expected_type: type[object]) -> bool:
    return issubclass(type(value), expected_type)


def _exact_exchange(value: object) -> str:
    if type(value) is not str or value not in EXCHANGES:
        raise _error("invalid_exchange", "Universe exchange identity is invalid.")
    return value


def _safe_operation_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or _SAFE_OPERATION_ID.fullmatch(value) is None
        or any(marker in value.casefold() for marker in _UNSAFE_REFERENCE_MARKERS)
    ):
        raise _error(
            "invalid_operation_identity",
            "Universe operation identity is invalid.",
        )
    return value


def _safe_name(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or value != value.strip()
        or "/" in value
        or "\\" in value
        or any(unicode_category(character).startswith("C") for character in value)
    ):
        raise _error("invalid_security_name", "Universe security name is invalid.")
    return value


def _canonical_code(exchange: str, value: object) -> tuple[str, str]:
    if (
        type(value) is not str
        or len(value) != 6
        or not value.isascii()
        or not value.isdigit()
    ):
        raise _error("invalid_security_code", "Universe security code is invalid.")
    return value, f"{exchange}:{value}"


def _safe_provenance(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or _SAFE_PROVENANCE.fullmatch(value) is None
        or any(marker in value.casefold() for marker in _UNSAFE_REFERENCE_MARKERS)
    ):
        raise _error(
            "invalid_provenance",
            "Universe provenance is invalid.",
        )
    return value


def _normalized_datetime(value: object, code: str) -> tuple[datetime, str]:
    parsed = _parse_aware_datetime(value)
    if parsed is None:
        raise _error(code, "Universe time evidence is invalid.")
    normalized = parsed.astimezone(ASIA_SHANGHAI)
    return normalized, normalized.isoformat()


def _normalized_effective(value: object) -> tuple[date, str]:
    parsed_date = _parse_date(value)
    if parsed_date is not None:
        return parsed_date, parsed_date.isoformat()
    parsed_time = _parse_aware_datetime(value)
    if parsed_time is None:
        raise _error(
            "missing_effective_time",
            "Universe effective-time evidence is invalid.",
        )
    normalized = parsed_time.astimezone(ASIA_SHANGHAI)
    return normalized.date(), normalized.isoformat()


def _exact_nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise _error(
            "invalid_reconciliation",
            "Universe reconciliation counts are invalid.",
        )
    return value


def _snapshot_records(value: object) -> tuple[object, ...]:
    if type(value) not in {list, tuple}:
        raise _error(
            "invalid_records",
            "Universe outcome records must use a supported container.",
        )
    try:
        detached = tuple(cast(Sequence[object], value))
    except Exception:
        detached = None
    if detached is None:
        raise _snapshot_error()
    return detached


def _validate_list_state(
    *,
    attempted: object,
    source_result: object,
    expected_count: int,
    pages_expected: int,
    pages_received: int,
    record_count: int,
) -> tuple[bool, SourceResult]:
    if type(attempted) is not bool:
        raise _error("invalid_attempted", "Universe execution fact is invalid.")
    if type(source_result) is not str or source_result not in SOURCE_RESULTS:
        raise _error(
            "invalid_source_result", "Universe source-result state is invalid."
        )
    if (
        pages_expected < 1
        or pages_received > pages_expected
        or record_count > expected_count
        or (pages_received == 0 and record_count != 0)
    ):
        raise _error(
            "invalid_reconciliation",
            "Universe reconciliation counts are invalid.",
        )
    complete_query = pages_received == pages_expected
    valid = False
    if source_result == "complete":
        valid = (
            attempted
            and expected_count > 0
            and record_count == expected_count
            and complete_query
        )
    elif source_result == "observed_empty":
        valid = (
            attempted and expected_count == 0 and record_count == 0 and complete_query
        )
    elif source_result == "partial":
        valid = (
            attempted
            and expected_count > 0
            and (record_count < expected_count or not complete_query)
        )
    else:
        valid = record_count == 0 and pages_received == 0
    if not valid:
        raise _error(
            "source_result_conflict",
            "Universe source-result facts are contradictory.",
        )
    return attempted, cast(SourceResult, source_result)


def _validate_identity_state(
    *,
    attempted: object,
    source_result: object,
    expected_count: int,
    record_count: int,
) -> tuple[bool, SourceResult]:
    if type(attempted) is not bool:
        raise _error("invalid_attempted", "Universe execution fact is invalid.")
    if type(source_result) is not str or source_result not in SOURCE_RESULTS:
        raise _error(
            "invalid_source_result", "Universe source-result state is invalid."
        )
    if record_count > expected_count:
        raise _error(
            "invalid_reconciliation",
            "Universe reconciliation counts are invalid.",
        )
    valid = False
    if source_result == "complete":
        valid = attempted and expected_count > 0 and record_count == expected_count
    elif source_result == "observed_empty":
        valid = attempted and expected_count == 0 and record_count == 0
    elif source_result == "partial":
        valid = attempted and expected_count > 0 and record_count < expected_count
    else:
        valid = record_count == 0
    if not valid:
        raise _error(
            "source_result_conflict",
            "Universe source-result facts are contradictory.",
        )
    return attempted, cast(SourceResult, source_result)


def _listing_state(value: object) -> ListingState | None:
    if value is None or (type(value) is str and not value.strip()):
        return None
    if type(value) is not str:
        raise _error("invalid_listing_state", "Universe listing state is invalid.")
    if value != value.strip():
        raise _error("invalid_listing_state", "Universe listing state is invalid.")
    if value not in LISTING_STATES:
        return None
    return cast(ListingState, value)


def _optional_date(value: object) -> str | None:
    if value is None:
        return None
    parsed = _parse_date(value)
    if parsed is None:
        raise _error(
            "missing_effective_time",
            "Universe effective-time evidence is invalid.",
        )
    return parsed.isoformat()


def prepare_universe_request(
    request: UniverseRequest,
    *,
    research_now: datetime,
) -> _PreparedUniverseRequest:
    """Read, validate and detach one universe request exactly once."""

    if (
        not _has_runtime_type(request, UniverseRequest)
        or type(research_now) is not datetime
    ):
        raise _invalid_time()
    request_snapshot = _snapshot_properties(
        request,
        ("screen_date", "information_cutoff"),
    )
    if request_snapshot is None:
        raise _snapshot_error()
    screen_value, cutoff_value = request_snapshot
    screen_date = _parse_date(screen_value)
    cutoff = _parse_aware_datetime(cutoff_value)
    if (
        screen_date is None
        or cutoff is None
        or research_now.tzinfo is None
        or research_now.utcoffset() is None
    ):
        raise _invalid_time()
    normalized_now = research_now.astimezone(ASIA_SHANGHAI)
    normalized_cutoff = cutoff.astimezone(ASIA_SHANGHAI)
    if (
        normalized_cutoff > normalized_now
        or screen_date > normalized_cutoff.date()
        or (
            normalized_cutoff.date() < normalized_now.date()
            and normalized_cutoff.timetz().replace(tzinfo=None) != time(23, 59, 59)
        )
        or (
            normalized_cutoff.date() == normalized_now.date()
            and normalized_cutoff != normalized_now
        )
    ):
        raise _invalid_time()
    return _PreparedUniverseRequest(
        screen_date=screen_date.isoformat(),
        information_cutoff=normalized_cutoff.isoformat(),
        _authority=_PREPARE_AUTHORITY,
    )


def _prepare_list_record(
    value: object,
    *,
    outcome_exchange: str,
    screen_date: date,
) -> _PreparedExchangeListRecord:
    if not _has_runtime_type(value, ExchangeListRecord):
        raise _error("invalid_record", "Universe exchange-list record is invalid.")
    record_snapshot = _snapshot_properties(
        value,
        (
            "exchange",
            "code",
            "name",
            "security_category",
            "effective_at",
            "available_at",
            "retrieved_at",
            "provenance_uri",
        ),
    )
    if record_snapshot is None:
        raise _snapshot_error()
    (
        exchange_value,
        code_value,
        name_value,
        category_value,
        effective_value,
        available_value,
        retrieved_value,
        provenance_value,
    ) = record_snapshot

    exchange = _exact_exchange(exchange_value)
    if exchange != outcome_exchange:
        raise _error(
            "cross_exchange_identity_conflict",
            "Universe record exchange identity is contradictory.",
        )
    code, canonical_id = _canonical_code(exchange, code_value)
    name = _safe_name(name_value)
    if type(category_value) is not str or category_value != "A_SHARE":
        raise _error(
            "non_a_share_contamination",
            "Universe record security category is invalid.",
        )
    effective_date, effective_at = _normalized_effective(effective_value)
    if effective_date > screen_date:
        raise _error(
            "historical_list_scope_mismatch",
            "Universe exchange-list scope is invalid.",
        )
    available, available_at = _normalized_datetime(
        available_value, "missing_effective_time"
    )
    retrieved, retrieved_at = _normalized_datetime(
        retrieved_value, "missing_effective_time"
    )
    if available > retrieved:
        raise _error(
            "invalid_evidence_chronology",
            "Universe evidence chronology is invalid.",
        )
    return _PreparedExchangeListRecord(
        exchange=exchange,
        code=code,
        canonical_security_id=canonical_id,
        name=name,
        security_category="A_SHARE",
        effective_at=effective_at,
        available_at=available_at,
        retrieved_at=retrieved_at,
        provenance_uri=_safe_provenance(provenance_value),
        _authority=_PREPARE_AUTHORITY,
    )


def prepare_exchange_list_outcome(
    outcome: ExchangeListOutcome,
) -> _PreparedExchangeListOutcome:
    """Read, validate and detach one exchange-list outcome exactly once."""

    if not _has_runtime_type(outcome, ExchangeListOutcome):
        raise _error("invalid_outcome", "Universe exchange-list outcome is invalid.")
    outcome_snapshot = _snapshot_properties(
        outcome,
        (
            "operation_id",
            "exchange",
            "screen_date",
            "attempted",
            "source_result",
            "expected_count",
            "pages_expected",
            "pages_received",
            "records",
        ),
    )
    if outcome_snapshot is None:
        raise _snapshot_error()
    (
        operation_value,
        exchange_value,
        screen_value,
        attempted_value,
        source_result_value,
        expected_value,
        pages_expected_value,
        pages_received_value,
        records_value,
    ) = outcome_snapshot

    operation_id = _safe_operation_id(operation_value)
    exchange = _exact_exchange(exchange_value)
    screen_date = _parse_date(screen_value)
    if screen_date is None:
        raise _error(
            "historical_list_scope_mismatch",
            "Universe exchange-list scope is invalid.",
        )
    expected_count = _exact_nonnegative_int(expected_value)
    pages_expected = _exact_nonnegative_int(pages_expected_value)
    pages_received = _exact_nonnegative_int(pages_received_value)
    detached_records = _snapshot_records(records_value)
    if len({id(record) for record in detached_records}) != len(detached_records):
        raise _error(
            "duplicate_canonical_identity",
            "Universe exchange-list identity is duplicated.",
        )
    attempted, source_result = _validate_list_state(
        attempted=attempted_value,
        source_result=source_result_value,
        expected_count=expected_count,
        pages_expected=pages_expected,
        pages_received=pages_received,
        record_count=len(detached_records),
    )
    records = tuple(
        _prepare_list_record(
            record,
            outcome_exchange=exchange,
            screen_date=screen_date,
        )
        for record in detached_records
    )
    canonical_ids = [record.canonical_security_id for record in records]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise _error(
            "duplicate_canonical_identity",
            "Universe exchange-list identity is duplicated.",
        )
    return _PreparedExchangeListOutcome(
        operation_id=operation_id,
        exchange=exchange,
        screen_date=screen_date.isoformat(),
        attempted=attempted,
        source_result=source_result,
        expected_count=expected_count,
        pages_expected=pages_expected,
        pages_received=pages_received,
        records=records,
        _authority=_PREPARE_AUTHORITY,
    )


def _prepare_identity_record(
    value: object,
    *,
    outcome_exchange: str,
) -> _PreparedUniverseIdentity:
    if not _has_runtime_type(value, UniverseIdentity):
        raise _error("invalid_record", "Universe identity record is invalid.")
    record_snapshot = _snapshot_properties(
        value,
        (
            "exchange",
            "code",
            "name",
            "security_category",
            "listing_state",
            "effective_from",
            "effective_to",
            "observed_at",
            "available_at",
            "retrieved_at",
            "provenance_uri",
        ),
    )
    if record_snapshot is None:
        raise _snapshot_error()
    (
        exchange_value,
        code_value,
        name_value,
        category_value,
        listing_state_value,
        effective_from_value,
        effective_to_value,
        observed_value,
        available_value,
        retrieved_value,
        provenance_value,
    ) = record_snapshot

    exchange = _exact_exchange(exchange_value)
    if exchange != outcome_exchange:
        raise _error(
            "cross_exchange_identity_conflict",
            "Universe record exchange identity is contradictory.",
        )
    code, canonical_id = _canonical_code(exchange, code_value)
    name = _safe_name(name_value)
    if type(category_value) is not str or category_value != "A_SHARE":
        raise _error(
            "non_a_share_contamination",
            "Universe record security category is invalid.",
        )
    listing_state = _listing_state(listing_state_value)
    effective_from = _optional_date(effective_from_value)
    effective_to = _optional_date(effective_to_value)
    if (
        effective_from is not None
        and effective_to is not None
        and effective_from > effective_to
    ):
        raise _error(
            "invalid_effective_interval",
            "Universe effective interval is invalid.",
        )
    observed, observed_at = _normalized_datetime(
        observed_value, "missing_effective_time"
    )
    retrieved, retrieved_at = _normalized_datetime(
        retrieved_value, "missing_effective_time"
    )
    available: datetime | None = None
    available_at: str | None = None
    if available_value is not None:
        available, available_at = _normalized_datetime(
            available_value, "missing_effective_time"
        )
    if observed > retrieved or (
        available is not None and (observed > available or available > retrieved)
    ):
        raise _error(
            "invalid_evidence_chronology",
            "Universe evidence chronology is invalid.",
        )
    return _PreparedUniverseIdentity(
        exchange=exchange,
        code=code,
        canonical_security_id=canonical_id,
        name=name,
        security_category="A_SHARE",
        listing_state=listing_state,
        effective_from=effective_from,
        effective_to=effective_to,
        observed_at=observed_at,
        available_at=available_at,
        retrieved_at=retrieved_at,
        provenance_uri=_safe_provenance(provenance_value),
        _authority=_PREPARE_AUTHORITY,
    )


def prepare_identity_outcome(
    outcome: IdentityBatchOutcome,
) -> _PreparedIdentityBatchOutcome:
    """Read, validate and detach one identity outcome exactly once."""

    if not _has_runtime_type(outcome, IdentityBatchOutcome):
        raise _error("invalid_outcome", "Universe identity outcome is invalid.")
    outcome_snapshot = _snapshot_properties(
        outcome,
        (
            "operation_id",
            "exchange",
            "attempted",
            "source_result",
            "expected_count",
            "records",
        ),
    )
    if outcome_snapshot is None:
        raise _snapshot_error()
    (
        operation_value,
        exchange_value,
        attempted_value,
        source_result_value,
        expected_value,
        records_value,
    ) = outcome_snapshot

    operation_id = _safe_operation_id(operation_value)
    exchange = _exact_exchange(exchange_value)
    expected_count = _exact_nonnegative_int(expected_value)
    detached_records = _snapshot_records(records_value)
    if len({id(record) for record in detached_records}) != len(detached_records):
        raise _error(
            "duplicate_canonical_identity",
            "Universe identity is duplicated.",
        )
    attempted, source_result = _validate_identity_state(
        attempted=attempted_value,
        source_result=source_result_value,
        expected_count=expected_count,
        record_count=len(detached_records),
    )
    records = tuple(
        _prepare_identity_record(record, outcome_exchange=exchange)
        for record in detached_records
    )
    canonical_ids = [record.canonical_security_id for record in records]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise _error(
            "duplicate_canonical_identity",
            "Universe identity is duplicated.",
        )
    return _PreparedIdentityBatchOutcome(
        operation_id=operation_id,
        exchange=exchange,
        attempted=attempted,
        source_result=source_result,
        expected_count=expected_count,
        records=records,
        _authority=_PREPARE_AUTHORITY,
    )


__all__ = [
    "ExchangeListOutcome",
    "ExchangeListRecord",
    "IdentityBatchOutcome",
    "UniverseInputError",
    "UniverseIdentity",
    "UniverseRequest",
    "prepare_exchange_list_outcome",
    "prepare_identity_outcome",
    "prepare_universe_request",
]
