from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capital_contract import (  # noqa: E402
    CapitalObservation,
    CapitalQuery,
    CapitalSourceBatch,
    CapitalSourceFailure,
)
from a_share_research.capital_events import build_capital_events_result  # noqa: E402
from a_share_research.capital_registry import (  # noqa: E402
    build_default_capital_operations,
)
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def capital_request(
    data_types: list[str],
    *,
    subjects: list[dict[str, str]] | None = None,
    observed_from: str = "2026-07-01",
    observed_to: str = "2026-08-02",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "capital_events",
        "subjects": subjects or [],
        "as_of": "2026-08-02",
        "window": {
            "observed_from": observed_from,
            "observed_to": observed_to,
        },
        "parameters": {"data_types": data_types, "limit": 20},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


class FixedCapitalOperation:
    operation_id = "fixed_capital@1"
    supported_data_types = frozenset(
        {"market_dragon_tiger", "lockup", "stock_fund_flow", "board_fund_flow"}
    )

    def __init__(self, observations: tuple[CapitalObservation, ...]) -> None:
        self._observations = observations

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        self.query = query
        observations = tuple(
            replace(item, subject=query.subject)
            if query.subject is not None and item.subject is None
            else item
            for item in self._observations
        )
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=observations,
            limitations=("pagination_incomplete",),
            complete=False,
        )


class BluefocusIdentityTransport:
    fixtures = Path(__file__).resolve().parent / "fixtures" / "identity_sources"

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlparse(url).netloc
        if host == "query.sse.com.cn":
            body = b'{"result":[]}'
        elif host == "www.szse.cn":
            body = (self.fixtures / "szse_300058.json").read_bytes()
        elif host == "www.cninfo.com.cn":
            body = (self.fixtures / "cninfo_current_orgs.json").read_bytes()
        else:
            raise AssertionError(f"unexpected identity URL: {url}")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=body,
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class MoutaiCapitalTransport:
    identity_fixtures = (
        Path(__file__).resolve().parent / "fixtures" / "identity_sources"
    )
    capital_fixtures = Path(__file__).resolve().parent / "fixtures" / "company_capital"

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlparse(url)
        if parsed.netloc == "query.sse.com.cn":
            body = (self.identity_fixtures / "sse_600519.json").read_bytes()
        elif parsed.netloc == "www.szse.cn":
            body = (self.identity_fixtures / "szse_empty.json").read_bytes()
        elif parsed.netloc == "www.cninfo.com.cn":
            body = (self.identity_fixtures / "cninfo_stocks.json").read_bytes()
        elif parsed.netloc == "datacenter-web.eastmoney.com":
            page = parse_qs(parsed.query).get("pageNumber", ["1"])[0]
            body = (self.capital_fixtures / f"margin_page_{page}.json").read_bytes()
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=body,
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


def observation(
    *,
    data_type: str = "market_dragon_tiger",
    source_role: str = "market_signal",
    observed_on: str = "2026-07-31",
    subject: dict[str, Any] | None = None,
    metrics: dict[str, str | None] | None = None,
    units: dict[str, str] | None = None,
    directions: dict[str, str] | None = None,
    limitations: tuple[str, ...] | None = None,
) -> CapitalObservation:
    selected_metrics = metrics or {"net_buy_amount": "123000000.00"}
    selected_limitations = limitations
    if selected_limitations is None:
        selected_limitations = (
            ("security_exchange_unverified",)
            if subject is None and data_type == "market_dragon_tiger"
            else ()
        )
    return CapitalObservation(
        data_type=data_type,
        source_operation="fixed_capital@1",
        source_role=source_role,
        subject=subject,
        observed_on=observed_on,
        available_at=f"{min(observed_on, '2026-08-02')}T18:00:00+08:00",
        retrieved_at=datetime(2026, 8, 2, 19, 30, tzinfo=CHINA_STANDARD_TIME),
        period={
            "start": observed_on,
            "end": observed_on,
            "frequency": "event",
        },
        metrics=selected_metrics,
        units=({key: "CNY" for key in selected_metrics} if units is None else units),
        directions=(
            {key: "positive_is_net_buy" for key in selected_metrics}
            if directions is None
            else directions
        ),
        dimensions={
            "security_code": "300058",
            **(
                {"market_scope": "fixture_all_market"}
                if subject is None
                and data_type
                in {"northbound_flow", "board_fund_flow", "market_dragon_tiger"}
                else {}
            ),
        },
        locator_uri="https://example.test/capital/1",
        limitations=selected_limitations,
    )


