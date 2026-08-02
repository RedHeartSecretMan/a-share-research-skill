"""Experimental source operations for news and authoritative disclosures."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any
from urllib.parse import urlencode

from .content_contract import (
    ContentHttpTransport,
    ContentObservation,
    ContentQuery,
    SourceBatch,
    SourceFailure,
)
from .identity_sources import HttpResponse, TransportError
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
OFFICIAL_ANNOUNCEMENT_DOCUMENT_NAMESPACE = "cninfo-szse-official-announcement"


class _OperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _failure(operation_id: str, error: _OperationError) -> SourceFailure:
    return SourceFailure(operation_id, error.code, str(error))


def _gate_degradation(
    operation_id: str,
    diagnostic: RequestGateDiagnostic,
) -> SourceFailure:
    return SourceFailure(
        source_operation=operation_id,
        code=diagnostic.code,
        message=diagnostic.message,
        details=diagnostic.details(),
    )


def _canonical_subject(query: ContentQuery) -> tuple[str, str, str]:
    subject = query.subject
    if not isinstance(subject, dict):
        raise _OperationError(
            "invalid_subject", "The source operation requires one canonical subject."
        )
    security = subject.get("security")
    if not isinstance(security, dict):
        raise _OperationError(
            "invalid_subject", "The subject security is not a canonical object."
        )
    exchange = security.get("exchange")
    code = security.get("code")
    security_type = security.get("type")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isdigit()
        or security_type != "A_SHARE"
    ):
        raise _OperationError(
            "invalid_subject", "The subject must be a canonical SSE or SZSE A-share."
        )
    return exchange, code, f"{exchange}:{code}"


def _request_get(
    operation_id: str,
    transport: ContentHttpTransport,
    url: str,
    headers: dict[str, str],
) -> HttpResponse:
    try:
        response = transport.get(url, headers)
    except TransportError as error:
        raise _OperationError(error.code, str(error)) from error
    if response.status != 200:
        raise _OperationError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _OperationError("empty_response", "The source returned an empty body.")
    return response


def _request_post(
    operation_id: str,
    transport: ContentHttpTransport,
    url: str,
    headers: dict[str, str],
    body: bytes,
) -> HttpResponse:
    try:
        response = transport.post(url, headers, body)
    except TransportError as error:
        raise _OperationError(error.code, str(error)) from error
    if response.status != 200:
        raise _OperationError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _OperationError("empty_response", "The source returned an empty body.")
    return response


def _json_response(response: HttpResponse) -> object:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _OperationError(
            "unexpected_content_type", "The source response is not JSON."
        )
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _OperationError(
            "unknown_schema", "The source JSON encoding or schema is invalid."
        ) from error


def _full_china_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise _OperationError(
            "publication_time_missing",
            "The material has no complete publication timestamp.",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=CHINA_STANDARD_TIME
        )
    except ValueError as error:
        raise _OperationError(
            "publication_time_missing",
            "The material has no complete publication timestamp.",
        ) from error
    return parsed.isoformat()


def _in_window(published_at: str | None, query: ContentQuery) -> bool:
    if published_at is None:
        return False
    published_date = datetime.fromisoformat(published_at).date().isoformat()
    return query.published_from <= published_date <= query.published_to


def _document_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _OperationError(
            "document_id_missing", "The disclosure has no stable document ID."
        )
    normalized = str(value).strip()
    if not normalized:
        raise _OperationError(
            "document_id_missing", "The disclosure has no stable document ID."
        )
    return normalized


def _normalized_name(value: str) -> str:
    return "".join(value.split())


class EastmoneyStockNewsOperation:
    """Collect stock-search news without upgrading its subject relationship."""

    operation_id = "eastmoney_stock_news@1"
    supported_material_types = frozenset({"stock_news"})
    endpoint = "https://search-api-web.eastmoney.com/search/jsonp"
    callback = "jQuery_news"

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        page_size: int = 20,
        max_pages: int = 25,
        request_gate: RequestGate | None = None,
    ) -> None:
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
            raise ValueError("page_size and max_pages must be from 1 to 100")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: ContentQuery) -> SourceBatch:
        try:
            _exchange, code, _security = _canonical_subject(query)
        except _OperationError as error:
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(_failure(self.operation_id, error),),
                complete=False,
            )
        observations: list[ContentObservation] = []
        degradations: list[SourceFailure] = []
        assert query.subject is not None
        total_hits: int | None = None
        for page in range(1, self._max_pages + 1):
            try:
                response, gate_diagnostics = self._request_gate.run(
                    partial(
                        _request_get,
                        self.operation_id,
                        self._transport,
                        self._url(code, page),
                        {
                            "Accept": "text/javascript,*/*;q=0.1",
                            "Referer": "https://so.eastmoney.com/",
                            "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
                        },
                    )
                )
                degradations.extend(
                    _gate_degradation(self.operation_id, item)
                    for item in gate_diagnostics
                )
                page_observations, total_hits = self._parse_page(
                    response, query.subject, code
                )
            except RequestGateError as gate_error:
                terminal_error = gate_error.cause
                if not isinstance(terminal_error, _OperationError):
                    raise
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    source_errors=(_failure(self.operation_id, terminal_error),),
                    degradations=tuple(degradations)
                    + tuple(
                        _gate_degradation(self.operation_id, item)
                        for item in gate_error.diagnostics
                    ),
                    complete=False,
                )
            except _OperationError as error:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    source_errors=(_failure(self.operation_id, error),),
                    degradations=tuple(degradations),
                    complete=False,
                )
            observations.extend(page_observations)
            within_window = sum(
                _in_window(item.published_at, query) for item in observations
            )
            if within_window >= query.limit:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    degradations=tuple(degradations),
                )
            if len(observations) >= total_hits:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    degradations=tuple(degradations),
                )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            source_errors=(
                SourceFailure(
                    self.operation_id,
                    "pagination_incomplete",
                    "The bounded source pagination did not cover the requested window.",
                ),
            ),
            degradations=tuple(degradations),
            complete=False,
        )

    def _url(self, code: str, page: int) -> str:
        inner = {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": page,
                    "pageSize": self._page_size,
                    "preTag": "",
                    "postTag": "",
                }
            },
        }
        return f"{self.endpoint}?{urlencode({'cb': self.callback, 'param': json.dumps(inner, separators=(',', ':'))})}"

    def _parse_page(
        self, response: HttpResponse, subject: dict[str, Any], code: str
    ) -> tuple[list[ContentObservation], int]:
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type not in {"text/javascript", "application/javascript"}:
            raise _OperationError(
                "unexpected_content_type", "The stock-news response is not JSONP."
            )
        try:
            text = response.body.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise _OperationError(
                "unknown_schema", "The stock-news JSONP encoding is invalid."
            ) from error
        prefix = f"{self.callback}("
        if not text.startswith(prefix):
            raise _OperationError(
                "unknown_schema", "The stock-news JSONP callback is invalid."
            )
        suffix = ");" if text.endswith(");") else ")" if text.endswith(")") else None
        if suffix is None:
            raise _OperationError(
                "unknown_schema", "The stock-news JSONP is not closed."
            )
        try:
            payload = json.loads(text[len(prefix) : -len(suffix)])
        except json.JSONDecodeError as error:
            raise _OperationError(
                "unknown_schema", "The stock-news JSONP payload is invalid."
            ) from error
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise _OperationError(
                "upstream_business_error", "The stock-news business status failed."
            )
        hits_total = payload.get("hitsTotal")
        result = payload.get("result")
        if (
            isinstance(hits_total, bool)
            or not isinstance(hits_total, int)
            or hits_total < 0
            or not isinstance(result, dict)
            or not isinstance(result.get("cmsArticleWebOld"), list)
        ):
            raise _OperationError(
                "unknown_schema", "The stock-news response schema is unknown."
            )
        rows = result["cmsArticleWebOld"]
        if not rows:
            raise _OperationError(
                "empty_response", "The stock-news result contains no materials."
            )
        observations: list[ContentObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                raise _OperationError(
                    "unknown_schema", "A stock-news row is not an object."
                )
            document_id = row.get("code")
            title = row.get("title")
            locator = row.get("url")
            if (
                not isinstance(document_id, str)
                or not document_id.strip()
                or not isinstance(title, str)
                or not title.strip()
                or not isinstance(locator, str)
                or not locator.strip()
            ):
                raise _OperationError(
                    "document_id_missing",
                    "A stock-news row lacks its document identity or locator.",
                )
            published_at = _full_china_timestamp(row.get("date"))
            author = row.get("mediaName")
            content = row.get("content")
            if author is not None and not isinstance(author, str):
                raise _OperationError(
                    "unknown_schema", "A stock-news author field is invalid."
                )
            if content is not None and not isinstance(content, str):
                raise _OperationError(
                    "unknown_schema", "A stock-news summary field is invalid."
                )
            observations.append(
                ContentObservation(
                    material_type="stock_news",
                    source_operation=self.operation_id,
                    source_role="attributed_opinion",
                    source_document_id=document_id.strip(),
                    title=title.strip(),
                    published_at=published_at,
                    retrieved_at=response.retrieved_at,
                    locator_uri=locator.strip(),
                    subject=subject,
                    author=author.strip() if author and author.strip() else None,
                    summary=content.strip() if content and content.strip() else None,
                    document_locator=None,
                    attributes={
                        "source_category": "market_observation",
                        "search_identity": code,
                        "subject_relationship": "unverified",
                    },
                    limitations=(
                        "subject_relationship_unverified",
                        "content_is_attributed_material_not_verified_fact",
                    ),
                )
            )
        return observations, hits_total


class CninfoAnnouncementOperation:
    """Collect CNINFO disclosures through an observed, non-guessed org route."""

    operation_id = "cninfo_announcement@1"
    supported_material_types = frozenset({"announcement"})
    mapping_endpoint = "https://www.cninfo.com.cn/new/data/szse_stock.json"
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        page_size: int = 30,
        max_pages: int = 25,
    ) -> None:
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
            raise ValueError("page_size and max_pages must be from 1 to 100")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages

    def collect(self, query: ContentQuery) -> SourceBatch:
        try:
            _exchange, code, _security = _canonical_subject(query)
            subject_name = self._subject_name(query)
            org_id = self._org_id(query, code, subject_name)
        except _OperationError as error:
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(_failure(self.operation_id, error),),
                complete=False,
            )
        observations: list[ContentObservation] = []
        assert query.subject is not None
        for page in range(1, self._max_pages + 1):
            try:
                response = _request_post(
                    self.operation_id,
                    self._transport,
                    self.endpoint,
                    {
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.cninfo.com.cn",
                        "Referer": "https://www.cninfo.com.cn/new/disclosure",
                        "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
                    },
                    self._body(code, org_id, page),
                )
                page_items, total, total_pages, has_more = self._parse_page(
                    response,
                    subject=query.subject,
                    code=code,
                    expected_name=subject_name,
                    org_id=org_id,
                )
            except _OperationError as error:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    source_errors=(_failure(self.operation_id, error),),
                    complete=False,
                )
            observations.extend(page_items)
            within_window = sum(
                _in_window(item.published_at, query) for item in observations
            )
            if within_window >= query.limit:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                )
            if len(observations) >= total or page >= total_pages or not has_more:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            source_errors=(
                SourceFailure(
                    self.operation_id,
                    "pagination_incomplete",
                    "The bounded CNINFO pagination did not cover the requested window.",
                ),
            ),
            complete=False,
        )

    def _subject_name(self, query: ContentQuery) -> str:
        assert query.subject is not None
        name = query.subject.get("name")
        if not isinstance(name, str) or not _normalized_name(name):
            raise _OperationError(
                "invalid_subject",
                "CNINFO routing requires the canonical subject name.",
            )
        return _normalized_name(name)

    def _org_id(self, query: ContentQuery, code: str, subject_name: str) -> str:
        assert query.subject is not None
        issuer = query.subject.get("issuer")
        if isinstance(issuer, dict):
            identifier = issuer.get("identifier")
            if (
                isinstance(identifier, dict)
                and identifier.get("scheme") == "CNINFO_ORG_ID"
                and isinstance(identifier.get("value"), str)
                and identifier["value"].strip()
            ):
                return identifier["value"].strip()
        parameter_org_id = query.parameters.get("cninfo_org_id")
        if isinstance(parameter_org_id, str) and parameter_org_id.strip():
            return parameter_org_id.strip()
        response = _request_get(
            self.operation_id,
            self._transport,
            self.mapping_endpoint,
            {
                "Accept": "application/json",
                "Referer": "https://www.cninfo.com.cn/",
                "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
            },
        )
        payload = _json_response(response)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("stockList"), list
        ):
            raise _OperationError(
                "unknown_schema", "The CNINFO security route schema is unknown."
            )
        matches = []
        for row in payload["stockList"]:
            if not isinstance(row, dict):
                raise _OperationError(
                    "unknown_schema", "A CNINFO security route row is invalid."
                )
            if row.get("code") != code:
                continue
            route_name = row.get("zwjc")
            route_org_id = row.get("orgId")
            category = row.get("category")
            if (
                not isinstance(route_name, str)
                or _normalized_name(route_name) != subject_name
                or not isinstance(route_org_id, str)
                or not route_org_id.strip()
                or category != "A股"
            ):
                raise _OperationError(
                    "wrong_security_payload",
                    "The CNINFO route does not match the canonical A-share subject.",
                )
            matches.append(route_org_id.strip())
        if len(matches) != 1:
            raise _OperationError(
                "wrong_security_payload",
                "CNINFO did not return exactly one matching security route.",
            )
        return matches[0]

    def _body(self, code: str, org_id: str, page: int) -> bytes:
        return urlencode(
            {
                "stock": f"{code},{org_id}",
                "tabName": "fulltext",
                "pageSize": str(self._page_size),
                "pageNum": str(page),
                "column": "",
                "category": "",
                "plate": "",
                "seDate": "",
                "searchkey": "",
                "secid": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
        ).encode()

    def _parse_page(
        self,
        response: HttpResponse,
        *,
        subject: dict[str, Any],
        code: str,
        expected_name: str,
        org_id: str,
    ) -> tuple[list[ContentObservation], int, int, bool]:
        payload = _json_response(response)
        if not isinstance(payload, dict):
            raise _OperationError(
                "unknown_schema", "The CNINFO disclosure schema is unknown."
            )
        rows = payload.get("announcements")
        total = payload.get("totalAnnouncement")
        total_pages = payload.get("totalpages")
        has_more = payload.get("hasMore")
        if (
            not isinstance(rows, list)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or isinstance(total_pages, bool)
            or not isinstance(total_pages, int)
            or total_pages < 0
            or not isinstance(has_more, bool)
        ):
            raise _OperationError(
                "unknown_schema", "The CNINFO disclosure schema is unknown."
            )
        if not rows:
            raise _OperationError(
                "empty_response", "CNINFO returned no disclosure materials."
            )
        observations: list[ContentObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                raise _OperationError(
                    "unknown_schema", "A CNINFO disclosure row is invalid."
                )
            row_code = row.get("secCode")
            row_name = row.get("secName")
            row_org_id = row.get("orgId")
            if (
                row_code != code
                or not isinstance(row_name, str)
                or _normalized_name(row_name) != expected_name
                or row_org_id != org_id
            ):
                raise _OperationError(
                    "wrong_security_payload",
                    "A CNINFO disclosure does not match the routed subject.",
                )
            document_id = _document_id(row.get("announcementId"))
            title = row.get("announcementTitle")
            if not isinstance(title, str) or not title.strip():
                raise _OperationError(
                    "unknown_schema", "A CNINFO disclosure title is missing."
                )
            timestamp = row.get("announcementTime")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise _OperationError(
                    "publication_time_missing",
                    "A CNINFO disclosure has no complete publication timestamp.",
                )
            try:
                published_at = datetime.fromtimestamp(
                    timestamp / 1000, tz=CHINA_STANDARD_TIME
                ).isoformat()
            except (OverflowError, OSError, ValueError) as error:
                raise _OperationError(
                    "publication_time_missing",
                    "A CNINFO disclosure publication timestamp is invalid.",
                ) from error
            adjunct_url = row.get("adjunctUrl")
            adjunct_type = row.get("adjunctType")
            if (
                not isinstance(adjunct_url, str)
                or not adjunct_url.startswith("finalpage/")
                or ".." in adjunct_url
                or "://" in adjunct_url
                or not adjunct_url.lower().endswith(".pdf")
                or adjunct_type != "PDF"
            ):
                raise _OperationError(
                    "unknown_schema", "The CNINFO official PDF locator is invalid."
                )
            document_locator = f"https://static.cninfo.com.cn/{adjunct_url}"
            observations.append(
                ContentObservation(
                    material_type="announcement",
                    source_operation=self.operation_id,
                    source_role="authoritative_disclosure",
                    source_document_id=document_id,
                    source_document_namespace=(
                        OFFICIAL_ANNOUNCEMENT_DOCUMENT_NAMESPACE
                    ),
                    title=title.strip(),
                    published_at=published_at,
                    retrieved_at=response.retrieved_at,
                    locator_uri=(
                        "https://www.cninfo.com.cn/new/disclosure/detail?"
                        f"annoId={document_id}"
                    ),
                    subject=subject,
                    author=row_name,
                    summary=None,
                    document_locator=document_locator,
                    attributes={
                        "announcement_type": row.get("announcementType"),
                        "announcement_type_name": row.get("announcementTypeName"),
                        "cninfo_org_id": org_id,
                        "adjunct_size": row.get("adjunctSize"),
                        "route_identity": "matched_current_source_observation",
                        "subject_relationship": "unverified",
                    },
                    limitations=("issuer_security_relationship_unverified",),
                )
            )
        return observations, total, total_pages, has_more


class SzseAnnouncementOperation:
    """Collect official SZSE disclosure index materials and PDF locators."""

    operation_id = "szse_announcement@1"
    supported_material_types = frozenset({"announcement"})
    endpoint = "https://www.szse.cn/api/disc/announcement/annList"

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        page_size: int = 30,
        max_pages: int = 25,
    ) -> None:
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
            raise ValueError("page_size and max_pages must be from 1 to 100")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages

    def collect(self, query: ContentQuery) -> SourceBatch:
        try:
            exchange, code, _security = _canonical_subject(query)
            if exchange != "SZSE":
                return SourceBatch(operation_id=self.operation_id)
            subject_name = self._subject_name(query)
        except _OperationError as error:
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(_failure(self.operation_id, error),),
                complete=False,
            )
        observations: list[ContentObservation] = []
        assert query.subject is not None
        for page in range(1, self._max_pages + 1):
            try:
                response = _request_post(
                    self.operation_id,
                    self._transport,
                    self.endpoint,
                    {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Referer": (
                            "https://www.szse.cn/disclosure/listed/notice/index.html"
                        ),
                        "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
                    },
                    json.dumps(
                        {
                            "channelCode": ["listedNotice_disc"],
                            "pageSize": self._page_size,
                            "pageNum": page,
                            "stock": [code],
                        },
                        separators=(",", ":"),
                    ).encode(),
                )
                page_items, total = self._parse_page(
                    response,
                    subject=query.subject,
                    code=code,
                    expected_name=subject_name,
                )
            except _OperationError as error:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                    source_errors=(_failure(self.operation_id, error),),
                    complete=False,
                )
            observations.extend(page_items)
            within_window = sum(
                _in_window(item.published_at, query) for item in observations
            )
            if within_window >= query.limit or len(observations) >= total:
                return SourceBatch(
                    operation_id=self.operation_id,
                    observations=tuple(observations),
                )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            source_errors=(
                SourceFailure(
                    self.operation_id,
                    "pagination_incomplete",
                    "The bounded SZSE pagination did not cover the requested window.",
                ),
            ),
            complete=False,
        )

    def _subject_name(self, query: ContentQuery) -> str:
        assert query.subject is not None
        name = query.subject.get("name")
        if not isinstance(name, str) or not _normalized_name(name):
            raise _OperationError(
                "invalid_subject",
                "SZSE disclosure routing requires the canonical subject name.",
            )
        return _normalized_name(name)

    def _parse_page(
        self,
        response: HttpResponse,
        *,
        subject: dict[str, Any],
        code: str,
        expected_name: str,
    ) -> tuple[list[ContentObservation], int]:
        payload = _json_response(response)
        if not isinstance(payload, dict):
            raise _OperationError(
                "unknown_schema", "The SZSE disclosure schema is unknown."
            )
        total = payload.get("announceCount")
        rows = payload.get("data")
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or not isinstance(rows, list)
        ):
            raise _OperationError(
                "unknown_schema", "The SZSE disclosure schema is unknown."
            )
        if not rows:
            raise _OperationError(
                "empty_response", "SZSE returned no disclosure materials."
            )
        observations: list[ContentObservation] = []
        for row in rows:
            if not isinstance(row, dict):
                raise _OperationError(
                    "unknown_schema", "An SZSE disclosure row is invalid."
                )
            security_codes = row.get("secCode")
            security_names = row.get("secName")
            if (
                not isinstance(security_codes, list)
                or any(not isinstance(item, str) for item in security_codes)
                or code not in security_codes
                or not isinstance(security_names, list)
                or any(not isinstance(item, str) for item in security_names)
                or expected_name
                not in {_normalized_name(item) for item in security_names}
            ):
                raise _OperationError(
                    "wrong_security_payload",
                    "An SZSE disclosure does not match the canonical subject.",
                )
            document_id = _document_id(row.get("annId"))
            index_id = row.get("id")
            title = row.get("title")
            if (
                not isinstance(index_id, str)
                or not index_id.strip()
                or not isinstance(title, str)
                or not title.strip()
            ):
                raise _OperationError(
                    "unknown_schema", "An SZSE disclosure identity or title is missing."
                )
            published_at = _full_china_timestamp(row.get("publishTime"))
            attach_path = row.get("attachPath")
            if (
                not isinstance(attach_path, str)
                or not attach_path.startswith("/disc/")
                or ".." in attach_path
                or "://" in attach_path
                or not attach_path.lower().endswith(".pdf")
                or row.get("attachFormat") != "PDF"
            ):
                raise _OperationError(
                    "unknown_schema", "The SZSE official PDF locator is invalid."
                )
            document_locator = f"https://disc.static.szse.cn/download{attach_path}"
            observations.append(
                ContentObservation(
                    material_type="announcement",
                    source_operation=self.operation_id,
                    source_role="authoritative_disclosure",
                    source_document_id=document_id,
                    source_document_namespace=(
                        OFFICIAL_ANNOUNCEMENT_DOCUMENT_NAMESPACE
                    ),
                    title=title.strip(),
                    published_at=published_at,
                    retrieved_at=response.retrieved_at,
                    locator_uri=document_locator,
                    subject=subject,
                    author=expected_name,
                    summary=None,
                    document_locator=document_locator,
                    attributes={
                        "szse_index_id": index_id.strip(),
                        "attach_size": row.get("attachSize"),
                        "route_identity": "matched_current_source_observation",
                        "subject_relationship": "unverified",
                    },
                    limitations=("issuer_security_relationship_unverified",),
                )
            )
        return observations, total
