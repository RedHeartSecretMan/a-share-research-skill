"""Compose accepted market owners behind the overview workflow's internal Seam.

The runner is scoped to one workflow. Its session proof is acquired once and
shared by the index and completed-report owners; these are internal leaf tasks,
not additional public ResearchTask entrypoints.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping, cast

from .fixed_index_observations import FixedIndexOperationRequest
from .fixed_index_sources import build_default_fixed_index_adapter
from .market_foundation import (
    OWNER_IDS,
    OperationRequest,
    build_default_market_foundation_operations,
    fold_market_foundation,
    project_market_foundation_outcome,
)
from .market_session_context import (
    ASIA_SHANGHAI,
    ContextBindings,
    MarketSessionOperationRequest,
)
from .market_session_sources import build_default_market_session_adapter
from .research_result import (
    DirectTaskCalculation,
    DirectTaskClaim,
    DirectTaskLimitation,
    DirectTaskPlan,
    OperationExecution,
    OperationOutcome,
    OperationObligation,
    ResearchDependency,
    UnifiedResearchResult,
    fold_direct_task_research_result,
)

TASK_CAPABILITIES = {
    "overview_session": "market_trading_session",
    "overview_indices": "major_broad_indices",
    "overview_turnover": "whole_market_turnover",
    "overview_margin": "whole_market_margin",
}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _FrozenSession:
    def __init__(
        self, request: MarketSessionOperationRequest, outcome: OperationOutcome
    ) -> None:
        self.request = request
        self.outcome = outcome

    def execute(
        self, request: MarketSessionOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        if request != self.request:
            raise ValueError("overview session request changed during execution")
        return self.outcome


class MarketOverviewFoundation:
    """One workflow's shared session and four fixed owner compositions."""

    def __init__(
        self, request: Mapping[str, object], research_now: datetime | None
    ) -> None:
        self._clock = (
            (lambda: research_now)
            if research_now is not None
            else (lambda: datetime.now(ASIA_SHANGHAI))
        )
        digest = _digest(request)
        self._session_request = MarketSessionOperationRequest(
            cast(str, request["as_of"]),
            ContextBindings(
                digest,
                _digest("market-overview-foundation@1"),
                _digest("market_overview_research@2.0"),
                _digest(
                    OWNER_IDS + ("market_session_context@1", "fixed_four_indices@1")
                ),
                "overview:" + digest,
            ),
        )
        self._session: _FrozenSession | None = None

    def __call__(self, request: dict[str, Any]) -> UnifiedResearchResult:
        task_type = str(request["task_type"])
        capability = TASK_CAPABILITIES[task_type]
        sessions = build_default_market_session_adapter(
            clock=self._clock, execution_clock=self._clock
        )
        if self._session is None:
            self._session = _FrozenSession(
                self._session_request,
                sessions.execute(self._session_request, 120),
            )
        session_outcome = self._session.outcome
        claims: list[DirectTaskClaim] = []
        calculations: list[DirectTaskCalculation] = []
        limitations: list[DirectTaskLimitation] = []
        outcomes: tuple[OperationOutcome, ...]
        obligations: tuple[OperationObligation, ...]
        if task_type == "overview_session":
            outcomes = (session_outcome,)
            obligations = (sessions.obligation,)
            minimum = 1
            keys = _keys(session_outcome)
            if any(
                item.get("data_type") == "market_session_context"
                for item in session_outcome.observations
            ):
                claims.append(DirectTaskClaim(capability, capability, keys, {}))
        elif task_type == "overview_indices":
            adapter = build_default_fixed_index_adapter(
                session_adapter=self._session, clock=self._clock
            )
            outcome = adapter.execute(
                FixedIndexOperationRequest(
                    self._session_request,
                    purpose="completed_session",
                    completed_basis="latest_index_session",
                ),
                120,
            )
            outcomes = (outcome,)
            obligations = (adapter.obligation,)
            minimum = 4
            if outcome.observations:
                claims.append(
                    DirectTaskClaim(capability, capability, _keys(outcome), {})
                )
        else:
            operations = build_default_market_foundation_operations()
            suffix = (
                "a_share_turnover@1"
                if task_type == "overview_turnover"
                else "market_margin@1"
            )
            selected = tuple(
                operation
                for operation in operations
                if operation.operation_id.endswith(suffix)
            )
            obligations = tuple(operation.obligation for operation in selected)
            context = _context(session_outcome)
            minimum = 3
            if context is None:
                outcomes = tuple(
                    OperationOutcome(
                        capability,
                        operation.operation_id,
                        OperationExecution("skipped_research_dependency"),
                        field_dispositions=operation.field_dispositions,
                    )
                    for operation in selected
                )
                limitations.append(
                    DirectTaskLimitation(
                        "overview_completed_session_unproved",
                        "The common completed report session is not proved.",
                    )
                )
            else:
                report_request = OperationRequest(
                    str(context["screen_date"]), str(context["information_cutoff"])
                )
                outcomes = tuple(
                    operation.execute(report_request, 60) for operation in selected
                )
                folded = fold_market_foundation(
                    outcomes, report_request, operations=operations
                )
                keys = tuple(key for outcome in outcomes for key in _keys(outcome))
                if keys:
                    claims.append(
                        DirectTaskClaim(
                            capability + "_partitions",
                            capability,
                            keys,
                            {"report_date": report_request.report_date},
                        )
                    )
                if task_type == "overview_turnover":
                    totals = {
                        "turnover_amount_CNY": cast(dict[str, Any], folded["turnover"])[
                            "total"
                        ]
                    }
                else:
                    totals = cast(dict[str, Any], folded["margin"])["totals"]
                by_owner = {
                    outcome.operation_id: _keys(outcome) for outcome in outcomes
                }
                for field, total in totals.items():
                    if total is None:
                        continue
                    operands = tuple(
                        key for owner in total["operands"] for key in by_owner[owner]
                    )
                    calculations.append(
                        DirectTaskCalculation(
                            capability,
                            capability,
                            operands,
                            {
                                "report_date": report_request.report_date,
                                "metric": field,
                                "value": total["value"],
                                "unit": total["unit"],
                                "operand_evidence_ids": list(operands),
                            },
                        )
                    )
        return fold_direct_task_research_result(
            DirectTaskPlan(
                task_type=task_type,
                as_of=str(request["as_of"]),
                obligations=obligations,
                dependencies=(
                    ResearchDependency(
                        capability, capability, minimum, task_type != "overview_margin"
                    ),
                ),
            ),
            tuple(
                project_market_foundation_outcome(outcome)
                if task_type in {"overview_turnover", "overview_margin"}
                else outcome
                for outcome in outcomes
            ),
            claims=claims,
            calculations=calculations,
            limitations=limitations,
            summary="Completed the fixed overview market foundation step.",
        )