class CapitalEventsTests(unittest.TestCase):
    def test_identity_block_preserves_indeterminate_coverage_for_every_type(
        self,
    ) -> None:
        result = build_capital_events_result(
            capital_request(
                ["stock_fund_flow", "margin_trading"],
                subjects=[{"clue": "不存在的证券"}],
            ),
            [],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["brief"]["coverage"],
            {
                "stock_fund_flow": {
                    "state": "indeterminate",
                    "observation_count": 0,
                    "source_operations": [],
                },
                "margin_trading": {
                    "state": "indeterminate",
                    "observation_count": 0,
                    "source_operations": [],
                },
            },
        )

    def test_contract_complete_empty_type_is_distinct_from_source_failure(
        self,
    ) -> None:
        class CompleteEmptyNorthboundOperation:
            operation_id = "complete_empty_northbound@1"
            supported_data_types = frozenset({"northbound_flow"})

            def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                return CapitalSourceBatch(
                    operation_id=self.operation_id,
                    complete=True,
                )

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger", "northbound_flow"]),
            [
                FixedCapitalOperation((observation(),)),
                CompleteEmptyNorthboundOperation(),
            ],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["brief"]["coverage"]["northbound_flow"],
            {
                "state": "observed_empty",
                "observation_count": 0,
                "source_operations": ["complete_empty_northbound@1"],
            },
        )
        self.assertEqual(result["source_errors"], [])
        self.assertNotIn("requested_data_type_unavailable", _limitation_codes(result))
        self.assertNotIn("capital_events_unavailable", _limitation_codes(result))

    def test_one_unavailable_requested_type_blocks_without_dropping_other_results(
        self,
    ) -> None:
        class FailedMarginOperation:
            operation_id = "failed_margin@1"
            supported_data_types = frozenset({"margin_trading"})

            def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                return CapitalSourceBatch(
                    operation_id=self.operation_id,
                    source_errors=(
                        CapitalSourceFailure(
                            self.operation_id,
                            "upstream_unavailable",
                            "The source request could not be completed.",
                        ),
                    ),
                    complete=False,
                )

        stock_flow = observation(data_type="stock_fund_flow")
        result = build_capital_events_result(
            capital_request(
                ["stock_fund_flow", "margin_trading"],
                subjects=[{"clue": "蓝色光标"}],
            ),
            [FixedCapitalOperation((stock_flow,)), FailedMarginOperation()],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            [item["data_type"] for item in result["observations"]],
            ["stock_fund_flow"],
        )
        self.assertEqual(result["brief"]["data_type_counts"], {"stock_fund_flow": 1})
        self.assertEqual(
            result["brief"]["coverage"],
            {
                "stock_fund_flow": {
                    "state": "partial",
                    "observation_count": 1,
                    "source_operations": ["fixed_capital@1"],
                },
                "margin_trading": {
                    "state": "indeterminate",
                    "observation_count": 0,
                    "source_operations": ["failed_margin@1"],
                },
            },
        )
        self.assertEqual(
            [
                (item["source_operation"], item["code"])
                for item in result["source_errors"]
            ],
            [("failed_margin@1", "upstream_unavailable")],
        )
        self.assertEqual(
            next(
                item["data_types"]
                for item in result["limitations"]
                if item["code"] == "requested_data_type_unavailable"
            ),
            ["margin_trading"],
        )

    def test_unknown_availability_rejects_observation_retrieved_after_research_day(
        self,
    ) -> None:
        item = replace(
            observation(),
            available_at=None,
            retrieved_at=datetime(2026, 8, 3, 0, 30, tzinfo=CHINA_STANDARD_TIME),
            limitations=(
                "availability_time_unknown",
                "security_exchange_unverified",
            ),
        )

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [FixedCapitalOperation((item,))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["observations"], [])
        self.assertIn(
            "availability_time_unknown_outside_research_date",
            [error["code"] for error in result["source_errors"]],
        )

    def test_default_runtime_executes_margin_task_end_to_end(self) -> None:
        transport = MoutaiCapitalTransport()
        request = capital_request(
            ["margin_trading"],
            subjects=[{"clue": "贵州茅台"}],
            observed_from="2026-07-29",
            observed_to="2026-07-31",
        )
        request["parameters"]["limit"] = 3

        result = ResearchRuntime(
            identity_transport=transport,
            capital_transport=transport,
        ).research(request)

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["brief"]["observation_count"], 3)
        self.assertEqual(
            result["observations"][0]["metrics"]["financing_balance"],
            "18000000000.1200",
        )
        self.assertEqual(result["observations"][0]["units"]["financing_balance"], "CNY")

    def test_default_registry_covers_every_public_data_type(self) -> None:
        operations = build_default_capital_operations(BluefocusIdentityTransport())

        covered = set().union(
            *(operation.supported_data_types for operation in operations)
        )

        self.assertEqual(
            covered,
            {
                "northbound_flow",
                "stock_fund_flow",
                "board_fund_flow",
                "dragon_tiger",
                "market_dragon_tiger",
                "lockup",
                "margin_trading",
                "block_trade",
                "shareholder_count",
                "dividend",
            },
        )

    def test_runtime_routes_injected_capital_operation(self) -> None:
        operation = FixedCapitalOperation((observation(),))

        result = ResearchRuntime(
            identity_transport=BluefocusIdentityTransport(),
            capital_operations=[operation],
        ).research(capital_request(["market_dragon_tiger"]))

        self.assertEqual(result["task_type"], "capital_events")
        self.assertEqual(result["brief"]["observation_count"], 1)

    def test_runtime_blocks_capital_task_when_experimental_sources_are_forbidden(
        self,
    ) -> None:
        request = capital_request(["market_dragon_tiger"])
        request["source_policy"]["allow_experimental"] = False

        result = ResearchRuntime(
            identity_transport=BluefocusIdentityTransport(),
            capital_operations=[],
        ).research(request)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("source_policy_not_satisfied", _limitation_codes(result))

    def test_market_task_preserves_period_units_direction_and_unique_evidence(
        self,
    ) -> None:
        item = observation()
        operation = FixedCapitalOperation((item, item))

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [operation],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["units"], {"net_buy_amount": "CNY"})
        self.assertEqual(
            result["observations"][0]["directions"],
            {"net_buy_amount": "positive_is_net_buy"},
        )
        evidence_ids = [item["id"] for item in result["evidence"]]
        self.assertEqual(len(evidence_ids), len(set(evidence_ids)))
        self.assertIn("pagination_incomplete", _limitation_codes(result))

    def test_public_result_preserves_verified_source_ranking_order(self) -> None:
        first = replace(
            observation(),
            dimensions={
                "market_scope": "fixture_all_market",
                "net_buy_rank": 1,
                "provider_security_code": "300001",
            },
            locator_uri="https://example.test/capital/rank-1",
        )
        second = replace(
            observation(),
            dimensions={
                "market_scope": "fixture_all_market",
                "net_buy_rank": 2,
                "provider_security_code": "300999",
            },
            locator_uri="https://example.test/capital/rank-2",
        )

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [FixedCapitalOperation((first, second))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(
            [item["dimensions"]["net_buy_rank"] for item in result["observations"]],
            [1, 2],
        )

    def test_market_ranking_may_preserve_unresolved_provider_security(self) -> None:
        item = replace(
            observation(),
            subject=None,
            limitations=("security_exchange_unverified",),
        )

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [FixedCapitalOperation((item,))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertIsNone(result["observations"][0]["subject"])
        self.assertIn("security_exchange_unverified", _limitation_codes(result))

    def test_market_ranking_rejects_shape_only_canonical_security(self) -> None:
        item = replace(
            observation(),
            subject={
                "security": {
                    "exchange": "SZSE",
                    "code": "300058",
                    "type": "A_SHARE",
                },
                "name": "蓝色光标",
            },
            limitations=(),
        )

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [FixedCapitalOperation((item,))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["observations"], [])
        self.assertIn("subject", result["source_errors"][0]["invalid_fields"])

    def test_market_wide_observation_requires_machine_readable_scope(self) -> None:
        item = replace(observation(), dimensions={"provider_security_code": "300058"})

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [FixedCapitalOperation((item,))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("market_scope", result["source_errors"][0]["invalid_fields"])

    def test_rolling_board_period_accepts_unknown_start_with_exact_lookback(
        self,
    ) -> None:
        item = replace(
            observation(data_type="board_fund_flow"),
            period={
                "start": None,  # type: ignore[dict-item]
                "end": "2026-08-02",
                "frequency": "rolling_5_trading_days",
                "lookback_trading_days": "5",
            },
            limitations=("period_start_not_exposed",),
        )
        request = capital_request(
            ["board_fund_flow"],
            observed_from="2026-07-27",
            observed_to="2026-08-02",
        )

        result = build_capital_events_result(
            request,
            [FixedCapitalOperation((item,))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "limited")
        self.assertIsNone(result["observations"][0]["period"]["start"])
        self.assertEqual(
            result["observations"][0]["period"]["lookback_trading_days"], "5"
        )
        self.assertIn("period_start_not_exposed", _limitation_codes(result))

    def test_unknown_period_start_is_rejected_outside_exact_board_exception(
        self,
    ) -> None:
        base_period = {
            "start": None,  # type: ignore[dict-item]
            "end": "2026-08-02",
            "frequency": "rolling_5_trading_days",
            "lookback_trading_days": "5",
        }
        cases = (
            (
                observation(data_type="board_fund_flow"),
                (),
                capital_request(["board_fund_flow"]),
            ),
            (
                observation(data_type="board_fund_flow"),
                ("period_start_not_exposed",),
                capital_request(["board_fund_flow"]),
            ),
            (
                observation(data_type="stock_fund_flow"),
                ("period_start_not_exposed",),
                capital_request(
                    ["stock_fund_flow"],
                    subjects=[{"clue": "蓝色光标"}],
                ),
            ),
        )
        for index, (item, limitations, request) in enumerate(cases):
            with self.subTest(index=index):
                period = dict(base_period)
                if index == 1:
                    period["lookback_trading_days"] = "10"
                invalid = replace(item, period=period, limitations=limitations)

                result = build_capital_events_result(
                    request,
                    [FixedCapitalOperation((invalid,))],
                    BluefocusIdentityTransport(),
                )

                self.assertEqual(result["status"], "blocked")
                self.assertIn("period", result["source_errors"][0]["invalid_fields"])

    def test_public_limit_discloses_truncated_observation_type(self) -> None:
        first = observation(observed_on="2026-07-31")
        second = observation(observed_on="2026-07-30")
        request = capital_request(["market_dragon_tiger"])
        request["parameters"]["limit"] = 1

        result = build_capital_events_result(
            request,
            [FixedCapitalOperation((first, second))],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertIn("result_truncated_to_limit", _limitation_codes(result))

    def test_unknown_enum_and_metric_contract_are_isolated(self) -> None:
        invalid_role = observation(source_role="trusted_fact")
        invalid_metric = observation(units={})
        invalid_time = replace(observation(), available_at="not-a-time")
        operation = FixedCapitalOperation((invalid_role, invalid_metric, invalid_time))

        result = build_capital_events_result(
            capital_request(["market_dragon_tiger"]),
            [operation],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["observations"], [])
        self.assertEqual(
            [error["code"] for error in result["source_errors"]],
            ["unknown_schema", "unknown_schema", "unknown_schema"],
        )

    def test_subject_task_resolves_identity_and_passes_canonical_subject(self) -> None:
        operation = FixedCapitalOperation(
            (observation(data_type="stock_fund_flow", subject=None),)
        )

        result = build_capital_events_result(
            capital_request(
                ["stock_fund_flow"],
                subjects=[{"clue": "蓝色光标"}],
            ),
            [operation],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(result["subjects"][0]["security"]["code"], "300058")
        self.assertEqual(operation.query.subject["security"]["exchange"], "SZSE")

    def test_future_lockup_window_is_bounded_to_ninety_days(self) -> None:
        item = observation(data_type="lockup", observed_on="2026-10-30")
        operation = FixedCapitalOperation((item,))

        result = build_capital_events_result(
            capital_request(
                ["lockup"],
                subjects=[{"clue": "蓝色光标"}],
                observed_from="2026-08-02",
                observed_to="2026-10-31",
            ),
            [operation],
            BluefocusIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        with self.assertRaisesRegex(ValueError, "90 days"):
            build_capital_events_result(
                capital_request(
                    ["lockup"],
                    subjects=[{"clue": "蓝色光标"}],
                    observed_from="2026-08-02",
                    observed_to="2026-11-01",
                ),
                [operation],
                BluefocusIdentityTransport(),
            )

    def test_non_lockup_task_rejects_future_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "research date"):
            build_capital_events_result(
                capital_request(
                    ["market_dragon_tiger"],
                    observed_to="2026-08-03",
                ),
                [],
                BluefocusIdentityTransport(),
            )


def _limitation_codes(result: dict[str, Any]) -> set[str]:
    return {
        item["code"]
        for item in result["limitations"]
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


if __name__ == "__main__":
    unittest.main()
