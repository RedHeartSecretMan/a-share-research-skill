"""Experimental forward-adjusted daily-line source operations."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from .close_sources import DailyBarObservation, _ohlc, _volume_shares
from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    TransportError,
)


class TencentForwardAdjustedDailyLineOperation:
    operation_id = "tencent_forward_adjusted_daily_line@1"
    endpoint = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    corporate_action_fields = {"FHcontent", "cqr", "djr", "fh_sh", "nd"}

    def observe(
        self, security: str, as_of: date, transport: HttpTransport
    ) -> list[DailyBarObservation]:
        exchange, code = security.split(":", 1)
        prefix = {"SSE": "sh", "SZSE": "sz"}.get(exchange)
        if prefix is None:
            raise ValueError("Tencent adjusted lines require an SSE/SZSE security")
        query_security = f"{prefix}{code}"
        start = (as_of - timedelta(days=400)).isoformat()
        url = f"{self.endpoint}?{
            urlencode(
                {'param': (f'{query_security},day,{start},{as_of.isoformat()},500,qfq')}
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://gu.qq.com/",
            {"text/html", "text/plain"},
        )
        payload = _json(self.operation_id, response)
        data = payload.get("data") if isinstance(payload, dict) else None
        security_payload = data.get(query_security) if isinstance(data, dict) else None
        if not isinstance(security_payload, dict):
            raise _error(
                self.operation_id,
                "wrong_security_payload",
                "Tencent did not identify the requested adjusted security.",
            )
        rows = security_payload.get("qfqday")
        if not isinstance(rows, list):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "Tencent forward-adjusted rows do not match the expected schema.",
            )
        if not rows:
            raise _error(
                self.operation_id,
                "empty_observation",
                "Tencent returned no forward-adjusted daily observations.",
            )
        quote_time = _tencent_quote_time(
            security_payload, query_security, code, self.operation_id
        )
        observations = []
        for row in rows:
            if not isinstance(row, list) or len(row) not in {6, 7}:
                raise _error(
                    self.operation_id,
                    "unknown_schema",
                    "A Tencent forward-adjusted row has an unknown schema.",
                )
            annotation = None
            if len(row) == 7:
                annotation = row[6]
                if (
                    not isinstance(annotation, dict)
                    or set(annotation) != self.corporate_action_fields
                    or not all(isinstance(value, str) for value in annotation.values())
                ):
                    raise _error(
                        self.operation_id,
                        "unknown_schema",
                        "A Tencent corporate-action annotation is invalid.",
                    )
            try:
                trading_date = date.fromisoformat(row[0])
                volume = Decimal(row[5])
            except (TypeError, ValueError, InvalidOperation) as error:
                raise _error(
                    self.operation_id,
                    "unknown_schema",
                    "A Tencent forward-adjusted date or volume is invalid.",
                ) from error
            if trading_date > as_of:
                raise _error(
                    self.operation_id,
                    "observation_after_requested_range",
                    "Tencent returned an adjusted row after the research date.",
                )
            open_value, high_value, low_value, close_value = _ohlc(
                row[1], row[3], row[4], row[2], self.operation_id
            )
            session_close = datetime.combine(
                trading_date, time(15, 0), tzinfo=CHINA_STANDARD_TIME
            )
            is_live = trading_date == quote_time.date()
            before_close = is_live and quote_time.time() < time(15, 0)
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
                    price_type="intraday_last" if before_close else "close",
                    trading_status="traded" if volume > 0 else "suspended",
                    evidence_time=quote_time if is_live else session_close,
                    available_at=quote_time if is_live else session_close,
                    retrieved_at=response.retrieved_at,
                    availability_status=(
                        "source_timestamp"
                        if is_live
                        else "inferred_from_final_daily_line"
                    ),
                    corporate_action=annotation,
                    adjustment="forward_adjusted",
                )
            )
        return observations


class EastmoneyForwardAdjustedDailyLineOperation:
    operation_id = "eastmoney_forward_adjusted_daily_line@1"
    endpoint = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def observe(
        self, security: str, as_of: date, transport: HttpTransport
    ) -> list[DailyBarObservation]:
        exchange, code = security.split(":", 1)
        market = {"SZSE": "0", "SSE": "1"}.get(exchange)
        if market is None:
            raise ValueError("Eastmoney adjusted lines require an SSE/SZSE security")
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'secid': f'{market}.{code}',
                    'klt': '101',
                    'fqt': '1',
                    'beg': (as_of - timedelta(days=400)).strftime('%Y%m%d'),
                    'end': as_of.strftime('%Y%m%d'),
                    'fields1': 'f1,f2,f3,f4,f5,f6',
                    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
                }
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://quote.eastmoney.com/",
            {"application/json"},
        )
        payload = _json(self.operation_id, response)
        data = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("code") != code
            or not isinstance(data.get("klines"), list)
        ):
            error_code = (
                "wrong_security_payload"
                if isinstance(data, dict)
                and isinstance(data.get("code"), str)
                and data.get("code") != code
                else "unknown_schema"
            )
            raise _error(
                self.operation_id,
                error_code,
                "The Eastmoney adjusted response has an invalid security or schema.",
            )
        if not data["klines"]:
            raise _error(
                self.operation_id,
                "empty_observation",
                "Eastmoney returned no forward-adjusted daily observations.",
            )
        observations = []
        for encoded_row in data["klines"]:
            if not isinstance(encoded_row, str):
                raise _error(
                    self.operation_id,
                    "unknown_schema",
                    "An Eastmoney adjusted row is not encoded as text.",
                )
            row = encoded_row.split(",")
            if len(row) != 11:
                raise _error(
                    self.operation_id,
                    "unknown_schema",
                    "An Eastmoney adjusted row has an unknown schema.",
                )
            try:
                trading_date = date.fromisoformat(row[0])
                volume = Decimal(row[5])
            except (ValueError, InvalidOperation) as error:
                raise _error(
                    self.operation_id,
                    "unknown_schema",
                    "An Eastmoney adjusted date or volume is invalid.",
                ) from error
            open_value, high_value, low_value, close_value = _ohlc(
                row[1], row[3], row[4], row[2], self.operation_id
            )
            session_close = datetime.combine(
                trading_date, time(15, 0), tzinfo=CHINA_STANDARD_TIME
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
                    price_type="close",
                    trading_status="traded" if volume > 0 else "suspended",
                    evidence_time=session_close,
                    available_at=session_close,
                    retrieved_at=response.retrieved_at,
                    adjustment="forward_adjusted",
                )
            )
        return observations


def _tencent_quote_time(
    payload: dict[str, object], query_security: str, code: str, operation: str
) -> datetime:
    qt = payload.get("qt")
    fields = qt.get(query_security) if isinstance(qt, dict) else None
    if (
        not isinstance(fields, list)
        or len(fields) < 31
        or fields[2] != code
        or not isinstance(fields[30], str)
    ):
        raise _error(
            operation,
            "wrong_security_payload",
            "Tencent quote metadata does not match the adjusted security.",
        )
    try:
        return datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(
            tzinfo=CHINA_STANDARD_TIME
        )
    except ValueError as error:
        raise _error(
            operation,
            "unknown_schema",
            "Tencent quote metadata has an invalid timestamp.",
        ) from error


def _request(
    operation: str,
    transport: HttpTransport,
    url: str,
    referer: str,
    media_types: set[str],
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json,text/html",
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
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in media_types:
        raise _error(
            operation,
            "unexpected_content_type",
            "The adjusted source response has an unexpected content type.",
        )
    return response


def _json(operation: str, response: HttpResponse) -> object:
    try:
        return json.loads(response.body, parse_float=str, parse_int=str)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(
            operation,
            "unknown_schema",
            "The adjusted source response is not valid JSON.",
        ) from error


def _error(operation: str, code: str, message: str) -> SourceOperationError:
    return SourceOperationError(operation, code, message)
