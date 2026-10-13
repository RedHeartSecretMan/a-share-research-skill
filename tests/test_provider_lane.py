from __future__ import annotations

import copy
import unittest
from dataclasses import FrozenInstanceError, replace
from threading import Event, Lock, Thread
from time import monotonic, sleep

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import TransportError  # noqa: E402
from a_share_research.provider_lane import (  # noqa: E402
    AttemptEvidence,
    CancellationToken,
    ProductionInvocationAdapter,
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLanes,
)
from a_share_research.source_throttle import SerialRequestGate  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def unpaced_gate() -> SerialRequestGate:
    return SerialRequestGate(
        minimum_interval_seconds=0,
        jitter_bounds=(0, 0),
        rate_limit_backoffs=(0,),
    )


class ProviderLaneTests(unittest.TestCase):
    def test_one_actual_attempt_budget_stops_internal_rate_limit_retry(self) -> None:
        calls: list[str] = []

        def request() -> str:
            calls.append("provider invoked")
            raise TransportError("rate_limited", "sanitized")

        lanes = ProviderLanes()
        result = lanes.run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(request),
            policy=ProviderLanePolicy(
                actual_attempt_budget=1,
                timeout_seconds=1,
                rate_limit_backoffs=(0,),
            ),
        )

        self.assertEqual(calls, ["provider invoked"])
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].sequence, 1)
        self.assertEqual(result.attempts[0].state, "failed")
        self.assertEqual(result.quiescence.state, "quiescent")
        self.assertTrue(lanes.is_authoritative(result))

    def test_timeout_retains_same_group_until_actual_invocation_returns(self) -> None:
        released = Event()
        active = Event()
        successor_calls: list[bool] = []

        def slow_request() -> str:
            active.set()
            try:
                released.wait(0.15)
                return "late"
            finally:
                active.clear()

        lanes = ProviderLanes()
        started = monotonic()
        result = lanes.run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(slow_request),
            policy=ProviderLanePolicy(
                actual_attempt_budget=1,
                timeout_seconds=0.01,
                cancellation_grace_seconds=0.01,
            ),
        )
        elapsed = monotonic() - started
        try:
            successor = lanes.run(
                provider_group="eastmoney",
                adapter=ProductionInvocationAdapter(
                    lambda: successor_calls.append(active.is_set())
                ),
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            )
            self.assertEqual(result.failure_code, "provider_timeout")
            self.assertEqual(result.quiescence.state, "poisoned")
            self.assertLess(elapsed, 0.1)
            self.assertEqual(successor_calls, [])
            self.assertEqual(successor.failure_code, "provider_lane_poisoned")
        finally:
            released.set()

    def test_nested_retry_helpers_share_the_leaf_budget_without_double_counting(
        self,
    ) -> None:
        calls: list[str] = []
        inner = unpaced_gate()
        outer = unpaced_gate()

        def request() -> str:
            calls.append("leaf provider")
            if len(calls) == 1:
                raise TransportError("rate_limited", "sanitized")
            return "unexpected retry"

        class NestedRetryAdapter:
            def execute(self, invocation: ProviderInvocation) -> str:
                return outer.run(
                    lambda: inner.run(
                        lambda: invocation.call(ProductionInvocationAdapter(request))
                    )[0]
                )[0]

        result = ProviderLanes().run(
            provider_group="eastmoney",
            adapter=NestedRetryAdapter(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
        )

        self.assertEqual(calls, ["leaf provider"])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")

    def test_retry_evidence_is_ordered_immutable_and_module_issued(self) -> None:
        calls: list[str] = []

        def request() -> str:
            calls.append("leaf")
            if len(calls) < 3:
                raise TransportError("rate_limited", "sanitized")
            return "complete"

        lanes = ProviderLanes()
        result = lanes.run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(request),
            policy=ProviderLanePolicy(
                actual_attempt_budget=3,
                timeout_seconds=1,
                rate_limit_backoffs=(0, 0),
            ),
        )

        self.assertEqual(result.value, "complete")
        self.assertEqual(calls, ["leaf", "leaf", "leaf"])
        self.assertEqual([item.sequence for item in result.attempts], [1, 2, 3])
        self.assertEqual(
            [item.state for item in result.attempts], ["failed", "failed", "succeeded"]
        )
        for evidence in (*result.attempts, result.quiescence, result):
            self.assertTrue(lanes.is_authoritative(evidence))
            self.assertFalse(lanes.is_authoritative(copy.copy(evidence)))
            self.assertFalse(ProviderLanes().is_authoritative(evidence))
        with self.assertRaises(FrozenInstanceError):
            result.attempts[0].sequence = 100  # type: ignore[misc]
        with self.assertRaises(TypeError):
            replace(result.attempts[0], sequence=100)
        with self.assertRaises(TypeError):
            AttemptEvidence()

    def test_poisoned_lane_recovers_only_when_owned_call_returns(self) -> None:
        release = Event()
        calls: list[str] = []
        lanes = ProviderLanes()
        result = lanes.run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(lambda: release.wait(1)),
            policy=ProviderLanePolicy(
                actual_attempt_budget=1,
                timeout_seconds=0.01,
                cancellation_grace_seconds=0,
            ),
        )
        try:
            self.assertEqual(lanes.quiescence("eastmoney").state, "poisoned")
            self.assertEqual(result.attempts[0].state, "active")
            release.set()
            deadline = monotonic() + 1
            while lanes.quiescence("eastmoney").state != "quiescent":
                if monotonic() >= deadline:
                    self.fail("owned provider did not reach quiescence")
                sleep(0.001)
            successor = lanes.run(
                provider_group="eastmoney",
                adapter=ProductionInvocationAdapter(lambda: calls.append("successor")),
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            )
            self.assertEqual(successor.status, "succeeded")
            self.assertEqual(calls, ["successor"])
            self.assertEqual(result.quiescence.state, "poisoned")
            self.assertEqual(result.attempts[0].state, "active")
        finally:
            release.set()

    def test_cooperative_cancel_still_requires_actual_call_return(self) -> None:
        observed_cancellation = Event()

        class CooperativeAdapter:
            def invoke(self, cancellation: CancellationToken) -> str:
                if cancellation.wait(1):
                    observed_cancellation.set()
                return "terminal"

            def execute(self, invocation: ProviderInvocation) -> str:
                return invocation.call(self)

        lanes = ProviderLanes()
        result = lanes.run(
            provider_group="sse",
            adapter=CooperativeAdapter(),
            policy=ProviderLanePolicy(
                actual_attempt_budget=1,
                timeout_seconds=0.01,
                cancellation_grace_seconds=0.1,
            ),
        )
        self.assertTrue(observed_cancellation.is_set())
        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.quiescence.state, "quiescent")
        self.assertEqual(result.attempts[0].state, "succeeded")
        self.assertIsNone(result.value)

    def test_poisoned_call_keeps_one_of_the_three_active_group_slots(self) -> None:
        release = Event()
        active_lock = Lock()
        active = 0
        peak = 0
        started = {group: Event() for group in ("one", "two", "three", "four")}
        results: list[object] = []

        def request(group: str) -> str:
            nonlocal active, peak
            with active_lock:
                active += 1
                peak = max(peak, active)
            started[group].set()
            try:
                release.wait(1)
                return group
            finally:
                with active_lock:
                    active -= 1

        lanes = ProviderLanes()
        poisoned = lanes.run(
            provider_group="one",
            adapter=ProductionInvocationAdapter(lambda: request("one")),
            policy=ProviderLanePolicy(
                actual_attempt_budget=1,
                timeout_seconds=0.01,
                cancellation_grace_seconds=0,
            ),
        )

        def run_group(group: str) -> None:
            results.append(
                lanes.run(
                    provider_group=group,
                    adapter=ProductionInvocationAdapter(lambda: request(group)),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=1, timeout_seconds=1
                    ),
                )
            )

        workers = [
            Thread(target=run_group, args=(group,)) for group in ("two", "three")
        ]
        for worker in workers:
            worker.start()
        try:
            self.assertTrue(started["two"].wait(1))
            self.assertTrue(started["three"].wait(1))
            fourth = lanes.run(
                provider_group="four",
                adapter=ProductionInvocationAdapter(lambda: request("four")),
                policy=ProviderLanePolicy(
                    actual_attempt_budget=1, timeout_seconds=0.02
                ),
            )
            self.assertEqual(poisoned.quiescence.state, "poisoned")
            self.assertEqual(fourth.failure_code, "provider_lane_busy")
            self.assertEqual(fourth.attempts, ())
            self.assertFalse(started["four"].is_set())
            self.assertEqual(peak, 3)
        finally:
            release.set()
            for worker in workers:
                worker.join(1)
        self.assertFalse(any(worker.is_alive() for worker in workers))

    def test_same_group_successor_waits_through_active_invocation(self) -> None:
        release = Event()
        initial_started = Event()
        successor_started = Event()
        observed_overlap: list[bool] = []
        lanes = ProviderLanes()

        def initial() -> None:
            initial_started.set()
            release.wait(1)

        def successor() -> None:
            observed_overlap.append(not release.is_set())
            successor_started.set()

        def run(request: object) -> None:
            lanes.run(
                provider_group="eastmoney",
                adapter=ProductionInvocationAdapter(request),  # type: ignore[arg-type]
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            )

        first = Thread(target=run, args=(initial,))
        second = Thread(target=run, args=(successor,))
        first.start()
        self.assertTrue(initial_started.wait(1))
        second.start()
        try:
            self.assertFalse(successor_started.wait(0.02))
            self.assertEqual(lanes.quiescence("eastmoney").state, "active")
        finally:
            release.set()
            first.join(1)
            second.join(1)
        self.assertTrue(successor_started.is_set())
        self.assertEqual(observed_overlap, [False])

    def test_invoker_cannot_be_reused_by_a_later_execution(self) -> None:
        retained: list[ProviderInvocation] = []
        calls: list[str] = []

        class CaptureAdapter:
            def execute(self, invocation: ProviderInvocation) -> str:
                retained.append(invocation)
                return invocation.call(ProductionInvocationAdapter(lambda: "first"))

        class BorrowAdapter:
            def execute(self, invocation: ProviderInvocation) -> None:
                return retained[0].call(
                    ProductionInvocationAdapter(lambda: calls.append("borrowed"))
                )

        lanes = ProviderLanes()
        lanes.run(
            provider_group="first",
            adapter=CaptureAdapter(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
        )
        result = lanes.run(
            provider_group="second",
            adapter=BorrowAdapter(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
        )
        self.assertEqual(result.failure_code, "provider_invocation_unavailable")
        self.assertEqual(result.attempts, ())
        self.assertEqual(calls, [])

    def test_pacing_and_backoff_diagnostics_keep_existing_order_and_bounds(
        self,
    ) -> None:
        clock = FakeClock()
        lanes = ProviderLanes(
            clock=clock, sleeper=clock.sleep, jitter=lambda low, high: low
        )
        policy = ProviderLanePolicy(
            actual_attempt_budget=2,
            timeout_seconds=1,
            minimum_interval_seconds=1,
            jitter_bounds=(0.2, 0.2),
            rate_limit_backoffs=(0.5,),
        )
        lanes.run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(lambda: "first"),
            policy=policy,
        )
        calls = 0

        def request() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TransportError("rate_limited", "sanitized")
            return "second"

        result = lanes.run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(request),
            policy=policy,
        )
        self.assertEqual(clock.sleeps, [1.2, 0.7, 0.5])
        self.assertEqual(
            [item.code for item in result.diagnostics],
            ["source_request_paced", "rate_limit_backoff", "source_request_paced"],
        )
        self.assertEqual(result.diagnostics[1].attempt, 1)
        self.assertEqual(len(result.attempts), 2)

    def test_raw_failure_and_invalid_timing_never_enter_lane_diagnostics(self) -> None:
        class SecretError(Exception):
            code = "Bearer private-token /private/sensitive/file"

        def request() -> str:
            raise SecretError("secret headers and raw server response")

        result = ProviderLanes().run(
            provider_group="eastmoney",
            adapter=ProductionInvocationAdapter(request),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
        )
        self.assertEqual(result.failure_code, "provider_call_failed")
        self.assertEqual(result.diagnostics, ())
        self.assertNotIn("private-token", repr(result))
        self.assertNotIn("raw server", repr(result))
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=value)
            with self.assertRaises(ValueError):
                SerialRequestGate(jitter_bounds=(value, value))

    def test_subclass_equality_cannot_impersonate_module_issued_evidence(self) -> None:
        lanes = ProviderLanes()
        result = lanes.run(
            provider_group="proof-group",
            adapter=ProductionInvocationAdapter(lambda: "value"),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
        )
        proof = result.attempts[0]

        class ForgedAttempt(AttemptEvidence):
            def __init__(self) -> None:
                pass

            def __hash__(self) -> int:
                return hash(proof)

            def __eq__(self, other: object) -> bool:
                return True

        self.assertFalse(lanes.is_authoritative(ForgedAttempt()))

    def test_invoker_instance_fields_cannot_expand_the_module_owned_budget(
        self,
    ) -> None:
        calls: list[str] = []

        class TamperAdapter:
            def execute(self, invocation: ProviderInvocation) -> str:
                try:
                    invocation._budget = 2  # type: ignore[attr-defined]
                except AttributeError:
                    pass
                invocation.call(
                    ProductionInvocationAdapter(lambda: calls.append("one"))
                )
                invocation.call(
                    ProductionInvocationAdapter(lambda: calls.append("two"))
                )
                return "forged"

        result = ProviderLanes().run(
            provider_group="proof-budget",
            adapter=TamperAdapter(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
        )
        self.assertEqual(calls, ["one"])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
