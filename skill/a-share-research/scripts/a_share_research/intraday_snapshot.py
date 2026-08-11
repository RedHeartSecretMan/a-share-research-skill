"""Adjudicate one research-grade continuous-auction intraday snapshot."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Collection, cast

from .intraday_contract import (
    SESSION_STATES,
    IntradayObservation,
    IntradayQuery,
    IntradaySourceError,
    IntradaySourceOperation,
    session_at,
)
from .intraday_sources import (
    TENCENT_INTRADAY_FIELD_DISPOSITIONS,
    TONGDAXIN_INTRADAY_FIELD_DISPOSITIONS,
)
from .research_result import (
    DirectTaskCalculation,
    DirectTaskClaim,
    DirectTaskConflict,
    DirectTaskLimitation,
    DirectTaskPlan,
    EvidenceUsePolicy,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    UnifiedResearchResult,
    fold_direct_task_research_result,
    safe_public_locator,
    safe_public_provider_id,
)

SSE_A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZSE_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301")
TONGDAXIN_OPERATION = "tongdaxin_intraday_snapshot@1"
TENCENT_OPERATION = "tencent_intraday_snapshot@1"
_SAFE_CACHE_STATES = frozenset({"source_timestamp", "uncached", "fresh"})
_SAFE_PREVIOUS_CLOSE_BASES = frozenset(
    {"actual_close", "ex_right_reference", "source_reported_unadjudicated"}
)
_SNAPSHOT_CAPABILITY = "intraday_market_snapshot"
_SNAPSHOT_EVIDENCE_USE = "intraday_snapshot_core"
_SNAPSHOT_OPERATIONS = (TONGDAXIN_OPERATION, TENCENT_OPERATION)
_SNAPSHOT_FIELD_DISPOSITIONS = {
    TONGDAXIN_OPERATION: TONGDAXIN_INTRADAY_FIELD_DISPOSITIONS,
    TENCENT_OPERATION: TENCENT_INTRADAY_FIELD_DISPOSITIONS,
}


class _IntradayDomainBlock(Exception):
    """A valid intraday request that cannot form an applicable domain result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _safe_cache_state(value: object) -> str:
    """Keep provider cache metadata to a fixed non-sensitive vocabulary."""

    return (
        value if isinstance(value, str) and value in _SAFE_CACHE_STATES else "unknown"
    )


def _safe_previous_close_basis(value: object) -> str:
    """Keep provider previous-close semantics to the registered vocabulary."""

    return (
        value
        if isinstance(value, str) and value in _SAFE_PREVIOUS_CLOSE_BASES
        else "unknown"
    )


def _safe_corporate_action(value: object) -> dict[str, str] | None:
    """Expose only whether a corporate-action annotation was present."""

    if value:
        return {"present": "true"}
    return None


