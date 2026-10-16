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

    def test_fixed_inventory_has_twelve_executable_capabilities(self) -> None:
        request = market_overview_request()
        request["parameters"]["workflow"]["version"] = "2.0"
        completed = invoke_copied_installed(request)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        inventory = result["capability_registry"]["workflow"]["capability_inventory"]
        self.assertEqual(len(inventory), 15)
        self.assertEqual(sum(item["state"] == "supported" for item in inventory), 12)
        self.assertEqual(
            [
                item["capability_id"]
                for item in inventory
                if item["state"] == "unsupported"
            ],
            ["whole_market_turnover", "whole_market_margin", "market_wide_intraday"],
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
        self.assertNotIn("whole_market_margin", result["capability_registry"]["steps"])
        inventory = {
            item["capability_id"]: item
            for item in result["capability_registry"]["workflow"][
                "capability_inventory"
            ]
        }
        self.assertEqual(inventory["whole_market_margin"]["state"], "unsupported")
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

    def test_withdrawn_slots_never_execute_or_enter_public_facts_in_either_mode(self):
        withdrawn = {
            "whole_market_turnover",
            "whole_market_margin",
            "market_wide_intraday",
        }
        for mode in ("fast", "standard"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                calls = Path(temporary) / "calls.jsonl"
                result = self._run(
                    inputs={
                        "completeness_mode": mode,
                        "deep_extensions": ["market_wide_intraday"],
                    },
                    environment={"ASR96_CALLS": str(calls)},
                )
                executed = [
                    json.loads(line)["operation_id"]
                    for line in calls.read_text().splitlines()
                ]
                self.assertEqual(
                    set(executed), {"market_session_context@1", "fixed_four_indices@1"}
                )
                inventory = result["capability_registry"]["workflow"][
                    "capability_inventory"
                ]
                active = [item for item in inventory if item["state"] == "supported"]
                self.assertEqual(len(active), 4 if mode == "fast" else 12)
                self.assertEqual(sum(item["tier"] == "core" for item in active), 4)
                for item in inventory:
                    if item["capability_id"] in withdrawn:
                        self.assertEqual(item["state"], "unsupported")
                        self.assertEqual(item["step_ids"], [])
                        self.assertEqual(item["gap_route"], "future_scope")
                        self.assertEqual(
                            item["reason_code"],
                            {
                                "whole_market_turnover": "free_anonymous_turnover_unproved",
                                "whole_market_margin": "free_anonymous_margin_unproved",
                                "market_wide_intraday": "free_anonymous_intraday_unproved",
                            }[item["capability_id"]],
                        )
                self.assertFalse(withdrawn.intersection(result["coverage"]["steps"]))
                self.assertFalse(
                    withdrawn.intersection(result["capability_registry"]["steps"])
                )
                for field in ("observations", "claims", "calculations"):
                    self.assertFalse(
                        any(
                            item.get("capability_id", item.get("kind")) in withdrawn
                            for item in result[field]
                        )
                    )

    def test_rehashed_withdrawn_capability_proxy_claim_is_rejected(self):
        original = self._run()
        for capability in (
            "whole_market_turnover",
            "whole_market_margin",
            "market_wide_intraday",
        ):
            with self.subTest(capability=capability):
                result = deepcopy(original)
                claim = next(
                    item
                    for item in result["claims"]
                    if item["kind"] == "major_broad_indices"
                )
                claim["payload"]["capability_id"] = capability
                with self.assertRaises(ValueError):
                    _validate_serialized(_rehash_workflow(result))

    def test_current_qualification_is_exact_legacy_plus_session_and_indices(self):
        from tests.test_market_overview_research import REPOSITORY_ROOT
        from tests.generate_core_operation_release_matrix import (
            validate_evidence_references,
        )
        from a_share_research.operation_release_matrix import (
            load_active_operation_qualification_manifest,
        )

        current = json.loads(
            (
                REPOSITORY_ROOT
                / "docs/research/v0.4.0-overview-v2-operation-qualification.json"
            ).read_text()
        )
        legacy = json.loads(
            (
                REPOSITORY_ROOT / "docs/research/v0.3.0-operation-qualification.json"
            ).read_text()
        )
        by_owner = {
            item["producer_operation_id"]: item for item in current["operations"]
        }
        old = {item["producer_operation_id"]: item for item in legacy["operations"]}
        self.assertEqual(
            set(by_owner) - set(old),
            {"market_session_context@1", "fixed_four_indices@1"},
        )
        self.assertTrue(set(old).issubset(by_owner))
        for owner, record in old.items():
            self.assertEqual(
                {
                    key: value
                    for key, value in by_owner[owner].items()
                    if key != "capability_ids"
                },
                record,
            )
        validate_evidence_references(
            load_active_operation_qualification_manifest(current)
        )

    def test_fixed_indices_keep_four_identities_and_completed_session_semantics(self):
        result = self._run()
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
        self.assertEqual(
            sum(
                item["capability_id"] == "market_trading_session"
                for item in result["observations"]
            ),
            1,
        )

    def _run(self, *, inputs=None, environment=None, as_of=None):
        request = market_overview_request(inputs=inputs)
        if as_of is not None:
            request["as_of"] = as_of
        completed = invoke_copied_installed(request, environment_overrides=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)
