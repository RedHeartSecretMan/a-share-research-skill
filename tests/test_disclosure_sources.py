from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.content_contract import ContentQuery  # noqa: E402
from a_share_research.disclosure_sources import (  # noqa: E402
    CninfoAnnouncementOperation,
    EastmoneyStockNewsOperation,
    SzseAnnouncementOperation,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateDiagnostic,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
FIXTURES = (
    Path(__file__).resolve().parent / "fixtures" / "research_content" / "disclosures"
)
RETRIEVED_AT = datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME)
T = TypeVar("T")


class FixedTransport:
    def __init__(
        self,
        *,
        get_responses: list[HttpResponse | Exception] | None = None,
        post_responses: list[HttpResponse | Exception] | None = None,
    ) -> None:
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls: list[tuple[str, dict[str, str]]] = []
        self.post_calls: list[tuple[str, dict[str, str], bytes]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.get_calls.append((url, headers))
        response = self.get_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        self.post_calls.append((url, headers, body))
        response = self.post_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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
                delay_seconds=1.4,
            ),
        )


def no_wait_gate() -> SerialRequestGate:
    return SerialRequestGate(
        minimum_interval_seconds=0,
        jitter_bounds=(0, 0),
        rate_limit_backoffs=(),
    )


def response(fixture: str, content_type: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=Path(FIXTURES, fixture).read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


def inline_response(
    body: str,
    content_type: str = "application/json",
    *,
    status: int = 200,
) -> HttpResponse:
    return HttpResponse(
        status=status,
        content_type=content_type,
        body=body.encode(),
        retrieved_at=RETRIEVED_AT,
    )


def query(
    *,
    material_type: str,
    limit: int = 3,
    parameters: dict[str, object] | None = None,
) -> ContentQuery:
    return ContentQuery(
        material_types=(material_type,),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-07-01",
        published_to="2026-08-02",
        limit=limit,
        subject={
            "security": {
                "exchange": "SZSE",
                "code": "300058",
                "type": "A_SHARE",
            },
            "name": "蓝色光标",
            "issuer": {
                "identifier": None,
                "security_relationship": "unverified",
            },
        },
        parameters=dict(parameters or {}),
    )


class EastmoneyStockNewsOperationTests(unittest.TestCase):
    def test_news_request_uses_injected_gate_and_keeps_diagnostic(self) -> None:
        transport = FixedTransport(
            get_responses=[response("eastmoney_news_page_1.jsonp", "text/javascript")]
        )
        gate = DiagnosticRequestGate()

        batch = EastmoneyStockNewsOperation(
            transport,
            page_size=2,
            request_gate=gate,
        ).collect(query(material_type="stock_news", limit=2))

        self.assertEqual(gate.calls, 1)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        self.assertEqual(
            batch.degradations[0].details,
            {"delay_seconds": "1.400"},
        )

    def test_news_final_rate_limit_keeps_completed_backoff_diagnostic(self) -> None:
        sleeps: list[float] = []
        transport = FixedTransport(
            get_responses=[
                TransportError("rate_limited", "sanitized rate limit"),
                TransportError("rate_limited", "sanitized rate limit"),
            ]
        )
        gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(0.5,),
            sleeper=sleeps.append,
            jitter=lambda lower, upper: lower,
        )

        batch = EastmoneyStockNewsOperation(
            transport,
            request_gate=gate,
        ).collect(query(material_type="stock_news"))

        self.assertEqual(len(transport.get_calls), 2)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(
            [(error.code, error.message) for error in batch.source_errors],
            [("rate_limited", "sanitized rate limit")],
        )
        self.assertEqual(
            [degradation.code for degradation in batch.degradations],
            ["rate_limit_backoff"],
        )

    def test_news_paginates_to_limit_without_claiming_subject_relationship(
        self,
    ) -> None:
        transport = FixedTransport(
            get_responses=[
                response("eastmoney_news_page_1.jsonp", "text/javascript"),
                response("eastmoney_news_page_2.jsonp", "text/javascript"),
            ]
        )
        research_query = query(material_type="stock_news")

        batch = EastmoneyStockNewsOperation(
            transport,
            page_size=2,
            request_gate=no_wait_gate(),
        ).collect(research_query)

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.source_document_id for item in batch.observations],
            ["NEWS-3", "NEWS-2", "NEWS-1"],
        )
        first = batch.observations[0]
        self.assertEqual(first.source_role, "attributed_opinion")
        self.assertEqual(first.published_at, "2026-08-01T16:20:30+08:00")
        self.assertEqual(first.subject, research_query.subject)
        self.assertEqual(first.attributes["subject_relationship"], "unverified")
        self.assertIn("subject_relationship_unverified", first.limitations)
        self.assertEqual(len(transport.get_calls), 2)
        pages = []
        for url, _headers in transport.get_calls:
            outer = parse_qs(urlsplit(url).query)
            inner = json.loads(outer["param"][0])
            pages.append(inner["param"]["cmsArticleWebOld"]["pageIndex"])
        self.assertEqual(pages, [1, 2])

    def test_http_success_with_no_news_is_an_explicit_source_failure(self) -> None:
        transport = FixedTransport(
            get_responses=[
                inline_response(
                    'jQuery_news({"code":0,"msg":"OK","hitsTotal":0,'
                    '"result":{"cmsArticleWebOld":[]}})',
                    "text/javascript",
                )
            ]
        )

        batch = EastmoneyStockNewsOperation(
            transport,
            request_gate=no_wait_gate(),
        ).collect(query(material_type="stock_news"))

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(
            [error.code for error in batch.source_errors], ["empty_response"]
        )

    def test_jsonp_must_close_and_report_a_successful_business_status(self) -> None:
        for body, expected_code in (
            (
                'jQuery_news({"code":0,"hitsTotal":1,"result":{"cmsArticleWebOld":[]}}',
                "unknown_schema",
            ),
            (
                'jQuery_news({"code":401,"msg":"denied","hitsTotal":0,'
                '"result":{"cmsArticleWebOld":[]}})',
                "upstream_business_error",
            ),
        ):
            with self.subTest(expected_code=expected_code):
                batch = EastmoneyStockNewsOperation(
                    FixedTransport(
                        get_responses=[inline_response(body, "text/javascript")]
                    ),
                    request_gate=no_wait_gate(),
                ).collect(query(material_type="stock_news"))

                self.assertFalse(batch.complete)
                self.assertEqual(
                    [error.code for error in batch.source_errors],
                    [expected_code],
                )

    def test_news_pagination_disconnect_keeps_partial_materials(self) -> None:
        transport = FixedTransport(
            get_responses=[
                response("eastmoney_news_page_1.jsonp", "text/javascript"),
                TransportError("upstream_unavailable", "temporary disconnect"),
            ]
        )

        batch = EastmoneyStockNewsOperation(
            transport,
            page_size=2,
            request_gate=no_wait_gate(),
        ).collect(query(material_type="stock_news"))

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 2)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["upstream_unavailable"],
        )


