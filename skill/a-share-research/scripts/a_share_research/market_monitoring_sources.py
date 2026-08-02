"""Experimental market-monitoring sources with fail-closed semantics."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any
from urllib.parse import urlencode

from .identity_sources import HttpResponse, TransportError
from .market_signal_contract import (
    MarketSignalHttpTransport,
    MarketSignalObservation,
    MarketSignalQuery,
    SignalCoverage,
    SignalSourceBatch,
    SignalSourceFailure,
)
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
)

USER_AGENT = "Mozilla/5.0 (compatible; a-share-research-skill/0.1)"
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


class _MonitoringError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        degradations: tuple[SignalSourceFailure, ...] = (),
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.degradations = degradations
        self.details = dict(details or {})


class EastmoneyFocusMonitoringOperation:
    """Collect Eastmoney's provider watchlist without claiming official status."""

    operation_id = "eastmoney_focus_monitoring@1"
    supported_signal_types = frozenset({"focus_monitoring"})
    endpoint = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        if "focus_monitoring" not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        try:
            as_of = _market_wide_single_day(query)
            response, diagnostics = self._run_get()
            if _retrieval_date(response) != as_of:
                raise _MonitoringError(
                    "historical_snapshot_unavailable",
                    "The current provider watchlist cannot answer a historical "
                    "snapshot request.",
                    degradations=_gate_degradations(self.operation_id, diagnostics),
                )
            rows = _decode_monitor_rows(response)
            normalized: dict[tuple[str, ...], MarketSignalObservation] = {}
            exact_duplicates = 0
            for row in rows:
                observation = _monitor_observation(
                    row,
                    as_of=as_of,
                    retrieved_at=response.retrieved_at,
                    locator_uri=self.endpoint,
                )
                identity = _monitor_identity(observation)
                previous = normalized.get(identity)
                if previous is not None:
                    if previous != observation:
                        raise _MonitoringError(
                            "duplicate_source_conflict",
                            "Duplicate provider monitoring rows disagree.",
                            degradations=_gate_degradations(
                                self.operation_id, diagnostics
                            ),
                            details={
                                "provider_market_code": identity[0],
                                "provider_security_code": identity[1],
                                "monitoring_window_start": identity[2],
                                "monitoring_window_end": identity[3],
                            },
                        )
                    exact_duplicates += 1
                    continue
                normalized[identity] = observation
            observations = tuple(normalized.values())
            limitations: list[str] = []
            if exact_duplicates:
                limitations.append("exact_duplicate_rows_removed")
            if len(observations) > query.limit:
                limitations.append("result_truncated_to_limit")
            return SignalSourceBatch(
                operation_id=self.operation_id,
                observations=observations[: query.limit],
                coverage={
                    "focus_monitoring": SignalCoverage(
                        state="observed_nonempty",
                        provider_total=len(observations),
                        pages_collected=1,
                        pages_expected=1,
                    )
                },
                degradations=_gate_degradations(self.operation_id, diagnostics),
                limitations=tuple(limitations),
            )
        except _MonitoringError as error:
            return _failed(self.operation_id, "focus_monitoring", error)

    def _get(self) -> HttpResponse:
        return _get(
            self.operation_id,
            self._transport,
            self.endpoint,
            referer="https://vipmoney.eastmoney.com/",
        )

    def _run_get(
        self,
    ) -> tuple[HttpResponse, tuple[RequestGateDiagnostic, ...]]:
        try:
            return self._request_gate.run(partial(self._get))
        except RequestGateError as error:
            cause = error.cause
            if isinstance(cause, _MonitoringError):
                raise _MonitoringError(
                    cause.code,
                    str(cause),
                    degradations=_gate_degradations(
                        self.operation_id, error.diagnostics
                    ),
                    details=cause.details,
                ) from error
            raise


