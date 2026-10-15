"""Fixed current three-exchange source acquisition, before as-of qualification.

This Module assembles existing real source parsers and the caller's shared lanes.
It returns their existing acquisition values, never a second Universe model or
an expected denominator made from unproved dates/counts. Historical provider
selection and qualified records still depend on source-specific field proof.
The default projection preserves unknown denominators and the original source
observations separately; it cannot promote an unqualified row into a member.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, cast

from .a_share_universe import (
    AShareUniverse,
    IdentityAdapter,
    build_as_of_a_share_universe,
)
from .bse_universe_source import (
    BseCurrentIdentityAdapter,
    BseCurrentListAcquisition,
    BsePostTransport,
    acquire_bse_current_list,
)
from .bse_membership_event import (
    BseMembershipEventAcquisition,
    BseMembershipEventAdapter,
    BseMembershipEventTarget,
    BseMembershipIdentityAdapter,
)
from .content_contract import ContentHttpTransport
from .identity_sources import HttpTransport, UrlLibTransport
from .provider_lane import ProviderLanePolicy, ProviderLanes
from .sse_universe_source import (
    SseCurrentIdentityAdapter,
    SseListAcquisition,
    acquire_sse_current_lists,
)
from .sse_listing_notice import (
    SseListingNoticeAcquisition,
    SseListingNoticeAdapter,
    SseListingNoticeTarget,
)
from .szse_universe_source import (
    CurrentListAcquisition,
    SzseCurrentIdentityAdapter,
    acquire_szse_current_list,
)
from .szse_listing_material import (
    SzseListingEventAdapter,
    SzseListingEventTarget,
    SzseListingMaterialAcquisition,
)
from .universe_input import (
    ExchangeListOutcome,
    UniverseRequest,
    prepare_universe_request,
)


@dataclass(frozen=True, slots=True)
class CurrentSourcePolicies:
    """Finite owner budgets, all using the caller's shared provider lanes."""

    sse: ProviderLanePolicy
    szse: ProviderLanePolicy
    bse: ProviderLanePolicy


@dataclass(frozen=True, slots=True)
class CurrentSourceTransports:
    """Atomic test transports; None selects that source's owned urllib leaf."""

    sse: HttpTransport | None = None
    szse: HttpTransport | None = None
    bse: BsePostTransport | None = None


def _checked_source_transports(
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None,
) -> CurrentSourceTransports:
    if type(policies) is not CurrentSourcePolicies or any(
        type(policy) is not ProviderLanePolicy
        for policy in (policies.sse, policies.szse, policies.bse)
    ):
        raise TypeError("The source policies must use finite provider lane policies.")
    if transports is not None and type(transports) is not CurrentSourceTransports:
        raise TypeError("The source transports must use the fixed source seam.")
    selected = transports if transports is not None else CurrentSourceTransports()
    if any(
        isinstance(transport, UrlLibTransport)
        for transport in (selected.sse, selected.szse, selected.bse)
    ):
        raise TypeError("Each source must own its counted urllib transport.")
    return selected


_Acquisition = SseListAcquisition | CurrentListAcquisition | BseCurrentListAcquisition


class _RequestView(Protocol):
    screen_date: str
    information_cutoff: str


class _CurrentListAdapter:
    """One fixed exchange owner; raw acquisition remains separately auditable."""

    def __init__(
        self,
        exchange: str,
        *,
        research_now: datetime,
        lanes: ProviderLanes,
        policy: ProviderLanePolicy,
        transport: HttpTransport | BsePostTransport | None,
    ) -> None:
        self.exchange = exchange
        self.operation_id = f"{exchange.lower()}_current_universe_list@1"
        self._research_now = research_now
        self._lanes = lanes
        self._policy = policy
        self._transport = transport
        self._last_acquisition: _Acquisition | None = None

    @property
    def last_acquisition(self) -> _Acquisition | None:
        return self._last_acquisition

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        view = cast(_RequestView, request)
        detached = UniverseRequest(view.screen_date, view.information_cutoff)
        policy = replace(
            self._policy,
            timeout_seconds=min(self._policy.timeout_seconds, timeout_seconds),
        )
        source: _Acquisition
        if self.exchange == "SSE":
            source = acquire_sse_current_lists(
                detached,
                research_now=self._research_now,
                lanes=self._lanes,
                policy=policy,
                max_pages=policy.actual_attempt_budget,
                transport=cast(HttpTransport | None, self._transport),
            )
        elif self.exchange == "SZSE":
            source = acquire_szse_current_list(
                detached,
                research_now=self._research_now,
                lanes=self._lanes,
                policy=policy,
                max_pages=policy.actual_attempt_budget,
                transport=cast(HttpTransport | None, self._transport),
            )
        else:
            source = acquire_bse_current_list(
                detached,
                research_now=self._research_now,
                lanes=self._lanes,
                policy=policy,
                max_pages=policy.actual_attempt_budget,
                transport=cast(BsePostTransport | None, self._transport),
            )
        self._last_acquisition = source
        # None of the current owners proves this factual version's membership
        # effective/availability fields. Preserve its real rows and #119 evidence
        # above; no source total can be promoted into an ordinary-A denominator.
        return ExchangeListOutcome(
            self.operation_id,
            self.exchange,
            source.membership_effective_date,
            bool(source.lane_result.attempts),
            source.source_result,
            None,
            source.pages_expected,
            source.pages_received,
            (),
        )