def build_intraday_snapshot_result(
    request: dict[str, Any],
    operations: Collection[IntradaySourceOperation],
    research_now: datetime,
) -> UnifiedResearchResult:
    """Collect and cross-check one current-date intraday snapshot."""

    try:
        query = _normalize_query(request, research_now)
    except _IntradayDomainBlock as blocked:
        return _direct_snapshot_result(
            request,
            outcomes=_snapshot_skipped_outcomes("skipped_research_dependency"),
            limitations=(DirectTaskLimitation(blocked.code, str(blocked)),),
            summary=str(blocked),
        )
    operation_list = list(operations)
    if len(operation_list) != 2:
        raise ValueError(
            "intraday_market_signal requires exactly two source operations"
        )
    observations: list[IntradayObservation] = []
    outcomes: list[OperationOutcome] = []
    for operation in operation_list:
        try:
            observation = operation.collect(query)
            observation = _validate_observation(observation, operation.operation_id)
            observations.append(observation)
            outcomes.append(_snapshot_outcome(observation=observation))
        except IntradaySourceError as error:
            outcomes.append(_snapshot_outcome(error=error))
        except Exception:
            # Adapter implementations are outside the ResearchTask boundary.  A
            # provider exception must never escape as a traceback or reveal its
            # request, credentials, or response body through the JSON contract.
            outcomes.append(
                _snapshot_outcome(
                    error=IntradaySourceError(
                        operation.operation_id,
                        "operation_failure",
                        "The source operation failed without a safe diagnostic.",
                    )
                )
            )
    if any(item.source_errors for item in outcomes) or len(observations) != 2:
        return _direct_snapshot_result(
            request,
            outcomes=tuple(outcomes),
            limitations=(
                DirectTaskLimitation(
                    "intraday_sources_incomplete",
                    "Both independent intraday source operations are required.",
                ),
            ),
            summary="The required intraday source observations were incomplete.",
        )
    baseline, cross_check = observations
    incompatibilities = _pair_incompatibility(query, baseline, cross_check)
    if incompatibilities:
        return _direct_snapshot_result(
            request,
            outcomes=tuple(outcomes),
            conflicts=_direct_snapshot_conflicts(incompatibilities, observations),
            summary="The intraday observations were incompatible.",
        )
    result_session = _result_session(query, baseline, cross_check)
    if result_session is None:
        conflicts = [
            _pair_conflict(
                "intraday_session_unknown",
                "The current time and source timestamps do not establish an applicable session.",
                observations=(baseline, cross_check),
            )
        ]
        return _direct_snapshot_result(
            request,
            outcomes=tuple(outcomes),
            conflicts=_direct_snapshot_conflicts(conflicts, observations),
            summary="The current intraday session could not be established.",
        )
    freshness_conflicts = _freshness_conflicts(baseline, cross_check, result_session)
    if freshness_conflicts:
        return _direct_snapshot_result(
            request,
            outcomes=tuple(outcomes),
            conflicts=_direct_snapshot_conflicts(freshness_conflicts, observations),
            summary="The intraday observations did not satisfy freshness bounds.",
        )
    suspension_conflicts = _suspension_conflicts(baseline, cross_check)
    if suspension_conflicts:
        return _direct_snapshot_result(
            request,
            outcomes=tuple(outcomes),
            conflicts=_direct_snapshot_conflicts(suspension_conflicts, observations),
            summary="The intraday suspension state could not be established.",
        )
    assert baseline.previous_close is not None
    assert baseline.previous_close_basis is not None
    gap_seconds = abs((baseline.observed_at - cross_check.observed_at).total_seconds())
    observation_times: dict[str, str] = {
        "tongdaxin_baseline": baseline.observed_at.isoformat(),
        "tencent_cross_check": cross_check.observed_at.isoformat(),
        "retrieved_at": max(
            baseline.retrieved_at, cross_check.retrieved_at
        ).isoformat(),
        "pair_gap_seconds": _seconds_text(gap_seconds),
    }
    if result_session == "midday_break":
        observation_times["observation_boundary"] = "morning_last_compatible_pair"
    snapshot_facts: dict[str, Any] = {
        "subject": {
            "security": {
                "exchange": query.exchange,
                "code": query.code,
                "type": "A_SHARE",
            }
        },
        "as_of": query.as_of.isoformat(),
        "trading_date": baseline.trading_date.isoformat(),
        "session_state": result_session,
        "trading_status": baseline.trading_status,
        "price_type": baseline.price_type,
        "snapshot": {
            "latest_price": {"value": baseline.latest_price, "unit": "CNY/share"},
            "open": {"value": baseline.open_price, "unit": "CNY/share"},
            "high": {"value": baseline.high_price, "unit": "CNY/share"},
            "low": {"value": baseline.low_price, "unit": "CNY/share"},
            "previous_close": {
                "status": "unavailable",
                "reported_value": baseline.previous_close,
                "unit": "CNY/share",
                "basis": baseline.previous_close_basis,
                "reason": "independent_semantics_not_adjudicated",
            },
            "cumulative_volume": {
                "value": baseline.cumulative_volume_shares,
                "unit": "shares",
            },
            "cumulative_amount": {
                "value": baseline.cumulative_amount_cny,
                "unit": "CNY",
            },
        },
        "observation_times": observation_times,
    }
    previous_close = _adjudicate_previous_close(baseline, cross_check)
    snapshot_facts["snapshot"]["previous_close"] = previous_close["snapshot"]
    if _is_confirmed_suspension(baseline, cross_check):
        snapshot = cast(dict[str, Any], snapshot_facts["snapshot"])
        not_applicable = {
            "status": "not_applicable",
            "value": None,
            "unit": "CNY/share",
            "reason": "suspended",
        }
        for field in ("latest_price", "open", "high", "low"):
            snapshot[field] = dict(not_applicable)
        snapshot["cumulative_volume"] = {"value": "0", "unit": "shares"}
        snapshot["cumulative_amount"] = {
            "status": "not_applicable",
            "value": None,
            "unit": "CNY",
            "reason": "suspended_amount_not_observed",
        }
        snapshot_facts["trading_status"] = "suspended"
        snapshot_facts["price_type"] = "not_applicable"
    if previous_close["comparable"]:
        change_amount = Decimal(baseline.latest_price) - Decimal(
            baseline.previous_close
        )
        change_percent = (
            (change_amount / Decimal(baseline.previous_close)) * Decimal(100)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        snapshot_facts["snapshot"]["change_amount"] = {
            "value": _decimal_text(change_amount),
            "unit": "CNY/share",
        }
        snapshot_facts["snapshot"]["change_percent"] = {
            "value": _decimal_text(change_percent),
            "unit": "percent",
        }
    observation_keys = tuple(_snapshot_observation_key(item) for item in observations)
    calculations = [
        DirectTaskCalculation(
            kind="intraday_source_pair_gap",
            evidence_use=_SNAPSHOT_EVIDENCE_USE,
            observation_keys=observation_keys,
            payload={
                "pair_gap_seconds": snapshot_facts["observation_times"][
                    "pair_gap_seconds"
                ],
                "formula": "abs(baseline_observed_at - cross_check_observed_at)",
                "operands": {
                    "baseline_observed_at": baseline.observed_at.isoformat(),
                    "cross_check_observed_at": cross_check.observed_at.isoformat(),
                },
            },
        )
    ]
    if previous_close["comparable"]:
        calculations.extend(
            [
                DirectTaskCalculation(
                    kind="intraday_change_amount",
                    evidence_use=_SNAPSHOT_EVIDENCE_USE,
                    observation_keys=observation_keys,
                    payload={
                        **cast(
                            dict[str, object],
                            snapshot_facts["snapshot"]["change_amount"],
                        ),
                        "formula": "latest_price - previous_close",
                        "operands": {
                            "latest_price": baseline.latest_price,
                            "previous_close": baseline.previous_close,
                        },
                    },
                ),
                DirectTaskCalculation(
                    kind="intraday_change_percent",
                    evidence_use=_SNAPSHOT_EVIDENCE_USE,
                    observation_keys=observation_keys,
                    payload={
                        **cast(
                            dict[str, object],
                            snapshot_facts["snapshot"]["change_percent"],
                        ),
                        "formula": "(latest_price - previous_close) / previous_close * 100",
                        "operands": {
                            "latest_price": baseline.latest_price,
                            "previous_close": baseline.previous_close,
                        },
                    },
                ),
            ]
        )
    limitations = [
        DirectTaskLimitation(
            "experimental_intraday_sources",
            "The agreeing intraday operations remain experimental.",
            observation_keys,
        )
    ]
    if result_session == "midday_break":
        limitations.append(
            DirectTaskLimitation(
                "midday_break_morning_observation",
                "The snapshot retains the last compatible morning observation.",
                observation_keys,
            )
        )
    return _direct_snapshot_result(
        request,
        outcomes=tuple(outcomes),
        claims=(
            DirectTaskClaim(
                kind="intraday_market_snapshot",
                evidence_use=_SNAPSHOT_EVIDENCE_USE,
                observation_keys=observation_keys,
                payload=snapshot_facts,
            ),
        ),
        calculations=tuple(calculations),
        limitations=tuple(limitations),
        summary=(
            "Two experimental operations directly establish one "
            f"{result_session} intraday market snapshot."
        ),
    )


def _snapshot_observation_key(observation: IntradayObservation) -> str:
    return f"{observation.source_operation}:{observation.security}:{observation.observed_at.isoformat()}"


def _snapshot_observation_payload(
    observation: IntradayObservation,
) -> dict[str, object]:
    """Retain one operation's contract-valid raw proof and normalized facts."""

    return {
        "id": _snapshot_observation_key(observation),
        "source_operation": observation.source_operation,
        "kind": "intraday_snapshot_source_observation",
        "subject": {"security": observation.security},
        "trading_date": observation.trading_date.isoformat(),
        "observed_at": observation.observed_at.isoformat(),
        "retrieved_at": observation.retrieved_at.isoformat(),
        "session_state": observation.session_state,
        "trading_status": observation.trading_status,
        "price_type": observation.price_type,
        "prices": {
            "latest": observation.latest_price,
            "open": observation.open_price,
            "high": observation.high_price,
            "low": observation.low_price,
        },
        "previous_close": {
            "value": observation.previous_close,
            "basis": observation.previous_close_basis,
            "comparability": observation.previous_close_comparability,
        },
        "cumulative": {
            "volume": observation.cumulative_volume_shares,
            "volume_unit": "shares",
            "amount": observation.cumulative_amount_cny,
            "amount_unit": "CNY",
            "scope": "trading_day",
        },
        "cache_state": observation.cache_state,
        "observation_boundary": observation.observation_boundary,
        "no_trade_confirmed": observation.no_trade_confirmed,
        "corporate_action": observation.corporate_action,
        "field_lineage": {
            field: list(source_fields)
            for field, source_fields in observation.field_sources.items()
        },
        "provenance": [
            _snapshot_provenance(item, observation, index)
            for index, item in enumerate(observation.evidence)
        ],
    }


def _snapshot_provenance(
    item: dict[str, Any], observation: IntradayObservation, index: int
) -> dict[str, object]:
    locator = item.get("locator")
    uri = locator.get("uri") if isinstance(locator, dict) else None
    return {
        "provider_observation_id": safe_public_provider_id(
            item.get("id"),
            f"{observation.source_operation}:provider:{index}",
        ),
        "evidence_time": item.get("evidence_time"),
        "available_at": item.get("available_at"),
        "retrieved_at": item.get("retrieved_at"),
        "locator": {
            "uri": safe_public_locator(
                uri,
                f"source-observation:{index}",
            )
        },
    }


def _snapshot_outcome(
    *,
    observation: IntradayObservation | None = None,
    error: IntradaySourceError | None = None,
    operation_id: str | None = None,
    skipped: str | None = None,
) -> OperationOutcome:
    facts = sum((observation is not None, error is not None, skipped is not None))
    if facts != 1:
        raise ValueError("intraday snapshot outcome requires one execution fact")
    owner = (
        observation.source_operation
        if observation is not None
        else error.source_operation
        if error is not None
        else operation_id
    )
    if owner not in _SNAPSHOT_OPERATIONS:
        raise ValueError("intraday snapshot outcome operation is not planned")
    if observation is not None:
        execution = OperationExecution("succeeded", query_complete=True)
        observations: tuple[dict[str, object], ...] = (
            _snapshot_observation_payload(observation),
        )
        source_errors: tuple[dict[str, object], ...] = ()
    elif error is not None:
        execution = OperationExecution("failed")
        observations = ()
        source_errors = (cast(dict[str, object], _source_error_result(error)),)
    else:
        execution = OperationExecution(cast(Any, skipped))
        observations = ()
        source_errors = ()
    return OperationOutcome(
        capability_id=_SNAPSHOT_CAPABILITY,
        operation_id=owner,
        execution=execution,
        observations=observations,
        source_errors=source_errors,
        field_dispositions=_SNAPSHOT_FIELD_DISPOSITIONS[owner],
    )


def _snapshot_skipped_outcomes(state: str) -> tuple[OperationOutcome, ...]:
    return tuple(
        _snapshot_outcome(operation_id=operation_id, skipped=state)
        for operation_id in _SNAPSHOT_OPERATIONS
    )


def _direct_snapshot_conflicts(
    conflicts: Collection[dict[str, Any]],
    observations: Collection[IntradayObservation],
) -> tuple[DirectTaskConflict, ...]:
    observation_keys = tuple(_snapshot_observation_key(item) for item in observations)
    return tuple(
        DirectTaskConflict(
            code=str(item.get("code", "intraday_snapshot_conflict")),
            message=str(item.get("message", "The intraday observations conflict.")),
            observation_keys=observation_keys,
        )
        for item in conflicts
    )


def _direct_snapshot_result(
    request: dict[str, Any],
    *,
    outcomes: tuple[OperationOutcome, ...],
    claims: tuple[DirectTaskClaim, ...] = (),
    calculations: tuple[DirectTaskCalculation, ...] = (),
    conflicts: tuple[DirectTaskConflict, ...] = (),
    limitations: tuple[DirectTaskLimitation, ...] = (),
    summary: str,
) -> UnifiedResearchResult:
    by_operation = {item.operation_id: item for item in outcomes}
    if len(by_operation) != len(outcomes):
        raise ValueError("intraday snapshot outcomes must have unique owners")
    missing_state = (
        "skipped_policy"
        if not request["source_policy"]["allow_experimental"]
        else "skipped_research_dependency"
    )
    for operation_id in _SNAPSHOT_OPERATIONS:
        if operation_id not in by_operation:
            by_operation[operation_id] = _snapshot_outcome(
                operation_id=operation_id,
                skipped=missing_state,
            )
    ordered_outcomes = tuple(by_operation[item] for item in _SNAPSHOT_OPERATIONS)
    obligations = tuple(
        OperationObligation(
            capability_id=_SNAPSHOT_CAPABILITY,
            operation_id=operation_id,
            source_role="market_observation",
            experimental=True,
            exchange=None,
            evidence_uses=(EvidenceUsePolicy(_SNAPSHOT_EVIDENCE_USE, "supporting"),),
        )
        for operation_id in _SNAPSHOT_OPERATIONS
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="intraday_market_signal",
            as_of=str(request["as_of"]),
            obligations=obligations,
            dependencies=(
                ResearchDependency(
                    consumer_id="intraday_market_snapshot",
                    evidence_use=_SNAPSHOT_EVIDENCE_USE,
                    minimum_supporting_observations=2,
                    core=True,
                ),
            ),
        ),
        ordered_outcomes,
        claims=claims,
        calculations=calculations,
        conflicts=conflicts,
        limitations=limitations,
        summary=summary,
    )


