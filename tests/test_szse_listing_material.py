from __future__ import annotations

import importlib.machinery
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent


class SzseListingMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Resolve only the actual locked decoder package, without adding the
        # full user runtime or importing unrelated SDKs into the test process.
        source = Path(__file__).resolve().parents[1] / "skill/a-share-research"
        spec = importlib.machinery.PathFinder.find_spec(
            "pypdf", [str(source / ".runtime"), *sys.path]
        )
        if spec is None or spec.origin is None:
            raise RuntimeError(
                "Provide locked pypdf 6.10.2 in the Skill or test-tool runtime."
            )
        package = Path(spec.origin).resolve().parent
        metadata = next(
            (
                d
                for d in importlib.metadata.distributions(path=[str(package.parent)])
                if d.metadata.get("Name", "").lower() == "pypdf"
            ),
            None,
        )
        if metadata is None or metadata.version != "6.10.2":
            raise RuntimeError("The listing tests require locked pypdf 6.10.2.")
        cls.temporary = tempfile.TemporaryDirectory(prefix="asr-122-pdf-installed-")
        cls.root = Path(cls.temporary.name)
        cls.installed = cls.root / "a-share-research"
        shutil.copytree(
            source,
            cls.installed,
            ignore=shutil.ignore_patterns(".runtime", ".state", "__pycache__", "*.pyc"),
        )
        runtime = cls.installed / ".runtime"
        runtime.mkdir()
        shutil.copytree(
            package,
            runtime / "pypdf",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copytree(
            metadata.locate_file("pypdf-6.10.2.dist-info"),
            runtime / "pypdf-6.10.2.dist-info",
        )
        cls.fixture = (
            Path(__file__).resolve().parent
            / "fixtures/szse_listing_material_synthetic.pdf"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def run_case(
        self,
        *,
        oversized: bool = False,
        fixture: str = "szse_listing_material_synthetic.pdf",
    ) -> dict[str, object]:
        script = dedent("""
            import json,sys
            from pathlib import Path
            from datetime import datetime
            sys.path.insert(0,sys.argv[1])
            from a_share_research.identity_sources import HttpResponse,TransportError
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            from a_share_research.szse_listing_material import acquire_szse_listing_material
            frozen=datetime.fromisoformat('2026-10-15T10:00:00+08:00')
            retrieved=datetime.fromisoformat('2026-10-15T10:01:00+08:00')
            class Transport:
                def __init__(self): self.calls=0
                def get(self,url,headers):
                    assert url.startswith('https://disc.static.szse.cn/disc/disk02/finalpage/')
                    self.calls+=1
                    data=Path(sys.argv[2]).read_bytes()
                    if sys.argv[3]=='oversized': data+=b'X'*(4*1024*1024)
                    return HttpResponse(200,'application/pdf',data,retrieved)
            transport=Transport();lanes=ProviderLanes()
            result=acquire_szse_listing_material(UniverseRequest('2021-10-25','2021-10-25T23:59:59+08:00'),research_now=frozen,document_url='https://disc.static.szse.cn/disc/disk02/finalpage/2021-10-22/11111111-2222-3333-4444-555555555555.PDF',transport=transport,lanes=lanes,policy=ProviderLanePolicy(1,3))
            assert lanes.is_authoritative(result.lane_result)
            assert len(result.lane_result.attempts)==transport.calls==1
            print(json.dumps(result.to_public_dict(),ensure_ascii=False))
        """)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                script,
                str(self.installed / "scripts"),
                str(self.fixture.with_name(fixture)),
                "oversized" if oversized else "normal",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            close_fds=True,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "en_US.UTF-8",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(self.root),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_pdf_decoder_reads_formal_approval_but_url_date_cannot_fill_publication(
        self,
    ) -> None:
        result = self.run_case()
        self.assertEqual(result["source_result"], "partial")
        self.assertFalse(result["membership_proven"])
        self.assertIsNone(result["market_expected_count"])
        self.assertEqual(result["records"][0]["exchange"], "SZSE")
        self.assertEqual(result["records"][0]["code"], "120000")
        self.assertEqual(result["records"][0]["name"], "合成深股")
        self.assertEqual(result["records"][0]["security_category"], "A_SHARE")
        self.assertEqual(
            result["records"][0]["formal_listing_trading_day"], "2021-10-25"
        )
        self.assertEqual(
            result["records"][0]["approval_reference"], "深证上[2021]123号"
        )
        self.assertIsNone(result["records"][0]["available_at"])
        self.assertIsNone(result["records"][0]["publication_day"])
        self.assertIn("publication_time_unproved", result["limitation_codes"])
        self.assertNotIn("finalpage", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))

    def run_event_case(
        self, mode: str = "normal", screen_date: str = "2021-10-25"
    ) -> dict[str, object]:
        script = dedent("""
            import json,sys
            from pathlib import Path
            from datetime import datetime
            sys.path.insert(0,sys.argv[1])
            from a_share_research.a_share_universe import build_as_of_a_share_universe
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            from a_share_research.szse_listing_material import SzseListingEventAdapter
            frozen=datetime.fromisoformat('2026-10-15T10:00:00+08:00')
            retrieved=datetime.fromisoformat('2026-10-15T10:01:00+08:00')
            path='/disc/disk02/finalpage/2021-10-22/11111111-2222-3333-4444-555555555555.PDF'
            class Transport:
                def __init__(self): self.calls=[]
                def post(self,url,headers,body):
                    assert url=='https://www.szse.cn/api/disc/announcement/annList'
                    assert json.loads(body)=={'channelCode':['listedNotice_disc'],'pageSize':50,'pageNum':1,'stock':['120000']}
                    self.calls.append('POST')
                    if sys.argv[3]=='unsafe_error':
                        from a_share_research.identity_sources import TransportError
                        raise TransportError('private-provider-secret','/Users/private-provider-secret')
                    if sys.argv[3]=='rate_limited' and len(self.calls)==1:
                        return HttpResponse(429,'application/json',b'{}',retrieved)
                    row={'annId':12345,'id':'22222222-3333-4444-5555-666666666666','secCode':['120000'],'secName':['合成深股'],'title':'合成深股：首次公开发行股票并在创业板上市之上市公告书（更新后）','publishTime':'2021-10-22 00:00:00','attachPath':path}
                    if sys.argv[3]=='wrong_path': row['attachPath']=path.replace('2021-10-22','2021-10-21')
                    if sys.argv[3]=='wrong_name': row['secName']=['另一合成股']
                    if sys.argv[3]=='missing_publication': row['publishTime']=None
                    if sys.argv[3]=='late_publication': row['publishTime']='2021-10-25 00:00:00'
                    return HttpResponse(200,'application/json',json.dumps({'announceCount':1,'data':[row]}).encode(),retrieved)
                def get(self,url,headers):
                    assert url=='https://disc.static.szse.cn'+path
                    self.calls.append('GET')
                    return HttpResponse(200,'application/pdf',Path(sys.argv[2]).read_bytes(),retrieved)
            transport=Transport();lanes=ProviderLanes()
            budget=3 if sys.argv[3]=='rate_limited' else 1 if sys.argv[3]=='budget_exhausted' else 2
            adapter=SzseListingEventAdapter(research_now=frozen,source_code='120000',document_url='https://disc.static.szse.cn'+path,transport=transport,lanes=lanes,policy=ProviderLanePolicy(budget,3,rate_limit_backoffs=(0.0,)),max_metadata_pages=1)
            request=UniverseRequest(sys.argv[4],sys.argv[4]+'T23:59:59+08:00')
            if sys.argv[3]=='assembled':
                from a_share_research.universe_sources import acquire_default_universe,CurrentSourcePolicies,CurrentSourceTransports
                from a_share_research.szse_listing_material import SzseListingEventTarget
                class Other:
                    def get(self,*args): raise AssertionError('No current-to-history source fallback')
                    def post(self,*args): raise AssertionError('No current-to-history source fallback')
                acquired=acquire_default_universe(request,research_now=frozen,lanes=lanes,policies=CurrentSourcePolicies(ProviderLanePolicy(2,3),ProviderLanePolicy(2,3),ProviderLanePolicy(2,3)),transports=CurrentSourceTransports(Other(),Other(),Other()),szse_listing_event=SzseListingEventTarget('120000','https://disc.static.szse.cn'+path,1),listing_transport=transport)
                result=acquired.universe
                source=acquired.source_acquisitions[1]
                assert [bool(s.lane_result.attempts) for s in acquired.source_acquisitions]==[False,True,False]
            else:
                result=build_as_of_a_share_universe(request,research_now=frozen,exchange_list_adapters=(adapter,),identity_adapters=())
                source=adapter.last_acquisition
            assert source is not None and lanes.is_authoritative(source.lane_result)
            assert len(source.lane_result.attempts)==len(transport.calls)
            print(json.dumps({'universe':result.to_public_dict(),'source':source.to_public_dict(),'calls':transport.calls},ensure_ascii=False))
        """)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                script,
                str(self.installed / "scripts"),
                str(self.fixture),
                mode,
                screen_date,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            close_fds=True,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "en_US.UTF-8",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(self.root),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_matched_updated_version_produces_one_member_with_unknown_market_total(
        self,
    ) -> None:
        result = self.run_event_case()
        self.assertEqual(result["calls"], ["POST", "GET"])
        self.assertTrue(result["source"]["membership_proven"])
        szse = result["universe"]["partitions"]["SZSE"]
        self.assertEqual(
            szse["reconciliation"], {"expected": None, "resolved": 0, "failed": None}
        )
        self.assertFalse(result["universe"]["full_a_share_universe"])
        self.assertEqual(len(szse["securities"]), 1)
        self.assertEqual(szse["securities"][0]["canonical_security_id"], "SZSE:120000")
        event = result["source"]["records"][0]
        self.assertEqual(event["publication_day"], "2021-10-22")
        self.assertEqual(event["available_at"], "2021-10-23T00:00:00+08:00")
        self.assertEqual(
            event["availability_basis"], "consumer_conservative_next_day_Asia_Shanghai"
        )
        self.assertIn("更新后", event["publication_version_title"])
        self.assertNotIn("https://", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_metadata_retry_and_pdf_get_share_actual_invocation_budget(self) -> None:
        result = self.run_event_case("rate_limited")
        self.assertEqual(result["calls"], ["POST", "POST", "GET"])
        self.assertEqual(len(result["source"]["attempts"]), 3)
        self.assertEqual(len(result["universe"]["partitions"]["SZSE"]["securities"]), 1)

    def test_publication_is_not_reused_for_another_version_or_identity(self) -> None:
        for mode in ("wrong_path", "wrong_name", "missing_publication"):
            with self.subTest(mode=mode):
                result = self.run_event_case(mode)
                self.assertEqual(
                    result["universe"]["partitions"]["SZSE"]["securities"], []
                )
                self.assertFalse(result["universe"]["full_a_share_universe"])
                self.assertIsNone(result["source"]["market_expected_count"])

    def test_formal_event_day_does_not_imply_before_or_later_membership(self) -> None:
        for day in ("2021-10-24", "2021-10-26"):
            with self.subTest(day=day):
                result = self.run_event_case(screen_date=day)
                self.assertEqual(
                    result["universe"]["partitions"]["SZSE"]["securities"], []
                )
                self.assertIn(
                    "outside_proven_listing_event_day",
                    result["source"]["limitation_codes"],
                )
                self.assertEqual(len(result["source"]["records"]), 1)

    def test_consumer_day_bound_does_not_adopt_a_version_after_cutoff(self) -> None:
        result = self.run_event_case("late_publication")
        self.assertEqual(result["universe"]["partitions"]["SZSE"]["securities"], [])
        self.assertIn(
            "listing_version_after_cutoff", result["source"]["limitation_codes"]
        )
        self.assertEqual(
            result["source"]["records"][0]["available_at"], "2021-10-26T00:00:00+08:00"
        )

    def test_exhausted_metadata_budget_cannot_hide_a_pdf_get_or_claim_empty(
        self,
    ) -> None:
        result = self.run_event_case("budget_exhausted")
        self.assertEqual(result["calls"], ["POST"])
        self.assertEqual(len(result["source"]["attempts"]), 1)
        szse = result["universe"]["partitions"]["SZSE"]
        self.assertEqual(szse["securities"], [])
        self.assertIsNone(szse["reconciliation"]["expected"])
        self.assertEqual(szse["source_result"], "partial")

    def test_default_assembly_consumes_source_event_without_prefilled_universe(
        self,
    ) -> None:
        result = self.run_event_case("assembled")
        parts = result["universe"]["partitions"]
        self.assertEqual(len(parts["SZSE"]["securities"]), 1)
        self.assertEqual(result["calls"], ["POST", "GET"])
        self.assertTrue(
            all(part["reconciliation"]["expected"] is None for part in parts.values())
        )
        self.assertEqual(parts["SSE"]["source_result"], "unavailable")
        self.assertEqual(parts["BSE"]["source_result"], "unavailable")
        self.assertFalse(result["universe"]["full_a_share_universe"])

    def test_source_error_code_and_message_do_not_escape_public_output(self) -> None:
        result = self.run_event_case("unsafe_error")
        self.assertNotIn("private-provider-secret", json.dumps(result))
        self.assertEqual(result["universe"]["partitions"]["SZSE"]["securities"], [])

    def test_non_ordinary_class_and_unapproved_proposal_cannot_create_positive_event(
        self,
    ) -> None:
        for kind in ("wrong_class", "proposal_only"):
            with self.subTest(kind=kind):
                result = self.run_case(fixture=f"szse_listing_{kind}_synthetic.pdf")
                self.assertEqual(result["source_result"], "unavailable")
                self.assertEqual(result["records"], [])
                self.assertIn("listing_approval_unproved", result["limitation_codes"])
                self.assertIsNone(result["market_expected_count"])

    def test_conflicting_cover_identity_is_not_a_new_canonical_security(self) -> None:
        result = self.run_case(fixture="szse_listing_identity_conflict_synthetic.pdf")
        self.assertEqual(result["records"], [])
        self.assertIn("listing_cover_identity_conflict", result["limitation_codes"])

    def test_pdf_own_byte_ceiling_rejects_without_widening_shared_transport(
        self,
    ) -> None:
        result = self.run_case(oversized=True)
        self.assertEqual(result["source_result"], "unavailable")
        self.assertEqual(result["records"], [])
        self.assertIn("listing_pdf_too_large", result["limitation_codes"])


if __name__ == "__main__":
    unittest.main()
