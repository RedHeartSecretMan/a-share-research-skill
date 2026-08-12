from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import a_share_research.capability_execution as capability_execution  # noqa: E402
from a_share_research.capability_execution import (  # noqa: E402
    BoundedOperationRequest,
    CapabilityExecutionPlan,
    OperationExecutionPolicy,
    PlannedOperation,
    _deadline_limits_attempt,
    _deadline_sleeper,
    _execute_provider_lane,
    _execute_provider_requests,
    _merge_operation_outcomes,
    _outcome_from_wire,
    _PlanDeadlineExpired,
    _provider_gate,
    _spawn_attempt,
    compile_capability_execution_plan,
    execute_capability_plan,
)
from a_share_research.identity_resolution import (  # noqa: E402
    execute_security_identity_research,
)
from a_share_research.identity_sources import (  # noqa: E402
    IdentityOperationRequest,
    UrlLibTransport,
    build_default_identity_operations,
)
from a_share_research.research_result import (  # noqa: E402
    OperationExecution,
    OperationOutcome,
)

from tests.test_unified_identity_runtime import (  # noqa: E402
    deterministic_identity_obligation,
    identity_request,
    invoke_installed_identity,
)

PUBLIC_RESULT_KEYS = {
    "schema_version",
    "status",
    "research_anchor",
    "plan",
    "capability_registry",
    "coverage",
    "observations",
    "claims",
    "calculations",
    "conflicts",
    "limitations",
    "source_errors",
    "task_views",
    "briefs",
}


