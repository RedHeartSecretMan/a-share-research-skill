from __future__ import annotations

import json
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import asdict
from decimal import InvalidOperation, localcontext
from email.message import Message
from unittest.mock import patch
from urllib.response import addinfourl
import unittest
from dataclasses import replace
from datetime import timedelta, timezone
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path
from tests.test_market_session_context import BINDINGS, TZ, calendars

add_skill_scripts_to_path()

from a_share_research.fixed_index_sources import (  # noqa: E402
    ProductionFixedIndexAdapter,
    build_default_fixed_index_adapter,
    _current_uri,
)
from a_share_research.fixed_index_observations import FixedIndexOperationRequest  # noqa: E402
from a_share_research.identity_sources import HttpResponse, UrlLibTransport  # noqa: E402
from a_share_research.market_session_context import (  # noqa: E402
    DeterministicMarketSessionAdapter,
    MarketSessionOperationRequest,
)

NOW = datetime(2026, 10, 14, 10, tzinfo=TZ)


class IndexTransport:
    """Synthetic values in actual #121 K-proved SSE/SZSE layouts, never a live capture."""

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path == "/v1/sh1/snap/000001":
            material = {
                "code": "000001",
                "date": 20261014,
                "time": 95955,
                "snap": [
                    "上证指数",
                    100.0,
                    100.1,
                    102.0,
                    99.0,
                    101.25,
                    1.25,
                    1.25,
                    1000,
                    100000,
                    "synthetic-flag",
                ],
            }
        elif parsed.path == "/v1/sh1/dayk/000001":
            material = {
                "code": "000001",
                "begin": 0,
                "end": 2,
                "total": 2,
                "kline": [
                    [20261012, 99.0, 101.0, 98.0, 100.0, 500, 50000],
                    [20261013, 100.0, 102.0, 99.0, 101.25, 1000, 100000],
                    [20261014, 101.0, 103.0, 100.0, 102.5, 2000, 200000],
                ],
            }
        elif parsed.path == "/api/market/ssjjhq/getHistoryData":
            code = query["code"][0]
            material = {
                "code": "0",
                "message": "成功",
                "data": {
                    "code": code,
                    "name": "深证成指" if code == "399001" else "创业板指",
                    "picupdata": [
                        [
                            "2026-10-12",
                            "99.00",
                            "100.00",
                            "98.00",
                            "101.00",
                            "1.00",
                            "1.01",
                            500,
                            50000.0,
                        ],
                        [
                            "2026-10-13",
                            "100.00",
                            "101.25",
                            "99.00",
                            "102.00",
                            "1.25",
                            "1.25",
                            1000,
                            100000.0,
                        ],
                        [
                            "2026-10-14",
                            "101.00",
                            "102.50",
                            "100.00",
                            "103.00",
                            "1.25",
                            "1.23",
                            2000,
                            200000.0,
                        ],
                    ],
                    "picdowndata": [
                        ["2026-10-12", 500],
                        ["2026-10-13", 1000],
                        ["2026-10-14", 2000],
                    ],
                },
            }
        elif parsed.path == "/api/market/ssjjhq/getTimeData":
            code = query["code"][0]
            material = {
                "code": "0",
                "message": "成功",
                "datetime": "2026-10-14 10:00:00",
                "data": {
                    "code": code,
                    "name": "深证成指" if code == "399001" else "创业板指",
                    "close": "100.00",
                    "now": "101.25",
                    "open": "100.10",
                    "high": "102.00",
                    "low": "99.00",
                    "delta": "1.25",
                    "deltaPercent": "1.25",
                    "marketTime": "2026-10-14 09:59:55",
                    "volume": 1000,
                    "amount": 100000.0,
                    "picupdata": [["09:59", "101.25", "1.25", "1.25", 1000, 100000.0]],
                    "picdowndata": [["09:59", 1000]],
                    "releInfo": {
                        "cfzqlb": "",
                        "cfzqsclb": "",
                        "zsdm": code,
                        "zslx": "",
                    },
                },
            }
        elif parsed.path == "/neeqRealController/getRealBZ50.do":
            material = [
                {
                    "JSRQ": "2026-10-14",
                    "GXSJ": "093058",
                    "SSZS": 100.1,
                    "ZRSP": 100.0,
                    "ZD": 0.1,
                    "ZDF": 0.001,
                    "CJL": 100,
                    "CJJE": 10000,
                },
                {
                    "JSRQ": "2026-10-14",
                    "GXSJ": "095955",
                    "SSZS": 101.25,
                    "ZRSP": 100.0,
                    "ZD": 1.25,
                    "ZDF": 0.0125,
                    "CJL": 1000,
                    "CJJE": 100000,
                },
            ]
            return HttpResponse(
                200,
                "text/html",
                ("callback(" + json.dumps(material) + ");").encode(),
                NOW,
            )
        elif parsed.path == "/neeqRealController/getBz50Kline.do":
            self.assert_history_query(parsed.query)
            material = [
                {
                    "zsdm": "899050",
                    "jc": "北证50",
                    "jsrq": "20261013",
                    "jzrq": "20220429",
                    "drkp": 100.1,
                    "drsp": 101.25,
                    "drzd": 102.0,
                    "drzx": 99.0,
                    "zrsp": 100.0,
                    "zd": 1.25,
                    "zdf": 0.0125,
                    "cjl": 1000,
                    "cjje": 100000,
                },
                {
                    "zsdm": "899050",
                    "jc": "北证50",
                    "jsrq": "20261014",
                    "jzrq": "20220429",
                    "drkp": 101.0,
                    "drsp": 102.5,
                    "drzd": 103.0,
                    "drzx": 100.0,
                    "zrsp": 101.25,
                    "zd": 1.25,
                    "zdf": 0.01234567,
                    "cjl": 2000,
                    "cjje": 200000,
                },
            ]
            return HttpResponse(
                200,
                "text/html",
                ("callback(" + json.dumps(material) + ");").encode(),
                NOW,
            )
        else:
            raise AssertionError("Unexpected source route: " + parsed.path)
        return HttpResponse(
            200,
            "application/json",
            json.dumps(material, ensure_ascii=False).encode(),
            NOW,
        )

    @staticmethod
    def assert_history_query(query: str) -> None:
        if query != "%7B%22type%22%3A%22dayKline%22%7D&type=dayKline":
            raise AssertionError(
                "BSE history query differs from verified actual target."
            )


