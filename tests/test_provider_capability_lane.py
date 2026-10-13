from __future__ import annotations

import unittest
from threading import Event
from dataclasses import replace
from time import monotonic, sleep

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capability_execution import (  # noqa: E402
    BoundedOperationRequest,
    CapabilityExecutionPlan,
    compile_capability_execution_plan,
    execute_capability_plan,
)
from a_share_research.identity_sources import (  # noqa: E402
    UrlLibTransport,
    build_default_identity_operations,
)
from a_share_research.provider_lane import (  # noqa: E402
    ProviderInvocation,
    ProductionInvocationAdapter,
)
from a_share_research.research_result import (  # noqa: E402
    OperationExecution,
    OperationOutcome,
)


class ProviderCapabilityLaneTests(unittest.TestCase):
    def test_capability_attempts_are_authoritative_even_when_runner_claims_more(
        self,
    ) -> None:
        compiled = compile_capability_execution_plan(
            version="1.0",
            completeness_mode="standard",
            operations=build_default_identity_operations(UrlLibTransport()),
        )
        operation = compiled.operations[0]
        plan = CapabilityExecutionPlan("1.0", "standard", (operation,), 5)
        calls: list[str] = []

        def attempt(item, request, timeout_seconds, budget_limited):
            calls.append(item.obligation.operation_id)
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
                field_dispositions=item.field_dispositions,
                diagnostics={"attempts": 999},
            )

        outcomes = execute_capability_plan(
            plan,
            BoundedOperationRequest(
                "security_identity", {"query": "600519", "as_of": "2026-08-12"}
            ),
            attempt_runner=attempt,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcomes[0].diagnostics["attempts"], 1)

    def test_expired_followup_keeps_the_actual_attempt_in_its_prefix_evidence(
        self,
    ) -> None:
        compiled = compile_capability_execution_plan(
            version="1.0",
            completeness_mode="standard",
            operations=build_default_identity_operations(UrlLibTransport()),
        )
        operation = compiled.operations[0]
        plan = CapabilityExecutionPlan("1.0", "standard", (operation,), 0.1)
        release = Event()
        calls: list[str] = []
        owner = (operation.obligation.capability_id, operation.obligation.operation_id)

        def attempt(item, request, timeout_seconds, budget_limited):
            query = request.payload["query"]
            calls.append(query)
            if query == "followup":
                release.wait(1)
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
                field_dispositions=item.field_dispositions,
                diagnostics={"attempts": 999},
            )

        def followups(outcomes):
            return (
                (
                    owner,
                    BoundedOperationRequest(
                        "security_identity",
                        {"query": "followup", "as_of": "2026-08-12"},
                    ),
                ),
            )

        try:
            outcomes = execute_capability_plan(
                plan,
                BoundedOperationRequest(
                    "security_identity", {"query": "initial", "as_of": "2026-08-12"}
                ),
                attempt_runner=attempt,
                followup_planner=followups,
            )
            self.assertEqual(calls, ["initial", "followup"])
            self.assertEqual(outcomes[0].diagnostics["attempts"], 2)
            self.assertEqual(
                outcomes[0].source_errors[0]["code"], "plan_budget_exhausted"
            )
        finally:
            release.set()

    def test_owned_poison_is_retained_across_capability_plan_calls(self) -> None:
        original = compile_capability_execution_plan(
            version="1.0",
            completeness_mode="standard",
            operations=build_default_identity_operations(UrlLibTransport()),
        ).operations[0]
        operation = replace(
            original,
            policy=replace(
                original.policy,
                provider_id="cross-plan-poison",
                timeout_seconds=0.02,
                rate_limit_backoffs=(),
                max_requests_per_plan=1,
            ),
        )
        plan = CapabilityExecutionPlan("1.0", "standard", (operation,), 0.1)
        release = Event()
        returned = Event()
        calls: list[str] = []

        def owned_attempt(
            item,
            request,
            timeout_seconds,
            budget_limited,
            invocation: ProviderInvocation,
        ):
            def leaf():
                query = request.payload["query"]
                calls.append(query)
                if query == "first":
                    release.wait(2)
                    returned.set()
                return OperationOutcome(
                    item.obligation.capability_id,
                    item.obligation.operation_id,
                    OperationExecution("succeeded", query_complete=True),
                    field_dispositions=item.field_dispositions,
                )

            return invocation.call(ProductionInvocationAdapter(leaf))

        def execute(query: str):
            return execute_capability_plan(
                plan,
                BoundedOperationRequest(
                    "security_identity", {"query": query, "as_of": "2026-08-12"}
                ),
                owned_attempt_runner=owned_attempt,
            )[0]

        first = execute("first")
        try:
            successor = execute("successor")
            self.assertEqual(calls, ["first"])
            self.assertEqual(first.diagnostics["attempts"], 1)
            self.assertEqual(successor.diagnostics["attempts"], 0)
            self.assertEqual(
                successor.source_errors[0]["code"], "provider_lane_poisoned"
            )
            release.set()
            self.assertTrue(returned.wait(1))
            deadline = monotonic() + 1
            while True:
                recovered = execute("recovered")
                if recovered.execution.state == "succeeded":
                    break
                if monotonic() >= deadline:
                    self.fail("the completed owned execution did not release its lane")
                sleep(0.001)
            self.assertEqual(calls, ["first", "recovered"])
            self.assertEqual(recovered.diagnostics["attempts"], 1)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
