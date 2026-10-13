"""Authoritative accounting at the bounded provider invocation seam."""

from __future__ import annotations

import math
from dataclasses import dataclass
from random import uniform
from threading import Condition, Event, Thread, get_ident
from time import monotonic, sleep
from typing import Callable, Generic, Literal, Protocol, TypeVar, cast
from weakref import WeakKeyDictionary, WeakSet

from .source_throttle import (
    RequestGateBudgetError,
    RequestGateDiagnostic,
    RequestGateError,
    SerialRequestGate,
)

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
E = TypeVar("E", bound="_IssuedEvidence")


@dataclass(frozen=True)
class ProviderLanePolicy:
    """One owner's finite invocation and cancellation bounds."""

    actual_attempt_budget: int
    timeout_seconds: float
    call_timeout_seconds: float | None = None
    cancellation_grace_seconds: float = 0.05
    minimum_interval_seconds: float = 0.0
    jitter_bounds: tuple[float, float] = (0.0, 0.0)
    rate_limit_backoffs: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.actual_attempt_budget, bool)
            or not isinstance(self.actual_attempt_budget, int)
            or self.actual_attempt_budget < 1
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or (
                self.call_timeout_seconds is not None
                and (
                    not math.isfinite(self.call_timeout_seconds)
                    or self.call_timeout_seconds <= 0
                )
            )
            or not math.isfinite(self.cancellation_grace_seconds)
            or self.cancellation_grace_seconds < 0
            or not all(
                math.isfinite(value)
                for value in (
                    self.minimum_interval_seconds,
                    *self.jitter_bounds,
                    *self.rate_limit_backoffs,
                )
            )
        ):
            raise ValueError("provider lane policy is invalid")
        SerialRequestGate(
            minimum_interval_seconds=self.minimum_interval_seconds,
            jitter_bounds=self.jitter_bounds,
            rate_limit_backoffs=self.rate_limit_backoffs,
        )


class CancellationToken:
    """Read-only cooperative cancellation signal; it cannot attest quiescence."""

    def __init__(self, event: Event) -> None:
        self._event = event

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout_seconds: float | None = None) -> bool:
        return self._event.wait(timeout_seconds)


class ProviderCallAdapter(Protocol[T_co]):
    """Invoke exactly one owned provider call without hidden retries."""

    def invoke(self, cancellation: CancellationToken) -> T_co: ...


class ProviderOperationAdapter(Protocol[T_co]):
    """Coordinate owned calls only through this execution's leaf invoker."""

    def execute(self, invocation: ProviderInvocation) -> T_co: ...


class ProductionInvocationAdapter(Generic[T]):
    """Invoke an existing synchronous provider call once."""

    def __init__(self, request: Callable[[], T]) -> None:
        self._request = request

    def invoke(self, cancellation: CancellationToken) -> T:
        return self._request()

    def execute(self, invocation: ProviderInvocation) -> T:
        return invocation.call(self)


class _IssuedEvidence:
    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("provider lane evidence can only be issued by its lane")


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class AttemptEvidence(_IssuedEvidence):
    """Immutable evidence of an actual invocation, never a caller's count."""

    provider_group: str
    sequence: int
    state: Literal["succeeded", "failed", "active"]


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class QuiescenceEvidence(_IssuedEvidence):
    """The lane's observation of terminal execution or retained occupancy."""

    provider_group: str
    state: Literal["quiescent", "active", "poisoned"]


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class ProviderLaneResult(_IssuedEvidence, Generic[T]):
    """Bounded return with Module-issued accounting and quiescence evidence."""

    status: Literal["succeeded", "failed", "timed_out", "not_started"]
    value: T | None
    failure_code: str | None
    attempts: tuple[AttemptEvidence, ...]
    quiescence: QuiescenceEvidence
    diagnostics: tuple[RequestGateDiagnostic, ...]


def _mint(evidence_type: type[E], **fields: object) -> E:
    evidence = object.__new__(evidence_type)
    for name, value in fields.items():
        object.__setattr__(evidence, name, value)
    return evidence


class _InvocationUnavailable(Exception):
    """An expired or foreign-thread invoker cannot start a provider call."""


@dataclass
class _Attempt:
    started_at: float
    state: Literal["succeeded", "failed", "active"] = "active"


class _Execution(Generic[T]):
    def __init__(self) -> None:
        self.done = Event()
        self.cancel = Event()
        self.attempts: list[_Attempt] = []
        self.diagnostics: list[RequestGateDiagnostic] = []
        self.value: T | None = None
        self.failure_code: str | None = None
        self.worker_ident: int | None = None
        self.open = True
        self.poisoned = False
        self.call_timeout_seconds: float | None = None


