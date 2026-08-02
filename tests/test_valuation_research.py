from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "valuation_research" / "fixture_cli.py"
)


class SecurityValuationProcessTests(unittest.TestCase):
    def run_valuation(
        self, scenario: str = "default", **parameter_overrides: object
    ) -> dict[str, object]:
        parameters = {"target_pe": "30"}
        subject = {"clue": "工业富联", "issuer_security_class_count": 1}
        if "issuer_security_class_count" in parameter_overrides:
            subject["issuer_security_class_count"] = parameter_overrides.pop(
                "issuer_security_class_count"
            )
        parameters.update(parameter_overrides)
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [subject],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": parameters,
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "research-task.json")
            request_path.write_text(
                json.dumps(request, ensure_ascii=False), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "A_SHARE_RESEARCH_TEST_SCENARIO": scenario,
                },
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_industrial_fulian_valuation_rebuilds_reported_and_forward_metrics(
        self,
    ) -> None:
        result = self.run_valuation()

        self.assertEqual(result["task_type"], "security_valuation")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["subjects"][0],
            {
                "security": {
                    "exchange": "SSE",
                    "code": "601138",
                    "type": "A_SHARE",
                },
                "name": "工业富联",
                "issuer": {
                    "name": "富士康工业互联网股份有限公司",
                    "identifier": None,
                    "security_relationship": "verified",
                },
            },
        )
        self.assertEqual(
            result["valuation_basis"],
            {
                "trading_date": "2026-07-31",
                "price": {"value": "56.700", "unit": "CNY/share"},
                "effective_total_shares": {
                    "value": "19844092284",
                    "unit": "shares",
                    "effective_at": None,
                    "observed_at": "2026-08-02T18:30:00+08:00",
                    "effective_status": "current_snapshot_observation",
                },
            },
        )
        self.assertEqual(result["quarterly_snapshots"][0]["period"], "2026-03-31")
        self.assertEqual(
            set(result["quarterly_snapshots"][0]["statements"]),
            {"income", "balance", "cashflow"},
        )
        self.assertEqual(
            result["reported_financials"]["ttm_attributable_profit"],
            {
                "value": "40649643000.000000",
                "unit": "CNY",
                "period_method": "FY2025 + 2026Q1 - 2025Q1 comparative",
                "evidence_periods": ["2025-12-31", "2026-03-31", "2025-03-31"],
            },
        )
        self.assertEqual(
            {
                item["source_role"]
                for item in result["evidence"]
                if item.get("source_operation") == "sina_financial_statements@1"
            },
            {"market_observation"},
        )
        self.assertEqual(
            result["reported_financials"]["mrq_attributable_equity"],
            {
                "value": "176218112000.000000",
                "unit": "CNY",
                "period": "2026-03-31",
                "publication_date": "2026-04-29",
                "scope": "consolidated_attributable_to_owners_of_parent",
                "audit_status": "unaudited",
            },
        )
        self.assertEqual(
            result["forecast"]["consensus_eps"],
            [
                {
                    "year": 2026,
                    "value": "3.07",
                    "unit": "CNY/share",
                    "institutions": 20,
                },
                {
                    "year": 2027,
                    "value": "4.12",
                    "unit": "CNY/share",
                    "institutions": 20,
                },
                {
                    "year": 2028,
                    "value": "5.21",
                    "unit": "CNY/share",
                    "institutions": 18,
                },
            ],
        )
        self.assertEqual(
            {key: value["value"] for key, value in result["metrics"].items()},
            {
                "market_capitalization": "1125160032502.800",
                "pe_ttm": "27.6795",
                "pb_mrq": "6.3850",
                "forward_pe": "18.4691",
                "forecast_eps_growth": "34.2020",
                "peg": "0.5400",
                "pe_digestion_years": "0.0000",
            },
        )
        self.assertEqual(result["metrics"]["pe_digestion_years"]["target_pe"], "30")
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertIn(
            "experimental_automatic_valuation_sources",
            {item["code"] for item in result["limitations"]},
        )
        self.assertEqual(
            {
                item["source_operation"]
                for item in result["evidence"]
                if "source_operation" in item
            },
            {
                "sse_stock_list@1",
                "cninfo_security_dictionary@1",
                "sse_daily_line@1",
                "tencent_daily_line@1",
                "eastmoney_stock_info@1",
                "sina_financial_statements@1",
                "ths_consensus_eps@1",
            },
        )

    def test_nonpositive_ttm_profit_preserves_other_metrics_without_negative_pe(
        self,
    ) -> None:
        result = self.run_valuation("negative_profit")

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["metrics"]["pe_ttm"],
            {
                "status": "no_valuation_meaning",
                "reason": "ttm_attributable_profit_is_nonpositive",
            },
        )
        self.assertEqual(
            result["metrics"]["market_capitalization"]["status"], "calculated"
        )
        self.assertEqual(result["metrics"]["pb_mrq"]["status"], "calculated")
        self.assertEqual(result["metrics"]["forward_pe"]["status"], "calculated")

    def test_missing_consensus_forecast_blocks_but_keeps_reported_valuation(
        self,
    ) -> None:
        result = self.run_valuation("missing_forecast")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["metrics"]["pe_ttm"]["status"], "calculated")
        self.assertEqual(
            result["metrics"]["forward_pe"],
            {
                "status": "not_calculable",
                "reason": "consensus_eps_forecast_unavailable",
            },
        )
        self.assertIn(
            "unknown_schema", {item["code"] for item in result["source_errors"]}
        )
        self.assertIn(
            "automatic_valuation_critical_inputs_unavailable",
            {item["code"] for item in result["limitations"]},
        )

    def test_missing_price_blocks_and_keeps_unavailable_metric_contract(self) -> None:
        result = self.run_valuation("missing_price")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["metrics"],
            {
                "market_capitalization": {
                    "status": "not_calculable",
                    "reason": "valuation_price_not_established",
                },
                "pe_ttm": {
                    "status": "not_calculable",
                    "reason": "valuation_price_not_established",
                },
                "pb_mrq": {
                    "status": "not_calculable",
                    "reason": "valuation_price_not_established",
                },
                "forward_pe": {
                    "status": "not_calculable",
                    "reason": "valuation_price_not_established",
                },
                "forecast_eps_growth": {
                    "status": "not_calculable",
                    "reason": "not_evaluated_after_critical_input_failure",
                },
                "peg": {
                    "status": "not_calculable",
                    "reason": "valuation_price_not_established",
                },
                "pe_digestion_years": {
                    "status": "not_calculable",
                    "reason": "valuation_price_not_established",
                },
            },
        )
        self.assertTrue(result["evidence"])

    def test_missing_shares_blocks_and_keeps_price_evidence(self) -> None:
        result = self.run_valuation("missing_shares")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["metrics"]["market_capitalization"],
            {
                "status": "not_calculable",
                "reason": "effective_total_shares_unavailable",
            },
        )
        self.assertEqual(
            {item["source_operation"] for item in result["evidence"]},
            {
                "sse_stock_list@1",
                "cninfo_security_dictionary@1",
                "sse_daily_line@1",
                "tencent_daily_line@1",
            },
        )

    def test_unusable_financial_statements_block_but_keep_forward_metrics(self) -> None:
        result = self.run_valuation("financial_scope_conflict")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["metrics"]["pe_ttm"],
            {
                "status": "not_calculable",
                "reason": "reported_financial_statements_unavailable",
            },
        )
        self.assertEqual(result["metrics"]["forward_pe"]["status"], "calculated")
        self.assertIn(
            "financial_scope_mismatch",
            {item["code"] for item in result["source_errors"]},
        )
        critical_gap = next(
            item
            for item in result["limitations"]
            if item["code"] == "automatic_valuation_critical_inputs_unavailable"
        )
        self.assertEqual(critical_gap["inputs"], ["reported_financial_statements"])

    def test_missing_financial_source_blocks_but_keeps_forward_metrics(self) -> None:
        result = self.run_valuation("missing_financials")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["metrics"]["pe_ttm"]["status"], "not_calculable")
        self.assertEqual(result["metrics"]["forward_pe"]["status"], "calculated")
        self.assertIn(
            "empty_response", {item["code"] for item in result["source_errors"]}
        )

    def test_provider_market_cap_conflict_does_not_replace_project_calculation(
        self,
    ) -> None:
        result = self.run_valuation("provider_mcap_conflict")

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["metrics"]["market_capitalization"]["value"],
            "1125160032502.800",
        )
        self.assertIn(
            "provider_market_cap_conflict",
            {item["code"] for item in result["conflicts"]},
        )

    def test_provider_market_cap_sub_cent_rounding_is_not_a_conflict(self) -> None:
        result = self.run_valuation("provider_mcap_rounding")

        self.assertEqual(result["status"], "limited")
        self.assertNotIn(
            "provider_market_cap_conflict",
            {item["code"] for item in result["conflicts"]},
        )

    def test_irrelevant_duplicate_statement_label_does_not_hide_required_metrics(
        self,
    ) -> None:
        result = self.run_valuation("irrelevant_duplicate_financial_item")

        self.assertEqual(result["metrics"]["pe_ttm"]["status"], "calculated")
        self.assertNotIn(
            "duplicate_financial_item",
            {item["code"] for item in result["source_errors"]},
        )
        latest_income_items = result["financial_statements"]["income"][0]["items"]
        self.assertEqual(
            [
                item["value"]
                for item in latest_income_items
                if item["label"] == "利息收入"
            ],
            ["1", "2"],
        )

    def test_duplicate_required_statement_label_remains_inapplicable(self) -> None:
        result = self.run_valuation("required_duplicate_financial_item")

        self.assertEqual(result["metrics"]["pe_ttm"]["status"], "not_calculable")
        self.assertIn(
            "duplicate_financial_item",
            {item["code"] for item in result["source_errors"]},
        )

    def test_multi_class_issuer_valuation_fails_before_combining_security_classes(
        self,
    ) -> None:
        result = self.run_valuation(issuer_security_class_count=2)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][-1]["code"], "multi_class_issuer_not_supported"
        )
        self.assertEqual(result["metrics"], {})

    def test_unknown_security_class_scope_blocks_before_issuer_valuation(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联"}],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "research-task.json")
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][-1]["code"],
            "issuer_security_class_scope_not_established",
        )


