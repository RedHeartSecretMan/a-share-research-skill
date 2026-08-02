from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.content_contract import ContentQuery  # noqa: E402
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.report_sources import (  # noqa: E402
    EastmoneyReportOperation,
    IwencaiContentSearchOperation,
    ThsConsensusMaterialOperation,
)
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateDiagnostic,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME)
FIXTURES = Path(__file__).parent / "fixtures" / "research_content" / "reports"
T = TypeVar("T")


class EastmoneyFixtureTransport:
    def __init__(
        self,
        pages: dict[int, str | Exception],
        *,
        status: int = 200,
        content_type: str = "text/plain",
    ) -> None:
        self._pages = pages
        self._status = status
        self._content_type = content_type
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        page = int(parse_qs(urlparse(url).query)["pageNo"][0])
        fixture = self._pages[page]
        if isinstance(fixture, Exception):
            raise fixture
        return HttpResponse(
            status=self._status,
            content_type=self._content_type,
            body=(FIXTURES / fixture).read_bytes(),
            retrieved_at=RETRIEVED_AT,
        )

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        raise AssertionError("Eastmoney report discovery must use GET")


class IwencaiFixtureTransport:
    def __init__(self, fixture: str, *, publish_date: str | None = None) -> None:
        self._fixture = fixture
        self._publish_date = publish_date
        self.posts: list[tuple[str, dict[str, str], bytes]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        raise AssertionError("iwencai search must use POST")

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        self.posts.append((url, headers, body))
        response_body = (FIXTURES / self._fixture).read_bytes()
        if self._publish_date is not None:
            payload = json.loads(response_body)
            payload["data"][0]["publish_date"] = self._publish_date
            response_body = json.dumps(payload).encode("utf-8")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=response_body,
            retrieved_at=RETRIEVED_AT,
        )


class ThsFixtureTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        return HttpResponse(
            status=200,
            content_type="text/html",
            body=(FIXTURES / "ths_consensus_material.html").read_bytes(),
            retrieved_at=RETRIEVED_AT,
        )

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        raise AssertionError("THS consensus material must use GET")


class DiagnosticRequestGate:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        self.calls += 1
        result = request()
        return result, (
            RequestGateDiagnostic(
                code="source_request_paced",
                delay_seconds=1.25,
            ),
        )


def no_wait_gate() -> SerialRequestGate:
    return SerialRequestGate(
        minimum_interval_seconds=0,
        jitter_bounds=(0, 0),
        rate_limit_backoffs=(),
    )


def stock_query(*, limit: int = 20) -> ContentQuery:
    return ContentQuery(
        material_types=("research_report",),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-05-01",
        published_to="2026-08-02",
        limit=limit,
        subject={
            "security": {
                "exchange": "SSE",
                "code": "601138",
                "type": "A_SHARE",
            },
            "name": "工业富联",
        },
        parameters={},
    )


def industry_query(*, limit: int = 20) -> ContentQuery:
    return ContentQuery(
        material_types=("industry_report",),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-05-01",
        published_to="2026-08-02",
        limit=limit,
        subject=None,
        parameters={"industry_code": "1037"},
    )


def theme_query(*, limit: int = 20) -> ContentQuery:
    return ContentQuery(
        material_types=("research_report",),
        keywords=("AI服务器",),
        as_of="2026-08-02",
        published_from="2026-05-01",
        published_to="2026-08-02",
        limit=limit,
        subject=None,
        parameters={},
    )


def semantic_query(
    material_type: str = "research_report",
    *,
    allow_credentials: bool = True,
) -> ContentQuery:
    return ContentQuery(
        material_types=(material_type,),
        keywords=("AI服务器", "算力产业链"),
        as_of="2026-08-02",
        published_from="2026-05-01",
        published_to="2026-08-02",
        limit=20,
        subject={
            "security": {
                "exchange": "SSE",
                "code": "601138",
                "type": "A_SHARE",
            },
            "name": "工业富联",
        },
        parameters={},
        allow_credentials=allow_credentials,
    )


def consensus_query(*, as_of: str = "2026-08-02") -> ContentQuery:
    return ContentQuery(
        material_types=("consensus_material",),
        keywords=(),
        as_of=as_of,
        published_from=as_of,
        published_to=as_of,
        limit=10,
        subject=stock_query().subject,
        parameters={},
    )


