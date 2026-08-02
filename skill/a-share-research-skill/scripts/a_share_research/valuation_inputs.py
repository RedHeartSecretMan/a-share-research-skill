"""Determine which provided evidence can become valuation operands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .decimal_math import exact_product, exact_sum

SHARE_BASES = {
    "effective_total_shares",
    "float_shares",
    "period_end_total_shares",
    "rounded_total_shares",
    "estimated_total_shares",
}
PROVIDER_DERIVED_BASES = {
    "provider_market_cap",
    "provider_pe_ttm",
    "provider_pb_mrq",
    "forecast_attributable_profit",
    "provider_ttm_attributable_profit",
    "provider_mrq_equity",
}
PERIODIC_CUMULATIVE_ENDS = {(3, 31), (6, 30), (9, 30)}


@dataclass(frozen=True)
class FinancialObservation:
    item: dict[str, Any]
    value: Decimal
    report_identity: str
    period_start: date
    period_end: date
    period_type: str
    version_id: str
    supersedes: tuple[str, ...]


@dataclass(frozen=True)
class KnownPeriodicReport:
    item: dict[str, Any]
    period_end: date


def _decimal_observation(item: dict[str, Any]) -> Decimal | None:
    observed_value = item.get("observed_value")
    if not isinstance(observed_value, dict) or observed_value.get("unit") != "CNY":
        return None
    try:
        value = Decimal(observed_value.get("value", ""))
        scale = Decimal(observed_value.get("scale", ""))
    except (InvalidOperation, TypeError):
        return None
    if not value.is_finite() or not scale.is_finite() or scale <= 0:
        return None
    return exact_product(value, scale)


def _financial_observation(item: dict[str, Any]) -> FinancialObservation | None:
    if item.get("source_role") != "authoritative_disclosure" or item.get(
        "basis"
    ) not in {"attributable_profit", "attributable_equity"}:
        return None
    value = _decimal_observation(item)
    report = item.get("report")
    if value is None or not isinstance(report, dict):
        return None
    report_identity = report.get("identity")
    period_type = report.get("period_type")
    version = report.get("version")
    if (
        not isinstance(report_identity, str)
        or not report_identity
        or period_type not in {"cumulative", "full_year"}
        or report.get("consolidation_scope") != "consolidated"
        or report.get("attribution_scope") != "owners_of_parent"
        or not isinstance(version, dict)
    ):
        return None
    try:
        period_start = date.fromisoformat(report.get("period_start", ""))
        period_end = date.fromisoformat(report.get("period_end", ""))
    except (TypeError, ValueError):
        return None
    version_id = version.get("id")
    version_type = version.get("type")
    supersedes = version.get("supersedes")
    if (
        not isinstance(version_id, str)
        or not version_id
        or version_type not in {"original", "correction", "supplement", "replacement"}
        or not isinstance(supersedes, list)
        or not all(isinstance(value, str) and value for value in supersedes)
        or (version_type == "original" and supersedes)
        or (version_type != "original" and not supersedes)
        or period_start > period_end
        or item.get("evidence_time") != period_end.isoformat()
    ):
        return None
    if period_start != date(period_end.year, 1, 1):
        return None
    if period_type == "full_year" and (period_end.month, period_end.day) != (12, 31):
        return None
    if (
        period_type == "cumulative"
        and (period_end.month, period_end.day) not in PERIODIC_CUMULATIVE_ENDS
    ):
        return None
    return FinancialObservation(
        item=item,
        value=value,
        report_identity=report_identity,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,
        version_id=version_id,
        supersedes=tuple(supersedes),
    )


def _known_periodic_report(item: dict[str, Any]) -> KnownPeriodicReport | None:
    report = item.get("report")
    if item.get("source_role") != "authoritative_disclosure" or not isinstance(
        report, dict
    ):
        return None
    version = report.get("version")
    if (
        not isinstance(report.get("identity"), str)
        or not report["identity"]
        or report.get("period_type") not in {"cumulative", "full_year"}
        or not isinstance(version, dict)
        or not isinstance(version.get("id"), str)
        or not version["id"]
        or version.get("type")
        not in {"original", "correction", "supplement", "replacement"}
        or not isinstance(version.get("supersedes"), list)
        or not all(
            isinstance(value, str) and value for value in version.get("supersedes", [])
        )
    ):
        return None
    try:
        period_start = date.fromisoformat(report.get("period_start", ""))
        period_end = date.fromisoformat(report.get("period_end", ""))
    except (TypeError, ValueError):
        return None
    period_type = report["period_type"]
    if (
        period_start != date(period_end.year, 1, 1)
        or (
            period_type == "full_year"
            and (period_end.month, period_end.day) != (12, 31)
        )
        or (
            period_type == "cumulative"
            and (period_end.month, period_end.day) not in PERIODIC_CUMULATIVE_ENDS
        )
        or item.get("evidence_time") != period_end.isoformat()
    ):
        return None
    return KnownPeriodicReport(item=item, period_end=period_end)


def _latest_known_reports(
    evidence: list[dict[str, Any]],
) -> list[KnownPeriodicReport]:
    reports = [
        report
        for item in evidence
        if (report := _known_periodic_report(item)) is not None
    ]
    if not reports:
        return []
    latest_period_end = max(report.period_end for report in reports)
    return [report for report in reports if report.period_end == latest_period_end]


def _resolved_report_period(
    observations: list[FinancialObservation],
) -> FinancialObservation | None:
    if not observations:
        return None
    identities = {observation.report_identity for observation in observations}
    if len(identities) != 1:
        return None
    by_version: dict[str, list[FinancialObservation]] = {}
    for observation in observations:
        by_version.setdefault(observation.version_id, []).append(observation)
    if any(
        len(version_observations) != 1 for version_observations in by_version.values()
    ):
        return None
    versions = set(by_version)
    if any(
        not set(version_observations[0].supersedes).issubset(versions)
        for version_observations in by_version.values()
    ):
        return None
    superseded = {
        version_id
        for version_observations in by_version.values()
        for version_id in version_observations[0].supersedes
    }
    heads = versions - superseded
    if len(heads) != 1:
        return None
    head = heads.pop()

    reached: set[str] = set()
    pending = [head]
    while pending:
        version_id = pending.pop()
        if version_id in reached:
            return None
        reached.add(version_id)
        pending.extend(by_version[version_id][0].supersedes)
    if reached != versions:
        return None
    return by_version[head][0]


def _resolved_periods(
    observations: list[FinancialObservation],
) -> dict[tuple[date, date], FinancialObservation | None]:
    periods: dict[tuple[date, date], list[FinancialObservation]] = {}
    for observation in observations:
        periods.setdefault(
            (observation.period_start, observation.period_end), []
        ).append(observation)
    return {
        period: _resolved_report_period(period_observations)
        for period, period_observations in periods.items()
    }


def _denominator_result(evidence_ids: list[str], value: Decimal) -> dict[str, Any]:
    if value <= 0:
        return {
            "status": "no_valuation_meaning",
            "evidence_ids": evidence_ids,
            "denominator_classification": "non_positive",
            "issues": [],
        }
    return {
        "status": "applicable",
        "evidence_ids": evidence_ids,
        "denominator_classification": "positive",
        "issues": [],
    }


def _ttm_applicability(
    evidence: list[dict[str, Any]],
    rejected_evidence: list[dict[str, Any]],
    as_of: str | None,
) -> dict[str, Any]:
    latest_known_reports = _latest_known_reports(evidence)
    profit_items = [
        item for item in evidence if item.get("basis") == "attributable_profit"
    ]
    rejected_profit_items = [
        item for item in rejected_evidence if item.get("basis") == "attributable_profit"
    ]
    if rejected_profit_items:
        return _not_calculable(
            "ttm_attributable_profit_incompatible",
            "Attributable-profit evidence failed bundle or accounting compatibility validation.",
            [item["id"] for item in profit_items + rejected_profit_items],
        )
    if not profit_items:
        if latest_known_reports:
            return _not_calculable(
                "ttm_latest_report_profit_missing",
                "The latest known periodic report does not contain applicable attributable profit.",
                [report.item["id"] for report in latest_known_reports],
            )
        return _not_calculable(
            "ttm_attributable_profit_missing",
            "No reported attributable-profit evidence is available.",
        )
    observations = [
        observation
        for item in profit_items
        if (observation := _financial_observation(item)) is not None
    ]
    try:
        research_date = date.fromisoformat(as_of or "")
    except ValueError:
        research_date = None
    if research_date is not None:
        observations = [
            observation
            for observation in observations
            if observation.period_end <= research_date
        ]
    if len(observations) != len(profit_items):
        return _not_calculable(
            "ttm_attributable_profit_incompatible",
            "Attributable-profit evidence has an incompatible scope, unit, period, or report version.",
            [item["id"] for item in profit_items],
        )
    periods = _resolved_periods(observations)
    if any(observation is None for observation in periods.values()):
        return _not_calculable(
            "unresolved_report_version_relationship",
            "Report versions do not establish one applicable lineage for the affected profit period.",
            [item["id"] for item in profit_items],
        )
    resolved_periods = [
        observation for observation in periods.values() if observation is not None
    ]
    if resolved_periods and latest_known_reports:
        latest_profit_end = max(
            observation.period_end for observation in resolved_periods
        )
        if latest_profit_end < latest_known_reports[0].period_end:
            profit_ids = [item["id"] for item in profit_items]
            return _not_calculable(
                "ttm_latest_report_profit_missing",
                "The latest known periodic report does not contain applicable attributable profit.",
                [
                    *profit_ids,
                    *[
                        report.item["id"]
                        for report in latest_known_reports
                        if report.item["id"] not in set(profit_ids)
                    ],
                ],
            )
    if resolved_periods:
        latest = max(
            resolved_periods,
            key=lambda observation: observation.period_end,
        )
        if latest.period_type == "full_year":
            return _denominator_result([latest.item["id"]], latest.value)
        current = latest
        previous_year = current.period_end.year - 1
        previous_full_year = periods.get(
            (date(previous_year, 1, 1), date(previous_year, 12, 31))
        )
        matching_prior = periods.get(
            (
                date(previous_year, 1, 1),
                date(
                    previous_year,
                    current.period_end.month,
                    current.period_end.day,
                ),
            )
        )
        if previous_full_year is not None and matching_prior is not None:
            return _denominator_result(
                [
                    previous_full_year.item["id"],
                    current.item["id"],
                    matching_prior.item["id"],
                ],
                exact_sum(
                    previous_full_year.value,
                    current.value,
                    matching_prior.value.copy_negate(),
                ),
            )
    return _not_calculable(
        "ttm_report_period_missing",
        "The latest cumulative period lacks the required prior full year or matching prior-year period.",
        [item["id"] for item in profit_items],
    )


def _mrq_applicability(
    evidence: list[dict[str, Any]], rejected_evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    latest_known_reports = _latest_known_reports(evidence)
    equity_items = [
        item for item in evidence if item.get("basis") == "attributable_equity"
    ]
    rejected_equity_items = [
        item for item in rejected_evidence if item.get("basis") == "attributable_equity"
    ]
    if rejected_equity_items:
        return _not_calculable(
            "mrq_attributable_equity_incompatible",
            "Attributable-equity evidence failed bundle or accounting compatibility validation.",
            [item["id"] for item in equity_items + rejected_equity_items],
        )
    if not equity_items:
        return _not_calculable(
            "mrq_attributable_equity_missing",
            "No reported attributable-equity evidence is available.",
        )
    observations = [
        observation
        for item in equity_items
        if (observation := _financial_observation(item)) is not None
    ]
    if len(observations) != len(equity_items):
        return _not_calculable(
            "mrq_attributable_equity_incompatible",
            "Attributable-equity evidence has an incompatible scope, unit, period, or report version.",
            [item["id"] for item in equity_items],
        )
    periods = _resolved_periods(observations)
    if any(observation is None for observation in periods.values()):
        return _not_calculable(
            "unresolved_report_version_relationship",
            "Report versions do not establish one applicable lineage for the affected equity period.",
            [item["id"] for item in equity_items],
        )
    resolved = [
        observation for observation in periods.values() if observation is not None
    ]
    if resolved:
        latest = max(resolved, key=lambda observation: observation.period_end)
        if (
            latest_known_reports
            and latest.period_end < latest_known_reports[0].period_end
        ):
            equity_ids = [item["id"] for item in equity_items]
            return _not_calculable(
                "mrq_latest_report_equity_missing",
                "The latest known periodic report does not contain applicable attributable equity.",
                [
                    *equity_ids,
                    *[
                        report.item["id"]
                        for report in latest_known_reports
                        if report.item["id"] not in set(equity_ids)
                    ],
                ],
            )
        return _denominator_result([latest.item["id"]], latest.value)
    return _not_calculable(
        "mrq_attributable_equity_missing",
        "No applicable periodic attributable-equity report is available.",
        [item["id"] for item in equity_items],
    )


def _not_calculable(
    code: str, message: str, evidence_ids: list[str] | None = None
) -> dict[str, Any]:
    return {
        "status": "not_calculable",
        "evidence_ids": evidence_ids or [],
        "issues": [{"code": code, "message": message}],
    }


def build_valuation_input_applicability(
    evidence: list[dict[str, Any]],
    as_of: str | None = None,
    rejected_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return conservative operand applicability for a valid bundle."""
    rejected_items = rejected_evidence or []
    sessions = [
        item
        for item in evidence
        if item.get("basis") == "latest_completed_trading_session"
        and item.get("source_role") == "market_observation"
        and item.get("observed_value")
        == {"value": "completed", "unit": "trading_session"}
    ]
    closes = [item for item in evidence if item.get("basis") == "unadjusted_close"]
    session_dates = {item.get("evidence_time") for item in sessions}
    matching_closes = [
        item
        for item in closes
        if len(session_dates) == 1 and item.get("evidence_time") in session_dates
    ]
    normalized_close_values: set[Decimal] = set()
    valid_matching_close_count = 0
    for item in matching_closes:
        observed_value = item.get("observed_value")
        if not isinstance(observed_value, dict):
            continue
        try:
            observed = Decimal(observed_value.get("value", ""))
            scale = Decimal(observed_value.get("scale", ""))
        except (InvalidOperation, TypeError):
            continue
        value = exact_product(observed, scale)
        if observed.is_finite() and scale.is_finite() and scale > 0 and value > 0:
            valid_matching_close_count += 1
            normalized_close_values.add(value)
    rejected_price_items = [
        item
        for item in rejected_items
        if item.get("basis") in {"latest_completed_trading_session", "unadjusted_close"}
    ]
    if rejected_price_items:
        common_price = _not_calculable(
            "incompatible_unadjusted_close",
            "Trading-session or close evidence failed bundle compatibility validation.",
            [
                *[item["id"] for item in sessions + closes],
                *[item["id"] for item in rejected_price_items],
            ],
        )
    elif not sessions:
        common_price = _not_calculable(
            "trading_session_evidence_missing",
            "A price cannot establish the common valuation price without trading session evidence.",
            [item["id"] for item in closes],
        )
    elif not closes:
        common_price = _not_calculable(
            "unadjusted_close_missing",
            "No unadjusted close is available for the latest completed trading session.",
            [item["id"] for item in sessions],
        )
    elif matching_closes and valid_matching_close_count != len(matching_closes):
        common_price = _not_calculable(
            "incompatible_unadjusted_close",
            "Every close for the latest completed session requires a positive exact value and scale.",
            [
                *[session["id"] for session in sessions],
                *[close["id"] for close in matching_closes],
            ],
        )
    elif matching_closes and len(normalized_close_values) == 1:
        common_price = {
            "status": "applicable",
            "evidence_ids": [
                *[session["id"] for session in sessions],
                *[close["id"] for close in matching_closes],
            ],
            "issues": [],
        }
    elif matching_closes and len(normalized_close_values) > 1:
        common_price = _not_calculable(
            "conflicting_unadjusted_close",
            "Unadjusted close observations for the latest completed session conflict.",
            [
                *[session["id"] for session in sessions],
                *[close["id"] for close in matching_closes],
            ],
        )
    else:
        common_price = _not_calculable(
            "common_valuation_price_unresolved",
            "Exactly one unadjusted close must match the latest completed trading session.",
            [item["id"] for item in sessions + closes],
        )
    applicable_share_values: list[tuple[dict[str, Any], Decimal]] = []
    share_observations = [item for item in evidence if item.get("basis") in SHARE_BASES]
    share_candidates = [
        item
        for item in share_observations
        if item.get("basis") == "effective_total_shares"
    ]
    for item in share_candidates:
        observed_value = item.get("observed_value")
        if not isinstance(observed_value, dict):
            continue
        try:
            observed = Decimal(observed_value.get("value", ""))
            scale = Decimal(observed_value.get("scale", ""))
            normalized_value = exact_product(
                observed,
                scale,
            )
        except (InvalidOperation, TypeError):
            continue
        if (
            item.get("source_role") != "authoritative_disclosure"
            or observed_value.get("unit") != "shares"
            or normalized_value <= 0
            or normalized_value != normalized_value.to_integral_value()
            or as_of is None
            or not isinstance(item.get("valid_from"), str)
            or not isinstance(item.get("valid_through"), str)
            or not item["valid_from"] <= as_of <= item["valid_through"]
        ):
            continue
        applicable_share_values.append((item, normalized_value))
    distinct_share_values = {value for _, value in applicable_share_values}
    rejected_share_items = [
        item for item in rejected_items if item.get("basis") in SHARE_BASES
    ]
    if rejected_share_items:
        effective_total_shares = _not_calculable(
            "effective_total_shares_incompatible",
            "Total-share evidence failed bundle or effective-period validation.",
            [
                *[item["id"] for item in share_observations],
                *[item["id"] for item in rejected_share_items],
            ],
        )
    elif applicable_share_values and len(distinct_share_values) == 1:
        effective_total_shares = {
            "status": "applicable",
            "evidence_ids": [item["id"] for item, _ in applicable_share_values],
            "issues": [],
        }
    elif applicable_share_values:
        effective_total_shares = _not_calculable(
            "effective_total_shares_conflict",
            "Applicable exact total-share observations conflict.",
            [item["id"] for item, _ in applicable_share_values],
        )
    elif share_observations:
        effective_total_shares = _not_calculable(
            "effective_total_shares_inapplicable",
            "Float, period-end-only, rounded, estimated, or otherwise unproven shares cannot substitute for exact effective total shares.",
            [item["id"] for item in share_observations],
        )
    else:
        effective_total_shares = _not_calculable(
            "effective_total_shares_missing",
            "No exact ordinary total-share count is proven effective at the research boundary.",
            [item["id"] for item in share_candidates],
        )
    return {
        "common_valuation_price": common_price,
        "effective_total_shares": effective_total_shares,
        "ttm_attributable_profit": _ttm_applicability(evidence, rejected_items, as_of),
        "mrq_attributable_equity": _mrq_applicability(evidence, rejected_items),
        "cross_checks": {
            "evidence_ids": [
                item["id"]
                for item in evidence
                if item.get("basis") in PROVIDER_DERIVED_BASES
            ]
        },
    }
