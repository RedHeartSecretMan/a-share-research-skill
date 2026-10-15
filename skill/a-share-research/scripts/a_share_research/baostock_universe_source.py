"""Bounded given-day Baostock roster acquisition, before A-share qualification.

The provider roster contains indices as well as stocks. Its closed row count is
never an ordinary-A denominator, and current basic metadata cannot backfill a
historical name, class or listing-state version.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from threading import Lock
from typing import Protocol
from unicodedata import category

from .provider_lane import (
    ProductionInvocationAdapter,
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLaneResult,
    ProviderLanes,
)
from .universe_input import SourceResult, UniverseRequest, prepare_universe_request

_END = b"<![CDATA[]]>\n"
_MAX_RESPONSE_BYTES = 1_048_576
_FIELDS = ("code", "tradeStatus", "code_name")
_PROVIDER_CODE = re.compile(r"^(sh|sz)\.[0-9]{6}$")


@dataclass(frozen=True, slots=True)
class BaostockResponse:
    payload: bytes
    retrieved_at: datetime


class BaostockTransport(Protocol):
    """One synchronous application message, without retries or pagination."""

    def exchange(
        self, message_type: str, parameters: tuple[str, ...]
    ) -> BaostockResponse: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BaostockRosterRecord:
    provider_code: str
    name: str
    trade_status: str
    membership_effective_date: str
    retrieved_at: str
    provenance_uri: str = "baostock:query_all_stock:0.9.4"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider_code": self.provider_code,
            "name": self.name,
            "trade_status": self.trade_status,
            "membership_effective_date": self.membership_effective_date,
            "canonical_exchange": None,
            "security_category": None,
            "listing_state": None,
            "available_at": None,
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
        }


@dataclass(frozen=True, slots=True)
class BaostockRosterAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    pages_received: int
    pages_complete: bool
    closed_provider_count: int | None
    records: tuple[BaostockRosterRecord, ...]
    rejected_rows: int
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]
    expected_a_share_count: None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "baostock_dated_roster_acquisition@1",
            "source_scope": "provider_all_securities",
            "membership_effective_date": self.membership_effective_date,
            "information_cutoff": self.information_cutoff,
            "research_now": self.research_now,
            "membership_proven": False,
            "attempted": bool(self.lane_result.attempts),
            "source_result": self.source_result,
            "pages_received": self.pages_received,
            "pages_complete": self.pages_complete,
            "closed_provider_count": self.closed_provider_count,
            "expected_a_share_count": self.expected_a_share_count,
            "records": [row.to_public_dict() for row in self.records],
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


class _SourceFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _body(response: BaostockResponse, expected_type: str) -> tuple[str, ...]:
    if type(response) is not BaostockResponse:
        raise _SourceFailure("invalid_source_response")
    payload = response.payload
    if (
        type(payload) is not bytes
        or len(payload) > _MAX_RESPONSE_BYTES
        or not payload.endswith(_END)
    ):
        raise _SourceFailure("invalid_source_frame")
    retrieved = response.retrieved_at
    if (
        type(retrieved) is not datetime
        or retrieved.tzinfo is None
        or retrieved.utcoffset() is None
    ):
        raise _SourceFailure("invalid_retrieval_time")
    try:
        text = payload[: -len(_END)].decode("utf-8")
        header = text[:21].split("\x01")
        fields = tuple(text[21:].split("\x01"))
        if (
            len(header) != 3
            or header[1] != expected_type
            or not header[2].isdigit()
            or len(fields) < 4
        ):
            raise ValueError
    except (UnicodeError, ValueError):
        raise _SourceFailure("invalid_source_frame") from None
    if fields[0] != "0":
        raise _SourceFailure(
            "source_access_blocked"
            if fields[0] in {"10001006", "10001007", "10001011"}
            else "provider_source_failed"
        )
    return fields


def _safe_name(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 100
        and not any(category(character).startswith("C") for character in value)
        and not any(
            marker in value.casefold()
            for marker in (
                "http",
                "bearer",
                "token",
                "password",
                "credential",
                "/",
                "\\",
            )
        )
    )


@dataclass(frozen=True, slots=True)
class _Page:
    records: tuple[BaostockRosterRecord, ...]
    raw_count: int
    rejected: int


def _roster_page(response: BaostockResponse, *, page: int, day: str) -> _Page:
    fields = _body(response, "36")
    if (
        len(fields) < 9
        or fields[2] != "query_all_stock"
        or fields[3] != "anonymous"
        or fields[4] != str(page)
        or fields[5] != "2000"
        or fields[7] != day
        or tuple(fields[8].split(",")) != _FIELDS
    ):
        raise _SourceFailure("roster_context_mismatch")
    try:
        data = json.loads(fields[6])
        rows = data["record"]
        if type(data) is not dict or type(rows) is not list or len(rows) > 2000:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise _SourceFailure("invalid_roster_rows") from None
    records = []
    rejected = 0
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 3
            or type(row[0]) is not str
            or _PROVIDER_CODE.fullmatch(row[0]) is None
            or type(row[1]) is not str
            or row[1] not in {"0", "1"}
            or not _safe_name(row[2])
        ):
            rejected += 1
            continue
        records.append(
            BaostockRosterRecord(
                row[0], row[2], row[1], day, response.retrieved_at.isoformat()
            )
        )
    return _Page(tuple(records), len(rows), rejected)


class _RosterPages:
    def __init__(
        self,
        transport: BaostockTransport | None,
        day: str,
        max_pages: int,
        timeout_seconds: float,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.day = day
        self.max_pages = max_pages
        self.lock = Lock()
        self.pages: list[_Page] = []
        self.failure: str | None = None
        self.terminal = False

    def execute(self, invocation: ProviderInvocation) -> None:
        from .baostock_sdk_transport import BaostockSdkTransport

        transport = self.transport
        if transport is None:
            transport = BaostockSdkTransport(self.timeout_seconds)

        def exchange(kind: str, parameters: tuple[str, ...]) -> BaostockResponse:
            return invocation.call(
                ProductionInvocationAdapter(
                    partial(transport.exchange, kind, parameters)
                )
            )

        try:
            if isinstance(transport, BaostockSdkTransport):
                try:
                    transport.open()
                except Exception:
                    raise _SourceFailure("sdk_runtime_unavailable") from None
            login = _body(exchange("00", ("login", "anonymous")), "01")
            if login[2:4] != ("login", "anonymous"):
                raise _SourceFailure("login_context_mismatch")
            for number in range(1, self.max_pages + 1):
                response = exchange(
                    "35",
                    ("query_all_stock", "anonymous", str(number), "2000", self.day),
                )
                parsed = _roster_page(response, page=number, day=self.day)
                with self.lock:
                    self.pages.append(parsed)
                    self.terminal = parsed.raw_count < 2000
                if parsed.raw_count < 2000:
                    break
            logout = _body(exchange("02", ("logout", "anonymous")), "03")
            if logout[2:4] != ("logout", "anonymous"):
                raise _SourceFailure("logout_context_mismatch")
        except _SourceFailure as error:
            with self.lock:
                self.failure = error.code
            raise
        finally:
            transport.close()

    def snapshot(self) -> tuple[tuple[_Page, ...], bool, str | None]:
        with self.lock:
            return tuple(self.pages), self.terminal, self.failure


def acquire_baostock_dated_roster(
    request: UniverseRequest,
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
    max_pages: int,
    transport: BaostockTransport | None = None,
) -> BaostockRosterAcquisition:
    """Acquire the explicit requested day; preserve unknown class/time/scope.

    One short successful data page closes this provider enumeration, including a
    successful empty page after an exactly full last page. A failed next request,
    page ceiling or duplicate cannot establish closure. Login, every page and
    logout consume the same actual-invocation budget at #119's owning seam.
    """
    from .baostock_sdk_transport import BaostockSdkTransport

    prepared = prepare_universe_request(request, research_now=research_now)
    if isinstance(transport, BaostockSdkTransport):
        raise TypeError("The source must own its counted SDK worker transport.")
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("The roster page budget must be a positive integer.")
    pages = _RosterPages(
        transport,
        prepared.screen_date,
        max_pages,
        policy.call_timeout_seconds or policy.timeout_seconds,
    )
    lane_result = lanes.run(provider_group="baostock", adapter=pages, policy=policy)
    observed, terminal, failure = pages.snapshot()
    all_records = tuple(row for page in observed for row in page.records)
    records = tuple(
        sorted(
            {row.provider_code: row for row in reversed(all_records)}.values(),
            key=lambda row: row.provider_code,
        )
    )
    rejected = sum(page.rejected for page in observed)
    duplicate = len(all_records) != len(records)
    closed = terminal and not rejected and not duplicate
    limitations = [
        "ordinary_a_classification_unproved",
        "bse_scope_unproved",
        "historical_name_version_unproved",
        "availability_time_unknown",
        "historical_range_unproved",
    ]
    if failure:
        limitations.append(failure)
    if lane_result.failure_code:
        limitations.append(lane_result.failure_code)
    if rejected:
        limitations.append("invalid_roster_record")
    if duplicate:
        limitations.append("duplicate_roster_identity")
    if observed and not terminal:
        limitations.append("roster_pagination_not_closed")
    source_result: SourceResult = "partial" if observed else "unavailable"
    if failure == "source_access_blocked" and not observed:
        source_result = "credential_blocked"
    return BaostockRosterAcquisition(
        prepared.screen_date,
        prepared.information_cutoff,
        research_now.isoformat(),
        source_result,
        len(observed),
        closed,
        sum(page.raw_count for page in observed) if closed else None,
        records,
        rejected,
        tuple(limitations),
        lane_result,
    )
