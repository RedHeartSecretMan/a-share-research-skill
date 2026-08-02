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
    REPOSITORY_ROOT / "tests" / "fixtures" / "market_series" / "fixture_cli.py"
)


class MarketTrendProcessTests(unittest.TestCase):
    def run_market_trend(
        self,
        scenario: str = "bluefocus",
        adjustment: str = "unadjusted",
        trading_days: int = 10,
        as_of: str = "2026-08-02",
    ) -> dict[str, object]:
        request = {
            "schema_version": "1.0",
            "task_type": "market_trend",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": as_of,
            "window": {"trading_days": trading_days},
            "parameters": {"adjustment": adjustment},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "research-task.json")
            request_path.write_text(
                json.dumps(request, ensure_ascii=False),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "A_SHARE_RESEARCH_TEST_SCENARIO": scenario,
                },
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_bluefocus_ten_completed_sessions_form_a_cross_checked_trend(self) -> None:
        result = self.run_market_trend()

        self.assertEqual(result["task_type"], "market_trend")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["subjects"][0]["security"],
            {"exchange": "SZSE", "code": "300058", "type": "A_SHARE"},
        )
        self.assertEqual(
            result["window"],
            {
                "requested_trading_days": 10,
                "actual_trading_days": 10,
                "start": "2026-07-20",
                "end": "2026-07-31",
            },
        )
        self.assertEqual(len(result["series"]), 10)
        self.assertEqual(
            result["series"][0],
            {
                "trading_date": "2026-07-20",
                "open": "12.23",
                "high": "12.64",
                "low": "11.65",
                "close": "12.14",
                "volume": "299612200",
                "volume_unit": "shares",
                "adjustment": "unadjusted",
                "evidence_ids": [
                    "bar-szse_daily_line@1-SZSE:300058-2026-07-20",
                    "bar-tencent_daily_line@1-SZSE:300058-2026-07-20",
                ],
            },
        )
        self.assertEqual(result["series"][-1]["close"], "14.35")
        self.assertEqual(result["series"][-1]["volume"], "631845500")
        self.assertEqual(
            result["metrics"],
            {
                "cumulative_return": {"value": "18.2043", "unit": "percent"},
                "maximum_drawdown": {"value": "-10.9611", "unit": "percent"},
                "annualized_volatility": {
                    "value": "121.3998",
                    "unit": "percent",
                    "basis": "sample_stddev_of_daily_simple_returns_sqrt_252",
                },
                "up_sessions": 5,
                "down_sessions": 4,
                "unchanged_sessions": 0,
                "volume_change": {
                    "value": "30.7131",
                    "unit": "percent",
                    "basis": "last_5_session_average_vs_first_5_session_average",
                },
            },
        )
        self.assertEqual(result["conclusion"]["close_trend"], "up")
        self.assertEqual(result["corporate_actions"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(len(result["evidence"]), 22)
        self.assertIn(
            "experimental_market_series",
            {item["code"] for item in result["limitations"]},
        )

    def test_unadjusted_window_with_corporate_action_blocks_trend_metrics(self) -> None:
        result = self.run_market_trend("corporate_action")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["metrics"], {})
        self.assertEqual(result["conclusion"]["close_trend"], "unresolved")
        self.assertEqual(len(result["corporate_actions"]), 1)
        self.assertEqual(
            result["limitations"][-1]["code"],
            "corporate_action_requires_adjusted_series",
        )

    def test_missing_fallback_session_blocks_and_retains_conflict_evidence(
        self,
    ) -> None:
        result = self.run_market_trend("missing_session")

        self.assertEqual(result["status"], "blocked")
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["code"], "market_series_missing_session")
        self.assertEqual(conflict["trading_date"], "2026-07-24")
        retained_evidence_ids = {item["id"] for item in result["evidence"]}
        self.assertTrue(set(conflict["evidence_ids"]).issubset(retained_evidence_ids))

    def test_one_daily_value_conflict_blocks_the_entire_trend(self) -> None:
        result = self.run_market_trend("value_conflict")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["metrics"], {})
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["code"], "market_series_value_conflict")
        self.assertEqual(conflict["trading_date"], "2026-07-24")
        self.assertEqual(conflict["fields"], ["close"])

    def test_suspended_session_is_explicitly_rejected(self) -> None:
        result = self.run_market_trend("suspended_session")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][-1]["code"],
            "suspended_session_in_requested_range",
        )
        self.assertEqual(
            result["rejected_observations"][0]["observation"]["trading_date"],
            "2026-07-24",
        )
        self.assertEqual(
            result["rejected_observations"][0]["observation"]["trading_status"],
            "suspended",
        )

    def test_wrong_security_payload_is_a_source_error_not_empty_market_data(
        self,
    ) -> None:
        result = self.run_market_trend("wrong_security")

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "wrong_security_payload",
            {item["code"] for item in result["source_errors"]},
        )

    def test_empty_official_observation_is_an_explicit_source_error(self) -> None:
        result = self.run_market_trend("empty_response")

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "empty_observation",
            {item["code"] for item in result["source_errors"]},
        )

    def test_bluefocus_forward_adjusted_trend_uses_two_adjusted_sources(self) -> None:
        result = self.run_market_trend(adjustment="forward_adjusted")

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["series"]), 10)
        self.assertTrue(
            all(item["adjustment"] == "forward_adjusted" for item in result["series"])
        )
        self.assertEqual(
            result["metrics"]["cumulative_return"],
            {"value": "18.2043", "unit": "percent"},
        )
        market_operations = {
            item["source_operation"]
            for item in result["evidence"]
            if item["source_role"] == "market_observation"
        }
        self.assertEqual(
            market_operations,
            {
                "eastmoney_forward_adjusted_daily_line@1",
                "tencent_forward_adjusted_daily_line@1",
            },
        )

    def test_two_session_window_marks_sample_volatility_not_computable(self) -> None:
        result = self.run_market_trend(trading_days=2)

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["window"]["actual_trading_days"], 2)
        self.assertEqual(
            result["metrics"]["cumulative_return"],
            {"value": "19.9833", "unit": "percent"},
        )
        self.assertEqual(
            result["metrics"]["annualized_volatility"],
            {
                "status": "not_computable",
                "reason": "sample volatility requires at least two daily returns",
            },
        )
        volatility_calculation = next(
            item
            for item in result["calculations"]
            if item["id"] == "annualized_volatility"
        )
        self.assertEqual(volatility_calculation["status"], "not_computable")

    def test_forward_adjusted_series_retains_real_corporate_action_annotation(
        self,
    ) -> None:
        result = self.run_market_trend(
            scenario="adjusted_corporate_action",
            adjustment="forward_adjusted",
            trading_days=5,
            as_of="2026-06-26",
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["series"]), 5)
        self.assertEqual(len(result["corporate_actions"]), 1)
        self.assertEqual(result["corporate_actions"][0]["trading_date"], "2026-06-26")
        self.assertEqual(
            result["corporate_actions"][0]["details"]["FHcontent"],
            "10派0.1元",
        )
        self.assertIn(
            "forward_adjusted_series_contains_corporate_action",
            {item["code"] for item in result["limitations"]},
        )


