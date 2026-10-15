"""Bounded SSE point identities using the accepted, P-verified source parser.

This is a current identity path, not a whole-list acquisition contract. A-share
scope comes from the caller's prepared expected list; opaque source type/status
or date tokens cannot establish security category or historical membership.
"""

from __future__ import annotations

import json

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import partial
from threading import Lock
from time import time_ns
from unicodedata import category
from urllib.parse import urlencode
from typing import Protocol, cast

from .identity_sources import (
    MAX_RESPONSE_BYTES,
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    SseStockListOperation,
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
class SseIdentityAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    expected_count: int
    records: tuple[UniverseIdentity, ...]
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "sse_current_identity@1",
            "exchange": "SSE",
            "membership_effective_date": self.membership_effective_date,
            "information_cutoff": self.information_cutoff,
            "research_now": self.research_now,
            "membership_proven": False,
            "attempted": bool(self.lane_result.attempts),
            "source_result": self.source_result,
            "expected_count": self.expected_count,
            "resolved_count": len(self.records),
            "failed_count": self.expected_count - len(self.records),
            "records": [
                {
                    "code": row.code,
                    "name": row.name,
                    "listing_state": row.listing_state,
                    "effective_from": row.effective_from,
                    "effective_to": row.effective_to,
                    "observed_at": row.observed_at,
                    "available_at": row.available_at,
                    "retrieved_at": row.retrieved_at,
                    "provenance_uri": row.provenance_uri,
                }
                for row in self.records
            ],
            "limitation_codes": list(self.limitation_codes),
            "attempts": [
                {
                    "provider_group": item.provider_group,
                    "sequence": item.sequence,
                    "state": item.state,
                }
                for item in self.lane_result.attempts
            ],
            "quiescence": self.lane_result.quiescence.state,
        }


class _PointTransport:
    def __init__(
        self,
        transport: HttpTransport | None,
        invocation: ProviderInvocation,
        timeout_seconds: float,
        membership_date: str | None,
    ) -> None:
        self.transport = transport
        self.invocation = invocation
        self.timeout_seconds = timeout_seconds
        self.membership_date = membership_date

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        # The accepted urllib transport counts each physical request/redirect.
        # Injected transports promise one leaf call without hidden retries.
        transport = self.transport
        response = (
            UrlLibTransport(provider_invocation=self.invocation).get_with_timeout(
                url, headers, self.timeout_seconds
            )
            if transport is None
            else self.invocation.call(
                ProductionInvocationAdapter(
                    partial(self._get_one, transport, url, headers)
                )
            )
        )
        if response.status in {401, 403}:
            raise TransportError("source_access_blocked", "Source access is blocked.")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise TransportError("response_too_large", "Source response is too large.")
        retrieved = response.retrieved_at
        if (
            retrieved.tzinfo is None
            or retrieved.utcoffset() is None
            or (
                self.membership_date is not None
                and retrieved.astimezone(ASIA_SHANGHAI).date().isoformat()
                != self.membership_date
            )
        ):
            raise TransportError(
                "current_identity_date_mismatch", "Source retrieval date is invalid."
            )
        return response

    @staticmethod
    def _get_one(
        transport: HttpTransport, url: str, headers: dict[str, str]
    ) -> HttpResponse:
        response = transport.get(url, headers)
        if response.status == 429:
            # Normalize rate limiting inside the actual leaf, so #119 owns retry.
            raise TransportError("rate_limited", "The source request was rate limited.")
        return response