class InstalledCapabilityExecutionTests(unittest.TestCase):
    def test_installed_worker_wire_rejects_secret_fields_without_losing_siblings(
        self,
    ) -> None:
        completed = invoke_installed_identity(
            identity_request(allow_experimental=True),
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
            fixture_wire_secret_operation_id="sse_stock_list@1",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("SECRET_VALUE", completed.stdout)
        self.assertNotIn("SECRET_VALUE", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        result = json.loads(completed.stdout)
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(operations["sse_stock_list@1"]["state"], "failed")
        self.assertEqual(operations["szse_stock_list@1"]["state"], "observed_empty")
        self.assertEqual(
            operations["cninfo_security_dictionary@1"]["state"], "complete"
        )
        self.assertEqual(
            result["source_errors"][0]["code"], "operation_isolation_failed"
        )

    def test_installed_name_discovery_blocks_two_verified_same_name_candidates(
        self,
    ) -> None:
        request = identity_request(allow_experimental=True)
        request["subjects"] = [{"clue": "同名股份"}]

        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "productid=600001": "sse_same_name.json",
                "query.sse.com.cn": "sse_empty.json",
                "txtDMorJC=000002": "szse_same_name.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_same_name.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["claims"], [])
        self.assertEqual(
            [item["code"] for item in result["limitations"]],
            ["ambiguous_security_clue"],
        )
        official_observations = [
            item
            for item in result["observations"]
            if item["operation_id"] in {"sse_stock_list@1", "szse_stock_list@1"}
        ]
        self.assertEqual(
            {
                (
                    item["operation_id"],
                    item["payload"]["observation"]["code"],
                )
                for item in official_observations
            },
            {
                ("sse_stock_list@1", "600001"),
                ("szse_stock_list@1", "000002"),
            },
        )
        dictionary_observations = [
            item
            for item in result["observations"]
            if item["operation_id"] == "cninfo_security_dictionary@1"
        ]
        self.assertEqual(
            {
                item["payload"]["observation"]["code"]
                for item in dictionary_observations
            },
            {"600001", "000002"},
        )
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(operations["sse_stock_list@1"]["diagnostics"]["attempts"], 3)
        self.assertEqual(operations["szse_stock_list@1"]["diagnostics"]["attempts"], 3)
        self.assertEqual(result["plan"]["execution"]["total_budget_seconds"], "120.000")
        self.assertEqual(
            [
                item["max_requests_per_plan"]
                for item in result["plan"]["execution"]["operations"][:2]
            ],
            [3, 3],
        )

    def test_installed_name_discovery_candidate_overflow_fails_closed(self) -> None:
        request = identity_request(allow_experimental=True)
        request["subjects"] = [{"clue": "同名股份"}]

        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "productid=600001": "sse_same_name.json",
                "query.sse.com.cn": "sse_empty.json",
                "txtDMorJC=000002": "szse_same_name.json",
                "www.szse.cn": "szse_empty.json",
            },
            fixture_route_bodies={
                "www.cninfo.com.cn": {
                    "stockList": [
                        {
                            "code": code,
                            "category": "A股",
                            "orgId": f"org-{code}",
                            "zwjc": "同名股份",
                        }
                        for code in ("600001", "000002", "300003")
                    ]
                }
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["limitations"][0]["code"], "ambiguous_security_clue")
        self.assertEqual(
            {
                item["payload"]["observation"]["code"]
                for item in result["observations"]
                if item["operation_id"] == "cninfo_security_dictionary@1"
            },
            {"600001", "000002", "300003"},
        )

    def test_installed_max_requests_only_patch_changes_public_budget(self) -> None:
        request = identity_request(allow_experimental=True)

        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
            fixture_max_requests={
                "sse_stock_list@1": 1,
                "szse_stock_list@1": 1,
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["plan"]["execution"]["total_budget_seconds"], "63.000")

    def test_installed_standard_isolates_one_source_failure_and_retains_siblings(
        self,
    ) -> None:
        request = identity_request(allow_experimental=True)
        request["parameters"] = {"completeness_mode": "standard"}

        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "empty_body.txt",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(set(result), PUBLIC_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["plan"]["execution"]["mode"], "standard")

        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(
            list(operations),
            [
                "sse_stock_list@1",
                "szse_stock_list@1",
                "cninfo_security_dictionary@1",
            ],
        )
        self.assertEqual(operations["sse_stock_list@1"]["state"], "complete")
        self.assertEqual(operations["szse_stock_list@1"]["state"], "failed")
        self.assertEqual(
            operations["cninfo_security_dictionary@1"]["state"], "complete"
        )
        self.assertEqual(
            [item["operation_id"] for item in result["observations"]],
            ["sse_stock_list@1", "cninfo_security_dictionary@1"],
        )
        self.assertEqual(
            [item["operation_id"] for item in result["source_errors"]],
            ["szse_stock_list@1"],
        )

    def test_installed_standard_runs_different_providers_concurrently(self) -> None:
        request = identity_request(allow_experimental=True)
        request["parameters"] = {"completeness_mode": "standard"}

        started = time.monotonic()
        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
            fixture_delays={
                "query.sse.com.cn": 0.35,
                "www.szse.cn": 0.35,
                "www.cninfo.com.cn": 0.35,
            },
        )
        elapsed = time.monotonic() - started

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertLess(elapsed, 0.85)

    def test_installed_fast_guarantees_core_and_does_not_request_enrichment(
        self,
    ) -> None:
        request = identity_request(allow_experimental=True)
        request["parameters"] = {"completeness_mode": "fast"}

        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["plan"]["execution"]["mode"], "fast")
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(operations["sse_stock_list@1"]["state"], "complete")
        self.assertEqual(operations["szse_stock_list@1"]["state"], "observed_empty")
        self.assertEqual(
            operations["cninfo_security_dictionary@1"]["state"], "not_requested"
        )
        self.assertEqual(
            [item["operation_id"] for item in result["observations"]],
            ["sse_stock_list@1"],
        )

    def test_installed_fast_name_query_never_plans_a_followup(self) -> None:
        request = identity_request(allow_experimental=True)
        request["subjects"] = [{"clue": "贵州茅台"}]
        request["parameters"] = {"completeness_mode": "fast"}

        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_empty.json",
                "www.szse.cn": "szse_empty.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(operations["sse_stock_list@1"]["diagnostics"]["attempts"], 1)
        self.assertEqual(operations["szse_stock_list@1"]["diagnostics"]["attempts"], 1)
        self.assertEqual(
            operations["cninfo_security_dictionary@1"]["state"], "not_requested"
        )

    def test_installed_fast_precondition_keeps_enrichment_not_requested(self) -> None:
        cases = (
            (False, "600519", "skipped_policy"),
            (True, "BAD:600519", "skipped_research_dependency"),
            (True, "BSE:430047", "skipped_research_dependency"),
        )
        for allow_experimental, clue, expected_core in cases:
            with self.subTest(clue=clue):
                request = identity_request(allow_experimental=allow_experimental)
                request["subjects"] = [{"clue": clue}]
                request["parameters"] = {"completeness_mode": "fast"}

                completed = invoke_installed_identity(request)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                operations = result["coverage"]["security_identity_resolution"][
                    "operations"
                ]
                self.assertEqual(operations["sse_stock_list@1"]["state"], expected_core)
                self.assertEqual(
                    operations["szse_stock_list@1"]["state"], expected_core
                )
                self.assertEqual(
                    operations["cninfo_security_dictionary@1"]["state"],
                    "not_requested",
                )

    def test_installed_standard_precondition_blocks_every_planned_operation(
        self,
    ) -> None:
        cases = (
            (False, "600519", "skipped_policy"),
            (True, "BAD:600519", "skipped_research_dependency"),
            (True, "BSE:430047", "skipped_research_dependency"),
        )
        for allow_experimental, clue, expected in cases:
            with self.subTest(clue=clue):
                request = identity_request(allow_experimental=allow_experimental)
                request["subjects"] = [{"clue": clue}]
                request["parameters"] = {"completeness_mode": "standard"}

                completed = invoke_installed_identity(request)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                operations = result["coverage"]["security_identity_resolution"][
                    "operations"
                ]
                self.assertEqual(
                    {item["state"] for item in operations.values()},
                    {expected},
                )

    def test_installed_operation_timeout_is_local_and_kills_only_its_worker(
        self,
    ) -> None:
        request = identity_request(allow_experimental=True)
        request["parameters"] = {"completeness_mode": "standard"}

        started = time.monotonic()
        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
            fixture_delays={"www.szse.cn": 2.0},
            fixture_timeouts={"szse_stock_list@1": 0.2},
        )
        elapsed = time.monotonic() - started

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertLess(elapsed, 1.5)
        result = json.loads(completed.stdout)
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(operations["sse_stock_list@1"]["state"], "complete")
        self.assertEqual(operations["szse_stock_list@1"]["state"], "failed")
        self.assertEqual(
            operations["cninfo_security_dictionary@1"]["state"], "complete"
        )
        self.assertEqual(
            [item["operation_id"] for item in result["source_errors"]],
            ["szse_stock_list@1"],
        )
        self.assertEqual(result["source_errors"][0]["code"], "operation_timeout")
        self.assertEqual(
            [item["operation_id"] for item in result["observations"]],
            ["sse_stock_list@1", "cninfo_security_dictionary@1"],
        )

    def test_installed_total_budget_retains_completed_and_orders_unfinished(
        self,
    ) -> None:
        request = identity_request(allow_experimental=True)
        request["parameters"] = {"completeness_mode": "standard"}

        started = time.monotonic()
        completed = invoke_installed_identity(
            request,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
            fixture_delays={
                "query.sse.com.cn": 0.05,
                "www.szse.cn": 2.0,
                "www.cninfo.com.cn": 2.0,
            },
            fixture_timeouts={
                "sse_stock_list@1": 0.5,
                "szse_stock_list@1": 0.5,
                "cninfo_security_dictionary@1": 0.5,
            },
            fixture_backoffs={
                "sse_stock_list@1": (),
                "szse_stock_list@1": (),
                "cninfo_security_dictionary@1": (),
            },
            fixture_max_requests={
                "sse_stock_list@1": 1,
                "szse_stock_list@1": 1,
                "cninfo_security_dictionary@1": 1,
            },
        )
        elapsed = time.monotonic() - started

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertLess(elapsed, 1.2)
        result = json.loads(completed.stdout)
        self.assertEqual(
            [item["operation_id"] for item in result["observations"]],
            ["sse_stock_list@1"],
        )
        self.assertEqual(
            [(item["operation_id"], item["code"]) for item in result["source_errors"]],
            [
                ("szse_stock_list@1", "plan_budget_exhausted"),
                ("cninfo_security_dictionary@1", "plan_budget_exhausted"),
            ],
        )
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(
            list(operations),
            [
                "sse_stock_list@1",
                "szse_stock_list@1",
                "cninfo_security_dictionary@1",
            ],
        )


