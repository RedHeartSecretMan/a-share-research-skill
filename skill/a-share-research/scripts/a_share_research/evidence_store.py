"""Explicit, auditable persistence for eligible operation-owned evidence."""

from __future__ import annotations

import errno
import json
import os
import secrets
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterator, Literal, Mapping

from .bounded_public_data import validate_bounded_public_data
from .research_result import (
    FieldDisposition,
    OperationExecution,
    OperationOutcome,
    OperationReuseFacts,
)

if sys.platform != "win32":
    import fcntl

    def _try_process_lock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_process_lock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)

else:

    def _try_process_lock(descriptor: int) -> None:
        del descriptor
        raise OSError(errno.ENOTSUP, "anchored evidence-store locking unavailable")

    def _unlock_process_lock(descriptor: int) -> None:
        del descriptor
        raise OSError(errno.ENOTSUP, "anchored evidence-store locking unavailable")


_USE_ANCHORED_DIR_FD = (
    sys.platform != "win32" and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
)


STORE_SCHEMA_VERSION = "1.0"
POLICY_VERSION = "core-v0.3.0"
_MAX_STORE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_STRING_LENGTH = 2 * 1024 * 1024
_MAX_FRESH_ACQUISITION_CLOCK_SKEW = timedelta(minutes=5)
_PROCESS_LOCK_TIMEOUT_SECONDS = 30.0
_PROCESS_LOCK_RETRY_SECONDS = 0.05


@dataclass(frozen=True)
class EvidenceStoreRequest:
    path: Path
    mode: str
    request_scope: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.mode not in {"read_write", "delete"}:
            raise ValueError("evidence_store mode is unsupported")
        _validate_safe_value(self.request_scope)

    def plan_audit(self) -> dict[str, object]:
        return {
            "schema_version": STORE_SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "mode": self.mode,
            "request_scope": _json_copy(self.request_scope),
        }


@dataclass(frozen=True)
class EvidenceReusePolicy:
    capability_id: str
    operation_id: str
    source_version: str
    source_role: str
    experimental: bool
    field_dispositions: tuple[FieldDisposition, ...]
    subject_identity: str
    evidence_scope: Literal["completed_history", "current", "intraday"]
    max_age: timedelta
    persistence_permitted: bool = True
    outcome_fitness: Callable[[OperationOutcome], bool] | None = None

    def __post_init__(self) -> None:
        for value in (
            self.capability_id,
            self.operation_id,
            self.source_version,
            self.source_role,
            self.subject_identity,
        ):
            if not value or not value.strip():
                raise ValueError("evidence reuse policy identity must be non-empty")
        if self.max_age <= timedelta(0):
            raise ValueError("evidence reuse policy max age must be positive")
        if self.evidence_scope not in {"completed_history", "current", "intraday"}:
            raise ValueError("evidence reuse policy scope is unsupported")


@dataclass(frozen=True)
class EvidenceStoreResolution:
    outcome: OperationOutcome
    audit: Mapping[str, object]


@dataclass(frozen=True)
class _AnchoredStoreTransaction:
    path: Path
    parent_descriptor: int
    parent_identity: os.stat_result
    initial_target_identity: os.stat_result | None


def parse_evidence_store_request(
    request: Mapping[str, object],
) -> tuple[dict[str, object], EvidenceStoreRequest | None]:
    """Remove and validate the cross-task acquisition policy before dispatch."""

    source_policy = request.get("source_policy")
    if not isinstance(source_policy, dict):
        raise ValueError("research source_policy must be a JSON object")
    sanitized_policy = {
        key: value for key, value in source_policy.items() if key != "evidence_store"
    }
    sanitized_request = {**request, "source_policy": sanitized_policy}
    if "evidence_store" not in source_policy:
        return sanitized_request, None
    configuration = source_policy["evidence_store"]
    if not isinstance(configuration, dict) or set(configuration) != {"mode", "path"}:
        raise ValueError("research evidence_store has an invalid shape")
    mode = configuration.get("mode")
    path_value = configuration.get("path")
    if not isinstance(mode, str) or not isinstance(path_value, str):
        raise ValueError("research evidence_store fields must be strings")
    if not path_value or not path_value.strip() or "\x00" in path_value:
        raise ValueError("research evidence_store path must be a non-empty path")
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError("research evidence_store path must be absolute")
    request_scope = _json_copy(sanitized_request)
    assert isinstance(request_scope, dict)
    request_scope.pop("schema_version", None)
    return sanitized_request, EvidenceStoreRequest(
        path=path,
        mode=mode,
        request_scope=request_scope,
    )


