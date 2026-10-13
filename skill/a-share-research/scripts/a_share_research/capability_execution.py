"""Bounded execution of versioned source-capability plans."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Callable, Literal, Mapping, Protocol, Sequence, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from a_share_research.bounded_public_data import (
        MAX_PUBLIC_DATA_KEY_LENGTH as _MAX_WIRE_KEY_LENGTH,
    )
    from a_share_research.bounded_public_data import (
        validate_bounded_public_data,
    )
    from a_share_research.research_result import (
        FieldDisposition,
        OperationExecution,
        OperationObligation,
        OperationOutcome,
    )
    from a_share_research.source_throttle import RequestGateError, SerialRequestGate
else:
    from .bounded_public_data import (
        MAX_PUBLIC_DATA_KEY_LENGTH as _MAX_WIRE_KEY_LENGTH,
    )
    from .bounded_public_data import (
        validate_bounded_public_data,
    )
    from .research_result import (
        FieldDisposition,
        OperationExecution,
        OperationObligation,
        OperationOutcome,
    )
    from .source_throttle import RequestGateError, SerialRequestGate

ExecutionTier = Literal["core", "standard_enrichment", "deep_extension"]
CompletenessMode = Literal["standard", "fast"]

_TIERS = {"core", "standard_enrichment", "deep_extension"}
_MODES = {"standard", "fast"}
_MAX_WIRE_BYTES = 2 * 1024 * 1024
_MAX_OPERATIONS = 64
_MAX_TIMEOUT_SECONDS = 60.0
_MAX_TOTAL_BUDGET_SECONDS = 120.0


class _AttemptRunner(Protocol):
    def __call__(
        self,
        operation: PlannedOperation,
        request: BoundedOperationRequest,
        timeout_seconds: float,
        budget_limited: bool,
    ) -> OperationOutcome: ...


class _FollowupPlanner(Protocol):
    def __call__(
        self, outcomes: tuple[OperationOutcome, ...]
    ) -> tuple[tuple[tuple[str, str], BoundedOperationRequest], ...]: ...


@dataclass(frozen=True)
class OperationExecutionPolicy:
    """Qualified scheduling facts declared by a source operation."""

    provider_id: str
    tier: ExecutionTier
    timeout_seconds: float = 20.0
    minimum_interval_seconds: float = 0.0
    rate_limit_backoffs: tuple[float, ...] = (1.0, 2.0)
    max_requests_per_plan: int = 1

    def __post_init__(self) -> None:
        if (
            not self.provider_id.strip()
            or len(self.provider_id) > _MAX_WIRE_KEY_LENGTH
            or not self.provider_id.isascii()
            or any(
                not (character.isalnum() or character in "._-")
                for character in self.provider_id
            )
            or self.tier not in _TIERS
            or not _bounded_number(self.timeout_seconds, upper=_MAX_TIMEOUT_SECONDS)
            or not _millisecond_exact(self.timeout_seconds)
            or not _bounded_number(self.minimum_interval_seconds, upper=5.0, zero=True)
            or not _millisecond_exact(self.minimum_interval_seconds)
            or len(self.rate_limit_backoffs) > 3
            or not isinstance(self.max_requests_per_plan, int)
            or isinstance(self.max_requests_per_plan, bool)
            or not 1 <= self.max_requests_per_plan <= 4
            or any(
                not _bounded_number(item, upper=60.0, zero=True)
                or not _millisecond_exact(item)
                for item in self.rate_limit_backoffs
            )
        ):
            raise ValueError("operation execution policy is invalid")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "tier": self.tier,
            "timeout_seconds": _seconds(self.timeout_seconds),
            "minimum_interval_seconds": _seconds(self.minimum_interval_seconds),
            "rate_limit_backoffs": [
                _seconds(item) for item in self.rate_limit_backoffs
            ],
            "max_requests_per_plan": self.max_requests_per_plan,
        }


@dataclass(frozen=True)
class PlannedOperation:
    """One declared operation and the facts required to execute it safely."""

    obligation: OperationObligation
    field_dispositions: tuple[FieldDisposition, ...]
    policy: OperationExecutionPolicy

    def to_public_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.obligation.capability_id,
            "operation_id": self.obligation.operation_id,
            **self.policy.to_public_dict(),
        }


@dataclass(frozen=True)
class CapabilityExecutionPlan:
    """Immutable provider-independent plan consumed by the executor."""

    version: str
    completeness_mode: CompletenessMode
    operations: tuple[PlannedOperation, ...]
    total_budget_seconds: float

    def __post_init__(self) -> None:
        owners = [
            (item.obligation.capability_id, item.obligation.operation_id)
            for item in self.operations
        ]
        if (
            not self.version.strip()
            or self.completeness_mode not in _MODES
            or not self.operations
            or len(self.operations) > _MAX_OPERATIONS
            or len(owners) != len(set(owners))
            or not any(item.policy.tier == "core" for item in self.operations)
            or not _bounded_number(
                self.total_budget_seconds,
                upper=_MAX_TOTAL_BUDGET_SECONDS,
            )
        ):
            raise ValueError("capability execution plan is invalid")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "mode": self.completeness_mode,
            "total_budget_seconds": _seconds(self.total_budget_seconds),
            "operations": [item.to_public_dict() for item in self.operations],
        }

    def is_scheduled(self, owner: tuple[str, str]) -> bool:
        return any(
            (item.obligation.capability_id, item.obligation.operation_id) == owner
            and _is_scheduled(item.policy.tier, self.completeness_mode)
            for item in self.operations
        )


@dataclass(frozen=True)
class BoundedOperationRequest:
    """Strict data-only request sent to isolated operation attempts."""

    kind: Literal["security_identity"]
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.kind != "security_identity" or set(self.payload) != {"query", "as_of"}:
            raise ValueError("bounded operation request is invalid")
        if any(
            not isinstance(self.payload.get(key), str) or not self.payload[key]
            for key in ("query", "as_of")
        ):
            raise ValueError("bounded operation request is invalid")
        _encode_wire({"kind": self.kind, "payload": dict(self.payload)})


@dataclass(frozen=True)
class _MaterialOperation:
    obligation: OperationObligation
    field_dispositions: tuple[FieldDisposition, ...]
    execution_policy: OperationExecutionPolicy


def compile_capability_execution_plan(
    *,
    version: str,
    completeness_mode: str,
    operations: Sequence[object],
) -> CapabilityExecutionPlan:
    """Compile source-declared qualification facts without operation-ID mappings."""

    if completeness_mode not in _MODES:
        raise ValueError("capability completeness_mode is unsupported")
    planned: list[PlannedOperation] = []
    for operation in operations:
        obligation = getattr(operation, "obligation", None)
        dispositions = getattr(operation, "field_dispositions", None)
        policy = getattr(operation, "execution_policy", None)
        if (
            not isinstance(obligation, OperationObligation)
            or not isinstance(dispositions, tuple)
            or any(not isinstance(item, FieldDisposition) for item in dispositions)
            or not isinstance(policy, OperationExecutionPolicy)
        ):
            raise ValueError("source operation lacks execution qualification")
        planned.append(PlannedOperation(obligation, dispositions, policy))
    scheduled = [
        item
        for item in planned
        if _is_scheduled(item.policy.tier, cast(CompletenessMode, completeness_mode))
    ]
    derived_budget = _derive_total_budget_seconds(scheduled)
    return CapabilityExecutionPlan(
        version=version,
        completeness_mode=cast(CompletenessMode, completeness_mode),
        operations=tuple(planned),
        total_budget_seconds=derived_budget,
    )


def validate_capability_execution_material(
    *,
    version: str,
    material: Mapping[str, object],
    obligations: Sequence[OperationObligation],
    canonical_operations: Sequence[object],
) -> CapabilityExecutionPlan:
    """Recompile strict public plan material and recheck canonical operations."""

    if set(material) != {"mode", "total_budget_seconds", "operations"}:
        raise ValueError("capability execution material has unknown fields")
    mode = material.get("mode")
    total_budget = material.get("total_budget_seconds")
    operation_values = material.get("operations")
    if (
        not isinstance(mode, str)
        or not isinstance(total_budget, str)
        or not isinstance(operation_values, list)
        or len(operation_values) != len(obligations)
    ):
        raise ValueError("capability execution material is invalid")
    qualified: list[_MaterialOperation] = []
    for obligation, value in zip(obligations, operation_values, strict=True):
        if not isinstance(value, dict) or set(value) != {
            "capability_id",
            "operation_id",
            "provider_id",
            "tier",
            "timeout_seconds",
            "minimum_interval_seconds",
            "rate_limit_backoffs",
            "max_requests_per_plan",
        }:
            raise ValueError("planned operation material is invalid")
        if (
            value.get("capability_id") != obligation.capability_id
            or value.get("operation_id") != obligation.operation_id
        ):
            raise ValueError("planned operation order contradicts its obligations")
        backoff_values = value.get("rate_limit_backoffs")
        if not isinstance(backoff_values, list) or any(
            not isinstance(item, str) for item in backoff_values
        ):
            raise ValueError("planned operation retry policy is invalid")
        provider_id = value.get("provider_id")
        tier = value.get("tier")
        timeout = value.get("timeout_seconds")
        interval = value.get("minimum_interval_seconds")
        max_requests = value.get("max_requests_per_plan")
        if not all(
            isinstance(item, str) for item in (provider_id, tier, timeout, interval)
        ):
            raise ValueError("planned operation policy is invalid")
        if not isinstance(max_requests, int) or isinstance(max_requests, bool):
            raise ValueError("planned operation request bound is invalid")
        qualified.append(
            _MaterialOperation(
                obligation=obligation,
                field_dispositions=(),
                execution_policy=OperationExecutionPolicy(
                    provider_id=cast(str, provider_id),
                    tier=cast(ExecutionTier, tier),
                    timeout_seconds=_parse_seconds(cast(str, timeout)),
                    minimum_interval_seconds=_parse_seconds(cast(str, interval)),
                    rate_limit_backoffs=tuple(
                        _parse_seconds(item) for item in backoff_values
                    ),
                    max_requests_per_plan=max_requests,
                ),
            )
        )
    compiled = compile_capability_execution_plan(
        version=version,
        completeness_mode=mode,
        operations=qualified,
    )
    if material != compiled.to_public_dict() or total_budget != _seconds(
        compiled.total_budget_seconds
    ):
        raise ValueError("capability execution material is not canonical")
    canonical = compile_capability_execution_plan(
        version=version,
        completeness_mode=mode,
        operations=canonical_operations,
    )
    canonical_by_owner = {
        (item.obligation.capability_id, item.obligation.operation_id): item
        for item in canonical.operations
    }
    for item in compiled.operations:
        owner = (item.obligation.capability_id, item.obligation.operation_id)
        authority = canonical_by_owner.get(owner)
        if (
            authority is not None
            and item.to_public_dict() != authority.to_public_dict()
        ):
            raise ValueError("canonical operation execution policy was modified")
    return compiled


def _derive_total_budget_seconds(operations: Sequence[PlannedOperation]) -> float:
    """Derive one plan deadline from concurrent providers' declared worst cases."""

    providers: dict[str, list[OperationExecutionPolicy]] = {}
    for item in operations:
        providers.setdefault(item.policy.provider_id, []).append(item.policy)
    lane_totals: list[float] = []
    for policies in providers.values():
        if any(
            item.minimum_interval_seconds != policies[0].minimum_interval_seconds
            or item.rate_limit_backoffs != policies[0].rate_limit_backoffs
            for item in policies
        ):
            raise ValueError("same-provider operations use inconsistent gate policy")
        request_count = sum(
            (len(item.rate_limit_backoffs) + 1) * item.max_requests_per_plan
            for item in policies
        )
        operation_time = sum(
            (
                item.timeout_seconds * (len(item.rate_limit_backoffs) + 1)
                + sum(item.rate_limit_backoffs)
            )
            * item.max_requests_per_plan
            for item in policies
        )
        pacing_time = policies[0].minimum_interval_seconds * max(request_count - 1, 0)
        lane_totals.append(operation_time + pacing_time)
    derived = max(lane_totals, default=1.0)
    if not _bounded_number(derived, upper=_MAX_TOTAL_BUDGET_SECONDS):
        raise ValueError("derived capability plan budget exceeds its bound")
    return derived


