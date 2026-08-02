"""Experimental theme, board, rotation, and market-heat source operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
    ThemeAttribution,
)
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
    SerialRequestGate,
)

USER_AGENT = "Mozilla/5.0 a-share-research-skill/1"
THS_STRONG_STOCK_URL = (
    "http://zx.10jqka.com.cn/event/api/getharden/date/{date}/"
    "orderby/date/orderway/desc/charset/GBK/"
)
EASTMONEY_SLIST_URL = "https://push2.eastmoney.com/api/qt/slist/get"
EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
THS_MARKET_HEAT_URL = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
)
THS_REQUEST_GATE = SerialRequestGate()
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


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


def _failure(operation_id: str, error: _SourceError) -> SignalSourceFailure:
    return SignalSourceFailure(operation_id, error.code, str(error))


def _degradations(
    operation_id: str,
    diagnostics: tuple[RequestGateDiagnostic, ...],
) -> tuple[SignalSourceFailure, ...]:
    return tuple(
        SignalSourceFailure(
            source_operation=operation_id,
            code=item.code,
            message=item.message,
            details=item.details(),
        )
        for item in diagnostics
    )


def _failed_batch(
    operation_id: str, signal_type: str, error: _SourceError
) -> SignalSourceBatch:
    return SignalSourceBatch(
        operation_id=operation_id,
        coverage={signal_type: SignalCoverage(state="indeterminate")},
        source_errors=(_failure(operation_id, error),),
        degradations=_degradations(operation_id, error.diagnostics),
    )


def _single_observation_date(query: MarketSignalQuery) -> str:
    if query.observed_from != query.observed_to:
        raise _SourceError(
            "unsupported_window",
            "This snapshot source requires one exact observation date.",
        )
    try:
        observed_on = date.fromisoformat(query.observed_to)
        as_of = date.fromisoformat(query.as_of)
    except ValueError as error:
        raise _SourceError("invalid_date", "The query date is invalid.") from error
    if observed_on > as_of:
        raise _SourceError(
            "future_observation_date",
            "The observation date is later than the research boundary.",
        )
    return observed_on.isoformat()


def _request_json(
    operation_id: str,
    transport: MarketSignalHttpTransport,
    request_gate: RequestGate,
    locator_uri: str,
    headers: dict[str, str],
) -> tuple[dict[str, Any], HttpResponse, tuple[RequestGateDiagnostic, ...]]:
    try:
        response, diagnostics = request_gate.run(
            lambda: transport.get(locator_uri, headers)
        )
    except RequestGateError as error:
        cause = error.cause
        code = getattr(cause, "code", "upstream_unavailable")
        raise _SourceError(code, str(cause), diagnostics=error.diagnostics) from error
    except TransportError as error:
        raise _SourceError(error.code, str(error)) from error
    except Exception as error:
        raise _SourceError(
            "upstream_unavailable",
            "The source request could not be completed.",
        ) from error
    if response.status != 200:
        raise _SourceError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
            diagnostics=diagnostics,
        )
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _SourceError(
            "unexpected_content_type",
            "The source response is not JSON.",
            diagnostics=diagnostics,
        )
    if not response.body.strip():
        raise _SourceError(
            "empty_response",
            "The source returned an empty response body.",
            diagnostics=diagnostics,
        )
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _SourceError(
            "unknown_schema",
            "The source response does not match the expected schema.",
            diagnostics=diagnostics,
        ) from error
    if not isinstance(payload, dict):
        raise _SourceError(
            "unknown_schema",
            "The source response does not match the expected schema.",
            diagnostics=diagnostics,
        )
    return payload, response, diagnostics


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _SourceError(
            "unknown_schema", f"The source {field} field is missing or invalid."
        )
    return value.strip()


def _optional_decimal(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise _SourceError(
            "unknown_schema", f"The source {field} field is invalid."
        ) from error
    if not number.is_finite():
        raise _SourceError("unknown_schema", f"The source {field} field is invalid.")
    return format(number, "f")


def _required_decimal(value: object, field: str) -> Decimal:
    text = _optional_decimal(value, field)
    if text is None:
        raise _SourceError("unknown_schema", f"The source {field} field is missing.")
    return Decimal(text)


def _required_nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _SourceError("unknown_schema", f"The source {field} field is invalid.")
    try:
        number = int(value)
    except ValueError as error:
        raise _SourceError(
            "unknown_schema", f"The source {field} field is invalid."
        ) from error
    if number < 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise _SourceError("unknown_schema", f"The source {field} field is invalid.")
    return number


def _retrieved_on(response: HttpResponse) -> str:
    if response.retrieved_at.tzinfo is None:
        raise _SourceError(
            "unknown_retrieval_time",
            "The source retrieval time has no timezone.",
        )
    return response.retrieved_at.astimezone(CHINA_STANDARD_TIME).date().isoformat()


def _canonical_security(query: MarketSignalQuery) -> tuple[str, str, dict[str, Any]]:
    subject = query.subject
    if not isinstance(subject, dict):
        raise _SourceError(
            "missing_subject", "Security board membership requires one subject."
        )
    security = subject.get("security")
    if not isinstance(security, dict):
        raise _SourceError(
            "invalid_subject", "The subject has no canonical security identifier."
        )
    exchange = security.get("exchange")
    code = security.get("code")
    security_type = security.get("type")
    if exchange not in {"SSE", "SZSE"}:
        raise _SourceError(
            "unsupported_exchange",
            "The Eastmoney board source has not qualified this exchange mapping.",
        )
    if security_type != "A_SHARE":
        raise _SourceError(
            "unsupported_security_type",
            "Security board membership requires an A-share security.",
        )
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        raise _SourceError("invalid_subject", "The canonical security code is invalid.")
    return exchange, code, subject


@dataclass(frozen=True)
class _BoardRow:
    value: dict[str, Any]
    retrieved_at: datetime
    locator_uri: str
    response_echoed_security: bool


@dataclass(frozen=True)
class _IndustryRow:
    value: dict[str, Any]
    retrieved_at: datetime
    locator_uri: str


class ThsStrongStockThemeOperation:
    """Collect THS editorial reasons attached to one dated strong-stock list."""

    operation_id = "ths_strong_stock_theme@1"
    supported_signal_types = frozenset({"strong_stock_theme"})

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or THS_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        if "strong_stock_theme" not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        try:
            observed_on = _single_observation_date(query)
            locator_uri = THS_STRONG_STOCK_URL.format(date=observed_on)
            payload, response, diagnostics = _request_json(
                self.operation_id,
                self._transport,
                self._request_gate,
                locator_uri,
                {"User-Agent": USER_AGENT},
            )
            error_code = payload.get("errocode")
            if not isinstance(error_code, int):
                raise _SourceError(
                    "unknown_schema",
                    "The THS business status is missing or invalid.",
                    diagnostics=diagnostics,
                )
            if error_code != 0:
                raise _SourceError(
                    "provider_error",
                    "THS rejected the strong-stock theme request.",
                    diagnostics=diagnostics,
                )
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise _SourceError(
                    "unknown_schema",
                    "The THS strong-stock result list is missing.",
                    diagnostics=diagnostics,
                )
            observations: list[MarketSignalObservation] = []
            seen_codes: set[str] = set()
            for value in rows:
                if not isinstance(value, dict):
                    raise _SourceError(
                        "unknown_schema",
                        "A THS strong-stock row is invalid.",
                        diagnostics=diagnostics,
                    )
                code = _required_text(value.get("code"), "security code")
                if not (len(code) == 6 and code.isdigit()):
                    raise _SourceError(
                        "unknown_schema",
                        "The THS security code is invalid.",
                        diagnostics=diagnostics,
                    )
                if code in seen_codes:
                    raise _SourceError(
                        "duplicate_records",
                        "The THS strong-stock response contains duplicate securities.",
                        diagnostics=diagnostics,
                    )
                seen_codes.add(code)
                name = _required_text(value.get("name"), "security name")
                reason = _required_text(value.get("reason"), "editorial reason")
                document_id = f"ths-getharden-{observed_on.replace('-', '')}-{code}"
                attribution = ThemeAttribution(
                    text=reason,
                    provenance="editorial_annotation",
                    source_operation=self.operation_id,
                    source_document_id=document_id,
                    locator_uri=locator_uri,
                )
                observations.append(
                    MarketSignalObservation(
                        signal_type="strong_stock_theme",
                        source_operation=self.operation_id,
                        source_role="market_signal",
                        subject=None,
                        source_document_id=document_id,
                        observed_on=observed_on,
                        observed_at=None,
                        available_at=None,
                        retrieved_at=response.retrieved_at,
                        period={
                            "start": observed_on,
                            "end": observed_on,
                            "frequency": "trading_day",
                        },
                        metrics={
                            "change_rate": _optional_decimal(
                                value.get("zhangfu"), "change rate"
                            ),
                            "close_price": _optional_decimal(
                                value.get("close"), "close price"
                            ),
                            "turnover_rate": _optional_decimal(
                                value.get("huanshou"), "turnover rate"
                            ),
                            "trading_amount": _optional_decimal(
                                value.get("chengjiaoe"), "trading amount"
                            ),
                        },
                        units={
                            "change_rate": "percent",
                            "close_price": "CNY_per_share",
                            "turnover_rate": "percent",
                            "trading_amount": "CNY",
                        },
                        directions={
                            "change_rate": "positive_is_gain",
                            "close_price": "not_directional",
                            "turnover_rate": "not_directional",
                            "trading_amount": "not_directional",
                        },
                        rule=None,
                        attributions=(attribution,),
                        dimensions={
                            "market_scope": "mainland_a_share",
                            "provider_security_code": code,
                            "provider_security_name": name,
                            "provider_market": value.get("market"),
                        },
                        locator_uri=locator_uri,
                        limitations=(
                            "plaintext_http_source",
                            "security_exchange_unverified",
                            "availability_time_unknown",
                            "editorial_reason_not_independently_verified",
                        ),
                    )
                )
            selected = tuple(observations[: query.limit])
            state = "observed_nonempty" if rows else "observed_empty"
            return SignalSourceBatch(
                operation_id=self.operation_id,
                observations=selected,
                coverage={
                    "strong_stock_theme": SignalCoverage(
                        state=state,
                        provider_total=len(rows),
                        pages_collected=1,
                        pages_expected=1,
                    )
                },
                degradations=_degradations(self.operation_id, diagnostics),
            )
        except _SourceError as error:
            return _failed_batch(self.operation_id, "strong_stock_theme", error)


class EastmoneySecurityBoardMembershipOperation:
    """Collect the provider's unclassified board memberships for one security."""

    operation_id = "eastmoney_security_board_membership@1"
    supported_signal_types = frozenset({"security_board_membership"})

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        page_size: int = 200,
        max_pages: int = 20,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 200 or not 1 <= max_pages <= 100:
            raise ValueError("board-membership pagination bounds are invalid")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        signal_type = "security_board_membership"
        if signal_type not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        diagnostics: list[RequestGateDiagnostic] = []
        try:
            requested_on = _single_observation_date(query)
            exchange, code, subject = _canonical_security(query)
            market = {"SSE": "1", "SZSE": "0"}[exchange]
            expected_secid = f"{market}.{code}"
            rows: list[_BoardRow] = []
            provider_total: int | None = None
            page_index = 0
            pages_expected: int | None = None
            while True:
                if page_index >= self._max_pages:
                    raise _SourceError(
                        "pagination_incomplete",
                        "The board-membership response exceeded the bounded page limit.",
                    )
                params = {
                    "fltt": "2",
                    "invt": "2",
                    "secid": expected_secid,
                    "spt": "3",
                    "pi": str(page_index),
                    "pz": str(self._page_size),
                    "po": "1",
                    "fields": "f12,f14,f3,f128",
                }
                locator_uri = f"{EASTMONEY_SLIST_URL}?{urlencode(params)}"
                payload, response, page_diagnostics = _request_json(
                    self.operation_id,
                    self._transport,
                    self._request_gate,
                    locator_uri,
                    {
                        "User-Agent": USER_AGENT,
                        "Referer": "https://quote.eastmoney.com/",
                    },
                )
                diagnostics.extend(page_diagnostics)
                if _retrieved_on(response) != requested_on:
                    raise _SourceError(
                        "source_date_mismatch",
                        "The undated board snapshot was not retrieved on the requested date.",
                    )
                data = payload.get("data")
                if (
                    not isinstance(data, dict)
                    or "total" not in data
                    or "diff" not in data
                ):
                    raise _SourceError(
                        "unknown_schema",
                        "The Eastmoney board response lacks total or row data.",
                    )
                page_total = _required_nonnegative_int(data.get("total"), "total")
                if provider_total is None:
                    provider_total = page_total
                    pages_expected = max(
                        1,
                        (provider_total + self._page_size - 1) // self._page_size,
                    )
                elif page_total != provider_total:
                    raise _SourceError(
                        "pagination_inconsistent",
                        "The board response changed total count across pages.",
                    )
                diff = data.get("diff")
                if isinstance(diff, dict):
                    page_rows = list(diff.values())
                elif isinstance(diff, list):
                    page_rows = diff
                else:
                    raise _SourceError(
                        "unknown_schema", "The Eastmoney board row list is invalid."
                    )
                echoed = data.get("secid")
                if echoed is not None and echoed != expected_secid:
                    raise _SourceError(
                        "wrong_security",
                        "The board response identifies a different security.",
                    )
                if not all(isinstance(item, dict) for item in page_rows):
                    raise _SourceError(
                        "unknown_schema", "An Eastmoney board row is invalid."
                    )
                rows.extend(
                    _BoardRow(
                        value=item,
                        retrieved_at=response.retrieved_at,
                        locator_uri=locator_uri,
                        response_echoed_security=echoed is not None,
                    )
                    for item in page_rows
                )
                if len(rows) >= provider_total:
                    break
                if not page_rows:
                    raise _SourceError(
                        "pagination_incomplete",
                        "The board source returned an empty page before its declared total.",
                    )
                page_index += 1
            assert provider_total is not None and pages_expected is not None
            if len(rows) != provider_total or page_index + 1 != pages_expected:
                raise _SourceError(
                    "pagination_incomplete",
                    "The board response did not match its declared total.",
                )
            observations: list[MarketSignalObservation] = []
            seen_boards: set[str] = set()
            for fetched in rows:
                value = fetched.value
                board_code = _required_text(value.get("f12"), "board code")
                if not board_code.startswith("BK"):
                    raise _SourceError(
                        "unknown_schema",
                        "The board response contains a non-board code.",
                    )
                if board_code in seen_boards:
                    raise _SourceError(
                        "duplicate_records",
                        "The board response contains a duplicate board.",
                    )
                seen_boards.add(board_code)
                board_name = _required_text(value.get("f14"), "board name")
                document_id = (
                    f"eastmoney-slist-{exchange}-{code}-{requested_on.replace('-', '')}-"
                    f"{board_code}"
                )
                limitations = [
                    "board_classification_not_exposed",
                    "observation_time_not_exposed",
                    "availability_time_unknown",
                ]
                if not fetched.response_echoed_security:
                    limitations.append("source_does_not_echo_subject_identity")
                observations.append(
                    MarketSignalObservation(
                        signal_type=signal_type,
                        source_operation=self.operation_id,
                        source_role="market_signal",
                        subject=subject,
                        source_document_id=document_id,
                        observed_on=requested_on,
                        observed_at=None,
                        available_at=None,
                        retrieved_at=fetched.retrieved_at,
                        period={
                            "start": requested_on,
                            "end": requested_on,
                            "frequency": "snapshot",
                        },
                        metrics={
                            "board_change_rate": _optional_decimal(
                                value.get("f3"), "board change rate"
                            )
                        },
                        units={"board_change_rate": "percent"},
                        directions={"board_change_rate": "positive_is_gain"},
                        rule=None,
                        attributions=(),
                        dimensions={
                            "market_scope": "mainland_a_share",
                            "board_code": board_code,
                            "board_name": board_name,
                            "board_type": "unclassified",
                            "lead_stock": value.get("f128"),
                            "provider_security_code": code,
                        },
                        locator_uri=fetched.locator_uri,
                        limitations=tuple(limitations),
                    )
                )
            state = "observed_nonempty" if rows else "observed_empty"
            return SignalSourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations[: query.limit]),
                coverage={
                    signal_type: SignalCoverage(
                        state=state,
                        provider_total=provider_total,
                        pages_collected=page_index + 1,
                        pages_expected=pages_expected,
                    )
                },
                degradations=_degradations(self.operation_id, tuple(diagnostics)),
            )
        except _SourceError as error:
            if diagnostics and not error.diagnostics:
                error = _SourceError(
                    error.code, str(error), diagnostics=tuple(diagnostics)
                )
            return _failed_batch(self.operation_id, signal_type, error)