class EastmoneySevereAbnormalMovementOperation:
    """Collect the provider's latest severe-abnormal-movement pool."""

    operation_id = "eastmoney_severe_abnormal_movement@1"
    supported_signal_types = frozenset({"severe_abnormal_movement"})
    endpoint = "https://dycalchis.eastmoney.com/price-anomaly/list"
    _common_parameters = {
        "team": "h5",
        "product": "EastMoney",
        "client": "WAP",
        "version": "9001",
        "name": "WAP",
        "user": "123",
    }

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        page_size: int = 200,
        max_pages: int = 20,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 200:
            raise ValueError("page_size must be from 1 to 200")
        if not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be from 1 to 100")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        if "severe_abnormal_movement" not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        try:
            requested_day = _latest_pool_day(query)
            observations: list[MarketSignalObservation] = []
            seen_rows: dict[tuple[str, ...], dict[str, Any]] = {}
            diagnostics: list[RequestGateDiagnostic] = []
            expected_pages: int | None = None
            expected_response_day: str | None = None
            expected_open_code: str | None = None
            pages_collected = 0
            pagination_termination: str | None = None
            for page in range(1, self._max_pages + 1):
                url = self._page_url(page)
                response, page_diagnostics = self._run_get(url)
                diagnostics.extend(page_diagnostics)
                if _retrieval_date(response) != query.as_of:
                    raise _MonitoringError(
                        "historical_snapshot_unavailable",
                        "A later provider retrieval cannot backfill a historical "
                        "research date.",
                        degradations=_gate_degradations(
                            self.operation_id, tuple(diagnostics)
                        ),
                    )
                response_day, pages, open_code, rows = _decode_anomaly_page(response)
                pages_collected += 1
                if expected_response_day is None:
                    expected_response_day = response_day
                elif response_day != expected_response_day:
                    raise _MonitoringError(
                        "response_date_changed",
                        "The provider anomaly date changed during pagination.",
                        degradations=_gate_degradations(
                            self.operation_id, tuple(diagnostics)
                        ),
                    )
                if expected_open_code is None:
                    expected_open_code = open_code
                elif open_code != expected_open_code:
                    raise _MonitoringError(
                        "snapshot_state_changed",
                        "The provider market-open state changed during pagination.",
                        degradations=_gate_degradations(
                            self.operation_id, tuple(diagnostics)
                        ),
                    )
                if response_day != requested_day:
                    raise _MonitoringError(
                        "historical_snapshot_unavailable",
                        "The provider's latest anomaly date does not match the "
                        "requested date.",
                        degradations=_gate_degradations(
                            self.operation_id, tuple(diagnostics)
                        ),
                    )
                if expected_pages is None:
                    expected_pages = pages
                elif pages != expected_pages:
                    raise _MonitoringError(
                        "pagination_schema_changed",
                        "The provider page count changed during pagination.",
                        degradations=_gate_degradations(
                            self.operation_id, tuple(diagnostics)
                        ),
                    )
                if not rows:
                    if page == 1:
                        return SignalSourceBatch(
                            operation_id=self.operation_id,
                            coverage={
                                "severe_abnormal_movement": SignalCoverage(
                                    state="observed_empty",
                                    provider_total=0,
                                    pages_collected=1,
                                    pages_expected=pages,
                                    details={
                                        "pagination_termination": "empty_first_page",
                                        "provider_market_open_code": open_code,
                                    },
                                )
                            },
                            degradations=_gate_degradations(
                                self.operation_id, tuple(diagnostics)
                            ),
                            limitations=("provider_empty_not_market_absence",),
                        )
                    if page < pages:
                        raise _MonitoringError(
                            "pagination_incomplete",
                            "The provider returned an empty page before its "
                            "declared end.",
                            degradations=_gate_degradations(
                                self.operation_id, tuple(diagnostics)
                            ),
                        )
                    pagination_termination = "empty_sentinel"
                    break
                for row in rows:
                    identity = _anomaly_identity(row, response_day)
                    previous = seen_rows.get(identity)
                    if previous is not None:
                        if previous != row:
                            raise _MonitoringError(
                                "duplicate_conflict",
                                "Duplicate provider anomaly rows disagree.",
                                degradations=_gate_degradations(
                                    self.operation_id, tuple(diagnostics)
                                ),
                            )
                        continue
                    seen_rows[identity] = row
                    observations.append(
                        _anomaly_observation(
                            row,
                            response_day=response_day,
                            provider_market_open_code=open_code,
                            retrieved_at=response.retrieved_at,
                            locator_uri=url,
                        )
                    )
                if len(rows) < self._page_size:
                    if page < pages:
                        raise _MonitoringError(
                            "pagination_incomplete",
                            "The provider returned a short page before its declared end.",
                            degradations=_gate_degradations(
                                self.operation_id, tuple(diagnostics)
                            ),
                        )
                    pagination_termination = "short_declared_last_page"
                    break
                if page >= pages:
                    pagination_termination = "declared_last_page"
                    break
            else:
                raise _MonitoringError(
                    "pagination_incomplete",
                    "The bounded pagination did not reach a short or terminal page.",
                    degradations=_gate_degradations(
                        self.operation_id, tuple(diagnostics)
                    ),
                )
            if expected_pages is None:
                raise _MonitoringError(
                    "unknown_schema", "The provider returned no pagination metadata."
                )
            if pagination_termination is None:
                raise _MonitoringError(
                    "pagination_incomplete",
                    "The provider pagination did not expose a terminal condition.",
                    degradations=_gate_degradations(
                        self.operation_id, tuple(diagnostics)
                    ),
                )
            limited = len(observations) > query.limit
            return SignalSourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations[: query.limit]),
                coverage={
                    "severe_abnormal_movement": SignalCoverage(
                        state="observed_nonempty",
                        provider_total=None,
                        pages_collected=pages_collected,
                        pages_expected=expected_pages,
                        details={"pagination_termination": pagination_termination},
                    )
                },
                degradations=_gate_degradations(self.operation_id, tuple(diagnostics)),
                limitations=("result_truncated_to_limit",) if limited else (),
            )
        except _MonitoringError as error:
            return _failed(self.operation_id, "severe_abnormal_movement", error)

    def _page_url(self, page: int) -> str:
        parameters = {
            **self._common_parameters,
            "pageSize": str(self._page_size),
            "pageNo": str(page),
        }
        return f"{self.endpoint}?{urlencode(parameters)}"

    def _run_get(
        self, url: str
    ) -> tuple[HttpResponse, tuple[RequestGateDiagnostic, ...]]:
        try:
            return self._request_gate.run(
                partial(
                    _get,
                    self.operation_id,
                    self._transport,
                    url,
                    referer="https://vipmoney.eastmoney.com/",
                )
            )
        except RequestGateError as error:
            cause = error.cause
            if isinstance(cause, _MonitoringError):
                raise _MonitoringError(
                    cause.code,
                    str(cause),
                    degradations=_gate_degradations(
                        self.operation_id, error.diagnostics
                    ),
                    details=cause.details,
                ) from error
            raise