def _normalize_query(request: dict[str, Any], research_now: datetime) -> IntradayQuery:
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("intraday_market_signal requires exactly one subject")
    subject = subjects[0]
    security = subject.get("security") if isinstance(subject, dict) else None
    if not isinstance(security, str):
        raise ValueError("intraday_market_signal requires one canonical security")
    exchange, separator, code = security.partition(":")
    if (
        separator != ":"
        or exchange not in {"SSE", "SZSE"}
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        raise ValueError(
            "intraday_market_signal requires one canonical SSE/SZSE A-share"
        )
    prefixes = SSE_A_SHARE_PREFIXES if exchange == "SSE" else SZSE_A_SHARE_PREFIXES
    if not code.startswith(prefixes):
        raise ValueError(
            "intraday_market_signal subject is not a supported SSE/SZSE A-share"
        )
    if request.get("window") is not None:
        raise ValueError("intraday_market_signal window must be null")
    as_of = date.fromisoformat(request["as_of"])
    if as_of != research_now.date():
        raise _IntradayDomainBlock(
            "intraday_as_of_not_current",
            "Intraday snapshots are only applicable to the current China trading date.",
        )
    if as_of.weekday() >= 5:
        raise _IntradayDomainBlock(
            "intraday_non_trading_date",
            "The requested China date is not a scheduled trading date.",
        )
    if session_at(research_now) is None:
        raise _IntradayDomainBlock(
            "intraday_session_not_applicable",
            "The current China time is before open, after close, or otherwise outside an applicable session.",
        )
    return IntradayQuery(
        security=security,
        exchange=exchange,
        code=code,
        as_of=as_of,
        retrieved_at=research_now,
    )


def _pair_incompatibility(
    query: IntradayQuery,
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> list[dict[str, Any]]:
    """Return every explicit pair conflict without selecting a convenient source."""

    conflicts: list[dict[str, Any]] = []
    observations = (baseline, cross_check)
    if baseline.source_operation != TONGDAXIN_OPERATION or (
        cross_check.source_operation != TENCENT_OPERATION
    ):
        conflicts.append(
            _pair_conflict(
                "intraday_source_role_mismatch",
                "The required TongdaXin baseline and Tencent cross-check operations were not both supplied.",
                field="source_operation",
                baseline=baseline.source_operation,
                cross_check=cross_check.source_operation,
                observations=observations,
            )
        )
    if any(item.security != query.security for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_security_mismatch",
                "Intraday source operations returned a different security.",
                field="security",
                baseline=baseline.security,
                cross_check=cross_check.security,
                observations=observations,
            )
        )
    if any(item.trading_date != query.as_of for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_trading_date_mismatch",
                "Intraday source operations returned a different trading date.",
                field="trading_date",
                baseline=baseline.trading_date.isoformat(),
                cross_check=cross_check.trading_date.isoformat(),
                observations=observations,
            )
        )
    expected_session = session_at(query.retrieved_at)
    if expected_session is None:
        conflicts.append(
            _pair_conflict(
                "intraday_session_unknown",
                "The current retrieval time is outside an applicable trading session.",
                field="session_state",
                baseline=baseline.session_state,
                cross_check=cross_check.session_state,
                observations=observations,
            )
        )
    elif any(item.session_state not in SESSION_STATES for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_session_unknown",
                "Intraday source observations do not establish a known trading session.",
                field="session_state",
                baseline=baseline.session_state,
                cross_check=cross_check.session_state,
                observations=observations,
            )
        )
    elif any(
        session_at(item.observed_at) != item.session_state for item in observations
    ):
        conflicts.append(
            _pair_conflict(
                "intraday_session_mismatch",
                "A source session state is incompatible with its observation timestamp.",
                field="session_state",
                baseline=baseline.session_state,
                cross_check=cross_check.session_state,
                observations=observations,
            )
        )
    elif expected_session == "midday_break":
        if any(item.session_state != "continuous" for item in observations):
            conflicts.append(
                _pair_conflict(
                    "intraday_session_mismatch",
                    "The midday result must retain compatible morning continuous observations.",
                    field="session_state",
                    baseline=baseline.session_state,
                    cross_check=cross_check.session_state,
                    observations=observations,
                )
            )
        if any(
            item.observation_boundary != "morning_last_compatible"
            for item in observations
        ):
            conflicts.append(
                _pair_conflict(
                    "intraday_morning_observation_not_last",
                    "The midday result requires each source to identify its morning-last observation boundary.",
                    field="observation_boundary",
                    baseline=baseline.observation_boundary,
                    cross_check=cross_check.observation_boundary,
                    observations=observations,
                )
            )
        if any(not _is_morning_continuous(item.observed_at) for item in observations):
            conflicts.append(
                _pair_conflict(
                    "intraday_morning_observation_out_of_window",
                    "The midday result requires observations from the morning continuous session.",
                    field="observed_at",
                    observations=observations,
                )
            )
    elif any(item.session_state != expected_session for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_session_mismatch",
                "Intraday source observations are not in the current trading session.",
                field="session_state",
                baseline=baseline.session_state,
                cross_check=cross_check.session_state,
                observations=observations,
            )
        )
    expected_trading_status = (
        "auction"
        if expected_session in {"opening_auction", "closing_auction"}
        else "traded"
    )
    suspended_statuses = {"suspended"}
    no_trade_aliases = {"not_traded", "no_trade"}
    all_suspended = all(
        item.trading_status in suspended_statuses for item in observations
    )
    any_suspended = any(
        item.trading_status in suspended_statuses | no_trade_aliases
        for item in observations
    )
    if all_suspended:
        status_compatible = True
    elif any_suspended:
        status_compatible = False
    elif expected_trading_status == "auction":
        status_compatible = all(
            item.trading_status in {"auction", "unknown"} for item in observations
        )
    else:
        status_compatible = all(
            item.trading_status == expected_trading_status for item in observations
        )
    if not status_compatible:
        conflicts.append(
            _pair_conflict(
                (
                    "intraday_suspension_confirmation_mismatch"
                    if any_suspended
                    else "intraday_trading_status_mismatch"
                ),
                (
                    "Intraday source operations do not jointly confirm suspension."
                    if any_suspended
                    else "Intraday source observations do not establish the current session status."
                ),
                field="trading_status",
                baseline=baseline.trading_status,
                cross_check=cross_check.trading_status,
                observations=observations,
            )
        )
    expected_price_type = (
        "indicative_auction"
        if expected_session in {"opening_auction", "closing_auction"}
        else "latest_traded"
    )
    if any(item.price_type != expected_price_type for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_price_type_mismatch",
                "Intraday source observations do not establish the current session price type.",
                field="price_type",
                baseline=baseline.price_type,
                cross_check=cross_check.price_type,
                observations=observations,
            )
        )
    for field in ("latest_price", "open_price", "high_price", "low_price"):
        baseline_value = getattr(baseline, field)
        cross_check_value = getattr(cross_check, field)
        if baseline_value != cross_check_value:
            conflicts.append(
                _pair_conflict(
                    "intraday_core_price_mismatch",
                    f"Intraday source observations disagree on {field}.",
                    field=field,
                    baseline=baseline_value,
                    cross_check=cross_check_value,
                    observations=observations,
                )
            )
    incomplete_fields = [
        field
        for field, value in (
            ("previous_close", baseline.previous_close),
            ("previous_close_basis", baseline.previous_close_basis),
            ("cumulative_volume", baseline.cumulative_volume_shares),
            ("cumulative_amount", baseline.cumulative_amount_cny),
        )
        if value is None
    ]
    if incomplete_fields:
        conflicts.append(
            _pair_conflict(
                "intraday_baseline_incomplete",
                "TongdaXin did not establish all required snapshot fields.",
                field="baseline",
                missing_fields=incomplete_fields,
                observations=observations,
            )
        )
    return conflicts