def resolve_operation_evidence(
    store: EvidenceStoreRequest,
    policy: EvidenceReusePolicy,
    acquire: Callable[[], OperationOutcome],
    *,
    now: datetime,
) -> EvidenceStoreResolution:
    """Acquire and persist one eligible operation without exposing storage details."""

    current_time = _aware_datetime(now, "evidence store current time")
    record_id = _record_id(store.request_scope, policy)
    if not policy.persistence_permitted or policy.evidence_scope != "completed_history":
        outcome = acquire()
        _validate_acquired_owner(outcome, policy)
        acquired_at = _optional_original_acquired_at(outcome)
        return EvidenceStoreResolution(
            outcome=outcome,
            audit=_operation_audit(
                policy,
                record_id=record_id,
                stored_source_version=None,
                lookup_state="not_permitted",
                write_state="not_written",
                acquired_at=acquired_at,
                result_acquired_at=acquired_at,
                freshness="not_assessed",
                fitness="unfit",
                reason="policy_not_permitted",
                reuse_facts=outcome.reuse_facts,
            ),
        )
    if not _USE_ANCHORED_DIR_FD:
        raise ValueError(
            "research evidence_store persistence is unavailable on this platform"
        )
    with _store_lock(store.path) as transaction:
        return _resolve_persistent_operation_evidence(
            store,
            policy,
            acquire,
            transaction=transaction,
            current_time=current_time,
            record_id=record_id,
        )