class EtfMarketProcessTests(unittest.TestCase):
    def run_etf_market(self, scenario: str = "etf_market") -> dict[str, object]:
        request = {
            "schema_version": "1.0",
            "task_type": "etf_market",
            "subjects": [{"clue": "510050"}],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "research-task.json")
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
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "A_SHARE_RESEARCH_TEST_SCENARIO": scenario,
                },
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_510050_completed_quote_cross_checks_identity_price_and_volume(
        self,
    ) -> None:
        result = self.run_etf_market()

        self.assertEqual(result["task_type"], "etf_market")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["subjects"],
            [
                {
                    "security": {
                        "exchange": "SSE",
                        "code": "510050",
                        "type": "ETF",
                    },
                    "name": "上证50ETF华夏",
                    "fund_manager": "华夏基金管理有限公司",
                    "listing_date": "2005-02-23",
                }
            ],
        )
        self.assertEqual(
            result["quote"],
            {
                "trading_date": "2026-07-31",
                "observed_at": "2026-07-31T16:29:01+08:00",
                "market_state": "completed",
                "open": "3.0500",
                "high": "3.0590",
                "low": "3.0260",
                "last": "3.0330",
                "previous_close": "3.0260",
                "change_rate": {"value": "0.23", "unit": "percent"},
                "volume": {"value": "672576064", "unit": "shares"},
                "amount": {"value": "2046505657", "unit": "CNY"},
                "volume_cross_check": {
                    "status": "consistent_with_lot_rounding",
                    "difference_shares": "36",
                },
                "evidence_ids": [
                    "etf-sse_etf_snapshot@1-SSE:510050-2026-07-31",
                    "bar-tencent_daily_line@1-SSE:510050-2026-07-31",
                ],
            },
        )
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(len(result["evidence"]), 3)
        self.assertIn(
            "experimental_etf_market_sources",
            {item["code"] for item in result["limitations"]},
        )

    def test_etf_price_conflict_blocks_the_quote(self) -> None:
        result = self.run_etf_market("etf_value_conflict")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["quote"]["status"], "unresolved")
        self.assertEqual(result["conflicts"][0]["code"], "etf_quote_value_conflict")
        self.assertEqual(result["conflicts"][0]["fields"], ["last"])


if __name__ == "__main__":
    unittest.main()
