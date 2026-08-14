"""Copied-install bootstrap for deterministic single-security workflow tests."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

INSTALLED_SCRIPTS = Path(os.environ["A_SHARE_RESEARCH_INSTALLED_SCRIPTS"])
FIXTURES = Path(os.environ["A_SHARE_RESEARCH_FIXTURES"])
sys.path.insert(0, str(INSTALLED_SCRIPTS))

fixture_path = FIXTURES / "valuation_research" / "fixture_cli.py"
spec = importlib.util.spec_from_file_location("_valuation_fixture", fixture_path)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load the deterministic valuation fixture")
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)

import a_share_research.cli as cli  # noqa: E402
import a_share_research.communication_sources as communication_sources  # noqa: E402
from a_share_research.capital_registry import (  # noqa: E402
    build_default_capital_operations,
)
from a_share_research.communication_sources import MootdxF10Operation  # noqa: E402
from a_share_research.content_registry import (  # noqa: E402
    build_default_content_operations,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.market_signal_registry import (  # noqa: E402
    build_default_market_signal_operations,
)
from a_share_research.source_throttle import SerialRequestGate  # noqa: E402


class FixedMootdxF10Client:
    def F10(self, *, symbol: str, name: str) -> str:  # noqa: N802
        if symbol != "601138" or name != "公司概况":
            raise AssertionError("unexpected fixed Mootdx F10 request")
        return "工业富联公司概况公开聚合文本"


mootdx_f10_operation = MootdxF10Operation(
    client_factory=FixedMootdxF10Client,
    clock=lambda: datetime.fromisoformat("2026-08-02T18:30:00+08:00"),
)

RETRIEVED_AT = datetime.fromisoformat("2026-08-02T18:30:00+08:00")
NO_WAIT_GATE = SerialRequestGate(
    minimum_interval_seconds=0,
    jitter_bounds=(0, 0),
    rate_limit_backoffs=(),
)
SCENARIO = os.environ.get("A_SHARE_SINGLE_SECURITY_SCENARIO", "normal")


def _file_response(
    path: Path,
    *,
    content_type: str = "application/json",
    replacements: dict[str, str] | None = None,
) -> HttpResponse:
    body = path.read_bytes()
    for original, replacement in (replacements or {}).items():
        body = body.replace(original.encode(), replacement.encode())
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=body,
        retrieved_at=RETRIEVED_AT,
    )


def _json_response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode(),
        retrieved_at=RETRIEVED_AT,
    )


class FormalContentTransport:
    def __init__(self) -> None:
        self._news_page = 0

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.netloc == "reportapi.eastmoney.com":
            if SCENARIO == "materials_failure":
                raise TransportError(
                    "upstream_unavailable",
                    "The deterministic report source is unavailable.",
                )
            page = int(query["pageNo"][0])
            return _file_response(
                FIXTURES
                / "research_content"
                / "reports"
                / f"eastmoney_stock_page_{page}.json",
                content_type="text/plain",
            )
        if parsed.netloc == "basic.10jqka.com.cn":
            return _file_response(
                FIXTURES
                / "research_content"
                / "reports"
                / "ths_consensus_material.html",
                content_type="text/html",
            )
        if parsed.netloc == "search-api-web.eastmoney.com":
            self._news_page += 1
            return _file_response(
                FIXTURES
                / "research_content"
                / "disclosures"
                / f"eastmoney_news_page_{self._news_page}.jsonp",
                content_type="text/javascript",
                replacements={
                    "300058": "601138",
                    "蓝色光标": "工业富联",
                },
            )
        if parsed.netloc == "query.sse.com.cn" and parsed.path.endswith(
            "queryCompanyBulletin.do"
        ):
            page = int(query["pageHelp.pageNo"][0])
            payload = json.loads(
                (
                    FIXTURES
                    / "research_content"
                    / "disclosures"
                    / f"sse_announcements_page_{page}.json"
                ).read_text()
            )
            payload["beginDate"] = query["beginDate"][0]
            payload["endDate"] = query["endDate"][0]
            payload["productId"] = query["productId"][0]
            payload["pageHelp"]["pageSize"] = int(query["pageHelp.pageSize"][0])
            return _json_response(payload)
        if (
            parsed.netloc == "www.cninfo.com.cn"
            and parsed.path == "/new/data/szse_stock.json"
        ):
            payload = {
                "stockList": [
                    {
                        "code": "601138",
                        "zwjc": "工业富联",
                        "orgId": "99000601138",
                        "category": "A股",
                    }
                ]
            }
            return _json_response(payload)
        raise AssertionError(f"unexpected formal content GET: {url}")

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        parsed = urlparse(url)
        if (
            parsed.netloc == "www.cninfo.com.cn"
            and parsed.path == "/new/hisAnnouncement/query"
        ):
            page = "2" if b"pageNum=2" in body else "1"
            return _file_response(
                FIXTURES
                / "research_content"
                / "disclosures"
                / f"cninfo_announcements_page_{page}.json",
                replacements={
                    "300058": "601138",
                    "蓝色光标": "工业富联",
                    "9900010147": "99000601138",
                },
            )
        if parsed.netloc == "irm.cninfo.com.cn":
            fixture = (
                "cninfo_lookup_300058.json"
                if parsed.path.endswith("queryKeyboardInfo")
                else "cninfo_questions_300058.json"
            )
            return _file_response(
                FIXTURES / "research_content" / "communications" / fixture,
                replacements={
                    "300058": "601138",
                    "蓝色光标": "工业富联",
                },
            )
        raise AssertionError(f"unexpected formal content POST: {url}")


class FormalCapitalTransport:
    replacements = {
        "300058": "601138",
        "蓝色光标": "工业富联",
        "600519": "601138",
        "贵州茅台": "工业富联",
    }

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.netloc == "push2his.eastmoney.com":
            return _json_response(
                {
                    "data": {
                        "code": "601138",
                        "market": 1,
                        "klines": [
                            f"2026-07-{day:02d},{1000 + index},-200,300,400,600,1.5"
                            for index, day in enumerate(range(27, 32))
                        ],
                    }
                }
            )
        if parsed.netloc != "datacenter-web.eastmoney.com":
            raise AssertionError(f"unexpected formal capital GET: {url}")
        report = query["reportName"][0]
        page = query.get("pageNumber", ["1"])[0]
        company_fixture = {
            "RPTA_WEB_RZRQ_GGMX": f"margin_page_{page}.json",
            "RPT_HOLDERNUMLATEST": "shareholder_count.json",
            "RPT_SHAREBONUS_DET": "dividend.json",
        }.get(report)
        if company_fixture is not None:
            return _file_response(
                FIXTURES / "company_capital" / company_fixture,
                replacements=self.replacements,
            )
        if report == "RPT_DATA_BLOCKTRADE":
            fixture = "block_trade.json"
        elif report == "RPT_LIFT_STAGE":
            payload = json.loads(
                (FIXTURES / "trading_events" / "lockup.json").read_text()
            )
            payload["result"]["data"] = [
                item
                for item in payload["result"]["data"]
                if item["FREE_DATE"].startswith("2026-08-20")
            ]
            payload["result"]["count"] = 1
            payload = json.loads(
                json.dumps(payload, ensure_ascii=False)
                .replace("300058", "601138")
                .replace("蓝色光标", "工业富联")
            )
            return _json_response(payload)
        elif report == "RPT_BILLBOARD_DAILYDETAILSBUY":
            fixture = "dragon_buy_seats.json"
        elif report == "RPT_BILLBOARD_DAILYDETAILSSELL":
            fixture = "dragon_sell_seats.json"
        elif "SECURITY_CODE" in query.get("filter", [""])[0]:
            fixture = "dragon_records.json"
        else:
            raise AssertionError(f"unexpected formal trading report: {report}")
        return _file_response(
            FIXTURES / "trading_events" / fixture,
            replacements=self.replacements,
        )

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        raise AssertionError("formal capital operations must use GET")


class FormalBoardTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlparse(url)
        if parsed.netloc != "push2.eastmoney.com":
            raise AssertionError(f"unexpected formal board GET: {url}")
        return _json_response(
            {
                "data": {
                    "secid": "1.601138",
                    "total": 1,
                    "diff": [
                        {
                            "f12": "BK0727",
                            "f14": "消费电子",
                            "f3": "1.25",
                            "f128": "工业富联",
                        }
                    ],
                }
            }
        )

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        raise AssertionError("formal board operation must use GET")


formal_content_transport = FormalContentTransport()
formal_capital_transport = FormalCapitalTransport()
formal_board_transport = FormalBoardTransport()


def _without_request_waits(operations: tuple[object, ...]) -> tuple[object, ...]:
    for operation in operations:
        if hasattr(operation, "_request_gate"):
            operation._request_gate = NO_WAIT_GATE
    return operations


formal_content_operations = _without_request_waits(
    tuple(
        mootdx_f10_operation
        if operation.operation_id == mootdx_f10_operation.operation_id
        else operation
        for operation in build_default_content_operations(
            formal_content_transport,
            allow_credentials=False,
            allow_fallback=False,
            research_now=RETRIEVED_AT,
        )
    )
)
formal_capital_operations = _without_request_waits(
    build_default_capital_operations(formal_capital_transport)
)
formal_board_operations = _without_request_waits(
    build_default_market_signal_operations(formal_board_transport)
)
transport = fixture_module.ValuationFixtureTransport()
original_research = cli.research


def fixture_research(request: object, **kwargs: object) -> object:
    kwargs["identity_transport"] = transport
    kwargs["research_now"] = datetime.fromisoformat("2026-08-02T18:30:00+08:00")
    kwargs["available_optional_dependencies"] = set()
    if SCENARIO == "default_inventory":
        communication_sources._default_mootdx_client = FixedMootdxF10Client
        communication_sources._china_now = lambda: RETRIEVED_AT
        kwargs["content_transport"] = formal_content_transport
        kwargs["capital_transport"] = formal_capital_transport
        kwargs["market_signal_transport"] = formal_board_transport
    else:
        kwargs["content_operations"] = formal_content_operations
        kwargs["capital_operations"] = formal_capital_operations
        kwargs["market_signal_operations"] = formal_board_operations
    return original_research(request, **kwargs)


cli.research = fixture_research
