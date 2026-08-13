"""Versioned internal seam for market-signal source operations."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from .content_contract import ContentHttpTransport

MARKET_SIGNAL_TYPES = frozenset(
    {
        "strong_stock_theme",
        "security_board_membership",
        "industry_rotation",
        "limit_state",
        "focus_monitoring",
        "severe_abnormal_movement",
        "monitoring_intersection",
        "market_heat",
    }
)

SIGNAL_COVERAGE_STATES = frozenset(
    {"observed_nonempty", "observed_empty", "partial", "indeterminate"}
)

ATTRIBUTION_PROVENANCE = frozenset(
    {"editorial_annotation", "market_signal", "model_inference"}
)

MARKET_SIGNAL_SOURCE_ROLES = frozenset({"market_signal"})


@dataclass(frozen=True)
class MarketSignalCoverageAuthority:
    """Project and revalidate one owner-local provider-pool coverage contract."""

    operation_id: str
    signal_type: str
    provider_total_mode: Literal["required", "optional", "absent"]
    page_semantics: Literal[
        "single_page", "complete_pagination", "paged_prefix", "selected_pools"
    ]
    bounded_details: tuple[tuple[str, frozenset[str]], ...] = ()
    pool_states: frozenset[str] = frozenset()
    provider_total_fields: frozenset[str] = frozenset()
    pages_expected_fields: frozenset[str] = frozenset()
    collected_count_fields: frozenset[str] = frozenset()
    exposes_collected_count: bool = False
    provider_page_size: int | None = None

    def field_destination(self, field_name: str) -> str | None:
        """Return the public coverage destination for a retained raw count."""

        prefix = (
            "coverage.market_signal_observations.operations."
            f"{self.operation_id}.diagnostics.signal_coverage.{self.signal_type}"
        )
        if field_name in self.provider_total_fields:
            return f"{prefix}.provider_total"
        if field_name in self.pages_expected_fields:
            return f"{prefix}.pages_expected"
        if field_name in self.collected_count_fields:
            return f"{prefix}.collected_count"
        return None

    def public_material(
        self,
        coverage: SignalCoverage,
        *,
        observation_count: int,
        retained_pool_states: Collection[object],
        source_error_codes: Collection[str],
        request_limit: int,
    ) -> dict[str, object]:
        error_codes = sorted(set(source_error_codes))
        provider_total = (
            coverage.provider_total if self.provider_total_mode != "absent" else None
        )
        provider_total_status = (
            "reported"
            if provider_total is not None
            else "unavailable"
            if coverage.state == "indeterminate"
            and error_codes
            and coverage.pages_collected is None
            and coverage.pages_expected is None
            else "not_exposed"
            if self.provider_total_mode == "absent"
            or (
                self.provider_total_mode == "optional"
                and coverage.state == "partial"
                and coverage.pages_collected is not None
                and coverage.pages_expected is None
            )
            else "unavailable"
        )
        collected_count = (
            coverage.details.get("collected_count")
            if self.exposes_collected_count
            else None
        )
        truncated = bool(
            (
                isinstance(provider_total, int)
                and self.page_semantics in {"single_page", "complete_pagination"}
                and provider_total > observation_count
            )
            or (
                isinstance(collected_count, int)
                and not isinstance(collected_count, bool)
                and collected_count > observation_count
            )
        )
        material: dict[str, object] = {
            "state": coverage.state,
            "provider_total": provider_total,
            "provider_total_status": provider_total_status,
            "pages_collected": coverage.pages_collected,
            "pages_expected": coverage.pages_expected,
            "retained_count": observation_count,
            "truncated": truncated,
            "source_error_codes": error_codes,
        }
        if self.exposes_collected_count and "collected_count" in coverage.details:
            material["collected_count"] = coverage.details["collected_count"]
        for key, _ in self.bounded_details:
            if key in coverage.details:
                material[key] = coverage.details[key]
        if self.page_semantics == "selected_pools":
            for key in (
                "pool_states",
                "completed_pool_states",
                "failed_pool_states",
                "pool_errors",
                "duplicate_count",
            ):
                if key in coverage.details:
                    material[key] = coverage.details[key]
        self.validate_public_material(
            material,
            observation_count=observation_count,
            retained_pool_states=retained_pool_states,
            source_error_codes=error_codes,
            request_limit=request_limit,
        )
        return material

    def validate_public_material(
        self,
        material: Mapping[str, object],
        *,
        observation_count: int,
        retained_pool_states: Collection[object],
        source_error_codes: Collection[str],
        request_limit: int | None,
    ) -> None:
        core_keys = {
            "state",
            "provider_total",
            "provider_total_status",
            "pages_collected",
            "pages_expected",
            "retained_count",
            "truncated",
            "source_error_codes",
        }
        state = material.get("state")
        errors = material.get("source_error_codes")
        expected_errors = sorted(set(source_error_codes))
        retained_count = material.get("retained_count")
        truncated = material.get("truncated")
        pages_collected = material.get("pages_collected")
        pages_expected = material.get("pages_expected")
        early_unavailable = (
            state == "indeterminate"
            and observation_count == 0
            and bool(errors)
            and pages_collected is None
            and pages_expected is None
        )
        expected_keys = set(core_keys)
        if not early_unavailable:
            expected_keys.update(key for key, _ in self.bounded_details)
            if self.exposes_collected_count:
                expected_keys.add("collected_count")
            if self.page_semantics == "selected_pools":
                expected_keys.update(
                    {
                        "pool_states",
                        "completed_pool_states",
                        "failed_pool_states",
                        "pool_errors",
                        "duplicate_count",
                    }
                )
        if (
            set(material) != expected_keys
            or state not in SIGNAL_COVERAGE_STATES
            or not isinstance(errors, list)
            or errors != expected_errors
            or len(errors) != len(set(errors))
            or any(not isinstance(item, str) or not item for item in errors)
            or isinstance(observation_count, bool)
            or observation_count < 0
            or retained_count != observation_count
            or not isinstance(truncated, bool)
            or (
                request_limit is not None
                and (
                    isinstance(request_limit, bool)
                    or not isinstance(request_limit, int)
                    or request_limit < 1
                )
            )
        ):
            raise ValueError("market signal pool coverage is invalid")
        provider_total = material.get("provider_total")
        provider_total_status = material.get("provider_total_status")
        if (
            provider_total_status not in {"reported", "not_exposed", "unavailable"}
            or (
                provider_total is not None
                and (
                    isinstance(provider_total, bool)
                    or not isinstance(provider_total, int)
                    or provider_total < 0
                )
            )
            or (provider_total is None) != (provider_total_status != "reported")
            or (
                self.provider_total_mode == "absent"
                and provider_total_status
                != ("unavailable" if early_unavailable else "not_exposed")
            )
            or (
                self.provider_total_mode == "required"
                and provider_total_status != "reported"
                and not (state == "indeterminate" and bool(errors))
            )
            or (
                self.provider_total_mode == "optional"
                and provider_total_status == "unavailable"
                and not early_unavailable
            )
            or (
                provider_total_status == "not_exposed"
                and not (
                    self.provider_total_mode == "optional"
                    and state == "partial"
                    and pages_collected is not None
                    and pages_expected is None
                )
                and self.provider_total_mode != "absent"
            )
        ):
            raise ValueError("market signal pool coverage total is invalid")
        if pages_collected is None:
            if pages_expected is not None or not early_unavailable:
                raise ValueError("market signal pool coverage pages are invalid")
        elif (
            isinstance(pages_collected, bool)
            or not isinstance(pages_collected, int)
            or pages_collected < 0
            or (
                pages_expected is not None
                and (
                    isinstance(pages_expected, bool)
                    or not isinstance(pages_expected, int)
                    or pages_expected < 1
                    or pages_collected > pages_expected
                )
            )
        ):
            raise ValueError("market signal pool coverage pages are invalid")
        if not early_unavailable:
            if self.page_semantics == "single_page" and (
                pages_collected != 1 or pages_expected != 1
            ):
                raise ValueError("market signal pool coverage pages are invalid")
            if self.page_semantics == "complete_pagination" and (
                pages_expected is None
                or (
                    state in {"observed_nonempty", "observed_empty"}
                    and not errors
                    and pages_collected != pages_expected
                )
            ):
                raise ValueError("market signal pool coverage pages are invalid")
            if self.page_semantics == "paged_prefix" and (
                pages_collected != 1
                or (pages_expected is None and provider_total_status != "not_exposed")
            ):
                raise ValueError("market signal pool coverage pages are invalid")
            if (
                self.provider_page_size is not None
                and isinstance(provider_total, int)
                and pages_expected
                != max(
                    1,
                    (provider_total + self.provider_page_size - 1)
                    // self.provider_page_size,
                )
            ):
                raise ValueError("market signal pool coverage pages are invalid")
        duplicate_count = 0
        if self.page_semantics == "selected_pools" and not early_unavailable:
            duplicate_count = self._validate_selected_pool_material(
                material,
                observation_count=observation_count,
                retained_pool_states=retained_pool_states,
                expected_errors=expected_errors,
            )
            if pages_collected != len(material["completed_pool_states"]):  # type: ignore[arg-type]
                raise ValueError("market signal pool coverage pages are invalid")
            if pages_expected != len(material["pool_states"]):  # type: ignore[arg-type]
                raise ValueError("market signal pool coverage pages are invalid")
        for key, allowed_values in self.bounded_details:
            if not early_unavailable:
                value = material.get(key)
                if not isinstance(value, str) or value not in allowed_values:
                    raise ValueError("market signal pool coverage detail is invalid")
        expected_truncated = False
        if self.exposes_collected_count and not early_unavailable:
            collected_count = material.get("collected_count")
            if (
                isinstance(collected_count, bool)
                or not isinstance(collected_count, int)
                or collected_count < observation_count
            ):
                raise ValueError("market signal pool coverage collection is invalid")
            expected_truncated = collected_count > observation_count
        elif isinstance(provider_total, int) and self.page_semantics in {
            "single_page",
            "complete_pagination",
        }:
            if provider_total < observation_count + duplicate_count:
                raise ValueError("market signal pool coverage retention is invalid")
            expected_truncated = provider_total > observation_count + duplicate_count
        elif isinstance(provider_total, int) and self.page_semantics == "paged_prefix":
            if provider_total < observation_count:
                raise ValueError("market signal pool coverage retention is invalid")
            if (provider_total > observation_count) != (state == "partial"):
                raise ValueError("market signal pool coverage retention is invalid")
        if truncated != expected_truncated or (
            truncated and (request_limit is None or observation_count != request_limit)
        ):
            raise ValueError("market signal pool coverage truncation is invalid")
        if state == "observed_empty" and (
            observation_count
            or errors
            or truncated
            or (provider_total is not None and provider_total != 0)
        ):
            raise ValueError("market signal observed-empty provenance is invalid")
        if state == "observed_nonempty" and (not observation_count or errors):
            raise ValueError("market signal nonempty coverage is invalid")
        if state == "indeterminate" and (observation_count or not errors):
            raise ValueError("market signal indeterminate coverage is invalid")
        if state == "partial" and not (
            observation_count
            or errors
            or (
                self.page_semantics == "paged_prefix"
                and isinstance(provider_total, int)
                and provider_total > observation_count
            )
        ):
            raise ValueError("market signal partial coverage is invalid")

    def _validate_selected_pool_material(
        self,
        material: Mapping[str, object],
        *,
        observation_count: int,
        retained_pool_states: Collection[object],
        expected_errors: list[str],
    ) -> int:
        requested = material.get("pool_states")
        completed = material.get("completed_pool_states")
        failed = material.get("failed_pool_states")
        pool_errors = material.get("pool_errors")
        duplicate_count = material.get("duplicate_count")
        if (
            len(retained_pool_states) != observation_count
            or any(
                not isinstance(item, str) or item not in self.pool_states
                for item in retained_pool_states
            )
            or not isinstance(requested, list)
            or not requested
            or any(
                not isinstance(item, str) or item not in self.pool_states
                for item in requested
            )
            or len(requested) != len(set(requested))
            or not isinstance(completed, list)
            or any(
                not isinstance(item, str) or item not in self.pool_states
                for item in completed
            )
            or len(completed) != len(set(completed))
            or completed != sorted(completed)
            or not isinstance(failed, list)
            or any(
                not isinstance(item, str) or item not in self.pool_states
                for item in failed
            )
            or len(failed) != len(set(failed))
            or failed != sorted(failed)
            or set(completed).intersection(failed)
            or set(completed).union(failed) != set(requested)
            or not set(retained_pool_states).issubset(completed)
            or not isinstance(pool_errors, list)
            or isinstance(duplicate_count, bool)
            or not isinstance(duplicate_count, int)
            or duplicate_count < 0
        ):
            raise ValueError("market signal pool coverage selection is invalid")
        expected_failed: list[str] = []
        observed_error_codes: list[str] = []
        for item in pool_errors:
            if (
                not isinstance(item, dict)
                or set(item) != {"pool_state", "error_code"}
                or item.get("pool_state") not in failed
                or not isinstance(item.get("error_code"), str)
                or not item["error_code"]
            ):
                raise ValueError("market signal pool coverage errors are invalid")
            expected_failed.append(item["pool_state"])
            observed_error_codes.append(item["error_code"])
        if (
            expected_failed != failed
            or sorted(set(observed_error_codes)) != expected_errors
        ):
            raise ValueError("market signal pool coverage errors are invalid")
        provider_total = material.get("provider_total")
        if completed:
            if (
                not isinstance(provider_total, int)
                or isinstance(provider_total, bool)
                or provider_total != observation_count + duplicate_count
            ):
                raise ValueError("market signal pool coverage total is invalid")
        elif provider_total is not None or observation_count or duplicate_count:
            raise ValueError("market signal pool coverage total is invalid")
        expected_state = (
            "partial"
            if completed and failed
            else "indeterminate"
            if failed
            else "observed_nonempty"
            if observation_count
            else "observed_empty"
        )
        if material.get("state") != expected_state:
            raise ValueError("market signal pool coverage state is invalid")
        return duplicate_count


_QUALIFIED_MARKET_SIGNAL_COVERAGE_AUTHORITIES = (
    MarketSignalCoverageAuthority(
        "eastmoney_focus_monitoring@1",
        "focus_monitoring",
        "required",
        "single_page",
    ),
    MarketSignalCoverageAuthority(
        "eastmoney_industry_rotation@1",
        "industry_rotation",
        "required",
        "complete_pagination",
        provider_total_fields=frozenset({"data.total"}),
        provider_page_size=100,
    ),
    MarketSignalCoverageAuthority(
        "eastmoney_limit_state@1",
        "limit_state",
        "required",
        "selected_pools",
        pool_states=frozenset(
            {"limit_up", "limit_break", "limit_down", "previous_limit_up"}
        ),
        provider_total_fields=frozenset({"data.tc"}),
    ),
    MarketSignalCoverageAuthority(
        "eastmoney_security_board_membership@1",
        "security_board_membership",
        "required",
        "complete_pagination",
        provider_total_fields=frozenset({"data.total"}),
        provider_page_size=200,
    ),
    MarketSignalCoverageAuthority(
        "eastmoney_severe_abnormal_movement@1",
        "severe_abnormal_movement",
        "absent",
        "complete_pagination",
        bounded_details=(
            (
                "pagination_termination",
                frozenset(
                    {
                        "declared_last_page",
                        "empty_first_page",
                        "empty_sentinel",
                        "short_declared_last_page",
                        "source_error",
                    }
                ),
            ),
        ),
        pages_expected_fields=frozenset({"pages"}),
        collected_count_fields=frozenset({"count"}),
        exposes_collected_count=True,
    ),
    MarketSignalCoverageAuthority(
        "ths_limit_reason@1",
        "limit_state",
        "optional",
        "paged_prefix",
        provider_total_fields=frozenset({"data.total", "data.count"}),
        provider_page_size=200,
    ),
    MarketSignalCoverageAuthority(
        "ths_market_heat@1",
        "market_heat",
        "absent",
        "single_page",
        exposes_collected_count=True,
    ),
    MarketSignalCoverageAuthority(
        "ths_strong_stock_theme@1",
        "strong_stock_theme",
        "required",
        "single_page",
    ),
)

MARKET_SIGNAL_COVERAGE_AUTHORITIES: Mapping[
    tuple[str, str], MarketSignalCoverageAuthority
] = MappingProxyType(
    {
        (item.operation_id, item.signal_type): item
        for item in _QUALIFIED_MARKET_SIGNAL_COVERAGE_AUTHORITIES
    }
)


def market_signal_coverage_authority(
    operation_id: str,
    signal_type: str,
) -> MarketSignalCoverageAuthority | None:
    return MARKET_SIGNAL_COVERAGE_AUTHORITIES.get((operation_id, signal_type))


def market_signal_coverage_field_destination(
    operation_id: str,
    field_name: str,
) -> str | None:
    """Resolve a raw coverage count through the owner-local authority."""

    destinations = {
        destination
        for authority in _QUALIFIED_MARKET_SIGNAL_COVERAGE_AUTHORITIES
        if authority.operation_id == operation_id
        if (destination := authority.field_destination(field_name)) is not None
    }
    if len(destinations) > 1:
        raise ValueError("market signal coverage field destination is ambiguous")
    return next(iter(destinations), None)


def is_market_signal_coverage_destination(
    operation_id: str,
    destination: str,
) -> bool:
    """Return whether the path is declared by that owner's coverage authority."""

    return destination in {
        value
        for authority in _QUALIFIED_MARKET_SIGNAL_COVERAGE_AUTHORITIES
        if authority.operation_id == operation_id
        for field_name in (
            *authority.provider_total_fields,
            *authority.pages_expected_fields,
            *authority.collected_count_fields,
        )
        if (value := authority.field_destination(field_name)) is not None
    }


