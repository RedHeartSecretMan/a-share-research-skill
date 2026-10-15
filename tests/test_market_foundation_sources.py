from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_foundation import (  # noqa: E402
    OperationRequest,
    build_default_market_foundation_operations,
    fold_market_foundation,
)
import a_share_research.market_foundation_sources as sources  # noqa: E402

CHINA = timezone(timedelta(hours=8))
REQUEST = OperationRequest("2026-10-14", "2026-10-15T09:00:00+08:00")


class FixtureHttp:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.calls.append(("GET", url, headers, None))
        return self.response()

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        self.calls.append(("POST", url, headers, body))
        return self.response()

    def response(self) -> HttpResponse:
        return HttpResponse(
            200,
            "application/javascript",
            self.body,
            datetime(2026, 10, 15, 9, tzinfo=CHINA),
        )


def frame(payload: object, callback: str = "null") -> bytes:
    # Synthetic values; only retained H/I/L/N source field/frame shapes are used.
    return (callback + "(" + json.dumps(payload) + ");").encode()


def run_owner(owner: str, transport: object, request: OperationRequest = REQUEST):
    operations = build_default_market_foundation_operations(
        transports={owner: transport}
    )
    operation = next(item for item in operations if item.operation_id == owner)
    outcome = operation.execute(request, 1)
    return outcome.to_dict(), fold_market_foundation(
        (outcome,), request, operations=operations
    )


def szse_report(
    catalog: str,
    name: str,
    rows: list[object],
    date: str = "2026-10-14",
    subname: str = "",
) -> bytes:
    cols = (
        {
            "jrrjmc": "融券卖出量<br>(亿股/亿份)",
            "jrrjye": "融券余额<br>(亿元)",
            "jrrjyl": "融券余量<br>(亿股/亿份)",
            "jrrzmr": "融资买入额<br>(亿元)",
            "jrrzrjye": "融资融券余额<br>(亿元)",
            "jrrzye": "融资余额<br>(亿元)",
        }
        if catalog == "1837_xxpl"
        else {"zbmc": "指标名称", "gp": "深市合计", "xz": "主板", "cy": "创业板"}
    )
    metadata = {
        "catalogid": catalog,
        "name": name,
        "tabkey": "tab1",
        "pagecount": 0,
        "pageno": 0,
        "pagesize": -1,
        "recordcount": 0,
        "subname": subname,
        "cols": cols,
        "conditions": [
            {
                "name": "txtDate" if catalog == "1837_xxpl" else "txtQueryDate",
                "defaultValue": date,
            }
        ],
        "footer": "数据统计范围包含存托凭证。" if catalog != "1837_xxpl" else "",
    }
    return json.dumps([{"metadata": metadata, "data": rows, "error": None}]).encode()


