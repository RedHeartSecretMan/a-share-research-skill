"""Build a versioned, deterministic intraday replay tracer result."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Collection

from .intraday_daily_boundary import (
    DailyBoundaryMinute,
    assess_daily_boundary,
    build_unavailable_boundary,
    normalize_price_to_tick,
    validate_daily_batch,
)
from .intraday_replay_contract import (
    IntradayReplayDailySourceBatch,
    IntradayReplayDailySourceOperation,
    IntradayReplayQuery,
    IntradayReplaySourceBatch,
    IntradayReplaySourceError,
    IntradayReplaySourceOperation,
    IntradayReplaySourceRow,
    source_error_result,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
SSE_A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZSE_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301")
_TIMESTAMP_SEMANTICS = frozenset({"interval_start", "interval_end"})
_TRADE_STATES = frozenset({"traded", "no_trade"})
_TRADING_PHASES = frozenset(
    {
        "continuous",
        "continuous_morning",
        "continuous_afternoon",
        "opening_auction",
        "closing_auction",
        "midday_break",
    }
)
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_.:@-]+$")
_SAFE_LOCATOR = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)*$")


class _ReplayDomainBlock(Exception):
    """A well-formed request that cannot form an applicable replay."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _PreparedRow:
    """Validated row used for deterministic ordering and de-duplication."""

    interval_start: datetime
    interval_end: datetime
    source_timestamp: str
    timestamp_semantics: str
    trading_phase: str
    trade_state: str
    ohlc: dict[str, dict[str, str]] | dict[str, str]
    volume: dict[str, str]
    amount: dict[str, str]
    evidence_id: str
    evidence_locator: str
    fingerprint: tuple[str, ...]