@dataclass
class _Group:
    gate: SerialRequestGate
    timing: tuple[float, tuple[float, float], tuple[float, ...]]
    execution: _Execution[object] | None = None


@dataclass(frozen=True)
class _InvocationState:
    execution: _Execution[object]
    group: _Group
    condition: Condition
    budget: int


_INVOCATION_STATES: WeakKeyDictionary[ProviderInvocation, _InvocationState] = (
    WeakKeyDictionary()
)


class ProviderInvocation:
    """Opaque execution-owned leaf entry shared by nested/page requests.

    Only the owning worker can use the issued invoker while its execution is
    open. No mutable budget or execution fields are exposed on the instance.
    """

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("provider invokers can only be issued by their lane")

    def call(self, adapter: ProviderCallAdapter[T]) -> T:
        if type(self) is not ProviderInvocation:
            raise _InvocationUnavailable
        state = _INVOCATION_STATES.get(self)
        if state is None:
            raise _InvocationUnavailable
        execution = state.execution
        with state.condition:
            self._check_owner(state)
            remaining = state.budget - len(execution.attempts)
            if remaining <= 0:
                raise RequestGateBudgetError(())

        def invoke() -> T:
            with state.condition:
                self._check_owner(state)
                if len(execution.attempts) >= state.budget:
                    raise RequestGateBudgetError(())
                attempt = _Attempt(started_at=monotonic())
                execution.attempts.append(attempt)
                state.condition.notify_all()
            try:
                value = adapter.invoke(CancellationToken(execution.cancel))
            except BaseException:
                with state.condition:
                    attempt.state = "failed"
                    state.condition.notify_all()
                raise
            with state.condition:
                attempt.state = "succeeded"
                state.condition.notify_all()
            return value

        try:
            value, diagnostics = state.group.gate.run_bounded(
                invoke, actual_attempt_budget=remaining
            )
        except (RequestGateBudgetError, RequestGateError) as error:
            with state.condition:
                execution.diagnostics.extend(error.diagnostics)
            raise
        with state.condition:
            execution.diagnostics.extend(diagnostics)
        return value

    @staticmethod
    def _check_owner(state: _InvocationState) -> None:
        if (
            not state.execution.open
            or state.execution.cancel.is_set()
            or state.execution.worker_ident != get_ident()
            or state.group.execution is not state.execution
        ):
            raise _InvocationUnavailable


