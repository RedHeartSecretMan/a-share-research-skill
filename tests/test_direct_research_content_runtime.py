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
ISSUE_62_FAKE_IWENCAI_CREDENTIAL = "issue-62-named-fixture-credential"


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
    request: dict[str, object],
    *,
    fixture: bool = False,
    formal_mootdx_f10: bool = False,
    issue_62_scenario: str | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        installed = temporary_root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = temporary_root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        environment = os.environ.copy()
        command = [sys.executable]
        if issue_62_scenario is not None:
            identity_fixtures = temporary_root / "identity-fixtures"
            shutil.copytree(IDENTITY_FIXTURES, identity_fixtures)
            content_fixtures = temporary_root / "content-fixtures"
            shutil.copytree(
                REPOSITORY_ROOT / "tests" / "fixtures" / "research_content",
                content_fixtures,
            )
            shutil.copy2(
                REPOSITORY_ROOT
                / "tests"
                / "fixtures"
                / "research_content"
                / "issue_62"
                / "sitecustomize.py",
                temporary_root / "sitecustomize.py",
            )
            environment.update(
                {
                    "PYTHONPATH": str(temporary_root),
                    "PYTHONNOUSERSITE": "1",
                    "A_SHARE_ISSUE62_INSTALLED_SCRIPTS": str(installed / "scripts"),
                    "A_SHARE_ISSUE62_IDENTITY_FIXTURES": str(identity_fixtures),
                    "A_SHARE_ISSUE62_CONTENT_FIXTURES": str(content_fixtures),
                    "A_SHARE_ISSUE62_SCENARIO": issue_62_scenario,
                    "IWENCAI_API_KEY": ISSUE_62_FAKE_IWENCAI_CREDENTIAL,
                    "IWENCAI_BASE_URL": "https://iwencai.fixture.test",
                }
            )
        elif fixture:
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
                    from a_share_research.communication_sources import MootdxF10Operation
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

                    class FixedMootdxF10Client:
                        def F10(self, *, symbol, name):
                            if symbol != "300058" or name != "公司概况":
                                raise AssertionError("unexpected formal Mootdx F10 request")
                            return "蓝色光标公司概况公开聚合文本"

                    original_research = cli.research
                    def fixture_research(request, **kwargs):
                        if {formal_mootdx_f10!r}:
                            operations = (MootdxF10Operation(
                                client_factory=FixedMootdxF10Client,
                                clock=lambda: retrieved_at,
                            ),)
                        elif request["parameters"]["material_types"] == ["market_flash"]:
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
    if issue_62_scenario is not None and ISSUE_62_FAKE_IWENCAI_CREDENTIAL in (
        completed.stdout + completed.stderr
    ):
        raise AssertionError("named fixture credential escaped the public run seam")
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise AssertionError("public result must be a JSON object")
    return result


