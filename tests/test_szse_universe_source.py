from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from threading import Event
from time import monotonic, sleep
from urllib.parse import parse_qs, urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse, TransportError  # noqa: E402
from a_share_research.provider_lane import (  # noqa: E402
    ProviderLanePolicy,
    ProviderLanes,
)
from a_share_research.szse_universe_source import (  # noqa: E402
    acquire_szse_current_list,
)
from a_share_research.universe_input import UniverseRequest  # noqa: E402

NOW = datetime.fromisoformat("2026-10-14T10:00:00+08:00")
REQUEST = UniverseRequest("2026-10-14", NOW.isoformat())


def page(number: int, rows: list[dict[str, str]], *, count: int = 2) -> bytes:
    # Synthetic values; only the retained A batch's schema is reproduced.
    return json.dumps(
        [
            {"metadata": {"catalogid": "1110", "tabkey": "tab2"}, "data": []},
            {
                "error": None,
                "metadata": {
                    "catalogid": "1110",
                    "tabkey": "tab1",
                    "pageno": number,
                    "pagesize": 1,
                    "pagecount": max(1, count),
                    "recordcount": count,
                    "name": "A股列表",
                    "cols": {
                        "agdm": "A股代码",
                        "agjc": "A股简称",
                        "agssrq": "A股<br>上市日期",
                    },
                    "subname": "2030-01-01 ",
                },
                "data": rows,
            },
        ]
    ).encode()


def row(code: str, name: str = "合成股份") -> dict[str, str]:
    return {"agdm": code, "agjc": name, "agssrq": "2020-01-02", "bk": "合成板块"}


class FixtureTransport:
    def __init__(self, pages: dict[int, bytes]) -> None:
        self.pages = pages
        self.calls: list[int] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        selectors = parse_qs(urlparse(url).query, keep_blank_values=True)
        assert "txtDMorJC" not in selectors
        assert selectors["tab1PAGESIZE"] == ["20"]
        assert selectors["CATALOGID"] == ["1110"]
        assert selectors["TABKEY"] == ["tab1"]
        number = int(selectors["PAGENO"][0])
        self.calls.append(number)
        return HttpResponse(
            200,
            "application/json",
            self.pages[number],
            datetime.fromisoformat(f"2026-10-14T10:01:0{number}+08:00"),
        )