class EastmoneyIndustryRotationOperation:
    """Collect one complete, provider-timestamped industry ranking snapshot."""

    operation_id = "eastmoney_industry_rotation@1"
    supported_signal_types = frozenset({"industry_rotation"})

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        page_size: int = 100,
        max_pages: int = 20,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 200 or not 1 <= max_pages <= 100:
            raise ValueError("industry-rotation pagination bounds are invalid")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        signal_type = "industry_rotation"
        if signal_type not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        diagnostics: list[RequestGateDiagnostic] = []
        try:
            requested_on = _single_observation_date(query)
            if query.subject is not None:
                raise _SourceError(
                    "unexpected_subject",
                    "Industry rotation is a market-wide source operation.",
                )
            rows: list[_IndustryRow] = []
            provider_total: int | None = None
            pages_expected: int | None = None
            page_number = 1
            while True:
                if page_number > self._max_pages:
                    raise _SourceError(
                        "pagination_incomplete",
                        "The industry response exceeded the bounded page limit.",
                    )
                params = {
                    "pn": str(page_number),
                    "pz": str(self._page_size),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": "m:90+t:2",
                    "fields": ("f3,f12,f14,f104,f105,f136,f140,f124"),
                }
                locator_uri = f"{EASTMONEY_CLIST_URL}?{urlencode(params)}"
                payload, response, page_diagnostics = _request_json(
                    self.operation_id,
                    self._transport,
                    self._request_gate,
                    locator_uri,
                    {"User-Agent": USER_AGENT},
                )
                diagnostics.extend(page_diagnostics)
                data = payload.get("data")
                if (
                    not isinstance(data, dict)
                    or "total" not in data
                    or "diff" not in data
                ):
                    raise _SourceError(
                        "unknown_schema",
                        "The Eastmoney industry response lacks total or row data.",
                    )
                page_total = _required_nonnegative_int(data.get("total"), "total")
                if provider_total is None:
                    provider_total = page_total
                    pages_expected = max(
                        1,
                        (provider_total + self._page_size - 1) // self._page_size,
                    )
                elif page_total != provider_total:
                    raise _SourceError(
                        "pagination_inconsistent",
                        "The industry response changed total count across pages.",
                    )
                diff = data.get("diff")
                if isinstance(diff, dict):
                    page_rows = list(diff.values())
                elif isinstance(diff, list):
                    page_rows = diff
                else:
                    raise _SourceError(
                        "unknown_schema", "The Eastmoney industry row list is invalid."
                    )
                if not all(isinstance(item, dict) for item in page_rows):
                    raise _SourceError(
                        "unknown_schema", "An Eastmoney industry row is invalid."
                    )
                rows.extend(
                    _IndustryRow(
                        value=item,
                        retrieved_at=response.retrieved_at,
                        locator_uri=locator_uri,
                    )
                    for item in page_rows
                )
                if len(rows) >= provider_total:
                    break
                if not page_rows:
                    raise _SourceError(
                        "pagination_incomplete",
                        "The industry source returned an empty page before its declared total.",
                    )
                page_number += 1
            assert provider_total is not None and pages_expected is not None
            if len(rows) != provider_total or page_number != pages_expected:
                raise _SourceError(
                    "pagination_incomplete",
                    "The industry response did not match its declared total.",
                )
            if not rows:
                if _retrieved_on(response) != requested_on:
                    raise _SourceError(
                        "source_date_mismatch",
                        "The empty industry snapshot cannot be bound to the requested date.",
                    )
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        signal_type: SignalCoverage(
                            state="observed_empty",
                            provider_total=0,
                            pages_collected=1,
                            pages_expected=1,
                        )
                    },
                    degradations=_degradations(self.operation_id, tuple(diagnostics)),
                    limitations=("empty_snapshot_date_anchored_to_retrieval_time",),
                )
            normalized: list[
                tuple[_IndustryRow, str, str, Decimal, int, int, str, str | None]
            ] = []
            seen_boards: set[str] = set()
            provider_timestamp: datetime | None = None
            for fetched in rows:
                value = fetched.value
                board_code = _required_text(value.get("f12"), "board code")
                if not board_code.startswith("BK"):
                    raise _SourceError(
                        "unknown_schema",
                        "The industry response contains a non-board code.",
                    )
                if board_code in seen_boards:
                    raise _SourceError(
                        "duplicate_records",
                        "The industry response contains a duplicate board.",
                    )
                seen_boards.add(board_code)
                board_name = _required_text(value.get("f14"), "board name")
                change_rate = _required_decimal(value.get("f3"), "change rate")
                up_count = _required_nonnegative_int(value.get("f104"), "up count")
                down_count = _required_nonnegative_int(value.get("f105"), "down count")
                timestamp_value = _required_nonnegative_int(
                    value.get("f124"), "provider timestamp"
                )
                try:
                    observed_at = datetime.fromtimestamp(
                        timestamp_value, timezone.utc
                    ).astimezone(CHINA_STANDARD_TIME)
                except (OverflowError, OSError, ValueError) as error:
                    raise _SourceError(
                        "unknown_schema", "The provider timestamp is invalid."
                    ) from error
                if observed_at.date().isoformat() != requested_on:
                    raise _SourceError(
                        "source_date_mismatch",
                        "The industry snapshot date differs from the requested date.",
                    )
                retrieved_at = fetched.retrieved_at
                if retrieved_at.tzinfo is None:
                    raise _SourceError(
                        "unknown_retrieval_time",
                        "The source retrieval time has no timezone.",
                    )
                if observed_at > retrieved_at.astimezone(CHINA_STANDARD_TIME):
                    raise _SourceError(
                        "future_source_timestamp",
                        "The industry snapshot timestamp is later than retrieval.",
                    )
                if provider_timestamp is None:
                    provider_timestamp = observed_at
                elif observed_at != provider_timestamp:
                    raise _SourceError(
                        "mixed_snapshot_times",
                        "The industry ranking combines different provider snapshots.",
                    )
                leader = value.get("f140")
                if leader is not None and not isinstance(leader, str):
                    raise _SourceError(
                        "unknown_schema", "The industry leader field is invalid."
                    )
                normalized.append(
                    (
                        fetched,
                        board_code,
                        board_name,
                        change_rate,
                        up_count,
                        down_count,
                        leader or "",
                        _optional_decimal(value.get("f136"), "leader change rate"),
                    )
                )
            change_rates = [item[3] for item in normalized]
            if any(
                left < right
                for left, right in zip(change_rates, change_rates[1:], strict=False)
            ):
                raise _SourceError(
                    "unexpected_sort_order",
                    "The industry response is not sorted by change rate descending.",
                )
            assert provider_timestamp is not None
            observed_at_text = provider_timestamp.isoformat(timespec="seconds")
            observations: list[MarketSignalObservation] = []
            for rank, item in enumerate(normalized, start=1):
                (
                    fetched,
                    board_code,
                    board_name,
                    change_rate,
                    up_count,
                    down_count,
                    leader,
                    leader_change,
                ) = item
                document_id = (
                    f"eastmoney-industry-rotation-{requested_on.replace('-', '')}-"
                    f"{int(provider_timestamp.timestamp())}-{board_code}"
                )
                observations.append(
                    MarketSignalObservation(
                        signal_type=signal_type,
                        source_operation=self.operation_id,
                        source_role="market_signal",
                        subject=None,
                        source_document_id=document_id,
                        observed_on=requested_on,
                        observed_at=observed_at_text,
                        available_at=None,
                        retrieved_at=fetched.retrieved_at,
                        period={
                            "start": requested_on,
                            "end": requested_on,
                            "frequency": "snapshot",
                        },
                        metrics={
                            "change_rate": format(change_rate, "f"),
                            "up_count": str(up_count),
                            "down_count": str(down_count),
                            "leader_change_rate": leader_change,
                        },
                        units={
                            "change_rate": "percent",
                            "up_count": "security_count",
                            "down_count": "security_count",
                            "leader_change_rate": "percent",
                        },
                        directions={
                            "change_rate": "positive_is_gain",
                            "up_count": "not_directional",
                            "down_count": "not_directional",
                            "leader_change_rate": "positive_is_gain",
                        },
                        rule={
                            "code": "provider_change_rate_desc",
                            "sort_field": "change_rate",
                            "sort_direction": "descending",
                            "provider_field": "f3",
                        },
                        attributions=(),
                        dimensions={
                            "rank": rank,
                            "board_code": board_code,
                            "board_name": board_name,
                            "leader": leader,
                            "market_scope": "eastmoney_industry_boards",
                        },
                        locator_uri=fetched.locator_uri,
                        limitations=("availability_time_unknown",),
                    )
                )
            return SignalSourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations[: query.limit]),
                coverage={
                    signal_type: SignalCoverage(
                        state="observed_nonempty",
                        provider_total=provider_total,
                        pages_collected=page_number,
                        pages_expected=pages_expected,
                    )
                },
                degradations=_degradations(self.operation_id, tuple(diagnostics)),
            )
        except _SourceError as error:
            if diagnostics and not error.diagnostics:
                error = _SourceError(
                    error.code, str(error), diagnostics=tuple(diagnostics)
                )
            return _failed_batch(self.operation_id, signal_type, error)


