"""The H/I-proved BSE margin HTTP/frame/display mapping; no pubtime guess."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, cast
from urllib.parse import urlencode

from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    UrlLibTransport,
)
from .market_foundation import OperationRequest
from .provider_lane import ProductionInvocationAdapter, ProviderInvocation

_OWNER = "bse_market_margin@1"
_ENDPOINT = "https://www.bse.cn/rzrqjyyexxController/summaryInfoResult.do"
_ROUND_FIELDS = {
    "rzmre": "FINANCING_BUY_AMOUNT",
    "rzye": "FINANCING_BALANCE",
    "rqmcl": "SECURITIES_LENDING_SELL_QUANTITY",
    "rqyl": "SECURITIES_LENDING_QUANTITY",
    "rqye": "SECURITIES_LENDING_BALANCE",
    "rzrqye": "MARGIN_TOTAL_BALANCE",
}
_QUANTITIES = {"rqmcl", "rqyl"}
_DISPLAY_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,32})?$")


def _error(code: str) -> SourceOperationError:
    return SourceOperationError(
        _OWNER, code, "The BSE margin source report is unqualified."
    )


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("ambiguous source field")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("nonfinite source number")


def _frame(response: HttpResponse) -> tuple[list[object], str]:
    if response.status != 200 or len(response.body) > 65_536:
        raise _error("source_response_unavailable")
    try:
        text = response.body.decode("utf-8")
        match = re.fullmatch(r"\s*null\s*\((.*)\)\s*;?\s*", text, flags=re.DOTALL)
        if match is None:
            raise ValueError("unknown source wrapper")
        payload = match[1].strip()
        if not payload.startswith("["):
            raise ValueError("unknown source frame")
        decoder = json.JSONDecoder(
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs,
        )
        rest = payload[1:].lstrip()
        rows, end = decoder.raw_decode(rest)
        rest = rest[end:].lstrip()
        if not rest.startswith(",") or not isinstance(rows, list) or len(rows) > 1:
            raise ValueError("unknown source frame")
        date_match = re.fullmatch(
            r"\s*(['\"])([0-9]{4}-[0-9]{2}-[0-9]{2})\1\s*\]\s*", rest[1:]
        )
        if date_match is None:
            raise ValueError("unknown source date frame")
        report_date = date_match[2]
        date.fromisoformat(report_date)
        return rows, report_date
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise _error("source_schema_unproved") from None


def _candidate_times(row: Mapping[str, object]) -> dict[str, str]:
    candidates = {}
    for name in ("createTime", "lastUpdateTime", "sxrq"):
        value = row.get(name)
        if not isinstance(value, dict) or type(value.get("time")) is not int:
            continue
        try:
            millis = cast(int, value["time"])
            instant = (
                datetime(1970, 1, 1, tzinfo=timezone.utc)
                + timedelta(milliseconds=millis)
            ).astimezone(CHINA_STANDARD_TIME)
            candidates[name] = instant.isoformat(
                timespec="milliseconds" if millis % 1000 else "seconds"
            )
        except (ValueError, OverflowError):
            continue
    return candidates


class BseMarketMarginReportTransport:
    """Acquire one exact-date summary and parse the published Round values.

    An injected HttpTransport must be a single atomic fixture/request with no
    hidden retry. The default UrlLibTransport counts every leaf/redirect using
    the existing execution-owned invoker and uses the remaining owner timeout.
    """

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]:
        url = _ENDPOINT + "?" + urlencode({"transDate": request.report_date, "page": 0})
        headers = {
            "Accept": "application/json,text/javascript",
            "Referer": "https://www.bse.cn/disclosure/rzrq_trans_list.html",
            "User-Agent": "a-share-research-skill/1",
        }
        transport = self._transport
        if transport is None:
            response = UrlLibTransport(provider_invocation=invocation).get_with_timeout(
                url, headers, timeout
            )
        else:
            response = invocation.call(
                ProductionInvocationAdapter(lambda: transport.get(url, headers))
            )
        rows, report_date = _frame(response)
        if report_date != request.report_date:
            raise _error("source_report_date_mismatch")
        fields: dict[str, str] = {}
        units: dict[str, str] = {}
        precision: dict[str, int] = {}
        candidates: dict[str, str] = {}
        if rows:
            row = rows[0]
            if not isinstance(row, dict) or row.get("zqdm") != "999999":
                raise _error("source_schema_unproved")
            for raw, logical in _ROUND_FIELDS.items():
                raw_value, display = row.get(raw), row.get(raw + "Round")
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, Decimal))
                    or raw_value < 0
                ):
                    raise _error("source_schema_unproved")
                if (
                    not isinstance(display, str)
                    or len(display) > 256
                    or _DISPLAY_DECIMAL.fullmatch(display) is None
                ):
                    raise _error("source_schema_unproved")
                fields[logical] = display
                units[logical] = (
                    "ten_thousand_shares" if raw in _QUANTITIES else "ten_thousand_CNY"
                )
                precision[logical] = len(display.partition(".")[2])
            candidates = _candidate_times(row)
        if (
            response.retrieved_at.tzinfo is None
            or response.retrieved_at.utcoffset() is None
        ):
            raise _error("source_schema_unproved")
        return {
            "exchange": "BSE",
            "asset_scope": "exchange_wide_official_margin_trading_aggregate",
            "report_date": report_date,
            "published_at": None,
            "retrieved_at": response.retrieved_at.astimezone(
                CHINA_STANDARD_TIME
            ).isoformat(timespec="seconds"),
            "source_fields": fields,
            "source_units": units,
            "query_complete": bool(rows),
            "row_count": len(rows),
            "display_precision": precision,
            "source_time_candidates": candidates,
        }
