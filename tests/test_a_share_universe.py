from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.a_share_universe import (  # noqa: E402
    build_as_of_a_share_universe,
)
from a_share_research.universe_input import (  # noqa: E402
    ExchangeListOutcome,
    ExchangeListRecord,
    IdentityBatchOutcome,
    UniverseIdentity,
    UniverseRequest,
)

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _list_record(exchange: str, code: str, name: str) -> ExchangeListRecord:
    return ExchangeListRecord(
        exchange=exchange,
        code=code,
        name=name,
        security_category="A_SHARE",
        effective_at="2020-01-01",
        available_at="2026-08-20T16:00:00+08:00",
        retrieved_at="2026-08-21T10:00:00+08:00",
        provenance_uri=f"fixture-list:{exchange}:{code}",
    )


def _identity(exchange: str, code: str, name: str) -> UniverseIdentity:
    return UniverseIdentity(
        exchange=exchange,
        code=code,
        name=name,
        security_category="A_SHARE",
        listing_state="listed",
        effective_from="2020-01-01",
        effective_to=None,
        observed_at="2026-08-20T15:00:00+08:00",
        available_at="2026-08-20T16:00:00+08:00",
        retrieved_at="2026-08-21T10:00:00+08:00",
        provenance_uri=f"fixture-identity:{exchange}:{code}",
    )


@dataclass(frozen=True)
class _ListAdapter:
    exchange: str
    operation_id: str
    outcome: ExchangeListOutcome

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        return self.outcome


@dataclass(frozen=True)
class _IdentityAdapter:
    exchange: str
    operation_id: str
    outcome: IdentityBatchOutcome

    def resolve(
        self,
        request: object,
        listings: tuple[object, ...],
        timeout_seconds: float,
    ) -> IdentityBatchOutcome:
        return self.outcome


class AShareUniverseInterfaceTests(unittest.TestCase):
    def test_three_exchange_projection_closes_with_first_class_bse_identity(
        self,
    ) -> None:
        rows = {
            "SSE": _list_record("SSE", "600519", "贵州茅台"),
            "SZSE": _list_record("SZSE", "000001", "平安银行"),
            "BSE": _list_record("BSE", "920000", "北交样本"),
        }
        list_adapters = tuple(
            _ListAdapter(
                exchange=exchange,
                operation_id=f"fixture_{exchange.lower()}_list@1",
                outcome=ExchangeListOutcome(
                    operation_id=f"fixture_{exchange.lower()}_list@1",
                    exchange=exchange,
                    screen_date="2026-08-20",
                    attempted=True,
                    source_result="complete",
                    expected_count=1,
                    pages_expected=1,
                    pages_received=1,
                    records=[row],
                ),
            )
            for exchange, row in rows.items()
        )
        identity_adapters = tuple(
            _IdentityAdapter(
                exchange=exchange,
                operation_id=f"fixture_{exchange.lower()}_identity@1",
                outcome=IdentityBatchOutcome(
                    operation_id=f"fixture_{exchange.lower()}_identity@1",
                    exchange=exchange,
                    attempted=True,
                    source_result="complete",
                    expected_count=1,
                    records=[_identity(exchange, row.code, row.name)],
                ),
            )
            for exchange, row in rows.items()
        )

        result = build_as_of_a_share_universe(
            UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
            research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
            exchange_list_adapters=list_adapters,
            identity_adapters=identity_adapters,
        ).to_public_dict()

        self.assertTrue(result["full_a_share_universe"])
        self.assertEqual(list(result["partitions"]), ["SSE", "SZSE", "BSE"])
        self.assertEqual(
            result["partitions"]["BSE"]["securities"][0][
                "canonical_security_id"
            ],
            "BSE:920000",
        )
        for exchange in ("SSE", "SZSE", "BSE"):
            with self.subTest(exchange=exchange):
                self.assertEqual(
                    result["partitions"][exchange]["reconciliation"],
                    {"expected": 1, "resolved": 1, "failed": 0},
                )


if __name__ == "__main__":
    unittest.main()
