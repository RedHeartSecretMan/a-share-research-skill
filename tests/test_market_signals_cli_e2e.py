from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "market_signals" / "e2e" / "fixture_cli.py"
)
REQUEST = REPOSITORY_ROOT / "examples" / "requests" / "market-limit-ecology.json"


class MarketSignalsCliE2ETests(unittest.TestCase):
    def test_limit_ecology_runs_through_public_cli_and_default_registry(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(FIXTURE_CLI), "run", "--request", str(REQUEST)],
            cwd=REPOSITORY_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        result = json.loads(completed.stdout)
        self.assertEqual(result["task_type"], "market_signals")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["brief"]["signal_type_counts"], {"limit_state": 3})
        sentiment = result["brief"]["aggregates"]["limit_state_sentiment"]
        self.assertEqual(sentiment["limit_up_count"], "1")
        self.assertEqual(sentiment["limit_break_count"], "1")
        self.assertEqual(sentiment["limit_down_count"], "0")
        self.assertEqual(sentiment["break_rate"], "50.0")
        reasons = [
            attribution
            for item in result["observations"]
            for attribution in item["attributions"]
        ]
        self.assertEqual(reasons[0]["provenance"], "editorial_annotation")
        self.assertEqual(reasons[0]["text"], "AI营销+算力")


if __name__ == "__main__":
    unittest.main()
