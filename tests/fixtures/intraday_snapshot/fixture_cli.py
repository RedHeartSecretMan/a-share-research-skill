#!/usr/bin/env python3
"""Offline public-CLI harness for synthetic intraday source responses."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[2]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.intraday_contract import (  # noqa: E402
    IntradayObservation,
    IntradayQuery,
)
from a_share_research.intraday_sources import (  # noqa: E402
    TencentIntradayOperation,
    TongdaxinIntradayOperation,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 3, 10, 30, 5, tzinfo=CHINA_STANDARD_TIME)


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
                "vol_unit": "hands",
                "vol_scope": "trading_day",
                "amount_unit": "CNY",
                "amount_scope": "trading_day",
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
                "vol_unit": "hands",
                "vol_scope": "trading_day",
                "amount_unit": "CNY",
                "amount_scope": "trading_day",
            }
        )
        scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO", "success")
        if scenario == "tdx_malformed_price":
            values["price"] = "not-a-price"
        elif scenario == "tdx_float_noise":
            values["price"] = 1680.2500000001 if code == "600519" else 12.3400000001
        elif scenario == "tdx_negative_volume":
            values["vol"] = "-1"
        elif scenario == "tdx_zero_values":
            values["vol"] = "0"
            values["amount"] = "0"
        elif scenario == "tdx_fractional_volume":
            values["vol"] = "1.5"
        elif scenario == "tdx_unknown_volume_unit":
            values["vol_unit"] = "shares"
        elif scenario == "tdx_unknown_amount_unit":
            values["amount_unit"] = "dollars"
        elif scenario == "tdx_missing_units":
            values.pop("vol_unit")
            values.pop("vol_scope")
            values.pop("amount_unit")
            values.pop("amount_scope")
        elif scenario == "tdx_quote_date_mismatch":
            values["trading_date"] = "2026-08-02"
        return _Rows(
            [
                {
                    "code": code,
                    "market": 1 if code == "600519" else 0,
                    "servertime": "10:30:00",
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
        if scenario == "tdx_wrong_daily_security":
            return _Rows(
                [
                    {
                        "code": "000001",
                        "market": 0,
                        "year": 2026,
                        "month": 8,
                        "day": 3,
                    }
                ]
            )
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
        quote[30] = "20260803102958"
        if scenario == "tencent_float_noise":
            current[2] = "1680.2500000001" if code == "600519" else "12.3400000001"
            quote[3] = current[2]
        elif scenario == "tencent_malformed_json":
            return HttpResponse(
                status=200,
                content_type="text/html; charset=UTF-8",
                body=b"{not-json",
                retrieved_at=RETRIEVED_AT,
            )
        elif scenario == "tencent_empty_body":
            return HttpResponse(
                status=200,
                content_type="text/html; charset=UTF-8",
                body=b"",
                retrieved_at=RETRIEVED_AT,
            )
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
        if scenario == "tencent_wrong_security":
            body["data"] = {"sh000001": body["data"][query_security]}
        return HttpResponse(
            status=200,
            content_type="text/html; charset=UTF-8",
            body=json.dumps(body).encode("utf-8"),
            retrieved_at=RETRIEVED_AT,
        )


class FixtureTencentIntradayOperation(TencentIntradayOperation):
    def collect(self, query: IntradayQuery) -> IntradayObservation:
        observation = super().collect(query)
        if os.environ.get("A_SHARE_INTRADAY_SCENARIO") != "tencent_unknown_kind":
            return observation
        evidence = dict(observation.evidence[0])
        source_observation = dict(evidence["observation"])
        source_observation["kind"] = "unknown-provider-shape"
        evidence["observation"] = source_observation
        return replace(observation, evidence=(evidence,))


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
        FixtureTencentIntradayOperation(FixtureTransport()),
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
