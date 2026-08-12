from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.operation_release_matrix import (  # noqa: E402
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


class CoreOperationMatrixGeneratorTests(unittest.TestCase):
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
