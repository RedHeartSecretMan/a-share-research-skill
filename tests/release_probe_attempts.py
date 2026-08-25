"""Typed, fail-closed assembly of sanitized release-probe attempts."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Mapping, NoReturn, Sequence, cast

_CANDIDATE_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PLAN_ID = re.compile(r"^plan-[0-9a-f]{24}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_ATTEMPT_KEYS = {
    "schema_version",
    "artifact_kind",
    "candidate_sha",
    "verified_at",
    "installation_health",
    "plan_snapshots",
    "installation_loads",
    "probe_samples",
    "attempt_digest",
}
_SAMPLE_KEYS = {
    "producer_operation_id",
    "probe",
    "probe_status",
    "attempted",
    "source_result",
    "candidate_sha",
    "verified_at",
    "request_profile",
    "attempt_digest",
}
_SOURCE_RESULTS = {
    "complete",
    "observed_empty",
    "partial",
    "unavailable",
    "credential_blocked",
}
_BASE_SAMPLE_KEYS = {
    "producer_operation_id",
    "probe",
    "probe_status",
    "attempted",
    "source_result",
    "request_profile",
}
_MAX_ATTEMPTS = 16
_MAX_ATTEMPT_BYTES = 16 * 1024 * 1024
_MAX_ATTEMPT_AGE = timedelta(days=7)
_MAX_STRUCTURE_DEPTH = 64
_MAX_STRUCTURE_ITEMS = 200_000
_MAX_STRING_LENGTH = 64 * 1024


def create_release_probe_attempt(
    *,
    candidate_sha: str,
    verified_at: str,
    installation_health: Mapping[str, object],
    plan_snapshots: Sequence[Mapping[str, object]],
    installation_loads: Sequence[Mapping[str, object]],
    probe_samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Seal one sanitized public-run attempt with immutable provenance."""

    canonical_candidate = _candidate_sha(candidate_sha)
    canonical_verified_at = _timestamp(verified_at)
    if not probe_samples:
        raise ValueError("release probe attempt has no samples")
    samples: list[dict[str, object]] = []
    for raw_sample in probe_samples:
        sample = deepcopy(dict(raw_sample))
        if (
            set(sample) != _BASE_SAMPLE_KEYS
            or sample.get("probe_status") != "passed"
            or not isinstance(sample.get("attempted"), bool)
            or sample.get("source_result") not in _SOURCE_RESULTS
        ):
            raise ValueError("release probe attempt sample is invalid")
        for key in ("producer_operation_id", "probe", "request_profile"):
            _reference(sample.get(key), f"release probe sample {key}")
        sample["candidate_sha"] = canonical_candidate
        sample["verified_at"] = canonical_verified_at
        samples.append(sample)
    owners = [cast(str, item["producer_operation_id"]) for item in samples]
    if len(owners) != len(set(owners)):
        raise ValueError("release probe attempt producer is duplicated")
    attempt: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_kind": "core_operation_release_probe_attempt",
        "candidate_sha": canonical_candidate,
        "verified_at": canonical_verified_at,
        "installation_health": deepcopy(dict(installation_health)),
        "plan_snapshots": [deepcopy(dict(item)) for item in plan_snapshots],
        "installation_loads": [deepcopy(dict(item)) for item in installation_loads],
        "probe_samples": samples,
    }
    digest = _attempt_digest(attempt)
    attempt["attempt_digest"] = digest
    for sample in samples:
        sample["attempt_digest"] = digest
    return validate_release_probe_attempt(
        attempt, expected_candidate_sha=canonical_candidate
    )


def load_release_probe_attempt(
    path: Path, *, expected_candidate_sha: str, assembled_at: str
) -> dict[str, object]:
    """Load one bounded attempt and bind it to the current candidate."""

    try:
        if path.stat().st_size > _MAX_ATTEMPT_BYTES:
            raise ValueError("release probe attempt is too large")
        payload = json.loads(path.read_text(encoding="utf-8"))
        _validate_structure(payload)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise ValueError("release probe attempt could not be read safely") from error
    checked = validate_release_probe_attempt(
        _mapping(payload, "release probe attempt"),
        expected_candidate_sha=expected_candidate_sha,
    )
    _validate_attempt_freshness(checked, assembled_at=assembled_at)
    return checked


