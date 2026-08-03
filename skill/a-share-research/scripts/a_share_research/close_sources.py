"""Experimental source operations for unadjusted daily close observations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    TransportError,
)


def _operation_error(
    source_operation: str, code: str, message: str
) -> SourceOperationError:
    return SourceOperationError(source_operation, code, message)


def _request(
    source_operation: str,
    transport: HttpTransport,
    url: str,
    headers: dict[str, str],
) -> HttpResponse:
    try:
        response = transport.get(url, headers)
    except TransportError as error:
        raise _operation_error(source_operation, error.code, str(error)) from error
    if response.status != 200:
        raise _operation_error(
            source_operation,
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _operation_error(
            source_operation,
            "empty_response",
            "The source returned an empty response body.",
        )
    return response


def _decimal(value: object, source_operation: str) -> str:
    if not isinstance(value, str) or not value:
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The source response does not contain an exact close price.",
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The source close price is not a decimal value.",
        ) from error
    if not parsed.is_finite() or parsed <= 0:
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The source close price must be a positive finite decimal.",
        )
    return value


def _volume_shares(value: object, source_operation: str, *, lot_size: int) -> str:
    if not isinstance(value, str) or not value:
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The source response does not contain an exact volume.",
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The source volume is not a decimal value.",
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The source volume must be a non-negative finite decimal.",
        )
    shares = parsed * lot_size
    if shares != shares.to_integral_value():
        raise _operation_error(
            source_operation,
            "unknown_schema",
            "The normalized source volume is not a whole number of shares.",
        )
    return format(shares.quantize(Decimal(1)), "f")


def _ohlc(
    open_value: object,
    high_value: object,
    low_value: object,
    close_value: object,
    source_operation: str,
) -> tuple[str, str, str, str]:
    values = (
        _decimal(open_value, source_operation),
        _decimal(high_value, source_operation),
        _decimal(low_value, source_operation),
        _decimal(close_value, source_operation),
    )
    open_price, high_price, low_price, close_price = map(Decimal, values)
    if (
        low_price > high_price
        or open_price < low_price
        or open_price > high_price
        or close_price < low_price
        or close_price > high_price
    ):
        raise _operation_error(
            source_operation,
            "inconsistent_price_bar",
            "The source OHLC values are internally inconsistent.",
        )
    return values


@dataclass(frozen=True)
class DailyBarObservation:
    """A normalized daily OHLCV observation with an explicit adjustment basis."""

    source_operation: str
    source_uri: str
    security: str
    trading_date: date
    open_value: str
    high_value: str
    low_value: str
    close_value: str
    volume_shares: str
    price_type: str
    trading_status: str
    evidence_time: datetime
    available_at: datetime
    retrieved_at: datetime
    availability_status: str = "inferred_from_final_daily_line"
    corporate_action: dict[str, str] | None = None
    adjustment: str = "unadjusted"
    observation_boundary: str | None = None
    previous_close: str | None = None
    previous_close_basis: str | None = None

    @property
    def value(self) -> str:
        """Compatibility projection used by the completed-close module."""

        return self.close_value

    @property
    def evidence_id(self) -> str:
        return (
            f"close-{self.source_operation}-{self.security}-"
            f"{self.trading_date.isoformat()}"
        )

    @property
    def bar_evidence_id(self) -> str:
        return (
            f"bar-{self.source_operation}-{self.security}-"
            f"{self.trading_date.isoformat()}"
        )

    def to_evidence(self) -> dict[str, object]:
        return {
            "id": self.evidence_id,
            "source_role": "market_observation",
            "source_operation": self.source_operation,
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {"value": self.value, "unit": "CNY/share"},
            "basis": "unadjusted_close",
            "observation": {
                "kind": "daily_close",
                "trading_date": self.trading_date.isoformat(),
                "price_type": self.price_type,
                "adjustment": "unadjusted",
                "currency": "CNY",
                "trading_status": self.trading_status,
            },
            "evidence_time": self.evidence_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "availability_status": self.availability_status,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": (
                    f"{self.security} unadjusted close on "
                    f"{self.trading_date.isoformat()}"
                ),
            },
            "limitations": [
                "experimental_source_operation",
                (
                    "first_publication_time_not_source_verified"
                    if self.availability_status == "inferred_from_final_daily_line"
                    else "source_timestamp_not_independently_verified"
                ),
            ],
        }

    def to_bar_evidence(self) -> dict[str, object]:
        return {
            "id": self.bar_evidence_id,
            "source_role": "market_observation",
            "source_operation": self.source_operation,
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": {
                    "open": self.open_value,
                    "high": self.high_value,
                    "low": self.low_value,
                    "close": self.close_value,
                    "volume": self.volume_shares,
                },
                "unit": {
                    "price": "CNY/share",
                    "volume": "shares",
                },
            },
            "basis": f"{self.adjustment}_daily_ohlcv",
            "observation": {
                "kind": "daily_bar",
                "trading_date": self.trading_date.isoformat(),
                "adjustment": self.adjustment,
                "currency": "CNY",
                "trading_status": self.trading_status,
                "corporate_action": self.corporate_action,
            },
            "evidence_time": self.evidence_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "availability_status": self.availability_status,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": (
                    f"{self.security} {self.adjustment} daily bar on "
                    f"{self.trading_date.isoformat()}"
                ),
            },
            "limitations": [
                "experimental_source_operation",
                (
                    "first_publication_time_not_source_verified"
                    if self.availability_status == "inferred_from_final_daily_line"
                    else "source_timestamp_not_independently_verified"
                ),
            ],
        }

    def to_session_evidence(self) -> dict[str, object]:
        """Represent the daily-line row's completed-session assertion separately."""

        return {
            "id": (
                f"session-{self.source_operation}-{self.security}-"
                f"{self.trading_date.isoformat()}"
            ),
            "source_role": "market_observation",
            "source_operation": self.source_operation,
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": "completed",
                "unit": "trading_session",
            },
            "basis": "daily_line_completed_session",
            "observation": {
                "kind": "trading_session",
                "trading_date": self.trading_date.isoformat(),
                "trading_status": self.trading_status,
            },
            "evidence_time": self.evidence_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "availability_status": self.availability_status,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": (
                    f"{self.security} completed daily line on "
                    f"{self.trading_date.isoformat()}"
                ),
            },
            "limitations": [
                "experimental_source_operation",
                "session_status_derived_from_daily_line",
                "first_publication_time_not_source_verified",
            ],
        }


