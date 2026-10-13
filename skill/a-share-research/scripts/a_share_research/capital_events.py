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
from .identity_resolution import (
    execute_security_identity_research,
)
from .identity_sources import (
    HttpTransport,
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
    safe_public_locator,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
SUBJECT_FREE_DATA_TYPES = frozenset(
    {"northbound_flow", "board_fund_flow", "market_dragon_tiger"}
)
SUBJECT_REQUIRED_DATA_TYPES = CAPITAL_DATA_TYPES.difference(SUBJECT_FREE_DATA_TYPES)
_CAPITAL_CAPABILITY = "capital_event_observations"
_CAPITAL_EVIDENCE_USE = "capital_event_observation"


def build_capital_events_blocked_result(
    request: dict[str, Any],
    operations: Collection[CapitalSourceOperation],
) -> UnifiedResearchResult:
    """Return the formal result when policy prevents capital acquisition."""

    query = _normalize_query(request)
    operation_list = tuple(operations)
    _validate_capital_operations(operation_list)
    applicable = {
        operation.operation_id
        for operation in operation_list
        if operation.supported_data_types.intersection(query.data_types)
    }
    if not applicable:
        raise ValueError("capital_events has no applicable source operation")
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="capital_events",
            as_of=str(request["as_of"]),
            obligations=tuple(_capital_obligation(item) for item in operation_list),
            dependencies=(
                ResearchDependency(
                    consumer_id="capital_event_observation",
                    evidence_use=_CAPITAL_EVIDENCE_USE,
                    minimum_supporting_observations=1,
                    core=True,
                ),
            ),
        ),
        tuple(
            OperationOutcome(
                capability_id=_CAPITAL_CAPABILITY,
                operation_id=operation.operation_id,
                execution=OperationExecution(
                    "skipped_policy"
                    if operation.operation_id in applicable
                    else "not_requested"
                ),
                field_dispositions=_capital_dispositions(operation),
            )
            for operation in operation_list
        ),
        limitations=(
            DirectTaskLimitation(
                "source_policy_not_satisfied",
                "capital_events currently requires experimental source operations",
            ),
        ),
        summary="Capital-event acquisition was blocked by source policy.",
    )


