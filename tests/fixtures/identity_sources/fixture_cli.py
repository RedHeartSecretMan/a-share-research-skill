#!/usr/bin/env python3
"""Offline process harness that replaces only the external network boundary."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
SCRIPTS = FIXTURES.parents[2] / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402


class FixtureTransport:
    scenarios = {
        "default": {
            "query.sse.com.cn": "sse_600519.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_stocks.json",
        },
        "exchange_hint_conflict": {
            "query.sse.com.cn": "sse_empty.json",
            "www.szse.cn": "szse_000001.json",
            "www.cninfo.com.cn": "cninfo_stocks.json",
        },
        "szse_success": {
            "query.sse.com.cn": "sse_empty.json",
            "www.szse.cn": "szse_000001.json",
            "www.cninfo.com.cn": "cninfo_stocks.json",
        },
        "bse_name": {
            "query.sse.com.cn": "sse_empty.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_stocks.json",
        },
        "multiple_candidates": {
            "query.sse.com.cn": "sse_same_name.json",
            "www.szse.cn": "szse_same_name.json",
            "www.cninfo.com.cn": "cninfo_same_name.json",
        },
        "name_conflict": {
            "query.sse.com.cn": "sse_600519.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_name_conflict.json",
        },
        "exchange_conflict": {
            "query.sse.com.cn": "sse_600519.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_exchange_conflict.json",
        },
        "source_failure": {
            "query.sse.com.cn": "empty_body.txt",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_stocks.json",
        },
        "sse_name": {
            "productid=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0": "sse_empty.json",
            "productid=600519": "sse_600519.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_stocks.json",
        },
        "unknown_org": {
            "productid=601318": "sse_601318.json",
            "query.sse.com.cn": "sse_empty.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_numeric_org.json",
        },
        "bluefocus_name": {
            "query.sse.com.cn": "sse_empty.json",
            "www.szse.cn": "szse_300058.json",
            "www.cninfo.com.cn": "cninfo_current_orgs.json",
        },
        "industrial_fulian_name": {
            "query.sse.com.cn": "sse_601138.json",
            "www.szse.cn": "szse_empty.json",
            "www.cninfo.com.cn": "cninfo_current_orgs.json",
        },
    }

    def __init__(self) -> None:
        scenario = os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO", "default")
        self.routes = self.scenarios[scenario]

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        fixture = next(
            (name for marker, name in self.routes.items() if marker in url), None
        )
        if fixture is None:
            raise AssertionError(f"unexpected URL: {url}")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=Path(FIXTURES, fixture).read_bytes(),
            retrieved_at=datetime(
                2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))
            ),
        )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], identity_transport=FixtureTransport()))
