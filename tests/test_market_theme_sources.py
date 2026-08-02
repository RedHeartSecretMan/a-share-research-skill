from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse, TransportError  # noqa: E402
from a_share_research.market_signal_contract import MarketSignalQuery  # noqa: E402
from a_share_research.market_signals import build_market_signals_result  # noqa: E402
from a_share_research.market_theme_sources import (  # noqa: E402
    EastmoneyIndustryRotationOperation,
    EastmoneySecurityBoardMembershipOperation,
    ThsMarketHeatOperation,
    ThsStrongStockThemeOperation,
)
from a_share_research.source_throttle import RequestGateDiagnostic  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 10, 30, tzinfo=CHINA_STANDARD_TIME)
T = TypeVar("T")


class FixedTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.get_calls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.get_calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise AssertionError("market-theme operations use GET")


class DiagnosticGate:
    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        return request(), (RequestGateDiagnostic("source_request_paced", 1.2),)


class NoIdentityTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        raise AssertionError(f"market-wide task must not resolve identity: {url}")


def response(payload: object, *, retrieved_at: datetime = RETRIEVED_AT) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        retrieved_at=retrieved_at,
    )


def query(
    signal_type: str,
    *,
    observed_on: str = "2026-07-31",
    subject: dict[str, object] | None = None,
    parameters: dict[str, object] | None = None,
    limit: int = 20,
) -> MarketSignalQuery:
    return MarketSignalQuery(
        signal_types=(signal_type,),
        as_of="2026-08-02",
        observed_from=observed_on,
        observed_to=observed_on,
        limit=limit,
        subject=subject,
        parameters=parameters or {},
    )


def canonical_subject(
    exchange: str = "SZSE", code: str = "300058"
) -> dict[str, object]:
    return {
        "security": {"exchange": exchange, "code": code, "type": "A_SHARE"},
        "name": "蓝色光标",
    }


