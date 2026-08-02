"""Official SSE announcement source operation for research content."""

from __future__ import annotations

import json
import posixpath
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from .content_contract import (
    ContentObservation,
    ContentQuery,
    SourceBatch,
    SourceFailure,
)
from .identity_sources import HttpResponse, HttpTransport, TransportError

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
_DOCUMENT_ID = re.compile(r"^[0-9A-Za-z_-]+$")


class _OperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _failure(operation_id: str, error: _OperationError) -> SourceFailure:
    return SourceFailure(operation_id, error.code, str(error))


def _canonical_subject(query: ContentQuery) -> tuple[str, str, str]:
    subject = query.subject
    if not isinstance(subject, dict):
        raise _OperationError(
            "invalid_subject", "The SSE source requires one canonical subject."
        )
    security = subject.get("security")
    if not isinstance(security, dict):
        raise _OperationError(
            "invalid_subject", "The subject security is not a canonical object."
        )
    exchange = security.get("exchange")
    code = security.get("code")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or len(code) != 6
        or not code.isdigit()
        or security.get("type") != "A_SHARE"
    ):
        raise _OperationError(
            "invalid_subject", "The subject must be a canonical SSE or SZSE A-share."
        )
    name = subject.get("name")
    if not isinstance(name, str) or not "".join(name.split()):
        raise _OperationError(
            "invalid_subject", "The canonical subject name is required."
        )
    return exchange, code, "".join(name.split())


def _get(
    operation_id: str,
    transport: HttpTransport,
    url: str,
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json",
                "Referer": "https://www.sse.com.cn/",
                "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
            },
        )
    except TransportError as error:
        raise _OperationError(error.code, str(error)) from error
    if response.status == 429:
        raise _OperationError(
            "rate_limited", "The SSE announcement source rate limited the request."
        )
    if response.status != 200:
        raise _OperationError(
            "upstream_http_error",
            f"The SSE announcement source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _OperationError(
            "empty_response", "The SSE announcement source returned an empty body."
        )
    return response


def _json(response: HttpResponse) -> object:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _OperationError(
            "unexpected_content_type", "The SSE announcement response is not JSON."
        )
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _OperationError(
            "unknown_schema", "The SSE announcement JSON is invalid."
        ) from error


def _full_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise _OperationError(
            "publication_time_missing",
            "The SSE announcement has no complete publication timestamp.",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=CHINA_STANDARD_TIME
        )
    except ValueError as error:
        raise _OperationError(
            "publication_time_missing",
            "The SSE announcement has no complete publication timestamp.",
        ) from error
    return parsed.isoformat()


def _date(value: object) -> str:
    if not isinstance(value, str):
        raise _OperationError("unknown_schema", "The SSE disclosure date is missing.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise _OperationError(
            "unknown_schema", "The SSE disclosure date is invalid."
        ) from error
    if parsed.isoformat() != value:
        raise _OperationError("unknown_schema", "The SSE disclosure date is invalid.")
    return value


def _document_locator(value: object) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or not value.startswith("/disclosure/listedinfo/announcement/")
        or ".." in value
        or "://" in value
        or not value.lower().endswith(".pdf")
    ):
        raise _OperationError(
            "document_id_missing", "The SSE official PDF locator is invalid."
        )
    filename = posixpath.basename(value)
    document_id = filename[:-4]
    if not document_id or _DOCUMENT_ID.fullmatch(document_id) is None:
        raise _OperationError(
            "document_id_missing", "The SSE official PDF path has no stable ID."
        )
    return document_id, f"https://www.sse.com.cn{value}"


def _in_window(published_at: str, query: ContentQuery) -> bool:
    published_date = datetime.fromisoformat(published_at).date().isoformat()
    return query.published_from <= published_date <= query.published_to


