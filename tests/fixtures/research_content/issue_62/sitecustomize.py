"""Copied-installed default-registry fixture for Issue 62."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

INSTALLED_SCRIPTS = Path(os.environ["A_SHARE_ISSUE62_INSTALLED_SCRIPTS"])
IDENTITY_FIXTURES = Path(os.environ["A_SHARE_ISSUE62_IDENTITY_FIXTURES"])
CONTENT_FIXTURES = Path(os.environ["A_SHARE_ISSUE62_CONTENT_FIXTURES"])
SCENARIO = os.environ["A_SHARE_ISSUE62_SCENARIO"]

sys.path.insert(0, str(INSTALLED_SCRIPTS))

import a_share_research.cli as cli  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RETRIEVED_AT = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)


def _response(
    path: Path,
    *,
    content_type: str = "application/json",
) -> HttpResponse:
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=path.read_bytes(),
        retrieved_at=RETRIEVED_AT,
    )


class FixtureIdentityTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlsplit(url).netloc
        if host == "query.sse.com.cn":
            if SCENARIO in {
                "reports_consensus_search",
                "sse_announcement_with_cninfo_failure",
            }:
                return _response(IDENTITY_FIXTURES / "sse_601138.json")
            return HttpResponse(
                status=200,
                content_type="application/json",
                body=b'{"result":[]}',
                retrieved_at=RETRIEVED_AT,
            )
        if host == "www.szse.cn":
            if SCENARIO in {
                "reports_consensus_search",
                "sse_announcement_with_cninfo_failure",
            }:
                return _response(IDENTITY_FIXTURES / "szse_empty.json")
            return _response(IDENTITY_FIXTURES / "szse_300058.json")
        if host == "www.cninfo.com.cn":
            return _response(IDENTITY_FIXTURES / "cninfo_current_orgs.json")
        raise AssertionError("unexpected Issue 62 identity source operation")


class FixtureContentTransport:
    def __init__(self) -> None:
        self._news_page = 0

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        host = urlsplit(url).netloc
        if SCENARIO in {"market_flash_primary_success", "market_flash_fallback"}:
            if host == "www.cls.cn":
                if SCENARIO == "market_flash_fallback":
                    return HttpResponse(
                        status=200,
                        content_type="application/json",
                        body=b'{"errno":0,"data":{"roll_data":[]}}',
                        retrieved_at=RETRIEVED_AT,
                    )
                return _response(
                    CONTENT_FIXTURES / "communications" / "cls_success.json"
                )
            if host == "np-weblist.eastmoney.com":
                if SCENARIO != "market_flash_fallback":
                    raise AssertionError("fallback ran after primary success")
                payload = json.loads(
                    (
                        CONTENT_FIXTURES / "communications" / "eastmoney_success.json"
                    ).read_text(encoding="utf-8")
                )
                payload["req_trace"] = parse_qs(urlsplit(url).query)["req_trace"][0]
                return HttpResponse(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload).encode("utf-8"),
                    retrieved_at=RETRIEVED_AT,
                )
        if SCENARIO == "reports_consensus_search":
            if host == "reportapi.eastmoney.com":
                page = int(parse_qs(urlsplit(url).query)["pageNo"][0])
                return _response(
                    CONTENT_FIXTURES / "reports" / f"eastmoney_stock_page_{page}.json",
                    content_type="text/plain",
                )
            if host == "basic.10jqka.com.cn":
                return _response(
                    CONTENT_FIXTURES / "reports" / "ths_consensus_material.html",
                    content_type="text/html",
                )
        if (
            SCENARIO == "sse_announcement_with_cninfo_failure"
            and host == "query.sse.com.cn"
        ):
            request_query = parse_qs(urlsplit(url).query)
            page = int(request_query["pageHelp.pageNo"][0])
            payload = json.loads(
                (
                    CONTENT_FIXTURES
                    / "disclosures"
                    / f"sse_announcements_page_{page}.json"
                ).read_text(encoding="utf-8")
            )
            payload["pageHelp"]["pageSize"] = int(request_query["pageHelp.pageSize"][0])
            return HttpResponse(
                status=200,
                content_type="application/json",
                body=json.dumps(payload).encode("utf-8"),
                retrieved_at=RETRIEVED_AT,
            )
        if (
            SCENARIO == "sse_announcement_with_cninfo_failure"
            and host == "www.cninfo.com.cn"
            and urlsplit(url).path == "/new/data/szse_stock.json"
        ):
            return _response(IDENTITY_FIXTURES / "cninfo_current_orgs.json")
        if SCENARIO in {"bluefocus_disclosures_news", "iwencai_announcement"}:
            if host == "search-api-web.eastmoney.com":
                self._news_page += 1
                fixture_path = (
                    CONTENT_FIXTURES
                    / "disclosures"
                    / f"eastmoney_news_page_{self._news_page}.jsonp"
                )
                body = fixture_path.read_bytes().replace(
                    "300058业务动态".encode(),
                    "蓝色光标公告观察".encode(),
                )
                return HttpResponse(
                    status=200,
                    content_type="text/javascript",
                    body=body,
                    retrieved_at=RETRIEVED_AT,
                )
            if (
                host == "www.cninfo.com.cn"
                and urlsplit(url).path == "/new/data/szse_stock.json"
            ):
                return _response(
                    CONTENT_FIXTURES / "disclosures" / "cninfo_stock_map.json"
                )
        raise AssertionError("unexpected Issue 62 content GET operation")

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HttpResponse:
        host = urlsplit(url).netloc
        if SCENARIO == "investor_qa" and host == "irm.cninfo.com.cn":
            if urlsplit(url).path == "/newircs/index/queryKeyboardInfo":
                return _response(
                    CONTENT_FIXTURES / "communications" / "cninfo_lookup_300058.json"
                )
            if urlsplit(url).path == "/newircs/company/question":
                return _response(
                    CONTENT_FIXTURES / "communications" / "cninfo_questions_300058.json"
                )
        if (
            SCENARIO == "sse_announcement_with_cninfo_failure"
            and host == "www.cninfo.com.cn"
            and urlsplit(url).path == "/new/hisAnnouncement/query"
        ):
            return _response(
                CONTENT_FIXTURES / "disclosures" / "cninfo_announcements_page_1.json"
            )
        if SCENARIO == "reports_consensus_search":
            expected_secret = os.environ.get("IWENCAI_API_KEY")
            if not expected_secret or headers.get("Authorization") != (
                f"Bearer {expected_secret}"
            ):
                raise AssertionError("named iWencai credential was not used")
            if url != "https://iwencai.fixture.test/v1/comprehensive/search":
                raise AssertionError("named iWencai base URL was not used")
            if json.loads(body) != {
                "channels": ["report"],
                "app_id": "AIME_SKILL",
                "query": "AI服务器 算力产业链",
                "size": 20,
            }:
                raise AssertionError("unexpected semantic report request")
            return _response(
                CONTENT_FIXTURES / "reports" / "iwencai_report_success.json"
            )
        if SCENARIO == "iwencai_announcement" and host == "iwencai.fixture.test":
            expected_secret = os.environ.get("IWENCAI_API_KEY")
            if not expected_secret or headers.get("Authorization") != (
                f"Bearer {expected_secret}"
            ):
                raise AssertionError("named iWencai credential was not used")
            if json.loads(body) != {
                "channels": ["announcement"],
                "app_id": "AIME_SKILL",
                "query": "公司公告",
                "size": 20,
            }:
                raise AssertionError("unexpected semantic announcement request")
            payload = json.loads(
                (
                    CONTENT_FIXTURES / "reports" / "iwencai_report_success.json"
                ).read_text(encoding="utf-8")
            )
            payload["data"][0]["stock_infos"] = [{"code": "300058", "name": "蓝色光标"}]
            return HttpResponse(
                status=200,
                content_type="application/json",
                body=json.dumps(payload).encode("utf-8"),
                retrieved_at=RETRIEVED_AT,
            )
        if SCENARIO in {"bluefocus_disclosures_news", "iwencai_announcement"}:
            if (
                host == "www.cninfo.com.cn"
                and urlsplit(url).path == "/new/hisAnnouncement/query"
            ):
                page = "2" if b"pageNum=2" in body else "1"
                return _response(
                    CONTENT_FIXTURES
                    / "disclosures"
                    / f"cninfo_announcements_page_{page}.json"
                )
            if (
                host == "www.szse.cn"
                and urlsplit(url).path == "/api/disc/announcement/annList"
            ):
                page = json.loads(body).get("pageNum")
                if page not in {1, 2}:
                    raise AssertionError("unexpected Issue 62 SZSE fixture page")
                return _response(
                    CONTENT_FIXTURES
                    / "disclosures"
                    / f"szse_announcements_page_{page}.json"
                )
        raise AssertionError("unexpected Issue 62 content POST operation")


_original_research = cli.research


def _fixture_research(request: dict[str, object], **kwargs: object) -> object:
    del kwargs
    return _original_research(
        request,
        identity_transport=FixtureIdentityTransport(),
        content_transport=FixtureContentTransport(),
        research_now=RETRIEVED_AT,
        available_optional_dependencies=set(),
    )


cli.research = _fixture_research