def _resolve_persistent_operation_evidence(
    store: EvidenceStoreRequest,
    policy: EvidenceReusePolicy,
    acquire: Callable[[], OperationOutcome],
    *,
    transaction: _AnchoredStoreTransaction,
    current_time: datetime,
    record_id: str,
) -> EvidenceStoreResolution:
    records = _read_records(transaction)
    if store.mode == "delete":
        stored = records.get(record_id)
        if stored is None:
            outcome = acquire()
            _validate_acquired_owner(outcome, policy)
            acquired_at = _optional_original_acquired_at(outcome)
            return EvidenceStoreResolution(
                outcome=outcome,
                audit=_operation_audit(
                    policy,
                    record_id=record_id,
                    stored_source_version=None,
                    lookup_state="miss",
                    write_state="not_written",
                    acquired_at=None,
                    result_acquired_at=acquired_at,
                    freshness="not_assessed",
                    fitness="unfit",
                    reason="delete_target_absent",
                    reuse_facts=outcome.reuse_facts,
                ),
            )
        if not isinstance(stored, Mapping):
            raise ValueError("stored operation evidence must be an object")
        _, stored_acquired_at = _restore_operation_outcome(
            stored,
            record_id=record_id,
            request_scope=store.request_scope,
            policy=policy,
        )
        stored_time = _aware_datetime(stored_acquired_at, "stored acquisition time")
        age = current_time - stored_time
        if age < timedelta(0):
            raise ValueError("stored acquisition time cannot be in the future")
        freshness = "fresh" if age <= policy.max_age else "expired"
        records.pop(record_id)
        _write_records(transaction, records)
        outcome = acquire()
        _validate_acquired_owner(outcome, policy)
        return EvidenceStoreResolution(
            outcome=outcome,
            audit=_operation_audit(
                policy,
                record_id=record_id,
                stored_source_version=policy.source_version,
                lookup_state="deleted",
                write_state="deleted",
                acquired_at=stored_acquired_at,
                result_acquired_at=_optional_original_acquired_at(outcome),
                freshness=freshness,
                fitness="fit" if freshness == "fresh" else "unfit",
                reason="explicit_delete",
                reuse_facts=outcome.reuse_facts,
            ),
        )
    replaced_audit: dict[str, object] | None = None
    replaced_record_id: str | None = None
    if record_id in records:
        stored = records[record_id]
        if not isinstance(stored, Mapping):
            raise ValueError("stored operation evidence must be an object")
        outcome, acquired_at = _restore_operation_outcome(
            stored,
            record_id=record_id,
            request_scope=store.request_scope,
            policy=policy,
        )
        acquired_time = _aware_datetime(acquired_at, "stored acquisition time")
        age = current_time - acquired_time
        if age < timedelta(0):
            raise ValueError("stored acquisition time cannot be in the future")
        if age <= policy.max_age:
            return EvidenceStoreResolution(
                outcome=outcome,
                audit=_operation_audit(
                    policy,
                    record_id=record_id,
                    stored_source_version=policy.source_version,
                    lookup_state="hit",
                    write_state="not_written",
                    acquired_at=acquired_at,
                    result_acquired_at=acquired_at,
                    freshness="fresh",
                    fitness="fit",
                    reason=None,
                    reuse_facts=outcome.reuse_facts,
                ),
            )
        replaced_audit = _operation_audit(
            policy,
            record_id=record_id,
            stored_source_version=policy.source_version,
            lookup_state="expired",
            write_state="replaced",
            acquired_at=acquired_at,
            result_acquired_at=acquired_at,
            freshness="expired",
            fitness="unfit",
            reason="freshness_expired",
        )
    else:
        version_candidate = _version_mismatch_candidate(
            records,
            request_scope=store.request_scope,
            policy=policy,
            now=current_time,
        )
        if version_candidate is not None:
            replaced_record_id, stored_version, stored_acquired_at, freshness = (
                version_candidate
            )
            replaced_audit = _operation_audit(
                policy,
                record_id=record_id,
                stored_source_version=stored_version,
                lookup_state="version_mismatch",
                write_state="replaced",
                acquired_at=stored_acquired_at,
                result_acquired_at=stored_acquired_at,
                freshness=freshness,
                fitness="unfit",
                reason="source_version_mismatch",
            )
    outcome = acquire()
    _validate_acquired_owner(outcome, policy)
    if replaced_audit is not None:
        _set_operation_reuse_facts(replaced_audit, outcome.reuse_facts)
    if not _outcome_satisfies_reuse_policy(outcome, policy):
        acquired_at = _optional_original_acquired_at(outcome)
        if replaced_audit is not None:
            replaced_audit["write_state"] = "not_written"
            replaced_audit["result_acquired_at"] = acquired_at
            replaced_audit["reason"] = "replacement_outcome_not_reusable"
            return EvidenceStoreResolution(outcome=outcome, audit=replaced_audit)
        return EvidenceStoreResolution(
            outcome=outcome,
            audit=_operation_audit(
                policy,
                record_id=record_id,
                stored_source_version=(
                    replaced_audit.get("stored_source_version")
                    if replaced_audit is not None
                    else None
                ),
                lookup_state="miss",
                write_state="not_written",
                acquired_at=acquired_at,
                result_acquired_at=acquired_at,
                freshness="not_assessed",
                fitness="unfit",
                reason="outcome_not_reusable",
                reuse_facts=outcome.reuse_facts,
            ),
        )
    acquired_at = _original_acquired_at(outcome)
    acquired_time = _aware_datetime(acquired_at, "acquired operation evidence time")
    if acquired_time - current_time > _MAX_FRESH_ACQUISITION_CLOCK_SKEW:
        raise ValueError("acquired operation evidence time cannot be in the future")
    if current_time - acquired_time > policy.max_age:
        if replaced_audit is not None:
            replaced_audit["write_state"] = "not_written"
            replaced_audit["result_acquired_at"] = acquired_at
            replaced_audit["reason"] = "replacement_outcome_not_reusable"
            return EvidenceStoreResolution(outcome=outcome, audit=replaced_audit)
        return EvidenceStoreResolution(
            outcome=outcome,
            audit=_operation_audit(
                policy,
                record_id=record_id,
                stored_source_version=None,
                lookup_state="miss",
                write_state="not_written",
                acquired_at=acquired_at,
                result_acquired_at=acquired_at,
                freshness="expired",
                fitness="unfit",
                reason="acquired_evidence_stale",
                reuse_facts=outcome.reuse_facts,
            ),
        )
    record = {
        "schema_version": STORE_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "record_id": record_id,
        "source_version": policy.source_version,
        "request_scope": _json_copy(store.request_scope),
        "capability_id": policy.capability_id,
        "operation_id": policy.operation_id,
        "source_role": policy.source_role,
        "experimental": policy.experimental,
        "subject_identity": policy.subject_identity,
        "execution": {
            "state": outcome.execution.state,
            "query_complete": outcome.execution.query_complete,
        },
        "observations": outcome.to_dict()["observations"],
        "source_errors": outcome.to_dict()["source_errors"],
        "field_dispositions": [
            _json_copy(item.to_dict()) for item in outcome.field_dispositions
        ],
        "reuse_facts": (
            None if outcome.reuse_facts is None else outcome.reuse_facts.to_dict()
        ),
        "original_acquired_at": acquired_at,
    }
    _validate_safe_value(record)
    if replaced_record_id is not None:
        records.pop(replaced_record_id)
    records[record_id] = record
    _write_records(transaction, records)
    if replaced_audit is not None:
        replaced_audit["result_acquired_at"] = acquired_at
        return EvidenceStoreResolution(outcome=outcome, audit=replaced_audit)
    return EvidenceStoreResolution(
        outcome=outcome,
        audit=_operation_audit(
            policy,
            record_id=record_id,
            stored_source_version=None,
            lookup_state="miss",
            write_state="written",
            acquired_at=acquired_at,
            result_acquired_at=acquired_at,
            freshness="fresh",
            fitness="fit",
            reason=None,
            reuse_facts=outcome.reuse_facts,
        ),
    )


