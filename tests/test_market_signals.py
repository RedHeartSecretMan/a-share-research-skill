from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_signal_contract import (  # noqa: E402
    MarketSignalObservation,
    MarketSignalQuery,
    SignalCoverage,
    SignalSourceBatch,
    SignalSourceFailure,
    ThemeAttribution,
)
from a_share_research.market_signal_registry import (  # noqa: E402
    build_default_market_signal_operations,
)
from a_share_research.market_signals import build_market_signals_result  # noqa: E402
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def signal_request(
    signal_types: list[str],
    *,
    subjects: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "market_signals",
        "subjects": subjects or [],
        "as_of": "2026-08-02",
        "window": {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        },
        "parameters": {"signal_types": signal_types, "limit": 20},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def attribution(
    text: str = "算力",
    *,
    provenance: str = "editorial_annotation",
    operation_id: str = "fixed_signal@1",
) -> ThemeAttribution:
    return ThemeAttribution(
        text=text,
        provenance=provenance,
        source_operation=operation_id,
        source_document_id="theme-300058-20260731",
        locator_uri="https://example.test/themes/300058",
        basis_evidence_ids=(),
        method_id=None,
    )


def observation(
    *,
    signal_type: str = "strong_stock_theme",
    operation_id: str = "fixed_signal@1",
    pool_state: str | None = None,
    provider_security_code: str = "300058",
    attributions: tuple[ThemeAttribution, ...] | None = None,
) -> MarketSignalObservation:
    return MarketSignalObservation(
        signal_type=signal_type,
        source_operation=operation_id,
        source_role="market_signal",
        subject=None,
        source_document_id=f"{signal_type}-{provider_security_code}-20260731",
        observed_on="2026-07-31",
        observed_at=None,
        available_at="2026-08-02T10:00:00+08:00",
        retrieved_at=datetime(2026, 8, 2, 10, 0, tzinfo=CHINA_STANDARD_TIME),
        period={"start": "2026-07-31", "end": "2026-07-31", "frequency": "trading_day"},
        metrics={"change_rate": "10.01"},
        units={"change_rate": "percent"},
        directions={"change_rate": "positive_is_gain"},
        rule=None,
        attributions=(attribution(operation_id=operation_id),)
        if attributions is None
        else attributions,
        dimensions={
            "market_scope": "mainland_a_share",
            "provider_security_code": provider_security_code,
            **({"pool_state": pool_state} if pool_state is not None else {}),
        },
        locator_uri="https://example.test/signals/1",
        limitations=("security_exchange_unverified",),
    )


class FixedOperation:
    operation_id = "fixed_signal@1"
    supported_signal_types = frozenset(
        {"strong_stock_theme", "limit_state", "market_heat"}
    )

    def __init__(self, batch: SignalSourceBatch) -> None:
        self._batch = batch

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        self.query = query
        return self._batch


class NoIdentityTransport:
    def get(self, url: str, headers: dict[str, str]) -> Any:
        raise AssertionError(f"market-wide task must not resolve identity: {url}")


class FixtureIdentityTransport:
    fixtures = Path(__file__).resolve().parent / "fixtures" / "identity_sources"

    def __init__(self, *, bluefocus: bool) -> None:
        self._bluefocus = bluefocus

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlparse(url).netloc
        if host == "query.sse.com.cn":
            fixture = "sse_empty.json"
        elif host == "www.szse.cn":
            fixture = "szse_300058.json" if self._bluefocus else "szse_empty.json"
        elif host == "www.cninfo.com.cn":
            fixture = (
                "cninfo_current_orgs.json" if self._bluefocus else "cninfo_stocks.json"
            )
        else:
            raise AssertionError(f"unexpected identity URL: {url}")
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=(self.fixtures / fixture).read_bytes(),
            retrieved_at=datetime(2026, 8, 2, 18, 30, tzinfo=CHINA_STANDARD_TIME),
        )