class SzseCurrentListTests(unittest.TestCase):
    def test_observed_current_pages_keep_unknown_membership_times_and_counted_calls(
        self,
    ) -> None:
        transport = FixtureTransport(
            {1: page(1, [row("123456")]), 2: page(2, [row("654321")])}
        )
        lanes = ProviderLanes()
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=transport,
            lanes=lanes,
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        self.assertEqual(transport.calls, [1, 2])
        self.assertEqual(acquisition.reported_count, 2)
        self.assertTrue(acquisition.pages_complete)
        self.assertEqual(acquisition.source_result, "partial")
        self.assertEqual([r.code for r in acquisition.records], ["123456", "654321"])
        self.assertEqual(acquisition.records[0].listing_date, "2020-01-02")
        self.assertIsNone(acquisition.records[0].available_at)
        self.assertIsNone(acquisition.records[0].membership_effective_at)
        self.assertEqual(
            acquisition.records[1].retrieved_at, "2026-10-14T10:01:02+08:00"
        )
        self.assertEqual(len(acquisition.lane_result.attempts), 2)
        self.assertTrue(lanes.is_authoritative(acquisition.lane_result))
        self.assertEqual(acquisition.lane_result.quiescence.state, "quiescent")
        public = json.dumps(acquisition.to_public_dict(), ensure_ascii=False)
        self.assertNotIn("https://", public)
        self.assertNotIn("bk", public)
        with self.assertRaises(FrozenInstanceError):
            acquisition.records[0].code = "111111"  # type: ignore[misc]

    def test_duplicate_pages_retain_one_identity_and_never_close_the_reported_count(
        self,
    ) -> None:
        transport = FixtureTransport(
            {1: page(1, [row("123456")]), 2: page(2, [row("123456")])}
        )
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        self.assertEqual(len(acquisition.records), 1)
        self.assertEqual(acquisition.reported_count, 2)
        self.assertFalse(acquisition.pages_complete)
        self.assertIn("duplicate_current_list_identity", acquisition.limitation_codes)
        self.assertEqual(len(acquisition.lane_result.attempts), 2)

    def test_current_source_does_not_query_or_backfill_a_completed_historical_day(
        self,
    ) -> None:
        transport = FixtureTransport({})
        acquisition = acquire_szse_current_list(
            UniverseRequest("2026-10-13", "2026-10-13T23:59:59+08:00"),
            research_now=NOW,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(acquisition.source_result, "unavailable")
        self.assertIsNone(acquisition.reported_count)
        self.assertEqual(acquisition.records, ())
        self.assertIn(
            "current_list_cannot_prove_historical_membership",
            acquisition.limitation_codes,
        )
        self.assertEqual(acquisition.lane_result.attempts, ())

    def test_response_day_and_retrieval_are_normalized_to_shanghai(self) -> None:
        class UtcTransport(FixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                return replace(
                    super().get(url, headers),
                    retrieved_at=datetime.fromisoformat("2026-10-13T16:01:00+00:00"),
                )

        frozen = datetime.fromisoformat("2026-10-14T00:00:00+08:00")
        acquisition = acquire_szse_current_list(
            UniverseRequest("2026-10-14", frozen.isoformat()),
            research_now=frozen,
            transport=UtcTransport({1: page(1, [row("123456")], count=1)}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(len(acquisition.records), 1)
        self.assertEqual(
            acquisition.records[0].retrieved_at, "2026-10-14T00:01:00+08:00"
        )
        self.assertTrue(acquisition.pages_complete)

    def test_short_page_keeps_valid_rows_and_the_sources_larger_reported_count(
        self,
    ) -> None:
        body = json.loads(page(1, [row("123456")]))
        body[1]["metadata"].update(pagesize=2, pagecount=1)
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: json.dumps(body).encode()}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.reported_count, 2)
        self.assertEqual([r.code for r in acquisition.records], ["123456"])
        self.assertFalse(acquisition.pages_complete)
        self.assertEqual(acquisition.source_result, "partial")
        self.assertIn("page_row_count_mismatch", acquisition.limitation_codes)

    def test_unsafe_row_is_rejected_without_losing_other_identity_or_leaking_a_path(
        self,
    ) -> None:
        body = json.loads(
            page(1, [row("123456"), row("654321", "/private/tmp/source-secret")])
        )
        body[1]["metadata"].update(pagesize=2, pagecount=1)
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: json.dumps(body).encode()}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual([r.code for r in acquisition.records], ["123456"])
        self.assertEqual(acquisition.rejected_rows, 1)
        self.assertEqual(acquisition.reported_count, 2)
        self.assertFalse(acquisition.pages_complete)
        self.assertNotIn("source-secret", json.dumps(acquisition.to_public_dict()))
        self.assertNotIn("/private/", json.dumps(acquisition.to_public_dict()))

    def test_request_and_page_budgets_retain_the_observed_prefix(self) -> None:
        for max_pages, attempts, code in (
            (1, 2, "page_budget_exhausted"),
            (2, 1, "actual_attempt_budget_exhausted"),
        ):
            with self.subTest(max_pages=max_pages, attempts=attempts):
                transport = FixtureTransport(
                    {1: page(1, [row("123456")]), 2: page(2, [row("654321")])}
                )
                acquisition = acquire_szse_current_list(
                    REQUEST,
                    research_now=NOW,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=attempts, timeout_seconds=1
                    ),
                    max_pages=max_pages,
                )
                self.assertEqual(transport.calls, [1])
                self.assertEqual(acquisition.reported_count, 2)
                self.assertEqual(acquisition.pages_expected, 2)
                self.assertEqual(acquisition.pages_received, 1)
                self.assertEqual([r.code for r in acquisition.records], ["123456"])
                self.assertEqual(len(acquisition.lane_result.attempts), 1)
                self.assertFalse(acquisition.pages_complete)
                self.assertIn(code, acquisition.limitation_codes)

    def test_count_drift_retains_first_page_and_never_adopts_a_new_denominator(
        self,
    ) -> None:
        transport = FixtureTransport(
            {1: page(1, [row("123456")]), 2: page(2, [row("654321")], count=3)}
        )
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=3, timeout_seconds=1),
            max_pages=3,
        )
        self.assertEqual(transport.calls, [1, 2])
        self.assertEqual(acquisition.reported_count, 2)
        self.assertEqual(acquisition.pages_received, 1)
        self.assertFalse(acquisition.pages_complete)
        self.assertIn("pagination_count_drift", acquisition.limitation_codes)
        self.assertEqual([r.code for r in acquisition.records], ["123456"])

    def test_retry_consumes_the_same_lane_budget_and_drops_raw_exception_text(
        self,
    ) -> None:
        class RateLimitedTransport(FixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                number = int(parse_qs(urlparse(url).query)["PAGENO"][0])
                if number == 2 and 2 not in self.calls:
                    self.calls.append(2)
                    raise TransportError(
                        "rate_limited", "private-path /private/tmp/synthetic-secret"
                    )
                return super().get(url, headers)

        for budget, expected_calls in ((2, [1, 2]), (3, [1, 2, 2])):
            with self.subTest(budget=budget):
                transport = RateLimitedTransport(
                    {1: page(1, [row("123456")]), 2: page(2, [row("654321")])}
                )
                acquisition = acquire_szse_current_list(
                    REQUEST,
                    research_now=NOW,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=budget,
                        timeout_seconds=1,
                        rate_limit_backoffs=(0,),
                    ),
                    max_pages=2,
                )
                self.assertEqual(transport.calls, expected_calls)
                self.assertEqual(len(acquisition.lane_result.attempts), budget)
                self.assertEqual(acquisition.lane_result.attempts[1].state, "failed")
                self.assertEqual(acquisition.pages_complete, budget == 3)
                self.assertNotIn(
                    "synthetic-secret", json.dumps(acquisition.to_public_dict())
                )

    def test_timeout_retains_prefix_and_poison_prevents_an_overlapping_source_call(
        self,
    ) -> None:
        released = Event()

        class SlowTransport(FixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                number = int(parse_qs(urlparse(url).query)["PAGENO"][0])
                if number == 2:
                    released.wait(2)
                return super().get(url, headers)

        lanes = ProviderLanes()
        first = SlowTransport(
            {1: page(1, [row("123456")]), 2: page(2, [row("654321")])}
        )
        second = FixtureTransport({1: page(1, [row("111111")], count=1)})
        try:
            acquisition = acquire_szse_current_list(
                REQUEST,
                research_now=NOW,
                transport=first,
                lanes=lanes,
                policy=ProviderLanePolicy(
                    actual_attempt_budget=2,
                    timeout_seconds=0.02,
                    cancellation_grace_seconds=0.01,
                ),
                max_pages=2,
            )
            self.assertEqual(acquisition.reported_count, 2)
            self.assertEqual(acquisition.pages_received, 1)
            self.assertEqual(acquisition.lane_result.status, "timed_out")
            self.assertEqual(acquisition.lane_result.quiescence.state, "poisoned")
            successor = acquire_szse_current_list(
                REQUEST,
                research_now=NOW,
                transport=second,
                lanes=lanes,
                policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
                max_pages=1,
            )
            self.assertEqual(second.calls, [])
            self.assertEqual(successor.lane_result.status, "not_started")
            self.assertEqual(successor.source_result, "unavailable")
            self.assertEqual(successor.lane_result.attempts, ())
        finally:
            released.set()
        deadline = monotonic() + 1
        while lanes.quiescence("szse").state != "quiescent" and monotonic() < deadline:
            sleep(0.005)
        self.assertEqual(lanes.quiescence("szse").state, "quiescent")
        self.assertEqual(acquisition.pages_received, 1)
        self.assertEqual(acquisition.lane_result.quiescence.state, "poisoned")

    def test_copied_installed_skill_loads_and_executes_the_real_parser_with_transport_fixture(
        self,
    ) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        script = dedent("""
            import json
            import sys
            from datetime import datetime
            sys.path.insert(0, sys.argv[1])
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.bse_universe_source import prepare_bse_current_list_query
            assert prepare_bse_current_list_query(0, page_budget=1).parameters()["xxfcbj"] == ("2",)
            from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes
            from a_share_research.szse_universe_source import acquire_szse_current_list
            from a_share_research.universe_input import UniverseRequest
            class Fixture:
                def get(self, url, headers):
                    body = [{"error": None, "metadata": {"catalogid": "1110", "tabkey": "tab1", "pageno": 1, "pagesize": 20, "pagecount": 1, "recordcount": 1, "name": "A股列表", "cols": {"agdm": "A股代码", "agjc": "A股简称", "agssrq": "A股<br>上市日期"}, "subname": "2030-01-01 "}, "data": [{"agdm": "123456", "agjc": '<a href="http://www.szse.cn/certificate/individual/index.html?code=123456" target="_blank"><u>合成股份</u></a>', "agssrq": "2020-01-02"}]}]
                    return HttpResponse(200, "application/json", json.dumps(body).encode(), datetime.fromisoformat("2026-10-14T10:01:00+08:00"))
            frozen = datetime.fromisoformat("2026-10-14T10:00:00+08:00")
            acquisition = acquire_szse_current_list(UniverseRequest("2026-10-14", frozen.isoformat()), research_now=frozen, transport=Fixture(), lanes=ProviderLanes(), policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1), max_pages=1)
            print(json.dumps(acquisition.to_public_dict()))
        """)
        with tempfile.TemporaryDirectory(prefix="asr-122-installed-") as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                skill_root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            run = subprocess.run(
                [sys.executable, "-c", script, str(installed / "scripts")],
                cwd=temporary,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "en_US.UTF-8",
                    "PYTHONUTF8": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": temporary,
                },
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            public = json.loads(run.stdout)
            self.assertEqual(public["reported_count"], 1)
            self.assertEqual(public["records"][0]["code"], "123456")
            self.assertEqual(public["records"][0]["name"], "合成股份")
            self.assertNotIn("href", run.stdout)
            self.assertEqual(
                public["attempts"],
                [{"provider_group": "szse", "sequence": 1, "state": "succeeded"}],
            )
            self.assertFalse(public["membership_proven"])
            self.assertEqual(public["source_result"], "partial")
            self.assertNotIn(temporary, run.stdout)

    def test_missing_source_error_marker_is_not_treated_as_a_successful_report(
        self,
    ) -> None:
        body = json.loads(page(1, [row("123456")], count=1))
        del body[1]["error"]
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: json.dumps(body).encode()}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.source_result, "unavailable")
        self.assertIsNone(acquisition.reported_count)
        self.assertEqual(acquisition.records, ())
        self.assertEqual(len(acquisition.lane_result.attempts), 1)

    def test_access_failure_keeps_attempted_independent_from_source_result(
        self,
    ) -> None:
        class DeniedTransport(FixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                return replace(
                    response,
                    status=403,
                    body=b"synthetic private-path /private/tmp/not-public",
                )

        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=DeniedTransport({1: page(1, [row("123456")], count=1)}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.source_result, "credential_blocked")
        self.assertIsNone(acquisition.reported_count)
        self.assertEqual(acquisition.records, ())
        public = acquisition.to_public_dict()
        self.assertTrue(public["attempted"])
        self.assertNotIn("not-public", json.dumps(public))
        self.assertEqual(acquisition.lane_result.attempts[0].state, "succeeded")

    def test_declared_zero_rows_proves_only_current_page_closure_not_zero_expected_universe(
        self,
    ) -> None:
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: page(1, [], count=0)}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.reported_count, 0)
        self.assertTrue(acquisition.pages_complete)
        self.assertEqual(acquisition.source_result, "partial")
        self.assertFalse(acquisition.to_public_dict()["membership_proven"])
        self.assertNotIn("expected_count", acquisition.to_public_dict())

    def test_g_observed_anchor_and_underline_name_is_normalized_without_retaining_markup(
        self,
    ) -> None:
        markup = '<a href="http://www.szse.cn/certificate/individual/index.html?code=123456" target="_blank"><u>合成股份</u></a>'
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: page(1, [row("123456", markup)], count=1)}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(len(acquisition.records), 1)
        self.assertEqual(acquisition.records[0].name, "合成股份")
        self.assertTrue(acquisition.pages_complete)
        self.assertIsNone(acquisition.records[0].available_at)
        public = json.dumps(acquisition.to_public_dict(), ensure_ascii=False)
        self.assertNotIn("href", public)
        self.assertNotIn("http://", public)
        self.assertNotIn("<u>", public)
        self.assertEqual(acquisition.source_result, "partial")

    def test_only_the_g_observed_name_markup_for_the_same_security_is_accepted(
        self,
    ) -> None:
        invalid = (
            '<a href="http://www.szse.cn/certificate/individual/index.html?code=654321" target="_blank"><u>错误身份</u></a>',
            '<a href="http://example.invalid/certificate/individual/index.html?code=123456" target="_blank"><u>非来源链接</u></a>',
            '<a href="http://www.szse.cn/certificate/individual/index.html?code=123456" target="_blank" onclick="synthetic-secret"><u>错误属性</u></a>',
            '<a href="http://www.szse.cn/certificate/individual/index.html?code=123456" target="_blank"><u>未闭合',
            "<script>synthetic-secret</script>合成股份",
            "<span>未证明的形状</span>",
        )
        for markup in invalid:
            with self.subTest(markup=markup):
                body = json.loads(page(1, [row("111111"), row("123456", markup)]))
                body[1]["metadata"].update(pagesize=2, pagecount=1)
                acquisition = acquire_szse_current_list(
                    REQUEST,
                    research_now=NOW,
                    transport=FixtureTransport({1: json.dumps(body).encode()}),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=1, timeout_seconds=1
                    ),
                    max_pages=1,
                )
                self.assertEqual([r.code for r in acquisition.records], ["111111"])
                self.assertEqual(acquisition.reported_count, 2)
                self.assertFalse(acquisition.pages_complete)
                self.assertEqual(acquisition.rejected_rows, 1)
                self.assertNotIn(
                    "synthetic-secret", json.dumps(acquisition.to_public_dict())
                )

    def test_i_metadata_columns_must_prove_the_identity_labels(self) -> None:
        body = json.loads(page(1, [row("123456")], count=1))
        body[1]["metadata"]["cols"]["agssrq"] = "公告日期"
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: json.dumps(body).encode()}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.source_result, "unavailable")
        self.assertEqual(acquisition.records, ())
        self.assertIsNone(acquisition.reported_count)
        self.assertIn("unproved_current_list_columns", acquisition.limitation_codes)

    def test_i_subname_date_and_padding_are_normalized_without_becoming_time_authority(
        self,
    ) -> None:
        markup = "<a href='http://www.szse.cn/certificate/individual/index.html?code=123456' target='_blank'><u>合  成Ａ</u></a>"
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport({1: page(1, [row("123456", markup)], count=1)}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.records[0].name, "合成Ａ")
        self.assertEqual(acquisition.records[0].source_list_date_label, "2030-01-01")
        self.assertEqual(acquisition.source_list_date_labels, ("2030-01-01",))
        self.assertEqual(acquisition.information_cutoff, NOW.isoformat())
        self.assertIsNone(acquisition.records[0].available_at)
        self.assertIsNone(acquisition.records[0].membership_effective_at)
        self.assertFalse(acquisition.to_public_dict()["membership_proven"])
        self.assertIn(
            "list_date_label_semantics_unproved", acquisition.limitation_codes
        )

    def test_i_list_date_label_drift_keeps_the_first_page_without_closing_pagination(
        self,
    ) -> None:
        second = json.loads(page(2, [row("654321")]))
        second[1]["metadata"]["subname"] = "2030-01-02 "
        acquisition = acquire_szse_current_list(
            REQUEST,
            research_now=NOW,
            transport=FixtureTransport(
                {1: page(1, [row("123456")]), 2: json.dumps(second).encode()}
            ),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        self.assertEqual([r.code for r in acquisition.records], ["123456"])
        self.assertEqual(acquisition.reported_count, 2)
        self.assertEqual(acquisition.source_list_date_labels, ("2030-01-01",))
        self.assertFalse(acquisition.pages_complete)
        self.assertIn("list_date_label_drift", acquisition.limitation_codes)
        self.assertEqual(len(acquisition.lane_result.attempts), 2)


if __name__ == "__main__":
    unittest.main()