CloseObservation = DailyBarObservation


class SseDailyLineOperation:
    """Observe SSE webpage daily lines without treating them as qualified."""

    operation_id = "sse_daily_line@1"
    endpoint = "https://yunhq.sse.com.cn:32042/v1/sh1/dayk"

    def observe(
        self, security: str, transport: HttpTransport
    ) -> list[DailyBarObservation]:
        exchange, code = security.split(":", 1)
        if exchange != "SSE":
            raise ValueError("SSE daily-line operation requires an SSE security")
        url = f"{self.endpoint}/{code}?{
            urlencode(
                {
                    'select': 'date,open,high,low,close,volume',
                    'begin': '-1000',
                    'end': '-1',
                }
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            {
                "Accept": "application/json",
                "Referer": "https://www.sse.com.cn/market/price/trends/",
                "User-Agent": "a-share-research-skill/1",
            },
        )
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise _operation_error(
                self.operation_id,
                "unexpected_content_type",
                "The SSE daily-line response is not JSON.",
            )
        try:
            payload = json.loads(
                response.body,
                parse_float=str,
                parse_int=str,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _operation_error(
                self.operation_id,
                "unknown_schema",
                "The SSE daily-line response does not match the expected schema.",
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("code") != code
            or not isinstance(payload.get("kline"), list)
        ):
            error_code = (
                "wrong_security_payload"
                if isinstance(payload, dict)
                and isinstance(payload.get("code"), str)
                and payload.get("code") != code
                else "unknown_schema"
            )
            raise _operation_error(
                self.operation_id,
                error_code,
                "The SSE daily-line response has an invalid security or schema.",
            )
        if not payload["kline"]:
            raise _operation_error(
                self.operation_id,
                "empty_observation",
                "The SSE daily-line response contains no price observations.",
            )
        observations = []
        for row in payload["kline"]:
            if not isinstance(row, list) or len(row) != 6:
                raise _operation_error(
                    self.operation_id,
                    "unknown_schema",
                    "The SSE daily-line row does not match the expected schema.",
                )
            try:
                trading_date = datetime.strptime(row[0], "%Y%m%d").date()
                volume = Decimal(row[5])
            except (TypeError, ValueError, InvalidOperation) as error:
                raise _operation_error(
                    self.operation_id,
                    "unknown_schema",
                    "The SSE daily-line row has invalid date or volume fields.",
                ) from error
            session_close = datetime.combine(
                trading_date, time(15, 0), tzinfo=CHINA_STANDARD_TIME
            )
            open_value, high_value, low_value, close_value = _ohlc(
                row[1], row[2], row[3], row[4], self.operation_id
            )
            observations.append(
                DailyBarObservation(
                    source_operation=self.operation_id,
                    source_uri=url,
                    security=security,
                    trading_date=trading_date,
                    open_value=open_value,
                    high_value=high_value,
                    low_value=low_value,
                    close_value=close_value,
                    volume_shares=_volume_shares(row[5], self.operation_id, lot_size=1),
                    price_type="close",
                    trading_status="traded" if volume > 0 else "suspended",
                    evidence_time=session_close,
                    available_at=session_close,
                    retrieved_at=response.retrieved_at,
                )
            )
        return observations


class SzseDailyLineOperation:
    """Observe SZSE webpage daily lines without treating them as qualified."""

    operation_id = "szse_daily_line@1"
    endpoint = "https://www.szse.cn/api/market/ssjjhq/getHistoryData"

    def observe(
        self, security: str, transport: HttpTransport
    ) -> list[DailyBarObservation]:
        exchange, code = security.split(":", 1)
        if exchange != "SZSE":
            raise ValueError("SZSE daily-line operation requires an SZSE security")
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'cycleType': '32',
                    'marketId': '1',
                    'code': code,
                }
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            {
                "Accept": "application/json",
                "Referer": "https://www.szse.cn/market/trend/index.html",
                "User-Agent": "a-share-research-skill/1",
            },
        )
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise _operation_error(
                self.operation_id,
                "unexpected_content_type",
                "The SZSE daily-line response is not JSON.",
            )
        try:
            payload = json.loads(
                response.body,
                parse_float=str,
                parse_int=str,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _operation_error(
                self.operation_id,
                "unknown_schema",
                "The SZSE daily-line response does not match the expected schema.",
            ) from error
        data = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("code") != code
            or not isinstance(data.get("picupdata"), list)
        ):
            error_code = (
                "wrong_security_payload"
                if isinstance(data, dict)
                and isinstance(data.get("code"), str)
                and data.get("code") != code
                else "unknown_schema"
            )
            raise _operation_error(
                self.operation_id,
                error_code,
                "The SZSE daily-line response has an invalid security or schema.",
            )
        if not data["picupdata"]:
            raise _operation_error(
                self.operation_id,
                "empty_observation",
                "The SZSE daily-line response contains no price observations.",
            )
        observations = []
        for row in data["picupdata"]:
            if not isinstance(row, list) or len(row) != 9:
                raise _operation_error(
                    self.operation_id,
                    "unknown_schema",
                    "The SZSE daily-line row does not match the expected schema.",
                )
            try:
                trading_date = date.fromisoformat(row[0])
                volume = Decimal(row[7])
            except (TypeError, ValueError, InvalidOperation) as error:
                raise _operation_error(
                    self.operation_id,
                    "unknown_schema",
                    "The SZSE daily-line row has invalid date or volume fields.",
                ) from error
            session_close = datetime.combine(
                trading_date, time(15, 0), tzinfo=CHINA_STANDARD_TIME
            )
            open_value, high_value, low_value, close_value = _ohlc(
                row[1], row[4], row[3], row[2], self.operation_id
            )
            observations.append(
                DailyBarObservation(
                    source_operation=self.operation_id,
                    source_uri=url,
                    security=security,
                    trading_date=trading_date,
                    open_value=open_value,
                    high_value=high_value,
                    low_value=low_value,
                    close_value=close_value,
                    volume_shares=_volume_shares(
                        row[7], self.operation_id, lot_size=100
                    ),
                    price_type="close",
                    trading_status="traded" if volume > 0 else "suspended",
                    evidence_time=session_close,
                    available_at=session_close,
                    retrieved_at=response.retrieved_at,
                )
            )
        return observations