class MarketSignalsContractTests(unittest.TestCase):
    def test_default_registry_covers_every_source_backed_signal_type(self) -> None:
        operations = build_default_market_signal_operations(NoIdentityTransport())

        covered = set().union(
            *(operation.supported_signal_types for operation in operations)
        )

        self.assertEqual(
            covered,
            {
                "strong_stock_theme",
                "security_board_membership",
                "industry_rotation",
                "limit_state",
                "focus_monitoring",
                "severe_abnormal_movement",
                "market_heat",
            },
        )

    def test_runtime_routes_injected_operation_and_preserves_attribution(self) -> None:
        item = observation()
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(item,),
                coverage={
                    "strong_stock_theme": SignalCoverage(
                        state="observed_nonempty",
                        provider_total=1,
                        pages_collected=1,
                        pages_expected=1,
                    )
                },
            )
        )

        result = ResearchRuntime(
            identity_transport=NoIdentityTransport(),
            market_signal_operations=[operation],
        ).research(signal_request(["strong_stock_theme"]))

        self.assertEqual(result["task_type"], "market_signals")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["observations"][0]["attributions"][0]["provenance"],
            "editorial_annotation",
        )

    def test_complete_empty_pool_is_answerable_and_distinct_from_failure(self) -> None:
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                coverage={
                    "limit_state": SignalCoverage(
                        state="observed_empty",
                        provider_total=0,
                        pages_collected=1,
                        pages_expected=1,
                    )
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["limit_state"]), [operation], NoIdentityTransport()
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["source_errors"], [])
        self.assertEqual(
            result["brief"]["coverage"]["limit_state"]["state"],
            "observed_empty",
        )

    def test_permitted_fallback_without_qualified_source_is_disclosed(self) -> None:
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                coverage={
                    "market_heat": SignalCoverage(
                        state="observed_empty", provider_total=0
                    )
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["market_heat"]), [operation], NoIdentityTransport()
        )

        fallback_limitations = [
            item
            for item in result["limitations"]
            if item["code"] == "no_qualified_independent_fallback"
        ]
        self.assertEqual(fallback_limitations[0]["signal_types"], ["market_heat"])

    def test_identity_blocked_result_keeps_stable_brief_shape(self) -> None:
        result = build_market_signals_result(
            signal_request(
                ["security_board_membership"],
                subjects=[{"clue": "300999"}],
            ),
            [],
            FixtureIdentityTransport(bluefocus=False),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["brief"]["aggregates"], {})

    def test_empty_from_one_source_and_failure_from_another_is_partial(self) -> None:
        empty_operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                coverage={
                    "limit_state": SignalCoverage(
                        state="observed_empty", provider_total=0
                    )
                },
            )
        )

        class FailedOperation(FixedOperation):
            operation_id = "failed_signal@1"

        failed_operation = FailedOperation(
            SignalSourceBatch(
                operation_id="failed_signal@1",
                coverage={"limit_state": SignalCoverage(state="indeterminate")},
                source_errors=(
                    SignalSourceFailure(
                        "failed_signal@1",
                        "upstream_unavailable",
                        "The source was unavailable.",
                    ),
                ),
            )
        )

        result = build_market_signals_result(
            signal_request(["limit_state"]),
            [empty_operation, failed_operation],
            NoIdentityTransport(),
        )

        self.assertEqual(result["brief"]["coverage"]["limit_state"]["state"], "partial")

    def test_invalid_attribution_provenance_fails_closed(self) -> None:
        invalid = observation(attributions=(attribution(provenance="provider_guess"),))
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(invalid,),
                coverage={
                    "strong_stock_theme": SignalCoverage(state="observed_nonempty")
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["strong_stock_theme"]), [operation], NoIdentityTransport()
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "unknown_schema", [item["code"] for item in result["source_errors"]]
        )

    def test_conflicting_cross_source_pool_states_are_retained_and_reported(
        self,
    ) -> None:
        first = observation(signal_type="limit_state", pool_state="limit_up")
        second = replace(
            first,
            source_operation="other_signal@1",
            source_document_id="limit-break-300058-20260731",
            attributions=(),
            dimensions={**first.dimensions, "pool_state": "limit_break"},
            locator_uri="https://example.test/signals/2",
        )
        first_operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(first,),
                coverage={"limit_state": SignalCoverage(state="observed_nonempty")},
            )
        )

        class OtherOperation(FixedOperation):
            operation_id = "other_signal@1"

        second_operation = OtherOperation(
            SignalSourceBatch(
                operation_id="other_signal@1",
                observations=(second,),
                coverage={"limit_state": SignalCoverage(state="observed_nonempty")},
            )
        )

        result = build_market_signals_result(
            signal_request(["limit_state"]),
            [first_operation, second_operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 2)
        self.assertIn(
            "cross_source_signal_disagreement",
            [item["code"] for item in result["conflicts"]],
        )

    def test_previous_limit_up_membership_is_not_a_current_pool_conflict(self) -> None:
        current = observation(signal_type="limit_state", pool_state="limit_up")
        previous = replace(
            current,
            source_operation="other_signal@1",
            source_document_id="previous-limit-up-300058-20260731",
            attributions=(),
            dimensions={**current.dimensions, "pool_state": "previous_limit_up"},
            locator_uri="https://example.test/signals/previous-limit-up",
        )
        first_operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(current,),
                coverage={"limit_state": SignalCoverage(state="observed_nonempty")},
            )
        )

        class OtherOperation(FixedOperation):
            operation_id = "other_signal@1"

        second_operation = OtherOperation(
            SignalSourceBatch(
                operation_id="other_signal@1",
                observations=(previous,),
                coverage={"limit_state": SignalCoverage(state="observed_nonempty")},
            )
        )

        result = build_market_signals_result(
            signal_request(["limit_state"]),
            [first_operation, second_operation],
            NoIdentityTransport(),
        )

        self.assertEqual(result["conflicts"], [])

    def test_market_and_subject_scopes_cannot_be_mixed(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot mix"):
            build_market_signals_result(
                signal_request(
                    ["security_board_membership", "market_heat"],
                    subjects=[{"clue": "蓝色光标"}],
                ),
                [],
                NoIdentityTransport(),
            )

    def test_source_adapter_cannot_emit_model_inference(self) -> None:
        model_reason = attribution(provenance="model_inference")
        item = observation(attributions=(model_reason,))
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(item,),
                coverage={
                    "strong_stock_theme": SignalCoverage(state="observed_nonempty")
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["strong_stock_theme"]), [operation], NoIdentityTransport()
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "adapter_model_inference_forbidden",
            [item["code"] for item in result["source_errors"]],
        )

    def test_complete_limit_pools_publish_lineaged_sentiment_aggregates(self) -> None:
        limit_up = replace(
            observation(signal_type="limit_state", pool_state="limit_up"),
            metrics={"consecutive_limit_days": "3"},
            units={"consecutive_limit_days": "count"},
            directions={"consecutive_limit_days": "not_directional"},
        )
        second_limit_up = replace(
            limit_up,
            source_document_id="limit-up-300001-20260731",
            metrics={"consecutive_limit_days": "2"},
            dimensions={
                **limit_up.dimensions,
                "provider_security_code": "300001",
            },
            locator_uri="https://example.test/signals/limit-up-2",
        )
        limit_break = replace(
            limit_up,
            source_document_id="limit-break-300002-20260731",
            metrics={"consecutive_limit_days": None},
            dimensions={
                **limit_up.dimensions,
                "pool_state": "limit_break",
                "provider_security_code": "300002",
            },
            locator_uri="https://example.test/signals/limit-break",
        )
        limit_down = replace(
            limit_up,
            source_document_id="limit-down-300003-20260731",
            metrics={"consecutive_limit_days": None},
            dimensions={
                **limit_up.dimensions,
                "pool_state": "limit_down",
                "provider_security_code": "300003",
            },
            locator_uri="https://example.test/signals/limit-down",
        )
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(limit_up, second_limit_up, limit_break, limit_down),
                coverage={
                    "limit_state": SignalCoverage(
                        state="observed_nonempty",
                        provider_total=4,
                        pages_collected=3,
                        pages_expected=3,
                        details={
                            "pool_states": [
                                "limit_up",
                                "limit_break",
                                "limit_down",
                            ]
                        },
                    )
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["limit_state"]), [operation], NoIdentityTransport()
        )

        sentiment = result["brief"]["aggregates"]["limit_state_sentiment"]
        self.assertEqual(sentiment["limit_up_count"], "2")
        self.assertEqual(sentiment["limit_break_count"], "1")
        self.assertEqual(sentiment["limit_down_count"], "1")
        self.assertEqual(sentiment["break_rate"], "33.33333333333333333333333333")
        self.assertEqual(sentiment["max_consecutive_limit_days"], "3")
        self.assertEqual(sentiment["consecutive_limit_ladder"], {"2": "1", "3": "1"})
        self.assertEqual(
            sentiment["formula"], "(limit_break/(limit_up+limit_break))*100"
        )
        self.assertEqual(sentiment["units"]["break_rate"], "percent")
        self.assertEqual(len(sentiment["basis_evidence_ids"]), 4)

    def test_exact_duplicate_is_removed_from_result_and_evidence(self) -> None:
        item = observation()
        operation = FixedOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(item, item),
                coverage={
                    "strong_stock_theme": SignalCoverage(
                        state="observed_nonempty", provider_total=1
                    )
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["strong_stock_theme"]),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(len(result["evidence"]), 1)

    def test_monitoring_intersection_is_derived_from_canonical_basis_evidence(
        self,
    ) -> None:
        subject = {
            "security": {
                "exchange": "SZSE",
                "code": "300058",
                "type": "A_SHARE",
            },
            "name": "蓝色光标",
            "issuer": {},
        }
        focus = replace(
            observation(signal_type="focus_monitoring"),
            subject=subject,
            period={
                "start": "2026-07-01",
                "end": "2026-08-31",
                "frequency": "monitoring_window",
            },
            dimensions={"market_scope": "mainland_a_share"},
            limitations=(),
        )
        severe = replace(
            observation(signal_type="severe_abnormal_movement"),
            subject=subject,
            rule={"rule_code": "SZSE-SEVERE-01"},
            dimensions={"market_scope": "mainland_a_share"},
            limitations=(),
        )

        class MonitoringOperation(FixedOperation):
            supported_signal_types = frozenset(
                {"focus_monitoring", "severe_abnormal_movement"}
            )

        operation = MonitoringOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(focus, severe),
                coverage={
                    "focus_monitoring": SignalCoverage(
                        state="observed_nonempty", provider_total=1
                    ),
                    "severe_abnormal_movement": SignalCoverage(
                        state="observed_nonempty", provider_total=1
                    ),
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["observations"]), 1)
        intersection = result["observations"][0]
        self.assertEqual(intersection["signal_type"], "monitoring_intersection")
        basis_ids = intersection["attributions"][0]["basis_evidence_ids"]
        evidence_ids = {item["id"] for item in result["evidence"]}
        self.assertEqual(len(basis_ids), 2)
        self.assertTrue(set(basis_ids).issubset(evidence_ids))

    def test_intersection_with_partial_basis_keeps_observation_but_is_partial(
        self,
    ) -> None:
        subject = {
            "security": {
                "exchange": "SZSE",
                "code": "300058",
                "type": "A_SHARE",
            },
            "name": "蓝色光标",
            "issuer": {},
        }
        focus = replace(
            observation(signal_type="focus_monitoring"),
            subject=subject,
            period={
                "start": "2026-07-01",
                "end": "2026-08-31",
                "frequency": "monitoring_window",
            },
            dimensions={"market_scope": "mainland_a_share"},
            limitations=(),
        )
        severe = replace(
            observation(signal_type="severe_abnormal_movement"),
            subject=subject,
            rule={"rule_code": "SZSE-SEVERE-01"},
            dimensions={"market_scope": "mainland_a_share"},
            limitations=(),
        )

        class MonitoringOperation(FixedOperation):
            supported_signal_types = frozenset(
                {"focus_monitoring", "severe_abnormal_movement"}
            )

        operation = MonitoringOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(focus, severe),
                coverage={
                    "focus_monitoring": SignalCoverage(state="partial"),
                    "severe_abnormal_movement": SignalCoverage(
                        state="observed_nonempty", provider_total=1
                    ),
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(
            result["brief"]["coverage"]["monitoring_intersection"]["state"],
            "partial",
        )

        errored_operation = MonitoringOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(focus, severe),
                coverage={
                    "focus_monitoring": SignalCoverage(
                        state="observed_nonempty", provider_total=1
                    ),
                    "severe_abnormal_movement": SignalCoverage(
                        state="observed_nonempty", provider_total=1
                    ),
                },
                source_errors=(
                    SignalSourceFailure(
                        "fixed_signal@1",
                        "pagination_incomplete",
                        "A basis source did not prove complete retrieval.",
                    ),
                ),
            )
        )

        errored_result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [errored_operation],
            NoIdentityTransport(),
        )

        derived_coverage = errored_result["brief"]["coverage"][
            "monitoring_intersection"
        ]
        self.assertEqual(derived_coverage["state"], "partial")
        self.assertNotIn("provider_total", derived_coverage["sources"][0])

    def test_provider_overlap_is_enriched_with_canonical_identity(
        self,
    ) -> None:
        focus = replace(
            observation(signal_type="focus_monitoring"),
            period={
                "start": "2026-07-01",
                "end": "2026-08-31",
                "frequency": "monitoring_window",
            },
        )
        severe = replace(
            observation(signal_type="severe_abnormal_movement"),
            rule={"rule_code": "SZSE-SEVERE-01"},
        )

        class MonitoringOperation(FixedOperation):
            supported_signal_types = frozenset(
                {"focus_monitoring", "severe_abnormal_movement"}
            )

        operation = MonitoringOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(focus, severe),
                coverage={
                    "focus_monitoring": SignalCoverage(state="observed_nonempty"),
                    "severe_abnormal_movement": SignalCoverage(
                        state="observed_nonempty"
                    ),
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [operation],
            FixtureIdentityTransport(bluefocus=True),
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["brief"]["coverage"]["monitoring_intersection"]["state"],
            "observed_nonempty",
        )
        self.assertEqual(
            result["observations"][0]["subject"]["security"],
            {"exchange": "SZSE", "code": "300058", "type": "A_SHARE"},
        )
        self.assertGreaterEqual(
            len(result["observations"][0]["attributions"][0]["basis_evidence_ids"]),
            3,
        )

    def test_unresolved_provider_overlap_remains_blocked(self) -> None:
        focus = replace(
            observation(signal_type="focus_monitoring"),
            period={
                "start": "2026-07-01",
                "end": "2026-08-31",
                "frequency": "monitoring_window",
            },
            dimensions={
                "market_scope": "provider_watchlist",
                "provider_security_code": "300999",
            },
        )
        severe = replace(
            observation(signal_type="severe_abnormal_movement"),
            source_document_id="severe-300999",
            rule={"rule_code": "4"},
            dimensions={
                "market_scope": "provider_anomaly_pool",
                "provider_security_code": "300999",
            },
        )

        class MonitoringOperation(FixedOperation):
            supported_signal_types = frozenset(
                {"focus_monitoring", "severe_abnormal_movement"}
            )

        operation = MonitoringOperation(
            SignalSourceBatch(
                operation_id="fixed_signal@1",
                observations=(focus, severe),
                coverage={
                    "focus_monitoring": SignalCoverage(state="observed_nonempty"),
                    "severe_abnormal_movement": SignalCoverage(
                        state="observed_nonempty"
                    ),
                },
            )
        )

        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [operation],
            FixtureIdentityTransport(bluefocus=False),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "identity_unverified", [item["code"] for item in result["source_errors"]]
        )

    def test_intersection_collects_current_monitor_and_latest_anomaly_dates(
        self,
    ) -> None:
        class FocusOperation:
            operation_id = "focus_signal@1"
            supported_signal_types = frozenset({"focus_monitoring"})

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                self.query = query
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        "focus_monitoring": SignalCoverage(
                            state="observed_empty", provider_total=0
                        )
                    },
                )

        class AnomalyOperation:
            operation_id = "anomaly_signal@1"
            supported_signal_types = frozenset({"severe_abnormal_movement"})

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                self.query = query
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        "severe_abnormal_movement": SignalCoverage(
                            state="observed_empty", provider_total=0
                        )
                    },
                )

        focus = FocusOperation()
        anomaly = AnomalyOperation()

        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [focus, anomaly],
            NoIdentityTransport(),
        )

        self.assertEqual(focus.query.observed_to, "2026-08-02")
        self.assertEqual(anomaly.query.observed_to, "2026-07-31")
        self.assertEqual(
            result["brief"]["coverage"]["monitoring_intersection"]["state"],
            "observed_empty",
        )

    def test_intersection_requires_complete_coverage_from_both_basis_types(
        self,
    ) -> None:
        class FocusOnlyOperation:
            operation_id = "focus_only@1"
            supported_signal_types = frozenset({"focus_monitoring"})

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        "focus_monitoring": SignalCoverage(
                            state="observed_empty", provider_total=0
                        )
                    },
                )

        result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [FocusOnlyOperation()],
            NoIdentityTransport(),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["brief"]["coverage"]["monitoring_intersection"]["state"],
            "indeterminate",
        )

        class EmptyButErroredOperation:
            operation_id = "empty_but_errored@1"
            supported_signal_types = frozenset(
                {"focus_monitoring", "severe_abnormal_movement"}
            )

            def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
                return SignalSourceBatch(
                    operation_id=self.operation_id,
                    coverage={
                        signal_type: SignalCoverage(
                            state="observed_empty", provider_total=0
                        )
                        for signal_type in self.supported_signal_types
                    },
                    source_errors=(
                        SignalSourceFailure(
                            self.operation_id,
                            "upstream_unavailable",
                            "A basis source was unavailable.",
                        ),
                    ),
                )

        errored_result = build_market_signals_result(
            signal_request(["monitoring_intersection"]),
            [EmptyButErroredOperation()],
            NoIdentityTransport(),
        )

        self.assertEqual(
            errored_result["brief"]["coverage"]["monitoring_intersection"]["state"],
            "partial",
        )


if __name__ == "__main__":
    unittest.main()