def _freshness_conflicts(
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
    result_session: str,
) -> list[dict[str, Any]]:
    """Apply the 60-second active-session observation and pair bounds."""

    conflicts: list[dict[str, Any]] = []
    observations = (baseline, cross_check)
    for observation in observations:
        if observation.cache_state not in {"source_timestamp", "uncached", "fresh"}:
            conflicts.append(
                {
                    "code": "intraday_cache_state_unknown",
                    "message": "The source cache state is missing or unknown.",
                    "source_operation": observation.source_operation,
                    "cache_state": _safe_cache_state(observation.cache_state),
                }
            )
    if result_session in {"opening_auction", "continuous", "closing_auction"}:
        for observation in observations:
            age = (observation.retrieved_at - observation.observed_at).total_seconds()
            if age < 0:
                conflicts.append(
                    {
                        "code": "intraday_observation_time_invalid",
                        "message": "A source observation occurs after its retrieval time.",
                        "source_operation": observation.source_operation,
                    }
                )
            elif age > 60:
                conflicts.append(
                    {
                        "code": "intraday_observation_too_old",
                        "message": "An active-session source observation is older than 60 seconds.",
                        "source_operation": observation.source_operation,
                        "age_seconds": _seconds_text(age),
                    }
                )
    gap = abs((baseline.observed_at - cross_check.observed_at).total_seconds())
    if (
        result_session
        in {
            "opening_auction",
            "continuous",
            "midday_break",
            "closing_auction",
        }
        and gap > 60
    ):
        conflicts.append(
            {
                "code": "intraday_source_pair_gap_exceeded",
                "message": "The active-session source observations are more than 60 seconds apart.",
                "gap_seconds": _seconds_text(gap),
            }
        )
    return conflicts


