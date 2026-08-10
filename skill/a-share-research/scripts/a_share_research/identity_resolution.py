"""Cross-source orchestration for fail-closed security identity resolution."""

from __future__ import annotations

from .identity_sources import (
    IDENTITY_CAPABILITY_ID,
    CninfoSecurityDictionaryOperation,
    HttpTransport,
    IdentityObservation,
    IdentityOperationAdapter,
    IdentityOperationRequest,
    SourceOperationError,
    SseStockListOperation,
    SzseStockListOperation,
)
from .research_result import (
    CoverageState,
    OperationOutcome,
    ResearchStatus,
    UnifiedResearchResult,
    stable_result_id,
)


def build_security_identity_research_result(
    request: dict[str, object],
    operations: tuple[IdentityOperationAdapter, ...],
) -> UnifiedResearchResult:
    """Return the direct unified result for one security identity ResearchTask."""

    source_policy = request["source_policy"]
    if not isinstance(source_policy, dict):
        raise ValueError("research source_policy must be a JSON object")
    if source_policy["allow_experimental"] is not False:
        return _execute_identity_research(request, operations)

    outcomes = tuple(
        OperationOutcome(
            capability_id=operation.capability_id,
            operation_id=operation.operation_id,
            coverage="skipped_policy",
            field_dispositions=operation.field_dispositions,
        )
        for operation in operations
    )
    return _assemble_identity_result(
        operations=operations,
        outcomes=outcomes,
        as_of=str(request["as_of"]),
        status="blocked",
        claims=(),
        conflicts=(),
        limitation_payloads=(
            {
                "code": "source_policy_not_satisfied",
                "message": (
                    "security_identity currently requires experimental "
                    "source operations"
                ),
                "references": [],
            },
        ),
    )


def _execute_identity_research(
    request: dict[str, object], operations: tuple[IdentityOperationAdapter, ...]
) -> UnifiedResearchResult:
    subjects = request["subjects"]
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise ValueError("security_identity requires exactly one subject")
    subject = subjects[0]
    if not isinstance(subject, dict):
        raise ValueError("security_identity subject must be a JSON object")
    clue_value = subject.get("clue")
    if not isinstance(clue_value, str) or not clue_value.strip():
        raise ValueError("security_identity subject requires a non-empty clue")
    query = clue_value.strip()
    exchange_hint, clue = _parse_clue(query)
    if not _is_valid_clue(query, exchange_hint, clue):
        return _identity_result_without_execution(
            operations,
            as_of=str(request["as_of"]),
            code="invalid_security_clue",
            message=(
                "A security clue must be a name, a six-digit code, or an "
                "SSE/SZSE-qualified clue."
            ),
        )
    if exchange_hint == "BSE" or _is_known_bse_code(clue):
        return _identity_result_without_execution(
            operations,
            as_of=str(request["as_of"]),
            code="unsupported_exchange",
            message="BSE securities are not supported and will not be guessed.",
        )

    operation_request = IdentityOperationRequest(
        query=clue, as_of=str(request["as_of"])
    )
    outcomes = tuple(
        operation.execute(operation_request, timeout_seconds=20.0)
        for operation in operations
    )
    outcomes = _discover_official_identity_from_dictionary(
        as_of=str(request["as_of"]),
        operations=operations,
        outcomes=outcomes,
    )
    return _fold_identity_outcomes(
        request=request,
        exchange_hint=exchange_hint,
        operations=operations,
        outcomes=outcomes,
    )


