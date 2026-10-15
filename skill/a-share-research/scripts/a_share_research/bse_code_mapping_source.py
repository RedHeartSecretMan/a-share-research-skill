"""Bounded acquisition of BSE's dated 248-company code-cutover document.

The dated cohort and code change are not a full-BSE historical roster. Displayed
listing dates retain the original selected-layer qualifier and never become BSE
admission dates merely because this document has an availability bound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from functools import partial
from html.parser import HTMLParser
from threading import Lock
from unicodedata import category

from .identity_sources import (
    MAX_RESPONSE_BYTES,
    HttpResponse,
    HttpTransport,
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
    SourceResult,
    UniverseRequest,
    prepare_universe_request,
)

ARTICLE_URL = "https://www.bse.cn/code_mapping/200025792.html"
PUBLICATION_DATE = "2025-09-12"
CONSERVATIVE_AVAILABLE_AT = "2025-09-13T00:00:00+08:00"
CODE_CHANGE_EFFECTIVE_DATE = "2025-10-09"
COHORT_SIZE = 248
DATE_QUALIFIER = "由全国股转系统原精选层平移进入北交所的上市公司，上表中上市日期字段为其在原精选层的挂牌日期。"
_HEADERS = ("序号", "证券简称", "上市日期", "旧代码", "新代码")


@dataclass(frozen=True, slots=True)
class BseCodeMappingRecord:
    old_code: str
    new_code: str
    name: str
    displayed_listing_date: str
    retrieved_at: str
    provenance_uri: str
    available_at: str | None = None
    code_change_effective_date: str | None = None
    bse_listing_effective_date: None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "old_code": self.old_code,
            "new_code": self.new_code,
            "name": self.name,
            "displayed_listing_date": self.displayed_listing_date,
            "listing_date_qualifier": DATE_QUALIFIER,
            "bse_listing_effective_date": self.bse_listing_effective_date,
            "code_change_effective_date": self.code_change_effective_date,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
        }


@dataclass(frozen=True, slots=True)
class BseCodeMappingAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    observed_pair_count: int | None
    cohort_complete: bool
    records: tuple[BseCodeMappingRecord, ...]
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "bse_dated_code_mapping_acquisition@1",
            "source_scope": "BSE_200025792_CODE_CUTOVER_COHORT",
            "publication_date": PUBLICATION_DATE,
            "cohort_expected_pairs": COHORT_SIZE,
            "observed_pair_count": self.observed_pair_count,
            "cohort_complete": self.cohort_complete,
            "membership_proven": False,
            "historical_expected_universe_count": None,
            "membership_effective_date": self.membership_effective_date,
            "information_cutoff": self.information_cutoff,
            "research_now": self.research_now,
            "attempted": bool(self.lane_result.attempts),
            "source_result": self.source_result,
            "records": [row.to_public_dict() for row in self.records],
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


class _MappingFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(
            "The code mapping does not match its retained source contract."
        )
        self.code = code


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.outside: list[str] = []
        self.skipped: str | None = None
        self.invalid = False
        self.row_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skipped = tag
        if self.skipped:
            return
        if tag == "table":
            if self.table is not None:
                self.invalid = True
            else:
                self.table = []
                self.row_count = 0
        elif tag == "tr" and self.table is not None:
            if self.row is not None:
                self.invalid = True
            self.row = []
            self.row_count += 1
        elif tag in {"td", "th"} and self.row is not None:
            if self.cell is not None:
                self.invalid = True
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if self.skipped:
            if tag == self.skipped:
                self.skipped = None
            return
        if tag in {"td", "th"} and self.cell is not None:
            if self.row is not None:
                self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None:
                self.invalid = True
            if self.table is not None:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.row is not None or self.cell is not None:
                self.invalid = True
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, text: str) -> None:
        if self.skipped:
            return
        if self.cell is not None:
            self.cell.append(text)
        elif self.table is None:
            self.outside.append(text)


def _mapping_record(cells: list[str], retrieved_at: str) -> BseCodeMappingRecord:
    if len(cells) != 5:
        raise _MappingFailure("invalid_mapping_row")
    _, name, label, old_code, new_code = cells
    if (
        re.fullmatch(r"[0-9]{6}", old_code) is None
        or re.fullmatch(r"[0-9]{6}", new_code) is None
        or not name
        or len(name) > 256
        or any(category(c).startswith("C") or c in "/\\<>" for c in name)
        or re.fullmatch(r"[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}", label) is None
    ):
        raise _MappingFailure("invalid_mapping_row")
    try:
        year, month, day = (int(p) for p in label.split("/"))
        displayed = date(year, month, day).isoformat()
    except ValueError:
        raise _MappingFailure("invalid_mapping_row") from None
    return BseCodeMappingRecord(
        old_code,
        new_code,
        name,
        displayed,
        retrieved_at,
        f"exchange-mapping:BSE:200025792:old:{old_code}:new:{new_code}",
    )


def _parse_mapping(
    response: HttpResponse,
) -> tuple[tuple[BseCodeMappingRecord, ...], bool, tuple[str, ...]]:
    if response.status in {401, 403}:
        raise _MappingFailure("source_access_blocked")
    if response.status != 200:
        raise _MappingFailure("upstream_http_error")
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise _MappingFailure("response_too_large")
    if response.content_type.split(";", 1)[0].strip().lower() not in {
        "text/html",
        "application/xhtml+xml",
    }:
        raise _MappingFailure("unexpected_content_type")
    retrieved = response.retrieved_at
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise _MappingFailure("invalid_retrieval_time")
    parser = _Tables()
    try:
        parser.feed(response.body.decode("utf-8"))
        parser.close()
    except (UnicodeDecodeError, ValueError):
        raise _MappingFailure("unknown_mapping_shape") from None
    if parser.invalid or parser.table is not None or parser.row is not None:
        raise _MappingFailure("unknown_mapping_shape")
    matches = [
        table for table in parser.tables if table and tuple(table[0]) == _HEADERS
    ]
    if len(matches) != 1:
        raise _MappingFailure("mapping_header_unproved")
    rows = matches[0][1:]
    records: list[BseCodeMappingRecord] = []
    rejected = 0
    for cells in rows:
        try:
            records.append(
                _mapping_record(cells, retrieved.astimezone(ASIA_SHANGHAI).isoformat())
            )
        except _MappingFailure:
            rejected += 1
    limitations = [
        "code_cutover_cohort_not_all_bse",
        "selected_layer_date_not_bse_admission",
        "subsequent_membership_events_unproved",
    ]
    unique = (
        len({row.old_code for row in records})
        == len(records)
        == len({row.new_code for row in records})
    )
    outside = " ".join(parser.outside)
    dated_version = "新旧代码对照表" in outside and PUBLICATION_DATE in outside
    complete = (
        len(rows) == COHORT_SIZE + 1
        and len(records) == COHORT_SIZE
        and rejected == 1
        and unique
    )
    version_proven = (
        complete
        and dated_version
        and all(row.displayed_listing_date <= PUBLICATION_DATE for row in records)
    )
    if not complete:
        limitations.append("code_mapping_cohort_incomplete")
    if not unique:
        limitations.append("duplicate_code_mapping")
    if not version_proven:
        limitations.append("dated_fact_version_unproved")
    if version_proven:
        records = [
            replace(
                row,
                available_at=CONSERVATIVE_AVAILABLE_AT,
                code_change_effective_date=CODE_CHANGE_EFFECTIVE_DATE,
            )
            for row in records
        ]
    return tuple(records), complete and version_proven, tuple(limitations)


class _MappingDocument:
    def __init__(self, transport: HttpTransport | None, timeout_seconds: float) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.lock = Lock()
        self.parsed: (
            tuple[tuple[BseCodeMappingRecord, ...], bool, tuple[str, ...]] | None
        ) = None
        self.failure: str | None = None

    @staticmethod
    def _get(
        transport: HttpTransport, url: str, headers: dict[str, str]
    ) -> HttpResponse:
        response = transport.get(url, headers)
        if response.status == 429:
            raise TransportError("rate_limited", "The source request was rate limited.")
        return response

    def execute(self, invocation: ProviderInvocation) -> None:
        headers = {
            "Accept": "text/html",
            "Referer": "https://www.bse.cn/service/code_mapping.html",
            "User-Agent": "a-share-research-skill/1",
        }
        try:
            response = (
                UrlLibTransport(provider_invocation=invocation).get_with_timeout(
                    ARTICLE_URL, headers, self.timeout_seconds
                )
                if self.transport is None
                else invocation.call(
                    ProductionInvocationAdapter(
                        partial(self._get, self.transport, ARTICLE_URL, headers)
                    )
                )
            )
            parsed = _parse_mapping(response)
        except (_MappingFailure, TransportError) as error:
            with self.lock:
                self.failure = (
                    error.code
                    if isinstance(error, _MappingFailure)
                    else "mapping_transport_unavailable"
                )
            raise
        with self.lock:
            self.parsed = parsed

    def snapshot(
        self,
    ) -> tuple[
        tuple[tuple[BseCodeMappingRecord, ...], bool, tuple[str, ...]] | None,
        str | None,
    ]:
        with self.lock:
            return self.parsed, self.failure


def acquire_bse_code_mapping(
    request: UniverseRequest,
    *,
    research_now: datetime,
    transport: HttpTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
) -> BseCodeMappingAcquisition:
    """Read the named dated cutover carrier, retaining scoped facts and real time."""
    prepared = prepare_universe_request(request, research_now=research_now)
    if isinstance(transport, UrlLibTransport):
        raise TypeError("The source must own the counted urllib transport.")
    document = _MappingDocument(
        transport, policy.call_timeout_seconds or policy.timeout_seconds
    )
    result = lanes.run(provider_group="bse", adapter=document, policy=policy)
    parsed, failure = document.snapshot()
    records, complete, limitations = parsed if parsed is not None else ((), False, ())
    codes = list(limitations)
    if failure:
        codes.append(failure)
    if result.failure_code:
        codes.append(result.failure_code)
    source_result: SourceResult = "unavailable"
    if parsed is not None:
        source_result = "complete" if complete else "partial"
    if failure == "source_access_blocked":
        source_result = "credential_blocked"
    return BseCodeMappingAcquisition(
        prepared.screen_date,
        prepared.information_cutoff,
        research_now.isoformat(),
        source_result,
        len(records) if parsed is not None else None,
        complete,
        records,
        tuple(codes),
        result,
    )
