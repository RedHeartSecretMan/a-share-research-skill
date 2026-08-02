from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research-skill" / "scripts"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"
FIXTURE_CLI = FIXTURES / "fixture_cli.py"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.identity_sources import (  # noqa: E402
    CninfoSecurityDictionaryOperation,
    HttpResponse,
    SourceOperationError,
    SseStockListOperation,
    SzseStockListOperation,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


class FixtureTransport:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.content_type = content_type

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        return HttpResponse(
            status=200,
            content_type=self.content_type,
            body=self.body,
            retrieved_at=datetime(2026, 8, 2, 10, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class IdentitySourceOperationTests(unittest.TestCase):
    def test_sse_stock_list_normalizes_identity_observation(self) -> None:
        transport = FixtureTransport(Path(FIXTURES, "sse_600519.json").read_bytes())

        observations = SseStockListOperation().observe("600519", transport)

        self.assertEqual(len(observations), 1)
        evidence = observations[0].to_evidence()
        self.assertEqual(evidence["source_operation"], "sse_stock_list@1")
        self.assertTrue(evidence["experimental"])
        self.assertEqual(evidence["subject"]["security"], "SSE:600519")
        self.assertEqual(evidence["subject"]["issuer"], "贵州茅台酒股份有限公司")
        self.assertEqual(
            evidence["observation"],
            {
                "kind": "security_identity",
                "exchange": "SSE",
                "code": "600519",
                "name": "贵州茅台",
                "security_type": "A_SHARE",
                "valid_from": None,
                "valid_to": None,
                "listing_status": "current",
                "issuer_identifier": None,
                "issuer_relationship_verified": True,
            },
        )
        self.assertEqual(evidence["evidence_time"], "2026-08-02T10:30:00+08:00")
        self.assertIsNone(evidence["available_at"])
        self.assertEqual(evidence["retrieved_at"], "2026-08-02T10:30:00+08:00")

    def test_szse_stock_list_normalizes_identity_observation(self) -> None:
        transport = FixtureTransport(Path(FIXTURES, "szse_000001.json").read_bytes())

        observations = SzseStockListOperation().observe("000001", transport)

        self.assertEqual(len(observations), 1)
        evidence = observations[0].to_evidence()
        self.assertEqual(evidence["source_operation"], "szse_stock_list@1")
        self.assertTrue(evidence["experimental"])
        self.assertEqual(evidence["subject"]["security"], "SZSE:000001")
        self.assertIsNone(evidence["subject"]["issuer"])
        self.assertEqual(evidence["observation"]["name"], "平安银行")
        self.assertEqual(evidence["observation"]["valid_from"], "1991-04-03")

    def test_cninfo_dictionary_normalizes_identity_observation(self) -> None:
        transport = FixtureTransport(Path(FIXTURES, "cninfo_stocks.json").read_bytes())

        observations = CninfoSecurityDictionaryOperation().observe("600519", transport)

        self.assertEqual(len(observations), 1)
        evidence = observations[0].to_evidence()
        self.assertEqual(evidence["source_operation"], "cninfo_security_dictionary@1")
        self.assertEqual(evidence["subject"]["security"], "SSE:600519")
        self.assertEqual(evidence["observation"]["issuer_identifier"], "gssh0600519")
        self.assertEqual(evidence["observation"]["security_type"], "A_SHARE")

    def test_cninfo_issuer_relationship_must_embed_the_same_security_code(
        self,
    ) -> None:
        inconsistent = (
            Path(FIXTURES, "cninfo_stocks.json")
            .read_text(encoding="utf-8")
            .replace("gssh0600519", "gssh0600000")
        )
        transport = FixtureTransport(inconsistent.encode("utf-8"))

        with self.assertRaises(SourceOperationError) as caught:
            CninfoSecurityDictionaryOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "inconsistent_identity_payload")

    def test_http_success_with_empty_body_fails_closed(self) -> None:
        transport = FixtureTransport(b"")

        with self.assertRaises(SourceOperationError) as caught:
            SseStockListOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "empty_response")
        self.assertEqual(caught.exception.source_operation, "sse_stock_list@1")

    def test_unexpected_content_type_fails_closed(self) -> None:
        transport = FixtureTransport(
            Path(FIXTURES, "sse_600519.json").read_bytes(),
            content_type="text/html",
        )

        with self.assertRaises(SourceOperationError) as caught:
            SseStockListOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "unexpected_content_type")

    def test_unknown_source_schema_fails_closed(self) -> None:
        transport = FixtureTransport(b'{"data": []}')

        with self.assertRaises(SourceOperationError) as caught:
            SzseStockListOperation().observe("000001", transport)

        self.assertEqual(caught.exception.code, "unknown_schema")

    def test_wrong_security_payload_fails_closed(self) -> None:
        wrong_security = (
            Path(FIXTURES, "sse_600519.json")
            .read_text(encoding="utf-8")
            .replace("600519", "600000")
        )
        transport = FixtureTransport(wrong_security.encode("utf-8"))

        with self.assertRaises(SourceOperationError) as caught:
            SseStockListOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "wrong_security_payload")

    def test_matching_non_a_share_payload_fails_closed(self) -> None:
        wrong_type = (
            Path(FIXTURES, "cninfo_stocks.json")
            .read_text(encoding="utf-8")
            .replace(
                '"category": "A股",\n      "orgId": "gssh0600519"',
                '"category": "基金",\n      "orgId": "gssh0600519"',
            )
        )
        transport = FixtureTransport(wrong_type.encode("utf-8"))

        with self.assertRaises(SourceOperationError) as caught:
            CninfoSecurityDictionaryOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "security_type_mismatch")

    def test_non_current_sse_security_fails_closed(self) -> None:
        non_current = (
            Path(FIXTURES, "sse_600519.json")
            .read_text(encoding="utf-8")
            .replace('"STATE_CODE_A_DESC": "上市"', '"STATE_CODE_A_DESC": "终止上市"')
        )
        transport = FixtureTransport(non_current.encode("utf-8"))

        with self.assertRaises(SourceOperationError) as caught:
            SseStockListOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "security_not_current")

    def test_internally_inconsistent_sse_identity_fails_closed(self) -> None:
        inconsistent = (
            Path(FIXTURES, "sse_600519.json")
            .read_text(encoding="utf-8")
            .replace('"COMPANY_CODE": "600519"', '"COMPANY_CODE": "600000"')
        )
        transport = FixtureTransport(inconsistent.encode("utf-8"))

        with self.assertRaises(SourceOperationError) as caught:
            SseStockListOperation().observe("600519", transport)

        self.assertEqual(caught.exception.code, "inconsistent_identity_payload")

    def test_filtered_szse_wrong_security_payload_fails_closed(self) -> None:
        transport = FixtureTransport(Path(FIXTURES, "szse_000001.json").read_bytes())

        with self.assertRaises(SourceOperationError) as caught:
            SzseStockListOperation().observe("300750", transport)

        self.assertEqual(caught.exception.code, "wrong_security_payload")


