"""Coordinate bounded market-signal observations behind one public task."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Collection, Iterable, cast

from .identity_resolution import (
    execute_security_identity_research,
)
from .identity_sources import HttpTransport, build_default_identity_operations
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
    market_signal_coverage_authority,
    market_signal_coverage_field_destination,
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
    safe_public_locator,
    stable_result_id,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
_MARKET_SIGNAL_CAPABILITY = "market_signal_observations"
_MARKET_SIGNAL_EVIDENCE_USE = "market_signal_observation"


def build_market_signals_blocked_result(
    request: dict[str, Any],
    operations: Collection[MarketSignalSourceOperation],
) -> UnifiedResearchResult:
    """Return the formal result when policy prevents signal acquisition."""

    requested, query = _normalize_query(request)
    del requested
    operation_list = tuple(operations)
    _validate_market_signal_operations(operation_list)
    applicable = {
        operation.operation_id
        for operation in operation_list
        if operation.supported_signal_types.intersection(query.signal_types)
        and (
            not isinstance(operation, ParameterAwareMarketSignalSourceOperation)
            or operation.is_applicable(query)
        )
    }
    if not applicable:
        raise ValueError("market_signals has no applicable source operation")
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="market_signals",
            as_of=str(request["as_of"]),
            obligations=tuple(
                _market_signal_obligation(item) for item in operation_list
            ),
            dependencies=(
                ResearchDependency(
                    "market_signal_observation",
                    _MARKET_SIGNAL_EVIDENCE_USE,
                    1,
                    True,
                ),
            ),
        ),
        tuple(
            OperationOutcome(
                capability_id=_MARKET_SIGNAL_CAPABILITY,
                operation_id=operation.operation_id,
                execution=OperationExecution(
                    "skipped_policy"
                    if operation.operation_id in applicable
                    else "not_requested"
                ),
                field_dispositions=_market_signal_dispositions(operation),
            )
            for operation in operation_list
        ),
        limitations=(
            DirectTaskLimitation(
                "source_policy_not_satisfied",
                "market_signals currently requires experimental source operations",
            ),
        ),
        summary="Market-signal acquisition was blocked by source policy.",
    )


def _market_signal_obligation(
    operation: MarketSignalSourceOperation,
) -> OperationObligation:
    return OperationObligation(
        capability_id=_MARKET_SIGNAL_CAPABILITY,
        operation_id=operation.operation_id,
        source_role="market_signal",
        experimental=True,
        exchange=None,
        evidence_uses=(EvidenceUsePolicy(_MARKET_SIGNAL_EVIDENCE_USE, "supporting"),),
    )


def _sanitize_identity_outcomes(
    outcomes: tuple[OperationOutcome, ...],
) -> tuple[OperationOutcome, ...]:
    sanitized: list[OperationOutcome] = []
    for outcome in outcomes:
        observations: list[dict[str, object]] = []
        for index, payload in enumerate(outcome.to_dict()["observations"]):
            copied = json.loads(json.dumps(payload, ensure_ascii=False))
            if not isinstance(copied, dict):
                raise ValueError("identity observation must be a JSON object")
            locator = copied.get("locator")
            if isinstance(locator, dict) and "uri" in locator:
                locator["uri"] = safe_public_locator(
                    locator.get("uri"),
                    "identity:"
                    + hashlib.sha256(outcome.operation_id.encode()).hexdigest()[:12]
                    + f":{index}",
                )
            observations.append(copied)
        sanitized.append(
            OperationOutcome(
                outcome.capability_id,
                outcome.operation_id,
                outcome.execution,
                tuple(observations),
                outcome.source_errors,
                outcome.field_dispositions,
                outcome.diagnostics,
            )
        )
    return tuple(sanitized)


def _namespace_identity_observations(
    outcomes: tuple[OperationOutcome, ...],
    namespace: str,
) -> tuple[tuple[OperationOutcome, ...], dict[str, str]]:
    namespaced: list[OperationOutcome] = []
    key_map: dict[str, str] = {}
    for outcome in outcomes:
        observations: list[dict[str, object]] = []
        for index, payload in enumerate(outcome.to_dict()["observations"]):
            copied = json.loads(json.dumps(payload, ensure_ascii=False))
            if not isinstance(copied, dict):
                raise ValueError("identity observation must be a JSON object")
            local_key = copied.get("id")
            old_key = (
                local_key
                if isinstance(local_key, str) and local_key
                else f"{outcome.operation_id}:{index}"
            )
            new_key = f"{old_key}:monitoring:{namespace}"
            if old_key in key_map:
                raise ValueError("identity observation keys must be unique")
            key_map[old_key] = new_key
            copied["id"] = new_key
            observations.append(copied)
        namespaced.append(
            OperationOutcome(
                outcome.capability_id,
                outcome.operation_id,
                outcome.execution,
                tuple(observations),
                outcome.source_errors,
                outcome.field_dispositions,
                outcome.diagnostics,
            )
        )
    return tuple(namespaced), key_map


def _validate_market_signal_operations(
    operations: tuple[MarketSignalSourceOperation, ...],
) -> None:
    operation_ids = [item.operation_id for item in operations]
    if not operation_ids or len(operation_ids) != len(set(operation_ids)):
        raise ValueError("market-signal operation IDs must be non-empty and unique")
    for operation in operations:
        _market_signal_dispositions(operation)


def _market_signal_dispositions(
    operation: MarketSignalSourceOperation,
) -> tuple[FieldDisposition, ...]:
    raw_fields = getattr(operation, "raw_fields", None)
    if (
        not isinstance(raw_fields, tuple)
        or not raw_fields
        or any(not isinstance(item, str) or not item for item in raw_fields)
        or len(raw_fields) != len(set(raw_fields))
    ):
        raise ValueError(
            "market-signal operation raw_fields must be a unique non-empty tuple"
        )
    return tuple(
        _market_signal_field_disposition(operation.operation_id, item)
        for item in raw_fields
    )


def _market_signal_field_disposition(
    operation_id: str,
    field: str,
) -> FieldDisposition:
    coverage_destination = market_signal_coverage_field_destination(operation_id, field)
    if coverage_destination is not None:
        return FieldDisposition(field, "cross_check_only", coverage_destination)
    if field in {
        "data[]",
        "data.diff[]",
        "data.pool[]",
        "data.info[]",
        "data.stock_list[]",
        "result.data[]",
        "response[]",
    }:
        return FieldDisposition(
            field,
            "excluded",
            None,
            "Provider collection containers are not retained in public output.",
        )
    if field in {
        "errocode",
        "status_code",
        "data.total",
        "data.count",
        "data.tc",
        "result",
        "pages",
        "count",
    }:
        return FieldDisposition(
            field,
            "cross_check_only",
            f"coverage.{_MARKET_SIGNAL_CAPABILITY}.operations.{operation_id}.query_complete",
        )
    identity_dimensions = {
        "code": "provider_security_code",
        "name": "provider_security_name",
        "c": "provider_security_code",
        "n": "provider_security_name",
        "STKCODE": "provider_security_code",
        "STKNAME": "provider_security_name",
        "market": "provider_market",
        "m": "provider_market_code",
        "MARKET": "provider_market_code",
    }
    identity_dimension = identity_dimensions.get(field)
    if identity_dimension is not None:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.dimensions",
        )
    if field == "data.secid":
        return FieldDisposition(
            field, "cross_check_only", "observations[].payload.subject"
        )
    if field in {"f12", "f14"}:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.dimensions."
            + ("board_code" if field == "f12" else "board_name"),
        )
    if field == "LINK_URL":
        return FieldDisposition(
            field,
            "lineage_only",
            "observations[].payload.dimensions.provider_detail_url",
        )
    if field in {"reason", "reason_type", "tag.concept_tag"}:
        return FieldDisposition(
            field, "normalized", "observations[].payload.attributions"
        )
    metric_destinations = {
        "zhangfu": "change_rate",
        "close": "close_price",
        "huanshou": "turnover_rate",
        "chengjiaoe": "trading_amount",
        "f104": "up_count",
        "f105": "down_count",
        "f136": "leader_change_rate",
        "p": "price",
        "zdp": "change_rate",
        "hs": "turnover_rate",
        "amount": "amount",
        "ltsz": "float_market_cap",
        "fund": "seal_fund",
        "ztp": "limit_price",
        "zf": "amplitude",
        "zs": "speed",
        "pe": "pe",
        "fba": "board_amount",
        "lbc": "consecutive_limit_days",
        "zbc": "break_times",
        "days": "consecutive_limit_down_days",
        "oc": "open_times",
        "ylbc": "previous_consecutive_limit_days",
        "latest": "price",
        "change_rate": "change_rate",
        "limit_up_suc_rate": "seal_success_rate",
        "open_num": "break_times",
        "order_amount": "seal_fund",
        "a": "change_rate",
        "x": "cumulative_deviation",
        "t": "target_change_rate",
        "rate": "heat",
        "rise_and_fall": "change_rate",
        "hot_rank_chg": "rank_change",
        "order": "rank",
    }
    if field == "f3":
        metric_destinations[field] = "provider_change_rate"
    metric = metric_destinations.get(field)
    if metric is not None:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.metrics",
        )
    dimension_destinations = {
        "f128": "lead_stock",
        "f140": "leader",
        "hybk": "industry_label",
        "fbt": "first_seal_time",
        "lbt": "last_seal_time",
        "yfbt": "previous_first_seal_time",
        "zttj.days": "statistic_window_days",
        "zttj.ct": "statistic_limit_count",
        "first_limit_up_time": "first_seal_time",
        "limit_up_type": "board_type",
        "high_days": "high_days_label",
        "is_again_limit": "provider_is_again_limit",
        "tag.popularity_tag": "popularity_tag",
        "open": "provider_market_open_code",
        "s": "provider_board_code",
        "o": "provider_occurrence_state_code",
        "d": "statistics_trading_days",
    }
    dimension = dimension_destinations.get(field)
    if dimension is not None:
        return FieldDisposition(
            field,
            "normalized",
            f"observations[].payload.dimensions.{dimension}",
        )
    if field in {"f124", "date"}:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.observed_on",
        )
    if field in {"VALIDATESTARTDATE", "VALIDATEENDDATE"}:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.period."
            + ("start" if field == "VALIDATESTARTDATE" else "end"),
        )
    if field == "e":
        return FieldDisposition(field, "normalized", "observations[].payload.rule.code")
    raise ValueError(f"market-signal operation has unknown raw field(s): {field}")


def _market_signal_skipped_outcome(
    operation: MarketSignalSourceOperation,
    state: str,
) -> OperationOutcome:
    return OperationOutcome(
        capability_id=_MARKET_SIGNAL_CAPABILITY,
        operation_id=operation.operation_id,
        execution=OperationExecution(state),  # type: ignore[arg-type]
        field_dispositions=_market_signal_dispositions(operation),
    )


def _market_signal_failed_outcome(
    operation: MarketSignalSourceOperation,
    failure: SignalSourceFailure,
    *,
    diagnostics: dict[str, object] | None = None,
) -> OperationOutcome:
    return _market_signal_outcome(
        operation,
        (),
        (failure,),
        False,
        diagnostics,
    )


def _market_signal_public_coverage(
    operation_id: str,
    signal_type: str,
    coverage: SignalCoverage,
    *,
    observation_count: int,
    source_error_codes: Collection[str],
    request_limit: int,
    retained_pool_states: Collection[object] = (),
) -> dict[str, object]:
    authority = market_signal_coverage_authority(operation_id, signal_type)
    if authority is None:
        return {
            "state": coverage.state,
            "source_error_codes": sorted(set(source_error_codes)),
        }
    return authority.public_material(
        coverage,
        observation_count=observation_count,
        retained_pool_states=retained_pool_states,
        source_error_codes=source_error_codes,
        request_limit=request_limit,
    )


def _market_signal_outcome(
    operation: MarketSignalSourceOperation,
    observations: tuple[dict[str, object], ...],
    errors: tuple[SignalSourceFailure, ...],
    query_complete: bool,
    diagnostics: dict[str, object] | None = None,
) -> OperationOutcome:
    unique_errors = tuple(
        {
            (
                item.source_operation,
                item.code,
                item.message,
                tuple(item.details.get("signal_types", ())),
            ): item
            for item in errors
        }.values()
    )
    return OperationOutcome(
        capability_id=_MARKET_SIGNAL_CAPABILITY,
        operation_id=operation.operation_id,
        execution=(
            OperationExecution("failed")
            if unique_errors
            else OperationExecution("succeeded", query_complete=query_complete)
        ),
        observations=observations,
        source_errors=tuple(
            {
                "source_operation": operation.operation_id,
                "code": item.code,
                "message": item.message,
                **(
                    {"details": {"signal_types": item.details["signal_types"]}}
                    if isinstance(item.details.get("signal_types"), list)
                    else {}
                ),
            }
            for item in unique_errors
        ),
        field_dispositions=_market_signal_dispositions(operation),
        diagnostics=diagnostics or {},
    )


def _market_signal_observation_key(
    observation: MarketSignalObservation,
    index: int,
) -> str:
    return (
        f"{observation.source_operation}:{_observation_identity(observation)}:{index}"
    )


def _market_signal_observation_payload(
    observation: MarketSignalObservation,
    index: int,
    query: MarketSignalQuery,
) -> dict[str, object]:
    dimensions = json.loads(json.dumps(observation.dimensions, ensure_ascii=False))
    if not isinstance(dimensions, dict):
        raise ValueError("market-signal dimensions must be a JSON object")
    provider_detail_url = dimensions.get("provider_detail_url")
    if isinstance(provider_detail_url, str) and provider_detail_url:
        dimensions["provider_detail_url"] = safe_public_locator(
            provider_detail_url,
            "market-signal-detail:"
            + hashlib.sha256(observation.source_operation.encode()).hexdigest()[:12]
            + f":{index}",
        )
    attributions = [
        {
            "text": item.text,
            "provenance": item.provenance,
            "layer": {
                "editorial_annotation": "source_editorial",
                "market_signal": "source_market_signal",
                "model_inference": "model_inference",
            }[item.provenance],
            "source_operation": item.source_operation,
            "source_document_id": item.source_document_id,
            "locator": (
                {
                    "uri": safe_public_locator(
                        item.locator_uri,
                        f"signal-attribution:{hashlib.sha256(item.source_operation.encode()).hexdigest()[:12]}:{index}",
                    )
                }
                if item.locator_uri is not None
                else None
            ),
            "basis_evidence_ids": list(item.basis_evidence_ids),
            "method_id": item.method_id,
        }
        for item in observation.attributions
    ]
    source_facts: dict[str, object] = {
        "signal_type": observation.signal_type,
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
        "attributions": attributions,
        "dimensions": dimensions,
        "limitations": list(observation.limitations),
    }
    return {
        "id": _market_signal_observation_key(observation, index),
        "kind": "market_signal_observation",
        "signal_type": observation.signal_type,
        "source_operation": observation.source_operation,
        "source_role": observation.source_role,
        "subject": observation.subject,
        "identity_scope": _market_signal_identity_scope(observation),
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
        "attributions": attributions,
        "dimensions": dimensions,
        "locator": {
            "uri": safe_public_locator(
                observation.locator_uri,
                f"market-signal:{hashlib.sha256(observation.source_operation.encode()).hexdigest()[:12]}:{index}",
            )
        },
        "limitations": list(observation.limitations),
        "query_scope": {
            "as_of": query.as_of,
            "observed_from": query.observed_from,
            "observed_to": query.observed_to,
            "signal_types": list(query.signal_types),
            "requested_signal_types": list(
                dict.fromkeys(query.parameters.get("signal_types", query.signal_types))
            ),
            "limit": query.limit,
        },
        "claim_eligibility": "experimental_observation_only",
        "issuer_fact": False,
        "certain_causality": False,
        "source_facts": source_facts,
    }


def _market_signal_identity_scope(observation: MarketSignalObservation) -> str:
    if (
        observation.subject is not None
        or "provider_security_code" in observation.dimensions
    ):
        return "security"
    if any(
        key in observation.dimensions
        for key in ("board_code", "industry_code", "sector_code")
    ):
        return "sector"
    return "market"


def _limit_market_signal_observations(
    observations: list[tuple[MarketSignalObservation, str]],
    limit: int,
) -> list[tuple[MarketSignalObservation, str]]:
    counts: dict[str, int] = {}
    selected: list[tuple[MarketSignalObservation, str]] = []
    for item in observations:
        signal_type = item[0].signal_type
        if counts.get(signal_type, 0) >= limit:
            continue
        counts[signal_type] = counts.get(signal_type, 0) + 1
        selected.append(item)
    return selected


def _market_signal_claims(
    observation: MarketSignalObservation,
    key: str,
) -> tuple[DirectTaskClaim, ...]:
    layers = list(
        dict.fromkeys(
            {
                "editorial_annotation": "source_editorial",
                "market_signal": "source_market_signal",
                "model_inference": "model_inference",
            }[item.provenance]
            for item in observation.attributions
        )
    ) or ["source_market_signal"]
    return tuple(
        DirectTaskClaim(
            "market_signal_observation",
            _MARKET_SIGNAL_EVIDENCE_USE,
            (key,),
            {
                "signal_type": observation.signal_type,
                "layer": layer,
                "issuer_fact": False,
                "certain_causality": False,
                "claim_eligibility": "experimental_observation_only",
            },
        )
        for layer in layers
    )


def _resolve_direct_provider_identities(
    request: dict[str, Any],
    observations: list[tuple[MarketSignalObservation, str]],
    transport: HttpTransport,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, tuple[str, ...]],
    tuple[OperationObligation, ...],
    tuple[OperationOutcome, ...],
    tuple[DirectTaskClaim, ...],
    tuple[ResearchDependency, ...],
    tuple[DirectTaskLimitation, ...],
]:
    focus_codes = {
        code
        for item, _ in observations
        if item.signal_type == "focus_monitoring"
        and item.subject is None
        and isinstance((code := item.dimensions.get("provider_security_code")), str)
    }
    severe_codes = {
        code
        for item, _ in observations
        if item.signal_type == "severe_abnormal_movement"
        and item.subject is None
        and isinstance((code := item.dimensions.get("provider_security_code")), str)
    }
    subjects: dict[str, dict[str, Any]] = {}
    identity_keys: dict[str, tuple[str, ...]] = {}
    obligations: list[OperationObligation] = []
    outcomes: list[OperationOutcome] = []
    claims: list[DirectTaskClaim] = []
    dependencies: list[ResearchDependency] = []
    limitations: list[DirectTaskLimitation] = []
    for provider_code in sorted(focus_codes.intersection(severe_codes)):
        execution = execute_security_identity_research(
            {
                **request,
                "task_type": "security_identity",
                "subjects": [{"clue": provider_code}],
                "window": None,
                "parameters": {},
            },
            build_default_identity_operations(transport),
        )
        capability_id, consumer_id = monitoring_identity_application(provider_code)
        namespace = capability_id.rpartition(".")[2]
        capability_map = {
            item.capability_id: capability_id for item in execution.plan.obligations
        }
        namespaced_outcomes, key_map = _namespace_identity_observations(
            execution.outcomes,
            namespace,
        )
        obligations.extend(
            OperationObligation(
                capability_map[item.capability_id],
                item.operation_id,
                item.source_role,
                item.experimental,
                item.exchange,
                item.evidence_uses,
            )
            for item in execution.plan.obligations
        )
        outcomes.extend(
            OperationOutcome(
                capability_map[item.capability_id],
                item.operation_id,
                item.execution,
                item.observations,
                item.source_errors,
                item.field_dispositions,
                item.diagnostics,
            )
            for item in namespaced_outcomes
        )
        limitations.extend(
            DirectTaskLimitation(item.code, item.message)
            for item in execution.material.limitations
        )
        canonical = execution.material.canonical_identity
        if canonical is None or execution.material.status == "blocked":
            continue
        subject = {
            "security": {
                "exchange": canonical.exchange,
                "code": canonical.code,
                "type": canonical.security_type,
            },
            "name": canonical.name,
            "issuer": {
                "name": canonical.issuer_name,
                "identifier": canonical.issuer_identifier,
                "security_relationship": canonical.issuer_relationship,
            },
        }
        subjects[provider_code] = subject
        material_keys = tuple(
            key_map[key] for key in execution.material.observation_keys
        )
        identity_keys[provider_code] = material_keys
        claims.append(
            DirectTaskClaim(
                consumer_id,
                "security_identity",
                material_keys,
                {"provider_security_code": provider_code, "subject": subject},
            )
        )
        dependencies.append(
            ResearchDependency(consumer_id, "security_identity", 1, True)
        )
    return (
        subjects,
        identity_keys,
        tuple(obligations),
        _sanitize_identity_outcomes(tuple(outcomes)),
        tuple(claims),
        tuple(dependencies),
        tuple(limitations),
    )


def _derive_direct_intersections(
    requested_types: tuple[str, ...],
    observations: list[tuple[MarketSignalObservation, str]],
    coverage_by_type: dict[str, list[tuple[str, SignalCoverage]]],
    provider_subjects: dict[str, dict[str, Any]],
    provider_identity_keys: dict[str, tuple[str, ...]],
    *,
    basis_has_errors: bool,
) -> tuple[list[MarketSignalObservation], list[str], bool, bool]:
    if DERIVED_SIGNAL_TYPE not in requested_types:
        return [], [], False, False
    focus = [item for item in observations if item[0].signal_type == "focus_monitoring"]
    severe = [
        item
        for item in observations
        if item[0].signal_type == "severe_abnormal_movement"
    ]
    derived: list[MarketSignalObservation] = []
    unresolved_overlap = False
    for focus_item, focus_key in focus:
        for severe_item, severe_key in severe:
            if (
                _canonical_security_key(focus_item.subject)
                == _canonical_security_key(severe_item.subject)
                and _canonical_security_key(focus_item.subject) is not None
                and _monitoring_window_contains(focus_item, severe_item.observed_on)
            ):
                derived.append(
                    _intersection_observation(
                        focus_item,
                        severe_item,
                        (focus_key, severe_key),
                    )
                )
                continue
            focus_code = focus_item.dimensions.get("provider_security_code")
            severe_code = severe_item.dimensions.get("provider_security_code")
            if (
                focus_item.subject is None
                and severe_item.subject is None
                and isinstance(focus_code, str)
                and focus_code == severe_code
                and _monitoring_window_contains(focus_item, severe_item.observed_on)
            ):
                subject = provider_subjects.get(focus_code)
                if subject is None:
                    unresolved_overlap = True
                    continue
                ignored = {"security_exchange_unverified", "security_type_unverified"}
                derived.append(
                    _intersection_observation(
                        replace(
                            focus_item,
                            subject=subject,
                            limitations=tuple(
                                item
                                for item in focus_item.limitations
                                if item not in ignored
                            ),
                        ),
                        replace(
                            severe_item,
                            subject=subject,
                            limitations=tuple(
                                item
                                for item in severe_item.limitations
                                if item not in ignored
                            ),
                        ),
                        (
                            focus_key,
                            severe_key,
                            *provider_identity_keys.get(focus_code, ()),
                        ),
                    )
                )
    derived = _deduplicate_observations(derived)
    keys = [
        _market_signal_observation_key(item, index)
        for index, item in enumerate(derived)
    ]
    complete = (
        not basis_has_errors
        and not unresolved_overlap
        and all(
            coverage_by_type.get(signal_type)
            and all(
                coverage.state in {"observed_nonempty", "observed_empty"}
                for _, coverage in coverage_by_type[signal_type]
            )
            for signal_type in DERIVATION_BASIS_TYPES
        )
    )
    return derived, keys, bool(complete), unresolved_overlap


def _direct_market_signal_conflicts(
    observations: list[tuple[MarketSignalObservation, str]],
) -> tuple[DirectTaskConflict, ...]:
    grouped: dict[tuple[str, str, str], list[tuple[MarketSignalObservation, str]]] = {}
    for item in observations:
        observation = item[0]
        key = (
            observation.signal_type,
            observation.observed_on,
            _entity_key(observation) or "market",
        )
        grouped.setdefault(key, []).append(item)
    conflicts: list[DirectTaskConflict] = []
    for items in grouped.values():
        states = _mutually_exclusive_pool_states([item for item, _ in items])
        if len(states) > 1:
            conflicts.append(
                DirectTaskConflict(
                    "cross_source_signal_disagreement",
                    "Sources disagree on mutually exclusive market-signal states.",
                    tuple(key for _, key in items),
                )
            )
    return tuple(conflicts)


SUBJECT_SIGNAL_TYPES = frozenset({"security_board_membership"})
MARKET_WIDE_SIGNAL_TYPES = MARKET_SIGNAL_TYPES.difference(SUBJECT_SIGNAL_TYPES)
DERIVED_SIGNAL_TYPE = "monitoring_intersection"
DERIVATION_BASIS_TYPES = frozenset({"focus_monitoring", "severe_abnormal_movement"})
COORDINATOR_OPERATION_ID = "market_signal_coordinator@1"
_MONITORING_IDENTITY_CAPABILITY_PREFIX = "security_identity_resolution.monitoring."
_MONITORING_IDENTITY_CONSUMER_PREFIX = "monitoring_intersection_identity:"


def monitoring_identity_application(provider_code: str) -> tuple[str, str]:
    """Return the owner-valid IDs for one runtime-discovered monitoring subject."""

    if (
        not isinstance(provider_code, str)
        or len(provider_code) != 6
        or not provider_code.isdigit()
    ):
        raise ValueError("monitoring identity provider code is invalid")
    namespace = hashlib.sha256(provider_code.encode()).hexdigest()[:12]
    return (
        f"{_MONITORING_IDENTITY_CAPABILITY_PREFIX}{namespace}",
        f"{_MONITORING_IDENTITY_CONSUMER_PREFIX}{provider_code}",
    )


def is_monitoring_identity_capability_id(value: object) -> bool:
    """Return whether a value declares a runtime monitoring identity capability."""

    return isinstance(value, str) and value.startswith(
        _MONITORING_IDENTITY_CAPABILITY_PREFIX
    )


def is_monitoring_identity_consumer_id(value: object) -> bool:
    """Return whether a value declares a runtime monitoring identity consumer."""

    return isinstance(value, str) and value.startswith(
        _MONITORING_IDENTITY_CONSUMER_PREFIX
    )


def monitoring_identity_application_provider_code(
    capability_id: str, consumer_id: str
) -> str:
    """Validate and invert one monitoring identity application pair."""

    if not is_monitoring_identity_consumer_id(consumer_id):
        raise ValueError("monitoring identity application is invalid")
    provider_code = consumer_id.removeprefix(_MONITORING_IDENTITY_CONSUMER_PREFIX)
    try:
        expected_capability, expected_consumer = monitoring_identity_application(
            provider_code
        )
    except ValueError as error:
        raise ValueError("monitoring identity application is invalid") from error
    if capability_id != expected_capability or consumer_id != expected_consumer:
        raise ValueError("monitoring identity application is invalid")
    return provider_code


def build_market_signals_result(
    request: dict[str, Any],
    operations: Collection[MarketSignalSourceOperation],
    identity_transport: HttpTransport,
) -> UnifiedResearchResult:
    """Collect operation-owned market signals into the formal result."""

    requested_types, query = _normalize_query(request)
    operation_list = tuple(sorted(operations, key=lambda item: item.operation_id))
    _validate_market_signal_operations(operation_list)
    identity_obligations: tuple[OperationObligation, ...] = ()
    identity_outcomes: tuple[OperationOutcome, ...] = ()
    identity_claims: tuple[DirectTaskClaim, ...] = ()
    identity_dependencies: tuple[ResearchDependency, ...] = ()
    identity_limitations: tuple[DirectTaskLimitation, ...] = ()
    identity_blocked = False
    if query.subject is not None:
        clue = query.subject.get("clue")
        if not isinstance(clue, str) or not clue.strip():
            raise ValueError("market_signals subject requires a non-empty clue")
        identity_request: dict[str, object] = {
            **request,
            "task_type": "security_identity",
            "subjects": [{"clue": clue.strip()}],
            "window": None,
            "parameters": {},
        }
        identity_execution = execute_security_identity_research(
            identity_request,
            build_default_identity_operations(identity_transport),
        )
        identity_obligations = identity_execution.plan.obligations
        identity_outcomes = _sanitize_identity_outcomes(identity_execution.outcomes)
        identity_limitations = tuple(
            DirectTaskLimitation(item.code, item.message)
            for item in identity_execution.material.limitations
        )
        canonical = identity_execution.material.canonical_identity
        identity_blocked = (
            identity_execution.material.status == "blocked" or canonical is None
        )
        if canonical is not None and not identity_blocked:
            canonical_subject = {
                "security": {
                    "exchange": canonical.exchange,
                    "code": canonical.code,
                    "type": canonical.security_type,
                },
                "name": canonical.name,
                "issuer": {
                    "name": canonical.issuer_name,
                    "identifier": canonical.issuer_identifier,
                    "security_relationship": canonical.issuer_relationship,
                },
            }
            query = replace(query, subject=canonical_subject)
            identity_claims = (
                DirectTaskClaim(
                    "market_signals_subject_identity",
                    "security_identity",
                    identity_execution.material.observation_keys,
                    canonical_subject,
                ),
            )
            identity_dependencies = (
                ResearchDependency(
                    "market_signals_subject_identity",
                    "security_identity",
                    1,
                    True,
                ),
            )

    outcomes: list[OperationOutcome] = []
    owned: list[tuple[MarketSignalObservation, str]] = []
    coverage_by_type: dict[str, list[tuple[str, SignalCoverage]]] = {
        signal_type: [] for signal_type in query.signal_types
    }
    limitation_codes: set[str] = {"experimental_market_signal_sources"}
    for operation in operation_list:
        relevant = operation.supported_signal_types.intersection(query.signal_types)
        operation_query = _operation_query(query, relevant, requested_types)
        if not relevant or (
            isinstance(operation, ParameterAwareMarketSignalSourceOperation)
            and not operation.is_applicable(operation_query)
        ):
            outcomes.append(_market_signal_skipped_outcome(operation, "not_requested"))
            continue
        if identity_blocked:
            outcomes.append(
                _market_signal_skipped_outcome(operation, "skipped_research_dependency")
            )
            continue
        try:
            batch = operation.collect(operation_query)
        except Exception:
            failure = _scope_signal_failure(
                SignalSourceFailure(
                    operation.operation_id,
                    "operation_failure",
                    "The source operation failed without a safe diagnostic.",
                ),
                relevant,
            )
            outcomes.append(
                _market_signal_failed_outcome(
                    operation,
                    failure,
                    diagnostics={
                        "signal_coverage": {
                            signal_type: {
                                **_market_signal_public_coverage(
                                    operation.operation_id,
                                    signal_type,
                                    SignalCoverage("indeterminate"),
                                    observation_count=0,
                                    source_error_codes=(failure.code,),
                                    request_limit=operation_query.limit,
                                )
                            }
                            for signal_type in sorted(relevant)
                        }
                    },
                )
            )
            for signal_type in relevant:
                coverage_by_type[signal_type].append(
                    (operation.operation_id, SignalCoverage("indeterminate"))
                )
            continue
        if (
            batch.operation_id != operation.operation_id
            or any(
                item.source_operation != operation.operation_id
                for item in batch.observations
            )
            or any(
                item.source_operation != operation.operation_id
                for item in batch.source_errors
            )
            or any(
                item.source_operation != operation.operation_id
                for item in batch.degradations
            )
        ):
            failure = _scope_signal_failure(
                SignalSourceFailure(
                    operation.operation_id,
                    "operation_owner_mismatch",
                    "The source batch contradicted its operation owner.",
                ),
                relevant,
            )
            outcomes.append(
                _market_signal_failed_outcome(
                    operation,
                    failure,
                    diagnostics={
                        "signal_coverage": {
                            signal_type: {
                                **_market_signal_public_coverage(
                                    operation.operation_id,
                                    signal_type,
                                    SignalCoverage("indeterminate"),
                                    observation_count=0,
                                    source_error_codes=(failure.code,),
                                    request_limit=operation_query.limit,
                                )
                            }
                            for signal_type in sorted(relevant)
                        }
                    },
                )
            )
            for signal_type in relevant:
                coverage_by_type[signal_type].append(
                    (operation.operation_id, SignalCoverage("indeterminate"))
                )
            continue
        if any(
            (
                isinstance(item.details.get("signal_type"), str)
                and item.details["signal_type"] not in relevant
            )
            or (
                isinstance(item.details.get("signal_types"), list)
                and (
                    not item.details["signal_types"]
                    or any(
                        not isinstance(signal_type, str) or signal_type not in relevant
                        for signal_type in item.details["signal_types"]
                    )
                )
            )
            for item in (*batch.source_errors, *batch.degradations)
        ):
            failure = _scope_signal_failure(
                SignalSourceFailure(
                    operation.operation_id,
                    "operation_scope_mismatch",
                    "The source batch contradicted its requested signal-type scope.",
                ),
                relevant,
            )
            outcomes.append(
                _market_signal_failed_outcome(
                    operation,
                    failure,
                    diagnostics={
                        "signal_coverage": {
                            signal_type: {
                                **_market_signal_public_coverage(
                                    operation.operation_id,
                                    signal_type,
                                    SignalCoverage("indeterminate"),
                                    observation_count=0,
                                    source_error_codes=(failure.code,),
                                    request_limit=operation_query.limit,
                                )
                            }
                            for signal_type in sorted(relevant)
                        }
                    },
                )
            )
            for signal_type in relevant:
                coverage_by_type[signal_type].append(
                    (operation.operation_id, SignalCoverage("indeterminate"))
                )
            continue
        coverage_errors = _validate_batch_coverage(
            operation.operation_id,
            batch.operation_id,
            batch.coverage,
            relevant,
            batch.observations,
        )
        valid, schema_errors = _validate_observations(
            operation.operation_id,
            batch.observations,
            operation_query,
        )
        errors = tuple(
            _scope_signal_failure(item, relevant)
            for item in (*coverage_errors, *schema_errors, *batch.source_errors)
        )
        query_complete = bool(
            relevant
            and all(
                signal_type in batch.coverage
                and batch.coverage[signal_type].state
                in {"observed_nonempty", "observed_empty"}
                for signal_type in relevant
            )
        )
        operation_owned = [
            (item, _market_signal_observation_key(item, index))
            for index, item in enumerate(valid)
        ]
        owned.extend(operation_owned)
        effective_coverage: dict[str, SignalCoverage] = {}
        for signal_type in sorted(relevant):
            reported = batch.coverage.get(signal_type)
            reported_state = (
                reported.state
                if reported is not None and _valid_coverage(reported)
                else None
            )
            error_codes = sorted(
                {
                    item.code
                    for item in errors
                    if signal_type in item.details["signal_types"]
                }
            )
            valid_count = sum(item.signal_type == signal_type for item in valid)
            state = reported_state or "indeterminate"
            if error_codes:
                state = (
                    "partial"
                    if valid_count
                    or reported_state
                    in {"observed_nonempty", "observed_empty", "partial"}
                    else "indeterminate"
                )
            effective_coverage[signal_type] = SignalCoverage(
                state,
                provider_total=(
                    reported.provider_total
                    if reported is not None and reported_state is not None
                    else None
                ),
                pages_collected=(
                    reported.pages_collected
                    if reported is not None and reported_state is not None
                    else None
                ),
                pages_expected=(
                    reported.pages_expected
                    if reported is not None and reported_state is not None
                    else None
                ),
                details=(reported.details if reported is not None else {}),
            )
        signal_coverage: dict[str, dict[str, object]] = {}
        for signal_type in sorted(relevant):
            error_codes = sorted(
                item.code
                for item in errors
                if signal_type in item.details["signal_types"]
            )
            signal_coverage[signal_type] = _market_signal_public_coverage(
                operation.operation_id,
                signal_type,
                effective_coverage[signal_type],
                observation_count=sum(
                    item.signal_type == signal_type for item in valid
                ),
                source_error_codes=error_codes,
                request_limit=operation_query.limit,
                retained_pool_states=tuple(
                    item.dimensions.get("pool_state")
                    for item in valid
                    if item.signal_type == signal_type
                ),
            )
        outcomes.append(
            _market_signal_outcome(
                operation,
                tuple(
                    _market_signal_observation_payload(item, index, query)
                    for index, item in enumerate(valid)
                ),
                errors,
                query_complete,
                {"signal_coverage": signal_coverage},
            )
        )
        for signal_type, coverage in effective_coverage.items():
            coverage_by_type[signal_type].append((operation.operation_id, coverage))
        limitation_codes.update(batch.limitations)
        limitation_codes.update(item.code for item in batch.degradations)
        limitation_codes.update(code for item in valid for code in item.limitations)

    (
        provider_subjects,
        provider_identity_keys,
        provider_identity_obligations,
        provider_identity_outcomes,
        provider_identity_claims,
        provider_identity_dependencies,
        provider_identity_limitations,
    ) = _resolve_direct_provider_identities(request, owned, identity_transport)
    identity_obligations = (*identity_obligations, *provider_identity_obligations)
    identity_outcomes = (*identity_outcomes, *provider_identity_outcomes)
    identity_claims = (*identity_claims, *provider_identity_claims)
    identity_dependencies = (*identity_dependencies, *provider_identity_dependencies)
    identity_limitations = (*identity_limitations, *provider_identity_limitations)
    basis_has_errors = any(
        outcome.source_errors
        and operation.supported_signal_types.intersection(DERIVATION_BASIS_TYPES)
        for operation, outcome in zip(operation_list, outcomes, strict=True)
    )
    derived, derived_keys, derived_complete, unresolved_overlap = (
        _derive_direct_intersections(
            requested_types,
            owned,
            coverage_by_type,
            provider_subjects,
            provider_identity_keys,
            basis_has_errors=basis_has_errors,
        )
    )
    public_id_by_key: dict[str, str] = {}
    for outcome in (*identity_outcomes, *tuple(outcomes)):
        for index, payload in enumerate(outcome.to_dict()["observations"]):
            local_key = payload.get("id")
            key = (
                local_key
                if isinstance(local_key, str) and local_key
                else f"{outcome.operation_id}:{index}"
            )
            public_id_by_key[key] = stable_result_id(
                "observation",
                {
                    "capability_id": outcome.capability_id,
                    "operation_id": outcome.operation_id,
                    "payload": payload,
                },
            )
    derived = [
        replace(
            item,
            attributions=tuple(
                replace(
                    attribution,
                    basis_evidence_ids=tuple(
                        public_id_by_key.get(key, key)
                        for key in attribution.basis_evidence_ids
                    ),
                )
                for attribution in item.attributions
            ),
        )
        for item in derived
    ]
    derived_keys = [
        _market_signal_observation_key(item, index)
        for index, item in enumerate(derived)
    ]
    coordinator_outcome: OperationOutcome | None = None
    coordinator_obligation: OperationObligation | None = None
    if DERIVED_SIGNAL_TYPE in requested_types:
        derived_coverage_state = (
            "observed_nonempty"
            if derived and derived_complete
            else "partial"
            if derived
            else "observed_empty"
            if derived_complete
            else "partial"
            if basis_has_errors
            else "indeterminate"
        )
        coordinator_obligation = OperationObligation(
            _MARKET_SIGNAL_CAPABILITY,
            COORDINATOR_OPERATION_ID,
            "market_signal",
            False,
            None,
            (EvidenceUsePolicy(_MARKET_SIGNAL_EVIDENCE_USE, "supporting"),),
            operation_kind="deterministic_coordinator",
        )
        coordinator_outcome = OperationOutcome(
            capability_id=_MARKET_SIGNAL_CAPABILITY,
            operation_id=COORDINATOR_OPERATION_ID,
            execution=(
                OperationExecution("failed")
                if unresolved_overlap
                else OperationExecution("succeeded", query_complete=derived_complete)
            ),
            observations=tuple(
                _market_signal_observation_payload(item, index, query)
                for index, item in enumerate(derived)
            ),
            source_errors=(
                (
                    {
                        "source_operation": COORDINATOR_OPERATION_ID,
                        "code": "identity_unverified",
                        "message": "A provider security overlap could not be resolved to one canonical identity.",
                        "details": {"signal_types": [DERIVED_SIGNAL_TYPE]},
                    },
                )
                if unresolved_overlap
                else ()
            ),
            field_dispositions=(
                FieldDisposition(
                    "basis_observation_ids",
                    "lineage_only",
                    "observations[].payload.attributions[].basis_evidence_ids",
                ),
                FieldDisposition(
                    "deterministic_intersection",
                    "normalized",
                    "observations[].payload.rule",
                ),
            ),
            diagnostics={
                "signal_coverage": {
                    DERIVED_SIGNAL_TYPE: {
                        "state": derived_coverage_state,
                        "source_error_codes": (
                            ["identity_unverified"] if unresolved_overlap else []
                        ),
                    }
                }
            },
        )
        owned.extend(zip(derived, derived_keys, strict=True))
        coverage_by_type[DERIVED_SIGNAL_TYPE] = [
            (
                COORDINATOR_OPERATION_ID,
                SignalCoverage(
                    derived_coverage_state,
                    provider_total=len(derived) if derived_complete else None,
                ),
            )
        ]

    selected = _limit_market_signal_observations(
        [
            (item, key)
            for item, key in owned
            if item.signal_type in requested_types
            and query.observed_from <= item.observed_on <= query.observed_to
        ],
        query.limit,
    )
    if len(selected) < sum(
        item.signal_type in requested_types
        and query.observed_from <= item.observed_on <= query.observed_to
        for item, _ in owned
    ):
        limitation_codes.add("result_truncated_to_limit")
    claims = tuple(
        claim for item, key in selected for claim in _market_signal_claims(item, key)
    )
    aggregate_coverage = _aggregate_coverage(
        requested_types,
        coverage_by_type,
        [item for item, _ in selected],
        [
            {
                "signal_types": list(
                    cast(
                        list[str],
                        cast(dict[str, object], error["details"])["signal_types"],
                    )
                )
            }
            for outcome in outcomes
            for error in outcome.to_dict()["source_errors"]
        ],
    )
    calculations: tuple[DirectTaskCalculation, ...] = tuple(
        DirectTaskCalculation(
            "market_signal_coverage",
            _MARKET_SIGNAL_EVIDENCE_USE,
            tuple(key for item, key in selected if item.signal_type == signal_type),
            {"signal_type": signal_type, **aggregate_coverage[signal_type]},
        )
        for signal_type in requested_types
    )
    aggregates = _build_aggregates(
        requested_types,
        coverage_by_type,
        [item for item, _ in selected],
        {_observation_identity(item): {"id": key} for item, key in selected},
    )
    sentiment = aggregates.get("limit_state_sentiment")
    if isinstance(sentiment, dict):
        basis = sentiment.get("basis_evidence_ids")
        if isinstance(basis, list) and all(isinstance(item, str) for item in basis):
            calculations = (
                *calculations,
                DirectTaskCalculation(
                    "limit_state_sentiment",
                    _MARKET_SIGNAL_EVIDENCE_USE,
                    tuple(basis),
                    sentiment,
                ),
            )
    conflicts = _direct_market_signal_conflicts(selected)
    limitations = [
        DirectTaskLimitation(code, _limitation_message(code))
        for code in sorted(limitation_codes)
    ]
    if query.allow_fallback:
        limitations.append(
            DirectTaskLimitation(
                "no_qualified_independent_fallback",
                "No qualified independent fallback is registered for these signals.",
            )
        )
    if any(
        item["state"] in {"partial", "indeterminate"}
        for item in aggregate_coverage.values()
    ):
        limitations.append(
            DirectTaskLimitation(
                "market_signal_coverage_incomplete",
                "At least one requested signal type lacks complete source coverage.",
            )
        )
    obligations = (
        *identity_obligations,
        *(tuple(_market_signal_obligation(item) for item in operation_list)),
        *((coordinator_obligation,) if coordinator_obligation is not None else ()),
    )
    direct_outcomes = (
        *identity_outcomes,
        *tuple(outcomes),
        *((coordinator_outcome,) if coordinator_outcome is not None else ()),
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="market_signals",
            as_of=str(request["as_of"]),
            obligations=obligations,
            dependencies=(
                *identity_dependencies,
                ResearchDependency(
                    "market_signal_observation",
                    _MARKET_SIGNAL_EVIDENCE_USE,
                    1,
                    True,
                ),
            ),
        ),
        direct_outcomes,
        claims=(*identity_claims, *claims),
        calculations=calculations,
        conflicts=conflicts,
        limitations=(*identity_limitations, *limitations),
        summary=(
            f"Market signals retained {len(owned)} operation-owned observation(s); "
            f"{len(selected)} entered requested-signal adoption."
        ),
    )


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


def _scope_signal_failure(
    failure: SignalSourceFailure,
    relevant_types: Collection[str],
) -> SignalSourceFailure:
    explicit = failure.details.get("signal_types")
    if isinstance(explicit, list) and all(isinstance(item, str) for item in explicit):
        signal_types = sorted(set(explicit).intersection(relevant_types))
    else:
        signal_type = failure.details.get("signal_type")
        signal_types = (
            [signal_type]
            if isinstance(signal_type, str) and signal_type in relevant_types
            else sorted(relevant_types)
        )
    return SignalSourceFailure(
        failure.source_operation,
        failure.code,
        failure.message,
        {"signal_types": signal_types},
    )


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


def _deduplicate_observations(
    observations: Iterable[MarketSignalObservation],
) -> list[MarketSignalObservation]:
    selected: dict[str, MarketSignalObservation] = {}
    for item in observations:
        selected.setdefault(_observation_identity(item), item)
    return sorted(selected.values(), key=_observation_identity)


def _observation_identity(observation: MarketSignalObservation) -> str:
    serialized = json.dumps(
        {
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
                {
                    "text": item.text,
                    "provenance": item.provenance,
                    "source_operation": item.source_operation,
                    "source_document_id": item.source_document_id,
                    "locator_uri": item.locator_uri,
                    "basis_evidence_ids": list(item.basis_evidence_ids),
                    "method_id": item.method_id,
                }
                for item in observation.attributions
            ],
            "dimensions": observation.dimensions,
            "locator_uri": observation.locator_uri,
            "limitations": list(observation.limitations),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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


def _limitation_message(code: str) -> str:
    return {
        "availability_time_unknown": "The source does not expose when this signal first became available.",
        "security_exchange_unverified": "The source exposes a provider security code but not a canonical exchange-qualified identity.",
        "coordinator_derived_market_signal": "The signal was deterministically derived by the coordinator from cited basis evidence.",
        "pagination_incomplete": "The source did not prove complete coverage of the requested pool.",
        "observation_time_precision_is_date_only": "The source exposes a trading date but not an intraday observation time.",
    }.get(code, "The source observation carries this explicit limitation.")
