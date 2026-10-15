from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.a_share_universe import (  # noqa: E402
    UniverseContractError,
    build_as_of_a_share_universe,
)
from a_share_research.universe_input import (  # noqa: E402
    ExchangeListOutcome,
    ExchangeListRecord,
    IdentityBatchOutcome,
    UniverseIdentity,
    UniverseRequest,
)

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"


def _list_record(exchange: str, code: str, name: str) -> ExchangeListRecord:
    return ExchangeListRecord(
        exchange=exchange,
        code=code,
        name=name,
        security_category="A_SHARE",
        effective_at="2020-01-01",
        available_at="2026-08-20T16:00:00+08:00",
        retrieved_at="2026-08-21T10:00:00+08:00",
        provenance_uri=f"fixture-list:{exchange}:{code}",
    )


def _identity(exchange: str, code: str, name: str) -> UniverseIdentity:
    return UniverseIdentity(
        exchange=exchange,
        code=code,
        name=name,
        security_category="A_SHARE",
        listing_state="listed",
        effective_from="2020-01-01",
        effective_to=None,
        observed_at="2026-08-20T15:00:00+08:00",
        available_at="2026-08-20T16:00:00+08:00",
        retrieved_at="2026-08-21T10:00:00+08:00",
        provenance_uri=f"fixture-identity:{exchange}:{code}",
    )


@dataclass(frozen=True)
class _ListAdapter:
    exchange: str
    operation_id: str
    outcome: ExchangeListOutcome

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        return self.outcome


@dataclass(frozen=True)
class _IdentityAdapter:
    exchange: str
    operation_id: str
    outcome: IdentityBatchOutcome

    def resolve(
        self,
        request: object,
        listings: tuple[object, ...],
        timeout_seconds: float,
    ) -> IdentityBatchOutcome:
        return self.outcome


@dataclass(frozen=True)
class _RequestMutatingListAdapter:
    exchange: str
    operation_id: str
    outcome: ExchangeListOutcome

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        object.__setattr__(request, "screen_date", "2026-08-19")
        return self.outcome


@dataclass(frozen=True)
class _ListingMutatingIdentityAdapter:
    exchange: str
    operation_id: str
    outcome: IdentityBatchOutcome

    def resolve(
        self,
        request: object,
        listings: tuple[object, ...],
        timeout_seconds: float,
    ) -> IdentityBatchOutcome:
        object.__setattr__(listings[0], "name", "FORGED")
        return self.outcome


@dataclass(frozen=True)
class _TimeoutRecordingListAdapter:
    exchange: str
    operation_id: str
    outcome: ExchangeListOutcome
    seen: list[object]

    def acquire(self, request: object, timeout_seconds: float) -> ExchangeListOutcome:
        self.seen.append(timeout_seconds)
        return self.outcome


@dataclass(frozen=True)
class _TimeoutRecordingIdentityAdapter:
    exchange: str
    operation_id: str
    outcome: IdentityBatchOutcome
    seen: list[object]

    def resolve(
        self,
        request: object,
        listings: tuple[object, ...],
        timeout_seconds: float,
    ) -> IdentityBatchOutcome:
        self.seen.append(timeout_seconds)
        return self.outcome


def _complete_list_outcome(record: ExchangeListRecord) -> ExchangeListOutcome:
    return ExchangeListOutcome(
        operation_id=f"fixture_{record.exchange.lower()}_list@1",
        exchange=record.exchange,
        screen_date="2026-08-20",
        attempted=True,
        source_result="complete",
        expected_count=1,
        pages_expected=1,
        pages_received=1,
        records=[record],
    )


def _complete_identity_outcome(identity: UniverseIdentity) -> IdentityBatchOutcome:
    return IdentityBatchOutcome(
        operation_id=f"fixture_{identity.exchange.lower()}_identity@1",
        exchange=identity.exchange,
        attempted=True,
        source_result="complete",
        expected_count=1,
        records=[identity],
    )


def _fixture_rows() -> dict[str, ExchangeListRecord]:
    return {
        "SSE": _list_record("SSE", "600519", "贵州茅台"),
        "SZSE": _list_record("SZSE", "000001", "平安银行"),
        "BSE": _list_record("BSE", "920000", "北交样本"),
    }


def _build_fixture_universe(
    *,
    request: UniverseRequest | None = None,
    list_outcome_overrides: dict[str, ExchangeListOutcome] | None = None,
    identity_outcome_overrides: dict[str, IdentityBatchOutcome] | None = None,
    omit_list: frozenset[str] = frozenset(),
    omit_identity: frozenset[str] = frozenset(),
    list_adapter_overrides: dict[str, object] | None = None,
    identity_adapter_overrides: dict[str, object] | None = None,
) -> object:
    rows = _fixture_rows()
    list_outcomes = {
        exchange: _complete_list_outcome(record) for exchange, record in rows.items()
    }
    identity_outcomes = {
        exchange: _complete_identity_outcome(
            _identity(exchange, record.code, record.name)
        )
        for exchange, record in rows.items()
    }
    list_outcomes.update(list_outcome_overrides or {})
    identity_outcomes.update(identity_outcome_overrides or {})
    list_adapter_overrides = list_adapter_overrides or {}
    identity_adapter_overrides = identity_adapter_overrides or {}
    list_adapters = tuple(
        (
            list_adapter_overrides[exchange]
            if exchange in list_adapter_overrides
            else _ListAdapter(exchange, outcome.operation_id, outcome)
        )
        for exchange, outcome in list_outcomes.items()
        if exchange not in omit_list
    )
    identity_adapters = tuple(
        (
            identity_adapter_overrides[exchange]
            if exchange in identity_adapter_overrides
            else _IdentityAdapter(exchange, outcome.operation_id, outcome)
        )
        for exchange, outcome in identity_outcomes.items()
        if exchange not in omit_identity
    )
    return build_as_of_a_share_universe(
        request or UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
        research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
        exchange_list_adapters=list_adapters,  # type: ignore[arg-type]
        identity_adapters=identity_adapters,  # type: ignore[arg-type]
    )


