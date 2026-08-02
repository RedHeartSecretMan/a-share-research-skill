"""Automatic single-security valuation from cross-checked experimental evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any, cast

from .close_observation import build_close_result
from .decimal_math import decimal_ratio, exact_difference, exact_product, exact_sum
from .identity_resolution import resolve_security_identity
from .identity_sources import CHINA_STANDARD_TIME, HttpTransport, SourceOperationError
from .valuation_sources import (
    ConsensusEpsObservation,
    ConsensusEpsSnapshot,
    EastmoneyStockInfoOperation,
    FinancialStatementObservation,
    SinaFinancialStatementsOperation,
    ThsConsensusEpsOperation,
)

ATTRIBUTABLE_PROFIT = "归属于母公司所有者的净利润"
ATTRIBUTABLE_EQUITY = "归属于母公司股东权益合计"
REVENUE = "营业收入"
TOTAL_ASSETS = "资产总计"
TOTAL_LIABILITIES = "负债合计"
OPERATING_CASH_FLOW = "经营活动产生的现金流量净额"
NET_CASH_CHANGE = "现金及现金等价物净增加额"
STATEMENT_FIELDS = {
    "income": (REVENUE, ATTRIBUTABLE_PROFIT),
    "balance": (TOTAL_ASSETS, TOTAL_LIABILITIES, ATTRIBUTABLE_EQUITY),
    "cashflow": (OPERATING_CASH_FLOW, NET_CASH_CHANGE),
}
FOUR_PLACES = Decimal("0.0001")
COMPARISON_METRIC_ORDER = [
    "market_capitalization",
    "pe_ttm",
    "pb_mrq",
    "forward_pe",
    "forecast_eps_growth",
    "peg",
    "pe_digestion_years",
]


@dataclass(frozen=True)
class SelectedReportedFinancials:
    income: FinancialStatementObservation
    balance: FinancialStatementObservation
    cashflow: FinancialStatementObservation
    profit_reports: tuple[FinancialStatementObservation, ...]
    ttm_profit: Decimal
    mrq_equity: Decimal
    period_method: str


def build_security_valuation_result(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
    stock_info_operation: EastmoneyStockInfoOperation | None = None,
) -> dict[str, Any]:
    research_now = now or datetime.now(CHINA_STANDARD_TIME)
    research_date = date.fromisoformat(request["as_of"])
    target_pe = _target_pe(request["parameters"])
    request_subjects = request["subjects"]
    request_subject = (
        request_subjects[0]
        if len(request_subjects) == 1 and isinstance(request_subjects[0], dict)
        else {}
    )
    security_class_count = request_subject.get("issuer_security_class_count")
    if security_class_count is None:
        return _blocked(
            request,
            "issuer_security_class_scope_not_established",
            (
                "Issuer valuation requires an explicit security-class count; "
                "the runtime will not assume that one A-share is the issuer's "
                "only priced ordinary-share class."
            ),
        )
    if (
        not isinstance(security_class_count, int)
        or isinstance(security_class_count, bool)
        or security_class_count < 1
    ):
        raise ValueError("issuer_security_class_count must be a positive integer")
    if security_class_count > 1:
        return _blocked(
            request,
            "multi_class_issuer_not_supported",
            (
                "Issuer-wide valuation is blocked because multiple security "
                "classes require separate prices, currencies, and share counts."
            ),
        )
    clue = _subject_clue(request)
    identity = cast(
        dict[str, Any],
        resolve_security_identity(clue, request["as_of"], transport),
    )
    candidates = identity.get("candidates", [])
    if identity["status"] == "blocked" or len(candidates) != 1:
        return _blocked(
            request,
            "security_identity_not_resolved",
            "The valuation subject does not resolve to one A-share security.",
            evidence=identity.get("evidence", []),
            source_errors=identity.get("source_errors", []),
            conflicts=identity.get("conflicts", []),
            limitations=identity.get("limitations", []),
        )
    candidate = candidates[0]
    security_value = candidate["security"]
    security = f"{security_value['exchange']}:{security_value['code']}"
    subject = {
        "security": {
            "exchange": security_value["exchange"],
            "code": security_value["code"],
            "type": security_value["type"],
        },
        "name": candidate["name"],
        "issuer": candidate["issuer"],
    }
    evidence = list(identity.get("evidence", []))
    source_errors = list(identity.get("source_errors", []))
    conflicts = list(identity.get("conflicts", []))
    limitations = list(identity.get("limitations", []))
    limitations.append(
        {
            "code": "issuer_security_class_count_is_task_declaration",
            "message": (
                "The single-class scope is supplied by the research task and is "
                "not independently established by current source operations."
            ),
        }
    )
    if research_date != research_now.date():
        return _blocked(
            request,
            "automatic_valuation_requires_current_research_date",
            (
                "The current stock-information and consensus snapshots cannot "
                "be used for a historical research boundary."
            ),
            subjects=[subject],
            evidence=evidence,
            source_errors=source_errors,
            conflicts=conflicts,
            limitations=limitations,
        )

    close_result = cast(
        dict[str, Any],
        build_close_result(security, request["as_of"], transport, research_now),
    )
    evidence.extend(close_result["evidence"])
    evidence.extend(close_result["session_evidence"])
    source_errors.extend(close_result["source_errors"])
    conflicts.extend(close_result["conflicts"])
    limitations.extend(close_result["limitations"])
    if close_result["status"] == "blocked":
        return _blocked(
            request,
            "valuation_price_not_established",
            "A cross-checked completed-session valuation price is unavailable.",
            subjects=[subject],
            evidence=evidence,
            source_errors=source_errors,
            conflicts=conflicts,
            limitations=limitations,
            metrics=_price_unavailable_metrics(),
        )

    try:
        stock_info = (stock_info_operation or EastmoneyStockInfoOperation()).observe(
            security, transport
        )
    except SourceOperationError as error:
        source_errors.append(_source_error(error))
        return _blocked(
            request,
            "automatic_valuation_evidence_incomplete",
            "A critical effective-share observation is unavailable.",
            subjects=[subject],
            evidence=evidence,
            source_errors=source_errors,
            conflicts=conflicts,
            limitations=limitations,
            metrics={
                metric: {
                    "status": "not_calculable",
                    "reason": (
                        "effective_total_shares_unavailable"
                        if metric in {"market_capitalization", "pe_ttm", "pb_mrq"}
                        else "not_evaluated_after_critical_input_failure"
                    ),
                }
                for metric in COMPARISON_METRIC_ORDER
            },
        )

    if stock_info.name != candidate["name"]:
        conflicts.append(
            {
                "code": "stock_information_identity_conflict",
                "message": "The stock-information name disagrees with resolved identity.",
                "evidence_ids": [stock_info.evidence_id],
            }
        )
        return _blocked(
            request,
            "automatic_valuation_evidence_conflict",
            "A critical valuation observation identifies another security.",
            subjects=[subject],
            evidence=[*evidence, stock_info.to_evidence()],
            conflicts=conflicts,
            source_errors=source_errors,
            limitations=limitations,
        )

    statements: dict[str, list[FinancialStatementObservation]] = {}
    selected: SelectedReportedFinancials | None = None
    try:
        statements = SinaFinancialStatementsOperation().observe(
            security, research_date, transport
        )
        selected = _select_reported_financials(statements)
    except SourceOperationError as error:
        source_errors.append(_source_error(error))
        limitations.append(
            {
                "code": "reported_financial_statements_unavailable",
                "message": "Reported PE and PB inputs are unavailable or inapplicable.",
            }
        )
    except (InvalidOperation, KeyError, ValueError) as error:
        source_errors.append(
            {
                "source_operation": "reported_financial_selection@1",
                "code": "reported_financial_periods_incompatible",
                "message": str(error),
            }
        )
        limitations.append(
            {
                "code": "reported_financial_statements_unavailable",
                "message": "Reported PE and PB inputs are unavailable or inapplicable.",
            }
        )

    consensus: ConsensusEpsSnapshot | None = None
    forecasts: list[ConsensusEpsObservation] = []
    try:
        consensus = ThsConsensusEpsOperation().observe(security, transport)
        forecasts = [
            item for item in consensus.forecasts if item.year >= research_date.year
        ]
        if len(forecasts) < 2:
            raise ValueError("at least two forecast years are required")
        if forecasts[0].institutions < 3 or forecasts[1].institutions < 3:
            raise ValueError(
                "at least three institutions are required per forecast year"
            )
    except SourceOperationError as error:
        source_errors.append(_source_error(error))
        consensus = None
        forecasts = []
        limitations.append(
            {
                "code": "consensus_eps_forecast_unavailable",
                "message": "Forward valuation metrics cannot be calculated.",
            }
        )
    except ValueError as error:
        source_errors.append(
            {
                "source_operation": "consensus_forecast_selection@1",
                "code": "consensus_forecast_inapplicable",
                "message": str(error),
            }
        )
        forecasts = []
        limitations.append(
            {
                "code": "consensus_eps_forecast_unavailable",
                "message": "Forward valuation metrics cannot be calculated.",
            }
        )

    try:
        price = Decimal(close_result["close"]["value"])
        shares = Decimal(stock_info.total_shares)
        market_cap = exact_product(price, shares)
        provider_market_cap = Decimal(stock_info.provider_market_cap)
        provider_price = Decimal(stock_info.provider_price)
    except (InvalidOperation, KeyError, ValueError) as error:
        return _blocked(
            request,
            "valuation_inputs_not_calculable",
            str(error),
            subjects=[subject],
            evidence=[*evidence, stock_info.to_evidence()],
            conflicts=conflicts,
            source_errors=source_errors,
            limitations=limitations,
        )

    evidence.append(stock_info.to_evidence())
    provider_market_cap_difference = exact_difference(market_cap, provider_market_cap)
    if provider_price == price and abs(provider_market_cap_difference) > Decimal(
        "0.01"
    ):
        conflicts.append(
            {
                "code": "provider_market_cap_conflict",
                "message": (
                    "The provider market capitalization differs from the project's "
                    "price-times-shares calculation on a comparable price basis."
                ),
                "difference": format(provider_market_cap_difference, "f"),
                "evidence_ids": [stock_info.evidence_id],
            }
        )
    elif provider_price != price:
        limitations.append(
            {
                "code": "provider_market_cap_basis_not_comparable",
                "message": (
                    "The provider market capitalization uses another price snapshot "
                    "and is retained as observation only."
                ),
            }
        )
    financial_statement_highlights: dict[str, Any] = {}
    reported_financials: dict[str, Any] = {}
    metrics: dict[str, Any] = {"market_capitalization": _metric(market_cap, "CNY", 3)}
    ttm_profit: Decimal | None = None
    mrq_equity: Decimal | None = None
    if statements:
        evidence.extend(_statement_evidence(statements))
        limitations.extend(
            [
                {
                    "code": "financial_statements_are_provider_mirror_observations",
                    "message": (
                        "Statement values are observations from a provider mirror, "
                        "not independently retrieved authoritative disclosures."
                    ),
                },
                {
                    "code": "statement_version_semantics_not_independently_verified",
                    "message": (
                        "Each period is unique in the source snapshot, but correction "
                        "and replacement semantics are not independently qualified."
                    ),
                },
            ]
        )
    if selected is not None:
        financial_statement_highlights = _statement_highlights(selected)
        ttm_profit = selected.ttm_profit
        mrq_equity = selected.mrq_equity
        reported_financials = {
            "ttm_attributable_profit": {
                "value": format(ttm_profit, "f"),
                "unit": "CNY",
                "period_method": selected.period_method,
                "evidence_periods": [
                    item.period.isoformat() for item in selected.profit_reports
                ],
            },
            "mrq_attributable_equity": {
                "value": format(mrq_equity, "f"),
                "unit": "CNY",
                "period": selected.balance.period.isoformat(),
                "publication_date": selected.balance.publication_date.isoformat(),
                "scope": "consolidated_attributable_to_owners_of_parent",
                "audit_status": selected.balance.audit_status,
            },
        }
        metrics["pe_ttm"] = (
            _metric(decimal_ratio(market_cap, ttm_profit), "ratio")
            if ttm_profit > 0
            else {
                "status": "no_valuation_meaning",
                "reason": "ttm_attributable_profit_is_nonpositive",
            }
        )
        metrics["pb_mrq"] = (
            _metric(decimal_ratio(market_cap, mrq_equity), "ratio")
            if mrq_equity > 0
            else {
                "status": "no_valuation_meaning",
                "reason": "mrq_attributable_equity_is_nonpositive",
            }
        )
    else:
        metrics["pe_ttm"] = {
            "status": "not_calculable",
            "reason": "reported_financial_statements_unavailable",
        }
        metrics["pb_mrq"] = {
            "status": "not_calculable",
            "reason": "reported_financial_statements_unavailable",
        }

    forecast_output: dict[str, Any]
    forecast_eps: Decimal | None = None
    next_eps: Decimal | None = None
    forecast_growth: Decimal | None = None
    if consensus is not None and forecasts:
        evidence.append(consensus.to_evidence())
        forecast_output = {
            "consensus_eps": [
                {
                    "year": item.year,
                    "value": item.mean,
                    "unit": "CNY/share",
                    "institutions": item.institutions,
                }
                for item in forecasts
            ],
            "source_aggregation": "mean",
            "evidence_id": consensus.evidence_id,
        }
        forecast_eps = Decimal(forecasts[0].mean)
        next_eps = Decimal(forecasts[1].mean)
        if forecast_eps <= 0:
            metrics["forward_pe"] = {
                "status": "no_valuation_meaning",
                "reason": "first_forecast_year_eps_is_nonpositive",
            }
            metrics["forecast_eps_growth"] = {
                "status": "not_calculable",
                "reason": "first_forecast_year_eps_is_nonpositive",
            }
            metrics["peg"] = dict(metrics["forecast_eps_growth"])
            metrics["pe_digestion_years"] = dict(metrics["forecast_eps_growth"])
        else:
            forward_pe = decimal_ratio(price, forecast_eps)
            forecast_growth = decimal_ratio(next_eps, forecast_eps) - Decimal(1)
            metrics["forward_pe"] = {
                **_metric(forward_pe, "ratio"),
                "forecast_year": forecasts[0].year,
            }
            metrics["forecast_eps_growth"] = {
                **_metric(forecast_growth * 100, "percent"),
                "from_year": forecasts[0].year,
                "to_year": forecasts[1].year,
            }
            if forecast_growth <= 0:
                metrics["peg"] = {
                    "status": "no_valuation_meaning",
                    "reason": "forecast_eps_growth_is_nonpositive",
                }
                metrics["pe_digestion_years"] = {
                    "status": "no_valuation_meaning",
                    "reason": "forecast_eps_growth_is_nonpositive",
                }
            else:
                metrics["peg"] = {
                    **_metric(
                        decimal_ratio(forward_pe, forecast_growth * Decimal(100)),
                        "ratio",
                    ),
                    "growth_basis": "next_year_consensus_eps_vs_first_forecast_year",
                }
                metrics["pe_digestion_years"] = {
                    **_metric(
                        _pe_digestion_years(forward_pe, forecast_growth, target_pe),
                        "years",
                    ),
                    "target_pe": format(target_pe, "f"),
                    "target_role": "user_parameter_not_factual_valuation_anchor",
                }
    else:
        forecast_output = {
            "status": "not_calculable",
            "reason": "consensus_eps_forecast_unavailable",
        }
        unavailable = {
            "status": "not_calculable",
            "reason": "consensus_eps_forecast_unavailable",
        }
        metrics["forward_pe"] = dict(unavailable)
        metrics["forecast_eps_growth"] = dict(unavailable)
        metrics["peg"] = dict(unavailable)
        metrics["pe_digestion_years"] = dict(unavailable)
    limitations.append(
        {
            "code": "experimental_automatic_valuation_sources",
            "message": (
                "Automatic shares, statements, and consensus forecasts use "
                "experimental source operations, so the result is limited."
            ),
        }
    )
    limitations.append(
        {
            "code": "effective_share_start_time_unverified",
            "message": (
                "Total shares are a current snapshot observation; their underlying "
                "corporate-action effective start time is not established."
            ),
        }
    )
    if consensus is not None:
        limitations.append(
            {
                "code": "consensus_is_opinion_not_reported_fact",
                "message": (
                    "Consensus EPS is a source-aggregated forecast and must remain "
                    "separate from reported financial facts."
                ),
            }
        )
    critical_input_gaps = []
    if selected is None:
        critical_input_gaps.append("reported_financial_statements")
    if consensus is None or not forecasts:
        critical_input_gaps.append("consensus_eps_forecast")
    if critical_input_gaps:
        limitations.append(
            {
                "code": "automatic_valuation_critical_inputs_unavailable",
                "message": (
                    "At least one requested automatic-valuation input has no "
                    "qualified fallback, so the valuation is blocked."
                ),
                "inputs": critical_input_gaps,
            }
        )
    if (
        selected is not None
        and forecasts
        and ttm_profit is not None
        and mrq_equity is not None
        and forecast_eps is not None
        and next_eps is not None
    ):
        calculations = _calculations(
            price,
            shares,
            market_cap,
            ttm_profit,
            mrq_equity,
            forecasts[0].year,
            forecast_eps,
            forecasts[1].year,
            next_eps,
            target_pe,
        )
    else:
        calculations = [
            {
                "id": "market_capitalization",
                "formula": "completed_unadjusted_close * effective_total_shares",
                "operands": {
                    "price": format(price, "f"),
                    "shares": format(shares, "f"),
                },
            }
        ]
    return {
        "schema_version": request["schema_version"],
        "status": "blocked" if critical_input_gaps else "limited",
        "subjects": [subject],
        "research": {
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
            "retrieved_at": research_now.isoformat(),
            "question": "automatic_security_valuation",
        },
        "valuation_basis": {
            "trading_date": close_result["close"]["trading_date"],
            "price": {
                "value": close_result["close"]["value"],
                "unit": "CNY/share",
            },
            "effective_total_shares": {
                "value": stock_info.total_shares,
                "unit": "shares",
                "effective_at": None,
                "observed_at": stock_info.retrieved_at.isoformat(),
                "effective_status": "current_snapshot_observation",
            },
        },
        "financial_statement_highlights": financial_statement_highlights,
        "financial_statements": _financial_statement_series(statements),
        "quarterly_snapshots": _quarterly_snapshots(statements),
        "reported_financials": reported_financials,
        "forecast": forecast_output,
        "metrics": metrics,
        "calculations": calculations,
        "evidence": evidence,
        "conflicts": conflicts,
        "source_errors": source_errors,
        "degradations": list(stock_info.degradations),
        "limitations": _deduplicate_limitations(limitations),
    }


def build_valuation_comparison_result(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare two to ten securities on one explicit valuation basis."""

    subjects = request["subjects"]
    if not 2 <= len(subjects) <= 10 or any(
        not isinstance(subject, dict) for subject in subjects
    ):
        raise ValueError("valuation_compare requires two to ten subject objects")
    clues = [subject.get("clue") for subject in subjects]
    if any(not isinstance(clue, str) or not clue.strip() for clue in clues):
        raise ValueError("valuation_compare subjects require non-empty clues")
    normalized_clues = [cast(str, clue).strip() for clue in clues]
    if len(set(normalized_clues)) != len(normalized_clues):
        raise ValueError("valuation_compare subjects must be unique")

    rows = []
    evidence: list[Any] = []
    conflicts: list[Any] = []
    source_errors: list[Any] = []
    degradations: list[Any] = []
    limitations: list[dict[str, Any]] = []
    stock_info_operation = EastmoneyStockInfoOperation()
    for subject in subjects:
        single_request = {
            **request,
            "task_type": "security_valuation",
            "subjects": [subject],
        }
        result = build_security_valuation_result(
            single_request,
            transport,
            now,
            stock_info_operation,
        )
        result_subjects = result.get("subjects", [])
        resolved_subject = (
            result_subjects[0]
            if isinstance(result_subjects, list)
            and result_subjects
            and isinstance(result_subjects[0], dict)
            else None
        )
        security = (
            f"{resolved_subject['security']['exchange']}:"
            f"{resolved_subject['security']['code']}"
            if resolved_subject is not None
            and isinstance(resolved_subject.get("security"), dict)
            else None
        )
        result_metrics = result.get("metrics", {})
        metrics = {
            metric: result_metrics.get(
                metric,
                {"status": "not_calculable", "reason": "metric_unavailable"},
            )
            for metric in COMPARISON_METRIC_ORDER
        }
        valuation_basis = result.get("valuation_basis", {})
        rows.append(
            {
                "security": security,
                "name": (
                    resolved_subject.get("name")
                    if resolved_subject is not None
                    else subject.get("clue")
                ),
                "status": result["status"],
                "trading_date": (
                    valuation_basis.get("trading_date")
                    if isinstance(valuation_basis, dict)
                    else None
                ),
                "metrics": metrics,
                "limitation_codes": [
                    item.get("code")
                    for item in result.get("limitations", [])
                    if isinstance(item, dict)
                ],
            }
        )
        evidence.extend(result.get("evidence", []))
        conflicts.extend(result.get("conflicts", []))
        source_errors.extend(result.get("source_errors", []))
        degradations.extend(result.get("degradations", []))
        limitations.extend(result.get("limitations", []))
    successful_rows = [row for row in rows if row["status"] != "blocked"]
    blocked_rows = [row for row in rows if row["status"] == "blocked"]
    if blocked_rows:
        limitations.append(
            {
                "code": "valuation_comparison_contains_blocked_rows",
                "message": (
                    "At least one requested security has a blocked valuation and "
                    "remains in the comparison."
                ),
                "blocked_subjects": [
                    {"security": row["security"], "name": row["name"]}
                    for row in blocked_rows
                ],
            }
        )
    return {
        "schema_version": request["schema_version"],
        "status": "limited" if successful_rows else "blocked",
        "subjects": [
            {"security": row["security"], "name": row["name"]} for row in rows
        ],
        "research": {
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
            "question": "same_basis_valuation_comparison",
        },
        "comparison_basis": {
            "as_of": request["as_of"],
            "price_basis": "latest_completed_unadjusted_close",
            "target_pe": format(_target_pe(request["parameters"]), "f"),
            "metric_order": COMPARISON_METRIC_ORDER,
        },
        "rows": rows,
        "evidence": _deduplicate_evidence(evidence),
        "conflicts": conflicts,
        "source_errors": source_errors,
        "degradations": degradations,
        "limitations": _deduplicate_limitations(limitations),
    }