def _result_session(
    query: IntradayQuery,
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> str | None:
    """Resolve the result session from retrieval and source observations."""

    current_session = session_at(query.retrieved_at)
    if current_session is None:
        return None
    if current_session != "midday_break":
        if any(
            item.session_state != current_session for item in (baseline, cross_check)
        ):
            return None
        return current_session
    if any(item.session_state != "continuous" for item in (baseline, cross_check)):
        return None
    if any(
        session_at(item.observed_at) != "continuous" for item in (baseline, cross_check)
    ):
        return None
    return "midday_break"


def _is_morning_continuous(value: datetime) -> bool:
    observed_time = value.timetz().replace(tzinfo=None)
    return time(9, 30) <= observed_time <= time(11, 30)


def _seconds_text(value: float) -> str:
    decimal_value = Decimal(str(value))
    return format(decimal_value.normalize(), "f")


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _suspension_conflicts(
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> list[dict[str, Any]]:
    """Require both independent operations to prove a no-trade suspension."""

    observations = (baseline, cross_check)
    suspended_statuses = {"suspended"}
    no_trade_aliases = {"not_traded", "no_trade"}
    statuses = [item.trading_status for item in observations]
    if not any(status in suspended_statuses | no_trade_aliases for status in statuses):
        if (
            baseline.previous_close is not None
            and cross_check.previous_close is not None
            and baseline.latest_price == baseline.previous_close
            and cross_check.latest_price == cross_check.previous_close
            and all(item.trading_status == "traded" for item in observations)
            and not all(_has_positive_trade_evidence(item) for item in observations)
        ):
            return [
                _pair_conflict(
                    "intraday_suspension_ambiguous",
                    "Equal price and previous-close evidence cannot establish suspension.",
                    field="trading_status",
                    baseline=baseline.trading_status,
                    cross_check=cross_check.trading_status,
                    observations=observations,
                )
            ]
        return []
    if not all(status in suspended_statuses for status in statuses):
        return [
            _pair_conflict(
                "intraday_suspension_confirmation_mismatch",
                "Both source operations must explicitly report suspended status.",
                field="trading_status",
                baseline=baseline.trading_status,
                cross_check=cross_check.trading_status,
                observations=observations,
            )
        ]
    if not all(item.no_trade_confirmed for item in observations):
        return [
            _pair_conflict(
                "intraday_suspension_no_trade_unconfirmed",
                "Suspension is explicit but both sources do not establish no trading.",
                field="no_trade_confirmed",
                baseline=baseline.no_trade_confirmed,
                cross_check=cross_check.no_trade_confirmed,
                observations=observations,
            )
        ]
    return []


def _is_confirmed_suspension(
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> bool:
    statuses = {"suspended"}
    return all(
        item.trading_status in statuses and item.no_trade_confirmed
        for item in (baseline, cross_check)
    )


def _has_positive_trade_evidence(observation: IntradayObservation) -> bool:
    """Return whether a traded observation proves at least one trade occurred."""

    if observation.trading_status != "traded":
        return False
    if observation.cumulative_volume_shares is None:
        return False
    if Decimal(observation.cumulative_volume_shares) <= 0:
        return False
    if observation.source_operation == TONGDAXIN_OPERATION:
        if observation.cumulative_amount_cny is None:
            return False
        return Decimal(observation.cumulative_amount_cny) > 0
    return True


def _adjudicate_previous_close(
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> dict[str, Any]:
    basis = (baseline.previous_close_basis, cross_check.previous_close_basis)
    values = (baseline.previous_close, cross_check.previous_close)
    if any(value is None for value in values):
        return {
            "comparable": False,
            "snapshot": {
                "status": "unavailable",
                "reported_value": baseline.previous_close,
                "unit": "CNY/share",
                "basis": baseline.previous_close_basis,
                "reason": "previous_close_unavailable",
            },
        }
    comparability = (
        baseline.previous_close_comparability,
        cross_check.previous_close_comparability,
    )
    if any(value != "comparable" for value in comparability):
        return {
            "comparable": False,
            "snapshot": {
                "status": "unavailable",
                "reported_value": baseline.previous_close,
                "unit": "CNY/share",
                "basis": baseline.previous_close_basis,
                "reason": (
                    "independent_semantics_not_adjudicated"
                    if any(value in {None, "unknown"} for value in comparability)
                    else "independent_semantics_not_comparable"
                ),
            },
        }
    if (
        baseline.corporate_action is not None
        or cross_check.corporate_action is not None
    ):
        return {
            "comparable": False,
            "snapshot": {
                "status": "unavailable",
                "reported_value": baseline.previous_close,
                "unit": "CNY/share",
                "basis": baseline.previous_close_basis,
                "reason": "corporate_action_previous_close_not_comparable",
            },
        }
    comparable_bases = {"actual_close", "ex_right_reference"}
    if (
        basis[0] not in comparable_bases
        or basis[1] not in comparable_bases
        or basis[0] != basis[1]
        or values[0] != values[1]
    ):
        return {
            "comparable": False,
            "snapshot": {
                "status": "unavailable",
                "reported_value": baseline.previous_close,
                "unit": "CNY/share",
                "basis": baseline.previous_close_basis,
                "reason": (
                    "independent_semantics_not_adjudicated"
                    if basis
                    == (
                        "source_reported_unadjudicated",
                        "source_reported_unadjudicated",
                    )
                    else "independent_semantics_not_comparable"
                ),
            },
        }
    return {
        "comparable": True,
        "snapshot": {
            "status": "available",
            "value": baseline.previous_close,
            "reported_value": baseline.previous_close,
            "unit": "CNY/share",
            "basis": baseline.previous_close_basis,
        },
    }


def _validate_observation(
    observation: IntradayObservation,
    expected_operation: str,
) -> IntradayObservation:
    """Validate and tick-normalize one adapter result at the internal seam."""

    if not isinstance(observation, IntradayObservation):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source operation returned an unsupported observation shape.",
        )
    if observation.source_operation != expected_operation:
        raise IntradaySourceError(
            expected_operation,
            "operation_identity_mismatch",
            "The source operation returned an observation with another operation identity.",
        )
    if not isinstance(observation.security, str) or not observation.security:
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not identify a security.",
        )
    if not isinstance(observation.trading_date, date):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not contain a trading date.",
        )
    for name, value in (
        ("observed_at", observation.observed_at),
        ("retrieved_at", observation.retrieved_at),
    ):
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise IntradaySourceError(
                expected_operation,
                "unknown_schema",
                f"The source observation does not contain a timezone-aware {name}.",
            )
    if observation.trading_status not in {
        "traded",
        "suspended",
        "not_traded",
        "no_trade",
        "auction",
        "unknown",
    }:
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation has an unknown trading status.",
        )
    if not isinstance(observation.no_trade_confirmed, bool):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation has an invalid no-trade confirmation.",
        )
    if observation.corporate_action is not None and (
        not isinstance(observation.corporate_action, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in observation.corporate_action.items()
        )
    ):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation corporate-action annotation is invalid.",
        )
    price_values = {
        field: _canonical_price(getattr(observation, field), expected_operation, field)
        for field in ("latest_price", "open_price", "high_price", "low_price")
    }
    _validate_ohlc_values(price_values, expected_operation)
    previous_close = observation.previous_close
    if previous_close is not None:
        previous_close = _canonical_price(
            previous_close, expected_operation, "previous_close"
        )
    volume = _canonical_nonnegative(
        observation.cumulative_volume_shares,
        expected_operation,
        "cumulative_volume",
    )
    if volume is not None and Decimal(volume) != Decimal(volume).to_integral_value():
        raise IntradaySourceError(
            expected_operation,
            "ambiguous_volume_unit",
            "The normalized cumulative volume is not a whole number of shares.",
        )
    amount = _canonical_nonnegative(
        observation.cumulative_amount_cny,
        expected_operation,
        "cumulative_amount",
    )
    if not isinstance(observation.evidence, tuple) or not observation.evidence:
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not retain evidence.",
        )
    evidence: list[dict[str, Any]] = []
    for item in observation.evidence:
        evidence.append(
            _validate_evidence_item(item, expected_operation, observation.security)
        )
    required_sources = {
        "latest_price",
        "open",
        "high",
        "low",
        "previous_close",
    }
    if expected_operation == TONGDAXIN_OPERATION:
        required_sources.update({"cumulative_volume", "cumulative_amount"})
    if (
        not isinstance(observation.field_sources, dict)
        or not required_sources.issubset(observation.field_sources)
        or any(
            not isinstance(observation.field_sources[field], tuple)
            or not observation.field_sources[field]
            or any(
                not isinstance(source_field, str) or not source_field
                for source_field in observation.field_sources[field]
            )
            for field in required_sources
        )
    ):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not retain complete field lineage.",
        )
    return replace(
        observation,
        latest_price=price_values["latest_price"],
        open_price=price_values["open_price"],
        high_price=price_values["high_price"],
        low_price=price_values["low_price"],
        previous_close=previous_close,
        previous_close_basis=_safe_previous_close_basis(
            observation.previous_close_basis
        ),
        cumulative_volume_shares=(
            format(Decimal(volume).quantize(Decimal(1)), "f")
            if volume is not None
            else None
        ),
        cumulative_amount_cny=amount,
        cache_state=_safe_cache_state(observation.cache_state),
        corporate_action=_safe_corporate_action(observation.corporate_action),
        evidence=tuple(evidence),
    )


