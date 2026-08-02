from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.communication_sources import (  # noqa: E402
    ClsMarketFlashOperation,
    FallbackMarketFlashOperation,
)
from a_share_research.content_contract import ContentQuery  # noqa: E402
from a_share_research.content_registry import (  # noqa: E402
    build_default_content_operations,
)
from a_share_research.disclosure_sources import (  # noqa: E402
    SzseAnnouncementOperation,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.sse_disclosure_sources import (  # noqa: E402
    SseAnnouncementOperation,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 20, 30, tzinfo=CHINA_STANDARD_TIME)
FIXTURES = (
    Path(__file__).resolve().parent / "fixtures" / "research_content" / "disclosures"
)


class FixedTransport:
    def __init__(self, responses: list[HttpResponse | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.calls.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("SSE applicability checks must not issue POST requests")


def response(fixture: str, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        content_type="application/json",
        body=Path(FIXTURES, fixture).read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


def inline_response(
    body: str,
    *,
    status: int = 200,
    content_type: str = "application/json",
) -> HttpResponse:
    return HttpResponse(
        status=status,
        content_type=content_type,
        body=body.encode(),
        retrieved_at=RETRIEVED_AT,
    )


def query(
    *,
    exchange: str = "SSE",
    code: str = "601138",
    name: str = "工业富联",
    limit: int = 3,
) -> ContentQuery:
    return ContentQuery(
        material_types=("announcement",),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-07-01",
        published_to="2026-08-02",
        limit=limit,
        subject={
            "security": {
                "exchange": exchange,
                "code": code,
                "type": "A_SHARE",
            },
            "name": name,
            "issuer": {
                "identifier": None,
                "security_relationship": "unverified",
            },
        },
        parameters={},
    )


class ExchangeApplicabilityTests(unittest.TestCase):
    def test_szse_operation_is_a_complete_noop_for_an_sse_subject(self) -> None:
        transport = FixedTransport()

        batch = SzseAnnouncementOperation(transport).collect(query())

        self.assertTrue(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(transport.calls, [])

    def test_default_registry_includes_the_sse_announcement_operation(self) -> None:
        operations = build_default_content_operations(
            FixedTransport(),
            allow_credentials=False,
            allow_fallback=True,
            research_now=None,
        )

        self.assertTrue(
            any(
                isinstance(operation, SseAnnouncementOperation)
                for operation in operations
            )
        )
        self.assertEqual(
            sum(
                isinstance(operation, FallbackMarketFlashOperation)
                for operation in operations
            ),
            1,
        )
        self.assertFalse(
            any(
                isinstance(operation, ClsMarketFlashOperation)
                for operation in operations
            )
        )

    def test_default_registry_uses_only_primary_market_flash_when_fallback_is_disabled(
        self,
    ) -> None:
        operations = build_default_content_operations(
            FixedTransport(),
            allow_credentials=False,
            allow_fallback=False,
            research_now=None,
        )

        self.assertTrue(
            any(
                isinstance(operation, ClsMarketFlashOperation)
                for operation in operations
            )
        )
        self.assertFalse(
            any(
                isinstance(operation, FallbackMarketFlashOperation)
                for operation in operations
            )
        )

    def test_sse_operation_is_a_complete_noop_for_an_szse_subject(self) -> None:
        transport = FixedTransport()

        batch = SseAnnouncementOperation(transport).collect(
            query(exchange="SZSE", code="300058", name="蓝色光标")
        )

        self.assertTrue(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(transport.calls, [])


class SseAnnouncementOperationTests(unittest.TestCase):
    def test_official_announcements_paginate_and_keep_full_time_and_pdf_locator(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                response("sse_announcements_page_1.json"),
                response("sse_announcements_page_2.json"),
            ]
        )
        research_query = query()

        batch = SseAnnouncementOperation(transport, page_size=2).collect(research_query)

        self.assertTrue(batch.complete)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(
            [item.source_document_id for item in batch.observations],
            [
                "601138_20260802_A1",
                "601138_20260801_A2",
                "601138_20260730_A3",
            ],
        )
        first = batch.observations[0]
        self.assertEqual(first.source_role, "authoritative_disclosure")
        self.assertEqual(first.subject, research_query.subject)
        self.assertEqual(first.published_at, "2026-08-01T16:18:39+08:00")
        self.assertEqual(first.attributes["sse_disclosure_date"], "2026-08-02")
        self.assertEqual(
            first.document_locator,
            "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/"
            "2026-08-02/601138_20260802_A1.pdf",
        )
        pages = []
        for url, headers in transport.calls:
            parameters = parse_qs(urlsplit(url).query)
            self.assertEqual(parameters["productId"], ["601138"])
            self.assertEqual(parameters["beginDate"], ["2026-07-01"])
            self.assertEqual(parameters["endDate"], ["2026-08-02"])
            pages.append(int(parameters["pageHelp.pageNo"][0]))
            self.assertEqual(headers["Referer"], "https://www.sse.com.cn/")
        self.assertEqual(pages, [1, 2])

    def test_rate_limit_is_fail_closed_without_materials(self) -> None:
        transport = FixedTransport(
            [response("sse_announcements_page_1.json", status=429)]
        )

        batch = SseAnnouncementOperation(transport).collect(query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.observations, ())
        self.assertEqual(
            [error.code for error in batch.source_errors], ["rate_limited"]
        )

    def test_announcement_security_must_match_the_canonical_subject(self) -> None:
        wrong_security = (
            Path(FIXTURES, "sse_announcements_page_1.json")
            .read_text()
            .replace(
                '"SECURITY_CODE":"601138"',
                '"SECURITY_CODE":"600000"',
            )
        )

        batch = SseAnnouncementOperation(
            FixedTransport([inline_response(wrong_security)]), page_size=2
        ).collect(query())

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["wrong_security_payload"],
        )

    def test_announcement_requires_a_complete_index_add_time(self) -> None:
        missing_time = (
            Path(FIXTURES, "sse_announcements_page_1.json")
            .read_text()
            .replace(
                '"ADDDATE":"2026-08-01 16:18:39"',
                '"ADDDATE":"2026-08-01"',
            )
        )

        batch = SseAnnouncementOperation(
            FixedTransport([inline_response(missing_time)]), page_size=2
        ).collect(query())

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["publication_time_missing"],
        )

    def test_http_success_with_no_announcements_is_explicit_failure(self) -> None:
        empty = (
            '{"productId":"601138","isPagination":"true",'
            '"beginDate":"2026-07-01","endDate":"2026-08-02",'
            '"result":[],"pageHelp":{"data":[],"total":0,'
            '"pageCount":0,"pageNo":1,"pageSize":2}}'
        )

        batch = SseAnnouncementOperation(
            FixedTransport([inline_response(empty)]), page_size=2
        ).collect(query())

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors], ["empty_response"]
        )

    def test_unknown_content_type_is_not_treated_as_an_empty_result(self) -> None:
        body = Path(FIXTURES, "sse_announcements_page_1.json").read_text()

        batch = SseAnnouncementOperation(
            FixedTransport([inline_response(body, content_type="text/html")]),
            page_size=2,
        ).collect(query())

        self.assertFalse(batch.complete)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["unexpected_content_type"],
        )

    def test_pagination_disconnect_keeps_partial_materials_and_is_incomplete(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                response("sse_announcements_page_1.json"),
                TransportError("upstream_unavailable", "temporary disconnect"),
            ]
        )

        batch = SseAnnouncementOperation(transport, page_size=2).collect(query())

        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.observations), 2)
        self.assertEqual(
            [error.code for error in batch.source_errors],
            ["upstream_unavailable"],
        )


if __name__ == "__main__":
    unittest.main()
