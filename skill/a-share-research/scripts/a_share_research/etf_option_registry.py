"""Default experimental Adapter registry for ETF-option research."""

from __future__ import annotations

from .etf_option_contract import OptionSourceOperation
from .etf_option_sources import SinaEtfOptionSnapshotOperation
from .identity_sources import HttpTransport


def build_default_etf_option_operations(
    transport: HttpTransport,
) -> tuple[OptionSourceOperation, ...]:
    """Build request-scoped ETF-option source operations."""

    return (SinaEtfOptionSnapshotOperation(transport),)