def _subject_clue(request: dict[str, Any]) -> str:
    subjects = request["subjects"]
    if len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ValueError("security_valuation requires exactly one subject object")
    clue = subjects[0].get("clue")
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("security_valuation subject requires a non-empty clue")
    return clue.strip()


def _target_pe(parameters: dict[str, Any]) -> Decimal:
    value = parameters.get("target_pe")
    if not isinstance(value, str):
        raise ValueError("security_valuation requires target_pe as a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(
            "security_valuation target_pe must be a decimal string"
        ) from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("security_valuation target_pe must be positive")
    return parsed


def _select_reported_financials(
    statements: dict[str, list[FinancialStatementObservation]],
) -> SelectedReportedFinancials:
    income = statements["income"]
    balance = statements["balance"]
    cashflow = statements["cashflow"]
    latest_income = income[0]
    latest_balance = next(
        (item for item in balance if item.period == latest_income.period), None
    )
    latest_cashflow = next(
        (item for item in cashflow if item.period == latest_income.period), None
    )
    if latest_balance is None or latest_cashflow is None:
        raise ValueError(
            "the latest report period is not present across all statements"
        )
    if ATTRIBUTABLE_EQUITY not in latest_balance.values:
        raise ValueError("MRQ attributable equity is missing")
    if latest_income.period.month == 12:
        if ATTRIBUTABLE_PROFIT not in latest_income.values:
            raise ValueError("full-year attributable profit is missing")
        profit_reports = [latest_income]
        ttm_profit = Decimal(latest_income.values[ATTRIBUTABLE_PROFIT])
        period_method = f"FY{latest_income.period.year}"
    else:
        previous_fy = next(
            (
                item
                for item in income
                if item.period == date(latest_income.period.year - 1, 12, 31)
            ),
            None,
        )
        comparative = next(
            (
                item
                for item in income
                if item.period
                == date(
                    latest_income.period.year - 1,
                    latest_income.period.month,
                    latest_income.period.day,
                )
            ),
            None,
        )
        if previous_fy is None or comparative is None:
            raise ValueError("the three reported periods needed for TTM are missing")
        profit_reports = [previous_fy, latest_income, comparative]
        if any(ATTRIBUTABLE_PROFIT not in item.values for item in profit_reports):
            raise ValueError("a TTM attributable-profit component is missing")
        ttm_profit = exact_sum(
            Decimal(previous_fy.values[ATTRIBUTABLE_PROFIT]),
            Decimal(latest_income.values[ATTRIBUTABLE_PROFIT]),
            Decimal(comparative.values[ATTRIBUTABLE_PROFIT]).copy_negate(),
        )
        quarter = {3: "Q1", 6: "H1", 9: "Q3"}.get(latest_income.period.month)
        if quarter is None:
            raise ValueError("the latest cumulative report period is unsupported")
        period_method = (
            f"FY{previous_fy.period.year} + {latest_income.period.year}{quarter} - "
            f"{comparative.period.year}{quarter} comparative"
        )
    return SelectedReportedFinancials(
        income=latest_income,
        balance=latest_balance,
        cashflow=latest_cashflow,
        profit_reports=tuple(profit_reports),
        ttm_profit=ttm_profit,
        mrq_equity=Decimal(latest_balance.values[ATTRIBUTABLE_EQUITY]),
        period_method=period_method,
    )


