from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.etf_option_contract import OptionQuery  # noqa: E402
from a_share_research.etf_option_sources import (  # noqa: E402
    SinaEtfOptionSnapshotOperation,
)
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.source_throttle import RequestGateDiagnostic  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 10, 30, tzinfo=CHINA_STANDARD_TIME)
FIXTURES = Path(__file__).parent / "fixtures" / "etf_options"
T = TypeVar("T")


class DiagnosticGate:
    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), (RequestGateDiagnostic("source_request_paced", 1.0),)


class RateLimitDiagnosticGate:
    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), (RequestGateDiagnostic("rate_limit_backoff", 2.0, attempt=1),)


class FixtureTransport:
    def __init__(
        self,
        code: str = "510050",
        *,
        overrides: dict[str, HttpResponse] | None = None,
    ) -> None:
        self.code = code
        self.overrides = overrides or {}
        self.get_calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.get_calls.append(url)
        for marker, response in self.overrides.items():
            if marker in url:
                return response
        if "commonSoaQuery.do" in url:
            return json_response(
                {
                    "pageHelp": {"total": 1},
                    "result": [
                        {
                            "listingDate": "20050223",
                            "subClass": "01",
                            "companyName": "测试基金",
                            "fundAbbr": "ETF",
                            "fundType": "00",
                            "fundCode": self.code,
                            "secNameFull": f"测试ETF{self.code}",
                        }
                    ],
                    "sqlId": "FUND_LIST",
                }
            )
        if "yunhq.sse.com.cn" in url:
            return json_response(
                {
                    "code": self.code,
                    "date": 20260731,
                    "time": 150000,
                    "snap": [
                        "ETF",
                        f"测试ETF{self.code}",
                        3.01,
                        3.05,
                        2.99,
                        3.03,
                        3.00,
                        1.0,
                        100000,
                        303000,
                        "E110",
                    ],
                }
            )
        if "StockOptionService.getStockName" in url:
            if self.code == "588000":
                return file_response(
                    "months_588000.json", "application/json", gbk=False
                )
            return json_response(
                {
                    "result": {
                        "status": {"code": 0},
                        "data": {
                            "cateList": [],
                            "contractMonth": [
                                "2026-08",
                                "2026-08",
                                "2026-09",
                                "2026-12",
                                "2027-03",
                            ],
                            "stockId": self.code,
                            "cateId": f"{self.code}C2608M03000",
                        },
                    }
                }
            )
        if "OP_UP_" in url:
            if self.code == "510500" and "2609" in url:
                return file_response("calls_510500_2609.txt", "text/plain", gbk=True)
            return replace_underlying(
                file_response("calls_510050_2608.txt", "text/plain", gbk=True),
                self.code,
            )
        if "OP_DOWN_" in url:
            if self.code == "510500" and "2609" in url:
                return file_response("puts_510500_2609.txt", "text/plain", gbk=True)
            return replace_underlying(
                file_response("puts_510050_2608.txt", "text/plain", gbk=True),
                self.code,
            )
        if "CON_OP_" in url:
            name = (
                "tquote_adjusted_510500.txt"
                if self.code == "510500" and "10011281" in url
                else "tquotes_510050_2608.txt"
            )
            response = file_response(name, "text/plain", gbk=True)
            return replace_underlying(response, self.code)
        if "CON_SO_" in url:
            name = (
                "analytics_adjusted_510500.txt"
                if self.code == "510500" and "10011281" in url
                else "analytics_510050_2608.txt"
            )
            response = file_response(name, "text/plain", gbk=True)
            return replace_underlying(response, self.code)
        raise AssertionError(f"unexpected ETF-option URL: {url}")

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("ETF-option sources use GET")


def json_response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def file_response(name: str, content_type: str, *, gbk: bool) -> HttpResponse:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=text.encode("gbk" if gbk else "utf-8"),
        retrieved_at=RETRIEVED_AT,
    )


def text_response(text: str) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="text/plain",
        body=text.encode("gbk"),
        retrieved_at=RETRIEVED_AT,
    )


def replace_underlying(response: HttpResponse, code: str) -> HttpResponse:
    if code == "510050":
        return response
    body = response.body.decode("gbk").replace("510050", code).encode("gbk")
    return HttpResponse(
        status=response.status,
        content_type=response.content_type,
        body=body,
        retrieved_at=response.retrieved_at,
    )