class _CountingIdentityAdapter:
    def __init__(self, exchange: str, outcome: IdentityBatchOutcome) -> None:
        self.exchange = exchange
        self.operation_id = outcome.operation_id
        self.outcome = outcome
        self.calls = 0

    def resolve(
        self,
        request: object,
        listings: tuple[object, ...],
        timeout_seconds: float,
    ) -> IdentityBatchOutcome:
        self.calls += 1
        return self.outcome


class _StatefulRequest(UniverseRequest):
    def __init__(self) -> None:
        super().__init__("2026-08-20", "2026-08-20T23:59:59+08:00")
        object.__setattr__(self, "screen_reads", 0)

    def __getattribute__(self, name: str) -> object:
        if name == "screen_date":
            reads = object.__getattribute__(self, "screen_reads")
            object.__setattr__(self, "screen_reads", reads + 1)
            return "2026-08-20" if reads == 0 else "2026-08-22"
        return object.__getattribute__(self, name)


class _StatefulIdentityOutcome(IdentityBatchOutcome):
    def __init__(self, identity: UniverseIdentity) -> None:
        super().__init__(
            "fixture_sse_identity@1", "SSE", True, "complete", 1, [identity]
        )
        object.__setattr__(self, "operation_reads", 0)

    def __getattribute__(self, name: str) -> object:
        if name == "operation_id":
            reads = object.__getattribute__(self, "operation_reads")
            object.__setattr__(self, "operation_reads", reads + 1)
            return (
                "fixture_sse_identity@1"
                if reads == 0
                else "/private/tmp/provider-secret-token"
            )
        return object.__getattribute__(self, name)


class _StatefulOwner:
    def __init__(
        self,
        *,
        exchange: str,
        operation_id: str,
        outcome: ExchangeListOutcome | IdentityBatchOutcome,
        role: str,
        events: list[str],
        raw_list_records: list[ExchangeListRecord] | None = None,
    ) -> None:
        self._exchange = exchange
        self._operation_id = operation_id
        self._outcome = outcome
        self._role = role
        self._events = events
        self._raw_list_records = raw_list_records
        self._reads = {"exchange": 0, "operation_id": 0, role: 0}

    def __getattribute__(self, name: str) -> object:
        if name in {"exchange", "operation_id", "acquire", "resolve"}:
            values = object.__getattribute__(self, "_reads")
            role = object.__getattribute__(self, "_role")
            if name in {"acquire", "resolve"} and name != role:
                raise AttributeError(name)
            values[name] += 1
            events = object.__getattribute__(self, "_events")
            events.append(f"snapshot:{role}:{name}")
            if name == "exchange":
                return object.__getattribute__(self, "_exchange")
            if name == "operation_id":
                return object.__getattribute__(self, "_operation_id")
            return object.__getattribute__(self, "_invoke")
        return object.__getattribute__(self, name)

    def _invoke(self, *args: object) -> object:
        role = self._role
        self._events.append(f"call:{role}:{self._exchange}")
        request = args[0]
        if isinstance(request, UniverseRequest):
            raise AssertionError("raw request crossed the Adapter seam")
        if role == "resolve":
            listings = args[1]
            if type(listings) is not tuple:
                raise AssertionError("identity Adapter did not receive an exact tuple")
            with self.assert_frozen(listings[0]):
                pass
            if self._raw_list_records is not None:
                self._raw_list_records.append(
                    replace(self._raw_list_records[0], code="600001")
                )
        return self._outcome

    class assert_frozen:
        def __init__(self, value: object) -> None:
            self.value = value

        def __enter__(self) -> None:
            try:
                self.value.__setattr__("name", "changed")
            except FrozenInstanceError:
                return
            raise AssertionError("prepared listing was mutable")

        def __exit__(self, *args: object) -> None:
            return None

    @property
    def reads(self) -> dict[str, int]:
        return dict(self._reads)


