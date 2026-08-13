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
PUBLIC_MATERIAL_SOURCE_ROLES = {
    "research_report": frozenset({"attributed_opinion"}),
    "industry_report": frozenset({"attributed_opinion"}),
    "consensus_material": frozenset({"attributed_opinion"}),
    "issuer_profile": frozenset({"market_observation"}),
    "stock_news": frozenset({"attributed_opinion"}),
    "announcement": frozenset({"authoritative_disclosure", "market_observation"}),
    "market_flash": frozenset({"market_signal"}),
    "investor_qa": frozenset({"market_observation", "attributed_opinion"}),
}
PUBLIC_OWNER_MATERIAL_SOURCE_ROLES = {
    ("eastmoney_reports@1", "research_report"): frozenset({"attributed_opinion"}),
    ("eastmoney_reports@1", "industry_report"): frozenset({"attributed_opinion"}),
    ("ths_consensus_material@1", "consensus_material"): frozenset(
        {"attributed_opinion"}
    ),
    ("eastmoney_stock_news@1", "stock_news"): frozenset({"attributed_opinion"}),
    ("cninfo_announcement@1", "announcement"): frozenset({"authoritative_disclosure"}),
    ("sse_announcement@1", "announcement"): frozenset({"authoritative_disclosure"}),
    ("szse_announcement@1", "announcement"): frozenset({"authoritative_disclosure"}),
    ("cls_market_flash@1", "market_flash"): frozenset({"market_signal"}),
    ("eastmoney_market_flash@1", "market_flash"): frozenset({"market_signal"}),
    ("cninfo_investor_qa@1", "investor_qa"): frozenset(
        {"market_observation", "attributed_opinion"}
    ),
    ("iwencai_content_search@1", "research_report"): frozenset({"attributed_opinion"}),
    ("iwencai_content_search@1", "stock_news"): frozenset({"attributed_opinion"}),
    ("iwencai_content_search@1", "announcement"): frozenset({"market_observation"}),
}
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


def has_declared_material_owner_authority(operation_id: object) -> bool:
    """Return whether an operation has an owner-local public material contract."""

    return isinstance(operation_id, str) and any(
        owner_operation_id == operation_id
        for owner_operation_id, _material_type in PUBLIC_OWNER_MATERIAL_SOURCE_ROLES
    )


def valid_material_source_role(
    material_type: object,
    source_role: object,
    *,
    operation_id: object = None,
) -> bool:
    """Check the bounded evidence roles permitted for one material owner and kind."""

    if not isinstance(material_type, str) or not isinstance(source_role, str):
        return False
    if isinstance(operation_id, str):
        owner_roles = PUBLIC_OWNER_MATERIAL_SOURCE_ROLES.get(
            (operation_id, material_type)
        )
        if owner_roles is not None:
            return source_role in owner_roles
        if has_declared_material_owner_authority(operation_id):
            return False
    return source_role in PUBLIC_MATERIAL_SOURCE_ROLES.get(material_type, ())


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