def _anomaly_observation(
    row: dict[str, Any],
    *,
    response_day: str,
    provider_market_open_code: str,
    retrieved_at: datetime,
    locator_uri: str,
) -> MarketSignalObservation:
    provider_market = _raw_code(row.get("m"), "provider market code")
    provider_code = _required_text(row.get("c"), "provider security code")
    provider_name = _required_text(row.get("n"), "provider security name")
    board = _raw_code(row.get("s"), "provider board code")
    rule_code = _raw_code(row.get("e"), "provider rule code")
    occurrence = _raw_code(row.get("o"), "provider occurrence state")
    days = _nonnegative_integer(row.get("d"), "statistics trading days")
    change_rate = _decimal_text(row.get("a"), "change rate")
    deviation = _decimal_text(row.get("x"), "cumulative deviation")
    target = _decimal_text(row.get("t"), "target change rate")
    return MarketSignalObservation(
        signal_type="severe_abnormal_movement",
        source_operation=EastmoneySevereAbnormalMovementOperation.operation_id,
        source_role="market_signal",
        subject=None,
        source_document_id=(
            f"{response_day}:{provider_market}:{board}:{provider_code}:"
            f"{rule_code}:{days}:{occurrence}"
        ),
        observed_on=response_day,
        observed_at=None,
        available_at=None,
        retrieved_at=retrieved_at,
        period={
            "start": response_day,
            "end": response_day,
            "frequency": "trading_day",
        },
        metrics={
            "change_rate": change_rate,
            "cumulative_deviation": deviation,
            "target_change_rate": target,
        },
        units={
            "change_rate": "percent",
            "cumulative_deviation": "percent",
            "target_change_rate": "percent",
        },
        directions={
            "change_rate": "positive_is_gain",
            "cumulative_deviation": "positive_is_above_benchmark",
            "target_change_rate": "positive_is_gain",
        },
        rule={
            "scheme": "eastmoney_price_anomaly.e",
            "code": rule_code,
        },
        attributions=(),
        dimensions={
            "market_scope": "provider_anomaly_pool",
            "provider_market_code": provider_market,
            "provider_security_code": provider_code,
            "provider_security_name": provider_name,
            "provider_security_type": None,
            "provider_board_code": board,
            "provider_occurrence_state_code": occurrence,
            "provider_market_open_code": provider_market_open_code,
            "statistics_trading_days": str(days),
        },
        locator_uri=locator_uri,
        limitations=(
            "security_exchange_unverified",
            "security_type_unverified",
            "provider_rule_semantics_unverified",
            "provider_occurrence_state_unverified",
            "provider_market_open_state_unverified",
            "period_start_not_exposed",
            "availability_time_unknown",
        ),
    )


