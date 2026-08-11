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
FIXTURE_CLI = REPOSITORY_ROOT / "tests/fixtures/intraday_replay/fixture_cli.py"
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


def replay_request(
    security: str,
    *,
    as_of: str = "2026-08-04",
    replay_date: str = "2026-08-03",
    allow_experimental: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "intraday_replay",
        "subjects": [{"security": security}],
        "as_of": as_of,
        "window": {"observed_from": replay_date, "observed_to": replay_date},
        "parameters": {},
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


class IntradayReplayTracerCliTests(unittest.TestCase):
    def run_task(
        self, request: dict[str, object], *, environment: dict[str, str] | None = None
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory, "request.json")
            path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(FIXTURE_CLI), "run", "--request", str(path)],
                cwd=REPOSITORY_ROOT,
                env={**os.environ, **(environment or {})},
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(set(result), FORMAL_KEYS)
        self.assertEqual(result["plan"]["task_type"], "intraday_replay")
        return result

    def scenario(self, name: str, *, security: str = "SSE:600519") -> dict[str, Any]:
        return self.run_task(
            replay_request(security),
            environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": name},
        )

    def claim(
        self, result: dict[str, Any], kind: str = "intraday_replay"
    ) -> dict[str, Any]:
        return next(
            item["payload"] for item in result["claims"] if item["kind"] == kind
        )

    def normalized(self, result: dict[str, Any]) -> dict[str, Any]:
        return next(
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "intraday_replay_normalized_records"
        )

    def raw(self, result: dict[str, Any]) -> dict[str, Any]:
        return next(
            item["payload"]
            for item in result["observations"]
            if item["payload"].get("kind") == "intraday_replay_source_batch"
        )

    def codes(self, result: dict[str, Any]) -> set[str]:
        return {
            item["code"]
            for field in ("conflicts", "source_errors", "limitations")
            for item in result[field]
        }

    def test_complete_coverage_separates_sessions_break_and_auctions(self) -> None:
        facts = self.normalized(self.scenario("complete"))
        coverage = facts["coverage"]
        self.assertEqual(coverage["status"], "complete")
        self.assertTrue(coverage["lunch_break"]["excluded_from_coverage"])
        self.assertEqual(coverage["closing_auction"]["status"], "observed")

    def test_complete_coverage_accepts_szse_at_the_same_public_seam(self) -> None:
        result = self.scenario("complete", security="SZSE:000001")
        self.assertEqual(self.raw(result)["contract"]["security"], "SZSE:000001")

    def test_deterministic_replay_result_has_no_agent_prediction_layer(self) -> None:
        result = self.scenario("daily_agreement")
        self.assertNotIn("prediction", json.dumps(result).lower())

    def test_partial_closing_subinterval_is_not_claimed_complete(self) -> None:
        coverage = self.normalized(self.scenario("subinterval_partial"))["coverage"]
        self.assertEqual(coverage["status"], "partial")
        self.assertNotEqual(coverage["closing_auction"]["status"], "observed")

    def test_unknown_session_contract_fails_closed(self) -> None:
        result = self.scenario("unknown_session")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("session_semantics_unverified", self.codes(result))

    def test_partial_coverage_retains_qualified_no_trade_and_true_missing(self) -> None:
        facts = self.normalized(self.scenario("partial_no_trade"))
        self.assertEqual(facts["coverage"]["status"], "partial")
        self.assertIn("no_trade", {item["trade_state"] for item in facts["records"]})
        self.assertTrue(facts["coverage"]["missing_intervals"])

    def test_indeterminate_coverage_is_structured_blocked(self) -> None:
        result = self.scenario("indeterminate")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.normalized(result)["coverage"]["status"], "indeterminate")
        self.assertEqual(
            [item for item in result["claims"] if item["kind"] == "intraday_replay"], []
        )

    def test_unknown_auction_semantics_fail_closed(self) -> None:
        result = self.scenario("unknown_auction")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("auction_semantics_unverified", self.codes(result))

    def test_zero_volume_alone_is_not_no_trade_proof(self) -> None:
        result = self.scenario("zero_volume")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("traded_zero_volume_ambiguous", self.codes(result))
        self.assertTrue(self.raw(result)["source_records"])

    def test_current_day_before_session_end_is_blocked_at_public_seam(self) -> None:
        result = self.run_task(
            replay_request("SSE:600519", as_of="2026-08-03", replay_date="2026-08-03"),
            environment={"A_SHARE_INTRADAY_REPLAY_NOW": "2026-08-03T10:00:00+08:00"},
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("replay_session_not_completed", self.codes(result))

    def test_experimental_replay_is_versioned_sorted_and_chart_ready(self) -> None:
        result = self.scenario("daily_agreement")
        facts = self.claim(result)
        starts = [item["interval_start"] for item in facts["records"]]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(
            all(
                item["volume"]["unit"] == "shares" and item["amount"]["unit"] == "CNY"
                for item in facts["records"]
            )
        )

    def test_summary_keeps_bounded_counts_and_unavailable_endpoint_metrics_explicit(
        self,
    ) -> None:
        summary = self.claim(self.scenario("summary_missing_open"))["summary"]
        self.assertEqual(
            summary["counts"]["record_count"],
            len(self.claim(self.scenario("summary_missing_open"))["records"]),
        )
        self.assertEqual(
            summary["metrics"]["endpoints"]["open"]["status"], "unavailable"
        )

    def test_summary_recomputes_ties_and_never_crosses_no_trade_or_lunch(self) -> None:
        summary = self.claim(self.scenario("summary_metrics"))["summary"]
        self.assertEqual(summary["scope"]["lunch_break_excluded"], True)
        self.assertGreater(summary["counts"]["proven_no_trade_intervals"], 0)

    def test_summary_does_not_call_a_late_first_row_the_actual_open(self) -> None:
        summary = self.claim(self.scenario("summary_missing_open"))["summary"]
        self.assertEqual(summary["metrics"]["opening_gap"]["status"], "unavailable")

    def test_tracer_never_promotes_coverage_without_later_adjudication(self) -> None:
        result = self.scenario("short_calendar")
        self.assertTrue(self.raw(result)["source_records"])
        self.assertNotIn(
            "intraday_replay_normalized_records",
            {item["payload"].get("kind") for item in result["observations"]},
        )

    def test_daily_boundary_agreement_is_visible_at_the_public_seam(self) -> None:
        result = self.scenario("daily_agreement")
        self.assertEqual(
            self.claim(result)["daily_boundary"]["status"], "cross_checked"
        )
        self.assertEqual(
            self.claim(result, "intraday_daily_boundary")["comparison"]["status"],
            "agree",
        )

    def test_unexplained_daily_core_conflict_blocks_and_retains_both_sources(
        self,
    ) -> None:
        result = self.scenario("daily_conflict")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("daily_boundary_core_value_conflict", self.codes(result))
        self.assertEqual(len(result["observations"]), 3)

    def test_daily_identity_and_date_mismatch_fail_closed(self) -> None:
        for scenario, code in (
            ("daily_security_mismatch", "daily_source_security_mismatch"),
            ("daily_date_mismatch", "daily_source_trading_date_mismatch"),
        ):
            with self.subTest(scenario=scenario):
                self.assertIn(code, self.codes(self.scenario(scenario)))

    def test_confirmed_suspension_is_a_state_result_without_replay_outputs(
        self,
    ) -> None:
        facts = self.claim(self.scenario("suspension_confirmed"))
        self.assertEqual(facts["replay"]["trading_status"], "confirmed_suspended")
        self.assertEqual(facts["records"], [])

    def test_ex_right_reference_does_not_replace_actual_previous_close(self) -> None:
        baselines = self.claim(
            self.scenario("ex_right_only"), "intraday_daily_boundary"
        )["baselines"]
        self.assertEqual(baselines["actual_unadjusted_close"]["status"], "unavailable")
        self.assertEqual(baselines["ex_right_reference"]["status"], "available")

    def test_daily_unit_and_tick_normalization_remains_in_lineage(self) -> None:
        boundary = self.claim(
            self.scenario("daily_unit_normalization"), "intraday_daily_boundary"
        )
        self.assertEqual(boundary["volume"]["unit"], "shares")
        self.assertEqual(boundary["amount"]["unit"], "CNY")
        self.assertEqual(boundary["lineage"]["daily"]["amount_scale"], "1000")

    def test_qualified_auction_difference_is_explained_not_hidden(self) -> None:
        boundary = self.claim(
            self.scenario("daily_auction_explained"), "intraday_daily_boundary"
        )
        self.assertTrue(boundary["comparison"]["explained_differences"])

    def test_unavailable_daily_operation_degrades_without_dropping_minutes(
        self,
    ) -> None:
        result = self.scenario("daily_source_error")
        self.assertEqual(result["status"], "limited")
        self.assertTrue(self.claim(result)["records"])
        self.assertIn("daily_source_unavailable", self.codes(result))

    def test_single_source_suspension_is_not_confirmed(self) -> None:
        result = self.scenario("single_source_suspension")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("suspension_not_independently_confirmed", self.codes(result))

    def test_zero_volume_or_flat_price_does_not_imply_suspension(self) -> None:
        result = self.scenario("zero_volume")
        self.assertIn("traded_zero_volume_ambiguous", self.codes(result))

    def test_daily_operation_cannot_claim_the_minute_operation_identity(self) -> None:
        result = self.scenario("daily_same_operation")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("daily_operation_not_independent", self.codes(result))

    def test_szse_float_noise_is_fixed_and_duplicate_timestamp_is_deterministic(
        self,
    ) -> None:
        normalized_by_scenario = {}
        for scenario in ("float_noise", "duplicate", "duplicate_reversed"):
            result = self.scenario(scenario, security="SZSE:000001")
            facts = self.normalized(result)
            normalized_by_scenario[scenario] = facts
            self.assertEqual(
                [item["interval_start"] for item in facts["records"]],
                sorted(item["interval_start"] for item in facts["records"]),
            )
        self.assertEqual(
            normalized_by_scenario["duplicate"],
            normalized_by_scenario["duplicate_reversed"],
        )

    def test_date_and_policy_gates_are_structured_domain_results(self) -> None:
        policy = self.run_task(replay_request("SSE:600519", allow_experimental=False))
        self.assertIn("source_policy_not_satisfied", self.codes(policy))
        invalid = self.run_task(
            replay_request("SSE:600519", as_of="2026-08-03", replay_date="2026-08-03"),
            environment={"A_SHARE_INTRADAY_REPLAY_NOW": "2026-08-03T10:00:00+08:00"},
        )
        self.assertIn("replay_session_not_completed", self.codes(invalid))

    def test_real_entrypoint_reports_capability_scoped_missing_mootdx(self) -> None:
        from tests.test_direct_intraday_runtime import invoke_copied_installed

        result = invoke_copied_installed(replay_request("SSE:600519"))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("missing_optional_dependency", self.codes(result))
        message = next(
            item["message"]
            for item in result["limitations"]
            if item["code"] == "missing_optional_dependency"
        )
        self.assertIn("capability=intraday_replay", message)
        self.assertIn("dependency=mootdx", message)
        self.assertIn("required_version=0.11.7", message)

    def test_source_contract_rejects_unknown_semantics_and_adjustment(self) -> None:
        for scenario, code in (
            ("unknown_timestamp", "timestamp_semantics_unverified"),
            ("forward_adjusted", "unsupported_price_adjustment"),
        ):
            self.assertIn(code, self.codes(self.scenario(scenario)))

    def test_source_contract_fails_closed_without_calendar_or_zero_no_trade(
        self,
    ) -> None:
        for scenario, code in (
            ("missing_calendar", "completed_trading_calendar_unverified"),
            ("short_calendar", "completed_trading_calendar_incomplete"),
            ("no_trade_nonzero", "no_trade_volume_amount_conflict"),
        ):
            result = self.scenario(scenario)
            self.assertIn(code, self.codes(result))
            self.assertTrue(self.raw(result)["source_records"])

    def test_interval_cannot_end_after_source_retrieval(self) -> None:
        result = self.scenario("after_retrieval")
        self.assertIn("source_interval_after_retrieval", self.codes(result))
        self.assertTrue(self.raw(result)["source_records"])

    def test_qualified_hands_are_normalized_to_whole_shares(self) -> None:
        records = self.normalized(self.scenario("hands"))["records"]
        self.assertEqual(records[0]["volume"]["unit"], "shares")
        self.assertEqual(records[0]["volume"]["value"], "100")

    def test_interval_end_semantics_are_preserved_and_normalized(self) -> None:
        records = self.normalized(self.scenario("interval_end"))["records"]
        self.assertIn("interval_end", {item["timestamp_semantics"] for item in records})
        self.assertTrue(
            all(item["interval_start"] < item["interval_end"] for item in records)
        )


if __name__ == "__main__":
    unittest.main()
