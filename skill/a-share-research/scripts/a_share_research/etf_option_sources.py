"""Experimental Sina source operation for SSE ETF-option snapshots."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any, Iterable, TypeVar
from urllib.parse import urlencode

from .etf_market import SseEtfListOperation, SseEtfSnapshotOperation
from .etf_option_contract import (
    EtfOptionSubject,
    OptionAnalytic,
    OptionContractListingEvidence,
    OptionContractMonthEvidence,
    OptionContractQuote,
    OptionCoverage,
    OptionQuery,
    OptionSession,
    OptionSourceBatch,
    OptionSourceFailure,
)
from .identity_sources import (
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    TransportError,
)
from .source_throttle import (
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
SINA_OPTION_REQUEST_GATE = SerialRequestGate()
SINA_HEADERS = {
    "Referer": "https://stock.finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (compatible; a-share-research-skill/0.1)",
}
MONTHS_ENDPOINT = (
    "https://stock.finance.sina.com.cn/futures/api/openapi.php/"
    "StockOptionService.getStockName"
)
HQ_ENDPOINT = "https://hq.sinajs.cn/"
UNDERLYING_CATEGORIES = {
    "510050": "50ETF",
    "510300": "300ETF",
    "510500": "500ETF",
    "588000": "科创50",
}
_ASSIGNMENT = re.compile(r'^var hq_str_([A-Za-z0-9_]+)="(.*)";$')
_TRADE_CODE = re.compile(
    r"^(?P<underlying>\d{6})(?P<option_type>[CP])(?P<month>\d{4})"
    r"(?P<series>[MA])(?P<strike>\d{5})$"
)
T = TypeVar("T")


class _SourceError(Exception):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class SinaEtfOptionSnapshotOperation:
    """Collect one bounded option month with quotes and provider analytics."""

    operation_id = "sina_etf_option_snapshot@1"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        request_gate: RequestGate | None = None,
        batch_size: int = 2,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("Sina ETF-option batch size must be from 1 to 100")
        self._transport = transport
        self._request_gate = request_gate or SINA_OPTION_REQUEST_GATE
        self._batch_size = batch_size

    def collect(self, query: OptionQuery) -> OptionSourceBatch:
        subject: EtfOptionSubject | None = None
        session: OptionSession | None = None
        coverage = _indeterminate_coverage()
        degradations: list[OptionSourceFailure] = []
        listing_evidence: tuple[OptionContractListingEvidence, ...] = ()
        month_evidence: OptionContractMonthEvidence | None = None
        try:
            category = UNDERLYING_CATEGORIES.get(query.subject_clue)
            if category is None:
                raise _SourceError(
                    "unsupported_underlying",
                    "The ETF-option source does not support this underlying.",
                )
            identity = SseEtfListOperation().observe(
                query.subject_clue, self._transport
            )
            snapshot = SseEtfSnapshotOperation().observe(
                query.subject_clue, self._transport
            )
            subject = EtfOptionSubject(
                "SSE",
                identity.code,
                identity.name,
                identity_evidence_id=identity.evidence_id,
                identity_locator_uri=identity.source_uri,
                identity_retrieved_at=identity.retrieved_at,
                identity_observed_on=identity.listing_date.isoformat(),
            )
            if snapshot.code != identity.code:
                raise _SourceError(
                    "wrong_security_payload",
                    "The ETF identity and reference quote disagree.",
                )
            months_url = (
                f"{MONTHS_ENDPOINT}?{urlencode({'exchange': 'null', 'cate': category})}"
            )
            months_response = self._get(months_url, degradations)
            month_evidence = OptionContractMonthEvidence(
                source_operation=self.operation_id,
                evidence_id=f"option-months-sina-{query.subject_clue}",
                observed_months=(),
                identity_status="unvalidated",
                locator_uri=months_url,
                retrieved_at=months_response.retrieved_at,
            )
            months = _contract_months(
                months_response, expected_underlying=query.subject_clue
            )
            month_evidence = OptionContractMonthEvidence(
                source_operation=self.operation_id,
                evidence_id=f"option-months-sina-{query.subject_clue}",
                observed_months=months,
                identity_status="validated",
                locator_uri=months_url,
                retrieved_at=months_response.retrieved_at,
            )
            month = _select_month(query, months)
            if month is None:
                raise _SourceError(
                    "option_expiry_not_available",
                    "The requested option month is not available from the source.",
                )
            month_code = month[2:4] + month[5:7]
            call_codes, call_listing = self._contract_codes(
                query.subject_clue,
                month_code,
                contract_month=month,
                option_type="call",
                degradations=degradations,
            )
            put_codes, put_listing = self._contract_codes(
                query.subject_clue,
                month_code,
                contract_month=month,
                option_type="put",
                degradations=degradations,
            )
            rights = {"call": call_codes, "put": put_codes}
            listing_evidence = (call_listing, put_listing)
            code_rights: dict[str, str] = {}
            for option_type, codes in rights.items():
                for code in codes:
                    previous = code_rights.get(code)
                    if previous is not None and previous != option_type:
                        raise _SourceError(
                            "duplicate_contract_conflict",
                            "One provider contract appears as both call and put.",
                        )
                    code_rights[code] = option_type
            if not code_rights:
                raise _SourceError(
                    "empty_contract_list_unverified",
                    "The source did not establish a non-empty option contract list.",
                )
            coverage["contract_listing"] = OptionCoverage(
                "partial",
                observed_count=len(code_rights),
                details={
                    "contract_month": month,
                    "authoritative_total_available": False,
                    "evidence_ids": [item.evidence_id for item in listing_evidence],
                },
            )
            quote_rows, quote_locators, quote_retrieved = self._batched_rows(
                "CON_OP_", tuple(code_rights), degradations
            )
            analytic_rows, analytic_locators, analytic_retrieved = self._batched_rows(
                "CON_SO_", tuple(code_rights), degradations
            )
            contracts: list[OptionContractQuote] = []
            quote_times: set[datetime] = set()
            quote_times_by_code: dict[str, datetime] = {}
            no_quote_codes: list[str] = []
            for code, option_type in code_rights.items():
                quote = _parse_quote(
                    code,
                    quote_rows[code],
                    expected_underlying=query.subject_clue,
                    expected_option_type=option_type,
                    expected_month=month,
                    observed_on=query.observed_on,
                )
                if (
                    query.expiry_mode == "exact"
                    and quote["expiry_date"] != query.expiry_date
                ):
                    raise _SourceError(
                        "option_expiry_not_available",
                        "The provider contract expiry does not match the requested date.",
                    )
                analytics = _parse_analytics(
                    code,
                    analytic_rows[code],
                    expected_underlying=query.subject_clue,
                    expected_option_type=option_type,
                    expected_month=month,
                    quote=quote,
                )
                quote_times.add(quote["observed_at"])
                quote_times_by_code[code] = quote["observed_at"]
                if quote["quote_state"] == "no_quote":
                    no_quote_codes.append(code)
                contracts.append(
                    OptionContractQuote(
                        security={
                            "exchange": "SSE",
                            "code": code,
                            "type": "ETF_OPTION",
                        },
                        option_type=option_type,
                        strike=quote["strike"],
                        contract_month=month,
                        expiry_date=quote["expiry_date"],
                        series=quote["series"],
                        quote_state=quote["quote_state"],
                        last=quote["last"],
                        bid=quote["bid"],
                        ask=quote["ask"],
                        observed_at=quote["observed_at"].isoformat(),
                        analytics=analytics,
                        source_operation=self.operation_id,
                        evidence_id=(
                            f"option-quote-sina-{code}-{quote['observed_at'].strftime('%Y%m%dT%H%M%S%z')}"
                        ),
                        locator_uri=quote_locators[code],
                        limitations=tuple(
                            [
                                "provider_analytics_not_independently_verified",
                                "no_qualified_independent_fallback",
                            ]
                            + list(quote["limitations"])
                        ),
                        bid_size=quote["bid_size"],
                        ask_size=quote["ask_size"],
                        volume=quote["volume"],
                        open_interest=quote["open_interest"],
                        analytics_evidence_id=(
                            f"option-analytics-sina-{code}-{quote['observed_at'].strftime('%Y%m%dT%H%M%S%z')}"
                        ),
                        analytics_locator_uri=analytic_locators[code],
                        quote_retrieved_at=quote_retrieved[code],
                        analytics_retrieved_at=analytic_retrieved[code],
                    )
                )
            if len(quote_times) != 1:
                raise _SourceError(
                    "quote_time_conflict",
                    "Option contracts in one snapshot have different quote times.",
                    _quote_time_conflict_details(
                        operation_id=self.operation_id,
                        underlying=query.subject_clue,
                        contract_month=month,
                        quote_times_by_code=quote_times_by_code,
                        quote_locators=quote_locators,
                        quote_retrieved=quote_retrieved,
                    ),
                )
            observed_at = next(iter(quote_times))
            market_state = _market_state(observed_at.time())
            if market_state == "unknown":
                raise _SourceError(
                    "session_state_unknown",
                    "The option quote time does not establish an intraday or completed session.",
                )
            if query.quote_mode == "latest_completed" and market_state != "completed":
                raise _SourceError(
                    "session_not_completed",
                    "The option quote is intraday rather than a completed session.",
                )
            if snapshot.observed_at.date().isoformat() != query.observed_on:
                raise _SourceError(
                    "underlying_quote_date_mismatch",
                    "The ETF reference price does not match the option trading date.",
                )
            session = OptionSession(
                trading_date=query.observed_on,
                observed_at=observed_at.isoformat(),
                market_state=market_state,
                reference_price=snapshot.last_value,
                reference_price_kind="last",
                reference_evidence_id=snapshot.evidence_id,
                locator_uri=snapshot.source_uri,
                retrieved_at=max(
                    snapshot.retrieved_at,
                    months_response.retrieved_at,
                    *quote_retrieved.values(),
                    *analytic_retrieved.values(),
                ),
                reference_observed_at=snapshot.observed_at.isoformat(),
                reference_source_operation="sse_etf_snapshot@1",
                reference_retrieved_at=snapshot.retrieved_at,
            )
            quoted_count = len(contracts) - len(no_quote_codes)
            quote_state = "observed_nonempty" if not no_quote_codes else "partial"
            coverage["option_quotes"] = OptionCoverage(
                quote_state,
                expected_count=len(contracts),
                observed_count=quoted_count,
            )
            coverage["provider_analytics"] = OptionCoverage(
                "observed_nonempty",
                expected_count=len(contracts),
                observed_count=len(contracts),
            )
            source_errors = (
                (
                    OptionSourceFailure(
                        self.operation_id,
                        "no_quote",
                        "One or more option contracts have no usable market quote.",
                        {"contract_codes": sorted(no_quote_codes)},
                    ),
                )
                if no_quote_codes
                else ()
            )
            return OptionSourceBatch(
                operation_id=self.operation_id,
                subject=subject,
                session=session,
                contracts=tuple(contracts),
                coverage=coverage,
                source_errors=source_errors,
                degradations=tuple(_deduplicate_failures(degradations)),
                limitations=tuple(
                    [
                        "experimental_sina_option_source",
                        "provider_analytics_not_independently_verified",
                        "no_qualified_independent_fallback",
                        "contract_listing_authoritative_total_unavailable",
                        "contract_multiplier_not_exposed",
                    ]
                    + (
                        ["adjustment_terms_not_exposed"]
                        if any(item.series == "A" for item in contracts)
                        else []
                    )
                ),
                listing_evidence=listing_evidence,
                month_evidence=month_evidence,
            )
        except SourceOperationError as error:
            failure = OptionSourceFailure(
                self.operation_id,
                error.code,
                "The SSE ETF reference operation could not establish its contract.",
                {"basis_source_operation": error.source_operation},
            )
        except _SourceError as error:
            failure = OptionSourceFailure(
                self.operation_id, error.code, str(error), error.details
            )
        return OptionSourceBatch(
            operation_id=self.operation_id,
            subject=subject,
            session=session,
            coverage=coverage,
            source_errors=(failure,),
            degradations=tuple(_deduplicate_failures(degradations)),
            limitations=("experimental_sina_option_source",),
            listing_evidence=listing_evidence,
            month_evidence=month_evidence,
        )

    def _get(self, url: str, degradations: list[OptionSourceFailure]) -> HttpResponse:
        try:
            response, diagnostics = self._request_gate.run(
                partial(self._transport.get, url, SINA_HEADERS)
            )
        except RequestGateError as error:
            degradations.extend(
                _gate_failure(self.operation_id, item) for item in error.diagnostics
            )
            cause = error.cause
            if isinstance(cause, TransportError):
                raise _SourceError(cause.code, str(cause)) from cause
            raise _SourceError(
                "upstream_unavailable", "The source request gate failed."
            ) from cause
        except TransportError as error:
            raise _SourceError(error.code, str(error)) from error
        degradations.extend(
            _gate_failure(self.operation_id, item) for item in diagnostics
        )
        if response.status != 200:
            raise _SourceError(
                "upstream_http_error", "The option source returned a non-200 status."
            )
        return response

    def _contract_codes(
        self,
        underlying: str,
        month_code: str,
        *,
        contract_month: str,
        option_type: str,
        degradations: list[OptionSourceFailure],
    ) -> tuple[tuple[str, ...], OptionContractListingEvidence]:
        prefix = "OP_UP_" if option_type == "call" else "OP_DOWN_"
        symbol = f"{prefix}{underlying}{month_code}"
        response_url = f"{HQ_ENDPOINT}?{urlencode({'list': symbol})}"
        response = self._get(response_url, degradations)
        rows = _javascript_rows(response, (symbol,))
        values = [item for item in rows[symbol].split(",") if item]
        codes: list[str] = []
        seen: set[str] = set()
        for item in values:
            if not item.startswith("CON_OP_") or not item[7:].isdigit():
                raise _SourceError(
                    "unknown_schema",
                    "The option contract list contains an invalid code.",
                )
            code = item[7:]
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
        if not codes:
            raise _SourceError(
                "empty_contract_list_unverified",
                "The source returned an empty contract list without a provider total.",
            )
        return (
            tuple(codes),
            OptionContractListingEvidence(
                source_operation=self.operation_id,
                evidence_id=(
                    f"option-listing-sina-{underlying}-{month_code}-{option_type}"
                ),
                option_type=option_type,
                contract_month=contract_month,
                observed_count=len(codes),
                locator_uri=response_url,
                retrieved_at=response.retrieved_at,
            ),
        )

    def _batched_rows(
        self,
        prefix: str,
        codes: tuple[str, ...],
        degradations: list[OptionSourceFailure],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, datetime]]:
        rows: dict[str, str] = {}
        locators: dict[str, str] = {}
        retrieved_values: dict[str, datetime] = {}
        for chunk in _chunks(codes, self._batch_size):
            symbols = tuple(f"{prefix}{code}" for code in chunk)
            url = f"{HQ_ENDPOINT}?{urlencode({'list': ','.join(symbols)}, safe=',')}"
            response = self._get(url, degradations)
            parsed = _javascript_rows(response, symbols)
            for symbol, value in parsed.items():
                code = symbol.removeprefix(prefix)
                if code in rows and rows[code] != value:
                    raise _SourceError(
                        "duplicate_contract_conflict",
                        "Duplicate option rows disagree within one source snapshot.",
                    )
                rows[code] = value
                locators[code] = url
                retrieved_values[code] = response.retrieved_at
        missing = sorted(set(codes).difference(rows))
        if missing:
            raise _SourceError(
                "batch_response_incomplete",
                "The source batch omitted requested option contracts.",
            )
        return rows, locators, retrieved_values


def _contract_months(
    response: HttpResponse, *, expected_underlying: str
) -> tuple[str, ...]:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "text/json", "text/plain"}:
        raise _SourceError("unknown_schema", "The option month response is not JSON.")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _SourceError(
            "unknown_schema", "The option month response is invalid JSON."
        ) from error
    result = payload.get("result") if isinstance(payload, dict) else None
    status = result.get("status") if isinstance(result, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    if (
        not isinstance(status, dict)
        or status.get("code") != 0
        or not isinstance(data, dict)
        or data.get("stockId") != expected_underlying
        or not isinstance(data.get("contractMonth"), list)
    ):
        code = (
            "wrong_underlying_payload"
            if isinstance(data, dict)
            and isinstance(data.get("stockId"), str)
            and data.get("stockId") != expected_underlying
            else "unknown_schema"
        )
        raise _SourceError(
            code, "The option month response has an invalid identity or schema."
        )
    months: list[str] = []
    for value in data["contractMonth"]:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
            raise _SourceError(
                "unknown_schema",
                "The option source returned an invalid contract month.",
            )
        try:
            date.fromisoformat(value + "-01")
        except ValueError as error:
            raise _SourceError(
                "unknown_schema",
                "The option source returned an invalid contract month.",
            ) from error
        if value not in months:
            months.append(value)
    if not months:
        raise _SourceError(
            "empty_contract_months_unverified",
            "The option source did not establish any contract months.",
        )
    return tuple(months)


def _select_month(query: OptionQuery, months: tuple[str, ...]) -> str | None:
    if query.expiry_mode == "exact":
        if query.expiry_date is None:
            return None
        requested = query.expiry_date[:7]
        return requested if requested in months else None
    observed_month = query.observed_on[:7]
    available = [month for month in months if month >= observed_month]
    return min(available) if available else None


def _javascript_rows(
    response: HttpResponse, expected_symbols: Iterable[str]
) -> dict[str, str]:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"text/plain", "application/javascript", "text/javascript"}:
        raise _SourceError(
            "unknown_schema", "The option quote response has an invalid media type."
        )
    try:
        text = response.body.decode("gbk")
    except UnicodeDecodeError as error:
        raise _SourceError(
            "unknown_schema", "The option quote response is not valid GBK."
        ) from error
    expected = set(expected_symbols)
    rows: dict[str, str] = {}
    for line in (value.strip() for value in text.splitlines() if value.strip()):
        match = _ASSIGNMENT.fullmatch(line)
        if match is None or match.group(1) not in expected:
            raise _SourceError(
                "unknown_schema", "The option quote response has an invalid JS shell."
            )
        symbol, value = match.groups()
        previous = rows.get(symbol)
        if previous is not None and previous != value:
            raise _SourceError(
                "duplicate_contract_conflict",
                "Duplicate option rows disagree within one source response.",
            )
        rows[symbol] = value
    missing = expected.difference(rows)
    if missing:
        raise _SourceError(
            "batch_response_incomplete",
            "The source response omitted one or more requested option symbols.",
        )
    return rows


def _parse_quote(
    code: str,
    value: str,
    *,
    expected_underlying: str,
    expected_option_type: str,
    expected_month: str,
    observed_on: str,
) -> dict[str, Any]:
    fields = value.split(",")
    if len(fields) < 48:
        raise _SourceError("unknown_schema", "The option T-quote row is incomplete.")
    if fields[36] != expected_underlying:
        raise _SourceError(
            "wrong_underlying_payload", "The option quote belongs to another ETF."
        )
    option_type = {"C": "call", "P": "put"}.get(fields[45])
    if option_type != expected_option_type:
        raise _SourceError(
            "option_type_mismatch", "The option quote has the wrong call/put type."
        )
    series = fields[43]
    if series not in {"M", "A"}:
        raise _SourceError("unknown_schema", "The option quote series is invalid.")
    expiry = _strict_date(fields[46], "option expiry")
    if expiry[:7] != expected_month or expiry < observed_on:
        raise _SourceError(
            "expired_or_wrong_expiry",
            "The option quote expiry is expired or outside the requested month.",
        )
    observed_at = _china_datetime(fields[32], "option quote time")
    if observed_at.date().isoformat() != observed_on:
        raise _SourceError(
            "quote_date_mismatch", "The option quote date is outside the request."
        )
    bid = _optional_decimal(fields[1], "bid", nonnegative=True)
    last = _optional_decimal(fields[2], "last", nonnegative=True)
    ask = _optional_decimal(fields[3], "ask", nonnegative=True)
    quoted = all(value is not None and Decimal(value) > 0 for value in (bid, ask))
    usable_last = last if last is not None and Decimal(last) > 0 else None
    limitations = []
    if usable_last is None:
        limitations.append("last_trade_unavailable")
    if not quoted:
        limitations.append("two_sided_quote_unavailable")
    return {
        "code": code,
        "strike": _decimal(fields[7], "strike", positive=True),
        "series": series,
        "expiry_date": expiry,
        "observed_at": observed_at,
        "quote_state": "quoted" if quoted else "no_quote",
        "bid": bid if bid is not None and Decimal(bid) > 0 else None,
        "last": usable_last,
        "ask": ask if ask is not None and Decimal(ask) > 0 else None,
        "bid_size": _decimal(fields[0], "bid size", nonnegative=True),
        "ask_size": _decimal(fields[4], "ask size", nonnegative=True),
        "open_interest": _decimal(fields[5], "open interest", nonnegative=True),
        "volume": _decimal(fields[41], "volume", nonnegative=True),
        "limitations": tuple(limitations),
    }


def _parse_analytics(
    code: str,
    value: str,
    *,
    expected_underlying: str,
    expected_option_type: str,
    expected_month: str,
    quote: dict[str, Any],
) -> dict[str, OptionAnalytic]:
    fields = value.split(",")
    if len(fields) != 17 or fields[1:4] != ["", "", ""]:
        raise _SourceError(
            "unknown_schema", "The provider analytics row has shifted fields."
        )
    trade_code = _TRADE_CODE.fullmatch(fields[12])
    if trade_code is None:
        raise _SourceError(
            "unknown_schema", "The provider analytics trade code is invalid."
        )
    expected_letter = "C" if expected_option_type == "call" else "P"
    if (
        trade_code.group("underlying") != expected_underlying
        or trade_code.group("option_type") != expected_letter
        or trade_code.group("month") != expected_month[2:4] + expected_month[5:7]
        or trade_code.group("series") != quote["series"]
        or (
            quote["series"] == "M"
            and Decimal(trade_code.group("strike")) / Decimal("1000")
            != Decimal(quote["strike"])
        )
    ):
        raise _SourceError(
            "analytics_identity_mismatch",
            "The quote and provider analytics identify different contracts.",
        )
    strike = _decimal(fields[13], "analytics strike", positive=True)
    last = _decimal(fields[14], "analytics last", nonnegative=True)
    volume = _decimal(fields[4], "analytics volume", nonnegative=True)
    if (
        strike != quote["strike"]
        or (quote["last"] is not None and last != quote["last"])
        or volume != quote["volume"]
    ):
        raise _SourceError(
            "quote_analytics_mismatch",
            "The quote and provider analytics disagree on shared fields.",
        )
    values = {
        "delta": OptionAnalytic(_decimal(fields[5], "delta"), "dimensionless"),
        "gamma": OptionAnalytic(
            _decimal(fields[6], "gamma"), "provider_native_unverified"
        ),
        "theta": OptionAnalytic(
            _decimal(fields[7], "theta"), "provider_native_unverified"
        ),
        "vega": OptionAnalytic(
            _decimal(fields[8], "vega"), "provider_native_unverified"
        ),
        "implied_volatility": OptionAnalytic(
            _decimal(fields[9], "implied volatility", nonnegative=True),
            "decimal_fraction",
        ),
        "theoretical_value": OptionAnalytic(
            _decimal(fields[15], "theoretical value", nonnegative=True),
            "CNY/share",
        ),
    }
    if fields[16] != quote["series"]:
        raise _SourceError(
            "analytics_identity_mismatch",
            "The quote and provider analytics series disagree.",
        )
    return values


def _decimal(
    value: object,
    field: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise _SourceError("unknown_schema", f"The {field} value is invalid.")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise _SourceError(
            "unknown_schema", f"The {field} value is invalid."
        ) from error
    if (
        not parsed.is_finite()
        or (positive and parsed <= 0)
        or (nonnegative and parsed < 0)
    ):
        raise _SourceError("unknown_schema", f"The {field} value is invalid.")
    return format(parsed, "f")


def _optional_decimal(
    value: object, field: str, *, nonnegative: bool = False
) -> str | None:
    if value in {None, ""}:
        return None
    return _decimal(value, field, nonnegative=nonnegative)


def _strict_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _SourceError("unknown_schema", f"The {field} is invalid.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _SourceError("unknown_schema", f"The {field} is invalid.") from error
    if parsed.isoformat() != value:
        raise _SourceError("unknown_schema", f"The {field} is invalid.")
    return value


def _market_state(observed_time: time) -> str:
    if observed_time >= time(15, 0):
        return "completed"
    if observed_time >= time(9, 0):
        return "intraday"
    return "unknown"


def _quote_time_conflict_details(
    *,
    operation_id: str,
    underlying: str,
    contract_month: str,
    quote_times_by_code: dict[str, datetime],
    quote_locators: dict[str, str],
    quote_retrieved: dict[str, datetime],
) -> dict[str, Any]:
    observed_at = sorted({value.isoformat() for value in quote_times_by_code.values()})
    contract_counts = [
        {
            "observed_at": value,
            "contract_count": sum(
                item.isoformat() == value for item in quote_times_by_code.values()
            ),
        }
        for value in observed_at
    ]
    batch_evidence = []
    for index, locator in enumerate(sorted(set(quote_locators.values())), start=1):
        batch_codes = sorted(
            code
            for code, code_locator in quote_locators.items()
            if code_locator == locator
        )
        batch_evidence.append(
            {
                "id": (
                    "option-quote-batch-diagnostic-sina-"
                    f"{underlying}-{contract_month.replace('-', '')}-{index}"
                ),
                "source_role": "market_observation",
                "source_operation": operation_id,
                "status": "rejected",
                "rejection_code": "quote_time_conflict",
                "accepted_contract_evidence": False,
                "contract_count": len(batch_codes),
                "observed_at": sorted(
                    {quote_times_by_code[code].isoformat() for code in batch_codes}
                ),
                "retrieved_at": max(
                    quote_retrieved[code] for code in batch_codes
                ).isoformat(),
                "locator": {"uri": locator},
            }
        )
    return {
        "observed_at": observed_at,
        "contract_counts_by_observed_at": contract_counts,
        "quote_batch_evidence": batch_evidence,
    }


def _china_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise _SourceError("unknown_schema", f"The {field} is invalid.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=CHINA_STANDARD_TIME
        )
    except ValueError as error:
        raise _SourceError("unknown_schema", f"The {field} is invalid.") from error
    return parsed


def _chunks(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _gate_failure(
    operation_id: str, diagnostic: RequestGateDiagnostic
) -> OptionSourceFailure:
    return OptionSourceFailure(
        operation_id, diagnostic.code, diagnostic.message, diagnostic.details()
    )


def _deduplicate_failures(
    failures: Iterable[OptionSourceFailure],
) -> list[OptionSourceFailure]:
    selected: dict[tuple[str, str, str], OptionSourceFailure] = {}
    for item in failures:
        selected.setdefault((item.source_operation, item.code, str(item.details)), item)
    return list(selected.values())


def _indeterminate_coverage() -> dict[str, OptionCoverage]:
    return {
        "contract_listing": OptionCoverage("indeterminate"),
        "option_quotes": OptionCoverage("indeterminate"),
        "provider_analytics": OptionCoverage("indeterminate"),
    }
