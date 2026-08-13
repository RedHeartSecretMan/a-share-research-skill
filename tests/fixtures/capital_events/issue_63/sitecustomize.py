"""Copied-installed default-registry fixture for Issue 63."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

INSTALLED_SCRIPTS = Path(sys.argv[0]).resolve().parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(INSTALLED_SCRIPTS))

import a_share_research.cli as cli  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.research_runtime import research as real_research  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)
SCENARIO = os.environ.get("A_SHARE_ISSUE63_SCENARIO", "stock_flow")


def _response(path: Path) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=path.read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


def _inline_response(payload: dict[str, object], *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlsplit(url).netloc
        if host == "query.sse.com.cn":
            if SCENARIO == "trading_partial":
                return _response(FIXTURES / "identity_sources" / "sse_empty.json")
            return _response(FIXTURES / "identity_sources" / "sse_600519.json")
        if host == "www.szse.cn":
            if SCENARIO == "trading_partial":
                return _response(FIXTURES / "identity_sources" / "szse_300058.json")
            return _response(FIXTURES / "identity_sources" / "szse_empty.json")
        if host == "www.cninfo.com.cn":
            if SCENARIO == "trading_partial":
                return _response(
                    FIXTURES / "identity_sources" / "cninfo_current_orgs.json"
                )
            return _response(FIXTURES / "identity_sources" / "cninfo_stocks.json")
        if host == "push2his.eastmoney.com":
            payload = {
                "data": {
                    "code": "600519",
                    "market": 1,
                    "klines": [
                        f"2026-07-{day:02d},{1000 + index},-200,300,400,600,1.5"
                        for index, day in enumerate(range(21, 26))
                    ],
                }
            }
            return HttpResponse(
                status=200,
                content_type="application/json",
                body=json.dumps(payload).encode("utf-8"),
                retrieved_at=RETRIEVED_AT,
            )
        if host == "push2.eastmoney.com" and SCENARIO == "board_flow":
            payload = {
                "data": {
                    "total": 2,
                    "diff": [
                        {
                            "f12": "BK001",
                            "f14": "电子",
                            "f124": 1785427200,
                            "f3": "1.25",
                            "f62": "125000000.50",
                            "f184": "2.50",
                            "f204": "测试股份",
                            "f66": "50000000",
                            "f72": "40000000",
                            "f78": "30000000",
                            "f84": "5000000.50",
                        },
                        {
                            "f12": "BK002",
                            "f14": "通信",
                            "f124": 1785427200,
                            "f3": "0.75",
                            "f62": "100000000",
                            "f184": "2.00",
                            "f204": "样例股份",
                            "f66": "40000000",
                            "f72": "30000000",
                            "f78": "20000000",
                            "f84": "10000000",
                        },
                    ],
                }
            }
            return HttpResponse(
                status=200,
                content_type="application/json",
                body=json.dumps(payload).encode("utf-8"),
                retrieved_at=RETRIEVED_AT,
            )
        if host == "datacenter-web.eastmoney.com" and SCENARIO == "company_capital":
            query = parse_qs(urlsplit(url).query)
            report = query["reportName"][0]
            page = query.get("pageNumber", ["1"])[0]
            fixture = {
                "RPTA_WEB_RZRQ_GGMX": f"margin_page_{page}.json",
                "RPT_HOLDERNUMLATEST": "shareholder_count.json",
                "RPT_SHAREBONUS_DET": "dividend.json",
            }.get(report)
            if fixture is None:
                raise AssertionError("unexpected Issue 63 company-capital report")
            return _response(FIXTURES / "company_capital" / fixture)
        if host == "datacenter-web.eastmoney.com" and SCENARIO == "trading_partial":
            report = parse_qs(urlsplit(url).query)["reportName"][0]
            if report == "RPT_DATA_BLOCKTRADE":
                return _response(FIXTURES / "trading_events" / "block_trade.json")
            if report == "RPT_LIFT_STAGE":
                return _inline_response({}, status=500)
            raise AssertionError("unexpected Issue 63 trading-partial report")
        if host == "datacenter-web.eastmoney.com" and SCENARIO == "trading_empty":
            report = parse_qs(urlsplit(url).query)["reportName"][0]
            if report != "RPT_DAILYBILLBOARD_DETAILSNEW":
                raise AssertionError("unexpected Issue 63 trading-empty report")
            return _inline_response(
                {
                    "success": True,
                    "code": 0,
                    "result": {"pages": 1, "count": 0, "data": []},
                }
            )
        raise AssertionError("unexpected Issue 63 source operation")


_transport = FixtureTransport()


def _fixture_research(request: dict[str, object], **kwargs: object) -> object:
    del kwargs
    return real_research(
        request,
        identity_transport=_transport,
        capital_transport=_transport,
        research_now=RETRIEVED_AT,
        available_optional_dependencies=set(),
    )


cli.research = _fixture_research