class MarketFoundationSourceTests(unittest.TestCase):
    def test_default_post_redirect_cannot_hide_an_extra_leaf(self):
        calls = []

        class Opener:
            def __init__(self, handler):
                self.handler = handler
                self.handler.parent = self

            def open(self, request: Request, *, timeout: float):
                calls.append((request.get_method(), timeout))
                headers = Message()
                headers["Location"] = "/another"
                request.timeout = timeout
                return self.handler.http_error_303(
                    request, BytesIO(), 303, "See Other", headers
                )

        with patch(
            "a_share_research.identity_sources.build_opener",
            side_effect=lambda handler: Opener(handler),
        ):
            operations = build_default_market_foundation_operations()
            outcome = operations[2].execute(REQUEST, 0.5)
        result = fold_market_foundation((outcome,), REQUEST, operations=operations)
        self.assertEqual(calls, [("POST", 0.5)])
        self.assertEqual(result["operations"][2]["attempts"], 1)
        self.assertEqual(result["operations"][2]["source_result"], "unavailable")

    def test_owner_parsers_reject_date_duplicates_and_executable_frames(self):
        cases = [
            (
                "bse_a_share_turnover@1",
                sources.BseMarketTurnoverReportTransport,
                frame([{"xxzrlx": "2", "rq": "20261013", "hqcjje": 1}]),
                "source_report_date_mismatch",
            ),
            (
                "bse_a_share_turnover@1",
                sources.BseMarketTurnoverReportTransport,
                frame([{"xxzrlx": "2", "rq": "20261014", "hqcjje": 1}] * 2),
                "source_schema_unproved",
            ),
            (
                "sse_a_share_turnover@1",
                sources.SseMarketTurnoverReportTransport,
                frame(
                    {
                        "result": [
                            {
                                "PRODUCT_CODE": "01",
                                "TRADE_DATE": "20261014",
                                "TRADE_AMT": "1",
                            }
                        ]
                        * 2
                    },
                    "jsonpCallback121",
                ),
                "source_schema_unproved",
            ),
            (
                "sse_a_share_turnover@1",
                sources.SseMarketTurnoverReportTransport,
                b"jsonpCallback121({\"result\":[]});alert('/Users/private secret')",
                "source_schema_unproved",
            ),
            (
                "sse_market_margin@1",
                sources.SseMarketMarginReportTransport,
                frame(
                    {
                        "result": [],
                        "pageHelp": {
                            "pageCount": 2,
                            "total": 26,
                            "pageNo": 1,
                            "pageSize": 25,
                        },
                    },
                    "jsonpCallback121",
                ),
                "source_schema_unproved",
            ),
            (
                "szse_market_margin@1",
                sources.SzseMarketMarginReportTransport,
                szse_report("1837_xxpl", "融资融券交易明细", []),
                "source_schema_unproved",
            ),
            (
                "szse_market_margin@1",
                sources.SzseMarketMarginReportTransport,
                szse_report("1837_xxpl", "融资融券交易总量", [], "2026-10-13"),
                "source_report_date_mismatch",
            ),
            (
                "szse_market_margin@1",
                sources.SzseMarketMarginReportTransport,
                szse_report("1837_xxpl", "融资融券交易总量", [{"jrrzye": "1"}]),
                "source_schema_unproved",
            ),
            (
                "szse_a_share_turnover@1",
                sources.SzseMarketTurnoverReportTransport,
                szse_report(
                    "scsj_gprdgk_after",
                    "日度概况",
                    [
                        {
                            "zbmc": "成交金额（亿元）",
                            "gp": "7,52.37",
                            "xz": "1",
                            "cy": "1",
                        }
                    ],
                ),
                "source_schema_unproved",
            ),
        ]
        for owner, factory, body, code in cases:
            with self.subTest(owner=owner, code=code, size=len(body)):
                http = FixtureHttp(body)
                outcome, result = run_owner(owner, factory(http))
                operation = next(
                    item
                    for item in result["operations"]
                    if item["operation_id"] == owner
                )
                self.assertEqual(outcome["observations"], [])
                self.assertEqual(outcome["source_errors"][0]["code"], code)
                self.assertEqual(operation["attempts"], 1)
                self.assertNotIn("private", json.dumps(outcome))
                self.assertNotIn("secret", json.dumps(result))

    def test_copied_installed_default_six_owners_attempt_actual_parsers(self):
        from pathlib import Path
        import shutil
        import subprocess
        import sys
        import tempfile
        from tests.test_market_foundation_bse_margin import synthetic_summary

        bodies = {
            "sse_a_share_turnover@1": frame(
                {
                    "result": [
                        {
                            "PRODUCT_CODE": "01",
                            "TRADE_DATE": "20261014",
                            "TRADE_AMT": "30",
                        },
                        {
                            "PRODUCT_CODE": "03",
                            "TRADE_DATE": "20261014",
                            "TRADE_AMT": "5",
                        },
                    ]
                },
                "jsonpCallback121",
            ),
            "sse_market_margin@1": frame(
                {
                    "result": [
                        {
                            "opDate": "20261014",
                            "rzye": 1205000,
                            "rzmre": 102500,
                            "rqylje": 307500,
                            "rzrqjyzl": 1512500,
                            "rqyl": 80000,
                            "rqmcl": 20000,
                        }
                    ],
                    "pageHelp": {
                        "pageCount": 1,
                        "total": 1,
                        "pageNo": 1,
                        "pageSize": 25,
                    },
                },
                "jsonpCallback121",
            ),
            "bse_a_share_turnover@1": frame(
                [{"xxzrlx": "2", "rq": "20261014", "hqcjje": 125000}]
            ),
            "bse_market_margin@1": synthetic_summary("2026-10-14"),
            "szse_a_share_turnover@1": szse_report(
                "scsj_gprdgk_after",
                "日度概况",
                [
                    {
                        "zbmc": "成交金额（亿元）",
                        "gp": "4,567.89",
                        "xz": "1,234.56",
                        "cy": "3,333.33",
                    }
                ],
            ),
            "szse_market_margin@1": szse_report(
                "1837_xxpl",
                "融资融券交易总量",
                [
                    {
                        "jrrzye": "120.50",
                        "jrrzmr": "10.25",
                        "jrrjye": "30.75",
                        "jrrzrjye": "151.25",
                        "jrrjmc": "2.00",
                        "jrrjyl": "8.00",
                    }
                ],
                "2026-10-14",
                "2026-10-14",
            ),
        }
        script = """
import json,sys
from io import BytesIO
from pathlib import Path
from email.message import Message
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch
from a_share_research.market_foundation import OperationRequest,build_default_market_foundation_operations,fold_market_foundation
import a_share_research.market_foundation_sources as sources
assert Path(sources.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
bodies=json.load(sys.stdin)
calls=[]
class Response(BytesIO):
    status=200
    headers=Message()
class Opener:
    def open(self,request,*,timeout):
        path=urlsplit(request.full_url).path
        owner={"/commonQuery.do":"sse_a_share_turnover@1","/commonSoaQuery.do":"sse_market_margin@1","/marketStatController/dailyReport.do":"bse_a_share_turnover@1","/rzrqjyyexxController/summaryInfoResult.do":"bse_market_margin@1"}.get(path)
        if owner is None:
            catalog=parse_qs(urlsplit(request.full_url).query)["CATALOGID"][0]
            owner="szse_market_margin@1" if catalog=="1837_xxpl" else "szse_a_share_turnover@1"
        calls.append({"owner":owner,"method":request.get_method(),"timeout":timeout})
        return Response(bodies[owner].encode())
request=OperationRequest('2026-10-14','2026-10-15T09:00:00+08:00')
with patch('a_share_research.identity_sources.build_opener',return_value=Opener()):
    operations=build_default_market_foundation_operations()
    outcomes=tuple(operation.execute(request,.75) for operation in operations)
    result=fold_market_foundation(outcomes,request,operations=operations)
print(json.dumps({"result":result,"calls":calls,"outcomes":[outcome.to_dict() for outcome in outcomes]}))
"""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(
            prefix="asr-v040-112-real-installed-", dir="/private/tmp"
        ) as directory:
            installed = Path(directory) / "a-share-research"
            shutil.copytree(
                root / "skill/a-share-research",
                installed,
                ignore=shutil.ignore_patterns(".runtime", ".state", "__pycache__"),
            )
            scripts = installed / "scripts"
            completed = subprocess.run(
                [sys.executable, "-c", script, str(scripts)],
                input=json.dumps(
                    {owner: body.decode() for owner, body in bodies.items()}
                ),
                text=True,
                capture_output=True,
                check=True,
                close_fds=True,
                cwd=directory,
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "en_US.UTF-8",
                    "PYTHONUTF8": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(scripts),
                    "TMPDIR": directory,
                },
            )
        output = json.loads(completed.stdout)
        self.assertEqual(
            [row["attempts"] for row in output["result"]["operations"]], [1] * 6
        )
        self.assertEqual([row["timeout"] for row in output["calls"]], [0.75] * 6)
        self.assertEqual(
            [row["method"] for row in output["calls"]],
            ["GET", "GET", "POST", "GET", "GET", "GET"],
        )
        self.assertEqual(
            [row["source_result"] for row in output["result"]["operations"]],
            ["unavailable"] * 6,
        )
        self.assertEqual([row["observations"] for row in output["outcomes"]], [[]] * 6)
        self.assertIsNone(output["result"]["turnover"]["total"])

    def test_szse_positive_summary_control_echo_or_wrong_subname_never_proves_date(
        self,
    ):
        row = {
            "jrrzye": "120.50",
            "jrrzmr": "10.25",
            "jrrjye": "30.75",
            "jrrzrjye": "151.25",
            "jrrjmc": "2.00",
            "jrrjyl": "8.00",
        }
        request = OperationRequest("2026-10-13", REQUEST.information_cutoff)
        for subname, code in (
            ("", "source_report_date_unproved"),
            ("2026-10-12", "source_report_date_mismatch"),
        ):
            with self.subTest(subname=subname):
                http = FixtureHttp(
                    szse_report(
                        "1837_xxpl", "融资融券交易总量", [row], "2026-10-13", subname
                    )
                )
                outcome, result = run_owner(
                    "szse_market_margin@1",
                    sources.SzseMarketMarginReportTransport(http),
                    request,
                )
                self.assertEqual(outcome["source_errors"][0]["code"], code)
                self.assertEqual(outcome["observations"], [])
                self.assertEqual(result["operations"][4]["attempts"], 1)

    def test_szse_positive_summary_subname_binds_date_and_money_without_shares(self):
        # Q W1 proves one row with these six string fields and a dated subname;
        # every numeric value here is synthetic, not a retained provider value.
        row = {
            "jrrzye": "120.50",
            "jrrzmr": "10.25",
            "jrrjye": "30.75",
            "jrrzrjye": "151.25",
            "jrrjmc": "2.00",
            "jrrjyl": "8.00",
        }
        http = FixtureHttp(
            szse_report(
                "1837_xxpl", "融资融券交易总量", [row], "2026-10-13", "2026-10-13"
            )
        )
        request = OperationRequest("2026-10-13", REQUEST.information_cutoff)
        outcome, result = run_owner(
            "szse_market_margin@1",
            sources.SzseMarketMarginReportTransport(http),
            request,
        )
        proof = outcome["diagnostics"]["unadopted_report"]
        self.assertEqual(
            proof["metrics"],
            {
                "financing_balance_CNY": "12050000000",
                "financing_buy_amount_CNY": "1025000000",
                "securities_lending_balance_CNY": "3075000000",
                "margin_total_balance_CNY": "15125000000",
            },
        )
        self.assertEqual(
            proof["source_date_qualification"], "response_date_matches_request"
        )
        self.assertEqual(proof["row_count"], 1)
        self.assertEqual(
            proof["unproved_fields"],
            {
                "SECURITIES_LENDING_QUANTITY": "mixed_quantity_unit",
                "SECURITIES_LENDING_SELL_QUANTITY": "mixed_quantity_unit",
            },
        )
        self.assertEqual(
            outcome["source_errors"][0]["code"], "source_publication_unproved"
        )
        self.assertEqual(outcome["observations"], [])
        self.assertEqual(result["operations"][4]["attempts"], 1)

    def test_szse_cdr_inclusive_turnover_and_echo_date_never_become_a_share_total(self):
        rows = [
            {"zbmc": "成交量（亿）", "gp": "4.25", "xz": "1.75", "cy": "2.50"},
            {
                "zbmc": "成交金额（亿元）",
                "gp": "4,567.89",
                "xz": "1,234.56",
                "cy": "3,333.33",
            },
        ]
        http = FixtureHttp(szse_report("scsj_gprdgk_after", "日度概况", rows))
        outcome, result = run_owner(
            "szse_a_share_turnover@1", sources.SzseMarketTurnoverReportTransport(http)
        )
        proof = outcome["diagnostics"]["unadopted_report"]
        self.assertEqual(proof["metrics"], {})
        self.assertEqual(proof["source_scope_qualification"], "CDR_inclusive")
        self.assertEqual(proof["source_date_qualification"], "date_control_only")
        self.assertEqual(outcome["observations"], [])
        self.assertEqual(result["operations"][1]["attempts"], 1)
        self.assertIsNone(result["turnover"]["total"])
        params = parse_qs(urlsplit(http.calls[0][1]).query)
        self.assertEqual(params["CATALOGID"], ["scsj_gprdgk_after"])
        self.assertEqual(params["txtQueryDate"], ["2026-10-14"])
        self.assertEqual(params["bkbz"], ["GP|XZ|CY"])

    def test_szse_margin_empty_summary_keeps_control_date_distinct_from_business(self):
        http = FixtureHttp(szse_report("1837_xxpl", "融资融券交易总量", []))
        outcome, result = run_owner(
            "szse_market_margin@1", sources.SzseMarketMarginReportTransport(http)
        )
        proof = outcome["diagnostics"]["unadopted_report"]
        self.assertEqual(proof["metrics"], {})
        self.assertEqual(proof["source_date_qualification"], "date_control_only")
        self.assertEqual(proof["row_count"], 0)
        self.assertEqual(outcome["observations"], [])
        self.assertEqual(
            outcome["source_errors"][0]["code"], "source_report_empty_unqualified"
        )
        self.assertEqual(result["operations"][4]["attempts"], 1)
        self.assertEqual(result["operations"][4]["source_result"], "unavailable")
        params = parse_qs(urlsplit(http.calls[0][1]).query)
        self.assertEqual(
            params,
            {
                "SHOWTYPE": ["JSON"],
                "CATALOGID": ["1837_xxpl"],
                "txtDate": ["2026-10-14"],
            },
        )

    def test_sse_turnover_real_rows_do_not_prove_pure_a_cdr_exclusion(self):
        rows = [
            {"PRODUCT_CODE": code, "TRADE_DATE": "20261014", "TRADE_AMT": amount}
            for code, amount in (
                ("01", "30.25"),
                ("02", "9999"),
                ("03", "5.125"),
                ("11", "9999"),
                ("17", "9999"),
            )
        ]
        http = FixtureHttp(
            frame({"result": rows, "pageHelp": {"total": 0}}, "jsonpCallback121")
        )
        outcome, result = run_owner(
            "sse_a_share_turnover@1", sources.SseMarketTurnoverReportTransport(http)
        )
        proof = outcome["diagnostics"]["unadopted_report"]
        self.assertEqual(proof["metrics"], {"turnover_amount_CNY": "3537500000"})
        self.assertEqual(
            proof["source_scope_qualification"], "pure_A_CDR_exclusion_unproved"
        )
        self.assertEqual(outcome["source_errors"][0]["code"], "source_scope_unproved")
        self.assertEqual(outcome["observations"], [])
        self.assertIsNone(result["turnover"]["total"])
        self.assertEqual(result["operations"][0]["attempts"], 1)
        self.assertEqual(http.calls[0][2]["Referer"], "https://www.sse.com.cn/")
        params = parse_qs(urlsplit(http.calls[0][1]).query)
        self.assertEqual(params["sqlId"], ["COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C"])
        self.assertEqual(params["PRODUCT_CODE"], ["01,02,03,11,17"])
        self.assertEqual(params["SEARCH_DATE"], ["2026-10-14"])

    def test_sse_margin_parses_dated_money_without_mixed_quantity_share_claim(self):
        row = {
            "opDate": "20261013",
            "rzye": 1205000,
            "rzmre": 102500,
            "rqylje": 307500,
            "rzrqjyzl": 1512500,
            "rqyl": 80000,
            "rqmcl": 20000,
            "rzche": 120,
        }
        http = FixtureHttp(
            frame(
                {
                    "result": [row],
                    "pageHelp": {
                        "pageCount": 1,
                        "total": 1,
                        "pageNo": 1,
                        "pageSize": 25,
                    },
                },
                "jsonpCallback121",
            )
        )
        request = OperationRequest("2026-10-13", REQUEST.information_cutoff)
        outcome, result = run_owner(
            "sse_market_margin@1", sources.SseMarketMarginReportTransport(http), request
        )
        proof = outcome["diagnostics"]["unadopted_report"]
        self.assertEqual(
            proof["metrics"],
            {
                "financing_balance_CNY": "1205000",
                "financing_buy_amount_CNY": "102500",
                "securities_lending_balance_CNY": "307500",
                "margin_total_balance_CNY": "1512500",
            },
        )
        self.assertEqual(
            proof["unproved_fields"],
            {
                "SECURITIES_LENDING_QUANTITY": "mixed_quantity_unit",
                "SECURITIES_LENDING_SELL_QUANTITY": "mixed_quantity_unit",
            },
        )
        self.assertEqual(
            proof["source_date_qualification"], "response_date_matches_request"
        )
        self.assertEqual(result["operations"][3]["attempts"], 1)
        self.assertEqual(outcome["observations"], [])
        method, url, headers, _ = http.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(headers["Referer"], "https://www.sse.com.cn/")
        params = parse_qs(urlsplit(url).query, keep_blank_values=True)
        self.assertEqual(params["sqlId"], ["RZRQ_HZ_INFO"])
        self.assertEqual(params["beginDate"], ["20261013"])
        self.assertEqual(params["endDate"], ["20261013"])
        self.assertEqual(params["stockCode"], [""])

    def test_default_bse_daily_post_uses_owner_timeout_and_counted_leaf(self):
        calls = []
        body = frame([{"xxzrlx": "2", "rq": "20261014", "hqcjje": 125000}])

        class Response(BytesIO):
            status = 200
            headers = Message()

        class Opener:
            def open(self, request: Request, *, timeout: float):
                calls.append((request.get_method(), timeout, request.data))
                return Response(body)

        with patch(
            "a_share_research.identity_sources.build_opener", return_value=Opener()
        ):
            operations = build_default_market_foundation_operations()
            owner = operations[2]
            outcome = owner.execute(REQUEST, 0.75)
        result = fold_market_foundation((outcome,), REQUEST, operations=operations)
        self.assertEqual(calls, [("POST", 0.75, b"HQJSRQ=20261014")])
        self.assertEqual(result["operations"][2]["attempts"], 1)
        self.assertEqual(
            outcome.source_errors[0]["code"], "source_publication_unproved"
        )

    def test_bse_daily_exact_post_selects_class_two_raw_cny_without_xjx(self):
        http = FixtureHttp(
            frame(
                [
                    {"xxzrlx": "XJX", "rq": "20261014", "hqcjje": 999},
                    {"xxzrlx": "2", "rq": "20261014", "hqcjje": 125000.50},
                ]
            )
        )
        outcome, result = run_owner(
            "bse_a_share_turnover@1", sources.BseMarketTurnoverReportTransport(http)
        )
        proof = outcome["diagnostics"]["unadopted_report"]
        self.assertEqual(proof["metrics"], {"turnover_amount_CNY": "125000.5"})
        self.assertEqual(proof["source_publication"], "unknown")
        self.assertEqual(outcome["observations"], [])
        self.assertEqual(result["operations"][2]["attempts"], 1)
        method, url, headers, body = http.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://www.bse.cn/marketStatController/dailyReport.do")
        self.assertEqual(parse_qs(body.decode()), {"HQJSRQ": ["20261014"]})
        self.assertNotIn("latestDate", http.calls[0][1])


if __name__ == "__main__":
    unittest.main()
