"""Experimental source operations for automatic reported and forecast valuation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from .identity_sources import (
    HttpResponse,
    HttpTransport,
    SourceOperationError,
    TransportError,
)

REQUIRED_FINANCIAL_ITEMS = {
    "income": {"营业收入", "归属于母公司所有者的净利润"},
    "balance": {"资产总计", "负债合计", "归属于母公司股东权益合计"},
    "cashflow": {"经营活动产生的现金流量净额", "现金及现金等价物净增加额"},
}


def _error(operation: str, code: str, message: str) -> SourceOperationError:
    return SourceOperationError(operation, code, message)


def _request(
    operation: str,
    transport: HttpTransport,
    url: str,
    referer: str,
) -> HttpResponse:
    try:
        response = transport.get(
            url,
            {
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.1",
                "Referer": referer,
                "User-Agent": "Mozilla/5.0 a-share-research-skill/1",
            },
        )
    except TransportError as error:
        raise _error(operation, error.code, str(error)) from error
    if response.status != 200:
        raise _error(
            operation,
            "upstream_http_error",
            f"The source returned HTTP status {response.status}.",
        )
    if not response.body.strip():
        raise _error(operation, "empty_response", "The source returned no data.")
    return response


def _json(operation: str, response: HttpResponse) -> object:
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise _error(
            operation,
            "unexpected_content_type",
            "The source response is not JSON.",
        )
    try:
        return json.loads(
            response.body.decode("utf-8"),
            parse_float=Decimal,
            parse_int=Decimal,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(
            operation,
            "unknown_schema",
            "The source JSON does not match the expected encoding or schema.",
        ) from error


def _decimal(value: object, operation: str, *, positive: bool = False) -> str:
    if not isinstance(value, (str, Decimal)):
        raise _error(operation, "unknown_schema", "A numeric field is missing.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _error(
            operation, "unknown_schema", "A numeric field is invalid."
        ) from error
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise _error(operation, "unknown_schema", "A numeric field is out of range.")
    return format(parsed, "f")


@dataclass(frozen=True)
class StockInfoObservation:
    security: str
    name: str
    total_shares: str
    provider_market_cap: str
    provider_price: str
    source_uri: str
    retrieved_at: datetime

    @property
    def evidence_id(self) -> str:
        return f"stock-info-eastmoney_stock_info@1-{self.security}"

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "market_observation",
            "source_operation": "eastmoney_stock_info@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": {
                    "name": self.name,
                    "effective_total_shares": self.total_shares,
                    "provider_market_cap": self.provider_market_cap,
                    "provider_price": self.provider_price,
                },
                "unit": {
                    "effective_total_shares": "shares",
                    "provider_market_cap": "CNY",
                    "provider_price": "CNY/share",
                },
            },
            "basis": "current_stock_information_snapshot",
            "evidence_time": self.retrieved_at.isoformat(),
            "available_at": self.retrieved_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"current stock information for {self.security}",
            },
            "limitations": [
                "experimental_source_operation",
                "effective_start_time_not_independently_verified",
                "provider_market_cap_is_cross_check_only",
            ],
        }


class EastmoneyStockInfoOperation:
    operation_id = "eastmoney_stock_info@1"
    endpoints = (
        "https://push2delay.eastmoney.com/api/qt/stock/get",
        "https://push2.eastmoney.com/api/qt/stock/get",
    )

    def observe(self, security: str, transport: HttpTransport) -> StockInfoObservation:
        exchange, code = security.split(":", 1)
        market = {"SZSE": "0", "SSE": "1"}.get(exchange)
        if market is None:
            raise ValueError("stock information requires an SSE/SZSE security")
        query = urlencode(
            {
                "fltt": "2",
                "invt": "2",
                "fields": "f57,f58,f84,f85,f116,f117,f189,f43",
                "secid": f"{market}.{code}",
            }
        )
        response: HttpResponse | None = None
        url = ""
        for index, endpoint in enumerate(self.endpoints):
            url = f"{endpoint}?{query}"
            try:
                response = _request(
                    self.operation_id,
                    transport,
                    url,
                    "https://quote.eastmoney.com/",
                )
                break
            except SourceOperationError as error:
                if (
                    error.code != "upstream_unavailable"
                    or index == len(self.endpoints) - 1
                ):
                    raise
        if response is None:
            raise AssertionError("stock-information endpoint selection is incomplete")
        payload = _json(self.operation_id, response)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The stock-information payload is missing its data object.",
            )
        if data.get("f57") != code:
            raise _error(
                self.operation_id,
                "wrong_security_payload",
                "The stock-information response identifies another security.",
            )
        name = data.get("f58")
        if not isinstance(name, str) or not name:
            raise _error(
                self.operation_id, "unknown_schema", "The security name is missing."
            )
        return StockInfoObservation(
            security=security,
            name=name,
            total_shares=_decimal(data.get("f84"), self.operation_id, positive=True),
            provider_market_cap=_decimal(
                data.get("f116"), self.operation_id, positive=True
            ),
            provider_price=_decimal(data.get("f43"), self.operation_id, positive=True),
            source_uri=url,
            retrieved_at=response.retrieved_at,
        )


@dataclass(frozen=True)
class FinancialStatementObservation:
    security: str
    statement_type: str
    period: date
    publication_date: date
    currency: str
    report_scope: str
    audit_status: str
    data_source: str
    update_time: str
    values: dict[str, str]
    source_uri: str
    retrieved_at: datetime

    @property
    def evidence_id(self) -> str:
        return (
            f"statement-sina_financial_statements@1-{self.security}-"
            f"{self.statement_type}-{self.period.isoformat()}"
        )

    def to_evidence(self, selected_fields: tuple[str, ...]) -> dict[str, Any]:
        values = {
            field: self.values[field]
            for field in selected_fields
            if field in self.values
        }
        return {
            "id": self.evidence_id,
            "source_role": "authoritative_disclosure",
            "source_operation": "sina_financial_statements@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {"value": values, "unit": self.currency},
            "basis": f"reported_{self.statement_type}_statement",
            "report": {
                "period": self.period.isoformat(),
                "publication_date": self.publication_date.isoformat(),
                "scope": self.report_scope,
                "audit_status": self.audit_status,
                "data_source": self.data_source,
                "source_update_time": self.update_time,
            },
            "evidence_time": self.period.isoformat(),
            "available_at": self.publication_date.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": (
                    f"{self.statement_type} statement for {self.security} "
                    f"at {self.period.isoformat()}"
                ),
            },
            "limitations": [
                "experimental_source_operation",
                "report_version_relationship_not_independently_verified",
            ],
        }


class SinaFinancialStatementsOperation:
    operation_id = "sina_financial_statements@1"
    endpoint = (
        "https://quotes.sina.cn/cn/api/openapi.php/"
        "CompanyFinanceService.getFinanceReport2022"
    )
    source_types = {"income": "lrb", "balance": "fzb", "cashflow": "llb"}

    def observe(
        self,
        security: str,
        as_of: date,
        transport: HttpTransport,
        *,
        periods: int = 8,
    ) -> dict[str, list[FinancialStatementObservation]]:
        exchange, code = security.split(":", 1)
        prefix = {"SSE": "sh", "SZSE": "sz"}.get(exchange)
        if prefix is None:
            raise ValueError("financial statements require an SSE/SZSE security")
        result: dict[str, list[FinancialStatementObservation]] = {}
        for statement_type, source in self.source_types.items():
            url = f"{self.endpoint}?{
                urlencode(
                    {
                        'paperCode': f'{prefix}{code}',
                        'source': source,
                        'type': '0',
                        'page': '1',
                        'num': str(periods),
                    }
                )
            }"
            response = _request(
                self.operation_id,
                transport,
                url,
                "https://finance.sina.com.cn/",
            )
            payload = _json(self.operation_id, response)
            report_list = (
                payload.get("result", {}).get("data", {}).get("report_list")
                if isinstance(payload, dict)
                and isinstance(payload.get("result"), dict)
                and isinstance(payload["result"].get("data"), dict)
                else None
            )
            if not isinstance(report_list, dict) or not report_list:
                raise _error(
                    self.operation_id,
                    "empty_observation",
                    f"Sina returned no {statement_type} statement observations.",
                )
            observations = []
            for encoded_period, report in report_list.items():
                observation = self._parse_report(
                    security,
                    statement_type,
                    encoded_period,
                    report,
                    url,
                    response.retrieved_at,
                )
                if observation.publication_date <= as_of:
                    observations.append(observation)
            if not observations:
                raise _error(
                    self.operation_id,
                    "no_report_before_research_boundary",
                    f"No {statement_type} statement was public by the research date.",
                )
            result[statement_type] = sorted(
                observations, key=lambda item: item.period, reverse=True
            )
        return result

    def _parse_report(
        self,
        security: str,
        statement_type: str,
        encoded_period: object,
        report: object,
        source_uri: str,
        retrieved_at: datetime,
    ) -> FinancialStatementObservation:
        if not isinstance(encoded_period, str) or not isinstance(report, dict):
            raise _error(
                self.operation_id, "unknown_schema", "A report row is invalid."
            )
        try:
            period = datetime.strptime(encoded_period, "%Y%m%d").date()
            publication_date = datetime.strptime(
                report["publish_date"], "%Y%m%d"
            ).date()
        except (KeyError, TypeError, ValueError) as error:
            raise _error(
                self.operation_id,
                "unknown_schema",
                "A report period or publication date is invalid.",
            ) from error
        if report.get("rType") != "合并期末" or report.get("rCurrency") != "CNY":
            raise _error(
                self.operation_id,
                "financial_scope_mismatch",
                "The financial report is not a consolidated CNY period-end report.",
            )
        audit_value = report.get("is_audit")
        audit_status = (
            {"是": "audited", "未审计": "unaudited"}.get(audit_value)
            if isinstance(audit_value, str)
            else None
        )
        if audit_status is None or not isinstance(report.get("data_source"), str):
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The report audit or data-source field is invalid.",
            )
        rows = report.get("data")
        if not isinstance(rows, list):
            raise _error(
                self.operation_id, "unknown_schema", "Report rows are missing."
            )
        values: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise _error(
                    self.operation_id, "unknown_schema", "A report item is invalid."
                )
            title = row.get("item_title")
            value = row.get("item_value")
            if not isinstance(title, str) or not title or value in (None, ""):
                continue
            if title not in REQUIRED_FINANCIAL_ITEMS[statement_type]:
                continue
            if title in values:
                raise _error(
                    self.operation_id,
                    "duplicate_financial_item",
                    "A report contains a duplicate financial item.",
                )
            values[title] = _decimal(value, self.operation_id)
        if not values:
            raise _error(
                self.operation_id,
                "empty_observation",
                "A financial report contains no numeric observations.",
            )
        update_time = report.get("update_time")
        return FinancialStatementObservation(
            security=security,
            statement_type=statement_type,
            period=period,
            publication_date=publication_date,
            currency="CNY",
            report_scope="consolidated",
            audit_status=audit_status,
            data_source=report["data_source"],
            update_time=(
                format(update_time, "f")
                if isinstance(update_time, Decimal)
                else str(update_time)
            ),
            values=values,
            source_uri=source_uri,
            retrieved_at=retrieved_at,
        )


@dataclass(frozen=True)
class ConsensusEpsObservation:
    year: int
    institutions: int
    minimum: str
    mean: str
    maximum: str


@dataclass(frozen=True)
class ConsensusEpsSnapshot:
    security: str
    forecasts: tuple[ConsensusEpsObservation, ...]
    source_uri: str
    retrieved_at: datetime

    @property
    def evidence_id(self) -> str:
        return (
            f"forecast-ths_consensus_eps@1-{self.security}-{self.retrieved_at.date()}"
        )

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "source_role": "signed_opinion",
            "source_operation": "ths_consensus_eps@1",
            "experimental": True,
            "subject": {"security": self.security},
            "observed_value": {
                "value": [
                    {
                        "year": item.year,
                        "institutions": item.institutions,
                        "minimum": item.minimum,
                        "mean": item.mean,
                        "maximum": item.maximum,
                    }
                    for item in self.forecasts
                ],
                "unit": "CNY/share",
            },
            "basis": "source_aggregated_annual_consensus_eps",
            "evidence_time": self.retrieved_at.isoformat(),
            "available_at": None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "locator": {
                "uri": self.source_uri,
                "observation": f"annual consensus EPS snapshot for {self.security}",
            },
            "limitations": [
                "experimental_source_operation",
                "source_aggregated_consensus",
                "aggregate_first_publication_time_unknown",
            ],
        }


class _TableCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[tuple[str, list[list[str]]]] = []
        self._in_table = False
        self._in_caption = False
        self._in_cell = False
        self._caption: list[str] = []
        self._cell: list[str] = []
        self._row: list[str] = []
        self._rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._caption = []
            self._rows = []
        elif self._in_table and tag == "caption":
            self._in_caption = True
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in {"th", "td"}:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._in_caption:
            self._caption.append(data)
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "caption" and self._in_caption:
            self._in_caption = False
        elif tag in {"th", "td"} and self._in_cell:
            self._row.append("".join(self._cell).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_table and self._row:
            self._rows.append(self._row)
        elif tag == "table" and self._in_table:
            self.tables.append(("".join(self._caption).strip(), self._rows))
            self._in_table = False


class ThsConsensusEpsOperation:
    operation_id = "ths_consensus_eps@1"
    endpoint = "https://basic.10jqka.com.cn/new"

    def observe(self, security: str, transport: HttpTransport) -> ConsensusEpsSnapshot:
        _, code = security.split(":", 1)
        url = f"{self.endpoint}/{code}/worth.html"
        response = _request(
            self.operation_id,
            transport,
            url,
            "https://basic.10jqka.com.cn/",
        )
        text = ""
        for encoding in ("gb18030", "utf-8"):
            try:
                candidate = response.body.decode(encoding)
            except UnicodeDecodeError:
                continue
            if "预测年报每股收益" in candidate:
                text = candidate
                break
        if not text:
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The consensus page does not contain the expected EPS table.",
            )
        parser = _TableCollector()
        parser.feed(text)
        table = next(
            (rows for caption, rows in parser.tables if "预测年报每股收益" in caption),
            None,
        )
        if not table:
            raise _error(
                self.operation_id,
                "unknown_schema",
                "The consensus EPS table could not be parsed.",
            )
        forecasts = []
        for row in table:
            if len(row) < 5 or not row[0].isdigit():
                continue
            try:
                year = int(row[0])
                institutions = int(row[1])
            except ValueError as error:
                raise _error(
                    self.operation_id,
                    "unknown_schema",
                    "A consensus EPS year or institution count is invalid.",
                ) from error
            forecasts.append(
                ConsensusEpsObservation(
                    year=year,
                    institutions=institutions,
                    minimum=_decimal(row[2], self.operation_id),
                    mean=_decimal(row[3], self.operation_id),
                    maximum=_decimal(row[4], self.operation_id),
                )
            )
        if not forecasts:
            raise _error(
                self.operation_id,
                "empty_observation",
                "The source returned no consensus EPS forecasts.",
            )
        if len({item.year for item in forecasts}) != len(forecasts):
            raise _error(
                self.operation_id,
                "forecast_period_conflict",
                "The consensus EPS table repeats a forecast year.",
            )
        return ConsensusEpsSnapshot(
            security=security,
            forecasts=tuple(sorted(forecasts, key=lambda item: item.year)),
            source_uri=url,
            retrieved_at=response.retrieved_at,
        )