def _capital_obligation(operation: CapitalSourceOperation) -> OperationObligation:
    return OperationObligation(
        capability_id=_CAPITAL_CAPABILITY,
        operation_id=operation.operation_id,
        source_role="declared_per_observation",
        experimental=True,
        exchange=None,
        evidence_uses=(EvidenceUsePolicy(_CAPITAL_EVIDENCE_USE, "supporting"),),
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


def _validate_capital_operations(
    operations: tuple[CapitalSourceOperation, ...],
) -> None:
    operation_ids = [item.operation_id for item in operations]
    if not operation_ids or len(operation_ids) != len(set(operation_ids)):
        raise ValueError("capital operation IDs must be non-empty and unique")
    for operation in operations:
        _capital_dispositions(operation)


def _capital_dispositions(
    operation: CapitalSourceOperation,
) -> tuple[FieldDisposition, ...]:
    raw_fields = getattr(operation, "raw_fields", None)
    if (
        not isinstance(raw_fields, tuple)
        or not raw_fields
        or any(not isinstance(item, str) or not item for item in raw_fields)
        or len(raw_fields) != len(set(raw_fields))
    ):
        raise ValueError(
            "capital operation raw_fields must be a unique non-empty tuple of text"
        )
    return tuple(
        _capital_field_disposition(operation.operation_id, item) for item in raw_fields
    )


def _capital_field_disposition(operation_id: str, field: str) -> FieldDisposition:
    if field in {"result.data[]", "data.klines[]", "data.diff[]"}:
        return FieldDisposition(
            field,
            "excluded",
            None,
            "Provider collection containers are not retained in public output.",
        )
    if field in {"success", "code", "result.pages", "result.count", "data.total"}:
        return FieldDisposition(
            field,
            "cross_check_only",
            f"coverage.{_CAPITAL_CAPABILITY}.operations.{operation_id}.query_complete",
        )
    if field in {"f1", "f2", "f7", "f57"}:
        return FieldDisposition(
            field,
            "excluded",
            None,
            "The requested provider metadata field is not retained.",
        )
    if field in {"data.code", "data.market", "SCODE", "SECURITY_CODE"}:
        return FieldDisposition(
            field,
            "cross_check_only",
            "observations[].payload.subject",
        )
    if field == "SECURITY_NAME_ABBR":
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.dimensions",
        )
    if field in {"f12", "f14"}:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.dimensions."
            + ("board_code" if field == "f12" else "board_name"),
        )
    if field in {
        "TRADE_DATE",
        "DATE",
        "END_DATE",
        "FREE_DATE",
        "f51",
        "f124",
    }:
        return FieldDisposition(
            field, "normalized", "observations[].payload.observed_on"
        )
    metric_destinations = {
        "NET_DEAL_AMT": "net_buy_amount",
        "f52": "main_net_inflow",
        "f53": "small_order_net_inflow",
        "f54": "medium_order_net_inflow",
        "f55": "large_order_net_inflow",
        "f56": "super_large_order_net_inflow",
        "f62": "main_net_inflow",
        "f164": "main_net_inflow",
        "f174": "main_net_inflow",
        "f184": "main_net_inflow_ratio",
        "f165": "main_net_inflow_ratio",
        "f175": "main_net_inflow_ratio",
        "f109": "price_change_ratio",
        "f160": "price_change_ratio",
        "f66": "super_large_order_net_inflow",
        "f72": "large_order_net_inflow",
        "f78": "medium_order_net_inflow",
        "f84": "small_order_net_inflow",
        "RZYE": "financing_balance",
        "RZMRE": "financing_buy_amount",
        "RZCHE": "financing_repayment_amount",
        "RQYE": "securities_lending_balance",
        "RQMCL": "securities_lending_sell_volume",
        "RQCHL": "securities_lending_repayment_volume",
        "RZRQYE": "margin_balance",
        "HOLDER_NUM": "shareholder_count",
        "HOLDER_NUM_CHANGE": "shareholder_count_change",
        "HOLDER_NUM_RATIO": "shareholder_count_change_ratio",
        "AVG_FREE_SHARES": "average_shares_per_holder",
        "PRETAX_BONUS_RMB": "cash_dividend_per_10_shares_before_tax",
        "BONUS_RATIO": "bonus_shares_per_10_shares",
        "TRANSFER_RATIO": "transfer_shares_per_10_shares",
        "BILLBOARD_NET_AMT": "net_buy_amount",
        "TURNOVERRATE": "turnover_rate",
        "CLOSE_PRICE": "close_price",
        "CHANGE_RATE": "change_rate",
        "BILLBOARD_BUY_AMT": "buy_amount",
        "BILLBOARD_SELL_AMT": "sell_amount",
        "DEAL_PRICE": "deal_price",
        "DEAL_VOLUME": "deal_volume",
        "DEAL_AMT": "deal_amount",
        "FREE_SHARES": "released_shares",
        "ABLE_FREE_SHARES": "tradable_shares",
        "FREE_RATIO": "total_share_ratio",
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
        "MUTUAL_TYPE": "connect_channel",
        "f204": "leader",
        "f257": "leader",
        "PRE_END_DATE": "previous_period_end",
        "EX_DIVIDEND_DATE": "ex_dividend_date",
        "PLAN_NOTICE_DATE": "plan_notice_date",
        "REPORT_DATE": "report_period_end",
        "ASSIGN_PROGRESS": "implementation_status",
        "EQUITY_RECORD_DATE": "record_date",
        "PAY_CASH_DATE": "cash_payment_date",
        "EXPLANATION": "reason",
        "OPERATEDEPT_NAME": "buy_seats",
        "OPERATEDEPT_CODE": "buy_seats",
        "BUY": "buy_seats",
        "SELL": "sell_seats",
        "NET": "buy_seats",
        "BUYER_NAME": "buyer_department",
        "SELLER_NAME": "seller_department",
        "FREE_SHARES_TYPE": "lockup_type",
    }
    dimension = dimension_destinations.get(field)
    if dimension is not None:
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.dimensions",
        )
    raise ValueError(f"capital operation has unknown raw field(s): {field}")


def _capital_skipped_outcome(
    operation: CapitalSourceOperation,
    state: str,
) -> OperationOutcome:
    return OperationOutcome(
        capability_id=_CAPITAL_CAPABILITY,
        operation_id=operation.operation_id,
        execution=OperationExecution(state),  # type: ignore[arg-type]
        field_dispositions=_capital_dispositions(operation),
    )


