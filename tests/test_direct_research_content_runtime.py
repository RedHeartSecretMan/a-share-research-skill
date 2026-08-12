from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
IDENTITY_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from a_share_research.content_registry import (  # noqa: E402
    build_default_content_operations,
)
from a_share_research.report_sources import EastmoneyReportOperation  # noqa: E402
from a_share_research.research_result import (  # noqa: E402
    UnifiedResearchResult,
    stable_result_id,
)

UNIFIED_RESULT_KEYS = {
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
}


def rehash_result(result: dict[str, object]) -> dict[str, object]:
    value = deepcopy(result)

    def replace_references(item: object, replacements: dict[str, str]) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if isinstance(nested, str) and nested in replacements:
                    item[key] = replacements[nested]
                else:
                    replace_references(nested, replacements)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                if isinstance(nested, str) and nested in replacements:
                    item[index] = replacements[nested]
                else:
                    replace_references(nested, replacements)

    replacements: dict[str, str] = {}
    for observation in value["observations"]:  # type: ignore[union-attr]
        old = observation["id"]
        observation["id"] = stable_result_id(
            "observation",
            {
                "capability_id": observation["capability_id"],
                "operation_id": observation["operation_id"],
                "payload": observation["payload"],
            },
        )
        replacements[old] = observation["id"]
    replace_references(value, replacements)
    replacements = {}
    for field, kind in (
        ("claims", "claim"),
        ("calculations", "calculation"),
        ("conflicts", "conflict"),
        ("limitations", "limitation"),
    ):
        for item in value[field]:  # type: ignore[index]
            old = item["id"]
            item["id"] = stable_result_id(
                kind, {key: nested for key, nested in item.items() if key != "id"}
            )
            replacements[old] = item["id"]
    replace_references(value, replacements)
    brief = value["briefs"][0]  # type: ignore[index]
    old_brief = brief["id"]
    brief["id"] = stable_result_id(
        "brief", {key: nested for key, nested in brief.items() if key != "id"}
    )
    replace_references(value, {old_brief: brief["id"]})
    return value


def validate_serialized(result: dict[str, object]) -> None:
    UnifiedResearchResult(
        status=result["status"],  # type: ignore[arg-type]
        research_anchor=result["research_anchor"],  # type: ignore[arg-type]
        plan=result["plan"],  # type: ignore[arg-type]
        capability_registry=result["capability_registry"],  # type: ignore[arg-type]
        coverage=result["coverage"],  # type: ignore[arg-type]
        observations=tuple(result["observations"]),  # type: ignore[arg-type]
        claims=tuple(result["claims"]),  # type: ignore[arg-type]
        calculations=tuple(result["calculations"]),  # type: ignore[arg-type]
        conflicts=tuple(result["conflicts"]),  # type: ignore[arg-type]
        limitations=tuple(result["limitations"]),  # type: ignore[arg-type]
        source_errors=tuple(result["source_errors"]),  # type: ignore[arg-type]
        task_views=result["task_views"],  # type: ignore[arg-type]
        briefs=tuple(result["briefs"]),  # type: ignore[arg-type]
    ).to_dict()


def rehash_direct_plan(result: dict[str, object]) -> None:
    plan = result["plan"]  # type: ignore[assignment]
    plan["id"] = stable_result_id(
        "plan",
        {
            "version": plan["version"],
            "task_type": plan["task_type"],
            "operation_obligations": plan["operation_obligations"],
            "research_dependencies": plan["research_dependencies"],
            "field_dispositions": [
                {
                    "capability_id": capability_id,
                    "operation_id": operation_id,
                    "fields": operation["field_dispositions"],
                }
                for capability_id, capability in result["coverage"].items()  # type: ignore[union-attr]
                for operation_id, operation in capability["operations"].items()
            ],
        },
    )


