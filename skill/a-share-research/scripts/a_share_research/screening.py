"""Pure, deterministic implementation of the deferred A-share screening plan.

This module deliberately has no provider, clock, filesystem, or workflow side
effects.  It consumes an already prepared :class:`AShareUniverse` and the
normalized factor records produced by a separate acquisition seam.  The public
``market_screen_research`` workflow remains blocked; this seam is useful for
contract tests and a future qualified owner.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from types import MappingProxyType
from typing import cast

from .a_share_universe import AShareUniverse, UniverseSecurity

SCREENING_PLAN_ID = "fundamental_research_priority@1.0"
SCREENING_PUBLIC_STATE = "blocked"
FACTOR_DEFINITIONS: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "earnings_yield_ttm": MappingProxyType(
            {"formula": "1 / pe_ttm", "weight": Decimal("0.35")}
        ),
        "book_to_price_mrq": MappingProxyType(
            {"formula": "1 / pb_mrq", "weight": Decimal("0.25")}
        ),
        "return_on_equity_ttm": MappingProxyType(
            {
                "formula": "parent_profit_ttm / ((current_parent_equity_mrq + prior_year_parent_equity_mrq) / 2)",
                "weight": Decimal("0.25"),
            }
        ),
        "median_turnover_amount_20_sessions": MappingProxyType(
            {
                "formula": "median(turnover_amount_20_sessions)",
                "weight": Decimal("0.15"),
            }
        ),
    }
)
_FACTOR_ORDER = tuple(FACTOR_DEFINITIONS)
_EXCHANGES = ("SSE", "SZSE", "BSE")
_ORDINARY_CATEGORIES = frozenset(
    {
        "A_SHARE",
        "A_SHARE_STOCK",
        "ORDINARY_SHARE",
        "ORDINARY_STOCK",
        "RMB_ORDINARY_SHARE",
    }
)
_EXCLUDED_CATEGORIES = frozenset(
    {"ETF", "FUND", "BOND", "INDEX", "OPTION", "ETF_OPTION"}
)
_REASON_ORDER = (
    "listing_state_unproven",
    "as_of_unproven",
    "scope_conflict",
    "source_failed",
    "missing_factor_record",
    "missing_pe_ttm",
    "non_positive_pe_ttm",
    "missing_pb_mrq",
    "non_positive_pb_mrq",
    "missing_profit_ttm",
    "missing_parent_equity",
    "non_positive_average_parent_equity",
    "missing_turnover",
    "insufficient_trading_sessions",
    "unsupported_source",
)
_REASON_INDEX = {reason: index for index, reason in enumerate(_REASON_ORDER)}
_SCORE_QUANTUM = Decimal("0.000001")


class ScreeningContractError(ValueError):
    """A deterministic screening input or plan contract failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value


def _dec_string(value: Decimal, *, places: int | None = None) -> str:
    if places is not None:
        return format(
            value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN),
            f".{places}f",
        )
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _ordered_reasons(reasons: Iterable[str]) -> list[str]:
    return sorted(
        set(reasons),
        key=lambda reason: (_REASON_INDEX.get(reason, len(_REASON_INDEX)), reason),
    )


def _public_value(value: object) -> object:
    if isinstance(value, Decimal):
        return _dec_string(value)
    if isinstance(value, Mapping):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value


def _canonical_security_id(security: UniverseSecurity) -> str:
    return f"{security.exchange}:{security.code}"


def _input_value(value: object) -> Decimal | None:
    if isinstance(value, Mapping):
        return _decimal(value.get("value"))
    return _decimal(value)


def _factor_values(record: Mapping[str, object]) -> Mapping[str, object]:
    value = record.get("factor_inputs", record.get("factors", {}))
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _validate_scope(
    record: Mapping[str, object], screen_date: str, cutoff: str, reasons: set[str]
) -> None:
    """Apply the same frozen-date guard when callers bypass acquisition."""
    for key, expected in (("screen_date", screen_date), ("information_cutoff", cutoff)):
        actual = record.get(key)
        if actual is not None and actual != expected:
            reasons.add("scope_conflict")
    inputs = _factor_values(record)
    for value in inputs.values():
        values: Sequence[object]
        if isinstance(value, Mapping) and isinstance(value.get("sessions"), Sequence):
            values = cast(Sequence[object], value["sessions"])
        else:
            values = (value,)
        for item in values:
            if not isinstance(item, Mapping):
                continue
            available = item.get("available_at")
            if isinstance(available, str) and available > cutoff:
                reasons.add("as_of_unproven")
            item_screen = item.get("screen_date")
            item_cutoff = item.get("information_cutoff")
            if (item_screen is not None and item_screen != screen_date) or (
                item_cutoff is not None and item_cutoff != cutoff
            ):
                reasons.add("scope_conflict")


