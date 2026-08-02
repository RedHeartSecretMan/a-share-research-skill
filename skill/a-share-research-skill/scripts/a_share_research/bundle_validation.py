"""Portable research evidence bundle validation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from .valuation_inputs import (
    PERIODIC_CUMULATIVE_ENDS,
    build_valuation_input_applicability,
)

SOURCE_ROLES = {
    "authoritative_disclosure",
    "market_observation",
    "attributed_opinion",
    "market_signal",
}
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
CHINA_MARKET_CLOSE = time(hour=15)
FINANCIAL_BASES = {"attributable_profit", "attributable_equity"}
PROVIDER_RATIO_LABELS = {
    "provider_pe_ttm": "PE",
    "provider_pb_mrq": "PB",
}


@dataclass(frozen=True)
class JsonNumberToken:
    """A lossless JSON number retained for structured validation diagnostics."""

    text: str


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _json_number_issues(value: Any, path: str = "") -> list[dict[str, str]]:
    if isinstance(value, JsonNumberToken):
        return [
            _issue(
                "invalid_json_number",
                path or "$",
                "JSON numbers must be decimal strings with explicit units",
            )
        ]
    if isinstance(value, dict):
        issues: list[dict[str, str]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            issues.extend(_json_number_issues(child, child_path))
        return issues
    if isinstance(value, list):
        issues = []
        for index, child in enumerate(value):
            issues.extend(_json_number_issues(child, f"{path}[{index}]"))
        return issues
    return []


def _object(
    container: dict[str, Any],
    key: str,
    path: str,
    issues: list[dict[str, str]],
) -> dict[str, Any] | None:
    value = container.get(key)
    if not isinstance(value, dict):
        issues.append(_issue("invalid_object", path, f"{path} must be a JSON object"))
        return None
    return value


def _string(
    container: dict[str, Any],
    key: str,
    path: str,
    issues: list[dict[str, str]],
) -> str | None:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        issues.append(
            _issue(
                "missing_required_field",
                path,
                f"{path} must be a non-empty string",
            )
        )
        return None
    return value


def _date_value(
    container: dict[str, Any],
    key: str,
    path: str,
    issues: list[dict[str, str]],
) -> date | None:
    value = _string(container, key, path, issues)
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        issues.append(_issue("invalid_date", path, f"{path} must use YYYY-MM-DD"))
        return None
    if parsed.isoformat() != value:
        issues.append(_issue("invalid_date", path, f"{path} must use YYYY-MM-DD"))
        return None
    return parsed


def _retrieval_time(
    item: dict[str, Any], path: str, issues: list[dict[str, str]]
) -> datetime | None:
    value = _string(item, "retrieved_at", path, issues)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        issues.append(
            _issue("invalid_datetime", path, f"{path} must be an ISO 8601 datetime")
        )
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        issues.append(
            _issue("missing_utc_offset", path, f"{path} must include a UTC offset")
        )
        return None
    return parsed


def _validate_subject(
    manifest: dict[str, Any], issues: list[dict[str, str]]
) -> tuple[str | None, str | None]:
    subject = _object(manifest, "subject", "subject", issues)
    if subject is None:
        return None, None
    security = _object(subject, "security", "subject.security", issues)
    issuer = _object(subject, "issuer", "subject.issuer", issues)
    canonical_security: str | None = None
    if security is not None:
        exchange = _string(security, "exchange", "subject.security.exchange", issues)
        code = _string(security, "code", "subject.security.code", issues)
        security_type = _string(security, "type", "subject.security.type", issues)
        if exchange is not None and code is not None and security_type is not None:
            if (
                exchange not in {"SSE", "SZSE"}
                or len(code) != 6
                or not code.isascii()
                or not code.isdigit()
                or security_type != "A_SHARE"
            ):
                issues.append(
                    _issue(
                        "invalid_security_identity",
                        "subject.security",
                        "subject.security must be a canonical SSE or SZSE A-share",
                    )
                )
            else:
                canonical_security = f"{exchange}:{code}"
    issuer_name = None
    if issuer is not None:
        issuer_name = _string(issuer, "name", "subject.issuer.name", issues)
    return canonical_security, issuer_name


def _validate_observed_value(
    item: dict[str, Any],
    item_path: str,
    basis: str | None,
    issues: list[dict[str, str]],
) -> str | None:
    observed_value = _object(
        item, "observed_value", f"{item_path}.observed_value", issues
    )
    if observed_value is None:
        return None
    value_path = f"{item_path}.observed_value.value"
    value = _string(observed_value, "value", value_path, issues)
    if value is not None and basis != "latest_completed_trading_session":
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            parsed = None
        if parsed is None or not parsed.is_finite():
            issues.append(
                _issue(
                    "invalid_decimal",
                    value_path,
                    f"{value_path} must be a finite decimal string",
                )
            )
    return _string(
        observed_value,
        "unit",
        f"{item_path}.observed_value.unit",
        issues,
    )


def _validate_locator(
    item: dict[str, Any],
    item_path: str,
    bundle: Path,
    issues: list[dict[str, str]],
) -> None:
    locator_path = f"{item_path}.locator"
    locator = _object(item, "locator", locator_path, issues)
    if locator is None:
        return
    _string(locator, "observation", f"{locator_path}.observation", issues)
    has_uri = "uri" in locator
    has_artifact_path = "path" in locator
    if has_uri and has_artifact_path:
        issues.append(
            _issue(
                "ambiguous_locator",
                locator_path,
                "locator must use either uri or path, not both",
            )
        )
    if not has_uri and not has_artifact_path:
        _string(locator, "uri", f"{locator_path}.uri", issues)
        return
    if has_uri:
        uri_text = _string(locator, "uri", f"{locator_path}.uri", issues)
        if uri_text is not None:
            try:
                parsed_uri = urlsplit(uri_text)
            except ValueError:
                parsed_uri = None
            if parsed_uri is None or (
                not parsed_uri.scheme
                or parsed_uri.scheme.lower() in {"data", "file"}
                or bool(PureWindowsPath(uri_text).drive)
            ):
                issues.append(
                    _issue(
                        "invalid_source_locator",
                        f"{locator_path}.uri",
                        "external source locator must be a non-local absolute URI",
                    )
                )
    if not has_artifact_path:
        return
    path_text = _string(locator, "path", f"{locator_path}.path", issues)
    expected_hash = _string(locator, "sha256", f"{locator_path}.sha256", issues)
    if (
        expected_hash is not None
        and re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
    ):
        issues.append(
            _issue(
                "invalid_sha256",
                f"{locator_path}.sha256",
                "artifact sha256 must contain exactly 64 hexadecimal characters",
            )
        )
        expected_hash = None
    if path_text is None:
        return
    portable_path = PurePosixPath(path_text)
    if (
        "\\" in path_text
        or portable_path.is_absolute()
        or PureWindowsPath(path_text).is_absolute()
        or ".." in portable_path.parts
        or path_text in {"", "."}
    ):
        issues.append(
            _issue(
                "unsafe_artifact_path",
                f"{locator_path}.path",
                "artifact path must be a portable bundle-relative path",
            )
        )
        return
    bundle_root = bundle.resolve()
    try:
        resolved = bundle_root.joinpath(*portable_path.parts).resolve(strict=True)
        resolved.relative_to(bundle_root)
    except (OSError, ValueError):
        issues.append(
            _issue(
                "artifact_unavailable",
                f"{locator_path}.path",
                "artifact is missing or resolves outside the bundle",
            )
        )
        return
    if not resolved.is_file():
        issues.append(
            _issue(
                "artifact_not_file",
                f"{locator_path}.path",
                "artifact path must resolve to a regular file",
            )
        )
        return
    if expected_hash is None:
        return
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        issues.append(
            _issue(
                "artifact_unreadable",
                f"{locator_path}.path",
                "artifact could not be read for SHA-256 verification",
            )
        )
        return
    if digest.hexdigest() != expected_hash.lower():
        issues.append(
            _issue(
                "artifact_hash_mismatch",
                f"{locator_path}.sha256",
                "artifact SHA-256 does not match the manifest",
            )
        )


def _validate_limitations(
    item: dict[str, Any], item_path: str, issues: list[dict[str, str]]
) -> None:
    path = f"{item_path}.limitations"
    value = item.get("limitations")
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry for entry in value
    ):
        issues.append(
            _issue(
                "invalid_string_array",
                path,
                f"{path} must be an array of non-empty strings",
            )
        )


def _validate_positive_decimal_scale(
    item: dict[str, Any], item_path: str, issues: list[dict[str, str]]
) -> None:
    observed_value = item.get("observed_value")
    if not isinstance(observed_value, dict):
        return
    scale_path = f"{item_path}.observed_value.scale"
    scale = _string(observed_value, "scale", scale_path, issues)
    if scale is None:
        return
    try:
        parsed_scale = Decimal(scale)
    except InvalidOperation:
        parsed_scale = None
    if parsed_scale is None or not parsed_scale.is_finite() or parsed_scale <= 0:
        issues.append(
            _issue(
                "invalid_decimal_scale",
                scale_path,
                f"{scale_path} must be a positive finite decimal string",
            )
        )


def _validate_provider_ratio(
    item: dict[str, Any],
    item_path: str,
    source_role: str | None,
    unit: str | None,
    metric_label: str,
    issues: list[dict[str, str]],
) -> None:
    _validate_positive_decimal_scale(item, item_path, issues)
    if source_role is not None and source_role != "market_observation":
        issues.append(
            _issue(
                "incompatible_source_role",
                f"{item_path}.source_role",
                f"provider {metric_label} observations require the "
                "market_observation source role",
            )
        )
    if unit is not None and unit != "ratio":
        issues.append(
            _issue(
                "incompatible_unit",
                f"{item_path}.observed_value.unit",
                f"provider {metric_label} observations require ratio as their unit",
            )
        )


def _validate_financial_report(
    item: dict[str, Any],
    item_path: str,
    source_role: str | None,
    unit: str | None,
    issues: list[dict[str, str]],
) -> None:
    if source_role is not None and source_role != "authoritative_disclosure":
        issues.append(
            _issue(
                "incompatible_source_role",
                f"{item_path}.source_role",
                "valuation financial evidence requires authoritative disclosure",
            )
        )
    if unit is not None and unit != "CNY":
        issues.append(
            _issue(
                "incompatible_unit",
                f"{item_path}.observed_value.unit",
                "valuation financial evidence requires CNY as its unit",
            )
        )
    _validate_positive_decimal_scale(item, item_path, issues)
    report_path = f"{item_path}.report"
    report = _object(item, "report", report_path, issues)
    if report is None:
        return
    _string(report, "identity", f"{report_path}.identity", issues)
    period_start = _date_value(
        report, "period_start", f"{report_path}.period_start", issues
    )
    period_end = _date_value(report, "period_end", f"{report_path}.period_end", issues)
    if (
        period_start is not None
        and period_end is not None
        and period_start > period_end
    ):
        issues.append(
            _issue(
                "invalid_report_period",
                report_path,
                "report period_start must not be later than period_end",
            )
        )
    period_type = _string(report, "period_type", f"{report_path}.period_type", issues)
    if period_type is not None and period_type not in {"cumulative", "full_year"}:
        issues.append(
            _issue(
                "invalid_report_period_type",
                f"{report_path}.period_type",
                "report period_type must be cumulative or full_year",
            )
        )
    if (
        period_start is not None
        and period_end is not None
        and (
            period_start != date(period_end.year, 1, 1)
            or (
                period_type == "full_year"
                and (period_end.month, period_end.day) != (12, 31)
            )
            or (
                period_type == "cumulative"
                and (period_end.month, period_end.day) not in PERIODIC_CUMULATIVE_ENDS
            )
        )
    ):
        issues.append(
            _issue(
                "invalid_report_period",
                report_path,
                "report period must be a compatible calendar-year cumulative or full-year period",
            )
        )
    if (
        period_end is not None
        and isinstance(item.get("evidence_time"), str)
        and item["evidence_time"] != period_end.isoformat()
    ):
        issues.append(
            _issue(
                "report_period_mismatch",
                f"{item_path}.evidence_time",
                "financial evidence_time must equal the report period end",
            )
        )
    consolidation_scope = _string(
        report,
        "consolidation_scope",
        f"{report_path}.consolidation_scope",
        issues,
    )
    if consolidation_scope is not None and consolidation_scope != "consolidated":
        issues.append(
            _issue(
                "incompatible_consolidation_scope",
                f"{report_path}.consolidation_scope",
                "valuation financial evidence must use the consolidated scope",
            )
        )
    attribution_scope = _string(
        report,
        "attribution_scope",
        f"{report_path}.attribution_scope",
        issues,
    )
    if attribution_scope is not None and attribution_scope != "owners_of_parent":
        issues.append(
            _issue(
                "incompatible_attribution_scope",
                f"{report_path}.attribution_scope",
                "valuation financial evidence must be attributable to owners of the parent",
            )
        )
    version_path = f"{report_path}.version"
    version = _object(report, "version", version_path, issues)
    if version is None:
        return
    _string(version, "id", f"{version_path}.id", issues)
    version_type = _string(version, "type", f"{version_path}.type", issues)
    if version_type is not None and version_type not in {
        "original",
        "correction",
        "supplement",
        "replacement",
    }:
        issues.append(
            _issue(
                "invalid_report_version_type",
                f"{version_path}.type",
                "report version type is not recognized",
            )
        )
    supersedes_path = f"{version_path}.supersedes"
    supersedes = version.get("supersedes")
    if not isinstance(supersedes, list) or not all(
        isinstance(value, str) and value for value in supersedes
    ):
        issues.append(
            _issue(
                "invalid_string_array",
                supersedes_path,
                f"{supersedes_path} must be an array of non-empty strings",
            )
        )
    elif version_type == "original" and supersedes:
        issues.append(
            _issue(
                "invalid_report_version_relationship",
                supersedes_path,
                "an original report version cannot supersede another version",
            )
        )
    elif version_type in {"correction", "supplement", "replacement"} and not supersedes:
        issues.append(
            _issue(
                "missing_report_version_relationship",
                supersedes_path,
                "a non-original report version must identify what it supersedes",
            )
        )


def _validate_effective_total_shares(
    item: dict[str, Any],
    item_path: str,
    source_role: str | None,
    unit: str | None,
    research_date: date | None,
    issues: list[dict[str, str]],
) -> None:
    if source_role is not None and source_role != "authoritative_disclosure":
        issues.append(
            _issue(
                "incompatible_source_role",
                f"{item_path}.source_role",
                "effective total shares require the authoritative_disclosure source role",
            )
        )
    if unit is not None and unit != "shares":
        issues.append(
            _issue(
                "incompatible_unit",
                f"{item_path}.observed_value.unit",
                "effective total shares require shares as the unit",
            )
        )
    _validate_positive_decimal_scale(item, item_path, issues)
    valid_from = _date_value(item, "valid_from", f"{item_path}.valid_from", issues)
    valid_through = _date_value(
        item, "valid_through", f"{item_path}.valid_through", issues
    )
    if (
        valid_from is not None
        and valid_through is not None
        and valid_from > valid_through
    ):
        issues.append(
            _issue(
                "invalid_validity_period",
                item_path,
                "share validity period must not end before it begins",
            )
        )
    if (
        research_date is not None
        and valid_from is not None
        and valid_through is not None
        and not valid_from <= research_date <= valid_through
    ):
        issues.append(
            _issue(
                "shares_not_effective_at_research_boundary",
                item_path,
                "total shares are not proven effective at the research boundary",
            )
        )


def _validate_evidence_item(
    item: dict[str, Any],
    index: int,
    canonical_security: str | None,
    issuer_name: str | None,
    research_date: date | None,
    bundle: Path,
    valuation_question: bool,
) -> tuple[str | None, list[dict[str, str]]]:
    item_path = f"evidence[{index}]"
    issues: list[dict[str, str]] = []
    evidence_id = _string(item, "id", f"{item_path}.id", issues)
    source_role = _string(item, "source_role", f"{item_path}.source_role", issues)
    if source_role is not None and source_role not in SOURCE_ROLES:
        issues.append(
            _issue(
                "invalid_source_role",
                f"{item_path}.source_role",
                f"{item_path}.source_role is not recognized",
            )
        )
    _string(
        item,
        "source_operation",
        f"{item_path}.source_operation",
        issues,
    )
    subject = _object(item, "subject", f"{item_path}.subject", issues)
    if subject is not None:
        security = _string(subject, "security", f"{item_path}.subject.security", issues)
        issuer = _string(subject, "issuer", f"{item_path}.subject.issuer", issues)
        if (
            security is not None
            and canonical_security is not None
            and security != canonical_security
        ):
            issues.append(
                _issue(
                    "security_mismatch",
                    f"{item_path}.subject.security",
                    "evidence security does not match bundle subject",
                )
            )
        if issuer is not None and issuer_name is not None and issuer != issuer_name:
            issues.append(
                _issue(
                    "issuer_mismatch",
                    f"{item_path}.subject.issuer",
                    "evidence issuer does not match bundle subject",
                )
            )
    basis = _string(item, "basis", f"{item_path}.basis", issues)
    unit = _validate_observed_value(item, item_path, basis, issues)
    if basis == "latest_completed_trading_session":
        observed_value = item.get("observed_value")
        if source_role is not None and source_role != "market_observation":
            issues.append(
                _issue(
                    "incompatible_source_role",
                    f"{item_path}.source_role",
                    "trading session evidence requires the market_observation source role",
                )
            )
        if isinstance(observed_value, dict) and observed_value != {
            "value": "completed",
            "unit": "trading_session",
        }:
            issues.append(
                _issue(
                    "invalid_trading_session",
                    f"{item_path}.observed_value",
                    "latest completed trading session must be recorded as completed",
                )
            )
    if basis in FINANCIAL_BASES:
        _validate_financial_report(
            item,
            item_path,
            source_role,
            unit,
            issues,
        )
    if basis == "effective_total_shares":
        _validate_effective_total_shares(
            item,
            item_path,
            source_role,
            unit,
            research_date,
            issues,
        )
    if basis == "provider_market_cap":
        _validate_positive_decimal_scale(item, item_path, issues)
        if source_role is not None and source_role != "market_observation":
            issues.append(
                _issue(
                    "incompatible_source_role",
                    f"{item_path}.source_role",
                    "provider market-cap observations require the market_observation source role",
                )
            )
        if unit is not None and unit != "CNY":
            issues.append(
                _issue(
                    "incompatible_unit",
                    f"{item_path}.observed_value.unit",
                    "provider market-cap observations require CNY as their unit",
                )
            )
    if basis in PROVIDER_RATIO_LABELS:
        _validate_provider_ratio(
            item,
            item_path,
            source_role,
            unit,
            PROVIDER_RATIO_LABELS[basis],
            issues,
        )
    if basis == "unadjusted_close":
        if valuation_question:
            _validate_positive_decimal_scale(item, item_path, issues)
        if source_role is not None and source_role != "market_observation":
            issues.append(
                _issue(
                    "incompatible_source_role",
                    f"{item_path}.source_role",
                    "unadjusted_close requires the market_observation source role",
                )
            )
        if unit is not None and unit != "CNY/share":
            issues.append(
                _issue(
                    "incompatible_unit",
                    f"{item_path}.observed_value.unit",
                    "unadjusted_close requires CNY/share as its unit",
                )
            )
    evidence_date = _date_value(
        item, "evidence_time", f"{item_path}.evidence_time", issues
    )
    available_date = _date_value(
        item, "available_at", f"{item_path}.available_at", issues
    )
    retrieved_at = _retrieval_time(item, f"{item_path}.retrieved_at", issues)
    if research_date is not None:
        if evidence_date is not None and evidence_date > research_date:
            issues.append(
                _issue(
                    "evidence_after_research_date",
                    f"{item_path}.evidence_time",
                    "evidence_time is later than the research date",
                )
            )
        if available_date is not None and available_date > research_date:
            issues.append(
                _issue(
                    "publication_after_research_date",
                    f"{item_path}.available_at",
                    "available_at is later than the research date",
                )
            )
    if available_date is not None and retrieved_at is not None:
        retrieval_in_china = retrieved_at.astimezone(CHINA_STANDARD_TIME)
        if available_date > retrieval_in_china.date():
            issues.append(
                _issue(
                    "publication_after_retrieval",
                    f"{item_path}.available_at",
                    "available_at is later than retrieved_at",
                )
            )
        if evidence_date is not None and evidence_date > retrieval_in_china.date():
            issues.append(
                _issue(
                    "evidence_after_retrieval",
                    f"{item_path}.evidence_time",
                    "evidence_time is later than retrieved_at",
                )
            )
        if (
            basis == "unadjusted_close"
            and evidence_date == retrieval_in_china.date()
            and retrieval_in_china.time() < CHINA_MARKET_CLOSE
        ):
            issues.append(
                _issue(
                    "unfinished_trading_session",
                    f"{item_path}.retrieved_at",
                    "same-day unadjusted_close was retrieved before market close",
                )
            )
    _validate_locator(item, item_path, bundle, issues)
    _validate_limitations(item, item_path, issues)
    return evidence_id, issues


def build_bundle_validation_result(
    manifest: dict[str, Any], bundle: Path
) -> dict[str, Any]:
    """Return every independently detectable issue in a bundle manifest."""
    issues = _json_number_issues(manifest)
    if manifest.get("schema_version") != "1.0":
        issues.append(
            _issue(
                "unsupported_schema_version",
                "schema_version",
                "schema_version must be '1.0'",
            )
        )
    research_date = _date_value(manifest, "as_of", "as_of", issues)
    canonical_security, issuer_name = _validate_subject(manifest, issues)
    question = _string(manifest, "question", "question", issues)
    if question is not None and question not in {
        "provided_unadjusted_close",
        "current_valuation",
    }:
        issues.append(
            _issue(
                "unsupported_research_question",
                "question",
                "question must be provided_unadjusted_close or current_valuation",
            )
        )
    boundary_valid = not issues
    evidence_summaries: list[dict[str, Any]] = []
    evidence_items = manifest.get("evidence")
    first_index_by_id: dict[str, int] = {}
    if not isinstance(evidence_items, list):
        issues.append(
            _issue("invalid_array", "evidence", "evidence must be a JSON array")
        )
    else:
        for index, item in enumerate(evidence_items):
            item_path = f"evidence[{index}]"
            if not isinstance(item, dict):
                item_issues = [
                    _issue(
                        "invalid_object",
                        item_path,
                        f"{item_path} must be a JSON object",
                    )
                ]
                evidence_id = None
            else:
                evidence_id, item_issues = _validate_evidence_item(
                    item,
                    index,
                    canonical_security,
                    issuer_name,
                    research_date,
                    bundle,
                    question == "current_valuation",
                )
                if evidence_id is not None:
                    if evidence_id in first_index_by_id:
                        first_index = first_index_by_id[evidence_id]
                        first_summary = evidence_summaries[first_index]
                        if not any(
                            issue["code"] == "duplicate_evidence_id"
                            for issue in first_summary["issues"]
                        ):
                            first_issue = _issue(
                                "duplicate_evidence_id",
                                f"evidence[{first_index}].id",
                                "evidence id must be unique within the bundle",
                            )
                            first_summary["issues"].append(first_issue)
                            first_summary["admissible"] = False
                            issues.append(first_issue)
                        item_issues.append(
                            _issue(
                                "duplicate_evidence_id",
                                f"{item_path}.id",
                                "evidence id must be unique within the bundle",
                            )
                        )
                    else:
                        first_index_by_id[evidence_id] = index
            issues.extend(item_issues)
            evidence_summaries.append(
                {
                    "id": evidence_id,
                    "admissible": boundary_valid and not item_issues,
                    "source_verification": "unverified",
                    "issues": item_issues,
                }
            )
    result = {
        "schema_version": "1.0",
        "validation": {
            "structure": "valid" if not issues else "invalid",
            "source_verification": "unverified",
        },
        "issues": issues,
        "evidence": evidence_summaries,
    }
    if question == "current_valuation":
        id_counts: dict[str, int] = {}
        if isinstance(evidence_items, list):
            for item in evidence_items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    id_counts[item["id"]] = id_counts.get(item["id"], 0) + 1
        admissible_evidence_items: list[dict[str, Any]] = []
        rejected_evidence_items: list[dict[str, Any]] = []
        if isinstance(evidence_items, list):
            for item, summary in zip(evidence_items, evidence_summaries, strict=True):
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not item["id"]
                ):
                    continue
                if summary["admissible"] and id_counts[item["id"]] == 1:
                    admissible_evidence_items.append(item)
                else:
                    rejected_evidence_items.append(item)
        result["valuation_inputs"] = build_valuation_input_applicability(
            admissible_evidence_items,
            manifest.get("as_of"),
            rejected_evidence_items,
        )
    return result
