"""SSE ETF identity and cross-checked market quote research."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import urlencode

from .close_sources import DailyBarObservation, TencentDailyLineOperation
from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    TransportError,
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
)


@dataclass(frozen=True)
class EtfIdentityObservation:
    code: str
    name: str
    fund_manager: str
    listing_date: date
    source_uri: str
    retrieved_at: datetime

    @property
    def security(self) -> str:
        return f"SSE:{self.code}"

    @property
    def evidence_id(self) -> str:
        return f"etf-identity-sse_etf_list@1-{self.security}"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "authoritative_disclosure",
            "source_operation": "sse_etf_list@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": {
                    "name": self.name,
                    "fund_manager": self.fund_manager,
                    "listing_date": self.listing_date.isoformat(),
                },
                "unit": "ETF identity",
            },
            "basis": "current_sse_etf_list_membership",
            "evidence_time": self.listing_date.isoformat(),
            "available_at": None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"SSE ETF identity for {self.code}",
            },
            "limitations": [
                "experimental_source_operation",
                "availability_time_unknown",
            ],
        }


@dataclass(frozen=True)
class EtfSnapshotObservation:
    code: str
    name: str
    open_value: str
    high_value: str
    low_value: str
    last_value: str
    previous_close: str
    change_rate: str
    volume_shares: str
    amount_cny: str
    trade_phase: str
    observed_at: datetime
    source_uri: str
    retrieved_at: datetime

    @property
    def security(self) -> str:
        return f"SSE:{self.code}"

    @property
    def evidence_id(self) -> str:
        return (
            f"etf-sse_etf_snapshot@1-{self.security}-"
            f"{self.observed_at.date().isoformat()}"
        )

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "market_observation",
            "source_operation": "sse_etf_snapshot@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": {
                    "open": self.open_value,
                    "high": self.high_value,
                    "low": self.low_value,
                    "last": self.last_value,
                    "previous_close": self.previous_close,
                    "change_rate": self.change_rate,
                    "volume": self.volume_shares,
                    "amount": self.amount_cny,
                },
                "unit": {
                    "price": "CNY/share",
                    "change_rate": "percent",
                    "volume": "shares",
                    "amount": "CNY",
                },
            },
            "basis": "sse_etf_market_snapshot",
            "observation": {
                "kind": "ETF market quote",
                "trading_date": self.observed_at.date().isoformat(),
                "trade_phase": self.trade_phase,
            },
            "evidence_time": self.observed_at.isoformat(),
            "available_at": self.observed_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"SSE ETF snapshot for {self.code}",
            },
            "limitations": ["experimental_source_operation"],
        }


class SseEtfListOperation:
    operation_id = "sse_etf_list@1"
    endpoint = "https://query.sse.com.cn/commonSoaQuery.do"
    subclasses = "01,02,03,04,06,08,09,31,32,33,34,35,36,37,38"
    raw_fields = (
        "fundCode",
        "fundAbbr",
        "secNameFull",
        "companyName",
        "listingDate",
        "fundType",
        "subClass",
    )

    def observe(self, code: str, transport: HttpTransport) -> EtfIdentityObservation:
        url = f"{self.endpoint}?{
            urlencode(
                {
                    'isPagination': 'true',
                    'pageHelp.pageSize': '25',
                    'pageHelp.pageNo': '1',
                    'pageHelp.beginPage': '1',
                    'pageHelp.cacheSize': '1',
                    'pageHelp.endPage': '1',
                    'pagecache': 'false',
                    'sqlId': 'FUND_LIST',
                    'fundType': '00',
                    'subClass': self.subclasses,
                    'fundCode': code,
                }
            )
        }"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://www.sse.com.cn/assortment/fund/etf/list/",
        )
        payload = _json_payload(self.operation_id, response)
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, list):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF list response does not match the expected schema.",
            )
        matches = [
            item
            for item in result
            if isinstance(item, dict) and item.get("fundCode") == code
        ]
        if len(matches) != 1:
            raise _error(
                self.operation_id,
                "etf_identity_not_resolved",
                "The SSE ETF list did not establish exactly one matching ETF.",
            )
        item = matches[0]
        if not all(
            isinstance(item.get(field), str) and item[field]
            for field in (
                "secNameFull",
                "companyName",
                "listingDate",
                "fundType",
                "subClass",
            )
        ):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF identity fields are incomplete.",
            )
        if item["fundType"] != "00" or item["subClass"] not in self.subclasses.split(
            ","
        ):
            raise _error(
                self.operation_id,
                "security_type_mismatch",
                "The matching SSE fund is not an ETF.",
            )
        try:
            listing_date = datetime.strptime(item["listingDate"], "%Y%m%d").date()
        except ValueError as error:
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF listing date is invalid.",
            ) from error
        return EtfIdentityObservation(
            code=code,
            name=item["secNameFull"],
            fund_manager=item["companyName"],
            listing_date=listing_date,
            source_uri=url,
            retrieved_at=response.retrieved_at,
        )


class SseEtfSnapshotOperation:
    operation_id = "sse_etf_snapshot@1"
    endpoint = "https://yunhq.sse.com.cn:32042/v1/sh1/snap"
    select = (
        "name,cpxxextendname,open,high,low,last,prev_close,chg_rate,"
        "volume,amount,tradephase"
    )
    raw_fields = (*tuple(select.split(",")), "code", "date", "time")

    def observe(self, code: str, transport: HttpTransport) -> EtfSnapshotObservation:
        url = f"{self.endpoint}/{code}?{urlencode({'select': self.select})}"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://www.sse.com.cn/assortment/fund/etf/price/",
        )
        payload = _json_payload(self.operation_id, response)
        if (
            not isinstance(payload, dict)
            or payload.get("code") != code
            or not isinstance(payload.get("snap"), list)
            or len(payload["snap"]) != 11
        ):
            error_code = (
                "wrong_security_payload"
                if isinstance(payload, dict)
                and isinstance(payload.get("code"), str)
                and payload.get("code") != code
                else "unknown_schema"
            )
            raise _error(
                self.operation_id,
                error_code,
                "The SSE ETF snapshot has an invalid security or schema.",
            )
        values = payload["snap"]
        if not isinstance(values[0], str) or not isinstance(values[1], str):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF snapshot name fields are invalid.",
            )
        open_value, high_value, low_value, last_value, previous_close = (
            _positive_decimal(values[index], self.operation_id) for index in range(2, 7)
        )
        change_rate = _finite_decimal(values[7], self.operation_id)
        volume_shares = _nonnegative_integer(values[8], self.operation_id)
        amount_cny = _nonnegative_integer(values[9], self.operation_id)
        if not isinstance(values[10], str):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The SSE ETF trade phase is invalid.",
            )
        prices = map(Decimal, (open_value, high_value, low_value, last_value))
        open_price, high_price, low_price, last_price = prices
        if (
            low_price > high_price
            or not low_price <= open_price <= high_price
            or not low_price <= last_price <= high_price
        ):
            raise _error(
                self.operation_id,
                "inconsistent_price_bar",
                "The SSE ETF snapshot OHLC values are inconsistent.",
            )
        observed_at = _snapshot_time(payload, self.operation_id)
        return EtfSnapshotObservation(
            code=code,
            name=values[1],
            open_value=open_value,
            high_value=high_value,
            low_value=low_value,
            last_value=last_value,
            previous_close=previous_close,
            change_rate=change_rate,
            volume_shares=volume_shares,
            amount_cny=amount_cny,
            trade_phase=values[10].strip(),
            observed_at=observed_at,
            source_uri=url,
            retrieved_at=response.retrieved_at,
        )


_ETF_OPERATION_CONTRACTS: dict[str, tuple[str, str, tuple[FieldDisposition, ...]]] = {
    "sse_etf_list@1": (
        "authoritative_disclosure",
        "supporting",
        (
            FieldDisposition(
                "fundCode", "normalized", "observations.payload.subject.security"
            ),
            FieldDisposition(
                "fundAbbr",
                "excluded",
                None,
                "The returned abbreviation is not used or retained.",
            ),
            FieldDisposition(
                "secNameFull",
                "normalized",
                "observations.payload.observed_value.value.name",
            ),
            FieldDisposition(
                "companyName",
                "normalized",
                "observations.payload.observed_value.value.fund_manager",
            ),
            FieldDisposition(
                "listingDate",
                "normalized",
                "observations.payload.observed_value.value.listing_date",
            ),
            FieldDisposition(
                "fundType",
                "excluded",
                None,
                "The ETF type code is used for qualification but is not retained.",
            ),
            FieldDisposition(
                "subClass",
                "excluded",
                None,
                "The ETF subclass is used for qualification but is not retained.",
            ),
        ),
    ),
    "sse_etf_snapshot@1": (
        "market_observation",
        "supporting",
        (
            FieldDisposition(
                "name",
                "excluded",
                None,
                "The short name is schema-validated but not retained.",
            ),
            FieldDisposition(
                "cpxxextendname",
                "excluded",
                None,
                "The extended name is used for identity comparison but is not retained in the observation.",
            ),
            *(
                FieldDisposition(
                    field,
                    "normalized",
                    f"observations.payload.observed_value.value.{destination}",
                )
                for field, destination in (
                    ("open", "open"),
                    ("high", "high"),
                    ("low", "low"),
                    ("last", "last"),
                    ("prev_close", "previous_close"),
                    ("chg_rate", "change_rate"),
                    ("volume", "volume"),
                    ("amount", "amount"),
                )
            ),
            FieldDisposition(
                "tradephase",
                "normalized",
                "observations.payload.observation.trade_phase",
            ),
            FieldDisposition(
                "code", "cross_check_only", "observations.payload.subject.security"
            ),
            FieldDisposition(
                "date",
                "normalized",
                "observations.payload.observation.trading_date",
            ),
            FieldDisposition(
                "time", "lineage_only", "observations.payload.evidence_time"
            ),
        ),
    ),
    "tencent_daily_line@1": (
        "market_observation",
        "corroborating",
        TencentDailyLineOperation.field_dispositions,
    ),
}


def _etf_outcome(
    operation_id: str,
    *,
    observations: tuple[dict[str, Any], ...] = (),
    error: SourceOperationError | None = None,
    skipped: str | None = None,
) -> OperationOutcome:
    if (bool(observations) + (error is not None) + (skipped is not None)) != 1:
        raise ValueError("ETF outcome requires exactly one execution fact")
    if error is not None:
        execution = OperationExecution("failed")
        errors: tuple[dict[str, str], ...] = (_source_error(error),)
    elif skipped is not None:
        execution = OperationExecution(cast(Any, skipped))
        errors = ()
    else:
        execution = OperationExecution("succeeded", query_complete=True)
        errors = ()
    return OperationOutcome(
        capability_id="etf_market_quote",
        operation_id=operation_id,
        execution=execution,
        observations=observations,
        source_errors=errors,
        field_dispositions=_ETF_OPERATION_CONTRACTS[operation_id][2],
    )


def _etf_skipped_outcomes(state: str) -> tuple[OperationOutcome, ...]:
    return tuple(
        _etf_outcome(operation_id, skipped=state)
        for operation_id in _ETF_OPERATION_CONTRACTS
    )


def build_etf_market_result(
    request: dict[str, Any], transport: HttpTransport, now: datetime | None = None
) -> UnifiedResearchResult:
    research_now = now or datetime.now(CHINA_STANDARD_TIME)
    research_date = date.fromisoformat(request["as_of"])
    subjects = request["subjects"]
    if len(subjects) != 1 or not isinstance(subjects[0], dict):
        raise ValueError("etf_market requires exactly one subject object")
    clue = subjects[0].get("clue")
    if (
        not isinstance(clue, str)
        or len(clue) != 6
        or not clue.isascii()
        or not clue.isdigit()
    ):
        raise ValueError("etf_market subject requires one six-digit ETF code clue")
    if not request["source_policy"]["allow_experimental"]:
        return _blocked(
            request,
            "source_policy_not_satisfied",
            "etf_market currently requires experimental source operations",
            outcomes=_etf_skipped_outcomes("skipped_policy"),
        )
    if research_date > research_now.date():
        return _blocked(
            request,
            "future_research_date",
            "The research date is in the future.",
            outcomes=_etf_skipped_outcomes("skipped_research_dependency"),
        )
    outcomes: list[OperationOutcome] = []
    try:
        identity = SseEtfListOperation().observe(clue, transport)
    except SourceOperationError as error:
        return _blocked(
            request,
            "etf_identity_not_resolved",
            "The ETF identity could not be established.",
            outcomes=(
                _etf_outcome("sse_etf_list@1", error=error),
                _etf_outcome(
                    "sse_etf_snapshot@1", skipped="skipped_research_dependency"
                ),
                _etf_outcome(
                    "tencent_daily_line@1", skipped="skipped_research_dependency"
                ),
            ),
        )
    outcomes.append(
        _etf_outcome("sse_etf_list@1", observations=(identity.to_evidence(),))
    )
    subject = {
        "security": {"exchange": "SSE", "code": clue, "type": "ETF"},
        "name": identity.name,
        "fund_manager": identity.fund_manager,
        "listing_date": identity.listing_date.isoformat(),
    }
    try:
        snapshot = SseEtfSnapshotOperation().observe(clue, transport)
    except SourceOperationError as error:
        snapshot = None
        outcomes.append(_etf_outcome("sse_etf_snapshot@1", error=error))
    else:
        assert snapshot is not None
        outcomes.append(
            _etf_outcome("sse_etf_snapshot@1", observations=(snapshot.to_evidence(),))
        )
    try:
        fallback_observations = TencentDailyLineOperation().observe(
            f"SSE:{clue}", research_date, transport
        )
    except SourceOperationError as error:
        fallback_observations = []
        outcomes.append(_etf_outcome("tencent_daily_line@1", error=error))
    else:
        outcomes.append(
            _etf_outcome(
                "tencent_daily_line@1",
                observations=tuple(
                    item.to_bar_evidence() for item in fallback_observations
                ),
            )
        )
    if snapshot is None or snapshot.observed_at.date() > research_date:
        return _blocked(
            request,
            "etf_quote_not_cross_checked",
            "The ETF quote is unavailable or outside the research boundary.",
            subject=subject,
            outcomes=tuple(outcomes),
        )
    fallback = max(
        (
            item
            for item in fallback_observations
            if item.trading_date == snapshot.observed_at.date()
        ),
        key=lambda item: item.evidence_time,
        default=None,
    )
    conflicts = _quote_conflicts(snapshot, fallback)
    if any(item.source_errors for item in outcomes) or fallback is None or conflicts:
        return _direct_etf_result(
            request,
            outcomes=tuple(outcomes),
            conflicts=conflicts,
            limitations=[
                {
                    "code": "etf_quote_not_cross_checked",
                    "message": "The ETF quote does not agree across both market sources.",
                }
            ],
        )
    assert fallback is not None
    volume_difference = abs(
        Decimal(snapshot.volume_shares) - Decimal(fallback.volume_shares)
    )
    market_state = (
        "completed"
        if snapshot.observed_at.date() < research_now.date()
        or snapshot.observed_at.time() >= time(15, 0)
        else "intraday"
    )
    quote: dict[str, Any] = {
        "trading_date": snapshot.observed_at.date().isoformat(),
        "observed_at": snapshot.observed_at.isoformat(),
        "market_state": market_state,
        "open": snapshot.open_value,
        "high": snapshot.high_value,
        "low": snapshot.low_value,
        "last": snapshot.last_value,
        "previous_close": snapshot.previous_close,
        "change_rate": {"value": snapshot.change_rate, "unit": "percent"},
        "volume": {"value": snapshot.volume_shares, "unit": "shares"},
        "amount": {"value": snapshot.amount_cny, "unit": "CNY"},
        "volume_cross_check": {
            "status": "consistent_with_lot_rounding",
            "difference_shares": format(volume_difference, "f"),
        },
        "evidence_ids": [snapshot.evidence_id, fallback.bar_evidence_id],
    }
    quote_keys = (snapshot.evidence_id, fallback.bar_evidence_id)
    return _direct_etf_result(
        request,
        outcomes=tuple(outcomes),
        claims=[
            DirectTaskClaim(
                kind="etf_market_quote",
                evidence_use="etf_market",
                observation_keys=quote_keys,
                payload={
                    "subjects": [subject],
                    "research": {"retrieved_at": research_now.isoformat()},
                    "quote": quote,
                },
            )
        ],
        calculations=[
            DirectTaskCalculation(
                kind="etf_volume_cross_check",
                evidence_use="etf_market",
                observation_keys=quote_keys,
                payload=cast(dict[str, object], quote["volume_cross_check"]),
            )
        ],
        limitations=[
            {
                "code": "experimental_etf_market_sources",
                "message": (
                    "ETF identity and prices use experimental source operations; "
                    "fallback volume is rounded to board lots."
                ),
            }
        ],
    )


def _quote_conflicts(
    snapshot: EtfSnapshotObservation, fallback: DailyBarObservation | None
) -> list[dict[str, Any]]:
    if fallback is None:
        return []
    fields = {
        "open": (snapshot.open_value, fallback.open_value),
        "high": (snapshot.high_value, fallback.high_value),
        "low": (snapshot.low_value, fallback.low_value),
        "last": (snapshot.last_value, fallback.close_value),
    }
    differing = [
        field
        for field, values in fields.items()
        if Decimal(values[0]) != Decimal(values[1])
    ]
    volume_difference = abs(
        Decimal(snapshot.volume_shares) - Decimal(fallback.volume_shares)
    )
    if volume_difference > 100:
        differing.append("volume")
    if not differing:
        return []
    return [
        {
            "code": "etf_quote_value_conflict",
            "fields": differing,
            "evidence_ids": [snapshot.evidence_id, fallback.bar_evidence_id],
        }
    ]


def _request(
    operation: str,
    transport: HttpTransport,
    url: str,
    referer: str,
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json",
                "Referer": referer,
                "User-Agent": "a-share-research-skill/1",
            },
        )
    except TransportError as error:
        raise _error(operation, error.code, str(error)) from error
    if response.status != 200:
        raise _error(
            operation,
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _error(operation, "empty_response", "The source returned no data.")
    return response


def _json_payload(operation: str, response: HttpResponse) -> Any:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _error(
            operation,
            "unexpected_content_type",
            "The source response is not JSON.",
        )
    try:
        return json.loads(response.body, parse_float=str, parse_int=str)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(
            operation,
            "unknown_schema",
            "The source response is not valid JSON.",
        ) from error


def _snapshot_time(payload: dict[str, Any], operation: str) -> datetime:
    date_value = payload.get("date")
    time_value = payload.get("time")
    if not isinstance(date_value, str) or not isinstance(time_value, str):
        raise _error(operation, "unknown_schema", "The snapshot time is invalid.")
    try:
        return datetime.strptime(
            f"{date_value}{time_value.zfill(6)}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=CHINA_STANDARD_TIME)
    except ValueError as error:
        raise _error(
            operation, "unknown_schema", "The snapshot time is invalid."
        ) from error


def _finite_decimal(value: Any, operation: str) -> str:
    if not isinstance(value, str):
        raise _error(operation, "unknown_schema", "A snapshot decimal is missing.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _error(
            operation, "unknown_schema", "A snapshot decimal is invalid."
        ) from error
    if not parsed.is_finite():
        raise _error(operation, "unknown_schema", "A snapshot decimal is not finite.")
    return value


def _positive_decimal(value: Any, operation: str) -> str:
    text = _finite_decimal(value, operation)
    if Decimal(text) <= 0:
        raise _error(operation, "unknown_schema", "A snapshot price is not positive.")
    return text


def _nonnegative_integer(value: Any, operation: str) -> str:
    text = _finite_decimal(value, operation)
    parsed = Decimal(text)
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise _error(operation, "unknown_schema", "A snapshot quantity is invalid.")
    return format(parsed, "f")


def _source_error(error: SourceOperationError) -> dict[str, str]:
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": str(error),
    }


def _error(operation: str, code: str, message: str) -> SourceOperationError:
    return SourceOperationError(operation, code, message)


def _blocked(
    request: dict[str, Any],
    code: str,
    message: str,
    *,
    subject: dict[str, Any] | None = None,
    outcomes: tuple[OperationOutcome, ...] = (),
) -> UnifiedResearchResult:
    return _direct_etf_result(
        request,
        outcomes=outcomes,
        limitations=[{"code": code, "message": message}],
        claims=(
            [
                DirectTaskClaim(
                    kind="etf_identity",
                    evidence_use="etf_market",
                    observation_keys=tuple(
                        str(observation["id"])
                        for outcome in outcomes
                        for observation in outcome.observations
                        if outcome.operation_id == "sse_etf_list@1"
                        and isinstance(observation.get("id"), str)
                    ),
                    payload={"subjects": [subject]},
                )
            ]
            if subject is not None
            else []
        ),
    )


def _direct_etf_result(
    request: dict[str, Any],
    *,
    outcomes: tuple[OperationOutcome, ...],
    claims: list[DirectTaskClaim] | None = None,
    calculations: list[DirectTaskCalculation] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    limitations: list[dict[str, Any]] | None = None,
) -> UnifiedResearchResult:
    outcomes_by_operation = {item.operation_id: item for item in outcomes}
    if len(outcomes_by_operation) != len(outcomes):
        raise ValueError("ETF outcomes must have unique operation owners")
    missing_state = (
        "skipped_policy"
        if not request["source_policy"]["allow_experimental"]
        else "skipped_research_dependency"
    )
    for operation_id in _ETF_OPERATION_CONTRACTS:
        if operation_id not in outcomes_by_operation:
            outcomes_by_operation[operation_id] = _etf_outcome(
                operation_id, skipped=missing_state
            )
    ordered_outcomes = tuple(
        outcomes_by_operation[operation_id] for operation_id in _ETF_OPERATION_CONTRACTS
    )
    obligations = tuple(
        OperationObligation(
            capability_id="etf_market_quote",
            operation_id=operation_id,
            source_role=source_role,
            experimental=True,
            exchange="SSE",
            evidence_uses=(EvidenceUsePolicy("etf_market", cast(Any, evidence_role)),),
        )
        for operation_id, (
            source_role,
            evidence_role,
            _dispositions,
        ) in _ETF_OPERATION_CONTRACTS.items()
    )
    known_keys = {
        str(observation["id"])
        for outcome in ordered_outcomes
        for observation in outcome.observations
        if isinstance(observation.get("id"), str)
    }
    direct_conflicts = tuple(
        DirectTaskConflict(
            code=str(item.get("code", "etf_market_conflict")),
            message=str(item.get("message", "ETF market observations conflict.")),
            observation_keys=tuple(
                key
                for key in item.get("evidence_ids", [])
                if isinstance(key, str) and key in known_keys
            ),
        )
        for item in conflicts or ()
    )
    direct_limitations = tuple(
        DirectTaskLimitation(
            code=str(item.get("code", "etf_market_limited")),
            message=str(item.get("message", "ETF market research is limited.")),
        )
        for item in limitations or ()
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="etf_market",
            as_of=str(request["as_of"]),
            obligations=obligations,
            dependencies=(
                ResearchDependency(
                    consumer_id="etf_market_quote",
                    evidence_use="etf_market",
                    minimum_supporting_observations=1,
                    core=True,
                ),
            ),
        ),
        ordered_outcomes,
        claims=tuple(claims or ()),
        calculations=tuple(calculations or ()),
        conflicts=direct_conflicts,
        limitations=direct_limitations,
        summary=(
            "A cross-checked ETF market quote was retained."
            if any(item.kind == "etf_market_quote" for item in claims or ())
            else "The requested ETF market quote could not be established safely."
        ),
    )
