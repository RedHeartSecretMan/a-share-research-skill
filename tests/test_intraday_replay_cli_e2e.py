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
    REPOSITORY_ROOT / "tests" / "fixtures" / "intraday_replay" / "fixture_cli.py"
)


def replay_request(security: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "intraday_replay",
        "subjects": [{"security": security}],
        "as_of": "2026-08-04",
        "window": {
            "observed_from": "2026-08-03",
            "observed_to": "2026-08-03",
        },
        "parameters": {},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


class IntradayReplayTracerCliTests(unittest.TestCase):
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
                env={**os.environ, **(environment or {})},
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_complete_coverage_separates_sessions_break_and_auctions(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "complete"},
        )

        self.assertEqual(result["coverage"]["status"], "complete")
        self.assertEqual(
            result["coverage"]["expected_intervals"],
            [
                {
                    "interval_start": "2026-08-03T09:30:00+08:00",
                    "interval_end": "2026-08-03T11:30:00+08:00",
                    "trading_phase": "continuous_morning",
                },
                {
                    "interval_start": "2026-08-03T13:00:00+08:00",
                    "interval_end": "2026-08-03T14:57:00+08:00",
                    "trading_phase": "continuous_afternoon",
                },
            ],
        )
        self.assertEqual(
            result["coverage"]["lunch_break"],
            {
                "interval_start": "2026-08-03T11:30:00+08:00",
                "interval_end": "2026-08-03T13:00:00+08:00",
                "excluded_from_coverage": True,
            },
        )
        self.assertEqual(result["records"][0]["trading_phase"], "continuous_morning")
        self.assertEqual(result["records"][-1]["trading_phase"], "continuous_afternoon")
        self.assertEqual(len(result["records"]), 237)
        self.assertEqual(len(result["auction_results"]), 2)
        self.assertEqual(
            [item["trading_phase"] for item in result["auction_results"]],
            ["opening_auction", "closing_auction"],
        )
        self.assertEqual(
            result["auction_results"][1]["interval_start"],
            "2026-08-03T14:57:00+08:00",
        )
        self.assertEqual(
            result["auction_results"][1]["interval_end"],
            "2026-08-03T15:00:00+08:00",
        )
        self.assertEqual(
            result["coverage"]["coverage_ratio"],
            {
                "covered_minutes": 237,
                "expected_minutes": 237,
                "value": "1.0000",
                "formula": "covered_minutes / expected_minutes",
            },
        )

    def test_complete_coverage_accepts_szse_at_the_same_public_seam(self) -> None:
        result = self.run_task(
            replay_request("SZSE:000001"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "complete"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["subjects"][0]["security"]["exchange"], "SZSE")
        self.assertEqual(result["coverage"]["status"], "complete")
        self.assertEqual(result["coverage"]["coverage_ratio"]["value"], "1.0000")

    def test_deterministic_replay_result_has_no_agent_prediction_layer(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "complete"},
        )

        for forbidden_field in (
            "prediction",
            "predictions",
            "direction",
            "scenarios",
            "agent_view",
            "agent_analysis",
        ):
            with self.subTest(forbidden_field=forbidden_field):
                self.assertNotIn(forbidden_field, result)

    def test_partial_closing_subinterval_is_not_claimed_complete(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "subinterval_partial"},
        )

        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertEqual(result["coverage"]["closing_auction"]["status"], "partial")
        self.assertEqual(
            result["coverage"]["missing_auction_intervals"],
            [
                {
                    "interval_start": "2026-08-03T14:58:00+08:00",
                    "interval_end": "2026-08-03T15:00:00+08:00",
                    "trading_phase": "closing_auction",
                }
            ],
        )
        self.assertEqual(len(result["auction_results"]), 2)

    def test_unknown_session_contract_fails_closed(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "unknown_session"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["coverage"]["status"], "indeterminate")
        self.assertEqual(
            result["source_errors"][0]["code"], "session_semantics_unverified"
        )

    def test_partial_coverage_retains_qualified_no_trade_and_true_missing(self) -> None:
        result = self.run_task(
            replay_request("SZSE:000001"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "partial_no_trade"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["coverage"]["status"], "partial")
        no_trade = [
            row for row in result["records"] if row["trade_state"] == "no_trade"
        ]
        self.assertEqual(len(no_trade), 1)
        self.assertEqual(
            no_trade[0]["ohlc"],
            {"status": "unavailable", "reason": "source_proven_no_trade"},
        )
        self.assertEqual(no_trade[0]["volume"], {"value": "0", "unit": "shares"})
        self.assertEqual(no_trade[0]["amount"], {"value": "0.00", "unit": "CNY"})
        self.assertEqual(
            result["coverage"]["proven_no_trade_intervals"],
            [
                {
                    "interval_start": "2026-08-03T09:31:00+08:00",
                    "interval_end": "2026-08-03T09:32:00+08:00",
                    "trading_phase": "continuous_morning",
                }
            ],
        )
        self.assertEqual(
            result["coverage"]["missing_intervals"][0],
            {
                "interval_start": "2026-08-03T09:32:00+08:00",
                "interval_end": "2026-08-03T11:30:00+08:00",
                "trading_phase": "continuous_morning",
            },
        )
        self.assertEqual(result["coverage"]["coverage_ratio"]["covered_minutes"], 3)
        self.assertEqual(result["coverage"]["coverage_ratio"]["expected_minutes"], 237)
        self.assertEqual(result["coverage"]["coverage_ratio"]["value"], "0.0127")
        self.assertFalse(
            any(
                row["interval_start"] == "2026-08-03T09:32:00+08:00"
                for row in result["records"]
            )
        )

    def test_indeterminate_coverage_is_structured_blocked(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "indeterminate"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["coverage"]["status"], "indeterminate")
        self.assertEqual(
            result["coverage"]["coverage_ratio"],
            {
                "status": "unavailable",
                "reason": "coverage_bound_indeterminate",
                "formula": "covered_minutes / expected_minutes",
            },
        )
        self.assertIn(
            "intraday_replay_coverage_indeterminate",
            [item["code"] for item in result["limitations"]],
        )

    def test_unknown_auction_semantics_fail_closed(self) -> None:
        result = self.run_task(
            replay_request("SZSE:000001"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "unknown_auction"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["coverage"]["status"], "indeterminate")
        self.assertEqual(
            result["source_errors"][0]["code"], "auction_semantics_unverified"
        )

    def test_zero_volume_alone_is_not_no_trade_proof(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "zero_volume"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["source_errors"][0]["code"], "traded_zero_volume_ambiguous"
        )

    def test_current_day_before_session_end_is_blocked_at_public_seam(self) -> None:
        request = replay_request("SSE:600519")
        request["as_of"] = "2026-08-03"
        request["window"] = {
            "observed_from": "2026-08-03",
            "observed_to": "2026-08-03",
        }
        result = self.run_task(
            request,
            environment={"A_SHARE_INTRADAY_REPLAY_NOW": "2026-08-03T14:59:00+08:00"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "replay_session_not_completed"
        )

    def test_experimental_replay_is_versioned_sorted_and_chart_ready(self) -> None:
        result = self.run_task(replay_request("SSE:600519"))

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["task_type"], "intraday_replay")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["subjects"],
            [
                {
                    "security": {
                        "exchange": "SSE",
                        "code": "600519",
                        "type": "A_SHARE",
                    }
                }
            ],
        )
        self.assertEqual(result["research"]["as_of"], "2026-08-04")
        self.assertEqual(result["replay"]["trading_date"], "2026-08-03")
        self.assertEqual(result["replay"]["security"], "SSE:600519")
        self.assertEqual(
            [row["interval_start"] for row in result["records"]],
            [
                "2026-08-03T09:30:00+08:00",
                "2026-08-03T09:31:00+08:00",
            ],
        )
        self.assertEqual(
            result["records"][0]["interval_end"],
            "2026-08-03T09:31:00+08:00",
        )
        self.assertEqual(
            result["records"][0]["ohlc"],
            {
                "open": {"value": "10.10", "unit": "CNY/share"},
                "high": {"value": "10.21", "unit": "CNY/share"},
                "low": {"value": "10.09", "unit": "CNY/share"},
                "close": {"value": "10.20", "unit": "CNY/share"},
            },
        )
        self.assertEqual(
            result["records"][0]["volume"], {"value": "100", "unit": "shares"}
        )
        self.assertEqual(
            result["records"][0]["amount"], {"value": "1015.00", "unit": "CNY"}
        )
        self.assertEqual(result["records"][0]["timestamp_semantics"], "interval_start")
        self.assertEqual(
            result["records"][0]["source_timestamp"], "2026-08-03T09:30:00+08:00"
        )
        self.assertEqual(result["coverage"]["status"], "partial")
        self.assertEqual(
            result["source_operations"],
            [
                {
                    "operation_id": "fixture_intraday_replay@1",
                    "contract_version": "1.0",
                    "experimental": True,
                    "retrieved_at": "2026-08-04T16:00:00+08:00",
                }
            ],
        )
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(
            result["limitations"][0]["code"], "experimental_intraday_replay_source"
        )
        self.assertEqual(len(result["evidence"]), 2)
        self.assertIsNone(result["evidence"][0]["available_at"])
        self.assertIn(
            "public_availability_unverified", result["evidence"][0]["limitations"]
        )
        self.assertEqual(
            result["field_lineage"]["records[0].ohlc.close"]["evidence_ids"],
            [result["records"][0]["evidence_ids"][0]],
        )

    def test_summary_keeps_bounded_counts_and_unavailable_endpoint_metrics_explicit(
        self,
    ) -> None:
        result = self.run_task(replay_request("SSE:600519"))

        summary = result["summary"]
        self.assertEqual(summary["version"], "1.0")
        self.assertEqual(
            summary["counts"],
            {
                "record_count": 2,
                "continuous_records": 2,
                "traded_intervals": 2,
                "proven_no_trade_intervals": 0,
                "covered_intervals": 2,
                "expected_intervals": 237,
                "missing_intervals": result["coverage"]["missing_intervals"],
            },
        )
        self.assertEqual(
            summary["metrics"]["open_to_close"],
            {
                "status": "unavailable",
                "reason": "actual_close_not_established",
            },
        )
        self.assertEqual(
            summary["metrics"]["vwap"]["status"],
            "available",
        )
        self.assertEqual(summary["metrics"]["vwap"]["value"], "10.1967")

    def test_summary_recomputes_ties_and_never_crosses_no_trade_or_lunch(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "summary_metrics"},
        )

        summary = result["summary"]
        self.assertEqual(summary["counts"]["proven_no_trade_intervals"], 1)
        self.assertEqual(summary["metrics"]["vwap"]["value"], "9.9429")
        self.assertEqual(summary["metrics"]["high"]["value"], "10.30")
        self.assertEqual(len(summary["metrics"]["high"]["times"]), 3)
        self.assertEqual(summary["metrics"]["max_drawdown"]["value"], "0.30")
        self.assertEqual(summary["metrics"]["max_adjacent_rise"]["value"], "0.10")
        self.assertEqual(summary["metrics"]["max_adjacent_fall"]["value"], "0.30")
        self.assertEqual(summary["metrics"]["morning_volume_share"]["value"], "0.8889")
        self.assertEqual(
            len(summary["metrics"]["max_adjacent_rise"]["operands"]["ties"]), 2
        )
        adjacent_falls = summary["metrics"]["max_adjacent_fall"]["intervals"]
        self.assertEqual(
            adjacent_falls[0]["previous"]["interval_start"],
            "2026-08-03T09:33:00+08:00",
        )
        self.assertEqual(result["coverage"]["status"], "partial")

    def test_summary_does_not_call_a_late_first_row_the_actual_open(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "summary_missing_open"},
        )

        summary = result["summary"]
        self.assertEqual(
            summary["metrics"]["endpoints"]["open"],
            {"status": "unavailable", "reason": "actual_open_not_established"},
        )
        self.assertEqual(
            summary["metrics"]["open_to_close"],
            {"status": "unavailable", "reason": "actual_open_not_established"},
        )

    def test_tracer_never_promotes_coverage_without_later_adjudication(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "qualified"},
        )
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["limitations"][0]["code"],
            "intraday_replay_partial_coverage",
        )

    def test_daily_boundary_agreement_is_visible_at_the_public_seam(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_agreement"},
        )

        self.assertEqual(result["daily_boundary"]["status"], "cross_checked")
        self.assertEqual(
            result["daily_boundary"]["close"],
            {"value": "10.22", "unit": "CNY/share"},
        )
        self.assertEqual(
            result["daily_boundary"]["volume"], {"value": "300", "unit": "shares"}
        )
        self.assertEqual(
            result["daily_boundary"]["amount"], {"value": "3059.00", "unit": "CNY"}
        )

    def test_unexplained_daily_core_conflict_blocks_and_retains_both_sources(
        self,
    ) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_conflict"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["daily_boundary"]["status"], "blocked")
        self.assertEqual(
            result["conflicts"][0]["code"], "daily_boundary_core_value_conflict"
        )
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(len(result["source_operations"]), 2)
        self.assertEqual(len(result["evidence"]), 3)
        self.assertEqual(
            result["summary"]["metrics"]["opening_gap"],
            {
                "status": "unavailable",
                "reason": "daily_boundary_not_cross_checked",
            },
        )

    def test_daily_identity_and_date_mismatch_fail_closed(self) -> None:
        for scenario, source_code in (
            ("daily_security_mismatch", "daily_source_security_mismatch"),
            ("daily_date_mismatch", "daily_source_trading_date_mismatch"),
        ):
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    replay_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["source_errors"][0]["code"], source_code)

    def test_confirmed_suspension_is_a_state_result_without_replay_outputs(
        self,
    ) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "suspension_confirmed"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["replay"]["trading_status"], "confirmed_suspended")
        self.assertEqual(result["daily_boundary"]["status"], "suspended_observation")
        self.assertEqual(result["records"], [])
        self.assertNotIn("summary", result)
        self.assertEqual(len(result["source_operations"]), 2)
        self.assertEqual(len(result["evidence"]), 1)

    def test_ex_right_reference_does_not_replace_actual_previous_close(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "ex_right_only"},
        )

        baselines = result["daily_boundary"]["baselines"]
        self.assertEqual(baselines["actual_unadjusted_close"]["status"], "unavailable")
        self.assertEqual(baselines["ex_right_reference"]["status"], "available")
        self.assertEqual(baselines["comparability"]["status"], "not_comparable")
        unavailable = {item["field"] for item in result["unavailable_fields"]}
        self.assertTrue({"replay.opening_gap", "replay.relative_return"} <= unavailable)
        self.assertEqual(len(result["records"]), 2)

    def test_daily_unit_and_tick_normalization_remains_in_lineage(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={
                "A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_unit_normalization"
            },
        )

        self.assertEqual(result["daily_boundary"]["status"], "cross_checked")
        lineage = result["daily_boundary"]["lineage"]
        self.assertEqual(lineage["daily"]["volume_unit"], "hands")
        self.assertEqual(lineage["daily"]["amount_unit"], "CNY_thousand")
        self.assertEqual(lineage["daily"]["price_minimum_tick"], "0.01")

    def test_qualified_auction_difference_is_explained_not_hidden(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_auction_explained"},
        )

        self.assertEqual(result["daily_boundary"]["status"], "cross_checked")
        open_comparison = result["daily_boundary"]["comparison"]["fields"]["open"]
        self.assertEqual(open_comparison["status"], "explained_difference")
        self.assertEqual(open_comparison["explanation"], "auction_bucketing")
        self.assertEqual(result["conflicts"], [])

    def test_unavailable_daily_operation_degrades_without_dropping_minutes(
        self,
    ) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_source_error"},
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["daily_boundary"]["status"], "unavailable")
        self.assertEqual(result["source_errors"][0]["code"], "daily_source_unavailable")
        self.assertEqual(len(result["records"]), 2)

    def test_single_source_suspension_is_not_confirmed(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={
                "A_SHARE_INTRADAY_REPLAY_SCENARIO": "single_source_suspension"
            },
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["conflicts"][0]["code"], "suspension_not_independently_confirmed"
        )
        self.assertEqual(result["records"], [])
        self.assertNotEqual(result["replay"]["trading_status"], "confirmed_suspended")

    def test_zero_volume_or_flat_price_does_not_imply_suspension(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "zero_volume"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["source_errors"][0]["code"], "traded_zero_volume_ambiguous"
        )

    def test_daily_operation_cannot_claim_the_minute_operation_identity(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_same_operation"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["source_errors"][0]["code"], "daily_operation_not_independent"
        )

    def test_szse_float_noise_is_fixed_and_duplicate_timestamp_is_deterministic(
        self,
    ) -> None:
        result = self.run_task(
            replay_request("SZSE:000001"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "float_noise"},
        )

        self.assertEqual(result["subjects"][0]["security"]["exchange"], "SZSE")
        self.assertEqual(result["records"][1]["ohlc"]["open"]["value"], "10.20")
        self.assertEqual(result["records"][1]["amount"]["value"], "2044.00")

        duplicate = self.run_task(
            replay_request("SZSE:000001"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "duplicate"},
        )
        self.assertEqual(len(duplicate["records"]), 2)
        self.assertEqual(
            duplicate["conflicts"][0]["code"], "duplicate_intraday_interval"
        )
        reversed_duplicate = self.run_task(
            replay_request("SZSE:000001"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "duplicate_reversed"},
        )
        self.assertEqual(duplicate["records"], reversed_duplicate["records"])
        self.assertEqual(duplicate["evidence"], reversed_duplicate["evidence"])
        self.assertEqual(
            duplicate["field_lineage"], reversed_duplicate["field_lineage"]
        )
        conflict_evidence_ids = set(duplicate["conflicts"][0]["evidence_ids"])
        result_evidence_ids = {item["id"] for item in duplicate["evidence"]}
        self.assertTrue(conflict_evidence_ids <= result_evidence_ids)
        self.assertTrue(
            any(item.get("accepted") is False for item in duplicate["evidence"])
        )

    def test_date_and_policy_gates_are_structured_domain_results(self) -> None:
        future = replay_request("SSE:600519")
        future["window"] = {"observed_from": "2026-08-05", "observed_to": "2026-08-05"}
        future_result = self.run_task(future)
        self.assertEqual(future_result["status"], "blocked")
        self.assertEqual(future_result["limitations"][0]["code"], "future_replay_date")

        weekend = replay_request("SSE:600519")
        weekend["as_of"] = "2026-08-02"
        weekend["window"] = {"observed_from": "2026-08-02", "observed_to": "2026-08-02"}
        weekend_result = self.run_task(weekend)
        self.assertEqual(weekend_result["status"], "blocked")
        self.assertEqual(
            weekend_result["limitations"][0]["code"], "non_trading_replay_date"
        )

        policy = replay_request("SSE:600519")
        policy["source_policy"]["allow_experimental"] = False  # type: ignore[index]
        policy_result = self.run_task(policy)
        self.assertEqual(policy_result["status"], "blocked")
        self.assertEqual(
            policy_result["limitations"][0]["code"], "source_policy_not_satisfied"
        )

        historical = replay_request("SSE:600519")
        historical["as_of"] = "2026-08-03"
        historical_result = self.run_task(historical)
        self.assertEqual(historical_result["status"], "blocked")
        self.assertEqual(
            historical_result["source_errors"][0]["code"],
            "source_retrieved_after_research_boundary",
        )

    def test_real_entrypoint_reports_capability_scoped_missing_mootdx(self) -> None:
        request = replay_request("SSE:600519")
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "request.json")
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(
                        REPOSITORY_ROOT
                        / "skill"
                        / "a-share-research"
                        / "scripts"
                        / "entrypoint.py"
                    ),
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
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "missing_optional_dependency"
        )
        self.assertEqual(result["limitations"][0]["capability"], "intraday_replay")
        self.assertEqual(result["limitations"][0]["dependency"], "mootdx")
        self.assertEqual(result["limitations"][0]["required_version"], "0.11.7")

    def test_source_contract_rejects_unknown_semantics_and_adjustment(self) -> None:
        for scenario, source_code in (
            ("unknown_timestamp", "timestamp_semantics_unverified"),
            ("forward_adjusted", "unsupported_price_adjustment"),
        ):
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    replay_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["source_errors"][0]["code"], source_code)

    def test_source_contract_fails_closed_without_calendar_or_zero_no_trade(
        self,
    ) -> None:
        for scenario, source_code in (
            ("missing_calendar", "completed_trading_calendar_unverified"),
            ("short_calendar", "completed_trading_calendar_incomplete"),
            ("no_trade_nonzero", "no_trade_volume_amount_conflict"),
        ):
            with self.subTest(scenario=scenario):
                result = self.run_task(
                    replay_request("SSE:600519"),
                    environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": scenario},
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["source_errors"][0]["code"], source_code)

    def test_interval_cannot_end_after_source_retrieval(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "after_retrieval"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["source_errors"][0]["code"], "source_interval_after_retrieval"
        )

    def test_qualified_hands_are_normalized_to_whole_shares(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "hands"},
        )

        self.assertEqual(
            result["records"][0]["volume"], {"value": "100", "unit": "shares"}
        )
        self.assertEqual(
            result["records"][1]["volume"], {"value": "200", "unit": "shares"}
        )

    def test_interval_end_semantics_are_preserved_and_normalized(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519"),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "interval_end"},
        )

        self.assertEqual(result["records"][1]["timestamp_semantics"], "interval_end")
        self.assertEqual(
            result["records"][1]["interval_start"], "2026-08-03T09:31:00+08:00"
        )
        self.assertEqual(
            result["records"][1]["interval_end"], "2026-08-03T09:32:00+08:00"
        )


if __name__ == "__main__":
    unittest.main()