def _decode_anomaly_page(
    response: HttpResponse,
) -> tuple[str, int, str, list[dict[str, Any]]]:
    payload = _decode_json(response, preserve_decimals=True)
    if not isinstance(payload, dict):
        raise _MonitoringError(
            "unknown_schema", "The provider anomaly response is not an object."
        )
    result = payload.get("result")
    if isinstance(result, bool) or not isinstance(result, int):
        raise _MonitoringError(
            "unknown_schema", "The provider anomaly business status is invalid."
        )
    if result != 0:
        raise _MonitoringError(
            "provider_error",
            "The provider anomaly request was rejected.",
            details={"provider_result": result},
        )
    pages = payload.get("pages")
    count = payload.get("count")
    rows = payload.get("data")
    if (
        isinstance(pages, bool)
        or not isinstance(pages, int)
        or pages < 1
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
        or count != len(rows)
    ):
        raise _MonitoringError(
            "unknown_schema", "The provider anomaly pagination schema is invalid."
        )
    return (
        _compact_trade_date(payload.get("date")),
        pages,
        _raw_code(payload.get("open"), "provider market-open state"),
        rows,
    )


def _compact_trade_date(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _MonitoringError("invalid_date", "The provider anomaly date is invalid.")
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        raise _MonitoringError("invalid_date", "The provider anomaly date is invalid.")
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError as error:
        raise _MonitoringError(
            "invalid_date", "The provider anomaly date is invalid."
        ) from error


def _anomaly_identity(row: dict[str, Any], response_day: str) -> tuple[str, ...]:
    return (
        response_day,
        _raw_code(row.get("m"), "provider market code"),
        _raw_code(row.get("s"), "provider board code"),
        _required_text(row.get("c"), "provider security code"),
        _raw_code(row.get("e"), "provider rule code"),
        str(_nonnegative_integer(row.get("d"), "statistics trading days")),
        _raw_code(row.get("o"), "provider occurrence state"),
    )


def _monitor_observation(
    row: dict[str, Any],
    *,
    as_of: str,
    retrieved_at: datetime,
    locator_uri: str,
) -> MarketSignalObservation:
    provider_market = _required_text(row.get("MARKET"), "provider market code")
    provider_code = _required_text(row.get("STKCODE"), "provider security code")
    provider_name = _required_text(row.get("STKNAME"), "provider security name")
    start = _strict_date(row.get("VALIDATESTARTDATE"), "monitoring start")
    end = _strict_date(row.get("VALIDATEENDDATE"), "monitoring end")
    if start > end:
        raise _MonitoringError(
            "unknown_schema", "A provider monitoring window is reversed."
        )
    if as_of < start:
        monitoring_state = "scheduled"
    elif as_of <= end:
        monitoring_state = "active"
    else:
        monitoring_state = "expired"
    link = row.get("LINK_URL")
    if link is not None and not isinstance(link, str):
        raise _MonitoringError(
            "unknown_schema", "The provider monitoring detail link is invalid."
        )
    normalized_link = link.strip() if isinstance(link, str) else ""
    return MarketSignalObservation(
        signal_type="focus_monitoring",
        source_operation=EastmoneyFocusMonitoringOperation.operation_id,
        source_role="market_signal",
        subject=None,
        source_document_id=(f"{provider_market}:{provider_code}:{start}:{end}"),
        observed_on=as_of,
        observed_at=None,
        available_at=None,
        retrieved_at=retrieved_at,
        period={
            "start": start,
            "end": end,
            "frequency": "calendar_date_window",
        },
        metrics={"watchlist_membership": "1"},
        units={"watchlist_membership": "boolean_indicator"},
        directions={"watchlist_membership": "descriptive"},
        rule=None,
        attributions=(),
        dimensions={
            "market_scope": "provider_watchlist",
            "provider_market_code": provider_market,
            "provider_security_code": provider_code,
            "provider_security_name": provider_name,
            "provider_security_type": None,
            "monitoring_state": monitoring_state,
            "provider_detail_url": normalized_link or None,
        },
        locator_uri=locator_uri,
        limitations=(
            "provider_watchlist_not_official",
            "security_exchange_unverified",
            "security_type_unverified",
            "availability_time_unknown",
            "monitoring_reason_unverified",
        ),
    )


def _monitor_identity(observation: MarketSignalObservation) -> tuple[str, ...]:
    return (
        str(observation.dimensions["provider_market_code"]),
        str(observation.dimensions["provider_security_code"]),
        str(observation.period["start"]),
        str(observation.period["end"]),
    )


def _decode_monitor_rows(response: HttpResponse) -> list[dict[str, Any]]:
    payload = _decode_json(response)
    if not isinstance(payload, list) or any(
        not isinstance(row, dict) for row in payload
    ):
        raise _MonitoringError(
            "unknown_schema", "The provider monitoring response is not a row array."
        )
    if not payload:
        raise _MonitoringError(
            "empty_response_unverified",
            "An empty provider monitoring response cannot prove an empty watchlist.",
        )
    return payload


def _get(
    operation_id: str,
    transport: MarketSignalHttpTransport,
    url: str,
    *,
    referer: str,
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json",
                "Referer": referer,
                "User-Agent": USER_AGENT,
            },
        )
    except TransportError as error:
        raise _MonitoringError(error.code, str(error)) from error
    if response.status == 429:
        raise _MonitoringError("rate_limited", "The source rate limit was reached.")
    if response.status != 200:
        raise _MonitoringError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    return response


