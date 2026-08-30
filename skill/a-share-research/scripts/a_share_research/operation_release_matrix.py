"""Fail-closed release qualification for versioned-plan operation owners."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Literal, Mapping, Sequence, cast
from urllib.parse import urlsplit

from .exact_normalization import (
    ExactDecimalTerm,
    ExactNormalizationError,
    exact_scaled_sum,
)
from .research_result import (
    validate_field_disposition_destination_contract,
    validate_plan_operation_producer_authority,
)

OperationOwner = tuple[str, str]
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@#-]{0,255}$")
_CANONICAL_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CANONICAL_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00$"
)
_CANONICAL_DECIMAL = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE]-?(?:0|[1-9][0-9]*))?$"
)
_CANDIDATE_SHA = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_LIVE_EVIDENCE_AGE = timedelta(days=7)
_SOURCE_RESULT_PRECEDENCE = (
    "complete",
    "partial",
    "observed_empty",
    "credential_blocked",
    "unavailable",
)
_RELEASE_SOURCE_RESULTS = frozenset(_SOURCE_RESULT_PRECEDENCE)
_CREDENTIAL_FAILURE_CODES = {
    "credential_not_allowed",
    "credentials_not_allowed",
    "missing_credential",
    "permission_denied",
}
_SENSITIVE_KEY_TOKENS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "headers",
    "password",
    "raw_payload",
    "secret",
    "server_url",
    "token",
}
_SENSITIVE_COMPACT_KEY_TOKENS = frozenset(
    token.replace("_", "") for token in _SENSITIVE_KEY_TOKENS
)
_QUALIFIED_FIRST_PARTY_HOSTS = {
    "bse": "www.bse.cn",
    "sse": "www.sse.com.cn",
    "szse": "www.szse.cn",
}
_SOURCE_CONTRACT_KEYS = {
    "producer_operation_id",
    "capability_id",
    "fixture_id",
    "provider",
    "scope",
    "request_selector",
    "response_shape",
    "field_destinations",
    "normalization",
    "field_applicability",
    "state_mapping",
    "dependency_policy",
    "license_policy",
}
_MARKET_FOUNDATION_SOURCE_CAPABILITIES = {
    "bse_a_share_turnover@1": "whole_market_turnover",
    "bse_market_margin@1": "whole_market_margin",
    "sse_a_share_turnover@1": "whole_market_turnover",
    "sse_market_margin@1": "whole_market_margin",
    "szse_a_share_turnover@1": "whole_market_turnover",
    "szse_market_margin@1": "whole_market_margin",
}
# Canonical-JSON digests pin every reviewed public qualification fact and
# nested source contract to its exact operation owner.
_MARKET_FOUNDATION_QUALIFICATION_SHA256 = {
    "bse_a_share_turnover@1": "5761adb40b2d2023aef578d977f382586ffab1d3de3a8e5110241ea8b675a776",
    "bse_market_margin@1": "a41d52ee4975e1879891608071e287401dc7c66be21bc9633410cfe40ebab944",
    "sse_a_share_turnover@1": "c02f9ffea373119928d3e4b7850a141ef0ee16c955cf1205c23b397c7338bd3e",
    "sse_market_margin@1": "9e9f63d48867e996d39ed38a106af9483750b55ced651718e4c7ce00627591d6",
    "szse_a_share_turnover@1": "c5f3ff7e6e46e33d14461005fb006294ee053f91bcb93f6291e781bdc8522a56",
    "szse_market_margin@1": "c6dd9fe0f7900c1612ed3e0c26508f0c71e512a2b83bc2fa3fb4b37a3dc7b265",
}
_MARKET_FOUNDATION_FIXTURE_SHA256 = (
    "6ff5e67208789a855c2db9d4a23945442539f2e3e032df74af311bc7d64e1146"
)


@dataclass(frozen=True)
class _MarketFoundationNormalizationRule:
    """One owner-bound fixture transform delegated to exact arithmetic."""

    contract: Mapping[str, str]
    field_destinations: Mapping[str, str]
    source_scale_exponents: Mapping[str, int]
    observed_empty_row_count: int
    unavailable_reason: str


_MARGIN_FIELD_DESTINATIONS = {
    "FINANCING_BALANCE": "observations.payload.observation.metrics.financing_balance_CNY",
    "FINANCING_BUY_AMOUNT": "observations.payload.observation.metrics.financing_buy_amount_CNY",
    "SECURITIES_LENDING_SELL_QUANTITY": "observations.payload.observation.metrics.securities_lending_sell_quantity_shares",
    "SECURITIES_LENDING_QUANTITY": "observations.payload.observation.metrics.securities_lending_quantity_shares",
    "SECURITIES_LENDING_BALANCE": "observations.payload.observation.metrics.securities_lending_balance_CNY",
    "MARGIN_TOTAL_BALANCE": "observations.payload.observation.metrics.margin_total_balance_CNY",
}
_MARKET_FOUNDATION_NORMALIZATION_RULES = {
    "bse_a_share_turnover@1": _MarketFoundationNormalizationRule(
        contract={
            "asset_filter": "accept_only_the_official_BSE_stock_daily_report_total",
            "scale": "multiply_ten_thousand_CNY_by_10000_using_exact_decimal",
            "date": "require_report_date_equal_requested_completed_trading_date",
        },
        field_destinations={
            "A_SHARE_TURNOVER_TEN_THOUSAND_CNY": (
                "observations.payload.observation.metrics.turnover_amount_CNY"
            )
        },
        source_scale_exponents={"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": 4},
        observed_empty_row_count=1,
        unavailable_reason="required_report_unit_missing",
    ),
    "bse_market_margin@1": _MarketFoundationNormalizationRule(
        contract={
            "money_scale": "multiply_ten_thousand_CNY_by_10000_using_exact_decimal",
            "quantity_scale": "multiply_ten_thousand_shares_by_10000_using_exact_decimal",
            "field_absence": "map_required_field_absence_to_unavailable_never_not_applicable",
        },
        field_destinations=_MARGIN_FIELD_DESTINATIONS,
        source_scale_exponents={field: 4 for field in _MARGIN_FIELD_DESTINATIONS},
        observed_empty_row_count=0,
        unavailable_reason="required_summary_field_missing",
    ),
    "sse_a_share_turnover@1": _MarketFoundationNormalizationRule(
        contract={
            "asset_filter": "sum_only_PRODUCT_CODE_01_and_03_and_reject_missing_or_duplicate_rows",
            "scale": "multiply_each_hundred_million_CNY_value_by_100000000_then_exact_sum",
            "date": "require_both_rows_TRADE_DATE_equal_requested_completed_trading_date",
        },
        field_destinations={
            "TRADE_AMT_01": "observations.payload.observation.metrics.turnover_amount_CNY",
            "TRADE_AMT_03": "observations.payload.observation.metrics.turnover_amount_CNY",
        },
        source_scale_exponents={"TRADE_AMT_01": 8, "TRADE_AMT_03": 8},
        observed_empty_row_count=2,
        unavailable_reason="required_A_share_partition_missing",
    ),
    "sse_market_margin@1": _MarketFoundationNormalizationRule(
        contract={
            "money_scale": "official_CNY_values_retained_as_exact_decimal_CNY",
            "quantity_scale": "official_share_values_retained_as_exact_decimal_shares",
            "field_absence": "map_required_field_absence_to_unavailable_never_not_applicable",
        },
        field_destinations=_MARGIN_FIELD_DESTINATIONS,
        source_scale_exponents={field: 0 for field in _MARGIN_FIELD_DESTINATIONS},
        observed_empty_row_count=0,
        unavailable_reason="required_summary_field_missing",
    ),
    "szse_a_share_turnover@1": _MarketFoundationNormalizationRule(
        contract={
            "asset_filter": "accept_only_row_explicitly_labeled_A_share_total_and_reject_stock_or_index_proxy",
            "scale": "multiply_ten_thousand_CNY_by_10000_using_exact_decimal",
            "date": "require_report_date_equal_requested_completed_trading_date",
        },
        field_destinations={
            "A_SHARE_TURNOVER_TEN_THOUSAND_CNY": (
                "observations.payload.observation.metrics.turnover_amount_CNY"
            )
        },
        source_scale_exponents={"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": 4},
        observed_empty_row_count=1,
        unavailable_reason="required_A_share_total_missing",
    ),
    "szse_market_margin@1": _MarketFoundationNormalizationRule(
        contract={
            "money_scale": "official_CNY_values_retained_as_exact_decimal_CNY",
            "quantity_scale": "official_share_values_retained_as_exact_decimal_shares",
            "field_absence": "map_required_field_absence_to_unavailable_never_not_applicable",
        },
        field_destinations=_MARGIN_FIELD_DESTINATIONS,
        source_scale_exponents={field: 0 for field in _MARGIN_FIELD_DESTINATIONS},
        observed_empty_row_count=0,
        unavailable_reason="required_summary_field_missing",
    ),
}


@dataclass(frozen=True)
class ReleasePlanSnapshot:
    """One actual public versioned-plan result and its application profile."""

    result: Mapping[str, object]
    request_profile: str
    candidate_sha: str | None = None
    verified_at: str | None = None
    attempt_digest: str | None = None
    request_as_of: str | None = None


@dataclass(frozen=True)
class OperationQualificationEvidence:
    """Operation-specific qualification facts joined to the plan denominator."""

    producer_operation_id: str
    disposition: Literal["integrated", "experimental", "pending_dependency"]
    temporal_semantics: Mapping[str, str]
    unit_semantics: Mapping[str, str]
    failure_semantics: str
    empty_result_semantics: str
    acquisition_role: str
    credential_policy: str
    rate_limit_policy: Mapping[str, object]
    offline_contract_selector: str
    offline_contract_result: str
    evidence_references: tuple[str, ...]
    capability_ids: tuple[str, ...] = ()
    gap_issues: tuple[int, ...] = ()
    source_contract: Mapping[str, object] | None = None


@dataclass(frozen=True)
class InstallationLoadEvidence:
    """One producer's default-install load result and evidence origin."""

    producer_operation_id: str
    origin: Literal["installation_health", "copied_install_plan"]
    status: str
    selector: str


@dataclass(frozen=True)
class LiveProbeEvidence:
    """A sanitized, explicitly dated release-probe result for one owner."""

    producer_operation_id: str
    probe: str
    probe_status: str
    attempted: bool
    source_result: str
    verified_at: str
    candidate_sha: str | None = None
    request_profile: str | None = None
    attempt_digest: str | None = None


