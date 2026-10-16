from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.test_market_overview_research import invoke_copied_installed
from tests.test_market_signals import signal_request

BOOTSTRAP = (
    Path(__file__).parent / "fixtures/market_signals/pool_partial_sitecustomize.py"
).read_text()


class MarketPoolCoverageTests(unittest.TestCase):
    def test_schema_loss_below_limit_retains_typed_partial_envelope(self):
        request = signal_request(["strong_stock_theme"])
        request["parameters"]["limit"] = 10
        completed = invoke_copied_installed(request, bootstrap=BOOTSTRAP)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        material = result["coverage"]["market_signal_observations"]["operations"][
            "ths_strong_stock_theme@1"
        ]["diagnostics"]["signal_coverage"]["strong_stock_theme"]
        self.assertEqual(
            (
                material["state"],
                material["provider_total"],
                material["retained_count"],
                material["truncated"],
            ),
            ("partial", 2, 0, False),
        )
        self.assertEqual(material["source_error_codes"], ["unknown_schema"])
        self.assertFalse(result["observations"])
        self.assertTrue(result["source_errors"])

    def test_partial_nonempty_unknown_total_and_real_limit_remain_distinct(self):
        cases = (
            ({"retained": 1}, 10, "partial", 2, 1, False),
            ({"retained": 1, "total": 1}, 10, "partial", 1, 1, False),
            (
                {"retained": 1, "errors": [], "state": "observed_nonempty"},
                1,
                "observed_nonempty",
                2,
                1,
                True,
            ),
            (
                {"retained": 2, "errors": [], "state": "observed_nonempty"},
                10,
                "observed_nonempty",
                2,
                2,
                False,
            ),
            ({"retained": 1, "errors": []}, 10, "partial", 2, 1, False),
            (
                {"retained": 0, "total": None, "pages": None, "state": "indeterminate"},
                10,
                "indeterminate",
                None,
                0,
                False,
            ),
        )
        for case, limit, state, total, count, truncated in cases:
            with self.subTest(case=case, limit=limit):
                result = self._run(case, limit=limit)
                material = self._material(result)
                self.assertEqual(
                    (
                        material["state"],
                        material["provider_total"],
                        material["retained_count"],
                        material["truncated"],
                    ),
                    (state, total, count, truncated),
                )
                self.assertEqual(len(result["observations"]), count)

    def test_collected_count_without_provider_total_preserves_loss_and_limit(self):
        for count, limit, errors, state, truncated in (
            (0, 10, ["unknown_schema"], "partial", False),
            (1, 10, ["unknown_schema"], "partial", False),
            (1, 1, [], "observed_nonempty", True),
            (2, 10, [], "observed_nonempty", False),
        ):
            with self.subTest(count=count, limit=limit):
                result = self._run(
                    {"heat": True, "retained": count, "errors": errors, "state": state},
                    limit=limit,
                    signal_types=["market_heat"],
                )
                material = self._material(result, heat=True)
                self.assertIsNone(material["provider_total"])
                self.assertEqual(material["provider_total_status"], "not_exposed")
                self.assertEqual(material["collected_count"], 2)
                self.assertEqual(material["truncated"], truncated)

    def test_runtime_schema_rejection_preserves_raw_total_and_valid_sibling(self):
        result = self._run(
            {
                "retained": 2,
                "errors": [],
                "state": "observed_nonempty",
                "invalid_row": True,
            }
        )
        material = self._material(result)
        self.assertEqual(
            (
                material["state"],
                material["provider_total"],
                material["retained_count"],
                material["truncated"],
            ),
            ("partial", 2, 1, False),
        )
        self.assertIn("unknown_schema", material["source_error_codes"])
        self.assertEqual(len(result["observations"]), 1)

    def test_schema_loss_does_not_erase_an_independent_owner(self):
        result = self._run(
            {
                "owners": [
                    {"retained": 0},
                    {
                        "heat": True,
                        "retained": 1,
                        "collected": 1,
                        "errors": [],
                        "state": "observed_nonempty",
                    },
                ]
            },
            signal_types=["strong_stock_theme", "market_heat"],
        )
        self.assertEqual(self._material(result)["state"], "partial")
        self.assertEqual(
            self._material(result, heat=True)["state"], "observed_nonempty"
        )
        self.assertEqual(
            {item["operation_id"] for item in result["observations"]},
            {"ths_market_heat@1"},
        )

    def test_rehashed_count_and_truncation_forgery_is_rejected(self):
        from copy import deepcopy
        from tests.test_direct_research_workflow_runtime import (
            _rehash_workflow,
            _validate_serialized,
        )

        original = self._run({"retained": 1})
        for field, value in (
            ("truncated", True),
            ("retained_count", 2),
            ("provider_total", 0),
            ("provider_total", -1),
            ("collected_count", 0),
            ("state", "observed_empty"),
        ):
            with self.subTest(field=field, value=value):
                forged = deepcopy(original)
                self._material(forged)[field] = value
                with self.assertRaises(ValueError):
                    _validate_serialized(_rehash_workflow(forged))

    def test_unexplained_complete_count_gap_is_rejected(self):
        completed = self._invoke(
            {"retained": 1, "errors": [], "state": "observed_nonempty"}
        )
        self.assertEqual(completed.returncode, 2)
        self.assertFalse(completed.stdout)
        self.assertIn(
            "market signal pool coverage retention is invalid", completed.stderr
        )

    def test_boolean_retained_count_is_not_an_integer_count(self):
        from tests.test_direct_research_workflow_runtime import (
            _rehash_workflow,
            _validate_serialized,
        )

        result = self._run({"retained": 1})
        self._material(result)["retained_count"] = True
        with self.assertRaises(ValueError):
            _validate_serialized(_rehash_workflow(result))

    def _invoke(self, case, *, limit=10, signal_types=None):
        request = signal_request(signal_types or ["strong_stock_theme"])
        request["window"] = {"observed_from": "2026-08-02", "observed_to": "2026-08-02"}
        request["parameters"]["limit"] = limit
        return invoke_copied_installed(
            request,
            bootstrap=BOOTSTRAP,
            environment_overrides={"ASR_POOL_CASE": json.dumps(case)},
        )

    def _run(self, case, **kwargs):
        completed = self._invoke(case, **kwargs)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    @staticmethod
    def _material(result, *, heat=False):
        owner, signal = (
            ("ths_market_heat@1", "market_heat")
            if heat
            else ("ths_strong_stock_theme@1", "strong_stock_theme")
        )
        return result["coverage"]["market_signal_observations"]["operations"][owner][
            "diagnostics"
        ]["signal_coverage"][signal]