class DirectResearchContentPublicSeamTests(unittest.TestCase):
    def test_issue_62_copied_installed_bluefocus_qualifies_four_formal_owners(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": "2026-08-02",
            "window": {
                "published_from": "2026-07-01",
                "published_to": "2026-08-02",
            },
            "parameters": {
                "material_types": ["announcement", "stock_news"],
                "query": [],
                "limit": 20,
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        result = invoke_copied_installed(
            request,
            issue_62_scenario="bluefocus_disclosures_news",
        )
        expected_owners = {
            "eastmoney_stock_news@1",
            "cninfo_announcement@1",
            "sse_announcement@1",
            "szse_announcement@1",
        }

        obligations = {
            item["producer_operation_id"]: item
            for item in result["plan"]["operation_obligations"]
            if item["producer_operation_id"] in expected_owners
        }
        self.assertEqual(set(obligations), expected_owners)
        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(
            {
                owner: (
                    operations[owner]["execution_state"],
                    operations[owner]["state"],
                )
                for owner in expected_owners
            },
            {
                "eastmoney_stock_news@1": ("succeeded", "complete"),
                "cninfo_announcement@1": ("succeeded", "complete"),
                "sse_announcement@1": ("succeeded", "observed_empty"),
                "szse_announcement@1": ("succeeded", "complete"),
            },
        )
        expected_retained_destinations = {
            "eastmoney_stock_news@1": {
                "result.cmsArticleWebOld[].content": (
                    "normalized",
                    "observations[].payload.summary",
                ),
            },
            "cninfo_announcement@1": {
                "stockList[].code": (
                    "cross_check_only",
                    "observations[].payload.subject.security.code",
                ),
                "stockList[].zwjc": (
                    "cross_check_only",
                    "observations[].payload.subject.name",
                ),
                "stockList[].orgId": (
                    "cross_check_only",
                    "observations[].payload.attributes.cninfo_org_id",
                ),
                "announcements[].adjunctSize": (
                    "normalized",
                    "observations[].payload.attributes.adjunct_size",
                ),
                "announcements[].announcementType": (
                    "normalized",
                    "observations[].payload.attributes.announcement_type",
                ),
                "announcements[].announcementTypeName": (
                    "normalized",
                    "observations[].payload.attributes.announcement_type_name",
                ),
                "announcements[].orgId": (
                    "cross_check_only",
                    "observations[].payload.attributes.cninfo_org_id",
                ),
            },
            "sse_announcement@1": {
                "beginDate": (
                    "cross_check_only",
                    "observations[].payload.query_scope.published_from",
                ),
                "endDate": (
                    "cross_check_only",
                    "observations[].payload.query_scope.published_to",
                ),
                "productId": (
                    "cross_check_only",
                    "observations[].payload.subject.security.code",
                ),
                "result[].BULLETIN_HEADING": (
                    "normalized",
                    "observations[].payload.attributes.bulletin_heading",
                ),
                "result[].BULLETIN_TYPE": (
                    "normalized",
                    "observations[].payload.attributes.bulletin_type",
                ),
                "result[].SSEDATE": (
                    "normalized",
                    "observations[].payload.attributes.sse_disclosure_date",
                ),
            },
            "szse_announcement@1": {
                "data[].id": (
                    "normalized",
                    "observations[].payload.attributes.szse_index_id",
                ),
                "data[].attachSize": (
                    "normalized",
                    "observations[].payload.attributes.attach_size",
                ),
            },
        }
        for owner, expected in expected_retained_destinations.items():
            with self.subTest(owner=owner):
                actual = {
                    item["field"]: (item["disposition"], item["destination"])
                    for item in operations[owner]["field_dispositions"]
                }
                self.assertEqual(
                    {field: actual[field] for field in expected},
                    expected,
                )
        materials = [
            item
            for item in result["observations"]
            if item["operation_id"] in expected_owners
        ]
        self.assertEqual(
            {item["operation_id"] for item in materials},
            expected_owners - {"sse_announcement@1"},
        )
        for item in materials:
            payload = item["payload"]
            self.assertEqual(payload["source_operation"], item["operation_id"])
            self.assertTrue(payload["source_document_id"])
            self.assertTrue(payload["published_at"])
            self.assertEqual(payload["available_at"], payload["published_at"])
            self.assertTrue(payload["retrieved_at"])
            self.assertTrue(payload["locator"]["uri"])
            self.assertEqual(
                payload["license_boundary"],
                {"status": "not_established", "raw_payload": "excluded"},
            )
        announcement_groups = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
            and item["payload"]["material_type"] == "announcement"
        ]
        self.assertEqual(len(announcement_groups), 3)
        self.assertEqual(
            {item["payload"]["member_count"] for item in announcement_groups},
            {2},
        )
        grouped_observation_ids = {
            observation_id
            for item in announcement_groups
            for observation_id in item["observation_ids"]
        }
        announcement_observation_ids = {
            item["id"]
            for item in materials
            if item["payload"]["material_type"] == "announcement"
        }
        self.assertEqual(grouped_observation_ids, announcement_observation_ids)
        metadata_conflicts = [
            item
            for item in result["conflicts"]
            if item["code"] == "research_content_metadata_conflict"
        ]
        self.assertEqual(len(metadata_conflicts), 3)
        self.assertEqual(
            {tuple(sorted(item["observation_ids"])) for item in metadata_conflicts},
            {tuple(sorted(item["observation_ids"])) for item in announcement_groups},
        )
        same_title_news = [
            item
            for item in materials
            if item["operation_id"] == "eastmoney_stock_news@1"
            and item["payload"]["title"] == "蓝色光标公告观察"
        ]
        self.assertEqual(
            {item["payload"]["source_document_id"] for item in same_title_news},
            {"NEWS-2", "NEWS-3"},
        )
        news_groups = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
            and item["payload"]["material_type"] == "stock_news"
        ]
        self.assertEqual(len(news_groups), 3)
        self.assertEqual(
            {
                item["payload"]["source_document_id"]
                for item in news_groups
                if item["payload"]["source_document_id"] in {"NEWS-2", "NEWS-3"}
            },
            {"NEWS-2", "NEWS-3"},
        )
        self.assertTrue(
            all(item["payload"]["member_count"] == 1 for item in news_groups)
        )

        cninfo = next(
            item
            for item in materials
            if item["operation_id"] == "cninfo_announcement@1"
        )
        for field, forged_value in (
            ("source_role", "market_signal"),
            (
                "license_boundary",
                {"status": "source_terms", "raw_payload": "excluded"},
            ),
        ):
            with self.subTest(field=field):
                forged = deepcopy(result)
                forged_cninfo = next(
                    item
                    for item in forged["observations"]
                    if item["id"] == cninfo["id"]
                )
                forged_cninfo["payload"][field] = forged_value
                with self.assertRaisesRegex(ValueError, "research.content|owner"):
                    validate_serialized(rehash_result(forged))

    def test_issue_62_copied_installed_sse_material_survives_cninfo_failure(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [{"clue": "工业富联"}],
            "as_of": "2026-08-02",
            "window": {
                "published_from": "2026-07-01",
                "published_to": "2026-08-02",
            },
            "parameters": {
                "material_types": ["announcement"],
                "query": [],
                "limit": 20,
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        result = invoke_copied_installed(
            request,
            issue_62_scenario="sse_announcement_with_cninfo_failure",
        )
        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(
            (
                operations["cninfo_announcement@1"]["execution_state"],
                operations["cninfo_announcement@1"]["state"],
            ),
            ("failed", "failed"),
        )
        self.assertEqual(
            (
                operations["sse_announcement@1"]["execution_state"],
                operations["sse_announcement@1"]["state"],
            ),
            ("succeeded", "complete"),
        )
        self.assertEqual(
            {
                (item["operation_id"], item["code"])
                for item in result["source_errors"]
                if item["operation_id"] == "cninfo_announcement@1"
            },
            {("cninfo_announcement@1", "wrong_security_payload")},
        )
        materials = [
            item
            for item in result["observations"]
            if item["operation_id"] == "sse_announcement@1"
        ]
        self.assertEqual(len(materials), 3)
        self.assertEqual(
            {item["payload"]["source_document_id"] for item in materials},
            {
                "601138_20260802_A1",
                "601138_20260801_A2",
                "601138_20260730_A3",
            },
        )
        for item in materials:
            payload = item["payload"]
            self.assertEqual(payload["source_role"], "authoritative_disclosure")
            self.assertTrue(payload["published_at"])
            self.assertEqual(payload["available_at"], payload["published_at"])
            self.assertTrue(payload["retrieved_at"])
            self.assertTrue(payload["locator"]["uri"].startswith("source-document:"))
            self.assertTrue(
                payload["document_locator"]["uri"].startswith(
                    "source-document-content:"
                )
            )
            self.assertIsNone(payload["summary"])
            self.assertEqual(
                payload["license_boundary"],
                {"status": "not_established", "raw_payload": "excluded"},
            )

    def test_issue_62_serializer_revalidates_owner_roles_and_license_derivation(
        self,
    ) -> None:
        def announcement_request(
            clue: str, *, allow_credentials: bool
        ) -> dict[str, object]:
            return {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": clue}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": ["公司公告"] if allow_credentials else [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": allow_credentials,
                    "allow_fallback": True,
                },
            }

        bluefocus = invoke_copied_installed(
            announcement_request("蓝色光标", allow_credentials=False),
            issue_62_scenario="bluefocus_disclosures_news",
        )
        sse = invoke_copied_installed(
            announcement_request("工业富联", allow_credentials=False),
            issue_62_scenario="sse_announcement_with_cninfo_failure",
        )
        iwencai = invoke_copied_installed(
            announcement_request("蓝色光标", allow_credentials=True),
            issue_62_scenario="iwencai_announcement",
        )
        cases = (
            (bluefocus, "cninfo_announcement@1", "market_observation"),
            (bluefocus, "szse_announcement@1", "market_observation"),
            (sse, "sse_announcement@1", "market_observation"),
            (
                iwencai,
                "iwencai_content_search@1",
                "authoritative_disclosure",
            ),
        )
        for result, owner, forged_role in cases:
            with self.subTest(owner=owner):
                forged = deepcopy(result)
                material = next(
                    item
                    for item in forged["observations"]
                    if item["operation_id"] == owner
                )
                material["payload"]["source_role"] = forged_role
                with self.assertRaisesRegex(
                    ValueError, "research content material owner"
                ):
                    validate_serialized(rehash_result(forged))

        forged_license = deepcopy(bluefocus)
        cninfo = next(
            item
            for item in forged_license["observations"]
            if item["operation_id"] == "cninfo_announcement@1"
        )
        cninfo["payload"]["attributes"]["license"] = "source_terms"
        with self.assertRaisesRegex(ValueError, "research content material owner"):
            validate_serialized(rehash_result(forged_license))

    def test_issue_62_serializer_binds_cninfo_fallback_locators_to_document_identity(
        self,
    ) -> None:
        result = invoke_copied_installed(
            {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                },
            },
            issue_62_scenario="bluefocus_disclosures_news",
        )
        cninfo = next(
            item
            for item in result["observations"]
            if item["operation_id"] == "cninfo_announcement@1"
        )
        self.assertTrue(
            cninfo["payload"]["locator"]["uri"].startswith("source-document:")
        )
        self.assertTrue(
            cninfo["payload"]["document_locator"]["uri"].startswith(
                "source-document-content:"
            )
        )

        for forge_kind, field, forged_uri in (
            (
                "reserved_fallback",
                "locator",
                "source-document:000000000000000000000000",
            ),
            (
                "reserved_fallback",
                "document_locator",
                "source-document-content:000000000000000000000000",
            ),
            ("safe_opaque", "locator", "opaque-document:forged"),
            ("safe_opaque", "document_locator", "opaque-content:forged"),
        ):
            with self.subTest(forge_kind=forge_kind, field=field):
                forged = deepcopy(result)
                forged_cninfo = next(
                    item
                    for item in forged["observations"]
                    if item["id"] == cninfo["id"]
                )
                forged_cninfo["payload"][field]["uri"] = forged_uri
                with self.assertRaisesRegex(
                    ValueError, "research content material locator"
                ):
                    validate_serialized(rehash_result(forged))

    def test_issue_62_copied_installed_reports_qualify_three_formal_owners(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [{"clue": "工业富联"}],
            "as_of": "2026-08-02",
            "window": {
                "published_from": "2026-05-01",
                "published_to": "2026-08-02",
            },
            "parameters": {
                "material_types": ["research_report", "consensus_material"],
                "query": ["AI服务器", "算力产业链"],
                "limit": 20,
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": True,
                "allow_fallback": True,
            },
        }
        result = invoke_copied_installed(
            request,
            issue_62_scenario="reports_consensus_search",
        )
        expected_owners = {
            "eastmoney_reports@1",
            "iwencai_content_search@1",
            "ths_consensus_material@1",
        }

        obligations = {
            item["producer_operation_id"]: item
            for item in result["plan"]["operation_obligations"]
            if item["producer_operation_id"] in expected_owners
        }
        self.assertEqual(set(obligations), expected_owners)
        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(
            {
                owner: (
                    operations[owner]["execution_state"],
                    operations[owner]["state"],
                )
                for owner in expected_owners
            },
            {
                "eastmoney_reports@1": ("succeeded", "complete"),
                "iwencai_content_search@1": ("succeeded", "partial"),
                "ths_consensus_material@1": ("succeeded", "complete"),
            },
        )
        expected_retained_destinations = {
            "eastmoney_reports@1": {
                "currentYear": (
                    "normalized",
                    "observations[].payload.attributes.forecast_eps",
                ),
                "data[].stockName": (
                    "normalized",
                    "observations[].payload.attributes.provider_stock_name",
                ),
                "data[].industryCode": (
                    "normalized",
                    "observations[].payload.attributes.provider_industry_code",
                ),
                "data[].industryName": (
                    "normalized",
                    "observations[].payload.attributes.provider_industry_name",
                ),
                "data[].indvInduCode": (
                    "normalized",
                    "observations[].payload.attributes.provider_industry_code",
                ),
                "data[].indvInduName": (
                    "normalized",
                    "observations[].payload.attributes.provider_industry_name",
                ),
                "data[].predictThisYearEps": (
                    "normalized",
                    "observations[].payload.attributes.forecast_eps",
                ),
                "data[].predictNextYearEps": (
                    "normalized",
                    "observations[].payload.attributes.forecast_eps",
                ),
                "data[].predictNextTwoYearEps": (
                    "normalized",
                    "observations[].payload.attributes.forecast_eps",
                ),
                "data[].emRatingName": (
                    "normalized",
                    "observations[].payload.attributes.rating",
                ),
                "data[].reportType": (
                    "normalized",
                    "observations[].payload.attributes.report_type",
                ),
                "data[].attachPages": (
                    "normalized",
                    "observations[].payload.attributes.attachment_pages",
                ),
                "data[].attachSize": (
                    "normalized",
                    "observations[].payload.attributes.attachment_size_kib",
                ),
            },
            "iwencai_content_search@1": {
                "data[].stock_infos[].code": (
                    "cross_check_only",
                    "observations[].payload.attributes.provider_security_code",
                ),
                "data[].score": (
                    "normalized",
                    "observations[].payload.attributes.relevance_score",
                ),
            },
            "ths_consensus_material@1": {
                "security": (
                    "cross_check_only",
                    "observations[].payload.subject.security",
                ),
            },
        }
        for owner, expected in expected_retained_destinations.items():
            with self.subTest(owner=owner):
                actual = {
                    item["field"]: (item["disposition"], item["destination"])
                    for item in operations[owner]["field_dispositions"]
                }
                self.assertEqual(
                    {field: actual[field] for field in expected},
                    expected,
                )

        materials = [
            item
            for item in result["observations"]
            if item["operation_id"] in expected_owners
        ]
        self.assertEqual(len(materials), 5)
        self.assertEqual(
            {item["operation_id"] for item in materials},
            expected_owners,
        )
        for item in materials:
            payload = item["payload"]
            self.assertEqual(payload["source_operation"], item["operation_id"])
            self.assertTrue(payload["source_document_id"])
            self.assertIn("published_at", payload)
            self.assertTrue(payload["available_at"])
            self.assertTrue(payload["retrieved_at"])
            self.assertTrue(payload["locator"]["uri"])
            self.assertIn("summary", payload)
            self.assertEqual(
                payload["license_boundary"],
                {"status": "not_established", "raw_payload": "excluded"},
            )

    def test_issue_62_serializer_rejects_material_type_outside_formal_owner_authority(
        self,
    ) -> None:
        result = invoke_copied_installed(
            {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "工业富联"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-05-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["research_report", "consensus_material"],
                    "query": ["AI服务器", "算力产业链"],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": True,
                    "allow_fallback": True,
                },
            },
            issue_62_scenario="reports_consensus_search",
        )
        forged = deepcopy(result)
        report = next(
            item
            for item in forged["observations"]
            if item["operation_id"] == "eastmoney_reports@1"
        )
        report_group = next(
            item
            for item in forged["claims"]
            if item["kind"] == "research_content_material_group"
            and report["id"] in item["observation_ids"]
        )
        self.assertEqual(report["payload"]["material_type"], "research_report")
        self.assertEqual(report_group["payload"]["material_type"], "research_report")
        report["payload"]["material_type"] = "consensus_material"
        report_group["payload"]["material_type"] = "consensus_material"

        with self.assertRaisesRegex(ValueError, "research content material owner"):
            validate_serialized(rehash_result(forged))

    def test_issue_62_copied_installed_investor_qa_qualifies_formal_owner(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": "2026-08-02",
            "window": {
                "published_from": "2026-07-01",
                "published_to": "2026-08-02",
            },
            "parameters": {
                "material_types": ["investor_qa"],
                "query": [],
                "limit": 20,
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        result = invoke_copied_installed(
            request,
            issue_62_scenario="investor_qa",
        )
        owner = "cninfo_investor_qa@1"
        obligations = {
            item["producer_operation_id"]: item
            for item in result["plan"]["operation_obligations"]
            if item["producer_operation_id"] == owner
        }
        self.assertEqual(set(obligations), {owner})
        operation = result["coverage"]["research_content_materials"]["operations"][
            owner
        ]
        self.assertEqual(
            (operation["execution_state"], operation["state"]),
            ("succeeded", "complete"),
        )
        expected_retained_destinations = {
            "lookup.data[].stockCode": (
                "cross_check_only",
                "observations[].payload.subject.security.code",
            ),
            "lookup.data[].shortName": (
                "cross_check_only",
                "observations[].payload.attributes.provider_company_short_name",
            ),
            "lookup.data[].secid": (
                "cross_check_only",
                "observations[].payload.attributes.provider_org_id",
            ),
            "rows[].indexId": (
                "normalized",
                "observations[].payload.source_document_id",
            ),
            "rows[].stockCode": (
                "cross_check_only",
                "observations[].payload.subject.security.code",
            ),
            "rows[].companyShortName": (
                "cross_check_only",
                "observations[].payload.attributes.provider_company_short_name",
            ),
            "rows[].mainContent": (
                "normalized",
                "observations[].payload.summary",
            ),
            "rows[].pubDate": (
                "normalized",
                "observations[].payload.published_at",
            ),
            "rows[].updateDate": (
                "normalized",
                "observations[].payload.attributes.observed_update_at",
            ),
            "rows[].attachedId": (
                "normalized",
                "observations[].payload.source_document_id",
            ),
            "rows[].attachedContent": (
                "normalized",
                "observations[].payload.summary",
            ),
            "rows[].attachedPubDate": (
                "normalized",
                "observations[].payload.published_at",
            ),
        }
        actual = {
            item["field"]: (item["disposition"], item["destination"])
            for item in operation["field_dispositions"]
        }
        self.assertEqual(
            {field: actual[field] for field in expected_retained_destinations},
            expected_retained_destinations,
        )

        materials = [
            item for item in result["observations"] if item["operation_id"] == owner
        ]
        self.assertEqual(len(materials), 3)
        self.assertEqual(
            [item["payload"]["source_document_id"] for item in materials],
            ["question-Q-1", "question-Q-2", "answer-A-2"],
        )
        self.assertEqual(
            [item["payload"]["source_role"] for item in materials],
            ["market_observation", "market_observation", "attributed_opinion"],
        )
        self.assertEqual(
            [item["payload"]["published_at"] for item in materials],
            [
                "2026-07-31T09:09:13+08:00",
                "2026-07-30T10:00:00+08:00",
                "2026-07-31T11:00:00+08:00",
            ],
        )
        for item in materials:
            payload = item["payload"]
            self.assertEqual(payload["available_at"], payload["published_at"])
            self.assertTrue(payload["retrieved_at"])
            self.assertTrue(payload["locator"]["uri"])
            self.assertTrue(payload["summary"])
            self.assertEqual(
                payload["license_boundary"],
                {"status": "not_established", "raw_payload": "excluded"},
            )

    def test_issue_62_copied_installed_market_flash_qualifies_controlled_fallback(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [],
            "as_of": "2026-08-02",
            "window": {
                "published_from": "2026-08-02",
                "published_to": "2026-08-02",
            },
            "parameters": {
                "material_types": ["market_flash"],
                "query": [],
                "limit": 2,
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        owners = {"cls_market_flash@1", "eastmoney_market_flash@1"}
        scenarios = {
            "market_flash_primary_success": {
                "cls_market_flash@1": ("succeeded", "partial"),
                "eastmoney_market_flash@1": ("not_requested", "not_requested"),
            },
            "market_flash_fallback": {
                "cls_market_flash@1": ("failed", "failed"),
                "eastmoney_market_flash@1": ("succeeded", "partial"),
            },
        }
        results = {
            scenario: invoke_copied_installed(
                request,
                issue_62_scenario=scenario,
            )
            for scenario in scenarios
        }

        for scenario, expected_states in scenarios.items():
            with self.subTest(scenario=scenario):
                result = results[scenario]
                obligations = {
                    item["producer_operation_id"]
                    for item in result["plan"]["operation_obligations"]
                    if item["producer_operation_id"] in owners
                }
                self.assertEqual(obligations, owners)
                operations = result["coverage"]["research_content_materials"][
                    "operations"
                ]
                self.assertEqual(
                    {
                        owner: (
                            operations[owner]["execution_state"],
                            operations[owner]["state"],
                        )
                        for owner in owners
                    },
                    expected_states,
                )

        primary = results["market_flash_primary_success"]
        self.assertFalse(
            any(
                item["operation_id"] == "eastmoney_market_flash@1"
                for item in primary["observations"]
            )
        )
        fallback = results["market_flash_fallback"]
        self.assertEqual(
            {
                (item["operation_id"], item["code"])
                for item in fallback["source_errors"]
            },
            {("cls_market_flash@1", "empty_response")},
        )
        expected_retained_destinations = {
            "cls_market_flash@1": {
                "data.roll_data[].content": (
                    "normalized",
                    "observations[].payload.summary",
                ),
                "data.roll_data[].modified_time": (
                    "normalized",
                    "observations[].payload.attributes.modified_at",
                ),
                "data.roll_data[].stock_list[]": (
                    "normalized",
                    "observations[].payload.attributes.stock_list",
                ),
                "data.roll_data[].subjects[]": (
                    "normalized",
                    "observations[].payload.attributes.subjects",
                ),
            },
            "eastmoney_market_flash@1": {
                "data.fastNewsList[].realSort": (
                    "normalized",
                    "observations[].payload.attributes.provider_sort",
                ),
                "data.fastNewsList[].stockList[]": (
                    "normalized",
                    "observations[].payload.attributes.stock_list",
                ),
            },
        }
        fallback_operations = fallback["coverage"]["research_content_materials"][
            "operations"
        ]
        for owner, expected in expected_retained_destinations.items():
            with self.subTest(owner=owner):
                actual = {
                    item["field"]: (item["disposition"], item["destination"])
                    for item in fallback_operations[owner]["field_dispositions"]
                }
                self.assertEqual(
                    {field: actual[field] for field in expected},
                    expected,
                )
        self.assertIn(
            "fallback_used",
            {item["code"] for item in fallback["limitations"]},
        )

        expected_material_owners = {
            "market_flash_primary_success": "cls_market_flash@1",
            "market_flash_fallback": "eastmoney_market_flash@1",
        }
        for scenario, expected_owner in expected_material_owners.items():
            materials = [
                item
                for item in results[scenario]["observations"]
                if item["operation_id"] in owners
            ]
            self.assertEqual(len(materials), 1)
            payload = materials[0]["payload"]
            self.assertEqual(materials[0]["operation_id"], expected_owner)
            self.assertEqual(payload["source_role"], "market_signal")
            self.assertTrue(payload["source_document_id"])
            self.assertTrue(payload["published_at"])
            self.assertEqual(payload["available_at"], payload["published_at"])
            self.assertTrue(payload["retrieved_at"])
            self.assertTrue(payload["locator"]["uri"])
            self.assertTrue(payload["summary"])
            self.assertEqual(
                payload["license_boundary"],
                {"status": "not_established", "raw_payload": "excluded"},
            )

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
