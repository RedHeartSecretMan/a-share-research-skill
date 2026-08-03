"""Deterministic, evidence-scoped calculations for an intraday replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PRICE_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.0001")
VWAP_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class ReplaySummaryRow:
    """Normalized row exposed to the summary calculator."""

    interval_start: datetime
    interval_end: datetime
    trading_phase: str
    trade_state: str
    ohlc: Mapping[str, Any]
    volume: str
    amount: str
    evidence_id: str


def build_intraday_replay_summary(
    rows: Sequence[ReplaySummaryRow],
    auction_rows: Sequence[ReplaySummaryRow],
    coverage: Mapping[str, Any],
    daily_boundary: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build a deterministic summary without filling or joining across gaps."""

    continuous = sorted(rows, key=_row_key)
    auctions = sorted(auction_rows, key=_row_key)
    all_rows = [*continuous, *auctions]
    traded_continuous = [row for row in continuous if row.trade_state == "traded"]
    traded_all = [row for row in all_rows if row.trade_state == "traded"]
    coverage_status = str(coverage.get("status", "unavailable"))
    expected_minutes = _expected_minutes(coverage)
    missing_intervals = _list_of_dicts(coverage.get("missing_intervals"))
    summary: dict[str, Any] = {
        "version": "1.0",
        "calculation": "intraday_replay_summary@1",
        "scope": {
            "coverage_status": coverage_status,
            "timezone": "Asia/Shanghai",
            "continuous_phases": ["continuous_morning", "continuous_afternoon"],
            "lunch_break_excluded": True,
            "auctions_separate": True,
        },
        "counts": {
            "record_count": len(continuous),
            "continuous_records": len(continuous),
            "traded_intervals": len(traded_continuous),
            "proven_no_trade_intervals": sum(
                row.trade_state == "no_trade" for row in continuous
            ),
            "covered_intervals": len(continuous),
            "expected_intervals": expected_minutes,
            "missing_intervals": missing_intervals,
        },
        "coverage": {
            "status": coverage_status,
            "coverage_ratio": coverage.get(
                "coverage_ratio",
                {"status": "unavailable", "reason": "coverage_ratio_unavailable"},
            ),
            "missing_intervals": missing_intervals,
            "formula": "covered_minutes / expected_minutes",
        },
    }

    endpoints = _endpoints(continuous, auctions, coverage, daily_boundary)
    metrics: dict[str, Any] = {
        "endpoints": endpoints,
        "open_to_close": _open_to_close(endpoints),
        "opening_gap": _opening_gap(endpoints, daily_boundary),
        "relative_return": _relative_return(endpoints, daily_boundary),
    }

    high, low = _high_low(all_rows, coverage)
    metrics["high"] = high
    metrics["low"] = low
    absolute_range, relative_range = _ranges(high, low, daily_boundary, coverage)
    metrics["intraday_range"] = {
        "status": absolute_range.get("status", "unavailable"),
        "absolute": absolute_range,
        "relative": relative_range,
    }
    metrics["absolute_range"] = absolute_range
    metrics["relative_range"] = relative_range
    metrics["vwap"] = _vwap(traded_all, coverage)
    metrics["max_drawdown"] = _max_drawdown(traded_continuous, coverage)

    for phase, label in (
        ("continuous_morning", "morning"),
        ("continuous_afternoon", "afternoon"),
    ):
        phase_rows = [row for row in traded_continuous if row.trading_phase == phase]
        phase_return = _session_return(phase_rows, coverage)
        phase_share = _session_volume_share(phase_rows, traded_continuous, coverage)
        metrics[f"{label}_return"] = phase_return
        metrics[f"{label}_volume_share"] = phase_share
        metrics[label] = {"return": phase_return, "volume_share": phase_share}

    metrics["max_minute_volume"] = _max_amount_or_volume(
        traded_continuous, coverage, field="volume", unit="shares", quantum=Decimal(1)
    )
    metrics["max_minute_amount"] = _max_amount_or_volume(
        traded_continuous, coverage, field="amount", unit="CNY", quantum=Decimal("0.01")
    )
    rise, fall = _adjacent_changes(traded_continuous, coverage)
    metrics["max_adjacent_rise"] = rise
    metrics["max_adjacent_fall"] = fall

    unavailable_fields = _unavailable_fields(metrics)
    summary["metrics"] = metrics
    summary["unavailable_metrics"] = unavailable_fields
    summary["lineage"] = _lineage(metrics)
    summary["evidence_ids"] = _unique_ids(all_rows)
    return summary, unavailable_fields


