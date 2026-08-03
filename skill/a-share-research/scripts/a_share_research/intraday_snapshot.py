"""Adjudicate one research-grade continuous-auction intraday snapshot."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Collection

from .intraday_contract import (
    IntradayObservation,
    IntradayQuery,
    IntradaySourceError,
    IntradaySourceOperation,
)

SSE_A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZSE_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301")


def build_intraday_snapshot_result(
    request: dict[str, Any],
    operations: Collection[IntradaySourceOperation],
    research_now: datetime,
) -> dict[str, Any]:
    """Collect and cross-check one current-date continuous-auction snapshot."""

    query = _normalize_query(request, research_now)
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
    assert baseline.previous_close is not None
    assert baseline.previous_close_basis is not None
    baseline_id = baseline.evidence[0]["id"]
    cross_check_id = cross_check.evidence[0]["id"]
    core_evidence = [baseline_id, cross_check_id]
    all_evidence = [
        item["id"] for observation in observations for item in observation.evidence
    ]
    gap_seconds = abs(
        int((baseline.observed_at - cross_check.observed_at).total_seconds())
    )
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
        "session_state": "continuous",
        "trading_status": "traded",
        "price_type": "latest_traded",
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
        "observation_times": {
            "tongdaxin_baseline": baseline.observed_at.isoformat(),
            "tencent_cross_check": cross_check.observed_at.isoformat(),
            "retrieved_at": max(
                baseline.retrieved_at, cross_check.retrieved_at
            ).isoformat(),
            "pair_gap_seconds": str(gap_seconds),
        },
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
                "Two experimental operations agree on one continuous-auction "
                "intraday market snapshot."
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
            }
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
        raise ValueError("intraday_market_signal requires current China-date as_of")
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
    if any(item.session_state != "continuous" for item in (baseline, cross_check)):
        return (
            "intraday_session_mismatch",
            "Intraday source observations are not continuous-auction data.",
        )
    if any(item.trading_status != "traded" for item in (baseline, cross_check)):
        return (
            "intraday_trading_status_mismatch",
            "Intraday source observations do not establish traded status.",
        )
    if any(item.price_type != "latest_traded" for item in (baseline, cross_check)):
        return (
            "intraday_price_type_mismatch",
            "Intraday source observations do not establish latest traded prices.",
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