class _PointQueries:
    def __init__(
        self,
        expected: tuple[tuple[str, str], ...],
        *,
        membership_date: str,
        current_date: str,
        transport: HttpTransport | None,
        timeout_seconds: float,
        max_securities: int,
    ) -> None:
        self.expected = expected
        self.membership_date = membership_date
        self.current_date = current_date
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_securities = max_securities
        self.lock = Lock()
        self.records: list[UniverseIdentity] = []
        self.failures: list[str] = []

    def execute(self, invocation: ProviderInvocation) -> None:
        if self.membership_date != self.current_date:
            with self.lock:
                self.failures.append(
                    "current_identity_cannot_prove_historical_membership"
                )
            return
        transport = _PointTransport(
            self.transport, invocation, self.timeout_seconds, self.membership_date
        )
        operation = SseStockListOperation()
        for code, name in self.expected[: self.max_securities]:
            try:
                query = operation.query(code, transport)
            except SourceOperationError as error:
                # Keep only owner-defined reason codes, never supplier exception text.
                reason = (
                    "source_access_blocked"
                    if error.code == "source_access_blocked"
                    else "current_identity_date_mismatch"
                    if error.code == "current_identity_date_mismatch"
                    else "point_identity_unavailable"
                )
                with self.lock:
                    self.failures.append(reason)
                continue
            if (
                not query.query_complete
                or len(query.observations) != 1
                or query.observations[0].code != code
                or query.observations[0].name != name
                or query.observations[0].exchange != "SSE"
            ):
                with self.lock:
                    self.failures.append("point_identity_unproved")
                continue
            row = query.observations[0]
            retrieved = row.retrieved_at.astimezone(ASIA_SHANGHAI).isoformat()
            identity = UniverseIdentity(
                "SSE",
                code,
                name,
                "A_SHARE",
                None,
                None,
                None,
                retrieved,
                None,
                retrieved,
                f"exchange-identity:SSE:COMMON_SSE_ZQPZ_GP_GPLB_C:security:{code}",
            )
            with self.lock:
                self.records.append(identity)
        if len(self.expected) > self.max_securities:
            with self.lock:
                self.failures.append("security_query_budget_exhausted")

    def snapshot(self) -> tuple[tuple[UniverseIdentity, ...], tuple[str, ...]]:
        with self.lock:
            return tuple(self.records), tuple(self.failures)


class _RequestView(Protocol):
    screen_date: str
    information_cutoff: str


class _ListingView(Protocol):
    exchange: str
    code: str
    name: str
    security_category: str


class SseCurrentIdentityAdapter:
    """Resolve finite prepared expected identities without inventing bulk or PIT proof."""

    exchange = "SSE"
    operation_id = "sse_current_identity@1"

    def __init__(
        self,
        *,
        research_now: datetime,
        transport: HttpTransport | None,
        lanes: ProviderLanes,
        policy: ProviderLanePolicy,
        max_securities: int,
    ) -> None:
        if type(max_securities) is not int or max_securities < 1:
            raise ValueError(
                "The point identity query budget must be a positive integer."
            )
        if isinstance(transport, UrlLibTransport):
            raise TypeError("The source must own the counted urllib transport.")
        self._research_now = research_now
        self._transport = transport
        self._lanes = lanes
        self._policy = policy
        self._max_securities = max_securities
        self._last_acquisition: SseIdentityAcquisition | None = None

    @property
    def last_acquisition(self) -> SseIdentityAcquisition | None:
        return self._last_acquisition

    def resolve(
        self, request: object, listings: tuple[object, ...], timeout_seconds: float
    ) -> IdentityBatchOutcome:
        expected: dict[str, str] = {}
        for listing in listings:
            view = cast(_ListingView, listing)
            if (
                view.exchange != "SSE"
                or view.security_category != "A_SHARE"
                or re.fullmatch(r"[0-9]{6}", view.code) is None
                or view.code in expected
            ):
                raise ValueError(
                    "Expected identities must be unique prepared SSE A shares."
                )
            expected[view.code] = view.name
        if not expected:
            self._last_acquisition = None
            return IdentityBatchOutcome(
                self.operation_id, "SSE", False, "unavailable", 0, ()
            )
        view_request = cast(_RequestView, request)
        prepared = prepare_universe_request(
            UniverseRequest(view_request.screen_date, view_request.information_cutoff),
            research_now=self._research_now,
        )
        bounded_policy = replace(
            self._policy,
            timeout_seconds=min(self._policy.timeout_seconds, timeout_seconds),
        )
        queries = _PointQueries(
            tuple(sorted(expected.items())),
            membership_date=prepared.screen_date,
            current_date=self._research_now.astimezone(ASIA_SHANGHAI)
            .date()
            .isoformat(),
            transport=self._transport,
            timeout_seconds=bounded_policy.call_timeout_seconds
            or bounded_policy.timeout_seconds,
            max_securities=self._max_securities,
        )
        lane_result = self._lanes.run(
            provider_group="sse", adapter=queries, policy=bounded_policy
        )
        records, failures = queries.snapshot()
        limitations = (
            "membership_effective_time_unknown",
            "availability_time_unknown",
            "historical_range_unproved",
            "point_selector_not_whole_list",
            *failures,
            *((lane_result.failure_code,) if lane_result.failure_code else ()),
        )
        source_result: SourceResult = "unavailable"
        if records:
            source_result = "complete" if len(records) == len(expected) else "partial"
        elif "source_access_blocked" in failures:
            source_result = "credential_blocked"
        self._last_acquisition = SseIdentityAcquisition(
            prepared.screen_date,
            prepared.information_cutoff,
            self._research_now.isoformat(),
            source_result,
            len(expected),
            records,
            tuple(dict.fromkeys(limitations)),
            lane_result,
        )
        return IdentityBatchOutcome(
            self.operation_id,
            "SSE",
            bool(lane_result.attempts),
            source_result,
            len(expected),
            records,
        )