def _row_key(row: ReplaySummaryRow) -> tuple[datetime, datetime, str]:
    return row.interval_start, row.interval_end, row.evidence_id


def _expected_minutes(coverage: Mapping[str, Any]) -> int:
    ratio = coverage.get("coverage_ratio")
    if isinstance(ratio, Mapping):
        value = ratio.get("expected_minutes")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _row_price(row: ReplaySummaryRow, field: str) -> Decimal | None:
    value = row.ohlc.get(field)
    if not isinstance(value, Mapping):
        return None
    raw = value.get("value")
    if not isinstance(raw, str):
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return Decimal(0)
    return parsed if parsed.is_finite() else Decimal(0)


def _text(value: Decimal, quantum: Decimal) -> str:
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _ids(rows: Sequence[ReplaySummaryRow]) -> list[str]:
    return sorted({row.evidence_id for row in rows})


def _unique_ids(rows: Sequence[ReplaySummaryRow]) -> list[str]:
    return _ids(rows)


def _interval(row: ReplaySummaryRow) -> dict[str, str]:
    return {
        "interval_start": row.interval_start.isoformat(),
        "interval_end": row.interval_end.isoformat(),
        "trading_phase": row.trading_phase,
    }


def _scope(
    rows: Sequence[ReplaySummaryRow],
    coverage: Mapping[str, Any],
    *,
    includes_auctions: bool = False,
) -> dict[str, Any]:
    ordered = sorted(rows, key=_row_key)
    return {
        "coverage_status": str(coverage.get("status", "unavailable")),
        "trading_phases": sorted({row.trading_phase for row in ordered}),
        "intervals": [_interval(row) for row in ordered],
        "includes_auctions": includes_auctions,
    }


def _metric(
    value: str,
    unit: str,
    formula: str,
    operands: Mapping[str, Any],
    scope: Mapping[str, Any],
    evidence_ids: Sequence[str],
    quantum: Decimal,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": "available",
        "value": value,
        "unit": unit,
        "formula": formula,
        "operands": dict(operands),
        "rounding": f"ROUND_HALF_UP to quantum {quantum}",
        "scope": dict(scope),
        "evidence_ids": sorted(set(evidence_ids)),
        **extra,
    }


def _unavailable(reason: str) -> dict[str, str]:
    return {"status": "unavailable", "reason": reason}


def _endpoint(row: ReplaySummaryRow, field: str, source: str) -> dict[str, Any]:
    value = _row_price(row, field)
    if value is None:
        return _unavailable("transaction_price_unavailable")
    return {
        "status": "available",
        "value": format(value, "f"),
        "unit": "CNY/share",
        "source": source,
        "interval_start": row.interval_start.isoformat(),
        "interval_end": row.interval_end.isoformat(),
        "trading_phase": row.trading_phase,
        "evidence_ids": [row.evidence_id],
    }


