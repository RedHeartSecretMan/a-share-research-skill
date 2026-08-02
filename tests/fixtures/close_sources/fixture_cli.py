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
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        scenario = os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO", "sse_match")
        if "yunhq.sse.com.cn" in url and scenario in {
            "sse_match",
            "price_conflict",
            "stale_quote",
            "before_close",
            "suspended",
            "after_close",
            "weekend",
            "after_boundary",
            "empty_tencent",
            "historical_kline",
        }:
            fixture = "sse_600519_daily.json"
            content_type = "application/json"
        elif "www.szse.cn" in url and scenario in {"szse_match", "holiday"}:
            fixture = (
                "szse_000001_holiday.json"
                if scenario == "holiday"
                else "szse_000001_daily.json"
            )
            content_type = "application/json"
        elif "web.ifzq.gtimg.cn" in url and scenario == "empty_tencent":
            return HttpResponse(
                status=200,
                content_type="text/html; charset=UTF-8",
                body=(
                    b'{"code":0,"data":{"sh600519":{"day":[],"qt":{"sh600519":[]}}}}'
                ),
                retrieved_at=datetime(
                    2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))
                ),
            )
        elif "web.ifzq.gtimg.cn" in url:
            fixtures = {
                "sse_match": "tencent_sh600519_daily.json",
                "after_close": "tencent_sh600519_daily.json",
                "weekend": "tencent_sh600519_daily.json",
                "price_conflict": "tencent_sh600519_price_conflict.json",
                "stale_quote": "tencent_sh600519_stale.json",
                "before_close": "tencent_sh600519_intraday.json",
                "suspended": "tencent_sh600519_suspended.json",
                "after_boundary": "tencent_sh600519_after_boundary.json",
                "historical_kline": "tencent_sh600519_history.json",
                "szse_match": "tencent_sz000001_daily.json",
                "holiday": "tencent_sz000001_holiday.json",
            }
            fixture = fixtures[scenario]
            content_type = "text/html; charset=UTF-8"
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return HttpResponse(
            status=200,
            content_type=content_type,
            body=Path(FIXTURES, fixture).read_bytes(),
            retrieved_at=datetime(
                2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))
            ),
        )


if __name__ == "__main__":
    scenario = os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO", "sse_match")
    keyword_arguments = {"identity_transport": FixtureTransport()}
    if scenario == "before_close":
        keyword_arguments["research_now"] = datetime(
            2026, 7, 31, 14, 30, tzinfo=timezone(timedelta(hours=8))
        )
    elif scenario == "suspended":
        keyword_arguments["research_now"] = datetime(
            2026, 8, 1, 16, 30, tzinfo=timezone(timedelta(hours=8))
        )
    elif scenario == "after_close":
        keyword_arguments["research_now"] = datetime(
            2026, 7, 31, 16, 30, tzinfo=timezone(timedelta(hours=8))
        )
    elif scenario in {"weekend", "future"}:
        keyword_arguments["research_now"] = datetime(
            2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))
        )
    elif scenario == "holiday":
        keyword_arguments["research_now"] = datetime(
            2026, 2, 23, 10, 30, tzinfo=timezone(timedelta(hours=8))
        )
    raise SystemExit(main(sys.argv[1:], **keyword_arguments))
