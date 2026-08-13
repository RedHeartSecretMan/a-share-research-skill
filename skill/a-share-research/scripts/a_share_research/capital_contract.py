"""Versioned internal seam for capital-flow and corporate-event sources."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Protocol

from .content_contract import ContentHttpTransport

CAPITAL_DATA_TYPES = frozenset(
    {
        "northbound_flow",
        "stock_fund_flow",
        "board_fund_flow",
        "dragon_tiger",
        "market_dragon_tiger",
        "lockup",
        "margin_trading",
        "block_trade",
        "shareholder_count",
        "dividend",
    }
)

CAPITAL_SOURCE_ROLES = frozenset(
    {
        "authoritative_disclosure",
        "market_observation",
        "market_signal",
    }
)


@dataclass(frozen=True)
class CapitalPeriodAuthority:
    """One allowed period shape for an owner-local observation type."""

    frequency: str
    kind: str | None = None
    start_relation: Literal["observed_on", "range", "absent"] = "observed_on"
    marker: tuple[str, str] | None = None

    def matches(self, period: Mapping[str, object], observed_on: str) -> bool:
        expected_keys = {"start", "end", "frequency"}
        if self.kind is not None:
            expected_keys.add("kind")
        if self.marker is not None:
            expected_keys.add(self.marker[0])
        if set(period) != expected_keys or period.get("frequency") != self.frequency:
            return False
        if self.kind is not None and period.get("kind") != self.kind:
            return False
        if self.marker is not None and period.get(self.marker[0]) != self.marker[1]:
            return False
        start = period.get("start")
        end = period.get("end")
        if self.start_relation == "observed_on":
            return start == observed_on and end == observed_on
        if self.start_relation == "absent":
            return start is None and end == observed_on
        return (
            isinstance(start, str)
            and isinstance(end, str)
            and start <= observed_on <= end
        )


@dataclass(frozen=True)
class CapitalObservationVariant:
    """One inseparable metric and period variant for an observation type."""

    metrics: Mapping[str, tuple[str, str]]
    period: CapitalPeriodAuthority

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


@dataclass(frozen=True)
class CapitalObservationGroupAuthority:
    """A cross-record relation required by one owner-local observation type."""

    marker_key: str
    relation: Literal["exact_observed_range"]


@dataclass(frozen=True)
class CapitalObservationAuthority:
    """Owner-local semantic authority shared by its Adapter and serializer."""

    operation_id: str
    data_type: str
    source_role: str
    subject_scope: Literal["security", "market"]
    variants: tuple[CapitalObservationVariant, ...]
    market_scope: str | None = None
    group_authority: CapitalObservationGroupAuthority | None = None

    def __post_init__(self) -> None:
        if not self.variants:
            raise ValueError("capital observation authority requires a variant")

    def metric_semantics(
        self,
        metric_names: Collection[str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        names = set(metric_names)
        variants = [item for item in self.variants if set(item.metrics) == names]
        if not variants:
            raise ValueError("capital metric names contradict owner authority")
        metrics = variants[0].metrics
        if any(item.metrics != metrics for item in variants[1:]):
            raise AssertionError("capital metric variant semantics are ambiguous")
        return (
            {name: metrics[name][0] for name in metric_names},
            {name: metrics[name][1] for name in metric_names},
        )

    def validate_payload(self, payload: Mapping[str, object]) -> None:
        if payload.get("source_role") != self.source_role:
            raise ValueError("capital source role contradicts owner authority")
        subject = payload.get("subject")
        dimensions = payload.get("dimensions")
        if not isinstance(dimensions, Mapping):
            raise ValueError("capital dimensions contradict owner authority")
        if self.subject_scope == "security" and not isinstance(subject, Mapping):
            raise ValueError("capital security scope contradicts owner authority")
        if self.subject_scope == "security" and "market_scope" in dimensions:
            raise ValueError("capital security scope contradicts owner authority")
        if self.subject_scope == "market" and subject is not None:
            raise ValueError("capital market scope contradicts owner authority")
        if self.market_scope is not None and (
            dimensions.get("market_scope") != self.market_scope
        ):
            raise ValueError("capital market scope contradicts owner authority")
        metrics = payload.get("metrics")
        units = payload.get("units")
        directions = payload.get("directions")
        period = payload.get("period")
        if (
            not isinstance(metrics, Mapping)
            or not isinstance(units, Mapping)
            or not isinstance(directions, Mapping)
            or not isinstance(period, Mapping)
        ):
            raise ValueError("capital metric or period contradicts owner authority")
        observed_on = payload.get("observed_on")
        if not isinstance(observed_on, str):
            raise ValueError("capital period contradicts owner authority")
        metric_variants = [
            item for item in self.variants if set(item.metrics) == set(metrics)
        ]
        if not metric_variants:
            raise ValueError("capital metric names contradict owner authority")
        matching_variants = [
            item for item in metric_variants if item.period.matches(period, observed_on)
        ]
        if len(matching_variants) != 1:
            raise ValueError("capital period contradicts owner authority")
        variant_metrics = matching_variants[0].metrics
        expected_units = {name: variant_metrics[name][0] for name in metrics}
        expected_directions = {name: variant_metrics[name][1] for name in metrics}
        if units != expected_units:
            raise ValueError("capital unit contradicts owner authority")
        if directions != expected_directions:
            raise ValueError("capital direction contradicts owner authority")


def _uniform_metrics(
    names: tuple[str, ...], unit: str, direction: str
) -> Mapping[str, tuple[str, str]]:
    return MappingProxyType({name: (unit, direction) for name in names})


def _observation_variants(
    metrics: Mapping[str, tuple[str, str]],
    *periods: CapitalPeriodAuthority,
) -> tuple[CapitalObservationVariant, ...]:
    return tuple(CapitalObservationVariant(metrics, period) for period in periods)


def _event_period(frequency: str, *, kind: str | None = None) -> CapitalPeriodAuthority:
    return CapitalPeriodAuthority(frequency=frequency, kind=kind)


_QUALIFIED_CAPITAL_AUTHORITIES = (
    CapitalObservationAuthority(
        "eastmoney_stock_fund_flow@1",
        "stock_fund_flow",
        "market_signal",
        "security",
        _observation_variants(
            _uniform_metrics(
                (
                    "main_net_inflow",
                    "small_order_net_inflow",
                    "medium_order_net_inflow",
                    "large_order_net_inflow",
                    "super_large_order_net_inflow",
                ),
                "CNY",
                "positive_is_net_inflow",
            ),
            CapitalPeriodAuthority(
                frequency="trading_day",
                start_relation="range",
                marker=("trading_days", "5"),
            ),
            CapitalPeriodAuthority(
                frequency="trading_day",
                start_relation="range",
                marker=("trading_days", "10"),
            ),
        ),
        group_authority=CapitalObservationGroupAuthority(
            marker_key="trading_days",
            relation="exact_observed_range",
        ),
    ),
    CapitalObservationAuthority(
        "eastmoney_board_fund_flow@1",
        "board_fund_flow",
        "market_signal",
        "market",
        (
            CapitalObservationVariant(
                {
                    "main_net_inflow": ("CNY", "positive_is_net_inflow"),
                    "main_net_inflow_ratio": (
                        "PERCENT",
                        "positive_is_net_inflow",
                    ),
                    "price_change_ratio": ("PERCENT", "positive_is_gain"),
                    "super_large_order_net_inflow": (
                        "CNY",
                        "positive_is_net_inflow",
                    ),
                    "large_order_net_inflow": ("CNY", "positive_is_net_inflow"),
                    "medium_order_net_inflow": (
                        "CNY",
                        "positive_is_net_inflow",
                    ),
                    "small_order_net_inflow": ("CNY", "positive_is_net_inflow"),
                },
                _event_period("trading_day_snapshot"),
            ),
            *_observation_variants(
                {
                    "main_net_inflow": ("CNY", "positive_is_net_inflow"),
                    "main_net_inflow_ratio": (
                        "PERCENT",
                        "positive_is_net_inflow",
                    ),
                    "price_change_ratio": ("PERCENT", "positive_is_gain"),
                },
                CapitalPeriodAuthority(
                    frequency="rolling_5_trading_days",
                    start_relation="absent",
                    marker=("lookback_trading_days", "5"),
                ),
                CapitalPeriodAuthority(
                    frequency="rolling_10_trading_days",
                    start_relation="absent",
                    marker=("lookback_trading_days", "10"),
                ),
            ),
        ),
        market_scope="a_share_board_market",
    ),
    CapitalObservationAuthority(
        "eastmoney_margin_trading@1",
        "margin_trading",
        "market_observation",
        "security",
        _observation_variants(
            {
                "financing_balance": ("CNY", "higher_is_more_financing_exposure"),
                "financing_buy_amount": ("CNY", "inflow"),
                "financing_repayment_amount": ("CNY", "outflow"),
                "securities_lending_balance": (
                    "CNY",
                    "higher_is_more_short_exposure",
                ),
                "securities_lending_sell_volume": ("share", "short_opening"),
                "securities_lending_repayment_volume": ("share", "short_covering"),
                "margin_balance": ("CNY", "higher_is_more_leverage_exposure"),
            },
            _event_period("daily", kind="trading_day"),
        ),
    ),
    CapitalObservationAuthority(
        "eastmoney_shareholder_count@1",
        "shareholder_count",
        "market_observation",
        "security",
        _observation_variants(
            {
                "shareholder_count": ("account", "higher_is_more_distributed"),
                "shareholder_count_change": ("account", "positive_is_increase"),
                "shareholder_count_change_ratio": (
                    "percent",
                    "positive_is_increase",
                ),
                "average_shares_per_holder": (
                    "share_per_holder",
                    "higher_is_more_concentrated",
                ),
            },
            _event_period("quarterly", kind="reporting_period_end"),
        ),
    ),
    CapitalObservationAuthority(
        "eastmoney_dividend@1",
        "dividend",
        "market_observation",
        "security",
        _observation_variants(
            {
                "cash_dividend_per_10_shares_before_tax": (
                    "CNY_per_10_shares",
                    "distribution_to_holder",
                ),
                "bonus_shares_per_10_shares": (
                    "share_per_10_shares",
                    "distribution_to_holder",
                ),
                "transfer_shares_per_10_shares": (
                    "share_per_10_shares",
                    "capitalization_to_holder",
                ),
            },
            _event_period("event", kind="distribution_event"),
        ),
    ),
    CapitalObservationAuthority(
        "eastmoney_trading_events@1",
        "dragon_tiger",
        "market_signal",
        "security",
        _observation_variants(
            {
                "net_buy_amount": ("CNY", "positive_is_net_buy"),
                "turnover_rate": ("percent", "not_directional"),
                "institution_buy_amount": ("CNY", "positive_is_buy"),
                "institution_sell_amount": ("CNY", "positive_is_sell"),
                "institution_net_amount": ("CNY", "positive_is_net_buy"),
            },
            _event_period("event"),
        ),
    ),
    CapitalObservationAuthority(
        "eastmoney_trading_events@1",
        "market_dragon_tiger",
        "market_signal",
        "market",
        _observation_variants(
            {
                "close_price": ("CNY/share", "not_directional"),
                "change_rate": ("percent", "positive_is_price_increase"),
                "net_buy_amount": ("CNY", "positive_is_net_buy"),
                "buy_amount": ("CNY", "positive_is_buy"),
                "sell_amount": ("CNY", "positive_is_sell"),
                "turnover_rate": ("percent", "not_directional"),
            },
            _event_period("daily"),
        ),
        market_scope="eastmoney_all_market_billboard",
    ),
    CapitalObservationAuthority(
        "eastmoney_trading_events@1",
        "block_trade",
        "market_observation",
        "security",
        _observation_variants(
            {
                "deal_price": ("CNY/share", "not_directional"),
                "close_price": ("CNY/share", "not_directional"),
                "deal_volume": ("share", "positive_is_more_volume"),
                "deal_amount": ("CNY", "positive_is_more_value"),
                "premium_rate": (
                    "percent",
                    "positive_is_premium_negative_is_discount",
                ),
            },
            _event_period("event"),
        ),
    ),
    CapitalObservationAuthority(
        "eastmoney_trading_events@1",
        "lockup",
        "market_observation",
        "security",
        _observation_variants(
            {
                "released_shares": (
                    "10k_shares",
                    "positive_is_more_shares_released",
                ),
                "tradable_shares": (
                    "10k_shares",
                    "positive_is_more_tradable_shares",
                ),
                "total_share_ratio": (
                    "ratio",
                    "positive_is_larger_share_base_fraction",
                ),
            },
            _event_period("event"),
        ),
    ),
)

CAPITAL_OBSERVATION_AUTHORITIES: Mapping[
    tuple[str, str], CapitalObservationAuthority
] = MappingProxyType(
    {
        (item.operation_id, item.data_type): item
        for item in _QUALIFIED_CAPITAL_AUTHORITIES
    }
)


def capital_observation_authority(
    operation_id: str,
    data_type: str,
) -> CapitalObservationAuthority | None:
    return CAPITAL_OBSERVATION_AUTHORITIES.get((operation_id, data_type))


def validate_capital_observation_authority(
    operation_id: str,
    data_type: str,
    payload: Mapping[str, object],
) -> None:
    """Validate qualified owners while leaving injected test owners extensible."""

    authority = capital_observation_authority(operation_id, data_type)
    if authority is not None:
        authority.validate_payload(payload)
        return
    if any(owner == operation_id for owner, _ in CAPITAL_OBSERVATION_AUTHORITIES):
        raise ValueError("capital data type contradicts owner authority")


def validate_capital_observation_group_authority(
    operation_id: str,
    data_type: str,
    payloads: Collection[Mapping[str, object]],
) -> None:
    """Validate an optional relation across one owner's retained observation group."""

    authority = capital_observation_authority(operation_id, data_type)
    if authority is None or authority.group_authority is None or not payloads:
        return
    relation = authority.group_authority
    if relation.relation != "exact_observed_range":
        raise AssertionError("capital observation group relation is unsupported")
    periods = [item.get("period") for item in payloads]
    if any(not isinstance(item, Mapping) for item in periods):
        raise ValueError("capital observation group period contradicts owner authority")
    first_period = periods[0]
    assert isinstance(first_period, Mapping)
    if any(item != first_period for item in periods[1:]):
        raise ValueError("capital observation group period contradicts owner authority")
    observed_ons: list[str] = []
    for payload in payloads:
        observed_on = payload.get("observed_on")
        if not isinstance(observed_on, str):
            raise ValueError(
                "capital observation group period contradicts owner authority"
            )
        observed_ons.append(observed_on)
    unique_observed_ons = sorted(set(observed_ons))
    marker = first_period.get(relation.marker_key)
    if (
        not isinstance(marker, str)
        or not marker.isdigit()
        or marker.startswith("0")
        or int(marker) != len(unique_observed_ons)
        or first_period.get("start") != unique_observed_ons[0]
        or first_period.get("end") != unique_observed_ons[-1]
    ):
        raise ValueError("capital observation group period contradicts owner authority")