@dataclass(frozen=True)
class OperationReleaseMatrix:
    """Serializable operation qualification matrix."""

    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return deepcopy(dict(self.payload))

    def to_json_bytes(self) -> bytes:
        """Return stable artifact bytes independent of input ordering."""

        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")


def formal_operation_applications(
    plan_snapshots: Sequence[ReleasePlanSnapshot],
) -> tuple[OperationOwner, ...]:
    """Derive exact capability-to-producer applications from public plans."""

    applications: set[OperationOwner] = set()
    for snapshot in plan_snapshots:
        _safe_release_reference(snapshot.request_profile, "request profile")
        for plan, _, _ in _release_plan_views(snapshot.result):
            validate_plan_operation_producer_authority(plan)
            for raw_obligation in _sequence(
                plan.get("operation_obligations"),
                "release plan operation obligations",
            ):
                obligation = _mapping(raw_obligation, "release plan obligation")
                operation_kind = _text(
                    obligation.get("operation_kind"), "release operation kind"
                )
                if operation_kind == "deterministic_coordinator":
                    continue
                if operation_kind != "external_source":
                    raise ValueError("release operation kind is unsupported")
                applications.add(
                    (
                        _safe_release_reference(
                            obligation.get("capability_id"), "formal capability id"
                        ),
                        _safe_release_reference(
                            obligation.get("producer_operation_id"),
                            "formal producer operation id",
                        ),
                    )
                )
    if not applications:
        raise ValueError("release matrix requires formal plan operations")
    return tuple(sorted(applications))


def formal_producer_operation_ids(
    plan_snapshots: Sequence[ReleasePlanSnapshot],
) -> tuple[str, ...]:
    """Project the unique-producer denominator from formal applications."""

    applications = formal_operation_applications(plan_snapshots)
    return tuple(sorted({producer for _, producer in applications}))


def load_release_probe_evidence(
    payload: Mapping[str, object],
) -> tuple[
    tuple[ReleasePlanSnapshot, ...],
    tuple[InstallationLoadEvidence, ...],
    Mapping[str, object],
    tuple[LiveProbeEvidence, ...],
]:
    """Load one generated, sanitized release-probe evidence document."""

    if (
        set(payload)
        != {
            "schema_version",
            "candidate_sha",
            "verified_at",
            "attempt_digests",
            "installation_health",
            "plan_snapshots",
            "installation_loads",
            "live_probes",
        }
        or payload.get("schema_version") != "1.0"
    ):
        raise ValueError("release probe evidence is invalid")
    candidate_sha = _text(payload.get("candidate_sha"), "release candidate SHA")
    if _CANDIDATE_SHA.fullmatch(candidate_sha) is None:
        raise ValueError("release candidate SHA is invalid")
    verified_at = _text(payload.get("verified_at"), "release verified_at")
    assembled_time = _verified_at(verified_at)
    attempt_digests = tuple(
        _text(item, "release attempt digest")
        for item in _sequence(payload.get("attempt_digests"), "release attempt digests")
    )
    if (
        not attempt_digests
        or tuple(sorted(set(attempt_digests))) != attempt_digests
        or any(_ATTEMPT_DIGEST.fullmatch(item) is None for item in attempt_digests)
    ):
        raise ValueError("release attempt digests are invalid")
    snapshots: list[ReleasePlanSnapshot] = []
    for raw_snapshot in _sequence(
        payload.get("plan_snapshots"), "release plan snapshots"
    ):
        snapshot = _mapping(raw_snapshot, "release plan snapshot")
        if set(snapshot) != {
            "request_profile",
            "request_as_of",
            "result",
            "candidate_sha",
            "verified_at",
            "attempt_digest",
        }:
            raise ValueError("release plan snapshot is invalid")
        snapshot_verified_at = _text(
            snapshot.get("verified_at"), "release plan snapshot verified_at"
        )
        snapshot_time = _verified_at(snapshot_verified_at)
        if (
            snapshot.get("candidate_sha") != candidate_sha
            or snapshot.get("attempt_digest") not in attempt_digests
            or snapshot_time > assembled_time
            or assembled_time - snapshot_time > _MAX_LIVE_EVIDENCE_AGE
        ):
            raise ValueError("release plan snapshot provenance drifted")
        result = _mapping(snapshot.get("result"), "release plan result")
        request_as_of = canonical_release_as_of(
            snapshot.get("request_as_of"), "release plan snapshot as_of"
        )
        _validate_sanitized_release_result(result)
        snapshots.append(
            ReleasePlanSnapshot(
                result=deepcopy(dict(result)),
                request_profile=_safe_release_reference(
                    snapshot.get("request_profile"), "release request profile"
                ),
                candidate_sha=candidate_sha,
                verified_at=snapshot_verified_at,
                attempt_digest=cast(str, snapshot["attempt_digest"]),
                request_as_of=request_as_of,
            )
        )
    if {cast(str, snapshot.attempt_digest) for snapshot in snapshots} != set(
        attempt_digests
    ):
        raise ValueError("release attempt digests do not close plan snapshots")
    installation_loads: list[InstallationLoadEvidence] = []
    for raw_load in _sequence(
        payload.get("installation_loads"), "release installation loads"
    ):
        load = _mapping(raw_load, "release installation load")
        if set(load) != {"producer_operation_id", "origin", "status", "selector"}:
            raise ValueError("release installation load is invalid")
        origin = load.get("origin")
        if origin not in {"installation_health", "copied_install_plan"}:
            raise ValueError("release installation load origin is invalid")
        status = _text(load.get("status"), "installation status")
        if status != "complete":
            raise ValueError("release installation load is incomplete")
        installation_loads.append(
            InstallationLoadEvidence(
                producer_operation_id=_text(
                    load.get("producer_operation_id"), "installation producer id"
                ),
                origin=cast(
                    Literal["installation_health", "copied_install_plan"], origin
                ),
                status=status,
                selector=_safe_release_reference(
                    load.get("selector"), "installation selector"
                ),
            )
        )
    live_probes: list[LiveProbeEvidence] = []
    for raw_probe in _sequence(payload.get("live_probes"), "release live probes"):
        probe = _mapping(raw_probe, "release live probe")
        if set(probe) != {
            "producer_operation_id",
            "probe",
            "probe_status",
            "attempted",
            "source_result",
            "verified_at",
            "candidate_sha",
            "request_profile",
            "attempt_digest",
        }:
            raise ValueError("release live probe is invalid")
        probe_verified_at = _text(probe.get("verified_at"), "live probe verified_at")
        if (
            probe.get("candidate_sha") != candidate_sha
            or probe.get("attempt_digest") not in attempt_digests
        ):
            raise ValueError("release live probe provenance drifted")
        probe_time = _verified_at(probe_verified_at)
        age = assembled_time - probe_time
        if age < timedelta(0) or age > _MAX_LIVE_EVIDENCE_AGE:
            raise ValueError("release live probe evidence is stale")
        source_result = _text(probe.get("source_result"), "live probe source result")
        if source_result not in _RELEASE_SOURCE_RESULTS:
            raise ValueError("live probe source result is unsupported")
        live_probes.append(
            LiveProbeEvidence(
                producer_operation_id=_text(
                    probe.get("producer_operation_id"), "live probe producer id"
                ),
                probe=_safe_release_reference(probe.get("probe"), "live probe"),
                probe_status=_text(probe.get("probe_status"), "live probe status"),
                attempted=_boolean(probe.get("attempted"), "live probe attempted"),
                source_result=source_result,
                verified_at=probe_verified_at,
                candidate_sha=candidate_sha,
                request_profile=_safe_release_reference(
                    probe.get("request_profile"), "live probe request profile"
                ),
                attempt_digest=cast(str, probe["attempt_digest"]),
            )
        )
    health = _validated_installation_health(
        _mapping(payload.get("installation_health"), "installation health")
    )
    denominator = set(formal_producer_operation_ids(snapshots))
    if {item.producer_operation_id for item in installation_loads} != denominator or {
        item.producer_operation_id for item in live_probes
    } != denominator:
        raise ValueError("release probe evidence does not close the denominator")
    application_profiles: dict[str, set[str]] = {}
    for release_snapshot in snapshots:
        for plan, _, _ in _release_plan_views(release_snapshot.result):
            for raw_obligation in _sequence(
                plan.get("operation_obligations"), "release plan obligations"
            ):
                obligation = _mapping(raw_obligation, "release plan obligation")
                if obligation.get("operation_kind") != "external_source":
                    continue
                producer_operation_id = _safe_release_reference(
                    obligation.get("producer_operation_id"),
                    "formal producer operation id",
                )
                application_profiles.setdefault(producer_operation_id, set()).add(
                    release_snapshot.request_profile
                )
    if any(
        load.origin == "copied_install_plan"
        and load.selector
        not in application_profiles.get(load.producer_operation_id, set())
        for load in installation_loads
    ):
        raise ValueError("copied-install selector is not a plan application")
    _validate_live_probe_application_truth(snapshots, live_probes)
    return (
        tuple(snapshots),
        tuple(installation_loads),
        health,
        tuple(live_probes),
    )


