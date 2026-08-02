from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.cli import main  # noqa: E402
from a_share_research.etf_option_contract import (  # noqa: E402
    EtfOptionSubject,
    OptionAnalytic,
    OptionContractListingEvidence,
    OptionContractMonthEvidence,
    OptionContractQuote,
    OptionCoverage,
    OptionQuery,
    OptionSession,
    OptionSourceBatch,
)
from a_share_research.etf_option_sources import (  # noqa: E402
    SinaEtfOptionSnapshotOperation,
)
from a_share_research.research_runtime import ResearchRuntime, research  # noqa: E402

from tests.test_etf_option_sources import DiagnosticGate, FixtureTransport  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def option_request(*, allow_experimental: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "etf_options",
        "subjects": [{"clue": "510050"}],
        "as_of": "2026-08-03",
        "window": {
            "observed_from": "2026-08-03",
            "observed_to": "2026-08-03",
        },
        "parameters": {
            "view": "atm",
            "expiry": {"mode": "nearest_unexpired"},
            "quote_mode": "latest",
        },
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def contract(code: str, option_type: str) -> OptionContractQuote:
    return OptionContractQuote(
        security={"exchange": "SSE", "code": code, "type": "ETF_OPTION"},
        option_type=option_type,
        strike="3.00",
        contract_month="2026-08",
        expiry_date="2026-08-26",
        series="M",
        quote_state="quoted",
        last="0.0800",
        bid="0.0799",
        ask="0.0801",
        observed_at="2026-08-03T10:35:00+08:00",
        analytics={
            "delta": OptionAnalytic(
                "0.55" if option_type == "call" else "-0.45", "dimensionless"
            ),
            "gamma": OptionAnalytic("0.1200", "provider_native_unverified"),
            "theta": OptionAnalytic("-0.0010", "provider_native_unverified"),
            "vega": OptionAnalytic("0.0020", "provider_native_unverified"),
            "implied_volatility": OptionAnalytic("0.2400", "decimal_fraction"),
        },
        source_operation="fixed_options@1",
        evidence_id=f"option-{code}",
        locator_uri=f"https://example.test/options/{code}",
        analytics_evidence_id=f"option-analytics-{code}",
        analytics_locator_uri=f"https://example.test/options/{code}/analytics",
        quote_retrieved_at=datetime(2026, 8, 3, 10, 35, 1, tzinfo=CHINA_STANDARD_TIME),
        analytics_retrieved_at=datetime(
            2026, 8, 3, 10, 35, 2, tzinfo=CHINA_STANDARD_TIME
        ),
    )


def complete_batch() -> OptionSourceBatch:
    contracts = (contract("10000001", "call"), contract("10000002", "put"))
    retrieved_at = datetime(2026, 8, 3, 10, 35, tzinfo=CHINA_STANDARD_TIME)
    return OptionSourceBatch(
        operation_id="fixed_options@1",
        subject=EtfOptionSubject("SSE", "510050", "上证50ETF"),
        session=OptionSession(
            trading_date="2026-08-03",
            observed_at="2026-08-03T10:35:00+08:00",
            market_state="intraday",
            reference_price="3.02",
            reference_price_kind="last",
            reference_evidence_id="underlying-510050",
            locator_uri="https://example.test/underlying/510050",
            retrieved_at=datetime(2026, 8, 3, 10, 35, 1, tzinfo=CHINA_STANDARD_TIME),
            reference_observed_at="2026-08-03T10:34:59+08:00",
            reference_source_operation="fixed_underlying@1",
            reference_retrieved_at=datetime(
                2026, 8, 3, 10, 35, tzinfo=CHINA_STANDARD_TIME
            ),
        ),
        contracts=contracts,
        coverage={
            name: OptionCoverage(
                "observed_nonempty", expected_count=2, observed_count=2
            )
            for name in (
                "contract_listing",
                "option_quotes",
                "provider_analytics",
            )
        },
        listing_evidence=tuple(
            OptionContractListingEvidence(
                source_operation="fixed_options@1",
                evidence_id=f"listing-{option_type}",
                option_type=option_type,
                contract_month="2026-08",
                observed_count=1,
                locator_uri=f"https://example.test/options/list/{option_type}",
                retrieved_at=retrieved_at,
            )
            for option_type in ("call", "put")
        ),
        month_evidence=OptionContractMonthEvidence(
            source_operation="fixed_options@1",
            evidence_id="listing-months",
            observed_months=("2026-08",),
            identity_status="validated",
            locator_uri="https://example.test/options/months",
            retrieved_at=retrieved_at,
        ),
    )


class FixedOptionOperation:
    operation_id = "fixed_options@1"

    def __init__(self) -> None:
        self.called = False

    def collect(self, query: OptionQuery) -> OptionSourceBatch:
        self.called = True
        self.query = query
        return complete_batch()


class NoNetworkTransport:
    def get(self, url: str, headers: dict[str, str]) -> Any:
        raise AssertionError(f"fixed operation must not use the network: {url}")


class EtfOptionsRuntimeTests(unittest.TestCase):
    def test_research_runtime_routes_an_injected_option_operation(self) -> None:
        operation = FixedOptionOperation()

        result = ResearchRuntime(
            etf_option_operations=[operation],
            etf_option_transport=NoNetworkTransport(),
        ).research(option_request())

        self.assertTrue(operation.called)
        self.assertEqual(operation.query.subject_clue, "510050")
        self.assertEqual(result["task_type"], "etf_options")
        self.assertEqual(result["status"], "limited")

    def test_research_function_forwards_option_dependencies(self) -> None:
        operation = FixedOptionOperation()

        result = research(
            option_request(),
            etf_option_operations=[operation],
            etf_option_transport=NoNetworkTransport(),
        )

        self.assertTrue(operation.called)
        self.assertEqual(result["task_type"], "etf_options")

    def test_source_policy_blocks_before_collecting(self) -> None:
        operation = FixedOptionOperation()

        result = ResearchRuntime(etf_option_operations=[operation]).research(
            option_request(allow_experimental=False)
        )

        self.assertFalse(operation.called)
        self.assertEqual(result["task_type"], "etf_options")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "source_policy_not_satisfied"
        )

    def test_default_registry_receives_the_injected_transport(self) -> None:
        operation = FixedOptionOperation()
        transport = NoNetworkTransport()
        with patch(
            "a_share_research.etf_option_registry.build_default_etf_option_operations",
            return_value=(operation,),
        ) as registry:
            result = ResearchRuntime(etf_option_transport=transport).research(
                option_request()
            )

        registry.assert_called_once_with(transport)
        self.assertEqual(result["task_type"], "etf_options")

    def test_cli_run_uses_injected_operation_without_network(self) -> None:
        operation = FixedOptionOperation()
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "etf-options.json")
            request_path.write_text(json.dumps(option_request()), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    ["run", "--request", str(request_path)],
                    etf_option_operations=[operation],
                    etf_option_transport=NoNetworkTransport(),
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(operation.called)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["task_type"], "etf_options")
        self.assertEqual(result["status"], "limited")

    def test_cli_runs_production_adapter_against_offline_source_fixtures(self) -> None:
        request = option_request()
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        operation = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "etf-options-source.json")
            request_path.write_text(json.dumps(request), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    ["run", "--request", str(request_path)],
                    etf_option_operations=[operation],
                )

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["brief"]["coverage"]["contract_listing"]["state"],
            "partial",
        )
        evidence = {item["id"]: item for item in result["evidence"]}
        self.assertIn("option-months-sina-510050", evidence)
        analytic = next(
            item
            for item in evidence.values()
            if item["id"].startswith("option-analytics-sina-")
        )
        self.assertIn("CON_SO_", analytic["locator"]["uri"])
        reference = next(
            item
            for item in evidence.values()
            if item["source_operation"] == "sse_etf_snapshot@1"
        )
        self.assertEqual(reference["evidence_time"], "2026-07-31T15:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