class ThsMarketHeatOperation:
    """Collect the current THS A-share popularity ranking."""

    operation_id = "ths_market_heat@1"
    supported_signal_types = frozenset({"market_heat"})

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or THS_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        signal_type = "market_heat"
        if signal_type not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        try:
            requested_on = _single_observation_date(query)
            if query.subject is not None:
                raise _SourceError(
                    "unexpected_subject",
                    "Market heat is a market-wide source operation.",
                )
            period = query.parameters.get(
                "market_heat_period", query.parameters.get("period", "hour")
            )
            if period not in {"hour", "day"}:
                raise _SourceError(
                    "invalid_parameter",
                    "THS market heat period must be hour or day.",
                )
            params = {
                "stock_type": "a",
                "type": str(period),
                "list_type": "normal",
            }
            locator_uri = f"{THS_MARKET_HEAT_URL}?{urlencode(params)}"
            payload, source_response, diagnostics = _request_json(
                self.operation_id,
                self._transport,
                self._request_gate,
                locator_uri,
                {"User-Agent": USER_AGENT},
            )
            retrieved_on = _retrieved_on(source_response)
            if retrieved_on != requested_on:
                raise _SourceError(
                    "source_date_mismatch",
                    "The current market-heat list cannot answer a historical date.",
                    diagnostics=diagnostics,
                )
            data = payload.get("data")
            if not isinstance(data, dict) or "stock_list" not in data:
                raise _SourceError(
                    "unknown_schema",
                    "The THS market-heat list is missing.",
                    diagnostics=diagnostics,
                )
            rows = data.get("stock_list")
            if not isinstance(rows, list):
                raise _SourceError(
                    "unknown_schema",
                    "The THS market-heat list is invalid.",
                    diagnostics=diagnostics,
                )
            observations: list[MarketSignalObservation] = []
            seen_codes: set[str] = set()
            seen_ranks: set[int] = set()
            previous_rank = 0
            for value in rows:
                if not isinstance(value, dict):
                    raise _SourceError(
                        "unknown_schema",
                        "A THS market-heat row is invalid.",
                        diagnostics=diagnostics,
                    )
                rank = _required_nonnegative_int(value.get("order"), "rank")
                if rank < 1:
                    raise _SourceError(
                        "unknown_schema",
                        "The THS market-heat rank is invalid.",
                        diagnostics=diagnostics,
                    )
                code = _required_text(value.get("code"), "security code")
                if not (len(code) == 6 and code.isdigit()):
                    raise _SourceError(
                        "unknown_schema",
                        "The THS market-heat security code is invalid.",
                        diagnostics=diagnostics,
                    )
                if code in seen_codes or rank in seen_ranks:
                    raise _SourceError(
                        "duplicate_records",
                        "The THS market-heat response duplicates a security or rank.",
                        diagnostics=diagnostics,
                    )
                if rank <= previous_rank:
                    raise _SourceError(
                        "unexpected_sort_order",
                        "The THS market-heat response is not ranked ascending.",
                        diagnostics=diagnostics,
                    )
                seen_codes.add(code)
                seen_ranks.add(rank)
                previous_rank = rank
                name = _required_text(value.get("name"), "security name")
                tag = value.get("tag")
                if tag is None:
                    tag = {}
                if not isinstance(tag, dict):
                    raise _SourceError(
                        "unknown_schema",
                        "The THS market-heat tag object is invalid.",
                        diagnostics=diagnostics,
                    )
                concepts = tag.get("concept_tag") or []
                if (
                    not isinstance(concepts, list)
                    or any(
                        not isinstance(item, str) or not item.strip()
                        for item in concepts
                    )
                    or len(set(concepts)) != len(concepts)
                ):
                    raise _SourceError(
                        "unknown_schema",
                        "The THS market-heat concept tags are invalid.",
                        diagnostics=diagnostics,
                    )
                popularity_tag = tag.get("popularity_tag", "")
                if not isinstance(popularity_tag, str):
                    raise _SourceError(
                        "unknown_schema",
                        "The THS popularity tag is invalid.",
                        diagnostics=diagnostics,
                    )
                document_id = (
                    f"ths-market-heat-{period}-{requested_on.replace('-', '')}-{code}"
                )
                attributions = tuple(
                    ThemeAttribution(
                        text=concept.strip(),
                        provenance="market_signal",
                        source_operation=self.operation_id,
                        source_document_id=document_id,
                        locator_uri=locator_uri,
                    )
                    for concept in concepts
                )
                observations.append(
                    MarketSignalObservation(
                        signal_type=signal_type,
                        source_operation=self.operation_id,
                        source_role="market_signal",
                        subject=None,
                        source_document_id=document_id,
                        observed_on=requested_on,
                        observed_at=None,
                        available_at=None,
                        retrieved_at=source_response.retrieved_at,
                        period={
                            "start": requested_on,
                            "end": requested_on,
                            "frequency": f"current_{period}_ranking",
                        },
                        metrics={
                            "rank": str(rank),
                            "heat": _optional_decimal(value.get("rate"), "heat"),
                            "change_rate": _optional_decimal(
                                value.get("rise_and_fall"), "change rate"
                            ),
                            "rank_change": _optional_decimal(
                                value.get("hot_rank_chg"), "rank change"
                            ),
                        },
                        units={
                            "rank": "rank",
                            "heat": "provider_score",
                            "change_rate": "percent",
                            "rank_change": "rank",
                        },
                        directions={
                            "rank": "lower_is_more_popular",
                            "heat": "higher_is_more_popular",
                            "change_rate": "positive_is_gain",
                            "rank_change": "provider_defined",
                        },
                        rule={
                            "code": "provider_popularity_rank",
                            "period": period,
                        },
                        attributions=attributions,
                        dimensions={
                            "market_scope": "mainland_a_share",
                            "provider_security_code": code,
                            "provider_security_name": name,
                            "popularity_tag": popularity_tag,
                        },
                        locator_uri=locator_uri,
                        limitations=(
                            "ranking_observation_time_not_exposed",
                            "provider_total_not_exposed",
                            "security_exchange_unverified",
                            "availability_time_unknown",
                        ),
                    )
                )
            state = "observed_nonempty" if rows else "observed_empty"
            return SignalSourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations[: query.limit]),
                coverage={
                    signal_type: SignalCoverage(
                        state=state,
                        pages_collected=1,
                        pages_expected=1,
                    )
                },
                degradations=_degradations(self.operation_id, diagnostics),
                limitations=("provider_total_not_exposed",),
            )
        except _SourceError as error:
            return _failed_batch(self.operation_id, signal_type, error)