class CapabilityExecutionPolicyTests(unittest.TestCase):
    def test_policy_timings_require_exact_public_millisecond_round_trip(self) -> None:
        invalid = (
            {"timeout_seconds": 0.0001},
            {"timeout_seconds": 1.0001},
            {"timeout_seconds": 59.99900005},
            {"minimum_interval_seconds": 0.0006},
            {"rate_limit_backoffs": (0.0006,)},
        )
        for values in invalid:
            with (
                self.subTest(values=values),
                self.assertRaisesRegex(ValueError, "policy"),
            ):
                OperationExecutionPolicy(
                    provider_id="millisecond-provider",
                    tier="core",
                    **values,  # type: ignore[arg-type]
                )

        policy = OperationExecutionPolicy(
            provider_id="millisecond-provider",
            tier="core",
            timeout_seconds=1.001,
            minimum_interval_seconds=0.002,
            rate_limit_backoffs=(0.003,),
        )
        public = policy.to_public_dict()
        self.assertEqual(public["timeout_seconds"], "1.001")
        self.assertEqual(public["minimum_interval_seconds"], "0.002")
        self.assertEqual(public["rate_limit_backoffs"], ["0.003"])

    def test_provider_id_is_a_safe_public_identifier(self) -> None:
        for provider_id in (
            "https://provider.example/path?token=SECRET",
            "provider with spaces",
            "provider\ncontrol",
        ):
            with (
                self.subTest(provider_id=provider_id),
                self.assertRaisesRegex(ValueError, "policy"),
            ):
                OperationExecutionPolicy(provider_id=provider_id, tier="core")

        for provider_id in ("sse", "cninfo-provider", "provider.v1", "provider_2"):
            with self.subTest(provider_id=provider_id):
                self.assertEqual(
                    OperationExecutionPolicy(
                        provider_id=provider_id, tier="core"
                    ).provider_id,
                    provider_id,
                )

    def test_followups_reject_unknown_and_over_bound_exact_owners(self) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "bounded_followup@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(
                provider_id="bounded-followup",
                tier="core",
                timeout_seconds=1.0,
                rate_limit_backoffs=(),
                max_requests_per_plan=2,
            ),
        )
        plan = CapabilityExecutionPlan("1.0", "standard", (operation,), 2.0)
        request = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )

        def succeed(
            item: PlannedOperation,
            _: BoundedOperationRequest,
            __: float,
            ___: bool,
        ) -> OperationOutcome:
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
            )

        cases = (
            (
                "unknown",
                (("other_capability", "bounded_followup@1"), request),
                "planned operation",
            ),
            (
                "over_bound",
                (
                    (
                        operation.obligation.capability_id,
                        operation.obligation.operation_id,
                    ),
                    request,
                ),
                "declared operation bound",
            ),
        )
        for name, followup, expected in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, expected):
                execute_capability_plan(
                    plan,
                    request,
                    attempt_runner=succeed,
                    followup_planner=lambda _, followup=followup, name=name: (
                        (followup,) if name == "unknown" else (followup, followup)
                    ),
                )

    def test_followup_failure_retains_initial_observation_and_attempts(self) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "retained_followup@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(
                provider_id="retained-followup",
                tier="core",
                timeout_seconds=1.0,
                rate_limit_backoffs=(),
                max_requests_per_plan=2,
            ),
        )
        plan = CapabilityExecutionPlan("1.0", "standard", (operation,), 2.0)
        initial = BoundedOperationRequest(
            "security_identity", {"query": "name", "as_of": "2026-08-12"}
        )
        followup = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )

        def attempt(
            item: PlannedOperation,
            request: BoundedOperationRequest,
            _: float,
            __: bool,
        ) -> OperationOutcome:
            if request.payload["query"] == "name":
                return OperationOutcome(
                    item.obligation.capability_id,
                    item.obligation.operation_id,
                    OperationExecution("succeeded", query_complete=True),
                    observations=(
                        {
                            "source_operation": item.obligation.operation_id,
                            "value": "retained",
                        },
                    ),
                    diagnostics={"attempts": 1},
                )
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("failed"),
                source_errors=(
                    {
                        "source_operation": item.obligation.operation_id,
                        "code": "upstream_unavailable",
                        "message": "safe",
                    },
                ),
                diagnostics={"attempts": 1},
            )

        outcome = execute_capability_plan(
            plan,
            initial,
            attempt_runner=attempt,
            followup_planner=lambda _: (
                (
                    (
                        operation.obligation.capability_id,
                        operation.obligation.operation_id,
                    ),
                    followup,
                ),
            ),
        )[0]

        self.assertEqual(outcome.execution.state, "failed")
        self.assertIsNone(outcome.execution.query_complete)
        self.assertEqual(outcome.observations[0]["value"], "retained")
        self.assertEqual(outcome.source_errors[0]["code"], "upstream_unavailable")
        self.assertEqual(outcome.diagnostics["attempts"], 2)

    def test_followup_merge_retains_ordered_shared_gate_diagnostics(self) -> None:
        class ControlledClock:
            now = 0.0

            def __call__(self) -> float:
                return self.now

            def sleep(self, delay: float) -> None:
                self.now += delay

        operation = PlannedOperation(
            deterministic_identity_obligation(
                "gated_followup@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(
                provider_id="gated-followup",
                tier="core",
                timeout_seconds=1.0,
                minimum_interval_seconds=0.001,
                rate_limit_backoffs=(),
                max_requests_per_plan=2,
            ),
        )
        initial = BoundedOperationRequest(
            "security_identity", {"query": "name", "as_of": "2026-08-12"}
        )
        followup = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )

        def attempt(
            item: PlannedOperation,
            _: BoundedOperationRequest,
            __: float,
            ___: bool,
        ) -> OperationOutcome:
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
                diagnostics={"attempts": 1},
            )

        clock = ControlledClock()
        gate = _provider_gate(
            (operation,), deadline=3.0, clock=clock, sleeper=clock.sleep
        )
        first = _execute_provider_requests(
            ((operation, initial),),
            3.0,
            gate=gate,
            attempt_runner=attempt,
            clock=clock,
        )[0]
        second = _execute_provider_requests(
            ((operation, followup),),
            3.0,
            gate=gate,
            attempt_runner=attempt,
            clock=clock,
        )[0]
        outcome = _merge_operation_outcomes(first, second)

        gate_diagnostics = outcome.diagnostics["request_gate"]
        self.assertEqual(
            [item["code"] for item in gate_diagnostics], ["source_request_paced"]
        )
        self.assertEqual(gate_diagnostics[0]["delay_seconds"], "0.001")

    def test_default_identity_operations_declare_only_isolated_workers(self) -> None:
        operations = build_default_identity_operations(UrlLibTransport())

        self.assertEqual(
            {item.execution_backend for item in operations}, {"isolated_worker"}
        )
        for operation in operations:
            public_policy = operation.execution_policy.to_public_dict()
            self.assertNotIn("execution_backend", public_policy)
            self.assertNotIn("worker_transport_descriptor", public_policy)

    def test_total_budget_is_the_longest_provider_lane_worst_case(self) -> None:
        class QualifiedOperation:
            field_dispositions = ()

            def __init__(
                self, operation_id: str, policy: OperationExecutionPolicy
            ) -> None:
                self.obligation = deterministic_identity_obligation(
                    operation_id,
                    security_role="supporting",
                    source_role="authoritative_disclosure",
                    exchange="SSE",
                )
                self.execution_policy = policy

        operations = (
            QualifiedOperation(
                "provider_a_first@1",
                OperationExecutionPolicy(
                    provider_id="provider-a",
                    tier="core",
                    timeout_seconds=2.0,
                    minimum_interval_seconds=0.25,
                    rate_limit_backoffs=(0.5,),
                ),
            ),
            QualifiedOperation(
                "provider_a_second@1",
                OperationExecutionPolicy(
                    provider_id="provider-a",
                    tier="core",
                    timeout_seconds=3.0,
                    minimum_interval_seconds=0.25,
                    rate_limit_backoffs=(0.5,),
                ),
            ),
            QualifiedOperation(
                "provider_b@1",
                OperationExecutionPolicy(
                    provider_id="provider-b",
                    tier="core",
                    timeout_seconds=4.0,
                    minimum_interval_seconds=0.0,
                    rate_limit_backoffs=(),
                ),
            ),
        )

        plan = compile_capability_execution_plan(
            version="1.0",
            completeness_mode="standard",
            operations=operations,
        )

        # Provider A: two attempts per operation, two 0.5s backoffs, and
        # pacing before three of its four requests = 11.75s. Provider B = 4s.
        self.assertEqual(plan.total_budget_seconds, 11.75)

    def test_equal_plan_remaining_does_not_misclassify_operation_timeout(self) -> None:
        self.assertFalse(
            _deadline_limits_attempt(
                remaining_seconds=0.5,
                operation_timeout_seconds=0.5,
            )
        )
        self.assertTrue(
            _deadline_limits_attempt(
                remaining_seconds=0.499,
                operation_timeout_seconds=0.5,
            )
        )


