"""Coordinate bounded market-signal observations behind one public task."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Collection, Iterable

from .identity_resolution import resolve_security_identity
from .identity_sources import HttpTransport
from .market_signal_contract import (
    ATTRIBUTION_PROVENANCE,
    MARKET_SIGNAL_SOURCE_ROLES,
    MARKET_SIGNAL_TYPES,
    SIGNAL_COVERAGE_STATES,
    MarketSignalObservation,
    MarketSignalQuery,
    MarketSignalSourceOperation,
    ParameterAwareMarketSignalSourceOperation,
    SignalCoverage,
    SignalSourceFailure,
    ThemeAttribution,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
SUBJECT_SIGNAL_TYPES = frozenset({"security_board_membership"})
MARKET_WIDE_SIGNAL_TYPES = MARKET_SIGNAL_TYPES.difference(SUBJECT_SIGNAL_TYPES)
DERIVED_SIGNAL_TYPE = "monitoring_intersection"
DERIVATION_BASIS_TYPES = frozenset({"focus_monitoring", "severe_abnormal_movement"})
COORDINATOR_OPERATION_ID = "market_signal_coordinator@1"


def build_market_signals_result(
    request: dict[str, Any],
    operations: Collection[MarketSignalSourceOperation],
    identity_transport: HttpTransport,
) -> dict[str, Any]:
    """Collect, validate, reconcile, and derive stable market-signal evidence."""

    requested_types, query = _normalize_query(request)
    identity = _resolve_subject(request, requested_types, identity_transport)
    if identity["status"] == "blocked":
        return _blocked_identity_result(request, requested_types, identity)
    if identity["subjects"]:
        query = replace(query, subject=identity["subjects"][0])

    source_errors: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    batch_limitations: set[str] = set()
    accepted: list[MarketSignalObservation] = []
    coverage_by_type: dict[str, list[tuple[str, SignalCoverage]]] = {
        signal_type: [] for signal_type in query.signal_types
    }

    for operation in sorted(operations, key=lambda item: item.operation_id):
        relevant = operation.supported_signal_types.intersection(query.signal_types)
        if not relevant:
            continue
        operation_query = _operation_query(query, relevant, requested_types)
        if isinstance(
            operation, ParameterAwareMarketSignalSourceOperation
        ) and not operation.is_applicable(operation_query):
            continue
        batch = operation.collect(operation_query)
        coverage_errors = _validate_batch_coverage(
            operation.operation_id,
            batch.operation_id,
            batch.coverage,
            relevant,
            batch.observations,
        )
        source_errors.extend(_failure_results(coverage_errors, relevant))
        for signal_type, source_coverage in batch.coverage.items():
            if signal_type in relevant and _valid_coverage(source_coverage):
                coverage_by_type[signal_type].append(
                    (batch.operation_id, source_coverage)
                )

        valid, schema_errors = _validate_observations(
            batch.operation_id,
            batch.observations,
            operation_query,
        )
        accepted.extend(valid)
        source_errors.extend(_failure_results(schema_errors, relevant))
        source_errors.extend(_failure_results(batch.source_errors, relevant))
        degradations.extend(item.to_result() for item in batch.degradations)
        batch_limitations.update(batch.limitations)

    accepted = _deduplicate_observations(accepted)
    evidence_by_observation = {
        _observation_identity(item): _to_evidence(item) for item in accepted
    }
    derived, derivation_errors, derived_coverage, derivation_identity = (
        _derive_monitoring_intersections(
            requested_types,
            accepted,
            evidence_by_observation,
            coverage_by_type,
            source_errors,
            identity_transport,
            request["as_of"],
        )
    )
    source_errors.extend(_failure_results(derivation_errors, {DERIVED_SIGNAL_TYPE}))
    source_errors.extend(derivation_identity["source_errors"])
    accepted.extend(derived)
    if derived_coverage is not None:
        coverage_by_type[DERIVED_SIGNAL_TYPE] = [
            (COORDINATOR_OPERATION_ID, derived_coverage)
        ]

    accepted = _deduplicate_observations(accepted)
    evidence_by_observation = {
        _observation_identity(item): _to_evidence(item) for item in accepted
    }
    conflicts = [
        *derivation_identity["conflicts"],
        *_find_cross_source_conflicts(accepted, evidence_by_observation),
    ]

    public_observations = [
        item for item in accepted if item.signal_type in requested_types
    ]
    public_observations = _limit_per_type(public_observations, query.limit)
    observation_counts = _counts_by_type(public_observations)
    aggregate_coverage = _aggregate_coverage(
        requested_types,
        coverage_by_type,
        accepted,
        source_errors,
    )
    aggregates = _build_aggregates(
        requested_types,
        coverage_by_type,
        accepted,
        evidence_by_observation,
    )

    limitation_codes = batch_limitations | {
        code for item in accepted for code in item.limitations
    }
    limitations: list[dict[str, Any]] = [
        {
            "code": "experimental_market_signal_sources",
            "message": (
                "Market-signal observations use experimental source operations and "
                "describe market phenomena rather than issuer fundamentals."
            ),
        }
    ]
    if query.allow_fallback:
        limitations.append(
            {
                "code": "no_qualified_independent_fallback",
                "message": (
                    "Fallback use is permitted by policy, but no qualified "
                    "independent fallback is registered for the requested market "
                    "signals."
                ),
                "signal_types": list(requested_types),
            }
        )
    limitations.extend(
        {"code": code, "message": _limitation_message(code)}
        for code in sorted(limitation_codes)
    )
    incomplete_types = sorted(
        signal_type
        for signal_type, item in aggregate_coverage.items()
        if item["state"] in {"partial", "indeterminate"}
    )
    if incomplete_types:
        limitations.append(
            {
                "code": "market_signal_coverage_incomplete",
                "message": (
                    "At least one requested signal type lacks complete source "
                    "coverage; absence must not be interpreted as no signal."
                ),
                "signal_types": incomplete_types,
            }
        )
    if conflicts:
        limitations.append(
            {
                "code": "cross_source_signal_disagreement",
                "message": (
                    "Sources disagree on mutually exclusive market-signal fields; "
                    "all conflicting observations are retained."
                ),
            }
        )
    limitations.extend(identity["limitations"])
    limitations.extend(derivation_identity["limitations"])

    answered = any(
        item["state"] in {"observed_nonempty", "observed_empty"}
        or (item["state"] == "partial" and item["observation_count"] > 0)
        for item in aggregate_coverage.values()
    )
    status = "limited" if answered else "blocked"
    evidence = _deduplicate_evidence(
        [
            *identity["evidence"],
            *derivation_identity["evidence"],
            *evidence_by_observation.values(),
        ]
    )
    return {
        "schema_version": request["schema_version"],
        "status": status,
        "subjects": identity["subjects"],
        "observations": [_to_result(item) for item in public_observations],
        "brief": {
            "observation_count": len(public_observations),
            "signal_types": list(requested_types),
            "signal_type_counts": observation_counts,
            "coverage": aggregate_coverage,
            "aggregates": aggregates,
        },
        "evidence": evidence,
        "conflicts": [*identity["conflicts"], *conflicts],
        "source_errors": [*identity["source_errors"], *source_errors],
        "degradations": sorted(degradations, key=_json_sort_key),
        "limitations": limitations,
    }


def _operation_query(
    query: MarketSignalQuery,
    relevant_types: Collection[str],
    requested_types: tuple[str, ...],
) -> MarketSignalQuery:
    if DERIVED_SIGNAL_TYPE in requested_types and set(relevant_types) == {
        "focus_monitoring"
    }:
        return replace(
            query,
            observed_from=query.as_of,
            observed_to=query.as_of,
        )
    return query


def _normalize_query(
    request: dict[str, Any],
) -> tuple[tuple[str, ...], MarketSignalQuery]:
    parameters = request["parameters"]
    signal_types = parameters.get("signal_types")
    if (
        not isinstance(signal_types, list)
        or not signal_types
        or any(not isinstance(item, str) or not item for item in signal_types)
    ):
        raise ValueError(
            "market_signals parameters.signal_types must be a non-empty string array"
        )
    requested = tuple(dict.fromkeys(signal_types))
    unknown = sorted(set(requested).difference(MARKET_SIGNAL_TYPES))
    if unknown:
        raise ValueError(
            "market_signals has unsupported signal_types: " + ", ".join(unknown)
        )
    if set(requested).intersection(SUBJECT_SIGNAL_TYPES) and set(
        requested
    ).intersection(MARKET_WIDE_SIGNAL_TYPES):
        raise ValueError("market_signals cannot mix subject and market-wide scopes")

    subjects = request["subjects"]
    if set(requested).intersection(SUBJECT_SIGNAL_TYPES) and len(subjects) != 1:
        raise ValueError("the requested market_signals data requires one subject")
    if set(requested).issubset(MARKET_WIDE_SIGNAL_TYPES) and subjects:
        raise ValueError("market-wide market_signals data takes no subject")

    window = request["window"]
    if not isinstance(window, dict):
        raise ValueError("market_signals window must be an object")
    observed_from = _explicit_date(window.get("observed_from"), "observed_from")
    observed_to = _explicit_date(window.get("observed_to"), "observed_to")
    if observed_from > observed_to:
        raise ValueError("market_signals window starts after it ends")
    if observed_to > request["as_of"]:
        raise ValueError("market_signals window exceeds the research date")

    limit = parameters.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("market_signals parameters.limit must be from 1 to 100")

    collection_types = list(requested)
    if DERIVED_SIGNAL_TYPE in requested:
        for basis_type in sorted(DERIVATION_BASIS_TYPES):
            if basis_type not in collection_types:
                collection_types.append(basis_type)
    return requested, MarketSignalQuery(
        signal_types=tuple(collection_types),
        as_of=request["as_of"],
        observed_from=observed_from,
        observed_to=observed_to,
        limit=limit,
        subject=subjects[0] if subjects else None,
        parameters=dict(parameters),
        allow_fallback=request["source_policy"]["allow_fallback"],
    )


def _resolve_subject(
    request: dict[str, Any],
    requested_types: tuple[str, ...],
    transport: HttpTransport,
) -> dict[str, Any]:
    if not set(requested_types).intersection(SUBJECT_SIGNAL_TYPES):
        return {
            "status": "limited",
            "subjects": [],
            "evidence": [],
            "conflicts": [],
            "source_errors": [],
            "limitations": [],
        }
    subject = request["subjects"][0]
    clue = subject.get("clue") if isinstance(subject, dict) else None
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("market_signals subject requires a non-empty clue")
    resolved = resolve_security_identity(clue.strip(), request["as_of"], transport)
    candidates = resolved.get("candidates", [])
    if (
        resolved["status"] == "blocked"
        or not isinstance(candidates, list)
        or len(candidates) != 1
    ):
        return {
            "status": "blocked",
            "subjects": [],
            "evidence": resolved.get("evidence", []),
            "conflicts": resolved.get("conflicts", []),
            "source_errors": resolved.get("source_errors", []),
            "limitations": resolved.get("limitations", []),
        }
    candidate = candidates[0]
    security = candidate.get("security") if isinstance(candidate, dict) else None
    if not isinstance(security, dict):
        raise ValueError("identity result does not contain a canonical security")
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


def _validate_batch_coverage(
    operation_id: str,
    batch_operation_id: str,
    coverage: dict[str, SignalCoverage],
    relevant_types: Collection[str],
    observations: tuple[MarketSignalObservation, ...],
) -> list[SignalSourceFailure]:
    errors: list[SignalSourceFailure] = []
    if batch_operation_id != operation_id:
        errors.append(
            SignalSourceFailure(
                operation_id,
                "unknown_schema",
                "A source batch is outside the public market-signal contract.",
                {"invalid_fields": ["operation_id"]},
            )
        )
    for signal_type, item in coverage.items():
        invalid_fields: list[str] = []
        if signal_type not in relevant_types:
            invalid_fields.append("coverage.signal_type")
        if not _valid_coverage(item):
            invalid_fields.append("coverage")
        observation_count = len(
            {
                _observation_identity(observation)
                for observation in observations
                if observation.signal_type == signal_type
            }
        )
        if item.state == "observed_nonempty" and observation_count == 0:
            invalid_fields.append("coverage.observation_consistency")
        if item.state == "observed_empty" and observation_count != 0:
            invalid_fields.append("coverage.observation_consistency")
        if item.provider_total is not None and item.provider_total < observation_count:
            invalid_fields.append("coverage.provider_total")
        if invalid_fields:
            errors.append(
                SignalSourceFailure(
                    operation_id,
                    "unknown_schema",
                    "A source coverage item is outside the market-signal contract.",
                    {
                        "invalid_fields": sorted(set(invalid_fields)),
                        "signal_type": signal_type,
                    },
                )
            )
    missing = sorted(set(relevant_types).difference(coverage))
    if missing:
        errors.append(
            SignalSourceFailure(
                operation_id,
                "coverage_not_reported",
                "The source operation did not report coverage for supported signal types.",
                {"signal_types": missing},
            )
        )
    return errors


def _valid_coverage(item: object) -> bool:
    if not isinstance(item, SignalCoverage) or item.state not in SIGNAL_COVERAGE_STATES:
        return False
    counts = (item.provider_total, item.pages_collected, item.pages_expected)
    if any(
        isinstance(value, bool)
        or (value is not None and (not isinstance(value, int) or value < 0))
        for value in counts
    ):
        return False
    if item.state == "observed_empty" and item.provider_total not in {None, 0}:
        return False
    if (
        item.pages_collected is not None
        and item.pages_expected is not None
        and item.pages_collected > item.pages_expected
    ):
        return False
    return isinstance(item.details, dict)


def _validate_observations(
    operation_id: str,
    observations: tuple[MarketSignalObservation, ...],
    query: MarketSignalQuery,
) -> tuple[list[MarketSignalObservation], list[SignalSourceFailure]]:
    accepted: list[MarketSignalObservation] = []
    errors: list[SignalSourceFailure] = []
    for observation in observations:
        if any(
            attribution.provenance == "model_inference"
            for attribution in observation.attributions
        ):
            errors.append(
                SignalSourceFailure(
                    operation_id,
                    "adapter_model_inference_forbidden",
                    "A source Adapter cannot emit an attribution as model inference.",
                    {"signal_type": observation.signal_type},
                )
            )
            continue
        invalid_fields: list[str] = []
        if observation.signal_type not in MARKET_SIGNAL_TYPES:
            invalid_fields.append("signal_type")
        if observation.signal_type not in query.signal_types:
            invalid_fields.append("unrequested_signal_type")
        if observation.signal_type == DERIVED_SIGNAL_TYPE:
            invalid_fields.append("coordinator_derived_signal")
        if observation.source_operation != operation_id:
            invalid_fields.append("source_operation")
        if observation.source_role not in MARKET_SIGNAL_SOURCE_ROLES:
            invalid_fields.append("source_role")
        if observation.retrieved_at.utcoffset() is None:
            invalid_fields.append("retrieved_at")
        if _strict_date(observation.observed_on) is None:
            invalid_fields.append("observed_on")
        elif not query.observed_from <= observation.observed_on <= query.observed_to:
            invalid_fields.append("observed_on")
        if not _valid_observed_at(observation):
            invalid_fields.append("observed_at")
        if not _valid_period(observation.period):
            invalid_fields.append("period")
        if not _valid_metrics(observation):
            invalid_fields.append("metric_contract")
        if not _valid_rule(observation.rule):
            invalid_fields.append("rule")
        if (
            observation.signal_type == "severe_abnormal_movement"
            and observation.rule is None
        ):
            invalid_fields.append("rule")
        if not _valid_attributions(observation.attributions, operation_id):
            invalid_fields.append("attributions")
        if not isinstance(observation.dimensions, dict):
            invalid_fields.append("dimensions")
        else:
            market_scope = observation.dimensions.get("market_scope")
            if not isinstance(market_scope, str) or not market_scope:
                invalid_fields.append("market_scope")
        if not isinstance(observation.locator_uri, str) or not observation.locator_uri:
            invalid_fields.append("locator_uri")

        if query.subject is not None:
            if (
                observation.signal_type not in SUBJECT_SIGNAL_TYPES
                or observation.subject != query.subject
            ):
                invalid_fields.append("subject")
        elif (
            observation.subject is not None
            and _canonical_security_key(observation.subject) is None
        ):
            invalid_fields.append("subject")
        elif observation.subject is None and observation.dimensions.get(
            "provider_security_code"
        ):
            if "security_exchange_unverified" not in observation.limitations:
                invalid_fields.append("subject")

        availability_error = _availability_error(observation, query)
        if availability_error is not None:
            invalid_fields.append("available_at")
        if invalid_fields:
            errors.append(
                SignalSourceFailure(
                    operation_id,
                    "unknown_schema",
                    "A source observation is outside the public market-signal contract.",
                    {"invalid_fields": sorted(set(invalid_fields))},
                )
            )
            continue
        accepted.append(observation)
    return accepted, errors


def _valid_observed_at(observation: MarketSignalObservation) -> bool:
    if observation.observed_at is None:
        return True
    parsed = _aware_datetime(observation.observed_at)
    return parsed is not None and parsed.date().isoformat() == observation.observed_on


def _valid_period(period: object) -> bool:
    if not isinstance(period, dict):
        return False
    start = _strict_date(period.get("start"))
    end = _strict_date(period.get("end"))
    frequency = period.get("frequency")
    return bool(
        start is not None
        and end is not None
        and start <= end
        and isinstance(frequency, str)
        and frequency
    )


def _valid_metrics(observation: MarketSignalObservation) -> bool:
    if not isinstance(observation.metrics, dict) or not observation.metrics:
        return False
    keys = set(observation.metrics)
    if keys != set(observation.units) or keys != set(observation.directions):
        return False
    return bool(
        all(
            value is None or isinstance(value, str)
            for value in observation.metrics.values()
        )
        and all(
            isinstance(value, str) and value for value in observation.units.values()
        )
        and all(
            isinstance(value, str) and value
            for value in observation.directions.values()
        )
    )


def _valid_rule(rule: object) -> bool:
    if rule is None:
        return True
    if not isinstance(rule, dict) or not rule:
        return False
    rule_code = rule.get("code", rule.get("rule_code"))
    return isinstance(rule_code, str) and bool(rule_code)


def _valid_attributions(
    attributions: object,
    operation_id: str,
) -> bool:
    if not isinstance(attributions, tuple):
        return False
    for item in attributions:
        if not isinstance(item, ThemeAttribution):
            return False
        if not isinstance(item.text, str) or not item.text:
            return False
        if item.provenance not in ATTRIBUTION_PROVENANCE:
            return False
        if item.source_operation != operation_id:
            return False
        if item.source_document_id is not None and not isinstance(
            item.source_document_id, str
        ):
            return False
        if item.locator_uri is not None and not isinstance(item.locator_uri, str):
            return False
        if not all(
            isinstance(value, str) and value for value in item.basis_evidence_ids
        ):
            return False
        if len(set(item.basis_evidence_ids)) != len(item.basis_evidence_ids):
            return False
        if item.method_id is not None and (
            not isinstance(item.method_id, str) or not item.method_id
        ):
            return False
        if item.provenance == "editorial_annotation" and (
            not item.source_document_id or not item.locator_uri
        ):
            return False
        if item.provenance == "market_signal" and not (
            (item.source_document_id and item.locator_uri)
            or (item.method_id and item.basis_evidence_ids)
        ):
            return False
        if item.provenance == "model_inference" and (
            not item.method_id or not item.basis_evidence_ids
        ):
            return False
    return True


def _availability_error(
    observation: MarketSignalObservation,
    query: MarketSignalQuery,
) -> str | None:
    if observation.available_at is None:
        if "availability_time_unknown" not in observation.limitations:
            return "unknown_schema"
        retrieved_on = (
            observation.retrieved_at.astimezone(CHINA_STANDARD_TIME).date().isoformat()
        )
        return None if retrieved_on == query.as_of else "unknown_schema"
    available_at = _aware_datetime(observation.available_at)
    if available_at is None:
        return "unknown_schema"
    research_boundary = datetime.combine(
        date.fromisoformat(query.as_of), time.max, tzinfo=CHINA_STANDARD_TIME
    )
    if available_at > observation.retrieved_at or available_at > research_boundary:
        return "unknown_schema"
    return None


def _derive_monitoring_intersections(
    requested_types: tuple[str, ...],
    observations: list[MarketSignalObservation],
    evidence_by_observation: dict[str, dict[str, Any]],
    coverage_by_type: dict[str, list[tuple[str, SignalCoverage]]],
    source_errors: list[dict[str, Any]],
    identity_transport: HttpTransport,
    as_of: str,
) -> tuple[
    list[MarketSignalObservation],
    list[SignalSourceFailure],
    SignalCoverage | None,
    dict[str, list[dict[str, Any]]],
]:
    identity_context: dict[str, list[dict[str, Any]]] = {
        "evidence": [],
        "conflicts": [],
        "source_errors": [],
        "limitations": [],
    }
    if DERIVED_SIGNAL_TYPE not in requested_types:
        return [], [], None, identity_context
    focus = [item for item in observations if item.signal_type == "focus_monitoring"]
    severe = [
        item for item in observations if item.signal_type == "severe_abnormal_movement"
    ]
    derived: list[MarketSignalObservation] = []
    errors: list[SignalSourceFailure] = []
    identity_cache: dict[str, tuple[dict[str, Any] | None, tuple[str, ...]]] = {}

    for focus_item in focus:
        for severe_item in severe:
            focus_key = _canonical_security_key(focus_item.subject)
            severe_key = _canonical_security_key(severe_item.subject)
            if focus_key is None or severe_key is None:
                if _unverified_provider_match(focus_item, severe_item):
                    provider_code = str(focus_item.dimensions["provider_security_code"])
                    if provider_code not in identity_cache:
                        subject, resolved = _resolve_intersection_subject(
                            provider_code,
                            as_of,
                            identity_transport,
                        )
                        identity_cache[provider_code] = (
                            subject,
                            tuple(
                                item["id"]
                                for item in resolved.get("evidence", [])
                                if isinstance(item, dict)
                                and isinstance(item.get("id"), str)
                            ),
                        )
                        _merge_identity_context(identity_context, resolved)
                    subject, identity_evidence_ids = identity_cache[provider_code]
                    if subject is None:
                        errors.append(
                            SignalSourceFailure(
                                COORDINATOR_OPERATION_ID,
                                "identity_unverified",
                                "Provider security codes could not be resolved to one canonical A-share identity.",
                                {
                                    "basis_source_operations": sorted(
                                        {
                                            focus_item.source_operation,
                                            severe_item.source_operation,
                                        }
                                    ),
                                    "provider_security_code": provider_code,
                                },
                            )
                        )
                        continue
                    focus_evidence = evidence_by_observation[
                        _observation_identity(focus_item)
                    ]
                    severe_evidence = evidence_by_observation[
                        _observation_identity(severe_item)
                    ]
                    resolved_limitations = tuple(
                        code
                        for code in focus_item.limitations
                        if code
                        not in {
                            "security_exchange_unverified",
                            "security_type_unverified",
                        }
                    )
                    canonical_focus = replace(
                        focus_item,
                        subject=subject,
                        limitations=resolved_limitations,
                    )
                    canonical_severe = replace(
                        severe_item,
                        subject=subject,
                        limitations=tuple(
                            code
                            for code in severe_item.limitations
                            if code
                            not in {
                                "security_exchange_unverified",
                                "security_type_unverified",
                            }
                        ),
                    )
                    derived.append(
                        _intersection_observation(
                            canonical_focus,
                            canonical_severe,
                            (
                                focus_evidence["id"],
                                severe_evidence["id"],
                                *identity_evidence_ids,
                            ),
                        )
                    )
                continue
            if focus_key != severe_key or not _monitoring_window_contains(
                focus_item, severe_item.observed_on
            ):
                continue
            focus_evidence = evidence_by_observation[_observation_identity(focus_item)]
            severe_evidence = evidence_by_observation[
                _observation_identity(severe_item)
            ]
            derived.append(
                _intersection_observation(
                    focus_item,
                    severe_item,
                    (focus_evidence["id"], severe_evidence["id"]),
                )
            )

    derived = _deduplicate_observations(derived)
    basis_by_type = {
        signal_type: [item.state for _, item in coverage_by_type.get(signal_type, [])]
        for signal_type in DERIVATION_BASIS_TYPES
    }
    both_complete = all(
        states
        and all(state in {"observed_nonempty", "observed_empty"} for state in states)
        for states in basis_by_type.values()
    )
    basis_has_errors = any(
        DERIVATION_BASIS_TYPES.intersection(
            item.get("signal_types", [item.get("signal_type")])
        )
        for item in source_errors
    )
    derivation_is_complete = bool(
        both_complete
        and not basis_has_errors
        and not errors
        and not identity_context["source_errors"]
        and not identity_context["conflicts"]
    )
    if derived:
        coverage = SignalCoverage(
            state="observed_nonempty" if derivation_is_complete else "partial",
            provider_total=len(derived) if derivation_is_complete else None,
        )
    elif errors:
        coverage = SignalCoverage(
            state="indeterminate", details={"code": "identity_unverified"}
        )
    else:
        if derivation_is_complete:
            coverage = SignalCoverage(state="observed_empty", provider_total=0)
        elif all(basis_by_type.values()) and (
            basis_has_errors
            or any("partial" in states for states in basis_by_type.values())
        ):
            coverage = SignalCoverage(state="partial")
        else:
            coverage = SignalCoverage(state="indeterminate")
    return derived, _deduplicate_failures(errors), coverage, identity_context


def _intersection_observation(
    focus: MarketSignalObservation,
    severe: MarketSignalObservation,
    basis_evidence_ids: tuple[str, ...],
) -> MarketSignalObservation:
    available_values = [
        parsed
        for parsed in (
            _aware_datetime(focus.available_at),
            _aware_datetime(severe.available_at),
        )
        if parsed is not None
    ]
    available_at = (
        max(available_values).isoformat() if len(available_values) == 2 else None
    )
    limitations = set(focus.limitations).union(severe.limitations)
    limitations.add("coordinator_derived_market_signal")
    if available_at is None:
        limitations.add("availability_time_unknown")
    subject = severe.subject if severe.subject is not None else focus.subject
    assert subject is not None
    return MarketSignalObservation(
        signal_type=DERIVED_SIGNAL_TYPE,
        source_operation=COORDINATOR_OPERATION_ID,
        source_role="market_signal",
        subject=subject,
        source_document_id=None,
        observed_on=severe.observed_on,
        observed_at=severe.observed_at,
        available_at=available_at,
        retrieved_at=max(focus.retrieved_at, severe.retrieved_at),
        period={
            "start": severe.observed_on,
            "end": severe.observed_on,
            "frequency": "trading_day",
        },
        metrics={"basis_count": "2"},
        units={"basis_count": "count"},
        directions={"basis_count": "descriptive"},
        rule=severe.rule,
        attributions=(
            ThemeAttribution(
                text="重点监控与严重异常波动交叉",
                provenance="market_signal",
                source_operation=COORDINATOR_OPERATION_ID,
                source_document_id=None,
                locator_uri="urn:a-share-research:monitoring-intersection",
                basis_evidence_ids=basis_evidence_ids,
                method_id="deterministic_monitoring_intersection@1",
            ),
        ),
        dimensions={
            "market_scope": severe.dimensions["market_scope"],
            "pool_state": "focus_monitoring_and_severe_abnormal_movement",
            "derivation_method": "canonical_security_and_monitoring_window_overlap",
        },
        locator_uri="urn:a-share-research:monitoring-intersection",
        limitations=tuple(sorted(limitations)),
    )


def _monitoring_window_contains(
    focus: MarketSignalObservation, observed_on: str
) -> bool:
    start = (
        focus.dimensions.get("monitoring_window_start")
        or focus.dimensions.get("monitoring_start")
        or focus.period.get("start")
    )
    end = (
        focus.dimensions.get("monitoring_window_end")
        or focus.dimensions.get("monitoring_end")
        or focus.period.get("end")
    )
    parsed_start = _strict_date(start)
    parsed_end = _strict_date(end)
    return bool(
        parsed_start is not None
        and parsed_end is not None
        and parsed_start <= observed_on <= parsed_end
    )


def _unverified_provider_match(
    first: MarketSignalObservation, second: MarketSignalObservation
) -> bool:
    first_code = first.dimensions.get("provider_security_code")
    second_code = second.dimensions.get("provider_security_code")
    first_market = first.dimensions.get(
        "provider_market_code", first.dimensions.get("provider_market")
    )
    second_market = second.dimensions.get(
        "provider_market_code", second.dimensions.get("provider_market")
    )
    markets_match = (first_market is None and second_market is None) or (
        isinstance(first_market, (str, int))
        and isinstance(second_market, (str, int))
        and str(first_market) == str(second_market)
    )
    return bool(
        first.subject is None
        and second.subject is None
        and isinstance(first_code, str)
        and first_code
        and first_code == second_code
        and markets_match
        and _monitoring_window_contains(first, second.observed_on)
    )


def _resolve_intersection_subject(
    provider_code: str,
    as_of: str,
    transport: HttpTransport,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    resolved = resolve_security_identity(provider_code, as_of, transport)
    candidates = resolved.get("candidates")
    if (
        resolved.get("status") == "blocked"
        or not isinstance(candidates, list)
        or len(candidates) != 1
    ):
        return None, resolved
    candidate = candidates[0]
    security = candidate.get("security") if isinstance(candidate, dict) else None
    if not isinstance(security, dict) or security.get("type") != "A_SHARE":
        return None, resolved
    exchange = security.get("exchange")
    code = security.get("code")
    if exchange not in {"SSE", "SZSE"} or code != provider_code:
        return None, resolved
    return (
        {
            "security": {
                "exchange": exchange,
                "code": code,
                "type": "A_SHARE",
            },
            "name": candidate.get("name"),
            "issuer": candidate.get("issuer"),
        },
        resolved,
    )


def _merge_identity_context(
    context: dict[str, list[dict[str, Any]]],
    resolved: dict[str, Any],
) -> None:
    for key in ("evidence", "conflicts", "source_errors", "limitations"):
        values = resolved.get(key)
        if isinstance(values, list):
            context[key].extend(item for item in values if isinstance(item, dict))


def _deduplicate_evidence(
    evidence: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for item in evidence:
        identity = item.get("id")
        key = identity if isinstance(identity, str) else _json_value(item)
        selected.setdefault(key, item)
    return [selected[key] for key in sorted(selected)]


def _aggregate_coverage(
    requested_types: tuple[str, ...],
    coverage_by_type: dict[str, list[tuple[str, SignalCoverage]]],
    observations: list[MarketSignalObservation],
    source_errors: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for signal_type in requested_types:
        source_items = sorted(
            coverage_by_type.get(signal_type, []), key=lambda item: item[0]
        )
        states = [item.state for _, item in source_items]
        count = sum(1 for item in observations if item.signal_type == signal_type)
        has_errors = any(
            signal_type in item.get("signal_types", [item.get("signal_type")])
            for item in source_errors
        )
        if count:
            state = (
                "partial"
                if not states
                or any(value in {"partial", "indeterminate"} for value in states)
                or has_errors
                else "observed_nonempty"
            )
        elif has_errors:
            state = (
                "partial"
                if any(
                    value in {"observed_nonempty", "observed_empty", "partial"}
                    for value in states
                )
                else "indeterminate"
            )
        elif "partial" in states:
            state = "partial"
        elif "observed_empty" in states and not any(
            value in {"indeterminate", "observed_nonempty"} for value in states
        ):
            state = "observed_empty"
        elif states.count("observed_empty") and "indeterminate" in states:
            state = "partial"
        else:
            state = "indeterminate"
        result[signal_type] = {
            "state": state,
            "observation_count": count,
            "sources": [
                {"source_operation": operation_id, **coverage.to_result()}
                for operation_id, coverage in source_items
            ],
        }
    return result


def _failure_results(
    failures: Iterable[SignalSourceFailure],
    signal_types: Collection[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for failure in failures:
        result = failure.to_result()
        if "signal_type" not in result and "signal_types" not in result:
            result["signal_types"] = sorted(signal_types)
        results.append(result)
    return results


def _build_aggregates(
    requested_types: tuple[str, ...],
    coverage_by_type: dict[str, list[tuple[str, SignalCoverage]]],
    observations: list[MarketSignalObservation],
    evidence_by_observation: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if "limit_state" not in requested_types:
        return {}
    required_states = {"limit_up", "limit_break", "limit_down"}
    candidates: list[str] = []
    for operation_id, coverage_item in coverage_by_type.get("limit_state", []):
        pool_states = coverage_item.details.get("pool_states")
        if (
            coverage_item.state in {"observed_nonempty", "observed_empty"}
            and isinstance(pool_states, list)
            and required_states.issubset(pool_states)
        ):
            candidates.append(operation_id)
    if not candidates:
        return {}
    operation_id = sorted(candidates)[0]
    basis = [
        item
        for item in observations
        if item.signal_type == "limit_state"
        and item.source_operation == operation_id
        and item.dimensions.get("pool_state") in required_states
    ]
    counts = {
        state: sum(1 for item in basis if item.dimensions.get("pool_state") == state)
        for state in sorted(required_states)
    }
    ladder: dict[str, int] = {}
    for observation in basis:
        if observation.dimensions.get("pool_state") != "limit_up":
            continue
        raw_height = observation.metrics.get("consecutive_limit_days")
        if raw_height is None:
            return {}
        try:
            height = int(raw_height)
        except ValueError:
            return {}
        if height < 1 or str(height) != raw_height:
            return {}
        ladder[str(height)] = ladder.get(str(height), 0) + 1
    denominator = counts["limit_up"] + counts["limit_break"]
    break_rate = (
        str((Decimal(counts["limit_break"]) / Decimal(denominator)) * Decimal(100))
        if denominator
        else None
    )
    maximum = max((int(value) for value in ladder), default=None)
    basis_evidence_ids = sorted(
        evidence_by_observation[_observation_identity(item)]["id"] for item in basis
    )
    return {
        "limit_state_sentiment": {
            "source_operation": operation_id,
            "limit_up_count": str(counts["limit_up"]),
            "limit_break_count": str(counts["limit_break"]),
            "limit_down_count": str(counts["limit_down"]),
            "break_rate": break_rate,
            "max_consecutive_limit_days": (
                str(maximum) if maximum is not None else None
            ),
            "consecutive_limit_ladder": {
                key: str(ladder[key]) for key in sorted(ladder, key=int)
            },
            "formula": "(limit_break/(limit_up+limit_break))*100",
            "units": {
                "limit_up_count": "security_count",
                "limit_break_count": "security_count",
                "limit_down_count": "security_count",
                "break_rate": "percent",
                "max_consecutive_limit_days": "trading_day_count",
                "consecutive_limit_ladder": (
                    "security_count_by_consecutive_trading_day"
                ),
            },
            "basis_evidence_ids": basis_evidence_ids,
        }
    }


def _find_cross_source_conflicts(
    observations: list[MarketSignalObservation],
    evidence_by_observation: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[MarketSignalObservation]] = {}
    for item in observations:
        entity = _entity_key(item)
        if entity is None:
            continue
        grouped.setdefault((item.signal_type, item.observed_on, entity), []).append(
            item
        )

    conflicts: list[dict[str, Any]] = []
    for (signal_type, observed_on, entity), items in sorted(grouped.items()):
        if len({item.source_operation for item in items}) < 2:
            continue
        fields: dict[str, set[str]] = {
            "pool_state": _mutually_exclusive_pool_states(items),
            "rule": {_json_value(item.rule) for item in items if item.rule is not None},
            "monitoring_window": {
                _json_value(_monitoring_window(item))
                for item in items
                if _monitoring_window(item) is not None
            },
        }
        disagreeing = sorted(
            field for field, values in fields.items() if len(values) > 1
        )
        if not disagreeing:
            continue
        conflicts.append(
            {
                "code": "cross_source_signal_disagreement",
                "message": "Sources disagree on mutually exclusive market-signal fields.",
                "signal_type": signal_type,
                "observed_on": observed_on,
                "entity": entity,
                "fields": disagreeing,
                "evidence_ids": sorted(
                    evidence_by_observation[_observation_identity(item)]["id"]
                    for item in items
                ),
            }
        )
    return conflicts


def _mutually_exclusive_pool_states(
    observations: list[MarketSignalObservation],
) -> set[str]:
    current_states = {"limit_up", "limit_break", "limit_down"}
    return {
        value
        for item in observations
        if isinstance((value := item.dimensions.get("pool_state")), str)
        and value in current_states
    }


def _monitoring_window(item: MarketSignalObservation) -> dict[str, Any] | None:
    start = item.dimensions.get(
        "monitoring_window_start", item.dimensions.get("monitoring_start")
    )
    end = item.dimensions.get(
        "monitoring_window_end", item.dimensions.get("monitoring_end")
    )
    if start is None and end is None:
        return None
    return {"start": start, "end": end}


def _deduplicate_observations(
    observations: Iterable[MarketSignalObservation],
) -> list[MarketSignalObservation]:
    selected: dict[str, MarketSignalObservation] = {}
    for item in observations:
        selected.setdefault(_observation_identity(item), item)
    return sorted(selected.values(), key=_observation_sort_key)


def _deduplicate_failures(
    failures: Iterable[SignalSourceFailure],
) -> list[SignalSourceFailure]:
    selected: dict[str, SignalSourceFailure] = {}
    for item in failures:
        key = _json_value(item.to_result())
        selected.setdefault(key, item)
    return [selected[key] for key in sorted(selected)]


def _limit_per_type(
    observations: list[MarketSignalObservation], limit: int
) -> list[MarketSignalObservation]:
    counts: dict[str, int] = {}
    selected: list[MarketSignalObservation] = []
    for item in observations:
        if counts.get(item.signal_type, 0) >= limit:
            continue
        counts[item.signal_type] = counts.get(item.signal_type, 0) + 1
        selected.append(item)
    return selected


def _counts_by_type(
    observations: list[MarketSignalObservation],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in observations:
        counts[item.signal_type] = counts.get(item.signal_type, 0) + 1
    return counts


def _observation_identity(observation: MarketSignalObservation) -> str:
    serialized = _json_value(_to_result(observation, include_claim_eligibility=False))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _observation_sort_key(observation: MarketSignalObservation) -> tuple[str, ...]:
    return (
        observation.signal_type,
        observation.observed_on,
        _entity_key(observation) or "",
        observation.source_operation,
        observation.source_document_id or "",
        _observation_identity(observation),
    )


def _to_result(
    observation: MarketSignalObservation,
    *,
    include_claim_eligibility: bool = True,
) -> dict[str, Any]:
    result = {
        "signal_type": observation.signal_type,
        "source_operation": observation.source_operation,
        "source_role": observation.source_role,
        "subject": observation.subject,
        "source_document_id": observation.source_document_id,
        "observed_on": observation.observed_on,
        "observed_at": observation.observed_at,
        "available_at": observation.available_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "period": observation.period,
        "metrics": observation.metrics,
        "units": observation.units,
        "directions": observation.directions,
        "rule": observation.rule,
        "attributions": [
            _attribution_result(item) for item in observation.attributions
        ],
        "dimensions": observation.dimensions,
        "locator": {"uri": observation.locator_uri},
        "limitations": list(observation.limitations),
    }
    if include_claim_eligibility:
        result["claim_eligibility"] = "experimental_observation_only"
    return result


def _attribution_result(item: ThemeAttribution) -> dict[str, Any]:
    return {
        "text": item.text,
        "provenance": item.provenance,
        "source_operation": item.source_operation,
        "source_document_id": item.source_document_id,
        "locator": {"uri": item.locator_uri} if item.locator_uri else None,
        "basis_evidence_ids": list(item.basis_evidence_ids),
        "method_id": item.method_id,
    }


def _to_evidence(observation: MarketSignalObservation) -> dict[str, Any]:
    identity = _observation_identity(observation)
    return {
        "id": f"market-signal-{identity}",
        "source_role": observation.source_role,
        "source_operation": observation.source_operation,
        "source_document_id": observation.source_document_id,
        "experimental": True,
        "subject": observation.subject,
        "observation": {
            "kind": observation.signal_type,
            "observed_on": observation.observed_on,
            "observed_at": observation.observed_at,
            "period": observation.period,
            "metrics": observation.metrics,
            "units": observation.units,
            "directions": observation.directions,
            "rule": observation.rule,
            "attributions": [
                _attribution_result(item) for item in observation.attributions
            ],
            "dimensions": observation.dimensions,
        },
        "evidence_time": observation.observed_at or observation.observed_on,
        "available_at": observation.available_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "locator": {"uri": observation.locator_uri},
        "limitations": list(observation.limitations),
    }


def _canonical_security_key(subject: object) -> str | None:
    if not isinstance(subject, dict):
        return None
    security = subject.get("security")
    if not isinstance(security, dict):
        return None
    exchange = security.get("exchange")
    code = security.get("code")
    security_type = security.get("type")
    if exchange not in {"SSE", "SZSE", "BSE"}:
        return None
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return None
    if security_type != "A_SHARE":
        return None
    return f"{exchange}:{code}:{security_type}"


def _entity_key(observation: MarketSignalObservation) -> str | None:
    canonical = _canonical_security_key(observation.subject)
    if canonical is not None:
        return canonical
    provider_code = observation.dimensions.get("provider_security_code")
    if isinstance(provider_code, str) and provider_code:
        return f"provider:{provider_code}"
    market_scope = observation.dimensions.get("market_scope")
    if isinstance(market_scope, str) and market_scope:
        return f"market:{market_scope}"
    return None


def _blocked_identity_result(
    request: dict[str, Any],
    requested_types: tuple[str, ...],
    identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": [],
        "observations": [],
        "brief": {
            "observation_count": 0,
            "signal_types": list(requested_types),
            "signal_type_counts": {},
            "coverage": {
                signal_type: {
                    "state": "indeterminate",
                    "observation_count": 0,
                    "sources": [],
                }
                for signal_type in requested_types
            },
            "aggregates": {},
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
        raise ValueError(f"market_signals {field} must use YYYY-MM-DD")
    return parsed


def _strict_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _json_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _json_sort_key(value: dict[str, Any]) -> str:
    return _json_value(value)


def _limitation_message(code: str) -> str:
    return {
        "availability_time_unknown": "The source does not expose when this signal first became available.",
        "security_exchange_unverified": "The source exposes a provider security code but not a canonical exchange-qualified identity.",
        "coordinator_derived_market_signal": "The signal was deterministically derived by the coordinator from cited basis evidence.",
        "pagination_incomplete": "The source did not prove complete coverage of the requested pool.",
        "observation_time_precision_is_date_only": "The source exposes a trading date but not an intraday observation time.",
    }.get(code, "The source observation carries this explicit limitation.")
