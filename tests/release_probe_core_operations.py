#!/usr/bin/env python3
"""Maintainer-only release probe for plan-declared core operations (never CI)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping, Sequence, cast
from urllib.parse import urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from a_share_research.operation_release_matrix import (  # noqa: E402
    ReleasePlanSnapshot,
    build_operation_release_matrix,
    formal_producer_operation_ids,
    load_operation_qualification_manifest,
    load_release_probe_evidence,
)
from a_share_research.research_result import (  # noqa: E402
    validate_plan_operation_producer_authority,
)

from tests.release_probe_attempts import (  # noqa: E402
    assemble_release_probe_evidence,
    attempt_json_bytes,
    create_release_probe_attempt,
    load_release_probe_attempt,
    validate_release_probe_attempt_inputs,
)

PROBE_ID = "tests/release_probe_core_operations.py"
DEFAULT_QUALIFICATION = (
    REPOSITORY_ROOT / "docs" / "research" / "core-v0.3.0-operation-qualification.json"
)
_SAFE_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CREDENTIAL_FAILURE_CODES = {
    "credential_not_allowed",
    "credentials_not_allowed",
    "missing_credential",
    "permission_denied",
}
_RATE_LIMIT_FAILURE_CODES = {
    "rate_limit_exceeded",
    "rate_limited",
    "upstream_rate_limited",
}
_SOURCE_RESULT_PRECEDENCE = (
    "complete",
    "partial",
    "observed_empty",
    "credential_blocked",
    "rate_limited",
    "unavailable",
    "failed",
)

ProcessExecutor = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class _ProbeProgress:
    """Bounded release-probe progress safe to persist after a failed attempt."""

    verified_at: str
    stage: str
    reviewed_producer_operation_ids: tuple[str, ...] | None
    completed_request_profiles: tuple[str, ...] = ()
    failing_request_profile: str | None = None
    observed_producer_operation_ids: tuple[str, ...] = ()
    attempted_producer_operation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _release_timestamp(self.verified_at)
        if self.stage not in {
            "installation",
            "public_run",
            "public_result",
            "qualification_manifest",
            "truth_gate",
        }:
            raise ValueError("release probe progress stage is invalid")
        observed = set(self.observed_producer_operation_ids)
        attempted = set(self.attempted_producer_operation_ids)
        if self.reviewed_producer_operation_ids is None:
            if (
                self.stage != "qualification_manifest"
                or observed
                or attempted
                or self.completed_request_profiles
                or self.failing_request_profile is not None
            ):
                raise ValueError("release probe unavailable denominator is invalid")
            return
        reviewed = set(self.reviewed_producer_operation_ids)
        if (
            not reviewed
            or len(reviewed) != len(self.reviewed_producer_operation_ids)
            or tuple(sorted(reviewed)) != self.reviewed_producer_operation_ids
            or not observed <= reviewed
            or not attempted <= observed
        ):
            raise ValueError("release probe progress producer set is invalid")
        profiles = self.completed_request_profiles
        if len(set(profiles)) != len(profiles) or any(
            not _SAFE_PROFILE.fullmatch(profile) for profile in profiles
        ):
            raise ValueError("release probe completed profiles are invalid")
        if self.failing_request_profile is not None and (
            not _SAFE_PROFILE.fullmatch(self.failing_request_profile)
            or self.failing_request_profile in profiles
        ):
            raise ValueError("release probe failing profile is invalid")

    def failure_report(self) -> dict[str, object]:
        """Project only reviewed IDs and bounded public progress."""

        if self.reviewed_producer_operation_ids is None:
            return {
                "schema_version": "1.0",
                "status": "failed",
                "verified_at": self.verified_at,
                "stage": self.stage,
                "completed_request_profiles": [],
                "failing_request_profile": None,
                "denominator_status": "unavailable",
                "denominator_source": None,
                "external_producer_operation_ids": None,
                "observed_external_producer_operation_ids": [],
                "attempted_producer_operation_ids": [],
                "unattempted_producer_operation_ids": None,
            }
        reviewed = set(self.reviewed_producer_operation_ids)
        attempted = set(self.attempted_producer_operation_ids)
        return {
            "schema_version": "1.0",
            "status": "failed",
            "verified_at": self.verified_at,
            "stage": self.stage,
            "completed_request_profiles": list(self.completed_request_profiles),
            "failing_request_profile": self.failing_request_profile,
            "denominator_status": "available",
            "denominator_source": "reviewed_qualification_manifest",
            "external_producer_operation_ids": list(
                self.reviewed_producer_operation_ids
            ),
            "observed_external_producer_operation_ids": list(
                self.observed_producer_operation_ids
            ),
            "attempted_producer_operation_ids": list(
                self.attempted_producer_operation_ids
            ),
            "unattempted_producer_operation_ids": sorted(reviewed - attempted),
        }


class _ReleaseProbeFailure(RuntimeError):
    """Private failure signal carrying no provider or exception diagnostics."""

    def __init__(self, progress: _ProbeProgress) -> None:
        super().__init__("core operation release probe failed")
        self.progress = progress


@dataclass(frozen=True)
class ProbeRequestProfile:
    """One released/versioned plan variant exercised through the public CLI."""

    name: str
    request: Mapping[str, object]
    harness_timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if not _SAFE_PROFILE.fullmatch(self.name):
            raise ValueError("release probe request profile is unsafe")
        if not isinstance(self.request, Mapping):
            raise ValueError("release probe request profile is invalid")
        if (
            isinstance(self.harness_timeout_seconds, bool)
            or not isinstance(self.harness_timeout_seconds, int)
            or not 120 < self.harness_timeout_seconds <= 1200
        ):
            raise ValueError("release probe harness timeout is invalid")
        object.__setattr__(self, "request", deepcopy(dict(self.request)))


def _profile_execution_key(profile: ProbeRequestProfile) -> tuple[int, str]:
    """Run the session-bound snapshot first; keep every other profile stable."""

    return (0 if profile.name == "intraday-snapshot-sse" else 1, profile.name)


def build_release_request_profiles(
    *, as_of: str, replay_date: str
) -> tuple[ProbeRequestProfile, ...]:
    """Return bounded request variants; public plans remain operation authority."""

    as_of_value = date.fromisoformat(_date(as_of))
    replay_value = date.fromisoformat(_date(replay_date))
    if replay_value > as_of_value:
        raise ValueError("replay-date must not exceed as-of")
    recent = (as_of_value - timedelta(days=30)).isoformat()
    quarter = (as_of_value - timedelta(days=90)).isoformat()
    future = (as_of_value + timedelta(days=90)).isoformat()

    def policy(*, credentials: bool = False, fallback: bool = False) -> dict[str, bool]:
        return {
            "allow_experimental": True,
            "allow_credentials": credentials,
            "allow_fallback": fallback,
        }

    def profile(
        name: str,
        task_type: str,
        *,
        subjects: list[dict[str, object]],
        parameters: dict[str, object],
        window: dict[str, object] | None,
        credentials: bool = False,
        fallback: bool = False,
        harness_timeout_seconds: int = 300,
    ) -> ProbeRequestProfile:
        return ProbeRequestProfile(
            name=name,
            harness_timeout_seconds=harness_timeout_seconds,
            request={
                "schema_version": "1.0",
                "task_type": task_type,
                "subjects": subjects,
                "as_of": as_of,
                "window": window,
                "parameters": parameters,
                "source_policy": policy(
                    credentials=credentials,
                    fallback=fallback,
                ),
            },
        )

    profiles = (
        profile(
            "identity-standard",
            "security_identity",
            subjects=[{"clue": "600519"}],
            parameters={"completeness_mode": "standard"},
            window=None,
        ),
        profile(
            "identity-fast",
            "security_identity",
            subjects=[{"clue": "600519"}],
            parameters={"completeness_mode": "fast"},
            window=None,
        ),
        profile(
            "market-trend-unadjusted",
            "market_trend",
            subjects=[{"clue": "600519"}],
            parameters={"adjustment": "unadjusted"},
            window={"trading_days": 10},
            fallback=True,
        ),
        profile(
            "market-trend-forward-adjusted",
            "market_trend",
            subjects=[{"clue": "600519"}],
            parameters={"adjustment": "forward_adjusted"},
            window={"trading_days": 10},
            fallback=True,
        ),
        profile(
            "etf-market",
            "etf_market",
            subjects=[{"clue": "510050"}],
            parameters={},
            window=None,
            fallback=True,
        ),
        profile(
            "etf-options",
            "etf_options",
            subjects=[{"clue": "510050"}],
            parameters={
                "view": "atm",
                "expiry": {"mode": "nearest_unexpired"},
                "quote_mode": "latest",
            },
            window={
                "observed_from": as_of,
                "observed_to": as_of,
            },
        ),
        profile(
            "valuation-single",
            "security_valuation",
            subjects=[{"clue": "601138", "issuer_security_class_count": 1}],
            parameters={"target_pe": "30"},
            window=None,
            fallback=True,
        ),
        profile(
            "valuation-comparison",
            "valuation_compare",
            subjects=[
                {"clue": "601138", "issuer_security_class_count": 1},
                {"clue": "000001", "issuer_security_class_count": 1},
            ],
            parameters={"target_pe": "30"},
            window=None,
            fallback=True,
        ),
        profile(
            "content-subject-sse",
            "research_content",
            subjects=[{"clue": "601138"}],
            parameters={
                "material_types": [
                    "announcement",
                    "stock_news",
                    "investor_qa",
                    "issuer_profile",
                    "research_report",
                    "consensus_material",
                ],
                "profile_categories": ["公司概况", "股本结构"],
                "query": [],
                "limit": 20,
                "verify_documents": True,
            },
            window={"published_from": quarter, "published_to": as_of},
            fallback=True,
        ),
        profile(
            "content-subject-szse-announcement",
            "research_content",
            subjects=[{"clue": "300058"}],
            parameters={
                "material_types": ["announcement"],
                "query": [],
                "limit": 10,
                "verify_documents": True,
            },
            window={"published_from": quarter, "published_to": as_of},
        ),
        profile(
            "content-industry-report",
            "research_content",
            subjects=[],
            parameters={
                "material_types": ["industry_report"],
                "industry_code": "1037",
                "query": [],
                "limit": 10,
            },
            window={"published_from": quarter, "published_to": as_of},
        ),
        profile(
            "content-theme-credentialed",
            "research_content",
            subjects=[],
            parameters={
                "material_types": ["research_report"],
                "query": ["AI服务器", "算力产业链"],
                "limit": 10,
            },
            window={"published_from": quarter, "published_to": as_of},
            credentials=True,
        ),
        profile(
            "content-market-flash",
            "research_content",
            subjects=[],
            parameters={
                "material_types": ["market_flash"],
                "query": [],
                "limit": 20,
            },
            # Exercise both released owners through the public fallback path.
            # CLS requires a positive Unix timestamp, so no contract-valid row
            # can satisfy this historical window; CLS is still called and
            # validated before Eastmoney is attempted.
            window={"published_from": "0001-01-01", "published_to": "0001-01-01"},
            fallback=True,
        ),
        profile(
            "capital-subject",
            "capital_events",
            subjects=[{"clue": "601138"}],
            parameters={
                "data_types": [
                    "stock_fund_flow",
                    "dragon_tiger",
                    "lockup",
                    "margin_trading",
                    "block_trade",
                    "shareholder_count",
                    "dividend",
                ],
                "period": "5d",
                "limit": 20,
            },
            window={"observed_from": quarter, "observed_to": as_of},
        ),
        profile(
            "capital-board",
            "capital_events",
            subjects=[],
            parameters={
                "data_types": ["board_fund_flow"],
                "board_type": "industry",
                "period": "today",
                "limit": 20,
            },
            window={"observed_from": replay_date, "observed_to": replay_date},
        ),
        profile(
            "capital-market",
            "capital_events",
            subjects=[],
            parameters={
                "data_types": ["northbound_flow", "market_dragon_tiger"],
                "limit": 20,
            },
            window={"observed_from": recent, "observed_to": as_of},
        ),
        profile(
            "signals-subject",
            "market_signals",
            subjects=[{"clue": "300058"}],
            parameters={"signal_types": ["security_board_membership"], "limit": 20},
            window={"observed_from": as_of, "observed_to": as_of},
            fallback=True,
        ),
        profile(
            "signals-market",
            "market_signals",
            subjects=[],
            parameters={
                "signal_types": [
                    "strong_stock_theme",
                    "industry_rotation",
                    "limit_state",
                    "focus_monitoring",
                    "severe_abnormal_movement",
                    "monitoring_intersection",
                    "market_heat",
                ],
                "limit_states": [
                    "limit_up",
                    "limit_break",
                    "limit_down",
                    "previous_limit_up",
                ],
                "market_heat_period": "hour",
                "limit": 100,
            },
            window={"observed_from": recent, "observed_to": as_of},
            fallback=True,
        ),
        profile(
            "intraday-snapshot-sse",
            "intraday_market_signal",
            subjects=[{"security": "SSE:600519"}],
            parameters={},
            window=None,
        ),
        profile(
            "intraday-replay-sse",
            "intraday_replay",
            subjects=[{"security": "SSE:600519"}],
            parameters={},
            window={"observed_from": replay_date, "observed_to": replay_date},
        ),
        profile(
            "intraday-replay-szse",
            "intraday_replay",
            subjects=[{"security": "SZSE:000001"}],
            parameters={},
            window={"observed_from": replay_date, "observed_to": replay_date},
        ),
        profile(
            "workflow-single-security-research",
            "research_workflow",
            subjects=[{"clue": "601138", "issuer_security_class_count": 1}],
            parameters={
                "workflow": {"id": "single_security_research", "version": "1.0"},
                "inputs": {"target_pe": "30"},
            },
            window=None,
            harness_timeout_seconds=1200,
        ),
        profile(
            "workflow-single-security-valuation",
            "research_workflow",
            subjects=[{"clue": "601138", "issuer_security_class_count": 1}],
            parameters={
                "workflow": {"id": "single_security_valuation", "version": "1.0"},
                "inputs": {"target_pe": "30"},
            },
            window=None,
            harness_timeout_seconds=600,
        ),
        profile(
            "workflow-valuation-comparison",
            "research_workflow",
            subjects=[
                {"clue": "601138", "issuer_security_class_count": 1},
                {"clue": "000001", "issuer_security_class_count": 1},
            ],
            parameters={
                "workflow": {"id": "valuation_comparison", "version": "1.0"},
                "inputs": {"target_pe": "30"},
            },
            window=None,
            harness_timeout_seconds=900,
        ),
        profile(
            "workflow-theme-report-research",
            "research_workflow",
            subjects=[],
            parameters={
                "workflow": {"id": "theme_report_research", "version": "1.0"},
                "inputs": {
                    "query": ["人形机器人", "算力产业链"],
                    "published_from": quarter,
                    "published_to": as_of,
                    "limit": 20,
                    "verify_documents": True,
                },
            },
            window=None,
            credentials=True,
            harness_timeout_seconds=600,
        ),
        profile(
            "workflow-new-security-research",
            "research_workflow",
            subjects=[{"clue": "601138", "issuer_security_class_count": 1}],
            parameters={
                "workflow": {"id": "new_security_research", "version": "1.0"},
                "inputs": {
                    "target_pe": "30",
                    "report_window": {
                        "published_from": quarter,
                        "published_to": as_of,
                    },
                    "market_window": {
                        "observed_from": recent,
                        "observed_to": as_of,
                    },
                    "lockup_window": {
                        "observed_from": as_of,
                        "observed_to": future,
                    },
                    "fund_flow_period": "5d",
                    "limit": 20,
                    "verify_documents": False,
                },
            },
            window=None,
            harness_timeout_seconds=1200,
        ),
    )
    return tuple(sorted(profiles, key=lambda item: item.name))


def run_release_probe_attempt(
    *,
    skill_root: Path,
    profiles: Sequence[ProbeRequestProfile],
    verified_at: str,
    candidate_sha: str,
    execute: ProcessExecutor = subprocess.run,
) -> dict[str, object]:
    """Run one bounded public attempt without calling skipped owners attempted."""

    return _execute_release_probe(
        skill_root=skill_root,
        profiles=profiles,
        verified_at=verified_at,
        candidate_sha=candidate_sha,
        execute=execute,
    )


def _execute_release_probe(
    *,
    skill_root: Path,
    profiles: Sequence[ProbeRequestProfile],
    verified_at: str,
    candidate_sha: str,
    execute: ProcessExecutor,
) -> dict[str, object]:
    """Install a copy, call only its public run seam, and retain safe evidence."""

    canonical_verified_at = _verified_at_argument(verified_at)
    if not profiles or len({profile.name for profile in profiles}) != len(profiles):
        raise ValueError("release probe request profiles must be non-empty and unique")
    source_skill = skill_root.resolve()
    if (
        not (source_skill / "scripts" / "install.py").is_file()
        or not (source_skill / "scripts" / "entrypoint.py").is_file()
    ):
        raise ValueError("release probe Skill root is invalid")

    try:
        reviewed_producer_operation_ids = _reviewed_producer_operation_ids()
    except ValueError as error:
        raise _ReleaseProbeFailure(
            _ProbeProgress(
                verified_at=canonical_verified_at,
                stage="qualification_manifest",
                reviewed_producer_operation_ids=None,
            )
        ) from error
    reviewed_producer_operation_id_set = set(reviewed_producer_operation_ids)
    completed_request_profiles: list[str] = []
    observed_producer_operation_ids: set[str] = set()
    attempted_producer_operation_ids: set[str] = set()

    def failed_progress(
        stage: str, *, failing_request_profile: str | None = None
    ) -> _ReleaseProbeFailure:
        return _ReleaseProbeFailure(
            _ProbeProgress(
                verified_at=canonical_verified_at,
                stage=stage,
                reviewed_producer_operation_ids=reviewed_producer_operation_ids,
                completed_request_profiles=tuple(completed_request_profiles),
                failing_request_profile=failing_request_profile,
                observed_producer_operation_ids=tuple(
                    sorted(observed_producer_operation_ids)
                ),
                attempted_producer_operation_ids=tuple(
                    sorted(attempted_producer_operation_ids)
                ),
            )
        )

    with tempfile.TemporaryDirectory(prefix="a_share_core_release_probe_") as root:
        temporary_root = Path(root)
        installed_skill = temporary_root / "installed" / source_skill.name
        shutil.copytree(source_skill, installed_skill)
        isolated_home = temporary_root / "home"
        isolated_home.mkdir()
        environment = _probe_environment(isolated_home)
        install_environment = _install_environment(environment)
        provider_environment = _provider_environment(environment)
        try:
            installed = execute(
                [
                    sys.executable,
                    "-I",
                    str(installed_skill / "scripts" / "install.py"),
                ],
                cwd=temporary_root,
                env=install_environment,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if installed.returncode != 0:
                raise RuntimeError("copied Skill installation health is incomplete")
            installation_health = _safe_installation_health(
                _json_object(installed.stdout, "installation health")
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            raise failed_progress("installation") from error

        snapshots: list[dict[str, object]] = []
        evidence_by_producer: dict[str, list[dict[str, object]]] = {}
        copied_install_selectors: dict[str, set[str]] = {}
        for profile in sorted(profiles, key=_profile_execution_key):
            request_path = temporary_root / f"{profile.name}.request.json"
            try:
                request_path.write_text(
                    json.dumps(
                        profile.request,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                completed = execute(
                    [
                        sys.executable,
                        "-I",
                        str(installed_skill / "scripts" / "entrypoint.py"),
                        "run",
                        "--request",
                        str(request_path),
                    ],
                    cwd=temporary_root,
                    env=provider_environment,
                    capture_output=True,
                    text=True,
                    # This finite maintainer-harness ceiling is not a public
                    # direct/workflow execution-budget claim.
                    timeout=profile.harness_timeout_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError("release probe public run failed")
            except (
                OSError,
                RuntimeError,
                ValueError,
                subprocess.TimeoutExpired,
            ) as error:
                raise failed_progress(
                    "public_run", failing_request_profile=profile.name
                ) from error
            try:
                snapshot, evidence = sanitize_public_result(
                    request_profile=profile.name,
                    result=_json_object(completed.stdout, "public result"),
                    verified_at=canonical_verified_at,
                )
            except (RuntimeError, ValueError) as error:
                raise failed_progress(
                    "public_result", failing_request_profile=profile.name
                ) from error
            snapshots.append(snapshot)
            for raw_item in evidence:
                item = {**raw_item, "request_profile": profile.name}
                producer_operation_id = _text(
                    item.get("producer_operation_id"), "probe producer operation id"
                )
                if producer_operation_id not in reviewed_producer_operation_id_set:
                    raise failed_progress(
                        "public_result", failing_request_profile=profile.name
                    )
                observed_producer_operation_ids.add(producer_operation_id)
                if item.get("attempted") is True:
                    attempted_producer_operation_ids.add(producer_operation_id)
                evidence_by_producer.setdefault(producer_operation_id, []).append(item)
                copied_install_selectors.setdefault(producer_operation_id, set()).add(
                    profile.name
                )
            completed_request_profiles.append(profile.name)

    health_operation_ids = {
        cast(str, item["operation_id"])
        for item in cast(list[dict[str, object]], installation_health["operations"])
    }
    installation_loads = [
        {
            "producer_operation_id": producer_operation_id,
            "origin": (
                "installation_health"
                if producer_operation_id in health_operation_ids
                else "copied_install_plan"
            ),
            "status": "complete",
            "selector": (
                "installation_health"
                if producer_operation_id in health_operation_ids
                else min(copied_install_selectors[producer_operation_id])
            ),
        }
        for producer_operation_id in sorted(evidence_by_producer)
    ]
    probe_samples = [
        _attempt_probe_sample(
            producer_operation_id=producer_operation_id,
            values=values,
        )
        for producer_operation_id, values in sorted(evidence_by_producer.items())
    ]
    return create_release_probe_attempt(
        candidate_sha=candidate_sha,
        verified_at=canonical_verified_at,
        installation_health=installation_health,
        plan_snapshots=sorted(
            snapshots, key=lambda item: cast(str, item["request_profile"])
        ),
        installation_loads=installation_loads,
        probe_samples=probe_samples,
    )


def sanitize_public_result(
    *,
    request_profile: str,
    result: Mapping[str, object],
    verified_at: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Keep plan contracts and bounded statuses; drop every provider payload."""

    _release_timestamp(verified_at)
    if not _SAFE_PROFILE.fullmatch(request_profile):
        raise ValueError("public result contract has an unsafe request profile")
    if result.get("schema_version") != "1.0":
        raise ValueError("public result contract uses an unsupported schema")
    plan = _mapping(result.get("plan"), "public result contract plan")
    coverage = _mapping(result.get("coverage"), "public result contract coverage")
    error_codes = _source_error_codes(result.get("source_errors"))
    if plan.get("task_type") == "research_workflow":
        safe_result, safe_evidence = _sanitize_workflow_result(
            plan=plan,
            registry=_mapping(
                result.get("capability_registry"),
                "public result contract workflow registry",
            ),
            coverage=coverage,
            error_codes=error_codes,
            verified_at=verified_at,
        )
        return (
            {"request_profile": request_profile, "result": safe_result},
            safe_evidence,
        )
    safe_plan, safe_coverage, safe_evidence = _sanitize_plan_contract(
        plan=plan,
        coverage=coverage,
        error_codes=error_codes,
        verified_at=verified_at,
    )
    return (
        {
            "request_profile": request_profile,
            "result": {
                "schema_version": "1.0",
                "plan": safe_plan,
                "coverage": safe_coverage,
            },
        },
        safe_evidence,
    )