def _decode_json(response: HttpResponse, *, preserve_decimals: bool = False) -> Any:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "text/plain"}:
        raise _MonitoringError(
            "unexpected_content_type", "The source response is not JSON."
        )
    try:
        if preserve_decimals:
            return json.loads(response.body, parse_float=Decimal)
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _MonitoringError(
            "unknown_schema", "The source JSON is invalid."
        ) from error


def _retrieval_date(response: HttpResponse) -> str:
    retrieved_at = response.retrieved_at
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise _MonitoringError(
            "unknown_schema", "The source retrieval time has no timezone."
        )
    return retrieved_at.astimezone(CHINA_STANDARD_TIME).date().isoformat()


def _market_wide_single_day(query: MarketSignalQuery) -> str:
    if query.subject is not None:
        raise _MonitoringError(
            "invalid_subject", "Market monitoring is a market-wide operation."
        )
    as_of = _strict_date(query.as_of, "research date")
    observed_from = _strict_date(query.observed_from, "window start")
    observed_to = _strict_date(query.observed_to, "window end")
    if observed_from != observed_to or observed_to != as_of:
        raise _MonitoringError(
            "unsupported_historical_query",
            "The provider monitoring endpoint exposes only its current snapshot.",
        )
    if isinstance(query.limit, bool) or not 1 <= query.limit <= 500:
        raise _MonitoringError(
            "invalid_limit", "The source limit must be from 1 to 500."
        )
    return as_of


