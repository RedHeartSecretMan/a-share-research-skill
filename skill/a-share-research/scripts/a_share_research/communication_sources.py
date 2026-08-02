"""Experimental market-communication and issuer-interaction operations."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode

from .content_contract import (
    F10_PROFILE_CATEGORIES,
    ContentHttpTransport,
    ContentObservation,
    ContentQuery,
    ContentSourceOperation,
    SourceBatch,
    SourceFailure,
    valid_f10_profile_categories,
)
from .identity_sources import HttpResponse, TransportError
from .source_throttle import (
    EASTMONEY_REQUEST_GATE,
    RequestGate,
    RequestGateDiagnostic,
    RequestGateError,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
USER_AGENT = "Mozilla/5.0 (compatible; a-share-research-skill/0.1)"


class ClsMarketFlashOperation:
    """Collect signed public-market flashes from the CLS rolling feed."""

    operation_id = "cls_market_flash@1"
    supported_material_types = frozenset({"market_flash"})
    endpoint = "https://www.cls.cn/v1/roll/get_roll_list"

    def __init__(self, transport: ContentHttpTransport) -> None:
        self._transport = transport

    def collect(self, query: ContentQuery) -> SourceBatch:
        params = {
            "appName": "CailianpressWeb",
            "os": "web",
            "sv": "7.7.5",
            "last_time": "",
            "refresh_type": "1",
            "rn": str(query.limit),
        }
        sorted_query = "&".join(f"{key}={params[key]}" for key in sorted(params))
        sha1_hex = hashlib.sha1(sorted_query.encode("utf-8")).hexdigest()
        signature = hashlib.md5(sha1_hex.encode("ascii")).hexdigest()
        url = f"{self.endpoint}?{sorted_query}&sign={signature}"
        try:
            response = self._transport.get(
                url,
                {"User-Agent": USER_AGENT, "Referer": "https://www.cls.cn/"},
            )
        except TransportError as error:
            return _failed_batch(self.operation_id, error.code, str(error))
        payload, failure = _json_object(self.operation_id, response)
        if failure is not None:
            return _batch_with_failure(self.operation_id, failure)
        if payload.get("errno") != 0:
            return _failed_batch(
                self.operation_id,
                "provider_error",
                "The CLS source reported an unsuccessful business status.",
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("roll_data"), list):
            return _failed_batch(
                self.operation_id,
                "unknown_schema",
                "The CLS response does not match the expected schema.",
            )
        rows = data["roll_data"]
        if not rows:
            return _failed_batch(
                self.operation_id,
                "empty_response",
                "The CLS rolling feed returned no market flashes.",
            )

        observations: list[ContentObservation] = []
        for row in rows:
            observation = _cls_observation(row, response.retrieved_at)
            if observation is None:
                return _failed_batch(
                    self.operation_id,
                    "unknown_schema",
                    "A CLS market-flash record does not match the expected schema.",
                )
            observations.append(observation)
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            limitations=("feed_completeness_unproven",),
            complete=False,
        )


class EastmoneyMarketFlashOperation:
    """Collect the Eastmoney 7x24 feed as an independent market-signal source."""

    operation_id = "eastmoney_market_flash@1"
    supported_material_types = frozenset({"market_flash"})
    endpoint = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"

    def __init__(
        self,
        transport: ContentHttpTransport,
        *,
        trace_factory: Callable[[], str] | None = None,
        request_gate: RequestGate | None = None,
    ) -> None:
        self._transport = transport
        self._trace_factory = trace_factory or (lambda: str(uuid.uuid4()))
        self._request_gate = request_gate or EASTMONEY_REQUEST_GATE

    def collect(self, query: ContentQuery) -> SourceBatch:
        trace_id = self._trace_factory()
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": str(query.limit),
            "req_trace": trace_id,
        }
        url = f"{self.endpoint}?{urlencode(params)}"
        try:
            response, gate_diagnostics = self._request_gate.run(
                lambda: self._transport.get(
                    url,
                    {
                        "User-Agent": USER_AGENT,
                        "Referer": "https://kuaixun.eastmoney.com/",
                    },
                )
            )
        except RequestGateError as gate_error:
            error = gate_error.cause
            if not isinstance(error, TransportError):
                raise
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(
                    SourceFailure(self.operation_id, error.code, str(error)),
                ),
                degradations=_request_gate_degradations(
                    self.operation_id, gate_error.diagnostics
                ),
                complete=False,
            )
        except TransportError as error:
            return _failed_batch(self.operation_id, error.code, str(error))
        degradations = _request_gate_degradations(self.operation_id, gate_diagnostics)
        payload, failure = _json_object(self.operation_id, response)
        if failure is not None:
            return _append_degradations(
                _batch_with_failure(self.operation_id, failure), degradations
            )
        if payload.get("code") != "1":
            return _append_degradations(
                _failed_batch(
                    self.operation_id,
                    "provider_error",
                    "The Eastmoney source reported an unsuccessful business status.",
                ),
                degradations,
            )
        echoed_trace = payload.get("req_trace")
        if echoed_trace is not None and echoed_trace != trace_id:
            return _append_degradations(
                _failed_batch(
                    self.operation_id,
                    "unknown_schema",
                    "The Eastmoney response trace does not match the request.",
                ),
                degradations,
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("fastNewsList"), list):
            return _append_degradations(
                _failed_batch(
                    self.operation_id,
                    "unknown_schema",
                    "The Eastmoney response does not match the expected schema.",
                ),
                degradations,
            )
        rows = data["fastNewsList"]
        if not rows:
            return _append_degradations(
                _failed_batch(
                    self.operation_id,
                    "empty_response",
                    "The Eastmoney 7x24 feed returned no market flashes.",
                ),
                degradations,
            )
        observations: list[ContentObservation] = []
        for row in rows:
            observation = _eastmoney_flash_observation(row, response.retrieved_at)
            if observation is None:
                return _append_degradations(
                    _failed_batch(
                        self.operation_id,
                        "unknown_schema",
                        "An Eastmoney market-flash record does not match the expected schema.",
                    ),
                    degradations,
                )
            observations.append(observation)
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            degradations=degradations,
            limitations=("feed_completeness_unproven",),
            complete=False,
        )


class FallbackMarketFlashOperation:
    """Use Eastmoney only when the primary CLS feed produces no observations."""

    operation_id = "fallback_market_flash@1"
    supported_material_types = frozenset({"market_flash"})

    def __init__(
        self,
        primary: ContentSourceOperation,
        fallback: ContentSourceOperation,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def collect(self, query: ContentQuery) -> SourceBatch:
        primary = self._primary.collect(query)
        if primary.observations or not query.allow_fallback:
            return replace(primary, operation_id=self.operation_id)

        fallback = self._fallback.collect(query)
        fallback_used = SourceFailure(
            self.operation_id,
            "fallback_used",
            "The fallback market-flash source was used after the primary source produced no observations.",
            {
                "primary_operation": self._primary.operation_id,
                "fallback_operation": self._fallback.operation_id,
            },
        )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=fallback.observations,
            source_errors=primary.source_errors + fallback.source_errors,
            degradations=primary.degradations
            + fallback.degradations
            + (fallback_used,),
            limitations=tuple(
                dict.fromkeys(primary.limitations + fallback.limitations)
            ),
            complete=fallback.complete,
        )


class CninfoInvestorQaOperation:
    """Collect issuer-specific investor questions and company replies."""

    operation_id = "cninfo_investor_qa@1"
    supported_material_types = frozenset({"investor_qa"})
    lookup_endpoint = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
    question_endpoint = "https://irm.cninfo.com.cn/newircs/company/question"

    def __init__(self, transport: ContentHttpTransport) -> None:
        self._transport = transport

    def collect(self, query: ContentQuery) -> SourceBatch:
        canonical = _canonical_a_share(query.subject)
        if canonical is None:
            return _failed_batch(
                self.operation_id,
                "invalid_subject",
                "Investor Q&A requires one canonical SSE or SZSE A-share subject.",
            )
        exchange, code = canonical
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            lookup_response = self._transport.post(
                self.lookup_endpoint,
                headers,
                urlencode({"keyWord": code}).encode("ascii"),
            )
        except TransportError as error:
            return _failed_batch(self.operation_id, error.code, str(error))
        lookup, failure = _json_object(self.operation_id, lookup_response)
        if failure is not None:
            return _batch_with_failure(self.operation_id, failure)
        identity, identity_failure = _cninfo_identity(
            lookup, code, exchange, query.subject
        )
        if identity_failure is not None:
            return _batch_with_failure(self.operation_id, identity_failure)
        assert identity is not None
        org_id, company_name = identity

        params = {
            "_t": "1",
            "stockcode": code,
            "orgId": org_id,
            "pageSize": str(query.limit),
            "pageNum": "1",
            "keyWord": " ".join(query.keywords),
            "startDay": query.published_from,
            "endDay": query.published_to,
        }
        question_url = f"{self.question_endpoint}?{urlencode(params)}"
        try:
            question_response = self._transport.post(
                question_url,
                {"User-Agent": USER_AGENT},
                b"",
            )
        except TransportError as error:
            return _failed_batch(self.operation_id, error.code, str(error))
        payload, failure = _json_object(self.operation_id, question_response)
        if failure is not None:
            return _batch_with_failure(self.operation_id, failure)
        rows = payload.get("rows")
        total = payload.get("total")
        if (
            not isinstance(rows, list)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or (total > 0 and not rows)
        ):
            return _failed_batch(
                self.operation_id,
                "unknown_schema",
                "The CNINFO investor-Q&A response does not match the expected schema.",
            )

        observations: list[ContentObservation] = []
        source_errors: list[SourceFailure] = []
        assert query.subject is not None
        normalized_subject = query.subject
        for row in rows:
            normalized = _cninfo_qa_observations(
                row,
                normalized_subject,
                company_name,
                org_id,
                question_response.retrieved_at,
                question_url,
            )
            if isinstance(normalized, SourceFailure):
                if normalized.code == "answer_publication_time_missing":
                    source_errors.append(normalized)
                    question = _cninfo_question_only(
                        row,
                        normalized_subject,
                        company_name,
                        org_id,
                        question_response.retrieved_at,
                        question_url,
                    )
                    if question is None:
                        return _batch_with_failure(
                            self.operation_id,
                            SourceFailure(
                                self.operation_id,
                                "unknown_schema",
                                "A CNINFO investor-Q&A record does not match the expected schema.",
                            ),
                        )
                    observations.append(question)
                    continue
                return _batch_with_failure(self.operation_id, normalized)
            observations.extend(normalized)
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            source_errors=tuple(source_errors),
            limitations=("pagination_incomplete",) if total > len(rows) else (),
            complete=total <= len(rows) and not source_errors,
        )


class MootdxF10Operation:
    """Optionally observe issuer-profile text without overstating its evidence."""

    operation_id = "mootdx_f10@1"
    supported_material_types = frozenset({"issuer_profile"})
    allowed_categories = F10_PROFILE_CATEGORIES

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client_factory = client_factory or _default_mootdx_client
        self._clock = clock or _china_now

    def collect(self, query: ContentQuery) -> SourceBatch:
        canonical = _canonical_a_share(query.subject)
        if canonical is None:
            return _failed_batch(
                self.operation_id,
                "invalid_subject",
                "Issuer profile requires one canonical SSE or SZSE A-share subject.",
            )
        categories = query.parameters.get("profile_categories", ["公司概况"])
        if not valid_f10_profile_categories(categories):
            return _failed_batch(
                self.operation_id,
                "invalid_request",
                (
                    "Issuer-profile categories must contain 1 to 9 unique, "
                    "documented F10 categories."
                ),
            )
        try:
            client = self._client_factory()
        except (ImportError, ModuleNotFoundError):
            return SourceBatch(
                operation_id=self.operation_id,
                source_errors=(
                    SourceFailure(
                        self.operation_id,
                        "missing_optional_dependency",
                        "The optional mootdx dependency is not installed.",
                        {"dependency": "mootdx"},
                    ),
                ),
                complete=False,
            )
        except Exception:
            return _failed_batch(
                self.operation_id,
                "upstream_unavailable",
                "The optional mootdx F10 client could not be created.",
            )

        exchange, code = canonical
        assert query.subject is not None
        subject_name = query.subject.get("name")
        if not isinstance(subject_name, str) or not subject_name.strip():
            return _failed_batch(
                self.operation_id,
                "invalid_subject",
                "Issuer profile requires the canonical security name.",
            )
        observations: list[ContentObservation] = []
        source_errors: list[SourceFailure] = []
        for category in categories:
            try:
                text = client.F10(symbol=code, name=category)
            except Exception:
                source_errors.append(
                    SourceFailure(
                        self.operation_id,
                        "upstream_unavailable",
                        "The mootdx F10 request could not be completed.",
                        {"category": category},
                    )
                )
                continue
            if not isinstance(text, str):
                source_errors.append(
                    SourceFailure(
                        self.operation_id,
                        "unknown_schema",
                        "The mootdx F10 response is not text.",
                        {"category": category},
                    )
                )
                continue
            if not text.strip():
                source_errors.append(
                    SourceFailure(
                        self.operation_id,
                        "empty_response",
                        "The mootdx F10 source returned empty text.",
                        {"category": category},
                    )
                )
                continue
            retrieved_at = self._clock()
            observations.append(
                ContentObservation(
                    material_type="issuer_profile",
                    source_operation=self.operation_id,
                    source_role="market_observation",
                    source_document_id=None,
                    title=f"{subject_name.strip()} {category}",
                    published_at=None,
                    retrieved_at=retrieved_at,
                    locator_uri=(
                        f"mootdx://f10/{exchange}/{code}?"
                        f"{urlencode({'category': category})}"
                    ),
                    subject=query.subject,
                    author=None,
                    summary=None,
                    document_locator=None,
                    attributes={
                        "category": category,
                        "character_count": len(text),
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    },
                    limitations=(
                        "publication_time_unknown",
                        "source_document_id_unknown",
                        "f10_version_semantics_unverified",
                    ),
                    content=text,
                )
            )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            source_errors=tuple(source_errors),
            complete=(not source_errors and len(observations) == len(categories)),
        )


def _default_mootdx_client() -> Any:
    quotes_module = importlib.import_module("mootdx.quotes")
    return quotes_module.Quotes.factory(market="std")


def _china_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(CHINA_STANDARD_TIME)


def _canonical_a_share(subject: dict[str, Any] | None) -> tuple[str, str] | None:
    if not isinstance(subject, dict):
        return None
    security = subject.get("security")
    if not isinstance(security, dict):
        return None
    exchange = security.get("exchange")
    code = security.get("code")
    security_type = security.get("type")
    if (
        exchange not in {"SSE", "SZSE"}
        or not isinstance(code, str)
        or re.fullmatch(r"\d{6}", code) is None
        or security_type != "A_SHARE"
    ):
        return None
    return exchange, code


def _cninfo_identity(
    payload: dict[str, Any],
    code: str,
    exchange: str,
    requested_subject: dict[str, Any] | None,
) -> tuple[tuple[str, str] | None, SourceFailure | None]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None, SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "unknown_schema",
            "The CNINFO identity lookup does not match the expected schema.",
        )
    exact = [
        row for row in rows if isinstance(row, dict) and row.get("stockCode") == code
    ]
    if not exact:
        return None, SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "identity_not_found",
            "CNINFO did not return the requested A-share identity.",
        )
    if len(exact) != 1:
        return None, SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "ambiguous_identity",
            "CNINFO returned more than one exact identity candidate.",
        )
    row = exact[0]
    org_id = row.get("secid")
    company_name = row.get("shortName")
    stock_type = row.get("stockType")
    if (
        not isinstance(org_id, str)
        or not org_id.strip()
        or not isinstance(company_name, str)
        or not company_name.strip()
        or (stock_type is not None and stock_type not in {"A股", "A_SHARE", "S"})
    ):
        return None, SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "unknown_schema",
            "The CNINFO identity candidate is incomplete or not an A-share.",
        )
    requested_name = (
        requested_subject.get("name") if isinstance(requested_subject, dict) else None
    )
    if not isinstance(requested_name, str) or requested_name != company_name:
        return None, SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "wrong_security_payload",
            "The CNINFO issuer does not match the requested subject.",
            {"security": f"{exchange}:{code}"},
        )
    requested_issuer = (
        requested_subject.get("issuer") if isinstance(requested_subject, dict) else None
    )
    identifier = (
        requested_issuer.get("identifier")
        if isinstance(requested_issuer, dict)
        else None
    )
    if (
        isinstance(identifier, dict)
        and identifier.get("scheme") == "CNINFO_ORG_ID"
        and identifier.get("value") != org_id
    ):
        return None, SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "wrong_security_payload",
            "The CNINFO organization identifier conflicts with the subject identity.",
            {"security": f"{exchange}:{code}"},
        )
    return (org_id.strip(), company_name.strip()), None


def _cninfo_qa_observations(
    row: object,
    subject: dict[str, Any],
    company_name: str,
    org_id: str,
    retrieved_at: datetime,
    source_url: str,
) -> tuple[ContentObservation, ...] | SourceFailure:
    if not isinstance(row, dict):
        return SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "unknown_schema",
            "A CNINFO investor-Q&A record does not match the expected schema.",
        )
    security = subject.get("security")
    expected_code = security.get("code") if isinstance(security, dict) else None
    if (
        row.get("stockCode") != expected_code
        or row.get("companyShortName") != company_name
    ):
        return SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "wrong_security_payload",
            "A CNINFO investor-Q&A record belongs to another security.",
            {"security": security},
        )
    question = _cninfo_question_only(
        row, subject, company_name, org_id, retrieved_at, source_url
    )
    if question is None:
        return SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "unknown_schema",
            "A CNINFO investor-Q&A record does not match the expected schema.",
        )
    answer = row.get("attachedContent")
    if answer in (None, ""):
        return (question,)
    answer_id = row.get("attachedId")
    answerer = row.get("attachedAuthor")
    answer_published_at = _milliseconds_time(row.get("attachedPubDate"))
    if (
        not isinstance(answer, str)
        or not answer.strip()
        or not isinstance(answer_id, (str, int))
        or isinstance(answer_id, bool)
        or not str(answer_id)
        or not isinstance(answerer, str)
        or not answerer.strip()
        or answer_published_at is None
    ):
        return SourceFailure(
            CninfoInvestorQaOperation.operation_id,
            "answer_publication_time_missing",
            "A company reply lacks a distinct, usable publication time or identifier.",
            {"question_id": question.attributes["question_id"]},
        )
    update_at = _milliseconds_time(row.get("updateDate"))
    answer_observation = ContentObservation(
        material_type="investor_qa",
        source_operation=CninfoInvestorQaOperation.operation_id,
        source_role="attributed_opinion",
        source_document_id=f"answer-{answer_id}",
        title=f"{company_name}公司回复",
        published_at=answer_published_at,
        retrieved_at=retrieved_at,
        locator_uri=f"{source_url}#answer-{answer_id}",
        subject=subject,
        author=answerer.strip(),
        summary=answer.strip(),
        document_locator=None,
        attributes={
            "answer_id": str(answer_id),
            "question_id": question.attributes["question_id"],
            "question_published_at": question.published_at,
            "observed_update_at": update_at,
            "provider_company_short_name": company_name,
            "provider_org_id": org_id,
        },
        limitations=("company_reply_is_not_authoritative_disclosure",),
    )
    return question, answer_observation


def _cninfo_question_only(
    row: object,
    subject: dict[str, Any],
    company_name: str,
    org_id: str,
    retrieved_at: datetime,
    source_url: str,
) -> ContentObservation | None:
    if not isinstance(row, dict):
        return None
    security = subject.get("security")
    expected_code = security.get("code") if isinstance(security, dict) else None
    if (
        row.get("stockCode") != expected_code
        or row.get("companyShortName") != company_name
    ):
        return None
    question_id = row.get("indexId")
    question = row.get("mainContent")
    published_at = _milliseconds_time(row.get("pubDate"))
    if (
        not isinstance(question_id, (str, int))
        or isinstance(question_id, bool)
        or not str(question_id)
        or not isinstance(question, str)
        or not question.strip()
        or published_at is None
    ):
        return None
    update_at = _milliseconds_time(row.get("updateDate"))
    answer = row.get("attachedContent")
    author = row.get("authorName") or row.get("author")
    return ContentObservation(
        material_type="investor_qa",
        source_operation=CninfoInvestorQaOperation.operation_id,
        source_role="market_observation",
        source_document_id=f"question-{question_id}",
        title=f"{company_name}投资者提问",
        published_at=published_at,
        retrieved_at=retrieved_at,
        locator_uri=f"{source_url}#question-{question_id}",
        subject=subject,
        author=author.strip() if isinstance(author, str) and author.strip() else None,
        summary=question.strip(),
        document_locator=None,
        attributes={
            "question_id": str(question_id),
            "answer_status": "answered"
            if isinstance(answer, str) and answer.strip()
            else "unanswered",
            "observed_update_at": update_at,
            "provider_company_short_name": company_name,
            "provider_org_id": org_id,
        },
        limitations=("investor_question_is_not_issuer_disclosure",),
    )


def _milliseconds_time(value: object) -> str | None:
    return _optional_unix_time(value, seconds=False)


def _eastmoney_flash_observation(
    row: object, retrieved_at: datetime
) -> ContentObservation | None:
    if not isinstance(row, dict):
        return None
    source_id = row.get("code")
    title = row.get("title")
    published_text = row.get("showTime")
    if (
        not isinstance(source_id, str)
        or not source_id.strip()
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(published_text, str)
    ):
        return None
    try:
        published_at = datetime.strptime(published_text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=CHINA_STANDARD_TIME
        )
    except ValueError:
        return None
    summary = row.get("summary")
    if summary is not None and not isinstance(summary, str):
        return None
    return ContentObservation(
        material_type="market_flash",
        source_operation=EastmoneyMarketFlashOperation.operation_id,
        source_role="market_signal",
        source_document_id=source_id.strip(),
        title=title.strip(),
        published_at=published_at.isoformat(),
        retrieved_at=retrieved_at,
        locator_uri=(
            f"{EastmoneyMarketFlashOperation.endpoint}#fast-news-{source_id.strip()}"
        ),
        subject=None,
        author="东方财富",
        summary=summary.strip()
        if isinstance(summary, str) and summary.strip()
        else None,
        document_locator=None,
        attributes={
            "stock_list": row.get("stockList")
            if isinstance(row.get("stockList"), list)
            else [],
            "provider_sort": row.get("realSort"),
        },
        limitations=("publication_time_timezone_not_explicit",),
    )


def _cls_observation(row: object, retrieved_at: datetime) -> ContentObservation | None:
    if not isinstance(row, dict):
        return None
    source_id = row.get("id")
    published_timestamp = row.get("ctime")
    title = row.get("title") or row.get("brief")
    summary = row.get("content") or row.get("brief")
    locator = row.get("shareurl")
    if (
        isinstance(source_id, bool)
        or not isinstance(source_id, (int, str))
        or not str(source_id)
        or isinstance(published_timestamp, bool)
        or not isinstance(published_timestamp, (int, float))
        or published_timestamp <= 0
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(summary, str)
        or not isinstance(locator, str)
        or not locator.startswith(("https://", "http://"))
    ):
        return None
    published_at = datetime.fromtimestamp(published_timestamp, timezone.utc).astimezone(
        CHINA_STANDARD_TIME
    )
    modified_at = _optional_unix_time(row.get("modified_time"), seconds=True)
    author = row.get("author")
    return ContentObservation(
        material_type="market_flash",
        source_operation=ClsMarketFlashOperation.operation_id,
        source_role="market_signal",
        source_document_id=str(source_id),
        title=title.strip(),
        published_at=published_at.isoformat(),
        retrieved_at=retrieved_at,
        locator_uri=locator,
        subject=None,
        author=author.strip()
        if isinstance(author, str) and author.strip()
        else "财联社",
        summary=summary.strip() or None,
        document_locator=None,
        attributes={
            "modified_at": modified_at,
            "stock_list": row.get("stock_list")
            if isinstance(row.get("stock_list"), list)
            else [],
            "subjects": row.get("subjects")
            if isinstance(row.get("subjects"), list)
            else [],
        },
        limitations=(),
    )


def _optional_unix_time(value: object, *, seconds: bool) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    divisor = 1 if seconds else 1000
    return (
        datetime.fromtimestamp(value / divisor, timezone.utc)
        .astimezone(CHINA_STANDARD_TIME)
        .isoformat()
    )


def _json_object(
    operation_id: str, response: HttpResponse
) -> tuple[dict[str, Any], SourceFailure | None]:
    if response.status != 200:
        return {}, SourceFailure(
            operation_id,
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        return {}, SourceFailure(
            operation_id,
            "empty_response",
            "The source returned an empty response body.",
        )
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        return {}, SourceFailure(
            operation_id,
            "unexpected_content_type",
            "The source response is not JSON.",
        )
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, SourceFailure(
            operation_id,
            "unknown_schema",
            "The source response does not match the expected schema.",
        )
    if not isinstance(payload, dict):
        return {}, SourceFailure(
            operation_id,
            "unknown_schema",
            "The source response does not match the expected schema.",
        )
    return payload, None


def _failed_batch(operation_id: str, code: str, message: str) -> SourceBatch:
    return _batch_with_failure(operation_id, SourceFailure(operation_id, code, message))


def _batch_with_failure(operation_id: str, failure: SourceFailure) -> SourceBatch:
    return SourceBatch(
        operation_id=operation_id,
        source_errors=(failure,),
        complete=False,
    )


def _request_gate_degradations(
    operation_id: str,
    diagnostics: tuple[RequestGateDiagnostic, ...],
) -> tuple[SourceFailure, ...]:
    return tuple(
        SourceFailure(
            operation_id,
            diagnostic.code,
            diagnostic.message,
            diagnostic.details(),
        )
        for diagnostic in diagnostics
    )


def _append_degradations(
    batch: SourceBatch,
    degradations: tuple[SourceFailure, ...],
) -> SourceBatch:
    return replace(batch, degradations=batch.degradations + degradations)
