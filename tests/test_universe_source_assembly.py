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
from urllib.parse import parse_qs, urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse, UrlLibTransport  # noqa: E402
from a_share_research.provider_lane import ProviderLanePolicy, ProviderLanes  # noqa: E402
from a_share_research.universe_input import UniverseRequest  # noqa: E402
from a_share_research.universe_sources import (  # noqa: E402
    CurrentSourcePolicies,
    CurrentSourceTransports,
    acquire_default_current_sources,
)

FROZEN = datetime.fromisoformat("2026-10-15T10:00:00+08:00")
RETRIEVED = datetime.fromisoformat("2026-10-15T10:01:00+08:00")


class SseRawFixture:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        params = parse_qs(urlparse(url).query)
        assert urlparse(url).path == "/sseQuery/commonQuery.do"
        stock = params["STOCK_TYPE"][0]
        assert stock in {"1", "8"} and params["COMPANY_STATUS"] == ["2,4,5,7,8"]
        self.calls.append(stock)
        row = {
            "A_STOCK_CODE": "120000" if stock == "1" else "340000",
            "SEC_NAME_CN": "合成沪股",
            "LIST_DATE": "20200102",
            "STOCK_TYPE": stock,
            "PRODUCT_STATUS": "   D  F  N          ",
        }
        payload = {
            "result": [row],
            "pageHelp": {
                "beginPage": 1,
                "endPage": 1,
                "pageNo": 1,
                "cacheSize": 1,
                "pageSize": 25,
                "pageCount": 1,
                "total": 1,
                "data": [row],
            },
        }
        return HttpResponse(
            200,
            "application/json",
            ("jsonpCallback0(" + json.dumps(payload) + ");").encode(),
            RETRIEVED,
        )


class SzseRawFixture:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        params = parse_qs(urlparse(url).query)
        assert params["CATALOGID"] == ["1110"] and params["TABKEY"] == ["tab1"]
        self.calls += 1
        report = {
            "error": None,
            "metadata": {
                "catalogid": "1110",
                "tabkey": "tab1",
                "pageno": 1,
                "pagesize": 20,
                "pagecount": 1,
                "recordcount": 1,
                "name": "A股列表",
                "cols": {
                    "agdm": "A股代码",
                    "agjc": "A股简称",
                    "agssrq": "A股<br>上市日期",
                },
                "subname": "2026-10-15 ",
            },
            "data": [
                {
                    "agdm": "560000",
                    "agjc": "合成深股",
                    "agssrq": "2020-01-02",
                    "bk": "合成板块",
                }
            ],
        }
        return HttpResponse(
            200, "application/json", json.dumps([report]).encode(), RETRIEVED
        )


class BseRawFixture:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        params = parse_qs(body.decode(), keep_blank_values=True)
        assert params["xxfcbj[]"] == ["2"] and params["typejb"] == ["T"]
        self.calls += 1
        row = {
            "xxzqdm": "780000",
            "xxzqjc": "合成北股",
            "fxssrq": "20200102",
            "xxgprq": "20200102",
            "xxfcbj": "2",
            "xxzqjb": "T",
            "xxjsrq": "20300101",
            "xxgxsj": "10101",
        }
        page = {
            "content": [row],
            "number": 0,
            "numberOfElements": 1,
            "size": 20,
            "sort": None,
            "totalPages": 1,
            "totalElements": 1,
            "firstPage": True,
            "lastPage": True,
        }
        payload = (
            b"invalid-provider-body"
            if self.fail
            else ("fixture_callback(" + json.dumps([page]) + ");").encode()
        )
        return HttpResponse(200, "application/javascript", payload, RETRIEVED)


