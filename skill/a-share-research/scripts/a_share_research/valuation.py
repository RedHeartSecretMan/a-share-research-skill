"""Deterministic valuation results built from a validated evidence bundle."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from .bundle_validation import build_bundle_validation_result
from .decimal_math import decimal_ratio, exact_difference, exact_product, exact_sum


@dataclass(frozen=True)
class _ProviderCrossCheckPolicy:
    basis: str
    unit: str
    project_unavailable_explanation: str
    time_mismatch_explanation: str
    methodology_explanation: str
    methodology_comparable: bool
    preserve_provider_limitations: bool


_PROVIDER_CROSS_CHECK_POLICIES = {
    "market_capitalization": _ProviderCrossCheckPolicy(
        basis="provider_market_cap",
        unit="CNY",
        project_unavailable_explanation=(
            "No project market capitalization is available; the provider "
            "observation cannot replace missing or inapplicable project operands."
        ),
        time_mismatch_explanation=(
            "The provider market-cap observation date does not match the common "
            "valuation price date, so no project difference is calculated."
        ),
        methodology_explanation=(
            "The provider market-cap observation is retained only as a "
            "comparison candidate; source independence is unverified, and it "
            "does not replace the project calculation."
        ),
        methodology_comparable=True,
        preserve_provider_limitations=False,
    ),
    "pe_ttm": _ProviderCrossCheckPolicy(
        basis="provider_pe_ttm",
        unit="ratio",
        project_unavailable_explanation=(
            "No project PE TTM is available; the provider observation cannot "
            "replace missing, incompatible, or non-positive project operands."
        ),
        time_mismatch_explanation=(
            "The provider PE observation date does not match the common valuation "
            "price date, so no project difference is calculated."
        ),
        methodology_explanation=(
            "The provider PE observation is retained as a separate "
            "cross-check candidate, but its TTM and profit-scope methodology "
            "is not verified as comparable and it cannot replace the project "
            "calculation."
        ),
        methodology_comparable=False,
        preserve_provider_limitations=True,
    ),
    "pb_mrq": _ProviderCrossCheckPolicy(
        basis="provider_pb_mrq",
        unit="ratio",
        project_unavailable_explanation=(
            "No project PB MRQ is available; the provider observation cannot "
            "replace missing, incompatible, or non-positive project operands."
        ),
        time_mismatch_explanation=(
            "The provider PB observation date does not match the common valuation "
            "price date, so no project difference is calculated."
        ),
        methodology_explanation=(
            "The provider PB observation is retained as a separate cross-check "
            "candidate, but its equity-scope methodology is not verified as "
            "comparable and it cannot replace the project calculation."
        ),
        methodology_comparable=False,
        preserve_provider_limitations=True,
    ),
}


def _normalized_decimal(item: dict[str, Any]) -> Decimal:
    observed_value = item["observed_value"]
    return exact_product(
        Decimal(observed_value["value"]),
        Decimal(observed_value["scale"]),
    )


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")


def _canonical_security(
    manifest: dict[str, Any], validation: dict[str, Any]
) -> str | None:
    if any(
        issue["path"].startswith("subject.security") for issue in validation["issues"]
    ):
        return None
    subject = manifest.get("subject")
    if not isinstance(subject, dict):
        return None
    security = subject.get("security")
    if not isinstance(security, dict):
        return None
    return f"{security['exchange']}:{security['code']}"


def _result_limitations(
    validation: dict[str, Any], *metrics: dict[str, Any]
) -> list[dict[str, str]]:
    limitations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    metric_issues = [issue for metric in metrics for issue in metric["issues"]]
    for issue in [*validation["issues"], *metric_issues]:
        key = (issue["code"], issue["message"])
        if key in seen:
            continue
        seen.add(key)
        limitations.append({"code": issue["code"], "message": issue["message"]})
    limitations.append(
        {
            "code": "provided_evidence_source_unverified",
            "message": (
                "Caller-provided evidence has not been independently source "
                "verified; the research result cannot be supported."
            ),
        }
    )
    return limitations


def _lineage_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": item["id"],
        "basis": item["basis"],
        "evidence_time": item["evidence_time"],
        "observed_value": item["observed_value"],
    }


def _evidence_partitions(
    evidence: list[Any], validation: dict[str, Any]
) -> tuple[list[Any], list[dict[str, Any]]]:
    available: list[Any] = []
    unavailable: list[dict[str, Any]] = []
    for item, summary in zip(evidence, validation["evidence"], strict=True):
        if summary["admissible"]:
            available.append(item)
        else:
            unavailable.append({"evidence": item, "issues": summary["issues"]})
    return available, unavailable


def _provider_observation(
    item: dict[str, Any], normalized_value: Decimal, unit: str
) -> dict[str, Any]:
    return {
        "evidence_id": item["id"],
        "role": "provider_observation",
        "source_independence": "unverified",
        "evidence_time": item["evidence_time"],
        "observed_value": item["observed_value"],
        "normalized_value": {
            "value": _decimal_string(normalized_value),
            "unit": unit,
        },
    }


def _provider_cross_checks(
    evidence_by_id: dict[str, dict[str, Any]],
    evidence_ids: list[str],
    project_value: Decimal | None,
    project_evidence_time: str | None,
    metric: Literal["market_capitalization", "pe_ttm", "pb_mrq"],
) -> list[dict[str, Any]]:
    policy = _PROVIDER_CROSS_CHECK_POLICIES[metric]
    cross_checks = []
    for evidence_id in evidence_ids:
        item = evidence_by_id[evidence_id]
        if item["basis"] != policy.basis:
            continue
        observed_value = _normalized_decimal(item)
        if project_value is None:
            difference = None
            comparability = "not_comparable"
            incomparability_reason = "project_value_unavailable"
            explanation = policy.project_unavailable_explanation
        elif item["evidence_time"] != project_evidence_time:
            difference = None
            comparability = "not_comparable"
            incomparability_reason = "valuation_time_mismatch"
            explanation = policy.time_mismatch_explanation
        elif policy.methodology_comparable:
            comparability = "comparable"
            incomparability_reason = None
            difference = {
                "value": _decimal_string(
                    exact_difference(project_value, observed_value)
                ),
                "unit": policy.unit,
                "meaning": "project_calculation_minus_provider_observation",
            }
            explanation = policy.methodology_explanation
        else:
            difference = None
            comparability = "not_comparable"
            incomparability_reason = "provider_methodology_unverified"
            explanation = policy.methodology_explanation
        cross_check = {
            **_provider_observation(item, observed_value, policy.unit),
            "comparability": comparability,
            "difference": difference,
            "explanation": explanation,
            **(
                {"incomparability_reason": incomparability_reason}
                if incomparability_reason is not None
                else {}
            ),
        }
        if policy.preserve_provider_limitations:
            cross_check["provider_limitations"] = item["limitations"]
        cross_checks.append(cross_check)
    return cross_checks


def _market_capitalization(
    evidence: list[dict[str, Any]],
    valuation_inputs: dict[str, Any],
) -> dict[str, Any]:
    price_input = valuation_inputs["common_valuation_price"]
    shares_input = valuation_inputs["effective_total_shares"]
    by_id = {
        item["id"]: item
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if price_input["status"] != "applicable" or shares_input["status"] != "applicable":
        return {
            "status": "not_calculable",
            "value": None,
            "calculation": None,
            "cross_checks": _provider_cross_checks(
                by_id,
                valuation_inputs["cross_checks"]["evidence_ids"],
                None,
                None,
                "market_capitalization",
            ),
            "issues": [*price_input["issues"], *shares_input["issues"]],
        }

    price_evidence = [by_id[evidence_id] for evidence_id in price_input["evidence_ids"]]
    shares_evidence = [
        by_id[evidence_id] for evidence_id in shares_input["evidence_ids"]
    ]
    close = next(item for item in price_evidence if item["basis"] == "unadjusted_close")
    shares = shares_evidence[0]
    normalized_price = _normalized_decimal(close)
    normalized_shares = _normalized_decimal(shares)
    market_capitalization = exact_product(normalized_price, normalized_shares)
    cross_checks = _provider_cross_checks(
        by_id,
        valuation_inputs["cross_checks"]["evidence_ids"],
        market_capitalization,
        close["evidence_time"],
        "market_capitalization",
    )
    return {
        "status": "supported",
        "value": {
            "value": _decimal_string(market_capitalization),
            "unit": "CNY",
        },
        "calculation": {
            "formula": "common_valuation_price * effective_total_shares",
            "unit_conversion": "CNY/share * shares = CNY",
            "operands": {
                "common_valuation_price": {
                    "value": {
                        "value": _decimal_string(normalized_price),
                        "unit": "CNY/share",
                    },
                    "evidence": [_lineage_item(item) for item in price_evidence],
                },
                "effective_total_shares": {
                    "value": {
                        "value": _decimal_string(normalized_shares),
                        "unit": "shares",
                    },
                    "evidence": [_lineage_item(item) for item in shares_evidence],
                },
            },
        },
        "cross_checks": cross_checks,
        "issues": [],
    }


def _ttm_profit_operand(
    evidence_by_id: dict[str, dict[str, Any]], evidence_ids: list[str]
) -> tuple[Decimal, dict[str, Any]]:
    profit_evidence = [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
    normalized_values = [_normalized_decimal(item) for item in profit_evidence]
    if len(normalized_values) == 1:
        period_method = "full_year"
        ttm_profit = normalized_values[0]
        component_roles = [("full_year", "direct")]
    else:
        period_method = "previous_full_year_plus_latest_cumulative_minus_matching_prior"
        ttm_profit = exact_sum(
            normalized_values[0],
            normalized_values[1],
            normalized_values[2].copy_negate(),
        )
        component_roles = [
            ("previous_full_year", "add"),
            ("latest_current_year_cumulative", "add"),
            ("matching_prior_year_cumulative", "subtract"),
        ]
    return ttm_profit, {
        "value": {"value": _decimal_string(ttm_profit), "unit": "CNY"},
        "period_method": period_method,
        "components": [
            {
                "role": role,
                "operation": operation,
                "normalized_value": {
                    "value": _decimal_string(value),
                    "unit": "CNY",
                },
                "evidence_id": item["id"],
            }
            for item, value, (role, operation) in zip(
                profit_evidence,
                normalized_values,
                component_roles,
                strict=True,
            )
        ],
        "evidence": [_lineage_item(item) for item in profit_evidence],
    }


def _common_valuation_time(
    evidence_by_id: dict[str, dict[str, Any]], valuation_inputs: dict[str, Any]
) -> str | None:
    price_input = valuation_inputs["common_valuation_price"]
    if price_input["status"] != "applicable":
        return None
    return next(
        evidence_by_id[evidence_id]["evidence_time"]
        for evidence_id in price_input["evidence_ids"]
        if evidence_by_id[evidence_id]["basis"] == "unadjusted_close"
    )


def _pe_ttm(
    evidence: list[dict[str, Any]],
    valuation_inputs: dict[str, Any],
    market_capitalization: dict[str, Any],
) -> dict[str, Any]:
    profit_input = valuation_inputs["ttm_attributable_profit"]
    evidence_by_id = {
        item["id"]: item
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    project_evidence_time = _common_valuation_time(evidence_by_id, valuation_inputs)
    if (
        market_capitalization["status"] != "supported"
        or profit_input["status"] == "not_calculable"
    ):
        return {
            "status": "not_calculable",
            "value": None,
            "calculation": None,
            "cross_checks": _provider_cross_checks(
                evidence_by_id,
                valuation_inputs["cross_checks"]["evidence_ids"],
                None,
                project_evidence_time,
                "pe_ttm",
            ),
            "issues": [*market_capitalization["issues"], *profit_input["issues"]],
        }

    ttm_profit, profit_operand = _ttm_profit_operand(
        evidence_by_id, profit_input["evidence_ids"]
    )
    calculation = {
        "formula": "market_capitalization / ttm_attributable_profit",
        "unit_conversion": "CNY / CNY = ratio",
        "precision": {
            "significant_digits": 28,
            "rounding": "ROUND_HALF_EVEN",
        },
        "operands": {
            "market_capitalization": {
                "value": market_capitalization["value"],
                "source_metric": "market_capitalization",
            },
            "ttm_attributable_profit": profit_operand,
        },
    }
    if profit_input["status"] == "no_valuation_meaning":
        return {
            "status": "no_valuation_meaning",
            "value": None,
            "calculation": calculation,
            "cross_checks": _provider_cross_checks(
                evidence_by_id,
                valuation_inputs["cross_checks"]["evidence_ids"],
                None,
                project_evidence_time,
                "pe_ttm",
            ),
            "issues": [],
        }
    market_cap_value = Decimal(market_capitalization["value"]["value"])
    pe_value = decimal_ratio(market_cap_value, ttm_profit)
    return {
        "status": "supported",
        "value": {
            "value": _decimal_string(pe_value),
            "unit": "ratio",
        },
        "calculation": calculation,
        "cross_checks": _provider_cross_checks(
            evidence_by_id,
            valuation_inputs["cross_checks"]["evidence_ids"],
            pe_value,
            project_evidence_time,
            "pe_ttm",
        ),
        "issues": [],
    }


def _pb_mrq(
    evidence: list[dict[str, Any]],
    valuation_inputs: dict[str, Any],
    market_capitalization: dict[str, Any],
) -> dict[str, Any]:
    equity_input = valuation_inputs["mrq_attributable_equity"]
    evidence_by_id = {
        item["id"]: item
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    project_evidence_time = _common_valuation_time(evidence_by_id, valuation_inputs)
    if (
        market_capitalization["status"] != "supported"
        or equity_input["status"] == "not_calculable"
    ):
        return {
            "status": "not_calculable",
            "value": None,
            "calculation": None,
            "cross_checks": _provider_cross_checks(
                evidence_by_id,
                valuation_inputs["cross_checks"]["evidence_ids"],
                None,
                project_evidence_time,
                "pb_mrq",
            ),
            "issues": [*market_capitalization["issues"], *equity_input["issues"]],
        }

    equity_evidence = [
        evidence_by_id[evidence_id] for evidence_id in equity_input["evidence_ids"]
    ]
    mrq_equity = _normalized_decimal(equity_evidence[0])
    calculation = {
        "formula": "market_capitalization / mrq_attributable_equity",
        "unit_conversion": "CNY / CNY = ratio",
        "precision": {
            "significant_digits": 28,
            "rounding": "ROUND_HALF_EVEN",
        },
        "operands": {
            "market_capitalization": {
                "value": market_capitalization["value"],
                "source_metric": "market_capitalization",
            },
            "mrq_attributable_equity": {
                "value": {"value": _decimal_string(mrq_equity), "unit": "CNY"},
                "period_method": "latest_applicable_periodic_report",
                "evidence": [_lineage_item(item) for item in equity_evidence],
            },
        },
    }
    if equity_input["status"] == "no_valuation_meaning":
        return {
            "status": "no_valuation_meaning",
            "value": None,
            "calculation": calculation,
            "cross_checks": _provider_cross_checks(
                evidence_by_id,
                valuation_inputs["cross_checks"]["evidence_ids"],
                None,
                project_evidence_time,
                "pb_mrq",
            ),
            "issues": [],
        }
    market_cap_value = Decimal(market_capitalization["value"]["value"])
    pb_value = decimal_ratio(market_cap_value, mrq_equity)
    return {
        "status": "supported",
        "value": {"value": _decimal_string(pb_value), "unit": "ratio"},
        "calculation": calculation,
        "cross_checks": _provider_cross_checks(
            evidence_by_id,
            valuation_inputs["cross_checks"]["evidence_ids"],
            pb_value,
            project_evidence_time,
            "pb_mrq",
        ),
        "issues": [],
    }


def build_valuation_result(
    manifest: dict[str, Any], bundle: Path, as_of: str
) -> dict[str, Any]:
    """Validate current bundle material and calculate available valuation metrics."""
    if manifest.get("as_of") != as_of:
        raise ValueError("CLI research date does not match bundle as_of")
    validation = build_bundle_validation_result(manifest, bundle)
    valuation_inputs = validation.get("valuation_inputs", {})
    evidence = manifest.get("evidence")
    evidence_items = evidence if isinstance(evidence, list) else []
    available_evidence, unavailable_evidence = _evidence_partitions(
        evidence_items, validation
    )
    market_capitalization = _market_capitalization(evidence_items, valuation_inputs)
    pe_ttm = _pe_ttm(evidence_items, valuation_inputs, market_capitalization)
    pb_mrq = _pb_mrq(evidence_items, valuation_inputs, market_capitalization)
    subject = manifest.get("subject")
    issuer = subject.get("issuer", {}) if isinstance(subject, dict) else {}
    return {
        "schema_version": "1.1",
        "status": (
            "limited" if market_capitalization["status"] == "supported" else "blocked"
        ),
        "research": {
            "security": _canonical_security(manifest, validation),
            "issuer": issuer,
            "as_of": as_of,
            "timezone": "Asia/Shanghai",
            "question": manifest["question"],
        },
        "bundle_validation": validation,
        "metrics": {
            "market_capitalization": market_capitalization,
            "pe_ttm": pe_ttm,
            "pb_mrq": pb_mrq,
        },
        "evidence": available_evidence,
        "unavailable_evidence": unavailable_evidence,
        "limitations": _result_limitations(
            validation, market_capitalization, pe_ttm, pb_mrq
        ),
    }
