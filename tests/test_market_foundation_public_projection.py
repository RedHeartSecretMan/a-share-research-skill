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
