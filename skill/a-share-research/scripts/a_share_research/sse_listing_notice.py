"""Finite official STAR listing notice facts, not an SSE market roster."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from functools import partial
from html.parser import HTMLParser
from threading import Lock
from typing import Protocol, cast
from urllib.parse import urlsplit

from .identity_sources import (
    HttpResponse,
    HttpTransport,
    TransportError,
    UrlLibTransport,
)
from ._universe_http import is_permission_denied
from .provider_lane import (
    ProductionInvocationAdapter,
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLaneResult,
    ProviderLanes,
)
from .universe_input import (
    ASIA_SHANGHAI,
    ExchangeListOutcome,
    ExchangeListRecord,
    SourceResult,
    UniverseRequest,
    prepare_universe_request,
)

_PATH = re.compile(
    r"/disclosure/announcement/listing/c/(c_[0-9]{8}_[0-9]{1,12})\.shtml"
)
_HEADER = re.compile(
    r"关于(?P<issuer>[\u3400-\u9fffA-Za-z0-9（）()·]{1,100})"
    r"人民币普通股股票科创板上市交易的公告(?P<day>[0-9]{4}-[0-9]{2}-[0-9]{2})"
)


class _Failure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NoticeText(HTMLParser):
    """Decode static HTML text without site selectors or script execution."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"head", "script", "style", "noscript"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"head", "script", "style", "noscript"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


@dataclass(frozen=True, slots=True)
class SseListingNoticeRecord:
    code: str
    name: str
    formal_listing_trading_day: str
    article_header_day: str
    available_at: str
    retrieved_at: str
    provenance_uri: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": "SSE",
            "code": self.code,
            "name": self.name,
            "security_category": "A_SHARE",
            "source_class": "人民币普通股/A股股票",
            "formal_listing_trading_day": self.formal_listing_trading_day,
            "article_header_day": self.article_header_day,
            "source_date_role": "article_header_day_no_literal_publication_label",
            "available_at": self.available_at,
            "availability_basis": "consumer_conservative_next_day_Asia_Shanghai",
            "retrieved_at": self.retrieved_at,
            "provenance_uri": self.provenance_uri,
            "name_scope": "named_event_version_only",
        }


def _parse(response: HttpResponse, reference: str) -> SseListingNoticeRecord:
    if (
        response.status != 200
        or response.content_type.split(";", 1)[0].strip().lower() != "text/html"
        or len(response.body) > 1_048_576
        or response.retrieved_at.tzinfo is None
        or response.retrieved_at.utcoffset() is None
    ):
        raise _Failure("listing_notice_unavailable")
    try:
        parser = _NoticeText()
        parser.feed(response.body.decode("utf-8-sig"))
        parser.close()
    except (UnicodeError, ValueError):
        raise _Failure("listing_notice_encoding_unknown") from None
    text = re.sub(r"\s+", "", "".join(parser.parts))
    headers = list(_HEADER.finditer(text))
    if len(headers) != 1:
        raise _Failure("listing_notice_header_unproved")
    header = headers[0]
    try:
        header_day = date.fromisoformat(header["day"])
        available = datetime.combine(
            header_day + timedelta(days=1), time.min, tzinfo=ASIA_SHANGHAI
        )
    except (ValueError, OverflowError):
        raise _Failure("listing_notice_header_unproved") from None
    # The omitted intervening financial clause is matched but never retained.
    body = re.compile(
        re.escape(header["issuer"]) + r"A股股票将在本所科创板上市交易。[^。]{0,256}?于"
        r"(?P<year>[0-9]{4})年(?P<month>[0-9]{1,2})月(?P<day>[0-9]{1,2})日起上市交易。"
        r"证券简称为[\"“](?P<name>[\u3400-\u9fffA-Za-z0-9（）()·]{1,100})[\"”]，"
        r"证券代码为[\"“](?P<code>[0-9]{6})[\"”]。"
    )
    matches = list(body.finditer(text[header.end() :]))
    if len(matches) != 1 or available > response.retrieved_at:
        raise _Failure("listing_notice_event_unproved")
    match = matches[0]
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        raise _Failure("listing_notice_event_unproved") from None
    return SseListingNoticeRecord(
        match["code"],
        match["name"],
        day.isoformat(),
        header_day.isoformat(),
        available.isoformat(),
        response.retrieved_at.isoformat(),
        reference,
    )


@dataclass(frozen=True, slots=True)
class SseListingNoticeAcquisition:
    membership_effective_date: str
    information_cutoff: str
    research_now: str
    source_result: SourceResult
    records: tuple[SseListingNoticeRecord, ...]
    limitation_codes: tuple[str, ...]
    lane_result: ProviderLaneResult[SseListingNoticeRecord]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "operation_id": "sse_formal_listing_notice@1",
            "exchange": "SSE",
            "source_scope": "one_formal_STAR_listing_notice_not_whole_exchange",
            "membership_effective_date": self.membership_effective_date,
            "information_cutoff": self.information_cutoff,
            "research_now": self.research_now,
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