def _sanitize_plan_contract(
    *,
    plan: Mapping[str, object],
    coverage: Mapping[str, object],
    error_codes: Mapping[tuple[str, str], Sequence[str]],
    verified_at: str,
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    """Reduce one direct or workflow-leaf plan to its formal contracts."""

    obligations = _sequence(
        plan.get("operation_obligations"),
        "public result contract operation obligations",
    )
    if not obligations:
        raise ValueError("public result contract has no operation obligations")
    safe_plan: dict[str, object] = {
        "id": _text(plan.get("id"), "public result contract plan id"),
        "version": _text(plan.get("version"), "public result contract plan version"),
        "task_type": _text(plan.get("task_type"), "public result contract task type"),
        "capabilities": _string_list(
            plan.get("capabilities"), "public result contract capabilities"
        ),
        "operation_obligations": [],
        "research_dependencies": _safe_research_dependencies(
            plan.get("research_dependencies")
        ),
    }
    safe_coverage: dict[str, object] = {}
    safe_evidence: list[dict[str, object]] = []
    for raw_obligation in obligations:
        obligation = _mapping(
            raw_obligation, "public result contract operation obligation"
        )
        safe_obligation = _safe_obligation(obligation)
        cast(list[dict[str, object]], safe_plan["operation_obligations"]).append(
            safe_obligation
        )
        capability_id = cast(str, safe_obligation["capability_id"])
        operation_id = cast(str, safe_obligation["operation_id"])
        producer_operation_id = cast(str, safe_obligation["producer_operation_id"])
        aggregate = _mapping(
            coverage.get(capability_id),
            "public result contract capability coverage",
        )
        operations = _mapping(
            aggregate.get("operations"),
            "public result contract operation coverage",
        )
        operation = _mapping(
            operations.get(operation_id),
            "public result contract owned operation coverage",
        )
        safe_operation = _safe_operation(operation, capability_id, operation_id)
        aggregate_output = safe_coverage.setdefault(capability_id, {"operations": {}})
        aggregate_operations = cast(
            dict[str, object],
            cast(dict[str, object], aggregate_output)["operations"],
        )
        if operation_id in aggregate_operations:
            raise ValueError("public result contract operation is duplicated")
        aggregate_operations[operation_id] = safe_operation
        if safe_obligation["operation_kind"] == "external_source":
            safe_evidence.append(
                {
                    "producer_operation_id": producer_operation_id,
                    "probe": PROBE_ID,
                    "probe_status": "passed",
                    "attempted": safe_operation["execution_state"]
                    in {"succeeded", "failed"},
                    "source_result": _source_result(
                        safe_operation,
                        error_codes.get((capability_id, operation_id), ()),
                    ),
                    "verified_at": verified_at,
                }
            )
    if set(safe_coverage) != set(cast(list[str], safe_plan["capabilities"])):
        raise ValueError("public result contract capabilities do not close")
    validate_plan_operation_producer_authority(safe_plan)
    return safe_plan, safe_coverage, safe_evidence


def _sanitize_workflow_result(
    *,
    plan: Mapping[str, object],
    registry: Mapping[str, object],
    coverage: Mapping[str, object],
    error_codes: Mapping[tuple[str, str], Sequence[str]],
    verified_at: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    plan_steps = _sequence(plan.get("steps"), "public workflow plan steps")
    registry_steps = _mapping(registry.get("steps"), "public workflow registry steps")
    coverage_steps = _mapping(coverage.get("steps"), "public workflow coverage steps")
    safe_steps: list[dict[str, object]] = []
    safe_registry_steps: dict[str, object] = {}
    safe_coverage_steps: dict[str, object] = {}
    evidence: list[dict[str, object]] = []
    for raw_step in plan_steps:
        step = _mapping(raw_step, "public workflow plan step")
        if set(step) != {
            "step_id",
            "version",
            "task_type",
            "criticality",
            "depends_on",
        }:
            raise ValueError("public result contract workflow step is invalid")
        step_id = _text(step.get("step_id"), "workflow step id")
        if step_id in safe_registry_steps:
            raise ValueError("public result contract workflow step is duplicated")
        safe_step: dict[str, object] = {
            "step_id": step_id,
            "version": _text(step.get("version"), "workflow step version"),
            "task_type": _text(step.get("task_type"), "workflow step task"),
            "criticality": _text(step.get("criticality"), "workflow criticality"),
            "depends_on": _string_list_or_empty(
                step.get("depends_on"), "workflow dependencies"
            ),
        }
        registered = _mapping(
            registry_steps.get(step_id), "public workflow registered step"
        )
        covered = _mapping(coverage_steps.get(step_id), "public workflow covered step")
        if any(
            registered.get(key) != safe_step[key]
            for key in ("step_id", "version", "task_type")
        ):
            raise ValueError("public result contract workflow step drifted")
        leaf_plan_value = registered.get("leaf_plan")
        leaf_coverage_value = covered.get("leaf")
        if leaf_plan_value is None:
            if leaf_coverage_value != {}:
                raise ValueError("public result contract skipped workflow leaf drifted")
            safe_leaf_plan = None
            safe_leaf_coverage: dict[str, object] = {}
        else:
            safe_leaf_plan, safe_leaf_coverage, leaf_evidence = _sanitize_plan_contract(
                plan=_mapping(leaf_plan_value, "public workflow leaf plan"),
                coverage=_mapping(leaf_coverage_value, "public workflow leaf coverage"),
                error_codes=error_codes,
                verified_at=verified_at,
            )
            if (
                safe_leaf_plan["task_type"] != safe_step["task_type"]
                or safe_leaf_plan["version"] != safe_step["version"]
            ):
                raise ValueError("public result contract workflow leaf drifted")
            evidence.extend(leaf_evidence)
        safe_steps.append(safe_step)
        safe_registry_steps[step_id] = {
            "step_id": step_id,
            "task_type": safe_step["task_type"],
            "version": safe_step["version"],
            "leaf_plan": safe_leaf_plan,
        }
        safe_coverage_steps[step_id] = {"leaf": safe_leaf_coverage}
    if set(registry_steps) != set(safe_registry_steps) or set(coverage_steps) != set(
        safe_coverage_steps
    ):
        raise ValueError("public result contract workflow steps do not close")
    return (
        {
            "schema_version": "1.0",
            "plan": {
                "id": _text(plan.get("id"), "public workflow plan id"),
                "version": _text(plan.get("version"), "public workflow plan version"),
                "task_type": "research_workflow",
                "workflow_id": _text(plan.get("workflow_id"), "public workflow id"),
                "steps": safe_steps,
            },
            "capability_registry": {"steps": safe_registry_steps},
            "coverage": {"steps": safe_coverage_steps},
        },
        evidence,
    )


def _safe_obligation(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "capability_id",
        "operation_id",
        "producer_operation_id",
        "operation_kind",
        "source_role",
        "experimental",
        "exchange",
        "evidence_uses",
    }
    if set(value) != required:
        raise ValueError("public result contract operation obligation is invalid")
    evidence_uses = _sequence(
        value.get("evidence_uses"), "public result contract evidence uses"
    )
    safe_uses: list[dict[str, str]] = []
    for raw_use in evidence_uses:
        evidence_use = _mapping(raw_use, "public result contract evidence use")
        if set(evidence_use) != {"use", "role"}:
            raise ValueError("public result contract evidence use is invalid")
        safe_uses.append(
            {
                "use": _text(evidence_use.get("use"), "evidence use"),
                "role": _text(evidence_use.get("role"), "evidence role"),
            }
        )
    experimental = value.get("experimental")
    exchange = value.get("exchange")
    if not isinstance(experimental, bool) or exchange not in {None, "SSE", "SZSE"}:
        raise ValueError("public result contract operation obligation is invalid")
    return {
        "capability_id": _text(value.get("capability_id"), "capability id"),
        "operation_id": _text(value.get("operation_id"), "operation id"),
        "producer_operation_id": _text(
            value.get("producer_operation_id"), "producer operation id"
        ),
        "operation_kind": _operation_kind(value.get("operation_kind")),
        "source_role": _text(value.get("source_role"), "source role"),
        "experimental": experimental,
        "exchange": exchange,
        "evidence_uses": safe_uses,
    }


def _operation_kind(value: object) -> str:
    if value not in {"external_source", "deterministic_coordinator"}:
        raise ValueError("public result contract operation kind is invalid")
    return cast(str, value)


def _safe_research_dependencies(value: object) -> list[dict[str, object]]:
    safe: list[dict[str, object]] = []
    for raw_dependency in _sequence(
        value, "public result contract research dependencies"
    ):
        dependency = _mapping(
            raw_dependency, "public result contract research dependency"
        )
        if set(dependency) != {
            "consumer_id",
            "evidence_use",
            "minimum_supporting_observations",
            "core",
        }:
            raise ValueError("public result contract research dependency is invalid")
        minimum = dependency.get("minimum_supporting_observations")
        core = dependency.get("core")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
            or not isinstance(core, bool)
        ):
            raise ValueError("public result contract research dependency is invalid")
        safe.append(
            {
                "consumer_id": _text(
                    dependency.get("consumer_id"), "research dependency consumer"
                ),
                "evidence_use": _text(
                    dependency.get("evidence_use"), "research dependency use"
                ),
                "minimum_supporting_observations": minimum,
                "core": core,
            }
        )
    return safe


def _safe_operation(
    value: Mapping[str, object], capability_id: str, operation_id: str
) -> dict[str, object]:
    if (
        value.get("capability_id") != capability_id
        or value.get("operation_id") != operation_id
    ):
        raise ValueError("public result contract operation owner is invalid")
    execution_state = _text(value.get("execution_state"), "operation execution state")
    if execution_state not in {
        "not_requested",
        "skipped_policy",
        "skipped_research_dependency",
        "failed",
        "succeeded",
    }:
        raise ValueError("public result contract operation execution is invalid")
    state = _text(value.get("state"), "operation coverage state")
    query_complete = value.get("query_complete")
    if query_complete not in {None, True, False}:
        raise ValueError("public result contract query completeness is invalid")
    dispositions = _sequence(
        value.get("field_dispositions"),
        "public result contract field dispositions",
    )
    if not dispositions or any(not isinstance(item, Mapping) for item in dispositions):
        raise ValueError("public result contract field dispositions are invalid")
    safe_dispositions: list[dict[str, object]] = []
    for raw_disposition in dispositions:
        disposition = _mapping(
            raw_disposition, "public result contract field disposition"
        )
        disposition_kind = disposition.get("disposition")
        expected = {"field", "disposition", "destination"}
        if disposition_kind == "excluded":
            expected.add("reason")
        if set(disposition) != expected:
            raise ValueError("public result contract field dispositions are invalid")
        destination = disposition.get("destination")
        reason = disposition.get("reason")
        if disposition_kind == "excluded":
            if destination is not None or not isinstance(reason, str) or not reason:
                raise ValueError(
                    "public result contract field dispositions are invalid"
                )
        elif (
            disposition_kind
            not in {"normalized", "cross_check_only", "lineage_only", "retained"}
            or not isinstance(destination, str)
            or not destination
            or reason is not None
        ):
            raise ValueError("public result contract field dispositions are invalid")
        safe_dispositions.append(deepcopy(dict(disposition)))
    return {
        "capability_id": capability_id,
        "operation_id": operation_id,
        "execution_state": execution_state,
        "state": state,
        "query_complete": query_complete,
        "field_dispositions": safe_dispositions,
    }


def _source_error_codes(
    value: object,
) -> dict[tuple[str, str], tuple[str, ...]]:
    if value is None:
        return {}
    errors = _sequence(value, "public result contract source errors")
    indexed: dict[tuple[str, str], list[str]] = {}
    for raw_error in errors:
        error = _mapping(raw_error, "public result contract source error")
        owner = (
            _text(error.get("capability_id"), "source error capability id"),
            _text(error.get("operation_id"), "source error operation id"),
        )
        indexed.setdefault(owner, []).append(
            _text(error.get("code"), "source error code")
        )
    return {owner: tuple(codes) for owner, codes in indexed.items()}


def _source_result(
    operation: Mapping[str, object], failure_codes: Sequence[str]
) -> str:
    state = operation["state"]
    if state in {"complete", "observed_empty", "partial"}:
        return cast(str, state)
    if any(code in _CREDENTIAL_FAILURE_CODES for code in failure_codes):
        return "credential_blocked"
    if any(code in _RATE_LIMIT_FAILURE_CODES for code in failure_codes):
        return "rate_limited"
    if state in {
        "failed",
        "not_requested",
        "skipped_policy",
        "skipped_research_dependency",
    }:
        return "unavailable"
    raise ValueError("public result contract coverage state is unsupported")


def _combined_source_result(values: Sequence[Mapping[str, object]]) -> str:
    observed = {value["source_result"] for value in values}
    for source_result in _SOURCE_RESULT_PRECEDENCE:
        if source_result in observed:
            return source_result
    raise ValueError("release probe source results are invalid")


def _attempt_probe_sample(
    *,
    producer_operation_id: str,
    values: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Retain one owner sample while keeping skipped applications unattempted."""

    attempted = [value for value in values if value.get("attempted") is True]
    selected = attempted or list(values)
    if not selected:
        raise ValueError("release probe attempt has no owner applications")
    source_result = _combined_source_result(selected)
    request_profile = min(
        _text(value.get("request_profile"), "probe request profile")
        for value in selected
        if value.get("source_result") == source_result
    )
    return {
        "producer_operation_id": producer_operation_id,
        "probe": PROBE_ID,
        "probe_status": "passed",
        "attempted": bool(attempted),
        "source_result": source_result,
        "request_profile": request_profile,
    }


def _json_object(value: str, label: str) -> Mapping[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} did not return public JSON") from error
    return _mapping(parsed, label)


def _probe_environment(home: Path) -> dict[str, str]:
    """Build the credential-free base shared by copied Skill processes."""

    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
    }


def _install_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Add only validated host routing facts for the copied installer."""

    return _routing_environment(base)


def _provider_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Add safe routing, but never credentials or general host configuration."""

    return _routing_environment(base)


def _routing_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Copy only bounded, credential-free HTTP routing variables."""

    environment = dict(base)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    ):
        value = os.environ.get(key)
        if value and _safe_proxy_route(value):
            environment[key] = value
    for key in (
        "NO_PROXY",
        "no_proxy",
    ):
        value = os.environ.get(key)
        if value and _safe_no_proxy_route(value):
            environment[key] = value
    return environment


def _safe_proxy_route(value: str) -> bool:
    if not _bounded_routing_value(value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and (port is None or 0 < port <= 65535)
    )


def _safe_no_proxy_route(value: str) -> bool:
    if not _bounded_routing_value(value):
        return False
    tokens = value.split(",")
    return bool(
        tokens
        and all(
            token
            and len(token) <= 255
            and re.fullmatch(r"[A-Za-z0-9.*_:/\[\]-]+", token) is not None
            for token in tokens
        )
    )


def _bounded_routing_value(value: str) -> bool:
    return bool(
        value
        and len(value) <= 2048
        and value == value.strip()
        and all(character >= " " and character != "\x7f" for character in value)
    )


def _safe_installation_health(value: Mapping[str, object]) -> dict[str, object]:
    expected = {
        "schema_version",
        "status",
        "dependencies",
        "operations",
        "source_policy",
    }
    if set(value) != expected or value.get("schema_version") != "1.0":
        raise RuntimeError("copied Skill installation health is invalid")
    if value.get("status") != "complete":
        raise RuntimeError("copied Skill installation health is incomplete")
    dependencies: list[dict[str, object]] = []
    for raw_fact in _sequence(value.get("dependencies"), "health dependencies"):
        fact = _mapping(raw_fact, "health dependency")
        if set(fact) != {
            "distribution",
            "required_version",
            "installed_version",
            "status",
            "modules",
            "operation_ids",
        }:
            raise RuntimeError("copied Skill installation health is invalid")
        installed_version = fact.get("installed_version")
        if fact.get("status") != "complete" or not isinstance(installed_version, str):
            raise RuntimeError("copied Skill installation health is incomplete")
        dependencies.append(
            {
                "distribution": _fact_token(fact.get("distribution")),
                "required_version": _fact_token(fact.get("required_version")),
                "installed_version": _fact_token(installed_version),
                "status": "complete",
                "modules": [
                    _fact_token(item)
                    for item in _sequence(fact.get("modules"), "health modules")
                ],
                "operation_ids": [
                    _fact_token(item)
                    for item in _sequence(
                        fact.get("operation_ids"), "health operation ids"
                    )
                ],
            }
        )
    operations: list[dict[str, str]] = []
    for raw_fact in _sequence(value.get("operations"), "health operations"):
        fact = _mapping(raw_fact, "health operation")
        if set(fact) != {"operation_id", "status"} or fact.get("status") != "complete":
            raise RuntimeError("copied Skill installation health is incomplete")
        operations.append(
            {
                "operation_id": _fact_token(fact.get("operation_id")),
                "status": "complete",
            }
        )
    source_policy = _mapping(value.get("source_policy"), "health source policy")
    if set(source_policy) != {
        "source_accessibility",
        "credentials",
        "evidence_adoption",
    } or any(item != "not_checked" for item in source_policy.values()):
        raise RuntimeError("copied Skill installation health is invalid")
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


def _fact_token(value: object) -> str:
    token = _text(value, "installation health fact")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@+!-]{0,127}", token):
        raise RuntimeError("copied Skill installation health is invalid")
    return token


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} is invalid")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} is invalid")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{label} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} is invalid")
    return value


