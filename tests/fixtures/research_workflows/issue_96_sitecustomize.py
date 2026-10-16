"""Synthetic owner seams appended to copied-installed Issue 96 bootstraps.

This file deliberately has no future import: tests may append it to an existing
sitecustomize source. It imports only the copied skill, never repository tests.
"""

import json as _asr96_json
import os as _asr96_os
import sys as _asr96_sys
from datetime import date as _asr96_date
from datetime import datetime as _asr96_datetime
from datetime import timedelta as _asr96_timedelta
from pathlib import Path as _asr96_Path
from zoneinfo import ZoneInfo as _asr96_ZoneInfo

_asr96_sys.path.insert(0, str(_asr96_Path(_asr96_sys.argv[0]).resolve().parent))

import a_share_research.market_overview_foundation as _asr96_module  # noqa: E402
from a_share_research.fixed_index_observations import (
    FIXED_INDEX_IDS as _asr96_INDEX_IDS,
)
from a_share_research.fixed_index_observations import (  # noqa: E402
    DeterministicFixedIndexAdapter as _asr96_DeterministicIndices,
)
from a_share_research.fixed_index_observations import (
    _session_use as _asr96_index_session_use,
)
from a_share_research.identity_sources import (  # noqa: E402
    SourceOperationError as _asr96_SourceOperationError,
)
from a_share_research.market_foundation import (  # noqa: E402
    OWNER_IDS as _asr96_OWNER_IDS,
)
from a_share_research.market_foundation import (
    build_default_market_foundation_operations as _asr96_real_foundation_builder,
)
from a_share_research.market_session_context import (  # noqa: E402
    CalendarDay as _asr96_CalendarDay,
)
from a_share_research.market_session_context import (
    CalendarEvidence as _asr96_CalendarEvidence,
)
from a_share_research.market_session_context import (
    DeterministicMarketSessionAdapter as _asr96_DeterministicSession,
)
from a_share_research.provider_lane import (  # noqa: E402
    ProductionInvocationAdapter as _asr96_Invocation,
)
from a_share_research.research_result import (  # noqa: E402
    EvidenceUsePolicy as _asr96_EvidenceUsePolicy,
)
from a_share_research.research_result import (
    FieldDisposition as _asr96_FieldDisposition,
)
from a_share_research.research_result import (
    OperationObligation as _asr96_OperationObligation,
)

_ASR96_TZ = _asr96_ZoneInfo("Asia/Shanghai")
_ASR96_NOW = _asr96_datetime.fromisoformat(
    _asr96_os.environ.get("ASR96_NOW", "2026-08-03T19:40:00+08:00")
).astimezone(_ASR96_TZ)
_ASR96_SCENARIO = _asr96_os.environ.get("ASR96_SCENARIO", "complete")
_ASR96_SCENARIOS = {
    "complete",
    "missing_bse_turnover",
    "wrong_unit",
    "stale_index",
    "missing_bse_session",
    "missing_bse_index",
    "no_proxy",
    "empty_bse_margin",
    "partial_bse_margin",
    "partial_empty_bse_margin",
}
if _ASR96_SCENARIO not in _ASR96_SCENARIOS:
    raise ValueError("Unknown Issue 96 fixture scenario.")


def _asr96_record(operation_id, **fields):
    destination = _asr96_os.environ.get("ASR96_CALLS")
    if destination is None:
        return
    path = _asr96_Path(destination)
    if not path.is_absolute():
        raise ValueError("Issue 96 fixture call log must be absolute.")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            _asr96_json.dumps({"operation_id": operation_id, **fields}, sort_keys=True)
            + "\n"
        )


def _asr96_calendars():
    # Explicitly synthetic days and rules, including the prior completed date
    # needed to resolve the first August weekend without inventing a session.
    first, last = _asr96_date(2026, 7, 30), _asr96_date(2026, 11, 1)
    days = tuple(
        _asr96_CalendarDay(
            (first + _asr96_timedelta(days=offset)).isoformat(),
            "trading"
            if (first + _asr96_timedelta(days=offset)).weekday() < 5
            else "weekend",
        )
        for offset in range((last - first).days + 1)
    )
    return tuple(
        _asr96_CalendarEvidence(
            exchange=exchange,
            evidence_id="synthetic-96-calendar-" + exchange,
            version="synthetic-96-1",
            provenance_uri="fixture:issue96:" + exchange + ":calendar",
            available_at="2026-07-01T00:00:00+08:00",
            retrieved_at="2026-07-02T00:00:00+08:00",
            effective_from=first.isoformat(),
            effective_to=last.isoformat(),
            coverage_start=first.isoformat(),
            coverage_end=last.isoformat(),
            authority="proved",
            days=days,
            auction_times=(
                "09:15",
                "09:25",
                "09:30",
                "11:30",
                "13:00",
                "14:57",
                "15:00",
            ),
            rule_evidence_id="synthetic-96-rule-" + exchange,
            rule_provenance_uri="fixture:issue96:" + exchange + ":rule",
            synthetic=True,
            trading_day_completion_time="15:30",
            completion_evidence_id="synthetic-96-completion-" + exchange,
        )
        for exchange in ("SSE", "SZSE", "BSE")
        if _ASR96_SCENARIO != "missing_bse_session" or exchange != "BSE"
    )


