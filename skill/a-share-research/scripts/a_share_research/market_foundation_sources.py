"""Bounded H/I/L/N/Q market report parsers with explicit source eligibility gaps."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Protocol, cast
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


def _source_error(owner: str, code: str) -> SourceOperationError:
    return SourceOperationError(owner, code, "The market source report is unqualified.")


def _json_response(
    response: HttpResponse, owner: str, *, callback: str | None
) -> object:
    if response.status != 200 or len(response.body) > 65_536:
        raise _source_error(owner, "source_response_unavailable")
    try:
        text = response.body.decode("utf-8").strip()
        if callback is not None:
            name = re.escape(callback) if callback else r"[A-Za-z_$][A-Za-z0-9_$]{0,63}"
            match = re.fullmatch(name + r"\s*\((.*)\)\s*;?", text, flags=re.DOTALL)
            if match is None:
                raise ValueError("unknown source wrapper")
            text = match[1]
        return json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise _source_error(owner, "source_schema_unproved") from None


def _retrieval(response: HttpResponse, owner: str) -> str:
    if (
        response.retrieved_at.tzinfo is None
        or response.retrieved_at.utcoffset() is None
    ):
        raise _source_error(owner, "source_schema_unproved")
    return response.retrieved_at.astimezone(CHINA_STANDARD_TIME).isoformat(
        timespec="seconds"
    )


def _numeric(value: object, owner: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or value < 0:
        raise _source_error(owner, "source_schema_unproved")
    text = str(value)
    if len(text) > 256:
        raise _source_error(owner, "source_schema_unproved")
    return text


class MarketFoundationPostTransport(Protocol):
    """One injected atomic POST leaf; retries must not be hidden."""

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse: ...


class BseMarketTurnoverReportTransport:
    """H-shaped exact-date daily report, class 2 only; raw CNY from #114."""

    def __init__(self, transport: MarketFoundationPostTransport | None = None) -> None:
        self._transport = transport

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]:
        owner = "bse_a_share_turnover@1"
        endpoint = str(selector["resource_url"])
        headers = {
            "Accept": "application/json,text/javascript",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.bse.cn/static/statisticdata.html",
            "User-Agent": "a-share-research-skill/1",
        }
        body = urlencode({"HQJSRQ": request.report_date.replace("-", "")}).encode(
            "ascii"
        )
        transport = self._transport
        if transport is None:
            response = UrlLibTransport(
                provider_invocation=invocation
            ).post_with_timeout(endpoint, headers, body, timeout)
        else:
            response = invocation.call(
                ProductionInvocationAdapter(
                    lambda: transport.post(endpoint, headers, body)
                )
            )
        rows = _json_response(response, owner, callback="")
        if not isinstance(rows, list) or len(rows) > 32:
            raise _source_error(owner, "source_schema_unproved")
        selected: list[Mapping[str, object]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("xxzrlx"), str):
                raise _source_error(owner, "source_schema_unproved")
            if row.get("rq") != request.report_date.replace("-", ""):
                raise _source_error(owner, "source_report_date_mismatch")
            if row["xxzrlx"] == "2":
                selected.append(row)
        if len(selected) != 1:
            raise _source_error(owner, "source_schema_unproved")
        return {
            "exchange": "BSE",
            "asset_scope": "exchange_daily_A_share_stock_turnover",
            "report_date": request.report_date,
            "published_at": None,
            "retrieved_at": _retrieval(response, owner),
            "source_fields": {
                "A_SHARE_TURNOVER_CNY": _numeric(selected[0].get("hqcjje"), owner)
            },
            "source_units": {"A_SHARE_TURNOVER_CNY": "CNY"},
            "query_complete": True,
            "row_count": 1,
        }


def _get_report(
    transport: HttpTransport | None,
    url: str,
    headers: dict[str, str],
    timeout: float,
    invocation: ProviderInvocation,
) -> HttpResponse:
    if transport is None:
        return UrlLibTransport(provider_invocation=invocation).get_with_timeout(
            url, headers, timeout
        )
    return invocation.call(
        ProductionInvocationAdapter(lambda: transport.get(url, headers))
    )


