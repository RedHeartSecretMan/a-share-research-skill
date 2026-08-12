"""Versioned internal seam for research-content source operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .identity_sources import HttpResponse

PUBLIC_MATERIAL_TYPES = frozenset(
    {
        "research_report",
        "industry_report",
        "consensus_material",
        "issuer_profile",
        "stock_news",
        "announcement",
        "market_flash",
        "investor_qa",
    }
)
PUBLIC_SOURCE_ROLES = frozenset(
    {
        "authoritative_disclosure",
        "attributed_opinion",
        "market_observation",
        "market_signal",
    }
)
F10_PROFILE_CATEGORIES = frozenset(
    {
        "最新提示",
        "公司概况",
        "财务分析",
        "股东研究",
        "股本结构",
        "资本运作",
        "业内点评",
        "行业分析",
        "公司大事",
    }
)


def valid_f10_profile_categories(value: object) -> bool:
    """Return whether a caller supplied one unique, documented category set."""

    return (
        isinstance(value, list)
        and 1 <= len(value) <= len(F10_PROFILE_CATEGORIES)
        and all(
            isinstance(category, str) and category in F10_PROFILE_CATEGORIES
            for category in value
        )
        and len(set(value)) == len(value)
    )


def undeclared_raw_fields(
    value: object, declared_fields: tuple[str, ...]
) -> tuple[str, ...]:
    """Return provider field paths absent from one operation's static contract."""

    actual: set[str] = set()

    def visit(item: object, path: str) -> None:
        if isinstance(item, dict):
            if not item and path:
                actual.add(path)
            for key, nested in item.items():
                nested_path = f"{path}.{key}" if path else key
                visit(nested, nested_path)
        elif isinstance(item, list):
            array_path = f"{path}[]"
            actual.add(array_path)
            for nested in item:
                visit(nested, array_path)
        else:
            actual.add(path)

    visit(value, "")
    return tuple(sorted(actual.difference(declared_fields)))


@dataclass(frozen=True)
class ContentQuery:
    """Normalized query passed to one source operation."""

    material_types: tuple[str, ...]
    keywords: tuple[str, ...]
    as_of: str
    published_from: str
    published_to: str
    limit: int
    subject: dict[str, Any] | None
    parameters: dict[str, Any]
    allow_credentials: bool = False
    allow_fallback: bool = True


@dataclass(frozen=True)
class ContentObservation:
    """One source-indexed material before cross-source reconciliation."""

    material_type: str
    source_operation: str
    source_role: str
    source_document_id: str | None
    title: str
    published_at: str | None
    retrieved_at: datetime
    locator_uri: str
    subject: dict[str, Any] | None
    author: str | None
    summary: str | None
    document_locator: str | None
    attributes: dict[str, Any]
    limitations: tuple[str, ...]
    content: str | None = None
    source_document_namespace: str | None = None


@dataclass(frozen=True)
class SourceFailure:
    """Fail-closed, non-sensitive diagnosis returned by an operation."""

    source_operation: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        return {
            "source_operation": self.source_operation,
            "code": self.code,
            "message": self.message,
            **self.details,
        }


@dataclass(frozen=True)
class SourceBatch:
    """One operation's bounded observations and explicit acquisition state."""

    operation_id: str
    observations: tuple[ContentObservation, ...] = ()
    source_errors: tuple[SourceFailure, ...] = ()
    degradations: tuple[SourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()
    complete: bool = True


@dataclass(frozen=True)
class ContentEvidencePersistenceDeclaration:
    """One source owner's explicit license opt-in for normalized evidence reuse."""

    license_status: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.license_status, str)
            or not self.license_status.strip()
            or self.license_status == "not_established"
        ):
            raise ValueError("content evidence persistence license is invalid")


class EvidencePersistableContentSourceOperation:
    """Nominal opt-in mixin; structural lookalikes receive no persistence right."""

    @property
    def evidence_persistence(
        self,
    ) -> ContentEvidencePersistenceDeclaration:
        raise NotImplementedError


class ContentSourceOperation(Protocol):
    """Adapter operation seam hidden behind the research module."""

    operation_id: str
    supported_material_types: frozenset[str]

    @property
    def raw_fields(self) -> tuple[str, ...]: ...

    def collect(self, query: ContentQuery) -> SourceBatch: ...


class ContentHttpTransport(Protocol):
    """HTTP boundary for GET and POST based content operations."""

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse: ...

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse: ...