def validate_release_probe_attempt(
    payload: Mapping[str, object], *, expected_candidate_sha: str
) -> dict[str, object]:
    """Validate exact attempt shape, provenance, and canonical digest."""

    _validate_structure(payload)
    _candidate_sha(expected_candidate_sha)
    if (
        set(payload) != _ATTEMPT_KEYS
        or payload.get("schema_version") != "1.0"
        or payload.get("artifact_kind") != "core_operation_release_probe_attempt"
        or payload.get("candidate_sha") != expected_candidate_sha
    ):
        raise ValueError("release probe attempt is invalid")
    verified_at = _timestamp(payload.get("verified_at"))
    attempt_digest = _digest(payload.get("attempt_digest"))
    samples = _sequence(payload.get("probe_samples"), "release probe samples")
    if not samples:
        raise ValueError("release probe attempt has no samples")
    checked_samples: list[dict[str, object]] = []
    for raw_sample in samples:
        sample = _mapping(raw_sample, "release probe sample")
        if set(sample) != _SAMPLE_KEYS:
            raise ValueError("release probe sample is invalid")
        if (
            sample.get("candidate_sha") != expected_candidate_sha
            or sample.get("verified_at") != verified_at
            or sample.get("attempt_digest") != attempt_digest
            or sample.get("probe_status") != "passed"
            or not isinstance(sample.get("attempted"), bool)
            or sample.get("source_result") not in _SOURCE_RESULTS
        ):
            raise ValueError("release probe sample provenance is invalid")
        for key in ("producer_operation_id", "probe", "request_profile"):
            _reference(sample.get(key), f"release probe sample {key}")
        checked_samples.append(deepcopy(dict(sample)))
    owners = [item["producer_operation_id"] for item in checked_samples]
    if len(owners) != len(set(owners)):
        raise ValueError("release probe sample producer is duplicated")
    checked = deepcopy(dict(payload))
    checked["probe_samples"] = checked_samples
    if _attempt_digest(checked) != attempt_digest:
        raise ValueError("release probe attempt digest is invalid")
    _sequence(checked.get("plan_snapshots"), "release plan snapshots")
    _sequence(checked.get("installation_loads"), "release installation loads")
    _mapping(checked.get("installation_health"), "installation health")
    return checked


def assemble_release_probe_evidence(
    attempts: Sequence[Mapping[str, object]],
    *,
    candidate_sha: str,
    assembled_at: str,
) -> dict[str, object]:
    """Close one final report from fresh evidence for the same candidate."""

    _candidate_sha(candidate_sha)
    canonical_assembled_at = _timestamp(assembled_at)
    if not attempts:
        raise ValueError("release probe attempt set is invalid")
    ordered = list(
        validate_release_probe_attempt_inputs(
            attempts,
            candidate_sha=candidate_sha,
            assembled_at=canonical_assembled_at,
        )
    )
    digests = [cast(str, item["attempt_digest"]) for item in ordered]
    installation_health = ordered[-1]["installation_health"]
    loads: dict[str, dict[str, object]] = {}
    for attempt in ordered:
        for raw_load in _sequence(
            attempt["installation_loads"], "release installation loads"
        ):
            load = deepcopy(dict(_mapping(raw_load, "release installation load")))
            owner = _reference(
                load.get("producer_operation_id"), "installation producer"
            )
            previous = loads.get(owner)
            if previous is not None and previous != load:
                raise ValueError("release probe installation load drifted")
            loads[owner] = load
    samples_by_owner: dict[str, list[dict[str, object]]] = {}
    for attempt in ordered:
        for raw_sample in cast(list[dict[str, object]], attempt["probe_samples"]):
            sample = deepcopy(raw_sample)
            owner = cast(str, sample["producer_operation_id"])
            samples_by_owner.setdefault(owner, []).append(sample)
    live_probes: list[dict[str, object]] = []
    for owner, samples in sorted(samples_by_owner.items()):
        attempted = [item for item in samples if item["attempted"] is True]
        if not attempted:
            raise ValueError(f"release probe producer was never attempted: {owner}")
        facts_by_time: dict[str, set[tuple[str, str, str, str]]] = {}
        for item in attempted:
            facts_by_time.setdefault(cast(str, item["verified_at"]), set()).add(
                (
                    cast(str, item["probe"]),
                    cast(str, item["probe_status"]),
                    cast(str, item["source_result"]),
                    cast(str, item["request_profile"]),
                )
            )
        if any(len(facts) != 1 for facts in facts_by_time.values()):
            raise ValueError(
                f"release probe producer has conflicting live facts: {owner}"
            )
        selected = max(
            attempted,
            key=lambda item: (
                datetime.fromisoformat(cast(str, item["verified_at"])),
                cast(str, item["attempt_digest"]),
                cast(str, item["request_profile"]),
            ),
        )
        live_probes.append(deepcopy(selected))
    snapshots = [
        {
            **deepcopy(dict(_mapping(raw, "release plan snapshot"))),
            "candidate_sha": attempt["candidate_sha"],
            "verified_at": attempt["verified_at"],
            "attempt_digest": attempt["attempt_digest"],
        }
        for attempt in ordered
        for raw in _sequence(attempt["plan_snapshots"], "release plan snapshots")
    ]
    return {
        "schema_version": "1.0",
        "candidate_sha": candidate_sha,
        "verified_at": canonical_assembled_at,
        "attempt_digests": sorted(digests),
        "installation_health": deepcopy(
            dict(cast(Mapping[str, object], installation_health))
        ),
        "plan_snapshots": snapshots,
        "installation_loads": [loads[owner] for owner in sorted(loads)],
        "live_probes": live_probes,
    }


