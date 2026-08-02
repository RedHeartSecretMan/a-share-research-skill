#!/usr/bin/env python3
"""Offline process harness for the four versioned research workflows."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
REPOSITORY_ROOT = FIXTURES.parents[2]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(SCRIPTS))

from a_share_research.capital_contract import (  # noqa: E402
    CapitalQuery,
    CapitalSourceBatch,
)
from a_share_research.cli import main  # noqa: E402
from a_share_research.market_signal_contract import (  # noqa: E402
    MarketSignalQuery,
    SignalCoverage,
    SignalSourceBatch,
)
from a_share_research.report_sources import (  # noqa: E402
    EastmoneyReportOperation,
    IwencaiContentSearchOperation,
)

from tests.fixtures.valuation_research.fixture_cli import (  # noqa: E402
    ValuationFixtureTransport,
)
from tests.test_capital_events import observation as capital_observation  # noqa: E402
from tests.test_market_signals import observation as signal_observation  # noqa: E402
from tests.test_report_sources import (  # noqa: E402
    EastmoneyFixtureTransport,
    IwencaiFixtureTransport,
    no_wait_gate,
)

CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
RESEARCH_NOW = datetime(2026, 8, 2, 19, 40, tzinfo=CHINA_STANDARD_TIME)


class WorkflowCapitalOperation:
    """Return one contract-complete Industrial Fulian observation per leaf task."""

    operation_id = "fixed_workflow_capital@1"
    supported_data_types = frozenset(
        {"stock_fund_flow", "dragon_tiger", "lockup", "margin_trading"}
    )

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        dates = {
            "stock_fund_flow": "2026-07-31",
            "dragon_tiger": "2026-07-31",
            "lockup": "2026-08-20",
            "margin_trading": "2026-07-31",
        }
        observations = tuple(
            replace(
                capital_observation(
                    data_type=data_type,
                    source_role="market_observation",
                    observed_on=dates[data_type],
                    subject=query.subject,
                    limitations=(),
                ),
                source_operation=self.operation_id,
                dimensions={
                    "security_code": "601138",
                    "fixture_scope": "industrial_fulian_workflow",
                },
            )
            for data_type in query.data_types
            if data_type in self.supported_data_types
        )
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=observations,
            complete=True,
        )


class WorkflowBoardMembershipOperation:
    """Return one subject-bound board-membership market observation."""

    operation_id = "fixed_workflow_board_membership@1"
    supported_signal_types = frozenset({"security_board_membership"})

    def collect(self, query: MarketSignalQuery) -> SignalSourceBatch:
        item = replace(
            signal_observation(
                signal_type="security_board_membership",
                operation_id=self.operation_id,
                provider_security_code="601138",
            ),
            subject=query.subject,
            dimensions={
                "market_scope": "mainland_a_share",
                "provider_security_code": "601138",
                "board_name": "消费电子",
            },
            limitations=(),
        )
        return SignalSourceBatch(
            operation_id=self.operation_id,
            observations=(item,),
            coverage={
                "security_board_membership": SignalCoverage(
                    state="observed_nonempty",
                    provider_total=1,
                )
            },
        )


def _content_operations() -> list[object]:
    eastmoney = EastmoneyReportOperation(
        EastmoneyFixtureTransport(
            {
                1: "eastmoney_stock_page_1.json",
                2: "eastmoney_stock_page_2.json",
            }
        ),
        request_gate=no_wait_gate(),
    )
    iwencai = IwencaiContentSearchOperation(
        IwencaiFixtureTransport("iwencai_report_success.json"),
        credential_env="FIXTURE_IWENCAI_API_KEY",
        base_url_env="FIXTURE_IWENCAI_BASE_URL",
        trace_id_factory=lambda: "fixed-workflow-trace",
    )
    return [eastmoney, iwencai]


if __name__ == "__main__":
    raise SystemExit(
        main(
            sys.argv[1:],
            identity_transport=ValuationFixtureTransport(),
            research_now=RESEARCH_NOW,
            available_optional_dependencies=set(),
            content_operations=_content_operations(),
            capital_operations=[WorkflowCapitalOperation()],
            market_signal_operations=[WorkflowBoardMembershipOperation()],
        )
    )
