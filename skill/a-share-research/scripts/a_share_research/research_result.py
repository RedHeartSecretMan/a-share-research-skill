"""Operation-owned outcomes and the one public research result envelope."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
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
OperationExecutionState = Literal[
    "not_requested",
    "skipped_policy",
    "skipped_research_dependency",
    "failed",
    "succeeded",
]
EvidenceUse = Literal[
    "security_identity",
    "attribution_lineage",
    "issuer_relationship",
]
EvidenceRole = Literal[
    "supporting",
    "corroborating",
    "lineage",
    "conditional_supporting",
    "not_applicable",
]
AdoptionDecision = Literal["adopted", "not_applicable"]
AdoptionRole = Literal["supporting", "corroborating", "lineage"]

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
_EXECUTION_STATES = frozenset(
    {
        "not_requested",
        "skipped_policy",
        "skipped_research_dependency",
        "failed",
        "succeeded",
    }
)
_EVIDENCE_USES = frozenset(
    {"security_identity", "attribution_lineage", "issuer_relationship"}
)
_EVIDENCE_ROLES = frozenset(
    {
        "supporting",
        "corroborating",
        "lineage",
        "conditional_supporting",
        "not_applicable",
    }
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


def _derive_operation_coverage(
    execution: OperationExecution,
    observations: Sequence[Mapping[str, object]],
    source_errors: Sequence[Mapping[str, object]],
) -> CoverageState:
    """Apply the sole operation coverage truth table."""

    if execution.state in {
        "not_requested",
        "skipped_policy",
        "skipped_research_dependency",
    }:
        if observations or source_errors:
            raise ValueError("skipped operation execution cannot retain output")
        return cast(CoverageState, execution.state)
    if execution.state == "failed":
        if not source_errors:
            raise ValueError("failed operation execution requires a source error")
        return "partial" if observations else "failed"
    if source_errors:
        raise ValueError("successful operation execution cannot retain source errors")
    if execution.query_complete is False:
        return "partial"
    return "complete" if observations else "observed_empty"


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
class OperationExecution:
    """Facts from one operation attempt, before coverage is derived."""

    state: OperationExecutionState
    query_complete: bool | None = None

    def __post_init__(self) -> None:
        if self.state not in _EXECUTION_STATES:
            raise ValueError("operation execution uses an unsupported state")
        if self.state == "succeeded":
            if not isinstance(self.query_complete, bool):
                raise ValueError(
                    "successful operation execution requires query completeness"
                )
        elif self.query_complete is not None:
            raise ValueError(
                "only successful operation execution can report query completeness"
            )


@dataclass(frozen=True)
class EvidenceUsePolicy:
    """One operation's declared fitness for one evidence use."""

    use: EvidenceUse
    role: EvidenceRole

    def __post_init__(self) -> None:
        if self.use not in _EVIDENCE_USES or self.role not in _EVIDENCE_ROLES:
            raise ValueError("operation evidence-use policy is unsupported")

    def to_dict(self) -> dict[str, object]:
        return {"use": self.use, "role": self.role}


@dataclass(frozen=True)
class OperationObligation:
    """The single versioned-plan definition of one operation obligation."""

    capability_id: str
    operation_id: str
    source_role: str
    experimental: bool
    exchange: str | None
    evidence_uses: tuple[EvidenceUsePolicy, ...]

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.operation_id.strip():
            raise ValueError("operation obligation identity must be non-empty")
        if not self.source_role.strip():
            raise ValueError("operation obligation source role must be non-empty")
        if self.exchange not in {None, "SSE", "SZSE"}:
            raise ValueError("operation obligation exchange scope is invalid")
        uses = [item.use for item in self.evidence_uses]
        if set(uses) != _EVIDENCE_USES or len(uses) != len(set(uses)):
            raise ValueError("operation obligation must decide every evidence use")

    def evidence_role(self, use: EvidenceUse) -> EvidenceRole:
        return next(item.role for item in self.evidence_uses if item.use == use)

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "operation_id": self.operation_id,
            "source_role": self.source_role,
            "experimental": self.experimental,
            "exchange": self.exchange,
            "evidence_uses": [item.to_dict() for item in self.evidence_uses],
        }


@dataclass(frozen=True)
class ResearchDependency:
    """An explicit evidence-use dependency for one derived consumer."""

    consumer_id: str
    evidence_use: EvidenceUse
    minimum_supporting_observations: int
    core: bool

    def __post_init__(self) -> None:
        if not self.consumer_id.strip() or self.evidence_use not in _EVIDENCE_USES:
            raise ValueError("research dependency is invalid")
        if self.minimum_supporting_observations < 1:
            raise ValueError("research dependency requires positive support")

    def to_dict(self) -> dict[str, object]:
        return {
            "consumer_id": self.consumer_id,
            "evidence_use": self.evidence_use,
            "minimum_supporting_observations": self.minimum_supporting_observations,
            "core": self.core,
        }


