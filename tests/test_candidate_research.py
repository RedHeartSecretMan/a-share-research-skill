from __future__ import annotations

import unittest

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.candidate_research import (  # noqa: E402
    CandidateResearchContext,
    CandidateResearchContractError,
    CandidateSelection,
    research_candidates,
    run_candidate_deep_research,
    select_candidates,
    summarize_candidate,
)
from a_share_research.screening import (  # noqa: E402
    ScreeningPartition,
    ScreeningResult,
)


class CandidateResearchTests(unittest.TestCase):
    def test_standard_selects_stable_global_order_with_explicit_limit(self) -> None:
        ranking = [
            {
                "canonical_security_id": "SZSE:000002",
                "rank": 2,
                "research_priority_score": "90.000000",
            },
            {
                "canonical_security_id": "SSE:600001",
                "rank": 1,
                "research_priority_score": "95.000000",
            },
            {
                "canonical_security_id": "BSE:830001",
                "rank": 2,
                "research_priority_score": "90.000000",
            },
        ]
        selected = select_candidates(
            ranking, completeness_mode="standard", candidate_limit=2
        )
        self.assertEqual(
            [item.canonical_security_id for item in selected],
            ["SSE:600001", "BSE:830001"],
        )
        self.assertEqual([item.rank for item in selected], [1, 2])

    def test_context_is_immutable_and_frozen(self) -> None:
        context = CandidateResearchContext(
            requested_as_of="2026-08-21",
            screen_date="2026-08-20",
            information_cutoff="2026-08-20T23:59:59+08:00",
        )
        with self.assertRaises(AttributeError):
            context.screen_date = "2026-08-21"  # type: ignore[misc]


class CandidateExecutionTests(unittest.TestCase):
    def test_malformed_candidate_result_fails_closed(self) -> None:
        with self.assertRaises(CandidateResearchContractError):
            summarize_candidate(
                CandidateSelection("SSE:600001", 1, "99.000000"),
                {"status": "unknown", "claims": [{"id": 1}]},
            )

        with self.assertRaises(CandidateResearchContractError):
            research_candidates(
                [
                    {
                        "canonical_security_id": "SSE:600001",
                        "rank": 1,
                        "research_priority_score": "99",
                    }
                ],
                context=CandidateResearchContext(
                    "2026-08-21", "2026-08-20", "2026-08-20T23:59:59+08:00"
                ),
                completeness_mode="standard",
                candidate_limit=1,
                run_single_security=lambda _selection, _context: {"status": "unknown"},
            )

    def test_candidate_failure_isolated_and_context_is_reused(self) -> None:
        ranking = [
            {
                "canonical_security_id": "SSE:600001",
                "rank": 1,
                "research_priority_score": "99",
            },
            {
                "canonical_security_id": "SZSE:000001",
                "rank": 2,
                "research_priority_score": "98",
            },
            {
                "canonical_security_id": "BSE:830001",
                "rank": 3,
                "research_priority_score": "97",
            },
        ]
        context = CandidateResearchContext(
            "2026-08-21", "2026-08-20", "2026-08-20T23:59:59+08:00"
        )
        calls: list[tuple[str, CandidateResearchContext]] = []

        def runner(selection, observed_context):
            calls.append((selection.canonical_security_id, observed_context))
            if selection.canonical_security_id == "SZSE:000001":
                raise RuntimeError("source unavailable")
            return {
                "status": "limited",
                "claims": [{"id": "claim-1"}],
                "limitations": [{"id": "lim-1"}],
                "task_views": {
                    "research_workflow": {"steps": [{"step_id": "identity"}]}
                },
            }

        summaries = research_candidates(
            ranking,
            context=context,
            completeness_mode="standard",
            candidate_limit=3,
            run_single_security=runner,
        )
        self.assertEqual(
            [item.canonical_security_id for item in summaries],
            ["SSE:600001", "SZSE:000001", "BSE:830001"],
        )
        self.assertEqual(
            [item.state for item in summaries], ["limited", "failed", "limited"]
        )
        self.assertEqual(summaries[0].evidence_references["claims"], ("claim-1",))
        self.assertEqual(summaries[0].limitation_references, ("lim-1",))
        self.assertEqual([item[1] for item in calls], [context, context, context])

    def test_screening_scope_is_preserved_and_fast_runs_zero_candidates(self) -> None:
        screening = ScreeningResult(
            requested_as_of="2026-08-21",
            screen_date="2026-08-20",
            information_cutoff="2026-08-20T23:59:59+08:00",
            availability_at="2026-08-21T10:00:00+08:00",
            plan_id="fundamental_research_priority@1.0",
            public_state="blocked",
            partitions=(
                ScreeningPartition(
                    "SSE",
                    {},
                    (),
                    (),
                    (
                        {
                            "canonical_security_id": "SSE:600001",
                            "rank": 1,
                            "research_priority_score": "95.000000",
                        },
                    ),
                    (),
                ),
                ScreeningPartition("SZSE", {}, (), (), (), ()),
                ScreeningPartition("BSE", {}, (), (), (), ()),
            ),
        )
        context = CandidateResearchContext(
            "2026-08-21", "2026-08-20", "2026-08-20T23:59:59+08:00"
        )
        result = run_candidate_deep_research(
            screening, context=context, completeness_mode="fast"
        )
        self.assertEqual(result, ())
        with self.assertRaises(CandidateResearchContractError):
            run_candidate_deep_research(
                screening,
                context=CandidateResearchContext(
                    "2026-08-21", "2026-08-19", "2026-08-19T23:59:59+08:00"
                ),
                completeness_mode="fast",
            )


if __name__ == "__main__":
    unittest.main()
