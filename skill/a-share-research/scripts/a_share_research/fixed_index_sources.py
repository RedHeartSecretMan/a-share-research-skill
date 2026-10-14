"""Bounded four-index acquisition using reviewed #121 K/L source bindings.

Source quote/update clocks are session evidence, never first-publication times.
Usage is limited to the documented personal noncommercial research conditions.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone, timedelta
from decimal import Context, Decimal, localcontext
from time import monotonic
from typing import Any, cast
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .fixed_index_observations import (
    CAPABILITY_ID,
    FIXED_INDEX_IDS,
    OPERATION_ID,
    FixedIndexOperationRequest,
    DeterministicFixedIndexAdapter,
    SessionAdapter,
    _decimal,
    _instant,
    _session_use,
    _IndexError,
)
from .market_session_context import (
    ASIA_SHANGHAI,
    CalendarDay,
    CalendarEvidence,
    DeterministicMarketSessionAdapter,
    MarketSessionOperationRequest,
)
from .identity_sources import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrlLibTransport,
)
from .research_result import (
    FieldDisposition,
    OperationExecution,
    OperationOutcome,
    OperationObligation,
    EvidenceUsePolicy,
)
from .market_session_sources import build_default_market_session_adapter

MAX_INDEX_RESPONSE_BYTES = 256 * 1024
_HEADERS = {"User-Agent": "a-share-research-skill/1", "Accept-Encoding": "identity"}
_NAMES = {
    "SSE:000001": "上证指数",
    "SZSE:399001": "深证成指",
    "SZSE:399006": "创业板指",
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Request | None:
        return None


class _IndexUrlLibTransport(UrlLibTransport):
    """Keep the existing provider lane while bounding each actual wire read."""

    def __init__(self) -> None:
        super().__init__()
        self._opener = build_opener(_NoRedirect())

    def _open_once(self, request: Request, *, timeout_seconds: float) -> HttpResponse:
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(MAX_INDEX_RESPONSE_BYTES + 1)
                if len(body) > MAX_INDEX_RESPONSE_BYTES:
                    raise TransportError(
                        "response_too_large",
                        "Index response exceeds its wire read bound.",
                    )
                return HttpResponse(
                    response.status,
                    response.headers.get_content_type(),
                    body,
                    datetime.now(timezone.utc),
                )
        except HTTPError as error:
            raise TransportError(
                "upstream_http_error",
                "Index source returned an unsuccessful HTTP status.",
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise TransportError(
                "upstream_unavailable", "Index source request could not complete."
            ) from error


class IndexSourceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProductionFixedIndexAdapter:
    """The operation Seam with explicit transport and trusted session injection.

    Only named source requests run; there is no retry, fallback or BSE alias.
    Daily dates retain day precision; quote/update clocks retain their own meaning.
    """

    def __init__(
        self,
        transport: HttpTransport | None,
        *,
        session_adapter: SessionAdapter,
        clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._transport = _IndexUrlLibTransport() if transport is None else transport
        self._sessions = session_adapter
        self._clock = clock
        self._monotonic = monotonic_clock

    @property
    def obligation(self) -> OperationObligation:
        return OperationObligation(
            CAPABILITY_ID,
            OPERATION_ID,
            "market_observation",
            False,
            None,
            (EvidenceUsePolicy(CAPABILITY_ID, "supporting"),),
        )

    @property
    def field_dispositions(self) -> tuple[FieldDisposition, ...]:
        return (
            FieldDisposition(
                "normalized_index_evidence",
                "normalized",
                "observations[].payload.payload.observation",
            ),
        )

    @property
    def request_budget(self) -> dict[str, object]:
        return {
            "index_requests": 4,
            "per_index": 1,
            "retry": 0,
            "redirect": 0,
            "maximum_active_requests": 1,
        }

    def execute(
        self, request: FixedIndexOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("Index timeout must be finite and bounded.")
        if isinstance(self._transport, UrlLibTransport) and not isinstance(
            self._transport, _IndexUrlLibTransport
        ):
            return OperationOutcome(
                CAPABILITY_ID,
                OPERATION_ID,
                OperationExecution("failed"),
                source_errors=(_error(None, "index_transport_policy_unproved"),),
                diagnostics={
                    "index_requests": 0,
                    "request_budget": self.request_budget,
                    "parsed_sources": [],
                },
            )
        deadline = self._monotonic() + timeout_seconds
        session = self._sessions.execute(request.session_request, timeout_seconds)
        observations = session.to_dict()["observations"]
        matching = [
            item
            for item in observations
            if item.get("data_type") == "market_session_context"
        ]
        errors: list[Mapping[str, object]] = []
        parsed: list[dict[str, object]] = []
        attempts = 0
        normalized_rows: list[Mapping[str, object]] = []
        if len(matching) != 1:
            errors.append(_error(None, "index_session_context_unproved"))
        else:
            session_payload = cast(dict[str, Any], matching[0]["payload"])
            try:
                use = _session_use(request, session_payload)
            except _IndexError as error:
                errors.append(_error(None, error.code))
            else:
                snapshot = use.current or (
                    use.latest_index
                    and session_payload["session"]["is_current_phase"]
                    and use.target_date
                    == session_payload["context"]["research_now"][:10]
                )
                for canonical_id in FIXED_INDEX_IDS:
                    uri = (
                        _current_uri(canonical_id)
                        if snapshot
                        else _history_uri(canonical_id)
                    )
                    if uri is None:
                        errors.append(
                            _error(canonical_id, "index_source_route_unproved")
                        )
                        continue
                    try:
                        remaining = deadline - self._monotonic()
                        if remaining <= 0:
                            raise IndexSourceError("index_source_timeout")
                        attempts += 1
                        response = self._fetch(uri, remaining)
                        if self._monotonic() > deadline:
                            raise IndexSourceError("index_source_timeout")
                        material = _json_response(
                            response, bse=canonical_id == "BSE:899050"
                        )
                        try:
                            if _instant(response.retrieved_at) > _instant(
                                self._clock()
                            ):
                                raise ValueError
                        except (ValueError, TypeError) as error:
                            raise IndexSourceError(
                                "index_timestamp_unproved"
                            ) from error
                        facts = (
                            _current_shape(material, canonical_id)
                            if snapshot
                            else _history_shape(material, canonical_id)
                        )
                        parsed.append(facts)
                        if snapshot:
                            row = _current_row(material, canonical_id, response, uri)
                            if not use.current:
                                row["price_semantics"] = (
                                    "completed_session_reported_value"
                                )
                            normalized_rows.append(row)
                        else:
                            normalized_rows.append(
                                _daily_row(
                                    material,
                                    canonical_id,
                                    response,
                                    uri,
                                    session_payload["context"],
                                    request,
                                    self._clock(),
                                    use.target_date,
                                )
                            )
                    except IndexSourceError as error:
                        errors.append(_error(canonical_id, error.code))
                    except (TransportError, TimeoutError, OSError):
                        errors.append(_error(canonical_id, "index_source_unavailable"))
        normalized = DeterministicFixedIndexAdapter(
            normalized_rows, session_adapter=_OwnedSession(session), clock=self._clock
        ).execute(request, max(timeout_seconds, 0.001))
        projected = normalized.to_dict()
        combined_errors = tuple(projected["source_errors"]) + tuple(errors)
        return OperationOutcome(
            CAPABILITY_ID,
            OPERATION_ID,
            OperationExecution("failed")
            if combined_errors
            else OperationExecution("succeeded", True),
            observations=tuple(projected["observations"]),
            source_errors=combined_errors,
            field_dispositions=normalized.field_dispositions,
            diagnostics={
                "index_requests": attempts,
                "request_budget": self.request_budget,
                "parsed_sources": parsed,
                "source_use_scope": "personal_noncommercial_research",
                "bse_use_basis": "existing_bounded_research_reading_authority",
                "general_api_cache_distribution_rights": "unproved",
            },
        )

    def _fetch(self, uri: str, remaining: float) -> HttpResponse:
        if isinstance(self._transport, _IndexUrlLibTransport):
            return self._transport.get_with_timeout(
                uri, dict(_HEADERS), min(remaining, 20.0)
            )
        return self._transport.get(uri, dict(_HEADERS))


def _error(canonical_id: str | None, code: str) -> dict[str, object]:
    return {
        "source_operation": OPERATION_ID,
        "code": code,
        "message": "Required index source evidence could not be proved."
        + (" Index: " + canonical_id + "." if canonical_id else ""),
    }


def _current_uri(canonical_id: str) -> str | None:
    if canonical_id == "SSE:000001":
        return "https://yunhq.sse.com.cn:32042/v1/sh1/snap/000001?" + urlencode(
            {
                "select": "name,prev_close,open,high,low,last,change,chg_rate,volume,amount,tradephase"
            }
        )
    if canonical_id in ("SZSE:399001", "SZSE:399006"):
        return "https://www.szse.cn/api/market/ssjjhq/getTimeData?" + urlencode(
            {"marketId": "1", "code": canonical_id.split(":")[1]}
        )
    if canonical_id == "BSE:899050":
        return "https://www.bse.cn/neeqRealController/getRealBZ50.do?%7B%7D"
    return None


def _reject_constant(value: str) -> object:
    raise ValueError("Nonfinite index response number.")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(items)
    if len(result) != len(items):
        raise ValueError("Duplicate index response field.")
    return result


def _json_response(response: HttpResponse, *, bse: bool = False) -> Any:
    if (
        not isinstance(response, HttpResponse)
        or response.status != 200
        or not isinstance(response.body, bytes)
        or len(response.body) > MAX_INDEX_RESPONSE_BYTES
        or not isinstance(response.content_type, str)
        or response.content_type.split(";", 1)[0].strip().casefold()
        not in (
            (
                "text/html",
                "application/json",
                "text/javascript",
                "application/javascript",
            )
            if bse
            else ("application/json",)
        )
    ):
        raise IndexSourceError("index_source_response_invalid")
    try:
        text = response.body.decode("utf-8-sig")
        decoder = json.JSONDecoder(
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs,
        )
        if bse:
            match = re.match(
                r"^(?:/\*\*/\s*)?[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\s*\(\s*",
                text.strip(),
            )
            if match is None or match.end() > 256:
                raise ValueError
            tail = text.strip()[match.end() :]
            material, end = decoder.raw_decode(tail)
            if tail[end:].strip() not in (")", ");"):
                raise ValueError
        else:
            material = decoder.decode(text)
        if not isinstance(material, (dict, list) if bse else dict):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError) as error:
        raise IndexSourceError("index_source_payload_invalid") from error
    return material


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise IndexSourceError("index_source_value_invalid")
    try:
        return _decimal(str(value))
    except ValueError as error:
        raise IndexSourceError("index_source_value_invalid") from error


def _current_shape(material: Any, canonical_id: str) -> dict[str, object]:
    if canonical_id == "BSE:899050":
        return _bse_shape(material, history=False)
    try:
        if canonical_id == "SSE:000001":
            if (
                set(material) != {"code", "date", "time", "snap"}
                or material["code"] != "000001"
            ):
                raise IndexSourceError("index_source_identity_mismatch")
            snapshot = material["snap"]
            if (
                not isinstance(snapshot, list)
                or len(snapshot) != 11
                or snapshot[0] != _NAMES[canonical_id]
            ):
                raise IndexSourceError("index_source_shape_invalid")
            for value in snapshot[1:10]:
                _number(value)
            if not isinstance(snapshot[10], str):
                raise IndexSourceError("index_source_shape_invalid")
            if type(material["date"]) is not int or type(material["time"]) is not int:
                raise IndexSourceError("index_source_shape_invalid")
            date_token, time_token = str(material["date"]), str(material["time"])
            if (
                len(date_token) != 8
                or not date_token.isascii()
                or not date_token.isdigit()
                or not 5 <= len(time_token) <= 6
                or not time_token.isascii()
                or not time_token.isdigit()
            ):
                raise IndexSourceError("index_source_shape_invalid")
            return {
                "canonical_id": canonical_id,
                "reported_source_date": date_token,
                "reported_source_time": time_token,
                "source_clock_semantics": "source_reported_quote_update",
            }
        if material["code"] != "0" or not isinstance(material["data"], dict):
            raise IndexSourceError("index_source_shape_invalid")
        data = material["data"]
        if (
            data["code"] != canonical_id.split(":")[1]
            or data["name"] != _NAMES[canonical_id]
        ):
            raise IndexSourceError("index_source_identity_mismatch")
        for field in ("now", "close", "open", "high", "low", "delta", "deltaPercent"):
            if not isinstance(data[field], str):
                raise IndexSourceError("index_source_shape_invalid")
            _number(data[field])
        for field in ("picupdata", "picdowndata"):
            if (
                not isinstance(data[field], list)
                or not data[field]
                or len(data[field]) > 512
            ):
                raise IndexSourceError("index_source_shape_invalid")
        if (
            not isinstance(data["marketTime"], str)
            or len(data["marketTime"]) > 64
            or not isinstance(material["datetime"], str)
            or len(material["datetime"]) > 64
        ):
            raise IndexSourceError("index_source_shape_invalid")
        if (
            re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}",
                data["marketTime"],
            )
            is None
        ):
            raise IndexSourceError("index_timestamp_unproved")
        return {
            "canonical_id": canonical_id,
            "reported_market_time": data["marketTime"],
            "reported_response_datetime": material["datetime"],
            "source_clock_semantics": "source_reported_quote_update",
        }
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, IndexSourceError):
            raise
        raise IndexSourceError("index_source_shape_invalid") from error


def _history_uri(canonical_id: str) -> str | None:
    if canonical_id == "SSE:000001":
        return "https://yunhq.sse.com.cn:32042/v1/sh1/dayk/000001?" + urlencode(
            {
                "select": "date,open,high,low,close,volume,amount",
                "begin": "-1000",
                "end": "-1",
            }
        )
    if canonical_id in ("SZSE:399001", "SZSE:399006"):
        return "https://www.szse.cn/api/market/ssjjhq/getHistoryData?" + urlencode(
            {"cycleType": "32", "marketId": "1", "code": canonical_id.split(":")[1]}
        )
    if canonical_id == "BSE:899050":
        return "https://www.bse.cn/neeqRealController/getBz50Kline.do?%7B%22type%22%3A%22dayKline%22%7D&type=dayKline"
    return None


def _source_day(value: object, *, compact: bool) -> str:
    try:
        if compact:
            if (
                isinstance(value, bool)
                or not isinstance(value, (str, int))
                or len(str(value)) != 8
                or not str(value).isascii()
                or not str(value).isdigit()
            ):
                raise ValueError
            parsed = datetime.strptime(str(value), "%Y%m%d").date()
        else:
            if not isinstance(value, str):
                raise ValueError
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
        return parsed.isoformat()
    except (ValueError, TypeError) as error:
        raise IndexSourceError("index_source_date_invalid") from error


def _history_shape(material: Any, canonical_id: str) -> dict[str, object]:
    if canonical_id == "BSE:899050":
        return _bse_shape(material, history=True)
    try:
        if canonical_id == "SSE:000001":
            if material["code"] != "000001":
                raise IndexSourceError("index_source_identity_mismatch")
            data = material["kline"]
            width = 7
            compact = True
        else:
            if material["code"] != "0" or not isinstance(material["data"], dict):
                raise IndexSourceError("index_source_shape_invalid")
            envelope = material["data"]
            if (
                envelope["code"] != canonical_id.split(":")[1]
                or envelope["name"] != _NAMES[canonical_id]
            ):
                raise IndexSourceError("index_source_identity_mismatch")
            data = envelope["picupdata"]
            volume_data = envelope["picdowndata"]
            if (
                not isinstance(volume_data, list)
                or not isinstance(data, list)
                or len(volume_data) != len(data)
            ):
                raise IndexSourceError("index_source_shape_invalid")
            width = 9
            compact = False
        if not isinstance(data, list) or not 0 < len(data) <= 1000:
            raise IndexSourceError("index_source_shape_invalid")
        seed_rows = 1 if not compact and len(data) > 200 else 0
        dates = []
        for row in data:
            if not isinstance(row, list) or len(row) != width:
                raise IndexSourceError("index_source_shape_invalid")
            dates.append(_source_day(row[0], compact=compact))
            for value in row[1:]:
                _number(value)
        if dates != sorted(set(dates)):
            raise IndexSourceError("index_source_history_conflict")
        return {
            "canonical_id": canonical_id,
            "source_date_precision": "day",
            "reference_seed_rows": seed_rows,
            "first_source_date": dates[seed_rows],
            "last_source_date": dates[-1],
            "source_rows": len(data) - seed_rows,
            "source_clock_semantics": "source_reported_daily_session",
        }
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, IndexSourceError):
            raise
        raise IndexSourceError("index_source_shape_invalid") from error


def _bse_shape(material: Any, *, history: bool) -> dict[str, object]:
    try:
        if not isinstance(material, list) or not 0 < len(material) <= (
            2000 if history else 512
        ):
            raise IndexSourceError("index_source_shape_invalid")
        days, clocks = [], []
        fields: tuple[str, ...]
        for row in material:
            if not isinstance(row, dict):
                raise IndexSourceError("index_source_shape_invalid")
            if history:
                if row["zsdm"] != "899050":
                    raise IndexSourceError("index_source_identity_mismatch")
                days.append(_source_day(row["jsrq"], compact=True))
                fields = (
                    "drkp",
                    "drsp",
                    "drzd",
                    "drzx",
                    "zrsp",
                    "zd",
                    "zdf",
                    "cjl",
                    "cjje",
                )
            else:
                days.append(_source_day(row["JSRQ"], compact=False))
                clock = row["GXSJ"]
                if (
                    not isinstance(clock, str)
                    or re.fullmatch(r"[0-9]{6}", clock) is None
                ):
                    raise IndexSourceError("index_source_shape_invalid")
                datetime.strptime(clock, "%H%M%S")
                clocks.append(days[-1] + " " + clock)
                fields = ("SSZS", "ZRSP", "ZD", "ZDF", "CJL", "CJJE")
            for field in fields:
                _number(row[field])
        if history:
            if days != sorted(set(days)):
                raise IndexSourceError("index_source_history_conflict")
            return {
                "canonical_id": "BSE:899050",
                "source_date_precision": "day",
                "first_source_date": days[0],
                "last_source_date": days[-1],
                "source_rows": len(material),
                "source_clock_semantics": "source_reported_daily_session",
            }
        if clocks != sorted(set(clocks)) or len(set(days)) != 1:
            raise IndexSourceError("index_source_history_conflict")
        return {
            "canonical_id": "BSE:899050",
            "reported_market_time": clocks[-1],
            "source_rows": len(material),
            "source_clock_semantics": "source_reported_quote_update",
        }
    except (KeyError, ValueError, TypeError) as error:
        if isinstance(error, IndexSourceError):
            raise
        raise IndexSourceError("index_source_shape_invalid") from error


class _OwnedSession:
    """Internal handoff of the actual #117 result acquired by this owner.

    No caller-provided outcome enters the operation request; normalization uses
    the same original session result and performs no second document acquisition.
    """

    def __init__(self, outcome: OperationOutcome) -> None:
        self._outcome = outcome

    def execute(self, request: Any, timeout_seconds: float) -> OperationOutcome:
        return self._outcome


def _scaled_percent(value: object) -> str:
    numeric = _number(value)
    parts = numeric.as_tuple()
    return str(Decimal((parts.sign, parts.digits, int(parts.exponent) + 2)))


def _current_row(
    material: Any, canonical_id: str, response: HttpResponse, uri: str
) -> dict[str, object]:
    try:
        if canonical_id == "SSE:000001":
            quote = material["snap"]
            fields = {
                "last": str(_number(quote[5])),
                "prev_close": str(_number(quote[1])),
                "change": str(_number(quote[6])),
                "chg_rate": str(_number(quote[7])),
            }
            day = _source_day(material["date"], compact=True)
            source_time = str(material["time"]).zfill(6)
            instant = datetime.strptime(
                day + " " + source_time, "%Y-%m-%d %H%M%S"
            ).replace(tzinfo=ASIA_SHANGHAI)
            value, previous, delta, pct = (
                fields[key] for key in ("last", "prev_close", "change", "chg_rate")
            )
            clock_fields = {
                "date": str(material["date"]),
                "time": str(material["time"]),
            }
            baseline = {"field": "prev_close", "array_index": 1}
            version = "sse-index-097f290ec5c3fcad"
        elif canonical_id.startswith("SZSE:"):
            quote = material["data"]
            fields = {
                key: str(_number(quote[key]))
                for key in ("now", "close", "delta", "deltaPercent")
            }
            instant = datetime.fromisoformat(quote["marketTime"])
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=ASIA_SHANGHAI)
            instant = _instant(instant)
            value, previous, delta, pct = (
                fields[key] for key in ("now", "close", "delta", "deltaPercent")
            )
            day = instant.date().isoformat()
            clock_fields = {"marketTime": quote["marketTime"]}
            baseline = {"field": "close"}
            version = "szse-k-246926d9c3d4e16e"
        else:
            first, quote = material[0], material[-1]
            previous_value = _number(first["ZRSP"])
            if any(_number(row["ZRSP"]) != previous_value for row in material):
                raise IndexSourceError("index_source_baseline_conflict")
            fields = {key: str(_number(quote[key])) for key in ("SSZS", "ZD", "ZDF")}
            fields["ZRSP"] = str(previous_value)
            day = _source_day(quote["JSRQ"], compact=False)
            instant = datetime.strptime(
                day + " " + quote["GXSJ"], "%Y-%m-%d %H%M%S"
            ).replace(tzinfo=ASIA_SHANGHAI)
            value, previous, delta, pct = (
                fields["SSZS"],
                fields["ZRSP"],
                fields["ZD"],
                _scaled_percent(quote["ZDF"]),
            )
            clock_fields = {"JSRQ": quote["JSRQ"], "GXSJ": quote["GXSJ"]}
            baseline = {"field": "ZRSP", "row": 0}
            version = "bse-bz50-061f2bdabb5cdf20"
        return {
            "canonical_id": canonical_id,
            "value": value,
            "previous_close": previous,
            "change": delta,
            "change_percent": pct,
            "unit": "index_point",
            "price_semantics": "current_value",
            "baseline_semantics": "previous_session_close",
            "session_date": day,
            "observed_at": instant.isoformat(),
            "retrieved_at": _instant(response.retrieved_at).isoformat(),
            "provenance_uri": uri,
            "source_version": version,
            "synthetic": False,
            "arithmetic_basis": "source_reported",
            "source_time_precision": "second",
            "source_time_semantics": "source_reported_quote_update",
            "source_field_values": fields,
            "source_clock_fields": clock_fields,
            "baseline_provenance": baseline,
        }
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        if isinstance(error, IndexSourceError):
            raise
        raise IndexSourceError("index_timestamp_unproved") from error


def _previous_session(
    context: dict[str, Any],
    target: str,
    request: FixedIndexOperationRequest,
    checked: datetime,
) -> str:
    # Derive through the owning #117 Interface using its already-proved calendar
    # values. This pure lookup performs no source call and never replaces saved C.
    calendars = []
    for proof in context["calendar_proofs"]:
        material = dict(proof)
        material["days"] = tuple(CalendarDay(**day) for day in proof["days"])
        material["auction_times"] = tuple(proof["auction_times"])
        calendars.append(CalendarEvidence(**material))
    before = (date.fromisoformat(target) - timedelta(days=1)).isoformat()
    original_clock = _instant(context["research_now"])
    result = DeterministicMarketSessionAdapter(
        calendars,
        clock=lambda: original_clock,
        execution_clock=lambda: _instant(checked),
    ).execute(
        MarketSessionOperationRequest(before, request.session_request.bindings), 1
    )
    matches = [
        row
        for row in result.to_dict()["observations"]
        if row.get("data_type") == "market_session_context"
    ]
    if len(matches) != 1:
        raise IndexSourceError("index_source_baseline_unproved")
    return str(cast(dict[str, Any], matches[0]["payload"])["context"]["screen_date"])


def _daily_row(
    material: Any,
    canonical_id: str,
    response: HttpResponse,
    uri: str,
    context: dict[str, Any],
    request: FixedIndexOperationRequest,
    checked: datetime,
    target: str,
) -> dict[str, object]:
    derived: dict[str, object] = {}
    if canonical_id == "BSE:899050":
        matches = [
            (index, row)
            for index, row in enumerate(material)
            if _source_day(row["jsrq"], compact=True) == target
        ]
        if len(matches) != 1:
            raise IndexSourceError("index_source_session_missing")
        index, quote = matches[0]
        fields = {
            key: str(_number(quote[key])) for key in ("drsp", "zrsp", "zd", "zdf")
        }
        value, previous, delta, pct = (
            fields["drsp"],
            fields["zrsp"],
            fields["zd"],
            _scaled_percent(quote["zdf"]),
        )
        baseline = {"field": "zrsp", "row": index}
        clock_fields = {"jsrq": quote["jsrq"]}
        version = "bse-bz50-061f2bdabb5cdf20"
    else:
        compact = canonical_id == "SSE:000001"
        data = material["kline"] if compact else material["data"]["picupdata"]
        matched_indices = [
            index
            for index, row in enumerate(data)
            if _source_day(row[0], compact=compact) == target
        ]
        seed_rows = 1 if not compact and len(data) > 200 else 0
        if len(matched_indices) != 1 or matched_indices[0] < max(1, seed_rows):
            raise IndexSourceError("index_source_baseline_unproved")
        index = matched_indices[0]
        quote, earlier = data[index], data[index - 1]
        if _source_day(earlier[0], compact=compact) != _previous_session(
            context, target, request, checked
        ):
            raise IndexSourceError("index_source_baseline_conflict")
        close_cell = 4 if compact else 2
        value, previous = (
            str(_number(quote[close_cell])),
            str(_number(earlier[close_cell])),
        )
        fields = {"close": value, "previous_record_close": previous}
        if compact:
            with localcontext(Context(prec=512)):
                delta = str(_number(value) - _number(previous))
            with localcontext(Context(prec=64)):
                if _number(previous) <= 0:
                    raise IndexSourceError("index_source_value_invalid")
                pct = str(_number(delta) * 100 / _number(previous))
            derived = {
                "change": {
                    "formula": "close - previous_record_close",
                    "precision": 512,
                },
                "change_percent": {
                    "formula": "100 * change / previous_record_close",
                    "precision": 64,
                },
            }
        else:
            delta, pct = str(_number(quote[5])), str(_number(quote[6]))
            fields.update(delta=delta, deltaPercent=pct)
        baseline = {
            "field": "close",
            "row": index - 1,
            "session_date": _source_day(earlier[0], compact=compact),
            "role": "reference_seed"
            if seed_rows and index == 1
            else "previous_session_record",
        }
        clock_fields = {"date": str(quote[0])}
        version = "sse-index-097f290ec5c3fcad" if compact else "szse-k-246926d9c3d4e16e"
    row: dict[str, object] = {
        "canonical_id": canonical_id,
        "value": value,
        "previous_close": previous,
        "change": delta,
        "change_percent": pct,
        "unit": "index_point",
        "price_semantics": "completed_close",
        "baseline_semantics": "previous_session_close",
        "session_date": target,
        "observed_at": None,
        "retrieved_at": _instant(response.retrieved_at).isoformat(),
        "provenance_uri": uri,
        "source_version": version,
        "synthetic": False,
        "arithmetic_basis": "source_reported",
        "source_time_precision": "day",
        "source_time_semantics": "source_reported_daily_session",
        "source_field_values": fields,
        "source_clock_fields": clock_fields,
        "baseline_provenance": baseline,
    }
    if derived:
        row["derived_metrics"] = derived
    return row


def build_default_fixed_index_adapter(
    transport: HttpTransport | None = None,
    *,
    session_adapter: SessionAdapter | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] = monotonic,
) -> ProductionFixedIndexAdapter:
    """Compose the two existing operation owners without making source calls."""
    checked = clock or (lambda: datetime.now(ASIA_SHANGHAI))
    sessions = (
        session_adapter
        if session_adapter is not None
        else build_default_market_session_adapter(
            clock=checked, execution_clock=checked
        )
    )
    return ProductionFixedIndexAdapter(
        transport,
        session_adapter=sessions,
        clock=checked,
        monotonic_clock=monotonic_clock,
    )
