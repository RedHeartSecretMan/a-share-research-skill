from __future__ import annotations

import unittest
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from threading import Event
from time import monotonic, sleep

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.bse_code_mapping_source import acquire_bse_code_mapping  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes  # noqa: E402
from a_share_research.universe_input import UniverseRequest  # noqa: E402

FROZEN = datetime.fromisoformat("2026-10-15T10:00:00+08:00")


def mapping_html(count: int = 248, *, date_label: str = "2025-09-12") -> bytes:
    rows = "".join(
        f"<tr><td>{i + 1}</td><td>合成公司{i}</td><td>2024/4/8</td><td>{100000 + i}</td><td>{400000 + i}</td></tr>"
        for i in range(count)
    )
    return (
        f"<html><head><title>新旧代码对照表</title></head><body><div>{date_label}</div>"
        "<table><tr><td>序号</td><td>证券简称</td><td>上市日期</td><td>旧代码</td><td>新代码</td></tr>"
        + rows
        + "<tr><td colspan='5'>合成脚注</td></tr></table></body></html>"
    ).encode()


class MappingFixtureTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        assert url == "https://www.bse.cn/code_mapping/200025792.html"
        self.calls += 1
        return HttpResponse(
            200,
            "text/html",
            self.body,
            datetime.fromisoformat("2026-10-16T10:01:00+08:00"),
        )