def query(
    code: str = "510050",
    *,
    observed_on: str = "2026-07-31",
    expiry_mode: str = "nearest_unexpired",
    expiry_date: str | None = None,
) -> OptionQuery:
    return OptionQuery(
        subject_clue=code,
        as_of="2026-08-02",
        observed_on=observed_on,
        view="atm",
        expiry_mode=expiry_mode,
        expiry_date=expiry_date,
        quote_mode="latest_completed",
    )


class SinaEtfOptionSnapshotTests(unittest.TestCase):
    def test_collects_gbk_call_put_quotes_and_provider_analytics(self) -> None:
        transport = FixtureTransport()
        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.subject.code, "510050")
        self.assertEqual(
            batch.subject.identity_evidence_id,
            "etf-identity-sse_etf_list@1-SSE:510050",
        )
        self.assertEqual(batch.session.observed_at, "2026-07-31T15:00:00+08:00")
        self.assertEqual(
            batch.session.reference_observed_at, "2026-07-31T15:00:00+08:00"
        )
        self.assertEqual(batch.coverage["contract_listing"].state, "partial")
        self.assertIsNone(batch.coverage["contract_listing"].expected_count)
        self.assertEqual(len(batch.contracts), 2)
        call = next(item for item in batch.contracts if item.option_type == "call")
        self.assertEqual(call.security["code"], "10011855")
        self.assertEqual(call.contract_month, "2026-08")
        self.assertEqual(call.expiry_date, "2026-08-26")
        self.assertEqual(call.series, "M")
        self.assertEqual(call.strike, "3.0000")
        self.assertEqual(call.bid, "0.0710")
        self.assertEqual(call.ask, "0.0712")
        self.assertEqual(call.bid_size, "2")
        self.assertEqual(call.ask_size, "25")
        self.assertEqual(call.volume, "78077")
        self.assertEqual(call.open_interest, "86053")
        self.assertEqual(call.analytics["delta"].value, "0.6095")
        self.assertEqual(call.analytics["gamma"].unit, "provider_native_unverified")
        self.assertEqual(call.analytics["implied_volatility"].unit, "decimal_fraction")
        self.assertEqual(call.analytics["implied_volatility"].value, "0.1483")
        self.assertEqual(
            call.analytics["theoretical_value"].origin, "provider_reported"
        )
        self.assertIn("CON_OP_", call.locator_uri)
        self.assertIn("CON_SO_", call.analytics_locator_uri)
        self.assertNotEqual(call.evidence_id, call.analytics_evidence_id)
        self.assertIn(
            "source_request_paced", [item.code for item in batch.degradations]
        )
        option_batch_urls = [
            url for url in transport.get_calls if "CON_OP_" in url or "CON_SO_" in url
        ]
        self.assertTrue(option_batch_urls)
        self.assertTrue(
            all(
                url.count("CON_OP_") <= 2 and url.count("CON_SO_") <= 2
                for url in option_batch_urls
            )
        )
        self.assertTrue(all("%2C" not in url for url in option_batch_urls))
        self.assertTrue(any("," in url for url in option_batch_urls))

    def test_supported_etfs_use_their_exact_sina_category_and_identity(self) -> None:
        expected = {
            "510050": "50ETF",
            "510300": "300ETF",
            "510500": "500ETF",
            "588000": "科创50",
        }
        for code, category in expected.items():
            with self.subTest(code=code):
                transport = FixtureTransport(code)
                selected_query = (
                    query(code, expiry_mode="exact", expiry_date="2026-09-23")
                    if code == "510500"
                    else query(code)
                )
                batch = SinaEtfOptionSnapshotOperation(
                    transport, request_gate=DiagnosticGate()
                ).collect(selected_query)

                self.assertEqual(batch.source_errors, ())
                self.assertEqual(batch.subject.code, code)
                months_url = next(
                    url for url in transport.get_calls if "getStockName" in url
                )
                self.assertEqual(
                    parse_qs(urlsplit(months_url).query)["cate"], [category]
                )

    def test_588000_silent_50etf_fallback_fails_closed(self) -> None:
        transport = FixtureTransport(
            "588000",
            overrides={
                "getStockName": file_response(
                    "months_588000_silent_fallback.json",
                    "application/json",
                    gbk=False,
                )
            },
        )

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query("588000"))

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "wrong_underlying_payload")

    def test_missing_batch_symbol_is_not_treated_as_an_empty_contract(self) -> None:
        only_call = (
            (FIXTURES / "analytics_510050_2608.txt")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        transport = FixtureTransport(overrides={"CON_SO_": text_response(only_call)})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "batch_response_incomplete")
        self.assertEqual(batch.coverage["provider_analytics"].state, "indeterminate")

    def test_shifted_analytics_fields_fail_schema_validation(self) -> None:
        text = (FIXTURES / "analytics_510050_2608.txt").read_text(encoding="utf-8")
        shifted = text.replace(",,,,78077", ",,,78077", 1)
        transport = FixtureTransport(overrides={"CON_SO_": text_response(shifted)})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "unknown_schema")

    def test_non_gbk_javascript_fails_schema_validation(self) -> None:
        invalid_gbk = HttpResponse(
            status=200,
            content_type="text/plain",
            body=b"\x81",
            retrieved_at=RETRIEVED_AT,
        )
        transport = FixtureTransport(overrides={"OP_UP_": invalid_gbk})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "unknown_schema")

    def test_adjusted_contract_keeps_provider_strike_and_series(self) -> None:
        batch = SinaEtfOptionSnapshotOperation(
            FixtureTransport("510500"), request_gate=DiagnosticGate()
        ).collect(query("510500", expiry_mode="exact", expiry_date="2026-09-23"))

        self.assertEqual(batch.source_errors, ())
        call = next(item for item in batch.contracts if item.option_type == "call")
        self.assertEqual(call.series, "A")
        self.assertEqual(call.strike, "6.3850")
        self.assertEqual(call.expiry_date, "2026-09-23")

    def test_exact_expiry_validates_the_full_date_not_only_the_month(self) -> None:
        batch = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        ).collect(
            query(
                expiry_mode="exact",
                expiry_date="2026-08-25",
            )
        )

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "option_expiry_not_available")

    def test_nearest_month_selection_is_independent_of_provider_order(self) -> None:
        months = json_response(
            {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "contractMonth": ["2026-09", "2026-08", "2026-12"],
                        "stockId": "510050",
                    },
                }
            }
        )
        transport = FixtureTransport(overrides={"getStockName": months})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.source_errors, ())
        listing_url = next(url for url in transport.get_calls if "OP_UP_" in url)
        self.assertIn("2608", listing_url)

    def test_duplicate_conflicting_quote_rows_fail_closed(self) -> None:
        text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        first = text.splitlines()[0]
        conflicting = first.replace("0.0712", "0.0999", 1)
        transport = FixtureTransport(
            overrides={"CON_OP_": text_response(text + conflicting + "\n")}
        )

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "duplicate_contract_conflict")

    def test_quote_and_analytics_contract_identity_must_match(self) -> None:
        text = (FIXTURES / "analytics_510050_2608.txt").read_text(encoding="utf-8")
        wrong_identity = text.replace("510050C2608M03000", "510300C2608M03000", 1)
        transport = FixtureTransport(
            overrides={"CON_SO_": text_response(wrong_identity)}
        )

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "analytics_identity_mismatch")

    def test_analytics_trade_code_strike_must_match_quote(self) -> None:
        text = (FIXTURES / "analytics_510050_2608.txt").read_text(encoding="utf-8")
        wrong_strike = text.replace("510050C2608M03000", "510050C2608M03100", 1)
        transport = FixtureTransport(overrides={"CON_SO_": text_response(wrong_strike)})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "analytics_identity_mismatch")

    def test_no_quote_is_explicit_partial_coverage(self) -> None:
        text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        no_call_quote = text.replace(
            '="2,0.0710,0.0712,0.0712,25',
            '="2,0,0,0,25',
            1,
        )
        transport = FixtureTransport(
            overrides={"CON_OP_": text_response(no_call_quote)}
        )

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        call = next(item for item in batch.contracts if item.option_type == "call")
        self.assertEqual(call.quote_state, "no_quote")
        self.assertEqual(batch.coverage["option_quotes"].state, "partial")
        self.assertEqual(batch.source_errors[0].code, "no_quote")

    def test_two_sided_quote_survives_when_last_trade_is_unavailable(self) -> None:
        text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        no_last = text.replace(
            '="2,0.0710,0.0712,0.0712,25',
            '="2,0.0710,0,0.0712,25',
            1,
        )
        transport = FixtureTransport(overrides={"CON_OP_": text_response(no_last)})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        call = next(item for item in batch.contracts if item.option_type == "call")
        self.assertEqual(call.quote_state, "quoted")
        self.assertEqual(call.bid, "0.0710")
        self.assertEqual(call.ask, "0.0712")
        self.assertIsNone(call.last)
        self.assertIn("last_trade_unavailable", call.limitations)

    def test_expired_quote_and_wrong_trading_date_fail_explicitly(self) -> None:
        text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        expired = text.replace("2026-08-26", "2026-07-30")
        wrong_date = text.replace("2026-07-31 15:00:00", "2026-07-30 15:00:00")
        cases = [
            (expired, "expired_or_wrong_expiry"),
            (wrong_date, "quote_date_mismatch"),
        ]
        for raw, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                batch = SinaEtfOptionSnapshotOperation(
                    FixtureTransport(overrides={"CON_OP_": text_response(raw)}),
                    request_gate=DiagnosticGate(),
                ).collect(query())
                self.assertEqual(batch.contracts, ())
                self.assertEqual(batch.source_errors[0].code, expected_code)

    def test_mixed_contract_quote_times_fail_instead_of_fabricating_a_close(
        self,
    ) -> None:
        text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        lines = text.splitlines()
        lines[1] = lines[1].replace("2026-07-31 15:00:00", "2026-07-31 00:00:00")
        transport = FixtureTransport(
            overrides={"CON_OP_": text_response("\n".join(lines) + "\n")}
        )

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        failure = batch.source_errors[0]
        self.assertEqual(failure.code, "quote_time_conflict")
        self.assertEqual(
            failure.details["observed_at"],
            [
                "2026-07-31T00:00:00+08:00",
                "2026-07-31T15:00:00+08:00",
            ],
        )
        self.assertEqual(
            failure.details["contract_counts_by_observed_at"],
            [
                {
                    "observed_at": "2026-07-31T00:00:00+08:00",
                    "contract_count": 1,
                },
                {
                    "observed_at": "2026-07-31T15:00:00+08:00",
                    "contract_count": 1,
                },
            ],
        )
        quote_batch_evidence = failure.details["quote_batch_evidence"]
        self.assertEqual(len(quote_batch_evidence), 1)
        self.assertEqual(
            quote_batch_evidence[0]["locator"]["uri"],
            ("https://hq.sinajs.cn/?list=CON_OP_10011855,CON_OP_10011864"),
        )
        self.assertEqual(
            quote_batch_evidence[0]["retrieved_at"], RETRIEVED_AT.isoformat()
        )
        self.assertEqual(quote_batch_evidence[0]["status"], "rejected")
        self.assertEqual(
            quote_batch_evidence[0]["rejection_code"], "quote_time_conflict"
        )
        self.assertEqual(quote_batch_evidence[0]["contract_count"], 2)
        self.assertEqual(
            quote_batch_evidence[0]["observed_at"],
            [
                "2026-07-31T00:00:00+08:00",
                "2026-07-31T15:00:00+08:00",
            ],
        )
        self.assertNotIn("raw_payload", quote_batch_evidence[0])

    def test_midnight_quote_marker_is_not_silently_called_intraday(self) -> None:
        text = (FIXTURES / "tquotes_510050_2608.txt").read_text(encoding="utf-8")
        midnight = text.replace("2026-07-31 15:00:00", "2026-07-31 00:00:00")
        transport = FixtureTransport(overrides={"CON_OP_": text_response(midnight)})

        batch = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "session_state_unknown")

    def test_current_snapshot_does_not_claim_historical_coverage(self) -> None:
        batch = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        ).collect(query(observed_on="2026-07-30"))

        self.assertEqual(batch.contracts, ())
        self.assertEqual(batch.source_errors[0].code, "quote_date_mismatch")

    def test_rate_limit_backoff_is_preserved_as_a_degradation(self) -> None:
        batch = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=RateLimitDiagnosticGate()
        ).collect(query())

        self.assertEqual(batch.source_errors, ())
        degradation = next(
            item for item in batch.degradations if item.code == "rate_limit_backoff"
        )
        self.assertEqual(degradation.details["attempt"], "1")
        self.assertEqual(degradation.details["delay_seconds"], "2.000")


if __name__ == "__main__":
    unittest.main()
