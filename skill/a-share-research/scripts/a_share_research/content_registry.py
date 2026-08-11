"""Default production Adapter registry for research-content tasks."""

from __future__ import annotations

from datetime import datetime

from .communication_sources import (
    ClsMarketFlashOperation,
    CninfoInvestorQaOperation,
    EastmoneyMarketFlashOperation,
    MootdxF10Operation,
)
from .content_contract import ContentHttpTransport, ContentSourceOperation
from .disclosure_sources import (
    CninfoAnnouncementOperation,
    EastmoneyStockNewsOperation,
    SzseAnnouncementOperation,
)
from .report_sources import (
    EastmoneyReportOperation,
    IwencaiContentSearchOperation,
    ThsConsensusMaterialOperation,
)
from .sse_disclosure_sources import SseAnnouncementOperation


def build_default_content_operations(
    transport: ContentHttpTransport,
    *,
    allow_credentials: bool,
    allow_fallback: bool,
    research_now: datetime | None,
) -> tuple[ContentSourceOperation, ...]:
    """Build request-scoped operations without resolving credential values here."""

    operations: list[ContentSourceOperation] = [
        EastmoneyReportOperation(transport),
        ThsConsensusMaterialOperation(transport, research_now=research_now),
        EastmoneyStockNewsOperation(transport),
        CninfoAnnouncementOperation(transport),
        SseAnnouncementOperation(transport),
        SzseAnnouncementOperation(transport),
        ClsMarketFlashOperation(transport),
        CninfoInvestorQaOperation(transport),
        MootdxF10Operation(),
    ]
    if allow_fallback:
        operations.append(EastmoneyMarketFlashOperation(transport))
    if allow_credentials:
        operations.append(
            IwencaiContentSearchOperation(
                transport,
            )
        )
    return tuple(operations)
