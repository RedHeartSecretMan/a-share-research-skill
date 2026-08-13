"""Default candidate registry for the experimental intraday replay source."""

from __future__ import annotations

from .close_sources import (
    SseDailyLineOperation,
    SzseDailyLineOperation,
)
from .identity_sources import HttpTransport
from .intraday_replay_contract import (
    IntradayReplayDailySourceBatch,
    IntradayReplayDailySourceOperation,
    IntradayReplayQuery,
    IntradayReplaySourceError,
    IntradayReplaySourceOperation,
)
from .intraday_replay_mootdx import (
    MootdxClientFactory,
    MootdxIntradayReplayOperation,
    MootdxReplayContract,
)
from .research_result import FieldDisposition


def exchange_daily_field_dispositions(
    exchange: str,
    *,
    trading_status: str = "traded",
) -> tuple[FieldDisposition, ...]:
    """Return the selected exchange Adapter's actual response columns."""

    destinations = (
        {
            "date": "trading_date",
            "open": "open_price",
            "high": "high_price",
            "low": "low_price",
            "close": "close_price",
            "volume": "volume",
            "amount": "amount",
        }
        if exchange == "SSE"
        else {
            "row[0]": "trading_date",
            "row[1]": "open_price",
            "row[2]": "close_price",
            "row[3]": "low_price",
            "row[4]": "high_price",
            "row[7]": "volume",
            "row[8]": "amount",
        }
    )
    source_dispositions = (
        SseDailyLineOperation.field_dispositions
        if exchange == "SSE"
        else SzseDailyLineOperation.field_dispositions
    )
    price_fields = (
        {"open", "high", "low", "close"}
        if exchange == "SSE"
        else {"row[1]", "row[2]", "row[3]", "row[4]"}
    )
    return tuple(
        FieldDisposition(
            item.field,
            "excluded",
            None,
            "The suspended replay-date price is parsed for schema validity but is not retained in the daily-boundary contract.",
        )
        if trading_status == "suspended" and item.field in price_fields
        else FieldDisposition(
            item.field,
            "normalized",
            f"observations.payload.contract.{destinations[item.field]}",
        )
        if item.field in destinations
        else item
        for item in source_dispositions
    )


class ExchangeReplayDailyBoundaryOperation:
    """Use the exchange daily line as an independent boundary operation."""

    operation_id = "exchange_intraday_replay_daily@1"

    @staticmethod
    def field_dispositions(query: IntradayReplayQuery) -> tuple[FieldDisposition, ...]:
        """Expose the selected exchange Adapter's actual response columns."""

        return exchange_daily_field_dispositions(query.exchange)

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def collect(self, query: IntradayReplayQuery) -> IntradayReplayDailySourceBatch:
        operation = (
            SseDailyLineOperation()
            if query.exchange == "SSE"
            else SzseDailyLineOperation()
        )
        try:
            observations = operation.observe(query.security, self._transport)
        except Exception as error:
            if isinstance(error, IntradayReplaySourceError):
                raise
            raise IntradayReplaySourceError(
                self.operation_id,
                "daily_upstream_unavailable",
                "The independent exchange daily boundary was unavailable safely.",
            ) from error
        current = next(
            (item for item in observations if item.trading_date == query.replay_date),
            None,
        )
        if current is None:
            raise IntradayReplaySourceError(
                self.operation_id,
                "daily_replay_date_not_observed",
                "The independent exchange source did not observe the replay date.",
            )
        if current.trading_status != "suspended" and current.amount_cny is None:
            raise IntradayReplaySourceError(
                self.operation_id,
                "daily_amount_unavailable",
                "The independent exchange source does not expose a qualified CNY amount.",
            )
        previous = max(
            (item for item in observations if item.trading_date < query.replay_date),
            key=lambda item: item.trading_date,
            default=None,
        )
        return IntradayReplayDailySourceBatch(
            operation_id=self.operation_id,
            contract_version="1.0",
            security=query.security,
            trading_date=query.replay_date,
            retrieved_at=current.retrieved_at,
            experimental=True,
            price_adjustment=current.adjustment,
            price_unit="CNY/share",
            price_precision="0.01",
            volume_unit="shares",
            amount_unit="CNY",
            open_price=None
            if current.trading_status == "suspended"
            else current.open_value,
            high_price=None
            if current.trading_status == "suspended"
            else current.high_value,
            low_price=None
            if current.trading_status == "suspended"
            else current.low_value,
            close_price=None
            if current.trading_status == "suspended"
            else current.close_value,
            actual_close_price=(
                None if current.trading_status == "suspended" else current.close_value
            ),
            volume=current.volume_shares,
            amount=current.amount_cny,
            trading_status=current.trading_status,
            source_role="daily_boundary_cross_check",
            timestamp_timezone="Asia/Shanghai",
            evidence_locator="independent:exchange-daily",
            previous_trading_date=(
                previous.trading_date if previous is not None else None
            ),
            previous_close=previous.close_value if previous is not None else None,
            previous_close_basis=(
                "actual_unadjusted" if previous is not None else None
            ),
            available_at=current.available_at,
        )


def build_default_intraday_replay_operations(
    *,
    transport: HttpTransport,
    client_factory: MootdxClientFactory | None = None,
    contract: MootdxReplayContract | None = None,
) -> tuple[
    tuple[IntradayReplaySourceOperation, ...],
    tuple[IntradayReplayDailySourceOperation, ...],
]:
    """Build the candidate minute operation and separate daily operation."""

    if client_factory is None:
        from mootdx.quotes import Quotes  # type: ignore[import-not-found]

        client_factory = Quotes.factory
    return (
        (
            MootdxIntradayReplayOperation(
                client_factory,
                contract=contract,
            ),
        ),
        (ExchangeReplayDailyBoundaryOperation(transport),),
    )
