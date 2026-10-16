"""Synthetic complete limit pools for copied-installed selected-lineage tests."""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))

from a_share_research import cli  # noqa: E402
from a_share_research.market_signal_contract import (  # noqa: E402
    MarketSignalObservation,
    SignalCoverage,
    SignalSourceBatch,
)
from a_share_research.research_runtime import research  # noqa: E402


def signal(owner, code, state, day="2026-07-31"):
    return MarketSignalObservation(
        signal_type="limit_state",
        source_operation=owner,
        source_role="market_signal",
        subject=None,
        source_document_id=f"{state}-{code}-{day}",
        observed_on=day,
        observed_at=None,
        available_at="2026-08-02T10:00:00+08:00",
        retrieved_at=datetime.fromisoformat("2026-08-02T10:00:00+08:00"),
        period={"start": day, "end": day, "frequency": "trading_day"},
        metrics={"consecutive_limit_days": "3" if state == "limit_up" else None},
        units={"consecutive_limit_days": "count"},
        directions={"consecutive_limit_days": "not_directional"},
        rule=None,
        attributions=(),
        dimensions={
            "market_scope": "mainland_a_share",
            "provider_security_code": code,
            "provider_security_name": "合成证券",
            "pool_state": state,
        },
        locator_uri="https://example.test/synthetic/" + code,
        limitations=("security_exchange_unverified",),
    )


class Pools:
    supported_signal_types = frozenset({"limit_state"})
    raw_fields = ("data.info[]", "code", "name", "reason_type")

    def __init__(self, owner, rows):
        self.operation_id = owner
        self.rows = rows

    def collect(self, query):
        return SignalSourceBatch(
            operation_id=self.operation_id,
            observations=tuple(self.rows),
            coverage={
                "limit_state": SignalCoverage(
                    state="observed_nonempty",
                    provider_total=len(self.rows),
                    pages_collected=3,
                    pages_expected=3,
                    details={"pool_states": ["limit_up", "limit_break", "limit_down"]},
                )
            },
        )


def synthetic_research(request, **kwargs):
    owner = "a_synthetic_pools@1"
    rows = [
        signal(owner, "300001", "limit_up"),
        signal(owner, "300002", "limit_break"),
        signal(owner, "300003", "limit_down"),
        signal(owner, "300004", "limit_up"),
    ]
    if os.environ.get("ASR96_SENTIMENT_WINDOW") == "1":
        rows.append(signal(owner, "300005", "limit_down", "2026-07-30"))
    if os.environ.get("ASR96_SENTIMENT_SIBLINGS") == "1":
        rows.append(signal(owner, "300006", "previous_limit_up"))
    operations = [Pools(owner, rows)]
    if os.environ.get("ASR96_SENTIMENT_SIBLINGS") == "1":
        operations.append(
            Pools(
                "z_synthetic_pools@1",
                [
                    signal("z_synthetic_pools@1", "600001", "limit_up"),
                ],
            )
        )
    kwargs["market_signal_operations"] = operations
    return research(request, **kwargs)


cli.research = synthetic_research