def _statement_evidence(
    statements: dict[str, list[FinancialStatementObservation]],
) -> list[dict[str, Any]]:
    return [
        report.to_evidence(STATEMENT_FIELDS[statement_type])
        for statement_type, reports in statements.items()
        for report in reports
    ]


def _report_summary(report: FinancialStatementObservation) -> dict[str, Any]:
    return {
        "period": report.period.isoformat(),
        "publication_date": report.publication_date.isoformat(),
        "scope": report.report_scope,
        "audit_status": report.audit_status,
        "version_identifier": report.update_time,
        "version_relationship": "unique_period_snapshot_from_source_response",
        "values": {
            field: {"value": report.values[field], "unit": "CNY"}
            for field in STATEMENT_FIELDS[report.statement_type]
            if field in report.values
        },
        "items": [
            {"label": item.label, "value": item.value, "unit": "CNY"}
            for item in report.items
        ],
        "evidence_id": report.evidence_id,
    }


def _financial_statement_series(
    statements: dict[str, list[FinancialStatementObservation]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        statement_type: [_report_summary(report) for report in reports]
        for statement_type, reports in statements.items()
    }


def _quarterly_snapshots(
    statements: dict[str, list[FinancialStatementObservation]],
) -> list[dict[str, Any]]:
    reports_by_period = {
        statement_type: {report.period: report for report in reports}
        for statement_type, reports in statements.items()
    }
    periods = sorted(
        {period for reports in reports_by_period.values() for period in reports},
        reverse=True,
    )
    return [
        {
            "period": period.isoformat(),
            "complete": all(
                period in reports_by_period.get(statement_type, {})
                for statement_type in STATEMENT_FIELDS
            ),
            "statements": {
                statement_type: _report_summary(reports[period])
                for statement_type, reports in reports_by_period.items()
                if period in reports
            },
        }
        for period in periods
    ]


def _statement_highlights(selected: SelectedReportedFinancials) -> dict[str, Any]:
    output = {}
    for key, report in (
        ("income", selected.income),
        ("balance", selected.balance),
        ("cashflow", selected.cashflow),
    ):
        fields = STATEMENT_FIELDS[key]
        output[key] = {
            "period": report.period.isoformat(),
            "publication_date": report.publication_date.isoformat(),
            "scope": "consolidated",
            "audit_status": report.audit_status,
            "values": {
                field: {"value": report.values[field], "unit": "CNY"}
                for field in fields
                if field in report.values
            },
            "evidence_id": report.evidence_id,
        }
    return output


def _pe_digestion_years(
    current_pe: Decimal, growth: Decimal, target_pe: Decimal
) -> Decimal:
    if current_pe <= target_pe:
        return Decimal(0)
    with localcontext() as context:
        context.prec = 28
        return (current_pe / target_pe).ln() / (Decimal(1) + growth).ln()


def _metric(value: Decimal, unit: str, places: int = 4) -> dict[str, str]:
    quantum = Decimal(1).scaleb(-places)
    return {
        "status": "calculated",
        "value": format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f"),
        "unit": unit,
    }


def _calculations(
    price: Decimal,
    shares: Decimal,
    market_cap: Decimal,
    ttm_profit: Decimal,
    mrq_equity: Decimal,
    forecast_year: int,
    forecast_eps: Decimal,
    next_year: int,
    next_eps: Decimal,
    target_pe: Decimal,
) -> list[dict[str, Any]]:
    return [
        {
            "id": "market_capitalization",
            "formula": "completed_unadjusted_close * effective_total_shares",
            "operands": {"price": format(price, "f"), "shares": format(shares, "f")},
        },
        {
            "id": "pe_ttm",
            "formula": "market_capitalization / ttm_attributable_profit",
            "operands": {
                "market_capitalization": format(market_cap, "f"),
                "ttm_attributable_profit": format(ttm_profit, "f"),
            },
        },
        {
            "id": "pb_mrq",
            "formula": "market_capitalization / mrq_attributable_equity",
            "operands": {
                "market_capitalization": format(market_cap, "f"),
                "mrq_attributable_equity": format(mrq_equity, "f"),
            },
        },
        {
            "id": "forward_pe",
            "formula": "completed_unadjusted_close / first_forecast_year_consensus_eps",
            "operands": {
                "price": format(price, "f"),
                "forecast_year": forecast_year,
                "consensus_eps": format(forecast_eps, "f"),
            },
        },
        {
            "id": "forecast_eps_growth",
            "formula": "next_year_eps / first_forecast_year_eps - 1",
            "operands": {
                "first_year": forecast_year,
                "first_year_eps": format(forecast_eps, "f"),
                "next_year": next_year,
                "next_year_eps": format(next_eps, "f"),
            },
        },
        {
            "id": "peg",
            "formula": "forward_pe / (forecast_eps_growth_percent)",
        },
        {
            "id": "pe_digestion_years",
            "formula": "ln(forward_pe / target_pe) / ln(1 + forecast_eps_growth)",
            "operands": {"target_pe": format(target_pe, "f")},
        },
    ]


def _source_error(error: SourceOperationError) -> dict[str, str]:
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": str(error),
    }


