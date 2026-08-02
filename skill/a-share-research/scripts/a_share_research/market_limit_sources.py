"""Experimental limit-state and limit-reason market signal operations."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
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
    ThemeAttribution,
)
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
USER_AGENT = "Mozilla/5.0 (compatible; a-share-research-skill/0.1)"
EASTMONEY_TOKEN = "7eea3edcaed734bea9cbfc24409ed989"
THS_LIMIT_REQUEST_GATE = SerialRequestGate()

_EASTMONEY_POOLS = {
    "limit_up": ("getTopicZTPool", "fbt:asc"),
    "limit_break": ("getTopicZBPool", "fbt:asc"),
    "limit_down": ("getTopicDTPool", "fund:asc"),
    "previous_limit_up": ("getYesterdayZTPool", "zs:desc"),
}


class EastmoneyLimitStateOperation:
    """Collect complete dated Eastmoney limit-state pools."""

    operation_id = "eastmoney_limit_state@1"
    supported_signal_types = frozenset({"limit_state"})

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        if "limit_state" not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        if query.subject is not None:
            return _failed(
                self.operation_id, "invalid_subject", "Limit pools are market-wide."
            )
        if query.observed_from != query.observed_to:
            return _failed(
                self.operation_id,
                "invalid_window",
                "Limit-state sources require one explicit trading date.",
            )
        states = query.parameters.get("limit_states")
        if states is None:
            selected_states = tuple(_EASTMONEY_POOLS)
        elif (
            not isinstance(states, list)
            or not states
            or any(
                not isinstance(item, str) or item not in _EASTMONEY_POOLS
                for item in states
            )
            or len(set(states)) != len(states)
        ):
            return _failed(
                self.operation_id,
                "invalid_parameters",
                "limit_states must be a unique non-empty list of supported states.",
            )
        else:
            selected_states = tuple(states)

        observations: list[MarketSignalObservation] = []
        errors: list[SignalSourceFailure] = []
        degradations: list[SignalSourceFailure] = []
        totals = 0
        completed_states = 0
        exact_duplicates = 0
        seen_current_states: dict[tuple[str, str], str] = {}
        for state in selected_states:
            endpoint, sort = _EASTMONEY_POOLS[state]
            params = {
                "ut": EASTMONEY_TOKEN,
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "10000",
                "sort": sort,
                "date": query.observed_to.replace("-", ""),
            }
            locator = f"https://push2ex.eastmoney.com/{endpoint}?{urlencode(params)}"
            try:
                response, diagnostics = self._request_gate.run(
                    partial(
                        self._transport.get,
                        locator,
                        {
                            "User-Agent": USER_AGENT,
                            "Referer": "https://quote.eastmoney.com/",
                        },
                    )
                )
            except RequestGateError as gate_error:
                degradations.extend(
                    _gate_diagnostic(self.operation_id, item)
                    for item in gate_error.diagnostics
                )
                cause = gate_error.cause
                if not isinstance(cause, TransportError):
                    raise
                errors.append(
                    SignalSourceFailure(self.operation_id, cause.code, str(cause))
                )
                continue
            except TransportError as error:
                errors.append(
                    SignalSourceFailure(self.operation_id, error.code, str(error))
                )
                continue
            degradations.extend(
                _gate_diagnostic(self.operation_id, item) for item in diagnostics
            )
            payload = _json_object(response)
            if payload is None:
                errors.append(
                    SignalSourceFailure(
                        self.operation_id,
                        "unknown_schema",
                        "The Eastmoney limit-state response is not a JSON object.",
                    )
                )
                continue
            data = payload.get("data")
            if data is None:
                errors.append(
                    SignalSourceFailure(
                        self.operation_id,
                        "non_trading_day_or_invalid_date",
                        "The provider returned null data and did not establish an empty trading-day pool.",
                        {"pool_state": state, "requested_date": query.observed_to},
                    )
                )
                continue
            if not isinstance(data, dict) or not isinstance(data.get("pool"), list):
                errors.append(
                    SignalSourceFailure(
                        self.operation_id,
                        "unknown_schema",
                        "The Eastmoney limit-state pool does not match the expected schema.",
                        {"pool_state": state},
                    )
                )
                continue
            total = data.get("tc")
            rows = data["pool"]
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                errors.append(
                    SignalSourceFailure(
                        self.operation_id,
                        "unknown_schema",
                        "The Eastmoney limit-state response lacks a valid provider total.",
                        {"pool_state": state},
                    )
                )
                continue
            if total != len(rows):
                errors.append(
                    SignalSourceFailure(
                        self.operation_id,
                        "pagination_incomplete",
                        "The provider total does not match the complete large-page pool.",
                        {
                            "pool_state": state,
                            "provider_total": total,
                            "received": len(rows),
                        },
                    )
                )
                continue
            normalized = _normalize_eastmoney_rows(
                rows,
                state=state,
                observed_on=query.observed_to,
                response=response,
                locator_uri=locator,
                operation_id=self.operation_id,
            )
            if normalized is None:
                errors.append(
                    SignalSourceFailure(
                        self.operation_id,
                        "unknown_schema",
                        "An Eastmoney limit-state row does not match the expected schema.",
                        {"pool_state": state},
                    )
                )
                continue
            unique_normalized: dict[tuple[str, str, str], MarketSignalObservation] = {}
            duplicate_conflict = False
            for observation in normalized:
                provider_market = str(observation.dimensions.get("provider_market"))
                provider_code = str(observation.dimensions["provider_security_code"])
                duplicate_key = (state, provider_market, provider_code)
                previous = unique_normalized.get(duplicate_key)
                if previous is not None:
                    if previous != observation:
                        duplicate_conflict = True
                        break
                    exact_duplicates += 1
                    continue
                unique_normalized[duplicate_key] = observation
                if state in {"limit_up", "limit_break", "limit_down"}:
                    security_key = (provider_market, provider_code)
                    previous_state = seen_current_states.get(security_key)
                    if previous_state is not None and previous_state != state:
                        duplicate_conflict = True
                        break
                    seen_current_states[security_key] = state
            if duplicate_conflict:
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={"limit_state": SignalCoverage(state="indeterminate")},
                    source_errors=(
                        SignalSourceFailure(
                            self.operation_id,
                            "duplicate_source_conflict",
                            "Duplicate provider securities disagree within mutually exclusive limit pools.",
                        ),
                    ),
                    degradations=tuple(degradations),
                )
            completed_states += 1
            totals += total
            observations.extend(unique_normalized.values())

        if errors and completed_states:
            coverage_state = "partial"
        elif errors:
            coverage_state = "indeterminate"
        elif observations:
            coverage_state = "observed_nonempty"
        else:
            coverage_state = "observed_empty"
        return SignalSourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            coverage={
                "limit_state": SignalCoverage(
                    state=coverage_state,
                    provider_total=totals if completed_states else None,
                    pages_collected=completed_states,
                    pages_expected=len(selected_states),
                    details={"pool_states": list(selected_states)},
                )
            },
            source_errors=tuple(errors),
            degradations=tuple(degradations),
            limitations=("exact_duplicate_rows_removed",) if exact_duplicates else (),
        )


class ThsLimitReasonOperation:
    """Collect attributed editorial limit-up reasons from 10jqka."""

    operation_id = "ths_limit_reason@1"
    supported_signal_types = frozenset({"limit_state"})
    endpoint = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"

    def __init__(
        self,
        transport: MarketSignalHttpTransport,
        *,
        page_size: int = 200,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 500:
            raise ValueError("THS limit-reason page size is invalid")
        self._transport = transport
        self._page_size = page_size
        self._request_gate = request_gate or THS_LIMIT_REQUEST_GATE

    def is_applicable(self, query: MarketSignalQuery) -> bool:
        """Return whether the selected pool states include limit-up reasons."""

        requested_states = query.parameters.get("limit_states")
        return not isinstance(requested_states, list) or "limit_up" in requested_states

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        if "limit_state" not in query.signal_types:
            return SignalSourceBatch(operation_id=self.operation_id)
        if not self.is_applicable(query):
            return SignalSourceBatch(operation_id=self.operation_id)
        if query.subject is not None or query.observed_from != query.observed_to:
            return _failed(
                self.operation_id,
                "invalid_window_or_subject",
                "THS limit reasons require a market-wide, single-date request.",
            )
        params = {
            "page": "1",
            "limit": str(self._page_size),
            "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
            "filter": "HS,GEM2STAR",
            "order_field": "330324",
            "order_type": "0",
            "date": query.observed_to.replace("-", ""),
        }
        locator = f"{self.endpoint}?{urlencode(params)}"
        try:
            response, diagnostics = self._request_gate.run(
                partial(
                    self._transport.get,
                    locator,
                    {"User-Agent": USER_AGENT},
                )
            )
        except RequestGateError as gate_error:
            cause = gate_error.cause
            if not isinstance(cause, TransportError):
                raise
            return _append_gate_degradations(
                _failed(self.operation_id, cause.code, str(cause)),
                gate_error.diagnostics,
            )
        except TransportError as error:
            return _failed(self.operation_id, error.code, str(error))
        payload = _json_object(response)
        if payload is None:
            return _append_gate_degradations(
                _failed(
                    self.operation_id,
                    "unknown_schema",
                    "The THS limit-reason response is not a JSON object.",
                ),
                diagnostics,
            )
        status = payload.get("status_code")
        if status is not None and status != 0:
            return _append_gate_degradations(
                _failed(
                    self.operation_id,
                    "provider_error",
                    "The THS limit-reason source reported an unsuccessful business status.",
                ),
                diagnostics,
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("info"), list):
            return _append_gate_degradations(
                _failed(
                    self.operation_id,
                    "unknown_schema",
                    "The THS limit-reason response lacks the expected info list.",
                ),
                diagnostics,
            )
        rows = data["info"]
        if not rows:
            explicit_total = data.get("total", data.get("count"))
            if explicit_total != 0:
                return _append_gate_degradations(
                    _failed(
                        self.operation_id,
                        "empty_response_unverified",
                        "The source returned no reason rows without proving an empty pool.",
                    ),
                    diagnostics,
                )
            return _append_gate_degradations(
                SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        "limit_state": SignalCoverage(
                            state="observed_empty",
                            provider_total=0,
                            pages_collected=1,
                            pages_expected=1,
                        )
                    },
                ),
                diagnostics,
            )
        observations = _normalize_ths_rows(
            rows,
            observed_on=query.observed_to,
            response=response,
            locator_uri=locator,
            operation_id=self.operation_id,
        )
        if observations is None:
            return _append_gate_degradations(
                _failed(
                    self.operation_id,
                    "unknown_schema",
                    "A THS limit-reason row does not match the expected schema.",
                ),
                diagnostics,
            )
        explicit_total = data.get("total")
        if explicit_total is not None and (
            isinstance(explicit_total, bool)
            or not isinstance(explicit_total, int)
            or explicit_total < len(rows)
        ):
            return _append_gate_degradations(
                _failed(
                    self.operation_id,
                    "unknown_schema",
                    "The THS limit-reason provider total is invalid.",
                ),
                diagnostics,
            )
        is_partial = explicit_total is None or explicit_total > len(rows)
        pages_expected = (
            (explicit_total + self._page_size - 1) // self._page_size
            if isinstance(explicit_total, int)
            else None
        )
        limitations = (
            ("provider_total_not_exposed", "pagination_incomplete")
            if explicit_total is None
            else (("pagination_incomplete",) if is_partial else ())
        )
        return _append_gate_degradations(
            SignalSourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations),
                coverage={
                    "limit_state": SignalCoverage(
                        state="partial" if is_partial else "observed_nonempty",
                        provider_total=explicit_total,
                        pages_collected=1,
                        pages_expected=pages_expected,
                    )
                },
                limitations=limitations,
            ),
            diagnostics,
        )


def _normalize_eastmoney_rows(
    rows: list[Any],
    *,
    state: str,
    observed_on: str,
    response: HttpResponse,
    locator_uri: str,
    operation_id: str,
) -> list[MarketSignalObservation] | None:
    observations: list[MarketSignalObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        code = _required_code(row.get("c"))
        name = _required_text(row.get("n"))
        if code is None or name is None:
            return None
        metrics = _eastmoney_metrics(row, state)
        if metrics is None:
            return None
        units = {key: _metric_unit(key) for key in metrics}
        directions = {key: _metric_direction(key) for key in metrics}
        dimensions: dict[str, Any] = {
            "market_scope": "provider_a_share_limit_pool",
            "pool_state": state,
            "provider_security_code": code,
            "provider_security_name": name,
            "provider_market": row.get("m"),
            "industry_label": row.get("hybk"),
        }
        for key, source_key in (
            ("first_seal_time", "fbt"),
            ("last_seal_time", "lbt"),
            ("previous_first_seal_time", "yfbt"),
        ):
            if row.get(source_key) is not None:
                formatted = _clock_text(row[source_key])
                if formatted is None:
                    return None
                dimensions[key] = formatted
        statistic = row.get("zttj")
        if statistic is not None:
            if not isinstance(statistic, dict):
                return None
            dimensions["statistic_window_days"] = statistic.get("days")
            dimensions["statistic_limit_count"] = statistic.get("ct")
        observations.append(
            MarketSignalObservation(
                signal_type="limit_state",
                source_operation=operation_id,
                source_role="market_signal",
                subject=None,
                source_document_id=f"{state}:{observed_on}:{code}",
                observed_on=observed_on,
                observed_at=None,
                available_at=response.retrieved_at.isoformat(),
                retrieved_at=response.retrieved_at,
                period={
                    "start": observed_on,
                    "end": observed_on,
                    "frequency": "trading_day",
                },
                metrics=metrics,
                units=units,
                directions=directions,
                rule=None,
                attributions=(),
                dimensions=dimensions,
                locator_uri=locator_uri,
                limitations=(
                    "security_exchange_unverified",
                    "source_metric_is_provider_derived",
                ),
            )
        )
    return observations


def _eastmoney_metrics(row: dict[str, Any], state: str) -> dict[str, str | None] | None:
    field_map: dict[str, tuple[str, Decimal]] = {
        "price": ("p", Decimal("0.001")),
        "change_rate": ("zdp", Decimal(1)),
        "turnover_rate": ("hs", Decimal(1)),
    }
    optional_map: dict[str, tuple[str, Decimal]] = {
        "amount": ("amount", Decimal(1)),
        "float_market_cap": ("ltsz", Decimal(1)),
        "seal_fund": ("fund", Decimal(1)),
        "limit_price": ("ztp", Decimal("0.001")),
        "amplitude": ("zf", Decimal(1)),
        "speed": ("zs", Decimal(1)),
        "pe": ("pe", Decimal(1)),
        "board_amount": ("fba", Decimal(1)),
    }
    metrics: dict[str, str | None] = {}
    for result_key, (source_key, scale) in field_map.items():
        value = _decimal_text(row.get(source_key), scale)
        if value is None:
            return None
        metrics[result_key] = value
    for result_key, (source_key, scale) in optional_map.items():
        metrics[result_key] = _decimal_text(row.get(source_key), scale, optional=True)
    integer_fields = {
        "consecutive_limit_days": "lbc",
        "break_times": "zbc",
        "consecutive_limit_down_days": "days",
        "open_times": "oc",
        "previous_consecutive_limit_days": "ylbc",
    }
    for result_key, source_key in integer_fields.items():
        value = row.get(source_key)
        if value is None:
            metrics[result_key] = None
        elif isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        else:
            try:
                parsed = int(value)
            except ValueError:
                return None
            if parsed < 0:
                return None
            metrics[result_key] = str(parsed)
    if state == "limit_up" and metrics["consecutive_limit_days"] is None:
        return None
    return metrics


def _normalize_ths_rows(
    rows: list[Any],
    *,
    observed_on: str,
    response: HttpResponse,
    locator_uri: str,
    operation_id: str,
) -> list[MarketSignalObservation] | None:
    observations: list[MarketSignalObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        code = _required_code(row.get("code"))
        name = _required_text(row.get("name"))
        reason = _required_text(row.get("reason_type"))
        price = _decimal_text(row.get("latest"), Decimal(1))
        change_rate = _decimal_text(row.get("change_rate"), Decimal(1))
        if (
            code is None
            or name is None
            or reason is None
            or price is None
            or change_rate is None
        ):
            return None
        first_time = _unix_clock_text(row.get("first_limit_up_time"), observed_on)
        if row.get("first_limit_up_time") is not None and first_time is None:
            return None
        metrics = {
            "price": price,
            "change_rate": change_rate,
            "seal_success_rate": _decimal_text(
                row.get("limit_up_suc_rate"), Decimal(1), optional=True
            ),
            "break_times": _integer_text(row.get("open_num"), optional=True),
            "seal_fund": _decimal_text(
                row.get("order_amount"), Decimal(1), optional=True
            ),
        }
        document_id = f"limit_up:{observed_on}:{code}"
        observations.append(
            MarketSignalObservation(
                signal_type="limit_state",
                source_operation=operation_id,
                source_role="market_signal",
                subject=None,
                source_document_id=document_id,
                observed_on=observed_on,
                observed_at=None,
                available_at=response.retrieved_at.isoformat(),
                retrieved_at=response.retrieved_at,
                period={
                    "start": observed_on,
                    "end": observed_on,
                    "frequency": "trading_day",
                },
                metrics=metrics,
                units={
                    "price": "CNY_per_share",
                    "change_rate": "percent",
                    "seal_success_rate": "ratio",
                    "break_times": "count",
                    "seal_fund": "CNY",
                },
                directions={
                    "price": "not_directional",
                    "change_rate": "positive_is_gain",
                    "seal_success_rate": "higher_is_more_successful",
                    "break_times": "higher_is_more_breaks",
                    "seal_fund": "higher_is_more_seal_fund",
                },
                rule=None,
                attributions=(
                    ThemeAttribution(
                        text=reason,
                        provenance="editorial_annotation",
                        source_operation=operation_id,
                        source_document_id=document_id,
                        locator_uri=locator_uri,
                    ),
                ),
                dimensions={
                    "market_scope": "provider_a_share_limit_pool",
                    "pool_state": "limit_up",
                    "provider_security_code": code,
                    "provider_security_name": name,
                    "board_type": row.get("limit_up_type"),
                    "high_days_label": row.get("high_days"),
                    "first_seal_time": first_time,
                    "provider_is_again_limit": row.get("is_again_limit"),
                },
                locator_uri=locator_uri,
                limitations=(
                    "security_exchange_unverified",
                    "editorial_reason_not_independently_verified",
                ),
            )
        )
    return observations


def _failed(operation_id: str, code: str, message: str) -> SignalSourceBatch:
    return SignalSourceBatch(
        operation_id=operation_id,
        coverage={"limit_state": SignalCoverage(state="indeterminate")},
        source_errors=(SignalSourceFailure(operation_id, code, message),),
    )


def _append_gate_degradations(
    batch: SignalSourceBatch,
    diagnostics: tuple[RequestGateDiagnostic, ...],
) -> SignalSourceBatch:
    return replace(
        batch,
        degradations=(
            *batch.degradations,
            *(
                _gate_diagnostic(batch.operation_id, diagnostic)
                for diagnostic in diagnostics
            ),
        ),
    )


def _json_object(response: HttpResponse) -> dict[str, Any] | None:
    if response.status != 200 or response.content_type.split(";", 1)[
        0
    ].strip().lower() not in {
        "application/json",
        "text/json",
        "text/plain",
    }:
        return None
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _gate_diagnostic(
    operation_id: str, diagnostic: RequestGateDiagnostic
) -> SignalSourceFailure:
    return SignalSourceFailure(
        operation_id, diagnostic.code, diagnostic.message, diagnostic.details()
    )


def _required_code(value: object) -> str | None:
    text = _required_text(value)
    if text is None or len(text) != 6 or not text.isascii() or not text.isdigit():
        return None
    return text


def _required_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal_text(
    value: object, scale: Decimal, *, optional: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        parsed = Decimal(str(value)) * scale
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _integer_text(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return str(parsed) if parsed >= 0 else None


def _clock_text(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value).zfill(6)
    if len(text) != 6 or not text.isdigit():
        return None
    hour, minute, second = int(text[:2]), int(text[2:4]), int(text[4:])
    if hour > 23 or minute > 59 or second > 59:
        return None
    return f"{text[:2]}:{text[2:4]}:{text[4:]}+08:00"


def _unix_clock_text(value: object, observed_on: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        timestamp = int(value)
        parsed = datetime.fromtimestamp(timestamp, tz=CHINA_STANDARD_TIME)
    except (ValueError, OSError, OverflowError):
        return None
    if parsed.date().isoformat() != observed_on:
        return None
    clock = parsed.time()
    if not time(9, 15) <= clock <= time(15, 30):
        return None
    return parsed.strftime("%H:%M:%S+08:00")


def _metric_unit(metric: str) -> str:
    if metric in {"price", "limit_price"}:
        return "CNY_per_share"
    if metric in {"amount", "float_market_cap", "seal_fund", "board_amount"}:
        return "CNY"
    if metric in {"change_rate", "turnover_rate", "amplitude", "speed"}:
        return "percent"
    if metric == "pe":
        return "ratio"
    return "count"


def _metric_direction(metric: str) -> str:
    return {
        "change_rate": "positive_is_gain",
        "turnover_rate": "higher_is_more_turnover",
        "seal_fund": "higher_is_more_seal_fund",
        "break_times": "higher_is_more_breaks",
        "open_times": "higher_is_more_opens",
        "speed": "positive_is_gain",
    }.get(metric, "not_directional")