def execute_capability_plan(
    plan: CapabilityExecutionPlan,
    request: BoundedOperationRequest,
    *,
    attempt_runner: _AttemptRunner | None = None,
    followup_planner: _FollowupPlanner | None = None,
) -> tuple[OperationOutcome, ...]:
    """Execute providers concurrently and return outcomes in declaration order."""

    deadline = monotonic() + plan.total_budget_seconds
    immediate: dict[tuple[str, str], OperationOutcome] = {}
    providers: dict[str, list[PlannedOperation]] = {}
    for item in plan.operations:
        owner = (item.obligation.capability_id, item.obligation.operation_id)
        if not _is_scheduled(item.policy.tier, plan.completeness_mode):
            immediate[owner] = _local_outcome(item, "not_requested")
        else:
            providers.setdefault(item.policy.provider_id, []).append(item)

    gates = {
        provider_id: _provider_gate(items, deadline=deadline)
        for provider_id, items in providers.items()
    }
    completed = dict(immediate)
    initial = {
        provider_id: tuple((item, request) for item in items)
        for provider_id, items in providers.items()
    }
    _run_provider_phase(
        initial,
        gates=gates,
        deadline=deadline,
        completed=completed,
        attempt_runner=attempt_runner,
    )
    request_counts = {
        (item.obligation.capability_id, item.obligation.operation_id): 1
        for items in providers.values()
        for item in items
    }
    if followup_planner is not None and monotonic() < deadline:
        followups = followup_planner(_ordered_outcomes(plan, completed))
        followup_lanes: dict[
            str, list[tuple[PlannedOperation, BoundedOperationRequest]]
        ] = {}
        planned_by_owner = {
            (item.obligation.capability_id, item.obligation.operation_id): item
            for item in plan.operations
        }
        for owner, followup_request in followups:
            followup_operation = planned_by_owner.get(owner)
            if (
                followup_operation is None
                or not _is_scheduled(
                    followup_operation.policy.tier, plan.completeness_mode
                )
                or not isinstance(followup_request, BoundedOperationRequest)
            ):
                raise ValueError("followup request does not select a planned operation")
            request_counts[owner] = request_counts.get(owner, 0) + 1
            if request_counts[owner] > followup_operation.policy.max_requests_per_plan:
                raise ValueError(
                    "followup request exceeds its declared operation bound"
                )
            followup_lanes.setdefault(followup_operation.policy.provider_id, []).append(
                (followup_operation, followup_request)
            )
        _run_provider_phase(
            {key: tuple(value) for key, value in followup_lanes.items()},
            gates=gates,
            deadline=deadline,
            completed=completed,
            attempt_runner=attempt_runner,
            merge=True,
        )

    return _ordered_outcomes(plan, completed)


