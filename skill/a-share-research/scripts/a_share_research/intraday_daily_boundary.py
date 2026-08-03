"""Adjudicate an intraday sequence against one independent daily operation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .intraday_replay_contract import (
    IntradayReplayDailySourceBatch,
    IntradayReplayQuery,
    IntradayReplaySourceError,
)

CHINA_STANDARD_OFFSET = timedelta(hours=8)
_DAILY_STATUSES = frozenset({"traded", "suspended"})
_VOLUME_UNITS = frozenset({"shares", "hands"})
_AMOUNT_UNITS = frozenset({"CNY", "CNY_thousand"})
_EXPLANATION_CODES = frozenset(
    {"price_minimum_tick", "auction_bucketing", "unit_conversion"}
)
_EXPLAINABLE_FIELDS = frozenset(
    {"open", "high", "low", "close", "actual_close", "volume", "amount"}
)
_SAFE_LOCATOR = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)*$")


@dataclass(frozen=True)
class DailyBoundaryMinute:
    """Comparable minute values projected from the single minute operation."""

    interval_start: datetime
    trading_phase: str
    trade_state: str
    ohlc: Mapping[str, Mapping[str, str]] | Mapping[str, str]
    volume: str
    amount: str
    evidence_id: str


@dataclass(frozen=True)
class DailyBoundaryAssessment:
    """Daily-boundary projection plus evidence and conflict outputs."""

    boundary: dict[str, Any]
    evidence: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    unavailable_fields: list[dict[str, str]]


def validate_daily_batch(
    batch: object,
    expected_operation_id: str,
    minute_operation_id: str,
    query: IntradayReplayQuery,
) -> IntradayReplayDailySourceBatch:
    """Validate the independent operation without accepting provider details."""

    if not isinstance(batch, IntradayReplayDailySourceBatch):
        raise IntradayReplaySourceError(
            expected_operation_id,
            "unknown_schema",
            "The daily operation did not return a versioned boundary batch.",
        )
    if batch.operation_id != expected_operation_id:
        raise IntradayReplaySourceError(
            expected_operation_id,
            "daily_source_operation_mismatch",
            "The daily batch operation does not match the injected operation.",
        )
    if batch.operation_id == minute_operation_id:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_operation_not_independent",
            "The daily boundary operation must be independent of the minute operation.",
        )
    if batch.contract_version != "1.0":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "unsupported_daily_source_contract",
            "The daily boundary contract version is not supported.",
        )
    if batch.security != query.security:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_source_security_mismatch",
            "The daily boundary returned a different canonical security.",
        )
    if batch.trading_date != query.replay_date:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_source_trading_date_mismatch",
            "The daily boundary returned a different trading date.",
        )
    if batch.source_role != "daily_boundary_cross_check":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_source_role_mismatch",
            "The injected operation is not qualified as a daily boundary check.",
        )
    if batch.timestamp_timezone != "Asia/Shanghai":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_timestamp_timezone_unverified",
            "The daily boundary timezone is not verified as Asia/Shanghai.",
        )
    if batch.price_adjustment != "unadjusted":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_unsupported_price_adjustment",
            "The daily boundary requires unadjusted prices.",
        )
    if batch.price_unit != "CNY/share":
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_unknown_price_unit",
            "The daily boundary price unit is not CNY/share.",
        )
    if batch.volume_unit not in _VOLUME_UNITS:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_unknown_volume_unit",
            "The daily boundary volume unit is not qualified.",
        )
    if batch.volume_unit == "hands" and batch.volume_lot_size is None:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_volume_lot_size_unverified",
            "Hands-to-shares conversion requires a qualified lot size.",
        )
    if batch.amount_unit not in _AMOUNT_UNITS:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_unknown_amount_unit",
            "The daily boundary amount unit is not qualified.",
        )
    _positive_decimal(batch.price_precision, batch.operation_id, "price_precision")
    _positive_decimal(batch.amount_precision, batch.operation_id, "amount_precision")
    _positive_decimal(batch.amount_scale, batch.operation_id, "amount_scale")
    if batch.price_minimum_tick is not None:
        _positive_decimal(
            batch.price_minimum_tick, batch.operation_id, "price_minimum_tick"
        )
    if batch.volume_lot_size is not None:
        _positive_decimal(batch.volume_lot_size, batch.operation_id, "volume_lot_size")
    if not isinstance(batch.experimental, bool):
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_unknown_source_qualification",
            "The daily source experimental qualification is missing.",
        )
    if batch.trading_status not in _DAILY_STATUSES:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_unknown_trading_status",
            "The daily boundary trading status is not supported.",
        )
    if batch.retrieved_at.tzinfo is None or batch.retrieved_at.utcoffset() != (
        CHINA_STANDARD_OFFSET
    ):
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_retrieved_at_timezone_unverified",
            "The daily acquisition time must carry an explicit +08:00 offset.",
        )
    retrieved_at = batch.retrieved_at.astimezone(query.research_boundary.tzinfo)
    if retrieved_at > query.research_boundary:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_source_retrieved_after_research_boundary",
            "The daily boundary was acquired after the research boundary.",
        )
    for field, reason in batch.comparison_explanations:
        if field not in _EXPLAINABLE_FIELDS or reason not in _EXPLANATION_CODES:
            raise IntradayReplaySourceError(
                batch.operation_id,
                "unknown_daily_comparison_explanation",
                "The daily boundary comparison explanation is not qualified.",
            )
    return batch


def assess_daily_boundary(
    batch: IntradayReplayDailySourceBatch,
    query: IntradayReplayQuery,
    minute_operation_id: str,
    minute_rows: Sequence[DailyBoundaryMinute],
    minute_price_tick: str | None,
) -> DailyBoundaryAssessment:
    """Compare normalized daily values using exact Decimal equality."""

    daily_id = _daily_evidence_id(batch, query)
    evidence = [_daily_evidence(batch, query, daily_id)]
    if batch.trading_status == "suspended":
        boundary: dict[str, Any] = {
            "status": "suspended_observation",
            "security": query.security,
            "trading_date": query.replay_date.isoformat(),
            "trading_status": "suspended",
            "evidence_ids": [daily_id],
            "lineage": _normalization_lineage(batch, minute_price_tick),
            "baselines": _baselines(batch, query, daily_id),
        }
        return DailyBoundaryAssessment(boundary, evidence, [], [])

    normalized = _normalized_daily_values(batch)
    traded_rows = [row for row in minute_rows if row.trade_state == "traded"]
    if not traded_rows:
        boundary = {
            "status": "unavailable",
            "reason": "minute_sequence_has_no_traded_rows",
            "security": query.security,
            "trading_date": query.replay_date.isoformat(),
            "evidence_ids": [daily_id],
            "lineage": _normalization_lineage(batch, minute_price_tick),
            "baselines": _baselines(batch, query, daily_id),
        }
        return DailyBoundaryAssessment(
            boundary,
            evidence,
            [],
            [
                {
                    "field": "daily_boundary",
                    "reason": "minute_sequence_has_no_traded_rows",
                }
            ],
        )

    minute_values = _minute_values(traded_rows)
    comparisons = {
        field: {
            "minute": minute_values[field],
            "daily": normalized[field],
            "rule": "exact_decimal_equality_after_qualified_normalization",
        }
        for field in normalized
    }
    explanations = dict(batch.comparison_explanations)
    differences = []
    conflicts = []
    for field, comparison in comparisons.items():
        if Decimal(comparison["minute"]) == Decimal(comparison["daily"]):
            comparison["status"] = "equal"
            continue
        reason = explanations.get(field)
        if reason is not None:
            comparison["status"] = "explained_difference"
            comparison["explanation"] = reason
            differences.append(
                {
                    "field": field,
                    "classification": "explained_difference",
                    "reason": reason,
                    "minute_value": comparison["minute"],
                    "daily_value": comparison["daily"],
                    "evidence_ids": [traded_rows[0].evidence_id, daily_id],
                }
            )
        else:
            comparison["status"] = "conflict"
            conflicts.append(
                {
                    "code": "daily_boundary_core_value_conflict",
                    "fields": [field],
                    "message": (
                        "The minute sequence and independent daily boundary disagree "
                        "on a core unadjusted daily value."
                    ),
                    "minute_value": comparison["minute"],
                    "daily_value": comparison["daily"],
                    "evidence_ids": [traded_rows[0].evidence_id, daily_id],
                }
            )

    status = "blocked" if conflicts else "cross_checked"
    boundary = {
        "status": status,
        "security": query.security,
        "trading_date": query.replay_date.isoformat(),
        "trading_status": "traded",
        "open": _price_value(normalized["open"]),
        "high": _price_value(normalized["high"]),
        "low": _price_value(normalized["low"]),
        "close": _price_value(normalized["close"]),
        "actual_close": _price_value(normalized["actual_close"]),
        "volume": {"value": normalized["volume"], "unit": "shares"},
        "amount": {"value": normalized["amount"], "unit": "CNY"},
        "evidence_ids": [daily_id],
        "comparison": {
            "status": "conflict" if conflicts else "agree",
            "fields": comparisons,
            "explained_differences": differences,
        },
        "lineage": _normalization_lineage(batch, minute_price_tick),
        "baselines": _baselines(batch, query, daily_id),
    }
    unavailable_fields: list[dict[str, str]] = []
    baseline = boundary["baselines"]["comparability"]
    if baseline["status"] != "comparable":
        unavailable_fields.extend(
            {
                "field": field,
                "reason": baseline["reason"],
            }
            for field in ("replay.opening_gap", "replay.relative_return")
        )
    return DailyBoundaryAssessment(boundary, evidence, conflicts, unavailable_fields)


def build_unavailable_boundary(reason: str, message: str) -> dict[str, Any]:
    """Return a visible limited boundary when the independent source is absent."""

    return {
        "status": "unavailable",
        "reason": reason,
        "message": message,
        "evidence_ids": [],
        "baselines": {
            "previous_trading_date": None,
            "actual_unadjusted_close": {
                "status": "unavailable",
                "reason": "daily_boundary_source_unavailable",
            },
            "ex_right_reference": {
                "status": "unavailable",
                "reason": "daily_boundary_source_unavailable",
            },
            "comparability": {
                "status": "unavailable",
                "reason": "daily_boundary_source_unavailable",
            },
        },
    }


def _normalized_daily_values(batch: IntradayReplayDailySourceBatch) -> dict[str, str]:
    tick = batch.price_minimum_tick or batch.price_precision
    values = {
        name: _price_text(value, tick, batch.operation_id, name)
        for name, value in (
            ("open", batch.open_price),
            ("high", batch.high_price),
            ("low", batch.low_price),
            ("close", batch.close_price),
        )
    }
    actual_close = batch.actual_close_price
    actual_text = (
        values["close"]
        if actual_close is None
        else _price_text(actual_close, tick, batch.operation_id, "actual_close")
    )
    if actual_text != values["close"]:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_internal_close_conflict",
            "The daily close and actual close disagree within one source operation.",
        )
    volume = _volume_text(batch, batch.volume)
    amount = _amount_text(batch, batch.amount)
    return {
        **values,
        "actual_close": actual_text,
        "volume": volume,
        "amount": amount,
    }


def _minute_values(rows: Sequence[DailyBoundaryMinute]) -> dict[str, str]:
    first = rows[0]
    last = rows[-1]
    high = max(Decimal(_minute_price(row, "high")) for row in rows)
    low = min(Decimal(_minute_price(row, "low")) for row in rows)
    return {
        "open": _minute_price(first, "open"),
        "high": format(high, "f"),
        "low": format(low, "f"),
        "close": _minute_price(last, "close"),
        "actual_close": _minute_price(last, "close"),
        "volume": _sum_decimal(row.volume for row in rows),
        "amount": _sum_decimal(row.amount for row in rows),
    }


def _minute_price(row: DailyBoundaryMinute, field: str) -> str:
    value = row.ohlc.get(field)
    if not isinstance(value, Mapping):
        raise IntradayReplaySourceError(
            "intraday_replay",
            "missing_transaction_price",
            "A traded minute row did not expose a normalized price.",
        )
    price = value.get("value")
    if not isinstance(price, str):
        raise IntradayReplaySourceError(
            "intraday_replay",
            "unknown_schema",
            "A normalized minute price is not a decimal string.",
        )
    return price


def _price_text(
    value: object | None, tick: str, source_operation: str, field: str
) -> str:
    return normalize_price_to_tick(value, tick, source_operation, field)


def normalize_price_to_tick(
    value: object | None, tick: str, source_operation: str, field: str
) -> str:
    """Normalize a source price to the qualified minimum tick explicitly."""

    if value is None:
        raise IntradayReplaySourceError(
            source_operation,
            "daily_missing_core_value",
            f"The daily boundary did not provide {field}.",
        )
    parsed = _decimal(value, source_operation, field)
    if parsed <= 0:
        raise IntradayReplaySourceError(
            source_operation,
            "daily_invalid_core_value",
            f"The daily boundary {field} must be positive.",
        )
    try:
        tick_value = _positive_decimal(tick, source_operation, "price_minimum_tick")
        tick_units = (parsed / tick_value).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return format(tick_units * tick_value, "f")
    except InvalidOperation as error:
        raise IntradayReplaySourceError(
            source_operation,
            "daily_invalid_price_tick",
            "The daily boundary price cannot be normalized to its qualified tick.",
        ) from error


def _volume_text(batch: IntradayReplayDailySourceBatch, value: object | None) -> str:
    if value is None:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_missing_core_value",
            "The daily boundary did not provide total volume.",
        )
    parsed = _decimal(value, batch.operation_id, "volume")
    if batch.volume_unit == "hands":
        assert batch.volume_lot_size is not None
        parsed *= _decimal(batch.volume_lot_size, batch.operation_id, "volume_lot_size")
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_invalid_volume",
            "The normalized daily volume must be a non-negative whole number of shares.",
        )
    return format(parsed.quantize(Decimal(1)), "f")


def _amount_text(batch: IntradayReplayDailySourceBatch, value: object | None) -> str:
    if value is None:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_missing_core_value",
            "The daily boundary did not provide total amount.",
        )
    parsed = _decimal(value, batch.operation_id, "amount")
    parsed *= _decimal(batch.amount_scale, batch.operation_id, "amount_scale")
    if parsed < 0:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_invalid_amount",
            "The normalized daily amount must be non-negative.",
        )
    try:
        return format(
            parsed.quantize(
                _decimal(
                    batch.amount_precision, batch.operation_id, "amount_precision"
                ),
                rounding=ROUND_HALF_UP,
            ),
            "f",
        )
    except InvalidOperation as error:
        raise IntradayReplaySourceError(
            batch.operation_id,
            "daily_invalid_amount_precision",
            "The daily amount cannot be normalized to its qualified precision.",
        ) from error


def _normalization_lineage(
    batch: IntradayReplayDailySourceBatch, minute_price_tick: str | None
) -> dict[str, Any]:
    return {
        "comparison_rule": "exact_decimal_equality_after_qualified_normalization",
        "minute": {
            "price_minimum_tick": minute_price_tick,
            "volume_unit": "shares",
            "amount_unit": "CNY",
        },
        "daily": {
            "price_minimum_tick": batch.price_minimum_tick or batch.price_precision,
            "source_price_precision": batch.price_precision,
            "volume_unit": batch.volume_unit,
            "volume_lot_size": batch.volume_lot_size,
            "amount_unit": batch.amount_unit,
            "amount_scale": batch.amount_scale,
            "auction_bucket": dict(batch.comparison_explanations).get(
                "open", "not_declared"
            ),
        },
        "explanations": [
            {"field": field, "reason": reason}
            for field, reason in batch.comparison_explanations
        ],
    }


def _baselines(
    batch: IntradayReplayDailySourceBatch,
    query: IntradayReplayQuery,
    daily_evidence_id: str,
) -> dict[str, Any]:
    tick = batch.price_minimum_tick or batch.price_precision
    actual: dict[str, Any]
    ex_right: dict[str, Any]
    previous_date = (
        batch.previous_trading_date.isoformat()
        if batch.previous_trading_date is not None
        else None
    )
    if (
        batch.previous_close is not None
        and batch.previous_close_basis == "actual_unadjusted"
        and batch.previous_trading_date is not None
        and batch.previous_trading_date < query.replay_date
    ):
        actual = {
            "status": "available",
            "value": {
                "value": _price_text(
                    batch.previous_close, tick, batch.operation_id, "previous_close"
                ),
                "unit": "CNY/share",
            },
            "trading_date": previous_date,
            "basis": "actual_unadjusted",
            "evidence_ids": [daily_evidence_id],
        }
    else:
        actual = {
            "status": "unavailable",
            "reason": "actual_previous_close_not_explicitly_comparable",
        }
    if batch.ex_right_reference is not None:
        ex_right = {
            "status": "available",
            "value": {
                "value": _price_text(
                    batch.ex_right_reference,
                    tick,
                    batch.operation_id,
                    "ex_right_reference",
                ),
                "unit": "CNY/share",
            },
            "trading_date": (
                batch.ex_right_reference_date.isoformat()
                if batch.ex_right_reference_date is not None
                else previous_date
            ),
            "basis": "ex_right_reference",
            "evidence_ids": [daily_evidence_id],
        }
    elif (
        batch.previous_close is not None
        and batch.previous_close_basis == "ex_right_reference"
    ):
        ex_right = {
            "status": "available",
            "value": {
                "value": _price_text(
                    batch.previous_close, tick, batch.operation_id, "ex_right_reference"
                ),
                "unit": "CNY/share",
            },
            "trading_date": previous_date,
            "basis": "ex_right_reference",
            "evidence_ids": [daily_evidence_id],
        }
    else:
        ex_right = {
            "status": "unavailable",
            "reason": "ex_right_reference_not_provided",
        }
    comparability: dict[str, Any]
    if actual["status"] == "available":
        comparability = {
            "status": "comparable",
            "basis": "actual_unadjusted",
            "reason": "actual_previous_close_matches_unadjusted_intraday_basis",
        }
    elif ex_right["status"] == "available":
        comparability = {
            "status": "not_comparable",
            "basis": "ex_right_reference",
            "reason": "ex_right_reference_is_not_actual_unadjusted_close",
        }
    else:
        comparability = {
            "status": "unavailable",
            "basis": None,
            "reason": "no_explicit_previous_close_baseline",
        }
    return {
        "previous_trading_date": previous_date,
        "actual_unadjusted_close": actual,
        "ex_right_reference": ex_right,
        "comparability": comparability,
    }


def _price_value(value: str) -> dict[str, str]:
    return {"value": value, "unit": "CNY/share"}


def _daily_evidence_id(
    batch: IntradayReplayDailySourceBatch, query: IntradayReplayQuery
) -> str:
    digest = hashlib.sha256(
        f"{batch.operation_id}|{query.security}|{query.replay_date.isoformat()}".encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    return f"intraday-daily-boundary-{batch.operation_id}-{digest}"


def _daily_evidence(
    batch: IntradayReplayDailySourceBatch,
    query: IntradayReplayQuery,
    evidence_id: str,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "trading_status": batch.trading_status,
        "price_adjustment": batch.price_adjustment,
        "price_unit": batch.price_unit,
        "volume_unit": batch.volume_unit,
        "amount_unit": batch.amount_unit,
    }
    if batch.trading_status == "traded":
        values.update(
            {
                "open": batch.open_price,
                "high": batch.high_price,
                "low": batch.low_price,
                "close": batch.close_price,
                "actual_close": batch.actual_close_price or batch.close_price,
                "volume": batch.volume,
                "amount": batch.amount,
            }
        )
    locator = batch.evidence_locator
    if not isinstance(locator, str) or not locator or not _safe_locator(locator):
        locator = f"daily-boundary:{query.security}:{query.replay_date.isoformat()}"
    retrieved_at = batch.retrieved_at.astimezone(
        query.research_boundary.tzinfo
    ).isoformat()
    return {
        "id": evidence_id,
        "source_role": batch.source_role,
        "source_operation": batch.operation_id,
        "contract_version": batch.contract_version,
        "experimental": batch.experimental,
        "subject": {"security": query.security},
        "observed_value": values,
        "basis": {
            "price_adjustment": batch.price_adjustment,
            "price_minimum_tick": batch.price_minimum_tick or batch.price_precision,
            "volume_unit": batch.volume_unit,
            "amount_unit": batch.amount_unit,
            "amount_scale": batch.amount_scale,
        },
        "observation": {
            "kind": "intraday_daily_boundary",
            "trading_date": query.replay_date.isoformat(),
            "trading_status": batch.trading_status,
        },
        "evidence_time": f"{query.replay_date.isoformat()}T15:00:00+08:00",
        "available_at": retrieved_at,
        "retrieved_at": retrieved_at,
        "locator": {"uri": locator, "observation": "daily boundary"},
        "limitations": ["experimental_source_operation"] if batch.experimental else [],
    }


def _safe_locator(value: str) -> bool:
    lowered = value.lower()
    return (
        len(value) <= 200
        and _SAFE_LOCATOR.fullmatch(value) is not None
        and not any(
            marker in lowered
            for marker in ("http", "//", "bearer", "token", "secret", "password")
        )
    )


def _decimal(value: object, source_operation: str, field: str) -> Decimal:
    if isinstance(value, bool):
        raise IntradayReplaySourceError(
            source_operation,
            "daily_unknown_schema",
            f"The daily boundary {field} must be a finite decimal.",
        )
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise IntradayReplaySourceError(
            source_operation,
            "daily_unknown_schema",
            f"The daily boundary {field} must be a finite decimal.",
        ) from error
    if not parsed.is_finite():
        raise IntradayReplaySourceError(
            source_operation,
            "daily_unknown_schema",
            f"The daily boundary {field} must be a finite decimal.",
        )
    return parsed


def _positive_decimal(value: object, source_operation: str, field: str) -> Decimal:
    parsed = _decimal(value, source_operation, field)
    if parsed <= 0:
        raise IntradayReplaySourceError(
            source_operation,
            "daily_invalid_contract_value",
            f"The daily boundary {field} must be positive.",
        )
    return parsed


def _sum_decimal(values: Sequence[str] | Any) -> str:
    total = Decimal("0")
    for value in values:
        total += Decimal(str(value))
    return format(total, "f")
