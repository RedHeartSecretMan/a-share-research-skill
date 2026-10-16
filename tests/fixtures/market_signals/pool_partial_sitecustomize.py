"""Synthetic qualified pool rows with explicit source-loss coverage for CLI tests."""

import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))

from a_share_research import cli  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_signal_contract import (
    SignalCoverage,
    SignalSourceBatch,
    SignalSourceFailure,
)  # noqa: E402
from a_share_research.market_theme_sources import (
    ThsStrongStockThemeOperation,
    ThsMarketHeatOperation,
)  # noqa: E402
from a_share_research.source_throttle import SerialRequestGate  # noqa: E402
from a_share_research.research_runtime import research  # noqa: E402

CASE = json.loads(os.environ.get("ASR_POOL_CASE", "{}"))


class SyntheticTransport:
    def get(self, url, headers):
        heat = "stock_type" in url
        payload = (
            {
                "data": {
                    "stock_list": [
                        {
                            "order": i,
                            "code": code,
                            "name": "合成热度",
                            "tag": {},
                            "rate": "1",
                            "rise_and_fall": "2",
                            "hot_rank_chg": "1",
                        }
                        for i, code in enumerate(("300001", "300002"), 1)
                    ]
                }
            }
            if heat
            else None
        )
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=json.dumps(
                payload
                or {
                    "errocode": 0,
                    "data": [
                        {
                            "code": "300001",
                            "name": "合成证券甲",
                            "reason": "合成题材",
                            "market": "SZ",
                        },
                        {
                            "code": "300002",
                            "name": "合成证券乙",
                            "reason": "合成题材",
                            "market": "SZ",
                        },
                    ],
                }
            ).encode(),
            retrieved_at=datetime.fromisoformat("2026-08-02T10:00:00+08:00"),
        )


class SyntheticPool:
    operation_id = ThsStrongStockThemeOperation.operation_id
    supported_signal_types = ThsStrongStockThemeOperation.supported_signal_types
    raw_fields = ThsStrongStockThemeOperation.raw_fields

    def __init__(self, case):
        self.case = case
        self.adapter_type = (
            ThsMarketHeatOperation if case.get("heat") else ThsStrongStockThemeOperation
        )
        self.operation_id = self.adapter_type.operation_id
        self.supported_signal_types = self.adapter_type.supported_signal_types
        self.raw_fields = self.adapter_type.raw_fields

    def collect(self, query):
        adapter = self.adapter_type(
            SyntheticTransport(),
            request_gate=SerialRequestGate(
                minimum_interval_seconds=0, jitter_bounds=(0, 0), rate_limit_backoffs=()
            ),
        )
        batch = adapter.collect(replace(query, limit=100))
        count = self.case.get("retained", 0)
        errors = tuple(
            SignalSourceFailure(self.operation_id, code, "Synthetic source loss.")
            for code in self.case.get("errors", ["unknown_schema"])
        )
        observations = list(batch.observations[:count])
        if self.case.get("invalid_row") and observations:
            observations[-1] = replace(
                observations[-1], metrics={"invalid_metric": "1"}
            )
        signal_type = next(iter(self.supported_signal_types))
        return SignalSourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
            coverage={
                signal_type: SignalCoverage(
                    state=self.case.get("state", "partial"),
                    provider_total=self.case.get("total", 2),
                    pages_collected=self.case.get("pages", 1),
                    pages_expected=self.case.get("pages", 1),
                    details={"collected_count": self.case.get("collected", 2)}
                    if self.case.get("heat")
                    else {},
                )
            },
            source_errors=errors,
        )


def synthetic_research(request, **kwargs):
    kwargs["market_signal_operations"] = tuple(
        SyntheticPool(case) for case in CASE.get("owners", [CASE])
    )
    return research(request, **kwargs)


cli.research = synthetic_research
