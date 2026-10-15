"""Bounded acquisition of the observed SZSE current-list wire shape.

This source evidence is an input to the existing Universe Module, not another
Universe. The retained contract does not yet prove membership/availability times,
so these observations must not be projected as historical expected membership.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import partial
from html.parser import HTMLParser
from threading import Lock
from typing import Protocol, cast
from unicodedata import category
from urllib.parse import parse_qs, urlencode, urlparse

from .identity_sources import (
    MAX_RESPONSE_BYTES,
    HttpResponse,
    HttpTransport,
    SzseStockListOperation,
    UrlLibTransport,
)
from .provider_lane import (
    ProductionInvocationAdapter,
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLaneResult,
    ProviderLanes,
)
from .universe_input import (
    ASIA_SHANGHAI,
    IdentityBatchOutcome,
    UniverseIdentity,
    SourceResult,
    UniverseRequest,
    prepare_universe_request,
)


@dataclass(frozen=True, slots=True)
class CurrentListRecord:
    code: str
    name: str
    listing_date: str
    retrieved_at: str
    provenance_uri: str
    source_list_date_label: str
    membership_effective_at: None = None
    available_at: None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": "SZSE",
            "code": self.code,
            "name": self.name,
            "listing_date": self.listing_date,
            "source_list_date_label": self.source_list_date_label,
            "source_list_date_semantics": "unproved",
            "membership_effective_at": self.membership_effective_at,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
        }


@dataclass(frozen=True, slots=True)
class _Page:
    number: int
    pages: int
    size: int
    count: int
    records: tuple[CurrentListRecord, ...]
    rejected_rows: int
    short_page: bool
    list_date_label: str


@dataclass(frozen=True, slots=True)
class CurrentListAcquisition:
    """Detached, safe source evidence; reported count is not a PIT denominator."""

    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    reported_count: int | None
    pages_expected: int | None
    pages_received: int
    pages_complete: bool
    records: tuple[CurrentListRecord, ...]
    rejected_rows: int
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]
    source_list_date_labels: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "szse_current_list_acquisition@1",
            "exchange": "SZSE",
            "membership_effective_date": self.membership_effective_date,
            "information_cutoff": self.information_cutoff,
            "research_now": self.research_now,
            "membership_proven": False,
            "attempted": bool(self.lane_result.attempts),
            "source_result": self.source_result,
            "reported_count": self.reported_count,
            "pages_expected": self.pages_expected,
            "pages_received": self.pages_received,
            "pages_complete": self.pages_complete,
            "source_list_date_labels": list(self.source_list_date_labels),
            "records": [record.to_public_dict() for record in self.records],
            "rejected_rows": self.rejected_rows,
            "limitation_codes": list(self.limitation_codes),
            "attempts": [
                {
                    "provider_group": attempt.provider_group,
                    "sequence": attempt.sequence,
                    "state": attempt.state,
                }
                for attempt in self.lane_result.attempts
            ],
            "quiescence": self.lane_result.quiescence.state,
        }


class _ParseFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("The current list does not match its retained contract.")
        self.code = code


def _integer(value: object, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise _ParseFailure("invalid_pagination")
    return value


class _NameCell(HTMLParser):
    """Accept only the G-observed a/u cell; its locator never becomes evidence time."""

    def __init__(self, code: str) -> None:
        super().__init__(convert_charrefs=True)
        self.code = code
        self.stack: list[str] = []
        self.parts: list[str] = []
        self.anchor_seen = False
        self.underline_seen = False
        self.failed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and not self.stack and not self.anchor_seen:
            attributes = dict(attrs)
            if len(attributes) != len(attrs) or set(attributes) != {"href", "target"}:
                self.failed = True
                return
            href = attributes.get("href")
            parsed = urlparse(href or "")
            if (
                attributes["target"] != "_blank"
                or parsed.scheme not in {"http", "https"}
                or parsed.netloc != "www.szse.cn"
                or parsed.path != "/certificate/individual/index.html"
                or parsed.fragment
                or parsed.params
                or parse_qs(parsed.query, keep_blank_values=True)
                != {"code": [self.code]}
            ):
                self.failed = True
                return
            self.anchor_seen = True
        elif (
            tag == "u" and self.stack == ["a"] and not self.underline_seen and not attrs
        ):
            self.underline_seen = True
        else:
            self.failed = True
            return
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.failed = True
        else:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.stack == ["a", "u"]:
            self.parts.append(data)
        elif data.strip():
            self.failed = True

    def handle_comment(self, data: str) -> None:
        self.failed = True

    def handle_decl(self, decl: str) -> None:
        self.failed = True

    def handle_pi(self, data: str) -> None:
        self.failed = True


def _name(value: str, code: str) -> str:
    if "<" not in value and ">" not in value:
        return "".join(value.split())
    if len(value) > 1024:
        raise _ParseFailure("invalid_current_list_row")
    parser = _NameCell(code)
    parser.feed(value)
    parser.close()
    if (
        parser.failed
        or parser.stack
        or not parser.anchor_seen
        or not parser.underline_seen
    ):
        raise _ParseFailure("invalid_current_list_row")
    return "".join("".join(parser.parts).split())


def _record(
    value: object, *, page: int, retrieved_at: str, list_date_label: str
) -> CurrentListRecord:
    if not SzseStockListOperation.field_contract.accepts(value):
        raise _ParseFailure("invalid_current_list_row")
    row = cast(dict[str, str], value)
    code, listed = row["agdm"], row["agssrq"]
    name = _name(row["agjc"], code)
    if (
        re.fullmatch(r"[0-9]{6}", code) is None
        or not name.strip()
        or len(name) > 256
        or any(category(character).startswith("C") for character in name)
        or "/" in name
        or "\\" in name
        or "<" in name
        or ">" in name
    ):
        raise _ParseFailure("invalid_current_list_row")
    try:
        if date.fromisoformat(listed).isoformat() != listed:
            raise ValueError
    except ValueError:
        raise _ParseFailure("invalid_current_list_row") from None
    return CurrentListRecord(
        code,
        name.strip(),
        listed,
        retrieved_at,
        f"exchange-list:SZSE:1110:tab1:page:{page}:security:{code}",
        list_date_label,
    )


def _parse(response: HttpResponse, *, page: int, membership_date: str) -> _Page:
    if response.status in {401, 403}:
        raise _ParseFailure("source_access_blocked")
    if response.status != 200:
        raise _ParseFailure("source_unavailable")
    if (
        not response.content_type.lower().startswith("application/json")
        or len(response.body) > MAX_RESPONSE_BYTES
        or type(response.retrieved_at) is not datetime
        or response.retrieved_at.tzinfo is None
        or response.retrieved_at.utcoffset() is None
    ):
        raise _ParseFailure("invalid_current_list_response")
    # Actual response time never supplies source effective/available time.
    retrieved = response.retrieved_at.astimezone(ASIA_SHANGHAI)
    if retrieved.date().isoformat() != membership_date:
        raise _ParseFailure("current_list_date_mismatch")
    try:
        payload: object = json.loads(response.body)
    except (ValueError, UnicodeError):
        raise _ParseFailure("invalid_current_list_response") from None
    if type(payload) is not list:
        raise _ParseFailure("invalid_current_list_response")
    reports: list[dict[str, object]] = []
    for entry in payload:
        if type(entry) is dict and type(entry.get("metadata")) is dict:
            metadata = entry["metadata"]
            if metadata.get("catalogid") == "1110" and metadata.get("tabkey") == "tab1":
                reports.append(entry)
    if len(reports) != 1:
        raise _ParseFailure("ambiguous_current_list_report")
    report = reports[0]
    metadata = cast(dict[str, object], report["metadata"])
    cols = metadata.get("cols")
    expected_labels = {
        "agdm": "A股代码",
        "agjc": "A股简称",
        "agssrq": "A股<br>上市日期",
    }
    if (
        type(cols) is not dict
        or metadata.get("name") != "A股列表"
        or any(cols.get(field) != label for field, label in expected_labels.items())
    ):
        raise _ParseFailure("unproved_current_list_columns")
    source_label = metadata.get("subname")
    if type(source_label) is not str:
        raise _ParseFailure("unproved_list_date_label")
    list_date_label = source_label.strip()
    try:
        if date.fromisoformat(list_date_label).isoformat() != list_date_label:
            raise ValueError
    except ValueError:
        raise _ParseFailure("unproved_list_date_label") from None
    rows = report.get("data")
    if "error" not in report or report["error"] is not None or type(rows) is not list:
        raise _ParseFailure("invalid_current_list_response")
    number = _integer(metadata.get("pageno"), minimum=1)
    pages = _integer(metadata.get("pagecount"), minimum=1)
    size = _integer(metadata.get("pagesize"), minimum=1)
    count = _integer(metadata.get("recordcount"), minimum=0)
    if (
        number != page
        or pages != max(1, (count + size - 1) // size)
        or len(rows) > size
    ):
        raise _ParseFailure("invalid_pagination")
    expected_rows = min(size, max(0, count - (page - 1) * size))
    if len(rows) > expected_rows:
        raise _ParseFailure("page_row_count_mismatch")
    records: list[CurrentListRecord] = []
    rejected = 0
    for row in rows:
        try:
            records.append(
                _record(
                    row,
                    page=page,
                    retrieved_at=retrieved.isoformat(),
                    list_date_label=list_date_label,
                )
            )
        except _ParseFailure:
            rejected += 1
    return _Page(
        number,
        pages,
        size,
        count,
        tuple(records),
        rejected,
        len(rows) < expected_rows,
        list_date_label,
    )


class _Pages:
    def __init__(
        self,
        transport: HttpTransport | None,
        membership_date: str,
        max_pages: int,
        current_date: str,
    ) -> None:
        self.transport = transport
        self.membership_date = membership_date
        self.max_pages = max_pages
        self.current_date = current_date
        self.lock = Lock()
        self.pages: list[_Page] = []
        self.failure: str | None = None

    def execute(self, invocation: ProviderInvocation) -> None:
        if self.membership_date != self.current_date:
            with self.lock:
                self.failure = "current_list_cannot_prove_historical_membership"
            return
        transport = self.transport
        owned_transport = transport is None
        if transport is None:
            transport = UrlLibTransport(provider_invocation=invocation)
        total_pages = 1
        number = 1
        while number <= min(total_pages, self.max_pages):
            url = f"{SzseStockListOperation.endpoint}?{urlencode({'SHOWTYPE': 'JSON', 'CATALOGID': '1110', 'TABKEY': 'tab1', 'PAGENO': number, 'tab1PAGESIZE': 20})}"
            headers = {
                "Accept": "application/json",
                "Referer": "https://www.szse.cn/market/product/stock/list/",
                "User-Agent": "a-share-research-skill/1",
            }
            # Injected transports promise one synchronous call, without retry or
            # redirect. The owned urllib transport counts each actual leaf itself.
            response = (
                transport.get(url, headers)
                if owned_transport
                else invocation.call(
                    ProductionInvocationAdapter(partial(transport.get, url, headers))
                )
            )
            try:
                parsed = _parse(
                    response, page=number, membership_date=self.membership_date
                )
            except _ParseFailure as error:
                with self.lock:
                    self.failure = error.code
                raise
            with self.lock:
                if self.pages and (parsed.pages, parsed.size, parsed.count) != (
                    self.pages[0].pages,
                    self.pages[0].size,
                    self.pages[0].count,
                ):
                    self.failure = "pagination_count_drift"
                    raise _ParseFailure(self.failure)
                if (
                    self.pages
                    and parsed.list_date_label != self.pages[0].list_date_label
                ):
                    self.failure = "list_date_label_drift"
                    raise _ParseFailure(self.failure)
                self.pages.append(parsed)
            total_pages = parsed.pages
            number += 1

    def snapshot(self) -> tuple[tuple[_Page, ...], str | None]:
        with self.lock:
            return tuple(self.pages), self.failure


def acquire_szse_current_list(
    request: UniverseRequest,
    *,
    research_now: datetime,
    transport: HttpTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
    max_pages: int,
) -> CurrentListAcquisition:
    """Fetch finite current-list pages, without asserting historical membership.

    Inject a synchronous atomic transport for tests. Explicit ``None`` selects
    the existing urllib transport with invocation-owned redirect accounting.
    Bounds are caller-declared workload limits, not provider capacity claims.
    """
    prepared = prepare_universe_request(request, research_now=research_now)
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("The current-list page budget must be a positive integer.")
    if isinstance(transport, UrlLibTransport):
        raise TypeError("The source must own the counted urllib transport.")
    pages = _Pages(
        transport,
        prepared.screen_date,
        max_pages,
        research_now.astimezone(ASIA_SHANGHAI).date().isoformat(),
    )
    result = lanes.run(provider_group="szse", adapter=pages, policy=policy)
    observed, failure = pages.snapshot()
    all_records = tuple(record for item in observed for record in item.records)
    records = tuple({record.code: record for record in reversed(all_records)}.values())
    records = tuple(sorted(records, key=lambda record: record.code))
    rejected = sum(item.rejected_rows for item in observed)
    reported = observed[0].count if observed else None
    expected_pages = observed[0].pages if observed else None
    codes = [record.code for record in all_records]
    limitations = [
        "membership_effective_time_unknown",
        "availability_time_unknown",
        "historical_range_unproved",
        "list_date_label_semantics_unproved",
    ]
    if failure:
        limitations.append(failure)
    if result.failure_code:
        limitations.append(result.failure_code)
    if any(item.short_page for item in observed):
        limitations.append("page_row_count_mismatch")
    if rejected:
        limitations.append("invalid_current_list_row")
    if len(set(codes)) != len(codes):
        limitations.append("duplicate_current_list_identity")
    complete = (
        result.status == "succeeded"
        and expected_pages == len(observed)
        and not rejected
        and len(set(codes)) == len(codes)
        and reported == len(records)
    )
    if expected_pages is not None and expected_pages > max_pages:
        limitations.append("page_budget_exhausted")
    source_result: SourceResult = "partial" if observed else "unavailable"
    if failure == "source_access_blocked" and not observed:
        source_result = "credential_blocked"
    return CurrentListAcquisition(
        prepared.screen_date,
        prepared.information_cutoff,
        research_now.isoformat(),
        source_result,
        reported,
        expected_pages,
        len(observed),
        complete,
        records,
        rejected,
        tuple(limitations),
        result,
        tuple(dict.fromkeys(item.list_date_label for item in observed)),
    )


class _RequestView(Protocol):
    screen_date: str
    information_cutoff: str


class _ListingView(Protocol):
    exchange: str
    code: str
    name: str
    security_category: str


class SzseCurrentIdentityAdapter:
    """Resolve prepared #110 identities from the real I-shaped batch source.

    A complete identity query means all requested canonical code/name pairs were
    observed. Unknown listing state, effective interval and availability remain
    None, so the existing #110 Module keeps them unranked and incomplete.
    """

    exchange = "SZSE"
    operation_id = "szse_current_identity@1"

    def __init__(
        self,
        *,
        research_now: datetime,
        transport: HttpTransport | None,
        lanes: ProviderLanes,
        policy: ProviderLanePolicy,
        max_pages: int,
    ) -> None:
        self._research_now = research_now
        self._transport = transport
        self._lanes = lanes
        self._policy = policy
        self._max_pages = max_pages
        self._last_acquisition: CurrentListAcquisition | None = None

    @property
    def last_acquisition(self) -> CurrentListAcquisition | None:
        """The detached source snapshot, including authoritative lane attempts."""
        return self._last_acquisition

    def resolve(
        self, request: object, listings: tuple[object, ...], timeout_seconds: float
    ) -> IdentityBatchOutcome:
        expected: dict[str, str] = {}
        for listing in listings:
            view = cast(_ListingView, listing)
            exchange, code, name, security_category = (
                view.exchange,
                view.code,
                view.name,
                view.security_category,
            )
            if exchange != "SZSE" or security_category != "A_SHARE" or code in expected:
                raise ValueError(
                    "The identity request must use unique prepared SZSE A-share records."
                )
            expected[code] = name
        if not expected:
            # I did not prove a zero-row SZSE response contract. No request is
            # manufactured just to attest a caller's empty identity denominator.
            self._last_acquisition = None
            return IdentityBatchOutcome(
                self.operation_id, "SZSE", False, "unavailable", 0, ()
            )
        request_view = cast(_RequestView, request)
        detached_request = UniverseRequest(
            request_view.screen_date, request_view.information_cutoff
        )
        acquisition = acquire_szse_current_list(
            detached_request,
            research_now=self._research_now,
            transport=self._transport,
            lanes=self._lanes,
            policy=replace(
                self._policy,
                timeout_seconds=min(self._policy.timeout_seconds, timeout_seconds),
            ),
            max_pages=self._max_pages,
        )
        self._last_acquisition = acquisition
        identities = tuple(
            UniverseIdentity(
                "SZSE",
                row.code,
                row.name,
                "A_SHARE",
                None,
                None,
                None,
                row.retrieved_at,
                None,
                row.retrieved_at,
                row.provenance_uri,
            )
            for row in acquisition.records
            if expected.get(row.code) == row.name
        )
        # observed_at is the owner's actual observation of the retrieved response;
        # it is neither metadata.subname nor source-publication or effective time.
        attempted = bool(acquisition.lane_result.attempts)
        result: SourceResult = "unavailable"
        if identities:
            result = "complete" if len(identities) == len(expected) else "partial"
        elif acquisition.source_result == "credential_blocked":
            result = "credential_blocked"
        return IdentityBatchOutcome(
            self.operation_id, "SZSE", attempted, result, len(expected), identities
        )
