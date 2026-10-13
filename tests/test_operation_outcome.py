from __future__ import annotations

import json
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from a_share_research.research_result import (  # noqa: E402
    DirectTaskPlan,
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    fold_direct_task_research_result,
    stable_result_id,
)


class OperationOutcomeImmutabilityTests(unittest.TestCase):
    def test_every_owned_field_rejects_base_class_and_nested_mutation(self) -> None:
        shared = {"values": [{"value": "original"}]}
        observation = {"source_operation": "fixture@1", "proof": shared}
        source_error = {
            "source_operation": "fixture@1",
            "code": "partial",
            "message": "safe",
            "details": shared,
        }
        diagnostics = {"attempts": shared}
        storage = {"audit": shared}
        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("failed"),
            observations=(observation,),
            source_errors=(source_error,),
            diagnostics=diagnostics,
            evidence_storage=storage,
        )
        original_bytes = json.dumps(outcome.to_dict(), sort_keys=True)
        original_id = stable_result_id(
            "observation", outcome.to_dict()["observations"][0]
        )
        shared["values"][0]["value"] = "changed"
        observation["source_operation"] = "sibling@1"
        source_error.clear()
        diagnostics.clear()
        storage.clear()
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(outcome.observations[0]["source_operation"], "fixture@1")
        self.assertEqual(json.dumps(outcome.to_dict(), sort_keys=True), original_bytes)
        self.assertEqual(
            stable_result_id("observation", outcome.to_dict()["observations"][0]),
            original_id,
        )
        for root in (
            outcome.observations[0],
            outcome.source_errors[0],
            outcome.diagnostics,
            outcome.evidence_storage,
        ):
            with self.assertRaises(TypeError):
                dict.__setitem__(root, "escaped", True)
        owned_fields = (
            outcome.observations[0]["proof"],
            outcome.source_errors[0]["details"],
            outcome.diagnostics["attempts"],
            outcome.evidence_storage["audit"],
        )
        for owned in owned_fields:
            with self.subTest(owned=owned):
                self.assertIsInstance(owned, Mapping)
                self.assertEqual(owned["values"][0]["value"], "original")
                with self.assertRaises(TypeError):
                    dict.__setitem__(owned, "values", [])
                with self.assertRaises(TypeError):
                    owned["values"][0]["value"] = "escaped"
                with self.assertRaises((AttributeError, TypeError)):
                    owned["values"].append({"value": "escaped"})
                with self.assertRaises(TypeError):
                    list.__setitem__(owned["values"], 0, {"value": "escaped"})
        self.assertEqual(outcome.coverage, "partial")

    def test_reused_caller_mapping_is_observed_once_across_owned_fields(self) -> None:
        class OnceMapping(dict):
            def items(self):
                self.reads += 1
                if self.reads > 1:
                    raise AssertionError("caller proof was observed again")
                return super().items()

        shared = OnceMapping(values=["original"])
        shared.reads = 0
        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("failed"),
            observations=({"source_operation": "fixture@1", "proof": shared},),
            source_errors=(
                {
                    "source_operation": "fixture@1",
                    "code": "partial",
                    "message": "safe",
                    "details": shared,
                },
            ),
            diagnostics={"attempts": shared},
            evidence_storage={"audit": shared},
        )
        projection = outcome.to_dict()
        self.assertEqual(shared.reads, 1)
        self.assertEqual(
            projection["observations"][0]["proof"], {"values": ["original"]}
        )
        self.assertEqual(
            projection["source_errors"][0]["details"], {"values": ["original"]}
        )
        self.assertEqual(
            projection["diagnostics"]["attempts"], {"values": ["original"]}
        )
        self.assertEqual(
            projection["evidence_storage"]["audit"], {"values": ["original"]}
        )

    def test_mapping_operators_never_expose_mutable_stored_proof(self) -> None:
        for field in (
            "observations",
            "source_errors",
            "diagnostics",
            "evidence_storage",
        ):
            for operation in ("equality", "inequality", "union", "reverse_union"):
                with self.subTest(field=field, operation=operation):
                    outcome = OperationOutcome(
                        "fixture",
                        "fixture@1",
                        OperationExecution("failed"),
                        observations=(
                            {"source_operation": "fixture@1", "proof": {"values": [1]}},
                        ),
                        source_errors=(
                            {
                                "source_operation": "fixture@1",
                                "code": "partial",
                                "message": "safe",
                                "details": {"values": [2]},
                            },
                        ),
                        diagnostics={"attempts": {"values": [3]}},
                        evidence_storage={"audit": {"values": [4]}},
                    )
                    before = json.dumps(outcome.to_dict(), sort_keys=True)
                    roots = {
                        "observations": outcome.observations[0]["proof"],
                        "source_errors": outcome.source_errors[0]["details"],
                        "diagnostics": outcome.diagnostics["attempts"],
                        "evidence_storage": outcome.evidence_storage["audit"],
                    }
                    owned = roots[field]

                    class Peer:
                        def attempt(self, value):
                            try:
                                dict.__setitem__(value, "escaped", "changed")
                            except TypeError:
                                pass
                            return False

                        __eq__ = attempt
                        __ne__ = attempt
                        __or__ = attempt
                        __ror__ = attempt

                    if operation == "equality":
                        owned == Peer()
                    elif operation == "inequality":
                        owned != Peer()
                    elif operation == "union":
                        owned | Peer()
                    else:
                        Peer() | owned
                    self.assertNotIn("escaped", owned)
                    self.assertEqual(
                        json.dumps(outcome.to_dict(), sort_keys=True), before
                    )

    def test_public_builder_preserves_predecessor_source_error_strings(self) -> None:
        plan = DirectTaskPlan(
            task_type="release_source_error_test",
            as_of="2026-08-12",
            obligations=(
                OperationObligation(
                    "shared_source_error_contract",
                    "fixture@1",
                    "release_test",
                    True,
                    None,
                    (EvidenceUsePolicy("release_test", "supporting"),),
                ),
            ),
            dependencies=(ResearchDependency("release_test", "release_test", 1, True),),
        )
        for field in ("code", "message"):
            for value in ("", " \t\n", "x" * 257, "x" * 65_537, "\u0000\n"):
                with self.subTest(field=field, length=len(value)):
                    error = {
                        "source_operation": "fixture@1",
                        "code": "missing_credential",
                        "message": "safe",
                        field: value,
                    }
                    outcome = OperationOutcome(
                        "shared_source_error_contract",
                        "fixture@1",
                        OperationExecution("failed"),
                        source_errors=(error,),
                        field_dispositions=(
                            FieldDisposition(
                                "payload", "normalized", "observations[].payload"
                            ),
                        ),
                    )
                    result = fold_direct_task_research_result(
                        plan,
                        (outcome,),
                        summary="Release source-error contract fixture.",
                    ).to_dict()
                    public = result["source_errors"][0]
                    self.assertEqual(public[field], value)
                    canonical = {
                        "capability_id": "shared_source_error_contract",
                        "operation_id": "fixture@1",
                        "code": error["code"],
                        "message": error["message"],
                    }
                    self.assertEqual(
                        public["id"], stable_result_id("source-error", canonical)
                    )

    def test_many_reused_nested_references_stay_within_json_bounds(self) -> None:
        shared = {"value": "original"}
        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("succeeded", True),
            observations=(
                {"source_operation": "fixture@1", "values": [shared] * 1_500},
            ),
        )
        projected = outcome.to_dict()["observations"][0]["values"]
        self.assertEqual(len(projected), 1_500)
        self.assertEqual(projected[0], {"value": "original"})
        self.assertEqual(projected[-1], {"value": "original"})

    def test_expanded_aliases_keep_exact_structural_item_bound(self) -> None:
        shared = [0] * 4_998
        observation = {
            "source_operation": "fixture@1",
            "first": shared,
            "second": shared,
        }
        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("succeeded", True),
            observations=(observation,),
        )
        self.assertEqual(len(outcome.to_dict()["observations"][0]["first"]), 4_998)
        shared.append(0)
        with self.assertRaisesRegex(ValueError, "structural bounds"):
            OperationOutcome(
                "fixture",
                "fixture@1",
                OperationExecution("succeeded", True),
                observations=(observation,),
            )

    def test_invalid_proof_shapes_fail_closed_in_every_owned_field(self) -> None:
        too_deep = 0
        for _ in range(33):
            too_deep = [too_deep]
        cyclic = []
        cyclic.append(cyclic)
        invalid = (
            object(),
            {1: "value"},
            {"value"},
            frozenset({"value"}),
            float("inf"),
            float("nan"),
            too_deep,
            [0] * 10_000,
            cyclic,
        )
        for field in (
            "observations",
            "source_errors",
            "diagnostics",
            "evidence_storage",
        ):
            for value in invalid:
                with self.subTest(field=field, kind=type(value).__name__):
                    kwargs = {field: {"nested": value}}
                    execution = OperationExecution("succeeded", True)
                    if field in {"observations", "source_errors"}:
                        kwargs[field] = (
                            {"source_operation": "fixture@1", "nested": value},
                        )
                        if field == "source_errors":
                            execution = OperationExecution("failed")
                    with self.assertRaises(ValueError):
                        OperationOutcome("fixture", "fixture@1", execution, **kwargs)

    def test_sensitive_diagnostics_and_source_errors_fail_before_projection(
        self,
    ) -> None:
        for key in (
            "authorization_header",
            "Api-Key",
            "private_token",
            "provider_raw_payload",
        ):
            with self.subTest(key=key):
                for field in ("diagnostics", "source_errors"):
                    kwargs = {field: {"nested": {key: "SECRET_VALUE"}}}
                    execution = OperationExecution("succeeded", True)
                    if field == "source_errors":
                        execution = OperationExecution("failed")
                        kwargs[field] = (
                            {
                                "source_operation": "fixture@1",
                                "code": "partial",
                                "message": "safe",
                                "details": {key: "SECRET_VALUE"},
                            },
                        )
                    with self.assertRaises(ValueError):
                        OperationOutcome("fixture", "fixture@1", execution, **kwargs)

    def test_projection_normalizes_accepted_scalar_subclasses_and_tuples(self) -> None:
        class CallerString(str):
            pass

        class CallerInteger(int):
            pass

        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("succeeded", True),
            observations=(
                {
                    "source_operation": "fixture@1",
                    "values": (CallerString("literal"), CallerInteger(7)),
                },
            ),
        )
        values = outcome.to_dict()["observations"][0]["values"]
        self.assertIs(type(values), list)
        self.assertIs(type(values[0]), str)
        self.assertIs(type(values[1]), int)
        self.assertEqual(values, ["literal", 7])

    def test_projection_preserves_existing_owner_json_field_order(self) -> None:
        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("succeeded", True),
            observations=(
                {
                    "source_operation": "fixture@1",
                    "z": {"second": 2, "first": 1},
                    "a": [3],
                },
            ),
        )
        self.assertEqual(
            json.dumps(outcome.to_dict()["observations"][0], separators=(",", ":")),
            '{"source_operation":"fixture@1","z":{"second":2,"first":1},"a":[3]}',
        )

    def test_plain_projection_detaches_all_owned_fields_and_later_bytes(self) -> None:
        outcome = OperationOutcome(
            "fixture",
            "fixture@1",
            OperationExecution("failed"),
            observations=({"source_operation": "fixture@1", "proof": {"values": [1]}},),
            source_errors=(
                {
                    "source_operation": "fixture@1",
                    "code": "partial",
                    "message": "safe",
                    "details": {"values": [2]},
                },
            ),
            diagnostics={"attempts": {"values": [3]}},
            evidence_storage={"audit": {"values": [4]}},
        )
        first = outcome.to_dict()
        original_bytes = json.dumps(first, sort_keys=True, separators=(",", ":"))
        self.assertEqual(first["observations"][0]["proof"], {"values": [1]})
        self.assertEqual(first["source_errors"][0]["details"], {"values": [2]})
        self.assertEqual(first["diagnostics"], {"attempts": {"values": [3]}})
        self.assertEqual(first["evidence_storage"], {"audit": {"values": [4]}})
        first["observations"][0]["proof"]["values"].append(99)
        first["source_errors"][0]["details"]["values"].clear()
        first["diagnostics"]["attempts"]["values"][0] = 99
        first["evidence_storage"]["audit"]["values"].append(99)
        second = outcome.to_dict()
        self.assertEqual(
            json.dumps(second, sort_keys=True, separators=(",", ":")), original_bytes
        )
        self.assertEqual(outcome.coverage, "partial")
        coverage = outcome.to_coverage_dict(
            observation_ids=["observation-one"], source_error_ids=["error-one"]
        )
        self.assertEqual(coverage["diagnostics"], {"attempts": {"values": [3]}})
        self.assertEqual(coverage["evidence_storage"], {"audit": {"values": [4]}})
        coverage["diagnostics"]["attempts"]["values"].append(99)
        self.assertEqual(
            outcome.to_dict()["diagnostics"], {"attempts": {"values": [3]}}
        )


if __name__ == "__main__":
    unittest.main()
