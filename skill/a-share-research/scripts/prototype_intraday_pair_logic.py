"""PROTOTYPE — pure adjudication for two experimental intraday observations.

Question: can one Tencent observation and one mootdx/TongdaXin observation form
the agreed research-grade snapshot without inventing a date, session, or source
agreement?  This module is intentionally small enough to discard or lift later.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Any


CORE_PRICE_FIELDS = ("price", "open", "high", "low", "previous_close")


def session_at(observed_at: datetime) -> str | None:
    value = observed_at.timetz().replace(tzinfo=None)
    if time(9, 15) <= value <= time(9, 25):
        return "opening_auction"
    if time(9, 30) <= value <= time(11, 30):
        return "continuous"
    if time(11, 30) < value < time(13, 0):
        return "midday_break"
    if time(13, 0) <= value < time(14, 57):
        return "continuous"
    if time(14, 57) <= value <= time(15, 0):
        return "closing_auction"
    return None


def adjudicate(
    tencent: dict[str, Any],
    tongdaxin: dict[str, Any],
    *,
    maximum_gap_seconds: int = 60,
) -> dict[str, Any]:
    conflicts: list[dict[str, Any]] = []
    if tencent["security"] != tongdaxin["security"]:
        conflicts.append({"code": "security_conflict"})
    if tencent["trading_date"] != tongdaxin["trading_date"]:
        conflicts.append({"code": "trading_date_conflict"})

    tencent_time = datetime.fromisoformat(tencent["observed_at"])
    tongdaxin_time = datetime.fromisoformat(tongdaxin["observed_at"])
    tencent_session = session_at(tencent_time)
    tongdaxin_session = session_at(tongdaxin_time)
    if tencent_session is None or tongdaxin_session is None:
        conflicts.append({"code": "inapplicable_session"})
    elif tencent_session != tongdaxin_session:
        conflicts.append({"code": "session_conflict"})

    gap_seconds = abs((tencent_time - tongdaxin_time).total_seconds())
    if gap_seconds > maximum_gap_seconds:
        conflicts.append(
            {
                "code": "incompatible_observation_times",
                "gap_seconds": gap_seconds,
            }
        )

    for field in CORE_PRICE_FIELDS:
        if Decimal(tencent[field]) != Decimal(tongdaxin[field]):
            conflicts.append(
                {
                    "code": "core_price_conflict",
                    "field": field,
                    "tencent": tencent[field],
                    "tongdaxin": tongdaxin[field],
                }
            )

    return {
        "status": "blocked" if conflicts else "limited",
        "session_state": tencent_session if not conflicts else None,
        "observation_gap_seconds": gap_seconds,
        "conflicts": conflicts,
        "snapshot": None
        if conflicts
        else {
            "security": tencent["security"],
            "trading_date": tencent["trading_date"],
            "price": tencent["price"],
            "open": tencent["open"],
            "high": tencent["high"],
            "low": tencent["low"],
            "previous_close": tencent["previous_close"],
            "volume_shares": tencent["volume_shares"],
            "amount_cny": tongdaxin["amount_cny"],
            "source_operations": [
                tencent["source_operation"],
                tongdaxin["source_operation"],
            ],
        },
    }