class CurrentSourceAssemblyTests(unittest.TestCase):
    def test_default_assembly_runs_three_real_parsers_without_prefilled_universe(
        self,
    ) -> None:
        sse, szse, bse = SseRawFixture(), SzseRawFixture(), BseRawFixture()
        lanes = ProviderLanes()
        outcomes = acquire_default_current_sources(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            lanes=lanes,
            policies=CurrentSourcePolicies(
                ProviderLanePolicy(2, 1),
                ProviderLanePolicy(1, 1),
                ProviderLanePolicy(1, 1),
            ),
            transports=CurrentSourceTransports(sse, szse, bse),
        )
        self.assertEqual(
            [o.to_public_dict()["exchange"] for o in outcomes], ["SSE", "SZSE", "BSE"]
        )
        self.assertEqual([len(o.records) for o in outcomes], [2, 1, 1])
        self.assertEqual([o.reported_count for o in outcomes], [2, 1, 1])
        self.assertEqual([len(o.lane_result.attempts) for o in outcomes], [2, 1, 1])
        self.assertTrue(all(lanes.is_authoritative(o.lane_result) for o in outcomes))
        self.assertEqual((sse.calls, szse.calls, bse.calls), (["1", "8"], 1, 1))
        self.assertTrue(all(o.source_result == "partial" for o in outcomes))
        self.assertTrue(
            all(not o.to_public_dict()["membership_proven"] for o in outcomes)
        )
        self.assertTrue(
            all(o.information_cutoff == FROZEN.isoformat() for o in outcomes)
        )
        self.assertTrue(
            all(row.available_at is None for o in outcomes for row in o.records)
        )

    def test_failed_bse_keeps_other_source_records_and_unknown_count_is_not_zero(
        self,
    ) -> None:
        outcomes = acquire_default_current_sources(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            lanes=ProviderLanes(),
            policies=CurrentSourcePolicies(
                ProviderLanePolicy(2, 1),
                ProviderLanePolicy(1, 1),
                ProviderLanePolicy(1, 1),
            ),
            transports=CurrentSourceTransports(
                SseRawFixture(), SzseRawFixture(), BseRawFixture(True)
            ),
        )
        self.assertEqual([len(o.records) for o in outcomes], [2, 1, 0])
        self.assertEqual(outcomes[2].source_result, "unavailable")
        self.assertIsNone(outcomes[2].reported_count)
        self.assertEqual(len(outcomes[2].lane_result.attempts), 1)
        self.assertEqual(
            [o.source_result for o in outcomes[:2]], ["partial", "partial"]
        )

    def test_historical_request_does_not_turn_current_source_collection_into_historical_zero(
        self,
    ) -> None:
        sse, szse, bse = SseRawFixture(), SzseRawFixture(), BseRawFixture()
        outcomes = acquire_default_current_sources(
            UniverseRequest("2026-10-14", "2026-10-14T23:59:59+08:00"),
            research_now=FROZEN,
            lanes=ProviderLanes(),
            policies=CurrentSourcePolicies(
                ProviderLanePolicy(2, 1),
                ProviderLanePolicy(1, 1),
                ProviderLanePolicy(1, 1),
            ),
            transports=CurrentSourceTransports(sse, szse, bse),
        )
        self.assertEqual((sse.calls, szse.calls, bse.calls), ([], 0, 0))
        self.assertTrue(all(o.source_result == "unavailable" for o in outcomes))
        self.assertTrue(all(o.reported_count is None for o in outcomes))
        self.assertTrue(all(not o.lane_result.attempts for o in outcomes))
        self.assertTrue(
            all(o.membership_effective_date == "2026-10-14" for o in outcomes)
        )

    def test_sse_timeout_preserves_other_providers_and_returned_snapshot(self) -> None:
        entered, release = Event(), Event()

        class Slow(SseRawFixture):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                entered.set()
                release.wait(2)
                return response

        lanes = ProviderLanes()
        try:
            outcomes = acquire_default_current_sources(
                UniverseRequest("2026-10-15", FROZEN.isoformat()),
                research_now=FROZEN,
                lanes=lanes,
                policies=CurrentSourcePolicies(
                    ProviderLanePolicy(2, 0.05, cancellation_grace_seconds=0),
                    ProviderLanePolicy(1, 1),
                    ProviderLanePolicy(1, 1),
                ),
                transports=CurrentSourceTransports(
                    Slow(), SzseRawFixture(), BseRawFixture()
                ),
            )
            self.assertTrue(entered.is_set())
            self.assertEqual(outcomes[0].lane_result.status, "timed_out")
            self.assertEqual(outcomes[0].lane_result.quiescence.state, "poisoned")
            self.assertEqual([len(o.records) for o in outcomes], [0, 1, 1])
            self.assertEqual([len(o.lane_result.attempts) for o in outcomes], [1, 1, 1])
        finally:
            release.set()
            deadline = monotonic() + 1
            while (
                lanes.quiescence("sse").state != "quiescent" and monotonic() < deadline
            ):
                sleep(0.001)
        self.assertEqual(lanes.quiescence("sse").state, "quiescent")
        self.assertEqual(outcomes[0].records, ())

    def test_copied_installed_default_current_universe_runs_real_parsers_and_110(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        script = dedent("""
            import json,sys
            from datetime import datetime
            from urllib.parse import parse_qs,urlparse
            sys.path.insert(0,sys.argv[1])
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            from a_share_research.universe_sources import CurrentSourcePolicies,CurrentSourceTransports,acquire_default_current_universe,create_default_current_universe_adapters
            frozen=datetime.fromisoformat('2026-10-15T10:00:00+08:00')
            retrieved=datetime.fromisoformat('2026-10-15T10:01:00+08:00')
            class SSE:
                def get(self,url,headers):
                    stock=parse_qs(urlparse(url).query)['STOCK_TYPE'][0]
                    row={'A_STOCK_CODE':'120000' if stock=='1' else '340000','SEC_NAME_CN':'合成沪股','LIST_DATE':'20200102','STOCK_TYPE':stock,'PRODUCT_STATUS':'   D  F  N          '}
                    value={'result':[row],'pageHelp':{'beginPage':1,'endPage':1,'pageNo':1,'cacheSize':1,'pageSize':25,'pageCount':1,'total':1,'data':[row]}}
                    return HttpResponse(200,'application/json',('jsonpCallback0('+json.dumps(value)+');').encode(),retrieved)
            class SZSE:
                def get(self,url,headers):
                    value={'error':None,'metadata':{'catalogid':'1110','tabkey':'tab1','pageno':1,'pagesize':20,'pagecount':1,'recordcount':1,'name':'A股列表','cols':{'agdm':'A股代码','agjc':'A股简称','agssrq':'A股<br>上市日期'},'subname':'2026-10-15 '},'data':[{'agdm':'560000','agjc':'合成深股','agssrq':'2020-01-02','bk':'合成板块'}]}
                    return HttpResponse(200,'application/json',json.dumps([value]).encode(),retrieved)
            class BSE:
                def post(self,url,headers,body):
                    assert parse_qs(body.decode())['xxfcbj[]']==['2']
                    row={'xxzqdm':'780000','xxzqjc':'合成北股','fxssrq':'20200102','xxgprq':'20200102','xxfcbj':'2','xxzqjb':'T','xxjsrq':'20300101','xxgxsj':'10101'}
                    value={'content':[row],'number':0,'numberOfElements':1,'size':20,'sort':None,'totalPages':1,'totalElements':1,'firstPage':True,'lastPage':True}
                    return HttpResponse(200,'application/javascript',('fixture_callback('+json.dumps([value])+');').encode(),retrieved)
            lanes=ProviderLanes()
            owners=create_default_current_universe_adapters(research_now=frozen,lanes=lanes,policies=CurrentSourcePolicies(ProviderLanePolicy(2,1),ProviderLanePolicy(1,1),ProviderLanePolicy(1,1)),transports=CurrentSourceTransports(SSE(),SZSE(),BSE()))
            assert [owner.exchange for owner in owners.identity_adapters]==['SSE','SZSE','BSE']
            empty=[owner.resolve(UniverseRequest('2026-10-15',frozen.isoformat()),(),1) for owner in owners.identity_adapters]
            assert all(not outcome.attempted and outcome.source_result=='unavailable' for outcome in empty)
            acquisition=acquire_default_current_universe(UniverseRequest('2026-10-15',frozen.isoformat()),research_now=frozen,lanes=lanes,policies=CurrentSourcePolicies(ProviderLanePolicy(2,1),ProviderLanePolicy(1,1),ProviderLanePolicy(1,1)),transports=CurrentSourceTransports(SSE(),SZSE(),BSE()))
            outcomes=acquisition.source_acquisitions
            assert not acquisition.universe.full_a_share_universe
            assert all(p.expected is None and p.failed is None for p in acquisition.universe.partitions)
            assert all(p.source_result=='partial' for p in acquisition.universe.partitions)
            assert all(lanes.is_authoritative(o.lane_result) for o in outcomes)
            assert all(not o.to_public_dict()['membership_proven'] for o in outcomes)
            print(json.dumps({'records':[len(o.records) for o in outcomes],'attempts':[len(o.lane_result.attempts) for o in outcomes],'states':[o.source_result for o in outcomes]}))
        """)
        with tempfile.TemporaryDirectory(
            prefix="asr-122-current-installed-"
        ) as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            result = subprocess.run(
                [sys.executable, "-c", script, str(installed / "scripts")],
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
                {
                    "records": [2, 1, 1],
                    "attempts": [2, 1, 1],
                    "states": ["partial", "partial", "partial"],
                },
            )
            self.assertNotIn(temporary, result.stdout)

    def test_all_source_configuration_is_validated_before_any_acquisition(self) -> None:
        class Unowned(UrlLibTransport):
            def __init__(self) -> None:
                pass

            def post(
                self, url: str, headers: dict[str, str], body: bytes
            ) -> HttpResponse:
                raise AssertionError("Unowned transport must never run.")

        sse, szse = SseRawFixture(), SzseRawFixture()
        with self.assertRaises(TypeError):
            acquire_default_current_sources(
                UniverseRequest("2026-10-15", FROZEN.isoformat()),
                research_now=FROZEN,
                lanes=ProviderLanes(),
                policies=CurrentSourcePolicies(
                    ProviderLanePolicy(2, 1),
                    ProviderLanePolicy(1, 1),
                    ProviderLanePolicy(1, 1),
                ),
                transports=CurrentSourceTransports(sse, szse, Unowned()),
            )
        self.assertEqual((sse.calls, szse.calls), ([], 0))


class DefaultCurrentUniverseProjectionTests(unittest.TestCase):
    def test_default_identity_owners_execute_real_parsers_with_bounded_expected_input(
        self,
    ) -> None:
        from tests.test_sse_universe_source import SseFixtureTransport, identity_row
        from a_share_research.universe_input import ExchangeListRecord
        from a_share_research.universe_sources import (
            create_default_current_universe_adapters,
        )

        # Identity seam only: these synthetic expected identities do not supply
        # a complete Universe or qualify the current production list's dates.
        sse = SseFixtureTransport({"120000": [identity_row("120000", "合成沪股")]})
        szse, bse = SzseRawFixture(), BseRawFixture()
        lanes = ProviderLanes()
        owners = create_default_current_universe_adapters(
            research_now=FROZEN,
            lanes=lanes,
            policies=CurrentSourcePolicies(
                ProviderLanePolicy(2, 1),
                ProviderLanePolicy(1, 1),
                ProviderLanePolicy(1, 1),
            ),
            transports=CurrentSourceTransports(sse, szse, bse),
        )
        outcomes = []
        for owner, code, name in zip(
            owners.identity_adapters,
            ("120000", "560000", "780000"),
            ("合成沪股", "合成深股", "合成北股"),
            strict=True,
        ):
            listing = ExchangeListRecord(
                owner.exchange,
                code,
                name,
                "A_SHARE",
                "2026-10-15",
                "2026-10-15T09:00:00+08:00",
                RETRIEVED.isoformat(),
                f"fixture-list:{owner.exchange}:{code}",
            )
            outcome = owner.resolve(
                UniverseRequest("2026-10-15", FROZEN.isoformat()),
                (listing,),
                1,
            )
            outcomes.append(outcome)
            source = owner.last_acquisition
            self.assertTrue(lanes.is_authoritative(source.lane_result))
            self.assertEqual(len(source.lane_result.attempts), 1)
        self.assertEqual([outcome.expected_count for outcome in outcomes], [1, 1, 1])
        self.assertEqual([len(outcome.records) for outcome in outcomes], [1, 1, 1])
        self.assertTrue(all(outcome.attempted for outcome in outcomes))
        self.assertTrue(
            all(
                row.available_at is None
                for outcome in outcomes
                for row in outcome.records
            )
        )
        self.assertEqual((sse.calls, szse.calls, bse.calls), (["120000"], 1, 1))

    def test_default_owners_are_driven_by_public_universe_module(self) -> None:
        from a_share_research.a_share_universe import build_as_of_a_share_universe
        from a_share_research.universe_sources import (
            create_default_current_universe_adapters,
        )

        sse, szse, bse = SseRawFixture(), SzseRawFixture(), BseRawFixture()
        lanes = ProviderLanes()
        owners = create_default_current_universe_adapters(
            research_now=FROZEN,
            lanes=lanes,
            policies=CurrentSourcePolicies(
                ProviderLanePolicy(2, 1),
                ProviderLanePolicy(1, 1),
                ProviderLanePolicy(1, 1),
            ),
            transports=CurrentSourceTransports(sse, szse, bse),
        )
        self.assertEqual((sse.calls, szse.calls, bse.calls), ([], 0, 0))
        self.assertEqual(
            [owner.exchange for owner in owners.identity_adapters],
            ["SSE", "SZSE", "BSE"],
        )
        universe = build_as_of_a_share_universe(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            exchange_list_adapters=owners.exchange_list_adapters,
            identity_adapters=owners.identity_adapters,
        )
        self.assertEqual((sse.calls, szse.calls, bse.calls), (["1", "8"], 1, 1))
        self.assertFalse(universe.full_a_share_universe)
        self.assertTrue(all(p.expected is None for p in universe.partitions))
        self.assertTrue(all(p.identity_outcome is None for p in universe.partitions))
        sources = tuple(
            owner.last_acquisition for owner in owners.exchange_list_adapters
        )
        self.assertEqual([len(source.records) for source in sources], [2, 1, 1])
        self.assertTrue(
            all(lanes.is_authoritative(source.lane_result) for source in sources)
        )

    def test_real_source_counts_do_not_fill_unproved_ordinary_a_denominators(
        self,
    ) -> None:
        from a_share_research.universe_sources import acquire_default_current_universe

        lanes = ProviderLanes()
        result = acquire_default_current_universe(
            UniverseRequest("2026-10-15", FROZEN.isoformat()),
            research_now=FROZEN,
            lanes=lanes,
            policies=CurrentSourcePolicies(
                ProviderLanePolicy(2, 1),
                ProviderLanePolicy(1, 1),
                ProviderLanePolicy(1, 1),
            ),
            transports=CurrentSourceTransports(
                SseRawFixture(), SzseRawFixture(), BseRawFixture()
            ),
        )
        self.assertFalse(result.universe.full_a_share_universe)
        self.assertEqual(
            [p.expected for p in result.universe.partitions], [None, None, None]
        )
        self.assertEqual(
            [p.failed for p in result.universe.partitions], [None, None, None]
        )
        self.assertEqual(
            [p.list_outcome.pages_received for p in result.universe.partitions],
            [2, 1, 1],
        )
        self.assertTrue(
            all(p.source_result == "partial" for p in result.universe.partitions)
        )
        self.assertEqual(
            [len(s.records) for s in result.source_acquisitions], [2, 1, 1]
        )
        public = result.to_public_dict()
        self.assertEqual(
            public["universe"]["partitions"]["BSE"]["reconciliation"]["expected"], None
        )
        self.assertEqual(public["source_acquisitions"][2]["reported_count"], 1)


if __name__ == "__main__":
    unittest.main()
