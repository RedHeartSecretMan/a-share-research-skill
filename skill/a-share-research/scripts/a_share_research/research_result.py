"""Operation-owned outcomes and the one public research result envelope."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from hashlib import sha256
from typing import Literal, Mapping, Sequence, cast

CoverageState = Literal[
    "not_requested",
    "skipped_policy",
    "skipped_research_dependency",
    "failed",
    "observed_empty",
    "partial",
    "complete",
]
ResearchStatus = Literal["supported", "limited", "blocked"]
Disposition = Literal[
    "normalized",
    "cross_check_only",
    "lineage_only",
    "excluded",
]

_COVERAGE_STATES = frozenset(
    {
        "not_requested",
        "skipped_policy",
        "skipped_research_dependency",
        "failed",
        "observed_empty",
        "partial",
        "complete",
    }
)
_RESEARCH_STATUSES = frozenset({"supported", "limited", "blocked"})
_DISPOSITIONS = frozenset(
    {"normalized", "cross_check_only", "lineage_only", "excluded"}
)
_DIAGNOSTIC_FORBIDDEN_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "header",
    "host",
    "payload",
    "raw",
    "secret",
    "server",
    "token",
)
_MAX_DIAGNOSTIC_BYTES = 2_048
_MAX_RESULT_BYTES = 2 * 1024 * 1024


def _json_copy(value: object, *, path: str, max_bytes: int) -> object:
    """Return a detached JSON-safe value while rejecting unsafe numbers."""

    def reject_non_finite(candidate: object, candidate_path: str) -> None:
        if isinstance(candidate, float) and not math.isfinite(candidate):
            raise ValueError(f"{candidate_path} contains a non-finite number")
        if isinstance(candidate, Mapping):
            for key, nested in candidate.items():
                if not isinstance(key, str):
                    raise ValueError(f"{candidate_path} keys must be strings")
                reject_non_finite(nested, f"{candidate_path}.{key}")
        elif isinstance(candidate, (list, tuple)):
            for index, nested in enumerate(candidate):
                reject_non_finite(nested, f"{candidate_path}[{index}]")

    reject_non_finite(value, path)
    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{path} must be JSON-safe") from error
    if len(serialized.encode("utf-8")) > max_bytes:
        raise ValueError(f"{path} exceeds its bounded JSON size")
    return deepcopy(value)


def _json_object(
    value: Mapping[str, object], *, path: str, max_bytes: int = _MAX_RESULT_BYTES
) -> dict[str, object]:
    copied = _json_copy(value, path=path, max_bytes=max_bytes)
    if not isinstance(copied, dict):
        raise ValueError(f"{path} must be a JSON object")
    return cast(dict[str, object], copied)


def stable_result_id(kind: str, payload: Mapping[str, object]) -> str:
    """Assign a deterministic ID to one normalized public result object."""

    canonical = json.dumps(
        _json_object(payload, path=kind),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{kind}-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], *, path: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(f"{path} does not match the strict result schema")


def _validate_diagnostic_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = key.casefold()
            if any(
                fragment in normalized for fragment in _DIAGNOSTIC_FORBIDDEN_FRAGMENTS
            ):
                raise ValueError("operation diagnostics contain a sensitive field")
            _validate_diagnostic_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_diagnostic_keys(nested)


@dataclass(frozen=True)
class FieldDisposition:
    """The destination decision for one field handled by one operation."""

    field: str
    disposition: Disposition
    destination: str | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("field disposition requires a non-empty field")
        if self.disposition not in _DISPOSITIONS:
            raise ValueError("field disposition uses an unsupported decision")
        if self.disposition == "excluded":
            if self.destination is not None or not self.reason:
                raise ValueError("excluded fields require only a reason")
        elif not self.destination or self.reason is not None:
            raise ValueError("retained fields require only a destination")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "field": self.field,
            "disposition": self.disposition,
            "destination": self.destination,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class OperationOutcome:
    """All public proof owned by exactly one source operation."""

    capability_id: str
    operation_id: str
    coverage: CoverageState
    observations: tuple[Mapping[str, object], ...] = ()
    source_errors: tuple[Mapping[str, object], ...] = ()
    field_dispositions: tuple[FieldDisposition, ...] = ()
    diagnostics: Mapping[str, object] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.operation_id.strip():
            raise ValueError("operation outcome identity must be non-empty")
        if self.coverage not in _COVERAGE_STATES:
            raise ValueError("operation outcome uses unsupported coverage")
        observations = tuple(
            _json_object(item, path="operation observation")
            for item in self.observations
        )
        source_errors = tuple(
            _json_object(item, path="operation source error")
            for item in self.source_errors
        )
        diagnostics = dict(self.diagnostics)
        _validate_diagnostic_keys(diagnostics)
        diagnostics = _json_object(
            diagnostics,
            path="operation diagnostics",
            max_bytes=_MAX_DIAGNOSTIC_BYTES,
        )
        self._validate_ownership(observations, source_errors)
        fields = [item.field for item in self.field_dispositions]
        if len(fields) != len(set(fields)):
            raise ValueError("operation field dispositions must be unique")
        if self.coverage in {
            "not_requested",
            "skipped_policy",
            "skipped_research_dependency",
            "observed_empty",
        } and (observations or source_errors):
            raise ValueError("operation coverage contradicts retained output")
        if self.coverage == "failed" and (observations or not source_errors):
            raise ValueError("failed operation requires only source errors")
        if self.coverage == "complete" and (not observations or source_errors):
            raise ValueError("complete operation requires observations without errors")
        if self.coverage == "partial" and not observations:
            raise ValueError("partial operation requires retained observations")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "source_errors", source_errors)
        object.__setattr__(self, "diagnostics", diagnostics)

    def _validate_ownership(
        self,
        observations: Sequence[Mapping[str, object]],
        source_errors: Sequence[Mapping[str, object]],
    ) -> None:
        for observation in observations:
            if observation.get("source_operation") != self.operation_id:
                raise ValueError("operation cannot own a sibling observation")
        for error in source_errors:
            if error.get("source_operation") != self.operation_id:
                raise ValueError("operation cannot own a sibling source error")

    def to_coverage_dict(
        self,
        *,
        observation_ids: Sequence[str],
        source_error_ids: Sequence[str],
    ) -> dict[str, object]:
        self._validate_ownership(self.observations, self.source_errors)
        _validate_diagnostic_keys(self.diagnostics)
        if len(observation_ids) != len(self.observations):
            raise ValueError("operation observation ownership is incomplete")
        if len(source_error_ids) != len(self.source_errors):
            raise ValueError("operation source-error ownership is incomplete")
        return {
            "capability_id": self.capability_id,
            "operation_id": self.operation_id,
            "state": self.coverage,
            "observation_ids": list(observation_ids),
            "source_error_ids": list(source_error_ids),
            "field_dispositions": [item.to_dict() for item in self.field_dispositions],
            "diagnostics": _json_copy(
                self.diagnostics,
                path="operation diagnostics",
                max_bytes=_MAX_DIAGNOSTIC_BYTES,
            ),
        }


@dataclass(frozen=True)
class UnifiedResearchResult:
    """The strict, version-1 public result returned by ResearchRuntime."""

    status: ResearchStatus
    research_anchor: Mapping[str, object]
    plan: Mapping[str, object]
    capability_registry: Mapping[str, object]
    coverage: Mapping[str, object]
    observations: tuple[Mapping[str, object], ...]
    claims: tuple[Mapping[str, object], ...]
    calculations: tuple[Mapping[str, object], ...]
    conflicts: tuple[Mapping[str, object], ...]
    limitations: tuple[Mapping[str, object], ...]
    source_errors: tuple[Mapping[str, object], ...]
    task_views: Mapping[str, object]
    briefs: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        if self.status not in _RESEARCH_STATUSES:
            raise ValueError("research result uses unsupported status")
        result: dict[str, object] = {
            "schema_version": "1.0",
            "status": self.status,
            "research_anchor": _json_object(
                self.research_anchor, path="result research anchor"
            ),
            "plan": _json_object(self.plan, path="result plan"),
            "capability_registry": _json_object(
                self.capability_registry, path="result capability registry"
            ),
            "coverage": _json_object(self.coverage, path="result coverage"),
            "observations": [
                _json_object(item, path="result observation")
                for item in self.observations
            ],
            "claims": [_json_object(item, path="result claim") for item in self.claims],
            "calculations": [
                _json_object(item, path="result calculation")
                for item in self.calculations
            ],
            "conflicts": [
                _json_object(item, path="result conflict") for item in self.conflicts
            ],
            "limitations": [
                _json_object(item, path="result limitation")
                for item in self.limitations
            ],
            "source_errors": [
                _json_object(item, path="result source error")
                for item in self.source_errors
            ],
            "task_views": _json_object(self.task_views, path="result task views"),
            "briefs": [_json_object(item, path="result brief") for item in self.briefs],
        }
        self._validate_public_ownership(result)
        _require_exact_keys(
            result,
            {
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
            },
            path="research result",
        )
        bounded = _json_copy(
            result, path="research result", max_bytes=_MAX_RESULT_BYTES
        )
        if not isinstance(bounded, dict):
            raise ValueError("research result must be a JSON object")
        return cast(dict[str, object], bounded)

    def _validate_public_ownership(self, result: Mapping[str, object]) -> None:
        research_anchor = cast(dict[str, object], result["research_anchor"])
        _require_exact_keys(
            research_anchor,
            {"as_of", "timezone"},
            path="research anchor",
        )
        if (
            not isinstance(research_anchor.get("as_of"), str)
            or not research_anchor["as_of"]
            or research_anchor.get("timezone") != "Asia/Shanghai"
        ):
            raise ValueError("research anchor is invalid")
        plan = cast(dict[str, object], result["plan"])
        _require_exact_keys(
            plan,
            {"id", "version", "task_type", "capabilities"},
            path="research plan",
        )
        capability_registry = cast(dict[str, object], result["capability_registry"])
        for capability_id, capability_value in capability_registry.items():
            if not isinstance(capability_value, dict):
                raise ValueError("capability registration must be a JSON object")
            _require_exact_keys(
                capability_value,
                {"id", "version", "granularity", "operations"},
                path="capability registration",
            )
            if capability_value.get("id") != capability_id:
                raise ValueError("capability registration identity is inconsistent")
            registered_operations = capability_value.get("operations")
            if not isinstance(registered_operations, list):
                raise ValueError("registered operations must be a JSON array")
            for registered_operation in registered_operations:
                if not isinstance(registered_operation, dict):
                    raise ValueError("operation registration must be a JSON object")
                _require_exact_keys(
                    registered_operation,
                    {"id", "source_role", "experimental"},
                    path="operation registration",
                )

        observations = cast(list[dict[str, object]], result["observations"])
        source_errors = cast(list[dict[str, object]], result["source_errors"])
        claims = cast(list[dict[str, object]], result["claims"])
        calculations = cast(list[dict[str, object]], result["calculations"])
        conflicts = cast(list[dict[str, object]], result["conflicts"])
        limitations = cast(list[dict[str, object]], result["limitations"])
        briefs = cast(list[dict[str, object]], result["briefs"])

        for claim in claims:
            _require_exact_keys(
                claim,
                {"id", "kind", "observation_ids", "payload"},
                path="claim",
            )
        for calculation in calculations:
            _require_exact_keys(
                calculation,
                {"id", "kind", "input_ids", "payload"},
                path="calculation",
            )
        for conflict in conflicts:
            _require_exact_keys(
                conflict,
                {"id", "code", "message", "observation_ids"},
                path="conflict",
            )
        for limitation in limitations:
            _require_exact_keys(
                limitation,
                {"id", "code", "message", "references"},
                path="limitation",
            )
        for brief in briefs:
            _require_exact_keys(
                brief,
                {
                    "id",
                    "status",
                    "summary",
                    "observation_ids",
                    "claim_ids",
                    "calculation_ids",
                    "conflict_ids",
                    "limitation_ids",
                    "source_error_ids",
                },
                path="brief",
            )
            if brief.get("status") != self.status:
                raise ValueError("brief status contradicts result status")

        observation_owners: dict[str, tuple[str, str]] = {}
        for observation in observations:
            _require_exact_keys(
                observation,
                {"id", "capability_id", "operation_id", "payload"},
                path="observation",
            )
            observation_id = observation.get("id")
            observation_capability_id = observation.get("capability_id")
            observation_operation_id = observation.get("operation_id")
            if (
                not isinstance(observation_id, str)
                or not observation_id
                or not isinstance(observation_capability_id, str)
                or not observation_capability_id
                or not isinstance(observation_operation_id, str)
                or not observation_operation_id
            ):
                raise ValueError("observation ownership must be explicit")
            observation_owners[observation_id] = (
                observation_capability_id,
                observation_operation_id,
            )

        source_error_owners: dict[str, tuple[str, str]] = {}
        for source_error in source_errors:
            _require_exact_keys(
                source_error,
                {
                    "id",
                    "capability_id",
                    "operation_id",
                    "code",
                    "message",
                },
                path="source error",
            )
            error_id = source_error.get("id")
            error_capability_id = source_error.get("capability_id")
            error_operation_id = source_error.get("operation_id")
            if (
                not isinstance(error_id, str)
                or not error_id
                or not isinstance(error_capability_id, str)
                or not error_capability_id
                or not isinstance(error_operation_id, str)
                or not error_operation_id
            ):
                raise ValueError("source-error ownership must be explicit")
            source_error_owners[error_id] = (
                error_capability_id,
                error_operation_id,
            )

        coverage = cast(dict[str, object], result["coverage"])
        for capability_id, capability_value in coverage.items():
            if not isinstance(capability_value, dict):
                raise ValueError("capability coverage must be a JSON object")
            _require_exact_keys(
                capability_value,
                {"state", "operations"},
                path="capability coverage",
            )
            if capability_value.get("state") not in _COVERAGE_STATES:
                raise ValueError("capability coverage uses unsupported state")
            operations = capability_value.get("operations")
            if not isinstance(operations, dict):
                raise ValueError("capability operations must be a JSON object")
            for operation_id, operation_value in operations.items():
                if not isinstance(operation_value, dict):
                    raise ValueError("operation coverage must be a JSON object")
                _require_exact_keys(
                    operation_value,
                    {
                        "capability_id",
                        "operation_id",
                        "state",
                        "observation_ids",
                        "source_error_ids",
                        "field_dispositions",
                        "diagnostics",
                    },
                    path="operation coverage",
                )
                if (
                    operation_value.get("capability_id") != capability_id
                    or operation_value.get("operation_id") != operation_id
                ):
                    raise ValueError("operation coverage identity is inconsistent")
                dispositions = operation_value.get("field_dispositions")
                if not isinstance(dispositions, list):
                    raise ValueError("field dispositions must be a JSON array")
                for disposition in dispositions:
                    if not isinstance(disposition, dict):
                        raise ValueError("field disposition must be a JSON object")
                    expected = {"field", "disposition", "destination"}
                    if disposition.get("disposition") == "excluded":
                        expected.add("reason")
                    _require_exact_keys(disposition, expected, path="field disposition")
                owner = (capability_id, operation_id)
                for observation_id in cast(
                    list[object], operation_value["observation_ids"]
                ):
                    if observation_owners.get(cast(str, observation_id)) != owner:
                        raise ValueError("operation references a sibling observation")
                for error_id in cast(list[object], operation_value["source_error_ids"]):
                    if source_error_owners.get(cast(str, error_id)) != owner:
                        raise ValueError("operation references a sibling source error")

        ids_by_reference_field = {
            "observation_ids": {
                cast(str, item["id"])
                for item in observations
                if isinstance(item.get("id"), str)
            },
            "claim_ids": {
                cast(str, item["id"])
                for item in claims
                if isinstance(item.get("id"), str)
            },
            "calculation_ids": {
                cast(str, item["id"])
                for item in calculations
                if isinstance(item.get("id"), str)
            },
            "conflict_ids": {
                cast(str, item["id"])
                for item in conflicts
                if isinstance(item.get("id"), str)
            },
            "limitation_ids": {
                cast(str, item["id"])
                for item in limitations
                if isinstance(item.get("id"), str)
            },
            "source_error_ids": {
                cast(str, item["id"])
                for item in source_errors
                if isinstance(item.get("id"), str)
            },
        }
        brief_ids = {
            cast(str, item["id"]) for item in briefs if isinstance(item.get("id"), str)
        }
        task_views = cast(dict[str, object], result["task_views"])
        task_type = plan.get("task_type")
        view = task_views.get(task_type) if isinstance(task_type, str) else None
        if not isinstance(view, dict):
            raise ValueError("research result requires its task view")
        _require_exact_keys(
            view,
            {
                "status",
                "observation_ids",
                "claim_ids",
                "calculation_ids",
                "conflict_ids",
                "limitation_ids",
                "source_error_ids",
                "brief_id",
            },
            path="task view",
        )
        if view.get("status") != self.status:
            raise ValueError("task-view status contradicts result status")
        for field in (
            "observation_ids",
            "claim_ids",
            "calculation_ids",
            "conflict_ids",
            "limitation_ids",
            "source_error_ids",
        ):
            references = view.get(field)
            if not isinstance(references, list) or any(
                not isinstance(item, str) or item not in ids_by_reference_field[field]
                for item in references
            ):
                raise ValueError("task view contains an unknown result reference")
        brief_id = view.get("brief_id")
        if not isinstance(brief_id, str) or brief_id not in brief_ids:
            raise ValueError("task view contains an unknown brief reference")
        for brief in briefs:
            for field, known_ids in ids_by_reference_field.items():
                references = brief.get(field)
                if not isinstance(references, list) or any(
                    not isinstance(item, str) or item not in known_ids
                    for item in references
                ):
                    raise ValueError("brief contains an unknown result reference")


__all__ = [
    "CoverageState",
    "Disposition",
    "FieldDisposition",
    "OperationOutcome",
    "ResearchStatus",
    "UnifiedResearchResult",
    "stable_result_id",
]