@dataclass(frozen=True, slots=True)
class SseListRecord:
    code: str
    name: str
    displayed_listing_date: str
    retrieved_at: str
    provenance_uri: str
    displayed_delisting_date: str | None = None
    a_stock_code: str | None = None
    b_stock_code: str | None = None
    security_category: str | None = None
    membership_effective_at: None = None
    available_at: None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": "SSE",
            "code": self.code,
            "name": self.name,
            "displayed_listing_date": self.displayed_listing_date,
            "displayed_delisting_date": self.displayed_delisting_date,
            "a_stock_code": self.a_stock_code,
            "b_stock_code": self.b_stock_code,
            "component_absence_proven": False,
            "source_date_semantics": "display_listing_date_only",
            "security_category": self.security_category,
            "security_class_proven": self.security_category is not None,
            "classification_as_of_proven": False,
            "classification_basis": "official_B_share_component"
            if self.security_category == "B_SHARE"
            else "official_scope_and_C20_inference"
            if self.security_category
            else "unproved",
            "classification_rule_versions": [
                "SSE_product_status_3.540",
                "SSE_special_markers_2025_07",
            ],
            "listing_state_proven": False,
            "membership_effective_at": self.membership_effective_at,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
        }


@dataclass(frozen=True, slots=True)
class _MainboardPage:
    number: int
    count: int
    pages: int
    records: tuple[SseListRecord, ...]
    rejected: int
    short: bool


@dataclass(frozen=True, slots=True)
class SseListAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    reported_count: int | None
    pages_expected: int | None
    pages_received: int
    pages_complete: bool
    records: tuple[SseListRecord, ...]
    rejected_rows: int
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]

    source_scope: str = "SSE_MAINBOARD_A_SELECTOR"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "sse_list_partition_acquisition@1",
            "exchange": "SSE",
            "source_scope": self.source_scope,
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
                    "provider_group": a.provider_group,
                    "sequence": a.sequence,
                    "state": a.state,
                }
                for a in self.lane_result.attempts
            ],
            "quiescence": self.lane_result.quiescence.state,
        }


class _MainboardParseFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(
            "The current mainboard list does not match its retained contract."
        )
        self.code = code


_SSE_MAINBOARD_FIELDS = frozenset(
    {
        "AREA_NAME",
        "AREA_NAME_DESC",
        "A_STOCK_CODE",
        "B_STOCK_CODE",
        "COMPANY_ABBR",
        "COMPANY_ABBR_EN",
        "COMPANY_CODE",
        "CSRC_CODE",
        "CSRC_CODE_DESC",
        "DELIST_DATE",
        "FULL_NAME",
        "FULL_NAME_IN_ENGLISH",
        "LIST_BOARD",
        "LIST_DATE",
        "NUM",
        "PRODUCT_STATUS",
        "SEC_NAME_CN",
        "SEC_NAME_FULL",
        "STATE_CODE",
        "STATE_CODE_STOCK",
        "STOCK_TYPE",
    }
)


