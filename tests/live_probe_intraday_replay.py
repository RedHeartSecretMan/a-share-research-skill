#!/usr/bin/env python3
"""Maintainer-only live probe for the experimental intraday replay adapter.

This probe is intentionally separate from ordinary tests.  It invokes the
public ``entrypoint.py run --request`` seam for one SSE and one SZSE security,
uses an ephemeral home, and emits only a small allow-listed diagnostic report.
It never writes provider responses, credentials, server addresses, or fixtures.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = (
    REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts" / "entrypoint.py"
)
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
DEFAULT_SSE = "SSE:600519"
DEFAULT_SZSE = "SZSE:000001"
_SECURITY_PREFIXES = {
    "SSE": ("600", "601", "603", "605", "688", "689"),
    "SZSE": ("000", "001", "002", "003", "300", "301"),
}
_SAFE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OPERATIONS = {
    "mootdx_intraday_replay@1",
    "exchange_intraday_replay_daily@1",
}
_CODES = {
    "missing_optional_dependency",
    "intraday_replay_source_contract_not_satisfied",
    "intraday_replay_source_failure",
    "completed_trading_calendar_unverified",
    "completed_trading_calendar_incomplete",
    "timestamp_semantics_unverified",
    "timestamp_timezone_unverified",
    "price_scale_unverified",
    "volume_unit_unverified",
    "amount_unit_unverified",
    "auction_semantics_unverified",
    "source_security_mismatch",
    "source_trading_date_mismatch",
    "source_row_after_research_boundary",
    "source_interval_after_retrieval",
    "replay_date_not_observed",
    "replay_date_outside_recent_20",
    "empty_response",
    "unknown_schema",
    "unknown_timestamp_schema",
    "upstream_unavailable",
    "daily_upstream_unavailable",
    "daily_amount_unavailable",
    "daily_boundary_source_unavailable",
    "daily_source_security_mismatch",
    "daily_source_trading_date_mismatch",
    "daily_operation_not_independent",
    "daily_internal_close_conflict",
    "probe_process_failure",
    "probe_protocol_failure",
    "probe_timeout",
}
_STATUSES = {"limited", "blocked"}
_COVERAGE_STATUSES = {"complete", "partial", "indeterminate", "not_adjudicated"}
_DAILY_STATUSES = {"cross_checked", "unavailable", "blocked", "suspended_observation"}


def build_probe_request(
    security: str, as_of: str, replay_date: str
) -> dict[str, object]:
    """Build the versioned request sent to the public CLI."""

    return {
        "schema_version": "1.0",
        "task_type": "intraday_replay",
        "subjects": [{"security": security}],
        "as_of": as_of,
        "window": {"observed_from": replay_date, "observed_to": replay_date},
        "parameters": {},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def _probe_environment(home: str) -> dict[str, str]:
    """Pass only non-sensitive settings to the isolated subprocess."""

    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "HOME": home,
        "USERPROFILE": home,
    }


def _run_public_request(request: Mapping[str, object]) -> tuple[int, str]:
    """Run one public request without returning stderr or provider payloads."""

    with tempfile.TemporaryDirectory(prefix="a_share_intraday_replay_probe_") as root:
        temporary_root = Path(root)
        isolated_home = temporary_root / "home"
        isolated_home.mkdir()
        request_path = temporary_root / "request.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ENTRYPOINT),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                env=_probe_environment(str(isolated_home)),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, ""
        except OSError:
            return 127, ""
    return completed.returncode, completed.stdout


def _safe_operation(value: object) -> str:
    return value if isinstance(value, str) and value in _OPERATIONS else "unknown"


def _safe_code(value: object) -> str:
    return value if isinstance(value, str) and value in _CODES else "probe_failure"


def _safe_failure(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {"source_operation": "unknown", "code": "probe_failure"}
    operation = value.get("source_operation", value.get("operation_id"))
    return {
        "source_operation": _safe_operation(operation),
        "code": _safe_code(value.get("code")),
    }


def _failures(result: Mapping[str, object]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for field in ("source_errors", "conflicts", "limitations"):
        values = result.get(field)
        if isinstance(values, list):
            failures.extend(_safe_failure(item) for item in values)
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for failure in failures:
        key = (failure["source_operation"], failure["code"])
        if key not in seen:
            seen.add(key)
            unique.append(failure)
    return unique


def _result_matches_request(
    value: Mapping[str, object], expected_security: str, expected_replay_date: str
) -> bool:
    subjects = value.get("subjects")
    if not isinstance(subjects, list) or len(subjects) != 1:
        return False
    subject = subjects[0]
    if not isinstance(subject, Mapping):
        return False
    security = subject.get("security")
    if isinstance(security, str):
        subject_security = security
    elif isinstance(security, Mapping):
        exchange = security.get("exchange")
        code = security.get("code")
        subject_security = f"{exchange}:{code}"
    else:
        return False
    if subject_security != expected_security:
        return False
    replay = value.get("replay")
    if not isinstance(replay, Mapping):
        return True
    return (
        replay.get("security") == expected_security
        and replay.get("trading_date") == expected_replay_date
    )


def _result_from_process(
    returncode: int,
    stdout: str,
    *,
    expected_security: str | None = None,
    expected_replay_date: str | None = None,
) -> dict[str, object]:
    """Validate the public result and reduce it to safe replay diagnostics."""

    if returncode == 124:
        return {
            "status": "blocked",
            "failures": [_safe_failure({"code": "probe_timeout"})],
        }
    if returncode != 0:
        return {
            "status": "blocked",
            "failures": [_safe_failure({"code": "probe_process_failure"})],
        }
    try:
        value = json.loads(stdout)
    except (TypeError, ValueError):
        return {
            "status": "blocked",
            "failures": [_safe_failure({"code": "probe_protocol_failure"})],
        }
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "1.0"
        or value.get("task_type") != "intraday_replay"
        or value.get("status") not in _STATUSES
        or (
            expected_security is not None
            and expected_replay_date is not None
            and not _result_matches_request(
                value, expected_security, expected_replay_date
            )
        )
    ):
        return {
            "status": "blocked",
            "failures": [_safe_failure({"code": "probe_protocol_failure"})],
        }
    replay = value.get("replay")
    coverage = value.get("coverage")
    daily = value.get("daily_boundary")
    source_operations = value.get("source_operations")
    safe_operations = (
        [
            _safe_operation(item.get("operation_id"))
            for item in source_operations
            if isinstance(item, Mapping)
        ]
        if isinstance(source_operations, list)
        else []
    )
    coverage_status = (
        coverage.get("status")
        if isinstance(coverage, Mapping)
        and coverage.get("status") in _COVERAGE_STATUSES
        else "not_adjudicated"
    )
    daily_status = (
        daily.get("status")
        if isinstance(daily, Mapping) and daily.get("status") in _DAILY_STATUSES
        else "unavailable"
    )
    record_count = replay.get("record_count") if isinstance(replay, Mapping) else 0
    auction_count = (
        replay.get("auction_result_count") if isinstance(replay, Mapping) else 0
    )
    return {
        "status": value["status"],
        "coverage_status": coverage_status,
        "daily_boundary_status": daily_status,
        "record_count": record_count
        if isinstance(record_count, int) and record_count >= 0
        else 0,
        "auction_result_count": auction_count
        if isinstance(auction_count, int) and auction_count >= 0
        else 0,
        "source_operations": safe_operations,
        "failures": _failures(value),
    }


def _security(value: str) -> str:
    exchange, separator, code = value.partition(":")
    if (
        separator != ":"
        or exchange not in _SECURITY_PREFIXES
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
        or not code.startswith(_SECURITY_PREFIXES[exchange])
    ):
        raise argparse.ArgumentTypeError(
            "security must be a canonical SSE/SZSE A-share such as SSE:600519"
        )
    return value


def _exchange_security(value: str, exchange: str) -> str:
    security = _security(value)
    if not security.startswith(f"{exchange}:"):
        raise argparse.ArgumentTypeError(
            f"this argument requires a {exchange} security"
        )
    return security


def _sse_security(value: str) -> str:
    return _exchange_security(value, "SSE")


def _szse_security(value: str) -> str:
    return _exchange_security(value, "SZSE")


def _date(value: str, field: str) -> str:
    if not _SAFE_DATE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{field} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{field} must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError(f"{field} must use YYYY-MM-DD")
    return value


def run_probe(
    as_of: str,
    replay_date: str,
    securities: tuple[str, str] = (DEFAULT_SSE, DEFAULT_SZSE),
) -> dict[str, object]:
    """Run one explicit, isolated replay request for each required exchange."""

    if (
        len(securities) != 2
        or not securities[0].startswith("SSE:")
        or not securities[1].startswith("SZSE:")
    ):
        raise ValueError("the replay probe requires one SSE and one SZSE security")
    observations: list[dict[str, object]] = []
    for security in securities:
        request = build_probe_request(security, as_of, replay_date)
        returncode, stdout = _run_public_request(request)
        observations.append(
            {
                "security": security,
                "as_of": as_of,
                "replay_date": replay_date,
                **_result_from_process(
                    returncode,
                    stdout,
                    expected_security=security,
                    expected_replay_date=replay_date,
                ),
            }
        )
    return {
        "schema_version": "1.0",
        "probe": "intraday_replay_live_observation",
        "generated_at": datetime.now(CHINA_STANDARD_TIME).isoformat(),
        "as_of": as_of,
        "replay_date": replay_date,
        "scope": {
            "task_type": "intraday_replay",
            "source_policy": "experimental operations explicitly allowed; credentials disabled",
            "securities": list(securities),
            "ordinary_ci": False,
            "home_is_ephemeral": True,
            "provider_payloads_persisted": False,
        },
        "observations": observations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Maintainer-only explicit intraday replay live probe (never CI)."
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        required=True,
        help="acknowledge live network access and experimental-only qualification",
    )
    parser.add_argument(
        "--as-of", type=lambda value: _date(value, "as-of"), required=True
    )
    parser.add_argument(
        "--replay-date",
        type=lambda value: _date(value, "replay-date"),
        required=True,
    )
    parser.add_argument("--sse", type=_sse_security, default=DEFAULT_SSE)
    parser.add_argument("--szse", type=_szse_security, default=DEFAULT_SZSE)
    arguments = parser.parse_args(argv)
    report = run_probe(
        arguments.as_of,
        arguments.replay_date,
        (arguments.sse, arguments.szse),
    )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