class CninfoAnnouncementOperationTests(unittest.TestCase):
    def test_announcements_resolve_official_route_and_paginate_to_limit(
        self,
    ) -> None:
        transport = FixedTransport(
            get_responses=[response("cninfo_stock_map.json", "application/json")],
            post_responses=[
                response("cninfo_announcements_page_1.json", "application/json"),
                response("cninfo_announcements_page_2.json", "application/json"),
            ],
        )
        research_query = query(material_type="announcement")

        batch = CninfoAnnouncementOperation(transport, page_size=2).collect(
            research_query
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.source_document_id for item in batch.observations],
            ["1225378221", "1225349961", "1225337565"],
        )
        first = batch.observations[0]
        self.assertEqual(
            first.source_document_namespace,
            "cninfo-szse-official-announcement",
        )
        self.assertEqual(first.source_role, "authoritative_disclosure")
        self.assertEqual(first.published_at, "2026-08-01T17:00:00+08:00")
        self.assertEqual(first.subject, research_query.subject)
        self.assertEqual(first.attributes["subject_relationship"], "unverified")
        self.assertIn("issuer_security_relationship_unverified", first.limitations)
        self.assertEqual(
            first.document_locator,
            "https://static.cninfo.com.cn/finalpage/2026-08-01/1225378221.PDF",
        )
        self.assertEqual(len(transport.get_calls), 1)
        self.assertEqual(len(transport.post_calls), 2)
        pages = []
        for _url, _headers, body in transport.post_calls:
            form = parse_qs(body.decode())
            self.assertEqual(form["stock"], ["300058,9900010147"])
            pages.append(int(form["pageNum"][0]))
        self.assertEqual(pages, [1, 2])

    def test_route_mapping_must_match_the_canonical_security_name(self) -> None:
        wrong_name = (
            Path(FIXTURES, "cninfo_stock_map.json")
            .read_text()
            .replace("蓝色光标", "另一家公司")
        )
        transport = FixedTransport(get_responses=[inline_response(wrong_name)])

        batch = CninfoAnnouncementOperation(transport).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["wrong_security_payload"],
        )
        self.assertEqual(transport.post_calls, [])

    def test_http_success_with_no_announcements_is_explicit_failure(self) -> None:
        transport = FixedTransport(
            get_responses=[response("cninfo_stock_map.json", "application/json")],
            post_responses=[
                inline_response(
                    '{"announcements":[],"hasMore":false,'
                    '"totalAnnouncement":0,"totalpages":0}'
                )
            ],
        )

        batch = CninfoAnnouncementOperation(transport).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors], ["empty_response"]
        )

    def test_second_page_disconnect_keeps_partial_materials_and_is_incomplete(
        self,
    ) -> None:
        transport = FixedTransport(
            get_responses=[response("cninfo_stock_map.json", "application/json")],
            post_responses=[
                response("cninfo_announcements_page_1.json", "application/json"),
                TransportError("upstream_unavailable", "temporary disconnect"),
            ],
        )

        batch = CninfoAnnouncementOperation(transport, page_size=2).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 2)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["upstream_unavailable"],
        )

    def test_announcement_requires_a_complete_publication_timestamp(self) -> None:
        missing_time = (
            Path(FIXTURES, "cninfo_announcements_page_1.json")
            .read_text()
            .replace("1785574800000", "null", 1)
        )
        transport = FixedTransport(
            get_responses=[response("cninfo_stock_map.json", "application/json")],
            post_responses=[inline_response(missing_time)],
        )

        batch = CninfoAnnouncementOperation(transport).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["publication_time_missing"],
        )