def _canonical_price(value: object, operation: str, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int, float)):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal price.",
        )
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidOperation
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal price.",
        ) from error
    if not parsed.is_finite() or parsed <= 0:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not a positive finite price.",
        )
    normalized = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if normalized <= 0:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is below the minimum CNY tick.",
        )
    return format(normalized, "f")


def _canonical_nonnegative(
    value: object,
    operation: str,
    field: str,
) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int, float)):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal value.",
        )
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidOperation
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal value.",
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not a nonnegative finite decimal.",
        )
    return format(parsed.normalize(), "f")


def _validate_ohlc_values(values: dict[str, str], operation: str) -> None:
    low = Decimal(values["low_price"])
    high = Decimal(values["high_price"])
    opening = Decimal(values["open_price"])
    latest = Decimal(values["latest_price"])
    if low > high or opening < low or opening > high or latest < low or latest > high:
        raise IntradaySourceError(
            operation,
            "inconsistent_price_bar",
            "The source OHLC values are internally inconsistent.",
        )


def _normalize_evidence_prices(item: dict[str, Any], operation: str) -> dict[str, Any]:
    normalized = dict(item)
    observed_value = item.get("observed_value")
    if isinstance(observed_value, dict):
        observed_value = dict(observed_value)
        for source_field in ("latest_price", "open", "high", "low", "previous_close"):
            if (
                source_field in observed_value
                and observed_value[source_field] is not None
            ):
                observed_value[source_field] = _canonical_price(
                    observed_value[source_field], operation, source_field
                )
        if operation == TONGDAXIN_OPERATION:
            for source_field in ("cumulative_volume", "cumulative_amount"):
                if (
                    source_field in observed_value
                    and observed_value[source_field] is not None
                ):
                    observed_value[source_field] = _canonical_nonnegative(
                        observed_value[source_field], operation, source_field
                    )
        normalized["observed_value"] = observed_value
    return normalized


