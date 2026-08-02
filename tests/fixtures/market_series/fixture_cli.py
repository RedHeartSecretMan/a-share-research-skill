#!/usr/bin/env python3
"""Offline market-series process harness with fixed external responses."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
IDENTITY_FIXTURES = FIXTURES.parent / "identity_sources"
SCRIPTS = FIXTURES.parents[2] / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        scenario = os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO", "bluefocus")
        if "commonSoaQuery.do" in url:
            fixture = FIXTURES / "sse_510050_fund_list.json"
            content_type = "application/json"
        elif "snap/510050" in url:
            fixture = FIXTURES / "sse_510050_snapshot.json"
            content_type = "application/json"
        elif "getHistoryData" in url:
            fixture = FIXTURES / "szse_300058_daily.json"
            content_type = "application/json"
        elif "push2his.eastmoney.com" in url:
            fixture = FIXTURES / "eastmoney_sz300058_forward_adjusted.json"
            content_type = "application/json"
        elif "web.ifzq.gtimg.cn" in url:
            fixture = (
                FIXTURES / "tencent_sh510050_daily.json"
                if "sh510050" in url
                else FIXTURES / "tencent_sz300058_forward_adjusted.json"
                if "fqkline" in url
                else FIXTURES / "tencent_sz300058_daily.json"
            )
            content_type = "text/html; charset=UTF-8"
        elif "query.sse.com.cn" in url:
            fixture = IDENTITY_FIXTURES / "sse_empty.json"
            content_type = "application/json"
        elif "ShowReport/data" in url:
            fixture = IDENTITY_FIXTURES / "szse_300058.json"
            content_type = "application/json"
        elif "www.cninfo.com.cn" in url:
            fixture = IDENTITY_FIXTURES / "cninfo_current_orgs.json"
            content_type = "application/json"
        else:
            raise AssertionError(f"unexpected URL: {url}")
        body = fixture.read_bytes()
        if scenario == "adjusted_corporate_action" and "fqkline" in url:
            payload = json.loads(body)
            payload["data"]["sz300058"]["qfqday"] = payload["data"]["sz300058"][
                "qfqday"
            ][:5]
            payload["data"]["sz300058"]["qt"]["sz300058"][30] = "20260626163000"
            body = json.dumps(payload, ensure_ascii=False).encode()
        if scenario == "adjusted_corporate_action" and "push2his" in url:
            payload = json.loads(body)
            payload["data"]["klines"] = payload["data"]["klines"][:5]
            body = json.dumps(payload, ensure_ascii=False).encode()
        if "getHistoryData" in url and scenario in {
            "empty_response",
            "suspended_session",
            "wrong_security",
        }:
            payload = json.loads(body)
            if scenario == "suspended_session":
                payload["data"]["picupdata"][4][7] = 0
            elif scenario == "wrong_security":
                payload["data"]["code"] = "300059"
            else:
                payload["data"]["picupdata"] = []
            body = json.dumps(payload, ensure_ascii=False).encode()
        if "sh510050" in url and scenario == "etf_value_conflict":
            payload = json.loads(body)
            payload["data"]["sh510050"]["day"][0][2] = "3.032"
            body = json.dumps(payload, ensure_ascii=False).encode()
        if "sz300058" in url and scenario in {
            "corporate_action",
            "missing_session",
            "value_conflict",
        }:
            payload = json.loads(body)
            if scenario == "corporate_action":
                payload["data"]["sz300058"]["day"][4].append(
                    {
                        "nd": "2025",
                        "fh_sh": "0.1",
                        "djr": "2026-07-23",
                        "cqr": "2026-07-24",
                        "FHcontent": "10派0.1元",
                    }
                )
            elif scenario == "missing_session":
                payload["data"]["sz300058"]["day"].pop(4)
            else:
                payload["data"]["sz300058"]["day"][4][2] = "11.220"
            body = json.dumps(payload, ensure_ascii=False).encode()
        return HttpResponse(
            status=200,
            content_type=content_type,
            body=body,
            retrieved_at=(
                datetime(2026, 6, 26, 16, 30, tzinfo=timezone(timedelta(hours=8)))
                if scenario == "adjusted_corporate_action"
                else datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8)))
            ),
        )


if __name__ == "__main__":
    scenario = os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO", "bluefocus")
    raise SystemExit(
        main(
            sys.argv[1:],
            identity_transport=FixtureTransport(),
            research_now=(
                datetime(2026, 6, 26, 16, 30, tzinfo=timezone(timedelta(hours=8)))
                if scenario == "adjusted_corporate_action"
                else datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8)))
            ),
            available_optional_dependencies=set(),
        )
    )