def _deduplicate_limitations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen: set[tuple[object, object]] = set()
    for item in items:
        key = (item.get("code"), item.get("message"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _deduplicate_evidence(items: list[Any]) -> list[Any]:
    result = []
    seen: set[object] = set()
    for item in items:
        evidence_id = item.get("id") if isinstance(item, dict) else None
        key = evidence_id if evidence_id is not None else id(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _blocked(
    request: dict[str, Any],
    code: str,
    message: str,
    *,
    subjects: list[dict[str, Any]] | None = None,
    evidence: list[Any] | None = None,
    conflicts: list[Any] | None = None,
    source_errors: list[Any] | None = None,
    limitations: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_limitations = list(limitations or [])
    result_limitations.append({"code": code, "message": message})
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": subjects if subjects is not None else request["subjects"],
        "valuation_basis": {"status": "unresolved"},
        "reported_financials": {},
        "forecast": {},
        "metrics": metrics or {},
        "calculations": [],
        "evidence": evidence or [],
        "conflicts": conflicts or [],
        "source_errors": source_errors or [],
        "degradations": [],
        "limitations": _deduplicate_limitations(result_limitations),
    }


def _price_unavailable_metrics() -> dict[str, dict[str, str]]:
    return {
        metric: {
            "status": "not_calculable",
            "reason": (
                "not_evaluated_after_critical_input_failure"
                if metric == "forecast_eps_growth"
                else "valuation_price_not_established"
            ),
        }
        for metric in COMPARISON_METRIC_ORDER
    }
