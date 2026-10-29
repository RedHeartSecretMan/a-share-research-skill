"""Shared asset and research-scene metadata for the v0.4.1 capability model.

The registry is descriptive.  It does not decide whether a request is accepted,
which source operations run, or what status an individual result receives.  Those
behaviours remain owned by the existing request and result contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

AssetClass = Literal[
    "a_share_equity",
    "etf",
    "etf_option",
    "index",
    "market_aggregate",
    "bond",
    "fund",
    "other_derivative",
]
ResearchScene = Literal[
    "identity_scope",
    "market_observation",
    "valuation_fundamentals",
    "materials_events",
    "market_signals",
    "screening_ranking",
    "workflow_orchestration",
    "evidence_result",
]
CapabilityScope = Literal["subject_scoped", "market_wide", "cross_cutting"]
CapabilityKind = Literal["public", "internal", "policy", "planned", "deferred"]
ImplementationStatus = Literal["retain", "refactor", "defer"]
PublicState = Literal["supported", "blocked", "deferred"]
Priority = Literal["P0", "P1", "P2", "P3"]

_ASSET_CLASSES = frozenset(
    {
        "a_share_equity",
        "etf",
        "etf_option",
        "index",
        "market_aggregate",
        "bond",
        "fund",
        "other_derivative",
    }
)
_RESEARCH_SCENES = frozenset(
    {
        "identity_scope",
        "market_observation",
        "valuation_fundamentals",
        "materials_events",
        "market_signals",
        "screening_ranking",
        "workflow_orchestration",
        "evidence_result",
    }
)
_SCOPES = frozenset({"subject_scoped", "market_wide", "cross_cutting"})
_KINDS = frozenset({"public", "internal", "policy", "planned", "deferred"})
_IMPLEMENTATION_STATUSES = frozenset({"retain", "refactor", "defer"})
_PUBLIC_STATES = frozenset({"supported", "blocked", "deferred"})
_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})


@dataclass(frozen=True, slots=True)
class CapabilityMetadata:
    """Immutable classification for one task, workflow, or capability slot."""

    id: str
    version: str
    asset_class: AssetClass
    research_scene: ResearchScene
    scope: CapabilityScope
    capability_kind: CapabilityKind
    implementation_status: ImplementationStatus
    public_state: PublicState
    priority: Priority

    def __post_init__(self) -> None:
        validate_capability_metadata(self)

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "asset_class": self.asset_class,
            "research_scene": self.research_scene,
            "scope": self.scope,
            "capability_kind": self.capability_kind,
            "implementation_status": self.implementation_status,
            "public_state": self.public_state,
            "priority": self.priority,
        }


def validate_capability_metadata(item: CapabilityMetadata) -> None:
    """Validate the cross-axis invariants shared by all metadata consumers."""

    if not isinstance(item.id, str) or not item.id.strip():
        raise ValueError("capability metadata id must be non-empty")
    if not isinstance(item.version, str) or not item.version.strip():
        raise ValueError("capability metadata version must be non-empty")
    if item.asset_class not in _ASSET_CLASSES:
        raise ValueError("capability metadata asset_class is invalid")
    if item.research_scene not in _RESEARCH_SCENES:
        raise ValueError("capability metadata research_scene is invalid")
    if item.scope not in _SCOPES:
        raise ValueError("capability metadata scope is invalid")
    if item.capability_kind not in _KINDS:
        raise ValueError("capability metadata capability_kind is invalid")
    if item.implementation_status not in _IMPLEMENTATION_STATUSES:
        raise ValueError("capability metadata implementation_status is invalid")
    if item.public_state not in _PUBLIC_STATES:
        raise ValueError("capability metadata public_state is invalid")
    if item.priority not in _PRIORITIES:
        raise ValueError("capability metadata priority is invalid")
    if item.capability_kind == "deferred":
        if item.implementation_status != "defer" or item.public_state != "deferred":
            raise ValueError("deferred capability metadata must remain deferred")
    if item.capability_kind != "public" and item.public_state == "supported":
        raise ValueError("only public capability metadata can claim public support")
    if item.capability_kind == "public" and item.public_state == "deferred":
        raise ValueError("public capability metadata cannot be implicitly deferred")


def _item(
    id: str,
    asset_class: AssetClass,
    research_scene: ResearchScene,
    scope: CapabilityScope,
    capability_kind: CapabilityKind,
    implementation_status: ImplementationStatus,
    public_state: PublicState,
    priority: Priority,
    version: str = "1.0",
) -> CapabilityMetadata:
    return CapabilityMetadata(
        id=id,
        version=version,
        asset_class=asset_class,
        research_scene=research_scene,
        scope=scope,
        capability_kind=capability_kind,
        implementation_status=implementation_status,
        public_state=public_state,
        priority=priority,
    )


_ITEMS = (
    # Direct public ResearchTask capabilities.
    _item(
        "security_identity",
        "a_share_equity",
        "identity_scope",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_trend",
        "a_share_equity",
        "market_observation",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "security_valuation",
        "a_share_equity",
        "valuation_fundamentals",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "valuation_compare",
        "a_share_equity",
        "valuation_fundamentals",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "research_content",
        "a_share_equity",
        "materials_events",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "capital_events",
        "a_share_equity",
        "materials_events",
        "cross_cutting",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_signals",
        "a_share_equity",
        "market_signals",
        "cross_cutting",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    # Scope facets are policy rows for one public task ID; callers still use the
    # unsuffixed task and its existing request contract.
    _item(
        "capital_events_subject_scoped",
        "a_share_equity",
        "materials_events",
        "subject_scoped",
        "policy",
        "retain",
        "blocked",
        "P0",
    ),
    _item(
        "capital_events_market_wide",
        "market_aggregate",
        "materials_events",
        "market_wide",
        "policy",
        "retain",
        "blocked",
        "P0",
    ),
    _item(
        "market_signals_subject_scoped",
        "a_share_equity",
        "market_signals",
        "subject_scoped",
        "policy",
        "retain",
        "blocked",
        "P0",
    ),
    _item(
        "market_signals_market_wide",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "policy",
        "retain",
        "blocked",
        "P0",
    ),
    _item(
        "etf_market",
        "etf",
        "market_observation",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "etf_options",
        "etf_option",
        "market_observation",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P2",
    ),
    _item(
        "intraday_market_signal",
        "a_share_equity",
        "market_signals",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P2",
    ),
    _item(
        "intraday_replay",
        "a_share_equity",
        "market_observation",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P2",
    ),
    _item(
        "research_workflow",
        "a_share_equity",
        "workflow_orchestration",
        "cross_cutting",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    # Fixed public workflow identities.  Workflow IDs remain stable public IDs.
    _item(
        "single_security_research",
        "a_share_equity",
        "workflow_orchestration",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "single_security_valuation",
        "a_share_equity",
        "workflow_orchestration",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "valuation_comparison",
        "a_share_equity",
        "workflow_orchestration",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "theme_report_research",
        "a_share_equity",
        "workflow_orchestration",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "new_security_research",
        "a_share_equity",
        "workflow_orchestration",
        "subject_scoped",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_overview_research",
        "market_aggregate",
        "workflow_orchestration",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
        "2.0",
    ),
    # Screening remains a planned/refactor product and is not a registered public workflow.
    _item(
        "market_screen_research",
        "a_share_equity",
        "screening_ranking",
        "market_wide",
        "planned",
        "refactor",
        "blocked",
        "P1",
    ),
    _item(
        "fundamental_research_priority",
        "a_share_equity",
        "screening_ranking",
        "market_wide",
        "planned",
        "refactor",
        "blocked",
        "P1",
    ),
    # Market overview inventory, including synthetic policy slots.
    _item(
        "industry_board_breadth",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "limit_pools",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "industry_theme_rotation",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_heat",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "focus_monitoring",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "severe_abnormal_movement",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "board_fund_flow",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_dragon_tiger",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "northbound_disclosure",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_flash",
        "market_aggregate",
        "market_signals",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "market_trading_session",
        "market_aggregate",
        "market_observation",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "major_broad_indices",
        "index",
        "market_observation",
        "market_wide",
        "public",
        "retain",
        "supported",
        "P0",
    ),
    _item(
        "whole_market_turnover",
        "market_aggregate",
        "market_observation",
        "market_wide",
        "policy",
        "defer",
        "blocked",
        "P0",
    ),
    _item(
        "whole_market_margin",
        "market_aggregate",
        "materials_events",
        "market_wide",
        "policy",
        "defer",
        "blocked",
        "P0",
    ),
    _item(
        "market_wide_intraday",
        "market_aggregate",
        "market_observation",
        "market_wide",
        "policy",
        "defer",
        "blocked",
        "P0",
    ),
    # Internal modules are explicit and cannot be inferred as public capabilities.
    _item(
        "market_foundation",
        "market_aggregate",
        "market_observation",
        "cross_cutting",
        "internal",
        "retain",
        "blocked",
        "P0",
    ),
    _item(
        "result_artifact",
        "market_aggregate",
        "evidence_result",
        "cross_cutting",
        "internal",
        "retain",
        "blocked",
        "P1",
    ),
    # Explicit P3 deferred asset classes; no public support is inferred from fields shared with stocks.
    _item(
        "bond_research",
        "bond",
        "identity_scope",
        "subject_scoped",
        "deferred",
        "defer",
        "deferred",
        "P3",
    ),
    _item(
        "fund_research",
        "fund",
        "identity_scope",
        "subject_scoped",
        "deferred",
        "defer",
        "deferred",
        "P3",
    ),
    _item(
        "stock_option_research",
        "other_derivative",
        "market_observation",
        "subject_scoped",
        "deferred",
        "defer",
        "deferred",
        "P3",
    ),
    _item(
        "futures_research",
        "other_derivative",
        "market_observation",
        "subject_scoped",
        "deferred",
        "defer",
        "deferred",
        "P3",
    ),
    _item(
        "warrant_research",
        "other_derivative",
        "market_observation",
        "subject_scoped",
        "deferred",
        "defer",
        "deferred",
        "P3",
    ),
    _item(
        "open_ended_screening",
        "market_aggregate",
        "screening_ranking",
        "market_wide",
        "deferred",
        "defer",
        "deferred",
        "P3",
    ),
)

if len({item.id for item in _ITEMS}) != len(_ITEMS):
    raise AssertionError("capability metadata contains duplicate IDs")

CAPABILITY_METADATA: Mapping[str, CapabilityMetadata] = MappingProxyType(
    {item.id: item for item in _ITEMS}
)
# Descriptive alias for callers that prefer the longer registry name.
CAPABILITY_METADATA_REGISTRY = CAPABILITY_METADATA


def get_capability_metadata(capability_id: str) -> CapabilityMetadata:
    """Return metadata for an exact ID, failing closed for unknown capabilities."""

    try:
        return CAPABILITY_METADATA[capability_id]
    except KeyError as error:
        raise KeyError(f"unknown capability metadata id: {capability_id}") from error


def validate_capability_metadata_registry(
    registry: Mapping[str, CapabilityMetadata] = CAPABILITY_METADATA,
) -> None:
    """Validate registry keys and rows before a consumer publishes a view."""

    for capability_id, item in registry.items():
        if capability_id != item.id:
            raise ValueError("capability metadata registry key is inconsistent")
        validate_capability_metadata(item)


def capability_metadata_for(
    *,
    asset_class: AssetClass | None = None,
    research_scene: ResearchScene | None = None,
    scope: CapabilityScope | None = None,
    capability_kind: CapabilityKind | None = None,
) -> tuple[CapabilityMetadata, ...]:
    """Select immutable metadata rows by the shared axes."""

    return tuple(
        item
        for item in CAPABILITY_METADATA.values()
        if (asset_class is None or item.asset_class == asset_class)
        and (research_scene is None or item.research_scene == research_scene)
        and (scope is None or item.scope == scope)
        and (capability_kind is None or item.capability_kind == capability_kind)
    )


__all__ = [
    "AssetClass",
    "ResearchScene",
    "CapabilityScope",
    "CapabilityKind",
    "ImplementationStatus",
    "PublicState",
    "Priority",
    "CapabilityMetadata",
    "CAPABILITY_METADATA",
    "CAPABILITY_METADATA_REGISTRY",
    "capability_metadata_for",
    "get_capability_metadata",
    "validate_capability_metadata",
    "validate_capability_metadata_registry",
]