def market_request(signal_type: str, observed_on: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "market_signals",
        "subjects": [],
        "as_of": "2026-08-02",
        "window": {
            "observed_from": observed_on,
            "observed_to": observed_on,
        },
        "parameters": {"signal_types": [signal_type], "limit": 20},
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


class ThsStrongStockThemeTests(unittest.TestCase):
    def test_editorial_theme_has_stable_locator_and_plaintext_risk(self) -> None:
        payload = {
            "errocode": 0,
            "data": [
                {
                    "code": "300058",
                    "name": "蓝色光标",
                    "reason": "AI营销+智谱AI",
                    "zhangfu": "10.01",
                    "close": "12.34",
                    "huanshou": "8.50",
                    "chengjiaoe": "123456789.12",
                    "market": "深",
                }
            ],
        }
        transport = FixedTransport([response(payload)])

        batch = ThsStrongStockThemeOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(query("strong_stock_theme"))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(batch.degradations[0].code, "source_request_paced")
        self.assertEqual(
            batch.coverage["strong_stock_theme"].state, "observed_nonempty"
        )
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2026-07-31")
        self.assertEqual(item.metrics["change_rate"], "10.01")
        self.assertEqual(item.attributions[0].provenance, "editorial_annotation")
        self.assertEqual(item.attributions[0].text, "AI营销+智谱AI")
        self.assertEqual(
            item.source_document_id,
            "ths-getharden-20260731-300058",
        )
        self.assertEqual(
            item.attributions[0].source_document_id,
            item.source_document_id,
        )
        self.assertEqual(item.locator_uri, transport.get_calls[0])
        self.assertTrue(item.locator_uri.startswith("http://"))
        self.assertIn("plaintext_http_source", item.limitations)
        self.assertIn("security_exchange_unverified", item.limitations)
        self.assertEqual(item.dimensions["market_scope"], "mainland_a_share")

    def test_public_coordinator_accepts_the_source_observation(self) -> None:
        operation = ThsStrongStockThemeOperation(
            FixedTransport(
                [
                    response(
                        {
                            "errocode": 0,
                            "data": [
                                {
                                    "code": "300058",
                                    "name": "蓝色光标",
                                    "reason": "AI营销",
                                    "zhangfu": "10.01",
                                }
                            ],
                        }
                    )
                ]
            ),
            request_gate=DiagnosticGate(),
        )
        result = build_market_signals_result(
            market_request("strong_stock_theme", "2026-07-31"),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["observations"]), 1)
        self.assertNotIn(
            "unknown_schema", [item["code"] for item in result["source_errors"]]
        )

    def test_business_empty_is_distinct_from_missing_schema(self) -> None:
        empty = ThsStrongStockThemeOperation(
            FixedTransport([response({"errocode": 0, "data": []})]),
            request_gate=DiagnosticGate(),
        ).collect(query("strong_stock_theme"))
        missing = ThsStrongStockThemeOperation(
            FixedTransport([response({"errocode": 0})]),
            request_gate=DiagnosticGate(),
        ).collect(query("strong_stock_theme"))

        self.assertEqual(empty.observations, ())
        self.assertEqual(empty.source_errors, ())
        self.assertEqual(empty.coverage["strong_stock_theme"].state, "observed_empty")
        self.assertEqual(missing.coverage["strong_stock_theme"].state, "indeterminate")
        self.assertEqual(missing.source_errors[0].code, "unknown_schema")

    def test_duplicate_security_and_rate_limit_fail_closed(self) -> None:
        row = {
            "code": "300058",
            "name": "蓝色光标",
            "reason": "AI营销",
            "zhangfu": "10.01",
        }
        duplicate = ThsStrongStockThemeOperation(
            FixedTransport([response({"errocode": 0, "data": [row, row]})]),
            request_gate=DiagnosticGate(),
        ).collect(query("strong_stock_theme"))
        rate_limited = ThsStrongStockThemeOperation(
            FixedTransport([TransportError("rate_limited", "slow down")]),
            request_gate=DiagnosticGate(),
        ).collect(query("strong_stock_theme"))

        self.assertEqual(duplicate.observations, ())
        self.assertEqual(duplicate.source_errors[0].code, "duplicate_records")
        self.assertEqual(rate_limited.observations, ())
        self.assertEqual(rate_limited.source_errors[0].code, "rate_limited")


