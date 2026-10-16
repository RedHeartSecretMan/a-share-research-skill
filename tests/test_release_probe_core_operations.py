from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import Callable, Mapping, cast
from unittest.mock import patch

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.research_result import (  # noqa: E402
    DirectTaskPlan,
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    fold_direct_task_research_result,
    stable_result_id,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPOSITORY_ROOT / "tests" / "release_probe_core_operations.py"
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
SPEC = importlib.util.spec_from_file_location(
    "release_probe_core_operations", PROBE_PATH
)
assert SPEC is not None and SPEC.loader is not None
release_probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_probe
SPEC.loader.exec_module(release_probe)

_RC_VERIFIED_AT = "2026-08-13T09:35:00+08:00"
_RC_AS_OF = "2026-08-13"
_RC_REPLAY_DATE = "2026-08-12"
_ORIGINAL_FINAL = b"existing FINAL candidate bytes must survive\n"
_RC_CANDIDATE_SHA = "a" * 40
_IDENTITY_PRODUCERS = (
    "cninfo_security_dictionary@1",
    "sse_stock_list@1",
    "szse_stock_list@1",
)
_NONFAILED_CREDENTIAL_CONTRADICTIONS = (
    (
        "succeeded-observed-empty",
        "succeeded",
        "observed_empty",
        True,
        True,
        "observed_empty",
    ),
    (
        "not-requested",
        "not_requested",
        "not_requested",
        None,
        False,
        "unavailable",
    ),
    (
        "skipped-policy",
        "skipped_policy",
        "skipped_policy",
        None,
        False,
        "unavailable",
    ),
    (
        "skipped-research-dependency",
        "skipped_research_dependency",
        "skipped_research_dependency",
        None,
        False,
        "unavailable",
    ),
)


def _mark_external_fixture_applications_failed(result: dict[str, object]) -> None:
    """Turn a fixed no-I/O public sample into attempted failure evidence."""

    def mark(plan: dict[str, object], coverage: dict[str, object]) -> None:
        for obligation in plan["operation_obligations"]:
            if obligation["operation_kind"] != "external_source":
                continue
            # #96's copied-installed deterministic owners already attempted
            # their accepted normalization paths; retain those real outcomes.
            if obligation["capability_id"] in {
                "market_trading_session",
                "major_broad_indices",
                "whole_market_turnover",
                "whole_market_margin",
            }:
                continue
            operation = coverage[obligation["capability_id"]]["operations"][
                obligation["operation_id"]
            ]
            operation["execution_state"] = "failed"
            operation["state"] = "failed"
            operation["query_complete"] = None

    plan = result["plan"]
    if plan["task_type"] != "research_workflow":
        mark(plan, result["coverage"])
        return
    registry_steps = result["capability_registry"]["steps"]
    coverage_steps = result["coverage"]["steps"]
    for step_id, registered in registry_steps.items():
        if registered["leaf_plan"] is not None:
            mark(registered["leaf_plan"], coverage_steps[step_id]["leaf"])


def _complete_installation_health() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "complete",
        "dependencies": [
            {
                "distribution": "baostock",
                "required_version": "0.9.4",
                "installed_version": "0.9.4",
                "status": "complete",
                "modules": ["baostock"],
                "operation_ids": ["baostock_dated_roster_acquisition@1"],
            },
            {
                "distribution": "pypdf",
                "required_version": "6.10.2",
                "installed_version": "6.10.2",
                "status": "complete",
                "modules": ["pypdf"],
                "operation_ids": ["szse_formal_listing_material@1"],
            },
        ],
        "operations": [],
        "source_policy": {
            "source_accessibility": "not_checked",
            "credentials": "not_checked",
            "evidence_adoption": "not_checked",
        },
    }


def _reviewed_qualification_producers() -> tuple[str, ...]:
    qualification = json.loads(
        (
            REPOSITORY_ROOT
            / "docs"
            / "research"
            / "v0.3.0-operation-qualification.json"
        ).read_text(encoding="utf-8")
    )
    return tuple(
        sorted(
            operation["producer_operation_id"]
            for operation in qualification["operations"]
        )
    )


def _rc_main_argv(
    output: Path,
    failure_output: Path | None = None,
    *,
    attempt_input: Path | None = None,
    attempt_output: Path | None = None,
    include_attempt_output: bool = True,
) -> list[str]:
    argv = [
        "--confirm-live",
        "--verified-at",
        _RC_VERIFIED_AT,
        "--as-of",
        _RC_AS_OF,
        "--replay-date",
        _RC_REPLAY_DATE,
        "--output",
        str(output),
    ]
    if failure_output is not None:
        argv.extend(("--failure-output", str(failure_output)))
    if attempt_input is not None:
        argv.extend(("--attempt-input", str(attempt_input)))
    if include_attempt_output and attempt_output is None:
        attempt_output = output.with_name(f"{output.name}.attempt.json")
    if attempt_output is not None:
        argv.extend(("--attempt-output", str(attempt_output)))
    return argv


