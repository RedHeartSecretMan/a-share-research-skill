from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent
import unittest
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from datetime import datetime

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.baostock_universe_source import (  # noqa: E402
    BaostockResponse,
    acquire_baostock_dated_roster,
)
from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes  # noqa: E402
from a_share_research.universe_input import UniverseRequest  # noqa: E402

FROZEN = datetime.fromisoformat("2026-10-15T10:00:00+08:00")
RETRIEVED = datetime.fromisoformat("2026-10-15T10:01:00+08:00")


def roster_frame(
    rows: list[list[str]], *, day: str = "2026-10-14", page: int = 1
) -> bytes:
    # Explicit synthetic body follows retained Baostock 0.9.4 query_all_stock
    # parsing positions; these names are synthetic, not captured provider rows.
    body = "\x01".join(
        [
            "0",
            "ok",
            "query_all_stock",
            "anonymous",
            str(page),
            "2000",
            json.dumps({"record": rows}),
            day,
            "code,tradeStatus,code_name",
            "0",
        ]
    )
    return (
        "00.9.40\x0136\x01" + str(len(body)).zfill(10) + body + "<![CDATA[]]>\n"
    ).encode()


class Frames:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = list(frames)
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.closed = False

    def exchange(
        self, message_type: str, parameters: tuple[str, ...]
    ) -> BaostockResponse:
        self.calls.append((message_type, parameters))
        if message_type in {"00", "02"}:
            method, response_type = (
                ("login", "01") if message_type == "00" else ("logout", "03")
            )
            body = "\x01".join(["0", "ok", method, "anonymous", "0"])
            frame = (
                "00.9.40\x01"
                + response_type
                + "\x01"
                + str(len(body)).zfill(10)
                + body
                + "<![CDATA[]]>\n"
            ).encode()
        else:
            frame = self.frames.pop(0)
        return BaostockResponse(frame, RETRIEVED)

    def close(self) -> None:
        self.closed = True


class BaostockDatedRosterTests(unittest.TestCase):
    def test_given_day_roster_preserves_all_securities_without_inventing_a_share_count(
        self,
    ) -> None:
        transport = Frames(
            [
                roster_frame(
                    [
                        ["sh.120000", "1", "合成沪股"],
                        ["sz.560000", "0", "合成深股"],
                        ["sh.010000", "1", "合成指数"],
                    ]
                )
            ]
        )
        lanes = ProviderLanes()
        result = acquire_baostock_dated_roster(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            lanes=lanes,
            policy=ProviderLanePolicy(3, 1),
            max_pages=1,
            transport=transport,
        )
        self.assertTrue(result.pages_complete)
        self.assertEqual(result.closed_provider_count, 3)
        self.assertEqual(len(result.records), 3)
        self.assertEqual(result.records[0].provider_code, "sh.010000")
        self.assertEqual(result.records[0].name, "合成指数")
        self.assertIsNone(result.expected_a_share_count)
        self.assertEqual(result.source_result, "partial")
        self.assertFalse(result.to_public_dict()["membership_proven"])
        self.assertEqual([call[0] for call in transport.calls], ["00", "35", "02"])
        self.assertEqual(
            transport.calls[1][1],
            ("query_all_stock", "anonymous", "1", "2000", "2026-10-14"),
        )
        self.assertEqual(len(result.lane_result.attempts), 3)
        self.assertTrue(lanes.is_authoritative(result.lane_result))
        self.assertTrue(transport.closed)


