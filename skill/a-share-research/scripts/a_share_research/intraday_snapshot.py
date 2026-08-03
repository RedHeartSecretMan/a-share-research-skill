"""Adjudicate one research-grade continuous-auction intraday snapshot."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Collection

from .intraday_contract import (
    IntradayObservation,
    IntradayQuery,
    IntradaySourceError,
    IntradaySourceOperation,
)

SSE_A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZSE_A_SHARE_PREFIXES = ("000", "001", "002", "003", "300", "301")
TONGDAXIN_OPERATION = "tongdaxin_intraday_snapshot@1"
TENCENT_OPERATION = "tencent_intraday_snapshot@1"


def build_intraday_snapshot_result(
    request: dict[str, Any],
    operations: Collection[IntradaySourceOperation],
    research_now: datetime,
) -> dict[str, Any]:
    """Collect and cross-check one current-date continuous-auction snapshot."""

    query = _normalize_query(request, research_now)
    operation_list = list(operations)
    if len(operation_list) != 2:
        raise ValueError(
            "intraday_market_signal requires exactly two source operations"
        )
    observations: list[IntradayObservation] = []
    source_errors: list[dict[str, Any]] = []
    for operation in operation_list:
        try:
            observation = operation.collect(query)
            observations.append(
                _validate_observation(observation, operation.operation_id)
            )
        except IntradaySourceError as error:
            source_errors.append(_source_error_result(error))
        except Exception:
            # Adapter implementations are outside the ResearchTask boundary.  A
            # provider exception must never escape as a traceback or reveal its
            # request, credentials, or response body through the JSON contract.
            source_errors.append(
                {
                    "source_operation": operation.operation_id,
                    "code": "operation_failure",
                    "message": "The source operation failed without a safe diagnostic.",
                }
            )
    if source_errors or len(observations) != 2:
        return _blocked_result(request, query, observations, source_errors)
    baseline, cross_check = observations
    incompatibilities = _pair_incompatibility(query, baseline, cross_check)
    if incompatibilities:
        return _incompatible_result(request, query, observations, incompatibilities)
    assert baseline.previous_close is not None
    assert baseline.previous_close_basis is not None
    baseline_id = baseline.evidence[0]["id"]
    cross_check_id = cross_check.evidence[0]["id"]
    core_evidence = [baseline_id, cross_check_id]
    all_evidence = [
        item["id"] for observation in observations for item in observation.evidence
    ]
    gap_seconds = abs(
        int((baseline.observed_at - cross_check.observed_at).total_seconds())
    )
    return {
        "schema_version": request["schema_version"],
        "status": "limited",
        "subject": {
            "security": {
                "exchange": query.exchange,
                "code": query.code,
                "type": "A_SHARE",
            }
        },
        "as_of": query.as_of.isoformat(),
        "trading_date": baseline.trading_date.isoformat(),
        "session_state": baseline.session_state,
        "trading_status": baseline.trading_status,
        "price_type": baseline.price_type,
        "snapshot": {
            "latest_price": {"value": baseline.latest_price, "unit": "CNY/share"},
            "open": {"value": baseline.open_price, "unit": "CNY/share"},
            "high": {"value": baseline.high_price, "unit": "CNY/share"},
            "low": {"value": baseline.low_price, "unit": "CNY/share"},
            "previous_close": {
                "status": "unavailable",
                "reported_value": baseline.previous_close,
                "unit": "CNY/share",
                "basis": baseline.previous_close_basis,
                "reason": "independent_semantics_not_adjudicated",
            },
            "cumulative_volume": {
                "value": baseline.cumulative_volume_shares,
                "unit": "shares",
            },
            "cumulative_amount": {
                "value": baseline.cumulative_amount_cny,
                "unit": "CNY",
            },
        },
        "observation_times": {
            "tongdaxin_baseline": baseline.observed_at.isoformat(),
            "tencent_cross_check": cross_check.observed_at.isoformat(),
            "retrieved_at": max(
                baseline.retrieved_at, cross_check.retrieved_at
            ).isoformat(),
            "pair_gap_seconds": str(gap_seconds),
        },
        "source_operations": [item.source_operation for item in observations],
        "field_lineage": {
            "subject": {
                "evidence_ids": core_evidence,
                "source_fields": ["code", "market", "qt.security"],
            },
            "trading_date": {
                "evidence_ids": all_evidence,
                "source_fields": ["year", "month", "day", "day.date"],
            },
            "session_state": {
                "evidence_ids": core_evidence,
                "source_fields": ["servertime", "qt.timestamp"],
            },
            "trading_status": {
                "evidence_ids": core_evidence,
                "source_fields": ["vol", "day.volume"],
            },
            "price_type": {
                "evidence_ids": core_evidence,
                "source_fields": ["price", "day.close", "qt.timestamp"],
            },
            **{
                f"snapshot.{field}": {
                    "evidence_ids": core_evidence,
                    "source_fields": [
                        *baseline.field_sources[field],
                        *cross_check.field_sources[field],
                    ],
                }
                for field in (
                    "latest_price",
                    "open",
                    "high",
                    "low",
                )
            },
            "snapshot.previous_close": {
                "evidence_ids": [baseline_id],
                "source_fields": list(baseline.field_sources["previous_close"]),
            },
            "snapshot.cumulative_volume": {
                "evidence_ids": [baseline_id],
                "source_fields": list(baseline.field_sources["cumulative_volume"]),
            },
            "snapshot.cumulative_amount": {
                "evidence_ids": [baseline_id],
                "source_fields": list(baseline.field_sources["cumulative_amount"]),
            },
            "observation_times.tongdaxin_baseline": {
                "evidence_ids": [baseline_id],
                "source_fields": ["servertime"],
            },
            "observation_times.tencent_cross_check": {
                "evidence_ids": [cross_check_id],
                "source_fields": ["qt.timestamp"],
            },
            "observation_times.retrieved_at": {
                "evidence_ids": all_evidence,
                "source_fields": ["retrieved_at"],
            },
            "observation_times.pair_gap_seconds": {
                "evidence_ids": core_evidence,
                "source_fields": ["servertime", "qt.timestamp"],
                "calculation": "absolute_time_difference_seconds@1",
            },
        },
        "brief": {
            "status": "limited",
            "summary": (
                "Two experimental operations agree on one continuous-auction "
                "intraday market snapshot."
            ),
            "evidence_ids": core_evidence,
        },
        "evidence": [
            item for observation in observations for item in observation.evidence
        ],
        "conflicts": [],
        "source_errors": [],
        "limitations": [
            {
                "code": "experimental_intraday_sources",
                "message": (
                    "The source operations agree but have not completed production "
                    "qualification; the snapshot is limited."
                ),
            }
        ],
    }


def _normalize_query(request: dict[str, Any], research_now: datetime) -> IntradayQuery:
    subjects = request.get("subjects")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("intraday_market_signal requires exactly one subject")
    subject = subjects[0]
    security = subject.get("security") if isinstance(subject, dict) else None
    if not isinstance(security, str):
        raise ValueError("intraday_market_signal requires one canonical security")
    exchange, separator, code = security.partition(":")
    if (
        separator != ":"
        or exchange not in {"SSE", "SZSE"}
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        raise ValueError(
            "intraday_market_signal requires one canonical SSE/SZSE A-share"
        )
    prefixes = SSE_A_SHARE_PREFIXES if exchange == "SSE" else SZSE_A_SHARE_PREFIXES
    if not code.startswith(prefixes):
        raise ValueError(
            "intraday_market_signal subject is not a supported SSE/SZSE A-share"
        )
    if request.get("window") is not None:
        raise ValueError("intraday_market_signal window must be null")
    as_of = date.fromisoformat(request["as_of"])
    if as_of != research_now.date():
        raise ValueError("intraday_market_signal requires current China-date as_of")
    return IntradayQuery(
        security=security,
        exchange=exchange,
        code=code,
        as_of=as_of,
        retrieved_at=research_now,
    )


def _pair_incompatibility(
    query: IntradayQuery,
    baseline: IntradayObservation,
    cross_check: IntradayObservation,
) -> list[dict[str, Any]]:
    """Return every explicit pair conflict without selecting a convenient source."""

    conflicts: list[dict[str, Any]] = []
    observations = (baseline, cross_check)
    if baseline.source_operation != TONGDAXIN_OPERATION or (
        cross_check.source_operation != TENCENT_OPERATION
    ):
        conflicts.append(
            _pair_conflict(
                "intraday_source_role_mismatch",
                "The required TongdaXin baseline and Tencent cross-check operations were not both supplied.",
                field="source_operation",
                baseline=baseline.source_operation,
                cross_check=cross_check.source_operation,
                observations=observations,
            )
        )
    if any(item.security != query.security for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_security_mismatch",
                "Intraday source operations returned a different security.",
                field="security",
                baseline=baseline.security,
                cross_check=cross_check.security,
                observations=observations,
            )
        )
    if any(item.trading_date != query.as_of for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_trading_date_mismatch",
                "Intraday source operations returned a different trading date.",
                field="trading_date",
                baseline=baseline.trading_date.isoformat(),
                cross_check=cross_check.trading_date.isoformat(),
                observations=observations,
            )
        )
    if any(item.session_state != "continuous" for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_session_mismatch",
                "Intraday source observations are not continuous-auction data.",
                field="session_state",
                baseline=baseline.session_state,
                cross_check=cross_check.session_state,
                observations=observations,
            )
        )
    if any(item.trading_status != "traded" for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_trading_status_mismatch",
                "Intraday source observations do not establish traded status.",
                field="trading_status",
                baseline=baseline.trading_status,
                cross_check=cross_check.trading_status,
                observations=observations,
            )
        )
    if any(item.price_type != "latest_traded" for item in observations):
        conflicts.append(
            _pair_conflict(
                "intraday_price_type_mismatch",
                "Intraday source observations do not establish latest traded prices.",
                field="price_type",
                baseline=baseline.price_type,
                cross_check=cross_check.price_type,
                observations=observations,
            )
        )
    for field in ("latest_price", "open_price", "high_price", "low_price"):
        baseline_value = getattr(baseline, field)
        cross_check_value = getattr(cross_check, field)
        if baseline_value != cross_check_value:
            conflicts.append(
                _pair_conflict(
                    "intraday_core_price_mismatch",
                    f"Intraday source observations disagree on {field}.",
                    field=field,
                    baseline=baseline_value,
                    cross_check=cross_check_value,
                    observations=observations,
                )
            )
    incomplete_fields = [
        field
        for field, value in (
            ("previous_close", baseline.previous_close),
            ("previous_close_basis", baseline.previous_close_basis),
            ("cumulative_volume", baseline.cumulative_volume_shares),
            ("cumulative_amount", baseline.cumulative_amount_cny),
        )
        if value is None
    ]
    if incomplete_fields:
        conflicts.append(
            _pair_conflict(
                "intraday_baseline_incomplete",
                "TongdaXin did not establish all required snapshot fields.",
                field="baseline",
                missing_fields=incomplete_fields,
                observations=observations,
            )
        )
    return conflicts


def _incompatible_result(
    request: dict[str, Any],
    query: IntradayQuery,
    observations: list[IntradayObservation],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = _diagnostic_fields(query, observations)
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        **diagnostics,
        "conflicts": conflicts,
        "source_errors": [],
        "limitations": [
            {
                "code": "intraday_source_pair_incompatible",
                "message": "The two source observations cannot form one snapshot.",
            }
        ],
    }


def _blocked_result(
    request: dict[str, Any],
    query: IntradayQuery,
    observations: list[IntradayObservation],
    source_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = _diagnostic_fields(query, observations)
    for source_error in source_errors:
        operation = source_error.get("source_operation")
        if (
            isinstance(operation, str)
            and operation not in diagnostics["source_operations"]
        ):
            diagnostics["source_operations"].append(operation)
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        **diagnostics,
        "conflicts": [],
        "source_errors": source_errors,
        "limitations": [
            {
                "code": "intraday_source_pair_incomplete",
                "message": "Both required intraday source operations must succeed.",
            }
        ],
    }


def _validate_observation(
    observation: IntradayObservation,
    expected_operation: str,
) -> IntradayObservation:
    """Validate and tick-normalize one adapter result at the internal seam."""

    if not isinstance(observation, IntradayObservation):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source operation returned an unsupported observation shape.",
        )
    if observation.source_operation != expected_operation:
        raise IntradaySourceError(
            expected_operation,
            "operation_identity_mismatch",
            "The source operation returned an observation with another operation identity.",
        )
    if not isinstance(observation.security, str) or not observation.security:
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not identify a security.",
        )
    if not isinstance(observation.trading_date, date):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not contain a trading date.",
        )
    for name, value in (
        ("observed_at", observation.observed_at),
        ("retrieved_at", observation.retrieved_at),
    ):
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise IntradaySourceError(
                expected_operation,
                "unknown_schema",
                f"The source observation does not contain a timezone-aware {name}.",
            )
    price_values = {
        field: _canonical_price(getattr(observation, field), expected_operation, field)
        for field in ("latest_price", "open_price", "high_price", "low_price")
    }
    _validate_ohlc_values(price_values, expected_operation)
    previous_close = observation.previous_close
    if previous_close is not None:
        previous_close = _canonical_price(
            previous_close, expected_operation, "previous_close"
        )
    volume = _canonical_nonnegative(
        observation.cumulative_volume_shares,
        expected_operation,
        "cumulative_volume",
    )
    if volume is not None and Decimal(volume) != Decimal(volume).to_integral_value():
        raise IntradaySourceError(
            expected_operation,
            "ambiguous_volume_unit",
            "The normalized cumulative volume is not a whole number of shares.",
        )
    amount = _canonical_nonnegative(
        observation.cumulative_amount_cny,
        expected_operation,
        "cumulative_amount",
    )
    if not isinstance(observation.evidence, tuple) or not observation.evidence:
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not retain evidence.",
        )
    evidence: list[dict[str, Any]] = []
    for item in observation.evidence:
        evidence.append(
            _validate_evidence_item(item, expected_operation, observation.security)
        )
    required_sources = {"latest_price", "open", "high", "low"}
    if expected_operation == TONGDAXIN_OPERATION:
        required_sources.update(
            {"previous_close", "cumulative_volume", "cumulative_amount"}
        )
    if (
        not isinstance(observation.field_sources, dict)
        or not required_sources.issubset(observation.field_sources)
        or any(
            not isinstance(observation.field_sources[field], tuple)
            or not observation.field_sources[field]
            or any(
                not isinstance(source_field, str) or not source_field
                for source_field in observation.field_sources[field]
            )
            for field in required_sources
        )
    ):
        raise IntradaySourceError(
            expected_operation,
            "unknown_schema",
            "The source observation does not retain complete field lineage.",
        )
    return replace(
        observation,
        latest_price=price_values["latest_price"],
        open_price=price_values["open_price"],
        high_price=price_values["high_price"],
        low_price=price_values["low_price"],
        previous_close=previous_close,
        cumulative_volume_shares=(
            format(Decimal(volume).quantize(Decimal(1)), "f")
            if volume is not None
            else None
        ),
        cumulative_amount_cny=amount,
        evidence=tuple(evidence),
    )


def _canonical_price(value: object, operation: str, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int, float)):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal price.",
        )
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidOperation
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal price.",
        ) from error
    if not parsed.is_finite() or parsed <= 0:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not a positive finite price.",
        )
    normalized = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if normalized <= 0:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is below the minimum CNY tick.",
        )
    return format(normalized, "f")


def _canonical_nonnegative(
    value: object,
    operation: str,
    field: str,
) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int, float)):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal value.",
        )
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidOperation
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not an exact decimal value.",
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            f"The source {field} is not a nonnegative finite decimal.",
        )
    return format(parsed.normalize(), "f")


def _validate_ohlc_values(values: dict[str, str], operation: str) -> None:
    low = Decimal(values["low_price"])
    high = Decimal(values["high_price"])
    opening = Decimal(values["open_price"])
    latest = Decimal(values["latest_price"])
    if low > high or opening < low or opening > high or latest < low or latest > high:
        raise IntradaySourceError(
            operation,
            "inconsistent_price_bar",
            "The source OHLC values are internally inconsistent.",
        )


def _normalize_evidence_prices(item: dict[str, Any], operation: str) -> dict[str, Any]:
    normalized = dict(item)
    observed_value = item.get("observed_value")
    if isinstance(observed_value, dict):
        observed_value = dict(observed_value)
        for source_field in ("latest_price", "open", "high", "low", "previous_close"):
            if (
                source_field in observed_value
                and observed_value[source_field] is not None
            ):
                observed_value[source_field] = _canonical_price(
                    observed_value[source_field], operation, source_field
                )
        if operation == TONGDAXIN_OPERATION:
            for source_field in ("cumulative_volume", "cumulative_amount"):
                if (
                    source_field in observed_value
                    and observed_value[source_field] is not None
                ):
                    observed_value[source_field] = _canonical_nonnegative(
                        observed_value[source_field], operation, source_field
                    )
        normalized["observed_value"] = observed_value
    return normalized


def _validate_evidence_item(
    item: object,
    operation: str,
    security: str,
) -> dict[str, Any]:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"]
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source observation evidence is not a structured record.",
        )
    if item.get("source_operation") != operation:
        raise IntradaySourceError(
            operation,
            "operation_identity_mismatch",
            "The source evidence identifies another operation.",
        )
    subject = item.get("subject")
    if (
        not isinstance(subject, dict)
        or not isinstance(subject.get("security"), str)
        or not subject["security"]
        or subject["security"] != security
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not identify a security.",
        )
    observation = item.get("observation")
    if not isinstance(observation, dict) or not isinstance(
        observation.get("kind"), str
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not identify an observation shape.",
        )
    locator = item.get("locator")
    if (
        not isinstance(locator, dict)
        or not isinstance(locator.get("uri"), str)
        or not locator["uri"].strip()
    ):
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not retain a locator.",
        )
    retrieved_at = item.get("retrieved_at")
    if not isinstance(retrieved_at, str) or not retrieved_at.strip():
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence does not retain a retrieval time.",
        )
    try:
        parsed_retrieved_at = datetime.fromisoformat(retrieved_at)
    except ValueError as error:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence retrieval time is not ISO formatted.",
        ) from error
    if parsed_retrieved_at.tzinfo is None:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence retrieval time has no timezone.",
        )
    kind = observation["kind"]
    if kind not in {
        "intraday_quote",
        "latest_daily_bar_date_basis",
        "intraday_core_price_cross_check",
    }:
        raise IntradaySourceError(
            operation,
            "unknown_schema",
            "The source evidence uses an unknown observation shape.",
        )
    if kind in {"intraday_quote", "intraday_core_price_cross_check"}:
        observed_value = item.get("observed_value")
        required_values = {"latest_price", "open", "high", "low"}
        if operation == TONGDAXIN_OPERATION:
            required_values.update(
                {"previous_close", "cumulative_volume", "cumulative_amount"}
            )
        if (
            not isinstance(observed_value, dict)
            or not required_values.issubset(observed_value)
            or any(observed_value[field] is None for field in required_values)
        ):
            raise IntradaySourceError(
                operation,
                "unknown_schema",
                "The source evidence does not retain complete observed values.",
            )
        for source_field in ("latest_price", "open", "high", "low"):
            _canonical_price(observed_value[source_field], operation, source_field)
        if operation == TONGDAXIN_OPERATION:
            _canonical_price(
                observed_value["previous_close"], operation, "previous_close"
            )
            _canonical_nonnegative(
                observed_value["cumulative_volume"], operation, "cumulative_volume"
            )
            _canonical_nonnegative(
                observed_value["cumulative_amount"], operation, "cumulative_amount"
            )
    return _normalize_evidence_prices(item, operation)


def _source_error_result(error: IntradaySourceError) -> dict[str, Any]:
    safe_messages = {
        "upstream_unavailable": "The source operation was unavailable.",
        "upstream_http_error": "The source operation returned an upstream error.",
        "empty_response": "The source operation returned an empty response.",
        "empty_observation": "The source operation returned no observations.",
        "unknown_schema": "The source response did not match the expected schema.",
        "wrong_security_payload": "The source response identifies another security.",
        "quote_daily_security_mismatch": "The quote and daily bar identify different securities.",
        "quote_daily_date_mismatch": "The quote and daily bar identify different dates.",
        "trading_date_mismatch": "The source daily bar is not bound to the requested date.",
        "inconsistent_price_bar": "The source OHLC values are internally inconsistent.",
        "ambiguous_volume_unit": "The source volume unit cannot be established as hands.",
        "ambiguous_volume_scope": "The source volume cumulative scope cannot be established.",
        "ambiguous_amount_unit": "The source amount unit cannot be established as CNY.",
        "ambiguous_amount_scope": "The source amount cumulative scope cannot be established.",
        "ambiguous_zero_value": "The source zero cumulative value has no explicit no-trade or suspended status.",
        "operation_identity_mismatch": "The source operation returned another operation identity.",
    }
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": safe_messages.get(
            error.code,
            "The source operation could not establish a usable observation.",
        ),
    }


def _pair_conflict(
    code: str,
    message: str,
    *,
    observations: tuple[IntradayObservation, IntradayObservation],
    field: str | None = None,
    baseline: object | None = None,
    cross_check: object | None = None,
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    conflict: dict[str, Any] = {
        "code": code,
        "message": message,
        "evidence_ids": _evidence_ids(observations),
    }
    if field is not None:
        conflict["field"] = field
    if baseline is not None:
        conflict["baseline"] = baseline
    if cross_check is not None:
        conflict["cross_check"] = cross_check
    if missing_fields:
        conflict["missing_fields"] = missing_fields
    return conflict


def _evidence_ids(
    observations: tuple[IntradayObservation, ...] | list[IntradayObservation],
) -> list[str]:
    return [
        item["id"]
        for observation in observations
        for item in observation.evidence
        if isinstance(item.get("id"), str)
    ]


def _diagnostic_fields(
    query: IntradayQuery,
    observations: list[IntradayObservation],
) -> dict[str, Any]:
    evidence = [item for observation in observations for item in observation.evidence]
    fields: dict[str, Any] = {
        "subject": {
            "security": {
                "exchange": query.exchange,
                "code": query.code,
                "type": "A_SHARE",
            }
        },
        "evidence": evidence,
        "source_operations": [item.source_operation for item in observations],
        "observation_times": {
            "retrieved_at": max(
                [query.retrieved_at, *(item.retrieved_at for item in observations)]
            ).isoformat(),
        },
        "field_lineage": {},
        "brief": {
            "status": "blocked",
            "evidence_ids": _evidence_ids(observations),
        },
    }
    for observation in observations:
        prefix = observation.source_operation
        fields["observation_times"][prefix] = observation.observed_at.isoformat()
        for field, source_fields in observation.field_sources.items():
            fields["field_lineage"].setdefault(
                f"{prefix}.{field}",
                {
                    "evidence_ids": [item["id"] for item in observation.evidence],
                    "source_fields": list(source_fields),
                },
            )
    if observations:
        fields["trading_date"] = observations[0].trading_date.isoformat()
    fields["as_of"] = query.as_of.isoformat()
    return fields
