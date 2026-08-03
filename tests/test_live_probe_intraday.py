from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPOSITORY_ROOT / "tests" / "live_probe_intraday.py"
SPEC = importlib.util.spec_from_file_location("live_probe_intraday", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
live_probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_probe)


class IntradayLiveProbeTests(unittest.TestCase):
    def test_sanitize_failure_drops_raw_provider_details_and_credentials(self) -> None:
        failure = live_probe.sanitize_failure(
            {
                "source_operation": "https://provider.example/?token=secret-value",
                "code": "upstream_http_error",
                "message": "Authorization: Bearer secret-value response body=raw-provider-payload",
            }
        )

        self.assertEqual(failure["code"], "upstream_http_error")
        self.assertEqual(failure["source_operation"], "unknown")
        self.assertNotIn("secret-value", json.dumps(failure))
        self.assertNotIn("raw-provider-payload", json.dumps(failure))
        self.assertNotIn("Authorization", json.dumps(failure))

    def test_probe_report_contract_contains_dated_observation_fields(self) -> None:
        result = {
            "status": "limited",
            "subject": {
                "security": {"exchange": "SSE", "code": "600519", "type": "A_SHARE"}
            },
            "trading_date": "2026-08-03",
            "session_state": "continuous",
            "trading_status": "traded",
            "price_type": "latest_traded",
            "source_operations": [
                "tongdaxin_intraday_snapshot@1",
                "tencent_intraday_snapshot@1",
            ],
            "observation_times": {
                "tongdaxin_baseline": "2026-08-03T10:30:00+08:00",
                "tencent_cross_check": "2026-08-03T10:29:58+08:00",
                "retrieved_at": "2026-08-03T10:30:05+08:00",
                "pair_gap_seconds": "2",
            },
            "snapshot": {
                "latest_price": {"value": "1680.25", "unit": "CNY/share"},
                "open": {"value": "1675.00", "unit": "CNY/share"},
                "high": {"value": "1688.00", "unit": "CNY/share"},
                "low": {"value": "1670.50", "unit": "CNY/share"},
                "previous_close": {
                    "status": "unavailable",
                    "reported_value": "1668.00",
                    "unit": "CNY/share",
                },
                "cumulative_volume": {"value": "1234500", "unit": "shares"},
            },
            "conflicts": [],
            "source_errors": [],
            "limitations": [],
        }

        observation = live_probe.summarize_observation(
            "SSE:600519", "2026-08-03", result
        )

        self.assertEqual(
            observation["date"], {"requested": "2026-08-03", "observed": "2026-08-03"}
        )
        self.assertEqual(
            observation["source_identity"],
            ["tongdaxin_intraday_snapshot@1", "tencent_intraday_snapshot@1"],
        )
        self.assertEqual(observation["session"]["state"], "continuous")  # type: ignore[index]
        self.assertEqual(observation["price_agreement"]["status"], "agreed")  # type: ignore[index]
        self.assertEqual(observation["units"]["latest_price"], "CNY/share")  # type: ignore[index]
        self.assertEqual(observation["failures"], [])

    def test_probe_rejects_json_without_research_result_contract(self) -> None:
        incomplete_payloads = [
            {},
            {"schema_version": "1.0", "status": "limited"},
            {
                "schema_version": "1.0",
                "task_type": "intraday_market_signal",
                "status": "limited",
            },
            {
                "schema_version": "1.0",
                "task_type": "intraday_market_signal",
                "status": "supported",
            },
            {
                "schema_version": "1.0",
                "task_type": "intraday_market_signal",
                "status": "blocked",
            },
        ]
        for payload in incomplete_payloads:
            with self.subTest(payload=payload):
                result = live_probe._result_from_process(0, json.dumps(payload), "")
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(
                    result["_failures"][0]["code"], "probe_protocol_failure"
                )

        valid_blocked = {
            "schema_version": "1.0",
            "task_type": "intraday_market_signal",
            "status": "blocked",
            "subjects": [{"security": "SSE:600519"}],
            "evidence": [],
            "limitations": [{"code": "missing_optional_dependency"}],
        }
        self.assertEqual(
            live_probe._result_from_process(0, json.dumps(valid_blocked), ""),
            valid_blocked,
        )
        blocked_with_exceeded_gap = {
            **valid_blocked,
            "observation_times": {
                "tongdaxin_baseline": "2026-08-03T10:30:00+08:00",
                "tencent_cross_check": "2026-08-03T10:28:00+08:00",
                "retrieved_at": "2026-08-03T10:30:05+08:00",
                "pair_gap_seconds": "120",
            },
        }
        blocked_result = live_probe._result_from_process(
            0, json.dumps(blocked_with_exceeded_gap), ""
        )
        self.assertEqual(
            live_probe.summarize_observation(
                "SSE:600519", "2026-08-03", blocked_result
            )["timing"]["pair_gap_seconds"],
            "120",
        )

        valid_limited = {
            "schema_version": "1.0",
            "task_type": "intraday_market_signal",
            "status": "limited",
            "subject": {
                "security": {
                    "exchange": "SSE",
                    "code": "600519",
                    "type": "A_SHARE",
                }
            },
            "as_of": "2026-08-03",
            "trading_date": "2026-08-03",
            "session_state": "continuous",
            "trading_status": "traded",
            "price_type": "latest_traded",
            "source_operations": [
                "tongdaxin_intraday_snapshot@1",
                "tencent_intraday_snapshot@1",
            ],
            "observation_times": {
                "tongdaxin_baseline": "2026-08-03T10:30:00+08:00",
                "tencent_cross_check": "2026-08-03T10:29:58+08:00",
                "retrieved_at": "2026-08-03T10:30:05+08:00",
                "pair_gap_seconds": "2",
            },
            "snapshot": {
                "latest_price": {"value": "1680.25", "unit": "CNY/share"},
                "open": {"value": "1675.00", "unit": "CNY/share"},
                "high": {"value": "1688.00", "unit": "CNY/share"},
                "low": {"value": "1670.50", "unit": "CNY/share"},
                "previous_close": {
                    "status": "unavailable",
                    "reported_value": "1668.00",
                    "unit": "CNY/share",
                },
                "cumulative_volume": {"value": "1234500", "unit": "shares"},
                "cumulative_amount": {
                    "value": "2071234567.89",
                    "unit": "CNY",
                },
            },
            "evidence": [
                {
                    "id": "intraday-test-1",
                    "source_operation": "tongdaxin_intraday_snapshot@1",
                    "subject": {"security": "SSE:600519"},
                    "observation": {"kind": "intraday_quote"},
                    "locator": {"uri": "provider://redacted"},
                    "retrieved_at": "2026-08-03T10:30:05+08:00",
                },
                {
                    "id": "intraday-test-2",
                    "source_operation": "tencent_intraday_snapshot@1",
                    "subject": {"security": "SSE:600519"},
                    "observation": {"kind": "intraday_core_price_cross_check"},
                    "locator": {"uri": "provider://redacted-cross-check"},
                    "retrieved_at": "2026-08-03T10:30:05+08:00",
                },
            ],
            "field_lineage": {
                "subject": {"evidence_ids": ["intraday-test-1", "intraday-test-2"]}
            },
            "brief": {
                "status": "limited",
                "evidence_ids": ["intraday-test-1", "intraday-test-2"],
            },
            "conflicts": [],
            "source_errors": [],
            "limitations": [{"code": "experimental_intraday_sources"}],
        }
        self.assertEqual(
            live_probe._result_from_process(
                0,
                json.dumps(valid_limited),
                "",
                expected_security="SSE:600519",
                expected_as_of="2026-08-03",
            ),
            valid_limited,
        )
        missing_cross_source = {
            **valid_limited,
            "evidence": valid_limited["evidence"][:1],  # type: ignore[index]
        }
        missing_cross_result = live_probe._result_from_process(
            0, json.dumps(missing_cross_source), ""
        )
        self.assertEqual(missing_cross_result["status"], "blocked")
        self.assertEqual(
            missing_cross_result["_failures"][0]["code"],
            "probe_protocol_failure",
        )
        for incomplete_lineage in (
            {**valid_limited, "evidence": []},
            {**valid_limited, "field_lineage": {}},
            {**valid_limited, "brief": {"status": "blocked"}},
        ):
            incomplete_lineage_result = live_probe._result_from_process(
                0, json.dumps(incomplete_lineage), ""
            )
            self.assertEqual(incomplete_lineage_result["status"], "blocked")
            self.assertEqual(
                incomplete_lineage_result["_failures"][0]["code"],
                "probe_protocol_failure",
            )
        for mismatched in (
            {**valid_limited, "as_of": "2026-08-02", "trading_date": "2026-08-02"},
            {
                **valid_limited,
                "subject": {
                    "security": {
                        "exchange": "SZSE",
                        "code": "000001",
                        "type": "A_SHARE",
                    }
                },
            },
        ):
            mismatched_result = live_probe._result_from_process(
                0,
                json.dumps(mismatched),
                "",
                expected_security="SSE:600519",
                expected_as_of="2026-08-03",
            )
            self.assertEqual(mismatched_result["status"], "blocked")
            self.assertEqual(
                mismatched_result["_failures"][0]["code"],
                "probe_protocol_failure",
            )
        malformed_envelopes = [
            {
                **valid_limited,
                "subject": {
                    "security": {
                        "exchange": "SSE",
                        "code": "Bearer",
                        "type": "A_SHARE",
                    }
                },
            },
            {**valid_limited, "session_state": "Bearer:secret"},
            {**valid_limited, "trading_status": "Bearer:secret"},
            {**valid_limited, "price_type": "Bearer:secret"},
            {**valid_limited, "session_state": "midday_break"},
            {**valid_limited, "as_of": "Bearer:secret"},
            {
                **valid_limited,
                "observation_times": {
                    **valid_limited["observation_times"],  # type: ignore[arg-type]
                    "retrieved_at": "Bearer:secret",
                },
            },
            {
                **valid_limited,
                "observation_times": {
                    **valid_limited["observation_times"],  # type: ignore[arg-type]
                    "pair_gap_seconds": "61",
                },
            },
        ]
        for malformed_envelope in malformed_envelopes:
            malformed_result = live_probe._result_from_process(
                0, json.dumps(malformed_envelope), ""
            )
            self.assertEqual(malformed_result["status"], "blocked")
            self.assertEqual(
                malformed_result["_failures"][0]["code"],
                "probe_protocol_failure",
            )
        incomplete_snapshot = {
            **valid_limited,
            "snapshot": {
                **valid_limited["snapshot"],  # type: ignore[arg-type]
                "open": {"value": "1"},
            },
        }
        incomplete_snapshot_result = live_probe._result_from_process(
            0, json.dumps(incomplete_snapshot), ""
        )
        self.assertEqual(incomplete_snapshot_result["status"], "blocked")
        self.assertEqual(
            incomplete_snapshot_result["_failures"][0]["code"],
            "probe_protocol_failure",
        )
        missing_previous_close = {
            **valid_limited,
            "snapshot": {
                field: item
                for field, item in valid_limited["snapshot"].items()  # type: ignore[union-attr]
                if field != "previous_close"
            },
        }
        missing_previous_result = live_probe._result_from_process(
            0, json.dumps(missing_previous_close), ""
        )
        self.assertEqual(missing_previous_result["status"], "blocked")
        self.assertEqual(
            missing_previous_result["_failures"][0]["code"],
            "probe_protocol_failure",
        )
        for malformed_latest in (
            {"value": "Bearer:secret", "unit": "CNY/share"},
            {"value": "1", "unit": "https://provider.example/secret"},
            {"value": "-1", "unit": "CNY/share"},
            {"value": "1.234", "unit": "CNY/share"},
            {"value": None, "unit": "CNY/share", "status": "not_applicable"},
        ):
            malformed_snapshot = {
                **valid_limited,
                "snapshot": {
                    **valid_limited["snapshot"],  # type: ignore[arg-type]
                    "latest_price": malformed_latest,
                },
            }
            malformed_result = live_probe._result_from_process(
                0, json.dumps(malformed_snapshot), ""
            )
            self.assertEqual(malformed_result["status"], "blocked")
            self.assertEqual(
                malformed_result["_failures"][0]["code"],
                "probe_protocol_failure",
            )
        unknown_snapshot = {
            **valid_limited,
            "snapshot": {
                **valid_limited["snapshot"],  # type: ignore[arg-type]
                "raw_payload": {"value": "secret", "unit": "CNY"},
            },
        }
        unknown_result = live_probe._result_from_process(
            0, json.dumps(unknown_snapshot), ""
        )
        self.assertEqual(unknown_result["status"], "blocked")
        self.assertEqual(
            unknown_result["_failures"][0]["code"], "probe_protocol_failure"
        )
        for field, malformed_value in (
            ("cumulative_volume", {"value": "1.5", "unit": "shares"}),
            ("cumulative_amount", {"value": "-1", "unit": "CNY"}),
            ("cumulative_volume", {"value": "0", "unit": "shares"}),
        ):
            malformed_cumulative = {
                **valid_limited,
                "snapshot": {
                    **valid_limited["snapshot"],  # type: ignore[arg-type]
                    field: malformed_value,
                },
            }
            malformed_cumulative_result = live_probe._result_from_process(
                0, json.dumps(malformed_cumulative), ""
            )
            self.assertEqual(malformed_cumulative_result["status"], "blocked")
            self.assertEqual(
                malformed_cumulative_result["_failures"][0]["code"],
                "probe_protocol_failure",
            )
        undisclosed_limitations = {**valid_limited, "limitations": []}
        undisclosed_result = live_probe._result_from_process(
            0, json.dumps(undisclosed_limitations), ""
        )
        self.assertEqual(undisclosed_result["status"], "blocked")
        self.assertEqual(
            undisclosed_result["_failures"][0]["code"],
            "probe_protocol_failure",
        )
        incomplete_timing = {
            **valid_limited,
            "observation_times": {
                "tongdaxin_baseline": "2026-08-03T10:30:00+08:00",
                "tencent_cross_check": "2026-08-03T10:29:58+08:00",
            },
        }
        incomplete_timing_result = live_probe._result_from_process(
            0, json.dumps(incomplete_timing), ""
        )
        self.assertEqual(incomplete_timing_result["status"], "blocked")
        self.assertEqual(
            incomplete_timing_result["_failures"][0]["code"],
            "probe_protocol_failure",
        )
        supported_payload = {**valid_limited, "status": "supported"}
        supported_result = live_probe._result_from_process(
            0, json.dumps(supported_payload), ""
        )
        self.assertEqual(supported_result["status"], "blocked")
        self.assertEqual(
            supported_result["_failures"][0]["code"], "probe_protocol_failure"
        )

    def test_probe_rejects_same_exchange_override(self) -> None:
        with patch.object(live_probe, "run_probe") as run_probe:
            with self.assertRaises(SystemExit) as invalid_sse:
                live_probe.main(
                    [
                        "--confirm-live",
                        "--as-of",
                        "2026-08-03",
                        "--sse",
                        "SZSE:000001",
                    ]
                )
            self.assertEqual(invalid_sse.exception.code, 2)
            run_probe.assert_not_called()

        with patch.object(live_probe, "run_probe") as run_probe:
            with self.assertRaises(SystemExit) as invalid_prefix:
                live_probe.main(
                    [
                        "--confirm-live",
                        "--as-of",
                        "2026-08-03",
                        "--sse",
                        "SSE:510300",
                    ]
                )
            self.assertEqual(invalid_prefix.exception.code, 2)
            run_probe.assert_not_called()

    def test_price_agreement_requires_both_operations_and_core_prices(self) -> None:
        result = {
            "status": "limited",
            "source_operations": ["tongdaxin_intraday_snapshot@1"],
            "observation_times": {"tongdaxin_baseline": "2026-08-03T10:30:00+08:00"},
            "snapshot": {"latest_price": {"value": "1680.25", "unit": "CNY/share"}},
            "conflicts": [],
            "source_errors": [],
        }

        self.assertEqual(
            live_probe._price_agreement(result)["status"], "not_established"
        )

    def test_suspension_does_not_report_price_agreement(self) -> None:
        result = {
            "status": "limited",
            "trading_status": "suspended",
            "price_type": "not_applicable",
            "session_state": "continuous",
            "source_operations": [
                "tongdaxin_intraday_snapshot@1",
                "tencent_intraday_snapshot@1",
            ],
            "observation_times": {
                "tongdaxin_baseline": "2026-08-03T10:30:00+08:00",
                "tencent_cross_check": "2026-08-03T10:29:58+08:00",
            },
            "snapshot": {
                "latest_price": {"status": "not_applicable", "value": None},
                "open": {"status": "not_applicable", "value": None},
                "high": {"status": "not_applicable", "value": None},
                "low": {"status": "not_applicable", "value": None},
            },
            "conflicts": [],
            "source_errors": [],
        }

        self.assertEqual(
            live_probe._price_agreement(result)["status"], "not_established"
        )

    def test_sanitize_failure_allowlists_codes_and_operations(self) -> None:
        failure = live_probe.sanitize_failure(
            {
                "source_operation": "Bearer:secret",
                "code": "https://provider/path/secret",
            }
        )

        self.assertEqual(failure["source_operation"], "unknown")
        self.assertEqual(failure["code"], "probe_failure")
        for code in ("empty_observation", "operation_identity_mismatch"):
            self.assertEqual(
                live_probe.sanitize_failure(
                    {
                        "source_operation": "tongdaxin_intraday_snapshot@1",
                        "code": code,
                    }
                )["code"],
                code,
            )

    def test_probe_requires_explicit_opt_in_and_as_of(self) -> None:
        with patch.object(live_probe, "run_probe") as run_probe:
            with self.assertRaises(SystemExit) as missing_confirmation:
                live_probe.main(["--as-of", "2026-08-03"])
            self.assertEqual(missing_confirmation.exception.code, 2)
            run_probe.assert_not_called()

        with patch.object(
            live_probe, "run_probe", return_value={"ok": True}
        ) as run_probe:
            self.assertEqual(
                live_probe.main(["--confirm-live", "--as-of", "2026-08-03"]), 0
            )
            run_probe.assert_called_once_with(
                "2026-08-03", (live_probe.DEFAULT_SSE, live_probe.DEFAULT_SZSE)
            )


if __name__ == "__main__":
    unittest.main()
