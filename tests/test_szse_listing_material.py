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
            from a_share_research.identity_sources import HttpResponse
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
