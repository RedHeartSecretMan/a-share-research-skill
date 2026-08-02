from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CLI = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "research_content"
    / "e2e"
    / "fixture_cli.py"
)
REQUESTS = REPOSITORY_ROOT / "examples" / "requests"


class ResearchContentCliE2ETests(unittest.TestCase):
    def run_request(
        self,
        request_name: str,
        scenario: str,
        *,
        extra_environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "A_SHARE_RESEARCH_CONTENT_E2E_SCENARIO": scenario,
            **(extra_environment or {}),
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(FIXTURE_CLI),
                "run",
                "--request",
                str(REQUESTS / request_name),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        return completed, result

    def test_theme_report_cli_uses_named_iwencai_environment_without_leaking_it(
        self,
    ) -> None:
        secret = "e2e-iwencai-secret-must-not-leak"

        completed, result = self.run_request(
            "theme-report-search.json",
            "theme_report",
            extra_environment={
                "IWENCAI_API_KEY": secret,
                "IWENCAI_BASE_URL": "https://iwencai.fixture.test",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertNotIn(secret, completed.stdout)
        self.assertEqual(result["task_type"], "research_content")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["brief"]["material_type_counts"], {"research_report": 1}
        )
        self.assertEqual(
            result["materials"][0]["source_operation"], "iwencai_content_search@1"
        )

    def test_bluefocus_disclosures_and_news_cli_uses_default_registry(self) -> None:
        completed, result = self.run_request(
            "bluefocus-announcements-news.json",
            "bluefocus_disclosures_news",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertEqual(result["task_type"], "research_content")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["brief"]["material_type_counts"],
            {"stock_news": 3, "announcement": 3},
        )
        self.assertEqual(
            result["subjects"][0]["security"],
            {"exchange": "SZSE", "code": "300058", "type": "A_SHARE"},
        )
        self.assertEqual(result["source_errors"], [])


if __name__ == "__main__":
    unittest.main()
