from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from threading import Event
from time import monotonic, sleep
from urllib.parse import parse_qs, urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.a_share_universe import build_as_of_a_share_universe  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes  # noqa: E402
from a_share_research.sse_universe_source import (  # noqa: E402
    SseCurrentIdentityAdapter,
    SseMainboardCurrentIdentityAdapter,
    acquire_sse_mainboard_current_list,
    acquire_sse_list_partition,
    acquire_sse_current_lists,
)
from a_share_research.universe_input import (  # noqa: E402
    ExchangeListOutcome,
    ExchangeListRecord,
    UniverseRequest,
)

FROZEN = datetime.fromisoformat("2026-10-15T10:00:00+08:00")


def identity_row(code: str, name: str = "合成公司") -> dict[str, str]:
    # P proves these five existing parser fields, not arbitrary category/time tokens.
    return {
        "SECURITY_CODE_A": code,
        "SECURITY_ABBR_A": name,
        "FULLNAME": "合成发行主体股份有限公司",
        "STATE_CODE_A_DESC": "上市",
        "COMPANY_CODE": code,
    }


class SseFixtureTransport:
    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlparse(url)
        self.assert_request(parsed.path, headers)
        params = parse_qs(parsed.query)
        assert params["sqlId"] == ["COMMON_SSE_ZQPZ_GP_GPLB_C"]
        assert params["isPagination"] == ["false"]
        code = params["productid"][0]
        self.calls.append(code)
        return HttpResponse(
            200,
            "application/json",
            json.dumps({"result": self.rows[code], "pageHelp": {"total": 0}}).encode(),
            datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
        )

    @staticmethod
    def assert_request(path: str, headers: dict[str, str]) -> None:
        assert path == "/commonQuery.do"
        assert headers == {
            "Accept": "application/json",
            "Referer": "https://www.sse.com.cn/assortment/stock/list/",
            "User-Agent": "a-share-research-skill/1",
        }


@dataclass(frozen=True)
class ProvenSseListFixture:
    codes: tuple[str, ...] = ("120001",)
    exchange: str = "SSE"
    operation_id: str = "fixture_sse_expected_list@1"

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        # Synthetic independently proved A-share list. This is not a production roster.
        records = tuple(
            ExchangeListRecord(
                "SSE",
                code,
                "合成公司",
                "A_SHARE",
                "2026-10-15",
                "2026-10-15T09:00:00+08:00",
                "2026-10-15T10:01:00+08:00",
                f"fixture-list:SSE:{code}",
            )
            for code in self.codes
        )
        return ExchangeListOutcome(
            self.operation_id,
            "SSE",
            "2026-10-15",
            True,
            "complete",
            len(records),
            1,
            1,
            records,
        )


