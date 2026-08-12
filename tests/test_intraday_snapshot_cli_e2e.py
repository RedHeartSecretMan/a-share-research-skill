from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = REPOSITORY_ROOT / "tests/fixtures/intraday_snapshot/fixture_cli.py"
FORMAL_KEYS = {
    "schema_version",
    "status",
    "research_anchor",
    "plan",
    "capability_registry",
    "coverage",
    "observations",
    "claims",
    "calculations",
    "conflicts",
    "limitations",
    "source_errors",
    "task_views",
    "briefs",
}


def intraday_request(
    security: str, *, as_of: str = "2026-08-03", allow_experimental: bool = True
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "intraday_market_signal",
        "subjects": [{"security": security}],
        "as_of": as_of,
        "window": None,
        "parameters": {},
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


class IntradaySnapshotCliE2ETests(unittest.TestCase):
    def run_task(
        self, request: dict[str, object], *, environment: dict[str, str] | None = None
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "request.json")
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    **(environment or {}),
                },
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(set(result), FORMAL_KEYS)
        self.assertEqual(result["plan"]["task_type"], "intraday_market_signal")
        return result

    def scenario(
        self, name: str, *, security: str = "SSE:600519", as_of: str = "2026-08-03"
    ) -> dict[str, Any]:
        return self.run_task(
            intraday_request(security, as_of=as_of),
            environment={"A_SHARE_INTRADAY_SCENARIO": name},
        )

    def claim(self, result: dict[str, Any]) -> dict[str, Any]:
        return next(
            item["payload"]
            for item in result["claims"]
            if item["kind"] == "intraday_market_snapshot"
        )

    def codes(self, result: dict[str, Any]) -> set[str]:
        return {
            item["code"]
            for field in ("conflicts", "source_errors", "limitations")
            for item in result[field]
        }

    def assert_blocked(self, scenario: str, code: str) -> dict[str, Any]:
        result = self.scenario(scenario)
        self.assertEqual(result["status"], "blocked")
        self.assertIn(code, self.codes(result))
        return result

    def test_sse_continuous_snapshot_is_limited_and_lineage_complete(self) -> None:
        result = self.scenario("success")
        facts = self.claim(result)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(facts["subject"]["security"]["exchange"], "SSE")
        self.assertEqual(facts["snapshot"]["cumulative_volume"]["unit"], "shares")
        self.assertEqual(facts["snapshot"]["cumulative_amount"]["unit"], "CNY")
        self.assertEqual(len(result["observations"]), 2)
        for operation in result["coverage"]["intraday_market_snapshot"][
            "operations"
        ].values():
            self.assertTrue(operation["field_dispositions"])

    def test_szse_continuous_snapshot_uses_the_same_public_contract(self) -> None:
        result = self.scenario("success", security="SZSE:000001")
        self.assertEqual(self.claim(result)["subject"]["security"]["exchange"], "SZSE")

    def test_jointly_confirmed_suspension_is_limited_without_fake_prices(self) -> None:
        facts = self.claim(self.scenario("suspension_confirmed"))
        self.assertEqual(facts["trading_status"], "suspended")
        self.assertEqual(facts["snapshot"]["latest_price"]["status"], "not_applicable")
        self.assertIsNone(facts["snapshot"]["latest_price"]["value"])

    def test_corporate_action_previous_close_is_unavailable_without_change_metrics(
        self,
    ) -> None:
        result = self.scenario("corporate_action_unavailable")
        snapshot = self.claim(result)["snapshot"]
        self.assertEqual(snapshot["previous_close"]["status"], "unavailable")
        self.assertNotIn("change_amount", snapshot)

    def test_comparable_previous_close_produces_decimal_change_metrics(self) -> None:
        snapshot = self.claim(self.scenario("comparable_prev_close"))["snapshot"]
        self.assertEqual(
            snapshot["change_amount"], {"value": "12.25", "unit": "CNY/share"}
        )
        self.assertEqual(
            snapshot["change_percent"], {"value": "0.73", "unit": "percent"}
        )

    def test_one_source_suspension_cannot_be_promoted(self) -> None:
        self.assert_blocked(
            "suspension_one_source", "intraday_suspension_confirmation_mismatch"
        )

    def test_suspension_core_price_conflict_still_blocks(self) -> None:
        self.assert_blocked("suspension_core_conflict", "intraday_core_price_mismatch")

    def test_suspension_without_joint_no_trade_confirmation_blocks(self) -> None:
        self.assert_blocked(
            "suspension_status_ambiguous", "intraday_suspension_no_trade_unconfirmed"
        )

    def test_not_traded_alias_does_not_establish_suspension(self) -> None:
        self.assert_blocked(
            "suspension_alias", "intraday_suspension_confirmation_mismatch"
        )

    def test_equal_previous_close_with_trading_evidence_remains_limited(self) -> None:
        result = self.scenario("price_equal_previous_close")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            self.claim(result)["snapshot"]["latest_price"]["value"], "1668.00"
        )

    def test_unknown_previous_close_comparability_stays_unavailable(self) -> None:
        snapshot = self.claim(self.scenario("unknown_prev_close_comparability"))[
            "snapshot"
        ]
        self.assertEqual(
            snapshot["previous_close"]["reason"],
            "independent_semantics_not_adjudicated",
        )
        self.assertNotIn("change_percent", snapshot)

    def test_source_policy_rejection_blocks_before_source_collection(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519", allow_experimental=False),
            environment={"A_SHARE_INTRADAY_FAIL_IF_COLLECTED": "1"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("source_policy_not_satisfied", self.codes(result))
        self.assertTrue(
            all(
                op["execution_state"] == "skipped_policy"
                for op in result["coverage"]["intraday_market_snapshot"][
                    "operations"
                ].values()
            )
        )

    def test_missing_locked_mootdx_blocks_only_this_task_before_collection(
        self,
    ) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={
                "A_SHARE_INTRADAY_DEPENDENCIES": "",
                "A_SHARE_INTRADAY_FAIL_IF_COLLECTED": "1",
            },
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("installation_incomplete", self.codes(result))

    def test_real_entrypoint_applies_source_policy_without_network(self) -> None:
        self.test_source_policy_rejection_blocks_before_source_collection()

    def test_non_a_share_code_is_rejected_before_source_collection(self) -> None:
        request = intraday_request("SSE:510300")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "request.json")
            path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(FIXTURE_CLI), "run", "--request", str(path)],
                cwd=REPOSITORY_ROOT,
                env={**os.environ, "A_SHARE_INTRADAY_FAIL_IF_COLLECTED": "1"},
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 2)

    def test_source_failures_preserve_other_side_evidence_and_diagnosis(self) -> None:
        expected = {
            "tdx_factory_failure": "upstream_unavailable",
            "tdx_missing_daily": "unknown_schema",
            "tdx_old_daily": "trading_date_mismatch",
            "tencent_failure": "upstream_http_error",
        }
        for scenario, code in expected.items():
            with self.subTest(scenario=scenario):
                result = self.assert_blocked(scenario, code)
                self.assertEqual(len(result["observations"]), 1)

    def test_pair_incompatibility_is_an_exit_zero_blocked_domain_result(self) -> None:
        result = self.assert_blocked(
            "core_price_mismatch", "intraday_core_price_mismatch"
        )
        self.assertEqual(len(result["observations"]), 2)

    def test_provider_float_noise_is_normalized_to_the_cny_tick(self) -> None:
        for scenario in ("tdx_float_noise", "tencent_float_noise"):
            self.assertEqual(
                self.claim(self.scenario(scenario))["snapshot"]["latest_price"][
                    "value"
                ],
                "1680.25",
            )

    def test_malformed_or_ambiguous_tongdaxin_values_block_with_sanitized_error(
        self,
    ) -> None:
        expected = {
            "tdx_malformed_price": "unknown_schema",
            "tdx_negative_volume": "unknown_schema",
            "tdx_zero_values": "ambiguous_zero_value",
            "tdx_fractional_volume": "ambiguous_volume_unit",
            "tdx_unknown_volume_unit": "ambiguous_volume_unit",
            "tdx_unknown_amount_unit": "ambiguous_amount_unit",
            "tdx_missing_units": "ambiguous_volume_unit",
            "tdx_quote_date_mismatch": "quote_daily_date_mismatch",
            "tdx_wrong_daily_security": "quote_daily_security_mismatch",
        }
        for scenario, code in expected.items():
            with self.subTest(scenario=scenario):
                result = self.assert_blocked(scenario, code)
                self.assertEqual(len(result["observations"]), 1)
                self.assertNotIn("not-a-price", json.dumps(result))

    def test_tongdaxin_raw_schema_rejects_unknown_zero_and_keeps_legal_aliases(
        self,
    ) -> None:
        for scenario in ("tdx_unknown_quote_zero", "tdx_unknown_daily_zero"):
            with self.subTest(scenario=scenario):
                self.assert_blocked(scenario, "unknown_schema")
        self.assertEqual(self.scenario("tdx_legal_aliases")["status"], "limited")

    def test_malformed_or_empty_tencent_payload_blocks_without_losing_tdx_evidence(
        self,
    ) -> None:
        for scenario, code in (
            ("tencent_malformed_json", "unknown_schema"),
            ("tencent_empty_body", "empty_response"),
            ("tencent_wrong_security", "wrong_security_payload"),
            ("tencent_unknown_kind", "unknown_schema"),
        ):
            with self.subTest(scenario=scenario):
                self.assertEqual(
                    len(self.assert_blocked(scenario, code)["observations"]), 1
                )

    def test_active_source_observation_older_than_sixty_seconds_blocks(self) -> None:
        self.assertEqual(
            len(
                self.assert_blocked("source_stale", "intraday_observation_too_old")[
                    "observations"
                ]
            ),
            2,
        )

    def test_active_source_pair_more_than_sixty_seconds_apart_blocks(self) -> None:
        self.assert_blocked("pair_gap", "intraday_source_pair_gap_exceeded")

    def test_opening_auction_uses_indicative_prices_from_both_sources(self) -> None:
        facts = self.claim(self.scenario("opening_auction"))
        self.assertEqual(
            (facts["session_state"], facts["price_type"]),
            ("opening_auction", "indicative_auction"),
        )

    def test_closing_auction_uses_indicative_prices_from_both_sources(self) -> None:
        facts = self.claim(self.scenario("closing_auction"))
        self.assertEqual(
            (facts["session_state"], facts["price_type"]),
            ("closing_auction", "indicative_auction"),
        )

    def test_midday_break_retains_the_last_compatible_morning_pair(self) -> None:
        facts = self.claim(self.scenario("midday_break"))
        self.assertEqual(facts["session_state"], "midday_break")
        self.assertEqual(facts["observation_times"]["pair_gap_seconds"], "5")

    def test_unknown_source_cache_state_blocks_with_evidence(self) -> None:
        self.assertEqual(
            len(
                self.assert_blocked("unknown_cache", "intraday_cache_state_unknown")[
                    "observations"
                ]
            ),
            2,
        )

    def test_untrusted_cache_state_is_not_echoed_in_public_result(self) -> None:
        result = self.scenario("cache_state_secret")
        self.assertNotIn("provider-secret-token", json.dumps(result))

    def test_untrusted_previous_close_metadata_is_not_echoed(self) -> None:
        result = self.scenario("metadata_secret")
        self.assertNotIn("provider-secret-token", json.dumps(result))

    def test_missing_source_cache_state_blocks_closed(self) -> None:
        self.assert_blocked("missing_cache", "unknown_cache_state")

    def test_midday_pair_gap_still_requires_a_compatible_morning_pair(self) -> None:
        self.assert_blocked("midday_pair_gap", "intraday_source_pair_gap_exceeded")

    def test_midday_non_last_morning_marker_cannot_be_promoted(self) -> None:
        self.assert_blocked("midday_not_last", "incompatible_observation_boundary")

    def test_unknown_tencent_price_type_blocks_closed(self) -> None:
        self.assert_blocked("unknown_price_type", "unknown_price_type")

    def test_pre_open_returns_a_structured_blocked_result(self) -> None:
        result = self.assert_blocked("pre_open", "intraday_session_not_applicable")
        self.assertEqual(result["observations"], [])

    def test_post_close_returns_a_structured_blocked_result(self) -> None:
        self.assert_blocked("post_close", "intraday_session_not_applicable")

    def test_historical_and_future_dates_are_domain_blocked_with_exit_zero(
        self,
    ) -> None:
        for as_of, code in (
            ("2026-08-02", "intraday_as_of_not_current"),
            ("2026-08-04", "intraday_as_of_not_current"),
        ):
            result = self.run_task(intraday_request("SSE:600519", as_of=as_of))
            self.assertIn(code, self.codes(result))

    def test_non_trading_date_is_domain_blocked(self) -> None:
        result = self.scenario("non_trading", as_of="2026-08-08")
        self.assertIn("intraday_non_trading_date", self.codes(result))

    def test_weekday_holiday_without_current_day_source_evidence_blocks(self) -> None:
        result = self.scenario("weekday_holiday", as_of="2026-08-04")
        self.assertEqual(
            {item["code"] for item in result["source_errors"]},
            {"trading_date_mismatch", "incomplete_observation"},
        )

    def test_source_session_mismatch_is_structured_blocked(self) -> None:
        self.assert_blocked("session_mismatch", "intraday_session_mismatch")

    def test_missing_source_time_preserves_the_other_source_diagnostic(self) -> None:
        result = self.assert_blocked("missing_time", "unknown_schema")
        self.assertEqual(len(result["observations"]), 1)


if __name__ == "__main__":
    unittest.main()
