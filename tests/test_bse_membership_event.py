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


def synthetic_pdf(pages: list[str]) -> bytes:
    """A wholly synthetic Unicode PDF, with an explicit character map."""
    chars = sorted(set("".join(pages)) - {"\n"})
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CIDSystemInfo << /Registry (ASR) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /ASRSynthetic def /CMapType 2 def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
    )
    for offset in range(0, len(chars), 80):
        part = chars[offset : offset + 80]
        cmap += f"{len(part)} beginbfchar\n"
        cmap += "".join(
            f"<{c.encode('utf-16-be').hex()}> <{c.encode('utf-16-be').hex()}>\n"
            for c in part
        )
        cmap += "endbfchar\n"
    cmap += "endcmap CMapName currentdict /CMap defineresource pop end end\n"
    objects: list[bytes] = []

    def stream(data: bytes) -> bytes:
        return f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"

    objects.extend(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"",
            b"<< /Type /Font /Subtype /Type0 /BaseFont /ASRSynthetic /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
            b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ASRSynthetic /CIDSystemInfo << /Registry (ASR) /Ordering (UCS) /Supplement 0 >> /DW 1000 /CIDToGIDMap /Identity >>",
            stream(cmap.encode()),
        ]
    )
    kids = []
    for page in pages:
        page_id = len(objects) + 1
        kids.append(f"{page_id} 0 R")
        content_id = page_id + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        text = "BT /F1 10 Tf 1 0 0 1 40 750 Tm\n"
        for line in page.splitlines():
            text += f"<{line.encode('utf-16-be').hex()}> Tj 0 -14 Td\n"
        objects.append(stream((text + "ET\n").encode()))
    objects[1] = (
        f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>".encode()
    )
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


class BseMembershipEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = Path(__file__).resolve().parents[1] / "skill/a-share-research"
        spec = importlib.machinery.PathFinder.find_spec(
            "pypdf", [str(source / ".runtime"), *sys.path]
        )
        if spec is None or spec.origin is None:
            raise RuntimeError("Provide locked pypdf 6.10.2 for the installed test.")
        package = Path(spec.origin).resolve().parent
        metadata = next(
            d
            for d in importlib.metadata.distributions(path=[str(package.parent)])
            if d.metadata.get("Name", "").lower() == "pypdf"
        )
        if metadata.version != "6.10.2":
            raise RuntimeError("Locked pypdf 6.10.2 is required.")
        cls.temporary = tempfile.TemporaryDirectory(prefix="asr-122-bse-installed-")
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
        issuer = "合成公司0股份有限公司"
        materials = (
            [
                issuer + "法律意见书\n股票代码832000",
                "公司发行的人民币普通股股票，在北京证券交易所上市，股票代码832000。",
            ],
            [
                "证券简称：合成公司0\n" + issuer + "简式权益变动报告书",
                "权益变动\n上市公司名称："
                + issuer
                + "\n股票代码400000\n股票种类：人民币普通股",
            ],
            [
                issuer + "半年度报告\n股票代码400000",
                "股票代码400000\n股票上市交易所：北京证券交易所\n上市时间2021年11月15日",
            ],
        )
        for name, pages in zip(("old", "new", "listing"), materials, strict=True):
            (cls.root / f"{name}.pdf").write_bytes(synthetic_pdf(pages))
        (cls.root / "new_wrong_table.pdf").write_bytes(
            synthetic_pdf(
                [
                    "证券简称：合成公司0\n" + issuer + "简式权益变动报告书",
                    "证券代码400000\n权益变动\n股票代码400999\n股票种类：人民币普通股",
                ]
            )
        )
        (cls.root / "old_wrong_table.pdf").write_bytes(
            synthetic_pdf(
                [
                    issuer + "法律意见书",
                    "证券代码832000\n股票代码123456。公司发行的人民币普通股股票，在北京证券交易所上市。",
                ]
            )
        )
        (cls.root / "future_listing.pdf").write_bytes(
            synthetic_pdf(
                [
                    issuer + "半年度报告\n股票代码400000",
                    "股票代码400000\n股票上市交易所：北京证券交易所\n上市时间2026年11月15日",
                ]
            )
        )
        (cls.root / "wrong_class.pdf").write_bytes(
            synthetic_pdf(
                [
                    issuer + "简式权益变动报告书",
                    "权益变动\n股票代码400000\n股票种类：优先股",
                ]
            )
        )
        issuer2 = "合成公司1股份有限公司"
        for name, pages in zip(
            ("old1", "new1", "listing1"),
            (
                [
                    issuer2 + "法律意见书",
                    "公司发行的人民币普通股股票，在北京证券交易所上市，股票代码100001。",
                ],
                [
                    issuer2 + "简式权益变动报告书",
                    "权益变动\n股票代码400001\n股票种类：人民币普通股",
                ],
                [
                    issuer2 + "半年度报告",
                    "股票代码400001\n股票上市交易所：北京证券交易所\n上市时间2021年12月8日",
                ],
            ),
            strict=True,
        ):
            (cls.root / f"{name}.pdf").write_bytes(synthetic_pdf(pages))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def run_event_case(self, mode: str = "normal") -> dict[str, object]:
        script = dedent("""
            import copy,json,sys
            from pathlib import Path
            from datetime import datetime
            sys.path.insert(0,sys.argv[1])
            from a_share_research.bse_membership_event import BseMembershipEventTarget, observe_bse_material
            from a_share_research.universe_sources import acquire_default_universe,CurrentSourcePolicies,CurrentSourceTransports
            from a_share_research.identity_sources import HttpResponse
            from a_share_research.provider_lane import ProviderLanePolicy,ProviderLanes
            from a_share_research.universe_input import UniverseRequest
            index=1 if sys.argv[3]=='second_target' else 0
            code=str(400000+index);issuer=f'合成公司{index}股份有限公司'
            urls=(
                f'https://static.cninfo.com.cn/finalpage/2022-01-04/{1111111+index}.PDF',
                f'https://www.bse.cn/disclosure/2026/2026-07-22/{index+2:08d}-2222-2222-2222-222222222222.pdf',
                f'https://static.cninfo.com.cn/finalpage/2026-08-14/{3333333+index}.PDF')
            bodies={u:Path(sys.argv[2],n+('1' if index else '')+'.pdf').read_bytes() for u,n in zip(urls,('old','new','listing'))}
            if sys.argv[3]=='future_listing':bodies[urls[2]]=Path(sys.argv[2],'future_listing.pdf').read_bytes()
            if sys.argv[3]=='wrong_class':bodies[urls[1]]=Path(sys.argv[2],'wrong_class.pdf').read_bytes()
            if sys.argv[3]=='old_wrong_table_target':
                bodies[urls[0]]=Path(sys.argv[2],'old_wrong_table.pdf').read_bytes()
            if sys.argv[3]=='wrong_table_target':
                bodies[urls[1]]=Path(sys.argv[2],'new_wrong_table.pdf').read_bytes()
            class Documents:
                calls=0
                retried=False
                stamp=datetime.fromisoformat('2026-10-16T06:43:00+08:00')
                def get(self,url,headers):
                    assert url in bodies
                    self.calls+=1
                    if sys.argv[3]=='retry_budget' and self.calls>3 and url==urls[1] and not self.retried:
                        self.retried=True
                        return HttpResponse(429,'application/pdf',b'',self.stamp)
                    if sys.argv[3]=='observer_blocked':
                        return HttpResponse(403,'text/plain',b'/private/source-secret',self.stamp)
                    return HttpResponse(200,'application/pdf',bodies[url],self.stamp)
            class Mapping:
                calls=0
                def get(self,url,headers):
                    assert url=='https://www.bse.cn/code_mapping/200025792.html'
                    self.calls+=1
                    if sys.argv[3]=='mapping_blocked':return HttpResponse(403,'text/plain',b'/private/mapping-secret',datetime.fromisoformat('2026-10-16T07:01:00+08:00'))
                    rows=''.join(f'<tr><td>{i+1}</td><td>合成公司{i}</td><td>2020/4/8</td><td>{100000+i if i else 832000}</td><td>{400000+i}</td></tr>' for i in range(248))
                    body='<html><body>新旧代码对照表 2025-09-12<table><tr><td>序号</td><td>证券简称</td><td>上市日期</td><td>旧代码</td><td>新代码</td></tr>'+rows+"<tr><td colspan='5'>合成脚注</td></tr></table></body></html>"
                    return HttpResponse(200,'text/html',body.encode(),datetime.fromisoformat('2026-10-16T07:01:00+08:00'))
            class Disabled:
                def get(self,*a):
                    from a_share_research.identity_sources import TransportError
                    raise TransportError('unavailable','explicit fake current source family')
                def post(self,*a):return self.get(*a)
            docs=Documents();mapping=Mapping();lanes=ProviderLanes()
            backoffs=(0,) if sys.argv[3]=='retry_budget' else ()
            def policy(n,t):return ProviderLanePolicy(n,t,rate_limit_backoffs=backoffs)
            if sys.argv[3]=='observer_blocked':
                observation=observe_bse_material(urls[0],transport=docs,lanes=lanes,policy=policy(1,4))
                assert docs.calls==1
                print(json.dumps(observation.to_public_dict()));sys.exit(0)
            observations=tuple(observe_bse_material(u,transport=docs,lanes=lanes,policy=policy(1,4)) for u in urls)
            assert all(o.source_result=='complete' and lanes.is_authoritative(o.lane_result) for o in observations)
            observed=tuple(o.material for o in observations)
            assert docs.calls==3
            if sys.argv[3]=='forged_observation':
                forged=copy.copy(observed[0])
                object.__setattr__(forged,'available_upper_bound','2000-01-01T00:00:00+08:00')
                observed=(forged,*observed[1:])
            try:
                target=BseMembershipEventTarget(code,issuer,*observed)
            except ValueError:
                print(json.dumps({'validation_error':'invalid_source_observation'}));sys.exit(0)
            docs.stamp=datetime.fromisoformat('2026-10-16T07:01:00+08:00')
            if sys.argv[3]=='version_changed':
                bodies[urls[1]]+=bytes([10])+b'% changed representation'+bytes([10])
            frozen=datetime.fromisoformat('2026-10-16T07:00:00+08:00')
            if sys.argv[3]=='early_cutoff':frozen=datetime.fromisoformat('2026-10-16T06:40:00+08:00')
            day={'before_day':'2025-10-08','after_day':'2025-10-10','today':'2026-10-16'}.get(sys.argv[3],'2025-10-09')
            budget=3 if sys.argv[3]=='budget' else 4
            if sys.argv[3] in ('identity','identity_mismatch','identity_empty','identity_missing'):
                from a_share_research.universe_sources import create_default_universe_adapters
                from a_share_research.universe_input import prepare_exchange_list_outcome,prepare_identity_outcome
                owners=create_default_universe_adapters(research_now=frozen,lanes=lanes,policies=CurrentSourcePolicies(*(policy(4,8) for _ in range(3))),transports=CurrentSourceTransports(Disabled(),Disabled(),Disabled()),bse_membership_event=target,bse_material_transport=docs,bse_mapping_transport=mapping)
                request=UniverseRequest(day,frozen.isoformat())
                source_list=owners.exchange_list_adapters[2].acquire(request,8)
                prepared=prepare_exchange_list_outcome(source_list)
                batch=tuple(prepared.records)
                if sys.argv[3]=='identity_empty':batch=()
                if sys.argv[3] in ('identity_mismatch','identity_missing'):
                    from types import SimpleNamespace
                    extra=SimpleNamespace(exchange='BSE',code=code if sys.argv[3]=='identity_mismatch' else '999999',name='不可冒名',security_category='A_SHARE')
                    batch=(extra,) if sys.argv[3]=='identity_mismatch' else (*batch,extra)
                outcome=owners.identity_adapters[2].resolve(request,batch,8)
                identity=prepare_identity_outcome(outcome)
                print(json.dumps({'identity':{'source_result':identity.source_result,'expected_count':identity.expected_count,'records':[{'code':r.code,'name':r.name,'listing_state':r.listing_state,'effective_from':r.effective_from,'effective_to':r.effective_to,'observed_at':r.observed_at,'available_at':r.available_at,'retrieved_at':r.retrieved_at} for r in identity.records]},'fixture_calls':{'documents':docs.calls,'mapping':mapping.calls}},ensure_ascii=False));sys.exit(0)
            result=acquire_default_universe(UniverseRequest(day,frozen.isoformat()),research_now=frozen,lanes=lanes,policies=CurrentSourcePolicies(*(policy(budget,8) for _ in range(3))),transports=CurrentSourceTransports(Disabled(),Disabled(),Disabled()),bse_membership_event=target,bse_material_transport=docs,bse_mapping_transport=mapping)
            value=result.to_public_dict()
            value['fixture_calls']={'documents':docs.calls,'mapping':mapping.calls}
            print(json.dumps(value,ensure_ascii=False))
        """)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                script,
                str(self.installed / "scripts"),
                str(self.root),
                mode,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
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
        value = json.loads(result.stdout)
        self.assertNotIn(str(self.root), result.stdout)
        return value

    def test_installed_default_normalizer_retains_one_cutover_member_with_unknown_market_total(
        self,
    ) -> None:
        value = self.run_event_case()
        bse = value["universe"]["partitions"]["BSE"]
        self.assertIsNone(bse["reconciliation"]["expected"])
        self.assertEqual(bse["source_result"], "partial")
        self.assertEqual(bse["securities"][0]["canonical_security_id"], "BSE:400000")
        self.assertEqual(bse["securities"][0]["name"], "合成公司0")
        self.assertFalse(value["universe"]["full_a_share_universe"])
        self.assertEqual(value["fixture_calls"], {"documents": 6, "mapping": 1})

    def test_source_audit_keeps_admission_selected_layer_cutover_and_availability_roles(
        self,
    ) -> None:
        source = self.run_event_case()["source_acquisitions"][2]
        self.assertEqual(source["bse_listing_date"], "2021-11-15")
        self.assertEqual(source["code_event"]["displayed_listing_date"], "2020-04-08")
        self.assertEqual(
            source["code_event"]["code_change_effective_date"], "2025-10-09"
        )
        self.assertEqual(
            [v["role"] for v in source["material_versions"]],
            ["old_share", "new_share", "listing"],
        )
        self.assertTrue(
            all(
                v["availability_role"] == "observed_retrieval_upper_bound_only"
                for v in source["material_versions"]
            )
        )
        self.assertEqual(
            sum(len(v["observation_attempts"]) for v in source["material_versions"]), 3
        )
        self.assertEqual([a["stage"] for a in source["attempts"]], [1, 2, 3, 4])

    def test_copied_or_backdated_observation_cannot_borrow_another_source_attempt(
        self,
    ) -> None:
        value = self.run_event_case("forged_observation")
        self.assertEqual(value, {"validation_error": "invalid_source_observation"})

    def test_material_observation_preserves_permission_failure_and_actual_attempt(
        self,
    ) -> None:
        value = self.run_event_case("observer_blocked")
        self.assertEqual(value["source_result"], "credential_blocked")
        self.assertTrue(value["attempted"])
        self.assertEqual(len(value["attempts"]), 1)
        self.assertIsNone(value["material"])
        self.assertNotIn("source-secret", json.dumps(value))

    def test_generic_header_cannot_attribute_another_stock_table_to_target(
        self,
    ) -> None:
        for mode in ("wrong_table_target", "old_wrong_table_target"):
            with self.subTest(mode=mode):
                value = self.run_event_case(mode)
                self.assertEqual(
                    value["universe"]["partitions"]["BSE"]["securities"], []
                )
                self.assertFalse(value["universe"]["full_a_share_universe"])

    def test_same_default_method_accepts_another_issuer_and_nonprefix_code_pair(
        self,
    ) -> None:
        value = self.run_event_case("second_target")
        records = value["universe"]["partitions"]["BSE"]["securities"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["canonical_security_id"], "BSE:400001")
        self.assertEqual(records[0]["name"], "合成公司1")
        self.assertEqual(value["fixture_calls"], {"documents": 6, "mapping": 1})

    def test_cutover_day_proof_does_not_fill_neighbouring_or_current_dates(
        self,
    ) -> None:
        for mode in ("before_day", "after_day", "today"):
            with self.subTest(mode=mode):
                value = self.run_event_case(mode)
                part = value["universe"]["partitions"]["BSE"]
                self.assertEqual(part["securities"], [])
                self.assertIsNone(part["reconciliation"]["expected"])
                self.assertEqual(value["fixture_calls"], {"documents": 3, "mapping": 0})
                self.assertIn(
                    "outside_proven_code_cutover_day",
                    value["source_acquisitions"][2]["limitation_codes"],
                )

    def test_observation_upper_bound_is_not_backfilled_before_frozen_cutoff(
        self,
    ) -> None:
        value = self.run_event_case("early_cutoff")
        self.assertEqual(value["universe"]["partitions"]["BSE"]["securities"], [])
        self.assertEqual(value["fixture_calls"], {"documents": 3, "mapping": 0})
        self.assertIn(
            "material_observation_after_cutoff",
            value["source_acquisitions"][2]["limitation_codes"],
        )

    def test_same_url_changed_bytes_cannot_inherit_previous_version_bound(self) -> None:
        value = self.run_event_case("version_changed")
        self.assertEqual(value["universe"]["partitions"]["BSE"]["securities"], [])
        self.assertEqual(value["fixture_calls"], {"documents": 5, "mapping": 1})
        self.assertIn(
            "material_version_changed",
            value["source_acquisitions"][2]["limitation_codes"],
        )

    def test_code_event_permission_failure_is_not_ordinary_unavailable(self) -> None:
        value = self.run_event_case("mapping_blocked")
        source = value["source_acquisitions"][2]
        self.assertEqual(source["source_result"], "credential_blocked")
        self.assertEqual(value["fixture_calls"], {"documents": 3, "mapping": 1})
        self.assertNotIn("mapping-secret", json.dumps(value))

    def test_default_identity_owner_uses_production_list_batch_and_same_qualified_evidence(
        self,
    ) -> None:
        value = self.run_event_case("identity")
        identity = value["identity"]
        self.assertEqual(identity["expected_count"], 1)
        self.assertEqual(identity["source_result"], "complete")
        self.assertEqual(
            identity["records"],
            [
                {
                    "code": "400000",
                    "name": "合成公司0",
                    "listing_state": "listed",
                    "effective_from": "2025-10-09",
                    "effective_to": None,
                    "observed_at": "2026-10-16T06:43:00+08:00",
                    "available_at": "2026-10-16T06:43:00+08:00",
                    "retrieved_at": "2026-10-16T07:01:00+08:00",
                }
            ],
        )
        self.assertEqual(value["fixture_calls"], {"documents": 9, "mapping": 2})

    def test_finite_event_budget_preserves_unknown_denominator_and_real_attempts(
        self,
    ) -> None:
        value = self.run_event_case("budget")
        source = value["source_acquisitions"][2]
        self.assertEqual(source["records"], [])
        self.assertIsNone(source["expected_count"])
        self.assertEqual(source["source_result"], "partial")
        self.assertEqual(len(source["attempts"]), 3)
        self.assertEqual(value["fixture_calls"], {"documents": 5, "mapping": 1})
        self.assertIn("actual_attempt_budget_exhausted", source["limitation_codes"])

    def test_identity_batch_keeps_missing_expected_and_rejects_name_substitution(
        self,
    ) -> None:
        mismatch = self.run_event_case("identity_mismatch")
        self.assertEqual(
            mismatch["identity"],
            {"source_result": "unavailable", "expected_count": 1, "records": []},
        )
        missing = self.run_event_case("identity_missing")
        self.assertEqual(missing["identity"]["source_result"], "partial")
        self.assertEqual(missing["identity"]["expected_count"], 2)
        self.assertEqual(len(missing["identity"]["records"]), 1)
        empty = self.run_event_case("identity_empty")
        self.assertEqual(
            empty["identity"],
            {"source_result": "unavailable", "expected_count": 0, "records": []},
        )
        self.assertEqual(empty["fixture_calls"], {"documents": 6, "mapping": 1})

    def test_wrong_share_class_and_future_bse_admission_cannot_qualify_cutover(
        self,
    ) -> None:
        for mode, reason in (
            ("wrong_class", "material_role_unproved"),
            ("future_listing", "bse_admission_after_code_event"),
        ):
            with self.subTest(mode=mode):
                value = self.run_event_case(mode)
                source = value["source_acquisitions"][2]
                self.assertEqual(source["records"], [])
                self.assertIn(reason, source["limitation_codes"])
                self.assertFalse(value["universe"]["full_a_share_universe"])

    def test_retry_uses_same_actual_leaf_budget_as_initial_material_requests(
        self,
    ) -> None:
        value = self.run_event_case("retry_budget")
        source = value["source_acquisitions"][2]
        self.assertEqual(source["records"], [])
        self.assertEqual(len(source["attempts"]), 4)
        self.assertEqual(value["fixture_calls"], {"documents": 6, "mapping": 1})
        self.assertIn("actual_attempt_budget_exhausted", source["limitation_codes"])


if __name__ == "__main__":
    unittest.main()