@dataclass(frozen=True)
class MarketSignalPeriodAuthority:
    """One owner-local public period shape."""

    frequencies: frozenset[str]
    observed_at: Literal["required", "absent"]
    boundary: Literal["observed_on", "provider_range"] = "observed_on"

    def __post_init__(self) -> None:
        if not self.frequencies:
            raise ValueError("market signal period authority requires a frequency")

    def matches(
        self,
        period: Mapping[str, object],
        observed_on: str,
        observed_at: object,
    ) -> bool:
        if (
            set(period) != {"start", "end", "frequency"}
            or period.get("frequency") not in self.frequencies
            or not (
                (self.observed_at == "required" and isinstance(observed_at, str))
                or (self.observed_at == "absent" and observed_at is None)
            )
        ):
            return False
        start = period.get("start")
        end = period.get("end")
        if self.boundary == "observed_on":
            return start == observed_on and end == observed_on
        if not isinstance(start, str) or not isinstance(end, str):
            return False
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            return False
        return (
            start_date.isoformat() == start
            and end_date.isoformat() == end
            and start_date <= end_date
        )

    def payload(
        self,
        observed_on: str,
        observed_at: str | None,
        *,
        start: str | None = None,
        end: str | None = None,
        frequency: str | None = None,
    ) -> dict[str, str | None]:
        selected_frequency = frequency
        if selected_frequency is None:
            if len(self.frequencies) != 1:
                raise ValueError("market signal period frequency must be selected")
            selected_frequency = next(iter(self.frequencies))
        period: dict[str, str | None] = {
            "start": observed_on if start is None else start,
            "end": observed_on if end is None else end,
            "frequency": selected_frequency,
        }
        if not self.matches(period, observed_on, observed_at):
            raise ValueError("market signal period contradicts owner authority")
        return period


