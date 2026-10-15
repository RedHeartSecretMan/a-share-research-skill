from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from threading import Event
from time import monotonic, sleep
from urllib.parse import parse_qs
from dataclasses import FrozenInstanceError, dataclass

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.bse_universe_source import (  # noqa: E402
    BseCurrentIdentityAdapter,
    acquire_bse_current_list,
    prepare_bse_current_list_query,
)

from a_share_research.identity_sources import HttpResponse, TransportError  # noqa: E402
from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes  # noqa: E402
from a_share_research.universe_input import (  # noqa: E402
    ExchangeListOutcome,
    ExchangeListRecord,
    UniverseRequest,
)
from a_share_research.a_share_universe import build_as_of_a_share_universe  # noqa: E402

FROZEN = datetime.fromisoformat("2026-10-15T10:00:00+08:00")
REQUEST = UniverseRequest("2026-10-15", FROZEN.isoformat())


def identity_row(code: str, name: str = "合成公司") -> dict[str, str]:
    # Synthetic identities/dates, never retained source sample values.
    return {
        "xxzqdm": code,
        "xxzqjc": name,
        "fxssrq": "20100102",
        "xxgprq": "20100102",
        "xxfcbj": "2",
        "xxzqjb": "T",
        "xxjsrq": "20300101",
        "xxgxsj": "10101",
    }


