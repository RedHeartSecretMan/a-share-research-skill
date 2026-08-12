from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "capital_events" / "e2e" / "fixture_cli.py"
)
REQUEST = REPOSITORY_ROOT / "examples" / "requests" / "moutai-margin-trading.json"


class CapitalEventsCliE2ETests(unittest.TestCase):
    def test_margin_request_runs_through_public_cli_and_default_registry(self) -> None:
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
        self.assertEqual(result["plan"]["task_type"], "capital_events")
        self.assertEqual(result["status"], "limited")
        observations = [
            item["payload"]
            for item in result["observations"]
            if item["capability_id"] == "capital_event_observations"
        ]
        self.assertEqual(observations[0]["subject"]["security"]["code"], "600519")
        coverage = next(
            item["payload"]
            for item in result["calculations"]
            if item["kind"] == "capital_event_coverage"
        )
        self.assertEqual(coverage["observation_count"], 3)
        first = observations[0]
        self.assertEqual(first["metrics"]["financing_balance"], "18000000000.1200")
        self.assertEqual(first["units"]["financing_balance"], "CNY")
        self.assertEqual(
            first["directions"]["financing_balance"],
            "higher_is_more_financing_exposure",
        )


if __name__ == "__main__":
    unittest.main()
