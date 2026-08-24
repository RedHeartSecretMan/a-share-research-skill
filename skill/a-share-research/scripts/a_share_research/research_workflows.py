"""Fixed, versioned workflows composed from existing research tasks."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, cast

from .research_result import (
    UnifiedResearchResult,
    _validate_workflow_default_operation_inventory,
    stable_result_id,
)
from .workflow_contracts import (
    validate_registered_workflow_public_scope,
    validate_registered_workflow_request,
)

LeafTaskRunner = Callable[[dict[str, Any]], UnifiedResearchResult]

WORKFLOW_STEP_VERSION = "1.0"
_RECORD_FIELDS = (
    "observations",
    "claims",
    "calculations",
    "conflicts",
    "limitations",
    "source_errors",
)

_CapabilityTier = Literal["core", "standard", "deep"]


@dataclass(frozen=True)
class _WorkflowCapabilityAuthority:
    """Immutable capability truth compiled into one workflow registry."""

    capability_id: str
    tier: _CapabilityTier
    step_ids: tuple[str, ...] = ()
    reason_code: str | None = None
    gap_route: str | None = None

    def to_public_dict(self, *, completeness_mode: str) -> dict[str, object]:
        unsupported = self.reason_code is not None
        if unsupported != (self.gap_route is not None):
            raise AssertionError("workflow capability gap authority is incomplete")
        state = (
            "unsupported"
            if unsupported
            else (
                "not_requested"
                if self.tier == "standard" and completeness_mode == "fast"
                else "supported"
            )
        )
        result: dict[str, object] = {
            "capability_id": self.capability_id,
            "tier": self.tier,
            "state": state,
            "step_ids": list(self.step_ids),
        }
        if unsupported:
            result.update(
                {
                    "reason_code": self.reason_code,
                    "gap_route": self.gap_route,
                }
            )
        return result


_MARKET_OVERVIEW_CAPABILITY_AUTHORITY = (
    _WorkflowCapabilityAuthority(
        "industry_board_breadth", "core", ("market_core_signals",)
    ),
    _WorkflowCapabilityAuthority("limit_pools", "core", ("market_core_signals",)),
    _WorkflowCapabilityAuthority(
        "industry_theme_rotation",
        "standard",
        ("market_signal_enhancements",),
    ),
    _WorkflowCapabilityAuthority(
        "market_heat", "standard", ("market_signal_enhancements",)
    ),
    _WorkflowCapabilityAuthority(
        "focus_monitoring", "standard", ("market_signal_enhancements",)
    ),
    _WorkflowCapabilityAuthority(
        "severe_abnormal_movement",
        "standard",
        ("market_signal_enhancements",),
    ),
    _WorkflowCapabilityAuthority(
        "board_fund_flow", "standard", ("market_capital_enhancements",)
    ),
    _WorkflowCapabilityAuthority(
        "market_dragon_tiger", "standard", ("market_capital_enhancements",)
    ),
    _WorkflowCapabilityAuthority(
        "northbound_disclosure", "standard", ("market_capital_enhancements",)
    ),
    _WorkflowCapabilityAuthority("market_flash", "standard", ("market_flash",)),
    _WorkflowCapabilityAuthority(
        "market_trading_session",
        "core",
        reason_code="qualified_market_wide_owner_absent",
        gap_route="v0.4.0_spec",
    ),
    _WorkflowCapabilityAuthority(
        "major_broad_indices",
        "core",
        reason_code="qualified_market_wide_owner_absent",
        gap_route="v0.4.0_spec",
    ),
    _WorkflowCapabilityAuthority(
        "whole_market_turnover",
        "core",
        reason_code="qualified_market_wide_owner_absent",
        gap_route="v0.4.0_spec",
    ),
    _WorkflowCapabilityAuthority(
        "whole_market_margin",
        "standard",
        reason_code="qualified_market_wide_owner_absent",
        gap_route="v0.4.0_spec",
    ),
    _WorkflowCapabilityAuthority(
        "market_wide_intraday",
        "deep",
        reason_code="qualified_market_wide_owner_absent",
        gap_route="v0.4.0_spec",
    ),
)


def _market_overview_capability_inventory(
    inputs: Mapping[str, object],
) -> list[dict[str, object]]:
    completeness_mode = cast(str, inputs.get("completeness_mode", "standard"))
    return [
        item.to_public_dict(completeness_mode=completeness_mode)
        for item in _MARKET_OVERVIEW_CAPABILITY_AUTHORITY
    ]


def _validate_market_overview_unsupported_facts(
    result: Mapping[str, object],
) -> None:
    unsupported_ids = {
        item.capability_id
        for item in _MARKET_OVERVIEW_CAPABILITY_AUTHORITY
        if item.reason_code is not None
    }

    def carries_unsupported_capability_id(value: object) -> bool:
        if isinstance(value, Mapping):
            return any(
                (
                    key == "capability_id"
                    and isinstance(nested, str)
                    and nested in unsupported_ids
                )
                or carries_unsupported_capability_id(nested)
                for key, nested in value.items()
            )
        if isinstance(value, list):
            return any(carries_unsupported_capability_id(item) for item in value)
        return False

    for field in ("claims", "calculations"):
        values = result.get(field)
        if not isinstance(values, list):
            continue
        for record in values:
            if not isinstance(record, Mapping):
                continue
            kind = record.get("kind")
            if not isinstance(kind, str):
                raise ValueError("workflow formal fact kind must be a string")
            if kind in unsupported_ids or carries_unsupported_capability_id(
                record.get("payload")
            ):
                raise ValueError(
                    "unsupported market overview capability escaped into formal facts"
                )


def build_research_workflow_result(
    request: dict[str, Any],
    run_leaf_task: LeafTaskRunner,
) -> UnifiedResearchResult:
    """Compile and execute one approved workflow through leaf ResearchTasks."""

    validated_request = validate_registered_workflow_request(request)
    request = validated_request.public_request_copy()
    workflow_id = validated_request.workflow_id
    workflow_version = validated_request.workflow_version
    inputs = validated_request.inputs_copy()
    if workflow_id == "single_security_valuation":
        plan = [_plan_step("valuation", "security_valuation")]
        leaf_request = _leaf_request(
            request,
            task_type="security_valuation",
            subjects=request["subjects"],
            window=None,
            parameters={"target_pe": inputs["target_pe"]},
        )
        steps = [_execute_step(plan[0], leaf_request, run_leaf_task)]
    elif workflow_id == "valuation_comparison":
        plan = [_plan_step("comparison", "valuation_compare")]
        leaf_request = _leaf_request(
            request,
            task_type="valuation_compare",
            subjects=request["subjects"],
            window=None,
            parameters={"target_pe": inputs["target_pe"]},
        )
        steps = [_execute_step(plan[0], leaf_request, run_leaf_task)]
    elif workflow_id == "theme_report_research":
        plan = [_plan_step("report_research", "research_content")]
        leaf_request = _leaf_request(
            request,
            task_type="research_content",
            subjects=[],
            window={
                "published_from": inputs["published_from"],
                "published_to": inputs["published_to"],
            },
            parameters={
                "material_types": ["research_report"],
                "query": copy.deepcopy(inputs["query"]),
                "limit": inputs["limit"],
                "verify_documents": inputs["verify_documents"],
            },
        )
        steps = [_execute_step(plan[0], leaf_request, run_leaf_task)]
    elif workflow_id == "single_security_research":
        plan = _single_security_plan()
        steps = _run_single_security_workflow(
            request,
            inputs,
            plan,
            run_leaf_task,
        )
    elif workflow_id == "market_overview_research":
        plan = _market_overview_plan()
        steps = _run_market_overview_workflow(
            request,
            inputs,
            plan,
            run_leaf_task,
        )
    elif workflow_id == "new_security_research":
        plan = _new_security_plan()
        steps = _run_new_security_workflow(
            request,
            inputs,
            plan,
            run_leaf_task,
        )
    else:
        raise AssertionError("registered workflow has no execution implementation")
    return _fold_workflow_result(
        request,
        workflow_id,
        workflow_version,
        plan,
        steps,
    )


def _run_market_overview_workflow(
    request: dict[str, Any],
    inputs: dict[str, Any],
    plan: list[dict[str, Any]],
    run_leaf_task: LeafTaskRunner,
) -> list[dict[str, Any]]:
    completeness_mode = cast(str, inputs.get("completeness_mode", "standard"))
    specs_by_id = {item[0]: item for item in _market_overview_child_specs(request)}
    steps: list[dict[str, Any]] = []
    for plan_step in plan:
        if (
            completeness_mode == "fast"
            and plan_step["criticality"] == "standard_enrichment"
        ):
            steps.append(_not_requested_step(plan_step))
            continue
        step_id, task_type, window, parameters, subjects = specs_by_id[
            plan_step["step_id"]
        ]
        child_request = _leaf_request(
            request,
            task_type=task_type,
            subjects=subjects,
            window=window,
            parameters=parameters,
        )
        steps.append(_execute_step(plan_step, child_request, run_leaf_task))
    return steps


def _market_overview_child_specs(
    request: Mapping[str, object],
) -> list[tuple[str, str, object, object, object]]:
    as_of = cast(str, request["as_of"])
    one_day_window = {"observed_from": as_of, "observed_to": as_of}
    return [
        (
            "market_core_signals",
            "market_signals",
            one_day_window,
            {"signal_types": ["industry_rotation", "limit_state"], "limit": 100},
            [],
        ),
        (
            "market_signal_enhancements",
            "market_signals",
            one_day_window,
            {
                "signal_types": [
                    "strong_stock_theme",
                    "focus_monitoring",
                    "severe_abnormal_movement",
                    "market_heat",
                ],
                "limit": 100,
            },
            [],
        ),
        (
            "market_capital_enhancements",
            "capital_events",
            one_day_window,
            {
                "data_types": [
                    "board_fund_flow",
                    "market_dragon_tiger",
                    "northbound_flow",
                ],
                "board_type": "industry",
                "period": "today",
                "limit": 100,
            },
            [],
        ),
        (
            "market_flash",
            "research_content",
            {"published_from": as_of, "published_to": as_of},
            {
                "material_types": ["market_flash"],
                "query": [],
                "limit": 100,
                "verify_documents": False,
            },
            [],
        ),
    ]


def _run_single_security_workflow(
    request: dict[str, Any],
    inputs: dict[str, Any],
    plan: list[dict[str, Any]],
    run_leaf_task: LeafTaskRunner,
) -> list[dict[str, Any]]:
    completeness_mode = cast(str, inputs.get("completeness_mode", "standard"))
    identity_request = _leaf_request(
        request,
        task_type="security_identity",
        subjects=[{"clue": request["subjects"][0]["clue"]}],
        window=None,
        parameters={},
    )
    identity_step = _execute_step(plan[0], identity_request, run_leaf_task)
    identity_result = cast(UnifiedResearchResult, identity_step["result"])
    canonical_subject = _canonical_subject(identity_result.to_dict())
    if canonical_subject is None:
        identity_step["state"] = "blocked"
        identity_step["workflow_limitation"] = _identity_workflow_limitation()
        remaining = [
            (
                _not_requested_step(item)
                if completeness_mode == "fast"
                and item["criticality"] == "standard_enrichment"
                else _skipped_step(item, "identity")
            )
            for item in plan[1:]
        ]
        return [identity_step, *remaining]

    plan_by_id = {item["step_id"]: item for item in plan}
    specs_by_id = {
        item[0]: item
        for item in _single_security_child_specs(request, inputs, canonical_subject)
    }
    steps = [identity_step]
    for plan_step in plan[1:]:
        if (
            completeness_mode == "fast"
            and plan_step["criticality"] == "standard_enrichment"
        ):
            steps.append(_not_requested_step(plan_step))
            continue
        step_id, task_type, window, parameters, subjects = specs_by_id[
            plan_step["step_id"]
        ]
        child_request = _leaf_request(
            request,
            task_type=task_type,
            subjects=subjects,
            window=window,
            parameters=parameters,
        )
        steps.append(_execute_step(plan_by_id[step_id], child_request, run_leaf_task))
    return steps


def _single_security_child_specs(
    request: Mapping[str, object],
    inputs: Mapping[str, object],
    canonical_subject: Mapping[str, object],
) -> list[tuple[str, str, object, object, object]]:
    security = cast(Mapping[str, object], canonical_subject["security"])
    canonical_clue = f"{security['exchange']}:{security['code']}"
    subject = cast(list[Mapping[str, object]], request["subjects"])[0]
    as_of = date.fromisoformat(cast(str, request["as_of"]))
    deep_extensions = cast(list[str], inputs.get("deep_extensions", []))
    return [
        (
            "market_trend",
            "market_trend",
            {"trading_days": 250 if "long_history" in deep_extensions else 20},
            {"adjustment": "unadjusted"},
            [{"clue": canonical_clue}],
        ),
        (
            "valuation",
            "security_valuation",
            None,
            {"target_pe": inputs["target_pe"]},
            [
                {
                    "clue": canonical_clue,
                    "issuer_security_class_count": subject[
                        "issuer_security_class_count"
                    ],
                }
            ],
        ),
        (
            "materials",
            "research_content",
            {
                "published_from": (
                    as_of
                    - timedelta(
                        days=365 if "large_materials" in deep_extensions else 90
                    )
                ).isoformat(),
                "published_to": as_of.isoformat(),
            },
            {
                "material_types": [
                    "research_report",
                    "issuer_profile",
                    "consensus_material",
                    "stock_news",
                    "announcement",
                    "investor_qa",
                ],
                "query": [],
                "limit": 100 if "large_materials" in deep_extensions else 20,
                "verify_documents": False,
                "profile_categories": ["公司概况"],
            },
            [{"clue": canonical_clue}],
        ),
        (
            "capital_activity",
            "capital_events",
            {
                "observed_from": (as_of - timedelta(days=90)).isoformat(),
                "observed_to": as_of.isoformat(),
            },
            {
                "data_types": [
                    "stock_fund_flow",
                    "dragon_tiger",
                    "margin_trading",
                    "block_trade",
                    "shareholder_count",
                    "dividend",
                ],
                "period": "5d",
                "limit": 20,
            },
            [{"clue": canonical_clue}],
        ),
        (
            "lockup_events",
            "capital_events",
            {
                "observed_from": as_of.isoformat(),
                "observed_to": (as_of + timedelta(days=90)).isoformat(),
            },
            {"data_types": ["lockup"], "limit": 20},
            [{"clue": canonical_clue}],
        ),
        (
            "market_context",
            "market_signals",
            {
                "observed_from": as_of.isoformat(),
                "observed_to": as_of.isoformat(),
            },
            {"signal_types": ["security_board_membership"], "limit": 20},
            [{"clue": canonical_clue}],
        ),
    ]


def _run_new_security_workflow(
    request: dict[str, Any],
    inputs: dict[str, Any],
    plan: list[dict[str, Any]],
    run_leaf_task: LeafTaskRunner,
) -> list[dict[str, Any]]:
    identity_request = _leaf_request(
        request,
        task_type="security_identity",
        subjects=[{"clue": request["subjects"][0]["clue"]}],
        window=None,
        parameters={},
    )
    identity_step = _execute_step(plan[0], identity_request, run_leaf_task)
    identity_result = cast(UnifiedResearchResult, identity_step["result"])
    canonical_subject = _canonical_subject(identity_result.to_dict())
    if canonical_subject is None:
        identity_step["state"] = "blocked"
        identity_step["workflow_limitation"] = _identity_workflow_limitation()
        steps = [identity_step]
        steps.extend(_skipped_step(item, "identity") for item in plan[1:])
        return steps

    child_specs = _new_security_child_specs(request, inputs, canonical_subject)
    plan_by_id = {item["step_id"]: item for item in plan}
    steps = [identity_step]
    for step_id, task_type, window, parameters, subjects in child_specs:
        child_request = _leaf_request(
            request,
            task_type=task_type,
            subjects=subjects,
            window=window,
            parameters=parameters,
        )
        steps.append(_execute_step(plan_by_id[step_id], child_request, run_leaf_task))

    return steps


def _new_security_child_specs(
    request: Mapping[str, object],
    inputs: Mapping[str, object],
    canonical_subject: Mapping[str, object],
) -> list[tuple[str, str, object, object, object]]:
    security = cast(Mapping[str, object], canonical_subject["security"])
    canonical_clue = f"{security['exchange']}:{security['code']}"
    subject = cast(list[Mapping[str, object]], request["subjects"])[0]
    class_count = subject["issuer_security_class_count"]
    limit = inputs["limit"]
    market_window = cast(Mapping[str, object], inputs["market_window"])
    return [
        (
            "institutional_coverage",
            "research_content",
            inputs["report_window"],
            {
                "material_types": ["research_report", "consensus_material"],
                "query": [],
                "limit": limit,
                "verify_documents": inputs["verify_documents"],
            },
            [{"clue": canonical_clue}],
        ),
        (
            "valuation",
            "security_valuation",
            None,
            {"target_pe": inputs["target_pe"]},
            [
                {
                    "clue": canonical_clue,
                    "issuer_security_class_count": class_count,
                }
            ],
        ),
        (
            "board_membership",
            "market_signals",
            {
                "observed_from": market_window["observed_to"],
                "observed_to": market_window["observed_to"],
            },
            {"signal_types": ["security_board_membership"], "limit": limit},
            [{"clue": canonical_clue}],
        ),
        (
            "fund_flow",
            "capital_events",
            inputs["market_window"],
            {
                "data_types": ["stock_fund_flow"],
                "period": inputs["fund_flow_period"],
                "limit": limit,
            },
            [{"clue": canonical_clue}],
        ),
        (
            "dragon_tiger",
            "capital_events",
            inputs["market_window"],
            {"data_types": ["dragon_tiger"], "limit": limit},
            [{"clue": canonical_clue}],
        ),
        (
            "lockup",
            "capital_events",
            inputs["lockup_window"],
            {"data_types": ["lockup"], "limit": limit},
            [{"clue": canonical_clue}],
        ),
        (
            "margin_trading",
            "capital_events",
            inputs["market_window"],
            {"data_types": ["margin_trading"], "limit": limit},
            [{"clue": canonical_clue}],
        ),
    ]


def _leaf_request(
    request: dict[str, Any],
    *,
    task_type: str,
    subjects: object,
    window: object,
    parameters: object,
) -> dict[str, Any]:
    return {
        "schema_version": request["schema_version"],
        "task_type": task_type,
        "subjects": copy.deepcopy(subjects),
        "as_of": request["as_of"],
        "window": copy.deepcopy(window),
        "parameters": copy.deepcopy(parameters),
        "source_policy": copy.deepcopy(request["source_policy"]),
    }


def _compiled_leaf_scope(
    request: Mapping[str, object],
    workflow_id: str,
    step_id: str,
    canonical_subject: Mapping[str, object] | None,
) -> dict[str, Any] | None:
    parameters = cast(Mapping[str, object], request["parameters"])
    inputs = cast(Mapping[str, object], parameters["inputs"])
    if workflow_id == "single_security_valuation":
        return _leaf_request(
            cast(dict[str, Any], request),
            task_type="security_valuation",
            subjects=request["subjects"],
            window=None,
            parameters={"target_pe": inputs["target_pe"]},
        )
    if workflow_id == "valuation_comparison":
        return _leaf_request(
            cast(dict[str, Any], request),
            task_type="valuation_compare",
            subjects=request["subjects"],
            window=None,
            parameters={"target_pe": inputs["target_pe"]},
        )
    if workflow_id == "theme_report_research":
        return _leaf_request(
            cast(dict[str, Any], request),
            task_type="research_content",
            subjects=[],
            window={
                "published_from": inputs["published_from"],
                "published_to": inputs["published_to"],
            },
            parameters={
                "material_types": ["research_report"],
                "query": copy.deepcopy(inputs["query"]),
                "limit": inputs["limit"],
                "verify_documents": inputs["verify_documents"],
            },
        )
    if workflow_id == "market_overview_research":
        spec = next(
            item for item in _market_overview_child_specs(request) if item[0] == step_id
        )
        _, task_type, window, leaf_parameters, subjects = spec
        return _leaf_request(
            cast(dict[str, Any], request),
            task_type=task_type,
            subjects=subjects,
            window=window,
            parameters=leaf_parameters,
        )
    if step_id == "identity":
        return _leaf_request(
            cast(dict[str, Any], request),
            task_type="security_identity",
            subjects=[
                {
                    "clue": cast(list[Mapping[str, object]], request["subjects"])[0][
                        "clue"
                    ]
                }
            ],
            window=None,
            parameters={},
        )
    if canonical_subject is None:
        return None
    child_specs = (
        _single_security_child_specs(request, inputs, canonical_subject)
        if workflow_id == "single_security_research"
        else _new_security_child_specs(request, inputs, canonical_subject)
    )
    spec = next(item for item in child_specs if item[0] == step_id)
    _, task_type, window, leaf_parameters, subjects = spec
    return _leaf_request(
        cast(dict[str, Any], request),
        task_type=task_type,
        subjects=subjects,
        window=window,
        parameters=leaf_parameters,
    )


def _validate_workflow_request_scope(
    request_scope: object, workflow_id: str
) -> tuple[dict[str, Any], str]:
    if not isinstance(request_scope, dict):
        raise ValueError("workflow plan request scope must be an object")
    validated = validate_registered_workflow_public_scope(request_scope)
    if validated.workflow_id != workflow_id:
        raise ValueError("workflow plan request scope contradicts its workflow")
    return validated.public_request_copy(), validated.workflow_version


def _validate_leaf_request_facts(
    leaf: Mapping[str, object],
    request_scope: Mapping[str, object],
    *,
    default_operations: Sequence[object] = (),
) -> None:
    task_type = request_scope["task_type"]
    parameters = cast(Mapping[str, object], request_scope["parameters"])
    window = request_scope["window"]
    expected_query_scope: dict[str, object] | None = None
    payload_kind: str | None = None
    requested_kind_field: str | None = None
    requested_kinds: object = None
    if task_type == "capital_events":
        typed_window = cast(Mapping[str, object], window)
        expected_query_scope = {
            "as_of": request_scope["as_of"],
            "observed_from": typed_window["observed_from"],
            "observed_to": typed_window["observed_to"],
            "data_types": parameters["data_types"],
            "limit": parameters["limit"],
        }
        payload_kind = "capital_event_observation"
        requested_kind_field = "data_type"
        requested_kinds = parameters["data_types"]
    elif task_type == "market_signals":
        typed_window = cast(Mapping[str, object], window)
        expected_query_scope = {
            "as_of": request_scope["as_of"],
            "observed_from": typed_window["observed_from"],
            "observed_to": typed_window["observed_to"],
            "signal_types": parameters["signal_types"],
            "requested_signal_types": parameters["signal_types"],
            "limit": parameters["limit"],
        }
        payload_kind = "market_signal_observation"
        requested_kind_field = "signal_type"
        requested_kinds = parameters["signal_types"]
    elif task_type == "research_content":
        typed_window = cast(Mapping[str, object], window)
        expected_query_scope = {
            "as_of": request_scope["as_of"],
            "published_from": typed_window["published_from"],
            "published_to": typed_window["published_to"],
            "material_types": parameters["material_types"],
            "query": [
                cast(str, item).strip()
                for item in cast(list[object], parameters["query"])
            ],
            "limit": parameters["limit"],
            "verify_documents": parameters["verify_documents"],
        }
        payload_kind = "research_content_material"
        requested_kind_field = "material_type"
        requested_kinds = parameters["material_types"]
    elif task_type == "market_trend":
        if not isinstance(window, Mapping):
            raise ValueError("workflow market trend window is invalid")
        requested_trading_days = window.get("trading_days")
        summaries = [
            claim
            for claim in cast(list[Mapping[str, object]], leaf["claims"])
            if claim.get("kind") == "market_trend_summary"
        ]
        if len(summaries) > 1:
            raise ValueError("workflow market trend window is ambiguous")
        if summaries:
            payload = summaries[0].get("payload")
            selected_window = (
                payload.get("window") if isinstance(payload, Mapping) else None
            )
            series = payload.get("series") if isinstance(payload, Mapping) else None
            research = payload.get("research") if isinstance(payload, Mapping) else None
            if (
                not isinstance(selected_window, Mapping)
                or not isinstance(series, list)
                or not isinstance(research, Mapping)
                or selected_window.get("requested_trading_days")
                != requested_trading_days
                or selected_window.get("actual_trading_days") != len(series)
                or len(series) != requested_trading_days
                or research.get("as_of") != request_scope["as_of"]
                or research.get("timezone") != "Asia/Shanghai"
                or research.get("adjustment") != parameters.get("adjustment")
            ):
                raise ValueError(
                    "workflow market trend window contradicts its leaf facts"
                )
        for claim in cast(list[Mapping[str, object]], leaf["claims"]):
            if claim.get("kind") != "market_trend_diagnostics":
                continue
            payload = claim.get("payload")
            selected_count = (
                payload.get("selected_session_count")
                if isinstance(payload, Mapping)
                else None
            )
            if selected_count is not None and (
                not isinstance(selected_count, int)
                or isinstance(selected_count, bool)
                or not isinstance(requested_trading_days, int)
                or selected_count > requested_trading_days
            ):
                raise ValueError(
                    "workflow market trend window contradicts its diagnostics"
                )
    if expected_query_scope is not None:
        for observation in cast(list[Mapping[str, object]], leaf["observations"]):
            payload = observation.get("payload")
            if not isinstance(payload, Mapping) or payload.get("kind") != payload_kind:
                continue
            if (
                payload.get("query_scope") != expected_query_scope
                or not isinstance(requested_kinds, list)
                or payload.get(cast(str, requested_kind_field)) not in requested_kinds
            ):
                raise ValueError("workflow leaf facts contradict their request scope")
    if task_type == "research_content" and "profile_categories" in parameters:
        from .communication_sources import MootdxF10Operation
        from .content_contract import valid_f10_profile_categories

        profile_categories = parameters["profile_categories"]
        if not valid_f10_profile_categories(profile_categories):
            raise ValueError("workflow F10 profile categories are invalid")
        typed_profile_categories = cast(Sequence[str], profile_categories)
        for observation in cast(list[Mapping[str, object]], leaf["observations"]):
            if observation.get("operation_id") != MootdxF10Operation.operation_id:
                continue
            payload = observation.get("payload")
            attributes = (
                payload.get("attributes") if isinstance(payload, Mapping) else None
            )
            if (
                not isinstance(payload, Mapping)
                or payload.get("material_type")
                not in MootdxF10Operation.supported_material_types
                or not isinstance(attributes, Mapping)
                or attributes.get("category") not in typed_profile_categories
            ):
                raise ValueError(
                    "workflow F10 profile category contradicts its request scope"
                )
    coverage_kind: str | None = None
    coverage_field: str | None = None
    if task_type == "capital_events":
        coverage_kind = "capital_event_coverage"
        coverage_field = "data_type"
    elif task_type == "market_signals":
        coverage_kind = "market_signal_coverage"
        coverage_field = "signal_type"
    if coverage_kind is not None:
        coverage_values: list[object] = []
        for calculation in cast(list[Mapping[str, object]], leaf["calculations"]):
            if calculation.get("kind") != coverage_kind:
                continue
            payload = calculation.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError("workflow leaf coverage contradicts its request scope")
            coverage_values.append(payload.get(cast(str, coverage_field)))
        if not isinstance(requested_kinds, list):
            raise ValueError("workflow leaf coverage contradicts its request scope")
        if (
            len(coverage_values) != len(requested_kinds)
            or set(coverage_values) != set(requested_kinds)
        ) and not (
            not coverage_values
            and _leaf_is_wholly_blocked_by_source_policy(
                leaf,
                request_scope,
                requested_kinds=requested_kinds,
                default_operations=default_operations,
            )
        ):
            raise ValueError("workflow leaf coverage contradicts its request scope")
    if task_type in {"security_valuation", "valuation_compare"}:
        target_pe = parameters["target_pe"]
        for calculation in cast(list[Mapping[str, object]], leaf["calculations"]):
            kind = calculation.get("kind")
            if not isinstance(kind, str) or not kind.endswith("pe_digestion_years"):
                continue
            payload = calculation.get("payload")
            operands = payload.get("operands") if isinstance(payload, Mapping) else None
            if (
                not isinstance(operands, Mapping)
                or operands.get("target_pe") != target_pe
            ):
                raise ValueError("workflow leaf facts contradict their request scope")

    subjects = request_scope["subjects"]
    if not isinstance(subjects, list) or not subjects:
        return
    if task_type == "security_valuation":
        class_count = cast(Mapping[str, object], subjects[0]).get(
            "issuer_security_class_count"
        )
        metrics_claim = any(
            claim.get("kind") == "security_valuation_metrics"
            for claim in cast(list[Mapping[str, object]], leaf["claims"])
        )
        if class_count != 1:
            if leaf["status"] != "blocked" or metrics_claim:
                raise ValueError(
                    "workflow leaf class scope contradicts its request scope"
                )
            return
    comparison_claim: Mapping[str, object] | None = None
    comparison_rows: list[Mapping[str, object]] | None = None
    if task_type == "valuation_compare":
        comparison_claim = next(
            (
                claim
                for claim in cast(list[Mapping[str, object]], leaf["claims"])
                if claim.get("kind")
                in {"valuation_compare_diagnostics", "valuation_compare_members"}
            ),
            None,
        )
        payload = (
            comparison_claim.get("payload")
            if isinstance(comparison_claim, Mapping)
            else None
        )
        rows = payload.get("rows") if isinstance(payload, Mapping) else None
        if (
            not isinstance(rows, list)
            or len(rows) != len(subjects)
            or any(not isinstance(row, Mapping) for row in rows)
        ):
            raise ValueError("workflow leaf subject contradicts its request scope")
        comparison_rows = cast(list[Mapping[str, object]], rows)
        for subject, row in zip(subjects, comparison_rows, strict=True):
            class_count = cast(Mapping[str, object], subject).get(
                "issuer_security_class_count"
            )
            limitation_codes = row.get("limitation_codes")
            if class_count != 1 and (
                row.get("status") != "blocked"
                or row.get("security") is not None
                or not isinstance(limitation_codes, list)
                or "multi_class_issuer_not_supported" not in limitation_codes
            ):
                raise ValueError(
                    "workflow leaf class scope contradicts its request scope"
                )
    if task_type in {"security_valuation", "valuation_compare"}:
        resolvable_subjects = [
            subject
            for subject in subjects
            if cast(Mapping[str, object], subject).get("issuer_security_class_count")
            == 1
        ]
    else:
        resolvable_subjects = subjects
    expected_securities = _securities_for_request_clues(leaf, resolvable_subjects)
    if expected_securities is None:
        if leaf["status"] == "blocked":
            if task_type == "valuation_compare":
                payload = (
                    comparison_claim.get("payload")
                    if isinstance(comparison_claim, Mapping)
                    else None
                )
                claim_subjects = (
                    payload.get("subjects") if isinstance(payload, Mapping) else None
                )
                if (
                    not isinstance(claim_subjects, list)
                    or len(claim_subjects) != len(subjects)
                    or comparison_rows is None
                    or any(
                        not isinstance(claim_subject, Mapping)
                        or claim_subject.get("security") is not None
                        or claim_subject.get("name")
                        != cast(Mapping[str, object], subject).get("clue")
                        or row.get("security") is not None
                        or row.get("name")
                        != cast(Mapping[str, object], subject).get("clue")
                        or row.get("status") != "blocked"
                        for subject, claim_subject, row in zip(
                            subjects,
                            claim_subjects,
                            comparison_rows,
                            strict=True,
                        )
                    )
                ):
                    raise ValueError(
                        "workflow leaf subject contradicts its request scope"
                    )
            return
        raise ValueError("workflow leaf subject contradicts its request scope")
    if task_type == "valuation_compare":
        payload = (
            comparison_claim.get("payload")
            if isinstance(comparison_claim, Mapping)
            else None
        )
        claim_subjects = (
            payload.get("subjects") if isinstance(payload, Mapping) else None
        )
        expected_ids: list[str | None] = []
        resolved_index = 0
        for subject in subjects:
            if (
                cast(Mapping[str, object], subject).get("issuer_security_class_count")
                == 1
            ):
                security = expected_securities[resolved_index]
                resolved_index += 1
                expected_ids.append(f"{security['exchange']}:{security['code']}")
            else:
                expected_ids.append(None)
        if (
            not isinstance(claim_subjects, list)
            or [item.get("security") for item in claim_subjects] != expected_ids
            or comparison_rows is None
            or [item.get("security") for item in comparison_rows] != expected_ids
        ):
            raise ValueError("workflow leaf subject contradicts its request scope")
        for index, subject in enumerate(subjects):
            typed_subject = cast(Mapping[str, object], subject)
            if typed_subject.get("issuer_security_class_count") == 1:
                continue
            clue = typed_subject["clue"]
            claim_subject = claim_subjects[index]
            if (
                not isinstance(claim_subject, Mapping)
                or claim_subject.get("name") != clue
                or comparison_rows[index].get("name") != clue
            ):
                raise ValueError("workflow leaf subject contradicts its request scope")
        return
    if len(expected_securities) != 1:
        return
    expected_security = expected_securities[0]
    subject_claim_kinds = {
        "capital_events": "capital_events_subject_identity",
        "market_signals": "market_signals_subject_identity",
        "research_content": "research_content_subject_identity",
    }
    matched_subject = False
    for claim in cast(list[Mapping[str, object]], leaf["claims"]):
        payload = claim.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if claim.get("kind") == subject_claim_kinds.get(cast(str, task_type)):
            matched_subject = True
            if payload.get("security") != expected_security:
                raise ValueError("workflow leaf subject contradicts its request scope")
        elif task_type == "security_valuation" and claim.get("kind") in {
            "security_valuation_diagnostics",
            "security_valuation_metrics",
        }:
            claim_subjects = payload.get("subjects")
            matched_subject = True
            if (
                not isinstance(claim_subjects, list)
                or len(claim_subjects) != 1
                or not isinstance(claim_subjects[0], Mapping)
                or claim_subjects[0].get("security") != expected_security
            ):
                raise ValueError("workflow leaf subject contradicts its request scope")
    if (
        task_type in {*subject_claim_kinds, "security_valuation"}
        and leaf["status"] != "blocked"
        and not matched_subject
    ):
        raise ValueError("workflow leaf subject contradicts its request scope")


def _leaf_is_wholly_blocked_by_source_policy(
    leaf: Mapping[str, object],
    request_scope: Mapping[str, object],
    *,
    requested_kinds: Sequence[object],
    default_operations: Sequence[object],
) -> bool:
    """Recognize the sole legitimate no-calculation policy-blocked leaf."""

    task_type = request_scope.get("task_type")
    if task_type == "market_signals":
        capability_id = "market_signal_observations"
        supported_field = "supported_signal_types"
        limitation_message = (
            "market_signals currently requires experimental source operations"
        )
    elif task_type == "capital_events":
        capability_id = "capital_event_observations"
        supported_field = "supported_data_types"
        limitation_message = (
            "capital_events currently requires experimental source operations"
        )
    else:
        return False
    source_policy = request_scope.get("source_policy")
    if (
        leaf.get("status") != "blocked"
        or not isinstance(source_policy, Mapping)
        or source_policy.get("allow_experimental") is not False
        or not default_operations
        or any(
            leaf.get(field) != []
            for field in (
                "observations",
                "claims",
                "calculations",
                "conflicts",
                "source_errors",
            )
        )
    ):
        return False
    limitation_payload: dict[str, object] = {
        "code": "source_policy_not_satisfied",
        "message": limitation_message,
        "references": [],
    }
    expected_limitation = {
        "id": stable_result_id("limitation", limitation_payload),
        **limitation_payload,
    }
    if leaf.get("limitations") != [expected_limitation]:
        return False

    coverage = leaf.get("coverage")
    aggregate = coverage.get(capability_id) if isinstance(coverage, Mapping) else None
    operations = aggregate.get("operations") if isinstance(aggregate, Mapping) else None
    if not isinstance(operations, Mapping):
        return False
    operation_states: dict[str, object] = {}
    for operation_id, operation_coverage in operations.items():
        if not isinstance(operation_id, str) or not isinstance(
            operation_coverage, Mapping
        ):
            return False
        operation_states[operation_id] = operation_coverage.get("execution_state")

    signal_query: object | None = None
    if task_type == "market_signals":
        from .market_signal_contract import MarketSignalQuery

        window = request_scope.get("window")
        parameters = request_scope.get("parameters")
        subjects = request_scope.get("subjects")
        if (
            not isinstance(window, Mapping)
            or not isinstance(parameters, Mapping)
            or not isinstance(subjects, list)
            or any(not isinstance(item, str) for item in requested_kinds)
        ):
            return False
        subject = subjects[0] if subjects else None
        if subject is not None and not isinstance(subject, dict):
            return False
        signal_query = MarketSignalQuery(
            signal_types=tuple(cast(Sequence[str], requested_kinds)),
            as_of=cast(str, request_scope["as_of"]),
            observed_from=cast(str, window["observed_from"]),
            observed_to=cast(str, window["observed_to"]),
            limit=cast(int, parameters["limit"]),
            subject=copy.deepcopy(subject),
            parameters=copy.deepcopy(dict(parameters)),
            allow_fallback=cast(bool, source_policy["allow_fallback"]),
        )

    applicable_owner_ids: set[str] = set()
    for requested_kind in requested_kinds:
        if not isinstance(requested_kind, str):
            return False
        owners_for_kind: set[str] = set()
        for operation in default_operations:
            operation_id = getattr(operation, "operation_id", None)
            supported_kinds = getattr(operation, supported_field, None)
            if (
                not isinstance(operation_id, str)
                or not isinstance(supported_kinds, frozenset)
                or requested_kind not in supported_kinds
            ):
                continue
            is_applicable = getattr(operation, "is_applicable", None)
            if signal_query is not None and callable(is_applicable):
                if not is_applicable(signal_query):
                    continue
            owners_for_kind.add(operation_id)
        if not owners_for_kind or any(
            operation_states.get(operation_id) != "skipped_policy"
            for operation_id in owners_for_kind
        ):
            return False
        applicable_owner_ids.update(owners_for_kind)
    return all(
        state
        == (
            "skipped_policy"
            if operation_id in applicable_owner_ids
            else "not_requested"
        )
        for operation_id, state in operation_states.items()
    )


def _securities_for_request_clues(
    leaf: Mapping[str, object], subjects: list[object]
) -> list[dict[str, object]] | None:
    identities: list[tuple[set[str], dict[str, object]]] = []
    for observation in cast(list[Mapping[str, object]], leaf["observations"]):
        payload = observation.get("payload")
        identity = payload.get("observation") if isinstance(payload, Mapping) else None
        if (
            not isinstance(identity, Mapping)
            or identity.get("kind") != "security_identity"
            or identity.get("exchange") not in {"SSE", "SZSE"}
            or not isinstance(identity.get("code"), str)
            or not isinstance(identity.get("name"), str)
            or identity.get("security_type") != "A_SHARE"
        ):
            continue
        typed_payload = cast(Mapping[str, object], payload)
        exchange = cast(str, identity["exchange"])
        code = cast(str, identity["code"])
        aliases = {code, f"{exchange}:{code}", cast(str, identity["name"])}
        subject = typed_payload.get("subject")
        issuer = subject.get("issuer") if isinstance(subject, Mapping) else None
        if isinstance(issuer, str) and issuer.strip():
            aliases.add(issuer)
        candidate: dict[str, object] = {
            "exchange": exchange,
            "code": code,
            "type": "A_SHARE",
        }
        if not any(existing == candidate for _, existing in identities):
            identities.append((aliases, candidate))
    resolved: list[dict[str, object]] = []
    for subject in subjects:
        clue = subject.get("clue") if isinstance(subject, Mapping) else None
        matches = [security for aliases, security in identities if clue in aliases]
        if len(matches) != 1:
            return None
        resolved.append(matches[0])
    return resolved


def _new_security_plan() -> list[dict[str, Any]]:
    return [
        _plan_step("identity", "security_identity", criticality="gate"),
        _plan_step(
            "institutional_coverage", "research_content", depends_on=["identity"]
        ),
        _plan_step("valuation", "security_valuation", depends_on=["identity"]),
        _plan_step("board_membership", "market_signals", depends_on=["identity"]),
        _plan_step("fund_flow", "capital_events", depends_on=["identity"]),
        _plan_step("dragon_tiger", "capital_events", depends_on=["identity"]),
        _plan_step("lockup", "capital_events", depends_on=["identity"]),
        _plan_step("margin_trading", "capital_events", depends_on=["identity"]),
    ]


def _market_overview_plan() -> list[dict[str, Any]]:
    return [
        _plan_step(
            "market_core_signals",
            "market_signals",
            criticality="core",
        ),
        _plan_step(
            "market_signal_enhancements",
            "market_signals",
            criticality="standard_enrichment",
        ),
        _plan_step(
            "market_capital_enhancements",
            "capital_events",
            criticality="standard_enrichment",
        ),
        _plan_step(
            "market_flash",
            "research_content",
            criticality="standard_enrichment",
        ),
    ]


def _single_security_plan() -> list[dict[str, Any]]:
    return [
        _plan_step("identity", "security_identity", criticality="gate"),
        _plan_step(
            "market_trend",
            "market_trend",
            depends_on=["identity"],
            criticality="core",
        ),
        _plan_step(
            "valuation",
            "security_valuation",
            depends_on=["identity"],
            criticality="standard_enrichment",
        ),
        _plan_step(
            "materials",
            "research_content",
            depends_on=["identity"],
            criticality="standard_enrichment",
        ),
        _plan_step(
            "capital_activity",
            "capital_events",
            depends_on=["identity"],
            criticality="standard_enrichment",
        ),
        _plan_step(
            "lockup_events",
            "capital_events",
            depends_on=["identity"],
            criticality="standard_enrichment",
        ),
        _plan_step(
            "market_context",
            "market_signals",
            depends_on=["identity"],
            criticality="standard_enrichment",
        ),
    ]


def _plan_step(
    step_id: str,
    task_type: str,
    *,
    depends_on: list[str] | None = None,
    criticality: str = "required_evidence",
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "version": WORKFLOW_STEP_VERSION,
        "task_type": task_type,
        "criticality": criticality,
        "depends_on": [] if depends_on is None else depends_on,
    }


def _execute_step(
    plan_step: dict[str, Any],
    request: dict[str, Any],
    run_leaf_task: LeafTaskRunner,
) -> dict[str, Any]:
    result = run_leaf_task(request)
    if not isinstance(result, UnifiedResearchResult):
        raise ValueError("workflow leaf task must return UnifiedResearchResult")
    status = cast(str, result.to_dict()["status"])
    return {
        **copy.deepcopy(plan_step),
        "state": status,
        "result": result,
        "request_scope": copy.deepcopy(request),
    }


def _skipped_step(plan_step: dict[str, Any], dependency: str) -> dict[str, Any]:
    return {
        **copy.deepcopy(plan_step),
        "state": "skipped_research_dependency",
        "result": None,
        "request_scope": None,
        "workflow_limitation": _dependency_skip_limitation(),
    }


def _not_requested_step(plan_step: dict[str, Any]) -> dict[str, Any]:
    return {
        **copy.deepcopy(plan_step),
        "state": "not_requested",
        "result": None,
        "request_scope": None,
    }


def _canonical_subject(result: object) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("status") not in {
        "supported",
        "limited",
    }:
        return None
    conflicts = result.get("conflicts")
    claims = result.get("claims")
    if conflicts or not isinstance(claims, list) or len(claims) != 1:
        return None
    claim = claims[0]
    if not isinstance(claim, dict) or claim.get("kind") != "security_identity":
        return None
    candidate = claim.get("payload")
    if not isinstance(candidate, dict):
        return None
    security = candidate.get("security")
    name = candidate.get("name")
    issuer = candidate.get("issuer")
    if not isinstance(security, dict):
        return None
    exchange = security.get("exchange")
    code = security.get("code")
    security_type = security.get("type")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
        or security_type != "A_SHARE"
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(issuer, dict)
    ):
        return None
    return {
        "security": {
            "exchange": exchange,
            "code": code,
            "type": security_type,
        },
        "name": name,
        "issuer": copy.deepcopy(issuer),
    }


def _workflow_limitation(
    code: str, message: str, references: Sequence[str]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "message": message,
        "references": list(references),
    }
    return {"id": stable_result_id("limitation", payload), **payload}


def _identity_workflow_limitation() -> dict[str, object]:
    return _workflow_limitation(
        "workflow_identity_not_canonical",
        "The identity step did not establish one canonical A-share security "
        "for dependent workflow steps.",
        (),
    )


def _dependency_skip_limitation() -> dict[str, object]:
    return _workflow_limitation(
        "workflow_step_skipped_research_dependency",
        "The workflow step has a research gap because its identity dependency was "
        "blocked.",
        (),
    )


def _merge_records(
    target: list[dict[str, object]],
    seen: dict[str, dict[str, object]],
    values: object,
    *,
    field: str,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"workflow leaf {field} must be an array")
    references: list[str] = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise ValueError(f"workflow leaf {field} record is invalid")
        record = cast(dict[str, object], copy.deepcopy(value))
        record_id = cast(str, record["id"])
        prior = seen.get(record_id)
        if prior is not None and prior != record:
            raise ValueError("workflow leaf record ID is not globally stable")
        if prior is None:
            seen[record_id] = record
            target.append(record)
        references.append(record_id)
    return references


def _derive_workflow_status(
    workflow_id: str, step_refs: Sequence[Mapping[str, object]]
) -> str:
    states = [item.get("state") for item in step_refs]
    if workflow_id == "market_overview_research":
        return "blocked" if states[0] == "blocked" else "limited"
    if workflow_id not in {"new_security_research", "single_security_research"}:
        return cast(str, states[0])
    if states[0] == "blocked":
        return "blocked"
    substantive = [state for state in states[1:] if state != "not_requested"]
    if states[0] == "supported" and all(item == "supported" for item in substantive):
        return "supported"
    if any(item in {"supported", "limited"} for item in substantive):
        return "limited"
    return "blocked"


def _fold_workflow_result(
    request: Mapping[str, object],
    workflow_id: str,
    workflow_version: str,
    plan_steps: Sequence[Mapping[str, object]],
    executed_steps: Sequence[Mapping[str, object]],
) -> UnifiedResearchResult:
    collections: dict[str, list[dict[str, object]]] = {
        field: [] for field in _RECORD_FIELDS
    }
    seen: dict[str, dict[str, object]] = {}
    briefs: list[dict[str, object]] = []
    step_refs: list[dict[str, object]] = []
    registries: dict[str, object] = {}
    coverage: dict[str, object] = {}

    for executed in executed_steps:
        step_id = cast(str, executed["step_id"])
        result = executed.get("result")
        references: dict[str, list[str]] = {field: [] for field in _RECORD_FIELDS}
        workflow_limitation_ids: list[str] = []
        brief_ids: list[str] = []
        if isinstance(result, UnifiedResearchResult):
            leaf = result.to_dict()
            for field in _RECORD_FIELDS:
                references[field] = _merge_records(
                    collections[field], seen, leaf[field], field=field
                )
            brief_ids = _merge_records(briefs, seen, leaf["briefs"], field="briefs")
            registries[step_id] = {
                "step_id": step_id,
                "task_type": executed["task_type"],
                "version": executed["version"],
                "leaf_request_scope": copy.deepcopy(executed["request_scope"]),
                "leaf_plan": copy.deepcopy(leaf["plan"]),
                "capability_registry": copy.deepcopy(leaf["capability_registry"]),
            }
        else:
            limitation = executed.get("workflow_limitation")
            if isinstance(limitation, dict):
                workflow_limitation_ids = _merge_records(
                    collections["limitations"],
                    seen,
                    [limitation],
                    field="limitations",
                )
            registries[step_id] = {
                "step_id": step_id,
                "task_type": executed["task_type"],
                "version": executed["version"],
                "leaf_request_scope": None,
                "leaf_plan": None,
                "capability_registry": {},
            }
        workflow_limitation = executed.get("workflow_limitation")
        if isinstance(workflow_limitation, dict) and isinstance(
            result, UnifiedResearchResult
        ):
            workflow_limitation_ids = _merge_records(
                collections["limitations"],
                seen,
                [workflow_limitation],
                field="limitations",
            )
        step_refs.append(
            {
                "step_id": step_id,
                "version": executed["version"],
                "task_type": executed["task_type"],
                "depends_on": copy.deepcopy(executed["depends_on"]),
                "state": executed["state"],
                **{f"{field[:-1]}_ids": ids for field, ids in references.items()},
                "workflow_limitation_ids": workflow_limitation_ids,
                "brief_ids": brief_ids,
            }
        )
        coverage[step_id] = {
            "state": (cast(str, executed["state"]) if result is None else "complete"),
            "leaf": (
                copy.deepcopy(result.to_dict()["coverage"])
                if isinstance(result, UnifiedResearchResult)
                else {}
            ),
            **{
                f"{field[:-1]}_ids": copy.deepcopy(references[field])
                for field in _RECORD_FIELDS
            },
            "workflow_limitation_ids": copy.deepcopy(workflow_limitation_ids),
            "brief_ids": copy.deepcopy(brief_ids),
        }

    status = _derive_workflow_status(workflow_id, step_refs)
    all_ids = {
        f"{field[:-1]}_ids": [cast(str, item["id"]) for item in values]
        for field, values in collections.items()
    }
    plan_payload: dict[str, object] = {
        "version": workflow_version,
        "task_type": "research_workflow",
        "workflow_id": workflow_id,
        "request_scope": copy.deepcopy(dict(request)),
        "steps": [copy.deepcopy(dict(item)) for item in plan_steps],
    }
    plan = {"id": stable_result_id("plan", plan_payload), **plan_payload}
    brief_payload: dict[str, object] = {
        "status": status,
        "summary": f"Completed preset research workflow {workflow_id}.",
        **copy.deepcopy(all_ids),
    }
    brief_id = stable_result_id("brief", brief_payload)
    briefs.append({"id": brief_id, **brief_payload})
    view = {
        "status": status,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "steps": step_refs,
        **copy.deepcopy(all_ids),
        "brief_id": brief_id,
    }
    workflow_registry: dict[str, object] = {
        "id": workflow_id,
        "version": workflow_version,
    }
    if workflow_id == "market_overview_research":
        inputs = cast(
            Mapping[str, object],
            cast(Mapping[str, object], request["parameters"])["inputs"],
        )
        workflow_registry["capability_inventory"] = (
            _market_overview_capability_inventory(inputs)
        )
    return UnifiedResearchResult(
        status=cast(Any, status),
        research_anchor={"as_of": request["as_of"], "timezone": "Asia/Shanghai"},
        plan=plan,
        capability_registry={
            "workflow": workflow_registry,
            "steps": registries,
        },
        coverage={"steps": coverage},
        observations=tuple(collections["observations"]),
        claims=tuple(collections["claims"]),
        calculations=tuple(collections["calculations"]),
        conflicts=tuple(collections["conflicts"]),
        limitations=tuple(collections["limitations"]),
        source_errors=tuple(collections["source_errors"]),
        task_views={"research_workflow": view},
        briefs=tuple(briefs),
    )


def _expected_workflow_steps(workflow_id: str) -> list[dict[str, Any]]:
    if workflow_id == "single_security_valuation":
        return [_plan_step("valuation", "security_valuation")]
    if workflow_id == "valuation_comparison":
        return [_plan_step("comparison", "valuation_compare")]
    if workflow_id == "theme_report_research":
        return [_plan_step("report_research", "research_content")]
    if workflow_id == "single_security_research":
        return _single_security_plan()
    if workflow_id == "market_overview_research":
        return _market_overview_plan()
    if workflow_id == "new_security_research":
        return _new_security_plan()
    raise ValueError("workflow result uses an unsupported workflow")


def _exact_keys(value: Mapping[str, object], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{path} does not match the strict workflow result schema")


def _validate_stable_record(
    record: Mapping[str, object], *, prefix: str, expected: set[str], path: str
) -> str:
    _exact_keys(record, {"id", *expected}, path)
    record_id = record.get("id")
    if not isinstance(record_id, str) or record_id != stable_result_id(
        prefix, {key: value for key, value in record.items() if key != "id"}
    ):
        raise ValueError(f"{path} ID contradicts its public owned facts")
    return record_id


def validate_workflow_research_result(result: Mapping[str, object]) -> None:
    """Close a workflow over fixed steps and shared formal leaf records."""

    plan = result.get("plan")
    if not isinstance(plan, Mapping):
        raise ValueError("workflow plan must be an object")
    _exact_keys(
        plan,
        {
            "id",
            "version",
            "task_type",
            "workflow_id",
            "request_scope",
            "steps",
        },
        "workflow plan",
    )
    workflow_id = plan.get("workflow_id")
    if not isinstance(workflow_id, str):
        raise ValueError("workflow plan identity is invalid")
    request_scope, workflow_version = _validate_workflow_request_scope(
        plan.get("request_scope"), workflow_id
    )
    if result.get("research_anchor") != {
        "as_of": request_scope["as_of"],
        "timezone": "Asia/Shanghai",
    }:
        raise ValueError("workflow research anchor contradicts its request scope")
    expected_steps = _expected_workflow_steps(workflow_id)
    if (
        plan.get("version") != workflow_version
        or plan.get("task_type") != "research_workflow"
        or plan.get("steps") != expected_steps
        or plan.get("id")
        != stable_result_id(
            "plan", {key: value for key, value in plan.items() if key != "id"}
        )
    ):
        raise ValueError("workflow plan contradicts its fixed versioned contract")

    registry = result.get("capability_registry")
    coverage = result.get("coverage")
    if not isinstance(registry, Mapping) or not isinstance(coverage, Mapping):
        raise ValueError("workflow registry and coverage must be objects")
    _exact_keys(registry, {"workflow", "steps"}, "workflow registry")
    _exact_keys(coverage, {"steps"}, "workflow coverage")
    workflow_registry = registry.get("workflow")
    registered_steps = registry.get("steps")
    covered_steps = coverage.get("steps")
    workflow_inputs = cast(
        Mapping[str, object],
        cast(Mapping[str, object], request_scope["parameters"])["inputs"],
    )
    expected_workflow_registry: dict[str, object] = {
        "id": workflow_id,
        "version": workflow_version,
    }
    if workflow_id == "market_overview_research":
        expected_workflow_registry["capability_inventory"] = (
            _market_overview_capability_inventory(workflow_inputs)
        )
    if (
        workflow_registry != expected_workflow_registry
        or not isinstance(registered_steps, Mapping)
        or not isinstance(covered_steps, Mapping)
    ):
        raise ValueError("workflow registry contradicts the plan")
    step_ids = [item["step_id"] for item in expected_steps]
    if set(registered_steps) != set(step_ids) or set(covered_steps) != set(step_ids):
        raise ValueError("workflow step registry or coverage is incomplete")
    for step in expected_steps:
        registered = registered_steps.get(step["step_id"])
        if not isinstance(registered, Mapping):
            raise ValueError("workflow step registry must be an object")
        _exact_keys(
            registered,
            {
                "step_id",
                "task_type",
                "version",
                "leaf_request_scope",
                "leaf_plan",
                "capability_registry",
            },
            "workflow step registry",
        )
        if any(
            registered.get(field) != step[field]
            for field in ("step_id", "task_type", "version")
        ):
            raise ValueError("workflow step registry contradicts the plan")

    record_specs = {
        "observations": (
            "observation",
            {"capability_id", "operation_id", "payload"},
        ),
        "calculations": (
            "calculation",
            {"kind", "evidence_use", "input_ids", "payload"},
        ),
        "conflicts": ("conflict", {"code", "message", "observation_ids"}),
        "limitations": ("limitation", {"code", "message", "references"}),
    }
    records: dict[str, dict[str, Mapping[str, object]]] = {}
    for field, (prefix, expected) in record_specs.items():
        values = result.get(field)
        if not isinstance(values, list):
            raise ValueError(f"workflow {field} must be an array")
        indexed: dict[str, Mapping[str, object]] = {}
        for value in values:
            if not isinstance(value, Mapping):
                raise ValueError(f"workflow {field} record must be an object")
            record_id = _validate_stable_record(
                value, prefix=prefix, expected=expected, path=f"workflow {field} record"
            )
            if record_id in indexed:
                raise ValueError(f"workflow {field} IDs must be unique")
            indexed[record_id] = value
        records[field] = indexed

    claims_value = result.get("claims")
    if not isinstance(claims_value, list):
        raise ValueError("workflow claims must be an array")
    claims: dict[str, Mapping[str, object]] = {}
    for claim in claims_value:
        if not isinstance(claim, Mapping):
            raise ValueError("workflow claim must be an object")
        direct = "evidence_use" in claim
        expected = {
            "kind",
            "observation_ids",
            "evidence_adoptions",
            "payload",
        }
        if direct:
            expected.add("evidence_use")
        claim_id = _validate_stable_record(
            claim, prefix="claim", expected=expected, path="workflow claim"
        )
        if claim_id in claims:
            raise ValueError("workflow claim IDs must be unique")
        claims[claim_id] = claim
    records["claims"] = claims
    if workflow_id == "market_overview_research":
        _validate_market_overview_unsupported_facts(result)

    errors_value = result.get("source_errors")
    if not isinstance(errors_value, list):
        raise ValueError("workflow source errors must be an array")
    errors: dict[str, Mapping[str, object]] = {}
    for error in errors_value:
        if not isinstance(error, Mapping):
            raise ValueError("workflow source error must be an object")
        expected = {"capability_id", "operation_id", "code", "message"}
        if "details" in error:
            expected.add("details")
        error_id = _validate_stable_record(
            error,
            prefix="source-error",
            expected=expected,
            path="workflow source error",
        )
        if error_id in errors:
            raise ValueError("workflow source error IDs must be unique")
        errors[error_id] = error
    records["source_errors"] = errors

    briefs_value = result.get("briefs")
    if not isinstance(briefs_value, list):
        raise ValueError("workflow briefs must be an array")
    briefs: dict[str, Mapping[str, object]] = {}
    brief_fields = {
        "status",
        "summary",
        "observation_ids",
        "claim_ids",
        "calculation_ids",
        "conflict_ids",
        "limitation_ids",
        "source_error_ids",
    }
    for brief in briefs_value:
        if not isinstance(brief, Mapping):
            raise ValueError("workflow brief must be an object")
        brief_id = _validate_stable_record(
            brief, prefix="brief", expected=brief_fields, path="workflow brief"
        )
        if brief_id in briefs:
            raise ValueError("workflow brief IDs must be unique")
        briefs[brief_id] = brief

    views = result.get("task_views")
    if not isinstance(views, Mapping) or set(views) != {"research_workflow"}:
        raise ValueError("workflow requires one formal task view")
    view = views["research_workflow"]
    if not isinstance(view, Mapping):
        raise ValueError("workflow task view must be an object")
    ref_fields = {
        "observation_ids": "observations",
        "claim_ids": "claims",
        "calculation_ids": "calculations",
        "conflict_ids": "conflicts",
        "limitation_ids": "limitations",
        "source_error_ids": "source_errors",
    }
    _exact_keys(
        view,
        {
            "status",
            "workflow_id",
            "workflow_version",
            "steps",
            *ref_fields,
            "brief_id",
        },
        "workflow task view",
    )
    step_refs = view.get("steps")
    if not isinstance(step_refs, list) or len(step_refs) != len(expected_steps):
        raise ValueError("workflow step references are incomplete")
    referenced: dict[str, set[str]] = {field: set() for field in records}
    referenced_briefs: set[str] = set()
    derived_states: dict[str, object] = {}
    canonical_subject: Mapping[str, object] | None = None
    completeness_mode = workflow_inputs.get("completeness_mode", "standard")
    for contract, step_ref in zip(expected_steps, step_refs, strict=True):
        if not isinstance(step_ref, Mapping):
            raise ValueError("workflow step reference must be an object")
        _exact_keys(
            step_ref,
            {
                "step_id",
                "version",
                "task_type",
                "depends_on",
                "state",
                *ref_fields,
                "workflow_limitation_ids",
                "brief_ids",
            },
            "workflow step reference",
        )
        for field in ("step_id", "version", "task_type", "depends_on"):
            if step_ref.get(field) != contract[field]:
                raise ValueError("workflow step reference contradicts the plan")
        step_id = cast(str, contract["step_id"])
        registered = cast(Mapping[str, object], registered_steps[step_id])
        dependency_blocked = any(
            derived_states.get(dependency) == "blocked"
            for dependency in cast(list[str], contract["depends_on"])
        )
        for reference_field, collection in ref_fields.items():
            ids = step_ref.get(reference_field)
            if not isinstance(ids, list) or any(
                not isinstance(item, str) or item not in records[collection]
                for item in ids
            ):
                raise ValueError("workflow step has an unknown record reference")
            referenced[collection].update(cast(list[str], ids))
        workflow_limitations = step_ref.get("workflow_limitation_ids")
        brief_ids = step_ref.get("brief_ids")
        if (
            not isinstance(workflow_limitations, list)
            or any(item not in records["limitations"] for item in workflow_limitations)
            or not isinstance(brief_ids, list)
            or any(item not in briefs for item in brief_ids)
        ):
            raise ValueError("workflow step has an unknown derived reference")
        referenced["limitations"].update(cast(list[str], workflow_limitations))
        referenced_briefs.update(cast(list[str], brief_ids))
        step_coverage = covered_steps.get(contract["step_id"])
        expected_coverage = {
            "state": (
                cast(str, step_ref["state"])
                if step_ref.get("state")
                in {"skipped_research_dependency", "not_requested"}
                else "complete"
            ),
            "leaf": step_coverage.get("leaf")
            if isinstance(step_coverage, Mapping)
            else None,
            **{
                field: step_ref[field]
                for field in (*ref_fields, "workflow_limitation_ids", "brief_ids")
                if field != "brief_id"
            },
        }
        if step_coverage != expected_coverage:
            raise ValueError("workflow coverage contradicts its step references")
        if step_ref.get("state") in {
            "skipped_research_dependency",
            "not_requested",
        }:
            not_requested = step_ref.get("state") == "not_requested"
            expected_limitation = _dependency_skip_limitation()
            if (
                (not_requested and completeness_mode != "fast")
                or (not_requested and contract["criticality"] != "standard_enrichment")
                or (not not_requested and not dependency_blocked)
                or brief_ids
                or any(step_ref[field] for field in ref_fields)
                or workflow_limitations
                != ([] if not_requested else [expected_limitation["id"]])
                or (
                    not not_requested
                    and records["limitations"].get(cast(str, expected_limitation["id"]))
                    != expected_limitation
                )
                or registered.get("leaf_request_scope") is not None
                or registered.get("leaf_plan") is not None
                or registered.get("capability_registry") != {}
                or not isinstance(step_coverage, Mapping)
                or step_coverage.get("leaf") != {}
            ):
                raise ValueError("workflow dependency state contradicts its fixed plan")
            derived_states[step_id] = step_ref.get("state")
        else:
            if dependency_blocked:
                raise ValueError("workflow dependency state contradicts its fixed plan")
            if (
                completeness_mode == "fast"
                and contract["criticality"] == "standard_enrichment"
            ):
                raise ValueError("workflow fast mode executed an enhancement")
            if len(brief_ids) != 1:
                raise ValueError("completed workflow step requires one leaf brief")
            leaf_brief = briefs[cast(str, brief_ids[0])]
            for reference_field in ref_fields:
                if leaf_brief.get(reference_field) != step_ref.get(reference_field):
                    raise ValueError("workflow step omitted completed leaf facts")
            expected_scope = _compiled_leaf_scope(
                request_scope, workflow_id, step_id, canonical_subject
            )
            if (
                expected_scope is None
                or registered.get("leaf_request_scope") != expected_scope
            ):
                raise ValueError("workflow leaf request scope contradicts its step")
            leaf_plan = registered.get("leaf_plan")
            leaf_registry = registered.get("capability_registry")
            leaf_coverage = (
                step_coverage.get("leaf")
                if isinstance(step_coverage, Mapping)
                else None
            )
            if not all(
                isinstance(item, Mapping)
                for item in (leaf_plan, leaf_registry, leaf_coverage)
            ):
                raise ValueError("completed workflow step lacks formal leaf lineage")
            typed_leaf_plan = cast(Mapping[str, object], leaf_plan)
            leaf_task_type = typed_leaf_plan.get("task_type")
            if leaf_task_type != contract["task_type"]:
                raise ValueError("workflow leaf plan contradicts its step task")
            leaf_result = UnifiedResearchResult(
                status=cast(Any, leaf_brief["status"]),
                research_anchor=cast(Mapping[str, object], result["research_anchor"]),
                plan=typed_leaf_plan,
                capability_registry=cast(Mapping[str, object], leaf_registry),
                coverage=cast(Mapping[str, object], leaf_coverage),
                observations=tuple(
                    records["observations"][item]
                    for item in cast(list[str], step_ref["observation_ids"])
                ),
                claims=tuple(
                    records["claims"][item]
                    for item in cast(list[str], step_ref["claim_ids"])
                ),
                calculations=tuple(
                    records["calculations"][item]
                    for item in cast(list[str], step_ref["calculation_ids"])
                ),
                conflicts=tuple(
                    records["conflicts"][item]
                    for item in cast(list[str], step_ref["conflict_ids"])
                ),
                limitations=tuple(
                    records["limitations"][item]
                    for item in cast(list[str], step_ref["limitation_ids"])
                ),
                source_errors=tuple(
                    records["source_errors"][item]
                    for item in cast(list[str], step_ref["source_error_ids"])
                ),
                task_views={
                    cast(str, leaf_task_type): {
                        "status": leaf_brief["status"],
                        **{
                            field: copy.deepcopy(step_ref[field])
                            for field in ref_fields
                        },
                        "brief_id": brief_ids[0],
                    }
                },
                briefs=(leaf_brief,),
            )
            serialized_leaf = leaf_result.to_dict()
            default_operations: tuple[object, ...] = ()
            if workflow_id in {
                "single_security_research",
                "market_overview_research",
            }:
                default_operations = _validate_workflow_default_operation_inventory(
                    serialized_leaf,
                    task_type=leaf_task_type,
                    source_policy=cast(
                        Mapping[str, object], expected_scope["source_policy"]
                    ),
                )
            _validate_leaf_request_facts(
                serialized_leaf,
                expected_scope,
                default_operations=default_operations,
            )
            expected_workflow_limitations: list[dict[str, object]] = []
            if step_id == "identity":
                canonical_subject = _canonical_subject(serialized_leaf)
                if canonical_subject is None:
                    expected_workflow_limitations = [_identity_workflow_limitation()]
            expected_limitation_ids = [
                cast(str, item["id"]) for item in expected_workflow_limitations
            ]
            if workflow_limitations != expected_limitation_ids or any(
                records["limitations"].get(cast(str, item["id"])) != item
                for item in expected_workflow_limitations
            ):
                raise ValueError("workflow dependency state contradicts its fixed plan")
            derived_step_status = (
                "blocked" if expected_workflow_limitations else leaf_brief.get("status")
            )
            if step_ref.get("state") != derived_step_status:
                raise ValueError("workflow step status contradicts its formal records")
            derived_states[step_id] = derived_step_status

    for collection, indexed in records.items():
        if referenced[collection] != set(indexed):
            raise ValueError("workflow records are not closed by step references")
    main_brief_id = view.get("brief_id")
    if not isinstance(main_brief_id, str) or main_brief_id not in briefs:
        raise ValueError("workflow task view has an unknown brief")
    if referenced_briefs | {main_brief_id} != set(briefs):
        raise ValueError("workflow briefs are not closed by stable references")
    derived_status = _derive_workflow_status(workflow_id, step_refs)
    if (
        result.get("status") != derived_status
        or view.get("status") != derived_status
        or view.get("workflow_id") != workflow_id
        or view.get("workflow_version") != workflow_version
    ):
        raise ValueError("workflow status or identity contradicts its steps")
    main_brief = briefs[main_brief_id]
    if (
        main_brief.get("status") != derived_status
        or main_brief.get("summary")
        != f"Completed preset research workflow {workflow_id}."
    ):
        raise ValueError("workflow brief contradicts its derived result")
    for reference_field, collection in ref_fields.items():
        expected_ids = list(records[collection])
        if (
            view.get(reference_field) != expected_ids
            or main_brief.get(reference_field) != expected_ids
        ):
            raise ValueError("workflow task view or brief omitted formal records")