class SseCurrentIdentityTests(unittest.TestCase):
    def test_p_point_identity_uses_real_parser_and_110_keeps_unknown_time_unranked(
        self,
    ) -> None:
        transport = SseFixtureTransport({"120001": [identity_row("120001")]})
        lanes = ProviderLanes()
        adapter = SseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=lanes,
            policy=ProviderLanePolicy(2, 1.0),
            max_securities=2,
        )
        universe = build_as_of_a_share_universe(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            exchange_list_adapters=(ProvenSseListFixture(),),
            identity_adapters=(adapter,),
            research_now=FROZEN,
        )
        public = universe.to_public_dict()
        sse = public["partitions"]["SSE"]
        self.assertEqual(
            sse["reconciliation"], {"expected": 1, "resolved": 1, "failed": 0}
        )
        self.assertFalse(public["full_a_share_universe"])
        self.assertFalse(sse["complete"])
        self.assertEqual(transport.calls, ["120001"])
        self.assertEqual(
            sse["securities"][0]["unranked_reasons"],
            ["listing_state_unproven", "as_of_unproven"],
        )
        acquired = adapter.last_acquisition
        self.assertIsNotNone(acquired)
        assert acquired is not None
        self.assertTrue(lanes.is_authoritative(acquired.lane_result))
        self.assertEqual(len(acquired.lane_result.attempts), 1)
        self.assertEqual(acquired.source_result, "complete")
        self.assertFalse(acquired.to_public_dict()["membership_proven"])
        self.assertEqual(acquired.records[0].observed_at, "2026-10-15T10:01:00+08:00")
        self.assertIsNone(acquired.records[0].available_at)
        self.assertNotIn("https://", json.dumps(acquired.to_public_dict()))

    def test_http_429_retry_and_initial_queries_share_one_actual_attempt_budget(
        self,
    ) -> None:
        class RateLimited(SseFixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                if len(self.calls) == 1:
                    return HttpResponse(
                        429, "application/json", b"{}", response.retrieved_at
                    )
                return response

        transport = RateLimited(
            {code: [identity_row(code)] for code in ("120001", "120002")}
        )
        adapter = SseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(3, 1.0, rate_limit_backoffs=(0.0,)),
            max_securities=2,
        )
        outcome = adapter.resolve(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            tuple(ProvenSseListFixture(("120001", "120002")).acquire(None, 1).records),
            1.0,
        )
        self.assertEqual(outcome.source_result, "complete")
        self.assertEqual(transport.calls, ["120001", "120001", "120002"])
        assert adapter.last_acquisition is not None
        self.assertEqual(
            [item.state for item in adapter.last_acquisition.lane_result.attempts],
            ["failed", "succeeded", "succeeded"],
        )

    def test_partial_identity_preserves_expected_and_does_not_drop_unknown_or_wrong_names(
        self,
    ) -> None:
        cases = (
            [],
            [identity_row("120001", "另一合成公司")],
            [identity_row("120001"), identity_row("120001")],
        )
        for invalid_rows in cases:
            with self.subTest(rows=invalid_rows):
                transport = SseFixtureTransport(
                    {"120001": invalid_rows, "120002": [identity_row("120002")]}
                )
                adapter = SseCurrentIdentityAdapter(
                    research_now=FROZEN,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(2, 1.0),
                    max_securities=2,
                )
                universe = build_as_of_a_share_universe(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    exchange_list_adapters=(
                        ProvenSseListFixture(("120001", "120002")),
                    ),
                    identity_adapters=(adapter,),
                )
                sse = next(
                    partition
                    for partition in universe.partitions
                    if partition.exchange == "SSE"
                )
                self.assertEqual((sse.expected, sse.resolved, sse.failed), (2, 1, 1))
                self.assertEqual(len(sse.securities), 2)
                self.assertIn("source_failed", sse.securities[0].unranked_reasons)
                assert adapter.last_acquisition is not None
                self.assertEqual(adapter.last_acquisition.source_result, "partial")

    def test_historical_request_never_calls_current_point_selector_or_backdates_observation(
        self,
    ) -> None:
        transport = SseFixtureTransport({"120001": [identity_row("120001")]})
        adapter = SseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1.0),
            max_securities=1,
        )
        outcome = adapter.resolve(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            tuple(ProvenSseListFixture().acquire(None, 1).records),
            1.0,
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual((outcome.expected_count, len(outcome.records)), (1, 0))
        self.assertFalse(outcome.attempted)
        self.assertEqual(outcome.source_result, "unavailable")
        assert adapter.last_acquisition is not None
        self.assertIn(
            "current_identity_cannot_prove_historical_membership",
            adapter.last_acquisition.limitation_codes,
        )
        self.assertEqual(
            adapter.last_acquisition.to_public_dict()["research_now"],
            FROZEN.isoformat(),
        )

    def test_point_budget_and_actual_attempt_budget_do_not_shrink_expected_denominator(
        self,
    ) -> None:
        for maximum, budget, reason in (
            (1, 5, "security_query_budget_exhausted"),
            (2, 1, "actual_attempt_budget_exhausted"),
        ):
            with self.subTest(maximum=maximum, budget=budget):
                transport = SseFixtureTransport(
                    {code: [identity_row(code)] for code in ("120001", "120002")}
                )
                adapter = SseCurrentIdentityAdapter(
                    research_now=FROZEN,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(budget, 1.0),
                    max_securities=maximum,
                )
                outcome = adapter.resolve(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    tuple(
                        ProvenSseListFixture(("120001", "120002"))
                        .acquire(None, 1)
                        .records
                    ),
                    1.0,
                )
                self.assertEqual(outcome.source_result, "partial")
                self.assertEqual((outcome.expected_count, len(outcome.records)), (2, 1))
                self.assertEqual(transport.calls, ["120001"])
                assert adapter.last_acquisition is not None
                self.assertIn(reason, adapter.last_acquisition.limitation_codes)
                self.assertEqual(len(adapter.last_acquisition.lane_result.attempts), 1)

    def test_opaque_type_b_component_and_future_date_do_not_infer_category_or_effective_time(
        self,
    ) -> None:
        row = identity_row("120001")
        row.update(
            TYPE="0",
            SECURITY_CODE_B="910001",
            QIANYI_DATE="2030-01-01 11:22:33",
            STATUS="opaque",
        )
        transport = SseFixtureTransport({"120001": [row]})
        adapter = SseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1.0),
            max_securities=1,
        )
        outcome = adapter.resolve(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            tuple(ProvenSseListFixture().acquire(None, 1).records),
            1.0,
        )
        self.assertEqual(outcome.source_result, "complete")
        self.assertIsNone(outcome.records[0].effective_from)
        self.assertIsNone(outcome.records[0].listing_state)
        assert adapter.last_acquisition is not None
        public = adapter.last_acquisition.to_public_dict()
        self.assertNotIn("2030-01-01", json.dumps(public))
        self.assertNotIn("910001", json.dumps(public))
        public["records"].clear()
        self.assertEqual(len(adapter.last_acquisition.to_public_dict()["records"]), 1)

    def test_missing_result_or_empty_result_is_unavailable_not_observed_empty(
        self,
    ) -> None:
        class Invalid(SseFixtureTransport):
            def __init__(self, body: bytes) -> None:
                super().__init__({"120001": []})
                self.body = body

            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                return HttpResponse(
                    200, "application/json", self.body, response.retrieved_at
                )

        for body in (
            b'{"success":"false","error":"unsafe /tmp/private-secret"}',
            b'{"result":[],"pageHelp":{"total":0}}',
        ):
            with self.subTest(body=body):
                adapter = SseCurrentIdentityAdapter(
                    research_now=FROZEN,
                    transport=Invalid(body),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1.0),
                    max_securities=1,
                )
                outcome = adapter.resolve(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    tuple(ProvenSseListFixture().acquire(None, 1).records),
                    1.0,
                )
                self.assertEqual(outcome.source_result, "unavailable")
                self.assertTrue(outcome.attempted)
                self.assertEqual(outcome.expected_count, 1)
                assert adapter.last_acquisition is not None
                self.assertNotIn(
                    "private-secret",
                    json.dumps(adapter.last_acquisition.to_public_dict()),
                )

    def test_timeout_keeps_resolved_prefix_and_poisoned_lane_prevents_overlap(
        self,
    ) -> None:
        entered, release = Event(), Event()

        class Slow(SseFixtureTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                if len(self.calls) == 2:
                    entered.set()
                    release.wait(2)
                return response

        transport = Slow({code: [identity_row(code)] for code in ("120001", "120002")})
        lanes = ProviderLanes()
        policy = ProviderLanePolicy(2, 0.03, cancellation_grace_seconds=0)
        adapter = SseCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=lanes,
            policy=policy,
            max_securities=2,
        )
        listings = tuple(
            ProvenSseListFixture(("120001", "120002")).acquire(None, 1).records
        )
        try:
            outcome = adapter.resolve(
                UniverseRequest("2026-10-15", FROZEN.isoformat()), listings, 1.0
            )
            self.assertTrue(entered.is_set())
            self.assertEqual(outcome.source_result, "partial")
            self.assertEqual((outcome.expected_count, len(outcome.records)), (2, 1))
            assert adapter.last_acquisition is not None
            source = adapter.last_acquisition
            self.assertEqual(source.lane_result.status, "timed_out")
            self.assertEqual(source.lane_result.quiescence.state, "poisoned")
            again = adapter.resolve(
                UniverseRequest("2026-10-15", FROZEN.isoformat()), listings, 1.0
            )
            self.assertFalse(again.attempted)
            self.assertEqual(transport.calls, ["120001", "120002"])
            assert adapter.last_acquisition is not None
            self.assertIn(
                "provider_lane_poisoned", adapter.last_acquisition.limitation_codes
            )
        finally:
            release.set()
            deadline = monotonic() + 1
            while (
                lanes.quiescence("sse").state != "quiescent" and monotonic() < deadline
            ):
                sleep(0.001)
        self.assertEqual(lanes.quiescence("sse").state, "quiescent")
        # A late worker cannot mutate the already returned immutable snapshot.
        self.assertEqual(len(source.records), 1)

    def test_copied_installed_point_parser_uses_110_public_module_and_119_real_leaf(
        self,
    ) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        script = dedent("""
            import json, sys
            from datetime import datetime
            from urllib.parse import parse_qs, urlparse
            sys.path.insert(0, sys.argv[1])
            from a_share_research.a_share_universe import build_as_of_a_share_universe
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes
            from a_share_research.sse_universe_source import SseCurrentIdentityAdapter
            from a_share_research.universe_input import ExchangeListOutcome, ExchangeListRecord, UniverseRequest
            frozen = datetime.fromisoformat("2026-10-15T10:00:00+08:00")
            class Fixture:
                def get(self, url, headers):
                    code = parse_qs(urlparse(url).query)["productid"][0]
                    row = {"SECURITY_CODE_A":code, "SECURITY_ABBR_A":"合成公司", "FULLNAME":"合成主体", "STATE_CODE_A_DESC":"上市", "COMPANY_CODE":code}
                    return HttpResponse(200, "application/json", json.dumps({"result":[row],"pageHelp":{"total":0}}).encode(), datetime.fromisoformat("2026-10-15T10:01:00+08:00"))
            class ProvedList:
                exchange = "SSE"
                operation_id = "fixture_list@1"
                def acquire(self, request, timeout_seconds):
                    row = ExchangeListRecord("SSE", "120001", "合成公司", "A_SHARE", "2026-10-15", "2026-10-15T09:00:00+08:00", "2026-10-15T10:01:00+08:00", "fixture-list:SSE:120001")
                    return ExchangeListOutcome(self.operation_id,"SSE","2026-10-15",True,"complete",1,1,1,(row,))
            lanes = ProviderLanes()
            adapter = SseCurrentIdentityAdapter(research_now=frozen,transport=Fixture(),lanes=lanes,policy=ProviderLanePolicy(1,1),max_securities=1)
            universe = build_as_of_a_share_universe(UniverseRequest("2026-10-15",frozen.isoformat()), research_now=frozen,exchange_list_adapters=(ProvedList(),),identity_adapters=(adapter,))
            assert lanes.is_authoritative(adapter.last_acquisition.lane_result)
            print(json.dumps({"universe":universe.to_public_dict(),"source":adapter.last_acquisition.to_public_dict()}))
        """)
        with tempfile.TemporaryDirectory(prefix="asr-122-p-installed-") as temporary:
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
            public = json.loads(run.stdout)
            self.assertEqual(
                public["universe"]["partitions"]["SSE"]["reconciliation"],
                {"expected": 1, "resolved": 1, "failed": 0},
            )
            self.assertEqual(len(public["source"]["attempts"]), 1)
            self.assertFalse(public["source"]["membership_proven"])
            self.assertFalse(public["universe"]["full_a_share_universe"])
            self.assertNotIn(temporary, run.stdout)
            self.assertNotIn("query.sse", run.stdout)


