from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "close_sources" / "fixture_cli.py"
)
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.close_sources import (  # noqa: E402
    SseDailyLineOperation,
    TencentDailyLineOperation,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    SourceOperationError,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


class StaticTransport:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.content_type = content_type

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        return HttpResponse(
            status=200,
            content_type=self.content_type,
            body=self.body,
            retrieved_at=datetime(2026, 8, 2, 10, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class CloseSourceOperationTests(unittest.TestCase):
    def test_http_success_with_no_daily_rows_fails_closed(self) -> None:
        transport = StaticTransport(b'{"code":"600519","kline":[]}')

        with self.assertRaises(SourceOperationError) as caught:
            SseDailyLineOperation().observe("SSE:600519", transport)

        self.assertEqual(caught.exception.source_operation, "sse_daily_line@1")
        self.assertEqual(caught.exception.code, "empty_observation")

    def test_http_success_with_empty_tencent_daily_line_fails_closed(self) -> None:
        transport = StaticTransport(
            b'{"code":0,"data":{"sh600519":{"day":[],"qt":{"sh600519":[]}}}}',
            content_type="text/html; charset=UTF-8",
        )

        with self.assertRaises(SourceOperationError) as caught:
            TencentDailyLineOperation().observe(
                "SSE:600519", date(2026, 7, 31), transport
            )

        self.assertEqual(caught.exception.source_operation, "tencent_daily_line@1")
        self.assertEqual(caught.exception.code, "empty_observation")

    def test_wrong_security_payload_fails_closed(self) -> None:
        transport = StaticTransport(
            b'{"code":"600000","kline":[["20260731","1","1","1","1","1"]]}'
        )

        with self.assertRaises(SourceOperationError) as caught:
            SseDailyLineOperation().observe("SSE:600519", transport)

        self.assertEqual(caught.exception.code, "wrong_security_payload")

    def test_unexpected_content_type_fails_closed(self) -> None:
        transport = StaticTransport(
            b'{"code":"600519","kline":[]}', content_type="text/html"
        )

        with self.assertRaises(SourceOperationError) as caught:
            SseDailyLineOperation().observe("SSE:600519", transport)

        self.assertEqual(caught.exception.code, "unexpected_content_type")

    def test_changed_daily_row_schema_fails_closed(self) -> None:
        transport = StaticTransport(
            b'{"code":"600519","kline":[["20260731","1","1","1","1"]]}'
        )

        with self.assertRaises(SourceOperationError) as caught:
            SseDailyLineOperation().observe("SSE:600519", transport)

        self.assertEqual(caught.exception.code, "unknown_schema")

    def test_tencent_daily_line_accepts_documented_corporate_action_annotation(
        self,
    ) -> None:
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "day": [
                        [
                            "2026-06-26",
                            "1410.000",
                            "1410.000",
                            "1412.450",
                            "1401.010",
                            "26509.000",
                            {
                                "nd": "2025",
                                "fh_sh": "239.57",
                                "djr": "2025-12-18",
                                "cqr": "2025-12-19",
                                "FHcontent": "10派239.57元",
                            },
                        ]
                    ],
                    "qt": {
                        "sh600519": [
                            "",
                            "贵州茅台",
                            "600519",
                            "1410.000",
                            "1400.000",
                            "",
                            "26509.000",
                            *([""] * 23),
                            "20260626150000",
                        ]
                    },
                }
            },
        }
        transport = StaticTransport(
            json.dumps(payload, ensure_ascii=False).encode(),
            content_type="text/html; charset=UTF-8",
        )

        observations = TencentDailyLineOperation().observe(
            "SSE:600519", date(2026, 6, 26), transport
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].trading_date, date(2026, 6, 26))
        self.assertEqual(observations[0].value, "1410.000")

    def test_tencent_daily_line_rejects_unknown_row_extension(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "day": [["2026-06-26", "1", "1", "1", "1", "1", {"x": "1"}]],
                    "qt": {
                        "sh600519": [
                            "",
                            "贵州茅台",
                            "600519",
                            "1",
                            "1",
                            "",
                            "1",
                            *([""] * 23),
                            "20260626150000",
                        ]
                    },
                }
            },
        }
        transport = StaticTransport(
            json.dumps(payload, ensure_ascii=False).encode(),
            content_type="text/html; charset=UTF-8",
        )

        with self.assertRaises(SourceOperationError) as caught:
            TencentDailyLineOperation().observe(
                "SSE:600519", date(2026, 6, 26), transport
            )

        self.assertEqual(caught.exception.code, "unknown_schema")

    def test_live_declared_suspension_does_not_relabel_historical_rows(self) -> None:
        quote = [""] * 35
        quote[2] = "600519"
        quote[3] = "1668.00"
        quote[4] = "1668.00"
        quote[6] = "0"
        quote[30] = "20260803103000"
        quote[33] = "suspended"
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "day": [
                        [
                            "2026-07-31",
                            "1660.00",
                            "1668.00",
                            "1672.00",
                            "1655.00",
                            "10000",
                        ],
                        ["2026-08-03", "1668.00", "1668.00", "1668.00", "1668.00", "0"],
                    ],
                    "qt": {"sh600519": quote},
                }
            },
        }
        transport = StaticTransport(
            json.dumps(payload).encode(), content_type="text/html; charset=UTF-8"
        )

        observations = TencentDailyLineOperation().observe(
            "SSE:600519", date(2026, 8, 3), transport
        )

        self.assertEqual(
            [observation.trading_status for observation in observations],
            ["traded", "suspended"],
        )