class SseAnnouncementOperation:
    """Collect official SSE announcements for canonical SSE A-share subjects."""

    operation_id = "sse_announcement@1"
    supported_material_types = frozenset({"announcement"})
    endpoint = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        page_size: int = 25,
        max_pages: int = 25,
    ) -> None:
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
            raise ValueError("page_size and max_pages must be from 1 to 100")
        self._transport = transport
        self._page_size = page_size
        self._max_pages = max_pages

    def collect(self, query: ContentQuery) -> SourceBatch:
        try:
            exchange, code, expected_name = _canonical_subject(query)
            if exchange != "SSE":
                return SourceBatch(operation_id=self.operation_id)
        except _OperationError as error:
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(_failure(self.operation_id, error),),
                complete=False,
            )
        assert query.subject is not None
        observations: list[ContentObservation] = []
        for page in range(1, self._max_pages + 1):
            try:
                response = _get(
                    self.operation_id,
                    self._transport,
                    self._url(query, code, page),
                )
                page_items, total, page_count = self._parse_page(
                    response,
                    query=query,
                    page=page,
                    code=code,
                    expected_name=expected_name,
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
                _in_window(item.published_at, query)
                for item in observations
                if item.published_at is not None
            )
            if (
                within_window >= query.limit
                or len(observations) >= total
                or page >= page_count
            ):
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
                    "The bounded SSE pagination did not cover the requested window.",
                ),
            ),
            complete=False,
        )

    def _url(self, query: ContentQuery, code: str, page: int) -> str:
        return f"{self.endpoint}?{
            urlencode(
                {
                    'isPagination': 'true',
                    'productId': code,
                    'keyWord': '',
                    'securityType': '0101',
                    'reportType2': 'DQGG',
                    'reportType': 'ALL',
                    'beginDate': query.published_from,
                    'endDate': query.published_to,
                    'pageHelp.pageSize': str(self._page_size),
                    'pageHelp.pageNo': str(page),
                    'pageHelp.beginPage': str(page),
                    'pageHelp.endPage': str(page),
                    'pageHelp.cacheSize': '1',
                }
            )
        }"

    def _parse_page(
        self,
        response: HttpResponse,
        *,
        query: ContentQuery,
        page: int,
        code: str,
        expected_name: str,
    ) -> tuple[list[ContentObservation], int, int]:
        payload = _json(response)
        if not isinstance(payload, dict):
            raise _OperationError(
                "unknown_schema", "The SSE announcement schema is unknown."
            )
        rows = payload.get("result")
        page_help = payload.get("pageHelp")
        if (
            payload.get("productId") != code
            or payload.get("beginDate") != query.published_from
            or payload.get("endDate") != query.published_to
            or payload.get("isPagination") != "true"
            or not isinstance(rows, list)
            or not isinstance(page_help, dict)
            or not isinstance(page_help.get("data"), list)
            or page_help["data"] != rows
        ):
            raise _OperationError(
                "unknown_schema", "The SSE announcement schema is unknown."
            )
        total = page_help.get("total")
        page_count = page_help.get("pageCount")
        page_no = page_help.get("pageNo")
        page_size = page_help.get("pageSize")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (
                total,
                page_count,
                page_no,
                page_size,
            )
        ):
            raise _OperationError(
                "unknown_schema", "The SSE pagination schema is unknown."
            )
        assert isinstance(total, int)
        assert isinstance(page_count, int)
        assert isinstance(page_no, int)
        assert isinstance(page_size, int)
        if (
            total < 0
            or page_count < 0
            or page_no != page
            or page_size != self._page_size
            or total < len(rows)
        ):
            raise _OperationError(
                "unknown_schema", "The SSE pagination values are inconsistent."
            )
        if not rows:
            raise _OperationError(
                "empty_response", "SSE returned no announcement materials."
            )
        observations: list[ContentObservation] = []
        assert query.subject is not None
        for row in rows:
            if not isinstance(row, dict):
                raise _OperationError(
                    "unknown_schema", "An SSE announcement row is invalid."
                )
            row_name = row.get("SECURITY_NAME")
            if (
                row.get("SECURITY_CODE") != code
                or not isinstance(row_name, str)
                or "".join(row_name.split()) != expected_name
            ):
                raise _OperationError(
                    "wrong_security_payload",
                    "An SSE announcement does not match the canonical subject.",
                )
            title = row.get("TITLE")
            if not isinstance(title, str) or not title.strip():
                raise _OperationError(
                    "unknown_schema", "An SSE announcement title is missing."
                )
            published_at = _full_timestamp(row.get("ADDDATE"))
            disclosure_date = _date(row.get("SSEDATE"))
            document_id, document_locator = _document_locator(row.get("URL"))
            observations.append(
                ContentObservation(
                    material_type="announcement",
                    source_operation=self.operation_id,
                    source_role="authoritative_disclosure",
                    source_document_id=document_id,
                    title=title.strip(),
                    published_at=published_at,
                    retrieved_at=response.retrieved_at,
                    locator_uri=document_locator,
                    subject=query.subject,
                    author=row_name,
                    summary=None,
                    document_locator=document_locator,
                    attributes={
                        "sse_disclosure_date": disclosure_date,
                        "bulletin_heading": row.get("BULLETIN_HEADING"),
                        "bulletin_type": row.get("BULLETIN_TYPE"),
                        "publication_time_basis": "sse_index_add_time",
                        "document_id_basis": "official_pdf_path",
                        "subject_relationship": "unverified",
                    },
                    limitations=(
                        "issuer_security_relationship_unverified",
                        "publication_time_uses_sse_index_add_time",
                    ),
                )
            )
        return observations, total, page_count
