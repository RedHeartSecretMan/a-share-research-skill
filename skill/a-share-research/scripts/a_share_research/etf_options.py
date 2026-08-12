"""Coordinate ETF-option contracts, quotes, ATM selection, and analytics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Collection, Mapping, cast

from .etf_option_contract import (
    OPTION_ANALYTIC_NAMES,
    EtfOptionSubject,
    OptionAcquisitionOwners,
    OptionContractQuote,
    OptionQuery,
    OptionSourceBatch,
    OptionSourceOperation,
    json_safe_copy,
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
    UnifiedResearchResult,
    fold_direct_task_research_result,
    stable_result_id,
)

SUPPORTED_ETF_CODES = frozenset({"510050", "510300", "510500", "588000"})
REQUIRED_ANALYTICS = frozenset(
    {"delta", "gamma", "theta", "vega", "implied_volatility"}
)
ANALYTIC_UNITS = {
    "delta": "dimensionless",
    "gamma": "provider_native_unverified",
    "theta": "provider_native_unverified",
    "vega": "provider_native_unverified",
    "implied_volatility": "decimal_fraction",
    "theoretical_value": "CNY/share",
}

_DEFAULT_OWNERS = OptionAcquisitionOwners(
    executor_operation="sina_etf_option_snapshot@1",
    identity_operation="sse_etf_list@1",
    reference_operation="sse_etf_snapshot@1",
    option_operation="sina_etf_option_snapshot@1",
)
_EVIDENCE_USES = (
    "etf_option_identity",
    "etf_option_selection",
    "etf_option_quote",
    "etf_option_analytics",
    "etf_option_atm",
)
_IDENTITY_RAW_FIELDS = (
    "fundCode",
    "fundAbbr",
    "secNameFull",
    "companyName",
    "listingDate",
    "fundType",
    "subClass",
)
_REFERENCE_RAW_FIELDS = (
    "name",
    "cpxxextendname",
    "open",
    "high",
    "low",
    "last",
    "prev_close",
    "chg_rate",
    "volume",
    "amount",
    "tradephase",
    "code",
    "date",
    "time",
)


def _declared_owners(operation: OptionSourceOperation) -> OptionAcquisitionOwners:
    owners = getattr(operation, "acquisition_owners", None)
    if not isinstance(owners, OptionAcquisitionOwners):
        raise ValueError("ETF-option operation must declare acquisition owners")
    if operation.operation_id != owners.executor_operation:
        raise ValueError("ETF-option executor contradicts its declared owner map")
    return owners


def _declared_raw_fields(operation: OptionSourceOperation) -> tuple[str, ...]:
    raw_fields = getattr(operation, "raw_fields", None)
    if (
        not isinstance(raw_fields, tuple)
        or not raw_fields
        or any(not isinstance(item, str) or not item for item in raw_fields)
        or len(raw_fields) != len(set(raw_fields))
    ):
        raise ValueError("ETF-option operation must declare unique active raw fields")
    return raw_fields


def _batch_matches_declared_owners(
    batch: OptionSourceBatch, expected: OptionAcquisitionOwners
) -> bool:
    if batch.owners != expected or batch.operation_id != expected.executor_operation:
        return False
    if (
        batch.subject is not None
        and batch.subject.identity_source_operation != expected.identity_operation
    ):
        return False
    if (
        batch.session is not None
        and batch.session.reference_source_operation != expected.reference_operation
    ):
        return False
    option_children = (
        *batch.contracts,
        *batch.listing_evidence,
        *(() if batch.month_evidence is None else (batch.month_evidence,)),
    )
    if any(
        item.source_operation != expected.option_operation for item in option_children
    ):
        return False
    allowed_error_owners = {
        expected.identity_operation,
        expected.reference_operation,
        expected.option_operation,
    }
    return not any(
        item.source_operation not in allowed_error_owners
        for item in (*batch.source_errors, *batch.degradations)
    )


def _dispositions(
    operation_id: str,
    owners: OptionAcquisitionOwners,
    raw_fields: tuple[str, ...],
) -> tuple[FieldDisposition, ...]:
    if operation_id == owners.identity_operation:
        normalized = {
            "fundCode": "observations.payload.subject.security.code",
            "secNameFull": "observations.payload.subject.name",
            "listingDate": "observations.payload.observed_on",
        }
        excluded = {
            "fundAbbr": "The ETF abbreviation is not used or retained.",
            "companyName": "The fund manager is outside the ETF-option fact set.",
            "fundType": "The ETF type is used for qualification but not retained.",
            "subClass": "The ETF subclass is used for qualification but not retained.",
        }
        return tuple(
            FieldDisposition(
                field,
                "normalized" if field in normalized else "excluded",
                normalized.get(field),
                excluded.get(field),
            )
            for field in _IDENTITY_RAW_FIELDS
        )
    if operation_id == owners.reference_operation:
        normalized = {
            "last": "observations.payload.reference_price",
            "date": "observations.payload.trading_date",
            "time": "observations.payload.observed_at",
        }
        cross_checks = {"code": "observations.payload.subject.security.code"}
        excluded = {
            "name": "The short name is schema-validated but not retained.",
            "cpxxextendname": (
                "The extended name is used for identity comparison but is not "
                "retained in the public result."
            ),
            "open": "The open price is outside the ETF-option reference fact.",
            "high": "The high price is outside the ETF-option reference fact.",
            "low": "The low price is outside the ETF-option reference fact.",
            "prev_close": "The previous close is outside the ETF-option reference fact.",
            "chg_rate": "The change rate is outside the ETF-option reference fact.",
            "volume": "ETF volume is outside the ETF-option reference fact.",
            "amount": "ETF turnover is outside the ETF-option reference fact.",
            "tradephase": "The trade phase is validated but not retained.",
        }
        return tuple(
            FieldDisposition(
                field,
                "normalized"
                if field in normalized
                else "cross_check_only"
                if field in cross_checks
                else "excluded",
                normalized.get(field, cross_checks.get(field)),
                excluded.get(field),
            )
            for field in _REFERENCE_RAW_FIELDS
        )
    fields = raw_fields
    if not fields:
        return (
            FieldDisposition(
                "operation_not_executed",
                "excluded",
                None,
                "No source operation was available to declare active raw fields.",
            ),
        )
    normalized = {
        "months.result.data.contractMonth[]": "observations.payload.observed_months",
        "listing[]": "observations.payload.contract_codes",
        "quote[0]": "observations.payload.quote.bid_size",
        "quote[1]": "observations.payload.quote.bid",
        "quote[2]": "observations.payload.quote.last",
        "quote[3]": "observations.payload.quote.ask",
        "quote[4]": "observations.payload.quote.ask_size",
        "quote[5]": "observations.payload.quote.open_interest",
        "quote[7]": "observations.payload.contract.strike",
        "quote[32]": "observations.payload.quote.observed_at",
        "quote[41]": "observations.payload.quote.volume",
        "quote[43]": "observations.payload.contract.series",
        "quote[45]": "observations.payload.contract.option_type",
        "quote[46]": "observations.payload.contract.expiry_date",
        "analytics[5]": "observations.payload.analytics.delta",
        "analytics[6]": "observations.payload.analytics.gamma",
        "analytics[7]": "observations.payload.analytics.theta",
        "analytics[8]": "observations.payload.analytics.vega",
        "analytics[9]": "observations.payload.analytics.implied_volatility",
        "analytics[15]": "observations.payload.analytics.theoretical_value",
    }
    cross_checks = {
        "months.result.data.stockId": "observations.payload.subject_code",
        "quote[36]": "observations.payload.subject_code",
        "analytics[4]": "observations.payload.quote.volume",
        "analytics[12]": "observations.payload.contract.security.code",
        "analytics[13]": "observations.payload.contract.strike",
        "analytics[14]": "observations.payload.quote.last",
        "analytics[16]": "observations.payload.contract.series",
    }
    excluded = {
        "months.result.status.code": (
            "The provider status code is used only for source-contract validation."
        ),
        "analytics[1]": (
            "The empty provider slot is used only for shifted-schema validation."
        ),
        "analytics[2]": (
            "The empty provider slot is used only for shifted-schema validation."
        ),
        "analytics[3]": (
            "The empty provider slot is used only for shifted-schema validation."
        ),
    }
    return tuple(
        FieldDisposition(
            field,
            "excluded"
            if field in excluded
            else "cross_check_only"
            if field in cross_checks
            else "normalized",
            None
            if field in excluded
            else normalized.get(field, cross_checks.get(field, "observations.payload")),
            excluded.get(field),
        )
        for field in fields
    )


def _operation_obligations(
    owners: OptionAcquisitionOwners,
) -> tuple[OperationObligation, ...]:
    contracts = (
        (
            "etf_option_identity",
            owners.identity_operation,
            "authoritative_disclosure",
            {"etf_option_identity": "supporting"},
        ),
        (
            "etf_option_reference",
            owners.reference_operation,
            "market_observation",
            {"etf_option_atm": "supporting"},
        ),
        (
            "etf_option_contracts",
            owners.option_operation,
            "market_observation",
            {
                "etf_option_selection": "supporting",
                "etf_option_quote": "supporting",
                "etf_option_analytics": "supporting",
                "etf_option_atm": "supporting",
            },
        ),
    )
    return tuple(
        OperationObligation(
            capability_id=capability_id,
            operation_id=operation_id,
            source_role=source_role,
            experimental=True,
            exchange="SSE",
            evidence_uses=tuple(
                EvidenceUsePolicy(
                    evidence_use,
                    cast(Any, roles.get(evidence_use, "not_applicable")),
                )
                for evidence_use in _EVIDENCE_USES
            ),
        )
        for capability_id, operation_id, source_role, roles in contracts
    )


def _option_plan(
    request: Mapping[str, object], owners: OptionAcquisitionOwners
) -> DirectTaskPlan:
    return DirectTaskPlan(
        task_type="etf_options",
        as_of=cast(str, request["as_of"]),
        obligations=_operation_obligations(owners),
        dependencies=(
            ResearchDependency("etf_option_identity", "etf_option_identity", 1, True),
            ResearchDependency("etf_option_selection", "etf_option_selection", 1, True),
            ResearchDependency("etf_option_quote", "etf_option_quote", 2, True),
            ResearchDependency(
                "etf_option_analytics", "etf_option_analytics", 1, False
            ),
            ResearchDependency("etf_option_atm", "etf_option_atm", 2, True),
        ),
    )


def _outcome(
    owners: OptionAcquisitionOwners,
    raw_fields: tuple[str, ...],
    operation_id: str,
    *,
    observations: tuple[Mapping[str, object], ...] = (),
    errors: tuple[Mapping[str, object], ...] = (),
    skipped: str | None = None,
    query_complete: bool = True,
) -> OperationOutcome:
    capability_id = next(
        item.capability_id
        for item in _operation_obligations(owners)
        if item.operation_id == operation_id
    )
    execution = (
        OperationExecution(cast(Any, skipped))
        if skipped is not None
        else OperationExecution("failed")
        if errors
        else OperationExecution("succeeded", query_complete=query_complete)
    )
    return OperationOutcome(
        capability_id=capability_id,
        operation_id=operation_id,
        execution=execution,
        observations=observations,
        source_errors=errors,
        field_dispositions=_dispositions(operation_id, owners, raw_fields),
    )


def _skipped_option_outcomes(
    owners: OptionAcquisitionOwners,
    raw_fields: tuple[str, ...],
    state: str,
) -> tuple[OperationOutcome, ...]:
    return tuple(
        _outcome(owners, raw_fields, operation_id, skipped=state)
        for operation_id in (
            owners.identity_operation,
            owners.reference_operation,
            owners.option_operation,
        )
    )


def _quarantined_option_outcomes(
    owners: OptionAcquisitionOwners, raw_fields: tuple[str, ...]
) -> tuple[OperationOutcome, ...]:
    local_error = {
        "source_operation": owners.option_operation,
        "code": "option_source_owner_mismatch",
        "message": "The source batch contradicted its externally declared owners.",
    }
    return (
        _outcome(
            owners,
            raw_fields,
            owners.identity_operation,
            skipped="skipped_research_dependency",
        ),
        _outcome(
            owners,
            raw_fields,
            owners.reference_operation,
            skipped="skipped_research_dependency",
        ),
        _outcome(
            owners,
            raw_fields,
            owners.option_operation,
            errors=(local_error,),
        ),
    )


def _direct_option_result(
    request: Mapping[str, object],
    *,
    owners: OptionAcquisitionOwners,
    raw_fields: tuple[str, ...],
    outcomes: tuple[OperationOutcome, ...],
    claims: tuple[DirectTaskClaim, ...] = (),
    calculations: tuple[DirectTaskCalculation, ...] = (),
    conflicts: tuple[DirectTaskConflict, ...] = (),
    limitations: tuple[DirectTaskLimitation, ...] = (),
    summary: str = "ETF-option research could not establish an adoptable contract view.",
) -> UnifiedResearchResult:
    del raw_fields
    return fold_direct_task_research_result(
        _option_plan(request, owners),
        outcomes,
        claims=claims,
        calculations=calculations,
        conflicts=conflicts,
        limitations=limitations,
        summary=summary,
    )


def _local_observation(kind: str, payload: Mapping[str, object]) -> dict[str, object]:
    content = {"kind": kind, **dict(payload)}
    return {"id": stable_result_id("option-local", content), **content}


def _identity_observation(subject: EtfOptionSubject, owner: str) -> dict[str, object]:
    return _local_observation(
        "etf_option_identity",
        {
            "source_operation": owner,
            "subject": {
                "security": {
                    "exchange": subject.exchange,
                    "code": subject.code,
                    "type": "ETF",
                },
                "name": subject.name,
            },
            "observed_on": subject.identity_observed_on,
            "retrieved_at": (
                None
                if subject.identity_retrieved_at is None
                else subject.identity_retrieved_at.isoformat()
            ),
            "locator": {"uri": subject.identity_locator_uri},
            "provenance": {"provider_record_id": subject.identity_evidence_id},
        },
    )


def _reference_observation(batch: OptionSourceBatch, owner: str) -> dict[str, object]:
    assert batch.subject is not None and batch.session is not None
    session = batch.session
    return _local_observation(
        "etf_option_reference",
        {
            "source_operation": owner,
            "subject": {
                "security": {
                    "exchange": batch.subject.exchange,
                    "code": batch.subject.code,
                    "type": "ETF",
                }
            },
            "trading_date": session.trading_date,
            "observed_at": session.reference_observed_at,
            "market_state": session.market_state,
            "reference_price": {
                "value": session.reference_price,
                "unit": "CNY/share",
                "kind": session.reference_price_kind,
            },
            "retrieved_at": (
                session.reference_retrieved_at or session.retrieved_at
            ).isoformat(),
            "locator": {"uri": session.locator_uri},
            "provenance": {"provider_record_id": session.reference_evidence_id},
        },
    )


def _month_observation(
    batch: OptionSourceBatch, owner: str
) -> dict[str, object] | None:
    item = batch.month_evidence
    if item is None:
        return None
    return _local_observation(
        "etf_option_contract_months",
        {
            "source_operation": owner,
            "subject_code": None if batch.subject is None else batch.subject.code,
            "observed_months": list(item.observed_months),
            "identity_status": item.identity_status,
            "retrieved_at": item.retrieved_at.isoformat(),
            "locator": {"uri": item.locator_uri},
            "provenance": {"provider_record_id": item.evidence_id},
        },
    )


def _listing_observation(
    item: object, owner: str, subject_code: str | None
) -> dict[str, object]:
    listing = cast(Any, item)
    return _local_observation(
        "etf_option_contract_listing",
        {
            "source_operation": owner,
            "subject_code": subject_code,
            "option_type": listing.option_type,
            "contract_month": listing.contract_month,
            "observed_count": listing.observed_count,
            "contract_codes": list(listing.contract_codes),
            "authoritative_total_available": False,
            "retrieved_at": listing.retrieved_at.isoformat(),
            "locator": {"uri": listing.locator_uri},
            "provenance": {"provider_record_id": listing.evidence_id},
        },
    )


def _contract_fact(item: OptionContractQuote) -> dict[str, object]:
    return {
        "security": json_safe_copy(item.security),
        "option_type": item.option_type,
        "strike": {"value": item.strike, "unit": "CNY/share"},
        "contract_month": item.contract_month,
        "expiry_date": item.expiry_date,
        "series": item.series,
    }


def _quote_fact(item: OptionContractQuote) -> dict[str, object]:
    return {
        "state": item.quote_state,
        "observed_at": item.observed_at,
        "last": _price(item.last),
        "bid": _price(item.bid),
        "ask": _price(item.ask),
        "bid_size": _contracts(item.bid_size),
        "ask_size": _contracts(item.ask_size),
        "volume": _contracts(item.volume),
        "open_interest": _contracts(item.open_interest),
    }


def _quote_observation(item: OptionContractQuote, owner: str) -> dict[str, object]:
    return _local_observation(
        "etf_option_quote",
        {
            "source_operation": owner,
            "contract": _contract_fact(item),
            "quote": _quote_fact(item),
            "retrieved_at": (
                None
                if item.quote_retrieved_at is None
                else item.quote_retrieved_at.isoformat()
            ),
            "locator": {"uri": item.locator_uri},
            "limitations": list(item.limitations),
            "provenance": {"provider_record_id": item.evidence_id},
        },
    )


def _analytics_observation(
    item: OptionContractQuote, owner: str
) -> dict[str, object] | None:
    if not item.analytics:
        return None
    return _local_observation(
        "etf_option_analytics",
        {
            "source_operation": owner,
            "contract": _contract_fact(item),
            "analytics": {
                name: {
                    "status": "observed",
                    "value": analytic.value,
                    "unit": analytic.unit,
                    "origin": analytic.origin,
                }
                for name, analytic in sorted(item.analytics.items())
            },
            "retrieved_at": (
                None
                if item.analytics_retrieved_at is None
                else item.analytics_retrieved_at.isoformat()
            ),
            "locator": {"uri": item.analytics_locator_uri},
            "provenance": {
                "provider_record_id": item.analytics_evidence_id,
                "provider_contract_id": item.evidence_id,
            },
        },
    )


def _deduplicate_observations(
    observations: Collection[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    by_key = {str(item["id"]): item for item in observations}
    return tuple(by_key[key] for key in sorted(by_key))


def _owned_errors(
    batch: OptionSourceBatch, owner: str
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "source_operation": owner,
            "code": item.code,
            "message": item.message,
        }
        for item in batch.source_errors
        if item.source_operation == owner
    )


def _option_outcomes(
    batch: OptionSourceBatch,
    owners: OptionAcquisitionOwners,
    raw_fields: tuple[str, ...],
) -> tuple[OperationOutcome, ...]:
    identity_observations = (
        ()
        if batch.subject is None
        else (_identity_observation(batch.subject, owners.identity_operation),)
    )
    reference_observations = (
        ()
        if batch.subject is None or batch.session is None
        else (_reference_observation(batch, owners.reference_operation),)
    )
    option_observations: list[Mapping[str, object]] = []
    month = _month_observation(batch, owners.option_operation)
    if month is not None:
        option_observations.append(month)
    option_observations.extend(
        _listing_observation(
            item,
            owners.option_operation,
            None if batch.subject is None else batch.subject.code,
        )
        for item in batch.listing_evidence
    )
    for item in batch.contracts:
        option_observations.append(_quote_observation(item, owners.option_operation))
        analytics = _analytics_observation(item, owners.option_operation)
        if analytics is not None:
            option_observations.append(analytics)
    outcomes: list[OperationOutcome] = []
    for operation_id, observations in (
        (owners.identity_operation, identity_observations),
        (owners.reference_operation, reference_observations),
        (owners.option_operation, _deduplicate_observations(option_observations)),
    ):
        errors = _owned_errors(batch, operation_id)
        outcomes.append(
            _outcome(
                owners,
                raw_fields,
                operation_id,
                observations=tuple(observations),
                errors=errors,
                query_complete=(
                    not errors
                    and (
                        operation_id != owners.option_operation
                        or all(
                            item.state in {"observed_nonempty", "observed_empty"}
                            for item in batch.coverage.values()
                        )
                    )
                ),
            )
        )
    return tuple(outcomes)


def _observations_by_kind(
    outcomes: Collection[OperationOutcome], kind: str
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        item
        for outcome in outcomes
        for item in outcome.observations
        if item.get("kind") == kind
    )


def _has_only_local_analytics_failures(batch: OptionSourceBatch) -> bool:
    analytics_coverage = batch.coverage.get("provider_analytics")
    quote_coverage = batch.coverage.get("option_quotes")
    contract_count = len(batch.contracts)
    if (
        analytics_coverage is None
        or analytics_coverage.state != "partial"
        or analytics_coverage.expected_count != contract_count
        or not any(item.analytics for item in batch.contracts)
        or quote_coverage is None
        or quote_coverage.state != "observed_nonempty"
        or quote_coverage.expected_count != contract_count
        or quote_coverage.observed_count != contract_count
        or any(item.quote_state != "quoted" for item in batch.contracts)
    ):
        return False
    if batch.owners is None:
        return False
    option_owner = batch.owners.option_operation
    contract_codes = {str(item.security["code"]) for item in batch.contracts}
    for failure in batch.source_errors:
        if failure.source_operation != option_owner:
            return False
        affected_many = failure.details.get("contract_codes")
        if isinstance(affected_many, tuple) and affected_many:
            if any(
                key in failure.details
                for key in ("scope", "contract_code", "analytics_field")
            ):
                return False
            if not {str(item) for item in affected_many}.issubset(contract_codes):
                return False
            continue
        affected_one = failure.details.get("contract_code")
        scope = failure.details.get("scope")
        if not isinstance(affected_one, str) or affected_one not in contract_codes:
            return False
        if scope not in {None, "analytics_field"}:
            return False
        analytics_field = failure.details.get("analytics_field")
        if (
            scope == "analytics_field" and analytics_field not in OPTION_ANALYTIC_NAMES
        ) or (scope is None and analytics_field is not None):
            return False
    return True


def _observation_key(item: Mapping[str, object]) -> str:
    return cast(str, item["id"])


def _observation_contract_code(item: Mapping[str, object]) -> str:
    contract = cast(Mapping[str, object], item["contract"])
    security = cast(Mapping[str, object], contract["security"])
    return str(security["code"])


def _selected_option_result(
    request: Mapping[str, object],
    query: OptionQuery,
    batch: OptionSourceBatch,
    owners: OptionAcquisitionOwners,
    raw_fields: tuple[str, ...],
    outcomes: tuple[OperationOutcome, ...],
    expiry_date: str,
    candidates: list[str],
    selected: list[OptionContractQuote],
    limitations: tuple[DirectTaskLimitation, ...],
) -> UnifiedResearchResult:
    assert batch.subject is not None and batch.session is not None
    identity = _observations_by_kind(outcomes, "etf_option_identity")
    reference = _observations_by_kind(outcomes, "etf_option_reference")
    months = _observations_by_kind(outcomes, "etf_option_contract_months")
    listings = _observations_by_kind(outcomes, "etf_option_contract_listing")
    quotes = _observations_by_kind(outcomes, "etf_option_quote")
    analytics = _observations_by_kind(outcomes, "etf_option_analytics")

    selected_codes = {str(item.security["code"]) for item in selected}
    selected_quotes = tuple(
        item for item in quotes if _observation_contract_code(item) in selected_codes
    )
    selected_analytics = tuple(
        item for item in analytics if _observation_contract_code(item) in selected_codes
    )
    quote_key_by_code = {
        _observation_contract_code(item): _observation_key(item)
        for item in selected_quotes
    }
    analytics_key_by_code = {
        _observation_contract_code(item): _observation_key(item)
        for item in selected_analytics
    }
    contract_rows: list[dict[str, object]] = []
    for item in selected:
        code = str(item.security["code"])
        contract_rows.append(
            {
                "contract": _contract_fact(item),
                "quote": _quote_fact(item),
                "analytics": {
                    name: {
                        "status": "observed",
                        "value": analytic.value,
                        "unit": analytic.unit,
                        "origin": analytic.origin,
                    }
                    for name, analytic in sorted(item.analytics.items())
                },
                "quote_evidence_ids": [quote_key_by_code[code]],
                "analytics_evidence_ids": (
                    [analytics_key_by_code[code]]
                    if code in analytics_key_by_code
                    else []
                ),
                "limitations": list(item.limitations),
            }
        )
    selection_keys = tuple(
        _observation_key(item) for item in (*months, *listings, *selected_quotes)
    )
    quote_keys = tuple(_observation_key(item) for item in selected_quotes)
    analytic_keys = tuple(_observation_key(item) for item in selected_analytics)
    reference_keys = tuple(_observation_key(item) for item in reference)
    identity_keys = tuple(_observation_key(item) for item in identity)
    claims: list[DirectTaskClaim] = []
    if identity_keys:
        claims.append(
            DirectTaskClaim(
                "etf_option_identity",
                "etf_option_identity",
                identity_keys,
                {
                    "subject": {
                        "security": {
                            "exchange": batch.subject.exchange,
                            "code": batch.subject.code,
                            "type": "ETF",
                        },
                        "name": batch.subject.name,
                    },
                    "identity_evidence_ids": list(identity_keys),
                },
            )
        )
    claims.append(
        DirectTaskClaim(
            "etf_option_selection",
            "etf_option_selection",
            selection_keys,
            {
                "request": {
                    "subject_clue": query.subject_clue,
                    "observed_on": query.observed_on,
                    "view": query.view,
                    "expiry_mode": query.expiry_mode,
                    "expiry_date": query.expiry_date,
                    "quote_mode": query.quote_mode,
                },
                "session": {
                    "trading_date": batch.session.trading_date,
                    "observed_at": batch.session.observed_at,
                    "market_state": batch.session.market_state,
                    "quote_mode": query.quote_mode,
                    "reference_price": {
                        "value": batch.session.reference_price,
                        "unit": "CNY/share",
                        "kind": batch.session.reference_price_kind,
                    },
                    "reference_evidence_ids": list(reference_keys),
                },
                "contract_set": {
                    "expiry_date": expiry_date,
                    "contract_months": sorted(
                        {item.contract_month for item in selected}
                    ),
                    "series": sorted({item.series for item in selected}),
                    "contract_count": len(selected),
                    "listing_evidence_ids": [
                        _observation_key(item) for item in (*months, *listings)
                    ],
                },
                "atm": {
                    "status": "tie" if len(candidates) > 1 else "identified",
                    "method_id": "nearest_strike_to_underlying_reference@1",
                    "strike_candidates": candidates,
                    "basis_evidence_ids": list(reference_keys),
                },
                "contracts": contract_rows,
                "t_quote": {
                    "grouping": [
                        "contract_month",
                        "expiry_date",
                        "series",
                        "strike",
                    ],
                    "rows": _direct_t_quote_rows(
                        selected, quote_key_by_code, analytics_key_by_code
                    ),
                },
            },
        )
    )
    claims.append(
        DirectTaskClaim(
            "etf_option_quote",
            "etf_option_quote",
            quote_keys,
            {"contracts": contract_rows},
        )
    )
    if analytic_keys:
        claims.append(
            DirectTaskClaim(
                "etf_option_analytics",
                "etf_option_analytics",
                analytic_keys,
                {
                    "contracts": [
                        row for row in contract_rows if row["analytics_evidence_ids"]
                    ]
                },
            )
        )
    calculations = (
        DirectTaskCalculation(
            "etf_option_atm",
            "etf_option_atm",
            (*reference_keys, *quote_keys),
            {
                "method_id": "nearest_strike_to_underlying_reference@1",
                "reference_price": batch.session.reference_price,
                "candidate_strikes": candidates,
                "basis_evidence_ids": [*reference_keys, *quote_keys],
            },
        ),
    )
    return _direct_option_result(
        request,
        owners=owners,
        raw_fields=raw_fields,
        outcomes=outcomes,
        claims=tuple(claims),
        calculations=calculations,
        limitations=limitations,
        summary=(
            f"Retained {len(batch.contracts)} acquired ETF-option contracts and "
            f"adopted {len(selected)} for the requested view."
        ),
    )


def _direct_t_quote_rows(
    selected: list[OptionContractQuote],
    quote_key_by_code: Mapping[str, str],
    analytics_key_by_code: Mapping[str, str],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, Decimal], dict[str, OptionContractQuote]] = {}
    for item in selected:
        key = (
            item.contract_month,
            item.expiry_date,
            item.series,
            _decimal(item.strike, "strike"),
        )
        grouped.setdefault(key, {})[item.option_type] = item
    rows: list[dict[str, object]] = []
    for _key, sides in sorted(grouped.items()):
        if set(sides) != {"call", "put"}:
            continue
        call, put = sides["call"], sides["put"]
        call_code = str(call.security["code"])
        put_code = str(put.security["code"])
        rows.append(
            {
                "contract_month": call.contract_month,
                "expiry_date": call.expiry_date,
                "series": call.series,
                "strike": {"value": call.strike, "unit": "CNY/share"},
                "call": {
                    "contract": _contract_fact(call),
                    "quote": _quote_fact(call),
                    "quote_evidence_ids": [quote_key_by_code[call_code]],
                    "analytics_evidence_ids": (
                        [analytics_key_by_code[call_code]]
                        if call_code in analytics_key_by_code
                        else []
                    ),
                },
                "put": {
                    "contract": _contract_fact(put),
                    "quote": _quote_fact(put),
                    "quote_evidence_ids": [quote_key_by_code[put_code]],
                    "analytics_evidence_ids": (
                        [analytics_key_by_code[put_code]]
                        if put_code in analytics_key_by_code
                        else []
                    ),
                },
            }
        )
    return rows


def build_etf_options_result(
    request: dict[str, Any], operations: Collection[OptionSourceOperation]
) -> UnifiedResearchResult:
    """Fold trusted typed acquisition material directly into the public result."""

    query = _normalize_query(request)
    operation_list = list(operations)
    if len(operation_list) != 1:
        from .etf_option_sources import SinaEtfOptionSnapshotOperation

        return _direct_option_result(
            request,
            owners=_DEFAULT_OWNERS,
            raw_fields=SinaEtfOptionSnapshotOperation.raw_fields,
            outcomes=_skipped_option_outcomes(
                _DEFAULT_OWNERS,
                SinaEtfOptionSnapshotOperation.raw_fields,
                "skipped_research_dependency",
            ),
            limitations=(
                DirectTaskLimitation(
                    "option_source_count_invalid",
                    "ETF-option research requires exactly one declared source operation.",
                ),
            ),
        )
    operation = operation_list[0]
    owners = _declared_owners(operation)
    raw_fields = _declared_raw_fields(operation)
    if not request["source_policy"]["allow_experimental"]:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=_skipped_option_outcomes(owners, raw_fields, "skipped_policy"),
            limitations=(
                DirectTaskLimitation(
                    "source_policy_not_satisfied",
                    "etf_options currently requires experimental source operations",
                ),
            ),
        )
    batch = operation.collect(query)
    if not _batch_matches_declared_owners(batch, owners):
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=_quarantined_option_outcomes(owners, raw_fields),
            limitations=(
                DirectTaskLimitation(
                    "option_source_owner_mismatch",
                    "The source batch contradicted its externally declared owners and was quarantined.",
                ),
            ),
        )
    outcomes = _option_outcomes(batch, owners, raw_fields)
    if batch.subject is None or batch.session is None:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_source_incomplete",
                    "The source did not establish both a canonical ETF identity and a usable option session.",
                ),
            ),
        )
    if batch.subject.exchange != "SSE" or batch.subject.code != query.subject_clue:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_subject_identity_mismatch",
                    "The source ETF identity does not match the requested underlying.",
                ),
            ),
        )
    source_problem = _source_completeness_problem(batch)
    if source_problem is not None:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation("option_source_not_complete", source_problem),
            ),
        )
    if batch.source_errors and not _has_only_local_analytics_failures(batch):
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_source_not_complete",
                    "The ETF-option batch contains a non-local source failure.",
                ),
            ),
        )
    time_problem = _time_contract_problem(query, batch)
    if time_problem is not None:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(DirectTaskLimitation(time_problem[0], time_problem[1]),),
        )
    if any(
        analytic.origin != "provider_reported"
        for item in batch.contracts
        for analytic in item.analytics.values()
    ):
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_metric_origin_unverified",
                    "Source analytics must remain explicitly provider-reported.",
                ),
            ),
        )
    if (
        query.quote_mode == "latest_completed"
        and batch.session.market_state != "completed"
    ):
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_quote_mode_mismatch",
                    "The source snapshot is not a completed trading session.",
                ),
            ),
        )
    analytics_problem = _analytics_contract_problem(batch.contracts)
    if analytics_problem is not None:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_analytics_contract_invalid", analytics_problem
                ),
            ),
        )
    if (
        query.expiry_mode == "exact"
        and query.expiry_date is not None
        and _date(query.expiry_date, "expiry date")
        < _date(query.observed_on, "observation date")
    ):
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_contract_expired",
                    "The requested option expiry precedes the quote observation date.",
                ),
            ),
        )
    duplicate_conflicts = _duplicate_contract_conflicts(batch.contracts)
    if duplicate_conflicts:
        quote_keys = tuple(
            _observation_key(item)
            for item in _observations_by_kind(outcomes, "etf_option_quote")
        )
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            conflicts=(
                DirectTaskConflict(
                    "duplicate_option_contract_conflict",
                    "Duplicate option-contract rows disagree on canonical contract data.",
                    quote_keys,
                ),
            ),
        )
    expiry_date = _select_expiry(query, batch.contracts)
    if expiry_date is None:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_expiry_not_available",
                    "The requested unexpired option expiry was not established.",
                ),
            ),
        )
    expiry_contracts = [
        item for item in batch.contracts if item.expiry_date == expiry_date
    ]
    incomplete_pairs = _incomplete_pair_keys(expiry_contracts)
    if incomplete_pairs:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_pair_incomplete",
                    "The selected expiry does not contain complete call-put pairs.",
                ),
            ),
        )
    standard = [item for item in expiry_contracts if item.series == "M"]
    pairs = _contract_pairs(standard)
    reference_price = _decimal(batch.session.reference_price, "reference price")
    distances = {strike: abs(strike - reference_price) for strike in pairs}
    if not distances:
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "atm_not_identifiable",
                    "No complete standard call-put pair can establish an ATM strike.",
                ),
            ),
        )
    minimum_distance = min(distances.values())
    candidate_values = sorted(
        (
            strike
            for strike, distance in distances.items()
            if distance == minimum_distance
        ),
    )
    candidates = [pairs[strike][0].strike for strike in candidate_values]
    if query.view == "chain":
        selected = sorted(expiry_contracts, key=_contract_sort_key)
    else:
        selected = [item for strike in candidate_values for item in pairs[strike]]
    if query.view == "atm" and any(item.quote_state != "quoted" for item in selected):
        return _direct_option_result(
            request,
            owners=owners,
            raw_fields=raw_fields,
            outcomes=outcomes,
            limitations=(
                DirectTaskLimitation(
                    "option_quote_unavailable",
                    "At least one selected ATM contract has no usable quote.",
                ),
            ),
        )
    limitations: list[DirectTaskLimitation] = [
        DirectTaskLimitation(
            "experimental_etf_option_sources",
            "ETF-option observations use experimental source operations.",
        ),
        *(
            DirectTaskLimitation(code, "The source disclosed an ETF-option limitation.")
            for code in sorted(set(batch.limitations))
        ),
        *(DirectTaskLimitation(item.code, item.message) for item in batch.degradations),
    ]
    if batch.source_errors:
        limitations.append(
            DirectTaskLimitation(
                "option_acquisition_partial",
                "One or more ETF-option acquisition facts were unavailable or invalid.",
            )
        )
    return _selected_option_result(
        request,
        query,
        batch,
        owners,
        raw_fields,
        outcomes,
        expiry_date,
        candidates,
        selected,
        tuple(limitations),
    )


def _normalize_query(request: dict[str, Any]) -> OptionQuery:
    if request.get("task_type") != "etf_options":
        raise ValueError("etf_options requires task_type etf_options")
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("etf_options requires exactly one ETF subject")
    subject = subjects[0]
    clue = subject.get("clue") if isinstance(subject, dict) else None
    if clue not in SUPPORTED_ETF_CODES:
        raise ValueError("etf_options subject is not a supported ETF code clue")
    window = request.get("window")
    if not isinstance(window, dict):
        raise ValueError("etf_options window must be a JSON object")
    observed_from = window.get("observed_from")
    observed_to = window.get("observed_to")
    if observed_from != observed_to or not isinstance(observed_from, str):
        raise ValueError("etf_options requires one explicit observation date")
    observed_date = _date(observed_from, "observation date")
    as_of = request.get("as_of")
    if not isinstance(as_of, str) or observed_date > _date(as_of, "as_of"):
        raise ValueError("etf_options observation date must not exceed as_of")
    parameters = request.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("etf_options parameters must be a JSON object")
    view = parameters.get("view")
    if view not in {"atm", "chain"}:
        raise ValueError("etf_options parameters.view must be atm or chain")
    quote_mode = parameters.get("quote_mode")
    if quote_mode not in {"latest", "latest_completed"}:
        raise ValueError(
            "etf_options parameters.quote_mode must be latest or latest_completed"
        )
    expiry = parameters.get("expiry")
    if not isinstance(expiry, dict) or expiry.get("mode") not in {
        "nearest_unexpired",
        "exact",
    }:
        raise ValueError("etf_options parameters.expiry is invalid")
    expiry_mode = expiry["mode"]
    expiry_date = expiry.get("date")
    if expiry_mode == "nearest_unexpired":
        if set(expiry) != {"mode"}:
            raise ValueError("nearest_unexpired expiry accepts only mode")
        expiry_date = None
    elif set(expiry) != {"mode", "date"} or not isinstance(expiry_date, str):
        raise ValueError("exact expiry requires one YYYY-MM-DD date")
    elif _date(expiry_date, "expiry date").isoformat() != expiry_date:
        raise ValueError("exact expiry requires one YYYY-MM-DD date")
    return OptionQuery(
        subject_clue=clue,
        as_of=as_of,
        observed_on=observed_from,
        view=view,
        expiry_mode=expiry_mode,
        expiry_date=expiry_date,
        quote_mode=quote_mode,
    )


def _select_expiry(
    query: OptionQuery, contracts: tuple[OptionContractQuote, ...]
) -> str | None:
    observed_on = _date(query.observed_on, "observation date")
    if query.expiry_mode == "exact":
        assert query.expiry_date is not None
        available = {item.expiry_date for item in contracts}
        return (
            query.expiry_date
            if _date(query.expiry_date, "expiry date") >= observed_on
            and query.expiry_date in available
            else None
        )
    candidates = sorted(
        {
            item.expiry_date
            for item in contracts
            if _date(item.expiry_date, "expiry date") >= observed_on
        }
    )
    return candidates[0] if candidates else None


def _contract_pairs(
    contracts: list[OptionContractQuote],
) -> dict[Decimal, tuple[OptionContractQuote, OptionContractQuote]]:
    by_strike: dict[Decimal, dict[str, OptionContractQuote]] = {}
    for item in contracts:
        by_strike.setdefault(_decimal(item.strike, "strike"), {})[item.option_type] = (
            item
        )
    return {
        strike: (sides["call"], sides["put"])
        for strike, sides in by_strike.items()
        if set(sides) == {"call", "put"}
    }


def _incomplete_pair_keys(
    contracts: list[OptionContractQuote],
) -> list[tuple[str, Decimal]]:
    sides_by_key: dict[tuple[str, Decimal], set[str]] = {}
    for item in contracts:
        key = (item.series, _decimal(item.strike, "strike"))
        sides_by_key.setdefault(key, set()).add(item.option_type)
    return sorted(
        (key for key, sides in sides_by_key.items() if sides != {"call", "put"}),
        key=lambda item: (item[0], item[1]),
    )


def _duplicate_contract_conflicts(
    contracts: tuple[OptionContractQuote, ...],
) -> list[dict[str, Any]]:
    seen_security: dict[tuple[str | None, ...], OptionContractQuote] = {}
    seen_logical: dict[tuple[str, str, str, Decimal, str], OptionContractQuote] = {}
    conflicts: list[dict[str, Any]] = []
    for item in contracts:
        security_key = (
            item.security.get("exchange"),
            item.security.get("code"),
            item.security.get("type"),
        )
        logical_key = (
            item.contract_month,
            item.expiry_date,
            item.series,
            _decimal(item.strike, "strike"),
            item.option_type,
        )
        previous = seen_security.get(security_key) or seen_logical.get(logical_key)
        if previous is not None and previous != item:
            conflicts.append(
                {
                    "code": "duplicate_option_contract_conflict",
                    "contract_security": json_safe_copy(item.security),
                    "series": item.series,
                    "strike": item.strike,
                    "option_type": item.option_type,
                    "evidence_ids": sorted({previous.evidence_id, item.evidence_id}),
                }
            )
        else:
            seen_security[security_key] = item
            seen_logical[logical_key] = item
    return conflicts


def _price(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"value": value, "unit": "CNY/share"}


def _contracts(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"value": value, "unit": "contract"}


def _source_completeness_problem(batch: OptionSourceBatch) -> str | None:
    if (
        batch.month_evidence is None
        or batch.month_evidence.identity_status != "validated"
        or len(batch.listing_evidence) != 2
        or {item.option_type for item in batch.listing_evidence} != {"call", "put"}
        or sum(item.observed_count for item in batch.listing_evidence)
        != len(batch.contracts)
    ):
        return "The contract month or call-put listings are not traceable."
    required = {"contract_listing", "option_quotes", "provider_analytics"}
    if not required.issubset(batch.coverage):
        return "The source did not report every required coverage dimension."
    contract_count = len(batch.contracts)
    if contract_count == 0:
        return "The source did not retain any contract-valid quote facts."
    for name in sorted(required):
        coverage = batch.coverage[name]
        if name == "contract_listing" and coverage.state == "partial":
            if (
                "contract_listing_authoritative_total_unavailable"
                not in batch.limitations
                or coverage.expected_count is not None
                or coverage.observed_count != contract_count
            ):
                return "The partial contract listing is not bounded or disclosed correctly."
            continue
        allowed_states = {"observed_nonempty", "partial"}
        if coverage.state not in allowed_states:
            return f"The {name} coverage is not complete enough for research."
        if name != "contract_listing" and (
            coverage.expected_count is None or coverage.observed_count is None
        ):
            return f"The {name} coverage did not expose bounded counts."
        if name == "option_quotes" and coverage.observed_count != contract_count:
            return "The option quote coverage counts disagree with the contract set."
    return None


def _time_contract_problem(
    query: OptionQuery, batch: OptionSourceBatch
) -> tuple[str, str] | None:
    assert batch.session is not None
    if batch.session.trading_date != query.observed_on:
        return (
            "option_session_date_mismatch",
            "The ETF reference session does not match the requested trading date.",
        )
    if batch.session.market_state not in {"intraday", "completed"}:
        return (
            "option_session_state_unknown",
            "The ETF option session state is not established.",
        )
    if batch.session.reference_observed_at is None:
        return (
            "option_reference_time_missing",
            "The ETF reference price has no independent observation time.",
        )
    try:
        reference_time = _china_time(
            batch.session.reference_observed_at, "reference observed_at"
        )
    except ValueError:
        return (
            "option_reference_time_mismatch",
            "The ETF reference observation is not valid China Standard Time.",
        )
    if reference_time.date().isoformat() != query.observed_on:
        return (
            "option_reference_time_mismatch",
            "The ETF reference price does not match the requested trading date.",
        )
    try:
        session_time = _china_time(batch.session.observed_at, "session observed_at")
    except ValueError:
        return (
            "option_session_date_mismatch",
            "The ETF reference observation is not a valid China Standard Time value.",
        )
    if session_time.date().isoformat() != query.observed_on:
        return (
            "option_session_date_mismatch",
            "The ETF reference observation does not match the requested trading date.",
        )
    for item in batch.contracts:
        try:
            quote_time = _china_time(item.observed_at, "contract observed_at")
        except ValueError:
            return (
                "option_quote_time_mismatch",
                "An option quote time is not valid China Standard Time.",
            )
        if quote_time != session_time:
            return (
                "option_quote_time_mismatch",
                "ETF reference and option quotes do not share one observation time.",
            )
    return None


def _analytics_contract_problem(
    contracts: tuple[OptionContractQuote, ...],
) -> str | None:
    for item in contracts:
        names = set(item.analytics)
        if not names.issubset(OPTION_ANALYTIC_NAMES):
            return "Provider analytics fields are missing or have changed."
        if item.option_type not in {"call", "put"} or item.series not in {"M", "A"}:
            return "Option type or contract series is invalid."
        if item.quote_state not in {"quoted", "no_quote"}:
            return "Option quote state is invalid."
        if item.quote_retrieved_at is None:
            return "Quote evidence is not independently traceable."
        if item.analytics and (
            item.analytics_evidence_id is None
            or item.analytics_locator_uri is None
            or item.analytics_retrieved_at is None
            or item.analytics_evidence_id == item.evidence_id
        ):
            return (
                "Quote and provider analytics evidence are not independently traceable."
            )
        if (
            item.security.get("exchange") != "SSE"
            or item.security.get("type") != "ETF_OPTION"
            or not item.security.get("code", "").isdigit()
            or item.contract_month != item.expiry_date[:7]
        ):
            return "Option contract identity fields are invalid."
        for name, analytic in item.analytics.items():
            if analytic.origin != "provider_reported":
                return "Provider analytics origin is invalid."
            if analytic.unit != ANALYTIC_UNITS[name]:
                return f"Provider analytics unit is invalid for {name}."
            analytic_value = _decimal(analytic.value, name)
            if name == "delta" and not Decimal("-1") <= analytic_value <= Decimal("1"):
                return "Provider delta is outside the dimensionless range."
            if name == "implied_volatility" and not Decimal(
                "0"
            ) <= analytic_value <= Decimal("5"):
                return "Provider implied volatility is not a decimal fraction."
        for quote_value, field in (
            (item.strike, "strike"),
            (item.last, "last"),
            (item.bid, "bid"),
            (item.ask, "ask"),
            (item.bid_size, "bid size"),
            (item.ask_size, "ask size"),
            (item.volume, "volume"),
            (item.open_interest, "open interest"),
        ):
            if quote_value is not None and _decimal(quote_value, field) < 0:
                return f"Option {field} is negative."
    return None


def _china_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"etf_options {field} must use an ISO-8601 timestamp"
        ) from error
    if parsed.utcoffset() != timedelta(hours=8):
        raise ValueError(f"etf_options {field} must use China Standard Time")
    return parsed


def _contract_sort_key(item: OptionContractQuote) -> tuple[int, Decimal, int, str]:
    return (
        0 if item.series == "M" else 1,
        _decimal(item.strike, "strike"),
        0 if item.option_type == "call" else 1,
        item.security.get("code", ""),
    )


def _date(value: str, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"etf_options {field} must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"etf_options {field} must use YYYY-MM-DD")
    return parsed


def _decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(
            f"etf_options {field} must be a finite decimal string"
        ) from error
    if not parsed.is_finite():
        raise ValueError(f"etf_options {field} must be a finite decimal string")
    return parsed
