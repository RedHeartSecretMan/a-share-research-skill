from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.content_contract import (  # noqa: E402
    ContentObservation,
    ContentQuery,
    SourceBatch,
)
from a_share_research.document_validation import (  # noqa: E402
    DocumentTarget,
    DocumentValidator,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 20, 30, tzinfo=CHINA_STANDARD_TIME)


class FixedDocumentTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.calls.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("document validation must not issue POST requests")


def response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/pdf",
) -> HttpResponse:
    return HttpResponse(
        status=status,
        content_type=content_type,
        body=body,
        retrieved_at=RETRIEVED_AT,
    )


def target() -> DocumentTarget:
    return DocumentTarget(
        material_source_operation="cninfo_announcement@1",
        source_document_id="1225378221",
        locator_uri="https://static.cninfo.com.cn/finalpage/1225378221.PDF",
    )


class DocumentValidatorTests(unittest.TestCase):
    def test_small_complete_pdf_is_verified_with_a_bounded_request(self) -> None:
        transport = FixedDocumentTransport([response(b"%PDF-1.7\nfixture")])

        result = DocumentValidator(transport, max_bytes=64).validate(target())

        self.assertEqual(result.status, "verified")
        self.assertIsNone(result.source_error)
        self.assertIsNone(result.degradation)
        self.assertEqual(result.bytes_received, 16)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]["Range"], "bytes=0-63")

    def test_transport_failure_is_a_document_source_error(self) -> None:
        transport = FixedDocumentTransport(
            [TransportError("upstream_unavailable", "temporary disconnect")]
        )

        result = DocumentValidator(transport, max_bytes=64).validate(target())

        self.assertEqual(result.status, "failed")
        assert result.source_error is not None
        self.assertEqual(result.source_error.code, "document_download_failed")
        self.assertEqual(result.source_error.details["cause"], "upstream_unavailable")

    def test_html_response_is_not_accepted_as_a_pdf(self) -> None:
        result = DocumentValidator(
            FixedDocumentTransport(
                [response(b"<html>denied</html>", content_type="text/html")]
            ),
            max_bytes=64,
        ).validate(target())

        self.assertEqual(result.status, "failed")
        assert result.source_error is not None
        self.assertEqual(result.source_error.code, "document_not_pdf")

    def test_response_larger_than_the_bound_is_rejected(self) -> None:
        result = DocumentValidator(
            FixedDocumentTransport([response(b"%PDF-1.7" + b"x" * 64)]),
            max_bytes=64,
        ).validate(target())

        self.assertEqual(result.status, "failed")
        assert result.source_error is not None
        self.assertEqual(result.source_error.code, "document_too_large")

    def test_partial_content_only_verifies_the_locator_and_prefix(self) -> None:
        result = DocumentValidator(
            FixedDocumentTransport([response(b"%PDF-1.7", status=206)]),
            max_bytes=64,
        ).validate(target())

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.source_error)
        assert result.degradation is not None
        self.assertEqual(result.degradation.code, "document_partially_validated")


class FixedDocumentMaterialOperation:
    operation_id = "fixed_document_material@1"
    supported_material_types = frozenset({"research_report"})

    def __init__(self, document_locator: str | None) -> None:
        self.document_locator = document_locator

    def collect(self, query: ContentQuery) -> SourceBatch:
        return SourceBatch(
            operation_id=self.operation_id,
            observations=(
                ContentObservation(
                    material_type="research_report",
                    source_operation=self.operation_id,
                    source_role="attributed_opinion",
                    source_document_id="REPORT-1",
                    title="主题研究报告",
                    published_at="2026-08-01T09:30:00+08:00",
                    retrieved_at=RETRIEVED_AT,
                    locator_uri="https://example.test/reports/REPORT-1",
                    subject=None,
                    author="研究机构甲",
                    summary=None,
                    document_locator=self.document_locator,
                    attributes={},
                    limitations=(),
                ),
            ),
        )


