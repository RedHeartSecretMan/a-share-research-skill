"""Cross-checked daily market series and deterministic trend calculations."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .adjusted_series_sources import (
    EastmoneyForwardAdjustedDailyLineOperation,
    TencentForwardAdjustedDailyLineOperation,
)
from .close_sources import (
    DailyBarObservation,
    SseDailyLineOperation,
    SzseDailyLineOperation,
    TencentDailyLineOperation,
)
from .identity_resolution import resolve_security_identity
from .identity_sources import CHINA_STANDARD_TIME, HttpTransport, SourceOperationError

FOUR_DECIMALS = Decimal("0.0001")


def build_market_trend_result(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one unadjusted, cross-checked completed-session trend result."""

    research_now = now or datetime.now(CHINA_STANDARD_TIME)
    research_date = date.fromisoformat(request["as_of"])
    trading_days = _trading_days(request["window"])
    adjustment = request["parameters"].get("adjustment")
    if adjustment not in {"unadjusted", "forward_adjusted"}:
        return _blocked(
            request,
            code="unsupported_adjustment",
            message=("market_trend supports adjustment=unadjusted or forward_adjusted"),
        )
    identity = _resolve_subject(request, transport)
    if identity["status"] == "blocked":
        return identity
    subject = identity["subjects"][0]
    security_value = subject["security"]
    security = f"{security_value['exchange']}:{security_value['code']}"
    if research_date > research_now.date():
        return _blocked(
            request,
            code="future_research_date",
            message="The research date is later than the retrieval date.",
            identity=identity,
        )

    source_errors = list(identity["source_errors"])
    if adjustment == "forward_adjusted":
        adjusted_primary = EastmoneyForwardAdjustedDailyLineOperation()
        adjusted_secondary = TencentForwardAdjustedDailyLineOperation()
        secondary_operation_id = adjusted_secondary.operation_id
        try:
            official_observations = adjusted_primary.observe(
                security, research_date, transport
            )
        except SourceOperationError as error:
            official_observations = []
            source_errors.append(_source_error(error))
        try:
            fallback_observations = adjusted_secondary.observe(
                security, research_date, transport
            )
        except SourceOperationError as error:
            fallback_observations = []
            source_errors.append(_source_error(error))
    else:
        exchange_operation = (
            SseDailyLineOperation()
            if security.startswith("SSE:")
            else SzseDailyLineOperation()
        )
        unadjusted_secondary = TencentDailyLineOperation()
        secondary_operation_id = unadjusted_secondary.operation_id
        try:
            official_observations = exchange_operation.observe(security, transport)
        except SourceOperationError as error:
            official_observations = []
            source_errors.append(_source_error(error))
        try:
            fallback_observations = unadjusted_secondary.observe(
                security, research_date, transport
            )
        except SourceOperationError as error:
            fallback_observations = []
            source_errors.append(_source_error(error))

    research_boundary = (
        datetime.combine(research_date, time.max, tzinfo=CHINA_STANDARD_TIME)
        if research_date < research_now.date()
        else research_now
    )
    recent_official_rows = sorted(
        (
            item
            for item in official_observations
            if item.trading_date <= research_date
            and item.available_at <= research_boundary
            and item.price_type == "close"
        ),
        key=lambda item: item.trading_date,
    )[-trading_days:]
    suspended_rows = [
        item for item in recent_official_rows if item.trading_status == "suspended"
    ]
    if suspended_rows:
        result = _blocked(
            request,
            code="suspended_session_in_requested_range",
            message=(
                "An official daily line in the requested range is suspended and "
                "cannot be treated as a traded OHLCV session."
            ),
            identity=identity,
            source_errors=source_errors,
        )
        rejected = [item.to_bar_evidence() for item in suspended_rows]
        result["rejected_observations"] = rejected
        result["evidence"] = [*identity["evidence"], *rejected]
        return result
    official = _completed_observations(
        official_observations, research_date, research_boundary
    )
    fallback = _completed_observations(
        fallback_observations, research_date, research_boundary
    )
    selected_official = official[-trading_days:]
    if len(selected_official) < trading_days:
        return _blocked(
            request,
            code="insufficient_trading_sessions",
            message=(
                f"Only {len(selected_official)} completed official sessions are "
                f"available; {trading_days} were requested."
            ),
            identity=identity,
            source_errors=source_errors,
        )

    fallback_by_date = {item.trading_date: item for item in fallback}
    pairs: list[tuple[DailyBarObservation, DailyBarObservation]] = []
    conflicts: list[dict[str, Any]] = []
    for official_bar in selected_official:
        fallback_bar = fallback_by_date.get(official_bar.trading_date)
        if fallback_bar is None:
            conflicts.append(
                {
                    "code": "market_series_missing_session",
                    "trading_date": official_bar.trading_date.isoformat(),
                    "missing_source_operation": secondary_operation_id,
                    "evidence_ids": [official_bar.bar_evidence_id],
                }
            )
            continue
        differing_fields = _differing_fields(official_bar, fallback_bar)
        if differing_fields:
            conflicts.append(
                {
                    "code": "market_series_value_conflict",
                    "trading_date": official_bar.trading_date.isoformat(),
                    "fields": differing_fields,
                    "evidence_ids": [
                        official_bar.bar_evidence_id,
                        fallback_bar.bar_evidence_id,
                    ],
                }
            )
            continue
        pairs.append((official_bar, fallback_bar))

    evidence = list(identity["evidence"])
    for official_bar in selected_official:
        evidence.append(official_bar.to_bar_evidence())
        fallback_bar = fallback_by_date.get(official_bar.trading_date)
        if fallback_bar is not None:
            evidence.append(fallback_bar.to_bar_evidence())
    if source_errors or conflicts or len(pairs) != trading_days:
        result = _blocked(
            request,
            code="market_series_not_cross_checked",
            message="The requested daily series is not complete and consistent.",
            identity=identity,
            source_errors=source_errors,
        )
        result["evidence"] = evidence
        result["conflicts"] = conflicts
        return result

    bars = [official_bar for official_bar, _ in pairs]
    series = [
        _series_row(official_bar, fallback_bar) for official_bar, fallback_bar in pairs
    ]
    corporate_actions = [
        {
            "trading_date": fallback_bar.trading_date.isoformat(),
            "source_operation": fallback_bar.source_operation,
            "details": fallback_bar.corporate_action,
            "evidence_id": fallback_bar.bar_evidence_id,
        }
        for _, fallback_bar in pairs
        if fallback_bar.corporate_action is not None
    ]
    if corporate_actions and adjustment == "unadjusted":
        result = _blocked(
            request,
            code="corporate_action_requires_adjusted_series",
            message=(
                "The unadjusted window contains a source-annotated corporate "
                "action, so trend metrics are not comparable across the window."
            ),
            identity=identity,
        )
        result["series"] = series
        result["corporate_actions"] = corporate_actions
        result["evidence"] = evidence
        return result

    metrics, calculations = _metrics(bars)
    limitations = list(identity["limitations"])
    limitations.append(
        {
            "code": "experimental_market_series",
            "message": (
                "OHLCV agrees across two experimental source operations; the "
                "operations have not completed source qualification."
            ),
        }
    )
    if corporate_actions:
        limitations.append(
            {
                "code": "forward_adjusted_series_contains_corporate_action",
                "message": (
                    "The forward-adjusted window contains a source-annotated "
                    "corporate action retained in the result."
                ),
            }
        )
    first_close = Decimal(bars[0].close_value)
    last_close = Decimal(bars[-1].close_value)
    close_trend = (
        "up"
        if last_close > first_close
        else "down"
        if last_close < first_close
        else "flat"
    )
    return {
        "schema_version": request["schema_version"],
        "status": "limited",
        "subjects": identity["subjects"],
        "research": {
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
            "retrieved_at": research_now.isoformat(),
            "adjustment": adjustment,
        },
        "window": {
            "requested_trading_days": trading_days,
            "actual_trading_days": len(bars),
            "start": bars[0].trading_date.isoformat(),
            "end": bars[-1].trading_date.isoformat(),
        },
        "series": series,
        "metrics": metrics,
        "conclusion": {
            "close_trend": close_trend,
            "statement": (
                f"The completed-session close trend is {close_trend} over the "
                f"requested {trading_days}-session window."
            ),
            "calculation_ids": [
                "cumulative_return",
                "maximum_drawdown",
                "annualized_volatility",
                "volume_change",
            ],
        },
        "corporate_actions": corporate_actions,
        "calculations": calculations,
        "evidence": evidence,
        "conflicts": [],
        "source_errors": [],
        "degradations": [],
        "limitations": limitations,
    }


