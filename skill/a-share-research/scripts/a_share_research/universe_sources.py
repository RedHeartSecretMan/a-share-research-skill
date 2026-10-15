"""Fixed current three-exchange source acquisition, before as-of qualification.

This Module assembles existing real source parsers and the caller's shared lanes.
It returns their existing acquisition values, never a second Universe model or
an expected denominator made from unproved dates/counts. Historical provider
selection and qualified records still depend on source-specific field proof.
The default projection preserves unknown denominators and the original source
observations separately; it cannot promote an unqualified row into a member.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .a_share_universe import AShareUniverse, build_as_of_a_share_universe
from .bse_universe_source import (
    BseCurrentListAcquisition,
    BsePostTransport,
    acquire_bse_current_list,
)
from .identity_sources import HttpTransport, UrlLibTransport
from .provider_lane import ProviderLanePolicy, ProviderLanes
from .sse_universe_source import SseListAcquisition, acquire_sse_current_lists
from .szse_universe_source import CurrentListAcquisition, acquire_szse_current_list
from .universe_input import (
    ExchangeListOutcome,
    UniverseRequest,
    prepare_universe_request,
)


@dataclass(frozen=True, slots=True)
class CurrentSourcePolicies:
    """Explicit finite policies for the fixed three provider groups."""

    sse: ProviderLanePolicy
    szse: ProviderLanePolicy
    bse: ProviderLanePolicy


@dataclass(frozen=True, slots=True)
class CurrentSourceTransports:
    """Atomic test transports; None selects that source's owned urllib leaf."""

    sse: HttpTransport | None = None
    szse: HttpTransport | None = None
    bse: BsePostTransport | None = None


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
    if type(policies) is not CurrentSourcePolicies or any(
        type(policy) is not ProviderLanePolicy
        for policy in (policies.sse, policies.szse, policies.bse)
    ):
        raise TypeError(
            "The current-source policies must use finite provider lane policies."
        )
    if transports is not None and type(transports) is not CurrentSourceTransports:
        raise TypeError("The current-source transports must use the fixed source seam.")
    detached = UniverseRequest(prepared.screen_date, prepared.information_cutoff)
    selected = transports if transports is not None else CurrentSourceTransports()
    if any(
        isinstance(transport, UrlLibTransport)
        for transport in (selected.sse, selected.szse, selected.bse)
    ):
        raise TypeError("Each source must own its counted urllib transport.")
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
class CurrentUniverseAcquisition:
    """The existing immutable #110 result and its safe source observations."""

    universe: AShareUniverse
    source_acquisitions: tuple[
        SseListAcquisition, CurrentListAcquisition, BseCurrentListAcquisition
    ]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "universe": self.universe.to_public_dict(),
            "source_acquisitions": [
                source.to_public_dict() for source in self.source_acquisitions
            ],
        }


class _ListSnapshotAdapter:
    def __init__(self, outcome: ExchangeListOutcome) -> None:
        self.outcome = outcome
        self.exchange = outcome.exchange
        self.operation_id = outcome.operation_id

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        return self.outcome


def acquire_default_current_universe(
    request: UniverseRequest,
    *,
    research_now: datetime,
    lanes: ProviderLanes,
    policies: CurrentSourcePolicies,
    transports: CurrentSourceTransports | None = None,
) -> CurrentUniverseAcquisition:
    """Run the real current parsers and #110 without caller-filled membership.

    Current source rows do not yet prove membership effective/availability times.
    Their safe observations remain available for audit, while canonical expected
    counts remain unknown. Provider counts never become ordinary-A denominators.
    Historical requests preserve the current owners' explicit unsupported states.
    This partial path is not qualification of the unresolved membership fields.
    """
    prepared = prepare_universe_request(request, research_now=research_now)
    detached = UniverseRequest(prepared.screen_date, prepared.information_cutoff)
    sources = acquire_default_current_sources(
        detached,
        research_now=research_now,
        lanes=lanes,
        policies=policies,
        transports=transports,
    )
    typed_sources: tuple[
        SseListAcquisition | CurrentListAcquisition | BseCurrentListAcquisition, ...
    ] = sources
    owners = []
    for exchange, source in zip(("SSE", "SZSE", "BSE"), typed_sources, strict=True):
        attempted = bool(source.lane_result.attempts)
        source_result = source.source_result
        # Every observed current row lacks a proved as-of version. Retain these
        # source observations, but do not supply invented canonical list fields.
        outcome = ExchangeListOutcome(
            f"{exchange.lower()}_current_universe_list@1",
            exchange,
            prepared.screen_date,
            attempted,
            source_result,
            None,
            source.pages_expected,
            source.pages_received,
            (),
        )
        owners.append(_ListSnapshotAdapter(outcome))
    universe = build_as_of_a_share_universe(
        detached,
        research_now=research_now,
        exchange_list_adapters=tuple(owners),
        identity_adapters=(),
    )
    return CurrentUniverseAcquisition(universe, sources)
