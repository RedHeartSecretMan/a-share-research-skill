from __future__ import annotations

import unittest

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.market_foundation import (  # noqa: E402
    OperationRequest,
    build_default_market_foundation_operations,
)
from a_share_research.operation_release_matrix import (  # noqa: E402
    load_release_probe_evidence,
)
from a_share_research.research_result import (  # noqa: E402
    DirectTaskPlan,
    EvidenceUsePolicy,
    IdentityResearchPlan,
    OperationObligation,
    ResearchDependency,
    fold_direct_task_research_result,
)
from tests.release_probe_core_operations import sanitize_public_result  # noqa: E402
from tests.test_market_foundation import (  # noqa: E402
    ReportTransport,
    margin_report,
    turnover_report,
)


class MarketFoundationPublicProjectionTests(unittest.TestCase):
    def assert_unattempted_projection(self, outcome, operations, request):
        from a_share_research.market_foundation import fold_market_foundation

        folded = fold_market_foundation((outcome,), request, operations=operations)
        self.assertEqual(folded["operations"][0]["attempts"], 0)
        self.assertFalse(folded["operations"][0]["attempted"])
        plan = DirectTaskPlan(
            task_type="market_foundation_qualification",
            as_of=request.report_date,
            obligations=(
                OperationObligation(
                    outcome.capability_id,
                    outcome.operation_id,
                    "market_observation",
                    False,
                    "SSE",
                    (EvidenceUsePolicy(outcome.capability_id, "supporting"),),
                ),
            ),
            dependencies=(
                ResearchDependency(
                    outcome.capability_id, outcome.capability_id, 1, True
                ),
            ),
        )
        public = fold_direct_task_research_result(
            plan, (outcome,), summary="Offline unstarted source proof."
        ).to_dict()
        _, probes = sanitize_public_result(
            request_profile="market-foundation-unstarted",
            request_as_of=request.report_date,
            result=public,
            verified_at="2026-10-01T10:00:00+08:00",
        )
        self.assertFalse(probes[0]["attempted"])
        self.assertEqual(outcome.execution.state, "skipped_policy")
        self.assertEqual(probes[0]["source_result"], "unavailable")

    def test_poisoned_group_never_becomes_an_attempted_public_source(self):
        from threading import Event
        from a_share_research.provider_lane import (
            ProviderLanes,
            ProviderLanePolicy,
            ProductionInvocationAdapter,
        )

        release = Event()

        class Blocker:
            def execute(self, invocation):
                return invocation.call(
                    ProductionInvocationAdapter(lambda: release.wait(2))
                )

        lanes = ProviderLanes()
        seed = lanes.run(
            provider_group="sse",
            adapter=Blocker(),
            policy=ProviderLanePolicy(
                actual_attempt_budget=1,
                timeout_seconds=0.02,
                cancellation_grace_seconds=0,
            ),
        )
        self.assertEqual(seed.quiescence.state, "poisoned")
        transport = ReportTransport(
            turnover_report("SSE", {"TRADE_AMT_01": "1", "TRADE_AMT_03": "1"})
        )
        try:
            operations = build_default_market_foundation_operations(
                transports={"sse_a_share_turnover@1": transport}, lanes=lanes
            )
            request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
            outcome = operations[0].execute(request, 0.02)
            self.assertEqual(transport.calls, 0)
            self.assert_unattempted_projection(outcome, operations, request)
        finally:
            release.set()

    def test_busy_group_never_becomes_an_attempted_public_source(self):
        from threading import Event, Thread
        from a_share_research.provider_lane import (
            ProviderLanes,
            ProviderLanePolicy,
            ProductionInvocationAdapter,
        )

        release, started = Event(), Event()

        class Blocker:
            def execute(self, invocation):
                def hold():
                    started.set()
                    return release.wait(2)

                return invocation.call(ProductionInvocationAdapter(hold))

        lanes = ProviderLanes()
        seed = Thread(
            target=lambda: lanes.run(
                provider_group="sse",
                adapter=Blocker(),
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=2),
            )
        )
        seed.start()
        transport = ReportTransport(
            turnover_report("SSE", {"TRADE_AMT_01": "1", "TRADE_AMT_03": "1"})
        )
        try:
            self.assertTrue(started.wait(1))
            operations = build_default_market_foundation_operations(
                transports={"sse_a_share_turnover@1": transport}, lanes=lanes
            )
            request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
            outcome = operations[0].execute(request, 0.02)
            self.assertEqual(transport.calls, 0)
            self.assert_unattempted_projection(outcome, operations, request)
        finally:
            release.set()
            seed.join(2)
        self.assertFalse(seed.is_alive())

    def test_deadline_before_first_leaf_never_becomes_public_attempted(self):
        from threading import Event
        from a_share_research.provider_lane import ProductionInvocationAdapter

        release, finished = Event(), Event()
        calls = []

        class DelayedTransport:
            def acquire(self, selector, request, timeout, invocation):
                try:
                    release.wait(2)
                    return invocation.call(
                        ProductionInvocationAdapter(lambda: calls.append("leaf"))
                    )
                finally:
                    finished.set()

        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": DelayedTransport()}
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        try:
            outcome = operations[0].execute(request, 0.02)
            self.assertEqual(calls, [])
            self.assert_unattempted_projection(outcome, operations, request)
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.assertEqual(calls, [])

    def test_report_without_any_leaf_cannot_create_public_attempted_proof(self):
        class NoLeafTransport:
            def acquire(self, selector, request, timeout, invocation):
                return turnover_report(
                    "SSE", {"TRADE_AMT_01": "1", "TRADE_AMT_03": "1"}
                )

        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": NoLeafTransport()}
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcome = operations[0].execute(request, 1)
        self.assert_unattempted_projection(outcome, operations, request)

    def test_actual_leaf_failure_remains_attempted_through_public_projection(self):
        from a_share_research.market_foundation import fold_market_foundation
        from a_share_research.provider_lane import ProductionInvocationAdapter

        class FailedTransport:
            def acquire(self, selector, request, timeout, invocation):
                def unavailable():
                    raise RuntimeError("synthetic offline failure")

                return invocation.call(ProductionInvocationAdapter(unavailable))

        operations = build_default_market_foundation_operations(
            transports={"sse_a_share_turnover@1": FailedTransport()}
        )
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcome = operations[0].execute(request, 1)
        folded = fold_market_foundation((outcome,), request, operations=operations)
        self.assertEqual(outcome.execution.state, "failed")
        self.assertEqual(folded["operations"][0]["attempts"], 1)
        self.assertTrue(folded["operations"][0]["attempted"])
        plan = DirectTaskPlan(
            task_type="market_foundation_qualification",
            as_of=request.report_date,
            obligations=(
                OperationObligation(
                    outcome.capability_id,
                    outcome.operation_id,
                    "market_observation",
                    False,
                    "SSE",
                    (EvidenceUsePolicy(outcome.capability_id, "supporting"),),
                ),
            ),
            dependencies=(
                ResearchDependency(
                    outcome.capability_id, outcome.capability_id, 1, True
                ),
            ),
        )
        public = fold_direct_task_research_result(
            plan, (outcome,), summary="Offline attempted source failure."
        ).to_dict()
        _, probes = sanitize_public_result(
            request_profile="market-foundation-failed",
            request_as_of=request.report_date,
            result=public,
            verified_at="2026-10-01T10:00:00+08:00",
        )
        self.assertTrue(probes[0]["attempted"])
        self.assertEqual(probes[0]["source_result"], "unavailable")

    def test_identity_request_scope_and_unknown_exchange_metadata_stay_rejected(self):
        obligation = OperationObligation(
            "whole_market_turnover",
            "bse_a_share_turnover@1",
            "market_observation",
            False,
            "BSE",
            (EvidenceUsePolicy("whole_market_turnover", "supporting"),),
        )
        with self.assertRaisesRegex(ValueError, "identity plan exchange hint"):
            IdentityResearchPlan(
                as_of="2026-09-30",
                exchange_hint="BSE",
                obligations=(obligation,),
                dependencies=(),
                execution={},
            )
        with self.assertRaisesRegex(ValueError, "operation obligation exchange scope"):
            OperationObligation(
                "whole_market_turnover",
                "unknown_turnover@1",
                "market_observation",
                False,
                "UNKNOWN",
                (EvidenceUsePolicy("whole_market_turnover", "supporting"),),
            )

    def test_actual_bse_owners_keep_exchange_through_public_and_release_projection(
        self,
    ):
        transports = {
            "bse_a_share_turnover@1": ReportTransport(
                turnover_report("BSE", {"A_SHARE_TURNOVER_CNY": "1250005000"})
            ),
            "bse_market_margin@1": ReportTransport(margin_report("BSE")),
        }
        operations = build_default_market_foundation_operations(transports=transports)
        request = OperationRequest("2026-09-30", "2026-09-30T19:00:00+08:00")
        outcomes = tuple(
            operation.execute(request, 1)
            for operation in operations
            if operation.operation_id in transports
        )
        obligations = tuple(
            OperationObligation(
                outcome.capability_id,
                outcome.operation_id,
                "market_observation",
                False,
                "BSE",
                (EvidenceUsePolicy(outcome.capability_id, "supporting"),),
            )
            for outcome in outcomes
        )
        plan = DirectTaskPlan(
            task_type="market_foundation_qualification",
            as_of=request.report_date,
            obligations=obligations,
            dependencies=tuple(
                ResearchDependency(
                    outcome.capability_id, outcome.capability_id, 1, True
                )
                for outcome in outcomes
            ),
        )
        public = fold_direct_task_research_result(
            plan, outcomes, summary="Completed BSE market report fixture."
        ).to_dict()
        self.assertEqual(
            {item["exchange"] for item in public["plan"]["operation_obligations"]},
            {"BSE"},
        )
        self.assertEqual(
            {
                item["payload"]["observation"]["exchange"]
                for item in public["observations"]
            },
            {"BSE"},
        )
        safe, probes = sanitize_public_result(
            request_profile="bse-market-foundation",
            request_as_of=request.report_date,
            result=public,
            verified_at="2026-10-01T10:00:00+08:00",
        )
        self.assertEqual({item["source_result"] for item in probes}, {"complete"})
        candidate, digest, verified = "a" * 40, "b" * 64, "2026-10-01T10:00:00+08:00"
        artifact = {
            "schema_version": "1.0",
            "candidate_sha": candidate,
            "verified_at": verified,
            "attempt_digests": [digest],
            "installation_health": {
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            "plan_snapshots": [
                {
                    **safe,
                    "candidate_sha": candidate,
                    "verified_at": verified,
                    "attempt_digest": digest,
                }
            ],
            "installation_loads": [
                {
                    "producer_operation_id": outcome.operation_id,
                    "origin": "copied_install_plan",
                    "status": "complete",
                    "selector": "bse-market-foundation",
                }
                for outcome in outcomes
            ],
            "live_probes": [
                {
                    **probe,
                    "candidate_sha": candidate,
                    "request_profile": "bse-market-foundation",
                    "attempt_digest": digest,
                }
                for probe in probes
            ],
        }
        snapshots, loads, _, evidence = load_release_probe_evidence(artifact)
        self.assertEqual(len(loads), 2)
        self.assertEqual(len(evidence), 2)
        self.assertEqual(
            {
                item["exchange"]
                for item in snapshots[0].result["plan"]["operation_obligations"]
            },
            {"BSE"},
        )


if __name__ == "__main__":
    unittest.main()