def load_operation_qualification_manifest(
    payload: Mapping[str, object],
    *,
    expected_producer_operation_ids: Sequence[str],
    expected_operation_applications: Sequence[OperationOwner] | None = None,
) -> tuple[OperationQualificationEvidence, ...]:
    """Load reviewed semantics and exactly join a plan-derived denominator."""

    schema_version = payload.get("schema_version")
    if set(payload) != {"schema_version", "operations"} or schema_version not in {
        "1.0",
        "2.0",
    }:
        raise ValueError("operation qualification manifest is invalid")
    values = _sequence(payload.get("operations"), "qualification operations")
    evidence: list[OperationQualificationEvidence] = []
    expected_keys = {
        "producer_operation_id",
        "disposition",
        "temporal_semantics",
        "unit_semantics",
        "failure_semantics",
        "empty_result_semantics",
        "acquisition_role",
        "credential_policy",
        "rate_limit_policy",
        "offline_contract_selector",
        "offline_contract_result",
        "evidence_references",
        "gap_issues",
    }
    if schema_version == "2.0":
        expected_keys.add("capability_ids")
    for raw_value in values:
        value = _mapping(raw_value, "qualification operation")
        producer_operation_id = _text(
            value.get("producer_operation_id"),
            "qualification producer operation id",
        )
        requires_source_contract = (
            producer_operation_id in _MARKET_FOUNDATION_SOURCE_CAPABILITIES
        )
        if (
            set(value)
            not in {
                frozenset(expected_keys),
                frozenset((*expected_keys, "source_contract")),
            }
            or ("source_contract" in value) != requires_source_contract
        ):
            raise ValueError("operation qualification entry is invalid")
        capability_ids: tuple[str, ...] = ()
        if schema_version == "2.0":
            capability_ids = tuple(
                _safe_release_reference(item, "qualification capability id")
                for item in _sequence(
                    value.get("capability_ids"), "qualification capability ids"
                )
            )
            if (
                not capability_ids
                or tuple(sorted(set(capability_ids))) != capability_ids
            ):
                raise ValueError("qualification capability ids are invalid")
        disposition = value.get("disposition")
        if disposition not in {"integrated", "experimental", "pending_dependency"}:
            raise ValueError("operation qualification disposition is invalid")
        references = tuple(
            _safe_release_reference(item, "qualification evidence reference")
            for item in _sequence(
                value.get("evidence_references"),
                "qualification evidence references",
            )
        )
        if not references or len(references) != len(set(references)):
            raise ValueError("qualification evidence references are invalid")
        gap_issues = tuple(
            _strict_issue(item)
            for item in _sequence(value.get("gap_issues"), "qualification gaps")
        )
        evidence.append(
            OperationQualificationEvidence(
                producer_operation_id=producer_operation_id,
                disposition=cast(
                    Literal["integrated", "experimental", "pending_dependency"],
                    disposition,
                ),
                temporal_semantics=cast(
                    dict[str, str],
                    deepcopy(
                        dict(
                            _mapping(
                                value.get("temporal_semantics"), "temporal semantics"
                            )
                        )
                    ),
                ),
                unit_semantics=cast(
                    dict[str, str],
                    deepcopy(
                        dict(_mapping(value.get("unit_semantics"), "unit semantics"))
                    ),
                ),
                failure_semantics=_text(
                    value.get("failure_semantics"), "failure semantics"
                ),
                empty_result_semantics=_text(
                    value.get("empty_result_semantics"), "empty-result semantics"
                ),
                acquisition_role=_text(
                    value.get("acquisition_role"), "acquisition role"
                ),
                credential_policy=_text(
                    value.get("credential_policy"), "credential policy"
                ),
                rate_limit_policy=deepcopy(
                    dict(_mapping(value.get("rate_limit_policy"), "rate-limit policy"))
                ),
                offline_contract_selector=_safe_release_reference(
                    value.get("offline_contract_selector"),
                    "offline contract selector",
                ),
                offline_contract_result=_text(
                    value.get("offline_contract_result"),
                    "offline contract result",
                ),
                evidence_references=references,
                capability_ids=capability_ids,
                gap_issues=gap_issues,
                source_contract=(
                    deepcopy(
                        dict(
                            _validated_source_contract(
                                _mapping(
                                    value.get("source_contract"),
                                    "qualification source contract",
                                ),
                                producer_operation_id=producer_operation_id,
                                capability_ids=capability_ids,
                            )
                        )
                    )
                    if requires_source_contract
                    else None
                ),
            )
        )
    indexed = _unique_evidence(evidence, "qualification evidence")
    expected = {
        _text(item, "expected producer operation id")
        for item in expected_producer_operation_ids
    }
    if (
        len(expected) != len(expected_producer_operation_ids)
        or set(indexed) != expected
    ):
        raise ValueError("qualification evidence does not close the denominator")
    reviewed_applications = {
        (capability_id, item.producer_operation_id)
        for item in evidence
        for capability_id in item.capability_ids
    }
    if expected_operation_applications is not None:
        expected_applications = tuple(
            _operation_application(item, "expected operation application")
            for item in expected_operation_applications
        )
        if len(set(expected_applications)) != len(
            expected_applications
        ) or reviewed_applications != set(expected_applications):
            raise ValueError(
                "qualification application authority does not close the plan"
            )
    for item in evidence:
        _validate_qualification(item)
    return tuple(sorted(evidence, key=lambda item: item.producer_operation_id))


def load_active_operation_qualification_manifest(
    payload: Mapping[str, object],
) -> tuple[OperationQualificationEvidence, ...]:
    """Load installed authority and project only executable production owners."""

    manifest = _mapping(payload, "operation qualification manifest")
    manifest_producer_ids = tuple(
        _text(
            _mapping(item, "qualification operation").get("producer_operation_id"),
            "qualification producer operation id",
        )
        for item in _sequence(manifest.get("operations"), "qualification operations")
    )
    reviewed = load_operation_qualification_manifest(
        manifest,
        expected_producer_operation_ids=manifest_producer_ids,
    )
    return tuple(item for item in reviewed if item.disposition != "pending_dependency")


def load_market_foundation_qualification_fixtures(
    payload: Mapping[str, object],
    qualification_evidence: Sequence[OperationQualificationEvidence],
) -> tuple[Mapping[str, object], ...]:
    """Load the six installed deterministic source-contract fixtures."""

    if (
        set(payload) != {"schema_version", "fixtures"}
        or payload.get("schema_version") != "1.0"
    ):
        raise ValueError("market foundation qualification fixtures are invalid")
    qualifications = {
        item.producer_operation_id: item
        for item in qualification_evidence
        if item.source_contract is not None
    }
    if set(qualifications) != set(_MARKET_FOUNDATION_SOURCE_CAPABILITIES):
        raise ValueError("market foundation qualification owners do not close")
    fixtures: dict[str, Mapping[str, object]] = {}
    for raw_fixture in _sequence(payload.get("fixtures"), "qualification fixtures"):
        fixture = _mapping(raw_fixture, "qualification fixture")
        if set(fixture) != {
            "producer_operation_id",
            "capability_id",
            "complete",
            "observed_empty",
            "unavailable",
        }:
            raise ValueError("market foundation qualification fixture is invalid")
        operation_id = _safe_release_reference(
            fixture.get("producer_operation_id"), "qualification fixture owner"
        )
        capability_id = _safe_release_reference(
            fixture.get("capability_id"), "qualification fixture capability"
        )
        qualification = qualifications.get(operation_id)
        if (
            qualification is None
            or operation_id in fixtures
            or _MARKET_FOUNDATION_SOURCE_CAPABILITIES.get(operation_id) != capability_id
        ):
            raise ValueError("market foundation qualification fixture owner is invalid")
        contract = _mapping(
            qualification.source_contract, "qualification fixture source contract"
        )
        if contract.get("fixture_id") != operation_id:
            raise ValueError("market foundation qualification fixture is not bound")
        complete = _mapping(fixture.get("complete"), "qualification complete fixture")
        if (
            set(complete)
            != {
                "query_complete",
                "report_date",
                "published_at",
                "source_fields",
                "normalized_destinations",
            }
            or complete.get("query_complete") is not True
        ):
            raise ValueError("qualification complete fixture is invalid")
        _validate_market_foundation_fixture_time(complete)
        source_fields = _mapping(
            complete.get("source_fields"), "qualification fixture source fields"
        )
        normalized = _mapping(
            complete.get("normalized_destinations"),
            "qualification fixture normalized destinations",
        )
        destinations = _mapping(
            contract.get("field_destinations"), "qualification field destinations"
        )
        if (
            not source_fields
            or set(source_fields) != set(destinations)
            or set(normalized) != set(destinations.values())
        ):
            raise ValueError("qualification fixture field authority drifted")
        for values, label in (
            (source_fields, "fixture source field"),
            (normalized, "fixture normalized destination"),
        ):
            for key, value in values.items():
                _text(key, label)
                _text(value, label)
        _validate_market_foundation_fixture_normalization(
            operation_id=operation_id,
            contract=contract,
            source_fields=source_fields,
            normalized_destinations=normalized,
        )
        observed_empty = _mapping(
            fixture.get("observed_empty"), "qualification empty fixture"
        )
        rule = _MARKET_FOUNDATION_NORMALIZATION_RULES[operation_id]
        if capability_id == "whole_market_turnover":
            if (
                set(observed_empty)
                != {
                    "query_complete",
                    "row_count",
                    "state",
                    "report_date",
                    "published_at",
                    "source_fields",
                    "normalized_destinations",
                }
                or observed_empty.get("query_complete") is not True
                or observed_empty.get("row_count") != rule.observed_empty_row_count
                or observed_empty.get("state") != "observed_empty"
            ):
                raise ValueError("qualification empty fixture is invalid")
            _validate_market_foundation_fixture_time(observed_empty)
            empty_source_fields = _mapping(
                observed_empty.get("source_fields"),
                "qualification empty source fields",
            )
            empty_destinations = _mapping(
                observed_empty.get("normalized_destinations"),
                "qualification empty normalized destinations",
            )
            if (
                set(empty_source_fields) != set(rule.source_scale_exponents)
                or set(empty_destinations) != set(rule.field_destinations.values())
                or any(
                    not _exact_fixture_decimal(
                        item, "qualification empty source decimal"
                    ).is_zero()
                    for item in empty_source_fields.values()
                )
                or any(
                    not _exact_fixture_decimal(
                        item, "qualification empty normalized decimal"
                    ).is_zero()
                    for item in empty_destinations.values()
                )
            ):
                raise ValueError("qualification empty fixture is invalid")
            _validate_market_foundation_fixture_normalization(
                operation_id=operation_id,
                contract=contract,
                source_fields=empty_source_fields,
                normalized_destinations=empty_destinations,
            )
        else:
            if (
                set(observed_empty)
                != {
                    "query_complete",
                    "row_count",
                    "state",
                    "report_date",
                    "published_at",
                }
                or observed_empty.get("query_complete") is not True
                or observed_empty.get("row_count") != rule.observed_empty_row_count
                or observed_empty.get("state") != "observed_empty"
            ):
                raise ValueError("qualification empty fixture is invalid")
            _validate_market_foundation_fixture_time(observed_empty)
        unavailable = _mapping(
            fixture.get("unavailable"), "qualification unavailable fixture"
        )
        if (
            set(unavailable) != {"query_complete", "reason", "state"}
            or unavailable.get("query_complete") is not False
            or unavailable.get("state") != "unavailable"
            or unavailable.get("reason") != rule.unavailable_reason
        ):
            raise ValueError("qualification unavailable fixture is invalid")
        _safe_release_reference(
            unavailable.get("reason"), "qualification unavailable reason"
        )
        fixtures[operation_id] = deepcopy(dict(fixture))
    if set(fixtures) != set(qualifications):
        raise ValueError("market foundation qualification fixtures do not close")
    return tuple(fixtures[operation_id] for operation_id in sorted(fixtures))


