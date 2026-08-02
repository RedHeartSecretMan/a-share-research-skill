#!/usr/bin/env python3
"""Offline CLI harness exercising the default research-content registry."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[3]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
IDENTITY_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"
REPORT_FIXTURES = FIXTURES.parent / "reports"
DISCLOSURE_FIXTURES = FIXTURES.parent / "disclosures"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # type: ignore[import-not-found]  # noqa: E402
from a_share_research.identity_sources import (  # type: ignore[import-not-found]  # noqa: E402
    HttpResponse,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)


def _response(
    path: Path,
    *,
    content_type: str = "application/json",
) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=path.read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


class FixtureIdentityTransport:
    """Resolve only the BlueFocus fixture; theme requests never call this seam."""

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlsplit(url).netloc
        if host == "query.sse.com.cn":
            return HttpResponse(
                status=200,
                content_type="application/json",
                body=b'{"result":[]}',
                retrieved_at=RETRIEVED_AT,
            )
        if host == "www.szse.cn":
            return _response(IDENTITY_FIXTURES / "szse_300058.json")
        if host == "www.cninfo.com.cn":
            return _response(IDENTITY_FIXTURES / "cninfo_current_orgs.json")
        raise AssertionError("unexpected identity source operation")


class FixtureContentTransport:
    """Serve fixed source responses and assert credential/request contracts."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self._news_page = 0

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlsplit(url).netloc
        if self.scenario == "theme_report" and host == "reportapi.eastmoney.com":
            page = int(parse_qs(urlsplit(url).query)["pageNo"][0])
            return _response(
                REPORT_FIXTURES / f"eastmoney_stock_page_{page}.json",
                content_type="text/plain",
            )
        if self.scenario == "bluefocus_disclosures_news":
            if host == "search-api-web.eastmoney.com":
                self._news_page += 1
                return _response(
                    DISCLOSURE_FIXTURES
                    / f"eastmoney_news_page_{self._news_page}.jsonp",
                    content_type="text/javascript",
                )
            if (
                host == "www.cninfo.com.cn"
                and urlsplit(url).path == "/new/data/szse_stock.json"
            ):
                return _response(DISCLOSURE_FIXTURES / "cninfo_stock_map.json")
        raise AssertionError("unexpected content GET operation")

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        host = urlsplit(url).netloc
        if self.scenario == "theme_report":
            expected_secret = os.environ.get("IWENCAI_API_KEY")
            if not expected_secret or headers.get("Authorization") != (
                f"Bearer {expected_secret}"
            ):
                raise AssertionError("named iWencai credential was not used")
            if url != "https://iwencai.fixture.test/v1/comprehensive/search":
                raise AssertionError("named iWencai base URL was not used")
            payload = json.loads(body)
            if payload != {
                "channels": ["report"],
                "app_id": "AIME_SKILL",
                "query": "AI服务器 算力产业链",
                "size": 20,
            }:
                raise AssertionError("unexpected semantic report request")
            return _response(REPORT_FIXTURES / "iwencai_report_success.json")
        if self.scenario == "bluefocus_disclosures_news":
            if (
                host == "www.cninfo.com.cn"
                and urlsplit(url).path == "/new/hisAnnouncement/query"
            ):
                page = "2" if b"pageNum=2" in body else "1"
                return _response(
                    DISCLOSURE_FIXTURES / f"cninfo_announcements_page_{page}.json"
                )
            if (
                host == "www.szse.cn"
                and urlsplit(url).path == "/api/disc/announcement/annList"
            ):
                payload = json.loads(body)
                page = payload.get("pageNum")
                if page not in {1, 2}:
                    raise AssertionError("unexpected SZSE fixture page")
                return _response(
                    DISCLOSURE_FIXTURES / f"szse_announcements_page_{page}.json"
                )
        raise AssertionError("unexpected content POST operation")


if __name__ == "__main__":
    scenario = os.environ.get("A_SHARE_RESEARCH_CONTENT_E2E_SCENARIO", "")
    if scenario not in {"theme_report", "bluefocus_disclosures_news"}:
        raise SystemExit("invalid fixture scenario")
    raise SystemExit(
        main(
            sys.argv[1:],
            identity_transport=FixtureIdentityTransport(),
            content_transport=FixtureContentTransport(scenario),
            research_now=RETRIEVED_AT,
            available_optional_dependencies=set(),
        )
    )