class TencentDailyLineOperation:
    """Observe Tencent daily lines without promoting them to qualified data."""

    operation_id = "tencent_daily_line@1"
    endpoint = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
    corporate_action_fields = {"FHcontent", "cqr", "djr", "fh_sh", "nd"}

    def observe(
        self,
        security: str,
        as_of: date,
        transport: HttpTransport,
    ) -> list[DailyBarObservation]:
        exchange, code = security.split(":", 1)
        prefix = {"SSE": "sh", "SZSE": "sz"}.get(exchange)
        if prefix is None:
            raise ValueError(
                "Tencent daily-line operation requires an SSE/SZSE security"
            )
        query_security = f"{prefix}{code}"
        start = (as_of - timedelta(days=400)).isoformat()
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'param': f'{query_security},day,{start},{as_of.isoformat()},500',
                }
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            {
                "Accept": "application/json,text/html",
                "Referer": "https://gu.qq.com/",
                "User-Agent": "a-share-research-skill/1",
            },
        )
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type not in {"text/html", "text/plain"}:
            raise _operation_error(
                self.operation_id,
                "unexpected_content_type",
                "The Tencent daily-line response is not the expected JSON format.",
            )
        try:
            payload = json.loads(
                response.body,
                parse_float=str,
                parse_int=str,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _operation_error(
                self.operation_id,
                "unknown_schema",
                "The Tencent daily-line response is not valid JSON.",
            ) from error
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or query_security not in data:
            raise _operation_error(
                self.operation_id,
                "wrong_security_payload",
                "The Tencent daily-line response does not identify the requested security.",
            )
        security_payload = data[query_security]
        if not isinstance(security_payload, dict) or not isinstance(
            security_payload.get("day"), list
        ):
            raise _operation_error(
                self.operation_id,
                "unknown_schema",
                "The Tencent daily-line response does not match the expected schema.",
            )
        if not security_payload["day"]:
            raise _operation_error(
                self.operation_id,
                "empty_observation",
                "The Tencent daily-line response contains no price observations.",
            )
        qt = security_payload.get("qt")
        quote_fields = qt.get(query_security) if isinstance(qt, dict) else None
        if not isinstance(quote_fields, list) or len(quote_fields) < 31:
            raise _operation_error(
                self.operation_id,
                "unknown_schema",
                "The Tencent quote metadata does not match the expected schema.",
            )
        if quote_fields[2] != code:
            raise _operation_error(
                self.operation_id,
                "wrong_security_payload",
                "The Tencent quote metadata identifies a different security.",
            )
        try:
            quote_time = datetime.strptime(quote_fields[30], "%Y%m%d%H%M%S").replace(
                tzinfo=CHINA_STANDARD_TIME
            )
            quote_volume = Decimal(quote_fields[6])
            quote_value = Decimal(_decimal(quote_fields[3], self.operation_id))
            previous_close = Decimal(_decimal(quote_fields[4], self.operation_id))
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _operation_error(
                self.operation_id,
                "unknown_schema",
                "The Tencent quote metadata has invalid price, time, or volume fields.",
            ) from error
        observations = []
        observed_dates = set()
        for row in security_payload["day"]:
            if not isinstance(row, list) or len(row) not in {6, 7}:
                raise _operation_error(
                    self.operation_id,
                    "unknown_schema",
                    "The Tencent daily-line row does not match the expected schema.",
                )
            annotation = None
            if len(row) == 7:
                annotation = row[6]
                if (
                    not isinstance(annotation, dict)
                    or set(annotation) != self.corporate_action_fields
                    or not all(isinstance(value, str) for value in annotation.values())
                ):
                    raise _operation_error(
                        self.operation_id,
                        "unknown_schema",
                        "The Tencent daily-line annotation does not match the expected schema.",
                    )
            try:
                trading_date = date.fromisoformat(row[0])
                volume = Decimal(row[5])
            except (TypeError, ValueError, InvalidOperation) as error:
                raise _operation_error(
                    self.operation_id,
                    "unknown_schema",
                    "The Tencent daily-line row has invalid date or volume fields.",
                ) from error
            if trading_date > as_of:
                raise _operation_error(
                    self.operation_id,
                    "observation_after_requested_range",
                    "Tencent returned a daily observation after the requested date.",
                )
            is_live_row = trading_date == quote_time.date()
            before_close = is_live_row and quote_time.time() < time(15, 0)
            live_price_type = "intraday_last"
            observation_boundary = None
            declared_status = (
                str(quote_fields[33]).casefold()
                if len(quote_fields) > 33 and quote_fields[33]
                else None
            )
            if declared_status not in {
                None,
                "traded",
                "suspended",
                "not_traded",
                "no_trade",
            }:
                raise _operation_error(
                    self.operation_id,
                    "unknown_trading_status",
                    "The Tencent quote metadata has an unknown trading status.",
                )
            previous_close_basis = (
                str(quote_fields[34])
                if len(quote_fields) > 34 and quote_fields[34]
                else None
            )
            if is_live_row and len(quote_fields) > 31:
                declared_price_type = quote_fields[31]
                if declared_price_type and declared_price_type not in {
                    "latest_traded",
                    "indicative_auction",
                }:
                    raise _operation_error(
                        self.operation_id,
                        "unknown_price_type",
                        "The Tencent quote metadata has an unknown intraday price type.",
                    )
                if declared_price_type:
                    live_price_type = declared_price_type
            if is_live_row and len(quote_fields) > 32 and quote_fields[32]:
                observation_boundary = str(quote_fields[32])
            suspended = is_live_row and (
                declared_status in {"suspended", "not_traded", "no_trade"}
                or (
                    quote_time.time() >= time(15, 0)
                    and quote_volume == 0
                    and quote_value == previous_close
                )
            )
            session_close = datetime.combine(
                trading_date, time(15, 0), tzinfo=CHINA_STANDARD_TIME
            )
            open_value, high_value, low_value, close_value = _ohlc(
                row[1], row[3], row[4], row[2], self.operation_id
            )
            observations.append(
                DailyBarObservation(
                    source_operation=self.operation_id,
                    source_uri=url,
                    security=security,
                    trading_date=trading_date,
                    open_value=open_value,
                    high_value=high_value,
                    low_value=low_value,
                    close_value=close_value,
                    volume_shares=_volume_shares(
                        row[5], self.operation_id, lot_size=100
                    ),
                    price_type=live_price_type if before_close else "close",
                    trading_status=(
                        "suspended"
                        if suspended
                        else "traded"
                        if volume > 0
                        else "unknown"
                    ),
                    evidence_time=quote_time if is_live_row else session_close,
                    available_at=quote_time if is_live_row else session_close,
                    retrieved_at=response.retrieved_at,
                    availability_status=(
                        "source_timestamp"
                        if is_live_row
                        else "inferred_from_final_daily_line"
                    ),
                    corporate_action=annotation,
                    observation_boundary=observation_boundary,
                    previous_close=format(previous_close, "f"),
                    previous_close_basis=previous_close_basis,
                )
            )
            observed_dates.add(trading_date)
        quote_is_suspended = (
            quote_time.date() <= as_of
            and quote_time.time() >= time(15, 0)
            and quote_volume == 0
            and quote_value == previous_close
        )
        if quote_is_suspended and quote_time.date() not in observed_dates:
            observations.append(
                DailyBarObservation(
                    source_operation=self.operation_id,
                    source_uri=url,
                    security=security,
                    trading_date=quote_time.date(),
                    open_value=quote_fields[3],
                    high_value=quote_fields[3],
                    low_value=quote_fields[3],
                    close_value=quote_fields[3],
                    volume_shares=_volume_shares(
                        quote_fields[6], self.operation_id, lot_size=100
                    ),
                    price_type="stale_last",
                    trading_status="suspended",
                    evidence_time=quote_time,
                    available_at=quote_time,
                    retrieved_at=response.retrieved_at,
                    availability_status="source_timestamp",
                    previous_close=format(previous_close, "f"),
                    previous_close_basis=previous_close_basis,
                )
            )
        return observations
