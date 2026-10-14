from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
import unittest
import zipfile
import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import asdict
from dataclasses import replace
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_session_context import (  # noqa: E402
    ContextBindings,
    MarketSessionOperationRequest,
)
from a_share_research.market_session_sources import (  # noqa: E402
    ProductionMarketSessionAdapter,
    load_market_session_source_contracts,
)

TZ = ZoneInfo("Asia/Shanghai")
FIRST = datetime(2026, 10, 14, 10, tzinfo=TZ)
CHECKED = datetime(2026, 10, 14, 10, 0, 3, tzinfo=TZ)
BINDINGS = ContextBindings("1" * 64, "2" * 64, "3" * 64, "4" * 64, "source-fixture")


def source_fixtures():
    # These document layouts/bodies are synthetic. No research response body
    # was retained or copied into ordinary fixtures. The injected PDF digest
    # tests the exact-version verifier without manufacturing the real PDF.
    pdf = b"%PDF-1.7\nsynthetic-reviewed-rule-version\n"
    contracts = tuple(
        replace(
            item,
            synthetic=True,
            rule_pdf_sha256=hashlib.sha256(pdf).hexdigest()
            if item.rule_format == "pdf"
            else None,
        )
        for item in load_market_session_source_contracts()
    )
    bodies = {}
    for item in contracts:
        clauses = []
        for name, start, end, reopen in item.holiday_ranges:
            first, last, following = (
                datetime.fromisoformat(value) for value in (start, end, reopen)
            )
            clauses.append(
                f"{name}：{first.month}月{first.day}日（星期四）至{last.month}月{last.day}日（星期六）休市。{following.month}月{following.day}日起照常开市。"
            )
        annual = (
            "<html><head><title>"
            + item.calendar_title
            + "</title></head><body>时间：2025-12-22<p>"
            + item.calendar_document_number
            + "</p><p>2026年休市安排</p>"
            + "".join("<p>" + value + "</p>" for value in clauses)
            + "</body></html>"
        )
        bodies[item.calendar_uri] = HttpResponse(
            200,
            "text/html",
            annual.encode(),
            datetime(2026, 10, 14, 10, 0, 1, tzinfo=TZ),
        )
        weekdays = (
            item.weekday_section
            + "本所交易日为每周一至周五。国家法定假日和本所公告的休市日，本所市场休市。"
        )
        auction = (
            item.auction_section
            + "证券采用竞价交易方式的，每个交易日9:15至9:25为开盘集合竞价时间，9:30至11:30、13:00至14:57为连续竞价时间，14:57至15:00为收盘集合竞价时间。"
        )
        effect = item.effect_section + "本规则自2026年7月6日起施行。"
        if item.exchange == "SSE":
            paragraphs = [
                "上海证券交易所交易规则（2026年修订）",
                weekdays,
                auction,
                "3.7.1投资者可以通过盘后固定价格交易方式买卖A股和交易型开放式基金。",
                "3.7.2每个交易日的15:05至15:30为盘后固定价格交易时间，当日15:00仍处于停牌状态的证券不参加。",
                effect,
            ]
            xml = (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(
                    "<w:p><w:r><w:t>" + escape(value) + "</w:t></w:r></w:p>"
                    for value in paragraphs
                )
                + "</w:body></w:document>"
            )
            buffer = io.BytesIO()
            with zipfile.ZipFile(
                buffer, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("word/document.xml", xml)
            body, mime = (
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        elif item.exchange == "BSE":
            text = (
                "北京证券交易所交易规则北证公告〔2026〕17号时效性：现行有效。3.6.5第二三款、3.7.1至3.7.10及4.5.1至4.5.4的实施时间另行通知。"
                + weekdays
                + auction
                + "3.6.4本所每个交易日15:00至15:30对大宗交易进行成交确认。3.6.8大宗交易成交量在结束后计入该证券当日成交总量。3.7.2每个交易日15:05至15:30为盘后固定价格交易时间。"
                + effect
            )
            body, mime = (
                ("<html><body>" + text + "</body></html>").encode(),
                "text/html",
            )
        else:
            body, mime = pdf, "application/pdf"
        bodies[item.rule_uri] = HttpResponse(
            200, mime, body, datetime(2026, 10, 14, 10, 0, 2, tzinfo=TZ)
        )
    return contracts, bodies


class DocumentTransport:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def get(self, uri, headers):
        self.calls.append(uri)
        return self.responses[uri]


class MarketSessionSourceTests(unittest.TestCase):
    def test_three_exchange_documents_form_same_immutable_context_without_live_IO(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        transport = DocumentTransport(responses)
        adapter = ProductionMarketSessionAdapter(
            transport, contracts, clock=lambda: FIRST, execution_clock=lambda: CHECKED
        )
        outcome = adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 5
        )
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        projection = outcome.to_dict()
        context = projection["observations"][0]["payload"]["context"]
        self.assertEqual(context["screen_date"], "2026-10-13")
        self.assertEqual(context["information_cutoff"], FIRST.isoformat())
        self.assertEqual(projection["diagnostics"]["document_requests"], 6)
        self.assertEqual(len(transport.calls), 6)
        proofs = context["calendar_proofs"]
        self.assertEqual([item["exchange"] for item in proofs], ["SSE", "SZSE", "BSE"])
        self.assertEqual(
            proofs[2]["source_metadata"]["completion_kind"], "block_trade_confirmation"
        )
        self.assertIn("3.7.1-3.7.10", proofs[2]["source_metadata"]["deferred_sections"])
        self.assertIsNone(proofs[1]["source_metadata"]["exact_first_available_at"])
        self.assertEqual(proofs[1]["available_at"], "2026-04-25T00:00:00+08:00")
        self.assertEqual(proofs[1]["source_metadata"]["publication_precision"], "day")

    def test_invalid_bindings_are_rejected_before_any_document_request(self) -> None:
        contracts, responses = source_fixtures()
        transport = DocumentTransport(responses)
        outcome = ProductionMarketSessionAdapter(
            transport, contracts, clock=lambda: FIRST, execution_clock=lambda: CHECKED
        ).execute(
            MarketSessionOperationRequest(
                "2026-10-14", replace(BINDINGS, request_digest="invalid")
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(transport.calls, [])
        self.assertEqual(outcome.source_errors[0]["code"], "context_binding_mismatch")

    def test_changed_persisted_days_cannot_gain_authority_from_recomputed_digest(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        original = (
            ProductionMarketSessionAdapter(
                DocumentTransport(responses),
                contracts,
                clock=lambda: FIRST,
                execution_clock=lambda: CHECKED,
            )
            .execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
            .to_dict()["observations"][0]["payload"]["context"]
        )
        changed = deepcopy(original)
        for proof in changed["calendar_proofs"]:
            for day in proof["days"]:
                if day["session_date"] == "2026-10-13":
                    day["status"] = "holiday"
        changed["screen_date"] = "2026-10-12"
        del changed["digest"]
        digest = hashlib.sha256(
            json.dumps(
                changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        changed["digest"] = digest
        transport = DocumentTransport({})
        later = datetime(2026, 10, 15, 10, tzinfo=TZ)
        outcome = ProductionMarketSessionAdapter(
            transport, contracts, clock=lambda: later, execution_clock=lambda: later
        ).execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS, changed, digest), 5
        )
        self.assertEqual(outcome.coverage, "failed")
        self.assertFalse(outcome.observations)
        self.assertEqual(transport.calls, [])

    def test_unrecognized_annual_stops_that_exchange_and_retains_other_proofs(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        bse = contracts[2]
        responses[bse.calendar_uri] = replace(responses[bse.calendar_uri], status=403)
        transport = DocumentTransport(responses)
        outcome = ProductionMarketSessionAdapter(
            transport, contracts, clock=lambda: FIRST, execution_clock=lambda: CHECKED
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "partial")
        self.assertNotIn(bse.rule_uri, transport.calls)
        proofs = [
            item["payload"]["calendar_proof"]["exchange"]
            for item in outcome.to_dict()["observations"]
        ]
        self.assertEqual(proofs, ["SSE", "SZSE"])
        self.assertTrue(
            all("context" not in item["payload"] for item in outcome.observations)
        )

    def test_unknown_pdf_revision_has_explicit_version_failure(self) -> None:
        contracts, responses = source_fixtures()
        responses[contracts[1].rule_uri] = replace(
            responses[contracts[1].rule_uri],
            body=b"%PDF-1.7\nunqualified-new-revision\n",
        )
        outcome = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "partial")
        self.assertIn(
            "calendar_rule_version_unproved",
            [item["code"] for item in outcome.source_errors],
        )
        self.assertEqual(
            [
                item["payload"]["calendar_proof"]["exchange"]
                for item in outcome.to_dict()["observations"]
            ],
            ["SSE", "BSE"],
        )

    def test_invalid_response_types_are_partial_failures_without_exception_text(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        responses[contracts[0].calendar_uri] = replace(
            responses[contracts[0].calendar_uri], content_type=None
        )
        outcome = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "partial")
        self.assertIn(
            "calendar_document_type_unproved",
            [item["code"] for item in outcome.source_errors],
        )

    def test_successful_resume_uses_original_proof_without_any_acquisition(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        original = (
            ProductionMarketSessionAdapter(
                DocumentTransport(responses),
                contracts,
                clock=lambda: FIRST,
                execution_clock=lambda: CHECKED,
            )
            .execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
            .to_dict()["observations"][0]["payload"]["context"]
        )
        transport = DocumentTransport({})
        later = datetime(2026, 10, 15, 10, tzinfo=TZ)
        outcome = ProductionMarketSessionAdapter(
            transport, contracts, clock=lambda: later, execution_clock=lambda: later
        ).execute(
            MarketSessionOperationRequest(
                "2026-10-14", BINDINGS, original, original["digest"]
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "complete")
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["context"], original
        )
        self.assertEqual(transport.calls, [])

    def test_new_bse_notice_version_cannot_reuse_old_publication_binding(self) -> None:
        contracts, responses = source_fixtures()
        bse = contracts[2]
        responses[bse.rule_uri] = replace(
            responses[bse.rule_uri],
            body=responses[bse.rule_uri].body.replace(
                "北证公告〔2026〕17号".encode(), "北证公告〔2026〕18号".encode()
            ),
        )
        outcome = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "partial")
        self.assertIn(
            "calendar_rule_version_unproved",
            [error["code"] for error in outcome.source_errors],
        )

    def test_statutory_holiday_wording_does_not_change_proved_weekday_semantics(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        bse = contracts[2]
        responses[bse.rule_uri] = replace(
            responses[bse.rule_uri],
            body=responses[bse.rule_uri].body.replace(
                "国家法定假日".encode(), "国家法定节假日".encode()
            ),
        )
        outcome = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)

    def test_deferred_afterhours_is_not_fallback_for_wrong_operative_block_time(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        bse = contracts[2]
        responses[bse.rule_uri] = replace(
            responses[bse.rule_uri],
            body=responses[bse.rule_uri].body.replace(
                "15:00至15:30对大宗交易".encode(), "15:00至15:20对大宗交易".encode()
            ),
        )
        outcome = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "partial")
        self.assertIn(
            "calendar_day_completion_unproved",
            [error["code"] for error in outcome.source_errors],
        )

    def test_timeout_preserves_completed_exchange_and_stops_new_calls(self) -> None:
        contracts, responses = source_fixtures()
        clock_state = [0.0]

        class SlowTransport(DocumentTransport):
            def get(self, uri, headers):
                result = super().get(uri, headers)
                clock_state[0] += 10.0 if len(self.calls) == 3 else 0.5
                return result

        transport = SlowTransport(responses)
        outcome = ProductionMarketSessionAdapter(
            transport,
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
            monotonic_clock=lambda: clock_state[0],
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(
            [
                item["payload"]["calendar_proof"]["exchange"]
                for item in outcome.to_dict()["observations"]
            ],
            ["SSE"],
        )
        self.assertIn(
            "calendar_source_timeout",
            [error["code"] for error in outcome.source_errors],
        )

    def test_leading_zero_auction_hours_keep_same_calendar_semantics(self) -> None:
        contracts, responses = source_fixtures()
        bse = contracts[2]
        responses[bse.rule_uri] = replace(
            responses[bse.rule_uri],
            body=responses[bse.rule_uri]
            .body.replace(b"9:15", b"09:15")
            .replace(b"9:25", b"09:25")
            .replace(b"9:30", b"09:30"),
        )
        outcome = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)

    def test_changed_calendar_evidence_id_cannot_resume_even_with_new_local_digest(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        original = (
            ProductionMarketSessionAdapter(
                DocumentTransport(responses),
                contracts,
                clock=lambda: FIRST,
                execution_clock=lambda: CHECKED,
            )
            .execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
            .to_dict()["observations"][0]["payload"]["context"]
        )
        changed = deepcopy(original)
        changed["calendar_proofs"][2]["evidence_id"] = "unrelated-calendar"
        del changed["digest"]
        digest = hashlib.sha256(
            json.dumps(
                changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        changed["digest"] = digest
        transport = DocumentTransport({})
        outcome = ProductionMarketSessionAdapter(
            transport, contracts, clock=lambda: CHECKED, execution_clock=lambda: CHECKED
        ).execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS, changed, digest), 5
        )
        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(transport.calls, [])

    def test_oversize_document_and_future_response_time_do_not_form_C(self) -> None:
        for variant in ("oversize", "future"):
            with self.subTest(variant=variant):
                contracts, responses = source_fixtures()
                bse = contracts[2]
                original = responses[bse.calendar_uri]
                responses[bse.calendar_uri] = (
                    replace(original, body=b"x" * (2 * 1024 * 1024 + 1))
                    if variant == "oversize"
                    else replace(original, retrieved_at=datetime(2027, 1, 1, tzinfo=TZ))
                )
                outcome = ProductionMarketSessionAdapter(
                    DocumentTransport(responses),
                    contracts,
                    clock=lambda: FIRST,
                    execution_clock=lambda: CHECKED,
                ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 5)
                self.assertEqual(outcome.coverage, "partial")
                self.assertTrue(
                    all(
                        "context" not in item["payload"]
                        for item in outcome.observations
                    )
                )

    def test_declared_period_gap_and_future_request_do_not_acquire_documents(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        for requested in ("2026-07-05", "2026-10-15"):
            with self.subTest(requested=requested):
                transport = DocumentTransport(responses)
                outcome = ProductionMarketSessionAdapter(
                    transport,
                    contracts,
                    clock=lambda: FIRST,
                    execution_clock=lambda: CHECKED,
                ).execute(MarketSessionOperationRequest(requested, BINDINGS), 5)
                self.assertEqual(outcome.coverage, "failed")
                self.assertEqual(transport.calls, [])

    def test_production_operation_public_references_resolve_through_existing_fold(
        self,
    ) -> None:
        from a_share_research.research_result import (
            DirectTaskPlan,
            DirectTaskClaim,
            ResearchDependency,
            fold_direct_task_research_result,
        )

        contracts, responses = source_fixtures()
        adapter = ProductionMarketSessionAdapter(
            DocumentTransport(responses),
            contracts,
            clock=lambda: FIRST,
            execution_clock=lambda: CHECKED,
        )
        outcome = adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 5
        )
        plan = DirectTaskPlan(
            task_type="market_session_context_contract_test",
            as_of="2026-10-14",
            obligations=(adapter.obligation,),
            dependencies=(
                ResearchDependency(
                    "market_trading_session", "market_trading_session", 1, True
                ),
            ),
        )
        local = "market_session_context@1:0"
        result = fold_direct_task_research_result(
            plan,
            (outcome,),
            claims=(
                DirectTaskClaim(
                    "market_session_context",
                    "market_trading_session",
                    (local,),
                    {"context_evidence_ids": [local]},
                ),
            ),
            summary="Synthetic qualified-calendar parser contract.",
        ).to_dict()
        self.assertEqual(
            result["claims"][0]["payload"]["context_evidence_ids"],
            [result["observations"][0]["id"]],
        )
        first = outcome.to_dict()
        first["observations"][0]["payload"]["context"]["calendar_proofs"].clear()
        self.assertEqual(
            len(
                outcome.to_dict()["observations"][0]["payload"]["context"][
                    "calendar_proofs"
                ]
            ),
            3,
        )

    def test_clean_install_default_factory_loads_contracts_and_real_parser_with_fake_transport(
        self,
    ) -> None:
        contracts, responses = source_fixtures()
        request_material = {
            "contracts": [asdict(item) for item in contracts],
            "responses": {
                uri: {
                    "mime": value.content_type,
                    "body": base64.b64encode(value.body).decode(),
                }
                for uri, value in responses.items()
            },
        }
        source_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        program = """
import json,sys,base64
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from a_share_research.identity_sources import HttpResponse
from a_share_research.market_session_context import ContextBindings,MarketSessionOperationRequest
import a_share_research.market_session_sources as source
material=json.load(sys.stdin)
# Test-only synthetic body binding; the default source catalogue still loads.
actual=source.load_market_session_source_contracts()
assert [item.exchange for item in actual]==['SSE','SZSE','BSE']
for item in material['contracts']:
    for key in ('auction_times','completion_sections','deferred_sections'):
        item[key]=tuple(item[key])
    item['holiday_ranges']=tuple(tuple(row) for row in item['holiday_ranges'])
synthetic=tuple(source.MarketSessionSourceContract(**item) for item in material['contracts'])
source.load_market_session_source_contracts=lambda:synthetic
class Fake:
    def get(self,uri,headers):
        value=material['responses'][uri]
        return HttpResponse(200,value['mime'],base64.b64decode(value['body']),datetime.fromisoformat('2026-10-14T10:00:02+08:00'))
adapter=source.build_default_market_session_adapter(Fake(),clock=lambda:datetime.fromisoformat('2026-10-14T10:00:00+08:00'),execution_clock=lambda:datetime.fromisoformat('2026-10-14T10:00:03+08:00'))
outcome=adapter.execute(MarketSessionOperationRequest('2026-10-14',ContextBindings('1'*64,'2'*64,'3'*64,'4'*64,'installed-source-fixture')),5)
assert outcome.coverage=='complete',outcome.to_dict()['source_errors']
context=outcome.to_dict()['observations'][0]['payload']['context']
print(json.dumps({'screen_date':context['screen_date'],'exchanges':[p['exchange'] for p in context['calendar_proofs']],'documents':outcome.diagnostics['document_requests']},sort_keys=True))
"""
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                source_root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", program, str(installed / "scripts")],
                cwd=temporary,
                input=json.dumps(request_material),
                capture_output=True,
                text=True,
                timeout=15,
                close_fds=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout),
                {
                    "screen_date": "2026-10-13",
                    "exchanges": ["SSE", "SZSE", "BSE"],
                    "documents": 6,
                },
            )


if __name__ == "__main__":
    unittest.main()