def build_intraday_replay_result(
    request: dict[str, Any],
    operations: Collection[IntradayReplaySourceOperation],
    research_now: datetime,
    daily_operations: Collection[IntradayReplayDailySourceOperation] = (),
) -> dict[str, Any]:
    """Collect one source operation and project its normalized replay rows."""

    try:
        query = _normalize_query(request, research_now)
    except _ReplayDomainBlock as blocked:
        return _blocked_result(request, blocked.code, str(blocked))

    operation_list = list(operations)
    if not operation_list:
        return _blocked_result(
            request,
            "intraday_replay_source_unavailable",
            "No intraday replay source operation is configured.",
            query=query,
        )
    if len(operation_list) != 1:
        return _blocked_result(
            request,
            "intraday_replay_requires_one_source_operation",
            "The complete minute sequence must come from exactly one source operation.",
            query=query,
        )

    operation = operation_list[0]
    source_errors: list[dict[str, Any]] = []
    operation_id = getattr(operation, "operation_id", None)
    if not isinstance(operation_id, str) or not _SAFE_OPERATION_ID.fullmatch(
        operation_id
    ):
        return _blocked_result(
            request,
            "intraday_replay_source_contract_not_satisfied",
            "The injected replay source did not satisfy its versioned contract.",
            query=query,
            source_errors=[
                {
                    "source_operation": "intraday_replay",
                    "code": "unsafe_source_operation_id",
                    "message": "The source operation identifier is not in the safe vocabulary.",
                }
            ],
        )
    try:
        batch = operation.collect(query)
        normalized_batch = _validate_batch(batch, operation_id, query)
        if not normalized_batch.completed_trading_dates:
            raise IntradayReplaySourceError(
                operation_id,
                "completed_trading_calendar_unverified",
                "The replay source did not provide a completed trading-date calendar.",
            )
        eligible_dates = _eligible_dates(
            normalized_batch.completed_trading_dates, query.as_of
        )
        if query.replay_date not in eligible_dates:
            raise IntradayReplaySourceError(
                operation_id,
                "replay_date_outside_recent_20",
                "The replay date is not among the 20 most recent completed trading days.",
            )
        rows, conflicts = _normalize_rows(normalized_batch, query)
    except IntradayReplaySourceError as error:
        source_errors.append(source_error_result(error))
        return _blocked_result(
            request,
            "intraday_replay_source_contract_not_satisfied",
            "The injected replay source did not satisfy its versioned contract.",
            query=query,
            source_errors=source_errors,
        )
    except Exception:
        source_errors.append(
            {
                "source_operation": operation_id,
                "code": "operation_failure",
                "message": "The source operation failed without a safe diagnostic.",
            }
        )
        return _blocked_result(
            request,
            "intraday_replay_source_failure",
            "The replay source operation could not form a valid result.",
            query=query,
            source_errors=source_errors,
        )

    daily_boundary = build_unavailable_boundary(
        "daily_boundary_source_unavailable",
        "No independent daily boundary operation is configured.",
    )
    daily_batch: IntradayReplayDailySourceBatch | None = None
    daily_evidence: list[dict[str, Any]] = []
    daily_errors: list[dict[str, Any]] = []
    daily_unavailable_fields: list[dict[str, str]] = []
    daily_conflicts: list[dict[str, Any]] = []
    daily_operation_list = list(daily_operations)
    if len(daily_operation_list) > 1:
        daily_errors.append(
            {
                "source_operation": "intraday_replay_daily_boundary",
                "code": "daily_boundary_requires_one_source_operation",
                "message": (
                    "The replay daily boundary must use exactly one independent "
                    "source operation."
                ),
            }
        )
        return _project_result(
            request,
            query,
            normalized_batch,
            rows,
            conflicts,
            status="blocked",
            daily_boundary=daily_boundary,
            source_errors=daily_errors,
        )
    if daily_operation_list:
        daily_operation = daily_operation_list[0]
        daily_operation_id = getattr(daily_operation, "operation_id", None)
        if not isinstance(daily_operation_id, str) or not _SAFE_OPERATION_ID.fullmatch(
            daily_operation_id
        ):
            daily_errors.append(
                {
                    "source_operation": "intraday_replay_daily_boundary",
                    "code": "unsafe_daily_source_operation_id",
                    "message": (
                        "The daily source operation identifier is not in the safe "
                        "vocabulary."
                    ),
                }
            )
            return _project_result(
                request,
                query,
                normalized_batch,
                rows,
                conflicts,
                status="blocked",
                daily_boundary=daily_boundary,
                source_errors=daily_errors,
            )
        try:
            daily_batch = validate_daily_batch(
                daily_operation.collect(query),
                daily_operation_id,
                normalized_batch.operation_id,
                query,
            )
            assessment = assess_daily_boundary(
                daily_batch,
                query,
                normalized_batch.operation_id,
                [
                    DailyBoundaryMinute(
                        interval_start=row.interval_start,
                        trading_phase=row.trading_phase,
                        trade_state=row.trade_state,
                        ohlc=row.ohlc,
                        volume=row.volume["value"],
                        amount=row.amount["value"],
                        evidence_id=row.evidence_id,
                    )
                    for row in rows
                ],
                normalized_batch.price_minimum_tick or normalized_batch.price_precision,
            )
            daily_boundary = assessment.boundary
            daily_evidence = assessment.evidence
            daily_conflicts = assessment.conflicts
            daily_unavailable_fields = assessment.unavailable_fields
        except IntradayReplaySourceError as error:
            daily_errors.append(source_error_result(error))
            daily_boundary = build_unavailable_boundary(
                error.code,
                "The independent daily boundary source was unavailable under its safe contract.",
            )
            if _daily_error_blocks(error.code):
                return _project_result(
                    request,
                    query,
                    normalized_batch,
                    rows,
                    conflicts,
                    status="blocked",
                    daily_boundary=daily_boundary,
                    source_errors=daily_errors,
                )
        except Exception:
            daily_errors.append(
                {
                    "source_operation": daily_operation_id,
                    "code": "daily_operation_failure",
                    "message": "The daily boundary operation failed safely.",
                }
            )
            daily_boundary = build_unavailable_boundary(
                "daily_operation_failure",
                "The independent daily boundary was not available.",
            )

    source_errors.extend(daily_errors)
    if daily_conflicts:
        conflicts = [*conflicts, *daily_conflicts]
    if (
        normalized_batch.trading_status == "suspended"
        or daily_boundary.get("trading_status") == "suspended"
    ):
        if (
            normalized_batch.trading_status == "suspended"
            and daily_boundary.get("trading_status") == "suspended"
            and not rows
        ):
            return _project_result(
                request,
                query,
                normalized_batch,
                [],
                conflicts,
                status="limited",
                daily_boundary=daily_boundary,
                daily_batch=daily_batch,
                daily_evidence=daily_evidence,
                source_errors=source_errors,
                unavailable_fields=daily_unavailable_fields,
                confirmed_suspension=True,
            )
        conflicts.append(
            {
                "code": "suspension_not_independently_confirmed",
                "message": (
                    "A suspension observation was not confirmed by both the minute "
                    "and independent daily operations."
                ),
                "evidence_ids": [
                    *[row.evidence_id for row in rows],
                    *daily_boundary.get("evidence_ids", []),
                ],
            }
        )

    if not rows:
        if normalized_batch.trading_status == "traded" and not daily_operation_list:
            return _blocked_result(
                request,
                "empty_intraday_replay",
                "The replay source returned no admissible minute records.",
                query=query,
                source_operations=[normalized_batch],
                source_errors=source_errors,
            )
        return _project_result(
            request,
            query,
            normalized_batch,
            [],
            conflicts,
            status="blocked",
            daily_boundary=daily_boundary,
            daily_batch=daily_batch,
            daily_evidence=daily_evidence,
            source_errors=source_errors,
            unavailable_fields=daily_unavailable_fields,
        )

    result_status = "blocked" if conflicts else "limited"
    return _project_result(
        request,
        query,
        normalized_batch,
        rows,
        conflicts,
        status=result_status,
        daily_boundary=daily_boundary,
        daily_batch=daily_batch,
        daily_evidence=daily_evidence,
        source_errors=source_errors,
        unavailable_fields=daily_unavailable_fields,
    )