@dataclass(frozen=True)
class CapitalQuery:
    """Normalized request passed to one capital-event source operation."""

    data_types: tuple[str, ...]
    as_of: str
    observed_from: str
    observed_to: str
    limit: int
    subject: dict[str, Any] | None
    parameters: dict[str, Any]
    allow_fallback: bool = True


@dataclass(frozen=True)
class CapitalObservation:
    """One source-indexed observation with explicit units and direction semantics."""

    data_type: str
    source_operation: str
    source_role: str
    subject: dict[str, Any] | None
    observed_on: str
    available_at: str | None
    retrieved_at: datetime
    period: dict[str, str | None]
    metrics: dict[str, str | None]
    units: dict[str, str]
    directions: dict[str, str]
    dimensions: dict[str, Any]
    locator_uri: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapitalSourceFailure:
    """Fail-closed, non-sensitive diagnosis returned by a source operation."""

    source_operation: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        return {
            "source_operation": self.source_operation,
            "code": self.code,
            "message": self.message,
            **self.details,
        }


@dataclass(frozen=True)
class CapitalModeAcquisition:
    """One trading-event mode's fixed source-acquisition material."""

    data_type: str
    operation_id: str
    observations: tuple[CapitalObservation, ...] = ()
    source_errors: tuple[CapitalSourceFailure, ...] = ()
    degradations: tuple[CapitalSourceFailure, ...] = ()
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "source_errors", tuple(self.source_errors))
        object.__setattr__(self, "degradations", tuple(self.degradations))
        if self.data_type not in CAPITAL_DATA_TYPES:
            raise ValueError("capital acquisition data type is unknown")
        if any(
            item.data_type != self.data_type
            or item.source_operation != self.operation_id
            for item in self.observations
        ):
            raise ValueError("capital acquisition observation owner is inconsistent")
        if any(
            item.source_operation != self.operation_id
            for item in (*self.source_errors, *self.degradations)
        ):
            raise ValueError("capital acquisition error owner is inconsistent")

    @property
    def state(self) -> str:
        """Derive one mode's four-state acquisition result."""

        if self.complete:
            return "observed_nonempty" if self.observations else "observed_empty"
        return "partial" if self.observations else "indeterminate"