# Only these UI callers and actual schemas are retained in verified R/S records.
# The profiles remain source acquisition scopes, not declared pure-A Universe owners.
_SSE_LIST_PARTITIONS: dict[str, tuple[str, str, str, str, str, tuple[str, ...]]] = {
    "active_mainboard": (
        "1",
        "2,4,5,7,8",
        "/sseQuery/commonQuery.do",
        "share",
        "SSE_MAINBOARD_A_SELECTOR",
        ("1",),
    ),
    "active_star": (
        "8",
        "2,4,5,7,8",
        "/sseQuery/commonQuery.do",
        "share",
        "SSE_ACTIVE_STAR_SELECTOR",
        ("8",),
    ),
    "terminated_mainboard": (
        "1,2",
        "3",
        "/commonQuery.do",
        "delisting",
        "SSE_TERMINATED_MAINBOARD_AB_SELECTOR",
        ("1", "2"),
    ),
    "terminated_star": (
        "8",
        "3",
        "/commonQuery.do",
        "delisting",
        "SSE_TERMINATED_STAR_SELECTOR",
        ("8",),
    ),
}


def _mainboard_integer(value: object, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise _MainboardParseFailure("invalid_mainboard_pagination")
    return value


# U: formal field29 is C20; undefined positions are spaces. Website-to-field
# association is an inference from its name, same fifth slot and guide article3.
_C20_SLOTS = (
    " N",
    " D",
    " R",
    " DSPTU",
    " Y",
    " LM",
    " FS",
    " U",
    " W",
    " YN",
    " Y",
    *([" "] * 9),
)


def _current_security_category(
    row: dict[str, str], *, a_code: str | None, b_code: str | None
) -> str | None:
    # A positive B component never removes an A component in the same row.
    if row["STOCK_TYPE"] == "2" and a_code is None and b_code is not None:
        return "B_SHARE"
    flags = row.get("PRODUCT_STATUS")
    if (
        flags is None
        or len(flags) != 20
        or any(value not in allowed for value, allowed in zip(flags, _C20_SLOTS))
        or row["STOCK_TYPE"] not in {"1", "8"}
        or a_code is None
    ):
        return None
    if flags[4] == "Y":
        return "CDR"
    if flags[3] == "U":
        return "PREFERRED_SHARE"
    if flags[5] in {"L", "M"}:
        return "BOND"
    # Missing domestic stock state is unknown, not proof from an absent D suffix.
    if flags[3] not in {"D", "S", "P", "T"}:
        return None
    return "A_SHARE"


def _mainboard_record(
    value: object, *, page: int, retrieved_at: str, partition: str
) -> SseListRecord:
    if not isinstance(value, dict):
        raise _MainboardParseFailure("invalid_mainboard_row")
    required = {"A_STOCK_CODE", "SEC_NAME_CN", "LIST_DATE", "STOCK_TYPE"}
    if partition.startswith("terminated_"):
        required.add("DELIST_DATE")
    if not required <= set(value) <= _SSE_MAINBOARD_FIELDS or any(
        type(v) is not str for v in value.values()
    ):
        raise _MainboardParseFailure("invalid_mainboard_row")
    row = cast(dict[str, str], value)
    code, name, label = (
        row["A_STOCK_CODE"],
        row["SEC_NAME_CN"].strip(),
        row["LIST_DATE"],
    )

    def component(raw: str) -> str | None:
        if raw in {"", "-"}:
            return None
        if re.fullmatch(r"[0-9]{6}", raw) is None:
            raise _MainboardParseFailure("invalid_mainboard_row")
        return raw

    a_code = component(row["A_STOCK_CODE"])
    b_code = component(row.get("B_STOCK_CODE", ""))
    if a_code is None and partition != "terminated_mainboard":
        raise _MainboardParseFailure("invalid_mainboard_row")
    code = a_code or b_code or ""
    if (
        row["STOCK_TYPE"] not in _SSE_LIST_PARTITIONS[partition][5]
        or re.fullmatch(r"[0-9]{6}", code) is None
        or not name
        or len(name) > 256
        or any(category(c).startswith("C") for c in name)
        or any(c in name for c in ("/", "\\", "<", ">"))
        or re.fullmatch(r"[0-9]{8}", label) is None
    ):
        raise _MainboardParseFailure("invalid_mainboard_row")
    try:
        displayed = date.fromisoformat(
            f"{label[:4]}-{label[4:6]}-{label[6:]}"
        ).isoformat()
    except ValueError:
        raise _MainboardParseFailure("invalid_mainboard_row") from None
    delisted: str | None = None
    if partition.startswith("terminated_"):
        raw = row["DELIST_DATE"]
        if re.fullmatch(r"[0-9]{8}", raw) is None:
            raise _MainboardParseFailure("invalid_mainboard_row")
        try:
            delisted = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}").isoformat()
            if delisted < displayed:
                raise ValueError
        except ValueError:
            raise _MainboardParseFailure("invalid_mainboard_row") from None
    # LIST_DATE has an official display label, not a proved effective interval.
    # PRODUCT_STATUS / STATE_CODE remain opaque; positive A/B components are retained.
    return SseListRecord(
        code,
        name,
        displayed,
        retrieved_at,
        f"exchange-list:SSE:{partition}:page:{page}:security:{code}",
        displayed_delisting_date=delisted,
        a_stock_code=a_code,
        b_stock_code=b_code,
        security_category=_current_security_category(row, a_code=a_code, b_code=b_code),
    )