@dataclass(frozen=True)
class MarketSignalPeriodStateAuthority:
    """Derive one categorical dimension from an observation and provider window."""

    dimension_key: str

    def state(self, period: Mapping[str, object], observed_on: str) -> str:
        start = period.get("start")
        end = period.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("market signal period state is invalid")
        try:
            observed_date = date.fromisoformat(observed_on)
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as error:
            raise ValueError("market signal period state is invalid") from error
        if (
            observed_date.isoformat() != observed_on
            or start_date.isoformat() != start
            or end_date.isoformat() != end
            or start_date > end_date
        ):
            raise ValueError("market signal period state is invalid")
        if observed_date < start_date:
            return "scheduled"
        if observed_date <= end_date:
            return "active"
        return "expired"

    def validate(
        self,
        period: Mapping[str, object],
        observed_on: str,
        dimensions: Mapping[str, object],
    ) -> None:
        if dimensions.get(self.dimension_key) != self.state(period, observed_on):
            raise ValueError("market signal period state contradicts owner authority")


@dataclass(frozen=True)
class MarketSignalObservationAuthority:
    """Owner-local semantics shared by one source Adapter and the serializer."""

    operation_id: str
    signal_type: str
    source_role: str
    subject_scope: Literal["security", "market"]
    market_scope: str
    metrics: Mapping[str, tuple[str, str]]
    period: MarketSignalPeriodAuthority
    period_state: MarketSignalPeriodStateAuthority | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def metric_semantics(
        self,
        metric_names: Collection[str],
    ) -> tuple[dict[str, str], dict[str, str]]:
        if set(metric_names) != set(self.metrics):
            raise ValueError("market signal metric names contradict owner authority")
        return (
            {name: self.metrics[name][0] for name in metric_names},
            {name: self.metrics[name][1] for name in metric_names},
        )

    def validate_payload(self, payload: Mapping[str, object]) -> None:
        if payload.get("source_role") != self.source_role:
            raise ValueError("market signal source role contradicts owner authority")
        subject = payload.get("subject")
        if self.subject_scope == "security" and not isinstance(subject, Mapping):
            raise ValueError("market signal subject scope contradicts owner authority")
        if self.subject_scope == "market" and subject is not None:
            raise ValueError("market signal subject scope contradicts owner authority")
        dimensions = payload.get("dimensions")
        if (
            not isinstance(dimensions, Mapping)
            or dimensions.get("market_scope") != self.market_scope
        ):
            raise ValueError("market signal market scope contradicts owner authority")
        metrics = payload.get("metrics")
        units = payload.get("units")
        directions = payload.get("directions")
        if (
            not isinstance(metrics, Mapping)
            or not isinstance(units, Mapping)
            or not isinstance(directions, Mapping)
        ):
            raise ValueError("market signal metric semantics are invalid")
        expected_units, expected_directions = self.metric_semantics(metrics)
        if units != expected_units:
            raise ValueError("market signal unit contradicts owner authority")
        if directions != expected_directions:
            raise ValueError("market signal direction contradicts owner authority")
        period = payload.get("period")
        observed_on = payload.get("observed_on")
        if (
            not isinstance(period, Mapping)
            or not isinstance(observed_on, str)
            or not self.period.matches(period, observed_on, payload.get("observed_at"))
        ):
            raise ValueError("market signal period contradicts owner authority")
        if self.period_state is not None:
            self.period_state.validate(period, observed_on, dimensions)