@dataclass(frozen=True)
class IdentityResearchPlan:
    """The complete deterministic input plan for the identity result fold."""

    as_of: str
    exchange_hint: str | None
    obligations: tuple[OperationObligation, ...]
    dependencies: tuple[ResearchDependency, ...]
    precondition_reason: str | None = None
    plan_id: str = "security_identity"
    version: str = "1.0"
    task_type: str = "security_identity"

    def __post_init__(self) -> None:
        operation_ids = [item.operation_id for item in self.obligations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("identity plan operation obligations must be unique")
        if not self.obligations:
            raise ValueError("identity plan requires operation obligations")
        if self.exchange_hint not in {None, "SSE", "SZSE"}:
            raise ValueError("identity plan exchange hint is invalid")


@dataclass(frozen=True)
class OperationOutcome:
    """All public proof owned by exactly one source operation."""

    capability_id: str
    operation_id: str
    execution: OperationExecution
    observations: tuple[Mapping[str, object], ...] = ()
    source_errors: tuple[Mapping[str, object], ...] = ()
    field_dispositions: tuple[FieldDisposition, ...] = ()
    diagnostics: Mapping[str, object] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.operation_id.strip():
            raise ValueError("operation outcome identity must be non-empty")
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
        _derive_operation_coverage(self.execution, observations, source_errors)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "source_errors", source_errors)
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def coverage(self) -> CoverageState:
        return _derive_operation_coverage(
            self.execution,
            self.observations,
            self.source_errors,
        )

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
            "execution_state": self.execution.state,
            "state": self.coverage,
            "query_complete": self.execution.query_complete,
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
            {
                "id",
                "version",
                "task_type",
                "capabilities",
                "operation_obligations",
                "research_dependencies",
            },
            path="research plan",
        )
        operation_obligations = plan.get("operation_obligations")
        if not isinstance(operation_obligations, list):
            raise ValueError("research plan operation obligations must be an array")
        planned_operations: set[tuple[str, str]] = set()
        planned_obligation_contracts: dict[tuple[str, str], OperationObligation] = {}
        for obligation in operation_obligations:
            if not isinstance(obligation, dict):
                raise ValueError("research plan operation obligation must be an object")
            _require_exact_keys(
                obligation,
                {
                    "capability_id",
                    "operation_id",
                    "source_role",
                    "experimental",
                    "exchange",
                    "evidence_uses",
                },
                path="research plan operation obligation",
            )
            evidence_uses = obligation.get("evidence_uses")
            if not isinstance(evidence_uses, list):
                raise ValueError("operation evidence uses must be an array")
            declared_uses: set[str] = set()
            typed_evidence_uses: list[EvidenceUsePolicy] = []
            for evidence_use in evidence_uses:
                if not isinstance(evidence_use, dict):
                    raise ValueError("operation evidence use must be an object")
                _require_exact_keys(
                    evidence_use,
                    {"use", "role"},
                    path="operation evidence use",
                )
                use = evidence_use.get("use")
                role = evidence_use.get("role")
                if use not in _EVIDENCE_USES or role not in _EVIDENCE_ROLES:
                    raise ValueError("operation evidence use is unsupported")
                declared_uses.add(cast(str, use))
                typed_evidence_uses.append(
                    EvidenceUsePolicy(
                        cast(EvidenceUse, use),
                        cast(EvidenceRole, role),
                    )
                )
            capability_id = obligation.get("capability_id")
            operation_id = obligation.get("operation_id")
            if not isinstance(capability_id, str) or not isinstance(operation_id, str):
                raise ValueError("operation obligation identity is invalid")
            if (
                not isinstance(obligation.get("source_role"), str)
                or not isinstance(obligation.get("experimental"), bool)
                or obligation.get("exchange") not in {None, "SSE", "SZSE"}
                or declared_uses != _EVIDENCE_USES
                or len(declared_uses) != len(evidence_uses)
            ):
                raise ValueError("operation obligation contract is invalid")
            owner = (capability_id, operation_id)
            planned_operations.add(owner)
            planned_obligation_contracts[owner] = OperationObligation(
                capability_id=capability_id,
                operation_id=operation_id,
                source_role=cast(str, obligation["source_role"]),
                experimental=cast(bool, obligation["experimental"]),
                exchange=cast(str | None, obligation["exchange"]),
                evidence_uses=tuple(typed_evidence_uses),
            )
        if len(planned_operations) != len(operation_obligations):
            raise ValueError("research plan operation obligations must be unique")
        planned_capabilities = {item[0] for item in planned_operations}
        capabilities = plan.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or any(not isinstance(item, str) or not item for item in capabilities)
            or len(set(capabilities)) != len(capabilities)
            or set(capabilities) != planned_capabilities
        ):
            raise ValueError("research plan capabilities contradict its obligations")
        research_dependencies = plan.get("research_dependencies")
        if not isinstance(research_dependencies, list):
            raise ValueError("research plan dependencies must be an array")
        dependency_consumers: set[str] = set()
        typed_dependencies: list[ResearchDependency] = []
        for dependency in research_dependencies:
            if not isinstance(dependency, dict):
                raise ValueError("research dependency must be an object")
            _require_exact_keys(
                dependency,
                {
                    "consumer_id",
                    "evidence_use",
                    "minimum_supporting_observations",
                    "core",
                },
                path="research dependency",
            )
            consumer_id = dependency.get("consumer_id")
            minimum = dependency.get("minimum_supporting_observations")
            if (
                not isinstance(consumer_id, str)
                or not consumer_id
                or dependency.get("evidence_use") not in _EVIDENCE_USES
                or not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or minimum < 1
                or not isinstance(dependency.get("core"), bool)
            ):
                raise ValueError("research dependency contract is invalid")
            dependency_consumers.add(consumer_id)
            typed_dependencies.append(
                ResearchDependency(
                    consumer_id=consumer_id,
                    evidence_use=cast(EvidenceUse, dependency["evidence_use"]),
                    minimum_supporting_observations=minimum,
                    core=cast(bool, dependency["core"]),
                )
            )
        if len(dependency_consumers) != len(research_dependencies):
            raise ValueError("research dependencies must be unique")
        capability_registry = cast(dict[str, object], result["capability_registry"])
        if set(capability_registry) != planned_capabilities:
            raise ValueError("capability registry contradicts plan capabilities")
        registered_operations: set[tuple[str, str]] = set()
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
            registered_operation_values = capability_value.get("operations")
            if not isinstance(registered_operation_values, list):
                raise ValueError("registered operations must be a JSON array")
            for registered_operation in registered_operation_values:
                if not isinstance(registered_operation, dict):
                    raise ValueError("operation registration must be a JSON object")
                _require_exact_keys(
                    registered_operation,
                    {"id", "source_role", "experimental"},
                    path="operation registration",
                )
                operation_id = registered_operation.get("id")
                if not isinstance(operation_id, str):
                    raise ValueError(
                        "operation registration contradicts its plan obligation"
                    )
                owner = (capability_id, operation_id)
                obligation = planned_obligation_contracts.get(owner)
                if (
                    obligation is None
                    or registered_operation.get("source_role") != obligation.source_role
                    or registered_operation.get("experimental")
                    is not obligation.experimental
                ):
                    raise ValueError(
                        "operation registration contradicts its plan obligation"
                    )
                registered_operations.add((capability_id, operation_id))
            if (
                capability_value.get("version") != plan.get("version")
                or capability_value.get("granularity") != "one_security_identity_claim"
            ):
                raise ValueError("capability registration contract is inconsistent")
        if (
            len(registered_operations)
            != sum(
                len(cast(list[object], item["operations"]))
                for item in capability_registry.values()
                if isinstance(item, dict)
            )
            or registered_operations != planned_operations
        ):
            raise ValueError("capability registry contradicts plan operations")

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
                {
                    "id",
                    "kind",
                    "observation_ids",
                    "evidence_adoptions",
                    "payload",
                },
                path="claim",
            )
            evidence_adoptions = claim.get("evidence_adoptions")
            if not isinstance(evidence_adoptions, list):
                raise ValueError("claim evidence adoptions must be an array")
            for adoption in evidence_adoptions:
                if not isinstance(adoption, dict):
                    raise ValueError("claim evidence adoption must be an object")
                _require_exact_keys(
                    adoption,
                    {"observation_id", "use", "decision", "role", "reason"},
                    path="claim evidence adoption",
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
        if set(coverage) != planned_capabilities:
            raise ValueError("capability coverage contradicts plan capabilities")
        covered_operations: set[tuple[str, str]] = set()
        covered_observation_ids: set[str] = set()
        covered_source_error_ids: set[str] = set()
        all_derived_operation_states: list[CoverageState] = []
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
            derived_operation_states: list[CoverageState] = []
            for operation_id, operation_value in operations.items():
                if not isinstance(operation_value, dict):
                    raise ValueError("operation coverage must be a JSON object")
                _require_exact_keys(
                    operation_value,
                    {
                        "capability_id",
                        "operation_id",
                        "execution_state",
                        "state",
                        "query_complete",
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
                covered_operations.add((capability_id, operation_id))
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
                observation_references = operation_value.get("observation_ids")
                error_references = operation_value.get("source_error_ids")
                if (
                    not isinstance(observation_references, list)
                    or any(not isinstance(item, str) for item in observation_references)
                    or len(set(observation_references)) != len(observation_references)
                    or not isinstance(error_references, list)
                    or any(not isinstance(item, str) for item in error_references)
                    or len(set(error_references)) != len(error_references)
                ):
                    raise ValueError("operation ownership references are invalid")
                for observation_id in observation_references:
                    if observation_owners.get(observation_id) != owner:
                        raise ValueError("operation references a sibling observation")
                    if observation_id in covered_observation_ids:
                        raise ValueError("observation ownership is duplicated")
                    covered_observation_ids.add(observation_id)
                for error_id in error_references:
                    if source_error_owners.get(error_id) != owner:
                        raise ValueError("operation references a sibling source error")
                    if error_id in covered_source_error_ids:
                        raise ValueError("source-error ownership is duplicated")
                    covered_source_error_ids.add(error_id)
                execution_state = operation_value.get("execution_state")
                query_complete = operation_value.get("query_complete")
                if not isinstance(execution_state, str) or (
                    query_complete is not None and not isinstance(query_complete, bool)
                ):
                    raise ValueError("operation execution facts are invalid")
                execution = OperationExecution(
                    cast(OperationExecutionState, execution_state),
                    query_complete=query_complete,
                )
                derived_coverage = _derive_operation_coverage(
                    execution,
                    tuple({} for _ in observation_references),
                    tuple({} for _ in error_references),
                )
                if operation_value.get("state") != derived_coverage:
                    raise ValueError("operation coverage contradicts execution facts")
                derived_operation_states.append(derived_coverage)
                all_derived_operation_states.append(derived_coverage)
            if capability_value.get("state") != _aggregate_capability_coverage(
                derived_operation_states
            ):
                raise ValueError(
                    "capability coverage contradicts derived operation coverage"
                )
        if covered_operations != planned_operations:
            raise ValueError("operation coverage contradicts plan obligations")
        if covered_observation_ids != set(observation_owners):
            raise ValueError("operation observation ownership is incomplete")
        if covered_source_error_ids != set(source_error_owners):
            raise ValueError("operation source-error ownership is incomplete")

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
        if len(ids_by_reference_field["observation_ids"]) != len(observations):
            raise ValueError("research result observation IDs must be unique")
        observations_by_id = {cast(str, item["id"]): item for item in observations}
        for claim in claims:
            claim_observation_ids = claim.get("observation_ids")
            if not isinstance(claim_observation_ids, list) or any(
                not isinstance(item, str)
                or item not in ids_by_reference_field["observation_ids"]
                for item in claim_observation_ids
            ):
                raise ValueError("claim contains an unknown observation reference")
            adoption_uses_by_observation: dict[str, set[str]] = {
                cast(str, item): set() for item in claim_observation_ids
            }
            actual_adoptions: dict[tuple[str, str], dict[str, object]] = {}
            for adoption in cast(list[dict[str, object]], claim["evidence_adoptions"]):
                observation_id = adoption.get("observation_id")
                use = adoption.get("use")
                decision = adoption.get("decision")
                role = adoption.get("role")
                reason = adoption.get("reason")
                if (
                    not isinstance(observation_id, str)
                    or observation_id not in claim_observation_ids
                    or use not in _EVIDENCE_USES
                ):
                    raise ValueError("claim evidence adoption reference is invalid")
                if cast(str, use) in adoption_uses_by_observation[observation_id]:
                    raise ValueError("claim evidence adoption is duplicated")
                adoption_uses_by_observation[observation_id].add(cast(str, use))
                actual_adoptions[(observation_id, cast(str, use))] = adoption
                if decision == "adopted":
                    if role not in {"supporting", "corroborating", "lineage"}:
                        raise ValueError("adopted evidence requires a use role")
                    if reason is not None:
                        raise ValueError(
                            "adopted evidence cannot carry a rejection reason"
                        )
                elif decision == "not_applicable":
                    if role is not None or not isinstance(reason, str) or not reason:
                        raise ValueError("inapplicable evidence requires only a reason")
                else:
                    raise ValueError("claim evidence adoption decision is unsupported")
            if any(
                uses != _EVIDENCE_USES for uses in adoption_uses_by_observation.values()
            ):
                raise ValueError("claim evidence adoption is incomplete")
            expected_adoptions = {
                (observation_id, cast(str, adoption["use"])): adoption
                for observation_id in adoption_uses_by_observation
                for adoption in _identity_evidence_adoptions(
                    observations_by_id[observation_id],
                    planned_obligation_contracts[observation_owners[observation_id]],
                    as_of=cast(str, research_anchor["as_of"]),
                )
            }
            if actual_adoptions != expected_adoptions:
                raise ValueError(
                    "claim evidence adoption contradicts observation eligibility"
                )

        dependency_satisfaction: dict[str, bool] = {}
        for dependency in typed_dependencies:
            if dependency.consumer_id == "security_identity_claim":
                dependency_satisfaction[dependency.consumer_id] = any(
                    claim.get("kind") == "security_identity"
                    and sum(
                        1
                        for adoption in cast(
                            list[dict[str, object]], claim["evidence_adoptions"]
                        )
                        if adoption.get("use") == dependency.evidence_use
                        and adoption.get("decision") == "adopted"
                        and adoption.get("role") == "supporting"
                    )
                    >= dependency.minimum_supporting_observations
                    for claim in claims
                )
            elif dependency.consumer_id == "issuer_relationship":
                dependency_satisfaction[dependency.consumer_id] = any(
                    isinstance(claim.get("payload"), dict)
                    and isinstance(
                        cast(dict[str, object], claim["payload"]).get("issuer"),
                        dict,
                    )
                    and cast(
                        dict[str, object],
                        cast(dict[str, object], claim["payload"])["issuer"],
                    ).get("security_relationship")
                    == "verified"
                    and sum(
                        1
                        for adoption in cast(
                            list[dict[str, object]], claim["evidence_adoptions"]
                        )
                        if adoption.get("use") == dependency.evidence_use
                        and adoption.get("decision") == "adopted"
                        and adoption.get("role") == "supporting"
                    )
                    >= dependency.minimum_supporting_observations
                    for claim in claims
                )
            else:
                raise ValueError("research dependency consumer is unsupported")
        unmet_core_dependency = any(
            dependency.core and not dependency_satisfaction[dependency.consumer_id]
            for dependency in typed_dependencies
        )
        if conflicts or len(claims) != 1 or unmet_core_dependency:
            derived_status: ResearchStatus = "blocked"
        else:
            claim_adoptions = cast(
                list[dict[str, object]], claims[0]["evidence_adoptions"]
            )
            has_security_corroboration = any(
                adoption.get("use") == "security_identity"
                and adoption.get("decision") == "adopted"
                and adoption.get("role") == "corroborating"
                for adoption in claim_adoptions
            )
            is_limited = (
                any(item.experimental for item in planned_obligation_contracts.values())
                or bool(source_errors)
                or any(
                    not dependency_satisfaction[dependency.consumer_id]
                    for dependency in typed_dependencies
                    if not dependency.core
                )
                or not has_security_corroboration
                or any(
                    state not in {"complete", "observed_empty"}
                    for state in all_derived_operation_states
                )
            )
            derived_status = "limited" if is_limited else "supported"
        if result.get("status") != derived_status:
            raise ValueError("research result contradicts its derived result status")
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


_IDENTITY_RESEARCH_DEPENDENCIES = (
    ResearchDependency(
        consumer_id="security_identity_claim",
        evidence_use="security_identity",
        minimum_supporting_observations=1,
        core=True,
    ),
    ResearchDependency(
        consumer_id="issuer_relationship",
        evidence_use="issuer_relationship",
        minimum_supporting_observations=1,
        core=False,
    ),
)
_STATUS_REASON_MESSAGES = {
    "source_policy_not_satisfied": (
        "security_identity currently requires experimental source operations"
    ),
    "invalid_security_clue": (
        "A security clue must be a name, a six-digit code, or an "
        "SSE/SZSE-qualified clue."
    ),
    "unsupported_exchange": "BSE securities are not supported and will not be guessed.",
    "identity_observation_outside_research_date": (
        "Current identity observations cannot establish identity for a different "
        "research date."
    ),
    "experimental_identity_sources": (
        "Identity uses experimental source operations and is not independently "
        "source verified."
    ),
    "issuer_relationship_unverified": (
        "The available observations do not independently verify the issuer "
        "relationship."
    ),
    "identity_source_partially_failed": (
        "A sibling identity operation failed; independent valid observations remain "
        "available."
    ),
    "identity_uncorroborated": (
        "The authoritative security identity observation was not independently "
        "corroborated."
    ),
    "ambiguous_security_clue": ("The clue matches multiple attributable securities."),
    "identity_conflict": "Identity source observations conflict.",
    "source_operation_failed": ("Required identity evidence could not be established."),
    "identity_not_resolved": "No attributable security identity was established.",
}


def compile_identity_research_plan(
    *,
    as_of: str,
    exchange_hint: str | None,
    obligations: Sequence[OperationObligation],
    precondition_reason: str | None = None,
) -> IdentityResearchPlan:
    """Compile the one versioned identity plan consumed by the pure fold."""

    if precondition_reason is not None and precondition_reason not in {
        "source_policy_not_satisfied",
        "invalid_security_clue",
        "unsupported_exchange",
    }:
        raise ValueError("identity plan precondition reason is unsupported")
    return IdentityResearchPlan(
        as_of=as_of,
        exchange_hint=exchange_hint,
        obligations=tuple(obligations),
        dependencies=_IDENTITY_RESEARCH_DEPENDENCIES,
        precondition_reason=precondition_reason,
    )


def fold_identity_research_result(
    plan: IdentityResearchPlan,
    outcomes: Sequence[OperationOutcome],
) -> UnifiedResearchResult:
    """Purely fold one identity plan and its owned outcomes into the public result."""

    obligations = {item.operation_id: item for item in plan.obligations}
    outcomes_by_operation = {item.operation_id: item for item in outcomes}
    if len(outcomes_by_operation) != len(tuple(outcomes)):
        raise ValueError("identity fold operation outcomes must be unique")
    if set(outcomes_by_operation) != set(obligations):
        raise ValueError("identity fold outcomes do not satisfy plan obligations")
    for operation_id, outcome in outcomes_by_operation.items():
        obligation = obligations[operation_id]
        if outcome.capability_id != obligation.capability_id:
            raise ValueError("identity outcome capability contradicts its obligation")

    observation_records: list[dict[str, object]] = []
    observation_ids_by_operation: dict[str, list[str]] = {}
    for obligation in plan.obligations:
        outcome = outcomes_by_operation[obligation.operation_id]
        for payload in outcome.observations:
            record_payload: dict[str, object] = {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "payload": dict(payload),
            }
            observation_id = stable_result_id("observation", record_payload)
            observation_records.append({"id": observation_id, **record_payload})
            observation_ids_by_operation.setdefault(outcome.operation_id, []).append(
                observation_id
            )

    source_errors: list[dict[str, object]] = []
    source_error_ids_by_operation: dict[str, list[str]] = {}
    operation_coverage: dict[str, dict[str, object]] = {}
    for obligation in plan.obligations:
        outcome = outcomes_by_operation[obligation.operation_id]
        for error in outcome.source_errors:
            error_payload = {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "code": error["code"],
                "message": error["message"],
            }
            error_id = stable_result_id("source-error", error_payload)
            source_errors.append({"id": error_id, **error_payload})
            source_error_ids_by_operation.setdefault(outcome.operation_id, []).append(
                error_id
            )
        operation_coverage[outcome.operation_id] = outcome.to_coverage_dict(
            observation_ids=observation_ids_by_operation.get(outcome.operation_id, ()),
            source_error_ids=source_error_ids_by_operation.get(
                outcome.operation_id, ()
            ),
        )

    adoptions_by_observation = {
        str(item["id"]): _identity_evidence_adoptions(
            item,
            obligations[str(item["operation_id"])],
            as_of=plan.as_of,
        )
        for item in observation_records
    }
    adoptions_by_observation_and_use = {
        (observation_id, str(adoption["use"])): adoption
        for observation_id, adoptions in adoptions_by_observation.items()
        for adoption in adoptions
    }
    support_records = [
        item
        for item in observation_records
        if adoptions_by_observation_and_use[(str(item["id"]), "security_identity")].get(
            "decision"
        )
        == "adopted"
        and adoptions_by_observation_and_use[
            (str(item["id"]), "security_identity")
        ].get("role")
        == "supporting"
    ]
    corroborating_records = [
        item
        for item in observation_records
        if adoptions_by_observation_and_use[(str(item["id"]), "security_identity")].get(
            "decision"
        )
        == "adopted"
        and adoptions_by_observation_and_use[
            (str(item["id"]), "security_identity")
        ].get("role")
        == "corroborating"
    ]
    corroborating_by_code: dict[str, list[dict[str, object]]] = {}
    for record in corroborating_records:
        identity = _identity_payload(record)
        code = identity.get("code")
        if isinstance(code, str):
            corroborating_by_code.setdefault(code, []).append(record)
    considered_corroborating_by_code: dict[str, list[dict[str, object]]] = {}
    for record in observation_records:
        if (
            obligations[str(record["operation_id"])].evidence_role("security_identity")
            != "corroborating"
        ):
            continue
        code = _identity_payload(record).get("code")
        if isinstance(code, str):
            considered_corroborating_by_code.setdefault(code, []).append(record)

    candidates: list[
        tuple[dict[str, object], tuple[dict[str, object], ...], bool, bool]
    ] = []
    conflict_payloads: list[dict[str, object]] = []
    core_dependency = _research_dependency(plan, "security_identity_claim")
    issuer_dependency = _research_dependency(plan, "issuer_relationship")
    for support_record in support_records:
        identity = _identity_payload(support_record)
        code = identity.get("code")
        exchange = identity.get("exchange")
        if not isinstance(code, str) or not isinstance(exchange, str):
            continue
        corroborating = corroborating_by_code.get(code, [])
        exchange_conflicts = [
            item
            for item in corroborating
            if _identity_payload(item).get("exchange") not in {None, exchange}
        ]
        if exchange_conflicts:
            for conflict_record in exchange_conflicts:
                conflict_payloads.append(
                    {
                        "code": "source_identity_conflict",
                        "message": f"Sources disagree on the exchange for code {code}.",
                        "observation_ids": [
                            support_record["id"],
                            conflict_record["id"],
                        ],
                    }
                )
            continue
        matching = [
            item
            for item in corroborating
            if _same_identity_payloads(identity, _identity_payload(item))
        ]
        name_conflicts = [
            item
            for item in matching
            if _identity_payload(item).get("name") != identity.get("name")
        ]
        if name_conflicts:
            for conflict_record in name_conflicts:
                conflict_payloads.append(
                    {
                        "code": "source_identity_conflict",
                        "message": f"Source names disagree for {exchange}:{code}.",
                        "observation_ids": [
                            support_record["id"],
                            conflict_record["id"],
                        ],
                    }
                )
            continue
        matching = [
            item
            for item in matching
            if _identity_payload(item).get("name") == identity.get("name")
        ]
        if plan.exchange_hint is not None and exchange != plan.exchange_hint:
            conflict_payloads.append(
                {
                    "code": "exchange_hint_conflict",
                    "message": (
                        "The exchange hint conflicts with the attributable identity."
                    ),
                    "observation_ids": [support_record["id"]],
                }
            )
            continue
        eligible_candidate_records = tuple((support_record, *matching))
        core_support_count = sum(
            1
            for item in eligible_candidate_records
            if adoptions_by_observation_and_use[
                (str(item["id"]), core_dependency.evidence_use)
            ].get("decision")
            == "adopted"
            and adoptions_by_observation_and_use[
                (str(item["id"]), core_dependency.evidence_use)
            ].get("role")
            == "supporting"
        )
        if core_support_count < core_dependency.minimum_supporting_observations:
            continue
        issuer_support_count = sum(
            1
            for item in eligible_candidate_records
            if adoptions_by_observation_and_use[
                (str(item["id"]), issuer_dependency.evidence_use)
            ].get("decision")
            == "adopted"
            and adoptions_by_observation_and_use[
                (str(item["id"]), issuer_dependency.evidence_use)
            ].get("role")
            == "supporting"
        )
        relationship_supported = (
            issuer_support_count >= issuer_dependency.minimum_supporting_observations
        )
        candidate = {
            "security": {
                "exchange": exchange,
                "code": code,
                "type": "A_SHARE",
                "validity": {
                    "from": identity.get("valid_from"),
                    "to": None,
                    "status": "current",
                    "observed_at": _retrieved_date(support_record),
                },
            },
            "name": identity.get("name"),
            "issuer": {
                "name": _observation_subject(support_record).get("issuer"),
                "identifier": None,
                "security_relationship": (
                    "verified" if relationship_supported else "unverified"
                ),
            },
            "identity_status": (
                "cross_checked_experimental" if matching else "attributed_experimental"
            ),
        }
        considered_records = tuple(
            item
            for item in considered_corroborating_by_code.get(code, ())
            if _same_identity_payloads(identity, _identity_payload(item))
        )
        candidates.append(
            (
                candidate,
                tuple((support_record, *considered_records)),
                relationship_supported,
                not issuer_dependency.core or relationship_supported,
            )
        )

    reason_payloads: list[dict[str, object]] = []
    claims: list[dict[str, object]] = []
    status: ResearchStatus
    if plan.precondition_reason is not None:
        status = "blocked"
        reason_payloads.append(_status_reason(plan.precondition_reason, ()))
    elif conflict_payloads:
        status = "blocked"
        reason_payloads.append(
            _status_reason(
                "identity_conflict",
                tuple(str(item["id"]) for item in observation_records),
            )
        )
    elif len(candidates) > 1:
        status = "blocked"
        reason_payloads.append(
            _status_reason(
                "ambiguous_security_clue",
                tuple(str(item["id"]) for item in observation_records),
            )
        )
    elif len(candidates) == 1:
        (
            candidate,
            claim_records,
            relationship_supported,
            core_dependencies_satisfied,
        ) = candidates[0]
        claim_observation_ids = tuple(str(item["id"]) for item in claim_records)
        evidence_adoptions = tuple(
            adoption
            for record in claim_records
            for adoption in adoptions_by_observation[str(record["id"])]
        )
        claim_payload: dict[str, object] = {
            "kind": "security_identity",
            "observation_ids": list(claim_observation_ids),
            "evidence_adoptions": list(evidence_adoptions),
            "payload": candidate,
        }
        claims.append({"id": stable_result_id("claim", claim_payload), **claim_payload})
        reason_payloads.append(
            _status_reason("experimental_identity_sources", claim_observation_ids)
        )
        if len(claim_records) == 1:
            reason_payloads.append(
                _status_reason("identity_uncorroborated", claim_observation_ids)
            )
        if not relationship_supported:
            reason_payloads.append(
                _status_reason("issuer_relationship_unverified", claim_observation_ids)
            )
        if source_errors:
            reason_payloads.append(
                _status_reason(
                    "identity_source_partially_failed", claim_observation_ids
                )
            )
        status = (
            "blocked"
            if not core_dependencies_satisfied
            else "limited"
            if reason_payloads
            else "supported"
        )
    else:
        status = "blocked"
        code = "source_operation_failed" if source_errors else "identity_not_resolved"
        reason_payloads.append(
            _status_reason(
                code,
                tuple(str(item["id"]) for item in observation_records),
            )
        )

    conflicts = tuple(
        {"id": stable_result_id("conflict", item), **item} for item in conflict_payloads
    )
    limitations = tuple(
        {"id": stable_result_id("limitation", item), **item} for item in reason_payloads
    )
    observation_ids = [str(item["id"]) for item in observation_records]
    claim_ids = [str(item["id"]) for item in claims]
    conflict_ids = [str(item["id"]) for item in conflicts]
    limitation_ids = [str(item["id"]) for item in limitations]
    source_error_ids = [str(item["id"]) for item in source_errors]
    brief_payload: dict[str, object] = {
        "status": status,
        "summary": (
            "One attributable identity was retained."
            if claims
            else "Security identity could not be established."
        ),
        "observation_ids": observation_ids,
        "claim_ids": claim_ids,
        "calculation_ids": [],
        "conflict_ids": conflict_ids,
        "limitation_ids": limitation_ids,
        "source_error_ids": source_error_ids,
    }
    brief_id = stable_result_id("brief", brief_payload)
    capability_ids = list(
        dict.fromkeys(item.capability_id for item in plan.obligations)
    )
    coverage: dict[str, object] = {}
    capability_registry: dict[str, object] = {}
    for capability_id in capability_ids:
        capability_obligations = tuple(
            item for item in plan.obligations if item.capability_id == capability_id
        )
        capability_outcomes = tuple(
            outcomes_by_operation[item.operation_id] for item in capability_obligations
        )
        coverage[capability_id] = {
            "state": _aggregate_capability_coverage(
                tuple(item.coverage for item in capability_outcomes)
            ),
            "operations": {
                item.operation_id: operation_coverage[item.operation_id]
                for item in capability_obligations
            },
        }
        capability_registry[capability_id] = {
            "id": capability_id,
            "version": plan.version,
            "granularity": "one_security_identity_claim",
            "operations": [
                {
                    "id": item.operation_id,
                    "source_role": item.source_role,
                    "experimental": item.experimental,
                }
                for item in capability_obligations
            ],
        }

    return UnifiedResearchResult(
        status=status,
        research_anchor={"as_of": plan.as_of, "timezone": "Asia/Shanghai"},
        plan={
            "id": plan.plan_id,
            "version": plan.version,
            "task_type": plan.task_type,
            "capabilities": capability_ids,
            "operation_obligations": [item.to_dict() for item in plan.obligations],
            "research_dependencies": [item.to_dict() for item in plan.dependencies],
        },
        capability_registry=capability_registry,
        coverage=coverage,
        observations=tuple(observation_records),
        claims=tuple(claims),
        calculations=(),
        conflicts=conflicts,
        limitations=limitations,
        source_errors=tuple(source_errors),
        task_views={
            plan.task_type: {
                "status": status,
                "observation_ids": observation_ids,
                "claim_ids": claim_ids,
                "calculation_ids": [],
                "conflict_ids": conflict_ids,
                "limitation_ids": limitation_ids,
                "source_error_ids": source_error_ids,
                "brief_id": brief_id,
            }
        },
        briefs=({"id": brief_id, **brief_payload},),
    )


def _research_dependency(
    plan: IdentityResearchPlan, consumer_id: str
) -> ResearchDependency:
    matches = [item for item in plan.dependencies if item.consumer_id == consumer_id]
    if len(matches) != 1:
        raise ValueError("identity plan research dependency is missing or duplicated")
    return matches[0]


def _identity_payload(record: Mapping[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    observation = payload.get("observation") if isinstance(payload, dict) else None
    return observation if isinstance(observation, dict) else {}


def _observation_subject(record: Mapping[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    subject = payload.get("subject") if isinstance(payload, dict) else None
    return subject if isinstance(subject, dict) else {}


def _retrieved_date(record: Mapping[str, object]) -> str:
    payload = record.get("payload")
    retrieved_at = payload.get("retrieved_at") if isinstance(payload, dict) else None
    return retrieved_at[:10] if isinstance(retrieved_at, str) else ""


def _same_identity_payloads(
    supporting: Mapping[str, object], corroborating: Mapping[str, object]
) -> bool:
    return supporting.get("code") == corroborating.get("code") and (
        corroborating.get("exchange") is None
        or corroborating.get("exchange") == supporting.get("exchange")
    )


def _identity_evidence_adoptions(
    record: Mapping[str, object],
    obligation: OperationObligation,
    *,
    as_of: str,
) -> tuple[dict[str, object], ...]:
    observation_id = str(record["id"])
    identity = _identity_payload(record)
    ineligible_reason = _identity_observation_ineligibility(
        record,
        obligation,
        as_of=as_of,
    )
    adoptions: list[dict[str, object]] = []
    evidence_uses: tuple[EvidenceUse, ...] = (
        "security_identity",
        "attribution_lineage",
        "issuer_relationship",
    )
    for use in evidence_uses:
        declared_role = obligation.evidence_role(use)
        if ineligible_reason is not None:
            declared_role = "not_applicable"
        elif declared_role == "conditional_supporting":
            declared_role = (
                "supporting"
                if identity.get("issuer_relationship_verified") is True
                else "not_applicable"
            )
        if declared_role == "not_applicable":
            adoptions.append(
                {
                    "observation_id": observation_id,
                    "use": use,
                    "decision": "not_applicable",
                    "role": None,
                    "reason": (
                        ineligible_reason
                        or "issuer_relationship_not_independently_verified"
                        if use == "issuer_relationship"
                        else ineligible_reason or "operation_not_applicable_to_use"
                    ),
                }
            )
        else:
            adoptions.append(
                {
                    "observation_id": observation_id,
                    "use": use,
                    "decision": "adopted",
                    "role": declared_role,
                    "reason": None,
                }
            )
    return tuple(adoptions)


def _identity_observation_ineligibility(
    record: Mapping[str, object],
    obligation: OperationObligation,
    *,
    as_of: str,
) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return "observation_contract_incomplete"
    subject = payload.get("subject")
    observed_value = payload.get("observed_value")
    identity = payload.get("observation")
    locator = payload.get("locator")
    limitations = payload.get("limitations")
    retrieved_at = payload.get("retrieved_at")
    if (
        payload.get("source_role") != obligation.source_role
        or payload.get("source_operation") != obligation.operation_id
        or payload.get("experimental") is not obligation.experimental
        or not isinstance(subject, dict)
        or not isinstance(observed_value, dict)
        or not isinstance(payload.get("basis"), str)
        or not payload["basis"]
        or not isinstance(identity, dict)
        or not isinstance(locator, dict)
        or not isinstance(limitations, list)
        or any(not isinstance(item, str) for item in limitations)
        or not isinstance(retrieved_at, str)
        or payload.get("evidence_time") != retrieved_at
        or "available_at" not in payload
    ):
        return "observation_contract_incomplete"
    exchange = identity.get("exchange")
    code = identity.get("code")
    name = identity.get("name")
    expected_unit = (
        "canonical_security_identifier"
        if isinstance(exchange, str)
        else "unresolved_security_code_clue"
    )
    expected_value = f"{exchange}:{code}" if isinstance(exchange, str) else code
    if (
        identity.get("kind") != "security_identity"
        or exchange not in {None, "SSE", "SZSE"}
        or not isinstance(code, str)
        or not code
        or not isinstance(name, str)
        or not name
        or identity.get("security_type") != "A_SHARE"
        or not isinstance(identity.get("issuer_relationship_verified"), bool)
        or observed_value.get("value") != expected_value
        or observed_value.get("unit") != expected_unit
        or not isinstance(locator.get("uri"), str)
        or not locator["uri"]
        or not isinstance(locator.get("observation"), str)
        or not locator["observation"]
    ):
        return "observation_contract_incomplete"
    try:
        retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except ValueError:
        return "observation_contract_incomplete"
    if retrieved.tzinfo is None:
        return "observation_contract_incomplete"
    if retrieved.date().isoformat() != as_of:
        return "observation_outside_research_date"
    return None


def _status_reason(code: str, references: Sequence[str]) -> dict[str, object]:
    try:
        message = _STATUS_REASON_MESSAGES[code]
    except KeyError as error:
        raise ValueError("identity status reason is unsupported") from error
    return {"code": code, "message": message, "references": list(references)}


def _aggregate_capability_coverage(
    states: Sequence[CoverageState],
) -> CoverageState:
    """Apply the sole capability coverage truth table."""

    states = tuple(states)
    if not states:
        raise ValueError("capability coverage requires operation outcomes")
    if len(set(states)) == 1:
        return states[0]
    if "failed" in states or "partial" in states:
        if all(state == "failed" for state in states):
            return "failed"
        return "partial"
    if any(
        state in {"not_requested", "skipped_policy", "skipped_research_dependency"}
        for state in states
    ):
        return "partial"
    if "complete" in states:
        return "complete"
    return "observed_empty"


__all__ = [
    "EvidenceUsePolicy",
    "CoverageState",
    "Disposition",
    "FieldDisposition",
    "IdentityResearchPlan",
    "OperationExecution",
    "OperationObligation",
    "OperationOutcome",
    "ResearchDependency",
    "ResearchStatus",
    "UnifiedResearchResult",
    "compile_identity_research_plan",
    "fold_identity_research_result",
    "stable_result_id",
]