@dataclass(frozen=True, slots=True)
class SseListingNoticeTarget:
    document_url: str


class _RequestView(Protocol):
    screen_date: str
    information_cutoff: str


class _Notice:
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
        self.failure: str | None = None

    @staticmethod
    def _get(
        transport: HttpTransport, url: str, headers: dict[str, str]
    ) -> HttpResponse:
        response = transport.get(url, headers)
        if response.status == 429:
            raise TransportError("rate_limited", "The listing notice was rate limited.")
        return response

    def execute(self, invocation: ProviderInvocation) -> SseListingNoticeRecord:
        headers = {
            "Accept": "text/html",
            "Referer": "https://www.sse.com.cn/",
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
                raise _Failure("source_access_blocked")
            return _parse(response, self.reference)
        except _Failure as error:
            with self.lock:
                self.failure = error.code
            raise
        except TransportError as error:
            with self.lock:
                self.failure = (
                    "source_access_blocked"
                    if is_permission_denied(error)
                    else "listing_notice_unavailable"
                )
            raise

    def failure_code(self) -> str | None:
        with self.lock:
            return self.failure


class SseListingNoticeAdapter:
    exchange = "SSE"
    operation_id = "sse_formal_listing_notice@1"

    def __init__(
        self,
        *,
        target: SseListingNoticeTarget,
        research_now: datetime,
        transport: HttpTransport | None,
        lanes: ProviderLanes,
        policy: ProviderLanePolicy,
    ) -> None:
        if (
            type(target) is not SseListingNoticeTarget
            or type(target.document_url) is not str
        ):
            raise ValueError("The listing notice needs an exact source target.")
        parsed = urlsplit(target.document_url)
        match = _PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.sse.com.cn"
            or parsed.query
            or parsed.fragment
            or match is None
        ):
            raise ValueError("The listing notice needs a qualified official locator.")
        if isinstance(transport, UrlLibTransport):
            raise TypeError("The listing notice must own its counted urllib transport.")
        self._target, self._reference, self._now = (
            target,
            "sse-listing:" + match[1],
            research_now,
        )
        self._transport, self._lanes, self._policy = transport, lanes, policy
        self._last_acquisition: SseListingNoticeAcquisition | None = None

    @property
    def last_acquisition(self) -> SseListingNoticeAcquisition | None:
        return self._last_acquisition

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        view = cast(_RequestView, request)
        prepared = prepare_universe_request(
            UniverseRequest(view.screen_date, view.information_cutoff),
            research_now=self._now,
        )
        policy = replace(
            self._policy,
            timeout_seconds=min(self._policy.timeout_seconds, timeout_seconds),
        )
        notice = _Notice(
            self._target.document_url,
            self._reference,
            self._transport,
            policy.call_timeout_seconds or policy.timeout_seconds,
        )
        lane = self._lanes.run(provider_group="sse", adapter=notice, policy=policy)
        record = lane.value
        failure = notice.failure_code()
        state: SourceResult = (
            "partial"
            if record
            else "credential_blocked"
            if failure == "source_access_blocked"
            else "unavailable"
        )
        limitations = [
            "single_event_not_full_exchange",
            "later_membership_events_unproved",
            "article_header_day_conservative_consumer_bound",
        ]
        if failure:
            limitations.append(failure)
        if lane.failure_code:
            limitations.append(lane.failure_code)
        records: tuple[ExchangeListRecord, ...] = ()
        if record:
            if record.formal_listing_trading_day != prepared.screen_date:
                limitations.append("outside_proven_listing_event_day")
            elif datetime.fromisoformat(record.available_at) > datetime.fromisoformat(
                prepared.information_cutoff
            ):
                limitations.append("listing_version_after_cutoff")
            else:
                records = (
                    ExchangeListRecord(
                        "SSE",
                        record.code,
                        record.name,
                        "A_SHARE",
                        record.formal_listing_trading_day,
                        record.available_at,
                        record.retrieved_at,
                        record.provenance_uri,
                    ),
                )
        self._last_acquisition = SseListingNoticeAcquisition(
            prepared.screen_date,
            prepared.information_cutoff,
            self._now.isoformat(),
            state,
            (record,) if record else (),
            tuple(limitations),
            lane,
        )
        return ExchangeListOutcome(
            self.operation_id,
            "SSE",
            prepared.screen_date,
            bool(lane.attempts),
            state,
            None,
            None,
            1 if record else 0,
            records,
        )
