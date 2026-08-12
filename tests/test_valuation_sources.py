from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
    SinaFinancialStatementsOperation,
    ThsConsensusEpsOperation,
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


class ValuationOperationFixtureTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlsplit(url)
        if parsed.hostname == "quotes.sina.cn":
            source = parse_qs(parsed.query)["source"][0]
            filename = {
                "lrb": "sina_601138_income.json",
                "fzb": "sina_601138_balance.json",
                "llb": "sina_601138_cashflow.json",
            }[source]
            content_type = "application/json"
        elif parsed.hostname == "basic.10jqka.com.cn":
            filename = "ths_601138_worth.html"
            content_type = "text/html; charset=utf-8"
        else:
            raise AssertionError("unexpected valuation source URL")
        return HttpResponse(
            status=200,
            content_type=content_type,
            body=Path(FIXTURES, filename).read_bytes(),
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


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


class SinaFinancialStatementsOperationTests(unittest.TestCase):
    def test_statement_contract_preserves_period_scope_units_and_owner(self) -> None:
        operation = SinaFinancialStatementsOperation()

        statements = operation.observe(
            "SSE:601138",
            date(2026, 8, 2),
            ValuationOperationFixtureTransport(),
        )
        balance = statements["balance"][0]
        evidence = balance.to_evidence(("归属于母公司股东权益合计",))

        self.assertEqual(
            {
                "operation_id": operation.operation_id,
                "statement_types": tuple(statements),
                "period": balance.period.isoformat(),
                "publication_date": balance.publication_date.isoformat(),
                "scope": balance.report_scope,
                "currency": balance.currency,
                "equity": balance.values["归属于母公司股东权益合计"],
                "evidence_operation": evidence["source_operation"],
            },
            {
                "operation_id": "sina_financial_statements@1",
                "statement_types": ("income", "balance", "cashflow"),
                "period": "2026-03-31",
                "publication_date": "2026-04-29",
                "scope": "consolidated",
                "currency": "CNY",
                "equity": "176218112000.000000",
                "evidence_operation": "sina_financial_statements@1",
            },
        )


class ThsConsensusEpsOperationTests(unittest.TestCase):
    def test_consensus_contract_preserves_year_units_coverage_and_owner(self) -> None:
        operation = ThsConsensusEpsOperation()

        snapshot = operation.observe("SSE:601138", ValuationOperationFixtureTransport())
        evidence = snapshot.to_evidence()

        self.assertEqual(
            {
                "operation_id": operation.operation_id,
                "forecasts": tuple(
                    (
                        item.year,
                        item.institutions,
                        item.minimum,
                        item.mean,
                        item.maximum,
                    )
                    for item in snapshot.forecasts
                ),
                "unit": evidence["observed_value"]["unit"],
                "source_role": evidence["source_role"],
                "evidence_operation": evidence["source_operation"],
            },
            {
                "operation_id": "ths_consensus_eps@1",
                "forecasts": (
                    (2026, 20, "2.63", "3.07", "3.36"),
                    (2027, 20, "3.22", "4.12", "4.80"),
                    (2028, 18, "3.80", "5.21", "6.85"),
                ),
                "unit": "CNY/share",
                "source_role": "signed_opinion",
                "evidence_operation": "ths_consensus_eps@1",
            },
        )


if __name__ == "__main__":
    unittest.main()
