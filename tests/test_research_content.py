from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.content_contract import (  # noqa: E402
    ContentObservation,
    ContentQuery,
    SourceBatch,
    SourceFailure,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.report_sources import EastmoneyReportOperation  # noqa: E402
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402
from a_share_research.source_throttle import SerialRequestGate  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
REPORT_FIXTURES = Path(__file__).parent / "fixtures" / "research_content" / "reports"
FIXTURE_RAW_FIELDS = ("fixture_observation",)


class FixedReportOperation:
    operation_id = "fixed_reports@1"
    supported_material_types = frozenset({"research_report"})
    raw_fields = FIXTURE_RAW_FIELDS

    def collect(self, query: ContentQuery) -> SourceBatch:
        self.asserted_query = query
        retrieved_at = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)
        return SourceBatch(
            operation_id=self.operation_id,
            observations=(
                ContentObservation(
                    material_type="research_report",
                    source_operation=self.operation_id,
                    source_role="attributed_opinion",
                    source_document_id="AP-1",
                    title="人形机器人丝杠行业研究",
                    published_at="2026-07-31T00:00:00+08:00",
                    retrieved_at=retrieved_at,
                    locator_uri="https://example.test/reports/AP-1",
                    subject=None,
                    author="研究机构甲",
                    summary=None,
                    document_locator="https://example.test/reports/AP-1.pdf",
                    attributes={"rating": "增持"},
                    limitations=("publication_time_precision_is_date_only",),
                ),
                ContentObservation(
                    material_type="research_report",
                    source_operation=self.operation_id,
                    source_role="attributed_opinion",
                    source_document_id="AP-1",
                    title="人形机器人丝杠行业研究",
                    published_at="2026-07-31T00:00:00+08:00",
                    retrieved_at=retrieved_at,
                    locator_uri="https://example.test/reports/AP-1",
                    subject=None,
                    author="研究机构甲",
                    summary=None,
                    document_locator="https://example.test/reports/AP-1.pdf",
                    attributes={"rating": "增持"},
                    limitations=("publication_time_precision_is_date_only",),
                ),
                ContentObservation(
                    material_type="research_report",
                    source_operation=self.operation_id,
                    source_role="attributed_opinion",
                    source_document_id="AP-FUTURE",
                    title="未来发布的报告",
                    published_at="2026-08-03T00:00:00+08:00",
                    retrieved_at=retrieved_at,
                    locator_uri="https://example.test/reports/AP-FUTURE",
                    subject=None,
                    author="研究机构乙",
                    summary=None,
                    document_locator=None,
                    attributes={},
                    limitations=(),
                ),
            ),
        )


class FixedInvestorQaOperation:
    operation_id = "fixed_investor_qa@1"
    supported_material_types = frozenset({"investor_qa"})
    raw_fields = FIXTURE_RAW_FIELDS

    def collect(self, query: ContentQuery) -> SourceBatch:
        assert query.subject is not None
        retrieved_at = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)
        return SourceBatch(
            operation_id=self.operation_id,
            observations=(
                ContentObservation(
                    material_type="investor_qa",
                    source_operation=self.operation_id,
                    source_role="market_observation",
                    source_document_id="question-1",
                    title="蓝色光标投资者提问",
                    published_at="2026-07-31T09:00:00+08:00",
                    retrieved_at=retrieved_at,
                    locator_uri="https://example.test/qa#question-1",
                    subject=query.subject,
                    author="投资者",
                    summary="公司的 AI 算力业务今年有什么进展？",
                    document_locator=None,
                    attributes={"answer_status": "answered"},
                    limitations=("investor_question_is_not_issuer_disclosure",),
                ),
                ContentObservation(
                    material_type="investor_qa",
                    source_operation=self.operation_id,
                    source_role="attributed_opinion",
                    source_document_id="answer-1",
                    title="蓝色光标公司回复",
                    published_at="2026-07-31T11:00:00+08:00",
                    retrieved_at=retrieved_at,
                    locator_uri="https://example.test/qa#answer-1",
                    subject=query.subject,
                    author="蓝色光标",
                    summary="公司正在建设 AI 算力平台，并扩充算力中心资源。",
                    document_locator=None,
                    attributes={"question_id": "1"},
                    limitations=("company_reply_is_not_authoritative_disclosure",),
                ),
            ),
        )


