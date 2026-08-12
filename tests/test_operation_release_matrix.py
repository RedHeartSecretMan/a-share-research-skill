from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import replace

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.operation_release_matrix import (  # noqa: E402
    InstallationLoadEvidence,
    LiveProbeEvidence,
    OperationQualificationEvidence,
    ReleasePlanSnapshot,
    build_operation_release_matrix,
    formal_producer_operation_ids,
    load_operation_qualification_manifest,
)
from a_share_research.research_result import (  # noqa: E402
    EvidenceUsePolicy,
    OperationObligation,
)

from tests.test_direct_research_workflow_runtime import (  # noqa: E402
    comparison_workflow_request,
)
from tests.test_research_workflows import fixture_runtime  # noqa: E402


class OperationReleaseMatrixTests(unittest.TestCase):
    def test_qualification_manifest_strictly_joins_plan_denominator(self) -> None:
        payload = {
            "schema_version": "1.0",
            "operations": [
                {
                    "producer_operation_id": "alpha_source@1",
                    "disposition": "experimental",
                    "temporal_semantics": {"basis": "observed_at"},
                    "unit_semantics": {"value": "source_declared_unit"},
                    "failure_semantics": "operation_local_failure",
                    "empty_result_semantics": "complete_query_zero_records",
                    "acquisition_role": "primary",
                    "credential_policy": "not_required",
                    "rate_limit_policy": {"scope": "owner_operation"},
                    "offline_contract_selector": "tests.test_identity_sources",
                    "offline_contract_result": "passed",
                    "evidence_references": [
                        "skill/a-share-research/scripts/a_share_research/identity_sources.py",
                        "docs/specs/0007-implementation-amendment-a.md#testing-seams",
                    ],
                    "gap_issues": [58],
                }
            ],
        }

        evidence = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=("alpha_source@1",),
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            evidence[0].evidence_references,
            (
                "skill/a-share-research/scripts/a_share_research/identity_sources.py",
                "docs/specs/0007-implementation-amendment-a.md#testing-seams",
            ),
        )
        for mutation in (
            {**payload, "unknown": True},
            {**payload, "operations": []},
            {
                **payload,
                "operations": [{**payload["operations"][0], "unknown": "field"}],
            },
        ):
            with self.assertRaises(ValueError):
                load_operation_qualification_manifest(
                    mutation,
                    expected_producer_operation_ids=("alpha_source@1",),
                )

    def test_workflow_public_leaf_plans_add_applications_without_new_denominator(
        self,
    ) -> None:
        public_result = (
            fixture_runtime()
            .research(comparison_workflow_request(["工业富联", "平安银行"]))
            .to_dict()
        )
        step_registry = public_result["capability_registry"]["steps"]["comparison"]
        obligations = step_registry["leaf_plan"]["operation_obligations"]
        producer_ids = sorted({item["producer_operation_id"] for item in obligations})
        snapshot = ReleasePlanSnapshot(
            result=public_result,
            request_profile="workflow-valuation-comparison",
        )
        qualifications = tuple(
            OperationQualificationEvidence(
                producer_operation_id=producer_id,
                disposition="experimental",
                temporal_semantics={"basis": "owner_contract"},
                unit_semantics={"basis": "owner_contract"},
                failure_semantics="operation_local_failure",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="workflow_leaf",
                credential_policy="none",
                rate_limit_policy={"scope": "provider", "bounded": True},
                offline_contract_selector="tests.test_research_workflows",
                offline_contract_result="passed",
                evidence_references=("tests.test_research_workflows",),
            )
            for producer_id in producer_ids
        )
        installations = tuple(
            InstallationLoadEvidence(
                producer_operation_id=producer_id,
                origin="copied_install_plan",
                status="complete",
                selector="workflow-valuation-comparison",
            )
            for producer_id in producer_ids
        )
        probes = tuple(
            LiveProbeEvidence(
                producer_operation_id=producer_id,
                probe="tests/release_probe_core_operations.py",
                probe_status="passed",
                attempted=True,
                source_result="complete",
                verified_at="2026-08-12T09:30:00+08:00",
            )
            for producer_id in producer_ids
        )

        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=qualifications,
            installation_loads=installations,
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=probes,
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], len(producer_ids))
        for operation in matrix["operations"]:
            self.assertEqual(len(operation["applications"]), 2)
            self.assertTrue(
                all(
                    application["workflow_use"]
                    == {
                        "plan_id": public_result["plan"]["id"],
                        "plan_version": "1.0",
                        "workflow_id": "valuation_comparison",
                        "step_id": "comparison",
                        "step_version": "1.0",
                    }
                    for application in operation["applications"]
                )
            )

    def _stable_matrix_inputs(self) -> dict[str, object]:
        producer_ids = ("alpha_source@1", "beta_source@1")
        snapshots: list[ReleasePlanSnapshot] = []
        for index, producer_id in enumerate(producer_ids):
            capability_id = f"capability-{index}"
            snapshots.append(
                ReleasePlanSnapshot(
                    result={
                        "schema_version": "1.0",
                        "plan": {
                            "id": f"plan-{index}",
                            "version": "1.0",
                            "task_type": f"task-{index}",
                            "capabilities": [capability_id],
                            "operation_obligations": [
                                {
                                    "capability_id": capability_id,
                                    "operation_id": producer_id,
                                    "producer_operation_id": producer_id,
                                    "operation_kind": "external_source",
                                    "source_role": "market_observation",
                                    "experimental": True,
                                    "exchange": None,
                                    "evidence_uses": [
                                        {"use": "test_use", "role": "supporting"}
                                    ],
                                }
                            ],
                            "research_dependencies": [],
                        },
                        "coverage": {
                            capability_id: {
                                "status": "observed_empty",
                                "operations": {
                                    producer_id: {
                                        "capability_id": capability_id,
                                        "operation_id": producer_id,
                                        "execution_state": "succeeded",
                                        "state": "observed_empty",
                                        "query_complete": True,
                                        "observation_ids": [],
                                        "source_error_ids": [],
                                        "field_dispositions": [
                                            {
                                                "field": f"field-{index}",
                                                "disposition": "normalized",
                                                "destination": (
                                                    "observations[].payload.value"
                                                ),
                                            }
                                        ],
                                        "diagnostics": {},
                                    }
                                },
                            }
                        },
                    },
                    request_profile=f"profile-{index}",
                )
            )
        qualifications = tuple(
            OperationQualificationEvidence(
                producer_operation_id=producer_id,
                disposition="experimental",
                temporal_semantics={"basis": "observed_at"},
                unit_semantics={"value": "source_declared_unit"},
                failure_semantics="failed_with_source_error",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="complementary",
                credential_policy="none",
                rate_limit_policy={"scope": "provider", "max_requests": 1},
                offline_contract_selector=f"tests.contracts.operation_{index}",
                offline_contract_result="passed",
                evidence_references=(f"tests.contracts.operation_{index}",),
            )
            for index, producer_id in enumerate(producer_ids)
        )
        installations = tuple(
            InstallationLoadEvidence(
                producer_operation_id=producer_id,
                origin="copied_install_plan",
                status="complete",
                selector=f"tests.install.operation_{index}",
            )
            for index, producer_id in enumerate(producer_ids)
        )
        probes = tuple(
            LiveProbeEvidence(
                producer_operation_id=producer_id,
                probe="tests/release_probe_core_operations.py",
                probe_status="passed",
                attempted=True,
                source_result="observed_empty",
                verified_at="2026-08-12T09:30:00+08:00",
            )
            for producer_id in producer_ids
        )
        return {
            "plan_snapshots": tuple(snapshots),
            "qualification_evidence": qualifications,
            "installation_loads": installations,
            "installation_health": {
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [
                    {
                        "distribution": "anyio",
                        "required_version": "4.14.2",
                        "installed_version": "4.14.2",
                        "status": "complete",
                        "modules": [],
                        "operation_ids": [],
                    }
                ],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            "live_probes": probes,
        }

    def test_matrix_bytes_are_order_independent_and_applications_are_deduplicated(
        self,
    ) -> None:
        inputs = self._stable_matrix_inputs()
        snapshots = inputs["plan_snapshots"]
        qualifications = inputs["qualification_evidence"]
        installations = inputs["installation_loads"]
        probes = inputs["live_probes"]
        assert isinstance(snapshots, tuple)
        assert isinstance(qualifications, tuple)
        assert isinstance(installations, tuple)
        assert isinstance(probes, tuple)

        forward = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (snapshots[0], snapshots[0], snapshots[1]),
            }
        )
        reversed_inputs = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (snapshots[1], snapshots[0], snapshots[0]),
                "qualification_evidence": tuple(reversed(qualifications)),
                "installation_loads": tuple(reversed(installations)),
                "live_probes": tuple(reversed(probes)),
            }
        )

        self.assertEqual(forward.to_json_bytes(), reversed_inputs.to_json_bytes())
        matrix = forward.to_dict()
        alpha = matrix["operations"][0]
        self.assertEqual(len(alpha["applications"]), 1)
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"],
            {"numerator": 2, "denominator": 2, "rate": "1.000000"},
        )

    def test_matrix_summarizes_same_candidate_attempt_provenance(self) -> None:
        inputs = self._stable_matrix_inputs()
        candidate_sha = "a" * 40
        digests = ("b" * 64, "c" * 64)
        probes = tuple(
            replace(
                probe,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                candidate_sha=candidate_sha,
                request_profile=f"profile-{index}",
                attempt_digest=digests[index],
            )
            for index, probe in enumerate(inputs["live_probes"])
        )
        snapshots = tuple(
            replace(
                snapshot,
                candidate_sha=candidate_sha,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                attempt_digest=digests[index],
            )
            for index, snapshot in enumerate(inputs["plan_snapshots"])
        )

        matrix = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": snapshots,
                "live_probes": probes,
            }
        ).to_dict()

        self.assertEqual(
            matrix["release_evidence"],
            {
                "candidate_sha": candidate_sha,
                "attempt_digests": list(digests),
                "verified_from": "2026-08-12T09:30:00+08:00",
                "verified_through": "2026-08-13T09:30:00+08:00",
            },
        )

    def test_matrix_keeps_an_attempt_that_contributes_only_skipped_applications(
        self,
    ) -> None:
        inputs = self._stable_matrix_inputs()
        candidate_sha = "a" * 40
        digests = ("b" * 64, "c" * 64, "d" * 64)
        snapshots = tuple(
            replace(
                snapshot,
                candidate_sha=candidate_sha,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                attempt_digest=digests[index],
            )
            for index, snapshot in enumerate(inputs["plan_snapshots"])
        )
        skipped_result = deepcopy(snapshots[0].result)
        skipped_operation = skipped_result["coverage"]["capability-0"]["operations"][
            "alpha_source@1"
        ]
        skipped_operation["execution_state"] = "not_requested"
        skipped_operation["state"] = "not_requested"
        skipped_operation["query_complete"] = None
        skipped = ReleasePlanSnapshot(
            result=skipped_result,
            request_profile="profile-skipped",
            candidate_sha=candidate_sha,
            verified_at="2026-08-14T09:30:00+08:00",
            attempt_digest=digests[2],
        )
        probes = tuple(
            replace(
                probe,
                verified_at=f"2026-08-{12 + index:02d}T09:30:00+08:00",
                candidate_sha=candidate_sha,
                request_profile=f"profile-{index}",
                attempt_digest=digests[index],
            )
            for index, probe in enumerate(inputs["live_probes"])
        )

        matrix = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (*snapshots, skipped),
                "live_probes": probes,
            }
        ).to_dict()

        self.assertEqual(matrix["release_evidence"]["attempt_digests"], list(digests))

    def test_live_attempt_truth_must_match_plan_application_execution(self) -> None:
        inputs = self._stable_matrix_inputs()
        snapshots = deepcopy(inputs["plan_snapshots"])
        first = snapshots[0].result
        capability = first["coverage"]["capability-0"]
        operation = capability["operations"]["alpha_source@1"]
        operation["execution_state"] = "not_requested"
        operation["state"] = "not_requested"
        operation["query_complete"] = None

        with self.assertRaisesRegex(ValueError, "attempt.*execution"):
            build_operation_release_matrix(**{**inputs, "plan_snapshots": snapshots})

    def test_failed_application_destination_must_use_the_public_contract(self) -> None:
        inputs = self._stable_matrix_inputs()
        snapshots = deepcopy(inputs["plan_snapshots"])
        first = snapshots[0].result
        operation = first["coverage"]["capability-0"]["operations"]["alpha_source@1"]
        operation["execution_state"] = "failed"
        operation["state"] = "failed"
        operation["query_complete"] = None
        operation["field_dispositions"][0]["destination"] = (
            "totally.invalid.destination"
        )
        probes = tuple(inputs["live_probes"])
        probes = (replace(probes[0], source_result="unavailable"), probes[1])

        with self.assertRaisesRegex(ValueError, "destination is unsupported"):
            build_operation_release_matrix(
                **{
                    **inputs,
                    "plan_snapshots": snapshots,
                    "live_probes": probes,
                }
            )

    def test_release_metadata_rejects_secret_or_server_shaped_values(self) -> None:
        base = self._stable_matrix_inputs()
        snapshots = base["plan_snapshots"]
        qualifications = base["qualification_evidence"]
        installations = base["installation_loads"]
        probes = base["live_probes"]
        assert isinstance(snapshots, tuple)
        assert isinstance(qualifications, tuple)
        assert isinstance(installations, tuple)
        assert isinstance(probes, tuple)
        cases = {
            "request URL": {
                "plan_snapshots": (
                    replace(snapshots[0], request_profile="https://10.0.0.1/private"),
                    snapshots[1],
                )
            },
            "offline absolute path": {
                "qualification_evidence": (
                    replace(
                        qualifications[0],
                        offline_contract_selector="/Users/private/test.py",
                    ),
                    qualifications[1],
                )
            },
            "rate credential key": {
                "qualification_evidence": (
                    replace(
                        qualifications[0],
                        rate_limit_policy={"access_token": "secret-value"},
                    ),
                    qualifications[1],
                )
            },
            "install absolute path": {
                "installation_loads": (
                    replace(installations[0], selector="/private/tmp/private-install"),
                    installations[1],
                )
            },
            "probe server URL": {
                "live_probes": (
                    replace(probes[0], probe="https://provider.example/private-probe"),
                    probes[1],
                )
            },
        }
        for name, mutation in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "unsafe release metadata"),
            ):
                build_operation_release_matrix(**{**base, **mutation})

    def test_operation_evidence_is_joined_per_producer_and_rates_are_counted(
        self,
    ) -> None:
        capability_id = "research_content_materials"
        producer_ids = ("public_material@1", "credentialed_material@1")
        obligations = [
            {
                "capability_id": capability_id,
                "operation_id": producer_id,
                "producer_operation_id": producer_id,
                "operation_kind": "external_source",
                "source_role": "declared_per_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [
                    {"use": "research_content_material", "role": "supporting"}
                ],
            }
            for producer_id in producer_ids
        ]
        operations = {
            producer_id: {
                "capability_id": capability_id,
                "operation_id": producer_id,
                "execution_state": "succeeded",
                "state": "observed_empty",
                "query_complete": True,
                "observation_ids": [],
                "source_error_ids": [],
                "field_dispositions": [
                    {
                        "field": f"{producer_id}.field",
                        "disposition": "normalized",
                        "destination": "observations[].payload.content",
                    }
                ],
                "diagnostics": {},
            }
            for producer_id in producer_ids
        }
        operations[producer_ids[1]]["execution_state"] = "failed"
        operations[producer_ids[1]]["state"] = "failed"
        operations[producer_ids[1]]["query_complete"] = None
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "plan": {
                    "id": "plan-two-content-producers",
                    "version": "1.0",
                    "task_type": "research_content",
                    "capabilities": [capability_id],
                    "operation_obligations": obligations,
                    "research_dependencies": [],
                },
                "coverage": {
                    capability_id: {
                        "status": "observed_empty",
                        "operations": operations,
                    }
                },
            },
            request_profile="two-content-producers",
        )
        qualification = (
            OperationQualificationEvidence(
                producer_operation_id=producer_ids[0],
                disposition="experimental",
                temporal_semantics={"basis": "published_at", "timezone": "source"},
                unit_semantics={"content": "unicode_text"},
                failure_semantics="failed_with_source_error",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="complementary",
                credential_policy="none",
                rate_limit_policy={"scope": "provider", "max_requests": 1},
                offline_contract_selector="tests.contracts.public_material",
                offline_contract_result="passed",
                evidence_references=("tests.contracts.public_material",),
                gap_issues=(62,),
            ),
            OperationQualificationEvidence(
                producer_operation_id=producer_ids[1],
                disposition="experimental",
                temporal_semantics={
                    "basis": "available_at",
                    "timezone": "Asia/Shanghai",
                },
                unit_semantics={"content": "licensed_document_text"},
                failure_semantics="credential_failure_is_operation_local",
                empty_result_semantics="complete_search_zero_documents",
                acquisition_role="fallback",
                credential_policy="local_reference_required",
                rate_limit_policy={"scope": "account", "max_requests": 2},
                offline_contract_selector="tests.contracts.credentialed_material",
                offline_contract_result="passed",
                evidence_references=("tests.contracts.credentialed_material",),
                gap_issues=(),
            ),
        )
        install_loads = tuple(
            InstallationLoadEvidence(
                producer_operation_id=producer_id,
                origin="copied_install_plan",
                status="complete",
                selector="tests.test_skill_distribution",
            )
            for producer_id in producer_ids
        )
        live_probes = tuple(
            LiveProbeEvidence(
                producer_operation_id=producer_id,
                probe="tests/release_probe_core_operations.py",
                probe_status="passed",
                attempted=True,
                source_result=(
                    "observed_empty" if index == 0 else "credential_blocked"
                ),
                verified_at="2026-08-12T09:30:00+08:00",
            )
            for index, producer_id in enumerate(producer_ids)
        )

        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=qualification,
            installation_loads=install_loads,
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=live_probes,
        ).to_dict()

        by_id = {item["producer_operation_id"]: item for item in matrix["operations"]}
        self.assertEqual(by_id[producer_ids[0]]["credential_policy"], "none")
        self.assertEqual(
            by_id[producer_ids[1]]["credential_policy"],
            "local_reference_required",
        )
        self.assertNotEqual(
            by_id[producer_ids[0]]["temporal_semantics"],
            by_id[producer_ids[1]]["temporal_semantics"],
        )
        self.assertNotEqual(
            by_id[producer_ids[0]]["unit_semantics"],
            by_id[producer_ids[1]]["unit_semantics"],
        )
        self.assertEqual(
            matrix["summary"],
            {
                "formal_operation_disposition_rate": {
                    "numerator": 2,
                    "denominator": 2,
                    "rate": "1.000000",
                },
                "active_request_field_destination_rate": {
                    "numerator": 2,
                    "denominator": 2,
                    "rate": "1.000000",
                },
                "default_install_load_rate": {
                    "numerator": 2,
                    "denominator": 2,
                    "rate": "1.000000",
                },
            },
        )

    def test_ordinary_plan_obligation_exposes_its_producer_identity(self) -> None:
        obligation = OperationObligation(
            capability_id="research_content",
            operation_id="mootdx_f10@1",
            source_role="declared_per_observation",
            experimental=True,
            exchange=None,
            evidence_uses=(
                EvidenceUsePolicy("research_content_material", "supporting"),
            ),
        )

        self.assertEqual(
            obligation.to_dict(),
            {
                "capability_id": "research_content",
                "operation_id": "mootdx_f10@1",
                "producer_operation_id": "mootdx_f10@1",
                "operation_kind": "external_source",
                "source_role": "declared_per_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [
                    {"use": "research_content_material", "role": "supporting"}
                ],
            },
        )

    def test_deterministic_coordinator_is_not_a_formal_source_denominator(self) -> None:
        from a_share_research.market_signals import COORDINATOR_OPERATION_ID

        inputs = self._stable_matrix_inputs()
        snapshots = inputs["plan_snapshots"]
        assert isinstance(snapshots, tuple)
        result = deepcopy(snapshots[0].result)
        result["plan"]["task_type"] = "market_signals"
        coordinator_id = COORDINATOR_OPERATION_ID
        result["plan"]["operation_obligations"].append(
            {
                "capability_id": "capability-0",
                "operation_id": coordinator_id,
                "producer_operation_id": coordinator_id,
                "operation_kind": "deterministic_coordinator",
                "source_role": "market_signal",
                "experimental": False,
                "exchange": None,
                "evidence_uses": [{"use": "test_use", "role": "supporting"}],
            }
        )
        result["coverage"]["capability-0"]["operations"][coordinator_id] = {
            "capability_id": "capability-0",
            "operation_id": coordinator_id,
            "execution_state": "succeeded",
            "state": "observed_empty",
            "query_complete": True,
            "field_dispositions": [
                {
                    "field": "derived_value",
                    "disposition": "normalized",
                    "destination": "observations[].payload.derived_value",
                }
            ],
        }

        matrix = build_operation_release_matrix(
            **{
                **inputs,
                "plan_snapshots": (
                    replace(snapshots[0], result=result),
                    snapshots[1],
                ),
            }
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 2)
        self.assertNotIn(
            coordinator_id,
            {item["producer_operation_id"] for item in matrix["operations"]},
        )

    def test_member_applications_share_one_formal_producer_denominator(self) -> None:
        producer_operation_id = "sina_financial_statements@1"
        capability_id = "valuation_compare_metrics"
        application_ids = (
            f"member-0::{producer_operation_id}",
            f"member-1::{producer_operation_id}",
        )
        obligations = [
            {
                "capability_id": capability_id,
                "operation_id": application_id,
                "producer_operation_id": producer_operation_id,
                "operation_kind": "external_source",
                "source_role": "market_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [{"use": "valuation_compare", "role": "supporting"}],
            }
            for application_id in application_ids
        ]
        operations = {
            application_id: {
                "capability_id": capability_id,
                "operation_id": application_id,
                "execution_state": "succeeded",
                "state": "observed_empty",
                "query_complete": True,
                "observation_ids": [],
                "source_error_ids": [],
                "field_dispositions": [
                    {
                        "field": "report_date",
                        "disposition": "normalized",
                        "destination": "observations[].payload.report.period_end",
                    }
                ],
                "diagnostics": {},
            }
            for application_id in application_ids
        }
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "plan": {
                    "id": "plan-comparison",
                    "version": "1.0",
                    "task_type": "valuation_compare",
                    "capabilities": [capability_id],
                    "operation_obligations": obligations,
                    "research_dependencies": [],
                },
                "coverage": {
                    capability_id: {
                        "status": "observed_empty",
                        "operations": operations,
                    }
                },
            },
            request_profile="two-member-comparison",
        )
        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=(
                OperationQualificationEvidence(
                    producer_operation_id=producer_operation_id,
                    disposition="experimental",
                    temporal_semantics={"basis": "report_period_end"},
                    unit_semantics={"financial_values": "reported_currency"},
                    failure_semantics="member_local_failure",
                    empty_result_semantics="complete_query_zero_reports",
                    acquisition_role="supporting",
                    credential_policy="none",
                    rate_limit_policy={"scope": "provider", "max_requests": 2},
                    offline_contract_selector=(
                        "tests.test_direct_market_valuation_runtime"
                    ),
                    offline_contract_result="passed",
                    evidence_references=("tests.test_direct_market_valuation_runtime",),
                    gap_issues=(61,),
                ),
            ),
            installation_loads=(
                InstallationLoadEvidence(
                    producer_operation_id=producer_operation_id,
                    origin="copied_install_plan",
                    status="complete",
                    selector="tests.test_skill_distribution",
                ),
            ),
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=(
                LiveProbeEvidence(
                    producer_operation_id=producer_operation_id,
                    probe="tests/release_probe_core_operations.py",
                    probe_status="passed",
                    attempted=True,
                    source_result="observed_empty",
                    verified_at="2026-08-12T09:30:00+08:00",
                ),
            ),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 1)
        operation = matrix["operations"][0]
        self.assertEqual(operation["producer_operation_id"], producer_operation_id)
        self.assertEqual(
            [item["operation_id"] for item in operation["applications"]],
            list(application_ids),
        )
        forged = deepcopy(snapshot.result)
        forged["plan"]["operation_obligations"][0]["producer_operation_id"] = (
            "tencent_daily_line@1"
        )
        with self.assertRaisesRegex(ValueError, "producer operation"):
            formal_producer_operation_ids(
                (
                    ReleasePlanSnapshot(
                        result=forged,
                        request_profile="forged-member-comparison",
                    ),
                )
            )

    def test_one_producer_preserves_task_local_field_contracts_per_application(
        self,
    ) -> None:
        base = self._stable_matrix_inputs()
        snapshots = base["plan_snapshots"]
        assert isinstance(snapshots, tuple)
        producer_id = "shared_source@1"
        first = deepcopy(snapshots[0].result)
        second = deepcopy(snapshots[0].result)
        for result, profile, destination in (
            (first, "task-a", "observations[].payload.subject.security"),
            (second, "task-b", "observations[].payload.subject.security.code"),
        ):
            obligation = result["plan"]["operation_obligations"][0]
            old_id = obligation["operation_id"]
            obligation["operation_id"] = producer_id
            obligation["producer_operation_id"] = producer_id
            result["plan"]["id"] = f"plan-{profile}"
            result["plan"]["task_type"] = profile
            operation = result["coverage"]["capability-0"]["operations"].pop(old_id)
            operation["operation_id"] = producer_id
            operation["field_dispositions"][0]["destination"] = destination
            result["coverage"]["capability-0"]["operations"][producer_id] = operation
        qualification = replace(
            base["qualification_evidence"][0],
            producer_operation_id=producer_id,
        )
        installation = replace(
            base["installation_loads"][0], producer_operation_id=producer_id
        )
        live = replace(base["live_probes"][0], producer_operation_id=producer_id)

        matrix = build_operation_release_matrix(
            plan_snapshots=(
                ReleasePlanSnapshot(result=first, request_profile="task-a"),
                ReleasePlanSnapshot(result=second, request_profile="task-b"),
            ),
            qualification_evidence=(qualification,),
            installation_loads=(installation,),
            installation_health={
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            live_probes=(live,),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 1)
        operation = matrix["operations"][0]
        self.assertEqual(len(operation["applications"]), 2)
        self.assertEqual(
            {
                application["request_profile"]: application["field_dispositions"]
                for application in operation["applications"]
            },
            {
                "task-a": [
                    {
                        "field": "field-0",
                        "disposition": "normalized",
                        "destination": "observations[].payload.subject.security",
                    }
                ],
                "task-b": [
                    {
                        "field": "field-0",
                        "disposition": "normalized",
                        "destination": "observations[].payload.subject.security.code",
                    }
                ],
            },
        )
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"],
            {"numerator": 2, "denominator": 2, "rate": "1.000000"},
        )

    def test_one_plan_owned_operation_requires_all_three_release_evidence_kinds(
        self,
    ) -> None:
        owner = ("research_content", "mootdx_f10@1")
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "task_type": "research_content",
                "research_anchor": {
                    "as_of": "2026-08-12",
                    "timezone": "Asia/Shanghai",
                },
                "plan": {
                    "id": "plan-content-f10",
                    "version": "1.0",
                    "task_type": "research_content",
                    "capabilities": [owner[0]],
                    "operation_obligations": [
                        {
                            "capability_id": owner[0],
                            "operation_id": owner[1],
                            "producer_operation_id": owner[1],
                            "operation_kind": "external_source",
                            "source_role": "declared_per_observation",
                            "experimental": True,
                            "exchange": None,
                            "evidence_uses": [
                                {
                                    "use": "research_content_material",
                                    "role": "supporting",
                                }
                            ],
                        }
                    ],
                    "research_dependencies": [
                        {
                            "consumer_id": "research_content_material_group",
                            "evidence_use": "research_content_material",
                            "minimum_supporting_observations": 1,
                            "core": True,
                        }
                    ],
                },
                "coverage": {
                    owner[0]: {
                        "status": "complete",
                        "operations": {
                            owner[1]: {
                                "capability_id": owner[0],
                                "operation_id": owner[1],
                                "execution_state": "succeeded",
                                "state": "observed_empty",
                                "query_complete": True,
                                "observation_ids": [],
                                "source_error_ids": [],
                                "field_dispositions": [
                                    {
                                        "field": "content_material",
                                        "disposition": "retained",
                                        "destination": "observations[].payload",
                                    }
                                ],
                                "diagnostics": {},
                            }
                        },
                    }
                },
            },
            request_profile="content-f10-fallback",
        )
        qualification = OperationQualificationEvidence(
            producer_operation_id=owner[1],
            disposition="experimental",
            temporal_semantics={"basis": "available_at"},
            unit_semantics={"content_material": "normalized_material"},
            failure_semantics="failed_with_source_error",
            empty_result_semantics="complete_query_zero_records",
            acquisition_role="fallback",
            credential_policy="none",
            rate_limit_policy={"scope": "provider", "max_requests": 1},
            offline_contract_selector=(
                "tests.test_operation_release_matrix."
                "OperationReleaseMatrixTests."
                "test_one_plan_owned_operation_requires_all_three_release_evidence_kinds"
            ),
            offline_contract_result="passed",
            evidence_references=("tests.test_operation_release_matrix",),
            gap_issues=(61,),
        )
        live = LiveProbeEvidence(
            producer_operation_id=owner[1],
            probe="tests/release_probe_core_operations.py",
            probe_status="passed",
            attempted=True,
            source_result="observed_empty",
            verified_at="2026-08-12T09:30:00+08:00",
        )
        installation_health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [
                {
                    "distribution": "mootdx",
                    "required_version": "0.11.7",
                    "installed_version": "0.11.7",
                    "status": "complete",
                    "modules": ["mootdx.quotes"],
                    "operation_ids": [owner[1]],
                }
            ],
            "operations": [{"operation_id": owner[1], "status": "complete"}],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }

        matrix = build_operation_release_matrix(
            plan_snapshots=(snapshot,),
            qualification_evidence=(qualification,),
            installation_loads=(
                InstallationLoadEvidence(
                    producer_operation_id=owner[1],
                    origin="installation_health",
                    status="complete",
                    selector="tests.test_installation_health",
                ),
            ),
            installation_health=installation_health,
            live_probes=(live,),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 1)
        self.assertEqual(
            matrix["denominator"]["operation_owner_sha256"],
            "341719cc3d5706dd177a5a62692ebb7cbed03fcd89845eedfca9ceedcf982161",
        )
        self.assertEqual(
            matrix["summary"]["formal_operation_disposition_rate"]["rate"],
            "1.000000",
        )
        self.assertEqual(
            matrix["summary"]["active_request_field_destination_rate"]["rate"],
            "1.000000",
        )
        self.assertEqual(
            matrix["summary"]["default_install_load_rate"]["rate"],
            "1.000000",
        )
        operation = matrix["operations"][0]
        self.assertEqual(operation["producer_operation_id"], owner[1])
        self.assertEqual(
            operation["applicability"],
            {
                "capability_ids": [owner[0]],
                "exchanges": [],
                "plan_ids": ["plan-content-f10"],
                "plan_versions": ["1.0"],
                "request_profiles": ["content-f10-fallback"],
                "task_types": ["research_content"],
                "workflow_ids": [],
            },
        )
        self.assertEqual(operation["verified_at"], live.verified_at)
        self.assertEqual(operation["offline_contract"]["result"], "passed")
        self.assertEqual(operation["live_probe"]["probe_status"], "passed")
        self.assertEqual(operation["install_load"]["status"], "complete")
        self.assertEqual(
            operation["applications"][0]["field_dispositions"],
            [
                {
                    "field": "content_material",
                    "disposition": "retained",
                    "destination": "observations[].payload",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
