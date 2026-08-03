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


def intraday_request(security: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "intraday_market_signal",
        "subjects": [{"security": security}],
        "as_of": "2026-08-03",
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


if __name__ == "__main__":
    unittest.main()