def _validate_market_foundation_fixture_time(value: Mapping[str, object]) -> None:
    try:
        report_text = _text(value.get("report_date"), "fixture report date")
        published_text = _text(value.get("published_at"), "fixture publication time")
        if (
            _CANONICAL_DATE.fullmatch(report_text) is None
            or _CANONICAL_TIMESTAMP.fullmatch(published_text) is None
        ):
            raise ValueError("qualification fixture time is invalid")
        report_date = date.fromisoformat(report_text)
        published_at = datetime.fromisoformat(published_text)
        if (
            report_date.isoformat() != report_text
            or published_at.isoformat(timespec="seconds") != published_text
            or published_at.utcoffset() != timedelta(hours=8)
            or published_at.date() < report_date
        ):
            raise ValueError("qualification fixture time is invalid")
    except ValueError as error:
        raise ValueError("qualification fixture time is invalid") from error


def _validate_market_foundation_fixture_normalization(
    *,
    operation_id: str,
    contract: Mapping[str, object],
    source_fields: Mapping[str, object],
    normalized_destinations: Mapping[str, object],
) -> None:
    rule = _MARKET_FOUNDATION_NORMALIZATION_RULES.get(operation_id)
    if rule is None:
        raise ValueError("qualification fixture normalization owner is invalid")
    normalization = _mapping(
        contract.get("normalization"), "qualification fixture normalization contract"
    )
    destinations = _mapping(
        contract.get("field_destinations"), "qualification field destinations"
    )
    if (
        normalization != rule.contract
        or destinations != rule.field_destinations
        or set(rule.field_destinations) != set(rule.source_scale_exponents)
    ):
        raise ValueError("qualification fixture normalization authority drifted")

    terms_by_destination: dict[str, list[ExactDecimalTerm]] = {}
    for source_field, scale_exponent in rule.source_scale_exponents.items():
        destination = rule.field_destinations[source_field]
        terms_by_destination.setdefault(destination, []).append(
            ExactDecimalTerm(
                value=_exact_fixture_decimal(
                    source_fields.get(source_field),
                    "qualification fixture source decimal",
                ),
                scale_exponent=scale_exponent,
            )
        )

    try:
        for destination, terms in terms_by_destination.items():
            expected = exact_scaled_sum(tuple(terms))
            actual = exact_scaled_sum(
                (
                    ExactDecimalTerm(
                        value=_exact_fixture_decimal(
                            normalized_destinations.get(destination),
                            "qualification fixture normalization decimal",
                        ),
                        scale_exponent=0,
                    ),
                )
            )
            if actual.as_tuple() != expected.as_tuple():
                raise ValueError("qualification fixture normalization is invalid")
    except ExactNormalizationError as error:
        raise ValueError("qualification fixture normalization is invalid") from error


def _exact_fixture_decimal(value: object, label: str) -> Decimal:
    text = _text(value, label)
    if _CANONICAL_DECIMAL.fullmatch(text) is None:
        raise ValueError(f"{label} is invalid")
    try:
        decimal = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{label} is invalid") from error
    if not decimal.is_finite():
        raise ValueError(f"{label} is invalid")
    return decimal