def _parse_mainboard_page(
    response: HttpResponse, *, page: int, partition: str
) -> _MainboardPage:
    if response.status != 200:
        raise _MainboardParseFailure("upstream_http_error")
    if response.content_type.split(";", 1)[0].strip().lower() not in {
        "application/json",
        "application/javascript",
        "text/javascript",
    }:
        raise _MainboardParseFailure("unexpected_content_type")
    try:
        text = response.body.decode("utf-8")
        match = re.fullmatch(r"\s*jsonpCallback0\((.*)\);?\s*", text, re.DOTALL)
        if match is None:
            raise ValueError
        payload = json.loads(match.group(1))
    except (ValueError, UnicodeDecodeError):
        raise _MainboardParseFailure("invalid_static_jsonp") from None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("result"), list)
        or not isinstance(payload.get("pageHelp"), dict)
    ):
        raise _MainboardParseFailure("missing_mainboard_page")
    if any(payload.get(k) for k in ("actionErrors", "fieldErrors", "actionMessages")):
        raise _MainboardParseFailure("source_business_failure")
    metadata = payload["pageHelp"]
    count = _mainboard_integer(metadata.get("total"), minimum=1)
    pages = _mainboard_integer(metadata.get("pageCount"), minimum=1)
    if (
        any(
            type(metadata.get(k)) is not int or metadata[k] != page
            for k in ("beginPage", "endPage", "pageNo")
        )
        or type(metadata.get("cacheSize")) is not int
        or metadata["cacheSize"] != 1
        or type(metadata.get("pageSize")) is not int
        or metadata["pageSize"] != 25
        or pages != (count + 24) // 25
        or page > pages
    ):
        raise _MainboardParseFailure("invalid_mainboard_pagination")
    rows = payload["result"]
    expected_rows = min(25, count - (page - 1) * 25)
    if len(rows) > expected_rows:
        raise _MainboardParseFailure("page_row_count_mismatch")
    records: list[SseListRecord] = []
    rejected = 0
    retrieved = response.retrieved_at.astimezone(ASIA_SHANGHAI).isoformat()
    for row in rows:
        try:
            records.append(
                _mainboard_record(
                    row, page=page, retrieved_at=retrieved, partition=partition
                )
            )
        except _MainboardParseFailure:
            rejected += 1
    return _MainboardPage(
        page, count, pages, tuple(records), rejected, len(rows) < expected_rows
    )