def _ordered_outcomes(
    plan: CapabilityExecutionPlan,
    completed: Mapping[tuple[str, str], OperationOutcome],
) -> tuple[OperationOutcome, ...]:
    return tuple(
        completed.get(
            (item.obligation.capability_id, item.obligation.operation_id),
            _failed_outcome(
                item,
                code="plan_budget_exhausted",
                message="The capability plan budget expired before this operation completed.",
            ),
        )
        for item in plan.operations
    )


def _run_provider_phase(
    lanes: Mapping[str, tuple[tuple[PlannedOperation, BoundedOperationRequest], ...]],
    *,
    gates: Mapping[str, SerialRequestGate],
    deadline: float,
    completed: dict[tuple[str, str], OperationOutcome],
    attempt_runner: _AttemptRunner | None,
    merge: bool = False,
) -> None:
    if not lanes:
        return
    executor = ThreadPoolExecutor(max_workers=len(lanes))
    queues = {provider_id: list(requests) for provider_id, requests in lanes.items()}
    futures: dict[
        Future[tuple[OperationOutcome, ...]],
        tuple[str, PlannedOperation],
    ] = {}

    def submit_next(provider_id: str) -> None:
        if not queues[provider_id]:
            return
        operation, request = queues[provider_id].pop(0)
        future = executor.submit(
            _execute_provider_requests,
            ((operation, request),),
            deadline,
            gate=gates[provider_id],
            attempt_runner=attempt_runner,
        )
        futures[future] = (provider_id, operation)

    try:
        for provider_id in lanes:
            submit_next(provider_id)
        while futures and monotonic() < deadline:
            done, _ = wait(
                futures,
                timeout=max(deadline - monotonic(), 0.0),
                return_when=FIRST_COMPLETED,
            )
            if not done:
                break
            for future in done:
                provider_id, _ = futures.pop(future)
                outcome = future.result()[0]
                owner = (outcome.capability_id, outcome.operation_id)
                completed[owner] = (
                    _merge_operation_outcomes(completed[owner], outcome)
                    if merge and owner in completed
                    else outcome
                )
                submit_next(provider_id)
        unfinished: list[PlannedOperation] = []
        for future, (provider_id, operation) in futures.items():
            future.cancel()
            unfinished.append(operation)
            unfinished.extend(item for item, _ in queues[provider_id])
        for operation in unfinished:
            owner = (
                operation.obligation.capability_id,
                operation.obligation.operation_id,
            )
            budget_outcome = _failed_outcome(
                operation,
                code="plan_budget_exhausted",
                message="The capability plan budget expired before this operation completed.",
            )
            completed[owner] = (
                _merge_operation_outcomes(completed[owner], budget_outcome)
                if merge and owner in completed
                else budget_outcome
            )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _merge_operation_outcomes(
    previous: OperationOutcome, followup: OperationOutcome
) -> OperationOutcome:
    if (
        previous.capability_id != followup.capability_id
        or previous.operation_id != followup.operation_id
        or previous.field_dispositions != followup.field_dispositions
    ):
        raise ValueError("followup outcome contradicts its operation owner")
    source_errors = tuple((*previous.source_errors, *followup.source_errors))
    execution = (
        OperationExecution("failed")
        if source_errors
        else OperationExecution(
            "succeeded",
            query_complete=(
                previous.execution.query_complete is True
                and followup.execution.query_complete is True
            ),
        )
    )
    diagnostics = previous.to_dict()["diagnostics"]
    followup_diagnostics = followup.to_dict()["diagnostics"]
    previous_attempts = diagnostics.get("attempts", 0)
    followup_attempts = followup_diagnostics.get("attempts", 0)
    if isinstance(previous_attempts, int) and isinstance(followup_attempts, int):
        diagnostics["attempts"] = previous_attempts + followup_attempts
    if "timeout_seconds" in followup_diagnostics:
        diagnostics["timeout_seconds"] = followup_diagnostics["timeout_seconds"]
    previous_gate = diagnostics.get("request_gate", ())
    followup_gate = followup_diagnostics.get("request_gate", ())
    if isinstance(previous_gate, list) and isinstance(followup_gate, list):
        diagnostics["request_gate"] = [*previous_gate, *followup_gate]
    elif isinstance(followup_gate, list):
        diagnostics["request_gate"] = list(followup_gate)
    return OperationOutcome(
        capability_id=previous.capability_id,
        operation_id=previous.operation_id,
        execution=execution,
        observations=tuple((*previous.observations, *followup.observations)),
        source_errors=source_errors,
        field_dispositions=previous.field_dispositions,
        diagnostics=diagnostics,
    )


