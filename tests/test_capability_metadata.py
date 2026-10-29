from __future__ import annotations

import unittest

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capability_metadata import (  # noqa: E402
    CAPABILITY_METADATA,
    CapabilityMetadata,
    get_capability_metadata,
    validate_capability_metadata,
    validate_capability_metadata_registry,
)
from a_share_research.workflow_contracts import (  # noqa: E402
    REGISTERED_WORKFLOW_VERSIONS,
)


class CapabilityMetadataTests(unittest.TestCase):
    def test_registry_covers_public_tasks_and_workflows(self) -> None:
        expected = {
            "security_identity",
            "market_trend",
            "security_valuation",
            "valuation_compare",
            "research_content",
            "capital_events",
            "market_signals",
            "etf_market",
            "etf_options",
            "intraday_market_signal",
            "intraday_replay",
            "research_workflow",
            "single_security_research",
            "single_security_valuation",
            "valuation_comparison",
            "theme_report_research",
            "new_security_research",
            "market_overview_research",
            "market_screen_research",
        }
        self.assertTrue(expected.issubset(CAPABILITY_METADATA))
        for capability_id in expected:
            item = get_capability_metadata(capability_id)
            self.assertIsInstance(item, CapabilityMetadata)
            self.assertEqual(item.id, capability_id)
            validate_capability_metadata(item)

    def test_scope_and_state_distinguish_policy_and_deferred_rows(self) -> None:
        self.assertEqual(
            get_capability_metadata("market_signals").scope, "cross_cutting"
        )
        self.assertEqual(
            get_capability_metadata("market_signals_subject_scoped").scope,
            "subject_scoped",
        )
        self.assertEqual(
            get_capability_metadata("market_signals_market_wide").scope,
            "market_wide",
        )
        for scoped_id in (
            "market_signals_subject_scoped",
            "market_signals_market_wide",
            "capital_events_subject_scoped",
            "capital_events_market_wide",
        ):
            self.assertNotEqual(
                get_capability_metadata(scoped_id).capability_kind, "public"
            )
        self.assertEqual(
            get_capability_metadata("capital_events_subject_scoped").scope,
            "subject_scoped",
        )
        self.assertEqual(
            get_capability_metadata("capital_events_market_wide").scope,
            "market_wide",
        )

    def test_etf_capability_is_separate_from_stock_screening_asset_class(self) -> None:
        etf = get_capability_metadata("etf_market")
        screening = get_capability_metadata("market_screen_research")
        self.assertEqual(etf.asset_class, "etf")
        self.assertEqual(screening.asset_class, "a_share_equity")
        self.assertNotEqual(etf.asset_class, screening.asset_class)
        market_policy = get_capability_metadata("whole_market_turnover")
        self.assertEqual(market_policy.scope, "market_wide")
        self.assertEqual(market_policy.capability_kind, "policy")
        self.assertEqual(market_policy.public_state, "blocked")

        deferred = get_capability_metadata("bond_research")
        self.assertEqual(deferred.asset_class, "bond")
        self.assertEqual(deferred.capability_kind, "deferred")
        self.assertEqual(deferred.public_state, "deferred")

    def test_metadata_is_json_ready_and_registry_is_unique(self) -> None:
        validate_capability_metadata_registry()
        for workflow_id, version in REGISTERED_WORKFLOW_VERSIONS.items():
            self.assertIn(workflow_id, CAPABILITY_METADATA)
            self.assertEqual(CAPABILITY_METADATA[workflow_id].version, version)
            self.assertEqual(CAPABILITY_METADATA[workflow_id].capability_kind, "public")
        self.assertEqual(
            set(CAPABILITY_METADATA),
            {
                "board_fund_flow",
                "bond_research",
                "capital_events",
                "capital_events_market_wide",
                "capital_events_subject_scoped",
                "etf_market",
                "etf_options",
                "focus_monitoring",
                "fundamental_research_priority",
                "fund_research",
                "futures_research",
                "industry_board_breadth",
                "industry_theme_rotation",
                "intraday_market_signal",
                "intraday_replay",
                "limit_pools",
                "major_broad_indices",
                "market_dragon_tiger",
                "market_flash",
                "market_foundation",
                "market_heat",
                "market_overview_research",
                "market_screen_research",
                "market_signals",
                "market_signals_market_wide",
                "market_signals_subject_scoped",
                "market_trading_session",
                "market_trend",
                "market_wide_intraday",
                "new_security_research",
                "northbound_disclosure",
                "open_ended_screening",
                "research_content",
                "research_workflow",
                "result_artifact",
                "security_identity",
                "security_valuation",
                "severe_abnormal_movement",
                "single_security_research",
                "single_security_valuation",
                "stock_option_research",
                "theme_report_research",
                "valuation_compare",
                "valuation_comparison",
                "warrant_research",
                "whole_market_margin",
                "whole_market_turnover",
            },
        )
        ids = list(CAPABILITY_METADATA)
        self.assertEqual(len(ids), len(set(ids)))
        for item in CAPABILITY_METADATA.values():
            payload = item.to_dict()
            self.assertEqual(payload["id"], item.id)
            self.assertEqual(
                set(payload),
                {
                    "id",
                    "version",
                    "asset_class",
                    "research_scene",
                    "scope",
                    "capability_kind",
                    "implementation_status",
                    "public_state",
                    "priority",
                },
            )

    def test_non_public_kinds_cannot_claim_supported(self) -> None:
        with self.assertRaises(ValueError):
            CapabilityMetadata(
                id="internal_example",
                version="1.0",
                asset_class="market_aggregate",
                research_scene="evidence_result",
                scope="cross_cutting",
                capability_kind="internal",
                implementation_status="retain",
                public_state="supported",
                priority="P0",
            )


if __name__ == "__main__":
    unittest.main()