def _capital_failed_outcome(
    operation: CapitalSourceOperation,
    failure: CapitalSourceFailure,
) -> OperationOutcome:
    return _capital_outcome(operation, (), (failure,), False)


def _capital_outcome(
    operation: CapitalSourceOperation,
    observations: tuple[dict[str, object], ...],
    errors: tuple[CapitalSourceFailure, ...],
    query_complete: bool,
    diagnostics: dict[str, object] | None = None,
) -> OperationOutcome:
    unique_errors = tuple(
        {
            (
                item.source_operation,
                item.code,
                item.message,
                item.details.get("data_type"),
            ): item
            for item in errors
        }.values()
    )
    return OperationOutcome(
        capability_id=_CAPITAL_CAPABILITY,
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
                    {"details": {"data_type": item.details["data_type"]}}
                    if isinstance(item.details.get("data_type"), str)
                    else {}
                ),
            }
            for item in unique_errors
        ),
        field_dispositions=_capital_dispositions(operation),
        diagnostics=diagnostics or {},
    )


def _capital_observation_key(
    observation: CapitalObservation,
    index: int,
) -> str:
    return (
        f"{observation.source_operation}:{_observation_identity(observation)}:{index}"
    )


def _capital_observation_payload(
    observation: CapitalObservation,
    index: int,
    query: CapitalQuery,
) -> dict[str, object]:
    key = _capital_observation_key(observation, index)
    source_facts: dict[str, object] = {
        "data_type": observation.data_type,
        "subject": observation.subject,
        "observed_on": observation.observed_on,
        "available_at": observation.available_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "period": observation.period,
        "metrics": observation.metrics,
        "units": observation.units,
        "directions": observation.directions,
        "dimensions": observation.dimensions,
        "limitations": list(observation.limitations),
    }
    return {
        "id": key,
        "kind": "capital_event_observation",
        "data_type": observation.data_type,
        "source_operation": observation.source_operation,
        "source_role": observation.source_role,
        "subject": observation.subject,
        "observed_on": observation.observed_on,
        "available_at": observation.available_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "period": observation.period,
        "metrics": observation.metrics,
        "units": observation.units,
        "directions": observation.directions,
        "dimensions": observation.dimensions,
        "locator": {
            "uri": safe_public_locator(
                observation.locator_uri,
                f"capital:{hashlib.sha256(observation.source_operation.encode()).hexdigest()[:12]}:{index}",
            )
        },
        "limitations": list(observation.limitations),
        "query_scope": {
            "as_of": query.as_of,
            "observed_from": query.observed_from,
            "observed_to": query.observed_to,
            "data_types": list(query.data_types),
            "limit": query.limit,
        },
        "claim_eligibility": "experimental_observation_only",
        "source_facts": source_facts,
    }


def _limit_capital_observations(
    observations: list[tuple[CapitalObservation, str]],
    limit: int,
) -> list[tuple[CapitalObservation, str]]:
    counts: dict[str, int] = {}
    selected: list[tuple[CapitalObservation, str]] = []
    for item in observations:
        data_type = item[0].data_type
        if counts.get(data_type, 0) >= limit:
            continue
        counts[data_type] = counts.get(data_type, 0) + 1
        selected.append(item)
    return selected


