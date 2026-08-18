#!/usr/bin/env python3
"""Offline process harness for automatic valuation research."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parent
SHARED_FIXTURES = FIXTURES.parent / "identity_sources"
SCRIPTS = FIXTURES.parents[2] / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402

SECURITIES = {
    "601138": {
        "exchange": "SSE",
        "name": "工业富联",
        "full_name": "富士康工业互联网股份有限公司",
        "price": "56.700",
        "shares": "19844092284",
    },
    "600519": {
        "exchange": "SSE",
        "name": "贵州茅台",
        "full_name": "贵州茅台酒股份有限公司",
        "price": "1350.60",
        "shares": "1000000000",
    },
    "601318": {
        "exchange": "SSE",
        "name": "中国平安",
        "full_name": "中国平安保险（集团）股份有限公司",
        "price": "64.00",
        "shares": "1000000000",
    },
    "300058": {
        "exchange": "SZSE",
        "name": "蓝色光标",
        "full_name": "北京蓝色光标数据科技股份有限公司",
        "price": "14.35",
        "shares": "1000000000",
    },
    "000001": {
        "exchange": "SZSE",
        "name": "平安银行",
        "full_name": "平安银行股份有限公司",
        "price": "11.00",
        "shares": "1000000000",
    },
}


def _matching_security(clue: str) -> tuple[str, dict[str, str]] | None:
    return next(
        (
            (code, security)
            for code, security in SECURITIES.items()
            if clue in {code, security["name"], security["full_name"]}
        ),
        None,
    )


def _json_response(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


def _near_envelope_financial_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        raise AssertionError("financial fixture payload must be an object")
    result = payload.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    report_list = data.get("report_list") if isinstance(data, dict) else None
    if not isinstance(report_list, dict) or not report_list:
        raise AssertionError("financial fixture report list is missing")
    template = next(iter(report_list.values()))
    if not isinstance(template, dict) or not isinstance(template.get("data"), list):
        raise AssertionError("financial fixture report template is invalid")
    periods = (
        "20260331",
        "20251231",
        "20250930",
        "20250630",
        "20250331",
        "20241231",
        "20240930",
        "20240630",
    )
    data["report_list"] = {
        period: {
            **template,
            "publish_date": "20260801",
            "update_time": 1785542400 + index,
            "data": [
                *template["data"],
                *[
                    {
                        "item_title": f"未使用扩展科目{period}-{item_index:04d}",
                        "item_value": "1",
                        "item_tongbi": None,
                    }
                    for item_index in range(557)
                ],
            ],
        }
        for index, period in enumerate(periods)
    }
    return payload


def _workflow_daily_dates() -> list[str]:
    current = date(2026, 7, 31)
    values: list[str] = []
    while len(values) < 250:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current -= timedelta(days=1)
    return list(reversed(values))


class ValuationFixtureTransport:
    def __init__(self) -> None:
        self._workflow_close_calls = {"sse": 0, "tencent": 0}

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        content_type = "application/json"
        filename: str | None = None
        if "query.sse.com.cn" in parsed.netloc:
            clue = query["productid"][0]
            match = _matching_security(clue)
            rows = []
            if match is not None and match[1]["exchange"] == "SSE":
                code, security = match
                rows.append(
                    {
                        "SECURITY_CODE_A": code,
                        "SECURITY_ABBR_A": security["name"],
                        "FULLNAME": security["full_name"],
                        "STATE_CODE_A_DESC": "上市",
                        "COMPANY_CODE": code,
                    }
                )
            body = _json_response({"result": rows})
        elif "www.szse.cn" in parsed.netloc and "/api/report/" in parsed.path:
            clue = query["txtDMorJC"][0]
            match = _matching_security(clue)
            rows = []
            if match is not None and match[1]["exchange"] == "SZSE":
                code, security = match
                rows.append(
                    {
                        "agdm": code,
                        "agjc": security["name"],
                        "agssrq": "2000-01-01",
                    }
                )
            body = _json_response(
                [
                    {
                        "metadata": {
                            "catalogid": "1110",
                            "tabkey": "tab1",
                            "recordcount": len(rows),
                        },
                        "data": rows,
                        "error": None,
                    }
                ]
            )
        elif "www.cninfo.com.cn" in parsed.netloc:
            body = _json_response(
                {
                    "stockList": [
                        {
                            "code": code,
                            "category": "A股",
                            "orgId": f"99000{index:05d}",
                            "zwjc": security["name"],
                        }
                        for index, (code, security) in enumerate(
                            SECURITIES.items(), start=1
                        )
                    ]
                }
            )
        elif "yunhq.sse.com.cn" in parsed.netloc:
            code = parsed.path.split("/")[-1]
            security = SECURITIES[code]
            price = security["price"]
            use_workflow_history = (
                os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO")
                in {"single_security_history", "single_security_history_conflict"}
                and self._workflow_close_calls["sse"] == 0
            )
            self._workflow_close_calls["sse"] += 1
            dates = _workflow_daily_dates() if use_workflow_history else ["2026-07-31"]
            body = _json_response(
                {
                    "code": code,
                    "total": len(dates),
                    "begin": 0,
                    "end": len(dates) - 1,
                    "kline": [
                        [day.replace("-", ""), price, price, price, price, "1000000"]
                        for day in dates
                    ],
                }
            )
        elif "www.szse.cn" in parsed.netloc and "getHistoryData" in parsed.path:
            code = query["code"][0]
            security = SECURITIES[code]
            price = security["price"]
            body = _json_response(
                {
                    "data": {
                        "code": code,
                        "picupdata": [
                            [
                                "2026-07-31",
                                price,
                                price,
                                price,
                                price,
                                "0",
                                "0",
                                "10000",
                                "10000000",
                            ]
                        ],
                    }
                }
            )
        elif "web.ifzq.gtimg.cn" in parsed.netloc:
            query_security = query["param"][0].split(",", 1)[0]
            code = query_security[2:]
            security = SECURITIES[code]
            price = security["price"]
            quote = [""] * 47
            quote[1] = security["name"]
            quote[2] = code
            quote[3] = price
            quote[4] = price
            quote[6] = "10000"
            quote[30] = "20260731161450"
            quote[39] = "910039.39"
            quote[44] = "910044.44"
            quote[45] = "910045.45"
            quote[46] = "910046.46"
            use_workflow_history = (
                os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO")
                in {"single_security_history", "single_security_history_conflict"}
                and self._workflow_close_calls["tencent"] == 0
            )
            self._workflow_close_calls["tencent"] += 1
            dates = _workflow_daily_dates() if use_workflow_history else ["2026-07-31"]
            body = _json_response(
                {
                    "code": 0,
                    "data": {
                        query_security: {
                            "day": [
                                [day, price, price, price, price, "10000"]
                                for day in dates
                            ],
                            "qt": {query_security: quote},
                        }
                    },
                }
            )
            content_type = "text/html; charset=UTF-8"
        elif (
            parsed.netloc.endswith("eastmoney.com")
            and "/api/qt/stock/get" in parsed.path
        ):
            code = query["secid"][0].split(".", 1)[1]
            security = SECURITIES[code]
            market_cap = Decimal(security["price"]) * Decimal(security["shares"])
            body = _json_response(
                {
                    "data": {
                        "f57": code,
                        "f58": security["name"],
                        "f84": security["shares"],
                        "f85": security["shares"],
                        "f116": format(market_cap, "f"),
                        "f117": format(market_cap, "f"),
                        "f189": "20000101",
                        "f43": security["price"],
                    }
                }
            )
        elif "quotes.sina.cn" in parsed.netloc:
            source = query["source"][0]
            filename = {
                "lrb": "sina_601138_income.json",
                "fzb": "sina_601138_balance.json",
                "llb": "sina_601138_cashflow.json",
            }[source]
            body = Path(FIXTURES, filename).read_bytes()
        elif "basic.10jqka.com.cn" in parsed.netloc:
            filename = "ths_601138_worth.html"
            body = Path(FIXTURES, filename).read_bytes()
            content_type = "text/html; charset=gbk"
        else:
            raise AssertionError(f"unexpected URL: {url}")

        scenario = os.environ.get("A_SHARE_RESEARCH_TEST_SCENARIO", "default")
        if "eastmoney.com" in parsed.netloc and scenario in {
            "provider_mcap_conflict",
            "single_security_history_conflict",
        }:
            payload = json.loads(body)
            payload["data"]["f116"] = "1"
            body = _json_response(payload)
        if "yunhq.sse.com.cn" in parsed.netloc and scenario in {
            "missing_price",
            "missing_price_identity_conflict",
        }:
            payload = json.loads(body)
            payload["total"] = 0
            payload["kline"] = []
            body = _json_response(payload)
        if (
            parsed.netloc.endswith("eastmoney.com")
            and "/api/qt/stock/get" in parsed.path
            and scenario == "missing_price_identity_conflict"
        ):
            payload = json.loads(body)
            payload["data"]["f58"] = "贵州茅台"
            body = _json_response(payload)
        if "eastmoney.com" in parsed.netloc and scenario == "missing_shares":
            body = _json_response({"data": None})
        if "eastmoney.com" in parsed.netloc and scenario == "provider_mcap_rounding":
            payload = json.loads(body, parse_float=Decimal)
            payload["data"]["f116"] = str(
                Decimal(payload["data"]["f116"]) + Decimal("0.0001")
            )
            body = _json_response(payload)
        if filename == "sina_601138_income.json" and scenario in {
            "negative_profit",
            "financial_scope_conflict",
            "irrelevant_duplicate_financial_item",
            "required_duplicate_financial_item",
        }:
            payload = json.loads(body)
            reports = payload["result"]["data"]["report_list"]
            if scenario == "negative_profit":
                reports["20251231"]["data"][1]["item_value"] = "-35285561000.000000"
            elif scenario == "financial_scope_conflict":
                reports["20260331"]["rType"] = "母公司期末"
            else:
                title = (
                    "利息收入"
                    if scenario == "irrelevant_duplicate_financial_item"
                    else "归属于母公司所有者的净利润"
                )
                reports["20260331"]["data"].extend(
                    [
                        {"item_title": title, "item_value": "1"},
                        {"item_title": title, "item_value": "2"},
                    ]
                )
            body = json.dumps(payload, ensure_ascii=False).encode()
        if (
            filename is not None
            and filename.startswith("sina_")
            and scenario == "missing_financials"
        ):
            body = b""
        if filename == "ths_601138_worth.html" and (
            scenario == "missing_forecast"
            or (scenario == "one_missing_forecast" and "/601138/" in parsed.path)
        ):
            body = b"<html><body><p>No forecast coverage</p></body></html>"
        if (
            filename is not None
            and filename.startswith("sina_")
            and scenario == "near_envelope_comparison"
        ):
            body = _json_response(_near_envelope_financial_payload(json.loads(body)))
        return HttpResponse(
            status=200,
            content_type=content_type,
            body=body,
            retrieved_at=datetime(
                2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))
            ),
        )


if __name__ == "__main__":
    raise SystemExit(
        main(
            sys.argv[1:],
            identity_transport=ValuationFixtureTransport(),
            research_now=datetime(
                2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))
            ),
            available_optional_dependencies=set(),
        )
    )
