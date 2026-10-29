from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.candidate_research import CandidateSummary  # noqa: E402
from a_share_research.result_artifact import (  # noqa: E402
    DeterministicFilesystemAdapter,
    ResultArtifactIdentity,
)
from a_share_research.screening import ScreeningPartition, ScreeningResult  # noqa: E402
from a_share_research.screening_result_artifact import (  # noqa: E402
    ScreeningResultArtifact,
    ScreeningResultArtifactContractError,
)


def _identity() -> ResultArtifactIdentity:
    return ResultArtifactIdentity(
        "1.0", b"request", b"producer", b"plan", b"operations"
    )


def _screening() -> ScreeningResult:
    partitions = []
    for exchange in ("SSE", "SZSE", "BSE"):
        partitions.append(
            ScreeningPartition(
                exchange,
                {
                    "expected": 1,
                    "excluded": 0,
                    "resolved": 1,
                    "acquired": 1,
                    "ranked": 1,
                    "unranked": 0,
                    "failed": 0,
                    "unsupported": 0,
                },
                (),
                (
                    {
                        "canonical_security_id": f"{exchange}:600001",
                        "factor_inputs": {},
                        "resolved": True,
                    },
                ),
                (
                    {
                        "canonical_security_id": f"{exchange}:600001",
                        "rank": 1,
                        "research_priority_score": "99.000000",
                        "resolved": True,
                    },
                ),
                (),
            )
        )
    return ScreeningResult(
        "2026-08-21",
        "2026-08-20",
        "2026-08-20T23:59:59+08:00",
        "2026-08-21T10:00:00+08:00",
        "fundamental_research_priority@1.0",
        "blocked",
        tuple(partitions),
    )


def _candidate() -> CandidateSummary:
    return CandidateSummary(
        "SSE:600001",
        1,
        "99.000000",
        False,
        "limited",
        {"claims": ("claim-1",)},
        ("limitation-1",),
        (),
        {"workflow_id": "single_security_research", "workflow_version": "1.0"},
    )


class ScreeningResultArtifactTests(unittest.TestCase):
    def test_create_finalize_and_equivalent_resume_return_same_receipt(self) -> None:
        with TemporaryDirectory() as temporary:
            adapter = DeterministicFilesystemAdapter(Path(temporary) / "state")
            path = Path(temporary) / "artifact"
            writer = ScreeningResultArtifact.create(
                path, _identity(), _screening(), [_candidate()], filesystem=adapter
            )
            first = writer.finalize()
            second = ScreeningResultArtifact.resume(
                path, _identity(), filesystem=adapter
            )
            self.assertEqual(first, second)
            manifest = adapter.read_bytes(path, "manifest.json")
            self.assertNotIn(b"provider_raw_payload", manifest)
            self.assertNotIn(b"/private/", manifest)
            import json

            parsed = json.loads(manifest)
            self.assertIn("excluded", parsed["coverage"]["SSE"])

    def test_invalid_candidate_is_rejected_before_any_physical_change(self) -> None:
        with TemporaryDirectory() as temporary:
            adapter = DeterministicFilesystemAdapter(Path(temporary) / "state")
            path = Path(temporary) / "artifact"
            unsafe = CandidateSummary(
                "SSE:600001",
                1,
                "99.000000",
                False,
                "limited",
                {},
                (),
                (),
                {"headers": "secret"},
            )
            with self.assertRaises(ScreeningResultArtifactContractError):
                ScreeningResultArtifact.create(
                    path, _identity(), _screening(), [unsafe], filesystem=adapter
                )
            self.assertFalse(adapter.exists(path))

    def test_coverage_counters_must_match_persisted_rows_before_create(self) -> None:
        with TemporaryDirectory() as temporary:
            adapter = DeterministicFilesystemAdapter(Path(temporary) / "state")
            path = Path(temporary) / "artifact"
            original = _screening()
            first = original.partitions[0]
            tampered = replace(
                first,
                coverage={**first.coverage, "resolved": 0},
            )
            invalid = replace(original, partitions=(tampered, *original.partitions[1:]))
            with self.assertRaises(ScreeningResultArtifactContractError):
                ScreeningResultArtifact.create(
                    path, _identity(), invalid, [_candidate()], filesystem=adapter
                )
            self.assertFalse(adapter.exists(path))

    def test_candidate_summary_must_match_ranking_before_create(self) -> None:
        with TemporaryDirectory() as temporary:
            adapter = DeterministicFilesystemAdapter(Path(temporary) / "state")
            path = Path(temporary) / "artifact"
            bad = replace(_candidate(), rank=99)
            with self.assertRaises(ScreeningResultArtifactContractError):
                ScreeningResultArtifact.create(
                    path, _identity(), _screening(), [bad], filesystem=adapter
                )
            self.assertFalse(adapter.exists(path))

    def test_partition_security_ids_must_match_exchange_before_create(self) -> None:
        with TemporaryDirectory() as temporary:
            adapter = DeterministicFilesystemAdapter(Path(temporary) / "state")
            path = Path(temporary) / "artifact"
            original = _screening()
            first = original.partitions[0]
            bad_ranking = (
                {**first.ranking[0], "canonical_security_id": "SZSE:600001"},
            )
            bad_factors = (
                {**first.factors[0], "canonical_security_id": "SZSE:600001"},
            )
            tampered = replace(first, factors=bad_factors, ranking=bad_ranking)
            invalid = replace(original, partitions=(tampered, *original.partitions[1:]))
            with self.assertRaises(ScreeningResultArtifactContractError):
                ScreeningResultArtifact.create(
                    path, _identity(), invalid, filesystem=adapter
                )
            self.assertFalse(adapter.exists(path))

    def test_partial_resume_skips_exact_files_and_external_tamper_fails_closed(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            adapter = DeterministicFilesystemAdapter(Path(temporary) / "state")
            path = Path(temporary) / "artifact"
            adapter.fail_next("after_final_publication")
            with self.assertRaises(ScreeningResultArtifactContractError):
                ScreeningResultArtifact.create(
                    path, _identity(), _screening(), [_candidate()], filesystem=adapter
                )
            resumed = ScreeningResultArtifact.resume(
                path, _identity(), _screening(), [_candidate()], filesystem=adapter
            )
            assert isinstance(resumed, ScreeningResultArtifact)
            receipt = resumed.finalize()
            self.assertEqual(receipt["status"], "finalized")
            adapter.tamper(path, "partitions/ranking/SSE/part-00000.jsonl", b"tampered")
            with self.assertRaises(ScreeningResultArtifactContractError):
                ScreeningResultArtifact.resume(path, _identity(), filesystem=adapter)


if __name__ == "__main__":
    unittest.main()
