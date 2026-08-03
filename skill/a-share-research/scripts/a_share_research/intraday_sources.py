"""Experimental TongdaXin and Tencent intraday source operations."""

from __future__ import annotations

import math
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable

from .close_sources import TencentDailyLineOperation
from .identity_sources import HttpTransport, SourceOperationError
from .intraday_contract import (
    IntradayObservation,
    IntradayQuery,
    IntradaySourceError,
)


def _source_error(operation: str, code: str, message: str) -> IntradaySourceError:
    return IntradaySourceError(operation, code, message)


def _row(frame: Any, index: int, operation: str) -> dict[str, object]:
    try:
        value = frame.iloc[index].to_dict()
    except (AttributeError, IndexError, KeyError, TypeError) as error:
        raise _source_error(
            operation,
            "unknown_schema",
            "The source response does not contain the required observation row.",
        ) from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _source_error(
            operation,
            "unknown_schema",
            "The source observation row does not match the expected schema.",
        )
    return value


def _text_decimal(value: object, operation: str, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int, float)):
        raise _source_error(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal value.",
        )
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidOperation
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise _source_error(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal value.",
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise _source_error(
            operation,
            "unknown_schema",
            f"The source {field} is not a nonnegative finite decimal value.",
        )
    return format(parsed.normalize(), "f")


def _price(value: object, operation: str, field: str) -> str:
    parsed = Decimal(_text_decimal(value, operation, field))
    if parsed <= 0:
        raise _source_error(
            operation,
            "unknown_schema",
            f"The source {field} is not a positive price.",
        )
    normalized = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if normalized <= 0:
        raise _source_error(
            operation,
            "unknown_schema",
            f"The source {field} is below the minimum CNY tick.",
        )
    return format(normalized, "f")


def _validate_ohlc(values: dict[str, str], operation: str) -> None:
    """Reject a source bar whose prices cannot describe one OHLC observation."""

    low = Decimal(values["low_price"])
    high = Decimal(values["high_price"])
    opening = Decimal(values["open_price"])
    latest = Decimal(values["latest_price"])
    if low > high or opening < low or opening > high or latest < low or latest > high:
        raise _source_error(
            operation,
            "inconsistent_price_bar",
            "The source OHLC values are internally inconsistent.",
        )


def _normalized_volume_shares(
    value: object,
    operation: str,
    *,
    row: dict[str, object],
) -> str:
    """Normalize TongdaXin's explicitly contract-defined hands to shares."""

    declared_unit = row.get("vol_unit", row.get("volume_unit"))
    if declared_unit is None or str(declared_unit).casefold() not in {
        "hand",
        "hands",
        "lot",
        "lots",
    }:
        raise _source_error(
            operation,
            "ambiguous_volume_unit",
            "The TongdaXin volume unit is missing or not contract-defined as hands.",
        )
    declared_scope = row.get("vol_scope", row.get("volume_scope"))
    if declared_scope is None or str(declared_scope).casefold() not in {
        "trading_day",
        "day",
        "session_cumulative",
    }:
        raise _source_error(
            operation,
            "ambiguous_volume_scope",
            "The TongdaXin volume is not identified as cumulative for this trading day.",
        )
    try:
        hands = Decimal(_text_decimal(value, operation, "vol"))
    except IntradaySourceError:
        raise
    if hands != hands.to_integral_value():
        raise _source_error(
            operation,
            "ambiguous_volume_unit",
            "The TongdaXin volume in hands is not a whole number.",
        )
    if hands == 0 and not _zero_value_is_explicit(row):
        raise _source_error(
            operation,
            "ambiguous_zero_value",
            "The TongdaXin zero volume is not explicitly confirmed as no-trade or suspended.",
        )
    shares = hands * Decimal(100)
    if shares != shares.to_integral_value():
        raise _source_error(
            operation,
            "ambiguous_volume_unit",
            "The normalized TongdaXin volume is not a whole number of shares.",
        )
    return format(shares.quantize(Decimal(1)), "f")


def _normalized_amount_cny(
    value: object,
    operation: str,
    *,
    row: dict[str, object],
) -> str:
    """Normalize a contract-defined cumulative amount to CNY."""

    declared_unit = row.get("amount_unit")
    if declared_unit is None or str(declared_unit).casefold() not in {
        "cny",
        "rmb",
        "yuan",
    }:
        raise _source_error(
            operation,
            "ambiguous_amount_unit",
            "The TongdaXin amount unit is missing or not contract-defined as CNY.",
        )
    declared_scope = row.get("amount_scope")
    if declared_scope is None or str(declared_scope).casefold() not in {
        "trading_day",
        "day",
        "session_cumulative",
    }:
        raise _source_error(
            operation,
            "ambiguous_amount_scope",
            "The TongdaXin amount is not identified as cumulative for this trading day.",
        )
    amount = _text_decimal(value, operation, "amount")
    if Decimal(amount) == 0 and not _zero_value_is_explicit(row):
        raise _source_error(
            operation,
            "ambiguous_zero_value",
            "The TongdaXin zero amount is not explicitly confirmed as no-trade or suspended.",
        )
    return amount


def _zero_value_is_explicit(row: dict[str, object]) -> bool:
    status = row.get("trading_status", row.get("status"))
    return isinstance(status, str) and status.casefold() in {
        "not_traded",
        "suspended",
        "no_trade",
    }


def _continuous_session(observed_at: datetime, operation: str) -> str:
    observed_time = observed_at.timetz().replace(tzinfo=None)
    if time(9, 30) <= observed_time <= time(11, 30) or time(
        13, 0
    ) <= observed_time < time(14, 57):
        return "continuous"
    raise _source_error(
        operation,
        "inapplicable_session",
        "The source observation is not from continuous auction trading.",
    )


class TongdaxinIntradayOperation:
    """Bind one mootdx quote to its same-client latest daily bar."""

    operation_id = "tongdaxin_intraday_snapshot@1"

    def __init__(self, client_factory: Callable[..., Any]) -> None:
        self._client_factory = client_factory

    def collect(self, query: IntradayQuery) -> IntradayObservation:
        client: Any | None = None
        try:
            client = self._client_factory(market="std")
            quote = _row(client.quotes(symbol=[query.code]), 0, self.operation_id)
            daily_bar = _row(
                client.bars(
                    symbol=query.code,
                    frequency=9,
                    start=0,
                    offset=1,
                ),
                -1,
                self.operation_id,
            )
        except IntradaySourceError:
            raise
        except Exception as error:
            raise _source_error(
                self.operation_id,
                "upstream_unavailable",
                "The TongdaXin source operation could not be completed.",
            ) from error
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        expected_market = 1 if query.exchange == "SSE" else 0
        if (
            quote.get("code") != query.code
            or quote.get("market") != expected_market
            or isinstance(quote.get("market"), bool)
        ):
            raise _source_error(
                self.operation_id,
                "wrong_security_payload",
                "The TongdaXin quote identifies a different security.",
            )
        bar_code = daily_bar.get("code")
        if bar_code is not None and str(bar_code) != query.code:
            raise _source_error(
                self.operation_id,
                "quote_daily_security_mismatch",
                "The TongdaXin daily bar identifies a different security.",
            )
        bar_market = daily_bar.get("market")
        if bar_market is not None and (
            isinstance(bar_market, bool) or bar_market != expected_market
        ):
            raise _source_error(
                self.operation_id,
                "quote_daily_security_mismatch",
                "The TongdaXin daily bar identifies a different market.",
            )
        try:
            trading_date = query.as_of.replace(
                year=int(str(daily_bar["year"])),
                month=int(str(daily_bar["month"])),
                day=int(str(daily_bar["day"])),
            )
            server_time = datetime.strptime(
                str(quote["servertime"]).split(".", 1)[0], "%H:%M:%S"
            ).time()
        except (KeyError, TypeError, ValueError) as error:
            raise _source_error(
                self.operation_id,
                "unknown_schema",
                "The TongdaXin quote time or latest daily-bar date is invalid.",
            ) from error
        if trading_date != query.as_of:
            raise _source_error(
                self.operation_id,
                "trading_date_mismatch",
                "The TongdaXin latest daily bar does not establish the requested date.",
            )
        quote_date = quote.get("trading_date", quote.get("date"))
        if quote_date is not None and str(quote_date) != trading_date.isoformat():
            raise _source_error(
                self.operation_id,
                "quote_daily_date_mismatch",
                "The TongdaXin quote and latest daily bar describe different dates.",
            )
        observed_at = datetime.combine(
            trading_date,
            server_time,
            tzinfo=query.retrieved_at.tzinfo,
        )
        session_state = _continuous_session(observed_at, self.operation_id)
        price_values = {
            field: _price(quote.get(source), self.operation_id, source)
            for field, source in {
                "latest_price": "price",
                "open_price": "open",
                "high_price": "high",
                "low_price": "low",
                "previous_close": "last_close",
            }.items()
        }
        _validate_ohlc(price_values, self.operation_id)
        volume_shares = _normalized_volume_shares(
            quote.get("vol"),
            self.operation_id,
            row=quote,
        )
        amount_cny = _normalized_amount_cny(
            quote.get("amount"),
            self.operation_id,
            row=quote,
        )
        trading_status = str(quote.get("trading_status", "traded"))
        quote_id = f"intraday-tdx-quote-{query.security}-{observed_at.isoformat()}"
        bar_id = f"intraday-tdx-date-{query.security}-{trading_date.isoformat()}"
        locator = f"mootdx://std/quote/{query.code}"
        quote_evidence = {
            "id": quote_id,
            "source_role": "market_observation",
            "source_operation": self.operation_id,
            "experimental": True,
            "subject": {"security": query.security},
            "observation": {
                "kind": "intraday_quote",
                "trading_date": trading_date.isoformat(),
                "session_state": session_state,
                "trading_status": trading_status,
                "price_type": "latest_traded",
                "date_basis_evidence_id": bar_id,
            },
            "observed_value": {
                "latest_price": price_values["latest_price"],
                "open": price_values["open_price"],
                "high": price_values["high_price"],
                "low": price_values["low_price"],
                "previous_close": price_values["previous_close"],
                "cumulative_volume": volume_shares,
                "cumulative_amount": amount_cny,
            },
            "unit": {
                "price": "CNY/share",
                "source_volume": "hands",
                "cumulative_volume": "shares",
                "cumulative_amount": "CNY",
            },
            "cumulative_scope": "trading_day",
            "evidence_time": observed_at.isoformat(),
            "available_at": None,
            "retrieved_at": query.retrieved_at.isoformat(),
            "locator": {"uri": locator},
            "limitations": ["experimental_source_operation"],
        }
        bar_evidence = {
            "id": bar_id,
            "source_role": "market_observation",
            "source_operation": self.operation_id,
            "experimental": True,
            "subject": {"security": query.security},
            "observation": {
                "kind": "latest_daily_bar_date_basis",
                "trading_date": trading_date.isoformat(),
                "quote_evidence_id": quote_id,
            },
            "evidence_time": None,
            "available_at": None,
            "retrieved_at": query.retrieved_at.isoformat(),
            "locator": {"uri": f"mootdx://std/daily-bar/{query.code}"},
            "limitations": [
                "experimental_source_operation",
                "security_identity_bound_to_explicit_symbol_request",
            ],
        }
        return IntradayObservation(
            source_operation=self.operation_id,
            security=query.security,
            trading_date=trading_date,
            observed_at=observed_at,
            retrieved_at=query.retrieved_at,
            session_state=session_state,
            trading_status=trading_status,
            price_type="latest_traded",
            latest_price=price_values["latest_price"],
            open_price=price_values["open_price"],
            high_price=price_values["high_price"],
            low_price=price_values["low_price"],
            previous_close=price_values["previous_close"],
            previous_close_basis="source_reported_unadjudicated",
            cumulative_volume_shares=volume_shares,
            cumulative_amount_cny=amount_cny,
            evidence=(quote_evidence, bar_evidence),
            field_sources={
                "latest_price": ("price",),
                "open": ("open",),
                "high": ("high",),
                "low": ("low",),
                "previous_close": ("last_close",),
                "cumulative_volume": ("vol",),
                "cumulative_amount": ("amount",),
            },
        )


class TencentIntradayOperation:
    """Cross-check intraday OHLC through Tencent's verified daily-line boundary."""

    operation_id = "tencent_intraday_snapshot@1"

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def collect(self, query: IntradayQuery) -> IntradayObservation:
        try:
            bars = TencentDailyLineOperation().observe(
                query.security, query.as_of, self._transport
            )
        except SourceOperationError as error:
            raise _source_error(self.operation_id, error.code, str(error)) from error
        current = next(
            (item for item in bars if item.trading_date == query.as_of), None
        )
        if current is None:
            raise _source_error(
                self.operation_id,
                "incomplete_observation",
                "Tencent did not establish the current intraday core-price observation.",
            )
        session_state = _continuous_session(current.evidence_time, self.operation_id)
        price_values = {
            "latest_price": _price(current.close_value, self.operation_id, "close"),
            "open_price": _price(current.open_value, self.operation_id, "open"),
            "high_price": _price(current.high_value, self.operation_id, "high"),
            "low_price": _price(current.low_value, self.operation_id, "low"),
        }
        _validate_ohlc(price_values, self.operation_id)
        if current.price_type == "intraday_last":
            price_type = "latest_traded"
        elif current.price_type == "indicative_auction":
            price_type = "indicative_auction"
        else:
            price_type = current.price_type
        evidence_id = (
            f"intraday-tencent-{query.security}-{current.evidence_time.isoformat()}"
        )
        evidence = {
            "id": evidence_id,
            "source_role": "market_observation",
            "source_operation": self.operation_id,
            "experimental": True,
            "subject": {"security": query.security},
            "observation": {
                "kind": "intraday_core_price_cross_check",
                "trading_date": query.as_of.isoformat(),
                "session_state": session_state,
                "trading_status": (
                    "auction" if price_type == "indicative_auction" else "traded"
                ),
                "price_type": price_type,
            },
            "observed_value": {
                "latest_price": price_values["latest_price"],
                "open": price_values["open_price"],
                "high": price_values["high_price"],
                "low": price_values["low_price"],
            },
            "unit": {"price": "CNY/share"},
            "evidence_time": current.evidence_time.isoformat(),
            "available_at": current.available_at.isoformat(),
            "retrieved_at": current.retrieved_at.isoformat(),
            "locator": {"uri": current.source_uri},
            "limitations": ["experimental_source_operation"],
        }
        return IntradayObservation(
            source_operation=self.operation_id,
            security=query.security,
            trading_date=query.as_of,
            observed_at=current.evidence_time,
            retrieved_at=current.retrieved_at,
            session_state=session_state,
            trading_status=(
                "auction" if price_type == "indicative_auction" else "traded"
            ),
            price_type=price_type,
            latest_price=price_values["latest_price"],
            open_price=price_values["open_price"],
            high_price=price_values["high_price"],
            low_price=price_values["low_price"],
            previous_close=None,
            previous_close_basis=None,
            evidence=(evidence,),
            field_sources={
                "latest_price": ("day.close", "qt.timestamp"),
                "open": ("day.open",),
                "high": ("day.high",),
                "low": ("day.low",),
            },
        )