class SameProviderExecutionTests(unittest.TestCase):
    def test_plan_budget_retains_completed_owner_from_a_late_same_provider_lane(
        self,
    ) -> None:
        policy = OperationExecutionPolicy(
            provider_id="same-late-provider",
            tier="core",
            timeout_seconds=1.0,
            rate_limit_backoffs=(),
        )
        operations = tuple(
            PlannedOperation(
                deterministic_identity_obligation(
                    operation_id,
                    security_role="supporting",
                    source_role="authoritative_disclosure",
                    exchange="SSE",
                ),
                (),
                policy,
            )
            for operation_id in (
                "completed_same@1",
                "late_same@1",
                "queued_same@1",
            )
        )
        plan = CapabilityExecutionPlan("1.0", "standard", operations, 0.1)
        request = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )
        late_started = threading.Event()
        release_late = threading.Event()
        calls: list[str] = []
        active = 0
        maximum_active = 0

        def attempt(
            operation: PlannedOperation,
            _: BoundedOperationRequest,
            __: float,
            ___: bool,
        ) -> OperationOutcome:
            nonlocal active, maximum_active
            calls.append(operation.obligation.operation_id)
            active += 1
            maximum_active = max(maximum_active, active)
            if operation.obligation.operation_id == "late_same@1":
                late_started.set()
                release_late.wait(2.0)
                active -= 1
                return OperationOutcome(
                    operation.obligation.capability_id,
                    operation.obligation.operation_id,
                    OperationExecution("succeeded", query_complete=True),
                )
            active -= 1
            return OperationOutcome(
                operation.obligation.capability_id,
                operation.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
                observations=(
                    {
                        "source_operation": operation.obligation.operation_id,
                        "value": "retained",
                    },
                ),
            )

        started = time.monotonic()
        outcomes = execute_capability_plan(plan, request, attempt_runner=attempt)
        elapsed = time.monotonic() - started
        self.assertTrue(late_started.is_set())
        release_late.set()

        self.assertLess(elapsed, 0.4)
        self.assertEqual(calls, ["completed_same@1", "late_same@1"])
        self.assertEqual(maximum_active, 1)
        self.assertEqual(outcomes[0].observations[0]["value"], "retained")
        self.assertEqual(outcomes[1].source_errors[0]["code"], "plan_budget_exhausted")
        self.assertEqual(outcomes[2].source_errors[0]["code"], "plan_budget_exhausted")

    def test_late_followup_retains_initial_observation_and_becomes_budget_failed(
        self,
    ) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "late_followup@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(
                provider_id="late-followup-provider",
                tier="core",
                timeout_seconds=1.0,
                rate_limit_backoffs=(),
                max_requests_per_plan=2,
            ),
        )
        plan = CapabilityExecutionPlan("1.0", "standard", (operation,), 0.1)
        initial = BoundedOperationRequest(
            "security_identity", {"query": "name", "as_of": "2026-08-12"}
        )
        followup = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )
        followup_started = threading.Event()
        release_followup = threading.Event()

        def attempt(
            item: PlannedOperation,
            request: BoundedOperationRequest,
            _: float,
            __: bool,
        ) -> OperationOutcome:
            if request.payload["query"] == "600519":
                followup_started.set()
                release_followup.wait(2.0)
                return OperationOutcome(
                    item.obligation.capability_id,
                    item.obligation.operation_id,
                    OperationExecution("succeeded", query_complete=True),
                )
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
                observations=(
                    {
                        "source_operation": item.obligation.operation_id,
                        "value": "retained",
                    },
                ),
                diagnostics={"attempts": 1},
            )

        outcomes = execute_capability_plan(
            plan,
            initial,
            attempt_runner=attempt,
            followup_planner=lambda _: (
                (
                    (
                        operation.obligation.capability_id,
                        operation.obligation.operation_id,
                    ),
                    followup,
                ),
            ),
        )
        self.assertTrue(followup_started.is_set())
        release_followup.set()
        outcome = outcomes[0]

        self.assertEqual(outcome.execution.state, "failed")
        self.assertEqual(outcome.observations[0]["value"], "retained")
        self.assertEqual(outcome.source_errors[0]["code"], "plan_budget_exhausted")
        self.assertEqual(outcome.diagnostics["attempts"], 1)

    def test_injected_adapters_use_the_bounded_in_process_backend(self) -> None:
        calls: list[tuple[str, float]] = []

        class InjectedAdapter:
            field_dispositions = ()
            execution_backend = "in_process"

            def __init__(self, operation_id: str) -> None:
                self.obligation = deterministic_identity_obligation(
                    operation_id,
                    security_role="supporting",
                    source_role="authoritative_disclosure",
                    exchange="SSE",
                )
                self.execution_policy = OperationExecutionPolicy(
                    provider_id="shared-injected-provider",
                    tier="core",
                    timeout_seconds=0.2,
                    rate_limit_backoffs=(),
                )

            def execute(
                self,
                request: IdentityOperationRequest,
                timeout_seconds: float,
            ) -> OperationOutcome:
                calls.append((self.obligation.operation_id, timeout_seconds))
                return OperationOutcome(
                    capability_id=self.obligation.capability_id,
                    operation_id=self.obligation.operation_id,
                    execution=OperationExecution("succeeded", query_complete=True),
                )

        operations = (
            InjectedAdapter("injected_first@1"),
            InjectedAdapter("injected_second@1"),
        )

        execution = execute_security_identity_research(
            identity_request(allow_experimental=True),
            operations,  # type: ignore[arg-type]
        )

        self.assertEqual(
            [item.operation_id for item in execution.outcomes],
            ["injected_first@1", "injected_second@1"],
        )
        self.assertEqual(
            [item[0] for item in calls],
            ["injected_first@1", "injected_second@1"],
        )
        self.assertTrue(all(0 < timeout <= 0.2 for _, timeout in calls))

    def test_injected_transport_cannot_impersonate_the_isolated_backend(self) -> None:
        calls: list[str] = []

        class ImpersonatingTransport:
            worker_transport_descriptor = "urllib"

            def get(self, url: str, headers: dict[str, str]) -> object:
                del headers
                calls.append(url)
                raise AssertionError("injected transport was selected in process")

        class ImpersonatingSubclass(UrlLibTransport):
            def get_with_timeout(
                self, url: str, headers: dict[str, str], timeout_seconds: float
            ) -> object:
                del headers, timeout_seconds
                calls.append(url)
                raise AssertionError("subclass transport was selected in process")

        for transport, message in (
            (ImpersonatingTransport(), "selected in process"),
            (ImpersonatingSubclass(), "subclass transport was selected in process"),
        ):
            with self.subTest(transport=type(transport).__name__):
                operations = build_default_identity_operations(
                    transport  # type: ignore[arg-type]
                )

                self.assertEqual(
                    {item.execution_backend for item in operations}, {"in_process"}
                )
                with self.assertRaisesRegex(AssertionError, message):
                    execute_security_identity_research(
                        identity_request(allow_experimental=True), operations
                    )
        self.assertTrue(calls)

    def test_one_gate_serializes_paces_and_retries_only_rate_limit(self) -> None:
        class ControlledClock:
            now = 0.0

            def __call__(self) -> float:
                return self.now

            def sleep(self, delay: float) -> None:
                sleeps.append(delay)
                self.now += delay

        policy = OperationExecutionPolicy(
            provider_id="shared-provider",
            tier="core",
            timeout_seconds=2.0,
            minimum_interval_seconds=0.25,
            rate_limit_backoffs=(0.5,),
        )
        operations = tuple(
            PlannedOperation(
                deterministic_identity_obligation(
                    operation_id,
                    security_role="supporting",
                    source_role="authoritative_disclosure",
                    exchange="SSE",
                ),
                (),
                policy,
            )
            for operation_id in ("shared_first@1", "shared_second@1")
        )
        request = BoundedOperationRequest(
            kind="security_identity",
            payload={"query": "600519", "as_of": "2026-08-12"},
        )
        calls: list[str] = []
        sleeps: list[float] = []
        active = 0
        maximum_active = 0

        def attempt(
            operation: PlannedOperation,
            _: BoundedOperationRequest,
            __: float,
            ___: bool,
        ) -> OperationOutcome:
            nonlocal active, maximum_active
            operation_id = operation.obligation.operation_id
            calls.append(operation_id)
            active += 1
            maximum_active = max(maximum_active, active)
            active -= 1
            if operation_id == "shared_first@1" and calls.count(operation_id) == 1:
                return OperationOutcome(
                    capability_id=operation.obligation.capability_id,
                    operation_id=operation_id,
                    execution=OperationExecution("failed"),
                    source_errors=(
                        {
                            "source_operation": operation_id,
                            "code": "rate_limited",
                            "message": "The source rate limit was reached.",
                        },
                    ),
                )
            return OperationOutcome(
                capability_id=operation.obligation.capability_id,
                operation_id=operation_id,
                execution=OperationExecution("succeeded", query_complete=True),
                diagnostics={"attempts": 1},
            )

        clock = ControlledClock()
        outcomes = _execute_provider_lane(
            operations,
            request,
            deadline=20.0,
            attempt_runner=attempt,
            clock=clock,
            sleeper=clock.sleep,
        )

        self.assertEqual(
            calls,
            ["shared_first@1", "shared_first@1", "shared_second@1"],
        )
        self.assertEqual(maximum_active, 1)
        self.assertEqual(sleeps, [0.5, 0.25])
        self.assertEqual(outcomes[0].diagnostics["attempts"], 2)
        self.assertEqual(outcomes[1].diagnostics["attempts"], 1)
        self.assertEqual(
            [item.coverage for item in outcomes],
            [
                "observed_empty",
                "observed_empty",
            ],
        )

    def test_non_rate_source_failures_are_not_retried(self) -> None:
        policy = OperationExecutionPolicy(
            provider_id="shared-provider",
            tier="core",
            timeout_seconds=2.0,
            minimum_interval_seconds=0.0,
            rate_limit_backoffs=(0.5, 1.0),
        )
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "unknown_schema@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            policy,
        )
        calls = 0

        def attempt(*_: object) -> OperationOutcome:
            nonlocal calls
            calls += 1
            return OperationOutcome(
                capability_id=operation.obligation.capability_id,
                operation_id=operation.obligation.operation_id,
                execution=OperationExecution("failed"),
                source_errors=(
                    {
                        "source_operation": operation.obligation.operation_id,
                        "code": "unknown_schema",
                        "message": "The source response did not match its schema.",
                    },
                ),
            )

        outcomes = _execute_provider_lane(
            (operation,),
            BoundedOperationRequest(
                kind="security_identity",
                payload={"query": "600519", "as_of": "2026-08-12"},
            ),
            deadline=20.0,
            attempt_runner=attempt,
            clock=lambda: 0.0,
            sleeper=lambda _: self.fail("non-rate failure must not sleep"),
        )

        self.assertEqual(calls, 1)
        self.assertEqual(outcomes[0].source_errors[0]["code"], "unknown_schema")

    def test_exhausted_rate_limit_returns_its_owned_terminal_outcome(self) -> None:
        policy = OperationExecutionPolicy(
            provider_id="shared-provider",
            tier="core",
            timeout_seconds=2.0,
            minimum_interval_seconds=0.0,
            rate_limit_backoffs=(0.5,),
        )
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "limited@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            policy,
        )
        calls = 0
        sleeps: list[float] = []

        def attempt(*_: object) -> OperationOutcome:
            nonlocal calls
            calls += 1
            return OperationOutcome(
                capability_id=operation.obligation.capability_id,
                operation_id=operation.obligation.operation_id,
                execution=OperationExecution("failed"),
                source_errors=(
                    {
                        "source_operation": operation.obligation.operation_id,
                        "code": "rate_limited",
                        "message": "The source rate limit was reached.",
                    },
                ),
            )

        outcomes = _execute_provider_lane(
            (operation,),
            BoundedOperationRequest(
                kind="security_identity",
                payload={"query": "600519", "as_of": "2026-08-12"},
            ),
            deadline=20.0,
            attempt_runner=attempt,
            clock=lambda: 0.0,
            sleeper=sleeps.append,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(outcomes[0].source_errors[0]["code"], "rate_limited")
        self.assertEqual(
            outcomes[0].diagnostics["request_gate"],
            [{"code": "rate_limit_backoff", "delay_seconds": "0.500", "attempt": 1}],
        )
        self.assertEqual(outcomes[0].diagnostics["attempts"], 2)

    def test_rate_limit_backoff_is_clipped_by_the_plan_deadline(self) -> None:
        class ControlledClock:
            now = 0.0

            def __call__(self) -> float:
                return self.now

            def sleep(self, delay: float) -> None:
                sleeps.append(delay)
                self.now += delay

        policy = OperationExecutionPolicy(
            provider_id="shared-provider",
            tier="core",
            timeout_seconds=2.0,
            minimum_interval_seconds=0.0,
            rate_limit_backoffs=(0.5,),
        )
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "deadline_limited@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            policy,
        )
        calls = 0
        sleeps: list[float] = []

        def attempt(*_: object) -> OperationOutcome:
            nonlocal calls
            calls += 1
            return OperationOutcome(
                capability_id=operation.obligation.capability_id,
                operation_id=operation.obligation.operation_id,
                execution=OperationExecution("failed"),
                source_errors=(
                    {
                        "source_operation": operation.obligation.operation_id,
                        "code": "rate_limited",
                        "message": "The source rate limit was reached.",
                    },
                ),
            )

        clock = ControlledClock()
        outcomes = _execute_provider_lane(
            (operation,),
            BoundedOperationRequest(
                kind="security_identity",
                payload={"query": "600519", "as_of": "2026-08-12"},
            ),
            deadline=0.2,
            attempt_runner=attempt,
            clock=clock,
            sleeper=clock.sleep,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(sleeps, [0.2])
        self.assertEqual(outcomes[0].source_errors[0]["code"], "plan_budget_exhausted")

    def test_expired_deadline_does_not_call_the_gate_sleeper(self) -> None:
        with self.assertRaises(_PlanDeadlineExpired):
            _deadline_sleeper(
                deadline=1.0,
                clock=lambda: 1.0,
                sleeper=lambda _: self.fail("expired deadline must not sleep"),
            )(0.5)

    def test_executor_return_is_not_blocked_by_a_late_provider_future(self) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "late_provider@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(
                provider_id="late-provider",
                tier="core",
                timeout_seconds=1.0,
                rate_limit_backoffs=(),
            ),
        )
        plan = CapabilityExecutionPlan(
            version="1.0",
            completeness_mode="standard",
            operations=(operation,),
            total_budget_seconds=0.1,
        )
        request = BoundedOperationRequest(
            kind="security_identity",
            payload={"query": "600519", "as_of": "2026-08-12"},
        )
        provider_started = threading.Event()
        release_provider = threading.Event()
        executor_returned = threading.Event()
        returned: list[tuple[OperationOutcome, ...]] = []

        def late_provider(*_: object, **__: object) -> tuple[OperationOutcome, ...]:
            provider_started.set()
            release_provider.wait(2.0)
            return (
                OperationOutcome(
                    capability_id=operation.obligation.capability_id,
                    operation_id=operation.obligation.operation_id,
                    execution=OperationExecution("succeeded", query_complete=True),
                ),
            )

        def run_executor() -> None:
            returned.append(execute_capability_plan(plan, request))
            executor_returned.set()

        with patch(
            "a_share_research.capability_execution._execute_provider_requests",
            late_provider,
        ):
            thread = threading.Thread(target=run_executor)
            thread.start()
            self.assertTrue(provider_started.wait(0.5))
            returned_before_release = executor_returned.wait(0.4)
            release_provider.set()
            thread.join(1.0)

        self.assertTrue(returned_before_release)
        self.assertFalse(thread.is_alive())
        self.assertEqual(
            returned[0][0].source_errors[0]["code"], "plan_budget_exhausted"
        )