def validate_plan_audit(value: Mapping[str, object]) -> None:
    """Validate the sanitized acquisition policy carried by a formal plan."""

    if set(value) != {
        "schema_version",
        "policy_version",
        "mode",
        "request_scope",
    }:
        raise ValueError("plan evidence storage audit has an invalid shape")
    if (
        value.get("schema_version") != STORE_SCHEMA_VERSION
        or value.get("policy_version") != POLICY_VERSION
        or value.get("mode") not in {"read_write", "delete"}
        or not isinstance(value.get("request_scope"), Mapping)
    ):
        raise ValueError("plan evidence storage audit is invalid")
    request_scope = value["request_scope"]
    assert isinstance(request_scope, Mapping)
    if set(request_scope) != {
        "task_type",
        "subjects",
        "as_of",
        "window",
        "parameters",
        "source_policy",
    }:
        raise ValueError("plan evidence storage request scope has an invalid shape")
    policy = request_scope.get("source_policy")
    if not isinstance(policy, Mapping) or set(policy) != {
        "allow_experimental",
        "allow_credentials",
        "allow_fallback",
    }:
        raise ValueError("plan evidence storage source policy is invalid")
    _validate_safe_value(value)


def validate_operation_audit(
    value: Mapping[str, object],
    *,
    plan_audit: Mapping[str, object],
    capability_id: str,
    operation_id: str,
) -> None:
    """Full-rehash one operation audit against its owning plan."""

    validate_plan_audit(plan_audit)
    required_keys = {
        "record_id",
        "source_version",
        "stored_source_version",
        "subject_identity",
        "lookup_state",
        "write_state",
        "original_acquired_at",
        "result_acquired_at",
        "freshness",
        "fitness_for_use",
        "reason",
    }
    if set(value) not in {
        frozenset(required_keys),
        frozenset((*required_keys, "reuse_facts")),
    }:
        raise ValueError("operation evidence storage audit has an invalid shape")
    if "reuse_facts" in value:
        OperationReuseFacts.from_dict(value["reuse_facts"])
    source_version = value.get("source_version")
    stored_source_version = value.get("stored_source_version")
    subject_identity = value.get("subject_identity")
    if (
        not isinstance(source_version, str)
        or not source_version
        or not isinstance(subject_identity, str)
        or not subject_identity
        or not (
            stored_source_version is None
            or isinstance(stored_source_version, str)
            and bool(stored_source_version)
        )
    ):
        raise ValueError("operation evidence storage source version is invalid")
    policy = EvidenceReusePolicy(
        capability_id=capability_id,
        operation_id=operation_id,
        source_version=source_version,
        source_role="public_audit_validation",
        experimental=False,
        field_dispositions=(),
        subject_identity=subject_identity,
        evidence_scope="completed_history",
        max_age=timedelta(seconds=1),
    )
    request_scope = plan_audit["request_scope"]
    assert isinstance(request_scope, Mapping)
    if value.get("record_id") != _record_id(request_scope, policy):
        raise ValueError("operation evidence storage record ID is invalid")
    original_acquired_at = value.get("original_acquired_at")
    result_acquired_at = value.get("result_acquired_at")
    if not (
        original_acquired_at is None or isinstance(original_acquired_at, str)
    ) or not (result_acquired_at is None or isinstance(result_acquired_at, str)):
        raise ValueError("operation evidence storage audit facts are invalid")
    if isinstance(original_acquired_at, str):
        _aware_datetime(
            original_acquired_at, "operation audit original acquisition time"
        )
    if isinstance(result_acquired_at, str):
        _aware_datetime(result_acquired_at, "operation audit result acquisition time")
    facts = (
        value.get("lookup_state"),
        value.get("write_state"),
        value.get("freshness"),
        value.get("fitness_for_use"),
        value.get("reason"),
    )
    if facts not in {
        ("miss", "written", "fresh", "fit", None),
        ("hit", "not_written", "fresh", "fit", None),
        (
            "expired",
            "replaced",
            "expired",
            "unfit",
            "freshness_expired",
        ),
        (
            "miss",
            "not_written",
            "not_assessed",
            "unfit",
            "outcome_not_reusable",
        ),
        (
            "miss",
            "not_written",
            "expired",
            "unfit",
            "acquired_evidence_stale",
        ),
        (
            "not_permitted",
            "not_written",
            "not_assessed",
            "unfit",
            "policy_not_permitted",
        ),
        (
            "version_mismatch",
            "replaced",
            "fresh",
            "unfit",
            "source_version_mismatch",
        ),
        ("deleted", "deleted", "fresh", "fit", "explicit_delete"),
        ("deleted", "deleted", "expired", "unfit", "explicit_delete"),
        (
            "miss",
            "not_written",
            "not_assessed",
            "unfit",
            "delete_target_absent",
        ),
        (
            "version_mismatch",
            "replaced",
            "expired",
            "unfit",
            "source_version_mismatch",
        ),
        (
            "expired",
            "not_written",
            "expired",
            "unfit",
            "replacement_outcome_not_reusable",
        ),
        (
            "version_mismatch",
            "not_written",
            "fresh",
            "unfit",
            "replacement_outcome_not_reusable",
        ),
        (
            "version_mismatch",
            "not_written",
            "expired",
            "unfit",
            "replacement_outcome_not_reusable",
        ),
    }:
        raise ValueError("operation evidence storage audit facts are invalid")
    if (
        facts[0] in {"miss", "hit"}
        and facts[4] != "delete_target_absent"
        and (original_acquired_at != result_acquired_at)
    ):
        raise ValueError("operation evidence storage acquisition times contradict")
    if facts[0] in {"hit", "expired", "deleted"} and (
        stored_source_version != source_version
    ):
        raise ValueError("operation evidence storage stored version contradicts")
    if facts[0] == "version_mismatch" and (
        stored_source_version is None or stored_source_version == source_version
    ):
        raise ValueError("operation evidence storage version mismatch contradicts")
    if facts[0] in {"miss", "not_permitted"} and stored_source_version is not None:
        raise ValueError("operation evidence storage unexpected stored version")
    if facts[0] in {"hit", "expired", "version_mismatch", "deleted"} and (
        original_acquired_at is None
    ):
        raise ValueError(
            "operation evidence storage stored acquisition time is missing"
        )
    if facts[1] == "replaced" and result_acquired_at is None:
        raise ValueError("operation evidence storage replacement time is missing")
    _validate_safe_value(value)


