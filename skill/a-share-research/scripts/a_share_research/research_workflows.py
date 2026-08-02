"""Fixed, versioned workflows composed from existing research tasks."""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

LeafTaskRunner = Callable[[dict[str, Any]], dict[str, Any]]

WORKFLOW_VERSION = "1.0"
WORKFLOW_IDS = {
    "single_security_valuation",
    "valuation_comparison",
    "theme_report_research",
    "new_security_research",
}
DIAGNOSTIC_FIELDS = (
    "evidence",
    "conflicts",
    "source_errors",
    "degradations",
    "limitations",
)


def build_research_workflow_result(
    request: dict[str, Any],
    run_leaf_task: LeafTaskRunner,
) -> dict[str, Any]:
    """Compile and execute one approved workflow through leaf ResearchTasks."""

    workflow_id, inputs = _validate_workflow_request(request)
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
        subjects = _result_subjects(steps[0])
        status = _step_result_status(steps[0])
        required_step_ids = {"valuation"}
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
        subjects = _result_subjects(steps[0])
        status = _step_result_status(steps[0])
        required_step_ids = {"comparison"}
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
        subjects = _result_subjects(steps[0])
        status = _step_result_status(steps[0])
        required_step_ids = {"report_research"}
    else:
        plan = _new_security_plan()
        steps, subjects, status = _run_new_security_workflow(
            request,
            inputs,
            plan,
            run_leaf_task,
        )
        required_step_ids = {item["step_id"] for item in plan[1:]}

    diagnostics = _project_diagnostics(steps)
    return {
        "schema_version": request["schema_version"],
        "status": status,
        "subjects": subjects,
        "workflow": {
            "id": workflow_id,
            "requested_version": WORKFLOW_VERSION,
            "executed_version": WORKFLOW_VERSION,
        },
        "research": {
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
        },
        "plan": copy.deepcopy(plan),
        "steps": steps,
        "brief": _workflow_brief(steps, required_step_ids),
        **diagnostics,
    }


