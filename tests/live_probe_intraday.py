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
_EXPECTED_SOURCE_OPERATIONS = {
    "tongdaxin_intraday_snapshot@1",
    "tencent_intraday_snapshot@1",
}
_KNOWN_FAILURE_CODES = {
    "ambiguous_amount_scope",
    "ambiguous_amount_unit",
    "ambiguous_volume_scope",
    "ambiguous_volume_unit",
    "ambiguous_zero_value",
    "empty_response",
    "experimental_intraday_sources",
    "inapplicable_session",
    "incompatible_observation_boundary",
    "incompatible_price_type",
    "incomplete_observation",
    "intraday_as_of_not_current",
    "intraday_baseline_incomplete",
    "intraday_cache_state_unknown",
    "intraday_core_price_mismatch",
    "intraday_freshness_not_satisfied",
    "intraday_morning_observation_not_last",
    "intraday_morning_observation_out_of_window",
    "intraday_non_trading_date",
    "intraday_observation_time_invalid",
    "intraday_observation_too_old",
    "intraday_price_type_mismatch",
    "intraday_security_mismatch",
    "intraday_session_mismatch",
    "intraday_session_not_applicable",
    "intraday_session_unknown",
    "intraday_source_pair_gap_exceeded",
    "intraday_source_pair_incompatible",
    "intraday_source_pair_incomplete",
    "intraday_source_role_mismatch",
    "intraday_suspension_ambiguous",
    "intraday_suspension_confirmation_mismatch",
    "intraday_suspension_confirmed",
    "intraday_suspension_no_trade_unconfirmed",
    "intraday_trading_date_mismatch",
    "intraday_trading_status_mismatch",
    "inconsistent_price_bar",
    "missing_optional_dependency",
    "operation_failure",
    "probe_process_failure",
    "probe_protocol_failure",
    "probe_timeout",
    "quote_daily_date_mismatch",
    "quote_daily_security_mismatch",
    "source_policy_not_satisfied",
    "trading_date_mismatch",
    "unknown_cache_state",
    "unknown_observation_boundary",
    "unknown_price_type",
    "unknown_schema",
    "unknown_trading_status",
    "upstream_http_error",
    "upstream_unavailable",
    "wrong_security_payload",
}
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


def sanitize_failure(value: object) -> dict[str, str]:
    """Reduce a runtime/provider diagnostic to a non-sensitive stable shape."""

    if isinstance(value, Mapping):
        raw_code = value.get("code")
        code = (
            raw_code
            if isinstance(raw_code, str) and raw_code in _KNOWN_FAILURE_CODES
            else "probe_failure"
        )
        raw_operation = value.get("source_operation")
        operation = (
            raw_operation
            if isinstance(raw_operation, str)
            and raw_operation in _EXPECTED_SOURCE_OPERATIONS
            else "unknown"
        )
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


def _nonempty_string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _diagnostic_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, Mapping) for item in value)
    )


def _has_observation_contract(value: Mapping[str, object]) -> bool:
    subject = value.get("subject")
    if not isinstance(subject, Mapping) or not isinstance(
        subject.get("security"), Mapping
    ):
        return False
    if not all(
        isinstance(value.get(field), str)
        for field in (
            "as_of",
            "trading_date",
            "session_state",
            "trading_status",
            "price_type",
        )
    ):
        return False
    source_operations = value.get("source_operations")
    if not _nonempty_string_list(source_operations):
        return False
    if (
        len(source_operations) != 2
        or set(source_operations) != _EXPECTED_SOURCE_OPERATIONS
    ):
        return False
    observation_times = value.get("observation_times")
    if not isinstance(observation_times, Mapping) or not observation_times:
        return False
    if not {"tongdaxin_baseline", "tencent_cross_check"}.issubset(observation_times):
        return False
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in observation_times.items()
    ):
        return False
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return False
    if not {
        "latest_price",
        "open",
        "high",
        "low",
    }.issubset(snapshot):
        return False
    return all(
        isinstance(value.get(field), list)
        for field in (
            "evidence",
            "conflicts",
            "source_errors",
            "limitations",
        )
    )


def _has_blocked_contract(value: Mapping[str, object]) -> bool:
    subjects = value.get("subjects")
    has_subject = (
        isinstance(subjects, list)
        and bool(subjects)
        and all(isinstance(item, Mapping) for item in subjects)
    )
    return (
        has_subject
        and isinstance(value.get("evidence"), list)
        and any(
            _diagnostic_list(value.get(field))
            for field in ("limitations", "source_errors", "conflicts")
        )
    )


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
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "1.0"
        or value.get("task_type") != "intraday_market_signal"
        or value.get("status") not in {"limited", "blocked"}
        or (value.get("status") == "limited" and not _has_observation_contract(value))
        or (value.get("status") == "blocked" and not _has_blocked_contract(value))
    ):
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
    return [
        value
        if isinstance(value, str) and value in _EXPECTED_SOURCE_OPERATIONS
        else "unknown"
        for value in values
    ]


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
    source_errors = result.get("source_errors")
    source_error_present = isinstance(source_errors, list) and bool(source_errors)
    status = result.get("status")
    source_operations = _source_identity(result)
    timing = _timing(result)
    snapshot = result.get("snapshot")
    normalized: dict[str, str] = {}
    if isinstance(snapshot, Mapping):
        for field in ("latest_price", "open", "high", "low"):
            item = snapshot.get(field)
            if isinstance(item, Mapping) and isinstance(item.get("value"), str):
                normalized[field] = item["value"]
    session = result.get("session_state")
    trading_status = result.get("trading_status")
    price_type = result.get("price_type")
    agreed = (
        status == "limited"
        and not conflict_present
        and not source_error_present
        and len(source_operations) == 2
        and set(source_operations) == _EXPECTED_SOURCE_OPERATIONS
        and {"tongdaxin_baseline", "tencent_cross_check"}.issubset(timing)
        and session
        in {"opening_auction", "continuous", "midday_break", "closing_auction"}
        and trading_status in {"traded", "auction"}
        and price_type in {"latest_traded", "indicative_auction"}
        and len(normalized) == 4
    )
    agreement: dict[str, object] = {
        "status": "agreed" if agreed else "not_established",
        "basis": "cross-source normalized CNY 0.01 tick",
    }
    if agreed:
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

    if (
        len(securities) != 2
        or not securities[0].startswith("SSE:")
        or not securities[1].startswith("SZSE:")
    ):
        raise ValueError("the intraday probe requires one SSE and one SZSE security")
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


def _exchange_security(value: str, expected_exchange: str) -> str:
    security = _security(value)
    if not security.startswith(f"{expected_exchange}:"):
        raise argparse.ArgumentTypeError(
            f"this probe argument requires a {expected_exchange} security"
        )
    code = security.split(":", 1)[1]
    prefixes = (
        ("600", "601", "603", "605", "688", "689")
        if expected_exchange == "SSE"
        else ("000", "001", "002", "003", "300", "301")
    )
    if not code.startswith(prefixes):
        raise argparse.ArgumentTypeError(
            f"this probe argument is not a supported {expected_exchange} A-share"
        )
    return security


def _sse_security(value: str) -> str:
    return _exchange_security(value, "SSE")


def _szse_security(value: str) -> str:
    return _exchange_security(value, "SZSE")


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
    parser.add_argument("--sse", type=_sse_security, default=DEFAULT_SSE)
    parser.add_argument("--szse", type=_szse_security, default=DEFAULT_SZSE)
    arguments = parser.parse_args(argv)
    report = run_probe(arguments.as_of, (arguments.sse, arguments.szse))
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
