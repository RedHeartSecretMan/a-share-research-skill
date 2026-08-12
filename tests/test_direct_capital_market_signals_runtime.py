from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from a_share_research.capital_registry import (  # noqa: E402
    build_default_capital_operations,
)
from a_share_research.market_signal_registry import (  # noqa: E402
    build_default_market_signal_operations,
)
from a_share_research.research_result import (  # noqa: E402
    UnifiedResearchResult,
    stable_result_id,
)

UNIFIED_RESULT_KEYS = {
    "schema_version",
    "status",
    "research_anchor",
    "plan",
    "capability_registry",
    "coverage",
    "observations",
    "claims",
    "calculations",
    "conflicts",
    "limitations",
    "source_errors",
    "task_views",
    "briefs",
}


def _rehash_direct_plan(result: dict[str, object]) -> None:
    plan = result["plan"]  # type: ignore[assignment]
    plan["id"] = stable_result_id(
        "plan",
        {
            "version": plan["version"],
            "task_type": plan["task_type"],
            "operation_obligations": plan["operation_obligations"],
            "research_dependencies": plan["research_dependencies"],
            "field_dispositions": [
                {
                    "capability_id": capability_id,
                    "operation_id": operation_id,
                    "fields": operation["field_dispositions"],
                }
                for capability_id, capability in result["coverage"].items()  # type: ignore[union-attr]
                for operation_id, operation in capability["operations"].items()
            ],
        },
    )


def _validate_serialized(result: dict[str, object]) -> None:
    UnifiedResearchResult(
        status=result["status"],  # type: ignore[arg-type]
        research_anchor=result["research_anchor"],  # type: ignore[arg-type]
        plan=result["plan"],  # type: ignore[arg-type]
        capability_registry=result["capability_registry"],  # type: ignore[arg-type]
        coverage=result["coverage"],  # type: ignore[arg-type]
        observations=tuple(result["observations"]),  # type: ignore[arg-type]
        claims=tuple(result["claims"]),  # type: ignore[arg-type]
        calculations=tuple(result["calculations"]),  # type: ignore[arg-type]
        conflicts=tuple(result["conflicts"]),  # type: ignore[arg-type]
        limitations=tuple(result["limitations"]),  # type: ignore[arg-type]
        source_errors=tuple(result["source_errors"]),  # type: ignore[arg-type]
        task_views=result["task_views"],  # type: ignore[arg-type]
        briefs=tuple(result["briefs"]),  # type: ignore[arg-type]
    ).to_dict()


def _destination_resolves(
    result: dict[str, object], operation_id: str, destination: str
) -> bool:
    if destination.startswith("coverage."):
        value: object = result
        for segment in destination.split("."):
            if not isinstance(value, dict) or segment not in value:
                return False
            value = value[segment]
        return True
    prefix = "observations[].payload."
    if not destination.startswith(prefix):
        return False
    path = destination.removeprefix(prefix).split(".")
    for record in result["observations"]:  # type: ignore[union-attr]
        if record["operation_id"] != operation_id:
            continue
        value = record["payload"]
        for segment in path:
            if not isinstance(value, dict) or segment not in value:
                break
            value = value[segment]
        else:
            return True
    return False