def build_operation_release_matrix(
    *,
    plan_snapshots: Sequence[ReleasePlanSnapshot],
    qualification_evidence: Sequence[OperationQualificationEvidence],
    installation_loads: Sequence[InstallationLoadEvidence],
    installation_health: Mapping[str, object],
    live_probes: Sequence[LiveProbeEvidence],
    qualification_fixtures: Mapping[str, object] | None = None,
) -> OperationReleaseMatrix:
    """Build a closed matrix whose denominator comes only from public plans."""

    installation_health = _validated_installation_health(installation_health)

    producers: dict[str, dict[str, object]] = {}
    formal_applications: set[OperationOwner] = set()
    active_release_applications: dict[str, set[tuple[str, str, str, str]]] = {}
    snapshot_candidate_shas: set[str] = set()
    snapshot_attempt_digests: set[str] = set()
    snapshot_verified_at: list[str] = []
    for snapshot in plan_snapshots:
        _safe_release_reference(snapshot.request_profile, "request profile")
        snapshot_provenance = (
            snapshot.candidate_sha,
            snapshot.verified_at,
            snapshot.attempt_digest,
        )
        if any(value is not None for value in snapshot_provenance) and not all(
            value is not None for value in snapshot_provenance
        ):
            raise ValueError("release plan snapshot provenance is incomplete")
        if all(value is not None for value in snapshot_provenance):
            candidate_sha = cast(str, snapshot.candidate_sha)
            verified_at = cast(str, snapshot.verified_at)
            attempt_digest = cast(str, snapshot.attempt_digest)
            if (
                _CANDIDATE_SHA.fullmatch(candidate_sha) is None
                or _ATTEMPT_DIGEST.fullmatch(attempt_digest) is None
            ):
                raise ValueError("release plan snapshot provenance is invalid")
            _verified_at(verified_at)
            snapshot_candidate_shas.add(candidate_sha)
            snapshot_attempt_digests.add(attempt_digest)
            snapshot_verified_at.append(verified_at)
        for plan, coverage, workflow_use in _release_plan_views(snapshot.result):
            validate_plan_operation_producer_authority(plan)
            obligations = _sequence(
                plan.get("operation_obligations"),
                "release plan operation obligations",
            )
            for value in obligations:
                obligation = _mapping(value, "release plan operation obligation")
                application_owner = _owner(obligation)
                producer_operation_id = _safe_release_reference(
                    obligation.get("producer_operation_id"),
                    "formal producer operation id",
                )
                operation_kind = _text(
                    obligation.get("operation_kind"), "release operation kind"
                )
                if operation_kind == "deterministic_coordinator":
                    continue
                if operation_kind != "external_source":
                    raise ValueError("release operation kind is unsupported")
                formal_applications.add((application_owner[0], producer_operation_id))
                capability = _mapping(
                    coverage.get(application_owner[0]), "release capability coverage"
                )
                operations = _mapping(
                    capability.get("operations"), "release operation coverage"
                )
                operation = _mapping(
                    operations.get(application_owner[1]),
                    "release operation disposition",
                )
                if _owner(operation) != application_owner:
                    raise ValueError("release operation owner does not match its plan")
                fields = _sequence(
                    operation.get("field_dispositions"),
                    "release operation field dispositions",
                )
                if not fields:
                    raise ValueError("formal operation field dispositions are missing")
                for field in fields:
                    _validate_field_disposition(field, application_owner)
                application = {
                    "capability_id": application_owner[0],
                    "operation_id": application_owner[1],
                    "plan_id": _text(plan.get("id"), "release plan id"),
                    "plan_version": _text(plan.get("version"), "release plan version"),
                    "task_type": _text(plan.get("task_type"), "release task type"),
                    "request_profile": _text(
                        snapshot.request_profile, "release request profile"
                    ),
                    "execution_state": _text(
                        operation.get("execution_state"), "release execution state"
                    ),
                    "source_role": obligation.get("source_role"),
                    "experimental": obligation.get("experimental"),
                    "exchange": obligation.get("exchange"),
                    "evidence_uses": deepcopy(obligation.get("evidence_uses")),
                    "field_dispositions": deepcopy(list(fields)),
                    **(
                        {"workflow_use": deepcopy(dict(workflow_use))}
                        if workflow_use is not None
                        else {}
                    ),
                }
                if operation.get("execution_state") in {"succeeded", "failed"} and all(
                    value is not None for value in snapshot_provenance
                ):
                    active_release_applications.setdefault(
                        producer_operation_id, set()
                    ).add(
                        (
                            cast(str, snapshot.candidate_sha),
                            cast(str, snapshot.verified_at),
                            cast(str, snapshot.attempt_digest),
                            snapshot.request_profile,
                        )
                    )
                existing = producers.get(producer_operation_id)
                if existing is None:
                    producers[producer_operation_id] = {
                        "applications": [application],
                    }
                else:
                    applications = cast(
                        list[dict[str, object]], existing["applications"]
                    )
                    matching = [
                        item
                        for item in applications
                        if _application_identity_key(item)
                        == _application_identity_key(application)
                    ]
                    if matching:
                        if matching != [application]:
                            raise ValueError(
                                "formal operation application contract drifted"
                            )
                    else:
                        applications.append(application)

    if not producers:
        raise ValueError("release matrix requires formal plan operations")
    for item in qualification_evidence:
        _validate_qualification(item)
        if not item.capability_ids:
            raise ValueError("qualification capability ids are invalid")
    market_foundation_producers = set(producers) & set(
        _MARKET_FOUNDATION_SOURCE_CAPABILITIES
    )
    if market_foundation_producers:
        if qualification_fixtures is None:
            raise ValueError("market foundation normalization fixtures are required")
        load_market_foundation_qualification_fixtures(
            qualification_fixtures,
            qualification_evidence,
        )
        if (
            _canonical_mapping_sha256(
                qualification_fixtures,
                "market foundation fixture authority",
            )
            != _MARKET_FOUNDATION_FIXTURE_SHA256
        ):
            raise ValueError("market foundation fixture authority drifted")
    elif qualification_fixtures is not None:
        raise ValueError("market foundation normalization fixtures are unplanned")
    qualification_by_producer = _unique_evidence(
        qualification_evidence, "qualification evidence"
    )
    install_by_producer = _unique_evidence(installation_loads, "installation load")
    live_by_owner = _unique_evidence(live_probes, "live probe")
    reviewed_applications = {
        (capability_id, item.producer_operation_id)
        for item in qualification_evidence
        for capability_id in item.capability_ids
    }
    if reviewed_applications != formal_applications:
        raise ValueError("qualification application authority does not close the plan")
    if set(qualification_by_producer) != set(producers):
        raise ValueError("qualification evidence does not close the denominator")
    if set(install_by_producer) != set(producers):
        raise ValueError("installation load evidence does not close the denominator")
    if set(live_by_owner) != set(producers):
        raise ValueError("live probe evidence does not close the denominator")
    _validate_live_probe_application_truth(plan_snapshots, live_probes)
    provenance_fields = [
        (
            item.candidate_sha,
            item.request_profile,
            item.attempt_digest,
        )
        for item in live_probes
    ]
    has_provenance = [
        any(value is not None for value in fields) for fields in provenance_fields
    ]
    if any(has_provenance) and not all(
        all(value is not None for value in fields) for fields in provenance_fields
    ):
        raise ValueError("live probe provenance is incomplete")
    release_evidence: dict[str, object] | None = None
    if all(has_provenance):
        candidate_shas = {cast(str, item.candidate_sha) for item in live_probes}
        live_attempt_digests = {cast(str, item.attempt_digest) for item in live_probes}
        if (
            len(candidate_shas) != 1
            or any(_CANDIDATE_SHA.fullmatch(item) is None for item in candidate_shas)
            or any(
                _ATTEMPT_DIGEST.fullmatch(item) is None for item in live_attempt_digests
            )
            or candidate_shas != snapshot_candidate_shas
            or not live_attempt_digests <= snapshot_attempt_digests
        ):
            raise ValueError("live probe provenance is invalid")
        verified_values = sorted(
            snapshot_verified_at,
            key=datetime.fromisoformat,
        )
        release_evidence = {
            "candidate_sha": candidate_shas.pop(),
            "attempt_digests": sorted(snapshot_attempt_digests),
            "verified_from": verified_values[0],
            "verified_through": verified_values[-1],
        }
    elif snapshot_attempt_digests:
        raise ValueError("release plan provenance lacks live probe provenance")

    install_operations = _install_operations(installation_health)
    dependencies = _install_dependencies(installation_health)
    health_operation_ids = set(install_operations)
    declared_health_ids = {
        item.producer_operation_id
        for item in installation_loads
        if item.origin == "installation_health"
    }
    dependency_operation_ids = {
        _text(operation_id, "dependency operation id")
        for item in dependencies
        for operation_id in cast(Sequence[object], item["operation_ids"])
    }
    if (
        installation_health.get("status") != "complete"
        or health_operation_ids != declared_health_ids
        or dependency_operation_ids != health_operation_ids
    ):
        raise ValueError(
            "installation health evidence does not exactly join its owners"
        )

    entries: list[dict[str, object]] = []
    active_field_denominator = 0
    active_field_numerator = 0
    for producer_operation_id in sorted(producers):
        facts = producers[producer_operation_id]
        qualification = cast(
            OperationQualificationEvidence,
            qualification_by_producer[producer_operation_id],
        )
        install = cast(
            InstallationLoadEvidence, install_by_producer[producer_operation_id]
        )
        live = cast(LiveProbeEvidence, live_by_owner[producer_operation_id])
        _validate_qualification(qualification)
        if qualification.offline_contract_result != "passed":
            raise ValueError("offline contract evidence must pass")
        if live.probe_status != "passed":
            raise ValueError("live probe harness evidence must pass")
        pending_dependency = qualification.disposition == "pending_dependency"
        if pending_dependency and (
            live.attempted or live.source_result != "unavailable"
        ):
            raise ValueError(
                "pending qualification cannot claim attempted source evidence"
            )
        if not pending_dependency and not live.attempted:
            raise ValueError("live probe source must be attempted")
        _safe_release_reference(live.probe, "live probe")
        if live.source_result not in _RELEASE_SOURCE_RESULTS:
            raise ValueError("live probe source result is unsupported")
        _verified_at(live.verified_at)
        _safe_release_reference(install.selector, "installation selector")
        if install.status != "complete":
            raise ValueError("default-install load evidence is incomplete")
        if install.origin == "installation_health":
            health_fact = install_operations.get(producer_operation_id)
            if health_fact is None or health_fact.get("status") != install.status:
                raise ValueError("installation health operation evidence drifted")
        elif producer_operation_id in health_operation_ids:
            raise ValueError("health-owned operation used copied-install evidence")
        else:
            health_fact = None
        dependency_facts = [
            deepcopy(dict(item))
            for item in dependencies
            if producer_operation_id in cast(Sequence[object], item["operation_ids"])
        ]
        applications = sorted(
            cast(list[dict[str, object]], facts["applications"]),
            key=_application_sort_key,
        )
        if live.request_profile is not None and live.request_profile not in {
            cast(str, item["request_profile"]) for item in applications
        }:
            raise ValueError("live probe request profile is not a plan application")
        if live.candidate_sha is not None and (
            live.candidate_sha,
            live.verified_at,
            live.attempt_digest,
            live.request_profile,
        ) not in active_release_applications.get(producer_operation_id, set()):
            raise ValueError("live probe is not bound to an active plan application")
        fields = sorted(
            {
                _text(
                    _mapping(field, "field disposition").get("field"),
                    "field disposition field",
                )
                for application in applications
                for field in _sequence(
                    application.get("field_dispositions"),
                    "application field dispositions",
                )
            }
        )
        applicability = {
            "capability_ids": sorted(
                {
                    _text(item.get("capability_id"), "application capability")
                    for item in applications
                }
            ),
            "exchanges": sorted(
                {
                    cast(str, item["exchange"])
                    for item in applications
                    if isinstance(item.get("exchange"), str)
                    and cast(str, item["exchange"]).strip()
                }
            ),
            "plan_ids": sorted(
                {
                    _text(item.get("plan_id"), "application plan")
                    for item in applications
                }
            ),
            "plan_versions": sorted(
                {
                    _text(item.get("plan_version"), "application plan version")
                    for item in applications
                }
            ),
            "request_profiles": sorted(
                {
                    _text(item.get("request_profile"), "application request profile")
                    for item in applications
                }
            ),
            "task_types": sorted(
                {
                    _text(item.get("task_type"), "application task type")
                    for item in applications
                }
            ),
            "workflow_ids": sorted(
                {
                    _text(
                        _mapping(
                            item.get("workflow_use"), "application workflow use"
                        ).get("workflow_id"),
                        "application workflow id",
                    )
                    for item in applications
                    if item.get("workflow_use") is not None
                }
            ),
        }
        producer_is_active = any(
            item.get("execution_state") in {"succeeded", "failed"}
            for item in applications
        )
        if live.attempted != producer_is_active:
            raise ValueError(
                "live probe attempted fact contradicts application execution"
            )
        active_contracts = {
            json.dumps(
                application["field_dispositions"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ): cast(list[object], application["field_dispositions"])
            for application in applications
            if application.get("execution_state") in {"succeeded", "failed"}
        }
        active_field_denominator += sum(
            len(contract) for contract in active_contracts.values()
        )
        active_field_numerator += sum(
            _valid_field_disposition(field)
            for contract in active_contracts.values()
            for field in contract
        )
        entries.append(
            {
                "producer_operation_id": producer_operation_id,
                "applications": deepcopy(applications),
                "applicability": applicability,
                "fields": fields,
                "disposition": qualification.disposition,
                "temporal_semantics": deepcopy(dict(qualification.temporal_semantics)),
                "unit_semantics": deepcopy(dict(qualification.unit_semantics)),
                "failure_semantics": qualification.failure_semantics,
                "empty_result_semantics": qualification.empty_result_semantics,
                "acquisition_role": qualification.acquisition_role,
                "credential_policy": qualification.credential_policy,
                "rate_limit_policy": deepcopy(dict(qualification.rate_limit_policy)),
                "gap_issues": list(qualification.gap_issues),
                "dependency_facts": dependency_facts,
                "install_load": {
                    "origin": install.origin,
                    "status": install.status,
                    "selector": install.selector,
                    **(
                        {"health_fact": deepcopy(dict(health_fact))}
                        if health_fact is not None
                        else {}
                    ),
                },
                "offline_contract": {
                    "selector": qualification.offline_contract_selector,
                    "result": qualification.offline_contract_result,
                },
                "evidence_references": list(qualification.evidence_references),
                **(
                    {"source_contract": deepcopy(dict(qualification.source_contract))}
                    if qualification.source_contract is not None
                    else {}
                ),
                "live_probe": {
                    "probe": live.probe,
                    "probe_status": live.probe_status,
                    "attempted": live.attempted,
                    "source_result": live.source_result,
                    **(
                        {
                            "candidate_sha": live.candidate_sha,
                            "request_profile": live.request_profile,
                            "attempt_digest": live.attempt_digest,
                        }
                        if live.candidate_sha is not None
                        and live.request_profile is not None
                        and live.attempt_digest is not None
                        else {}
                    ),
                },
                "verified_at": live.verified_at,
            }
        )

    if active_field_denominator == 0:
        raise ValueError("release snapshots contain no active-request fields")

    owner_payload = [
        {"producer_operation_id": producer_operation_id}
        for producer_operation_id in sorted(producers)
    ]
    owner_digest = sha256(
        json.dumps(
            owner_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    return OperationReleaseMatrix(
        {
            "schema_version": "1.0",
            **(
                {"release_evidence": release_evidence}
                if release_evidence is not None
                else {}
            ),
            "denominator": {
                "authority": "public_versioned_plan_operation_obligations",
                "operation_count": len(entries),
                "operation_owner_sha256": owner_digest,
            },
            "summary": {
                "formal_operation_disposition_rate": _rate(
                    len(qualification_by_producer), len(producers)
                ),
                "active_request_field_destination_rate": _rate(
                    active_field_numerator, active_field_denominator
                ),
                "default_install_load_rate": _rate(
                    sum(item.status == "complete" for item in installation_loads),
                    len(producers),
                ),
            },
            "operations": entries,
        }
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys are invalid")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} is invalid")
    return value


def _text(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} is invalid")
    return value


def _owner(value: Mapping[str, object]) -> OperationOwner:
    return (
        _safe_release_reference(value.get("capability_id"), "release capability id"),
        _safe_release_reference(value.get("operation_id"), "release operation id"),
    )


def _release_plan_views(
    result: Mapping[str, object],
) -> tuple[
    tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object] | None],
    ...,
]:
    """Expose direct plans or serializer-validated workflow leaf plans."""

    if result.get("schema_version") != "1.0":
        raise ValueError("release result schema is unsupported")
    plan = _mapping(result.get("plan"), "release plan")
    coverage = _mapping(result.get("coverage"), "release coverage")
    if plan.get("task_type") != "research_workflow":
        _safe_release_reference(plan.get("id"), "release plan id")
        _safe_release_reference(plan.get("version"), "release plan version")
        _safe_release_reference(plan.get("task_type"), "release task type")
        return ((plan, coverage, None),)

    workflow_plan_id = _safe_release_reference(
        plan.get("id"), "release workflow plan id"
    )
    workflow_plan_version = _safe_release_reference(
        plan.get("version"), "release workflow plan version"
    )
    workflow_id = _safe_release_reference(
        plan.get("workflow_id"), "release workflow id"
    )
    plan_steps = _sequence(plan.get("steps"), "release workflow plan steps")
    registry = _mapping(
        result.get("capability_registry"), "release workflow capability registry"
    )
    registry_steps = _mapping(registry.get("steps"), "release workflow registry steps")
    coverage_steps = _mapping(coverage.get("steps"), "release workflow coverage steps")
    step_contracts: dict[str, Mapping[str, object]] = {}
    for value in plan_steps:
        contract = _mapping(value, "release workflow plan step")
        step_id = _safe_release_reference(
            contract.get("step_id"), "release workflow step id"
        )
        if step_id in step_contracts:
            raise ValueError("release workflow step is duplicated")
        step_contracts[step_id] = contract
    if set(step_contracts) != set(registry_steps) or set(step_contracts) != set(
        coverage_steps
    ):
        raise ValueError("release workflow leaf lineage does not close")

    views: list[
        tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]]
    ] = []
    for step_id in sorted(step_contracts):
        contract = step_contracts[step_id]
        registered = _mapping(
            registry_steps.get(step_id), "release workflow registered step"
        )
        covered = _mapping(coverage_steps.get(step_id), "release workflow covered step")
        step_type = _safe_release_reference(
            contract.get("task_type"), "release workflow step task"
        )
        step_version = _safe_release_reference(
            contract.get("version"), "release workflow step version"
        )
        if (
            registered.get("step_id") != step_id
            or registered.get("task_type") != step_type
            or registered.get("version") != step_version
        ):
            raise ValueError("release workflow registered step drifted")
        leaf_plan_value = registered.get("leaf_plan")
        leaf_coverage_value = covered.get("leaf")
        if leaf_plan_value is None:
            if leaf_coverage_value != {}:
                raise ValueError("release workflow skipped leaf has coverage")
            continue
        leaf_plan = _mapping(leaf_plan_value, "release workflow leaf plan")
        leaf_coverage = _mapping(leaf_coverage_value, "release workflow leaf coverage")
        if (
            leaf_plan.get("task_type") != step_type
            or leaf_plan.get("version") != step_version
        ):
            raise ValueError("release workflow leaf plan drifted from its step")
        views.append(
            (
                leaf_plan,
                leaf_coverage,
                {
                    "plan_id": workflow_plan_id,
                    "plan_version": workflow_plan_version,
                    "workflow_id": workflow_id,
                    "step_id": step_id,
                    "step_version": step_version,
                },
            )
        )
    return tuple(views)