class DuplicateDocumentMaterialOperation:
    operation_id = "duplicate_document_material@1"
    supported_material_types = frozenset({"research_report"})

    def collect(self, query: ContentQuery) -> SourceBatch:
        observations = []
        for operation, locator in (
            ("source_a@1", "https://example.test/a.pdf"),
            ("source_b@1", "https://example.test/b.pdf"),
        ):
            observations.append(
                ContentObservation(
                    material_type="research_report",
                    source_operation=operation,
                    source_role="attributed_opinion",
                    source_document_id="REPORT-SHARED",
                    source_document_namespace="shared-report-fixture",
                    title="共享研报",
                    published_at="2026-08-01T09:30:00+08:00",
                    retrieved_at=RETRIEVED_AT,
                    locator_uri=locator.removesuffix(".pdf"),
                    subject=None,
                    author="研究机构甲",
                    summary=None,
                    document_locator=locator,
                    attributes={},
                    limitations=(),
                )
            )
        return SourceBatch(
            operation_id=self.operation_id,
            observations=tuple(observations),
        )


def research_request(*, verify_documents: bool | None = None) -> dict[str, object]:
    parameters: dict[str, object] = {
        "material_types": ["research_report"],
        "query": ["主题"],
        "limit": 1,
    }
    if verify_documents is not None:
        parameters["verify_documents"] = verify_documents
    return {
        "schema_version": "1.0",
        "task_type": "research_content",
        "subjects": [],
        "as_of": "2026-08-02",
        "window": {
            "published_from": "2026-07-01",
            "published_to": "2026-08-02",
        },
        "parameters": parameters,
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


class DocumentValidationProcessTests(unittest.TestCase):
    def test_document_verification_is_not_requested_by_default(self) -> None:
        transport = FixedDocumentTransport([])

        result = ResearchRuntime(
            content_operations=(
                FixedDocumentMaterialOperation("https://example.test/REPORT-1.pdf"),
            ),
            content_transport=transport,
        ).research(research_request())

        self.assertEqual(transport.calls, [])
        self.assertEqual(
            result["materials"][0]["document_validation"]["status"],
            "not_requested",
        )

    def test_opt_in_download_failure_is_visible_without_dropping_locator(self) -> None:
        transport = FixedDocumentTransport(
            [TransportError("upstream_unavailable", "temporary disconnect")]
        )

        result = ResearchRuntime(
            content_operations=(
                FixedDocumentMaterialOperation("https://example.test/REPORT-1.pdf"),
            ),
            content_transport=transport,
        ).research(research_request(verify_documents=True))

        material = result["materials"][0]
        self.assertEqual(material["document_validation"]["status"], "failed")
        self.assertEqual(
            material["document_locator"], "https://example.test/REPORT-1.pdf"
        )
        self.assertEqual(
            [error["code"] for error in result["source_errors"]],
            ["document_download_failed"],
        )
        self.assertEqual(len(transport.calls), 1)

    def test_partial_range_validation_is_an_explicit_degradation(self) -> None:
        transport = FixedDocumentTransport([response(b"%PDF-1.7", status=206)])

        result = ResearchRuntime(
            content_operations=(
                FixedDocumentMaterialOperation("https://example.test/REPORT-1.pdf"),
            ),
            content_transport=transport,
        ).research(research_request(verify_documents=True))

        self.assertEqual(
            result["materials"][0]["document_validation"]["status"],
            "partial",
        )
        self.assertEqual(
            [item["code"] for item in result["degradations"]],
            ["document_partially_validated"],
        )

    def test_mixed_duplicate_source_validation_is_only_partial(self) -> None:
        transport = FixedDocumentTransport(
            [
                response(b"%PDF-1.7\nfixture"),
                TransportError("upstream_unavailable", "temporary disconnect"),
            ]
        )

        result = ResearchRuntime(
            content_operations=(DuplicateDocumentMaterialOperation(),),
            content_transport=transport,
        ).research(research_request(verify_documents=True))

        validation = result["materials"][0]["document_validation"]
        self.assertEqual(validation["status"], "partial")
        self.assertEqual(
            {item["status"] for item in validation["sources"]},
            {"verified", "failed"},
        )
        self.assertEqual(
            [error["code"] for error in result["source_errors"]],
            ["document_download_failed"],
        )

    def test_verify_documents_must_be_an_explicit_boolean(self) -> None:
        request = research_request()
        parameters = request["parameters"]
        assert isinstance(parameters, dict)
        parameters["verify_documents"] = "yes"

        with self.assertRaisesRegex(ValueError, "verify_documents"):
            ResearchRuntime(
                content_operations=(
                    FixedDocumentMaterialOperation("https://example.test/REPORT-1.pdf"),
                ),
                content_transport=FixedDocumentTransport([]),
            ).research(request)


if __name__ == "__main__":
    unittest.main()
