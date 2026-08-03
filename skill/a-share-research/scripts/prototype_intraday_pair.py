#!/usr/bin/env python3
"""PROTOTYPE — inspect one Tencent + TongdaXin intraday evidence pair.

Run in one command:
UV_CACHE_DIR=/private/tmp/a-share-prototype-uv-cache uv run --python 3.12 \
  --with mootdx==0.11.7 python skill/a-share-research/scripts/prototype_intraday_pair.py

Press ``f`` to fetch and adjudicate SZSE:300058, or ``q`` to quit.  The same
command accepts ``--once`` for a single non-interactive observation.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from a_share_research.close_sources import TencentDailyLineOperation
from a_share_research.identity_sources import UrlLibTransport
from prototype_intraday_pair_logic import adjudicate


CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def _text(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _a_share_price(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def fetch_pair(security: str) -> dict[str, Any]:
    exchange, code = security.split(":", 1)
    if exchange not in {"SSE", "SZSE"}:
        raise ValueError("prototype supports one canonical SSE/SZSE A-share")

    tencent_bars = TencentDailyLineOperation().observe(
        security,
        datetime.now(CHINA_STANDARD_TIME).date(),
        UrlLibTransport(),
    )
    tencent_bar = tencent_bars[-1]
    previous_tencent_bar = max(
        (
            item
            for item in tencent_bars
            if item.trading_date < tencent_bar.trading_date
        ),
        key=lambda item: item.trading_date,
    )
    tencent_raw = asdict(tencent_bar)

    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")
    try:
        quote = client.quotes(symbol=[code]).iloc[0].to_dict()
        bar = client.bars(symbol=code, frequency=9, start=0, offset=1).iloc[-1]
    finally:
        client.close()

    trading_date = date(
        int(bar["year"]),
        int(bar["month"]),
        int(bar["day"]),
    )
    tongdaxin_time = datetime.combine(
        trading_date,
        datetime.strptime(str(quote["servertime"]).split(".", 1)[0], "%H:%M:%S").time(),
        tzinfo=CHINA_STANDARD_TIME,
    )

    tencent = {
        "source_operation": "tencent_intraday_candidate@prototype",
        "security": security,
        "trading_date": tencent_raw["trading_date"].isoformat(),
        "observed_at": tencent_raw["evidence_time"].isoformat(),
        "price": tencent_raw["close_value"],
        "open": tencent_raw["open_value"],
        "high": tencent_raw["high_value"],
        "low": tencent_raw["low_value"],
        "previous_close": previous_tencent_bar.close_value,
        "volume_shares": tencent_raw["volume_shares"],
    }
    tongdaxin = {
        "source_operation": "mootdx_intraday_candidate@prototype",
        "security": security,
        "trading_date": trading_date.isoformat(),
        "observed_at": tongdaxin_time.isoformat(),
        "price": _a_share_price(quote["price"]),
        "open": _a_share_price(quote["open"]),
        "high": _a_share_price(quote["high"]),
        "low": _a_share_price(quote["low"]),
        "previous_close": _a_share_price(quote["last_close"]),
        "volume_shares": _text(Decimal(str(quote["vol"])) * 100),
        "amount_cny": _text(quote["amount"]),
        "date_basis": "latest_daily_bar",
    }
    return {
        "question": (
            "Can Tencent and mootdx form one research-grade intraday snapshot "
            "without inventing identity, date, session, timing, or price agreement?"
        ),
        "tencent": tencent,
        "tongdaxin": tongdaxin,
        "adjudication": adjudicate(tencent, tongdaxin),
    }


def render(state: dict[str, Any]) -> None:
    print("\033[2J\033[H", end="")
    print("\033[1mPROTOTYPE — intraday source-pair adjudication\033[0m")
    print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
    print("\n\033[1m[f]\033[0m fetch  \033[1m[q]\033[0m quit")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--security", default="SZSE:300058")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    state: dict[str, Any] = {"status": "not_observed", "security": args.security}
    if args.once:
        print(json.dumps(fetch_pair(args.security), ensure_ascii=False, indent=2))
        return

    while True:
        render(state)
        action = input("> ").strip().lower()
        if action == "q":
            return
        if action == "f":
            try:
                state = fetch_pair(args.security)
            except Exception as error:  # prototype: keep failure visible
                state = {
                    "status": "fetch_failed",
                    "error_type": type(error).__name__,
                    "message": str(error),
                }


if __name__ == "__main__":
    main()