def _record_id(request_scope: Mapping[str, object], policy: EvidenceReusePolicy) -> str:
    payload = {
        "schema_version": STORE_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "request_scope": request_scope,
        "capability_id": policy.capability_id,
        "operation_id": policy.operation_id,
        "source_version": policy.source_version,
        "subject_identity": policy.subject_identity,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(encoded).hexdigest()[:24]


def _version_mismatch_candidate(
    records: Mapping[str, object],
    *,
    request_scope: Mapping[str, object],
    policy: EvidenceReusePolicy,
    now: datetime,
) -> tuple[str, str, str, str] | None:
    candidates: list[tuple[str, str, str, str]] = []
    for stored_record_id, candidate in records.items():
        if not isinstance(candidate, Mapping):
            continue
        if not (
            candidate.get("schema_version") == STORE_SCHEMA_VERSION
            and candidate.get("policy_version") == POLICY_VERSION
            and candidate.get("request_scope") == request_scope
            and candidate.get("capability_id") == policy.capability_id
            and candidate.get("operation_id") == policy.operation_id
            and candidate.get("subject_identity") == policy.subject_identity
        ):
            continue
        stored_version = candidate.get("source_version")
        if not isinstance(stored_version, str) or not stored_version:
            raise ValueError("stored operation source version is invalid")
        if stored_version == policy.source_version:
            raise ValueError("stored operation record identity is inconsistent")
        stored_policy = replace(policy, source_version=stored_version)
        expected_record_id = _record_id(request_scope, stored_policy)
        if stored_record_id != expected_record_id:
            raise ValueError("stored operation record ID is invalid")
        _, acquired_at = _restore_operation_outcome(
            candidate,
            record_id=stored_record_id,
            request_scope=request_scope,
            policy=stored_policy,
        )
        acquired_time = _aware_datetime(acquired_at, "stored acquisition time")
        age = now - acquired_time
        if age < timedelta(0):
            raise ValueError("stored acquisition time cannot be in the future")
        freshness = "fresh" if age <= policy.max_age else "expired"
        candidates.append((stored_record_id, stored_version, acquired_at, freshness))
    if len(candidates) > 1:
        raise ValueError("multiple stored source versions match this operation")
    return candidates[0] if candidates else None


def _original_acquired_at(outcome: OperationOutcome) -> str:
    retrieved = {
        value
        for observation in outcome.observations
        for value in (observation.get("retrieved_at"),)
        if isinstance(value, str) and value
    }
    if not retrieved:
        raise ValueError("persisted evidence requires an original acquisition time")
    parsed = [
        (_aware_datetime(value, "operation evidence acquisition time"), value)
        for value in retrieved
    ]
    return max(parsed, key=lambda item: item[0])[1]


def _optional_original_acquired_at(outcome: OperationOutcome) -> str | None:
    retrieved = {
        value
        for observation in outcome.observations
        for value in (observation.get("retrieved_at"),)
        if isinstance(value, str) and value
    }
    if not retrieved:
        return None
    parsed = [
        (_aware_datetime(value, "operation evidence acquisition time"), value)
        for value in retrieved
    ]
    return max(parsed, key=lambda item: item[0])[1]


def _validate_acquired_owner(
    outcome: OperationOutcome, policy: EvidenceReusePolicy
) -> None:
    if outcome.capability_id != policy.capability_id or outcome.operation_id != (
        policy.operation_id
    ):
        raise ValueError("acquired outcome contradicts evidence reuse policy")


def _outcome_is_reusable(outcome: OperationOutcome) -> bool:
    return (
        outcome.execution.state == "succeeded"
        and outcome.execution.query_complete is True
        and bool(outcome.observations)
        and not outcome.source_errors
        and _optional_original_acquired_at(outcome) is not None
    )


def _outcome_satisfies_reuse_policy(
    outcome: OperationOutcome, policy: EvidenceReusePolicy
) -> bool:
    if not _outcome_is_reusable(outcome):
        return False
    return policy.outcome_fitness is None or policy.outcome_fitness(outcome)


def _operation_audit(
    policy: EvidenceReusePolicy,
    *,
    record_id: str,
    stored_source_version: object,
    lookup_state: str,
    write_state: str,
    acquired_at: str | None,
    result_acquired_at: str | None,
    freshness: str,
    fitness: str,
    reason: str | None,
    reuse_facts: OperationReuseFacts | None = None,
) -> dict[str, object]:
    audit: dict[str, object] = {
        "record_id": record_id,
        "source_version": policy.source_version,
        "stored_source_version": stored_source_version,
        "subject_identity": policy.subject_identity,
        "lookup_state": lookup_state,
        "write_state": write_state,
        "original_acquired_at": acquired_at,
        "result_acquired_at": result_acquired_at,
        "freshness": freshness,
        "fitness_for_use": fitness,
        "reason": reason,
    }
    _set_operation_reuse_facts(audit, reuse_facts)
    return audit


def _set_operation_reuse_facts(
    audit: dict[str, object], reuse_facts: OperationReuseFacts | None
) -> None:
    if reuse_facts is None:
        audit.pop("reuse_facts", None)
    else:
        audit["reuse_facts"] = reuse_facts.to_dict()


def _restore_operation_outcome(
    record: Mapping[str, object],
    *,
    record_id: str,
    request_scope: Mapping[str, object],
    policy: EvidenceReusePolicy,
) -> tuple[OperationOutcome, str]:
    expected_keys = {
        "schema_version",
        "policy_version",
        "record_id",
        "source_version",
        "request_scope",
        "capability_id",
        "operation_id",
        "source_role",
        "experimental",
        "subject_identity",
        "execution",
        "observations",
        "source_errors",
        "field_dispositions",
        "reuse_facts",
        "original_acquired_at",
    }
    if set(record) != expected_keys:
        raise ValueError("stored operation evidence has an invalid shape")
    if (
        record.get("schema_version") != STORE_SCHEMA_VERSION
        or record.get("policy_version") != POLICY_VERSION
        or record.get("record_id") != record_id
        or record.get("source_version") != policy.source_version
        or record.get("request_scope") != request_scope
        or record.get("capability_id") != policy.capability_id
        or record.get("operation_id") != policy.operation_id
        or record.get("source_role") != policy.source_role
        or record.get("experimental") is not policy.experimental
        or record.get("subject_identity") != policy.subject_identity
    ):
        raise ValueError("stored operation evidence contradicts its current policy")
    execution = record.get("execution")
    observations = record.get("observations")
    source_errors = record.get("source_errors")
    dispositions = record.get("field_dispositions")
    reuse_facts_value = record.get("reuse_facts")
    if (
        not isinstance(execution, Mapping)
        or set(execution) != {"state", "query_complete"}
        or not isinstance(observations, list)
        or not isinstance(source_errors, list)
        or not isinstance(dispositions, list)
        or dispositions != [item.to_dict() for item in policy.field_dispositions]
    ):
        raise ValueError("stored operation evidence contract is invalid")
    execution_state = execution.get("state")
    query_complete = execution.get("query_complete")
    if not isinstance(execution_state, str) or query_complete not in {
        None,
        True,
        False,
    }:
        raise ValueError("stored operation execution facts are invalid")
    outcome = OperationOutcome(
        capability_id=policy.capability_id,
        operation_id=policy.operation_id,
        execution=OperationExecution(
            execution_state,  # type: ignore[arg-type]
            query_complete=query_complete,
        ),
        observations=tuple(observations),
        source_errors=tuple(source_errors),
        field_dispositions=policy.field_dispositions,
        diagnostics={},
        reuse_facts=(
            None
            if reuse_facts_value is None
            else OperationReuseFacts.from_dict(reuse_facts_value)
        ),
    )
    if not _outcome_satisfies_reuse_policy(outcome, policy):
        raise ValueError("stored operation evidence does not satisfy its reuse policy")
    acquired_at = record.get("original_acquired_at")
    if not isinstance(acquired_at, str) or acquired_at != _original_acquired_at(
        outcome
    ):
        raise ValueError("stored operation acquisition time is invalid")
    _aware_datetime(acquired_at, "stored acquisition time")
    return outcome, acquired_at


def _aware_datetime(value: str | datetime, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise ValueError(f"{path} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{path} must include a timezone")
    return parsed


def _validate_store_path(path: Path) -> None:
    if not path.name or path.name in {".", ".."}:
        raise ValueError("research evidence_store path must name a regular file")
    with _opened_parent(path) as parent_descriptor:
        target = _target_stat(parent_descriptor, path.name)
        if target is not None and not stat.S_ISREG(target.st_mode):
            raise ValueError("research evidence_store path must be a regular file")


@contextmanager
def _opened_parent(path: Path) -> Iterator[int]:
    """Anchor an existing real parent without following any path-component link."""

    if not path.is_absolute() or not path.name:
        raise ValueError("research evidence_store path must name a regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parent.parts[1:]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        parent_info = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_info.st_mode):
            raise ValueError(
                "research evidence_store parent must be an existing real directory"
            )
    except ValueError:
        raise
    except OSError:
        raise ValueError(
            "research evidence_store parent must be an existing real directory"
        ) from None
    assert descriptor is not None
    try:
        yield descriptor
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _target_stat(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        target = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(
            "research evidence_store path could not be inspected"
        ) from None
    if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
        raise ValueError("research evidence_store path must be a regular file")
    return target


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _same_optional_identity(
    left: os.stat_result | None, right: os.stat_result | None
) -> bool:
    if left is None or right is None:
        return left is right
    return _same_identity(left, right)


def _parent_is_still_current(path: Path, anchored: os.stat_result) -> bool:
    try:
        with _opened_parent(path) as current_descriptor:
            return _same_identity(anchored, os.fstat(current_descriptor))
    except ValueError:
        return False


def _acquire_process_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    deadline = time.monotonic() + _PROCESS_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            _try_process_lock(descriptor)
            return
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "evidence store lock acquisition timed out"
                ) from None
            time.sleep(min(_PROCESS_LOCK_RETRY_SECONDS, remaining))


def _release_process_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    _unlock_process_lock(descriptor)


@contextmanager
def _store_lock(path: Path) -> Iterator[_AnchoredStoreTransaction]:
    """Serialize one explicit store's read-modify-replace lifecycle across processes."""

    if not _USE_ANCHORED_DIR_FD:
        raise ValueError(
            "research evidence_store persistence is unavailable on this platform"
        )

    lock_name = (
        f".a-share-evidence-{sha256(path.name.encode('utf-8')).hexdigest()[:24]}.lock"
    )
    descriptor: int | None = None
    try:
        with _opened_parent(path) as parent_descriptor:
            _target_stat(parent_descriptor, path.name)
            anchored_parent = os.fstat(parent_descriptor)
            existing_lock = _target_stat(parent_descriptor, lock_name)
            descriptor = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            opened_lock = os.fstat(descriptor)
            if not stat.S_ISREG(opened_lock.st_mode) or (
                existing_lock is not None
                and not _same_identity(existing_lock, opened_lock)
            ):
                raise OSError("evidence store lock identity changed")
            if opened_lock.st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            _acquire_process_lock(descriptor)
            current_lock = _target_stat(parent_descriptor, lock_name)
            if (
                current_lock is None
                or not _same_identity(opened_lock, current_lock)
                or not _parent_is_still_current(path, anchored_parent)
            ):
                raise OSError("evidence store lock boundary changed")
            initial_target = _target_stat(parent_descriptor, path.name)
            yield _AnchoredStoreTransaction(
                path=path,
                parent_descriptor=parent_descriptor,
                parent_identity=anchored_parent,
                initial_target_identity=initial_target,
            )
    except ValueError:
        raise
    except OSError:
        raise ValueError(
            "research evidence_store lock could not be acquired safely"
        ) from None
    finally:
        if descriptor is not None:
            try:
                _release_process_lock(descriptor)
            except OSError:
                pass
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_records(transaction: _AnchoredStoreTransaction) -> dict[str, object]:
    path = transaction.path
    parent_descriptor = transaction.parent_descriptor
    try:
        if not _parent_is_still_current(path, transaction.parent_identity):
            raise OSError("evidence store parent identity changed")
        target_before = _target_stat(parent_descriptor, path.name)
        if not _same_optional_identity(
            target_before, transaction.initial_target_identity
        ):
            raise OSError("evidence store target identity changed")
        if target_before is None:
            return {}
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not _same_identity(target_before, opened)
                or opened.st_size > _MAX_STORE_BYTES
            ):
                raise ValueError(
                    "research evidence_store exceeds its safe size or identity limit"
                )
            chunks: list[bytes] = []
            remaining = _MAX_STORE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining == 0 and os.read(descriptor, 1):
                raise ValueError("research evidence_store exceeds its safe size limit")
        finally:
            os.close(descriptor)
        target_after = _target_stat(parent_descriptor, path.name)
        if (
            target_after is None
            or not _same_identity(opened, target_after)
            or not _parent_is_still_current(path, transaction.parent_identity)
        ):
            raise OSError("evidence store changed while being read")
    except (json.JSONDecodeError, OSError, UnicodeError):
        raise ValueError("research evidence_store could not be read safely") from None
    except ValueError:
        raise
    return _records_from_bytes(b"".join(chunks))


def _records_from_bytes(encoded: bytes) -> dict[str, object]:
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("research evidence_store could not be read safely") from None
    if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
        raise ValueError("research evidence_store has an invalid schema")
    if value["schema_version"] != STORE_SCHEMA_VERSION or not isinstance(
        value["records"], dict
    ):
        raise ValueError("research evidence_store has an unsupported version")
    _validate_safe_value(value["records"])
    return dict(value["records"])


def _write_records(
    transaction: _AnchoredStoreTransaction, records: Mapping[str, object]
) -> None:
    path = transaction.path
    payload = {
        "schema_version": STORE_SCHEMA_VERSION,
        "records": _json_copy(records),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(encoded) > _MAX_STORE_BYTES:
        raise ValueError("research evidence_store exceeds its safe size limit")
    temporary_name = f".evidence-{secrets.token_hex(12)}.tmp"
    backup_name = ""
    replaced_target = False
    rollback_failed = False
    parent_descriptor = transaction.parent_descriptor
    try:
        try:
            if not _parent_is_still_current(path, transaction.parent_identity):
                raise OSError("evidence store parent identity changed")
            target_before = _target_stat(parent_descriptor, path.name)
            if not _same_optional_identity(
                target_before, transaction.initial_target_identity
            ):
                raise OSError("evidence store target identity changed")
            temporary_descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            try:
                offset = 0
                while offset < len(encoded):
                    offset += os.write(temporary_descriptor, encoded[offset:])
                os.fsync(temporary_descriptor)
            finally:
                os.close(temporary_descriptor)
            if not _parent_is_still_current(path, transaction.parent_identity):
                raise OSError("evidence store parent identity changed")
            target_current = _target_stat(parent_descriptor, path.name)
            if not _same_optional_identity(
                target_current, transaction.initial_target_identity
            ):
                raise OSError("evidence store target identity changed")
            if target_before is not None:
                backup_name = f".evidence-backup-{secrets.token_hex(12)}.tmp"
                os.link(
                    path.name,
                    backup_name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            temporary_name = ""
            replaced_target = True
            if not _parent_is_still_current(path, transaction.parent_identity):
                raise OSError("evidence store parent identity changed")
            os.fsync(parent_descriptor)
        except OSError:
            if replaced_target:
                try:
                    if backup_name:
                        os.rename(
                            backup_name,
                            path.name,
                            src_dir_fd=parent_descriptor,
                            dst_dir_fd=parent_descriptor,
                        )
                        backup_name = ""
                    else:
                        os.unlink(path.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
                except OSError:
                    rollback_failed = True
            raise
    except ValueError:
        raise
    except OSError:
        raise ValueError(
            "research evidence_store could not be written safely"
        ) from None
    finally:
        for disposable in (
            temporary_name,
            "" if rollback_failed else backup_name,
        ):
            if disposable:
                try:
                    os.unlink(disposable, dir_fd=parent_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass


def _json_copy(value: object) -> object:
    _validate_safe_value(value)
    return json.loads(
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    )


def _validate_safe_value(value: object) -> None:
    validate_bounded_public_data(
        value,
        artifact="research evidence_store",
        allow_floats=False,
        allow_excluded_raw_marker=True,
        require_ascii_keys=False,
        max_total_string_length=_MAX_TOTAL_STRING_LENGTH,
    )


__all__ = [
    "EvidenceReusePolicy",
    "EvidenceStoreRequest",
    "EvidenceStoreResolution",
    "parse_evidence_store_request",
    "resolve_operation_evidence",
    "validate_operation_audit",
    "validate_plan_audit",
]