class IdentityResolutionCliTests(unittest.TestCase):
    def run_resolve(
        self,
        query: str,
        scenario: str = "default",
        as_of: str = "2026-08-02",
    ) -> dict[str, object]:
        environment = os.environ.copy()
        environment["A_SHARE_RESEARCH_TEST_SCENARIO"] = scenario
        completed = subprocess.run(
            [
                sys.executable,
                str(FIXTURE_CLI),
                "resolve",
                "--query",
                query,
                "--as-of",
                as_of,
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_resolve_outputs_final_candidate_status_and_evidence(self) -> None:
        result = self.run_resolve("600519")

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["candidates"][0]["security"]["exchange"], "SSE")
        self.assertEqual(result["candidates"][0]["security"]["code"], "600519")
        self.assertEqual(
            result["candidates"][0]["issuer"]["security_relationship"],
            "verified",
        )
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["sse_stock_list@1", "cninfo_security_dictionary@1"],
        )

    def test_szse_and_cninfo_return_one_canonical_candidate(self) -> None:
        result = self.run_resolve("000001", "szse_success")

        self.assertEqual(result["status"], "limited")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["security"]["exchange"], "SZSE")
        self.assertEqual(candidate["security"]["code"], "000001")
        self.assertIsNone(candidate["issuer"]["name"])
        self.assertEqual(
            candidate["issuer"]["identifier"],
            {"scheme": "CNINFO_ORG_ID", "value": "gssz0000001"},
        )
        self.assertEqual(candidate["issuer"]["security_relationship"], "verified")
        self.assertEqual(
            [item["source_operation"] for item in result["evidence"]],
            ["szse_stock_list@1", "cninfo_security_dictionary@1"],
        )

    def test_exchange_qualified_clue_is_preserved_and_cross_checked(self) -> None:
        result = self.run_resolve("SSE:600519")

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["research"]["exchange_hint"], "SSE")
        self.assertEqual(result["research"]["normalized_clue"], "600519")

    def test_name_discovery_requeries_sse_by_cninfo_observed_code(self) -> None:
        result = self.run_resolve("贵州茅台", "sse_name")

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["candidates"][0]["security"]["exchange"], "SSE")

    def test_unknown_cninfo_market_remains_unresolved(self) -> None:
        result = self.run_resolve("中国平安", "unknown_org")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["limitations"][0]["code"], "identity_not_resolved")
        dictionary_evidence = next(
            item
            for item in result["evidence"]
            if item["source_operation"] == "cninfo_security_dictionary@1"
        )
        self.assertIsNone(dictionary_evidence["subject"]["security"])
        self.assertEqual(
            dictionary_evidence["subject"]["security_clue"],
            {"code": "601318"},
        )

    def test_current_sources_cannot_backfill_a_historical_identity(self) -> None:
        result = self.run_resolve("600519", as_of="2026-08-01")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["limitations"][0]["code"],
            "identity_observation_outside_research_date",
        )

    def test_contradictory_exchange_hint_blocks_without_discarding_evidence(
        self,
    ) -> None:
        result = self.run_resolve("SSE:000001", "exchange_hint_conflict")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["conflicts"][0]["code"], "exchange_hint_conflict")

    def test_unsupported_bse_is_zero_exit_blocked_json(self) -> None:
        for clue in ("920000", "832000", "430047", "870299", "BSE:920000"):
            with self.subTest(clue=clue):
                result = self.run_resolve(clue)

                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["candidates"], [])
                self.assertEqual(
                    result["limitations"][0]["code"], "unsupported_exchange"
                )

    def test_bse_name_discovered_by_cninfo_is_unsupported(self) -> None:
        result = self.run_resolve("安徽凤凰", "bse_name")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["limitations"][0]["code"], "unsupported_exchange")

    def test_invalid_clues_block_before_network(self) -> None:
        for clue in ("", "   ", "60051", "HKEX:600519"):
            with self.subTest(clue=clue):
                result = self.run_resolve(clue)
                self.assertEqual(
                    result["limitations"][0]["code"], "invalid_security_clue"
                )

    def test_multiple_cross_checked_candidates_require_clarification(self) -> None:
        result = self.run_resolve("同名股份", "multiple_candidates")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["limitations"][0]["code"], "ambiguous_security_clue")

    def test_source_name_conflict_is_preserved(self) -> None:
        result = self.run_resolve("600519", "name_conflict")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["conflicts"][0]["code"], "source_identity_conflict")

    def test_source_exchange_conflict_is_preserved(self) -> None:
        result = self.run_resolve("600519", "exchange_conflict")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["conflicts"][0]["code"], "source_identity_conflict")

    def test_source_operation_failure_is_a_blocked_domain_result(self) -> None:
        result = self.run_resolve("600519", "source_failure")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["source_errors"][0]["code"], "empty_response")
        self.assertEqual(result["limitations"][0]["code"], "source_operation_failed")


if __name__ == "__main__":
    unittest.main()
