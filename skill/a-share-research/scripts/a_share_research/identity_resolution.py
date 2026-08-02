"""Cross-source orchestration for fail-closed security identity resolution."""

from __future__ import annotations

from .identity_sources import (
    CninfoSecurityDictionaryOperation,
    HttpTransport,
    IdentityObservation,
    SourceOperationError,
    SseStockListOperation,
    SzseStockListOperation,
)


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