def _execute_provider_lane(
    operations: tuple[PlannedOperation, ...],
    request: BoundedOperationRequest,
    deadline: float,
    *,
    attempt_runner: _AttemptRunner | None = None,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[OperationOutcome, ...]:
    gate = _provider_gate(operations, deadline=deadline, clock=clock, sleeper=sleeper)
    return _execute_provider_requests(
        tuple((item, request) for item in operations),
        deadline,
        gate=gate,
        attempt_runner=attempt_runner,
        clock=clock,
    )


def _provider_gate(
    operations: Sequence[PlannedOperation],
    *,
    deadline: float,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> SerialRequestGate:
    first_policy = operations[0].policy
    if any(
        item.policy.provider_id != first_policy.provider_id
        or item.policy.minimum_interval_seconds != first_policy.minimum_interval_seconds
        or item.policy.rate_limit_backoffs != first_policy.rate_limit_backoffs
        for item in operations
    ):
        raise ValueError("same-provider operations use inconsistent gate policy")
    return SerialRequestGate(
        minimum_interval_seconds=first_policy.minimum_interval_seconds,
        jitter_bounds=(0.0, 0.0),
        rate_limit_backoffs=first_policy.rate_limit_backoffs,
        clock=clock,
        sleeper=_deadline_sleeper(
            deadline=deadline,
            clock=clock,
            sleeper=sleeper,
        ),
    )


def _execute_provider_requests(
    requests: tuple[tuple[PlannedOperation, BoundedOperationRequest], ...],
    deadline: float,
    *,
    gate: SerialRequestGate,
    attempt_runner: _AttemptRunner | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[OperationOutcome, ...]:
    runner = attempt_runner or _spawn_attempt
    outcomes: list[OperationOutcome] = []
    for item, request in requests:
        remaining = deadline - clock()
        if remaining <= 0:
            outcomes.append(
                _failed_outcome(
                    item,
                    code="plan_budget_exhausted",
                    message="The capability plan budget expired before this operation started.",
                )
            )
            continue

        def attempt(
            operation: PlannedOperation = item,
            operation_request: BoundedOperationRequest = request,
        ) -> OperationOutcome:
            attempt_remaining = deadline - clock()
            if attempt_remaining <= 0:
                return _failed_outcome(
                    operation,
                    code="plan_budget_exhausted",
                    message=(
                        "The capability plan budget expired before this operation "
                        "attempt started."
                    ),
                )
            budget_limited = _deadline_limits_attempt(
                remaining_seconds=attempt_remaining,
                operation_timeout_seconds=operation.policy.timeout_seconds,
            )
            outcome = runner(
                operation,
                operation_request,
                min(operation.policy.timeout_seconds, attempt_remaining),
                budget_limited,
            )
            if (
                outcome.execution.state == "failed"
                and len(outcome.source_errors) == 1
                and outcome.source_errors[0].get("code") == "rate_limited"
            ):
                raise _RateLimitedOutcome(outcome)
            return outcome

        try:
            outcome, gate_diagnostics = gate.run(attempt)
        except _PlanDeadlineExpired:
            outcome = _failed_outcome(
                item,
                code="plan_budget_exhausted",
                message=(
                    "The capability plan budget expired while waiting for the "
                    "provider gate."
                ),
            )
            gate_diagnostics = ()
        except RequestGateError as error:
            cause = error.cause
            if isinstance(cause, _RateLimitedOutcome):
                outcome = cause.outcome
            else:
                raise
            gate_diagnostics = error.diagnostics
        except _RateLimitedOutcome as error:
            outcome = error.outcome
            gate_diagnostics = ()
        diagnostics = dict(outcome.diagnostics)
        if gate_diagnostics:
            diagnostics["request_gate"] = [
                {
                    "code": item.code,
                    "delay_seconds": _seconds(item.delay_seconds),
                    **({"attempt": item.attempt} if item.attempt is not None else {}),
                }
                for item in gate_diagnostics
            ]
            retry_count = sum(
                item.code == "rate_limit_backoff" for item in gate_diagnostics
            )
            if retry_count:
                final_attempts = diagnostics.get("attempts", 1)
                diagnostics["attempts"] = (
                    final_attempts if isinstance(final_attempts, int) else 1
                ) + retry_count
        outcomes.append(
            OperationOutcome(
                capability_id=outcome.capability_id,
                operation_id=outcome.operation_id,
                execution=outcome.execution,
                observations=outcome.observations,
                source_errors=outcome.source_errors,
                field_dispositions=outcome.field_dispositions,
                diagnostics=diagnostics,
            )
        )
    return tuple(outcomes)


class _RateLimitedOutcome(Exception):
    code = "rate_limited"

    def __init__(self, outcome: OperationOutcome) -> None:
        super().__init__("The source rate limit was reached.")
        self.outcome = outcome


class _PlanDeadlineExpired(Exception):
    """Private signal raised when gate waiting consumes the remaining plan budget."""


def _deadline_sleeper(
    *,
    deadline: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Callable[[float], None]:
    def bounded_sleep(delay: float) -> None:
        remaining = max(deadline - clock(), 0.0)
        if remaining <= 0:
            raise _PlanDeadlineExpired
        actual = min(delay, remaining)
        sleeper(actual)
        if actual < delay or clock() >= deadline:
            raise _PlanDeadlineExpired

    return bounded_sleep


def _spawn_attempt(
    item: PlannedOperation,
    request: BoundedOperationRequest,
    timeout_seconds: float,
    budget_limited: bool,
) -> OperationOutcome:
    wire_request = {
        "wire_version": "1",
        "operation_id": item.obligation.operation_id,
        "timeout_seconds": timeout_seconds,
        "request": {"kind": request.kind, "payload": dict(request.payload)},
    }
    encoded = _encode_wire(wire_request)
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return _failed_outcome(
            item,
            code="operation_isolation_failed",
            message="The isolated source operation could not be started.",
        )
    try:
        stdout, _ = process.communicate(encoded, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_worker(process)
        return _failed_outcome(
            item,
            code=("plan_budget_exhausted" if budget_limited else "operation_timeout"),
            message=(
                "The capability plan budget expired before this operation completed."
                if budget_limited
                else "The source operation exceeded its bounded timeout."
            ),
        )
    except OSError:
        _terminate_worker(process)
        return _failed_outcome(
            item,
            code="operation_isolation_failed",
            message="The isolated source operation could not be completed.",
        )
    if process.returncode != 0:
        return _failed_outcome(
            item,
            code="operation_isolation_failed",
            message="The isolated source operation could not produce a valid outcome.",
        )
    try:
        payload = _decode_wire(stdout)
        return _outcome_from_wire(item, payload)
    except (TypeError, ValueError):
        return _failed_outcome(
            item,
            code="worker_protocol_error",
            message="The isolated source operation returned an invalid wire document.",
        )


def _terminate_worker(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.communicate()
    except OSError:
        pass


def _outcome_from_wire(
    item: PlannedOperation, payload: Mapping[str, object]
) -> OperationOutcome:
    if (
        set(payload)
        != {
            "wire_version",
            "capability_id",
            "operation_id",
            "execution",
            "observations",
            "source_errors",
            "field_dispositions",
            "diagnostics",
        }
        or payload.get("wire_version") != "1"
    ):
        raise ValueError("operation outcome wire shape is invalid")
    if (
        payload.get("capability_id") != item.obligation.capability_id
        or payload.get("operation_id") != item.obligation.operation_id
    ):
        raise ValueError("operation outcome wire owner is invalid")
    execution = payload.get("execution")
    observations = payload.get("observations")
    errors = payload.get("source_errors")
    dispositions = payload.get("field_dispositions")
    diagnostics = payload.get("diagnostics")
    if (
        not isinstance(execution, dict)
        or set(execution) != {"state", "query_complete"}
        or not isinstance(observations, list)
        or any(not isinstance(value, dict) for value in observations)
        or not isinstance(errors, list)
        or any(not isinstance(value, dict) for value in errors)
        or not isinstance(dispositions, list)
        or not isinstance(diagnostics, dict)
    ):
        raise ValueError("operation outcome wire content is invalid")
    typed_dispositions = tuple(_disposition_from_wire(value) for value in dispositions)
    if typed_dispositions != item.field_dispositions:
        raise ValueError("operation outcome wire dispositions are invalid")
    _validate_wire_public_data(observations)
    _validate_wire_public_data(errors)
    _validate_wire_public_data(diagnostics)
    return OperationOutcome(
        capability_id=cast(str, payload["capability_id"]),
        operation_id=cast(str, payload["operation_id"]),
        execution=OperationExecution(
            cast(str, execution["state"]),  # type: ignore[arg-type]
            query_complete=cast(bool | None, execution["query_complete"]),
        ),
        observations=tuple(cast(list[Mapping[str, object]], observations)),
        source_errors=tuple(cast(list[Mapping[str, object]], errors)),
        field_dispositions=typed_dispositions,
        diagnostics=diagnostics,
    )


def _disposition_from_wire(value: object) -> FieldDisposition:
    if not isinstance(value, dict):
        raise ValueError("operation wire disposition is invalid")
    expected = {"field", "disposition", "destination"}
    if value.get("disposition") == "excluded":
        expected.add("reason")
    if set(value) != expected:
        raise ValueError("operation wire disposition is invalid")
    return FieldDisposition(
        field=cast(str, value["field"]),
        disposition=cast(str, value["disposition"]),  # type: ignore[arg-type]
        destination=cast(str | None, value["destination"]),
        reason=cast(str | None, value.get("reason")),
    )


def _validate_wire_public_data(value: object) -> None:
    _validate_bounded_wire_value(value)


def _validate_bounded_wire_value(value: object) -> None:
    validate_bounded_public_data(value, artifact="operation wire")


def _worker_main() -> int:
    try:
        payload = _decode_wire(sys.stdin.buffer.read(_MAX_WIRE_BYTES + 1))
        if (
            set(payload)
            != {
                "wire_version",
                "operation_id",
                "timeout_seconds",
                "request",
            }
            or payload.get("wire_version") != "1"
        ):
            raise ValueError("operation request wire shape is invalid")
        operation_id = payload.get("operation_id")
        timeout_seconds = payload.get("timeout_seconds")
        request_value = payload.get("request")
        if (
            not isinstance(operation_id, str)
            or not _bounded_number(timeout_seconds, upper=_MAX_TIMEOUT_SECONDS)
            or not isinstance(request_value, dict)
            or set(request_value) != {"kind", "payload"}
        ):
            raise ValueError("operation request wire content is invalid")
        request = BoundedOperationRequest(
            kind=cast(Literal["security_identity"], request_value["kind"]),
            payload=cast(Mapping[str, object], request_value["payload"]),
        )
        from a_share_research.identity_sources import (
            IdentityOperationRequest,
            UrlLibTransport,
            build_default_identity_operations,
        )

        matches = tuple(
            item
            for item in build_default_identity_operations(UrlLibTransport())
            if item.obligation.operation_id == operation_id
        )
        if len(matches) != 1:
            raise ValueError("operation request does not select one known operation")
        outcome = matches[0].execute(
            IdentityOperationRequest(
                query=cast(str, request.payload["query"]),
                as_of=cast(str, request.payload["as_of"]),
            ),
            timeout_seconds=cast(float, timeout_seconds),
        )
        sys.stdout.buffer.write(_encode_wire(_outcome_to_wire(outcome)))
        return 0
    except (OSError, TypeError, ValueError):
        return 2


def _outcome_to_wire(outcome: OperationOutcome) -> dict[str, object]:
    projection = outcome.to_dict()
    return {
        "wire_version": "1",
        "capability_id": outcome.capability_id,
        "operation_id": outcome.operation_id,
        "execution": {
            "state": outcome.execution.state,
            "query_complete": outcome.execution.query_complete,
        },
        "observations": projection["observations"],
        "source_errors": projection["source_errors"],
        "field_dispositions": [item.to_dict() for item in outcome.field_dispositions],
        "diagnostics": projection["diagnostics"],
    }


def _local_outcome(item: PlannedOperation, state: str) -> OperationOutcome:
    return OperationOutcome(
        capability_id=item.obligation.capability_id,
        operation_id=item.obligation.operation_id,
        execution=OperationExecution(state),  # type: ignore[arg-type]
        field_dispositions=item.field_dispositions,
    )


def _failed_outcome(
    item: PlannedOperation, *, code: str, message: str
) -> OperationOutcome:
    return OperationOutcome(
        capability_id=item.obligation.capability_id,
        operation_id=item.obligation.operation_id,
        execution=OperationExecution("failed"),
        source_errors=(
            {
                "source_operation": item.obligation.operation_id,
                "code": code,
                "message": message,
            },
        ),
        field_dispositions=item.field_dispositions,
    )


def _is_scheduled(tier: ExecutionTier, mode: CompletenessMode) -> bool:
    return tier == "core" or (mode == "standard" and tier == "standard_enrichment")


def _bounded_number(value: object, *, upper: float, zero: bool = False) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and (float(value) >= 0 if zero else float(value) > 0)
        and float(value) <= upper
    )


def _deadline_limits_attempt(
    *, remaining_seconds: float, operation_timeout_seconds: float
) -> bool:
    """Report only a strictly shorter plan deadline as the limiting timeout."""

    return remaining_seconds < operation_timeout_seconds


def _millisecond_exact(value: float) -> bool:
    return math.isclose(
        value * 1000,
        round(value * 1000),
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def _seconds(value: float) -> str:
    return format(value, ".3f")


def _parse_seconds(value: str) -> float:
    if not value or len(value) > 32 or not value.isascii():
        raise ValueError("execution duration is invalid")
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError("execution duration is invalid") from error
    if not math.isfinite(parsed):
        raise ValueError("execution duration is invalid")
    return parsed


def _encode_wire(value: object) -> bytes:
    _validate_bounded_wire_value(value)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("operation wire must be JSON-safe") from error
    if len(encoded) > _MAX_WIRE_BYTES:
        raise ValueError("operation wire exceeds its bounded size")
    return encoded


def _decode_wire(encoded: bytes) -> Mapping[str, object]:
    if len(encoded) > _MAX_WIRE_BYTES:
        raise ValueError("operation wire exceeds its bounded size")
    try:
        value = json.loads(
            encoded,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("operation wire is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("operation wire must be an object")
    _validate_bounded_wire_value(value)
    return cast(Mapping[str, object], value)


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    raise SystemExit(_worker_main())