class BluefocusIdentityTransport:
    fixtures = Path(__file__).resolve().parent / "fixtures" / "identity_sources"

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlparse(url).netloc
        if host == "query.sse.com.cn":
            body = b'{"result":[]}'
        elif host == "www.szse.cn":
            body = (self.fixtures / "szse_300058.json").read_bytes()
        elif host == "www.cninfo.com.cn":
            body = (self.fixtures / "cninfo_current_orgs.json").read_bytes()
        else:
            raise AssertionError(f"unexpected identity URL: {url}")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=body,
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class ThemeContentTransport:
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "research_content"
        / "reports"
        / "iwencai_report_success.json"
    )
    report_fixtures = fixture.parent

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        page = int(parse_qs(urlparse(url).query)["pageNo"][0])
        return HttpResponse(
            status=200,
            content_type="text/plain",
            body=(
                self.report_fixtures / f"eastmoney_stock_page_{page}.json"
            ).read_bytes(),
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        self.authorization = headers.get("Authorization")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=self.fixture.read_bytes(),
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class ResearchContentProcessTests(unittest.TestCase):
    def research_report_request(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "task_type": "research_content",
            "subjects": [],
            "as_of": "2026-08-02",
            "window": {
                "published_from": "2026-05-01",
                "published_to": "2026-08-02",
            },
            "parameters": {
                "material_types": ["research_report"],
                "query": ["人形机器人", "丝杠"],
                "limit": 20,
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

    def investor_qa_request(
        self,
        query_keywords: list[str],
        theme_keywords: list[str] | None = None,
    ) -> dict[str, object]:
        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        parameters: dict[str, object] = {
            "material_types": ["investor_qa"],
            "query": query_keywords,
            "limit": 20,
        }
        if theme_keywords is not None:
            parameters["theme_keywords"] = theme_keywords
        request["parameters"] = parameters
        return request

    def test_content_operation_without_static_raw_fields_fails_closed(self) -> None:
        class MissingRawFieldsOperation:
            operation_id = "missing_raw_fields@1"
            supported_material_types = frozenset({"research_report"})

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(operation_id=self.operation_id)

        with self.assertRaisesRegex(ValueError, "raw_fields"):
            ResearchRuntime(content_operations=(MissingRawFieldsOperation(),)).research(
                self.research_report_request()
            )

    def test_invalid_fallback_graphs_fail_before_source_collection(self) -> None:
        calls: list[str] = []

        class GraphOperation:
            raw_fields = FIXTURE_RAW_FIELDS

            def __init__(
                self,
                operation_id: str,
                supported_material_types: frozenset[str],
                fallback_for: str | None = None,
            ) -> None:
                self.operation_id = operation_id
                self.supported_material_types = supported_material_types
                self.fallback_for = fallback_for

            def collect(self, query: ContentQuery) -> SourceBatch:
                calls.append(self.operation_id)
                return SourceBatch(operation_id=self.operation_id)

        research = frozenset({"research_report"})
        industry = frozenset({"industry_report"})
        cases = (
            (
                "missing",
                (GraphOperation("fallback@1", research, "absent@1"),),
            ),
            (
                "cycle",
                (
                    GraphOperation("a@1", research, "b@1"),
                    GraphOperation("b@1", research, "a@1"),
                ),
            ),
            (
                "scope",
                (
                    GraphOperation("primary@1", research),
                    GraphOperation("fallback@1", industry, "primary@1"),
                ),
            ),
        )
        for expected, operations in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    ResearchRuntime(content_operations=operations).research(
                        self.research_report_request()
                    )
        self.assertEqual(calls, [])

    def test_theme_reports_are_time_bounded_deduplicated_attributed_opinions(
        self,
    ) -> None:
        operation = FixedReportOperation()
        request = self.research_report_request()

        result = (
            ResearchRuntime(content_operations=(operation,)).research(request).to_dict()
        )

        self.assertEqual(result["plan"]["task_type"], "research_content")
        self.assertEqual(result["status"], "limited")
        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(materials), 2)
        self.assertEqual(
            {item["payload"]["source_document_id"] for item in materials}, {"AP-1"}
        )
        self.assertEqual(
            {item["payload"]["source_role"] for item in materials},
            {"attributed_opinion"},
        )
        self.assertEqual(
            {item["payload"]["published_at"] for item in materials},
            {"2026-07-31T00:00:00+08:00"},
        )
        groups = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        ]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["payload"]["member_count"], 2)
        self.assertEqual(
            set(groups[0]["observation_ids"]), {item["id"] for item in materials}
        )
        self.assertTrue(
            {
                "experimental_research_content_sources",
                "publication_time_precision_is_date_only",
            }.issubset({item["code"] for item in result["limitations"]})
        )
        self.assertEqual(
            {(item["operation_id"], item["code"]) for item in result["source_errors"]},
            {("fixed_reports@1", "invalid_observation_time")},
        )
        self.assertEqual(operation.asserted_query.material_types, ("research_report",))
        self.assertEqual(operation.asserted_query.keywords, ("人形机器人", "丝杠"))
        observation_ids = [item["id"] for item in result["observations"]]
        self.assertEqual(len(observation_ids), len(set(observation_ids)))

    def test_multitype_report_failure_retains_prior_operation_observations(
        self,
    ) -> None:
        class LaterTypeUnknownSchemaTransport:
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                query = parse_qs(urlparse(url).query)
                if query["qType"] == ["0"]:
                    fixture = f"eastmoney_stock_page_{query['pageNo'][0]}.json"
                    body = (REPORT_FIXTURES / fixture).read_bytes()
                else:
                    payload = json.loads(
                        (REPORT_FIXTURES / "eastmoney_industry_page_1.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    payload["unexpected_provider_field"] = "must-fail-closed"
                    body = json.dumps(payload).encode("utf-8")
                return HttpResponse(
                    status=200,
                    content_type="text/plain",
                    body=body,
                    retrieved_at=datetime(
                        2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME
                    ),
                )

            def post(
                self, url: str, headers: dict[str, str], body: bytes
            ) -> HttpResponse:
                raise AssertionError("Eastmoney reports must use GET")

        request = self.research_report_request()
        request["parameters"] = {
            "material_types": ["research_report", "industry_report"],
            "query": ["AI服务器"],
            "industry_code": "1037",
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                content_operations=(
                    EastmoneyReportOperation(
                        LaterTypeUnknownSchemaTransport(),
                        request_gate=SerialRequestGate(
                            minimum_interval_seconds=0,
                            jitter_bounds=(0, 0),
                            rate_limit_backoffs=(),
                        ),
                    ),
                )
            )
            .research(request)
            .to_dict()
        )

        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertGreater(len(materials), 0)
        self.assertEqual(
            {item["payload"]["material_type"] for item in materials},
            {"research_report"},
        )
        operation = result["coverage"]["research_content_materials"]["operations"][
            "eastmoney_reports@1"
        ]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {(item["operation_id"], item["code"]) for item in result["source_errors"]},
            {("eastmoney_reports@1", "unknown_schema")},
        )

    def test_date_only_report_uses_strict_publication_date_without_inventing_time(
        self,
    ) -> None:
        class DateOnlyReportOperation:
            operation_id = "date_only_reports@1"
            supported_material_types = frozenset({"research_report"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                retrieved_at = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)

                def observation(
                    document_id: str, publication_date: str
                ) -> ContentObservation:
                    return ContentObservation(
                        material_type="research_report",
                        source_operation=self.operation_id,
                        source_role="attributed_opinion",
                        source_document_id=document_id,
                        title=document_id,
                        published_at=None,
                        retrieved_at=retrieved_at,
                        locator_uri=f"https://example.test/{document_id}",
                        subject=None,
                        author="研究机构",
                        summary=None,
                        document_locator=None,
                        attributes={"publication_date": publication_date},
                        limitations=("publication_time_precision_is_date_only",),
                    )

                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        observation("IN-WINDOW", "2026-07-31"),
                        observation("OUTSIDE-WINDOW", "2026-04-30"),
                        observation("AFTER-RETRIEVAL", "2026-08-03"),
                        observation("INVALID-DATE", "2026-7-31"),
                    ),
                )

        result = (
            ResearchRuntime(content_operations=(DateOnlyReportOperation(),))
            .research(self.research_report_request())
            .to_dict()
        )

        materials = [
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [material["source_document_id"] for material in materials],
            ["IN-WINDOW", "OUTSIDE-WINDOW"],
        )
        material = materials[0]
        self.assertIsNone(material["published_at"])
        self.assertEqual(material["attributes"]["publication_date"], "2026-07-31")
        self.assertEqual(material["available_at"], "2026-08-02T19:30:00+08:00")
        group = next(
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        )
        self.assertEqual(group["payload"]["source_document_id"], "IN-WINDOW")
        self.assertEqual(
            {(item["operation_id"], item["code"]) for item in result["source_errors"]},
            {("date_only_reports@1", "invalid_observation_time")},
        )

    def test_no_usable_material_is_blocked_without_claiming_no_coverage(self) -> None:
        class FailedReportOperation:
            operation_id = "failed_reports@1"
            supported_material_types = frozenset({"research_report"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(
                    operation_id=self.operation_id,
                    source_errors=(
                        SourceFailure(
                            source_operation=self.operation_id,
                            code="empty_response",
                            message="The source returned no response body.",
                        ),
                    ),
                )

        result = (
            ResearchRuntime(content_operations=(FailedReportOperation(),))
            .research(self.research_report_request())
            .to_dict()
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            [
                item
                for item in result["observations"]
                if item["payload"].get("kind") == "research_content_material"
            ],
            [],
        )
        self.assertEqual(result["source_errors"][0]["code"], "empty_response")
        self.assertEqual(
            result["coverage"]["research_content_materials"]["operations"][
                "failed_reports@1"
            ]["state"],
            "failed",
        )
        self.assertIn(
            "research_content_unavailable",
            {item["code"] for item in result["limitations"]},
        )

    def test_incomplete_batch_does_not_invent_a_pagination_limitation(self) -> None:
        class BoundedSemanticSearchOperation:
            operation_id = "bounded_semantic_search@1"
            supported_material_types = frozenset({"research_report"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="research_report",
                            source_operation=self.operation_id,
                            source_role="attributed_opinion",
                            source_document_id="REPORT-1",
                            title="AI 服务器行业研究",
                            published_at="2026-07-31T09:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="https://example.test/REPORT-1",
                            subject=None,
                            author="研究机构",
                            summary="AI 服务器需求研究",
                            document_locator=None,
                            attributes={},
                            limitations=(),
                        ),
                    ),
                    limitations=("semantic_search_completeness_unproven",),
                    complete=False,
                )

        result = (
            ResearchRuntime(content_operations=(BoundedSemanticSearchOperation(),))
            .research(self.research_report_request())
            .to_dict()
        )

        limitation_codes = {item["code"] for item in result["limitations"]}
        self.assertIn("semantic_search_completeness_unproven", limitation_codes)
        self.assertNotIn("pagination_incomplete", limitation_codes)
        self.assertEqual(
            result["coverage"]["research_content_materials"]["operations"][
                "bounded_semantic_search@1"
            ]["state"],
            "partial",
        )

    def test_materials_require_aware_causally_ordered_timestamps(self) -> None:
        class TimestampedReportOperation:
            operation_id = "timestamped_reports@1"
            supported_material_types = frozenset({"research_report"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                aware_retrieved_at = datetime(
                    2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                )

                def observation(
                    document_id: str,
                    published_at: str,
                    retrieved_at: datetime,
                ) -> ContentObservation:
                    return ContentObservation(
                        material_type="research_report",
                        source_operation=self.operation_id,
                        source_role="attributed_opinion",
                        source_document_id=document_id,
                        title=document_id,
                        published_at=published_at,
                        retrieved_at=retrieved_at,
                        locator_uri=f"https://example.test/{document_id}",
                        subject=None,
                        author="研究机构",
                        summary=None,
                        document_locator=None,
                        attributes={},
                        limitations=(),
                    )

                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        observation(
                            "SAFE",
                            "2026-08-02T19:00:00+08:00",
                            aware_retrieved_at,
                        ),
                        observation(
                            "SAME-DAY-FUTURE",
                            "2026-08-02T20:00:00+08:00",
                            aware_retrieved_at,
                        ),
                        observation(
                            "NAIVE-PUBLISHED",
                            "2026-08-01T09:00:00",
                            aware_retrieved_at,
                        ),
                        observation(
                            "NAIVE-RETRIEVED",
                            "2026-08-01T09:00:00+08:00",
                            datetime(2026, 8, 2, 19, 30),
                        ),
                    ),
                )

        result = (
            ResearchRuntime(content_operations=(TimestampedReportOperation(),))
            .research(self.research_report_request())
            .to_dict()
        )

        materials = [
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [material["source_document_id"] for material in materials], ["SAFE"]
        )

    def test_identity_blocked_result_keeps_the_brief_shape(self) -> None:
        class UnavailableIdentityTransport:
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                raise TransportError(
                    "upstream_unavailable",
                    "The source request could not be completed.",
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
        }

        result = (
            ResearchRuntime(
                identity_transport=UnavailableIdentityTransport(),
                content_operations=(),
            )
            .research(request)
            .to_dict()
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["claims"], [])
        self.assertEqual(
            result["task_views"]["research_content"]["observation_ids"],
            result["briefs"][0]["observation_ids"],
        )

    def test_identity_blocked_investor_qa_keeps_theme_aggregation_limitation(
        self,
    ) -> None:
        class UnavailableIdentityTransport:
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                raise TransportError(
                    "upstream_unavailable",
                    "The source request could not be completed.",
                )

        result = (
            ResearchRuntime(
                identity_transport=UnavailableIdentityTransport(),
                content_operations=(),
            )
            .research(self.investor_qa_request([], ["算力"]))
            .to_dict()
        )

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(
            any(
                item["kind"] == "research_content_material_group"
                for item in result["claims"]
            )
        )
        self.assertEqual(result["calculations"], [])

    def test_subject_material_uses_cross_checked_canonical_security(self) -> None:
        class AnnouncementOperation:
            operation_id = "fixed_announcements@1"
            supported_material_types = frozenset({"announcement"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                self.subject = query.subject
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="announcement",
                            source_operation=self.operation_id,
                            source_role="authoritative_disclosure",
                            source_document_id="1225378221",
                            title="2025年年度权益分派实施公告",
                            published_at="2026-06-18T17:32:10+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="https://example.test/1225378221",
                            subject=query.subject,
                            author="北京蓝色光标数据科技股份有限公司",
                            summary=None,
                            document_locator="https://example.test/1225378221.pdf",
                            attributes={},
                            limitations=(),
                        ),
                    ),
                )

        operation = AnnouncementOperation()
        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
        }

        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(operation,),
            )
            .research(request)
            .to_dict()
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            operation.subject,
            {
                "security": {
                    "exchange": "SZSE",
                    "code": "300058",
                    "type": "A_SHARE",
                },
                "name": "蓝色光标",
                "issuer": {
                    "name": None,
                    "identifier": None,
                    "security_relationship": "unverified",
                },
            },
        )
        identity_claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "research_content_subject_identity"
        )
        self.assertEqual(identity_claim["payload"], operation.subject)
        material = next(
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        )
        self.assertEqual(material["subject"], operation.subject)
        self.assertIn(
            "experimental_identity_sources",
            {item["code"] for item in result["limitations"]},
        )
        self.assertIn(
            "issuer_relationship_unverified",
            {item["code"] for item in result["limitations"]},
        )

    def test_material_scope_rejects_ambiguous_subject_combinations(self) -> None:
        cases: tuple[
            tuple[str, list[dict[str, str]], list[str], dict[str, Any]], ...
        ] = (
            ("announcement", [], [], {}),
            ("market_flash", [{"clue": "蓝色光标"}], [], {}),
            ("research_report", [], [], {}),
            ("industry_report", [], ["消费电子"], {}),
        )
        for material_type, subjects, keywords, extra_parameters in cases:
            with self.subTest(material_type=material_type):
                request = self.research_report_request()
                request["subjects"] = subjects
                request["parameters"] = {
                    "material_types": [material_type],
                    "query": keywords,
                    "limit": 20,
                    **extra_parameters,
                }
                with self.assertRaises(ValueError):
                    ResearchRuntime(content_operations=()).research(request)

    def test_invalid_f10_profile_categories_fail_before_external_calls(self) -> None:
        class NoExternalCalls:
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                raise AssertionError("invalid requests must not resolve identity")

        invalid_categories = (
            [],
            ["公司概况", "公司概况"],
            [
                "最新提示",
                "公司概况",
                "财务分析",
                "股东研究",
                "股本结构",
                "资本运作",
                "业内点评",
                "行业分析",
                "公司大事",
                "公司概况",
            ],
            ["未声明分类"],
        )

        for categories in invalid_categories:
            with self.subTest(categories=categories):
                request = self.research_report_request()
                request["subjects"] = [{"clue": "蓝色光标"}]
                request["parameters"] = {
                    "material_types": ["issuer_profile"],
                    "query": [],
                    "limit": 20,
                    "profile_categories": categories,
                }

                with self.assertRaisesRegex(
                    ValueError,
                    r"invalid_request: .*parameters\.profile_categories",
                ):
                    ResearchRuntime(
                        identity_transport=NoExternalCalls(),
                        content_operations=(),
                    ).research(request)

    def test_unknown_observation_enums_are_source_errors_not_public_evidence(
        self,
    ) -> None:
        class MixedSchemaOperation:
            operation_id = "mixed_schema@1"
            supported_material_types = frozenset({"research_report"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                retrieved_at = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)

                def observation(
                    document_id: str,
                    material_type: str,
                    source_role: str,
                ) -> ContentObservation:
                    return ContentObservation(
                        material_type=material_type,
                        source_operation=self.operation_id,
                        source_role=source_role,
                        source_document_id=document_id,
                        title=document_id,
                        published_at="2026-07-31T09:00:00+08:00",
                        retrieved_at=retrieved_at,
                        locator_uri=f"https://example.test/{document_id}",
                        subject=None,
                        author="研究机构甲",
                        summary=None,
                        document_locator=None,
                        attributes={},
                        limitations=(),
                    )

                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        observation(
                            "valid",
                            "research_report",
                            "attributed_opinion",
                        ),
                        observation(
                            "unknown-material",
                            "private_note",
                            "attributed_opinion",
                        ),
                        observation(
                            "unknown-role",
                            "research_report",
                            "anonymous_claim",
                        ),
                    ),
                )

        result = (
            ResearchRuntime(content_operations=(MixedSchemaOperation(),))
            .research(self.research_report_request())
            .to_dict()
        )

        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [item["payload"]["source_document_id"] for item in materials], ["valid"]
        )
        schema_errors = [
            item for item in result["source_errors"] if item["code"] == "unknown_schema"
        ]
        self.assertEqual(len(schema_errors), 2)
        self.assertEqual(
            {item["operation_id"] for item in schema_errors}, {"mixed_schema@1"}
        )
        self.assertEqual(
            result["coverage"]["research_content_materials"]["operations"][
                "mixed_schema@1"
            ]["state"],
            "partial",
        )

    def test_same_document_id_in_different_namespaces_is_not_deduplicated(
        self,
    ) -> None:
        class NamespacedAnnouncementOperation:
            supported_material_types = frozenset({"announcement"})
            raw_fields = FIXTURE_RAW_FIELDS

            def __init__(self, operation_id: str, namespace: str | None) -> None:
                self.operation_id = operation_id
                self.namespace = namespace

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="announcement",
                            source_operation=self.operation_id,
                            source_role="authoritative_disclosure",
                            source_document_id="SAME-ID",
                            source_document_namespace=self.namespace,
                            title=f"{self.namespace or self.operation_id} 公告",
                            published_at="2026-07-31T16:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri=f"https://example.test/{self.operation_id}",
                            subject=query.subject,
                            author="蓝色光标",
                            summary=None,
                            document_locator=None,
                            attributes={},
                            limitations=(),
                        ),
                    ),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(
                    NamespacedAnnouncementOperation("source_a@1", "source-a-document"),
                    NamespacedAnnouncementOperation("source_b@1", "source-b-document"),
                ),
            )
            .research(request)
            .to_dict()
        )

        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(materials), 2)
        self.assertEqual(
            {item["payload"]["source_document_namespace"] for item in materials},
            {"source-a-document", "source-b-document"},
        )
        groups = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        ]
        self.assertEqual(len(groups), 2)

        operation_local_result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(
                    NamespacedAnnouncementOperation("source_a@1", None),
                    NamespacedAnnouncementOperation("source_b@1", None),
                ),
            )
            .research(request)
            .to_dict()
        )

        local_materials = [
            item
            for item in operation_local_result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(local_materials), 2)
        self.assertEqual(
            {item["payload"]["source_document_namespace"] for item in local_materials},
            {"operation:source_a@1", "operation:source_b@1"},
        )

    def test_same_operation_document_keeps_distinct_source_metadata(self) -> None:
        class RevisedMetadataOperation:
            operation_id = "revised_metadata@1"
            supported_material_types = frozenset({"research_report"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="research_report",
                            source_operation=self.operation_id,
                            source_role="market_observation",
                            source_document_id="REPORT-REVISION",
                            title="报告索引标题",
                            published_at="2026-07-31T09:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 18, 0, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="https://example.test/index/REPORT-REVISION",
                            subject=None,
                            author="研究机构甲",
                            summary=None,
                            document_locator="https://example.test/v1.pdf",
                            attributes={"rating": "增持"},
                            limitations=("index_metadata_only",),
                        ),
                        ContentObservation(
                            material_type="research_report",
                            source_operation=self.operation_id,
                            source_role="attributed_opinion",
                            source_document_id="REPORT-REVISION",
                            title="报告修订标题",
                            published_at="2026-07-31T10:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 0, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="https://example.test/detail/REPORT-REVISION",
                            subject=None,
                            author="研究机构乙",
                            summary=None,
                            document_locator="https://example.test/v2.pdf",
                            attributes={"rating": "中性"},
                            limitations=("revision_relationship_unverified",),
                        ),
                    ),
                )

        result = (
            ResearchRuntime(content_operations=(RevisedMetadataOperation(),))
            .research(self.research_report_request())
            .to_dict()
        )

        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(materials), 2)
        group = next(
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        )
        self.assertEqual(group["payload"]["member_count"], 2)
        self.assertEqual(
            set(group["observation_ids"]), {item["id"] for item in materials}
        )
        self.assertIn(
            "research_content_metadata_conflict",
            {item["code"] for item in result["conflicts"]},
        )

    def test_cross_source_duplicate_preserves_both_official_locators(self) -> None:
        class OfficialAnnouncementOperation:
            supported_material_types = frozenset({"announcement"})
            raw_fields = FIXTURE_RAW_FIELDS

            def __init__(
                self,
                operation_id: str,
                locator: str,
                source_role: str,
                published_at: str,
                retrieved_at: datetime,
                title: str,
                author: str,
                attributes: dict[str, str],
                limitations: tuple[str, ...],
            ) -> None:
                self.operation_id = operation_id
                self.locator = locator
                self.source_role = source_role
                self.published_at = published_at
                self.retrieved_at = retrieved_at
                self.title = title
                self.author = author
                self.attributes = attributes
                self.limitations = limitations

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="announcement",
                            source_operation=self.operation_id,
                            source_role=self.source_role,
                            source_document_id="1225378221",
                            source_document_namespace=(
                                "cninfo-szse-official-announcement"
                            ),
                            title=self.title,
                            published_at=self.published_at,
                            retrieved_at=self.retrieved_at,
                            locator_uri=self.locator,
                            subject=query.subject,
                            author=self.author,
                            summary=None,
                            document_locator=f"{self.locator}.pdf",
                            attributes=self.attributes,
                            limitations=self.limitations,
                        ),
                    ),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement"],
            "query": [],
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(
                    OfficialAnnouncementOperation(
                        "cninfo_announcements@1",
                        "https://cninfo.test/1225378221",
                        "authoritative_disclosure",
                        "2026-06-18T17:30:00+08:00",
                        datetime(2026, 8, 2, 19, 20, tzinfo=CHINA_STANDARD_TIME),
                        "2025年年度权益分派实施公告（巨潮）",
                        "北京蓝色光标数据科技股份有限公司",
                        {"route": "cninfo"},
                        ("issuer_relationship_unverified",),
                    ),
                    OfficialAnnouncementOperation(
                        "szse_announcements@1",
                        "https://szse.test/1225378221",
                        "market_observation",
                        "2026-06-18T17:32:10+08:00",
                        datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME),
                        "2025年年度权益分派实施公告",
                        "蓝色光标",
                        {"route": "szse"},
                        ("security_relationship_unverified",),
                    ),
                ),
            )
            .research(request)
            .to_dict()
        )

        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(materials), 2)
        self.assertEqual(
            {item["operation_id"] for item in materials},
            {"cninfo_announcements@1", "szse_announcements@1"},
        )
        self.assertEqual(
            {item["payload"]["source_document_namespace"] for item in materials},
            {"cninfo-szse-official-announcement"},
        )
        self.assertEqual(
            {item["payload"]["title"] for item in materials},
            {"2025年年度权益分派实施公告（巨潮）", "2025年年度权益分派实施公告"},
        )
        group = next(
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        )
        self.assertEqual(group["payload"]["member_count"], 2)
        self.assertEqual(
            set(group["observation_ids"]), {item["id"] for item in materials}
        )
        self.assertIn(
            "research_content_metadata_conflict",
            {item["code"] for item in result["conflicts"]},
        )

    def test_partial_material_type_coverage_names_the_missing_type(self) -> None:
        class PartialOperation:
            operation_id = "partial_content@1"
            supported_material_types = frozenset({"announcement", "stock_news"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="announcement",
                            source_operation=self.operation_id,
                            source_role="authoritative_disclosure",
                            source_document_id="ANN-1",
                            title="公告",
                            published_at="2026-07-31T16:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="https://example.test/ANN-1",
                            subject=query.subject,
                            author="蓝色光标",
                            summary=None,
                            document_locator=None,
                            attributes={},
                            limitations=(),
                        ),
                    ),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement", "stock_news"],
            "query": [],
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(PartialOperation(),),
            )
            .research(request)
            .to_dict()
        )

        self.assertEqual(result["status"], "limited")
        self.assertIn(
            "requested_material_type_unavailable",
            {item["code"] for item in result["limitations"]},
        )
        materials = [
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            [item["material_type"] for item in materials], ["announcement"]
        )
        self.assertEqual(
            materials[0]["query_scope"]["material_types"],
            ["announcement", "stock_news"],
        )

    def test_missing_requested_type_blocks_without_discarding_other_materials_or_diagnostics(
        self,
    ) -> None:
        class NewsOperation:
            operation_id = "fixed_news@1"
            supported_material_types = frozenset({"stock_news"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="stock_news",
                            source_operation=self.operation_id,
                            source_role="attributed_opinion",
                            source_document_id="NEWS-1",
                            title="新闻材料",
                            published_at="2026-07-31T16:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="https://example.test/NEWS-1",
                            subject=query.subject,
                            author="新闻来源",
                            summary=None,
                            document_locator=None,
                            attributes={},
                            limitations=(),
                        ),
                    ),
                )

        class CninfoAnnouncementOperation:
            operation_id = "fixture_cninfo_announcement@1"
            supported_material_types = frozenset({"announcement"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(
                    operation_id=self.operation_id,
                    source_errors=(
                        SourceFailure(
                            source_operation=self.operation_id,
                            code="unknown_schema",
                            message="The announcement response schema is unknown.",
                        ),
                    ),
                )

        class SzseAnnouncementOperation:
            operation_id = "fixture_szse_announcement@1"
            supported_material_types = frozenset({"announcement"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(
                    operation_id=self.operation_id,
                    source_errors=(
                        SourceFailure(
                            source_operation=self.operation_id,
                            code="pagination_incomplete",
                            message="Announcement pagination is incomplete.",
                        ),
                    ),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement", "stock_news"],
            "query": [],
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(
                    NewsOperation(),
                    CninfoAnnouncementOperation(),
                    SzseAnnouncementOperation(),
                ),
            )
            .research(request)
            .to_dict()
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            [
                item["payload"]["material_type"]
                for item in result["observations"]
                if item["payload"].get("kind") == "research_content_material"
            ],
            ["stock_news"],
        )
        self.assertEqual(
            [
                (error["operation_id"], error["code"])
                for error in result["source_errors"]
            ],
            [
                ("fixture_cninfo_announcement@1", "unknown_schema"),
                ("fixture_szse_announcement@1", "pagination_incomplete"),
            ],
        )
        self.assertIn(
            "requested_material_type_unavailable",
            {item["code"] for item in result["limitations"]},
        )

    def test_parallel_source_can_cover_type_without_hiding_peer_diagnostic(
        self,
    ) -> None:
        class SuccessfulMixedOperation:
            operation_id = "successful_mixed@1"
            supported_material_types = frozenset({"announcement", "stock_news"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(
                        ContentObservation(
                            material_type=material_type,
                            source_operation=self.operation_id,
                            source_role=source_role,
                            source_document_id=document_id,
                            title=document_id,
                            published_at="2026-07-31T16:00:00+08:00",
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri=f"https://example.test/{document_id}",
                            subject=query.subject,
                            author="来源",
                            summary=None,
                            document_locator=None,
                            attributes={},
                            limitations=(),
                        )
                        for material_type, source_role, document_id in (
                            (
                                "announcement",
                                "authoritative_disclosure",
                                "ANN-1",
                            ),
                            ("stock_news", "attributed_opinion", "NEWS-1"),
                        )
                    ),
                )

        class FailedAnnouncementOperation:
            operation_id = "failed_announcement@1"
            supported_material_types = frozenset({"announcement"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                return SourceBatch(
                    operation_id=self.operation_id,
                    source_errors=(
                        SourceFailure(
                            source_operation=self.operation_id,
                            code="upstream_unavailable",
                            message="The parallel announcement source is unavailable.",
                        ),
                    ),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement", "stock_news"],
            "query": [],
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(
                    FailedAnnouncementOperation(),
                    SuccessfulMixedOperation(),
                ),
            )
            .research(request)
            .to_dict()
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {
                item["payload"]["material_type"]
                for item in result["observations"]
                if item["payload"].get("kind") == "research_content_material"
            },
            {"stock_news", "announcement"},
        )
        self.assertEqual(
            [
                (error["operation_id"], error["code"])
                for error in result["source_errors"]
            ],
            [("failed_announcement@1", "upstream_unavailable")],
        )
        self.assertIn(
            "experimental_research_content_sources",
            {item["code"] for item in result["limitations"]},
        )
        self.assertNotIn(
            "requested_material_type_unavailable",
            {item["code"] for item in result["limitations"]},
        )

    def test_current_material_with_unknown_publication_time_is_retained_as_limited(
        self,
    ) -> None:
        class CurrentProfileOperation:
            operation_id = "fixed_f10@1"
            supported_material_types = frozenset({"issuer_profile"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=(
                        ContentObservation(
                            material_type="issuer_profile",
                            source_operation=self.operation_id,
                            source_role="market_observation",
                            source_document_id=None,
                            title="公司概况",
                            published_at=None,
                            retrieved_at=datetime(
                                2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME
                            ),
                            locator_uri="tdx://f10/SZSE/300058/公司概况",
                            subject=query.subject,
                            author=None,
                            summary="公司资料正文",
                            document_locator=None,
                            attributes={"category": "公司概况"},
                            limitations=(
                                "publication_time_unknown",
                                "source_document_id_unknown",
                            ),
                        ),
                    ),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["issuer_profile"],
            "query": [],
            "limit": 20,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(CurrentProfileOperation(),),
            )
            .research(request)
            .to_dict()
        )

        self.assertEqual(result["status"], "limited")
        material = next(
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        )
        self.assertIsNone(material["published_at"])
        self.assertIsInstance(material["source_document_id"], str)
        self.assertEqual(material["available_at"], "2026-08-02T19:30:00+08:00")

    def test_default_registry_executes_credentialed_theme_report_search(self) -> None:
        request = self.research_report_request()
        source_policy = request["source_policy"]
        assert isinstance(source_policy, dict)
        source_policy["allow_credentials"] = True
        transport = ThemeContentTransport()

        with patch.dict(
            "os.environ",
            {
                "IWENCAI_API_KEY": "test-secret-not-for-output",
                "IWENCAI_BASE_URL": "https://iwencai.example.test",
            },
            clear=False,
        ):
            result = (
                ResearchRuntime(content_transport=transport).research(request).to_dict()
            )

        self.assertEqual(result["status"], "limited")
        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0]["operation_id"], "iwencai_content_search@1")
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(transport.authorization, "Bearer test-secret-not-for-output")
        self.assertNotIn("test-secret-not-for-output", str(result))

    def test_limit_applies_per_material_type_not_across_the_whole_result(self) -> None:
        class MixedOperation:
            operation_id = "mixed_content@1"
            supported_material_types = frozenset({"announcement", "stock_news"})
            raw_fields = FIXTURE_RAW_FIELDS

            def collect(self, query: ContentQuery) -> SourceBatch:
                assert query.subject is not None
                observations = []
                for material_type, role in (
                    ("announcement", "authoritative_disclosure"),
                    ("stock_news", "attributed_opinion"),
                ):
                    for index in (1, 2):
                        observations.append(
                            ContentObservation(
                                material_type=material_type,
                                source_operation=self.operation_id,
                                source_role=role,
                                source_document_id=f"{material_type}-{index}",
                                title=f"{material_type}-{index}",
                                published_at=(
                                    f"2026-07-{30 + index:02d}T16:00:00+08:00"
                                ),
                                retrieved_at=datetime(
                                    2026,
                                    8,
                                    2,
                                    19,
                                    30,
                                    tzinfo=CHINA_STANDARD_TIME,
                                ),
                                locator_uri=(
                                    f"https://example.test/{material_type}-{index}"
                                ),
                                subject=query.subject,
                                author="来源",
                                summary=None,
                                document_locator=None,
                                attributes={},
                                limitations=(),
                            )
                        )
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                )

        request = self.research_report_request()
        request["subjects"] = [{"clue": "蓝色光标"}]
        request["parameters"] = {
            "material_types": ["announcement", "stock_news"],
            "query": [],
            "limit": 1,
        }
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(MixedOperation(),),
            )
            .research(request)
            .to_dict()
        )

        materials = [
            item
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(len(materials), 2)
        groups = [
            item
            for item in result["claims"]
            if item["kind"] == "research_content_material_group"
        ]
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {item["payload"]["material_type"] for item in groups},
            {"stock_news", "announcement"},
        )

    def test_investor_qa_retains_auditable_theme_material_members(
        self,
    ) -> None:
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(FixedInvestorQaOperation(),),
            )
            .research(self.investor_qa_request(["服务端检索词"], [" 算力 ", "AI"]))
            .to_dict()
        )

        materials = [
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        ]
        self.assertEqual(
            {item["source_document_id"] for item in materials},
            {"answer-1", "question-1"},
        )
        self.assertEqual(
            {item["summary"] for item in materials},
            {
                "公司正在建设 AI 算力平台，并扩充算力中心资源。",
                "公司的 AI 算力业务今年有什么进展？",
            },
        )

    def test_investor_qa_without_theme_match_still_retains_source_summaries(
        self,
    ) -> None:
        result = (
            ResearchRuntime(
                identity_transport=BluefocusIdentityTransport(),
                content_operations=(FixedInvestorQaOperation(),),
            )
            .research(self.investor_qa_request(["AI"]))
            .to_dict()
        )

        self.assertEqual(
            {
                item["payload"]["source_document_id"]: item["payload"]["summary"]
                for item in result["observations"]
                if item["payload"].get("kind") == "research_content_material"
            },
            {
                "answer-1": "公司正在建设 AI 算力平台，并扩充算力中心资源。",
                "question-1": "公司的 AI 算力业务今年有什么进展？",
            },
        )

    def test_investor_qa_theme_keywords_require_one_to_twenty_unique_strings(
        self,
    ) -> None:
        invalid_values: tuple[object, ...] = (
            [],
            [f"主题{index}" for index in range(21)],
            [""],
            ["   "],
            ["AI", "ai"],
            ["算力", " 算力 "],
            [1],
            "算力",
        )
        for invalid_value in invalid_values:
            with self.subTest(theme_keywords=invalid_value):
                request = self.investor_qa_request([])
                parameters = request["parameters"]
                assert isinstance(parameters, dict)
                parameters["theme_keywords"] = invalid_value

                with self.assertRaises(ValueError):
                    ResearchRuntime(
                        identity_transport=BluefocusIdentityTransport(),
                        content_operations=(FixedInvestorQaOperation(),),
                    ).research(request)


if __name__ == "__main__":
    unittest.main()