def _resolve_subject(
    request: dict[str, Any], transport: HttpTransport
) -> dict[str, Any]:
    subjects = request["subjects"]
    if len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ValueError("market_trend requires exactly one subject object")
    subject = subjects[0]
    clue = subject.get("clue")
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("market_trend subject requires a non-empty clue")
    identity = resolve_security_identity(clue.strip(), request["as_of"], transport)
    candidate_value = identity.get("candidates", [])
    candidates = candidate_value if isinstance(candidate_value, list) else []
    if identity["status"] == "blocked" or len(candidates) != 1:
        return {
            "schema_version": request["schema_version"],
            "status": "blocked",
            "subjects": [],
            "evidence": identity.get("evidence", []),
            "conflicts": identity.get("conflicts", []),
            "source_errors": identity.get("source_errors", []),
            "degradations": [],
            "limitations": identity.get("limitations", []),
        }
    candidate = candidates[0]
    if not isinstance(candidate, dict) or not isinstance(
        candidate.get("security"), dict
    ):
        raise ValueError("identity result does not contain a canonical security")
    security = candidate["security"]
    return {
        "status": identity["status"],
        "subjects": [
            {
                "security": {
                    "exchange": security["exchange"],
                    "code": security["code"],
                    "type": security["type"],
                },
                "name": candidate["name"],
                "issuer": candidate["issuer"],
            }
        ],
        "evidence": identity.get("evidence", []),
        "conflicts": identity.get("conflicts", []),
        "source_errors": identity.get("source_errors", []),
        "limitations": identity.get("limitations", []),
    }


