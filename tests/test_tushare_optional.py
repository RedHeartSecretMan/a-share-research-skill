from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.tushare_optional import (  # noqa: E402
    TushareOptionalProbe,
)

NOW = datetime.now(timezone.utc)


class FakeTransport:
    def __init__(self, body: dict[str, object], status: int = 200) -> None:
        self.body = json.dumps(body).encode()
        self.status = status
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        self.calls.append((url, headers, body))
        return HttpResponse(self.status, "application/json", self.body, NOW)


class TushareOptionalTests(unittest.TestCase):
    def test_missing_token_is_disabled_without_network_call(self) -> None:
        transport = FakeTransport({"code": 0, "data": {"items": []}})
        result = TushareOptionalProbe(transport=transport, environ={}).probe()
        self.assertEqual(result.status, "disabled")
        self.assertFalse(result.token_configured)
        self.assertEqual(result.to_public_dict()["token_source"], "none")
        self.assertEqual(transport.calls, [])

    def test_provider_permission_error_is_redacted(self) -> None:
        transport = FakeTransport({"code": 40101, "data": None})
        result = TushareOptionalProbe(
            transport=transport,
            environ={"TUSHARE_TOKEN": "secret-value"},
        ).probe()
        self.assertEqual(result.status, "credential_blocked")
        self.assertEqual(result.provider_code, 40101)
        self.assertNotIn("secret-value", json.dumps(result.to_public_dict()))
        self.assertIn(b"secret-value", transport.calls[0][2])
        self.assertIn(b'"api_name":"daily"', transport.calls[0][2])

    def test_success_remains_optional_and_redacted(self) -> None:
        transport = FakeTransport(
            {"code": 0, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}}
        )
        result = TushareOptionalProbe(
            transport=transport,
            environ={"TUSHARE_TOKEN": "secret-value"},
        ).probe()
        self.assertEqual(result.status, "available")
        self.assertEqual(result.row_count, 1)
        self.assertIn(b'"api_name":"daily"', transport.calls[0][2])
        public = result.to_public_dict()
        self.assertEqual(public["public_state"], "blocked")
        self.assertNotIn("secret-value", json.dumps(public))
