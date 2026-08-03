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
        self.assertEqual(result["coverage"]["status"], "not_adjudicated")
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
        self.assertEqual(
            result["field_lineage"]["records[0].ohlc.close"]["evidence_ids"],
            [result["records"][0]["evidence_ids"][0]],
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

    def test_real_entrypoint_returns_blocked_json_without_a_default_source(
        self,
    ) -> None:
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
            result["limitations"][0]["code"], "intraday_replay_source_unavailable"
        )

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