def validate_release_probe_attempt_inputs(
    attempts: Sequence[Mapping[str, object]],
    *,
    candidate_sha: str,
    assembled_at: str,
) -> tuple[dict[str, object], ...]:
    """Validate reusable attempt inputs before any new public process runs."""

    _candidate_sha(candidate_sha)
    canonical_assembled_at = _timestamp(assembled_at)
    if len(attempts) > _MAX_ATTEMPTS:
        raise ValueError("release probe attempt set is invalid")
    checked = [
        validate_release_probe_attempt(attempt, expected_candidate_sha=candidate_sha)
        for attempt in attempts
    ]
    for attempt in checked:
        _validate_attempt_freshness(attempt, assembled_at=canonical_assembled_at)
    digests = [cast(str, item["attempt_digest"]) for item in checked]
    if len(digests) != len(set(digests)):
        raise ValueError("release probe attempt is duplicated")
    ordered = sorted(
        checked,
        key=lambda item: (
            datetime.fromisoformat(cast(str, item["verified_at"])),
            cast(str, item["attempt_digest"]),
        ),
    )
    if ordered:
        installation_health = ordered[-1]["installation_health"]
        if any(item["installation_health"] != installation_health for item in ordered):
            raise ValueError("release probe installation health drifted")
    loads: dict[str, dict[str, object]] = {}
    plan_contracts: dict[tuple[str, ...], object] = {}
    facts_by_owner_time: dict[tuple[str, datetime], set[tuple[str, str, str, str]]] = {}
    for attempt in ordered:
        for key, contract in _attempt_plan_contracts(attempt).items():
            previous_contract = plan_contracts.get(key)
            if previous_contract is not None and previous_contract != contract:
                raise ValueError("release probe plan contract drifted")
            plan_contracts[key] = contract
        for raw_load in _sequence(
            attempt["installation_loads"], "release installation loads"
        ):
            load = deepcopy(dict(_mapping(raw_load, "release installation load")))
            owner = _reference(
                load.get("producer_operation_id"), "installation producer"
            )
            previous = loads.get(owner)
            if previous is not None and previous != load:
                raise ValueError("release probe installation load drifted")
            loads[owner] = load
        for sample in cast(list[dict[str, object]], attempt["probe_samples"]):
            if sample["attempted"] is not True:
                continue
            owner = cast(str, sample["producer_operation_id"])
            evidence_time = datetime.fromisoformat(cast(str, sample["verified_at"]))
            facts_by_owner_time.setdefault((owner, evidence_time), set()).add(
                (
                    cast(str, sample["probe"]),
                    cast(str, sample["probe_status"]),
                    cast(str, sample["source_result"]),
                    cast(str, sample["request_profile"]),
                )
            )
    if any(len(facts) != 1 for facts in facts_by_owner_time.values()):
        raise ValueError("release probe producer has conflicting live facts")
    return tuple(ordered)


