"""SSE ETF identity and cross-checked market quote research."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from .close_sources import DailyBarObservation, TencentDailyLineOperation
from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    TransportError,
)


@dataclass(frozen=True)
class EtfIdentityObservation:
    code: str
    name: str
    fund_manager: str
    listing_date: date
    source_uri: str
    retrieved_at: datetime

    @property
    def security(self) -> str:
        return f"SSE:{self.code}"

    @property
    def evidence_id(self) -> str:
        return f"etf-identity-sse_etf_list@1-{self.security}"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "authoritative_disclosure",
            "source_operation": "sse_etf_list@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": {
                    "name": self.name,
                    "fund_manager": self.fund_manager,
                    "listing_date": self.listing_date.isoformat(),
                },
                "unit": "ETF identity",
            },
            "basis": "current_sse_etf_list_membership",
            "evidence_time": self.listing_date.isoformat(),
            "available_at": None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"SSE ETF identity for {self.code}",
            },
            "limitations": [
                "experimental_source_operation",
                "availability_time_unknown",
            ],
        }


@dataclass(frozen=True)
class EtfSnapshotObservation:
    code: str
    name: str
    open_value: str
    high_value: str
    low_value: str
    last_value: str
    previous_close: str
    change_rate: str
    volume_shares: str
    amount_cny: str
    trade_phase: str
    observed_at: datetime
    source_uri: str
    retrieved_at: datetime

    @property
    def security(self) -> str:
        return f"SSE:{self.code}"

    @property
    def evidence_id(self) -> str:
        return (
            f"etf-sse_etf_snapshot@1-{self.security}-"
            f"{self.observed_at.date().isoformat()}"
        )

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "market_observation",
            "source_operation": "sse_etf_snapshot@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": {
                    "open": self.open_value,
                    "high": self.high_value,
                    "low": self.low_value,
                    "last": self.last_value,
                    "previous_close": self.previous_close,
                    "change_rate": self.change_rate,
                    "volume": self.volume_shares,
                    "amount": self.amount_cny,
                },
                "unit": {
                    "price": "CNY/share",
                    "change_rate": "percent",
                    "volume": "shares",
                    "amount": "CNY",
                },
            },
            "basis": "sse_etf_market_snapshot",
            "observation": {
                "kind": "ETF market quote",
                "trading_date": self.observed_at.date().isoformat(),
                "trade_phase": self.trade_phase,
            },
            "evidence_time": self.observed_at.isoformat(),
            "available_at": self.observed_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"SSE ETF snapshot for {self.code}",
            },
            "limitations": ["experimental_source_operation"],
        }


class SseEtfListOperation:
    operation_id = "sse_etf_list@1"
    endpoint = "https://query.sse.com.cn/commonSoaQuery.do"
    subclasses = "01,02,03,04,06,08,09,31,32,33,34,35,36,37,38"

    def observe(self, code: str, transport: HttpTransport) -> EtfIdentityObservation:
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'isPagination': 'true',
                    'pageHelp.pageSize': '25',
                    'pageHelp.pageNo': '1',
                    'pageHelp.beginPage': '1',
                    'pageHelp.cacheSize': '1',
                    'pageHelp.endPage': '1',
                    'pagecache': 'false',
                    'sqlId': 'FUND_LIST',
                    'fundType': '00',
                    'subClass': self.subclasses,
                    'fundCode': code,
                }
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://www.sse.com.cn/assortment/fund/etf/list/",
        )
        payload = _json_payload(self.operation_id, response)
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, list):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF list response does not match the expected schema.",
            )
        matches = [
            item
            for item in result
            if isinstance(item, dict) and item.get("fundCode") == code
        ]
        if len(matches) != 1:
            raise _error(
                self.operation_id,
                "etf_identity_not_resolved",
                "The SSE ETF list did not establish exactly one matching ETF.",
            )
        item = matches[0]
        if not all(
            isinstance(item.get(field), str) and item[field]
            for field in (
                "secNameFull",
                "companyName",
                "listingDate",
                "fundType",
                "subClass",
            )
        ):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF identity fields are incomplete.",
            )
        if item["fundType"] != "00" or item["subClass"] not in self.subclasses.split(
            ","
        ):
            raise _error(
                self.operation_id,
                "security_type_mismatch",
                "The matching SSE fund is not an ETF.",
            )
        try:
            listing_date = datetime.strptime(item["listingDate"], "%Y%m%d").date()
        except ValueError as error:
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF listing date is invalid.",
            ) from error
        return EtfIdentityObservation(
            code=code,
            name=item["secNameFull"],
            fund_manager=item["companyName"],
            listing_date=listing_date,
            source_uri=url,
            retrieved_at=response.retrieved_at,
        )


class SseEtfSnapshotOperation:
    operation_id = "sse_etf_snapshot@1"
    endpoint = "https://yunhq.sse.com.cn:32042/v1/sh1/snap"
    select = (
        "name,cpxxextendname,open,high,low,last,prev_close,chg_rate,"
        "volume,amount,tradephase"
    )

    def observe(self, code: str, transport: HttpTransport) -> EtfSnapshotObservation:
        url = f"{self.endpoint}/{code}?{urlencode({'select': self.select})}"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://www.sse.com.cn/assortment/fund/etf/price/",
        )
        payload = _json_payload(self.operation_id, response)
        if (
            not isinstance(payload, dict)
            or payload.get("code") != code
            or not isinstance(payload.get("snap"), list)
            or len(payload["snap"]) != 11
        ):
            error_code = (
                "wrong_security_payload"
                if isinstance(payload, dict)
                and isinstance(payload.get("code"), str)
                and payload.get("code") != code
                else "unknown_schema"
            )
            raise _error(
                self.operation_id,
                error_code,
                "The SSE ETF snapshot has an invalid security or schema.",
            )
        values = payload["snap"]
        if not isinstance(values[0], str) or not isinstance(values[1], str):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF snapshot name fields are invalid.",
            )
        open_value, high_value, low_value, last_value, previous_close = (
            _positive_decimal(values[index], self.operation_id) for index in range(2, 7)
        )
        change_rate = _finite_decimal(values[7], self.operation_id)
        volume_shares = _nonnegative_integer(values[8], self.operation_id)
        amount_cny = _nonnegative_integer(values[9], self.operation_id)
        if not isinstance(values[10], str):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF trade phase is invalid.",
            )
        prices = map(Decimal, (open_value, high_value, low_value, last_value))
        open_price, high_price, low_price, last_price = prices
        if (
            low_price > high_price
            or not low_price <= open_price <= high_price
            or not low_price <= last_price <= high_price
        ):
            raise _error(
                self.operation_id,
                "inconsistent_price_bar",
                "The SSE ETF snapshot OHLC values are inconsistent.",
            )
        observed_at = _snapshot_time(payload, self.operation_id)
        return EtfSnapshotObservation(
            code=code,
            name=values[1],
            open_value=open_value,
            high_value=high_value,
            low_value=low_value,
            last_value=last_value,
            previous_close=previous_close,
            change_rate=change_rate,
            volume_shares=volume_shares,
            amount_cny=amount_cny,
            trade_phase=values[10].strip(),
            observed_at=observed_at,
            source_uri=url,
            retrieved_at=response.retrieved_at,
        )


def build_etf_market_result(
    request: dict[str, Any], transport: HttpTransport, now: datetime | None = None
) -> dict[str, Any]:
    research_now = now or datetime.now(CHINA_STANDARD_TIME)
    research_date = date.fromisoformat(request["as_of"])
    subjects = request["subjects"]
    if len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ValueError("etf_market requires exactly one subject object")
    clue = subjects[0].get("clue")
    if (
        not isinstance(clue, str)
        or len(clue) != 6
        or not clue.isascii()
        or not clue.isdigit()
    ):
        raise ValueError("etf_market subject requires one six-digit ETF code clue")
    if research_date > research_now.date():
        return _blocked(
            request, "future_research_date", "The research date is in the future."
        )
    source_errors: list[dict[str, str]] = []
    try:
        identity = SseEtfListOperation().observe(clue, transport)
    except SourceOperationError as error:
        return _blocked(
            request,
            "etf_identity_not_resolved",
            "The ETF identity could not be established.",
            source_errors=[_source_error(error)],
        )
    subject = {
        "security": {"exchange": "SSE", "code": clue, "type": "ETF"},
        "name": identity.name,
        "fund_manager": identity.fund_manager,
        "listing_date": identity.listing_date.isoformat(),
    }
    try:
        snapshot = SseEtfSnapshotOperation().observe(clue, transport)
    except SourceOperationError as error:
        snapshot = None
        source_errors.append(_source_error(error))
    try:
        fallback_observations = TencentDailyLineOperation().observe(
            f"SSE:{clue}", research_date, transport
        )
    except SourceOperationError as error:
        fallback_observations = []
        source_errors.append(_source_error(error))
    if snapshot is None or snapshot.observed_at.date() > research_date:
        return _blocked(
            request,
            "etf_quote_not_cross_checked",
            "The ETF quote is unavailable or outside the research boundary.",
            subject=subject,
            evidence=[identity.to_evidence()],
            source_errors=source_errors,
        )
    fallback = max(
        (
            item
            for item in fallback_observations
            if item.trading_date == snapshot.observed_at.date()
        ),
        key=lambda item: item.evidence_time,
        default=None,
    )
    evidence = [identity.to_evidence(), snapshot.to_evidence()]
    if fallback is not None:
        evidence.append(fallback.to_bar_evidence())
    conflicts = _quote_conflicts(snapshot, fallback)
    if source_errors or fallback is None or conflicts:
        result = _blocked(
            request,
            "etf_quote_not_cross_checked",
            "The ETF quote does not agree across both market sources.",
            subject=subject,
            evidence=evidence,
            source_errors=source_errors,
        )
        result["conflicts"] = conflicts
        return result
    assert fallback is not None
    volume_difference = abs(
        Decimal(snapshot.volume_shares) - Decimal(fallback.volume_shares)
    )
    market_state = (
        "completed"
        if snapshot.observed_at.date() < research_now.date()
        or snapshot.observed_at.time() >= time(15, 0)
        else "intraday"
    )
    return {
        "schema_version": request["schema_version"],
        "status": "limited",
        "subjects": [subject],
        "research": {
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
            "retrieved_at": research_now.isoformat(),
        },
        "quote": {
            "trading_date": snapshot.observed_at.date().isoformat(),
            "observed_at": snapshot.observed_at.isoformat(),
            "market_state": market_state,
            "open": snapshot.open_value,
            "high": snapshot.high_value,
            "low": snapshot.low_value,
            "last": snapshot.last_value,
            "previous_close": snapshot.previous_close,
            "change_rate": {"value": snapshot.change_rate, "unit": "percent"},
            "volume": {"value": snapshot.volume_shares, "unit": "shares"},
            "amount": {"value": snapshot.amount_cny, "unit": "CNY"},
            "volume_cross_check": {
                "status": "consistent_with_lot_rounding",
                "difference_shares": format(volume_difference, "f"),
            },
            "evidence_ids": [snapshot.evidence_id, fallback.bar_evidence_id],
        },
        "evidence": evidence,
        "conflicts": [],
        "source_errors": [],
        "degradations": [],
        "limitations": [
            {
                "code": "experimental_etf_market_sources",
                "message": (
                    "ETF identity and prices use experimental source operations; "
                    "fallback volume is rounded to board lots."
                ),
            }
        ],
    }


def _quote_conflicts(
    snapshot: EtfSnapshotObservation, fallback: DailyBarObservation | None
) -> list[dict[str, Any]]:
    if fallback is None:
        return []
    fields = {
        "open": (snapshot.open_value, fallback.open_value),
        "high": (snapshot.high_value, fallback.high_value),
        "low": (snapshot.low_value, fallback.low_value),
        "last": (snapshot.last_value, fallback.close_value),
    }
    differing = [
        field
        for field, values in fields.items()
        if Decimal(values[0]) != Decimal(values[1])
    ]
    volume_difference = abs(
        Decimal(snapshot.volume_shares) - Decimal(fallback.volume_shares)
    )
    if volume_difference > 100:
        differing.append("volume")
    if not differing:
        return []
    return [
        {
            "code": "etf_quote_value_conflict",
            "fields": differing,
            "evidence_ids": [snapshot.evidence_id, fallback.bar_evidence_id],
        }
    ]


def _request(
    operation: str,
    transport: HttpTransport,
    url: str,
    referer: str,
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json",
                "Referer": referer,
                "User-Agent": "a-share-research-skill/1",
            },
        )
    except TransportError as error:
        raise _error(operation, error.code, str(error)) from error
    if response.status != 200:
        raise _error(
            operation,
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _error(operation, "empty_response", "The source returned no data.")
    return response


def _json_payload(operation: str, response: HttpResponse) -> Any:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _error(
            operation,
            "unexpected_content_type",
            "The source response is not JSON.",
        )
    try:
        return json.loads(response.body, parse_float=str, parse_int=str)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(
            operation,
            "unknown_schema",
            "The source response is not valid JSON.",
        ) from error


def _snapshot_time(payload: dict[str, Any], operation: str) -> datetime:
    date_value = payload.get("date")
    time_value = payload.get("time")
    if not isinstance(date_value, str) or not isinstance(time_value, str):
        raise _error(operation, "unknown_schema", "The snapshot time is invalid.")
    try:
        return datetime.strptime(
            f"{date_value}{time_value.zfill(6)}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=CHINA_STANDARD_TIME)
    except ValueError as error:
        raise _error(
            operation, "unknown_schema", "The snapshot time is invalid."
        ) from error


def _finite_decimal(value: Any, operation: str) -> str:
    if not isinstance(value, str):
        raise _error(operation, "unknown_schema", "A snapshot decimal is missing.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _error(
            operation, "unknown_schema", "A snapshot decimal is invalid."
        ) from error
    if not parsed.is_finite():
        raise _error(operation, "unknown_schema", "A snapshot decimal is not finite.")
    return value


def _positive_decimal(value: Any, operation: str) -> str:
    text = _finite_decimal(value, operation)
    if Decimal(text) <= 0:
        raise _error(operation, "unknown_schema", "A snapshot price is not positive.")
    return text


def _nonnegative_integer(value: Any, operation: str) -> str:
    text = _finite_decimal(value, operation)
    parsed = Decimal(text)
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise _error(operation, "unknown_schema", "A snapshot quantity is invalid.")
    return format(parsed, "f")


def _source_error(error: SourceOperationError) -> dict[str, str]:
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": str(error),
    }


def _error(operation: str, code: str, message: str) -> SourceOperationError:
    return SourceOperationError(operation, code, message)


def _blocked(
    request: dict[str, Any],
    code: str,
    message: str,
    *,
    subject: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    source_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": [subject] if subject is not None else request["subjects"],
        "quote": {"status": "unresolved"},
        "evidence": evidence or [],
        "conflicts": [],
        "source_errors": source_errors or [],
        "degradations": [],
        "limitations": [{"code": code, "message": message}],
    }
