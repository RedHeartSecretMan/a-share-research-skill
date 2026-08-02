from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLI = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts" / "entrypoint.py"
MINIMAL_BUNDLE = REPOSITORY_ROOT / "tests" / "fixtures" / "minimal_evidence_bundle"


def valuation_evidence(
    evidence_id: str,
    basis: str,
    value: str,
    unit: str,
    evidence_time: str,
) -> dict[str, object]:
    return {
        "id": evidence_id,
        "source_role": "market_observation",
        "source_operation": "caller_provided_observation",
        "subject": {
            "security": "SSE:600519",
            "issuer": "贵州茅台酒股份有限公司",
        },
        "observed_value": {"value": value, "unit": unit},
        "basis": basis,
        "evidence_time": evidence_time,
        "available_at": evidence_time,
        "retrieved_at": "2026-08-01T09:00:00+08:00",
        "locator": {
            "uri": f"https://example.invalid/evidence/{evidence_id}",
            "observation": evidence_id,
        },
        "limitations": ["Caller-provided evidence is source-unverified."],
    }


def current_valuation_manifest() -> dict[str, object]:
    manifest = {
        "schema_version": "1.0",
        "subject": {
            "security": {
                "exchange": "SSE",
                "code": "600519",
                "type": "A_SHARE",
            },
            "issuer": {"name": "贵州茅台酒股份有限公司"},
        },
        "as_of": "2026-08-01",
        "question": "current_valuation",
        "evidence": [
            valuation_evidence(
                "session-2026-07-31",
                "latest_completed_trading_session",
                "completed",
                "trading_session",
                "2026-07-31",
            ),
            valuation_evidence(
                "close-2026-07-31",
                "unadjusted_close",
                "1350.60",
                "CNY/share",
                "2026-07-31",
            ),
        ],
    }
    manifest["evidence"][1]["observed_value"]["scale"] = "1"
    return manifest


def add_effective_total_shares(
    manifest: dict[str, object],
    *,
    value: str,
    scale: str,
) -> None:
    shares = valuation_evidence(
        "shares-effective-2026-06-30",
        "effective_total_shares",
        value,
        "shares",
        "2026-06-30",
    )
    shares["source_role"] = "authoritative_disclosure"
    shares["observed_value"]["scale"] = scale
    shares["valid_from"] = "2026-06-30"
    shares["valid_through"] = "2026-08-01"
    manifest["evidence"].append(shares)


def financial_evidence(
    evidence_id: str,
    basis: str,
    value: str,
    period_start: str,
    period_end: str,
    period_type: str,
    available_at: str,
) -> dict[str, object]:
    item = valuation_evidence(
        evidence_id,
        basis,
        value,
        "CNY",
        period_end,
    )
    item["source_role"] = "authoritative_disclosure"
    item["available_at"] = available_at
    item["observed_value"]["scale"] = "1"
    item["report"] = {
        "identity": f"report-{period_end}",
        "period_start": period_start,
        "period_end": period_end,
        "period_type": period_type,
        "consolidation_scope": "consolidated",
        "attribution_scope": "owners_of_parent",
        "version": {
            "id": f"report-{period_end}-original",
            "type": "original",
            "supersedes": [],
        },
    }
    return item


def complete_valuation_manifest() -> dict[str, object]:
    manifest = current_valuation_manifest()
    add_effective_total_shares(manifest, value="1000", scale="1")
    manifest["evidence"].extend(
        [
            financial_evidence(
                "profit-fy-2025",
                "attributable_profit",
                "10000",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-03-30",
            ),
            financial_evidence(
                "equity-fy-2025",
                "attributable_equity",
                "50000",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-03-30",
            ),
        ]
    )
    return manifest


class CliContractTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *arguments],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def run_current_valuation(
        self, manifest: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            return self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

    def test_complete_source_unverified_valuation_is_an_honest_limited_brief(
        self,
    ) -> None:
        completed = self.run_current_valuation(complete_valuation_manifest())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {name: metric["status"] for name, metric in result["metrics"].items()},
            {
                "market_capitalization": "supported",
                "pe_ttm": "supported",
                "pb_mrq": "supported",
            },
        )
        self.assertEqual(
            {item["id"] for item in result["evidence"]},
            {
                "session-2026-07-31",
                "close-2026-07-31",
                "shares-effective-2026-06-30",
                "profit-fy-2025",
                "equity-fy-2025",
            },
        )
        self.assertEqual(result["unavailable_evidence"], [])
        self.assertEqual(
            [limitation["code"] for limitation in result["limitations"]],
            ["provided_evidence_source_unverified"],
        )

    def test_partial_valuation_separates_unavailable_evidence_from_source_facts(
        self,
    ) -> None:
        manifest = complete_valuation_manifest()
        unavailable_equity = manifest["evidence"][-1]
        unavailable_equity["report"]["attribution_scope"] = "parent_only"

        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {name: metric["status"] for name, metric in result["metrics"].items()},
            {
                "market_capitalization": "supported",
                "pe_ttm": "supported",
                "pb_mrq": "not_calculable",
            },
        )
        self.assertNotIn("equity-fy-2025", {item["id"] for item in result["evidence"]})
        self.assertEqual(
            [item["evidence"]["id"] for item in result["unavailable_evidence"]],
            ["equity-fy-2025"],
        )
        self.assertEqual(
            [issue["code"] for issue in result["unavailable_evidence"][0]["issues"]],
            ["incompatible_attribution_scope"],
        )

    def test_duplicate_evidence_ids_make_every_duplicate_unavailable(self) -> None:
        manifest = complete_valuation_manifest()
        duplicate_close = copy.deepcopy(manifest["evidence"][1])
        duplicate_close["observed_value"]["value"] = "1351.00"
        manifest["evidence"].append(duplicate_close)

        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn(
            "close-2026-07-31", {item["id"] for item in result["evidence"]}
        )
        duplicates = [
            item
            for item in result["unavailable_evidence"]
            if item["evidence"]["id"] == "close-2026-07-31"
        ]
        self.assertEqual(len(duplicates), 2)
        self.assertTrue(
            all(
                [issue["code"] for issue in item["issues"]] == ["duplicate_evidence_id"]
                for item in duplicates
            )
        )
        validation_duplicates = [
            item
            for item in result["bundle_validation"]["evidence"]
            if item["id"] == "close-2026-07-31"
        ]
        self.assertEqual(
            [item["admissible"] for item in validation_duplicates], [False, False]
        )

    def test_nonpositive_denominators_remain_answers_in_the_limited_brief(
        self,
    ) -> None:
        manifest = complete_valuation_manifest()
        manifest["evidence"][-2]["observed_value"]["value"] = "0"
        manifest["evidence"][-1]["observed_value"]["value"] = "-50000"

        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            {name: metric["status"] for name, metric in result["metrics"].items()},
            {
                "market_capitalization": "supported",
                "pe_ttm": "no_valuation_meaning",
                "pb_mrq": "no_valuation_meaning",
            },
        )
        self.assertIsNone(result["metrics"]["pe_ttm"]["value"])
        self.assertIsNone(result["metrics"]["pb_mrq"]["value"])

    def test_common_price_conflict_blocks_the_brief_without_dropping_observations(
        self,
    ) -> None:
        manifest = complete_valuation_manifest()
        conflicting_close = valuation_evidence(
            "conflicting-close-2026-07-31",
            "unadjusted_close",
            "1351.00",
            "CNY/share",
            "2026-07-31",
        )
        conflicting_close["observed_value"]["scale"] = "1"
        manifest["evidence"].append(conflicting_close)

        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            {name: metric["status"] for name, metric in result["metrics"].items()},
            {
                "market_capitalization": "not_calculable",
                "pe_ttm": "not_calculable",
                "pb_mrq": "not_calculable",
            },
        )
        self.assertEqual(
            {
                item["id"]
                for item in result["evidence"]
                if item["basis"] == "unadjusted_close"
            },
            {"close-2026-07-31", "conflicting-close-2026-07-31"},
        )
        self.assertIn(
            "conflicting_unadjusted_close",
            [limitation["code"] for limitation in result["limitations"]],
        )
        self.assertEqual(result["unavailable_evidence"], [])

    def test_valuation_calculates_market_cap_from_exact_scaled_operands(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "12345",
            "unit": "CNY/share",
            "scale": "0.01",
        }
        add_effective_total_shares(
            manifest,
            value="987654321",
            scale="10",
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["metrics"]["market_capitalization"],
            {
                "status": "supported",
                "value": {"value": "1219259259274.50", "unit": "CNY"},
                "calculation": {
                    "formula": "common_valuation_price * effective_total_shares",
                    "unit_conversion": "CNY/share * shares = CNY",
                    "operands": {
                        "common_valuation_price": {
                            "value": {"value": "123.45", "unit": "CNY/share"},
                            "evidence": [
                                {
                                    "evidence_id": "session-2026-07-31",
                                    "basis": "latest_completed_trading_session",
                                    "evidence_time": "2026-07-31",
                                    "observed_value": {
                                        "value": "completed",
                                        "unit": "trading_session",
                                    },
                                },
                                {
                                    "evidence_id": "close-2026-07-31",
                                    "basis": "unadjusted_close",
                                    "evidence_time": "2026-07-31",
                                    "observed_value": {
                                        "value": "12345",
                                        "unit": "CNY/share",
                                        "scale": "0.01",
                                    },
                                },
                            ],
                        },
                        "effective_total_shares": {
                            "value": {"value": "9876543210", "unit": "shares"},
                            "evidence": [
                                {
                                    "evidence_id": "shares-effective-2026-06-30",
                                    "basis": "effective_total_shares",
                                    "evidence_time": "2026-06-30",
                                    "observed_value": {
                                        "value": "987654321",
                                        "unit": "shares",
                                        "scale": "10",
                                    },
                                }
                            ],
                        },
                    },
                },
                "cross_checks": [],
                "issues": [],
            },
        )

    def test_valuation_preserves_more_than_default_decimal_precision(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "123456789012345678901234567890",
            "unit": "CNY/share",
            "scale": "0.01",
        }
        add_effective_total_shares(
            manifest,
            value="987654321098765432109876543210",
            scale="10",
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["market_capitalization"]
        self.assertEqual(
            metric["value"],
            {
                "value": (
                    "12193263113702179522618503273362292333223746380111126352690.00"
                ),
                "unit": "CNY",
            },
        )

    def test_pe_ttm_uses_an_applicable_full_year_report_with_exact_scale(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "10",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        annual_profit = financial_evidence(
            "profit-fy-2025",
            "attributable_profit",
            "5",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        annual_profit["observed_value"]["scale"] = "100"
        manifest["evidence"].append(annual_profit)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pe_ttm"]
        self.assertEqual(
            metric,
            {
                "status": "supported",
                "value": {"value": "20", "unit": "ratio"},
                "calculation": {
                    "formula": "market_capitalization / ttm_attributable_profit",
                    "unit_conversion": "CNY / CNY = ratio",
                    "precision": {
                        "significant_digits": 28,
                        "rounding": "ROUND_HALF_EVEN",
                    },
                    "operands": {
                        "market_capitalization": {
                            "value": {"value": "10000", "unit": "CNY"},
                            "source_metric": "market_capitalization",
                        },
                        "ttm_attributable_profit": {
                            "value": {"value": "500", "unit": "CNY"},
                            "period_method": "full_year",
                            "components": [
                                {
                                    "role": "full_year",
                                    "operation": "direct",
                                    "normalized_value": {
                                        "value": "500",
                                        "unit": "CNY",
                                    },
                                    "evidence_id": "profit-fy-2025",
                                }
                            ],
                            "evidence": [
                                {
                                    "evidence_id": "profit-fy-2025",
                                    "basis": "attributable_profit",
                                    "evidence_time": "2025-12-31",
                                    "observed_value": {
                                        "value": "5",
                                        "unit": "CNY",
                                        "scale": "100",
                                    },
                                }
                            ],
                        },
                    },
                },
                "cross_checks": [],
                "issues": [],
            },
        )

    def test_pe_ttm_forms_three_period_profit_with_explicit_lineage(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "12",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="100", scale="1")
        profit_inputs = [
            financial_evidence(
                "profit-fy-2025",
                "attributable_profit",
                "9",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-03-30",
            ),
            financial_evidence(
                "profit-h1-2026",
                "attributable_profit",
                "60",
                "2026-01-01",
                "2026-06-30",
                "cumulative",
                "2026-07-25",
            ),
            financial_evidence(
                "profit-h1-2025",
                "attributable_profit",
                "30",
                "2025-01-01",
                "2025-06-30",
                "cumulative",
                "2025-07-25",
            ),
        ]
        profit_inputs[0]["observed_value"]["scale"] = "100"
        profit_inputs[1]["observed_value"]["scale"] = "10"
        profit_inputs[2]["observed_value"]["scale"] = "10"
        manifest["evidence"].extend(profit_inputs)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pe_ttm"]
        self.assertEqual(metric["status"], "supported")
        self.assertEqual(metric["value"], {"value": "1", "unit": "ratio"})
        profit = metric["calculation"]["operands"]["ttm_attributable_profit"]
        self.assertEqual(profit["value"], {"value": "1200", "unit": "CNY"})
        self.assertEqual(
            profit["period_method"],
            "previous_full_year_plus_latest_cumulative_minus_matching_prior",
        )
        self.assertEqual(
            profit["components"],
            [
                {
                    "role": "previous_full_year",
                    "operation": "add",
                    "normalized_value": {"value": "900", "unit": "CNY"},
                    "evidence_id": "profit-fy-2025",
                },
                {
                    "role": "latest_current_year_cumulative",
                    "operation": "add",
                    "normalized_value": {"value": "600", "unit": "CNY"},
                    "evidence_id": "profit-h1-2026",
                },
                {
                    "role": "matching_prior_year_cumulative",
                    "operation": "subtract",
                    "normalized_value": {"value": "300", "unit": "CNY"},
                    "evidence_id": "profit-h1-2025",
                },
            ],
        )

    def test_pb_mrq_uses_the_latest_periodic_equity(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "10",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        annual_equity = financial_evidence(
            "equity-fy-2025",
            "attributable_equity",
            "2000",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        latest_equity = financial_evidence(
            "equity-h1-2026",
            "attributable_equity",
            "2500",
            "2026-01-01",
            "2026-06-30",
            "cumulative",
            "2026-07-25",
        )
        manifest["evidence"].extend([annual_equity, latest_equity])
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pb_mrq"]
        self.assertEqual(metric["status"], "supported")
        self.assertEqual(metric["value"], {"value": "4", "unit": "ratio"})
        self.assertEqual(
            metric["calculation"],
            {
                "formula": "market_capitalization / mrq_attributable_equity",
                "unit_conversion": "CNY / CNY = ratio",
                "precision": {
                    "significant_digits": 28,
                    "rounding": "ROUND_HALF_EVEN",
                },
                "operands": {
                    "market_capitalization": {
                        "value": {"value": "10000", "unit": "CNY"},
                        "source_metric": "market_capitalization",
                    },
                    "mrq_attributable_equity": {
                        "value": {"value": "2500", "unit": "CNY"},
                        "period_method": "latest_applicable_periodic_report",
                        "evidence": [
                            {
                                "evidence_id": "equity-h1-2026",
                                "basis": "attributable_equity",
                                "evidence_time": "2026-06-30",
                                "observed_value": {
                                    "value": "2500",
                                    "unit": "CNY",
                                    "scale": "1",
                                },
                            }
                        ],
                    },
                },
            },
        )
        self.assertEqual(metric["cross_checks"], [])
        self.assertEqual(metric["issues"], [])

    def test_pb_mrq_uses_an_applicable_full_year_equity_report(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "1",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].append(
            financial_evidence(
                "equity-fy-2025",
                "attributable_equity",
                "100",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-03-30",
            )
        )
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pb_mrq"]
        self.assertEqual(metric["status"], "supported")
        self.assertEqual(metric["value"], {"value": "10", "unit": "ratio"})
        self.assertEqual(
            metric["calculation"]["operands"]["mrq_attributable_equity"][
                "period_method"
            ],
            "latest_applicable_periodic_report",
        )

    def test_pb_mrq_normalizes_equity_scale_as_an_independent_operand_rule(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "1",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        scaled_equity = financial_evidence(
            "equity-fy-2025-scaled",
            "attributable_equity",
            "25",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        scaled_equity["observed_value"]["scale"] = "10"
        manifest["evidence"].append(scaled_equity)
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pb_mrq"]
        self.assertEqual(metric["value"], {"value": "4", "unit": "ratio"})
        self.assertEqual(
            metric["calculation"]["operands"]["mrq_attributable_equity"]["value"],
            {"value": "250", "unit": "CNY"},
        )

    def test_missing_mrq_equity_is_not_calculable(self) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pb_mrq"]
        self.assertEqual(metric["status"], "not_calculable")
        self.assertIsNone(metric["value"])
        self.assertEqual(
            [issue["code"] for issue in metric["issues"]],
            ["mrq_attributable_equity_missing"],
        )

    def test_wrong_equity_attribution_scope_is_not_calculable(self) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        equity = financial_evidence(
            "equity-fy-2025-wrong-attribution",
            "attributable_equity",
            "100",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        equity["report"]["attribution_scope"] = "total_equity"
        manifest["evidence"].append(equity)
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        metric = result["metrics"]["pb_mrq"]
        self.assertEqual(metric["status"], "not_calculable")
        self.assertEqual(
            [issue["code"] for issue in metric["issues"]],
            ["mrq_attributable_equity_incompatible"],
        )
        self.assertIn(
            "incompatible_attribution_scope",
            [issue["code"] for issue in result["bundle_validation"]["issues"]],
        )

    def test_nonpositive_mrq_equity_has_no_valuation_meaning_without_a_pb(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].append(
            financial_evidence(
                "equity-h1-2026",
                "attributable_equity",
                "0",
                "2026-01-01",
                "2026-06-30",
                "cumulative",
                "2026-07-25",
            )
        )
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        metric = result["metrics"]["pb_mrq"]
        self.assertEqual(result["status"], "limited")
        self.assertEqual(metric["status"], "no_valuation_meaning")
        self.assertIsNone(metric["value"])
        self.assertEqual(metric["issues"], [])
        self.assertEqual(
            metric["calculation"]["operands"]["mrq_attributable_equity"]["value"],
            {"value": "0", "unit": "CNY"},
        )

    def test_pb_mrq_uses_the_explicit_correction_to_a_full_year_report(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "1",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        original = financial_evidence(
            "equity-fy-2025-original",
            "attributable_equity",
            "100",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-20",
        )
        correction = copy.deepcopy(original)
        correction["id"] = "equity-fy-2025-corrected"
        correction["observed_value"]["value"] = "200"
        correction["available_at"] = "2026-04-01"
        correction["report"]["version"] = {
            "id": "report-2025-12-31-correction",
            "type": "correction",
            "supersedes": ["report-2025-12-31-original"],
        }
        manifest["evidence"].extend([original, correction])
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pb_mrq"]
        self.assertEqual(metric["status"], "supported")
        self.assertEqual(metric["value"], {"value": "5", "unit": "ratio"})
        self.assertEqual(
            [
                item["evidence_id"]
                for item in metric["calculation"]["operands"][
                    "mrq_attributable_equity"
                ]["evidence"]
            ],
            ["equity-fy-2025-corrected"],
        )

    def test_pb_mrq_rejects_inapplicable_equity_evidence(self) -> None:
        def late_publication(item: dict[str, Any]) -> list[dict[str, Any]]:
            item["available_at"] = "2026-08-02"
            return [item]

        def parent_only(item: dict[str, Any]) -> list[dict[str, Any]]:
            item["report"]["consolidation_scope"] = "parent_company"
            return [item]

        def average_equity(item: dict[str, Any]) -> list[dict[str, Any]]:
            item["basis"] = "average_attributable_equity"
            return [item]

        def incompatible_unit(item: dict[str, Any]) -> list[dict[str, Any]]:
            item["observed_value"]["unit"] = "CNY/share"
            return [item]

        def conflicting_versions(item: dict[str, Any]) -> list[dict[str, Any]]:
            other = copy.deepcopy(item)
            other["id"] = "equity-fy-2025-other-original"
            other["observed_value"]["value"] = "120"
            other["report"]["version"]["id"] = "report-2025-other-original"
            return [item, other]

        cases = (
            (
                "late_publication",
                late_publication,
                "mrq_attributable_equity_incompatible",
            ),
            (
                "parent_only",
                parent_only,
                "mrq_attributable_equity_incompatible",
            ),
            ("average_equity", average_equity, "mrq_attributable_equity_missing"),
            (
                "incompatible_unit",
                incompatible_unit,
                "mrq_attributable_equity_incompatible",
            ),
            (
                "conflicting_versions",
                conflicting_versions,
                "unresolved_report_version_relationship",
            ),
        )
        for name, mutate, expected_issue in cases:
            with self.subTest(name=name):
                manifest = current_valuation_manifest()
                add_effective_total_shares(manifest, value="1000", scale="1")
                equity = financial_evidence(
                    "equity-fy-2025",
                    "attributable_equity",
                    "100",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-30",
                )
                manifest["evidence"].extend(mutate(equity))
                completed = self.run_current_valuation(manifest)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                metric = json.loads(completed.stdout)["metrics"]["pb_mrq"]
                self.assertEqual(metric["status"], "not_calculable")
                self.assertIsNone(metric["value"])
                self.assertIsNone(metric["calculation"])
                self.assertEqual(
                    [issue["code"] for issue in metric["issues"]],
                    [expected_issue],
                )

    def test_provider_pe_is_an_explained_cross_check_not_a_project_operand(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "10",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].append(
            financial_evidence(
                "profit-fy-2025",
                "attributable_profit",
                "500",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-03-30",
            )
        )
        provider_pe = valuation_evidence(
            "provider-pe",
            "provider_pe_ttm",
            "19.5",
            "ratio",
            "2026-07-31",
        )
        provider_pe["observed_value"]["scale"] = "1"
        provider_pe["limitations"] = [
            "Provider TTM period and attributable-profit scope are not "
            "independently verified."
        ]
        manifest["evidence"].append(provider_pe)
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pe_ttm"]
        self.assertEqual(metric["value"], {"value": "20", "unit": "ratio"})
        self.assertEqual(
            metric["cross_checks"],
            [
                {
                    "evidence_id": "provider-pe",
                    "role": "provider_observation",
                    "source_independence": "unverified",
                    "comparability": "not_comparable",
                    "incomparability_reason": "provider_methodology_unverified",
                    "evidence_time": "2026-07-31",
                    "observed_value": {
                        "value": "19.5",
                        "unit": "ratio",
                        "scale": "1",
                    },
                    "normalized_value": {"value": "19.5", "unit": "ratio"},
                    "difference": None,
                    "provider_limitations": [
                        "Provider TTM period and attributable-profit scope are not "
                        "independently verified."
                    ],
                    "explanation": (
                        "The provider PE observation is retained as a separate "
                        "cross-check candidate, but its TTM and profit-scope methodology "
                        "is not verified as comparable and it cannot replace the project "
                        "calculation."
                    ),
                }
            ],
        )

    def test_provider_pb_is_only_a_validated_cross_check_not_an_equity_operand(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        valid_provider_pb = valuation_evidence(
            "provider-pb",
            "provider_pb_mrq",
            "8.1",
            "ratio",
            "2026-07-31",
        )
        valid_provider_pb["observed_value"]["scale"] = "1"
        valid_provider_pb["limitations"] = [
            "Provider MRQ and attributable-equity scope are not independently verified."
        ]
        invalid_provider_pb = copy.deepcopy(valid_provider_pb)
        invalid_provider_pb["id"] = "provider-pb-wrong-unit"
        invalid_provider_pb["observed_value"]["unit"] = "CNY"
        manifest["evidence"].extend([valid_provider_pb, invalid_provider_pb])
        completed = self.run_current_valuation(manifest)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        metric = result["metrics"]["pb_mrq"]
        self.assertEqual(metric["status"], "not_calculable")
        self.assertEqual(
            [issue["code"] for issue in metric["issues"]],
            ["mrq_attributable_equity_missing"],
        )
        self.assertEqual(len(metric["cross_checks"]), 1)
        cross_check = metric["cross_checks"][0]
        self.assertEqual(cross_check["evidence_id"], "provider-pb")
        self.assertEqual(cross_check["role"], "provider_observation")
        self.assertEqual(cross_check["comparability"], "not_comparable")
        self.assertEqual(
            cross_check["incomparability_reason"], "project_value_unavailable"
        )
        self.assertIsNone(cross_check["difference"])
        self.assertEqual(
            cross_check["provider_limitations"], valid_provider_pb["limitations"]
        )
        self.assertIn(
            "incompatible_unit",
            [issue["code"] for issue in result["bundle_validation"]["issues"]],
        )

    def test_pe_ttm_normalizes_financial_scale_as_an_independent_operand_rule(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "1",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        scaled_profit = financial_evidence(
            "profit-fy-2025-scaled",
            "attributable_profit",
            "25",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        scaled_profit["observed_value"]["scale"] = "10"
        manifest["evidence"].append(scaled_profit)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pe_ttm"]
        self.assertEqual(metric["value"], {"value": "4", "unit": "ratio"})
        self.assertEqual(
            metric["calculation"]["operands"]["ttm_attributable_profit"]["value"],
            {"value": "250", "unit": "CNY"},
        )

    def test_pe_ttm_preserves_large_decimal_cancellation_before_classification(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "1",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].extend(
            [
                financial_evidence(
                    "profit-fy-2025",
                    "attributable_profit",
                    "100000000000000000000000000001",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-30",
                ),
                financial_evidence(
                    "profit-q1-2026",
                    "attributable_profit",
                    "1",
                    "2026-01-01",
                    "2026-03-31",
                    "cumulative",
                    "2026-04-25",
                ),
                financial_evidence(
                    "profit-q1-2025",
                    "attributable_profit",
                    "100000000000000000000000000000",
                    "2025-01-01",
                    "2025-03-31",
                    "cumulative",
                    "2025-04-25",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pe_ttm"]
        self.assertEqual(metric["status"], "supported")
        self.assertEqual(metric["value"], {"value": "500", "unit": "ratio"})
        self.assertEqual(
            metric["calculation"]["operands"]["ttm_attributable_profit"]["value"],
            {"value": "2", "unit": "CNY"},
        )

    def test_nonpositive_ttm_profit_has_no_valuation_meaning_without_a_negative_pe(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].append(
            financial_evidence(
                "profit-fy-2025",
                "attributable_profit",
                "-100",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-03-30",
            )
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        metric = result["metrics"]["pe_ttm"]
        self.assertEqual(result["status"], "limited")
        self.assertEqual(metric["status"], "no_valuation_meaning")
        self.assertIsNone(metric["value"])
        self.assertEqual(metric["issues"], [])
        self.assertEqual(
            metric["calculation"]["operands"]["ttm_attributable_profit"]["value"],
            {"value": "-100", "unit": "CNY"},
        )

    def test_missing_profit_period_is_not_calculable_without_blocking_market_cap(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].extend(
            [
                financial_evidence(
                    "profit-fy-2025",
                    "attributable_profit",
                    "100",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-30",
                ),
                financial_evidence(
                    "profit-h1-2026",
                    "attributable_profit",
                    "60",
                    "2026-01-01",
                    "2026-06-30",
                    "cumulative",
                    "2026-07-25",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["metrics"]["market_capitalization"]["status"], "supported"
        )
        metric = result["metrics"]["pe_ttm"]
        self.assertEqual(metric["status"], "not_calculable")
        self.assertIsNone(metric["value"])
        self.assertIsNone(metric["calculation"])
        self.assertEqual(
            [issue["code"] for issue in metric["issues"]],
            ["ttm_report_period_missing"],
        )

    def test_pe_ttm_uses_the_explicitly_resolved_report_correction(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "1",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        original = financial_evidence(
            "profit-fy-2025-original",
            "attributable_profit",
            "100",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-20",
        )
        correction = copy.deepcopy(original)
        correction["id"] = "profit-fy-2025-corrected"
        correction["observed_value"]["value"] = "200"
        correction["available_at"] = "2026-04-01"
        correction["report"]["version"] = {
            "id": "report-2025-12-31-correction",
            "type": "correction",
            "supersedes": ["report-2025-12-31-original"],
        }
        manifest["evidence"].extend([original, correction])
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["pe_ttm"]
        self.assertEqual(metric["status"], "supported")
        self.assertEqual(metric["value"], {"value": "5", "unit": "ratio"})
        self.assertEqual(
            [
                item["evidence_id"]
                for item in metric["calculation"]["operands"][
                    "ttm_attributable_profit"
                ]["evidence"]
            ],
            ["profit-fy-2025-corrected"],
        )

    def test_report_published_after_the_research_boundary_cannot_supply_pe(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        manifest["evidence"].append(
            financial_evidence(
                "profit-fy-2025-late",
                "attributable_profit",
                "100",
                "2025-01-01",
                "2025-12-31",
                "full_year",
                "2026-08-02",
            )
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        metric = result["metrics"]["pe_ttm"]
        self.assertEqual(metric["status"], "not_calculable")
        self.assertIsNone(metric["value"])
        self.assertIn(
            "publication_after_research_date",
            [issue["code"] for issue in result["bundle_validation"]["issues"]],
        )
        self.assertEqual(
            [issue["code"] for issue in metric["issues"]],
            ["ttm_attributable_profit_incompatible"],
        )

    def test_provider_market_cap_is_an_explained_cross_check_not_an_operand(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "100",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        provider_market_cap = valuation_evidence(
            "provider-market-cap",
            "provider_market_cap",
            "9900",
            "CNY",
            "2026-07-31",
        )
        provider_market_cap["observed_value"]["scale"] = "10"
        manifest["evidence"].append(provider_market_cap)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metric = json.loads(completed.stdout)["metrics"]["market_capitalization"]
        self.assertEqual(metric["value"], {"value": "100000", "unit": "CNY"})
        self.assertEqual(
            metric["cross_checks"],
            [
                {
                    "evidence_id": "provider-market-cap",
                    "role": "provider_observation",
                    "source_independence": "unverified",
                    "comparability": "comparable",
                    "evidence_time": "2026-07-31",
                    "observed_value": {
                        "value": "9900",
                        "unit": "CNY",
                        "scale": "10",
                    },
                    "normalized_value": {"value": "99000", "unit": "CNY"},
                    "difference": {
                        "value": "1000",
                        "unit": "CNY",
                        "meaning": "project_calculation_minus_provider_observation",
                    },
                    "explanation": (
                        "The provider market-cap observation is retained only as a "
                        "comparison candidate; source independence is unverified, and it "
                        "does not replace the project calculation."
                    ),
                }
            ],
        )

    def test_provider_market_cap_from_another_date_is_retained_without_a_difference(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "100",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        provider_market_cap = valuation_evidence(
            "provider-market-cap",
            "provider_market_cap",
            "99000",
            "CNY",
            "2026-07-30",
        )
        provider_market_cap["observed_value"]["scale"] = "1"
        manifest["evidence"].append(provider_market_cap)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        cross_check = json.loads(completed.stdout)["metrics"]["market_capitalization"][
            "cross_checks"
        ][0]
        self.assertEqual(cross_check["comparability"], "not_comparable")
        self.assertEqual(
            cross_check["incomparability_reason"], "valuation_time_mismatch"
        )
        self.assertIsNone(cross_check["difference"])
        self.assertIn("does not match", cross_check["explanation"])

    def test_incompatible_provider_market_cap_is_rejected_without_blocking_project_value(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["observed_value"] = {
            "value": "100",
            "unit": "CNY/share",
            "scale": "1",
        }
        add_effective_total_shares(manifest, value="1000", scale="1")
        provider_market_cap = valuation_evidence(
            "provider-market-cap",
            "provider_market_cap",
            "99000",
            "USD",
            "2026-07-31",
        )
        manifest["evidence"].append(provider_market_cap)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        metric = result["metrics"]["market_capitalization"]
        self.assertEqual(metric["value"], {"value": "100000", "unit": "CNY"})
        self.assertEqual(metric["cross_checks"], [])
        self.assertTrue(
            {"incompatible_unit", "missing_required_field"}.issubset(
                {
                    issue["code"]
                    for issue in result["bundle_validation"]["issues"]
                    if issue["path"].startswith("evidence[3].observed_value")
                }
            )
        )

    def test_non_market_observation_provider_market_cap_is_not_a_cross_check(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        provider_market_cap = valuation_evidence(
            "provider-market-cap",
            "provider_market_cap",
            "1350600",
            "CNY",
            "2026-07-31",
        )
        provider_market_cap["source_role"] = "authoritative_disclosure"
        provider_market_cap["observed_value"]["scale"] = "1"
        manifest["evidence"].append(provider_market_cap)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["metrics"]["market_capitalization"]["cross_checks"], [])
        self.assertIn(
            "incompatible_source_role",
            [issue["code"] for issue in result["bundle_validation"]["issues"]],
        )

    def test_valuation_revalidates_changed_bundle_material_before_calculation(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        close_locator = manifest["evidence"][1]["locator"]
        close_locator.pop("uri")
        close_locator["path"] = "materials/close.txt"
        close_locator["sha256"] = (
            "6db7d803e74f1ffa7d8f5adc0bf95b3e15bf4c8373fffadf546227cc6c6742cb"
        )
        with tempfile.TemporaryDirectory() as bundle:
            materials = Path(bundle, "materials")
            materials.mkdir()
            artifact = Path(materials, "close.txt")
            artifact.write_bytes(b"before")
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            validated = self.run_cli("validate-bundle", "--bundle", bundle)
            artifact.write_bytes(b"after")
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertEqual(
            json.loads(validated.stdout)["validation"]["structure"], "valid"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["metrics"]["market_capitalization"]["status"],
            "not_calculable",
        )
        self.assertEqual(
            result["bundle_validation"]["validation"]["structure"],
            "invalid",
        )
        self.assertIn(
            "artifact_hash_mismatch",
            [issue["code"] for issue in result["bundle_validation"]["issues"]],
        )

    def test_float_shares_and_provider_market_cap_cannot_substitute_for_total_shares(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        float_shares = valuation_evidence(
            "float-shares",
            "float_shares",
            "900",
            "shares",
            "2026-06-30",
        )
        float_shares["source_role"] = "authoritative_disclosure"
        float_shares["observed_value"]["scale"] = "1"
        provider_market_cap = valuation_evidence(
            "provider-market-cap",
            "provider_market_cap",
            "1200000",
            "CNY",
            "2026-07-31",
        )
        provider_market_cap["observed_value"]["scale"] = "1"
        manifest["evidence"].extend([float_shares, provider_market_cap])
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        metric = result["metrics"]["market_capitalization"]
        self.assertEqual(metric["status"], "not_calculable")
        self.assertIsNone(metric["value"])
        self.assertEqual(
            [issue["code"] for issue in metric["issues"]],
            ["effective_total_shares_inapplicable"],
        )
        self.assertEqual(
            metric["cross_checks"],
            [
                {
                    "evidence_id": "provider-market-cap",
                    "role": "provider_observation",
                    "source_independence": "unverified",
                    "comparability": "not_comparable",
                    "incomparability_reason": "project_value_unavailable",
                    "evidence_time": "2026-07-31",
                    "observed_value": {
                        "value": "1200000",
                        "unit": "CNY",
                        "scale": "1",
                    },
                    "normalized_value": {"value": "1200000", "unit": "CNY"},
                    "difference": None,
                    "explanation": (
                        "No project market capitalization is available; the provider "
                        "observation cannot replace missing or inapplicable project operands."
                    ),
                }
            ],
        )

    def test_missing_common_valuation_price_is_a_zero_exit_blocked_result(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"] = manifest["evidence"][:1]
        add_effective_total_shares(manifest, value="1000", scale="1")
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            [
                issue["code"]
                for issue in result["metrics"]["market_capitalization"]["issues"]
            ],
            ["unadjusted_close_missing"],
        )
        self.assertEqual(
            [limitation["code"] for limitation in result["limitations"]],
            [
                "unadjusted_close_missing",
                "ttm_attributable_profit_missing",
                "mrq_attributable_equity_missing",
                "provided_evidence_source_unverified",
            ],
        )

    def test_unconfirmed_security_identity_is_a_zero_exit_blocked_result(
        self,
    ) -> None:
        manifest = complete_valuation_manifest()
        manifest["subject"]["security"]["code"] = "60051"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["research"]["security"])
        self.assertIn(
            "invalid_security_identity",
            [limitation["code"] for limitation in result["limitations"]],
        )

    def test_current_valuation_cli_date_must_match_the_bundle_boundary(self) -> None:
        manifest = current_valuation_manifest()
        add_effective_total_shares(manifest, value="1000", scale="1")
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-07-31",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("does not match bundle as_of", completed.stderr)

    def test_common_valuation_price_requires_matching_completed_session(self) -> None:
        manifest = current_valuation_manifest()
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "valid")
        self.assertEqual(
            result["valuation_inputs"]["common_valuation_price"],
            {
                "status": "applicable",
                "evidence_ids": [
                    "session-2026-07-31",
                    "close-2026-07-31",
                ],
                "issues": [],
            },
        )

    def test_price_without_session_evidence_is_not_calculable(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"] = manifest["evidence"][1:]
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        price = result["valuation_inputs"]["common_valuation_price"]
        self.assertEqual(price["status"], "not_calculable")
        self.assertEqual(price["evidence_ids"], ["close-2026-07-31"])
        self.assertEqual(
            [issue["code"] for issue in price["issues"]],
            ["trading_session_evidence_missing"],
        )

    def test_common_valuation_close_requires_an_explicit_positive_scale(self) -> None:
        manifest = current_valuation_manifest()
        del manifest["evidence"][1]["observed_value"]["scale"]
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertIn(
            "evidence[1].observed_value.scale",
            {issue["path"] for issue in result["issues"]},
        )
        self.assertEqual(
            result["valuation_inputs"]["common_valuation_price"]["status"],
            "not_calculable",
        )

    def test_attributed_opinion_cannot_establish_a_completed_trading_session(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][0]["source_role"] = "attributed_opinion"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertIn(
            "incompatible_source_role",
            {issue["code"] for issue in result["issues"]},
        )
        self.assertEqual(
            result["valuation_inputs"]["common_valuation_price"]["status"],
            "not_calculable",
        )

    def test_one_malformed_close_cannot_hide_behind_an_agreeing_valid_close(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        malformed_close = valuation_evidence(
            "close-without-scale",
            "unadjusted_close",
            "1350.60",
            "CNY/share",
            "2026-07-31",
        )
        manifest["evidence"].append(malformed_close)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        price = json.loads(completed.stdout)["valuation_inputs"][
            "common_valuation_price"
        ]
        self.assertEqual(price["status"], "not_calculable")
        self.assertEqual(
            [issue["code"] for issue in price["issues"]],
            ["incompatible_unadjusted_close"],
        )

    def test_subject_mismatched_close_is_never_an_applicable_operand(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"][1]["subject"]["security"] = "SZSE:000001"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        price = result["valuation_inputs"]["common_valuation_price"]
        self.assertEqual(price["status"], "not_calculable")
        self.assertEqual(
            [issue["code"] for issue in price["issues"]],
            ["incompatible_unadjusted_close"],
        )

    def test_exact_total_shares_must_be_effective_at_research_boundary(self) -> None:
        manifest = current_valuation_manifest()
        shares = valuation_evidence(
            "shares-effective-2026-06-30",
            "effective_total_shares",
            "1256197800",
            "shares",
            "2026-06-30",
        )
        shares["source_role"] = "authoritative_disclosure"
        shares["observed_value"]["scale"] = "1"
        shares["valid_from"] = "2026-06-30"
        shares["valid_through"] = "2026-08-01"
        manifest["evidence"].append(shares)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["valuation_inputs"]["effective_total_shares"],
            {
                "status": "applicable",
                "evidence_ids": ["shares-effective-2026-06-30"],
                "issues": [],
            },
        )

    def test_float_period_end_rounded_and_estimated_shares_cannot_substitute(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        replacements = []
        for evidence_id, basis in (
            ("float-shares", "float_shares"),
            ("period-end-shares", "period_end_total_shares"),
            ("rounded-shares", "rounded_total_shares"),
            ("estimated-shares", "estimated_total_shares"),
        ):
            item = valuation_evidence(
                evidence_id,
                basis,
                "1256200000",
                "shares",
                "2026-06-30",
            )
            item["observed_value"]["scale"] = "1"
            replacements.append(item)
        manifest["evidence"].extend(replacements)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        shares = json.loads(completed.stdout)["valuation_inputs"][
            "effective_total_shares"
        ]
        self.assertEqual(shares["status"], "not_calculable")
        self.assertEqual(
            shares["evidence_ids"],
            [
                "float-shares",
                "period-end-shares",
                "rounded-shares",
                "estimated-shares",
            ],
        )
        self.assertEqual(
            [issue["code"] for issue in shares["issues"]],
            ["effective_total_shares_inapplicable"],
        )

    def test_report_lineage_selects_three_period_ttm_and_latest_mrq_inputs(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"].extend(
            [
                financial_evidence(
                    "profit-fy-2025",
                    "attributable_profit",
                    "100",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-30",
                ),
                financial_evidence(
                    "profit-h1-2026",
                    "attributable_profit",
                    "60",
                    "2026-01-01",
                    "2026-06-30",
                    "cumulative",
                    "2026-07-25",
                ),
                financial_evidence(
                    "profit-h1-2025",
                    "attributable_profit",
                    "40",
                    "2025-01-01",
                    "2025-06-30",
                    "cumulative",
                    "2025-07-25",
                ),
                financial_evidence(
                    "equity-h1-2026",
                    "attributable_equity",
                    "500",
                    "2026-01-01",
                    "2026-06-30",
                    "cumulative",
                    "2026-07-25",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        inputs = json.loads(completed.stdout)["valuation_inputs"]
        self.assertEqual(
            inputs["ttm_attributable_profit"],
            {
                "status": "applicable",
                "evidence_ids": [
                    "profit-fy-2025",
                    "profit-h1-2026",
                    "profit-h1-2025",
                ],
                "denominator_classification": "positive",
                "issues": [],
            },
        )
        self.assertEqual(
            inputs["mrq_attributable_equity"],
            {
                "status": "applicable",
                "evidence_ids": ["equity-h1-2026"],
                "denominator_classification": "positive",
                "issues": [],
            },
        )

    def test_provider_derivatives_are_cross_checks_not_valuation_operands(self) -> None:
        manifest = current_valuation_manifest()
        for evidence_id, basis, value, unit in (
            ("provider-market-cap", "provider_market_cap", "1700000000000", "CNY"),
            ("provider-pe", "provider_pe_ttm", "22.5", "ratio"),
            ("provider-pb", "provider_pb_mrq", "8.1", "ratio"),
            ("forecast-profit", "forecast_attributable_profit", "90000000000", "CNY"),
        ):
            item = valuation_evidence(
                evidence_id,
                basis,
                value,
                unit,
                "2026-07-31",
            )
            item["observed_value"]["scale"] = "1"
            manifest["evidence"].append(item)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        inputs = json.loads(completed.stdout)["valuation_inputs"]
        self.assertEqual(
            inputs["cross_checks"],
            {
                "evidence_ids": [
                    "provider-market-cap",
                    "provider-pe",
                    "provider-pb",
                    "forecast-profit",
                ]
            },
        )
        self.assertEqual(inputs["effective_total_shares"]["status"], "not_calculable")
        self.assertEqual(inputs["ttm_attributable_profit"]["status"], "not_calculable")
        self.assertEqual(inputs["mrq_attributable_equity"]["status"], "not_calculable")
        self.assertEqual(
            inputs["ttm_attributable_profit"]["issues"][0]["code"],
            "ttm_attributable_profit_missing",
        )
        self.assertEqual(
            inputs["mrq_attributable_equity"]["issues"][0]["code"],
            "mrq_attributable_equity_missing",
        )

    def test_matching_close_observations_must_agree_before_price_is_applicable(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        second_close = valuation_evidence(
            "close-cross-check-2026-07-31",
            "unadjusted_close",
            "1350.600",
            "CNY/share",
            "2026-07-31",
        )
        second_close["observed_value"]["scale"] = "1"
        manifest["evidence"].append(second_close)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        price = json.loads(completed.stdout)["valuation_inputs"][
            "common_valuation_price"
        ]
        self.assertEqual(price["status"], "applicable")
        self.assertEqual(
            price["evidence_ids"],
            [
                "session-2026-07-31",
                "close-2026-07-31",
                "close-cross-check-2026-07-31",
            ],
        )

    def test_conflicting_close_observations_are_preserved_and_fail_closed(self) -> None:
        manifest = current_valuation_manifest()
        conflicting_close = valuation_evidence(
            "conflicting-close-2026-07-31",
            "unadjusted_close",
            "1351.00",
            "CNY/share",
            "2026-07-31",
        )
        conflicting_close["observed_value"]["scale"] = "1"
        manifest["evidence"].append(conflicting_close)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        price = json.loads(completed.stdout)["valuation_inputs"][
            "common_valuation_price"
        ]
        self.assertEqual(price["status"], "not_calculable")
        self.assertEqual(
            price["evidence_ids"],
            [
                "session-2026-07-31",
                "close-2026-07-31",
                "conflicting-close-2026-07-31",
            ],
        )
        self.assertEqual(
            [issue["code"] for issue in price["issues"]],
            ["conflicting_unadjusted_close"],
        )

    def test_ambiguous_report_versions_make_the_affected_denominator_inapplicable(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        first = financial_evidence(
            "profit-fy-2025-a",
            "attributable_profit",
            "100",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        second = copy.deepcopy(first)
        second["id"] = "profit-fy-2025-b"
        second["observed_value"]["value"] = "110"
        second["report"]["version"]["id"] = "report-2025-12-31-other-original"
        manifest["evidence"].extend([first, second])
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        profit = json.loads(completed.stdout)["valuation_inputs"][
            "ttm_attributable_profit"
        ]
        self.assertEqual(profit["status"], "not_calculable")
        self.assertEqual(
            profit["evidence_ids"],
            ["profit-fy-2025-a", "profit-fy-2025-b"],
        )
        self.assertEqual(
            [issue["code"] for issue in profit["issues"]],
            ["unresolved_report_version_relationship"],
        )

    def test_financial_evidence_requires_complete_report_metadata(self) -> None:
        manifest = current_valuation_manifest()
        profit = financial_evidence(
            "incomplete-profit-report",
            "attributable_profit",
            "100",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        del profit["observed_value"]["scale"]
        del profit["report"]["attribution_scope"]
        del profit["report"]["version"]
        manifest["evidence"].append(profit)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertTrue(
            {
                "evidence[2].observed_value.scale",
                "evidence[2].report.attribution_scope",
                "evidence[2].report.version",
            }.issubset({issue["path"] for issue in result["issues"]})
        )
        self.assertFalse(result["evidence"][2]["admissible"])

    def test_effective_total_shares_requires_exact_scale_and_validity_window(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        shares = valuation_evidence(
            "unproven-total-shares",
            "effective_total_shares",
            "1256197800",
            "shares",
            "2026-06-30",
        )
        shares["source_role"] = "authoritative_disclosure"
        manifest["evidence"].append(shares)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertTrue(
            {
                "evidence[2].observed_value.scale",
                "evidence[2].valid_from",
                "evidence[2].valid_through",
            }.issubset({issue["path"] for issue in result["issues"]})
        )

    def test_non_finite_total_shares_are_rejected_without_crashing(self) -> None:
        manifest = current_valuation_manifest()
        shares = valuation_evidence(
            "non-finite-total-shares",
            "effective_total_shares",
            "NaN",
            "shares",
            "2026-06-30",
        )
        shares["source_role"] = "authoritative_disclosure"
        shares["observed_value"]["scale"] = "1"
        shares["valid_from"] = "2026-06-30"
        shares["valid_through"] = "2026-08-01"
        manifest["evidence"].append(shares)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertEqual(
            result["valuation_inputs"]["effective_total_shares"]["status"],
            "not_calculable",
        )

    def test_explicit_correction_and_latest_mrq_classify_nonpositive_denominators(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        original_profit = financial_evidence(
            "profit-fy-2025-original",
            "attributable_profit",
            "10",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-20",
        )
        corrected_profit = copy.deepcopy(original_profit)
        corrected_profit["id"] = "profit-fy-2025-corrected"
        corrected_profit["observed_value"]["value"] = "-2"
        corrected_profit["available_at"] = "2026-04-01"
        corrected_profit["report"]["version"] = {
            "id": "report-2025-12-31-correction",
            "type": "correction",
            "supersedes": ["report-2025-12-31-original"],
        }
        annual_equity = financial_evidence(
            "equity-fy-2025",
            "attributable_equity",
            "500",
            "2025-01-01",
            "2025-12-31",
            "full_year",
            "2026-03-20",
        )
        latest_equity = financial_evidence(
            "equity-h1-2026",
            "attributable_equity",
            "0",
            "2026-01-01",
            "2026-06-30",
            "cumulative",
            "2026-07-25",
        )
        current_profit = financial_evidence(
            "profit-h1-2026",
            "attributable_profit",
            "5",
            "2026-01-01",
            "2026-06-30",
            "cumulative",
            "2026-07-25",
        )
        matching_prior_profit = financial_evidence(
            "profit-h1-2025",
            "attributable_profit",
            "10",
            "2025-01-01",
            "2025-06-30",
            "cumulative",
            "2025-07-25",
        )
        manifest["evidence"].extend(
            [
                original_profit,
                corrected_profit,
                current_profit,
                matching_prior_profit,
                annual_equity,
                latest_equity,
            ]
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        inputs = json.loads(completed.stdout)["valuation_inputs"]
        self.assertEqual(
            inputs["ttm_attributable_profit"],
            {
                "status": "no_valuation_meaning",
                "evidence_ids": [
                    "profit-fy-2025-corrected",
                    "profit-h1-2026",
                    "profit-h1-2025",
                ],
                "denominator_classification": "non_positive",
                "issues": [],
            },
        )
        self.assertEqual(
            inputs["mrq_attributable_equity"],
            {
                "status": "no_valuation_meaning",
                "evidence_ids": ["equity-h1-2026"],
                "denominator_classification": "non_positive",
                "issues": [],
            },
        )

    def test_financial_units_and_cumulative_periods_must_be_compatible(self) -> None:
        manifest = current_valuation_manifest()
        profit = financial_evidence(
            "incompatible-profit",
            "attributable_profit",
            "100",
            "2025-02-01",
            "2025-12-31",
            "full_year",
            "2026-03-30",
        )
        profit["observed_value"]["unit"] = "CNY/share"
        manifest["evidence"].append(profit)
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertTrue(
            {
                "incompatible_unit",
                "invalid_report_period",
            }.issubset({issue["code"] for issue in result["issues"]})
        )
        profit_input = result["valuation_inputs"]["ttm_attributable_profit"]
        self.assertEqual(profit_input["evidence_ids"], ["incompatible-profit"])
        self.assertEqual(
            [issue["code"] for issue in profit_input["issues"]],
            ["ttm_attributable_profit_incompatible"],
        )

    def test_ttm_uses_latest_available_cumulative_period_across_year_boundary(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        manifest["as_of"] = "2026-01-15"
        manifest["evidence"] = [
            financial_evidence(
                "profit-fy-2024",
                "attributable_profit",
                "80",
                "2024-01-01",
                "2024-12-31",
                "full_year",
                "2025-03-30",
            ),
            financial_evidence(
                "profit-q3-2025",
                "attributable_profit",
                "75",
                "2025-01-01",
                "2025-09-30",
                "cumulative",
                "2025-10-30",
            ),
            financial_evidence(
                "profit-q3-2024",
                "attributable_profit",
                "60",
                "2024-01-01",
                "2024-09-30",
                "cumulative",
                "2024-10-30",
            ),
        ]
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        profit = json.loads(completed.stdout)["valuation_inputs"][
            "ttm_attributable_profit"
        ]
        self.assertEqual(profit["status"], "applicable")
        self.assertEqual(
            profit["evidence_ids"],
            ["profit-fy-2024", "profit-q3-2025", "profit-q3-2024"],
        )

    def test_mrq_rejects_non_periodic_month_end_equity(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"].append(
            financial_evidence(
                "equity-monthly-2026-05",
                "attributable_equity",
                "500",
                "2026-01-01",
                "2026-05-31",
                "cumulative",
                "2026-06-15",
            )
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertIn(
            "invalid_report_period",
            {issue["code"] for issue in result["issues"]},
        )
        self.assertEqual(
            result["valuation_inputs"]["mrq_attributable_equity"]["status"],
            "not_calculable",
        )

    def test_mrq_requires_equity_from_the_latest_known_periodic_report(self) -> None:
        manifest = current_valuation_manifest()
        manifest["evidence"].extend(
            [
                financial_evidence(
                    "equity-fy-2025",
                    "attributable_equity",
                    "500",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-20",
                ),
                financial_evidence(
                    "profit-q1-2026",
                    "attributable_profit",
                    "20",
                    "2026-01-01",
                    "2026-03-31",
                    "cumulative",
                    "2026-04-25",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        equity = json.loads(completed.stdout)["valuation_inputs"][
            "mrq_attributable_equity"
        ]
        self.assertEqual(equity["status"], "not_calculable")
        self.assertEqual(
            equity["evidence_ids"],
            ["equity-fy-2025", "profit-q1-2026"],
        )
        self.assertEqual(
            [issue["code"] for issue in equity["issues"]],
            ["mrq_latest_report_equity_missing"],
        )

    def test_any_newer_periodic_financial_evidence_advances_the_report_boundary(
        self,
    ) -> None:
        manifest = current_valuation_manifest()
        total_equity = financial_evidence(
            "total-equity-h1-2026",
            "total_equity",
            "600",
            "2026-01-01",
            "2026-06-30",
            "cumulative",
            "2026-07-25",
        )
        total_equity["report"]["attribution_scope"] = "all_equity"
        manifest["evidence"].extend(
            [
                financial_evidence(
                    "profit-fy-2025",
                    "attributable_profit",
                    "100",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-20",
                ),
                financial_evidence(
                    "equity-fy-2025",
                    "attributable_equity",
                    "500",
                    "2025-01-01",
                    "2025-12-31",
                    "full_year",
                    "2026-03-20",
                ),
                total_equity,
            ]
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        inputs = json.loads(completed.stdout)["valuation_inputs"]
        self.assertEqual(
            inputs["ttm_attributable_profit"]["evidence_ids"],
            ["profit-fy-2025", "total-equity-h1-2026"],
        )
        self.assertEqual(
            inputs["ttm_attributable_profit"]["issues"][0]["code"],
            "ttm_latest_report_profit_missing",
        )
        self.assertEqual(
            inputs["mrq_attributable_equity"]["evidence_ids"],
            ["equity-fy-2025", "total-equity-h1-2026"],
        )
        self.assertEqual(
            inputs["mrq_attributable_equity"]["issues"][0]["code"],
            "mrq_latest_report_equity_missing",
        )

    def test_valid_bundle_is_structurally_valid_but_source_unverified(self) -> None:
        completed = self.run_cli(
            "validate-bundle",
            "--bundle",
            str(MINIMAL_BUNDLE),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(
            result["validation"],
            {"structure": "valid", "source_verification": "unverified"},
        )
        self.assertEqual(result["issues"], [])
        self.assertEqual(
            result["evidence"],
            [
                {
                    "id": "close-2026-07-31",
                    "admissible": True,
                    "source_verification": "unverified",
                    "issues": [],
                }
            ],
        )

    def test_bundle_validation_collects_all_independent_evidence_errors(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        evidence = manifest["evidence"][0]
        del evidence["source_role"]
        del evidence["source_operation"]
        del evidence["available_at"]
        del evidence["locator"]["observation"]
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        issue_paths = {issue["path"] for issue in result["issues"]}
        self.assertTrue(
            {
                "evidence[0].source_role",
                "evidence[0].source_operation",
                "evidence[0].available_at",
                "evidence[0].locator.observation",
            }.issubset(issue_paths)
        )
        self.assertEqual(result["evidence"][0]["id"], "close-2026-07-31")
        self.assertFalse(result["evidence"][0]["admissible"])
        self.assertEqual(
            result["evidence"][0]["issues"],
            [
                issue
                for issue in result["issues"]
                if issue["path"].startswith("evidence[0].")
            ],
        )

    def test_included_artifact_is_hash_checked_without_claiming_source_verification(
        self,
    ) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        locator = manifest["evidence"][0]["locator"]
        del locator["uri"]
        locator["path"] = "materials/source.bin"
        locator["sha256"] = (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        with tempfile.TemporaryDirectory() as bundle:
            materials = Path(bundle, "materials")
            materials.mkdir()
            Path(materials, "source.bin").write_bytes(b"abc")
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "valid")
        self.assertEqual(result["validation"]["source_verification"], "unverified")
        self.assertTrue(result["evidence"][0]["admissible"])
        self.assertEqual(result["evidence"][0]["source_verification"], "unverified")

    def test_validation_aggregates_schema_time_unit_relationship_and_artifact_issues(
        self,
    ) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["schema_version"] = "0.9"
        evidence = manifest["evidence"][0]
        evidence["subject"] = {
            "security": "SZSE:000001",
            "issuer": "平安银行股份有限公司",
        }
        evidence["observed_value"]["unit"] = "shares"
        evidence["available_at"] = "2026-08-02"
        evidence["locator"] = {
            "path": "materials/source.bin",
            "sha256": "0" * 64,
            "observation": "daily close",
        }
        with tempfile.TemporaryDirectory() as bundle:
            materials = Path(bundle, "materials")
            materials.mkdir()
            Path(materials, "source.bin").write_bytes(b"abc")
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertTrue(
            {
                "unsupported_schema_version",
                "security_mismatch",
                "issuer_mismatch",
                "incompatible_unit",
                "publication_after_research_date",
                "artifact_hash_mismatch",
            }.issubset({issue["code"] for issue in result["issues"]})
        )
        self.assertFalse(result["evidence"][0]["admissible"])

    def test_artifact_paths_fail_closed_before_any_bundle_escape(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        original = manifest["evidence"][0]
        sha256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        with tempfile.TemporaryDirectory() as parent:
            bundle = Path(parent, "bundle")
            materials = Path(bundle, "materials")
            materials.mkdir(parents=True)
            outside = Path(parent, "outside.bin")
            outside.write_bytes(b"abc")
            external_link = Path(materials, "external-link.bin")
            try:
                external_link.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symbolic links unavailable: {error}")

            evidence_items = []
            for evidence_id, path in (
                ("absolute", str(outside.resolve())),
                ("traversal", "../outside.bin"),
                ("external-link", "materials/external-link.bin"),
            ):
                item = copy.deepcopy(original)
                item["id"] = evidence_id
                item["locator"] = {
                    "path": path,
                    "sha256": sha256,
                    "observation": "daily close",
                }
                evidence_items.append(item)
            manifest["evidence"] = evidence_items
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", str(bundle))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            [issue["code"] for issue in result["issues"]],
            ["unsafe_artifact_path", "unsafe_artifact_path", "artifact_unavailable"],
        )
        self.assertEqual(
            [item["admissible"] for item in result["evidence"]],
            [False, False, False],
        )

    def test_caller_source_claim_cannot_upgrade_source_verification(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["source_verification"] = "verified"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "valid")
        self.assertEqual(result["validation"]["source_verification"], "unverified")
        self.assertEqual(result["evidence"][0]["source_verification"], "unverified")

    def test_validation_collects_json_numbers_with_other_manifest_issues(self) -> None:
        manifest_text = Path(MINIMAL_BUNDLE, "manifest.json").read_text(
            encoding="utf-8"
        )
        manifest_text = manifest_text.replace(
            '"unit": "CNY/share"', '"unit": "CNY/share", "scale": 0.01'
        ).replace('"source_role": "market_observation",', "")
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(manifest_text, encoding="utf-8")
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertTrue(
            {
                "evidence[0].source_role",
                "evidence[0].observed_value.scale",
            }.issubset({issue["path"] for issue in result["issues"]})
        )

    def test_local_paths_cannot_masquerade_as_external_source_locators(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        original = manifest["evidence"][0]
        evidence_items = []
        for evidence_id, uri in (
            ("relative", "materials/source.pdf"),
            ("file-uri", "file:///tmp/source.pdf"),
        ):
            item = copy.deepcopy(original)
            item["id"] = evidence_id
            item["locator"]["uri"] = uri
            evidence_items.append(item)
        manifest["evidence"] = evidence_items
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            [issue["code"] for issue in result["issues"]],
            ["invalid_source_locator", "invalid_source_locator"],
        )
        self.assertEqual(
            [item["admissible"] for item in result["evidence"]],
            [False, False],
        )

    def test_evidence_cannot_describe_an_observation_after_retrieval(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        evidence = manifest["evidence"][0]
        evidence["evidence_time"] = "2026-08-01"
        evidence["available_at"] = "2026-07-30"
        evidence["retrieved_at"] = "2026-07-31T16:00:00+08:00"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["validation"]["structure"], "invalid")
        self.assertIn(
            "evidence_after_retrieval",
            [issue["code"] for issue in result["issues"]],
        )

    def test_malformed_uri_is_collected_with_other_evidence_issues(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        evidence = manifest["evidence"][0]
        del evidence["source_role"]
        evidence["locator"]["uri"] = "https://["
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(
            {"evidence[0].source_role", "evidence[0].locator.uri"}.issubset(
                {issue["path"] for issue in result["issues"]}
            )
        )

    def test_ambiguous_locator_still_reports_independent_locator_issues(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["locator"].update(
            {
                "uri": "https://[",
                "path": "../outside.pdf",
                "sha256": "not-a-hash",
            }
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli("validate-bundle", "--bundle", bundle)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(
            {
                "ambiguous_locator",
                "invalid_source_locator",
                "invalid_sha256",
                "unsafe_artifact_path",
            }.issubset({issue["code"] for issue in result["issues"]})
        )

    def test_minimal_provided_evidence_is_an_honest_limited_result(self) -> None:
        completed = self.run_cli(
            "valuation",
            "--bundle",
            str(MINIMAL_BUNDLE),
            "--as-of",
            "2026-08-01",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["research"]["as_of"], "2026-08-01")
        self.assertEqual(result["research"]["timezone"], "Asia/Shanghai")
        self.assertEqual(result["research"]["security"], "SSE:600519")
        self.assertEqual(result["research"]["question"], "provided_unadjusted_close")
        self.assertEqual(
            result["evidence"][0]["observed_value"],
            {"value": "1350.60", "unit": "CNY/share"},
        )
        self.assertEqual(
            [limitation["code"] for limitation in result["limitations"]],
            ["provided_evidence_source_unverified"],
        )

    def test_relative_research_date_is_a_protocol_error(self) -> None:
        completed = self.run_cli(
            "valuation",
            "--bundle",
            str(MINIMAL_BUNDLE),
            "--as-of",
            "current",
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("YYYY-MM-DD", completed.stderr)

    def test_missing_bundle_manifest_is_an_io_error_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as empty_bundle:
            completed = self.run_cli(
                "valuation",
                "--bundle",
                empty_bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("error: cannot read bundle manifest", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_malformed_manifest_is_a_protocol_error_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text("not-json", encoding="utf-8")
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("error: invalid bundle protocol", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_numeric_evidence_value_must_be_an_exact_decimal_string(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["observed_value"]["value"] = 1350.60
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("decimal string", completed.stderr)

    def test_unquoted_json_number_is_rejected_before_float_parsing(self) -> None:
        manifest_text = Path(MINIMAL_BUNDLE, "manifest.json").read_text(
            encoding="utf-8"
        )
        manifest_text = manifest_text.replace(
            '"unit": "CNY/share"', '"unit": "CNY/share", "scale": 0.01'
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(manifest_text, encoding="utf-8")
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("JSON numbers must be decimal strings", completed.stderr)

    def test_non_finite_json_constant_is_rejected_before_float_parsing(self) -> None:
        manifest_text = Path(MINIMAL_BUNDLE, "manifest.json").read_text(
            encoding="utf-8"
        )
        manifest_text = manifest_text.replace(
            '"unit": "CNY/share"', '"unit": "CNY/share", "confidence": NaN'
        )
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(manifest_text, encoding="utf-8")
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("JSON numbers must be decimal strings", completed.stderr)

    def test_result_is_saved_only_to_an_explicit_output_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory, "result.json")
            completed = self.run_cli(
                "valuation",
                "--bundle",
                str(MINIMAL_BUNDLE),
                "--as-of",
                "2026-08-01",
                "--output",
                str(output),
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                json.loads(completed.stdout),
            )

    def test_output_write_failure_keeps_valid_stdout_result(self) -> None:
        with tempfile.TemporaryDirectory() as output_directory:
            completed = self.run_cli(
                "valuation",
                "--bundle",
                str(MINIMAL_BUNDLE),
                "--as-of",
                "2026-08-01",
                "--output",
                output_directory,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["status"], "limited")
        self.assertIn("error: cannot write result", completed.stderr)

    def test_default_run_does_not_write_to_the_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory, "bundle")
            bundle.mkdir()
            Path(bundle, "manifest.json").write_bytes(
                Path(MINIMAL_BUNDLE, "manifest.json").read_bytes()
            )
            before = sorted(path.relative_to(bundle) for path in bundle.rglob("*"))

            completed = self.run_cli(
                "valuation",
                "--bundle",
                str(bundle),
                "--as-of",
                "2026-08-01",
            )

            after = sorted(path.relative_to(bundle) for path in bundle.rglob("*"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(after, before)

    def test_missing_domain_evidence_is_a_zero_exit_blocked_result(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"] = []
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            [limitation["code"] for limitation in result["limitations"]],
            ["no_admissible_evidence"],
        )

    def test_natural_language_research_question_is_a_protocol_error(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["question"] = "请分析贵州茅台"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("question must be provided_unadjusted_close", completed.stderr)

    def test_cli_research_date_must_match_the_bundle_boundary(self) -> None:
        completed = self.run_cli(
            "valuation",
            "--bundle",
            str(MINIMAL_BUNDLE),
            "--as-of",
            "2026-07-31",
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("does not match bundle as_of", completed.stderr)

    def test_missing_research_subject_is_a_protocol_error(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        del manifest["subject"]
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("subject", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_missing_evidence_source_role_is_a_protocol_error(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        del manifest["evidence"][0]["source_role"]
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("source_role", completed.stderr)

    def test_evidence_subject_must_match_the_single_bundle_subject(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["subject"]["security"] = "SZSE:000001"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("security does not match bundle subject", completed.stderr)

    def test_evidence_published_after_research_date_is_rejected(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["available_at"] = "2026-08-02"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("available_at is later than the research date", completed.stderr)

    def test_same_day_close_cannot_be_retrieved_before_market_close(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        evidence = manifest["evidence"][0]
        evidence["evidence_time"] = "2026-08-01"
        evidence["available_at"] = "2026-08-01"
        evidence["retrieved_at"] = "2026-08-01T09:00:00+08:00"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("before the China market close", completed.stderr)

    def test_close_evidence_requires_price_unit(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["observed_value"]["unit"] = "shares"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("unadjusted_close requires CNY/share", completed.stderr)

    def test_close_evidence_requires_market_observation_role(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["evidence"][0]["source_role"] = "market_signal"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("market_observation source role", completed.stderr)

    def test_bundle_subject_must_use_a_canonical_a_share_identifier(self) -> None:
        manifest = json.loads(
            Path(MINIMAL_BUNDLE, "manifest.json").read_text(encoding="utf-8")
        )
        manifest["subject"]["security"]["type"] = "BOND"
        with tempfile.TemporaryDirectory() as bundle:
            Path(bundle, "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            completed = self.run_cli(
                "valuation",
                "--bundle",
                bundle,
                "--as-of",
                "2026-08-01",
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("canonical SSE or SZSE A-share", completed.stderr)


if __name__ == "__main__":
    unittest.main()
