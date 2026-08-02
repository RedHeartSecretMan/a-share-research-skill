"""Default production Adapter registry for capital-event tasks."""

from __future__ import annotations

from .capital_contract import CapitalHttpTransport, CapitalSourceOperation
from .capital_flow_sources import (
    EastmoneyBoardFundFlowOperation,
    EastmoneyNorthboundFlowOperation,
    EastmoneyStockFundFlowOperation,
)
from .company_capital_sources import (
    EastmoneyDividendOperation,
    EastmoneyMarginTradingOperation,
    EastmoneyShareholderCountOperation,
)
from .trading_event_sources import EastmoneyTradingEventOperation


def build_default_capital_operations(
    transport: CapitalHttpTransport,
) -> tuple[CapitalSourceOperation, ...]:
    """Build request-scoped experimental operations behind the public task."""

    return (
        EastmoneyNorthboundFlowOperation(transport),
        EastmoneyStockFundFlowOperation(transport),
        EastmoneyBoardFundFlowOperation(transport),
        EastmoneyTradingEventOperation(transport),
        EastmoneyMarginTradingOperation(transport),
        EastmoneyShareholderCountOperation(transport),
        EastmoneyDividendOperation(transport),
    )
