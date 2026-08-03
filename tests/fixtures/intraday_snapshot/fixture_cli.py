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


def _retrieved_at_for_scenario() -> datetime:
    scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO")
    times = {
        "pre_open": (8, 30, 5),
        "opening_auction": (9, 20, 5),
        "midday_break": (12, 0, 5),
        "midday_pair_gap": (12, 0, 5),
        "midday_not_last": (12, 0, 5),
        "closing_auction": (14, 58, 5),
        "post_close": (15, 30, 5),
    }
    hour, minute, second = times.get(scenario, (10, 30, 5))
    day = 8 if scenario == "non_trading" else 4 if scenario == "weekday_holiday" else 3
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
                "vol_unit": "hands",
                "vol_scope": "trading_day",
                "amount_unit": "CNY",
                "amount_scope": "trading_day",
                "cache_state": "source_timestamp",
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
                "cache_state": "source_timestamp",
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
        elif scenario in {
            "suspension_confirmed",
            "suspension_one_source",
            "suspension_core_conflict",
            "suspension_status_ambiguous",
            "suspension_alias",
        }:
            values.update(
                {
                    "price": "1668.00",
                    "open": "1668.00",
                    "high": "1668.00",
                    "low": "1668.00",
                    "last_close": "1668.00",
                    "vol": "0",
                    "amount": "0",
                    "trading_status": (
                        "not_traded" if scenario == "suspension_alias" else "suspended"
                    ),
                }
            )
            if scenario == "suspension_status_ambiguous":
                values["amount"] = "1.00"
        elif scenario in {"corporate_action_unavailable", "comparable_prev_close"}:
            values["previous_close_basis"] = "actual_close"
            if scenario == "corporate_action_unavailable":
                values["corporate_action"] = {"type": "cash_dividend"}
        elif scenario == "metadata_secret":
            values["previous_close_basis"] = "Bearer provider-secret-token"
            values["corporate_action"] = {"details": "Bearer provider-secret-token"}
        elif scenario == "price_equal_previous_close":
            values.update(
                {
                    "price": "1668.00",
                    "open": "1668.00",
                    "high": "1668.00",
                    "low": "1668.00",
                    "last_close": "1668.00",
                }
            )
        if scenario in {"opening_auction", "closing_auction"}:
            values["price_type"] = "indicative_auction"
        if scenario == "unknown_cache":
            values["cache_state"] = "unknown"
        if scenario == "cache_state_secret":
            values["cache_state"] = "Bearer provider-secret-token"
        if scenario == "missing_cache":
            values.pop("cache_state")
        if scenario in {"midday_break", "midday_pair_gap"}:
            values["observation_boundary"] = "morning_last_compatible"
        if scenario == "midday_not_last":
            values["observation_boundary"] = "morning_observation"
        observed_times = {
            "opening_auction": "09:20:00",
            "midday_break": "11:29:50",
            "midday_pair_gap": "11:28:00",
            "midday_not_last": "10:00:00",
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
        quote_times = {
            "opening_auction": "20260803092002",
            "midday_break": "20260803112955",
            "midday_pair_gap": "20260803112600",
            "midday_not_last": "20260803100000",
            "closing_auction": "20260803145802",
            "source_stale": "20260803102800",
            "pair_gap": "20260803102800",
            "session_mismatch": "20260803092002",
        }
        quote[30] = quote_times.get(scenario, "20260803102958")
        if scenario in {
            "suspension_confirmed",
            "suspension_one_source",
            "suspension_status_ambiguous",
            "suspension_alias",
        }:
            current[1] = current[2] = current[3] = current[4] = "1668.00"
            current[5] = "0"
            previous[2] = "1668.00"
            quote[3] = quote[4] = "1668.00"
            quote[6] = "0"
            if scenario in {
                "suspension_confirmed",
                "suspension_status_ambiguous",
                "suspension_alias",
            }:
                quote[33] = (
                    "not_traded" if scenario == "suspension_alias" else "suspended"
                )
        elif scenario in {"corporate_action_unavailable", "comparable_prev_close"}:
            quote[34] = (
                "ex_right_reference"
                if scenario == "corporate_action_unavailable"
                else "actual_close"
            )
        if scenario in {"opening_auction", "closing_auction", "session_mismatch"}:
            quote[31] = "indicative_auction"
        elif scenario == "unknown_price_type":
            quote[31] = "unknown_price_type"
        if scenario in {"midday_break", "midday_pair_gap"}:
            quote[32] = "morning_last_compatible"
        elif scenario == "midday_not_last":
            quote[32] = "morning_observation"
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
        if scenario == "price_equal_previous_close":
            current[1] = current[2] = current[3] = current[4] = "1668.00"
            previous[2] = "1668.00"
            quote[3] = quote[4] = "1668.00"
        body = {
            "code": 0,
            "data": {
                query_security: {
                    "day": [previous, current],
                    "qt": {query_security: quote},
                }
            },
        }
        if scenario == "corporate_action_unavailable":
            body["data"][query_security]["day"][-1].append(
                {
                    "nd": "2026",
                    "fh_sh": "1.00",
                    "djr": "2026-08-03",
                    "cqr": "2026-08-04",
                    "FHcontent": "10派1.00元",
                }
            )
        if scenario == "suspension_core_conflict":
            current[1] = current[2] = current[3] = current[4] = "1668.00"
            current[5] = "0"
            previous[2] = "1668.00"
            quote[3] = quote[4] = "1668.00"
            quote[6] = "0"
            quote[33] = "suspended"
            current[2] = "1668.01"
            current[3] = "1668.02"
            current[4] = "1667.99"
            quote[3] = "1668.01"
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
        scenario = os.environ.get("A_SHARE_INTRADAY_SCENARIO")
        if scenario == "unknown_prev_close_comparability":
            return replace(observation, previous_close_comparability="unknown")
        if scenario != "tencent_unknown_kind":
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
