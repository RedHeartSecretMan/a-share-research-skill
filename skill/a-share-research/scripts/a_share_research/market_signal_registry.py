"""Default experimental Adapter registry for market-signal research."""

from __future__ import annotations

from .market_limit_sources import (
    EastmoneyLimitStateOperation,
    ThsLimitReasonOperation,
)
from .market_monitoring_sources import (
    EastmoneyFocusMonitoringOperation,
    EastmoneySevereAbnormalMovementOperation,
)
from .market_signal_contract import (
    MarketSignalHttpTransport,
    MarketSignalSourceOperation,
)
from .market_theme_sources import (
    EastmoneyIndustryRotationOperation,
    EastmoneySecurityBoardMembershipOperation,
    ThsMarketHeatOperation,
    ThsStrongStockThemeOperation,
)


def build_default_market_signal_operations(
    transport: MarketSignalHttpTransport,
) -> tuple[MarketSignalSourceOperation, ...]:
    """Build request-scoped source operations behind ``market_signals``."""

    return (
        ThsStrongStockThemeOperation(transport),
        EastmoneySecurityBoardMembershipOperation(transport),
        EastmoneyIndustryRotationOperation(transport),
        EastmoneyLimitStateOperation(transport),
        ThsLimitReasonOperation(transport),
        EastmoneyFocusMonitoringOperation(transport),
        EastmoneySevereAbnormalMovementOperation(transport),
        ThsMarketHeatOperation(transport),
    )