class SzseAnnouncementOperationTests(unittest.TestCase):
    def test_announcements_normalize_ann_id_and_paginate_to_limit(self) -> None:
        transport = FixedTransport(
            post_responses=[
                response("szse_announcements_page_1.json", "application/json"),
                response("szse_announcements_page_2.json", "application/json"),
            ]
        )
        research_query = query(material_type="announcement")

        batch = SzseAnnouncementOperation(transport, page_size=2).collect(
            research_query
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.source_document_id for item in batch.observations],
            ["1225378221", "1225349961", "1225337565"],
        )
        first = batch.observations[0]
        self.assertEqual(
            first.source_document_namespace,
            "cninfo-szse-official-announcement",
        )
        self.assertEqual(first.source_role, "authoritative_disclosure")
        self.assertEqual(first.published_at, "2026-08-01T17:00:00+08:00")
        self.assertEqual(first.subject, research_query.subject)
        self.assertEqual(first.attributes["subject_relationship"], "unverified")
        self.assertIn("issuer_security_relationship_unverified", first.limitations)
        self.assertEqual(
            first.document_locator,
            "https://disc.static.szse.cn/download/disc/disk03/finalpage/"
            "2026-08-01/uuid-3.PDF",
        )
        pages = []
        for _url, _headers, body in transport.post_calls:
            request = json.loads(body)
            self.assertEqual(request["stock"], ["300058"])
            pages.append(request["pageNum"])
        self.assertEqual(pages, [1, 2])

    def test_announcement_security_must_match_the_canonical_subject(self) -> None:
        wrong_security = (
            Path(FIXTURES, "szse_announcements_page_1.json")
            .read_text()
            .replace('"300058"', '"000001"')
        )
        transport = FixedTransport(post_responses=[inline_response(wrong_security)])

        batch = SzseAnnouncementOperation(transport).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["wrong_security_payload"],
        )

    def test_http_success_with_no_szse_announcements_is_explicit_failure(
        self,
    ) -> None:
        transport = FixedTransport(
            post_responses=[inline_response('{"announceCount":0,"data":[]}')]
        )

        batch = SzseAnnouncementOperation(transport).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors], ["empty_response"]
        )

    def test_szse_pagination_disconnect_keeps_partial_materials(self) -> None:
        transport = FixedTransport(
            post_responses=[
                response("szse_announcements_page_1.json", "application/json"),
                TransportError("upstream_unavailable", "temporary disconnect"),
            ]
        )

        batch = SzseAnnouncementOperation(transport, page_size=2).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 2)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["upstream_unavailable"],
        )

    def test_szse_announcement_requires_complete_publication_time(self) -> None:
        missing_time = (
            Path(FIXTURES, "szse_announcements_page_1.json")
            .read_text()
            .replace('"2026-08-01 17:00:00"', '"2026-08-01"', 1)
        )
        transport = FixedTransport(post_responses=[inline_response(missing_time)])

        batch = SzseAnnouncementOperation(transport).collect(
            query(material_type="announcement")
        )

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["publication_time_missing"],
        )


if __name__ == "__main__":
    unittest.main()
