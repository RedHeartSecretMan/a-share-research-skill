"""Fixed current three-exchange source acquisition, before as-of qualification.

This Module assembles existing real source parsers and the caller's shared lanes.
It returns their existing acquisition values, never a second Universe model or
an expected denominator made from unproved dates/counts. Historical provider
selection and qualified #110 list projection are separate, still-unclosed work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .bse_universe_source import (
    BseCurrentListAcquisition,
    BsePostTransport,
    acquire_bse_current_list,
)
from .identity_sources import HttpTransport, UrlLibTransport
from .provider_lane import ProviderLanePolicy, ProviderLanes
from .sse_universe_source import SseListAcquisition, acquire_sse_current_lists
from .szse_universe_source import CurrentListAcquisition, acquire_szse_current_list
from .universe_input import UniverseRequest, prepare_universe_request


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
