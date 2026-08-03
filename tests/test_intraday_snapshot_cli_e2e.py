from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "intraday_snapshot" / "fixture_cli.py"
)
ENTRYPOINT = (
    REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts" / "entrypoint.py"
)


def intraday_request(security: str, *, as_of: str = "2026-08-03") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "intraday_market_signal",
        "subjects": [{"security": security}],
        "as_of": as_of,
        "window": None,
        "parameters": {},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


class IntradaySnapshotCliE2ETests(unittest.TestCase):
    def run_task(
        self,
        request: dict[str, object],
        *,
        environment: dict[str, str] | None = None,
    ) -> dict[str, object]:
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
        return json.loads(completed.stdout)

    def test_sse_continuous_snapshot_is_limited_and_lineage_complete(self) -> None:
        result = self.run_task(intraday_request("SSE:600519"))

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["task_type"], "intraday_market_signal")
        self.assertEqual(
            result["subject"],
            {
                "security": {
                    "exchange": "SSE",
                    "code": "600519",
                    "type": "A_SHARE",
                }
            },
        )
        self.assertEqual(result["as_of"], "2026-08-03")
        self.assertEqual(result["trading_date"], "2026-08-03")
        self.assertEqual(result["session_state"], "continuous")
        self.assertEqual(result["trading_status"], "traded")
        self.assertEqual(result["price_type"], "latest_traded")
        self.assertEqual(
            result["snapshot"],
            {
                "latest_price": {"value": "1680.25", "unit": "CNY/share"},
                "open": {"value": "1675.00", "unit": "CNY/share"},
                "high": {"value": "1688.00", "unit": "CNY/share"},
                "low": {"value": "1670.50", "unit": "CNY/share"},
                "previous_close": {
                    "status": "unavailable",
                    "reported_value": "1668.00",
                    "unit": "CNY/share",
                    "basis": "source_reported_unadjudicated",
                    "reason": "independent_semantics_not_adjudicated",
                },
                "cumulative_volume": {"value": "1234500", "unit": "shares"},
                "cumulative_amount": {"value": "2071234567.89", "unit": "CNY"},
            },
        )
        self.assertEqual(
            result["observation_times"],
            {
                "tongdaxin_baseline": "2026-08-03T10:30:00+08:00",
                "tencent_cross_check": "2026-08-03T10:29:58+08:00",
                "retrieved_at": "2026-08-03T10:30:05+08:00",
                "pair_gap_seconds": "2",
            },
        )
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            [
                "tongdaxin_intraday_snapshot@1",
                "tongdaxin_intraday_snapshot@1",
                "tencent_intraday_snapshot@1",
            ],
        )
        self.assertEqual(
            result["field_lineage"]["snapshot.cumulative_amount"],
            {
                "evidence_ids": [
                    "intraday-tdx-quote-SSE:600519-2026-08-03T10:30:00+08:00"
                ],
                "source_fields": ["amount"],
            },
        )
        self.assertEqual(
            result["field_lineage"]["snapshot.previous_close"]["evidence_ids"],
            ["intraday-tdx-quote-SSE:600519-2026-08-03T10:30:00+08:00"],
        )
        self.assertEqual(
            result["field_lineage"]["snapshot.cumulative_volume"]["evidence_ids"],
            ["intraday-tdx-quote-SSE:600519-2026-08-03T10:30:00+08:00"],
        )
        self.assertEqual(
            set(result["field_lineage"]),
            {
                "subject",
                "trading_date",
                "session_state",
                "trading_status",
                "price_type",
                "snapshot.latest_price",
                "snapshot.open",
                "snapshot.high",
                "snapshot.low",
                "snapshot.previous_close",
                "snapshot.cumulative_volume",
                "snapshot.cumulative_amount",
                "observation_times.tongdaxin_baseline",
                "observation_times.tencent_cross_check",
                "observation_times.retrieved_at",
                "observation_times.pair_gap_seconds",
            },
        )
        self.assertEqual(len(result["source_operations"]), 2)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(
            result["limitations"][0]["code"], "experimental_intraday_sources"
        )

    def test_szse_continuous_snapshot_uses_the_same_public_contract(self) -> None:
        result = self.run_task(intraday_request("SZSE:000001"))

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["subject"]["security"],
            {"exchange": "SZSE", "code": "000001", "type": "A_SHARE"},
        )
        self.assertEqual(result["snapshot"]["latest_price"]["value"], "12.34")
        self.assertEqual(
            result["snapshot"]["cumulative_volume"],
            {"value": "987600", "unit": "shares"},
        )
        self.assertEqual(
            result["snapshot"]["cumulative_amount"],
            {"value": "12187654.32", "unit": "CNY"},
        )
        self.assertEqual(
            result["source_operations"],
            [
                "tongdaxin_intraday_snapshot@1",
                "tencent_intraday_snapshot@1",
            ],
        )

    def test_jointly_confirmed_suspension_is_limited_without_fake_prices(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "suspension_confirmed"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["trading_status"], "suspended")
        for field in ("latest_price", "open", "high", "low"):
            self.assertEqual(
                result["snapshot"][field],
                {
                    "status": "not_applicable",
                    "value": None,
                    "unit": "CNY/share",
                    "reason": "suspended",
                },
            )
        self.assertEqual(
            result["snapshot"]["cumulative_volume"],
            {"value": "0", "unit": "shares"},
        )
        self.assertEqual(
            result["snapshot"]["cumulative_amount"],
            {"value": "0", "unit": "CNY"},
        )

    def test_corporate_action_previous_close_is_unavailable_without_change_metrics(
        self,
    ) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "corporate_action_unavailable"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["snapshot"]["latest_price"]["value"], "1680.25")
        previous_close = result["snapshot"]["previous_close"]
        self.assertEqual(previous_close["status"], "unavailable")
        self.assertEqual(
            previous_close["reason"],
            "corporate_action_previous_close_not_comparable",
        )
        self.assertNotIn("change_amount", result["snapshot"])
        self.assertNotIn("change_percent", result["snapshot"])

    def test_comparable_previous_close_produces_decimal_change_metrics(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "comparable_prev_close"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["snapshot"]["previous_close"],
            {
                "status": "available",
                "value": "1668.00",
                "reported_value": "1668.00",
                "unit": "CNY/share",
                "basis": "actual_close",
            },
        )
        self.assertEqual(
            result["snapshot"]["change_amount"],
            {"value": "12.25", "unit": "CNY/share"},
        )
        self.assertEqual(
            result["snapshot"]["change_percent"],
            {"value": "0.73", "unit": "percent"},
        )

    def test_one_source_suspension_cannot_be_promoted(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "suspension_one_source"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_suspension_confirmation_mismatch",
            {item["code"] for item in result["conflicts"]},
        )
        self.assertGreaterEqual(len(result["evidence"]), 2)

    def test_suspension_core_price_conflict_still_blocks(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "suspension_core_conflict"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_core_price_mismatch",
            {item["code"] for item in result["conflicts"]},
        )

    def test_suspension_without_joint_no_trade_confirmation_blocks(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "suspension_status_ambiguous"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_suspension_no_trade_unconfirmed",
            {item["code"] for item in result["conflicts"]},
        )

    def test_equal_previous_close_does_not_establish_suspension(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "price_equal_previous_close"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_suspension_ambiguous",
            {item["code"] for item in result["conflicts"]},
        )

    def test_source_policy_rejection_blocks_before_source_collection(self) -> None:
        request = intraday_request("SSE:600519")
        request["source_policy"]["allow_experimental"] = False  # type: ignore[index]

        result = self.run_task(
            request,
            environment={"A_SHARE_INTRADAY_FAIL_IF_COLLECTED": "1"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "source_policy_not_satisfied"
        )
        self.assertEqual(result["evidence"], [])

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
        limitation = result["limitations"][0]
        self.assertEqual(limitation["code"], "missing_optional_dependency")
        self.assertEqual(limitation["dependency"], "mootdx")
        self.assertEqual(limitation["required_version"], "0.11.7")
        self.assertEqual(result["evidence"], [])

    def test_real_entrypoint_applies_source_policy_without_network(self) -> None:
        request = intraday_request("SSE:600519")
        request["source_policy"]["allow_experimental"] = False  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "request.json")
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ENTRYPOINT),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout)["limitations"][0]["code"],
            "source_policy_not_satisfied",
        )

    def test_non_a_share_code_is_rejected_before_source_collection(self) -> None:
        request = intraday_request("SSE:510300")
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
                    "A_SHARE_INTRADAY_FAIL_IF_COLLECTED": "1",
                },
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("not a supported SSE/SZSE A-share", completed.stderr)

    def test_source_failures_preserve_other_side_evidence_and_diagnosis(self) -> None:
        expected_codes = {
            "tdx_factory_failure": "upstream_unavailable",
            "tdx_missing_daily": "unknown_schema",
            "tdx_old_daily": "trading_date_mismatch",
            "tencent_failure": "upstream_http_error",
        }
        for scenario, expected_code in expected_codes.items():
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    intraday_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["source_errors"][0]["code"], expected_code)
                self.assertGreaterEqual(len(result["evidence"]), 1)

    def test_pair_incompatibility_is_an_exit_zero_blocked_domain_result(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "core_price_mismatch"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["conflicts"][0]["code"], "intraday_core_price_mismatch")
        self.assertEqual(result["conflicts"][0]["field"], "latest_price")
        self.assertEqual(result["conflicts"][0]["baseline"], "1680.25")
        self.assertEqual(result["conflicts"][0]["cross_check"], "1680.26")
        self.assertEqual(
            result["limitations"][0]["code"],
            "intraday_source_pair_incompatible",
        )

    def test_provider_float_noise_is_normalized_to_the_cny_tick(self) -> None:
        for scenario in ("tdx_float_noise", "tencent_float_noise"):
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    intraday_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "limited")
                self.assertEqual(result["snapshot"]["latest_price"]["value"], "1680.25")
                self.assertEqual(result["conflicts"], [])

    def test_malformed_or_ambiguous_tongdaxin_values_block_with_sanitized_error(
        self,
    ) -> None:
        expected_codes = {
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
        for scenario, expected_code in expected_codes.items():
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    intraday_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["source_errors"][0]["code"], expected_code)
                self.assertNotIn("not-a-price", result["source_errors"][0]["message"])
                self.assertGreaterEqual(len(result["evidence"]), 1)

    def test_malformed_or_empty_tencent_payload_blocks_without_losing_tdx_evidence(
        self,
    ) -> None:
        for scenario, expected_code in (
            ("tencent_malformed_json", "unknown_schema"),
            ("tencent_empty_body", "empty_response"),
            ("tencent_wrong_security", "wrong_security_payload"),
            ("tencent_unknown_kind", "unknown_schema"),
        ):
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    intraday_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["source_errors"][0]["code"], expected_code)
                self.assertEqual(
                    result["source_errors"][0]["message"],
                    {
                        "unknown_schema": "The source response did not match the expected schema.",
                        "empty_response": "The source operation returned an empty response.",
                        "wrong_security_payload": "The source response identifies another security.",
                    }[expected_code],
                )
                self.assertGreaterEqual(len(result["evidence"]), 2)

    def test_active_source_observation_older_than_sixty_seconds_blocks(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "source_stale"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_observation_too_old",
            {item["code"] for item in result["conflicts"]},
        )

    def test_active_source_pair_more_than_sixty_seconds_apart_blocks(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "pair_gap"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_source_pair_gap_exceeded",
            {item["code"] for item in result["conflicts"]},
        )

    def test_opening_auction_uses_indicative_prices_from_both_sources(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "opening_auction"},
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["session_state"], "opening_auction")
        self.assertEqual(result["price_type"], "indicative_auction")
        self.assertEqual(result["trading_status"], "auction")

    def test_closing_auction_uses_indicative_prices_from_both_sources(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "closing_auction"},
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["session_state"], "closing_auction")
        self.assertEqual(result["price_type"], "indicative_auction")

    def test_midday_break_retains_the_last_compatible_morning_pair(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "midday_break"},
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["session_state"], "midday_break")
        self.assertEqual(result["price_type"], "latest_traded")
        self.assertEqual(result["trading_status"], "traded")
        self.assertEqual(
            result["observation_times"]["tongdaxin_baseline"],
            "2026-08-03T11:29:50+08:00",
        )
        self.assertEqual(
            result["observation_times"]["tencent_cross_check"],
            "2026-08-03T11:29:55+08:00",
        )
        self.assertEqual(
            result["observation_times"]["observation_boundary"],
            "morning_last_compatible_pair",
        )
        self.assertEqual(
            result["snapshot"]["cumulative_volume"],
            {"value": "1234500", "unit": "shares"},
        )

    def test_unknown_source_cache_state_blocks_with_evidence(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "unknown_cache"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_cache_state_unknown",
            {item["code"] for item in result["conflicts"]},
        )
        self.assertGreaterEqual(len(result["evidence"]), 1)

    def test_missing_source_cache_state_blocks_closed(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "missing_cache"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_errors"][0]["code"], "unknown_cache_state")

    def test_midday_pair_gap_still_requires_a_compatible_morning_pair(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "midday_pair_gap"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_source_pair_gap_exceeded",
            {item["code"] for item in result["conflicts"]},
        )

    def test_midday_non_last_morning_marker_cannot_be_promoted(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "midday_not_last"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["source_errors"][0]["code"], "incompatible_observation_boundary"
        )

    def test_unknown_tencent_price_type_blocks_closed(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "unknown_price_type"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_errors"][0]["code"], "unknown_price_type")

    def test_pre_open_returns_a_structured_blocked_result(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "pre_open"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "intraday_session_not_applicable"
        )

    def test_post_close_returns_a_structured_blocked_result(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "post_close"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "intraday_session_not_applicable"
        )

    def test_historical_and_future_dates_are_domain_blocked_with_exit_zero(
        self,
    ) -> None:
        for as_of, expected_code in (
            ("2026-08-02", "intraday_as_of_not_current"),
            ("2026-08-04", "intraday_as_of_not_current"),
        ):
            with self.subTest(as_of=as_of):
                result = self.run_task(intraday_request("SSE:600519", as_of=as_of))
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["limitations"][0]["code"], expected_code)

    def test_non_trading_date_is_domain_blocked(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519", as_of="2026-08-08"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "non_trading"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["limitations"][0]["code"], "intraday_non_trading_date")

    def test_weekday_holiday_without_current_day_source_evidence_blocks(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519", as_of="2026-08-04"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "weekday_holiday"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["source_errors"]), 2)
        self.assertEqual(
            {item["code"] for item in result["source_errors"]},
            {"trading_date_mismatch", "incomplete_observation"},
        )

    def test_source_session_mismatch_is_structured_blocked(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "session_mismatch"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "intraday_session_mismatch",
            {item["code"] for item in result["conflicts"]},
        )

    def test_missing_source_time_preserves_the_other_source_diagnostic(self) -> None:
        result = self.run_task(
            intraday_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_SCENARIO": "missing_time"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_errors"][0]["code"], "unknown_schema")
        self.assertGreaterEqual(len(result["evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
