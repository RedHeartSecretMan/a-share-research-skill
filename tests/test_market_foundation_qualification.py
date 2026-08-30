from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Mapping

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.operation_release_matrix import (  # noqa: E402
    InstallationLoadEvidence,
    LiveProbeEvidence,
    OperationQualificationEvidence,
    ReleasePlanSnapshot,
    build_operation_release_matrix,
    load_market_foundation_qualification_fixtures,
    load_operation_qualification_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_PATH = (
    REPOSITORY_ROOT / "docs" / "research" / "v0.4.0-operation-qualification.json"
)
TARGET_APPLICATIONS = (
    ("whole_market_turnover", "sse_a_share_turnover@1"),
    ("whole_market_turnover", "szse_a_share_turnover@1"),
    ("whole_market_turnover", "bse_a_share_turnover@1"),
    ("whole_market_margin", "sse_market_margin@1"),
    ("whole_market_margin", "szse_market_margin@1"),
    ("whole_market_margin", "bse_market_margin@1"),
)
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "skill"
    / "a-share-research"
    / "references"
    / "market-foundation-qualification-fixtures.json"
)
LONG_DECIMAL_SOURCE = "12345678901234567890123456789.1"
LONG_DECIMAL_CASES = {
    "bse_a_share_turnover@1": {
        "source_fields": {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": LONG_DECIMAL_SOURCE},
        "destination": "observations.payload.observation.metrics.turnover_amount_CNY",
        "exact": "123456789012345678901234567891000.0",
        "forged": "1.234567890123456789012345679E+32",
    },
    "bse_market_margin@1": {
        "source_fields": {"FINANCING_BALANCE": LONG_DECIMAL_SOURCE},
        "destination": "observations.payload.observation.metrics.financing_balance_CNY",
        "exact": "123456789012345678901234567891000.0",
        "forged": "1.234567890123456789012345679E+32",
    },
    "sse_a_share_turnover@1": {
        "source_fields": {
            "TRADE_AMT_01": LONG_DECIMAL_SOURCE,
            "TRADE_AMT_03": "0.8",
        },
        "destination": "observations.payload.observation.metrics.turnover_amount_CNY",
        "exact": "1234567890123456789012345678990000000.0",
        "forged": "1.234567890123456789012345679E+36",
    },
    "sse_market_margin@1": {
        "source_fields": {"FINANCING_BALANCE": LONG_DECIMAL_SOURCE},
        "destination": "observations.payload.observation.metrics.financing_balance_CNY",
        "exact": LONG_DECIMAL_SOURCE,
        "forged": "1.234567890123456789012345679E+28",
    },
    "szse_a_share_turnover@1": {
        "source_fields": {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": LONG_DECIMAL_SOURCE},
        "destination": "observations.payload.observation.metrics.turnover_amount_CNY",
        "exact": "123456789012345678901234567891000.0",
        "forged": "1.234567890123456789012345679E+32",
    },
    "szse_market_margin@1": {
        "source_fields": {"FINANCING_BALANCE": LONG_DECIMAL_SOURCE},
        "destination": "observations.payload.observation.metrics.financing_balance_CNY",
        "exact": LONG_DECIMAL_SOURCE,
        "forged": "1.234567890123456789012345679E+28",
    },
}
EXTREME_DECIMAL_CASES = {
    "bse_a_share_turnover@1": {
        "source_fields": {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "1e1000000"},
        "destination": "observations.payload.observation.metrics.turnover_amount_CNY",
        "exact": "1e1000004",
    },
    "bse_market_margin@1": {
        "source_fields": {"FINANCING_BALANCE": "1e1000000"},
        "destination": "observations.payload.observation.metrics.financing_balance_CNY",
        "exact": "1e1000004",
    },
    "sse_a_share_turnover@1": {
        "source_fields": {
            "TRADE_AMT_01": "1e1000000",
            "TRADE_AMT_03": "2e1000000",
        },
        "destination": "observations.payload.observation.metrics.turnover_amount_CNY",
        "exact": "3e1000008",
    },
    "sse_market_margin@1": {
        "source_fields": {"FINANCING_BALANCE": "1e1000000"},
        "destination": "observations.payload.observation.metrics.financing_balance_CNY",
        "exact": "1e1000000",
    },
    "szse_a_share_turnover@1": {
        "source_fields": {"A_SHARE_TURNOVER_TEN_THOUSAND_CNY": "1e1000000"},
        "destination": "observations.payload.observation.metrics.turnover_amount_CNY",
        "exact": "1e1000004",
    },
    "szse_market_margin@1": {
        "source_fields": {"FINANCING_BALANCE": "1e1000000"},
        "destination": "observations.payload.observation.metrics.financing_balance_CNY",
        "exact": "1e1000000",
    },
}
DECIMAL_CONTEXTS = ((6, 9, -9), (28, 99, -99), (80, 999, -999))


class MarketFoundationQualificationTests(unittest.TestCase):
    def _load_qualifications(self) -> tuple[OperationQualificationEvidence, ...]:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        return load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=tuple(
                item["producer_operation_id"] for item in payload["operations"]
            ),
        )

    def _assert_decimal_cases_are_context_independent(
        self,
        cases: Mapping[str, Mapping[str, object]],
        *,
        reject_forged: bool,
    ) -> None:
        qualifications = self._load_qualifications()
        installed_fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for operation_id, case in cases.items():
            exact = deepcopy(installed_fixtures)
            fixture = next(
                item
                for item in exact["fixtures"]
                if item["producer_operation_id"] == operation_id
            )
            source_fields = case["source_fields"]
            destination = case["destination"]
            expected = case["exact"]
            assert isinstance(source_fields, dict)
            assert isinstance(destination, str)
            assert isinstance(expected, str)
            fixture["complete"]["source_fields"].update(source_fields)
            fixture["complete"]["normalized_destinations"][destination] = expected
            for precision, emax, emin in DECIMAL_CONTEXTS:
                with self.subTest(
                    operation=operation_id,
                    precision=precision,
                    emax=emax,
                    emin=emin,
                ):
                    with localcontext() as context:
                        context.prec = precision
                        context.Emax = emax
                        context.Emin = emin
                        loaded = load_market_foundation_qualification_fixtures(
                            exact,
                            qualifications,
                        )
                    self.assertEqual(len(loaded), 6)
            if reject_forged:
                forged_value = case["forged"]
                assert isinstance(forged_value, str)
                forged = deepcopy(exact)
                forged_fixture = next(
                    item
                    for item in forged["fixtures"]
                    if item["producer_operation_id"] == operation_id
                )
                forged_fixture["complete"]["normalized_destinations"][destination] = (
                    forged_value
                )
                with self.subTest(operation=operation_id, forged=True):
                    with self.assertRaisesRegex(ValueError, "normalization"):
                        load_market_foundation_qualification_fixtures(
                            forged,
                            qualifications,
                        )

    def _load_target_contract(self, operation_id: str) -> dict[str, object]:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        expected_ids = tuple(
            item["producer_operation_id"] for item in payload["operations"]
        )
        expected_applications = tuple(
            (capability_id, item["producer_operation_id"])
            for item in payload["operations"]
            for capability_id in item["capability_ids"]
        )
        qualifications = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=expected_ids,
            expected_operation_applications=expected_applications,
        )
        qualification = next(
            item
            for item in qualifications
            if item.producer_operation_id == operation_id
        )
        self.assertEqual(qualification.disposition, "pending_dependency")
        self.assertEqual(qualification.offline_contract_result, "passed")
        self.assertIsNotNone(qualification.source_contract)
        return dict(qualification.source_contract or {})

    def _assert_first_party_contract(
        self,
        operation_id: str,
        *,
        exchange: str,
        capability_id: str,
    ) -> dict[str, object]:
        contract = self._load_target_contract(operation_id)
        self.assertEqual(
            set(contract),
            {
                "producer_operation_id",
                "capability_id",
                "fixture_id",
                "provider",
                "scope",
                "request_selector",
                "response_shape",
                "field_destinations",
                "normalization",
                "field_applicability",
                "state_mapping",
                "dependency_policy",
                "license_policy",
            },
        )
        provider = contract["provider"]
        scope = contract["scope"]
        selector = contract["request_selector"]
        states = contract["state_mapping"]
        assert isinstance(provider, dict)
        assert isinstance(scope, dict)
        assert isinstance(selector, dict)
        assert isinstance(states, dict)
        self.assertEqual(provider["role"], "first_party_exchange")
        self.assertEqual(contract["producer_operation_id"], operation_id)
        self.assertEqual(contract["capability_id"], f"whole_market_{capability_id}")
        self.assertEqual(contract["fixture_id"], operation_id)
        self.assertEqual(scope["exchange"], exchange)
        self.assertIn(capability_id, scope["kind"])
        self.assertIn("single_security", scope["excluded_proxies"])
        self.assertLessEqual(selector["max_requests"], 2)
        self.assertEqual(selector["max_pages"], 1)
        self.assertEqual(
            set(states),
            {
                "complete",
                "observed_empty",
                "partial",
                "credential_blocked",
                "unavailable",
                "not_applicable",
            },
        )
        self.assertEqual(len(set(states.values())), 6)
        return contract

    def test_issue_114_sse_a_share_turnover_contract(self) -> None:
        operation_id = "sse_a_share_turnover@1"
        contract = self._assert_first_party_contract(
            operation_id,
            exchange="SSE",
            capability_id="turnover",
        )
        scope = contract["scope"]
        normalization = contract["normalization"]
        destinations = contract["field_destinations"]
        assert isinstance(scope, dict)
        assert isinstance(normalization, dict)
        assert isinstance(destinations, dict)
        self.assertIn("PRODUCT_CODE_01", scope["included_assets"])
        self.assertIn("PRODUCT_CODE_03", scope["included_assets"])
        self.assertIn("PRODUCT_CODE_02", scope["excluded_proxies"])
        self.assertIn("PRODUCT_CODE_11", scope["excluded_proxies"])
        self.assertIn("PRODUCT_CODE_17", scope["excluded_proxies"])
        self.assertIn("100000000", normalization["scale"])
        self.assertIn("turnover_amount_CNY", destinations["TRADE_AMT_01"])
        self.assertEqual(destinations["TRADE_AMT_01"], destinations["TRADE_AMT_03"])

    def test_issue_114_szse_a_share_turnover_contract(self) -> None:
        operation_id = "szse_a_share_turnover@1"
        contract = self._assert_first_party_contract(
            operation_id,
            exchange="SZSE",
            capability_id="turnover",
        )
        selector = contract["request_selector"]
        normalization = contract["normalization"]
        assert isinstance(selector, dict)
        assert isinstance(normalization, dict)
        self.assertIn("scsj_gprdgk_after", selector["selector_kind"])
        self.assertIn("10000", normalization["scale"])
        self.assertIn("A_share", normalization["asset_filter"])
        self.assertIn("index_proxy", normalization["asset_filter"])

    def test_issue_114_bse_a_share_turnover_contract(self) -> None:
        operation_id = "bse_a_share_turnover@1"
        contract = self._assert_first_party_contract(
            operation_id,
            exchange="BSE",
            capability_id="turnover",
        )
        selector = contract["request_selector"]
        normalization = contract["normalization"]
        assert isinstance(selector, dict)
        assert isinstance(normalization, dict)
        self.assertEqual(selector["max_requests"], 2)
        self.assertIn("daily_report", selector["selector_kind"])
        self.assertIn("10000", normalization["scale"])

    def _assert_margin_contract(self, operation_id: str, exchange: str) -> None:
        contract = self._assert_first_party_contract(
            operation_id,
            exchange=exchange,
            capability_id="margin",
        )
        destinations = contract["field_destinations"]
        response = contract["response_shape"]
        normalization = contract["normalization"]
        states = contract["state_mapping"]
        assert isinstance(destinations, dict)
        assert isinstance(response, dict)
        assert isinstance(normalization, dict)
        assert isinstance(states, dict)
        self.assertEqual(
            set(destinations),
            {
                "FINANCING_BALANCE",
                "FINANCING_BUY_AMOUNT",
                "SECURITIES_LENDING_SELL_QUANTITY",
                "SECURITIES_LENDING_QUANTITY",
                "SECURITIES_LENDING_BALANCE",
                "MARGIN_TOTAL_BALANCE",
            },
        )
        self.assertIn("publication_time", response["required_fields"])
        self.assertIn("must_not_precede", response["publication_time_rule"])
        self.assertIn("not_applicable", normalization["field_absence"])
        self.assertIn("official_summary_rule", states["not_applicable"])
        self.assertNotEqual(states["not_applicable"], states["observed_empty"])
        self.assertNotEqual(states["observed_empty"], states["unavailable"])

    def test_issue_114_sse_market_margin_contract(self) -> None:
        operation_id = "sse_market_margin@1"
        self._assert_margin_contract(operation_id, "SSE")

    def test_issue_114_szse_market_margin_contract(self) -> None:
        operation_id = "szse_market_margin@1"
        self._assert_margin_contract(operation_id, "SZSE")

    def test_issue_114_bse_market_margin_contract(self) -> None:
        operation_id = "bse_market_margin@1"
        self._assert_margin_contract(operation_id, "BSE")

    def test_issue_114_authority_closes_exact_existing_plus_six_denominator(
        self,
    ) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        current_ids = tuple(
            item["producer_operation_id"] for item in payload["operations"]
        )
        existing_ids = tuple(
            operation_id
            for operation_id in current_ids
            if operation_id not in {producer for _, producer in TARGET_APPLICATIONS}
        )
        self.assertEqual(len(existing_ids), 44)

        expected_ids = (
            *existing_ids,
            *(producer for _, producer in TARGET_APPLICATIONS),
        )
        existing_applications = tuple(
            (capability_id, item["producer_operation_id"])
            for item in payload["operations"]
            if item["producer_operation_id"] in existing_ids
            for capability_id in item["capability_ids"]
        )
        qualifications = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=expected_ids,
            expected_operation_applications=(
                *existing_applications,
                *TARGET_APPLICATIONS,
            ),
        )

        self.assertEqual(len(qualifications), 50)
        self.assertEqual(
            {
                item.producer_operation_id
                for item in qualifications
                if item.producer_operation_id
                in {producer for _, producer in TARGET_APPLICATIONS}
            },
            {producer for _, producer in TARGET_APPLICATIONS},
        )

    def test_issue_114_authority_fails_closed_on_plan_and_contract_drift(self) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        expected_ids = tuple(
            item["producer_operation_id"] for item in payload["operations"]
        )
        expected_applications = tuple(
            (capability_id, item["producer_operation_id"])
            for item in payload["operations"]
            for capability_id in item["capability_ids"]
        )
        target_index = next(
            index
            for index, item in enumerate(payload["operations"])
            if item["producer_operation_id"] == "sse_a_share_turnover@1"
        )
        mutations = []
        missing = deepcopy(payload)
        del missing["operations"][target_index]
        mutations.append(missing)
        duplicated = deepcopy(payload)
        duplicated["operations"].append(
            deepcopy(duplicated["operations"][target_index])
        )
        mutations.append(duplicated)
        unknown = deepcopy(payload)
        unknown["operations"][target_index]["producer_operation_id"] = (
            "unknown_turnover@1"
        )
        mutations.append(unknown)
        unplanned = deepcopy(payload)
        unplanned["operations"].append(
            {
                **deepcopy(unplanned["operations"][0]),
                "producer_operation_id": "unplanned_source@1",
            }
        )
        mutations.append(unplanned)
        cross_capability = deepcopy(payload)
        cross_capability["operations"][target_index]["capability_ids"] = [
            "whole_market_margin"
        ]
        mutations.append(cross_capability)
        unsafe = deepcopy(payload)
        unsafe["operations"][target_index]["source_contract"]["provider"][
            "documentation_url"
        ] = "https://example.com/unreviewed"
        mutations.append(unsafe)
        missing_contract = deepcopy(payload)
        del missing_contract["operations"][target_index]["source_contract"]
        mutations.append(missing_contract)
        swapped_contracts = deepcopy(payload)
        margin_index = next(
            index
            for index, item in enumerate(payload["operations"])
            if item["producer_operation_id"] == "sse_market_margin@1"
        )
        (
            swapped_contracts["operations"][target_index]["source_contract"],
            swapped_contracts["operations"][margin_index]["source_contract"],
        ) = (
            swapped_contracts["operations"][margin_index]["source_contract"],
            swapped_contracts["operations"][target_index]["source_contract"],
        )
        mutations.append(swapped_contracts)

        for mutation in mutations:
            with self.subTest(operation_count=len(mutation["operations"])):
                with self.assertRaises(ValueError):
                    load_operation_qualification_manifest(
                        mutation,
                        expected_producer_operation_ids=expected_ids,
                        expected_operation_applications=expected_applications,
                    )

    def test_issue_114_source_urls_are_owner_bound_and_malformed_shapes_fail(
        self,
    ) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        expected_ids = tuple(
            item["producer_operation_id"] for item in payload["operations"]
        )
        expected_applications = tuple(
            (capability_id, item["producer_operation_id"])
            for item in payload["operations"]
            for capability_id in item["capability_ids"]
        )
        target_index = next(
            index
            for index, item in enumerate(payload["operations"])
            if item["producer_operation_id"] == "bse_a_share_turnover@1"
        )
        mutations: list[dict[str, object]] = []
        for section, key in (
            ("provider", "documentation_url"),
            ("provider", "terms_url"),
            ("request_selector", "resource_url"),
        ):
            cross_provider = deepcopy(payload)
            cross_provider["operations"][target_index]["source_contract"][section][
                key
            ] = "https://www.sse.com.cn/reviewed-but-wrong-exchange"
            mutations.append(cross_provider)
        for unsafe_url in (
            "https://www.bse.cn/path?access_token=redacted",
            "https://www.bse.cn:abc/path",
            "https://www.bse.cn:99999/path",
        ):
            malformed = deepcopy(payload)
            malformed["operations"][target_index]["source_contract"][
                "request_selector"
            ]["resource_url"] = unsafe_url
            mutations.append(malformed)
        coherent_provider_forgery = deepcopy(payload)
        forged_contract = coherent_provider_forgery["operations"][target_index][
            "source_contract"
        ]
        forged_contract["provider"].update(
            {
                "id": "sse",
                "organization": "Shanghai Stock Exchange",
                "documentation_url": "https://www.sse.com.cn/market/stockdata/overview/day/",
                "terms_url": "https://www.sse.com.cn/home/legal/",
            }
        )
        forged_contract["scope"]["exchange"] = "SSE"
        forged_contract["request_selector"]["resource_url"] = (
            "https://www.sse.com.cn/market/stockdata/overview/day/"
        )
        mutations.append(coherent_provider_forgery)
        same_host_unreviewed_path = deepcopy(payload)
        same_host_unreviewed_path["operations"][target_index]["source_contract"][
            "request_selector"
        ]["resource_url"] = "https://www.bse.cn/unreviewed-resource"
        mutations.append(same_host_unreviewed_path)

        for mutation in mutations:
            with self.subTest(
                resource=mutation["operations"][target_index]["source_contract"]
            ):
                with self.assertRaises(ValueError):
                    load_operation_qualification_manifest(
                        mutation,
                        expected_producer_operation_ids=expected_ids,
                        expected_operation_applications=expected_applications,
                    )

    def test_issue_114_nested_sensitive_source_policies_fail_closed(self) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        expected_ids = tuple(
            item["producer_operation_id"] for item in payload["operations"]
        )
        target_index = next(
            index
            for index, item in enumerate(payload["operations"])
            if item["producer_operation_id"] == "sse_market_margin@1"
        )
        for policy_name in ("dependency_policy", "license_policy"):
            mutation = deepcopy(payload)
            mutation["operations"][target_index]["source_contract"][policy_name][
                "access_token"
            ] = "redacted"
            with self.subTest(policy=policy_name):
                with self.assertRaises(ValueError):
                    load_operation_qualification_manifest(
                        mutation,
                        expected_producer_operation_ids=expected_ids,
                    )

    def test_issue_114_sse_destination_split_fails_closed(self) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        sse_contract = next(
            item["source_contract"]
            for item in payload["operations"]
            if item["producer_operation_id"] == "sse_a_share_turnover@1"
        )
        split_destination = (
            "observations.payload.observation.metrics.turnover_partition_03_CNY"
        )
        sse_contract["field_destinations"]["TRADE_AMT_03"] = split_destination
        with self.assertRaisesRegex(ValueError, "authority drifted"):
            load_operation_qualification_manifest(
                payload,
                expected_producer_operation_ids=tuple(
                    item["producer_operation_id"] for item in payload["operations"]
                ),
            )

    def test_issue_114_loader_works_from_a_copied_installed_skill(self) -> None:
        payload = QUALIFICATION_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = root / "a-share-research"
            shutil.copytree(REPOSITORY_ROOT / "skill" / "a-share-research", installed)
            manifest = root / "qualification.json"
            manifest.write_text(payload, encoding="utf-8")
            probe = (
                "import json,sys;"
                f"sys.path.insert(0,{str(installed / 'scripts')!r});"
                "from a_share_research.operation_release_matrix import load_market_foundation_qualification_fixtures,load_operation_qualification_manifest;"
                f"p=json.load(open({str(manifest)!r},encoding='utf-8'));"
                "ids=tuple(x['producer_operation_id'] for x in p['operations']);"
                "q=load_operation_qualification_manifest(p,expected_producer_operation_ids=ids);"
                f"f=json.load(open({str(installed / 'references' / 'market-foundation-qualification-fixtures.json')!r},encoding='utf-8'));"
                "fixtures=load_market_foundation_qualification_fixtures(f,q);"
                "print(json.dumps({'count':len(q),'contracts':sum(x.source_contract is not None for x in q),'fixtures':len(fixtures)},sort_keys=True))"
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-c", probe],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"contracts": 6, "count": 50, "fixtures": 6},
        )

    def test_issue_114_installed_fixtures_prove_exact_normalization_and_states(
        self,
    ) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        qualifications = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=tuple(
                item["producer_operation_id"] for item in payload["operations"]
            ),
        )
        fixtures = load_market_foundation_qualification_fixtures(
            json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
            qualifications,
        )

        self.assertEqual(
            {fixture["producer_operation_id"] for fixture in fixtures},
            {producer for _, producer in TARGET_APPLICATIONS},
        )
        for fixture in fixtures:
            with self.subTest(operation=fixture["producer_operation_id"]):
                complete = fixture["complete"]
                observed_empty = fixture["observed_empty"]
                unavailable = fixture["unavailable"]
                self.assertTrue(complete["query_complete"])
                if fixture["capability_id"] == "whole_market_turnover":
                    self.assertTrue(observed_empty["query_complete"])
                    self.assertEqual(observed_empty["state"], "observed_empty")
                    self.assertTrue(
                        all(
                            Decimal(value).is_zero()
                            for value in observed_empty["source_fields"].values()
                        )
                    )
                    self.assertTrue(
                        all(
                            Decimal(value).is_zero()
                            for value in observed_empty[
                                "normalized_destinations"
                            ].values()
                        )
                    )
                else:
                    self.assertTrue(observed_empty["query_complete"])
                    self.assertEqual(observed_empty["row_count"], 0)
                    self.assertEqual(observed_empty["state"], "observed_empty")
                self.assertEqual(observed_empty["report_date"], "2026-08-26")
                self.assertEqual(
                    observed_empty["published_at"],
                    "2026-08-26T18:00:00+08:00",
                )
                self.assertFalse(unavailable["query_complete"])
                self.assertEqual(unavailable["state"], "unavailable")
        fixture_by_owner = {
            fixture["producer_operation_id"]: fixture for fixture in fixtures
        }
        self.assertEqual(
            Decimal(
                fixture_by_owner["sse_a_share_turnover@1"]["complete"]["source_fields"][
                    "TRADE_AMT_01"
                ]
            )
            * Decimal("100000000")
            + Decimal(
                fixture_by_owner["sse_a_share_turnover@1"]["complete"]["source_fields"][
                    "TRADE_AMT_03"
                ]
            )
            * Decimal("100000000"),
            Decimal("3500000000"),
        )
        for operation_id in (
            "sse_market_margin@1",
            "szse_market_margin@1",
            "bse_market_margin@1",
        ):
            contract = self._load_target_contract(operation_id)
            applicability = contract["field_applicability"]
            assert isinstance(applicability, dict)
            self.assertEqual(applicability["not_applicable_fields"], "none")
            self.assertIn("all_six_fields", applicability["formal_rule"])
        szse_contract = self._load_target_contract("szse_market_margin@1")
        self.assertEqual(
            szse_contract["normalization"],
            {
                "field_absence": "map_required_field_absence_to_unavailable_never_not_applicable",
                "money_scale": "official_CNY_values_retained_as_exact_decimal_CNY",
                "quantity_scale": "official_share_values_retained_as_exact_decimal_shares",
            },
        )

    def test_issue_114_turnover_empty_requires_explicit_zero_partitions(self) -> None:
        qualifications = self._load_qualifications()
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for operation_id in (
            "bse_a_share_turnover@1",
            "sse_a_share_turnover@1",
            "szse_a_share_turnover@1",
        ):
            mutation = deepcopy(fixtures)
            fixture = next(
                item
                for item in mutation["fixtures"]
                if item["producer_operation_id"] == operation_id
            )
            fixture["observed_empty"] = {
                "query_complete": True,
                "row_count": 0,
                "state": "observed_empty",
            }
            with self.subTest(operation=operation_id):
                with self.assertRaisesRegex(ValueError, "empty fixture"):
                    load_market_foundation_qualification_fixtures(
                        mutation,
                        qualifications,
                    )
            nonzero = deepcopy(fixtures)
            nonzero_fixture = next(
                item
                for item in nonzero["fixtures"]
                if item["producer_operation_id"] == operation_id
            )
            source_field = next(
                iter(nonzero_fixture["observed_empty"]["source_fields"])
            )
            destination = next(
                iter(nonzero_fixture["observed_empty"]["normalized_destinations"])
            )
            nonzero_fixture["observed_empty"]["source_fields"][source_field] = "1"
            scale = 8 if operation_id.startswith("sse_") else 4
            nonzero_fixture["observed_empty"]["normalized_destinations"][
                destination
            ] = f"1e{scale}"
            with self.subTest(operation=operation_id, nonzero=True):
                with self.assertRaisesRegex(ValueError, "empty fixture"):
                    load_market_foundation_qualification_fixtures(
                        nonzero,
                        qualifications,
                    )

    def test_issue_114_publication_time_is_aware_and_not_before_report_date(
        self,
    ) -> None:
        qualifications = self._load_qualifications()
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for fixture_part in ("complete", "observed_empty"):
            for published_at in (
                "2026-08-26",
                "2026-08-26T18:00:00",
                "2026-08-25T18:00:00+08:00",
                "2026-08-26T10:00:00+00:00",
            ):
                mutation = deepcopy(fixtures)
                mutation["fixtures"][0][fixture_part]["published_at"] = published_at
                with self.subTest(
                    fixture_part=fixture_part,
                    published_at=published_at,
                ):
                    with self.assertRaisesRegex(ValueError, "time is invalid"):
                        load_market_foundation_qualification_fixtures(
                            mutation,
                            qualifications,
                        )

    def test_issue_114_unavailable_reason_is_owner_bound(self) -> None:
        qualifications = self._load_qualifications()
        fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        for operation_id in {producer for _, producer in TARGET_APPLICATIONS}:
            mutation = deepcopy(fixtures)
            fixture = next(
                item
                for item in mutation["fixtures"]
                if item["producer_operation_id"] == operation_id
            )
            fixture["unavailable"]["reason"] = "unrelated_safe_reason"
            with self.subTest(operation=operation_id):
                with self.assertRaisesRegex(ValueError, "unavailable fixture"):
                    load_market_foundation_qualification_fixtures(
                        mutation,
                        qualifications,
                    )

    def test_issue_114_sse_extreme_exponents_are_exact_and_context_independent(
        self,
    ) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        qualifications = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=tuple(
                item["producer_operation_id"] for item in payload["operations"]
            ),
        )
        fixture_payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        sse_fixture = next(
            item
            for item in fixture_payload["fixtures"]
            if item["producer_operation_id"] == "sse_a_share_turnover@1"
        )
        sse_fixture["complete"]["source_fields"] = {
            "TRADE_AMT_01": "1e1000000",
            "TRADE_AMT_03": "2e1000000",
        }
        destination = "observations.payload.observation.metrics.turnover_amount_CNY"
        sse_fixture["complete"]["normalized_destinations"][destination] = "3e1000008"

        for precision, emax, emin in ((6, 9, -9), (28, 99, -99), (80, 999, -999)):
            with self.subTest(precision=precision, emax=emax, emin=emin):
                with localcontext() as context:
                    context.prec = precision
                    context.Emax = emax
                    context.Emin = emin
                    loaded = load_market_foundation_qualification_fixtures(
                        fixture_payload,
                        qualifications,
                    )
                self.assertEqual(len(loaded), 6)

        forged = deepcopy(fixture_payload)
        forged_sse = next(
            item
            for item in forged["fixtures"]
            if item["producer_operation_id"] == "sse_a_share_turnover@1"
        )
        forged_sse["complete"]["normalized_destinations"][destination] = "1e1000008"
        with self.assertRaisesRegex(ValueError, "normalization"):
            load_market_foundation_qualification_fixtures(
                forged,
                qualifications,
            )

    def test_issue_114_all_six_owners_normalize_long_coefficients_exactly(
        self,
    ) -> None:
        self._assert_decimal_cases_are_context_independent(
            LONG_DECIMAL_CASES,
            reject_forged=True,
        )

    def test_issue_114_all_six_owners_accept_compact_extreme_exponents(
        self,
    ) -> None:
        self._assert_decimal_cases_are_context_independent(
            EXTREME_DECIMAL_CASES,
            reject_forged=False,
        )

    def test_issue_114_all_six_owners_reject_nonfinite_source_values(self) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        qualifications = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=tuple(
                item["producer_operation_id"] for item in payload["operations"]
            ),
        )
        installed_fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

        for operation_id in {producer for _, producer in TARGET_APPLICATIONS}:
            for value in ("NaN", "Infinity", "-Infinity"):
                mutation = deepcopy(installed_fixtures)
                fixture = next(
                    item
                    for item in mutation["fixtures"]
                    if item["producer_operation_id"] == operation_id
                )
                source_field = next(iter(fixture["complete"]["source_fields"]))
                fixture["complete"]["source_fields"][source_field] = value
                with self.subTest(operation=operation_id, value=value):
                    with self.assertRaisesRegex(ValueError, "decimal is invalid"):
                        load_market_foundation_qualification_fixtures(
                            mutation,
                            qualifications,
                        )

    def test_issue_114_matrix_projects_six_plan_obligations_from_authority(
        self,
    ) -> None:
        payload = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
        all_ids = tuple(item["producer_operation_id"] for item in payload["operations"])
        qualifications = load_operation_qualification_manifest(
            payload,
            expected_producer_operation_ids=all_ids,
        )
        target_qualifications = tuple(
            item
            for item in qualifications
            if item.producer_operation_id
            in {producer for _, producer in TARGET_APPLICATIONS}
        )
        active_qualification = next(
            item
            for item in qualifications
            if item.producer_operation_id == "cls_market_flash@1"
        )
        profile = "issue-114-market-foundation"
        coverage: dict[str, object] = {}
        obligations = []
        exchanges = {
            producer: producer.split("_", 1)[0].upper()
            for _, producer in TARGET_APPLICATIONS
        }
        for capability_id in {capability for capability, _ in TARGET_APPLICATIONS}:
            operations = {}
            for _, producer in (
                application
                for application in TARGET_APPLICATIONS
                if application[0] == capability_id
            ):
                obligations.append(
                    {
                        "capability_id": capability_id,
                        "operation_id": producer,
                        "producer_operation_id": producer,
                        "operation_kind": "external_source",
                        "source_role": "market_observation",
                        "experimental": False,
                        "exchange": exchanges[producer],
                        "evidence_uses": [{"use": capability_id, "role": "supporting"}],
                    }
                )
                operations[producer] = {
                    "capability_id": capability_id,
                    "operation_id": producer,
                    "execution_state": "not_requested",
                    "state": "not_requested",
                    "query_complete": None,
                    "source_error_codes": [],
                    "observation_ids": [],
                    "source_error_ids": [],
                    "field_dispositions": [
                        {
                            "field": "qualified_source_contract",
                            "disposition": "normalized",
                            "destination": "observations[].payload.observation",
                        }
                    ],
                    "diagnostics": {},
                }
            coverage[capability_id] = {
                "status": "observed_empty",
                "operations": operations,
            }
        obligations.append(
            {
                "capability_id": "research_content_materials",
                "operation_id": "cls_market_flash@1",
                "producer_operation_id": "cls_market_flash@1",
                "operation_kind": "external_source",
                "source_role": "research_material",
                "experimental": False,
                "evidence_uses": [
                    {"use": "research_content_materials", "role": "supporting"}
                ],
            }
        )
        coverage["research_content_materials"] = {
            "status": "observed_empty",
            "operations": {
                "cls_market_flash@1": {
                    "capability_id": "research_content_materials",
                    "operation_id": "cls_market_flash@1",
                    "execution_state": "succeeded",
                    "state": "observed_empty",
                    "query_complete": True,
                    "source_error_codes": [],
                    "observation_ids": [],
                    "source_error_ids": [],
                    "field_dispositions": [
                        {
                            "field": "qualified_active_control",
                            "disposition": "normalized",
                            "destination": "observations[].payload.observation",
                        }
                    ],
                    "diagnostics": {},
                }
            },
        }
        snapshot = ReleasePlanSnapshot(
            result={
                "schema_version": "1.0",
                "plan": {
                    "id": "market-foundation-qualification",
                    "version": "1.0",
                    "task_type": "market_foundation_qualification",
                    "capabilities": sorted(coverage),
                    "operation_obligations": obligations,
                    "research_dependencies": [],
                },
                "coverage": coverage,
            },
            request_profile=profile,
        )
        installs = (
            *(
                InstallationLoadEvidence(
                    producer_operation_id=producer,
                    origin="copied_install_plan",
                    status="complete",
                    selector=profile,
                )
                for _, producer in TARGET_APPLICATIONS
            ),
            InstallationLoadEvidence(
                producer_operation_id="cls_market_flash@1",
                origin="copied_install_plan",
                status="complete",
                selector=profile,
            ),
        )
        probes = (
            *(
                LiveProbeEvidence(
                    producer_operation_id=producer,
                    probe="tests/test_market_foundation_qualification.py",
                    probe_status="passed",
                    attempted=False,
                    source_result="unavailable",
                    verified_at="2026-08-27T16:00:00+08:00",
                )
                for _, producer in TARGET_APPLICATIONS
            ),
            LiveProbeEvidence(
                producer_operation_id="cls_market_flash@1",
                probe="tests/test_market_foundation_qualification.py",
                probe_status="passed",
                attempted=True,
                source_result="observed_empty",
                verified_at="2026-08-27T16:00:00+08:00",
            ),
        )
        matrix_inputs = {
            "plan_snapshots": (snapshot,),
            "qualification_evidence": (
                *target_qualifications,
                active_qualification,
            ),
            "installation_loads": installs,
            "installation_health": {
                "schema_version": "1.0",
                "status": "complete",
                "dependencies": [],
                "operations": [],
                "source_policy": {
                    "source_accessibility": "not_checked",
                    "credentials": "not_checked",
                    "evidence_adoption": "not_checked",
                },
            },
            "live_probes": probes,
        }
        with self.assertRaisesRegex(ValueError, "normalization fixtures"):
            build_operation_release_matrix(**matrix_inputs)
        matrix = build_operation_release_matrix(
            **matrix_inputs,
            qualification_fixtures=json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
        ).to_dict()

        self.assertEqual(matrix["denominator"]["operation_count"], 7)
        self.assertEqual(
            {
                item["producer_operation_id"]
                for item in matrix["operations"]
                if item["disposition"] == "pending_dependency"
            },
            {producer for _, producer in TARGET_APPLICATIONS},
        )
        self.assertTrue(
            all(
                "source_contract" in item
                for item in matrix["operations"]
                if item["disposition"] == "pending_dependency"
            )
        )
        forged_active = (
            LiveProbeEvidence(
                producer_operation_id=probes[0].producer_operation_id,
                probe=probes[0].probe,
                probe_status=probes[0].probe_status,
                attempted=True,
                source_result="observed_empty",
                verified_at=probes[0].verified_at,
            ),
            *probes[1:],
        )
        with self.assertRaisesRegex(ValueError, "attempted"):
            build_operation_release_matrix(
                **{**matrix_inputs, "live_probes": forged_active},
                qualification_fixtures=json.loads(
                    FIXTURE_PATH.read_text(encoding="utf-8")
                ),
            )
        missing_contract = (
            replace(target_qualifications[0], source_contract=None),
            *target_qualifications[1:],
            active_qualification,
        )
        with self.assertRaisesRegex(ValueError, "source contract"):
            build_operation_release_matrix(
                **{
                    **matrix_inputs,
                    "qualification_evidence": missing_contract,
                },
                qualification_fixtures=json.loads(
                    FIXTURE_PATH.read_text(encoding="utf-8")
                ),
            )


if __name__ == "__main__":
    unittest.main()
