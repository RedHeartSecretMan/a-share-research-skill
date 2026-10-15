"""Finite formal-listing facts from the AJ-observed SZSE PDF book shape.

A formally approved event is not a complete exchange roster. Until matching
publication metadata is proved, its fields cannot enter a canonical membership
record. A date in a storage URL is never a publication date.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import cast
from unicodedata import category
from urllib.error import HTTPError
from urllib.parse import urlsplit
from uuid import UUID

from .identity_sources import (
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
from .universe_input import SourceResult, UniverseRequest, prepare_universe_request

MAX_LISTING_PDF_BYTES = 4 * 1024 * 1024
MAX_LISTING_PDF_PAGES = 40
MAX_LISTING_TEXT_BYTES = 262_144
_LOCATOR_PATH = re.compile(
    r"/(?:download/)?disc/disk[0-9]{2}/finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/([0-9a-fA-F-]{36})\.pdf",
    re.IGNORECASE,
)
_APPROVAL = re.compile(
    r"根据深圳证券交易所《[^》]{1,200}人民币普通股股票在创业板上市的通知》"
    r"（(?P<approval>深证上\[[0-9]{4}\][0-9]{1,8}号)），同意公司发行的人民币普通股股票"
    r"在深圳证券交易所创业板上市交易，证券简称“(?P<name>[^”]{1,100})”，"
    r"证券代码“(?P<code>[0-9]{6})”，[^。]{0,500}?将于"
    r"(?P<year>[0-9]{4})年(?P<month>[0-9]{1,2})月(?P<day>[0-9]{1,2})日起上市交易。"
)


class _MaterialFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _formal_fields(pages: list[str]) -> dict[str, str]:
    """Parse the observed formal-approval clause, never a merely proposed date."""
    if (
        not pages
        or len(pages) > MAX_LISTING_PDF_PAGES
        or sum(len(p.encode("utf-8")) for p in pages) > MAX_LISTING_TEXT_BYTES
    ):
        raise _MaterialFailure("listing_text_bound_exceeded")
    compact = re.sub(r"\s+", "", "\n".join(pages))
    if "上市公告书" not in compact:
        raise _MaterialFailure("listing_material_title_unproved")
    matches = list(_APPROVAL.finditer(compact))
    values: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for match in matches:
        name = match["name"]
        if any(category(c).startswith("C") or c in "/\\<>" for c in name):
            continue
        try:
            listing_day = date(
                int(match["year"]), int(match["month"]), int(match["day"])
            ).isoformat()
        except ValueError:
            continue
        fields = {
            "exchange": "SZSE",
            "code": match["code"],
            "name": name,
            "security_category": "A_SHARE",
            "formal_listing_trading_day": listing_day,
            "approval_reference": match["approval"],
        }
        values[(fields["code"], name, listing_day, fields["approval_reference"])] = (
            fields
        )
    if len(values) != 1:
        raise _MaterialFailure("listing_approval_unproved")
    fields = next(iter(values.values()))
    cover = re.sub(r"\s+", "", pages[0])
    if f"股票简称：{fields['name']}股票代码：{fields['code']}" not in cover:
        raise _MaterialFailure("listing_cover_identity_conflict")
    return fields


@dataclass(frozen=True, slots=True)
class SzseListingEventRecord:
    code: str
    name: str
    formal_listing_trading_day: str
    approval_reference: str
    retrieved_at: str
    provenance_uri: str
    exchange: str = "SZSE"
    security_category: str = "A_SHARE"
    publication_day: None = None
    available_at: None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "code": self.code,
            "name": self.name,
            "security_category": self.security_category,
            "source_class": "人民币普通股",
            "formal_listing_trading_day": self.formal_listing_trading_day,
            "approval_reference": self.approval_reference,
            "publication_day": self.publication_day,
            "available_at": self.available_at,
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
            "name_scope": "named_event_version_only",
            "actual_trades_observed": False,
        }


@dataclass(frozen=True, slots=True)
class SzseListingMaterialAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    records: tuple[SzseListingEventRecord, ...]
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[None]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "szse_formal_listing_material@1",
            "exchange": "SZSE",
            "source_scope": "one_formal_listing_book_not_whole_exchange",
            "membership_effective_date": self.membership_effective_date,
            "information_cutoff": self.information_cutoff,
            "research_now": self.research_now,
            "membership_proven": False,
            "market_expected_count": None,
            "attempted": bool(self.lane_result.attempts),
            "source_result": self.source_result,
            "records": [r.to_public_dict() for r in self.records],
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


def _locator(url: str) -> str:
    if type(url) is not str or len(url) > 1000:
        raise ValueError("The listing material needs a qualified official locator.")
    parsed = urlsplit(url)
    match = _LOCATOR_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "disc.static.szse.cn"
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise ValueError("The listing material needs a qualified official locator.")
    try:
        identity = UUID(match[1])
    except ValueError:
        raise ValueError(
            "The listing material locator has an invalid identity."
        ) from None
    return f"szse-listing:{identity}"


def _decode_pdf(payload: bytes, timeout_seconds: float) -> dict[str, str]:
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("szse_listing_pdf_worker.py")),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        raise _MaterialFailure("listing_pdf_unavailable") from None
    try:
        output, _ = process.communicate(payload, timeout=max(0.001, timeout_seconds))
    except subprocess.TimeoutExpired:
        raise _MaterialFailure("listing_pdf_timeout") from None
    except OSError:
        raise _MaterialFailure("listing_pdf_unavailable") from None
    finally:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        # A termination request is not proof of exit; retain the owning lane
        # until native exit, including unexpected pipe failures.
        process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
    if process.returncode != 0 or len(output) > 4096:
        raise _MaterialFailure("listing_pdf_unavailable")
    try:
        value: object = json.loads(output)
    except (ValueError, UnicodeError):
        raise _MaterialFailure("listing_pdf_unavailable") from None
    if type(value) is not dict:
        raise _MaterialFailure("listing_pdf_unavailable")
    data = cast(dict[str, object], value)
    failure = data.get("failure")
    if failure in {
        "listing_approval_unproved",
        "listing_material_title_unproved",
        "listing_cover_identity_conflict",
        "listing_text_bound_exceeded",
    }:
        raise _MaterialFailure(cast(str, failure))
    expected = {
        "exchange",
        "code",
        "name",
        "security_category",
        "formal_listing_trading_day",
        "approval_reference",
    }
    if (
        set(data) != expected
        or any(type(v) is not str for v in data.values())
        or data.get("exchange") != "SZSE"
        or data.get("security_category") != "A_SHARE"
    ):
        raise _MaterialFailure("listing_pdf_unavailable")
    return cast(dict[str, str], data)


class _ListingMaterial:
    def __init__(
        self, url: str, reference: str, transport: HttpTransport | None, timeout: float
    ) -> None:
        self.url, self.reference, self.transport, self.timeout = (
            url,
            reference,
            transport,
            timeout,
        )
        self.lock = Lock()
        self.record: SzseListingEventRecord | None = None
        self.failure: str | None = None

    @staticmethod
    def _get(
        transport: HttpTransport, url: str, headers: dict[str, str]
    ) -> HttpResponse:
        response = transport.get(url, headers)
        if response.status == 429:
            raise TransportError(
                "rate_limited", "The listing source request was rate limited."
            )
        return response

    def execute(self, invocation: ProviderInvocation) -> None:
        started = monotonic()
        headers = {
            "Accept": "application/pdf",
            "Referer": "https://www.szse.cn/",
            "User-Agent": "a-share-research-skill/1",
        }
        try:
            response = (
                UrlLibTransport(provider_invocation=invocation).get_with_timeout(
                    self.url, headers, self.timeout
                )
                if self.transport is None
                else invocation.call(
                    ProductionInvocationAdapter(
                        partial(self._get, self.transport, self.url, headers)
                    )
                )
            )
            if response.status in {401, 403}:
                raise _MaterialFailure("source_access_blocked")
            if (
                response.status != 200
                or response.content_type.split(";", 1)[0].strip().lower()
                != "application/pdf"
            ):
                raise _MaterialFailure("listing_pdf_unavailable")
            if len(response.body) > MAX_LISTING_PDF_BYTES:
                raise _MaterialFailure("listing_pdf_too_large")
            if not response.body.startswith(b"%PDF-"):
                raise _MaterialFailure("listing_pdf_signature_invalid")
            if (
                response.retrieved_at.tzinfo is None
                or response.retrieved_at.utcoffset() is None
            ):
                raise _MaterialFailure("invalid_retrieval_time")
            fields = _decode_pdf(response.body, self.timeout - (monotonic() - started))
            record = SzseListingEventRecord(
                fields["code"],
                fields["name"],
                fields["formal_listing_trading_day"],
                fields["approval_reference"],
                response.retrieved_at.isoformat(),
                self.reference,
            )
        except _MaterialFailure as error:
            with self.lock:
                self.failure = error.code
            raise
        except TransportError as error:
            code = (
                "source_access_blocked"
                if isinstance(error.__cause__, HTTPError)
                and error.__cause__.code in {401, 403}
                else "listing_material_unavailable"
            )
            with self.lock:
                self.failure = code
            raise
        with self.lock:
            self.record = record

    def snapshot(self) -> tuple[SzseListingEventRecord | None, str | None]:
        with self.lock:
            return self.record, self.failure


def acquire_szse_listing_material(
    request: UniverseRequest,
    *,
    research_now: datetime,
    document_url: str,
    transport: HttpTransport | None,
    lanes: ProviderLanes,
    policy: ProviderLanePolicy,
) -> SzseListingMaterialAcquisition:
    prepared = prepare_universe_request(request, research_now=research_now)
    reference = _locator(document_url)
    if isinstance(transport, UrlLibTransport):
        raise TypeError("The listing source must own its counted urllib transport.")
    document = _ListingMaterial(
        document_url,
        reference,
        transport,
        policy.call_timeout_seconds or policy.timeout_seconds,
    )
    result = lanes.run(provider_group="szse", adapter=document, policy=policy)
    record, failure = document.snapshot()
    limitations = [
        "publication_time_unproved",
        "single_event_not_full_exchange",
        "later_membership_events_unproved",
    ]
    if failure:
        limitations.append(failure)
    if result.failure_code:
        limitations.append(result.failure_code)
    state: SourceResult = (
        "partial"
        if record is not None
        else "credential_blocked"
        if failure == "source_access_blocked"
        else "unavailable"
    )
    return SzseListingMaterialAcquisition(
        prepared.screen_date,
        prepared.information_cutoff,
        research_now.isoformat(),
        state,
        (record,) if record else (),
        tuple(limitations),
        result,
    )
