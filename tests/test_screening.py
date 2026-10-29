from __future__ import annotations

import unittest
from decimal import Decimal

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.a_share_universe import (  # noqa: E402
    AShareUniverse,
    UniversePartition,
    UniverseSecurity,
)
from a_share_research.screening import (  # noqa: E402
    FACTOR_DEFINITIONS,
    SCREENING_PLAN_ID,
    screen_a_share_universe,
)


def _security(
    exchange: str, code: str, *, category: str = "A_SHARE"
) -> UniverseSecurity:
    return UniverseSecurity(
        exchange=exchange,
        code=code,
        name=f"fixture-{code}",
        security_category=category,
        listing_state="listed",
        effective_from="2020-01-01",
        effective_to=None,
        universe_state="expected_universe",
        unranked_reasons=(),
        list_operation_id=f"{exchange.lower()}_list@1",
        list_provenance_uri=f"fixture:list:{exchange}:{code}",
        list_effective_at="2020-01-01T00:00:00+08:00",
        list_available_at="2026-08-20T15:00:00+08:00",
        list_retrieved_at="2026-08-21T10:00:00+08:00",
        identity_operation_id=f"{exchange.lower()}_identity@1",
        identity_provenance_uri=f"fixture:identity:{exchange}:{code}",
        identity_observed_at="2026-08-20T15:00:00+08:00",
        identity_available_at="2026-08-20T15:00:00+08:00",
        identity_retrieved_at="2026-08-21T10:00:00+08:00",
    )


def _universe(*securities: UniverseSecurity) -> AShareUniverse:
    partitions = []
    for exchange in ("SSE", "SZSE", "BSE"):
        rows = tuple(item for item in securities if item.exchange == exchange)
        partitions.append(
            UniversePartition(
                exchange=exchange,
                complete=True,
                attempted=True,
                source_result="complete",
                operation_id=f"{exchange.lower()}_identity@1",
                expected=len(rows),
                resolved=len(rows),
                failed=0,
                securities=rows,
                limitation_codes=(),
                list_outcome=None,
                identity_outcome=None,
            )
        )
    return AShareUniverse(
        screen_date="2026-08-20",
        information_cutoff="2026-08-20T23:59:59+08:00",
        full_a_share_universe=True,
        partitions=tuple(partitions),
    )


def _factor(
    canonical: str, pe: str, pb: str, profit: str = "10", equity: str = "100"
) -> dict[str, object]:
    return {
        "canonical_security_id": canonical,
        "factor_inputs": {
            "pe_ttm": {"value": pe, "unit": "ratio"},
            "pb_mrq": {"value": pb, "unit": "ratio"},
            "parent_profit_ttm": {"value": profit, "unit": "CNY"},
            "current_parent_equity_mrq": {"value": equity, "unit": "CNY"},
            "prior_year_parent_equity_mrq": {"value": equity, "unit": "CNY"},
            "turnover_amount_20_sessions": {
                "unit": "CNY",
                "sessions": [
                    {"value": str(i), "session_date": f"2026-07-{i:02d}"}
                    for i in range(1, 21)
                ],
            },
        },
    }


class ScreeningTests(unittest.TestCase):
    def test_plan_and_factors_are_frozen(self) -> None:
        self.assertEqual(SCREENING_PLAN_ID, "fundamental_research_priority@1.0")
        self.assertEqual(
            tuple(FACTOR_DEFINITIONS),
            (
                "earnings_yield_ttm",
                "book_to_price_mrq",
                "return_on_equity_ttm",
                "median_turnover_amount_20_sessions",
            ),
        )
        self.assertEqual(
            FACTOR_DEFINITIONS["earnings_yield_ttm"]["weight"], Decimal("0.35")
        )

    def test_deterministic_decimal_ranking_and_ties(self) -> None:
        universe = _universe(_security("SSE", "600001"), _security("SZSE", "000001"))
        rows = [
            _factor("SSE:600001", "10", "2"),
            _factor("SZSE:000001", "20", "4"),
        ]
        first = screen_a_share_universe(universe, rows, requested_as_of="2026-08-21")
        second = screen_a_share_universe(
            universe, list(reversed(rows)), requested_as_of="2026-08-21"
        )
        self.assertEqual(first.to_public_dict(), second.to_public_dict())
        ranking = first.to_public_dict()["partitions"]["SSE"]["ranking"]
        self.assertEqual(ranking[0]["canonical_security_id"], "SSE:600001")
        self.assertEqual(ranking[0]["research_priority_score"], "80.000000")
        self.assertEqual(first.to_public_dict()["screen_date"], "2026-08-20")
        self.assertEqual(
            first.to_public_dict()["information_cutoff"], "2026-08-20T23:59:59+08:00"
        )

    def test_missing_and_non_positive_inputs_are_unranked_with_stable_reasons(
        self,
    ) -> None:
        universe = _universe(_security("SSE", "600001"), _security("SZSE", "000001"))
        bad = _factor("SSE:600001", "0", "-1")
        bad["factor_inputs"] = {
            "pe_ttm": bad["factor_inputs"]["pe_ttm"],
            "pb_mrq": {"value": "-1", "unit": "ratio"},
        }
        result = screen_a_share_universe(universe, [bad])
        row = result.to_public_dict()["partitions"]["SSE"]["unranked"][0]
        self.assertEqual(
            row["unranked_reasons"],
            [
                "non_positive_pe_ttm",
                "non_positive_pb_mrq",
                "missing_profit_ttm",
                "missing_parent_equity",
                "missing_turnover",
                "insufficient_trading_sessions",
            ],
        )

    def test_asset_classes_are_explicitly_excluded_and_ledger_reconciles(self) -> None:
        universe = _universe(
            _security("SSE", "600001"), _security("SSE", "510050", category="ETF")
        )
        result = screen_a_share_universe(universe, [_factor("SSE:600001", "10", "2")])
        partition = result.to_public_dict()["partitions"]["SSE"]
        self.assertEqual(
            partition["coverage"],
            {
                "expected": 2,
                "excluded": 1,
                "resolved": 2,
                "acquired": 1,
                "ranked": 1,
                "unranked": 0,
                "failed": 0,
                "unsupported": 0,
            },
        )
        self.assertEqual(partition["excluded"][0]["reason"], "asset_class_excluded")

    def test_cutoff_conflict_keeps_row_unranked_without_reweighting(self) -> None:
        universe = _universe(_security("SSE", "600001"), _security("SZSE", "000001"))
        late = _factor("SSE:600001", "10", "2")
        late["information_cutoff"] = "2026-08-21T00:00:00+08:00"
        result = screen_a_share_universe(
            universe, [late, _factor("SZSE:000001", "20", "4")]
        )
        partition = result.to_public_dict()["partitions"]["SSE"]
        self.assertEqual(partition["coverage"]["ranked"], 0)
        self.assertIn("scope_conflict", partition["unranked"][0]["unranked_reasons"])


if __name__ == "__main__":
    unittest.main()
