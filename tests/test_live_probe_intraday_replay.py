from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPOSITORY_ROOT / "tests" / "live_probe_intraday_replay.py"
SPEC = importlib.util.spec_from_file_location("live_probe_intraday_replay", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class IntradayReplayLiveProbeTests(unittest.TestCase):
    def test_request_is_the_public_replay_shape(self) -> None:
        request = probe.build_probe_request("SSE:600519", "2026-08-04", "2026-08-03")
        self.assertEqual(request["task_type"], "intraday_replay")
        self.assertEqual(
            request["window"],
            {"observed_from": "2026-08-03", "observed_to": "2026-08-03"},
        )
        self.assertTrue(request["source_policy"]["allow_experimental"])  # type: ignore[index]
        self.assertFalse(request["source_policy"]["allow_credentials"])  # type: ignore[index]

    def test_protocol_reduction_drops_raw_provider_details(self) -> None:
        payload = {
            "schema_version": "1.0",
            "task_type": "intraday_replay",
            "status": "limited",
            "replay": {"record_count": 3, "auction_result_count": 1},
            "coverage": {"status": "partial"},
            "daily_boundary": {"status": "unavailable"},
            "source_operations": [
                {"operation_id": "mootdx_intraday_replay@1"},
                {"operation_id": "exchange_intraday_replay_daily@1"},
            ],
            "source_errors": [
                {
                    "source_operation": "https://provider.example/?token=secret",
                    "code": "upstream_unavailable",
                    "message": "raw response secret",
                }
            ],
            "limitations": [{"code": "experimental_intraday_replay_source"}],
            "conflicts": [],
        }

        result = probe._result_from_process(0, json.dumps(payload))

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["coverage_status"], "partial")
        self.assertEqual(result["record_count"], 3)
        serialized = json.dumps(result)
        self.assertNotIn("provider.example", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("raw response", serialized)
        self.assertEqual(
            result["failures"],
            [
                {"source_operation": "unknown", "code": "upstream_unavailable"},
                {"source_operation": "unknown", "code": "probe_failure"},
            ],
        )

    def test_protocol_reduction_rejects_a_wrong_subject_or_replay_date(self) -> None:
        payload = {
            "schema_version": "1.0",
            "task_type": "intraday_replay",
            "status": "blocked",
            "subjects": [{"security": "SSE:600000"}],
        }

        result = probe._result_from_process(
            0,
            json.dumps(payload),
            expected_security="SSE:600519",
            expected_replay_date="2026-08-03",
        )

        self.assertEqual(result["failures"][0]["code"], "probe_protocol_failure")  # type: ignore[index]

    def test_probe_requires_confirmation_and_both_exchanges(self) -> None:
        with patch.object(probe, "run_probe") as run_probe:
            with self.assertRaises(SystemExit) as raised:
                probe.main(["--as-of", "2026-08-04", "--replay-date", "2026-08-03"])
            self.assertEqual(raised.exception.code, 2)
            run_probe.assert_not_called()

        with patch.object(probe, "run_probe") as run_probe:
            with self.assertRaises(SystemExit) as raised:
                probe.main(
                    [
                        "--confirm-live",
                        "--as-of",
                        "2026-08-04",
                        "--replay-date",
                        "2026-08-03",
                        "--sse",
                        "SZSE:000001",
                    ]
                )
            self.assertEqual(raised.exception.code, 2)
            run_probe.assert_not_called()

    def test_run_probe_reports_both_subjects_without_network_in_test(self) -> None:
        with patch.object(
            probe,
            "_run_public_request",
            return_value=(
                0,
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "task_type": "intraday_replay",
                        "status": "blocked",
                        "coverage": {"status": "not_adjudicated"},
                        "replay": {"record_count": 0, "auction_result_count": 0},
                        "daily_boundary": {"status": "unavailable"},
                        "source_operations": [],
                        "source_errors": [],
                        "limitations": [{"code": "missing_optional_dependency"}],
                        "conflicts": [],
                    }
                ),
            ),
        ) as run_request:
            result = probe.run_probe("2026-08-04", "2026-08-03")

        self.assertEqual(
            [item["security"] for item in result["observations"]],
            ["SSE:600519", "SZSE:000001"],
        )
        self.assertEqual(run_request.call_count, 2)
        self.assertFalse(result["scope"]["ordinary_ci"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
