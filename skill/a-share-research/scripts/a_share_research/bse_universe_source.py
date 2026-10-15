"""Bounded BSE current-list POST/JSONP acquisition from verified G/H facts.

The current rows have unknown membership/first-availability times. Displayed
listing dates retain the selected-layer transfer qualifier, never PIT authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from threading import Lock
from typing import Literal, Protocol, cast
from unicodedata import category
from urllib.parse import urlencode

from ._universe_http import is_permission_denied
from .identity_sources import (
    MAX_RESPONSE_BYTES,
    HttpResponse,
    TransportError,
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
    SourceResult,
    UniverseIdentity,
    UniverseRequest,
    prepare_universe_request,
)


@dataclass(frozen=True, slots=True)
class BseCurrentListQuery:
    page_index: int
    method: Literal["POST"] = "POST"
    endpoint: str = "https://www.bse.cn/nqxxController/nqxxCnzq.do"
    array_wire_encoding: Literal["form_brackets"] = "form_brackets"
    membership_effective_at: None = None
    available_at: None = None
    display_date_qualifier: str = (
        "精选层挂牌公司平移为本所上市公司的，本列表显示的上市日期为其在精选层挂牌日期"
    )

    def parameters(self) -> dict[str, object]:
        """Return detached semantic parameters, never an assumed HTTP body."""
        return {
            "page": self.page_index,
            "typejb": "T",
            "xxfcbj": ("2",),
            "xxzqdm": "",
            "sortfield": "xxzqdm",
            "sorttype": "asc",
        }

    def encoded_body(self) -> bytes:
        """H's positively validated bracket form; this was not browser wire capture."""
        return urlencode(
            {
                "page": self.page_index,
                "typejb": "T",
                "xxfcbj[]": "2",
                "xxzqdm": "",
                "sortfield": "xxzqdm",
                "sorttype": "asc",
            }
        ).encode("ascii")


def prepare_bse_current_list_query(
    page_index: int, *, page_budget: int
) -> BseCurrentListQuery:
    """Prepare a bounded zero-based page from the G-observed client declaration."""
    if (
        type(page_index) is not int
        or type(page_budget) is not int
        or page_budget < 1
        or page_index < 0
        or page_index >= page_budget
    ):
        raise ValueError("The current-list page must fall within its finite budget.")
    return BseCurrentListQuery(page_index)


class BsePostTransport(Protocol):
    """One synchronous POST, without hidden retry or redirect."""

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class BseCurrentListRecord:
    code: str
    name: str
    displayed_listing_date: str
    source_date_label: str
    source_update_label: str
    retrieved_at: str
    provenance_uri: str
    membership_effective_at: None = None
    available_at: None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": "BSE",
            "code": self.code,
            "name": self.name,
            "displayed_listing_date": self.displayed_listing_date,
            "display_date_qualifier": BseCurrentListQuery(0).display_date_qualifier,
            "source_date_label": self.source_date_label,
            "source_update_label": self.source_update_label,
            "source_time_semantics": "unproved",
            "membership_effective_at": self.membership_effective_at,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
        }


@dataclass(frozen=True, slots=True)
class _BsePage:
    number: int
    pages: int
    size: int
    count: int
    records: tuple[BseCurrentListRecord, ...]
    rejected_rows: int
    short_page: bool


@dataclass(frozen=True, slots=True)
class BseCurrentListAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    reported_count: int | None
    pages_expected: int | None
    pages_received: int
    pages_complete: bool
    records: tuple[BseCurrentListRecord, ...]
    rejected_rows: int
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "bse_current_list_acquisition@1",
            "exchange": "BSE",
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


