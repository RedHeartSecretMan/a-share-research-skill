from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.operation_release_matrix import (  # noqa: E402
    formal_operation_applications,
    load_operation_qualification_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPOSITORY_ROOT / "tests" / "generate_core_operation_release_matrix.py"
SPEC = importlib.util.spec_from_file_location(
    "generate_core_operation_release_matrix", GENERATOR_PATH
)
assert SPEC is not None and SPEC.loader is not None
generator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generator
SPEC.loader.exec_module(generator)


def _operation_values(value: object) -> list[dict[str, object]]:
    operations: list[dict[str, object]] = []
    if isinstance(value, dict):
        if {
            "operation_id",
            "execution_state",
            "state",
            "query_complete",
        } <= set(value):
            operations.append(value)
        for item in value.values():
            operations.extend(_operation_values(item))
    elif isinstance(value, list):
        for item in value:
            operations.extend(_operation_values(item))
    return operations


def _v030_payload() -> dict[str, object]:
    return json.loads(generator.DEFAULT_RELEASE_EVIDENCE.read_text(encoding="utf-8"))


def _credential_probe(payload: dict[str, object]) -> dict[str, object]:
    return next(
        item
        for item in payload["live_probes"]
        if item["source_result"] == "credential_blocked"
    )


def _matching_snapshot(
    payload: dict[str, object], probe: dict[str, object]
) -> dict[str, object]:
    keys = (
        "request_profile",
        "candidate_sha",
        "verified_at",
        "attempt_digest",
    )
    return next(
        snapshot
        for snapshot in payload["plan_snapshots"]
        if all(snapshot[key] == probe[key] for key in keys)
    )


class CoreOperationMatrixGeneratorTests(unittest.TestCase):
    def test_issue_114_v040_source_contract_references_resolve_without_production_owner(
        self,
    ) -> None:
        path = (
            REPOSITORY_ROOT
            / "docs"
            / "research"
            / "v0.4.0-operation-qualification.json"
        )
        manifest = json.loads(path.read_text(encoding="utf-8"))
        producer_ids = tuple(
            item["producer_operation_id"] for item in manifest["operations"]
        )
        qualification = load_operation_qualification_manifest(
            manifest,
            expected_producer_operation_ids=producer_ids,
        )

        generator.validate_evidence_references(qualification)

        self.assertEqual(len(qualification), 50)
        self.assertEqual(
            sum(item.source_contract is not None for item in qualification), 6
        )

    def test_current_52_owner_evidence_uses_accepted_normalization_fixtures(self) -> None:
        from tests.test_release_probe_core_operations import (
            _reviewed_application_attempt,
            release_probe,
        )

        qualification_path = (
            REPOSITORY_ROOT
            / "docs/research/v0.4.0-overview-v2-operation-qualification.json"
        )
        manifest = json.loads(qualification_path.read_text(encoding="utf-8"))
        producers = {item["producer_operation_id"] for item in manifest["operations"]}
        attempt = _reviewed_application_attempt(
            candidate_sha="a" * 40,
            verified_at="2026-10-16T14:00:00+08:00",
            attempted_producers=producers,
        )
        report = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha="a" * 40,
            assembled_at="2026-10-16T14:00:00+08:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "final-evidence.json"
            evidence_path.write_text(json.dumps(report), encoding="utf-8")
            matrix = json.loads(
                generator.render_matrix_bytes(
                    qualification_path=qualification_path,
                    release_evidence_path=evidence_path,
                )
            )

        self.assertEqual(matrix["denominator"]["operation_count"], 52)
        self.assertEqual(
            {item["producer_operation_id"] for item in matrix["operations"]},
            producers,
        )
        self.assertTrue(
            all(
                item["live_probe"]["source_result"] == "unavailable"
                for item in matrix["operations"]
            )
        )

    def test_v030_legacy_adapter_preserves_input_and_adds_exact_codes(self) -> None:
        payload = _v030_payload()
        original = deepcopy(payload)

        adapted = generator._adapt_v030_release_evidence(payload)

        self.assertEqual(payload, original)
        operations = _operation_values(adapted)
        self.assertTrue(operations)
        self.assertTrue(all("source_error_codes" in item for item in operations))
        credential_operations = [
            item for item in operations if item["source_error_codes"]
        ]
        self.assertEqual(
            [
                (item["operation_id"], item["source_error_codes"])
                for item in credential_operations
            ],
            [("iwencai_content_search@1", ["missing_credential"])],
        )

    def test_v030_legacy_adapter_rejects_ambiguous_or_unsupported_facts(
        self,
    ) -> None:
        for name in (
            "duplicate-fact",
            "missing-owner",
            "provenance-mismatch",
            "non-failed-owner",
            "unsupported-classification",
            "ambiguous-snapshot",
        ):
            with self.subTest(name=name):
                payload = _v030_payload()
                probe = _credential_probe(payload)
                snapshot = _matching_snapshot(payload, probe)
                if name == "duplicate-fact":
                    payload["live_probes"].append(deepcopy(probe))
                elif name == "missing-owner":
                    probe["producer_operation_id"] = "unknown_legacy_source@1"
                elif name == "provenance-mismatch":
                    probe["request_profile"] = "unknown-legacy-profile"
                elif name == "non-failed-owner":
                    operation = next(
                        item
                        for item in _operation_values(snapshot["result"])
                        if item["operation_id"] == probe["producer_operation_id"]
                    )
                    operation["execution_state"] = "succeeded"
                    operation["state"] = "complete"
                    operation["query_complete"] = True
                elif name == "unsupported-classification":
                    probe["source_result"] = "credentials_missing"
                else:
                    payload["plan_snapshots"].append(deepcopy(snapshot))

                with self.assertRaises(ValueError):
                    generator._adapt_v030_release_evidence(payload)

    def test_v030_legacy_loader_requires_exact_evidence_bytes(self) -> None:
        expected = generator.DEFAULT_RELEASE_EVIDENCE.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            exact_copy = Path(temporary) / "v0.3.0-release-probe-evidence.json"
            exact_copy.write_bytes(expected)
            adapted = generator._load_v030_release_evidence(exact_copy)
            self.assertEqual(
                [
                    item["source_error_codes"]
                    for item in _operation_values(adapted)
                    if item["source_error_codes"]
                ],
                [["missing_credential"]],
            )

            drifted = Path(temporary) / "drifted-release-probe-evidence.json"
            drifted.write_bytes(expected + b"\n")
            with self.assertRaises(ValueError):
                generator._load_v030_release_evidence(drifted)

    def test_render_matrix_bytes_requires_exact_v030_evidence_bytes(self) -> None:
        payload = generator._adapt_v030_release_evidence(_v030_payload())
        with tempfile.TemporaryDirectory() as temporary:
            drifted = Path(temporary) / "v0.3.0-release-probe-evidence.json"
            drifted.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "legacy release evidence bytes are unsupported"
            ):
                generator._load_v030_release_evidence(drifted)
            with self.assertRaisesRegex(
                ValueError, "legacy release evidence bytes are unsupported"
            ):
                generator.render_matrix_bytes(
                    qualification_path=generator.DEFAULT_QUALIFICATION,
                    release_evidence_path=drifted,
                )

    def test_exact_v030_adapter_enriches_normal_builder_application_authority(
        self,
    ) -> None:
        with patch.object(
            generator,
            "build_operation_release_matrix",
            wraps=generator.build_operation_release_matrix,
        ) as builder:
            rendered = generator.render_matrix_bytes(
                qualification_path=generator.DEFAULT_QUALIFICATION,
                release_evidence_path=generator.DEFAULT_RELEASE_EVIDENCE,
            )

        self.assertEqual(rendered, generator.DEFAULT_MATRIX.read_bytes())
        arguments = builder.call_args.kwargs
        qualifications = arguments["qualification_evidence"]
        self.assertTrue(
            all(qualification.capability_ids for qualification in qualifications)
        )
        reviewed_applications = {
            (capability_id, qualification.producer_operation_id)
            for qualification in qualifications
            for capability_id in qualification.capability_ids
        }
        self.assertEqual(
            reviewed_applications,
            set(formal_operation_applications(arguments["plan_snapshots"])),
        )

    def test_copied_generator_rejects_schema_valid_v030_byte_drift(self) -> None:
        payload = generator._adapt_v030_release_evidence(_v030_payload())
        with tempfile.TemporaryDirectory() as temporary:
            copied_repository = Path(temporary) / "copied-repository"
            shutil.copytree(
                REPOSITORY_ROOT,
                copied_repository,
                ignore=shutil.ignore_patterns(
                    ".git", "__pycache__", ".mypy_cache", ".ruff_cache"
                ),
            )
            drifted = copied_repository / "drifted-v0.3-release-evidence.json"
            drifted.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(
                        copied_repository
                        / "tests"
                        / "generate_core_operation_release_matrix.py"
                    ),
                    "--check",
                    "--release-evidence",
                    str(drifted),
                ],
                cwd=copied_repository,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: core operation release matrix generation failed\n",
        )

    def test_reviewed_manifest_references_resolve_to_repo_symbols(self) -> None:
        manifest = json.loads(
            generator.DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
        )
        producer_ids = tuple(
            item["producer_operation_id"] for item in manifest["operations"]
        )
        qualification = load_operation_qualification_manifest(
            manifest,
            expected_producer_operation_ids=producer_ids,
        )

        generator.validate_evidence_references(qualification)

        self.assertEqual(len(qualification), 44)
        with self.assertRaisesRegex(ValueError, "symbol is missing"):
            generator.validate_evidence_references(
                (
                    replace(
                        qualification[0],
                        evidence_references=(
                            "tests/test_identity_sources.py#MissingContractCanary",
                        ),
                    ),
                )
            )

    def test_offline_selectors_must_bind_their_owner_operations(self) -> None:
        manifest = json.loads(
            generator.DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
        )
        producer_ids = tuple(
            item["producer_operation_id"] for item in manifest["operations"]
        )
        qualification = load_operation_qualification_manifest(
            manifest,
            expected_producer_operation_ids=producer_ids,
        )
        misbound_ids = (
            "sina_financial_statements@1",
            "sse_etf_list@1",
            "sse_etf_snapshot@1",
            "ths_consensus_eps@1",
        )
        misbound = tuple(
            replace(
                item,
                offline_contract_selector=(
                    "tests/test_identity_sources.py#IdentitySourceOperationTests"
                ),
            )
            for item in qualification
            if item.producer_operation_id in misbound_ids
        )

        with self.assertRaisesRegex(
            ValueError,
            "offline selectors do not bind producers: " + ", ".join(misbound_ids),
        ):
            generator.validate_evidence_references(misbound)

    def test_offline_selector_anchor_must_bind_its_owner_operation(self) -> None:
        manifest = json.loads(
            generator.DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
        )
        producer_ids = tuple(
            item["producer_operation_id"] for item in manifest["operations"]
        )
        qualification = load_operation_qualification_manifest(
            manifest,
            expected_producer_operation_ids=producer_ids,
        )
        szse_daily_line = next(
            item
            for item in qualification
            if item.producer_operation_id == "szse_daily_line@1"
        )
        wrong_anchor = replace(
            szse_daily_line,
            offline_contract_selector="tests/test_close_sources.py#StaticTransport",
        )

        with self.assertRaisesRegex(
            ValueError,
            "offline selectors do not bind producers: szse_daily_line@1",
        ):
            generator.validate_evidence_references((wrong_anchor,))


if __name__ == "__main__":
    unittest.main()