class ProviderLanes:
    """Serialize each group through quiescence; retain poisoned occupancy.

    At most three groups are active, including calls refusing cancellation.
    Timeout bounds cover queueing plus execution; cancellation grace is added
    only for a started worker. Quiescence is observed from worker termination.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
        jitter: Callable[[float, float], float] = uniform,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter
        self._condition = Condition()
        self._groups: dict[str, _Group] = {}
        self._active_groups = 0
        self._proofs: WeakSet[_IssuedEvidence] = WeakSet()

    def is_authoritative(self, evidence: object) -> bool:
        """Reject constructed, copied or other-lane evidence at the owning seam."""
        with self._condition:
            return (
                type(evidence)
                in {AttemptEvidence, QuiescenceEvidence, ProviderLaneResult}
                and evidence in self._proofs
            )

    def quiescence(self, provider_group: str) -> QuiescenceEvidence:
        """Observe recovery without trusting an Adapter's cancellation claim."""
        self._validate_group(provider_group)
        with self._condition:
            group = self._groups.get(provider_group)
            execution = group.execution if group is not None else None
            state = (
                "quiescent"
                if execution is None
                else "poisoned"
                if execution.poisoned
                else "active"
            )
            evidence = _mint(
                QuiescenceEvidence, provider_group=provider_group, state=state
            )
            self._proofs.add(evidence)
            return evidence

    def run(
        self,
        *,
        provider_group: str,
        adapter: ProviderOperationAdapter[T],
        policy: ProviderLanePolicy,
    ) -> ProviderLaneResult[T]:
        """Run owned leaf calls with one finite initial/retry/page budget."""
        self._validate_group(provider_group)
        deadline = monotonic() + policy.timeout_seconds
        timing = (
            policy.minimum_interval_seconds,
            policy.jitter_bounds,
            policy.rate_limit_backoffs,
        )
        with self._condition:
            group = self._groups.get(provider_group)
            if group is None:
                group = _Group(
                    SerialRequestGate(
                        minimum_interval_seconds=policy.minimum_interval_seconds,
                        jitter_bounds=policy.jitter_bounds,
                        rate_limit_backoffs=policy.rate_limit_backoffs,
                        clock=self._clock,
                        sleeper=self._sleeper,
                        jitter=self._jitter,
                    ),
                    timing,
                )
                self._groups[provider_group] = group
            elif group.timing != timing:
                raise ValueError(
                    "same-provider operations use inconsistent gate policy"
                )
            while group.execution is not None or self._active_groups >= 3:
                if group.execution is not None and group.execution.poisoned:
                    return self._result(
                        provider_group,
                        _Execution[T](),
                        status="not_started",
                        failure_code="provider_lane_poisoned",
                        quiescence_state="poisoned",
                    )
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return self._result(
                        provider_group,
                        _Execution[T](),
                        status="not_started",
                        failure_code="provider_lane_busy",
                        quiescence_state=(
                            "active" if group.execution is not None else "quiescent"
                        ),
                    )
                self._condition.wait(remaining)
            execution: _Execution[T] = _Execution()
            execution.call_timeout_seconds = policy.call_timeout_seconds
            group.execution = cast(_Execution[object], execution)
            self._active_groups += 1
            worker = Thread(
                target=self._execute,
                args=(provider_group, group, execution, adapter, policy),
                daemon=True,
            )
            try:
                worker.start()
            except RuntimeError:
                group.execution = None
                self._active_groups -= 1
                self._condition.notify_all()
                return self._result(
                    provider_group,
                    execution,
                    status="not_started",
                    failure_code="provider_invocation_unavailable",
                    quiescence_state="quiescent",
                )
        with self._condition:
            while not execution.done.is_set():
                remaining = deadline - monotonic()
                if (
                    execution.call_timeout_seconds is not None
                    and execution.attempts
                    and execution.attempts[-1].state == "active"
                ):
                    remaining = min(
                        remaining,
                        execution.attempts[-1].started_at
                        + execution.call_timeout_seconds
                        - monotonic(),
                    )
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if execution.done.is_set():
                return self._result(
                    provider_group,
                    execution,
                    status="succeeded" if execution.failure_code is None else "failed",
                    failure_code=execution.failure_code,
                    quiescence_state="quiescent",
                )
        execution.cancel.set()
        execution.done.wait(policy.cancellation_grace_seconds)
        with self._condition:
            if not execution.done.is_set():
                execution.poisoned = True
            return self._result(
                provider_group,
                execution,
                status="timed_out",
                failure_code="provider_timeout",
                quiescence_state=("poisoned" if execution.poisoned else "quiescent"),
            )

    def _execute(
        self,
        provider_group: str,
        group: _Group,
        execution: _Execution[T],
        adapter: ProviderOperationAdapter[T],
        policy: ProviderLanePolicy,
    ) -> None:
        execution.worker_ident = get_ident()
        invocation = object.__new__(ProviderInvocation)
        with self._condition:
            _INVOCATION_STATES[invocation] = _InvocationState(
                cast(_Execution[object], execution),
                group,
                self._condition,
                policy.actual_attempt_budget,
            )
        try:
            execution.value = adapter.execute(invocation)
        except RequestGateBudgetError:
            execution.failure_code = "actual_attempt_budget_exhausted"
        except _InvocationUnavailable:
            execution.failure_code = "provider_invocation_unavailable"
        except RequestGateError:
            execution.failure_code = "rate_limited"
        except BaseException as error:
            try:
                rate_limited = getattr(error, "code", None) == "rate_limited"
            except BaseException:
                rate_limited = False
            execution.failure_code = (
                "rate_limited" if rate_limited else "provider_call_failed"
            )
        finally:
            with self._condition:
                execution.open = False
                _INVOCATION_STATES.pop(invocation, None)
                group.execution = None
                self._active_groups -= 1
                execution.done.set()
                self._condition.notify_all()

    def _result(
        self,
        provider_group: str,
        execution: _Execution[T],
        *,
        status: Literal["succeeded", "failed", "timed_out", "not_started"],
        failure_code: str | None,
        quiescence_state: Literal["quiescent", "active", "poisoned"],
    ) -> ProviderLaneResult[T]:
        attempts = tuple(
            _mint(
                AttemptEvidence,
                provider_group=provider_group,
                sequence=sequence,
                state=attempt.state,
            )
            for sequence, attempt in enumerate(execution.attempts, start=1)
        )
        quiescence = _mint(
            QuiescenceEvidence,
            provider_group=provider_group,
            state=quiescence_state,
        )
        result: ProviderLaneResult[T] = _mint(
            ProviderLaneResult,
            status=status,
            value=execution.value if status == "succeeded" else None,
            failure_code=failure_code,
            attempts=attempts,
            quiescence=quiescence,
            diagnostics=tuple(execution.diagnostics),
        )
        self._proofs.update((*attempts, quiescence, result))
        return result

    @staticmethod
    def _validate_group(provider_group: str) -> None:
        if (
            not provider_group
            or len(provider_group) > 128
            or not provider_group.isascii()
            or any(
                not (character.isalnum() or character in "._-")
                for character in provider_group
            )
        ):
            raise ValueError("provider group is invalid")