class EastmoneySecurityBoardMembershipTests(unittest.TestCase):
    def test_uses_canonical_exchange_and_proves_all_pages_before_limiting(self) -> None:
        transport = FixedTransport(
            [
                response(
                    {
                        "data": {
                            "secid": "0.300058",
                            "total": 2,
                            "diff": [
                                {
                                    "f12": "BK0420",
                                    "f14": "文化传媒",
                                    "f3": "1.23",
                                    "f128": "读客文化",
                                }
                            ],
                        }
                    }
                ),
                response(
                    {
                        "data": {
                            "secid": "0.300058",
                            "total": 2,
                            "diff": [
                                {
                                    "f12": "BK1234",
                                    "f14": "AIGC概念",
                                    "f3": "2.34",
                                    "f128": "蓝色光标",
                                }
                            ],
                        }
                    }
                ),
            ]
        )

        batch = EastmoneySecurityBoardMembershipOperation(
            transport,
            page_size=1,
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "security_board_membership",
                observed_on="2026-08-02",
                subject=canonical_subject(),
                limit=1,
            )
        )

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(batch.coverage["security_board_membership"].provider_total, 2)
        self.assertEqual(batch.coverage["security_board_membership"].pages_collected, 2)
        item = batch.observations[0]
        self.assertEqual(item.subject, canonical_subject())
        self.assertEqual(item.dimensions["board_code"], "BK0420")
        self.assertEqual(item.dimensions["board_type"], "unclassified")
        self.assertEqual(item.dimensions["market_scope"], "mainland_a_share")
        self.assertIn("board_classification_not_exposed", item.limitations)
        self.assertIn("observation_time_not_exposed", item.limitations)
        first_params = parse_qs(urlsplit(transport.get_calls[0]).query)
        second_params = parse_qs(urlsplit(transport.get_calls[1]).query)
        self.assertEqual(first_params["secid"], ["0.300058"])
        self.assertEqual(first_params["pi"], ["0"])
        self.assertEqual(second_params["pi"], ["1"])

    def test_secid_uses_canonical_exchange_not_the_code_prefix(self) -> None:
        transport = FixedTransport(
            [
                response(
                    {
                        "data": {
                            "secid": "1.300058",
                            "total": 0,
                            "diff": [],
                        }
                    }
                )
            ]
        )

        batch = EastmoneySecurityBoardMembershipOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "security_board_membership",
                observed_on="2026-08-02",
                subject=canonical_subject(exchange="SSE", code="300058"),
            )
        )

        self.assertEqual(batch.source_errors, ())
        params = parse_qs(urlsplit(transport.get_calls[0]).query)
        self.assertEqual(params["secid"], ["1.300058"])

    def test_wrong_echoed_security_is_not_accepted(self) -> None:
        batch = EastmoneySecurityBoardMembershipOperation(
            FixedTransport(
                [
                    response(
                        {
                            "data": {
                                "secid": "1.600519",
                                "total": 0,
                                "diff": [],
                            }
                        }
                    )
                ]
            ),
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "security_board_membership",
                observed_on="2026-08-02",
                subject=canonical_subject(),
            )
        )

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.source_errors[0].code, "wrong_security")

    def test_complete_empty_and_missing_schema_are_distinct(self) -> None:
        empty = EastmoneySecurityBoardMembershipOperation(
            FixedTransport(
                [response({"data": {"secid": "0.300058", "total": 0, "diff": []}})]
            ),
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "security_board_membership",
                observed_on="2026-08-02",
                subject=canonical_subject(),
            )
        )
        missing = EastmoneySecurityBoardMembershipOperation(
            FixedTransport([response({"data": {"secid": "0.300058"}})]),
            request_gate=DiagnosticGate(),
        ).collect(
            query(
                "security_board_membership",
                observed_on="2026-08-02",
                subject=canonical_subject(),
            )
        )

        self.assertEqual(
            empty.coverage["security_board_membership"].state,
            "observed_empty",
        )
        self.assertEqual(empty.source_errors, ())
        self.assertEqual(missing.source_errors[0].code, "unknown_schema")


