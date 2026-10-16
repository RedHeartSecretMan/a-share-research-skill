"""Pure request contracts for fixed, independently versioned workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Literal, Mapping, TypeVar, cast

from .bounded_public_data import MAX_PUBLIC_DATA_DEPTH, MAX_PUBLIC_DATA_ITEMS

_ValueT = TypeVar("_ValueT")
_SnapshotContainer = dict[str, Any] | list[Any]


@dataclass(slots=True)
class _SnapshotMemoEntry:
    source: object
    owned: _SnapshotContainer
    subtree_items: int
    max_relative_depth: int


@dataclass(slots=True)
class _SnapshotFrame:
    source: object
    owned: _SnapshotContainer
    iterator: Iterator[object]
    is_mapping: bool
    identity: int
    depth: int
    parent: _SnapshotFrame | None
    parent_key: str | None
    subtree_items: int = 1
    max_relative_depth: int = 0


@dataclass(slots=True)
class _FreezeFrame:
    owned: _SnapshotContainer
    iterator: Iterator[object]
    is_mapping: bool
    parent: _FreezeFrame | None
    parent_key: str | None


def _snapshot_json_value(value: object) -> Any:
    """Read an untrusted JSON-shaped value exactly once into owned containers."""

    memo: dict[int, _SnapshotMemoEntry] = {}
    active: dict[int, object] = {}
    stack: list[_SnapshotFrame] = []
    pending: tuple[object, int, _SnapshotFrame | None, str | None] | None = (
        value,
        0,
        None,
        None,
    )
    item_count = 0
    missing = object()
    result: Any = missing

    def place(
        owned: Any,
        subtree_items: int,
        max_relative_depth: int,
        parent: _SnapshotFrame | None,
        parent_key: str | None,
    ) -> None:
        nonlocal result
        if parent is None:
            result = owned
            return
        if isinstance(parent.owned, dict):
            if parent_key is None:
                raise AssertionError("mapping snapshot placement requires a key")
            parent.owned[parent_key] = owned
        else:
            parent.owned.append(owned)
        parent.subtree_items += subtree_items
        parent.max_relative_depth = max(
            parent.max_relative_depth,
            1 + max_relative_depth,
        )

    try:
        while pending is not None or stack:
            if pending is not None:
                item, depth, parent, parent_key = pending
                pending = None
                item_count += 1
                if item_count > MAX_PUBLIC_DATA_ITEMS or depth > MAX_PUBLIC_DATA_DEPTH:
                    raise ValueError(
                        "research_workflow request exceeds its structural bounds"
                    )

                is_mapping = isinstance(item, Mapping)
                is_sequence = isinstance(item, (list, tuple))
                if is_mapping or is_sequence:
                    identity = id(item)
                    if identity in active:
                        raise ValueError(
                            "research_workflow request contains a cyclic container"
                        )
                    cached = memo.get(identity)
                    if cached is not None and cached.source is item:
                        item_count += cached.subtree_items - 1
                        if (
                            item_count > MAX_PUBLIC_DATA_ITEMS
                            or depth + cached.max_relative_depth > MAX_PUBLIC_DATA_DEPTH
                        ):
                            raise ValueError(
                                "research_workflow request exceeds its structural bounds"
                            )
                        place(
                            cached.owned,
                            cached.subtree_items,
                            cached.max_relative_depth,
                            parent,
                            parent_key,
                        )
                        continue

                    active[identity] = item
                    if is_mapping:
                        owned: _SnapshotContainer = {}
                        iterator = iter(cast(Mapping[object, object], item))
                    else:
                        owned = []
                        iterator = cast(
                            Iterator[object],
                            iter(cast(list[Any] | tuple[Any, ...], item)),
                        )
                    stack.append(
                        _SnapshotFrame(
                            source=item,
                            owned=owned,
                            iterator=iterator,
                            is_mapping=is_mapping,
                            identity=identity,
                            depth=depth,
                            parent=parent,
                            parent_key=parent_key,
                        )
                    )
                    continue

                item_type = type(item)
                if (
                    item is None
                    or item_type is str
                    or item_type is bool
                    or item_type is int
                ):
                    place(item, 1, 0, parent, parent_key)
                    continue
                if item_type is float and isfinite(cast(float, item)):
                    place(item, 1, 0, parent, parent_key)
                    continue
                raise ValueError(
                    "research_workflow request values must be JSON-compatible"
                )

            frame = stack[-1]
            try:
                child_or_key = next(frame.iterator)
            except StopIteration:
                stack.pop()
                active.pop(frame.identity)
                completed = _SnapshotMemoEntry(
                    source=frame.source,
                    owned=frame.owned,
                    subtree_items=frame.subtree_items,
                    max_relative_depth=frame.max_relative_depth,
                )
                memo[frame.identity] = completed
                place(
                    completed.owned,
                    completed.subtree_items,
                    completed.max_relative_depth,
                    frame.parent,
                    frame.parent_key,
                )
                continue

            if frame.is_mapping:
                key = child_or_key
                if type(key) is not str:
                    raise ValueError("research_workflow mapping keys must be strings")
                owned_mapping = cast(dict[str, Any], frame.owned)
                if key in owned_mapping:
                    raise ValueError("research_workflow mapping keys must be unique")
                child = cast(Mapping[object, object], frame.source)[key]
                pending = (child, frame.depth + 1, frame, key)
            else:
                pending = (child_or_key, frame.depth + 1, frame, None)
    except Exception:
        active.clear()
        raise

    if result is missing:
        raise AssertionError("workflow snapshot did not produce a root")
    return result


def _snapshot_request(request: Mapping[str, object]) -> dict[str, Any]:
    return cast(dict[str, Any], _snapshot_json_value(request))


def _freeze_value(value: Any) -> Any:
    stack: list[_FreezeFrame] = []
    pending: tuple[Any, _FreezeFrame | None, str | None] | None = (
        value,
        None,
        None,
    )
    missing = object()
    result: Any = missing

    def place(
        frozen: Any,
        parent: _FreezeFrame | None,
        parent_key: str | None,
    ) -> None:
        nonlocal result
        if parent is None:
            result = frozen
            return
        if isinstance(parent.owned, dict):
            if parent_key is None:
                raise AssertionError("mapping freeze placement requires a key")
            parent.owned[parent_key] = frozen
        else:
            parent.owned.append(frozen)

    while pending is not None or stack:
        if pending is not None:
            item, parent, parent_key = pending
            pending = None
            if isinstance(item, Mapping):
                stack.append(
                    _FreezeFrame(
                        owned={},
                        iterator=cast(Iterator[object], iter(item.items())),
                        is_mapping=True,
                        parent=parent,
                        parent_key=parent_key,
                    )
                )
                continue
            if isinstance(item, (list, tuple)):
                stack.append(
                    _FreezeFrame(
                        owned=[],
                        iterator=cast(Iterator[object], iter(item)),
                        is_mapping=False,
                        parent=parent,
                        parent_key=parent_key,
                    )
                )
                continue
            place(item, parent, parent_key)
            continue

        frame = stack[-1]
        try:
            child_or_entry = next(frame.iterator)
        except StopIteration:
            stack.pop()
            frozen = (
                MappingProxyType(cast(dict[str, Any], frame.owned))
                if frame.is_mapping
                else tuple(cast(list[Any], frame.owned))
            )
            place(frozen, frame.parent, frame.parent_key)
            continue

        if frame.is_mapping:
            key, child = cast(tuple[str, Any], child_or_entry)
            pending = (child, frame, key)
        else:
            pending = (child_or_entry, frame, None)

    if result is missing:
        raise AssertionError("workflow freeze did not produce a root")
    return result


def _freeze_mapping(value: Mapping[str, _ValueT]) -> Mapping[str, _ValueT]:
    return cast(Mapping[str, _ValueT], _freeze_value(value))


def _json_safe_copy(value: Any) -> Any:
    return _snapshot_json_value(value)


@dataclass(frozen=True, slots=True)
class _ResultArtifactConfiguration:
    """Validated execution-only configuration kept outside the public request."""

    path: Path
    mode: Literal["create", "resume"]


@dataclass(frozen=True, slots=True)
class _ValidatedWorkflowRequest:
    """A strict workflow request split into public and execution-only facts."""

    workflow_id: str
    workflow_version: str
    inputs: Mapping[str, Any]
    public_request: Mapping[str, Any]
    result_artifact: _ResultArtifactConfiguration | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", _freeze_mapping(self.inputs))
        object.__setattr__(
            self,
            "public_request",
            _freeze_mapping(self.public_request),
        )

    def inputs_copy(self) -> dict[str, Any]:
        """Return an isolated mutable JSON-container copy for execution."""

        return cast(dict[str, Any], _json_safe_copy(self.inputs))

    def public_request_copy(self) -> dict[str, Any]:
        """Return an isolated mutable JSON-container copy for serialization."""

        return cast(dict[str, Any], _json_safe_copy(self.public_request))


class _WorkflowShape(Enum):
    MARKET_OVERVIEW = "market_overview"
    MARKET_SCREEN = "market_screen"
    SINGLE_SECURITY_RESEARCH = "single_security_research"
    SINGLE_SECURITY_VALUATION = "single_security_valuation"
    VALUATION_COMPARISON = "valuation_comparison"
    THEME_REPORT_RESEARCH = "theme_report_research"
    NEW_SECURITY_RESEARCH = "new_security_research"


@dataclass(frozen=True, slots=True)
class _WorkflowContract:
    """One immutable workflow identity bound to exactly one request shape."""

    workflow_id: str
    version: str
    _shape: _WorkflowShape


_MARKET_OVERVIEW_V1 = _WorkflowContract(
    "market_overview_research", "1.0", _WorkflowShape.MARKET_OVERVIEW
)
_MARKET_OVERVIEW_V2 = _WorkflowContract(
    "market_overview_research", "2.0", _WorkflowShape.MARKET_OVERVIEW
)
_MARKET_SCREEN_V1 = _WorkflowContract(
    "market_screen_research", "1.0", _WorkflowShape.MARKET_SCREEN
)
_SINGLE_SECURITY_RESEARCH_V1 = _WorkflowContract(
    "single_security_research", "1.0", _WorkflowShape.SINGLE_SECURITY_RESEARCH
)
_SINGLE_SECURITY_VALUATION_V1 = _WorkflowContract(
    "single_security_valuation", "1.0", _WorkflowShape.SINGLE_SECURITY_VALUATION
)
_VALUATION_COMPARISON_V1 = _WorkflowContract(
    "valuation_comparison", "1.0", _WorkflowShape.VALUATION_COMPARISON
)
_THEME_REPORT_RESEARCH_V1 = _WorkflowContract(
    "theme_report_research", "1.0", _WorkflowShape.THEME_REPORT_RESEARCH
)
_NEW_SECURITY_RESEARCH_V1 = _WorkflowContract(
    "new_security_research", "1.0", _WorkflowShape.NEW_SECURITY_RESEARCH
)

_DEFINED_WORKFLOW_CONTRACTS = (
    _MARKET_OVERVIEW_V1,
    _MARKET_OVERVIEW_V2,
    _MARKET_SCREEN_V1,
    _SINGLE_SECURITY_RESEARCH_V1,
    _SINGLE_SECURITY_VALUATION_V1,
    _VALUATION_COMPARISON_V1,
    _THEME_REPORT_RESEARCH_V1,
    _NEW_SECURITY_RESEARCH_V1,
)
_DEFINED_WORKFLOW_IDENTITIES = tuple(
    (contract.workflow_id, contract.version) for contract in _DEFINED_WORKFLOW_CONTRACTS
)
if len(set(_DEFINED_WORKFLOW_IDENTITIES)) != len(_DEFINED_WORKFLOW_IDENTITIES):
    raise AssertionError("workflow contract identity is defined more than once")


def _fixed_registry(
    contracts: tuple[_WorkflowContract, ...],
) -> Mapping[str, _WorkflowContract]:
    registry = {contract.workflow_id: contract for contract in contracts}
    if len(registry) != len(contracts):
        raise AssertionError("workflow registry contains duplicate identities")
    return MappingProxyType(registry)


_REGISTERED_WORKFLOW_CONTRACTS = _fixed_registry(
    (
        _MARKET_OVERVIEW_V2,
        _SINGLE_SECURITY_RESEARCH_V1,
        _SINGLE_SECURITY_VALUATION_V1,
        _VALUATION_COMPARISON_V1,
        _THEME_REPORT_RESEARCH_V1,
        _NEW_SECURITY_RESEARCH_V1,
    )
)
_V040_WORKFLOW_CONTRACTS = _fixed_registry(
    (
        _MARKET_OVERVIEW_V2,
        _MARKET_SCREEN_V1,
        _SINGLE_SECURITY_RESEARCH_V1,
        _SINGLE_SECURITY_VALUATION_V1,
        _VALUATION_COMPARISON_V1,
        _THEME_REPORT_RESEARCH_V1,
        _NEW_SECURITY_RESEARCH_V1,
    )
)

REGISTERED_WORKFLOW_VERSIONS: Mapping[str, str] = MappingProxyType(
    {
        workflow_id: contract.version
        for workflow_id, contract in _REGISTERED_WORKFLOW_CONTRACTS.items()
    }
)
V040_WORKFLOW_VERSIONS: Mapping[str, str] = MappingProxyType(
    {
        workflow_id: contract.version
        for workflow_id, contract in _V040_WORKFLOW_CONTRACTS.items()
    }
)


def _validate_workflow_request(
    request: Mapping[str, object],
    registry: Mapping[str, _WorkflowContract],
) -> _ValidatedWorkflowRequest:
    contract = _resolve_registered_contract(request, registry)
    _, _, inputs = _strict_workflow_parts(request)
    if contract._shape is _WorkflowShape.MARKET_SCREEN:
        return _validate_market_screen_ingress(request, contract, registry, inputs)
    if contract._shape is _WorkflowShape.MARKET_OVERVIEW:
        if request.get("window") is not None or request.get("subjects") != []:
            raise ValueError("market_overview_research request scope is invalid")
        if not set(inputs).issubset({"completeness_mode", "deep_extensions"}):
            raise ValueError(
                "market_overview_research inputs do not match the workflow contract"
            )
        completeness_mode = inputs.get("completeness_mode", "standard")
        if not isinstance(completeness_mode, str) or completeness_mode not in {
            "standard",
            "fast",
        }:
            raise ValueError(
                "market_overview_research completeness_mode must be standard or fast"
            )
        deep_extensions = inputs.get("deep_extensions", [])
        if (
            not isinstance(deep_extensions, list)
            or any(item != "market_wide_intraday" for item in deep_extensions)
            or len(set(deep_extensions)) != len(deep_extensions)
        ):
            raise ValueError(
                "market_overview_research deep_extensions contains an unsupported value"
            )
        return _build_validated_workflow_request(
            contract=contract,
            registry=registry,
            inputs=cast(dict[str, Any], _json_safe_copy(inputs)),
            public_request=cast(dict[str, Any], _json_safe_copy(request)),
        )
    _validate_existing_workflow_shape(contract, request, inputs)
    return _build_validated_workflow_request(
        contract=contract,
        registry=registry,
        inputs=cast(dict[str, Any], _json_safe_copy(inputs)),
        public_request=cast(dict[str, Any], _json_safe_copy(request)),
    )


def validate_registered_workflow_request(
    request: Mapping[str, object],
) -> _ValidatedWorkflowRequest:
    """Validate only a workflow version currently registered for execution."""

    return _validate_workflow_request(
        _snapshot_request(request),
        _REGISTERED_WORKFLOW_CONTRACTS,
    )


def validate_v040_workflow_request(
    request: Mapping[str, object],
) -> _ValidatedWorkflowRequest:
    """Validate only a workflow version in the fixed v0.4.0 registry."""

    return _validate_workflow_request(
        _snapshot_request(request),
        _V040_WORKFLOW_CONTRACTS,
    )


def validate_registered_workflow_public_scope(
    request: Mapping[str, object],
) -> _ValidatedWorkflowRequest:
    """Validate a sanitized request scope against the current registry."""

    return _validate_workflow_public_scope(
        _snapshot_request(request),
        _REGISTERED_WORKFLOW_CONTRACTS,
    )


def validate_v040_workflow_public_scope(
    request: Mapping[str, object],
) -> _ValidatedWorkflowRequest:
    """Validate a sanitized request scope against the fixed v0.4.0 registry."""

    return _validate_workflow_public_scope(
        _snapshot_request(request),
        _V040_WORKFLOW_CONTRACTS,
    )


def _validate_workflow_public_scope(
    request: Mapping[str, object],
    registry: Mapping[str, _WorkflowContract],
) -> _ValidatedWorkflowRequest:
    """Validate an artifact-free request scope against an active registry."""

    contract = _resolve_registered_contract(request, registry)
    _, _, inputs = _strict_workflow_parts(request)
    if contract._shape is not _WorkflowShape.MARKET_SCREEN:
        return _validate_workflow_request(request, registry)
    return _validate_market_screen_public_scope(
        request,
        contract,
        registry,
        inputs,
    )


def _build_validated_workflow_request(
    *,
    contract: _WorkflowContract,
    registry: Mapping[str, _WorkflowContract],
    inputs: Mapping[str, Any],
    public_request: Mapping[str, Any],
    result_artifact: _ResultArtifactConfiguration | None = None,
) -> _ValidatedWorkflowRequest:
    workflow_id, workflow_version, _ = _strict_workflow_parts(public_request)
    if (
        workflow_id != contract.workflow_id
        or workflow_version != contract.version
        or registry.get(workflow_id) is not contract
    ):
        raise AssertionError("validated workflow identity escaped registry authority")
    return _ValidatedWorkflowRequest(
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        inputs=inputs,
        public_request=public_request,
        result_artifact=result_artifact,
    )


def _validate_workflow_envelope(request: Mapping[str, object]) -> None:
    if set(request) != {
        "schema_version",
        "task_type",
        "subjects",
        "as_of",
        "window",
        "parameters",
        "source_policy",
    }:
        raise ValueError("research_workflow request contains unknown or missing fields")
    source_policy = request.get("source_policy")
    if (
        request.get("schema_version") != "1.0"
        or request.get("task_type") != "research_workflow"
        or not isinstance(request.get("subjects"), list)
        or not isinstance(request.get("as_of"), str)
        or request.get("window") is not None
        or not isinstance(source_policy, Mapping)
        or set(source_policy)
        != {"allow_experimental", "allow_credentials", "allow_fallback"}
        or any(not isinstance(value, bool) for value in source_policy.values())
    ):
        raise ValueError("research_workflow request envelope is invalid")
    _strict_date(request["as_of"], "as_of")


def _resolve_registered_contract(
    request: Mapping[str, object],
    registry: Mapping[str, _WorkflowContract],
) -> _WorkflowContract:
    """Resolve only generic identity facts before any workflow-specific shape."""

    _validate_workflow_envelope(request)
    parameters = request.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("research_workflow parameters must be an object")
    workflow = parameters.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValueError("research_workflow parameters.workflow is invalid")
    workflow_id = workflow.get("id")
    workflow_version = workflow.get("version")
    if not isinstance(workflow_id, str) or not isinstance(workflow_version, str):
        raise ValueError("research_workflow identity and version must be strings")
    registered = registry.get(workflow_id)
    if registered is None:
        raise ValueError("research_workflow id is not supported")
    if registered.version != workflow_version:
        raise ValueError("research_workflow version is not supported")
    return registered


def _strict_workflow_parts(
    request: Mapping[str, object],
) -> tuple[str, str, Mapping[str, Any]]:
    parameters = request.get("parameters")
    if not isinstance(parameters, Mapping) or set(parameters) != {
        "workflow",
        "inputs",
    }:
        raise ValueError("research_workflow parameters must be an object")
    workflow = parameters.get("workflow")
    inputs = parameters.get("inputs")
    if (
        not isinstance(workflow, Mapping)
        or set(workflow) != {"id", "version"}
        or not isinstance(inputs, Mapping)
    ):
        raise ValueError("research_workflow parameters are invalid")
    workflow_id = workflow.get("id")
    workflow_version = workflow.get("version")
    if not isinstance(workflow_id, str) or not isinstance(workflow_version, str):
        raise ValueError("research_workflow identity and version must be strings")
    return workflow_id, workflow_version, inputs


def _validate_market_screen_ingress(
    request: Mapping[str, object],
    contract: _WorkflowContract,
    registry: Mapping[str, _WorkflowContract],
    inputs: Mapping[str, Any],
) -> _ValidatedWorkflowRequest:
    required_inputs = {
        "screening_plan",
        "completeness_mode",
        "result_artifact",
    }
    if not required_inputs.issubset(inputs) or not set(inputs).issubset(
        {*required_inputs, "candidate_limit"}
    ):
        raise ValueError(
            "market_screen_research inputs do not match the workflow contract"
        )
    public_request = cast(dict[str, Any], _json_safe_copy(request))
    public_parameters = cast(dict[str, Any], public_request["parameters"])
    public_inputs = cast(dict[str, Any], public_parameters["inputs"])
    result_artifact = public_inputs.pop("result_artifact")
    normalized_inputs = _normalize_market_screen_inputs(request, public_inputs)
    artifact_configuration = _validate_result_artifact_configuration(result_artifact)
    return _build_validated_workflow_request(
        contract=contract,
        registry=registry,
        inputs=normalized_inputs,
        public_request=public_request,
        result_artifact=artifact_configuration,
    )


def _validate_market_screen_public_scope(
    request: Mapping[str, object],
    contract: _WorkflowContract,
    registry: Mapping[str, _WorkflowContract],
    inputs: Mapping[str, Any],
) -> _ValidatedWorkflowRequest:
    normalized_inputs = _normalize_market_screen_inputs(request, inputs)
    return _build_validated_workflow_request(
        contract=contract,
        registry=registry,
        inputs=normalized_inputs,
        public_request=cast(dict[str, Any], _json_safe_copy(request)),
    )


def _normalize_market_screen_inputs(
    request: Mapping[str, object],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    if request.get("window") is not None or request.get("subjects") != []:
        raise ValueError("market_screen_research request scope is invalid")
    required_inputs = {"screening_plan", "completeness_mode"}
    if not required_inputs.issubset(inputs) or not set(inputs).issubset(
        {*required_inputs, "candidate_limit"}
    ):
        raise ValueError(
            "market_screen_research public inputs do not match the workflow contract"
        )
    screening_plan = inputs["screening_plan"]
    if not isinstance(screening_plan, Mapping) or dict(screening_plan) != {
        "id": "fundamental_research_priority",
        "version": "1.0",
    }:
        raise ValueError("market_screen_research screening_plan is unsupported")
    completeness_mode = inputs["completeness_mode"]
    if not isinstance(completeness_mode, str) or completeness_mode not in {
        "fast",
        "standard",
    }:
        raise ValueError("market_screen_research completeness_mode is unsupported")
    default_candidate_limit = 0 if completeness_mode == "fast" else 5
    candidate_limit = inputs.get("candidate_limit", default_candidate_limit)
    if isinstance(candidate_limit, bool) or not isinstance(candidate_limit, int):
        raise ValueError("market_screen_research candidate_limit must be an integer")
    if completeness_mode == "fast" and candidate_limit != 0:
        raise ValueError(
            "market_screen_research fast candidate_limit must be omitted or zero"
        )
    if completeness_mode == "standard" and not 1 <= candidate_limit <= 20:
        raise ValueError(
            "market_screen_research standard candidate_limit must be from 1 to 20"
        )
    normalized_inputs = cast(dict[str, Any], _json_safe_copy(inputs))
    normalized_inputs["candidate_limit"] = candidate_limit
    return normalized_inputs


def _validate_result_artifact_configuration(
    value: object,
) -> _ResultArtifactConfiguration:
    if not isinstance(value, Mapping) or set(value) != {"path", "mode"}:
        raise ValueError("market_screen_research result_artifact is invalid")
    path_value = value["path"]
    mode = value["mode"]
    if not isinstance(path_value, str):
        raise ValueError("market_screen_research result_artifact path must be absolute")
    artifact_path = Path(path_value)
    if (
        "\x00" in path_value
        or not artifact_path.is_absolute()
        or ".." in artifact_path.parts
        or artifact_path == Path(artifact_path.anchor)
    ):
        raise ValueError("market_screen_research result_artifact path must be absolute")
    if not isinstance(mode, str) or mode not in {"create", "resume"}:
        raise ValueError(
            "market_screen_research result_artifact mode must be create or resume"
        )
    return _ResultArtifactConfiguration(
        path=artifact_path,
        mode=cast(Literal["create", "resume"], mode),
    )


def _validate_existing_workflow_shape(
    contract: _WorkflowContract,
    request: Mapping[str, object],
    inputs: Mapping[str, Any],
) -> None:
    subjects = request["subjects"]
    as_of = cast(str, request["as_of"])
    if contract._shape is _WorkflowShape.SINGLE_SECURITY_RESEARCH:
        _require_subject_count(subjects, minimum=1, maximum=1)
        allowed_inputs = {"target_pe", "completeness_mode", "deep_extensions"}
        if "target_pe" not in inputs or not set(inputs).issubset(allowed_inputs):
            raise ValueError(
                "single_security_research inputs do not match the workflow contract"
            )
        _require_target_pe(inputs["target_pe"])
        completeness_mode = inputs.get("completeness_mode", "standard")
        if not isinstance(completeness_mode, str) or completeness_mode not in {
            "standard",
            "fast",
        }:
            raise ValueError(
                "single_security_research completeness_mode must be standard or fast"
            )
        deep_extensions = inputs.get("deep_extensions", [])
        if (
            not isinstance(deep_extensions, list)
            or any(
                not isinstance(item, str)
                or item not in {"long_history", "large_materials"}
                for item in deep_extensions
            )
            or len(set(deep_extensions)) != len(deep_extensions)
        ):
            raise ValueError(
                "single_security_research deep_extensions contains an unsupported value"
            )
        if completeness_mode == "fast" and "large_materials" in deep_extensions:
            raise ValueError(
                "single_security_research fast cannot be combined with large_materials"
            )
        return
    if contract._shape is _WorkflowShape.SINGLE_SECURITY_VALUATION:
        _require_subject_count(subjects, minimum=1, maximum=1)
        _require_inputs(inputs, {"target_pe"})
        _require_target_pe(inputs["target_pe"])
        return
    if contract._shape is _WorkflowShape.VALUATION_COMPARISON:
        _require_subject_count(subjects, minimum=2, maximum=10)
        _require_inputs(inputs, {"target_pe"})
        _require_target_pe(inputs["target_pe"])
        return
    if contract._shape is _WorkflowShape.THEME_REPORT_RESEARCH:
        if subjects:
            raise ValueError("theme_report_research takes no subjects")
        _require_inputs(
            inputs,
            {
                "query",
                "published_from",
                "published_to",
                "limit",
                "verify_documents",
            },
        )
        _require_string_array(inputs["query"], "theme_report_research query")
        _require_window(
            inputs,
            start_field="published_from",
            end_field="published_to",
            as_of=as_of,
            allow_future_days=0,
        )
        _require_limit(inputs["limit"])
        _require_boolean(inputs["verify_documents"], "verify_documents")
        return
    if contract._shape is _WorkflowShape.NEW_SECURITY_RESEARCH:
        _require_subject_count(subjects, minimum=1, maximum=1)
        _require_inputs(
            inputs,
            {
                "target_pe",
                "report_window",
                "market_window",
                "lockup_window",
                "fund_flow_period",
                "limit",
                "verify_documents",
            },
        )
        _require_target_pe(inputs["target_pe"])
        _require_nested_window(
            inputs["report_window"],
            start_field="published_from",
            end_field="published_to",
            as_of=as_of,
            allow_future_days=0,
            name="report_window",
        )
        _require_nested_window(
            inputs["market_window"],
            start_field="observed_from",
            end_field="observed_to",
            as_of=as_of,
            allow_future_days=0,
            name="market_window",
        )
        _require_nested_window(
            inputs["lockup_window"],
            start_field="observed_from",
            end_field="observed_to",
            as_of=as_of,
            allow_future_days=90,
            name="lockup_window",
        )
        fund_flow_period = inputs["fund_flow_period"]
        if not isinstance(fund_flow_period, str) or fund_flow_period not in {
            "5d",
            "10d",
        }:
            raise ValueError("new_security_research fund_flow_period must be 5d or 10d")
        _require_limit(inputs["limit"])
        _require_boolean(inputs["verify_documents"], "verify_documents")
        return
    raise AssertionError("workflow contract has no request shape")


def _require_inputs(inputs: Mapping[str, object], fields: set[str]) -> None:
    if set(inputs) != fields:
        raise ValueError("research_workflow inputs do not match the workflow contract")


def _require_subject_count(subjects: object, *, minimum: int, maximum: int) -> None:
    if not isinstance(subjects, list) or not minimum <= len(subjects) <= maximum:
        raise ValueError(
            "research_workflow subjects do not match the workflow contract"
        )
    for subject in subjects:
        _require_workflow_subject(subject)


def _require_workflow_subject(subject: object) -> None:
    if not isinstance(subject, Mapping) or set(subject) != {
        "clue",
        "issuer_security_class_count",
    }:
        raise ValueError("workflow subject contains unknown fields")
    clue = subject.get("clue")
    class_count = subject.get("issuer_security_class_count")
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("workflow subject requires a non-empty clue")
    if (
        not isinstance(class_count, int)
        or isinstance(class_count, bool)
        or class_count < 1
    ):
        raise ValueError(
            "workflow subject requires a positive issuer_security_class_count"
        )


def _require_target_pe(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("workflow target_pe must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("workflow target_pe must be a decimal string") from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("workflow target_pe must be positive")


def _require_string_array(value: object, name: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{name} must be a non-empty string array")


def _require_limit(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("workflow limit must be from 1 to 100")


def _require_boolean(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"workflow {name} must be boolean")


def _require_nested_window(
    value: object,
    *,
    start_field: str,
    end_field: str,
    as_of: str,
    allow_future_days: int,
    name: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != {start_field, end_field}:
        raise ValueError(f"new_security_research {name} is invalid")
    _require_window(
        value,
        start_field=start_field,
        end_field=end_field,
        as_of=as_of,
        allow_future_days=allow_future_days,
    )


def _require_window(
    value: Mapping[str, object],
    *,
    start_field: str,
    end_field: str,
    as_of: str,
    allow_future_days: int,
) -> None:
    start = _strict_date(value.get(start_field), start_field)
    end = _strict_date(value.get(end_field), end_field)
    if start > end:
        raise ValueError("workflow window starts after it ends")
    if end > date.fromisoformat(as_of) + timedelta(days=allow_future_days):
        raise ValueError("workflow window exceeds its research boundary")


def _strict_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"workflow {field} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"workflow {field} must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"workflow {field} must use YYYY-MM-DD")
    return parsed


__all__ = [
    "REGISTERED_WORKFLOW_VERSIONS",
    "V040_WORKFLOW_VERSIONS",
    "validate_registered_workflow_public_scope",
    "validate_registered_workflow_request",
    "validate_v040_workflow_public_scope",
    "validate_v040_workflow_request",
]