def _signal_period(
    *frequencies: str,
    observed_at: Literal["required", "absent"] = "absent",
    boundary: Literal["observed_on", "provider_range"] = "observed_on",
) -> MarketSignalPeriodAuthority:
    return MarketSignalPeriodAuthority(frozenset(frequencies), observed_at, boundary)


_QUALIFIED_MARKET_SIGNAL_AUTHORITIES = (
    MarketSignalObservationAuthority(
        "eastmoney_focus_monitoring@1",
        "focus_monitoring",
        "market_signal",
        "market",
        "provider_watchlist",
        {"watchlist_membership": ("boolean_indicator", "descriptive")},
        _signal_period("calendar_date_window", boundary="provider_range"),
        MarketSignalPeriodStateAuthority("monitoring_state"),
    ),
    MarketSignalObservationAuthority(
        "eastmoney_industry_rotation@1",
        "industry_rotation",
        "market_signal",
        "market",
        "eastmoney_industry_boards",
        {
            "change_rate": ("percent", "positive_is_gain"),
            "up_count": ("security_count", "not_directional"),
            "down_count": ("security_count", "not_directional"),
            "leader_change_rate": ("percent", "positive_is_gain"),
        },
        _signal_period("snapshot", observed_at="required"),
    ),
    MarketSignalObservationAuthority(
        "eastmoney_limit_state@1",
        "limit_state",
        "market_signal",
        "market",
        "provider_a_share_limit_pool",
        {
            "price": ("CNY_per_share", "not_directional"),
            "change_rate": ("percent", "positive_is_gain"),
            "turnover_rate": ("percent", "higher_is_more_turnover"),
            "amount": ("CNY", "not_directional"),
            "float_market_cap": ("CNY", "not_directional"),
            "seal_fund": ("CNY", "higher_is_more_seal_fund"),
            "limit_price": ("CNY_per_share", "not_directional"),
            "amplitude": ("percent", "not_directional"),
            "speed": ("percent", "positive_is_gain"),
            "pe": ("ratio", "not_directional"),
            "board_amount": ("CNY", "not_directional"),
            "consecutive_limit_days": ("count", "not_directional"),
            "break_times": ("count", "higher_is_more_breaks"),
            "consecutive_limit_down_days": ("count", "not_directional"),
            "open_times": ("count", "higher_is_more_opens"),
            "previous_consecutive_limit_days": (
                "count",
                "not_directional",
            ),
        },
        _signal_period("trading_day"),
    ),
    MarketSignalObservationAuthority(
        "eastmoney_security_board_membership@1",
        "security_board_membership",
        "market_signal",
        "security",
        "mainland_a_share",
        {"board_change_rate": ("percent", "positive_is_gain")},
        _signal_period("snapshot"),
    ),
    MarketSignalObservationAuthority(
        "eastmoney_severe_abnormal_movement@1",
        "severe_abnormal_movement",
        "market_signal",
        "market",
        "provider_anomaly_pool",
        {
            "change_rate": ("percent", "positive_is_gain"),
            "cumulative_deviation": (
                "percent",
                "positive_is_above_benchmark",
            ),
            "target_change_rate": ("percent", "positive_is_gain"),
        },
        _signal_period("trading_day"),
    ),
    MarketSignalObservationAuthority(
        "ths_limit_reason@1",
        "limit_state",
        "market_signal",
        "market",
        "provider_a_share_limit_pool",
        {
            "price": ("CNY_per_share", "not_directional"),
            "change_rate": ("percent", "positive_is_gain"),
            "seal_success_rate": ("ratio", "higher_is_more_successful"),
            "break_times": ("count", "higher_is_more_breaks"),
            "seal_fund": ("CNY", "higher_is_more_seal_fund"),
        },
        _signal_period("trading_day"),
    ),
    MarketSignalObservationAuthority(
        "ths_market_heat@1",
        "market_heat",
        "market_signal",
        "market",
        "mainland_a_share",
        {
            "rank": ("rank", "lower_is_more_popular"),
            "heat": ("provider_score", "higher_is_more_popular"),
            "change_rate": ("percent", "positive_is_gain"),
            "rank_change": ("rank", "provider_defined"),
        },
        _signal_period("current_hour_ranking", "current_day_ranking"),
    ),
    MarketSignalObservationAuthority(
        "ths_strong_stock_theme@1",
        "strong_stock_theme",
        "market_signal",
        "market",
        "mainland_a_share",
        {
            "change_rate": ("percent", "positive_is_gain"),
            "close_price": ("CNY_per_share", "not_directional"),
            "turnover_rate": ("percent", "not_directional"),
            "trading_amount": ("CNY", "not_directional"),
        },
        _signal_period("trading_day"),
    ),
)

