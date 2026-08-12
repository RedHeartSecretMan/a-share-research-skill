#!/usr/bin/env python3
"""Regenerate or byte-check the core-v0.3.0 operation release matrix."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Mapping, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from a_share_research.operation_release_matrix import (  # noqa: E402
    OperationQualificationEvidence,
    build_operation_release_matrix,
    formal_producer_operation_ids,
    load_operation_qualification_manifest,
    load_release_probe_evidence,
)

DEFAULT_QUALIFICATION = (
    REPOSITORY_ROOT / "docs" / "research" / "core-v0.3.0-operation-qualification.json"
)
DEFAULT_RELEASE_EVIDENCE = (
    REPOSITORY_ROOT / "docs" / "research" / "core-v0.3.0-release-probe-evidence.json"
)
DEFAULT_MATRIX = (
    REPOSITORY_ROOT / "docs" / "research" / "core-v0.3.0-operation-release-matrix.json"
)


def _json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} is invalid")
    return cast(Mapping[str, object], value)


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
        if separator and path.suffix == ".py" and anchor.endswith("Operation"):
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


def render_matrix_bytes(
    *, qualification_path: Path, release_evidence_path: Path
) -> bytes:
    release_payload = _json_object(release_evidence_path, "release probe evidence")
    snapshots, installation_loads, installation_health, live_probes = (
        load_release_probe_evidence(release_payload)
    )
    denominator = formal_producer_operation_ids(snapshots)
    qualification = load_operation_qualification_manifest(
        _json_object(qualification_path, "operation qualification manifest"),
        expected_producer_operation_ids=denominator,
    )
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