def _attempt_plan_contracts(
    attempt: Mapping[str, object],
) -> dict[tuple[str, ...], object]:
    """Project stable plan and disposition contracts from one attempt."""

    contracts: dict[tuple[str, ...], object] = {}
    for raw_snapshot in _sequence(
        attempt.get("plan_snapshots"), "release plan snapshots"
    ):
        snapshot = _mapping(raw_snapshot, "release plan snapshot")
        profile = _reference(snapshot.get("request_profile"), "release request profile")
        request_as_of = _request_as_of(snapshot.get("request_as_of"))
        result = _mapping(snapshot.get("result"), "release plan result")
        plan = _mapping(result.get("plan"), "release plan")
        stable_plan = _stable_plan_contract(
            contracts,
            profile=profile,
            scope="direct",
            plan=plan,
            coverage=_mapping(result.get("coverage"), "release plan coverage"),
            request_as_of=request_as_of,
        )
        _add_plan_contract(contracts, ("plan", profile), stable_plan)
        if plan.get("task_type") != "research_workflow":
            _add_direct_disposition_contracts(
                contracts,
                profile=profile,
                scope="direct",
                plan=stable_plan,
                coverage=_mapping(result.get("coverage"), "release plan coverage"),
            )
            continue
        registry_steps = _mapping(
            _mapping(
                result.get("capability_registry"), "release workflow registry"
            ).get("steps"),
            "release workflow registry steps",
        )
        coverage_steps = _mapping(
            _mapping(result.get("coverage"), "release workflow coverage").get("steps"),
            "release workflow coverage steps",
        )
        for raw_step in _sequence(plan.get("steps"), "release workflow plan steps"):
            step = _mapping(raw_step, "release workflow plan step")
            step_id = _reference(step.get("step_id"), "release workflow step id")
            registered = _mapping(
                registry_steps.get(step_id), "release workflow registered step"
            )
            leaf_plan_value = registered.get("leaf_plan")
            if leaf_plan_value is None:
                continue
            leaf_plan = _mapping(leaf_plan_value, "release workflow leaf plan")
            covered = _mapping(
                coverage_steps.get(step_id), "release workflow covered step"
            )
            leaf_coverage = _mapping(
                covered.get("leaf"), "release workflow leaf coverage"
            )
            stable_leaf_plan = _stable_plan_contract(
                contracts,
                profile=profile,
                scope=step_id,
                plan=leaf_plan,
                coverage=leaf_coverage,
                request_as_of=request_as_of,
            )
            leaf_key = ("leaf_plan", profile, step_id)
            _add_plan_contract(contracts, leaf_key, stable_leaf_plan)
            _add_direct_disposition_contracts(
                contracts,
                profile=profile,
                scope=step_id,
                plan=stable_leaf_plan,
                coverage=leaf_coverage,
            )
    return contracts


class _NoIoIdentityTransport:
    def get(self, url: str, headers: dict[str, str]) -> NoReturn:
        raise AssertionError("release attempt authority construction performed I/O")


