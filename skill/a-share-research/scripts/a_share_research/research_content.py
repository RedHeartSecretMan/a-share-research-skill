"""Research-content process that reconciles heterogeneous material indexes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Collection

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
from .document_validation import (
    DOCUMENT_VALIDATION_OPERATION,
    DocumentTarget,
    DocumentValidationResult,
    DocumentValidator,
)
from .identity_resolution import resolve_security_identity
from .identity_sources import HttpTransport

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


def build_research_content_result(
    request: dict[str, Any],
    operations: Collection[ContentSourceOperation],
    identity_transport: HttpTransport,
    document_transport: ContentHttpTransport | None = None,
) -> dict[str, Any]:
    """Collect, time-bound, and reconcile research materials."""

    query = _normalize_query(request)
    identity = _resolve_subject(request, identity_transport)
    if identity["status"] == "blocked":
        theme_aggregation = _aggregate_investor_qa_themes([], query)
        blocked_brief: dict[str, Any] = {
            "material_count": 0,
            "material_types": list(query.material_types),
            "material_type_counts": {},
        }
        blocked_limitations = list(identity["limitations"])
        if theme_aggregation is not None:
            blocked_brief["theme_aggregation"] = theme_aggregation
            blocked_limitations.append(_unavailable_theme_aggregation_limitation())
        return {
            "schema_version": request["schema_version"],
            "status": "blocked",
            "subjects": [],
            "materials": [],
            "brief": blocked_brief,
            "evidence": identity["evidence"],
            "conflicts": identity["conflicts"],
            "source_errors": identity["source_errors"],
            "degradations": [],
            "limitations": blocked_limitations,
        }
    if identity["subjects"]:
        query = replace(query, subject=identity["subjects"][0])
    selected = [
        operation
        for operation in operations
        if operation.supported_material_types.intersection(query.material_types)
    ]
    source_errors: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    observations: list[ContentObservation] = []
    batch_limitations: set[str] = set()
    for operation in selected:
        batch = operation.collect(query)
        valid_observations, schema_errors = _validate_observation_enums(
            batch.operation_id,
            batch.observations,
        )
        observations.extend(valid_observations)
        source_errors.extend(item.to_result() for item in schema_errors)
        source_errors.extend(item.to_result() for item in batch.source_errors)
        degradations.extend(item.to_result() for item in batch.degradations)
        batch_limitations.update(batch.limitations)

    accepted = [
        observation
        for observation in observations
        if _within_research_window(observation, query)
    ]
    materials = _limit_per_material_type(_deduplicate(accepted), query.limit)
    document_source_errors, document_degradations = _validate_documents(
        materials,
        enabled=query.parameters.get("verify_documents", False),
        transport=document_transport,
    )
    source_errors.extend(document_source_errors)
    degradations.extend(document_degradations)
    material_type_counts: dict[str, int] = {}
    for material in materials:
        material_type = material["material_type"]
        material_type_counts[material_type] = (
            material_type_counts.get(material_type, 0) + 1
        )
    theme_aggregation = _aggregate_investor_qa_themes(materials, query)
    material_limitations = {
        limitation for observation in accepted for limitation in observation.limitations
    }
    limitations: list[dict[str, Any]] = [
        {
            "code": "experimental_research_content_sources",
            "message": (
                "Research content currently uses experimental source operations."
            ),
        }
    ]
    if theme_aggregation is not None and theme_aggregation["status"] == "unavailable":
        limitations.append(_unavailable_theme_aggregation_limitation())
    for code in sorted(batch_limitations | material_limitations):
        limitations.append({"code": code, "message": _limitation_message(code)})
    missing_material_types = sorted(
        set(query.material_types).difference(material_type_counts)
    )
    if missing_material_types:
        limitations.append(
            {
                "code": "requested_material_type_unavailable",
                "message": (
                    "At least one requested material type produced no usable "
                    "observation; this does not prove the material does not exist."
                ),
                "material_types": missing_material_types,
            }
        )
    limitations.extend(identity["limitations"])
    if not materials:
        limitations.append(
            {
                "code": "research_content_unavailable",
                "message": (
                    "No usable material was established for the requested window; "
                    "this does not prove that no such material exists."
                ),
            }
        )

    brief: dict[str, Any] = {
        "material_count": len(materials),
        "material_types": list(query.material_types),
        "material_type_counts": material_type_counts,
    }
    if theme_aggregation is not None:
        brief["theme_aggregation"] = theme_aggregation

    return {
        "schema_version": request["schema_version"],
        "status": "limited" if materials else "blocked",
        "subjects": identity["subjects"],
        "materials": materials,
        "brief": brief,
        "evidence": [
            *identity["evidence"],
            *[_to_evidence(item) for item in _unique_canonical_observations(accepted)],
        ],
        "conflicts": identity["conflicts"],
        "source_errors": [*identity["source_errors"], *source_errors],
        "degradations": degradations,
        "limitations": limitations,
    }


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
    for observation in observations:
        invalid_fields: list[str] = []
        if observation.material_type not in PUBLIC_MATERIAL_TYPES:
            invalid_fields.append("material_type")
        if observation.source_role not in PUBLIC_SOURCE_ROLES:
            invalid_fields.append("source_role")
        if invalid_fields:
            errors.append(
                SourceFailure(
                    source_operation=operation_id,
                    code="unknown_schema",
                    message=(
                        "A source observation uses a value outside the public "
                        "content contract."
                    ),
                    details={"invalid_fields": invalid_fields},
                )
            )
            continue
        accepted.append(observation)
    return accepted, errors


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


def _resolve_subject(
    request: dict[str, Any], transport: HttpTransport
) -> dict[str, Any]:
    subjects = request["subjects"]
    if not subjects:
        return {
            "status": "limited",
            "subjects": [],
            "evidence": [],
            "conflicts": [],
            "source_errors": [],
            "limitations": [],
        }
    subject = subjects[0]
    if not isinstance(subject, dict):
        raise ValueError("research_content subject must be an object")
    clue = subject.get("clue")
    if not isinstance(clue, str) or not clue.strip():
        raise ValueError("research_content subject requires a non-empty clue")
    resolved = resolve_security_identity(clue.strip(), request["as_of"], transport)
    candidates_value = resolved.get("candidates", [])
    candidates = candidates_value if isinstance(candidates_value, list) else []
    if resolved["status"] == "blocked" or len(candidates) != 1:
        return {
            "status": "blocked",
            "subjects": [],
            "evidence": resolved.get("evidence", []),
            "conflicts": resolved.get("conflicts", []),
            "source_errors": resolved.get("source_errors", []),
            "limitations": resolved.get("limitations", []),
        }
    candidate = candidates[0]
    if not isinstance(candidate, dict) or not isinstance(
        candidate.get("security"), dict
    ):
        raise ValueError("identity result does not contain a canonical security")
    security = candidate["security"]
    return {
        "status": resolved["status"],
        "subjects": [
            {
                "security": {
                    "exchange": security["exchange"],
                    "code": security["code"],
                    "type": security["type"],
                },
                "name": candidate["name"],
                "issuer": candidate["issuer"],
            }
        ],
        "evidence": resolved.get("evidence", []),
        "conflicts": resolved.get("conflicts", []),
        "source_errors": resolved.get("source_errors", []),
        "limitations": resolved.get("limitations", []),
    }


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


def _deduplicate(observations: list[ContentObservation]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for observation in sorted(
        observations,
        key=lambda item: (
            item.published_at or item.retrieved_at.isoformat(),
            item.source_document_id or "",
            item.source_operation,
        ),
        reverse=True,
    ):
        key = _deduplication_key(observation)
        material = selected.get(key)
        if material is None:
            selected[key] = _to_material(observation)
            continue
        source_observation = _source_observation(observation)
        if source_observation in material["source_observations"]:
            continue
        if (
            observation.source_operation != material["source_operation"]
            and observation.source_operation not in material["duplicate_sources"]
        ):
            material["duplicate_sources"].append(observation.source_operation)
        material["source_observations"].append(source_observation)
        material["metadata_conflicts"] = _metadata_conflicts(
            material["source_observations"]
        )
    return list(selected.values())


def _deduplication_key(observation: ContentObservation) -> tuple[str, str, str]:
    return _canonical_observation_key(observation)


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


def _unique_canonical_observations(
    observations: list[ContentObservation],
) -> list[ContentObservation]:
    selected: dict[tuple[str, str, str], ContentObservation] = {}
    for observation in observations:
        selected.setdefault(_canonical_observation_key(observation), observation)
    return list(selected.values())


def _limit_per_material_type(
    materials: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    for material in materials:
        material_type = material["material_type"]
        count = counts.get(material_type, 0)
        if count >= limit:
            continue
        selected.append(material)
        counts[material_type] = count + 1
    return selected


def _aggregate_investor_qa_themes(
    materials: list[dict[str, Any]], query: ContentQuery
) -> dict[str, Any] | None:
    if "investor_qa" not in query.material_types:
        return None
    requested_keywords = query.parameters.get("theme_keywords", [])
    theme_keywords = requested_keywords if isinstance(requested_keywords, list) else []
    unique_keywords: dict[str, str] = {}
    for keyword in theme_keywords:
        if not isinstance(keyword, str):
            continue
        normalized = keyword.casefold()
        unique_keywords.setdefault(normalized, keyword)
    themes: list[dict[str, Any]] = []
    for normalized, keyword in unique_keywords.items():
        mention_count = 0
        material_count = 0
        matching_materials: list[str] = []
        for material in materials:
            if material.get("material_type") != "investor_qa":
                continue
            material_mentions = sum(
                value.casefold().count(normalized)
                for field in ("title", "summary", "content")
                if isinstance((value := material.get(field)), str)
            )
            if not material_mentions:
                continue
            mention_count += material_mentions
            material_count += 1
            source_document_id = material.get("source_document_id")
            if isinstance(source_document_id, str):
                matching_materials.append(source_document_id)
        if mention_count:
            themes.append(
                {
                    "theme": keyword,
                    "mention_count": mention_count,
                    "material_count": material_count,
                    "source_document_ids": sorted(matching_materials),
                }
            )
    themes.sort(
        key=lambda item: (
            -item["mention_count"],
            -item["material_count"],
            item["theme"].casefold(),
        )
    )
    return {
        "status": "available" if themes else "unavailable",
        "method": "theme_keyword_literal_frequency",
        "audited_fields": ["title", "summary", "content"],
        "themes": themes,
    }


def _unavailable_theme_aggregation_limitation() -> dict[str, str]:
    return {
        "code": "investor_qa_theme_aggregation_unavailable",
        "message": (
            "Investor Q&A themes could not be aggregated reliably because no "
            "requested theme keyword had an auditable literal match."
        ),
    }


def _to_material(observation: ContentObservation) -> dict[str, Any]:
    return {
        "material_type": observation.material_type,
        "source_document_namespace": observation.source_document_namespace,
        "source_document_id": observation.source_document_id,
        "title": observation.title,
        "published_at": observation.published_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "source_role": observation.source_role,
        "claim_eligibility": "experimental_observation_only",
        "source_operation": observation.source_operation,
        "subject": observation.subject,
        "author": observation.author,
        "summary": observation.summary,
        "content": observation.content,
        "locator": {"uri": observation.locator_uri},
        "document_locator": observation.document_locator,
        "attributes": observation.attributes,
        "duplicate_sources": [],
        "source_observations": [_source_observation(observation)],
        "metadata_conflicts": [],
        "limitations": list(observation.limitations),
    }


def _source_observation(observation: ContentObservation) -> dict[str, Any]:
    return {
        "source_operation": observation.source_operation,
        "source_document_namespace": observation.source_document_namespace,
        "source_document_id": observation.source_document_id,
        "title": observation.title,
        "author": observation.author,
        "source_role": observation.source_role,
        "published_at": observation.published_at,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "locator": {"uri": observation.locator_uri},
        "document_locator": observation.document_locator,
        "attributes": observation.attributes,
        "limitations": list(observation.limitations),
    }


def _metadata_conflicts(
    source_observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for field in (
        "title",
        "author",
        "source_role",
        "published_at",
        "retrieved_at",
        "locator",
        "document_locator",
        "attributes",
        "limitations",
    ):
        values = [observation[field] for observation in source_observations]
        if all(value == values[0] for value in values[1:]):
            continue
        conflicts.append(
            {
                "field": field,
                "observations": [
                    {
                        "source_operation": observation["source_operation"],
                        "value": observation[field],
                    }
                    for observation in source_observations
                ],
            }
        )
    return conflicts


def _validate_documents(
    materials: list[dict[str, Any]],
    *,
    enabled: bool,
    transport: ContentHttpTransport | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_errors: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    for material in materials:
        material["document_validation"] = {
            "status": "not_requested",
            "sources": [],
        }
    if not enabled:
        return source_errors, degradations
    if transport is None:
        failure = {
            "source_operation": DOCUMENT_VALIDATION_OPERATION,
            "code": "document_validation_unavailable",
            "message": "No document-validation transport is available.",
        }
        source_errors.append(failure)
        for material in materials:
            material["document_validation"] = {
                "status": "failed",
                "sources": [],
            }
        return source_errors, degradations

    validator = DocumentValidator(transport)
    for material in materials:
        results: list[DocumentValidationResult] = []
        unavailable_count = 0
        source_observations = material.get("source_observations", [])
        if not isinstance(source_observations, list):
            source_observations = []
        for source_observation in source_observations:
            if not isinstance(source_observation, dict):
                continue
            source_operation = source_observation.get("source_operation")
            document_locator = source_observation.get("document_locator")
            if not isinstance(source_operation, str):
                continue
            if not isinstance(document_locator, str) or not document_locator:
                unavailable_count += 1
                degradation = {
                    "source_operation": DOCUMENT_VALIDATION_OPERATION,
                    "code": "document_locator_unavailable",
                    "message": (
                        "The selected material has no source-provided document locator."
                    ),
                    "material_source_operation": source_operation,
                    "source_document_id": material.get("source_document_id"),
                }
                degradations.append(degradation)
                source_observation["document_validation"] = {
                    "status": "unavailable",
                    "validation_operation": DOCUMENT_VALIDATION_OPERATION,
                    "source_operation": source_operation,
                    "source_document_id": material.get("source_document_id"),
                }
                continue
            result = validator.validate(
                DocumentTarget(
                    material_source_operation=source_operation,
                    source_document_id=(
                        material["source_document_id"]
                        if isinstance(material.get("source_document_id"), str)
                        else None
                    ),
                    locator_uri=document_locator,
                )
            )
            results.append(result)
            source_observation["document_validation"] = result.to_result()
            if result.source_error is not None:
                source_errors.append(result.source_error.to_result())
            if result.degradation is not None:
                degradations.append(result.degradation.to_result())
        statuses = [result.status for result in results]
        if (
            statuses
            and all(item == "verified" for item in statuses)
            and not unavailable_count
        ):
            status = "verified"
        elif "verified" in statuses or "partial" in statuses:
            status = "partial"
        elif "failed" in statuses:
            status = "failed"
        else:
            status = "unavailable"
        material["document_validation"] = {
            "status": status,
            "sources": [result.to_result() for result in results],
        }
    return source_errors, degradations


def _to_evidence(observation: ContentObservation) -> dict[str, Any]:
    canonical_identity = json.dumps(
        _canonical_observation_key(observation),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    evidence_identity = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return {
        "id": f"content-{evidence_identity}",
        "source_role": observation.source_role,
        "source_operation": observation.source_operation,
        "experimental": True,
        "subject": observation.subject,
        "observation": {
            "kind": observation.material_type,
            "source_document_namespace": observation.source_document_namespace,
            "source_document_id": observation.source_document_id,
            "title": observation.title,
        },
        "evidence_time": observation.published_at,
        "available_at": (
            observation.published_at
            if observation.published_at is not None
            else observation.retrieved_at.isoformat()
        ),
        "retrieved_at": observation.retrieved_at.isoformat(),
        "locator": {"uri": observation.locator_uri},
        "limitations": list(observation.limitations),
    }


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
    }.get(code, "The material carries a source-specific limitation.")
