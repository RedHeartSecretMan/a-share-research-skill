"""Fail-closed capital-flow source operations with explicit metric semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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

USER_AGENT = "Mozilla/5.0 (compatible; a-share-research-skill/0.1)"
NORTHBOUND_NET_BUY_DISCLOSURE_CUTOFF = date(2024, 8, 19)
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class _FetchedJson:
    payload: dict[str, Any]
    retrieved_at: datetime
    degradations: tuple[CapitalSourceFailure, ...]


class _CapitalFlowError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        degradations: tuple[CapitalSourceFailure, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.degradations = degradations


class EastmoneyNorthboundFlowOperation:
    """Collect historical northbound net-buy observations before the disclosure cut."""

    operation_id = "eastmoney_northbound_flow@1"
    supported_data_types = frozenset({"northbound_flow"})
    endpoint = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    def __init__(
        self,
        transport: CapitalHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        if "northbound_flow" not in query.data_types:
            return CapitalSourceBatch(operation_id=self.operation_id)
        try:
            observed_from, observed_to = _validated_dates(query)
            if query.subject is not None:
                raise _CapitalFlowError(
                    "invalid_subject",
                    "Northbound flow is a market-wide observation and takes no security subject.",
                )
            if observed_to >= NORTHBOUND_NET_BUY_DISCLOSURE_CUTOFF:
                raise _CapitalFlowError(
                    "northbound_net_buy_disclosure_unavailable",
                    "Daily northbound net-buy amounts are unavailable at or after the disclosure boundary.",
                )
            url = _url(
                self.endpoint,
                {
                    "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                    "columns": "TRADE_DATE,MUTUAL_TYPE,NET_DEAL_AMT",
                    "filter": (
                        f"(TRADE_DATE>='{observed_from.isoformat()}')"
                        f"(TRADE_DATE<='{observed_to.isoformat()}')"
                    ),
                    "pageNumber": "1",
                    "pageSize": str(query.limit),
                    "sortColumns": "TRADE_DATE",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            fetched = _get_json(
                self.operation_id,
                self._transport,
                self._request_gate,
                url,
                referer="https://data.eastmoney.com/",
            )
            rows = _datacenter_rows(fetched.payload)
            if not rows:
                raise _CapitalFlowError(
                    "empty_response", "The northbound source returned no observations."
                )
            observations = tuple(
                _northbound_observation(
                    row,
                    observed_from=observed_from,
                    observed_to=observed_to,
                    retrieved_at=fetched.retrieved_at,
                    locator_uri=url,
                )
                for row in rows[: query.limit]
            )
            return CapitalSourceBatch(
                operation_id=self.operation_id,
                observations=observations,
                degradations=fetched.degradations,
                limitations=("availability_time_unknown",),
                complete=True,
            )
        except _CapitalFlowError as error:
            return _failed(self.operation_id, error)


class EastmoneyStockFundFlowOperation:
    """Collect five or ten daily stock fund-flow observations."""

    operation_id = "eastmoney_stock_fund_flow@1"
    supported_data_types = frozenset({"stock_fund_flow"})
    endpoint = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

    def __init__(
        self,
        transport: CapitalHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        if "stock_fund_flow" not in query.data_types:
            return CapitalSourceBatch(operation_id=self.operation_id)
        try:
            observed_from, observed_to = _validated_dates(query)
            exchange, code = _canonical_a_share(query.subject)
            period = query.parameters.get("period")
            if period not in {"5d", "10d"}:
                raise _CapitalFlowError(
                    "invalid_query", "Stock fund flow supports only 5d or 10d periods."
                )
            trading_days = int(period[:-1])
            if query.limit < trading_days:
                raise _CapitalFlowError(
                    "invalid_query",
                    "The observation limit is smaller than the requested trading-day period.",
                )
            market = 1 if exchange == "SSE" else 0
            url = _url(
                self.endpoint,
                {
                    "secid": f"{market}.{code}",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "klt": "101",
                    "lmt": str(trading_days),
                },
            )
            fetched = _get_json(
                self.operation_id,
                self._transport,
                self._request_gate,
                url,
                referer="https://quote.eastmoney.com/",
            )
            data = fetched.payload.get("data")
            if not isinstance(data, dict):
                raise _CapitalFlowError(
                    "unknown_schema", "The stock fund-flow response has no data object."
                )
            if data.get("code") != code or data.get("market") != market:
                raise _CapitalFlowError(
                    "identity_mismatch",
                    "The stock fund-flow response does not match the canonical subject.",
                )
            rows = data.get("klines")
            if not isinstance(rows, list) or any(
                not isinstance(row, str) for row in rows
            ):
                raise _CapitalFlowError(
                    "unknown_schema", "The stock fund-flow rows have an unknown schema."
                )
            if not rows:
                raise _CapitalFlowError(
                    "empty_response", "The stock fund-flow source returned no rows."
                )
            if len(rows) != trading_days:
                raise _CapitalFlowError(
                    "insufficient_trading_days",
                    "The source did not return the requested trading-day period.",
                )
            parsed = tuple(_stock_row(row) for row in rows)
            dates = tuple(item[0] for item in parsed)
            if (
                dates != tuple(sorted(set(dates)))
                or dates[0] < observed_from
                or dates[-1] > observed_to
            ):
                raise _CapitalFlowError(
                    "date_mismatch",
                    "Stock fund-flow dates do not align with the requested observation window.",
                )
            period_value: dict[str, str | None] = {
                "start": dates[0].isoformat(),
                "end": dates[-1].isoformat(),
                "frequency": "trading_day",
                "trading_days": str(trading_days),
            }
            observations = tuple(
                _stock_observation(
                    values,
                    subject=query.subject,
                    period=period_value,
                    retrieved_at=fetched.retrieved_at,
                    locator_uri=url,
                )
                for values in reversed(parsed)
            )
            return CapitalSourceBatch(
                operation_id=self.operation_id,
                observations=observations,
                degradations=fetched.degradations,
                limitations=("availability_time_unknown",),
                complete=True,
            )
        except _CapitalFlowError as error:
            return _failed(self.operation_id, error)


class EastmoneyBoardFundFlowOperation:
    """Collect bounded industry, concept, or region fund-flow rankings."""

    operation_id = "eastmoney_board_fund_flow@1"
    supported_data_types = frozenset({"board_fund_flow"})
    endpoint = "https://push2.eastmoney.com/api/qt/clist/get"
    _board_filters = {
        "industry": "m:90+t:2",
        "concept": "m:90+t:3",
        "region": "m:90+t:1",
    }
    _period_fields = {
        "today": ("f62", "f184", "f3", "f204"),
        "5d": ("f164", "f165", "f109", "f257"),
        "10d": ("f174", "f175", "f160", None),
    }

    def __init__(
        self,
        transport: CapitalHttpTransport,
        *,
        page_size: int = 200,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be from 1 to 200")
        self._transport = transport
        self._page_size = page_size
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        if "board_fund_flow" not in query.data_types:
            return CapitalSourceBatch(operation_id=self.operation_id)
        try:
            observed_from, observed_to = _validated_dates(query)
            if query.subject is not None:
                raise _CapitalFlowError(
                    "invalid_subject",
                    "Board fund flow is a market-wide observation and takes no security subject.",
                )
            board_type = query.parameters.get("board_type")
            period_name = query.parameters.get("period")
            if (
                board_type not in self._board_filters
                or period_name not in self._period_fields
            ):
                raise _CapitalFlowError(
                    "invalid_query",
                    "Board fund flow requires industry/concept/region and today/5d/10d.",
                )
            if period_name == "today" and observed_from != observed_to:
                raise _CapitalFlowError(
                    "date_mismatch",
                    "A today board-flow request must use one observation date.",
                )
            main_field, pct_field, change_field, leader_field = self._period_fields[
                period_name
            ]
            fields = ["f12", "f14", "f124", change_field, main_field, pct_field]
            if leader_field is not None:
                fields.append(leader_field)
            if period_name == "today":
                fields.extend(("f66", "f72", "f78", "f84"))

            observations: list[CapitalObservation] = []
            degradations: list[CapitalSourceFailure] = []
            provider_total: int | None = None
            page = 1
            while len(observations) < query.limit:
                url = _url(
                    self.endpoint,
                    {
                        "pn": str(page),
                        "pz": str(self._page_size),
                        "po": "1",
                        "np": "1",
                        "fltt": "2",
                        "invt": "2",
                        "fid": main_field,
                        "fs": self._board_filters[board_type],
                        "fields": ",".join(dict.fromkeys(fields)),
                    },
                )
                fetched = _get_json(
                    self.operation_id,
                    self._transport,
                    self._request_gate,
                    url,
                    referer="https://data.eastmoney.com/",
                )
                degradations.extend(fetched.degradations)
                data = fetched.payload.get("data")
                if not isinstance(data, dict):
                    raise _CapitalFlowError(
                        "unknown_schema",
                        "The board fund-flow response has no data object.",
                        degradations=tuple(degradations),
                    )
                total = data.get("total")
                rows = data.get("diff")
                if (
                    isinstance(total, bool)
                    or not isinstance(total, int)
                    or total < 0
                    or not isinstance(rows, list)
                    or any(not isinstance(row, dict) for row in rows)
                ):
                    raise _CapitalFlowError(
                        "unknown_schema",
                        "The board fund-flow pagination schema is invalid.",
                        degradations=tuple(degradations),
                    )
                if provider_total is None:
                    provider_total = total
                elif provider_total != total:
                    raise _CapitalFlowError(
                        "unknown_schema",
                        "The board fund-flow total changed during pagination.",
                        degradations=tuple(degradations),
                    )
                if not rows:
                    if page == 1:
                        raise _CapitalFlowError(
                            "empty_response",
                            "The board fund-flow source returned no rows.",
                            degradations=tuple(degradations),
                        )
                    break
                provider_days = {_unix_day(row.get("f124")) for row in rows}
                if len(provider_days) != 1:
                    raise _CapitalFlowError(
                        "date_mismatch",
                        "One board-flow page contains conflicting provider trading dates.",
                        degradations=tuple(degradations),
                    )
                provider_day = next(iter(provider_days))
                if observed_to != provider_day:
                    raise _CapitalFlowError(
                        "date_mismatch",
                        "The requested board-flow date does not match the provider trading date.",
                        degradations=tuple(degradations),
                    )
                for row in rows:
                    observations.append(
                        _board_observation(
                            row,
                            board_type=board_type,
                            period_name=period_name,
                            period_fields=self._period_fields[period_name],
                            period=_board_period(provider_day, period_name),
                            rank=len(observations) + 1,
                            provider_total=total,
                            retrieved_at=fetched.retrieved_at,
                            locator_uri=url,
                        )
                    )
                    if len(observations) >= query.limit:
                        break
                if len(observations) >= query.limit or len(observations) >= total:
                    break
                page += 1
            complete = provider_total is not None and len(observations) >= min(
                query.limit, provider_total
            )
            errors: tuple[CapitalSourceFailure, ...] = ()
            if not complete:
                errors = (
                    CapitalSourceFailure(
                        self.operation_id,
                        "pagination_incomplete",
                        "The bounded board fund-flow pagination ended before the requested limit.",
                    ),
                )
            return CapitalSourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations),
                source_errors=errors,
                degradations=tuple(degradations),
                limitations=(
                    (
                        "availability_time_unknown",
                        "period_start_not_exposed",
                        "trading_day_alignment_unverified",
                        "session_completeness_unverified",
                    )
                    if period_name != "today"
                    else (
                        "availability_time_unknown",
                        "session_completeness_unverified",
                    )
                ),
                complete=complete,
            )
        except _CapitalFlowError as error:
            return _failed(self.operation_id, error)


def _get_json(
    operation_id: str,
    transport: CapitalHttpTransport,
    request_gate: RequestGate,
    url: str,
    *,
    referer: str,
) -> _FetchedJson:
    try:
        response, diagnostics = request_gate.run(
            partial(
                transport.get,
                url,
                {"User-Agent": USER_AGENT, "Referer": referer},
            )
        )
    except RequestGateError as gate_error:
        terminal_error = gate_error.cause
        if not isinstance(terminal_error, TransportError):
            raise
        raise _CapitalFlowError(
            terminal_error.code,
            str(terminal_error),
            degradations=_gate_degradations(operation_id, gate_error.diagnostics),
        ) from gate_error
    except TransportError as error:
        raise _CapitalFlowError(error.code, str(error)) from error
    degradations = _gate_degradations(operation_id, diagnostics)
    payload = _json_object(response, degradations=degradations)
    return _FetchedJson(payload, response.retrieved_at, degradations)


def _json_object(
    response: HttpResponse,
    *,
    degradations: tuple[CapitalSourceFailure, ...],
) -> dict[str, Any]:
    if response.status != 200:
        raise _CapitalFlowError(
            "upstream_http_error",
            f"The capital-flow source returned HTTP status {response.status}.",
            degradations=degradations,
        )
    if not response.body.strip():
        raise _CapitalFlowError(
            "empty_response",
            "The capital-flow source returned an empty response body.",
            degradations=degradations,
        )
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "text/plain"} and not media_type.endswith(
        "+json"
    ):
        raise _CapitalFlowError(
            "unexpected_content_type",
            "The capital-flow response is not JSON.",
            degradations=degradations,
        )
    try:
        payload = json.loads(response.body, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _CapitalFlowError(
            "unknown_schema",
            "The capital-flow response has invalid JSON.",
            degradations=degradations,
        ) from error
    if not isinstance(payload, dict):
        raise _CapitalFlowError(
            "unknown_schema",
            "The capital-flow response is not an object.",
            degradations=degradations,
        )
    return payload


def _validated_dates(query: CapitalQuery) -> tuple[date, date]:
    try:
        as_of = date.fromisoformat(query.as_of)
        observed_from = date.fromisoformat(query.observed_from)
        observed_to = date.fromisoformat(query.observed_to)
    except ValueError as error:
        raise _CapitalFlowError(
            "invalid_query", "Capital-flow dates must use exact YYYY-MM-DD values."
        ) from error
    if (
        as_of.isoformat() != query.as_of
        or observed_from.isoformat() != query.observed_from
        or observed_to.isoformat() != query.observed_to
        or observed_from > observed_to
        or observed_to > as_of
        or not 1 <= query.limit <= 500
    ):
        raise _CapitalFlowError(
            "invalid_query", "The capital-flow date window or limit is invalid."
        )
    return observed_from, observed_to


def _canonical_a_share(subject: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(subject, dict) or not isinstance(subject.get("security"), dict):
        raise _CapitalFlowError(
            "invalid_subject", "Stock fund flow requires one canonical A-share subject."
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
    ):
        raise _CapitalFlowError(
            "invalid_subject", "Stock fund flow requires one canonical A-share subject."
        )
    sse_prefixes = ("600", "601", "603", "605", "688", "689")
    szse_prefixes = ("000", "001", "002", "003", "300", "301")
    if (exchange == "SSE" and not code.startswith(sse_prefixes)) or (
        exchange == "SZSE" and not code.startswith(szse_prefixes)
    ):
        raise _CapitalFlowError(
            "invalid_subject",
            "The security code conflicts with its canonical exchange.",
        )
    return exchange, code


def _datacenter_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if payload.get("success") is not True:
        raise _CapitalFlowError(
            "provider_error", "The northbound source reported an unsuccessful status."
        )
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise _CapitalFlowError(
            "unknown_schema", "The northbound response has an unknown schema."
        )
    rows = result["data"]
    if any(not isinstance(row, dict) for row in rows):
        raise _CapitalFlowError(
            "unknown_schema", "A northbound response row has an unknown schema."
        )
    return tuple(rows)


def _northbound_observation(
    row: dict[str, Any],
    *,
    observed_from: date,
    observed_to: date,
    retrieved_at: datetime,
    locator_uri: str,
) -> CapitalObservation:
    observed_on = _provider_day(row.get("TRADE_DATE"))
    if not observed_from <= observed_on <= observed_to:
        raise _CapitalFlowError(
            "date_mismatch",
            "A northbound observation falls outside the requested window.",
        )
    mutual_type = row.get("MUTUAL_TYPE")
    channel = (
        {"001": "shanghai_connect", "003": "shenzhen_connect"}.get(mutual_type)
        if isinstance(mutual_type, str)
        else None
    )
    if channel is None:
        raise _CapitalFlowError(
            "unknown_schema", "A northbound channel identifier is unknown."
        )
    net_buy = _decimal_text(row.get("NET_DEAL_AMT"))
    return CapitalObservation(
        data_type="northbound_flow",
        source_operation=EastmoneyNorthboundFlowOperation.operation_id,
        source_role="market_observation",
        subject=None,
        observed_on=observed_on.isoformat(),
        available_at=None,
        retrieved_at=retrieved_at,
        period={
            "start": observed_on.isoformat(),
            "end": observed_on.isoformat(),
            "frequency": "trading_day",
        },
        metrics={"net_buy_amount": net_buy},
        units={"net_buy_amount": "CNY_100_MILLION"},
        directions={"net_buy_amount": "positive_is_net_inflow"},
        dimensions={
            "connect_channel": channel,
            "market_scope": "mainland_hong_kong_stock_connect_northbound",
        },
        locator_uri=locator_uri,
        limitations=("availability_time_unknown",),
    )


def _stock_row(row: str) -> tuple[date, str, str, str, str, str]:
    parts = row.split(",")
    if len(parts) < 6:
        raise _CapitalFlowError(
            "unknown_schema", "A stock fund-flow row has too few fields."
        )
    observed_on = _provider_day(parts[0])
    return (
        observed_on,
        _decimal_text(parts[1]),
        _decimal_text(parts[2]),
        _decimal_text(parts[3]),
        _decimal_text(parts[4]),
        _decimal_text(parts[5]),
    )


def _stock_observation(
    values: tuple[date, str, str, str, str, str],
    *,
    subject: dict[str, Any] | None,
    period: dict[str, str | None],
    retrieved_at: datetime,
    locator_uri: str,
) -> CapitalObservation:
    observed_on, main, small, medium, large, super_large = values
    metrics: dict[str, str | None] = {
        "main_net_inflow": main,
        "small_order_net_inflow": small,
        "medium_order_net_inflow": medium,
        "large_order_net_inflow": large,
        "super_large_order_net_inflow": super_large,
    }
    return CapitalObservation(
        data_type="stock_fund_flow",
        source_operation=EastmoneyStockFundFlowOperation.operation_id,
        source_role="market_signal",
        subject=subject,
        observed_on=observed_on.isoformat(),
        available_at=None,
        retrieved_at=retrieved_at,
        period=period,
        metrics=metrics,
        units={name: "CNY" for name in metrics},
        directions={name: "positive_is_net_inflow" for name in metrics},
        dimensions={"frequency": "trading_day"},
        locator_uri=locator_uri,
        limitations=("availability_time_unknown",),
    )


def _board_period(snapshot_day: date, period_name: str) -> dict[str, str | None]:
    if period_name == "today":
        return {
            "start": snapshot_day.isoformat(),
            "end": snapshot_day.isoformat(),
            "frequency": "trading_day_snapshot",
        }
    lookback = period_name[:-1]
    return {
        "start": None,
        "end": snapshot_day.isoformat(),
        "frequency": f"rolling_{lookback}_trading_days",
        "lookback_trading_days": lookback,
    }


def _board_observation(
    row: dict[str, Any],
    *,
    board_type: str,
    period_name: str,
    period_fields: tuple[str, str, str, str | None],
    period: dict[str, str | None],
    rank: int,
    provider_total: int,
    retrieved_at: datetime,
    locator_uri: str,
) -> CapitalObservation:
    code = row.get("f12")
    name = row.get("f14")
    if not isinstance(code, str) or not code or not isinstance(name, str) or not name:
        raise _CapitalFlowError(
            "unknown_schema", "A board fund-flow row has no stable identity."
        )
    main_field, pct_field, change_field, leader_field = period_fields
    metrics: dict[str, str | None] = {
        "main_net_inflow": _decimal_text(row.get(main_field)),
        "main_net_inflow_ratio": _decimal_text(row.get(pct_field)),
        "price_change_ratio": _decimal_text(row.get(change_field)),
    }
    units = {
        "main_net_inflow": "CNY",
        "main_net_inflow_ratio": "PERCENT",
        "price_change_ratio": "PERCENT",
    }
    directions = {
        "main_net_inflow": "positive_is_net_inflow",
        "main_net_inflow_ratio": "positive_is_net_inflow",
        "price_change_ratio": "positive_is_gain",
    }
    if period_name == "today":
        for metric, field in (
            ("super_large_order_net_inflow", "f66"),
            ("large_order_net_inflow", "f72"),
            ("medium_order_net_inflow", "f78"),
            ("small_order_net_inflow", "f84"),
        ):
            value = row.get(field)
            metrics[metric] = None if value is None else _decimal_text(value)
            units[metric] = "CNY"
            directions[metric] = "positive_is_net_inflow"
    leader = row.get(leader_field) if leader_field is not None else None
    if leader is not None and not isinstance(leader, str):
        raise _CapitalFlowError(
            "unknown_schema", "A board fund-flow leader field is invalid."
        )
    provider_day = _unix_day(row.get("f124"))
    if provider_day.isoformat() != period["end"]:
        raise _CapitalFlowError(
            "date_mismatch",
            "The board fund-flow snapshot date does not match the requested boundary.",
        )
    return CapitalObservation(
        data_type="board_fund_flow",
        source_operation=EastmoneyBoardFundFlowOperation.operation_id,
        source_role="market_signal",
        subject=None,
        observed_on=provider_day.isoformat(),
        available_at=None,
        retrieved_at=retrieved_at,
        period=period,
        metrics=metrics,
        units=units,
        directions=directions,
        dimensions={
            "board_type": board_type,
            "market_scope": "a_share_board_market",
            "board_code": code,
            "board_name": name,
            "rank": rank,
            "provider_total": provider_total,
            "leader": leader,
        },
        locator_uri=locator_uri,
        limitations=(
            (
                "availability_time_unknown",
                "period_start_not_exposed",
                "trading_day_alignment_unverified",
                "session_completeness_unverified",
                *(
                    ("source_value_missing",)
                    if any(value is None for value in metrics.values())
                    else ()
                ),
            )
            if period_name != "today"
            else (
                "availability_time_unknown",
                "session_completeness_unverified",
                *(
                    ("source_value_missing",)
                    if any(value is None for value in metrics.values())
                    else ()
                ),
            )
        ),
    )


def _provider_day(value: object) -> date:
    if not isinstance(value, str):
        raise _CapitalFlowError(
            "unknown_schema", "A capital-flow observation date is missing."
        )
    day_text = value.split(" ", 1)[0]
    try:
        parsed = date.fromisoformat(day_text)
    except ValueError as error:
        raise _CapitalFlowError(
            "unknown_schema", "A capital-flow observation date is invalid."
        ) from error
    if parsed.isoformat() != day_text:
        raise _CapitalFlowError(
            "unknown_schema", "A capital-flow observation date is invalid."
        )
    return parsed


def _unix_day(value: object) -> date:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise _CapitalFlowError(
            "unknown_schema", "A capital-flow snapshot timestamp is invalid."
        )
    try:
        return (
            datetime.fromtimestamp(value, timezone.utc)
            .astimezone(CHINA_STANDARD_TIME)
            .date()
        )
    except (OverflowError, OSError, ValueError) as error:
        raise _CapitalFlowError(
            "unknown_schema", "A capital-flow snapshot timestamp is invalid."
        ) from error


def _decimal_text(value: object) -> str:
    if (
        isinstance(value, bool)
        or value is None
        or not isinstance(value, (str, int, float, Decimal))
    ):
        raise _CapitalFlowError(
            "unknown_schema", "A required capital-flow metric is missing or invalid."
        )
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise _CapitalFlowError(
            "unknown_schema", "A required capital-flow metric is invalid."
        ) from error
    if not parsed.is_finite():
        raise _CapitalFlowError(
            "unknown_schema", "A required capital-flow metric is invalid."
        )
    return format(parsed, "f")


def _gate_degradations(
    operation_id: str,
    diagnostics: tuple[RequestGateDiagnostic, ...],
) -> tuple[CapitalSourceFailure, ...]:
    return tuple(
        CapitalSourceFailure(
            operation_id,
            diagnostic.code,
            diagnostic.message,
            diagnostic.details(),
        )
        for diagnostic in diagnostics
    )


def _failed(operation_id: str, error: _CapitalFlowError) -> CapitalSourceBatch:
    return CapitalSourceBatch(
        operation_id=operation_id,
        source_errors=(CapitalSourceFailure(operation_id, error.code, str(error)),),
        degradations=error.degradations,
        complete=False,
    )


def _url(endpoint: str, parameters: dict[str, str]) -> str:
    return f"{endpoint}?{urlencode(parameters)}"