class _Asr96Session(_asr96_DeterministicSession):
    @property
    def obligation(self):
        return _asr96_OperationObligation(
            "market_trading_session",
            "market_session_context@1",
            "exchange_document",
            False,
            None,
            (_asr96_EvidenceUsePolicy("market_trading_session", "supporting"),),
        )

    @property
    def field_dispositions(self):
        return (
            _asr96_FieldDisposition(
                "calendar_and_rule_proof", "normalized", "observations[].payload"
            ),
        )

    def execute(self, request, timeout_seconds):
        _asr96_record(
            "market_session_context@1", requested_as_of=request.requested_as_of
        )
        return super().execute(request, timeout_seconds)


def _asr96_build_session(transport=None, *, clock=None, execution_clock=None):
    del transport, clock, execution_clock
    return _Asr96Session(_asr96_calendars(), clock=lambda: _ASR96_NOW)


class _Asr96OwnedSession:
    def __init__(self, outcome):
        self.outcome = outcome

    def execute(self, request, timeout_seconds):
        del request, timeout_seconds
        return self.outcome


class _Asr96Indices(_asr96_DeterministicIndices):
    @property
    def obligation(self):
        return _asr96_OperationObligation(
            "major_broad_indices",
            "fixed_four_indices@1",
            "market_observation",
            False,
            None,
            (_asr96_EvidenceUsePolicy("major_broad_indices", "supporting"),),
        )

    def execute(self, request, timeout_seconds):
        _asr96_record(
            "fixed_four_indices@1",
            requested_as_of=request.session_request.requested_as_of,
            purpose=request.purpose,
            completed_basis=request.completed_basis,
        )
        session = self._sessions.execute(request.session_request, timeout_seconds)
        matching = [
            item
            for item in session.to_dict()["observations"]
            if item.get("data_type") == "market_session_context"
        ]
        material = []
        if len(matching) == 1:
            use = _asr96_index_session_use(request, matching[0]["payload"])
            for identity in _asr96_INDEX_IDS:
                if _ASR96_SCENARIO == "missing_bse_index" and identity == "BSE:899050":
                    continue
                target = use.target_date
                if _ASR96_SCENARIO == "stale_index" and identity == "SSE:000001":
                    target = (
                        _asr96_date.fromisoformat(target) - _asr96_timedelta(days=1)
                    ).isoformat()
                material.append(
                    {
                        "canonical_id": identity,
                        "value": "101.25",
                        "previous_close": "100",
                        "change": "1.25",
                        "change_percent": "1.25",
                        "unit": "index_point",
                        "price_semantics": "current_value"
                        if use.current
                        else "completed_close",
                        "baseline_semantics": "previous_session_close",
                        "session_date": target,
                        "observed_at": (
                            _ASR96_NOW - _asr96_timedelta(seconds=5)
                        ).isoformat()
                        if use.current
                        else None,
                        "source_time_precision": "second" if use.current else "day",
                        "retrieved_at": _ASR96_NOW.isoformat(),
                        "provenance_uri": "fixture:issue96:index:" + identity,
                        "source_version": "synthetic-96-1",
                        "synthetic": True,
                    }
                )
        return _asr96_DeterministicIndices(
            material,
            session_adapter=_Asr96OwnedSession(session),
            clock=lambda: _ASR96_NOW,
        ).execute(request, timeout_seconds)


def _asr96_build_indices(
    transport=None, *, session_adapter=None, clock=None, monotonic_clock=None
):
    del transport, clock, monotonic_clock
    sessions = _asr96_build_session() if session_adapter is None else session_adapter
    return _Asr96Indices((), session_adapter=sessions, clock=lambda: _ASR96_NOW)