_ROW_FIELDS = frozenset(
    {
        "fxssrq",
        "xxbldw",
        "xxbnsy",
        "xxcfgbz",
        "xxcqcx",
        "xxcyhbjq",
        "xxdqr",
        "xxdtjg",
        "xxdzdtjg",
        "xxdzztjg",
        "xxfcbj",
        "xxfxsgb",
        "xxghfl",
        "xxgprq",
        "xxgxsj",
        "xxhbzl",
        "xxhxcs",
        "xxhyzl",
        "xxisin",
        "xxjczq",
        "xxjgdw",
        "xxjsfl",
        "xxjsrq",
        "xxmbxl",
        "xxmgmz",
        "xxqtyw",
        "xxsbcs",
        "xxsldw",
        "xxsnsy",
        "xxssdq",
        "xxtpbz",
        "xxwltp",
        "xxxjxz",
        "xxyhsl",
        "xxywjc",
        "xxzbqs",
        "xxzgb",
        "xxzhbl",
        "xxzqdm",
        "xxzqjb",
        "xxzqjc",
        "xxzqqxr",
        "xxzrdw",
        "xxzrlx",
        "xxzrzt",
        "xxzsssl",
        "xxztjg",
        "xxzxsbsl",
    }
)
_IDENTITY_FIELDS = frozenset(
    {"xxzqdm", "xxzqjc", "fxssrq", "xxgprq", "xxfcbj", "xxzqjb", "xxjsrq", "xxgxsj"}
)
_JSONP_PREFIX = re.compile(
    r"^(?:/\*\*/\s*)?[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\s*\("
)


class _BseParseFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("The current list does not match its retained contract.")
        self.code = code


