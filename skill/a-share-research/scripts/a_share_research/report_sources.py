"""Experimental report discovery operations for the research-content module."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import partial
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlparse

from .content_contract import (
    ContentHttpTransport,
    ContentObservation,
    ContentQuery,
    SourceBatch,
    SourceFailure,
)
from .identity_sources import SourceOperationError, TransportError
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
)
from .valuation_sources import ThsConsensusEpsOperation

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
EASTMONEY_REPORT_URL = "https://reportapi.eastmoney.com/report/list"
EASTMONEY_PDF_TEMPLATE = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_INFO_CODE = re.compile(r"AP[0-9]+\Z")
_INDUSTRY_CODE = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True)
class _ReportPage:
    hits: int
    total_pages: int
    page_number: int
    rows: tuple[dict[str, Any], ...]
    current_year: int | None


class _ReportSourceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EastmoneyReportOperation:
    """Discover stock and provider-industry reports without fetching PDFs."""

    operation_id = "eastmoney_reports@1"
    supported_material_types = frozenset({"research_report", "industry_report"})

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: ContentQuery) -> SourceBatch:
        requested = tuple(
            material_type
            for material_type in query.material_types
            if material_type in self.supported_material_types
        )
        if not requested:
            return SourceBatch(operation_id=self.operation_id)
        observations: list[ContentObservation] = []
        degradations: list[SourceFailure] = []
        complete = True
        try:
            for material_type in requested:
                collected, item_degradations, item_complete = self._collect_type(
                    query,
                    material_type,
                )
                observations.extend(collected)
                degradations.extend(item_degradations)
                complete = complete and item_complete
        except _ReportSourceError as error:
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(
                    SourceFailure(
                        source_operation=self.operation_id,
                        code=error.code,
                        message=str(error),
                    ),
                ),
                complete=False,
            )
        except RequestGateError as gate_error:
            terminal_error = gate_error.cause
            if not isinstance(terminal_error, TransportError):
                raise
            return SourceBatch(
                operation_id=self.operation_id,
                observations=tuple(observations),
                source_errors=(
                    SourceFailure(
                        source_operation=self.operation_id,
                        code=terminal_error.code,
                        message=str(terminal_error),
                    ),
                ),
                degradations=tuple(degradations)
                + tuple(
                    _gate_degradation(self.operation_id, item)
                    for item in gate_error.diagnostics
                ),
                complete=False,
            )
        except TransportError as error:
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(
                    SourceFailure(
                        source_operation=self.operation_id,
                        code=error.code,
                        message=str(error),
                    ),
                ),
                complete=False,
            )
        limitations: list[str] = []
        if not complete:
            limitations.append("pagination_incomplete")
        if "research_report" in requested and query.subject is None:
            limitations.extend(
                (
                    "title_keyword_filter_not_semantic_search",
                    "theme_report_universe_incomplete",
                )
            )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            degradations=tuple(degradations),
            limitations=tuple(limitations),
            complete=complete,
        )

    def _collect_type(
        self,
        query: ContentQuery,
        material_type: str,
    ) -> tuple[list[ContentObservation], list[SourceFailure], bool]:
        code: str | None = None
        expected_name: str | None = None
        industry_code: str | None = None
        theme_title_filter = (
            material_type == "research_report" and query.subject is None
        )
        if material_type == "research_report":
            if theme_title_filter:
                if not query.keywords:
                    raise _ReportSourceError(
                        "invalid_query",
                        "Theme report discovery requires at least one title keyword.",
                    )
            else:
                code, expected_name = _canonical_stock_subject(query.subject)
            q_type = "0"
        else:
            industry_code = _required_industry_code(query.parameters)
            q_type = "1"

        observations: list[ContentObservation] = []
        degradations: list[SourceFailure] = []
        previous_date: date | None = None
        page_number = 1
        while True:
            url = _report_url(
                q_type=q_type,
                code=code,
                industry_code=industry_code,
                query=query,
                page_number=page_number,
                page_size=100 if theme_title_filter else min(query.limit, 100),
            )
            request = partial(
                self._transport.get,
                url,
                {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://data.eastmoney.com/",
                },
            )
            response, gate_diagnostics = self._request_gate.run(request)
            degradations.extend(
                _gate_degradation(self.operation_id, item) for item in gate_diagnostics
            )
            page = _decode_report_page(response, page_number)
            if page_number == 1 and page.hits == 0 and not page.rows:
                raise _ReportSourceError(
                    "indeterminate_empty_result",
                    "The source returned an empty first page without a business status.",
                )
            if page_number <= page.total_pages and page.hits > 0 and not page.rows:
                raise _ReportSourceError(
                    "unknown_schema",
                    "The source returned an empty page before the reported final page.",
                )

            covered_window_start = False
            for row in page.rows:
                published_date = _published_date(row)
                if previous_date is not None and published_date > previous_date:
                    raise _ReportSourceError(
                        "unknown_schema",
                        "The source report rows are not ordered by publication date.",
                    )
                previous_date = published_date
                if published_date < date.fromisoformat(query.published_from):
                    covered_window_start = True
                    continue
                if published_date > date.fromisoformat(query.published_to):
                    degradations.append(
                        SourceFailure(
                            source_operation=self.operation_id,
                            code="future_material_rejected",
                            message=(
                                "A source row beyond the requested publication "
                                "boundary was rejected."
                            ),
                        )
                    )
                    continue
                if theme_title_filter and not any(
                    keyword in _required_text(row, "title")
                    for keyword in query.keywords
                ):
                    continue
                observation = _report_observation(
                    row=row,
                    material_type=material_type,
                    subject=query.subject
                    if material_type == "research_report"
                    else None,
                    expected_code=code,
                    expected_name=expected_name,
                    expected_industry_code=industry_code,
                    current_year=page.current_year,
                    source_uri=url,
                    retrieved_at=response.retrieved_at,
                )
                if len(observations) < query.limit:
                    observations.append(observation)
                if not theme_title_filter and len(observations) >= query.limit:
                    return (
                        observations,
                        degradations,
                        (covered_window_start or page_number >= page.total_pages),
                    )

            if covered_window_start or page_number >= page.total_pages:
                return observations, degradations, True
            page_number += 1


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


class IwencaiContentSearchOperation:
    """Credentialed semantic content discovery with sanitized failures."""

    operation_id = "iwencai_content_search@1"
    supported_material_types = frozenset(
        {"research_report", "announcement", "stock_news"}
    )
    _channels = {
        "research_report": "report",
        "announcement": "announcement",
        "stock_news": "news",
    }

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        environ: Mapping[str, str] | None = None,
        credential_env: str = "IWENCAI_API_KEY",
        base_url_env: str = "IWENCAI_BASE_URL",
        trace_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transport = transport
        self._environ = os.environ if environ is None else environ
        self._credential_env = credential_env
        self._base_url_env = base_url_env
        self._trace_id_factory = trace_id_factory or (lambda: secrets.token_hex(32))

    def collect(self, query: ContentQuery) -> SourceBatch:
        requested = tuple(
            material_type
            for material_type in query.material_types
            if material_type in self.supported_material_types
        )
        if not requested:
            return SourceBatch(operation_id=self.operation_id)
        if not query.allow_credentials:
            return self._failed(
                "credentials_not_allowed",
                "Credentialed content search is disabled by source policy.",
            )
        credential = self._environ.get(self._credential_env, "")
        if not credential:
            return self._failed(
                "missing_credential",
                "The configured content-search credential is unavailable.",
            )
        base_url = self._environ.get(
            self._base_url_env,
            "https://openapi.iwencai.com",
        ).rstrip("/")
        if not _is_https_origin(base_url):
            return self._failed(
                "invalid_source_configuration",
                "The configured content-search origin is invalid.",
            )
        if not query.keywords:
            return self._failed(
                "invalid_query",
                "Semantic content search requires at least one keyword.",
            )

        observations: list[ContentObservation] = []
        degradations: list[SourceFailure] = []
        endpoint = f"{base_url}/v1/comprehensive/search"
        for material_type in requested:
            payload = {
                "channels": [self._channels[material_type]],
                "app_id": "AIME_SKILL",
                "query": " ".join(query.keywords),
                "size": query.limit,
            }
            headers = {
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
                "X-Claw-Call-Type": "normal",
                "X-Claw-Skill-Id": "report-search",
                "X-Claw-Skill-Version": "2.0.0",
                "X-Claw-Plugin-Id": "none",
                "X-Claw-Plugin-Version": "none",
                "X-Claw-Trace-Id": self._trace_id_factory(),
            }
            try:
                response = self._transport.post(
                    endpoint,
                    headers,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
                rows = _decode_iwencai_rows(response)
                for row in rows:
                    observation, future = _iwencai_observation(
                        row=row,
                        material_type=material_type,
                        query=query,
                        source_uri=endpoint,
                        retrieved_at=response.retrieved_at,
                    )
                    if future:
                        degradations.append(
                            SourceFailure(
                                source_operation=self.operation_id,
                                code="future_material_rejected",
                                message=(
                                    "A source row beyond the requested publication "
                                    "boundary was rejected."
                                ),
                            )
                        )
                    elif observation is not None:
                        observations.append(observation)
            except _ReportSourceError as error:
                return self._failed(error.code, str(error))
            except TransportError as error:
                return self._failed(error.code, str(error))
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            degradations=tuple(degradations),
            limitations=("semantic_search_completeness_unproven",),
            complete=False,
        )

    def _failed(self, code: str, message: str) -> SourceBatch:
        return SourceBatch(
            operation_id=self.operation_id,
            source_errors=(
                SourceFailure(
                    source_operation=self.operation_id,
                    code=code,
                    message=message,
                ),
            ),
            complete=False,
        )


class ThsConsensusMaterialOperation:
    """Expose the qualified current consensus snapshot as opinion material."""

    operation_id = "ths_consensus_material@1"
    supported_material_types = frozenset({"consensus_material"})

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        research_now: datetime | None = None,
    ) -> None:
        self._transport = transport
        self._research_now = research_now
        self._source = ThsConsensusEpsOperation()

    def collect(self, query: ContentQuery) -> SourceBatch:
        if "consensus_material" not in query.material_types:
            return SourceBatch(operation_id=self.operation_id)
        try:
            code, name = _canonical_stock_subject(query.subject)
        except _ReportSourceError as error:
            return self._failed(error.code, str(error))
        now = self._research_now or datetime.now(CHINA_STANDARD_TIME)
        if query.as_of != now.astimezone(CHINA_STANDARD_TIME).date().isoformat():
            return self._failed(
                "current_snapshot_not_historical",
                "The consensus source only exposes a current snapshot.",
            )
        security_data = query.subject["security"] if query.subject else {}
        security = f"{security_data['exchange']}:{code}"
        try:
            snapshot = self._source.observe(security, self._transport)
        except SourceOperationError as error:
            return self._failed(error.code, str(error))
        if (
            snapshot.retrieved_at.astimezone(CHINA_STANDARD_TIME).date().isoformat()
            != query.as_of
        ):
            return self._failed(
                "current_snapshot_not_historical",
                "The consensus source only exposes a current snapshot.",
            )
        forecasts = [
            {
                "year": item.year,
                "institutions": item.institutions,
                "minimum": item.minimum,
                "mean": item.mean,
                "maximum": item.maximum,
            }
            for item in snapshot.forecasts
        ]
        observation = ContentObservation(
            material_type="consensus_material",
            source_operation=self.operation_id,
            source_role="attributed_opinion",
            source_document_id=None,
            title=f"{name or security} consensus EPS snapshot",
            published_at=None,
            retrieved_at=snapshot.retrieved_at,
            locator_uri=snapshot.source_uri,
            subject=query.subject,
            author="同花顺机构一致预期聚合",
            summary=None,
            document_locator=None,
            attributes={
                "aggregation": "source_aggregated_mean",
                "unit": "CNY/share",
                "forecasts": forecasts,
            },
            limitations=(
                "current_snapshot_only",
                "publication_time_unknown",
                "source_document_id_unknown",
                "aggregate_first_publication_time_unknown",
            ),
        )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=(observation,),
            complete=True,
        )

    def _failed(self, code: str, message: str) -> SourceBatch:
        return SourceBatch(
            operation_id=self.operation_id,
            source_errors=(
                SourceFailure(
                    source_operation=self.operation_id,
                    code=code,
                    message=message,
                ),
            ),
            complete=False,
        )


def _is_https_origin(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _decode_iwencai_rows(response: Any) -> tuple[dict[str, Any], ...]:
    if response.status != 200:
        raise _ReportSourceError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _ReportSourceError(
            "unexpected_content_type",
            "The source response is not JSON.",
        )
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected search schema.",
        ) from error
    if not isinstance(payload, dict):
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected search schema.",
        )
    status_code = payload.get("status_code")
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected search schema.",
        )
    if status_code != 0:
        raise _ReportSourceError(
            "upstream_business_error",
            "The content-search source rejected the request.",
        )
    rows = payload.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected search schema.",
        )
    return tuple(rows)


def _iwencai_observation(
    *,
    row: dict[str, Any],
    material_type: str,
    query: ContentQuery,
    source_uri: str,
    retrieved_at: datetime,
) -> tuple[ContentObservation | None, bool]:
    document_id = _required_text(row, "uid")
    title = _required_text(row, "title")
    published_value = _required_text(row, "publish_date")
    try:
        published = date.fromisoformat(published_value)
    except ValueError as error:
        raise _ReportSourceError(
            "unknown_schema",
            "A semantic-search publication date is invalid.",
        ) from error
    if published.isoformat() != published_value:
        raise _ReportSourceError(
            "unknown_schema",
            "A semantic-search publication date is invalid.",
        )
    if published > date.fromisoformat(query.published_to):
        return None, True
    if published < date.fromisoformat(query.published_from):
        return None, False

    expected_code: str | None = None
    if query.subject is not None:
        expected_code, _ = _canonical_stock_subject(query.subject)
        stock_infos = row.get("stock_infos")
        if not isinstance(stock_infos, list) or not any(
            isinstance(item, dict) and item.get("code") == expected_code
            for item in stock_infos
        ):
            raise _ReportSourceError(
                "identity_mismatch",
                "A semantic-search row does not match the requested security.",
            )

    extra = row.get("extra", {})
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError as error:
            raise _ReportSourceError(
                "unknown_schema",
                "A semantic-search extra field is invalid.",
            ) from error
    if not isinstance(extra, dict):
        raise _ReportSourceError(
            "unknown_schema",
            "A semantic-search extra field is invalid.",
        )
    author = extra.get("organization")
    if author is not None and (not isinstance(author, str) or not author.strip()):
        raise _ReportSourceError(
            "unknown_schema",
            "A semantic-search organization is invalid.",
        )
    if material_type == "research_report" and not author:
        raise _ReportSourceError(
            "unknown_schema",
            "A research report must identify its attributed publisher.",
        )
    score = row.get("score")
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, (int, float))
    ):
        raise _ReportSourceError(
            "unknown_schema",
            "A semantic-search relevance score is invalid.",
        )
    role = (
        "market_observation"
        if material_type == "announcement"
        else "attributed_opinion"
    )
    limitations = [
        "publication_time_unknown",
        "publication_time_precision_is_date_only",
    ]
    if material_type == "announcement":
        limitations.append("disclosure_source_not_independently_verified")
    return (
        ContentObservation(
            material_type=material_type,
            source_operation="iwencai_content_search@1",
            source_role=role,
            source_document_id=document_id,
            title=title,
            published_at=None,
            retrieved_at=retrieved_at,
            locator_uri=source_uri,
            subject=query.subject,
            author=author.strip() if isinstance(author, str) else None,
            summary=None,
            document_locator=None,
            attributes={
                "channel": IwencaiContentSearchOperation._channels[material_type],
                "publication_date": published.isoformat(),
                "relevance_score": score,
                "provider_security_code": expected_code,
            },
            limitations=tuple(limitations),
        ),
        False,
    )


def _canonical_stock_subject(subject: dict[str, Any] | None) -> tuple[str, str | None]:
    if not isinstance(subject, dict):
        raise _ReportSourceError(
            "invalid_canonical_subject",
            "Stock report discovery requires one canonical A-share subject.",
        )
    security = subject.get("security")
    if not isinstance(security, dict):
        raise _ReportSourceError(
            "invalid_canonical_subject",
            "Stock report discovery requires one canonical A-share subject.",
        )
    exchange = security.get("exchange")
    code = security.get("code")
    security_type = security.get("type")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or re.fullmatch(r"[0-9]{6}", code) is None
        or security_type != "A_SHARE"
    ):
        raise _ReportSourceError(
            "invalid_canonical_subject",
            "Stock report discovery requires one canonical SSE or SZSE A-share subject.",
        )
    name = subject.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise _ReportSourceError(
            "invalid_canonical_subject",
            "A supplied canonical subject name must be non-empty.",
        )
    return code, name.strip() if isinstance(name, str) else None


def _required_industry_code(parameters: dict[str, Any]) -> str:
    industry_code = parameters.get("industry_code")
    if (
        not isinstance(industry_code, str)
        or _INDUSTRY_CODE.fullmatch(industry_code) is None
    ):
        raise _ReportSourceError(
            "invalid_industry_code",
            "Industry report discovery requires a provider industry_code.",
        )
    return industry_code


def _report_url(
    *,
    q_type: str,
    code: str | None,
    industry_code: str | None,
    query: ContentQuery,
    page_number: int,
    page_size: int,
) -> str:
    parameters = {
        "industryCode": industry_code or "*",
        "pageSize": str(page_size),
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": query.published_from,
        "endTime": query.published_to,
        "pageNo": str(page_number),
        "fields": "",
        "qType": q_type,
    }
    if code is not None:
        parameters.update(
            {
                "orgCode": "",
                "code": code,
                "rcode": "",
                "p": str(page_number),
                "pageNum": str(page_number),
                "pageNumber": str(page_number),
            }
        )
    return f"{EASTMONEY_REPORT_URL}?{urlencode(parameters)}"


def _decode_report_page(response: Any, expected_page: int) -> _ReportPage:
    if response.status != 200:
        raise _ReportSourceError(
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "text/plain":
        raise _ReportSourceError(
            "unexpected_content_type",
            "The source response is not the qualified text/plain JSON representation.",
        )
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected report schema.",
        ) from error
    if not isinstance(payload, dict):
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected report schema.",
        )
    hits = _nonnegative_int(payload.get("hits"))
    total_pages = _nonnegative_int(payload.get("TotalPage"))
    page_number = _nonnegative_int(payload.get("pageNo"))
    size = _nonnegative_int(payload.get("size"))
    rows = payload.get("data")
    if (
        hits is None
        or total_pages is None
        or page_number != expected_page
        or size is None
        or not isinstance(rows, list)
        or size != len(rows)
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise _ReportSourceError(
            "unknown_schema",
            "The source response does not match the expected report schema.",
        )
    current_year = payload.get("currentYear")
    if current_year is not None and _nonnegative_int(current_year) is None:
        raise _ReportSourceError(
            "unknown_schema",
            "The source current forecast year is invalid.",
        )
    return _ReportPage(
        hits=hits,
        total_pages=total_pages,
        page_number=page_number,
        rows=tuple(rows),
        current_year=current_year,
    )


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _published_date(row: dict[str, Any]) -> date:
    return _published_timestamp(row).date()


def _published_timestamp(row: dict[str, Any]) -> datetime:
    value = row.get("publishDate")
    if not isinstance(value, str):
        raise _ReportSourceError(
            "unknown_schema",
            "A source report publication date is missing or invalid.",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError as error:
        raise _ReportSourceError(
            "unknown_schema",
            "A source report publication date is missing or invalid.",
        ) from error
    return parsed.replace(tzinfo=CHINA_STANDARD_TIME)


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _ReportSourceError(
            "unknown_schema",
            f"A source report {field} is missing or invalid.",
        )
    return value.strip()


def _optional_text(row: dict[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise _ReportSourceError(
            "unknown_schema",
            f"A source report {field} is invalid.",
        )
    return value


def _optional_int(row: dict[str, Any], field: str) -> int | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _ReportSourceError(
            "unknown_schema",
            f"A source report {field} is invalid.",
        )
    return value


def _report_observation(
    *,
    row: dict[str, Any],
    material_type: str,
    subject: dict[str, Any] | None,
    expected_code: str | None,
    expected_name: str | None,
    expected_industry_code: str | None,
    current_year: int | None,
    source_uri: str,
    retrieved_at: datetime,
) -> ContentObservation:
    info_code = _required_text(row, "infoCode")
    if _INFO_CODE.fullmatch(info_code) is None:
        raise _ReportSourceError(
            "unknown_schema",
            "A source report infoCode is invalid.",
        )
    title = _required_text(row, "title")
    author = _required_text(row, "orgSName")
    if material_type == "research_report":
        provider_code = _required_text(row, "stockCode")
        provider_name = _required_text(row, "stockName")
        if expected_code is not None and (
            provider_code != expected_code
            or (expected_name is not None and provider_name != expected_name)
        ):
            raise _ReportSourceError(
                "identity_mismatch",
                "A source report row does not match the requested security.",
            )
    else:
        provider_industry_code = _required_text(row, "industryCode")
        provider_industry_name = _required_text(row, "industryName")
        if provider_industry_code != expected_industry_code:
            raise _ReportSourceError(
                "identity_mismatch",
                "A source report row does not match the requested provider industry.",
            )
        provider_code = None
        provider_name = None
    published = _published_timestamp(row)
    forecast_eps: dict[str, str] = {}
    if current_year is not None:
        for offset, field in enumerate(
            (
                "predictThisYearEps",
                "predictNextYearEps",
                "predictNextTwoYearEps",
            )
        ):
            value = _optional_text(row, field)
            if value is not None:
                forecast_eps[str(current_year + offset)] = value
    attributes = {
        "provider_stock_code": provider_code,
        "provider_stock_name": provider_name,
        "provider_industry_code": _optional_text(row, "indvInduCode")
        if material_type == "research_report"
        else expected_industry_code,
        "provider_industry_name": _optional_text(row, "indvInduName")
        if material_type == "research_report"
        else provider_industry_name,
        "rating": _optional_text(row, "emRatingName"),
        "report_type": _optional_int(row, "reportType"),
        "attachment_pages": _optional_int(row, "attachPages"),
        "attachment_size_kib": _optional_int(row, "attachSize"),
        "forecast_eps": forecast_eps,
        "forecast_eps_role": "single_publisher_opinion",
    }
    return ContentObservation(
        material_type=material_type,
        source_operation="eastmoney_reports@1",
        source_role="attributed_opinion",
        source_document_id=info_code,
        title=title,
        published_at=published.isoformat(),
        retrieved_at=retrieved_at,
        locator_uri=source_uri,
        subject=subject,
        author=author,
        summary=None,
        document_locator=EASTMONEY_PDF_TEMPLATE.format(info_code=info_code),
        attributes=attributes,
        limitations=("publication_time_timezone_not_explicit",),
    )