def _endpoints(
    continuous: Sequence[ReplaySummaryRow],
    auctions: Sequence[ReplaySummaryRow],
    coverage: Mapping[str, Any],
    daily_boundary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    coverage_status = str(coverage.get("status", "unavailable"))
    traded_continuous = [row for row in continuous if row.trade_state == "traded"]
    traded_opening = [
        row
        for row in auctions
        if row.trading_phase == "opening_auction" and row.trade_state == "traded"
    ]
    traded_closing = [
        row
        for row in auctions
        if row.trading_phase == "closing_auction" and row.trade_state == "traded"
    ]
    first = traded_opening or (
        traded_continuous
        if continuous and _covers_session_start(continuous, coverage)
        else []
    )
    opening = (
        _endpoint(first[0], "open", first[0].trading_phase)
        if first
        else _unavailable("actual_open_not_established")
    )
    if traded_closing:
        closing = _endpoint(traded_closing[-1], "close", "closing_auction")
    else:
        closing = _daily_close(daily_boundary)
        if closing.get("status") != "available":
            closing_rows = [
                row for row in auctions if row.trading_phase == "closing_auction"
            ]
            if (
                coverage_status == "complete"
                and closing_rows
                and all(row.trade_state == "no_trade" for row in closing_rows)
                and traded_continuous
                and _covers_session_end(continuous, coverage)
            ):
                closing = _endpoint(
                    traded_continuous[-1], "close", "last_continuous_transaction"
                )
            else:
                closing = _unavailable("actual_close_not_established")
    return {"open": opening, "close": closing}


def _coverage_boundary(
    coverage: Mapping[str, Any], index: int, field: str
) -> str | None:
    expected = coverage.get("expected_intervals")
    if not isinstance(expected, list) or len(expected) <= index:
        return None
    interval = expected[index]
    if not isinstance(interval, Mapping):
        return None
    value = interval.get(field)
    return value if isinstance(value, str) else None


def _covers_session_start(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> bool:
    expected_start = _coverage_boundary(coverage, 0, "interval_start")
    return (
        expected_start is not None
        and rows[0].interval_start.isoformat() == expected_start
    )


def _covers_session_end(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> bool:
    expected_end = _coverage_boundary(coverage, 1, "interval_end")
    return (
        expected_end is not None and rows[-1].interval_end.isoformat() == expected_end
    )


def _daily_close(daily_boundary: Mapping[str, Any] | None) -> dict[str, Any]:
    if daily_boundary is None or daily_boundary.get("status") != "cross_checked":
        return _unavailable("actual_close_not_established")
    value = daily_boundary.get("actual_close")
    if not isinstance(value, Mapping) or not isinstance(value.get("value"), str):
        return _unavailable("actual_close_not_established")
    return {
        "status": "available",
        "value": value["value"],
        "unit": "CNY/share",
        "source": "daily_boundary.actual_close",
        "evidence_ids": list(daily_boundary.get("evidence_ids", [])),
    }


def _open_to_close(endpoints: Mapping[str, Any]) -> dict[str, Any]:
    opening = endpoints.get("open")
    closing = endpoints.get("close")
    opening_mapping = _available_endpoint_mapping(opening)
    closing_mapping = _available_endpoint_mapping(closing)
    if opening_mapping is None:
        return _unavailable("actual_open_not_established")
    if closing_mapping is None:
        return _unavailable("actual_close_not_established")
    open_value = _decimal(str(opening_mapping["value"]))
    close_value = _decimal(str(closing_mapping["value"]))
    change = close_value - open_value
    if open_value == 0:
        return _unavailable("actual_open_zero")
    return {
        "status": "available",
        "absolute_change": _metric(
            _text(change, PRICE_QUANTUM),
            "CNY/share",
            "actual_close - actual_open",
            {"actual_open": opening_mapping, "actual_close": closing_mapping},
            {"endpoints": ["actual_open", "actual_close"]},
            _endpoint_ids(opening_mapping, closing_mapping),
            PRICE_QUANTUM,
        ),
        "return": _metric(
            _text(change / open_value, RATIO_QUANTUM),
            "ratio",
            "(actual_close - actual_open) / actual_open",
            {"actual_open": opening_mapping, "actual_close": closing_mapping},
            {"endpoints": ["actual_open", "actual_close"]},
            _endpoint_ids(opening_mapping, closing_mapping),
            RATIO_QUANTUM,
        ),
        "operands": {
            "actual_open": opening_mapping,
            "actual_close": closing_mapping,
        },
    }


def _available_endpoint(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("status") == "available"


def _available_endpoint_mapping(value: object) -> Mapping[str, Any] | None:
    if _available_endpoint(value) and isinstance(value, Mapping):
        return value
    return None


def _endpoint_ids(*values: object) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            ids = value.get("evidence_ids")
            if isinstance(ids, list):
                result.extend(item for item in ids if isinstance(item, str))
    return sorted(set(result))


def _baseline(
    daily_boundary: Mapping[str, Any] | None,
) -> tuple[Decimal | None, list[str], str]:
    if daily_boundary is None:
        return None, [], "actual_previous_close_unavailable"
    if daily_boundary.get("status") != "cross_checked":
        reason = daily_boundary.get("reason")
        return (
            None,
            [],
            reason if isinstance(reason, str) else "daily_boundary_not_cross_checked",
        )
    baselines = daily_boundary.get("baselines")
    if not isinstance(baselines, Mapping):
        return None, [], "actual_previous_close_unavailable"
    actual = baselines.get("actual_unadjusted_close")
    comparability = baselines.get("comparability")
    reason = "actual_previous_close_unavailable"
    if isinstance(comparability, Mapping) and isinstance(
        comparability.get("reason"), str
    ):
        reason = comparability["reason"]
    if not isinstance(actual, Mapping) or actual.get("status") != "available":
        return None, [], reason
    value = actual.get("value")
    if not isinstance(value, Mapping) or not isinstance(value.get("value"), str):
        return None, [], reason
    return _decimal(value["value"]), _string_list(actual.get("evidence_ids")), ""


def _opening_gap(
    endpoints: Mapping[str, Any], daily_boundary: Mapping[str, Any] | None
) -> dict[str, Any]:
    opening = endpoints.get("open")
    baseline, ids, reason = _baseline(daily_boundary)
    if not _available_endpoint(opening):
        return _unavailable("actual_open_not_established")
    if baseline is None:
        return _unavailable(reason)
    opening_mapping = _available_endpoint_mapping(opening)
    if opening_mapping is None:
        return _unavailable("actual_open_not_established")
    opening_value = _decimal(str(opening_mapping["value"]))
    if baseline == 0:
        return _unavailable("actual_previous_close_zero")
    change = opening_value - baseline
    return {
        "status": "available",
        "absolute_change": _metric(
            _text(change, PRICE_QUANTUM),
            "CNY/share",
            "actual_open - actual_previous_close",
            {
                "actual_open": opening_mapping,
                "actual_previous_close": _text(baseline, PRICE_QUANTUM),
            },
            {"endpoints": ["actual_open", "actual_previous_close"]},
            _endpoint_ids(opening_mapping) + ids,
            PRICE_QUANTUM,
        ),
        "return": _metric(
            _text(change / baseline, RATIO_QUANTUM),
            "ratio",
            "(actual_open - actual_previous_close) / actual_previous_close",
            {
                "actual_open": opening_mapping,
                "actual_previous_close": _text(baseline, PRICE_QUANTUM),
            },
            {"endpoints": ["actual_open", "actual_previous_close"]},
            _endpoint_ids(opening_mapping) + ids,
            RATIO_QUANTUM,
        ),
    }


def _relative_return(
    endpoints: Mapping[str, Any], daily_boundary: Mapping[str, Any] | None
) -> dict[str, Any]:
    closing = endpoints.get("close")
    baseline, ids, reason = _baseline(daily_boundary)
    if not _available_endpoint(closing):
        return _unavailable("actual_close_not_established")
    if baseline is None:
        return _unavailable(reason)
    closing_mapping = _available_endpoint_mapping(closing)
    if closing_mapping is None:
        return _unavailable("actual_close_not_established")
    close_value = _decimal(str(closing_mapping["value"]))
    if baseline == 0:
        return _unavailable("actual_previous_close_zero")
    change = close_value - baseline
    return _metric(
        _text(change / baseline, RATIO_QUANTUM),
        "ratio",
        "(actual_close - actual_previous_close) / actual_previous_close",
        {
            "actual_close": closing_mapping,
            "actual_previous_close": _text(baseline, PRICE_QUANTUM),
        },
        {"endpoints": ["actual_close", "actual_previous_close"]},
        _endpoint_ids(closing_mapping) + ids,
        RATIO_QUANTUM,
    )


def _high_low(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _extreme(rows, coverage, "high", max, "full-day high"),
        _extreme(rows, coverage, "low", min, "full-day low"),
    )


def _extreme(
    rows: Sequence[ReplaySummaryRow],
    coverage: Mapping[str, Any],
    field: str,
    selector: Any,
    description: str,
) -> dict[str, Any]:
    candidates = [
        (row, price)
        for row in rows
        if row.trade_state == "traded"
        for price in [_row_price(row, field)]
        if price is not None
    ]
    if not candidates:
        return _unavailable("no_traded_prices")
    value = selector(price for _, price in candidates)
    tied = [(row, price) for row, price in candidates if price == value]
    return _metric(
        format(value, "f"),
        "CNY/share",
        f"{description} over admitted traded {field} values",
        {"value": format(value, "f"), "tie_count": len(tied)},
        _scope(
            [row for row, _ in tied],
            coverage,
            includes_auctions=any(
                row.trading_phase in {"opening_auction", "closing_auction"}
                for row, _ in tied
            ),
        ),
        [row.evidence_id for row, _ in tied],
        PRICE_QUANTUM,
        times=[row.interval_start.isoformat() for row, _ in tied],
        intervals=[_interval(row) for row, _ in tied],
    )


def _ranges(
    high: Mapping[str, Any],
    low: Mapping[str, Any],
    daily_boundary: Mapping[str, Any] | None,
    coverage: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if high.get("status") != "available" or low.get("status") != "available":
        absolute = _unavailable("high_low_unavailable")
        return absolute, _unavailable("high_low_unavailable")
    high_value = _decimal(str(high["value"]))
    low_value = _decimal(str(low["value"]))
    baseline, ids, reason = _baseline(daily_boundary)
    absolute = _metric(
        _text(high_value - low_value, PRICE_QUANTUM),
        "CNY/share",
        "high - low",
        {"high": high, "low": low},
        {"coverage_status": coverage.get("status", "unavailable")},
        _string_list(high.get("evidence_ids")) + _string_list(low.get("evidence_ids")),
        PRICE_QUANTUM,
    )
    if baseline is None:
        relative = _unavailable(reason)
    elif baseline == 0:
        relative = _unavailable("actual_previous_close_zero")
    else:
        relative = _metric(
            _text((high_value - low_value) / baseline, RATIO_QUANTUM),
            "ratio",
            "(high - low) / actual_previous_close",
            {
                "high": high,
                "low": low,
                "actual_previous_close": _text(baseline, PRICE_QUANTUM),
            },
            {"coverage_status": coverage.get("status", "unavailable")},
            _string_list(high.get("evidence_ids"))
            + _string_list(low.get("evidence_ids"))
            + ids,
            RATIO_QUANTUM,
        )
    return absolute, relative


def _vwap(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> dict[str, Any]:
    if not rows:
        return _unavailable("no_traded_intervals")
    total_volume = sum((_decimal(row.volume) for row in rows), Decimal(0))
    total_amount = sum((_decimal(row.amount) for row in rows), Decimal(0))
    if total_volume == 0:
        return _unavailable("total_traded_volume_zero")
    return _metric(
        _text(total_amount / total_volume, VWAP_QUANTUM),
        "CNY/share",
        "total_amount / total_shares",
        {
            "total_amount": {
                "value": _text(total_amount, Decimal("0.01")),
                "unit": "CNY",
            },
            "total_shares": {
                "value": _text(total_volume, Decimal(1)),
                "unit": "shares",
            },
        },
        _scope(
            rows,
            coverage,
            includes_auctions=any(
                row.trading_phase in {"opening_auction", "closing_auction"}
                for row in rows
            ),
        ),
        _ids(rows),
        VWAP_QUANTUM,
    )


def _segments(rows: Sequence[ReplaySummaryRow]) -> list[list[ReplaySummaryRow]]:
    segments: list[list[ReplaySummaryRow]] = []
    for row in sorted(rows, key=_row_key):
        if not segments:
            segments.append([row])
            continue
        previous = segments[-1][-1]
        if (
            previous.trading_phase == row.trading_phase
            and previous.interval_end == row.interval_start
        ):
            segments[-1].append(row)
        else:
            segments.append([row])
    return segments


def _max_drawdown(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> dict[str, Any]:
    best: tuple[Decimal, ReplaySummaryRow, ReplaySummaryRow] | None = None
    for segment in _segments(rows):
        if len(segment) < 2:
            continue
        peak_row = segment[0]
        peak = _row_price(peak_row, "close")
        if peak is None:
            continue
        for row in segment[1:]:
            close = _row_price(row, "close")
            if close is None:
                continue
            decline = peak - close
            if best is None or decline > best[0]:
                best = (decline, peak_row, row)
            if close > peak:
                peak = close
                peak_row = row
    if best is None:
        return _unavailable("no_contiguous_close_pair")
    decline, peak_row, trough_row = best
    return _metric(
        _text(decline, PRICE_QUANTUM),
        "CNY/share",
        "running_peak_close - later_close within one uninterrupted continuous segment",
        {
            "peak": _close_operand(peak_row),
            "trough": _close_operand(trough_row),
        },
        _scope([peak_row, trough_row], coverage),
        [peak_row.evidence_id, trough_row.evidence_id],
        PRICE_QUANTUM,
        peak=_close_operand(peak_row),
        trough=_close_operand(trough_row),
    )


def _close_operand(row: ReplaySummaryRow) -> dict[str, Any]:
    price = _row_price(row, "close")
    return {
        "value": format(price, "f") if price is not None else None,
        "unit": "CNY/share",
        "interval_start": row.interval_start.isoformat(),
        "interval_end": row.interval_end.isoformat(),
        "evidence_ids": [row.evidence_id],
    }


def _session_return(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> dict[str, Any]:
    if not rows:
        return _unavailable("no_traded_rows_in_session")
    first = _row_price(rows[0], "open")
    last = _row_price(rows[-1], "close")
    if first is None or last is None:
        return _unavailable("session_endpoint_price_unavailable")
    if first == 0:
        return _unavailable("session_open_zero")
    return _metric(
        _text((last - first) / first, RATIO_QUANTUM),
        "ratio",
        "(session_last_close - session_first_open) / session_first_open",
        {
            "session_first_open": {"value": format(first, "f"), "unit": "CNY/share"},
            "session_last_close": {"value": format(last, "f"), "unit": "CNY/share"},
        },
        _scope(rows, coverage),
        _ids(rows),
        RATIO_QUANTUM,
    )


def _session_volume_share(
    rows: Sequence[ReplaySummaryRow],
    all_rows: Sequence[ReplaySummaryRow],
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    denominator = sum((_decimal(row.volume) for row in all_rows), Decimal(0))
    if denominator == 0:
        return _unavailable("total_traded_volume_zero")
    numerator = sum((_decimal(row.volume) for row in rows), Decimal(0))
    return _metric(
        _text(numerator / denominator, RATIO_QUANTUM),
        "ratio",
        "session_traded_shares / all_continuous_traded_shares",
        {
            "session_traded_shares": {
                "value": _text(numerator, Decimal(1)),
                "unit": "shares",
            },
            "all_continuous_traded_shares": {
                "value": _text(denominator, Decimal(1)),
                "unit": "shares",
            },
        },
        _scope(rows, coverage),
        _ids(rows) + _ids(all_rows),
        RATIO_QUANTUM,
    )


def _max_amount_or_volume(
    rows: Sequence[ReplaySummaryRow],
    coverage: Mapping[str, Any],
    *,
    field: str,
    unit: str,
    quantum: Decimal,
) -> dict[str, Any]:
    if not rows:
        return _unavailable("no_traded_intervals")
    values = [(row, _decimal(getattr(row, field))) for row in rows]
    value = max(item[1] for item in values)
    ties = [item for item in values if item[1] == value]
    return _metric(
        _text(value, quantum),
        unit,
        f"maximum {field} over continuous traded intervals",
        {"value": _text(value, quantum), "tie_count": len(ties)},
        _scope(rows, coverage),
        _ids([row for row, _ in ties]),
        quantum,
        times=[row.interval_start.isoformat() for row, _ in ties],
        intervals=[_interval(row) for row, _ in ties],
    )


def _adjacent_changes(
    rows: Sequence[ReplaySummaryRow], coverage: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    pairs = [
        (previous, current)
        for segment in _segments(rows)
        for previous, current in zip(segment, segment[1:], strict=False)
    ]
    rises: list[tuple[ReplaySummaryRow, ReplaySummaryRow, Decimal]] = []
    falls: list[tuple[ReplaySummaryRow, ReplaySummaryRow, Decimal]] = []
    for previous, current in pairs:
        previous_close = _row_price(previous, "close")
        current_close = _row_price(current, "close")
        if previous_close is None or current_close is None:
            continue
        change = current_close - previous_close
        if change > 0:
            rises.append((previous, current, change))
        elif change < 0:
            falls.append((previous, current, -change))
    return (
        _adjacent_metric(rises, coverage, "rise", "current_close - previous_close"),
        _adjacent_metric(falls, coverage, "fall", "previous_close - current_close"),
    )


def _adjacent_metric(
    candidates: Sequence[tuple[ReplaySummaryRow, ReplaySummaryRow, Decimal]],
    coverage: Mapping[str, Any],
    direction: str,
    formula: str,
) -> dict[str, Any]:
    if not candidates:
        return _unavailable(f"no_adjacent_{direction}_intervals")
    value = max(item[2] for item in candidates)
    ties = [item for item in candidates if item[2] == value]
    intervals = [
        {
            "previous": _interval(previous),
            "current": _interval(current),
        }
        for previous, current, _ in ties
    ]
    tied_operands = [
        {
            "previous_close": _close_operand(previous),
            "current_close": _close_operand(current),
        }
        for previous, current, _ in ties
    ]
    return _metric(
        _text(value, PRICE_QUANTUM),
        "CNY/share",
        formula,
        {
            "previous_close": tied_operands[0]["previous_close"],
            "current_close": tied_operands[0]["current_close"],
            "tie_count": len(ties),
            "ties": tied_operands,
        },
        _scope([row for item in ties for row in item[:2]], coverage),
        _ids([row for item in ties for row in item[:2]]),
        PRICE_QUANTUM,
        intervals=intervals,
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _unavailable_fields(metrics: Mapping[str, Any]) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for name, metric in metrics.items():
        if not isinstance(metric, Mapping):
            continue
        if metric.get("status") == "unavailable":
            reason = metric.get("reason")
            if isinstance(reason, str):
                field = f"replay.summary.metrics.{name}"
                if field not in seen:
                    fields.append({"field": field, "reason": reason})
                    seen.add(field)
        if name == "intraday_range":
            for child_name in ("absolute", "relative"):
                child = metric.get(child_name)
                if isinstance(child, Mapping) and child.get("status") == "unavailable":
                    reason = child.get("reason")
                    if isinstance(reason, str):
                        field = f"replay.summary.metrics.{child_name}_range"
                        if field not in seen:
                            fields.append({"field": field, "reason": reason})
                            seen.add(field)
    return fields


def _lineage(metrics: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, metric in metrics.items():
        ids = _nested_ids(metric)
        formula = metric.get("formula") if isinstance(metric, Mapping) else None
        result[f"metrics.{name}"] = {
            "evidence_ids": ids,
            "source_fields": ["records", "auction_results"],
            "calculation": formula
            if isinstance(formula, str)
            else "nested_metric_operands",
        }
    return result


def _nested_ids(value: object) -> list[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "evidence_ids" and isinstance(child, list):
                found.update(item for item in child if isinstance(item, str))
            else:
                found.update(_nested_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_nested_ids(child))
    return sorted(found)
