"""Command-line protocol for A-share research."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Collection, NoReturn, Sequence

from .bundle_validation import JsonNumberToken, build_bundle_validation_result
from .capital_contract import CapitalHttpTransport, CapitalSourceOperation
from .close_observation import build_close_result
from .content_contract import ContentHttpTransport, ContentSourceOperation
from .etf_option_contract import OptionSourceOperation
from .identity_resolution import resolve_security_identity
from .identity_sources import HttpTransport, UrlLibTransport
from .intraday_contract import IntradaySourceOperation
from .market_signal_contract import (
    MarketSignalHttpTransport,
    MarketSignalSourceOperation,
)
from .provided_evidence import build_provided_evidence_result
from .research_runtime import research
from .valuation import build_valuation_result


def _explicit_research_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "research date must use explicit YYYY-MM-DD format"
        ) from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError(
            "research date must use explicit YYYY-MM-DD format"
        )
    return value


def _canonical_security(value: str) -> str:
    exchange, separator, code = value.partition(":")
    if (
        separator != ":"
        or exchange not in {"SSE", "SZSE"}
        or len(code) != 6
        or not code.isascii()
        or not code.isdigit()
    ):
        raise argparse.ArgumentTypeError(
            "security must be an SSE/SZSE canonical identifier such as SSE:600519"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a_share_research")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--output", type=Path)

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--query", required=True)
    resolve.add_argument(
        "--as-of",
        type=_explicit_research_date,
        required=True,
        help="explicit research date in China Standard Time (YYYY-MM-DD)",
    )
    resolve.add_argument("--output", type=Path)

    close = subparsers.add_parser("close")
    close.add_argument("--security", type=_canonical_security, required=True)
    close.add_argument(
        "--as-of",
        type=_explicit_research_date,
        required=True,
        help="explicit research date in China Standard Time (YYYY-MM-DD)",
    )
    close.add_argument("--output", type=Path)

    validation = subparsers.add_parser("validate-bundle")
    validation.add_argument("--bundle", type=Path, required=True)

    valuation = subparsers.add_parser("valuation")
    valuation.add_argument("--bundle", type=Path, required=True)
    valuation.add_argument(
        "--as-of",
        type=_explicit_research_date,
        required=True,
        help="explicit research date in China Standard Time (YYYY-MM-DD)",
    )
    valuation.add_argument("--output", type=Path)
    return parser


def _load_manifest(
    bundle: Path, *, preserve_json_numbers: bool = False
) -> dict[str, Any]:
    parse_number = JsonNumberToken if preserve_json_numbers else _reject_json_number
    with (bundle / "manifest.json").open(encoding="utf-8") as manifest_file:
        loaded = json.load(
            manifest_file,
            parse_constant=parse_number,
            parse_float=parse_number,
            parse_int=parse_number,
        )
    if not isinstance(loaded, dict):
        raise ValueError("bundle manifest must be a JSON object")
    return loaded


def _reject_json_number(_: str) -> NoReturn:
    raise ValueError("JSON numbers must be decimal strings with explicit units")


def _load_research_request(request_path: Path) -> dict[str, Any]:
    with request_path.open(encoding="utf-8") as request_file:
        loaded = json.load(request_file)
    if not isinstance(loaded, dict):
        raise ValueError("research request must be a JSON object")
    return loaded


def main(
    argv: Sequence[str] | None = None,
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
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "run":
            result = research(
                _load_research_request(arguments.request),
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
            )
        elif arguments.command == "resolve":
            result = resolve_security_identity(
                arguments.query,
                arguments.as_of,
                identity_transport or UrlLibTransport(),
            )
        elif arguments.command == "close":
            result = build_close_result(
                arguments.security,
                arguments.as_of,
                identity_transport or UrlLibTransport(),
                research_now,
            )
        else:
            manifest = _load_manifest(
                arguments.bundle,
                preserve_json_numbers=arguments.command == "validate-bundle",
            )
            if arguments.command == "validate-bundle":
                result = build_bundle_validation_result(manifest, arguments.bundle)
            elif manifest.get("question") == "current_valuation":
                result = build_valuation_result(
                    manifest,
                    arguments.bundle,
                    arguments.as_of,
                )
            else:
                result = build_provided_evidence_result(manifest, arguments.as_of)
    except OSError as error:
        input_name = (
            "research request" if arguments.command == "run" else "bundle manifest"
        )
        print(f"error: cannot read {input_name}: {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        protocol_name = (
            "research task" if arguments.command == "run" else "bundle protocol"
        )
        print(f"error: invalid {protocol_name}: {error}", file=sys.stderr)
        return 2
    serialized = json.dumps(
        result,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    output = getattr(arguments, "output", None)
    if output is not None:
        try:
            output.write_text(f"{serialized}\n", encoding="utf-8")
        except OSError as error:
            print(f"error: cannot write result: {error}", file=sys.stderr)
    print(serialized)
    return 0
