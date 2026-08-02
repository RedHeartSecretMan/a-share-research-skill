from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.etf_option_contract import (  # noqa: E402
    EtfOptionSubject,
    OptionAnalytic,
    OptionContractListingEvidence,
    OptionContractMonthEvidence,
    OptionContractQuote,
    OptionCoverage,
    OptionQuery,
    OptionSession,
    OptionSourceBatch,
    OptionSourceFailure,
)
from a_share_research.etf_options import build_etf_options_result  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def option_request() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "etf_options",
        "subjects": [{"clue": "510050"}],
        "as_of": "2026-08-03",
        "window": {
            "observed_from": "2026-08-03",
            "observed_to": "2026-08-03",
        },
        "parameters": {
            "view": "atm",
            "expiry": {"mode": "nearest_unexpired"},
            "quote_mode": "latest",
        },
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def analytics(delta: str) -> dict[str, OptionAnalytic]:
    return {
        "delta": OptionAnalytic(delta, "dimensionless"),
        "gamma": OptionAnalytic("0.1200", "provider_native_unverified"),
        "theta": OptionAnalytic("-0.0010", "provider_native_unverified"),
        "vega": OptionAnalytic("0.0020", "provider_native_unverified"),
        "implied_volatility": OptionAnalytic("0.2400", "decimal_fraction"),
    }


def contract(
    code: str,
    option_type: str,
    strike: str,
    *,
    delta: str,
) -> OptionContractQuote:
    return OptionContractQuote(
        security={"exchange": "SSE", "code": code, "type": "ETF_OPTION"},
        option_type=option_type,
        strike=strike,
        contract_month="2026-08",
        expiry_date="2026-08-26",
        series="M",
        quote_state="quoted",
        last="0.0800",
        bid="0.0799",
        ask="0.0801",
        observed_at="2026-08-03T10:35:00+08:00",
        analytics=analytics(delta),
        source_operation="fixed_options@1",
        evidence_id=f"option-{code}",
        locator_uri=f"https://example.test/options/{code}",
        bid_size="10",
        ask_size="20",
        volume="1000",
        open_interest="2000",
        analytics_evidence_id=f"option-analytics-{code}",
        analytics_locator_uri=f"https://example.test/options/{code}/analytics",
        quote_retrieved_at=datetime(2026, 8, 3, 10, 35, 1, tzinfo=CHINA_STANDARD_TIME),
        analytics_retrieved_at=datetime(
            2026, 8, 3, 10, 35, 2, tzinfo=CHINA_STANDARD_TIME
        ),
    )


class FixedOptionOperation:
    operation_id = "fixed_options@1"

    def __init__(self, batch: OptionSourceBatch) -> None:
        self.batch = batch

    def collect(self, query: OptionQuery) -> OptionSourceBatch:
        self.query = query
        return self.batch


def complete_batch(*contracts: OptionContractQuote) -> OptionSourceBatch:
    retrieved_at = datetime(2026, 8, 3, 10, 35, tzinfo=CHINA_STANDARD_TIME)
    return OptionSourceBatch(
        operation_id="fixed_options@1",
        subject=EtfOptionSubject("SSE", "510050", "上证50ETF"),
        session=OptionSession(
            trading_date="2026-08-03",
            observed_at="2026-08-03T10:35:00+08:00",
            market_state="intraday",
            reference_price="3.02",
            reference_price_kind="last",
            reference_evidence_id="underlying-510050",
            locator_uri="https://example.test/underlying/510050",
            retrieved_at=datetime(2026, 8, 3, 10, 35, 1, tzinfo=CHINA_STANDARD_TIME),
            reference_observed_at="2026-08-03T10:34:59+08:00",
            reference_source_operation="fixed_underlying@1",
            reference_retrieved_at=datetime(
                2026, 8, 3, 10, 35, tzinfo=CHINA_STANDARD_TIME
            ),
        ),
        contracts=contracts,
        coverage={
            "contract_listing": OptionCoverage(
                "observed_nonempty",
                expected_count=len(contracts),
                observed_count=len(contracts),
            ),
            "option_quotes": OptionCoverage(
                "observed_nonempty",
                expected_count=len(contracts),
                observed_count=len(contracts),
            ),
            "provider_analytics": OptionCoverage(
                "observed_nonempty",
                expected_count=len(contracts),
                observed_count=len(contracts),
            ),
        },
        listing_evidence=tuple(
            OptionContractListingEvidence(
                source_operation="fixed_options@1",
                evidence_id=f"listing-{option_type}",
                option_type=option_type,
                contract_month="2026-08",
                observed_count=sum(
                    item.option_type == option_type for item in contracts
                ),
                locator_uri=f"https://example.test/options/list/{option_type}",
                retrieved_at=retrieved_at,
            )
            for option_type in ("call", "put")
        ),
        month_evidence=OptionContractMonthEvidence(
            source_operation="fixed_options@1",
            evidence_id="listing-months",
            observed_months=("2026-08", "2026-09"),
            identity_status="validated",
            locator_uri="https://example.test/options/months",
            retrieved_at=retrieved_at,
        ),
    )