class BseCodeMappingTests(unittest.TestCase):
    def test_t_dated_static_cohort_has_conservative_version_bound_and_original_cutover_not_all_bse(
        self,
    ) -> None:
        transport = MappingFixtureTransport(mapping_html())
        lanes = ProviderLanes()
        source = acquire_bse_code_mapping(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            transport=transport,
            lanes=lanes,
            policy=ProviderLanePolicy(1, 1),
        )
        self.assertEqual(transport.calls, 1)
        self.assertTrue(lanes.is_authoritative(source.lane_result))
        self.assertEqual(len(source.lane_result.attempts), 1)
        self.assertEqual(source.source_result, "complete")
        self.assertTrue(source.cohort_complete)
        self.assertEqual(source.observed_pair_count, 248)
        self.assertEqual(len(source.records), 248)
        record = source.records[0]
        self.assertEqual((record.old_code, record.new_code), ("100000", "400000"))
        self.assertEqual(record.displayed_listing_date, "2024-04-08")
        self.assertEqual(record.available_at, "2025-09-13T00:00:00+08:00")
        self.assertEqual(record.code_change_effective_date, "2025-10-09")
        self.assertEqual(record.retrieved_at, "2026-10-16T10:01:00+08:00")
        self.assertIsNone(record.bse_listing_effective_date)
        public = source.to_public_dict()
        self.assertEqual(public["source_scope"], "BSE_200025792_CODE_CUTOVER_COHORT")
        self.assertFalse(public["membership_proven"])
        self.assertIsNone(public["historical_expected_universe_count"])
        self.assertEqual(public["research_now"], FROZEN.isoformat())
        self.assertEqual(public["information_cutoff"], "2026-10-14T23:59:59+08:00")
        self.assertNotIn("<table>", str(public))

    def test_later_added_rows_or_changed_date_do_not_inherit_initial_document_availability(
        self,
    ) -> None:
        for body, count in (
            (mapping_html(249), 249),
            (mapping_html(247), 247),
            (mapping_html(date_label="2025-10-14"), 248),
        ):
            with self.subTest(count=count):
                source = acquire_bse_code_mapping(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=MappingFixtureTransport(body),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1),
                )
                self.assertEqual(source.source_result, "partial")
                self.assertFalse(source.cohort_complete)
                self.assertEqual(source.observed_pair_count, count)
                self.assertEqual(len(source.records), count)
                self.assertTrue(all(r.available_at is None for r in source.records))
                self.assertTrue(
                    all(r.code_change_effective_date is None for r in source.records)
                )
                self.assertIsNone(
                    source.to_public_dict()["historical_expected_universe_count"]
                )

    def test_duplicate_or_unsafe_mapping_rows_keep_other_facts_without_version_claim(
        self,
    ) -> None:
        for body in (
            mapping_html().replace(b">100001<", b">100000<", 1),
            mapping_html().replace("合成公司0".encode(), b"unsafe /tmp/private", 1),
        ):
            with self.subTest(body_hash=len(body)):
                source = acquire_bse_code_mapping(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=MappingFixtureTransport(body),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1),
                )
                self.assertEqual(source.source_result, "partial")
                self.assertFalse(source.cohort_complete)
                self.assertGreaterEqual(len(source.records), 247)
                self.assertTrue(all(r.available_at is None for r in source.records))
                self.assertNotIn("private", json.dumps(source.to_public_dict()))

    def test_wrong_headers_or_missing_body_never_mean_successful_empty_cohort(
        self,
    ) -> None:
        for body in (
            mapping_html().replace(
                "<td>旧代码</td>".encode(), "<td>未证明字段</td>".encode(), 1
            ),
            b"<html></html>",
        ):
            with self.subTest(body=body[:30]):
                source = acquire_bse_code_mapping(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=MappingFixtureTransport(body),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1),
                )
                self.assertEqual(source.source_result, "unavailable")
                self.assertIsNone(source.observed_pair_count)
                self.assertEqual(source.records, ())
                self.assertEqual(len(source.lane_result.attempts), 1)

    def test_mapping_timeout_keeps_bse_provider_lane_occupied_for_current_list(
        self,
    ) -> None:
        from a_share_research.bse_universe_source import acquire_bse_current_list

        entered, release = Event(), Event()

        class Slow(MappingFixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                entered.set()
                release.wait(2)
                return response

        class NoPost:
            calls = 0

            def post(
                self, url: str, headers: dict[str, str], body: bytes
            ) -> HttpResponse:
                self.calls += 1
                raise AssertionError("An occupied BSE group must not start a POST.")

        lanes = ProviderLanes()
        transport = Slow(mapping_html())
        try:
            source = acquire_bse_code_mapping(
                UniverseRequest("2026-10-15", FROZEN.isoformat()),
                research_now=FROZEN,
                transport=transport,
                lanes=lanes,
                policy=ProviderLanePolicy(1, 0.03, cancellation_grace_seconds=0),
            )
            self.assertTrue(entered.is_set())
            self.assertEqual(source.lane_result.status, "timed_out")
            self.assertEqual(source.lane_result.quiescence.state, "poisoned")
            post = NoPost()
            current = acquire_bse_current_list(
                UniverseRequest("2026-10-15", FROZEN.isoformat()),
                research_now=FROZEN,
                transport=post,
                lanes=lanes,
                policy=ProviderLanePolicy(1, 1),
                max_pages=1,
            )
            self.assertEqual(post.calls, 0)
            self.assertEqual(current.lane_result.attempts, ())
            self.assertIn("provider_lane_poisoned", current.limitation_codes)
        finally:
            release.set()
            deadline = monotonic() + 1
            while (
                lanes.quiescence("bse").state != "quiescent" and monotonic() < deadline
            ):
                sleep(0.001)
        self.assertEqual(lanes.quiescence("bse").state, "quiescent")
        self.assertEqual(source.records, ())

    def test_copied_installed_dated_mapping_parser_has_real_counted_leaf_and_safe_version_fields(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        script = dedent("""
            import json,sys
            from datetime import datetime
            sys.path.insert(0,sys.argv[1])
            from a_share_research.bse_code_mapping_source import acquire_bse_code_mapping
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            rows=''.join('<tr><td>1</td><td>合成公司</td><td>2024/4/8</td><td>'+str(100000+i)+'</td><td>'+str(400000+i)+'</td></tr>' for i in range(248))
            body=('<title>新旧代码对照表</title><div>2025-09-12</div><table><tr><td>序号</td><td>证券简称</td><td>上市日期</td><td>旧代码</td><td>新代码</td></tr>'+rows+'<tr><td>合成脚注</td></tr></table>').encode()
            class Fixture:
                def get(self,url,headers):
                    assert url=='https://www.bse.cn/code_mapping/200025792.html'
                    return HttpResponse(200,'text/html',body,datetime.fromisoformat('2026-10-16T10:01:00+08:00'))
            frozen=datetime.fromisoformat('2026-10-15T10:00:00+08:00')
            lanes=ProviderLanes()
            source=acquire_bse_code_mapping(UniverseRequest('2026-10-15',frozen.isoformat()),research_now=frozen,transport=Fixture(),lanes=lanes,policy=ProviderLanePolicy(1,1))
            assert lanes.is_authoritative(source.lane_result)
            assert len(source.records)==248 and source.cohort_complete
            assert source.records[0].available_at=='2025-09-13T00:00:00+08:00'
            assert source.records[0].bse_listing_effective_date is None
            print(json.dumps({'cohort':source.observed_pair_count,'attempts':len(source.lane_result.attempts),'membership':source.to_public_dict()['membership_proven']}))
        """)
        with tempfile.TemporaryDirectory(prefix="asr-122-t-installed-") as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            run = subprocess.run(
                [sys.executable, "-c", script, str(installed / "scripts")],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                close_fds=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "en_US.UTF-8",
                    "PYTHONUTF8": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": temporary,
                },
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(
                json.loads(run.stdout),
                {"cohort": 248, "attempts": 1, "membership": False},
            )
            self.assertNotIn(temporary, run.stdout)


if __name__ == "__main__":
    unittest.main()