def _build_factor_values(
    inputs: Mapping[str, object], reasons: set[str]
) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    pe = _input_value(inputs.get("pe_ttm"))
    if pe is None:
        reasons.add("missing_pe_ttm")
    elif pe <= 0:
        reasons.add("non_positive_pe_ttm")
    else:
        values["earnings_yield_ttm"] = Decimal(1) / pe
    pb = _input_value(inputs.get("pb_mrq"))
    if pb is None:
        reasons.add("missing_pb_mrq")
    elif pb <= 0:
        reasons.add("non_positive_pb_mrq")
    else:
        values["book_to_price_mrq"] = Decimal(1) / pb
    profit = _input_value(inputs.get("parent_profit_ttm"))
    current = _input_value(inputs.get("current_parent_equity_mrq"))
    prior = _input_value(inputs.get("prior_year_parent_equity_mrq"))
    if profit is None:
        reasons.add("missing_profit_ttm")
    if current is None or prior is None:
        reasons.add("missing_parent_equity")
    elif (current + prior) / Decimal(2) <= 0:
        reasons.add("non_positive_average_parent_equity")
    elif profit is not None:
        values["return_on_equity_ttm"] = profit / ((current + prior) / Decimal(2))
    turnover = inputs.get("turnover_amount_20_sessions")
    sessions: object = (
        turnover.get("sessions") if isinstance(turnover, Mapping) else turnover
    )
    rows = (
        sessions
        if isinstance(sessions, Sequence)
        and not isinstance(sessions, (str, bytes, bytearray))
        else ()
    )
    amounts = [_input_value(item) for item in rows]
    typed_amounts: list[Decimal] = [
        item for item in amounts if isinstance(item, Decimal)
    ]
    if len(rows) != 20 or len(typed_amounts) != 20:
        reasons.add("missing_turnover")
        if len(rows) < 20:
            reasons.add("insufficient_trading_sessions")
    else:
        session_dates = [
            item.get("session_date") for item in rows if isinstance(item, Mapping)
        ]
        states = [
            item.get("session_state") for item in rows if isinstance(item, Mapping)
        ]
        if len(session_dates) != 20 or len(set(session_dates)) != 20:
            reasons.add("insufficient_trading_sessions")
        if states and any(state not in {None, "completed"} for state in states):
            reasons.add("insufficient_trading_sessions")
        ordered = sorted(typed_amounts)
        values["median_turnover_amount_20_sessions"] = (
            ordered[9] + ordered[10]
        ) / Decimal(2)
    return values


def _percentiles(
    rows: Sequence[tuple[str, dict[str, Decimal]]], factor: str
) -> dict[str, Decimal]:
    ordered = sorted(rows, key=lambda item: (item[1][factor], item[0]))
    groups: dict[Decimal, list[str]] = {}
    for canonical_id, values in ordered:
        groups.setdefault(values[factor], []).append(canonical_id)
    ranks: dict[str, Decimal] = {}
    zero_rank = 0
    for value in sorted(groups):
        ids = groups[value]
        average = Decimal(2 * zero_rank + len(ids) - 1) / Decimal(2)
        for canonical_id in ids:
            ranks[canonical_id] = (
                Decimal(50)
                if len(ordered) == 1
                else Decimal(100) * average / Decimal(len(ordered) - 1)
            )
        zero_rank += len(ids)
    return ranks


