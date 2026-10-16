"""Qualification-backed completed-day market report normalization and fold.

The injected acquisition interface returns a parsed report stage. It is not a
provider wire schema: each real transport must establish its own raw parser,
date selector and publication evidence before supplying this stage.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Protocol, Sequence, cast

from .exact_normalization import ExactDecimalTerm, exact_scaled_sum
from .identity_sources import SourceOperationError
from .operation_release_matrix import (
    canonical_release_source_error_codes,
    derive_release_source_result,
    load_operation_qualification_manifest,
    market_foundation_report_facts,
)
from .provider_lane import ProviderInvocation, ProviderLanePolicy, ProviderLanes
from .research_result import (
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
)

EXCHANGES = ("SSE", "SZSE", "BSE")
OWNER_IDS = tuple(
    f"{exchange.lower()}_{suffix}@1"
    for suffix in ("a_share_turnover", "market_margin")
    for exchange in EXCHANGES
)
_REFERENCE = (
    Path(__file__).resolve().parents[2]
    / "references"
    / "market-foundation-owner-qualification.json"
)
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE]-?(?:0|[1-9][0-9]*))?$")
_REPORT_KEYS = {
    "exchange",
    "asset_scope",
    "report_date",
    "published_at",
    "retrieved_at",
    "source_fields",
    "source_units",
    "query_complete",
    "row_count",
}


@dataclass(frozen=True)
class OperationRequest:
    """One completed report date and its frozen information cutoff."""

    report_date: str
    information_cutoff: str

    def __post_init__(self) -> None:
        day = _date(self.report_date)
        cutoff = _timestamp(self.information_cutoff)
        if cutoff.date() < day:
            raise ValueError("market report cutoff precedes report date")


class MarketFoundationReportTransport(Protocol):
    """Acquire a parsed source report via execution-owned leaf invocations.

    A transport owns raw decoding and may not hide retries or leaf requests.
    Report-stage fixtures only verify normalization; they prove no wire parser.
    """

    def acquire(
        self,
        selector: Mapping[str, object],
        request: OperationRequest,
        timeout: float,
        invocation: ProviderInvocation,
    ) -> Mapping[str, object]: ...


class UnprovedMarketFoundationSource(ValueError):
    """A real owner transport/parser has not yet been established."""


class MarketFoundationPermissionDenied(Exception):
    """A transport observed an explicit source permission rejection."""


class MarketFoundationOperationAdapter:
    """Own report normalization, immutable outcomes and actual attempt proof."""

    def __init__(
        self,
        operation_id: str,
        contract: Mapping[str, object],
        transport: MarketFoundationReportTransport | None,
        lanes: ProviderLanes,
    ) -> None:
        authoritative = _load_contracts().get(operation_id)
        if authoritative is None or json.dumps(contract, sort_keys=True) != json.dumps(
            authoritative, sort_keys=True
        ):
            raise ValueError("market owner qualification contract is invalid")
        self.operation_id = operation_id
        self._contract_json = json.dumps(contract, sort_keys=True)
        self._transport = transport
        self._lanes = lanes
        self._issued: list[tuple[OperationOutcome, int]] = []

    @property
    def obligation(self) -> OperationObligation:
        contract = json.loads(self._contract_json)
        capability = contract["capability_id"]
        return OperationObligation(
            capability,
            self.operation_id,
            "market_observation",
            False,
            contract["scope"]["exchange"],
            (EvidenceUsePolicy(capability, "supporting"),),
        )

    @property
    def field_dispositions(self) -> tuple[FieldDisposition, ...]:
        contract = json.loads(self._contract_json)
        return tuple(
            FieldDisposition(field, "normalized", destination)
            for field, destination in contract["field_destinations"].items()
        )

    def execute(self, request: OperationRequest, timeout: float) -> OperationOutcome:
        """Run a bounded supplied acquisition without claiming a missing parser."""

        if self._transport is None:
            raise UnprovedMarketFoundationSource(
                "market owner raw parser and exact-date transport are unproved"
            )
        if (
            isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 60
        ):
            raise ValueError("market owner timeout is invalid")
        contract = cast(dict[str, object], json.loads(self._contract_json))
        selector = _mapping(contract["request_selector"])
        provider = _mapping(contract["provider"])
        transport = self._transport

        class Acquisition:
            def execute(self, invocation: ProviderInvocation) -> OperationOutcome:
                try:
                    report = transport.acquire(selector, request, timeout, invocation)
                except MarketFoundationPermissionDenied:
                    return _unavailable(contract, source_result="credential_blocked")
                except SourceOperationError as error:
                    return _unavailable(
                        contract,
                        error_code=error.code
                        if error.source_operation == contract["producer_operation_id"]
                        else "market_source_unavailable",
                    )
                # #116 supplies the detached recursively immutable snapshot.
                snapshot = OperationOutcome(
                    str(contract["capability_id"]),
                    str(contract["producer_operation_id"]),
                    OperationExecution("succeeded", query_complete=True),
                    observations=(
                        {
                            "source_operation": contract["producer_operation_id"],
                            "report": report,
                        },
                    ),
                )
                return _normalize_report(contract, snapshot, request)

        result = self._lanes.run(
            provider_group=str(provider["id"]),
            adapter=Acquisition(),
            policy=ProviderLanePolicy(
                actual_attempt_budget=cast(int, selector["max_requests"]),
                timeout_seconds=timeout,
            ),
        )
        attempts = len(result.attempts)
        if not attempts:
            outcome = _unavailable(contract, invoked=False)
        elif result.status == "succeeded" and result.value is not None:
            outcome = result.value
        else:
            outcome = _unavailable(contract)
        self._issued.append((outcome, attempts))
        return outcome

    def _attempt_count(self, outcome: OperationOutcome) -> int | None:
        return next((count for item, count in self._issued if item is outcome), None)


def build_default_market_foundation_operations(
    *,
    transports: Mapping[str, MarketFoundationReportTransport] | None = None,
    lanes: ProviderLanes | None = None,
) -> tuple[MarketFoundationOperationAdapter, ...]:
    """Load the exact six accepted owners from the installed reference.

    Missing real transports remain explicit construction gaps. This builder
    does not invent HTTP endpoints or return permanent failed placeholders.
    """

    supplied = dict(transports or {})
    if set(supplied) - set(OWNER_IDS):
        raise ValueError("market transport owner is outside the qualified set")
    if "bse_market_margin@1" not in supplied:
        from .market_foundation_sources import BseMarketMarginReportTransport

        supplied["bse_market_margin@1"] = BseMarketMarginReportTransport()
    if "bse_a_share_turnover@1" not in supplied:
        from .market_foundation_sources import BseMarketTurnoverReportTransport

        supplied["bse_a_share_turnover@1"] = BseMarketTurnoverReportTransport()
    if "sse_market_margin@1" not in supplied:
        from .market_foundation_sources import SseMarketMarginReportTransport

        supplied["sse_market_margin@1"] = SseMarketMarginReportTransport()
    if "sse_a_share_turnover@1" not in supplied:
        from .market_foundation_sources import SseMarketTurnoverReportTransport

        supplied["sse_a_share_turnover@1"] = SseMarketTurnoverReportTransport()
    if "szse_market_margin@1" not in supplied:
        from .market_foundation_sources import SzseMarketMarginReportTransport

        supplied["szse_market_margin@1"] = SzseMarketMarginReportTransport()
    if "szse_a_share_turnover@1" not in supplied:
        from .market_foundation_sources import SzseMarketTurnoverReportTransport

        supplied["szse_a_share_turnover@1"] = SzseMarketTurnoverReportTransport()
    contracts = _load_contracts()
    shared_lanes = lanes or ProviderLanes()
    return tuple(
        MarketFoundationOperationAdapter(
            owner, contracts[owner], supplied.get(owner), shared_lanes
        )
        for owner in OWNER_IDS
    )


def _load_contracts() -> dict[str, Mapping[str, object]]:
    payload = json.loads(_REFERENCE.read_text(encoding="utf-8"))
    qualifications = load_operation_qualification_manifest(
        payload, expected_producer_operation_ids=OWNER_IDS
    )
    return {
        item.producer_operation_id: cast(Mapping[str, object], item.source_contract)
        for item in qualifications
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("market report object is invalid")
    return cast(Mapping[str, object], value)


def _date(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
    ):
        raise ValueError("market report date is invalid")
    return date.fromisoformat(value)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00", value
    ):
        raise ValueError("market source timestamp is invalid")
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(hours=8):
        raise ValueError("market source timestamp offset is invalid")
    return result


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 256 or not _DECIMAL.fullmatch(value):
        raise ValueError("market source decimal is invalid")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("market source decimal is invalid") from error
    if not result.is_finite() or result < 0:
        raise ValueError("market source decimal is invalid")
    return result


def _number(value: Decimal) -> str:
    # Scientific notation bounds the result even for very large exponents.
    parts = value.as_tuple()
    if (
        isinstance(parts.exponent, int)
        and max(len(parts.digits) + parts.exponent, -parts.exponent) <= 256
    ):
        return format(value, "f")
    text = str(value)
    if "E+" in text:
        text = text.replace("E+", "e")
    return text


def _source_unit(contract: Mapping[str, object], *, quantity: bool) -> tuple[int, str]:
    normalization = _mapping(contract["normalization"])
    if quantity:
        return (
            (4, "ten_thousand_shares")
            if normalization["quantity_scale"]
            == "multiply_ten_thousand_shares_by_10000_using_exact_decimal"
            else (0, "shares")
        )
    rule = normalization.get("scale", normalization.get("money_scale"))
    if rule in {
        "multiply_each_hundred_million_CNY_value_by_100000000_then_exact_sum",
        "multiply_hundred_million_CNY_by_100000000_using_exact_decimal",
        "multiply_each_hundred_million_CNY_value_by_100000000_using_exact_decimal",
    }:
        return 8, "hundred_million_CNY"
    if rule == "multiply_ten_thousand_CNY_by_10000_using_exact_decimal":
        return 4, "ten_thousand_CNY"
    return 0, "CNY"


def _unavailable(
    contract: Mapping[str, object],
    *,
    source_result: str = "unavailable",
    error_code: str = "market_source_unavailable",
    diagnostics: Mapping[str, object] | None = None,
    invoked: bool = True,
) -> OperationOutcome:
    owner = str(contract["producer_operation_id"])
    messages = {
        "market_source_unavailable": "The market source did not provide a qualified report.",
        "source_schema_unproved": "The source report shape is unproved.",
        "source_response_unavailable": "The source response is unavailable.",
        "source_report_date_mismatch": "The source report date does not match the request.",
        "source_publication_unproved": "The source publication time is unproved.",
        "source_scope_unproved": "The source asset scope is unproved.",
        "source_report_date_unproved": "The independent source report date is unproved.",
        "source_report_empty_unqualified": "An empty source response does not prove zero business.",
    }
    if error_code not in messages:
        error_code = "market_source_unavailable"
    return OperationOutcome(
        str(contract["capability_id"]),
        owner,
        OperationExecution("failed" if invoked else "skipped_policy"),
        source_errors=(
            {
                "source_operation": owner,
                "code": "permission_denied"
                if source_result == "credential_blocked"
                else error_code,
                "message": "The market source explicitly rejected permission."
                if source_result == "credential_blocked"
                else messages[error_code],
            },
        )
        if invoked
        else (),
        field_dispositions=tuple(
            FieldDisposition(field, "normalized", str(destination))
            for field, destination in _mapping(contract["field_destinations"]).items()
        ),
        diagnostics=diagnostics or {},
    )


def _normalize_report(
    contract: Mapping[str, object],
    snapshot: OperationOutcome,
    request: OperationRequest,
) -> OperationOutcome:
    try:
        report = _mapping(snapshot.observations[0]["report"])
        extra = set(report) - _REPORT_KEYS
        if (
            not _REPORT_KEYS <= set(report)
            or extra
            - {
                "display_precision",
                "source_time_candidates",
                "unproved_fields",
                "complete_fields",
                "source_date_qualification",
                "source_scope_qualification",
            }
            or extra & {"display_precision", "source_time_candidates"}
            and snapshot.operation_id != "bse_market_margin@1"
        ):
            raise ValueError("market source report fields are invalid")
        scope = _mapping(contract["scope"])
        if (
            report["exchange"] != scope["exchange"]
            or report["asset_scope"] != scope["kind"]
        ):
            raise ValueError("market source scope is invalid")
        report_day = _date(report["report_date"])
        publication = (
            None
            if report["published_at"] is None
            else _timestamp(report["published_at"])
        )
        retrieval = _timestamp(report["retrieved_at"])
        if (
            report["report_date"] != request.report_date
            or publication is not None
            and (
                publication.date() < report_day
                or publication > retrieval
                or publication > _timestamp(request.information_cutoff)
            )
            or type(report["query_complete"]) is not bool
            or type(report["row_count"]) is not int
            or report["row_count"] < 0
        ):
            raise ValueError("market source time/completeness is invalid")
        fields = _mapping(report["source_fields"])
        source_units = _mapping(report["source_units"])
        destinations = _mapping(contract["field_destinations"])
        unproved = _mapping(report.get("unproved_fields", {}))
        if (
            not set(unproved) <= set(destinations)
            or set(unproved) & set(fields)
            or any(value != "mixed_quantity_unit" for value in unproved.values())
            or any(
                not str(destinations[field]).endswith("_shares") for field in unproved
            )
        ):
            raise ValueError("market source field eligibility is invalid")
        date_qualification = report.get(
            "source_date_qualification", "response_date_matches_request"
        )
        scope_qualification = report.get("source_scope_qualification", "qualified")
        if date_qualification not in {
            "response_date_matches_request",
            "date_control_only",
        } or scope_qualification not in {
            "qualified",
            "pure_A_CDR_exclusion_unproved",
            "CDR_inclusive",
        }:
            raise ValueError("market source qualification is invalid")
        margin = contract["capability_id"] == "whole_market_margin"
        empty = margin and report["query_complete"] is True and report["row_count"] == 0
        complete = report["query_complete"]
        expected_rows = 2 if snapshot.operation_id == "sse_a_share_turnover@1" else 1
        row_count = report["row_count"]
        if (
            row_count > expected_rows
            or complete
            and not empty
            and row_count != expected_rows
        ):
            raise ValueError("market required source rows are invalid")
        inventory_valid = (
            set(fields) == (set() if empty else set(destinations))
            if complete
            else set(fields) <= set(destinations)
        )
        if not inventory_valid or set(source_units) != set(fields):
            raise ValueError("market required source field inventory is invalid")
        complete_fields = report.get(
            "complete_fields", tuple(fields) if complete else ()
        )
        if (
            not isinstance(complete_fields, (tuple, list))
            or any(not isinstance(field, str) for field in complete_fields)
            or len(complete_fields) != len(set(complete_fields))
            or not set(complete_fields) <= set(fields)
        ):
            raise ValueError("market source field completeness is invalid")
        complete_metrics = sorted(
            {str(destinations[field]).rsplit(".", 1)[1] for field in complete_fields}
        )
        terms: dict[str, list[ExactDecimalTerm]] = {}
        units: dict[str, str] = {}
        for source_field, value in fields.items():
            destination = str(destinations[source_field]).rsplit(".", 1)[1]
            quantity = destination.endswith("_shares")
            scale, expected_unit = _source_unit(contract, quantity=quantity)
            if source_units[source_field] != expected_unit:
                raise ValueError("market source unit is invalid")
            terms.setdefault(destination, []).append(
                ExactDecimalTerm(_decimal(value), scale)
            )
            units[destination] = "shares" if quantity else "CNY"
        metrics = {
            field: _number(exact_scaled_sum(values)) for field, values in terms.items()
        }
        if (
            publication is None
            or scope_qualification != "qualified"
            or date_qualification != "response_date_matches_request"
        ):
            precision = _mapping(report.get("display_precision", {}))
            if (
                precision
                and set(precision) != set(fields)
                or any(
                    type(value) is not int or not 0 <= value <= 32
                    for value in precision.values()
                )
            ):
                raise ValueError("source display precision is invalid")
            candidates = _mapping(report.get("source_time_candidates", {}))
            if set(candidates) - {"createTime", "lastUpdateTime", "sxrq"}:
                raise ValueError("source time candidate fields are invalid")
            for value in candidates.values():
                if (
                    not isinstance(value, str)
                    or len(value) > 40
                    or datetime.fromisoformat(value).utcoffset() != timedelta(hours=8)
                ):
                    raise ValueError("source time candidate is invalid")
            proof = {
                "report_date": request.report_date,
                "retrieved_at": report["retrieved_at"],
                "metrics": metrics,
                "units": units,
                "row_count": row_count,
                "value_basis": "reported_display" if precision else "reported_source",
                "display_precision": dict(precision),
                "source_publication": "unknown" if publication is None else "proved",
                "source_scope_qualification": scope_qualification,
                "unproved_fields": dict(unproved),
                "complete_fields": complete_metrics,
                "source_time_candidates": dict(candidates),
                "source_time_qualification": "unqualified_source_record_times",
                "source_date_qualification": date_qualification
                if row_count
                else "date_control_only",
            }
            return _unavailable(
                contract,
                error_code="source_scope_unproved"
                if scope_qualification != "qualified"
                else "source_report_date_unproved"
                if row_count and date_qualification != "response_date_matches_request"
                else "source_publication_unproved"
                if row_count
                else "source_report_empty_unqualified",
                diagnostics={"unadopted_report": proof},
            )
        payload = {
            "id": f"{snapshot.operation_id}:{request.report_date}",
            "source_operation": snapshot.operation_id,
            "retrieved_at": report["retrieved_at"],
            "source_locator": _mapping(contract["request_selector"])["resource_url"],
            "observation": {
                "exchange": scope["exchange"],
                "asset_scope": scope["kind"],
                "report_date": request.report_date,
                "published_at": report["published_at"],
                "row_count": row_count,
                "metrics": metrics,
                "units": units,
            },
        }
        return OperationOutcome(
            snapshot.capability_id,
            snapshot.operation_id,
            OperationExecution("succeeded", query_complete=complete),
            observations=(payload,),
            diagnostics={
                "complete_fields": complete_metrics,
                "unproved_fields": dict(unproved),
            },
            field_dispositions=tuple(
                FieldDisposition(field, "normalized", str(destination))
                for field, destination in destinations.items()
            ),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return _unavailable(contract)


def fold_market_foundation(
    outcomes: Sequence[OperationOutcome],
    request: OperationRequest,
    *,
    operations: Sequence[MarketFoundationOperationAdapter],
) -> dict[str, object]:
    """Fold only operation-issued immutable proof, keeping every partition."""

    if any(
        type(operation) is not MarketFoundationOperationAdapter
        for operation in operations
    ):
        raise ValueError("market fold operation authority is invalid")
    owners = {operation.operation_id: operation for operation in operations}
    if len(owners) != len(operations) or set(owners) != set(OWNER_IDS):
        raise ValueError("market fold requires the exact qualified owner set")
    received: dict[str, OperationOutcome] = {}
    for supplied_outcome in outcomes:
        if (
            supplied_outcome.operation_id not in owners
            or supplied_outcome.operation_id in received
        ):
            raise ValueError("market fold owner is unexpected or duplicated")
        received[supplied_outcome.operation_id] = supplied_outcome
    public_operations: list[dict[str, object]] = []
    partitions: dict[str, list[dict[str, object]]] = {"turnover": [], "margin": []}
    for owner in OWNER_IDS:
        operation = owners[owner]
        outcome = received.get(owner)
        count = operation._attempt_count(outcome) if outcome is not None else None
        trusted = outcome is not None and count is not None
        result = (
            _report_source_result(outcome)
            if trusted and outcome is not None
            else "unavailable"
        )
        material: dict[str, object] | None = None
        lineage: dict[str, object] | None = None
        if trusted and outcome is not None and outcome.observations:
            plain = outcome.to_dict()["observations"][0]
            observation = _mapping(plain["observation"])
            if observation["report_date"] == request.report_date and _timestamp(
                observation["published_at"]
            ) <= _timestamp(request.information_cutoff):
                material = dict(observation)
                lineage = {
                    key: plain[key]
                    for key in (
                        "id",
                        "source_operation",
                        "source_locator",
                        "retrieved_at",
                    )
                }
            else:
                result = "unavailable"
        public_operations.append(
            {
                "operation_id": owner,
                "execution": outcome.execution.state
                if trusted and outcome is not None
                else "not_requested"
                if outcome is None
                else "failed",
                "source_result": result,
                "attempts": count if count is not None else 0,
                "attempted": count is not None and count > 0,
            }
        )
        kind = "turnover" if "a_share_turnover" in owner else "margin"
        partitions[kind].append(
            {
                "exchange": owner.split("_", 1)[0].upper(),
                "operation_id": owner,
                "source_result": result,
                "observation": material,
                "lineage": lineage,
                "complete_fields": outcome.to_dict()["diagnostics"].get(
                    "complete_fields", []
                )
                if trusted and outcome is not None and material is not None
                else [],
                "unproved_fields": outcome.to_dict()["diagnostics"].get(
                    "unproved_fields", {}
                )
                if trusted and outcome is not None and material is not None
                else {},
            }
        )
    return {
        "report_date": request.report_date,
        "operations": public_operations,
        "turnover": {
            "partitions": partitions["turnover"],
            "total": _total(partitions["turnover"], "turnover_amount_CNY"),
        },
        "margin": {
            "partitions": partitions["margin"],
            "totals": _margin_totals(partitions["margin"]),
        },
    }


def _report_source_result(outcome: OperationOutcome) -> str:
    projection = outcome.to_dict()
    operation: dict[str, object] = {
        "capability_id": outcome.capability_id,
        "operation_id": outcome.operation_id,
        "execution_state": outcome.execution.state,
        "state": outcome.coverage,
        "query_complete": outcome.execution.query_complete,
        "source_error_codes": list(
            canonical_release_source_error_codes(
                tuple(error.get("code") for error in projection["source_errors"])
            )
        ),
    }
    facts = market_foundation_report_facts(operation, projection["observations"])
    if facts is not None:
        operation["market_foundation_report_facts"] = facts
    return derive_release_source_result((operation,))


def _total(
    partitions: Sequence[Mapping[str, object]],
    field: str,
    *,
    allow_complete_field: bool = False,
) -> dict[str, object] | None:
    values: list[ExactDecimalTerm] = []
    evidence: list[str] = []
    for partition in partitions:
        observation = partition["observation"]
        usable = (
            partition["source_result"] in {"complete", "observed_empty"}
            or allow_complete_field
            and partition["source_result"] == "partial"
            and field in cast(Sequence[str], partition.get("complete_fields", ()))
        )
        if not usable or not isinstance(observation, Mapping):
            return None
        metrics = _mapping(observation["metrics"])
        units = _mapping(observation["units"])
        unit = "shares" if field.endswith("_shares") else "CNY"
        if field not in metrics or units.get(field) != unit:
            return None
        values.append(ExactDecimalTerm(_decimal(metrics[field]), 0))
        evidence.append(str(partition["operation_id"]))
    try:
        return {
            "value": _number(exact_scaled_sum(values)),
            "unit": unit,
            "operands": evidence,
        }
    except ValueError:
        return None


def _margin_totals(partitions: Sequence[Mapping[str, object]]) -> dict[str, object]:
    fields = (
        "financing_balance_CNY",
        "financing_buy_amount_CNY",
        "securities_lending_sell_quantity_shares",
        "securities_lending_quantity_shares",
        "securities_lending_balance_CNY",
        "margin_total_balance_CNY",
    )
    # The exact accepted six-owner authority forbids not_applicable for every
    # field. Absence cannot shrink any field's exchange denominator.
    return {
        field: _total(partitions, field, allow_complete_field=True) for field in fields
    }


def _public_report_dispositions(
    fields: Sequence[FieldDisposition],
    observations: Sequence[Mapping[str, object]],
    *,
    complete: bool,
) -> tuple[FieldDisposition, ...]:
    if not observations:
        return tuple(fields)
    report = _mapping(observations[0]["observation"])
    metrics = _mapping(report["metrics"])
    return tuple(
        field
        if field.destination is not None
        and field.destination.rsplit(".", 1)[-1] in metrics
        else FieldDisposition(
            field.field,
            "excluded",
            None,
            "source_explicitly_empty_report"
            if complete and report["row_count"] == 0
            else "source_field_not_available",
        )
        for field in fields
    )


def project_market_foundation_outcome(outcome: OperationOutcome) -> OperationOutcome:
    """Retain normalized field eligibility for public fold, after the owner fold.

    Empty and partial reports keep their real proof and exclude absent fields;
    their missing metrics never become zero. Use the original issued outcome
    for fold_market_foundation's operation authority before this projection.
    """
    projection = outcome.to_dict()
    observations = tuple(
        {**row, "complete_fields": projection["diagnostics"].get("complete_fields", [])}
        for row in projection["observations"]
    )
    return OperationOutcome(
        outcome.capability_id,
        outcome.operation_id,
        outcome.execution,
        observations=observations,
        source_errors=outcome.source_errors,
        field_dispositions=_public_report_dispositions(
            outcome.field_dispositions,
            observations,
            complete=outcome.execution.query_complete is True,
        ),
        diagnostics=outcome.diagnostics,
    )


def validate_market_foundation_research_result(result: Mapping[str, object]) -> None:
    try:
        _validate_market_foundation_research_result(result)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        raise ValueError(
            "market foundation facts contradict their owner contract"
        ) from error


def _validate_market_foundation_research_result(result: Mapping[str, object]) -> None:
    """Recheck retained report scope, field eligibility and exact total lineage.

    The overview retains only normalized owner proof. Hashes identify records;
    they do not replace the three-exchange scope and arithmetic checks here.
    """
    plan = _mapping(result["plan"])
    capability = (
        "whole_market_turnover"
        if plan["task_type"] == "overview_turnover"
        else "whole_market_margin"
    )
    contracts = {
        owner: contract
        for owner, contract in _load_contracts().items()
        if contract["capability_id"] == capability
    }
    obligations = plan["operation_obligations"]
    expected = [
        OperationObligation(
            capability,
            owner,
            "market_observation",
            False,
            str(_mapping(contracts[owner]["scope"])["exchange"]),
            (EvidenceUsePolicy(capability, "supporting"),),
        ).to_dict()
        for owner in OWNER_IDS
        if owner in contracts
    ]
    if obligations != expected:
        raise ValueError("market foundation owner authority is invalid")
    coverage = _mapping(_mapping(result["coverage"])[capability])
    operations = _mapping(coverage["operations"])
    observations = result["observations"]
    partitions = []
    references: dict[str, str] = {}
    report_dates: set[str] = set()
    for owner, contract in contracts.items():
        rows = [
            row
            for row in cast(Sequence[Mapping[str, object]], observations)
            if row["operation_id"] == owner
        ]
        if len(rows) > 1:
            raise ValueError("market report partition is duplicated")
        operation = _mapping(operations[owner])
        if operation["execution_state"] == "succeeded" and not rows:
            raise ValueError("market report success requires normalized report proof")
        expected_fields = [
            item.to_dict()
            for item in _public_report_dispositions(
                MarketFoundationOperationAdapter(
                    owner, contract, None, ProviderLanes()
                ).field_dispositions,
                tuple(_mapping(row["payload"]) for row in rows),
                complete=operation["query_complete"] is True,
            )
        ]
        if operation["field_dispositions"] != expected_fields:
            raise ValueError("market report field authority is invalid")
        material = None
        complete_fields: Sequence[str] = ()
        if rows:
            row = rows[0]
            payload = _mapping(row["payload"])
            material = _mapping(payload["observation"])
            scope = _mapping(contract["scope"])
            report_date = _date(material["report_date"])
            publication = _timestamp(material["published_at"])
            retrieval = _timestamp(payload["retrieved_at"])
            if (
                payload["source_operation"] != owner
                or payload["source_locator"]
                != _mapping(contract["request_selector"])["resource_url"]
                or material["exchange"] != scope["exchange"]
                or material["asset_scope"] != scope["kind"]
                or report_date > _date(_mapping(result["research_anchor"])["as_of"])
                or publication.date() < report_date
                or publication > retrieval
            ):
                raise ValueError("market report scope or time is invalid")
            metrics, units = _mapping(material["metrics"]), _mapping(material["units"])
            allowed = {
                str(value).rsplit(".", 1)[1]
                for value in _mapping(contract["field_destinations"]).values()
            }
            complete_fields = cast(Sequence[str], payload.get("complete_fields", ()))
            if (
                set(metrics) - allowed
                or set(units) != set(metrics)
                or not isinstance(complete_fields, list)
                or len(complete_fields) != len(set(complete_fields))
                or not set(complete_fields) <= set(metrics)
            ):
                raise ValueError("market report metric inventory is invalid")
            for field, value in metrics.items():
                _decimal(value)
                if units[field] != ("shares" if field.endswith("_shares") else "CNY"):
                    raise ValueError("market report unit is invalid")
            row_count = material["row_count"]
            expected_rows = 2 if owner == "sse_a_share_turnover@1" else 1
            if type(row_count) is not int or not 0 <= row_count <= expected_rows:
                raise ValueError("market report row count is invalid")
            if operation["state"] == "complete":
                empty_margin = capability == "whole_market_margin" and row_count == 0
                required = set() if empty_margin else allowed
                if (
                    set(metrics) != required
                    or set(complete_fields) != required
                    or (not empty_margin and row_count != expected_rows)
                ):
                    raise ValueError("complete market report omits required fields")
            references[owner] = str(row["id"])
            report_dates.add(str(material["report_date"]))
        partitions.append(
            {
                "operation_id": owner,
                "source_result": operation["state"],
                "observation": material,
                "complete_fields": complete_fields,
            }
        )
    if len(report_dates) > 1:
        raise ValueError("market report partitions have incompatible dates")
    by_owner = {str(partition["operation_id"]): partition for partition in partitions}
    partitions = [by_owner[owner] for owner in OWNER_IDS if owner in contracts]
    totals = (
        {"turnover_amount_CNY": _total(partitions, "turnover_amount_CNY")}
        if capability == "whole_market_turnover"
        else _margin_totals(partitions)
    )
    expected_calculations = []
    for field, total in totals.items():
        if total is None:
            continue
        typed_total = _mapping(total)
        evidence_ids = [
            references[str(owner)]
            for owner in cast(Sequence[str], typed_total["operands"])
        ]
        expected_calculations.append(
            {
                "kind": capability,
                "evidence_use": capability,
                "input_ids": evidence_ids,
                "payload": {
                    "report_date": next(iter(report_dates)),
                    "metric": field,
                    "value": typed_total["value"],
                    "unit": typed_total["unit"],
                    "operand_evidence_ids": evidence_ids,
                },
            }
        )
    calculations = [
        {key: value for key, value in calculation.items() if key != "id"}
        for calculation in cast(Sequence[Mapping[str, object]], result["calculations"])
    ]
    if calculations != expected_calculations:
        raise ValueError("market report total contradicts its three-exchange operands")