def aggregate_state(operations: dict[str, dict[str, object]]) -> str:
    states = [operation["state"] for operation in operations.values()]
    if len(set(states)) == 1:
        return states[0]  # type: ignore[return-value]
    if "failed" in states or "partial" in states:
        return "failed" if all(state == "failed" for state in states) else "partial"
    if any(
        state in {"not_requested", "skipped_policy", "skipped_research_dependency"}
        for state in states
    ):
        return "partial"
    if "complete" in states:
        return "complete"
    return "observed_empty"


def content_request(*, allow_experimental: bool) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "research_content",
        "subjects": [],
        "as_of": "2026-08-02",
        "window": {
            "published_from": "2026-07-01",
            "published_to": "2026-08-02",
        },
        "parameters": {
            "material_types": ["research_report"],
            "query": ["研究"],
            "limit": 20,
        },
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def invoke_copied_installed(
    request: dict[str, object], *, fixture: bool = False
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        installed = temporary_root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = temporary_root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        environment = os.environ.copy()
        command = [sys.executable]
        if fixture:
            identity_fixtures = temporary_root / "identity-fixtures"
            shutil.copytree(IDENTITY_FIXTURES, identity_fixtures)
            sitecustomize = temporary_root / "sitecustomize.py"
            sitecustomize.write_text(
                textwrap.dedent(
                    f"""
                    import sys
                    from datetime import datetime, timedelta, timezone
                    from urllib.parse import urlsplit

                    sys.path.insert(0, {str(installed / "scripts")!r})
                    from a_share_research.content_contract import ContentObservation, SourceBatch, SourceFailure
                    from a_share_research.identity_sources import HttpResponse
                    import a_share_research.cli as cli

                    retrieved_at = datetime(2026, 8, 2, 19, 30, tzinfo=timezone(timedelta(hours=8)))
                    identity_fixtures = __import__('pathlib').Path({str(identity_fixtures)!r})

                    class FixtureIdentityTransport:
                        def get(self, url, headers):
                            host = urlsplit(url).netloc
                            if host == "query.sse.com.cn":
                                body = b'{{"result":[]}}'
                            elif host == "www.szse.cn":
                                body = (identity_fixtures / "szse_300058.json").read_bytes()
                            elif host == "www.cninfo.com.cn":
                                body = (identity_fixtures / "cninfo_current_orgs.json").read_bytes()
                            else:
                                raise AssertionError("unexpected identity source")
                            return HttpResponse(200, "application/json", body, retrieved_at)

                    class FixedOperation:
                        supported_material_types = frozenset({{
                            "research_report", "announcement", "investor_qa", "issuer_profile", "market_flash"
                        }})
                        raw_fields = ("report_id", "title", "published_at", "summary", "locator")

                        def __init__(self, operation_id, title):
                            self.operation_id = operation_id
                            self.title = title

                        def collect(self, query):
                            roles = {{
                                "research_report": "attributed_opinion",
                                "announcement": "authoritative_disclosure",
                                "investor_qa": "attributed_opinion",
                                "issuer_profile": "market_observation",
                                "market_flash": "market_signal",
                            }}
                            observations = []
                            for material_type in query.material_types:
                                if material_type not in self.supported_material_types:
                                    continue
                                unknown_time = material_type == "issuer_profile"
                                observations.append(ContentObservation(
                                    material_type=material_type,
                                    source_operation=self.operation_id,
                                    source_role=roles[material_type],
                                    source_document_namespace=f"fixture-{{material_type}}",
                                    source_document_id=f"{{material_type}}-1",
                                    title=self.title if material_type == "research_report" else f"{{material_type}} title",
                                    published_at=None if unknown_time else "2026-07-31T09:00:00+08:00",
                                    retrieved_at=retrieved_at,
                                    locator_uri=f"https://fixture.test/{{self.operation_id}}/{{material_type}}-1",
                                    subject=query.subject,
                                    author="Fixture Research",
                                    summary=f"Auditable {{material_type}} summary",
                                    document_locator=f"https://fixture.test/{{self.operation_id}}/{{material_type}}-1.pdf",
                                    attributes={{"license": "source_terms"}},
                                    limitations=("publication_time_unknown",) if unknown_time else (),
                                    content=None,
                                ))
                            return SourceBatch(
                                operation_id=self.operation_id,
                                observations=tuple(observations),
                                complete=True,
                            )

                    class FailingOperation:
                        operation_id = "failed_reports@1"
                        supported_material_types = frozenset({{"research_report"}})
                        raw_fields = ("report_id", "title")

                        def collect(self, query):
                            return SourceBatch(
                                operation_id=self.operation_id,
                                source_errors=(SourceFailure(
                                    self.operation_id,
                                    "upstream_unavailable",
                                    "The fixture source is unavailable.",
                                ),),
                            )

                    class WindowOwnershipOperation(FixedOperation):
                        operation_id = "window_ownership_reports@1"

                        def __init__(self):
                            super().__init__(self.operation_id, "Inside-window report")

                        def collect(self, query):
                            inside = super().collect(query).observations[0]
                            outside = ContentObservation(
                                material_type="research_report",
                                source_operation=self.operation_id,
                                source_role="attributed_opinion",
                                source_document_namespace="fixture-research_report",
                                source_document_id="research_report-outside",
                                title="Outside-window report",
                                published_at="2026-06-30T09:00:00+08:00",
                                retrieved_at=retrieved_at,
                                locator_uri="https://fixture.test/window_ownership_reports@1/outside",
                                subject=query.subject,
                                author="Fixture Research",
                                summary="Contract-valid material outside the business window",
                                document_locator=None,
                                attributes={{"license": "source_terms"}},
                                limitations=(),
                                content=None,
                            )
                            return SourceBatch(
                                operation_id=self.operation_id,
                                observations=(inside, outside),
                                complete=True,
                            )

                    class FallbackFlashOperation(FixedOperation):
                        operation_id = "flash_fallback@1"
                        fallback_for = "flash_primary@1"
                        supported_material_types = frozenset({{"market_flash"}})

                        def __init__(self):
                            super().__init__(self.operation_id, "Fallback flash")

                    class FailingFlashOperation:
                        operation_id = "flash_primary@1"
                        supported_material_types = frozenset({{"market_flash"}})
                        raw_fields = ("code", "message")

                        def collect(self, query):
                            return SourceBatch(
                                operation_id=self.operation_id,
                                source_errors=(SourceFailure(
                                    self.operation_id,
                                    "upstream_unavailable",
                                    "The primary flash source is unavailable.",
                                ),),
                                complete=False,
                            )

                    class PartialReportOperation(FixedOperation):
                        operation_id = "partial_reports@1"

                        def __init__(self):
                            super().__init__(self.operation_id, "Partial report")

                        def collect(self, query):
                            batch = super().collect(query)
                            return SourceBatch(
                                operation_id=self.operation_id,
                                observations=batch.observations,
                                source_errors=(SourceFailure(
                                    self.operation_id,
                                    "pagination_incomplete",
                                    "A later page was unavailable.",
                                ),),
                                complete=False,
                            )

                    class EmptyReportOperation:
                        operation_id = "empty_reports@1"
                        supported_material_types = frozenset({{"research_report"}})
                        raw_fields = ("report_id", "title")

                        def collect(self, query):
                            return SourceBatch(operation_id=self.operation_id)

                    class NonApplicableOperation(EmptyReportOperation):
                        operation_id = "non_applicable_announcements@1"
                        supported_material_types = frozenset({{"announcement"}})

                    original_research = cli.research
                    def fixture_research(request, **kwargs):
                        if request["parameters"]["material_types"] == ["market_flash"]:
                            primary = (
                                FailingFlashOperation()
                                if request["parameters"].get("query") == ["force-fallback"]
                                else FixedOperation("flash_primary@1", "Primary flash")
                            )
                            operations = (
                                (FallbackFlashOperation(), primary)
                                if request["parameters"].get("query") == ["reverse-primary-success"]
                                else (primary, FallbackFlashOperation())
                            )
                        elif request["parameters"].get("query") == ["window-ownership"]:
                            operations = (WindowOwnershipOperation(),)
                        elif request["parameters"].get("query") == ["truth-table"]:
                            operations = (
                                FixedOperation("complete_reports@1", "Complete report"),
                                PartialReportOperation(),
                                FailingOperation(),
                                EmptyReportOperation(),
                                NonApplicableOperation(),
                            )
                        else:
                            operations = (
                                FixedOperation("report_source_a@1", "Report title A"),
                                FixedOperation("report_source_b@1", "Report title B"),
                                FailingOperation(),
                            )
                        return original_research(
                            request,
                            identity_transport=FixtureIdentityTransport(),
                            content_operations=operations,
                        )
                    cli.research = fixture_research
                    """
                ),
                encoding="utf-8",
            )
            environment["PYTHONPATH"] = str(temporary_root)
            environment["PYTHONNOUSERSITE"] = "1"
        else:
            command.append("-I")
        command.extend(
            [
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ]
        )
        completed = subprocess.run(
            command,
            cwd=temporary_root,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise AssertionError("public result must be a JSON object")
    return result


class DirectResearchContentPublicSeamTests(unittest.TestCase):
    def test_content_authority_rejects_coherent_capability_rebinding(self) -> None:
        result = invoke_copied_installed(content_request(allow_experimental=False))
        source_capability = "research_content_materials"
        target_capability = "forged_content_materials"
        operation_id = "eastmoney_reports@1"

        source_coverage = result["coverage"][source_capability]
        operation = source_coverage["operations"].pop(operation_id)
        operation["capability_id"] = target_capability
        for disposition in operation["field_dispositions"]:
            destination = disposition["destination"]
            if isinstance(destination, str) and destination.startswith(
                f"coverage.{source_capability}."
            ):
                disposition["destination"] = destination.replace(
                    f"coverage.{source_capability}.",
                    f"coverage.{target_capability}.",
                    1,
                )
            if disposition["field"] == "data[].stockCode":
                disposition["destination"] = "observations[].payload.title"
        source_coverage["state"] = aggregate_state(source_coverage["operations"])
        result["coverage"][target_capability] = {
            "state": operation["state"],
            "operations": {operation_id: operation},
        }

        source_registration = result["capability_registry"][source_capability]
        registration = next(
            item
            for item in source_registration["operations"]
            if item["id"] == operation_id
        )
        source_registration["operations"].remove(registration)
        result["capability_registry"][target_capability] = {
            "id": target_capability,
            "version": source_registration["version"],
            "granularity": source_registration["granularity"],
            "operations": [registration],
        }
        result["plan"]["capabilities"].append(target_capability)
        next(
            item
            for item in result["plan"]["operation_obligations"]
            if item["operation_id"] == operation_id
        )["capability_id"] = target_capability
        rehash_direct_plan(result)

        with self.assertRaisesRegex(ValueError, "content operation capability"):
            validate_serialized(result)

    def test_content_authority_rejects_rehashed_existing_sibling_destination(
        self,
    ) -> None:
        result = invoke_copied_installed(content_request(allow_experimental=False))
        operation = result["coverage"]["research_content_materials"]["operations"][
            "eastmoney_reports@1"
        ]
        retained = next(
            item
            for item in operation["field_dispositions"]
            if item["destination"] is not None
            and item["destination"].startswith("observations[].payload.")
        )
        retained["destination"] = "observations[].payload.title"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(ValueError, "field disposition contract"):
            validate_serialized(result)

    def test_copied_installed_policy_block_is_the_formal_unified_result(self) -> None:
        result = invoke_copied_installed(content_request(allow_experimental=False))

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["plan"]["task_type"], "research_content")
        self.assertEqual(result["status"], "blocked")
        coverage = result["coverage"]["research_content_materials"]
        self.assertEqual(
            coverage["operations"]["eastmoney_reports@1"]["state"],
            "skipped_policy",
        )

    def test_copied_installed_policy_coverage_uses_static_report_raw_fields(
        self,
    ) -> None:
        result = invoke_copied_installed(content_request(allow_experimental=False))

        operation = result["coverage"]["research_content_materials"]["operations"][
            "eastmoney_reports@1"
        ]
        self.assertEqual(
            {item["field"] for item in operation["field_dispositions"]},
            set(EastmoneyReportOperation.raw_fields),
        )
        stock_code = next(
            item
            for item in operation["field_dispositions"]
            if item["field"] == "data[].stockCode"
        )
        self.assertEqual(
            stock_code["destination"],
            "observations[].payload.attributes.provider_stock_code",
        )
        for disposition in operation["field_dispositions"]:
            if disposition["disposition"] == "excluded":
                self.assertIsNone(disposition["destination"])
                self.assertTrue(disposition["reason"])
            else:
                self.assertIsInstance(disposition["destination"], str)
                self.assertNotEqual(
                    disposition["destination"],
                    "observations.payload.normalized_material",
                )
                self.assertTrue(
                    disposition["destination"].startswith(
                        ("observations[].payload.", "coverage.")
                    )
                )

    def test_every_default_operation_declares_static_raw_fields_used_when_skipped(
        self,
    ) -> None:
        class NoCallTransport:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("policy skip must not fetch")

            def post(self, url: str, headers: dict[str, str], body: bytes) -> object:
                raise AssertionError("policy skip must not fetch")

        operations = build_default_content_operations(
            NoCallTransport(),  # type: ignore[arg-type]
            allow_credentials=False,
            allow_fallback=True,
            research_now=datetime(
                2026, 8, 2, 19, 30, tzinfo=timezone(timedelta(hours=8))
            ),
        )
        result = invoke_copied_installed(content_request(allow_experimental=False))
        covered = {
            operation_id: operation
            for capability in result["coverage"].values()
            for operation_id, operation in capability["operations"].items()
        }

        for operation in operations:
            with self.subTest(operation=operation.operation_id):
                self.assertTrue(operation.raw_fields)
                self.assertEqual(
                    {
                        item["field"]
                        for item in covered[operation.operation_id][
                            "field_dispositions"
                        ]
                    },
                    set(operation.raw_fields),
                )

    def test_copied_installed_partial_success_retains_operation_owned_members(
        self,
    ) -> None:
        result = invoke_copied_installed(
            content_request(allow_experimental=True), fixture=True
        )

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["plan"]["task_type"], "research_content")
        observations = result["observations"]
        self.assertEqual(len(observations), 2)
        self.assertEqual(
            {item["operation_id"] for item in observations},
            {"report_source_a@1", "report_source_b@1"},
        )
        for item in observations:
            self.assertEqual(item["payload"]["source_operation"], item["operation_id"])
            self.assertNotIn("raw", item["payload"])
            self.assertEqual(
                item["payload"]["query_scope"],
                {
                    "as_of": "2026-08-02",
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                    "material_types": ["research_report"],
                    "query": ["研究"],
                    "limit": 20,
                    "verify_documents": False,
                },
            )
        material_claims = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        ]
        self.assertEqual(len(material_claims), 1)
        self.assertEqual(
            set(material_claims[0]["observation_ids"]),
            {item["id"] for item in observations},
        )
        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(operations["report_source_a@1"]["state"], "complete")
        self.assertEqual(operations["report_source_b@1"]["state"], "complete")
        self.assertEqual(operations["failed_reports@1"]["state"], "failed")
        self.assertEqual(
            {item["operation_id"] for item in result["source_errors"]},
            {"failed_reports@1"},
        )

    def test_serializer_rejects_rehashed_group_and_window_forgery(self) -> None:
        result = invoke_copied_installed(
            content_request(allow_experimental=True), fixture=True
        )
        forged_group = deepcopy(result)
        group = next(
            item
            for item in forged_group["claims"]  # type: ignore[index]
            if item["kind"] == "research_content_material_group"
        )
        group["payload"]["member_count"] = 99
        with self.assertRaisesRegex(ValueError, "research.content|material group"):
            validate_serialized(rehash_result(forged_group))

        forged_window = deepcopy(result)
        material = next(
            item
            for item in forged_window["observations"]  # type: ignore[index]
            if item["payload"].get("kind") == "research_content_material"
        )
        material["payload"]["published_at"] = "2026-06-30T09:00:00+08:00"
        material["payload"]["available_at"] = "2026-06-30T09:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "research.content|window"):
            validate_serialized(rehash_result(forged_window))

    def test_serializer_rejects_rehashed_subject_identity_forgery(self) -> None:
        request = content_request(allow_experimental=True)
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
        }
        forged = invoke_copied_installed(request, fixture=True)
        for observation in forged["observations"]:
            payload = observation["payload"]
            if payload.get("kind") == "research_content_material":
                payload["subject"]["name"] = "伪造证券"
        identity_claim = next(
            item
            for item in forged["claims"]
            if item["kind"] == "research_content_subject_identity"
        )
        identity_claim["payload"]["name"] = "伪造证券"

        with self.assertRaisesRegex(ValueError, "research.content|identity"):
            validate_serialized(rehash_result(forged))

    def test_copied_installed_fallback_is_not_attempted_after_primary_success(
        self,
    ) -> None:
        request = content_request(allow_experimental=True)
        request["parameters"] = {
            "material_types": ["market_flash"],
            "query": [],
            "limit": 20,
        }

        result = invoke_copied_installed(request, fixture=True)

        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(operations["flash_primary@1"]["state"], "complete")
        self.assertEqual(operations["flash_fallback@1"]["state"], "not_requested")
        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [item["operation_id"] for item in materials], ["flash_primary@1"]
        )

    def test_copied_installed_compatible_fallback_keeps_primary_failure_and_child_owner(
        self,
    ) -> None:
        request = content_request(allow_experimental=True)
        request["parameters"] = {
            "material_types": ["market_flash"],
            "query": ["force-fallback"],
            "limit": 20,
        }

        result = invoke_copied_installed(request, fixture=True)

        self.assertEqual(result["status"], "limited")
        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(operations["flash_primary@1"]["state"], "failed")
        self.assertEqual(operations["flash_fallback@1"]["state"], "complete")
        material = next(
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        )
        self.assertEqual(material["operation_id"], "flash_fallback@1")
        self.assertEqual(material["payload"]["source_operation"], "flash_fallback@1")
        self.assertEqual(
            {item["operation_id"] for item in result["source_errors"]},
            {"flash_primary@1"},
        )

    def test_copied_installed_fallback_selection_is_input_order_independent(
        self,
    ) -> None:
        request = content_request(allow_experimental=True)
        request["parameters"] = {
            "material_types": ["market_flash"],
            "query": ["reverse-primary-success"],
            "limit": 20,
        }

        result = invoke_copied_installed(request, fixture=True)

        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(operations["flash_primary@1"]["state"], "complete")
        self.assertEqual(operations["flash_fallback@1"]["state"], "not_requested")
        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [item["operation_id"] for item in materials], ["flash_primary@1"]
        )

    def test_copied_installed_operation_coverage_truth_table(self) -> None:
        request = content_request(allow_experimental=True)
        request["parameters"]["query"] = ["truth-table"]

        result = invoke_copied_installed(request, fixture=True)

        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(
            {
                operation_id: operation["state"]
                for operation_id, operation in operations.items()
            },
            {
                "complete_reports@1": "complete",
                "partial_reports@1": "partial",
                "failed_reports@1": "failed",
                "empty_reports@1": "observed_empty",
                "non_applicable_announcements@1": "not_requested",
            },
        )
        partial_ids = {
            item["id"]
            for item in result["observations"]
            if item["operation_id"] == "partial_reports@1"
        }
        self.assertTrue(partial_ids)
        self.assertTrue(
            any(
                partial_ids.intersection(claim["observation_ids"])
                for claim in result["claims"]
                if claim["kind"] == "research_content_material_group"
            )
        )
        for operation_id, operation in operations.items():
            self.assertEqual(
                len(operation["field_dispositions"]),
                len({item["field"] for item in operation["field_dispositions"]}),
                operation_id,
            )
            self.assertNotIn(
                "observations.payload.normalized_material",
                {item["destination"] for item in operation["field_dispositions"]},
                operation_id,
            )

    def test_copied_installed_retains_owned_material_before_window_adoption(
        self,
    ) -> None:
        request = content_request(allow_experimental=True)
        request["parameters"]["query"] = ["window-ownership"]

        result = invoke_copied_installed(request, fixture=True)

        materials = [
            item
            for item in result["observations"]
            if item["operation_id"] == "window_ownership_reports@1"
            and item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [item["payload"]["title"] for item in materials],
            ["Inside-window report", "Outside-window report"],
        )
        for item in materials:
            self.assertEqual(
                item["payload"]["query_scope"],
                {
                    "as_of": "2026-08-02",
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                    "material_types": ["research_report"],
                    "query": ["window-ownership"],
                    "limit": 20,
                    "verify_documents": False,
                },
            )
        coverage = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(
            coverage["window_ownership_reports@1"]["observation_ids"],
            [item["id"] for item in materials],
        )
        group_claims = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        ]
        self.assertEqual(len(group_claims), 1)
        self.assertEqual(group_claims[0]["observation_ids"], [materials[0]["id"]])
        self.assertEqual(
            group_claims[0]["evidence_adoptions"],
            [
                {
                    "observation_id": materials[0]["id"],
                    "use": "research_content_material",
                    "decision": "adopted",
                    "role": "supporting",
                    "reason": None,
                }
            ],
        )
        self.assertEqual(result["status"], "limited")

    def test_serializer_rejects_rehashed_unknown_retained_observation_kind(
        self,
    ) -> None:
        request = content_request(allow_experimental=True)
        request["parameters"]["query"] = ["window-ownership"]
        result = invoke_copied_installed(request, fixture=True)
        forged = deepcopy(result)
        outside = next(
            item
            for item in forged["observations"]  # type: ignore[index]
            if item["payload"].get("title") == "Outside-window report"
        )
        outside["payload"]["kind"] = "unknown_research_content_material"

        with self.assertRaisesRegex(ValueError, "observation|kind|schema"):
            validate_serialized(rehash_result(forged))

    def test_copied_installed_subject_materials_retain_canonical_identity_and_times(
        self,
    ) -> None:
        request = content_request(allow_experimental=True)
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement", "investor_qa", "issuer_profile"],
            "query": [],
            "limit": 20,
        }

        result = invoke_copied_installed(request, fixture=True)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            {item["payload"]["material_type"] for item in materials},
            {"announcement", "investor_qa", "issuer_profile"},
        )
        for item in materials:
            payload = item["payload"]
            self.assertEqual(
                payload["subject"]["security"],
                {"exchange": "SZSE", "code": "300058", "type": "A_SHARE"},
            )
            self.assertIn("available_at", payload)
            self.assertIn("retrieved_at", payload)
            self.assertEqual(payload["license_boundary"]["raw_payload"], "excluded")

    def test_copied_installed_identity_block_skips_content_dependency(self) -> None:
        request = content_request(allow_experimental=True)
        request["subjects"] = [{"clue": "不存在的证券"}]
        request["parameters"] = {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
        }

        result = invoke_copied_installed(request, fixture=True)

        self.assertEqual(result["status"], "blocked")
        content_operations = result["coverage"]["research_content_materials"][
            "operations"
        ]
        self.assertEqual(
            content_operations["report_source_a@1"]["state"],
            "skipped_research_dependency",
        )
        self.assertEqual(
            content_operations["report_source_b@1"]["state"],
            "skipped_research_dependency",
        )


if __name__ == "__main__":
    unittest.main()