def _validate_sanitized_release_result(result: Mapping[str, object]) -> None:
    if result.get("schema_version") != "1.0":
        raise ValueError("release result schema is unsupported")
    plan = _mapping(result.get("plan"), "release plan")
    task_type = plan.get("task_type")
    if task_type == "research_workflow":
        if set(result) != {"schema_version", "plan", "capability_registry", "coverage"}:
            raise ValueError("sanitized workflow release result is invalid")
        if set(plan) != {"id", "version", "task_type", "workflow_id", "steps"}:
            raise ValueError("sanitized workflow plan is invalid")
        for key, label in (
            ("id", "workflow plan id"),
            ("version", "workflow plan version"),
            ("task_type", "workflow task type"),
            ("workflow_id", "workflow id"),
        ):
            _safe_release_reference(plan.get(key), label)
        registry = _mapping(result.get("capability_registry"), "workflow registry")
        coverage = _mapping(result.get("coverage"), "workflow coverage")
        if set(registry) != {"steps"} or set(coverage) != {"steps"}:
            raise ValueError("sanitized workflow lineage is invalid")
        registry_steps = _mapping(registry.get("steps"), "workflow registry steps")
        coverage_steps = _mapping(coverage.get("steps"), "workflow coverage steps")
        plan_steps = _sequence(plan.get("steps"), "workflow plan steps")
        plan_step_ids: set[str] = set()
        for raw_step in plan_steps:
            step = _mapping(raw_step, "workflow plan step")
            if set(step) != {
                "step_id",
                "version",
                "task_type",
                "criticality",
                "depends_on",
            }:
                raise ValueError("sanitized workflow plan step is invalid")
            step_id = _safe_release_reference(step.get("step_id"), "workflow step id")
            if step_id in plan_step_ids:
                raise ValueError("sanitized workflow plan step is duplicated")
            plan_step_ids.add(step_id)
            _safe_release_reference(step.get("version"), "workflow step version")
            _safe_release_reference(step.get("task_type"), "workflow step task type")
            _safe_release_reference(
                step.get("criticality"), "workflow step criticality"
            )
            dependencies = [
                _safe_release_reference(item, "workflow step dependency")
                for item in _sequence(
                    step.get("depends_on"), "workflow step dependencies"
                )
            ]
            if len(dependencies) != len(set(dependencies)):
                raise ValueError("sanitized workflow step dependencies are invalid")
        if plan_step_ids != set(registry_steps) or plan_step_ids != set(coverage_steps):
            raise ValueError("sanitized workflow lineage does not close")
        for step_id, raw_registered in registry_steps.items():
            registered = _mapping(raw_registered, "workflow registered step")
            if set(registered) != {"step_id", "task_type", "version", "leaf_plan"}:
                raise ValueError("sanitized workflow registered step is invalid")
            covered = _mapping(coverage_steps.get(step_id), "workflow covered step")
            if set(covered) != {"leaf"}:
                raise ValueError("sanitized workflow covered step is invalid")
            leaf_plan = registered.get("leaf_plan")
            leaf_coverage = covered.get("leaf")
            if leaf_plan is None:
                if leaf_coverage != {}:
                    raise ValueError("sanitized skipped workflow leaf is invalid")
            else:
                _validate_sanitized_direct_contract(
                    _mapping(leaf_plan, "workflow leaf plan"),
                    _mapping(leaf_coverage, "workflow leaf coverage"),
                )
        _release_plan_views(result)
        return
    if set(result) != {"schema_version", "plan", "coverage"}:
        raise ValueError("sanitized direct release result is invalid")
    _validate_sanitized_direct_contract(
        plan, _mapping(result.get("coverage"), "release coverage")
    )


def _validate_sanitized_direct_contract(
    plan: Mapping[str, object], coverage: Mapping[str, object]
) -> None:
    if set(plan) != {
        "id",
        "version",
        "task_type",
        "capabilities",
        "operation_obligations",
        "research_dependencies",
    }:
        raise ValueError("sanitized direct release plan is invalid")
    _safe_release_reference(plan.get("id"), "release plan id")
    _safe_release_reference(plan.get("version"), "release plan version")
    _safe_release_reference(plan.get("task_type"), "release task type")
    capabilities = [
        _safe_release_reference(item, "release plan capability")
        for item in _sequence(plan.get("capabilities"), "release plan capabilities")
    ]
    if (
        not capabilities
        or len(capabilities) != len(set(capabilities))
        or set(capabilities) != set(coverage)
    ):
        raise ValueError("sanitized release plan capabilities are invalid")
    owners: set[OperationOwner] = set()
    for raw_obligation in _sequence(
        plan.get("operation_obligations"), "release plan obligations"
    ):
        obligation = _mapping(raw_obligation, "release plan obligation")
        if set(obligation) != {
            "capability_id",
            "operation_id",
            "producer_operation_id",
            "operation_kind",
            "source_role",
            "experimental",
            "exchange",
            "evidence_uses",
        }:
            raise ValueError("sanitized release plan obligation is invalid")
        owner = _owner(obligation)
        if owner in owners or owner[0] not in capabilities:
            raise ValueError("sanitized release plan obligation owner is invalid")
        owners.add(owner)
        _safe_release_reference(
            obligation.get("producer_operation_id"), "formal producer operation id"
        )
        if obligation.get("operation_kind") not in {
            "external_source",
            "deterministic_coordinator",
        }:
            raise ValueError("sanitized release operation kind is invalid")
        _safe_release_reference(obligation.get("source_role"), "release source role")
        if not isinstance(obligation.get("experimental"), bool) or obligation.get(
            "exchange"
        ) not in {None, "SSE", "SZSE"}:
            raise ValueError("sanitized release plan obligation is invalid")
        for raw_use in _sequence(
            obligation.get("evidence_uses"), "release obligation evidence uses"
        ):
            evidence_use = _mapping(raw_use, "release obligation evidence use")
            if set(evidence_use) != {"use", "role"}:
                raise ValueError("sanitized release evidence use is invalid")
            _safe_release_reference(evidence_use.get("use"), "release evidence use")
            _safe_release_reference(evidence_use.get("role"), "release evidence role")
    if not owners:
        raise ValueError("sanitized release plan obligations are missing")
    validate_plan_operation_producer_authority(plan)
    for raw_dependency in _sequence(
        plan.get("research_dependencies"), "release plan dependencies"
    ):
        dependency = _mapping(raw_dependency, "release plan dependency")
        if set(dependency) != {
            "consumer_id",
            "evidence_use",
            "minimum_supporting_observations",
            "core",
        }:
            raise ValueError("sanitized release plan dependency is invalid")
        _safe_release_reference(
            dependency.get("consumer_id"), "release dependency consumer"
        )
        _safe_release_reference(
            dependency.get("evidence_use"), "release dependency evidence use"
        )
        minimum = dependency.get("minimum_supporting_observations")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
            or not isinstance(dependency.get("core"), bool)
        ):
            raise ValueError("sanitized release plan dependency is invalid")
    covered_owners: set[OperationOwner] = set()
    for capability_id, raw_capability in coverage.items():
        capability = _mapping(raw_capability, "release capability")
        if set(capability) != {"operations"}:
            raise ValueError("sanitized release capability is invalid")
        operations = _mapping(capability.get("operations"), "release operations")
        for operation_id, raw_operation in operations.items():
            operation = _mapping(raw_operation, "release operation")
            if set(operation) != {
                "capability_id",
                "operation_id",
                "execution_state",
                "state",
                "query_complete",
                "source_error_codes",
                "field_dispositions",
            }:
                raise ValueError("sanitized release operation is invalid")
            if _owner(operation) != (capability_id, operation_id):
                raise ValueError("sanitized release operation owner is invalid")
            covered_owners.add((capability_id, operation_id))
            _text(operation.get("execution_state"), "release execution state")
            _text(operation.get("state"), "release coverage state")
            if operation.get("query_complete") not in {None, True, False}:
                raise ValueError("sanitized release query completeness is invalid")
            derive_release_source_result((operation,))
            fields = _sequence(
                operation.get("field_dispositions"), "release field dispositions"
            )
            if not fields:
                raise ValueError("release field dispositions are missing")
            for field in fields:
                _validate_field_disposition(field, (capability_id, operation_id))
    if covered_owners != owners:
        raise ValueError("sanitized release operation coverage does not close")


def _validate_field_disposition(value: object, owner: OperationOwner) -> None:
    field = _mapping(value, "field disposition")
    disposition = field.get("disposition")
    expected = {"field", "disposition", "destination"}
    if disposition == "excluded":
        expected.add("reason")
    if set(field) != expected or not _valid_field_disposition(field):
        raise ValueError("field disposition is invalid")
    _safe_release_contract_text(field.get("field"), "field disposition field")
    if disposition == "excluded":
        _safe_release_contract_text(field.get("reason"), "field disposition reason")
    destination = field.get("destination")
    if isinstance(destination, str):
        validate_field_disposition_destination_contract(destination, owner)