def _daily_error_blocks(code: str) -> bool:
    return code in {
        "daily_source_security_mismatch",
        "daily_source_trading_date_mismatch",
        "daily_operation_not_independent",
        "daily_source_operation_mismatch",
        "daily_boundary_requires_one_source_operation",
        "daily_internal_close_conflict",
    }


def _normalize_query(
    request: dict[str, Any], research_now: datetime
) -> IntradayReplayQuery:
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("intraday_replay requires exactly one subject")
    subject = subjects[0]
    if not isinstance(subject, dict):
        raise ValueError("intraday_replay subject must be a JSON object")
    security = _canonical_security(subject)
    exchange, _, code = security.partition(":")
    as_of = _parse_date(request.get("as_of"), "research as_of")
    window = request.get("window")
    if not isinstance(window, dict):
        raise ValueError("intraday_replay window must be a JSON object")
    observed_from = _parse_date(
        window.get("observed_from"), "intraday_replay window.observed_from"
    )
    observed_to = _parse_date(
        window.get("observed_to"), "intraday_replay window.observed_to"
    )
    if observed_from != observed_to:
        raise _ReplayDomainBlock(
            "replay_window_not_one_day",
            "intraday_replay requires a one-day observed window.",
        )
    research_now_cst = _as_china_time(research_now)
    if as_of > research_now_cst.date():
        raise _ReplayDomainBlock(
            "future_research_boundary",
            "The research as_of date is later than the current China date.",
        )
    if observed_from > as_of:
        raise _ReplayDomainBlock(
            "future_replay_date",
            "The replay date is later than the research as_of date.",
        )
    if observed_from > research_now_cst.date():
        raise _ReplayDomainBlock(
            "future_replay_date",
            "The replay date is later than the current China date.",
        )
    if observed_from.weekday() >= 5:
        raise _ReplayDomainBlock(
            "non_trading_replay_date",
            "The replay date is not a scheduled China trading weekday.",
        )
    if observed_from == research_now_cst.date() and research_now_cst.time() < time(
        15, 0
    ):
        raise _ReplayDomainBlock(
            "replay_session_not_completed",
            "The current-day replay session has not conclusively ended.",
        )
    research_boundary = (
        datetime.combine(as_of, time.max, tzinfo=CHINA_STANDARD_TIME)
        if as_of < research_now_cst.date()
        else research_now_cst
    )
    return IntradayReplayQuery(
        security=security,
        exchange=exchange,
        code=code,
        as_of=as_of,
        replay_date=observed_from,
        research_boundary=research_boundary,
        retrieved_at=research_now_cst,
    )


def _canonical_security(subject: dict[str, Any]) -> str:
    value = subject.get("security")
    if value is None and isinstance(subject.get("clue"), str):
        value = subject["clue"]
    if isinstance(value, dict):
        exchange = value.get("exchange")
        code = value.get("code")
        security_type = value.get("type", "A_SHARE")
        if not isinstance(exchange, str) or not isinstance(code, str):
            raise ValueError("intraday_replay security requires exchange and code")
        if security_type != "A_SHARE":
            raise _ReplayDomainBlock(
                "unsupported_security_type",
                "intraday_replay supports only SSE/SZSE A shares.",
            )
        value = f"{exchange}:{code}"
    if not isinstance(value, str):
        raise ValueError("intraday_replay requires one canonical security")
    exchange, separator, code = value.strip().upper().partition(":")
    if separator != ":" or exchange not in {"SSE", "SZSE"}:
        raise _ReplayDomainBlock(
            "security_not_canonical",
            "intraday_replay requires an explicit SSE: or SZSE: security.",
        )
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        raise _ReplayDomainBlock(
            "invalid_security_code",
            "intraday_replay requires a six-digit SSE/SZSE A-share code.",
        )
    prefixes = SSE_A_SHARE_PREFIXES if exchange == "SSE" else SZSE_A_SHARE_PREFIXES
    if not code.startswith(prefixes):
        raise _ReplayDomainBlock(
            "unsupported_security_type",
            "intraday_replay supports only SSE/SZSE A shares.",
        )
    return f"{exchange}:{code}"


def _parse_date(value: object, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must use explicit YYYY-MM-DD format")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must use explicit YYYY-MM-DD format") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use explicit YYYY-MM-DD format")
    return parsed


def _as_china_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_STANDARD_TIME)
    return value.astimezone(CHINA_STANDARD_TIME)


