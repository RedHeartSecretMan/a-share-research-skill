#!/usr/bin/env python3
"""Offline CLI harness exercising the default market-signal registry."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[3]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # type: ignore[import-not-found]  # noqa: E402
from a_share_research.identity_sources import (  # type: ignore[import-not-found]  # noqa: E402
    HttpResponse,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)


def _response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def _eastmoney_row(code: str, name: str) -> dict[str, object]:
    return {
        "c": code,
        "n": name,
        "p": 12340,
        "zdp": "10.01",
        "hs": "7.8",
        "lbc": 1,
        "fbt": 92500,
        "lbt": 145959,
        "fund": "12000000",
        "zbc": 0,
        "hybk": "文化传媒",
        "zttj": {"days": 1, "ct": 1},
    }


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlsplit(url)
        if parsed.netloc == "push2ex.eastmoney.com":
            endpoint = parsed.path.rsplit("/", 1)[-1]
            if endpoint == "getTopicZTPool":
                return _response(
                    {"data": {"tc": 1, "pool": [_eastmoney_row("300058", "蓝色光标")]}}
                )
            if endpoint == "getTopicZBPool":
                row = _eastmoney_row("300001", "特锐德")
                row.update({"ztp": 13000, "zf": "15.2", "zs": "1.1"})
                return _response({"data": {"tc": 1, "pool": [row]}})
            if endpoint in {"getTopicDTPool", "getYesterdayZTPool"}:
                return _response({"data": {"tc": 0, "pool": []}})
        if parsed.netloc == "data.10jqka.com.cn":
            return _response(
                {
                    "status_code": 0,
                    "data": {
                        "info": [
                            {
                                "code": "300058",
                                "name": "蓝色光标",
                                "latest": "12.34",
                                "change_rate": "10.01",
                                "reason_type": "AI营销+算力",
                                "limit_up_type": "换手板",
                                "limit_up_suc_rate": "0.8",
                                "open_num": 0,
                                "order_amount": "12000000",
                                "high_days": "首板",
                                "first_limit_up_time": 1785461400,
                                "is_again_limit": 0,
                            }
                        ]
                    },
                }
            )
        raise AssertionError(f"unexpected fixture URL: {url}")

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError(f"unexpected fixture POST: {url}")


if __name__ == "__main__":
    transport = FixtureTransport()
    raise SystemExit(
        main(
            sys.argv[1:],
            identity_transport=transport,
            market_signal_transport=transport,
            research_now=RETRIEVED_AT,
            available_optional_dependencies=set(),
        )
    )
