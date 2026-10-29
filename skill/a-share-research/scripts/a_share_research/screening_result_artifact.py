"""Internal screening-to-Result-Artifact integration seam.

The screening and candidate modules remain pure.  This module is the only
module that maps their bounded public projections to the write-once Result
Artifact interface.  It validates every record and the complete file envelope
before invoking ``ResultArtifact.create``; therefore invalid caller data cannot
create a target, anchor, stage, or residue.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .candidate_research import CandidateSummary
from .result_artifact import (
    ResultArtifact,
    ResultArtifactIdentity,
)
from .result_artifact_inputs import (
    PreparedResultArtifactFile,
    prepare_candidate,
    prepare_completed_partition,
)
from .screening import ScreeningResult

_EXCHANGES = ("SSE", "SZSE", "BSE")
_ARTIFACT_SCHEMA_VERSION = "screening-result@1.0"
_CONTEXT_SCHEMA_VERSION = "screening-context@1.0"


class ScreeningResultArtifactContractError(ValueError):
    """A screening Result Artifact input or lifecycle contract failure."""


@dataclass(frozen=True, slots=True)
class _PreparedScreeningArtifact:
    context: PreparedResultArtifactFile
    partitions: tuple[PreparedResultArtifactFile, ...]
    candidates: tuple[PreparedResultArtifactFile, ...]
    coverage: Mapping[str, Mapping[str, int]]

    @property
    def all_files(self) -> tuple[PreparedResultArtifactFile, ...]:
        return self.partitions + self.candidates


@dataclass(slots=True)
class ScreeningResultArtifact:
    """A narrow writer around the generic Result Artifact lifecycle.

    ``create`` and ``resume`` return a partial writer.  ``finalize`` publishes
    the manifest last.  A finalized ``resume`` returns the exact low-level
    receipt instead of rerunning or rewriting screening work.
    """

    _artifact: ResultArtifact
    _prepared: _PreparedScreeningArtifact

    @classmethod
    def create(
        cls,
        path: Path,
        identity: ResultArtifactIdentity,
        screening: ScreeningResult,
        candidates: Sequence[CandidateSummary] = (),
        *,
        filesystem: Any = None,
    ) -> ScreeningResultArtifact:
        prepared = _prepare(screening, candidates, identity)
        try:
            artifact = ResultArtifact.create(
                path,
                identity,
                filesystem=filesystem,
                initial_context=prepared.context,
            )
            for item in prepared.all_files:
                if item.relative_path.startswith("candidates/"):
                    artifact.write_candidate(item)
                else:
                    artifact.write_completed_partition(item)
        except Exception as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ScreeningResultArtifactContractError(
                "screening result artifact create failed"
            ) from error
        return cls(artifact, prepared)

    @classmethod
    def resume(
        cls,
        path: Path,
        identity: ResultArtifactIdentity,
        screening: ScreeningResult | None = None,
        candidates: Sequence[CandidateSummary] = (),
        *,
        filesystem: Any = None,
    ) -> ScreeningResultArtifact | dict[str, object]:
        try:
            resumed = ResultArtifact.resume(path, identity, filesystem=filesystem)
        except Exception as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ScreeningResultArtifactContractError(
                "screening result artifact resume failed"
            ) from error
        if isinstance(resumed, dict):
            # The low-level module has already verified the exact manifest and
            # receipt.  Return that detached receipt byte-for-byte.
            return resumed
        if screening is None:
            raise ScreeningResultArtifactContractError(
                "screening result artifact resume requires screening data for a partial artifact"
            )
        prepared = _prepare(screening, candidates, identity)
        # Context and every existing file are verified by the low-level handle.
        # Skip exact completed files so an equivalent resume is idempotent while
        # refusing changed bytes.
        checkpoint = resumed.checkpoint()
        try:
            persisted_context = checkpoint["frozen_context"]
            expected_context = json.loads(prepared.context.content_bytes)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ScreeningResultArtifactContractError(
                "screening result artifact frozen context is invalid on resume"
            ) from error
        if persisted_context != expected_context:
            raise ScreeningResultArtifactContractError(
                "screening result artifact frozen context differs on resume"
            )
        completed = {
            item["relative_path"]: item
            for item in cast(list[dict[str, object]], checkpoint["completed_files"])
        }
        for item in prepared.all_files:
            existing = completed.get(item.relative_path)
            if existing is not None:
                if (
                    resumed.read_completed_file(item.relative_path)
                    != item.content_bytes
                ):
                    raise ScreeningResultArtifactContractError(
                        "screening result artifact completed file differs on resume"
                    )
                continue
            if item.relative_path.startswith("candidates/"):
                resumed.write_candidate(item)
            else:
                resumed.write_completed_partition(item)
        return cls(resumed, prepared)

    def checkpoint(self) -> dict[str, object]:
        return self._artifact.checkpoint()

    def finalize(self) -> dict[str, object]:
        try:
            return self._artifact.finalize(coverage=self._prepared.coverage)
        except Exception as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ScreeningResultArtifactContractError(
                "screening result artifact finalize failed"
            ) from error


def _prepare(
    screening: ScreeningResult,
    candidates: Sequence[CandidateSummary],
    identity: ResultArtifactIdentity,
) -> _PreparedScreeningArtifact:
    """Build all immutable files before any filesystem mutation."""

    if not isinstance(screening, ScreeningResult):
        raise ScreeningResultArtifactContractError("screening result is invalid")
    if not isinstance(candidates, Sequence) or isinstance(
        candidates, (str, bytes, bytearray)
    ):
        raise ScreeningResultArtifactContractError("candidate summaries are invalid")
    if len(candidates) > 20:
        raise ScreeningResultArtifactContractError(
            "candidate summaries exceed the bound"
        )
    if not isinstance(identity, ResultArtifactIdentity):
        raise ScreeningResultArtifactContractError(
            "result artifact identity is invalid"
        )

    context_record = {
        "requested_as_of": screening.requested_as_of,
        "screen_date": screening.screen_date,
        "information_cutoff": screening.information_cutoff,
        "availability_at": screening.availability_at,
        "screening_plan_id": screening.plan_id,
        "public_state": screening.public_state,
    }
    try:
        context = prepare_completed_partition(
            kind="universe",
            exchange="SSE",
            shard_index=0,
            schema_version=_CONTEXT_SCHEMA_VERSION,
            records=(context_record,),
        )
        partitions: list[PreparedResultArtifactFile] = []
        coverage: dict[str, Mapping[str, int]] = {}
        for partition in screening.partitions:
            if partition.exchange not in _EXCHANGES:
                raise ScreeningResultArtifactContractError(
                    "screening exchange is invalid"
                )
            if partition.exchange in coverage:
                raise ScreeningResultArtifactContractError(
                    "screening exchange is duplicated"
                )
            rows = _partition_records(partition.exchange, partition)
            universe_rows, factor_rows, ranking_rows = rows
            counts = dict(partition.coverage)
            expected = {
                "acquired",
                "expected",
                "failed",
                "ranked",
                "resolved",
                "unranked",
                "unsupported",
                "excluded",
            }
            if set(counts) not in (expected, expected - {"excluded"}) or any(
                type(value) is not int or value < 0 for value in counts.values()
            ):
                raise ScreeningResultArtifactContractError(
                    "screening coverage is invalid"
                )
            physical = {
                "expected": len(universe_rows),
                "acquired": len(factor_rows),
                "ranked": len(ranking_rows),
                "unranked": sum(
                    row.get("state") == "unranked" for row in universe_rows
                ),
                "excluded": sum(
                    row.get("state") == "excluded" for row in universe_rows
                ),
                "resolved": sum(row.get("resolved") is True for row in universe_rows),
                "failed": sum(
                    "source_failed"
                    in cast(Sequence[object], row.get("unranked_reasons", ()))
                    for row in universe_rows
                    if row.get("state") == "unranked"
                ),
                "unsupported": sum(
                    "unsupported_source"
                    in cast(Sequence[object], row.get("unranked_reasons", ()))
                    for row in universe_rows
                    if row.get("state") == "unranked"
                ),
            }
            if any(counts.get(key) != value for key, value in physical.items()):
                raise ScreeningResultArtifactContractError(
                    "screening coverage does not reconcile with artifact rows"
                )
            partitions.extend(
                (
                    prepare_completed_partition(
                        kind="universe",
                        exchange=partition.exchange,
                        shard_index=0,
                        schema_version=_ARTIFACT_SCHEMA_VERSION,
                        records=rows[0],
                    ),
                    prepare_completed_partition(
                        kind="factors",
                        exchange=partition.exchange,
                        shard_index=0,
                        schema_version=_ARTIFACT_SCHEMA_VERSION,
                        records=rows[1],
                    ),
                    prepare_completed_partition(
                        kind="ranking",
                        exchange=partition.exchange,
                        shard_index=0,
                        schema_version=_ARTIFACT_SCHEMA_VERSION,
                        records=rows[2],
                    ),
                )
            )
            coverage[partition.exchange] = counts
        if set(coverage) != set(_EXCHANGES):
            raise ScreeningResultArtifactContractError(
                "screening coverage must contain all exchanges"
            )

        candidate_files: list[PreparedResultArtifactFile] = []
        ranked_rows = {
            cast(str, row["canonical_security_id"]): row
            for partition in screening.partitions
            for row in partition.ranking
        }
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, CandidateSummary):
                raise ScreeningResultArtifactContractError(
                    "candidate summary is invalid"
                )
            if candidate.canonical_security_id in seen:
                raise ScreeningResultArtifactContractError(
                    "candidate IDs are duplicated"
                )
            ranked = ranked_rows.get(candidate.canonical_security_id)
            if ranked is None:
                raise ScreeningResultArtifactContractError(
                    "candidate is not present in screening ranking"
                )
            if (
                ranked.get("rank") != candidate.rank
                or ranked.get("research_priority_score")
                != candidate.research_priority_score
                or ranked.get("score_tied", False) != candidate.score_tied
            ):
                raise ScreeningResultArtifactContractError(
                    "candidate summary differs from screening ranking"
                )
            seen.add(candidate.canonical_security_id)
            document = candidate.to_summary_dict()
            document.update(
                {
                    "requested_as_of": screening.requested_as_of,
                    "screen_date": screening.screen_date,
                    "information_cutoff": screening.information_cutoff,
                }
            )
            candidate_files.append(
                prepare_candidate(
                    canonical_security_id=candidate.canonical_security_id,
                    schema_version=_ARTIFACT_SCHEMA_VERSION,
                    document=document,
                )
            )

        ResultArtifact.checkpoint_capacity(
            identity,
            partition_count=len(partitions),
            candidate_count=len(candidate_files),
            initial_context=context,
        )
    except ScreeningResultArtifactContractError:
        raise
    except Exception as error:
        raise ScreeningResultArtifactContractError(
            "screening result artifact inputs are invalid"
        ) from error
    return _PreparedScreeningArtifact(
        context=context,
        partitions=tuple(partitions),
        candidates=tuple(candidate_files),
        coverage=coverage,
    )


def _partition_records(
    exchange: str,
    partition: Any,
) -> tuple[
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
    tuple[Mapping[str, object], ...],
]:
    """Return universe, factor, and ranking rows with one stable owner each."""

    if (
        not isinstance(partition.excluded, tuple)
        or not isinstance(partition.unranked, tuple)
        or not isinstance(partition.ranking, tuple)
        or not isinstance(partition.factors, tuple)
    ):
        raise ScreeningResultArtifactContractError(
            "screening partition records are invalid"
        )
    universe: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for state, rows in (
        ("excluded", partition.excluded),
        ("unranked", partition.unranked),
        ("ranked", partition.ranking),
    ):
        for row in rows:
            if (
                not isinstance(row, Mapping)
                or not isinstance(row.get("canonical_security_id"), str)
                or type(row.get("resolved")) is not bool
            ):
                raise ScreeningResultArtifactContractError(
                    "screening partition row is invalid"
                )
            canonical = cast(str, row["canonical_security_id"])
            _validate_security_id(canonical, exchange)
            if canonical in seen:
                raise ScreeningResultArtifactContractError(
                    "screening partition contains duplicate security IDs"
                )
            seen.add(canonical)
            value = dict(row)
            value["state"] = state
            universe.append(value)
    factors: list[Mapping[str, object]] = []
    factor_seen: set[str] = set()
    for row in partition.factors:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("canonical_security_id"), str
        ):
            raise ScreeningResultArtifactContractError(
                "screening factor row is invalid"
            )
        canonical = cast(str, row["canonical_security_id"])
        _validate_security_id(canonical, exchange)
        if canonical not in seen or canonical in factor_seen:
            raise ScreeningResultArtifactContractError(
                "screening factor row has an unknown or duplicate security ID"
            )
        factor_seen.add(canonical)
        factors.append(dict(row))
    ranking = tuple(dict(row) for row in partition.ranking)
    return tuple(universe), tuple(factors), ranking


def _validate_security_id(canonical: str, exchange: str) -> None:
    observed_exchange, separator, code = canonical.partition(":")
    if (
        separator != ":"
        or observed_exchange != exchange
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        raise ScreeningResultArtifactContractError(
            "screening row security ID does not match its exchange partition"
        )


# Function forms make the seam convenient to call from orchestration while the
# class keeps create/resume/finalize state explicit.
def create_screening_result_artifact(
    *args: Any, **kwargs: Any
) -> ScreeningResultArtifact:
    return ScreeningResultArtifact.create(*args, **kwargs)


def resume_screening_result_artifact(
    *args: Any, **kwargs: Any
) -> ScreeningResultArtifact | dict[str, object]:
    return ScreeningResultArtifact.resume(*args, **kwargs)


__all__ = [
    "ScreeningResultArtifact",
    "ScreeningResultArtifactContractError",
    "create_screening_result_artifact",
    "resume_screening_result_artifact",
]
