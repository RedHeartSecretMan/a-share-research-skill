"""Deep module interface for versioned A-share research tasks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Collection

from .automatic_valuation import (
    build_security_valuation_result,
    build_valuation_comparison_result,
)
from .capital_contract import (
    CapitalHttpTransport,
    CapitalSourceOperation,
)
from .capital_events import (
    build_capital_events_blocked_result,
    build_capital_events_result,
)
from .capital_registry import build_default_capital_operations
from .content_contract import (
    ContentHttpTransport,
    ContentSourceOperation,
)
from .content_registry import build_default_content_operations
from .etf_market import build_etf_market_result
from .etf_option_contract import OptionSourceOperation
from .etf_options import build_etf_options_result
from .evidence_store import EvidenceStoreRequest, parse_evidence_store_request
from .identity_resolution import build_security_identity_research_result
from .identity_sources import (
    CHINA_STANDARD_TIME,
    HttpTransport,
    UrlLibTransport,
    build_default_identity_operations,
)
from .installation_health import (
    MOOTDX_RUNTIME_DEPENDENCY,
    dependency_available,
)
from .intraday_contract import IntradaySourceOperation
from .intraday_replay import (
    build_intraday_replay_blocked_result,
    build_intraday_replay_result,
)
from .intraday_replay_contract import (
    IntradayReplayDailySourceOperation,
    IntradayReplaySourceOperation,
)
from .intraday_snapshot import (
    build_intraday_blocked_result,
    build_intraday_snapshot_result,
)
from .market_series import build_market_trend_result
from .market_signal_contract import (
    MarketSignalHttpTransport,
    MarketSignalSourceOperation,
)
from .market_signals import (
    build_market_signals_blocked_result,
    build_market_signals_result,
)
from .research_content import (
    build_research_content_blocked_result,
    build_research_content_result,
)
from .research_result import UnifiedResearchResult
from .research_workflows import build_research_workflow_result


class ResearchRuntime:
    """Route stable research tasks while hiding source-specific operations."""

    def __init__(
        self,
        *,
        identity_transport: HttpTransport | None = None,
        research_now: datetime | None = None,
        available_optional_dependencies: Collection[str] | None = None,
        content_operations: Collection[ContentSourceOperation] | None = None,
        content_transport: ContentHttpTransport | None = None,
        capital_operations: Collection[CapitalSourceOperation] | None = None,
        capital_transport: CapitalHttpTransport | None = None,
        market_signal_operations: Collection[MarketSignalSourceOperation] | None = None,
        market_signal_transport: MarketSignalHttpTransport | None = None,
        etf_option_operations: Collection[OptionSourceOperation] | None = None,
        etf_option_transport: HttpTransport | None = None,
        intraday_operations: Collection[IntradaySourceOperation] | None = None,
        intraday_transport: HttpTransport | None = None,
        intraday_replay_operations: Collection[IntradayReplaySourceOperation]
        | None = None,
        intraday_replay_daily_operations: Collection[IntradayReplayDailySourceOperation]
        | None = None,
    ) -> None:
        self._identity_transport = identity_transport or UrlLibTransport()
        self._research_now = research_now
        self._available_optional_dependencies = available_optional_dependencies
        self._content_operations = (
            None if content_operations is None else tuple(content_operations)
        )
        self._content_transport = content_transport or UrlLibTransport()
        self._capital_operations = (
            None if capital_operations is None else tuple(capital_operations)
        )
        self._capital_transport = capital_transport or UrlLibTransport()
        self._market_signal_operations = (
            None
            if market_signal_operations is None
            else tuple(market_signal_operations)
        )
        self._market_signal_transport = market_signal_transport or UrlLibTransport()
        self._etf_option_operations = (
            None if etf_option_operations is None else tuple(etf_option_operations)
        )
        self._etf_option_transport = etf_option_transport or UrlLibTransport()
        self._intraday_operations = (
            None if intraday_operations is None else tuple(intraday_operations)
        )
        self._intraday_transport = intraday_transport or UrlLibTransport()
        self._intraday_replay_operations = (
            None
            if intraday_replay_operations is None
            else tuple(intraday_replay_operations)
        )
        self._intraday_replay_daily_operations = (
            None
            if intraday_replay_daily_operations is None
            else tuple(intraday_replay_daily_operations)
        )

    def research(self, request: dict[str, Any]) -> UnifiedResearchResult:
        """Execute one versioned research task."""

        sanitized_request, evidence_store = parse_evidence_store_request(request)
        _validate_request_envelope(sanitized_request)
        if sanitized_request["task_type"] == "security_identity":
            _require_no_evidence_store(
                evidence_store, str(sanitized_request["task_type"])
            )
            return build_security_identity_research_result(
                sanitized_request,
                build_default_identity_operations(self._identity_transport),
            )
        return self._dispatch_validated(
            sanitized_request,
            allow_workflow=True,
            evidence_store=evidence_store,
        )

    def _run_leaf_task(self, request: dict[str, Any]) -> UnifiedResearchResult:
        """Run an internally compiled leaf task through this Runtime."""

        _validate_request_envelope(request)
        if request["task_type"] == "research_workflow":
            raise ValueError("research_workflow steps must be leaf ResearchTasks")
        if request["task_type"] == "security_identity":
            return build_security_identity_research_result(
                request,
                build_default_identity_operations(self._identity_transport),
            )
        return self._dispatch_validated(
            request,
            allow_workflow=False,
            evidence_store=None,
        )

    def _dispatch_validated(
        self,
        request: dict[str, Any],
        *,
        allow_workflow: bool,
        evidence_store: EvidenceStoreRequest | None,
    ) -> UnifiedResearchResult:
        """Dispatch an envelope that has already passed common validation."""

        task_type = request["task_type"]
        result: UnifiedResearchResult
        if task_type == "research_workflow":
            _require_no_evidence_store(evidence_store, task_type)
            if not allow_workflow:
                raise ValueError("nested research_workflow tasks are not allowed")
            result = build_research_workflow_result(
                request, self._run_leaf_task, research_now=self._research_now
            )
        elif task_type == "market_trend":
            result = build_market_trend_result(
                request,
                self._identity_transport,
                self._research_now,
                evidence_store=evidence_store,
            )
        elif task_type == "etf_market":
            _require_no_evidence_store(evidence_store, task_type)
            result = build_etf_market_result(
                request,
                self._identity_transport,
                self._research_now,
            )
        elif task_type == "etf_options":
            _require_no_evidence_store(evidence_store, task_type)
            etf_option_operations = self._etf_option_operations
            if etf_option_operations is None:
                from .etf_option_registry import build_default_etf_option_operations

                etf_option_operations = build_default_etf_option_operations(
                    self._etf_option_transport
                )
            result = build_etf_options_result(request, etf_option_operations)
        elif task_type == "security_valuation":
            result = build_security_valuation_result(
                request,
                self._identity_transport,
                self._research_now,
                evidence_store=evidence_store,
            )
        elif task_type == "valuation_compare":
            _require_no_evidence_store(evidence_store, task_type)
            result = build_valuation_comparison_result(
                request,
                self._identity_transport,
                self._research_now,
            )
        elif task_type == "research_content":
            content_operations = self._content_operations
            if content_operations is None:
                content_operations = build_default_content_operations(
                    self._content_transport,
                    allow_credentials=request["source_policy"]["allow_credentials"],
                    allow_fallback=request["source_policy"]["allow_fallback"],
                    research_now=self._research_now,
                )
            if not request["source_policy"]["allow_experimental"]:
                result = build_research_content_blocked_result(
                    request,
                    content_operations,
                    evidence_store,
                )
            else:
                result = build_research_content_result(
                    request,
                    content_operations,
                    self._identity_transport,
                    self._content_transport,
                    evidence_store,
                    self._research_now,
                )
        elif task_type == "capital_events":
            _require_no_evidence_store(evidence_store, task_type)
            capital_operations = self._capital_operations
            if capital_operations is None:
                capital_operations = build_default_capital_operations(
                    self._capital_transport
                )
            if not request["source_policy"]["allow_experimental"]:
                result = build_capital_events_blocked_result(
                    request,
                    capital_operations,
                )
            else:
                result = build_capital_events_result(
                    request,
                    capital_operations,
                    self._identity_transport,
                )
        elif task_type == "market_signals":
            _require_no_evidence_store(evidence_store, task_type)
            market_signal_operations = self._market_signal_operations
            if market_signal_operations is None:
                from .market_signal_registry import (
                    build_default_market_signal_operations,
                )

                market_signal_operations = build_default_market_signal_operations(
                    self._market_signal_transport
                )
            if not request["source_policy"]["allow_experimental"]:
                result = build_market_signals_blocked_result(
                    request,
                    market_signal_operations,
                )
            else:
                result = build_market_signals_result(
                    request,
                    market_signal_operations,
                    self._identity_transport,
                )
        elif task_type == "intraday_market_signal":
            _require_no_evidence_store(evidence_store, task_type)
            if not request["source_policy"]["allow_experimental"]:
                result = build_intraday_blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "intraday_market_signal requires both experimental source "
                        "operations"
                    ),
                )
            elif not self._dependency_available(
                MOOTDX_RUNTIME_DEPENDENCY.distribution,
                MOOTDX_RUNTIME_DEPENDENCY.required_version,
            ):
                result = build_intraday_blocked_result(
                    request,
                    code="installation_incomplete",
                    message=(
                        "intraday_market_data requires the locked core mootdx "
                        "Adapter dependency installation"
                    ),
                    limitation_details={
                        "capability": "intraday_market_data",
                        "dependency": "mootdx",
                        "required_version": "0.11.7",
                    },
                )
            else:
                intraday_operations = self._intraday_operations
                if intraday_operations is None:
                    from .intraday_registry import build_default_intraday_operations

                    intraday_operations = build_default_intraday_operations(
                        self._intraday_transport
                    )
                result = build_intraday_snapshot_result(
                    request,
                    intraday_operations,
                    self._research_now or datetime.now(CHINA_STANDARD_TIME),
                )
        elif task_type == "intraday_replay":
            _require_no_evidence_store(evidence_store, task_type)
            if not request["source_policy"]["allow_experimental"]:
                result = build_intraday_replay_blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "intraday_replay requires an explicit experimental source "
                        "policy until a source operation is qualified"
                    ),
                )
            elif (
                self._intraday_replay_operations is None
                and not self._dependency_available(
                    MOOTDX_RUNTIME_DEPENDENCY.distribution,
                    MOOTDX_RUNTIME_DEPENDENCY.required_version,
                )
            ):
                result = build_intraday_replay_blocked_result(
                    request,
                    code="installation_incomplete",
                    message=(
                        "intraday_replay requires the locked core mootdx Adapter "
                        "dependency installation"
                    ),
                    limitation_details={
                        "capability": "intraday_replay",
                        "dependency": "mootdx",
                        "required_version": "0.11.7",
                    },
                )
            else:
                replay_operations = self._intraday_replay_operations
                replay_daily_operations = self._intraday_replay_daily_operations
                if replay_operations is None:
                    try:
                        from .intraday_replay_registry import (
                            build_default_intraday_replay_operations,
                        )

                        (
                            replay_operations,
                            default_daily_operations,
                        ) = build_default_intraday_replay_operations(
                            transport=self._intraday_transport,
                        )
                        if replay_daily_operations is None:
                            replay_daily_operations = default_daily_operations
                    except ModuleNotFoundError:
                        result = build_intraday_replay_blocked_result(
                            request,
                            code="installation_incomplete",
                            message=(
                                "intraday_replay requires the locked core mootdx "
                                "Adapter dependency installation"
                            ),
                            limitation_details={
                                "capability": "intraday_replay",
                                "dependency": "mootdx",
                                "required_version": "0.11.7",
                            },
                        )
                        return result
                result = build_intraday_replay_result(
                    request,
                    replay_operations or (),
                    self._research_now or datetime.now(CHINA_STANDARD_TIME),
                    daily_operations=replay_daily_operations or (),
                )
        else:
            raise ValueError(f"Research task type is not supported: {task_type}")
        return result

    def _dependency_available(self, dependency: str, version: str) -> bool:
        if self._available_optional_dependencies is not None:
            return f"{dependency}=={version}" in self._available_optional_dependencies
        return dependency_available(dependency, version)


def _require_no_evidence_store(
    evidence_store: EvidenceStoreRequest | None, task_type: str
) -> None:
    if evidence_store is not None:
        raise ValueError(f"{task_type} does not permit persisted research evidence")


def research(
    request: dict[str, Any],
    *,
    identity_transport: HttpTransport | None = None,
    research_now: datetime | None = None,
    available_optional_dependencies: Collection[str] | None = None,
    content_operations: Collection[ContentSourceOperation] | None = None,
    content_transport: ContentHttpTransport | None = None,
    capital_operations: Collection[CapitalSourceOperation] | None = None,
    capital_transport: CapitalHttpTransport | None = None,
    market_signal_operations: Collection[MarketSignalSourceOperation] | None = None,
    market_signal_transport: MarketSignalHttpTransport | None = None,
    etf_option_operations: Collection[OptionSourceOperation] | None = None,
    etf_option_transport: HttpTransport | None = None,
    intraday_operations: Collection[IntradaySourceOperation] | None = None,
    intraday_transport: HttpTransport | None = None,
    intraday_replay_operations: Collection[IntradayReplaySourceOperation] | None = None,
    intraday_replay_daily_operations: Collection[IntradayReplayDailySourceOperation]
    | None = None,
) -> UnifiedResearchResult:
    """Run a task through the public research module interface."""

    return ResearchRuntime(
        identity_transport=identity_transport,
        research_now=research_now,
        available_optional_dependencies=available_optional_dependencies,
        content_operations=content_operations,
        content_transport=content_transport,
        capital_operations=capital_operations,
        capital_transport=capital_transport,
        market_signal_operations=market_signal_operations,
        market_signal_transport=market_signal_transport,
        etf_option_operations=etf_option_operations,
        etf_option_transport=etf_option_transport,
        intraday_operations=intraday_operations,
        intraday_transport=intraday_transport,
        intraday_replay_operations=intraday_replay_operations,
        intraday_replay_daily_operations=intraday_replay_daily_operations,
    ).research(request)


def _validate_request_envelope(request: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "task_type",
        "subjects",
        "as_of",
        "window",
        "parameters",
        "source_policy",
    }
    missing = sorted(required.difference(request))
    if missing:
        raise ValueError(f"research task is missing fields: {', '.join(missing)}")
    if set(request) != required:
        raise ValueError("research task contains unknown fields")
    if request["schema_version"] != "1.0":
        raise ValueError("unsupported research task schema_version")
    if not isinstance(request["task_type"], str) or not request["task_type"]:
        raise ValueError("research task_type must be a non-empty string")
    if not isinstance(request["subjects"], list):
        raise ValueError("research subjects must be a JSON array")
    as_of = request["as_of"]
    if not isinstance(as_of, str):
        raise ValueError("research as_of must use explicit YYYY-MM-DD format")
    try:
        parsed_as_of = date.fromisoformat(as_of)
    except ValueError as error:
        raise ValueError(
            "research as_of must use explicit YYYY-MM-DD format"
        ) from error
    if parsed_as_of.isoformat() != as_of:
        raise ValueError("research as_of must use explicit YYYY-MM-DD format")
    if not isinstance(request["parameters"], dict):
        raise ValueError("research parameters must be a JSON object")
    if not isinstance(request["source_policy"], dict):
        raise ValueError("research source_policy must be a JSON object")
    required_policy_fields = {
        "allow_experimental",
        "allow_credentials",
        "allow_fallback",
    }
    missing_policy_fields = sorted(
        required_policy_fields.difference(request["source_policy"])
    )
    if missing_policy_fields:
        raise ValueError(
            "research source_policy is missing fields: "
            + ", ".join(missing_policy_fields)
        )
    if set(request["source_policy"]) != required_policy_fields:
        raise ValueError("research source_policy contains unknown fields")
    if any(
        not isinstance(request["source_policy"][field], bool)
        for field in required_policy_fields
    ):
        raise ValueError("research source_policy fields must be booleans")
    if request["task_type"] == "security_identity":
        if len(request["subjects"]) != 1:
            raise ValueError("security_identity requires exactly one subject")
        subject = request["subjects"][0]
        if not isinstance(subject, dict):
            raise ValueError("security_identity subject must be a JSON object")
        if set(subject) != {"clue"}:
            raise ValueError("security_identity subject has an invalid shape")
        if not isinstance(subject["clue"], str) or not subject["clue"].strip():
            raise ValueError("security_identity subject requires a non-empty clue")
        parameters = request["parameters"]
        if set(parameters) not in (set(), {"completeness_mode"}):
            raise ValueError("security_identity parameters contain unknown fields")
        if parameters.get("completeness_mode", "standard") not in {"standard", "fast"}:
            raise ValueError("security_identity completeness_mode is unsupported")
        if request["window"] is not None:
            raise ValueError("security_identity window must be null")
