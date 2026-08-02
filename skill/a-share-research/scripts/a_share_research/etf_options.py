"""Coordinate ETF-option contracts, quotes, ATM selection, and analytics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Collection

from .etf_option_contract import (
    OPTION_ANALYTIC_NAMES,
    EtfOptionSubject,
    OptionContractQuote,
    OptionQuery,
    OptionSourceBatch,
    OptionSourceOperation,
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


def build_etf_options_result(
    request: dict[str, Any], operations: Collection[OptionSourceOperation]
) -> dict[str, Any]:
    """Return a stable ETF-option result from typed source batches."""

    query = _normalize_query(request)
    operation_list = list(operations)
    if len(operation_list) != 1:
        return _blocked_result(
            request,
            "option_source_count_invalid",
            "ETF-option v0.1 requires exactly one explicitly selected source operation.",
            [],
        )
    batches = [operation_list[0].collect(query)]
    batch = batches[0]
    if batch.subject is None or batch.session is None:
        return _blocked_result(
            request,
            "option_source_incomplete",
            "The source did not establish both a canonical ETF identity and a usable option session.",
            batches,
            subject=batch.subject,
        )
    if batch.subject.exchange != "SSE" or batch.subject.code != query.subject_clue:
        return _blocked_result(
            request,
            "option_subject_identity_mismatch",
            "The source ETF identity does not match the requested underlying.",
            batches,
            subject=batch.subject,
        )
    source_problem = _source_completeness_problem(batch)
    if source_problem is not None:
        return _blocked_result(
            request,
            "option_source_not_complete",
            source_problem,
            batches,
            subject=batch.subject,
        )
    time_problem = _time_contract_problem(query, batch)
    if time_problem is not None:
        return _blocked_result(
            request,
            time_problem[0],
            time_problem[1],
            batches,
            subject=batch.subject,
        )
    if any(
        analytic.origin != "provider_reported"
        for item in batch.contracts
        for analytic in item.analytics.values()
    ):
        return _blocked_result(
            request,
            "option_metric_origin_unverified",
            "Source analytics must remain explicitly provider-reported.",
            batches,
            subject=batch.subject,
        )
    analytics_problem = _analytics_contract_problem(batch.contracts)
    if analytics_problem is not None:
        return _blocked_result(
            request,
            "option_analytics_contract_invalid",
            analytics_problem,
            batches,
            subject=batch.subject,
        )
    if (
        query.expiry_mode == "exact"
        and query.expiry_date is not None
        and _date(query.expiry_date, "expiry date")
        < _date(query.observed_on, "observation date")
    ):
        return _blocked_result(
            request,
            "option_contract_expired",
            "The requested option expiry precedes the quote observation date.",
            batches,
            subject=batch.subject,
        )
    duplicate_conflicts = _duplicate_contract_conflicts(batch.contracts)
    if duplicate_conflicts:
        return _blocked_result(
            request,
            "duplicate_option_contract_conflict",
            "Duplicate option-contract rows disagree on canonical contract data.",
            batches,
            subject=batch.subject,
            conflicts=duplicate_conflicts,
        )
    expiry_date = _select_expiry(query, batch.contracts)
    if expiry_date is None:
        return _blocked_result(
            request,
            "option_expiry_not_available",
            "The requested unexpired option expiry was not established.",
            batches,
            subject=batch.subject,
        )
    expiry_contracts = [
        item for item in batch.contracts if item.expiry_date == expiry_date
    ]
    incomplete_pairs = _incomplete_pair_keys(expiry_contracts)
    if incomplete_pairs:
        return _blocked_result(
            request,
            "option_pair_incomplete",
            "The selected expiry does not contain complete call-put pairs.",
            batches,
            subject=batch.subject,
        )
    standard = [item for item in expiry_contracts if item.series == "M"]
    pairs = _contract_pairs(standard)
    reference_price = _decimal(batch.session.reference_price, "reference price")
    distances = {strike: abs(strike - reference_price) for strike in pairs}
    if not distances:
        return _blocked_result(
            request,
            "atm_not_identifiable",
            "No complete standard call-put pair can establish an ATM strike.",
            batches,
            subject=batch.subject,
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
    result = _result(request, query, batch, expiry_date, candidates, selected)
    if query.view == "atm" and any(item.quote_state != "quoted" for item in selected):
        result["status"] = "blocked"
        result["limitations"].append(
            {
                "code": "option_quote_unavailable",
                "message": "At least one selected ATM contract has no usable quote.",
            }
        )
    if (
        query.quote_mode == "latest_completed"
        and batch.session.market_state != "completed"
    ):
        result["status"] = "blocked"
        result["limitations"].append(
            {
                "code": "option_quote_mode_mismatch",
                "message": "The source snapshot is not a completed trading session.",
            }
        )
    if batch.source_errors:
        result["status"] = "blocked"
        result["limitations"].append(
            {
                "code": "option_quote_unavailable",
                "message": "The source reported one or more contracts without a usable quote.",
            }
        )
    return result


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
                    "contract_security": item.security,
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


def _result(
    request: dict[str, Any],
    query: OptionQuery,
    batch: OptionSourceBatch,
    expiry_date: str,
    candidates: list[str],
    selected: list[OptionContractQuote],
) -> dict[str, Any]:
    assert batch.subject is not None and batch.session is not None
    contracts = [_contract_result(item) for item in selected]
    return {
        "schema_version": request["schema_version"],
        "task_type": "etf_options",
        "status": "limited",
        "subjects": [batch.subject.to_result()],
        "research": {
            "as_of": query.as_of,
            "timezone": "Asia/Shanghai",
            "retrieved_at": batch.session.retrieved_at.isoformat(),
        },
        "session": {
            "trading_date": batch.session.trading_date,
            "observed_at": batch.session.observed_at,
            "market_state": batch.session.market_state,
            "quote_mode": query.quote_mode,
            "underlying_reference": {
                "price": {
                    "value": batch.session.reference_price,
                    "unit": "CNY/share",
                },
                "price_kind": batch.session.reference_price_kind,
                "observed_at": batch.session.reference_observed_at,
                "evidence_ids": [batch.session.reference_evidence_id],
            },
        },
        "contract_set": {
            "expiry_date": expiry_date,
            "contract_months": sorted({item.contract_month for item in selected}),
            "series": sorted({item.series for item in selected}),
            "contract_count": len(selected),
            "source_operation": batch.operation_id,
            "evidence_ids": [
                *(
                    [batch.month_evidence.evidence_id]
                    if batch.month_evidence is not None
                    else []
                ),
                *(item.evidence_id for item in batch.listing_evidence),
            ],
        },
        "atm": {
            "status": "tie" if len(candidates) > 1 else "identified",
            "method_id": "nearest_strike_to_underlying_reference@1",
            "strike_candidates": candidates,
            "basis_evidence_ids": [batch.session.reference_evidence_id],
        },
        "contracts": contracts,
        "t_quote": {
            "grouping": ["contract_month", "expiry_date", "series", "strike"],
            "rows": _t_quote_rows(selected),
        },
        "brief": {
            "contract_count": len(contracts),
            "quoted_contract_count": sum(
                item.quote_state == "quoted" for item in selected
            ),
            "no_quote_contract_count": sum(
                item.quote_state == "no_quote" for item in selected
            ),
            "coverage": {
                key: value.to_result() for key, value in sorted(batch.coverage.items())
            },
        },
        "calculations": [],
        "evidence": _evidence(batch, selected),
        "conflicts": [],
        "source_errors": [item.to_result() for item in batch.source_errors],
        "degradations": [item.to_result() for item in batch.degradations],
        "limitations": [
            {
                "code": "experimental_etf_option_sources",
                "message": "ETF-option observations use experimental source operations.",
            },
            *(
                [
                    {
                        "code": code,
                        "message": "The source disclosed an ETF-option limitation.",
                    }
                    for code in sorted(set(batch.limitations))
                ]
            ),
        ],
    }


def _contract_result(item: OptionContractQuote) -> dict[str, Any]:
    return {
        "contract": _contract_identity(item),
        "quote": _quote_result(item),
        "analytics": _analytics_result(item),
        "evidence_ids": sorted(
            {
                item.evidence_id,
                item.analytics_evidence_id or item.evidence_id,
            }
        ),
        "limitations": list(item.limitations),
    }


def _contract_identity(item: OptionContractQuote) -> dict[str, Any]:
    return {
        "security": item.security,
        "option_type": item.option_type,
        "strike": {"value": item.strike, "unit": "CNY/share"},
        "contract_month": item.contract_month,
        "expiry_date": item.expiry_date,
        "series": item.series,
    }


def _quote_result(item: OptionContractQuote) -> dict[str, Any]:
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
        "evidence_ids": [item.evidence_id],
    }


def _analytics_result(item: OptionContractQuote) -> dict[str, Any]:
    evidence_id = item.analytics_evidence_id or item.evidence_id
    return {
        key: {
            **item.analytics[key].to_result(evidence_id),
            "source_operation": item.source_operation,
        }
        for key in sorted(OPTION_ANALYTIC_NAMES.intersection(item.analytics))
    }


def _evidence(
    batch: OptionSourceBatch, contracts: list[OptionContractQuote]
) -> list[dict[str, Any]]:
    assert batch.subject is not None and batch.session is not None
    subject = batch.subject.to_result()
    result: list[dict[str, Any]] = _source_boundary_evidence(batch)
    reference_time = batch.session.reference_observed_at
    reference_retrieved_at = (
        batch.session.reference_retrieved_at or batch.session.retrieved_at
    )
    result.append(
        {
            "id": batch.session.reference_evidence_id,
            "source_role": "market_observation",
            "source_operation": (
                batch.session.reference_source_operation or batch.operation_id
            ),
            "experimental": True,
            "subject": subject,
            "observation": {
                "kind": "ETF underlying reference quote",
                "value": batch.session.reference_price,
                "unit": "CNY/share",
            },
            "evidence_time": reference_time,
            "available_at": reference_time,
            "retrieved_at": reference_retrieved_at.isoformat(),
            "locator": {"uri": batch.session.locator_uri},
            "limitations": [],
        }
    )
    for item in contracts:
        result.append(
            {
                "id": item.evidence_id,
                "source_role": "market_observation",
                "source_operation": item.source_operation,
                "experimental": True,
                "subject": {"security": item.security},
                "observation": {
                    "contract": _contract_identity(item),
                    "quote": _quote_result(item),
                },
                "evidence_time": item.observed_at,
                "available_at": item.observed_at,
                "retrieved_at": (
                    item.quote_retrieved_at or batch.session.retrieved_at
                ).isoformat(),
                "locator": {"uri": item.locator_uri},
                "limitations": list(item.limitations),
            }
        )
        analytics_evidence_id = item.analytics_evidence_id or item.evidence_id
        result.append(
            {
                "id": analytics_evidence_id,
                "source_role": "market_observation",
                "source_operation": item.source_operation,
                "experimental": True,
                "subject": {"security": item.security},
                "observation": {
                    "contract": _contract_identity(item),
                    "provider_analytics": _analytics_result(item),
                },
                "evidence_time": item.observed_at,
                "available_at": item.observed_at,
                "retrieved_at": (
                    item.analytics_retrieved_at or batch.session.retrieved_at
                ).isoformat(),
                "locator": {"uri": item.analytics_locator_uri or item.locator_uri},
                "limitations": ["provider_analytics_not_independently_verified"],
            }
        )
    return result


def _blocked_result(
    request: dict[str, Any],
    code: str,
    message: str,
    batches: list[OptionSourceBatch],
    *,
    subject: EtfOptionSubject | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": request["schema_version"],
        "task_type": "etf_options",
        "status": "blocked",
        "subjects": [subject.to_result()]
        if subject is not None
        else request["subjects"],
        "session": {"status": "unresolved"},
        "contract_set": {"status": "unresolved"},
        "atm": {"status": "not_identifiable"},
        "contracts": [],
        "t_quote": {"status": "unresolved", "rows": []},
        "brief": {
            "contract_count": 0,
            "quoted_contract_count": 0,
            "no_quote_contract_count": 0,
            "coverage": {
                key: value.to_result()
                for batch in batches
                for key, value in sorted(batch.coverage.items())
            },
        },
        "calculations": [],
        "evidence": [
            evidence for batch in batches for evidence in _blocked_evidence(batch)
        ],
        "conflicts": conflicts or [],
        "source_errors": [
            item.to_result() for batch in batches for item in batch.source_errors
        ],
        "degradations": [
            item.to_result() for batch in batches for item in batch.degradations
        ],
        "limitations": [{"code": code, "message": message}],
    }


def _price(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"value": value, "unit": "CNY/share"}


def _contracts(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"value": value, "unit": "contract"}


def _source_completeness_problem(batch: OptionSourceBatch) -> str | None:
    fatal_errors = [item for item in batch.source_errors if item.code != "no_quote"]
    if fatal_errors:
        return "The source reported a fatal acquisition or schema error."
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
        allowed_states = (
            {"observed_nonempty", "partial"}
            if name == "option_quotes" and batch.source_errors
            else {"observed_nonempty"}
        )
        if coverage.state not in allowed_states:
            return f"The {name} coverage is not complete enough for research."
        if coverage.expected_count is None or coverage.observed_count is None:
            return f"The {name} coverage did not expose bounded counts."
        if name in {"contract_listing", "provider_analytics"} and (
            coverage.expected_count != contract_count
            or coverage.observed_count != contract_count
        ):
            return f"The {name} coverage counts disagree with the contract set."
        if (
            name == "option_quotes"
            and not batch.source_errors
            and (
                coverage.expected_count != contract_count
                or coverage.observed_count != contract_count
            )
        ):
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
        if not REQUIRED_ANALYTICS.issubset(names) or not names.issubset(
            OPTION_ANALYTIC_NAMES
        ):
            return "Provider analytics fields are missing or have changed."
        if item.option_type not in {"call", "put"} or item.series not in {"M", "A"}:
            return "Option type or contract series is invalid."
        if item.quote_state not in {"quoted", "no_quote"}:
            return "Option quote state is invalid."
        if (
            item.analytics_evidence_id is None
            or item.analytics_locator_uri is None
            or item.quote_retrieved_at is None
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


def _t_quote_rows(contracts: list[OptionContractQuote]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, Decimal], dict[str, OptionContractQuote]] = {}
    for item in contracts:
        key = (
            item.contract_month,
            item.expiry_date,
            item.series,
            _decimal(item.strike, "strike"),
        )
        grouped.setdefault(key, {})[item.option_type] = item
    rows = []
    for key in sorted(grouped, key=lambda item: (item[0], item[1], item[2], item[3])):
        month, expiry, series, _ = key
        sides = grouped[key]
        call = sides["call"]
        put = sides["put"]
        rows.append(
            {
                "contract_month": month,
                "expiry_date": expiry,
                "series": series,
                "strike": {"value": call.strike, "unit": "CNY/share"},
                "call_security": call.security,
                "put_security": put.security,
                "call": {
                    "security": call.security,
                    "quote": _quote_result(call),
                    "analytics": _analytics_result(call),
                },
                "put": {
                    "security": put.security,
                    "quote": _quote_result(put),
                    "analytics": _analytics_result(put),
                },
                "call_evidence_ids": [call.evidence_id],
                "put_evidence_ids": [put.evidence_id],
            }
        )
    return rows


def _source_boundary_evidence(batch: OptionSourceBatch) -> list[dict[str, Any]]:
    result = _identity_evidence(batch.subject)
    if batch.month_evidence is not None:
        result.append(batch.month_evidence.to_evidence(batch.subject))
    if batch.subject is not None:
        result.extend(
            item.to_evidence(batch.subject) for item in batch.listing_evidence
        )
    return result


def _identity_evidence(subject: EtfOptionSubject | None) -> list[dict[str, Any]]:
    if (
        subject is None
        or subject.identity_evidence_id is None
        or subject.identity_locator_uri is None
        or subject.identity_retrieved_at is None
        or subject.identity_observed_on is None
    ):
        return []
    return [
        {
            "id": subject.identity_evidence_id,
            "source_role": "authoritative_disclosure",
            "source_operation": "sse_etf_list@1",
            "experimental": True,
            "subject": subject.to_result(),
            "observation": {"kind": "SSE ETF identity", "name": subject.name},
            "evidence_time": subject.identity_observed_on,
            "available_at": None,
            "retrieved_at": subject.identity_retrieved_at.isoformat(),
            "locator": {"uri": subject.identity_locator_uri},
            "limitations": ["availability_time_unknown"],
        }
    ]


def _blocked_evidence(batch: OptionSourceBatch) -> list[dict[str, Any]]:
    if batch.subject is not None and batch.session is not None:
        return _evidence(batch, list(batch.contracts))
    return _source_boundary_evidence(batch)


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