def bse_page(number: int, rows: list[dict[str, str]], *, count: int = 21) -> bytes:
    size = 20
    total_pages = max(1, (count + size - 1) // size)
    payload = [
        {
            "content": rows,
            "firstPage": number == 0,
            "lastPage": number == total_pages - 1,
            "number": number,
            "numberOfElements": len(rows),
            "size": size,
            "sort": None,
            "totalElements": count,
            "totalPages": total_pages,
        }
    ]
    return ("fixture_callback(" + json.dumps(payload) + ");").encode()


class BseFixtureTransport:
    def __init__(self, pages: dict[int, bytes]) -> None:
        self.pages = pages
        self.calls: list[int] = []
        self.bodies: list[bytes] = []

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        assert url == "https://www.bse.cn/nqxxController/nqxxCnzq.do"
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"
        params = parse_qs(body.decode(), keep_blank_values=True)
        assert params["xxfcbj[]"] == ["2"]
        assert params["xxzqdm"] == [""]
        assert params["typejb"] == ["T"]
        assert "date" not in params
        number = int(params["page"][0])
        self.calls.append(number)
        self.bodies.append(body)
        return HttpResponse(
            200,
            "application/javascript",
            self.pages[number],
            datetime.fromisoformat(f"2026-10-15T10:01:0{number}+08:00"),
        )


@dataclass(frozen=True)
class ProvenBseListFixture:
    exchange: str = "BSE"
    operation_id: str = "fixture_bse_expected_list@1"

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        codes = [str(120000 + i) for i in range(20)] + ["340000"]
        records = tuple(
            ExchangeListRecord(
                "BSE",
                code,
                "合成公司",
                "A_SHARE",
                "2026-10-15",
                "2026-10-15T09:00:00+08:00",
                "2026-10-15T10:01:00+08:00",
                f"fixture-list:BSE:{code}",
            )
            for code in codes
        )
        return ExchangeListOutcome(
            self.operation_id, "BSE", "2026-10-15", True, "complete", 21, 1, 1, records
        )


class BseCurrentListQueryTests(unittest.TestCase):
    def test_h_verified_bracket_form_keeps_g_semantic_parameters_detached(
        self,
    ) -> None:
        query = prepare_bse_current_list_query(0, page_budget=2)
        self.assertEqual(query.method, "POST")
        self.assertEqual(
            query.endpoint, "https://www.bse.cn/nqxxController/nqxxCnzq.do"
        )
        self.assertEqual(
            query.parameters(),
            {
                "page": 0,
                "typejb": "T",
                "xxfcbj": ("2",),
                "xxzqdm": "",
                "sortfield": "xxzqdm",
                "sorttype": "asc",
            },
        )
        self.assertEqual(query.array_wire_encoding, "form_brackets")
        self.assertIn("精选层", query.display_date_qualifier)
        self.assertIsNone(query.membership_effective_at)
        self.assertIsNone(query.available_at)
        with self.assertRaises(FrozenInstanceError):
            query.page_index = 1  # type: ignore[misc]
        mutable = query.parameters()
        mutable["xxzqdm"] = "832000"
        self.assertEqual(query.parameters()["xxzqdm"], "")

    def test_declared_page_budget_is_finite_and_does_not_invent_the_sources_page_count(
        self,
    ) -> None:
        self.assertEqual(
            prepare_bse_current_list_query(1, page_budget=2).parameters()["page"], 1
        )
        for index, budget in ((-1, 2), (2, 2), (0, 0), (True, 2), (0, True)):
            with self.subTest(index=index, budget=budget):
                with self.assertRaises(ValueError):
                    prepare_bse_current_list_query(index, page_budget=budget)

    def test_h_post_jsonp_acquisition_counts_real_pages_and_preserves_unknown_time_meanings(
        self,
    ) -> None:
        transport = BseFixtureTransport(
            {
                0: bse_page(0, [identity_row(str(120000 + i)) for i in range(20)]),
                1: bse_page(1, [identity_row("340000")]),
            }
        )
        lanes = ProviderLanes()
        acquisition = acquire_bse_current_list(
            REQUEST,
            research_now=FROZEN,
            transport=transport,
            lanes=lanes,
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        self.assertEqual(transport.calls, [0, 1])
        self.assertEqual(acquisition.reported_count, 21)
        self.assertEqual(acquisition.pages_expected, 2)
        self.assertTrue(acquisition.pages_complete)
        self.assertEqual(len(acquisition.records), 21)
        record = acquisition.records[0]
        self.assertEqual(record.code, "120000")
        self.assertEqual(record.displayed_listing_date, "2010-01-02")
        self.assertEqual(record.source_date_label, "20300101")
        self.assertEqual(record.source_update_label, "10101")
        self.assertIsNone(record.available_at)
        self.assertIsNone(record.membership_effective_at)
        self.assertEqual(
            acquisition.records[-1].retrieved_at, "2026-10-15T10:01:01+08:00"
        )
        self.assertEqual(acquisition.source_result, "partial")
        self.assertEqual(len(acquisition.lane_result.attempts), 2)
        self.assertTrue(lanes.is_authoritative(acquisition.lane_result))
        public = json.dumps(acquisition.to_public_dict(), ensure_ascii=False)
        self.assertNotIn("http", public)
        self.assertNotIn("fixture_callback", public)
        self.assertIn("精选层", public)
        self.assertFalse(acquisition.to_public_dict()["membership_proven"])

    def test_h_identity_adapter_crosses_public_110_seam_without_promoting_unknown_times(
        self,
    ) -> None:
        transport = BseFixtureTransport(
            {
                0: bse_page(0, [identity_row(str(120000 + i)) for i in range(20)]),
                1: bse_page(1, [identity_row("340000")]),
            }
        )
        lanes = ProviderLanes()
        adapter = BseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=lanes,
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        universe = build_as_of_a_share_universe(
            REQUEST,
            research_now=FROZEN,
            exchange_list_adapters=(ProvenBseListFixture(),),
            identity_adapters=(adapter,),
            timeout_seconds=1,
        )
        partition = next(p for p in universe.partitions if p.exchange == "BSE")
        self.assertEqual(
            (partition.expected, partition.resolved, partition.failed), (21, 21, 0)
        )
        self.assertFalse(partition.complete)
        self.assertFalse(universe.full_a_share_universe)
        self.assertEqual(len(partition.securities), 21)
        for security in partition.securities:
            self.assertIsNone(security.effective_from)
            self.assertIsNone(security.identity_available_at)
            self.assertIsNone(security.listing_state)
            self.assertIn("as_of_unproven", security.unranked_reasons)
            self.assertIn("listing_state_unproven", security.unranked_reasons)
        self.assertEqual(transport.calls, [0, 1])
        self.assertIsNotNone(adapter.last_acquisition)
        self.assertTrue(lanes.is_authoritative(adapter.last_acquisition.lane_result))
        self.assertEqual(len(adapter.last_acquisition.lane_result.attempts), 2)

    def test_static_jsonp_rejects_executable_tail_and_wrong_page_wrapper(self) -> None:
        valid = bse_page(0, [identity_row("120000")], count=1)
        bodies = (
            valid + b"synthetic_callback('synthetic-secret');",
            b"fixture_callback(__import__('os'));",
            b"fixture_callback({});",
            b"fixture_callback([{},{}]);",
        )
        for body in bodies:
            with self.subTest(body=body[:40]):
                acquisition = acquire_bse_current_list(
                    REQUEST,
                    research_now=FROZEN,
                    transport=BseFixtureTransport({0: body}),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=1, timeout_seconds=1
                    ),
                    max_pages=1,
                )
                self.assertEqual(acquisition.source_result, "unavailable")
                self.assertEqual(acquisition.records, ())
                self.assertIsNone(acquisition.reported_count)
                self.assertEqual(len(acquisition.lane_result.attempts), 1)
                self.assertNotIn(
                    "synthetic-secret", json.dumps(acquisition.to_public_dict())
                )

    def test_pagination_budget_missing_page_and_count_drift_preserve_first_page(
        self,
    ) -> None:
        first_rows = [identity_row(str(120000 + i)) for i in range(20)]
        for max_pages, budget, second, expected_calls, code in (
            (1, 2, bse_page(1, [identity_row("340000")]), [0], "page_budget_exhausted"),
            (
                2,
                1,
                bse_page(1, [identity_row("340000")]),
                [0],
                "actual_attempt_budget_exhausted",
            ),
            (2, 2, None, [0, 1], "provider_call_failed"),
            (
                2,
                2,
                bse_page(1, [identity_row("340000")], count=22),
                [0, 1],
                "pagination_count_drift",
            ),
        ):
            with self.subTest(max_pages=max_pages, budget=budget, code=code):
                pages = {0: bse_page(0, first_rows)}
                if second is not None:
                    pages[1] = second
                transport = BseFixtureTransport(pages)
                acquisition = acquire_bse_current_list(
                    REQUEST,
                    research_now=FROZEN,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=budget, timeout_seconds=1
                    ),
                    max_pages=max_pages,
                )
                self.assertEqual(transport.calls, expected_calls)
                self.assertEqual(acquisition.reported_count, 21)
                self.assertEqual(acquisition.pages_received, 1)
                self.assertEqual(len(acquisition.records), 20)
                self.assertFalse(acquisition.pages_complete)
                self.assertIn(code, acquisition.limitation_codes)

    def test_duplicate_or_wrong_selector_rows_never_close_a_smaller_denominator(
        self,
    ) -> None:
        first_rows = [identity_row(str(120000 + i)) for i in range(20)]
        for final, count in (
            (identity_row("120000"), 20),
            (dict(identity_row("340000"), xxfcbj="0"), 20),
            (dict(identity_row("340000"), xxzqjb="X"), 20),
            (identity_row("340000", "/private/tmp/synthetic-secret"), 20),
        ):
            with self.subTest(final=final["xxzqdm"], category=final["xxzqjb"]):
                acquisition = acquire_bse_current_list(
                    REQUEST,
                    research_now=FROZEN,
                    transport=BseFixtureTransport(
                        {0: bse_page(0, first_rows), 1: bse_page(1, [final])}
                    ),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=2, timeout_seconds=1
                    ),
                    max_pages=2,
                )
                self.assertEqual(acquisition.reported_count, 21)
                self.assertEqual(len(acquisition.records), count)
                self.assertFalse(acquisition.pages_complete)
                self.assertEqual(acquisition.source_result, "partial")
                self.assertNotIn(
                    "synthetic-secret", json.dumps(acquisition.to_public_dict())
                )

    def test_current_source_rejects_historical_day_without_post_or_fabricated_zero(
        self,
    ) -> None:
        transport = BseFixtureTransport({})
        acquisition = acquire_bse_current_list(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(acquisition.lane_result.attempts, ())
        self.assertIsNone(acquisition.reported_count)
        self.assertEqual(acquisition.source_result, "unavailable")
        self.assertIn(
            "current_list_cannot_prove_historical_membership",
            acquisition.limitation_codes,
        )

    def test_h_rate_limit_retry_shares_page_budget_and_attempt_authority(self) -> None:
        class RateLimitedPost(BseFixtureTransport):
            def post(
                self, url: str, headers: dict[str, str], body: bytes
            ) -> HttpResponse:
                number = int(parse_qs(body.decode())["page"][0])
                if number == 1 and 1 not in self.calls:
                    self.calls.append(1)
                    raise TransportError(
                        "rate_limited", "synthetic-secret /private/tmp/never-public"
                    )
                return super().post(url, headers, body)

        for budget, calls in ((2, [0, 1]), (3, [0, 1, 1])):
            with self.subTest(budget=budget):
                transport = RateLimitedPost(
                    {
                        0: bse_page(
                            0, [identity_row(str(120000 + i)) for i in range(20)]
                        ),
                        1: bse_page(1, [identity_row("340000")]),
                    }
                )
                acquisition = acquire_bse_current_list(
                    REQUEST,
                    research_now=FROZEN,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=budget,
                        timeout_seconds=1,
                        rate_limit_backoffs=(0,),
                    ),
                    max_pages=2,
                )
                self.assertEqual(transport.calls, calls)
                self.assertEqual(len(acquisition.lane_result.attempts), budget)
                self.assertEqual(acquisition.lane_result.attempts[1].state, "failed")
                self.assertEqual(acquisition.pages_complete, budget == 3)
                self.assertNotIn(
                    "synthetic-secret", json.dumps(acquisition.to_public_dict())
                )

    def test_timeout_poison_blocks_same_group_successor_and_keeps_snapshot_immutable(
        self,
    ) -> None:
        released = Event()

        class SlowPost(BseFixtureTransport):
            def post(
                self, url: str, headers: dict[str, str], body: bytes
            ) -> HttpResponse:
                if parse_qs(body.decode())["page"] == ["1"]:
                    released.wait(2)
                return super().post(url, headers, body)

        lanes = ProviderLanes()
        transport = SlowPost(
            {
                0: bse_page(0, [identity_row(str(120000 + i)) for i in range(20)]),
                1: bse_page(1, [identity_row("340000")]),
            }
        )
        successor_transport = BseFixtureTransport(
            {0: bse_page(0, [identity_row("560000")], count=1)}
        )
        try:
            first = acquire_bse_current_list(
                REQUEST,
                research_now=FROZEN,
                transport=transport,
                lanes=lanes,
                policy=ProviderLanePolicy(
                    actual_attempt_budget=2,
                    timeout_seconds=0.02,
                    cancellation_grace_seconds=0.01,
                ),
                max_pages=2,
            )
            self.assertEqual(first.pages_received, 1)
            self.assertEqual(len(first.records), 20)
            self.assertEqual(first.lane_result.quiescence.state, "poisoned")
            successor = acquire_bse_current_list(
                REQUEST,
                research_now=FROZEN,
                transport=successor_transport,
                lanes=lanes,
                policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
                max_pages=1,
            )
            self.assertEqual(successor_transport.calls, [])
            self.assertEqual(successor.lane_result.status, "not_started")
            self.assertIsNone(successor.reported_count)
        finally:
            released.set()
        deadline = monotonic() + 1
        while lanes.quiescence("bse").state != "quiescent" and monotonic() < deadline:
            sleep(0.005)
        self.assertEqual(lanes.quiescence("bse").state, "quiescent")
        self.assertEqual(first.pages_received, 1)
        self.assertEqual(first.lane_result.quiescence.state, "poisoned")

    def test_110_name_mismatch_keeps_the_expected_security_failed_instead_of_deleting_it(
        self,
    ) -> None:
        first_rows = [
            identity_row(str(120000 + i), "变更后的名字" if i == 0 else "合成公司")
            for i in range(20)
        ]
        adapter = BseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=BseFixtureTransport(
                {0: bse_page(0, first_rows), 1: bse_page(1, [identity_row("340000")])}
            ),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            max_pages=2,
        )
        universe = build_as_of_a_share_universe(
            REQUEST,
            research_now=FROZEN,
            exchange_list_adapters=(ProvenBseListFixture(),),
            identity_adapters=(adapter,),
            timeout_seconds=1,
        )
        partition = next(p for p in universe.partitions if p.exchange == "BSE")
        self.assertEqual(
            (partition.expected, partition.resolved, partition.failed), (21, 20, 1)
        )
        missing = next(s for s in partition.securities if s.code == "120000")
        self.assertIn("source_failed", missing.unranked_reasons)
        self.assertEqual(missing.name, "合成公司")
        self.assertFalse(partition.complete)

    def test_copied_installed_bse_parser_and_identity_adapter_keep_public_unproven_reconciliation(
        self,
    ) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        script = dedent("""
            import json
            import sys
            from datetime import datetime
            from urllib.parse import parse_qs
            sys.path.insert(0, sys.argv[1])
            from a_share_research.a_share_universe import build_as_of_a_share_universe
            from a_share_research.bse_universe_source import BseCurrentIdentityAdapter
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes
            from a_share_research.universe_input import ExchangeListOutcome, ExchangeListRecord, UniverseRequest
            codes = [str(120000 + i) for i in range(20)] + ["340000"]
            class FixtureTransport:
                def post(self, url, headers, body):
                    params = parse_qs(body.decode(), keep_blank_values=True)
                    assert params["xxfcbj[]"] == ["2"] and params["xxzqdm"] == [""]
                    number = int(params["page"][0])
                    selected = codes[:20] if number == 0 else codes[20:]
                    rows = [{"xxzqdm": c, "xxzqjc": "合成公司", "fxssrq": "20100102", "xxgprq": "20100102", "xxfcbj": "2", "xxzqjb": "T", "xxjsrq": "20300101", "xxgxsj": "10101"} for c in selected]
                    page = [{"content": rows, "number": number, "numberOfElements": len(rows), "size": 20, "sort": None, "totalPages": 2, "totalElements": 21, "firstPage": number == 0, "lastPage": number == 1}]
                    return HttpResponse(200, "application/javascript", ("fixture_callback(" + json.dumps(page) + ");").encode(), datetime.fromisoformat("2026-10-15T10:01:00+08:00"))
            class ProvenListFixture:
                exchange = "BSE"
                operation_id = "fixture_bse_expected_list@1"
                def acquire(self, request, timeout_seconds):
                    records = tuple(ExchangeListRecord("BSE", c, "合成公司", "A_SHARE", "2026-10-15", "2026-10-15T09:00:00+08:00", "2026-10-15T10:01:00+08:00", "fixture-list:BSE:" + c) for c in codes)
                    return ExchangeListOutcome(self.operation_id, "BSE", "2026-10-15", True, "complete", 21, 1, 1, records)
            frozen = datetime.fromisoformat("2026-10-15T10:00:00+08:00")
            lanes = ProviderLanes()
            adapter = BseCurrentIdentityAdapter(research_now=frozen, transport=FixtureTransport(), lanes=lanes, policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1), max_pages=2)
            universe = build_as_of_a_share_universe(UniverseRequest("2026-10-15", frozen.isoformat()), research_now=frozen, exchange_list_adapters=(ProvenListFixture(),), identity_adapters=(adapter,), timeout_seconds=1)
            partition = next(p for p in universe.partitions if p.exchange == "BSE")
            assert lanes.is_authoritative(adapter.last_acquisition.lane_result)
            print(json.dumps({"partition": partition.to_public_dict(), "source": adapter.last_acquisition.to_public_dict()}))
        """)
        with tempfile.TemporaryDirectory(prefix="asr-122-h-installed-") as temporary:
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
            self.assertEqual(
                public["partition"]["reconciliation"],
                {"expected": 21, "resolved": 21, "failed": 0},
            )
            self.assertFalse(public["partition"]["complete"])
            self.assertEqual(len(public["source"]["attempts"]), 2)
            self.assertFalse(public["source"]["membership_proven"])
            self.assertIsNone(public["partition"]["securities"][0]["effective_from"])
            self.assertNotIn(temporary, run.stdout)
            self.assertNotIn("fixture_callback", run.stdout)

    def test_unobserved_bse_page_size_does_not_expand_the_source_contract(self) -> None:
        body = bse_page(0, [identity_row("120000")], count=1).replace(
            b'"size": 20', b'"size": 99999'
        )
        acquisition = acquire_bse_current_list(
            REQUEST,
            research_now=FROZEN,
            transport=BseFixtureTransport({0: body}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            max_pages=1,
        )
        self.assertEqual(acquisition.source_result, "unavailable")
        self.assertIsNone(acquisition.reported_count)
        self.assertEqual(acquisition.records, ())
        self.assertEqual(len(acquisition.lane_result.attempts), 1)
        self.assertIn("unproved_current_list_page_size", acquisition.limitation_codes)


if __name__ == "__main__":
    unittest.main()
