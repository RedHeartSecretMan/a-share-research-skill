"""Bounded, opt-in validation for source-provided document locators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .content_contract import SourceFailure
from .identity_sources import HttpTransport, TransportError

DOCUMENT_VALIDATION_OPERATION = "document_validation@1"
DEFAULT_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class DocumentTarget:
    """One source locator selected after material deduplication and limiting."""

    material_source_operation: str
    source_document_id: str | None
    locator_uri: str


@dataclass(frozen=True)
class DocumentValidationResult:
    """Visible validation state plus one fail-closed diagnosis, if any."""

    target: DocumentTarget
    status: str
    content_type: str | None = None
    bytes_received: int | None = None
    retrieved_at: datetime | None = None
    source_error: SourceFailure | None = None
    degradation: SourceFailure | None = None

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "validation_operation": DOCUMENT_VALIDATION_OPERATION,
            "source_operation": self.target.material_source_operation,
            "source_document_id": self.target.source_document_id,
        }
        if self.content_type is not None:
            result["content_type"] = self.content_type
        if self.bytes_received is not None:
            result["bytes_received"] = self.bytes_received
        if self.retrieved_at is not None:
            result["retrieved_at"] = self.retrieved_at.isoformat()
        return result


class DocumentValidator:
    """Validate one locator through a bounded GET without parsing its contents."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        max_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
    ) -> None:
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 8
        ):
            raise ValueError("max_bytes must be an integer of at least 8")
        self._transport = transport
        self._max_bytes = max_bytes

    def validate(self, target: DocumentTarget) -> DocumentValidationResult:
        try:
            response = self._transport.get(
                target.locator_uri,
                {
                    "Accept": "application/pdf",
                    "Range": f"bytes=0-{self._max_bytes - 1}",
                    "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
                },
            )
        except TransportError as error:
            code = (
                "document_too_large"
                if error.code == "response_too_large"
                else "document_download_failed"
            )
            return self._failed(
                target,
                code,
                "The source-provided document could not be downloaded safely.",
                cause=error.code,
            )
        if response.status not in {200, 206}:
            return self._failed(
                target,
                "document_download_failed",
                f"The document source returned HTTP status {response.status}.",
                cause="upstream_http_error",
                http_status=response.status,
            )
        media_type = response.content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/pdf":
            return self._failed(
                target,
                "document_not_pdf",
                "The source-provided document is not an application/pdf response.",
                cause="unexpected_content_type",
                content_type=media_type,
                bytes_received=len(response.body),
                retrieved_at=response.retrieved_at,
            )
        if len(response.body) > self._max_bytes:
            return self._failed(
                target,
                "document_too_large",
                "The source-provided document exceeds the validation byte bound.",
                cause="response_too_large",
                content_type=media_type,
                bytes_received=len(response.body),
                retrieved_at=response.retrieved_at,
            )
        if not response.body.startswith(b"%PDF-"):
            return self._failed(
                target,
                "invalid_pdf_document",
                "The source response lacks a PDF document signature.",
                cause="invalid_document_signature",
                content_type=media_type,
                bytes_received=len(response.body),
                retrieved_at=response.retrieved_at,
            )
        if response.status == 206:
            degradation = SourceFailure(
                source_operation=DOCUMENT_VALIDATION_OPERATION,
                code="document_partially_validated",
                message=(
                    "Only a bounded PDF prefix was returned; the full document "
                    "content was not validated."
                ),
                details=self._details(target),
            )
            return DocumentValidationResult(
                target=target,
                status="partial",
                content_type=media_type,
                bytes_received=len(response.body),
                retrieved_at=response.retrieved_at,
                degradation=degradation,
            )
        return DocumentValidationResult(
            target=target,
            status="verified",
            content_type=media_type,
            bytes_received=len(response.body),
            retrieved_at=response.retrieved_at,
        )

    def _failed(
        self,
        target: DocumentTarget,
        code: str,
        message: str,
        *,
        cause: str,
        http_status: int | None = None,
        content_type: str | None = None,
        bytes_received: int | None = None,
        retrieved_at: datetime | None = None,
    ) -> DocumentValidationResult:
        details = {**self._details(target), "cause": cause}
        if http_status is not None:
            details["http_status"] = http_status
        failure = SourceFailure(
            source_operation=DOCUMENT_VALIDATION_OPERATION,
            code=code,
            message=message,
            details=details,
        )
        return DocumentValidationResult(
            target=target,
            status="failed",
            content_type=content_type,
            bytes_received=bytes_received,
            retrieved_at=retrieved_at,
            source_error=failure,
        )

    @staticmethod
    def _details(target: DocumentTarget) -> dict[str, Any]:
        return {
            "material_source_operation": target.material_source_operation,
            "source_document_id": target.source_document_id,
        }