def _stable_plan_contract(
    contracts: dict[tuple[str, ...], object],
    *,
    profile: str,
    scope: str,
    plan: Mapping[str, object],
    coverage: Mapping[str, object],
    request_as_of: str,
) -> dict[str, object]:
    """Separate owner-valid runtime applications from one stable plan declaration."""

    copied = deepcopy(dict(plan))
    if plan.get("task_type") == "research_workflow":
        _validate_workflow_plan_instance(
            profile=profile,
            plan=plan,
            request_as_of=request_as_of,
        )
        copied.pop("id")
        return copied
    if plan.get("task_type") != "market_signals":
        return copied
    capabilities = [
        _reference(item, "release capability id")
        for item in _sequence(plan.get("capabilities"), "release plan capabilities")
    ]
    from a_share_research.market_signals import (
        is_monitoring_identity_capability_id,
        is_monitoring_identity_consumer_id,
        monitoring_identity_application_provider_code,
    )

    dynamic_capabilities = {
        capability_id
        for capability_id in capabilities
        if is_monitoring_identity_capability_id(capability_id)
    }
    if not dynamic_capabilities:
        return copied
    if sum(item in dynamic_capabilities for item in capabilities) != len(
        dynamic_capabilities
    ):
        raise ValueError("release probe monitoring identity application is invalid")

    from a_share_research.research_result import stable_result_id

    obligations = [
        _mapping(item, "release plan obligation")
        for item in _sequence(
            plan.get("operation_obligations"), "release plan obligations"
        )
    ]
    dependencies = [
        _mapping(item, "release research dependency")
        for item in _sequence(
            plan.get("research_dependencies"), "release research dependencies"
        )
    ]
    matched_dependencies: dict[str, tuple[str, Mapping[str, object]]] = {}
    for dependency in dependencies:
        consumer_id = _reference(
            dependency.get("consumer_id"), "release dependency consumer"
        )
        if not is_monitoring_identity_consumer_id(consumer_id):
            continue
        matches: list[tuple[str, str]] = []
        for capability_id in dynamic_capabilities:
            try:
                provider_code = monitoring_identity_application_provider_code(
                    capability_id, consumer_id
                )
            except ValueError:
                continue
            matches.append((capability_id, provider_code))
        if len(matches) != 1 or matches[0][0] in matched_dependencies:
            raise ValueError("release probe monitoring identity application is invalid")
        matched_dependencies[matches[0][0]] = (matches[0][1], dependency)
    if set(matched_dependencies) != dynamic_capabilities:
        raise ValueError("release probe monitoring identity application is invalid")

    _validate_sanitized_plan_id(plan, coverage)
    for capability_id in sorted(dynamic_capabilities):
        provider_code, owned_dependency = matched_dependencies[capability_id]
        application = _monitoring_identity_application_contract(
            capability_id=capability_id,
            provider_code=provider_code,
            dependency=owned_dependency,
            obligations=obligations,
            coverage=coverage,
        )
        _add_plan_contract(
            contracts,
            ("monitoring_identity_application", profile, scope, provider_code),
            application,
        )

    stable_obligations = [
        deepcopy(dict(obligation))
        for obligation in obligations
        if obligation.get("capability_id") not in dynamic_capabilities
    ]
    dynamic_consumers = {
        cast(str, dependency[1]["consumer_id"])
        for dependency in matched_dependencies.values()
    }
    stable_dependencies = [
        deepcopy(dict(dependency))
        for dependency in dependencies
        if dependency.get("consumer_id") not in dynamic_consumers
    ]
    stable_capabilities = [
        capability_id
        for capability_id in capabilities
        if capability_id not in dynamic_capabilities
    ]
    copied["capabilities"] = stable_capabilities
    copied["operation_obligations"] = stable_obligations
    copied["research_dependencies"] = stable_dependencies
    copied["id"] = stable_result_id(
        "plan",
        _sanitized_plan_identity(
            copied,
            coverage,
            obligations=stable_obligations,
            dependencies=stable_dependencies,
        ),
    )
    return copied


def _validate_workflow_plan_instance(
    *, profile: str, plan: Mapping[str, object], request_as_of: str
) -> None:
    from a_share_research.research_result import stable_result_id

    from tests.release_probe_core_operations import build_release_request_profiles

    if set(plan) != {"id", "version", "task_type", "workflow_id", "steps"}:
        raise ValueError("release workflow plan contract is invalid")
    plan_id = plan.get("id")
    if not isinstance(plan_id, str) or _PLAN_ID.fullmatch(plan_id) is None:
        raise ValueError("release workflow plan ID is invalid")
    configured = {
        item.name: item
        for item in build_release_request_profiles(
            as_of=request_as_of, replay_date=request_as_of
        )
    }.get(profile)
    if configured is None or configured.request.get("task_type") != "research_workflow":
        raise ValueError("release workflow plan profile is invalid")
    expected = stable_result_id(
        "plan",
        {
            "version": plan.get("version"),
            "task_type": plan.get("task_type"),
            "workflow_id": plan.get("workflow_id"),
            "request_scope": deepcopy(dict(configured.request)),
            "steps": deepcopy(plan.get("steps")),
        },
    )
    if plan_id != expected:
        raise ValueError("release workflow plan ID contradicts its request profile")


def _validate_sanitized_plan_id(
    plan: Mapping[str, object], coverage: Mapping[str, object]
) -> None:
    from a_share_research.research_result import stable_result_id

    expected = stable_result_id("plan", _sanitized_plan_identity(plan, coverage))
    if plan.get("id") != expected:
        raise ValueError("release probe plan ID contradicts its contracts")