def _validate_evidence_item(
    item: object,
    operation: str,
    security: str,
) -> dict[str, Any]:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"]
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source observation evidence is not a structured record.",
        )
    if item.get("source_operation") != operation:
        raise IntradaySourceError(
            operation,
            "operation_identity_mismatch",
            "The source evidence identifies another operation.",
        )
    subject = item.get("subject")
    if (
        not isinstance(subject, dict)
        or not isinstance(subject.get("security"), str)
        or not subject["security"]
        or subject["security"] != security
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not identify a security.",
        )
    observation = item.get("observation")
    if not isinstance(observation, dict) or not isinstance(
        observation.get("kind"), str
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not identify an observation shape.",
        )
    locator = item.get("locator")
    if (
        not isinstance(locator, dict)
        or not isinstance(locator.get("uri"), str)
        or not locator["uri"].strip()
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not retain a locator.",
        )
    retrieved_at = item.get("retrieved_at")
    if not isinstance(retrieved_at, str) or not retrieved_at.strip():
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not retain a retrieval time.",
        )
    try:
        parsed_retrieved_at = datetime.fromisoformat(retrieved_at)
    except ValueError as error:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence retrieval time is not ISO formatted.",
        ) from error
    if parsed_retrieved_at.tzinfo is None:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence retrieval time has no timezone.",
        )
    kind = observation["kind"]
    if kind not in {
        "intraday_quote",
        "latest_daily_bar_date_basis",
        "intraday_core_price_cross_check",
    }:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence uses an unknown observation shape.",
        )
    if kind in {"intraday_quote", "intraday_core_price_cross_check"}:
        observed_value = item.get("observed_value")
        required_values = {"latest_price", "open", "high", "low", "previous_close"}
        if operation == TONGDAXIN_OPERATION:
            required_values.update(
                {"previous_close", "cumulative_volume", "cumulative_amount"}
            )
        if (
            not isinstance(observed_value, dict)
            or not required_values.issubset(observed_value)
            or any(observed_value[field] is None for field in required_values)
        ):
            raise IntradaySourceError(
                operation,
                "unknown_schema",
                "The source evidence does not retain complete observed values.",
            )
        for source_field in ("latest_price", "open", "high", "low"):
            _canonical_price(observed_value[source_field], operation, source_field)
        if operation == TONGDAXIN_OPERATION:
            _canonical_price(
                observed_value["previous_close"], operation, "previous_close"
            )
            _canonical_nonnegative(
                observed_value["cumulative_volume"], operation, "cumulative_volume"
            )
            _canonical_nonnegative(
                observed_value["cumulative_amount"], operation, "cumulative_amount"
            )
    normalized = _normalize_evidence_prices(item, operation)
    normalized_observation = normalized.get("observation")
    if isinstance(normalized_observation, dict):
        normalized_observation = dict(normalized_observation)
        if "cache_state" in normalized_observation:
            normalized_observation["cache_state"] = _safe_cache_state(
                normalized_observation.get("cache_state")
            )
        if "previous_close_basis" in normalized_observation:
            normalized_observation["previous_close_basis"] = _safe_previous_close_basis(
                normalized_observation.get("previous_close_basis")
            )
        if "corporate_action" in normalized_observation:
            normalized_observation["corporate_action"] = _safe_corporate_action(
                normalized_observation.get("corporate_action")
            )
        normalized["observation"] = normalized_observation
    return normalized