def mainboard_row(code: str) -> dict[str, str]:
    return {
        "A_STOCK_CODE": code,
        "B_STOCK_CODE": "-",
        "SEC_NAME_CN": "合成股份",
        "SEC_NAME_FULL": "合成股份",
        "LIST_DATE": "20200102",
        "DELIST_DATE": "-",
        "STOCK_TYPE": "1",
        "STATE_CODE": "2",
        "PRODUCT_STATUS": "   D  F  N          ",
    }


def mainboard_page(
    number: int, rows: list[dict[str, str]], *, count: int = 26
) -> bytes:
    value = {
        "result": rows,
        "pageHelp": {
            "beginPage": number,
            "endPage": number,
            "cacheSize": 1,
            "pageNo": number,
            "pageSize": 25,
            "pageCount": (count + 24) // 25,
            "total": count,
            "data": rows,
        },
        "queryDate": "",
    }
    return ("jsonpCallback0(" + json.dumps(value) + ");").encode()


class MainboardFixtureTransport:
    def __init__(self, pages: dict[int, bytes]) -> None:
        self.pages = pages
        self.calls: list[int] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        params = parse_qs(urlparse(url).query, keep_blank_values=True)
        assert params["STOCK_TYPE"] == ["1"]
        assert params["COMPANY_STATUS"] == ["2,4,5,7,8"]
        number = int(params["pageHelp.pageNo"][0])
        self.calls.append(number)
        return HttpResponse(
            200,
            "application/json",
            self.pages[number],
            datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
        )


