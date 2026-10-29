"""Optional Tushare credential and capability detection.

This module deliberately does not register a public workflow or persist a
token.  A caller opts in by providing ``TUSHARE_TOKEN`` in the process
environment.  The public projection contains only redacted status and stable
failure codes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import urlsplit

from .identity_sources import HttpResponse, TransportError

TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"
TUSHARE_ENDPOINT_ENV = "TUSHARE_API_URL"
TUSHARE_API_NAME_ENV = "TUSHARE_API_NAME"
DEFAULT_TUSHARE_ENDPOINT = "https://api.tushare.pro"
DEFAULT_TUSHARE_API_NAME = "daily"
_SUPPORTED_PROBES = {"daily", "new_share", "daily_basic", "stock_basic"}


class TushareOptionalError(ValueError):
    """A caller supplied an invalid optional Tushare configuration."""


class TusharePostTransport(Protocol):
    """The injectable network boundary used by the optional probe."""

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class TushareProbeResult:
    """Redacted status for one opt-in Tushare capability probe."""

    operation_id: str
    status: Literal["disabled", "available", "credential_blocked", "unavailable"]
    token_configured: bool
    token_source: Literal["environment", "none"]
    endpoint: str
    provider_code: int | None
    row_count: int | None
    limitation_codes: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        """Return a safe projection that cannot contain the credential."""

        return {
            "operation_id": self.operation_id,
            "capability_kind": "optional",
            "public_state": "blocked",
            "status": self.status,
            "token_configured": self.token_configured,
            "token_source": self.token_source,
            "endpoint_origin": urlsplit(self.endpoint).netloc,
            "provider_code": self.provider_code,
            "row_count": self.row_count,
            "limitation_codes": list(self.limitation_codes),
        }


class TushareOptionalProbe:
    """Probe one Tushare operation without making it a product baseline."""

    operation_id = "tushare_optional_capability_probe@1"

    def __init__(
        self,
        *,
        transport: TusharePostTransport,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._environ = os.environ if environ is None else environ

    def probe(self) -> TushareProbeResult:
        token = self._environ.get(TUSHARE_TOKEN_ENV, "").strip()
        endpoint = self._environ.get(
            TUSHARE_ENDPOINT_ENV, DEFAULT_TUSHARE_ENDPOINT
        ).strip()
        api_name = self._environ.get(
            TUSHARE_API_NAME_ENV, DEFAULT_TUSHARE_API_NAME
        ).strip()
        if not endpoint or urlsplit(endpoint).scheme != "https":
            raise TushareOptionalError("Tushare endpoint must be an HTTPS origin.")
        if api_name not in _SUPPORTED_PROBES:
            raise TushareOptionalError("Tushare probe API is unsupported.")
        if not token:
            return TushareProbeResult(
                self.operation_id,
                "disabled",
                False,
                "none",
                endpoint,
                None,
                None,
                ("missing_optional_credential",),
            )

        probe = {
            "daily": (
                {"ts_code": "000001.SZ", "trade_date": "20261028"},
                "ts_code,trade_date,close,vol,amount",
            ),
            "new_share": ({}, "ts_code,name,ipo_date,issue_date"),
            "daily_basic": (
                {"trade_date": "20261028"},
                "ts_code,trade_date,pe_ttm,pb,turnover_rate",
            ),
            "stock_basic": (
                {"exchange": "BSE", "list_status": "L"},
                "ts_code,symbol,name,exchange,list_date",
            ),
        }[api_name]
        payload = {
            "api_name": api_name,
            "token": token,
            "params": probe[0],
            "fields": probe[1],
        }
        try:
            response = self._transport.post(
                endpoint,
                {"Content-Type": "application/json"},
                json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            )
        except TransportError:
            return TushareProbeResult(
                self.operation_id,
                "unavailable",
                True,
                "environment",
                endpoint,
                None,
                None,
                ("provider_unavailable",),
            )
        if response.status in {401, 403}:
            return TushareProbeResult(
                self.operation_id,
                "credential_blocked",
                True,
                "environment",
                endpoint,
                None,
                None,
                ("provider_permission_denied",),
            )
        try:
            value = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return TushareProbeResult(
                self.operation_id,
                "unavailable",
                True,
                "environment",
                endpoint,
                None,
                None,
                ("provider_schema_unknown",),
            )
        if not isinstance(value, dict) or type(value.get("code")) is not int:
            return TushareProbeResult(
                self.operation_id,
                "unavailable",
                True,
                "environment",
                endpoint,
                None,
                None,
                ("provider_schema_unknown",),
            )
        provider_code = value["code"]
        data = value.get("data")
        if provider_code != 0:
            return TushareProbeResult(
                self.operation_id,
                "credential_blocked"
                if provider_code in {40101, 2002}
                else "unavailable",
                True,
                "environment",
                endpoint,
                provider_code,
                None,
                ("provider_permission_denied",)
                if provider_code in {40101, 2002}
                else ("provider_error",),
            )
        rows: Any = data.get("items") if isinstance(data, dict) else None
        row_count = len(rows) if isinstance(rows, list) else None
        if row_count is None:
            return TushareProbeResult(
                self.operation_id,
                "unavailable",
                True,
                "environment",
                endpoint,
                provider_code,
                None,
                ("provider_schema_unknown",),
            )
        return TushareProbeResult(
            self.operation_id,
            "available",
            True,
            "environment",
            endpoint,
            provider_code,
            row_count,
            ("optional_provider_not_public_baseline",),
        )