class EtfOptionsContractTests(unittest.TestCase):
    def test_unique_atm_returns_complete_standard_call_put_pair(self) -> None:
        operation = FixedOptionOperation(
            complete_batch(
                contract("10000001", "call", "3.00", delta="0.55"),
                contract("10000002", "put", "3.00", delta="-0.45"),
                contract("10000003", "call", "3.10", delta="0.40"),
                contract("10000004", "put", "3.10", delta="-0.60"),
            )
        )

        result = build_etf_options_result(option_request(), [operation])

        self.assertEqual(result["task_type"], "etf_options")
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["atm"]["status"], "identified")
        self.assertEqual(result["atm"]["strike_candidates"], ["3.00"])
        self.assertEqual(len(result["contracts"]), 2)
        self.assertEqual(
            {item["contract"]["option_type"] for item in result["contracts"]},
            {"call", "put"},
        )
        self.assertEqual(
            result["contracts"][0]["analytics"]["delta"]["origin"],
            "provider_reported",
        )
        self.assertEqual(result["calculations"], [])
        self.assertEqual(operation.query.view, "atm")
        evidence = {item["id"]: item for item in result["evidence"]}
        self.assertEqual(
            evidence["underlying-510050"]["source_operation"],
            "fixed_underlying@1",
        )
        self.assertEqual(
            evidence["underlying-510050"]["evidence_time"],
            "2026-08-03T10:34:59+08:00",
        )
        self.assertEqual(
            evidence["option-analytics-10000001"]["locator"]["uri"],
            "https://example.test/options/10000001/analytics",
        )
        self.assertEqual(
            result["contracts"][0]["analytics"]["delta"]["evidence_ids"],
            ["option-analytics-10000001"],
        )

    def test_chain_keeps_standard_and_adjusted_series_as_distinct_pairs(self) -> None:
        request = option_request()
        request["parameters"]["view"] = "chain"
        operation = FixedOptionOperation(
            complete_batch(
                contract("10000001", "call", "3.00", delta="0.55"),
                contract("10000002", "put", "3.00", delta="-0.45"),
                replace(
                    contract("10000003", "call", "3.00", delta="0.56"),
                    series="A",
                ),
                replace(
                    contract("10000004", "put", "3.00", delta="-0.44"),
                    series="A",
                ),
            )
        )

        result = build_etf_options_result(request, [operation])

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["contracts"]), 4)
        self.assertEqual(
            [item["contract"]["series"] for item in result["contracts"]],
            ["M", "M", "A", "A"],
        )
        self.assertEqual(len(result["t_quote"]["rows"]), 2)
        self.assertEqual(
            [item["series"] for item in result["t_quote"]["rows"]], ["A", "M"]
        )
        self.assertTrue(
            all(
                row["call_security"]["code"] != row["put_security"]["code"]
                for row in result["t_quote"]["rows"]
            )
        )
        self.assertTrue(
            all(
                row[side]["quote"]["observed_at"]
                and row[side]["analytics"]["implied_volatility"]["origin"]
                == "provider_reported"
                for row in result["t_quote"]["rows"]
                for side in ("call", "put")
            )
        )

    def test_exact_expired_contract_selection_is_explicitly_blocked(self) -> None:
        request = option_request()
        request["parameters"]["expiry"] = {
            "mode": "exact",
            "date": "2026-07-30",
        }
        operation = FixedOptionOperation(
            complete_batch(
                replace(
                    contract("10000001", "call", "3.00", delta="0.55"),
                    contract_month="2026-07",
                    expiry_date="2026-07-30",
                ),
                replace(
                    contract("10000002", "put", "3.00", delta="-0.45"),
                    contract_month="2026-07",
                    expiry_date="2026-07-30",
                ),
            )
        )

        result = build_etf_options_result(request, [operation])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["limitations"][0]["code"], "option_contract_expired")

    def test_missing_call_or_put_side_does_not_silently_drop_a_strike(self) -> None:
        operation = FixedOptionOperation(
            complete_batch(
                contract("10000001", "call", "3.00", delta="0.55"),
                contract("10000003", "call", "3.10", delta="0.40"),
                contract("10000004", "put", "3.10", delta="-0.60"),
            )
        )

        result = build_etf_options_result(option_request(), [operation])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["limitations"][0]["code"], "option_pair_incomplete")

    def test_no_quote_atm_contract_is_retained_and_blocks_the_answer(self) -> None:
        operation = FixedOptionOperation(
            complete_batch(
                replace(
                    contract("10000001", "call", "3.00", delta="0.55"),
                    quote_state="no_quote",
                    last=None,
                    bid=None,
                    ask=None,
                ),
                contract("10000002", "put", "3.00", delta="-0.45"),
            )
        )

        result = build_etf_options_result(option_request(), [operation])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["contracts"][0]["quote"]["state"], "no_quote")
        self.assertEqual(result["brief"]["no_quote_contract_count"], 1)
        self.assertIn(
            "option_quote_unavailable",
            {item["code"] for item in result["limitations"]},
        )

    def test_latest_completed_rejects_an_intraday_snapshot(self) -> None:
        request = option_request()
        request["parameters"]["quote_mode"] = "latest_completed"
        operation = FixedOptionOperation(
            complete_batch(
                contract("10000001", "call", "3.00", delta="0.55"),
                contract("10000002", "put", "3.00", delta="-0.45"),
            )
        )

        result = build_etf_options_result(request, [operation])

        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "option_quote_mode_mismatch",
            {item["code"] for item in result["limitations"]},
        )

    def test_conflicting_duplicate_contract_is_not_silently_overwritten(self) -> None:
        duplicate = contract("10000001", "call", "3.00", delta="0.55")
        operation = FixedOptionOperation(
            complete_batch(
                duplicate,
                replace(duplicate, last="0.0810"),
                contract("10000002", "put", "3.00", delta="-0.45"),
            )
        )

        result = build_etf_options_result(option_request(), [operation])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["conflicts"][0]["code"],
            "duplicate_option_contract_conflict",
        )

    def test_source_error_and_indeterminate_coverage_are_preserved(self) -> None:
        batch = complete_batch()
        batch = replace(
            batch,
            coverage={
                "contract_listing": OptionCoverage("indeterminate"),
                "option_quotes": OptionCoverage("indeterminate"),
                "provider_analytics": OptionCoverage("indeterminate"),
            },
            source_errors=(
                OptionSourceFailure(
                    "fixed_options@1",
                    "upstream_unavailable",
                    "The upstream option source was unavailable.",
                ),
            ),
        )

        result = build_etf_options_result(
            option_request(), [FixedOptionOperation(batch)]
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["brief"]["coverage"]["contract_listing"]["state"],
            "indeterminate",
        )
        self.assertEqual(result["source_errors"][0]["code"], "upstream_unavailable")

    def test_blocked_source_boundary_retains_referenced_etf_identity_evidence(
        self,
    ) -> None:
        identity_retrieved_at = datetime(
            2026, 8, 3, 10, 34, 58, tzinfo=CHINA_STANDARD_TIME
        )
        batch = replace(
            complete_batch(),
            subject=EtfOptionSubject(
                "SSE",
                "510050",
                "上证50ETF",
                identity_evidence_id="etf-identity-sse_etf_list@1-SSE:510050",
                identity_locator_uri="https://example.test/etf-list/510050",
                identity_retrieved_at=identity_retrieved_at,
                identity_observed_on="2005-02-23",
            ),
            session=None,
            contracts=(),
            source_errors=(
                OptionSourceFailure(
                    "fixed_options@1",
                    "quote_time_conflict",
                    "Option contracts in one snapshot have different quote times.",
                ),
            ),
        )

        result = build_etf_options_result(
            option_request(), [FixedOptionOperation(batch)]
        )

        subject_evidence_id = result["subjects"][0]["evidence_ids"][0]
        evidence = {item["id"]: item for item in result["evidence"]}
        self.assertIn(subject_evidence_id, evidence)
        self.assertEqual(
            evidence[subject_evidence_id]["locator"]["uri"],
            "https://example.test/etf-list/510050",
        )
        self.assertEqual(
            evidence[subject_evidence_id]["source_operation"], "sse_etf_list@1"
        )

    def test_equidistant_standard_strikes_are_all_retained_as_atm(self) -> None:
        operation = FixedOptionOperation(
            complete_batch(
                contract("10000001", "call", "3.00", delta="0.55"),
                contract("10000002", "put", "3.00", delta="-0.45"),
                contract("10000003", "call", "3.04", delta="0.45"),
                contract("10000004", "put", "3.04", delta="-0.55"),
            )
        )

        result = build_etf_options_result(option_request(), [operation])

        self.assertEqual(result["atm"]["status"], "tie")
        self.assertEqual(result["atm"]["strike_candidates"], ["3.00", "3.04"])
        self.assertEqual(len(result["contracts"]), 4)

    def test_request_requires_supported_subject_single_day_and_known_modes(
        self,
    ) -> None:
        invalid_requests = []
        unsupported = option_request()
        unsupported["subjects"] = [{"clue": "159919"}]
        invalid_requests.append(unsupported)
        range_request = option_request()
        range_request["window"]["observed_from"] = "2026-08-02"
        invalid_requests.append(range_request)
        unknown_view = option_request()
        unknown_view["parameters"]["view"] = "surface"
        invalid_requests.append(unknown_view)
        malformed_expiry = option_request()
        malformed_expiry["parameters"]["expiry"] = {
            "mode": "exact",
            "date": "2026-8-26",
        }
        invalid_requests.append(malformed_expiry)

        for request in invalid_requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                build_etf_options_result(request, [])

    def test_provider_analytics_cannot_be_relabelled_as_project_calculation(
        self,
    ) -> None:
        call = contract("10000001", "call", "3.00", delta="0.55")
        call = replace(
            call,
            analytics={
                **call.analytics,
                "delta": OptionAnalytic(
                    "0.55", "dimensionless", origin="project_calculated"
                ),
            },
        )
        operation = FixedOptionOperation(
            complete_batch(
                call,
                contract("10000002", "put", "3.00", delta="-0.45"),
            )
        )

        result = build_etf_options_result(option_request(), [operation])

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["calculations"], [])
        self.assertIn(
            "option_metric_origin_unverified",
            {item["code"] for item in result["limitations"]},
        )

    def test_source_subject_and_all_quote_times_must_match_request(self) -> None:
        base = complete_batch(
            contract("10000001", "call", "3.00", delta="0.55"),
            contract("10000002", "put", "3.00", delta="-0.45"),
        )
        wrong_subject = replace(
            base, subject=EtfOptionSubject("SSE", "510300", "沪深300ETF")
        )
        wrong_session_day = replace(
            base,
            session=replace(base.session, trading_date="2026-08-02"),
        )
        wrong_quote_day = replace(
            base,
            contracts=(
                replace(base.contracts[0], observed_at="2026-08-02T15:00:00+08:00"),
                base.contracts[1],
            ),
        )

        for batch, expected_code in (
            (wrong_subject, "option_subject_identity_mismatch"),
            (wrong_session_day, "option_session_date_mismatch"),
            (wrong_quote_day, "option_quote_time_mismatch"),
        ):
            with self.subTest(expected_code=expected_code):
                result = build_etf_options_result(
                    option_request(), [FixedOptionOperation(batch)]
                )
                self.assertEqual(result["status"], "blocked")
                self.assertIn(
                    expected_code,
                    {item["code"] for item in result["limitations"]},
                )

    def test_complete_looking_batch_with_source_error_or_bad_coverage_blocks(
        self,
    ) -> None:
        base = complete_batch(
            contract("10000001", "call", "3.00", delta="0.55"),
            contract("10000002", "put", "3.00", delta="-0.45"),
        )
        source_error = replace(
            base,
            source_errors=(
                OptionSourceFailure(
                    "fixed_options@1", "batch_response_incomplete", "missing row"
                ),
            ),
        )
        bad_coverage = replace(
            base,
            coverage={
                **base.coverage,
                "contract_listing": OptionCoverage(
                    "partial", expected_count=4, observed_count=2
                ),
            },
        )

        for batch in (source_error, bad_coverage):
            with self.subTest(batch=batch):
                result = build_etf_options_result(
                    option_request(), [FixedOptionOperation(batch)]
                )
                self.assertEqual(result["status"], "blocked")
                self.assertIn(
                    "option_source_not_complete",
                    {item["code"] for item in result["limitations"]},
                )

    def test_provider_analytics_require_complete_valid_values_and_units(self) -> None:
        base = contract("10000001", "call", "3.00", delta="0.55")
        variants = (
            replace(base, analytics={"delta": base.analytics["delta"]}),
            replace(
                base,
                analytics={
                    **base.analytics,
                    "gamma": OptionAnalytic("0.12", "1/CNY"),
                },
            ),
            replace(
                base,
                analytics={
                    **base.analytics,
                    "delta": OptionAnalytic("1.2", "dimensionless"),
                },
            ),
            replace(
                base,
                analytics={
                    **base.analytics,
                    "implied_volatility": OptionAnalytic("14.83", "decimal_fraction"),
                },
            ),
        )
        for call in variants:
            with self.subTest(call=call):
                result = build_etf_options_result(
                    option_request(),
                    [
                        FixedOptionOperation(
                            complete_batch(
                                call,
                                contract("10000002", "put", "3.00", delta="-0.45"),
                            )
                        )
                    ],
                )
                self.assertEqual(result["status"], "blocked")
                self.assertIn(
                    "option_analytics_contract_invalid",
                    {item["code"] for item in result["limitations"]},
                )

    def test_multiple_expiries_are_distinct_and_exact_must_exist(self) -> None:
        august = (
            contract("10000001", "call", "3.00", delta="0.55"),
            contract("10000002", "put", "3.00", delta="-0.45"),
        )
        september = tuple(
            replace(
                item,
                security={**item.security, "code": str(int(item.security["code"]) + 2)},
                contract_month="2026-09",
                expiry_date="2026-09-23",
            )
            for item in august
        )
        batch = complete_batch(*august, *september)
        request = option_request()
        request["parameters"]["expiry"] = {
            "mode": "exact",
            "date": "2026-09-23",
        }

        result = build_etf_options_result(request, [FixedOptionOperation(batch)])

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["contract_set"]["expiry_date"], "2026-09-23")
        missing = option_request()
        missing["parameters"]["expiry"] = {
            "mode": "exact",
            "date": "2026-10-28",
        }
        blocked = build_etf_options_result(missing, [FixedOptionOperation(batch)])
        self.assertEqual(
            blocked["limitations"][0]["code"], "option_expiry_not_available"
        )

    def test_decimal_equivalent_strikes_pair_and_quote_fields_are_preserved(
        self,
    ) -> None:
        call = contract("10000001", "call", "3.0", delta="0.55")
        put = contract("10000002", "put", "3.00", delta="-0.45")

        result = build_etf_options_result(
            option_request(), [FixedOptionOperation(complete_batch(call, put))]
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["atm"]["strike_candidates"], ["3.0"])
        quote = result["contracts"][0]["quote"]
        self.assertEqual(quote["bid_size"], {"value": "10", "unit": "contract"})
        self.assertEqual(quote["volume"], {"value": "1000", "unit": "contract"})

    def test_v01_requires_exactly_one_source_operation(self) -> None:
        batch = complete_batch(
            contract("10000001", "call", "3.00", delta="0.55"),
            contract("10000002", "put", "3.00", delta="-0.45"),
        )

        result = build_etf_options_result(
            option_request(), [FixedOptionOperation(batch), FixedOptionOperation(batch)]
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["limitations"][0]["code"], "option_source_count_invalid"
        )


if __name__ == "__main__":
    unittest.main()