def _validate_workflow_request(
    request: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if request["window"] is not None:
        raise ValueError("research_workflow window must be null")
    parameters = request["parameters"]
    if set(parameters) != {"workflow", "inputs"}:
        raise ValueError(
            "research_workflow parameters must contain only workflow and inputs"
        )
    workflow = parameters["workflow"]
    inputs = parameters["inputs"]
    if not isinstance(workflow, dict) or set(workflow) != {"id", "version"}:
        raise ValueError("research_workflow parameters.workflow is invalid")
    workflow_id = workflow["id"]
    if not isinstance(workflow_id, str) or workflow_id not in WORKFLOW_IDS:
        raise ValueError("research_workflow id is not supported")
    if workflow["version"] != WORKFLOW_VERSION:
        raise ValueError("research_workflow version is not supported")
    if not isinstance(inputs, dict):
        raise ValueError("research_workflow parameters.inputs must be an object")

    if workflow_id == "single_security_valuation":
        _require_subject_count(request["subjects"], minimum=1, maximum=1)
        _require_inputs(inputs, {"target_pe"})
        _require_target_pe(inputs["target_pe"])
    elif workflow_id == "valuation_comparison":
        _require_subject_count(request["subjects"], minimum=2, maximum=10)
        _require_inputs(inputs, {"target_pe"})
        _require_target_pe(inputs["target_pe"])
    elif workflow_id == "theme_report_research":
        if request["subjects"]:
            raise ValueError("theme_report_research takes no subjects")
        _require_inputs(
            inputs,
            {
                "query",
                "published_from",
                "published_to",
                "limit",
                "verify_documents",
            },
        )
        _require_string_array(inputs["query"], "theme_report_research query")
        _require_window(
            inputs,
            start_field="published_from",
            end_field="published_to",
            as_of=request["as_of"],
            allow_future_days=0,
        )
        _require_limit(inputs["limit"])
        _require_boolean(inputs["verify_documents"], "verify_documents")
    else:
        _require_subject_count(request["subjects"], minimum=1, maximum=1)
        _require_new_security_subject(request["subjects"][0])
        _require_inputs(
            inputs,
            {
                "target_pe",
                "report_window",
                "market_window",
                "lockup_window",
                "fund_flow_period",
                "limit",
                "verify_documents",
            },
        )
        _require_target_pe(inputs["target_pe"])
        _require_nested_window(
            inputs["report_window"],
            start_field="published_from",
            end_field="published_to",
            as_of=request["as_of"],
            allow_future_days=0,
            name="report_window",
        )
        _require_nested_window(
            inputs["market_window"],
            start_field="observed_from",
            end_field="observed_to",
            as_of=request["as_of"],
            allow_future_days=0,
            name="market_window",
        )
        _require_nested_window(
            inputs["lockup_window"],
            start_field="observed_from",
            end_field="observed_to",
            as_of=request["as_of"],
            allow_future_days=90,
            name="lockup_window",
        )
        if inputs["fund_flow_period"] not in {"5d", "10d"}:
            raise ValueError("new_security_research fund_flow_period must be 5d or 10d")
        _require_limit(inputs["limit"])
        _require_boolean(inputs["verify_documents"], "verify_documents")
    return workflow_id, inputs


def _run_new_security_workflow(
    request: dict[str, Any],
    inputs: dict[str, Any],
    plan: list[dict[str, Any]],
    run_leaf_task: LeafTaskRunner,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    identity_request = _leaf_request(
        request,
        task_type="security_identity",
        subjects=request["subjects"],
        window=None,
        parameters={},
    )
    identity_step = _execute_step(plan[0], identity_request, run_leaf_task)
    canonical_subject = _canonical_subject(identity_step.get("result"))
    if canonical_subject is None:
        identity_step["state"] = "blocked"
        identity_step["limitations"] = [
            {
                "code": "workflow_identity_not_canonical",
                "message": (
                    "The identity step did not establish one canonical A-share "
                    "security for dependent workflow steps."
                ),
            }
        ]
        steps = [identity_step]
        steps.extend(_skipped_step(item, "identity") for item in plan[1:])
        return steps, [], "blocked"

    canonical_clue = (
        f"{canonical_subject['security']['exchange']}:"
        f"{canonical_subject['security']['code']}"
    )
    class_count = request["subjects"][0]["issuer_security_class_count"]
    limit = inputs["limit"]
    child_specs = [
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
                "observed_from": inputs["market_window"]["observed_to"],
                "observed_to": inputs["market_window"]["observed_to"],
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

    substantive_statuses = [_step_result_status(item) for item in steps[1:]]
    identity_status = _step_result_status(identity_step)
    if identity_status == "supported" and all(
        item == "supported" for item in substantive_statuses
    ):
        status = "supported"
    elif any(item != "blocked" for item in substantive_statuses):
        status = "limited"
    else:
        status = "blocked"
    return steps, [canonical_subject], status


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


def _plan_step(
    step_id: str,
    task_type: str,
    *,
    depends_on: list[str] | None = None,
    criticality: str = "required_evidence",
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "task_type": task_type,
        "criticality": criticality,
        "depends_on": [] if depends_on is None else depends_on,
        "selected": True,
    }


def _execute_step(
    plan_step: dict[str, Any],
    request: dict[str, Any],
    run_leaf_task: LeafTaskRunner,
) -> dict[str, Any]:
    result = run_leaf_task(request)
    status = _validated_result_status(result)
    return {
        **copy.deepcopy(plan_step),
        "state": status,
        "research_task": copy.deepcopy(request),
        "result": result,
    }


def _skipped_step(plan_step: dict[str, Any], dependency: str) -> dict[str, Any]:
    return {
        **copy.deepcopy(plan_step),
        "state": "skipped_dependency",
        "research_task": None,
        "result": None,
        "skip": {
            "code": "workflow_step_skipped_dependency",
            "blocked_by": [dependency],
        },
        "limitations": [
            {
                "code": "workflow_step_skipped_dependency",
                "message": "The workflow step was not run because identity was blocked.",
                "blocked_by": [dependency],
            }
        ],
    }


def _canonical_subject(result: object) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("status") not in {
        "supported",
        "limited",
    }:
        return None
    conflicts = result.get("conflicts")
    candidates = result.get("candidates")
    if conflicts or not isinstance(candidates, list) or len(candidates) != 1:
        return None
    candidate = candidates[0]
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


def _project_diagnostics(steps: list[dict[str, Any]]) -> dict[str, list[Any]]:
    projected: dict[str, list[Any]] = {field: [] for field in DIAGNOSTIC_FIELDS}
    for step in steps:
        result = step.get("result")
        if isinstance(result, dict):
            for field in DIAGNOSTIC_FIELDS:
                values = result.get(field, [])
                if isinstance(values, list):
                    projected[field].extend(
                        {"step_id": step["step_id"], "item": copy.deepcopy(item)}
                        for item in values
                    )
        limitations = step.get("limitations", [])
        if isinstance(limitations, list):
            projected["limitations"].extend(
                {"step_id": step["step_id"], "item": copy.deepcopy(item)}
                for item in limitations
            )
    return projected


def _workflow_brief(
    steps: list[dict[str, Any]],
    required_step_ids: set[str],
) -> dict[str, Any]:
    missing = [
        item["step_id"]
        for item in steps
        if item["step_id"] in required_step_ids
        and item["state"] in {"blocked", "skipped_dependency"}
    ]
    gate_satisfied = all(
        item["state"] in {"supported", "limited"}
        for item in steps
        if item["criticality"] == "gate" and item["selected"]
    )
    required_evidence_satisfied = any(
        item["state"] in {"supported", "limited"}
        for item in steps
        if item["criticality"] == "required_evidence" and item["selected"]
    )
    return {
        "planned_step_count": len(steps),
        "selected_step_count": sum(bool(item["selected"]) for item in steps),
        "executed_step_count": sum(item.get("result") is not None for item in steps),
        "minimum_evidence_satisfied": (gate_satisfied and required_evidence_satisfied),
        "missing_required_steps": missing,
        "state_counts": {
            state: sum(item["state"] == state for item in steps)
            for state in ("supported", "limited", "blocked", "skipped_dependency")
        },
    }


def _result_subjects(step: dict[str, Any]) -> list[dict[str, Any]]:
    result = step.get("result")
    if not isinstance(result, dict):
        return []
    subjects = result.get("subjects")
    if not isinstance(subjects, list) or any(
        not isinstance(item, dict) for item in subjects
    ):
        return []
    return copy.deepcopy(subjects)


def _step_result_status(step: dict[str, Any]) -> str:
    result = step.get("result")
    if not isinstance(result, dict):
        return "blocked"
    return _validated_result_status(result)


def _validated_result_status(result: dict[str, Any]) -> str:
    status = result.get("status")
    if not isinstance(status, str) or status not in {
        "supported",
        "limited",
        "blocked",
    }:
        raise ValueError("workflow leaf result has an invalid status")
    return status


def _require_inputs(inputs: dict[str, Any], fields: set[str]) -> None:
    if set(inputs) != fields:
        raise ValueError("research_workflow inputs do not match the workflow contract")


def _require_subject_count(subjects: list[Any], *, minimum: int, maximum: int) -> None:
    if not minimum <= len(subjects) <= maximum or any(
        not isinstance(item, dict) for item in subjects
    ):
        raise ValueError(
            "research_workflow subjects do not match the workflow contract"
        )


def _require_new_security_subject(subject: dict[str, Any]) -> None:
    clue = subject.get("clue")
    class_count = subject.get("issuer_security_class_count")
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("new_security_research subject requires a non-empty clue")
    if (
        not isinstance(class_count, int)
        or isinstance(class_count, bool)
        or class_count < 1
    ):
        raise ValueError(
            "new_security_research requires a positive issuer_security_class_count"
        )


def _require_target_pe(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("workflow target_pe must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("workflow target_pe must be a decimal string") from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("workflow target_pe must be positive")


def _require_string_array(value: object, name: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{name} must be a non-empty string array")


def _require_limit(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("workflow limit must be from 1 to 100")


def _require_boolean(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"workflow {name} must be boolean")


def _require_nested_window(
    value: object,
    *,
    start_field: str,
    end_field: str,
    as_of: str,
    allow_future_days: int,
    name: str,
) -> None:
    if not isinstance(value, dict) or set(value) != {start_field, end_field}:
        raise ValueError(f"new_security_research {name} is invalid")
    _require_window(
        value,
        start_field=start_field,
        end_field=end_field,
        as_of=as_of,
        allow_future_days=allow_future_days,
    )


def _require_window(
    value: dict[str, Any],
    *,
    start_field: str,
    end_field: str,
    as_of: str,
    allow_future_days: int,
) -> None:
    start = _strict_date(value.get(start_field), start_field)
    end = _strict_date(value.get(end_field), end_field)
    if start > end:
        raise ValueError("workflow window starts after it ends")
    if end > date.fromisoformat(as_of) + timedelta(days=allow_future_days):
        raise ValueError("workflow window exceeds its research boundary")


def _strict_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"workflow {field} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"workflow {field} must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"workflow {field} must use YYYY-MM-DD")
    return parsed
