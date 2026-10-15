from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent


class SseListingNoticeTests(unittest.TestCase):
    def run_case(self, mode: str = "normal", day: str = "2019-07-22") -> dict:
        script = dedent("""
            import json,sys
            from datetime import datetime
            sys.path.insert(0,sys.argv[1])
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            from a_share_research.universe_sources import acquire_default_universe,CurrentSourcePolicies,CurrentSourceTransports
            from a_share_research.sse_listing_notice import SseListingNoticeTarget
            now=datetime.fromisoformat('2026-10-15T10:00:00+08:00')
            retrieved=datetime.fromisoformat('2026-10-15T10:01:00+08:00')
            class Source:
                def __init__(self):self.calls=0
                def get(self,url,headers):
                    assert url=='https://www.sse.com.cn/disclosure/announcement/listing/c/c_19990101_1234567.shtml'
                    self.calls+=1
                    title='关于合成发行主体股份有限公司人民币普通股股票科创板上市交易的公告'
                    body='合成发行主体股份有限公司A股股票将在本所科创板上市交易。相关准备工作完成后，于2019年7月22日起上市交易。证券简称为"合成沪股"，证券代码为"120001"。'
                    header='2019-07-16'
                    if sys.argv[2]=='missing_date':header=''
                    if sys.argv[2]=='wrong_class':title=title.replace('人民币普通股','存托凭证')
                    if sys.argv[2]=='proposal':body=body.replace('将在本所科创板上市交易','拟申请在本所科创板上市交易')
                    if sys.argv[2]=='late_header':header='2019-07-22'
                    html='<html><head><script>2019-07-01</script></head><body><h1>'+title+'</h1><p>'+header+'</p><div>'+body+'</div></body></html>'
                    return HttpResponse(200,'text/html',html.encode(),retrieved)
            class Other:
                def get(self,*args):raise AssertionError('No current-to-history fallback')
                def post(self,*args):raise AssertionError('No current-to-history fallback')
            source=Source();lanes=ProviderLanes()
            result=acquire_default_universe(UniverseRequest(sys.argv[3],sys.argv[3]+'T23:59:59+08:00'),research_now=now,lanes=lanes,policies=CurrentSourcePolicies(ProviderLanePolicy(1,2),ProviderLanePolicy(1,2),ProviderLanePolicy(1,2)),transports=CurrentSourceTransports(Other(),Other(),Other()),sse_listing_event=SseListingNoticeTarget('https://www.sse.com.cn/disclosure/announcement/listing/c/c_19990101_1234567.shtml'),sse_listing_transport=source)
            acquired=result.source_acquisitions[0]
            assert lanes.is_authoritative(acquired.lane_result)
            assert len(acquired.lane_result.attempts)==source.calls==1
            print(json.dumps(result.to_public_dict(),ensure_ascii=False))
        """)
        root = Path(__file__).resolve().parents[1] / "skill/a-share-research"
        with tempfile.TemporaryDirectory(prefix="asr122-sse-event-") as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    script,
                    str(installed / "scripts"),
                    mode,
                    day,
                ],
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
            self.assertNotIn(temporary, result.stdout)
            return json.loads(result.stdout)

    def test_official_notice_uses_same_article_header_day_and_keeps_unknown_total(
        self,
    ) -> None:
        result = self.run_case()
        sse = result["universe"]["partitions"]["SSE"]
        self.assertFalse(result["universe"]["full_a_share_universe"])
        self.assertEqual(
            sse["reconciliation"], {"expected": None, "resolved": 0, "failed": None}
        )
        self.assertEqual(sse["securities"][0]["canonical_security_id"], "SSE:120001")
        source = result["source_acquisitions"][0]
        self.assertEqual(source["records"][0]["article_header_day"], "2019-07-16")
        self.assertEqual(
            source["records"][0]["available_at"], "2019-07-17T00:00:00+08:00"
        )
        self.assertEqual(
            source["records"][0]["source_date_role"],
            "article_header_day_no_literal_publication_label",
        )
        self.assertNotIn("1999-01-01", json.dumps(result))
        self.assertNotIn("https://", json.dumps(result))

    def test_missing_article_day_wrong_class_and_proposal_cannot_create_members(
        self,
    ) -> None:
        for mode in ("missing_date", "wrong_class", "proposal", "late_header"):
            with self.subTest(mode=mode):
                result = self.run_case(mode)
                part = result["universe"]["partitions"]["SSE"]
                self.assertEqual(part["securities"], [])
                self.assertIsNone(part["reconciliation"]["expected"])
                self.assertFalse(result["universe"]["full_a_share_universe"])

    def test_event_does_not_supply_membership_before_or_after_its_formal_day(
        self,
    ) -> None:
        for day in ("2019-07-21", "2019-07-23"):
            with self.subTest(day=day):
                result = self.run_case(day=day)
                self.assertEqual(
                    result["universe"]["partitions"]["SSE"]["securities"], []
                )
                self.assertIn(
                    "outside_proven_listing_event_day",
                    result["source_acquisitions"][0]["limitation_codes"],
                )


if __name__ == "__main__":
    unittest.main()
