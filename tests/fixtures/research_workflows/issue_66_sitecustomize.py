"""Copied-installed transport and clock fixtures for Issue 66."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_clock_value = 0.0


def _fixed_monotonic() -> float:
    global _clock_value
    _clock_value += 2.0
    return _clock_value


def _fixed_sleep(delay: float) -> None:
    global _clock_value
    _clock_value += delay


time.monotonic = _fixed_monotonic
time.sleep = _fixed_sleep

INSTALLED_SCRIPTS = Path(sys.argv[0]).resolve().parent
sys.path.insert(0, str(INSTALLED_SCRIPTS))

import a_share_research.cli as cli  # noqa: E402
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.research_runtime import research as real_research  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 3, 19, 40, tzinfo=CHINA_STANDARD_TIME)
OBSERVED_AT = int(datetime(2026, 8, 3, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp())
FLASH_SCENARIO = os.environ.get("A_SHARE_ISSUE66_FLASH", "primary")
MAX_CARDINALITY_ROWS = int(os.environ.get("A_SHARE_ISSUE66_ROWS", "0"))
MAX_CARDINALITY_PADDING = "x" * int(os.environ.get("A_SHARE_ISSUE66_PAD", "0"))


def _many_rows(
    row: dict[str, object],
    mutate: Callable[[dict[str, object], int], None],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(MAX_CARDINALITY_ROWS):
        item = deepcopy(row)
        mutate(item, index)
        rows.append(item)
    return rows


def _max_cardinality_payload(payload: object) -> object:
    if MAX_CARDINALITY_ROWS == 0:
        return payload
    value = deepcopy(payload)

    if (
        isinstance(value, list)
        and value
        and isinstance(value[0], dict)
        and "STKCODE" in value[0]
    ):
        value = _many_rows(
            value[0],
            lambda row, index: row.update(
                STKCODE=f"605{index:03d}",
                STKNAME=f"focus-{index:03d}{MAX_CARDINALITY_PADDING}",
            ),
        )

    if not isinstance(value, dict):
        return value
    data = value.get("data")
    if isinstance(data, dict) and isinstance(data.get("diff"), list) and data["diff"]:
        source = data["diff"][0]
        if "f62" in source:
            data["diff"] = _many_rows(
                source,
                lambda row, index: row.update(
                    f12=f"BK{index:04d}",
                    f14=f"flow-{index:03d}{MAX_CARDINALITY_PADDING}",
                ),
            )
            data["total"] = MAX_CARDINALITY_ROWS
        elif "f104" in source:
            data["diff"] = _many_rows(
                source,
                lambda row, index: row.update(
                    f12=f"BK{index:04d}",
                    f14=f"industry-{index:03d}{MAX_CARDINALITY_PADDING}",
                ),
            )
            data["total"] = MAX_CARDINALITY_ROWS
    if isinstance(data, dict) and isinstance(data.get("pool"), list) and data["pool"]:
        source = data["pool"][0]
        data["pool"] = _many_rows(
            source,
            lambda row, index: row.update(
                c=f"300{index:03d}",
                n=f"limit-{index:03d}{MAX_CARDINALITY_PADDING}",
            ),
        )
        data["tc"] = MAX_CARDINALITY_ROWS
    if (
        isinstance(data, list)
        and data
        and isinstance(data[0], dict)
        and "reason" in data[0]
    ):
        data[:] = _many_rows(
            data[0],
            lambda row, index: row.update(
                code=f"300{index:03d}",
                name=f"theme-{index:03d}{MAX_CARDINALITY_PADDING}",
            ),
        )
    if (
        isinstance(data, dict)
        and isinstance(data.get("stock_list"), list)
        and data["stock_list"]
    ):
        source = data["stock_list"][0]
        data["stock_list"] = _many_rows(
            source,
            lambda row, index: row.update(
                order=index + 1,
                code=f"300{index:03d}",
                name=f"heat-{index:03d}{MAX_CARDINALITY_PADDING}",
            ),
        )
    if (
        isinstance(data, dict)
        and isinstance(data.get("roll_data"), list)
        and data["roll_data"]
    ):
        source = data["roll_data"][0]
        data["roll_data"] = _many_rows(
            source,
            lambda row, index: row.update(
                id=660000 + index,
                title=f"flash-{index:03d}{MAX_CARDINALITY_PADDING}",
                shareurl=f"https://www.cls.cn/detail/{660000 + index}",
            ),
        )

    result = value.get("result")
    if (
        isinstance(result, dict)
        and isinstance(result.get("data"), list)
        and result["data"]
    ):
        source = result["data"][0]
        if "c" in source:
            result["data"] = _many_rows(
                source,
                lambda row, index: row.update(
                    c=f"688{index:03d}",
                    n=f"abnormal-{index:03d}{MAX_CARDINALITY_PADDING}",
                ),
            )
        elif "SECURITY_CODE" in source:
            result["data"] = _many_rows(
                source,
                lambda row, index: row.update(
                    SECURITY_CODE=f"300{index:03d}",
                    SECURITY_NAME_ABBR=(f"dragon-{index:03d}{MAX_CARDINALITY_PADDING}"),
                ),
            )
        result["count"] = MAX_CARDINALITY_ROWS
        result["pages"] = 1
    return value


def _response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(_max_cardinality_payload(payload), ensure_ascii=False).encode(
            "utf-8"
        ),
        retrieved_at=RETRIEVED_AT,
    )


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        del headers
        parsed = urlsplit(url)
        if (
            parsed.netloc == "push2.eastmoney.com"
            and parsed.path == "/api/qt/clist/get"
        ):
            query = parse_qs(parsed.query)
            if query.get("fid") == ["f62"]:
                return _response(
                    {
                        "data": {
                            "total": 2,
                            "diff": [
                                {
                                    "f12": "BK001",
                                    "f14": "电子",
                                    "f124": OBSERVED_AT,
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
                                    "f124": OBSERVED_AT,
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
                )
            return _response(
                {
                    "data": {
                        "total": 2,
                        "diff": [
                            {
                                "f12": "BK1001",
                                "f14": "软件开发",
                                "f3": "3.50",
                                "f104": 12,
                                "f105": 3,
                                "f140": "领涨股",
                                "f136": "5.67",
                                "f124": OBSERVED_AT,
                            },
                            {
                                "f12": "BK1002",
                                "f14": "煤炭开采",
                                "f3": "-1.25",
                                "f104": 4,
                                "f105": 10,
                                "f140": "次领涨股",
                                "f136": "1.20",
                                "f124": OBSERVED_AT,
                            },
                        ],
                    }
                }
            )
        if parsed.netloc == "push2ex.eastmoney.com":
            if parsed.path.endswith("getTopicZTPool"):
                return _response(
                    {
                        "data": {
                            "tc": 1,
                            "pool": [
                                {
                                    "c": "300058",
                                    "n": "蓝色光标",
                                    "p": 12340,
                                    "zdp": "10.01",
                                    "amount": "200000000.12",
                                    "ltsz": "9000000000",
                                    "hs": "7.80",
                                    "lbc": 2,
                                    "fbt": 92500,
                                    "lbt": 145959,
                                    "fund": "123456789",
                                    "zbc": 1,
                                    "hybk": "文化传媒",
                                    "zttj": {"days": 3, "ct": 2},
                                }
                            ],
                        }
                    }
                )
            if parsed.path.endswith("getTopicDTPool"):
                return _response({"data": None})
            if parsed.path.endswith(("getTopicZBPool", "getYesterdayZTPool")):
                return _response({"data": {"tc": 0, "pool": []}})
        if parsed.netloc == "data.10jqka.com.cn":
            return _response({"status_code": 0, "data": {"total": 0, "info": []}})
        if parsed.netloc == "zx.10jqka.com.cn":
            return _response(
                {
                    "errocode": 0,
                    "data": [
                        {
                            "code": "300058",
                            "name": "蓝色光标",
                            "reason": "AI营销+智谱AI",
                            "zhangfu": "10.01",
                            "close": "12.34",
                            "huanshou": "8.50",
                            "chengjiaoe": "123456789.12",
                            "market": "深",
                        }
                    ],
                }
            )
        if parsed.netloc == "mobappconfig.securities.eastmoney.com":
            return _response(
                [
                    {
                        "MARKET": "1",
                        "STKCODE": "605255",
                        "STKNAME": "天普股份",
                        "VALIDATESTARTDATE": "2026-07-30",
                        "VALIDATEENDDATE": "2026-08-12",
                        "LINK_URL": "https://wap.18.cn/app/detail/818/3265",
                    }
                ]
            )
        if parsed.netloc == "dycalchis.eastmoney.com":
            return _response(
                {
                    "result": 0,
                    "msg": "",
                    "pages": 1,
                    "date": 20260803,
                    "open": 0,
                    "count": 1,
                    "data": [
                        {
                            "m": 0,
                            "c": "688001",
                            "n": "样例证券",
                            "s": 6,
                            "e": 4,
                            "x": 70.11,
                            "d": 9,
                            "t": 17.59,
                            "a": 5.03,
                            "o": 2,
                        }
                    ],
                }
            )
        if parsed.netloc == "dq.10jqka.com.cn":
            return _response(
                {
                    "data": {
                        "stock_list": [
                            {
                                "order": 1,
                                "code": "300058",
                                "name": "蓝色光标",
                                "tag": {
                                    "concept_tag": ["AI营销"],
                                    "popularity_tag": "热度上升",
                                },
                                "rate": "98.5",
                                "rise_and_fall": "10.01",
                                "hot_rank_chg": "2",
                            }
                        ]
                    }
                }
            )
        if parsed.netloc == "datacenter-web.eastmoney.com":
            report_name = parse_qs(parsed.query).get("reportName", [None])[0]
            if report_name == "RPT_MUTUAL_DEAL_HISTORY":
                raise AssertionError(
                    "northbound cutoff must fail before issuing an HTTP request"
                )
            if report_name == "RPT_DAILYBILLBOARD_DETAILSNEW":
                return _response(
                    {
                        "success": True,
                        "code": 0,
                        "result": {
                            "pages": 1,
                            "count": 1,
                            "data": [
                                {
                                    "SECURITY_CODE": "300058",
                                    "SECURITY_NAME_ABBR": "蓝色光标",
                                    "TRADE_DATE": "2026-08-03 00:00:00",
                                    "EXPLANATION": "日涨幅达到15%的前5只证券",
                                    "CLOSE_PRICE": 12.34,
                                    "CHANGE_RATE": 16.25,
                                    "BILLBOARD_NET_AMT": 50000000,
                                    "BILLBOARD_BUY_AMT": 150000000,
                                    "BILLBOARD_SELL_AMT": 100000000,
                                    "TURNOVERRATE": 9.5,
                                }
                            ],
                        },
                    }
                )
        if parsed.netloc == "www.cls.cn":
            if FLASH_SCENARIO == "fallback":
                return _response({"errno": 0, "data": {"roll_data": []}})
            published_at = int(
                datetime(2026, 8, 3, 10, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
            )
            return _response(
                {
                    "errno": 0,
                    "msg": "",
                    "data": {
                        "roll_data": [
                            {
                                "id": 660001,
                                "ctime": published_at,
                                "modified_time": published_at + 60,
                                "title": "A股市场公开快讯",
                                "brief": "公开市场事件摘要",
                                "content": "公开市场事件正文",
                                "shareurl": "https://www.cls.cn/detail/660001",
                                "stock_list": [],
                                "subjects": [],
                            }
                        ]
                    },
                }
            )
        if parsed.netloc == "np-weblist.eastmoney.com":
            if FLASH_SCENARIO != "fallback":
                raise AssertionError("fallback ran after primary success")
            request_query = parse_qs(parsed.query)
            return _response(
                {
                    "req_trace": request_query["req_trace"][0],
                    "code": "1",
                    "message": "success",
                    "data": {
                        "sortEnd": "1785748299000000",
                        "index": 1,
                        "total": 5000,
                        "size": 1,
                        "fastNewsList": [
                            {
                                "code": "202608030000000001",
                                "showTime": "2026-08-03 19:11:39",
                                "title": "全球市场公开快讯",
                                "summary": "公开事件摘要",
                                "realSort": "1785748299000000",
                                "stockList": [],
                            }
                        ],
                    },
                }
            )
        raise TransportError(
            "upstream_unavailable",
            "The fixed Issue 66 transport has no response for this enhancement.",
        )

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        del url, headers, body
        raise TransportError(
            "upstream_unavailable",
            "The fixed Issue 66 transport has no response for this enhancement.",
        )


_transport = FixtureTransport()


def _fixture_research(request: dict[str, object], **kwargs: object) -> object:
    del kwargs
    return real_research(
        request,
        identity_transport=_transport,
        content_transport=_transport,
        capital_transport=_transport,
        market_signal_transport=_transport,
        research_now=RETRIEVED_AT,
        available_optional_dependencies=set(),
    )


cli.research = _fixture_research