def _discover_official_identity_from_dictionary(
    *,
    as_of: str,
    operations: tuple[IdentityOperationAdapter, ...],
    outcomes: tuple[OperationOutcome, ...],
) -> tuple[OperationOutcome, ...]:
    by_operation = {outcome.operation_id: outcome for outcome in outcomes}
    official_codes = {
        identity.get("code")
        for operation_id in ("sse_stock_list@1", "szse_stock_list@1")
        for outcome in (
            [by_operation[operation_id]] if operation_id in by_operation else []
        )
        for observation in outcome.observations
        for identity in [_outcome_identity_payload(observation)]
        if isinstance(identity.get("code"), str)
    }
    dictionary = by_operation.get("cninfo_security_dictionary@1")
    if dictionary is None:
        return outcomes
    adapters = {operation.operation_id: operation for operation in operations}
    for observation in dictionary.observations:
        identity = _outcome_identity_payload(observation)
        code = identity.get("code")
        exchange = identity.get("exchange")
        if not isinstance(code, str) or code in official_codes or exchange == "BSE":
            continue
        target_ids = (
            (f"{str(exchange).lower()}_stock_list@1",)
            if exchange in {"SSE", "SZSE"}
            else ("sse_stock_list@1", "szse_stock_list@1")
        )
        for operation_id in target_ids:
            if operation_id not in by_operation or operation_id not in adapters:
                continue
            previous = by_operation[operation_id]
            if previous.coverage == "failed":
                continue
            discovered = adapters[operation_id].execute(
                IdentityOperationRequest(query=code, as_of=as_of),
                timeout_seconds=20.0,
            )
            by_operation[operation_id] = _merge_identity_outcomes(previous, discovered)
            official_codes.update(
                candidate.get("code")
                for item in discovered.observations
                for candidate in [_outcome_identity_payload(item)]
                if isinstance(candidate.get("code"), str)
            )
    return tuple(by_operation[operation.operation_id] for operation in operations)


def _outcome_identity_payload(observation: object) -> dict[str, object]:
    if not isinstance(observation, dict):
        return {}
    identity = observation.get("observation")
    return identity if isinstance(identity, dict) else {}


def _merge_identity_outcomes(
    previous: OperationOutcome, discovered: OperationOutcome
) -> OperationOutcome:
    if (
        previous.capability_id != discovered.capability_id
        or previous.operation_id != discovered.operation_id
    ):
        raise ValueError("cannot merge outcomes from sibling operations")
    observations = tuple((*previous.observations, *discovered.observations))
    source_errors = tuple((*previous.source_errors, *discovered.source_errors))
    coverage: CoverageState
    if source_errors:
        coverage = "partial" if observations else "failed"
    else:
        coverage = "complete" if observations else "observed_empty"
    attempts = _diagnostic_integer(previous.diagnostics.get("attempts")) + (
        _diagnostic_integer(discovered.diagnostics.get("attempts"))
    )
    timeout_seconds = discovered.diagnostics.get(
        "timeout_seconds", previous.diagnostics.get("timeout_seconds", 20.0)
    )
    return OperationOutcome(
        capability_id=previous.capability_id,
        operation_id=previous.operation_id,
        coverage=coverage,
        observations=observations,
        source_errors=source_errors,
        field_dispositions=previous.field_dispositions,
        diagnostics={
            "attempts": attempts,
            "timeout_seconds": timeout_seconds,
        },
    )


def _diagnostic_integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _identity_result_without_execution(
    operations: tuple[IdentityOperationAdapter, ...],
    *,
    as_of: str,
    code: str,
    message: str,
) -> UnifiedResearchResult:
    outcomes = tuple(
        OperationOutcome(
            capability_id=operation.capability_id,
            operation_id=operation.operation_id,
            coverage="skipped_research_dependency",
            field_dispositions=operation.field_dispositions,
        )
        for operation in operations
    )
    return _assemble_identity_result(
        operations=operations,
        outcomes=outcomes,
        as_of=as_of,
        status="blocked",
        claims=(),
        conflicts=(),
        limitation_payloads=({"code": code, "message": message, "references": []},),
    )