def _validate_live_probe_application_truth(
    snapshots: Sequence[ReleasePlanSnapshot],
    live_probes: Sequence[LiveProbeEvidence],
) -> None:
    for live in live_probes:
        operations: list[Mapping[str, object]] = []
        for snapshot in snapshots:
            if live.request_profile is not None and (
                snapshot.request_profile != live.request_profile
            ):
                continue
            if live.candidate_sha is not None and (
                snapshot.candidate_sha,
                snapshot.verified_at,
                snapshot.attempt_digest,
            ) != (
                live.candidate_sha,
                live.verified_at,
                live.attempt_digest,
            ):
                continue
            for plan, coverage, _ in _release_plan_views(snapshot.result):
                for raw_obligation in _sequence(
                    plan.get("operation_obligations"), "release plan obligations"
                ):
                    obligation = _mapping(raw_obligation, "release plan obligation")
                    if (
                        obligation.get("operation_kind") != "external_source"
                        or obligation.get("producer_operation_id")
                        != live.producer_operation_id
                    ):
                        continue
                    owner = _owner(obligation)
                    capability = _mapping(
                        coverage.get(owner[0]), "release capability coverage"
                    )
                    operation_map = _mapping(
                        capability.get("operations"), "release operation coverage"
                    )
                    operation = _mapping(
                        operation_map.get(owner[1]), "release operation"
                    )
                    if _owner(operation) != owner:
                        raise ValueError(
                            "release operation owner does not match its plan"
                        )
                    operations.append(operation)
        if not operations:
            raise ValueError("live probe request profile is not a plan application")
        attempted = any(
            operation.get("execution_state") in {"succeeded", "failed"}
            for operation in operations
        )
        if live.attempted != attempted:
            raise ValueError(
                "live probe attempted fact contradicts application execution"
            )
        expected_result = derive_release_source_result(operations)
        if live.source_result != expected_result:
            raise ValueError(
                "live probe source result contradicts application execution"
            )


def canonical_release_source_error_codes(value: object) -> tuple[str, ...]:
    """Retain only the finite credential-class facts safe to seal."""

    codes = {
        item
        for item in _sequence(value, "release source error codes")
        if isinstance(item, str) and item in _CREDENTIAL_FAILURE_CODES
    }
    return tuple(sorted(codes))


def derive_release_source_result(
    operations: Sequence[Mapping[str, object]],
) -> str:
    """Derive one exact result from sanitized public applications."""

    if not operations:
        raise ValueError("release operation applications are missing")
    observed = {_operation_source_result(operation) for operation in operations}
    for result in _SOURCE_RESULT_PRECEDENCE:
        if result in observed:
            return result
    raise ValueError("release operation source results are invalid")


def _operation_source_result(operation: Mapping[str, object]) -> str:
    raw_error_codes = _sequence(
        operation.get("source_error_codes"), "release source error codes"
    )
    source_error_codes = canonical_release_source_error_codes(raw_error_codes)
    if tuple(raw_error_codes) != source_error_codes:
        raise ValueError("release source error codes are not canonical")
    execution_state = _text(operation.get("execution_state"), "release execution state")
    if execution_state != "failed" and source_error_codes:
        raise ValueError("release source error codes require failed execution")
    coverage_state = _text(operation.get("state"), "release coverage state")
    query_complete = operation.get("query_complete")
    if execution_state == "succeeded":
        if query_complete is True and coverage_state in {"complete", "observed_empty"}:
            return coverage_state
        if query_complete is False and coverage_state == "partial":
            return "partial"
    elif execution_state == "failed":
        if query_complete is None and coverage_state == "partial":
            return "partial"
        if query_complete is None and coverage_state == "failed":
            if source_error_codes:
                return "credential_blocked"
            return "unavailable"
    elif execution_state in {
        "not_requested",
        "skipped_policy",
        "skipped_research_dependency",
    }:
        if query_complete is None and coverage_state == execution_state:
            return "unavailable"
    raise ValueError("release operation execution facts are invalid")


def _unique_evidence(
    values: Sequence[OperationQualificationEvidence]
    | Sequence[InstallationLoadEvidence]
    | Sequence[LiveProbeEvidence],
    label: str,
) -> dict[
    str,
    OperationQualificationEvidence | InstallationLoadEvidence | LiveProbeEvidence,
]:
    indexed: dict[
        str,
        OperationQualificationEvidence | InstallationLoadEvidence | LiveProbeEvidence,
    ] = {}
    for value in values:
        producer_operation_id = _text(
            value.producer_operation_id, f"{label} producer operation id"
        )
        if producer_operation_id in indexed:
            raise ValueError(f"{label} owner is duplicated")
        indexed[producer_operation_id] = value
    return indexed


def _validate_qualification(value: OperationQualificationEvidence) -> None:
    if not isinstance(value.capability_ids, tuple):
        raise ValueError("qualification capability ids are invalid")
    if value.capability_ids:
        canonical_capability_ids = tuple(
            _safe_release_reference(item, "qualification capability id")
            for item in value.capability_ids
        )
        if tuple(sorted(set(canonical_capability_ids))) != canonical_capability_ids:
            raise ValueError("qualification capability ids are invalid")
    requires_source_contract = (
        value.producer_operation_id in _MARKET_FOUNDATION_SOURCE_CAPABILITIES
    )
    if (value.source_contract is not None) != requires_source_contract:
        raise ValueError("qualification source contract ownership is invalid")
    if value.disposition not in {
        "integrated",
        "experimental",
        "pending_dependency",
    }:
        raise ValueError("formal operation disposition is unsupported")
    for semantics, label in (
        (value.temporal_semantics, "temporal semantics"),
        (value.unit_semantics, "unit semantics"),
    ):
        if not semantics or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(item, str)
            or not item.strip()
            for key, item in semantics.items()
        ):
            raise ValueError(f"operation {label} are invalid")
    for fact, label in (
        (value.failure_semantics, "failure semantics"),
        (value.empty_result_semantics, "empty-result semantics"),
        (value.acquisition_role, "acquisition role"),
        (value.credential_policy, "credential policy"),
    ):
        _text(fact, f"operation {label}")
    _safe_release_reference(
        value.offline_contract_selector, "offline contract selector"
    )
    if not value.evidence_references or len(value.evidence_references) != len(
        set(value.evidence_references)
    ):
        raise ValueError("qualification evidence references are invalid")
    for reference in value.evidence_references:
        _safe_release_reference(reference, "qualification evidence reference")
    if not value.rate_limit_policy:
        raise ValueError("operation rate-limit policy is invalid")
    _safe_release_mapping(value.rate_limit_policy, "operation rate-limit policy")
    if value.source_contract is not None:
        _validated_source_contract(
            value.source_contract,
            producer_operation_id=value.producer_operation_id,
            capability_ids=value.capability_ids,
        )
    if len(value.gap_issues) != len(set(value.gap_issues)) or any(
        issue not in {58, 59, 61, 62, 63, 64} for issue in value.gap_issues
    ):
        raise ValueError("operation gap routing is invalid")
    if requires_source_contract:
        _validate_market_foundation_qualification_authority(value)


def _validate_market_foundation_qualification_authority(
    value: OperationQualificationEvidence,
) -> None:
    source_contract = value.source_contract
    if source_contract is None:
        raise ValueError("qualification source contract ownership is invalid")
    payload = {
        "producer_operation_id": value.producer_operation_id,
        "capability_ids": list(value.capability_ids),
        "disposition": value.disposition,
        "temporal_semantics": deepcopy(dict(value.temporal_semantics)),
        "unit_semantics": deepcopy(dict(value.unit_semantics)),
        "failure_semantics": value.failure_semantics,
        "empty_result_semantics": value.empty_result_semantics,
        "acquisition_role": value.acquisition_role,
        "credential_policy": value.credential_policy,
        "rate_limit_policy": deepcopy(dict(value.rate_limit_policy)),
        "offline_contract_selector": value.offline_contract_selector,
        "offline_contract_result": value.offline_contract_result,
        "evidence_references": list(value.evidence_references),
        "gap_issues": list(value.gap_issues),
        "source_contract": deepcopy(dict(source_contract)),
    }
    if (
        _canonical_mapping_sha256(payload, "qualification authority")
        != (_MARKET_FOUNDATION_QUALIFICATION_SHA256[value.producer_operation_id])
    ):
        raise ValueError("qualification authority drifted")


