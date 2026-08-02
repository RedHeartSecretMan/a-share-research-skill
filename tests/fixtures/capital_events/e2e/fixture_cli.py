#!/usr/bin/env python3
"""Offline CLI harness exercising the default capital-event registry."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[3]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
IDENTITY_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"
CAPITAL_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "company_capital"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # type: ignore[import-not-found]  # noqa: E402
from a_share_research.identity_sources import (  # type: ignore[import-not-found]  # noqa: E402
    HttpResponse,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)


def _response(path: Path) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=path.read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlsplit(url)
        if parsed.netloc == "query.sse.com.cn":
            return _response(IDENTITY_FIXTURES / "sse_600519.json")
        if parsed.netloc == "www.szse.cn":
            return _response(IDENTITY_FIXTURES / "szse_empty.json")
        if parsed.netloc == "www.cninfo.com.cn":
            return _response(IDENTITY_FIXTURES / "cninfo_stocks.json")
        if parsed.netloc == "datacenter-web.eastmoney.com":
            query = parse_qs(parsed.query)
            if query.get("reportName") != ["RPTA_WEB_RZRQ_GGMX"]:
                raise AssertionError("unexpected capital-event report")
            page = query.get("pageNumber", ["1"])[0]
            return _response(CAPITAL_FIXTURES / f"margin_page_{page}.json")
        raise AssertionError(f"unexpected fixture URL: {url}")


if __name__ == "__main__":
    transport = FixtureTransport()
    raise SystemExit(
        main(
            sys.argv[1:],
            identity_transport=transport,
            capital_transport=transport,
            research_now=RETRIEVED_AT,
            available_optional_dependencies=set(),
        )
    )