class ValuationComparisonProcessTests(unittest.TestCase):
    def test_comparison_keeps_blocked_row_and_discloses_partial_coverage(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "工业富联", "issuer_security_class_count": 1},
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
            ],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "research-task.json")
            request_path.write_text(
                json.dumps(request, ensure_ascii=False), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "A_SHARE_RESEARCH_TEST_SCENARIO": "one_missing_forecast",
                },
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            [row["status"] for row in result["rows"]], ["blocked", "limited"]
        )
        self.assertEqual(
            result["rows"][0]["metrics"]["forward_pe"]["status"],
            "not_calculable",
        )
        partial = next(
            item
            for item in result["limitations"]
            if item["code"] == "valuation_comparison_contains_blocked_rows"
        )
        self.assertEqual(
            partial["blocked_subjects"],
            [{"security": "SSE:601138", "name": "工业富联"}],
        )

    def test_five_security_comparison_uses_one_date_and_one_metric_basis(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "工业富联", "issuer_security_class_count": 1},
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
                {"clue": "中国平安", "issuer_security_class_count": 1},
                {"clue": "蓝色光标", "issuer_security_class_count": 1},
                {"clue": "平安银行", "issuer_security_class_count": 1},
            ],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "research-task.json")
            request_path.write_text(
                json.dumps(request, ensure_ascii=False), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_CLI),
                    "run",
                    "--request",
                    str(request_path),
                ],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["task_type"], "valuation_compare")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["comparison_basis"],
            {
                "as_of": "2026-08-02",
                "price_basis": "latest_completed_unadjusted_close",
                "target_pe": "30",
                "metric_order": [
                    "market_capitalization",
                    "pe_ttm",
                    "pb_mrq",
                    "forward_pe",
                    "forecast_eps_growth",
                    "peg",
                    "pe_digestion_years",
                ],
            },
        )
        self.assertEqual(
            [row["security"] for row in result["rows"]],
            [
                "SSE:601138",
                "SSE:600519",
                "SSE:601318",
                "SZSE:300058",
                "SZSE:000001",
            ],
        )
        self.assertEqual(len(result["rows"]), 5)
        for row in result["rows"]:
            self.assertEqual(row["status"], "limited")
            self.assertEqual(row["trading_date"], "2026-07-31")
            self.assertEqual(
                list(row["metrics"]), result["comparison_basis"]["metric_order"]
            )
            self.assertTrue(
                all(
                    metric.get("role") != "provider_metric"
                    for metric in row["metrics"].values()
                )
            )
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["source_errors"], [])


if __name__ == "__main__":
    unittest.main()