def _canonical_mapping_sha256(value: Mapping[str, object], label: str) -> str:
    try:
        canonical_bytes = json.dumps(
            deepcopy(dict(value)),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is invalid") from error
    return sha256(canonical_bytes).hexdigest()


def _validated_source_contract(
    value: Mapping[str, object],
    *,
    producer_operation_id: str,
    capability_ids: tuple[str, ...],
) -> Mapping[str, object]:
    """Validate one installed, first-party operation source contract."""

    if set(value) != _SOURCE_CONTRACT_KEYS:
        raise ValueError("qualification source contract is invalid")
    expected_capability = _MARKET_FOUNDATION_SOURCE_CAPABILITIES.get(
        producer_operation_id
    )
    if (
        expected_capability is None
        or capability_ids != (expected_capability,)
        or value.get("producer_operation_id") != producer_operation_id
        or value.get("capability_id") != expected_capability
        or value.get("fixture_id") != producer_operation_id
    ):
        raise ValueError("qualification source contract owner is invalid")
    provider = _mapping(value.get("provider"), "qualification source provider")
    if (
        set(provider)
        != {
            "id",
            "organization",
            "role",
            "documentation_url",
            "terms_url",
        }
        or provider.get("role") != "first_party_exchange"
    ):
        raise ValueError("qualification source provider is invalid")
    provider_id = _safe_release_reference(provider.get("id"), "source provider id")
    expected_hostname = _QUALIFIED_FIRST_PARTY_HOSTS.get(provider_id)
    if expected_hostname is None:
        raise ValueError("qualification source provider is invalid")
    _text(provider.get("organization"), "source provider organization")
    for key in ("documentation_url", "terms_url"):
        _qualified_first_party_url(
            provider.get(key),
            f"source provider {key}",
            expected_hostname=expected_hostname,
        )

    scope = _mapping(value.get("scope"), "qualification source scope")
    if set(scope) != {
        "exchange",
        "kind",
        "included_assets",
        "excluded_proxies",
    }:
        raise ValueError("qualification source scope is invalid")
    exchange = _safe_release_reference(scope.get("exchange"), "source exchange")
    if exchange != provider_id.upper():
        raise ValueError("qualification source scope is invalid")
    for key in ("kind", "included_assets", "excluded_proxies"):
        _text(scope.get(key), f"qualification source scope {key}")

    selector = _mapping(value.get("request_selector"), "qualification request selector")
    if set(selector) != {
        "method",
        "selector_kind",
        "resource_url",
        "date_selector",
        "max_requests",
        "max_pages",
    } or selector.get("method") not in {"GET", "POST"}:
        raise ValueError("qualification request selector is invalid")
    _text(selector.get("selector_kind"), "qualification selector kind")
    _qualified_first_party_url(
        selector.get("resource_url"),
        "source resource URL",
        expected_hostname=expected_hostname,
    )
    _text(selector.get("date_selector"), "qualification date selector")
    for key in ("max_requests", "max_pages"):
        bound = selector.get(key)
        if (
            not isinstance(bound, int)
            or isinstance(bound, bool)
            or not 1 <= bound <= 10
        ):
            raise ValueError("qualification request selector is invalid")

    response = _mapping(value.get("response_shape"), "qualification response shape")
    if set(response) != {"shape", "required_fields", "publication_time_rule"}:
        raise ValueError("qualification response shape is invalid")
    for key in response:
        _text(response.get(key), f"qualification response {key}")

    for key in ("field_destinations", "normalization"):
        facts = _mapping(value.get(key), f"qualification {key}")
        if not facts:
            raise ValueError(f"qualification {key} is invalid")
        for fact_key, fact in facts.items():
            _safe_release_reference(fact_key, f"qualification {key} key")
            _text(fact, f"qualification {key} value")

    applicability = _mapping(
        value.get("field_applicability"), "qualification field applicability"
    )
    if set(applicability) != {
        "applicable_fields",
        "not_applicable_fields",
        "formal_rule",
    }:
        raise ValueError("qualification field applicability is invalid")
    for rule in applicability.values():
        _text(rule, "qualification field applicability rule")

    state_mapping = _mapping(value.get("state_mapping"), "qualification state mapping")
    if set(state_mapping) != {
        "complete",
        "observed_empty",
        "partial",
        "credential_blocked",
        "unavailable",
        "not_applicable",
    }:
        raise ValueError("qualification state mapping is invalid")
    for state, rule in state_mapping.items():
        _safe_release_reference(state, "qualification state")
        _text(rule, "qualification state rule")

    for key in ("dependency_policy", "license_policy"):
        policy = _mapping(value.get(key), f"qualification {key}")
        if not policy:
            raise ValueError(f"qualification {key} is invalid")
        _safe_release_mapping(policy, f"qualification {key}")
    return value


def _qualified_first_party_url(
    value: object,
    label: str,
    *,
    expected_hostname: str,
) -> str:
    url = _text(value, label)
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_hostname
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} is invalid")
    return url


def _safe_release_mapping(value: Mapping[str, object], label: str) -> None:
    for key, item in value.items():
        normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
        if (
            not isinstance(key, str)
            or not key.strip()
            or normalized_key in _SENSITIVE_KEY_TOKENS
            or normalized_key.replace("_", "") in _SENSITIVE_COMPACT_KEY_TOKENS
            or any(
                token in normalized_key.split("_")
                for token in {"credential", "secret", "password", "cookie"}
            )
        ):
            raise ValueError("unsafe release metadata")
        if not isinstance(item, (str, int, bool)) or isinstance(item, float):
            raise ValueError(f"{label} contains unsupported values")
        if isinstance(item, str) and not item.strip():
            raise ValueError(f"{label} contains empty values")


def _safe_release_reference(value: object, label: str) -> str:
    reference = _text(value, label)
    if (
        not _SAFE_REFERENCE.fullmatch(reference)
        or reference.startswith(("/", "~", ".", "-"))
        or "//" in reference
        or ".." in reference.split("/")
        or "://" in reference
        or re.search(r"(?:^|[/:])(?:\d{1,3}\.){3}\d{1,3}(?:[/:]|$)", reference)
        or any(ord(character) < 32 or ord(character) == 127 for character in reference)
    ):
        raise ValueError("unsafe release metadata")
    return reference


def _safe_release_contract_text(value: object, label: str) -> str:
    """Allow bounded owner contract prose while rejecting sensitive metadata."""

    contract_text = _text(value, label)
    normalized = re.sub(r"[^a-z0-9]+", "_", contract_text.casefold()).strip("_")
    sensitive_word = re.search(
        r"(?:^|_)(?:authorization|bearer|credential|credentials|password|secret|"
        r"token|cookie|headers|api_key)(?:_|$)",
        normalized,
    )
    if (
        "://" in contract_text
        or re.search(r"(?:^|[/:])(?:\d{1,3}\.){3}\d{1,3}(?:[/:]|$)", contract_text)
        or re.search(r"(?:^|\s)[^\s/:@]+:[^\s/@]+@[^\s/]+", contract_text)
        or sensitive_word is not None
    ):
        raise ValueError("unsafe release metadata")
    return contract_text


def _application_identity_key(value: Mapping[str, object]) -> tuple[str, ...]:
    workflow_use = value.get("workflow_use")
    workflow = workflow_use if isinstance(workflow_use, Mapping) else {}
    return tuple(
        str(value.get(key, ""))
        for key in (
            "task_type",
            "plan_version",
            "plan_id",
            "request_profile",
            "capability_id",
            "operation_id",
            "execution_state",
        )
    ) + tuple(
        str(workflow.get(key, ""))
        for key in (
            "workflow_id",
            "plan_version",
            "plan_id",
            "step_id",
            "step_version",
        )
    )


def _application_sort_key(value: Mapping[str, object]) -> tuple[str, ...]:
    return _application_identity_key(value) + (
        json.dumps(
            value.get("field_dispositions"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _valid_field_disposition(value: object) -> int:
    field = _mapping(value, "field disposition")
    name = field.get("field")
    disposition = field.get("disposition")
    destination = field.get("destination")
    reason = field.get("reason")
    if not isinstance(name, str) or not name.strip():
        return 0
    if disposition == "excluded":
        return int(
            destination is None and isinstance(reason, str) and bool(reason.strip())
        )
    return int(
        disposition in {"normalized", "cross_check_only", "lineage_only", "retained"}
        and isinstance(destination, str)
        and bool(destination.strip())
        and reason is None
    )


def _rate(numerator: int, denominator: int) -> dict[str, object]:
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise ValueError("release matrix rate is invalid")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": format(Decimal(numerator) / Decimal(denominator), ".6f"),
    }


def _install_operations(
    health: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for value in _sequence(health.get("operations"), "installation operations"):
        fact = _mapping(value, "installation operation")
        operation_id = _text(fact.get("operation_id"), "installation operation id")
        if operation_id in indexed:
            raise ValueError("installation operation is duplicated")
        indexed[operation_id] = fact
    return indexed


def _validated_installation_health(
    health: Mapping[str, object],
) -> dict[str, object]:
    if (
        set(health)
        != {
            "schema_version",
            "status",
            "dependencies",
            "operations",
            "source_policy",
        }
        or health.get("schema_version") != "1.0"
    ):
        raise ValueError("installation health is invalid")
    if health.get("status") != "complete":
        raise ValueError("installation health is incomplete")
    dependencies: list[dict[str, object]] = []
    for raw_fact in _sequence(health.get("dependencies"), "installation dependencies"):
        fact = _mapping(raw_fact, "installation dependency")
        if (
            set(fact)
            != {
                "distribution",
                "required_version",
                "installed_version",
                "status",
                "modules",
                "operation_ids",
            }
            or fact.get("status") != "complete"
        ):
            raise ValueError("installation health dependency is invalid")
        modules = _health_tokens(fact.get("modules"), "installation modules")
        operation_ids = _health_tokens(
            fact.get("operation_ids"), "installation dependency operations"
        )
        dependencies.append(
            {
                "distribution": _health_token(
                    fact.get("distribution"), "installation distribution"
                ),
                "required_version": _health_token(
                    fact.get("required_version"), "installation required version"
                ),
                "installed_version": _health_token(
                    fact.get("installed_version"), "installation installed version"
                ),
                "status": "complete",
                "modules": modules,
                "operation_ids": operation_ids,
            }
        )
    operations: list[dict[str, str]] = []
    for raw_fact in _sequence(health.get("operations"), "installation operations"):
        fact = _mapping(raw_fact, "installation operation")
        if set(fact) != {"operation_id", "status"} or fact.get("status") != "complete":
            raise ValueError("installation health operation is invalid")
        operations.append(
            {
                "operation_id": _health_token(
                    fact.get("operation_id"), "installation operation id"
                ),
                "status": "complete",
            }
        )
    source_policy = _mapping(health.get("source_policy"), "installation source policy")
    if set(source_policy) != {
        "source_accessibility",
        "credentials",
        "evidence_adoption",
    } or any(item != "not_checked" for item in source_policy.values()):
        raise ValueError("installation health source policy is invalid")
    return {
        "schema_version": "1.0",
        "status": "complete",
        "dependencies": dependencies,
        "operations": operations,
        "source_policy": {
            "source_accessibility": "not_checked",
            "credentials": "not_checked",
            "evidence_adoption": "not_checked",
        },
    }


def _health_tokens(value: object, label: str) -> list[str]:
    tokens = [_health_token(item, label) for item in _sequence(value, label)]
    if len(tokens) != len(set(tokens)):
        raise ValueError(f"{label} are invalid")
    return tokens


def _health_token(value: object, label: str) -> str:
    token = _text(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@+!-]{0,127}", token):
        raise ValueError(f"{label} is invalid")
    return token


def _install_dependencies(
    health: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    facts: list[Mapping[str, object]] = []
    for value in _sequence(health.get("dependencies"), "installation dependencies"):
        fact = _mapping(value, "installation dependency")
        _sequence(fact.get("operation_ids"), "dependency operation ids")
        facts.append(fact)
    return tuple(facts)


def _verified_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("release evidence verified_at is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("release evidence verified_at requires a timezone")
    return parsed


def canonical_release_as_of(value: object, label: str) -> str:
    """Return one exact ISO request date used by release evidence authorities."""

    text = _text(value, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.isoformat() != text:
        raise ValueError(f"{label} is invalid")
    return text


def _operation_application(value: object, label: str) -> OperationOwner:
    application = _sequence(value, label)
    if len(application) != 2:
        raise ValueError(f"{label} is invalid")
    return (
        _safe_release_reference(application[0], f"{label} capability id"),
        _safe_release_reference(application[1], f"{label} producer operation id"),
    )


def _strict_issue(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("operation gap routing is invalid")
    return value
