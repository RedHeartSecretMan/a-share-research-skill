#!/usr/bin/env python3
"""Regenerate or byte-check the v0.3.0 operation release matrix."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Mapping, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from a_share_research.operation_release_matrix import (  # noqa: E402
    OperationQualificationEvidence,
    ReleasePlanSnapshot,
    build_operation_release_matrix,
    formal_operation_applications,
    formal_producer_operation_ids,
    load_operation_qualification_manifest,
    load_release_probe_evidence,
)

DEFAULT_QUALIFICATION = (
    REPOSITORY_ROOT / "docs" / "research" / "v0.3.0-operation-qualification.json"
)
DEFAULT_RELEASE_EVIDENCE = (
    REPOSITORY_ROOT / "docs" / "research" / "v0.3.0-release-probe-evidence.json"
)
DEFAULT_MATRIX = (
    REPOSITORY_ROOT / "docs" / "research" / "v0.3.0-operation-release-matrix.json"
)
_V030_RELEASE_EVIDENCE_SHA256 = (
    "e7213c5316c01ffe702ed1b4c1ce6ca7447d014151488ee95d553a59dc6cbafd"
)
_V030_CANDIDATE_SHA = "7acb445cdccb9f8311d0a1d094f195eb0d7229e2"
_V030_VERIFIED_AT = "2026-08-18T11:24:17+08:00"
_V030_ATTEMPT_DIGEST = (
    "5fa0716b4a3b55d695c4e1fe443554b4db0a065ecdab764f0c800dcc3bcce00e"
)
_RELEASE_SOURCE_RESULTS = {
    "complete",
    "observed_empty",
    "partial",
    "credential_blocked",
    "unavailable",
}


def _json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} is invalid")
    return cast(Mapping[str, object], value)


def _legacy_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} is invalid")
    return cast(dict[str, object], value)


def _legacy_sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} is invalid")
    return value


def _legacy_plan_views(
    result: dict[str, object],
) -> tuple[tuple[dict[str, object], dict[str, object]], ...]:
    plan = _legacy_mapping(result.get("plan"), "legacy release plan")
    coverage = _legacy_mapping(result.get("coverage"), "legacy release coverage")
    if plan.get("task_type") != "research_workflow":
        return ((plan, coverage),)
    registry = _legacy_mapping(
        result.get("capability_registry"), "legacy workflow registry"
    )
    registry_steps = _legacy_mapping(
        registry.get("steps"), "legacy workflow registry steps"
    )
    coverage_steps = _legacy_mapping(
        coverage.get("steps"), "legacy workflow coverage steps"
    )
    views: list[tuple[dict[str, object], dict[str, object]]] = []
    for raw_step in _legacy_sequence(plan.get("steps"), "legacy workflow steps"):
        step = _legacy_mapping(raw_step, "legacy workflow step")
        step_id = step.get("step_id")
        if not isinstance(step_id, str):
            raise ValueError("legacy workflow step is invalid")
        registered = _legacy_mapping(
            registry_steps.get(step_id), "legacy workflow registered step"
        )
        leaf_plan = registered.get("leaf_plan")
        covered = _legacy_mapping(
            coverage_steps.get(step_id), "legacy workflow covered step"
        )
        leaf_coverage = covered.get("leaf")
        if leaf_plan is None:
            if leaf_coverage != {}:
                raise ValueError("legacy workflow skipped leaf is invalid")
            continue
        views.append(
            (
                _legacy_mapping(leaf_plan, "legacy workflow leaf plan"),
                _legacy_mapping(leaf_coverage, "legacy workflow leaf coverage"),
            )
        )
    return tuple(views)


def _legacy_operation(
    coverage: dict[str, object], capability_id: object, operation_id: object
) -> dict[str, object]:
    if not isinstance(capability_id, str) or not isinstance(operation_id, str):
        raise ValueError("legacy release operation owner is invalid")
    capability = _legacy_mapping(
        coverage.get(capability_id), "legacy release capability"
    )
    operations = _legacy_mapping(
        capability.get("operations"), "legacy release operations"
    )
    return _legacy_mapping(
        operations.get(operation_id), "legacy release owned operation"
    )


def _has_v030_release_evidence_identity(payload: Mapping[str, object]) -> bool:
    return (
        payload.get("schema_version") == "1.0"
        and payload.get("candidate_sha") == _V030_CANDIDATE_SHA
        and payload.get("verified_at") == _V030_VERIFIED_AT
        and payload.get("attempt_digests") == [_V030_ATTEMPT_DIGEST]
    )


def _adapt_v030_release_evidence(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Adapt only the frozen v0.3 live lineage without mutating history."""

    if not _has_v030_release_evidence_identity(payload):
        raise ValueError("legacy release evidence identity is unsupported")
    adapted = deepcopy(dict(payload))
    snapshots = [
        _legacy_mapping(item, "legacy release snapshot")
        for item in _legacy_sequence(
            adapted.get("plan_snapshots"), "legacy release snapshots"
        )
    ]
    views_by_snapshot: dict[
        int, tuple[tuple[dict[str, object], dict[str, object]], ...]
    ] = {}
    for snapshot in snapshots:
        result = _legacy_mapping(snapshot.get("result"), "legacy release result")
        views = _legacy_plan_views(result)
        views_by_snapshot[id(snapshot)] = views
        for _, coverage in views:
            for raw_capability in coverage.values():
                capability = _legacy_mapping(
                    raw_capability, "legacy release capability"
                )
                operations = _legacy_mapping(
                    capability.get("operations"), "legacy release operations"
                )
                for raw_operation in operations.values():
                    operation = _legacy_mapping(
                        raw_operation, "legacy release operation"
                    )
                    if "source_error_codes" in operation:
                        raise ValueError("legacy release operation was already adapted")
                    operation["source_error_codes"] = []

    live_probes = [
        _legacy_mapping(item, "legacy live probe")
        for item in _legacy_sequence(adapted.get("live_probes"), "legacy live probes")
    ]
    if any(
        item.get("source_result") not in _RELEASE_SOURCE_RESULTS for item in live_probes
    ):
        raise ValueError("legacy live probe classification is unsupported")
    credential_probes = [
        item
        for item in live_probes
        if item.get("source_result") == "credential_blocked"
    ]
    credential_keys = [
        (
            item.get("producer_operation_id"),
            item.get("request_profile"),
            item.get("candidate_sha"),
            item.get("verified_at"),
            item.get("attempt_digest"),
        )
        for item in credential_probes
    ]
    if len(credential_keys) != len(set(credential_keys)):
        raise ValueError("legacy credential probe is duplicated")

    provenance_keys = (
        "request_profile",
        "candidate_sha",
        "verified_at",
        "attempt_digest",
    )
    for probe in credential_probes:
        matching_snapshots = [
            snapshot
            for snapshot in snapshots
            if all(snapshot.get(key) == probe.get(key) for key in provenance_keys)
        ]
        if len(matching_snapshots) != 1:
            raise ValueError("legacy credential provenance is ambiguous")
        snapshot = matching_snapshots[0]
        matching_operations: list[dict[str, object]] = []
        for plan, coverage in views_by_snapshot[id(snapshot)]:
            for raw_obligation in _legacy_sequence(
                plan.get("operation_obligations"), "legacy release obligations"
            ):
                obligation = _legacy_mapping(
                    raw_obligation, "legacy release obligation"
                )
                if obligation.get(
                    "operation_kind"
                ) != "external_source" or obligation.get(
                    "producer_operation_id"
                ) != probe.get("producer_operation_id"):
                    continue
                matching_operations.append(
                    _legacy_operation(
                        coverage,
                        obligation.get("capability_id"),
                        obligation.get("operation_id"),
                    )
                )
        if len(matching_operations) != 1:
            raise ValueError("legacy credential operation is ambiguous")
        operation = matching_operations[0]
        if (
            operation.get("execution_state") != "failed"
            or operation.get("state") != "failed"
            or operation.get("query_complete") is not None
        ):
            raise ValueError("legacy credential operation did not fail")
        operation["source_error_codes"] = ["missing_credential"]
    return adapted


