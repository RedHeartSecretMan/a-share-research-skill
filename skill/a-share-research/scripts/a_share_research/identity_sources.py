"""Experimental source operations for resolving A-share security identity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .research_result import FieldDisposition, OperationOutcome

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class HttpResponse:
    """The small HTTP boundary shared by experimental source operations."""

    status: int
    content_type: str
    body: bytes
    retrieved_at: datetime


class HttpTransport(Protocol):
    """Network boundary injected into an Adapter operation."""

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse: ...


class TransportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UrlLibTransport:
    """Standard-library HTTP transport for installed runtime use."""

    def __init__(self) -> None:
        self._opener = build_opener()

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        request = Request(url, headers=headers, method="GET")
        return self._open(request, timeout_seconds=20.0)

    def get_with_timeout(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url, headers=headers, method="GET")
        return self._open(request, timeout_seconds=timeout_seconds)

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        request = Request(url, headers=headers, data=body, method="POST")
        return self._open(request, timeout_seconds=20.0)

    def _open(self, request: Request, *, timeout_seconds: float) -> HttpResponse:
        try:
            with self._opener.open(request, timeout=timeout_seconds) as opened:
                body = opened.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TransportError(
                        "response_too_large",
                        "The source response exceeds the safe size limit.",
                    )
                return HttpResponse(
                    status=opened.status,
                    content_type=opened.headers.get_content_type(),
                    body=body,
                    retrieved_at=datetime.now(timezone.utc).astimezone(
                        CHINA_STANDARD_TIME
                    ),
                )
        except HTTPError as error:
            if error.code == 429:
                raise TransportError(
                    "rate_limited",
                    "The source rate limit was reached.",
                ) from error
            raise TransportError(
                "upstream_http_error",
                f"The source returned HTTP status {error.code}.",
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise TransportError(
                "upstream_unavailable",
                "The source request could not be completed.",
            ) from error


class SourceOperationError(Exception):
    """A fail-closed, non-sensitive diagnosis from a source operation."""

    def __init__(self, source_operation: str, code: str, message: str) -> None:
        super().__init__(message)
        self.source_operation = source_operation
        self.code = code


def _unknown_schema(source_operation: str) -> SourceOperationError:
    return SourceOperationError(
        source_operation,
        "unknown_schema",
        "The source response does not match the expected schema.",
    )


def _request_response(
    source_operation: str,
    transport: HttpTransport,
    url: str,
    headers: dict[str, str],
) -> HttpResponse:
    try:
        return transport.get(url, headers)
    except TransportError as error:
        raise SourceOperationError(source_operation, error.code, str(error)) from error


def _decode_json_response(source_operation: str, response: HttpResponse) -> object:
    if response.status != 200:
        raise SourceOperationError(
            source_operation,
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise SourceOperationError(
            source_operation,
            "empty_response",
            "The source returned an empty response body.",
        )
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise SourceOperationError(
            source_operation,
            "unexpected_content_type",
            "The source response is not JSON.",
        )
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _unknown_schema(source_operation) from error


@dataclass(frozen=True)
class IdentityObservation:
    """A normalized observation made by one experimental source operation."""

    source_operation: str
    source_uri: str
    exchange: str | None
    code: str
    name: str
    issuer_name: str | None
    issuer_identifier: str | None
    issuer_relationship_verified: bool
    valid_from: str | None
    basis: str
    retrieved_at: datetime

    def to_observation(self, *, source_role: str) -> dict[str, object]:
        security = (
            f"{self.exchange}:{self.code}" if self.exchange is not None else self.code
        )
        subject: dict[str, object] = {
            "security": security if self.exchange is not None else None,
            "issuer": self.issuer_name,
        }
        if self.exchange is None:
            subject["security_clue"] = {"code": self.code}
        return {
            "source_role": source_role,
            "source_operation": self.source_operation,
            "experimental": True,
            "subject": subject,
            "observed_value": (
                {
                    "value": security,
                    "unit": "canonical_security_identifier",
                }
                if self.exchange is not None
                else {
                    "value": self.code,
                    "unit": "unresolved_security_code_clue",
                }
            ),
            "basis": self.basis,
            "observation": {
                "kind": "security_identity",
                "exchange": self.exchange,
                "code": self.code,
                "name": self.name,
                "security_type": "A_SHARE",
                "valid_from": self.valid_from,
                "valid_to": None,
                "listing_status": "current",
                "issuer_identifier": self.issuer_identifier,
                "issuer_relationship_verified": self.issuer_relationship_verified,
            },
            "evidence_time": self.retrieved_at.isoformat(),
            "available_at": None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"current A-share identity for {security}",
            },
            "limitations": [
                "experimental_source_operation",
                "availability_time_unknown",
            ],
        }

    def to_evidence(self) -> dict[str, object]:
        """Retain the pre-v0.3 internal shape for unmigrated ResearchTasks."""

        evidence_identity = (
            f"{self.exchange}:{self.code}"
            if self.exchange is not None
            else f"UNRESOLVED:{self.code}"
        )
        return {
            "id": f"identity-{self.source_operation}-{evidence_identity}",
            **self.to_observation(source_role="authoritative_disclosure"),
        }


@dataclass(frozen=True)
class IdentityFieldContract:
    """The declared actual fields and required subset for one identity operation."""

    dispositions: tuple[FieldDisposition, ...]
    required_fields: frozenset[str]

    def accepts(self, row: object) -> bool:
        if not isinstance(row, dict):
            return False
        declared_fields = {item.field for item in self.dispositions}
        actual_fields = set(row)
        return self.required_fields <= actual_fields <= declared_fields and all(
            isinstance(row[field], str) and row[field] for field in self.required_fields
        )


class SseStockListOperation:
    """Observe current SSE A-share identity from the official stock list."""

    operation_id = "sse_stock_list@1"
    endpoint = "https://query.sse.com.cn/commonQuery.do"
    field_contract = IdentityFieldContract(
        dispositions=(
            FieldDisposition(
                "SECURITY_CODE_A",
                "normalized",
                "observations[].payload.observation.code",
            ),
            FieldDisposition(
                "SECURITY_ABBR_A",
                "normalized",
                "observations[].payload.observation.name",
            ),
            FieldDisposition(
                "FULLNAME", "normalized", "observations[].payload.subject.issuer"
            ),
            FieldDisposition(
                "STATE_CODE_A_DESC",
                "cross_check_only",
                "claims[].payload.security.validity.status",
            ),
            FieldDisposition(
                "COMPANY_CODE",
                "cross_check_only",
                "claims[].payload.issuer.security_relationship",
            ),
        ),
        required_fields=frozenset(
            {
                "SECURITY_CODE_A",
                "SECURITY_ABBR_A",
                "FULLNAME",
                "STATE_CODE_A_DESC",
                "COMPANY_CODE",
            }
        ),
    )

    def observe(
        self, query: str, transport: HttpTransport
    ) -> list[IdentityObservation]:
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'isPagination': 'false',
                    'sqlId': 'COMMON_SSE_ZQPZ_GP_GPLB_C',
                    'productid': query,
                }
            )
        }"
        response = _request_response(
            self.operation_id,
            transport,
            url,
            {
                "Accept": "application/json",
                "Referer": "https://www.sse.com.cn/assortment/stock/list/",
                "User-Agent": "a-share-research-skill/1",
            },
        )
        payload = _decode_json_response(self.operation_id, response)
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
            raise _unknown_schema(self.operation_id)
        observations: list[IdentityObservation] = []
        for row in payload["result"]:
            if not self.field_contract.accepts(row):
                raise _unknown_schema(self.operation_id)
            assert isinstance(row, dict)
            if query not in {
                row["SECURITY_CODE_A"],
                row["SECURITY_ABBR_A"],
                row["FULLNAME"],
            }:
                continue
            if row["STATE_CODE_A_DESC"] != "上市":
                raise SourceOperationError(
                    self.operation_id,
                    "security_not_current",
                    "The matching SSE security is not currently listed.",
                )
            if row["COMPANY_CODE"] != row["SECURITY_CODE_A"]:
                raise SourceOperationError(
                    self.operation_id,
                    "inconsistent_identity_payload",
                    "The SSE security and issuer relationship is inconsistent.",
                )
            observations.append(
                IdentityObservation(
                    source_operation=self.operation_id,
                    source_uri=url,
                    exchange="SSE",
                    code=row["SECURITY_CODE_A"],
                    name=row["SECURITY_ABBR_A"],
                    issuer_name=row["FULLNAME"],
                    issuer_identifier=None,
                    issuer_relationship_verified=True,
                    valid_from=None,
                    basis="current_stock_list_membership",
                    retrieved_at=response.retrieved_at,
                )
            )
        if (
            payload["result"]
            and query.isascii()
            and query.isdigit()
            and not observations
        ):
            raise SourceOperationError(
                self.operation_id,
                "wrong_security_payload",
                "The source returned a different security than requested.",
            )
        return observations


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "".join("".join(parser.parts).split())


class SzseStockListOperation:
    """Observe current SZSE A-share identity from the official stock list."""

    operation_id = "szse_stock_list@1"
    endpoint = "https://www.szse.cn/api/report/ShowReport/data"
    field_contract = IdentityFieldContract(
        dispositions=(
            FieldDisposition(
                "agdm", "normalized", "observations[].payload.observation.code"
            ),
            FieldDisposition(
                "agjc", "normalized", "observations[].payload.observation.name"
            ),
            FieldDisposition(
                "agssrq",
                "normalized",
                "observations[].payload.observation.valid_from",
            ),
        ),
        required_fields=frozenset({"agdm", "agjc", "agssrq"}),
    )

    def observe(
        self, query: str, transport: HttpTransport
    ) -> list[IdentityObservation]:
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'SHOWTYPE': 'JSON',
                    'CATALOGID': '1110',
                    'TABKEY': 'tab1',
                    'PAGENO': '1',
                    'txtDMorJC': query,
                }
            )
        }"
        response = _request_response(
            self.operation_id,
            transport,
            url,
            {
                "Accept": "application/json",
                "Referer": "https://www.szse.cn/market/product/stock/list/",
                "User-Agent": "a-share-research-skill/1",
            },
        )
        payload = _decode_json_response(self.operation_id, response)
        if not isinstance(payload, list):
            raise _unknown_schema(self.operation_id)
        report = next(
            (
                entry
                for entry in payload
                if isinstance(entry, dict)
                and isinstance(entry.get("metadata"), dict)
                and entry["metadata"].get("catalogid") == "1110"
                and entry["metadata"].get("tabkey") == "tab1"
            ),
            None,
        )
        if report is None or not isinstance(report.get("data"), list):
            raise _unknown_schema(self.operation_id)
        observations: list[IdentityObservation] = []
        for row in report["data"]:
            if not self.field_contract.accepts(row):
                raise _unknown_schema(self.operation_id)
            assert isinstance(row, dict)
            name = _plain_text(row["agjc"])
            if row["agdm"] != query and name != query:
                continue
            observations.append(
                IdentityObservation(
                    source_operation=self.operation_id,
                    source_uri=url,
                    exchange="SZSE",
                    code=row["agdm"],
                    name=name,
                    issuer_name=None,
                    issuer_identifier=None,
                    issuer_relationship_verified=False,
                    valid_from=row["agssrq"],
                    basis="current_stock_list_membership",
                    retrieved_at=response.retrieved_at,
                )
            )
        if report["data"] and query.isascii() and query.isdigit() and not observations:
            raise SourceOperationError(
                self.operation_id,
                "wrong_security_payload",
                "The source returned a different security than requested.",
            )
        return observations


class CninfoSecurityDictionaryOperation:
    """Observe security-to-issuer links from the CNINFO dictionary."""

    operation_id = "cninfo_security_dictionary@1"
    endpoint = "https://www.cninfo.com.cn/new/data/szse_stock.json"
    field_contract = IdentityFieldContract(
        dispositions=(
            FieldDisposition(
                "code", "normalized", "observations[].payload.observation.code"
            ),
            FieldDisposition(
                "zwjc", "normalized", "observations[].payload.observation.name"
            ),
            FieldDisposition(
                "category", "cross_check_only", "claims[].payload.security.type"
            ),
            FieldDisposition(
                "orgId",
                "normalized",
                "observations[].payload.observation.issuer_identifier",
            ),
            FieldDisposition(
                "pinyin",
                "excluded",
                None,
                "provider_search_alias_not_needed_for_identity",
            ),
        ),
        required_fields=frozenset({"code", "zwjc", "category", "orgId"}),
    )

    def observe(
        self, query: str, transport: HttpTransport
    ) -> list[IdentityObservation]:
        response = _request_response(
            self.operation_id,
            transport,
            self.endpoint,
            {
                "Accept": "application/json",
                "Referer": "https://www.cninfo.com.cn/new/snapshot/companyListCn",
                "User-Agent": "a-share-research-skill/1",
            },
        )
        payload = _decode_json_response(self.operation_id, response)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("stockList"), list
        ):
            raise _unknown_schema(self.operation_id)
        observations: list[IdentityObservation] = []
        for row in payload["stockList"]:
            if not self.field_contract.accepts(row):
                raise _unknown_schema(self.operation_id)
            assert isinstance(row, dict)
            if row["code"] != query and row["zwjc"] != query:
                continue
            if row["category"] != "A股":
                raise SourceOperationError(
                    self.operation_id,
                    "security_type_mismatch",
                    "The matching CNINFO entry is not an A-share security.",
                )
            observations.append(
                IdentityObservation(
                    source_operation=self.operation_id,
                    source_uri=self.endpoint,
                    exchange=None,
                    code=row["code"],
                    name=row["zwjc"],
                    issuer_name=None,
                    issuer_identifier=row["orgId"],
                    issuer_relationship_verified=False,
                    valid_from=None,
                    basis="current_security_dictionary_entry",
                    retrieved_at=response.retrieved_at,
                )
            )
        return observations


IDENTITY_CAPABILITY_ID = "security_identity_resolution"


@dataclass(frozen=True)
class IdentityOperationRequest:
    """The bounded request accepted by every identity operation Adapter."""

    query: str
    as_of: str


class IdentityOperationAdapter(Protocol):
    """The only lower seam used by identity research."""

    @property
    def capability_id(self) -> str: ...

    @property
    def operation_id(self) -> str: ...

    @property
    def source_role(self) -> str: ...

    @property
    def field_dispositions(self) -> tuple[FieldDisposition, ...]: ...

    def execute(
        self, request: IdentityOperationRequest, timeout_seconds: float
    ) -> OperationOutcome: ...


class _ObservingOperation(Protocol):
    operation_id: str
    field_contract: IdentityFieldContract

    def observe(
        self, query: str, transport: HttpTransport
    ) -> list[IdentityObservation]: ...


@dataclass(frozen=True)
class ProductionIdentityOperationAdapter:
    """Turn one qualified parser operation into an operation-owned outcome."""

    operation: _ObservingOperation
    transport: HttpTransport
    source_role: str
    capability_id: str = IDENTITY_CAPABILITY_ID

    @property
    def operation_id(self) -> str:
        return self.operation.operation_id

    @property
    def field_dispositions(self) -> tuple[FieldDisposition, ...]:
        return self.operation.field_contract.dispositions

    def execute(
        self, request: IdentityOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        if not request.query.strip():
            raise ValueError("identity operation query must be non-empty")
        if timeout_seconds <= 0:
            raise ValueError("identity operation timeout must be positive")
        bounded_transport = _BoundedIdentityTransport(
            self.transport, timeout_seconds=timeout_seconds
        )
        try:
            observations = self.operation.observe(request.query, bounded_transport)
        except SourceOperationError as error:
            return OperationOutcome(
                capability_id=self.capability_id,
                operation_id=self.operation_id,
                coverage="failed",
                source_errors=(
                    {
                        "source_operation": self.operation_id,
                        "code": error.code,
                        "message": str(error),
                    },
                ),
                field_dispositions=self.field_dispositions,
                diagnostics={"attempts": 1, "timeout_seconds": timeout_seconds},
            )
        normalized = tuple(
            item.to_observation(source_role=self.source_role) for item in observations
        )
        return OperationOutcome(
            capability_id=self.capability_id,
            operation_id=self.operation_id,
            coverage="complete" if normalized else "observed_empty",
            observations=normalized,
            field_dispositions=self.field_dispositions,
            diagnostics={"attempts": 1, "timeout_seconds": timeout_seconds},
        )


@dataclass(frozen=True)
class _BoundedIdentityTransport:
    delegate: HttpTransport
    timeout_seconds: float

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        if isinstance(self.delegate, UrlLibTransport):
            return self.delegate.get_with_timeout(url, headers, self.timeout_seconds)
        return self.delegate.get(url, headers)


def build_default_identity_operations(
    transport: HttpTransport,
) -> tuple[IdentityOperationAdapter, ...]:
    """Build the three fixed production identity operation Adapters."""

    operations: tuple[IdentityOperationAdapter, ...] = (
        ProductionIdentityOperationAdapter(
            operation=SseStockListOperation(),
            transport=transport,
            source_role="authoritative_disclosure",
        ),
        ProductionIdentityOperationAdapter(
            operation=SzseStockListOperation(),
            transport=transport,
            source_role="authoritative_disclosure",
        ),
        ProductionIdentityOperationAdapter(
            operation=CninfoSecurityDictionaryOperation(),
            transport=transport,
            source_role="market_observation",
        ),
    )
    return operations
