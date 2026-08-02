"""Eastmoney observations for company financing and capital-distribution events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
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
USER_AGENT = "Mozilla/5.0 (compatible; a-share-research-skill/0.1)"
_SOURCE_LIMITATIONS = (
    "availability_time_unknown",
    "observation_time_precision_is_date_only",
)
_DATE_OR_MIDNIGHT = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]00:00:00(?:\.0+)?)?\Z")


class _OperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Page:
    rows: tuple[dict[str, Any], ...]
    pages: int
    count: int


def _failure(operation_id: str, error: _OperationError) -> CapitalSourceFailure:
    return CapitalSourceFailure(operation_id, error.code, str(error))


def _gate_diagnostic(
    operation_id: str,
    diagnostic: RequestGateDiagnostic,
) -> CapitalSourceFailure:
    return CapitalSourceFailure(
        source_operation=operation_id,
        code=diagnostic.code,
        message=diagnostic.message,
        details=diagnostic.details(),
    )


def _canonical_subject(query: CapitalQuery) -> tuple[str, str, dict[str, Any]]:
    subject = query.subject
    if not isinstance(subject, dict) or not isinstance(subject.get("security"), dict):
        raise _OperationError(
            "invalid_subject",
            "Company capital data requires one canonical A-share subject.",
        )
    security = subject["security"]
    exchange = security.get("exchange")
    code = security.get("code")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
        or security.get("type") != "A_SHARE"
    ):
        raise _OperationError(
            "invalid_subject",
            "Company capital data requires a canonical SSE or SZSE A-share.",
        )
    return exchange, code, subject


def _strict_date(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DATE_OR_MIDNIGHT.fullmatch(value):
        raise _OperationError("unknown_schema", f"The {field} date is invalid.")
    normalized = value[:10]
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as error:
        raise _OperationError(
            "unknown_schema", f"The {field} date is invalid."
        ) from error
    if parsed.isoformat() != normalized:
        raise _OperationError("unknown_schema", f"The {field} date is invalid.")
    return normalized


def _optional_date(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _strict_date(value, field)


def _query_window(query: CapitalQuery) -> None:
    observed_from = _request_date(query.observed_from, "observed_from")
    observed_to = _request_date(query.observed_to, "observed_to")
    as_of = _request_date(query.as_of, "as_of")
    if observed_from > observed_to or observed_to > as_of:
        raise _OperationError(
            "invalid_parameters",
            "The capital-data date window is inconsistent with as_of.",
        )
    if isinstance(query.limit, bool) or not 1 <= query.limit <= 100:
        raise _OperationError(
            "invalid_parameters", "The capital-data limit must be from 1 to 100."
        )


def _request_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _OperationError(
            "invalid_parameters", f"Capital query {field} must use YYYY-MM-DD."
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _OperationError(
            "invalid_parameters", f"Capital query {field} must use YYYY-MM-DD."
        ) from error
    if parsed.isoformat() != value:
        raise _OperationError(
            "invalid_parameters", f"Capital query {field} must use YYYY-MM-DD."
        )
    return value


def _numeric(value: object, field: str) -> str:
    if isinstance(value, bool) or value is None:
        raise _OperationError(
            "unknown_schema", f"The {field} numeric value is missing."
        )
    if isinstance(value, Decimal):
        number = value
        rendered = str(value)
    elif isinstance(value, (str, int)):
        rendered = str(value).strip()
        try:
            number = Decimal(rendered)
        except InvalidOperation as error:
            raise _OperationError(
                "unknown_schema", f"The {field} numeric value is invalid."
            ) from error
    else:
        raise _OperationError(
            "unknown_schema", f"The {field} numeric value is invalid."
        )
    if not rendered or not number.is_finite():
        raise _OperationError(
            "unknown_schema", f"The {field} numeric value is invalid."
        )
    return rendered


def _nullable_numeric(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _numeric(value, field)


def _metric_limitations(metrics: dict[str, str | None]) -> tuple[str, ...]:
    return (
        ("source_value_missing",)
        if any(value is None for value in metrics.values())
        else ()
    )


def _sorted_observations(
    observations: list[CapitalObservation],
) -> tuple[CapitalObservation, ...]:
    return tuple(sorted(observations, key=lambda item: item.observed_on, reverse=True))


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _OperationError("unknown_schema", f"The {field} value is missing.")
    return value.strip()


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise _OperationError("unknown_schema", f"The {field} value is invalid.")
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise _OperationError(
            "unknown_schema", f"The {field} value is invalid."
        ) from error
    if normalized < 0 or Decimal(str(value)) != normalized:
        raise _OperationError("unknown_schema", f"The {field} value is invalid.")
    return normalized


def _request_get(
    transport: CapitalHttpTransport,
    url: str,
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json",
                "Referer": "https://data.eastmoney.com/",
                "User-Agent": USER_AGENT,
            },
        )
    except TransportError as error:
        raise _OperationError(error.code, str(error)) from error
    if response.status == 429:
        raise _OperationError("rate_limited", "The source rate limit was reached.")
    if response.status != 200:
        raise _OperationError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _OperationError("empty_response", "The source returned an empty body.")
    return response


def _parse_page(response: HttpResponse) -> _Page:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "text/plain"} and not media_type.endswith(
        "+json"
    ):
        raise _OperationError(
            "unexpected_content_type", "The source response is not JSON."
        )
    try:
        payload = json.loads(
            response.body,
            parse_float=Decimal,
            parse_int=Decimal,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _OperationError(
            "unknown_schema", "The source JSON encoding or schema is invalid."
        ) from error
    if (
        isinstance(payload, dict)
        and payload.get("success") is False
        and payload.get("code") == 9201
        and payload.get("result") is None
    ):
        raise _OperationError(
            "empty_response", "The source returned no rows for the requested window."
        )
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise _OperationError(
            "upstream_business_error", "The source business status failed."
        )
    result = payload.get("result")
    if result is None:
        raise _OperationError("empty_response", "The source returned no result.")
    if not isinstance(result, dict):
        raise _OperationError("unknown_schema", "The source result schema is invalid.")
    pages = _integer(result.get("pages"), "pages")
    count = _integer(result.get("count"), "count")
    data = result.get("data")
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise _OperationError("unknown_schema", "The source data schema is invalid.")
    if not data:
        if count == 0:
            raise _OperationError("empty_response", "The source returned no rows.")
        raise _OperationError(
            "unknown_schema", "The source pagination returned an empty data page."
        )
    if pages < 1 or count < len(data):
        raise _OperationError(
            "unknown_schema", "The source pagination metadata is inconsistent."
        )
    return _Page(tuple(data), pages, count)


class _EastmoneyCompanyCapitalOperation:
    supported_data_types: frozenset[str]
    operation_id: str
    report_name: str
    identity_field: str
    date_field: str
    sort_column: str
    period_kind: str
    period_frequency: str
    requires_complete_pagination_for_fallback_dates = False

    def __init__(
        self,
        transport: CapitalHttpTransport,
        *,
        page_size: int = 20,
        max_pages: int = 25,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
            raise ValueError("page_size and max_pages must be from 1 to 100")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        try:
            exchange, code, subject = _canonical_subject(query)
            _query_window(query)
        except _OperationError as error:
            return CapitalSourceBatch(
                operation_id=self.operation_id,
                source_errors=(_failure(self.operation_id, error),),
                complete=False,
            )

        observations: list[CapitalObservation] = []
        degradations: list[CapitalSourceFailure] = []
        expected_pages: int | None = None
        previous_sort_date: str | None = None
        source_order_proven = True
        for page_number in range(1, self._max_pages + 1):
            locator = self._url(code, page_number)
            try:
                response, diagnostics = self._request_gate.run(
                    partial(_request_get, self._transport, locator)
                )
            except RequestGateError as error:
                degradations.extend(
                    _gate_diagnostic(self.operation_id, item)
                    for item in error.diagnostics
                )
                cause = error.cause
                source_error = (
                    cause
                    if isinstance(cause, _OperationError)
                    else _OperationError(
                        "upstream_unavailable", "The source request failed."
                    )
                )
                return self._failed(observations, degradations, source_error)
            except _OperationError as error:
                return self._failed(observations, degradations, error)
            degradations.extend(
                _gate_diagnostic(self.operation_id, item) for item in diagnostics
            )
            try:
                if response.retrieved_at.utcoffset() is None:
                    raise _OperationError(
                        "unknown_schema", "The source retrieval time has no timezone."
                    )
                page = _parse_page(response)
                if expected_pages is None:
                    expected_pages = page.pages
                elif page.pages != expected_pages:
                    raise _OperationError(
                        "unknown_schema", "The source pagination metadata changed."
                    )
                saw_older = False
                for row_index, row in enumerate(page.rows):
                    row_code = row.get(self.identity_field)
                    if row_code != code:
                        raise _OperationError(
                            "identity_mismatch",
                            "A source row does not match the requested security.",
                        )
                    observed_on = self._observed_on(row)
                    sort_date = _optional_date(
                        row.get(self.sort_column), self.sort_column
                    )
                    if sort_date is None:
                        source_order_proven = False
                        previous_sort_date = None
                    elif source_order_proven:
                        if (
                            previous_sort_date is not None
                            and sort_date > previous_sort_date
                        ):
                            raise _OperationError(
                                "source_order_mismatch",
                                "The source rows are not ordered by the requested descending sort date.",
                            )
                        previous_sort_date = sort_date
                    if observed_on < query.observed_from:
                        saw_older = True
                        continue
                    if observed_on > query.observed_to:
                        continue
                    observations.append(
                        self._observation(
                            row,
                            exchange=exchange,
                            code=code,
                            subject=subject,
                            observed_on=observed_on,
                            retrieved_at=response.retrieved_at,
                            locator=locator,
                        )
                    )
                    if (
                        len(observations) >= query.limit
                        and not self.requires_complete_pagination_for_fallback_dates
                    ):
                        coverage_proven = (
                            row_index == len(page.rows) - 1
                            and page_number >= page.pages
                        )
                        return CapitalSourceBatch(
                            operation_id=self.operation_id,
                            observations=_sorted_observations(observations),
                            degradations=tuple(degradations),
                            limitations=(
                                ()
                                if coverage_proven
                                else ("result_truncated_to_limit",)
                            ),
                            complete=coverage_proven,
                        )
            except _OperationError as error:
                return self._failed(observations, degradations, error)
            if (
                saw_older
                and source_order_proven
                and not self.requires_complete_pagination_for_fallback_dates
            ) or page_number >= page.pages:
                return self._completed(observations, degradations, limit=query.limit)
        ordered = _sorted_observations(observations)
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=ordered[: query.limit],
            source_errors=(
                CapitalSourceFailure(
                    self.operation_id,
                    "pagination_incomplete",
                    "The bounded pagination did not cover the requested date window.",
                ),
            ),
            degradations=tuple(degradations),
            limitations=(
                ("result_truncated_to_limit",) if len(ordered) > query.limit else ()
            ),
            complete=False,
        )

    def _completed(
        self,
        observations: list[CapitalObservation],
        degradations: list[CapitalSourceFailure],
        *,
        limit: int,
    ) -> CapitalSourceBatch:
        if not observations:
            return self._failed(
                observations,
                degradations,
                _OperationError(
                    "no_observations_in_window",
                    "The source returned rows but none were inside the requested window.",
                ),
            )
        ordered = _sorted_observations(observations)
        truncated = len(ordered) > limit
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=ordered[:limit],
            degradations=tuple(degradations),
            limitations=("result_truncated_to_limit",) if truncated else (),
            complete=not truncated,
        )

    def _failed(
        self,
        observations: list[CapitalObservation],
        degradations: list[CapitalSourceFailure],
        error: _OperationError,
    ) -> CapitalSourceBatch:
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            source_errors=(_failure(self.operation_id, error),),
            degradations=tuple(degradations),
            complete=False,
        )

    def _url(self, code: str, page: int) -> str:
        query = {
            "reportName": self.report_name,
            "columns": "ALL",
            "filter": f'({self.identity_field}="{code}")',
            "pageNumber": str(page),
            "pageSize": str(self._page_size),
            "sortColumns": self.sort_column,
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        return f"{DATACENTER_URL}?{urlencode(query)}"

    def _observed_on(self, row: dict[str, Any]) -> str:
        return _strict_date(row.get(self.date_field), self.date_field)

    def _observation(
        self,
        row: dict[str, Any],
        *,
        exchange: str,
        code: str,
        subject: dict[str, Any],
        observed_on: str,
        retrieved_at: datetime,
        locator: str,
    ) -> CapitalObservation:
        metrics, units, directions, dimensions, limitations = self._map(row)
        return CapitalObservation(
            data_type=next(iter(self.supported_data_types)),
            source_operation=self.operation_id,
            source_role="market_observation",
            subject=subject,
            observed_on=observed_on,
            available_at=None,
            retrieved_at=retrieved_at,
            period={
                "kind": self.period_kind,
                "start": observed_on,
                "end": observed_on,
                "frequency": self.period_frequency,
            },
            metrics=metrics,
            units=units,
            directions=directions,
            dimensions={
                "exchange": exchange,
                "security_code": code,
                **dimensions,
            },
            locator_uri=locator,
            limitations=(*_SOURCE_LIMITATIONS, *limitations),
        )

    def _map(
        self, row: dict[str, Any]
    ) -> tuple[
        dict[str, str | None],
        dict[str, str],
        dict[str, str],
        dict[str, Any],
        tuple[str, ...],
    ]:
        raise NotImplementedError


class EastmoneyMarginTradingOperation(_EastmoneyCompanyCapitalOperation):
    """Collect daily margin-financing and securities-lending balances."""

    operation_id = "eastmoney_margin_trading@1"
    supported_data_types = frozenset({"margin_trading"})
    report_name = "RPTA_WEB_RZRQ_GGMX"
    identity_field = "SCODE"
    date_field = "DATE"
    sort_column = "DATE"
    period_kind = "trading_day"
    period_frequency = "daily"

    def _map(
        self, row: dict[str, Any]
    ) -> tuple[
        dict[str, str | None],
        dict[str, str],
        dict[str, str],
        dict[str, Any],
        tuple[str, ...],
    ]:
        metrics: dict[str, str | None] = {
            "financing_balance": _nullable_numeric(row.get("RZYE"), "RZYE"),
            "financing_buy_amount": _nullable_numeric(row.get("RZMRE"), "RZMRE"),
            "financing_repayment_amount": _nullable_numeric(row.get("RZCHE"), "RZCHE"),
            "securities_lending_balance": _nullable_numeric(row.get("RQYE"), "RQYE"),
            "securities_lending_sell_volume": _nullable_numeric(
                row.get("RQMCL"), "RQMCL"
            ),
            "securities_lending_repayment_volume": _nullable_numeric(
                row.get("RQCHL"), "RQCHL"
            ),
            "margin_balance": _nullable_numeric(row.get("RZRQYE"), "RZRQYE"),
        }
        limitations = _metric_limitations(metrics)
        return (
            metrics,
            {
                "financing_balance": "CNY",
                "financing_buy_amount": "CNY",
                "financing_repayment_amount": "CNY",
                "securities_lending_balance": "CNY",
                "securities_lending_sell_volume": "share",
                "securities_lending_repayment_volume": "share",
                "margin_balance": "CNY",
            },
            {
                "financing_balance": "higher_is_more_financing_exposure",
                "financing_buy_amount": "inflow",
                "financing_repayment_amount": "outflow",
                "securities_lending_balance": "higher_is_more_short_exposure",
                "securities_lending_sell_volume": "short_opening",
                "securities_lending_repayment_volume": "short_covering",
                "margin_balance": "higher_is_more_leverage_exposure",
            },
            {},
            limitations,
        )


class EastmoneyShareholderCountOperation(_EastmoneyCompanyCapitalOperation):
    """Collect reporting-period shareholder counts and concentration measures."""

    operation_id = "eastmoney_shareholder_count@1"
    supported_data_types = frozenset({"shareholder_count"})
    report_name = "RPT_HOLDERNUMLATEST"
    identity_field = "SECURITY_CODE"
    date_field = "END_DATE"
    sort_column = "END_DATE"
    period_kind = "reporting_period_end"
    period_frequency = "quarterly"

    def _map(
        self, row: dict[str, Any]
    ) -> tuple[
        dict[str, str | None],
        dict[str, str],
        dict[str, str],
        dict[str, Any],
        tuple[str, ...],
    ]:
        metrics: dict[str, str | None] = {
            "shareholder_count": _nullable_numeric(row.get("HOLDER_NUM"), "HOLDER_NUM"),
            "shareholder_count_change": _nullable_numeric(
                row.get("HOLDER_NUM_CHANGE"), "HOLDER_NUM_CHANGE"
            ),
            "shareholder_count_change_ratio": _nullable_numeric(
                row.get("HOLDER_NUM_RATIO"), "HOLDER_NUM_RATIO"
            ),
            "average_shares_per_holder": _nullable_numeric(
                row.get("AVG_FREE_SHARES"), "AVG_FREE_SHARES"
            ),
        }
        return (
            metrics,
            {
                "shareholder_count": "account",
                "shareholder_count_change": "account",
                "shareholder_count_change_ratio": "percent",
                "average_shares_per_holder": "share_per_holder",
            },
            {
                "shareholder_count": "higher_is_more_distributed",
                "shareholder_count_change": "positive_is_increase",
                "shareholder_count_change_ratio": "positive_is_increase",
                "average_shares_per_holder": "higher_is_more_concentrated",
            },
            {
                "previous_period_end": _optional_date(
                    row.get("PRE_END_DATE"), "PRE_END_DATE"
                )
            },
            _metric_limitations(metrics),
        )


class EastmoneyDividendOperation(_EastmoneyCompanyCapitalOperation):
    """Collect cash-dividend, bonus-share, and transfer implementation events."""

    operation_id = "eastmoney_dividend@1"
    supported_data_types = frozenset({"dividend"})
    report_name = "RPT_SHAREBONUS_DET"
    identity_field = "SECURITY_CODE"
    date_field = "EX_DIVIDEND_DATE"
    sort_column = "EX_DIVIDEND_DATE"
    period_kind = "distribution_event"
    period_frequency = "event"
    requires_complete_pagination_for_fallback_dates = True

    def _observed_on(self, row: dict[str, Any]) -> str:
        ex_date = _optional_date(row.get("EX_DIVIDEND_DATE"), "EX_DIVIDEND_DATE")
        plan_date = _optional_date(row.get("PLAN_NOTICE_DATE"), "PLAN_NOTICE_DATE")
        report_date = _strict_date(row.get("REPORT_DATE"), "REPORT_DATE")
        return ex_date or plan_date or report_date

    def _map(
        self, row: dict[str, Any]
    ) -> tuple[
        dict[str, str | None],
        dict[str, str],
        dict[str, str],
        dict[str, Any],
        tuple[str, ...],
    ]:
        ex_date = _optional_date(row.get("EX_DIVIDEND_DATE"), "EX_DIVIDEND_DATE")
        plan_date = _optional_date(row.get("PLAN_NOTICE_DATE"), "PLAN_NOTICE_DATE")
        limitations = (
            () if ex_date is not None else ("event_date_uses_plan_or_report_date",)
        )
        metrics: dict[str, str | None] = {
            "cash_dividend_per_10_shares_before_tax": _nullable_numeric(
                row.get("PRETAX_BONUS_RMB"), "PRETAX_BONUS_RMB"
            ),
            "bonus_shares_per_10_shares": _nullable_numeric(
                row.get("BONUS_RATIO"), "BONUS_RATIO"
            ),
            "transfer_shares_per_10_shares": _nullable_numeric(
                row.get("TRANSFER_RATIO"), "TRANSFER_RATIO"
            ),
        }
        return (
            metrics,
            {
                "cash_dividend_per_10_shares_before_tax": "CNY_per_10_shares",
                "bonus_shares_per_10_shares": "share_per_10_shares",
                "transfer_shares_per_10_shares": "share_per_10_shares",
            },
            {
                "cash_dividend_per_10_shares_before_tax": "distribution_to_holder",
                "bonus_shares_per_10_shares": "distribution_to_holder",
                "transfer_shares_per_10_shares": "capitalization_to_holder",
            },
            {
                "implementation_status": _required_text(
                    row.get("ASSIGN_PROGRESS"), "ASSIGN_PROGRESS"
                ),
                "report_period_end": _strict_date(
                    row.get("REPORT_DATE"), "REPORT_DATE"
                ),
                "plan_notice_date": plan_date,
                "record_date": _optional_date(
                    row.get("EQUITY_RECORD_DATE"), "EQUITY_RECORD_DATE"
                ),
                "ex_dividend_date": ex_date,
                "cash_payment_date": _optional_date(
                    row.get("PAY_CASH_DATE"), "PAY_CASH_DATE"
                ),
            },
            (*limitations, *_metric_limitations(metrics)),
        )
