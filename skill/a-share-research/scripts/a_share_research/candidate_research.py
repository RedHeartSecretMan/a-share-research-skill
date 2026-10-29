"""Bounded candidate deep research over deterministic screening output.

The module is an internal seam for the deferred screening workflow.  It owns
candidate selection, frozen-context propagation, failure isolation and bounded
candidate summaries; it does not acquire live sources or mutate the screening
universe, factors or ranking.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import cast

from .screening import ScreeningResult


class CandidateResearchContractError(ValueError):
    """A candidate selection or frozen-context contract violation."""

    def __init__(
        self, message: str, *, code: str = "candidate_contract_invalid"
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CandidateResearchContext:
    """Immutable screening boundaries shared by every candidate."""

    requested_as_of: str
    screen_date: str
    information_cutoff: str

    def __post_init__(self) -> None:
        for name in ("requested_as_of", "screen_date"):
            value = getattr(self, name)
            try:
                parsed = date.fromisoformat(value)
            except (TypeError, ValueError):
                raise CandidateResearchContractError(
                    f"candidate research {name} must be an ISO date"
                ) from None
            if parsed.isoformat() != value:
                raise CandidateResearchContractError(
                    f"candidate research {name} must be an ISO date"
                )
        try:
            cutoff = datetime.fromisoformat(self.information_cutoff)
        except (TypeError, ValueError):
            raise CandidateResearchContractError(
                "candidate research information_cutoff must be an aware timestamp"
            ) from None
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise CandidateResearchContractError(
                "candidate research information_cutoff must be an aware timestamp"
            )


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    canonical_security_id: str
    rank: int
    research_priority_score: str
    score_tied: bool = False


def _score(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CandidateResearchContractError(
            "candidate ranking score must be finite decimal"
        ) from None
    if not parsed.is_finite():
        raise CandidateResearchContractError(
            "candidate ranking score must be finite decimal"
        )
    return parsed


def _selection(row: Mapping[str, object]) -> CandidateSelection:
    canonical = row.get("canonical_security_id")
    rank = row.get("rank")
    if (
        not isinstance(canonical, str)
        or not canonical
        or isinstance(rank, bool)
        or not isinstance(rank, int)
        or rank < 1
        or ("score_tied" in row and not isinstance(row["score_tied"], bool))
    ):
        raise CandidateResearchContractError("candidate ranking row is invalid")
    score = _score(row.get("research_priority_score"))
    score_tied = row.get("score_tied", False)
    assert isinstance(score_tied, bool)
    return CandidateSelection(
        canonical_security_id=canonical,
        rank=rank,
        research_priority_score=format(score, ".6f"),
        score_tied=score_tied,
    )


def select_candidates(
    ranking: Sequence[Mapping[str, object]],
    *,
    completeness_mode: str,
    candidate_limit: int | None = None,
) -> tuple[CandidateSelection, ...]:
    """Select a bounded deterministic prefix from screening ranking rows.

    Ranking rows are copied into immutable selections and sorted by the public
    score/id keys.  The input sequence is never modified.
    """

    if completeness_mode not in {"fast", "standard"}:
        raise CandidateResearchContractError(
            "candidate research completeness_mode is unsupported"
        )
    default_limit = 0 if completeness_mode == "fast" else 5
    limit = default_limit if candidate_limit is None else candidate_limit
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise CandidateResearchContractError("candidate_limit must be an integer")
    if completeness_mode == "fast" and limit != 0:
        raise CandidateResearchContractError(
            "fast candidate_limit must be omitted or zero"
        )
    if completeness_mode == "standard" and not 1 <= limit <= 20:
        raise CandidateResearchContractError(
            "standard candidate_limit must be from 1 to 20"
        )
    selected = [_selection(row) for row in ranking]
    if len({item.canonical_security_id for item in selected}) != len(selected):
        raise CandidateResearchContractError("candidate ranking contains duplicate IDs")
    selected.sort(
        key=lambda item: (
            -_score(item.research_priority_score),
            item.canonical_security_id,
        )
    )
    return tuple(selected[:limit])


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    """Bounded public projection of one candidate workflow result."""

    canonical_security_id: str
    rank: int
    research_priority_score: str
    score_tied: bool
    state: str
    evidence_references: Mapping[str, tuple[str, ...]]
    limitation_references: tuple[str, ...]
    brief_references: tuple[str, ...]
    workflow_lineage: Mapping[str, object]
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_references",
            MappingProxyType(dict(self.evidence_references)),
        )
        object.__setattr__(
            self, "workflow_lineage", MappingProxyType(dict(self.workflow_lineage))
        )

    def to_summary_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "canonical_security_id": self.canonical_security_id,
            "rank": self.rank,
            "research_priority_score": self.research_priority_score,
            "score_tied": self.score_tied,
            "state": self.state,
            "evidence_references": {
                key: list(refs) for key, refs in self.evidence_references.items()
            },
            "limitation_references": list(self.limitation_references),
            "brief_references": list(self.brief_references),
            "workflow_lineage": dict(self.workflow_lineage),
        }
        if self.error_code is not None:
            value["error_code"] = self.error_code
        return value


def _references(result: Mapping[str, object], field: str) -> tuple[str, ...]:
    values = result.get(field, [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise CandidateResearchContractError(
            f"candidate result {field} must be an array"
        )
    refs: list[str] = []
    for item in values:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise CandidateResearchContractError(
                f"candidate result {field} record is invalid"
            )
        refs.append(cast(str, item["id"]))
    return tuple(refs)


def summarize_candidate(
    selection: CandidateSelection,
    result: Mapping[str, object],
) -> CandidateSummary:
    """Project one single-security result without copying full leaf records."""

    status = result.get("status")
    if status not in {"supported", "limited", "blocked", "failed"}:
        raise CandidateResearchContractError("candidate result status is invalid")
    view = result.get("task_views")
    workflow_view = view.get("research_workflow") if isinstance(view, Mapping) else None
    lineage: dict[str, object] = {
        "workflow_id": "single_security_research",
        "workflow_version": "1.0",
    }
    if isinstance(workflow_view, Mapping):
        lineage.update(
            {
                "step_ids": tuple(
                    item.get("step_id")
                    for item in workflow_view.get("steps", [])
                    if isinstance(item, Mapping)
                    and isinstance(item.get("step_id"), str)
                )
            }
        )
    return CandidateSummary(
        canonical_security_id=selection.canonical_security_id,
        rank=selection.rank,
        research_priority_score=selection.research_priority_score,
        score_tied=selection.score_tied,
        state=cast(str, status),
        evidence_references={
            field: _references(result, field)
            for field in (
                "observations",
                "claims",
                "calculations",
                "conflicts",
                "source_errors",
            )
        },
        limitation_references=_references(result, "limitations"),
        brief_references=_references(result, "briefs"),
        workflow_lineage=lineage,
    )


def research_candidates(
    ranking: Sequence[Mapping[str, object]],
    *,
    context: CandidateResearchContext,
    completeness_mode: str,
    candidate_limit: int | None = None,
    run_single_security: Callable[
        [CandidateSelection, CandidateResearchContext], object
    ]
    | None = None,
) -> tuple[CandidateSummary, ...]:
    """Run independent candidate workflows and isolate one candidate failure."""

    selections = select_candidates(
        ranking,
        completeness_mode=completeness_mode,
        candidate_limit=candidate_limit,
    )
    if run_single_security is None or not selections:
        return tuple(
            CandidateSummary(
                canonical_security_id=item.canonical_security_id,
                rank=item.rank,
                research_priority_score=item.research_priority_score,
                score_tied=item.score_tied,
                state="not_requested",
                evidence_references={},
                limitation_references=(),
                brief_references=(),
                workflow_lineage={
                    "workflow_id": "single_security_research",
                    "workflow_version": "1.0",
                },
            )
            for item in selections
        )
    summaries: list[CandidateSummary] = []
    for selection in selections:
        try:
            result = run_single_security(selection, context)
        except Exception as error:
            summaries.append(
                CandidateSummary(
                    canonical_security_id=selection.canonical_security_id,
                    rank=selection.rank,
                    research_priority_score=selection.research_priority_score,
                    score_tied=selection.score_tied,
                    state="failed",
                    evidence_references={},
                    limitation_references=(),
                    brief_references=(),
                    workflow_lineage={
                        "workflow_id": "single_security_research",
                        "workflow_version": "1.0",
                    },
                    error_code=(
                        getattr(error, "code", None) or "candidate_research_failed"
                    ),
                )
            )
            continue
        if not isinstance(result, Mapping) and hasattr(result, "to_dict"):
            result = result.to_dict()
        if not isinstance(result, Mapping):
            raise CandidateResearchContractError("candidate result is invalid")
        summaries.append(summarize_candidate(selection, result))
    return tuple(summaries)


def ranking_rows_from_screening(
    screening: ScreeningResult,
) -> tuple[Mapping[str, object], ...]:
    """Flatten screening partitions into the immutable global ranking order."""

    rows: list[Mapping[str, object]] = []
    for partition in screening.partitions:
        rows.extend(partition.ranking)
    for row in rows:
        _selection(row)
    if len({row.get("canonical_security_id") for row in rows}) != len(rows):
        raise CandidateResearchContractError("screening ranking contains duplicate IDs")
    rows.sort(
        key=lambda row: (
            -_score(row.get("research_priority_score")),
            cast(str, row.get("canonical_security_id")),
        )
    )
    return tuple(dict(row) for row in rows)


def run_candidate_deep_research(
    screening: ScreeningResult,
    *,
    context: CandidateResearchContext,
    completeness_mode: str,
    candidate_limit: int | None = None,
    run_single_security: Callable[
        [CandidateSelection, CandidateResearchContext], object
    ]
    | None = None,
) -> tuple[CandidateSummary, ...]:
    """Run bounded deep research while preserving the frozen screening scope."""

    if (screening.requested_as_of != context.requested_as_of) or (
        screening.screen_date != context.screen_date
        or screening.information_cutoff != context.information_cutoff
    ):
        raise CandidateResearchContractError(
            "candidate research context conflicts with screening scope"
        )
    selected_limit = (
        (0 if completeness_mode == "fast" else 5)
        if candidate_limit is None
        else candidate_limit
    )
    summaries = research_candidates(
        ranking_rows_from_screening(screening),
        context=context,
        completeness_mode=completeness_mode,
        candidate_limit=selected_limit,
        run_single_security=run_single_security,
    )
    return summaries


__all__ = [
    "CandidateResearchContext",
    "CandidateResearchContractError",
    "CandidateSelection",
    "CandidateSummary",
    "ranking_rows_from_screening",
    "research_candidates",
    "run_candidate_deep_research",
    "select_candidates",
    "summarize_candidate",
]
