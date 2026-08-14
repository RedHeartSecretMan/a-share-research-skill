"""Cross-checked daily market series and deterministic trend calculations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, cast

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
from .evidence_store import (
    EvidenceReusePolicy,
    EvidenceStoreRequest,
    resolve_operation_evidence,
)
from .identity_resolution import execute_security_identity_research
from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpTransport,
    SourceOperationError,
    build_default_identity_operations,
)
from .research_result import (
    DirectTaskCalculation,
    DirectTaskClaim,
    DirectTaskConflict,
    DirectTaskLimitation,
    DirectTaskPlan,
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    UnifiedResearchResult,
    fold_direct_task_research_result,
)

FOUR_DECIMALS = Decimal("0.0001")


def build_market_trend_result(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
    *,
    evidence_store: EvidenceStoreRequest | None = None,
) -> UnifiedResearchResult:
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
            evidence_store=evidence_store,
        )
    if not request["source_policy"]["allow_experimental"]:
        return _market_policy_block(request, adjustment, evidence_store)
    subjects = request["subjects"]
    if len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ValueError("market_trend requires exactly one subject object")
    clue = subjects[0].get("clue")
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("market_trend subject requires a non-empty clue")
    identity_request: dict[str, object] = {
        **request,
        "task_type": "security_identity",
        "subjects": [{"clue": clue.strip()}],
        "window": None,
        "parameters": {},
    }
    identity_execution = execute_security_identity_research(
        identity_request,
        build_default_identity_operations(transport),
    )
    identity_material = identity_execution.material
    canonical_identity = identity_material.canonical_identity
    if identity_material.status == "blocked" or canonical_identity is None:
        return _direct_market_result(
            request,
            identity_obligations=identity_execution.plan.obligations,
            outcomes=identity_execution.outcomes,
            limitations=[
                {
                    "code": "security_identity_unresolved",
                    "message": "The requested security identity could not be resolved safely.",
                }
            ],
            evidence_store=evidence_store,
        )
    security = f"{canonical_identity.exchange}:{canonical_identity.code}"
    operation_outcomes = list(identity_execution.outcomes)
    identity = {
        "subjects": [
            {
                "security": {
                    "exchange": canonical_identity.exchange,
                    "code": canonical_identity.code,
                    "type": canonical_identity.security_type,
                },
                "name": canonical_identity.name,
                "issuer": {
                    "name": canonical_identity.issuer_name,
                    "identifier": canonical_identity.issuer_identifier,
                    "security_relationship": canonical_identity.issuer_relationship,
                },
            }
        ],
        "conflicts": [],
        "limitations": [],
        "identity_obligations": identity_execution.plan.obligations,
        "identity_outcomes": identity_execution.outcomes,
    }
    if research_date > research_now.date():
        return _blocked(
            request,
            code="future_research_date",
            message="The research date is later than the retrieval date.",
            identity=identity,
            outcomes=tuple(operation_outcomes),
            evidence_store=evidence_store,
        )

    source_failed = False
    if adjustment == "forward_adjusted":
        adjusted_primary = EastmoneyForwardAdjustedDailyLineOperation()
        adjusted_secondary = TencentForwardAdjustedDailyLineOperation()
        primary_operation_id = adjusted_primary.operation_id
        secondary_operation_id = adjusted_secondary.operation_id
        try:
            if evidence_store is None:
                official_observations = adjusted_primary.observe(
                    security, research_date, transport
                )
                official_outcome = _market_outcome(
                    adjusted_primary.operation_id,
                    observations=official_observations,
                )
            else:
                adjusted_acquired_observations: list[DailyBarObservation] = []

                def acquire_adjusted() -> OperationOutcome:
                    try:
                        adjusted_acquired_observations.extend(
                            _completed_history_for_store(
                                adjusted_primary.observe(
                                    security,
                                    research_date,
                                    transport,
                                ),
                                research_date,
                                research_now,
                            )
                        )
                    except SourceOperationError as error:
                        return _market_outcome(
                            adjusted_primary.operation_id,
                            error=error,
                        )
                    return _market_outcome(
                        adjusted_primary.operation_id,
                        observations=adjusted_acquired_observations,
                    )

                resolution = resolve_operation_evidence(
                    evidence_store,
                    EvidenceReusePolicy(
                        capability_id="market_trend_series",
                        operation_id=adjusted_primary.operation_id,
                        source_version=_MARKET_EVIDENCE_STORE_VERSION,
                        source_role="market_observation",
                        experimental=True,
                        field_dispositions=_market_dispositions(
                            adjusted_primary.operation_id
                        ),
                        subject_identity=security,
                        evidence_scope="completed_history",
                        max_age=timedelta(days=31),
                    ),
                    acquire_adjusted,
                    now=research_now,
                )
                official_observations = adjusted_acquired_observations
                if not adjusted_acquired_observations:
                    official_observations = _daily_bars_from_outcome(resolution.outcome)
                official_outcome = replace(
                    resolution.outcome,
                    evidence_storage=resolution.audit,
                )
                if official_outcome.execution.state == "failed":
                    source_failed = True
        except SourceOperationError as error:
            official_observations = []
            source_failed = True
            operation_outcomes.append(
                _market_outcome(adjusted_primary.operation_id, error=error)
            )
        else:
            operation_outcomes.append(official_outcome)
        try:
            fallback_observations = adjusted_secondary.observe(
                security, research_date, transport
            )
        except SourceOperationError as error:
            fallback_observations = []
            source_failed = True
            operation_outcomes.append(
                _market_outcome(adjusted_secondary.operation_id, error=error)
            )
        else:
            operation_outcomes.append(
                _market_outcome(
                    adjusted_secondary.operation_id, observations=fallback_observations
                )
            )
    else:
        exchange_operation = (
            SseDailyLineOperation()
            if security.startswith("SSE:")
            else SzseDailyLineOperation()
        )
        unadjusted_secondary = TencentDailyLineOperation()
        primary_operation_id = exchange_operation.operation_id
        secondary_operation_id = unadjusted_secondary.operation_id
        try:
            if evidence_store is None:
                official_observations = exchange_operation.observe(security, transport)
                official_outcome = _market_outcome(
                    exchange_operation.operation_id,
                    observations=official_observations,
                )
            else:
                acquired_observations: list[DailyBarObservation] = []

                def acquire_official() -> OperationOutcome:
                    try:
                        acquired_observations.extend(
                            _completed_history_for_store(
                                exchange_operation.observe(security, transport),
                                research_date,
                                research_now,
                            )
                        )
                    except SourceOperationError as error:
                        return _market_outcome(
                            exchange_operation.operation_id,
                            error=error,
                        )
                    return _market_outcome(
                        exchange_operation.operation_id,
                        observations=acquired_observations,
                    )

                resolution = resolve_operation_evidence(
                    evidence_store,
                    EvidenceReusePolicy(
                        capability_id="market_trend_series",
                        operation_id=exchange_operation.operation_id,
                        source_version=_MARKET_EVIDENCE_STORE_VERSION,
                        source_role="authoritative_disclosure",
                        experimental=True,
                        field_dispositions=_market_dispositions(
                            exchange_operation.operation_id
                        ),
                        subject_identity=security,
                        evidence_scope="completed_history",
                        max_age=timedelta(days=31),
                    ),
                    acquire_official,
                    now=research_now,
                )
                official_observations = acquired_observations
                if not acquired_observations:
                    official_observations = _daily_bars_from_outcome(resolution.outcome)
                official_outcome = replace(
                    resolution.outcome,
                    evidence_storage=resolution.audit,
                )
                if official_outcome.execution.state == "failed":
                    source_failed = True
        except SourceOperationError as error:
            official_observations = []
            source_failed = True
            operation_outcomes.append(
                _market_outcome(exchange_operation.operation_id, error=error)
            )
        else:
            operation_outcomes.append(official_outcome)
        try:
            fallback_observations = unadjusted_secondary.observe(
                security, research_date, transport
            )
        except SourceOperationError as error:
            fallback_observations = []
            source_failed = True
            operation_outcomes.append(
                _market_outcome(unadjusted_secondary.operation_id, error=error)
            )
        else:
            operation_outcomes.append(
                _market_outcome(
                    unadjusted_secondary.operation_id,
                    observations=fallback_observations,
                )
            )

    research_boundary = (
        datetime.combine(research_date, time.max, tzinfo=CHINA_STANDARD_TIME)
        if research_date < research_now.date()
        else research_now
    )
    recent_official_rows = _eligible_market_observations(
        official_observations,
        research_date,
        research_boundary,
    )[-trading_days:]
    eligible_fallback_rows = _eligible_market_observations(
        fallback_observations,
        research_date,
        research_boundary,
    )
    selected_official_dates = {item.trading_date for item in recent_official_rows}
    adopted_fallback_rows = (
        [
            item
            for item in eligible_fallback_rows
            if item.trading_date in selected_official_dates
        ]
        if selected_official_dates
        else eligible_fallback_rows[-trading_days:]
    )
    operation_outcomes = _adopt_market_window(
        operation_outcomes,
        {
            primary_operation_id: recent_official_rows,
            secondary_operation_id: adopted_fallback_rows,
        },
    )
    suspended_rows = [
        item for item in recent_official_rows if item.trading_status == "suspended"
    ]
    if suspended_rows:
        rejected = [item.to_bar_evidence() for item in suspended_rows]
        return _blocked(
            request,
            code="suspended_session_in_requested_range",
            message=(
                "An official daily line in the requested range is suspended and "
                "cannot be treated as a traded OHLCV session."
            ),
            identity=identity,
            outcomes=tuple(operation_outcomes),
            claims=[
                DirectTaskClaim(
                    kind="market_trend_diagnostics",
                    evidence_use="market_trend",
                    observation_keys=tuple(str(item["id"]) for item in rejected),
                    payload={"rejected_observations": rejected},
                )
            ],
            evidence_store=evidence_store,
        )
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
            outcomes=tuple(operation_outcomes),
            evidence_store=evidence_store,
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

    selected_keys = tuple(
        key
        for official_bar in selected_official
        for key in (
            official_bar.bar_evidence_id,
            fallback_by_date[official_bar.trading_date].bar_evidence_id
            if official_bar.trading_date in fallback_by_date
            else None,
        )
        if key is not None
    )
    if source_failed or conflicts or len(pairs) != trading_days:
        return _blocked(
            request,
            code="market_series_not_cross_checked",
            message="The requested daily series is not complete and consistent.",
            identity=identity,
            outcomes=tuple(operation_outcomes),
            conflicts=conflicts,
            claims=[
                DirectTaskClaim(
                    kind="market_trend_diagnostics",
                    evidence_use="market_trend",
                    observation_keys=selected_keys,
                    payload={"selected_session_count": len(pairs)},
                )
            ],
            evidence_store=evidence_store,
        )

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
        return _blocked(
            request,
            code="corporate_action_requires_adjusted_series",
            message=(
                "The unadjusted window contains a source-annotated corporate "
                "action, so trend metrics are not comparable across the window."
            ),
            identity=identity,
            outcomes=tuple(operation_outcomes),
            claims=[
                DirectTaskClaim(
                    kind="market_trend_diagnostics",
                    evidence_use="market_trend",
                    observation_keys=selected_keys,
                    payload={
                        "series": series,
                        "corporate_actions": corporate_actions,
                    },
                )
            ],
            evidence_store=evidence_store,
        )

    metrics, calculations = _metrics(bars)
    limitations = cast(list[dict[str, Any]], list(identity["limitations"]))
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
    identity_keys = identity_material.observation_keys
    observation_keys = (*identity_keys, *selected_keys)
    retrieved_at = _canonical_market_retrieved_at(
        _retained_claim_observations(operation_outcomes, observation_keys)
    )
    domain_payload: dict[str, object] = {
        "subjects": identity["subjects"],
        "research": {
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
            "retrieved_at": retrieved_at,
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
    }
    return _direct_market_result(
        request,
        identity_obligations=identity_execution.plan.obligations,
        outcomes=tuple(operation_outcomes),
        limitations=limitations,
        claims=[
            DirectTaskClaim(
                kind="market_trend_summary",
                evidence_use="market_trend",
                observation_keys=observation_keys,
                payload=domain_payload,
            )
        ],
        calculations=[
            DirectTaskCalculation(
                kind=f"market_trend_calculation:{item['id']}",
                evidence_use="market_trend",
                observation_keys=tuple(item.get("evidence_ids", [])),
                payload=item,
            )
            for item in calculations
        ],
        evidence_store=evidence_store,
    )


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


def _eligible_market_observations(
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
        ),
        key=lambda item: item.trading_date,
    )


def _adopt_market_window(
    outcomes: list[OperationOutcome],
    observations_by_operation: Mapping[str, list[DailyBarObservation]],
) -> list[OperationOutcome]:
    adopted = {
        operation_id: tuple(item.to_bar_evidence() for item in observations)
        for operation_id, observations in observations_by_operation.items()
    }
    return [
        replace(outcome, observations=adopted[outcome.operation_id])
        if outcome.operation_id in adopted and outcome.execution.state == "succeeded"
        else outcome
        for outcome in outcomes
    ]


def _retained_claim_observations(
    outcomes: Sequence[OperationOutcome], observation_keys: Sequence[str]
) -> tuple[Mapping[str, object], ...]:
    observations_by_key: dict[str, Mapping[str, object]] = {}
    for outcome in outcomes:
        for index, observation in enumerate(outcome.observations):
            local_id = observation.get("id")
            key = (
                local_id
                if isinstance(local_id, str) and local_id
                else f"{outcome.operation_id}:{index}"
            )
            if key in observations_by_key:
                raise ValueError("market claim observation keys must be unique")
            observations_by_key[key] = observation
    try:
        return tuple(observations_by_key[key] for key in observation_keys)
    except KeyError as error:
        raise ValueError("market claim references unknown retained evidence") from error


def _canonical_market_retrieved_at(
    observations: Sequence[Mapping[str, object]],
) -> str:
    retrieved: list[datetime] = []
    for observation in observations:
        value = observation.get("retrieved_at")
        try:
            parsed = datetime.fromisoformat(cast(str, value))
        except (TypeError, ValueError) as error:
            raise ValueError("market claim retrieval boundary is invalid") from error
        if parsed.utcoffset() is None:
            raise ValueError("market claim retrieval boundary is invalid")
        retrieved.append(parsed)
    if not retrieved:
        raise ValueError("market claim retrieval boundary is missing")
    return max(retrieved).astimezone(CHINA_STANDARD_TIME).isoformat()


def _completed_history_for_store(
    observations: list[DailyBarObservation],
    research_date: date,
    research_now: datetime,
) -> list[DailyBarObservation]:
    boundary = (
        datetime.combine(research_date, time.max, tzinfo=CHINA_STANDARD_TIME)
        if research_date < research_now.date()
        else research_now
    )
    return [
        item
        for item in observations
        if item.trading_date <= research_date
        and item.available_at <= boundary
        and item.price_type == "close"
    ]


def _daily_bars_from_outcome(
    outcome: OperationOutcome,
) -> list[DailyBarObservation]:
    restored: list[DailyBarObservation] = []
    for evidence in outcome.observations:
        subject = evidence.get("subject")
        observed_value = evidence.get("observed_value")
        observation = evidence.get("observation")
        locator = evidence.get("locator")
        if not all(
            isinstance(item, dict)
            for item in (subject, observed_value, observation, locator)
        ):
            raise ValueError("stored daily-bar evidence contract is invalid")
        assert isinstance(subject, dict)
        assert isinstance(observed_value, dict)
        assert isinstance(observation, dict)
        assert isinstance(locator, dict)
        values = observed_value.get("value")
        units = observed_value.get("unit")
        if not isinstance(values, dict) or units != {
            "price": "CNY/share",
            "volume": "shares",
        }:
            raise ValueError("stored daily-bar values are invalid")
        try:
            item = DailyBarObservation(
                source_operation=outcome.operation_id,
                source_uri=str(locator["uri"]),
                security=str(subject["security"]),
                trading_date=date.fromisoformat(str(observation["trading_date"])),
                open_value=str(values["open"]),
                high_value=str(values["high"]),
                low_value=str(values["low"]),
                close_value=str(values["close"]),
                volume_shares=str(values["volume"]),
                price_type="close",
                trading_status=str(observation["trading_status"]),
                evidence_time=datetime.fromisoformat(str(evidence["evidence_time"])),
                available_at=datetime.fromisoformat(str(evidence["available_at"])),
                retrieved_at=datetime.fromisoformat(str(evidence["retrieved_at"])),
                availability_status=str(evidence["availability_status"]),
                corporate_action=cast(
                    dict[str, str] | None, observation.get("corporate_action")
                ),
                adjustment=str(observation["adjustment"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("stored daily-bar evidence contract is invalid") from error
        if item.to_bar_evidence() != evidence:
            raise ValueError("stored daily-bar evidence is not canonical")
        restored.append(item)
    return restored


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


def _market_dispositions(operation_id: str) -> tuple[FieldDisposition, ...]:
    if operation_id == SseDailyLineOperation.operation_id:
        return SseDailyLineOperation.field_dispositions
    if operation_id == SzseDailyLineOperation.operation_id:
        return SzseDailyLineOperation.field_dispositions
    if operation_id == TencentDailyLineOperation.operation_id:
        return TencentDailyLineOperation.field_dispositions
    if operation_id == EastmoneyForwardAdjustedDailyLineOperation.operation_id:
        return EastmoneyForwardAdjustedDailyLineOperation.field_dispositions
    if operation_id == TencentForwardAdjustedDailyLineOperation.operation_id:
        return TencentForwardAdjustedDailyLineOperation.field_dispositions
    raise ValueError(f"unknown market operation contract: {operation_id}")


def _market_outcome(
    operation_id: str,
    *,
    observations: list[DailyBarObservation] | None = None,
    error: SourceOperationError | None = None,
    skipped: str | None = None,
) -> OperationOutcome:
    if sum(item is not None for item in (observations, error, skipped)) != 1:
        raise ValueError("market outcome requires exactly one execution fact")
    if observations is not None:
        execution = OperationExecution("succeeded", query_complete=True)
        payloads = tuple(item.to_bar_evidence() for item in observations)
        errors: tuple[dict[str, str], ...] = ()
    elif error is not None:
        execution = OperationExecution("failed")
        payloads = ()
        errors = (_source_error(error),)
    else:
        execution = OperationExecution(cast(Any, skipped))
        payloads = ()
        errors = ()
    return OperationOutcome(
        capability_id="market_trend_series",
        operation_id=operation_id,
        execution=execution,
        observations=payloads,
        source_errors=errors,
        field_dispositions=_market_dispositions(operation_id),
    )


def _market_policy_block(
    request: dict[str, Any],
    adjustment: str,
    evidence_store: EvidenceStoreRequest | None,
) -> UnifiedResearchResult:
    operation_ids = (
        (
            "eastmoney_forward_adjusted_daily_line@1",
            "tencent_forward_adjusted_daily_line@1",
        )
        if adjustment == "forward_adjusted"
        else (
            "sse_daily_line@1",
            "szse_daily_line@1",
            "tencent_daily_line@1",
        )
    )
    outcomes = tuple(
        OperationOutcome(
            capability_id="market_trend_series",
            operation_id=operation_id,
            execution=OperationExecution("skipped_policy"),
            field_dispositions=_market_dispositions(operation_id),
        )
        for operation_id in operation_ids
    )
    obligations = tuple(
        OperationObligation(
            capability_id="market_trend_series",
            operation_id=operation_id,
            source_role="market_observation",
            experimental=True,
            exchange=None,
            evidence_uses=(EvidenceUsePolicy("market_trend", "supporting"),),
        )
        for operation_id in operation_ids
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="market_trend",
            as_of=str(request["as_of"]),
            obligations=obligations,
            dependencies=(
                ResearchDependency(
                    consumer_id="market_trend_summary",
                    evidence_use="market_trend",
                    minimum_supporting_observations=1,
                    core=True,
                ),
            ),
            evidence_storage=(
                None if evidence_store is None else evidence_store.plan_audit()
            ),
        ),
        outcomes,
        limitations=(
            DirectTaskLimitation(
                code="source_policy_not_satisfied",
                message="market_trend currently requires experimental source operations",
            ),
        ),
        summary="The requested market trend could not be established safely.",
    )


def _blocked(
    request: dict[str, Any],
    *,
    code: str,
    message: str,
    identity: dict[str, Any] | None = None,
    outcomes: tuple[OperationOutcome, ...] = (),
    conflicts: list[dict[str, Any]] | None = None,
    claims: list[DirectTaskClaim] | None = None,
    evidence_store: EvidenceStoreRequest | None = None,
) -> UnifiedResearchResult:
    identity = identity or {}
    limitations = list(identity.get("limitations", []))
    limitations.append({"code": code, "message": message})
    identity_outcomes = cast(
        tuple[OperationOutcome, ...], identity.get("identity_outcomes", ())
    )
    identity_obligations = cast(
        tuple[OperationObligation, ...], identity.get("identity_obligations", ())
    )
    return _direct_market_result(
        request,
        identity_obligations=identity_obligations,
        outcomes=outcomes or identity_outcomes,
        conflicts=[
            *cast(list[dict[str, Any]], identity.get("conflicts", [])),
            *(conflicts or []),
        ],
        limitations=limitations,
        claims=claims,
        evidence_store=evidence_store,
    )


_MARKET_OPERATION_ROLES = {
    "sse_daily_line@1": ("authoritative_disclosure", "supporting"),
    "szse_daily_line@1": ("authoritative_disclosure", "supporting"),
    "tencent_daily_line@1": ("market_observation", "corroborating"),
    "eastmoney_forward_adjusted_daily_line@1": ("market_observation", "supporting"),
    "tencent_forward_adjusted_daily_line@1": ("market_observation", "corroborating"),
}

_MARKET_EVIDENCE_STORE_VERSION = "market-series-contract/1.0"
_MARKET_PERSISTED_OPERATIONS = frozenset(
    {
        SseDailyLineOperation.operation_id,
        SzseDailyLineOperation.operation_id,
        EastmoneyForwardAdjustedDailyLineOperation.operation_id,
    }
)


def _validate_market_evidence_storage_authority(
    result: Mapping[str, object],
) -> None:
    plan = result.get("plan")
    anchor = result.get("research_anchor")
    coverage = result.get("coverage")
    if not isinstance(plan, Mapping) or not isinstance(anchor, Mapping):
        raise ValueError("market evidence storage authority is invalid")
    plan_audit = plan.get("evidence_storage")
    if plan_audit is None:
        return
    if not isinstance(plan_audit, Mapping) or not isinstance(coverage, Mapping):
        raise ValueError("market evidence storage authority is invalid")
    request_scope = plan_audit.get("request_scope")
    if (
        not isinstance(request_scope, Mapping)
        or request_scope.get("task_type") != "market_trend"
        or request_scope.get("as_of") != anchor.get("as_of")
    ):
        raise ValueError("market evidence storage authority is invalid")
    parameters = request_scope.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("market evidence storage authority is invalid")
    adjustment = parameters.get("adjustment")
    trading_days = _trading_days(request_scope.get("window"))
    eligible_operations = (
        {EastmoneyForwardAdjustedDailyLineOperation.operation_id}
        if adjustment == "forward_adjusted"
        else {
            SseDailyLineOperation.operation_id,
            SzseDailyLineOperation.operation_id,
        }
        if adjustment == "unadjusted"
        else set()
    )
    if not eligible_operations:
        raise ValueError("market evidence storage authority is invalid")
    audited: list[tuple[str, Mapping[str, object]]] = []
    for capability_id, aggregate in coverage.items():
        if not isinstance(aggregate, Mapping) or not isinstance(
            aggregate.get("operations"), Mapping
        ):
            continue
        operations = cast(Mapping[str, object], aggregate["operations"])
        for operation_id, operation in operations.items():
            if not isinstance(operation, Mapping):
                continue
            audit = operation.get("evidence_storage")
            if audit is None:
                if (
                    capability_id == "market_trend_series"
                    and operation_id in eligible_operations
                    and operation.get("execution_state") in {"succeeded", "failed"}
                ):
                    raise ValueError("market evidence storage authority is invalid")
                continue
            if (
                capability_id != "market_trend_series"
                or operation_id not in _MARKET_PERSISTED_OPERATIONS
                or operation.get("execution_state") not in {"succeeded", "failed"}
                or not isinstance(audit, Mapping)
                or audit.get("source_version") != _MARKET_EVIDENCE_STORE_VERSION
                or "reuse_facts" in audit
            ):
                raise ValueError("market evidence storage authority is invalid")
            audited.append((operation_id, audit))
    if not audited:
        return
    if any(operation_id not in eligible_operations for operation_id, _ in audited):
        raise ValueError("market evidence storage authority is invalid")
    claims = result.get("claims")
    if not isinstance(claims, (list, tuple)):
        raise ValueError("market evidence storage authority is invalid")
    claim = next(
        (
            item
            for item in claims
            if isinstance(item, Mapping) and item.get("kind") == "market_trend_summary"
        ),
        None,
    )
    if claim is None:
        return
    payload = claim.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("market evidence storage authority is invalid")
    research = payload.get("research")
    window = payload.get("window")
    if (
        not isinstance(research, Mapping)
        or not isinstance(window, Mapping)
        or research.get("as_of") != request_scope.get("as_of")
        or research.get("adjustment") != adjustment
        or window.get("requested_trading_days") != trading_days
    ):
        raise ValueError("market evidence storage authority is invalid")


def _direct_market_result(
    request: dict[str, Any],
    *,
    identity_obligations: tuple[OperationObligation, ...] = (),
    outcomes: tuple[OperationOutcome, ...] = (),
    conflicts: list[dict[str, Any]] | None = None,
    limitations: list[dict[str, Any]] | None = None,
    claims: list[DirectTaskClaim] | None = None,
    calculations: list[DirectTaskCalculation] | None = None,
    evidence_store: EvidenceStoreRequest | None = None,
) -> UnifiedResearchResult:
    adjustment = request.get("parameters", {}).get("adjustment")
    expected = (
        (
            "eastmoney_forward_adjusted_daily_line@1",
            "tencent_forward_adjusted_daily_line@1",
        )
        if adjustment == "forward_adjusted"
        else (
            "sse_daily_line@1",
            "szse_daily_line@1",
            "tencent_daily_line@1",
        )
    )
    by_owner = {(item.capability_id, item.operation_id): item for item in outcomes}
    if len(by_owner) != len(outcomes):
        raise ValueError("market outcomes must be uniquely owned")
    missing_state = (
        "skipped_policy"
        if not request["source_policy"]["allow_experimental"]
        else "skipped_research_dependency"
    )
    for operation_id in expected:
        owner = ("market_trend_series", operation_id)
        if owner not in by_owner:
            by_owner[owner] = _market_outcome(operation_id, skipped=missing_state)
    ordered_outcomes = tuple(by_owner.values())
    obligations = [
        OperationObligation(
            capability_id=item.capability_id,
            operation_id=item.operation_id,
            source_role=item.source_role,
            experimental=item.experimental,
            exchange=item.exchange,
            evidence_uses=(
                *item.evidence_uses,
                EvidenceUsePolicy("market_trend", "lineage"),
            ),
        )
        for item in identity_obligations
    ]
    for outcome in ordered_outcomes:
        if outcome.capability_id != "market_trend_series":
            continue
        source_role, evidence_role = _MARKET_OPERATION_ROLES[outcome.operation_id]
        obligations.append(
            OperationObligation(
                capability_id=outcome.capability_id,
                operation_id=outcome.operation_id,
                source_role=source_role,
                experimental=True,
                exchange=None,
                evidence_uses=(
                    EvidenceUsePolicy("market_trend", cast(Any, evidence_role)),
                ),
            )
        )
    known_keys = {
        str(observation["id"])
        for outcome in ordered_outcomes
        for observation in outcome.observations
        if isinstance(observation.get("id"), str)
    }
    direct_conflicts = tuple(
        DirectTaskConflict(
            code=str(item.get("code", "market_series_conflict")),
            message=str(item.get("message", "Market-series observations conflict.")),
            observation_keys=tuple(
                key
                for key in item.get("evidence_ids", [])
                if isinstance(key, str) and key in known_keys
            ),
        )
        for item in conflicts or []
    )
    direct_limitations = tuple(
        DirectTaskLimitation(
            code=str(item.get("code", "market_trend_limited")),
            message=str(item.get("message", "Market-trend research is limited.")),
        )
        for item in limitations or []
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="market_trend",
            as_of=str(request["as_of"]),
            obligations=tuple(obligations),
            dependencies=(
                ResearchDependency(
                    consumer_id="market_trend_summary",
                    evidence_use="market_trend",
                    minimum_supporting_observations=1,
                    core=True,
                ),
            ),
            evidence_storage=(
                None if evidence_store is None else evidence_store.plan_audit()
            ),
        ),
        ordered_outcomes,
        claims=tuple(claims or ()),
        calculations=tuple(calculations or ()),
        conflicts=direct_conflicts,
        limitations=direct_limitations,
        summary=(
            "A complete cross-checked market trend was retained."
            if claims
            else "The requested market trend could not be established safely."
        ),
    )