def _sse_response(response: HttpResponse, owner: str) -> Mapping[str, object]:
    payload = _json_response(response, owner, callback="jsonpCallback121")
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise _source_error(owner, "source_schema_unproved")
    return payload


class SseMarketMarginReportTransport:
    """L/N summary/date/paging; money is CNY, quantities stay unqualified."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]:
        owner = "sse_market_margin@1"
        day = request.report_date.replace("-", "")
        params: dict[str, object] = {
            "isPagination": "true",
            "pageHelp.pageSize": 25,
            "pageHelp.pageNo": 1,
            "pageHelp.beginPage": 1,
            "pageHelp.cacheSize": 1,
            "pageHelp.endPage": 1,
            "stockCode": "",
            "beginDate": day,
            "endDate": day,
            "sqlId": "RZRQ_HZ_INFO",
            "jsonCallBack": "jsonpCallback121",
        }
        headers = {
            "Accept": "application/json,text/javascript",
            "Referer": "https://www.sse.com.cn/",
            "User-Agent": "a-share-research-skill/1",
        }
        response = _get_report(
            self._transport,
            str(selector["resource_url"]) + "?" + urlencode(params),
            headers,
            timeout,
            invocation,
        )
        payload = _sse_response(response, owner)
        rows = cast(list[object], payload["result"])
        page = payload.get("pageHelp")
        if not isinstance(page, dict) or len(rows) > 1:
            raise _source_error(owner, "source_schema_unproved")
        if (
            any(
                type(page.get(key)) is not int
                for key in ("pageCount", "total", "pageNo", "pageSize")
            )
            or page["pageNo"] != 1
            or page["pageSize"] != 25
            or page["pageCount"] not in (0, 1)
            or page["total"] != len(rows)
        ):
            raise _source_error(owner, "source_schema_unproved")
        fields: dict[str, str] = {}
        if rows:
            row = rows[0]
            if not isinstance(row, dict):
                raise _source_error(owner, "source_schema_unproved")
            if row.get("opDate") != day:
                raise _source_error(owner, "source_report_date_mismatch")
            mapping = {
                "rzye": "FINANCING_BALANCE",
                "rzmre": "FINANCING_BUY_AMOUNT",
                "rqylje": "SECURITIES_LENDING_BALANCE",
                "rzrqjyzl": "MARGIN_TOTAL_BALANCE",
            }
            fields = {
                logical: _numeric(row.get(raw), owner)
                for raw, logical in mapping.items()
            }
            for raw in ("rqyl", "rqmcl"):
                _numeric(
                    row.get(raw), owner
                )  # Validate shape; no stock/fund/CDR/lot coercion.
        return {
            "exchange": "SSE",
            "asset_scope": "exchange_wide_official_margin_trading_summary",
            "report_date": request.report_date,
            "published_at": None,
            "retrieved_at": _retrieval(response, owner),
            "source_fields": fields,
            "source_units": {field: "CNY" for field in fields},
            "query_complete": False,
            "row_count": len(rows),
            "complete_fields": list(fields),
            "unproved_fields": {
                "SECURITIES_LENDING_QUANTITY": "mixed_quantity_unit",
                "SECURITIES_LENDING_SELL_QUANTITY": "mixed_quantity_unit",
            },
            "source_date_qualification": "response_date_matches_request"
            if rows
            else "date_control_only",
        }


def _display_numeric(value: object, owner: str, *, commas: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise _source_error(owner, "source_schema_unproved")
    pattern = r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
    if commas and "," in value:
        pattern = r"[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]+)?"
    if re.fullmatch(pattern, value) is None:
        raise _source_error(owner, "source_schema_unproved")
    return value.replace(",", "")


class SseMarketTurnoverReportTransport:
    """H/N daily product rows; preserve candidates until pure-A scope proves."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]:
        owner = "sse_a_share_turnover@1"
        params = {
            "sqlId": "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C",
            "PRODUCT_CODE": "01,02,03,11,17",
            "type": "inParams",
            "SEARCH_DATE": request.report_date,
            "jsonCallBack": "jsonpCallback121",
        }
        headers = {
            "Accept": "application/json,text/javascript",
            "Referer": "https://www.sse.com.cn/",
            "User-Agent": "a-share-research-skill/1",
        }
        response = _get_report(
            self._transport,
            str(selector["resource_url"]) + "?" + urlencode(params),
            headers,
            timeout,
            invocation,
        )
        payload = _sse_response(response, owner)
        rows = cast(list[object], payload["result"])
        if len(rows) > 5:
            raise _source_error(owner, "source_schema_unproved")
        seen: set[str] = set()
        fields: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("PRODUCT_CODE") not in {
                "01",
                "02",
                "03",
                "11",
                "17",
            }:
                raise _source_error(owner, "source_schema_unproved")
            code = str(row["PRODUCT_CODE"])
            if code in seen:
                raise _source_error(owner, "source_schema_unproved")
            seen.add(code)
            if row.get("TRADE_DATE") != request.report_date.replace("-", ""):
                raise _source_error(owner, "source_report_date_mismatch")
            if code in {"01", "03"}:
                fields["TRADE_AMT_" + code] = _display_numeric(
                    row.get("TRADE_AMT"), owner
                )
        return {
            "exchange": "SSE",
            "asset_scope": "exchange_daily_A_share_stock_turnover",
            "report_date": request.report_date,
            "published_at": None,
            "retrieved_at": _retrieval(response, owner),
            "source_fields": fields,
            "source_units": {field: "hundred_million_CNY" for field in fields},
            "query_complete": len(fields) == 2,
            "row_count": len(fields),
            "source_scope_qualification": "pure_A_CDR_exclusion_unproved",
        }