class CloseObservationCliTests(unittest.TestCase):
    def run_close(
        self,
        security: str,
        as_of: str,
        scenario: str = "sse_match",
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["A_SHARE_RESEARCH_TEST_SCENARIO"] = scenario
        return subprocess.run(
            [
                sys.executable,
                str(FIXTURE_CLI),
                "close",
                "--security",
                security,
                "--as-of",
                as_of,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_matching_sse_and_tencent_close_is_an_honest_limited_result(
        self,
    ) -> None:
        completed = self.run_close("SSE:600519", "2026-07-31")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["research"]["security"], "SSE:600519")
        self.assertEqual(result["research"]["as_of"], "2026-07-31")
        self.assertEqual(
            result["latest_completed_session"],
            {
                "trading_date": "2026-07-31",
                "status": "completed",
                "evidence_ids": ["session-sse_daily_line@1-SSE:600519-2026-07-31"],
            },
        )
        self.assertEqual(
            result["close"],
            {
                "status": "cross_checked_experimental",
                "trading_date": "2026-07-31",
                "value": "1350.600",
                "unit": "CNY/share",
                "evidence_ids": [
                    "close-sse_daily_line@1-SSE:600519-2026-07-31",
                    "close-tencent_daily_line@1-SSE:600519-2026-07-31",
                ],
            },
        )
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["sse_daily_line@1", "tencent_daily_line@1"],
        )
        for evidence in result["evidence"]:
            self.assertEqual(evidence["subject"]["security"], "SSE:600519")
            self.assertEqual(evidence["observation"]["trading_date"], "2026-07-31")
            self.assertEqual(evidence["observed_value"]["unit"], "CNY/share")
            self.assertEqual(evidence["observation"]["price_type"], "close")
            self.assertEqual(evidence["observation"]["adjustment"], "unadjusted")
            self.assertEqual(evidence["observation"]["currency"], "CNY")
            self.assertEqual(evidence["observation"]["trading_status"], "traded")
            self.assertIsNotNone(evidence["evidence_time"])
            self.assertIsNotNone(evidence["available_at"])
            self.assertEqual(evidence["retrieved_at"], "2026-08-02T10:30:00+08:00")
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(
            result["latest_completed_session"]["evidence_ids"],
            [result["session_evidence"][0]["id"]],
        )
        self.assertEqual(
            [item["code"] for item in result["limitations"]],
            ["experimental_close_sources"],
        )

    def test_matching_szse_and_tencent_close_uses_the_same_contract(self) -> None:
        completed = self.run_close("SZSE:000001", "2026-07-31", scenario="szse_match")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["close"]["value"], "11.630")
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["szse_daily_line@1", "tencent_daily_line@1"],
        )
        self.assertEqual(
            {item["observation"]["trading_date"] for item in result["evidence"]},
            {"2026-07-31"},
        )

    def test_price_conflict_preserves_both_observations_without_selection(
        self,
    ) -> None:
        completed = self.run_close(
            "SSE:600519", "2026-07-31", scenario="price_conflict"
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["close"], {"status": "unresolved"})
        self.assertEqual(
            [item["observed_value"]["value"] for item in result["evidence"]],
            ["1350.6000", "1350.610"],
        )
        self.assertEqual(
            result["conflicts"],
            [
                {
                    "code": "close_price_conflict",
                    "message": (
                        "Sources disagree on the unadjusted close for "
                        "SSE:600519 on 2026-07-31."
                    ),
                    "evidence_ids": [
                        "close-sse_daily_line@1-SSE:600519-2026-07-31",
                        "close-tencent_daily_line@1-SSE:600519-2026-07-31",
                    ],
                }
            ],
        )

    def test_stale_quote_is_an_explicit_trading_date_conflict(self) -> None:
        completed = self.run_close("SSE:600519", "2026-07-31", scenario="stale_quote")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["close"], {"status": "unresolved"})
        self.assertEqual(
            [item["observation"]["trading_date"] for item in result["evidence"]],
            ["2026-07-31", "2026-07-30"],
        )
        self.assertEqual(result["conflicts"][0]["code"], "close_date_conflict")
        self.assertEqual(
            result["conflicts"][0]["classification"],
            "stale_tencent_daily_line",
        )
        self.assertEqual(
            result["conflicts"][0]["evidence_ids"],
            [
                "close-sse_daily_line@1-SSE:600519-2026-07-31",
                "close-tencent_daily_line@1-SSE:600519-2026-07-30",
            ],
        )

    def test_current_day_before_close_keeps_the_prior_completed_session(self) -> None:
        completed = self.run_close("SSE:600519", "2026-07-31", scenario="before_close")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["latest_completed_session"],
            {
                "trading_date": "2026-07-30",
                "status": "completed",
                "evidence_ids": ["session-sse_daily_line@1-SSE:600519-2026-07-30"],
            },
        )
        self.assertEqual(
            [item["observation"]["trading_date"] for item in result["evidence"]],
            ["2026-07-30", "2026-07-30"],
        )
        self.assertEqual(
            result["rejected_observations"][0]["observation"]["price_type"],
            "intraday_last",
        )
        self.assertEqual(
            result["limitations"][1]["code"],
            "unfinished_current_session_ignored",
        )
        self.assertEqual(result["conflicts"], [])

    def test_suspension_is_not_misreported_as_a_fresh_close(self) -> None:
        completed = self.run_close("SSE:600519", "2026-08-01", scenario="suspended")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["latest_completed_session"],
            {
                "trading_date": "2026-07-31",
                "status": "completed",
                "evidence_ids": ["session-sse_daily_line@1-SSE:600519-2026-07-31"],
            },
        )
        self.assertEqual(
            result["rejected_observations"][0]["observation"]["trading_status"],
            "suspended",
        )
        self.assertEqual(result["conflicts"][0]["classification"], "security_suspended")
        self.assertEqual(result["close"], {"status": "unresolved"})

    def test_current_day_after_formal_daily_close_uses_that_session(self) -> None:
        completed = self.run_close("SSE:600519", "2026-07-31", scenario="after_close")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["close"]["trading_date"], "2026-07-31")

    def test_weekend_uses_the_latest_completed_friday_session(self) -> None:
        completed = self.run_close("SSE:600519", "2026-08-02", scenario="weekend")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["latest_completed_session"]["trading_date"], "2026-07-31"
        )

    def test_exchange_holiday_uses_the_latest_completed_session(self) -> None:
        completed = self.run_close("SZSE:000001", "2026-02-23", scenario="holiday")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["latest_completed_session"]["trading_date"], "2026-02-13"
        )

    def test_future_date_blocks_before_any_source_request(self) -> None:
        completed = self.run_close("SSE:600519", "2026-08-03", scenario="future")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["limitations"][0]["code"], "future_research_date")

    def test_close_rejects_unresolved_or_unsupported_security_identifiers(
        self,
    ) -> None:
        for security in ("600519", "BSE:920000", "sse:600519", "SSE:60051"):
            with self.subTest(security=security):
                completed = self.run_close(security, "2026-07-31")

                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                self.assertIn("SSE/SZSE canonical identifier", completed.stderr)

    def test_close_rejects_relative_research_dates(self) -> None:
        completed = self.run_close("SSE:600519", "current")

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("YYYY-MM-DD", completed.stderr)

    def test_observation_after_historical_boundary_is_rejected_not_used(
        self,
    ) -> None:
        completed = self.run_close(
            "SSE:600519", "2026-07-31", scenario="after_boundary"
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["sse_daily_line@1"],
        )
        self.assertEqual(
            result["rejected_observations"],
            [],
        )
        self.assertEqual(
            result["source_errors"][0]["code"],
            "observation_after_requested_range",
        )
        self.assertEqual(result["close"], {"status": "unresolved"})

    def test_source_failure_is_a_zero_exit_blocked_domain_result(self) -> None:
        completed = self.run_close("SSE:600519", "2026-07-31", scenario="empty_tencent")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["source_errors"],
            [
                {
                    "source_operation": "tencent_daily_line@1",
                    "code": "empty_observation",
                    "message": (
                        "The Tencent daily-line response contains no price "
                        "observations."
                    ),
                }
            ],
        )
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["sse_daily_line@1"],
        )
        self.assertEqual(result["close"], {"status": "unresolved"})

    def test_historical_date_uses_tencent_daily_observation_not_current_quote(
        self,
    ) -> None:
        completed = self.run_close(
            "SSE:600519", "2026-07-30", scenario="historical_kline"
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["close"]["trading_date"], "2026-07-30")
        self.assertEqual(result["close"]["value"], "1361.760")
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["sse_daily_line@1", "tencent_daily_line@1"],
        )
        self.assertEqual(
            {item["observation"]["trading_date"] for item in result["evidence"]},
            {"2026-07-30"},
        )


if __name__ == "__main__":
    unittest.main()