class _MainboardPages:
    def __init__(
        self,
        *,
        membership_date: str,
        current_date: str,
        transport: HttpTransport | None,
        timeout_seconds: float,
        max_pages: int,
        partition: str,
    ) -> None:
        self.membership_date = membership_date
        self.current_date = current_date
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self.partition = partition
        self.lock = Lock()
        self.pages: list[_MainboardPage] = []
        self.failure: str | None = None

    def execute(self, invocation: ProviderInvocation) -> None:
        historical_carrier = self.partition.startswith("terminated_")
        if not historical_carrier and self.membership_date != self.current_date:
            with self.lock:
                self.failure = "current_list_cannot_prove_historical_membership"
            return
        # Current event catalogs can carry historical facts retrieved after cutoff.
        # They still have no proved available/effective version and cannot be adopted.
        transport = _PointTransport(
            self.transport,
            invocation,
            self.timeout_seconds,
            None if historical_carrier else self.membership_date,
        )
        # Actual cache-busting time is transport metadata, not the frozen research clock.
        cache_token = time_ns() // 1_000_000
        profile = _SSE_LIST_PARTITIONS[self.partition]
        number, total_pages = 1, 1
        while number <= min(total_pages, self.max_pages):
            params = {
                "STOCK_TYPE": profile[0],
                "REG_PROVINCE": "",
                "CSRC_CODE": "",
                "STOCK_CODE": "",
                "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
                "COMPANY_STATUS": profile[1],
                "type": "inParams",
                "isPagination": "true",
                "pageHelp.cacheSize": 1,
                "pageHelp.beginPage": number,
                "pageHelp.pageSize": 25,
                "pageHelp.pageNo": number,
                "pageHelp.endPage": number,
                "jsonCallBack": "jsonpCallback0",
                "_": cache_token,
            }
            url = "https://query.sse.com.cn" + profile[2] + "?" + urlencode(params)
            headers = {
                "Accept": "application/json,text/javascript,*/*;q=0.1",
                "Referer": "https://www.sse.com.cn/assortment/stock/list/"
                + profile[3]
                + "/",
                "User-Agent": "a-share-research-skill/1",
            }
            try:
                response = transport.get(url, headers)
                parsed = _parse_mainboard_page(
                    response, page=number, partition=self.partition
                )
            except (_MainboardParseFailure, TransportError) as error:
                with self.lock:
                    self.failure = (
                        error.code
                        if isinstance(error, _MainboardParseFailure)
                        else "source_access_blocked"
                        if error.code == "source_access_blocked"
                        else "current_list_unavailable"
                    )
                raise
            with self.lock:
                if self.pages and (parsed.pages, parsed.count) != (
                    self.pages[0].pages,
                    self.pages[0].count,
                ):
                    self.failure = "pagination_count_drift"
                    raise _MainboardParseFailure(self.failure)
                self.pages.append(parsed)
            total_pages = parsed.pages
            number += 1

    def snapshot(self) -> tuple[tuple[_MainboardPage, ...], str | None]:
        with self.lock:
            return tuple(self.pages), self.failure