def _completed_observations(
    observations: list[DailyBarObservation],
    research_date: date,
    research_boundary: datetime,
) -> list[DailyBarObservation]:
    return sorted(
        (
            item
            for item in observations
            if item.trading_date <= research_date
            and item.available_at <= research_boundary
            and item.price_type == "close"
            and item.trading_status == "traded"
        ),
        key=lambda item: item.trading_date,
    )


def _differing_fields(
    first: DailyBarObservation, second: DailyBarObservation
) -> list[str]:
    fields = {
        "open": (first.open_value, second.open_value),
        "high": (first.high_value, second.high_value),
        "low": (first.low_value, second.low_value),
        "close": (first.close_value, second.close_value),
        "volume": (first.volume_shares, second.volume_shares),
        "adjustment": (first.adjustment, second.adjustment),
    }
    differing = []
    for field, values in fields.items():
        mismatch = (
            values[0] != values[1]
            if field == "adjustment"
            else Decimal(values[0]) != Decimal(values[1])
        )
        if mismatch:
            differing.append(field)
    return differing


def _series_row(
    official: DailyBarObservation, fallback: DailyBarObservation
) -> dict[str, Any]:
    return {
        "trading_date": official.trading_date.isoformat(),
        "open": official.open_value,
        "high": official.high_value,
        "low": official.low_value,
        "close": official.close_value,
        "volume": official.volume_shares,
        "volume_unit": "shares",
        "adjustment": official.adjustment,
        "evidence_ids": [official.bar_evidence_id, fallback.bar_evidence_id],
    }