def _validate_batch(
    batch: IntradayReplaySourceBatch,
    expected_operation_id: str,
    query: IntradayReplayQuery,
) -> IntradayReplaySourceBatch:
    if not isinstance(batch, IntradayReplaySourceBatch):
        raise IntradayReplaySourceError(
            expected_operation_id,
            "unknown_schema",
            "The replay source did not return a versioned source batch.",
        )
    if batch.operation_id != expected_operation_id:
        raise IntradayReplaySourceError(
            expected_operation_id,
            "source_operation_mismatch",
            "The source batch operation does not match the injected operation.",
        )
    if not _SAFE_OPERATION_ID.fullmatch(batch.operation_id):
        raise IntradayReplaySourceError(
            expected_operation_id,
            "unsafe_source_operation_id",
            "The source operation identifier is not in the safe vocabulary.",
        )
    if batch.contract_version != "1.0":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unsupported_source_contract",
            "The replay source contract version is not supported.",
        )
    if batch.security != query.security:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "source_security_mismatch",
            "The replay source returned a different canonical security.",
        )
    if batch.trading_date != query.replay_date:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "source_trading_date_mismatch",
            "The replay source returned a different trading date.",
        )
    if batch.source_role != "market_observation":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "source_role_mismatch",
            "The replay source must provide market observations.",
        )
    if batch.timestamp_timezone != "Asia/Shanghai":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "timestamp_timezone_unverified",
            "The source timestamp timezone is not verified as Asia/Shanghai.",
        )
    if batch.price_adjustment != "unadjusted":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unsupported_price_adjustment",
            "Intraday replay requires unadjusted source prices.",
        )
    if batch.price_unit != "CNY/share":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_price_unit",
            "The replay source price unit is not CNY/share.",
        )
    if batch.volume_unit not in {"shares", "hands"}:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_volume_unit",
            "The replay source volume unit is not qualified.",
        )
    if batch.volume_unit == "hands" and batch.volume_lot_size is None:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "volume_lot_size_unverified",
            "Hands-to-shares conversion requires a qualified lot size.",
        )
    if batch.amount_unit != "CNY":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_amount_unit",
            "The replay source amount unit is not CNY.",
        )
    if batch.trading_status not in {"traded", "suspended"}:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_trading_status",
            "The replay source trading status is not supported.",
        )
    if batch.trading_status == "suspended" and batch.rows:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "suspended_source_has_minute_rows",
            "A suspended minute source cannot also provide a minute sequence.",
        )
    if batch.retrieved_at.tzinfo is None or batch.retrieved_at.utcoffset() != timedelta(
        hours=8
    ):
        raise IntradayReplaySourceError(
            batch.operation_id,
            "retrieved_at_timezone_unverified",
            "The source acquisition time must carry an explicit +08:00 offset.",
        )
    retrieved_at = _as_china_time(batch.retrieved_at)
    if retrieved_at > query.research_boundary:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "source_retrieved_after_research_boundary",
            "The replay source was acquired after the research boundary.",
        )
    _positive_decimal(batch.price_precision, batch.operation_id, "price_precision")
    if batch.price_minimum_tick is not None:
        _positive_decimal(
            batch.price_minimum_tick, batch.operation_id, "price_minimum_tick"
        )
    _positive_decimal(batch.amount_precision, batch.operation_id, "amount_precision")
    if not isinstance(batch.experimental, bool):
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_source_qualification",
            "The source experimental qualification is missing.",
        )
    return batch


def _normalize_rows(
    batch: IntradayReplaySourceBatch,
    query: IntradayReplayQuery,
) -> tuple[list[_PreparedRow], list[dict[str, Any]]]:
    prepared = [
        _normalize_row(row, index, batch, query) for index, row in enumerate(batch.rows)
    ]
    prepared.sort(key=lambda row: (row.interval_start, row.fingerprint))
    deduplicated: list[_PreparedRow] = []
    conflicts: list[dict[str, Any]] = []
    index = 0
    while index < len(prepared):
        same_interval = [prepared[index]]
        index += 1
        while (
            index < len(prepared)
            and prepared[index].interval_start == same_interval[0].interval_start
        ):
            same_interval.append(prepared[index])
            index += 1
        winner = min(same_interval, key=lambda row: row.fingerprint)
        deduplicated.append(winner)
        if len(same_interval) > 1:
            conflicts.append(
                {
                    "code": "duplicate_intraday_interval",
                    "message": (
                        "Duplicate source timestamps were deterministically reduced "
                        "to one normalized interval."
                    ),
                    "interval_start": winner.interval_start.isoformat(),
                    "evidence_ids": [row.evidence_id for row in same_interval],
                    "resolution": "lexicographically_smallest_normalized_row",
                }
            )
    return deduplicated, conflicts