def _request(task_type: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": task_type,
        "subjects": [],
        "as_of": "2026-08-02",
        "window": {"observed_from": "2026-08-01", "observed_to": "2026-08-02"},
        "parameters": {
            ("data_types" if task_type == "capital_events" else "signal_types"): [
                "northbound_flow" if task_type == "capital_events" else "market_heat"
            ]
        },
        "source_policy": {
            "allow_experimental": False,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def _invoke_copied_installed(
    request: dict[str, object], *, bootstrap: str | None = None
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        installed = root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        command = [sys.executable, "-I"]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if bootstrap is not None:
            shutil.copytree(REPOSITORY_ROOT / "tests" / "fixtures", root / "fixtures")
            (root / "sitecustomize.py").write_text(bootstrap, encoding="utf-8")
            command = [sys.executable]
            environment["PYTHONPATH"] = str(root)
        completed = subprocess.run(
            [
                *command,
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return json.loads(completed.stdout)


class DirectCapitalMarketSignalsRuntimeTests(unittest.TestCase):
    def test_production_authorities_reject_rehashed_sibling_destinations(self) -> None:
        for task_type, capability_id in (
            ("capital_events", "capital_event_observations"),
            ("market_signals", "market_signal_observations"),
        ):
            with self.subTest(task_type=task_type):
                result = _invoke_copied_installed(_request(task_type))
                operations = result["coverage"][capability_id]["operations"]
                operation = next(iter(operations.values()))
                forged_destination = (
                    f"coverage.{capability_id}.operations."
                    f"{operation['operation_id']}.query_complete"
                )
                retained = next(
                    item
                    for item in operation["field_dispositions"]
                    if item["destination"] is not None
                    and item["destination"] != forged_destination
                )
                retained["destination"] = forged_destination
                _rehash_direct_plan(result)
                with self.assertRaisesRegex(ValueError, "field disposition contract"):
                    _validate_serialized(result)

    def test_copied_installed_policy_block_uses_one_unified_result(self) -> None:
        for task_type in ("capital_events", "market_signals"):
            with self.subTest(task_type=task_type):
                result = _invoke_copied_installed(_request(task_type))
                self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["task_views"][task_type]["status"], "blocked")

    def test_market_signal_owner_rejects_rehashed_operation_kind_forgery(
        self,
    ) -> None:
        from a_share_research.market_signal_contract import (
            MarketSignalQuery,
            SignalCoverage,
            SignalSourceBatch,
        )
        from a_share_research.market_signals import (
            COORDINATOR_OPERATION_ID,
            build_market_signals_result,
        )

        from tests.test_market_signals import NoIdentityTransport, signal_request

        class EmptyOwnerOperation:
            def __init__(self, owner: object) -> None:
                self.operation_id = owner.operation_id  # type: ignore[attr-defined]
                self.supported_signal_types = owner.supported_signal_types  # type: ignore[attr-defined]
                self.raw_fields = owner.raw_fields  # type: ignore[attr-defined]

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                relevant = self.supported_signal_types.intersection(query.signal_types)
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        signal_type: SignalCoverage("observed_empty")
                        for signal_type in relevant
                    },
                )

        class NoNetwork:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("owner construction must not use the network")

        owners = build_default_market_signal_operations(NoNetwork())  # type: ignore[arg-type]
        operations = tuple(
            EmptyOwnerOperation(owner)
            for owner in owners
            if owner.supported_signal_types.intersection(
                {"focus_monitoring", "severe_abnormal_movement"}
            )
        )
        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            operations,  # type: ignore[arg-type]
            NoIdentityTransport(),
        ).to_dict()
        obligations = result["plan"]["operation_obligations"]
        self.assertEqual(
            {item["operation_id"]: item["operation_kind"] for item in obligations},
            {
                "eastmoney_focus_monitoring@1": "external_source",
                "eastmoney_severe_abnormal_movement@1": "external_source",
                COORDINATOR_OPERATION_ID: "deterministic_coordinator",
            },
        )

        for target_id, forged_kind in (
            (COORDINATOR_OPERATION_ID, "external_source"),
            ("eastmoney_focus_monitoring@1", "deterministic_coordinator"),
        ):
            with self.subTest(target_id=target_id):
                forged = deepcopy(result)
                target = next(
                    item
                    for item in forged["plan"]["operation_obligations"]
                    if item["operation_id"] == target_id
                )
                target["operation_kind"] = forged_kind
                _rehash_direct_plan(forged)
                with self.assertRaisesRegex(
                    ValueError, "operation kind contradicts owner authority"
                ):
                    _validate_serialized(forged)

    def test_all_production_adapters_declare_owned_static_raw_fields(self) -> None:
        class NoNetworkTransport:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("registry construction must not use the network")

        transport = NoNetworkTransport()
        operations = (
            *build_default_capital_operations(transport),  # type: ignore[arg-type]
            *build_default_market_signal_operations(transport),  # type: ignore[arg-type]
        )

        self.assertEqual(len(operations), 15)
        self.assertEqual(len({item.operation_id for item in operations}), 15)
        for operation in operations:
            with self.subTest(operation_id=operation.operation_id):
                self.assertIsInstance(operation.raw_fields, tuple)
                self.assertTrue(operation.raw_fields)
                self.assertEqual(
                    len(operation.raw_fields), len(set(operation.raw_fields))
                )
                self.assertTrue(
                    all(isinstance(item, str) and item for item in operation.raw_fields)
                )

    def test_static_field_meaning_never_falls_back_to_operation_id(self) -> None:
        from a_share_research.capital_events import _capital_field_disposition
        from a_share_research.market_signals import (
            _market_signal_field_disposition,
        )

        for field in ("f3", "SECURITY_NAME_ABBR"):
            expected = _capital_field_disposition("fixture_owner_a@1", field)
            actual = _capital_field_disposition("eastmoney_stock_fund_flow@1", field)
            self.assertEqual(actual.disposition, expected.disposition)
            self.assertEqual(actual.destination, expected.destination)
        for field in ("f3", "m"):
            expected = _market_signal_field_disposition("fixture_owner_a@1", field)
            actual = _market_signal_field_disposition("eastmoney_limit_state@1", field)
            self.assertEqual(actual.disposition, expected.disposition)
            self.assertEqual(actual.destination, expected.destination)
        with self.assertRaisesRegex(ValueError, "unknown raw field"):
            _capital_field_disposition("fixture_owner_a@1", "unknown")
        with self.assertRaisesRegex(ValueError, "unknown raw field"):
            _market_signal_field_disposition("fixture_owner_a@1", "unknown")

    def test_all_production_dispositions_are_static_across_execution_states(
        self,
    ) -> None:
        from a_share_research.capital_contract import CapitalSourceFailure
        from a_share_research.capital_events import (
            _capital_failed_outcome,
            _capital_outcome,
            _capital_skipped_outcome,
        )
        from a_share_research.market_signal_contract import SignalSourceFailure
        from a_share_research.market_signals import (
            _market_signal_failed_outcome,
            _market_signal_outcome,
            _market_signal_skipped_outcome,
        )

        class NoNetwork:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("registry construction must not use the network")

        for operation in build_default_capital_operations(NoNetwork()):  # type: ignore[arg-type]
            skipped = _capital_skipped_outcome(operation, "not_requested")
            succeeded = _capital_outcome(operation, (), (), True)
            failed = _capital_failed_outcome(
                operation,
                CapitalSourceFailure(operation.operation_id, "fixture", "fixture"),
            )
            self.assertEqual(skipped.field_dispositions, succeeded.field_dispositions)
            self.assertEqual(skipped.field_dispositions, failed.field_dispositions)
        for operation in build_default_market_signal_operations(NoNetwork()):  # type: ignore[arg-type]
            skipped = _market_signal_skipped_outcome(operation, "not_requested")
            succeeded = _market_signal_outcome(operation, (), (), True)
            failed = _market_signal_failed_outcome(
                operation,
                SignalSourceFailure(operation.operation_id, "fixture", "fixture"),
            )
            self.assertEqual(skipped.field_dispositions, succeeded.field_dispositions)
            self.assertEqual(skipped.field_dispositions, failed.field_dispositions)

    def test_public_dispositions_exactly_close_raw_inventory_and_destinations(
        self,
    ) -> None:
        from a_share_research.capital_events import (
            build_capital_events_blocked_result,
            build_capital_events_result,
        )
        from a_share_research.market_signal_contract import (
            SignalCoverage,
            SignalSourceBatch,
        )
        from a_share_research.market_signals import (
            build_market_signals_blocked_result,
            build_market_signals_result,
        )
        from a_share_research.trading_event_sources import (
            EastmoneyTradingEventOperation,
        )

        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            capital_request,
        )
        from tests.test_market_signals import (
            FixedOperation,
            NoIdentityTransport,
            signal_request,
        )
        from tests.test_market_signals import (
            observation as signal_observation,
        )
        from tests.test_trading_event_sources import (
            FixedTransport,
            inline_response,
            no_wait_gate,
            response,
        )

        class NoNetwork:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("registry construction must not use the network")

        transport = NoNetwork()
        capital_operations = build_default_capital_operations(transport)  # type: ignore[arg-type]
        market_operations = build_default_market_signal_operations(transport)  # type: ignore[arg-type]
        blocked_results = (
            build_capital_events_blocked_result(
                _request("capital_events"), capital_operations
            ).to_dict(),
            build_market_signals_blocked_result(
                _request("market_signals"), market_operations
            ).to_dict(),
        )
        declared = {
            item.operation_id: set(item.raw_fields)
            for item in (*capital_operations, *market_operations)
        }
        public: dict[str, set[str]] = {}
        for result in blocked_results:
            for capability in result["coverage"].values():
                for operation_id, operation in capability["operations"].items():
                    public[operation_id] = {
                        item["field"] for item in operation["field_dispositions"]
                    }
        self.assertEqual(public, declared)

        trading = EastmoneyTradingEventOperation(
            FixedTransport(
                [response("block_trade.json"), inline_response({}, status=500)]
            ),
            request_gate=no_wait_gate(),
        )
        capital_result = build_capital_events_result(
            capital_request(["block_trade", "lockup"], subjects=[{"clue": "300058"}]),
            (trading,),
            BluefocusIdentityTransport(),
        ).to_dict()
        signal = signal_observation()
        market_result = build_market_signals_result(
            signal_request(["strong_stock_theme"]),
            (
                FixedOperation(
                    SignalSourceBatch(
                        operation_id="fixed_signal@1",
                        observations=(signal,),
                        coverage={
                            "strong_stock_theme": SignalCoverage("observed_nonempty")
                        },
                    )
                ),
            ),
            NoIdentityTransport(),
        ).to_dict()
        target_operations = {trading.operation_id, "fixed_signal@1"}
        for result in (capital_result, market_result):
            for capability in result["coverage"].values():
                for operation_id, operation in capability["operations"].items():
                    if (
                        operation_id not in target_operations
                        or not operation["observation_ids"]
                    ):
                        continue
                    for disposition in operation["field_dispositions"]:
                        if disposition["disposition"] == "excluded":
                            continue
                        with self.subTest(
                            operation_id=operation_id,
                            field=disposition["field"],
                        ):
                            self.assertTrue(
                                _destination_resolves(
                                    result, operation_id, disposition["destination"]
                                ),
                                disposition,
                            )

    def test_every_production_owner_success_destination_resolves(self) -> None:
        from a_share_research.capital_contract import (
            CapitalObservation,
            CapitalQuery,
            CapitalSourceBatch,
        )
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.market_signal_contract import (
            MarketSignalObservation,
            MarketSignalQuery,
            SignalCoverage,
            SignalSourceBatch,
            ThemeAttribution,
        )
        from a_share_research.market_signals import build_market_signals_result

        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            capital_request,
        )
        from tests.test_market_signals import NoIdentityTransport, signal_request

        class NoNetwork:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("registry construction must not use the network")

        now = datetime(2026, 8, 2, 19, 40, tzinfo=timezone(timedelta(hours=8)))
        capital_types = {
            "eastmoney_northbound_flow@1": "northbound_flow",
            "eastmoney_stock_fund_flow@1": "stock_fund_flow",
            "eastmoney_board_fund_flow@1": "board_fund_flow",
            "eastmoney_trading_events@1": "market_dragon_tiger",
            "eastmoney_margin_trading@1": "margin_trading",
            "eastmoney_shareholder_count@1": "shareholder_count",
            "eastmoney_dividend@1": "dividend",
        }
        capital_metrics = {
            key: "1"
            for key in (
                "net_buy_amount",
                "main_net_inflow",
                "small_order_net_inflow",
                "medium_order_net_inflow",
                "large_order_net_inflow",
                "super_large_order_net_inflow",
                "main_net_inflow_ratio",
                "price_change_ratio",
                "financing_balance",
                "financing_buy_amount",
                "financing_repayment_amount",
                "securities_lending_balance",
                "securities_lending_sell_volume",
                "securities_lending_repayment_volume",
                "margin_balance",
                "shareholder_count",
                "shareholder_count_change",
                "shareholder_count_change_ratio",
                "average_shares_per_holder",
                "cash_dividend_per_10_shares_before_tax",
                "bonus_shares_per_10_shares",
                "transfer_shares_per_10_shares",
            )
        }
        capital_dimensions = {
            key: "fixture"
            for key in (
                "connect_channel",
                "leader",
                "previous_period_end",
                "ex_dividend_date",
                "plan_notice_date",
                "report_period_end",
                "implementation_status",
                "record_date",
                "cash_payment_date",
                "provider_security_name",
                "board_code",
                "board_name",
            )
        }
        capital_dimensions["market_scope"] = "mainland_a_share"
        capital_dimensions["provider_security_code"] = "300058"
        for production in build_default_capital_operations(NoNetwork()):  # type: ignore[arg-type]
            data_type = capital_types[production.operation_id]
            subject_required = data_type not in {
                "northbound_flow",
                "board_fund_flow",
                "market_dragon_tiger",
            }

            class CapitalFixture:
                operation_id = production.operation_id
                supported_data_types = frozenset({data_type})
                raw_fields = production.raw_fields
                fixture_data_type = data_type

                def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                    query_subject = query.subject
                    limitations = (
                        ("security_exchange_unverified",)
                        if self.fixture_data_type == "market_dragon_tiger"
                        else ()
                    )
                    item = CapitalObservation(
                        data_type=self.fixture_data_type,
                        source_operation=self.operation_id,
                        source_role="market_observation",
                        subject=query_subject,
                        observed_on="2026-08-01",
                        available_at="2026-08-01T18:00:00+08:00",
                        retrieved_at=now,
                        period={
                            "start": "2026-08-01",
                            "end": "2026-08-01",
                            "frequency": "daily",
                        },
                        metrics=capital_metrics,
                        units={key: "unit" for key in capital_metrics},
                        directions={key: "not_directional" for key in capital_metrics},
                        dimensions=capital_dimensions,
                        locator_uri="https://provider.invalid/private",
                        limitations=limitations,
                    )
                    return CapitalSourceBatch(self.operation_id, observations=(item,))

            result = build_capital_events_result(
                capital_request(
                    [data_type],
                    subjects=[{"clue": "300058"}] if subject_required else [],
                    observed_from="2026-08-01",
                    observed_to="2026-08-01",
                ),
                (CapitalFixture(),),
                BluefocusIdentityTransport(),
            ).to_dict()
            operation = result["coverage"]["capital_event_observations"]["operations"][
                production.operation_id
            ]
            for disposition in operation["field_dispositions"]:
                if disposition["disposition"] != "excluded":
                    self.assertTrue(
                        _destination_resolves(
                            result, production.operation_id, disposition["destination"]
                        ),
                        (production.operation_id, disposition),
                    )

        signal_types = {
            "ths_strong_stock_theme@1": "strong_stock_theme",
            "eastmoney_security_board_membership@1": "security_board_membership",
            "eastmoney_industry_rotation@1": "industry_rotation",
            "ths_market_heat@1": "market_heat",
            "eastmoney_limit_state@1": "limit_state",
            "ths_limit_reason@1": "limit_state",
            "eastmoney_focus_monitoring@1": "focus_monitoring",
            "eastmoney_severe_abnormal_movement@1": "severe_abnormal_movement",
        }
        signal_metrics = {
            key: "1"
            for key in (
                "change_rate",
                "close_price",
                "turnover_rate",
                "trading_amount",
                "up_count",
                "down_count",
                "leader_change_rate",
                "board_change_rate",
                "price",
                "amount",
                "float_market_cap",
                "seal_fund",
                "limit_price",
                "amplitude",
                "speed",
                "pe",
                "board_amount",
                "consecutive_limit_days",
                "break_times",
                "consecutive_limit_down_days",
                "open_times",
                "previous_consecutive_limit_days",
                "seal_success_rate",
                "cumulative_deviation",
                "target_change_rate",
                "heat",
                "rank_change",
                "rank",
            )
        }
        signal_dimensions = {
            key: "fixture"
            for key in (
                "provider_security_code",
                "provider_security_name",
                "provider_market",
                "provider_market_code",
                "board_code",
                "board_name",
                "provider_detail_url",
                "lead_stock",
                "leader",
                "industry_label",
                "first_seal_time",
                "last_seal_time",
                "previous_first_seal_time",
                "statistic_window_days",
                "statistic_limit_count",
                "board_type",
                "high_days_label",
                "provider_is_again_limit",
                "popularity_tag",
                "provider_market_open_code",
                "provider_board_code",
                "provider_occurrence_state_code",
                "statistics_trading_days",
            )
        }
        signal_dimensions["market_scope"] = "mainland_a_share"
        signal_dimensions["pool_state"] = "limit_up"
        for production in build_default_market_signal_operations(NoNetwork()):  # type: ignore[arg-type]
            signal_type = signal_types[production.operation_id]

            class SignalFixture:
                operation_id = production.operation_id
                supported_signal_types = frozenset({signal_type})
                raw_fields = production.raw_fields
                fixture_signal_type = signal_type

                def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                    item = MarketSignalObservation(
                        signal_type=self.fixture_signal_type,
                        source_operation=self.operation_id,
                        source_role="market_signal",
                        subject=query.subject,
                        source_document_id="fixture-document",
                        observed_on="2026-07-31",
                        observed_at=None,
                        available_at="2026-08-02T10:00:00+08:00",
                        retrieved_at=now,
                        period={
                            "start": "2026-07-31",
                            "end": "2026-07-31",
                            "frequency": "trading_day",
                        },
                        metrics=signal_metrics,
                        units={key: "unit" for key in signal_metrics},
                        directions={key: "not_directional" for key in signal_metrics},
                        rule={"code": "fixture_rule"},
                        attributions=(
                            ThemeAttribution(
                                "fixture",
                                "editorial_annotation",
                                self.operation_id,
                                "fixture-document",
                                "https://provider.invalid/private",
                            ),
                        ),
                        dimensions=signal_dimensions,
                        locator_uri="https://provider.invalid/private",
                        limitations=("security_exchange_unverified",),
                    )
                    return SignalSourceBatch(
                        self.operation_id,
                        observations=(item,),
                        coverage={
                            self.fixture_signal_type: SignalCoverage(
                                "observed_nonempty"
                            )
                        },
                    )

            subject_required = signal_type == "security_board_membership"
            request = signal_request(
                [signal_type],
                subjects=[{"clue": "300058"}] if subject_required else [],
            )
            result = build_market_signals_result(
                request,
                (SignalFixture(),),
                BluefocusIdentityTransport()
                if subject_required
                else NoIdentityTransport(),
            ).to_dict()
            operation = result["coverage"]["market_signal_observations"]["operations"][
                production.operation_id
            ]
            for disposition in operation["field_dispositions"]:
                if disposition["disposition"] != "excluded":
                    self.assertTrue(
                        _destination_resolves(
                            result, production.operation_id, disposition["destination"]
                        ),
                        (production.operation_id, disposition),
                    )

    def test_completed_block_trade_coverage_survives_later_lockup_failure(self) -> None:
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.trading_event_sources import (
            EastmoneyTradingEventOperation,
        )

        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            capital_request,
        )
        from tests.test_trading_event_sources import (
            FixedTransport,
            inline_response,
            no_wait_gate,
            response,
        )

        request = capital_request(
            ["block_trade", "lockup"],
            subjects=[{"clue": "300058"}],
        )
        operation = EastmoneyTradingEventOperation(
            FixedTransport(
                [response("block_trade.json"), inline_response({}, status=500)]
            ),
            request_gate=no_wait_gate(),
        )

        result = build_capital_events_result(
            request,
            (operation,),
            BluefocusIdentityTransport(),
        ).to_dict()
        calculations = {
            item["payload"]["data_type"]: item["payload"]
            for item in result["calculations"]
            if item["kind"] == "capital_event_coverage"
        }

        self.assertEqual(calculations["block_trade"]["state"], "observed_nonempty")
        self.assertEqual(calculations["block_trade"]["observation_count"], 2)
        self.assertEqual(calculations["lockup"]["state"], "indeterminate")
        retained = [
            item
            for item in result["observations"]
            if item["operation_id"] == operation.operation_id
        ]
        self.assertEqual(len(retained), 2)

    def test_copied_installed_success_retains_outside_window_source_fact(self) -> None:
        request = _request("capital_events")
        request["source_policy"]["allow_experimental"] = True  # type: ignore[index]
        bootstrap = """
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))
from a_share_research.capital_contract import CapitalObservation, CapitalSourceBatch, CapitalSourceFailure
from a_share_research.research_runtime import research as real_research
import a_share_research.cli as cli

retrieved_at = datetime(2026, 8, 2, 19, 40, tzinfo=timezone(timedelta(hours=8)))

def observation(observed_on, value):
    return CapitalObservation(
        data_type="northbound_flow",
        source_operation="fixture_northbound@1",
        source_role="market_observation",
        subject=None,
        observed_on=observed_on,
        available_at=observed_on + "T18:00:00+08:00",
        retrieved_at=retrieved_at,
        period={"start": observed_on, "end": observed_on, "frequency": "daily"},
        metrics={"net_buy_amount": value},
        units={"net_buy_amount": "CNY"},
        directions={"net_buy_amount": "positive_is_net_inflow"},
        dimensions={"market_scope": "northbound_connect"},
        locator_uri="https://provider.invalid/private/path?token=secret",
    )

class FixtureOperation:
    operation_id = "fixture_northbound@1"
    supported_data_types = frozenset({"northbound_flow"})
    raw_fields = ("result.data[]", "TRADE_DATE", "NET_DEAL_AMT")
    def collect(self, query):
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=(observation("2026-08-02", "12.50"), observation("2026-07-31", "9.00")),
            source_errors=(CapitalSourceFailure(self.operation_id, "pagination_incomplete", "A later page was unavailable."),),
            complete=False,
        )

def fixture_research(request, **kwargs):
    return real_research(request, capital_operations=(FixtureOperation(),))

cli.research = fixture_research
"""

        result = _invoke_copied_installed(request, bootstrap=bootstrap)
        capital = [
            item
            for item in result["observations"]
            if item["operation_id"] == "fixture_northbound@1"
        ]
        self.assertEqual(
            [item["payload"]["observed_on"] for item in capital],
            ["2026-08-02", "2026-07-31"],
        )
        adopted_ids = {
            observation_id
            for claim in result["claims"]
            if claim["kind"] == "capital_event_observation"
            for observation_id in claim["observation_ids"]
        }
        self.assertEqual(adopted_ids, {capital[0]["id"]})
        calculation = next(
            item
            for item in result["calculations"]
            if item["kind"] == "capital_event_coverage"
        )
        self.assertEqual(calculation["input_ids"], [capital[0]["id"]])
        self.assertEqual(calculation["payload"]["observation_count"], 1)
        self.assertNotIn("provider.invalid", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_foreign_batch_is_wholly_quarantined_without_text_leak(self) -> None:
        from a_share_research.capital_contract import (
            CapitalSourceBatch,
            CapitalSourceFailure,
        )
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.market_signal_contract import (
            SignalCoverage,
            SignalSourceBatch,
            SignalSourceFailure,
        )
        from a_share_research.market_signals import build_market_signals_result

        class CapitalMismatch:
            operation_id = "declared_capital@1"
            supported_data_types = frozenset({"northbound_flow"})
            raw_fields = ("result.data[]", "TRADE_DATE", "NET_DEAL_AMT")

            def collect(self, query: object) -> CapitalSourceBatch:
                return CapitalSourceBatch(
                    operation_id="foreign_capital@1",
                    source_errors=(
                        CapitalSourceFailure(
                            "foreign_capital@1",
                            "foreign_failure",
                            "secret foreign text",
                        ),
                    ),
                    complete=False,
                )

        class MarketMismatch:
            operation_id = "declared_market@1"
            supported_signal_types = frozenset({"market_heat"})
            raw_fields = ("data.stock_list[]", "order", "code", "name", "rate")

            def collect(self, query: object) -> SignalSourceBatch:
                return SignalSourceBatch(
                    operation_id="foreign_market@1",
                    coverage={"market_heat": SignalCoverage("indeterminate")},
                    source_errors=(
                        SignalSourceFailure(
                            "foreign_market@1", "foreign_failure", "secret foreign text"
                        ),
                    ),
                )

        class NoNetwork:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("subject-free tasks must not resolve identity")

        capital_request = _request("capital_events")
        capital_request["source_policy"]["allow_experimental"] = True  # type: ignore[index]
        market_request = _request("market_signals")
        market_request["source_policy"]["allow_experimental"] = True  # type: ignore[index]
        for result, owner in (
            (
                build_capital_events_result(
                    capital_request, (CapitalMismatch(),), NoNetwork()
                ).to_dict(),
                "declared_capital@1",
            ),
            (
                build_market_signals_result(
                    market_request, (MarketMismatch(),), NoNetwork()
                ).to_dict(),
                "declared_market@1",
            ),
        ):
            with self.subTest(owner=owner):
                self.assertEqual(len(result["source_errors"]), 1)
                error = result["source_errors"][0]
                self.assertEqual(error["operation_id"], owner)
                self.assertEqual(error["code"], "operation_owner_mismatch")
                serialized = json.dumps(result)
                self.assertNotIn("foreign_", serialized)
                self.assertNotIn("secret foreign text", serialized)

    def test_serializer_rejects_full_rehash_forged_mode_coverage(self) -> None:
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.trading_event_sources import (
            EastmoneyTradingEventOperation,
        )

        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            capital_request,
        )
        from tests.test_trading_event_sources import (
            FixedTransport,
            inline_response,
            no_wait_gate,
            response,
        )

        result = build_capital_events_result(
            capital_request(["block_trade", "lockup"], subjects=[{"clue": "300058"}]),
            (
                EastmoneyTradingEventOperation(
                    FixedTransport(
                        [response("block_trade.json"), inline_response({}, status=500)]
                    ),
                    request_gate=no_wait_gate(),
                ),
            ),
            BluefocusIdentityTransport(),
        ).to_dict()
        forged = deepcopy(result)
        calculation = next(
            item
            for item in forged["calculations"]
            if item["kind"] == "capital_event_coverage"
            and item["payload"]["data_type"] == "block_trade"
        )
        old_id = calculation["id"]
        calculation["payload"]["state"] = "partial"
        calculation["id"] = stable_result_id(
            "calculation",
            {key: value for key, value in calculation.items() if key != "id"},
        )

        def replace(value: object, old: str, new: str) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if nested == old:
                        value[key] = new
                    else:
                        replace(nested, old, new)
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    if nested == old:
                        value[index] = new
                    else:
                        replace(nested, old, new)

        replace(forged, old_id, calculation["id"])
        brief = forged["briefs"][0]
        old_brief_id = brief["id"]
        brief["id"] = stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        )
        replace(forged, old_brief_id, brief["id"])
        with self.assertRaisesRegex(
            ValueError, "capital coverage calculation contradicts"
        ):
            UnifiedResearchResult(
                status=forged["status"],
                research_anchor=forged["research_anchor"],
                plan=forged["plan"],
                capability_registry=forged["capability_registry"],
                coverage=forged["coverage"],
                observations=tuple(forged["observations"]),
                claims=tuple(forged["claims"]),
                calculations=tuple(forged["calculations"]),
                conflicts=tuple(forged["conflicts"]),
                limitations=tuple(forged["limitations"]),
                source_errors=tuple(forged["source_errors"]),
                task_views=forged["task_views"],
                briefs=tuple(forged["briefs"]),
            ).to_dict()

    def test_serializer_rejects_synchronously_rehashed_source_state_forgeries(
        self,
    ) -> None:
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.market_signal_contract import (
            SignalCoverage,
            SignalSourceBatch,
            SignalSourceFailure,
        )
        from a_share_research.trading_event_sources import (
            EastmoneyTradingEventOperation,
        )

        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            capital_request,
        )
        from tests.test_direct_research_content_runtime import (
            rehash_result,
            validate_serialized,
        )
        from tests.test_market_signals import (
            FixedOperation,
            NoIdentityTransport,
            observation,
            signal_request,
        )
        from tests.test_market_signals import (
            build_market_signals_result as build_market_result,
        )
        from tests.test_trading_event_sources import (
            FixedTransport,
            inline_response,
            no_wait_gate,
            response,
        )

        capital = build_capital_events_result(
            capital_request(["block_trade", "lockup"], subjects=[{"clue": "300058"}]),
            (
                EastmoneyTradingEventOperation(
                    FixedTransport(
                        [response("block_trade.json"), inline_response({}, status=500)]
                    ),
                    request_gate=no_wait_gate(),
                ),
            ),
            BluefocusIdentityTransport(),
        ).to_dict()
        legitimate_capital = deepcopy(capital)
        capital_operation = capital["coverage"]["capital_event_observations"][
            "operations"
        ]["eastmoney_trading_events@1"]
        block_material = capital_operation["diagnostics"]["mode_acquisitions"][
            "block_trade"
        ]
        self.assertEqual(
            block_material,
            {
                "complete": True,
                "source_error_codes": [],
            },
        )
        block_material["complete"] = False
        block_material["source_error_codes"] = ["forged_block_failure"]
        capital_calculation = next(
            item
            for item in capital["calculations"]
            if item["kind"] == "capital_event_coverage"
            and item["payload"]["data_type"] == "block_trade"
        )
        capital_calculation["payload"]["state"] = "partial"
        unproved_incomplete = deepcopy(legitimate_capital)
        unproved_incomplete["coverage"]["capital_event_observations"]["operations"][
            "eastmoney_trading_events@1"
        ]["diagnostics"]["mode_acquisitions"]["block_trade"]["complete"] = False
        next(
            item
            for item in unproved_incomplete["calculations"]
            if item["kind"] == "capital_event_coverage"
            and item["payload"]["data_type"] == "block_trade"
        )["payload"]["state"] = "partial"

        market = build_market_result(
            signal_request(["strong_stock_theme"]),
            [
                FixedOperation(
                    SignalSourceBatch(
                        operation_id="fixed_signal@1",
                        observations=(observation(),),
                        coverage={
                            "strong_stock_theme": SignalCoverage("observed_nonempty")
                        },
                    )
                )
            ],
            NoIdentityTransport(),
        )
        market_calculation = next(
            item
            for item in market["calculations"]
            if item["kind"] == "market_signal_coverage"
        )
        self.assertEqual(market_calculation["payload"]["state"], "observed_nonempty")
        self.assertEqual(
            market_calculation["payload"]["sources"][0]["state"],
            "observed_nonempty",
        )
        market_calculation["payload"]["sources"][0]["state"] = "indeterminate"
        market_calculation["payload"]["state"] = "partial"

        for task_type, forged in (
            ("capital_events", capital),
            ("capital_events_unproved_incomplete", unproved_incomplete),
            ("market_signals", market),
        ):
            with self.subTest(task_type=task_type):
                with self.assertRaisesRegex(
                    ValueError,
                    "source material|coverage calculation contradicts|domain fact",
                ):
                    validate_serialized(rehash_result(forged))

        def replace_reference(value: object, old: str, new: str) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if nested == old:
                        value[key] = new
                    else:
                        replace_reference(nested, old, new)
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    if nested == old:
                        value[index] = new
                    else:
                        replace_reference(nested, old, new)

        foreign_scope = deepcopy(capital)
        error = foreign_scope["source_errors"][0]
        error["details"] = {"signal_types": ["strong_stock_theme"]}
        old_error_id = error["id"]
        error["id"] = stable_result_id(
            "source-error", {key: value for key, value in error.items() if key != "id"}
        )
        replace_reference(foreign_scope, old_error_id, error["id"])
        with self.assertRaisesRegex(ValueError, "scope is invalid for this task"):
            validate_serialized(rehash_result(foreign_scope))

        unknown_scope = deepcopy(legitimate_capital)
        unknown_error = {
            "capability_id": "capital_event_observations",
            "operation_id": "eastmoney_trading_events@1",
            "code": "forged_unknown_scope",
            "message": "Forged unknown capital scope.",
            "details": {"data_type": "unknown_capital_type"},
        }
        unknown_error["id"] = stable_result_id("source-error", unknown_error)
        unknown_scope["source_errors"].append(unknown_error)
        unknown_scope["coverage"]["capital_event_observations"]["operations"][
            "eastmoney_trading_events@1"
        ]["source_error_ids"].append(unknown_error["id"])
        unknown_scope["task_views"]["capital_events"]["source_error_ids"].append(
            unknown_error["id"]
        )
        unknown_scope["briefs"][0]["source_error_ids"].append(unknown_error["id"])
        with self.assertRaisesRegex(ValueError, "scope type is not declared"):
            validate_serialized(rehash_result(unknown_scope))

        market_error_scope = build_market_result(
            signal_request(["strong_stock_theme"]),
            [
                FixedOperation(
                    SignalSourceBatch(
                        operation_id="fixed_signal@1",
                        coverage={
                            "strong_stock_theme": SignalCoverage("indeterminate")
                        },
                        source_errors=(
                            SignalSourceFailure(
                                "fixed_signal@1",
                                "upstream_unavailable",
                                "The fixture source was unavailable.",
                            ),
                        ),
                    )
                )
            ],
            NoIdentityTransport(),
        )
        market_error = {
            "capability_id": "market_signal_observations",
            "operation_id": "fixed_signal@1",
            "code": "forged_unknown_scope",
            "message": "Forged unknown market-signal scope.",
            "details": {"signal_types": ["unknown_signal_type"]},
        }
        market_error["id"] = stable_result_id("source-error", market_error)
        market_error_scope["source_errors"].append(market_error)
        market_error_scope["coverage"]["market_signal_observations"]["operations"][
            "fixed_signal@1"
        ]["source_error_ids"].append(market_error["id"])
        market_error_scope["task_views"]["market_signals"]["source_error_ids"].append(
            market_error["id"]
        )
        market_error_scope["briefs"][0]["source_error_ids"].append(market_error["id"])
        with self.assertRaisesRegex(ValueError, "scope type is not declared"):
            validate_serialized(rehash_result(market_error_scope))

    def test_failed_sibling_prevents_complete_empty_task_coverage(self) -> None:
        from a_share_research.capital_contract import CapitalQuery, CapitalSourceBatch
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.market_signal_contract import (
            MarketSignalQuery,
            SignalCoverage,
            SignalSourceBatch,
        )
        from a_share_research.market_signals import build_market_signals_result

        from tests.test_capital_events import capital_request
        from tests.test_market_signals import NoIdentityTransport, signal_request

        class EmptyCapital:
            operation_id = "empty_capital@1"
            supported_data_types = frozenset({"northbound_flow"})
            raw_fields = ("result.data[]", "TRADE_DATE", "NET_DEAL_AMT")

            def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                return CapitalSourceBatch(self.operation_id)

        class BoomCapital(EmptyCapital):
            operation_id = "boom_capital@1"

            def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                raise RuntimeError("secret capital source text")

        class EmptyMarket:
            operation_id = "empty_market@1"
            supported_signal_types = frozenset({"limit_state"})
            raw_fields = ("data.pool[]", "c", "date", "e")

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                return SignalSourceBatch(
                    self.operation_id,
                    coverage={"limit_state": SignalCoverage("observed_empty")},
                )

        class BoomMarket(EmptyMarket):
            operation_id = "boom_market@1"

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                raise RuntimeError("secret market source text")

        cases = (
            (
                "capital_events",
                lambda: build_capital_events_result(
                    capital_request(["northbound_flow"]),
                    (EmptyCapital(), BoomCapital()),
                    NoIdentityTransport(),
                ).to_dict(),
                "capital_event_coverage",
                "source_operations",
                "boom_capital@1",
            ),
            (
                "market_signals",
                lambda: build_market_signals_result(
                    signal_request(["limit_state"]),
                    (EmptyMarket(), BoomMarket()),
                    NoIdentityTransport(),
                ).to_dict(),
                "market_signal_coverage",
                "sources",
                "boom_market@1",
            ),
        )
        for task_type, build, calculation_kind, sources_field, failed_owner in cases:
            with self.subTest(task_type=task_type):
                result = build()
                self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
                calculation = next(
                    item
                    for item in result["calculations"]
                    if item["kind"] == calculation_kind
                )
                self.assertEqual(calculation["payload"]["state"], "partial")
                sources = calculation["payload"][sources_field]
                owners = (
                    sources
                    if sources_field == "source_operations"
                    else [item["source_operation"] for item in sources]
                )
                self.assertIn(failed_owner, owners)
                self.assertEqual(
                    [item["code"] for item in result["source_errors"]],
                    ["operation_failure"],
                )
                self.assertNotIn("secret", json.dumps(result))

    def test_foreign_source_scope_is_quarantined_as_one_safe_failure(self) -> None:
        from a_share_research.capital_contract import (
            CapitalQuery,
            CapitalSourceBatch,
            CapitalSourceFailure,
        )
        from a_share_research.capital_events import build_capital_events_result
        from a_share_research.market_signal_contract import (
            MarketSignalQuery,
            SignalCoverage,
            SignalSourceBatch,
            SignalSourceFailure,
        )
        from a_share_research.market_signals import build_market_signals_result

        from tests.test_capital_events import capital_request
        from tests.test_market_signals import NoIdentityTransport, signal_request

        class CapitalScopeMismatch:
            operation_id = "declared_capital_scope@1"
            supported_data_types = frozenset({"northbound_flow"})
            raw_fields = ("result.data[]", "TRADE_DATE", "NET_DEAL_AMT")

            def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
                return CapitalSourceBatch(
                    self.operation_id,
                    source_errors=(
                        CapitalSourceFailure(
                            self.operation_id,
                            "foreign_capital_error",
                            "secret foreign capital text",
                            {"data_type": "margin_trading"},
                        ),
                    ),
                    complete=False,
                )

        class MarketScopeMismatch:
            operation_id = "declared_market_scope@1"
            supported_signal_types = frozenset({"limit_state"})
            raw_fields = ("data.pool[]", "c", "date", "e")

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                return SignalSourceBatch(
                    self.operation_id,
                    coverage={"limit_state": SignalCoverage("observed_empty")},
                    source_errors=(
                        SignalSourceFailure(
                            self.operation_id,
                            "foreign_signal_error",
                            "secret foreign signal text",
                            {"signal_types": ["market_heat"]},
                        ),
                    ),
                )

        for task_type, owner, build in (
            (
                "capital_events",
                "declared_capital_scope@1",
                lambda: build_capital_events_result(
                    capital_request(["northbound_flow"]),
                    (CapitalScopeMismatch(),),
                    NoIdentityTransport(),
                ).to_dict(),
            ),
            (
                "market_signals",
                "declared_market_scope@1",
                lambda: build_market_signals_result(
                    signal_request(["limit_state"]),
                    (MarketScopeMismatch(),),
                    NoIdentityTransport(),
                ).to_dict(),
            ),
        ):
            with self.subTest(task_type=task_type):
                result = build()
                self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
                self.assertEqual(len(result["source_errors"]), 1)
                self.assertEqual(result["source_errors"][0]["operation_id"], owner)
                self.assertEqual(
                    result["source_errors"][0]["code"], "operation_scope_mismatch"
                )
                serialized = json.dumps(result)
                self.assertNotIn("foreign_", serialized)
                self.assertNotIn("secret", serialized)
                self.assertNotIn("margin_trading", serialized)
                self.assertNotIn("market_heat", serialized)

    def test_scoped_market_failure_does_not_pollute_healthy_sibling_type(self) -> None:
        from a_share_research.market_signal_contract import (
            MarketSignalQuery,
            SignalCoverage,
            SignalSourceBatch,
            SignalSourceFailure,
        )
        from a_share_research.research_runtime import research

        from tests.test_market_signals import (
            NoIdentityTransport,
            observation,
            signal_request,
        )

        class ThemeAndHeatOperation:
            operation_id = "theme_and_heat@1"
            supported_signal_types = frozenset({"strong_stock_theme", "market_heat"})
            raw_fields = ("data.info[]", "code", "name", "reason_type")

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                return SignalSourceBatch(
                    self.operation_id,
                    observations=(
                        observation(
                            signal_type="strong_stock_theme",
                            operation_id=self.operation_id,
                        ),
                    ),
                    coverage={
                        "strong_stock_theme": SignalCoverage("observed_nonempty"),
                        "market_heat": SignalCoverage("indeterminate"),
                    },
                    source_errors=(
                        SignalSourceFailure(
                            self.operation_id,
                            "heat_source_unavailable",
                            "The heat source was unavailable.",
                            {"signal_types": ["market_heat"]},
                        ),
                    ),
                )

        result = research(
            signal_request(["strong_stock_theme", "market_heat"]),
            market_signal_operations=(ThemeAndHeatOperation(),),
            identity_transport=NoIdentityTransport(),
        ).to_dict()
        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        coverage = {
            item["payload"]["signal_type"]: item["payload"]
            for item in result["calculations"]
            if item["kind"] == "market_signal_coverage"
        }
        self.assertEqual(coverage["strong_stock_theme"]["state"], "observed_nonempty")
        self.assertEqual(coverage["market_heat"]["state"], "indeterminate")
        theme_ids = {
            item["id"]
            for item in result["observations"]
            if item["capability_id"] == "market_signal_observations"
            and item["payload"]["signal_type"] == "strong_stock_theme"
        }
        self.assertTrue(theme_ids)
        self.assertTrue(
            any(
                claim["kind"] == "market_signal_observation"
                and set(claim["observation_ids"]).issubset(theme_ids)
                for claim in result["claims"]
            )
        )

    def test_scoped_market_failures_are_symmetric_and_composable(self) -> None:
        from a_share_research.market_signal_contract import (
            MarketSignalQuery,
            SignalCoverage,
            SignalSourceBatch,
            SignalSourceFailure,
        )
        from a_share_research.research_runtime import research

        from tests.test_market_signals import (
            NoIdentityTransport,
            observation,
            signal_request,
        )

        class ScopedOperation:
            operation_id = "scoped_signal@1"
            supported_signal_types = frozenset(
                {"strong_stock_theme", "market_heat", "limit_state"}
            )
            raw_fields = ("data.info[]", "code", "name", "reason_type")

            def __init__(
                self,
                healthy_types: tuple[str, ...],
                failed_types: tuple[str, ...],
            ) -> None:
                self._healthy_types = healthy_types
                self._failed_types = failed_types

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                return SignalSourceBatch(
                    self.operation_id,
                    observations=tuple(
                        observation(signal_type=item, operation_id=self.operation_id)
                        for item in self._healthy_types
                    ),
                    coverage={
                        item: SignalCoverage(
                            "observed_nonempty"
                            if item in self._healthy_types
                            else "indeterminate"
                        )
                        for item in (*self._healthy_types, *self._failed_types)
                    },
                    source_errors=tuple(
                        SignalSourceFailure(
                            self.operation_id,
                            f"{item}_unavailable",
                            "The scoped source was unavailable.",
                            {"signal_types": [item]},
                        )
                        for item in self._failed_types
                    ),
                )

        for healthy_types, failed_types in (
            (("market_heat",), ("strong_stock_theme",)),
            (("limit_state",), ("strong_stock_theme", "market_heat")),
        ):
            with self.subTest(healthy_types=healthy_types, failed_types=failed_types):
                result = research(
                    signal_request([*healthy_types, *failed_types]),
                    market_signal_operations=(
                        ScopedOperation(healthy_types, failed_types),
                    ),
                    identity_transport=NoIdentityTransport(),
                ).to_dict()
                coverage = {
                    item["payload"]["signal_type"]: item["payload"]["state"]
                    for item in result["calculations"]
                    if item["kind"] == "market_signal_coverage"
                }
                self.assertEqual(
                    coverage,
                    {
                        **{item: "observed_nonempty" for item in healthy_types},
                        **{item: "indeterminate" for item in failed_types},
                    },
                )
                healthy_observation_ids = {
                    item["id"]
                    for item in result["observations"]
                    if item["capability_id"] == "market_signal_observations"
                    and item["payload"]["signal_type"] in healthy_types
                }
                self.assertEqual(len(healthy_observation_ids), len(healthy_types))
                self.assertTrue(
                    healthy_observation_ids.issubset(
                        {
                            observation_id
                            for claim in result["claims"]
                            for observation_id in claim["observation_ids"]
                        }
                    )
                )

    def test_serializer_rejects_forged_attribution_basis_and_market_calculation(
        self,
    ) -> None:
        from a_share_research.market_signal_contract import (
            SignalCoverage,
            SignalSourceBatch,
        )

        from tests.test_direct_research_content_runtime import (
            rehash_result,
            validate_serialized,
        )
        from tests.test_market_signals import (
            FixedOperation,
            NoIdentityTransport,
            build_market_signals_result,
            observation,
            signal_request,
        )

        source = build_market_signals_result(
            signal_request(["strong_stock_theme"]),
            [
                FixedOperation(
                    SignalSourceBatch(
                        operation_id="fixed_signal@1",
                        observations=(observation(),),
                        coverage={
                            "strong_stock_theme": SignalCoverage("observed_nonempty")
                        },
                    )
                )
            ],
            NoIdentityTransport(),
        )
        cases = ("model_inference", "dangling_basis", "calculation")
        for case in cases:
            with self.subTest(case=case):
                forged = deepcopy(source)
                if case == "calculation":
                    calculation = next(
                        item
                        for item in forged["calculations"]
                        if item["kind"] == "market_signal_coverage"
                    )
                    calculation["payload"]["observation_count"] = 999
                else:
                    record = forged["observations"][0]
                    attribution = dict(record["payload"]["attributions"][0])
                    attribution.update(
                        {
                            "provenance": (
                                "model_inference"
                                if case == "model_inference"
                                else "market_signal"
                            ),
                            "layer": (
                                "model_inference"
                                if case == "model_inference"
                                else "source_market_signal"
                            ),
                            "method_id": (
                                "forged_model_method@1"
                                if case == "model_inference"
                                else "source_composition@1"
                            ),
                            "basis_evidence_ids": ["observation-does-not-exist"],
                        }
                    )
                    record["payload"]["attributions"] = [attribution]
                    record["payload"]["source_facts"]["attributions"] = [
                        dict(attribution)
                    ]
                    claim = next(
                        item
                        for item in forged["claims"]
                        if record["id"] in item["observation_ids"]
                    )
                    claim["payload"]["layer"] = attribution["layer"]
                with self.assertRaisesRegex(
                    ValueError,
                    "attribution|model.inference|basis|market.signal.*calculation|domain fact",
                ):
                    validate_serialized(rehash_result(forged))

    def test_copied_installed_production_trading_variants_are_formal(self) -> None:
        bootstrap = """
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))
from a_share_research.identity_sources import HttpResponse
from a_share_research.research_runtime import research as real_research
from a_share_research.source_throttle import SerialRequestGate
from a_share_research.trading_event_sources import EastmoneyTradingEventOperation
import a_share_research.cli as cli

root = Path(__file__).resolve().parent / "fixtures"
retrieved_at = datetime(2026, 8, 2, 19, 40, tzinfo=timezone(timedelta(hours=8)))

def response(path):
    return HttpResponse(200, "application/json", path.read_bytes(), retrieved_at)

class FixtureTransport:
    def get(self, url, headers):
        parsed = urlsplit(url)
        if parsed.netloc == "query.sse.com.cn":
            return HttpResponse(200, "application/json", b'{"result":[]}', retrieved_at)
        if parsed.netloc == "www.szse.cn":
            return response(root / "identity_sources" / "szse_300058.json")
        if parsed.netloc == "www.cninfo.com.cn":
            return response(root / "identity_sources" / "cninfo_current_orgs.json")
        query = parse_qs(parsed.query)
        report = query["reportName"][0]
        if report == "RPT_DATA_BLOCKTRADE":
            fixture = "block_trade.json"
        elif report == "RPT_LIFT_STAGE":
            fixture = "lockup.json"
        elif report == "RPT_BILLBOARD_DAILYDETAILSBUY":
            fixture = "dragon_buy_seats.json"
        elif report == "RPT_BILLBOARD_DAILYDETAILSSELL":
            fixture = "dragon_sell_seats.json"
        elif "SECURITY_CODE" in query["filter"][0]:
            fixture = "dragon_records.json"
        else:
            fixture = "market_dragon_page_" + query["pageNumber"][0] + ".json"
        return response(root / "trading_events" / fixture)

transport = FixtureTransport()
operation = EastmoneyTradingEventOperation(
    transport,
    request_gate=SerialRequestGate(minimum_interval_seconds=0, jitter_bounds=(0, 0), rate_limit_backoffs=()),
)

def fixture_research(request, **kwargs):
    return real_research(request, identity_transport=transport, capital_operations=(operation,))

cli.research = fixture_research
"""
        for data_type in (
            "block_trade",
            "dragon_tiger",
            "lockup",
            "market_dragon_tiger",
        ):
            with self.subTest(data_type=data_type):
                request = _request("capital_events")
                request["source_policy"]["allow_experimental"] = True  # type: ignore[index]
                request["parameters"]["data_types"] = [data_type]  # type: ignore[index]
                if data_type == "market_dragon_tiger":
                    request["window"] = {
                        "observed_from": "2026-08-01",
                        "observed_to": "2026-08-01",
                    }
                else:
                    request["subjects"] = [{"clue": "300058"}]
                    request["window"] = {
                        "observed_from": "2026-07-01",
                        "observed_to": (
                            "2026-10-31" if data_type == "lockup" else "2026-08-02"
                        ),
                    }
                result = _invoke_copied_installed(request, bootstrap=bootstrap)
                self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
                calculation = next(
                    item
                    for item in result["calculations"]
                    if item["kind"] == "capital_event_coverage"
                )
                self.assertEqual(calculation["payload"]["state"], "observed_nonempty")
                self.assertGreater(calculation["payload"]["observation_count"], 0)


if __name__ == "__main__":
    unittest.main()