def _metrics(
    bars: list[DailyBarObservation],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    closes = [Decimal(item.close_value) for item in bars]
    volumes = [Decimal(item.volume_shares) for item in bars]
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    cumulative_return = closes[-1] / closes[0] - 1
    peak = closes[0]
    maximum_drawdown = Decimal(0)
    for close in closes:
        peak = max(peak, close)
        maximum_drawdown = min(maximum_drawdown, close / peak - 1)
    if len(returns) >= 2:
        mean_return = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum(
            ((item - mean_return) ** 2 for item in returns), Decimal(0)
        ) / Decimal(len(returns) - 1)
        annualized_volatility_metric: dict[str, str] = {
            **_percent_metric(variance.sqrt() * Decimal(252).sqrt()),
            "basis": "sample_stddev_of_daily_simple_returns_sqrt_252",
        }
        annualized_volatility_calculation: dict[str, Any] = {
            "id": "annualized_volatility",
            "formula": "sample_stddev(daily_simple_returns) * sqrt(252) * 100",
            "operands": {"closes": [str(item) for item in closes]},
            "evidence_ids": [item.bar_evidence_id for item in bars],
        }
    else:
        annualized_volatility_metric = {
            "status": "not_computable",
            "reason": "sample volatility requires at least two daily returns",
        }
        annualized_volatility_calculation = {
            "id": "annualized_volatility",
            "status": "not_computable",
            "formula": "sample_stddev(daily_simple_returns) * sqrt(252) * 100",
            "operands": {"closes": [str(item) for item in closes]},
            "evidence_ids": [item.bar_evidence_id for item in bars],
        }
    comparison_sessions = len(volumes) // 2
    comparison_decimal = Decimal(comparison_sessions)
    first_average_volume = (
        sum(volumes[:comparison_sessions], Decimal(0)) / comparison_decimal
    )
    last_average_volume = (
        sum(volumes[-comparison_sessions:], Decimal(0)) / comparison_decimal
    )
    volume_change = last_average_volume / first_average_volume - Decimal(1)
    evidence_ids = [item.bar_evidence_id for item in bars]
    metrics = {
        "cumulative_return": _percent_metric(cumulative_return),
        "maximum_drawdown": _percent_metric(maximum_drawdown),
        "annualized_volatility": annualized_volatility_metric,
        "up_sessions": sum(item > 0 for item in returns),
        "down_sessions": sum(item < 0 for item in returns),
        "unchanged_sessions": sum(item == 0 for item in returns),
        "volume_change": {
            **_percent_metric(volume_change),
            "basis": (
                f"last_{comparison_sessions}_session_average_vs_"
                f"first_{comparison_sessions}_session_average"
            ),
        },
    }
    calculations = [
        {
            "id": "cumulative_return",
            "formula": "(last_close / first_close - 1) * 100",
            "operands": {
                "first_close": str(closes[0]),
                "last_close": str(closes[-1]),
            },
            "evidence_ids": [evidence_ids[0], evidence_ids[-1]],
        },
        {
            "id": "maximum_drawdown",
            "formula": "min(close / running_peak_close - 1) * 100",
            "operands": {"closes": [str(item) for item in closes]},
            "evidence_ids": evidence_ids,
        },
        annualized_volatility_calculation,
        {
            "id": "volume_change",
            "formula": "(last_half_average_volume / first_half_average_volume - 1) * 100",
            "operands": {"volumes_shares": [str(item) for item in volumes]},
            "evidence_ids": evidence_ids,
        },
    ]
    return metrics, calculations


def _percent_metric(value: Decimal) -> dict[str, str]:
    rounded = (value * 100).quantize(FOUR_DECIMALS, rounding=ROUND_HALF_UP)
    return {"value": format(rounded, "f"), "unit": "percent"}


def _trading_days(window: Any) -> int:
    if not isinstance(window, dict):
        raise ValueError("market_trend window must be a JSON object")
    trading_days = window.get("trading_days")
    if (
        not isinstance(trading_days, int)
        or isinstance(trading_days, bool)
        or not 2 <= trading_days <= 250
    ):
        raise ValueError("market_trend trading_days must be an integer from 2 to 250")
    return trading_days


def _source_error(error: SourceOperationError) -> dict[str, str]:
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": str(error),
    }


def _blocked(
    request: dict[str, Any],
    *,
    code: str,
    message: str,
    identity: dict[str, Any] | None = None,
    source_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    identity = identity or {}
    limitations = list(identity.get("limitations", []))
    limitations.append({"code": code, "message": message})
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": identity.get("subjects", request["subjects"]),
        "series": [],
        "metrics": {},
        "conclusion": {"close_trend": "unresolved"},
        "corporate_actions": [],
        "calculations": [],
        "evidence": identity.get("evidence", []),
        "conflicts": identity.get("conflicts", []),
        "source_errors": source_errors or identity.get("source_errors", []),
        "degradations": [],
        "limitations": limitations,
    }