@dataclass(frozen=True, slots=True)
class ScreeningPartition:
    exchange: str
    coverage: Mapping[str, int]
    excluded: tuple[Mapping[str, object], ...]
    factors: tuple[Mapping[str, object], ...]
    ranking: tuple[Mapping[str, object], ...]
    unranked: tuple[Mapping[str, object], ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "coverage": dict(self.coverage),
            "excluded": [dict(item) for item in self.excluded],
            "factors": [dict(item) for item in self.factors],
            "ranking": [dict(item) for item in self.ranking],
            "unranked": [dict(item) for item in self.unranked],
        }


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    requested_as_of: str | None
    screen_date: str
    information_cutoff: str
    availability_at: str | None
    plan_id: str
    public_state: str
    partitions: tuple[ScreeningPartition, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "requested_as_of": self.requested_as_of,
            "screen_date": self.screen_date,
            "information_cutoff": self.information_cutoff,
            "availability_at": self.availability_at,
            "screening_plan": {
                "id": self.plan_id,
                "factor_definitions": {
                    key: {
                        "formula": value["formula"],
                        "weight": _dec_string(cast(Decimal, value["weight"])),
                    }
                    for key, value in FACTOR_DEFINITIONS.items()
                },
                "percentile": "ascending_mid_rank_zero_based",
                "decimal_precision": "exact_decimal;score_round_half_even_6",
                "sort_keys": [
                    "research_priority_score:desc",
                    "canonical_security_id:asc",
                ],
                "tie_handling": "equal_scores_share_rank_and_score_tied",
            },
            "public_state": self.public_state,
            "partitions": {
                item.exchange: item.to_public_dict() for item in self.partitions
            },
        }