def _fold_identity_outcomes(
    *,
    request: dict[str, object],
    exchange_hint: str | None,
    operations: tuple[IdentityOperationAdapter, ...],
    outcomes: tuple[OperationOutcome, ...],
) -> UnifiedResearchResult:
    observation_records, observation_ids_by_operation = _identity_observations(outcomes)

    official_records = [
        item
        for item in observation_records
        if item["operation_id"] in {"sse_stock_list@1", "szse_stock_list@1"}
    ]
    cninfo_records = [
        item
        for item in observation_records
        if item["operation_id"] == "cninfo_security_dictionary@1"
    ]
    cninfo_by_code: dict[str, list[dict[str, object]]] = {}
    for record in cninfo_records:
        identity = _identity_payload(record)
        code = identity.get("code")
        if isinstance(code, str):
            cninfo_by_code.setdefault(code, []).append(record)

    candidates: list[tuple[dict[str, object], tuple[str, ...]]] = []
    conflict_payloads: list[dict[str, object]] = []
    for official_record in official_records:
        official = _identity_payload(official_record)
        code = official.get("code")
        exchange = official.get("exchange")
        if not isinstance(code, str) or not isinstance(exchange, str):
            continue
        same_code_cninfo = cninfo_by_code.get(code, [])
        exchange_conflicts = [
            item
            for item in same_code_cninfo
            if _identity_payload(item).get("exchange") not in {None, exchange}
        ]
        if exchange_conflicts:
            for cninfo_record in exchange_conflicts:
                conflict_payloads.append(
                    {
                        "code": "source_identity_conflict",
                        "message": f"Sources disagree on the exchange for code {code}.",
                        "observation_ids": [
                            official_record["id"],
                            cninfo_record["id"],
                        ],
                    }
                )
            continue
        matching_cninfo = [
            item
            for item in same_code_cninfo
            if _same_identity_payloads(official, _identity_payload(item))
        ]
        if len(matching_cninfo) != 1:
            continue
        cninfo_record = matching_cninfo[0]
        cninfo = _identity_payload(cninfo_record)
        if official.get("name") != cninfo.get("name"):
            conflict_payloads.append(
                {
                    "code": "source_identity_conflict",
                    "message": f"Source names disagree for {exchange}:{code}.",
                    "observation_ids": [
                        official_record["id"],
                        cninfo_record["id"],
                    ],
                }
            )
            continue
        if exchange_hint is not None and exchange != exchange_hint:
            conflict_payloads.append(
                {
                    "code": "exchange_hint_conflict",
                    "message": (
                        "The exchange hint conflicts with the cross-checked identity."
                    ),
                    "observation_ids": [
                        official_record["id"],
                        cninfo_record["id"],
                    ],
                }
            )
            continue
        relationship_verified = bool(official.get("issuer_relationship_verified"))
        candidate = {
            "security": {
                "exchange": exchange,
                "code": code,
                "type": "A_SHARE",
                "validity": {
                    "from": official.get("valid_from"),
                    "to": None,
                    "status": "current",
                    "observed_at": _retrieved_date(official_record),
                },
            },
            "name": official.get("name"),
            "issuer": {
                "name": _observation_subject(official_record).get("issuer"),
                "identifier": None,
                "security_relationship": (
                    "verified" if relationship_verified else "unverified"
                ),
            },
            "identity_status": "cross_checked_experimental",
        }
        candidates.append(
            (
                candidate,
                (
                    str(official_record["id"]),
                    str(cninfo_record["id"]),
                ),
            )
        )

    source_errors_present = any(outcome.source_errors for outcome in outcomes)
    observation_dates = {
        _retrieved_date(item) for item in observation_records if _retrieved_date(item)
    }
    limitations: list[dict[str, object]] = []
    claims: list[dict[str, object]] = []
    status: ResearchStatus = "blocked"
    if observation_dates and observation_dates != {request["as_of"]}:
        limitations.append(
            {
                "code": "identity_observation_outside_research_date",
                "message": (
                    "Current identity observations cannot establish identity for a "
                    "different research date."
                ),
                "references": [str(item["id"]) for item in observation_records],
            }
        )
    elif len(candidates) == 1 and not conflict_payloads:
        candidate, candidate_observation_ids = candidates[0]
        claim_payload: dict[str, object] = {
            "kind": "security_identity",
            "observation_ids": list(candidate_observation_ids),
            "payload": candidate,
        }
        claims.append(
            {
                "id": stable_result_id("claim", claim_payload),
                **claim_payload,
            }
        )
        status = "limited"
        limitations.append(
            {
                "code": "experimental_identity_sources",
                "message": (
                    "Identity was cross-checked using experimental source operations."
                ),
                "references": list(candidate_observation_ids),
            }
        )
        issuer = candidate["issuer"]
        if isinstance(issuer, dict) and issuer["security_relationship"] == "unverified":
            limitations.append(
                {
                    "code": "issuer_relationship_unverified",
                    "message": "The issuer relationship is not independently verified.",
                    "references": list(candidate_observation_ids),
                }
            )
        if source_errors_present:
            limitations.append(
                {
                    "code": "identity_source_partially_failed",
                    "message": (
                        "A sibling identity operation failed; retained identity "
                        "observations remain visible."
                    ),
                    "references": list(candidate_observation_ids),
                }
            )
    elif len(candidates) > 1:
        limitations.append(
            {
                "code": "ambiguous_security_clue",
                "message": "The clue matches multiple cross-checked securities.",
                "references": [str(item["id"]) for item in observation_records],
            }
        )
    elif conflict_payloads:
        limitations.append(
            {
                "code": "identity_conflict",
                "message": "Identity source observations conflict.",
                "references": [str(item["id"]) for item in observation_records],
            }
        )
    elif source_errors_present:
        limitations.append(
            {
                "code": "source_operation_failed",
                "message": "Required identity evidence could not be established.",
                "references": [str(item["id"]) for item in observation_records],
            }
        )
    else:
        limitations.append(
            {
                "code": "identity_not_resolved",
                "message": "No consistent security identity was established.",
                "references": [str(item["id"]) for item in observation_records],
            }
        )

    conflicts = tuple(
        {"id": stable_result_id("conflict", item), **item} for item in conflict_payloads
    )
    return _assemble_identity_result(
        operations=operations,
        outcomes=outcomes,
        as_of=str(request["as_of"]),
        status=status,
        observations=tuple(observation_records),
        observation_ids_by_operation=observation_ids_by_operation,
        claims=tuple(claims),
        conflicts=conflicts,
        limitation_payloads=tuple(limitations),
    )


