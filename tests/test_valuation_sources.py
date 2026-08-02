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
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        if "push2delay.eastmoney.com" not in url:
            raise TransportError("upstream_unavailable", "temporary disconnect")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=Path(FIXTURES, "eastmoney_601138_stock_info.json").read_bytes(),
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class EastmoneyStockInfoOperationTests(unittest.TestCase):
    def test_stock_information_uses_delayed_host_before_primary_fallback(self) -> None:
        transport = PrimaryUnavailableTransport()

        observation = EastmoneyStockInfoOperation().observe("SSE:601138", transport)

        self.assertEqual(observation.total_shares, "19844092284")
        self.assertEqual(len(transport.urls), 1)
        self.assertIn("push2delay.eastmoney.com", transport.urls[0])


if __name__ == "__main__":
    unittest.main()
