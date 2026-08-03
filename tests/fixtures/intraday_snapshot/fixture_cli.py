#!/usr/bin/env python3
"""Offline public-CLI harness for synthetic intraday source responses."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[2]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.intraday_sources import (  # noqa: E402
    TencentIntradayOperation,
    TongdaxinIntradayOperation,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def _retrieved_at_for_scenario() -> datetime:
    scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO")
    times = {
        "pre_open": (8, 30, 5),
        "opening_auction": (9, 20, 5),
        "midday_break": (12, 0, 5),
        "closing_auction": (14, 58, 5),
        "post_close": (15, 30, 5),
    }
    hour, minute, second = times.get(scenario, (10, 30, 5))
    day = 8 if scenario == "non_trading" else 3
    return datetime(2026, 8, day, hour, minute, second, tzinfo=CHINA_STANDARD_TIME)


RETRIEVED_AT = _retrieved_at_for_scenario()


class _Row:
    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def to_dict(self) -> dict[str, object]:
        return self._value


class _Rows:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self._values = values
        self.iloc = self

    def __getitem__(self, index: int) -> _Row:
        return _Row(self._values[index])


class FixtureTongdaxinClient:
    def quotes(self, *, symbol: list[str]) -> _Rows:
        if symbol not in (["600519"], ["000001"]):
            raise AssertionError(f"unexpected TongdaXin symbol: {symbol}")
        code = symbol[0]
        values = (
            {
                "price": "1680.25",
                "open": "1675.00",
                "high": "1688.00",
                "low": "1670.50",
                "last_close": "1668.00",
                "vol": "12345",
                "amount": "2071234567.89",
            }
            if code == "600519"
            else {
                "price": "12.34",
                "open": "12.20",
                "high": "12.40",
                "low": "12.10",
                "last_close": "12.18",
                "vol": "9876",
                "amount": "12187654.32",
            }
        )
        scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO")
        if scenario in {"opening_auction", "closing_auction"}:
            values["price_type"] = "indicative_auction"
        if scenario == "unknown_cache":
            values["cache_state"] = "unknown"
        observed_times = {
            "opening_auction": "09:20:00",
            "midday_break": "11:29:50",
            "closing_auction": "14:58:00",
            "source_stale": "10:28:00",
            "session_mismatch": "10:30:00",
            "missing_time": None,
        }
        observed_time = observed_times.get(scenario, "10:30:00")
        return _Rows(
            [
                {
                    "code": code,
                    "market": 1 if code == "600519" else 0,
                    "servertime": observed_time,
                    **values,
                }
            ]
        )

    def bars(self, *, symbol: str, frequency: int, start: int, offset: int) -> _Rows:
        if symbol not in {"600519", "000001"} or (frequency, start, offset) != (
            9,
            0,
            1,
        ):
            raise AssertionError("unexpected TongdaXin latest-daily-bar request")
        scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO", "success")
        if scenario == "tdx_missing_daily":
            return _Rows([])
        return _Rows(
            [
                {
                    "year": 2026,
                    "month": 8,
                    "day": 2 if scenario == "tdx_old_daily" else 3,
                }
            ]
        )

    def close(self) -> None:
        return None


class FixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        if os.environ.get("A_SHARE_INTRADAY_FAIL_IF_COLLECTED") == "1":
            raise AssertionError("source collection must not occur")
        if "web.ifzq.gtimg.cn" not in url:
            raise AssertionError(f"unexpected Tencent URL: {url}")
        scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO", "success")
        if scenario == "tencent_failure":
            return HttpResponse(
                status=503,
                content_type="text/plain",
                body=b"synthetic unavailable",
                retrieved_at=RETRIEVED_AT,
            )
        if "sh600519" in url:
            query_security = "sh600519"
            code = "600519"
            current = [
                "2026-08-03",
                "1675.00",
                "1680.25",
                "1688.00",
                "1670.50",
                "12345",
            ]
            previous = [
                "2026-07-31",
                "1660.00",
                "1668.00",
                "1672.00",
                "1655.00",
                "10000",
            ]
        elif "sz000001" in url:
            query_security = "sz000001"
            code = "000001"
            current = ["2026-08-03", "12.20", "12.34", "12.40", "12.10", "9876"]
            previous = ["2026-07-31", "12.00", "12.18", "12.22", "11.95", "8000"]
        else:
            raise AssertionError(f"unexpected Tencent security URL: {url}")
        quote = [""] * 35
        quote[2] = code
        quote[3] = current[2]
        quote[4] = previous[2]
        quote[6] = current[5]
        quote_times = {
            "opening_auction": "20260803092002",
            "midday_break": "20260803112955",
            "closing_auction": "20260803145802",
            "source_stale": "20260803102800",
            "pair_gap": "20260803102800",
            "session_mismatch": "20260803092002",
        }
        quote[30] = quote_times.get(scenario, "20260803102958")
        if scenario in {"opening_auction", "closing_auction", "session_mismatch"}:
            quote[31] = "indicative_auction"
        if scenario == "core_price_mismatch":
            current[2] = "1680.26"
            quote[3] = "1680.26"
        body = {
            "code": 0,
            "data": {
                query_security: {
                    "day": [previous, current],
                    "qt": {query_security: quote},
                }
            },
        }
        return HttpResponse(
            status=200,
            content_type="text/html; charset=UTF-8",
            body=json.dumps(body).encode("utf-8"),
            retrieved_at=RETRIEVED_AT,
        )


def _client_factory(**kwargs: Any) -> FixtureTongdaxinClient:
    if os.environ.get("A_SHARE_INTRADAY_FAIL_IF_COLLECTED") == "1":
        raise AssertionError("source collection must not occur")
    if kwargs != {"market": "std"}:
        raise AssertionError(f"unexpected TongdaXin client args: {kwargs}")
    if os.environ.get("A_SHARE_INTRADAY_SCENARIO") == "tdx_factory_failure":
        raise OSError("synthetic client initialization failure")
    return FixtureTongdaxinClient()


if __name__ == "__main__":
    operations = (
        TongdaxinIntradayOperation(_client_factory),
        TencentIntradayOperation(FixtureTransport()),
    )
    configured_dependencies = os.environ.get(
        "A_SHARE_INTRADAY_DEPENDENCIES", "mootdx==0.11.7"
    )
    raise SystemExit(
        main(
            sys.argv[1:],
            research_now=RETRIEVED_AT,
            available_optional_dependencies={
                item for item in configured_dependencies.split(",") if item
            },
            intraday_operations=operations,
        )
    )