@dataclass(frozen=True, slots=True)
class CurrentUniverseAdapters:
    """Fixed source owners for #110; construction performs no source calls."""

    exchange_list_adapters: tuple[
        _CurrentListAdapter
        | SseListingNoticeAdapter
        | SzseListingEventAdapter
        | BseMembershipEventAdapter,
        ...,
    ]
    identity_adapters: tuple[IdentityAdapter, ...]


def create_default_current_universe_adapters(
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None = None,
) -> CurrentUniverseAdapters:
    """Load three real list and identity owners, with explicit finite budgets.

    Each list/identity execution uses its declared #119 budget. No identity call
    is invented when #110 rejects an incomplete list. Current rows' unknown
    effective/availability versions remain unqualified, even on today's request.
    """
    selected = _checked_source_transports(policies, transports)
    lists = tuple(
        _CurrentListAdapter(
            exchange,
            research_now=research_now,
            lanes=lanes,
            policy=policy,
            transport=transport,
        )
        for exchange, policy, transport in (
            ("SSE", policies.sse, selected.sse),
            ("SZSE", policies.szse, selected.szse),
            ("BSE", policies.bse, selected.bse),
        )
    )
    identities: tuple[IdentityAdapter, ...] = (
        SseCurrentIdentityAdapter(
            research_now=research_now,
            lanes=lanes,
            policy=policies.sse,
            transport=selected.sse,
            max_securities=policies.sse.actual_attempt_budget,
        ),
        SzseCurrentIdentityAdapter(
            research_now=research_now,
            lanes=lanes,
            policy=policies.szse,
            transport=selected.szse,
            max_pages=policies.szse.actual_attempt_budget,
        ),
        BseCurrentIdentityAdapter(
            research_now=research_now,
            lanes=lanes,
            policy=policies.bse,
            transport=selected.bse,
            max_pages=policies.bse.actual_attempt_budget,
        ),
    )
    return CurrentUniverseAdapters(lists, identities)


def acquire_default_current_sources(
    request: UniverseRequest,
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None = None,
) -> tuple[SseListAcquisition, CurrentListAcquisition, BseCurrentListAcquisition]:
    """Acquire the fixed current scopes without prefilled Universe snapshots.

    One frozen request is detached before I/O. Every group's page ceiling equals
    its explicit actual-attempt ceiling: retries consume that same #119 budget,
    so no extra implicit page or retry quota is created. Source-reported counts
    remain source counts; None stays unknown and does not become expected zero.
    """
    prepared = prepare_universe_request(request, research_now=research_now)
    detached = UniverseRequest(prepared.screen_date, prepared.information_cutoff)
    selected = _checked_source_transports(policies, transports)
    sse = acquire_sse_current_lists(
        detached,
        research_now=research_now,
        transport=selected.sse,
        lanes=lanes,
        policy=policies.sse,
        max_pages=policies.sse.actual_attempt_budget,
    )
    szse = acquire_szse_current_list(
        detached,
        research_now=research_now,
        transport=selected.szse,
        lanes=lanes,
        policy=policies.szse,
        max_pages=policies.szse.actual_attempt_budget,
    )
    bse = acquire_bse_current_list(
        detached,
        research_now=research_now,
        transport=selected.bse,
        lanes=lanes,
        policy=policies.bse,
        max_pages=policies.bse.actual_attempt_budget,
    )
    return sse, szse, bse


@dataclass(frozen=True, slots=True)
class UniverseAcquisition:
    """The existing immutable #110 result and its safe source observations."""

    universe: AShareUniverse
    source_acquisitions: tuple[
        SseListAcquisition | SseListingNoticeAcquisition,
        CurrentListAcquisition | SzseListingMaterialAcquisition,
        BseCurrentListAcquisition | BseMembershipEventAcquisition,
    ]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "universe": self.universe.to_public_dict(),
            "source_acquisitions": [
                source.to_public_dict() for source in self.source_acquisitions
            ],
        }


def acquire_default_current_universe(
    request: UniverseRequest,
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None = None,
) -> UniverseAcquisition:
    """Run the real current parsers and #110 without caller-filled membership.

    Current source rows do not yet prove membership effective/availability times.
    Their safe observations remain available for audit, while canonical expected
    counts remain unknown. Provider counts never become ordinary-A denominators.
    Historical requests preserve the current owners' explicit unsupported states.
    This partial path is not qualification of the unresolved membership fields.
    """
    return acquire_default_universe(
        request,
        research_now=research_now,
        lanes=lanes,
        policies=policies,
        transports=transports,
    )


