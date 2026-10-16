from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.test_market_overview_research import invoke_copied_installed
from tests.test_market_signals import signal_request

BOOTSTRAP = (
    Path(__file__).parent
    / "fixtures/research_workflows/issue_96_sentiment_sitecustomize.py"
).read_text()


class MarketSentimentLineageTests(unittest.TestCase):
    def test_truncated_limit_keeps_all_source_rows_and_only_selected_sentiment_basis(
        self,
    ):
        request = signal_request(["limit_state"])
        request["parameters"]["limit"] = 2
        completed = invoke_copied_installed(request, bootstrap=BOOTSTRAP)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(len(result["observations"]), 4)
        calculation = next(
            item
            for item in result["calculations"]
            if item["kind"] == "limit_state_sentiment"
        )
        selected = {
            item["observation_ids"][0]
            for item in result["claims"]
            if item["kind"] == "market_signal_observation"
        }
        self.assertEqual(set(calculation["input_ids"]), selected)
        self.assertEqual(len(calculation["input_ids"]), 2)

    def test_time_window_retains_outside_rows_without_adopting_them(self):
        result = self._run(environment={"ASR96_SENTIMENT_WINDOW": "1"})
        self.assertEqual(len(result["observations"]), 5)
        calculation = self._sentiment(result)
        self.assertEqual(len(calculation["input_ids"]), 4)
        self.assertEqual(calculation["payload"]["limit_up_count"], "2")
        self.assertEqual(calculation["payload"]["limit_break_count"], "1")
        self.assertEqual(calculation["payload"]["limit_down_count"], "1")
        rows = {item["id"]: item["payload"] for item in result["observations"]}
        self.assertTrue(
            all(
                rows[key]["observed_on"] == "2026-07-31"
                for key in calculation["input_ids"]
            )
        )

    def test_owner_and_adjacent_pool_rows_remain_evidence_without_entering_sentiment(
        self,
    ):
        result = self._run(environment={"ASR96_SENTIMENT_SIBLINGS": "1"})
        self.assertEqual(len(result["observations"]), 6)
        calculation = self._sentiment(result)
        self.assertEqual(
            calculation["payload"]["source_operation"], "a_synthetic_pools@1"
        )
        self.assertEqual(len(calculation["input_ids"]), 4)
        self.assertEqual(
            calculation["payload"]["break_rate"], "33.33333333333333333333333333"
        )
        self.assertEqual(calculation["payload"]["consecutive_limit_ladder"], {"3": "2"})

    def test_rehashed_foreign_unselected_adjacent_missing_and_duplicate_lineage_fails(
        self,
    ):
        from copy import deepcopy
        from tests.test_direct_research_workflow_runtime import (
            _rehash_workflow,
            _validate_serialized,
        )

        original = self._run(limit=2, environment={"ASR96_SENTIMENT_SIBLINGS": "1"})
        original_calculation = self._sentiment(original)
        original_ids = original_calculation["input_ids"]
        candidates = {
            "missing": original_ids[:-1],
            "duplicate": original_ids + original_ids[:1],
        }
        for row in original["observations"]:
            payload = row["payload"]
            if payload["source_operation"] == "z_synthetic_pools@1":
                candidates["foreign"] = original_ids + [row["id"]]
            elif payload["dimensions"]["pool_state"] == "previous_limit_up":
                candidates["adjacent"] = original_ids + [row["id"]]
            elif row["id"] not in original_ids:
                candidates["unselected"] = original_ids + [row["id"]]
        self.assertEqual(
            set(candidates),
            {"missing", "duplicate", "foreign", "adjacent", "unselected"},
        )
        for mutation, ids in candidates.items():
            with self.subTest(mutation=mutation):
                result = deepcopy(original)
                calculation = self._sentiment(result)
                calculation["input_ids"] = ids
                calculation["payload"]["basis_evidence_ids"] = ids
                with self.assertRaisesRegex(
                    ValueError, "market sentiment calculation inputs are invalid"
                ):
                    _validate_serialized(_rehash_workflow(result))
        result = deepcopy(original)
        self._sentiment(result)["payload"]["limit_up_count"] = "99"
        with self.assertRaisesRegex(
            ValueError, "market sentiment calculation contradicts observations"
        ):
            _validate_serialized(_rehash_workflow(result))

    def _run(self, *, limit=20, environment=None):
        request = signal_request(["limit_state"])
        request["parameters"]["limit"] = limit
        completed = invoke_copied_installed(
            request, bootstrap=BOOTSTRAP, environment_overrides=environment
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    @staticmethod
    def _sentiment(result):
        return next(
            item
            for item in result["calculations"]
            if item["kind"] == "limit_state_sentiment"
        )
