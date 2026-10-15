"""Fixed four-index observations behind the Market Foundation operation Seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Context, Decimal, DecimalException, localcontext
from fractions import Fraction
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from .bounded_public_data import validate_bounded_public_data
from .market_session_context import ASIA_SHANGHAI, MarketSessionOperationRequest
from .research_result import (
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
)

CAPABILITY_ID = "major_broad_indices"
OPERATION_ID = "fixed_four_indices@1"
FIXED_INDEX_IDS = ("SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050")


class SessionAdapter(Protocol):
    def execute(
        self, request: MarketSessionOperationRequest, timeout_seconds: float
    ) -> OperationOutcome: ...


@dataclass(frozen=True)
class FixedIndexOperationRequest:
    session_request: MarketSessionOperationRequest
    purpose: Literal["current_session", "completed_session"] = "completed_session"
    completed_basis: Literal["frozen_context", "latest_index_session"] = (
        "frozen_context"
    )

    def __post_init__(self) -> None:
        if self.purpose not in ("current_session", "completed_session"):
            raise ValueError("Fixed index purpose is unsupported.")
        if self.completed_basis not in ("frozen_context", "latest_index_session"):
            raise ValueError("Fixed index completion basis is unsupported.")


class DeterministicFixedIndexAdapter:
    def __init__(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        session_adapter: SessionAdapter,
        clock: Callable[[], datetime],
    ) -> None:
        self._rows = rows
        self._sessions = session_adapter
        self._clock = clock

    @property
    def obligation(self) -> OperationObligation:
        return OperationObligation(
            CAPABILITY_ID,
            OPERATION_ID,
            "market_observation",
            True,
            None,
            (EvidenceUsePolicy(CAPABILITY_ID, "supporting"),),
        )

    @property
    def field_dispositions(self) -> tuple[FieldDisposition, ...]:
        return (
            FieldDisposition(
                "normalized_index_evidence",
                "normalized",
                "observations[].payload.payload.observation",
            ),
        )

    def execute(
        self, request: FixedIndexOperationRequest, timeout_seconds: float
    ) -> OperationOutcome:
        session = self._sessions.execute(request.session_request, timeout_seconds)
        projected = session.to_dict()["observations"]
        matching = [
            item
            for item in projected
            if item.get("data_type") == "market_session_context"
        ]
        if len(matching) != 1:
            return self._outcome(
                (),
                (
                    {
                        "source_operation": OPERATION_ID,
                        "code": "index_session_context_unproved",
                        "message": "Required session context is not proved.",
                    },
                ),
            )
        payload = cast(dict[str, Any], matching[0]["payload"])
        descriptor = payload["session"]
        context = payload["context"]
        try:
            use = _session_use(request, payload)
        except _IndexError as error:
            return self._outcome(
                (),
                (
                    {
                        "source_operation": OPERATION_ID,
                        "code": error.code,
                        "message": "Required index session use is not proved.",
                    },
                ),
            )
        current, latest_index = use.current, use.latest_index
        target_date, temporal_basis = use.target_date, use.temporal_basis
        valid = []
        errors = []
        rows: list[Mapping[str, object]] = []
        for ordinal, candidate in enumerate(self._rows):
            try:
                carrier = OperationOutcome(
                    CAPABILITY_ID,
                    OPERATION_ID,
                    OperationExecution("succeeded", True),
                    observations=(
                        {
                            "source_operation": OPERATION_ID,
                            "payload": {"row": candidate},
                        },
                    ),
                )
                snapshot = cast(
                    dict[str, Any], carrier.to_dict()["observations"][0]["payload"]
                )["row"]
                if not isinstance(snapshot, dict):
                    raise ValueError("Index normalized row is not an object.")
                rows.append(cast(Mapping[str, object], snapshot))
            except (TypeError, ValueError, KeyError, RecursionError):
                errors.append(
                    {
                        "source_operation": OPERATION_ID,
                        "code": "index_projection_invalid",
                        "message": "Index normalized input cannot be safely observed. Input row: "
                        + str(ordinal)
                        + ".",
                    }
                )
        if any(row.get("canonical_id") not in FIXED_INDEX_IDS for row in rows):
            errors.append(
                {
                    "source_operation": OPERATION_ID,
                    "code": "index_identity_unexpected",
                    "message": "Index evidence contains an identity outside the fixed set.",
                }
            )
        for canonical_id in FIXED_INDEX_IDS:
            candidates = [
                row for row in rows if row.get("canonical_id") == canonical_id
            ]
            if not candidates:
                errors.append(
                    {
                        "source_operation": OPERATION_ID,
                        "code": "index_identity_missing",
                        "message": "Required index evidence is missing."
                        + " Index: "
                        + canonical_id
                        + ".",
                    }
                )
                continue
            if len(candidates) > 1:
                errors.append(
                    {
                        "source_operation": OPERATION_ID,
                        "code": "index_identity_duplicate",
                        "message": "Index identity occurs more than once."
                        + " Index: "
                        + canonical_id
                        + ".",
                    }
                )
                continue
            for row in candidates:
                if (
                    not _ROW_FIELDS
                    <= set(row)
                    <= _ROW_FIELDS
                    | {
                        "arithmetic_basis",
                        "source_time_precision",
                        "source_time_semantics",
                        "source_field_values",
                        "source_clock_fields",
                        "baseline_provenance",
                        "derived_metrics",
                        "observation_boundary",
                        "quote_provenance",
                    }
                ):
                    errors.append(
                        {
                            "source_operation": OPERATION_ID,
                            "code": "index_row_invalid",
                            "message": "Index normalized row fields are incomplete or unreviewed."
                            + " Index: "
                            + canonical_id
                            + ".",
                        }
                    )
                    continue
                try:
                    _provenance(row)
                except _IndexError as error:
                    errors.append(
                        {
                            "source_operation": OPERATION_ID,
                            "code": error.code,
                            "message": "Index source provenance cannot be proved."
                            + " Index: "
                            + canonical_id
                            + ".",
                        }
                    )
                    continue
                if row["session_date"] != target_date:
                    errors.append(
                        {
                            "source_operation": OPERATION_ID,
                            "code": "index_session_mismatch",
                            "message": "Index evidence does not match the proved applicable session."
                            + " Index: "
                            + canonical_id
                            + ".",
                        }
                    )
                else:
                    try:
                        retrieved = _instant(row.get("retrieved_at"))
                        checked = _instant(self._clock())
                        precision = row.get("source_time_precision", "second")
                        if precision == "day":
                            if (
                                current
                                or row.get("observed_at") is not None
                                or retrieved
                                < _auction_boundary(context, canonical_id, target_date)
                            ):
                                raise ValueError
                            observed = None
                        elif precision == "second":
                            observed = _instant(row.get("observed_at"))
                            if (
                                observed.date().isoformat() != target_date
                                or observed > retrieved
                            ):
                                raise ValueError
                            if not current and observed < _auction_boundary(
                                context, canonical_id, target_date
                            ):
                                raise ValueError
                        else:
                            raise ValueError
                        if retrieved > checked:
                            raise ValueError
                    except (ValueError, TypeError):
                        errors.append(
                            {
                                "source_operation": OPERATION_ID,
                                "code": "index_timestamp_unproved",
                                "message": "Index source chronology cannot be proved."
                                + " Index: "
                                + canonical_id
                                + ".",
                            }
                        )
                        continue
                    expected_price = "current_value" if current else "completed_close"
                    allowed_prices = (
                        {expected_price, "completed_session_reported_value"}
                        if latest_index
                        else {expected_price}
                    )
                    if row.get("price_semantics") not in allowed_prices:
                        errors.append(
                            {
                                "source_operation": OPERATION_ID,
                                "code": "index_price_semantics_mismatch",
                                "message": "Index price semantics do not match the requested use."
                                + " Index: "
                                + canonical_id
                                + ".",
                            }
                        )
                        continue
                    if (
                        current
                        and descriptor["freshness_basis"] == "current_session_hold"
                        and (
                            observed is None
                            or row.get("observation_boundary") != "latest_source_quote"
                            or not _auction_boundary(
                                context, canonical_id, target_date, position=2
                            )
                            <= observed
                            <= _auction_boundary(
                                context, canonical_id, target_date, position=3
                            )
                        )
                    ):
                        errors.append(
                            {
                                "source_operation": OPERATION_ID,
                                "code": "index_morning_observation_unproved",
                                "message": "Midday index use requires the source's latest compatible morning quote. Index: "
                                + canonical_id
                                + ".",
                            }
                        )
                        continue
                    if (
                        current
                        and observed is not None
                        and descriptor["freshness_basis"] == "active_session"
                        and (checked - observed).total_seconds() > 60
                    ):
                        errors.append(
                            {
                                "source_operation": OPERATION_ID,
                                "code": "index_observation_stale",
                                "message": "Current index source observation exceeds the active-session age limit."
                                + " Index: "
                                + canonical_id
                                + ".",
                            }
                        )
                        continue
                    try:
                        _values(row)
                    except _IndexError as error:
                        errors.append(
                            {
                                "source_operation": OPERATION_ID,
                                "code": error.code,
                                "message": "Index value, unit or arithmetic cannot be proved."
                                + " Index: "
                                + canonical_id
                                + ".",
                            }
                        )
                        continue
                    try:
                        validate_bounded_public_data(
                            row,
                            artifact="index observation",
                            allow_floats=False,
                            max_total_string_length=16384,
                        )
                    except (ValueError, TypeError):
                        errors.append(
                            {
                                "source_operation": OPERATION_ID,
                                "code": "index_projection_invalid",
                                "message": "Index evidence cannot be safely projected."
                                + " Index: "
                                + canonical_id
                                + ".",
                            }
                        )
                        continue
                    valid.append(
                        {
                            "id": OPERATION_ID
                            + ":"
                            + request.purpose
                            + (":" + request.completed_basis if not current else "")
                            + ":"
                            + canonical_id,
                            "source_operation": OPERATION_ID,
                            "data_type": "fixed_index_observation",
                            "retrieved_at": row["retrieved_at"],
                            "payload": {
                                "observation": dict(row),
                                "session_operation": session.operation_id,
                                "context_digest": context["digest"],
                                "temporal_basis": temporal_basis,
                                "purpose": request.purpose,
                                "completed_basis": request.completed_basis
                                if not current
                                else None,
                            },
                        }
                    )
        return self._outcome(tuple(valid), tuple(errors))

    def _outcome(
        self,
        observations: tuple[Mapping[str, object], ...],
        errors: tuple[Mapping[str, object], ...],
    ) -> OperationOutcome:
        return OperationOutcome(
            CAPABILITY_ID,
            OPERATION_ID,
            OperationExecution("failed")
            if errors
            else OperationExecution("succeeded", True),
            observations=observations,
            source_errors=errors,
            field_dispositions=self.field_dispositions,
        )


@dataclass(frozen=True)
class _IndexSessionUse:
    current: bool
    latest_index: bool
    target_date: str
    temporal_basis: str


def _session_use(
    request: FixedIndexOperationRequest, payload: Mapping[str, Any]
) -> _IndexSessionUse:
    """Resolve the fixed internal plan once from the owning session result."""
    context, descriptor = payload["context"], payload["session"]
    current = request.purpose == "current_session"
    same_day = context["requested_as_of"] == context["research_now"][:10]
    if current and (not descriptor["current_session_applicable"] or not same_day):
        raise _IndexError("index_current_session_unproved")
    latest = (
        not current and request.completed_basis == "latest_index_session" and same_day
    )
    target = (
        descriptor["session_date"]
        if current
        else descriptor["latest_regular_auction_completed_session"]
        if latest
        else context["screen_date"]
    )
    if target is None:
        raise _IndexError("index_session_context_unproved")
    return _IndexSessionUse(
        current,
        latest,
        str(target),
        "applicable_current_session"
        if current
        else "latest_regular_index_session"
        if latest
        else "frozen_completed_session",
    )


def _instant(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Index instant requires an explicit timezone.")
    try:
        return value.astimezone(ASIA_SHANGHAI)
    except (ValueError, OverflowError) as error:
        raise ValueError(
            "Index instant cannot be represented in Asia/Shanghai."
        ) from error


class _IndexError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 128:
        raise _IndexError("index_value_invalid")
    try:
        result = Decimal(value)
    except DecimalException as error:
        raise _IndexError("index_value_invalid") from error
    if not result.is_finite():
        raise _IndexError("index_value_invalid")
    parts = result.as_tuple()
    if (
        len(parts.digits) > 100
        or not isinstance(parts.exponent, int)
        or abs(parts.exponent) > 100
    ):
        raise _IndexError("index_value_invalid")
    return result


def _values(row: Mapping[str, object]) -> None:
    if row.get("unit") != "index_point":
        raise _IndexError("index_unit_mismatch")
    if row.get("baseline_semantics") != "previous_session_close":
        raise _IndexError("index_arithmetic_mismatch")
    value, previous, change, percent = (
        _decimal(row.get(key))
        for key in ("value", "previous_close", "change", "change_percent")
    )
    if value <= 0 or previous <= 0:
        raise _IndexError("index_value_invalid")
    if row.get("arithmetic_basis") == "source_reported":
        if not _reported_consistent(
            value, previous, change, percent, str(row["canonical_id"])
        ):
            raise _IndexError("index_arithmetic_mismatch")
        return
    if row.get("arithmetic_basis") not in (None, "exact"):
        raise _IndexError("index_arithmetic_mismatch")
    with localcontext(Context(prec=512)):
        if change != value - previous or percent * previous != change * 100:
            raise _IndexError("index_arithmetic_mismatch")


_ROW_FIELDS = frozenset(
    (
        "canonical_id",
        "value",
        "previous_close",
        "change",
        "change_percent",
        "unit",
        "price_semantics",
        "baseline_semantics",
        "session_date",
        "observed_at",
        "retrieved_at",
        "provenance_uri",
        "source_version",
        "synthetic",
    )
)


def _provenance(row: Mapping[str, object]) -> None:
    uri, version = row.get("provenance_uri"), row.get("source_version")
    if (
        not isinstance(uri, str)
        or not uri
        or not isinstance(version, str)
        or not 0 < len(version) <= 128
        or not isinstance(row.get("synthetic"), bool)
    ):
        raise _IndexError("index_provenance_unproved")
    if uri.startswith("fixture:") and row["synthetic"] is True:
        return
    try:
        parts = urlsplit(uri)
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise ValueError
    except ValueError as error:
        raise _IndexError("index_provenance_unproved") from error


def _reported_consistent(
    value: Decimal,
    previous: Decimal,
    change: Decimal,
    percent: Decimal,
    canonical_id: str,
) -> bool:
    # One closed display quantum permits the source's declared display precision
    # without guessing its hidden rounding direction or rewriting reported values.
    numbers = (value, previous, change, percent)
    quantum = Fraction(1, 100)
    quanta = [quantum] * 4
    if canonical_id.startswith("SZSE:"):
        quanta = [
            Fraction(Decimal((0, (1,), min(int(item.as_tuple().exponent), -2))))
            for item in numbers
        ]
    v, p, d, r = (Fraction(item) for item in numbers)
    qv, qp, qd, qr = quanta
    p_low, p_high = max(Fraction(0), p - qp), p + qp
    v_low, v_high = max(Fraction(0), v - qv), v + qv
    d_low, d_high = d - qd, d + qd
    r_low, r_high = (r - qr) / 100, (r + qr) / 100
    lower = ((Fraction(0), d_low), (Fraction(-1), v_low), (r_low, Fraction(0)))
    upper = ((Fraction(0), d_high), (Fraction(-1), v_high), (r_high, Fraction(0)))
    lines = (*lower, *upper)
    candidates = {p_low, p_high}
    for first, (a, b) in enumerate(lines):
        for c, e in lines[first + 1 :]:
            if a != c:
                point = (e - b) / (a - c)
                if p_low <= point <= p_high:
                    candidates.add(point)
    # This is exact feasibility for p>0, v>0, d=v-p, and percent=100*d/p within
    # the source/report/display intervals; separate unrelated overlaps cannot pass.
    return any(
        point > 0
        and max(a * point + b for a, b in lower) <= min(a * point + b for a, b in upper)
        and min(a * point + b for a, b in upper) > -point
        for point in candidates
    )


def _auction_boundary(
    context: Mapping[str, Any],
    canonical_id: str,
    session_date: str,
    *,
    position: int = -1,
) -> datetime:
    # Use the owning #117 proof's declared auction boundary; do not infer
    # trading days, actual index completion or a source observation timestamp.
    proof = next(
        item
        for item in context["calendar_proofs"]
        if item["exchange"] == canonical_id.split(":")[0]
    )
    return datetime.combine(
        date.fromisoformat(session_date),
        time.fromisoformat(proof["auction_times"][position]),
        ASIA_SHANGHAI,
    )