MARKET_SIGNAL_OBSERVATION_AUTHORITIES: Mapping[
    tuple[str, str], MarketSignalObservationAuthority
] = MappingProxyType(
    {
        (item.operation_id, item.signal_type): item
        for item in _QUALIFIED_MARKET_SIGNAL_AUTHORITIES
    }
)


def market_signal_observation_authority(
    operation_id: str,
    signal_type: str,
) -> MarketSignalObservationAuthority | None:
    return MARKET_SIGNAL_OBSERVATION_AUTHORITIES.get((operation_id, signal_type))


def validate_market_signal_observation_authority(
    operation_id: str,
    signal_type: str,
    payload: Mapping[str, object],
) -> None:
    """Validate qualified owners while leaving injected test owners extensible."""

    authority = market_signal_observation_authority(operation_id, signal_type)
    if authority is not None:
        authority.validate_payload(payload)
        return
    if any(owner == operation_id for owner, _ in MARKET_SIGNAL_OBSERVATION_AUTHORITIES):
        raise ValueError("market signal type contradicts owner authority")


@dataclass(frozen=True)
class MarketSignalQuery:
    """Normalized bounded request passed to one market-signal operation."""

    signal_types: tuple[str, ...]
    as_of: str
    observed_from: str
    observed_to: str
    limit: int
    subject: dict[str, Any] | None
    parameters: dict[str, Any]
    allow_fallback: bool = True