def _load_v030_release_evidence(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != _V030_RELEASE_EVIDENCE_SHA256:
            raise ValueError("legacy release evidence bytes are unsupported")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("legacy release evidence is unreadable") from error
    return _adapt_v030_release_evidence(
        _legacy_mapping(payload, "legacy release evidence")
    )


def _python_tree(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ValueError("qualification evidence reference is unreadable") from error


def _python_symbols(path: Path) -> set[str]:
    tree = _python_tree(path)
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _reference_exists(reference: str) -> None:
    path_text, separator, anchor = reference.partition("#")
    if not separator or not anchor:
        raise ValueError("qualification evidence reference requires an anchor")
    path = REPOSITORY_ROOT / path_text
    try:
        path.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise ValueError(
            "qualification evidence reference escaped the repository"
        ) from error
    if not path.is_file():
        raise ValueError("qualification evidence reference file is missing")
    if path.suffix == ".py":
        if anchor not in _python_symbols(path):
            raise ValueError("qualification evidence reference symbol is missing")
        return
    if path.suffix == ".md":
        headings = [
            line.lstrip("#").strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]
        if not any(anchor.casefold() in heading.casefold() for heading in headings):
            raise ValueError("qualification evidence reference heading is missing")
        return
    raise ValueError("qualification evidence reference type is unsupported")


def _owner_operation_symbol(item: OperationQualificationEvidence) -> str:
    source_root = SCRIPTS_ROOT / "a_share_research"
    candidates: set[str] = set()
    for reference in item.evidence_references:
        path_text, separator, anchor = reference.partition("#")
        path = REPOSITORY_ROOT / path_text
        try:
            path.relative_to(source_root)
        except ValueError:
            continue
        declared_adapters = {
            "market_session_context@1": (
                "market_session_sources.py",
                "ProductionMarketSessionAdapter",
            ),
            "fixed_four_indices@1": (
                "fixed_index_sources.py",
                "ProductionFixedIndexAdapter",
            ),
        }
        if (
            separator
            and path.suffix == ".py"
            and (
                anchor.endswith("Operation")
                or (path.name, anchor)
                == declared_adapters.get(item.producer_operation_id)
            )
        ):
            candidates.add(anchor)
    if len(candidates) != 1:
        raise ValueError("qualification owner operation reference is invalid")
    return candidates.pop()


def _selector_module_binds_owner(item: OperationQualificationEvidence) -> bool:
    path_text, _, anchor = item.offline_contract_selector.partition("#")
    tree = _python_tree(REPOSITORY_ROOT / path_text)
    matching_anchors = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == anchor
    ]
    if len(matching_anchors) != 1:
        return False
    if item.source_contract is not None:
        fixture_reference = (
            "skill/a-share-research/scripts/a_share_research/"
            "operation_release_matrix.py#"
            "load_market_foundation_qualification_fixtures"
        )
        operation_bound = any(
            isinstance(node, ast.Constant) and node.value == item.producer_operation_id
            for node in ast.walk(matching_anchors[0])
        )
        installed_validator_used = any(
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "load_market_foundation_qualification_fixtures"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr
                    == "load_market_foundation_qualification_fixtures"
                )
            )
            for node in ast.walk(tree)
        )
        return (
            operation_bound
            and installed_validator_used
            and fixture_reference in item.evidence_references
        )
    owner_symbol = _owner_operation_symbol(item)
    return any(
        (isinstance(node, ast.Constant) and node.value == item.producer_operation_id)
        or (isinstance(node, ast.Name) and node.id == owner_symbol)
        or (isinstance(node, ast.Attribute) and node.attr == owner_symbol)
        for node in ast.walk(matching_anchors[0])
    )