def _source_error_result(error: IntradaySourceError) -> dict[str, Any]:
    safe_messages = {
        "upstream_unavailable": "The source operation was unavailable.",
        "upstream_http_error": "The source operation returned an upstream error.",
        "empty_response": "The source operation returned an empty response.",
        "empty_observation": "The source operation returned no observations.",
        "unknown_schema": "The source response did not match the expected schema.",
        "wrong_security_payload": "The source response identifies another security.",
        "quote_daily_security_mismatch": "The quote and daily bar identify different securities.",
        "quote_daily_date_mismatch": "The quote and daily bar identify different dates.",
        "trading_date_mismatch": "The source daily bar is not bound to the requested date.",
        "inconsistent_price_bar": "The source OHLC values are internally inconsistent.",
        "ambiguous_volume_unit": "The source volume unit cannot be established as hands.",
        "ambiguous_volume_scope": "The source volume cumulative scope cannot be established.",
        "ambiguous_amount_unit": "The source amount unit cannot be established as CNY.",
        "ambiguous_amount_scope": "The source amount cumulative scope cannot be established.",
        "ambiguous_zero_value": "The source zero cumulative value has no explicit no-trade or suspended status.",
        "operation_identity_mismatch": "The source operation returned another operation identity.",
    }
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": safe_messages.get(
            error.code,
            "The source operation could not establish a usable observation.",
        ),
    }


def _pair_conflict(
    code: str,
    message: str,
    *,
    observations: tuple[IntradayObservation, IntradayObservation],
    field: str | None = None,
    baseline: object | None = None,
    cross_check: object | None = None,
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    conflict: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if field is not None:
        conflict["field"] = field
    if baseline is not None:
        conflict["baseline"] = baseline
    if cross_check is not None:
        conflict["cross_check"] = cross_check
    if missing_fields:
        conflict["missing_fields"] = missing_fields
    return conflict


def build_intraday_blocked_result(
    request: dict[str, Any],
    *,
    code: str,
    message: str,
    limitation_details: dict[str, Any] | None = None,
) -> UnifiedResearchResult:
    """Build a stable blocked envelope before intraday source collection."""

    detail = ""
    if limitation_details:
        detail = " " + ", ".join(
            f"{key}={value}" for key, value in sorted(limitation_details.items())
        )
    return _direct_snapshot_result(
        request,
        outcomes=_snapshot_skipped_outcomes(
            "skipped_policy"
            if code == "source_policy_not_satisfied"
            else "skipped_research_dependency"
        ),
        limitations=(DirectTaskLimitation(code, f"{message}{detail}"),),
        summary=message,
    )


def _canonical_subject_from_request(request: dict[str, Any]) -> dict[str, Any] | None:
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or len(subjects) != 1:
        return None
    subject = subjects[0]
    security = subject.get("security") if isinstance(subject, dict) else None
    if not isinstance(security, str):
        return None
    exchange, separator, code = security.partition(":")
    if (
        separator != ":"
        or exchange not in {"SSE", "SZSE"}
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        return None
    prefixes = SSE_A_SHARE_PREFIXES if exchange == "SSE" else SZSE_A_SHARE_PREFIXES
    if not code.startswith(prefixes):
        return None
    return {
        "security": {
            "exchange": exchange,
            "code": code,
            "type": "A_SHARE",
        }
    }