def _szse_summary(
    response: HttpResponse,
    owner: str,
    catalog: str,
    name: str,
    control: str,
    request: OperationRequest,
) -> tuple[Mapping[str, object], list[object]]:
    payload = _json_response(response, owner, callback=None)
    if not isinstance(payload, list) or len(payload) > 8:
        raise _source_error(owner, "source_schema_unproved")
    selected: list[Mapping[str, object]] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
            raise _source_error(owner, "source_schema_unproved")
        metadata = item["metadata"]
        if (
            metadata.get("catalogid") == catalog
            and metadata.get("tabkey") == "tab1"
            and metadata.get("name") == name
        ):
            selected.append(item)
    if len(selected) != 1:
        raise _source_error(owner, "source_schema_unproved")
    report = selected[0]
    metadata = cast(Mapping[str, object], report["metadata"])
    rows = report.get("data")
    if (
        report.get("error") is not None
        or not isinstance(rows, list)
        or len(rows) > 8
        or any(
            type(metadata.get(key)) is not int
            for key in ("pagecount", "pageno", "pagesize", "recordcount")
        )
        or (metadata["pagecount"], metadata["pageno"], metadata["pagesize"])
        != (0, 0, -1)
    ):
        raise _source_error(owner, "source_schema_unproved")
    conditions = metadata.get("conditions")
    if not isinstance(conditions, list) or len(conditions) > 16:
        raise _source_error(owner, "source_schema_unproved")
    dates = [
        item.get("defaultValue")
        for item in conditions
        if isinstance(item, dict) and item.get("name") == control
    ]
    if len(dates) != 1 or dates[0] != request.report_date:
        raise _source_error(owner, "source_report_date_mismatch")
    return metadata, rows


