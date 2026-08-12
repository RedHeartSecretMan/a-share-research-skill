"""Shared fail-closed bounds for public normalized JSON material."""

from __future__ import annotations

import math
from typing import Mapping

MAX_PUBLIC_DATA_DEPTH = 32
MAX_PUBLIC_DATA_ITEMS = 10_000
MAX_PUBLIC_DATA_KEY_LENGTH = 128
MAX_PUBLIC_DATA_STRING_LENGTH = 64 * 1024

_FORBIDDEN_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "authorization_header",
        "callable",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "header",
        "headers",
        "api_key",
        "apikey",
        "password",
        "passwords",
        "provider_raw_payload",
        "raw_payload",
        "refresh_token",
        "secret",
        "secrets",
        "token",
        "transport",
    }
)
_FORBIDDEN_KEY_PREFIXES = (
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "credential",
    "header",
    "password",
    "secret",
    "token",
)


def validate_bounded_public_data(
    value: object,
    *,
    artifact: str,
    allow_floats: bool = True,
    allow_excluded_raw_marker: bool = False,
    require_ascii_keys: bool = True,
    max_total_string_length: int | None = None,
) -> None:
    """Validate one JSON value using the worker-wire structural/security policy."""

    item_count = 0
    total_string_length = 0

    def visit(item: object, depth: int) -> None:
        nonlocal item_count, total_string_length
        item_count += 1
        if item_count > MAX_PUBLIC_DATA_ITEMS or depth > MAX_PUBLIC_DATA_DEPTH:
            raise ValueError(f"{artifact} exceeds its structural bounds")
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or require_ascii_keys
                    and not key.isascii()
                    or len(key) > MAX_PUBLIC_DATA_KEY_LENGTH
                ):
                    raise ValueError(f"{artifact} key is invalid")
                normalized = (
                    key.casefold().replace("-", "_").replace(".", "_").replace(" ", "_")
                )
                if (
                    key.startswith("_")
                    or normalized in _FORBIDDEN_KEYS
                    and not (
                        allow_excluded_raw_marker
                        and normalized == "raw_payload"
                        and nested == "excluded"
                    )
                    or any(
                        normalized.startswith(f"{prefix}_")
                        for prefix in _FORBIDDEN_KEY_PREFIXES
                    )
                ):
                    raise ValueError(
                        f"{artifact} contains a private or sensitive field"
                    )
                total_string_length += len(key)
                visit(nested, depth + 1)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested, depth + 1)
        elif isinstance(item, str):
            if len(item) > MAX_PUBLIC_DATA_STRING_LENGTH:
                raise ValueError(f"{artifact} string exceeds its bound")
            total_string_length += len(item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{artifact} number must be finite")
            if not allow_floats:
                raise ValueError(f"{artifact} does not accept JSON floats")
        elif item is not None and not isinstance(item, (bool, int)):
            raise ValueError(f"{artifact} contains a non-JSON value")
        if (
            max_total_string_length is not None
            and total_string_length > max_total_string_length
        ):
            raise ValueError(f"{artifact} exceeds its total string bound")

    visit(value, 0)


__all__ = [
    "MAX_PUBLIC_DATA_DEPTH",
    "MAX_PUBLIC_DATA_ITEMS",
    "MAX_PUBLIC_DATA_KEY_LENGTH",
    "MAX_PUBLIC_DATA_STRING_LENGTH",
    "validate_bounded_public_data",
]