@dataclass(frozen=True)
class ThemeAttribution:
    """A reason label whose provenance remains explicit in public evidence."""

    text: str
    provenance: str
    source_operation: str
    source_document_id: str | None
    locator_uri: str | None
    basis_evidence_ids: tuple[str, ...] = ()
    method_id: str | None = None


@dataclass(frozen=True)
class MarketSignalObservation:
    """One source-indexed market signal before coordinator reconciliation."""

    signal_type: str
    source_operation: str
    source_role: str
    subject: dict[str, Any] | None
    source_document_id: str | None
    observed_on: str
    observed_at: str | None
    available_at: str | None
    retrieved_at: datetime
    period: dict[str, str | None]
    metrics: dict[str, str | None]
    units: dict[str, str]
    directions: dict[str, str]
    rule: dict[str, Any] | None
    attributions: tuple[ThemeAttribution, ...]
    dimensions: dict[str, Any]
    locator_uri: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignalCoverage:
    """One operation's explicit coverage state for one requested signal type."""

    state: str
    provider_total: int | None = None
    pages_collected: int | None = None
    pages_expected: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.state}
        if self.provider_total is not None:
            result["provider_total"] = self.provider_total
        if self.pages_collected is not None:
            result["pages_collected"] = self.pages_collected
        if self.pages_expected is not None:
            result["pages_expected"] = self.pages_expected
        result.update(self.details)
        return result


@dataclass(frozen=True)
class SignalSourceFailure:
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
class SignalSourceBatch:
    """One operation's observations and per-signal acquisition coverage."""

    operation_id: str
    observations: tuple[MarketSignalObservation, ...] = ()
    coverage: dict[str, SignalCoverage] = field(default_factory=dict)
    source_errors: tuple[SignalSourceFailure, ...] = ()
    degradations: tuple[SignalSourceFailure, ...] = ()
    limitations: tuple[str, ...] = ()


class MarketSignalSourceOperation(Protocol):
    """Adapter operation seam hidden behind the market-signals module."""

    operation_id: str
    supported_signal_types: frozenset[str]
    raw_fields: tuple[str, ...]

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch: ...


@runtime_checkable
class ParameterAwareMarketSignalSourceOperation(Protocol):
    """Optional seam for operations whose applicability depends on sub-parameters."""

    def is_applicable(self, query: MarketSignalQuery) -> bool: ...


MarketSignalHttpTransport = ContentHttpTransport