class BaostockDatedRosterFailureTests(unittest.TestCase):
    def acquire(self, transport: Frames, *, budget: int = 5, max_pages: int = 2):
        return acquire_baostock_dated_roster(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(budget, 1),
            max_pages=max_pages,
            transport=transport,
        )

    def test_full_page_requires_successful_next_page_and_deduplicates_honestly(self):
        rows = [[f"sh.{number:06}", "1", "合成成员"] for number in range(2000)]
        transport = Frames([roster_frame(rows), roster_frame([], page=2)])
        result = self.acquire(transport)
        self.assertTrue(result.pages_complete)
        self.assertEqual(result.closed_provider_count, 2000)
        self.assertEqual(result.pages_received, 2)
        self.assertEqual(len(result.lane_result.attempts), 4)

    def test_wrong_response_day_cannot_become_historical_roster(self):
        transport = Frames(
            [roster_frame([["sh.120000", "1", "合成成员"]], day="2026-10-15")]
        )
        result = self.acquire(transport)
        self.assertFalse(result.pages_complete)
        self.assertIsNone(result.closed_provider_count)
        self.assertEqual(result.records, ())
        self.assertIn("roster_context_mismatch", result.limitation_codes)
        self.assertTrue(transport.closed)

    def test_duplicate_code_and_bad_row_preserve_valid_record_but_close_completeness(
        self,
    ):
        transport = Frames(
            [
                roster_frame(
                    [
                        ["sh.120000", "1", "合成成员"],
                        ["sh.120000", "1", "合成成员"],
                        ["unsafe", "1", "/private/credential"],
                    ]
                )
            ]
        )
        result = self.acquire(transport)
        self.assertFalse(result.pages_complete)
        self.assertIsNone(result.closed_provider_count)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.rejected_rows, 1)
        self.assertIn("duplicate_roster_identity", result.limitation_codes)
        self.assertNotIn("/private", json.dumps(result.to_public_dict()))

    def test_page_and_attempt_budgets_never_invent_terminal_empty_page(self):
        rows = [[f"sh.{number:06}", "1", "合成成员"] for number in range(2000)]
        transport = Frames([roster_frame(rows)])
        result = self.acquire(transport, budget=2, max_pages=1)
        self.assertEqual(len(transport.calls), 2)
        self.assertFalse(result.pages_complete)
        self.assertIsNone(result.closed_provider_count)
        self.assertEqual(len(result.records), 2000)
        self.assertIn("roster_pagination_not_closed", result.limitation_codes)
        self.assertTrue(transport.closed)


class DefaultSdkHealthTests(unittest.TestCase):
    def test_missing_bundled_sdk_is_unavailable_without_any_source_invocation(self):
        # Deliberately declare an unavailable runtime in an independent artifact.
        # Installing the repository's normal runtime must never turn this test
        # into a live default SDK request.
        root = Path(__file__).resolve().parents[1] / "skill/a-share-research"
        script = dedent("""
            import json,sys
            from datetime import datetime
            sys.path.insert(0,sys.argv[1])
            from a_share_research.baostock_universe_source import acquire_baostock_dated_roster
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            lanes=ProviderLanes()
            result=acquire_baostock_dated_roster(UniverseRequest('2026-10-14','2026-10-14T23:59:59+08:00'),research_now=datetime.fromisoformat('2026-10-15T10:00:00+08:00'),lanes=lanes,policy=ProviderLanePolicy(3,1),max_pages=1)
            assert not result.pages_complete and result.records==()
            assert result.lane_result.attempts==()
            assert result.source_result=='unavailable'
            assert 'sdk_runtime_unavailable' in result.limitation_codes
            assert lanes.is_authoritative(result.lane_result)
            print(json.dumps({'source_result':result.source_result,'attempts':0}))
        """)
        with tempfile.TemporaryDirectory(prefix="asr-122-missing-sdk-") as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(installed / "scripts")],
                cwd=temporary,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                close_fds=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "en_US.UTF-8",
                    "PYTHONUTF8": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": temporary,
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {"source_result": "unavailable", "attempts": 0},
            )