def _normalize_row(
    row: IntradayReplaySourceRow,
    index: int,
    batch: IntradayReplaySourceBatch,
    query: IntradayReplayQuery,
) -> _PreparedRow:
    if row.timestamp_semantics not in _TIMESTAMP_SEMANTICS:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "timestamp_semantics_unverified",
            "The source timestamp must declare interval_start or interval_end semantics.",
        )
    if row.trade_state not in _TRADE_STATES:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_trade_state",
            "The source row trade state is not in the supported vocabulary.",
        )
    if row.trading_phase not in _TRADING_PHASES:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unknown_trading_phase",
            "The source row does not identify a trading phase.",
        )
    source_timestamp, observed_at = _parse_source_timestamp(
        row.source_timestamp,
        row.trading_date or batch.trading_date,
        batch.operation_id,
    )
    if observed_at.date() != query.replay_date:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "source_row_date_mismatch",
            "A replay row belongs to a different trading date.",
        )
    if observed_at.second != 0 or observed_at.microsecond != 0:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "source_timestamp_not_minute_aligned",
            "Replay source timestamps must identify a minute boundary.",
        )
    if row.price_adjustment not in {None, "unadjusted"}:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unsupported_price_adjustment",
            "A replay row declared a non-unadjusted price basis.",
        )
    if row.timestamp_semantics == "interval_start":
        interval_start = observed_at
        interval_end = observed_at + timedelta(minutes=1)
    else:
        interval_start = observed_at - timedelta(minutes=1)
        interval_end = observed_at
    ohlc: dict[str, dict[str, str]] | dict[str, str]
    if row.trade_state == "traded":
        ohlc = {
            name: {"value": _price_text(value, batch), "unit": "CNY/share"}
            for name, value in (
                ("open", row.open_price),
                ("high", row.high_price),
                ("low", row.low_price),
                ("close", row.close_price),
            )
        }
    else:
        if any(
            value is not None
            for value in (
                row.open_price,
                row.high_price,
                row.low_price,
                row.close_price,
            )
        ):
            raise IntradayReplaySourceError(
                batch.operation_id,
                "no_trade_ohlc_conflict",
                "A no-trade row cannot carry transaction OHLC values.",
            )
        ohlc = {"status": "unavailable", "reason": "source_proven_no_trade"}
    volume = _volume_text(row.volume, batch)
    amount = _amount_text(row.amount, batch)
    if row.trade_state == "no_trade" and (volume != "0" or Decimal(amount) != 0):
        raise IntradayReplaySourceError(
            batch.operation_id,
            "no_trade_volume_amount_conflict",
            "A proven no-trade row must have zero shares and zero CNY amount.",
        )
    evidence_locator = _safe_locator(row.evidence_locator, interval_start)
    fingerprint = (
        interval_start.isoformat(),
        interval_end.isoformat(),
        source_timestamp,
        row.timestamp_semantics,
        row.trading_phase,
        row.trade_state,
        repr(ohlc),
        volume,
        amount,
        evidence_locator,
    )
    evidence_id = _evidence_id(
        batch.operation_id,
        query.security,
        query.replay_date,
        interval_start,
        fingerprint,
    )
    return _PreparedRow(
        interval_start=interval_start,
        interval_end=interval_end,
        source_timestamp=source_timestamp,
        timestamp_semantics=row.timestamp_semantics,
        trading_phase=row.trading_phase,
        trade_state=row.trade_state,
        ohlc=ohlc,
        volume={"value": volume, "unit": "shares"},
        amount={"value": amount, "unit": "CNY"},
        evidence_id=evidence_id,
        evidence_locator=evidence_locator,
        fingerprint=fingerprint,
    )


def _parse_source_timestamp(
    value: str | datetime,
    trading_date: date,
    source_operation: str,
) -> tuple[str, datetime]:
    if isinstance(value, datetime):
        original = value.isoformat()
        parsed = value
    elif isinstance(value, str) and value:
        original = value
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            try:
                parsed_time = time.fromisoformat(value)
            except ValueError as error:
                raise IntradayReplaySourceError(
                    source_operation,
                    "unknown_timestamp_schema",
                    "The replay source timestamp is not ISO date-time or local time.",
                ) from error
            parsed = datetime.combine(trading_date, parsed_time)
    else:
        raise IntradayReplaySourceError(
            source_operation,
            "unknown_timestamp_schema",
            "The replay source timestamp is missing.",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_STANDARD_TIME)
    elif parsed.utcoffset() != timedelta(hours=8):
        raise IntradayReplaySourceError(
            source_operation,
            "timestamp_timezone_unverified",
            "The replay source timestamp is not explicitly in +08:00 time.",
        )
    return original, parsed.astimezone(CHINA_STANDARD_TIME)


def _price_text(value: object | None, batch: IntradayReplaySourceBatch) -> str:
    if value is None:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "missing_transaction_price",
            "A traded replay row must provide all unadjusted OHLC values.",
        )
    return normalize_price_to_tick(
        value,
        batch.price_minimum_tick or batch.price_precision,
        batch.operation_id,
        "price",
    )


def _volume_text(value: object, batch: IntradayReplaySourceBatch) -> str:
    parsed = _decimal(value, batch.operation_id, "volume")
    if parsed < 0:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "negative_volume",
            "Replay volume cannot be negative.",
        )
    if batch.volume_unit == "hands":
        assert batch.volume_lot_size is not None
        lot_size = _decimal(
            batch.volume_lot_size, batch.operation_id, "volume_lot_size"
        )
        if lot_size <= 0:
            raise IntradayReplaySourceError(
                batch.operation_id,
                "invalid_volume_lot_size",
                "Replay volume lot size must be positive.",
            )
        parsed *= lot_size
    if parsed != parsed.to_integral_value():
        raise IntradayReplaySourceError(
            batch.operation_id,
            "fractional_share_volume",
            "Normalized replay volume must be a whole number of shares.",
        )
    return format(parsed.quantize(Decimal(1)), "f")