def _number(value: object, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise _BseParseFailure("invalid_pagination")
    return value


def _static_json(body: bytes) -> object:
    try:
        text = body.decode("utf-8-sig", "strict").strip()
        try:
            return cast(object, json.loads(text))
        except json.JSONDecodeError:
            prefix = _JSONP_PREFIX.match(text)
            if prefix is None:
                raise _BseParseFailure("invalid_static_jsonp") from None
            remaining = text[prefix.end() :].lstrip()
            payload, end = json.JSONDecoder().raw_decode(remaining)
            if remaining[end:].strip() not in {")", ");"}:
                raise _BseParseFailure("invalid_static_jsonp")
            return cast(object, payload)
    except (UnicodeError, ValueError, RecursionError):
        raise _BseParseFailure("invalid_static_jsonp") from None


def _bse_record(value: object, *, page: int, retrieved_at: str) -> BseCurrentListRecord:
    if type(value) is not dict:
        raise _BseParseFailure("invalid_current_list_row")
    row = cast(dict[str, object], value)
    if not (_IDENTITY_FIELDS <= set(row) <= _ROW_FIELDS) or any(
        type(row[field]) is not str or not row[field] for field in _IDENTITY_FIELDS
    ):
        raise _BseParseFailure("invalid_current_list_row")
    typed = cast(dict[str, str], row)
    code, name = typed["xxzqdm"], typed["xxzqjc"].strip()
    if (
        re.fullmatch(r"[0-9]{6}", code) is None
        or not name
        or len(name) > 256
        or any(category(character).startswith("C") for character in name)
        or any(marker in name for marker in ("/", "\\", "<", ">"))
    ):
        raise _BseParseFailure("invalid_current_list_row")
    if typed["xxfcbj"] != "2" or typed["xxzqjb"] != "T":
        raise _BseParseFailure("invalid_current_list_scope")
    try:
        for field in ("fxssrq", "xxgprq"):
            if re.fullmatch(r"[0-9]{8}", typed[field]) is None:
                raise ValueError
            datetime.strptime(typed[field], "%Y%m%d")
        displayed = datetime.strptime(typed["fxssrq"], "%Y%m%d").date().isoformat()
    except ValueError:
        raise _BseParseFailure("invalid_current_list_row") from None
    if (
        re.fullmatch(r"[0-9]{8}", typed["xxjsrq"]) is None
        or re.fullmatch(r"[0-9]{1,6}", typed["xxgxsj"]) is None
    ):
        raise _BseParseFailure("invalid_current_list_row")
    return BseCurrentListRecord(
        code,
        name,
        displayed,
        typed["xxjsrq"],
        typed["xxgxsj"],
        retrieved_at,
        f"exchange-list:BSE:page:{page}:security:{code}",
    )


def _parse_bse_page(
    response: HttpResponse, *, page: int, membership_date: str
) -> _BsePage:
    if response.status in {401, 403}:
        raise _BseParseFailure("source_access_blocked")
    if response.status != 200:
        raise _BseParseFailure("source_unavailable")
    if (
        len(response.body) > MAX_RESPONSE_BYTES
        or type(response.retrieved_at) is not datetime
        or response.retrieved_at.tzinfo is None
        or response.retrieved_at.utcoffset() is None
    ):
        raise _BseParseFailure("invalid_current_list_response")
    retrieved = response.retrieved_at.astimezone(ASIA_SHANGHAI)
    if retrieved.date().isoformat() != membership_date:
        raise _BseParseFailure("current_list_date_mismatch")
    payload = _static_json(response.body)
    if type(payload) is not list or len(payload) != 1 or type(payload[0]) is not dict:
        raise _BseParseFailure("invalid_current_list_response")
    metadata = cast(dict[str, object], payload[0])
    rows = metadata.get("content")
    if type(rows) is not list:
        raise _BseParseFailure("invalid_current_list_response")
    number = _number(metadata.get("number"), minimum=0)
    pages = _number(metadata.get("totalPages"), minimum=1)
    size = _number(metadata.get("size"), minimum=1)
    if size != 20:
        raise _BseParseFailure("unproved_current_list_page_size")
    count = _number(metadata.get("totalElements"), minimum=1)
    observed_rows = _number(metadata.get("numberOfElements"), minimum=0)
    if (
        number != page
        or pages != (count + size - 1) // size
        or number >= pages
        or observed_rows != len(rows)
        or len(rows) > size
    ):
        raise _BseParseFailure("invalid_pagination")
    if (
        type(metadata.get("firstPage")) is not bool
        or type(metadata.get("lastPage")) is not bool
        or metadata["firstPage"] != (number == 0)
        or metadata["lastPage"] != (number == pages - 1)
    ):
        raise _BseParseFailure("invalid_pagination_flags")
    expected_rows = min(size, count - number * size)
    if len(rows) > expected_rows:
        raise _BseParseFailure("page_row_count_mismatch")
    records: list[BseCurrentListRecord] = []
    rejected = 0
    for row in rows:
        try:
            records.append(
                _bse_record(row, page=page, retrieved_at=retrieved.isoformat())
            )
        except _BseParseFailure:
            rejected += 1
    return _BsePage(
        number, pages, size, count, tuple(records), rejected, len(rows) < expected_rows
    )


class _BsePages:
    def __init__(
        self,
        transport: BsePostTransport | None,
        membership_date: str,
        current_date: str,
        max_pages: int,
        timeout_seconds: float,
    ) -> None:
        self.transport = transport
        self.membership_date = membership_date
        self.current_date = current_date
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.lock = Lock()
        self.pages: list[_BsePage] = []
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
        number = 0
        total_pages = 1
        while number < min(total_pages, self.max_pages):
            query = prepare_bse_current_list_query(number, page_budget=self.max_pages)
            headers = {
                "Accept": "application/json, application/javascript",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.bse.cn/nq/listedcompany.html",
                "User-Agent": "a-share-research-skill/1",
            }
            body = query.encoded_body()
            try:
                response = (
                    cast(UrlLibTransport, transport).post_with_timeout(
                        query.endpoint, headers, body, self.timeout_seconds
                    )
                    if owned_transport
                    else invocation.call(
                        ProductionInvocationAdapter(
                            partial(transport.post, query.endpoint, headers, body)
                        )
                    )
                )
            except TransportError as error:
                with self.lock:
                    self.failure = (
                        "source_access_blocked"
                        if is_permission_denied(error)
                        else "current_list_unavailable"
                    )
                raise
            try:
                parsed = _parse_bse_page(
                    response, page=number, membership_date=self.membership_date
                )
            except _BseParseFailure as error:
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
                    raise _BseParseFailure(self.failure)
                self.pages.append(parsed)
            total_pages = parsed.pages
            number += 1

    def snapshot(self) -> tuple[tuple[_BsePage, ...], str | None]:
        with self.lock:
            return tuple(self.pages), self.failure


def acquire_bse_current_list(
    request: UniverseRequest,
    *,
    research_now: datetime,
    transport: BsePostTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
    max_pages: int,
) -> BseCurrentListAcquisition:
    """Execute the verified finite POST/JSONP path, with no historical backfill.

    Tests inject an atomic fixture transport. Explicit None opts into the existing
    urllib transport with the same leaf invoker for every request and redirect.
    """
    prepared = prepare_universe_request(request, research_now=research_now)
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("The current-list page budget must be a positive integer.")
    if isinstance(transport, UrlLibTransport):
        raise TypeError("The source must own the counted urllib transport.")
    pages = _BsePages(
        transport,
        prepared.screen_date,
        research_now.astimezone(ASIA_SHANGHAI).date().isoformat(),
        max_pages,
        policy.call_timeout_seconds or policy.timeout_seconds,
    )
    result = lanes.run(provider_group="bse", adapter=pages, policy=policy)
    observed, failure = pages.snapshot()
    all_records = tuple(record for item in observed for record in item.records)
    records = tuple(
        sorted(
            {record.code: record for record in reversed(all_records)}.values(),
            key=lambda record: record.code,
        )
    )
    rejected = sum(item.rejected_rows for item in observed)
    reported = observed[0].count if observed else None
    expected_pages = observed[0].pages if observed else None
    codes = [record.code for record in all_records]
    limitations = [
        "membership_effective_time_unknown",
        "availability_time_unknown",
        "historical_range_unproved",
        "display_listing_date_selected_layer_qualified",
        "source_update_time_semantics_unproved",
    ]
    if failure:
        limitations.append(failure)
    if result.failure_code:
        limitations.append(result.failure_code)
    if any(item.short_page for item in observed):
        limitations.append("page_row_count_mismatch")
    if rejected:
        limitations.append("invalid_current_list_row")
    if len(codes) != len(set(codes)):
        limitations.append("duplicate_current_list_identity")
    if expected_pages is not None and expected_pages > max_pages:
        limitations.append("page_budget_exhausted")
    complete = (
        result.status == "succeeded"
        and expected_pages == len(observed)
        and not rejected
        and len(codes) == len(set(codes))
        and reported == len(records)
    )
    source_result: SourceResult = "partial" if observed else "unavailable"
    if failure == "source_access_blocked" and not observed:
        source_result = "credential_blocked"
    return BseCurrentListAcquisition(
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
    )


class _RequestView(Protocol):
    screen_date: str
    information_cutoff: str


class _ListingView(Protocol):
    exchange: str
    code: str
    name: str
    security_category: str


class BseCurrentIdentityAdapter:
    """Resolve prepared #110 identities from the real H-shaped batch source.

    A complete identity query means all requested canonical code/name pairs were
    observed. Unknown listing state, effective interval and availability remain
    None, so the existing #110 Module keeps them unranked and incomplete.
    """

    exchange = "BSE"
    operation_id = "bse_current_identity@1"

    def __init__(
        self,
        *,
        research_now: datetime,
        transport: BsePostTransport | None,
        lanes: ProviderLanes,
        policy: ProviderLanePolicy,
        max_pages: int,
    ) -> None:
        self._research_now = research_now
        self._transport = transport
        self._lanes = lanes
        self._policy = policy
        self._max_pages = max_pages
        self._last_acquisition: BseCurrentListAcquisition | None = None

    @property
    def last_acquisition(self) -> BseCurrentListAcquisition | None:
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
            if exchange != "BSE" or security_category != "A_SHARE" or code in expected:
                raise ValueError(
                    "The identity request must use unique prepared BSE A-share records."
                )
            expected[code] = name
        if not expected:
            # H did not prove a zero-row BSE response contract. No request is
            # manufactured just to attest a caller's empty identity denominator.
            self._last_acquisition = None
            return IdentityBatchOutcome(
                self.operation_id, "BSE", False, "unavailable", 0, ()
            )
        request_view = cast(_RequestView, request)
        detached_request = UniverseRequest(
            request_view.screen_date, request_view.information_cutoff
        )
        acquisition = acquire_bse_current_list(
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
                "BSE",
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
        # it is neither xxjsrq/xxgxsj nor source-publication or effective time.
        attempted = bool(acquisition.lane_result.attempts)
        result: SourceResult = "unavailable"
        if identities:
            result = "complete" if len(identities) == len(expected) else "partial"
        elif acquisition.source_result == "credential_blocked":
            result = "credential_blocked"
        return IdentityBatchOutcome(
            self.operation_id, "BSE", attempted, result, len(expected), identities
        )