def _capital_conflicts(
    observations: list[tuple[CapitalObservation, str]],
) -> tuple[DirectTaskConflict, ...]:
    grouped: dict[tuple[str, str, str, str], list[tuple[CapitalObservation, str]]] = {}
    for item in observations:
        observation = item[0]
        group = (
            observation.data_type,
            observation.observed_on,
            json.dumps(observation.subject, sort_keys=True, ensure_ascii=False),
            json.dumps(observation.period, sort_keys=True, ensure_ascii=False),
        )
        grouped.setdefault(group, []).append(item)
    conflicts: list[DirectTaskConflict] = []
    for items in grouped.values():
        signatures = {
            json.dumps(
                {
                    "metrics": item.metrics,
                    "units": item.units,
                    "directions": item.directions,
                    "dimensions": item.dimensions,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            for item, _ in items
        }
        if len(items) > 1 and len(signatures) > 1:
            conflicts.append(
                DirectTaskConflict(
                    "capital_observation_conflict",
                    "Capital observations conflict for the same identity and period.",
                    tuple(key for _, key in items),
                )
            )
    return tuple(conflicts)


def build_capital_events_result(
    request: dict[str, Any],
    operations: Collection[CapitalSourceOperation],
    identity_transport: HttpTransport,
) -> UnifiedResearchResult:
    """Collect operation-owned capital observations into the formal result."""

    query = _normalize_query(request)
    operation_list = tuple(operations)
    _validate_capital_operations(operation_list)
    identity_obligations: tuple[OperationObligation, ...] = ()
    identity_outcomes: tuple[OperationOutcome, ...] = ()
    identity_claims: tuple[DirectTaskClaim, ...] = ()
    identity_dependencies: tuple[ResearchDependency, ...] = ()
    identity_limitations: tuple[DirectTaskLimitation, ...] = ()
    identity_blocked = False
    if query.subject is not None:
        clue = query.subject.get("clue")
        if not isinstance(clue, str) or not clue.strip():
            raise ValueError("capital_events subject requires a non-empty clue")
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
                    "capital_events_subject_identity",
                    "security_identity",
                    identity_execution.material.observation_keys,
                    canonical_subject,
                ),
            )
            identity_dependencies = (
                ResearchDependency(
                    "capital_events_subject_identity",
                    "security_identity",
                    1,
                    True,
                ),
            )

    outcomes: list[OperationOutcome] = []
    owned: list[tuple[CapitalObservation, str]] = []
    coverage_batches: dict[str, list[tuple[str, int, bool, bool]]] = {
        data_type: [] for data_type in query.data_types
    }
    limitation_codes: set[str] = {"experimental_capital_event_sources"}
    for operation in operation_list:
        applicable = operation.supported_data_types.intersection(query.data_types)
        if not applicable:
            outcomes.append(_capital_skipped_outcome(operation, "not_requested"))
            continue
        if identity_blocked:
            outcomes.append(
                _capital_skipped_outcome(operation, "skipped_research_dependency")
            )
            continue
        try:
            batch = operation.collect(query)
        except Exception:
            failure = CapitalSourceFailure(
                operation.operation_id,
                "operation_failure",
                "The source operation failed without a safe diagnostic.",
            )
            outcomes.append(_capital_failed_outcome(operation, failure))
            for data_type in applicable:
                coverage_batches[data_type].append(
                    (operation.operation_id, 0, False, True)
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
            or (
                bool(batch.mode_acquisitions)
                and set(batch.mode_acquisitions) != set(applicable)
            )
            or any(
                data_type != acquisition.data_type
                or acquisition.operation_id != operation.operation_id
                or any(
                    item.data_type != data_type
                    or item.source_operation != operation.operation_id
                    for item in acquisition.observations
                )
                or any(
                    item.source_operation != operation.operation_id
                    for item in (
                        *acquisition.source_errors,
                        *acquisition.degradations,
                    )
                )
                for data_type, acquisition in batch.mode_acquisitions.items()
            )
        ):
            failure = CapitalSourceFailure(
                operation.operation_id,
                "operation_owner_mismatch",
                "The source batch contradicted its operation owner.",
            )
            outcomes.append(_capital_failed_outcome(operation, failure))
            for data_type in applicable:
                coverage_batches[data_type].append(
                    (operation.operation_id, 0, False, True)
                )
            continue
        if any(
            isinstance(item.details.get("data_type"), str)
            and item.details["data_type"] not in applicable
            for item in (*batch.source_errors, *batch.degradations)
        ):
            failure = CapitalSourceFailure(
                operation.operation_id,
                "operation_scope_mismatch",
                "The source batch contradicted its requested data-type scope.",
            )
            outcomes.append(_capital_failed_outcome(operation, failure))
            for data_type in applicable:
                coverage_batches[data_type].append(
                    (operation.operation_id, 0, False, True)
                )
            continue
        valid, schema_errors = _validate_observations(
            operation.operation_id,
            batch.observations,
            query,
        )
        operation_owned = [
            (item, _capital_observation_key(item, index))
            for index, item in enumerate(valid)
        ]
        owned.extend(operation_owned)
        errors = (*schema_errors, *batch.source_errors)
        has_errors = bool(errors)
        in_window = [item for item in valid if _within_window(item, query)]
        mode_diagnostics: dict[str, object] = {}
        if batch.mode_acquisitions:
            mode_diagnostics["mode_acquisitions"] = {
                data_type: {
                    "complete": acquisition.complete,
                    "source_error_codes": [
                        item.code for item in acquisition.source_errors
                    ]
                    + [
                        item.code
                        for item in schema_errors
                        if item.details.get("data_type") == data_type
                    ],
                }
                for data_type, acquisition in batch.mode_acquisitions.items()
            }
        for data_type in applicable:
            acquisition = batch.mode_acquisitions.get(data_type)
            if acquisition is None:
                query_complete = batch.complete
                type_has_errors = has_errors
            else:
                query_complete = acquisition.complete
                type_has_errors = bool(
                    acquisition.source_errors
                    or any(
                        item.details.get("data_type") == data_type
                        for item in schema_errors
                    )
                )
            coverage_batches[data_type].append(
                (
                    operation.operation_id,
                    sum(item.data_type == data_type for item in in_window),
                    query_complete,
                    type_has_errors,
                )
            )
        outcomes.append(
            _capital_outcome(
                operation,
                tuple(
                    _capital_observation_payload(item, index, query)
                    for index, item in enumerate(valid)
                ),
                errors,
                batch.complete,
                mode_diagnostics,
            )
        )
        limitation_codes.update(batch.limitations)
        limitation_codes.update(item.code for item in batch.degradations)
        limitation_codes.update(code for item in valid for code in item.limitations)

    selected = _limit_capital_observations(
        [(item, key) for item, key in owned if _within_window(item, query)],
        query.limit,
    )
    if len(selected) < sum(_within_window(item, query) for item, _ in owned):
        limitation_codes.add("result_truncated_to_limit")
    counts = {
        data_type: sum(item.data_type == data_type for item, _ in selected)
        for data_type in query.data_types
    }
    coverage = _coverage_by_data_type(query.data_types, counts, coverage_batches)
    claims = tuple(
        DirectTaskClaim(
            "capital_event_observation",
            _CAPITAL_EVIDENCE_USE,
            (key,),
            {
                "data_type": item.data_type,
                "claim_eligibility": "experimental_observation_only",
                "issuer_fact": item.source_role == "authoritative_disclosure",
            },
        )
        for item, key in selected
    )
    calculations = tuple(
        DirectTaskCalculation(
            "capital_event_coverage",
            _CAPITAL_EVIDENCE_USE,
            tuple(key for item, key in selected if item.data_type == data_type),
            {"data_type": data_type, **coverage[data_type]},
        )
        for data_type in query.data_types
    )
    conflicts = _capital_conflicts(selected)
    limitations = [
        DirectTaskLimitation(code, _limitation_message(code))
        for code in sorted(limitation_codes)
    ]
    if any(item["state"] == "indeterminate" for item in coverage.values()):
        limitations.append(
            DirectTaskLimitation(
                "requested_data_type_unavailable",
                "At least one requested capital-event type produced no usable observation.",
            )
        )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="capital_events",
            as_of=str(request["as_of"]),
            obligations=(
                *identity_obligations,
                *(tuple(_capital_obligation(item) for item in operation_list)),
            ),
            dependencies=(
                *identity_dependencies,
                ResearchDependency(
                    "capital_event_observation",
                    _CAPITAL_EVIDENCE_USE,
                    1,
                    True,
                ),
            ),
        ),
        (*identity_outcomes, *tuple(outcomes)),
        claims=(*identity_claims, *claims),
        calculations=calculations,
        conflicts=conflicts,
        limitations=(*identity_limitations, *limitations),
        summary=(
            f"Capital events retained {len(owned)} operation-owned observation(s); "
            f"{len(selected)} entered business-window adoption."
        ),
    )


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
        has_failed_source = any(has_errors for _, _, _, has_errors in contributions)
        complete_nonempty_source = any(
            count > 0 and complete and not has_errors
            for _, count, complete, has_errors in contributions
        )
        if observation_count:
            state = "observed_nonempty" if complete_nonempty_source else "partial"
        elif complete_source:
            state = "partial" if has_failed_source else "observed_empty"
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
                        "data_type": observation.data_type,
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
                    {
                        "data_type": observation.data_type,
                        "invalid_fields": sorted(set(invalid_fields)),
                    },
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