def _amount_text(value: object, batch: IntradayReplaySourceBatch) -> str:
    amount_precision = _decimal(
        batch.amount_precision, batch.operation_id, "amount_precision"
    )
    parsed = _decimal(value, batch.operation_id, "amount")
    if parsed < 0:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "negative_amount",
            "Replay amount cannot be negative.",
        )
    return _fixed_decimal_text(
        parsed, amount_precision, batch.operation_id, "amount", positive=False
    )


def _fixed_decimal_text(
    value: object,
    precision: Decimal,
    source_operation: str,
    field_name: str,
    *,
    positive: bool,
) -> str:
    parsed = _decimal(value, source_operation, field_name)
    if positive and parsed <= 0:
        raise IntradayReplaySourceError(
            source_operation,
            f"invalid_{field_name}",
            f"Replay {field_name} must be positive.",
        )
    if not positive and parsed < 0:
        raise IntradayReplaySourceError(
            source_operation,
            f"invalid_{field_name}",
            f"Replay {field_name} cannot be negative.",
        )
    try:
        quantized = parsed.quantize(precision, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise IntradayReplaySourceError(
            source_operation,
            f"invalid_{field_name}_precision",
            f"Replay {field_name} cannot be normalized to the qualified precision.",
        ) from error
    return format(quantized, "f")


def _decimal(value: object, source_operation: str, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise IntradayReplaySourceError(
            source_operation,
            "unknown_schema",
            f"Replay {field_name} must be a finite decimal value.",
        )
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise IntradayReplaySourceError(
            source_operation,
            "unknown_schema",
            f"Replay {field_name} must be a finite decimal value.",
        ) from error
    if not parsed.is_finite():
        raise IntradayReplaySourceError(
            source_operation,
            "unknown_schema",
            f"Replay {field_name} must be a finite decimal value.",
        )
    return parsed


def _positive_decimal(value: object, source_operation: str, field_name: str) -> Decimal:
    parsed = _decimal(value, source_operation, field_name)
    if parsed <= 0:
        raise IntradayReplaySourceError(
            source_operation,
            f"invalid_{field_name}",
            f"Replay {field_name} must be positive.",
        )
    return parsed


def _eligible_dates(dates: tuple[date, ...], as_of: date) -> tuple[date, ...]:
    if any(not isinstance(item, date) for item in dates):
        raise IntradayReplaySourceError(
            "intraday_replay",
            "unknown_calendar_schema",
            "The source completed-trading-date calendar is not valid.",
        )
    eligible = sorted({item for item in dates if item <= as_of})
    return tuple(eligible[-20:])


def _evidence_id(
    operation_id: str,
    security: str,
    replay_date: date,
    interval_start: datetime,
    fingerprint: tuple[str, ...],
) -> str:
    digest = hashlib.sha256("\x1f".join(fingerprint).encode("utf-8")).hexdigest()[:16]
    return (
        f"intraday-replay-{operation_id}-{security}-{replay_date.isoformat()}-"
        f"{interval_start.strftime('%H%M%S')}-{digest}"
    )


def _safe_locator(value: str | None, interval_start: datetime) -> str:
    unsafe_markers = ("http", "//", "bearer", "token", "secret", "password")
    if (
        value
        and _SAFE_LOCATOR.fullmatch(value)
        and not any(marker in value.lower() for marker in unsafe_markers)
    ):
        return value
    return f"source-row:{interval_start.isoformat()}"


def _project_result(
    request: dict[str, Any],
    query: IntradayReplayQuery,
    batch: IntradayReplaySourceBatch,
    rows: list[_PreparedRow],
    conflicts: list[dict[str, Any]],
    *,
    status: str = "limited",
    daily_boundary: dict[str, Any] | None = None,
    daily_batch: IntradayReplayDailySourceBatch | None = None,
    daily_evidence: list[dict[str, Any]] | None = None,
    source_errors: list[dict[str, Any]] | None = None,
    unavailable_fields: list[dict[str, str]] | None = None,
    confirmed_suspension: bool = False,
) -> dict[str, Any]:
    evidence = [_row_evidence(row, query, batch) for row in rows]
    evidence.extend(daily_evidence or [])
    result_rows = [_row_result(row) for row in rows]
    minute_evidence_ids = [row.evidence_id for row in rows]
    all_evidence_ids = [*minute_evidence_ids]
    daily_evidence_ids = [item["id"] for item in daily_evidence or []]
    all_evidence_ids.extend(daily_evidence_ids)
    daily_boundary = daily_boundary or {
        "status": "unavailable",
        "reason": "daily_boundary_not_run",
        "evidence_ids": [],
    }
    field_lineage: dict[str, Any] = {}
    field_lineage.update(
        {
            "subjects[0].security": {
                "evidence_ids": all_evidence_ids,
                "source_fields": ["security"],
            },
            "research.as_of": {
                "evidence_ids": [],
                "source_fields": ["request.as_of"],
            },
            "research.research_boundary": {
                "evidence_ids": [],
                "source_fields": ["request.as_of", "research_now"],
                "calculation": "research_boundary_at_china_date@1",
            },
            "replay.trading_date": {
                "evidence_ids": all_evidence_ids,
                "source_fields": ["trading_date", "source_timestamp"],
            },
            "replay.adjustment": {
                "evidence_ids": all_evidence_ids,
                "source_fields": ["price_adjustment"],
            },
            "source_operations[0]": {
                "evidence_ids": minute_evidence_ids,
                "source_fields": [
                    "operation_id",
                    "contract_version",
                    "experimental",
                    "retrieved_at",
                ],
            },
        }
    )
    if daily_batch is not None:
        field_lineage["source_operations[1]"] = {
            "evidence_ids": daily_evidence_ids,
            "source_fields": [
                "operation_id",
                "contract_version",
                "experimental",
                "retrieved_at",
                "source_role",
            ],
        }
        for field in (
            "open",
            "high",
            "low",
            "close",
            "actual_close",
            "volume",
            "amount",
        ):
            field_lineage[f"daily_boundary.{field}"] = {
                "evidence_ids": daily_evidence_ids,
                "source_fields": [field],
                "calculation": "daily_boundary_cross_check@1",
            }
        field_lineage["daily_boundary.lineage"] = {
            "evidence_ids": daily_evidence_ids,
            "source_fields": [
                "price_minimum_tick",
                "volume_unit",
                "amount_unit",
                "comparison_explanations",
            ],
            "calculation": "qualified_daily_normalization@1",
        }
        field_lineage["daily_boundary.baselines"] = {
            "evidence_ids": daily_evidence_ids,
            "source_fields": [
                "previous_close",
                "previous_close_basis",
                "ex_right_reference",
            ],
            "calculation": "previous_close_semantics@1",
        }
    for index, row in enumerate(rows):
        evidence_ids = [row.evidence_id]
        field_lineage.update(
            {
                f"records[{index}].interval_start": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["source_timestamp"],
                },
                f"records[{index}].interval_end": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["source_timestamp", "timestamp_semantics"],
                },
                f"records[{index}].source_timestamp": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["source_timestamp"],
                },
                f"records[{index}].trading_phase": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["trading_phase"],
                },
                f"records[{index}].trade_state": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["trade_state"],
                },
                f"records[{index}].volume": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["volume", "volume_unit"],
                    "calculation": "normalize_volume_to_shares@1",
                },
                f"records[{index}].amount": {
                    "evidence_ids": evidence_ids,
                    "source_fields": ["amount", "amount_unit"],
                },
            }
        )
        for price_field in ("open", "high", "low", "close"):
            field_lineage[f"records[{index}].ohlc.{price_field}"] = {
                "evidence_ids": evidence_ids,
                "source_fields": [f"{price_field}_price"],
            }
    limitation = {
        "code": "experimental_intraday_replay_source",
        "message": (
            "The replay source operation is experimental and has not completed "
            "production qualification; the result is limited."
        ),
    }
    limitations = [limitation] if batch.experimental else []
    if daily_batch is not None and daily_batch.experimental:
        limitations.append(
            {
                "code": "experimental_daily_boundary_source",
                "message": (
                    "The independent daily boundary operation is experimental; "
                    "the result remains limited."
                ),
            }
        )
    limitations.append(
        {
            "code": "intraday_replay_coverage_not_adjudicated",
            "message": (
                "Trading-phase coverage remains unadjudicated by this tracer and "
                "is scoped to the coverage work item."
            ),
        }
    )
    if daily_boundary.get("status") == "unavailable":
        limitations.append(
            {
                "code": "daily_boundary_unavailable",
                "message": (
                    "The independent daily boundary is unavailable; the minute "
                    "sequence is retained as limited evidence."
                ),
            }
        )
    source_operations: list[dict[str, Any]] = [
        {
            "operation_id": batch.operation_id,
            "contract_version": batch.contract_version,
            "experimental": batch.experimental,
            "retrieved_at": _as_china_time(batch.retrieved_at).isoformat(),
        }
    ]
    if daily_batch is not None:
        source_operations.append(
            {
                "operation_id": daily_batch.operation_id,
                "contract_version": daily_batch.contract_version,
                "experimental": daily_batch.experimental,
                "retrieved_at": _as_china_time(daily_batch.retrieved_at).isoformat(),
                "source_role": daily_batch.source_role,
            }
        )
    replay: dict[str, Any] = {
        "security": query.security,
        "trading_date": query.replay_date.isoformat(),
        "adjustment": batch.price_adjustment,
        "record_count": len(result_rows),
        "trading_status": "confirmed_suspended" if confirmed_suspension else "traded",
    }
    return {
        "schema_version": request["schema_version"],
        "status": status,
        "subjects": [
            {
                "security": {
                    "exchange": query.exchange,
                    "code": query.code,
                    "type": "A_SHARE",
                }
            }
        ],
        "research": {
            "as_of": query.as_of.isoformat(),
            "research_boundary": query.research_boundary.isoformat(),
            "timezone": "Asia/Shanghai",
            "retrieved_at": _as_china_time(batch.retrieved_at).isoformat(),
        },
        "window": {
            "observed_from": query.replay_date.isoformat(),
            "observed_to": query.replay_date.isoformat(),
            "timezone": "Asia/Shanghai",
        },
        "replay": replay,
        "records": result_rows,
        "coverage": {
            "status": "not_adjudicated",
            "reason": "coverage_adjudication_is_scoped_to_issue_30",
        },
        "source_operations": source_operations,
        "daily_boundary": daily_boundary,
        "field_lineage": field_lineage,
        "evidence": evidence,
        "conflicts": conflicts,
        "source_errors": source_errors or [],
        "degradations": [],
        "limitations": limitations,
        "unavailable_fields": [
            {
                "field": "coverage",
                "reason": "coverage_adjudication_is_scoped_to_issue_30",
            },
            *(unavailable_fields or []),
        ],
    }


