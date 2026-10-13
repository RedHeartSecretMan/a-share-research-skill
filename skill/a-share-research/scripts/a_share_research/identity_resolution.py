"""Cross-source orchestration for fail-closed security identity resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, cast

from .capability_execution import (
    BoundedOperationRequest,
    PlannedOperation,
    _spawn_attempt,
    compile_capability_execution_plan,
    execute_capability_plan,
)
from .identity_sources import (
    CninfoSecurityDictionaryOperation,
    HttpTransport,
    IdentityObservation,
    IdentityOperationAdapter,
    IdentityOperationRequest,
    SourceOperationError,
    ProductionIdentityOperationAdapter,
    SseStockListOperation,
    SzseStockListOperation,
)
from .provider_lane import ProductionInvocationAdapter, ProviderInvocation
from .research_result import (
    IdentityResearchPlan,
    OperationExecution,
    OperationExecutionState,
    OperationOutcome,
    ResearchStatus,
    UnifiedResearchResult,
    compile_identity_research_plan,
    fold_identity_research_result,
    stable_result_id,
)


@dataclass(frozen=True)
class CanonicalIdentityMaterial:
    exchange: str
    code: str
    security_type: str
    name: str
    issuer_name: str | None
    issuer_identifier: str | None
    issuer_relationship: str


@dataclass(frozen=True)
class IdentityLimitationMaterial:
    code: str
    message: str


@dataclass(frozen=True)
class IdentityResearchMaterial:
    status: ResearchStatus
    canonical_identity: CanonicalIdentityMaterial | None
    limitations: tuple[IdentityLimitationMaterial, ...]
    observation_keys: tuple[str, ...]


@dataclass(frozen=True)
class IdentityResearchExecution:
    plan: IdentityResearchPlan
    outcomes: tuple[OperationOutcome, ...]
    material: IdentityResearchMaterial
    result: UnifiedResearchResult


def build_security_identity_research_result(
    request: dict[str, object],
    operations: tuple[IdentityOperationAdapter, ...],
) -> UnifiedResearchResult:
    """Execute the versioned identity plan and return its direct unified result."""

    return execute_security_identity_research(request, operations).result


def execute_security_identity_research(
    request: dict[str, object],
    operations: tuple[IdentityOperationAdapter, ...],
) -> IdentityResearchExecution:
    """Execute identity while retaining the outcomes at their ownership boundary."""

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
    source_policy = request["source_policy"]
    if not isinstance(source_policy, dict):
        raise ValueError("research source_policy must be a JSON object")
    if any(
        operation.execution_backend not in {"isolated_worker", "in_process"}
        for operation in operations
    ):
        raise ValueError("identity adapter execution backend is unsupported")

    precondition_reason: str | None = None
    execution_state: OperationExecutionState = "skipped_research_dependency"
    if source_policy["allow_experimental"] is False:
        precondition_reason = "source_policy_not_satisfied"
        execution_state = "skipped_policy"
    elif not _is_valid_clue(query, exchange_hint, clue):
        precondition_reason = "invalid_security_clue"
    elif exchange_hint == "BSE" or _is_known_bse_code(clue):
        precondition_reason = "unsupported_exchange"

    parameters = cast(dict[str, object], request["parameters"])
    execution_plan = compile_capability_execution_plan(
        version="1.0",
        completeness_mode=cast(str, parameters.get("completeness_mode", "standard")),
        operations=operations,
    )
    plan = compile_identity_research_plan(
        as_of=str(request["as_of"]),
        exchange_hint=exchange_hint if exchange_hint in {"SSE", "SZSE"} else None,
        obligations=tuple(operation.obligation for operation in operations),
        execution=execution_plan.to_public_dict(),
        precondition_reason=precondition_reason,
    )
    if precondition_reason is not None:
        outcomes = tuple(
            OperationOutcome(
                capability_id=operation.obligation.capability_id,
                operation_id=operation.obligation.operation_id,
                execution=OperationExecution(
                    execution_state
                    if execution_plan.is_scheduled(
                        (
                            operation.obligation.capability_id,
                            operation.obligation.operation_id,
                        )
                    )
                    else "not_requested"
                ),
                field_dispositions=operation.field_dispositions,
            )
            for operation in operations
        )
        return _complete_identity_execution(plan, outcomes)

    operation_request = IdentityOperationRequest(
        query=clue,
        as_of=str(request["as_of"]),
    )
    adapters = {
        operation.obligation.operation_id: operation for operation in operations
    }

    def run_attempt(
        operation: PlannedOperation,
        request: BoundedOperationRequest,
        timeout_seconds: float,
        budget_limited: bool,
        invocation: ProviderInvocation,
    ) -> OperationOutcome:
        adapter = adapters.get(operation.obligation.operation_id)
        if adapter is None:
            raise ValueError("execution plan selected an unknown identity adapter")
        if adapter.execution_backend == "isolated_worker":
            return _spawn_attempt(
                operation,
                request,
                timeout_seconds,
                budget_limited,
                provider_invocation=invocation,
            )
        if adapter.execution_backend != "in_process":
            raise ValueError("identity adapter execution backend is unsupported")
        source_request = IdentityOperationRequest(
            query=cast(str, request.payload["query"]),
            as_of=cast(str, request.payload["as_of"]),
        )
        if isinstance(adapter, ProductionIdentityOperationAdapter):
            return adapter.execute(
                source_request,
                timeout_seconds=timeout_seconds,
                provider_invocation=invocation,
            )
        return invocation.call(
            ProductionInvocationAdapter(
                lambda: adapter.execute(source_request, timeout_seconds=timeout_seconds)
            )
        )

    def plan_followups(
        outcomes: tuple[OperationOutcome, ...],
    ) -> tuple[tuple[tuple[str, str], BoundedOperationRequest], ...]:
        return _plan_official_identity_followups(
            as_of=str(request["as_of"]),
            plan=plan,
            outcomes=outcomes,
        )

    outcomes = execute_capability_plan(
        execution_plan,
        BoundedOperationRequest(
            kind="security_identity",
            payload={
                "query": operation_request.query,
                "as_of": operation_request.as_of,
            },
        ),
        owned_attempt_runner=run_attempt,
        followup_planner=plan_followups,
    )
    return _complete_identity_execution(plan, outcomes)


def _complete_identity_execution(
    plan: IdentityResearchPlan,
    outcomes: tuple[OperationOutcome, ...],
) -> IdentityResearchExecution:
    result = fold_identity_research_result(plan, outcomes)
    return IdentityResearchExecution(
        plan=plan,
        outcomes=outcomes,
        material=_identity_research_material(result, outcomes),
        result=result,
    )


def _identity_research_material(
    result: UnifiedResearchResult,
    outcomes: tuple[OperationOutcome, ...],
) -> IdentityResearchMaterial:
    identity_claims = tuple(
        item for item in result.claims if item.get("kind") == "security_identity"
    )
    canonical_identity: CanonicalIdentityMaterial | None = None
    if len(identity_claims) == 1:
        payload = _required_mapping(
            identity_claims[0].get("payload"), "identity payload"
        )
        security = _required_mapping(payload.get("security"), "identity security")
        issuer = _required_mapping(payload.get("issuer"), "identity issuer")
        issuer_identifier = issuer.get("identifier")
        if issuer_identifier is not None and not isinstance(issuer_identifier, str):
            raise ValueError("identity issuer identifier must be text or null")
        canonical_identity = CanonicalIdentityMaterial(
            exchange=_required_text(security.get("exchange"), "identity exchange"),
            code=_required_text(security.get("code"), "identity code"),
            security_type=_required_text(security.get("type"), "identity type"),
            name=_required_text(payload.get("name"), "identity name"),
            issuer_name=_optional_text(issuer.get("name"), "identity issuer name"),
            issuer_identifier=issuer_identifier,
            issuer_relationship=_required_text(
                issuer.get("security_relationship"),
                "identity issuer relationship",
            ),
        )
    elif identity_claims:
        raise ValueError("identity execution produced multiple canonical claims")
    limitations = tuple(
        IdentityLimitationMaterial(
            code=_required_text(item.get("code"), "identity limitation code"),
            message=_required_text(item.get("message"), "identity limitation message"),
        )
        for item in result.limitations
    )
    adopted_observation_ids: set[str] = set()
    for claim in identity_claims:
        claim_observation_ids = claim.get("observation_ids")
        if not isinstance(claim_observation_ids, list):
            raise ValueError("identity claim observation IDs must be an array")
        adopted_observation_ids.update(
            item for item in claim_observation_ids if isinstance(item, str)
        )
    observation_keys = tuple(
        str(local_key)
        for outcome in outcomes
        for index, payload in enumerate(outcome.to_dict()["observations"])
        for local_key in (
            payload.get("id")
            if isinstance(payload.get("id"), str) and payload.get("id")
            else f"{outcome.operation_id}:{index}",
        )
        if stable_result_id(
            "observation",
            {
                "capability_id": outcome.capability_id,
                "operation_id": outcome.operation_id,
                "payload": dict(payload),
            },
        )
        in adopted_observation_ids
    )
    return IdentityResearchMaterial(
        status=result.status,
        canonical_identity=canonical_identity,
        limitations=limitations,
        observation_keys=observation_keys,
    )


def _required_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be non-empty text")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{path} must be non-empty text or null")
    return value


def _plan_official_identity_followups(
    *,
    as_of: str,
    plan: IdentityResearchPlan,
    outcomes: tuple[OperationOutcome, ...],
) -> tuple[tuple[tuple[str, str], BoundedOperationRequest], ...]:
    by_operation = {outcome.operation_id: outcome for outcome in outcomes}
    supporting_ids = {
        item.operation_id
        for item in plan.obligations
        if item.evidence_role("security_identity") == "supporting"
    }
    corroborating_ids = {
        item.operation_id
        for item in plan.obligations
        if item.evidence_role("security_identity") == "corroborating"
    }
    official_codes = {
        identity.get("code")
        for operation_id in supporting_ids
        for outcome in (
            [by_operation[operation_id]] if operation_id in by_operation else []
        )
        for observation in outcome.to_dict()["observations"]
        for identity in [_outcome_identity_payload(observation)]
        if isinstance(identity.get("code"), str)
    }
    public_execution_operations = plan.execution.get("operations")
    if not isinstance(public_execution_operations, list):
        raise ValueError("identity execution plan operations are invalid")
    request_limits = {
        item.get("operation_id"): item.get("max_requests_per_plan")
        for item in public_execution_operations
        if isinstance(item, dict)
    }
    supporting_limits = tuple(
        request_limits.get(operation_id) for operation_id in supporting_ids
    )
    if not supporting_limits or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in supporting_limits
    ):
        raise ValueError("identity follow-up request limits are invalid")
    candidate_limit = min(cast(int, item) - 1 for item in supporting_limits)
    candidate_keys = sorted(
        {
            (
                exchange if exchange in {"SSE", "SZSE"} else None,
                code,
            )
            for corroborating_id in corroborating_ids
            for corroborating in (
                [by_operation[corroborating_id]]
                if corroborating_id in by_operation
                else []
            )
            for observation in corroborating.to_dict()["observations"]
            for identity in [_outcome_identity_payload(observation)]
            for code in [identity.get("code")]
            for exchange in [identity.get("exchange")]
            if isinstance(code, str)
            and code not in official_codes
            and exchange != "BSE"
        },
        key=lambda item: (item[0] or "", item[1]),
    )
    followups: list[tuple[tuple[str, str], BoundedOperationRequest]] = []
    for exchange, code in candidate_keys[:candidate_limit]:
        target_ids = tuple(
            item.operation_id
            for item in plan.obligations
            if item.operation_id in supporting_ids
            and (exchange not in {"SSE", "SZSE"} or item.exchange == exchange)
        )
        for operation_id in target_ids:
            previous = by_operation.get(operation_id)
            if previous is None:
                continue
            if previous.execution.state == "failed":
                continue
            followups.append(
                (
                    (
                        previous.capability_id,
                        operation_id,
                    ),
                    BoundedOperationRequest(
                        kind="security_identity",
                        payload={"query": code, "as_of": as_of},
                    ),
                )
            )
    return tuple(followups)


def _outcome_identity_payload(observation: object) -> dict[str, object]:
    if not isinstance(observation, dict):
        return {}
    identity = observation.get("observation")
    return identity if isinstance(identity, dict) else {}


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
