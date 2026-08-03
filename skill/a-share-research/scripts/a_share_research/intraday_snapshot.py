"""Adjudicate one research-grade continuous-auction intraday snapshot."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Collection

from .intraday_contract import (
    SESSION_STATES,
    IntradayObservation,
    IntradayQuery,
    IntradaySourceError,
    IntradaySourceOperation,
    session_at,
)

SSE_A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZSE_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301")
TONGDAXIN_OPERATION = "tongdaxin_intraday_snapshot@1"
TENCENT_OPERATION = "tencent_intraday_snapshot@1"


class _IntradayDomainBlock(Exception):
    """A valid intraday request that cannot form an applicable domain result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_intraday_snapshot_result(
    request: dict[str, Any],
    operations: Collection[IntradaySourceOperation],
    research_now: datetime,
) -> dict[str, Any]:
    """Collect and cross-check one current-date intraday snapshot."""

    try:
        query = _normalize_query(request, research_now)
    except _IntradayDomainBlock as blocked:
        return _domain_blocked_result(request, blocked.code, str(blocked))
    operation_list = list(operations)
    if len(operation_list) != 2:
        raise ValueError(
            "intraday_market_signal requires exactly two source operations"
        )
    observations: list[IntradayObservation] = []
    source_errors: list[dict[str, Any]] = []
    for operation in operation_list:
        try:
            observation = operation.collect(query)
            observations.append(
                _validate_observation(observation, operation.operation_id)
            )
        except IntradaySourceError as error:
            source_errors.append(_source_error_result(error))
        except Exception:
            # Adapter implementations are outside the ResearchTask boundary.  A
            # provider exception must never escape as a traceback or reveal its
            # request, credentials, or response body through the JSON contract.
            source_errors.append(
                {
                    "source_operation": operation.operation_id,
                    "code": "operation_failure",
                    "message": "The source operation failed without a safe diagnostic.",
                }
            )
    if source_errors or len(observations) != 2:
        return _blocked_result(request, query, observations, source_errors)
    baseline, cross_check = observations
    incompatibilities = _pair_incompatibility(query, baseline, cross_check)
    if incompatibilities:
        return _incompatible_result(request, query, observations, incompatibilities)
    result_session = _result_session(query, baseline, cross_check)
    if result_session is None:
        return _incompatible_result(
            request,
            query,
            observations,
            [
                _pair_conflict(
                    "intraday_session_unknown",
                    "The current time and source timestamps do not establish an applicable session.",
                    observations=(baseline, cross_check),
                )
            ],
        )
    freshness_conflicts = _freshness_conflicts(baseline, cross_check, result_session)
    if freshness_conflicts:
        return _conflict_result(request, observations, freshness_conflicts)
    suspension_conflicts = _suspension_conflicts(baseline, cross_check)
    if suspension_conflicts:
        return _incompatible_result(request, query, observations, suspension_conflicts)
    assert baseline.previous_close is not None
    assert baseline.previous_close_basis is not None
    baseline_id = baseline.evidence[0]["id"]
    cross_check_id = cross_check.evidence[0]["id"]
    core_evidence = [baseline_id, cross_check_id]
    all_evidence = [
        item["id"] for observation in observations for item in observation.evidence
    ]
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
    result = {
        "schema_version": request["schema_version"],
        "status": "limited",
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
        "source_operations": [item.source_operation for item in observations],
        "field_lineage": {
            "subject": {
                "evidence_ids": core_evidence,
                "source_fields": ["code", "market", "qt.security"],
            },
            "trading_date": {
                "evidence_ids": all_evidence,
                "source_fields": ["year", "month", "day", "day.date"],
            },
            "session_state": {
                "evidence_ids": core_evidence,
                "source_fields": ["servertime", "qt.timestamp"],
            },
            "trading_status": {
                "evidence_ids": core_evidence,
                "source_fields": ["vol", "day.volume"],
            },
            "price_type": {
                "evidence_ids": core_evidence,
                "source_fields": ["price", "day.close", "qt.timestamp"],
            },
            **{
                f"snapshot.{field}": {
                    "evidence_ids": core_evidence,
                    "source_fields": [
                        *baseline.field_sources[field],
                        *cross_check.field_sources[field],
                    ],
                }
                for field in (
                    "latest_price",
                    "open",
                    "high",
                    "low",
                )
            },
            "snapshot.previous_close": {
                "evidence_ids": [baseline_id],
                "source_fields": list(baseline.field_sources["previous_close"]),
            },
            "snapshot.cumulative_volume": {
                "evidence_ids": [baseline_id],
                "source_fields": list(baseline.field_sources["cumulative_volume"]),
            },
            "snapshot.cumulative_amount": {
                "evidence_ids": [baseline_id],
                "source_fields": list(baseline.field_sources["cumulative_amount"]),
            },
            "observation_times.tongdaxin_baseline": {
                "evidence_ids": [baseline_id],
                "source_fields": ["servertime"],
            },
            "observation_times.tencent_cross_check": {
                "evidence_ids": [cross_check_id],
                "source_fields": ["qt.timestamp"],
            },
            "observation_times.retrieved_at": {
                "evidence_ids": all_evidence,
                "source_fields": ["retrieved_at"],
            },
            "observation_times.pair_gap_seconds": {
                "evidence_ids": core_evidence,
                "source_fields": ["servertime", "qt.timestamp"],
                "calculation": "absolute_time_difference_seconds@1",
            },
            **(
                {
                    "observation_times.observation_boundary": {
                        "evidence_ids": core_evidence,
                        "source_fields": ["observation_boundary"],
                    }
                }
                if result_session == "midday_break"
                else {}
            ),
        },
        "brief": {
            "status": "limited",
            "summary": (
                "Two experimental operations agree on one "
                f"{result_session} intraday market snapshot."
            ),
            "evidence_ids": core_evidence,
        },
        "evidence": [
            item for observation in observations for item in observation.evidence
        ],
        "conflicts": [],
        "source_errors": [],
        "limitations": [
            {
                "code": "experimental_intraday_sources",
                "message": (
                    "The source operations agree but have not completed production "
                    "qualification; the snapshot is limited."
                ),
            },
            *(
                [
                    {
                        "code": "midday_break_morning_observation",
                        "message": (
                            "Prices and cumulative trading remain at the last compatible "
                            "morning observation during the midday break."
                        ),
                    }
                ]
                if result_session == "midday_break"
                else []
            ),
        ],
    }
    previous_close = _adjudicate_previous_close(baseline, cross_check)
    result["snapshot"]["previous_close"] = previous_close["snapshot"]
    if previous_close["comparable"]:
        change_amount = Decimal(baseline.latest_price) - Decimal(
            baseline.previous_close
        )
        change_percent = (
            (change_amount / Decimal(baseline.previous_close)) * Decimal(100)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result["snapshot"]["change_amount"] = {
            "value": _decimal_text(change_amount),
            "unit": "CNY/share",
        }
        result["snapshot"]["change_percent"] = {
            "value": _decimal_text(change_percent),
            "unit": "percent",
        }
    if _is_confirmed_suspension(baseline, cross_check):
        return _suspended_result(result, baseline, cross_check)
    return result


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
    suspended_statuses = {"suspended", "not_traded", "no_trade"}
    all_suspended = all(
        item.trading_status in suspended_statuses for item in observations
    )
    any_suspended = any(
        item.trading_status in suspended_statuses for item in observations
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
                    "cache_state": observation.cache_state,
                    "evidence_ids": [item["id"] for item in observation.evidence],
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
                        "evidence_ids": [item["id"] for item in observation.evidence],
                    }
                )
            elif age > 60:
                conflicts.append(
                    {
                        "code": "intraday_observation_too_old",
                        "message": "An active-session source observation is older than 60 seconds.",
                        "source_operation": observation.source_operation,
                        "age_seconds": _seconds_text(age),
                        "evidence_ids": [item["id"] for item in observation.evidence],
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
                "evidence_ids": [
                    item["id"]
                    for observation in observations
                    for item in observation.evidence
                ],
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
    suspended_statuses = {"suspended", "not_traded", "no_trade"}
    statuses = [item.trading_status for item in observations]
    if not any(status in suspended_statuses for status in statuses):
        if (
            baseline.previous_close is not None
            and cross_check.previous_close is not None
            and baseline.latest_price == baseline.previous_close
            and cross_check.latest_price == cross_check.previous_close
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
                "Only one source explicitly confirms suspension.",
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
    statuses = {"suspended", "not_traded", "no_trade"}
    return all(
        item.trading_status in statuses and item.no_trade_confirmed
        for item in (baseline, cross_check)
    )


def _suspended_result(
    result: dict[str, Any],
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> dict[str, Any]:
    result = dict(result)
    snapshot = dict(result["snapshot"])
    not_applicable = {
        "status": "not_applicable",
        "value": None,
        "unit": "CNY/share",
        "reason": "suspended",
    }
    for field in ("latest_price", "open", "high", "low"):
        snapshot[field] = dict(not_applicable)
    snapshot["cumulative_volume"] = {"value": "0", "unit": "shares"}
    snapshot["cumulative_amount"] = {"value": "0", "unit": "CNY"}
    result["snapshot"] = snapshot
    result["trading_status"] = "suspended"
    result["price_type"] = "not_applicable"
    result["brief"] = {
        "status": "limited",
        "summary": (
            "Two experimental operations jointly confirm a current-session suspension; "
            "prices are not applicable and no trading is confirmed."
        ),
        "evidence_ids": result["brief"]["evidence_ids"],
    }
    result["limitations"] = [
        {
            "code": "intraday_suspension_confirmed",
            "message": (
                "Both independent source operations explicitly confirm suspension "
                "and no trading for the applicable session."
            ),
        },
        *result["limitations"],
    ]
    return result


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


def _conflict_result(
    request: dict[str, Any],
    observations: list[IntradayObservation],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = {
        "evidence": [
            item for observation in observations for item in observation.evidence
        ],
        "source_operations": [item.source_operation for item in observations],
        "observation_times": {
            item.source_operation: item.observed_at.isoformat() for item in observations
        },
        "field_lineage": {
            f"{item.source_operation}.{field}": {
                "evidence_ids": [entry["id"] for entry in item.evidence],
                "source_fields": list(source_fields),
            }
            for item in observations
            for field, source_fields in item.field_sources.items()
        },
    }
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        **diagnostics,
        "conflicts": conflicts,
        "source_errors": [],
        "limitations": [
            {
                "code": "intraday_freshness_not_satisfied",
                "message": "Active-session observations must be fresh and mutually compatible.",
            }
        ],
    }


def _incompatible_result(
    request: dict[str, Any],
    query: IntradayQuery,
    observations: list[IntradayObservation],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = _diagnostic_fields(query, observations)
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        **diagnostics,
        "conflicts": conflicts,
        "source_errors": [],
        "limitations": [
            {
                "code": "intraday_source_pair_incompatible",
                "message": "The two source observations cannot form one snapshot.",
            }
        ],
    }


def _blocked_result(
    request: dict[str, Any],
    query: IntradayQuery,
    observations: list[IntradayObservation],
    source_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = _diagnostic_fields(query, observations)
    for source_error in source_errors:
        operation = source_error.get("source_operation")
        if (
            isinstance(operation, str)
            and operation not in diagnostics["source_operations"]
        ):
            diagnostics["source_operations"].append(operation)
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        **diagnostics,
        "conflicts": [],
        "source_errors": source_errors,
        "limitations": [
            {
                "code": "intraday_source_pair_incomplete",
                "message": "Both required intraday source operations must succeed.",
            }
        ],
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
        cumulative_volume_shares=(
            format(Decimal(volume).quantize(Decimal(1)), "f")
            if volume is not None
            else None
        ),
        cumulative_amount_cny=amount,
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
    return _normalize_evidence_prices(item, operation)


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
        "evidence_ids": _evidence_ids(observations),
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


def _evidence_ids(
    observations: tuple[IntradayObservation, ...] | list[IntradayObservation],
) -> list[str]:
    return [
        item["id"]
        for observation in observations
        for item in observation.evidence
        if isinstance(item.get("id"), str)
    ]


def _diagnostic_fields(
    query: IntradayQuery,
    observations: list[IntradayObservation],
) -> dict[str, Any]:
    evidence = [item for observation in observations for item in observation.evidence]
    fields: dict[str, Any] = {
        "subject": {
            "security": {
                "exchange": query.exchange,
                "code": query.code,
                "type": "A_SHARE",
            }
        },
        "evidence": evidence,
        "source_operations": [item.source_operation for item in observations],
        "observation_times": {
            "retrieved_at": max(
                [query.retrieved_at, *(item.retrieved_at for item in observations)]
            ).isoformat(),
        },
        "field_lineage": {},
        "brief": {
            "status": "blocked",
            "evidence_ids": _evidence_ids(observations),
        },
    }
    for observation in observations:
        prefix = observation.source_operation
        fields["observation_times"][prefix] = observation.observed_at.isoformat()
        for field, source_fields in observation.field_sources.items():
            fields["field_lineage"].setdefault(
                f"{prefix}.{field}",
                {
                    "evidence_ids": [item["id"] for item in observation.evidence],
                    "source_fields": list(source_fields),
                },
            )
    if observations:
        fields["trading_date"] = observations[0].trading_date.isoformat()
    fields["as_of"] = query.as_of.isoformat()
    return fields


def _domain_blocked_result(
    request: dict[str, Any], code: str, message: str
) -> dict[str, Any]:
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        "evidence": [],
        "conflicts": [],
        "source_errors": [],
        "limitations": [{"code": code, "message": message}],
    }
