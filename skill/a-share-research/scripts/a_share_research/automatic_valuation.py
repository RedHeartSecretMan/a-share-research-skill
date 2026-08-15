"""Automatic single-security valuation from cross-checked experimental evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any, Mapping, cast

from .close_observation import execute_close_research
from .decimal_math import decimal_ratio, exact_difference, exact_product, exact_sum
from .evidence_store import (
    EvidenceReusePolicy,
    EvidenceStoreRequest,
    resolve_operation_evidence,
)
from .identity_resolution import execute_security_identity_research
from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpTransport,
    SourceOperationError,
    build_default_identity_operations,
)
from .research_result import (
    DirectTaskCalculation,
    DirectTaskClaim,
    DirectTaskConflict,
    DirectTaskLimitation,
    DirectTaskPlan,
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    ResearchStatus,
    UnifiedResearchResult,
    fold_direct_task_research_result,
)
from .valuation_sources import (
    ConsensusEpsObservation,
    ConsensusEpsSnapshot,
    EastmoneyStockInfoOperation,
    FinancialStatementItem,
    FinancialStatementObservation,
    SinaFinancialStatementsOperation,
    StockInfoObservation,
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


@dataclass(frozen=True)
class ValuationMemberMaterial:
    status: ResearchStatus
    security: str | None
    name: str | None
    trading_date: str | None
    metrics: dict[str, Any]
    limitation_codes: tuple[str, ...]
    observation_keys: tuple[str, ...]


@dataclass(frozen=True)
class SecurityValuationExecution:
    """Operation-owned valuation execution for direct comparison composition."""

    result: UnifiedResearchResult
    outcomes: tuple[OperationOutcome, ...]
    claims: tuple[DirectTaskClaim, ...]
    calculations: tuple[DirectTaskCalculation, ...]
    conflicts: tuple[dict[str, Any], ...]
    limitations: tuple[dict[str, Any], ...]
    material: ValuationMemberMaterial

    def validated_material(self) -> ValuationMemberMaterial:
        expected_keys = _valuation_observation_keys(self.outcomes)
        if self.material.observation_keys != expected_keys:
            raise ValueError("valuation material does not close over owned outcomes")
        owned_keys = set(expected_keys)
        for calculation in self.calculations:
            if not calculation.observation_keys or not set(
                calculation.observation_keys
            ).issubset(owned_keys):
                raise ValueError(
                    "valuation material calculation dependencies are not owned"
                )
        calculated_metrics = {
            item.kind for item in self.calculations if "status" not in item.payload
        }
        if any(
            isinstance(self.material.metrics.get(metric), dict)
            and self.material.metrics[metric].get("status") == "calculated"
            and metric not in calculated_metrics
            for metric in COMPARISON_METRIC_ORDER
        ):
            raise ValueError("valuation material metric lacks its calculation")
        return self.material


def build_security_valuation_result(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
    stock_info_operation: EastmoneyStockInfoOperation | None = None,
    evidence_store: EvidenceStoreRequest | None = None,
) -> UnifiedResearchResult:
    return _execute_security_valuation(
        request, transport, now, stock_info_operation, evidence_store
    ).result


def _execute_security_valuation(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
    stock_info_operation: EastmoneyStockInfoOperation | None = None,
    evidence_store: EvidenceStoreRequest | None = None,
) -> SecurityValuationExecution:
    research_now = now or datetime.now(CHINA_STANDARD_TIME)
    research_date = date.fromisoformat(request["as_of"])
    target_pe = _target_pe(request["parameters"])
    if not request["source_policy"]["allow_experimental"]:
        return _blocked_execution(
            request,
            "source_policy_not_satisfied",
            "security_valuation currently requires experimental source operations",
            evidence_store=evidence_store,
        )
    request_subjects = request["subjects"]
    request_subject = (
        request_subjects[0]
        if len(request_subjects) == 1 and isinstance(request_subjects[0], dict)
        else {}
    )
    security_class_count = request_subject.get("issuer_security_class_count")
    if security_class_count is None:
        return _blocked_execution(
            request,
            "issuer_security_class_scope_not_established",
            (
                "Issuer valuation requires an explicit security-class count; "
                "the runtime will not assume that one A-share is the issuer's "
                "only priced ordinary-share class."
            ),
            evidence_store=evidence_store,
        )
    if (
        not isinstance(security_class_count, int)
        or isinstance(security_class_count, bool)
        or security_class_count < 1
    ):
        raise ValueError("issuer_security_class_count must be a positive integer")
    if security_class_count > 1:
        return _blocked_execution(
            request,
            "multi_class_issuer_not_supported",
            (
                "Issuer-wide valuation is blocked because multiple security "
                "classes require separate prices, currencies, and share counts."
            ),
            evidence_store=evidence_store,
        )
    clue = _subject_clue(request)
    identity_execution = execute_security_identity_research(
        {
            "schema_version": "1.0",
            "task_type": "security_identity",
            "subjects": [{"clue": clue}],
            "as_of": request["as_of"],
            "window": None,
            "parameters": {},
            "source_policy": request["source_policy"],
        },
        build_default_identity_operations(transport),
    )
    identity_material = identity_execution.material
    canonical_identity = identity_material.canonical_identity
    identity_limitations: list[dict[str, Any]] = [
        {"code": item.code, "message": item.message}
        for item in identity_material.limitations
    ]
    if identity_material.status == "blocked" or canonical_identity is None:
        return _blocked_execution(
            request,
            "security_identity_not_resolved",
            "The valuation subject does not resolve to one A-share security.",
            outcomes=identity_execution.outcomes,
            limitations=identity_limitations,
            evidence_store=evidence_store,
        )
    security = f"{canonical_identity.exchange}:{canonical_identity.code}"
    subject = {
        "security": {
            "exchange": canonical_identity.exchange,
            "code": canonical_identity.code,
            "type": canonical_identity.security_type,
        },
        "name": canonical_identity.name,
        "issuer": {
            "name": canonical_identity.issuer_name,
            "identifier": canonical_identity.issuer_identifier,
            "security_relationship": canonical_identity.issuer_relationship,
        },
    }
    outcomes = list(identity_execution.outcomes)
    conflicts: list[Any] = []
    limitations = identity_limitations
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
        return _blocked_execution(
            request,
            "automatic_valuation_requires_current_research_date",
            (
                "The current stock-information and consensus snapshots cannot "
                "be used for a historical research boundary."
            ),
            subjects=[subject],
            outcomes=tuple(outcomes),
            conflicts=conflicts,
            limitations=limitations,
            evidence_store=evidence_store,
        )

    close_execution = execute_close_research(
        security, request["as_of"], transport, research_now
    )
    close_material = close_execution.validated_material()
    outcomes.extend(close_execution.outcomes)
    conflicts.extend(close_material.conflicts)
    limitations.extend(close_material.limitations)
    selected_close = close_material.selected_close
    if close_material.status == "blocked" or selected_close is None:
        return _blocked_execution(
            request,
            "valuation_price_not_established",
            "A cross-checked completed-session valuation price is unavailable.",
            subjects=[subject],
            outcomes=tuple(outcomes),
            conflicts=conflicts,
            limitations=limitations,
            metrics=_price_unavailable_metrics(),
            evidence_store=evidence_store,
        )

    stock_info: StockInfoObservation | None = None
    try:
        stock_info = (stock_info_operation or EastmoneyStockInfoOperation()).observe(
            security, transport
        )
    except SourceOperationError as error:
        outcomes.append(
            _valuation_outcome("eastmoney_stock_info@1", error=_source_error(error))
        )
        limitations.append(
            {
                "code": "effective_total_shares_unavailable",
                "message": (
                    "Market capitalization, reported PE, and PB cannot be "
                    "calculated without an effective-share observation."
                ),
            }
        )
    else:
        outcomes.append(
            _valuation_outcome(
                "eastmoney_stock_info@1",
                observations=(stock_info.to_evidence(),),
                diagnostics={"attempts": list(stock_info.degradations)},
            )
        )

    if stock_info is not None and stock_info.name != canonical_identity.name:
        conflicts.append(
            {
                "code": "stock_information_identity_conflict",
                "message": "The stock-information name disagrees with resolved identity.",
                "evidence_ids": [stock_info.evidence_id],
            }
        )
        return _blocked_execution(
            request,
            "automatic_valuation_evidence_conflict",
            "A critical valuation observation identifies another security.",
            subjects=[subject],
            outcomes=tuple(outcomes),
            conflicts=conflicts,
            limitations=limitations,
            evidence_store=evidence_store,
        )

    statements: dict[str, list[FinancialStatementObservation]] = {}
    selected: SelectedReportedFinancials | None = None
    statement_operation = SinaFinancialStatementsOperation()

    def acquire_statements() -> OperationOutcome:
        try:
            acquired = statement_operation.observe(security, research_date, transport)
        except SourceOperationError as error:
            return _valuation_outcome(
                statement_operation.operation_id,
                error=_source_error(error),
            )
        return _valuation_outcome(
            statement_operation.operation_id,
            observations=tuple(_statement_evidence(acquired)),
        )

    if evidence_store is None:
        statement_outcome = acquire_statements()
    else:
        statement_resolution = resolve_operation_evidence(
            evidence_store,
            EvidenceReusePolicy(
                capability_id="security_valuation_metrics",
                operation_id=statement_operation.operation_id,
                source_version=_FINANCIAL_EVIDENCE_STORE_VERSION,
                source_role="market_observation",
                experimental=True,
                field_dispositions=_valuation_dispositions(
                    statement_operation.operation_id
                ),
                subject_identity=security,
                evidence_scope="completed_history",
                max_age=timedelta(days=31),
            ),
            acquire_statements,
            now=research_now,
        )
        statement_outcome = replace(
            statement_resolution.outcome,
            evidence_storage=statement_resolution.audit,
        )
    outcomes.append(statement_outcome)
    if statement_outcome.execution.state != "succeeded":
        limitations.append(
            {
                "code": "reported_financial_statements_unavailable",
                "message": "Reported PE and PB inputs are unavailable or inapplicable.",
            }
        )
    else:
        statements = _statements_from_outcome(statement_outcome, security)
        try:
            selected = _select_reported_financials(statements)
        except (InvalidOperation, KeyError, ValueError) as error:
            selection_error = {
                "source_operation": "reported_financial_selection@1",
                "code": "reported_financial_periods_incompatible",
                "message": str(error),
            }
            outcomes.append(
                _valuation_outcome(
                    "reported_financial_selection@1", error=selection_error
                )
            )
            limitations.append(
                {
                    "code": "reported_financial_statements_unavailable",
                    "message": (
                        "Reported PE and PB inputs are unavailable or inapplicable."
                    ),
                }
            )

    consensus: ConsensusEpsSnapshot | None = None
    forecasts: list[ConsensusEpsObservation] = []
    try:
        consensus = ThsConsensusEpsOperation().observe(security, transport)
        forecasts = [
            item for item in consensus.forecasts if item.year >= research_date.year
        ]
    except SourceOperationError as error:
        outcomes.append(
            _valuation_outcome("ths_consensus_eps@1", error=_source_error(error))
        )
        consensus = None
        forecasts = []
        limitations.append(
            {
                "code": "consensus_eps_forecast_unavailable",
                "message": "Forward valuation metrics cannot be calculated.",
            }
        )
    else:
        outcomes.append(
            _valuation_outcome(
                "ths_consensus_eps@1", observations=(consensus.to_evidence(),)
            )
        )
        try:
            if len(forecasts) < 2:
                raise ValueError("at least two forecast years are required")
            if forecasts[0].institutions < 3 or forecasts[1].institutions < 3:
                raise ValueError(
                    "at least three institutions are required per forecast year"
                )
        except ValueError as error:
            selection_error = {
                "source_operation": "consensus_forecast_selection@1",
                "code": "consensus_forecast_inapplicable",
                "message": str(error),
            }
            outcomes.append(
                _valuation_outcome(
                    "consensus_forecast_selection@1", error=selection_error
                )
            )
            forecasts = []
            limitations.append(
                {
                    "code": "consensus_eps_forecast_unavailable",
                    "message": "Forward valuation metrics cannot be calculated.",
                }
            )

    try:
        price = Decimal(selected_close.value)
    except (InvalidOperation, KeyError, ValueError) as error:
        return _blocked_execution(
            request,
            "valuation_inputs_not_calculable",
            str(error),
            subjects=[subject],
            outcomes=tuple(outcomes),
            conflicts=conflicts,
            limitations=limitations,
            evidence_store=evidence_store,
        )

    shares: Decimal | None = None
    market_cap: Decimal | None = None
    if stock_info is not None:
        try:
            shares = Decimal(stock_info.total_shares)
            market_cap = exact_product(price, shares)
            provider_market_cap = Decimal(stock_info.provider_market_cap)
            provider_price = Decimal(stock_info.provider_price)
        except (InvalidOperation, KeyError, ValueError) as error:
            return _blocked_execution(
                request,
                "valuation_inputs_not_calculable",
                str(error),
                subjects=[subject],
                outcomes=tuple(outcomes),
                conflicts=conflicts,
                limitations=limitations,
                evidence_store=evidence_store,
            )

        provider_market_cap_difference = exact_difference(
            market_cap, provider_market_cap
        )
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
    ttm_profit: Decimal | None = None
    mrq_equity: Decimal | None = None
    if statements:
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
        ttm_profit = selected.ttm_profit
        mrq_equity = selected.mrq_equity

    forecast_eps: Decimal | None = None
    next_eps: Decimal | None = None
    if consensus is not None and forecasts:
        forecast_eps = Decimal(forecasts[0].mean)
        next_eps = Decimal(forecasts[1].mean)
    limitations.append(
        {
            "code": "experimental_automatic_valuation_sources",
            "message": (
                "Automatic shares, statements, and consensus forecasts use "
                "experimental source operations, so the result is limited."
            ),
        }
    )
    if stock_info is not None:
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
    if stock_info is None:
        critical_input_gaps.append("effective_total_shares")
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
                    "qualified fallback; only metrics independent of that input "
                    "are retained."
                ),
                "inputs": critical_input_gaps,
            }
        )
    calculations = _calculations(
        price,
        shares,
        market_cap,
        ttm_profit,
        mrq_equity,
        forecasts[0].year if forecasts else None,
        forecast_eps,
        forecasts[1].year if len(forecasts) > 1 else None,
        next_eps,
        target_pe,
    )
    valuation_payload = _valuation_claim_payload(
        subject=subject,
        research={
            "as_of": request["as_of"],
            "timezone": "Asia/Shanghai",
            "retrieved_at": research_now.isoformat(),
            "question": "automatic_security_valuation",
        },
        trading_date=selected_close.trading_date,
        price=selected_close.value,
        stock_info=stock_info,
        statements=statements,
        selected=selected,
        consensus=consensus,
        forecasts=forecasts,
        target_pe=target_pe,
    )
    owned_evidence = [
        dict(observation)
        for outcome in outcomes
        for observation in outcome.observations
    ]
    all_keys = _valuation_observation_keys(tuple(outcomes))
    direct_calculations = [
        DirectTaskCalculation(
            kind=str(item["id"]),
            evidence_use="security_valuation",
            observation_keys=_valuation_calculation_keys(
                str(item["id"]), owned_evidence
            ),
            payload={key: value for key, value in item.items() if key != "id"},
        )
        for item in calculations
    ]
    return _direct_valuation_execution(
        request,
        outcomes=tuple(outcomes),
        conflicts=conflicts,
        limitations=_deduplicate_limitations(limitations),
        claims=[
            DirectTaskClaim(
                kind="security_valuation_metrics",
                evidence_use="security_valuation",
                observation_keys=all_keys,
                payload=valuation_payload,
            )
        ],
        calculations=direct_calculations,
        evidence_store=evidence_store,
    )


def build_valuation_comparison_result(
    request: dict[str, Any],
    transport: HttpTransport,
    now: datetime | None = None,
) -> UnifiedResearchResult:
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

    if not request["source_policy"]["allow_experimental"]:
        blocked_outcomes = tuple(
            _valuation_outcome(operation_id, skipped="skipped_policy")
            for operation_id in _VALUATION_REQUIRED_OPERATIONS
        )
        blocked_rows = [
            {
                "member_id": f"member-{index}",
                "security": None,
                "name": clue,
                "status": "blocked",
                "trading_date": None,
                "metrics": {
                    metric: {
                        "status": "not_calculable",
                        "reason": "source_policy_not_satisfied",
                    }
                    for metric in COMPARISON_METRIC_ORDER
                },
                "limitation_codes": ["source_policy_not_satisfied"],
                "evidence_ids": [],
            }
            for index, clue in enumerate(normalized_clues)
        ]
        return _direct_valuation_execution(
            request,
            task_type="valuation_compare",
            outcomes=tuple(
                outcome
                for index in range(len(subjects))
                for outcome in _scope_valuation_member(
                    blocked_outcomes, f"member-{index}"
                )[0]
            ),
            conflicts=[],
            limitations=[
                {
                    "code": "source_policy_not_satisfied",
                    "message": (
                        "valuation_compare currently requires experimental source "
                        "operations"
                    ),
                }
            ],
            claims=[
                DirectTaskClaim(
                    kind="valuation_compare_diagnostics",
                    evidence_use="valuation_compare",
                    observation_keys=(),
                    payload={
                        "subjects": [
                            {"security": None, "name": clue}
                            for clue in normalized_clues
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
                        "rows": blocked_rows,
                    },
                )
            ],
        ).result

    rows: list[dict[str, Any]] = []
    comparison_outcomes: list[OperationOutcome] = []
    comparison_calculations: list[DirectTaskCalculation] = []
    conflicts: list[Any] = []
    limitations: list[dict[str, Any]] = []
    stock_info_operation = EastmoneyStockInfoOperation()
    for index, subject in enumerate(subjects):
        single_request = {
            **request,
            "task_type": "security_valuation",
            "subjects": [subject],
        }
        execution = _execute_security_valuation(
            single_request,
            transport,
            now,
            stock_info_operation,
        )
        member_id = f"member-{index}"
        scoped_outcomes, key_map = _scope_valuation_member(
            execution.outcomes, member_id
        )
        comparison_outcomes.extend(scoped_outcomes)
        member_material = execution.validated_material()
        metrics = {
            metric: member_material.metrics.get(
                metric,
                {"status": "not_calculable", "reason": "metric_unavailable"},
            )
            for metric in COMPARISON_METRIC_ORDER
        }
        member_keys = tuple(
            key_map[key] for key in member_material.observation_keys if key in key_map
        )
        rows.append(
            {
                "member_id": member_id,
                "security": member_material.security,
                "name": member_material.name or subject.get("clue"),
                "status": member_material.status,
                "trading_date": member_material.trading_date,
                "metrics": metrics,
                "limitation_codes": list(member_material.limitation_codes),
                "evidence_ids": list(member_keys),
            }
        )
        for calculation in execution.calculations:
            comparison_calculations.append(
                DirectTaskCalculation(
                    kind=f"{member_id}::{calculation.kind}",
                    evidence_use="valuation_compare",
                    observation_keys=tuple(
                        key_map[key]
                        for key in calculation.observation_keys
                        if key in key_map
                    ),
                    payload=calculation.payload,
                )
            )
        for conflict in execution.conflicts:
            scoped_conflict = dict(conflict)
            scoped_conflict["code"] = f"{member_id}::{conflict.get('code', 'conflict')}"
            scoped_conflict["evidence_ids"] = [
                key_map[key]
                for key in conflict.get("evidence_ids", [])
                if key in key_map
            ]
            conflicts.append(scoped_conflict)
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
    comparison_payload: dict[str, object] = {
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
    }
    all_keys = tuple(
        str(observation["id"])
        for outcome in comparison_outcomes
        for observation in outcome.observations
        if isinstance(observation.get("id"), str)
    )
    return _direct_valuation_execution(
        request,
        task_type="valuation_compare",
        outcomes=tuple(comparison_outcomes),
        conflicts=conflicts,
        limitations=_deduplicate_limitations(limitations),
        claims=[
            DirectTaskClaim(
                kind=(
                    "valuation_compare_members"
                    if successful_rows
                    else "valuation_compare_diagnostics"
                ),
                evidence_use="valuation_compare",
                observation_keys=all_keys,
                payload=comparison_payload,
            )
        ],
        calculations=comparison_calculations,
    ).result


def _scope_valuation_member(
    outcomes: tuple[OperationOutcome, ...], member_id: str
) -> tuple[tuple[OperationOutcome, ...], dict[str, str]]:
    """Make operation, error, and local observation ownership member-unique."""

    scoped_outcomes = []
    key_map: dict[str, str] = {}
    for outcome in outcomes:
        operation_id = f"{member_id}::{outcome.operation_id}"
        observations = []
        for index, observation in enumerate(outcome.observations):
            original_key = observation.get("id")
            local_key = (
                str(original_key)
                if isinstance(original_key, str) and original_key
                else f"{outcome.operation_id}:{index}"
            )
            scoped_key = f"{member_id}::{local_key}"
            key_map[local_key] = scoped_key
            payload = dict(observation)
            payload["id"] = scoped_key
            payload["source_operation"] = operation_id
            observations.append(payload)
        source_errors = []
        for source_error in outcome.source_errors:
            payload = dict(source_error)
            payload["source_operation"] = operation_id
            source_errors.append(payload)
        scoped_outcomes.append(
            OperationOutcome(
                capability_id="valuation_compare_metrics",
                operation_id=operation_id,
                execution=outcome.execution,
                observations=tuple(observations),
                source_errors=tuple(source_errors),
                field_dispositions=outcome.field_dispositions,
                diagnostics=_scope_member_diagnostics(
                    outcome.diagnostics, operation_id
                ),
            )
        )
    return tuple(scoped_outcomes), key_map


def _valuation_member_producer_operation_id(operation_id: str) -> str:
    """Invert the application identity emitted by ``_scope_valuation_member``."""

    member_id, separator, producer_operation_id = operation_id.partition("::")
    if (
        separator != "::"
        or not member_id.startswith("member-")
        or not member_id.removeprefix("member-").isdigit()
        or not producer_operation_id
        or "::" in producer_operation_id
    ):
        raise ValueError("valuation member application identity is invalid")
    return producer_operation_id


def _scope_member_diagnostics(
    diagnostics: dict[str, object] | Any, operation_id: str
) -> dict[str, object]:
    result = dict(diagnostics)
    attempts = result.get("attempts")
    if isinstance(attempts, list):
        result["attempts"] = [
            {**item, "source_operation": operation_id}
            if isinstance(item, dict) and "source_operation" in item
            else item
            for item in attempts
        ]
    return result


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


def _statements_from_outcome(
    outcome: OperationOutcome, security: str
) -> dict[str, list[FinancialStatementObservation]]:
    """Rebuild typed statement operands from the one normalized owned outcome."""

    if (
        outcome.capability_id != "security_valuation_metrics"
        or outcome.operation_id != "sina_financial_statements@1"
        or outcome.execution.state != "succeeded"
        or outcome.execution.query_complete is not True
        or outcome.source_errors
    ):
        raise ValueError("financial statement outcome is not reusable")
    statements: dict[str, list[FinancialStatementObservation]] = {
        "income": [],
        "balance": [],
        "cashflow": [],
    }
    for evidence in outcome.observations:
        if (
            evidence.get("source_operation") != outcome.operation_id
            or evidence.get("subject") != {"security": security}
            or evidence.get("source_role") != "market_observation"
            or evidence.get("experimental") is not True
        ):
            raise ValueError("financial statement evidence owner is invalid")
        basis = evidence.get("basis")
        if not isinstance(basis, str) or not (
            basis.startswith("reported_") and basis.endswith("_statement")
        ):
            raise ValueError("financial statement evidence basis is invalid")
        statement_type = basis.removeprefix("reported_").removesuffix("_statement")
        if statement_type not in statements:
            raise ValueError("financial statement evidence type is invalid")
        report = evidence.get("report")
        observed_value = evidence.get("observed_value")
        full_items = evidence.get("full_statement_items")
        locator = evidence.get("locator")
        retrieved_at = evidence.get("retrieved_at")
        if (
            not isinstance(report, dict)
            or set(report)
            != {
                "period",
                "publication_date",
                "scope",
                "audit_status",
                "data_source",
                "source_update_time",
                "version_identifier",
                "version_relationship",
            }
            or report.get("version_identifier") != report.get("source_update_time")
            or report.get("version_relationship")
            != "unique_period_snapshot_from_source_response"
            or not isinstance(observed_value, dict)
            or set(observed_value) != {"value", "unit"}
            or observed_value.get("unit") != "CNY"
            or not isinstance(observed_value.get("value"), dict)
            or not isinstance(full_items, list)
            or not isinstance(locator, dict)
            or set(locator) != {"uri", "observation"}
            or not isinstance(locator.get("uri"), str)
            or not isinstance(retrieved_at, str)
        ):
            raise ValueError("financial statement evidence contract is invalid")
        try:
            period = date.fromisoformat(cast(str, report["period"]))
            publication_date = date.fromisoformat(cast(str, report["publication_date"]))
            retrieved = datetime.fromisoformat(retrieved_at)
        except (TypeError, ValueError) as error:
            raise ValueError("financial statement evidence time is invalid") from error
        if (
            retrieved.utcoffset() is None
            or publication_date > retrieved.date()
            or evidence.get("evidence_time") != period.isoformat()
            or evidence.get("available_at") != publication_date.isoformat()
        ):
            raise ValueError("financial statement evidence time is invalid")
        items: list[FinancialStatementItem] = []
        for item in full_items:
            if (
                not isinstance(item, dict)
                or set(item) != {"label", "value", "unit"}
                or item.get("unit") != "CNY"
                or not isinstance(item.get("label"), str)
                or not isinstance(item.get("value"), str)
            ):
                raise ValueError("financial statement item is invalid")
            items.append(
                FinancialStatementItem(
                    label=cast(str, item["label"]),
                    value=cast(str, item["value"]),
                )
            )
        values = {item.label: item.value for item in items}
        selected_values = cast(dict[str, object], observed_value["value"])
        if any(values.get(field) != value for field, value in selected_values.items()):
            raise ValueError("financial statement selected values contradict items")
        statement = FinancialStatementObservation(
            security=security,
            statement_type=statement_type,
            period=period,
            publication_date=publication_date,
            currency="CNY",
            report_scope=cast(str, report["scope"]),
            audit_status=cast(str, report["audit_status"]),
            data_source=cast(str, report["data_source"]),
            update_time=cast(str, report["source_update_time"]),
            values=values,
            items=tuple(items),
            source_uri=cast(str, locator["uri"]),
            retrieved_at=retrieved,
        )
        if statement.to_evidence(STATEMENT_FIELDS[statement_type]) != evidence:
            raise ValueError("financial statement evidence is not canonical")
        statements[statement_type].append(statement)
    if not all(statements.values()):
        raise ValueError("financial statement outcome is incomplete")
    return statements


def _statements_from_evidence(
    evidence: list[Mapping[str, object]], security: str
) -> dict[str, list[FinancialStatementObservation]]:
    """Rebuild typed statements for serializer authority from public evidence."""

    return _statements_from_outcome(
        OperationOutcome(
            capability_id="security_valuation_metrics",
            operation_id="sina_financial_statements@1",
            execution=OperationExecution("succeeded", query_complete=True),
            observations=tuple(evidence),
            field_dispositions=_valuation_dispositions("sina_financial_statements@1"),
        ),
        security,
    )


def _stock_info_from_evidence(
    evidence: Mapping[str, object],
    degradations: tuple[dict[str, str], ...] = (),
) -> StockInfoObservation:
    subject = evidence.get("subject")
    observed_value = evidence.get("observed_value")
    locator = evidence.get("locator")
    retrieved_at = evidence.get("retrieved_at")
    if (
        not isinstance(subject, Mapping)
        or not isinstance(subject.get("security"), str)
        or not isinstance(observed_value, Mapping)
        or not isinstance(observed_value.get("value"), Mapping)
        or not isinstance(locator, Mapping)
        or not isinstance(locator.get("uri"), str)
        or not isinstance(retrieved_at, str)
    ):
        raise ValueError("stock information evidence contract is invalid")
    values = cast(Mapping[str, object], observed_value["value"])
    if any(
        not isinstance(values.get(key), str)
        for key in (
            "name",
            "effective_total_shares",
            "provider_market_cap",
            "provider_price",
        )
    ):
        raise ValueError("stock information evidence contract is invalid")
    stock = StockInfoObservation(
        security=cast(str, subject["security"]),
        name=cast(str, values["name"]),
        total_shares=cast(str, values["effective_total_shares"]),
        provider_market_cap=cast(str, values["provider_market_cap"]),
        provider_price=cast(str, values["provider_price"]),
        source_uri=cast(str, locator["uri"]),
        retrieved_at=datetime.fromisoformat(retrieved_at),
        degradations=degradations,
    )
    if stock.to_evidence() != evidence:
        raise ValueError("stock information evidence is not canonical")
    return stock


def _consensus_from_evidence(
    evidence: Mapping[str, object], security: str
) -> ConsensusEpsSnapshot:
    observed_value = evidence.get("observed_value")
    locator = evidence.get("locator")
    retrieved_at = evidence.get("retrieved_at")
    if (
        not isinstance(observed_value, Mapping)
        or not isinstance(observed_value.get("value"), list)
        or not isinstance(locator, Mapping)
        or not isinstance(locator.get("uri"), str)
        or not isinstance(retrieved_at, str)
    ):
        raise ValueError("consensus evidence contract is invalid")
    forecasts: list[ConsensusEpsObservation] = []
    for item in cast(list[object], observed_value["value"]):
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("year"), int)
            or isinstance(item.get("year"), bool)
            or not isinstance(item.get("institutions"), int)
            or isinstance(item.get("institutions"), bool)
            or any(
                not isinstance(item.get(key), str)
                for key in ("minimum", "mean", "maximum")
            )
        ):
            raise ValueError("consensus evidence contract is invalid")
        forecasts.append(
            ConsensusEpsObservation(
                year=cast(int, item["year"]),
                institutions=cast(int, item["institutions"]),
                minimum=cast(str, item["minimum"]),
                mean=cast(str, item["mean"]),
                maximum=cast(str, item["maximum"]),
            )
        )
    snapshot = ConsensusEpsSnapshot(
        security=security,
        forecasts=tuple(forecasts),
        source_uri=cast(str, locator["uri"]),
        retrieved_at=datetime.fromisoformat(retrieved_at),
    )
    if snapshot.to_evidence() != evidence:
        raise ValueError("consensus evidence is not canonical")
    return snapshot


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


def _valuation_claim_payload(
    *,
    subject: Mapping[str, object],
    research: Mapping[str, object],
    trading_date: str,
    price: str,
    stock_info: StockInfoObservation | None,
    statements: dict[str, list[FinancialStatementObservation]],
    selected: SelectedReportedFinancials | None,
    consensus: ConsensusEpsSnapshot | None,
    forecasts: list[ConsensusEpsObservation],
    target_pe: Decimal,
) -> dict[str, object]:
    """Project the complete public claim from normalized valuation owner facts."""

    price_value = Decimal(price)
    market_cap = (
        exact_product(price_value, Decimal(stock_info.total_shares))
        if stock_info is not None
        else None
    )
    metrics: dict[str, object] = {
        "market_capitalization": (
            _metric(market_cap, "CNY", 3)
            if market_cap is not None
            else {
                "status": "not_calculable",
                "reason": "effective_total_shares_unavailable",
            }
        )
    }
    if selected is None:
        unavailable_reported = {
            "status": "not_calculable",
            "reason": "reported_financial_statements_unavailable",
        }
        metrics["pe_ttm"] = dict(unavailable_reported)
        metrics["pb_mrq"] = dict(unavailable_reported)
    else:
        metrics["pe_ttm"] = (
            {
                "status": "no_valuation_meaning",
                "reason": "ttm_attributable_profit_is_nonpositive",
            }
            if selected.ttm_profit <= 0
            else _metric(decimal_ratio(market_cap, selected.ttm_profit), "ratio")
            if market_cap is not None
            else {
                "status": "not_calculable",
                "reason": "effective_total_shares_unavailable",
            }
        )
        metrics["pb_mrq"] = (
            {
                "status": "no_valuation_meaning",
                "reason": "mrq_attributable_equity_is_nonpositive",
            }
            if selected.mrq_equity <= 0
            else _metric(decimal_ratio(market_cap, selected.mrq_equity), "ratio")
            if market_cap is not None
            else {
                "status": "not_calculable",
                "reason": "effective_total_shares_unavailable",
            }
        )
    if consensus is None or not forecasts:
        unavailable_forecast = {
            "status": "not_calculable",
            "reason": "consensus_eps_forecast_unavailable",
        }
        for metric in (
            "forward_pe",
            "forecast_eps_growth",
            "peg",
            "pe_digestion_years",
        ):
            metrics[metric] = dict(unavailable_forecast)
    else:
        forecast_eps = Decimal(forecasts[0].mean)
        next_eps = Decimal(forecasts[1].mean)
        if forecast_eps <= 0:
            metrics["forward_pe"] = {
                "status": "no_valuation_meaning",
                "reason": "first_forecast_year_eps_is_nonpositive",
            }
            unavailable_growth = {
                "status": "not_calculable",
                "reason": "first_forecast_year_eps_is_nonpositive",
            }
            metrics["forecast_eps_growth"] = dict(unavailable_growth)
            metrics["peg"] = dict(unavailable_growth)
            metrics["pe_digestion_years"] = dict(unavailable_growth)
        else:
            forward_pe = decimal_ratio(price_value, forecast_eps)
            growth = decimal_ratio(next_eps, forecast_eps) - Decimal(1)
            metrics["forward_pe"] = {
                **_metric(forward_pe, "ratio"),
                "forecast_year": forecasts[0].year,
            }
            metrics["forecast_eps_growth"] = {
                **_metric(growth * 100, "percent"),
                "from_year": forecasts[0].year,
                "to_year": forecasts[1].year,
            }
            if growth <= 0:
                no_growth = {
                    "status": "no_valuation_meaning",
                    "reason": "forecast_eps_growth_is_nonpositive",
                }
                metrics["peg"] = dict(no_growth)
                metrics["pe_digestion_years"] = dict(no_growth)
            else:
                metrics["peg"] = {
                    **_metric(decimal_ratio(forward_pe, growth * 100), "ratio"),
                    "growth_basis": ("next_year_consensus_eps_vs_first_forecast_year"),
                }
                metrics["pe_digestion_years"] = {
                    **_metric(
                        _pe_digestion_years(forward_pe, growth, target_pe), "years"
                    ),
                    "target_pe": format(target_pe, "f"),
                    "target_role": "user_parameter_not_factual_valuation_anchor",
                }
    financial_statement_highlights = (
        _statement_highlights(selected) if selected is not None else {}
    )
    reported_financials: dict[str, object] = {}
    if selected is not None:
        reported_financials = {
            "ttm_attributable_profit": {
                "value": format(selected.ttm_profit, "f"),
                "unit": "CNY",
                "period_method": selected.period_method,
                "evidence_periods": [
                    item.period.isoformat() for item in selected.profit_reports
                ],
            },
            "mrq_attributable_equity": {
                "value": format(selected.mrq_equity, "f"),
                "unit": "CNY",
                "period": selected.balance.period.isoformat(),
                "publication_date": selected.balance.publication_date.isoformat(),
                "scope": "consolidated_attributable_to_owners_of_parent",
                "audit_status": selected.balance.audit_status,
            },
        }
    forecast: dict[str, object]
    if consensus is not None and forecasts:
        forecast = {
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
    else:
        forecast = {
            "status": "not_calculable",
            "reason": "consensus_eps_forecast_unavailable",
        }
    return {
        "subjects": [dict(subject)],
        "research": dict(research),
        "valuation_basis": {
            "trading_date": trading_date,
            "price": {"value": price, "unit": "CNY/share"},
            "effective_total_shares": (
                {
                    "value": stock_info.total_shares,
                    "unit": "shares",
                    "effective_at": None,
                    "observed_at": stock_info.retrieved_at.isoformat(),
                    "effective_status": "current_snapshot_observation",
                }
                if stock_info is not None
                else {
                    "status": "not_calculable",
                    "reason": "effective_total_shares_unavailable",
                }
            ),
        },
        "financial_statement_highlights": financial_statement_highlights,
        "financial_statements": _financial_statement_series(statements),
        "quarterly_snapshots": _quarterly_snapshots(statements),
        "reported_financials": reported_financials,
        "forecast": forecast,
        "metrics": metrics,
        "degradations": list(stock_info.degradations) if stock_info is not None else [],
    }


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
    shares: Decimal | None,
    market_cap: Decimal | None,
    ttm_profit: Decimal | None,
    mrq_equity: Decimal | None,
    forecast_year: int | None,
    forecast_eps: Decimal | None,
    next_year: int | None,
    next_eps: Decimal | None,
    target_pe: Decimal,
) -> list[dict[str, Any]]:
    calculations: list[dict[str, Any]] = []
    if shares is not None and market_cap is not None:
        calculations.append(
            {
                "id": "market_capitalization",
                "formula": "completed_unadjusted_close * effective_total_shares",
                "operands": {
                    "price": format(price, "f"),
                    "shares": format(shares, "f"),
                },
            }
        )
    if market_cap is not None and ttm_profit is not None and ttm_profit > 0:
        calculations.append(
            {
                "id": "pe_ttm",
                "formula": "market_capitalization / ttm_attributable_profit",
                "operands": {
                    "market_capitalization": format(market_cap, "f"),
                    "ttm_attributable_profit": format(ttm_profit, "f"),
                },
            }
        )
    if market_cap is not None and mrq_equity is not None and mrq_equity > 0:
        calculations.append(
            {
                "id": "pb_mrq",
                "formula": "market_capitalization / mrq_attributable_equity",
                "operands": {
                    "market_capitalization": format(market_cap, "f"),
                    "mrq_attributable_equity": format(mrq_equity, "f"),
                },
            }
        )
    if forecast_year is not None and forecast_eps is not None and forecast_eps > 0:
        forward_pe = decimal_ratio(price, forecast_eps)
        calculations.append(
            {
                "id": "forward_pe",
                "formula": "completed_unadjusted_close / first_forecast_year_consensus_eps",
                "operands": {
                    "price": format(price, "f"),
                    "forecast_year": forecast_year,
                    "consensus_eps": format(forecast_eps, "f"),
                },
            }
        )
    if (
        forecast_year is not None
        and forecast_eps is not None
        and forecast_eps > 0
        and next_year is not None
        and next_eps is not None
    ):
        growth = decimal_ratio(next_eps, forecast_eps) - Decimal(1)
        calculations.append(
            {
                "id": "forecast_eps_growth",
                "formula": "next_year_eps / first_forecast_year_eps - 1",
                "operands": {
                    "first_year": forecast_year,
                    "first_year_eps": format(forecast_eps, "f"),
                    "next_year": next_year,
                    "next_year_eps": format(next_eps, "f"),
                },
            }
        )
        if growth > 0:
            calculations.extend(
                (
                    {
                        "id": "peg",
                        "formula": "forward_pe / (forecast_eps_growth_percent)",
                        "operands": {
                            "forward_pe": format(forward_pe, "f"),
                            "forecast_eps_growth_percent": format(
                                growth * Decimal(100), "f"
                            ),
                        },
                    },
                    {
                        "id": "pe_digestion_years",
                        "formula": (
                            "ln(forward_pe / target_pe) / ln(1 + forecast_eps_growth)"
                        ),
                        "operands": {
                            "forward_pe": format(forward_pe, "f"),
                            "forecast_eps_growth": format(growth, "f"),
                            "target_pe": format(target_pe, "f"),
                        },
                    },
                )
            )
    return calculations


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


_VALUATION_RAW_FIELDS: dict[str, tuple[str, ...]] = {
    "sina_financial_statements@1": (
        "publish_date",
        "rType",
        "rCurrency",
        "is_audit",
        "audit_opinion",
        "data_source",
        "update_time",
        "item_title",
        "item_value",
        "item_tongbi",
    ),
    "ths_consensus_eps@1": (
        "forecast_year",
        "institutions",
        "minimum",
        "mean",
        "maximum",
    ),
    "reported_financial_selection@1": ("statement_periods",),
    "consensus_forecast_selection@1": ("forecast_years", "institutions"),
}
_VALUATION_REQUIRED_OPERATIONS = (
    "eastmoney_stock_info@1",
    "sina_financial_statements@1",
    "ths_consensus_eps@1",
)
_FINANCIAL_EVIDENCE_STORE_VERSION = "valuation-financial-statements/1.0"


def _validate_valuation_evidence_storage_authority(
    result: Mapping[str, object],
) -> None:
    plan = result.get("plan")
    anchor = result.get("research_anchor")
    coverage = result.get("coverage")
    observations = result.get("observations")
    if not isinstance(plan, Mapping) or not isinstance(anchor, Mapping):
        raise ValueError("valuation evidence storage authority is invalid")
    plan_audit = plan.get("evidence_storage")
    if plan_audit is None:
        return
    if (
        not isinstance(plan_audit, Mapping)
        or not isinstance(coverage, Mapping)
        or not isinstance(observations, (list, tuple))
    ):
        raise ValueError("valuation evidence storage authority is invalid")
    request_scope = plan_audit.get("request_scope")
    if (
        not isinstance(request_scope, Mapping)
        or request_scope.get("task_type") != "security_valuation"
        or request_scope.get("as_of") != anchor.get("as_of")
        or request_scope.get("window") is not None
    ):
        raise ValueError("valuation evidence storage authority is invalid")
    for capability_id, aggregate in coverage.items():
        if not isinstance(aggregate, Mapping) or not isinstance(
            aggregate.get("operations"), Mapping
        ):
            continue
        operations = cast(Mapping[str, object], aggregate["operations"])
        for operation_id, operation in operations.items():
            if not isinstance(operation, Mapping):
                continue
            audit = operation.get("evidence_storage")
            if audit is None:
                if (
                    capability_id == "security_valuation_metrics"
                    and operation_id == SinaFinancialStatementsOperation.operation_id
                    and operation.get("execution_state") in {"succeeded", "failed"}
                ):
                    raise ValueError("valuation evidence storage authority is invalid")
                continue
            if (
                capability_id != "security_valuation_metrics"
                or operation_id != SinaFinancialStatementsOperation.operation_id
                or operation.get("execution_state") not in {"succeeded", "failed"}
                or not isinstance(audit, Mapping)
                or audit.get("source_version") != _FINANCIAL_EVIDENCE_STORE_VERSION
                or "reuse_facts" in audit
            ):
                raise ValueError("valuation evidence storage authority is invalid")
            owned_subjects: set[str] = set()
            for record in observations:
                if (
                    not isinstance(record, Mapping)
                    or record.get("capability_id") != "security_valuation_metrics"
                    or record.get("operation_id") != operation_id
                    or not isinstance(record.get("payload"), Mapping)
                ):
                    continue
                payload = cast(Mapping[str, object], record["payload"])
                subject = payload.get("subject")
                if isinstance(subject, Mapping) and isinstance(
                    subject.get("security"), str
                ):
                    owned_subjects.add(cast(str, subject["security"]))
            if owned_subjects and audit.get("subject_identity") not in owned_subjects:
                raise ValueError("valuation evidence storage authority is invalid")


def _valuation_dispositions(operation_id: str) -> tuple[FieldDisposition, ...]:
    if operation_id == EastmoneyStockInfoOperation.operation_id:
        return EastmoneyStockInfoOperation.field_dispositions
    destinations = {
        "publish_date": "observations.payload.report.publication_date",
        "rType": "observations.payload.report.scope",
        "rCurrency": "observations.payload.observed_value.unit",
        "is_audit": "observations.payload.report.audit_status",
        "data_source": "observations.payload.report.data_source",
        "update_time": "observations.payload.report.source_update_time",
        "item_title": "observations.payload.full_statement_items[].label",
        "item_value": "observations.payload.full_statement_items[].value",
        "forecast_year": "observations.payload.observed_value.value[].year",
        "institutions": "observations.payload.observed_value.value[].institutions",
        "minimum": "observations.payload.observed_value.value[].minimum",
        "mean": "observations.payload.observed_value.value[].mean",
        "maximum": "observations.payload.observed_value.value[].maximum",
        "statement_periods": "observations.payload.statement_periods",
        "forecast_years": "observations.payload.forecast_years",
    }
    result = []
    for field in _VALUATION_RAW_FIELDS[operation_id]:
        if field in {"audit_opinion", "item_tongbi"}:
            result.append(
                FieldDisposition(
                    field,
                    "excluded",
                    None,
                    "The provider field is not an approved deterministic operand.",
                )
            )
        else:
            result.append(
                FieldDisposition(
                    field,
                    (
                        "cross_check_only"
                        if field in {"provider_market_cap", "provider_price"}
                        else "normalized"
                    ),
                    destinations[field],
                )
            )
    return tuple(result)


def _valuation_outcome(
    operation_id: str,
    *,
    observations: tuple[dict[str, Any], ...] = (),
    error: dict[str, Any] | None = None,
    skipped: str | None = None,
    diagnostics: dict[str, object] | None = None,
) -> OperationOutcome:
    if (bool(observations) + (error is not None) + (skipped is not None)) != 1:
        raise ValueError("valuation outcome requires exactly one execution fact")
    if error is not None:
        execution = OperationExecution("failed")
        errors: tuple[dict[str, Any], ...] = (error,)
    elif skipped is not None:
        execution = OperationExecution(cast(Any, skipped))
        errors = ()
    else:
        execution = OperationExecution("succeeded", query_complete=True)
        errors = ()
    return OperationOutcome(
        capability_id="security_valuation_metrics",
        operation_id=operation_id,
        execution=execution,
        observations=observations,
        source_errors=errors,
        field_dispositions=_valuation_dispositions(operation_id),
        diagnostics=diagnostics or {},
    )


def _valuation_calculation_keys(
    calculation_kind: str, evidence: list[dict[str, Any]]
) -> tuple[str, ...]:
    official_close = {"sse_daily_line@1", "szse_daily_line@1"}
    operations = {
        "market_capitalization": official_close | {"eastmoney_stock_info@1"},
        "pe_ttm": official_close
        | {"eastmoney_stock_info@1", "sina_financial_statements@1"},
        "pb_mrq": official_close
        | {"eastmoney_stock_info@1", "sina_financial_statements@1"},
        "forward_pe": official_close | {"ths_consensus_eps@1"},
        "forecast_eps_growth": {"ths_consensus_eps@1"},
        "peg": official_close | {"ths_consensus_eps@1"},
        "pe_digestion_years": official_close | {"ths_consensus_eps@1"},
    }.get(calculation_kind, set())
    return tuple(
        str(item["id"])
        for item in evidence
        if isinstance(item.get("id"), str)
        and item.get("source_operation") in operations
    )


def _valuation_observation_keys(
    outcomes: tuple[OperationOutcome, ...],
) -> tuple[str, ...]:
    return tuple(
        str(local_key)
        for outcome in outcomes
        for index, observation in enumerate(outcome.observations)
        for local_key in (
            observation.get("id")
            if isinstance(observation.get("id"), str) and observation.get("id")
            else f"{outcome.operation_id}:{index}",
        )
    )


def _blocked(
    request: dict[str, Any],
    code: str,
    message: str,
    *,
    subjects: list[dict[str, Any]] | None = None,
    outcomes: tuple[OperationOutcome, ...] = (),
    conflicts: list[Any] | None = None,
    limitations: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
) -> UnifiedResearchResult:
    return _blocked_execution(
        request,
        code,
        message,
        subjects=subjects,
        outcomes=outcomes,
        conflicts=conflicts,
        limitations=limitations,
        metrics=metrics,
    ).result


def _blocked_execution(
    request: dict[str, Any],
    code: str,
    message: str,
    *,
    subjects: list[dict[str, Any]] | None = None,
    outcomes: tuple[OperationOutcome, ...] = (),
    conflicts: list[Any] | None = None,
    limitations: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    evidence_store: EvidenceStoreRequest | None = None,
) -> SecurityValuationExecution:
    result_limitations = list(limitations or [])
    result_limitations.append({"code": code, "message": message})
    keys = _valuation_observation_keys(outcomes)
    return _direct_valuation_execution(
        request,
        outcomes=outcomes,
        conflicts=conflicts or [],
        limitations=_deduplicate_limitations(result_limitations),
        claims=(
            [
                DirectTaskClaim(
                    kind="security_valuation_diagnostics",
                    evidence_use="security_valuation",
                    observation_keys=keys,
                    payload={
                        "subjects": (
                            subjects if subjects is not None else request["subjects"]
                        ),
                        "valuation_basis": {"status": "unresolved"},
                        "metrics": metrics or {},
                    },
                )
            ]
            if metrics
            else []
        ),
        evidence_store=evidence_store,
    )


def _direct_valuation_result(
    request: dict[str, Any],
    *,
    outcomes: tuple[OperationOutcome, ...],
    conflicts: list[Any],
    limitations: list[dict[str, Any]],
    claims: list[DirectTaskClaim],
    calculations: list[DirectTaskCalculation] | None = None,
) -> UnifiedResearchResult:
    return _direct_valuation_execution(
        request,
        outcomes=outcomes,
        conflicts=conflicts,
        limitations=limitations,
        claims=claims,
        calculations=calculations,
    ).result


def _direct_valuation_execution(
    request: dict[str, Any],
    *,
    outcomes: tuple[OperationOutcome, ...],
    conflicts: list[Any],
    limitations: list[dict[str, Any]],
    claims: list[DirectTaskClaim],
    calculations: list[DirectTaskCalculation] | None = None,
    task_type: str = "security_valuation",
    evidence_store: EvidenceStoreRequest | None = None,
) -> SecurityValuationExecution:
    outcomes_by_owner = {
        (item.capability_id, item.operation_id): item for item in outcomes
    }
    if len(outcomes_by_owner) != len(outcomes):
        raise ValueError("valuation outcomes must be uniquely owned")
    existing_operations = {item.operation_id for item in outcomes}
    missing_state = (
        "skipped_policy"
        if not request["source_policy"]["allow_experimental"]
        else "skipped_research_dependency"
    )
    for operation_id in _VALUATION_REQUIRED_OPERATIONS:
        if (
            task_type == "security_valuation"
            and operation_id not in existing_operations
        ):
            missing = _valuation_outcome(operation_id, skipped=missing_state)
            outcomes_by_owner[(missing.capability_id, missing.operation_id)] = missing
    ordered_outcomes = tuple(outcomes_by_owner.values())
    obligations = []
    for outcome in ordered_outcomes:
        base_operation = (
            _valuation_member_producer_operation_id(outcome.operation_id)
            if task_type == "valuation_compare"
            else outcome.operation_id
        )
        evidence_role = (
            "lineage"
            if base_operation in {"sse_stock_list@1", "szse_stock_list@1"}
            else "corroborating"
            if base_operation
            in {"cninfo_security_dictionary@1", "tencent_daily_line@1"}
            else "supporting"
        )
        source_role = (
            "authoritative_disclosure"
            if base_operation in {"sse_stock_list@1", "szse_stock_list@1"}
            else "signed_opinion"
            if base_operation == "ths_consensus_eps@1"
            else "market_observation"
        )
        obligations.append(
            OperationObligation(
                capability_id=outcome.capability_id,
                operation_id=outcome.operation_id,
                source_role=source_role,
                experimental=True,
                exchange=None,
                evidence_uses=(
                    EvidenceUsePolicy(
                        (
                            "valuation_compare"
                            if task_type == "valuation_compare"
                            else "security_valuation"
                        ),
                        cast(Any, evidence_role),
                    ),
                ),
                producer_operation_id=base_operation,
            )
        )
    known_keys = {
        str(observation["id"])
        for outcome in ordered_outcomes
        for observation in outcome.observations
        if isinstance(observation.get("id"), str)
    }
    evidence_use = (
        "valuation_compare"
        if task_type == "valuation_compare"
        else "security_valuation"
    )
    dependencies = [
        ResearchDependency(
            consumer_id=(
                "valuation_compare_members"
                if task_type == "valuation_compare"
                else "security_valuation_metrics"
            ),
            evidence_use=evidence_use,
            minimum_supporting_observations=1,
            core=True,
        )
    ]
    if task_type == "security_valuation":
        dependencies.extend(
            ResearchDependency(
                consumer_id=metric,
                evidence_use=evidence_use,
                minimum_supporting_observations=1,
                core=False,
            )
            for metric in COMPARISON_METRIC_ORDER
        )
    result = fold_direct_task_research_result(
        DirectTaskPlan(
            task_type=task_type,
            as_of=str(request["as_of"]),
            obligations=tuple(obligations),
            dependencies=tuple(dependencies),
            evidence_storage=(
                None if evidence_store is None else evidence_store.plan_audit()
            ),
        ),
        ordered_outcomes,
        claims=tuple(claims),
        calculations=tuple(calculations or ()),
        conflicts=tuple(
            DirectTaskConflict(
                code=str(item.get("code", "valuation_conflict")),
                message=str(item.get("message", "Valuation observations conflict.")),
                observation_keys=tuple(
                    key
                    for key in item.get("evidence_ids", [])
                    if isinstance(key, str) and key in known_keys
                ),
            )
            for item in conflicts
            if isinstance(item, dict)
        ),
        limitations=tuple(
            DirectTaskLimitation(
                code=str(item.get("code", "valuation_limited")),
                message=str(item.get("message", "Valuation research is limited.")),
            )
            for item in limitations
        ),
        summary=(
            "Deterministic valuation metrics and their operands were retained."
            if any(
                item.kind in {"security_valuation_metrics", "valuation_compare_members"}
                for item in claims
            )
            else "The requested security valuation could not be established safely."
        ),
    )
    return SecurityValuationExecution(
        result=result,
        outcomes=ordered_outcomes,
        claims=tuple(claims),
        calculations=tuple(calculations or ()),
        conflicts=tuple(item for item in conflicts if isinstance(item, dict)),
        limitations=tuple(limitations),
        material=_valuation_member_material(
            task_type=task_type,
            outcomes=ordered_outcomes,
            claims=tuple(claims),
            limitations=tuple(limitations),
        ),
    )


def _valuation_member_material(
    *,
    task_type: str,
    outcomes: tuple[OperationOutcome, ...],
    claims: tuple[DirectTaskClaim, ...],
    limitations: tuple[dict[str, Any], ...],
) -> ValuationMemberMaterial:
    member_claim = next(
        (
            item
            for item in claims
            if item.kind
            in {"security_valuation_metrics", "security_valuation_diagnostics"}
        ),
        None,
    )
    security: str | None = None
    name: str | None = None
    trading_date: str | None = None
    metrics: dict[str, Any] = {}
    if member_claim is not None:
        subjects = member_claim.payload.get("subjects")
        subject = (
            subjects[0]
            if isinstance(subjects, list) and subjects and isinstance(subjects[0], dict)
            else None
        )
        security_value = subject.get("security") if subject is not None else None
        if isinstance(security_value, dict):
            exchange = security_value.get("exchange")
            code = security_value.get("code")
            if isinstance(exchange, str) and isinstance(code, str):
                security = f"{exchange}:{code}"
        if subject is not None and isinstance(subject.get("name"), str):
            name = str(subject["name"])
        valuation_basis = member_claim.payload.get("valuation_basis")
        if isinstance(valuation_basis, dict) and isinstance(
            valuation_basis.get("trading_date"), str
        ):
            trading_date = str(valuation_basis["trading_date"])
        claim_metrics = member_claim.payload.get("metrics")
        if isinstance(claim_metrics, dict):
            metrics = {str(key): dict(value) for key, value in claim_metrics.items()}
    status: ResearchStatus = (
        "limited"
        if task_type == "security_valuation"
        and member_claim is not None
        and member_claim.kind == "security_valuation_metrics"
        else "blocked"
    )
    return ValuationMemberMaterial(
        status=status,
        security=security,
        name=name,
        trading_date=trading_date,
        metrics=metrics,
        limitation_codes=tuple(
            str(item["code"])
            for item in limitations
            if isinstance(item.get("code"), str)
        ),
        observation_keys=_valuation_observation_keys(outcomes),
    )


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