class SseMainboardCurrentListTests(unittest.TestCase):
    def test_r_real_mainboard_jsonp_pages_keep_counts_and_unproved_membership_distinct(
        self,
    ) -> None:
        class Pages:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                parsed = urlparse(url)
                assert parsed.path == "/sseQuery/commonQuery.do"
                params = parse_qs(parsed.query, keep_blank_values=True)
                assert params["sqlId"] == ["COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L"]
                assert params["STOCK_TYPE"] == ["1"]
                assert params["COMPANY_STATUS"] == ["2,4,5,7,8"]
                assert params["pageHelp.pageSize"] == ["25"]
                assert params["STOCK_CODE"] == [""]
                assert params["CSRC_CODE"] == [""]
                assert params["REG_PROVINCE"] == [""]
                assert headers["Referer"].endswith("/share/")
                number = int(params["pageHelp.pageNo"][0])
                assert params["pageHelp.beginPage"] == [str(number)]
                assert params["pageHelp.endPage"] == [str(number)]
                self.calls.append(number)
                codes = (
                    [str(120000 + i) for i in range(25)] if number == 1 else ["340000"]
                )
                rows = [
                    {
                        "A_STOCK_CODE": code,
                        "B_STOCK_CODE": "-",
                        "SEC_NAME_CN": "合成股份",
                        "SEC_NAME_FULL": "合成股份",
                        "LIST_DATE": "20200102",
                        "DELIST_DATE": "-",
                        "STOCK_TYPE": "1",
                        "STATE_CODE": "2",
                        "PRODUCT_STATUS": "   D  F  N          ",
                    }
                    for code in codes
                ]
                value = {
                    "result": rows,
                    "pageHelp": {
                        "beginPage": number,
                        "endPage": number,
                        "cacheSize": 1,
                        "pageNo": number,
                        "pageSize": 25,
                        "pageCount": 2,
                        "total": 26,
                        "data": rows,
                    },
                    "queryDate": "",
                }
                body = ("jsonpCallback0(" + json.dumps(value) + ");").encode()
                return HttpResponse(
                    200,
                    "application/json",
                    body,
                    datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
                )

        transport = Pages()
        lanes = ProviderLanes()
        acquisition = acquire_sse_mainboard_current_list(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            transport=transport,
            lanes=lanes,
            policy=ProviderLanePolicy(2, 1),
            max_pages=2,
        )
        self.assertEqual(transport.calls, [1, 2])
        self.assertEqual(acquisition.reported_count, 26)
        self.assertEqual(
            (acquisition.pages_expected, acquisition.pages_received), (2, 2)
        )
        self.assertTrue(acquisition.pages_complete)
        self.assertEqual(len(acquisition.records), 26)
        self.assertEqual(acquisition.records[0].displayed_listing_date, "2020-01-02")
        self.assertIsNone(acquisition.records[0].membership_effective_at)
        self.assertIsNone(acquisition.records[0].available_at)
        self.assertEqual(acquisition.source_result, "partial")
        self.assertFalse(acquisition.to_public_dict()["membership_proven"])
        self.assertEqual(
            acquisition.to_public_dict()["source_scope"], "SSE_MAINBOARD_A_SELECTOR"
        )
        self.assertTrue(lanes.is_authoritative(acquisition.lane_result))
        self.assertEqual(len(acquisition.lane_result.attempts), 2)
        public = json.dumps(acquisition.to_public_dict())
        self.assertNotIn("jsonpCallback", public)
        self.assertNotIn("https://", public)

    def test_page_and_attempt_limits_retain_prefix_and_original_reported_count(
        self,
    ) -> None:
        for maximum, budget in ((1, 2), (2, 1)):
            with self.subTest(maximum=maximum, budget=budget):
                transport = MainboardFixtureTransport(
                    {
                        1: mainboard_page(
                            1, [mainboard_row(str(120000 + i)) for i in range(25)]
                        ),
                        2: mainboard_page(2, [mainboard_row("340000")]),
                    }
                )
                source = acquire_sse_mainboard_current_list(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=transport,
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(budget, 1),
                    max_pages=maximum,
                )
                self.assertEqual(source.reported_count, 26)
                self.assertEqual((source.pages_expected, source.pages_received), (2, 1))
                self.assertEqual(len(source.records), 25)
                self.assertFalse(source.pages_complete)
                self.assertEqual(source.source_result, "partial")
                self.assertEqual(transport.calls, [1])
                self.assertEqual(len(source.lane_result.attempts), 1)

    def test_count_drift_and_duplicate_identity_do_not_close_a_smaller_denominator(
        self,
    ) -> None:
        first = mainboard_page(1, [mainboard_row(str(120000 + i)) for i in range(25)])
        for second in (
            mainboard_page(2, [mainboard_row("340000")], count=51),
            mainboard_page(2, [mainboard_row("120000")]),
        ):
            with self.subTest(second=second):
                source = acquire_sse_mainboard_current_list(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=MainboardFixtureTransport({1: first, 2: second}),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(2, 1),
                    max_pages=2,
                )
                self.assertEqual(source.reported_count, 26)
                self.assertEqual(len(source.records), 25)
                self.assertFalse(source.pages_complete)
                self.assertEqual(source.source_result, "partial")
                self.assertEqual(len(source.lane_result.attempts), 2)

    def test_wrong_stock_type_and_unsafe_rows_keep_other_valid_records(self) -> None:
        wrong = mainboard_row("120001")
        wrong["STOCK_TYPE"] = "2"
        unsafe = mainboard_row("120002")
        unsafe["SEC_NAME_CN"] = "synthetic-secret /tmp/private"
        source = acquire_sse_mainboard_current_list(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            transport=MainboardFixtureTransport(
                {
                    1: mainboard_page(
                        1, [mainboard_row("120000"), wrong, unsafe], count=3
                    )
                }
            ),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1),
            max_pages=1,
        )
        self.assertEqual([r.code for r in source.records], ["120000"])
        self.assertEqual(source.reported_count, 3)
        self.assertEqual(source.rejected_rows, 2)
        self.assertFalse(source.pages_complete)
        self.assertNotIn("private", json.dumps(source.to_public_dict()))

    def test_jsonp_tail_bad_pagination_and_unproved_mainboard_empty_fail_closed(
        self,
    ) -> None:
        valid = mainboard_page(1, [mainboard_row("120000")], count=1)
        for body in (
            valid + b"evil();",
            valid.replace(b'"pageSize": 25', b'"pageSize": 50'),
            mainboard_page(1, [], count=0),
            b'jsonpCallback0({"success":"false"});',
        ):
            with self.subTest(body=body):
                source = acquire_sse_mainboard_current_list(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=MainboardFixtureTransport({1: body}),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1),
                    max_pages=1,
                )
                self.assertEqual(source.source_result, "unavailable")
                self.assertIsNone(source.reported_count)
                self.assertFalse(source.pages_complete)
                self.assertEqual(source.records, ())
                self.assertEqual(len(source.lane_result.attempts), 1)

    def test_historical_mainboard_request_is_not_sent_to_current_selector(self) -> None:
        transport = MainboardFixtureTransport({})
        source = acquire_sse_mainboard_current_list(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1),
            max_pages=1,
        )
        self.assertEqual(transport.calls, [])
        self.assertIsNone(source.reported_count)
        self.assertFalse(source.to_public_dict()["attempted"])
        self.assertIn(
            "current_list_cannot_prove_historical_membership", source.limitation_codes
        )

    def test_copied_installed_mainboard_path_calls_verified_bulk_selector_and_real_parser(
        self,
    ) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        script = dedent("""
            import json,sys
            from datetime import datetime
            from urllib.parse import parse_qs,urlparse
            sys.path.insert(0,sys.argv[1])
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.sse_universe_source import acquire_sse_mainboard_current_list
            from a_share_research.universe_input import UniverseRequest
            class Fixture:
                def get(self,url,headers):
                    assert urlparse(url).path=="/sseQuery/commonQuery.do"
                    params=parse_qs(urlparse(url).query)
                    assert params["STOCK_TYPE"]==["1"] and params["pageHelp.pageSize"]==["25"]
                    row={"A_STOCK_CODE":"120000","SEC_NAME_CN":"合成公司","LIST_DATE":"20200102","STOCK_TYPE":"1"}
                    value={"result":[row],"pageHelp":{"beginPage":1,"endPage":1,"pageNo":1,"cacheSize":1,"pageSize":25,"pageCount":1,"total":1,"data":[row]}}
                    return HttpResponse(200,"application/json",("jsonpCallback0("+json.dumps(value)+");").encode(),datetime.fromisoformat("2026-10-15T10:01:00+08:00"))
            frozen=datetime.fromisoformat("2026-10-15T10:00:00+08:00")
            lanes=ProviderLanes()
            source=acquire_sse_mainboard_current_list(UniverseRequest("2026-10-15",frozen.isoformat()),research_now=frozen,transport=Fixture(),lanes=lanes,policy=ProviderLanePolicy(1,1),max_pages=1)
            assert lanes.is_authoritative(source.lane_result)
            print(json.dumps(source.to_public_dict()))
        """)
        with tempfile.TemporaryDirectory(prefix="asr-122-r-installed-") as temporary:
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
            public = json.loads(run.stdout)
            self.assertEqual(public["reported_count"], 1)
            self.assertEqual(len(public["attempts"]), 1)
            self.assertEqual(public["source_scope"], "SSE_MAINBOARD_A_SELECTOR")
            self.assertFalse(public["membership_proven"])
            self.assertNotIn(temporary, run.stdout)
            self.assertNotIn("jsonpCallback", run.stdout)

    def test_r_batch_identity_uses_110_seam_without_point_queries_or_time_backfill(
        self,
    ) -> None:
        row = mainboard_row("120000")
        row["SEC_NAME_CN"] = "合成公司"
        transport = MainboardFixtureTransport({1: mainboard_page(1, [row], count=1)})
        adapter = SseMainboardCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1),
            max_pages=1,
        )
        universe = build_as_of_a_share_universe(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            exchange_list_adapters=(ProvenSseListFixture(("120000",)),),
            identity_adapters=(adapter,),
        )
        sse = next(p for p in universe.partitions if p.exchange == "SSE")
        self.assertEqual((sse.expected, sse.resolved, sse.failed), (1, 1, 0))
        self.assertFalse(sse.complete)
        self.assertFalse(universe.full_a_share_universe)
        self.assertEqual(transport.calls, [1])
        self.assertIsNone(sse.securities[0].effective_from)
        self.assertIsNone(sse.securities[0].identity_available_at)
        self.assertEqual(
            set(sse.securities[0].unranked_reasons),
            {"listing_state_unproven", "as_of_unproven"},
        )
        assert adapter.last_acquisition is not None
        self.assertEqual(adapter.last_acquisition.reported_count, 1)
        self.assertFalse(adapter.last_acquisition.to_public_dict()["membership_proven"])

    def test_s_actual_star_and_termination_selectors_share_parser_without_class_guessing(
        self,
    ) -> None:
        for partition, stock_type, status, path, scope, source_type in (
            (
                "active_star",
                "8",
                "2,4,5,7,8",
                "/sseQuery/commonQuery.do",
                "SSE_ACTIVE_STAR_SELECTOR",
                "8",
            ),
            (
                "terminated_mainboard",
                "1,2",
                "3",
                "/commonQuery.do",
                "SSE_TERMINATED_MAINBOARD_AB_SELECTOR",
                "2",
            ),
            (
                "terminated_star",
                "8",
                "3",
                "/commonQuery.do",
                "SSE_TERMINATED_STAR_SELECTOR",
                "8",
            ),
        ):
            with self.subTest(partition=partition):

                class Source:
                    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                        parsed = urlparse(url)
                        params = parse_qs(parsed.query, keep_blank_values=True)
                        assert parsed.path == path
                        assert params["STOCK_TYPE"] == [stock_type]
                        assert params["COMPANY_STATUS"] == [status]
                        row = mainboard_row("120000")
                        row.update(
                            STOCK_TYPE=source_type,
                            PRODUCT_STATUS="   D  F  N          ",
                        )
                        if status == "3":
                            row["DELIST_DATE"] = "20251014"
                        if source_type == "2":
                            row.update(A_STOCK_CODE="-", B_STOCK_CODE="340000")
                        return HttpResponse(
                            200,
                            "application/json",
                            mainboard_page(1, [row], count=1),
                            datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
                        )

                source = acquire_sse_list_partition(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    partition=partition,
                    transport=Source(),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1),
                    max_pages=1,
                )
                self.assertEqual(source.to_public_dict()["source_scope"], scope)
                self.assertTrue(source.pages_complete)
                self.assertEqual(source.reported_count, 1)
                self.assertEqual(len(source.records), 1)
                record = source.records[0]
                self.assertEqual(
                    record.displayed_delisting_date,
                    "2025-10-14" if status == "3" else None,
                )
                self.assertEqual(
                    record.b_stock_code, "340000" if source_type == "2" else None
                )
                self.assertEqual(
                    record.a_stock_code, None if source_type == "2" else "120000"
                )
                self.assertFalse(source.to_public_dict()["membership_proven"])
                self.assertTrue(record.to_public_dict()["security_class_proven"])
                self.assertFalse(record.to_public_dict()["classification_as_of_proven"])
                self.assertIsNone(record.membership_effective_at)
                self.assertIsNone(record.available_at)

    def test_terminated_historical_carrier_can_be_retrieved_later_without_as_of_adoption(
        self,
    ) -> None:
        class HistoricalCarrier:
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                params = parse_qs(urlparse(url).query)
                assert params["COMPANY_STATUS"] == ["3"] and params["STOCK_TYPE"] == [
                    "8"
                ]
                row = mainboard_row("120000")
                row.update(STOCK_TYPE="8", DELIST_DATE="20251014")
                return HttpResponse(
                    200,
                    "application/json",
                    mainboard_page(1, [row], count=1),
                    datetime.fromisoformat("2026-10-16T10:01:00+08:00"),
                )

        source = acquire_sse_list_partition(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            partition="terminated_star",
            transport=HistoricalCarrier(),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1),
            max_pages=1,
        )
        self.assertEqual(source.reported_count, 1)
        self.assertEqual(source.source_result, "partial")
        self.assertEqual(len(source.lane_result.attempts), 1)
        self.assertEqual(source.records[0].displayed_delisting_date, "2025-10-14")
        self.assertEqual(source.records[0].retrieved_at, "2026-10-16T10:01:00+08:00")
        self.assertIsNone(source.records[0].available_at)
        self.assertIsNone(source.records[0].membership_effective_at)
        self.assertEqual(source.information_cutoff, "2026-10-14T23:59:59+08:00")
        self.assertFalse(source.to_public_dict()["membership_proven"])

    def test_u_complete_c20_combines_scope_and_other_class_constraints_for_positive_classification(
        self,
    ) -> None:
        status = "   D  F  N          "
        self.assertEqual(len(status), 20)
        cases = (
            (status, "A_SHARE"),
            (status[:4] + "Y" + status[5:], "CDR"),
            (status[:3] + "U" + status[4:], "PREFERRED_SHARE"),
            (status[:5] + "L" + status[6:], "BOND"),
            ("-", None),
            (status[:-1], None),
            (status[:4] + "?" + status[5:], None),
            (" " * 20, None),
        )
        for flags, expected in cases:
            with self.subTest(flags=flags):
                row = mainboard_row("120000")
                row["PRODUCT_STATUS"] = flags
                source = acquire_sse_mainboard_current_list(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=MainboardFixtureTransport(
                        {1: mainboard_page(1, [row], count=1)}
                    ),
                    lanes=ProviderLanes(),
                    policy=ProviderLanePolicy(1, 1),
                    max_pages=1,
                )
                self.assertEqual(len(source.records), 1)
                record = source.records[0].to_public_dict()
                self.assertEqual(record["security_category"], expected)
                self.assertEqual(record["security_class_proven"], expected is not None)
                self.assertFalse(record["classification_as_of_proven"])
                self.assertIsNone(record["available_at"])
                self.assertEqual(
                    record["classification_basis"],
                    "official_scope_and_C20_inference" if expected else "unproved",
                )
                self.assertNotIn("PRODUCT_STATUS", record)

    def test_positive_cdr_fact_cannot_be_relabelled_a_share_by_expected_identity(
        self,
    ) -> None:
        row = mainboard_row("120000")
        row.update(SEC_NAME_CN="合成公司", PRODUCT_STATUS="   DY F  N          ")
        adapter = SseMainboardCurrentIdentityAdapter(
            research_now=FROZEN,
            transport=MainboardFixtureTransport({1: mainboard_page(1, [row], count=1)}),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(1, 1),
            max_pages=1,
        )
        universe = build_as_of_a_share_universe(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            exchange_list_adapters=(ProvenSseListFixture(("120000",)),),
            identity_adapters=(adapter,),
        )
        sse = next(p for p in universe.partitions if p.exchange == "SSE")
        self.assertEqual((sse.expected, sse.resolved, sse.failed), (1, 0, 1))
        self.assertIn("source_failed", sse.securities[0].unranked_reasons)
        assert adapter.last_acquisition is not None
        self.assertEqual(adapter.last_acquisition.records[0].security_category, "CDR")
        self.assertFalse(universe.full_a_share_universe)