def _keys(outcome: OperationOutcome) -> tuple[str, ...]:
    return tuple(
        str(item.get("id", f"{outcome.operation_id}:{index}"))
        for index, item in enumerate(outcome.observations)
    )


def _context(outcome: OperationOutcome) -> Mapping[str, object] | None:
    for row in outcome.observations:
        if row.get("data_type") == "market_session_context":
            payload = cast(Mapping[str, object], row["payload"])
            return cast(Mapping[str, object], payload["context"])
    return None


def validate_market_overview_foundation_facts(result: Mapping[str, Any]) -> None:
    """Bind every retained foundation fact back to this workflow's session."""
    try:
        _validate_market_overview_foundation_facts(result)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        raise ValueError(
            "overview foundation facts contradict their fixed contract"
        ) from error


def _validate_market_overview_foundation_facts(result: Mapping[str, Any]) -> None:
    from .fixed_index_observations import DeterministicFixedIndexAdapter
    from .market_session_context import (
        CalendarDay,
        CalendarEvidence,
        DeterministicMarketSessionAdapter,
    )

    request = result["plan"]["request_scope"]
    session_request = MarketOverviewFoundation(request, None)._session_request
    registry = result["capability_registry"]["steps"]
    session_rows = [
        row["payload"]
        for row in result["observations"]
        if row["capability_id"] == "market_trading_session"
        and row["payload"].get("data_type") == "market_session_context"
    ]
    if len(session_rows) > 1:
        raise ValueError("overview session context is duplicated")
    context = None
    frozen = None
    if session_rows:
        payload = session_rows[0]["payload"]
        context = payload["context"]
        calendars = tuple(
            CalendarEvidence(
                **{
                    **proof,
                    "days": tuple(CalendarDay(**day) for day in proof["days"]),
                    "auction_times": tuple(proof["auction_times"]),
                }
            )
            for proof in context["calendar_proofs"]
        )
        original_now = datetime.fromisoformat(context["research_now"])
        checked_at = datetime.fromisoformat(context["evidence_checked_at"])
        restored = DeterministicMarketSessionAdapter(
            calendars,
            clock=lambda: original_now,
            execution_clock=lambda: checked_at,
        ).execute(
            MarketSessionOperationRequest(
                session_request.requested_as_of,
                session_request.bindings,
                frozen_context=context,
                expected_context_digest=context["digest"],
            ),
            1,
        )
        if restored.to_dict()["observations"] != session_rows:
            raise ValueError("overview session proof contradicts the frozen request")
        frozen = _FrozenSession(session_request, restored)
    for task_type, capability in TASK_CAPABILITIES.items():
        step = registry[capability]
        plan = step["leaf_plan"]
        if plan is None:
            continue
        minimum = (
            4
            if task_type == "overview_indices"
            else 3
            if task_type in {"overview_turnover", "overview_margin"}
            else 1
        )
        if plan["research_dependencies"] != [
            ResearchDependency(
                capability, capability, minimum, task_type != "overview_margin"
            ).to_dict()
        ]:
            raise ValueError("overview foundation dependency authority is invalid")
        rows = [
            row for row in result["observations"] if row["capability_id"] == capability
        ]
        step_ref = next(
            step
            for step in result["task_views"]["research_workflow"]["steps"]
            if step["step_id"] == capability
        )
        if task_type in {"overview_session", "overview_indices"}:
            if step_ref["calculation_ids"]:
                raise ValueError(
                    "overview session and indices have no derived calculations"
                )
            operation = result["coverage"]["steps"][capability]["leaf"][capability][
                "operations"
            ][plan["operation_obligations"][0]["operation_id"]]
            if operation["execution_state"] == "succeeded" and (
                (task_type == "overview_session" and context is None)
                or (task_type == "overview_indices" and len(rows) != 4)
            ):
                raise ValueError(
                    "overview owner success requires its nonempty fixed proof"
                )
        claims = [
            claim for claim in result["claims"] if claim["id"] in step_ref["claim_ids"]
        ]
        has_claim = bool(rows) and (
            task_type != "overview_session" or context is not None
        )
        expected_claims = (
            []
            if not has_claim
            else [
                {
                    "kind": capability
                    if task_type in {"overview_session", "overview_indices"}
                    else capability + "_partitions",
                    "evidence_use": capability,
                    "observation_ids": [row["id"] for row in rows],
                    "payload": {}
                    if task_type in {"overview_session", "overview_indices"}
                    else {
                        "report_date": context["screen_date"]
                        if context is not None
                        else None
                    },
                }
            ]
        )
        if [
            {
                key: claim[key]
                for key in ("kind", "evidence_use", "observation_ids", "payload")
            }
            for claim in claims
        ] != expected_claims:
            raise ValueError(
                "overview foundation claim contradicts its fixed projection"
            )
        if task_type == "overview_session":
            expected = build_default_market_session_adapter().obligation.to_dict()
            if plan["operation_obligations"] != [expected]:
                raise ValueError("overview session owner authority is invalid")
        elif task_type == "overview_indices":
            if frozen is None:
                if rows:
                    raise ValueError("overview indices lack session proof")
                continue
            expected = build_default_fixed_index_adapter(
                session_adapter=frozen
            ).obligation.to_dict()
            if plan["operation_obligations"] != [expected]:
                raise ValueError("overview fixed-index owner authority is invalid")
            if context is None:
                raise ValueError("overview fixed-index session context is missing")
            checked = max(
                [datetime.fromisoformat(context["evidence_checked_at"])]
                + [
                    datetime.fromisoformat(row["payload"]["retrieved_at"])
                    for row in rows
                ]
            )
            normalized = DeterministicFixedIndexAdapter(
                [row["payload"]["payload"]["observation"] for row in rows],
                session_adapter=frozen,
                clock=lambda: checked,
            ).execute(
                FixedIndexOperationRequest(
                    session_request, completed_basis="latest_index_session"
                ),
                1,
            )
            if normalized.to_dict()["observations"] != [row["payload"] for row in rows]:
                raise ValueError(
                    "overview index facts contradict fixed identities or session semantics"
                )
        else:
            for row in rows:
                if context is None:
                    raise ValueError("overview report lacks completed-session proof")
                observation = row["payload"]["observation"]
                if observation["report_date"] != context[
                    "screen_date"
                ] or datetime.fromisoformat(
                    observation["published_at"]
                ) > datetime.fromisoformat(context["information_cutoff"]):
                    raise ValueError(
                        "overview report contradicts the frozen completed session"
                    )
