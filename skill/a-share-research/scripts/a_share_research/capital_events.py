"""Coordinate capital-flow, trading-event, and corporate-action observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Collection

from .capital_contract import (
    CAPITAL_DATA_TYPES,
    CAPITAL_SOURCE_ROLES,
    CapitalObservation,
    CapitalQuery,
    CapitalSourceFailure,
    CapitalSourceOperation,
)
from .identity_resolution import resolve_security_identity
from .identity_sources import HttpTransport

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
SUBJECT_FREE_DATA_TYPES = frozenset(
    {"northbound_flow", "board_fund_flow", "market_dragon_tiger"}
)
SUBJECT_REQUIRED_DATA_TYPES = CAPITAL_DATA_TYPES.difference(SUBJECT_FREE_DATA_TYPES)


def build_capital_events_result(
    request: dict[str, Any],
    operations: Collection[CapitalSourceOperation],
    identity_transport: HttpTransport,
) -> dict[str, Any]:
    """Collect and normalize capital-event evidence under one public task."""

    query = _normalize_query(request)
    identity = _resolve_subject(request, identity_transport)
    if identity["status"] == "blocked":
        return _blocked_identity_result(request, query, identity)
    if identity["subjects"]:
        query = replace(query, subject=identity["subjects"][0])

    source_errors: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    observations: list[CapitalObservation] = []
    batch_limitations: set[str] = set()
    coverage_batches: dict[str, list[tuple[str, int, bool, bool]]] = {
        data_type: [] for data_type in query.data_types
    }
    for operation in operations:
        applicable_types = operation.supported_data_types.intersection(query.data_types)
        if not applicable_types:
            continue
        batch = operation.collect(query)
        valid, schema_errors = _validate_observations(
            batch.operation_id,
            batch.observations,
            query,
        )
        valid_in_window = [item for item in valid if _within_window(item, query)]
        has_errors = bool(schema_errors or batch.source_errors)
        for data_type in query.data_types:
            if data_type not in applicable_types:
                continue
            coverage_batches[data_type].append(
                (
                    batch.operation_id,
                    sum(item.data_type == data_type for item in valid_in_window),
                    batch.complete,
                    has_errors,
                )
            )
        observations.extend(valid)
        source_errors.extend(item.to_result() for item in schema_errors)
        source_errors.extend(item.to_result() for item in batch.source_errors)
        degradations.extend(item.to_result() for item in batch.degradations)
        batch_limitations.update(batch.limitations)

    accepted = [item for item in observations if _within_window(item, query)]
    deduplicated = _deduplicate(accepted)
    deduplicated_counts: dict[str, int] = {}
    for item in deduplicated:
        data_type = item["data_type"]
        deduplicated_counts[data_type] = deduplicated_counts.get(data_type, 0) + 1
    selected = _limit_per_type(deduplicated, query.limit)
    type_counts: dict[str, int] = {}
    for item in selected:
        data_type = item["data_type"]
        type_counts[data_type] = type_counts.get(data_type, 0) + 1
    coverage = _coverage_by_data_type(
        query.data_types,
        type_counts,
        coverage_batches,
    )

    limitation_codes = batch_limitations | {
        code for item in accepted for code in item.limitations
    }
    limitations: list[dict[str, Any]] = [
        {
            "code": "experimental_capital_event_sources",
            "message": (
                "Capital-event observations currently use experimental source "
                "operations and cannot independently establish a supported claim."
            ),
        }
    ]
    limitations.extend(
        {"code": code, "message": _limitation_message(code)}
        for code in sorted(limitation_codes)
    )
    truncated_types = sorted(
        data_type
        for data_type, count in deduplicated_counts.items()
        if count > query.limit
    )
    if truncated_types:
        limitations.append(
            {
                "code": "result_truncated_to_limit",
                "message": (
                    "The public result contains only the requested number of "
                    "observations for at least one data type."
                ),
                "data_types": truncated_types,
            }
        )
    missing_types = sorted(
        data_type
        for data_type, item in coverage.items()
        if item["state"] == "indeterminate"
    )
    if missing_types:
        limitations.append(
            {
                "code": "requested_data_type_unavailable",
                "message": (
                    "At least one requested capital-event type produced no usable "
                    "observation; this does not prove the event or flow did not exist."
                ),
                "data_types": missing_types,
            }
        )
    limitations.extend(identity["limitations"])
    if not selected and missing_types:
        limitations.append(
            {
                "code": "capital_events_unavailable",
                "message": (
                    "No contract-complete capital-event observation was established "
                    "for the requested window."
                ),
            }
        )

    evidence_observations = _limit_per_type(
        _deduplicate_observations(accepted),
        query.limit,
    )
    return {
        "schema_version": request["schema_version"],
        "status": "blocked" if missing_types else "limited",
        "subjects": identity["subjects"],
        "observations": selected,
        "brief": {
            "observation_count": len(selected),
            "data_types": list(query.data_types),
            "data_type_counts": type_counts,
            "coverage": coverage,
        },
        "evidence": [
            *identity["evidence"],
            *[_to_evidence(item) for item in evidence_observations],
        ],
        "conflicts": identity["conflicts"],
        "source_errors": [*identity["source_errors"], *source_errors],
        "degradations": degradations,
        "limitations": limitations,
    }


def _coverage_by_data_type(
    data_types: tuple[str, ...],
    type_counts: dict[str, int],
    batches: dict[str, list[tuple[str, int, bool, bool]]],
) -> dict[str, dict[str, Any]]:
    coverage: dict[str, dict[str, Any]] = {}
    for data_type in data_types:
        contributions = batches[data_type]
        observation_count = type_counts.get(data_type, 0)
        complete_source = any(
            complete and not has_errors for _, _, complete, has_errors in contributions
        )
        complete_nonempty_source = any(
            count > 0 and complete and not has_errors
            for _, count, complete, has_errors in contributions
        )
        if observation_count:
            state = "observed_nonempty" if complete_nonempty_source else "partial"
        elif complete_source:
            state = "observed_empty"
        else:
            state = "indeterminate"
        coverage[data_type] = {
            "state": state,
            "observation_count": observation_count,
            "source_operations": list(dict.fromkeys(item[0] for item in contributions)),
        }
    return coverage


def _normalize_query(request: dict[str, Any]) -> CapitalQuery:
    parameters = request["parameters"]
    data_types = parameters.get("data_types")
    if (
        not isinstance(data_types, list)
        or not data_types
        or any(not isinstance(item, str) or not item for item in data_types)
    ):
        raise ValueError(
            "capital_events parameters.data_types must be a non-empty string array"
        )
    normalized_types = tuple(dict.fromkeys(data_types))
    unknown = sorted(set(normalized_types).difference(CAPITAL_DATA_TYPES))
    if unknown:
        raise ValueError(
            "capital_events has unsupported data_types: " + ", ".join(unknown)
        )
    subject_types = set(normalized_types).intersection(SUBJECT_REQUIRED_DATA_TYPES)
    subject_free_types = set(normalized_types).intersection(SUBJECT_FREE_DATA_TYPES)
    if subject_types and subject_free_types:
        raise ValueError(
            "capital_events cannot mix subject-required and subject-free data types"
        )
    subjects = request["subjects"]
    if subject_types and len(subjects) != 1:
        raise ValueError("the requested capital_events data requires one subject")
    if subject_free_types and subjects:
        raise ValueError(
            "the requested market-wide capital_events data takes no subject"
        )

    window = request["window"]
    if not isinstance(window, dict):
        raise ValueError("capital_events window must be an object")
    observed_from = _explicit_date(window.get("observed_from"), "observed_from")
    observed_to = _explicit_date(window.get("observed_to"), "observed_to")
    if observed_from > observed_to:
        raise ValueError("capital_events window starts after it ends")
    if set(normalized_types) == {"lockup"}:
        latest = date.fromisoformat(request["as_of"]) + timedelta(days=90)
        if date.fromisoformat(observed_to) > latest:
            raise ValueError("capital_events lockup window exceeds 90 days")
    elif observed_to > request["as_of"]:
        raise ValueError("capital_events window exceeds the research date")

    limit = parameters.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("capital_events parameters.limit must be from 1 to 100")
    subject = subjects[0] if subjects else None
    if subject is not None and not isinstance(subject, dict):
        raise ValueError("capital_events subject must be an object")
    return CapitalQuery(
        data_types=normalized_types,
        as_of=request["as_of"],
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        subject=subject,
        parameters=dict(parameters),
        allow_fallback=request["source_policy"]["allow_fallback"],
    )


def _resolve_subject(
    request: dict[str, Any], transport: HttpTransport
) -> dict[str, Any]:
    subjects = request["subjects"]
    if not subjects:
        return {
            "status": "limited",
            "subjects": [],
            "evidence": [],
            "conflicts": [],
            "source_errors": [],
            "limitations": [],
        }
    subject = subjects[0]
    clue = subject.get("clue") if isinstance(subject, dict) else None
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("capital_events subject requires a non-empty clue")
    resolved = resolve_security_identity(clue.strip(), request["as_of"], transport)
    candidates_value = resolved.get("candidates", [])
    candidates = candidates_value if isinstance(candidates_value, list) else []
    if resolved["status"] == "blocked" or len(candidates) != 1:
        return {
            "status": "blocked",
            "subjects": [],
            "evidence": resolved.get("evidence", []),
            "conflicts": resolved.get("conflicts", []),
            "source_errors": resolved.get("source_errors", []),
            "limitations": resolved.get("limitations", []),
        }
    candidate = candidates[0]
    if not isinstance(candidate, dict) or not isinstance(
        candidate.get("security"), dict
    ):
        raise ValueError("identity result does not contain a canonical security")
    security = candidate["security"]
    return {
        "status": resolved["status"],
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
        "evidence": resolved.get("evidence", []),
        "conflicts": resolved.get("conflicts", []),
        "source_errors": resolved.get("source_errors", []),
        "limitations": resolved.get("limitations", []),
    }


def _validate_observations(
    operation_id: str,
    observations: tuple[CapitalObservation, ...],
    query: CapitalQuery,
) -> tuple[list[CapitalObservation], list[CapitalSourceFailure]]:
    accepted: list[CapitalObservation] = []
    errors: list[CapitalSourceFailure] = []
    for observation in observations:
        invalid_fields: list[str] = []
        if observation.data_type not in CAPITAL_DATA_TYPES:
            invalid_fields.append("data_type")
        if observation.data_type not in query.data_types:
            invalid_fields.append("unrequested_data_type")
        if observation.source_role not in CAPITAL_SOURCE_ROLES:
            invalid_fields.append("source_role")
        if observation.source_operation != operation_id:
            invalid_fields.append("source_operation")
        if observation.retrieved_at.utcoffset() is None:
            invalid_fields.append("retrieved_at")
        if _strict_date(observation.observed_on) is None:
            invalid_fields.append("observed_on")
        if not _valid_period(observation):
            invalid_fields.append("period")
        metric_keys = set(observation.metrics)
        if metric_keys != set(observation.units) or metric_keys != set(
            observation.directions
        ):
            invalid_fields.append("metric_contract")
        if any(
            value is not None and not isinstance(value, str)
            for value in observation.metrics.values()
        ):
            invalid_fields.append("metrics")
        if any(
            not isinstance(value, str) or not value
            for value in observation.units.values()
        ):
            invalid_fields.append("units")
        if any(
            not isinstance(value, str) or not value
            for value in observation.directions.values()
        ):
            invalid_fields.append("directions")
        if query.subject is not None and observation.subject != query.subject:
            invalid_fields.append("subject")
        if query.subject is None:
            if observation.data_type == "market_dragon_tiger":
                unresolved_provider_security = (
                    observation.subject is None
                    and "security_exchange_unverified" in observation.limitations
                )
                if not unresolved_provider_security:
                    invalid_fields.append("subject")
            elif observation.subject is not None:
                invalid_fields.append("subject")
            market_scope = observation.dimensions.get("market_scope")
            if not isinstance(market_scope, str) or not market_scope:
                invalid_fields.append("market_scope")
        availability_error = _availability_error(observation, query)
        if availability_error == "availability_time_unknown_outside_research_date":
            errors.append(
                CapitalSourceFailure(
                    operation_id,
                    availability_error,
                    (
                        "An observation with unknown availability time was retrieved "
                        "outside the research date and cannot be used historically."
                    ),
                    {
                        "as_of": query.as_of,
                        "retrieved_on": observation.retrieved_at.astimezone(
                            CHINA_STANDARD_TIME
                        )
                        .date()
                        .isoformat(),
                    },
                )
            )
            continue
        if availability_error is not None:
            invalid_fields.append("available_at")
        if invalid_fields:
            errors.append(
                CapitalSourceFailure(
                    operation_id,
                    "unknown_schema",
                    "A source observation is outside the public capital-event contract.",
                    {"invalid_fields": sorted(set(invalid_fields))},
                )
            )
            continue
        accepted.append(observation)
    return accepted, errors


def _within_window(observation: CapitalObservation, query: CapitalQuery) -> bool:
    observed_on = _strict_date(observation.observed_on)
    if observed_on is None:
        return False
    if not query.observed_from <= observed_on <= query.observed_to:
        return False
    return True


def _valid_period(observation: CapitalObservation) -> bool:
    period_start = _strict_date(observation.period.get("start"))
    period_end = _strict_date(observation.period.get("end"))
    frequency = observation.period.get("frequency")
    if period_end is None or not isinstance(frequency, str) or not frequency:
        return False
    if period_start is not None:
        return period_start <= period_end
    expected_lookback = {
        "rolling_5_trading_days": "5",
        "rolling_10_trading_days": "10",
    }.get(frequency)
    return bool(
        observation.data_type == "board_fund_flow"
        and observation.period.get("start") is None
        and expected_lookback is not None
        and observation.period.get("lookback_trading_days") == expected_lookback
        and "period_start_not_exposed" in observation.limitations
    )


def _availability_error(
    observation: CapitalObservation,
    query: CapitalQuery,
) -> str | None:
    if observation.retrieved_at.utcoffset() is None:
        return "unknown_schema"
    if observation.available_at is None:
        if "availability_time_unknown" not in observation.limitations:
            return "unknown_schema"
        if (
            observation.retrieved_at.astimezone(CHINA_STANDARD_TIME).date().isoformat()
            != query.as_of
        ):
            return "availability_time_unknown_outside_research_date"
        return None
    try:
        available_at = datetime.fromisoformat(observation.available_at)
    except ValueError:
        return "unknown_schema"
    research_boundary = datetime.combine(
        date.fromisoformat(query.as_of),
        time.max,
        tzinfo=CHINA_STANDARD_TIME,
    )
    is_valid = bool(
        available_at.utcoffset() is not None
        and available_at <= observation.retrieved_at
        and available_at <= research_boundary
    )
    return None if is_valid else "unknown_schema"


def _deduplicate(
    observations: list[CapitalObservation],
) -> list[dict[str, Any]]:
    return [_to_result(item) for item in _deduplicate_observations(observations)]


def _deduplicate_observations(
    observations: list[CapitalObservation],
) -> list[CapitalObservation]:
    selected: dict[str, CapitalObservation] = {}
    for item in observations:
        selected.setdefault(_observation_identity(item), item)
    return list(selected.values())


def _limit_per_type(items: list[Any], limit: int) -> list[Any]:
    counts: dict[str, int] = {}
    selected: list[Any] = []
    for item in items:
        data_type = item["data_type"] if isinstance(item, dict) else item.data_type
        if counts.get(data_type, 0) >= limit:
            continue
        counts[data_type] = counts.get(data_type, 0) + 1
        selected.append(item)
    return selected


def _observation_identity(observation: CapitalObservation) -> str:
    payload = {
        "data_type": observation.data_type,
        "source_operation": observation.source_operation,
        "subject": observation.subject,
        "observed_on": observation.observed_on,
        "period": observation.period,
        "metrics": observation.metrics,
        "units": observation.units,
        "directions": observation.directions,
        "dimensions": observation.dimensions,
        "locator_uri": observation.locator_uri,
    }
    serialized = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _to_result(observation: CapitalObservation) -> dict[str, Any]:
    return {
        "data_type": observation.data_type,
        "source_operation": observation.source_operation,
        "source_role": observation.source_role,
        "claim_eligibility": "experimental_observation_only",
        "subject": observation.subject,
        "observed_on": observation.observed_on,
        "available_at": observation.available_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "period": observation.period,
        "metrics": observation.metrics,
        "units": observation.units,
        "directions": observation.directions,
        "dimensions": observation.dimensions,
        "locator": {"uri": observation.locator_uri},
        "limitations": list(observation.limitations),
    }


def _to_evidence(observation: CapitalObservation) -> dict[str, Any]:
    identity = _observation_identity(observation)
    return {
        "id": f"capital-{identity}",
        "source_role": observation.source_role,
        "source_operation": observation.source_operation,
        "experimental": True,
        "subject": observation.subject,
        "observation": {
            "kind": observation.data_type,
            "observed_on": observation.observed_on,
            "period": observation.period,
            "metrics": observation.metrics,
            "units": observation.units,
            "directions": observation.directions,
            "dimensions": observation.dimensions,
        },
        "evidence_time": observation.observed_on,
        "available_at": observation.available_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "locator": {"uri": observation.locator_uri},
        "limitations": list(observation.limitations),
    }


def _blocked_identity_result(
    request: dict[str, Any], query: CapitalQuery, identity: dict[str, Any]
) -> dict[str, Any]:
    coverage = _coverage_by_data_type(
        query.data_types,
        {},
        {data_type: [] for data_type in query.data_types},
    )
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": [],
        "observations": [],
        "brief": {
            "observation_count": 0,
            "data_types": list(query.data_types),
            "data_type_counts": {},
            "coverage": coverage,
        },
        "evidence": identity["evidence"],
        "conflicts": identity["conflicts"],
        "source_errors": identity["source_errors"],
        "degradations": [],
        "limitations": identity["limitations"],
    }


def _explicit_date(value: object, field: str) -> str:
    parsed = _strict_date(value)
    if parsed is None:
        raise ValueError(f"capital_events {field} must use YYYY-MM-DD")
    return parsed


def _strict_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _limitation_message(code: str) -> str:
    return {
        "availability_time_unknown": (
            "The source does not expose when this observation first became available."
        ),
        "northbound_net_flow_not_disclosed": (
            "The current disclosure regime does not expose a contract-complete "
            "northbound net-flow value."
        ),
        "security_exchange_unverified": (
            "The all-market source exposes a provider security code but does not "
            "establish its canonical SSE or SZSE identity."
        ),
        "source_value_missing": (
            "The provider left at least one metric null; the result preserves that "
            "gap instead of converting it to zero."
        ),
        "pagination_incomplete": (
            "The source did not prove complete coverage of the requested window."
        ),
        "period_start_not_exposed": (
            "The provider labels the metric as a rolling trading-day window but "
            "does not expose the first included trading date."
        ),
        "dragon_tiger_seat_and_institution_details_not_collected": (
            "Seat and institution details were collected only for the latest "
            "dragon-tiger event; empty fields on earlier events do not mean none."
        ),
        "observation_time_precision_is_date_only": (
            "The source exposes the observation date but not an intraday timestamp."
        ),
        "event_date_uses_plan_or_report_date": (
            "The distribution has no ex-dividend date, so its event date uses the "
            "plan notice or reporting-period date."
        ),
        "result_truncated_to_limit": (
            "The source result stopped at the requested limit and does not prove "
            "complete coverage of the window."
        ),
        "source_metric_is_provider_derived": (
            "The metric is a provider-derived market signal, not an issuer disclosure."
        ),
        "trading_day_alignment_unverified": (
            "The source did not independently prove trading-calendar alignment."
        ),
        "session_completeness_unverified": (
            "The provider exposes a trading date but does not prove that its current "
            "session snapshot was taken after market close."
        ),
    }.get(code, "The source observation carries this explicit limitation.")
