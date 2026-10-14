from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.market_foundation import (  # noqa: E402
    OperationRequest,
    build_default_market_foundation_operations,
    fold_market_foundation,
)
from a_share_research.market_foundation_sources import BseMarketMarginReportTransport  # noqa: E402
from a_share_research.provider_lane import ProductionInvocationAdapter  # noqa: E402

CHINA = timezone(timedelta(hours=8))


def synthetic_summary(date: str = "2026-10-13") -> bytes:
    # Production-shaped field/frame names from frozen H/I; all values synthetic.
    row = {
        "rzmre": 102523,
        "rzye": 1205006,
        "rqmcl": 20000,
        "rqyl": 80000,
        "rqye": 307501,
        "rzrqye": 1512507,
        "rzmreRound": "10.25",
        "rzyeRound": "120.50",
        "rqmclRound": "2.00",
        "rqylRound": "8.00",
        "rqyeRound": "30.75",
        "rzrqyeRound": "151.25",
        "zqdm": "999999",
        "zqjc": "",
        "status": 0,
        "createTime": {"time": 1791936002000, "timezoneOffset": -480},
        "lastUpdateTime": {"time": 1791936002000, "timezoneOffset": -480},
        "sxrq": {"time": 1791820800000, "timezoneOffset": -480},
    }
    return ("null([" + json.dumps([row]) + ", '" + date + "']);").encode()


class FixtureHttp:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        return HttpResponse(
            200,
            "application/javascript",
            self.body,
            datetime(2026, 10, 14, 19, tzinfo=CHINA),
        )


