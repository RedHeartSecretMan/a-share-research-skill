"""Deep module interface for versioned A-share research tasks."""

from __future__ import annotations

import importlib.util
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
from .capital_events import build_capital_events_result
from .capital_registry import build_default_capital_operations
from .content_contract import (
    ContentHttpTransport,
    ContentSourceOperation,
)
from .content_registry import build_default_content_operations
from .etf_market import build_etf_market_result
from .etf_option_contract import OptionSourceOperation
from .etf_options import build_etf_options_result
from .identity_resolution import resolve_security_identity
from .identity_sources import HttpTransport, UrlLibTransport
from .market_series import build_market_trend_result
from .market_signal_contract import (
    MarketSignalHttpTransport,
    MarketSignalSourceOperation,
)
from .market_signals import build_market_signals_result
from .research_content import build_research_content_result
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

    def research(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute one versioned research task."""

        _validate_request_envelope(request)
        return self._dispatch_validated(request, allow_workflow=True)

    def _run_leaf_task(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run an internally compiled leaf task through this Runtime."""

        _validate_request_envelope(request)
        if request["task_type"] == "research_workflow":
            raise ValueError("research_workflow steps must be leaf ResearchTasks")
        return self._dispatch_validated(request, allow_workflow=False)

    def _dispatch_validated(
        self,
        request: dict[str, Any],
        *,
        allow_workflow: bool,
    ) -> dict[str, Any]:
        """Dispatch an envelope that has already passed common validation."""

        task_type = request["task_type"]
        if task_type == "research_workflow":
            if not allow_workflow:
                raise ValueError("nested research_workflow tasks are not allowed")
            result = build_research_workflow_result(request, self._run_leaf_task)
        elif task_type == "security_identity":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "security_identity currently requires experimental "
                        "source operations"
                    ),
                )
            else:
                result = self._research_identity(request)
        elif task_type == "market_trend":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "market_trend currently requires experimental source operations"
                    ),
                )
            else:
                result = build_market_trend_result(
                    request,
                    self._identity_transport,
                    self._research_now,
                )
        elif task_type == "etf_market":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "etf_market currently requires experimental source operations"
                    ),
                )
            else:
                result = build_etf_market_result(
                    request,
                    self._identity_transport,
                    self._research_now,
                )
        elif task_type == "etf_options":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "etf_options currently requires experimental source operations"
                    ),
                )
            else:
                etf_option_operations = self._etf_option_operations
                if etf_option_operations is None:
                    from .etf_option_registry import (
                        build_default_etf_option_operations,
                    )

                    etf_option_operations = build_default_etf_option_operations(
                        self._etf_option_transport
                    )
                result = build_etf_options_result(request, etf_option_operations)
        elif task_type == "security_valuation":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "security_valuation currently requires experimental "
                        "source operations"
                    ),
                )
            else:
                result = build_security_valuation_result(
                    request,
                    self._identity_transport,
                    self._research_now,
                )
        elif task_type == "valuation_compare":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "valuation_compare currently requires experimental "
                        "source operations"
                    ),
                )
            else:
                result = build_valuation_comparison_result(
                    request,
                    self._identity_transport,
                    self._research_now,
                )
        elif task_type == "research_content":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "research_content currently requires experimental "
                        "source operations"
                    ),
                )
            else:
                content_operations = self._content_operations
                if content_operations is None:
                    content_operations = build_default_content_operations(
                        self._content_transport,
                        allow_credentials=request["source_policy"]["allow_credentials"],
                        allow_fallback=request["source_policy"]["allow_fallback"],
                        research_now=self._research_now,
                    )
                result = build_research_content_result(
                    request,
                    content_operations,
                    self._identity_transport,
                    self._content_transport,
                )
        elif task_type == "capital_events":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "capital_events currently requires experimental source "
                        "operations"
                    ),
                )
            else:
                capital_operations = self._capital_operations
                if capital_operations is None:
                    capital_operations = build_default_capital_operations(
                        self._capital_transport
                    )
                result = build_capital_events_result(
                    request,
                    capital_operations,
                    self._identity_transport,
                )
        elif task_type == "market_signals":
            if not request["source_policy"]["allow_experimental"]:
                result = _blocked_result(
                    request,
                    code="source_policy_not_satisfied",
                    message=(
                        "market_signals currently requires experimental source "
                        "operations"
                    ),
                )
            else:
                market_signal_operations = self._market_signal_operations
                if market_signal_operations is None:
                    from .market_signal_registry import (
                        build_default_market_signal_operations,
                    )

                    market_signal_operations = build_default_market_signal_operations(
                        self._market_signal_transport
                    )
                result = build_market_signals_result(
                    request,
                    market_signal_operations,
                    self._identity_transport,
                )
        elif task_type == "intraday_market_signal":
            if not self._dependency_available("mootdx"):
                result = _blocked_result(
                    request,
                    code="missing_optional_dependency",
                    message=(
                        "intraday_market_data requires the optional mootdx Adapter "
                        "dependency"
                    ),
                    limitation_details={
                        "capability": "intraday_market_data",
                        "dependency": "mootdx",
                    },
                )
            else:
                result = _blocked_result(
                    request,
                    code="capability_not_implemented",
                    message=(
                        "intraday_market_signal is registered but not implemented yet"
                    ),
                )
        else:
            result = _blocked_result(
                request,
                code="unsupported_task_type",
                message=f"Research task type is not supported: {task_type}",
            )
        return _complete_result(result, task_type)

    def _research_identity(self, request: dict[str, Any]) -> dict[str, Any]:
        subjects = request["subjects"]
        if len(subjects) != 1:
            raise ValueError("security_identity requires exactly one subject")
        subject = subjects[0]
        if not isinstance(subject, dict):
            raise ValueError("each research subject must be a JSON object")
        clue = subject.get("clue")
        if not isinstance(clue, str) or not clue.strip():
            raise ValueError("security_identity subject requires a non-empty clue")
        return resolve_security_identity(
            clue.strip(),
            request["as_of"],
            self._identity_transport,
        )

    def _dependency_available(self, dependency: str) -> bool:
        if self._available_optional_dependencies is not None:
            return dependency in self._available_optional_dependencies
        return importlib.util.find_spec(dependency) is not None


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
) -> dict[str, Any]:
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
    if any(
        not isinstance(request["source_policy"][field], bool)
        for field in required_policy_fields
    ):
        raise ValueError("research source_policy fields must be booleans")


def _blocked_result(
    request: dict[str, Any],
    *,
    code: str,
    message: str,
    limitation_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limitation = {"code": code, "message": message}
    if limitation_details:
        limitation.update(limitation_details)
    return {
        "schema_version": request["schema_version"],
        "status": "blocked",
        "subjects": request["subjects"],
        "evidence": [],
        "conflicts": [],
        "source_errors": [],
        "degradations": [],
        "limitations": [limitation],
    }


def _complete_result(result: dict[str, Any], task_type: str) -> dict[str, Any]:
    completed = {
        **result,
        "task_type": task_type,
    }
    completed.setdefault("evidence", [])
    completed.setdefault("conflicts", [])
    completed.setdefault("source_errors", [])
    completed.setdefault("degradations", [])
    completed.setdefault("limitations", [])
    return completed
