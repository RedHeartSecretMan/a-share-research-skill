"""Qualified public calendar/rule acquisition for the Market Session Module.

This owner reads documents only. Its published schedule does not certify trade,
quote or daily-report completeness. Unknown document versions fail closed.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from typing import Any, Literal, cast
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from .identity_sources import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrlLibTransport,
)
from .market_session_context import (
    ASIA_SHANGHAI,
    CAPABILITY_ID,
    EXCHANGES,
    OPERATION_ID,
    CalendarDay,
    CalendarEvidence,
    DeterministicMarketSessionAdapter,
    MarketSessionOperationRequest,
    _ContextError,
    _validate_bindings,
)
from .research_result import (
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
)

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCX_ENTRIES = 64
_MAX_TEXT = 2 * 1024 * 1024
_HEADERS = {"User-Agent": "a-share-research-skill/1", "Accept-Encoding": "identity"}


@dataclass(frozen=True)
class MarketSessionSourceContract:
    """One reviewed document version, never caller ResearchTask configuration."""

    exchange: str
    version: str
    year: int
    calendar_uri: str
    calendar_title: str
    calendar_document_number: str
    calendar_publication_date: str
    rule_uri: str
    rule_format: Literal["html", "docx", "pdf"]
    rule_document_number: str | None
    rule_publication_uri: str
    rule_publication_date: str
    calendar_body_sha256: str | None
    rule_body_sha256: str | None
    effective_from: str
    effective_to: str
    weekday_section: str
    auction_section: str
    effect_section: str
    auction_times: tuple[str, ...]
    scheduled_day_completion: str
    completion_kind: str
    completion_sections: tuple[str, ...]
    deferred_sections: tuple[str, ...]
    holiday_ranges: tuple[tuple[str, ...], ...]
    asset_scope: str
    availability_basis: str
    exception_scope: str
    synthetic: bool = False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Request | None:
        return None


class _CalendarUrlLibTransport(UrlLibTransport):
    """Reuse bounded standard HTTP while refusing extra implicit requests."""

    def __init__(self) -> None:
        super().__init__()
        self._opener = build_opener(_NoRedirect())


def load_market_session_source_contracts() -> tuple[MarketSessionSourceContract, ...]:
    path = (
        Path(__file__).resolve().parents[2]
        / "references"
        / "market-session-sources.json"
    )
    material = json.loads(path.read_text(encoding="utf-8"))
    if material.get("schema_version") != "1.0" or not isinstance(
        material.get("sources"), list
    ):
        raise ValueError("market calendar source contracts are invalid")
    contracts = []
    for value in material["sources"]:
        for key in ("auction_times", "completion_sections", "deferred_sections"):
            value[key] = tuple(value[key])
        value["holiday_ranges"] = tuple(tuple(row) for row in value["holiday_ranges"])
        contracts.append(MarketSessionSourceContract(**value))
    if tuple(item.exchange for item in contracts) != EXCHANGES:
        raise ValueError("market calendar source contracts omit an exchange")
    return tuple(contracts)


def build_default_market_session_adapter(
    transport: HttpTransport | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    execution_clock: Callable[[], datetime] | None = None,
) -> ProductionMarketSessionAdapter:
    """Load the three fixed source contracts without making any source call."""

    return ProductionMarketSessionAdapter(
        _CalendarUrlLibTransport() if transport is None else transport,
        load_market_session_source_contracts(),
        clock=clock or (lambda: datetime.now(ASIA_SHANGHAI)),
        execution_clock=execution_clock or (lambda: datetime.now(ASIA_SHANGHAI)),
    )


class CalendarSourceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProductionMarketSessionAdapter:
    """One bounded document-set owner behind execute(request, timeout).

    Six direct document requests, no retries/redirects, sequential exchanges.
    Restoration validates persisted proof before any new source path and never
    substitutes newly acquired calendars for the frozen original context.
    """

    def __init__(
        self,
        transport: HttpTransport,
        contracts: Sequence[MarketSessionSourceContract],
        *,
        clock: Callable[[], datetime],
        execution_clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._transport = transport
        self._contracts = tuple(contracts)
        if tuple(item.exchange for item in self._contracts) != EXCHANGES:
            raise ValueError("market calendar source contracts omit an exchange")
        self._clock = clock
        self._execution_clock = execution_clock
        self._monotonic = monotonic_clock

    @property
    def obligation(self) -> OperationObligation:
        return OperationObligation(
            CAPABILITY_ID,
            OPERATION_ID,
            "exchange_document",
            any(item.synthetic for item in self._contracts),
            None,
            (EvidenceUsePolicy("market_trading_session", "supporting"),),
        )

    @property
    def field_dispositions(self) -> tuple[FieldDisposition, ...]:
        return (
            FieldDisposition(
                "calendar_and_rule_proof", "normalized", "observations[].payload"
            ),
        )

    @property
    def request_budget(self) -> dict[str, object]:
        return {
            "documents": 6,
            "per_exchange": 2,
            "retry": 0,
            "redirect": 0,
            "maximum_active_requests": 1,
        }

    def execute(
        self, request: MarketSessionOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("calendar timeout must be finite and bounded")
        try:
            first_now = _instant(self._clock())
            _validate_bindings(asdict(request.bindings))
        except (CalendarSourceError, _ContextError, TypeError) as error:
            code = (
                error.code
                if isinstance(error, (CalendarSourceError, _ContextError))
                else "context_binding_mismatch"
            )
            return self._partial((), ((None, code),), 0)
        if request.frozen_context is not None:
            try:
                restored_calendars = self._restored_calendars(request.frozen_context)
            except (CalendarSourceError, TypeError, ValueError, KeyError) as error:
                code = (
                    error.code
                    if isinstance(error, CalendarSourceError)
                    else "context_semantic_mismatch"
                )
                return self._partial((), ((None, code),), 0)
            return DeterministicMarketSessionAdapter(
                restored_calendars,
                clock=lambda: first_now,
                execution_clock=self._execution_clock,
            ).execute(request, timeout_seconds)
        try:
            requested = date.fromisoformat(request.requested_as_of)
            if requested.isoformat() != request.requested_as_of:
                raise ValueError
        except (ValueError, TypeError):
            return self._partial((), ((None, "invalid_session_date"),), 0)
        if requested > first_now.date():
            return self._partial((), ((None, "future_requested_date"),), 0)
        if request.expected_context_digest is not None:
            return self._partial((), ((None, "context_digest_mismatch"),), 0)
        if any(
            not date.fromisoformat(item.effective_from)
            <= requested
            <= date.fromisoformat(item.effective_to)
            for item in self._contracts
        ):
            return self._partial((), ((None, "calendar_coverage_gap"),), 0)
        deadline = self._monotonic() + timeout_seconds
        calendars: list[CalendarEvidence] = []
        errors: list[tuple[str | None, str]] = []
        attempts = 0
        for contract in self._contracts:
            try:
                responses = []
                for uri in (contract.calendar_uri, contract.rule_uri):
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        raise CalendarSourceError("calendar_source_timeout")
                    attempts += 1
                    response = self._fetch(uri, remaining)
                    if self._monotonic() > deadline:
                        raise CalendarSourceError("calendar_source_timeout")
                    _response(
                        response,
                        MAX_HTML_BYTES
                        if uri == contract.calendar_uri
                        else MAX_DOCUMENT_BYTES,
                    )
                    if _instant(response.retrieved_at) > _instant(
                        self._execution_clock()
                    ):
                        raise CalendarSourceError("calendar_timestamp_unproved")
                    publication = (
                        contract.calendar_publication_date
                        if uri == contract.calendar_uri
                        else contract.rule_publication_date
                    )
                    if _instant(response.retrieved_at) < datetime.combine(
                        date.fromisoformat(publication), time.min, ASIA_SHANGHAI
                    ):
                        raise CalendarSourceError("calendar_timestamp_unproved")
                    if uri == contract.calendar_uri:
                        _holiday_ranges(_text_html(response), contract)
                        _verify_document_version(
                            response,
                            contract.calendar_body_sha256,
                            "calendar_document_version_unproved",
                        )
                    responses.append(response)
                calendars.append(
                    _calendar_from_documents(contract, responses[0], responses[1])
                )
            except CalendarSourceError as error:
                errors.append((contract.exchange, error.code))
            except (TransportError, TimeoutError, OSError):
                errors.append((contract.exchange, "calendar_source_unavailable"))
        if errors:
            return self._partial(calendars, errors, attempts)
        outcome = DeterministicMarketSessionAdapter(
            calendars,
            clock=lambda: first_now,
            execution_clock=self._execution_clock,
        ).execute(request, timeout_seconds)
        projection = outcome.to_dict()
        return OperationOutcome(
            CAPABILITY_ID,
            OPERATION_ID,
            outcome.execution,
            observations=tuple(projection["observations"]),
            source_errors=tuple(projection["source_errors"]),
            field_dispositions=self.field_dispositions,
            diagnostics={
                "document_requests": attempts,
                "request_budget": self.request_budget,
            },
        )

    def _fetch(self, uri: str, remaining: float) -> HttpResponse:
        if isinstance(self._transport, _CalendarUrlLibTransport):
            return self._transport.get_with_timeout(
                uri, dict(_HEADERS), min(remaining, 20.0)
            )
        return self._transport.get(uri, dict(_HEADERS))

    def _partial(
        self,
        calendars: Sequence[CalendarEvidence],
        errors: Sequence[tuple[str | None, str]],
        attempts: int,
    ) -> OperationOutcome:
        return OperationOutcome(
            CAPABILITY_ID,
            OPERATION_ID,
            OperationExecution("failed"),
            observations=tuple(
                {
                    "source_operation": OPERATION_ID,
                    "data_type": "exchange_trading_calendar",
                    "retrieved_at": item.retrieved_at,
                    "payload": {"calendar_proof": item.to_dict()},
                }
                for item in calendars
            ),
            source_errors=tuple(
                {
                    "source_operation": OPERATION_ID,
                    "code": code,
                    "message": "Required calendar/rule evidence could not be proved."
                    + (f" Exchange: {exchange}." if exchange else ""),
                }
                for exchange, code in errors
            ),
            field_dispositions=self.field_dispositions,
            diagnostics={
                "document_requests": attempts,
                "request_budget": self.request_budget,
            },
        )

    def _restored_calendars(
        self, context: Mapping[str, object]
    ) -> tuple[CalendarEvidence, ...]:
        from .bounded_public_data import validate_bounded_public_data

        validate_bounded_public_data(
            context, artifact="persisted calendar context", allow_floats=False
        )
        snapshot = json.loads(json.dumps(context))
        proofs = snapshot["calendar_proofs"]
        if not isinstance(proofs, list) or len(proofs) != 3:
            raise CalendarSourceError("calendar_exchange_gap")
        calendars = []
        for proof, contract in zip(proofs, self._contracts, strict=True):
            proof["days"] = tuple(CalendarDay(**day) for day in proof["days"])
            proof["auction_times"] = tuple(proof["auction_times"])
            calendar = CalendarEvidence(**proof)
            _validate_registered_calendar(calendar, contract)
            calendars.append(calendar)
        return tuple(calendars)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip += 1
        elif not self.skip and tag in {
            "p",
            "div",
            "br",
            "tr",
            "li",
            "h1",
            "h2",
            "title",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag in {"p", "div", "tr", "li", "h1", "h2", "title"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _text_html(response: HttpResponse) -> str:
    _response(response, MAX_HTML_BYTES)
    if "html" not in response.content_type.casefold():
        raise CalendarSourceError("calendar_document_type_unproved")
    try:
        text = response.body.decode("utf-8-sig")
        collector = _VisibleText()
        collector.feed(text)
    except (UnicodeError, ValueError) as error:
        raise CalendarSourceError("calendar_document_invalid") from error
    return _normalize("".join(collector.parts))


def _text_docx(response: HttpResponse) -> str:
    _response(response, MAX_DOCUMENT_BYTES)
    try:
        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_DOCX_ENTRIES
                or sum(item.file_size for item in entries) > MAX_DOCUMENT_BYTES
            ):
                raise CalendarSourceError("calendar_document_too_large")
            if any(
                item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                for item in entries
            ):
                raise CalendarSourceError("calendar_document_invalid")
            with archive.open("word/document.xml") as stream:
                body = stream.read(MAX_DOCUMENT_BYTES + 1)
            if len(body) > MAX_DOCUMENT_BYTES:
                raise CalendarSourceError("calendar_document_too_large")
            if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
                raise CalendarSourceError("calendar_document_invalid")
            root = ElementTree.fromstring(body)
            paragraphs = [
                "".join(node.itertext())
                for node in root.iter()
                if node.tag.endswith("}p")
            ]
    except (
        zipfile.BadZipFile,
        zlib.error,
        KeyError,
        RuntimeError,
        ElementTree.ParseError,
    ) as error:
        raise CalendarSourceError("calendar_document_invalid") from error
    return _normalize("\n".join(paragraphs))


def _normalize(value: str) -> str:
    if len(value.encode("utf-8")) > _MAX_TEXT:
        raise CalendarSourceError("calendar_document_too_large")
    return re.sub(r"\s+", "", value).replace("：", ":")


def _instant(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CalendarSourceError("calendar_timestamp_unproved")
    return value.astimezone(ASIA_SHANGHAI)


def _response(response: HttpResponse, maximum: int) -> None:
    if not isinstance(response, HttpResponse) or response.status != 200:
        raise CalendarSourceError("calendar_source_unavailable")
    if not isinstance(response.content_type, str):
        raise CalendarSourceError("calendar_document_type_unproved")
    if not isinstance(response.body, bytes) or len(response.body) > maximum:
        raise CalendarSourceError("calendar_document_too_large")
    _instant(response.retrieved_at)


def _available_bound(value: str) -> datetime:
    return datetime.combine(
        date.fromisoformat(value) + timedelta(days=1), time.min, ASIA_SHANGHAI
    )


def _holiday_ranges(
    text: str, contract: MarketSessionSourceContract
) -> tuple[tuple[str, ...], ...]:
    if (
        _normalize(contract.calendar_title) not in text
        or _normalize(contract.calendar_document_number) not in text
    ):
        raise CalendarSourceError("calendar_document_version_unproved")
    year = contract.year
    result = []
    for name, expected_start, expected_end, expected_reopen in contract.holiday_ranges:
        label = re.escape(name.removesuffix("节")) + "节?"
        match = re.search(
            label
            + r":([0-9]{1,2})月([0-9]{1,2})日.*?至([0-9]{1,2})月([0-9]{1,2})日.*?休市.*?([0-9]{1,2})月([0-9]{1,2})日(?:（[^）]*）)?起照常开市",
            text,
        )
        if match is None:
            raise CalendarSourceError("calendar_holiday_scope_unproved")
        values = tuple(int(value) for value in match.groups())
        try:
            start, end, reopen = (
                date(year, values[index], values[index + 1]).isoformat()
                for index in (0, 2, 4)
            )
        except ValueError as error:
            raise CalendarSourceError("calendar_holiday_scope_unproved") from error
        if (start, end, reopen) != (expected_start, expected_end, expected_reopen):
            raise CalendarSourceError("calendar_document_version_unproved")
        result.append((name, start, end, reopen))
    _validate_annual_declarations(text, contract)
    return tuple(result)


_DATE_TOKEN = r"(?:[0-9]{4}年)?[0-9]{1,2}月[0-9]{1,2}日(?:[（(][^）)]*[）)])?"


def _annual_date(value: str, year: int) -> date:
    match = re.fullmatch(
        r"(?:([0-9]{4})年)?([0-9]{1,2})月([0-9]{1,2})日(?:[（(][^）)]*[）)])?",
        value,
    )
    if match is None or (match[1] is not None and int(match[1]) != year):
        raise CalendarSourceError("calendar_holiday_scope_unproved")
    try:
        return date(year, int(match[2]), int(match[3]))
    except ValueError as error:
        raise CalendarSourceError("calendar_holiday_scope_unproved") from error


def _validate_annual_declarations(
    text: str, contract: MarketSessionSourceContract
) -> None:
    # Consume every closure/opening declaration, not just the seven positive
    # holiday signatures. Unknown statements cannot silently become weekdays.
    closures = _closed_dates(contract.holiday_ranges)
    closure_expression = (
        _DATE_TOKEN + rf"(?:(?:至|、){_DATE_TOKEN})*(?:为)?(?:周末)?休市"
    )
    for match in re.finditer(closure_expression, text):
        declaration = match[0]
        declared_dates = [
            _annual_date(value, contract.year)
            for value in re.findall(_DATE_TOKEN, declaration)
        ]
        if "至" in declaration:
            if len(declared_dates) != 2 or declared_dates[1] < declared_dates[0]:
                raise CalendarSourceError("calendar_holiday_scope_unproved")
            declared_dates = [
                declared_dates[0] + timedelta(days=offset)
                for offset in range((declared_dates[1] - declared_dates[0]).days + 1)
            ]
        if "周末" in declaration:
            if any(value.weekday() < 5 for value in declared_dates):
                raise CalendarSourceError("calendar_holiday_scope_unproved")
        elif any(value not in closures for value in declared_dates):
            raise CalendarSourceError("calendar_holiday_scope_unproved")
    opening_expression = _DATE_TOKEN + r"(?:起)?照常开市"
    expected_openings = {date.fromisoformat(row[3]) for row in contract.holiday_ranges}
    actual_openings = {
        _annual_date(re.findall(_DATE_TOKEN, match[0])[0], contract.year)
        for match in re.finditer(opening_expression, text)
    }
    if actual_openings != expected_openings:
        raise CalendarSourceError("calendar_holiday_scope_unproved")
    remaining = re.sub(opening_expression, "", re.sub(closure_expression, "", text))
    if re.search(r"休市(?!安排)|开市", remaining):
        raise CalendarSourceError("calendar_holiday_scope_unproved")


def _verify_document_version(
    response: HttpResponse, expected: str | None, code: str
) -> None:
    # Whole qualified bytes bind all declarations, including language not
    # interpreted by the named-clause parser. Unknown revisions are not proof.
    if expected is None or hashlib.sha256(response.body).hexdigest() != expected:
        raise CalendarSourceError(code)


def _rule_text(
    response: HttpResponse, contract: MarketSessionSourceContract
) -> str | None:
    if contract.rule_format == "pdf":
        _response(response, MAX_DOCUMENT_BYTES)
        if (
            not response.body.startswith(b"%PDF-")
            or hashlib.sha256(response.body).hexdigest() != contract.rule_body_sha256
        ):
            raise CalendarSourceError("calendar_rule_version_unproved")
        return None
    return (
        _text_docx(response) if contract.rule_format == "docx" else _text_html(response)
    )


def _section(text: str, number: str) -> str:
    expression = rf"(?<![0-9.]){re.escape(number)}(?![0-9.])(.+?)(?=(?<![0-9.])[0-9]+\.[0-9]+(?:\.[0-9]+)?(?![0-9.])|$)"
    matches = re.findall(expression, text)
    relevant = [value for value in matches if len(value) > 10]
    if len(relevant) != 1:
        raise CalendarSourceError("calendar_rule_scope_unproved")
    return cast(str, relevant[0])


def _validate_deferments(text: str, contract: MarketSessionSourceContract) -> None:
    statements = re.findall(
        r"[^。！？]*(?:另行通知|暂不实施|暂缓实施|暂不适用|暂停实施)[^。！？]*",
        text,
    )
    if contract.exchange == "BSE":
        if len(statements) != 1 or "实施时间另行通知" not in statements[0]:
            raise CalendarSourceError("calendar_rule_deferment_unproved")
        statement = statements[0]
        clauses = re.findall(r"(?<![0-9.])[0-9]+\.[0-9]+\.[0-9]+(?![0-9.])", statement)
        if clauses != ["3.6.5", "3.7.1", "3.7.10", "4.5.1", "4.5.4"]:
            raise CalendarSourceError("calendar_rule_deferment_unproved")
        paragraph_scope = statement.split("3.6.5", 1)[1].split("3.7.1", 1)[0]
        if re.sub(r"[第条款、，及和]", "", paragraph_scope) != "二三":
            raise CalendarSourceError("calendar_rule_deferment_unproved")
        for first, last in (("3.7.1", "3.7.10"), ("4.5.1", "4.5.4")):
            between = statement.split(first, 1)[1].split(last, 1)[0]
            if re.sub(r"[第条]", "", between) != "至":
                raise CalendarSourceError("calendar_rule_deferment_unproved")
    else:
        # SSE's qualified Annex3 defers specified3.6 paragraphs, never3.7.
        # Acquired contradictory or unrecognized deferred clauses fail closed.
        known = {value.split(" ", 1)[0] for value in contract.deferred_sections}
        for statement in statements:
            deferred_clauses = set(
                re.findall(r"(?<![0-9.])[0-9]+\.[0-9]+\.[0-9]+(?![0-9.])", statement)
            )
            if not deferred_clauses or not deferred_clauses <= known:
                raise CalendarSourceError("calendar_rule_deferment_unproved")


def _validate_rule(text: str | None, contract: MarketSessionSourceContract) -> None:
    if text is None:
        # Only the exact previously qualified PDF version has no live text
        # parser. Body drift requires a new reviewed version, never blind reuse.
        return
    if (
        contract.rule_document_number is not None
        and contract.rule_document_number not in text
    ):
        raise CalendarSourceError("calendar_rule_version_unproved")
    if contract.exchange == "BSE" and "现行有效" not in text:
        raise CalendarSourceError("calendar_rule_version_unproved")
    _validate_deferments(text, contract)
    weekdays = _section(text, contract.weekday_section)
    if (
        "每周一至周五" not in weekdays
        or re.search(r"国家法定(?:节)?假日", weekdays) is None
        or "公告的休市日" not in weekdays
    ):
        raise CalendarSourceError("calendar_weekday_rule_unproved")
    auction = _section(text, contract.auction_section)
    source_ranges = re.findall(
        r"(?<![0-9])([0-9]{1,2}):([0-9]{2})至([0-9]{1,2}):([0-9]{2})", auction
    )
    try:
        ranges = tuple(
            (
                time(int(a), int(b)).isoformat(timespec="minutes"),
                time(int(c), int(d)).isoformat(timespec="minutes"),
            )
            for a, b, c, d in source_ranges
        )
    except ValueError as error:
        raise CalendarSourceError("calendar_auction_rule_unproved") from error
    if ranges != (
        ("09:15", "09:25"),
        ("09:30", "11:30"),
        ("13:00", "14:57"),
        ("14:57", "15:00"),
    ):
        raise CalendarSourceError("calendar_auction_rule_unproved")
    if (
        "开盘集合竞价" not in auction
        or "连续竞价" not in auction
        or "收盘集合竞价" not in auction
    ):
        raise CalendarSourceError("calendar_auction_rule_unproved")
    effect = _section(text, contract.effect_section)
    parsed = date.fromisoformat(contract.effective_from)
    if f"{parsed.year}年{parsed.month}月{parsed.day}日起施行" not in effect:
        raise CalendarSourceError("calendar_rule_version_unproved")
    if contract.exchange == "BSE":
        confirmation = _section(text, "3.6.4")
        daily = _section(text, "3.6.8")
        if (
            "15:00至15:30" not in confirmation
            or "大宗交易" not in confirmation
            or "确认" not in confirmation
            or "总量" not in daily
        ):
            raise CalendarSourceError("calendar_day_completion_unproved")
    else:
        if "买卖A股" not in _section(text, "3.7.1"):
            raise CalendarSourceError("calendar_asset_scope_unproved")
        if not re.search(r"15:05至15:30为盘后固定价格交易时间", text):
            raise CalendarSourceError("calendar_day_completion_unproved")


def _closed_dates(holidays: Sequence[Sequence[str]]) -> set[date]:
    return {
        date.fromisoformat(first) + timedelta(days=offset)
        for _, first, last, _ in holidays
        for offset in range(
            (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
        )
    }


def _declared_days(
    contract: MarketSessionSourceContract, holidays: Sequence[Sequence[str]]
) -> tuple[CalendarDay, ...]:
    start = date.fromisoformat(contract.effective_from)
    end = date.fromisoformat(contract.effective_to)
    closures = _closed_dates(holidays)
    days = tuple(
        CalendarDay(
            (day := start + timedelta(days=offset)).isoformat(),
            "holiday"
            if day in closures
            else "weekend"
            if day.weekday() >= 5
            else "trading",
        )
        for offset in range((end - start).days + 1)
    )
    return days


def _registered_metadata(contract: MarketSessionSourceContract) -> dict[str, object]:
    return {
        "proof_kind": "published_calendar_schedule",
        "asset_scope": contract.asset_scope,
        "calendar_document_number": contract.calendar_document_number,
        "calendar_publication_date": contract.calendar_publication_date,
        "rule_publication_date": contract.rule_publication_date,
        "publication_precision": "day",
        "availability_basis": contract.availability_basis,
        "exact_first_available_at": None,
        "rule_publication_reference": contract.rule_publication_uri,
        "rule_format": contract.rule_format,
        "rule_document_number": contract.rule_document_number,
        "completion_kind": contract.completion_kind,
        "completion_sections": list(contract.completion_sections),
        "deferred_sections": list(contract.deferred_sections),
        "known_exception_scope": contract.exception_scope,
        "unbounded_later_exception_absence_proved": False,
        "actual_trade_quote_or_publication_completeness_proved": False,
    }


def _calendar_from_documents(
    contract: MarketSessionSourceContract, annual: HttpResponse, rule: HttpResponse
) -> CalendarEvidence:
    holidays = _holiday_ranges(_text_html(annual), contract)
    _validate_rule(_rule_text(rule, contract), contract)
    _verify_document_version(
        annual, contract.calendar_body_sha256, "calendar_document_version_unproved"
    )
    _verify_document_version(
        rule, contract.rule_body_sha256, "calendar_rule_version_unproved"
    )
    start = date.fromisoformat(contract.effective_from)
    end = date.fromisoformat(contract.effective_to)
    days = _declared_days(contract, holidays)
    retrieved = max(_instant(annual.retrieved_at), _instant(rule.retrieved_at))
    available = max(
        _available_bound(contract.calendar_publication_date),
        _available_bound(contract.rule_publication_date),
    )
    metadata = _registered_metadata(contract)
    metadata.update(
        {
            "calendar_body_sha256": hashlib.sha256(annual.body).hexdigest(),
            "rule_body_sha256": hashlib.sha256(rule.body).hexdigest(),
        }
    )
    return CalendarEvidence(
        exchange=contract.exchange,
        evidence_id=f"{contract.exchange.lower()}-calendar-{contract.year}",
        version=contract.version,
        provenance_uri=contract.calendar_uri,
        available_at=available.isoformat(),
        retrieved_at=retrieved.isoformat(),
        effective_from=contract.effective_from,
        effective_to=contract.effective_to,
        coverage_start=start.isoformat(),
        coverage_end=end.isoformat(),
        authority="proved",
        days=days,
        auction_times=contract.auction_times,
        rule_evidence_id=f"{contract.exchange.lower()}-rule-{contract.year}",
        rule_provenance_uri=contract.rule_uri,
        synthetic=contract.synthetic,
        trading_day_completion_time=contract.scheduled_day_completion,
        completion_evidence_id=f"{contract.exchange.lower()}-scheduled-day-{contract.year}",
        source_metadata=metadata,
    )


def _validate_registered_calendar(
    calendar: CalendarEvidence, contract: MarketSessionSourceContract
) -> None:
    metadata = calendar.source_metadata
    if (
        not isinstance(metadata, Mapping)
        or calendar.exchange != contract.exchange
        or calendar.version != contract.version
        or calendar.provenance_uri != contract.calendar_uri
        or calendar.rule_provenance_uri != contract.rule_uri
    ):
        raise CalendarSourceError("calendar_reference_unproved")
    if (
        calendar.evidence_id != f"{contract.exchange.lower()}-calendar-{contract.year}"
        or calendar.rule_evidence_id
        != f"{contract.exchange.lower()}-rule-{contract.year}"
        or calendar.completion_evidence_id
        != f"{contract.exchange.lower()}-scheduled-day-{contract.year}"
    ):
        raise CalendarSourceError("calendar_reference_unproved")
    expected_available = max(
        _available_bound(contract.calendar_publication_date),
        _available_bound(contract.rule_publication_date),
    ).isoformat()
    if (
        calendar.auction_times != contract.auction_times
        or calendar.trading_day_completion_time != contract.scheduled_day_completion
        or calendar.effective_from != contract.effective_from
        or calendar.effective_to != contract.effective_to
        or calendar.coverage_start != contract.effective_from
        or calendar.coverage_end != contract.effective_to
        or calendar.available_at != expected_available
        or calendar.synthetic != contract.synthetic
        or calendar.days != _declared_days(contract, contract.holiday_ranges)
    ):
        raise CalendarSourceError("calendar_rule_version_unproved")
    expected_metadata = _registered_metadata(contract)
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise CalendarSourceError("calendar_rule_version_unproved")
    for key in ("calendar_body_sha256", "rule_body_sha256"):
        if (
            not isinstance(metadata.get(key), str)
            or re.fullmatch(r"[a-f0-9]{64}", cast(str, metadata[key])) is None
        ):
            raise CalendarSourceError("calendar_rule_version_unproved")
    if (
        metadata.get("calendar_body_sha256") != contract.calendar_body_sha256
        or metadata.get("rule_body_sha256") != contract.rule_body_sha256
    ):
        raise CalendarSourceError("calendar_rule_version_unproved")