class SseCurrentCollectionTests(unittest.TestCase):
    def test_current_mainboard_and_star_share_one_real_attempt_budget_and_keep_partial(
        self,
    ) -> None:
        for budget, expected_calls, expected_count in (
            (2, ["1", "8"], 2),
            (1, ["1"], None),
        ):
            with self.subTest(budget=budget):

                class Current:
                    def __init__(self) -> None:
                        self.calls: list[str] = []

                    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                        params = parse_qs(urlparse(url).query)
                        stock_type = params["STOCK_TYPE"][0]
                        self.calls.append(stock_type)
                        assert params["COMPANY_STATUS"] == ["2,4,5,7,8"]
                        code = "120000" if stock_type == "1" else "340000"
                        row = mainboard_row(code)
                        row["STOCK_TYPE"] = stock_type
                        return HttpResponse(
                            200,
                            "application/json",
                            mainboard_page(1, [row], count=1),
                            datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
                        )

                transport = Current()
                lanes = ProviderLanes()
                source = acquire_sse_current_lists(
                    UniverseRequest("2026-10-15", FROZEN.isoformat()),
                    research_now=FROZEN,
                    transport=transport,
                    lanes=lanes,
                    policy=ProviderLanePolicy(budget, 1),
                    max_pages=2,
                )
                self.assertEqual(transport.calls, expected_calls)
                self.assertTrue(lanes.is_authoritative(source.lane_result))
                self.assertEqual(len(source.lane_result.attempts), len(expected_calls))
                self.assertEqual(source.reported_count, expected_count)
                self.assertEqual(len(source.records), len(expected_calls))
                self.assertEqual(source.pages_complete, budget == 2)
                self.assertEqual(source.source_result, "partial")
                self.assertEqual(
                    source.to_public_dict()["source_scope"],
                    "SSE_CURRENT_MAINBOARD_AND_STAR",
                )
                self.assertFalse(source.to_public_dict()["membership_proven"])
                self.assertTrue(all(r.available_at is None for r in source.records))
                if budget == 1:
                    self.assertIn(
                        "actual_attempt_budget_exhausted", source.limitation_codes
                    )
                    self.assertIsNone(source.pages_expected)

    def test_current_collection_page_ceiling_is_shared_across_both_partitions(
        self,
    ) -> None:
        class Current:
            calls: list[str] = []

            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                stock = parse_qs(urlparse(url).query)["STOCK_TYPE"][0]
                self.calls.append(stock)
                row = mainboard_row("120000")
                return HttpResponse(
                    200,
                    "application/json",
                    mainboard_page(1, [row], count=1),
                    datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
                )

        transport = Current()
        source = acquire_sse_current_lists(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(4, 1),
            max_pages=1,
        )
        self.assertEqual(transport.calls, ["1"])
        self.assertEqual(len(source.lane_result.attempts), 1)
        self.assertEqual(len(source.records), 1)
        self.assertIsNone(source.reported_count)
        self.assertIn("page_budget_exhausted", source.limitation_codes)

    def test_current_collection_retry_spends_shared_budget_before_star(self) -> None:
        class Limited:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                stock = parse_qs(urlparse(url).query)["STOCK_TYPE"][0]
                self.calls.append(stock)
                body = mainboard_page(1, [mainboard_row("120000")], count=1)
                return HttpResponse(
                    429 if len(self.calls) == 1 else 200,
                    "application/json",
                    body,
                    datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
                )

        transport = Limited()
        source = acquire_sse_current_lists(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            transport=transport,
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(2, 1, rate_limit_backoffs=(0.0,)),
            max_pages=2,
        )
        self.assertEqual(transport.calls, ["1", "1"])
        self.assertEqual(
            [a.state for a in source.lane_result.attempts], ["failed", "succeeded"]
        )
        self.assertEqual(len(source.records), 1)
        self.assertIsNone(source.reported_count)
        self.assertIn("actual_attempt_budget_exhausted", source.limitation_codes)

    def test_bad_mainboard_partition_does_not_erase_star_observation(self) -> None:
        class OneBad:
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                stock = parse_qs(urlparse(url).query)["STOCK_TYPE"][0]
                row = mainboard_row("340000")
                row["STOCK_TYPE"] = "8"
                body = b"{}" if stock == "1" else mainboard_page(1, [row], count=1)
                return HttpResponse(
                    200,
                    "application/json",
                    body,
                    datetime.fromisoformat("2026-10-15T10:01:00+08:00"),
                )

        source = acquire_sse_current_lists(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            transport=OneBad(),
            lanes=ProviderLanes(),
            policy=ProviderLanePolicy(2, 1),
            max_pages=2,
        )
        self.assertEqual([r.code for r in source.records], ["340000"])
        self.assertEqual(len(source.lane_result.attempts), 2)
        self.assertIsNone(source.reported_count)
        self.assertFalse(source.pages_complete)
        self.assertEqual(source.source_result, "partial")


if __name__ == "__main__":
    unittest.main()