def validate_evidence_references(
    qualification: tuple[OperationQualificationEvidence, ...],
) -> None:
    """Fail if a reviewed selector no longer resolves to its repo owner."""

    unbound_producers: list[str] = []
    for item in qualification:
        _reference_exists(item.offline_contract_selector)
        for reference in item.evidence_references:
            _reference_exists(reference)
        if not _selector_module_binds_owner(item):
            unbound_producers.append(item.producer_operation_id)
    if unbound_producers:
        raise ValueError(
            "qualification offline selectors do not bind producers: "
            + ", ".join(sorted(unbound_producers))
        )


def _adapt_v030_qualification_authority(
    qualification: tuple[OperationQualificationEvidence, ...],
    plan_snapshots: tuple[ReleasePlanSnapshot, ...],
) -> tuple[OperationQualificationEvidence, ...]:
    """Enrich only frozen v0.3 qualifications before the strict builder seam."""

    capability_ids_by_producer: dict[str, list[str]] = {}
    for capability_id, producer_operation_id in formal_operation_applications(
        plan_snapshots
    ):
        capability_ids_by_producer.setdefault(producer_operation_id, []).append(
            capability_id
        )
    if set(capability_ids_by_producer) != {
        item.producer_operation_id for item in qualification
    }:
        raise ValueError("legacy qualification authority does not close the plan")
    return tuple(
        replace(
            item,
            capability_ids=tuple(
                capability_ids_by_producer[item.producer_operation_id]
            ),
        )
        for item in qualification
    )