def _identity_observations(
    outcomes: tuple[OperationOutcome, ...],
) -> tuple[list[dict[str, object]], dict[str, list[str]]]:
    records: list[dict[str, object]] = []
    ids_by_operation: dict[str, list[str]] = {}
    for outcome in outcomes:
        for payload in outcome.observations:
            record_payload: dict[str, object] = {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "payload": dict(payload),
            }
            observation_id = stable_result_id("observation", record_payload)
            records.append({"id": observation_id, **record_payload})
            ids_by_operation.setdefault(outcome.operation_id, []).append(observation_id)
    return records, ids_by_operation


def _identity_payload(record: dict[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}
    identity = payload.get("observation")
    return identity if isinstance(identity, dict) else {}


def _observation_subject(record: dict[str, object]) -> dict[str, object]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}
    subject = payload.get("subject")
    return subject if isinstance(subject, dict) else {}


def _retrieved_date(record: dict[str, object]) -> str:
    payload = record.get("payload")
    retrieved_at = payload.get("retrieved_at") if isinstance(payload, dict) else None
    return retrieved_at[:10] if isinstance(retrieved_at, str) else ""


def _same_identity_payloads(
    official: dict[str, object], dictionary: dict[str, object]
) -> bool:
    return official.get("code") == dictionary.get("code") and (
        dictionary.get("exchange") is None
        or dictionary.get("exchange") == official.get("exchange")
    )


