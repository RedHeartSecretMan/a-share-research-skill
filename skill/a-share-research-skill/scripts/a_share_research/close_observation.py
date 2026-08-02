"""Cross-check experimental daily close observations at the CLI boundary."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from .close_sources import (
    CloseObservation,
    SseDailyLineOperation,
    SzseDailyLineOperation,
    TencentDailyLineOperation,
)
from .identity_sources import CHINA_STANDARD_TIME, HttpTransport, SourceOperationError


def _source_error(error: SourceOperationError) -> dict[str, str]:
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": str(error),
    }


def build_close_result(
    security: str,
    as_of: str,
    transport: HttpTransport,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return a common close only when source identity, date, basis and value agree."""

    research_now = now or datetime.now(CHINA_STANDARD_TIME)
    research_date = date.fromisoformat(as_of)
    research = {
        "security": security,
        "as_of": as_of,
        "timezone": "Asia/Shanghai",
        "retrieved_at": research_now.isoformat(),
    }
    if research_date > research_now.date():
        return {
            "schema_version": "1.0",
            "status": "blocked",
            "research": research,
            "latest_completed_session": None,
            "close": {"status": "unresolved"},
            "evidence": [],
            "session_evidence": [],
            "rejected_observations": [],
            "conflicts": [],
            "source_errors": [],
            "limitations": [
                {
                    "code": "future_research_date",
                    "message": "The research date is later than the retrieval date.",
                }
            ],
        }
    official_operation = (
        SseDailyLineOperation()
        if security.startswith("SSE:")
        else SzseDailyLineOperation()
    )
    source_errors = []
    try:
        official_observations = official_operation.observe(security, transport)
    except SourceOperationError as error:
        official_observations = []
        source_errors.append(_source_error(error))
    try:
        tencent_observations = TencentDailyLineOperation().observe(
            security, research_date, transport
        )
    except SourceOperationError as error:
        tencent_observations = []
        source_errors.append(_source_error(error))
    research_boundary = (
        datetime.combine(
            research_date,
            time.max,
            tzinfo=CHINA_STANDARD_TIME,
        )
        if research_date < research_now.date()
        else research_now
    )
    current_session_unfinished = (
        research_date == research_now.date() and research_now.time() < time(15, 0)
    )

    def applicable(observation: CloseObservation) -> bool:
        return (
            observation.trading_date <= research_date
            and observation.available_at <= research_boundary
            and observation.price_type == "close"
            and observation.trading_status == "traded"
            and not (
                current_session_unfinished and observation.trading_date == research_date
            )
        )

    official = max(
        (item for item in official_observations if applicable(item)),
        key=lambda item: item.trading_date,
        default=None,
    )
    tencent = max(
        (item for item in tencent_observations if applicable(item)),
        key=lambda item: item.trading_date,
        default=None,
    )
    diagnostics = [
        item
        for item in tencent_observations
        if item.trading_date == research_date
        and (item.price_type != "close" or item.trading_status != "traded")
    ]
    evidence = []
    if official is not None:
        evidence.append(official.to_evidence())
    if tencent is not None:
        evidence.append(tencent.to_evidence())
    session_evidence = [official.to_session_evidence()] if official is not None else []
    rejected_observations = [item.to_evidence() for item in diagnostics]
    conflicts = []
    suspended = next(
        (item for item in diagnostics if item.trading_status == "suspended"),
        None,
    )
    if suspended is not None and official is not None:
        conflicts.append(
            {
                "code": "close_date_conflict",
                "classification": "security_suspended",
                "message": (
                    f"Tencent reports {security} as suspended on the research date."
                ),
                "evidence_ids": [official.evidence_id, suspended.evidence_id],
            }
        )
    if (
        official is not None
        and tencent is not None
        and tencent.security == official.security
        and tencent.trading_date != official.trading_date
    ):
        conflicts.append(
            {
                "code": "close_date_conflict",
                "classification": (
                    "stale_tencent_daily_line"
                    if tencent.trading_date < official.trading_date
                    else "stale_exchange_daily_line"
                ),
                "message": (
                    "Sources disagree on the trading date for the latest "
                    f"completed session of {security}."
                ),
                "evidence_ids": [official.evidence_id, tencent.evidence_id],
            }
        )
    if (
        official is not None
        and tencent is not None
        and tencent.security == official.security
        and tencent.trading_date == official.trading_date
        and Decimal(tencent.value) != Decimal(official.value)
    ):
        conflicts.append(
            {
                "code": "close_price_conflict",
                "message": (
                    "Sources disagree on the unadjusted close for "
                    f"{security} on {official.trading_date.isoformat()}."
                ),
                "evidence_ids": [official.evidence_id, tencent.evidence_id],
            }
        )
    matching = (
        official is not None
        and tencent is not None
        and tencent.security == official.security
        and tencent.trading_date == official.trading_date
        and Decimal(tencent.value) == Decimal(official.value)
        and not conflicts
        and not source_errors
    )
    if matching:
        assert official is not None
        assert tencent is not None
        status = "limited"
        latest_session: dict[str, object] | None = {
            "trading_date": official.trading_date.isoformat(),
            "status": "completed",
            "evidence_ids": [session_evidence[0]["id"]],
        }
        close = {
            "status": "cross_checked_experimental",
            "trading_date": official.trading_date.isoformat(),
            "value": tencent.value,
            "unit": "CNY/share",
            "evidence_ids": [official.evidence_id, tencent.evidence_id],
        }
        limitations = [
            {
                "code": "experimental_close_sources",
                "message": (
                    "The close agrees across experimental source operations but "
                    "the operations have not completed source qualification."
                ),
            }
        ]
        if diagnostics and all(
            item.price_type == "intraday_last" for item in diagnostics
        ):
            limitations.append(
                {
                    "code": "unfinished_current_session_ignored",
                    "message": (
                        "The current intraday observation was retained but not "
                        "used as a completed close."
                    ),
                }
            )
    else:
        status = "blocked"
        latest_session = (
            {
                "trading_date": official.trading_date.isoformat(),
                "status": "completed",
                "evidence_ids": [session_evidence[0]["id"]],
            }
            if official is not None
            else None
        )
        close = {"status": "unresolved"}
        limitations = [
            {
                "code": "close_not_cross_checked",
                "message": (
                    "The available observations do not establish one common "
                    "unadjusted close."
                ),
            }
        ]
    return {
        "schema_version": "1.0",
        "status": status,
        "research": research,
        "latest_completed_session": latest_session,
        "close": close,
        "evidence": evidence,
        "session_evidence": session_evidence,
        "rejected_observations": rejected_observations,
        "conflicts": conflicts,
        "source_errors": source_errors,
        "limitations": limitations,
    }