class EastmoneyIndustryRotationTests(unittest.TestCase):
    def _row(
        self,
        code: str,
        name: str,
        change_rate: str,
        observed_at: int,
    ) -> dict[str, object]:
        return {
            "f12": code,
            "f14": name,
            "f3": change_rate,
            "f104": 12,
            "f105": 3,
            "f140": "领涨股",
            "f136": "5.67",
            "f124": observed_at,
        }

    def test_paginates_complete_sorted_snapshot_and_validates_f124(self) -> None:
        provider_time = int(
            datetime(2026, 7, 31, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        transport = FixedTransport(
            [
                response(
                    {
                        "data": {
                            "total": 2,
                            "diff": [
                                self._row("BK1001", "软件开发", "3.50", provider_time)
                            ],
                        }
                    }
                ),
                response(
                    {
                        "data": {
                            "total": 2,
                            "diff": [
                                self._row("BK1002", "煤炭行业", "-1.20", provider_time)
                            ],
                        }
                    }
                ),
            ]
        )

        batch = EastmoneyIndustryRotationOperation(
            transport,
            page_size=1,
            request_gate=DiagnosticGate(),
        ).collect(query("industry_rotation", limit=1))

        self.assertEqual(batch.source_errors, ())
        self.assertEqual(len(batch.observations), 1)
        self.assertEqual(batch.coverage["industry_rotation"].provider_total, 2)
        self.assertEqual(batch.coverage["industry_rotation"].pages_collected, 2)
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2026-07-31")
        self.assertEqual(item.observed_at, "2026-07-31T15:00:00+08:00")
        self.assertEqual(item.dimensions["rank"], 1)
        self.assertEqual(item.metrics["change_rate"], "3.50")
        self.assertEqual(item.rule["code"], "provider_change_rate_desc")
        first_params = parse_qs(urlsplit(transport.get_calls[0]).query)
        second_params = parse_qs(urlsplit(transport.get_calls[1]).query)
        self.assertEqual(first_params["pn"], ["1"])
        self.assertEqual(second_params["pn"], ["2"])
        self.assertEqual(first_params["fid"], ["f3"])
        self.assertEqual(first_params["po"], ["1"])
        self.assertIn("f124", first_params["fields"][0].split(","))

    def test_public_coordinator_accepts_rotation_rule_and_market_scope(self) -> None:
        provider_time = int(
            datetime(2026, 7, 31, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        operation = EastmoneyIndustryRotationOperation(
            FixedTransport(
                [
                    response(
                        {
                            "data": {
                                "total": 1,
                                "diff": [
                                    self._row(
                                        "BK1001", "软件开发", "3.50", provider_time
                                    )
                                ],
                            }
                        }
                    )
                ]
            ),
            request_gate=DiagnosticGate(),
        )

        result = build_market_signals_result(
            market_request("industry_rotation", "2026-07-31"),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertNotIn(
            "unknown_schema", [item["code"] for item in result["source_errors"]]
        )

    def test_wrong_source_date_and_wrong_sort_order_fail_closed(self) -> None:
        wrong_day = int(
            datetime(2026, 8, 1, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        provider_time = int(
            datetime(2026, 7, 31, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        date_mismatch = EastmoneyIndustryRotationOperation(
            FixedTransport(
                [
                    response(
                        {
                            "data": {
                                "total": 1,
                                "diff": [
                                    self._row("BK1001", "软件开发", "3.50", wrong_day)
                                ],
                            }
                        }
                    )
                ]
            ),
            request_gate=DiagnosticGate(),
        ).collect(query("industry_rotation"))
        wrong_sort = EastmoneyIndustryRotationOperation(
            FixedTransport(
                [
                    response(
                        {
                            "data": {
                                "total": 2,
                                "diff": [
                                    self._row(
                                        "BK1001", "软件开发", "-1", provider_time
                                    ),
                                    self._row("BK1002", "煤炭行业", "2", provider_time),
                                ],
                            }
                        }
                    )
                ]
            ),
            page_size=2,
            request_gate=DiagnosticGate(),
        ).collect(query("industry_rotation"))

        self.assertEqual(date_mismatch.source_errors[0].code, "source_date_mismatch")
        self.assertEqual(wrong_sort.source_errors[0].code, "unexpected_sort_order")

    def test_early_empty_page_is_incomplete_not_observed_empty(self) -> None:
        provider_time = int(
            datetime(2026, 7, 31, 15, 0, tzinfo=CHINA_STANDARD_TIME).timestamp()
        )
        batch = EastmoneyIndustryRotationOperation(
            FixedTransport(
                [
                    response(
                        {
                            "data": {
                                "total": 2,
                                "diff": [
                                    self._row(
                                        "BK1001", "软件开发", "3.50", provider_time
                                    )
                                ],
                            }
                        }
                    ),
                    response({"data": {"total": 2, "diff": []}}),
                ]
            ),
            page_size=1,
            request_gate=DiagnosticGate(),
        ).collect(query("industry_rotation"))

        self.assertEqual(batch.observations, ())
        self.assertEqual(batch.coverage["industry_rotation"].state, "indeterminate")
        self.assertEqual(batch.source_errors[0].code, "pagination_incomplete")


class ThsMarketHeatTests(unittest.TestCase):
    def test_concept_tags_are_market_signal_attributions_with_unknown_rank_time(
        self,
    ) -> None:
        transport = FixedTransport(
            [
                response(
                    {
                        "data": {
                            "stock_list": [
                                {
                                    "order": 1,
                                    "code": "300058",
                                    "name": "蓝色光标",
                                    "rate": "98765",
                                    "rise_and_fall": "2.50",
                                    "hot_rank_chg": "3",
                                    "tag": {
                                        "concept_tag": ["AI营销", "算力"],
                                        "popularity_tag": "人气上升",
                                    },
                                }
                            ]
                        }
                    }
                )
            ]
        )

        batch = ThsMarketHeatOperation(
            transport, request_gate=DiagnosticGate()
        ).collect(
            query(
                "market_heat",
                observed_on="2026-08-02",
                parameters={"market_heat_period": "hour"},
            )
        )

        self.assertEqual(batch.source_errors, ())
        item = batch.observations[0]
        self.assertEqual(item.observed_on, "2026-08-02")
        self.assertIsNone(item.observed_at)
        self.assertEqual(item.metrics["rank"], "1")
        self.assertEqual(
            [value.provenance for value in item.attributions],
            ["market_signal", "market_signal"],
        )
        self.assertEqual(
            [value.text for value in item.attributions],
            ["AI营销", "算力"],
        )
        self.assertTrue(
            all(
                value.source_document_id == item.source_document_id
                for value in item.attributions
            )
        )
        self.assertIn("ranking_observation_time_not_exposed", item.limitations)
        self.assertIn("provider_total_not_exposed", item.limitations)
        params = parse_qs(urlsplit(transport.get_calls[0]).query)
        self.assertEqual(params["type"], ["hour"])

    def test_empty_list_is_success_but_missing_list_and_historical_use_fail(
        self,
    ) -> None:
        empty = ThsMarketHeatOperation(
            FixedTransport([response({"data": {"stock_list": []}})]),
            request_gate=DiagnosticGate(),
        ).collect(query("market_heat", observed_on="2026-08-02"))
        missing = ThsMarketHeatOperation(
            FixedTransport([response({"data": {}})]),
            request_gate=DiagnosticGate(),
        ).collect(query("market_heat", observed_on="2026-08-02"))
        historical = ThsMarketHeatOperation(
            FixedTransport([response({"data": {"stock_list": []}})]),
            request_gate=DiagnosticGate(),
        ).collect(query("market_heat", observed_on="2026-07-31"))

        self.assertEqual(empty.coverage["market_heat"].state, "observed_empty")
        self.assertEqual(empty.source_errors, ())
        self.assertEqual(missing.source_errors[0].code, "unknown_schema")
        self.assertEqual(historical.source_errors[0].code, "source_date_mismatch")

    def test_public_coordinator_accepts_market_heat_attribution(self) -> None:
        operation = ThsMarketHeatOperation(
            FixedTransport(
                [
                    response(
                        {
                            "data": {
                                "stock_list": [
                                    {
                                        "order": 1,
                                        "code": "300058",
                                        "name": "蓝色光标",
                                        "rate": "98765",
                                        "rise_and_fall": "2.50",
                                        "hot_rank_chg": "3",
                                        "tag": {
                                            "concept_tag": ["AI营销"],
                                            "popularity_tag": "人气上升",
                                        },
                                    }
                                ]
                            }
                        }
                    )
                ]
            ),
            request_gate=DiagnosticGate(),
        )

        result = build_market_signals_result(
            market_request("market_heat", "2026-08-02"),
            [operation],
            NoIdentityTransport(),
        )

        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(
            result["observations"][0]["attributions"][0]["provenance"],
            "market_signal",
        )
        self.assertNotIn(
            "unknown_schema", [item["code"] for item in result["source_errors"]]
        )


if __name__ == "__main__":
    unittest.main()