class AShareUniverseInterfaceTests(unittest.TestCase):
    def test_three_exchange_projection_closes_with_first_class_bse_identity(
        self,
    ) -> None:
        rows = {
            "SSE": _list_record("SSE", "600519", "贵州茅台"),
            "SZSE": _list_record("SZSE", "000001", "平安银行"),
            "BSE": _list_record("BSE", "920000", "北交样本"),
        }
        list_adapters = tuple(
            _ListAdapter(
                exchange=exchange,
                operation_id=f"fixture_{exchange.lower()}_list@1",
                outcome=ExchangeListOutcome(
                    operation_id=f"fixture_{exchange.lower()}_list@1",
                    exchange=exchange,
                    screen_date="2026-08-20",
                    attempted=True,
                    source_result="complete",
                    expected_count=1,
                    pages_expected=1,
                    pages_received=1,
                    records=[row],
                ),
            )
            for exchange, row in rows.items()
        )
        identity_adapters = tuple(
            _IdentityAdapter(
                exchange=exchange,
                operation_id=f"fixture_{exchange.lower()}_identity@1",
                outcome=IdentityBatchOutcome(
                    operation_id=f"fixture_{exchange.lower()}_identity@1",
                    exchange=exchange,
                    attempted=True,
                    source_result="complete",
                    expected_count=1,
                    records=[_identity(exchange, row.code, row.name)],
                ),
            )
            for exchange, row in rows.items()
        )

        result = build_as_of_a_share_universe(
            UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
            research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
            exchange_list_adapters=list_adapters,
            identity_adapters=identity_adapters,
        ).to_public_dict()

        self.assertTrue(result["full_a_share_universe"])
        self.assertEqual(list(result["partitions"]), ["SSE", "SZSE", "BSE"])
        self.assertEqual(
            result["partitions"]["BSE"]["securities"][0]["canonical_security_id"],
            "BSE:920000",
        )
        for exchange in ("SSE", "SZSE", "BSE"):
            with self.subTest(exchange=exchange):
                self.assertEqual(
                    result["partitions"][exchange]["reconciliation"],
                    {"expected": 1, "resolved": 1, "failed": 0},
                )

    def test_preparation_and_owner_snapshots_precede_calls_without_rereads(
        self,
    ) -> None:
        events: list[str] = []
        rows = _fixture_rows()
        raw_records = {exchange: [row] for exchange, row in rows.items()}
        list_owners: list[_StatefulOwner] = []
        identity_owners: list[_StatefulOwner] = []
        for exchange, row in rows.items():
            list_operation = f"fixture_{exchange.lower()}_list@1"
            identity_operation = f"fixture_{exchange.lower()}_identity@1"
            list_owners.append(
                _StatefulOwner(
                    exchange=exchange,
                    operation_id=list_operation,
                    outcome=ExchangeListOutcome(
                        list_operation,
                        exchange,
                        "2026-08-20",
                        True,
                        "complete",
                        1,
                        1,
                        1,
                        raw_records[exchange],
                    ),
                    role="acquire",
                    events=events,
                )
            )
            identity_owners.append(
                _StatefulOwner(
                    exchange=exchange,
                    operation_id=identity_operation,
                    outcome=_complete_identity_outcome(
                        _identity(exchange, row.code, row.name)
                    ),
                    role="resolve",
                    events=events,
                    raw_list_records=raw_records[exchange],
                )
            )

        result = build_as_of_a_share_universe(
            UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
            research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
            exchange_list_adapters=tuple(list_owners),  # type: ignore[arg-type]
            identity_adapters=tuple(identity_owners),  # type: ignore[arg-type]
        ).to_public_dict()

        first_call = next(
            index for index, event in enumerate(events) if event.startswith("call:")
        )
        self.assertEqual(first_call, 18)
        self.assertEqual(
            [event for event in events if event.startswith("call:")],
            [
                "call:acquire:SSE",
                "call:resolve:SSE",
                "call:acquire:SZSE",
                "call:resolve:SZSE",
                "call:acquire:BSE",
                "call:resolve:BSE",
            ],
        )
        for owner in (*list_owners, *identity_owners):
            role = owner._role
            self.assertEqual(
                owner.reads,
                {"exchange": 1, "operation_id": 1, role: 1},
            )
        self.assertTrue(result["full_a_share_universe"])
        self.assertEqual(
            result["partitions"]["SSE"]["reconciliation"],
            {"expected": 1, "resolved": 1, "failed": 0},
        )

    def test_explicit_exchange_identity_never_uses_code_prefix_inference(self) -> None:
        rows = {
            "SSE": _list_record("SSE", "000001", "上交样本"),
            "SZSE": _list_record("SZSE", "600000", "深交样本"),
            "BSE": _list_record("BSE", "300001", "北交样本"),
        }
        list_outcomes = {
            exchange: _complete_list_outcome(row) for exchange, row in rows.items()
        }
        identity_outcomes = {
            exchange: _complete_identity_outcome(
                _identity(exchange, row.code, row.name)
            )
            for exchange, row in rows.items()
        }

        result = _build_fixture_universe(
            list_outcome_overrides=list_outcomes,
            identity_outcome_overrides=identity_outcomes,
        ).to_public_dict()

        self.assertEqual(
            [
                result["partitions"][exchange]["securities"][0]["canonical_security_id"]
                for exchange in ("SSE", "SZSE", "BSE")
            ],
            ["SSE:000001", "SZSE:600000", "BSE:300001"],
        )

    def test_unknown_exchange_total_retains_partial_records_and_null_counts(
        self,
    ) -> None:
        row = _fixture_rows()["BSE"]
        records = [row]
        partial = replace(
            _complete_list_outcome(row),
            source_result="partial",
            expected_count=None,
            records=records,
        )
        identity_spy = _CountingIdentityAdapter(
            "BSE", _complete_identity_outcome(_identity("BSE", row.code, row.name))
        )

        universe = _build_fixture_universe(
            list_outcome_overrides={"BSE": partial},
            identity_adapter_overrides={"BSE": identity_spy},
        )
        records.clear()
        result = universe.to_public_dict()
        partition = result["partitions"]["BSE"]

        self.assertEqual(identity_spy.calls, 0)
        self.assertEqual(
            partition["reconciliation"],
            {"expected": None, "resolved": 0, "failed": None},
        )
        self.assertFalse(partition["complete"])
        self.assertFalse(result["full_a_share_universe"])
        self.assertTrue(result["partitions"]["SSE"]["complete"])
        self.assertTrue(result["partitions"]["SZSE"]["complete"])
        self.assertEqual(partition["limitation_codes"], ["exchange_list_total_unknown"])
        self.assertEqual(
            partition["source_outcomes"]["exchange_list"],
            {
                "operation_id": "fixture_bse_list@1",
                "attempted": True,
                "source_result": "partial",
                "expected_count": None,
                "observed_count": 1,
                "pages_expected": 1,
                "pages_received": 1,
            },
        )
        self.assertIsNone(partition["source_outcomes"]["identity"])
        security = partition["securities"][0]
        self.assertEqual(security["canonical_security_id"], "BSE:920000")
        self.assertEqual(
            security["unranked_reasons"],
            ["listing_state_unproven", "as_of_unproven", "source_failed"],
        )
        self.assertEqual(
            security["provenance"]["exchange_list"]["uri"], "fixture-list:BSE:920000"
        )
        partition["reconciliation"]["expected"] = 0
        partition["securities"].clear()
        fresh = universe.to_public_dict()["partitions"]["BSE"]
        self.assertIsNone(fresh["reconciliation"]["expected"])
        self.assertEqual(len(fresh["securities"]), 1)

    def test_unknown_page_total_keeps_actual_pages_and_partial_records(self) -> None:
        row = _fixture_rows()["BSE"]
        for expected in (None, 1):
            partial = replace(
                _complete_list_outcome(row),
                source_result="partial",
                expected_count=expected,
                pages_expected=None,
            )
            with self.subTest(expected=expected):
                result = _build_fixture_universe(
                    list_outcome_overrides={"BSE": partial}
                ).to_public_dict()
                partition = result["partitions"]["BSE"]
                source = partition["source_outcomes"]["exchange_list"]
                self.assertEqual(
                    partition["reconciliation"],
                    {"expected": expected, "resolved": 0, "failed": expected},
                )
                self.assertIn("pages_expected", source)
                self.assertIsNone(source["pages_expected"])
                self.assertEqual(source["pages_received"], 1)
                self.assertEqual(source["observed_count"], 1)
                self.assertEqual(partition["securities"][0]["code"], "920000")
                self.assertFalse(partition["complete"])
                self.assertFalse(result["full_a_share_universe"])

    def test_unknown_total_preserves_canonical_record_order_in_each_exchange(
        self,
    ) -> None:
        for exchange, row in _fixture_rows().items():
            other = replace(row, code="000000", name="另一成员")
            partial = replace(
                _complete_list_outcome(row),
                source_result="partial",
                expected_count=None,
                records=[row, other],
            )
            with self.subTest(exchange=exchange):
                result = _build_fixture_universe(
                    list_outcome_overrides={exchange: partial}
                ).to_public_dict()
                partition = result["partitions"][exchange]
                self.assertEqual(
                    [item["code"] for item in partition["securities"]],
                    ["000000", row.code],
                )
                self.assertIsNone(partition["reconciliation"]["expected"])
                self.assertEqual(
                    partition["source_outcomes"]["exchange_list"]["observed_count"], 2
                )
                self.assertFalse(result["full_a_share_universe"])

    def test_partial_list_retains_observed_record_and_skips_identity(self) -> None:
        row = _fixture_rows()["SSE"]
        partial = ExchangeListOutcome(
            "fixture_sse_list@1",
            "SSE",
            "2026-08-20",
            True,
            "partial",
            2,
            2,
            1,
            [row],
        )
        identity_spy = _CountingIdentityAdapter(
            "SSE", _complete_identity_outcome(_identity("SSE", row.code, row.name))
        )

        result = _build_fixture_universe(
            list_outcome_overrides={"SSE": partial},
            identity_adapter_overrides={"SSE": identity_spy},
        ).to_public_dict()

        partition = result["partitions"]["SSE"]
        self.assertEqual(identity_spy.calls, 0)
        self.assertEqual(
            partition["reconciliation"], {"expected": 2, "resolved": 0, "failed": 2}
        )
        self.assertEqual(partition["source_result"], "partial")
        self.assertTrue(partition["attempted"])
        self.assertEqual(partition["limitation_codes"], ["incomplete_pagination"])
        self.assertEqual(len(partition["securities"]), 1)
        self.assertEqual(
            partition["securities"][0]["unranked_reasons"],
            ["listing_state_unproven", "as_of_unproven", "source_failed"],
        )
        self.assertEqual(
            partition["source_outcomes"]["exchange_list"],
            {
                "operation_id": "fixture_sse_list@1",
                "attempted": True,
                "source_result": "partial",
                "expected_count": 2,
                "observed_count": 1,
                "pages_expected": 2,
                "pages_received": 1,
            },
        )
        self.assertIsNone(partition["source_outcomes"]["identity"])
        self.assertFalse(result["full_a_share_universe"])

    def test_partial_identity_retains_resolved_subset_and_denominator(self) -> None:
        first = _fixture_rows()["SSE"]
        second = replace(first, code="600000", name="浦发银行")
        list_outcome = ExchangeListOutcome(
            "fixture_sse_list@1",
            "SSE",
            "2026-08-20",
            True,
            "complete",
            2,
            1,
            1,
            [first, second],
        )
        identity_outcome = IdentityBatchOutcome(
            "fixture_sse_identity@1",
            "SSE",
            True,
            "partial",
            2,
            [_identity("SSE", first.code, first.name)],
        )

        result = _build_fixture_universe(
            list_outcome_overrides={"SSE": list_outcome},
            identity_outcome_overrides={"SSE": identity_outcome},
        ).to_public_dict()

        partition = result["partitions"]["SSE"]
        self.assertEqual(
            partition["reconciliation"], {"expected": 2, "resolved": 1, "failed": 1}
        )
        self.assertEqual(partition["source_result"], "partial")
        self.assertEqual(partition["limitation_codes"], ["identity_incomplete"])
        self.assertEqual(
            [item["canonical_security_id"] for item in partition["securities"]],
            ["SSE:600000", "SSE:600519"],
        )
        unresolved = partition["securities"][0]
        self.assertEqual(
            unresolved["unranked_reasons"],
            ["listing_state_unproven", "as_of_unproven", "source_failed"],
        )
        self.assertEqual(
            partition["source_outcomes"]["identity"]["source_result"], "partial"
        )

    def test_missing_owners_preserve_unknown_list_and_known_identity_counts(
        self,
    ) -> None:
        missing_list = _build_fixture_universe(
            omit_list=frozenset({"BSE"})
        ).to_public_dict()
        missing_identity = _build_fixture_universe(
            omit_identity=frozenset({"BSE"})
        ).to_public_dict()

        list_partition = missing_list["partitions"]["BSE"]
        self.assertEqual(
            list_partition["reconciliation"],
            {"expected": None, "resolved": 0, "failed": None},
        )
        self.assertEqual(
            list_partition["limitation_codes"], ["missing_exchange_list_adapter"]
        )
        identity_partition = missing_identity["partitions"]["BSE"]
        self.assertEqual(
            identity_partition["reconciliation"],
            {"expected": 1, "resolved": 0, "failed": 1},
        )
        self.assertEqual(
            identity_partition["limitation_codes"], ["missing_identity_adapter"]
        )
        self.assertFalse(missing_list["full_a_share_universe"])
        self.assertFalse(missing_identity["full_a_share_universe"])

    def test_blocked_and_unavailable_list_facts_are_preserved_exactly(self) -> None:
        for attempted in (False, True):
            for source_result in ("credential_blocked", "unavailable"):
                for expected in (0, None):
                    with self.subTest(
                        attempted=attempted,
                        source_result=source_result,
                        expected=expected,
                    ):
                        outcome = ExchangeListOutcome(
                            "fixture_sse_list@1",
                            "SSE",
                            "2026-08-20",
                            attempted,
                            source_result,
                            expected,
                            1,
                            0,
                            [],
                        )
                        result = _build_fixture_universe(
                            list_outcome_overrides={"SSE": outcome}
                        ).to_public_dict()
                        partition = result["partitions"]["SSE"]
                        self.assertEqual(partition["attempted"], attempted)
                        self.assertEqual(partition["source_result"], source_result)
                        self.assertEqual(
                            partition["reconciliation"],
                            {"expected": expected, "resolved": 0, "failed": expected},
                        )
                        self.assertFalse(partition["complete"])

    def test_observed_empty_exchange_partition_is_closed_not_missing(self) -> None:
        empty_list = ExchangeListOutcome(
            "fixture_bse_list@1",
            "BSE",
            "2026-08-20",
            True,
            "observed_empty",
            0,
            1,
            1,
            [],
        )
        empty_identity = IdentityBatchOutcome(
            "fixture_bse_identity@1",
            "BSE",
            True,
            "observed_empty",
            0,
            [],
        )

        result = _build_fixture_universe(
            list_outcome_overrides={"BSE": empty_list},
            identity_outcome_overrides={"BSE": empty_identity},
        ).to_public_dict()

        self.assertTrue(result["partitions"]["BSE"]["complete"])
        self.assertEqual(
            result["partitions"]["BSE"]["reconciliation"],
            {"expected": 0, "resolved": 0, "failed": 0},
        )
        self.assertTrue(result["full_a_share_universe"])

    def test_interval_boundaries_unproven_reasons_and_five_states_are_exact(
        self,
    ) -> None:
        base = _identity("SSE", "600519", "贵州茅台")
        cases = (
            (replace(base, effective_from="2026-08-20"), "expected_universe", []),
            (replace(base, effective_to="2026-08-20"), "expected_universe", []),
            (
                replace(base, effective_from="2026-08-21"),
                "excluded_universe",
                ["outside_listing_interval"],
            ),
            (
                replace(base, effective_to="2026-08-19"),
                "excluded_universe",
                ["outside_listing_interval"],
            ),
            (
                replace(base, listing_state=None),
                "expected_universe",
                ["listing_state_unproven"],
            ),
            (
                replace(base, effective_from=None),
                "expected_universe",
                ["as_of_unproven"],
            ),
            (
                replace(
                    base,
                    effective_from="2026-08-21",
                    available_at="2026-08-21T09:00:00+08:00",
                ),
                "expected_universe",
                ["as_of_unproven"],
            ),
        )
        for identity, expected_state, expected_reasons in cases:
            with self.subTest(identity=identity):
                result = _build_fixture_universe(
                    identity_outcome_overrides={
                        "SSE": _complete_identity_outcome(identity)
                    }
                ).to_public_dict()
                security = result["partitions"]["SSE"]["securities"][0]
                self.assertEqual(security["universe_state"], expected_state)
                self.assertEqual(security["unranked_reasons"], expected_reasons)

        for state in (
            "listed",
            "paused_listing",
            "terminated_listing",
            "trading_halt",
            "current",
        ):
            with self.subTest(state=state):
                identity = replace(base, listing_state=state)
                result = _build_fixture_universe(
                    identity_outcome_overrides={
                        "SSE": _complete_identity_outcome(identity)
                    }
                ).to_public_dict()
                security = result["partitions"]["SSE"]["securities"][0]
                self.assertEqual(security["listing_state"], state)
                self.assertEqual(security["unranked_reasons"], [])

    def test_unproven_expected_identity_preserves_facts_but_never_completes(
        self,
    ) -> None:
        base = _identity("SSE", "600519", "贵州茅台")
        cases = (
            (replace(base, listing_state=None), ["listing_state_unproven"]),
            (replace(base, effective_from=None), ["as_of_unproven"]),
            (
                replace(
                    base,
                    effective_from="2026-08-21",
                    available_at="2026-08-21T09:00:00+08:00",
                ),
                ["as_of_unproven"],
            ),
        )

        for identity, reasons in cases:
            with self.subTest(identity=identity):
                result = _build_fixture_universe(
                    identity_outcome_overrides={
                        "SSE": _complete_identity_outcome(identity)
                    }
                ).to_public_dict()
                partition = result["partitions"]["SSE"]

                self.assertFalse(partition["complete"])
                self.assertFalse(result["full_a_share_universe"])
                self.assertEqual(partition["source_result"], "complete")
                self.assertEqual(
                    partition["reconciliation"],
                    {"expected": 1, "resolved": 1, "failed": 0},
                )
                self.assertEqual(partition["limitation_codes"], ["identity_incomplete"])
                self.assertEqual(len(partition["securities"]), 1)
                self.assertEqual(
                    partition["securities"][0]["unranked_reasons"], reasons
                )
                self.assertEqual(
                    partition["source_outcomes"]["identity"],
                    {
                        "operation_id": "fixture_sse_identity@1",
                        "attempted": True,
                        "source_result": "complete",
                        "expected_count": 1,
                        "observed_count": 1,
                    },
                )

    def test_adapter_request_mutation_cannot_change_authoritative_screen_date(
        self,
    ) -> None:
        row = _fixture_rows()["SSE"]
        outcome = _complete_list_outcome(row)
        result = _build_fixture_universe(
            list_adapter_overrides={
                "SSE": _RequestMutatingListAdapter("SSE", outcome.operation_id, outcome)
            }
        ).to_public_dict()

        self.assertEqual(result["screen_date"], "2026-08-20")
        self.assertTrue(result["partitions"]["SSE"]["complete"])

    def test_adapter_listing_mutation_cannot_change_name_or_denominator(
        self,
    ) -> None:
        row = _fixture_rows()["SSE"]
        outcome = _complete_identity_outcome(_identity("SSE", row.code, row.name))
        result = _build_fixture_universe(
            identity_adapter_overrides={
                "SSE": _ListingMutatingIdentityAdapter(
                    "SSE", outcome.operation_id, outcome
                )
            }
        ).to_public_dict()
        partition = result["partitions"]["SSE"]

        self.assertEqual(partition["securities"][0]["name"], "贵州茅台")
        self.assertEqual(
            partition["reconciliation"],
            {"expected": 1, "resolved": 1, "failed": 0},
        )
        self.assertTrue(partition["complete"])

    def test_conflict_table_fails_closed_with_stable_sanitized_errors(self) -> None:
        row = _fixture_rows()["SSE"]
        base_identity = _identity("SSE", row.code, row.name)
        cases = (
            (
                "historical_list_scope_mismatch",
                {"SSE": replace(_complete_list_outcome(row), screen_date="2026-08-21")},
                None,
            ),
            (
                "historical_list_as_of_unproven",
                {
                    "SSE": _complete_list_outcome(
                        replace(row, available_at="2026-08-21T09:00:00+08:00")
                    )
                },
                None,
            ),
            (
                "comparable_identity_conflict",
                None,
                {
                    "SSE": _complete_identity_outcome(
                        replace(base_identity, name="冲突名称")
                    )
                },
            ),
            (
                "unexpected_resolved_identity",
                None,
                {
                    "SSE": _complete_identity_outcome(
                        replace(base_identity, code="600000")
                    )
                },
            ),
            (
                "invalid_research_time",
                None,
                None,
            ),
        )
        for code, list_overrides, identity_overrides in cases:
            request = (
                UniverseRequest("2026-08-21", "2026-08-20T23:59:59+08:00")
                if code == "invalid_research_time"
                else None
            )
            with self.subTest(code=code):
                with self.assertRaises(UniverseContractError) as caught:
                    _build_fixture_universe(
                        request=request,
                        list_outcome_overrides=list_overrides,
                        identity_outcome_overrides=identity_overrides,
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_adapter_and_preparation_failures_do_not_leak_raw_details(self) -> None:
        secret = "RAW_SECRET:/private/tmp/provider-secret-token"

        class RaisingCall:
            exchange = "SSE"
            operation_id = "fixture_sse_list@1"

            def acquire(self, request: object, timeout_seconds: float) -> object:
                raise RuntimeError(secret)

        class RaisingMetadata:
            operation_id = "fixture_sse_list@1"

            @property
            def exchange(self) -> str:
                raise RuntimeError(secret)

            def acquire(self, request: object, timeout_seconds: float) -> object:
                raise AssertionError

        invalid_raw = _ListAdapter(
            "SSE",
            "fixture_sse_list@1",
            replace(
                _complete_list_outcome(_fixture_rows()["SSE"]), operation_id=secret
            ),
        )
        cases = (
            (RaisingCall(), "adapter_invocation_failed"),
            (RaisingMetadata(), "adapter_snapshot_failed"),
            (invalid_raw, "invalid_operation_identity"),
        )
        for owner, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(UniverseContractError) as caught:
                    _build_fixture_universe(list_adapter_overrides={"SSE": owner})
                self.assertEqual(caught.exception.code, expected_code)
                self.assertNotIn(secret, str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_stateful_request_and_identity_outcome_are_never_reread(self) -> None:
        request = _StatefulRequest()
        stateful_outcome = _StatefulIdentityOutcome(
            _identity("SSE", "600519", "贵州茅台")
        )

        result = _build_fixture_universe(
            request=request,
            identity_outcome_overrides={"SSE": stateful_outcome},
            identity_adapter_overrides={
                "SSE": _IdentityAdapter(
                    "SSE", "fixture_sse_identity@1", stateful_outcome
                )
            },
        )
        first = result.to_public_dict()
        first["partitions"]["SSE"]["securities"][0]["name"] = "changed"
        second = result.to_public_dict()

        self.assertEqual(request.screen_reads, 1)
        self.assertEqual(stateful_outcome.operation_reads, 1)
        self.assertEqual(second["screen_date"], "2026-08-20")
        self.assertEqual(
            second["partitions"]["SSE"]["securities"][0]["name"], "贵州茅台"
        )
        self.assertEqual(
            second["partitions"]["SSE"]["source_outcomes"]["identity"]["operation_id"],
            "fixture_sse_identity@1",
        )

    def test_prepared_boundary_closes_input_type_time_page_and_provenance_table(
        self,
    ) -> None:
        row = _fixture_rows()["SSE"]
        duplicate = _list_record("SSE", "600519", "贵州茅台")
        cases = (
            (
                "duplicate_canonical_identity",
                ExchangeListOutcome(
                    "fixture_sse_list@1",
                    "SSE",
                    "2026-08-20",
                    True,
                    "complete",
                    2,
                    1,
                    1,
                    [duplicate, duplicate],
                ),
            ),
            (
                "cross_exchange_identity_conflict",
                _complete_list_outcome(replace(row, exchange="SZSE")),
            ),
            (
                "non_a_share_contamination",
                _complete_list_outcome(replace(row, security_category="B_SHARE")),
            ),
            (
                "missing_effective_time",
                _complete_list_outcome(replace(row, effective_at="not-a-time")),
            ),
            (
                "historical_list_as_of_unproven",
                _complete_list_outcome(
                    replace(row, available_at="2026-08-21T09:00:00+08:00")
                ),
            ),
            (
                "invalid_reconciliation",
                replace(_complete_list_outcome(row), pages_received=2),
            ),
            (
                "source_result_conflict",
                replace(
                    _complete_list_outcome(row),
                    attempted=False,
                    source_result="complete",
                ),
            ),
            (
                "invalid_security_name",
                _complete_list_outcome(replace(row, name="/private/tmp/name")),
            ),
            (
                "invalid_provenance",
                _complete_list_outcome(
                    replace(row, provenance_uri="/private/tmp/provider-token")
                ),
            ),
        )
        for expected_code, outcome in cases:
            for unknown_total in (False, True):
                candidate = (
                    replace(outcome, expected_count=None, source_result="partial")
                    if unknown_total
                    else outcome
                )
                with self.subTest(
                    expected_code=expected_code, unknown_total=unknown_total
                ):
                    with self.assertRaises(UniverseContractError) as caught:
                        _build_fixture_universe(
                            list_outcome_overrides={"SSE": candidate}
                        )
                    self.assertEqual(caught.exception.code, expected_code)
                    self.assertNotIn("/private/tmp", str(caught.exception))
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)

    def test_identity_blocked_and_unavailable_facts_preserve_known_denominator(
        self,
    ) -> None:
        for attempted in (False, True):
            for source_result in ("credential_blocked", "unavailable"):
                with self.subTest(attempted=attempted, source_result=source_result):
                    outcome = IdentityBatchOutcome(
                        "fixture_sse_identity@1",
                        "SSE",
                        attempted,
                        source_result,
                        1,
                        [],
                    )
                    result = _build_fixture_universe(
                        identity_outcome_overrides={"SSE": outcome}
                    ).to_public_dict()
                    partition = result["partitions"]["SSE"]
                    self.assertEqual(partition["attempted"], attempted)
                    self.assertEqual(partition["source_result"], source_result)
                    self.assertEqual(
                        partition["reconciliation"],
                        {"expected": 1, "resolved": 0, "failed": 1},
                    )
                    self.assertEqual(
                        partition["source_outcomes"]["exchange_list"]["source_result"],
                        "complete",
                    )
                    self.assertEqual(
                        partition["source_outcomes"]["identity"]["source_result"],
                        source_result,
                    )
                    self.assertFalse(result["full_a_share_universe"])

    def test_normalized_times_and_safe_provenance_round_trip_exactly(self) -> None:
        row = replace(
            _fixture_rows()["SSE"],
            effective_at="2026-08-19T16:30:00+00:00",
            available_at="2026-08-20T08:00:00+00:00",
            retrieved_at="2026-08-21T02:00:00+00:00",
        )
        identity = replace(
            _identity("SSE", row.code, row.name),
            observed_at="2026-08-20T07:00:00+00:00",
            available_at="2026-08-20T08:00:00+00:00",
            retrieved_at="2026-08-21T02:00:00+00:00",
        )

        result = _build_fixture_universe(
            list_outcome_overrides={"SSE": _complete_list_outcome(row)},
            identity_outcome_overrides={"SSE": _complete_identity_outcome(identity)},
        ).to_public_dict()
        provenance = result["partitions"]["SSE"]["securities"][0]["provenance"]

        self.assertEqual(
            provenance["exchange_list"],
            {
                "operation_id": "fixture_sse_list@1",
                "uri": "fixture-list:SSE:600519",
                "effective_at": "2026-08-20T00:30:00+08:00",
                "available_at": "2026-08-20T16:00:00+08:00",
                "retrieved_at": "2026-08-21T10:00:00+08:00",
            },
        )
        self.assertEqual(
            provenance["identity"]["observed_at"], "2026-08-20T15:00:00+08:00"
        )

    def test_adapter_identity_and_timeout_table_fails_before_any_call(self) -> None:
        row = _fixture_rows()["SSE"]
        base = _ListAdapter("SSE", "fixture_sse_list@1", _complete_list_outcome(row))
        invalid_owners = (
            replace(base, exchange="UNKNOWN"),
            replace(base, operation_id="/private/tmp/provider-token"),
        )
        for owner in invalid_owners:
            with self.subTest(owner=owner):
                with self.assertRaises(UniverseContractError) as caught:
                    _build_fixture_universe(list_adapter_overrides={"SSE": owner})
                self.assertEqual(caught.exception.code, "invalid_adapter_identity")
                self.assertNotIn("/private/tmp", str(caught.exception))

        rows = _fixture_rows()
        list_adapters = tuple(
            _ListAdapter(exchange, outcome.operation_id, outcome)
            for exchange, outcome in (
                (exchange, _complete_list_outcome(record))
                for exchange, record in rows.items()
            )
        )
        identity_adapters = tuple(
            _IdentityAdapter(
                exchange,
                outcome.operation_id,
                outcome,
            )
            for exchange, outcome in (
                (
                    exchange,
                    _complete_identity_outcome(
                        _identity(exchange, record.code, record.name)
                    ),
                )
                for exchange, record in rows.items()
            )
        )
        for timeout in (True, 0, -1, float("inf"), float("nan"), "20"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(UniverseContractError) as caught:
                    build_as_of_a_share_universe(
                        UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
                        research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
                        exchange_list_adapters=list_adapters,
                        identity_adapters=identity_adapters,
                        timeout_seconds=timeout,  # type: ignore[arg-type]
                    )
                self.assertEqual(caught.exception.code, "invalid_timeout")

    def test_huge_positive_integer_timeout_is_forwarded_exactly(self) -> None:
        rows = _fixture_rows()
        seen: list[object] = []
        list_adapters = tuple(
            _TimeoutRecordingListAdapter(
                exchange,
                outcome.operation_id,
                outcome,
                seen,
            )
            for exchange, outcome in (
                (exchange, _complete_list_outcome(record))
                for exchange, record in rows.items()
            )
        )
        identity_adapters = tuple(
            _TimeoutRecordingIdentityAdapter(
                exchange,
                outcome.operation_id,
                outcome,
                seen,
            )
            for exchange, outcome in (
                (
                    exchange,
                    _complete_identity_outcome(
                        _identity(exchange, record.code, record.name)
                    ),
                )
                for exchange, record in rows.items()
            )
        )
        timeout = 10**1000

        result = build_as_of_a_share_universe(
            UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
            research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
            exchange_list_adapters=list_adapters,
            identity_adapters=identity_adapters,
            timeout_seconds=timeout,
        ).to_public_dict()

        self.assertTrue(result["full_a_share_universe"])
        self.assertEqual(len(seen), 6)
        self.assertTrue(all(value is timeout for value in seen))


class AShareUniverseInstalledCopyTests(unittest.TestCase):
    def test_copied_skill_retains_unknown_total_as_null_in_public_json(self) -> None:
        script = """
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from a_share_research.a_share_universe import build_as_of_a_share_universe
from a_share_research.universe_input import ExchangeListOutcome, ExchangeListRecord, UniverseRequest

class PartialList:
    exchange = "BSE"
    operation_id = "fixture_bse_list@1"

    def acquire(self, request, timeout_seconds):
        row = ExchangeListRecord(
            "BSE", "920000", "北交样本", "A_SHARE", "2020-01-01",
            "2026-08-20T16:00:00+08:00", "2026-08-21T10:00:00+08:00",
            "fixture-list:BSE:920000",
        )
        return ExchangeListOutcome(
            self.operation_id, "BSE", request.screen_date,
            True, "partial", None, None, 1, [row],
        )

result = build_as_of_a_share_universe(
    UniverseRequest("2026-08-20", "2026-08-20T23:59:59+08:00"),
    research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    exchange_list_adapters=(PartialList(),), identity_adapters=(),
)
print(json.dumps(result.to_public_dict()))
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            installed = Path(temporary_directory) / "a-share-research"
            shutil.copytree(SKILL_ROOT, installed)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(installed / "scripts")
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertFalse(result["full_a_share_universe"])
        partition = result["partitions"]["BSE"]
        self.assertEqual(
            partition["reconciliation"],
            {"expected": None, "resolved": 0, "failed": None},
        )
        self.assertIsNone(
            partition["source_outcomes"]["exchange_list"]["expected_count"]
        )
        source = partition["source_outcomes"]["exchange_list"]
        self.assertIn("pages_expected", source)
        self.assertIsNone(source["pages_expected"])
        self.assertEqual(source["pages_received"], 1)
        self.assertEqual(
            partition["source_outcomes"]["exchange_list"]["observed_count"], 1
        )
        self.assertEqual(
            partition["securities"][0]["canonical_security_id"], "BSE:920000"
        )

    def test_copied_skill_exports_the_universe_projection_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            installed = Path(temporary_directory) / "a-share-research"
            shutil.copytree(SKILL_ROOT, installed)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(installed / "scripts")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from a_share_research.a_share_universe import "
                        "build_as_of_a_share_universe; "
                        "print(build_as_of_a_share_universe.__name__)"
                    ),
                ],
                cwd=temporary_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "build_as_of_a_share_universe\n")


if __name__ == "__main__":
    unittest.main()