class _Asr96ReportTransport:
    def __init__(self, owner):
        self.owner = owner

    def acquire(self, selector, request, timeout, invocation):
        del selector, timeout

        def read_report():
            _asr96_record(
                self.owner,
                report_date=request.report_date,
                information_cutoff=request.information_cutoff,
            )
            if (
                _ASR96_SCENARIO == "missing_bse_turnover"
                and self.owner == "bse_a_share_turnover@1"
            ):
                raise _asr96_SourceOperationError(
                    self.owner,
                    "source_response_unavailable",
                    "Synthetic missing report.",
                )
            exchange = self.owner.split("_", 1)[0].upper()
            turnover = "a_share_turnover" in self.owner
            if turnover:
                fields = (
                    {"TRADE_AMT_01": "30", "TRADE_AMT_03": "5"}
                    if exchange == "SSE"
                    else {"A_SHARE_TURNOVER_HUNDRED_MILLION_CNY": "20"}
                    if exchange == "SZSE"
                    else {"A_SHARE_TURNOVER_CNY": "1250005000"}
                )
                unit = "CNY" if exchange == "BSE" else "hundred_million_CNY"
                units = {field: unit for field in fields}
                scope = "exchange_daily_A_share_stock_turnover"
            else:
                fields = {
                    "FINANCING_BALANCE": "1205000",
                    "FINANCING_BUY_AMOUNT": "102500",
                    "SECURITIES_LENDING_SELL_QUANTITY": "20000",
                    "SECURITIES_LENDING_QUANTITY": "80000",
                    "SECURITIES_LENDING_BALANCE": "307500",
                    "MARGIN_TOTAL_BALANCE": "1512500",
                }
                if exchange == "BSE":
                    fields = dict(
                        zip(
                            fields,
                            ("120.50", "10.25", "2.00", "8.00", "30.75", "151.25"),
                            strict=True,
                        )
                    )
                elif exchange == "SZSE":
                    fields.update(
                        FINANCING_BALANCE="0.01205",
                        FINANCING_BUY_AMOUNT="0.001025",
                        SECURITIES_LENDING_BALANCE="0.003075",
                        MARGIN_TOTAL_BALANCE="0.015125",
                    )
                units = {
                    field: ("ten_thousand_" if exchange == "BSE" else "")
                    + (
                        "shares"
                        if "QUANTITY" in field
                        else "hundred_million_CNY"
                        if exchange == "SZSE"
                        else "CNY"
                    )
                    for field in fields
                }
                scope = {
                    "SSE": "exchange_wide_official_margin_trading_summary",
                    "SZSE": "exchange_wide_official_margin_trading_total",
                    "BSE": "exchange_wide_official_margin_trading_aggregate",
                }[exchange]
            if (
                _ASR96_SCENARIO == "wrong_unit"
                and self.owner == "sse_a_share_turnover@1"
            ):
                units["TRADE_AMT_01"] = "shares"
            if _ASR96_SCENARIO == "no_proxy" and turnover:
                scope = "single_ETF_index_or_board_proxy"
            empty_margin = (
                _ASR96_SCENARIO == "empty_bse_margin"
                and self.owner == "bse_market_margin@1"
            )
            if empty_margin:
                fields, units = {}, {}
            partial_margin = (
                _ASR96_SCENARIO == "partial_bse_margin"
                and self.owner == "bse_market_margin@1"
            )
            missing_fields = {}
            if partial_margin:
                missing_fields = {
                    field: "mixed_quantity_unit"
                    for field in fields
                    if "QUANTITY" in field
                }
                fields = {
                    field: value
                    for field, value in fields.items()
                    if field not in missing_fields
                }
                units = {
                    field: value
                    for field, value in units.items()
                    if field not in missing_fields
                }
            partial_empty = (
                _ASR96_SCENARIO == "partial_empty_bse_margin"
                and self.owner == "bse_market_margin@1"
            )
            if partial_empty:
                fields, units = {}, {}
            return {
                "exchange": exchange,
                "asset_scope": scope,
                "report_date": request.report_date,
                "published_at": request.report_date + "T18:00:00+08:00",
                "retrieved_at": _ASR96_NOW.isoformat(),
                "source_fields": fields,
                "source_units": units,
                "query_complete": not (partial_margin or partial_empty),
                "unproved_fields": missing_fields,
                "complete_fields": list(fields),
                "row_count": 0
                if empty_margin or partial_empty
                else 2
                if self.owner == "sse_a_share_turnover@1"
                else 1,
            }

        return invocation.call(_asr96_Invocation(read_report))


def _asr96_build_foundation(*, transports=None, lanes=None):
    if transports is not None:
        raise ValueError("Issue 96 fixture supplies every owner transport.")
    return _asr96_real_foundation_builder(
        transports={owner: _Asr96ReportTransport(owner) for owner in _asr96_OWNER_IDS},
        lanes=lanes,
    )


_asr96_module.build_default_market_session_adapter = _asr96_build_session
_asr96_module.build_default_fixed_index_adapter = _asr96_build_indices
_asr96_module.build_default_market_foundation_operations = _asr96_build_foundation


if _asr96_os.environ.get("ASR96_LEGACY_SOURCES_UNAVAILABLE") == "1":
    import a_share_research.cli as _asr96_cli
    from a_share_research.identity_sources import HttpResponse as _asr96_HttpResponse
    from a_share_research.research_runtime import research as _asr96_research

    class _Asr96UnavailableTransport:
        def get(self, url, headers):
            del url, headers
            return _asr96_HttpResponse(
                status=503,
                content_type="application/json",
                body=b"{}",
                retrieved_at=_ASR96_NOW,
            )

        def post(self, url, headers, body):
            del body
            return self.get(url, headers)

    def _asr96_unavailable_research(request, **kwargs):
        del kwargs
        transport = _Asr96UnavailableTransport()
        return _asr96_research(
            request,
            identity_transport=transport,
            content_transport=transport,
            capital_transport=transport,
            market_signal_transport=transport,
            etf_option_transport=transport,
            intraday_transport=transport,
            research_now=_ASR96_NOW,
            available_optional_dependencies=(),
        )

    _asr96_cli.research = _asr96_unavailable_research
