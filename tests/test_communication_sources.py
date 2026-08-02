from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.communication_sources import (  # noqa: E402
    ClsMarketFlashOperation,
    CninfoInvestorQaOperation,
    EastmoneyMarketFlashOperation,
    FallbackMarketFlashOperation,
    MootdxF10Operation,
)
from a_share_research.content_contract import ContentQuery  # noqa: E402
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateDiagnostic,
    SerialRequestGate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
FIXTURES = Path(__file__).parent / "fixtures" / "research_content" / "communications"
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)
T = TypeVar("T")


class DiagnosticRequestGate:
    def __init__(self, diagnostics: tuple[RequestGateDiagnostic, ...] = ()) -> None:
        self.diagnostics = diagnostics

    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), self.diagnostics


class FixedTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.get_urls: list[str] = []
        self.post_calls: list[tuple[str, bytes]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.get_urls.append(url)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        self.post_calls.append((url, body))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(body: bytes, content_type: str = "application/json") -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=body,
        retrieved_at=RETRIEVED_AT,
    )


def json_response(payload: object) -> HttpResponse:
    return response(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def market_flash_query(*, allow_fallback: bool = True) -> ContentQuery:
    return ContentQuery(
        material_types=("market_flash",),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-08-02",
        published_to="2026-08-02",
        limit=2,
        subject=None,
        parameters={},
        allow_fallback=allow_fallback,
    )


def investor_qa_query() -> ContentQuery:
    return ContentQuery(
        material_types=("investor_qa",),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-07-01",
        published_to="2026-08-02",
        limit=20,
        subject={
            "security": {
                "exchange": "SZSE",
                "code": "300058",
                "type": "A_SHARE",
            },
            "name": "蓝色光标",
            "issuer": {
                "name": "北京蓝色光标数据科技股份有限公司",
                "identifier": {
                    "scheme": "CNINFO_ORG_ID",
                    "value": "9900010147",
                },
                "security_relationship": "verified",
            },
        },
        parameters={},
    )


def issuer_profile_query() -> ContentQuery:
    return ContentQuery(
        material_types=("issuer_profile",),
        keywords=(),
        as_of="2026-08-02",
        published_from="2026-01-01",
        published_to="2026-08-02",
        limit=10,
        subject=investor_qa_query().subject,
        parameters={"profile_categories": ["公司概况"]},
    )


class CommunicationSourceOperationTests(unittest.TestCase):
    def test_cls_market_flash_uses_local_signature_and_preserves_source_time(
        self,
    ) -> None:
        transport = FixedTransport(
            [response((FIXTURES / "cls_success.json").read_bytes())]
        )

        batch = ClsMarketFlashOperation(transport).collect(market_flash_query())

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)
        item = batch.observations[0]
        self.assertEqual(item.material_type, "market_flash")
        self.assertEqual(item.source_operation, "cls_market_flash@1")
        self.assertEqual(item.source_role, "market_signal")
        self.assertEqual(item.source_document_id, "2443575")
        self.assertEqual(item.published_at, "2026-08-02T19:26:05+08:00")
        self.assertEqual(item.retrieved_at, RETRIEVED_AT)
        self.assertEqual(item.locator_uri, "https://www.cls.cn/detail/2443575")
        self.assertEqual(batch.limitations, ("feed_completeness_unproven",))
        self.assertEqual(
            transport.get_urls,
            [
                "https://www.cls.cn/v1/roll/get_roll_list?"
                "appName=CailianpressWeb&last_time=&os=web&refresh_type=1&rn=2&"
                "sv=7.7.5&sign=681656257b917ff407cb7444df747354"
            ],
        )

    def test_eastmoney_market_flash_requires_success_code_and_preserves_item_id(
        self,
    ) -> None:
        transport = FixedTransport(
            [response((FIXTURES / "eastmoney_success.json").read_bytes())]
        )

        batch = EastmoneyMarketFlashOperation(
            transport,
            trace_factory=lambda: "00000000-0000-4000-8000-000000000016",
            request_gate=DiagnosticRequestGate(
                (
                    RequestGateDiagnostic(
                        code="source_request_paced", delay_seconds=1.125
                    ),
                )
            ),
        ).collect(market_flash_query())

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)
        item = batch.observations[0]
        self.assertEqual(item.material_type, "market_flash")
        self.assertEqual(item.source_operation, "eastmoney_market_flash@1")
        self.assertEqual(item.source_role, "market_signal")
        self.assertEqual(item.source_document_id, "202608023829145798")
        self.assertEqual(item.published_at, "2026-08-02T19:11:39+08:00")
        self.assertEqual(
            item.locator_uri,
            "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
            "#fast-news-202608023829145798",
        )
        self.assertEqual(item.limitations, ("publication_time_timezone_not_explicit",))
        self.assertIn(
            "req_trace=00000000-0000-4000-8000-000000000016",
            transport.get_urls[0],
        )
        self.assertEqual(batch.limitations, ("feed_completeness_unproven",))
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        self.assertEqual(batch.degradations[0].details["delay_seconds"], "1.125")

    def test_market_flash_failures_distinguish_empty_business_status_and_schema(
        self,
    ) -> None:
        cls_empty = ClsMarketFlashOperation(
            FixedTransport([json_response({"errno": 0, "data": {"roll_data": []}})])
        ).collect(market_flash_query())
        eastmoney_business_error = EastmoneyMarketFlashOperation(
            FixedTransport([json_response({"code": "0", "data": {}})]),
            trace_factory=lambda: "trace",
            request_gate=DiagnosticRequestGate(),
        ).collect(market_flash_query())
        eastmoney_bad_record = EastmoneyMarketFlashOperation(
            FixedTransport(
                [
                    json_response(
                        {
                            "code": "1",
                            "req_trace": "trace",
                            "data": {
                                "fastNewsList": [
                                    {"code": "N-1", "title": "缺少公开时间"}
                                ]
                            },
                        }
                    )
                ]
            ),
            trace_factory=lambda: "trace",
            request_gate=DiagnosticRequestGate(),
        ).collect(market_flash_query())

        self.assertEqual(cls_empty.source_errors[0].code, "empty_response")
        self.assertEqual(
            eastmoney_business_error.source_errors[0].code, "provider_error"
        )
        self.assertEqual(eastmoney_bad_record.source_errors[0].code, "unknown_schema")

    def test_market_flash_primary_success_short_circuits_fallback(self) -> None:
        transport = FixedTransport(
            [
                response((FIXTURES / "cls_success.json").read_bytes()),
                response((FIXTURES / "eastmoney_success.json").read_bytes()),
            ]
        )
        operation = FallbackMarketFlashOperation(
            ClsMarketFlashOperation(transport),
            EastmoneyMarketFlashOperation(
                transport,
                trace_factory=lambda: "00000000-0000-4000-8000-000000000016",
                request_gate=DiagnosticRequestGate(),
            ),
        )

        batch = operation.collect(market_flash_query())

        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(batch.observations[0].source_operation, "cls_market_flash@1")
        self.assertEqual(len(transport.get_urls), 1)
        self.assertEqual(batch.degradations, ())

    def test_market_flash_primary_failure_uses_fallback_and_preserves_diagnostics(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                json_response({"errno": 0, "data": {"roll_data": []}}),
                response((FIXTURES / "eastmoney_success.json").read_bytes()),
            ]
        )
        operation = FallbackMarketFlashOperation(
            ClsMarketFlashOperation(transport),
            EastmoneyMarketFlashOperation(
                transport,
                trace_factory=lambda: "00000000-0000-4000-8000-000000000016",
                request_gate=DiagnosticRequestGate(),
            ),
        )

        batch = operation.collect(market_flash_query())

        self.assertEqual(batch.source_errors[0].code, "empty_response")
        self.assertEqual(
            batch.observations[0].source_operation, "eastmoney_market_flash@1"
        )
        self.assertEqual(batch.degradations[-1].code, "fallback_used")
        self.assertEqual(len(transport.get_urls), 2)

    def test_market_flash_final_rate_limit_is_retained_after_fallback(self) -> None:
        sleeps: list[float] = []
        transport = FixedTransport(
            [
                json_response({"errno": 0, "data": {"roll_data": []}}),
                TransportError("rate_limited", "The source rate limit was reached."),
                TransportError("rate_limited", "The source rate limit was reached."),
            ]
        )
        no_retry_gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(0.5,),
            sleeper=sleeps.append,
            jitter=lambda lower, upper: lower,
        )
        operation = FallbackMarketFlashOperation(
            ClsMarketFlashOperation(transport),
            EastmoneyMarketFlashOperation(
                transport,
                trace_factory=lambda: "trace",
                request_gate=no_retry_gate,
            ),
        )

        batch = operation.collect(market_flash_query())

        self.assertEqual(
            [failure.code for failure in batch.source_errors],
            ["empty_response", "rate_limited"],
        )
        self.assertEqual(sleeps, [0.5])
        self.assertIn(
            "rate_limit_backoff",
            [degradation.code for degradation in batch.degradations],
        )
        self.assertEqual(batch.degradations[-1].code, "fallback_used")

    def test_cninfo_investor_qa_keeps_question_and_answer_times_and_roles_separate(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                response((FIXTURES / "cninfo_lookup_300058.json").read_bytes()),
                response((FIXTURES / "cninfo_questions_300058.json").read_bytes()),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertEqual(batch.source_errors, ())
        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 3)
        question_one, question_two, answer_two = batch.observations
        self.assertEqual(question_one.source_role, "market_observation")
        self.assertEqual(question_one.source_document_id, "question-Q-1")
        self.assertEqual(question_one.published_at, "2026-07-31T09:09:13+08:00")
        self.assertEqual(question_one.attributes["answer_status"], "unanswered")
        self.assertEqual(question_two.source_role, "market_observation")
        self.assertEqual(question_two.published_at, "2026-07-30T10:00:00+08:00")
        self.assertEqual(answer_two.source_role, "attributed_opinion")
        self.assertEqual(answer_two.source_document_id, "answer-A-2")
        self.assertEqual(answer_two.published_at, "2026-07-31T11:00:00+08:00")
        self.assertEqual(
            answer_two.attributes["observed_update_at"],
            "2026-07-31T11:05:00+08:00",
        )
        self.assertEqual(
            answer_two.subject,
            investor_qa_query().subject,
        )
        self.assertEqual(len(transport.post_calls), 2)
        self.assertEqual(
            transport.post_calls[0],
            (
                "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo",
                b"keyWord=300058",
            ),
        )
        self.assertIn("stockcode=300058", transport.post_calls[1][0])
        self.assertIn("orgId=9900010147", transport.post_calls[1][0])
        self.assertEqual(transport.post_calls[1][1], b"")

    def test_cninfo_investor_qa_accepts_current_stock_type_enum_s(self) -> None:
        transport = FixedTransport(
            [
                json_response(
                    {
                        "code": "",
                        "statusCode": 200,
                        "message": "success",
                        "data": [
                            {
                                "secid": "9900010147",
                                "shortName": "蓝色光标",
                                "stockCode": "300058",
                                "stockType": "S",
                            }
                        ],
                    }
                ),
                response((FIXTURES / "cninfo_questions_300058.json").read_bytes()),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertEqual(batch.source_errors, ())
        self.assertTrue(batch.complete)
        self.assertEqual(len(batch.observations), 3)

    def test_cninfo_investor_qa_rejects_a_wrong_security_payload(self) -> None:
        transport = FixedTransport(
            [
                response((FIXTURES / "cninfo_lookup_300058.json").read_bytes()),
                response(
                    (FIXTURES / "cninfo_questions_wrong_security.json").read_bytes()
                ),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertEqual(batch.observations, ())
        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.source_errors), 1)
        self.assertEqual(batch.source_errors[0].code, "wrong_security_payload")

    def test_cninfo_investor_qa_rejects_an_org_id_identity_conflict(self) -> None:
        transport = FixedTransport(
            [
                response(
                    b'{"data":[{"secid":"WRONG","shortName":"\xe8\x93\x9d\xe8\x89\xb2\xe5\x85\x89\xe6\xa0\x87",'
                    b'"stockCode":"300058","stockType":"A\xe8\x82\xa1"}]}'
                ),
                response(b'{"rows":[],"total":0,"pageNo":1,"pageSize":20}'),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertEqual(batch.observations, ())
        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.source_errors), 1)
        self.assertEqual(batch.source_errors[0].code, "wrong_security_payload")
        self.assertEqual(len(transport.post_calls), 1)

    def test_cninfo_investor_qa_accepts_a_schema_valid_empty_result(self) -> None:
        transport = FixedTransport(
            [
                response((FIXTURES / "cninfo_lookup_300058.json").read_bytes()),
                json_response(
                    {
                        "pageNo": 1,
                        "pageSize": 20,
                        "total": 0,
                        "totalPage": 0,
                        "rows": [],
                    }
                ),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors, ())
        self.assertTrue(batch.complete)

    def test_cninfo_investor_qa_marks_an_uncovered_first_page(self) -> None:
        questions = json.loads(
            (FIXTURES / "cninfo_questions_300058.json").read_text(encoding="utf-8")
        )
        questions["total"] = 65
        transport = FixedTransport(
            [
                response((FIXTURES / "cninfo_lookup_300058.json").read_bytes()),
                json_response(questions),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertFalse(batch.complete)
        self.assertEqual(batch.limitations, ("pagination_incomplete",))

    def test_cninfo_reply_without_its_own_publication_time_is_not_promoted(
        self,
    ) -> None:
        questions = json.loads(
            (FIXTURES / "cninfo_questions_300058.json").read_text(encoding="utf-8")
        )
        questions["rows"] = [questions["rows"][1]]
        questions["total"] = 1
        questions["rows"][0]["attachedPubDate"] = None
        transport = FixedTransport(
            [
                response((FIXTURES / "cninfo_lookup_300058.json").read_bytes()),
                json_response(questions),
            ]
        )

        batch = CninfoInvestorQaOperation(transport).collect(investor_qa_query())

        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(batch.observations[0].source_role, "market_observation")
        self.assertEqual(len(batch.source_errors), 1)
        self.assertEqual(batch.source_errors[0].code, "answer_publication_time_missing")
        self.assertFalse(batch.complete)

    def test_mootdx_f10_missing_dependency_is_explicit_without_installing(
        self,
    ) -> None:
        def missing_client() -> object:
            raise ModuleNotFoundError("No module named 'mootdx'")

        batch = MootdxF10Operation(client_factory=missing_client).collect(
            issuer_profile_query()
        )

        self.assertEqual(batch.observations, ())
        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.source_errors), 1)
        self.assertEqual(batch.source_errors[0].code, "missing_optional_dependency")
        self.assertEqual(batch.source_errors[0].details, {"dependency": "mootdx"})

    def test_mootdx_f10_rejects_invalid_category_sets_before_creating_client(
        self,
    ) -> None:
        client_factory_calls = 0

        def client_factory() -> object:
            nonlocal client_factory_calls
            client_factory_calls += 1
            raise AssertionError("invalid requests must not create an F10 client")

        invalid_categories = (
            [],
            ["公司概况", "公司概况"],
            [
                "最新提示",
                "公司概况",
                "财务分析",
                "股东研究",
                "股本结构",
                "资本运作",
                "业内点评",
                "行业分析",
                "公司大事",
                "公司概况",
            ],
            ["未声明分类"],
        )

        for categories in invalid_categories:
            with self.subTest(categories=categories):
                query = replace(
                    issuer_profile_query(),
                    parameters={"profile_categories": categories},
                )
                batch = MootdxF10Operation(client_factory=client_factory).collect(query)

                self.assertEqual(batch.observations, ())
                self.assertFalse(batch.complete)
                self.assertEqual(batch.source_errors[0].code, "invalid_request")
        self.assertEqual(client_factory_calls, 0)

    def test_mootdx_f10_text_is_preserved_without_inventing_publication_metadata(
        self,
    ) -> None:
        class FixedF10Client:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def F10(self, *, symbol: str, name: str) -> str:  # noqa: N802
                self.calls.append((symbol, name))
                return "公司概况公开聚合文本"

        client = FixedF10Client()

        batch = MootdxF10Operation(
            client_factory=lambda: client,
            clock=lambda: RETRIEVED_AT,
        ).collect(issuer_profile_query())

        self.assertEqual(client.calls, [("300058", "公司概况")])
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations, ())
        self.assertTrue(batch.complete)
        observation = batch.observations[0]
        self.assertEqual(observation.material_type, "issuer_profile")
        self.assertEqual(observation.source_role, "market_observation")
        self.assertIsNone(observation.source_document_id)
        self.assertIsNone(observation.published_at)
        self.assertEqual(observation.retrieved_at, RETRIEVED_AT)
        self.assertEqual(observation.subject, issuer_profile_query().subject)
        self.assertEqual(observation.content, "公司概况公开聚合文本")
        self.assertEqual(
            observation.attributes,
            {
                "category": "公司概况",
                "character_count": 10,
                "sha256": "8bab14d267fc1e281c8cd1a3e094ac676b39f73004266d6279f6aee0d73053c9",
            },
        )
        self.assertEqual(
            observation.limitations,
            (
                "publication_time_unknown",
                "source_document_id_unknown",
                "f10_version_semantics_unverified",
            ),
        )

    def test_f10_adapter_does_not_reinfer_exchange_from_code_prefix(self) -> None:
        query = issuer_profile_query()
        assert query.subject is not None
        subject = {
            **query.subject,
            "security": {
                "exchange": "SSE",
                "code": "300058",
                "type": "A_SHARE",
            },
        }

        class FixedClient:
            def F10(self, *, symbol: str, name: str) -> str:  # noqa: N802
                return "公司概况内容"

        batch = MootdxF10Operation(
            client_factory=FixedClient,
            clock=lambda: RETRIEVED_AT,
        ).collect(replace(query, subject=subject))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)

    def test_mootdx_f10_missing_category_is_explicit_and_incomplete(self) -> None:
        class PartialF10Client:
            def F10(self, *, symbol: str, name: str) -> str:  # noqa: N802
                return "公司概况正文" if name == "公司概况" else ""

        query = replace(
            issuer_profile_query(),
            parameters={"profile_categories": ["公司概况", "公司大事"]},
        )

        batch = MootdxF10Operation(
            client_factory=PartialF10Client,
            clock=lambda: RETRIEVED_AT,
        ).collect(query)

        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(batch.observations[0].attributes["category"], "公司概况")
        self.assertFalse(batch.complete)
        self.assertEqual(len(batch.source_errors), 1)
        self.assertEqual(batch.source_errors[0].code, "empty_response")
        self.assertEqual(batch.source_errors[0].details, {"category": "公司大事"})


if __name__ == "__main__":
    unittest.main()