class BseMarginOwnerTests(unittest.TestCase):
    def test_copied_install_default_uses_actual_parser_and_keeps_pubtime_unknown(self):
        script = """
import json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from a_share_research.identity_sources import HttpResponse
from a_share_research.market_foundation import OperationRequest,build_default_market_foundation_operations,fold_market_foundation
from a_share_research.provider_lane import ProductionInvocationAdapter
import a_share_research.market_foundation_sources as sources
assert Path(sources.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
body=sys.stdin.buffer.read()
class OwnedHttp:
    def __init__(self,*,provider_invocation): self.invocation=provider_invocation
    def get_with_timeout(self,url,headers,timeout):
        return self.invocation.call(ProductionInvocationAdapter(lambda: HttpResponse(200,'application/javascript',body,datetime(2026,10,14,19,tzinfo=timezone(timedelta(hours=8))))))
with patch.object(sources,'UrlLibTransport',OwnedHttp):
    operations=build_default_market_foundation_operations()
    owner=next(item for item in operations if item.operation_id=='bse_market_margin@1')
    request=OperationRequest('2026-10-13','2026-10-14T19:00:00+08:00')
    outcome=owner.execute(request,1)
    result=fold_market_foundation((outcome,),request,operations=operations)
print(json.dumps({'outcome':outcome.to_dict(),'fold':result}))
"""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(
            prefix="asr-v040-112-bse-install-", dir="/private/tmp"
        ) as directory:
            installed = Path(directory) / "a-share-research"
            shutil.copytree(
                root / "skill/a-share-research",
                installed,
                ignore=shutil.ignore_patterns(".runtime", ".state", "__pycache__"),
            )
            scripts = installed / "scripts"
            env = dict(os.environ, PYTHONPATH=str(scripts), PYTHONDONTWRITEBYTECODE="1")
            completed = subprocess.run(
                [sys.executable, "-c", script, str(scripts)],
                input=synthetic_summary(),
                cwd=directory,
                env=env,
                capture_output=True,
                close_fds=True,
                check=True,
            )
        projected = json.loads(completed.stdout)
        self.assertEqual(projected["outcome"]["observations"], [])
        self.assertEqual(
            projected["outcome"]["diagnostics"]["unadopted_report"]["metrics"][
                "financing_balance_CNY"
            ],
            "1205000",
        )
        self.assertEqual(projected["fold"]["operations"][-1]["attempts"], 1)
        self.assertEqual(
            projected["fold"]["operations"][-1]["source_result"], "unavailable"
        )

    def test_unknown_frames_missing_values_and_executable_payloads_fail_closed(self):
        bodies = [
            b"null([[], '2026-10-13']);alert('secret')",
            b"null([[], ('2026-10-13'+evil())]);",
            synthetic_summary().replace(b'"rzyeRound": "120.50"', b'"rzyeRound": "--"'),
            synthetic_summary().replace(b'"rzye": 1205006', b'"rzye": null'),
            synthetic_summary().replace(b'"rzye": 1205006', b'"rzye": NaN'),
            synthetic_summary().replace(b'"zqdm": "999999"', b'"zqdm": "920001"'),
            synthetic_summary().replace(
                b'"rzyeRound": "120.50"', b'"rzyeRound": "120.50", "rzyeRound": "999"'
            ),
        ]
        for body in bodies:
            with self.subTest(body_kind=len(body)):
                http = FixtureHttp(body)
                operations = build_default_market_foundation_operations(
                    transports={
                        "bse_market_margin@1": BseMarketMarginReportTransport(http)
                    }
                )
                owner = next(
                    item
                    for item in operations
                    if item.operation_id == "bse_market_margin@1"
                )
                outcome = owner.execute(
                    OperationRequest("2026-10-13", "2026-10-14T19:00:00+08:00"), 1
                )
                self.assertEqual(outcome.observations, ())
                self.assertEqual(
                    outcome.source_errors[0]["code"], "source_schema_unproved"
                )
                self.assertNotIn("secret", json.dumps(outcome.to_dict()))
                self.assertNotIn("raw_fields", outcome.to_dict()["diagnostics"])
                self.assertEqual(len(http.urls), 1)

    def test_unadopted_report_projection_is_recursively_detached(self):
        http = FixtureHttp(synthetic_summary())
        operations = build_default_market_foundation_operations(
            transports={"bse_market_margin@1": BseMarketMarginReportTransport(http)}
        )
        owner = next(
            item for item in operations if item.operation_id == "bse_market_margin@1"
        )
        outcome = owner.execute(
            OperationRequest("2026-10-13", "2026-10-14T19:00:00+08:00"), 1
        )
        projection = outcome.to_dict()
        proof = projection["diagnostics"]["unadopted_report"]
        proof["metrics"]["financing_balance_CNY"] = "999"
        proof["source_time_candidates"]["createTime"] = "publication"
        again = outcome.to_dict()["diagnostics"]["unadopted_report"]
        self.assertEqual(again["metrics"]["financing_balance_CNY"], "1205000")
        self.assertEqual(
            again["source_time_qualification"], "unqualified_source_record_times"
        )
        self.assertEqual(again["display_precision"]["FINANCING_BALANCE"], 2)

    def test_default_builder_uses_the_same_owned_bse_http_path(self):
        http = FixtureHttp(synthetic_summary())

        class OwnedFixtureHttp:
            def __init__(self, *, provider_invocation):
                self.invocation = provider_invocation

            def get_with_timeout(self, url, headers, timeout):
                return self.invocation.call(
                    ProductionInvocationAdapter(lambda: http.get(url, headers))
                )

        with patch(
            "a_share_research.market_foundation_sources.UrlLibTransport",
            OwnedFixtureHttp,
        ):
            operations = build_default_market_foundation_operations()
            owner = next(
                item
                for item in operations
                if item.operation_id == "bse_market_margin@1"
            )
            request = OperationRequest("2026-10-13", "2026-10-14T19:00:00+08:00")
            outcome = owner.execute(request, 1)
        result = fold_market_foundation((outcome,), request, operations=operations)
        self.assertEqual(len(http.urls), 1)
        self.assertEqual(result["operations"][-1]["attempts"], 1)
        self.assertEqual(result["operations"][-1]["source_result"], "unavailable")

    def test_empty_frame_is_not_zero_business_or_six_zero_fields(self):
        http = FixtureHttp(b"null([[], '2026-10-14']);")
        operations = build_default_market_foundation_operations(
            transports={"bse_market_margin@1": BseMarketMarginReportTransport(http)}
        )
        owner = next(
            item for item in operations if item.operation_id == "bse_market_margin@1"
        )
        request = OperationRequest("2026-10-14", "2026-10-14T19:00:00+08:00")
        outcome = owner.execute(request, 1)
        proof = outcome.to_dict()["diagnostics"]["unadopted_report"]
        self.assertEqual(
            outcome.source_errors[0]["code"], "source_report_empty_unqualified"
        )
        self.assertEqual(proof["metrics"], {})
        self.assertEqual(proof["source_date_qualification"], "date_control_only")
        self.assertEqual(outcome.observations, ())

    def test_previous_date_response_is_not_adopted_for_the_requested_date(self):
        http = FixtureHttp(synthetic_summary("2026-10-13"))
        operations = build_default_market_foundation_operations(
            transports={"bse_market_margin@1": BseMarketMarginReportTransport(http)}
        )
        owner = next(
            item for item in operations if item.operation_id == "bse_market_margin@1"
        )
        outcome = owner.execute(
            OperationRequest("2026-10-14", "2026-10-14T19:00:00+08:00"), 1
        )
        self.assertEqual(outcome.observations, ())
        self.assertEqual(
            outcome.source_errors[0]["code"], "source_report_date_mismatch"
        )

    def test_get_frame_and_round_values_are_preserved_without_publication_adoption(
        self,
    ):
        http = FixtureHttp(synthetic_summary())
        operations = build_default_market_foundation_operations(
            transports={"bse_market_margin@1": BseMarketMarginReportTransport(http)}
        )
        owner = next(
            item for item in operations if item.operation_id == "bse_market_margin@1"
        )
        request = OperationRequest("2026-10-13", "2026-10-14T19:00:00+08:00")
        outcome = owner.execute(request, 1)
        self.assertEqual(len(http.urls), 1)
        self.assertEqual(
            urlsplit(http.urls[0]).path, "/rzrqjyyexxController/summaryInfoResult.do"
        )
        self.assertEqual(
            parse_qs(urlsplit(http.urls[0]).query),
            {"transDate": ["2026-10-13"], "page": ["0"]},
        )
        self.assertEqual(outcome.observations, ())
        self.assertEqual(
            outcome.source_errors[0]["code"], "source_publication_unproved"
        )
        proof = outcome.to_dict()["diagnostics"]["unadopted_report"]
        self.assertEqual(proof["report_date"], "2026-10-13")
        self.assertEqual(proof["metrics"]["financing_balance_CNY"], "1205000")
        self.assertEqual(proof["metrics"]["financing_buy_amount_CNY"], "102500")
        self.assertEqual(
            proof["metrics"]["securities_lending_quantity_shares"], "80000"
        )
        self.assertEqual(proof["value_basis"], "reported_display")
        self.assertEqual(proof["source_publication"], "unknown")
        self.assertEqual(
            proof["source_time_candidates"]["createTime"], "2026-10-14T08:00:02+08:00"
        )
        self.assertNotIn("published_at", proof)
        result = fold_market_foundation((outcome,), request, operations=operations)
        self.assertEqual(result["operations"][-1]["attempts"], 1)
        self.assertEqual(result["operations"][-1]["source_result"], "unavailable")
        self.assertTrue(
            all(value is None for value in result["margin"]["totals"].values())
        )


if __name__ == "__main__":
    unittest.main()