def _invoke_main(
    output: Path,
    failure_output: Path | None = None,
    *,
    attempt_input: Path | None = None,
    attempt_output: Path | None = None,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with (
        patch.object(
            release_probe,
            "_repository_candidate_sha",
            return_value=_RC_CANDIDATE_SHA,
        ),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        returncode = release_probe.main(
            _rc_main_argv(
                output,
                failure_output,
                attempt_input=attempt_input,
                attempt_output=attempt_output,
            )
        )
    return returncode, stdout.getvalue(), stderr.getvalue()


def _identity_profiles(*names: str) -> tuple[object, ...]:
    return tuple(
        profile
        for profile in release_probe.build_release_request_profiles(
            as_of=_RC_AS_OF, replay_date=_RC_REPLAY_DATE
        )
        if profile.name in names
    )


def _synthetic_attempt(
    *,
    candidate_sha: str,
    verified_at: str,
    attempted_producers: set[str],
    capabilities_by_producer: dict[str, tuple[str, ...]] | None = None,
    producers: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Build one bounded attempt fixture from the reviewed literal denominator."""

    producers = _reviewed_qualification_producers() if producers is None else producers
    if capabilities_by_producer is None:
        capabilities_by_producer = {
            producer: ("synthetic_release_contract",) for producer in producers
        }
    if set(capabilities_by_producer) != set(producers) or any(
        not capability_ids for capability_ids in capabilities_by_producer.values()
    ):
        raise ValueError("synthetic release capability authority is invalid")
    obligations = [
        {
            "capability_id": capability_id,
            "operation_id": producer,
            "producer_operation_id": producer,
            "operation_kind": "external_source",
            "source_role": "release_test",
            "experimental": True,
            "exchange": None,
            "evidence_uses": [{"use": "release_test", "role": "primary"}],
        }
        for producer in producers
        for capability_id in capabilities_by_producer[producer]
    ]
    capability_ids = sorted(
        {
            capability_id
            for reviewed in capabilities_by_producer.values()
            for capability_id in reviewed
        }
    )
    coverage = {
        capability_id: {
            "operations": {
                producer: {
                    "capability_id": capability_id,
                    "operation_id": producer,
                    "execution_state": (
                        "failed" if producer in attempted_producers else "not_requested"
                    ),
                    "state": (
                        "failed" if producer in attempted_producers else "not_requested"
                    ),
                    "query_complete": None,
                    "source_error_codes": [],
                    "field_dispositions": [
                        {
                            "field": "payload",
                            "disposition": "normalized",
                            "destination": "observations[].payload",
                        }
                    ],
                }
                for producer in producers
                if capability_id in capabilities_by_producer[producer]
            }
        }
        for capability_id in capability_ids
    }
    core: dict[str, object] = {
        "schema_version": "1.0",
        "artifact_kind": "core_operation_release_probe_attempt",
        "candidate_sha": candidate_sha,
        "verified_at": verified_at,
        "installation_health": _complete_installation_health(),
        "plan_snapshots": [
            {
                "request_profile": "identity-fast",
                "request_as_of": datetime.fromisoformat(verified_at).date().isoformat(),
                "result": {
                    "schema_version": "1.0",
                    "plan": {
                        "id": "synthetic-release-plan",
                        "version": "1.0",
                        "task_type": "synthetic_release",
                        "capabilities": capability_ids,
                        "operation_obligations": obligations,
                        "research_dependencies": [],
                    },
                    "coverage": coverage,
                },
            }
        ],
        "installation_loads": [
            {
                "producer_operation_id": producer,
                "origin": "copied_install_plan",
                "status": "complete",
                "selector": "identity-fast",
            }
            for producer in producers
        ],
        "probe_samples": [
            {
                "producer_operation_id": producer,
                "probe": "copied-installed-public-run",
                "probe_status": "passed",
                "attempted": producer in attempted_producers,
                "source_result": "unavailable",
                "candidate_sha": candidate_sha,
                "verified_at": verified_at,
                "request_profile": "identity-fast",
            }
            for producer in producers
        ],
    }
    digest = sha256(
        json.dumps(
            core,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    core["attempt_digest"] = digest
    for sample in core["probe_samples"]:
        sample["attempt_digest"] = digest
    return core


def _reviewed_application_attempt(
    *,
    candidate_sha: str,
    verified_at: str,
    attempted_producers: set[str],
) -> dict[str, object]:
    """Build a success fixture from checked-in static application authority."""

    payload = json.loads(
        release_probe.DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
    )
    active_producers = set(release_probe._reviewed_producer_operation_ids())
    capabilities_by_producer = {
        item["producer_operation_id"]: tuple(item["capability_ids"])
        for item in payload["operations"]
        if item["producer_operation_id"] in active_producers
    }
    return _synthetic_attempt(
        candidate_sha=candidate_sha,
        verified_at=verified_at,
        attempted_producers=attempted_producers,
        capabilities_by_producer=capabilities_by_producer,
        producers=tuple(sorted(active_producers)),
    )


def _reseal_attempt(value: dict[str, object]) -> dict[str, object]:
    return release_probe.create_release_probe_attempt(
        candidate_sha=value["candidate_sha"],
        verified_at=value["verified_at"],
        installation_health=value["installation_health"],
        plan_snapshots=value["plan_snapshots"],
        installation_loads=value["installation_loads"],
        probe_samples=[
            {
                key: item[key]
                for key in (
                    "producer_operation_id",
                    "probe",
                    "probe_status",
                    "attempted",
                    "source_result",
                    "request_profile",
                )
            }
            for item in value["probe_samples"]
        ],
    )


def _failed_public_source_attempt(*, source_error_code: str) -> dict[str, object]:
    """Seal one failed application from the sanitizer's honest subtype."""

    capability_id = "failure_subtype_contract"
    producer_operation_id = _reviewed_qualification_producers()[0]
    request_profile = "failure-subtype"
    verified_at = "2026-08-12T09:30:00+08:00"
    source_error = _public_source_error(
        capability_id=capability_id,
        operation_id=producer_operation_id,
        code=source_error_code,
        message="Release source failed.",
    )
    snapshot, evidence = release_probe.sanitize_public_result(
        request_profile=request_profile,
        request_as_of="2026-08-12",
        result={
            "schema_version": "1.0",
            "research_anchor": {
                "as_of": "2026-08-12",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "plan-failure-subtype",
                "version": "1.0",
                "task_type": "failure_subtype_test",
                "capabilities": [capability_id],
                "operation_obligations": [
                    {
                        "capability_id": capability_id,
                        "operation_id": producer_operation_id,
                        "producer_operation_id": producer_operation_id,
                        "operation_kind": "external_source",
                        "source_role": "release_test",
                        "experimental": True,
                        "exchange": None,
                        "evidence_uses": [{"use": "release_test", "role": "primary"}],
                    }
                ],
                "research_dependencies": [],
            },
            "coverage": {
                capability_id: {
                    "operations": {
                        producer_operation_id: {
                            "capability_id": capability_id,
                            "operation_id": producer_operation_id,
                            "execution_state": "failed",
                            "state": "failed",
                            "query_complete": None,
                            "source_error_ids": [source_error["id"]],
                            "field_dispositions": [
                                {
                                    "field": "payload",
                                    "disposition": "normalized",
                                    "destination": "observations[].payload",
                                }
                            ],
                        }
                    }
                }
            },
            "source_errors": [source_error],
        },
        verified_at=verified_at,
    )
    return release_probe.create_release_probe_attempt(
        candidate_sha=_RC_CANDIDATE_SHA,
        verified_at=verified_at,
        installation_health=_complete_installation_health(),
        plan_snapshots=[snapshot],
        installation_loads=[
            {
                "producer_operation_id": producer_operation_id,
                "origin": "copied_install_plan",
                "status": "complete",
                "selector": request_profile,
            }
        ],
        probe_samples=[
            {key: value for key, value in evidence[0].items() if key != "verified_at"}
            | {"request_profile": request_profile}
        ],
    )


def _final_report_from_attempt(attempt: dict[str, object]) -> dict[str, object]:
    """Decorate one already sealed attempt as a final public artifact fixture."""

    digest = attempt["attempt_digest"]
    return {
        "schema_version": "1.0",
        "candidate_sha": attempt["candidate_sha"],
        "verified_at": attempt["verified_at"],
        "attempt_digests": [digest],
        "installation_health": deepcopy(attempt["installation_health"]),
        "plan_snapshots": [
            {
                **deepcopy(snapshot),
                "candidate_sha": attempt["candidate_sha"],
                "verified_at": attempt["verified_at"],
                "attempt_digest": digest,
            }
            for snapshot in attempt["plan_snapshots"]
        ],
        "installation_loads": deepcopy(attempt["installation_loads"]),
        "live_probes": deepcopy(attempt["probe_samples"]),
    }


def _offline_identity_result(
    command: list[str], *, attempted: bool, producer_override: str | None = None
) -> dict[str, object]:
    from a_share_research.research_result import _NoIoHttpTransport
    from a_share_research.research_runtime import research

    request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
    request["source_policy"]["allow_experimental"] = False
    result = research(
        request,
        identity_transport=_NoIoHttpTransport(),
        research_now=datetime(
            2026,
            8,
            13,
            9,
            35,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        available_optional_dependencies=(),
    ).to_dict()
    if producer_override is not None:
        for obligation in result["plan"]["operation_obligations"]:
            if obligation["operation_kind"] == "external_source":
                obligation["producer_operation_id"] = producer_override
    if attempted:
        _mark_external_fixture_applications_failed(result)
    return result


def _identity_executor(
    *,
    attempted: bool,
    fail_public_call: int | None = None,
    unsafe_detail: str = "",
    producer_override: str | None = None,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    health = _complete_installation_health()
    public_calls = 0

    def execute(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal public_calls
        if Path(command[-1]).name == "install.py":
            return subprocess.CompletedProcess(command, 0, json.dumps(health), "")
        public_calls += 1
        if public_calls == fail_public_call:
            return subprocess.CompletedProcess(
                command, 17, unsafe_detail, unsafe_detail
            )
        result = _offline_identity_result(
            command,
            attempted=attempted,
            producer_override=producer_override,
        )
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    return execute


def _invoke_offline_identity_main(
    *,
    profiles: tuple[object, ...],
    execute: Callable[..., subprocess.CompletedProcess[str]],
    output: Path,
    failure_output: Path,
) -> tuple[int, str, str]:
    real_run_release_probe = release_probe.run_release_probe_attempt

    def run_with_offline_executor(**kwargs: object) -> dict[str, object]:
        return real_run_release_probe(**kwargs, execute=execute)

    with (
        patch.object(
            release_probe,
            "build_release_request_profiles",
            return_value=profiles,
        ),
        patch.object(
            release_probe,
            "run_release_probe_attempt",
            side_effect=run_with_offline_executor,
        ),
    ):
        return _invoke_main(output, failure_output)


def _source_error_leaf(
    *,
    producer_operation_id: str,
    execution_state: str,
    state: str,
    query_complete: bool | None,
    source_error_ids: list[str],
    task_type: str = "research_content",
    capability_id: str = "shared_source_error_contract",
) -> tuple[dict[str, object], dict[str, object]]:
    plan = {
        "id": f"plan-{execution_state}",
        "version": "1.0",
        "task_type": task_type,
        "capabilities": [capability_id],
        "operation_obligations": [
            {
                "capability_id": capability_id,
                "operation_id": producer_operation_id,
                "producer_operation_id": producer_operation_id,
                "operation_kind": "external_source",
                "source_role": "declared_per_observation",
                "experimental": True,
                "exchange": None,
                "evidence_uses": [
                    {
                        "use": "research_content_material",
                        "role": "supporting",
                    }
                ],
            }
        ],
        "research_dependencies": [],
    }
    coverage = {
        capability_id: {
            "operations": {
                producer_operation_id: {
                    "capability_id": capability_id,
                    "operation_id": producer_operation_id,
                    "execution_state": execution_state,
                    "state": state,
                    "query_complete": query_complete,
                    "observation_ids": [],
                    "source_error_ids": source_error_ids,
                    "field_dispositions": [
                        {
                            "field": "document",
                            "disposition": "normalized",
                            "destination": "observations[].payload.content",
                        }
                    ],
                }
            }
        }
    }
    return plan, coverage


def _public_source_error(
    *,
    capability_id: str,
    operation_id: str,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "capability_id": capability_id,
        "operation_id": operation_id,
        "code": code,
        "message": message,
    }
    if details is not None:
        payload["details"] = dict(details)
    return {"id": stable_result_id("source-error", payload), **payload}


def _built_failed_source_error_result(
    *errors: Mapping[str, object],
    task_type: str = "release_source_error_test",
    capability_id: str = "shared_source_error_contract",
) -> dict[str, object]:
    """Build a real public result whose error-reference order is operation-local."""

    operation_id = _reviewed_qualification_producers()[0]
    outcome = OperationOutcome(
        capability_id=capability_id,
        operation_id=operation_id,
        execution=OperationExecution("failed"),
        source_errors=tuple(
            {
                "source_operation": operation_id,
                "code": error["code"],
                "message": error["message"],
                **({"details": error["details"]} if "details" in error else {}),
            }
            for error in errors
        ),
        field_dispositions=(
            FieldDisposition(
                "payload",
                "normalized",
                "observations[].payload",
            ),
        ),
    )
    result = fold_direct_task_research_result(
        DirectTaskPlan(
            task_type=task_type,
            as_of="2026-08-12",
            obligations=(
                OperationObligation(
                    capability_id=capability_id,
                    operation_id=operation_id,
                    source_role="release_test",
                    experimental=True,
                    exchange=None,
                    evidence_uses=(EvidenceUsePolicy("release_test", "supporting"),),
                ),
            ),
            dependencies=(ResearchDependency("release_test", "release_test", 1, True),),
        ),
        (outcome,),
        summary="Release source-error contract fixture.",
    )
    return result.to_dict()


def _credential_source_error(producer_operation_id: str) -> dict[str, object]:
    return _public_source_error(
        capability_id="shared_source_error_contract",
        operation_id=producer_operation_id,
        code="missing_credential",
        message="Authorization: Bearer workflow-secret",
    )


class CoreOperationReleaseProbeTests(unittest.TestCase):
    def test_etf_option_profile_uses_current_snapshot_boundary_at_public_cli(
        self,
    ) -> None:
        from a_share_research.cli import main as cli_main
        from a_share_research.etf_option_sources import (
            SinaEtfOptionSnapshotOperation,
        )

        from tests.test_etf_option_sources import DiagnosticGate, FixtureTransport

        profile = next(
            item
            for item in release_probe.build_release_request_profiles(
                as_of="2026-07-31", replay_date="2026-07-30"
            )
            if item.name == "etf-options"
        )
        operation = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        stdout = StringIO()
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as root:
            request_path = Path(root) / "request.json"
            request_path.write_text(
                json.dumps(profile.request, ensure_ascii=False), encoding="utf-8"
            )
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = cli_main(
                    ["run", "--request", str(request_path)],
                    etf_option_operations=[operation],
                )

        self.assertEqual(returncode, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {
                operation_id: operation["execution_state"]
                for capability in result["coverage"].values()
                for operation_id, operation in capability["operations"].items()
            },
            {
                "sse_etf_list@1": "succeeded",
                "sse_etf_snapshot@1": "succeeded",
                "sina_etf_option_snapshot@1": "succeeded",
            },
        )
        self.assertEqual(
            profile.request["window"],
            {"observed_from": "2026-07-31", "observed_to": "2026-07-31"},
        )
        self.assertEqual(profile.request["parameters"]["quote_mode"], "latest")

    def test_etf_option_profile_retains_multi_contract_failures_at_public_cli(
        self,
    ) -> None:
        from a_share_research.cli import main as cli_main
        from a_share_research.etf_option_sources import (
            SinaEtfOptionSnapshotOperation,
        )

        from tests.test_etf_option_sources import (
            FIXTURES,
            DiagnosticGate,
            FixtureTransport,
            text_response,
        )

        profile = next(
            item
            for item in release_probe.build_release_request_profiles(
                as_of="2026-07-31", replay_date="2026-07-30"
            )
            if item.name == "etf-options"
        )
        quote_text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        prior_day_quotes = quote_text.replace(
            "2026-07-31 15:00:00", "2026-07-30 15:00:00"
        )
        operation = SinaEtfOptionSnapshotOperation(
            FixtureTransport(overrides={"CON_OP_": text_response(prior_day_quotes)}),
            request_gate=DiagnosticGate(),
        )
        stdout = StringIO()
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as root:
            request_path = Path(root) / "request.json"
            request_path.write_text(
                json.dumps(profile.request, ensure_ascii=False), encoding="utf-8"
            )
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = cli_main(
                    ["run", "--request", str(request_path)],
                    etf_option_operations=[operation],
                )

        self.assertEqual(returncode, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "blocked")
        quote_date_error = next(
            error
            for error in result["source_errors"]
            if error["code"] == "quote_date_mismatch"
        )
        self.assertEqual(
            quote_date_error["details"],
            {"contract_codes": ["10011855", "10011864"]},
        )
        self.assertEqual(
            result["coverage"]["etf_option_contracts"]["operations"][
                "sina_etf_option_snapshot@1"
            ]["execution_state"],
            "failed",
        )

    def test_market_flash_profile_attempts_fallback_after_a_valid_cls_row(self) -> None:
        from a_share_research.identity_sources import HttpResponse, TransportError
        from a_share_research.research_runtime import research

        observed_at = datetime(
            2026,
            8,
            13,
            9,
            35,
            tzinfo=timezone(timedelta(hours=8)),
        )

        class FlashTransport:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                self.urls.append(url)
                if "cls.cn" in url:
                    payload = {
                        "errno": 0,
                        "msg": "",
                        "data": {
                            "roll_data": [
                                {
                                    "id": 2443575,
                                    "ctime": int(
                                        observed_at.replace(
                                            hour=9, minute=34
                                        ).timestamp()
                                    ),
                                    "modified_time": int(observed_at.timestamp()),
                                    "title": "A股市场盘中快讯",
                                    "brief": "公开市场事件摘要",
                                    "content": "公开市场事件正文",
                                    "shareurl": "https://www.cls.cn/detail/2443575",
                                    "stock_list": [],
                                    "subjects": [],
                                }
                            ]
                        },
                    }
                    return HttpResponse(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(payload).encode("utf-8"),
                        retrieved_at=observed_at,
                    )
                if "eastmoney.com" in url:
                    raise TransportError(
                        "upstream_unavailable", "The fallback source is unavailable."
                    )
                raise AssertionError("unexpected content source")

            def post(
                self, url: str, headers: dict[str, str], body: bytes
            ) -> HttpResponse:
                raise AssertionError("unexpected content source")

        profile = next(
            item
            for item in release_probe.build_release_request_profiles(
                as_of="2026-08-13", replay_date="2026-08-12"
            )
            if item.name == "content-market-flash"
        )
        transport = FlashTransport()

        result = research(
            dict(profile.request),
            content_transport=transport,
            research_now=observed_at,
            available_optional_dependencies=(),
        ).to_dict()

        operations = result["coverage"]["research_content_materials"]["operations"]
        self.assertEqual(
            operations["cls_market_flash@1"]["execution_state"], "succeeded"
        )
        self.assertEqual(
            operations["eastmoney_market_flash@1"]["execution_state"], "failed"
        )
        self.assertTrue(any("eastmoney.com" in url for url in transport.urls))

    def test_public_run_timeout_encloses_released_identity_budget(self) -> None:
        from a_share_research.research_result import _NoIoHttpTransport
        from a_share_research.research_runtime import research

        profile = next(
            item
            for item in release_probe.build_release_request_profiles(
                as_of="2026-08-13", replay_date="2026-08-12"
            )
            if item.name == "identity-standard"
        )
        health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [],
            "operations": [],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }
        public_run_timeouts: list[float] = []

        def execute(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if Path(command[-1]).name == "install.py":
                return subprocess.CompletedProcess(command, 0, json.dumps(health), "")
            public_run_timeouts.append(float(kwargs["timeout"]))
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            request["source_policy"]["allow_experimental"] = False
            result = research(
                request,
                identity_transport=_NoIoHttpTransport(),
                research_now=datetime(
                    2026,
                    8,
                    13,
                    9,
                    35,
                    tzinfo=timezone(timedelta(hours=8)),
                ),
                available_optional_dependencies=(),
            ).to_dict()
            self.assertEqual(
                result["plan"]["execution"]["total_budget_seconds"], "120.000"
            )
            _mark_external_fixture_applications_failed(result)
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        report = release_probe.run_release_probe_attempt(
            skill_root=SKILL_ROOT,
            profiles=(profile,),
            verified_at="2026-08-13T09:35:00+08:00",
            candidate_sha=_RC_CANDIDATE_SHA,
            execute=execute,
        )

        self.assertEqual(len(report["probe_samples"]), 3)
        self.assertEqual(len(public_run_timeouts), 1)
        self.assertEqual(public_run_timeouts[0], float(profile.harness_timeout_seconds))
        self.assertGreater(public_run_timeouts[0], 120.0)

    def test_main_writes_only_sanitized_evidence_and_prints_digest_summary(
        self,
    ) -> None:
        attempt = _reviewed_application_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(release_probe._reviewed_producer_operation_ids()),
        )
        report = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at=_RC_VERIFIED_AT,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "successful-attempt.failure.json"
            attempt_output = Path(temporary) / "successful-attempt.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe, "run_release_probe_attempt", return_value=attempt
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        failure_output,
                        attempt_output=attempt_output,
                    )
                )

            summary = json.loads(stdout.getvalue())
            self.assertEqual(returncode, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            self.assertEqual(
                json.loads(attempt_output.read_text(encoding="utf-8")), attempt
            )
            self.assertFalse(failure_output.exists())
            self.assertEqual(
                set(summary),
                {
                    "schema_version",
                    "status",
                    "verified_at",
                    "profile_count",
                    "operation_count",
                    "output",
                    "sha256",
                    "candidate_sha",
                    "attempt_count",
                },
            )
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["candidate_sha"], _RC_CANDIDATE_SHA)
            self.assertEqual(summary["attempt_count"], 1)
            self.assertNotIn("plan_snapshots", stdout.getvalue())

    def test_main_combines_complementary_same_candidate_attempts_before_final(
        self,
    ) -> None:
        producers = release_probe._reviewed_producer_operation_ids()
        prior_attempted = set(producers[:22])
        current_attempted = set(producers[22:])
        prior = _reviewed_application_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=prior_attempted,
        )
        current = _reviewed_application_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=current_attempted,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            failure_output = root / "current.failure.json"
            attempt_input = root / "prior.attempt.json"
            attempt_output = root / "current.attempt.json"
            attempt_input.write_text(
                json.dumps(prior, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                    create=True,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    return_value=current,
                    create=True,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        failure_output,
                        attempt_input=attempt_input,
                        attempt_output=attempt_output,
                    )
                )

            self.assertEqual(returncode, 0, stderr.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            self.assertFalse(failure_output.exists())
            self.assertEqual(
                json.loads(attempt_output.read_text(encoding="utf-8")), current
            )
            final = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(final["candidate_sha"], _RC_CANDIDATE_SHA)
            self.assertEqual(
                final["attempt_digests"],
                sorted((prior["attempt_digest"], current["attempt_digest"])),
            )
            self.assertEqual(len(final["plan_snapshots"]), 2)
            self.assertEqual(len(final["live_probes"]), 52)
            live_by_owner = {
                item["producer_operation_id"]: item for item in final["live_probes"]
            }
            self.assertEqual(set(live_by_owner), set(producers))
            self.assertTrue(all(item["attempted"] for item in live_by_owner.values()))
            for producer in prior_attempted:
                self.assertEqual(
                    live_by_owner[producer]["attempt_digest"],
                    prior["attempt_digest"],
                )
                self.assertEqual(
                    live_by_owner[producer]["verified_at"], prior["verified_at"]
                )
            for producer in current_attempted:
                self.assertEqual(
                    live_by_owner[producer]["attempt_digest"],
                    current["attempt_digest"],
                )
                self.assertEqual(
                    live_by_owner[producer]["verified_at"], current["verified_at"]
                )
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["candidate_sha"], _RC_CANDIDATE_SHA)
            self.assertEqual(summary["attempt_count"], 2)

    def test_attempt_union_accepts_owner_valid_dynamic_identity_applications(
        self,
    ) -> None:
        from dataclasses import replace
        from urllib.parse import urlparse

        from a_share_research.identity_sources import HttpResponse
        from a_share_research.market_signal_contract import (
            SignalCoverage,
            SignalSourceBatch,
        )
        from a_share_research.research_runtime import ResearchRuntime

        from tests.test_market_signals import (
            CHINA_STANDARD_TIME,
            FixedOperation,
            observation,
            signal_request,
        )

        fixtures = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"

        class IdentityTransport:
            def __init__(self, code: str) -> None:
                self.code = code

            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                host = urlparse(url).netloc
                if host == "query.sse.com.cn":
                    fixture = "sse_empty.json"
                elif host == "www.szse.cn":
                    fixture = f"szse_{self.code}.json"
                elif host == "www.cninfo.com.cn":
                    fixture = (
                        "cninfo_current_orgs.json"
                        if self.code == "300058"
                        else "cninfo_stocks.json"
                    )
                else:
                    raise AssertionError(f"unexpected identity URL: {url}")
                return HttpResponse(
                    status=200,
                    content_type="application/json",
                    body=(fixtures / fixture).read_bytes(),
                    retrieved_at=datetime(
                        2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME
                    ),
                )

        class MonitoringOperation(FixedOperation):
            supported_signal_types = frozenset(
                {"focus_monitoring", "severe_abnormal_movement"}
            )

        def dynamic_attempt(
            *, code: str, name: str, verified_at: str
        ) -> dict[str, object]:
            dimensions = {
                "market_scope": "mainland_a_share",
                "provider_security_code": code,
                "provider_security_name": name,
            }
            focus = replace(
                observation(
                    signal_type="focus_monitoring", provider_security_code=code
                ),
                period={
                    "start": "2026-07-01",
                    "end": "2026-08-31",
                    "frequency": "monitoring_window",
                },
                dimensions=dimensions,
            )
            severe = replace(
                observation(
                    signal_type="severe_abnormal_movement",
                    provider_security_code=code,
                ),
                rule={"rule_code": "SZSE-SEVERE-01"},
                dimensions=dimensions,
            )
            operation = MonitoringOperation(
                SignalSourceBatch(
                    operation_id="fixed_signal@1",
                    observations=(focus, severe),
                    coverage={
                        "focus_monitoring": SignalCoverage(
                            state="observed_nonempty", provider_total=1
                        ),
                        "severe_abnormal_movement": SignalCoverage(
                            state="observed_nonempty", provider_total=1
                        ),
                    },
                )
            )
            result = (
                ResearchRuntime(
                    identity_transport=IdentityTransport(code),
                    market_signal_operations=(operation,),
                )
                .research(signal_request(["monitoring_intersection"]))
                .to_dict()
            )
            snapshot, _ = release_probe.sanitize_public_result(
                request_profile="signals-market",
                request_as_of="2026-08-02",
                result=result,
                verified_at=verified_at,
            )
            dynamic_capabilities = [
                capability_id
                for capability_id in snapshot["result"]["plan"]["capabilities"]
                if capability_id.startswith("security_identity_resolution.monitoring.")
            ]
            self.assertEqual(
                len(dynamic_capabilities),
                1,
                {"plan": snapshot["result"]["plan"], "result": result},
            )
            attempt = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at=verified_at,
                attempted_producers=set(_reviewed_qualification_producers()),
            )
            attempt["plan_snapshots"] = [snapshot]
            return _reseal_attempt(attempt)

        first = dynamic_attempt(
            code="300058",
            name="蓝色光标",
            verified_at="2026-08-12T09:35:00+08:00",
        )
        second = dynamic_attempt(
            code="000001",
            name="平安银行",
            verified_at="2026-08-12T10:00:00+08:00",
        )
        self.assertNotEqual(
            first["plan_snapshots"][0]["result"]["plan"],
            second["plan_snapshots"][0]["result"]["plan"],
        )

        checked = release_probe.validate_release_probe_attempt_inputs(
            (first, second),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at=_RC_VERIFIED_AT,
        )

        self.assertEqual(len(checked), 2)

        from a_share_research.research_result import stable_result_id

        def reseal_plan(attempt: dict[str, object]) -> dict[str, object]:
            snapshot_result = attempt["plan_snapshots"][0]["result"]
            plan = snapshot_result["plan"]
            coverage = snapshot_result["coverage"]
            plan["id"] = stable_result_id(
                "plan",
                {
                    "version": plan["version"],
                    "task_type": plan["task_type"],
                    "operation_obligations": plan["operation_obligations"],
                    "research_dependencies": plan["research_dependencies"],
                    "field_dispositions": [
                        {
                            "capability_id": obligation["capability_id"],
                            "operation_id": obligation["operation_id"],
                            "fields": coverage[obligation["capability_id"]][
                                "operations"
                            ][obligation["operation_id"]]["field_dispositions"],
                        }
                        for obligation in plan["operation_obligations"]
                    ],
                },
            )
            return _reseal_attempt(attempt)

        disposition_forge = deepcopy(second)
        forged_result = disposition_forge["plan_snapshots"][0]["result"]
        dynamic_capability = next(
            item
            for item in forged_result["plan"]["capabilities"]
            if item.startswith("security_identity_resolution.monitoring.")
        )
        forged_result["coverage"][dynamic_capability]["operations"]["sse_stock_list@1"][
            "field_dispositions"
        ][0]["field"] = "forged_reviewed_field"

        dependency_forge = deepcopy(second)
        dependency = next(
            item
            for item in dependency_forge["plan_snapshots"][0]["result"]["plan"][
                "research_dependencies"
            ]
            if item["consumer_id"].startswith("monitoring_intersection_identity:")
        )
        dependency["consumer_id"] = "monitoring_intersection_identity:999999"

        application_forge = deepcopy(second)
        application_result = application_forge["plan_snapshots"][0]["result"]
        application_plan = application_result["plan"]
        application_coverage = application_result["coverage"]
        old_capability = next(
            item
            for item in application_plan["capabilities"]
            if item.startswith("security_identity_resolution.monitoring.")
        )
        forged_provider = "not-a-security"
        forged_namespace = sha256(forged_provider.encode()).hexdigest()[:12]
        new_capability = f"security_identity_resolution.monitoring.{forged_namespace}"
        new_consumer = f"monitoring_intersection_identity:{forged_provider}"
        application_plan["capabilities"] = [
            new_capability if item == old_capability else item
            for item in application_plan["capabilities"]
        ]
        for obligation in application_plan["operation_obligations"]:
            if obligation["capability_id"] == old_capability:
                obligation["capability_id"] = new_capability
        for application_dependency in application_plan["research_dependencies"]:
            if application_dependency["consumer_id"].startswith(
                "monitoring_intersection_identity:"
            ):
                application_dependency["consumer_id"] = new_consumer
        application_coverage[new_capability] = application_coverage.pop(old_capability)
        for operation in application_coverage[new_capability]["operations"].values():
            operation["capability_id"] = new_capability

        coverage_owner_forge = deepcopy(second)
        coverage_result = coverage_owner_forge["plan_snapshots"][0]["result"]
        coverage_capability = next(
            item
            for item in coverage_result["plan"]["capabilities"]
            if item.startswith("security_identity_resolution.monitoring.")
        )
        coverage_result["coverage"][coverage_capability]["operations"][
            "sse_stock_list@1"
        ]["capability_id"] = "forged_identity_owner"

        for name, forged in (
            ("owner-disposition", reseal_plan(disposition_forge)),
            ("provider-dependency", reseal_plan(dependency_forge)),
            ("non-security-application", reseal_plan(application_forge)),
            ("coverage-operation-owner", reseal_plan(coverage_owner_forge)),
        ):
            with (
                self.subTest(forge=name),
                self.assertRaisesRegex(ValueError, "monitoring identity application"),
            ):
                release_probe.validate_release_probe_attempt_inputs(
                    (first, forged),
                    candidate_sha=_RC_CANDIDATE_SHA,
                    assembled_at=_RC_VERIFIED_AT,
                )

    def test_attempt_union_validates_request_scoped_workflow_plan_ids(self) -> None:
        from tests.test_direct_market_valuation_runtime import invoke_copied_installed

        def workflow_attempt(*, as_of: str, verified_at: str) -> dict[str, object]:
            profile = next(
                item
                for item in release_probe.build_release_request_profiles(
                    as_of=as_of, replay_date=as_of
                )
                if item.name == "workflow-valuation-comparison"
            )
            result = invoke_copied_installed(
                dict(profile.request),
                valuation_fixtures=True,
                fixture_research_now=f"{as_of}T18:30:00+08:00",
            )
            snapshot, _ = release_probe.sanitize_public_result(
                request_profile=profile.name,
                request_as_of=as_of,
                result=result,
                verified_at=verified_at,
            )
            attempt = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at=verified_at,
                attempted_producers=set(_reviewed_qualification_producers()),
            )
            attempt["plan_snapshots"] = [snapshot]
            return _reseal_attempt(attempt)

        first = workflow_attempt(
            as_of="2026-08-01",
            verified_at="2026-08-02T09:35:00+08:00",
        )
        second = workflow_attempt(
            as_of="2026-08-02",
            verified_at="2026-08-02T10:00:00+08:00",
        )
        self.assertNotEqual(
            first["plan_snapshots"][0]["result"]["plan"]["id"],
            second["plan_snapshots"][0]["result"]["plan"]["id"],
        )

        checked = release_probe.validate_release_probe_attempt_inputs(
            (first, second),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at="2026-08-02T10:30:00+08:00",
        )
        self.assertEqual(len(checked), 2)

        forged = deepcopy(second)
        forged["plan_snapshots"][0]["result"]["plan"]["id"] = (
            "plan-000000000000000000000000"
        )
        forged = _reseal_attempt(forged)
        with self.assertRaisesRegex(ValueError, "workflow plan ID contradicts"):
            release_probe.validate_release_probe_attempt_inputs(
                (first, forged),
                candidate_sha=_RC_CANDIDATE_SHA,
                assembled_at="2026-08-02T10:30:00+08:00",
            )

        forged_scope = deepcopy(second)
        forged_scope["plan_snapshots"][0]["request_as_of"] = "2026-08-01"
        forged_scope = _reseal_attempt(forged_scope)
        with self.assertRaisesRegex(ValueError, "workflow plan ID contradicts"):
            release_probe.validate_release_probe_attempt_inputs(
                (first, forged_scope),
                candidate_sha=_RC_CANDIDATE_SHA,
                assembled_at="2026-08-02T10:30:00+08:00",
            )

    def test_main_requires_a_fresh_attempt_artifact_before_any_public_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe.tempfile,
                    "TemporaryDirectory",
                    side_effect=AssertionError("copied install must not start"),
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(output, include_attempt_output=False)
                )

        self.assertEqual(returncode, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(), "error: core operation release probe failed\n"
        )
        self.assertFalse(output.exists())

    def test_run_attempt_persists_unattempted_public_applications_without_finalizing(
        self,
    ) -> None:
        attempt = release_probe.run_release_probe_attempt(
            skill_root=SKILL_ROOT,
            profiles=_identity_profiles("identity-fast"),
            verified_at=_RC_VERIFIED_AT,
            candidate_sha=_RC_CANDIDATE_SHA,
            execute=_identity_executor(attempted=False),
        )

        self.assertEqual(
            set(attempt),
            {
                "schema_version",
                "artifact_kind",
                "candidate_sha",
                "verified_at",
                "installation_health",
                "plan_snapshots",
                "installation_loads",
                "probe_samples",
                "attempt_digest",
            },
        )
        self.assertEqual(attempt["candidate_sha"], _RC_CANDIDATE_SHA)
        self.assertEqual(attempt["verified_at"], _RC_VERIFIED_AT)
        self.assertEqual(
            {item["producer_operation_id"] for item in attempt["probe_samples"]},
            set(_IDENTITY_PRODUCERS),
        )
        self.assertTrue(
            all(item["attempted"] is False for item in attempt["probe_samples"])
        )
        self.assertEqual(json.loads(release_probe.attempt_json_bytes(attempt)), attempt)
        with self.assertRaisesRegex(ValueError, "never attempted"):
            release_probe.assemble_release_probe_evidence(
                (attempt,),
                candidate_sha=_RC_CANDIDATE_SHA,
                assembled_at=_RC_VERIFIED_AT,
            )

    def test_main_rejects_another_candidate_attempt_before_any_public_process(
        self,
    ) -> None:
        stale = _synthetic_attempt(
            candidate_sha="b" * 40,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "other-candidate.attempt.json"
            attempt_input.write_text(
                json.dumps(stale, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        attempt_input=attempt_input,
                        attempt_output=attempt_output,
                    )
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_stale_attempt_before_any_public_process(self) -> None:
        stale = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-01T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "stale.attempt.json"
            attempt_input.write_text(
                json.dumps(stale, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        attempt_input=attempt_input,
                        attempt_output=attempt_output,
                    )
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_duplicate_prior_digest_before_any_public_process(
        self,
    ) -> None:
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            first = root / "first.attempt.json"
            second = root / "second.attempt.json"
            serialized = (
                json.dumps(prior, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            first.write_text(serialized, encoding="utf-8")
            second.write_text(serialized, encoding="utf-8")
            attempt_output = root / "current.attempt.json"
            argv = _rc_main_argv(
                output,
                attempt_input=first,
                attempt_output=attempt_output,
            )
            argv.extend(("--attempt-input", str(second)))
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(argv)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_prior_union_drift_before_any_public_process(self) -> None:
        producers = set(_reviewed_qualification_producers())

        def load_drift() -> tuple[dict[str, object], dict[str, object]]:
            first = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at="2026-08-12T09:35:00+08:00",
                attempted_producers=producers,
            )
            second = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at="2026-08-12T10:00:00+08:00",
                attempted_producers=producers,
            )
            second["installation_loads"][0]["selector"] = "drifted-selector"
            return first, _reseal_attempt(second)

        def same_instant_conflict() -> tuple[dict[str, object], dict[str, object]]:
            first = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at="2026-08-12T09:35:00+08:00",
                attempted_producers=producers,
            )
            second = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at="2026-08-12T01:35:00+00:00",
                attempted_producers=producers,
            )
            second["probe_samples"][0]["source_result"] = "credential_blocked"
            return first, _reseal_attempt(second)

        def plan_contract_drift() -> tuple[dict[str, object], dict[str, object]]:
            first = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at="2026-08-12T09:35:00+08:00",
                attempted_producers=producers,
            )
            second = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at="2026-08-12T10:00:00+08:00",
                attempted_producers=producers,
            )
            operation = second["plan_snapshots"][0]["result"]["coverage"][
                "synthetic_release_contract"
            ]["operations"]["cninfo_announcement@1"]
            operation["field_dispositions"][0]["destination"] = (
                "observations[].payload.value"
            )
            return first, _reseal_attempt(second)

        for name, attempts in (
            ("load-drift", load_drift()),
            ("same-instant-conflict", same_instant_conflict()),
            ("plan-contract-drift", plan_contract_drift()),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "release-evidence.json"
                output.write_bytes(_ORIGINAL_FINAL)
                attempt_paths: list[Path] = []
                for index, attempt in enumerate(attempts):
                    path = root / f"{name}-{index}.attempt.json"
                    path.write_text(
                        json.dumps(
                            attempt, ensure_ascii=False, indent=2, sort_keys=True
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    attempt_paths.append(path)
                attempt_output = root / "current.attempt.json"
                argv = _rc_main_argv(
                    output,
                    attempt_input=attempt_paths[0],
                    attempt_output=attempt_output,
                )
                argv.extend(("--attempt-input", str(attempt_paths[1])))
                stdout = StringIO()
                stderr = StringIO()
                with (
                    patch.object(
                        release_probe,
                        "_repository_candidate_sha",
                        return_value=_RC_CANDIDATE_SHA,
                    ),
                    patch.object(
                        release_probe,
                        "run_release_probe_attempt",
                        side_effect=AssertionError("public process must not run"),
                    ) as public_process,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    returncode = release_probe.main(argv)

                self.assertEqual(returncode, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(), "error: core operation release probe failed\n"
                )
                public_process.assert_not_called()
                self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
                self.assertFalse(attempt_output.exists())

    def test_main_never_overwrites_a_preexisting_attempt_output(self) -> None:
        original_attempt = b"existing partial attempt bytes must survive\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_output = root / "current.attempt.json"
            attempt_output.write_bytes(original_attempt)
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(output, attempt_output=attempt_output)
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertEqual(attempt_output.read_bytes(), original_attempt)

    def test_attempt_contract_rejects_non_v040_source_result_states(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )

        for source_result in ("failed", "rate_limited"):
            forged = deepcopy(attempt)
            forged["probe_samples"][0]["source_result"] = source_result
            with (
                self.subTest(source_result=source_result),
                self.assertRaisesRegex(ValueError, "sample is invalid"),
            ):
                _reseal_attempt(forged)

    def test_attempt_contract_rejects_empty_or_duplicate_owners(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        first_owner = attempt["probe_samples"][0]["producer_operation_id"]
        cases = (
            ("empty", 0, ""),
            ("duplicate", 1, first_owner),
        )

        for name, sample_index, owner in cases:
            forged = deepcopy(attempt)
            forged["probe_samples"][sample_index]["producer_operation_id"] = owner
            with self.subTest(name=name), self.assertRaises(ValueError):
                _reseal_attempt(forged)

    def test_attempt_digest_covers_samples_and_sanitized_plan_contracts(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        mutations = (
            lambda value: value["probe_samples"][0].__setitem__(
                "source_result", "partial"
            ),
            lambda value: value["plan_snapshots"][0]["result"]["plan"].__setitem__(
                "id", "tampered-plan"
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, mutate in enumerate(mutations):
                with self.subTest(index=index):
                    forged = deepcopy(attempt)
                    mutate(forged)
                    path = Path(temporary) / f"forged-{index}.attempt.json"
                    path.write_text(
                        json.dumps(forged, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "digest"):
                        release_probe.load_release_probe_attempt(
                            path,
                            expected_candidate_sha=_RC_CANDIDATE_SHA,
                            assembled_at=_RC_VERIFIED_AT,
                        )

    def test_main_persists_partial_attempt_and_bounded_union_failure(self) -> None:
        profiles = _identity_profiles("identity-fast")
        real_run_attempt = release_probe.run_release_probe_attempt

        def run_with_offline_executor(**kwargs: object) -> dict[str, object]:
            return real_run_attempt(
                **kwargs, execute=_identity_executor(attempted=False)
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            failure_output = root / "union.failure.json"
            attempt_output = root / "current.attempt.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "build_release_request_profiles",
                    return_value=profiles,
                ),
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=run_with_offline_executor,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        failure_output,
                        attempt_output=attempt_output,
                    )
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            attempt = json.loads(attempt_output.read_text(encoding="utf-8"))
            self.assertEqual(attempt["candidate_sha"], _RC_CANDIDATE_SHA)
            self.assertTrue(
                all(item["attempted"] is False for item in attempt["probe_samples"])
            )
            failure = json.loads(failure_output.read_text(encoding="utf-8"))
            self.assertEqual(failure["stage"], "truth_gate")
            self.assertEqual(
                failure["observed_external_producer_operation_ids"],
                list(_IDENTITY_PRODUCERS),
            )
            self.assertEqual(failure["attempted_producer_operation_ids"], [])
            self.assertEqual(
                failure["unattempted_producer_operation_ids"],
                failure["external_producer_operation_ids"],
            )

    def test_main_rejects_rehashed_unknown_prior_owner_before_public_process(
        self,
    ) -> None:
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        prior["probe_samples"][0]["producer_operation_id"] = "unknown_source@1"
        forged = _reseal_attempt(prior)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "unknown.attempt.json"
            attempt_input.write_text(
                json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        attempt_input=attempt_input,
                        attempt_output=attempt_output,
                    )
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_resealed_prior_direct_producer_swap_before_process(
        self,
    ) -> None:
        producers = {"cls_market_flash@1", "cninfo_announcement@1"}
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        obligations = prior["plan_snapshots"][0]["result"]["plan"][
            "operation_obligations"
        ]
        obligations_by_application = {
            item["operation_id"]: item for item in obligations
        }
        obligations_by_application["cls_market_flash@1"]["producer_operation_id"] = (
            "cninfo_announcement@1"
        )
        obligations_by_application["cninfo_announcement@1"]["producer_operation_id"] = (
            "cls_market_flash@1"
        )
        for collection_name in ("installation_loads", "probe_samples"):
            for item in prior[collection_name]:
                if item["producer_operation_id"] in producers:
                    item["producer_operation_id"] = next(
                        producer
                        for producer in producers
                        if producer != item["producer_operation_id"]
                    )
        forged = _reseal_attempt(prior)
        current = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "producer-swap.attempt.json"
            attempt_input.write_text(
                json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                return_value=current,
            ) as public_process:
                returncode, stdout, stderr = _invoke_main(
                    output,
                    attempt_input=attempt_input,
                    attempt_output=attempt_output,
                )

            public_process.assert_not_called()
            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_resealed_prior_unsafe_plan_id_before_process(self) -> None:
        unsafe_plan_id = "https://secret-user:secret-pass@10.0.0.1/private"
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        prior["plan_snapshots"][0]["result"]["plan"]["id"] = unsafe_plan_id
        forged = _reseal_attempt(prior)
        current = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        current["plan_snapshots"][0]["result"]["plan"]["id"] = unsafe_plan_id
        current = _reseal_attempt(current)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "unsafe-plan-id.attempt.json"
            attempt_input.write_text(
                json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                return_value=current,
            ) as public_process:
                returncode, stdout, stderr = _invoke_main(
                    output,
                    attempt_input=attempt_input,
                    attempt_output=attempt_output,
                )

            public_process.assert_not_called()
            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_resealed_prior_market_signal_kind_forgery_before_process(
        self,
    ) -> None:
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        plan = prior["plan_snapshots"][0]["result"]["plan"]
        plan["task_type"] = "market_signals"
        obligation = next(
            item
            for item in plan["operation_obligations"]
            if item["operation_id"] == "cls_market_flash@1"
        )
        obligation["operation_kind"] = "deterministic_coordinator"
        for collection_name in ("installation_loads", "probe_samples"):
            prior[collection_name] = [
                item
                for item in prior[collection_name]
                if item["producer_operation_id"] != "cls_market_flash@1"
            ]
        forged = _reseal_attempt(prior)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "market-signal-kind.attempt.json"
            attempt_input.write_text(
                json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                side_effect=AssertionError("public process must not run"),
            ) as public_process:
                returncode, stdout, stderr = _invoke_main(
                    output, attempt_input=attempt_input
                )

            public_process.assert_not_called()
            self.assertEqual(
                (returncode, stdout, stderr),
                (2, "", "error: core operation release probe failed\n"),
            )
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)

    def test_main_rejects_resealed_prior_unsafe_disposition_text_before_process(
        self,
    ) -> None:
        unsafe_values = (
            {
                "field": "payload",
                "disposition": "excluded",
                "destination": None,
                "reason": ("Authorization Bearer SECRET via https://10.0.0.1/private"),
            },
            {
                "field": "https://user:pass@10.0.0.1/raw",
                "disposition": "excluded",
                "destination": None,
                "reason": "source_policy_not_satisfied",
            },
        )
        for index, disposition in enumerate(unsafe_values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                prior = _synthetic_attempt(
                    candidate_sha=_RC_CANDIDATE_SHA,
                    verified_at="2026-08-12T09:35:00+08:00",
                    attempted_producers=set(_reviewed_qualification_producers()),
                )
                operations = prior["plan_snapshots"][0]["result"]["coverage"][
                    "synthetic_release_contract"
                ]["operations"]
                operations["cls_market_flash@1"]["field_dispositions"][0] = disposition
                forged = _reseal_attempt(prior)
                root = Path(temporary)
                output = root / "release-evidence.json"
                output.write_bytes(_ORIGINAL_FINAL)
                attempt_input = root / f"unsafe-disposition-{index}.attempt.json"
                attempt_input.write_text(
                    json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                with patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process:
                    returncode, stdout, stderr = _invoke_main(
                        output, attempt_input=attempt_input
                    )

                public_process.assert_not_called()
                self.assertEqual(
                    (returncode, stdout, stderr),
                    (2, "", "error: core operation release probe failed\n"),
                )
                self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)

    def test_main_rejects_resealed_prior_unowned_profile_before_process(self) -> None:
        for forge_load_only in (False, True):
            with self.subTest(forge_load_only=forge_load_only):
                forged_profile = "forged-self-consistent-profile"
                prior = _synthetic_attempt(
                    candidate_sha=_RC_CANDIDATE_SHA,
                    verified_at="2026-08-12T09:35:00+08:00",
                    attempted_producers=set(_reviewed_qualification_producers()),
                )
                if forge_load_only:
                    prior["installation_loads"][0]["selector"] = forged_profile
                else:
                    prior["plan_snapshots"][0]["request_profile"] = forged_profile
                    for load in prior["installation_loads"]:
                        if load["origin"] == "copied_install_plan":
                            load["selector"] = forged_profile
                    for sample in prior["probe_samples"]:
                        sample["request_profile"] = forged_profile
                forged = _reseal_attempt(prior)
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    output = root / "release-evidence.json"
                    output.write_bytes(_ORIGINAL_FINAL)
                    attempt_input = root / "unowned-profile.attempt.json"
                    attempt_input.write_text(
                        json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True)
                        + "\n",
                        encoding="utf-8",
                    )
                    with patch.object(
                        release_probe,
                        "run_release_probe_attempt",
                        side_effect=AssertionError("public process must not run"),
                    ) as public_process:
                        returncode, stdout, stderr = _invoke_main(
                            output, attempt_input=attempt_input
                        )

                    public_process.assert_not_called()
                    self.assertEqual(
                        (returncode, stdout, stderr),
                        (2, "", "error: core operation release probe failed\n"),
                    )
                    self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)

    def test_main_rejects_rehashed_sample_owner_absent_from_its_attempt_plan(
        self,
    ) -> None:
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        absent_owner = prior["probe_samples"][-1]["producer_operation_id"]
        capability = prior["plan_snapshots"][0]["result"]["plan"]
        capability["operation_obligations"] = [
            item
            for item in capability["operation_obligations"]
            if item["producer_operation_id"] != absent_owner
        ]
        coverage = prior["plan_snapshots"][0]["result"]["coverage"][
            "synthetic_release_contract"
        ]["operations"]
        del coverage[absent_owner]
        forged = _reseal_attempt(prior)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "orphan.attempt.json"
            attempt_input.write_text(
                json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe,
                    "run_release_probe_attempt",
                    side_effect=AssertionError("public process must not run"),
                ) as public_process,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(
                    _rc_main_argv(
                        output,
                        attempt_input=attempt_input,
                        attempt_output=attempt_output,
                    )
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_main_rejects_resealed_source_result_that_contradicts_its_plan(
        self,
    ) -> None:
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        prior["probe_samples"][0]["source_result"] = "complete"
        forged = _reseal_attempt(prior)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release-evidence.json"
            output.write_bytes(_ORIGINAL_FINAL)
            attempt_input = root / "forged-source-result.attempt.json"
            attempt_input.write_text(
                json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            attempt_output = root / "current.attempt.json"
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                side_effect=AssertionError("public process must not run"),
            ) as public_process:
                returncode, stdout, stderr = _invoke_main(
                    output,
                    attempt_input=attempt_input,
                    attempt_output=attempt_output,
                )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            public_process.assert_not_called()
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertFalse(attempt_output.exists())

    def test_failed_source_subtype_cannot_be_relabelled_after_resealing(
        self,
    ) -> None:
        from a_share_research.operation_release_matrix import (
            OperationQualificationEvidence,
        )

        qualification = (
            OperationQualificationEvidence(
                producer_operation_id=_reviewed_qualification_producers()[0],
                disposition="experimental",
                temporal_semantics={"basis": "observed_at"},
                unit_semantics={"payload": "source_declared"},
                failure_semantics="failed_with_source_error",
                empty_result_semantics="complete_query_zero_records",
                acquisition_role="primary",
                credential_policy="optional",
                rate_limit_policy={"scope": "provider", "max_requests": 1},
                offline_contract_selector="tests.failure_subtype",
                offline_contract_result="passed",
                evidence_references=("tests.failure_subtype",),
                capability_ids=("failure_subtype_contract",),
            ),
        )

        for source_error_code, honest_result, forged_result in (
            ("missing_credential", "credential_blocked", "unavailable"),
            ("rate_limited", "unavailable", "credential_blocked"),
        ):
            with self.subTest(
                direction=f"{honest_result}->{forged_result}", stage="honest"
            ):
                honest_attempt = _failed_public_source_attempt(
                    source_error_code=source_error_code
                )
                self.assertEqual(
                    honest_attempt["probe_samples"][0]["source_result"],
                    honest_result,
                )
                release_probe._validate_attempt_contract(honest_attempt)
                honest_final = release_probe.assemble_release_probe_evidence(
                    (honest_attempt,),
                    candidate_sha=_RC_CANDIDATE_SHA,
                    assembled_at=honest_attempt["verified_at"],
                )
                snapshots, installation_loads, installation_health, live_probes = (
                    release_probe.load_release_probe_evidence(honest_final)
                )
                release_probe.build_operation_release_matrix(
                    plan_snapshots=snapshots,
                    qualification_evidence=qualification,
                    installation_loads=installation_loads,
                    installation_health=installation_health,
                    live_probes=live_probes,
                )

            forged_attempt = deepcopy(honest_attempt)
            forged_attempt["probe_samples"][0]["source_result"] = forged_result
            forged_attempt = _reseal_attempt(forged_attempt)
            forged_digest = forged_attempt["attempt_digest"]
            self.assertNotEqual(
                forged_digest,
                honest_attempt["attempt_digest"],
            )
            forged_final = _final_report_from_attempt(forged_attempt)
            forged_snapshots = tuple(
                replace(snapshot, attempt_digest=forged_digest)
                for snapshot in snapshots
            )
            forged_live_probes = tuple(
                replace(
                    live_probe,
                    source_result=forged_result,
                    attempt_digest=forged_digest,
                )
                for live_probe in live_probes
            )
            for gate in (
                "direct-attempt-contract",
                "attempt-union",
                "final-loader-truth",
                "matrix",
            ):
                with (
                    self.subTest(
                        direction=f"{honest_result}->{forged_result}", gate=gate
                    ),
                    self.assertRaises(ValueError),
                ):
                    if gate == "direct-attempt-contract":
                        release_probe._validate_attempt_contract(forged_attempt)
                    elif gate == "attempt-union":
                        release_probe.assemble_release_probe_evidence(
                            (forged_attempt,),
                            candidate_sha=_RC_CANDIDATE_SHA,
                            assembled_at=forged_attempt["verified_at"],
                        )
                    elif gate == "final-loader-truth":
                        release_probe.load_release_probe_evidence(forged_final)
                    else:
                        release_probe.build_operation_release_matrix(
                            plan_snapshots=forged_snapshots,
                            qualification_evidence=qualification,
                            installation_loads=installation_loads,
                            installation_health=installation_health,
                            live_probes=forged_live_probes,
                        )

    def test_attempt_contract_rejects_noncanonical_source_error_codes(
        self,
    ) -> None:
        for name, source_error_codes in (
            ("missing", None),
            ("unknown", ["rate_limited"]),
            ("duplicate", ["missing_credential", "missing_credential"]),
            ("unsorted", ["permission_denied", "missing_credential"]),
        ):
            with self.subTest(name=name):
                attempt = _failed_public_source_attempt(
                    source_error_code="missing_credential"
                )
                operation = attempt["plan_snapshots"][0]["result"]["coverage"][
                    "failure_subtype_contract"
                ]["operations"][_reviewed_qualification_producers()[0]]
                if source_error_codes is None:
                    operation.pop("source_error_codes")
                else:
                    operation["source_error_codes"] = source_error_codes
                forged = _reseal_attempt(attempt)

                with self.assertRaises(ValueError):
                    release_probe._validate_attempt_contract(forged)

    def test_public_sanitizer_rejects_credential_codes_outside_failed_execution(
        self,
    ) -> None:
        producer = _reviewed_qualification_producers()[0]
        for (
            name,
            execution_state,
            coverage_state,
            query_complete,
            _,
            _,
        ) in _NONFAILED_CREDENTIAL_CONTRADICTIONS:
            with self.subTest(name=name):
                attempt = _synthetic_attempt(
                    candidate_sha=_RC_CANDIDATE_SHA,
                    verified_at=_RC_VERIFIED_AT,
                    attempted_producers=set(),
                )
                result = deepcopy(attempt["plan_snapshots"][0]["result"])
                result["research_anchor"] = {
                    "as_of": _RC_AS_OF,
                    "timezone": "Asia/Shanghai",
                }
                operation = result["coverage"]["synthetic_release_contract"][
                    "operations"
                ][producer]
                operation["execution_state"] = execution_state
                operation["state"] = coverage_state
                operation["query_complete"] = query_complete
                source_error = _public_source_error(
                    capability_id="synthetic_release_contract",
                    operation_id=producer,
                    code="missing_credential",
                    message="Missing release credential.",
                )
                operation["source_error_ids"] = [source_error["id"]]
                result["source_errors"] = [source_error]

                with self.assertRaisesRegex(
                    ValueError,
                    "release source error codes require failed execution",
                ):
                    release_probe.sanitize_public_result(
                        request_profile=f"credential-contradiction-{name}",
                        request_as_of=_RC_AS_OF,
                        result=result,
                        verified_at=_RC_VERIFIED_AT,
                    )

    def test_sealed_attempt_and_final_loader_reject_credential_codes_outside_failed(
        self,
    ) -> None:
        producers = _reviewed_qualification_producers()
        producer = producers[0]
        for (
            name,
            execution_state,
            coverage_state,
            query_complete,
            attempted,
            source_result,
        ) in _NONFAILED_CREDENTIAL_CONTRADICTIONS:
            attempt = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at=_RC_VERIFIED_AT,
                attempted_producers=set(producers),
            )
            operation = attempt["plan_snapshots"][0]["result"]["coverage"][
                "synthetic_release_contract"
            ]["operations"][producer]
            operation["execution_state"] = execution_state
            operation["state"] = coverage_state
            operation["query_complete"] = query_complete
            operation["source_error_codes"] = ["missing_credential"]
            sample = next(
                item
                for item in attempt["probe_samples"]
                if item["producer_operation_id"] == producer
            )
            sample["attempted"] = attempted
            sample["source_result"] = source_result
            sealed_attempt = _reseal_attempt(attempt)
            final_report = _final_report_from_attempt(sealed_attempt)

            for gate in ("sealed-attempt", "final-loader"):
                with self.subTest(name=name, gate=gate), self.assertRaises(ValueError):
                    if gate == "sealed-attempt":
                        release_probe._validate_attempt_contract(sealed_attempt)
                    else:
                        release_probe.load_release_probe_evidence(final_report)

    def test_main_rejects_other_resealed_prior_contract_drift_before_process(
        self,
    ) -> None:
        def skipped_but_attempted(value: dict[str, object]) -> None:
            value["probe_samples"][0]["attempted"] = True

        def unowned_profile(value: dict[str, object]) -> None:
            value["probe_samples"][0]["request_profile"] = "unowned-profile"

        def incomplete_load(value: dict[str, object]) -> None:
            value["installation_loads"][0]["status"] = "failed"

        for name, attempted_producers, mutate in (
            ("skipped-but-attempted", set(), skipped_but_attempted),
            (
                "unowned-profile",
                set(_reviewed_qualification_producers()),
                unowned_profile,
            ),
            (
                "incomplete-load",
                set(_reviewed_qualification_producers()),
                incomplete_load,
            ),
        ):
            with self.subTest(name=name):
                prior = _synthetic_attempt(
                    candidate_sha=_RC_CANDIDATE_SHA,
                    verified_at="2026-08-12T09:35:00+08:00",
                    attempted_producers=attempted_producers,
                )
                mutate(prior)
                forged = _reseal_attempt(prior)
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    output = root / "release-evidence.json"
                    output.write_bytes(_ORIGINAL_FINAL)
                    attempt_input = root / f"{name}.attempt.json"
                    attempt_input.write_text(
                        json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True)
                        + "\n",
                        encoding="utf-8",
                    )
                    attempt_output = root / "current.attempt.json"
                    with patch.object(
                        release_probe,
                        "run_release_probe_attempt",
                        side_effect=AssertionError("public process must not run"),
                    ) as public_process:
                        returncode, stdout, stderr = _invoke_main(
                            output,
                            attempt_input=attempt_input,
                            attempt_output=attempt_output,
                        )

                    self.assertEqual(returncode, 2)
                    self.assertEqual(stdout, "")
                    self.assertEqual(
                        stderr, "error: core operation release probe failed\n"
                    )
                    public_process.assert_not_called()
                    self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
                    self.assertFalse(attempt_output.exists())

    def test_attempt_sample_profile_owns_its_selected_source_result(self) -> None:
        sample = release_probe._attempt_probe_sample(
            producer_operation_id="sample_source@1",
            values=(
                {
                    "attempted": True,
                    "source_result": "failed",
                    "request_profile": "alpha-failed",
                },
                {
                    "attempted": True,
                    "source_result": "complete",
                    "request_profile": "beta-complete",
                },
            ),
        )

        self.assertEqual(sample["source_result"], "complete")
        self.assertEqual(sample["request_profile"], "beta-complete")

    def test_union_selects_latest_attempt_by_absolute_timestamp(self) -> None:
        producers = set(_reviewed_qualification_producers())
        earlier = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-13T09:00:00+08:00",
            attempted_producers=producers,
        )
        later = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-13T02:00:00+00:00",
            attempted_producers=producers,
        )

        final = release_probe.assemble_release_probe_evidence(
            (earlier, later),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at="2026-08-13T11:00:00+08:00",
        )

        self.assertTrue(
            all(
                item["verified_at"] == later["verified_at"]
                for item in final["live_probes"]
            )
        )

    def test_union_rejects_conflicting_owner_facts_at_the_same_evidence_time(
        self,
    ) -> None:
        producers = set(_reviewed_qualification_producers())
        first = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=producers,
        )
        second = deepcopy(first)
        second["verified_at"] = "2026-08-13T01:35:00+00:00"
        for sample in second["probe_samples"]:
            sample["verified_at"] = second["verified_at"]
        second["probe_samples"][0]["source_result"] = "credential_blocked"
        producer = second["probe_samples"][0]["producer_operation_id"]
        second["plan_snapshots"][0]["result"]["coverage"]["synthetic_release_contract"][
            "operations"
        ][producer]["source_error_codes"] = ["missing_credential"]
        second = _reseal_attempt(second)

        with self.assertRaisesRegex(ValueError, "conflicting live facts"):
            release_probe.assemble_release_probe_evidence(
                (first, second),
                candidate_sha=_RC_CANDIDATE_SHA,
                assembled_at=_RC_VERIFIED_AT,
            )

    def test_final_rejects_live_provenance_bound_only_to_a_skipped_profile(
        self,
    ) -> None:
        producers = set(_reviewed_qualification_producers())
        attempted = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-13T09:00:00+08:00",
            attempted_producers=producers,
        )
        skipped = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-13T10:00:00+08:00",
            attempted_producers=set(),
        )
        skipped_profile_snapshot = deepcopy(skipped["plan_snapshots"][0])
        skipped_profile_snapshot["request_profile"] = "skipped-profile"
        skipped["plan_snapshots"].append(skipped_profile_snapshot)
        for sample in skipped["probe_samples"]:
            sample["request_profile"] = "skipped-profile"
        skipped = _reseal_attempt(skipped)
        final = release_probe.assemble_release_probe_evidence(
            (attempted, skipped),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at="2026-08-13T10:00:00+08:00",
        )
        forged = deepcopy(final)
        forged["live_probes"][0]["request_profile"] = "skipped-profile"
        forged["live_probes"][0]["verified_at"] = skipped["verified_at"]
        forged["live_probes"][0]["attempt_digest"] = skipped["attempt_digest"]

        with self.assertRaisesRegex(ValueError, "attempted fact"):
            release_probe._validate_release_report(forged)

    def test_final_loader_rejects_an_unowned_attempt_digest(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        final = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at=_RC_VERIFIED_AT,
        )
        forged = deepcopy(final)
        forged["attempt_digests"] = sorted((*forged["attempt_digests"], "f" * 64))

        with self.assertRaisesRegex(ValueError, "attempt digests"):
            release_probe.load_release_probe_evidence(forged)

    def test_five_public_source_results_survive_attempt_union_loader_and_matrix(
        self,
    ) -> None:
        producers = _reviewed_qualification_producers()
        source_results = dict(
            zip(
                producers[:5],
                (
                    "complete",
                    "observed_empty",
                    "partial",
                    "credential_blocked",
                    "unavailable",
                ),
                strict=True,
            )
        )
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(producers),
        )
        operations = attempt["plan_snapshots"][0]["result"]["coverage"][
            "synthetic_release_contract"
        ]["operations"]
        execution_facts = {
            "complete": ("succeeded", "complete", True),
            "observed_empty": ("succeeded", "observed_empty", True),
            "partial": ("succeeded", "partial", False),
            "credential_blocked": ("failed", "failed", None),
            "unavailable": ("failed", "failed", None),
        }
        for producer, source_result in source_results.items():
            execution_state, state, query_complete = execution_facts[source_result]
            operations[producer]["execution_state"] = execution_state
            operations[producer]["state"] = state
            operations[producer]["query_complete"] = query_complete
            operations[producer]["source_error_codes"] = (
                ["missing_credential"] if source_result == "credential_blocked" else []
            )
            sample = next(
                item
                for item in attempt["probe_samples"]
                if item["producer_operation_id"] == producer
            )
            sample["source_result"] = source_result

        sealed_attempt = _reseal_attempt(attempt)
        final = release_probe.assemble_release_probe_evidence(
            (sealed_attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at=_RC_VERIFIED_AT,
        )
        snapshots, installation_loads, installation_health, live_probes = (
            release_probe.load_release_probe_evidence(final)
        )
        qualification_payload = json.loads(
            (
                REPOSITORY_ROOT
                / "docs"
                / "research"
                / "v0.3.0-operation-qualification.json"
            ).read_text(encoding="utf-8")
        )
        qualification_payload["schema_version"] = "2.0"
        for operation in qualification_payload["operations"]:
            operation["capability_ids"] = ["synthetic_release_contract"]
        qualification = release_probe.load_operation_qualification_manifest(
            qualification_payload,
            expected_producer_operation_ids=producers,
            expected_operation_applications=tuple(
                ("synthetic_release_contract", producer) for producer in producers
            ),
        )
        matrix = release_probe.build_operation_release_matrix(
            plan_snapshots=snapshots,
            qualification_evidence=qualification,
            installation_loads=installation_loads,
            installation_health=installation_health,
            live_probes=live_probes,
        ).to_dict()
        matrix_results = {
            item["producer_operation_id"]: item["live_probe"]["source_result"]
            for item in matrix["operations"]
        }
        self.assertEqual(
            {producer: matrix_results[producer] for producer in source_results},
            source_results,
        )

    def test_final_loader_rejects_non_v040_source_result_states(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        final = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at=_RC_VERIFIED_AT,
        )

        for source_result in ("failed", "rate_limited"):
            forged = deepcopy(final)
            forged["live_probes"][0]["source_result"] = source_result
            with (
                self.subTest(source_result=source_result),
                self.assertRaisesRegex(ValueError, "source result is unsupported"),
            ):
                release_probe.load_release_probe_evidence(forged)

    def test_attempt_loader_safely_rejects_excessive_json_depth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deep.attempt.json"
            nested = "[" * 2_000 + "0" + "]" * 2_000
            path.write_text(
                "{"
                '"artifact_kind":"core_operation_release_probe_attempt",'
                f'"attempt_digest":"{"0" * 64}",'
                f'"candidate_sha":"{_RC_CANDIDATE_SHA}",'
                f'"installation_health":{nested},'
                '"installation_loads":[],"plan_snapshots":[],'
                '"probe_samples":[{'
                f'"attempt_digest":"{"0" * 64}",'
                '"attempted":false,'
                f'"candidate_sha":"{_RC_CANDIDATE_SHA}",'
                '"probe":"probe","probe_status":"passed",'
                '"producer_operation_id":"owner@1",'
                '"request_profile":"profile","source_result":"unavailable",'
                f'"verified_at":"{_RC_VERIFIED_AT}"'
                "}],"
                '"schema_version":"1.0",'
                f'"verified_at":"{_RC_VERIFIED_AT}"'
                "}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "read safely"):
                release_probe.load_release_probe_attempt(
                    path,
                    expected_candidate_sha=_RC_CANDIDATE_SHA,
                    assembled_at=_RC_VERIFIED_AT,
                )

    def test_attempt_loader_rejects_expired_or_future_attempts(self) -> None:
        for name, verified_at in (
            ("expired", "2026-08-01T09:35:00+08:00"),
            ("future", "2026-08-14T09:35:00+08:00"),
        ):
            attempt = _synthetic_attempt(
                candidate_sha=_RC_CANDIDATE_SHA,
                verified_at=verified_at,
                attempted_producers=set(_reviewed_qualification_producers()),
            )
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / f"{name}.attempt.json"
                path.write_text(
                    json.dumps(attempt, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "stale"):
                    release_probe.load_release_probe_attempt(
                        path,
                        expected_candidate_sha=_RC_CANDIDATE_SHA,
                        assembled_at=_RC_VERIFIED_AT,
                    )

    def test_final_loader_rejects_fully_rehashed_stale_live_provenance(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        final = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at=_RC_VERIFIED_AT,
        )
        for probe in final["live_probes"]:
            probe["verified_at"] = "2026-08-01T09:35:00+08:00"

        with self.assertRaisesRegex(ValueError, "stale"):
            release_probe.load_release_probe_evidence(final)

    def test_main_reports_fixed_safe_failure_without_exception_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                side_effect=RuntimeError("Authorization secret server_url=10.0.0.1"),
            ) as run_attempt:
                returncode, stdout, stderr = _invoke_main(output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            run_attempt.assert_called_once()

    def test_main_atomically_writes_bounded_profile_failure_progress(self) -> None:
        reviewed_producers = _reviewed_qualification_producers()
        self.assertEqual(len(reviewed_producers), 44)
        progress = release_probe._ProbeProgress(
            verified_at="2026-08-13T09:35:00+08:00",
            stage="public_run",
            reviewed_producer_operation_ids=reviewed_producers,
            completed_request_profiles=("intraday-snapshot-sse",),
            failing_request_profile="identity-standard",
            observed_producer_operation_ids=(
                "sse_stock_list@1",
                "szse_stock_list@1",
            ),
            attempted_producer_operation_ids=("sse_stock_list@1",),
        )
        unsafe_detail = (
            "Authorization: Bearer provider-secret; "
            "server_url=https://10.0.0.1/private?access_token=token-value; "
            "request=/private/tmp/customer/request.json; "
            'raw_payload={"account":"customer-one"}'
        )
        failure = release_probe._ReleaseProbeFailure(progress)
        failure.__cause__ = RuntimeError(unsafe_detail)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)
            with patch.object(
                release_probe, "run_release_probe_attempt", side_effect=failure
            ):
                returncode, stdout, stderr = _invoke_main(output, failure_output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            failure_report = json.loads(failure_output.read_text(encoding="utf-8"))
            self.assertEqual(
                failure_report,
                {
                    "schema_version": "1.0",
                    "status": "failed",
                    "verified_at": "2026-08-13T09:35:00+08:00",
                    "stage": "public_run",
                    "completed_request_profiles": ["intraday-snapshot-sse"],
                    "failing_request_profile": "identity-standard",
                    "denominator_status": "available",
                    "denominator_source": "reviewed_qualification_manifest",
                    "external_producer_operation_ids": list(reviewed_producers),
                    "observed_external_producer_operation_ids": [
                        "sse_stock_list@1",
                        "szse_stock_list@1",
                    ],
                    "attempted_producer_operation_ids": ["sse_stock_list@1"],
                    "unattempted_producer_operation_ids": sorted(
                        set(reviewed_producers) - {"sse_stock_list@1"}
                    ),
                },
            )
            disclosed = (
                stdout
                + stderr
                + output.read_text(encoding="utf-8")
                + failure_output.read_text(encoding="utf-8")
            )
            for forbidden in (
                "provider-secret",
                "10.0.0.1",
                "access_token",
                "token-value",
                "/private/tmp/customer/request.json",
                "raw_payload",
                "customer-one",
            ):
                self.assertNotIn(forbidden, disclosed)

    def test_main_reports_progress_from_a_failed_public_profile_run(self) -> None:
        profiles = _identity_profiles("identity-fast", "identity-standard")
        unsafe_detail = (
            "Authorization: Bearer provider-secret "
            "https://10.0.0.1/private raw_payload customer-request.json"
        )
        execute = _identity_executor(
            attempted=True,
            fail_public_call=2,
            unsafe_detail=unsafe_detail,
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)

            returncode, stdout, stderr = _invoke_offline_identity_main(
                profiles=profiles,
                execute=execute,
                output=output,
                failure_output=failure_output,
            )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            failure_report = json.loads(failure_output.read_text(encoding="utf-8"))
            self.assertEqual(failure_report["stage"], "public_run")
            self.assertEqual(
                failure_report["completed_request_profiles"], ["identity-fast"]
            )
            self.assertEqual(
                failure_report["failing_request_profile"], "identity-standard"
            )
            self.assertEqual(
                failure_report["denominator_source"],
                "reviewed_qualification_manifest",
            )
            self.assertEqual(len(failure_report["external_producer_operation_ids"]), 52)
            self.assertEqual(
                failure_report["observed_external_producer_operation_ids"],
                list(_IDENTITY_PRODUCERS),
            )
            self.assertEqual(
                failure_report["attempted_producer_operation_ids"],
                list(_IDENTITY_PRODUCERS),
            )
            self.assertEqual(
                set(failure_report["unattempted_producer_operation_ids"]),
                set(failure_report["external_producer_operation_ids"])
                - set(_IDENTITY_PRODUCERS),
            )
            disclosed = (
                stdout
                + stderr
                + output.read_text(encoding="utf-8")
                + failure_output.read_text(encoding="utf-8")
            )
            for forbidden in (
                "provider-secret",
                "10.0.0.1",
                "raw_payload",
                "customer-request.json",
            ):
                self.assertNotIn(forbidden, disclosed)

    def test_main_reports_exact_unattempted_owners_on_truth_gate_failure(
        self,
    ) -> None:
        profiles = _identity_profiles("identity-fast")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)
            returncode, stdout, stderr = _invoke_offline_identity_main(
                profiles=profiles,
                execute=_identity_executor(attempted=False),
                output=output,
                failure_output=failure_output,
            )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            failure_report = json.loads(failure_output.read_text(encoding="utf-8"))
            self.assertEqual(failure_report["stage"], "truth_gate")
            self.assertEqual(
                failure_report["completed_request_profiles"], ["identity-fast"]
            )
            self.assertIsNone(failure_report["failing_request_profile"])
            self.assertEqual(
                failure_report["observed_external_producer_operation_ids"],
                list(_IDENTITY_PRODUCERS),
            )
            self.assertEqual(failure_report["attempted_producer_operation_ids"], [])
            self.assertEqual(
                failure_report["unattempted_producer_operation_ids"],
                failure_report["external_producer_operation_ids"],
            )

    def test_main_reports_plan_denominator_drift_as_a_truth_gate_failure(
        self,
    ) -> None:
        profiles = _identity_profiles("identity-fast")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)
            returncode, stdout, stderr = _invoke_offline_identity_main(
                profiles=profiles,
                execute=_identity_executor(attempted=True),
                output=output,
                failure_output=failure_output,
            )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            failure_report = json.loads(failure_output.read_text(encoding="utf-8"))
            self.assertEqual(failure_report["stage"], "truth_gate")
            self.assertEqual(
                failure_report["completed_request_profiles"], ["identity-fast"]
            )
            self.assertIsNone(failure_report["failing_request_profile"])
            observed = list(_IDENTITY_PRODUCERS)
            self.assertEqual(
                failure_report["observed_external_producer_operation_ids"],
                observed,
            )
            self.assertEqual(
                failure_report["attempted_producer_operation_ids"], observed
            )
            self.assertEqual(
                set(failure_report["unattempted_producer_operation_ids"]),
                set(failure_report["external_producer_operation_ids"]) - set(observed),
            )

    def test_main_rejects_an_unreviewed_public_producer_without_disclosing_it(
        self,
    ) -> None:
        profiles = _identity_profiles("identity-fast")
        unknown_producer = "unreviewed_source@1"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)
            returncode, stdout, stderr = _invoke_offline_identity_main(
                profiles=profiles,
                execute=_identity_executor(
                    attempted=True, producer_override=unknown_producer
                ),
                output=output,
                failure_output=failure_output,
            )

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            failure_report = json.loads(failure_output.read_text(encoding="utf-8"))
            self.assertEqual(failure_report["stage"], "public_result")
            self.assertEqual(failure_report["completed_request_profiles"], [])
            self.assertEqual(failure_report["failing_request_profile"], "identity-fast")
            self.assertEqual(
                failure_report["observed_external_producer_operation_ids"], []
            )
            self.assertEqual(failure_report["attempted_producer_operation_ids"], [])
            self.assertNotIn(
                unknown_producer, failure_output.read_text(encoding="utf-8")
            )

    def test_main_reports_unavailable_denominator_before_any_public_run(
        self,
    ) -> None:
        unsafe_detail = (
            "qualification path=/private/customer/manifest.json "
            "Authorization=provider-secret"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)
            with patch.object(
                release_probe,
                "_reviewed_producer_operation_ids",
                side_effect=ValueError(unsafe_detail),
            ):
                returncode, stdout, stderr = _invoke_main(output, failure_output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertEqual(
                json.loads(failure_output.read_text(encoding="utf-8")),
                {
                    "schema_version": "1.0",
                    "status": "failed",
                    "verified_at": "2026-08-13T09:35:00+08:00",
                    "stage": "qualification_manifest",
                    "completed_request_profiles": [],
                    "failing_request_profile": None,
                    "denominator_status": "unavailable",
                    "denominator_source": None,
                    "external_producer_operation_ids": None,
                    "observed_external_producer_operation_ids": [],
                    "attempted_producer_operation_ids": [],
                    "unattempted_producer_operation_ids": None,
                },
            )
            self.assertNotIn(unsafe_detail, failure_output.read_text(encoding="utf-8"))

    def test_failure_report_atomic_publish_never_overwrites_a_racing_file(
        self,
    ) -> None:
        reviewed_producers = _reviewed_qualification_producers()
        failure = release_probe._ReleaseProbeFailure(
            release_probe._ProbeProgress(
                verified_at="2026-08-13T09:35:00+08:00",
                stage="installation",
                reviewed_producer_operation_ids=reviewed_producers,
            )
        )
        racing_report = b"prior attempt diagnostic must survive\n"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)

            def publish_racing_report(_: object, destination: object) -> None:
                Path(destination).write_bytes(racing_report)
                raise FileExistsError("attempt diagnostic already exists")

            with (
                patch.object(
                    release_probe, "run_release_probe_attempt", side_effect=failure
                ),
                patch.object(
                    release_probe.os, "link", side_effect=publish_racing_report
                ),
            ):
                returncode, stdout, stderr = _invoke_main(output, failure_output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertEqual(failure_output.read_bytes(), racing_report)
            self.assertEqual(list(Path(temporary).glob(".rc2.failure.json.*.tmp")), [])

    def test_main_never_overwrites_a_preexisting_failure_report(self) -> None:
        prior_failure = b"prior attempt diagnostic must survive\n"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            failure_output = Path(temporary) / "rc2.failure.json"
            output.write_bytes(_ORIGINAL_FINAL)
            failure_output.write_bytes(prior_failure)
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                side_effect=AssertionError("probe must not start"),
            ):
                returncode, stdout, stderr = _invoke_main(output, failure_output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertEqual(output.read_bytes(), _ORIGINAL_FINAL)
            self.assertEqual(failure_output.read_bytes(), prior_failure)

    def test_main_rejects_normalized_aliases_for_final_and_failure_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "subdirectory").mkdir()
            output = root / "release-evidence.json"
            failure_output = root / "subdirectory" / ".." / output.name
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                side_effect=AssertionError("probe must not start"),
            ):
                returncode, stdout, stderr = _invoke_main(output, failure_output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            self.assertFalse(output.exists())

    def test_main_safely_reports_a_bounded_public_process_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            with patch.object(
                release_probe,
                "run_release_probe_attempt",
                side_effect=subprocess.TimeoutExpired(
                    ["entrypoint.py", "run", "--request", "private-path.json"],
                    150,
                    stderr="Authorization: secret",
                ),
            ) as run_attempt:
                returncode, stdout, stderr = _invoke_main(output)

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "error: core operation release probe failed\n")
            run_attempt.assert_called_once()

    def test_main_preserves_existing_artifact_when_denominator_is_incomplete(
        self,
    ) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(),
        )
        original = b"NONFINAL artifact must survive\n"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            output.write_bytes(original)
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe, "run_release_probe_attempt", return_value=attempt
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(_rc_main_argv(output))

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            self.assertEqual(output.read_bytes(), original)

    def test_main_preserves_existing_artifact_when_atomic_replace_fails(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        original = b"NONFINAL artifact must survive\n"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-evidence.json"
            output.write_bytes(original)
            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(
                    release_probe,
                    "_repository_candidate_sha",
                    return_value=_RC_CANDIDATE_SHA,
                ),
                patch.object(
                    release_probe, "run_release_probe_attempt", return_value=attempt
                ),
                patch.object(
                    release_probe.os,
                    "replace",
                    side_effect=OSError("private destination path"),
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                returncode = release_probe.main(_rc_main_argv(output))

            self.assertEqual(returncode, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(), "error: core operation release probe failed\n"
            )
            self.assertEqual(output.read_bytes(), original)

    def test_reviewed_authority_uses_manifest_set_not_historical_numeric_total(
        self,
    ) -> None:
        payload = json.loads(
            (
                REPOSITORY_ROOT
                / "docs"
                / "research"
                / "v0.3.0-operation-qualification.json"
            ).read_text(encoding="utf-8")
        )
        payload["operations"] = payload["operations"][:3]
        expected = tuple(
            sorted(item["producer_operation_id"] for item in payload["operations"])
        )
        with tempfile.TemporaryDirectory() as temporary:
            authority = Path(temporary) / "static-operation-qualification.json"
            authority.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            with patch.object(
                release_probe,
                "DEFAULT_QUALIFICATION",
                authority,
            ):
                reviewed = release_probe._reviewed_producer_operation_ids()

        self.assertEqual(reviewed, expected)

    def test_maintainer_probe_defaults_to_static_v040_qualification_authority(
        self,
    ) -> None:
        expected = (
            REPOSITORY_ROOT
            / "docs"
            / "research"
            / "v0.4.0-overview-v2-operation-qualification.json"
        )
        self.assertEqual(release_probe.DEFAULT_QUALIFICATION, expected)
        payload = json.loads(expected.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "2.0")
        self.assertTrue(payload["operations"])
        self.assertTrue(all(item["capability_ids"] for item in payload["operations"]))

    def test_reviewed_application_authority_exactly_closes_historical_plans(
        self,
    ) -> None:
        from a_share_research.operation_release_matrix import (
            ReleasePlanSnapshot,
            formal_operation_applications,
            formal_producer_operation_ids,
            load_operation_qualification_manifest,
        )

        historical = json.loads(
            (
                REPOSITORY_ROOT
                / "docs"
                / "research"
                / "v0.3.0-release-probe-evidence.json"
            ).read_text(encoding="utf-8")
        )
        snapshots = tuple(
            ReleasePlanSnapshot(
                result=item["result"],
                request_profile=item["request_profile"],
            )
            for item in historical["plan_snapshots"]
        )
        plan_authority = set(formal_operation_applications(snapshots))
        reviewed_manifest = json.loads(
            release_probe.DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
        )
        reviewed_authority = {
            (capability_id, operation["producer_operation_id"])
            for operation in reviewed_manifest["operations"]
            for capability_id in operation["capability_ids"]
        }

        target_applications = {
            ("whole_market_turnover", "sse_a_share_turnover@1"),
            ("whole_market_turnover", "szse_a_share_turnover@1"),
            ("whole_market_turnover", "bse_a_share_turnover@1"),
            ("whole_market_margin", "sse_market_margin@1"),
            ("whole_market_margin", "szse_market_margin@1"),
            ("whole_market_margin", "bse_market_margin@1"),
            ("market_trading_session", "market_session_context@1"),
            ("major_broad_indices", "fixed_four_indices@1"),
        }
        self.assertEqual(reviewed_authority - plan_authority, target_applications)
        self.assertFalse(plan_authority - reviewed_authority)
        historical_manifest = deepcopy(reviewed_manifest)
        historical_manifest["operations"] = [
            operation
            for operation in historical_manifest["operations"]
            if operation["producer_operation_id"]
            in set(formal_producer_operation_ids(snapshots))
        ]
        qualification = load_operation_qualification_manifest(
            historical_manifest,
            expected_producer_operation_ids=formal_producer_operation_ids(snapshots),
            expected_operation_applications=tuple(sorted(plan_authority)),
        )
        self.assertEqual(
            {
                (capability_id, item.producer_operation_id)
                for item in qualification
                for capability_id in item.capability_ids
            },
            plan_authority,
        )

    def test_all_profiles_use_public_calls_and_close_plan_derived_producer_evidence(
        self,
    ) -> None:
        from a_share_research.adjusted_series_sources import (
            EastmoneyForwardAdjustedDailyLineOperation,
            TencentForwardAdjustedDailyLineOperation,
        )
        from a_share_research.automatic_valuation import (
            _VALUATION_REQUIRED_OPERATIONS,
        )
        from a_share_research.capital_registry import build_default_capital_operations
        from a_share_research.close_sources import (
            SseDailyLineOperation,
            SzseDailyLineOperation,
            TencentDailyLineOperation,
        )
        from a_share_research.content_registry import build_default_content_operations
        from a_share_research.etf_market import (
            SseEtfListOperation,
            SseEtfSnapshotOperation,
        )
        from a_share_research.etf_option_registry import (
            build_default_etf_option_operations,
        )
        from a_share_research.identity_sources import build_default_identity_operations
        from a_share_research.intraday_replay_registry import (
            build_default_intraday_replay_operations,
        )
        from a_share_research.intraday_sources import (
            TencentIntradayOperation,
            TongdaxinIntradayOperation,
        )
        from a_share_research.market_signal_registry import (
            build_default_market_signal_operations,
        )
        from a_share_research.operation_release_matrix import (
            ReleasePlanSnapshot,
            formal_operation_applications,
            formal_producer_operation_ids,
            load_operation_qualification_manifest,
        )
        from a_share_research.research_result import _NoIoHttpTransport
        from a_share_research.research_runtime import research

        profiles = release_probe.build_release_request_profiles(
            as_of="2026-08-12", replay_date="2026-08-11"
        )
        health_operation_ids = (
            "mootdx_f10@1",
            "mootdx_intraday_replay@1",
            "tongdaxin_intraday_snapshot@1",
        )
        health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [
                {
                    "distribution": "mootdx",
                    "required_version": "0.11.7",
                    "installed_version": "0.11.7",
                    "status": "complete",
                    "modules": ["mootdx.quotes"],
                    "operation_ids": list(health_operation_ids),
                }
            ],
            "operations": [
                {"operation_id": operation_id, "status": "complete"}
                for operation_id in health_operation_ids
            ],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }
        calls: list[list[str]] = []
        sent_requests: list[dict[str, object]] = []

        class UnavailableTransport:
            def get(self, url: str, headers: dict[str, str]) -> object:
                from a_share_research.identity_sources import HttpResponse

                del url, headers
                return HttpResponse(
                    status=503,
                    content_type="application/json",
                    body=b"{}",
                    retrieved_at=datetime(
                        2026,
                        8,
                        12,
                        18,
                        0,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                )

            def post(self, url: str, headers: dict[str, str], body: bytes) -> object:
                del body
                return self.get(url, headers)

        def execute(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if Path(command[-1]).name == "install.py":
                return subprocess.CompletedProcess(command, 0, json.dumps(health), "")
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            sent_requests.append(deepcopy(request))
            if (
                request.get("parameters", {}).get("workflow", {}).get("id")
                == "market_overview_research"
            ):
                from tests.test_market_overview_research import (
                    invoke_copied_installed,
                )

                completed = invoke_copied_installed(
                    request,
                    environment_overrides={
                        "ASR96_NOW": "2026-08-12T18:00:00+08:00",
                        "ASR96_LEGACY_SOURCES_UNAVAILABLE": "1",
                    },
                )
                if completed.returncode != 0:
                    raise AssertionError(completed.stderr)
                public = json.loads(completed.stdout)
                _mark_external_fixture_applications_failed(public)
                return subprocess.CompletedProcess(command, 0, json.dumps(public), "")
            with patch(
                "a_share_research.research_runtime.UrlLibTransport",
                side_effect=AssertionError(
                    "offline fixture attempted a default HTTP transport"
                ),
            ):
                public = research(
                    request,
                    identity_transport=UnavailableTransport(),
                    content_transport=UnavailableTransport(),
                    capital_transport=UnavailableTransport(),
                    market_signal_transport=UnavailableTransport(),
                    etf_option_transport=UnavailableTransport(),
                    intraday_transport=UnavailableTransport(),
                    research_now=datetime(
                        2026,
                        8,
                        12,
                        18,
                        0,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                    available_optional_dependencies=(),
                ).to_dict()
            # This deterministic executor is a fixed sanitized contract sample,
            # not live evidence.  Mark its plan-owned applications as failed so
            # orchestration aggregation exercises the attempted truth path; the
            # real release artifact is accepted only from actual public runs.
            _mark_external_fixture_applications_failed(public)
            return subprocess.CompletedProcess(command, 0, json.dumps(public), "")

        attempt = release_probe.run_release_probe_attempt(
            skill_root=SKILL_ROOT,
            profiles=profiles,
            verified_at="2026-08-12T09:30:00+08:00",
            candidate_sha=_RC_CANDIDATE_SHA,
            execute=execute,
        )
        report = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at="2026-08-12T09:30:00+08:00",
        )
        # This synthetic current run reaches 61 applications. It must not
        # finalize against the reviewed 67-application authority.
        with self.assertRaisesRegex(
            ValueError, "qualification application authority does not close the plan"
        ):
            release_probe._validate_release_report(report)

        self.assertEqual(len(calls), len(profiles) + 1)
        self.assertEqual(sent_requests[0]["task_type"], "intraday_market_signal")
        expected_execution = sorted(
            profiles,
            key=lambda item: (
                0 if item.name == "intraday-snapshot-sse" else 1,
                item.name,
            ),
        )
        self.assertEqual(
            sent_requests, [dict(profile.request) for profile in expected_execution]
        )
        self.assertEqual(len(report["plan_snapshots"]), len(profiles))
        self.assertEqual(
            [item["request_profile"] for item in report["plan_snapshots"]],
            sorted(profile.name for profile in profiles),
        )
        self.assertEqual(
            {
                item["producer_operation_id"]
                for item in report["installation_loads"]
                if item["origin"] == "installation_health"
            },
            set(health_operation_ids),
        )
        self.assertTrue(
            all(item["probe_status"] == "passed" for item in report["live_probes"])
        )
        self.assertTrue(
            all(item["attempted"] is True for item in report["live_probes"])
        )
        transport = _NoIoHttpTransport()
        replay_operations, replay_daily_operations = (
            build_default_intraday_replay_operations(
                transport=transport,
                client_factory=lambda: None,
            )
        )
        default_producer_authorities = {
            *(
                item.operation_id
                for item in build_default_identity_operations(transport)
            ),
            SseDailyLineOperation.operation_id,
            SzseDailyLineOperation.operation_id,
            TencentDailyLineOperation.operation_id,
            EastmoneyForwardAdjustedDailyLineOperation.operation_id,
            TencentForwardAdjustedDailyLineOperation.operation_id,
            SseEtfListOperation.operation_id,
            SseEtfSnapshotOperation.operation_id,
            *(
                item.operation_id
                for item in build_default_etf_option_operations(transport)
            ),
            *_VALUATION_REQUIRED_OPERATIONS,
            *(
                item.operation_id
                for item in build_default_content_operations(
                    transport,
                    allow_credentials=True,
                    allow_fallback=True,
                    research_now=datetime(
                        2026,
                        8,
                        12,
                        18,
                        0,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                )
            ),
            *(
                item.operation_id
                for item in build_default_capital_operations(transport)
            ),
            *(
                item.operation_id
                for item in build_default_market_signal_operations(transport)
            ),
            # The copied-install probe exercises ``build_default_intraday_operations``;
            # this default-install-independent test reads its two owner contracts.
            TongdaxinIntradayOperation.operation_id,
            TencentIntradayOperation.operation_id,
            *(item.operation_id for item in replay_operations),
            *(item.operation_id for item in replay_daily_operations),
            "market_session_context@1",
            "fixed_four_indices@1",
            "sse_a_share_turnover@1",
            "szse_a_share_turnover@1",
            "bse_a_share_turnover@1",
            "sse_market_margin@1",
            "szse_market_margin@1",
            "bse_market_margin@1",
        }
        snapshots = tuple(
            ReleasePlanSnapshot(
                result=item["result"],
                request_profile=item["request_profile"],
            )
            for item in report["plan_snapshots"]
        )
        plan_derived_producers = set(formal_producer_operation_ids(snapshots))
        plan_derived_applications = formal_operation_applications(snapshots)
        self.assertEqual(
            {item["producer_operation_id"] for item in report["live_probes"]},
            plan_derived_producers,
        )
        self.assertEqual(
            {item["producer_operation_id"] for item in report["installation_loads"]},
            plan_derived_producers,
        )
        self.assertEqual(plan_derived_producers, default_producer_authorities)
        qualification_payload = json.loads(
            release_probe.DEFAULT_QUALIFICATION.read_text(encoding="utf-8")
        )
        load_operation_qualification_manifest(
            qualification_payload,
            expected_producer_operation_ids=tuple(plan_derived_producers),
        )
        active_qualification_payload = deepcopy(qualification_payload)
        active_qualification_payload["operations"] = [
            operation
            for operation in active_qualification_payload["operations"]
            if operation["disposition"] != "pending_dependency"
        ]
        with self.assertRaisesRegex(
            ValueError, "qualification application authority does not close the plan"
        ):
            load_operation_qualification_manifest(
                active_qualification_payload,
                expected_producer_operation_ids=tuple(plan_derived_producers),
                expected_operation_applications=plan_derived_applications,
            )
        qualification = load_operation_qualification_manifest(
            active_qualification_payload,
            expected_producer_operation_ids=tuple(plan_derived_producers),
        )
        self.assertEqual(
            {item.producer_operation_id for item in qualification},
            plan_derived_producers,
        )

        incomplete = deepcopy(report)
        missing_owner = "tencent_intraday_snapshot@1"
        snapshot_result = next(
            item
            for item in incomplete["plan_snapshots"]
            if item["request_profile"] == "intraday-snapshot-sse"
        )
        snapshot_plan = snapshot_result["result"]["plan"]
        snapshot_plan["operation_obligations"] = [
            item
            for item in snapshot_plan["operation_obligations"]
            if item["producer_operation_id"] != missing_owner
        ]
        del snapshot_result["result"]["coverage"]["intraday_market_snapshot"][
            "operations"
        ][missing_owner]
        incomplete["installation_loads"] = [
            item
            for item in incomplete["installation_loads"]
            if item["producer_operation_id"] != missing_owner
        ]
        incomplete["live_probes"] = [
            item
            for item in incomplete["live_probes"]
            if item["producer_operation_id"] != missing_owner
        ]
        with self.assertRaisesRegex(
            ValueError, "qualification evidence does not close the denominator"
        ):
            release_probe._validate_release_report(incomplete)

    def test_workflow_sanitizer_keeps_only_outer_use_and_leaf_plan_contracts(
        self,
    ) -> None:
        from tests.test_direct_research_workflow_runtime import (
            comparison_workflow_request,
        )
        from tests.test_research_workflows import fixture_runtime

        result = (
            fixture_runtime()
            .research(comparison_workflow_request(["工业富联", "贵州茅台"]))
            .to_dict()
        )
        result["observations"].append(
            {"id": "raw-secret", "payload": {"raw_payload": "secret-value"}}
        )

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="workflow-valuation-comparison",
            request_as_of="2026-08-02",
            result=result,
            verified_at="2026-08-12T09:30:00+08:00",
        )

        safe_result = snapshot["result"]
        self.assertEqual(
            set(safe_result),
            {"schema_version", "plan", "capability_registry", "coverage"},
        )
        self.assertEqual(
            set(safe_result["plan"]),
            {"id", "version", "task_type", "workflow_id", "steps"},
        )
        registered = safe_result["capability_registry"]["steps"]["comparison"]
        self.assertEqual(
            set(registered), {"step_id", "task_type", "version", "leaf_plan"}
        )
        self.assertEqual(set(safe_result["coverage"]["steps"]["comparison"]), {"leaf"})
        self.assertEqual(len(registered["leaf_plan"]["operation_obligations"]), 18)
        self.assertEqual(len(evidence), 18)
        self.assertNotIn(
            "secret-value",
            json.dumps({"snapshot": snapshot, "evidence": evidence}),
        )

    def test_request_inventory_covers_released_plan_axes_without_operation_ids(
        self,
    ) -> None:
        profiles = release_probe.build_release_request_profiles(
            as_of="2026-08-12", replay_date="2026-08-11"
        )
        requests = [profile.request for profile in profiles]
        by_task: dict[str, list[dict[str, object]]] = {}
        for request in requests:
            by_task.setdefault(request["task_type"], []).append(request)

        self.assertEqual(len({profile.name for profile in profiles}), len(profiles))
        self.assertTrue(
            all(
                isinstance(profile.harness_timeout_seconds, int)
                and 120 < profile.harness_timeout_seconds <= 1200
                for profile in profiles
            )
        )
        self.assertEqual(
            next(
                profile.harness_timeout_seconds
                for profile in profiles
                if profile.name == "workflow-new-security-research"
            ),
            1200,
        )
        self.assertEqual(
            set(by_task),
            {
                "security_identity",
                "market_trend",
                "etf_market",
                "etf_options",
                "security_valuation",
                "valuation_compare",
                "research_content",
                "capital_events",
                "market_signals",
                "intraday_market_signal",
                "intraday_replay",
                "research_workflow",
            },
        )
        self.assertEqual(
            {
                request["parameters"].get("completeness_mode", "standard")
                for request in by_task["security_identity"]
            },
            {"standard", "fast"},
        )
        self.assertEqual(
            {
                request["parameters"]["adjustment"]
                for request in by_task["market_trend"]
            },
            {"unadjusted", "forward_adjusted"},
        )
        self.assertEqual(
            {
                subject["clue"][:3]
                for request in by_task["valuation_compare"]
                for subject in request["subjects"]
            },
            {"000", "601"},
        )
        self.assertEqual(
            {
                request["parameters"]["workflow"]["id"]
                for request in by_task["research_workflow"]
            },
            {
                "single_security_research",
                "single_security_valuation",
                "valuation_comparison",
                "theme_report_research",
                "new_security_research",
                "market_overview_research",
            },
        )
        single_security = next(
            profile
            for profile in profiles
            if profile.name == "workflow-single-security-research"
        )
        self.assertEqual(
            single_security.request["parameters"]["inputs"],
            {"target_pe": "30"},
        )
        self.assertEqual(single_security.harness_timeout_seconds, 1200)
        market_overview = next(
            profile
            for profile in profiles
            if profile.name == "workflow-market-overview-research-standard"
        )
        self.assertEqual(
            market_overview.request,
            {
                "schema_version": "1.0",
                "task_type": "research_workflow",
                "subjects": [],
                "as_of": "2026-08-12",
                "window": None,
                "parameters": {
                    "workflow": {
                        "id": "market_overview_research",
                        "version": "2.0",
                    },
                    "inputs": {"completeness_mode": "standard"},
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                },
            },
        )
        self.assertEqual(market_overview.harness_timeout_seconds, 1200)
        self.assertEqual(
            {
                material_type
                for request in by_task["research_content"]
                for material_type in request["parameters"]["material_types"]
            },
            {
                "research_report",
                "industry_report",
                "consensus_material",
                "issuer_profile",
                "stock_news",
                "announcement",
                "market_flash",
                "investor_qa",
            },
        )
        self.assertEqual(
            {
                data_type
                for request in by_task["capital_events"]
                for data_type in request["parameters"]["data_types"]
            },
            {
                "northbound_flow",
                "stock_fund_flow",
                "board_fund_flow",
                "dragon_tiger",
                "market_dragon_tiger",
                "lockup",
                "margin_trading",
                "block_trade",
                "shareholder_count",
                "dividend",
            },
        )
        self.assertEqual(
            {
                signal_type
                for request in by_task["market_signals"]
                for signal_type in request["parameters"]["signal_types"]
            },
            {
                "strong_stock_theme",
                "security_board_membership",
                "industry_rotation",
                "limit_state",
                "focus_monitoring",
                "severe_abnormal_movement",
                "monitoring_intersection",
                "market_heat",
            },
        )
        self.assertEqual(
            {
                request["subjects"][0]["security"].partition(":")[0]
                for request in by_task["intraday_replay"]
            },
            {"SSE", "SZSE"},
        )
        self.assertTrue(
            any(
                request["source_policy"]["allow_credentials"]
                for request in by_task["research_content"]
            )
        )
        self.assertTrue(all(request["as_of"] == "2026-08-12" for request in requests))
        serialized = json.dumps(requests, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("operation_id", serialized)
        self.assertNotIn("@1", serialized)

    def test_orchestration_installs_copy_then_calls_only_its_public_run_seam(
        self,
    ) -> None:
        producer_operation_id = "sse_stock_list@1"
        profile = release_probe.ProbeRequestProfile(
            name="identity-standard",
            request={
                "schema_version": "1.0",
                "task_type": "security_identity",
                "subjects": [{"clue": "600519"}],
                "as_of": "2026-08-12",
                "window": None,
                "parameters": {"mode": "standard"},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": False,
                },
            },
        )
        health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [],
            "operations": [],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }
        public_result = {
            "schema_version": "1.0",
            "status": "blocked",
            "research_anchor": {
                "as_of": "2026-08-12",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "identity-standard",
                "version": "1.0",
                "task_type": "security_identity",
                "capabilities": ["identity"],
                "operation_obligations": [
                    {
                        "capability_id": "identity",
                        "operation_id": producer_operation_id,
                        "producer_operation_id": producer_operation_id,
                        "operation_kind": "external_source",
                        "source_role": "primary",
                        "experimental": True,
                        "exchange": None,
                        "evidence_uses": [
                            {"use": "security_identity", "role": "supporting"}
                        ],
                    }
                ],
                "research_dependencies": [],
            },
            "coverage": {
                "identity": {
                    "state": "failed",
                    "operations": {
                        producer_operation_id: {
                            "capability_id": "identity",
                            "operation_id": producer_operation_id,
                            "execution_state": "failed",
                            "state": "failed",
                            "query_complete": None,
                            "observation_ids": [],
                            "source_error_ids": [],
                            "field_dispositions": [
                                {
                                    "field": "security",
                                    "disposition": "normalized",
                                    "destination": "subjects[].security",
                                }
                            ],
                            "diagnostics": {"server_url": "https://10.0.0.1/private"},
                        }
                    },
                }
            },
            "observations": [{"raw_payload": "provider-secret"}],
        }
        calls: list[tuple[list[str], dict[str, object]]] = []
        requests: list[dict[str, object]] = []

        def execute(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            if Path(command[-1]).name == "install.py":
                return subprocess.CompletedProcess(
                    command, 0, json.dumps(health), "provider install detail"
                )
            self.assertEqual(command[-3:-1], ["run", "--request"])
            requests.append(json.loads(Path(command[-1]).read_text(encoding="utf-8")))
            self.assertNotIn("ASR_TEST_SECRET", kwargs["env"])
            return subprocess.CompletedProcess(
                command, 0, json.dumps(public_result), "provider stderr secret"
            )

        with patch.dict(os.environ, {"ASR_TEST_SECRET": "must-not-propagate"}):
            report = release_probe.run_release_probe_attempt(
                skill_root=SKILL_ROOT,
                profiles=(profile,),
                verified_at="2026-08-12T09:30:00+08:00",
                candidate_sha=_RC_CANDIDATE_SHA,
                execute=execute,
            )

        self.assertEqual(len(calls), 2)
        install_command, install_kwargs = calls[0]
        run_command, run_kwargs = calls[1]
        self.assertEqual(install_command[:2], [sys.executable, "-I"])
        self.assertEqual(Path(install_command[-1]).name, "install.py")
        self.assertEqual(install_kwargs["timeout"], 1200)
        self.assertNotEqual(
            Path(install_command[-1]).resolve(),
            (SKILL_ROOT / "scripts" / "install.py").resolve(),
        )
        self.assertEqual(run_command[:2], [sys.executable, "-I"])
        self.assertEqual(Path(run_command[2]).name, "entrypoint.py")
        self.assertEqual(run_kwargs["timeout"], profile.harness_timeout_seconds)
        self.assertEqual(requests, [profile.request])
        self.assertEqual(
            set(report),
            {
                "schema_version",
                "artifact_kind",
                "candidate_sha",
                "verified_at",
                "installation_health",
                "plan_snapshots",
                "installation_loads",
                "probe_samples",
                "attempt_digest",
            },
        )
        self.assertEqual(report["candidate_sha"], _RC_CANDIDATE_SHA)
        self.assertEqual(report["verified_at"], "2026-08-12T09:30:00+08:00")
        self.assertEqual(report["installation_health"], health)
        self.assertEqual(
            report["installation_loads"],
            [
                {
                    "producer_operation_id": producer_operation_id,
                    "origin": "copied_install_plan",
                    "status": "complete",
                    "selector": "identity-standard",
                }
            ],
        )
        self.assertEqual(report["probe_samples"][0]["source_result"], "unavailable")
        self.assertIs(report["probe_samples"][0]["attempted"], True)
        self.assertEqual(
            report["probe_samples"][0]["attempt_digest"], report["attempt_digest"]
        )
        serialized = json.dumps(report, ensure_ascii=False)
        for forbidden in (
            "provider-secret",
            "provider stderr secret",
            "provider install detail",
            "10.0.0.1",
        ):
            self.assertNotIn(forbidden, serialized)

        cast(dict[str, object], public_result["research_anchor"])["as_of"] = (
            "2026-08-11"
        )
        with self.assertRaises(release_probe._ReleaseProbeFailure) as failure:
            release_probe.run_release_probe_attempt(
                skill_root=SKILL_ROOT,
                profiles=(profile,),
                verified_at="2026-08-12T09:30:00+08:00",
                candidate_sha=_RC_CANDIDATE_SHA,
                execute=execute,
            )
        self.assertEqual(failure.exception.progress.stage, "public_result")
        self.assertEqual(
            failure.exception.progress.failing_request_profile, "identity-standard"
        )

    def test_copied_processes_inherit_only_validated_credential_free_routing(
        self,
    ) -> None:
        profile = release_probe.ProbeRequestProfile(
            name="identity-standard",
            request={
                "schema_version": "1.0",
                "task_type": "security_identity",
                "subjects": [{"clue": "600519"}],
                "as_of": "2026-08-12",
                "window": None,
                "parameters": {"completeness_mode": "standard"},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": False,
                },
            },
        )
        operation_id = "sse_stock_list@1"
        health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [],
            "operations": [],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }
        result = {
            "schema_version": "1.0",
            "research_anchor": {
                "as_of": "2026-08-12",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "identity-standard",
                "version": "1.0",
                "task_type": "security_identity",
                "capabilities": ["identity"],
                "operation_obligations": [
                    {
                        "capability_id": "identity",
                        "operation_id": operation_id,
                        "producer_operation_id": operation_id,
                        "operation_kind": "external_source",
                        "source_role": "primary",
                        "experimental": True,
                        "exchange": None,
                        "evidence_uses": [
                            {"use": "security_identity", "role": "supporting"}
                        ],
                    }
                ],
                "research_dependencies": [],
            },
            "coverage": {
                "identity": {
                    "operations": {
                        operation_id: {
                            "capability_id": "identity",
                            "operation_id": operation_id,
                            "execution_state": "failed",
                            "state": "failed",
                            "query_complete": None,
                            "field_dispositions": [
                                {
                                    "field": "security",
                                    "disposition": "normalized",
                                    "destination": "subjects[].security",
                                }
                            ],
                        }
                    }
                }
            },
            "source_errors": [],
        }
        observed_environments: list[dict[str, str]] = []

        def execute(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            environment = deepcopy(kwargs["env"])
            observed_environments.append(environment)
            output = health if Path(command[-1]).name == "install.py" else result
            return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

        safe_routing = {
            "HTTP_PROXY": "http://package-route.invalid:8080",
            "http_proxy": "https://lower-route.invalid:8081",
            "NO_PROXY": "localhost,127.0.0.1",
        }
        unsafe_proxy_routes = (
            "https://proxy-user:proxy-secret@route.invalid:8443",
            "https://fragment-route.invalid:8443#internal",
            "https://proxy.example:8443/?access_token=SECRET",
            "https://path-route.invalid:8443/private",
        )
        unsafe_no_proxy = "localhost,credential@host.invalid"
        forbidden_host = {
            "AUTHORIZATION": "Bearer secret",
            "PIP_INDEX_URL": "https://token@packages.invalid/simple",
            "ACCESS_TOKEN": "secret",
        }
        reports = []
        for unsafe_proxy_route in unsafe_proxy_routes:
            with patch.dict(
                os.environ,
                {
                    **safe_routing,
                    **forbidden_host,
                    "HTTPS_PROXY": unsafe_proxy_route,
                    "no_proxy": unsafe_no_proxy,
                },
                clear=True,
            ):
                reports.append(
                    release_probe.run_release_probe_attempt(
                        skill_root=SKILL_ROOT,
                        profiles=(profile,),
                        verified_at="2026-08-12T09:30:00+08:00",
                        candidate_sha=_RC_CANDIDATE_SHA,
                        execute=execute,
                    )
                )

        for environment in observed_environments:
            self.assertEqual(
                {key: environment.get(key) for key in safe_routing}, safe_routing
            )
            self.assertTrue(
                all(
                    key not in environment
                    for key in ("HTTPS_PROXY", "no_proxy", *forbidden_host)
                )
            )
        serialized = json.dumps(reports, ensure_ascii=False)
        for value in (
            *safe_routing.values(),
            *unsafe_proxy_routes,
            unsafe_no_proxy,
            *forbidden_host.values(),
        ):
            self.assertNotIn(value, serialized)

    def test_verified_at_argument_is_canonical_string_not_datetime(self) -> None:
        value = release_probe._verified_at_argument("2026-08-12T01:30:00Z")

        self.assertIsInstance(value, str)
        self.assertEqual(value, "2026-08-12T01:30:00+00:00")

    def test_sanitizer_accepts_builder_source_error_reference_order(self) -> None:
        result = _built_failed_source_error_result(
            {
                "code": "missing_credential",
                "message": "Missing release credential.",
            },
            {
                "code": "permission_denied",
                "message": "Release credential lacks permission.",
            },
        )
        source_error_ids = [item["id"] for item in result["source_errors"]]
        producer = _reviewed_qualification_producers()[0]
        operation = result["coverage"]["shared_source_error_contract"]["operations"][
            producer
        ]

        self.assertEqual(source_error_ids, sorted(source_error_ids, reverse=True))
        self.assertEqual(operation["source_error_ids"], source_error_ids)

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="builder-source-error-order",
            request_as_of="2026-08-12",
            result=result,
            verified_at="2026-08-12T09:30:00+08:00",
        )

        safe_operation = snapshot["result"]["coverage"]["shared_source_error_contract"][
            "operations"
        ][producer]
        self.assertEqual(
            safe_operation["source_error_codes"],
            ["missing_credential", "permission_denied"],
        )
        self.assertEqual(evidence[0]["source_result"], "credential_blocked")
        serialized = json.dumps(
            {"snapshot": snapshot, "evidence": evidence}, ensure_ascii=False
        )
        for forbidden in (
            *source_error_ids,
            "Release credential lacks permission.",
            "Missing release credential.",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_sanitizer_accepts_public_builder_source_error_string_values(
        self,
    ) -> None:
        cases = (
            (
                "credential-257-message",
                "missing_credential",
                "m" * 257,
                ["missing_credential"],
                "credential_blocked",
            ),
            (
                "credential-longer-bounded-message",
                "missing_credential",
                "long bounded public message " * 256,
                ["missing_credential"],
                "credential_blocked",
            ),
            (
                "credential-empty-message",
                "missing_credential",
                "",
                ["missing_credential"],
                "credential_blocked",
            ),
            (
                "credential-escaped-control-message",
                "missing_credential",
                "public diagnostic\u0001message",
                ["missing_credential"],
                "credential_blocked",
            ),
            (
                "unknown-long-code",
                "u" * 257,
                "Public unknown source failure.",
                [],
                "unavailable",
            ),
        )
        producer = _reviewed_qualification_producers()[0]

        for name, code, message, expected_codes, expected_source_result in cases:
            with self.subTest(name=name):
                result = _built_failed_source_error_result(
                    {"code": code, "message": message}
                )
                canonical_payload = {
                    "capability_id": "shared_source_error_contract",
                    "operation_id": producer,
                    "code": code,
                    "message": message,
                }
                source_error = result["source_errors"][0]
                self.assertEqual(
                    source_error,
                    {
                        "id": stable_result_id("source-error", canonical_payload),
                        **canonical_payload,
                    },
                )

                snapshot, evidence = release_probe.sanitize_public_result(
                    request_profile=name,
                    request_as_of="2026-08-12",
                    result=result,
                    verified_at="2026-08-12T09:30:00+08:00",
                )

                operation = snapshot["result"]["coverage"][
                    "shared_source_error_contract"
                ]["operations"][producer]
                self.assertEqual(operation["source_error_codes"], expected_codes)
                self.assertEqual(evidence[0]["source_result"], expected_source_result)
                self.assertTrue(evidence[0]["attempted"])
                self.assertNotIn("source_errors", snapshot["result"])
                serialized = json.dumps(
                    {"snapshot": snapshot, "evidence": evidence}, ensure_ascii=False
                )
                self.assertNotIn(str(source_error["id"]), serialized)
                if message:
                    self.assertNotIn(message, serialized)
                    self.assertNotIn(
                        json.dumps(message, ensure_ascii=False)[1:-1], serialized
                    )
                if not expected_codes:
                    self.assertNotIn(code, serialized)

    def test_sanitizer_rejects_orphaned_direct_source_error(self) -> None:
        result = _built_failed_source_error_result(
            {
                "code": "missing_credential",
                "message": "Missing release credential.",
            }
        )
        producer = _reviewed_qualification_producers()[0]
        operation = result["coverage"]["shared_source_error_contract"]["operations"][
            producer
        ]
        self.assertEqual(
            operation["source_error_ids"],
            [result["source_errors"][0]["id"]],
        )
        operation["source_error_ids"] = []

        with self.assertRaisesRegex(ValueError, "source error IDs do not close"):
            release_probe.sanitize_public_result(
                request_profile="orphaned-direct-source-error",
                request_as_of="2026-08-12",
                result=result,
                verified_at="2026-08-12T09:30:00+08:00",
            )

    def test_sanitizer_rejects_builder_source_error_id_payload_drift(self) -> None:
        result = _built_failed_source_error_result(
            {
                "code": "rate_limited",
                "message": "Release source rate limited.",
            }
        )

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="builder-source-error-canonical",
            request_as_of="2026-08-12",
            result=result,
            verified_at="2026-08-12T09:30:00+08:00",
        )
        producer = _reviewed_qualification_producers()[0]
        safe_operation = snapshot["result"]["coverage"]["shared_source_error_contract"][
            "operations"
        ][producer]
        self.assertEqual(safe_operation["source_error_codes"], [])
        self.assertEqual(evidence[0]["source_result"], "unavailable")

        tampered = deepcopy(result)
        original_id = tampered["source_errors"][0]["id"]
        tampered["source_errors"][0]["code"] = "missing_credential"

        with self.assertRaisesRegex(
            ValueError, "source-error ID contradicts its canonical payload"
        ):
            release_probe.sanitize_public_result(
                request_profile="builder-source-error-tampered",
                request_as_of="2026-08-12",
                result=tampered,
                verified_at="2026-08-12T09:30:00+08:00",
            )
        self.assertEqual(tampered["source_errors"][0]["id"], original_id)

    def test_sanitizer_rejects_source_error_details_rejected_by_public_builder(
        self,
    ) -> None:
        details = {"contract_codes": ["19999999"]}
        with self.assertRaisesRegex(
            ValueError, "source error scope is invalid for this task"
        ):
            _built_failed_source_error_result(
                {
                    "code": "missing_credential",
                    "message": "Missing release credential.",
                    "details": details,
                }
            )

        result = _built_failed_source_error_result(
            {
                "code": "missing_credential",
                "message": "Missing release credential.",
            }
        )
        producer = _reviewed_qualification_producers()[0]
        source_error = _public_source_error(
            capability_id="shared_source_error_contract",
            operation_id=producer,
            code="missing_credential",
            message="Missing release credential.",
            details=details,
        )
        result["source_errors"] = [source_error]
        result["coverage"]["shared_source_error_contract"]["operations"][producer][
            "source_error_ids"
        ] = [source_error["id"]]

        try:
            snapshot, evidence = release_probe.sanitize_public_result(
                request_profile="wrong-task-contract-code-source-error",
                request_as_of="2026-08-12",
                result=result,
                verified_at="2026-08-12T09:30:00+08:00",
            )
        except ValueError as error:
            self.assertEqual(str(error), "source error scope is invalid for this task")
        else:
            operation = snapshot["result"]["coverage"]["shared_source_error_contract"][
                "operations"
            ][producer]
            self.assertEqual(operation["source_error_codes"], ["missing_credential"])
            self.assertEqual(evidence[0]["source_result"], "credential_blocked")
            self.fail("sanitizer accepted details rejected by the public builder")

    def test_sanitizer_matches_public_source_error_detail_owner_rules(self) -> None:
        from a_share_research.capital_contract import (
            CapitalQuery,
            CapitalSourceBatch,
            CapitalSourceFailure,
        )
        from a_share_research.etf_option_contract import OptionSourceFailure
        from a_share_research.market_signal_contract import (
            SignalCoverage,
            SignalSourceBatch,
            SignalSourceFailure,
        )

        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            build_capital_events_result,
            capital_request,
        )
        from tests.test_etf_options import (
            FixedOptionOperation,
            build_etf_options_result,
            complete_batch,
            contract,
            option_request,
        )
        from tests.test_market_signals import (
            FixedOperation,
            NoIdentityTransport,
            build_market_signals_result,
            signal_request,
        )

        class FailedCapitalOperation:
            operation_id = "failed_capital_scope@1"
            supported_data_types = frozenset({"market_dragon_tiger"})
            raw_fields = ("result.data[]", "TRADE_DATE", "NET_DEAL_AMT")

            def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                return CapitalSourceBatch(
                    operation_id=self.operation_id,
                    source_errors=(
                        CapitalSourceFailure(
                            self.operation_id,
                            "missing_credential",
                            "Missing release credential.",
                            {"data_type": "market_dragon_tiger"},
                        ),
                    ),
                    complete=False,
                )

        capital_result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [FailedCapitalOperation()],
            BluefocusIdentityTransport(),
        )
        market_result = build_market_signals_result(
            signal_request(["strong_stock_theme"]),
            [
                FixedOperation(
                    SignalSourceBatch(
                        operation_id="fixed_signal@1",
                        coverage={
                            "strong_stock_theme": SignalCoverage("indeterminate")
                        },
                        source_errors=(
                            SignalSourceFailure(
                                "fixed_signal@1",
                                "missing_credential",
                                "Missing release credential.",
                                {"signal_types": ["strong_stock_theme"]},
                            ),
                        ),
                    )
                )
            ],
            NoIdentityTransport(),
        )
        option_batch = complete_batch(
            contract("10000001", "call", "3.00", delta="0.55"),
            contract("10000002", "put", "3.00", delta="-0.45"),
        )
        option_result = build_etf_options_result(
            option_request(),
            [
                FixedOptionOperation(
                    replace(
                        option_batch,
                        source_errors=(
                            OptionSourceFailure(
                                "fixed_options@1",
                                "missing_credential",
                                "Missing release credential.",
                                {"contract_codes": ["10000001"]},
                            ),
                        ),
                    )
                )
            ],
        )
        correct_results = (
            ("data-type", capital_result, "data_type", "credential_blocked"),
            ("signal-types", market_result, "signal_types", "credential_blocked"),
            ("contract-codes", option_result, "contract_codes", "partial"),
        )
        producer = _reviewed_qualification_producers()[0]

        for name, result, detail_key, expected_source_result in correct_results:
            with self.subTest(name=name):
                source_error = next(
                    item
                    for item in result["source_errors"]
                    if detail_key in item.get("details", {})
                )
                canonical_payload = {
                    key: value for key, value in source_error.items() if key != "id"
                }
                self.assertEqual(
                    source_error["id"],
                    stable_result_id("source-error", canonical_payload),
                )
                snapshot, evidence = release_probe.sanitize_public_result(
                    request_profile=f"correct-{name}-source-error",
                    request_as_of=str(result["research_anchor"]["as_of"]),
                    result=result,
                    verified_at="2026-08-12T09:30:00+08:00",
                )
                capability_id = str(source_error["capability_id"])
                operation_id = str(source_error["operation_id"])
                operation = snapshot["result"]["coverage"][capability_id]["operations"][
                    operation_id
                ]
                self.assertEqual(
                    operation["source_error_codes"], ["missing_credential"]
                )
                operation_evidence = next(
                    item
                    for item in evidence
                    if item["producer_operation_id"] == operation_id
                )
                self.assertEqual(
                    operation_evidence["source_result"], expected_source_result
                )
                serialized = json.dumps(
                    {"snapshot": snapshot, "evidence": evidence}, ensure_ascii=False
                )
                self.assertNotIn(str(source_error["id"]), serialized)
                self.assertNotIn("Missing release credential.", serialized)
                details = source_error["details"]
                for private_value in details.values():
                    values = (
                        private_value
                        if isinstance(private_value, list)
                        else [private_value]
                    )
                    for value in values:
                        self.assertNotIn(str(value), serialized)

        scope_cases = (
            (
                "data-type",
                "capital_events",
                "capital_event_observations",
                {"data_type": "market_dragon_tiger"},
            ),
            (
                "signal-types",
                "market_signals",
                "market_signal_observations",
                {"signal_types": ["strong_stock_theme"]},
            ),
            (
                "contract-codes",
                "etf_options",
                "etf_option_contracts",
                {"contract_codes": ["10000001"]},
            ),
        )
        for name, task_type, capability_id, details in scope_cases:
            for mismatch, invalid_task_type, invalid_capability_id in (
                ("wrong-task", "release_source_error_test", capability_id),
                ("wrong-capability", task_type, "shared_source_error_contract"),
            ):
                with self.subTest(name=name, mismatch=mismatch):
                    invalid_error = _public_source_error(
                        capability_id=invalid_capability_id,
                        operation_id=producer,
                        code="missing_credential",
                        message="Missing release credential.",
                        details=details,
                    )
                    invalid_plan, invalid_coverage = _source_error_leaf(
                        producer_operation_id=producer,
                        execution_state="failed",
                        state="failed",
                        query_complete=None,
                        source_error_ids=[str(invalid_error["id"])],
                        task_type=invalid_task_type,
                        capability_id=invalid_capability_id,
                    )
                    invalid = {
                        "schema_version": "1.0",
                        "research_anchor": {
                            "as_of": "2026-08-12",
                            "timezone": "Asia/Shanghai",
                        },
                        "plan": invalid_plan,
                        "coverage": invalid_coverage,
                        "source_errors": [invalid_error],
                    }
                    with self.assertRaisesRegex(
                        ValueError, "source error scope is invalid for this task"
                    ):
                        release_probe.sanitize_public_result(
                            request_profile=f"{mismatch}-{name}-source-error",
                            request_as_of="2026-08-12",
                            result=invalid,
                            verified_at="2026-08-12T09:30:00+08:00",
                        )

    def test_workflow_leaf_enforces_source_error_detail_owner_scope(self) -> None:
        producer = _reviewed_qualification_producers()[0]
        details = {"contract_codes": ["19999999"]}

        def workflow_result(task_type: str, capability_id: str) -> dict[str, object]:
            source_error = _public_source_error(
                capability_id=capability_id,
                operation_id=producer,
                code="missing_credential",
                message="Missing release credential.",
                details=details,
            )
            leaf_plan, leaf_coverage = _source_error_leaf(
                producer_operation_id=producer,
                execution_state="failed",
                state="failed",
                query_complete=None,
                source_error_ids=[str(source_error["id"])],
                task_type=task_type,
                capability_id=capability_id,
            )
            return {
                "schema_version": "1.0",
                "research_anchor": {
                    "as_of": "2026-08-12",
                    "timezone": "Asia/Shanghai",
                },
                "plan": {
                    "id": "workflow-source-error-detail-scope",
                    "version": "1.0",
                    "task_type": "research_workflow",
                    "workflow_id": "source-error-detail-scope",
                    "steps": [
                        {
                            "step_id": "scoped-source-error",
                            "version": "1.0",
                            "task_type": task_type,
                            "criticality": "supporting",
                            "depends_on": [],
                        }
                    ],
                },
                "capability_registry": {
                    "steps": {
                        "scoped-source-error": {
                            "step_id": "scoped-source-error",
                            "version": "1.0",
                            "task_type": task_type,
                            "leaf_plan": leaf_plan,
                        }
                    }
                },
                "coverage": {"steps": {"scoped-source-error": {"leaf": leaf_coverage}}},
                "source_errors": [source_error],
            }

        valid = workflow_result("etf_options", "etf_option_contracts")
        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="workflow-correct-contract-code-source-error",
            request_as_of="2026-08-12",
            result=valid,
            verified_at="2026-08-12T09:30:00+08:00",
        )
        operation = snapshot["result"]["coverage"]["steps"]["scoped-source-error"][
            "leaf"
        ]["etf_option_contracts"]["operations"][producer]
        self.assertEqual(operation["source_error_codes"], ["missing_credential"])
        self.assertEqual(evidence[0]["source_result"], "credential_blocked")

        for name, task_type, capability_id in (
            ("wrong-task", "research_content", "etf_option_contracts"),
            ("wrong-capability", "etf_options", "shared_source_error_contract"),
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    ValueError, "source error scope is invalid for this task"
                ),
            ):
                release_probe.sanitize_public_result(
                    request_profile=f"workflow-{name}-source-error",
                    request_as_of="2026-08-12",
                    result=workflow_result(task_type, capability_id),
                    verified_at="2026-08-12T09:30:00+08:00",
                )

    def test_sanitizer_enforces_exact_source_error_schema_and_details(self) -> None:
        result = _built_failed_source_error_result(
            {
                "code": "missing_credential",
                "message": "Missing release credential.",
            }
        )

        for name, mutate, expected_error in (
            (
                "missing-message",
                lambda error: error.pop("message"),
                "strict result schema",
            ),
            (
                "extra-key",
                lambda error: error.__setitem__("raw_payload", "private"),
                "strict result schema",
            ),
            (
                "invalid-details",
                lambda error: error.__setitem__("details", {"raw_payload": "private"}),
                "scope material",
            ),
        ):
            with self.subTest(name=name):
                invalid = deepcopy(result)
                mutate(invalid["source_errors"][0])
                with self.assertRaisesRegex(ValueError, expected_error):
                    release_probe.sanitize_public_result(
                        request_profile=f"source-error-schema-{name}",
                        request_as_of="2026-08-12",
                        result=invalid,
                        verified_at="2026-08-12T09:30:00+08:00",
                    )

    def test_sanitizer_keeps_only_plan_contract_and_safe_operation_result(self) -> None:
        from a_share_research.operation_release_matrix import (
            load_release_probe_evidence,
        )

        producer_operation_id = _reviewed_qualification_producers()[0]
        capability_id = "capital_event_observations"
        source_error = _public_source_error(
            capability_id=capability_id,
            operation_id=producer_operation_id,
            code="missing_credential",
            message="Authorization: Bearer secret-value",
            details={"data_type": "full provider body"},
        )
        result = {
            "schema_version": "1.0",
            "status": "blocked",
            "research_anchor": {
                "as_of": "2026-08-12",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "plan-safe",
                "version": "1.0",
                "task_type": "capital_events",
                "capabilities": [capability_id],
                "operation_obligations": [
                    {
                        "capability_id": capability_id,
                        "operation_id": producer_operation_id,
                        "producer_operation_id": producer_operation_id,
                        "operation_kind": "external_source",
                        "source_role": "declared_per_observation",
                        "experimental": True,
                        "exchange": None,
                        "evidence_uses": [
                            {
                                "use": "research_content_material",
                                "role": "supporting",
                            }
                        ],
                    }
                ],
                "research_dependencies": [],
            },
            "coverage": {
                capability_id: {
                    "state": "failed",
                    "operations": {
                        producer_operation_id: {
                            "capability_id": capability_id,
                            "operation_id": producer_operation_id,
                            "execution_state": "failed",
                            "state": "failed",
                            "query_complete": None,
                            "observation_ids": [],
                            "source_error_ids": [source_error["id"]],
                            "field_dispositions": [
                                {
                                    "field": "document",
                                    "disposition": "normalized",
                                    "destination": "observations[].payload.content",
                                }
                            ],
                            "diagnostics": {"server_url": "https://10.0.0.1/private"},
                        }
                    },
                }
            },
            "source_errors": [source_error],
            "observations": [
                {
                    "locator": {"uri": "https://provider.example/private"},
                    "payload": "raw provider payload",
                }
            ],
        }

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="content-credentialed",
            request_as_of="2026-08-12",
            result=result,
            verified_at="2026-08-12T09:30:00+08:00",
        )

        serialized = json.dumps(
            {"snapshot": snapshot, "evidence": evidence}, ensure_ascii=False
        )
        self.assertEqual(set(snapshot), {"request_profile", "request_as_of", "result"})
        self.assertEqual(
            set(snapshot["result"]), {"schema_version", "plan", "coverage"}
        )
        operation = snapshot["result"]["coverage"][capability_id]["operations"][
            producer_operation_id
        ]
        self.assertEqual(
            set(operation),
            {
                "capability_id",
                "operation_id",
                "execution_state",
                "state",
                "query_complete",
                "source_error_codes",
                "field_dispositions",
            },
        )
        self.assertEqual(operation["source_error_codes"], ["missing_credential"])
        self.assertEqual(
            evidence,
            [
                {
                    "producer_operation_id": producer_operation_id,
                    "probe": "tests/release_probe_core_operations.py",
                    "probe_status": "passed",
                    "attempted": True,
                    "source_result": "credential_blocked",
                    "verified_at": "2026-08-12T09:30:00+08:00",
                }
            ],
        )
        rate_limited = deepcopy(result)
        rate_limited_error = _public_source_error(
            capability_id=capability_id,
            operation_id=producer_operation_id,
            code="rate_limited",
            message="Authorization: Bearer secret-value",
            details={"data_type": "full provider body"},
        )
        rate_limited["source_errors"] = [rate_limited_error]
        rate_limited["coverage"][capability_id]["operations"][producer_operation_id][
            "source_error_ids"
        ] = [rate_limited_error["id"]]
        rate_limited_snapshot, rate_limited_evidence = (
            release_probe.sanitize_public_result(
                request_profile="content-rate-limited",
                request_as_of="2026-08-12",
                result=rate_limited,
                verified_at="2026-08-12T09:30:00+08:00",
            )
        )
        self.assertIs(rate_limited_evidence[0]["attempted"], True)
        self.assertEqual(
            rate_limited_evidence[0]["source_result"],
            "unavailable",
        )
        rate_limited_operation = rate_limited_snapshot["result"]["coverage"][
            capability_id
        ]["operations"][producer_operation_id]
        self.assertEqual(rate_limited_operation["source_error_codes"], [])
        serialized = json.dumps(
            {
                "snapshot": snapshot,
                "evidence": evidence,
                "rate_limited_snapshot": rate_limited_snapshot,
                "rate_limited_evidence": rate_limited_evidence,
            },
            ensure_ascii=False,
        )
        for forbidden in (
            "secret-value",
            "Authorization",
            "10.0.0.1",
            "provider.example",
            "raw provider payload",
            "full provider body",
            str(source_error["id"]),
        ):
            self.assertNotIn(forbidden, serialized)

        installation_health = {
            "schema_version": "1.0",
            "status": "complete",
            "dependencies": [],
            "operations": [],
            "source_policy": {
                "source_accessibility": "not_checked",
                "credentials": "not_checked",
                "evidence_adoption": "not_checked",
            },
        }
        attempt = release_probe.create_release_probe_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:30:00+08:00",
            installation_health=installation_health,
            plan_snapshots=[snapshot],
            installation_loads=[
                {
                    "producer_operation_id": producer_operation_id,
                    "origin": "copied_install_plan",
                    "status": "complete",
                    "selector": "content-credentialed",
                }
            ],
            probe_samples=[
                {
                    key: value
                    for key, value in evidence[0].items()
                    if key != "verified_at"
                }
                | {"request_profile": "content-credentialed"}
            ],
        )
        report = release_probe.assemble_release_probe_evidence(
            (attempt,),
            candidate_sha=_RC_CANDIDATE_SHA,
            assembled_at="2026-08-12T09:30:00+08:00",
        )

        snapshots, installation_loads, _, live_probes = load_release_probe_evidence(
            report
        )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(installation_loads), 1)
        self.assertEqual(len(live_probes), 1)
        forged = deepcopy(report)
        forged["plan_snapshots"][0]["result"]["raw_payload"] = "secret"
        with self.assertRaisesRegex(ValueError, "sanitized direct release result"):
            load_release_probe_evidence(forged)
        nested_forgery = deepcopy(report)
        nested_forgery["plan_snapshots"][0]["result"]["coverage"][capability_id][
            "operations"
        ][producer_operation_id]["field_dispositions"][0]["raw_payload"] = "secret"
        with self.assertRaisesRegex(ValueError, "field disposition"):
            load_release_probe_evidence(nested_forgery)
        forged_health = deepcopy(report)
        forged_health["installation_health"]["raw_payload"] = "secret"
        with self.assertRaisesRegex(ValueError, "installation health"):
            load_release_probe_evidence(forged_health)

    def test_sanitizer_resolves_source_errors_by_each_operation_reference(self) -> None:
        producer_operation_id = _reviewed_qualification_producers()[0]
        credential_error = _credential_source_error(producer_operation_id)
        credential_error_id = str(credential_error["id"])
        states = (
            ("failed", "failed", "failed", None, [credential_error_id]),
            ("succeeded", "succeeded", "complete", True, []),
            ("not-requested", "not_requested", "not_requested", None, []),
        )
        steps: list[dict[str, object]] = []
        registry_steps: dict[str, object] = {}
        coverage_steps: dict[str, object] = {}
        for step_id, execution_state, state, query_complete, source_error_ids in states:
            leaf_plan, leaf_coverage = _source_error_leaf(
                producer_operation_id=producer_operation_id,
                execution_state=execution_state,
                state=state,
                query_complete=query_complete,
                source_error_ids=source_error_ids,
            )
            step = {
                "step_id": step_id,
                "version": "1.0",
                "task_type": "research_content",
                "criticality": "supporting",
                "depends_on": [],
            }
            steps.append(step)
            registry_steps[step_id] = {
                "step_id": step_id,
                "version": "1.0",
                "task_type": "research_content",
                "leaf_plan": leaf_plan,
            }
            coverage_steps[step_id] = {"leaf": leaf_coverage}
        result = {
            "schema_version": "1.0",
            "research_anchor": {
                "as_of": "2026-08-12",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "workflow-shared-source-error-owner",
                "version": "1.0",
                "task_type": "research_workflow",
                "workflow_id": "shared-source-error-owner",
                "steps": steps,
            },
            "capability_registry": {"steps": registry_steps},
            "coverage": {"steps": coverage_steps},
            "source_errors": [credential_error],
        }

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="workflow-shared-source-error-owner",
            request_as_of="2026-08-12",
            result=result,
            verified_at="2026-08-12T09:30:00+08:00",
        )

        safe_steps = snapshot["result"]["coverage"]["steps"]
        operations = [
            safe_steps[step_id]["leaf"]["shared_source_error_contract"]["operations"][
                producer_operation_id
            ]
            for step_id, *_ in states
        ]
        self.assertEqual(
            [operation["source_error_codes"] for operation in operations],
            [["missing_credential"], [], []],
        )
        self.assertEqual(
            [item["source_result"] for item in evidence],
            ["credential_blocked", "complete", "unavailable"],
        )
        self.assertEqual(
            [item["attempted"] for item in evidence],
            [True, True, False],
        )
        serialized = json.dumps(
            {"snapshot": snapshot, "evidence": evidence}, ensure_ascii=False
        )
        for forbidden in (
            credential_error_id,
            "workflow-secret",
            "Authorization",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_sanitizer_rejects_orphaned_workflow_source_error(self) -> None:
        producer_operation_id = _reviewed_qualification_producers()[0]
        credential_error = _credential_source_error(producer_operation_id)
        credential_error_id = str(credential_error["id"])
        steps: list[dict[str, object]] = []
        registry_steps: dict[str, object] = {}
        coverage_steps: dict[str, object] = {}
        for step_id in ("first-owner", "repeated-owner"):
            leaf_plan, leaf_coverage = _source_error_leaf(
                producer_operation_id=producer_operation_id,
                execution_state="failed",
                state="failed",
                query_complete=None,
                source_error_ids=[credential_error_id],
            )
            step = {
                "step_id": step_id,
                "version": "1.0",
                "task_type": "research_content",
                "criticality": "supporting",
                "depends_on": [],
            }
            steps.append(step)
            registry_steps[step_id] = {
                "step_id": step_id,
                "version": "1.0",
                "task_type": "research_content",
                "leaf_plan": leaf_plan,
            }
            coverage_steps[step_id] = {"leaf": leaf_coverage}
        result = {
            "schema_version": "1.0",
            "research_anchor": {
                "as_of": "2026-08-12",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "workflow-orphaned-source-error",
                "version": "1.0",
                "task_type": "research_workflow",
                "workflow_id": "orphaned-source-error",
                "steps": steps,
            },
            "capability_registry": {"steps": registry_steps},
            "coverage": {"steps": coverage_steps},
            "source_errors": [credential_error],
        }
        for covered in coverage_steps.values():
            operation = covered["leaf"]["shared_source_error_contract"]["operations"][
                producer_operation_id
            ]
            self.assertEqual(operation["source_error_ids"], [credential_error_id])
            operation["source_error_ids"] = []

        with self.assertRaisesRegex(ValueError, "source error IDs do not close"):
            release_probe.sanitize_public_result(
                request_profile="orphaned-workflow-source-error",
                request_as_of="2026-08-12",
                result=result,
                verified_at="2026-08-12T09:30:00+08:00",
            )

    def test_sanitizer_rejects_invalid_source_error_id_authority(self) -> None:
        producer_operation_id = _reviewed_qualification_producers()[0]

        def direct_result() -> dict[str, object]:
            credential_error = _credential_source_error(producer_operation_id)
            plan, coverage = _source_error_leaf(
                producer_operation_id=producer_operation_id,
                execution_state="failed",
                state="failed",
                query_complete=None,
                source_error_ids=[str(credential_error["id"])],
            )
            return {
                "schema_version": "1.0",
                "research_anchor": {
                    "as_of": "2026-08-12",
                    "timezone": "Asia/Shanghai",
                },
                "plan": plan,
                "coverage": coverage,
                "source_errors": [credential_error],
            }

        invalid_results: list[tuple[str, dict[str, object]]] = []
        unknown = direct_result()
        unknown["coverage"]["shared_source_error_contract"]["operations"][
            producer_operation_id
        ]["source_error_ids"] = ["unknown-error"]
        invalid_results.append(("unknown-reference", unknown))

        duplicate_reference = direct_result()
        duplicate_reference["coverage"]["shared_source_error_contract"]["operations"][
            producer_operation_id
        ]["source_error_ids"] = 2 * [duplicate_reference["source_errors"][0]["id"]]
        invalid_results.append(("duplicate-reference", duplicate_reference))

        foreign_owner = direct_result()
        foreign_error = _public_source_error(
            capability_id="foreign-capability",
            operation_id="foreign-source@1",
            code="missing_credential",
            message="Missing foreign credential.",
        )
        foreign_owner["source_errors"].append(foreign_error)
        foreign_owner["coverage"]["shared_source_error_contract"]["operations"][
            producer_operation_id
        ]["source_error_ids"] = [foreign_error["id"]]
        invalid_results.append(("foreign-owner", foreign_owner))

        duplicate_id = direct_result()
        duplicate_id["source_errors"].append(deepcopy(duplicate_id["source_errors"][0]))
        invalid_results.append(("duplicate-top-level-id", duplicate_id))

        for name, result in invalid_results:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "source error"),
            ):
                release_probe.sanitize_public_result(
                    request_profile=f"source-error-{name}",
                    request_as_of="2026-08-12",
                    result=result,
                    verified_at="2026-08-12T09:30:00+08:00",
                )

    def test_sanitizer_excludes_valuation_local_selection_from_producer_evidence(
        self,
    ) -> None:
        operation_id = "member-0::consensus_forecast_selection@1"
        result = {
            "schema_version": "1.0",
            "research_anchor": {
                "as_of": "2026-08-17",
                "timezone": "Asia/Shanghai",
            },
            "plan": {
                "id": "valuation-comparison-local-selection",
                "version": "1.0",
                "task_type": "valuation_compare",
                "capabilities": ["valuation_compare_metrics"],
                "operation_obligations": [
                    {
                        "capability_id": "valuation_compare_metrics",
                        "operation_id": operation_id,
                        "producer_operation_id": "consensus_forecast_selection@1",
                        "operation_kind": "deterministic_coordinator",
                        "source_role": "deterministic_selection",
                        "experimental": True,
                        "exchange": None,
                        "evidence_uses": [
                            {"use": "valuation_compare", "role": "supporting"}
                        ],
                    }
                ],
                "research_dependencies": [],
            },
            "coverage": {
                "valuation_compare_metrics": {
                    "operations": {
                        operation_id: {
                            "capability_id": "valuation_compare_metrics",
                            "operation_id": operation_id,
                            "execution_state": "failed",
                            "state": "failed",
                            "query_complete": None,
                            "field_dispositions": [
                                {
                                    "field": "forecast_years",
                                    "disposition": "normalized",
                                    "destination": "observations.payload.forecast_years",
                                }
                            ],
                        }
                    }
                }
            },
            "source_errors": [],
        }

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="valuation-comparison",
            request_as_of="2026-08-17",
            result=result,
            verified_at="2026-08-17T17:49:00+08:00",
        )

        self.assertEqual(evidence, [])
        obligation = snapshot["result"]["plan"]["operation_obligations"][0]
        self.assertEqual(obligation["operation_kind"], "deterministic_coordinator")
        forged = deepcopy(result)
        forged["plan"]["operation_obligations"][0]["operation_kind"] = "external_source"
        with self.assertRaisesRegex(
            ValueError, "operation kind contradicts source authority"
        ):
            release_probe.sanitize_public_result(
                request_profile="valuation-comparison",
                request_as_of="2026-08-17",
                result=forged,
                verified_at="2026-08-17T17:49:00+08:00",
            )

    def test_sanitizer_rejects_non_public_or_unowned_operation_results(self) -> None:
        with self.assertRaisesRegex(ValueError, "public result contract"):
            release_probe.sanitize_public_result(
                request_profile="broken",
                request_as_of="2026-08-12",
                result={"schema_version": "1.0", "plan": {}, "coverage": {}},
                verified_at="2026-08-12T09:30:00+08:00",
            )