def _assemble_identity_result(
    *,
    operations: tuple[IdentityOperationAdapter, ...],
    outcomes: tuple[OperationOutcome, ...],
    as_of: str,
    status: ResearchStatus,
    claims: tuple[dict[str, object], ...],
    conflicts: tuple[dict[str, object], ...],
    limitation_payloads: tuple[dict[str, object], ...],
    observations: tuple[dict[str, object], ...] = (),
    observation_ids_by_operation: dict[str, list[str]] | None = None,
) -> UnifiedResearchResult:
    observation_ids_by_operation = observation_ids_by_operation or {}
    source_errors: list[dict[str, object]] = []
    source_error_ids_by_operation: dict[str, list[str]] = {}
    operation_coverage: dict[str, dict[str, object]] = {}
    for outcome in outcomes:
        for error in outcome.source_errors:
            error_payload = {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "code": error["code"],
                "message": error["message"],
            }
            error_id = stable_result_id("source-error", error_payload)
            source_errors.append({"id": error_id, **error_payload})
            source_error_ids_by_operation.setdefault(outcome.operation_id, []).append(
                error_id
            )
        operation_coverage[outcome.operation_id] = outcome.to_coverage_dict(
            observation_ids=observation_ids_by_operation.get(outcome.operation_id, ()),
            source_error_ids=source_error_ids_by_operation.get(
                outcome.operation_id, ()
            ),
        )

    capability_state = _identity_capability_state(outcomes)
    limitations = tuple(
        {"id": stable_result_id("limitation", item), **item}
        for item in limitation_payloads
    )
    observation_ids = [str(item["id"]) for item in observations]
    claim_ids = [str(item["id"]) for item in claims]
    conflict_ids = [str(item["id"]) for item in conflicts]
    limitation_ids = [str(item["id"]) for item in limitations]
    source_error_ids = [str(item["id"]) for item in source_errors]
    brief_payload: dict[str, object] = {
        "status": status,
        "summary": (
            "One cross-checked identity was retained."
            if claims
            else "Security identity could not be established."
        ),
        "observation_ids": observation_ids,
        "claim_ids": claim_ids,
        "calculation_ids": [],
        "conflict_ids": conflict_ids,
        "limitation_ids": limitation_ids,
        "source_error_ids": source_error_ids,
    }
    brief_id = stable_result_id("brief", brief_payload)
    brief = {"id": brief_id, **brief_payload}
    registry_operations = [
        {
            "id": operation.operation_id,
            "source_role": operation.source_role,
            "experimental": True,
        }
        for operation in operations
    ]
    return UnifiedResearchResult(
        status=status,
        research_anchor={"as_of": as_of, "timezone": "Asia/Shanghai"},
        plan={
            "id": "security_identity",
            "version": "1.0",
            "task_type": "security_identity",
            "capabilities": [IDENTITY_CAPABILITY_ID],
        },
        capability_registry={
            IDENTITY_CAPABILITY_ID: {
                "id": IDENTITY_CAPABILITY_ID,
                "version": "1.0",
                "granularity": "one_security_identity_claim",
                "operations": registry_operations,
            }
        },
        coverage={
            IDENTITY_CAPABILITY_ID: {
                "state": capability_state,
                "operations": operation_coverage,
            }
        },
        observations=observations,
        claims=claims,
        calculations=(),
        conflicts=conflicts,
        limitations=limitations,
        source_errors=tuple(source_errors),
        task_views={
            "security_identity": {
                "status": status,
                "observation_ids": observation_ids,
                "claim_ids": claim_ids,
                "calculation_ids": [],
                "conflict_ids": conflict_ids,
                "limitation_ids": limitation_ids,
                "source_error_ids": source_error_ids,
                "brief_id": brief_id,
            }
        },
        briefs=(brief,),
    )


def _identity_capability_state(outcomes: tuple[OperationOutcome, ...]) -> str:
    observations = any(outcome.observations for outcome in outcomes)
    errors = any(outcome.source_errors for outcome in outcomes)
    if errors:
        return "partial" if observations else "failed"
    states = {outcome.coverage for outcome in outcomes}
    if states == {"skipped_policy"}:
        return "skipped_policy"
    if states == {"skipped_research_dependency"}:
        return "skipped_research_dependency"
    if observations:
        return "complete"
    return "observed_empty"


def _same_security(
    official: IdentityObservation, dictionary: IdentityObservation
) -> bool:
    return official.code == dictionary.code and (
        dictionary.exchange is None or dictionary.exchange == official.exchange
    )


def _source_error(error: SourceOperationError) -> dict[str, str]:
    return {
        "source_operation": error.source_operation,
        "code": error.code,
        "message": str(error),
    }


def _parse_clue(query: str) -> tuple[str | None, str]:
    exchange, separator, value = query.strip().partition(":")
    normalized_exchange = exchange.upper()
    if separator and normalized_exchange in {"SSE", "SZSE", "BSE"}:
        return normalized_exchange, value.strip()
    return None, query.strip()


def _is_known_bse_code(clue: str) -> bool:
    return (
        len(clue) == 6
        and clue.isascii()
        and clue.isdigit()
        and clue.startswith(("43", "83", "87", "920"))
    )


def _is_valid_clue(query: str, exchange_hint: str | None, clue: str) -> bool:
    if not clue or (":" in query and exchange_hint is None):
        return False
    if clue.isascii() and clue.isdigit():
        return len(clue) == 6
    return True