class SzseMarketMarginReportTransport:
    """H/I/Q summary; dated subname is report date, never publication."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]:
        owner = "szse_market_margin@1"
        params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": "1837_xxpl",
            "txtDate": request.report_date,
        }
        headers = {
            "Accept": "application/json",
            "Referer": "https://www.szse.cn/disclosure/margin/margin/index.html",
            "User-Agent": "a-share-research-skill/1",
        }
        response = _get_report(
            self._transport,
            str(selector["resource_url"]) + "?" + urlencode(params),
            headers,
            timeout,
            invocation,
        )
        metadata, rows = _szse_summary(
            response, owner, "1837_xxpl", "融资融券交易总量", "txtDate", request
        )
        expected_cols = {
            "jrrjmc": "融券卖出量<br>(亿股/亿份)",
            "jrrjye": "融券余额<br>(亿元)",
            "jrrjyl": "融券余量<br>(亿股/亿份)",
            "jrrzmr": "融资买入额<br>(亿元)",
            "jrrzrjye": "融资融券余额<br>(亿元)",
            "jrrzye": "融资余额<br>(亿元)",
        }
        if metadata.get("cols") != expected_cols:
            raise _source_error(owner, "source_schema_unproved")
        if len(rows) > 1:
            raise _source_error(owner, "source_schema_unproved")
        subname = metadata.get("subname")
        if not isinstance(subname, str):
            raise _source_error(owner, "source_schema_unproved")
        if subname and subname != request.report_date:
            raise _source_error(owner, "source_report_date_mismatch")
        fields: dict[str, str] = {}
        if rows:
            row = rows[0]
            if not isinstance(row, dict):
                raise _source_error(owner, "source_schema_unproved")
            mapping = {
                "jrrzye": "FINANCING_BALANCE",
                "jrrzmr": "FINANCING_BUY_AMOUNT",
                "jrrjye": "SECURITIES_LENDING_BALANCE",
                "jrrzrjye": "MARGIN_TOTAL_BALANCE",
            }
            fields = {
                logical: _display_numeric(row.get(raw), owner, commas=True)
                for raw, logical in mapping.items()
            }
            for raw in ("jrrjmc", "jrrjyl"):
                _display_numeric(row.get(raw), owner, commas=True)
        return {
            "exchange": "SZSE",
            "asset_scope": "exchange_wide_official_margin_trading_total",
            "report_date": request.report_date,
            "published_at": None,
            "retrieved_at": _retrieval(response, owner),
            "source_fields": fields,
            "source_units": {field: "hundred_million_CNY" for field in fields},
            "query_complete": False,
            "row_count": len(rows),
            "complete_fields": list(fields),
            "unproved_fields": {
                "SECURITIES_LENDING_QUANTITY": "mixed_quantity_unit",
                "SECURITIES_LENDING_SELL_QUANTITY": "mixed_quantity_unit",
            },
            "source_date_qualification": "response_date_matches_request"
            if subname and rows
            else "date_control_only",
        }


class SzseMarketTurnoverReportTransport:
    """H/I dated controls and CDR-inclusive gp/xz/cy; no pure-A alias."""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]:
        owner = "szse_a_share_turnover@1"
        params: dict[str, object] = {
            "SHOWTYPE": "JSON",
            "TABKEY": "tab1",
            "PAGENO": 1,
            "tab1PAGESIZE": 20,
            "CATALOGID": "scsj_gprdgk_after",
            "txtQueryDate": request.report_date,
            "tjzqlb": "D",
            "bkbz": "GP|XZ|CY",
            "cjbz": "cjl|cjje",
            "cxzs": "成交量（亿）|成交金额（亿元）",
        }
        headers = {
            "Accept": "application/json",
            "Referer": "https://www.szse.cn/market/stock/situation/daily/index.html",
            "User-Agent": "a-share-research-skill/1",
        }
        response = _get_report(
            self._transport,
            str(selector["resource_url"]) + "?" + urlencode(params),
            headers,
            timeout,
            invocation,
        )
        metadata, rows = _szse_summary(
            response, owner, "scsj_gprdgk_after", "日度概况", "txtQueryDate", request
        )
        if metadata.get("cols") != {
            "zbmc": "指标名称",
            "gp": "深市合计",
            "xz": "主板",
            "cy": "创业板",
        }:
            raise _source_error(owner, "source_schema_unproved")
        selected = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("zbmc") == "成交金额（亿元）"
        ]
        if len(selected) != 1:
            raise _source_error(owner, "source_schema_unproved")
        for field in ("gp", "xz", "cy"):
            _display_numeric(selected[0].get(field), owner, commas=True)
        footer = metadata.get("footer")
        if not isinstance(footer, str) or "数据统计范围包含存托凭证" not in footer:
            raise _source_error(owner, "source_scope_unproved")
        # The observed gp/xz/cy amounts include CDR, and the date is only a
        # control echo. None is assigned to the qualified pure-A logical field.
        return {
            "exchange": "SZSE",
            "asset_scope": "exchange_daily_A_share_stock_turnover",
            "report_date": request.report_date,
            "published_at": None,
            "retrieved_at": _retrieval(response, owner),
            "source_fields": {},
            "source_units": {},
            "query_complete": False,
            "row_count": 1,
            "source_scope_qualification": "CDR_inclusive",
            "source_date_qualification": "date_control_only",
        }
