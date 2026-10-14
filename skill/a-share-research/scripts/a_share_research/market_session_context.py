"""Calendar-backed regular-auction sessions and frozen screening context.

This internal Module has no provider or filesystem side effects. Calendar
Adapters own proof acquisition; #108 owns persisted byte/ownership integrity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

from .research_result import FieldDisposition, OperationExecution, OperationOutcome

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGES = ("SSE", "SZSE", "BSE")
OPERATION_ID = "market_session_context@1"
CAPABILITY_ID = "market_trading_session"


@dataclass(frozen=True)
class CalendarDay:
    session_date: str
    status: Literal["trading", "weekend", "holiday"]


@dataclass(frozen=True)
class CalendarEvidence:
    """One detached exchange calendar and its regular-auction rule proof.

    A proved range explicitly covers every date; a weekend is calendar evidence,
    not permission to infer the neighboring trading day from the clock.
    """

    exchange: str
    evidence_id: str
    version: str
    provenance_uri: str
    available_at: str
    retrieved_at: str
    effective_from: str
    effective_to: str
    coverage_start: str
    coverage_end: str
    authority: Literal["proved", "unknown", "stale"]
    days: tuple[CalendarDay, ...]
    auction_times: tuple[str, ...]
    rule_evidence_id: str
    rule_provenance_uri: str
    synthetic: bool = False
    trading_day_completion_time: str | None = None
    completion_evidence_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class ContextBindings:
    """Internal identities supplied by orchestration, never ResearchTask fields."""

    request_digest: str
    producer_digest: str
    plan_digest: str
    operation_manifest_digest: str
    artifact_id: str


@dataclass(frozen=True)
class MarketSessionOperationRequest:
    requested_as_of: str
    bindings: ContextBindings
    frozen_context: Mapping[str, object] | None = None
    expected_context_digest: str | None = None


class DeterministicMarketSessionAdapter:
    """The operation Adapter with injected aware clock and calendar evidence."""

    def __init__(
        self,
        calendars: Sequence[CalendarEvidence],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._calendars = tuple(
            replace(
                item, days=tuple(item.days), auction_times=tuple(item.auction_times)
            )
            for item in calendars
        )
        self._clock = clock

    def execute(
        self, request: MarketSessionOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("session timeout must be finite and positive")
        try:
            now = _aware(self._clock())
            requested = _date(request.requested_as_of)
            if requested > now.date():
                raise _ContextError("future_requested_date")
            cutoff = (
                now
                if requested == now.date()
                else datetime.combine(requested, time(23, 59, 59), ASIA_SHANGHAI)
            )
            bindings = asdict(request.bindings)
            _validate_bindings(bindings)
            if request.frozen_context is None:
                if request.expected_context_digest is not None:
                    raise _ContextError("context_digest_mismatch")
                original_now = now
                ordered = _validate_calendars(self._calendars, cutoff, original_now)
                context = _create_context(
                    request.requested_as_of, bindings, original_now, cutoff, ordered
                )
            else:
                context, original_now, cutoff, ordered = self._restore(
                    request, bindings, now
                )
            _validate_calendars(ordered, original_now, original_now)
            phase = _phase(ordered[0], original_now)
            return _outcome(
                {
                    "session": {
                        "scope": "regular_auction",
                        "phase": phase,
                        "phase_instant": original_now.isoformat(),
                        "is_current_phase": original_now == now,
                        "session_date": original_now.date().isoformat(),
                        "exchanges": list(EXCHANGES),
                        "current_session_applicable": original_now == now
                        and phase
                        in {
                            "opening_auction",
                            "continuous_auction",
                            "midday_break",
                            "closing_auction",
                        },
                        "freshness_basis": "active_session"
                        if original_now == now
                        and phase
                        in {"opening_auction", "continuous_auction", "closing_auction"}
                        else "completed_session",
                        "latest_completed_session": _latest_completed(
                            ordered, original_now
                        ),
                    },
                    "context": context,
                },
                now,
            )
        except _ContextError as error:
            return _failure(error.code)

    def _restore(
        self,
        request: MarketSessionOperationRequest,
        bindings: Mapping[str, object],
        now: datetime,
    ) -> tuple[dict[str, object], datetime, datetime, tuple[CalendarEvidence, ...]]:
        # Consume only persisted values and already supplied calendar authority.
        # No new calendar/source acquisition is allowed during semantic checks.
        from .bounded_public_data import validate_bounded_public_data

        try:
            validate_bounded_public_data(
                request.frozen_context,
                artifact="frozen session context",
                allow_floats=False,
            )
            snapshot = json.loads(json.dumps(request.frozen_context, allow_nan=False))
            if not isinstance(snapshot, dict) or set(snapshot) != {
                "schema_version",
                "requested_as_of",
                "research_now",
                "information_cutoff",
                "screen_date",
                "calendar_proofs",
                "bindings",
                "digest",
            }:
                raise _ContextError("context_semantic_mismatch")
            digest = snapshot.pop("digest")
            if (
                not isinstance(request.expected_context_digest, str)
                or re.fullmatch(r"[a-f0-9]{64}", request.expected_context_digest)
                is None
                or digest != request.expected_context_digest
                or digest != _digest(snapshot)
            ):
                raise _ContextError("context_digest_mismatch")
            snapshot["digest"] = digest
            if (
                snapshot["requested_as_of"] != request.requested_as_of
                or snapshot["bindings"] != bindings
            ):
                raise _ContextError("context_binding_mismatch")
            original_now = _aware(snapshot["research_now"])
            if original_now > now:
                raise _ContextError("invalid_timestamp")
            requested = _date(request.requested_as_of)
            if requested > original_now.date():
                raise _ContextError("future_requested_date")
            cutoff = (
                original_now
                if requested == original_now.date()
                else datetime.combine(requested, time(23, 59, 59), ASIA_SHANGHAI)
            )
            ordered = _validate_calendars(self._calendars, cutoff, original_now)
            expected = _create_context(
                request.requested_as_of, bindings, original_now, cutoff, ordered
            )
            if snapshot != json.loads(json.dumps(expected)):
                raise _ContextError("context_semantic_mismatch")
            return snapshot, original_now, cutoff, ordered
        except _ContextError:
            raise
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise _ContextError("context_semantic_mismatch") from error


class _ContextError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _aware(value: datetime | str) -> datetime:
    try:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() is None
        ):
            raise ValueError
        return instant.astimezone(ASIA_SHANGHAI)
    except (ValueError, TypeError, OverflowError) as error:
        raise _ContextError("invalid_timestamp") from error


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError) as error:
        raise _ContextError("invalid_session_date") from error


def _validate_bindings(bindings: Mapping[str, object]) -> None:
    if set(bindings) != {
        "request_digest",
        "producer_digest",
        "plan_digest",
        "operation_manifest_digest",
        "artifact_id",
    }:
        raise _ContextError("context_binding_mismatch")
    for key, value in bindings.items():
        pattern = (
            r"[a-f0-9]{64}" if key.endswith("digest") else r"[A-Za-z0-9_.:-]{1,128}"
        )
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise _ContextError("context_binding_mismatch")


def _validate_calendars(
    calendars: Sequence[CalendarEvidence], cutoff: datetime, now: datetime
) -> tuple[CalendarEvidence, ...]:
    if len(calendars) != 3 or {item.exchange for item in calendars} != set(EXCHANGES):
        raise _ContextError("calendar_exchange_gap")
    ordered = tuple(
        next(item for item in calendars if item.exchange == exchange)
        for exchange in EXCHANGES
    )
    for calendar in ordered:
        for locator in (calendar.provenance_uri, calendar.rule_provenance_uri):
            _safe_reference(locator)
            if locator.startswith("fixture:") and not calendar.synthetic:
                raise _ContextError("calendar_reference_unproved")
        for identity in (
            calendar.evidence_id,
            calendar.version,
            calendar.rule_evidence_id,
        ):
            if (
                not isinstance(identity, str)
                or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identity) is None
            ):
                raise _ContextError("calendar_reference_unproved")
        if calendar.authority != "proved":
            raise _ContextError("calendar_authority_unproved")
        if not (
            _date(calendar.effective_from)
            <= cutoff.date()
            <= _date(calendar.effective_to)
        ):
            raise _ContextError("calendar_authority_unproved")
        start, end = _date(calendar.coverage_start), _date(calendar.coverage_end)
        if not start <= cutoff.date() <= end or not 0 <= (end - start).days < 732:
            raise _ContextError("calendar_coverage_gap")
        if _date(calendar.effective_from) > start:
            raise _ContextError("calendar_authority_unproved")
        expected_dates = tuple(
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        )
        if tuple(item.session_date for item in calendar.days) != expected_dates or any(
            item.status not in {"trading", "holiday", "weekend"}
            for item in calendar.days
        ):
            raise _ContextError("calendar_coverage_gap")
        if any(
            item.status == "weekend" and _date(item.session_date).weekday() < 5
            for item in calendar.days
        ):
            raise _ContextError("calendar_session_conflict")
        if (
            _aware(calendar.retrieved_at) > now
            or _aware(calendar.available_at) > cutoff
        ):
            raise _ContextError("calendar_timestamp_unproved")
        try:
            times = tuple(time.fromisoformat(value) for value in calendar.auction_times)
            if (
                len(times) != 7
                or tuple(sorted(set(times))) != times
                or any(value.tzinfo is not None for value in times)
            ):
                raise ValueError
        except (ValueError, TypeError) as error:
            raise _ContextError("calendar_rule_unproved") from error
        if (
            calendar.trading_day_completion_time is None
            or not calendar.completion_evidence_id
        ):
            raise _ContextError("trading_day_completion_unproved")
        try:
            completion = time.fromisoformat(calendar.trading_day_completion_time)
            if completion.tzinfo is not None or completion < times[-1]:
                raise ValueError
        except (ValueError, TypeError) as error:
            raise _ContextError("trading_day_completion_unproved") from error
    start = max(_date(item.coverage_start) for item in ordered)
    states = [
        dict((day.session_date, day.status) for day in item.days) for item in ordered
    ]
    for offset in range((cutoff.date() - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        if len({state[day] for state in states}) != 1:
            raise _ContextError("calendar_session_conflict")
    if (
        len({item.auction_times for item in ordered}) != 1
        or len({item.trading_day_completion_time for item in ordered}) != 1
    ):
        raise _ContextError("calendar_session_conflict")
    return ordered


def _latest_completed(calendars: tuple[CalendarEvidence, ...], cutoff: datetime) -> str:
    completed_sets = []
    for calendar in calendars:
        completion_time = time.fromisoformat(
            cast(str, calendar.trading_day_completion_time)
        )
        completed_sets.append(
            {
                day.session_date
                for day in calendar.days
                if day.status == "trading"
                and datetime.combine(
                    _date(day.session_date), completion_time, ASIA_SHANGHAI
                )
                <= cutoff
            }
        )
    completed = set.intersection(*completed_sets)
    if not completed:
        raise _ContextError("calendar_coverage_gap")
    return max(completed)


def _create_context(
    requested: str,
    bindings: Mapping[str, object],
    original_now: datetime,
    cutoff: datetime,
    calendars: tuple[CalendarEvidence, ...],
) -> dict[str, object]:
    context: dict[str, object] = {
        "schema_version": "1.0",
        "requested_as_of": requested,
        "research_now": original_now.isoformat(),
        "information_cutoff": cutoff.isoformat(),
        "screen_date": _latest_completed(calendars, cutoff),
        "calendar_proofs": [item.to_dict() for item in calendars],
        "bindings": dict(bindings),
    }
    context["digest"] = _digest(context)
    return context


def _failure(code: str) -> OperationOutcome:
    return OperationOutcome(
        CAPABILITY_ID,
        OPERATION_ID,
        OperationExecution("failed"),
        source_errors=(
            {
                "source_operation": OPERATION_ID,
                "code": code,
                "message": "Calendar-backed session context could not be proved.",
            },
        ),
        field_dispositions=(
            FieldDisposition("payload", "normalized", "observations[].payload"),
        ),
    )


def _phase(calendar: CalendarEvidence, instant: datetime) -> str:
    day = next(
        (
            item
            for item in calendar.days
            if item.session_date == instant.date().isoformat()
        ),
        None,
    )
    if day is None:
        raise _ContextError("calendar_coverage_gap")
    if day.status != "trading":
        return day.status
    boundaries = tuple(time.fromisoformat(value) for value in calendar.auction_times)
    phases = (
        "pre_open",
        "opening_auction",
        "opening_intermission",
        "continuous_auction",
        "midday_break",
        "continuous_auction",
        "closing_auction",
    )
    local_time = instant.time()
    for boundary, phase in zip(boundaries, phases, strict=True):
        if local_time < boundary:
            return phase
    return "post_close"


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _outcome(payload: Mapping[str, object], now: datetime) -> OperationOutcome:
    return OperationOutcome(
        CAPABILITY_ID,
        OPERATION_ID,
        OperationExecution("succeeded", True),
        observations=(
            {
                "source_operation": OPERATION_ID,
                "data_type": "market_session_context",
                "retrieved_at": now.isoformat(),
                "payload": payload,
            },
        ),
        field_dispositions=(
            FieldDisposition("payload", "normalized", "observations[].payload"),
        ),
    )


@dataclass(frozen=True)
class ExchangeCalendarDocument:
    """Bounded extracted document text, not a qualified provider wire shape."""

    exchange: str
    year: int
    holiday_text: str
    rule_text: str
    holiday_locator: str
    rule_locator: str
    retrieved_at: str
    synthetic: bool = False


class ExchangeCalendarDocumentAdapter:
    """Parse declared SSE/SZSE facts while retaining incomplete authority.

    Text extraction, transport, licensing, exceptions and full-day completion
    remain source-owner obligations. This Adapter does not acquire documents
    and is not registered as a default production owner.
    """

    def __init__(
        self,
        documents: Sequence[ExchangeCalendarDocument],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._documents = tuple(documents)
        self._clock = clock

    def execute(
        self, request: MarketSessionOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("session timeout must be finite and positive")
        try:
            now = _aware(self._clock())
            if _date(request.requested_as_of) > now.date():
                raise _ContextError("future_requested_date")
            _validate_bindings(asdict(request.bindings))
            if request.frozen_context is not None:
                # No parsed document subset may replace the original C.
                raise _ContextError("calendar_authority_unproved")
            if len(self._documents) > 3 or len(
                {item.exchange for item in self._documents}
            ) != len(self._documents):
                raise _ContextError("calendar_session_conflict")
        except _ContextError as error:
            return _failure(error.code)
        observations: list[Mapping[str, object]] = []
        errors: list[Mapping[str, object]] = []
        for document in self._documents:
            try:
                payload = _parse_document(document, now)
                observations.append(
                    {
                        "source_operation": OPERATION_ID,
                        "data_type": "declared_exchange_calendar",
                        "retrieved_at": document.retrieved_at,
                        "payload": payload,
                    }
                )
            except _ContextError as error:
                errors.append(
                    {
                        "source_operation": OPERATION_ID,
                        "code": error.code,
                        "message": "Declared exchange document facts could not be parsed.",
                    }
                )
        errors.append(
            {
                "source_operation": OPERATION_ID,
                "code": "calendar_authority_unproved",
                "message": "Declared document facts do not prove complete calendar or trading-day authority.",
            }
        )
        if {item.exchange for item in self._documents} != set(EXCHANGES):
            errors.append(
                {
                    "source_operation": OPERATION_ID,
                    "code": "calendar_exchange_gap",
                    "message": "A required exchange calendar remains unproved.",
                }
            )
        return OperationOutcome(
            CAPABILITY_ID,
            OPERATION_ID,
            OperationExecution("failed"),
            observations=tuple(observations),
            source_errors=tuple(errors),
            field_dispositions=(
                FieldDisposition("payload", "normalized", "observations[].payload"),
            ),
        )


def _safe_reference(value: str) -> None:
    from urllib.parse import urlsplit

    if (
        not isinstance(value, str)
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise _ContextError("calendar_reference_unproved")
    if re.fullmatch(r"fixture:[A-Za-z0-9:._-]+", value):
        return
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise _ContextError("calendar_reference_unproved") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {"www.sse.com.cn", "www.szse.cn", "docs.static.szse.cn", "www.bse.cn"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise _ContextError("calendar_reference_unproved")


def _parse_document(
    document: ExchangeCalendarDocument, now: datetime
) -> dict[str, object]:
    if (
        document.exchange not in {"SSE", "SZSE"}
        or type(document.year) is not int
        or not 1900 <= document.year <= 9999
    ):
        raise _ContextError("calendar_document_scope_unproved")
    for text in (document.holiday_text, document.rule_text):
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65_536:
            raise _ContextError("calendar_document_invalid")
    _safe_reference(document.holiday_locator)
    _safe_reference(document.rule_locator)
    if _aware(document.retrieved_at) > now:
        raise _ContextError("calendar_timestamp_unproved")
    holiday_text = re.sub(r"\s+", "", document.holiday_text)
    if f"{document.year}年" not in holiday_text:
        raise _ContextError("calendar_document_scope_unproved")
    closures: list[dict[str, str]] = []
    for label in ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"):
        match = re.search(
            rf"{label}[：:]([0-9]{{1,2}})月([0-9]{{1,2}})日[^。]*?至([0-9]{{1,2}})月([0-9]{{1,2}})日[^。]*?休市[^。]*?([0-9]{{1,2}})月([0-9]{{1,2}})日[^。]*?起照常开市",
            holiday_text,
        )
        if match is None:
            raise _ContextError("calendar_document_invalid")
        try:
            values = tuple(int(value) for value in match.groups())
            start, end, reopen = (
                date(document.year, values[index], values[index + 1])
                for index in (0, 2, 4)
            )
            if not start <= end < reopen:
                raise ValueError
        except ValueError as error:
            raise _ContextError("calendar_document_invalid") from error
        closures.append(
            {
                "name": label,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "declared_reopening_date": reopen.isoformat(),
            }
        )
    rules = re.sub(r"\s+", "", document.rule_text)
    phase_section, effect_section = (
        ("2.4.2", "11.9") if document.exchange == "SSE" else ("2.3.2", "10.9")
    )
    phase_match = re.search(
        rf"{re.escape(phase_section)}[^。]*?([0-9]{{1,2}}:[0-9]{{2}})至([0-9]{{1,2}}:[0-9]{{2}})为开盘集合竞价时间[^。]*?([0-9]{{1,2}}:[0-9]{{2}})至([0-9]{{1,2}}:[0-9]{{2}})、([0-9]{{1,2}}:[0-9]{{2}})至([0-9]{{1,2}}:[0-9]{{2}})为连续竞价时间[^。]*?([0-9]{{1,2}}:[0-9]{{2}})至([0-9]{{1,2}}:[0-9]{{2}})为收盘集合竞价时间",
        rules,
    )
    effect_match = re.search(
        rf"{re.escape(effect_section)}本规则自([0-9]{{4}})年([0-9]{{1,2}})月([0-9]{{1,2}})日起施行",
        rules,
    )
    if phase_match is None or effect_match is None:
        raise _ContextError("calendar_rule_unproved")
    try:
        raw_times = phase_match.groups()
        times = tuple(
            time.fromisoformat(value.zfill(5)).isoformat(timespec="minutes")
            for value in raw_times
        )
        if times[5] != times[6] or tuple(sorted(set(times))) != times[:6] + times[7:]:
            raise ValueError
        effective = date(*(int(value) for value in effect_match.groups()))
    except ValueError as error:
        raise _ContextError("calendar_rule_unproved") from error
    return {
        "exchange": document.exchange,
        "year": document.year,
        "synthetic": document.synthetic,
        "holiday_locator": document.holiday_locator,
        "rule_locator": document.rule_locator,
        "declared_holiday_closures": closures,
        "declared_auction_times": list(times[:6] + times[7:]),
        "declared_rule_effective_date": effective.isoformat(),
        "phase_rule_section": phase_section,
        "effect_rule_section": effect_section,
        "source_authority": "unproved",
        "available_at": None,
        "proof_gaps": [
            "first_availability_unproved",
            "temporary_exceptions_unproved",
            "rule_deferral_scope_unproved",
            "complete_calendar_range_unproved",
            "trading_day_completion_unproved",
        ],
    }
