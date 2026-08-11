"""Operation-owned outcomes and the one public research result envelope."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
    localcontext,
)
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
EvidenceUse = str
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
_IDENTITY_EVIDENCE_USES = frozenset(
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
        if not self.use.strip() or self.role not in _EVIDENCE_ROLES:
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
        if not uses or len(uses) != len(set(uses)):
            raise ValueError("operation obligation evidence uses must be unique")

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
        if not self.consumer_id.strip() or not self.evidence_use.strip():
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
        for obligation in self.obligations:
            if {
                item.use for item in obligation.evidence_uses
            } != _IDENTITY_EVIDENCE_USES:
                raise ValueError("identity obligation must decide every evidence use")


@dataclass(frozen=True)
class DirectTaskPlan:
    """Versioned obligations and dependencies for one migrated narrow task."""

    task_type: str
    as_of: str
    obligations: tuple[OperationObligation, ...]
    dependencies: tuple[ResearchDependency, ...]
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.task_type.strip() or not self.as_of.strip():
            raise ValueError("direct task plan identity is invalid")
        owners = {(item.capability_id, item.operation_id) for item in self.obligations}
        if not owners or len(owners) != len(self.obligations):
            raise ValueError(
                "direct task plan obligations must be non-empty and unique"
            )
        dependency_keys = [
            (item.consumer_id, item.evidence_use) for item in self.dependencies
        ]
        if not dependency_keys or len(dependency_keys) != len(set(dependency_keys)):
            raise ValueError(
                "direct task plan dependencies must be non-empty and unique"
            )
        declared_uses = {
            policy.use
            for obligation in self.obligations
            for policy in obligation.evidence_uses
        }
        if any(item.evidence_use not in declared_uses for item in self.dependencies):
            raise ValueError("direct task dependency use must be declared by the plan")


@dataclass(frozen=True)
class DirectTaskClaim:
    kind: str
    evidence_use: str
    observation_keys: tuple[str, ...]
    payload: Mapping[str, object]


@dataclass(frozen=True)
class DirectTaskCalculation:
    kind: str
    evidence_use: str
    observation_keys: tuple[str, ...]
    payload: Mapping[str, object]


@dataclass(frozen=True)
class DirectTaskConflict:
    code: str
    message: str
    observation_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DirectTaskLimitation:
    code: str
    message: str
    reference_keys: tuple[str, ...] = ()


def _validate_inner_source_operation(value: object, owner: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key == "source_operation" and nested != owner:
                raise ValueError("inner source operation contradicts outer ownership")
            _validate_inner_source_operation(nested, owner)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_inner_source_operation(nested, owner)


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
            _validate_inner_source_operation(observation, self.operation_id)
        for error in source_errors:
            if error.get("source_operation") != self.operation_id:
                raise ValueError("operation cannot own a sibling source error")
            _validate_inner_source_operation(error, self.operation_id)

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
        if plan.get("task_type") != "security_identity":
            self._validate_direct_task_ownership(result)
            return
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
                if use not in _IDENTITY_EVIDENCE_USES or role not in _EVIDENCE_ROLES:
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
                or declared_uses != _IDENTITY_EVIDENCE_USES
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
                or dependency.get("evidence_use") not in _IDENTITY_EVIDENCE_USES
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
                    or use not in _IDENTITY_EVIDENCE_USES
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
                uses != _IDENTITY_EVIDENCE_USES
                for uses in adoption_uses_by_observation.values()
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

    def _validate_direct_task_ownership(self, result: Mapping[str, object]) -> None:
        """Re-derive the public closure for a migrated narrow task."""

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
        task_type = plan.get("task_type")
        obligations_value = plan.get("operation_obligations")
        if (
            not isinstance(task_type, str)
            or not task_type
            or not isinstance(obligations_value, list)
            or not obligations_value
        ):
            raise ValueError("direct task plan is invalid")
        obligations: dict[tuple[str, str], dict[str, object]] = {}
        roles: dict[tuple[str, str, str], str] = {}
        for obligation in obligations_value:
            if not isinstance(obligation, dict):
                raise ValueError("operation obligation must be an object")
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
                path="operation obligation",
            )
            capability_id = obligation.get("capability_id")
            operation_id = obligation.get("operation_id")
            policies = obligation.get("evidence_uses")
            if (
                not isinstance(capability_id, str)
                or not capability_id
                or not isinstance(operation_id, str)
                or not operation_id
                or not isinstance(obligation.get("source_role"), str)
                or not isinstance(obligation.get("experimental"), bool)
                or obligation.get("exchange") not in {None, "SSE", "SZSE"}
                or not isinstance(policies, list)
                or not policies
            ):
                raise ValueError("operation obligation contract is invalid")
            owner = (capability_id, operation_id)
            if owner in obligations:
                raise ValueError("operation obligations must be unique")
            obligations[owner] = obligation
            seen_uses: set[str] = set()
            for policy in policies:
                if not isinstance(policy, dict):
                    raise ValueError("evidence-use policy must be an object")
                _require_exact_keys(policy, {"use", "role"}, path="evidence-use policy")
                use = policy.get("use")
                role = policy.get("role")
                if (
                    not isinstance(use, str)
                    or not use
                    or use in seen_uses
                    or role not in _EVIDENCE_ROLES
                ):
                    raise ValueError("evidence-use policy is invalid")
                seen_uses.add(use)
                roles[(*owner, use)] = cast(str, role)

        planned_capabilities = {item[0] for item in obligations}
        capabilities = plan.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or set(capabilities) != planned_capabilities
            or len(capabilities) != len(set(capabilities))
        ):
            raise ValueError("plan capabilities contradict obligations")
        registry = cast(dict[str, object], result["capability_registry"])
        coverage = cast(dict[str, object], result["coverage"])
        if (
            set(registry) != planned_capabilities
            or set(coverage) != planned_capabilities
        ):
            raise ValueError("capability aggregates contradict the plan")

        registered: set[tuple[str, str]] = set()
        for capability_id, registration in registry.items():
            if not isinstance(registration, dict):
                raise ValueError("capability registration must be an object")
            _require_exact_keys(
                registration,
                {"id", "version", "granularity", "operations"},
                path="capability registration",
            )
            operations = registration.get("operations")
            if (
                registration.get("id") != capability_id
                or registration.get("version") != plan.get("version")
                or not isinstance(registration.get("granularity"), str)
                or not isinstance(operations, list)
            ):
                raise ValueError("capability registration is invalid")
            for operation in operations:
                if not isinstance(operation, dict):
                    raise ValueError("operation registration must be an object")
                _require_exact_keys(
                    operation,
                    {"id", "source_role", "experimental"},
                    path="operation registration",
                )
                operation_id = operation.get("id")
                if not isinstance(operation_id, str):
                    raise ValueError("operation registration identity is invalid")
                owner = (capability_id, operation_id)
                obligation = obligations.get(owner)
                if (
                    obligation is None
                    or operation.get("source_role") != obligation.get("source_role")
                    or operation.get("experimental")
                    is not obligation.get("experimental")
                ):
                    raise ValueError(
                        "operation registration contradicts its obligation"
                    )
                registered.add((capability_id, operation_id))
        if registered != set(obligations):
            raise ValueError("registry does not close over planned operations")

        observations = cast(list[dict[str, object]], result["observations"])
        source_errors = cast(list[dict[str, object]], result["source_errors"])
        observation_owners: dict[str, tuple[str, str]] = {}
        for observation in observations:
            _require_exact_keys(
                observation,
                {"id", "capability_id", "operation_id", "payload"},
                path="observation",
            )
            observation_id = observation.get("id")
            observation_capability = observation.get("capability_id")
            observation_operation = observation.get("operation_id")
            if (
                not isinstance(observation_id, str)
                or not isinstance(observation_capability, str)
                or not isinstance(observation_operation, str)
                or observation_id in observation_owners
            ):
                raise ValueError("observation ownership is invalid")
            observation_owner = (observation_capability, observation_operation)
            if observation_owner not in obligations:
                raise ValueError("observation ownership is invalid")
            _validate_inner_source_operation(
                observation["payload"], observation_operation
            )
            payload = {
                "capability_id": observation_owner[0],
                "operation_id": observation_owner[1],
                "payload": observation["payload"],
            }
            if observation_id != stable_result_id("observation", payload):
                raise ValueError("observation ID contradicts its canonical payload")
            observation_owners[observation_id] = observation_owner
        source_error_owners: dict[str, tuple[str, str]] = {}
        for error in source_errors:
            _require_exact_keys(
                error,
                {"id", "capability_id", "operation_id", "code", "message"},
                path="source error",
            )
            error_id = error.get("id")
            error_capability = error.get("capability_id")
            error_operation = error.get("operation_id")
            if (
                not isinstance(error_id, str)
                or not isinstance(error_capability, str)
                or not isinstance(error_operation, str)
                or error_id in source_error_owners
            ):
                raise ValueError("source-error ownership is invalid")
            error_owner = (error_capability, error_operation)
            if error_owner not in obligations:
                raise ValueError("source-error ownership is invalid")
            payload = {
                "capability_id": error_owner[0],
                "operation_id": error_owner[1],
                "code": error.get("code"),
                "message": error.get("message"),
            }
            if error_id != stable_result_id("source-error", payload):
                raise ValueError("source-error ID contradicts its canonical payload")
            source_error_owners[error_id] = error_owner

        covered_observations: set[str] = set()
        covered_errors: set[str] = set()
        covered_operations: set[tuple[str, str]] = set()
        derived_states: list[CoverageState] = []
        disposition_contracts: list[dict[str, object]] = []
        for capability_id, aggregate in coverage.items():
            if not isinstance(aggregate, dict):
                raise ValueError("capability coverage must be an object")
            _require_exact_keys(aggregate, {"state", "operations"}, path="coverage")
            operations = aggregate.get("operations")
            if not isinstance(operations, dict):
                raise ValueError("operation coverage must be an object")
            capability_states: list[CoverageState] = []
            for operation_id, operation in operations.items():
                if not isinstance(operation, dict):
                    raise ValueError("operation coverage must be an object")
                _require_exact_keys(
                    operation,
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
                owner = (capability_id, operation_id)
                observation_ids = operation.get("observation_ids")
                error_ids = operation.get("source_error_ids")
                dispositions = operation.get("field_dispositions")
                if (
                    owner not in obligations
                    or operation.get("capability_id") != capability_id
                    or operation.get("operation_id") != operation_id
                    or not isinstance(observation_ids, list)
                    or not isinstance(error_ids, list)
                    or not isinstance(dispositions, list)
                    or not dispositions
                    or any(
                        observation_owners.get(item) != owner
                        for item in observation_ids
                    )
                    or any(source_error_owners.get(item) != owner for item in error_ids)
                ):
                    raise ValueError(
                        "operation ownership or field disposition is invalid"
                    )
                seen_fields: set[str] = set()
                for disposition in dispositions:
                    if not isinstance(disposition, dict):
                        raise ValueError("field disposition must be an object")
                    expected = {"field", "disposition", "destination"}
                    if disposition.get("disposition") == "excluded":
                        expected.add("reason")
                    _require_exact_keys(disposition, expected, path="field disposition")
                    field_name = disposition.get("field")
                    if not isinstance(field_name, str) or field_name in seen_fields:
                        raise ValueError("field disposition contract is invalid")
                    seen_fields.add(field_name)
                    FieldDisposition(
                        field_name,
                        cast(Disposition, disposition.get("disposition")),
                        cast(str | None, disposition.get("destination")),
                        cast(str | None, disposition.get("reason")),
                    )
                execution = OperationExecution(
                    cast(OperationExecutionState, operation.get("execution_state")),
                    query_complete=cast(bool | None, operation.get("query_complete")),
                )
                state = _derive_operation_coverage(
                    execution,
                    tuple({} for _ in observation_ids),
                    tuple({} for _ in error_ids),
                )
                if operation.get("state") != state:
                    raise ValueError("operation coverage contradicts execution facts")
                covered_observations.update(cast(list[str], observation_ids))
                covered_errors.update(cast(list[str], error_ids))
                covered_operations.add(owner)
                capability_states.append(state)
                derived_states.append(state)
                disposition_contracts.append(
                    {
                        "capability_id": capability_id,
                        "operation_id": operation_id,
                        "fields": dispositions,
                    }
                )
            if aggregate.get("state") != _aggregate_capability_coverage(
                capability_states
            ):
                raise ValueError("capability coverage contradicts operations")
        if (
            covered_operations != set(obligations)
            or covered_observations != set(observation_owners)
            or covered_errors != set(source_error_owners)
        ):
            raise ValueError("operation ownership is incomplete")
        expected_plan_id = stable_result_id(
            "plan",
            {
                "version": plan.get("version"),
                "task_type": task_type,
                "operation_obligations": obligations_value,
                "research_dependencies": plan.get("research_dependencies"),
                "field_dispositions": disposition_contracts,
            },
        )
        if plan.get("id") != expected_plan_id:
            raise ValueError("plan ID contradicts its operation contracts")

        ids_by_field: dict[str, set[str]] = {
            "observation_ids": set(observation_owners),
            "claim_ids": set(),
            "calculation_ids": set(),
            "conflict_ids": set(),
            "limitation_ids": set(),
            "source_error_ids": set(source_error_owners),
        }
        claims = cast(list[dict[str, object]], result["claims"])
        calculations = cast(list[dict[str, object]], result["calculations"])
        conflicts = cast(list[dict[str, object]], result["conflicts"])
        limitations = cast(list[dict[str, object]], result["limitations"])
        consumer_support: dict[tuple[str, str], int] = {}

        def validate_references(value: object) -> None:
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    if key == "evidence_id":
                        if (
                            not isinstance(nested, str)
                            or nested not in observation_owners
                        ):
                            raise ValueError(
                                "payload contains an unknown observation reference"
                            )
                    elif key.endswith("evidence_ids"):
                        if not isinstance(nested, list) or any(
                            not isinstance(item, str) or item not in observation_owners
                            for item in nested
                        ):
                            raise ValueError(
                                "payload contains an unknown observation reference"
                            )
                    elif key in {"conflict_ids", "limitation_ids", "source_error_ids"}:
                        if not isinstance(nested, list) or any(
                            not isinstance(item, str) or item not in ids_by_field[key]
                            for item in nested
                        ):
                            raise ValueError(
                                "payload contains an unknown owned reference"
                            )
                    else:
                        validate_references(nested)
            elif isinstance(value, list):
                for nested in value:
                    validate_references(nested)

        for claim in claims:
            _require_exact_keys(
                claim,
                {
                    "id",
                    "kind",
                    "evidence_use",
                    "observation_ids",
                    "evidence_adoptions",
                    "payload",
                },
                path="claim",
            )
            claim_id = claim.get("id")
            kind = claim.get("kind")
            evidence_use = claim.get("evidence_use")
            observation_ids = claim.get("observation_ids")
            adoptions = claim.get("evidence_adoptions")
            if (
                not isinstance(claim_id, str)
                or not isinstance(kind, str)
                or not isinstance(evidence_use, str)
                or not isinstance(observation_ids, list)
                or any(item not in observation_owners for item in observation_ids)
                or not isinstance(adoptions, list)
            ):
                raise ValueError("claim is invalid")
            if claim_id != stable_result_id(
                "claim", {k: v for k, v in claim.items() if k != "id"}
            ):
                raise ValueError("claim ID contradicts its canonical payload")
            supporting = 0
            adopted_ids: set[str] = set()
            for adoption in adoptions:
                if not isinstance(adoption, dict):
                    raise ValueError("claim adoption must be an object")
                _require_exact_keys(
                    adoption,
                    {"observation_id", "use", "decision", "role", "reason"},
                    path="claim adoption",
                )
                observation_id = adoption.get("observation_id")
                adoption_owner = observation_owners.get(cast(str, observation_id))
                expected_role = (
                    roles.get((*adoption_owner, evidence_use))
                    if adoption_owner
                    else None
                )
                if expected_role == "conditional_supporting":
                    expected_role = "supporting"
                if (
                    observation_id not in observation_ids
                    or observation_id in adopted_ids
                    or adoption.get("use") != evidence_use
                    or adoption.get("decision") != "adopted"
                    or adoption.get("role") != expected_role
                    or adoption.get("reason") is not None
                ):
                    raise ValueError("claim adoption contradicts the plan")
                adopted_ids.add(cast(str, observation_id))
                supporting += adoption.get("role") == "supporting"
            if adopted_ids != set(cast(list[str], observation_ids)):
                raise ValueError("claim adoption is incomplete")
            ids_by_field["claim_ids"].add(claim_id)
            consumer_support[(kind, evidence_use)] = max(
                consumer_support.get((kind, evidence_use), 0), supporting
            )
        if len(ids_by_field["claim_ids"]) != len(claims):
            raise ValueError("claim IDs must be unique")

        for calculation in calculations:
            _require_exact_keys(
                calculation,
                {"id", "kind", "evidence_use", "input_ids", "payload"},
                path="calculation",
            )
            calculation_id = calculation.get("id")
            kind = calculation.get("kind")
            evidence_use = calculation.get("evidence_use")
            input_ids = calculation.get("input_ids")
            if (
                not isinstance(calculation_id, str)
                or not isinstance(kind, str)
                or not isinstance(evidence_use, str)
                or not isinstance(input_ids, list)
                or any(item not in observation_owners for item in input_ids)
                or calculation_id
                != stable_result_id(
                    "calculation", {k: v for k, v in calculation.items() if k != "id"}
                )
            ):
                raise ValueError("calculation is invalid")
            ids_by_field["calculation_ids"].add(calculation_id)
            supporting = sum(
                roles.get((*observation_owners[item], evidence_use))
                in {"supporting", "conditional_supporting"}
                for item in cast(list[str], input_ids)
            )
            consumer_support[(kind, evidence_use)] = max(
                consumer_support.get((kind, evidence_use), 0), supporting
            )
        if len(ids_by_field["calculation_ids"]) != len(calculations):
            raise ValueError("calculation IDs must be unique")

        for records, field, kind, reference_field in (
            (conflicts, "conflict_ids", "conflict", "observation_ids"),
            (limitations, "limitation_ids", "limitation", "references"),
        ):
            expected = {"id", "code", "message", reference_field}
            for record in records:
                _require_exact_keys(record, expected, path=kind)
                record_id = record.get("id")
                references = record.get(reference_field)
                if (
                    not isinstance(record_id, str)
                    or not isinstance(references, list)
                    or any(item not in observation_owners for item in references)
                    or record_id
                    != stable_result_id(
                        kind, {k: v for k, v in record.items() if k != "id"}
                    )
                ):
                    raise ValueError(f"{kind} is invalid")
                ids_by_field[field].add(record_id)
            if len(ids_by_field[field]) != len(records):
                raise ValueError(f"{kind} IDs must be unique")

        for consumer in (*claims, *calculations):
            validate_references(consumer.get("payload"))
        dependencies = plan.get("research_dependencies")
        if not isinstance(dependencies, list) or not dependencies:
            raise ValueError("direct task requires dependencies")
        dependency_keys: set[tuple[str, str]] = set()
        unmet_core = False
        unmet_noncore = False
        declared_uses = {item[2] for item in roles}
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                raise ValueError("dependency must be an object")
            _require_exact_keys(
                dependency,
                {
                    "consumer_id",
                    "evidence_use",
                    "minimum_supporting_observations",
                    "core",
                },
                path="dependency",
            )
            consumer_id = dependency.get("consumer_id")
            evidence_use = dependency.get("evidence_use")
            minimum = dependency.get("minimum_supporting_observations")
            key = (cast(str, consumer_id), cast(str, evidence_use))
            if (
                not isinstance(consumer_id, str)
                or not isinstance(evidence_use, str)
                or evidence_use not in declared_uses
                or key in dependency_keys
                or not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or minimum < 1
                or not isinstance(dependency.get("core"), bool)
            ):
                raise ValueError("dependency is invalid")
            dependency_keys.add(key)
            satisfied = consumer_support.get(key, 0) >= minimum
            if not satisfied and dependency["core"]:
                unmet_core = True
            elif not satisfied:
                unmet_noncore = True
        derived_status: ResearchStatus = (
            "blocked"
            if unmet_core
            else "limited"
            if (
                unmet_noncore
                or source_errors
                or conflicts
                or limitations
                or any(
                    item.get("experimental") is True for item in obligations.values()
                )
                or any(
                    state not in {"complete", "observed_empty"}
                    for state in derived_states
                )
            )
            else "supported"
        )
        if result.get("status") != derived_status:
            raise ValueError("research result contradicts its derived result status")

        briefs = cast(list[dict[str, object]], result["briefs"])
        task_views = cast(dict[str, object], result["task_views"])
        if len(briefs) != 1 or set(task_views) != {task_type}:
            raise ValueError("direct task requires one brief and one task view")
        view = cast(dict[str, object], task_views[task_type])
        brief = briefs[0]
        for container, path in ((view, "task view"), (brief, "brief")):
            expected = {
                "status",
                "observation_ids",
                "claim_ids",
                "calculation_ids",
                "conflict_ids",
                "limitation_ids",
                "source_error_ids",
            }
            expected.add("brief_id" if path == "task view" else "id")
            if path == "brief":
                expected.add("summary")
            _require_exact_keys(container, expected, path=path)
            if container.get("status") != derived_status:
                raise ValueError(f"{path} status contradicts result status")
            for field, known in ids_by_field.items():
                references = container.get(field)
                if (
                    not isinstance(references, list)
                    or set(references) != known
                    or len(references) != len(known)
                ):
                    raise ValueError(f"{path} does not close over owned records")
        brief_id = brief.get("id")
        if view.get("brief_id") != brief_id or brief_id != stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        ):
            raise ValueError("brief reference is invalid")
        _validate_direct_domain_facts(result, task_type)


def _validate_direct_domain_facts(result: Mapping[str, object], task_type: str) -> None:
    if task_type == "market_trend":
        _validate_market_domain_facts(result)
    elif task_type == "etf_market":
        _validate_etf_domain_facts(result)
    elif task_type == "security_valuation":
        _validate_valuation_domain_facts(result)
    elif task_type == "valuation_compare":
        _validate_comparison_domain_facts(result)
    elif task_type == "etf_options":
        _validate_etf_option_domain_facts(result)


def _owned_observations(
    result: Mapping[str, object], *, operation_suffix: str | None = None
) -> list[dict[str, object]]:
    observations = cast(list[dict[str, object]], result["observations"])
    if operation_suffix is None:
        return observations
    return [
        item
        for item in observations
        if str(item["operation_id"]).rsplit("::", 1)[-1] == operation_suffix
    ]


def _record_payload(record: Mapping[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("domain observation payload must be an object")
    return payload


def _nested_mapping(value: object, *path: str) -> dict[str, object]:
    current = value
    for component in path:
        if not isinstance(current, dict) or component not in current:
            raise ValueError("domain fact path is missing")
        current = current[component]
    if not isinstance(current, dict):
        raise ValueError("domain fact must be an object")
    return current


def _decimal_fact(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("domain decimal fact is invalid")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("domain decimal fact is invalid") from error
    if not result.is_finite():
        raise ValueError("domain decimal fact is invalid")
    return result


def _percent_fact(value: Decimal) -> dict[str, str]:
    return {
        "value": format(
            (value * Decimal(100)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            "f",
        ),
        "unit": "percent",
    }


def _metric_fact(value: Decimal, unit: str, places: int = 4) -> dict[str, str]:
    return {
        "status": "calculated",
        "value": format(
            value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN),
            "f",
        ),
        "unit": unit,
    }


def _claim_by_kind(result: Mapping[str, object], kind: str) -> dict[str, object] | None:
    return next(
        (
            item
            for item in cast(list[dict[str, object]], result["claims"])
            if item.get("kind") == kind
        ),
        None,
    )


def _validate_etf_option_domain_facts(result: Mapping[str, object]) -> None:
    observations = _owned_observations(result)

    def reject_private_references(value: object) -> None:
        if isinstance(value, Mapping):
            if "evidence_id" in value or "evidence_ids" in value:
                raise ValueError(
                    "ETF-option domain facts expose provider-local formal references"
                )
            for nested in value.values():
                reject_private_references(nested)
        elif isinstance(value, list):
            for nested in value:
                reject_private_references(nested)

    for observation in observations:
        reject_private_references(_record_payload(observation))

    observations_by_id = {str(item["id"]): item for item in observations}
    payloads_by_kind: dict[str, list[dict[str, object]]] = {}
    expected_capabilities = {
        "etf_option_identity": "etf_option_identity",
        "etf_option_reference": "etf_option_reference",
        "etf_option_contract_months": "etf_option_contracts",
        "etf_option_contract_listing": "etf_option_contracts",
        "etf_option_quote": "etf_option_contracts",
        "etf_option_analytics": "etf_option_contracts",
    }
    option_operation: str | None = None
    for observation in observations:
        observation_payload = _record_payload(observation)
        kind = observation_payload.get("kind")
        if not isinstance(kind, str) or kind not in expected_capabilities:
            raise ValueError("ETF-option domain facts contain an unknown observation")
        if observation.get("capability_id") != expected_capabilities[kind]:
            raise ValueError("ETF-option domain facts contradict observation ownership")
        payloads_by_kind.setdefault(kind, []).append(observation_payload)
        if kind not in {"etf_option_identity", "etf_option_reference"}:
            current_operation = observation.get("operation_id")
            if not isinstance(current_operation, str) or (
                option_operation is not None and current_operation != option_operation
            ):
                raise ValueError(
                    "ETF-option domain facts contradict option observation ownership"
                )
            option_operation = current_operation

    def iso_date(value: object, *, month: bool = False) -> str:
        if not isinstance(value, str):
            raise ValueError("ETF-option domain facts contain an invalid date")
        candidate = f"{value}-01" if month else value
        try:
            parsed = datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(
                "ETF-option domain facts contain an invalid date"
            ) from error
        expected = parsed.strftime("%Y-%m" if month else "%Y-%m-%d")
        if value != expected:
            raise ValueError("ETF-option domain facts contain an invalid date")
        return value

    def typed_security(value: object, expected_type: str) -> dict[str, object]:
        security = _nested_mapping({"security": value}, "security")
        if (
            security.get("exchange") != "SSE"
            or security.get("type") != expected_type
            or not isinstance(security.get("code"), str)
            or not security["code"]
        ):
            raise ValueError("ETF-option domain facts contain an invalid security")
        return security

    def iso_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("ETF-option domain facts contain an invalid timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                "ETF-option domain facts contain an invalid timestamp"
            ) from error
        if parsed.tzinfo is None:
            raise ValueError("ETF-option domain facts contain an invalid timestamp")
        return parsed

    def typed_contract(value: object) -> dict[str, object]:
        contract = _nested_mapping({"contract": value}, "contract")
        typed_security(contract.get("security"), "ETF_OPTION")
        strike = _nested_mapping(contract, "strike")
        if strike.get("unit") != "CNY/share":
            raise ValueError("ETF-option domain facts contain an invalid strike")
        _decimal_fact(strike.get("value"))
        month = iso_date(contract.get("contract_month"), month=True)
        expiry = iso_date(contract.get("expiry_date"))
        if (
            expiry[:7] != month
            or contract.get("option_type") not in {"call", "put"}
            or contract.get("series") not in {"M", "A"}
        ):
            raise ValueError("ETF-option domain facts contain an invalid contract")
        return contract

    identities = payloads_by_kind.get("etf_option_identity", [])
    references = payloads_by_kind.get("etf_option_reference", [])
    if len(identities) > 1 or len(references) > 1:
        raise ValueError("ETF-option domain facts duplicate ETF session identity")
    identity_security: dict[str, object] | None = None
    if identities:
        identity_subject = _nested_mapping(identities[0], "subject")
        identity_security = typed_security(identity_subject.get("security"), "ETF")
    reference_trading_date: str | None = None
    if references:
        reference_subject = _nested_mapping(references[0], "subject")
        reference_security = typed_security(reference_subject.get("security"), "ETF")
        reference_price = _nested_mapping(references[0], "reference_price")
        if (
            identity_security is not None
            and reference_security != identity_security
            or reference_price.get("unit") != "CNY/share"
            or references[0].get("market_state")
            not in {"intraday", "completed", "unknown"}
        ):
            raise ValueError("ETF-option domain facts contradict the ETF reference")
        _decimal_fact(reference_price.get("value"))
        reference_trading_date = iso_date(references[0].get("trading_date"))

    retained_observed_months: set[str] = set()
    for month_payload in payloads_by_kind.get("etf_option_contract_months", []):
        if identity_security is not None and month_payload.get(
            "subject_code"
        ) != identity_security.get("code"):
            raise ValueError("ETF-option domain facts contradict the ETF identity")
        months = month_payload.get("observed_months")
        if not isinstance(months, list) or not months:
            raise ValueError("ETF-option domain facts contain invalid contract months")
        retained_observed_months.update(iso_date(item, month=True) for item in months)

    listings: list[dict[str, object]] = []
    for listing in payloads_by_kind.get("etf_option_contract_listing", []):
        if identity_security is not None and listing.get(
            "subject_code"
        ) != identity_security.get("code"):
            raise ValueError("ETF-option domain facts contradict the ETF identity")
        month = iso_date(listing.get("contract_month"), month=True)
        codes = listing.get("contract_codes")
        if (
            month not in retained_observed_months
            or listing.get("option_type") not in {"call", "put"}
            or not isinstance(codes, list)
            or any(not isinstance(code, str) or not code for code in codes)
        ):
            raise ValueError("ETF-option domain facts contain an invalid listing")
        listings.append(listing)

    quote_contracts: list[dict[str, object]] = []
    for quote in payloads_by_kind.get("etf_option_quote", []):
        contract = typed_contract(quote.get("contract"))
        security = _nested_mapping(contract, "security")
        code = security.get("code")
        quote_fact = _nested_mapping(quote, "quote")
        _require_exact_keys(
            quote_fact,
            {
                "state",
                "observed_at",
                "last",
                "bid",
                "ask",
                "bid_size",
                "ask_size",
                "volume",
                "open_interest",
            },
            path="ETF-option domain facts quote",
        )

        def typed_optional_measure(value: object, unit: str) -> None:
            if value is None:
                return
            if not isinstance(value, dict):
                raise ValueError("ETF-option domain facts contain an invalid quote")
            _require_exact_keys(
                value,
                {"value", "unit"},
                path="ETF-option domain facts quote measure",
            )
            if value.get("unit") != unit:
                raise ValueError("ETF-option domain facts contain an invalid quote")
            _decimal_fact(value.get("value"))

        for name in ("last", "bid", "ask"):
            typed_optional_measure(quote_fact.get(name), "CNY/share")
        for name in ("bid_size", "ask_size", "volume", "open_interest"):
            typed_optional_measure(quote_fact.get(name), "contract")
        quote_time = iso_datetime(quote_fact.get("observed_at"))
        quote_state = quote_fact.get("state")
        availability_invalid = (
            quote_state == "quoted"
            and (quote_fact.get("bid") is None or quote_fact.get("ask") is None)
        ) or (
            quote_state == "no_quote"
            and quote_fact.get("bid") is not None
            and quote_fact.get("ask") is not None
        )
        if (
            contract.get("contract_month") not in retained_observed_months
            or not any(
                code in cast(list[str], listing["contract_codes"])
                and listing.get("contract_month") == contract.get("contract_month")
                and listing.get("option_type") == contract.get("option_type")
                for listing in listings
            )
            or quote_state not in {"quoted", "no_quote"}
            or availability_invalid
            or reference_trading_date is None
            or quote_time.date().isoformat() != reference_trading_date
        ):
            raise ValueError("ETF-option domain facts contradict acquired quote facts")
        quote_contracts.append(contract)

    for analytics in payloads_by_kind.get("etf_option_analytics", []):
        contract = typed_contract(analytics.get("contract"))
        if contract not in quote_contracts:
            raise ValueError(
                "ETF-option domain facts contradict an owned quote for analytics"
            )
        analytics_facts = analytics.get("analytics")
        analytic_units = {
            "delta": "dimensionless",
            "gamma": "provider_native_unverified",
            "theta": "provider_native_unverified",
            "vega": "provider_native_unverified",
            "implied_volatility": "decimal_fraction",
            "theoretical_value": "CNY/share",
        }
        if (
            not isinstance(analytics_facts, dict)
            or not analytics_facts
            or not set(analytics_facts).issubset(analytic_units)
        ):
            raise ValueError("ETF-option domain facts contain invalid analytics")
        for name, fact in analytics_facts.items():
            if not isinstance(fact, dict):
                raise ValueError("ETF-option domain facts contain invalid analytics")
            _require_exact_keys(
                fact,
                {"status", "value", "unit", "origin"},
                path="ETF-option analytic",
            )
            if (
                fact.get("status") != "observed"
                or fact.get("origin") != "provider_reported"
                or fact.get("unit") != analytic_units[name]
            ):
                raise ValueError("ETF-option domain facts contain invalid analytics")
            _decimal_fact(fact.get("value"))

    selection = _claim_by_kind(result, "etf_option_selection")
    if selection is None:
        return
    payload = _nested_mapping(selection, "payload")
    request = _nested_mapping(payload, "request")
    session = _nested_mapping(payload, "session")
    contract_set = _nested_mapping(payload, "contract_set")
    atm = _nested_mapping(payload, "atm")
    contracts = payload.get("contracts")
    t_quote = payload.get("t_quote")
    if (
        request.get("view") not in {"atm", "chain"}
        or request.get("expiry_mode") not in {"nearest_unexpired", "exact"}
        or request.get("quote_mode") not in {"latest", "latest_completed"}
        or not isinstance(contracts, list)
        or not contracts
        or not isinstance(t_quote, dict)
    ):
        raise ValueError("ETF-option domain facts are incomplete")

    identity_claim = _claim_by_kind(result, "etf_option_identity")
    if identity_claim is None:
        raise ValueError("ETF-option domain facts lack the ETF identity claim")
    identity_payload = _nested_mapping(identity_claim, "payload")
    identity_subject = _nested_mapping(identity_payload, "subject")
    identity_security = _nested_mapping(identity_subject, "security")
    identity_ids = identity_payload.get("identity_evidence_ids")
    if not isinstance(identity_ids, list) or len(identity_ids) != 1:
        raise ValueError("ETF-option domain facts lack one ETF identity observation")
    identity_record = observations_by_id.get(str(identity_ids[0]))
    if identity_record is None:
        raise ValueError("ETF-option domain facts reference an unknown ETF identity")
    acquired_identity = _record_payload(identity_record)
    if (
        acquired_identity.get("kind") != "etf_option_identity"
        or acquired_identity.get("subject") != identity_subject
        or request.get("subject_clue") != identity_security.get("code")
    ):
        raise ValueError("ETF-option domain facts contradict the acquired ETF identity")
    reference_ids = session.get("reference_evidence_ids")
    if not isinstance(reference_ids, list) or len(reference_ids) != 1:
        raise ValueError("ETF-option domain facts lack one reference observation")
    reference_record = observations_by_id.get(str(reference_ids[0]))
    if reference_record is None:
        raise ValueError("ETF-option domain facts reference an unknown ETF quote")
    reference_payload = _record_payload(reference_record)
    reference_price = _nested_mapping(reference_payload, "reference_price")
    reference_subject = _nested_mapping(reference_payload, "subject", "security")
    if (
        reference_payload.get("kind") != "etf_option_reference"
        or request.get("subject_clue") != reference_subject.get("code")
        or session.get("trading_date") != reference_payload.get("trading_date")
        or session.get("market_state") != reference_payload.get("market_state")
        or session.get("reference_price") != reference_price
        or request.get("quote_mode") != session.get("quote_mode")
        or request.get("observed_on") != session.get("trading_date")
        or (
            request.get("quote_mode") == "latest_completed"
            and session.get("market_state") != "completed"
        )
    ):
        raise ValueError("ETF-option domain facts contradict the reference session")

    selected_quotes: list[dict[str, object]] = []
    selected_analytics: dict[str, dict[str, object]] = {}
    for row in contracts:
        if not isinstance(row, dict):
            raise ValueError("ETF-option domain facts contain an invalid contract")
        quote_ids = row.get("quote_evidence_ids")
        analytics_ids = row.get("analytics_evidence_ids")
        if (
            not isinstance(quote_ids, list)
            or len(quote_ids) != 1
            or not isinstance(analytics_ids, list)
        ):
            raise ValueError("ETF-option domain facts lack contract references")
        quote_record = observations_by_id.get(str(quote_ids[0]))
        if quote_record is None:
            raise ValueError("ETF-option domain facts reference an unknown quote")
        quote_payload = _record_payload(quote_record)
        if (
            quote_payload.get("kind") != "etf_option_quote"
            or row.get("contract") != quote_payload.get("contract")
            or row.get("quote") != quote_payload.get("quote")
        ):
            raise ValueError("ETF-option domain facts contradict an owned quote")
        selected_quotes.append(quote_payload)
        contract = _nested_mapping(quote_payload, "contract")
        security = _nested_mapping(contract, "security")
        code = security.get("code")
        if not isinstance(code, str) or not code:
            raise ValueError("ETF-option domain facts contain an invalid contract code")
        if len(analytics_ids) > 1:
            raise ValueError("ETF-option domain facts duplicate contract analytics")
        if analytics_ids:
            analytic_record = observations_by_id.get(str(analytics_ids[0]))
            if analytic_record is None:
                raise ValueError(
                    "ETF-option domain facts reference unknown contract analytics"
                )
            analytic_payload = _record_payload(analytic_record)
            if (
                analytic_payload.get("kind") != "etf_option_analytics"
                or analytic_payload.get("contract") != contract
                or row.get("analytics") != analytic_payload.get("analytics")
            ):
                raise ValueError(
                    "ETF-option domain facts contradict owned contract analytics"
                )
            selected_analytics[code] = analytic_payload
        elif row.get("analytics") != {}:
            raise ValueError("ETF-option domain facts forge unavailable analytics")

    contract_facts = [_nested_mapping(item, "contract") for item in selected_quotes]
    quote_claim = _claim_by_kind(result, "etf_option_quote")
    if (
        quote_claim is None
        or _nested_mapping(quote_claim, "payload").get("contracts") != contracts
    ):
        raise ValueError("ETF-option domain facts contradict the quote claim")
    expected_analytics_contracts = [
        row
        for row in contracts
        if isinstance(row, dict) and row.get("analytics_evidence_ids")
    ]
    analytics_claim = _claim_by_kind(result, "etf_option_analytics")
    if expected_analytics_contracts:
        if (
            analytics_claim is None
            or _nested_mapping(analytics_claim, "payload").get("contracts")
            != expected_analytics_contracts
        ):
            raise ValueError(
                "ETF-option domain facts discard an acquired analytics sibling"
            )
    elif analytics_claim is not None:
        raise ValueError("ETF-option domain facts forge an analytics claim")
    expiry_dates = {item.get("expiry_date") for item in contract_facts}
    months = sorted({str(item.get("contract_month")) for item in contract_facts})
    series = sorted({str(item.get("series")) for item in contract_facts})
    if (
        len(expiry_dates) != 1
        or contract_set.get("expiry_date") != next(iter(expiry_dates))
        or contract_set.get("contract_months") != months
        or contract_set.get("series") != series
        or contract_set.get("contract_count") != len(contract_facts)
    ):
        raise ValueError("ETF-option domain facts contradict the selected contracts")
    if request.get("expiry_mode") == "exact" and request.get(
        "expiry_date"
    ) != contract_set.get("expiry_date"):
        raise ValueError("ETF-option domain facts contradict exact-expiry selection")

    listing_ids = contract_set.get("listing_evidence_ids")
    if not isinstance(listing_ids, list) or not listing_ids:
        raise ValueError("ETF-option domain facts lack listing lineage")
    listed_codes: set[str] = set()
    observed_months: set[str] = set()
    for item in listing_ids:
        record = observations_by_id.get(str(item))
        if record is None:
            raise ValueError("ETF-option domain facts reference unknown listing facts")
        listing_payload = _record_payload(record)
        kind = listing_payload.get("kind")
        if kind == "etf_option_contract_listing":
            codes = listing_payload.get("contract_codes")
            if not isinstance(codes, list) or any(
                not isinstance(code, str) for code in codes
            ):
                raise ValueError(
                    "ETF-option domain facts contain invalid listing codes"
                )
            listed_codes.update(cast(list[str], codes))
        elif kind == "etf_option_contract_months":
            values = listing_payload.get("observed_months")
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError(
                    "ETF-option domain facts contain invalid contract months"
                )
            observed_months.update(cast(list[str], values))
        else:
            raise ValueError("ETF-option domain facts misuse listing lineage")
    selected_codes = {
        str(_nested_mapping(item, "security").get("code")) for item in contract_facts
    }
    if not selected_codes.issubset(listed_codes) or not set(months).issubset(
        observed_months
    ):
        raise ValueError("ETF-option domain facts contradict acquired listings")

    grouped: dict[Decimal, set[str]] = {}
    strike_text: dict[Decimal, str] = {}
    for item in contract_facts:
        if item.get("series") != "M":
            continue
        strike = _nested_mapping(item, "strike")
        value = _decimal_fact(strike.get("value"))
        option_type = item.get("option_type")
        if option_type not in {"call", "put"}:
            raise ValueError("ETF-option domain facts contain an invalid option side")
        grouped.setdefault(value, set()).add(cast(str, option_type))
        if option_type == "call":
            strike_text[value] = str(strike.get("value"))
    complete_strikes = [
        value for value, sides in grouped.items() if sides == {"call", "put"}
    ]
    if not complete_strikes:
        raise ValueError("ETF-option domain facts lack a complete ATM pair")
    reference_value = _decimal_fact(reference_price.get("value"))
    minimum_distance = min(abs(item - reference_value) for item in complete_strikes)
    expected_candidates = [
        strike_text[item]
        for item in sorted(complete_strikes)
        if abs(item - reference_value) == minimum_distance
    ]
    if (
        atm.get("method_id") != "nearest_strike_to_underlying_reference@1"
        or atm.get("strike_candidates") != expected_candidates
        or atm.get("status")
        != ("tie" if len(expected_candidates) > 1 else "identified")
        or atm.get("basis_evidence_ids") != reference_ids
    ):
        raise ValueError("ETF-option domain facts forge ATM selection")

    calculation = next(
        (
            item
            for item in cast(list[dict[str, object]], result["calculations"])
            if item.get("kind") == "etf_option_atm"
        ),
        None,
    )
    if calculation is None:
        raise ValueError("ETF-option domain facts lack the ATM calculation")
    calculation_payload = _nested_mapping(calculation, "payload")
    if (
        calculation_payload.get("method_id")
        != "nearest_strike_to_underlying_reference@1"
        or _decimal_fact(calculation_payload.get("reference_price")) != reference_value
        or calculation_payload.get("candidate_strikes") != expected_candidates
    ):
        raise ValueError("ETF-option domain facts contradict the ATM calculation")

    if t_quote.get("grouping") != [
        "contract_month",
        "expiry_date",
        "series",
        "strike",
    ] or not isinstance(t_quote.get("rows"), list):
        raise ValueError("ETF-option domain facts contain an invalid T quote")
    grouped_rows: dict[tuple[str, str, str, Decimal], dict[str, dict[str, object]]] = {}
    for row in contracts:
        assert isinstance(row, dict)
        contract = _nested_mapping(row, "contract")
        strike = _nested_mapping(contract, "strike")
        option_type = contract.get("option_type")
        if option_type not in {"call", "put"}:
            raise ValueError("ETF-option domain facts contain an invalid T quote side")
        key = (
            str(contract.get("contract_month")),
            str(contract.get("expiry_date")),
            str(contract.get("series")),
            _decimal_fact(strike.get("value")),
        )
        grouped_rows.setdefault(key, {})[cast(str, option_type)] = row
    expected_t_quote: list[dict[str, object]] = []
    for key, sides in sorted(grouped_rows.items()):
        if set(sides) != {"call", "put"}:
            raise ValueError("ETF-option domain facts contain an incomplete T quote")
        call, put = sides["call"], sides["put"]
        call_contract = _nested_mapping(call, "contract")
        expected_t_quote.append(
            {
                "contract_month": key[0],
                "expiry_date": key[1],
                "series": key[2],
                "strike": call_contract["strike"],
                "call": {
                    "contract": call["contract"],
                    "quote": call["quote"],
                    "quote_evidence_ids": call["quote_evidence_ids"],
                    "analytics_evidence_ids": call["analytics_evidence_ids"],
                },
                "put": {
                    "contract": put["contract"],
                    "quote": put["quote"],
                    "quote_evidence_ids": put["quote_evidence_ids"],
                    "analytics_evidence_ids": put["analytics_evidence_ids"],
                },
            }
        )
    if t_quote.get("rows") != expected_t_quote:
        raise ValueError("ETF-option domain facts contradict the selected T quote")


def _validate_market_domain_facts(result: Mapping[str, object]) -> None:
    claim = _claim_by_kind(result, "market_trend_summary")
    if claim is None:
        return
    payload = _nested_mapping(claim, "payload")
    series = payload.get("series")
    metrics = payload.get("metrics")
    if not isinstance(series, list) or len(series) < 2 or not isinstance(metrics, dict):
        raise ValueError("market domain facts are incomplete")
    observations_by_id = {str(item["id"]): item for item in _owned_observations(result)}
    closes: list[Decimal] = []
    volumes: list[Decimal] = []
    dates: list[str] = []
    for row in series:
        if not isinstance(row, dict):
            raise ValueError("market domain facts contain an invalid series row")
        evidence_ids = row.get("evidence_ids")
        if not isinstance(evidence_ids, list) or len(evidence_ids) != 2:
            raise ValueError("market domain facts lack cross-checked row evidence")
        evidence = [observations_by_id.get(str(item)) for item in evidence_ids]
        if any(item is None for item in evidence):
            raise ValueError("market domain facts reference unknown row evidence")
        for record in evidence:
            assert record is not None
            observation_payload = _record_payload(record)
            values = _nested_mapping(observation_payload, "observed_value", "value")
            observation = _nested_mapping(observation_payload, "observation")
            if (
                observation.get("trading_date") != row.get("trading_date")
                or observation.get("adjustment") != row.get("adjustment")
                or any(
                    _decimal_fact(values.get(field)) != _decimal_fact(row.get(field))
                    for field in ("open", "high", "low", "close", "volume")
                )
            ):
                raise ValueError("market domain facts contradict owned observations")
        dates.append(str(row["trading_date"]))
        closes.append(_decimal_fact(row["close"]))
        volumes.append(_decimal_fact(row["volume"]))
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("market domain facts contain invalid selected sessions")
    returns = [
        closes[index] / closes[index - 1] - Decimal(1)
        for index in range(1, len(closes))
    ]
    peak = closes[0]
    drawdown = Decimal(0)
    for close in closes:
        peak = max(peak, close)
        drawdown = min(drawdown, close / peak - Decimal(1))
    annualized_volatility: dict[str, object]
    if len(returns) >= 2:
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum(((item - mean) ** 2 for item in returns), Decimal(0)) / Decimal(
            len(returns) - 1
        )
        annualized_volatility = {
            **_percent_fact(variance.sqrt() * Decimal(252).sqrt()),
            "basis": "sample_stddev_of_daily_simple_returns_sqrt_252",
        }
    else:
        annualized_volatility = {
            "status": "not_computable",
            "reason": "sample volatility requires at least two daily returns",
        }
    comparison_sessions = len(volumes) // 2
    first_volume = sum(volumes[:comparison_sessions], Decimal(0)) / Decimal(
        comparison_sessions
    )
    last_volume = sum(volumes[-comparison_sessions:], Decimal(0)) / Decimal(
        comparison_sessions
    )
    expected_metrics: dict[str, object] = {
        "cumulative_return": _percent_fact(closes[-1] / closes[0] - Decimal(1)),
        "maximum_drawdown": _percent_fact(drawdown),
        "annualized_volatility": annualized_volatility,
        "up_sessions": sum(item > 0 for item in returns),
        "down_sessions": sum(item < 0 for item in returns),
        "unchanged_sessions": sum(item == 0 for item in returns),
        "volume_change": {
            **_percent_fact(last_volume / first_volume - Decimal(1)),
            "basis": (
                f"last_{comparison_sessions}_session_average_vs_"
                f"first_{comparison_sessions}_session_average"
            ),
        },
    }
    if metrics != expected_metrics:
        raise ValueError("market domain facts contain forged metrics")
    expected_formulas = {
        "cumulative_return": "(last_close / first_close - 1) * 100",
        "maximum_drawdown": "min(close / running_peak_close - 1) * 100",
        "annualized_volatility": (
            "sample_stddev(daily_simple_returns) * sqrt(252) * 100"
        ),
        "volume_change": (
            "(last_half_average_volume / first_half_average_volume - 1) * 100"
        ),
    }
    calculations = cast(list[dict[str, object]], result["calculations"])
    if len(calculations) != 4:
        raise ValueError("market domain facts lack sealed calculations")
    for calculation in calculations:
        kind = str(calculation.get("kind", "")).removeprefix(
            "market_trend_calculation:"
        )
        calculation_payload = _nested_mapping(calculation, "payload")
        if calculation_payload.get("id") != kind or calculation_payload.get(
            "formula"
        ) != expected_formulas.get(kind):
            raise ValueError("market domain facts contain a forged calculation")
        inputs = [
            observations_by_id.get(str(item))
            for item in cast(list[object], calculation.get("input_ids", []))
        ]
        if not inputs or any(item is None for item in inputs):
            raise ValueError("market domain facts lack owned calculation inputs")
        input_values = [
            _nested_mapping(
                _record_payload(cast(dict[str, object], item)),
                "observed_value",
                "value",
            )
            for item in inputs
        ]
        operands = calculation_payload.get("operands")
        if not isinstance(operands, dict):
            raise ValueError("market domain facts lack calculation operands")
        if kind == "cumulative_return":
            expected_operands: dict[str, object] = {
                "first_close": input_values[0]["close"],
                "last_close": input_values[-1]["close"],
            }
        elif kind in {"maximum_drawdown", "annualized_volatility"}:
            expected_operands = {"closes": [item["close"] for item in input_values]}
        else:
            expected_operands = {
                "volumes_shares": [item["volume"] for item in input_values]
            }
        if operands != expected_operands:
            raise ValueError("market domain facts contradict calculation inputs")
    corporate_actions = payload.get("corporate_actions")
    if not isinstance(corporate_actions, list):
        raise ValueError("market domain facts contain invalid corporate actions")
    for action in corporate_actions:
        if not isinstance(action, dict):
            raise ValueError("market domain facts contain invalid corporate actions")
        record = observations_by_id.get(str(action.get("evidence_id")))
        if record is None or _nested_mapping(
            _record_payload(record), "observation"
        ).get("corporate_action") != action.get("details"):
            raise ValueError("market domain facts forge a corporate action")


def _validate_etf_domain_facts(result: Mapping[str, object]) -> None:
    claim = _claim_by_kind(result, "etf_market_quote")
    if claim is None:
        return
    payload = _nested_mapping(claim, "payload")
    quote = payload.get("quote")
    subjects = payload.get("subjects")
    if not isinstance(quote, dict) or not isinstance(subjects, list) or not subjects:
        raise ValueError("ETF domain facts are incomplete")
    snapshot_records = _owned_observations(
        result, operation_suffix="sse_etf_snapshot@1"
    )
    tencent_records = _owned_observations(
        result, operation_suffix="tencent_daily_line@1"
    )
    identity_records = _owned_observations(result, operation_suffix="sse_etf_list@1")
    if len(snapshot_records) != 1 or not tencent_records or len(identity_records) != 1:
        raise ValueError("ETF domain facts lack owned observations")
    snapshot_payload = _record_payload(snapshot_records[0])
    snapshot_values = _nested_mapping(snapshot_payload, "observed_value", "value")
    snapshot_observation = _nested_mapping(snapshot_payload, "observation")
    tencent = next(
        (
            item
            for item in tencent_records
            if _nested_mapping(_record_payload(item), "observation").get("trading_date")
            == snapshot_observation.get("trading_date")
        ),
        None,
    )
    if tencent is None:
        raise ValueError("ETF domain facts lack same-session cross-check evidence")
    tencent_values = _nested_mapping(
        _record_payload(tencent), "observed_value", "value"
    )
    scalar_fields = {
        "open": "open",
        "high": "high",
        "low": "low",
        "last": "last",
        "previous_close": "previous_close",
    }
    if any(
        _decimal_fact(quote.get(public)) != _decimal_fact(snapshot_values.get(source))
        for public, source in scalar_fields.items()
    ) or quote.get("trading_date") != snapshot_observation.get("trading_date"):
        raise ValueError("ETF domain facts contradict the owned snapshot")
    for public, source, unit in (
        ("change_rate", "change_rate", "percent"),
        ("volume", "volume", "shares"),
        ("amount", "amount", "CNY"),
    ):
        public_value = quote.get(public)
        if (
            not isinstance(public_value, dict)
            or public_value.get("unit") != unit
            or _decimal_fact(public_value.get("value"))
            != _decimal_fact(snapshot_values.get(source))
        ):
            raise ValueError("ETF domain facts contradict the owned snapshot")
    identity_values = _nested_mapping(
        _record_payload(identity_records[0]), "observed_value", "value"
    )
    subject = subjects[0]
    if not isinstance(subject, dict) or any(
        subject.get(field) != identity_values.get(source)
        for field, source in (
            ("name", "name"),
            ("fund_manager", "fund_manager"),
            ("listing_date", "listing_date"),
        )
    ):
        raise ValueError("ETF domain facts contradict the owned identity")
    difference = abs(
        _decimal_fact(snapshot_values["volume"])
        - _decimal_fact(tencent_values["volume"])
    )
    cross_check = {
        "status": "consistent_with_lot_rounding" if difference <= 100 else "conflict",
        "difference_shares": format(difference, "f"),
    }
    if quote.get("volume_cross_check") != cross_check:
        raise ValueError("ETF domain facts forge the volume cross-check")
    calculations = cast(list[dict[str, object]], result["calculations"])
    if len(calculations) != 1 or calculations[0].get("payload") != cross_check:
        raise ValueError("ETF domain facts forge the cross-check calculation")


def _valuation_owned_facts(
    observations: Sequence[dict[str, object]],
    calculations: Sequence[dict[str, object]],
) -> tuple[str, str, str, dict[str, dict[str, object]], dict[str, object]]:
    def operation_records(suffix: str) -> list[dict[str, object]]:
        return [
            item
            for item in observations
            if str(item["operation_id"]).rsplit("::", 1)[-1] == suffix
        ]

    stock_records = operation_records("eastmoney_stock_info@1")
    official_close_records = [
        *operation_records("sse_daily_line@1"),
        *operation_records("szse_daily_line@1"),
    ]
    if len(stock_records) != 1 or len(official_close_records) != 1:
        raise ValueError("valuation domain facts lack price or share observations")
    stock_payload = _record_payload(stock_records[0])
    stock_values = _nested_mapping(stock_payload, "observed_value", "value")
    close_payload = _record_payload(official_close_records[0])
    close_observed = _nested_mapping(close_payload, "observed_value")
    close_observation = _nested_mapping(close_payload, "observation")
    subject = _nested_mapping(stock_payload, "subject")
    security = str(subject.get("security"))
    name = str(stock_values.get("name"))
    trading_date = str(close_observation.get("trading_date"))
    price = _decimal_fact(close_observed.get("value"))
    shares = _decimal_fact(stock_values.get("effective_total_shares"))
    market_cap = price * shares
    expected_metrics: dict[str, dict[str, object]] = {
        "market_capitalization": cast(
            dict[str, object], _metric_fact(market_cap, "CNY", 3)
        )
    }
    anchors: dict[str, object] = {
        "price": format(price, "f"),
        "shares": format(shares, "f"),
        "market_cap": format(market_cap, "f"),
    }
    statements = operation_records("sina_financial_statements@1")
    income_by_period: dict[str, Decimal] = {}
    equity_by_period: dict[str, Decimal] = {}
    for record in statements:
        statement_payload = _record_payload(record)
        report = _nested_mapping(statement_payload, "report")
        values = _nested_mapping(statement_payload, "observed_value", "value")
        period = report.get("period")
        if not isinstance(period, str):
            continue
        if "归属于母公司所有者的净利润" in values:
            income_by_period[period] = _decimal_fact(
                values["归属于母公司所有者的净利润"]
            )
        if "归属于母公司股东权益合计" in values:
            equity_by_period[period] = _decimal_fact(values["归属于母公司股东权益合计"])
    if len(income_by_period) >= 3:
        latest = max(income_by_period)
        previous_annual = max(
            period
            for period in income_by_period
            if period < latest and period.endswith("12-31")
        )
        previous_same_period = f"{int(latest[:4]) - 1}{latest[4:]}"
        if previous_same_period in income_by_period:
            ttm_profit = (
                income_by_period[previous_annual]
                + income_by_period[latest]
                - income_by_period[previous_same_period]
            )
            anchors["ttm_profit"] = format(ttm_profit, "f")
            if ttm_profit > 0:
                expected_metrics["pe_ttm"] = cast(
                    dict[str, object], _metric_fact(market_cap / ttm_profit, "ratio")
                )
    if equity_by_period:
        mrq_equity = equity_by_period[max(equity_by_period)]
        anchors["mrq_equity"] = format(mrq_equity, "f")
        if mrq_equity > 0:
            expected_metrics["pb_mrq"] = cast(
                dict[str, object], _metric_fact(market_cap / mrq_equity, "ratio")
            )
    forecasts = operation_records("ths_consensus_eps@1")
    if forecasts:
        forecast_value = _nested_mapping(
            _record_payload(forecasts[0]), "observed_value"
        ).get("value")
        if isinstance(forecast_value, list) and len(forecast_value) >= 2:
            first, second = forecast_value[:2]
            if isinstance(first, dict) and isinstance(second, dict):
                first_eps = _decimal_fact(first.get("mean"))
                next_eps = _decimal_fact(second.get("mean"))
                if first_eps > 0:
                    forward_pe = price / first_eps
                    growth = next_eps / first_eps - Decimal(1)
                    anchors.update(
                        {
                            "forecast_year": first.get("year"),
                            "forecast_eps": format(first_eps, "f"),
                            "next_year": second.get("year"),
                            "next_eps": format(next_eps, "f"),
                            "forward_pe": format(forward_pe, "f"),
                            "growth": format(growth, "f"),
                        }
                    )
                    expected_metrics["forward_pe"] = {
                        **_metric_fact(forward_pe, "ratio"),
                        "forecast_year": first.get("year"),
                    }
                    expected_metrics["forecast_eps_growth"] = {
                        **_metric_fact(growth * Decimal(100), "percent"),
                        "from_year": first.get("year"),
                        "to_year": second.get("year"),
                    }
                    if growth > 0:
                        expected_metrics["peg"] = {
                            **_metric_fact(
                                forward_pe / (growth * Decimal(100)), "ratio"
                            ),
                            "growth_basis": (
                                "next_year_consensus_eps_vs_first_forecast_year"
                            ),
                        }
    calculations_by_kind = {
        str(item.get("kind", "")).rsplit("::", 1)[-1]: item for item in calculations
    }
    expected_formulas = {
        "market_capitalization": "completed_unadjusted_close * effective_total_shares",
        "pe_ttm": "market_capitalization / ttm_attributable_profit",
        "pb_mrq": "market_capitalization / mrq_attributable_equity",
        "forward_pe": "completed_unadjusted_close / first_forecast_year_consensus_eps",
        "forecast_eps_growth": "next_year_eps / first_forecast_year_eps - 1",
        "peg": "forward_pe / (forecast_eps_growth_percent)",
        "pe_digestion_years": (
            "ln(forward_pe / target_pe) / ln(1 + forecast_eps_growth)"
        ),
    }
    expected_operands: dict[str, dict[str, object]] = {
        "market_capitalization": {
            "price": anchors["price"],
            "shares": anchors["shares"],
        }
    }
    if "ttm_profit" in anchors and _decimal_fact(anchors["ttm_profit"]) > 0:
        expected_operands["pe_ttm"] = {
            "market_capitalization": anchors["market_cap"],
            "ttm_attributable_profit": anchors["ttm_profit"],
        }
    if "mrq_equity" in anchors and _decimal_fact(anchors["mrq_equity"]) > 0:
        expected_operands["pb_mrq"] = {
            "market_capitalization": anchors["market_cap"],
            "mrq_attributable_equity": anchors["mrq_equity"],
        }
    if "forecast_eps" in anchors:
        expected_operands["forward_pe"] = {
            "price": anchors["price"],
            "forecast_year": anchors["forecast_year"],
            "consensus_eps": anchors["forecast_eps"],
        }
        expected_operands["forecast_eps_growth"] = {
            "first_year": anchors["forecast_year"],
            "first_year_eps": anchors["forecast_eps"],
            "next_year": anchors["next_year"],
            "next_year_eps": anchors["next_eps"],
        }
    if "growth" in anchors and _decimal_fact(anchors["growth"]) > 0:
        expected_operands["peg"] = {
            "forward_pe": anchors["forward_pe"],
            "forecast_eps_growth_percent": format(
                _decimal_fact(anchors["growth"]) * Decimal(100), "f"
            ),
        }
        digestion = calculations_by_kind.get("pe_digestion_years")
        if digestion is not None:
            digestion_payload = _nested_mapping(digestion, "payload")
            operands = digestion_payload.get("operands")
            if isinstance(operands, dict):
                target_pe = _decimal_fact(operands.get("target_pe"))
                forward_pe = _decimal_fact(anchors["forward_pe"])
                if forward_pe <= target_pe:
                    years = Decimal(0)
                else:
                    with localcontext() as context:
                        context.prec = 28
                        years = (forward_pe / target_pe).ln() / (
                            Decimal(1) + _decimal_fact(anchors["growth"])
                        ).ln()
                expected_metrics["pe_digestion_years"] = {
                    **_metric_fact(years, "years"),
                    "target_pe": format(target_pe, "f"),
                    "target_role": "user_parameter_not_factual_valuation_anchor",
                }
                expected_operands["pe_digestion_years"] = {
                    "forward_pe": anchors["forward_pe"],
                    "forecast_eps_growth": anchors["growth"],
                    "target_pe": format(target_pe, "f"),
                }
    for kind, calculation in calculations_by_kind.items():
        calculation_payload = _nested_mapping(calculation, "payload")
        if calculation_payload.get("formula") != expected_formulas.get(
            kind
        ) or calculation_payload.get("operands") != expected_operands.get(kind):
            raise ValueError("valuation domain facts contradict owned operands")
    if set(calculations_by_kind) != set(expected_operands):
        raise ValueError("valuation domain facts omit an available calculation")
    return security, name, trading_date, expected_metrics, anchors


def _validate_valuation_domain_facts(result: Mapping[str, object]) -> None:
    claim = _claim_by_kind(result, "security_valuation_metrics")
    if claim is None:
        return
    observations = _owned_observations(result)
    calculations = cast(list[dict[str, object]], result["calculations"])
    security, name, trading_date, expected_metrics, anchors = _valuation_owned_facts(
        observations, calculations
    )
    payload = _nested_mapping(claim, "payload")
    subjects = payload.get("subjects")
    basis = payload.get("valuation_basis")
    metrics = payload.get("metrics")
    if (
        not isinstance(subjects, list)
        or not subjects
        or not isinstance(subjects[0], dict)
        or subjects[0].get("name") != name
        or not isinstance(subjects[0].get("security"), dict)
        or f"{subjects[0]['security'].get('exchange')}:{subjects[0]['security'].get('code')}"
        != security
        or not isinstance(basis, dict)
        or basis.get("trading_date") != trading_date
        or _decimal_fact(_nested_mapping(basis, "price").get("value"))
        != _decimal_fact(anchors["price"])
        or _decimal_fact(_nested_mapping(basis, "effective_total_shares").get("value"))
        != _decimal_fact(anchors["shares"])
        or not isinstance(metrics, dict)
    ):
        raise ValueError("valuation domain facts contradict owned observations")
    for metric, expected in expected_metrics.items():
        if metrics.get(metric) != expected:
            raise ValueError("valuation domain facts contain a forged metric")


def _validate_comparison_domain_facts(result: Mapping[str, object]) -> None:
    claim = _claim_by_kind(result, "valuation_compare_members")
    if claim is None:
        return
    payload = _nested_mapping(claim, "payload")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("comparison domain facts are incomplete")
    observations = _owned_observations(result)
    calculations = cast(list[dict[str, object]], result["calculations"])
    source_errors = cast(list[dict[str, object]], result["source_errors"])
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("member_id"), str):
            raise ValueError("comparison domain facts contain an invalid member")
        member_id = str(row["member_id"])
        member_observations = [
            item
            for item in observations
            if str(item["operation_id"]).startswith(f"{member_id}::")
        ]
        member_calculations = [
            item
            for item in calculations
            if str(item.get("kind", "")).startswith(f"{member_id}::")
        ]
        member_errors = [
            item
            for item in source_errors
            if str(item["operation_id"]).startswith(f"{member_id}::")
        ]
        evidence_ids = [str(item["id"]) for item in member_observations]
        if row.get("evidence_ids") != evidence_ids:
            raise ValueError(
                "comparison domain facts do not close over member evidence"
            )
        if member_calculations:
            security, name, trading_date, expected_metrics, _ = _valuation_owned_facts(
                member_observations, member_calculations
            )
            if (
                row.get("status") != "limited"
                or row.get("security") != security
                or row.get("name") != name
                or row.get("trading_date") != trading_date
                or not isinstance(row.get("metrics"), dict)
                or any(
                    cast(dict[str, object], row["metrics"]).get(metric) != expected
                    for metric, expected in expected_metrics.items()
                )
            ):
                raise ValueError("comparison domain facts contradict member outcomes")
        elif not member_errors or row.get("status") != "blocked":
            raise ValueError("comparison domain facts forge member status")


def fold_direct_task_research_result(
    plan: DirectTaskPlan,
    outcomes: Sequence[OperationOutcome],
    *,
    claims: Sequence[DirectTaskClaim] = (),
    calculations: Sequence[DirectTaskCalculation] = (),
    conflicts: Sequence[DirectTaskConflict] = (),
    limitations: Sequence[DirectTaskLimitation] = (),
    summary: str,
) -> UnifiedResearchResult:
    """Fold operation-owned outcomes and typed domain material exactly once."""

    obligations = {
        (item.capability_id, item.operation_id): item for item in plan.obligations
    }
    outcomes_by_owner = {
        (item.capability_id, item.operation_id): item for item in outcomes
    }
    if len(outcomes_by_owner) != len(tuple(outcomes)):
        raise ValueError("direct task outcomes must be unique")
    if set(outcomes_by_owner) != set(obligations):
        raise ValueError("direct task outcomes do not satisfy plan obligations")

    observation_records: list[dict[str, object]] = []
    observation_ids_by_owner: dict[tuple[str, str], list[str]] = {}
    observation_id_by_key: dict[str, str] = {}
    for obligation in plan.obligations:
        owner = (obligation.capability_id, obligation.operation_id)
        outcome = outcomes_by_owner[owner]
        for index, payload in enumerate(outcome.observations):
            record_payload: dict[str, object] = {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "payload": dict(payload),
            }
            observation_id = stable_result_id("observation", record_payload)
            observation_records.append({"id": observation_id, **record_payload})
            observation_ids_by_owner.setdefault(owner, []).append(observation_id)
            local_key = payload.get("id")
            key = (
                local_key
                if isinstance(local_key, str) and local_key
                else f"{outcome.operation_id}:{index}"
            )
            if key in observation_id_by_key:
                raise ValueError("direct task observation keys must be unique")
            observation_id_by_key[key] = observation_id

    source_errors: list[dict[str, object]] = []
    source_error_ids_by_owner: dict[tuple[str, str], list[str]] = {}
    operation_coverage: dict[tuple[str, str], dict[str, object]] = {}
    for obligation in plan.obligations:
        owner = (obligation.capability_id, obligation.operation_id)
        outcome = outcomes_by_owner[owner]
        for error in outcome.source_errors:
            error_payload = {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "code": error["code"],
                "message": error["message"],
            }
            error_id = stable_result_id("source-error", error_payload)
            source_errors.append({"id": error_id, **error_payload})
            source_error_ids_by_owner.setdefault(owner, []).append(error_id)
        operation_coverage[owner] = outcome.to_coverage_dict(
            observation_ids=observation_ids_by_owner.get(owner, ()),
            source_error_ids=source_error_ids_by_owner.get(owner, ()),
        )

    def resolve_keys(keys: Sequence[str]) -> list[str]:
        references: list[str] = []
        for key in keys:
            reference = observation_id_by_key.get(key)
            if reference is None:
                raise ValueError(f"direct task references unknown observation: {key}")
            if reference not in references:
                references.append(reference)
        return references

    def resolve_payload_references(value: object) -> object:
        if isinstance(value, Mapping):
            resolved: dict[str, object] = {}
            for key, nested in value.items():
                if key == "evidence_id":
                    if not isinstance(nested, str):
                        raise ValueError("evidence_id must be an observation reference")
                    resolved[key] = resolve_keys((nested,))[0]
                elif key.endswith("evidence_ids"):
                    if not isinstance(nested, (list, tuple)) or any(
                        not isinstance(item, str) for item in nested
                    ):
                        raise ValueError(f"{key} must contain observation references")
                    resolved[key] = resolve_keys(cast(Sequence[str], nested))
                else:
                    resolved[key] = resolve_payload_references(nested)
            return resolved
        if isinstance(value, (list, tuple)):
            return [resolve_payload_references(item) for item in value]
        return value

    records_by_id = {str(item["id"]): item for item in observation_records}
    claim_records: list[dict[str, object]] = []
    consumer_support: dict[tuple[str, str], int] = {}
    for claim in claims:
        observation_ids = resolve_keys(claim.observation_keys)
        adoptions: list[dict[str, object]] = []
        supporting = 0
        for observation_id in observation_ids:
            record = records_by_id[observation_id]
            owner = (str(record["capability_id"]), str(record["operation_id"]))
            role = obligations[owner].evidence_role(claim.evidence_use)
            if role == "not_applicable":
                raise ValueError("claim cannot adopt an inapplicable observation")
            adoption_role = "supporting" if role == "conditional_supporting" else role
            supporting += adoption_role == "supporting"
            adoptions.append(
                {
                    "observation_id": observation_id,
                    "use": claim.evidence_use,
                    "decision": "adopted",
                    "role": adoption_role,
                    "reason": None,
                }
            )
        claim_payload: dict[str, object] = {
            "kind": claim.kind,
            "evidence_use": claim.evidence_use,
            "observation_ids": observation_ids,
            "evidence_adoptions": adoptions,
            "payload": _json_object(
                cast(Mapping[str, object], resolve_payload_references(claim.payload)),
                path="direct task claim payload",
            ),
        }
        claim_records.append(
            {"id": stable_result_id("claim", claim_payload), **claim_payload}
        )
        support_key = (claim.kind, claim.evidence_use)
        consumer_support[support_key] = max(
            consumer_support.get(support_key, 0), supporting
        )

    calculation_records: list[dict[str, object]] = []
    for calculation in calculations:
        input_ids = resolve_keys(calculation.observation_keys)
        calculation_payload: dict[str, object] = {
            "kind": calculation.kind,
            "evidence_use": calculation.evidence_use,
            "input_ids": input_ids,
            "payload": _json_object(
                cast(
                    Mapping[str, object],
                    resolve_payload_references(calculation.payload),
                ),
                path="direct task calculation payload",
            ),
        }
        calculation_records.append(
            {
                "id": stable_result_id("calculation", calculation_payload),
                **calculation_payload,
            }
        )
        supporting = sum(
            obligations[
                (
                    str(records_by_id[item]["capability_id"]),
                    str(records_by_id[item]["operation_id"]),
                )
            ].evidence_role(calculation.evidence_use)
            in {"supporting", "conditional_supporting"}
            for item in input_ids
        )
        calculation_support_key = (calculation.kind, calculation.evidence_use)
        consumer_support[calculation_support_key] = max(
            consumer_support.get(calculation_support_key, 0), supporting
        )

    conflict_records: list[dict[str, object]] = []
    seen_conflicts: set[str] = set()
    for conflict in conflicts:
        payload = {
            "code": conflict.code,
            "message": conflict.message,
            "observation_ids": resolve_keys(conflict.observation_keys),
        }
        record_id = stable_result_id("conflict", payload)
        if record_id not in seen_conflicts:
            seen_conflicts.add(record_id)
            conflict_records.append({"id": record_id, **payload})
    limitation_records: list[dict[str, object]] = []
    seen_limitations: set[str] = set()
    for limitation in limitations:
        payload = {
            "code": limitation.code,
            "message": limitation.message,
            "references": resolve_keys(limitation.reference_keys),
        }
        record_id = stable_result_id("limitation", payload)
        if record_id not in seen_limitations:
            seen_limitations.add(record_id)
            limitation_records.append({"id": record_id, **payload})

    unmet_core = any(
        item.core
        and consumer_support.get((item.consumer_id, item.evidence_use), 0)
        < item.minimum_supporting_observations
        for item in plan.dependencies
    )
    unmet_noncore = any(
        not item.core
        and consumer_support.get((item.consumer_id, item.evidence_use), 0)
        < item.minimum_supporting_observations
        for item in plan.dependencies
    )
    status: ResearchStatus = (
        "blocked"
        if unmet_core
        else "limited"
        if (
            unmet_noncore
            or source_errors
            or conflict_records
            or limitation_records
            or any(item.experimental for item in plan.obligations)
            or any(
                item.coverage not in {"complete", "observed_empty"} for item in outcomes
            )
        )
        else "supported"
    )

    capability_ids = list(
        dict.fromkeys(item.capability_id for item in plan.obligations)
    )
    coverage: dict[str, object] = {}
    registry: dict[str, object] = {}
    for capability_id in capability_ids:
        capability_obligations = tuple(
            item for item in plan.obligations if item.capability_id == capability_id
        )
        capability_outcomes = tuple(
            outcomes_by_owner[(item.capability_id, item.operation_id)]
            for item in capability_obligations
        )
        coverage[capability_id] = {
            "state": _aggregate_capability_coverage(
                tuple(item.coverage for item in capability_outcomes)
            ),
            "operations": {
                item.operation_id: operation_coverage[
                    (item.capability_id, item.operation_id)
                ]
                for item in capability_obligations
            },
        }
        registry[capability_id] = {
            "id": capability_id,
            "version": plan.version,
            "granularity": f"one_{plan.task_type}_result",
            "operations": [
                {
                    "id": item.operation_id,
                    "source_role": item.source_role,
                    "experimental": item.experimental,
                }
                for item in capability_obligations
            ],
        }

    observation_ids = [str(item["id"]) for item in observation_records]
    claim_ids = [str(item["id"]) for item in claim_records]
    calculation_ids = [str(item["id"]) for item in calculation_records]
    conflict_ids = [str(item["id"]) for item in conflict_records]
    limitation_ids = [str(item["id"]) for item in limitation_records]
    source_error_ids = [str(item["id"]) for item in source_errors]
    brief_payload: dict[str, object] = {
        "status": status,
        "summary": summary,
        "observation_ids": observation_ids,
        "claim_ids": claim_ids,
        "calculation_ids": calculation_ids,
        "conflict_ids": conflict_ids,
        "limitation_ids": limitation_ids,
        "source_error_ids": source_error_ids,
    }
    brief_id = stable_result_id("brief", brief_payload)
    obligation_payloads = [item.to_dict() for item in plan.obligations]
    dependency_payloads = [item.to_dict() for item in plan.dependencies]
    plan_id = stable_result_id(
        "plan",
        {
            "version": plan.version,
            "task_type": plan.task_type,
            "operation_obligations": obligation_payloads,
            "research_dependencies": dependency_payloads,
            "field_dispositions": [
                {
                    "capability_id": outcome.capability_id,
                    "operation_id": outcome.operation_id,
                    "fields": [item.to_dict() for item in outcome.field_dispositions],
                }
                for outcome in outcomes
            ],
        },
    )
    return UnifiedResearchResult(
        status=status,
        research_anchor={"as_of": plan.as_of, "timezone": "Asia/Shanghai"},
        plan={
            "id": plan_id,
            "version": plan.version,
            "task_type": plan.task_type,
            "capabilities": capability_ids,
            "operation_obligations": obligation_payloads,
            "research_dependencies": dependency_payloads,
        },
        capability_registry=registry,
        coverage=coverage,
        observations=tuple(observation_records),
        claims=tuple(claim_records),
        calculations=tuple(calculation_records),
        conflicts=tuple(conflict_records),
        limitations=tuple(limitation_records),
        source_errors=tuple(source_errors),
        task_views={
            plan.task_type: {
                "status": status,
                "observation_ids": observation_ids,
                "claim_ids": claim_ids,
                "calculation_ids": calculation_ids,
                "conflict_ids": conflict_ids,
                "limitation_ids": limitation_ids,
                "source_error_ids": source_error_ids,
                "brief_id": brief_id,
            }
        },
        briefs=({"id": brief_id, **brief_payload},),
    )


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
    "DirectTaskCalculation",
    "DirectTaskClaim",
    "DirectTaskConflict",
    "DirectTaskLimitation",
    "DirectTaskPlan",
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
    "fold_direct_task_research_result",
    "stable_result_id",
]
