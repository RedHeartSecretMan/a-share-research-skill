"""Retain report composition regressions at the internal Module Interface.

These copied-module tests do not execute the withdrawn public report workflow.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_market_overview_research import (
    REPOSITORY_ROOT,
    SKILL_ROOT,
    market_overview_request,
)


class InternalMarketFoundationCompositionTests(unittest.TestCase):
    def _run(self, scenario):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = root / "installed"
            shutil.copytree(SKILL_ROOT, installed)
            scripts = installed / "scripts"
            (root / "sitecustomize.py").write_text(
                (
                    REPOSITORY_ROOT
                    / "tests/fixtures/research_workflows/issue_96_sitecustomize.py"
                ).read_text()
            )
            script = scripts / "internal_report_regression.py"
            script.write_text(
                "import json\nfrom datetime import datetime\n"
                "from a_share_research.market_overview_foundation import MarketOverviewFoundation\n"
                + "foundation = MarketOverviewFoundation("
                + repr(market_overview_request())
                + ", datetime.fromisoformat('2026-08-03T19:40:00+08:00'))\n"
                "results = {kind: foundation({'task_type': 'overview_' + kind, 'as_of': '2026-08-03'}).to_dict() for kind in ('turnover', 'margin')}\n"
                "print(json.dumps(results))\n"
            )
            calls = root / "calls.jsonl"
            completed = subprocess.run(
                [sys.executable, str(script)],
                cwd=root,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PYTHONPATH": str(root),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "ASR96_SCENARIO": scenario,
                    "ASR96_CALLS": str(calls),
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout), [
                json.loads(line) for line in calls.read_text().splitlines()
            ]

    def test_poisoned_report_provider_blocks_only_its_following_margin(self):
        result, calls = self._run("poisoned_sse_turnover")
        owners = [item["operation_id"] for item in calls]
        self.assertNotIn("sse_market_margin@1", owners)
        self.assertIn("szse_market_margin@1", owners)
        self.assertIn("bse_market_margin@1", owners)
        margin = result["margin"]
        self.assertEqual(
            {item["operation_id"] for item in margin["observations"]},
            {"szse_market_margin@1", "bse_market_margin@1"},
        )
        operation = margin["coverage"]["whole_market_margin"]["operations"][
            "sse_market_margin@1"
        ]
        self.assertEqual(operation["execution_state"], "skipped_policy")
        self.assertFalse(operation["observation_ids"])
        self.assertFalse(margin["calculations"])

    def test_cooperative_cancel_keeps_following_margin_and_rejects_late_report(self):
        result, calls = self._run("cancelled_sse_turnover")
        self.assertEqual(
            [
                item
                for item in calls
                if item["operation_id"] == "sse-turnover-cancellation"
            ],
            [{"operation_id": "sse-turnover-cancellation", "observed_cancelled": True}],
        )
        self.assertIn("sse_market_margin@1", {item["operation_id"] for item in calls})
        self.assertNotIn(
            "sse_a_share_turnover@1",
            {item["operation_id"] for item in result["turnover"]["observations"]},
        )
        self.assertEqual(len(result["margin"]["observations"]), 3)
        self.assertTrue(result["margin"]["calculations"])

    def test_partial_empty_report_does_not_prove_zero_and_keeps_sibling_fields(self):
        result, _ = self._run("partial_empty_bse_margin")
        margin = result["margin"]
        operation = margin["coverage"]["whole_market_margin"]["operations"][
            "bse_market_margin@1"
        ]
        self.assertEqual(operation["state"], "partial")
        self.assertFalse(margin["calculations"])

    def test_partial_margin_retains_qualified_money_without_quantity_totals(self):
        result, _ = self._run("partial_bse_margin")
        margin = result["margin"]
        totals = {
            item["payload"]["metric"]: item["payload"]
            for item in margin["calculations"]
        }
        self.assertEqual(totals["financing_balance_CNY"]["value"], "3615000")
        self.assertNotIn("securities_lending_quantity_shares", totals)
        self.assertNotIn("securities_lending_sell_quantity_shares", totals)
        self.assertEqual(margin["coverage"]["whole_market_margin"]["state"], "partial")

    def test_complete_empty_margin_is_retained_without_inventing_zero_totals(self):
        result, _ = self._run("empty_bse_margin")
        margin = result["margin"]
        self.assertEqual(len(margin["observations"]), 3)
        empty = next(
            row["payload"]["observation"]
            for row in margin["observations"]
            if row["operation_id"] == "bse_market_margin@1"
        )
        self.assertEqual(empty["row_count"], 0)
        self.assertEqual(empty["metrics"], {})
        self.assertFalse(margin["calculations"])
        self.assertTrue(result["turnover"]["calculations"])
