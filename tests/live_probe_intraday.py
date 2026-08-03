#!/usr/bin/env python3
"""Maintainer-only intraday source probe; never run in ordinary CI.

The probe deliberately emits a small, dated observation report instead of the
provider payload.  It invokes the installed public ``run --request`` seam for
one SSE and one SZSE A-share, uses an ephemeral request/home directory, and
never accepts an output path or writes fixtures.
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
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = (
    REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts" / "entrypoint.py"
)
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
DEFAULT_SSE = "SSE:600519"
DEFAULT_SZSE = "SZSE:000001"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,160}$")

_SAFE_FAILURE_MESSAGES = {
    "missing_optional_dependency": "The capability-scoped source dependency is unavailable.",
    "source_policy_not_satisfied": "The explicit probe source policy was rejected.",
    "upstream_unavailable": "A required source operation was unavailable.",
    "upstream_http_error": "A required source operation returned an HTTP failure.",
    "unknown_schema": "A source response did not match its registered schema.",
    "empty_response": "A required source operation returned no observation.",
    "wrong_security_payload": "A source response identified another security.",
    "operation_failure": "A source operation failed without a safe diagnostic.",
    "probe_process_failure": "The public probe process did not complete.",
    "probe_protocol_failure": "The public probe returned an invalid JSON result.",
    "probe_timeout": "The public probe timed out before producing a result.",
}


def _safe_token(value: object, fallback: str) -> str:
    if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
        return value
    return fallback


def sanitize_failure(value: object) -> dict[str, str]:
    """Reduce a runtime/provider diagnostic to a non-sensitive stable shape."""

    if isinstance(value, Mapping):
        code = _safe_token(value.get("code"), "probe_failure")
        operation = _safe_token(value.get("source_operation"), "unknown")
    else:
        code = "probe_failure"
        operation = "unknown"
    return {
        "source_operation": operation,
        "code": code,
        "message": _SAFE_FAILURE_MESSAGES.get(
            code, "The live probe did not establish a usable source observation."
        ),
    }


def build_probe_request(security: str, as_of: str) -> dict[str, object]:
    """Build the same versioned request shape used by the public CLI."""

    return {
        "schema_version": "1.0",
        "task_type": "intraday_market_signal",
        "subjects": [{"security": security}],
        "as_of": as_of,
        "window": None,
        "parameters": {},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def _probe_environment(home: str) -> dict[str, str]:
    """Pass only non-sensitive process settings to the probe subprocess."""

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "HOME": home,
        "USERPROFILE": home,
    }
    return environment


def _run_public_request(request: Mapping[str, object]) -> tuple[int, str, str]:
    """Invoke ``entrypoint.py run --request`` with ephemeral input only."""

    with tempfile.TemporaryDirectory(prefix="a_share_intraday_probe_") as temporary:
        temporary_root = Path(temporary)
        request_path = temporary_root / "request.json"
        isolated_home = temporary_root / "home"
        isolated_home.mkdir()
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
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, "", "probe timeout"
        except OSError:
            return 127, "", "probe process failure"
    return completed.returncode, completed.stdout, completed.stderr


def _result_from_process(returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
    if returncode == 124:
        return {
            "status": "blocked",
            "_failures": [sanitize_failure({"code": "probe_timeout"})],
        }
    if returncode != 0:
        return {
            "status": "blocked",
            "_failures": [sanitize_failure({"code": "probe_process_failure"})],
        }
    try:
        value = json.loads(stdout)
    except (TypeError, ValueError):
        return {
            "status": "blocked",
            "_failures": [sanitize_failure({"code": "probe_protocol_failure"})],
        }
    if not isinstance(value, dict):
        return {
            "status": "blocked",
            "_failures": [sanitize_failure({"code": "probe_protocol_failure"})],
        }
    return value


def _failures(result: Mapping[str, object]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    embedded = result.get("_failures")
    if isinstance(embedded, list):
        failures.extend(sanitize_failure(item) for item in embedded)
    for field in ("source_errors", "conflicts", "limitations"):
        values = result.get(field)
        if isinstance(values, list):
            failures.extend(sanitize_failure(item) for item in values)
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for failure in failures:
        key = (failure["source_operation"], failure["code"])
        if key not in seen:
            seen.add(key)
            unique.append(failure)
    return unique


def _source_identity(result: Mapping[str, object]) -> list[str]:
    values = result.get("source_operations")
    if not isinstance(values, list):
        return []
    return [_safe_token(value, "unknown") for value in values]


def _timing(result: Mapping[str, object]) -> dict[str, str]:
    values = result.get("observation_times")
    if not isinstance(values, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in values.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _units(result: Mapping[str, object]) -> dict[str, str]:
    snapshot = result.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return {}
    units: dict[str, str] = {}
    for field, item in snapshot.items():
        if isinstance(field, str) and isinstance(item, Mapping):
            unit = item.get("unit")
            if isinstance(unit, str):
                units[field] = unit
    return units


def _price_agreement(result: Mapping[str, object]) -> dict[str, object]:
    conflicts = result.get("conflicts")
    conflict_present = isinstance(conflicts, list) and bool(conflicts)
    status = result.get("status")
    agreed = status in {"limited", "supported"} and not conflict_present
    agreement: dict[str, object] = {
        "status": "agreed" if agreed else "not_established",
        "basis": "cross-source normalized CNY 0.01 tick",
    }
    snapshot = result.get("snapshot")
    if agreed and isinstance(snapshot, Mapping):
        normalized: dict[str, str] = {}
        for field in ("latest_price", "open", "high", "low"):
            item = snapshot.get(field)
            if isinstance(item, Mapping) and isinstance(item.get("value"), str):
                normalized[field] = item["value"]
        if normalized:
            agreement["normalized_prices"] = normalized
    return agreement


def summarize_observation(
    security: str, as_of: str, result: Mapping[str, object]
) -> dict[str, object]:
    """Return only the dated, non-sensitive fields useful to a maintainer."""

    session = {
        "state": result.get("session_state"),
        "trading_status": result.get("trading_status"),
        "price_type": result.get("price_type"),
    }
    return {
        "subject": result.get(
            "subject",
            {
                "security": {
                    "exchange": security.split(":", 1)[0],
                    "code": security.split(":", 1)[1],
                }
            },
        ),
        "source_identity": _source_identity(result),
        "date": {
            "requested": as_of,
            "observed": result.get("trading_date"),
        },
        "timing": _timing(result),
        "session": session,
        "price_agreement": _price_agreement(result),
        "units": _units(result),
        "status": str(result.get("status", "blocked")),
        "failures": _failures(result),
    }


def run_probe(
    as_of: str, securities: tuple[str, str] = (DEFAULT_SSE, DEFAULT_SZSE)
) -> dict[str, object]:
    """Run one explicit observation for each required exchange."""

    observations: list[dict[str, object]] = []
    for security in securities:
        process_result = _run_public_request(build_probe_request(security, as_of))
        result = _result_from_process(*process_result)
        observations.append(summarize_observation(security, as_of, result))
    return {
        "schema_version": "1.0",
        "probe": "intraday_live_observation",
        "generated_at": datetime.now(CHINA_STANDARD_TIME).isoformat(),
        "as_of": as_of,
        "scope": {
            "task_type": "intraday_market_signal",
            "source_policy": "experimental operations explicitly allowed; credentials disabled",
            "securities": list(securities),
            "ordinary_ci": False,
            "provider_payloads_persisted": False,
        },
        "observations": observations,
    }


def _security(value: str) -> str:
    exchange, separator, code = value.partition(":")
    if (
        separator != ":"
        or exchange not in {"SSE", "SZSE"}
        or len(code) != 6
        or not code.isdigit()
    ):
        raise argparse.ArgumentTypeError(
            "security must be an SSE/SZSE canonical identifier such as SSE:600519"
        )
    return value


def _as_of(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("as-of must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("as-of must use YYYY-MM-DD")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Maintainer-only explicit intraday live probe (never CI)."
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        required=True,
        help="acknowledge live network access and non-production observation",
    )
    parser.add_argument("--as-of", type=_as_of, required=True)
    parser.add_argument("--sse", type=_security, default=DEFAULT_SSE)
    parser.add_argument("--szse", type=_security, default=DEFAULT_SZSE)
    arguments = parser.parse_args(argv)
    report = run_probe(arguments.as_of, (arguments.sse, arguments.szse))
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
