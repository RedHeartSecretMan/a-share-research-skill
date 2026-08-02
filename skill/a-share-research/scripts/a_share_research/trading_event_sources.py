"""Experimental Eastmoney sources for trading and corporate events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any
from urllib.parse import urlencode

from .capital_contract import (
    CapitalHttpTransport,
    CapitalObservation,
    CapitalQuery,
    CapitalSourceBatch,
    CapitalSourceFailure,
)
from .identity_sources import HttpResponse, TransportError
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
)

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
BLOCK_TRADE_UNIT_DEFINITION_URL = "https://data.eastmoney.com/dzjy/detail/{code}.html"
USER_AGENT = "Mozilla/5.0 a-share-research-skill/1"


@dataclass(frozen=True)
class _FetchedRow:
    value: dict[str, Any]
    retrieved_at: datetime
    locator_uri: str


class _SourceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostics: tuple[RequestGateDiagnostic, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


def _failure(operation_id: str, error: _SourceError) -> CapitalSourceFailure:
    return CapitalSourceFailure(operation_id, error.code, str(error))


def _gate_degradation(
    operation_id: str,
    diagnostic: RequestGateDiagnostic,
) -> CapitalSourceFailure:
    return CapitalSourceFailure(
        source_operation=operation_id,
        code=diagnostic.code,
        message=diagnostic.message,
        details=diagnostic.details(),
    )


class EastmoneyTradingEventOperation:
    """Collect bounded trading-event observations from Eastmoney datacenter."""

    operation_id = "eastmoney_trading_events@1"
    supported_data_types = frozenset(
        {"dragon_tiger", "market_dragon_tiger", "lockup", "block_trade"}
    )

    def __init__(
        self,
        transport: CapitalHttpTransport,
        *,
        page_size: int = 50,
        max_pages: int = 25,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 500 or not 1 <= max_pages <= 100:
            raise ValueError("trading-event pagination bounds are invalid")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        selected = tuple(
            data_type
            for data_type in query.data_types
            if data_type in self.supported_data_types
        )
        if not selected:
            return CapitalSourceBatch(operation_id=self.operation_id)
        observations: list[CapitalObservation] = []
        degradations: list[CapitalSourceFailure] = []
        for data_type in selected:
            try:
                if data_type == "dragon_tiger":
                    collected, diagnostics = self._collect_stock_dragon_tiger(query)
                elif data_type == "market_dragon_tiger":
                    collected, diagnostics = self._collect_market_dragon_tiger(query)
                elif data_type == "lockup":
                    collected, diagnostics = self._collect_lockup(query)
                elif data_type == "block_trade":
                    collected, diagnostics = self._collect_block_trade(query)
                else:
                    raise _SourceError(
                        "unsupported_data_type",
                        "The trading-event data type is not implemented.",
                    )
            except _SourceError as error:
                degradations.extend(
                    _gate_degradation(self.operation_id, item)
                    for item in error.diagnostics
                )
                return CapitalSourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    source_errors=(_failure(self.operation_id, error),),
                    degradations=tuple(degradations),
                    complete=False,
                )
            observations.extend(collected)
            degradations.extend(
                _gate_degradation(self.operation_id, item) for item in diagnostics
            )
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            degradations=tuple(degradations),
        )

    def _collect_stock_dragon_tiger(
        self,
        query: CapitalQuery,
    ) -> tuple[list[CapitalObservation], tuple[RequestGateDiagnostic, ...]]:
        observed_from, observed_to = _historical_window(query)
        _exchange, code = _canonical_subject(query)
        records, diagnostics = self._fetch_report(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_value=(
                f"(TRADE_DATE>='{observed_from}')(TRADE_DATE<='{observed_to}')"
                f'(SECURITY_CODE="{code}")'
            ),
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page_size=min(self._page_size, 500),
        )
        normalized_records: list[tuple[_FetchedRow, str, str, Decimal, Decimal]] = []
        for fetched in records:
            row = fetched.value
            _require_security(row, code)
            observed_on = _row_date(row.get("TRADE_DATE"), "trade date")
            _require_in_window(observed_on, observed_from, observed_to)
            reason = _required_text(row.get("EXPLANATION"), "billboard reason")
            normalized_records.append(
                (
                    fetched,
                    observed_on,
                    reason,
                    _required_decimal(row.get("BILLBOARD_NET_AMT"), "net buy"),
                    _required_decimal(row.get("TURNOVERRATE"), "turnover rate"),
                )
            )
        normalized_records.sort(key=lambda item: item[1], reverse=True)
        normalized_records = normalized_records[: query.limit]
        if not normalized_records:
            raise _SourceError(
                "empty_response",
                "The source returned no usable dragon-tiger records.",
            )
        latest_date = normalized_records[0][1]
        buy_rows, buy_diagnostics = self._fetch_report(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_value=(f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")"),
            sort_columns="BUY",
            sort_types="-1",
            page_size=50,
        )
        sell_rows, sell_diagnostics = self._fetch_report(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_value=(f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")"),
            sort_columns="SELL",
            sort_types="-1",
            page_size=50,
        )
        buy_seats = _normalize_seats(
            buy_rows, side="buy", code=code, trade_date=latest_date
        )
        sell_seats = _normalize_seats(
            sell_rows, side="sell", code=code, trade_date=latest_date
        )
        institution_buy = sum(
            (
                _required_decimal(item.value.get("BUY"), "institution buy")
                for item in buy_rows
                if str(item.value.get("OPERATEDEPT_CODE")) == "0"
            ),
            Decimal(0),
        )
        institution_sell = sum(
            (
                _required_decimal(item.value.get("SELL"), "institution sell")
                for item in sell_rows
                if str(item.value.get("OPERATEDEPT_CODE")) == "0"
            ),
            Decimal(0),
        )
        retrieved_at = max(
            [item.retrieved_at for item in records + buy_rows + sell_rows]
        )
        observations: list[CapitalObservation] = []
        for fetched, observed_on, reason, net_buy, turnover_rate in normalized_records:
            is_latest = observed_on == latest_date
            observations.append(
                CapitalObservation(
                    data_type="dragon_tiger",
                    source_operation=self.operation_id,
                    source_role="market_signal",
                    subject=query.subject,
                    observed_on=observed_on,
                    available_at=None,
                    retrieved_at=retrieved_at if is_latest else fetched.retrieved_at,
                    period={
                        "start": observed_on,
                        "end": observed_on,
                        "frequency": "event",
                    },
                    metrics={
                        "net_buy_amount": _decimal_text(net_buy),
                        "turnover_rate": _decimal_text(turnover_rate),
                        "institution_buy_amount": _decimal_text(institution_buy)
                        if is_latest
                        else None,
                        "institution_sell_amount": _decimal_text(institution_sell)
                        if is_latest
                        else None,
                        "institution_net_amount": _decimal_text(
                            institution_buy - institution_sell
                        )
                        if is_latest
                        else None,
                    },
                    units={
                        "net_buy_amount": "CNY",
                        "turnover_rate": "percent",
                        "institution_buy_amount": "CNY",
                        "institution_sell_amount": "CNY",
                        "institution_net_amount": "CNY",
                    },
                    directions={
                        "net_buy_amount": "positive_is_net_buy",
                        "turnover_rate": "not_directional",
                        "institution_buy_amount": "positive_is_buy",
                        "institution_sell_amount": "positive_is_sell",
                        "institution_net_amount": "positive_is_net_buy",
                    },
                    dimensions={
                        "reason": reason,
                        "buy_seats": buy_seats[:5] if is_latest else [],
                        "sell_seats": sell_seats[:5] if is_latest else [],
                        "seat_amount_unit": "CNY",
                        "seat_amount_directions": {
                            "buy_amount": "positive_is_buy",
                            "sell_amount": "positive_is_sell",
                            "net_amount": "positive_is_net_buy",
                        },
                    },
                    locator_uri=fetched.locator_uri,
                    limitations=(
                        "availability_time_unknown",
                        *(
                            ("dragon_tiger_seat_and_institution_details_not_collected",)
                            if not is_latest
                            else ()
                        ),
                    ),
                )
            )
        return observations, diagnostics + buy_diagnostics + sell_diagnostics

    def _collect_market_dragon_tiger(
        self,
        query: CapitalQuery,
    ) -> tuple[list[CapitalObservation], tuple[RequestGateDiagnostic, ...]]:
        if query.subject is not None:
            raise _SourceError(
                "invalid_subject",
                "The all-market dragon-tiger ranking must not have a subject.",
            )
        observed_from, observed_to = _historical_window(query)
        if observed_from != observed_to:
            raise _SourceError(
                "invalid_window",
                "The all-market dragon-tiger ranking requires one exact date.",
            )
        rows, diagnostics = self._fetch_report(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_value=(
                f"(TRADE_DATE>='{observed_from}')(TRADE_DATE<='{observed_to}')"
            ),
            sort_columns="BILLBOARD_NET_AMT",
            sort_types="-1",
            page_size=min(self._page_size, 500),
        )
        normalized: list[
            tuple[
                _FetchedRow,
                dict[str, Any],
                str,
                str,
                tuple[
                    Decimal,
                    Decimal,
                    Decimal,
                    Decimal,
                    Decimal,
                    Decimal | None,
                ],
            ]
        ] = []
        for fetched in rows:
            row = fetched.value
            provider_code, provider_name = _market_provider_security(row)
            observed_on = _row_date(row.get("TRADE_DATE"), "trade date")
            _require_in_window(observed_on, observed_from, observed_to)
            reason = _required_text(row.get("EXPLANATION"), "billboard reason")
            metrics = (
                _required_decimal(row.get("CLOSE_PRICE"), "close price"),
                _required_decimal(row.get("CHANGE_RATE"), "change rate"),
                _required_decimal(row.get("BILLBOARD_NET_AMT"), "net buy"),
                _required_decimal(row.get("BILLBOARD_BUY_AMT"), "buy amount"),
                _required_decimal(row.get("BILLBOARD_SELL_AMT"), "sell amount"),
                _optional_decimal(row.get("TURNOVERRATE"), "turnover rate"),
            )
            normalized.append(
                (
                    fetched,
                    {
                        "provider_security_code": provider_code,
                        "provider_security_name": provider_name,
                    },
                    observed_on,
                    reason,
                    metrics,
                )
            )
        normalized.sort(key=lambda item: item[4][2], reverse=True)
        observations: list[CapitalObservation] = []
        for rank, (
            fetched,
            provider_identity,
            observed_on,
            reason,
            metrics,
        ) in enumerate(normalized[: query.limit], 1):
            close_price, change_rate, net_buy, buy, sell, turnover_rate = metrics
            observations.append(
                CapitalObservation(
                    data_type="market_dragon_tiger",
                    source_operation=self.operation_id,
                    source_role="market_signal",
                    subject=None,
                    observed_on=observed_on,
                    available_at=None,
                    retrieved_at=fetched.retrieved_at,
                    period={
                        "start": observed_on,
                        "end": observed_on,
                        "frequency": "daily",
                    },
                    metrics={
                        "close_price": _decimal_text(close_price),
                        "change_rate": _decimal_text(change_rate),
                        "net_buy_amount": _decimal_text(net_buy),
                        "buy_amount": _decimal_text(buy),
                        "sell_amount": _decimal_text(sell),
                        "turnover_rate": (
                            _decimal_text(turnover_rate)
                            if turnover_rate is not None
                            else None
                        ),
                    },
                    units={
                        "close_price": "CNY/share",
                        "change_rate": "percent",
                        "net_buy_amount": "CNY",
                        "buy_amount": "CNY",
                        "sell_amount": "CNY",
                        "turnover_rate": "percent",
                    },
                    directions={
                        "close_price": "not_directional",
                        "change_rate": "positive_is_price_increase",
                        "net_buy_amount": "positive_is_net_buy",
                        "buy_amount": "positive_is_buy",
                        "sell_amount": "positive_is_sell",
                        "turnover_rate": "not_directional",
                    },
                    dimensions={
                        "market_scope": "eastmoney_all_market_billboard",
                        "reason": reason,
                        "net_buy_rank": rank,
                        **provider_identity,
                    },
                    locator_uri=fetched.locator_uri,
                    limitations=(
                        "availability_time_unknown",
                        "security_exchange_unverified",
                        *(("source_value_missing",) if turnover_rate is None else ()),
                    ),
                )
            )
        return observations, diagnostics

    def _collect_block_trade(
        self,
        query: CapitalQuery,
    ) -> tuple[list[CapitalObservation], tuple[RequestGateDiagnostic, ...]]:
        observed_from, observed_to = _historical_window(query)
        _exchange, code = _canonical_subject(query)
        rows, diagnostics = self._fetch_report(
            "RPT_DATA_BLOCKTRADE",
            filter_value=(
                f'(SECURITY_CODE="{code}")'
                f"(TRADE_DATE>='{observed_from}')(TRADE_DATE<='{observed_to}')"
            ),
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page_size=min(self._page_size, 500),
        )
        normalized: list[
            tuple[
                _FetchedRow,
                str,
                Decimal,
                Decimal,
                Decimal,
                Decimal,
                str,
                str,
            ]
        ] = []
        for fetched in rows:
            row = fetched.value
            _require_security(row, code)
            observed_on = _row_date(row.get("TRADE_DATE"), "block-trade date")
            _require_in_window(observed_on, observed_from, observed_to)
            deal_price = _required_positive_decimal(row.get("DEAL_PRICE"), "deal price")
            close_price = _required_positive_decimal(
                row.get("CLOSE_PRICE"), "close price"
            )
            volume = _required_nonnegative_decimal(
                row.get("DEAL_VOLUME"), "deal volume"
            )
            amount = _required_nonnegative_decimal(row.get("DEAL_AMT"), "deal amount")
            buyer = _required_text(row.get("BUYER_NAME"), "buyer department")
            seller = _required_text(row.get("SELLER_NAME"), "seller department")
            normalized.append(
                (
                    fetched,
                    observed_on,
                    deal_price,
                    close_price,
                    volume,
                    amount,
                    buyer,
                    seller,
                )
            )
        normalized.sort(key=lambda item: item[1], reverse=True)
        observations: list[CapitalObservation] = []
        for (
            fetched,
            observed_on,
            deal_price,
            close_price,
            volume,
            amount,
            buyer,
            seller,
        ) in normalized[: query.limit]:
            premium = (deal_price / close_price - Decimal(1)) * Decimal(100)
            observations.append(
                CapitalObservation(
                    data_type="block_trade",
                    source_operation=self.operation_id,
                    source_role="market_observation",
                    subject=query.subject,
                    observed_on=observed_on,
                    available_at=None,
                    retrieved_at=fetched.retrieved_at,
                    period={
                        "start": observed_on,
                        "end": observed_on,
                        "frequency": "event",
                    },
                    metrics={
                        "deal_price": _decimal_text(deal_price),
                        "close_price": _decimal_text(close_price),
                        "deal_volume": _decimal_text(volume),
                        "deal_amount": _decimal_text(amount),
                        "premium_rate": _decimal_text(premium),
                    },
                    units={
                        "deal_price": "CNY/share",
                        "close_price": "CNY/share",
                        "deal_volume": "share",
                        "deal_amount": "CNY",
                        "premium_rate": "percent",
                    },
                    directions={
                        "deal_price": "not_directional",
                        "close_price": "not_directional",
                        "deal_volume": "positive_is_more_volume",
                        "deal_amount": "positive_is_more_value",
                        "premium_rate": ("positive_is_premium_negative_is_discount"),
                    },
                    dimensions={
                        "buyer_department": buyer,
                        "seller_department": seller,
                        "provider_raw_units": {
                            "DEAL_VOLUME": "share",
                            "DEAL_AMT": "CNY",
                        },
                        "provider_display_scale_power_of_ten": "-4",
                        "unit_definition_uri": BLOCK_TRADE_UNIT_DEFINITION_URL.format(
                            code=code
                        ),
                    },
                    locator_uri=fetched.locator_uri,
                    limitations=("availability_time_unknown",),
                )
            )
        return observations, diagnostics

    def _collect_lockup(
        self,
        query: CapitalQuery,
    ) -> tuple[list[CapitalObservation], tuple[RequestGateDiagnostic, ...]]:
        observed_from, observed_to, as_of = _lockup_window(query)
        _exchange, code = _canonical_subject(query)
        rows, diagnostics = self._fetch_report(
            "RPT_LIFT_STAGE",
            filter_value=(
                f'(SECURITY_CODE="{code}")'
                f"(FREE_DATE>='{observed_from}')(FREE_DATE<='{observed_to}')"
            ),
            sort_columns="FREE_DATE",
            sort_types="1",
            page_size=min(self._page_size, 500),
        )
        normalized: list[tuple[_FetchedRow, str, str, Decimal, Decimal, Decimal]] = []
        for fetched in rows:
            row = fetched.value
            _require_security(row, code)
            observed_on = _row_date(row.get("FREE_DATE"), "lockup date")
            _require_in_window(observed_on, observed_from, observed_to)
            lockup_type = _required_text(
                row.get("FREE_SHARES_TYPE"), "lockup share type"
            )
            released = _required_nonnegative_decimal(
                row.get("FREE_SHARES"), "released shares"
            )
            tradable = _required_nonnegative_decimal(
                row.get("ABLE_FREE_SHARES"), "tradable shares"
            )
            ratio = _required_nonnegative_decimal(
                row.get("FREE_RATIO"), "total share ratio"
            )
            if ratio > 1:
                raise _SourceError(
                    "unknown_schema", "The source total share ratio exceeds one."
                )
            normalized.append(
                (fetched, observed_on, lockup_type, released, tradable, ratio)
            )
        normalized.sort(key=lambda item: item[1])
        observations: list[CapitalObservation] = []
        for fetched, observed_on, lockup_type, released, tradable, ratio in normalized[
            : query.limit
        ]:
            observations.append(
                CapitalObservation(
                    data_type="lockup",
                    source_operation=self.operation_id,
                    source_role="market_observation",
                    subject=query.subject,
                    observed_on=observed_on,
                    available_at=None,
                    retrieved_at=fetched.retrieved_at,
                    period={
                        "start": observed_on,
                        "end": observed_on,
                        "frequency": "event",
                    },
                    metrics={
                        "released_shares": _decimal_text(released),
                        "tradable_shares": _decimal_text(tradable),
                        "total_share_ratio": _decimal_text(ratio),
                    },
                    units={
                        "released_shares": "10k_shares",
                        "tradable_shares": "10k_shares",
                        "total_share_ratio": "ratio",
                    },
                    directions={
                        "released_shares": "positive_is_more_shares_released",
                        "tradable_shares": "positive_is_more_tradable_shares",
                        "total_share_ratio": ("positive_is_larger_share_base_fraction"),
                    },
                    dimensions={
                        "lockup_type": lockup_type,
                        "event_phase": "history"
                        if observed_on < as_of
                        else "upcoming_90_days",
                    },
                    locator_uri=fetched.locator_uri,
                    limitations=("availability_time_unknown",),
                )
            )
        return observations, diagnostics

    def _fetch_report(
        self,
        report_name: str,
        *,
        filter_value: str,
        sort_columns: str,
        sort_types: str,
        page_size: int,
    ) -> tuple[list[_FetchedRow], tuple[RequestGateDiagnostic, ...]]:
        rows: list[_FetchedRow] = []
        diagnostics: list[RequestGateDiagnostic] = []
        expected_pages: int | None = None
        expected_count: int | None = None
        for page in range(1, self._max_pages + 1):
            url = _report_url(
                report_name,
                filter_value=filter_value,
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                page_size=page_size,
            )
            try:
                response, gate_diagnostics = self._request_gate.run(
                    partial(self._get, url)
                )
            except RequestGateError as gate_error:
                cause = gate_error.cause
                if not isinstance(cause, _SourceError):
                    raise
                raise _SourceError(
                    cause.code,
                    str(cause),
                    diagnostics=tuple(diagnostics) + gate_error.diagnostics,
                ) from gate_error
            except _SourceError as error:
                raise _SourceError(
                    error.code,
                    str(error),
                    diagnostics=tuple(diagnostics),
                ) from error
            diagnostics.extend(gate_diagnostics)
            try:
                data, pages, count = _decode_page(response)
            except _SourceError as error:
                raise _SourceError(
                    error.code,
                    str(error),
                    diagnostics=tuple(diagnostics),
                ) from error
            if expected_pages is None:
                expected_pages, expected_count = pages, count
            elif pages != expected_pages or count != expected_count:
                raise _SourceError(
                    "pagination_schema_changed",
                    "The source pagination metadata changed between pages.",
                    diagnostics=tuple(diagnostics),
                )
            rows.extend(_FetchedRow(item, response.retrieved_at, url) for item in data)
            if page >= pages:
                assert expected_count is not None
                if len(rows) != expected_count:
                    raise _SourceError(
                        "pagination_incomplete",
                        "The source pagination did not yield its declared row count.",
                        diagnostics=tuple(diagnostics),
                    )
                if not rows:
                    raise _SourceError(
                        "empty_response",
                        "The source returned no trading-event rows.",
                        diagnostics=tuple(diagnostics),
                    )
                return rows, tuple(diagnostics)
        raise _SourceError(
            "pagination_incomplete",
            "The bounded pagination did not cover the source result.",
            diagnostics=tuple(diagnostics),
        )

    def _get(self, url: str) -> HttpResponse:
        try:
            response = self._transport.get(
                url,
                {
                    "Accept": "application/json",
                    "Referer": "https://data.eastmoney.com/",
                    "User-Agent": USER_AGENT,
                },
            )
        except TransportError as error:
            raise _SourceError(error.code, str(error)) from error
        if response.status == 429:
            raise _SourceError("rate_limited", "The source rate limit was reached.")
        if response.status != 200:
            raise _SourceError(
                "upstream_http_error",
                f"The source returned HTTP status {response.status}.",
            )
        return response


def _report_url(
    report_name: str,
    *,
    filter_value: str,
    sort_columns: str,
    sort_types: str,
    page: int,
    page_size: int,
) -> str:
    query = urlencode(
        {
            "reportName": report_name,
            "columns": "ALL",
            "filter": filter_value,
            "pageNumber": str(page),
            "pageSize": str(page_size),
            "sortColumns": sort_columns,
            "sortTypes": sort_types,
            "source": "WEB",
            "client": "WEB",
        }
    )
    return f"{DATACENTER_URL}?{query}"


def _decode_page(response: HttpResponse) -> tuple[list[dict[str, Any]], int, int]:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "text/plain"}:
        raise _SourceError(
            "unexpected_content_type", "The source response is not JSON."
        )
    try:
        payload = json.loads(response.body, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _SourceError("unknown_schema", "The source JSON is invalid.") from error
    if (
        isinstance(payload, dict)
        and payload.get("success") is False
        and payload.get("code") == 9201
        and payload.get("result") is None
    ):
        raise _SourceError(
            "empty_response", "The source returned no rows for the requested window."
        )
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or payload.get("code") != 0
        or not isinstance(payload.get("result"), dict)
    ):
        raise _SourceError(
            "upstream_business_error", "The source business status failed."
        )
    result = payload["result"]
    pages = result.get("pages")
    count = result.get("count")
    data = result.get("data")
    if (
        isinstance(pages, bool)
        or not isinstance(pages, int)
        or pages < 1
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or not isinstance(data, list)
        or any(not isinstance(item, dict) for item in data)
    ):
        raise _SourceError("unknown_schema", "The source response schema is unknown.")
    return data, pages, count


def _historical_window(query: CapitalQuery) -> tuple[str, str]:
    as_of = _strict_date(query.as_of, "research date")
    observed_from = _strict_date(query.observed_from, "window start")
    observed_to = _strict_date(query.observed_to, "window end")
    if observed_from > observed_to:
        raise _SourceError("invalid_window", "The observation window is reversed.")
    if observed_to > as_of:
        raise _SourceError(
            "future_window",
            "The historical observation window exceeds the research date.",
        )
    if isinstance(query.limit, bool) or not 1 <= query.limit <= 500:
        raise _SourceError("invalid_limit", "The source limit must be from 1 to 500.")
    return observed_from.isoformat(), observed_to.isoformat()


def _lockup_window(query: CapitalQuery) -> tuple[str, str, str]:
    as_of = _strict_date(query.as_of, "research date")
    observed_from = _strict_date(query.observed_from, "window start")
    observed_to = _strict_date(query.observed_to, "window end")
    if observed_from > observed_to:
        raise _SourceError(
            "invalid_window",
            "The lockup observation window is reversed.",
        )
    if observed_to > as_of + timedelta(days=90):
        raise _SourceError(
            "future_window",
            "The lockup window cannot exceed ninety days after the research date.",
        )
    if isinstance(query.limit, bool) or not 1 <= query.limit <= 500:
        raise _SourceError("invalid_limit", "The source limit must be from 1 to 500.")
    return observed_from.isoformat(), observed_to.isoformat(), as_of.isoformat()


def _strict_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise _SourceError("invalid_date", f"The {field} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _SourceError(
            "invalid_date", f"The {field} must use YYYY-MM-DD."
        ) from error
    if parsed.isoformat() != value:
        raise _SourceError("invalid_date", f"The {field} must use YYYY-MM-DD.")
    return parsed


def _canonical_subject(query: CapitalQuery) -> tuple[str, str]:
    subject = query.subject
    if not isinstance(subject, dict) or not isinstance(subject.get("security"), dict):
        raise _SourceError(
            "invalid_subject", "The source requires one canonical A-share subject."
        )
    security = subject["security"]
    exchange = security.get("exchange")
    code = security.get("code")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isdigit()
        or security.get("type") != "A_SHARE"
        or (exchange == "SSE" and not code.startswith("6"))
        or (exchange == "SZSE" and not code.startswith(("0", "3")))
    ):
        raise _SourceError(
            "invalid_subject", "The source supports canonical SSE and SZSE A-shares."
        )
    return exchange, code


def _require_security(row: dict[str, Any], code: str) -> None:
    if row.get("SECURITY_CODE") != code:
        raise _SourceError(
            "wrong_security_payload", "A source row belongs to another security."
        )


def _market_provider_security(row: dict[str, Any]) -> tuple[str, str]:
    code = row.get("SECURITY_CODE")
    name = _required_text(row.get("SECURITY_NAME_ABBR"), "security name")
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        raise _SourceError(
            "wrong_security_payload", "A market row has an invalid security code."
        )
    return code, name


def _row_date(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) < 10:
        raise _SourceError("invalid_date", f"The source {field} is invalid.")
    date_value = value[:10]
    parsed = _strict_date(date_value, f"source {field}")
    if len(value) > 10:
        try:
            datetime.fromisoformat(value)
        except ValueError as error:
            raise _SourceError(
                "invalid_date", f"The source {field} is invalid."
            ) from error
    return parsed.isoformat()


def _require_in_window(value: str, observed_from: str, observed_to: str) -> None:
    if not observed_from <= value <= observed_to:
        raise _SourceError(
            "wrong_date_payload", "A source row falls outside the requested window."
        )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _SourceError("unknown_schema", f"The source {field} is missing.")
    return value.strip()


def _required_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise _SourceError("unknown_schema", f"The source {field} is missing.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise _SourceError(
            "unknown_schema", f"The source {field} is invalid."
        ) from error
    if not parsed.is_finite():
        raise _SourceError("unknown_schema", f"The source {field} is invalid.")
    return parsed


def _optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    return _required_decimal(value, field)


def _required_nonnegative_decimal(value: object, field: str) -> Decimal:
    parsed = _required_decimal(value, field)
    if parsed < 0:
        raise _SourceError("unknown_schema", f"The source {field} is negative.")
    return parsed


def _required_positive_decimal(value: object, field: str) -> Decimal:
    parsed = _required_decimal(value, field)
    if parsed <= 0:
        raise _SourceError("unknown_schema", f"The source {field} is not positive.")
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_seats(
    rows: list[_FetchedRow], *, side: str, code: str, trade_date: str
) -> list[dict[str, Any]]:
    normalized: list[tuple[Decimal, dict[str, Any]]] = []
    sort_field = "BUY" if side == "buy" else "SELL"
    for fetched in rows:
        row = fetched.value
        _require_security(row, code)
        if _row_date(row.get("TRADE_DATE"), "seat trade date") != trade_date:
            raise _SourceError(
                "wrong_date_payload",
                "A seat row belongs to another dragon-tiger date.",
            )
        name = _required_text(row.get("OPERATEDEPT_NAME"), "seat name")
        seat_code = _required_text(row.get("OPERATEDEPT_CODE"), "seat code")
        buy = _required_decimal(row.get("BUY"), "seat buy amount")
        sell = _required_decimal(row.get("SELL"), "seat sell amount")
        net = _required_decimal(row.get("NET"), "seat net amount")
        normalized.append(
            (
                buy if sort_field == "BUY" else sell,
                {
                    "name": name,
                    "buy_amount": _decimal_text(buy),
                    "sell_amount": _decimal_text(sell),
                    "net_amount": _decimal_text(net),
                    "institution": seat_code == "0",
                },
            )
        )
    normalized.sort(key=lambda item: item[0], reverse=True)
    return [
        {"rank": index, **item} for index, (_value, item) in enumerate(normalized, 1)
    ]