class FixedIndexSourceTests(unittest.TestCase):
    def test_four_layouts_are_adopted_under_bounded_research_scope_without_general_rights_claim(
        self,
    ) -> None:
        adapter = ProductionFixedIndexAdapter(
            IndexTransport(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                purpose="current_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "complete")
        self.assertEqual(len(outcome.observations), 4)
        self.assertEqual(outcome.diagnostics["index_requests"], 4)
        facts = outcome.to_dict()["diagnostics"]["parsed_sources"]
        self.assertEqual(
            [item["canonical_id"] for item in facts],
            ["SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050"],
        )
        self.assertEqual(
            facts[0]["source_clock_semantics"], "source_reported_quote_update"
        )
        self.assertEqual(facts[0]["reported_source_date"], "20261014")
        self.assertEqual(
            [item["code"] for item in outcome.source_errors],
            [],
        )

    def test_completed_date_only_wire_preserves_precision_and_does_not_fabricate_close_clock(
        self,
    ) -> None:
        adapter = ProductionFixedIndexAdapter(
            IndexTransport(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-13", BINDINGS),
                purpose="completed_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "complete")
        self.assertEqual(outcome.diagnostics["index_requests"], 4)
        facts = outcome.to_dict()["diagnostics"]["parsed_sources"]
        self.assertEqual(
            [item["last_source_date"] for item in facts], ["2026-10-14"] * 4
        )
        self.assertEqual([item["source_date_precision"] for item in facts], ["day"] * 4)
        self.assertTrue(all("observed_at" not in item for item in facts))
        self.assertTrue(
            all(
                item["source_clock_semantics"] == "source_reported_daily_session"
                for item in facts
            )
        )

    def test_bad_response_metadata_and_clock_are_bounded_failures(self) -> None:
        for variant in (
            "mime",
            "naive_clock",
            "future_clock",
            "extreme_clock",
            "clock_token",
        ):

            class Bad(IndexTransport):
                def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                    response = super().get(url, headers)
                    if urlsplit(url).path != "/v1/sh1/snap/000001":
                        return response
                    if variant == "mime":
                        return replace(response, content_type=None)  # type: ignore[arg-type]
                    if variant == "naive_clock":
                        return replace(response, retrieved_at=NOW.replace(tzinfo=None))
                    if variant == "extreme_clock":
                        return replace(
                            response,
                            retrieved_at=datetime(
                                9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc
                            ),
                        )
                    if variant == "clock_token":
                        material = json.loads(response.body)
                        material["date"] = int("7" * 3000)
                        return replace(response, body=json.dumps(material).encode())
                    return replace(response, retrieved_at=NOW + timedelta(seconds=1))

            with self.subTest(variant=variant):
                adapter = ProductionFixedIndexAdapter(
                    Bad(),
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: NOW
                    ),
                    clock=lambda: NOW,
                )
                outcome = adapter.execute(
                    FixedIndexOperationRequest(
                        MarketSessionOperationRequest("2026-10-14", BINDINGS),
                        purpose="current_session",
                    ),
                    5,
                )
                self.assertEqual(outcome.coverage, "partial")
                self.assertIn(
                    next(
                        error["code"]
                        for error in outcome.source_errors
                        if error["code"] != "index_identity_missing"
                    ),
                    (
                        "index_source_response_invalid",
                        "index_timestamp_unproved",
                        "index_source_shape_invalid",
                    ),
                )
                self.assertEqual(
                    [
                        item["canonical_id"]
                        for item in outcome.to_dict()["diagnostics"]["parsed_sources"]
                    ],
                    ["SZSE:399001", "SZSE:399006", "BSE:899050"],
                )

    def test_unbounded_default_http_transport_cannot_follow_redirects_outside_source_scope(
        self,
    ) -> None:
        urls: list[str] = []

        def wire(
            _handler: object, _http_class: object, request: object, **kwargs: object
        ) -> object:
            url = request.full_url
            urls.append(url)
            headers = Message()
            headers["Content-Type"] = "application/json"
            if "outside.invalid" not in url:
                headers["Location"] = "https://outside.invalid/index"
                result = addinfourl(io.BytesIO(b""), headers, url, 302)
                result.msg = "Found"
            else:
                result = addinfourl(io.BytesIO(b"{}"), headers, url, 200)
                result.msg = "OK"
            return result

        with patch("urllib.request.AbstractHTTPHandler.do_open", new=wire):
            adapter = ProductionFixedIndexAdapter(
                UrlLibTransport(),
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: NOW
                ),
                clock=lambda: NOW,
            )
            outcome = adapter.execute(
                FixedIndexOperationRequest(
                    MarketSessionOperationRequest("2026-10-14", BINDINGS),
                    purpose="current_session",
                ),
                5,
            )
        self.assertEqual(outcome.coverage, "failed")
        self.assertLessEqual(len(urls), 3)
        self.assertFalse(any("outside.invalid" in url for url in urls))

    def test_owned_default_transport_bounds_the_actual_response_read(self) -> None:
        reads: list[int] = []

        class Body(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                reads.append(size)
                return super().read(size)

        def wire(
            _handler: object, _http_class: object, request: object, **kwargs: object
        ) -> object:
            headers = Message()
            headers["Content-Type"] = "application/json"
            result = addinfourl(
                Body(b"x" * (1024 * 1024)), headers, request.full_url, 200
            )
            result.msg = "OK"
            return result

        with patch("urllib.request.AbstractHTTPHandler.do_open", new=wire):
            adapter = ProductionFixedIndexAdapter(
                None,
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: NOW
                ),
                clock=lambda: NOW,
            )
            outcome = adapter.execute(
                FixedIndexOperationRequest(
                    MarketSessionOperationRequest("2026-10-14", BINDINGS),
                    purpose="current_session",
                ),
                5,
            )
        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(reads, [262145, 262145, 262145, 262145])

    def test_bse_actual_minute_and_daily_bindings_preserve_each_dialect(self) -> None:
        adapter = ProductionFixedIndexAdapter(
            IndexTransport(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        for purpose in ("current_session", "completed_session"):
            with self.subTest(purpose=purpose):
                outcome = adapter.execute(
                    FixedIndexOperationRequest(
                        MarketSessionOperationRequest("2026-10-14", BINDINGS),
                        purpose=purpose,
                    ),
                    5,
                )
                self.assertEqual(outcome.diagnostics["index_requests"], 4)
                bse = outcome.to_dict()["diagnostics"]["parsed_sources"][-1]
                self.assertEqual(bse["canonical_id"], "BSE:899050")
                if purpose == "current_session":
                    self.assertEqual(bse["reported_market_time"], "2026-10-14 095955")
                else:
                    self.assertEqual(bse["last_source_date"], "2026-10-14")
                    self.assertEqual(bse["source_rows"], 2)

    def test_four_real_layouts_normalize_reported_clock_points_and_bse_ratio_without_false_qualification(
        self,
    ) -> None:
        adapter = ProductionFixedIndexAdapter(
            IndexTransport(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                purpose="current_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "complete")
        values = [
            row["payload"]["observation"] for row in outcome.to_dict()["observations"]
        ]
        self.assertEqual(
            [row["canonical_id"] for row in values],
            ["SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050"],
        )
        self.assertEqual([row["value"] for row in values], ["101.25"] * 4)
        self.assertEqual([row["change_percent"] for row in values], ["1.25"] * 4)
        self.assertEqual(values[-1]["source_field_values"]["ZDF"], "0.0125")
        self.assertEqual(values[-1]["baseline_provenance"]["row"], 0)
        self.assertEqual(values[0]["observed_at"], "2026-10-14T09:59:55+08:00")
        self.assertTrue(
            all(
                row["source_time_semantics"] == "source_reported_quote_update"
                for row in values
            )
        )
        self.assertEqual(outcome.source_errors, ())
        self.assertEqual(
            outcome.diagnostics["general_api_cache_distribution_rights"], "unproved"
        )
        self.assertEqual(
            outcome.diagnostics["source_use_scope"], "personal_noncommercial_research"
        )

    def test_four_daily_owners_select_frozen_context_date_and_keep_day_precision(
        self,
    ) -> None:
        adapter = ProductionFixedIndexAdapter(
            IndexTransport(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-13", BINDINGS),
                purpose="completed_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        rows = [
            item["payload"]["observation"] for item in outcome.to_dict()["observations"]
        ]
        self.assertEqual(
            [row["canonical_id"] for row in rows],
            ["SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050"],
        )
        self.assertEqual([row["session_date"] for row in rows], ["2026-10-13"] * 4)
        self.assertTrue(
            all(
                row["source_time_precision"] == "day" and row["observed_at"] is None
                for row in rows
            )
        )
        self.assertEqual(rows[-1]["source_field_values"]["zdf"], "0.0125")
        self.assertEqual(rows[-1]["change_percent"], "1.25")

    def test_latest_completed_use_consumes_today_after_regular_auction_without_screen_proxy(
        self,
    ) -> None:
        checked = datetime(2026, 10, 14, 15, 10, tzinfo=TZ)
        paths = []

        class Closing(IndexTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                paths.append(urlsplit(url).path)
                response = super().get(url, headers)
                bse = urlsplit(url).path == "/neeqRealController/getRealBZ50.do"
                material = json.loads(
                    response.body.decode()[9:-2]
                    if response.body.startswith(b"callback(")
                    else response.body
                )
                if urlsplit(url).path == "/v1/sh1/snap/000001":
                    material["time"] = 150500
                elif urlsplit(url).path == "/api/market/ssjjhq/getTimeData":
                    material["data"]["marketTime"] = "2026-10-14 15:05:00"
                elif bse:
                    material[-1]["GXSJ"] = "150500"
                body = json.dumps(material).encode()
                if bse:
                    body = b"callback(" + body + b");"
                return replace(response, body=body, retrieved_at=checked)

        adapter = ProductionFixedIndexAdapter(
            Closing(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: checked
            ),
            clock=lambda: checked,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                completed_basis="latest_index_session",
            ),
            5,
        )
        values = [
            row["payload"]["observation"] for row in outcome.to_dict()["observations"]
        ]
        self.assertEqual(
            [row["session_date"] for row in values],
            ["2026-10-14"] * 4,
            outcome.source_errors,
        )
        self.assertEqual(
            [row["price_semantics"] for row in values],
            ["completed_session_reported_value"] * 4,
        )
        self.assertEqual(
            paths,
            [
                "/v1/sh1/snap/000001",
                "/api/market/ssjjhq/getTimeData",
                "/api/market/ssjjhq/getTimeData",
                "/neeqRealController/getRealBZ50.do",
            ],
        )

    def test_szse_201_rows_keep_reference_seed_outside_displayed_history(self) -> None:
        class Seeded(IndexTransport):
            def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                response = super().get(url, headers)
                if urlsplit(url).path != "/api/market/ssjjhq/getHistoryData":
                    return response
                material = json.loads(response.body)
                first = datetime(2026, 10, 13) - timedelta(days=200)
                records = []
                for index in range(201):
                    day = (first + timedelta(days=index)).date().isoformat()
                    records.append(
                        [
                            day,
                            "100.00",
                            "101.25" if index == 200 else "100.00",
                            "99.00",
                            "102.00",
                            "1.25" if index == 200 else "0.00",
                            "1.25" if index == 200 else "0.00",
                            1000,
                            100000,
                        ]
                    )
                material["data"]["picupdata"] = records
                material["data"]["picdowndata"] = [[row[0], 1000] for row in records]
                return replace(response, body=json.dumps(material).encode())

        adapter = ProductionFixedIndexAdapter(
            Seeded(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-13", BINDINGS)
            ),
            5,
        )
        facts = outcome.to_dict()["diagnostics"]["parsed_sources"][1]
        self.assertEqual(facts["source_rows"], 200)
        self.assertEqual(facts["reference_seed_rows"], 1)
        self.assertEqual(
            facts["first_source_date"],
            (datetime(2026, 10, 13) - timedelta(days=199)).date().isoformat(),
        )
        value = outcome.to_dict()["observations"][1]["payload"]["observation"]
        self.assertEqual(value["previous_close"], "100.00")
        self.assertEqual(value["baseline_provenance"]["row"], 199)

    def test_default_factory_is_cold_and_copied_install_preserves_public_references(
        self,
    ) -> None:
        with patch(
            "urllib.request.AbstractHTTPHandler.do_open",
            side_effect=AssertionError("Unexpected live call"),
        ):
            native = build_default_fixed_index_adapter(clock=lambda: NOW)
            self.assertFalse(native.obligation.experimental)
        identities = ("SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050")
        material = {
            "calendars": [asdict(item) for item in calendars()],
            "bindings": asdict(BINDINGS),
            "wire": {},
        }
        for identity in identities:
            uri = _current_uri(identity)
            response = IndexTransport().get(uri, {})
            material["wire"][uri] = {
                "content_type": response.content_type,
                "body": response.body.decode(),
            }
        program = r"""
import sys,json
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from a_share_research.fixed_index_sources import build_default_fixed_index_adapter
from a_share_research.fixed_index_observations import FixedIndexOperationRequest
from a_share_research.identity_sources import HttpResponse
from a_share_research.market_session_context import CalendarDay,CalendarEvidence,ContextBindings,DeterministicMarketSessionAdapter,MarketSessionOperationRequest
from a_share_research.research_result import DirectTaskPlan,DirectTaskClaim,ResearchDependency,fold_direct_task_research_result
material=json.load(sys.stdin)
for row in material['calendars']:
    row['days']=tuple(CalendarDay(**day) for day in row['days'])
    row['auction_times']=tuple(row['auction_times'])
now=datetime.fromisoformat('2026-10-14T10:00:00+08:00')
class SyntheticTransport:
    def __init__(self): self.calls=0
    def get(self,url,headers):
        self.calls+=1
        row=material['wire'][url]
        return HttpResponse(200,row['content_type'],row['body'].encode(),now)
transport=SyntheticTransport()
sessions=DeterministicMarketSessionAdapter(tuple(CalendarEvidence(**row) for row in material['calendars']),clock=lambda:now)
adapter=build_default_fixed_index_adapter(transport,session_adapter=sessions,clock=lambda:now)
assert transport.calls==0
outcome=adapter.execute(FixedIndexOperationRequest(MarketSessionOperationRequest('2026-10-14',ContextBindings(**material['bindings'])),purpose='current_session'),5)
local_ids=tuple(row['id'] for row in outcome.to_dict()['observations'])
plan=DirectTaskPlan(task_type='fixed_index_installed_contract',as_of='2026-10-14',obligations=(adapter.obligation,),dependencies=(ResearchDependency('major_broad_indices','major_broad_indices',4,True),))
result=fold_direct_task_research_result(plan,(outcome,),claims=(DirectTaskClaim('fixed_index_summary','major_broad_indices',local_ids,{'index_evidence_ids':list(local_ids)}),),summary='Synthetic installed wire contract.').to_dict()
assert result['claims'][0]['payload']['index_evidence_ids']==[row['id'] for row in result['observations']]
assert all(not hasattr(row,'_values') for row in result['observations'])
print(json.dumps({'coverage':outcome.coverage,'calls':transport.calls,'count':len(local_ids),'error_codes':[row['code'] for row in outcome.source_errors]}))
"""
        source = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        with tempfile.TemporaryDirectory(prefix="asr-118-source-install-") as temp:
            installed = Path(temp) / "a-share-research"
            shutil.copytree(
                source,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", program, str(installed / "scripts")],
                cwd=temp,
                input=json.dumps(material),
                capture_output=True,
                text=True,
                timeout=15,
                close_fds=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["calls"], 4)
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["error_codes"], [])

    def test_owned_transport_refuses_redirect_without_an_extra_wire_attempt(
        self,
    ) -> None:
        urls = []

        def wire(_handler, _http_class, request, **kwargs):
            urls.append(request.full_url)
            headers = Message()
            headers["Content-Type"] = "application/json"
            headers["Location"] = "https://outside.invalid/index"
            response = addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response

        with patch("urllib.request.AbstractHTTPHandler.do_open", new=wire):
            outcome = build_default_fixed_index_adapter(
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: NOW
                ),
                clock=lambda: NOW,
            ).execute(
                FixedIndexOperationRequest(
                    MarketSessionOperationRequest("2026-10-14", BINDINGS),
                    purpose="current_session",
                ),
                5,
            )
        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(len(urls), 4)
        self.assertFalse(any("outside.invalid" in url for url in urls))

    def test_historical_current_request_is_rejected_before_index_acquisition(
        self,
    ) -> None:
        class Unavailable:
            def get(self, url, headers):
                raise AssertionError(
                    "No index source is authorized for historical current use"
                )

        outcome = build_default_fixed_index_adapter(
            Unavailable(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-13", BINDINGS),
                purpose="current_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(outcome.diagnostics["index_requests"], 0)
        self.assertEqual(
            outcome.source_errors[0]["code"], "index_current_session_unproved"
        )

    def test_date_only_or_truncated_current_clock_cannot_create_midnight_during_hold(
        self,
    ) -> None:
        checked = datetime(2026, 10, 14, 12, tzinfo=TZ)
        for variant, expected in (
            ("szse_day_only", ["SSE:000001", "BSE:899050"]),
            ("sse_short_clock", ["SZSE:399001", "SZSE:399006", "BSE:899050"]),
        ):

            class IncompleteClock(IndexTransport):
                def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
                    response = super().get(url, headers)
                    path = urlsplit(url).path
                    if (
                        variant == "szse_day_only"
                        and path == "/api/market/ssjjhq/getTimeData"
                    ):
                        material = json.loads(response.body)
                        material["data"]["marketTime"] = "2026-10-14"
                        response = replace(response, body=json.dumps(material).encode())
                    elif variant == "sse_short_clock" and path == "/v1/sh1/snap/000001":
                        material = json.loads(response.body)
                        material["time"] = 959
                        response = replace(response, body=json.dumps(material).encode())
                    return replace(response, retrieved_at=checked)

            with self.subTest(variant=variant):
                outcome = ProductionFixedIndexAdapter(
                    IncompleteClock(),
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: checked
                    ),
                    clock=lambda: checked,
                ).execute(
                    FixedIndexOperationRequest(
                        MarketSessionOperationRequest("2026-10-14", BINDINGS),
                        purpose="current_session",
                    ),
                    5,
                )
                self.assertEqual(outcome.coverage, "partial")
                self.assertEqual(
                    [
                        row["payload"]["observation"]["canonical_id"]
                        for row in outcome.to_dict()["observations"]
                    ],
                    expected,
                )
                self.assertTrue(
                    all(
                        error["code"]
                        in (
                            "index_identity_missing",
                            "index_timestamp_unproved",
                            "index_source_shape_invalid",
                        )
                        for error in outcome.source_errors
                    )
                )

    def test_midday_native_complete_clock_cannot_escape_morning_quote_qualification(
        self,
    ) -> None:
        checked = datetime(2026, 10, 14, 12, tzinfo=TZ)

        class Midnight(IndexTransport):
            def get(self, url, headers):
                response = super().get(url, headers)
                if urlsplit(url).path == "/api/market/ssjjhq/getTimeData":
                    material = json.loads(response.body)
                    material["data"]["marketTime"] = "2026-10-14 00:00:00"
                    response = replace(response, body=json.dumps(material).encode())
                return replace(response, retrieved_at=checked)

        outcome = ProductionFixedIndexAdapter(
            Midnight(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: checked
            ),
            clock=lambda: checked,
        ).execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                purpose="current_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(
            [
                row["payload"]["observation"]["canonical_id"]
                for row in outcome.to_dict()["observations"]
            ],
            ["SSE:000001", "BSE:899050"],
        )
        self.assertTrue(
            all(
                error["code"] == "index_morning_observation_unproved"
                for error in outcome.source_errors
            )
        )

    def test_midday_latest_quote_role_retains_own_field_and_series_tail_provenance(
        self,
    ) -> None:
        checked = datetime(2026, 10, 14, 12, tzinfo=TZ)

        class Holding(IndexTransport):
            def get(self, url, headers):
                return replace(super().get(url, headers), retrieved_at=checked)

        outcome = ProductionFixedIndexAdapter(
            Holding(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: checked
            ),
            clock=lambda: checked,
        ).execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                purpose="current_session",
            ),
            5,
        )
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        values = [
            row["payload"]["observation"] for row in outcome.to_dict()["observations"]
        ]
        self.assertEqual(
            [row["quote_provenance"]["field"] for row in values],
            ["last", "now", "now", "SSZS"],
        )
        self.assertEqual(values[0]["quote_provenance"]["array_index"], 5)
        self.assertEqual(values[-1]["quote_provenance"]["row"], 1)
        self.assertEqual(values[-1]["quote_provenance"]["selection"], "series_last_row")
        self.assertTrue(
            all(row["observation_boundary"] == "latest_source_quote" for row in values)
        )
        self.assertEqual(
            [row["observed_at"] for row in values], ["2026-10-14T09:59:55+08:00"] * 4
        )

    def test_unrepresentable_json_decimal_keeps_other_indices_with_bounded_error(
        self,
    ) -> None:
        identities = ("SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050")
        for target in ("SSE:000001", "SZSE:399001", "BSE:899050"):
            for literal in (
                b"1e999999999999999999999999",
                b"1e-999999999999999999999999",
                b"1." + b"2" * 200,
                b"9" * 5000,
            ):
                for trapped in (True, False):

                    class HugeDecimal(IndexTransport):
                        def get(
                            self, url: str, headers: dict[str, str]
                        ) -> HttpResponse:
                            response = super().get(url, headers)
                            parsed = urlsplit(url)
                            if (
                                target == "SSE:000001"
                                and parsed.path == "/v1/sh1/snap/000001"
                            ):
                                response = replace(
                                    response,
                                    body=response.body.replace(b"101.25", literal),
                                )
                            elif (
                                target == "SZSE:399001"
                                and parsed.path == "/api/market/ssjjhq/getTimeData"
                                and parse_qs(parsed.query)["code"] == ["399001"]
                            ):
                                response = replace(
                                    response,
                                    body=response.body.replace(
                                        b'"amount": 100000.0', b'"amount": ' + literal
                                    ),
                                )
                            elif (
                                target == "BSE:899050"
                                and parsed.path == "/neeqRealController/getRealBZ50.do"
                            ):
                                response = replace(
                                    response,
                                    body=response.body.replace(
                                        b'"SSZS": 101.25', b'"SSZS": ' + literal
                                    ),
                                )
                            return response

                    with (
                        self.subTest(
                            target=target, literal=literal[:30], trapped=trapped
                        ),
                        localcontext() as numeric_context,
                    ):
                        numeric_context.traps[InvalidOperation] = trapped
                        outcome = ProductionFixedIndexAdapter(
                            HugeDecimal(),
                            session_adapter=DeterministicMarketSessionAdapter(
                                calendars(), clock=lambda: NOW
                            ),
                            clock=lambda: NOW,
                        ).execute(
                            FixedIndexOperationRequest(
                                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                                purpose="current_session",
                            ),
                            5,
                        )
                        self.assertEqual(outcome.coverage, "partial")
                        self.assertEqual(
                            [
                                row["payload"]["observation"]["canonical_id"]
                                for row in outcome.to_dict()["observations"]
                            ],
                            [identity for identity in identities if identity != target],
                        )
                        self.assertEqual(outcome.diagnostics["index_requests"], 4)
                        projected = json.dumps(outcome.to_dict())
                        self.assertNotIn(literal.decode(), projected)
                        self.assertNotIn("InvalidOperation", projected)
                        self.assertTrue(
                            all(
                                error["code"]
                                in (
                                    "index_identity_missing",
                                    "index_source_payload_invalid",
                                    "index_source_value_invalid",
                                )
                                for error in outcome.source_errors
                            )
                        )


if __name__ == "__main__":
    unittest.main()