def _sanitized_plan_identity(
    plan: Mapping[str, object],
    coverage: Mapping[str, object],
    *,
    obligations: Sequence[Mapping[str, object]] | None = None,
    dependencies: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    owned_obligations = list(
        obligations
        if obligations is not None
        else [
            _mapping(item, "release plan obligation")
            for item in _sequence(
                plan.get("operation_obligations"), "release plan obligations"
            )
        ]
    )
    owned_dependencies = list(
        dependencies
        if dependencies is not None
        else [
            _mapping(item, "release research dependency")
            for item in _sequence(
                plan.get("research_dependencies"), "release research dependencies"
            )
        ]
    )
    disposition_contracts: list[dict[str, object]] = []
    for obligation in owned_obligations:
        capability_id = _reference(
            obligation.get("capability_id"), "release capability id"
        )
        operation_id = _reference(
            obligation.get("operation_id"), "release operation id"
        )
        operation = _mapping(
            _mapping(
                _mapping(
                    coverage.get(capability_id), "release capability coverage"
                ).get("operations"),
                "release operation coverage",
            ).get(operation_id),
            "release owned operation coverage",
        )
        disposition_contracts.append(
            {
                "capability_id": capability_id,
                "operation_id": operation_id,
                "fields": deepcopy(
                    list(
                        _sequence(
                            operation.get("field_dispositions"),
                            "release field dispositions",
                        )
                    )
                ),
            }
        )
    identity: dict[str, object] = {
        "version": plan.get("version"),
        "task_type": plan.get("task_type"),
        "operation_obligations": [
            deepcopy(dict(obligation)) for obligation in owned_obligations
        ],
        "research_dependencies": [
            deepcopy(dict(dependency)) for dependency in owned_dependencies
        ],
        "field_dispositions": disposition_contracts,
    }
    if plan.get("evidence_storage") is not None:
        identity["evidence_storage"] = deepcopy(plan["evidence_storage"])
    return identity


def _monitoring_identity_application_contract(
    *,
    capability_id: str,
    provider_code: str,
    dependency: Mapping[str, object],
    obligations: Sequence[Mapping[str, object]],
    coverage: Mapping[str, object],
) -> dict[str, object]:
    from a_share_research.identity_sources import build_default_identity_operations
    from a_share_research.market_signals import monitoring_identity_application

    adapters = build_default_identity_operations(_NoIoIdentityTransport())
    expected_capability, expected_consumer = monitoring_identity_application(
        provider_code
    )
    expected_obligations: dict[str, dict[str, object]] = {}
    expected_dispositions: dict[str, list[dict[str, object]]] = {}
    for adapter in adapters:
        obligation = adapter.obligation.to_dict()
        obligation["capability_id"] = capability_id
        operation_id = cast(str, obligation["operation_id"])
        expected_obligations[operation_id] = obligation
        expected_dispositions[operation_id] = [
            item.to_dict() for item in adapter.field_dispositions
        ]
    owned_obligations = [
        obligation
        for obligation in obligations
        if obligation.get("capability_id") == capability_id
    ]
    actual_obligations = {
        _reference(obligation.get("operation_id"), "release operation id"): deepcopy(
            dict(obligation)
        )
        for obligation in owned_obligations
    }
    aggregate = _mapping(
        coverage.get(capability_id), "release monitoring identity coverage"
    )
    operations = _mapping(
        aggregate.get("operations"), "release monitoring identity operations"
    )
    if (
        len(owned_obligations) != len(expected_obligations)
        or capability_id != expected_capability
        or actual_obligations != expected_obligations
        or set(operations) != set(expected_obligations)
        or dict(dependency)
        != {
            "consumer_id": expected_consumer,
            "evidence_use": "security_identity",
            "minimum_supporting_observations": 1,
            "core": True,
        }
    ):
        raise ValueError("release probe monitoring identity application is invalid")
    for operation_id, expected in expected_dispositions.items():
        operation = _mapping(
            operations.get(operation_id), "release monitoring identity operation"
        )
        if (
            operation.get("capability_id") != capability_id
            or operation.get("operation_id") != operation_id
        ):
            raise ValueError("release probe monitoring identity application is invalid")
        actual = list(
            _sequence(operation.get("field_dispositions"), "release field dispositions")
        )
        if actual != expected:
            raise ValueError("release probe monitoring identity application is invalid")
    return {
        "capability_id": capability_id,
        "provider_code": provider_code,
        "operation_obligations": [
            expected_obligations[operation_id]
            for operation_id in sorted(expected_obligations)
        ],
        "research_dependency": deepcopy(dict(dependency)),
        "field_dispositions": {
            operation_id: expected_dispositions[operation_id]
            for operation_id in sorted(expected_dispositions)
        },
    }


def _request_as_of(value: object) -> str:
    from a_share_research.operation_release_matrix import canonical_release_as_of

    return canonical_release_as_of(value, "release plan request as_of")


def _add_direct_disposition_contracts(
    contracts: dict[tuple[str, ...], object],
    *,
    profile: str,
    scope: str,
    plan: Mapping[str, object],
    coverage: Mapping[str, object],
) -> None:
    for raw_obligation in _sequence(
        plan.get("operation_obligations"), "release plan obligations"
    ):
        obligation = _mapping(raw_obligation, "release plan obligation")
        capability_id = _reference(
            obligation.get("capability_id"), "release capability id"
        )
        operation_id = _reference(
            obligation.get("operation_id"), "release operation id"
        )
        capability = _mapping(
            coverage.get(capability_id), "release capability coverage"
        )
        operations = _mapping(
            capability.get("operations"), "release operation coverage"
        )
        operation = _mapping(
            operations.get(operation_id), "release owned operation coverage"
        )
        dispositions = deepcopy(
            list(
                _sequence(
                    operation.get("field_dispositions"),
                    "release field dispositions",
                )
            )
        )
        _add_plan_contract(
            contracts,
            ("field_dispositions", profile, scope, capability_id, operation_id),
            dispositions,
        )


def _add_plan_contract(
    contracts: dict[tuple[str, ...], object],
    key: tuple[str, ...],
    value: object,
) -> None:
    if key in contracts:
        raise ValueError("release probe plan contract is duplicated")
    contracts[key] = value


def attempt_json_bytes(payload: Mapping[str, object]) -> bytes:
    """Return stable bytes after validating the attempt's own digest."""

    candidate_sha = _candidate_sha(payload.get("candidate_sha"))
    checked = validate_release_probe_attempt(
        payload, expected_candidate_sha=candidate_sha
    )
    return (
        json.dumps(checked, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _attempt_digest(payload: Mapping[str, object]) -> str:
    core = deepcopy(dict(payload))
    core.pop("attempt_digest", None)
    core["probe_samples"] = [
        {
            key: deepcopy(value)
            for key, value in _mapping(raw_sample, "release probe sample").items()
            if key != "attempt_digest"
        }
        for raw_sample in _sequence(core.get("probe_samples"), "release probe samples")
    ]
    return sha256(
        json.dumps(
            core,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _candidate_sha(value: object) -> str:
    if not isinstance(value, str) or _CANDIDATE_SHA.fullmatch(value) is None:
        raise ValueError("release candidate SHA is invalid")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("release attempt digest is invalid")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("release evidence timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("release evidence timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("release evidence timestamp requires a timezone")
    canonical = parsed.isoformat()
    if canonical != value:
        raise ValueError("release evidence timestamp is not canonical")
    return canonical


def _validate_attempt_freshness(
    attempt: Mapping[str, object], *, assembled_at: str
) -> None:
    attempt_time = datetime.fromisoformat(_timestamp(attempt.get("verified_at")))
    assembly_time = datetime.fromisoformat(_timestamp(assembled_at))
    age = assembly_time - attempt_time
    if age < timedelta(0) or age > _MAX_ATTEMPT_AGE:
        raise ValueError("release probe attempt is stale")


def _reference(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or ".." in value.split("/")
        or "://" in value
    ):
        raise ValueError(f"{label} is unsafe")
    return value


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _validate_structure(value: object) -> None:
    """Bound recursive JSON before deepcopy or canonical serialization."""

    stack: list[tuple[object, int]] = [(value, 0)]
    item_count = 0
    while stack:
        item, depth = stack.pop()
        item_count += 1
        if depth > _MAX_STRUCTURE_DEPTH or item_count > _MAX_STRUCTURE_ITEMS:
            raise ValueError("release probe attempt exceeds structural bounds")
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 128
                    or any(ord(character) < 32 for character in key)
                ):
                    raise ValueError("release probe attempt key is invalid")
                stack.append((nested, depth + 1))
        elif isinstance(item, list):
            stack.extend((nested, depth + 1) for nested in item)
        elif isinstance(item, str):
            if len(item) > _MAX_STRING_LENGTH:
                raise ValueError("release probe attempt string exceeds its bound")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("release probe attempt number is invalid")
        elif item is not None and not isinstance(item, (bool, int)):
            raise ValueError("release probe attempt contains a non-JSON value")