def acquire_sse_list_partition(
    request: UniverseRequest,
    *,
    research_now: datetime,
    transport: HttpTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
    max_pages: int,
    partition: str = "active_mainboard",
) -> SseListAcquisition:
    """Parse one R/S-verified scope without claiming complete as-of membership."""
    if type(partition) is not str or partition not in _SSE_LIST_PARTITIONS:
        raise ValueError("The SSE source partition is not a verified caller.")
    prepared = prepare_universe_request(request, research_now=research_now)
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("The current-list page budget must be a positive integer.")
    if isinstance(transport, UrlLibTransport):
        raise TypeError("The source must own the counted urllib transport.")
    pages = _MainboardPages(
        membership_date=prepared.screen_date,
        current_date=research_now.astimezone(ASIA_SHANGHAI).date().isoformat(),
        transport=transport,
        timeout_seconds=policy.call_timeout_seconds or policy.timeout_seconds,
        max_pages=max_pages,
        partition=partition,
    )
    result = lanes.run(provider_group="sse", adapter=pages, policy=policy)
    observed, failure = pages.snapshot()
    all_records = tuple(row for p in observed for row in p.records)
    records = tuple(
        sorted(
            {row.code: row for row in reversed(all_records)}.values(),
            key=lambda row: row.code,
        )
    )
    rejected = sum(p.rejected for p in observed)
    count = observed[0].count if observed else None
    expected_pages = observed[0].pages if observed else None
    limitations = [
        "membership_effective_time_unknown",
        "availability_time_unknown",
        "historical_range_unproved",
        "source_partition_not_complete_sse_universe",
        "source_status_semantics_unproved",
    ]
    if any(row.security_category is None for row in records):
        limitations.append("security_class_unproved")
    if any(row.security_category not in {None, "A_SHARE"} for row in records):
        limitations.append("positive_non_a_records_present")
    if failure:
        limitations.append(failure)
    if result.failure_code:
        limitations.append(result.failure_code)
    if rejected:
        limitations.append("invalid_mainboard_row")
    if any(p.short for p in observed):
        limitations.append("page_row_count_mismatch")
    if len(all_records) != len(records):
        limitations.append("duplicate_current_list_identity")
    if expected_pages is not None and expected_pages > max_pages:
        limitations.append("page_budget_exhausted")
    complete = (
        result.status == "succeeded"
        and expected_pages == len(observed)
        and not rejected
        and count == len(all_records) == len(records)
    )
    source_result: SourceResult = "partial" if observed else "unavailable"
    if failure == "source_access_blocked" and not observed:
        source_result = "credential_blocked"
    return SseListAcquisition(
        prepared.screen_date,
        prepared.information_cutoff,
        research_now.isoformat(),
        source_result,
        count,
        expected_pages,
        len(observed),
        complete,
        records,
        rejected,
        tuple(limitations),
        result,
        _SSE_LIST_PARTITIONS[partition][4],
    )


def acquire_sse_mainboard_current_list(
    request: UniverseRequest,
    *,
    research_now: datetime,
    transport: HttpTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
    max_pages: int,
) -> SseListAcquisition:
    """The mainboard current path also used by its #110 identity Adapter."""
    return acquire_sse_list_partition(
        request,
        research_now=research_now,
        transport=transport,
        lanes=lanes,
        policy=policy,
        max_pages=max_pages,
        partition="active_mainboard",
    )


class SseMainboardCurrentIdentityAdapter:
    """Resolve prepared #110 identities from the real R-shaped mainboard source.

    A complete identity query means all requested canonical code/name pairs were
    observed. Unknown listing state, effective interval and availability remain
    None, so the existing #110 Module keeps them unranked and incomplete.
    """

    exchange = "SSE"
    operation_id = "sse_mainboard_current_identity@1"

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
        self._last_acquisition: SseListAcquisition | None = None

    @property
    def last_acquisition(self) -> SseListAcquisition | None:
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
            if exchange != "SSE" or security_category != "A_SHARE" or code in expected:
                raise ValueError(
                    "The identity request must use unique prepared SSE A-share records."
                )
            expected[code] = name
        if not expected:
            # R did not prove a zero-row mainboard response contract. No request is
            # manufactured just to attest a caller's empty identity denominator.
            self._last_acquisition = None
            return IdentityBatchOutcome(
                self.operation_id, "SSE", False, "unavailable", 0, ()
            )
        request_view = cast(_RequestView, request)
        detached_request = UniverseRequest(
            request_view.screen_date, request_view.information_cutoff
        )
        acquisition = acquire_sse_mainboard_current_list(
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
                "SSE",
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
            and row.security_category in {None, "A_SHARE"}
        )
        # observed_at is the owner's actual observation of the retrieved response;
        # it is neither LIST_DATE nor source-publication or effective time.
        attempted = bool(acquisition.lane_result.attempts)
        result: SourceResult = "unavailable"
        if identities:
            result = "complete" if len(identities) == len(expected) else "partial"
        elif acquisition.source_result == "credential_blocked":
            result = "credential_blocked"
        return IdentityBatchOutcome(
            self.operation_id, "SSE", attempted, result, len(expected), identities
        )