def _row_result(row: _PreparedRow) -> dict[str, Any]:
    return {
        "interval_start": row.interval_start.isoformat(),
        "interval_end": row.interval_end.isoformat(),
        "source_timestamp": row.source_timestamp,
        "timestamp_semantics": row.timestamp_semantics,
        "trading_phase": row.trading_phase,
        "trade_state": row.trade_state,
        "ohlc": row.ohlc,
        "volume": row.volume,
        "amount": row.amount,
        "evidence_ids": [row.evidence_id],
    }


def _row_evidence(
    row: _PreparedRow,
    query: IntradayReplayQuery,
    batch: IntradayReplaySourceBatch,
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "kind": "intraday_minute_record",
        "interval_start": row.interval_start.isoformat(),
        "interval_end": row.interval_end.isoformat(),
        "timestamp_semantics": row.timestamp_semantics,
        "trading_phase": row.trading_phase,
        "trade_state": row.trade_state,
        "adjustment": batch.price_adjustment,
        "price_unit": batch.price_unit,
        "volume_unit": "shares",
        "amount_unit": batch.amount_unit,
    }
    return {
        "id": row.evidence_id,
        "source_role": batch.source_role,
        "source_operation": batch.operation_id,
        "contract_version": batch.contract_version,
        "experimental": batch.experimental,
        "subject": {"security": query.security},
        "observed_value": {
            "source_timestamp": row.source_timestamp,
            "interval_start": row.interval_start.isoformat(),
            "interval_end": row.interval_end.isoformat(),
            "ohlc": row.ohlc,
            "volume": row.volume,
            "amount": row.amount,
        },
        "basis": {
            "price_adjustment": batch.price_adjustment,
            "price_precision": batch.price_precision,
            "price_minimum_tick": batch.price_minimum_tick or batch.price_precision,
            "timestamp_timezone": batch.timestamp_timezone,
        },
        "observation": observation,
        "evidence_time": row.source_timestamp,
        "available_at": _as_china_time(batch.retrieved_at).isoformat(),
        "retrieved_at": _as_china_time(batch.retrieved_at).isoformat(),
        "locator": {"uri": row.evidence_locator, "observation": "source row"},
        "limitations": (
            ["experimental_source_operation"] if batch.experimental else []
        ),
    }