class EastmoneyReportOperationTests(unittest.TestCase):
    def test_report_request_uses_injected_gate_and_keeps_diagnostic(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_industry_page_1.json"})
        gate = DiagnosticRequestGate()

        batch = EastmoneyReportOperation(
            transport,
            request_gate=gate,
        ).collect(industry_query())

        self.assertEqual(gate.calls, 1)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        self.assertEqual(
            batch.degradations[0].details,
            {"delay_seconds": "1.250"},
        )

    def test_report_final_rate_limit_keeps_completed_backoff_diagnostic(self) -> None:
        sleeps: list[float] = []
        transport = EastmoneyFixtureTransport(
            {
                1: TransportError("rate_limited", "sanitized rate limit"),
            }
        )
        gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(0.5,),
            sleeper=sleeps.append,
            jitter=lambda lower, upper: lower,
        )

        batch = EastmoneyReportOperation(transport, request_gate=gate).collect(
            industry_query()
        )

        self.assertEqual(len(transport.urls), 2)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(
            [(error.code, error.message) for error in batch.source_errors],
            [("rate_limited", "sanitized rate limit")],
        )
        self.assertEqual(
            [degradation.code for degradation in batch.degradations],
            ["rate_limit_backoff"],
        )

    def test_theme_report_uses_free_full_market_title_keyword_baseline(self) -> None:
        transport = EastmoneyFixtureTransport(
            {
                1: "eastmoney_stock_page_1.json",
                2: "eastmoney_stock_page_2.json",
            }
        )

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(theme_query())

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.title for item in batch.observations],
            [
                "AI服务器销售占比提升，毛利预期持续改善",
                "AI服务器需求强劲，收入及净利润持续高增",
            ],
        )
        request = parse_qs(urlparse(transport.urls[0]).query)
        self.assertEqual(request["qType"], ["0"])
        self.assertEqual(request["industryCode"], ["*"])
        self.assertEqual(request["pageSize"], ["100"])
        self.assertNotIn("code", request)
        self.assertEqual(
            batch.limitations,
            (
                "title_keyword_filter_not_semantic_search",
                "theme_report_universe_incomplete",
            ),
        )

    def test_theme_report_limit_does_not_stop_window_pagination(self) -> None:
        transport = EastmoneyFixtureTransport(
            {
                1: "eastmoney_stock_page_1.json",
                2: "eastmoney_stock_page_2.json",
            }
        )

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(theme_query(limit=1))

        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(len(transport.urls), 2)
        self.assertEqual(
            [parse_qs(urlparse(url).query)["pageNo"] for url in transport.urls],
            [["1"], ["2"]],
        )

    def test_theme_report_keeps_provider_identity_without_exchange_inference(
        self,
    ) -> None:
        transport = EastmoneyFixtureTransport(
            {
                1: "eastmoney_stock_page_1.json",
                2: "eastmoney_stock_page_2.json",
            }
        )

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(theme_query(limit=1))

        material = batch.observations[0]
        self.assertIsNone(material.subject)
        self.assertEqual(material.attributes["provider_stock_code"], "601138")
        self.assertEqual(material.attributes["provider_stock_name"], "工业富联")
        self.assertNotIn("exchange", material.attributes)

    def test_theme_report_source_failure_is_not_treated_as_an_empty_theme(
        self,
    ) -> None:
        transport = EastmoneyFixtureTransport(
            {1: TransportError("upstream_unavailable", "sanitized unavailable")}
        )

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(theme_query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(
            [(item.code, item.message) for item in batch.source_errors],
            [("upstream_unavailable", "sanitized unavailable")],
        )

    def test_theme_and_industry_request_still_collects_the_industry(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_industry_page_1.json"})
        query = ContentQuery(
            material_types=("research_report", "industry_report"),
            keywords=("人形机器人",),
            as_of="2026-08-02",
            published_from="2026-05-01",
            published_to="2026-08-02",
            limit=20,
            subject=None,
            parameters={"industry_code": "1037"},
        )

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(query)

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.material_type for item in batch.observations],
            ["industry_report"],
        )
        request = next(
            parse_qs(urlparse(url).query)
            for url in transport.urls
            if parse_qs(urlparse(url).query)["qType"] == ["1"]
        )
        self.assertEqual(request["qType"], ["1"])

    def test_stock_reports_cover_the_window_with_attributed_pdf_materials(
        self,
    ) -> None:
        transport = EastmoneyFixtureTransport(
            {
                1: "eastmoney_stock_page_1.json",
                2: "eastmoney_stock_page_2.json",
            }
        )

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(stock_query())

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 3)
        first = batch.observations[0]
        self.assertEqual(first.material_type, "research_report")
        self.assertEqual(first.source_role, "attributed_opinion")
        self.assertEqual(first.source_document_id, "AP202606181823651321")
        self.assertEqual(first.author, "金元证券")
        self.assertEqual(first.published_at, "2026-06-18T15:42:17.123000+08:00")
        self.assertEqual(
            first.document_locator,
            "https://pdf.dfcfw.com/pdf/H3_AP202606181823651321_1.pdf",
        )
        self.assertEqual(
            first.limitations,
            ("publication_time_timezone_not_explicit",),
        )
        self.assertEqual(first.subject, stock_query().subject)
        self.assertEqual(first.attributes["forecast_eps"]["2026"], "3.06")
        self.assertEqual(len(transport.urls), 2)
        second_page = parse_qs(urlparse(transport.urls[1]).query)
        self.assertEqual(second_page["pageNo"], ["2"])
        self.assertEqual(second_page["beginTime"], ["2026-05-01"])
        self.assertEqual(second_page["endTime"], ["2026-08-02"])

    def test_industry_reports_use_the_explicit_provider_industry_code(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_industry_page_1.json"})

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(industry_query())

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)
        material = batch.observations[0]
        self.assertEqual(material.material_type, "industry_report")
        self.assertIsNone(material.subject)
        self.assertEqual(material.attributes["provider_industry_code"], "1037")
        self.assertEqual(material.attributes["provider_industry_name"], "消费电子")
        request = parse_qs(urlparse(transport.urls[0]).query)
        self.assertEqual(request["qType"], ["1"])
        self.assertEqual(request["industryCode"], ["1037"])
        self.assertNotIn("code", request)

    def test_stock_report_identity_mismatch_fails_closed(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_wrong_security.json"})

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(stock_query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "identity_mismatch")

    def test_unknown_report_schema_is_not_treated_as_no_materials(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_unknown_schema.json"})

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(stock_query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "unknown_schema")

    def test_empty_first_page_is_indeterminate_without_a_business_status(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_empty_first_page.json"})

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(stock_query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(
            batch.source_errors[0].code,
            "indeterminate_empty_result",
        )

    def test_future_report_is_rejected_with_a_non_sensitive_diagnostic(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_future_row.json"})

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(stock_query())

        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(
            batch.observations[0].source_document_id,
            "AP202607311800000012",
        )
        self.assertEqual(batch.degradations[0].code, "future_material_rejected")
        self.assertNotIn("未来证券", batch.degradations[0].message)

    def test_limit_before_window_coverage_marks_pagination_incomplete(self) -> None:
        transport = EastmoneyFixtureTransport({1: "eastmoney_stock_page_1.json"})

        batch = EastmoneyReportOperation(
            transport, request_gate=no_wait_gate()
        ).collect(stock_query(limit=1))

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(len(transport.urls), 1)
        self.assertEqual(batch.limitations, ("pagination_incomplete",))

    def test_report_response_requires_http_200_text_plain_json(self) -> None:
        for status, content_type, expected_code in (
            (503, "text/plain", "upstream_http_error"),
            (200, "application/json", "unexpected_content_type"),
        ):
            with self.subTest(status=status, content_type=content_type):
                transport = EastmoneyFixtureTransport(
                    {1: "eastmoney_stock_page_1.json"},
                    status=status,
                    content_type=content_type,
                )

                batch = EastmoneyReportOperation(
                    transport, request_gate=no_wait_gate()
                ).collect(stock_query())

                self.assertFalse(batch.complete)
                self.assertEqual(batch.source_errors[0].code, expected_code)


class IwencaiContentSearchOperationTests(unittest.TestCase):
    def test_missing_or_disallowed_credentials_fail_without_exposing_values(
        self,
    ) -> None:
        transport = IwencaiFixtureTransport("iwencai_report_success.json")
        secret = "top-secret-iwencai-key"
        cases = (
            (
                IwencaiContentSearchOperation(
                    transport,
                    environ={"IWENCAI_API_KEY": secret},
                ),
                "credentials_not_allowed",
                semantic_query(allow_credentials=False),
            ),
            (
                IwencaiContentSearchOperation(
                    transport,
                    environ={},
                ),
                "missing_credential",
                semantic_query(),
            ),
        )

        for operation, expected_code, query in cases:
            with self.subTest(expected_code=expected_code):
                batch = operation.collect(query)
                self.assertFalse(batch.complete)
                self.assertEqual(batch.source_errors[0].code, expected_code)
                self.assertNotIn(secret, batch.source_errors[0].message)
        self.assertEqual(transport.posts, [])

    def test_semantic_report_search_uses_named_environment_credential(self) -> None:
        transport = IwencaiFixtureTransport("iwencai_report_success.json")
        operation = IwencaiContentSearchOperation(
            transport,
            environ={
                "IWENCAI_API_KEY": "fixture-key",
                "IWENCAI_BASE_URL": "https://openapi.iwencai.test",
            },
            trace_id_factory=lambda: "fixed-trace",
        )

        batch = operation.collect(semantic_query())

        self.assertEqual(batch.source_errors, ())
        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 1)
        material = batch.observations[0]
        self.assertEqual(material.material_type, "research_report")
        self.assertEqual(material.source_role, "attributed_opinion")
        self.assertEqual(material.source_document_id, "iwencai-report-1")
        self.assertEqual(material.author, "研究机构甲")
        self.assertIsNone(material.published_at)
        self.assertEqual(material.attributes["publication_date"], "2026-07-31")
        self.assertIn("publication_time_unknown", material.limitations)
        self.assertIn(
            "publication_time_precision_is_date_only",
            material.limitations,
        )
        url, headers, body = transport.posts[0]
        self.assertEqual(url, "https://openapi.iwencai.test/v1/comprehensive/search")
        self.assertEqual(headers["Authorization"], "Bearer fixture-key")
        self.assertEqual(headers["X-Claw-Trace-Id"], "fixed-trace")
        self.assertEqual(
            json.loads(body),
            {
                "channels": ["report"],
                "app_id": "AIME_SKILL",
                "query": "AI服务器 算力产业链",
                "size": 20,
            },
        )

    def test_semantic_search_requires_an_exact_date_only_publication_value(
        self,
    ) -> None:
        for invalid_value in (
            "2026-07-31garbage",
            "2026-07-31T15:42:17+08:00",
        ):
            with self.subTest(invalid_value=invalid_value):
                transport = IwencaiFixtureTransport(
                    "iwencai_report_success.json",
                    publish_date=invalid_value,
                )
                operation = IwencaiContentSearchOperation(
                    transport,
                    environ={"IWENCAI_API_KEY": "fixture-key"},
                )

                batch = operation.collect(semantic_query())

                self.assertEqual(batch.observations, ())
                self.assertEqual(
                    [error.code for error in batch.source_errors],
                    ["unknown_schema"],
                )

    def test_business_error_is_sanitized(self) -> None:
        transport = IwencaiFixtureTransport("iwencai_business_error.json")
        operation = IwencaiContentSearchOperation(
            transport,
            environ={"IWENCAI_API_KEY": "fixture-key"},
        )

        batch = operation.collect(semantic_query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.source_errors[0].code, "upstream_business_error")
        self.assertNotIn("credential rejected", batch.source_errors[0].message)
        self.assertNotIn("fixture-key", batch.source_errors[0].message)


class ThsConsensusMaterialOperationTests(unittest.TestCase):
    def test_current_consensus_snapshot_remains_an_attributed_opinion(self) -> None:
        transport = ThsFixtureTransport()

        batch = ThsConsensusMaterialOperation(
            transport,
            research_now=RETRIEVED_AT,
        ).collect(consensus_query())

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)
        material = batch.observations[0]
        self.assertEqual(material.material_type, "consensus_material")
        self.assertEqual(material.source_role, "attributed_opinion")
        self.assertIsNone(material.source_document_id)
        self.assertIsNone(material.published_at)
        self.assertEqual(material.retrieved_at, RETRIEVED_AT)
        self.assertEqual(material.subject, stock_query().subject)
        self.assertEqual(material.attributes["aggregation"], "source_aggregated_mean")
        self.assertEqual(
            material.attributes["forecasts"][0],
            {
                "year": 2026,
                "institutions": 20,
                "minimum": "2.80",
                "mean": "3.07",
                "maximum": "3.30",
            },
        )
        self.assertIn("current_snapshot_only", material.limitations)
        self.assertIn("publication_time_unknown", material.limitations)
        self.assertIn("source_document_id_unknown", material.limitations)
        self.assertIn(
            "aggregate_first_publication_time_unknown",
            material.limitations,
        )
        self.assertEqual(
            transport.urls,
            ["https://basic.10jqka.com.cn/new/601138/worth.html"],
        )

    def test_current_consensus_snapshot_cannot_be_used_for_historical_research(
        self,
    ) -> None:
        batch = ThsConsensusMaterialOperation(
            ThsFixtureTransport(),
            research_now=RETRIEVED_AT,
        ).collect(consensus_query(as_of="2026-08-01"))

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(
            batch.source_errors[0].code,
            "current_snapshot_not_historical",
        )


if __name__ == "__main__":
    unittest.main()