def _latest_pool_day(query: MarketSignalQuery) -> str:
    if query.subject is not None:
        raise _MonitoringError(
            "invalid_subject", "Severe abnormal movement is a market-wide operation."
        )
    as_of = _strict_date(query.as_of, "research date")
    observed_from = _strict_date(query.observed_from, "window start")
    observed_to = _strict_date(query.observed_to, "window end")
    if observed_from != observed_to:
        raise _MonitoringError(
            "unsupported_historical_query",
            "The provider anomaly endpoint exposes only one latest trading date.",
        )
    if observed_to > as_of:
        raise _MonitoringError(
            "future_window", "The anomaly date exceeds the research date."
        )
    if isinstance(query.limit, bool) or not 1 <= query.limit <= 500:
        raise _MonitoringError(
            "invalid_limit", "The source limit must be from 1 to 500."
        )
    return observed_to


def _strict_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _MonitoringError("invalid_date", f"The {field} must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _MonitoringError(
            "invalid_date", f"The {field} must use YYYY-MM-DD."
        ) from error
    if parsed.isoformat() != value:
        raise _MonitoringError("invalid_date", f"The {field} must use YYYY-MM-DD.")
    return value


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _MonitoringError("unknown_schema", f"The source {field} is missing.")
    return value.strip()


def _raw_code(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _MonitoringError("unknown_schema", f"The source {field} is invalid.")
    text = str(value).strip()
    if not text:
        raise _MonitoringError("unknown_schema", f"The source {field} is invalid.")
    return text


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _MonitoringError("unknown_schema", f"The source {field} is invalid.")
    return value


def _decimal_text(value: object, field: str) -> str:
    if (
        isinstance(value, bool)
        or value is None
        or not isinstance(value, (str, int, float, Decimal))
    ):
        raise _MonitoringError("unknown_schema", f"The source {field} is invalid.")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise _MonitoringError(
            "unknown_schema", f"The source {field} is invalid."
        ) from error
    if not parsed.is_finite():
        raise _MonitoringError("unknown_schema", f"The source {field} is invalid.")
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _gate_degradations(
    operation_id: str,
    diagnostics: tuple[RequestGateDiagnostic, ...],
) -> tuple[SignalSourceFailure, ...]:
    return tuple(
        SignalSourceFailure(
            operation_id,
            diagnostic.code,
            diagnostic.message,
            diagnostic.details(),
        )
        for diagnostic in diagnostics
    )


def _failed(
    operation_id: str,
    signal_type: str,
    error: _MonitoringError,
) -> SignalSourceBatch:
    return SignalSourceBatch(
        operation_id=operation_id,
        coverage={signal_type: SignalCoverage(state="indeterminate")},
        source_errors=(
            SignalSourceFailure(operation_id, error.code, str(error), error.details),
        ),
        degradations=error.degradations,
    )