def _string_list(value: object, label: str) -> list[str]:
    items = _sequence(value, label)
    result = [_text(item, label) for item in items]
    if not result or len(result) != len(set(result)):
        raise ValueError(f"{label} is invalid")
    return result


def _string_list_or_empty(value: object, label: str) -> list[str]:
    items = _sequence(value, label)
    result = [_text(item, label) for item in items]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} is invalid")
    return result


def _release_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("verified-at must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("verified-at must include a timezone")
    return parsed


def _verified_at_argument(value: str) -> str:
    """Validate once and retain a canonical, serializable release timestamp."""

    try:
        return _release_timestamp(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _validate_release_report(report: Mapping[str, object]) -> None:
    """Require the public probe to close the reviewed release denominator."""

    snapshots, installation_loads, installation_health, live_probes = (
        load_release_probe_evidence(report)
    )
    denominator = formal_producer_operation_ids(snapshots)
    try:
        qualification_payload = json.loads(
            DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("operation qualification manifest is unreadable") from error
    qualification = load_operation_qualification_manifest(
        _mapping(qualification_payload, "operation qualification manifest"),
        expected_producer_operation_ids=denominator,
    )
    build_operation_release_matrix(
        plan_snapshots=snapshots,
        qualification_evidence=qualification,
        installation_loads=installation_loads,
        installation_health=installation_health,
        live_probes=live_probes,
    )


def _reviewed_producer_operation_ids() -> tuple[str, ...]:
    """Load the reviewed v0.3.0 owner set without creating a second registry."""

    try:
        payload = _mapping(
            json.loads(DEFAULT_QUALIFICATION.read_text(encoding="utf-8")),
            "operation qualification manifest",
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("operation qualification manifest is unreadable") from error
    candidate_ids = tuple(
        _text(
            _mapping(item, "qualification operation").get("producer_operation_id"),
            "qualification producer operation id",
        )
        for item in _sequence(payload.get("operations"), "qualification operations")
    )
    qualification = load_operation_qualification_manifest(
        payload,
        expected_producer_operation_ids=candidate_ids,
    )
    reviewed = tuple(item.producer_operation_id for item in qualification)
    if len(reviewed) != 44:
        raise ValueError("reviewed release denominator is incomplete")
    return reviewed


def _truth_gate_progress(
    report: Mapping[str, object], *, verified_at: str
) -> _ProbeProgress:
    """Reproject already-sanitized report facts after final closure rejects them."""

    reviewed = _reviewed_producer_operation_ids()
    reviewed_set = set(reviewed)
    snapshots: list[ReleasePlanSnapshot] = []
    completed_profiles: set[str] = set()
    for raw_snapshot in _sequence(report.get("plan_snapshots"), "plan snapshots"):
        snapshot = _mapping(raw_snapshot, "plan snapshot")
        profile = _text(snapshot.get("request_profile"), "request profile")
        if not _SAFE_PROFILE.fullmatch(profile):
            raise ValueError("release probe report profiles are invalid")
        completed_profiles.add(profile)
        snapshots.append(
            ReleasePlanSnapshot(
                request_profile=profile,
                result=_mapping(snapshot.get("result"), "plan result"),
            )
        )
    observed = set(formal_producer_operation_ids(snapshots))
    attempted: set[str] = set()
    for raw_probe in _sequence(report.get("live_probes"), "live probes"):
        probe = _mapping(raw_probe, "live probe")
        producer_operation_id = _text(
            probe.get("producer_operation_id"), "live producer operation id"
        )
        if producer_operation_id in reviewed_set and probe.get("attempted") is True:
            attempted.add(producer_operation_id)
    return _ProbeProgress(
        verified_at=verified_at,
        stage="truth_gate",
        reviewed_producer_operation_ids=reviewed,
        completed_request_profiles=tuple(sorted(completed_profiles)),
        observed_producer_operation_ids=tuple(sorted(observed & reviewed_set)),
        attempted_producer_operation_ids=tuple(sorted(attempted)),
    )


def _attempt_union_progress(
    attempts: Sequence[Mapping[str, object]], *, verified_at: str
) -> _ProbeProgress:
    """Project only reviewed owner progress from validated partial attempts."""

    reviewed = _reviewed_producer_operation_ids()
    reviewed_set = set(reviewed)
    completed_profiles: set[str] = set()
    observed: set[str] = set()
    attempted: set[str] = set()
    for attempt in attempts:
        for raw_sample in _sequence(attempt.get("probe_samples"), "probe samples"):
            sample = _mapping(raw_sample, "probe sample")
            profile = _text(sample.get("request_profile"), "request profile")
            if not _SAFE_PROFILE.fullmatch(profile):
                raise ValueError("release probe attempt profile is invalid")
            completed_profiles.add(profile)
            owner = _text(
                sample.get("producer_operation_id"), "probe producer operation id"
            )
            if owner not in reviewed_set:
                continue
            observed.add(owner)
            if sample.get("attempted") is True:
                attempted.add(owner)
    return _ProbeProgress(
        verified_at=verified_at,
        stage="truth_gate",
        reviewed_producer_operation_ids=reviewed,
        completed_request_profiles=tuple(sorted(completed_profiles)),
        observed_producer_operation_ids=tuple(sorted(observed)),
        attempted_producer_operation_ids=tuple(sorted(attempted)),
    )


def _validate_attempt_contract(
    attempt: Mapping[str, object], *, configured_profiles: Sequence[str] | None = None
) -> None:
    """Close one partial attempt against its own sanitized public plans."""

    digest = _text(attempt.get("attempt_digest"), "attempt digest")
    candidate_sha = attempt.get("candidate_sha")
    verified_at = attempt.get("verified_at")
    decorated_snapshots = [
        {
            **dict(_mapping(raw, "release plan snapshot")),
            "candidate_sha": candidate_sha,
            "verified_at": verified_at,
            "attempt_digest": digest,
        }
        for raw in _sequence(attempt.get("plan_snapshots"), "plan snapshots")
    ]
    snapshots, _, _, _ = load_release_probe_evidence(
        {
            "schema_version": "1.0",
            "candidate_sha": candidate_sha,
            "verified_at": verified_at,
            "attempt_digests": [digest],
            "installation_health": attempt.get("installation_health"),
            "plan_snapshots": decorated_snapshots,
            "installation_loads": attempt.get("installation_loads"),
            "live_probes": attempt.get("probe_samples"),
        }
    )
    if configured_profiles is not None:
        configured = set(configured_profiles)
        if not configured or len(configured) != len(configured_profiles):
            raise ValueError("release probe configured profiles are invalid")
        snapshot_profiles = {snapshot.request_profile for snapshot in snapshots}
        if not snapshot_profiles <= configured:
            raise ValueError("release probe attempt profile is unconfigured")
    if not set(formal_producer_operation_ids(snapshots)) <= set(
        _reviewed_producer_operation_ids()
    ):
        raise ValueError("release probe attempt contains an unreviewed producer")


def _write_atomic(path: Path, payload: bytes) -> None:
    """Replace one release artifact only after complete durable temp output."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_atomic_new(path: Path, payload: bytes) -> None:
    """Atomically create one attempt artifact without replacing any file."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _validated_failure_output(output: Path, failure_output: Path | None) -> Path | None:
    """Accept only a fresh attempt-specific artifact distinct from final output."""

    if failure_output is None:
        return None
    normalized_output = output.resolve(strict=False)
    normalized_failure = failure_output.resolve(strict=False)
    if normalized_failure == normalized_output or normalized_failure.exists():
        raise ValueError("release probe failure output is invalid")
    return normalized_failure


def _validated_attempt_paths(
    *,
    output: Path,
    failure_output: Path | None,
    attempt_inputs: Sequence[Path],
    attempt_output: Path | None,
) -> tuple[tuple[Path, ...], Path]:
    """Validate append-only attempt paths before any copied process runs."""

    normalized_inputs = tuple(path.resolve(strict=False) for path in attempt_inputs)
    if len(set(normalized_inputs)) != len(normalized_inputs):
        raise ValueError("release probe attempt input is duplicated")
    if attempt_output is None:
        raise ValueError("release probe attempt output is required")
    normalized_output = attempt_output.resolve(strict=False)
    reserved = {output.resolve(strict=False), *normalized_inputs}
    if failure_output is not None:
        reserved.add(failure_output)
    if normalized_output in reserved or normalized_output.exists():
        raise ValueError("release probe attempt output is invalid")
    return normalized_inputs, normalized_output


def _repository_candidate_sha() -> str:
    """Bind persisted release evidence to one clean repository candidate."""

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    candidate_sha = completed.stdout.strip()
    if (
        completed.returncode != 0
        or re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None
    ):
        raise ValueError("release candidate SHA is unavailable")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if status.returncode != 0 or status.stdout:
        raise ValueError("release candidate worktree is not clean")
    return candidate_sha


def _date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Maintainer-only explicit core-operation release probe (never CI)."
        )
    )
    parser.add_argument("--confirm-live", action="store_true", required=True)
    parser.add_argument("--verified-at", type=_verified_at_argument, required=True)
    parser.add_argument("--as-of", type=_date, required=True)
    parser.add_argument("--replay-date", type=_date, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--failure-output", type=Path)
    parser.add_argument("--attempt-input", action="append", type=Path, default=[])
    parser.add_argument("--attempt-output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        failure_output = _validated_failure_output(
            arguments.output, arguments.failure_output
        )
        attempt_inputs, attempt_output = _validated_attempt_paths(
            output=arguments.output,
            failure_output=failure_output,
            attempt_inputs=arguments.attempt_input,
            attempt_output=arguments.attempt_output,
        )
        profiles = build_release_request_profiles(
            as_of=arguments.as_of,
            replay_date=arguments.replay_date,
        )
        skill_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        candidate_sha = _repository_candidate_sha()
        prior_attempts = [
            load_release_probe_attempt(
                path,
                expected_candidate_sha=candidate_sha,
                assembled_at=arguments.verified_at,
            )
            for path in attempt_inputs
        ]
        configured_profiles = tuple(profile.name for profile in profiles)
        for prior_attempt in prior_attempts:
            _validate_attempt_contract(
                prior_attempt, configured_profiles=configured_profiles
            )
        prior_attempts = list(
            validate_release_probe_attempt_inputs(
                prior_attempts,
                candidate_sha=candidate_sha,
                assembled_at=arguments.verified_at,
            )
        )
        current_attempt = run_release_probe_attempt(
            skill_root=skill_root,
            profiles=profiles,
            verified_at=arguments.verified_at,
            candidate_sha=candidate_sha,
        )
        _validate_attempt_contract(
            current_attempt, configured_profiles=configured_profiles
        )
        _write_atomic_new(attempt_output, attempt_json_bytes(current_attempt))
        all_attempts = (*prior_attempts, current_attempt)
        try:
            report = assemble_release_probe_evidence(
                all_attempts,
                candidate_sha=candidate_sha,
                assembled_at=arguments.verified_at,
            )
        except ValueError as error:
            raise _ReleaseProbeFailure(
                _attempt_union_progress(all_attempts, verified_at=arguments.verified_at)
            ) from error
        serialized = (
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        try:
            _validate_release_report(report)
        except ValueError as error:
            raise _ReleaseProbeFailure(
                _truth_gate_progress(report, verified_at=arguments.verified_at)
            ) from error
        _write_atomic(arguments.output, serialized)
    except _ReleaseProbeFailure as error:
        if failure_output is not None:
            try:
                failure_serialized = (
                    json.dumps(
                        error.progress.failure_report(),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                _write_atomic_new(failure_output, failure_serialized)
            except (OSError, ValueError):
                pass
        sys.stderr.write("error: core operation release probe failed\n")
        return 2
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        sys.stderr.write("error: core operation release probe failed\n")
        return 2
    summary = {
        "schema_version": "1.0",
        "status": "complete",
        "verified_at": arguments.verified_at,
        "profile_count": len(cast(list[object], report["plan_snapshots"])),
        "operation_count": len(cast(list[object], report["live_probes"])),
        "output": str(arguments.output),
        "sha256": sha256(serialized).hexdigest(),
        **(
            {
                "candidate_sha": report["candidate_sha"],
                "attempt_count": len(cast(list[object], report["attempt_digests"])),
            }
            if "candidate_sha" in report and "attempt_digests" in report
            else {}
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