def render_matrix_bytes(
    *, qualification_path: Path, release_evidence_path: Path
) -> bytes:
    release_payload = _json_object(release_evidence_path, "release probe evidence")
    is_v030_release_evidence = _has_v030_release_evidence_identity(release_payload)
    if is_v030_release_evidence:
        release_payload = _load_v030_release_evidence(release_evidence_path)
    snapshots, installation_loads, installation_health, live_probes = (
        load_release_probe_evidence(release_payload)
    )
    denominator = formal_producer_operation_ids(snapshots)
    qualification = load_operation_qualification_manifest(
        _json_object(qualification_path, "operation qualification manifest"),
        expected_producer_operation_ids=denominator,
    )
    if is_v030_release_evidence:
        qualification = _adapt_v030_qualification_authority(qualification, snapshots)
    validate_evidence_references(qualification)
    return build_operation_release_matrix(
        plan_snapshots=snapshots,
        qualification_evidence=qualification,
        installation_loads=installation_loads,
        installation_health=installation_health,
        live_probes=live_probes,
    ).to_json_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or byte-check the plan-derived core operation matrix."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)
    parser.add_argument(
        "--release-evidence", type=Path, default=DEFAULT_RELEASE_EVIDENCE
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_MATRIX)
    arguments = parser.parse_args(argv)
    try:
        rendered = render_matrix_bytes(
            qualification_path=arguments.qualification,
            release_evidence_path=arguments.release_evidence,
        )
        if arguments.check:
            if arguments.output.read_bytes() != rendered:
                raise ValueError("operation release matrix is stale")
        else:
            arguments.output.write_bytes(rendered)
    except (OSError, ValueError):
        sys.stderr.write("error: core operation release matrix generation failed\n")
        return 2
    payload = cast(dict[str, object], json.loads(rendered))
    summary = {
        "schema_version": "1.0",
        "status": "current" if arguments.check else "written",
        "operation_count": cast(dict[str, object], payload["denominator"])[
            "operation_count"
        ],
        "output": arguments.output.as_posix(),
        "sha256": sha256(rendered).hexdigest(),
    }
    sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