def _blocked_result(
    request: dict[str, Any],
    code: str,
    message: str,
    *,
    query: IntradayReplayQuery | None = None,
    source_errors: list[dict[str, Any]] | None = None,
    source_operations: list[IntradayReplaySourceBatch] | None = None,
) -> dict[str, Any]:
    research: dict[str, str] = {
        "as_of": str(request.get("as_of", "")),
        "timezone": "Asia/Shanghai",
    }
    window: dict[str, str] = {}
    replay: dict[str, str] = {}
    subjects: object = request.get("subjects", [])
    if query is not None:
        research.update(
            {
                "research_boundary": query.research_boundary.isoformat(),
                "retrieved_at": query.retrieved_at.isoformat(),
            }
        )
        window = {
            "observed_from": query.replay_date.isoformat(),
            "observed_to": query.replay_date.isoformat(),
            "timezone": "Asia/Shanghai",
        }
        replay = {
            "security": query.security,
            "trading_date": query.replay_date.isoformat(),
        }
        subjects = [
            {
                "security": {
                    "exchange": query.exchange,
                    "code": query.code,
                    "type": "A_SHARE",
                }
            }
        ]
    operations = []
    for batch in source_operations or []:
        operations.append(
            {
                "operation_id": batch.operation_id,
                "contract_version": batch.contract_version,
                "experimental": batch.experimental,
                "retrieved_at": _as_china_time(batch.retrieved_at).isoformat(),
            }
        )
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": subjects,
        "research": research,
        "window": window,
        "replay": replay,
        "records": [],
        "coverage": {
            "status": "not_adjudicated",
            "reason": "coverage_adjudication_is_scoped_to_issue_30",
        },
        "source_operations": operations,
        "field_lineage": {},
        "evidence": [],
        "conflicts": [],
        "source_errors": source_errors or [],
        "degradations": [],
        "limitations": [{"code": code, "message": message}],
        "unavailable_fields": [],
    }
