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


if __name__ == "__main__":
    unittest.main()
