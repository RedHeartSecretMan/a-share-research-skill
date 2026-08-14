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
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

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


def _mark_external_fixture_applications_failed(result: dict[str, object]) -> None:
    """Turn a fixed no-I/O public sample into attempted failure evidence."""

    def mark(plan: dict[str, object], coverage: dict[str, object]) -> None:
        for obligation in plan["operation_obligations"]:
            if obligation["operation_kind"] != "external_source":
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
        "dependencies": [],
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
            / "core-v0.3.0-operation-qualification.json"
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
) -> dict[str, object]:
    """Build one bounded attempt fixture from the reviewed literal denominator."""

    producers = _reviewed_qualification_producers()
    capability_id = "synthetic_release_contract"
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
    ]
    operations = {
        producer: {
            "capability_id": capability_id,
            "operation_id": producer,
            "execution_state": (
                "failed" if producer in attempted_producers else "not_requested"
            ),
            "state": "failed" if producer in attempted_producers else "not_requested",
            "query_complete": None,
            "field_dispositions": [
                {
                    "field": "payload",
                    "disposition": "normalized",
                    "destination": "observations[].payload",
                }
            ],
        }
        for producer in producers
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
                "result": {
                    "schema_version": "1.0",
                    "plan": {
                        "id": "synthetic-release-plan",
                        "version": "1.0",
                        "task_type": "synthetic_release",
                        "capabilities": [capability_id],
                        "operation_obligations": obligations,
                        "research_dependencies": [],
                    },
                    "coverage": {capability_id: {"operations": operations}},
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
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
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
        producers = _reviewed_qualification_producers()
        prior_attempted = set(producers[:22])
        current_attempted = set(producers[22:])
        prior = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at="2026-08-12T09:35:00+08:00",
            attempted_producers=prior_attempted,
        )
        current = _synthetic_attempt(
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
            self.assertEqual(len(final["live_probes"]), 44)
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
            second["probe_samples"][0]["source_result"] = "rate_limited"
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

    def test_attempt_digest_covers_samples_and_sanitized_plan_contracts(self) -> None:
        attempt = _synthetic_attempt(
            candidate_sha=_RC_CANDIDATE_SHA,
            verified_at=_RC_VERIFIED_AT,
            attempted_producers=set(_reviewed_qualification_producers()),
        )
        mutations = (
            lambda value: value["probe_samples"][0].__setitem__(
                "source_result", "failed"
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
        second["probe_samples"][0]["source_result"] = "rate_limited"
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
        skipped["plan_snapshots"][0]["request_profile"] = "skipped-profile"
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
            self.assertEqual(len(failure_report["external_producer_operation_ids"]), 44)
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

    def test_all_profiles_use_public_calls_and_close_44_producer_evidence(
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

        def execute(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if Path(command[-1]).name == "install.py":
                return subprocess.CompletedProcess(command, 0, json.dumps(health), "")
            request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            sent_requests.append(deepcopy(request))
            request["source_policy"]["allow_experimental"] = False
            public = research(
                request,
                identity_transport=_NoIoHttpTransport(),
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
        self.assertEqual(len(report["live_probes"]), 44)
        self.assertEqual(len(report["installation_loads"]), 44)
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
        }
        plan_derived_producers = set(
            formal_producer_operation_ids(
                tuple(
                    ReleasePlanSnapshot(
                        result=item["result"],
                        request_profile=item["request_profile"],
                    )
                    for item in report["plan_snapshots"]
                )
            )
        )
        self.assertEqual(len(default_producer_authorities), 44)
        self.assertEqual(plan_derived_producers, default_producer_authorities)
        qualification_payload = json.loads(
            (
                REPOSITORY_ROOT
                / "docs"
                / "research"
                / "core-v0.3.0-operation-qualification.json"
            ).read_text(encoding="utf-8")
        )
        qualification = load_operation_qualification_manifest(
            qualification_payload,
            expected_producer_operation_ids=tuple(plan_derived_producers),
        )
        self.assertEqual(len(qualification), 44)

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
                        "version": "1.0",
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
        install_command, _ = calls[0]
        run_command, _ = calls[1]
        self.assertEqual(install_command[:2], [sys.executable, "-I"])
        self.assertEqual(Path(install_command[-1]).name, "install.py")
        self.assertNotEqual(
            Path(install_command[-1]).resolve(),
            (SKILL_ROOT / "scripts" / "install.py").resolve(),
        )
        self.assertEqual(run_command[:2], [sys.executable, "-I"])
        self.assertEqual(Path(run_command[2]).name, "entrypoint.py")
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

    def test_sanitizer_keeps_only_plan_contract_and_safe_operation_result(self) -> None:
        from a_share_research.operation_release_matrix import (
            load_release_probe_evidence,
        )

        producer_operation_id = "credentialed_source@1"
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
                "task_type": "research_content",
                "capabilities": ["research_content_materials"],
                "operation_obligations": [
                    {
                        "capability_id": "research_content_materials",
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
                "research_content_materials": {
                    "state": "failed",
                    "operations": {
                        producer_operation_id: {
                            "capability_id": "research_content_materials",
                            "operation_id": producer_operation_id,
                            "execution_state": "failed",
                            "state": "failed",
                            "query_complete": None,
                            "observation_ids": [],
                            "source_error_ids": ["source-error-secret"],
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
            "source_errors": [
                {
                    "id": "source-error-secret",
                    "capability_id": "research_content_materials",
                    "operation_id": producer_operation_id,
                    "code": "missing_credential",
                    "message": "Authorization: Bearer secret-value",
                    "details": {"raw_payload": "full provider body"},
                }
            ],
            "observations": [
                {
                    "locator": {"uri": "https://provider.example/private"},
                    "payload": "raw provider payload",
                }
            ],
        }

        snapshot, evidence = release_probe.sanitize_public_result(
            request_profile="content-credentialed",
            result=result,
            verified_at="2026-08-12T09:30:00+08:00",
        )

        serialized = json.dumps(
            {"snapshot": snapshot, "evidence": evidence}, ensure_ascii=False
        )
        self.assertEqual(set(snapshot), {"request_profile", "result"})
        self.assertEqual(
            set(snapshot["result"]), {"schema_version", "plan", "coverage"}
        )
        operation = snapshot["result"]["coverage"]["research_content_materials"][
            "operations"
        ][producer_operation_id]
        self.assertEqual(
            set(operation),
            {
                "capability_id",
                "operation_id",
                "execution_state",
                "state",
                "query_complete",
                "field_dispositions",
            },
        )
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
        for forbidden in (
            "secret-value",
            "Authorization",
            "10.0.0.1",
            "provider.example",
            "raw provider payload",
            "full provider body",
            "source-error-secret",
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
        nested_forgery["plan_snapshots"][0]["result"]["coverage"][
            "research_content_materials"
        ]["operations"][producer_operation_id]["field_dispositions"][0][
            "raw_payload"
        ] = "secret"
        with self.assertRaisesRegex(ValueError, "field disposition"):
            load_release_probe_evidence(nested_forgery)
        forged_health = deepcopy(report)
        forged_health["installation_health"]["raw_payload"] = "secret"
        with self.assertRaisesRegex(ValueError, "installation health"):
            load_release_probe_evidence(forged_health)

    def test_sanitizer_rejects_non_public_or_unowned_operation_results(self) -> None:
        with self.assertRaisesRegex(ValueError, "public result contract"):
            release_probe.sanitize_public_result(
                request_profile="broken",
                result={"schema_version": "1.0", "plan": {}, "coverage": {}},
                verified_at="2026-08-12T09:30:00+08:00",
            )
