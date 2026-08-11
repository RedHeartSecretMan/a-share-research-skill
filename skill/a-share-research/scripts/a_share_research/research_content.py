"""Research-content process that reconciles heterogeneous material indexes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Collection, Mapping, cast

from .content_contract import (
    PUBLIC_MATERIAL_TYPES,
    PUBLIC_SOURCE_ROLES,
    ContentHttpTransport,
    ContentObservation,
    ContentQuery,
    ContentSourceOperation,
    SourceFailure,
    valid_f10_profile_categories,
)
from .identity_resolution import execute_security_identity_research
from .identity_sources import HttpTransport, build_default_identity_operations
from .research_result import (
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
    safe_public_locator,
)

MATERIAL_TYPES = PUBLIC_MATERIAL_TYPES
SUBJECT_REQUIRED_TYPES = frozenset(
    {
        "consensus_material",
        "issuer_profile",
        "stock_news",
        "announcement",
        "investor_qa",
    }
)
_CONTENT_CAPABILITY = "research_content_materials"
_CONTENT_EVIDENCE_USE = "research_content_material"


def build_research_content_blocked_result(
    request: dict[str, Any],
    operations: Collection[ContentSourceOperation],
) -> UnifiedResearchResult:
    """Return the formal result when policy prevents content acquisition."""

    query = _normalize_query(request)
    operation_list = tuple(operations)
    _validate_and_order_content_operations(operation_list, query)
    selected = {
        operation.operation_id
        for operation in operation_list
        if operation.supported_material_types.intersection(query.material_types)
    }
    if not selected:
        raise ValueError("research_content has no applicable source operation")
    obligations = tuple(_content_obligation(operation) for operation in operation_list)
    outcomes = tuple(
        _content_skipped_outcome(
            operation,
            "skipped_policy" if operation.operation_id in selected else "not_requested",
        )
        for operation in operation_list
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="research_content",
            as_of=str(request["as_of"]),
            obligations=obligations,
            dependencies=(
                ResearchDependency(
                    consumer_id="research_content_material",
                    evidence_use=_CONTENT_EVIDENCE_USE,
                    minimum_supporting_observations=1,
                    core=True,
                ),
            ),
        ),
        outcomes,
        limitations=(
            DirectTaskLimitation(
                "source_policy_not_satisfied",
                "research_content currently requires experimental source operations",
            ),
        ),
        summary="Research-content acquisition was blocked by source policy.",
    )


def build_research_content_result(
    request: dict[str, Any],
    operations: Collection[ContentSourceOperation],
    identity_transport: HttpTransport,
    document_transport: ContentHttpTransport | None = None,
) -> UnifiedResearchResult:
    """Collect, time-bound, and reconcile research materials."""

    del document_transport
    query = _normalize_query(request)
    operation_list = tuple(operations)
    execution_operations = _validate_and_order_content_operations(operation_list, query)
    identity_obligations: tuple[OperationObligation, ...] = ()
    identity_outcomes: tuple[OperationOutcome, ...] = ()
    identity_claims: tuple[DirectTaskClaim, ...] = ()
    identity_dependencies: tuple[ResearchDependency, ...] = ()
    identity_limitations: tuple[DirectTaskLimitation, ...] = ()
    identity_blocked = False
    if query.subject is not None:
        clue = query.subject.get("clue")
        if not isinstance(clue, str) or not clue.strip():
            raise ValueError("research_content subject requires a non-empty clue")
        identity_request: dict[str, object] = {
            **request,
            "task_type": "security_identity",
            "subjects": [{"clue": clue.strip()}],
            "window": None,
            "parameters": {},
        }
        identity_execution = execute_security_identity_research(
            identity_request,
            build_default_identity_operations(identity_transport),
        )
        identity_obligations = identity_execution.plan.obligations
        identity_outcomes = identity_execution.outcomes
        material = identity_execution.material
        identity_limitations = tuple(
            DirectTaskLimitation(item.code, item.message)
            for item in material.limitations
        )
        canonical = material.canonical_identity
        identity_blocked = material.status == "blocked" or canonical is None
        if not identity_blocked and canonical is not None:
            canonical_subject = {
                "security": {
                    "exchange": canonical.exchange,
                    "code": canonical.code,
                    "type": canonical.security_type,
                },
                "name": canonical.name,
                "issuer": {
                    "name": canonical.issuer_name,
                    "identifier": canonical.issuer_identifier,
                    "security_relationship": canonical.issuer_relationship,
                },
            }
            query = replace(query, subject=canonical_subject)
            identity_claims = (
                DirectTaskClaim(
                    kind="research_content_subject_identity",
                    evidence_use="security_identity",
                    observation_keys=material.observation_keys,
                    payload=canonical_subject,
                ),
            )
        if identity_obligations:
            identity_dependencies = (
                ResearchDependency(
                    consumer_id="research_content_subject_identity",
                    evidence_use="security_identity",
                    minimum_supporting_observations=1,
                    core=True,
                ),
            )
    outcomes: list[OperationOutcome] = []
    accepted_by_operation: dict[str, list[tuple[ContentObservation, str]]] = {}
    owned_material_count = 0
    limitation_codes: set[str] = {"experimental_research_content_sources"}
    for operation in execution_operations:
        if not operation.supported_material_types.intersection(query.material_types):
            outcomes.append(_content_skipped_outcome(operation, "not_requested"))
            continue
        if identity_blocked:
            outcomes.append(
                _content_skipped_outcome(operation, "skipped_research_dependency")
            )
            continue
        fallback_for = getattr(operation, "fallback_for", None)
        if fallback_for is not None:
            if not query.allow_fallback or accepted_by_operation.get(fallback_for):
                outcomes.append(_content_skipped_outcome(operation, "not_requested"))
                continue
        try:
            batch = operation.collect(query)
        except Exception:
            outcomes.append(
                _content_failed_outcome(
                    operation,
                    SourceFailure(
                        operation.operation_id,
                        "operation_failure",
                        "The source operation failed without a safe diagnostic.",
                    ),
                )
            )
            continue
        if batch.operation_id != operation.operation_id:
            outcomes.append(
                _content_failed_outcome(
                    operation,
                    SourceFailure(
                        operation.operation_id,
                        "operation_owner_mismatch",
                        "The source batch contradicted its operation owner.",
                    ),
                )
            )
            continue
        valid, schema_errors = _validate_observation_enums(
            operation.operation_id, batch.observations
        )
        owned = [
            (item, _content_observation_key(item, index))
            for index, item in enumerate(valid)
        ]
        accepted = [
            (item, key) for item, key in owned if _within_research_window(item, query)
        ]
        accepted_by_operation[operation.operation_id] = accepted
        owned_material_count += len(valid)
        errors = (*schema_errors, *batch.source_errors)
        outcomes.append(
            _content_outcome(
                operation,
                observations=tuple(
                    _content_observation_payload(item, index, query)
                    for index, item in enumerate(valid)
                ),
                errors=tuple(errors),
                query_complete=batch.complete,
            )
        )
        limitation_codes.update(batch.limitations)
        limitation_codes.update(code for item in valid for code in item.limitations)
        limitation_codes.update(item.code for item in batch.degradations)

    accepted = [
        item
        for operation in operation_list
        for item in accepted_by_operation.get(operation.operation_id, ())
    ]
    claims, conflicts, selected_materials = _content_material_groups(accepted, query)
    present_types = {item.material_type for item in selected_materials}
    missing_types = sorted(set(query.material_types).difference(present_types))
    limitations = [
        DirectTaskLimitation(code, _limitation_message(code))
        for code in sorted(limitation_codes)
    ]
    if missing_types:
        limitations.append(
            DirectTaskLimitation(
                "requested_material_type_unavailable",
                "At least one requested material type produced no usable observation.",
            )
        )
    if not selected_materials:
        limitations.append(
            DirectTaskLimitation(
                "research_content_unavailable",
                "No usable material was established for the requested window.",
            )
        )
    obligations = (
        *identity_obligations,
        *(tuple(_content_obligation(item) for item in operation_list)),
    )
    outcomes_by_operation = {item.operation_id: item for item in outcomes}
    ordered_outcomes = tuple(
        outcomes_by_operation[operation.operation_id] for operation in operation_list
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="research_content",
            as_of=str(request["as_of"]),
            obligations=obligations,
            dependencies=(
                *identity_dependencies,
                *(
                    (
                        ResearchDependency(
                            consumer_id="research_content_material_group",
                            evidence_use=_CONTENT_EVIDENCE_USE,
                            minimum_supporting_observations=1,
                            core=True,
                        ),
                    )
                    if operation_list
                    else ()
                ),
            ),
        ),
        (*identity_outcomes, *ordered_outcomes),
        claims=(*identity_claims, *claims),
        conflicts=conflicts,
        limitations=(*identity_limitations, *tuple(limitations)),
        summary=(
            f"Research content retained {owned_material_count} operation-owned material "
            f"observation(s) in {len(selected_materials)} reversible group(s)."
        ),
    )


def _content_obligation(operation: ContentSourceOperation) -> OperationObligation:
    return OperationObligation(
        capability_id=_CONTENT_CAPABILITY,
        operation_id=operation.operation_id,
        source_role="declared_per_observation",
        experimental=True,
        exchange=None,
        evidence_uses=(EvidenceUsePolicy(_CONTENT_EVIDENCE_USE, "supporting"),),
    )


def _validate_and_order_content_operations(
    operations: tuple[ContentSourceOperation, ...],
    query: ContentQuery,
) -> tuple[ContentSourceOperation, ...]:
    operations_by_id = {operation.operation_id: operation for operation in operations}
    if len(operations_by_id) != len(operations):
        raise ValueError("content operation IDs must be unique")
    fallback_edges: dict[str, str] = {}
    for operation in operations:
        _content_dispositions(operation)
        fallback_for = getattr(operation, "fallback_for", None)
        if fallback_for is None:
            continue
        primary = (
            operations_by_id.get(fallback_for)
            if isinstance(fallback_for, str)
            else None
        )
        if primary is None:
            raise ValueError("content fallback primary is missing")
        if primary.supported_material_types.intersection(
            query.material_types
        ) != operation.supported_material_types.intersection(query.material_types):
            raise ValueError("content fallback scope is incompatible with its primary")
        fallback_edges[operation.operation_id] = fallback_for

    ordered: list[ContentSourceOperation] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(operation_id: str) -> None:
        if operation_id in visited:
            return
        if operation_id in visiting:
            raise ValueError("content fallback graph contains a cycle")
        visiting.add(operation_id)
        primary_id = fallback_edges.get(operation_id)
        if primary_id is not None:
            visit(primary_id)
        visiting.remove(operation_id)
        visited.add(operation_id)
        ordered.append(operations_by_id[operation_id])

    for operation in operations:
        visit(operation.operation_id)
    return tuple(ordered)


def _content_dispositions(
    operation: ContentSourceOperation,
) -> tuple[FieldDisposition, ...]:
    try:
        raw_fields = operation.raw_fields
    except AttributeError as error:
        raise ValueError(
            "content operation raw_fields must be a unique non-empty tuple"
        ) from error
    if (
        not isinstance(raw_fields, tuple)
        or not raw_fields
        or any(not isinstance(item, str) or not item for item in raw_fields)
        or len(set(raw_fields)) != len(raw_fields)
    ):
        raise ValueError(
            "content operation raw_fields must be a unique non-empty tuple of text"
        )
    return tuple(
        _content_field_disposition(operation.operation_id, field)
        for field in raw_fields
    )


def _content_field_disposition(operation_id: str, field: str) -> FieldDisposition:
    leaf = field.rsplit(".", 1)[-1].removesuffix("[]").casefold()
    if field.endswith("[]"):
        return FieldDisposition(
            field,
            "excluded",
            None,
            "Provider collection containers are not retained in the public result.",
        )
    destinations = {
        "report_id": ("normalized", "observations[].payload.source_document_id"),
        "infocode": ("normalized", "observations[].payload.source_document_id"),
        "uid": ("normalized", "observations[].payload.source_document_id"),
        "announcementid": (
            "normalized",
            "observations[].payload.source_document_id",
        ),
        "annid": ("normalized", "observations[].payload.source_document_id"),
        "indexid": ("normalized", "observations[].payload.source_document_id"),
        "title": ("normalized", "observations[].payload.title"),
        "announcementtitle": ("normalized", "observations[].payload.title"),
        "bulletin_heading": ("normalized", "observations[].payload.title"),
        "published_at": ("normalized", "observations[].payload.published_at"),
        "publishdate": ("normalized", "observations[].payload.published_at"),
        "publish_date": (
            "normalized",
            "observations[].payload.attributes.publication_date",
        ),
        "date": ("normalized", "observations[].payload.published_at"),
        "announcementtime": ("normalized", "observations[].payload.published_at"),
        "publishtime": ("normalized", "observations[].payload.published_at"),
        "pubdate": ("normalized", "observations[].payload.published_at"),
        "showtime": ("normalized", "observations[].payload.published_at"),
        "ctime": ("normalized", "observations[].payload.published_at"),
        "attachedpubdate": ("normalized", "observations[].payload.published_at"),
        "adddate": ("normalized", "observations[].payload.available_at"),
        "retrieved_at": ("lineage_only", "observations[].payload.retrieved_at"),
        "summary": ("normalized", "observations[].payload.summary"),
        "brief": ("normalized", "observations[].payload.summary"),
        "content": ("normalized", "observations[].payload.content"),
        "maincontent": ("normalized", "observations[].payload.content"),
        "attachedcontent": ("normalized", "observations[].payload.content"),
        "locator": ("lineage_only", "observations[].payload.locator.uri"),
        "url": ("lineage_only", "observations[].payload.locator.uri"),
        "source_uri": ("lineage_only", "observations[].payload.locator.uri"),
        "shareurl": ("lineage_only", "observations[].payload.locator.uri"),
        "adjuncturl": (
            "lineage_only",
            "observations[].payload.document_locator.uri",
        ),
        "attachpath": (
            "lineage_only",
            "observations[].payload.document_locator.uri",
        ),
        "medianame": ("normalized", "observations[].payload.author"),
        "authorname": ("normalized", "observations[].payload.author"),
        "attachedauthor": ("normalized", "observations[].payload.author"),
        "orgsname": ("normalized", "observations[].payload.author"),
        "organization": ("normalized", "observations[].payload.author"),
        "category": ("normalized", "observations[].payload.attributes.category"),
        "companyshortname": (
            "cross_check_only",
            "observations[].payload.subject.name",
        ),
        "secname": ("cross_check_only", "observations[].payload.subject.name"),
        "security_name": (
            "cross_check_only",
            "observations[].payload.subject.name",
        ),
        "stockcode": (
            "cross_check_only",
            "observations[].payload.subject.security.code",
        ),
        "seccode": (
            "cross_check_only",
            "observations[].payload.subject.security.code",
        ),
        "security_code": (
            "cross_check_only",
            "observations[].payload.subject.security.code",
        ),
    }
    if leaf in {"code", "id"} and any(
        marker in field.casefold()
        for marker in ("cmsarticlewebold", "fastnewslist", "roll_data")
    ):
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.source_document_id",
        )
    if field.startswith("forecasts[]."):
        return FieldDisposition(
            field,
            "normalized",
            "observations[].payload.attributes.forecasts",
        )
    retained = destinations.get(leaf)
    if retained is not None:
        disposition, destination = retained
        return FieldDisposition(field, cast(Any, disposition), destination)
    if any(
        marker in leaf
        for marker in (
            "page",
            "total",
            "count",
            "size",
            "index",
            "hasmore",
            "sortend",
        )
    ):
        return FieldDisposition(
            field,
            "cross_check_only",
            f"coverage.research_content_materials.operations.{operation_id}.query_complete",
        )
    return FieldDisposition(
        field,
        "excluded",
        None,
        "The field is used only for source-contract validation and is not retained.",
    )


def _content_skipped_outcome(
    operation: ContentSourceOperation, state: str
) -> OperationOutcome:
    return OperationOutcome(
        capability_id=_CONTENT_CAPABILITY,
        operation_id=operation.operation_id,
        execution=OperationExecution(cast(Any, state)),
        field_dispositions=_content_dispositions(operation),
    )


def _content_failed_outcome(
    operation: ContentSourceOperation, error: SourceFailure
) -> OperationOutcome:
    return _content_outcome(operation, errors=(error,), query_complete=False)


def _content_outcome(
    operation: ContentSourceOperation,
    *,
    observations: tuple[Mapping[str, object], ...] = (),
    errors: tuple[SourceFailure, ...] = (),
    query_complete: bool,
) -> OperationOutcome:
    execution = (
        OperationExecution("failed")
        if errors
        else OperationExecution("succeeded", query_complete=query_complete)
    )
    return OperationOutcome(
        capability_id=_CONTENT_CAPABILITY,
        operation_id=operation.operation_id,
        execution=execution,
        observations=observations,
        source_errors=tuple(item.to_result() for item in errors),
        field_dispositions=_content_dispositions(operation),
    )


def _content_observation_key(observation: ContentObservation, index: int) -> str:
    fingerprint = _observation_fingerprint(observation)[:16]
    return f"{observation.source_operation}:{fingerprint}:{index}"


def _content_observation_payload(
    observation: ContentObservation, index: int, query: ContentQuery
) -> dict[str, object]:
    namespace = _effective_document_namespace(observation)
    document_id = (
        observation.source_document_id or _observation_fingerprint(observation)[:24]
    )
    public_document_key = hashlib.sha256(
        f"{namespace}\0{document_id}".encode("utf-8")
    ).hexdigest()[:24]
    locator_fallback = f"source-document:{public_document_key}"
    document_fallback = f"source-document-content:{public_document_key}"
    license_value = observation.attributes.get("license")
    license_boundary = (
        license_value
        if isinstance(license_value, str) and license_value
        else "not_established"
    )
    return {
        "id": _content_observation_key(observation, index),
        "source_operation": observation.source_operation,
        "kind": "research_content_material",
        "material_type": observation.material_type,
        "query_scope": {
            "as_of": query.as_of,
            "published_from": query.published_from,
            "published_to": query.published_to,
            "material_types": list(query.material_types),
            "limit": query.limit,
        },
        "source_role": observation.source_role,
        "source_document_namespace": namespace,
        "source_document_id": document_id,
        "title": observation.title,
        "published_at": observation.published_at,
        "available_at": (
            observation.published_at
            if observation.published_at is not None
            else observation.retrieved_at.isoformat()
        ),
        "retrieved_at": observation.retrieved_at.isoformat(),
        "locator": {
            "uri": safe_public_locator(observation.locator_uri, locator_fallback)
        },
        "document_locator": (
            {
                "uri": safe_public_locator(
                    observation.document_locator, document_fallback
                )
            }
            if observation.document_locator is not None
            else None
        ),
        "subject": observation.subject,
        "author": observation.author,
        "summary": observation.summary,
        "content": observation.content,
        "attributes": observation.attributes,
        "limitations": list(observation.limitations),
        "license_boundary": {
            "status": license_boundary,
            "raw_payload": "excluded",
        },
    }


def _content_material_groups(
    observations: list[tuple[ContentObservation, str]], query: ContentQuery
) -> tuple[
    tuple[DirectTaskClaim, ...],
    tuple[DirectTaskConflict, ...],
    tuple[ContentObservation, ...],
]:
    grouped: dict[tuple[str, str, str], list[tuple[ContentObservation, str]]] = {}
    for observation, observation_key in observations:
        grouped.setdefault(_canonical_observation_key(observation), []).append(
            (observation, observation_key)
        )
    counts: dict[str, int] = {}
    selected: list[ContentObservation] = []
    claims: list[DirectTaskClaim] = []
    conflicts: list[DirectTaskConflict] = []
    for group_key, members in grouped.items():
        material_type = group_key[0]
        if counts.get(material_type, 0) >= query.limit:
            continue
        counts[material_type] = counts.get(material_type, 0) + 1
        selected.append(members[0][0])
        keys = tuple(item[1] for item in members)
        claims.append(
            DirectTaskClaim(
                kind="research_content_material_group",
                evidence_use=_CONTENT_EVIDENCE_USE,
                observation_keys=keys,
                payload={
                    "material_type": material_type,
                    "source_document_namespace": group_key[1],
                    "source_document_id": (
                        members[0][0].source_document_id
                        or _observation_fingerprint(members[0][0])[:24]
                    ),
                    "member_evidence_ids": list(keys),
                    "member_count": len(keys),
                },
            )
        )
        comparable = {
            (
                item[0].title,
                item[0].author,
                item[0].published_at,
                item[0].summary,
            )
            for item in members
        }
        if len(comparable) > 1:
            conflicts.append(
                DirectTaskConflict(
                    "research_content_metadata_conflict",
                    "Grouped source members report different material metadata.",
                    keys,
                )
            )
    return tuple(claims), tuple(conflicts), tuple(selected)


def _normalize_query(request: dict[str, Any]) -> ContentQuery:
    parameters = request["parameters"]
    material_types = parameters.get("material_types")
    if (
        not isinstance(material_types, list)
        or not material_types
        or any(not isinstance(item, str) or not item for item in material_types)
    ):
        raise ValueError(
            "research_content parameters.material_types must be a non-empty string array"
        )
    keywords = parameters.get("query", [])
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, list) or any(
        not isinstance(item, str) or not item.strip() for item in keywords
    ):
        raise ValueError("research_content parameters.query must contain strings")
    theme_keywords = parameters.get("theme_keywords")
    normalized_parameters = dict(parameters)
    if "profile_categories" in parameters:
        profile_categories = parameters["profile_categories"]
        if not valid_f10_profile_categories(profile_categories):
            raise ValueError(
                "invalid_request: research_content parameters.profile_categories "
                "must contain 1 to 9 unique, documented F10 categories"
            )
    if theme_keywords is not None:
        if (
            not isinstance(theme_keywords, list)
            or not 1 <= len(theme_keywords) <= 20
            or any(
                not isinstance(item, str) or not item.strip() for item in theme_keywords
            )
        ):
            raise ValueError(
                "research_content parameters.theme_keywords must contain 1 to 20 "
                "non-empty strings"
            )
        normalized_theme_keywords = [item.strip() for item in theme_keywords]
        if len({item.casefold() for item in normalized_theme_keywords}) != len(
            normalized_theme_keywords
        ):
            raise ValueError(
                "research_content parameters.theme_keywords must be unique"
            )
        normalized_parameters["theme_keywords"] = normalized_theme_keywords
    limit = parameters.get("limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("research_content parameters.limit must be from 1 to 100")
    verify_documents = parameters.get("verify_documents", False)
    if not isinstance(verify_documents, bool):
        raise ValueError("research_content parameters.verify_documents must be boolean")
    window = request["window"]
    if not isinstance(window, dict):
        raise ValueError("research_content window must be an object")
    published_from = _explicit_date(window.get("published_from"), "published_from")
    published_to = _explicit_date(window.get("published_to"), "published_to")
    if published_from > published_to:
        raise ValueError("research_content window starts after it ends")
    if published_to > request["as_of"]:
        raise ValueError("research_content window exceeds the research date")
    subject = request["subjects"][0] if len(request["subjects"]) == 1 else None
    if len(request["subjects"]) > 1:
        raise ValueError("research_content accepts at most one subject")
    normalized_types = tuple(dict.fromkeys(material_types))
    _validate_material_scope(
        normalized_types,
        subject,
        tuple(item.strip() for item in keywords),
        normalized_parameters,
    )
    return ContentQuery(
        material_types=normalized_types,
        keywords=tuple(item.strip() for item in keywords),
        as_of=request["as_of"],
        published_from=published_from,
        published_to=published_to,
        limit=limit,
        subject=subject,
        parameters=normalized_parameters,
        allow_credentials=request["source_policy"]["allow_credentials"],
        allow_fallback=request["source_policy"]["allow_fallback"],
    )


def _validate_observation_enums(
    operation_id: str,
    observations: tuple[ContentObservation, ...],
) -> tuple[list[ContentObservation], list[SourceFailure]]:
    accepted: list[ContentObservation] = []
    errors: list[SourceFailure] = []
    seen_errors: set[tuple[str, tuple[str, ...]]] = set()
    for observation in observations:
        invalid_fields: list[str] = []
        error_code = "unknown_schema"
        if observation.source_operation != operation_id:
            invalid_fields.append("source_operation")
            error_code = "operation_owner_mismatch"
        if observation.material_type not in PUBLIC_MATERIAL_TYPES:
            invalid_fields.append("material_type")
        if observation.source_role not in PUBLIC_SOURCE_ROLES:
            invalid_fields.append("source_role")
        invalid_time_fields = _invalid_observation_time_fields(observation)
        if invalid_time_fields:
            invalid_fields.extend(invalid_time_fields)
            if error_code != "operation_owner_mismatch":
                error_code = "invalid_observation_time"
        if invalid_fields:
            error_key = (error_code, tuple(invalid_fields))
            if error_key in seen_errors:
                continue
            seen_errors.add(error_key)
            errors.append(
                SourceFailure(
                    source_operation=operation_id,
                    code=error_code,
                    message=(
                        "A source observation uses a value outside the public "
                        "content contract: " + ", ".join(invalid_fields) + "."
                    ),
                    details={"invalid_fields": invalid_fields},
                )
            )
            continue
        accepted.append(observation)
    return accepted, errors


def _invalid_observation_time_fields(
    observation: ContentObservation,
) -> list[str]:
    invalid_fields: list[str] = []
    retrieved_is_aware = observation.retrieved_at.utcoffset() is not None
    if not retrieved_is_aware:
        invalid_fields.append("retrieved_at")
    if observation.published_at is not None:
        try:
            published = datetime.fromisoformat(observation.published_at)
        except ValueError:
            published = None
        if (
            published is None
            or published.utcoffset() is None
            or retrieved_is_aware
            and published > observation.retrieved_at
        ):
            invalid_fields.append("published_at")
    elif "publication_date" in observation.attributes:
        publication_date = _strict_publication_date(
            observation.attributes["publication_date"]
        )
        if (
            publication_date is None
            or retrieved_is_aware
            and publication_date > observation.retrieved_at.date().isoformat()
        ):
            invalid_fields.append("attributes.publication_date")
    elif "publication_time_unknown" not in observation.limitations:
        invalid_fields.append("published_at")
    return invalid_fields


def _validate_material_scope(
    material_types: tuple[str, ...],
    subject: object,
    keywords: tuple[str, ...],
    parameters: dict[str, Any],
) -> None:
    unknown = sorted(set(material_types).difference(MATERIAL_TYPES))
    if unknown:
        raise ValueError(
            "research_content has unsupported material_types: " + ", ".join(unknown)
        )
    selected = set(material_types)
    if "market_flash" in selected and (subject is not None or len(selected) != 1):
        raise ValueError(
            "market_flash must be requested without a subject or other types"
        )
    if selected.intersection(SUBJECT_REQUIRED_TYPES) and subject is None:
        raise ValueError("the requested research_content material requires one subject")
    if "industry_report" in selected:
        industry_code = parameters.get("industry_code")
        if (
            subject is not None
            or not isinstance(industry_code, str)
            or not industry_code
        ):
            raise ValueError(
                "industry_report requires parameters.industry_code and no subject"
            )
    if "research_report" in selected and subject is None and not keywords:
        raise ValueError("a theme research_report requires query keywords")


def _explicit_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"research_content {field} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"research_content {field} must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"research_content {field} must use YYYY-MM-DD")
    return value


def _within_research_window(
    observation: ContentObservation, query: ContentQuery
) -> bool:
    if observation.retrieved_at.utcoffset() is None:
        return False
    if observation.published_at is None:
        retrieved_date = observation.retrieved_at.date().isoformat()
        if "publication_date" in observation.attributes:
            publication_date = _strict_publication_date(
                observation.attributes["publication_date"]
            )
            return (
                publication_date is not None
                and publication_date <= retrieved_date
                and query.published_from <= publication_date <= query.published_to
            )
        return (
            "publication_time_unknown" in observation.limitations
            and query.published_from <= retrieved_date <= query.published_to
        )
    try:
        published = datetime.fromisoformat(observation.published_at)
    except ValueError:
        return False
    if published.utcoffset() is None or published > observation.retrieved_at:
        return False
    published_date = published.date().isoformat()
    return query.published_from <= published_date <= query.published_to


def _strict_publication_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _canonical_observation_key(
    observation: ContentObservation,
) -> tuple[str, str, str]:
    if observation.source_document_id is not None:
        return (
            observation.material_type,
            _effective_document_namespace(observation),
            f"document:{observation.source_document_id}",
        )
    return (
        observation.material_type,
        _effective_document_namespace(observation),
        f"observation:{_observation_fingerprint(observation)}",
    )


def _effective_document_namespace(observation: ContentObservation) -> str:
    namespace = observation.source_document_namespace
    if isinstance(namespace, str) and namespace.strip():
        return namespace
    return f"operation:{observation.source_operation}"


def _observation_fingerprint(observation: ContentObservation) -> str:
    payload = {
        "material_type": observation.material_type,
        "source_operation": observation.source_operation,
        "source_role": observation.source_role,
        "title": observation.title,
        "published_at": observation.published_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "locator_uri": observation.locator_uri,
        "subject": observation.subject,
        "author": observation.author,
        "summary": observation.summary,
        "content": observation.content,
        "document_locator": observation.document_locator,
        "attributes": observation.attributes,
        "limitations": observation.limitations,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _limitation_message(code: str) -> str:
    return {
        "publication_time_precision_is_date_only": (
            "The source exposes a publication date but not the exact publication time."
        ),
        "publication_time_timezone_not_explicit": (
            "The source timestamp has second-level precision but no explicit timezone."
        ),
        "pagination_incomplete": (
            "The source did not prove that the requested publication window was complete."
        ),
        "feed_completeness_unproven": (
            "The bounded market feed does not prove complete coverage of the window."
        ),
        "semantic_search_completeness_unproven": (
            "Semantic search results do not prove complete coverage of matching material."
        ),
        "title_keyword_filter_not_semantic_search": (
            "The free theme-report baseline applies exact title substring filtering, "
            "not semantic search."
        ),
        "theme_report_universe_incomplete": (
            "The provider-wide stock-report feed does not prove a complete theme "
            "research universe."
        ),
    }.get(code, "The material carries a source-specific limitation.")
