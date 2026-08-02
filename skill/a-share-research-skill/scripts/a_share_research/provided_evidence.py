"""Research results built from caller-provided evidence."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

SOURCE_ROLES = {
    "authoritative_disclosure",
    "market_observation",
    "attributed_opinion",
    "market_signal",
}
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
CHINA_MARKET_CLOSE = time(hour=15)


def _require_object(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"bundle {key} must be a JSON object")
    return value


def _require_nonempty_string(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"bundle {key} must be a non-empty string")
    return value


def _require_date(container: dict[str, Any], key: str) -> date:
    value = _require_nonempty_string(container, key)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"bundle {key} must use YYYY-MM-DD format") from error
    if parsed.isoformat() != value:
        raise ValueError(f"bundle {key} must use YYYY-MM-DD format")
    return parsed


def _require_retrieval_time(container: dict[str, Any]) -> datetime:
    value = _require_nonempty_string(container, "retrieved_at")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("bundle retrieved_at must be an ISO 8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("bundle retrieved_at must include a UTC offset")
    return parsed


def _require_string_list(container: dict[str, Any], key: str) -> None:
    value = container.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"bundle {key} must be an array of non-empty strings")


def _validate_evidence_items(
    evidence_items: Any,
    canonical_security: str,
    issuer_name: str,
    research_date: date,
) -> list[dict[str, Any]]:
    if not isinstance(evidence_items, list):
        raise ValueError("evidence must be a JSON array")
    validated_items: list[dict[str, Any]] = []
    for item in evidence_items:
        if not isinstance(item, dict):
            raise ValueError("each evidence item must be a JSON object")
        _require_nonempty_string(item, "id")
        source_role = _require_nonempty_string(item, "source_role")
        if source_role not in SOURCE_ROLES:
            raise ValueError("bundle source_role is not a recognized source role")
        _require_nonempty_string(item, "source_operation")
        evidence_subject = _require_object(item, "subject")
        if _require_nonempty_string(evidence_subject, "security") != canonical_security:
            raise ValueError("evidence security does not match bundle subject")
        if _require_nonempty_string(evidence_subject, "issuer") != issuer_name:
            raise ValueError("evidence issuer does not match bundle subject")
        observed_value = _require_object(item, "observed_value")
        value = observed_value.get("value")
        unit = observed_value.get("unit")
        if not isinstance(value, str):
            raise ValueError("evidence value must be an exact decimal string")
        if not isinstance(unit, str) or not unit:
            raise ValueError("evidence value must have an explicit unit")
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(
                "evidence value must be an exact decimal string"
            ) from error
        if not decimal_value.is_finite():
            raise ValueError("evidence value must be a finite decimal string")
        basis = _require_nonempty_string(item, "basis")
        if basis == "unadjusted_close":
            if source_role != "market_observation":
                raise ValueError(
                    "unadjusted_close requires the market_observation source role"
                )
            if unit != "CNY/share":
                raise ValueError("unadjusted_close requires CNY/share as its unit")
        evidence_date = _require_date(item, "evidence_time")
        if evidence_date > research_date:
            raise ValueError("evidence_time is later than the research date")
        available_date = _require_date(item, "available_at")
        if available_date > research_date:
            raise ValueError("available_at is later than the research date")
        retrieved_at = _require_retrieval_time(item).astimezone(CHINA_STANDARD_TIME)
        if available_date > retrieved_at.date():
            raise ValueError("available_at is later than retrieved_at")
        if (
            basis == "unadjusted_close"
            and evidence_date == retrieved_at.date()
            and retrieved_at.time() < CHINA_MARKET_CLOSE
        ):
            raise ValueError(
                "same-day unadjusted_close was retrieved before the China market close"
            )
        locator = _require_object(item, "locator")
        _require_nonempty_string(locator, "uri")
        _require_nonempty_string(locator, "observation")
        _require_string_list(item, "limitations")
        validated_items.append(item)
    return validated_items


def build_provided_evidence_result(
    manifest: dict[str, Any], as_of: str
) -> dict[str, Any]:
    if manifest.get("schema_version") != "1.0":
        raise ValueError("bundle schema_version must be '1.0'")
    if manifest.get("as_of") != as_of:
        raise ValueError("CLI research date does not match bundle as_of")
    subject = _require_object(manifest, "subject")
    security = _require_object(subject, "security")
    issuer = _require_object(subject, "issuer")
    exchange = _require_nonempty_string(security, "exchange")
    code = _require_nonempty_string(security, "code")
    security_type = _require_nonempty_string(security, "type")
    if (
        exchange not in {"SSE", "SZSE"}
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
        or security_type != "A_SHARE"
    ):
        raise ValueError("bundle security must be a canonical SSE or SZSE A-share")
    issuer_name = _require_nonempty_string(issuer, "name")
    question = _require_nonempty_string(manifest, "question")
    if question != "provided_unadjusted_close":
        raise ValueError("bundle question must be provided_unadjusted_close")
    canonical_security = f"{exchange}:{code}"
    research_date = date.fromisoformat(as_of)
    evidence = _validate_evidence_items(
        manifest.get("evidence"),
        canonical_security=canonical_security,
        issuer_name=issuer_name,
        research_date=research_date,
    )
    answers_supported_question = any(
        item.get("basis") == "unadjusted_close" for item in evidence
    )
    if answers_supported_question:
        status = "limited"
        limitations = [
            {
                "code": "provided_evidence_source_unverified",
                "message": (
                    "Caller-provided evidence has not been independently source "
                    "verified; the research result cannot be supported."
                ),
            }
        ]
    else:
        status = "blocked"
        limitations = [
            {
                "code": "no_admissible_evidence",
                "message": "No evidence is available to answer the research question.",
            }
        ]
    return {
        "schema_version": "1.0",
        "status": status,
        "research": {
            "security": canonical_security,
            "issuer": issuer,
            "as_of": as_of,
            "timezone": "Asia/Shanghai",
            "question": question,
        },
        "evidence": evidence,
        "limitations": limitations,
    }
