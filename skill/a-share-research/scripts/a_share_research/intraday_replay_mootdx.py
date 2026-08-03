"""Fail-closed candidate adapters for the optional mootdx replay source."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .intraday_replay_contract import (
    IntradayReplayQuery,
    IntradayReplaySourceBatch,
    IntradayReplaySourceError,
    IntradayReplaySourceRow,
)

MootdxClientFactory = Callable[..., Any]


@dataclass(frozen=True)
class MootdxReplayContract:
    """Explicit provider qualifiers required before a row is admissible."""

    timestamp_semantics: str | None = None
    timestamp_timezone: str | None = None
    price_adjustment: str | None = None
    price_unit: str | None = None
    price_scale: str | None = None
    price_precision: str = "0.01"
    price_minimum_tick: str | None = None
    volume_unit: str | None = None
    volume_lot_size: str | None = None
    amount_unit: str | None = None
    amount_scale: str | None = None
    session_contract: str | None = None
    coverage_bound: str | None = None
    closing_auction_semantics: str | None = None
    completed_calendar_basis: str | None = None


class MootdxIntradayReplayOperation:
    """Collect candidate minute rows while requiring an explicit source contract.

    The default registry intentionally supplies no qualifiers.  This keeps a raw
    mootdx response from being promoted merely because its columns look familiar;
    a reviewed contract can be injected by a qualification harness or a future
    release without changing the public ResearchTask seam.
    """

    operation_id = "mootdx_intraday_replay@1"
    _frequency = 8

    def __init__(
        self,
        client_factory: MootdxClientFactory,
        *,
        contract: MootdxReplayContract | None = None,
        page_size: int = 800,
        max_pages: int = 8,
    ) -> None:
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("mootdx replay pagination bounds must be positive")
        self._client_factory = client_factory
        self._contract = contract or MootdxReplayContract()
        self._page_size = page_size
        self._max_pages = max_pages

    def collect(self, query: IntradayReplayQuery) -> IntradayReplaySourceBatch:
        client: Any | None = None
        try:
            client = self._client_factory(market="std")
            raw_rows = self._collect_pages(client, query)
        except IntradayReplaySourceError:
            raise
        except Exception as error:
            raise IntradayReplaySourceError(
                self.operation_id,
                "upstream_unavailable",
                "The mootdx replay source could not be collected safely.",
            ) from error
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        normalized_rows: list[IntradayReplaySourceRow] = []
        observed_dates: set[date] = set()
        for index, raw_row in enumerate(raw_rows):
            row_date, normalized = self._normalize_row(raw_row, index, query)
            observed_dates.add(row_date)
            if row_date == query.replay_date:
                normalized_rows.append(normalized)
        if not normalized_rows:
            raise IntradayReplaySourceError(
                self.operation_id,
                "replay_date_not_observed",
                "The mootdx response did not contain the requested replay date.",
            )
        if self._contract.completed_calendar_basis != (
            "source_verified_completed_trading_dates"
        ):
            raise IntradayReplaySourceError(
                self.operation_id,
                "completed_trading_calendar_unverified",
                "The mootdx response did not establish a completed trading calendar.",
            )
        calendar = tuple(sorted(observed_dates))
        return IntradayReplaySourceBatch(
            operation_id=self.operation_id,
            contract_version="1.0",
            security=query.security,
            trading_date=query.replay_date,
            retrieved_at=query.retrieved_at,
            experimental=True,
            price_adjustment=self._metadata_value(
                raw_rows, ("price_adjustment",), self._contract.price_adjustment
            ),
            price_unit=self._metadata_value(
                raw_rows, ("price_unit",), self._contract.price_unit
            ),
            price_precision=self._contract.price_precision,
            volume_unit=self._metadata_value(
                raw_rows, ("volume_unit",), self._contract.volume_unit
            ),
            amount_unit=self._metadata_value(
                raw_rows, ("amount_unit",), self._contract.amount_unit
            ),
            rows=tuple(normalized_rows),
            completed_trading_dates=calendar,
            source_role="market_observation",
            timestamp_timezone=self._metadata_value(
                raw_rows,
                ("timestamp_timezone",),
                self._contract.timestamp_timezone,
            ),
            volume_lot_size=self._optional_metadata_value(
                raw_rows, ("volume_lot_size",), self._contract.volume_lot_size
            ),
            amount_precision="0.01",
            session_contract=self._metadata_value(
                raw_rows, ("session_contract",), self._contract.session_contract
            ),
            coverage_bound=self._metadata_value(
                raw_rows, ("coverage_bound",), self._contract.coverage_bound
            ),
            closing_auction_semantics=self._metadata_value(
                raw_rows,
                ("closing_auction_semantics",),
                self._contract.closing_auction_semantics,
            ),
            price_minimum_tick=self._optional_metadata_value(
                raw_rows, ("price_minimum_tick",), self._contract.price_minimum_tick
            ),
            completed_calendar_basis=self._contract.completed_calendar_basis,
        )

    def _collect_pages(
        self, client: Any, query: IntradayReplayQuery
    ) -> list[Mapping[str, object]]:
        collected: list[Mapping[str, object]] = []
        for page_number in range(self._max_pages):
            try:
                frame = client.bars(
                    symbol=query.code,
                    frequency=self._frequency,
                    start=0,
                    offset=page_number * self._page_size,
                )
                page = _records(frame, self.operation_id)
            except IntradayReplaySourceError:
                raise
            except Exception as error:
                raise IntradayReplaySourceError(
                    self.operation_id,
                    "upstream_unavailable",
                    "The mootdx replay page could not be collected safely.",
                ) from error
            if not page:
                if page_number == 0:
                    raise IntradayReplaySourceError(
                        self.operation_id,
                        "empty_response",
                        "The mootdx replay source returned no rows.",
                    )
                break
            collected.extend(page)
        if not collected:
            raise IntradayReplaySourceError(
                self.operation_id,
                "empty_response",
                "The mootdx replay source returned no rows.",
            )
        return collected

    def _normalize_row(
        self,
        raw: Mapping[str, object],
        index: int,
        query: IntradayReplayQuery,
    ) -> tuple[date, IntradayReplaySourceRow]:
        code = _first(raw, "security", "code", "symbol")
        if code is not None and str(code) != query.code:
            raise IntradayReplaySourceError(
                self.operation_id,
                "source_security_mismatch",
                "The mootdx replay row identifies a different security.",
            )
        original_timestamp, observed_at = _timestamp(raw, self.operation_id)
        if observed_at > query.research_boundary:
            raise IntradayReplaySourceError(
                self.operation_id,
                "source_row_after_research_boundary",
                "The mootdx replay row is later than the research boundary.",
            )
        timestamp_semantics = self._row_or_contract(
            raw, "timestamp_semantics", self._contract.timestamp_semantics
        )
        if timestamp_semantics not in {"interval_start", "interval_end"}:
            raise IntradayReplaySourceError(
                self.operation_id,
                "timestamp_semantics_unverified",
                "The mootdx timestamp interval meaning is not qualified.",
            )
        timestamp_timezone = self._row_or_contract(
            raw, "timestamp_timezone", self._contract.timestamp_timezone
        )
        if timestamp_timezone != "Asia/Shanghai":
            raise IntradayReplaySourceError(
                self.operation_id,
                "timestamp_timezone_unverified",
                "The mootdx timestamp timezone is not qualified as Asia/Shanghai.",
            )
        trading_phase = _string_field(
            raw, ("trading_phase", "phase"), self.operation_id, "trading_phase"
        )
        trade_state = _string_field(
            raw, ("trade_state",), self.operation_id, "trade_state"
        )
        price_adjustment = self._row_or_contract(
            raw, "price_adjustment", self._contract.price_adjustment
        )
        if price_adjustment != "unadjusted":
            raise IntradayReplaySourceError(
                self.operation_id,
                "unsupported_price_adjustment",
                "The mootdx replay price basis is not qualified as unadjusted.",
            )
        price_scale = self._row_or_contract(
            raw, "price_scale", self._contract.price_scale
        )
        if price_scale is None:
            raise IntradayReplaySourceError(
                self.operation_id,
                "price_scale_unverified",
                "The mootdx replay price scaling is not qualified.",
            )
        prices = {
            field: _scaled_price(
                _first_required(raw, names, self.operation_id, field),
                price_scale,
                self._contract.price_minimum_tick or self._contract.price_precision,
                self.operation_id,
                field,
            )
            for field, names in {
                "open": ("open_price", "open"),
                "high": ("high_price", "high"),
                "low": ("low_price", "low"),
                "close": ("close_price", "close"),
            }.items()
        }
        volume_unit = self._row_or_contract(
            raw, "volume_unit", self._contract.volume_unit
        )
        volume_lot_size = self._row_or_contract(
            raw, "volume_lot_size", self._contract.volume_lot_size
        )
        volume = _volume_shares(
            _first_required(raw, ("volume", "vol"), self.operation_id, "volume"),
            volume_unit,
            volume_lot_size,
            self.operation_id,
        )
        amount_unit = self._row_or_contract(
            raw, "amount_unit", self._contract.amount_unit
        )
        amount_scale = self._row_or_contract(
            raw, "amount_scale", self._contract.amount_scale or "1"
        )
        amount = _amount_cny(
            _first_required(raw, ("amount", "turnover"), self.operation_id, "amount"),
            amount_unit,
            amount_scale,
            self.operation_id,
        )
        closing_semantics = self._row_or_contract(
            raw,
            "closing_auction_semantics",
            self._contract.closing_auction_semantics,
        )
        if trading_phase in {"opening_auction", "closing_auction"}:
            if closing_semantics not in {
                "final_match_14:57_15:00",
                "subinterval_transactions",
            }:
                raise IntradayReplaySourceError(
                    self.operation_id,
                    "auction_semantics_unverified",
                    "The mootdx auction treatment is not qualified.",
                )
        evidence_locator = f"mootdx:bars:{query.code}:{index}"
        return observed_at.date(), IntradayReplaySourceRow(
            source_timestamp=original_timestamp,
            timestamp_semantics=timestamp_semantics,
            trading_phase=trading_phase,
            trade_state=trade_state,
            open_price=prices["open"],
            high_price=prices["high"],
            low_price=prices["low"],
            close_price=prices["close"],
            volume=volume,
            amount=amount,
            evidence_locator=evidence_locator,
            trading_date=observed_at.date(),
            price_adjustment=price_adjustment,
            auction_interval_start=_optional_timestamp_value(
                raw, "auction_interval_start", self.operation_id
            ),
            auction_interval_end=_optional_timestamp_value(
                raw, "auction_interval_end", self.operation_id
            ),
        )

    def _row_or_contract(
        self, raw: Mapping[str, object], name: str, configured: str | None
    ) -> str | None:
        value = raw.get(name)
        if value is None:
            return configured
        return str(value)

    def _metadata_value(
        self,
        rows: Sequence[Mapping[str, object]],
        names: tuple[str, ...],
        configured: str | None,
    ) -> str:
        if configured is not None:
            return configured
        values = {
            str(value)
            for row in rows
            for value in (_first(row, *names),)
            if value is not None
        }
        if len(values) == 1:
            return values.pop()
        raise IntradayReplaySourceError(
            self.operation_id,
            "source_contract_unverified",
            "The mootdx response did not establish a single qualified source contract.",
        )

    def _optional_metadata_value(
        self,
        rows: Sequence[Mapping[str, object]],
        names: tuple[str, ...],
        configured: str | None,
    ) -> str | None:
        if configured is not None:
            return configured
        values = {
            str(value)
            for row in rows
            for value in (_first(row, *names),)
            if value is not None
        }
        if len(values) > 1:
            raise IntradayReplaySourceError(
                self.operation_id,
                "source_contract_unverified",
                "The mootdx response did not establish a single qualified source contract.",
            )
        return next(iter(values), None)


def _records(frame: object, operation: str) -> list[Mapping[str, object]]:
    value: object = frame
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict("records")
        except Exception as error:
            raise IntradayReplaySourceError(
                operation,
                "unknown_schema",
                "The mootdx replay response cannot be converted to records.",
            ) from error
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            "The mootdx replay response is not a sequence of records.",
        )
    records: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise IntradayReplaySourceError(
                operation,
                "unknown_schema",
                "The mootdx replay response contains a non-object row.",
            )
        records.append(item)
    return records


def _first(raw: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def _first_required(
    raw: Mapping[str, object],
    names: tuple[str, ...],
    operation: str,
    field: str,
) -> object:
    value = _first(raw, *names)
    if value is None:
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay row is missing {field}.",
        )
    return value


def _optional_timestamp_value(
    raw: Mapping[str, object], name: str, operation: str
) -> str | datetime | None:
    value = raw.get(name)
    if value is None or isinstance(value, (str, datetime)):
        return value
    raise IntradayReplaySourceError(
        operation,
        "unknown_schema",
        f"The mootdx replay {name} is not a timestamp.",
    )


def _string_field(
    raw: Mapping[str, object],
    names: tuple[str, ...],
    operation: str,
    field: str,
) -> str:
    value = _first(raw, *names)
    if not isinstance(value, str) or not value:
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay row is missing {field}.",
        )
    return value


def _timestamp(
    raw: Mapping[str, object], operation: str
) -> tuple[str | datetime, datetime]:
    value = _first(raw, "source_timestamp", "timestamp", "datetime", "time")
    if isinstance(value, datetime):
        original: str | datetime = value
        parsed = value
    elif isinstance(value, str) and value:
        original = value
        try:
            parsed = datetime.fromisoformat(value.replace(" ", "T"))
        except ValueError as error:
            raise IntradayReplaySourceError(
                operation,
                "unknown_timestamp_schema",
                "The mootdx replay timestamp is not ISO date-time.",
            ) from error
    else:
        raise IntradayReplaySourceError(
            operation,
            "unknown_timestamp_schema",
            "The mootdx replay row is missing a timestamp.",
        )
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(hours=8):
        raise IntradayReplaySourceError(
            operation,
            "timestamp_timezone_unverified",
            "The mootdx replay timestamp does not carry an explicit +08:00 offset.",
        )
    return original, parsed


def _decimal(
    value: object, operation: str, field: str, *, nonnegative: bool = True
) -> Decimal:
    if isinstance(value, bool):
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay {field} is not a decimal value.",
        )
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay {field} is not a decimal value.",
        ) from error
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay {field} is outside the qualified range.",
        )
    return parsed


def _scaled_price(
    value: object,
    scale: str,
    tick: str,
    operation: str,
    field: str,
) -> str:
    raw = _decimal(value, operation, field, nonnegative=False)
    scale_value = _decimal(scale, operation, "price_scale")
    tick_value = _decimal(tick, operation, "price_minimum_tick")
    if scale_value <= 0 or tick_value <= 0 or raw <= 0:
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay {field} is not a positive price.",
        )
    try:
        return format(
            (raw * scale_value).quantize(tick_value, rounding=ROUND_HALF_UP), "f"
        )
    except InvalidOperation as error:
        raise IntradayReplaySourceError(
            operation,
            "unknown_schema",
            f"The mootdx replay {field} cannot be normalized to its tick.",
        ) from error


def _volume_shares(
    value: object,
    unit: str | None,
    lot_size: str | None,
    operation: str,
) -> str:
    parsed = _decimal(value, operation, "volume")
    if unit == "shares":
        shares = parsed
    elif unit == "hands" and lot_size is not None:
        shares = parsed * _decimal(lot_size, operation, "volume_lot_size")
    else:
        raise IntradayReplaySourceError(
            operation,
            "volume_unit_unverified",
            "The mootdx volume unit or lot size is not qualified.",
        )
    if shares != shares.to_integral_value():
        raise IntradayReplaySourceError(
            operation,
            "fractional_share_volume",
            "The mootdx volume cannot be normalized to whole shares.",
        )
    return format(shares.quantize(Decimal(1)), "f")


def _amount_cny(
    value: object,
    unit: str | None,
    scale: str | None,
    operation: str,
) -> str:
    if unit not in {"CNY", "CNY_thousand"} or scale is None:
        raise IntradayReplaySourceError(
            operation,
            "amount_unit_unverified",
            "The mootdx amount unit or scale is not qualified.",
        )
    parsed = _decimal(value, operation, "amount")
    scale_value = _decimal(scale, operation, "amount_scale")
    if unit == "CNY_thousand" and scale_value != Decimal("1000"):
        raise IntradayReplaySourceError(
            operation,
            "amount_unit_unverified",
            "The mootdx thousand-CNY amount scale is not qualified.",
        )
    scaled = parsed * scale_value
    return format(scaled.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
