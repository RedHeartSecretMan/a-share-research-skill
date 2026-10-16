from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from copy import deepcopy

from tests.test_direct_research_workflow_runtime import (
    _rehash_workflow,
    _validate_serialized,
)

from tests.test_market_overview_research import (
    invoke_copied_installed,
    market_overview_request,
)


class MarketOverviewV2Tests(unittest.TestCase):
    def test_public_workflow_cuts_over_to_v2_and_rejects_v1(self) -> None:
        request = market_overview_request()
        request["parameters"]["workflow"]["version"] = "2.0"
        completed = invoke_copied_installed(request)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["plan"]["version"], "2.0")
        request["parameters"]["workflow"]["version"] = "1.0"
        completed = invoke_copied_installed(request)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("version is not supported", completed.stderr)

    def test_fixed_inventory_has_fourteen_executable_capabilities(self) -> None:
        request = market_overview_request()
        request["parameters"]["workflow"]["version"] = "2.0"
        completed = invoke_copied_installed(request)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        inventory = result["capability_registry"]["workflow"]["capability_inventory"]
        self.assertEqual(len(inventory), 15)
        self.assertEqual(sum(item["state"] == "supported" for item in inventory), 14)
        self.assertEqual(
            [
                item["capability_id"]
                for item in inventory
                if item["state"] == "unsupported"
            ],
            ["market_wide_intraday"],
        )

    def test_completed_totals_keep_three_exchange_lineage_and_exact_units(self) -> None:
        result = self._run()
        turnover = next(
            item
            for item in result["calculations"]
            if item["kind"] == "whole_market_turnover"
        )
        self.assertEqual(turnover["payload"]["value"], "6750005000")
        self.assertEqual(turnover["payload"]["unit"], "CNY")
        observations = {item["id"]: item for item in result["observations"]}
        self.assertEqual(
            [
                observations[item]["payload"]["observation"]["exchange"]
                for item in turnover["input_ids"]
            ],
            ["SSE", "SZSE", "BSE"],
        )
        margin = {
            item["payload"]["metric"]: item["payload"]
            for item in result["calculations"]
            if item["kind"] == "whole_market_margin"
        }
        self.assertEqual(margin["margin_total_balance_CNY"]["value"], "4537500")
        self.assertEqual(
            margin["securities_lending_quantity_shares"]["value"], "240000"
        )
        indices = [
            item["payload"]["payload"]["observation"]
            for item in result["observations"]
            if item["capability_id"] == "major_broad_indices"
        ]
        self.assertEqual(
            [item["canonical_id"] for item in indices],
            ["SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050"],
        )
        self.assertTrue(
            all(item["price_semantics"] == "completed_close" for item in indices)
        )

    def test_fast_has_only_core_obligations_and_explicit_deep_stays_unsupported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls = Path(temporary) / "calls.jsonl"
            result = self._run(
                inputs={
                    "completeness_mode": "fast",
                    "deep_extensions": ["market_wide_intraday"],
                },
                environment={"ASR96_CALLS": str(calls)},
            )
            executed = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(
            sum(
                item["operation_id"] == "market_session_context@1" for item in executed
            ),
            1,
        )
        self.assertFalse(any("margin" in item["operation_id"] for item in executed))
        index_call = next(
            item for item in executed if item["operation_id"] == "fixed_four_indices@1"
        )
        self.assertEqual(
            (index_call["purpose"], index_call["completed_basis"]),
            ("completed_session", "latest_index_session"),
        )
        step = result["capability_registry"]["steps"]["whole_market_margin"]
        self.assertIsNone(step["leaf_plan"])
        inventory = {
            item["capability_id"]: item
            for item in result["capability_registry"]["workflow"][
                "capability_inventory"
            ]
        }
        self.assertEqual(inventory["whole_market_margin"]["state"], "not_requested")
        self.assertEqual(
            inventory["market_wide_intraday"]["reason_code"],
            "free_anonymous_intraday_unproved",
        )
        self.assertEqual(inventory["market_wide_intraday"]["state"], "unsupported")
        self.assertNotIn("market_wide_intraday", result["coverage"]["steps"])
        for field in ("observations", "claims", "calculations", "source_errors"):
            self.assertFalse(
                any(
                    item.get("capability_id", item.get("kind"))
                    == "market_wide_intraday"
                    for item in result[field]
                )
            )

    def test_missing_or_ineligible_partition_preserves_siblings_without_market_total(
        self,
    ) -> None:
        for scenario, retained in (
            ("missing_bse_turnover", 2),
            ("wrong_unit", 2),
            ("no_proxy", 0),
        ):
            with self.subTest(scenario=scenario):
                result = self._run(environment={"ASR96_SCENARIO": scenario})
                observations = [
                    item
                    for item in result["observations"]
                    if item["capability_id"] == "whole_market_turnover"
                ]
                self.assertEqual(len(observations), retained)
                self.assertFalse(
                    any(
                        item["kind"] == "whole_market_turnover"
                        for item in result["calculations"]
                    )
                )
                self.assertTrue(
                    any(
                        item["capability_id"] == "whole_market_margin"
                        for item in result["observations"]
                    )
                )
                self.assertNotEqual(result["status"], "supported")

    def test_incomplete_fixed_set_and_missing_calendar_remain_local_failures(
        self,
    ) -> None:
        for scenario, count in (
            ("missing_bse_index", 3),
            ("stale_index", 3),
            ("missing_bse_session", 0),
        ):
            with self.subTest(scenario=scenario):
                result = self._run(environment={"ASR96_SCENARIO": scenario})
                self.assertEqual(
                    sum(
                        item["capability_id"] == "major_broad_indices"
                        for item in result["observations"]
                    ),
                    count,
                )
                self.assertTrue(result["source_errors"])
                if scenario != "missing_bse_session":
                    self.assertTrue(
                        any(
                            item["capability_id"] == "major_broad_indices"
                            for item in result["source_errors"]
                        )
                    )
                if scenario == "missing_bse_session":
                    self.assertFalse(
                        any(
                            item["capability_id"] == "whole_market_turnover"
                            for item in result["observations"]
                        )
                    )

    def test_weekend_uses_proved_prior_completed_day_and_original_cutoff(self) -> None:
        result = self._run(as_of="2026-08-01")
        turnover = next(
            item
            for item in result["calculations"]
            if item["kind"] == "whole_market_turnover"
        )
        self.assertEqual(turnover["payload"]["report_date"], "2026-07-31")
        session = next(
            item
            for item in result["observations"]
            if item["capability_id"] == "market_trading_session"
        )
        self.assertEqual(
            session["payload"]["payload"]["context"]["information_cutoff"],
            "2026-08-01T23:59:59+08:00",
        )

    def test_screen_request_rejected_before_sources_or_artifact_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "existing-artifact"
            artifact.mkdir()
            marker = artifact / "marker"
            marker.write_text("preserved")
            calls = root / "calls.jsonl"
            request = market_overview_request()
            request["parameters"] = {
                "workflow": {"id": "market_screen_research", "version": "1.0"},
                "inputs": {
                    "result_artifact": {"path": str(artifact), "mode": "resume"}
                },
            }
            completed = invoke_copied_installed(
                request, environment_overrides={"ASR96_CALLS": str(calls)}
            )
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertIn("id is not supported", completed.stderr)
            self.assertFalse(calls.exists())
            self.assertEqual(list(artifact.iterdir()), [marker])
            self.assertEqual(marker.read_text(), "preserved")

    def test_public_result_rejects_rehashed_market_proxy_or_false_total(self) -> None:
        original = self._run()
        for mutation in ("false_total", "proxy", "index_proxy", "wrong_report_day"):
            with self.subTest(mutation=mutation):
                result = deepcopy(original)
                if mutation == "false_total":
                    next(
                        item
                        for item in result["calculations"]
                        if item["kind"] == "whole_market_turnover"
                    )["payload"]["value"] = "1"
                elif mutation == "proxy":
                    next(
                        item
                        for item in result["observations"]
                        if item["capability_id"] == "whole_market_turnover"
                    )["payload"]["observation"]["asset_scope"] = "single_index"
                elif mutation == "index_proxy":
                    next(
                        item
                        for item in result["observations"]
                        if item["capability_id"] == "major_broad_indices"
                    )["payload"]["payload"]["observation"][
                        "canonical_id"
                    ] = "SSE:510300"
                else:
                    for row in result["observations"]:
                        if row["capability_id"] == "whole_market_turnover":
                            row["payload"]["observation"]["report_date"] = "2026-07-31"
                    for calculation in result["calculations"]:
                        if calculation["kind"] == "whole_market_turnover":
                            calculation["payload"]["report_date"] = "2026-07-31"
                with self.assertRaises(ValueError):
                    _validate_serialized(_rehash_workflow(result))

    def _run(self, *, inputs=None, environment=None, as_of=None):
        request = market_overview_request(inputs=inputs)
        if as_of is not None:
            request["as_of"] = as_of
        completed = invoke_copied_installed(request, environment_overrides=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def test_complete_empty_margin_keeps_partition_without_inventing_zero_totals(
        self,
    ) -> None:
        result = self._run(environment={"ASR96_SCENARIO": "empty_bse_margin"})
        rows = [
            row
            for row in result["observations"]
            if row["capability_id"] == "whole_market_margin"
        ]
        self.assertEqual(len(rows), 3)
        empty = next(
            row for row in rows if row["operation_id"] == "bse_market_margin@1"
        )
        self.assertEqual(empty["payload"]["observation"]["row_count"], 0)
        self.assertEqual(empty["payload"]["observation"]["metrics"], {})
        self.assertFalse(
            any(
                item["kind"] == "whole_market_margin" for item in result["calculations"]
            )
        )
        self.assertTrue(
            any(
                item["kind"] == "whole_market_turnover"
                for item in result["calculations"]
            )
        )

    def test_rehashed_claim_proxies_and_invalid_report_fields_fail_closed(self) -> None:
        original = self._run()
        for capability in (
            "market_trading_session",
            "major_broad_indices",
            "whole_market_turnover",
            "whole_market_margin",
        ):
            with self.subTest(capability=capability):
                result = deepcopy(original)
                claim = next(
                    item
                    for item in result["claims"]
                    if item["evidence_use"] == capability
                )
                claim["payload"] = {"canonical_id": "SSE:510300"}
                with self.assertRaises(ValueError):
                    _validate_serialized(_rehash_workflow(result))
        for field, value in (
            ("row_count", 999),
            ("units", {"turnover_amount_CNY": "index_point"}),
        ):
            with self.subTest(field=field):
                result = deepcopy(original)
                row = next(
                    item
                    for item in result["observations"]
                    if item["capability_id"] == "whole_market_turnover"
                )
                row["payload"]["observation"][field] = value
                with self.assertRaises(ValueError):
                    _validate_serialized(_rehash_workflow(result))

    def test_partial_margin_keeps_complete_money_fields_and_omits_quantity_totals(
        self,
    ) -> None:
        result = self._run(environment={"ASR96_SCENARIO": "partial_bse_margin"})
        totals = {
            item["payload"]["metric"]: item["payload"]
            for item in result["calculations"]
            if item["kind"] == "whole_market_margin"
        }
        self.assertEqual(totals["financing_balance_CNY"]["value"], "3615000")
        self.assertNotIn("securities_lending_quantity_shares", totals)
        self.assertNotIn("securities_lending_sell_quantity_shares", totals)
        self.assertEqual(
            result["coverage"]["steps"]["whole_market_margin"]["leaf"][
                "whole_market_margin"
            ]["state"],
            "partial",
        )

    def test_active_release_qualification_adds_only_published_owners_and_preserves_contracts(
        self,
    ) -> None:
        from tests.skill_imports import add_skill_scripts_to_path

        add_skill_scripts_to_path()
        from a_share_research.operation_release_matrix import (
            load_active_operation_qualification_manifest,
            load_operation_qualification_manifest,
        )
        from tests.test_market_overview_research import REPOSITORY_ROOT, SKILL_ROOT
        from tests.generate_core_operation_release_matrix import (
            validate_evidence_references,
        )

        current = json.loads(
            (
                REPOSITORY_ROOT
                / "docs/research/v0.4.0-overview-v2-operation-qualification.json"
            ).read_text()
        )
        historical = json.loads(
            (
                REPOSITORY_ROOT / "docs/research/v0.4.0-operation-qualification.json"
            ).read_text()
        )
        original = {
            row["producer_operation_id"]: row for row in historical["operations"]
        }
        qualifications = load_active_operation_qualification_manifest(current)
        validate_evidence_references(qualifications)
        self.assertEqual(len(qualifications), 52)
        self.assertEqual(
            {row["producer_operation_id"] for row in current["operations"]}
            - set(original),
            {"market_session_context@1", "fixed_four_indices@1"},
        )
        for row in current["operations"]:
            owner = row["producer_operation_id"]
            if owner in original:
                expected = (
                    {**original[owner], "disposition": "integrated"}
                    if original[owner]["disposition"] == "pending_dependency"
                    else original[owner]
                )
                self.assertEqual(row, expected)
        owner_records = json.loads(
            (
                SKILL_ROOT / "references/market-foundation-owner-qualification.json"
            ).read_text()
        )
        self.assertTrue(
            all(
                row["disposition"] == "pending_dependency"
                for row in owner_records["operations"]
            )
        )
        for mutation in ("experimental", "source_time"):
            forged = deepcopy(current)
            row = next(
                row
                for row in forged["operations"]
                if row["producer_operation_id"] == "bse_a_share_turnover@1"
            )
            if mutation == "experimental":
                row["disposition"] = "experimental"
            else:
                row["temporal_semantics"]["observation_boundary"] = (
                    "retrieval_proxies_fact_time"
                )
            with self.assertRaises(ValueError):
                load_operation_qualification_manifest(
                    forged,
                    expected_producer_operation_ids=tuple(
                        row["producer_operation_id"] for row in current["operations"]
                    ),
                )

    def test_partial_zero_rows_do_not_claim_explicit_empty_business_proof(self) -> None:
        result = self._run(environment={"ASR96_SCENARIO": "partial_empty_bse_margin"})
        operation = result["coverage"]["steps"]["whole_market_margin"]["leaf"][
            "whole_market_margin"
        ]["operations"]["bse_market_margin@1"]
        self.assertEqual(operation["state"], "partial")
        self.assertEqual(
            {field["reason"] for field in operation["field_dispositions"]},
            {"source_field_not_available"},
        )
        self.assertFalse(
            any(
                item["kind"] == "whole_market_margin" for item in result["calculations"]
            )
        )

    def test_poisoned_turnover_blocks_same_provider_margin_but_keeps_other_groups(
        self,
    ) -> None:
        result, calls = self._timeout_result("poisoned_sse_turnover")
        self.assertEqual(
            sum(item["operation_id"] == "sse_a_share_turnover@1" for item in calls), 1
        )
        self.assertFalse(
            any(item["operation_id"] == "sse_market_margin@1" for item in calls)
        )
        for owner in ("szse_market_margin@1", "bse_market_margin@1"):
            self.assertEqual(sum(item["operation_id"] == owner for item in calls), 1)
        margins = [
            item
            for item in result["observations"]
            if item["capability_id"] == "whole_market_margin"
        ]
        self.assertEqual(
            {item["operation_id"] for item in margins},
            {"szse_market_margin@1", "bse_market_margin@1"},
        )
        operation = result["coverage"]["steps"]["whole_market_margin"]["leaf"][
            "whole_market_margin"
        ]["operations"]["sse_market_margin@1"]
        self.assertEqual(operation["execution_state"], "skipped_policy")
        self.assertFalse(operation["observation_ids"])
        self.assertFalse(
            any(
                item["kind"] == "whole_market_margin" for item in result["calculations"]
            )
        )

    def test_cooperative_timeout_releases_provider_for_margin_without_adopting_late_report(
        self,
    ) -> None:
        result, calls = self._timeout_result("cancelled_sse_turnover")
        cancelled = [
            item
            for item in calls
            if item["operation_id"] == "sse-turnover-cancellation"
        ]
        self.assertEqual(
            cancelled,
            [{"operation_id": "sse-turnover-cancellation", "observed_cancelled": True}],
        )
        self.assertEqual(
            sum(item["operation_id"] == "sse_market_margin@1" for item in calls), 1
        )
        self.assertFalse(
            any(
                item["operation_id"] == "sse_a_share_turnover@1"
                for item in result["observations"]
            )
        )
        margins = [
            item
            for item in result["observations"]
            if item["capability_id"] == "whole_market_margin"
        ]
        self.assertEqual(len(margins), 3)
        self.assertTrue(
            any(
                item["kind"] == "whole_market_margin" for item in result["calculations"]
            )
        )

    def _timeout_result(self, scenario):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "calls.jsonl"
            result = self._run(
                environment={"ASR96_SCENARIO": scenario, "ASR96_CALLS": str(path)}
            )
            calls = [json.loads(line) for line in path.read_text().splitlines()]
        return result, calls
