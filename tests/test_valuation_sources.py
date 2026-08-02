from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "valuation_research"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    TransportError,
)
from a_share_research.valuation_sources import (  # noqa: E402
    EastmoneyStockInfoOperation,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


class PrimaryUnavailableTransport:
    def __init__(self, *, spaced_name: bool = False) -> None:
        self.urls: list[str] = []
        self.spaced_name = spaced_name

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        if "push2delay.eastmoney.com" not in url:
            raise TransportError("upstream_unavailable", "temporary disconnect")
        body = Path(FIXTURES, "eastmoney_601138_stock_info.json").read_bytes()
        if self.spaced_name:
            body = body.replace("工业富联".encode(), "工 业 富 联".encode())
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=body,
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class TransientDisconnectTransport(PrimaryUnavailableTransport):
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        if not self.urls:
            self.urls.append(url)
            raise TransportError("upstream_unavailable", "temporary disconnect")
        return super().get(url, headers)


class EastmoneyStockInfoOperationTests(unittest.TestCase):
    def test_stock_information_uses_delayed_host_before_primary_fallback(self) -> None:
        transport = PrimaryUnavailableTransport()

        observation = EastmoneyStockInfoOperation().observe("SSE:601138", transport)

        self.assertEqual(observation.total_shares, "19844092284")
        self.assertEqual(len(transport.urls), 1)
        self.assertIn("push2delay.eastmoney.com", transport.urls[0])

    def test_transient_disconnect_uses_bounded_backoff_and_keeps_diagnostic(
        self,
    ) -> None:
        transport = TransientDisconnectTransport()
        clock = FakeClock()
        operation = EastmoneyStockInfoOperation(
            minimum_interval_seconds=1.0,
            retry_delays=(0.5, 1.5),
            clock=clock,
            sleeper=clock.sleep,
        )

        observation = operation.observe("SSE:601138", transport)

        self.assertEqual(len(transport.urls), 2)
        self.assertEqual(clock.sleeps, [0.5, 0.5])
        self.assertEqual(
            observation.degradations,
            (
                {
                    "source_operation": "eastmoney_stock_info@1",
                    "code": "upstream_unavailable",
                    "message": "temporary disconnect",
                    "attempt": "1",
                },
            ),
        )

    def test_stock_information_normalizes_provider_display_whitespace(self) -> None:
        observation = EastmoneyStockInfoOperation().observe(
            "SSE:601138", PrimaryUnavailableTransport(spaced_name=True)
        )

        self.assertEqual(observation.name, "工业富联")


if __name__ == "__main__":
    unittest.main()