def create_default_universe_adapters(
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None = None,
    sse_listing_event: SseListingNoticeTarget | None = None,
    sse_listing_transport: HttpTransport | None = None,
    szse_listing_event: SzseListingEventTarget | None = None,
    listing_transport: ContentHttpTransport | None = None,
    bse_membership_event: BseMembershipEventTarget | None = None,
    bse_material_transport: HttpTransport | None = None,
    bse_mapping_transport: HttpTransport | None = None,
) -> CurrentUniverseAdapters:
    """Select paired real source owners without performing source calls."""
    owners = create_default_current_universe_adapters(
        research_now=research_now,
        lanes=lanes,
        policies=policies,
        transports=transports,
    )
    list_owners = owners.exchange_list_adapters
    identity_owners = owners.identity_adapters
    if sse_listing_event is not None:
        sse_event_owner = SseListingNoticeAdapter(
            target=sse_listing_event,
            research_now=research_now,
            transport=sse_listing_transport,
            lanes=lanes,
            policy=policies.sse,
        )
        list_owners = (sse_event_owner, list_owners[1], list_owners[2])
    elif sse_listing_transport is not None:
        raise TypeError("An SSE listing transport requires its exact source target.")
    if szse_listing_event is not None:
        if type(szse_listing_event) is not SzseListingEventTarget:
            raise TypeError("The listing event must use an exact source target.")
        event_owner = SzseListingEventAdapter(
            research_now=research_now,
            source_code=szse_listing_event.source_code,
            document_url=szse_listing_event.document_url,
            max_metadata_pages=szse_listing_event.max_metadata_pages,
            transport=listing_transport,
            lanes=lanes,
            policy=policies.szse,
        )
        list_owners = (list_owners[0], event_owner, list_owners[2])
    elif listing_transport is not None:
        raise TypeError("A listing transport requires its exact source target.")
    if bse_membership_event is not None:
        bse_event_owner = BseMembershipEventAdapter(
            target=bse_membership_event,
            research_now=research_now,
            lanes=lanes,
            policy=policies.bse,
            material_transport=bse_material_transport,
            mapping_transport=bse_mapping_transport,
        )
        list_owners = (list_owners[0], list_owners[1], bse_event_owner)
        identity_owners = (
            identity_owners[0],
            identity_owners[1],
            BseMembershipIdentityAdapter(bse_event_owner),
        )
    elif bse_material_transport is not None or bse_mapping_transport is not None:
        raise TypeError("BSE event transports require their exact source target.")
    return CurrentUniverseAdapters(list_owners, identity_owners)


def acquire_default_universe(
    request: UniverseRequest,
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None = None,
    sse_listing_event: SseListingNoticeTarget | None = None,
    sse_listing_transport: HttpTransport | None = None,
    szse_listing_event: SzseListingEventTarget | None = None,
    listing_transport: ContentHttpTransport | None = None,
    bse_membership_event: BseMembershipEventTarget | None = None,
    bse_material_transport: HttpTransport | None = None,
    bse_mapping_transport: HttpTransport | None = None,
) -> UniverseAcquisition:
    """Assemble real current owners and an optional exact official event.

    The target only routes finite acquisition. Metadata and PDF must establish
    every member field. No sample issuer is hardcoded and no caller-filled list
    is accepted. An exact event supplies one useful partial on its formal day;
    other exchange totals and unsupported dates remain unknown/unavailable.
    """
    prepared = prepare_universe_request(request, research_now=research_now)
    detached = UniverseRequest(prepared.screen_date, prepared.information_cutoff)
    owners = create_default_universe_adapters(
        research_now=research_now,
        lanes=lanes,
        policies=policies,
        transports=transports,
        sse_listing_event=sse_listing_event,
        sse_listing_transport=sse_listing_transport,
        szse_listing_event=szse_listing_event,
        listing_transport=listing_transport,
        bse_membership_event=bse_membership_event,
        bse_material_transport=bse_material_transport,
        bse_mapping_transport=bse_mapping_transport,
    )
    list_owners = owners.exchange_list_adapters
    universe = build_as_of_a_share_universe(
        detached,
        research_now=research_now,
        exchange_list_adapters=list_owners,
        identity_adapters=owners.identity_adapters,
    )
    sources = tuple(owner.last_acquisition for owner in list_owners)
    if len(sources) != 3 or any(source is None for source in sources):
        raise RuntimeError("A current-source owner did not return its acquisition.")
    typed_sources = cast(
        tuple[
            SseListAcquisition | SseListingNoticeAcquisition,
            CurrentListAcquisition | SzseListingMaterialAcquisition,
            BseCurrentListAcquisition | BseMembershipEventAcquisition,
        ],
        sources,
    )
    return UniverseAcquisition(universe, typed_sources)