class BaostockFrozenSourceTests(unittest.TestCase):
    def test_failed_followup_retains_first_page_without_claiming_a_closed_count(
        self,
    ) -> None:
        rows = [[f"sh.{number:06}", "1", "合成成员"] for number in range(2000)]
        transport = Frames([roster_frame(rows), b"invalid-provider-frame"])
        result = acquire_baostock_dated_roster(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(4, 1),
            max_pages=2,
            transport=transport,
        )
        self.assertEqual(len(result.records), 2000)
        self.assertEqual(result.pages_received, 1)
        self.assertFalse(result.pages_complete)
        self.assertIsNone(result.closed_provider_count)
        self.assertEqual(len(result.lane_result.attempts), 3)
        self.assertTrue(transport.closed)

    def test_source_callback_cannot_change_the_frozen_request_date(self) -> None:
        request = UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00")

        class Mutating(Frames):
            def exchange(
                self, kind: str, parameters: tuple[str, ...]
            ) -> BaostockResponse:
                object.__setattr__(request, "screen_date", "2026-10-15")
                return super().exchange(kind, parameters)

        transport = Mutating([roster_frame([["sh.120000", "1", "合成成员"]])])
        result = acquire_baostock_dated_roster(
            request,
            research_now=FROZEN,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(3, 1),
            max_pages=1,
            transport=transport,
        )
        self.assertEqual(result.membership_effective_date, "2026-10-14")
        self.assertEqual(result.records[0].membership_effective_date, "2026-10-14")
        self.assertEqual(transport.calls[1][1][-1], "2026-10-14")

    def test_timeout_snapshot_stays_frozen_and_same_group_cannot_overlap(self) -> None:
        entered, release = Event(), Event()

        class Slow(Frames):
            def exchange(
                self, kind: str, parameters: tuple[str, ...]
            ) -> BaostockResponse:
                value = super().exchange(kind, parameters)
                if kind == "35":
                    entered.set()
                    release.wait(1)
                return value

        lanes = ProviderLanes()
        first = Slow([roster_frame([["sh.120000", "1", "合成成员"]])])
        second = Frames([roster_frame([["sh.120001", "1", "另一成员"]])])
        policy = ProviderLanePolicy(3, 0.03, cancellation_grace_seconds=0)
        try:
            result = acquire_baostock_dated_roster(
                UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
                research_now=FROZEN,
                lanes=lanes,
                policy=policy,
                max_pages=1,
                transport=first,
            )
            self.assertTrue(entered.is_set())
            self.assertEqual(result.lane_result.quiescence.state, "poisoned")
            rejected = acquire_baostock_dated_roster(
                UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
                research_now=FROZEN,
                lanes=lanes,
                policy=policy,
                max_pages=1,
                transport=second,
            )
            self.assertEqual(second.calls, [])
            self.assertEqual(rejected.lane_result.attempts, ())
        finally:
            release.set()
            deadline = monotonic() + 1
            while (
                lanes.quiescence("baostock").state != "quiescent"
                and monotonic() < deadline
            ):
                sleep(0.001)
        self.assertEqual(lanes.quiescence("baostock").state, "quiescent")
        self.assertEqual(result.records, ())
        self.assertFalse(result.pages_complete)
        self.assertTrue(first.closed)


class OwnedSdkTransportTests(unittest.TestCase):
    def test_injected_owned_sdk_transport_cannot_hide_a_second_worker_or_budget(
        self,
    ) -> None:
        from a_share_research.baostock_sdk_transport import BaostockSdkTransport

        transport = BaostockSdkTransport(1)
        with self.assertRaises(TypeError):
            acquire_baostock_dated_roster(
                UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
                research_now=FROZEN,
                lanes=ProviderLanes(),
                policy=ProviderLanePolicy(3, 1),
                max_pages=1,
                transport=transport,
            )
        self.assertIsNone(transport.process)


class SdkWorkerExitTests(unittest.TestCase):
    def test_failed_worker_kill_cannot_attest_quiescence_before_observed_exit(
        self,
    ) -> None:
        release = Event()
        reader, writer = os.pipe()
        os.write(writer, b'{"kind":"ready"}\n')
        os.close(writer)

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = io.BytesIO()
                self.stdout = os.fdopen(reader, "rb", buffering=0)
                self.waited = False

            def poll(self):
                return 0 if release.is_set() else None

            def kill(self):
                raise OSError("synthetic worker termination failure")

            def wait(self):
                release.wait(2)
                self.waited = release.is_set()
                return 0

        process = FakeProcess()
        lanes = ProviderLanes()
        try:
            with patch(
                "a_share_research.baostock_sdk_transport.subprocess.Popen",
                return_value=process,
            ):
                result = acquire_baostock_dated_roster(
                    UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
                    research_now=FROZEN,
                    lanes=lanes,
                    policy=ProviderLanePolicy(3, 0.03, cancellation_grace_seconds=0),
                    max_pages=1,
                )
                self.assertEqual(result.lane_result.status, "timed_out")
                self.assertEqual(result.lane_result.quiescence.state, "poisoned")
                self.assertFalse(process.waited)
                self.assertFalse(process.stdout.closed)
        finally:
            release.set()
            deadline = monotonic() + 1
            while (
                lanes.quiescence("baostock").state != "quiescent"
                and monotonic() < deadline
            ):
                sleep(0.001)
            if not process.stdout.closed:
                process.stdout.close()
        self.assertEqual(lanes.quiescence("baostock").state, "quiescent")
        self.assertTrue(process.waited)


if __name__ == "__main__":
    unittest.main()