def screen_a_share_universe(
    universe: AShareUniverse,
    factor_records: Sequence[Mapping[str, object]],
    *,
    requested_as_of: str | None = None,
    availability_at: str | None = None,
) -> ScreeningResult:
    """Rank qualified ordinary shares and retain every excluded/unranked row."""

    by_id: dict[str, list[Mapping[str, object]]] = {}
    for record in factor_records:
        canonical = record.get("canonical_security_id")
        if isinstance(canonical, str):
            by_id.setdefault(canonical, []).append(record)
    all_rows: dict[
        str,
        tuple[
            UniverseSecurity, Mapping[str, object] | None, set[str], dict[str, Decimal]
        ],
    ] = {}
    for partition in universe.partitions:
        for security in partition.securities:
            canonical = _canonical_security_id(security)
            reasons = set(security.unranked_reasons)
            matches = by_id.get(canonical, [])
            factor_record: Mapping[str, object] | None = (
                matches[0] if len(matches) == 1 else None
            )
            if len(matches) > 1:
                reasons.add("scope_conflict")
            values: dict[str, Decimal] = {}
            category = security.security_category.upper()
            if category not in _ORDINARY_CATEGORIES:
                if category in _EXCLUDED_CATEGORIES or category:
                    reasons.add("asset_class_excluded")
            elif factor_record is None:
                reasons.add("missing_factor_record")
            else:
                _validate_scope(
                    factor_record,
                    universe.screen_date,
                    universe.information_cutoff,
                    reasons,
                )
                values = _build_factor_values(_factor_values(factor_record), reasons)
                declared = factor_record.get("unranked_reasons")
                declared_reasons = (
                    declared if isinstance(declared, (list, tuple)) else ()
                )
                for reason in declared_reasons:
                    if isinstance(reason, str):
                        reasons.add(reason)
            all_rows[canonical] = (security, factor_record, reasons, values)

    eligible = [
        (canonical, values)
        for canonical, (security, _record, reasons, values) in all_rows.items()
        if security.security_category.upper() in _ORDINARY_CATEGORIES and not reasons
    ]
    percentiles = {factor: _percentiles(eligible, factor) for factor in _FACTOR_ORDER}
    scored: dict[str, dict[str, object]] = {}
    with localcontext() as context:
        context.prec = 80
        for canonical, values in eligible:
            percentile_values = {
                factor: percentiles[factor][canonical] for factor in _FACTOR_ORDER
            }
            score = sum(
                (
                    percentile_values[factor]
                    * cast(Decimal, FACTOR_DEFINITIONS[factor]["weight"])
                    for factor in _FACTOR_ORDER
                ),
                Decimal(0),
            ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
            scored[canonical] = {
                "canonical_security_id": canonical,
                "factor_values": {
                    factor: _dec_string(values[factor]) for factor in _FACTOR_ORDER
                },
                "percentiles": {
                    factor: _dec_string(percentile_values[factor], places=6)
                    for factor in _FACTOR_ORDER
                },
                "research_priority_score": _dec_string(score, places=6),
                "score": score,
            }
    ordered_scored = sorted(
        scored.values(),
        key=lambda row: (
            -cast(Decimal, row["score"]),
            cast(str, row["canonical_security_id"]),
        ),
    )
    raw_scores = [cast(Decimal, row["score"]) for row in ordered_scored]
    previous_rank = 0
    for index, row in enumerate(ordered_scored):
        score = raw_scores[index]
        same_before = index > 0 and raw_scores[index - 1] == score
        same_after = index + 1 < len(ordered_scored) and raw_scores[index + 1] == score
        row["rank"] = previous_rank if same_before else index + 1
        previous_rank = cast(int, row["rank"])
        row["score_tied"] = same_before or same_after
        del row["score"]

    partitions: list[ScreeningPartition] = []
    for exchange in _EXCHANGES:
        rows = [
            (canonical, item)
            for canonical, item in all_rows.items()
            if item[0].exchange == exchange
        ]
        excluded: list[Mapping[str, object]] = []
        factors: list[Mapping[str, object]] = []
        ranking: list[Mapping[str, object]] = []
        for item in ordered_scored:
            canonical = cast(str, item["canonical_security_id"])
            if canonical.split(":", 1)[0] == exchange:
                ranked = dict(item)
                ranked["resolved"] = (
                    all_rows[canonical][0].identity_operation_id is not None
                )
                ranking.append(ranked)
        unranked: list[Mapping[str, object]] = []
        for canonical, (security, factor_record, reasons, _values) in sorted(rows):
            category = security.security_category.upper()
            if category not in _ORDINARY_CATEGORIES:
                excluded.append(
                    {
                        "canonical_security_id": canonical,
                        "security_category": security.security_category,
                        "reason": "asset_class_excluded",
                        "resolved": security.identity_operation_id is not None,
                    }
                )
                continue
            if factor_record is not None:
                acquired = {
                    "canonical_security_id": canonical,
                    "factor_inputs": _public_value(
                        factor_record.get("factor_inputs", {})
                    ),
                    "unranked_reasons": _ordered_reasons(reasons),
                    "resolved": security.identity_operation_id is not None,
                }
                factors.append(acquired)
            if canonical not in scored:
                unranked.append(
                    {
                        "canonical_security_id": canonical,
                        "unranked_reasons": _ordered_reasons(reasons),
                        "resolved": security.identity_operation_id is not None,
                    }
                )
        expected = len(rows)
        resolved = sum(
            1
            for _canonical, (security, _record, _reasons, _values) in rows
            if security.identity_operation_id is not None
        )
        acquired_count = len(factors)

        def has_reason(item: Mapping[str, object], reason: str) -> bool:
            raw_reasons = item.get("unranked_reasons")
            return isinstance(raw_reasons, (list, tuple, set)) and reason in raw_reasons

        failed = sum(has_reason(item, "source_failed") for item in unranked)
        unsupported = sum(has_reason(item, "unsupported_source") for item in unranked)
        partitions.append(
            ScreeningPartition(
                exchange,
                {
                    "expected": expected,
                    "excluded": len(excluded),
                    "resolved": resolved,
                    "acquired": acquired_count,
                    "ranked": len(ranking),
                    "unranked": len(unranked),
                    "failed": failed,
                    "unsupported": unsupported,
                },
                tuple(excluded),
                tuple(factors),
                tuple(ranking),
                tuple(unranked),
            )
        )
    if availability_at is None:
        observed_times = [
            cast(str, record.get("available_at"))
            for record in factor_records
            if isinstance(record.get("available_at"), str)
        ]
        availability_at = max(observed_times, default=universe.information_cutoff)
    return ScreeningResult(
        requested_as_of,
        universe.screen_date,
        universe.information_cutoff,
        availability_at,
        SCREENING_PLAN_ID,
        SCREENING_PUBLIC_STATE,
        tuple(partitions),
    )


rank_a_share_universe = screen_a_share_universe
run_fundamental_research_priority = screen_a_share_universe

__all__ = [
    "FACTOR_DEFINITIONS",
    "ScreeningContractError",
    "SCREENING_PLAN_ID",
    "SCREENING_PUBLIC_STATE",
    "ScreeningPartition",
    "ScreeningResult",
    "rank_a_share_universe",
    "run_fundamental_research_priority",
    "screen_a_share_universe",
]
