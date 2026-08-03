"""Experimental TongdaXin and Tencent intraday source operations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .close_sources import TencentDailyLineOperation
from .identity_sources import HttpTransport, SourceOperationError
from .intraday_contract import (
    IntradayObservation,
    IntradayQuery,
    IntradaySourceError,
    session_at,
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
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
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
    return format(parsed.quantize(Decimal("0.01")), "f")


def _session_state(observed_at: datetime, operation: str) -> str:
    state = session_at(observed_at)
    if state is not None:
        return state
    raise _source_error(
        operation,
        "inapplicable_session",
        "The source observation is outside an applicable trading session.",
    )


def _price_type(
    source_value: object,
    session_state: str,
    operation: str,
) -> str:
    expected = (
        "indicative_auction"
        if session_state in {"opening_auction", "closing_auction"}
        else "latest_traded"
    )
    if source_value is None:
        if expected == "latest_traded":
            return expected
        raise _source_error(
            operation,
            "unknown_price_type",
            "The auction source observation does not identify an indicative price.",
        )
    value = str(source_value)
    if value != expected:
        raise _source_error(
            operation,
            "incompatible_price_type",
            "The source price type is incompatible with its trading session.",
        )
    return value


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
        if quote.get("code") != query.code or quote.get("market") != expected_market:
            raise _source_error(
                self.operation_id,
                "wrong_security_payload",
                "The TongdaXin quote identifies a different security.",
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
        observed_at = datetime.combine(
            trading_date,
            server_time,
            tzinfo=query.retrieved_at.tzinfo,
        )
        session_state = _session_state(observed_at, self.operation_id)
        price_type = _price_type(
            quote.get("price_type"), session_state, self.operation_id
        )
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
        volume = Decimal(_text_decimal(quote.get("vol"), self.operation_id, "vol"))
        volume_shares = format(volume * Decimal(100), "f")
        amount_cny = _text_decimal(quote.get("amount"), self.operation_id, "amount")
        if "cache_state" not in quote:
            raise _source_error(
                self.operation_id,
                "unknown_cache_state",
                "The TongdaXin quote does not establish its cache state.",
            )
        cache_state_value = quote.get("cache_state")
        cache_state = None if cache_state_value is None else str(cache_state_value)
        trading_status = "auction" if price_type == "indicative_auction" else "traded"
        observation_boundary = (
            "morning_last_compatible"
            if session_at(query.retrieved_at) == "midday_break"
            else "current_session"
        )
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
                "price_type": price_type,
                "cache_state": cache_state,
                "observation_boundary": observation_boundary,
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
                "cumulative_volume": "shares",
                "cumulative_amount": "CNY",
            },
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
            price_type=price_type,
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
            cache_state=cache_state,
            observation_boundary=observation_boundary,
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
        session_state = _session_state(current.evidence_time, self.operation_id)
        price_type = _price_type(
            current.price_type if current.price_type == "indicative_auction" else None,
            session_state,
            self.operation_id,
        )
        trading_status = "auction" if price_type == "indicative_auction" else "traded"
        observation_boundary = (
            "morning_last_compatible"
            if session_at(query.retrieved_at) == "midday_break"
            else "current_session"
        )
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
                "trading_status": trading_status,
                "price_type": price_type,
                "observation_boundary": observation_boundary,
            },
            "observed_value": {
                "latest_price": current.close_value,
                "open": current.open_value,
                "high": current.high_value,
                "low": current.low_value,
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
            trading_status=trading_status,
            price_type=price_type,
            latest_price=current.close_value,
            open_price=current.open_value,
            high_price=current.high_value,
            low_price=current.low_value,
            previous_close=None,
            previous_close_basis=None,
            evidence=(evidence,),
            field_sources={
                "latest_price": ("day.close", "qt.timestamp"),
                "open": ("day.open",),
                "high": ("day.high",),
                "low": ("day.low",),
            },
            cache_state=current.availability_status,
            observation_boundary=observation_boundary,
        )