class OperationWireSafetyTests(unittest.TestCase):
    def test_worker_cleanup_oserror_never_escapes_local_timeout_or_io_failure(
        self,
    ) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "cleanup_failure@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(
                provider_id="cleanup-failure",
                tier="core",
                timeout_seconds=1.0,
                rate_limit_backoffs=(),
            ),
        )
        request = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )

        class CleanupFailureProcess:
            returncode = 1

            def __init__(self, first_error: BaseException) -> None:
                self.first_error = first_error
                self.calls = 0

            def communicate(
                self, _: bytes | None = None, timeout: float | None = None
            ) -> tuple[bytes, bytes]:
                del timeout
                self.calls += 1
                if self.calls == 1:
                    raise self.first_error
                raise OSError("SECRET cleanup communicate")

            def kill(self) -> None:
                raise OSError("SECRET cleanup kill")

        cases = (
            (
                "timeout",
                subprocess.TimeoutExpired("SECRET command", 1.0),
                "operation_timeout",
            ),
            ("io_failure", OSError("SECRET communicate"), "operation_isolation_failed"),
        )
        for name, first_error, expected_code in cases:
            with (
                self.subTest(name=name),
                patch(
                    "a_share_research.capability_execution.subprocess.Popen",
                    return_value=CleanupFailureProcess(first_error),
                ),
            ):
                outcome = _spawn_attempt(operation, request, 1.0, False)

                self.assertEqual(outcome.source_errors[0]["code"], expected_code)
                self.assertNotIn("SECRET", repr(outcome))

    def test_worker_launch_failure_is_local_and_retains_other_provider(self) -> None:
        operations = tuple(
            PlannedOperation(
                deterministic_identity_obligation(
                    operation_id,
                    security_role="supporting",
                    source_role="authoritative_disclosure",
                    exchange="SSE",
                ),
                (),
                OperationExecutionPolicy(
                    provider_id=provider_id,
                    tier="core",
                    timeout_seconds=1.0,
                    rate_limit_backoffs=(),
                ),
            )
            for operation_id, provider_id in (
                ("launch_failure@1", "failed-provider"),
                ("retained_sibling@1", "healthy-provider"),
            )
        )
        plan = CapabilityExecutionPlan("1.0", "standard", operations, 1.0)
        request = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )

        def attempt(
            item: PlannedOperation,
            request: BoundedOperationRequest,
            timeout_seconds: float,
            budget_limited: bool,
        ) -> OperationOutcome:
            if item.obligation.operation_id == "launch_failure@1":
                with patch(
                    "a_share_research.capability_execution.subprocess.Popen",
                    side_effect=OSError("SECRET launch path"),
                ):
                    return _spawn_attempt(
                        item, request, timeout_seconds, budget_limited
                    )
            return OperationOutcome(
                item.obligation.capability_id,
                item.obligation.operation_id,
                OperationExecution("succeeded", query_complete=True),
            )

        outcomes = execute_capability_plan(plan, request, attempt_runner=attempt)

        self.assertEqual(
            outcomes[0].source_errors[0]["code"], "operation_isolation_failed"
        )
        self.assertNotIn("SECRET", repr(outcomes[0]))
        self.assertEqual(outcomes[1].coverage, "observed_empty")

    def test_parent_rejects_private_worker_observation_fields(self) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "wire_source@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(provider_id="wire", tier="core"),
        )
        payload = {
            "wire_version": "1",
            "capability_id": operation.obligation.capability_id,
            "operation_id": operation.obligation.operation_id,
            "execution": {"state": "succeeded", "query_complete": True},
            "observations": [
                {
                    "source_operation": operation.obligation.operation_id,
                    "_private_payload": {"callable": "do_not_serialize"},
                }
            ],
            "source_errors": [],
            "field_dispositions": [],
            "diagnostics": {},
        }

        with self.assertRaisesRegex(ValueError, "private"):
            _outcome_from_wire(operation, payload)

    def test_worker_rejects_malformed_wire_without_stdout_or_stderr(self) -> None:
        valid_shape = {
            "wire_version": "1",
            "operation_id": "sse_stock_list@1",
            "timeout_seconds": 1.0,
            "request": {
                "kind": "security_identity",
                "payload": {"query": "600519", "as_of": "2026-08-12"},
            },
        }
        cases = {
            "unknown": json.dumps({**valid_shape, "unknown": True}).encode(),
            "null": json.dumps({**valid_shape, "request": None}).encode(),
            "private": json.dumps(
                {
                    **valid_shape,
                    "request": {
                        "kind": "security_identity",
                        "payload": {
                            "query": "600519",
                            "as_of": "2026-08-12",
                            "_private": "do-not-send",
                        },
                    },
                }
            ).encode(),
            "nonfinite": json.dumps(valid_shape).replace("1.0", "NaN", 1).encode(),
            "oversize": b"{" + b" " * (2 * 1024 * 1024 + 1),
        }
        for name, wire in cases.items():
            with self.subTest(name=name):
                completed = subprocess.run(
                    [sys.executable, capability_execution.__file__, "--worker"],
                    input=wire,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, b"")
                self.assertEqual(completed.stderr, b"")

    def test_parent_maps_secret_worker_diagnostics_to_safe_local_error(self) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "wire_source@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(provider_id="wire", tier="core"),
        )
        for secret_key in ("authorization_header", "password", "API-Key", "apiKey"):
            with self.subTest(secret_key=secret_key):
                response = {
                    "wire_version": "1",
                    "capability_id": operation.obligation.capability_id,
                    "operation_id": operation.obligation.operation_id,
                    "execution": {"state": "failed", "query_complete": None},
                    "observations": [],
                    "source_errors": [],
                    "field_dispositions": [],
                    "diagnostics": {secret_key: "SECRET_VALUE"},
                }

                class FakeProcess:
                    returncode = 0

                    def __init__(self, response_bytes: bytes) -> None:
                        self.response_bytes = response_bytes

                    def communicate(
                        self, _: bytes | None = None, timeout: float | None = None
                    ) -> tuple[bytes, bytes]:
                        del timeout
                        return (
                            self.response_bytes,
                            b"Traceback: SECRET_VALUE",
                        )

                    def kill(self) -> None:
                        self.fail("valid fake response must not time out")

                with patch(
                    "a_share_research.capability_execution.subprocess.Popen",
                    return_value=FakeProcess(json.dumps(response).encode()),
                ):
                    outcome = _spawn_attempt(
                        operation,
                        BoundedOperationRequest(
                            kind="security_identity",
                            payload={"query": "600519", "as_of": "2026-08-12"},
                        ),
                        1.0,
                        False,
                    )

                self.assertEqual(
                    outcome.source_errors[0]["code"], "worker_protocol_error"
                )
                self.assertNotIn("SECRET_VALUE", repr(outcome))

    def test_legitimate_provider_security_lineage_key_is_allowed(self) -> None:
        operation = PlannedOperation(
            deterministic_identity_obligation(
                "wire_source@1",
                security_role="supporting",
                source_role="authoritative_disclosure",
                exchange="SSE",
            ),
            (),
            OperationExecutionPolicy(provider_id="wire", tier="core"),
        )
        payload = {
            "wire_version": "1",
            "capability_id": operation.obligation.capability_id,
            "operation_id": operation.obligation.operation_id,
            "execution": {"state": "succeeded", "query_complete": True},
            "observations": [
                {
                    "source_operation": operation.obligation.operation_id,
                    "provider_security_code": "600519",
                }
            ],
            "source_errors": [],
            "field_dispositions": [],
            "diagnostics": {},
        }

        self.assertEqual(
            _outcome_from_wire(operation, payload).observations[0][
                "provider_security_code"
            ],
            "600519",
        )


if __name__ == "__main__":
    unittest.main()