@dataclass(frozen=True)
class CapitalSourceBatch:
    """One operation's bounded observations and explicit acquisition state."""

    operation_id: str
    observations: tuple[CapitalObservation, ...] = ()
    source_errors: tuple[CapitalSourceFailure, ...] = ()
    degradations: tuple[CapitalSourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()
    complete: bool = True
    mode_acquisitions: Mapping[str, CapitalModeAcquisition] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "source_errors", tuple(self.source_errors))
        object.__setattr__(self, "degradations", tuple(self.degradations))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        acquisitions = MappingProxyType(dict(self.mode_acquisitions))
        object.__setattr__(self, "mode_acquisitions", acquisitions)
        if not acquisitions:
            return
        if any(
            key != item.data_type or item.operation_id != self.operation_id
            for key, item in acquisitions.items()
        ):
            raise ValueError("capital mode acquisition owner is inconsistent")
        expected_observations = tuple(
            observation
            for acquisition in acquisitions.values()
            for observation in acquisition.observations
        )
        expected_errors = tuple(
            error
            for acquisition in acquisitions.values()
            for error in acquisition.source_errors
        )
        expected_degradations = tuple(
            degradation
            for acquisition in acquisitions.values()
            for degradation in acquisition.degradations
        )
        if (
            self.observations != expected_observations
            or self.source_errors != expected_errors
            or self.degradations != expected_degradations
            or self.complete != all(item.complete for item in acquisitions.values())
        ):
            raise ValueError("capital batch disagrees with mode acquisition material")

    @classmethod
    def from_mode_acquisitions(
        cls,
        operation_id: str,
        acquisitions: Mapping[str, CapitalModeAcquisition],
        *,
        limitations: tuple[str, ...] = (),
    ) -> CapitalSourceBatch:
        """Build aggregate batch fields from the typed source material."""

        fixed = tuple(acquisitions.values())
        return cls(
            operation_id=operation_id,
            observations=tuple(
                observation
                for acquisition in fixed
                for observation in acquisition.observations
            ),
            source_errors=tuple(
                error for acquisition in fixed for error in acquisition.source_errors
            ),
            degradations=tuple(
                degradation
                for acquisition in fixed
                for degradation in acquisition.degradations
            ),
            limitations=limitations,
            complete=all(item.complete for item in fixed),
            mode_acquisitions=acquisitions,
        )


class CapitalSourceOperation(Protocol):
    """Adapter operation seam hidden behind the capital-events module."""

    operation_id: str
    supported_data_types: frozenset[str]
    raw_fields: tuple[str, ...]

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch: ...


CapitalHttpTransport = ContentHttpTransport