def resolve_security_identity(
    query: str, as_of: str, transport: HttpTransport
) -> dict[str, object]:
    """Resolve one clue when official and CNINFO security observations agree."""

    exchange_hint, clue = _parse_clue(query)
    research = {
        "query": query,
        "normalized_clue": clue,
        "exchange_hint": exchange_hint,
        "as_of": as_of,
        "timezone": "Asia/Shanghai",
    }
    if not _is_valid_clue(query, exchange_hint, clue):
        return {
            "schema_version": "1.0",
            "status": "blocked",
            "research": research,
            "candidates": [],
            "evidence": [],
            "conflicts": [],
            "source_errors": [],
            "limitations": [
                {
                    "code": "invalid_security_clue",
                    "message": (
                        "A security clue must be a name, a six-digit code, or "
                        "an SSE/SZSE-qualified clue."
                    ),
                }
            ],
        }
    if exchange_hint == "BSE" or _is_known_bse_code(clue):
        return {
            "schema_version": "1.0",
            "status": "blocked",
            "research": research,
            "candidates": [],
            "evidence": [],
            "conflicts": [],
            "limitations": [
                {
                    "code": "unsupported_exchange",
                    "message": (
                        "BSE securities, including current and historical code "
                        "formats, are not supported and will not be routed to SSE "
                        "or SZSE."
                    ),
                }
            ],
        }
    source_errors = []
    official_observations: list[IdentityObservation] = []
    for operation in (SseStockListOperation(), SzseStockListOperation()):
        try:
            official_observations.extend(operation.observe(clue, transport))
        except SourceOperationError as error:
            source_errors.append(_source_error(error))
    try:
        cninfo_observations = CninfoSecurityDictionaryOperation().observe(
            clue, transport
        )
    except SourceOperationError as error:
        cninfo_observations = []
        source_errors.append(_source_error(error))

    def has_official_match(dictionary: IdentityObservation) -> bool:
        return any(
            _same_security(official, dictionary) for official in official_observations
        )

    failed_operations = {item["source_operation"] for item in source_errors}
    operations_by_exchange: dict[
        str, SseStockListOperation | SzseStockListOperation
    ] = {
        "SSE": SseStockListOperation(),
        "SZSE": SzseStockListOperation(),
    }
    for cninfo in cninfo_observations:
        if has_official_match(cninfo) or cninfo.exchange == "BSE":
            continue
        operations: list[SseStockListOperation | SzseStockListOperation] = (
            [operations_by_exchange[cninfo.exchange]]
            if cninfo.exchange in operations_by_exchange
            else list(operations_by_exchange.values())
        )
        for operation in operations:
            if operation.operation_id in failed_operations:
                continue
            try:
                discovered = operation.observe(cninfo.code, transport)
            except SourceOperationError as error:
                source_errors.append(_source_error(error))
                failed_operations.add(error.source_operation)
                continue
            official_observations.extend(discovered)
    cninfo_by_code: dict[str, list[IdentityObservation]] = {}
    for observation in cninfo_observations:
        cninfo_by_code.setdefault(observation.code, []).append(observation)
    candidates: list[dict[str, object]] = []
    evidence = [
        observation.to_evidence()
        for observation in [*official_observations, *cninfo_observations]
    ]
    conflicts = []
    for official in official_observations:
        same_code_cninfo = cninfo_by_code.get(official.code, [])
        exchange_conflicts = [
            item
            for item in same_code_cninfo
            if item.exchange is not None and item.exchange != official.exchange
        ]
        if exchange_conflicts:
            official_evidence = official.to_evidence()
            for cninfo in exchange_conflicts:
                cninfo_evidence = cninfo.to_evidence()
                conflicts.append(
                    {
                        "code": "source_identity_conflict",
                        "message": (
                            f"Sources disagree on the exchange for code "
                            f"{official.code}."
                        ),
                        "evidence_ids": [
                            official_evidence["id"],
                            cninfo_evidence["id"],
                        ],
                    }
                )
            continue
        matching_cninfo = [
            item for item in same_code_cninfo if _same_security(official, item)
        ]
        if len(matching_cninfo) != 1:
            continue
        cninfo = matching_cninfo[0]
        official_evidence = official.to_evidence()
        cninfo_evidence = cninfo.to_evidence()
        if official.name != cninfo.name:
            conflicts.append(
                {
                    "code": "source_identity_conflict",
                    "message": (
                        f"Source names disagree for {official.exchange}:"
                        f"{official.code}."
                    ),
                    "evidence_ids": [
                        official_evidence["id"],
                        cninfo_evidence["id"],
                    ],
                }
            )
            continue
        if exchange_hint is not None and official.exchange != exchange_hint:
            conflicts.append(
                {
                    "code": "exchange_hint_conflict",
                    "message": (
                        f"The {exchange_hint} hint conflicts with cross-checked "
                        f"identity {official.exchange}:{official.code}."
                    ),
                    "evidence_ids": [
                        official_evidence["id"],
                        cninfo_evidence["id"],
                    ],
                }
            )
            continue
        candidates.append(
            {
                "security": {
                    "exchange": official.exchange,
                    "code": official.code,
                    "type": "A_SHARE",
                    "validity": {
                        "from": official.valid_from,
                        "to": None,
                        "status": "current",
                        "observed_at": official.retrieved_at.date().isoformat(),
                    },
                },
                "name": official.name,
                "issuer": {
                    "name": official.issuer_name,
                    "identifier": (
                        {
                            "scheme": "CNINFO_ORG_ID",
                            "value": cninfo.issuer_identifier,
                        }
                        if cninfo.issuer_relationship_verified
                        else None
                    ),
                    "security_relationship": (
                        "verified"
                        if official.issuer_relationship_verified
                        or cninfo.issuer_relationship_verified
                        else "unverified"
                    ),
                },
                "identity_status": "cross_checked_experimental",
                "evidence_ids": [
                    official_evidence["id"],
                    cninfo_evidence["id"],
                ],
            }
        )
    observation_dates = {
        item.retrieved_at.date().isoformat()
        for item in [*official_observations, *cninfo_observations]
    }
    if source_errors:
        status = "blocked"
        candidates = []
        limitations = [
            {
                "code": "source_operation_failed",
                "message": (
                    "At least one required identity source operation failed; "
                    "identity resolution stopped."
                ),
            }
        ]
    elif any(item.exchange == "BSE" for item in cninfo_observations):
        status = "blocked"
        candidates = []
        limitations = [
            {
                "code": "unsupported_exchange",
                "message": (
                    "CNINFO identifies the clue as a BSE security; BSE is not "
                    "supported and will not be routed to SSE or SZSE."
                ),
            }
        ]
    elif observation_dates and observation_dates != {as_of}:
        status = "blocked"
        candidates = []
        limitations = [
            {
                "code": "identity_observation_outside_research_date",
                "message": (
                    "Current identity observations cannot establish identity "
                    "for a different research date."
                ),
            }
        ]
    elif len(candidates) == 1 and not conflicts:
        status = "limited"
        limitations = [
            {
                "code": "experimental_identity_sources",
                "message": (
                    "Identity was cross-checked using experimental source "
                    "operations and is not independently source verified."
                ),
            }
        ]
        issuer = candidates[0]["issuer"]
        if (
            isinstance(issuer, dict)
            and issuer.get("security_relationship") == "unverified"
        ):
            limitations.append(
                {
                    "code": "issuer_relationship_unverified",
                    "message": (
                        "The security identity is cross-checked, but the "
                        "available observations do not independently verify "
                        "the associated issuer relationship."
                    ),
                }
            )
    elif len(candidates) > 1:
        status = "blocked"
        limitations = [
            {
                "code": "ambiguous_security_clue",
                "message": (
                    "The clue matches multiple cross-checked securities; an "
                    "exchange-qualified clue is required."
                ),
            }
        ]
    else:
        status = "blocked"
        limitations = [
            {
                "code": "identity_not_resolved",
                "message": (
                    "The available source observations do not establish one "
                    "consistent security identity."
                ),
            }
        ]
    return {
        "schema_version": "1.0",
        "status": status,
        "research": research,
        "candidates": candidates,
        "evidence": evidence,
        "conflicts": conflicts,
        "source_errors": source_errors,
        "limitations": limitations,
    }
