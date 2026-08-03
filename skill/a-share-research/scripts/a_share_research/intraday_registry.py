"""Default experimental source operation selection for intraday snapshots."""

from __future__ import annotations

from .identity_sources import HttpTransport
from .intraday_contract import IntradaySourceOperation
from .intraday_sources import TencentIntradayOperation, TongdaxinIntradayOperation


def build_default_intraday_operations(
    transport: HttpTransport,
) -> tuple[IntradaySourceOperation, ...]:
    """Build the required TongdaXin baseline and Tencent cross-check pair."""

    from mootdx.quotes import Quotes  # type: ignore[import-not-found]

    return (
        TongdaxinIntradayOperation(Quotes.factory),
        TencentIntradayOperation(transport),
    )
