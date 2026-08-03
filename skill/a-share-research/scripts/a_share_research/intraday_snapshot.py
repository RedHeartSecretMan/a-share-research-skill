"""Adjudicate one research-grade continuous-auction intraday snapshot."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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
    source_errors: list[dict[str, str]] = []
    for operation in operation_list:
        try:
            observations.append(operation.collect(query))
        except IntradaySourceError as error:
            source_errors.append(
                {
                    "source_operation": error.source_operation,
                    "code": error.code,
                    "message": str(error),
                }
            )
    if source_errors or len(observations) != 2:
        return _blocked_result(request, observations, source_errors)
    baseline, cross_check = observations
    incompatibility = _pair_incompatibility(query, baseline, cross_check)
    if incompatibility is not None:
        return _incompatible_result(request, observations, incompatibility)
    result_session = _result_session(query, baseline, cross_check)
    if result_session is None:
        return _incompatible_result(
            request,
            observations,
            (
                "intraday_session_unknown",
                "The current time and source timestamps do not establish an applicable session.",
            ),
        )
    freshness_conflicts = _freshness_conflicts(baseline, cross_check, result_session)
    if freshness_conflicts:
        return _conflict_result(request, observations, freshness_conflicts)
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
    return {
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
) -> tuple[str, str] | None:
    if any(item.security != query.security for item in (baseline, cross_check)):
        return (
            "intraday_security_mismatch",
            "Intraday source operations returned a different security.",
        )
    if any(item.trading_date != query.as_of for item in (baseline, cross_check)):
        return (
            "intraday_trading_date_mismatch",
            "Intraday source operations returned a different trading date.",
        )
    expected_session = session_at(query.retrieved_at)
    if expected_session is None:
        return (
            "intraday_session_unknown",
            "The current retrieval time is outside an applicable trading session.",
        )
    if any(
        item.session_state not in SESSION_STATES for item in (baseline, cross_check)
    ):
        return (
            "intraday_session_unknown",
            "Intraday source observations do not establish a known trading session.",
        )
    if any(
        session_at(item.observed_at) != item.session_state
        for item in (baseline, cross_check)
    ):
        return (
            "intraday_session_mismatch",
            "A source session state is incompatible with its observation timestamp.",
        )
    if expected_session == "midday_break":
        if any(item.session_state != "continuous" for item in (baseline, cross_check)):
            return (
                "intraday_session_mismatch",
                "The midday result must retain compatible morning continuous observations.",
            )
        if any(
            item.observation_boundary != "morning_last_compatible"
            for item in (baseline, cross_check)
        ):
            return (
                "intraday_morning_observation_not_last",
                "The midday result requires each source to identify its morning-last observation boundary.",
            )
    elif any(
        item.session_state != expected_session for item in (baseline, cross_check)
    ):
        return (
            "intraday_session_mismatch",
            "Intraday source observations are not in the current trading session.",
        )
    expected_trading_status = (
        "auction"
        if expected_session in {"opening_auction", "closing_auction"}
        else "traded"
    )
    if expected_trading_status == "auction":
        status_compatible = all(
            item.trading_status in {"auction", "unknown", "not_traded"}
            for item in (baseline, cross_check)
        )
    else:
        status_compatible = all(
            item.trading_status == expected_trading_status
            for item in (baseline, cross_check)
        )
    if not status_compatible:
        return (
            "intraday_trading_status_mismatch",
            "Intraday source observations do not establish the current session status.",
        )
    expected_price_type = (
        "indicative_auction"
        if expected_session in {"opening_auction", "closing_auction"}
        else "latest_traded"
    )
    if any(item.price_type != expected_price_type for item in (baseline, cross_check)):
        return (
            "intraday_price_type_mismatch",
            "Intraday source observations do not establish the current session price type.",
        )
    for field in (
        "latest_price",
        "open_price",
        "high_price",
        "low_price",
    ):
        if Decimal(getattr(baseline, field)) != Decimal(getattr(cross_check, field)):
            return (
                "intraday_core_price_mismatch",
                f"Intraday source observations disagree on {field}.",
            )
    if (
        baseline.previous_close is None
        or baseline.previous_close_basis is None
        or baseline.cumulative_volume_shares is None
        or baseline.cumulative_amount_cny is None
    ):
        return (
            "intraday_baseline_incomplete",
            "TongdaXin did not establish previous-close availability, volume, and amount.",
        )
    return None


def _freshness_conflicts(
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
    result_session: str,
) -> list[dict[str, Any]]:
    """Apply the 60-second active-session observation and pair bounds."""

    conflicts: list[dict[str, Any]] = []
    observations = (baseline, cross_check)
    for observation in observations:
        if observation.cache_state not in {
            "source_timestamp",
            "uncached",
            "fresh",
        }:
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


def _seconds_text(value: float) -> str:
    decimal_value = Decimal(str(value))
    return format(decimal_value.normalize(), "f")


def _conflict_result(
    request: dict[str, Any],
    observations: list[IntradayObservation],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = [item for observation in observations for item in observation.evidence]
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        "evidence": evidence,
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
    observations: list[IntradayObservation],
    problem: tuple[str, str],
) -> dict[str, Any]:
    evidence = [item for observation in observations for item in observation.evidence]
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        "evidence": evidence,
        "conflicts": [
            {
                "code": problem[0],
                "message": problem[1],
                "evidence_ids": [item["id"] for item in evidence],
            }
        ],
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
    observations: list[IntradayObservation],
    source_errors: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        "evidence": [
            item for observation in observations for item in observation.evidence
        ],
        "conflicts": [],
        "source_errors": source_errors,
        "limitations": [
            {
                "code": "intraday_source_pair_incomplete",
                "message": "Both required intraday source operations must succeed.",
            }
        ],
    }


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
