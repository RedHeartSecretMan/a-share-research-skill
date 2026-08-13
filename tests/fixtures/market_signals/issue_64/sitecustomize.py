"""Copied-installed production-owner fixtures for Issue 64."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

INSTALLED_SCRIPTS = Path(sys.argv[0]).resolve().parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCENARIO = os.environ["A_SHARE_ISSUE64_SCENARIO"]

sys.path.insert(0, str(INSTALLED_SCRIPTS))

import a_share_research.cli as cli  # noqa: E402
from a_share_research.capital_flow_sources import (  # noqa: E402
    EastmoneyNorthboundFlowOperation,
)
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_limit_sources import (  # noqa: E402
    EastmoneyLimitStateOperation,
    ThsLimitReasonOperation,
)
from a_share_research.market_monitoring_sources import (  # noqa: E402
    EastmoneyFocusMonitoringOperation,
    EastmoneySevereAbnormalMovementOperation,
)
from a_share_research.market_theme_sources import (  # noqa: E402
    EastmoneyIndustryRotationOperation,
    EastmoneySecurityBoardMembershipOperation,
    ThsMarketHeatOperation,
    ThsStrongStockThemeOperation,
)
from a_share_research.research_runtime import research as real_research  # noqa: E402
from a_share_research.source_throttle import SerialRequestGate  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)
NORTHBOUND_RETRIEVED_AT = datetime(2024, 8, 16, 19, 40, tzinfo=CHINA_STANDARD_TIME)
NO_WAIT_GATE = SerialRequestGate(
    minimum_interval_seconds=0,
    jitter_bounds=(0, 0),
    rate_limit_backoffs=(),
)


def _response(
    payload: object, *, retrieved_at: datetime = RETRIEVED_AT
) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=retrieved_at,
    )


def _fixture_response(path: Path) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=path.read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        del headers
        host = urlsplit(url).netloc
        if SCENARIO == "board_membership":
            if host == "query.sse.com.cn":
                return _fixture_response(
                    FIXTURES / "identity_sources" / "sse_empty.json"
                )
            if host == "www.szse.cn":
                return _fixture_response(
                    FIXTURES / "identity_sources" / "szse_300058.json"
                )
            if host == "www.cninfo.com.cn":
                return _fixture_response(
                    FIXTURES / "identity_sources" / "cninfo_current_orgs.json"
                )
            if host == "push2.eastmoney.com":
                return _response(
                    {
                        "data": {
                            "secid": "0.300058",
                            "total": 1,
                            "diff": [
                                {
                                    "f12": "BK0420",
                                    "f14": "文化传媒",
                                    "f3": "1.23",
                                    "f128": "读客文化",
                                }
                            ],
                        }
                    }
                )
        if SCENARIO == "industry_rotation" and host == "push2.eastmoney.com":
            observed_at = int(
                datetime(2026, 7, 31, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
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
                                "f124": observed_at,
                            },
                            {
                                "f12": "BK1002",
                                "f14": "计算机设备",
                                "f3": "2.75",
                                "f104": 9,
                                "f105": 4,
                                "f140": "次领涨股",
                                "f136": "4.20",
                                "f124": observed_at,
                            },
                        ],
                    }
                }
            )
        if (
            SCENARIO == "focus_monitoring"
            and host == "mobappconfig.securities.eastmoney.com"
        ):
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
        if SCENARIO == "severe_abnormal" and host == "dycalchis.eastmoney.com":
            return _response(
                {
                    "result": 0,
                    "msg": "",
                    "pages": 1,
                    "date": 20260731,
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
        if (
            SCENARIO in {"market_heat", "limit_reason_heat"}
            and host == "dq.10jqka.com.cn"
        ):
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
        if (
            SCENARIO in {"limit_reason", "limit_reason_heat"}
            and host == "data.10jqka.com.cn"
        ):
            observed_on = (
                "2026-08-02" if SCENARIO == "limit_reason_heat" else "2026-07-31"
            )
            timestamp = int(
                datetime.fromisoformat(f"{observed_on}T09:30:00+08:00").timestamp()
            )
            return _response(
                {
                    "status_code": 0,
                    "data": {
                        "total": 1,
                        "info": [
                            {
                                "code": "300058",
                                "name": "蓝色光标",
                                "latest": "12.34",
                                "change_rate": "10.01",
                                "reason_type": "AI营销+算力",
                                "limit_up_type": "换手板",
                                "limit_up_suc_rate": "0.8",
                                "open_num": 1,
                                "order_amount": "10000000",
                                "high_days": "3天2板",
                                "first_limit_up_time": timestamp,
                                "is_again_limit": 1,
                            }
                        ],
                    },
                }
            )
        if SCENARIO == "strong_theme" and urlsplit(url).netloc == "zx.10jqka.com.cn":
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
        if (
            SCENARIO == "northbound_historical"
            and urlsplit(url).netloc == "datacenter-web.eastmoney.com"
        ):
            return _response(
                {
                    "success": True,
                    "result": {
                        "pages": 1,
                        "count": 1,
                        "data": [
                            {
                                "TRADE_DATE": "2024-08-16 00:00:00",
                                "MUTUAL_TYPE": "001",
                                "NET_DEAL_AMT": "12.34",
                            }
                        ],
                    },
                },
                retrieved_at=NORTHBOUND_RETRIEVED_AT,
            )
        if (
            SCENARIO == "limit_empty"
            and urlsplit(url).netloc == "push2ex.eastmoney.com"
        ):
            return _response({"data": {"tc": 0, "pool": []}})
        if (
            SCENARIO == "limit_partial"
            and urlsplit(url).netloc == "push2ex.eastmoney.com"
        ):
            if urlsplit(url).path.endswith("getTopicZTPool"):
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
            if urlsplit(url).path.endswith("getTopicDTPool"):
                return _response({"data": None})
        raise AssertionError("unexpected Issue 64 source operation")


_transport = FixtureTransport()


def _fixture_research(request: dict[str, object], **kwargs: object) -> object:
    del kwargs
    if SCENARIO.startswith("northbound_"):
        operation = EastmoneyNorthboundFlowOperation(
            _transport,
            request_gate=NO_WAIT_GATE,
        )
        return real_research(
            request,
            identity_transport=_transport,
            capital_operations=(operation,),
            research_now=(
                NORTHBOUND_RETRIEVED_AT
                if SCENARIO == "northbound_historical"
                else RETRIEVED_AT
            ),
            available_optional_dependencies=set(),
        )
    if SCENARIO == "limit_reason_heat":
        operations = (
            ThsLimitReasonOperation(_transport, request_gate=NO_WAIT_GATE),
            ThsMarketHeatOperation(_transport, request_gate=NO_WAIT_GATE),
        )
        return real_research(
            request,
            identity_transport=_transport,
            market_signal_operations=operations,
            research_now=RETRIEVED_AT,
            available_optional_dependencies=set(),
        )
    if SCENARIO.startswith("limit_"):
        operation = (
            ThsLimitReasonOperation(_transport, request_gate=NO_WAIT_GATE)
            if SCENARIO == "limit_reason"
            else EastmoneyLimitStateOperation(
                _transport,
                request_gate=NO_WAIT_GATE,
            )
        )
        return real_research(
            request,
            identity_transport=_transport,
            market_signal_operations=(operation,),
            research_now=RETRIEVED_AT,
            available_optional_dependencies=set(),
        )
    market_operations = {
        "board_membership": EastmoneySecurityBoardMembershipOperation,
        "industry_rotation": EastmoneyIndustryRotationOperation,
        "focus_monitoring": EastmoneyFocusMonitoringOperation,
        "severe_abnormal": EastmoneySevereAbnormalMovementOperation,
        "market_heat": ThsMarketHeatOperation,
    }
    operation_type = market_operations.get(SCENARIO)
    if operation_type is not None:
        operation = operation_type(_transport, request_gate=NO_WAIT_GATE)
        return real_research(
            request,
            identity_transport=_transport,
            market_signal_operations=(operation,),
            research_now=RETRIEVED_AT,
            available_optional_dependencies=set(),
        )
    operation = ThsStrongStockThemeOperation(
        _transport,
        request_gate=NO_WAIT_GATE,
    )
    return real_research(
        request,
        identity_transport=_transport,
        market_signal_operations=(operation,),
        research_now=RETRIEVED_AT,
        available_optional_dependencies=set(),
    )


cli.research = _fixture_research
