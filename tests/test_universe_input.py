from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

import a_share_research.universe_input as universe_input_module  # noqa: E402
from a_share_research.universe_input import (  # noqa: E402
    ExchangeListOutcome,
    ExchangeListRecord,
    IdentityBatchOutcome,
    UniverseIdentity,
    UniverseRequest,
    prepare_exchange_list_outcome,
    prepare_identity_outcome,
    prepare_universe_request,
)

ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"


class _StatefulRequest(UniverseRequest):
    def __init__(self) -> None:
        super().__init__(
            screen_date="2026-08-20",
            information_cutoff="2026-08-21T10:00:00+08:00",
        )
        object.__setattr__(self, "screen_reads", 0)

    def __getattribute__(self, name: str) -> object:
        if name == "screen_date":
            reads = object.__getattribute__(self, "screen_reads")
            object.__setattr__(self, "screen_reads", reads + 1)
            return "2026-08-20" if reads == 0 else "2026-08-22"
        return object.__getattribute__(self, name)


def _list_record(code: str = "600000") -> ExchangeListRecord:
    return ExchangeListRecord(
        exchange="SSE",
        code=code,
        name="浦发银行",
        security_category="A_SHARE",
        effective_at="1999-11-10",
        available_at="2026-08-20T09:00:00+08:00",
        retrieved_at="2026-08-21T10:00:00+08:00",
        provenance_uri="sse-list:2026-08-20",
    )


class _StatefulListOutcome(ExchangeListOutcome):
    def __init__(self, records: list[ExchangeListRecord]) -> None:
        super().__init__(
            operation_id="sse_stock_list@1",
            exchange="SSE",
            screen_date="2026-08-20",
            attempted=True,
            source_result="complete",
            expected_count=1,
            pages_expected=1,
            pages_received=1,
            records=records,
        )
        object.__setattr__(self, "expected_reads", 0)

    def __getattribute__(self, name: str) -> object:
        if name == "expected_count":
            reads = object.__getattribute__(self, "expected_reads")
            object.__setattr__(self, "expected_reads", reads + 1)
            return 1 if reads == 0 else 2
        return object.__getattribute__(self, name)


def _identity(
    code: str = "600000", listing_state: str | None = "listed"
) -> UniverseIdentity:
    return UniverseIdentity(
        exchange="SSE",
        code=code,
        name="浦发银行",
        security_category="A_SHARE",
        listing_state=listing_state,
        effective_from="1999-11-10",
        effective_to=None,
        observed_at="2026-08-20T15:00:00+08:00",
        available_at="2026-08-20T15:30:00+08:00",
        retrieved_at="2026-08-21T10:00:00+08:00",
        provenance_uri="sse-identity:2026-08-20",
    )


class _StatefulIdentityOutcome(IdentityBatchOutcome):
    def __init__(self, records: list[UniverseIdentity]) -> None:
        super().__init__(
            operation_id="sse_identity@1",
            exchange="SSE",
            attempted=True,
            source_result="complete",
            expected_count=1,
            records=records,
        )
        object.__setattr__(self, "operation_reads", 0)

    def __getattribute__(self, name: str) -> object:
        if name == "operation_id":
            reads = object.__getattribute__(self, "operation_reads")
            object.__setattr__(self, "operation_reads", reads + 1)
            return (
                "sse_identity@1" if reads == 0 else "/private/tmp/provider-secret-token"
            )
        return object.__getattribute__(self, name)


class _StatefulIdentity(UniverseIdentity):
    def __init__(self) -> None:
        source = _identity()
        super().__init__(
            exchange=source.exchange,
            code=source.code,
            name=source.name,
            security_category=source.security_category,
            listing_state=source.listing_state,
            effective_from=source.effective_from,
            effective_to=source.effective_to,
            observed_at=source.observed_at,
            available_at=source.available_at,
            retrieved_at=source.retrieved_at,
            provenance_uri=source.provenance_uri,
        )
        object.__setattr__(self, "name_reads", 0)

    def __getattribute__(self, name: str) -> object:
        if name == "name":
            reads = object.__getattribute__(self, "name_reads")
            object.__setattr__(self, "name_reads", reads + 1)
            return "浦发银行" if reads == 0 else b"invalid"
        return object.__getattribute__(self, name)


class UniverseRequestInputTests(unittest.TestCase):
    def test_stateful_request_is_read_once_and_prepared_immutably(self) -> None:
        request = _StatefulRequest()

        prepared = prepare_universe_request(
            request,
            research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
        )

        self.assertEqual(prepared.screen_date, "2026-08-20")
        self.assertEqual(prepared.information_cutoff, "2026-08-21T10:00:00+08:00")
        self.assertEqual(request.screen_reads, 1)
        with self.assertRaises(FrozenInstanceError):
            prepared.screen_date = "2026-08-22"

    def test_time_authority_rejects_malformed_naive_future_and_inverted_values(
        self,
    ) -> None:
        now = datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI)
        invalid_requests = (
            UniverseRequest("2026-8-20", "2026-08-21T10:00:00+08:00"),
            UniverseRequest("2026-08-20", "2026-08-21T10:00:00"),
            UniverseRequest("2026-08-20", "2026-08-22T10:00:00+08:00"),
            UniverseRequest("2026-08-21", "2026-08-20T23:59:59+08:00"),
            UniverseRequest("2026-08-20", "2026-08-20T22:00:00+08:00"),
        )

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaisesRegex(
                    ValueError, "^Universe request time authority is invalid[.]$"
                ) as caught:
                    prepare_universe_request(request, research_now=now)
                self.assertEqual(caught.exception.code, "invalid_research_time")

    def test_historical_and_current_cutoffs_use_exact_shanghai_authority(self) -> None:
        now = datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI)

        historical = prepare_universe_request(
            UniverseRequest("2026-08-20", "2026-08-20T15:59:59+00:00"),
            research_now=now,
        )
        current = prepare_universe_request(
            UniverseRequest("2026-08-20", "2026-08-21T02:00:00+00:00"),
            research_now=now,
        )

        self.assertEqual(historical.information_cutoff, "2026-08-20T23:59:59+08:00")
        self.assertEqual(current.information_cutoff, "2026-08-21T10:00:00+08:00")

    def test_prepared_values_have_no_public_constructor_or_replace_bypass(self) -> None:
        prepared = prepare_universe_request(
            UniverseRequest("2026-08-20", "2026-08-21T10:00:00+08:00"),
            research_now=datetime(2026, 8, 21, 10, 0, tzinfo=ASIA_SHANGHAI),
        )

        self.assertFalse(
            any(name.startswith("Prepared") for name in universe_input_module.__all__)
        )
        with self.assertRaises((TypeError, ValueError)):
            replace(prepared, screen_date="2026-08-22")
        with self.assertRaises(TypeError):
            type(prepared)(
                screen_date="2026-08-22",
                information_cutoff="2026-08-22T10:00:00+08:00",
            )


class ExchangeListInputTests(unittest.TestCase):
    def test_stateful_outcome_and_mutable_records_are_detached_once(self) -> None:
        records = [_list_record()]
        outcome = _StatefulListOutcome(records)

        prepared = prepare_exchange_list_outcome(outcome)
        records.append(_list_record("600001"))

        self.assertEqual(prepared.expected_count, 1)
        self.assertEqual(outcome.expected_reads, 1)
        self.assertEqual(len(prepared.records), 1)
        self.assertEqual(prepared.records[0].canonical_security_id, "SSE:600000")
        self.assertIsInstance(prepared.records, tuple)
        with self.assertRaises(FrozenInstanceError):
            prepared.records[0].name = "changed"

    def test_list_source_result_table_covers_all_eighty_shapes(self) -> None:
        row = _list_record()

        def expected_acceptance(
            attempted: bool,
            source_result: str,
            expected_count: int,
            pages_received: int,
            record_count: int,
        ) -> bool:
            if record_count > expected_count or (record_count and not pages_received):
                return False
            if source_result in {"credential_blocked", "unavailable"}:
                return pages_received == 0 and record_count == 0
            if not attempted:
                return False
            if source_result == "complete":
                return (expected_count, pages_received, record_count) == (1, 1, 1)
            if source_result == "observed_empty":
                return (expected_count, pages_received, record_count) == (0, 1, 0)
            return (
                source_result == "partial"
                and expected_count == 1
                and not (pages_received == 1 and record_count == 1)
            )

        combinations = product(
            (False, True),
            (
                "complete",
                "observed_empty",
                "partial",
                "credential_blocked",
                "unavailable",
            ),
            (0, 1),
            (0, 1),
            (0, 1),
        )
        for attempted, state, expected, pages_received, record_count in combinations:
            with self.subTest(
                attempted=attempted,
                state=state,
                expected=expected,
                pages_received=pages_received,
                record_count=record_count,
            ):
                outcome = ExchangeListOutcome(
                    operation_id="sse_stock_list@1",
                    exchange="SSE",
                    screen_date="2026-08-20",
                    attempted=attempted,
                    source_result=state,
                    expected_count=expected,
                    pages_expected=1,
                    pages_received=pages_received,
                    records=[row] if record_count else [],
                )
                try:
                    prepare_exchange_list_outcome(outcome)
                except ValueError:
                    accepted = False
                else:
                    accepted = True
                self.assertEqual(
                    accepted,
                    expected_acceptance(
                        attempted, state, expected, pages_received, record_count
                    ),
                )

    def test_partial_pagination_retains_detached_observed_records(self) -> None:
        prepared = prepare_exchange_list_outcome(
            ExchangeListOutcome(
                operation_id="sse_stock_list@1",
                exchange="SSE",
                screen_date="2026-08-20",
                attempted=True,
                source_result="partial",
                expected_count=2,
                pages_expected=2,
                pages_received=1,
                records=[_list_record()],
            )
        )

        self.assertEqual(prepared.source_result, "partial")
        self.assertEqual(prepared.expected_count, 2)
        self.assertEqual([item.code for item in prepared.records], ["600000"])

    def test_effective_timestamp_uses_shanghai_date_and_normalized_value(self) -> None:
        prepared = prepare_exchange_list_outcome(
            ExchangeListOutcome(
                operation_id="sse_stock_list@1",
                exchange="SSE",
                screen_date="2026-08-20",
                attempted=True,
                source_result="complete",
                expected_count=1,
                pages_expected=1,
                pages_received=1,
                records=[
                    replace(_list_record(), effective_at="2026-08-19T16:30:00+00:00")
                ],
            )
        )

        self.assertEqual(prepared.records[0].effective_at, "2026-08-20T00:30:00+08:00")

    def test_unsafe_operation_is_rejected_and_provenance_is_safely_sealed(self) -> None:
        unsafe = "/private/tmp/provider-secret-token"
        with self.assertRaises(ValueError) as caught:
            prepare_exchange_list_outcome(
                ExchangeListOutcome(
                    operation_id=unsafe,
                    exchange="SSE",
                    screen_date="2026-08-20",
                    attempted=False,
                    source_result="unavailable",
                    expected_count=0,
                    pages_expected=1,
                    pages_received=0,
                    records=[],
                )
            )
        self.assertEqual(caught.exception.code, "invalid_operation_identity")
        self.assertNotIn(unsafe, str(caught.exception))

        prepared = prepare_exchange_list_outcome(
            ExchangeListOutcome(
                operation_id="sse_stock_list@1",
                exchange="SSE",
                screen_date="2026-08-20",
                attempted=True,
                source_result="complete",
                expected_count=1,
                pages_expected=1,
                pages_received=1,
                records=[replace(_list_record(), provenance_uri=unsafe)],
            )
        )
        self.assertEqual(prepared.records[0].provenance_uri, "universe-list:SSE:600000")

    def test_exact_runtime_types_duplicates_and_exchange_conflicts_fail_closed(
        self,
    ) -> None:
        class StringSubclass(str):
            pass

        invalid_outcomes = (
            ExchangeListOutcome(
                b"sse_stock_list@1",
                "SSE",
                "2026-08-20",
                False,
                "unavailable",
                0,
                1,
                0,
                [],
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                1,
                "unavailable",
                0,
                1,
                0,
                [],
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                True,
                StringSubclass("complete"),
                1,
                1,
                1,
                [_list_record()],
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                True,
                "complete",
                True,
                1,
                1,
                [_list_record()],
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                True,
                "complete",
                1,
                1,
                1,
                (item for item in [_list_record()]),
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                True,
                "complete",
                1,
                1,
                1,
                [replace(_list_record(), exchange="SZSE")],
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                True,
                "complete",
                1,
                1,
                1,
                [replace(_list_record(), code="SSE:600000")],
            ),
            ExchangeListOutcome(
                "sse_stock_list@1",
                "SSE",
                "2026-08-20",
                True,
                "complete",
                1,
                1,
                1,
                [replace(_list_record(), provenance_uri=b"source")],
            ),
        )
        for outcome in invalid_outcomes:
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    prepare_exchange_list_outcome(outcome)

        duplicate = _list_record()
        with self.assertRaises(ValueError) as caught:
            prepare_exchange_list_outcome(
                ExchangeListOutcome(
                    "sse_stock_list@1",
                    "SSE",
                    "2026-08-20",
                    True,
                    "complete",
                    2,
                    1,
                    1,
                    [duplicate, duplicate],
                )
            )
        self.assertEqual(caught.exception.code, "duplicate_canonical_identity")


class IdentityInputTests(unittest.TestCase):
    def test_stateful_identity_outcome_is_not_reread_after_validation(self) -> None:
        records = [_identity()]
        outcome = _StatefulIdentityOutcome(records)

        prepared = prepare_identity_outcome(outcome)
        records.append(_identity("600001"))

        self.assertEqual(prepared.operation_id, "sse_identity@1")
        self.assertEqual(outcome.operation_reads, 1)
        self.assertEqual(len(prepared.records), 1)
        self.assertEqual(prepared.records[0].listing_state, "listed")

    def test_stateful_identity_record_properties_are_read_once(self) -> None:
        record = _StatefulIdentity()

        prepared = prepare_identity_outcome(
            IdentityBatchOutcome("sse_identity@1", "SSE", True, "complete", 1, [record])
        )

        self.assertEqual(prepared.records[0].name, "浦发银行")
        self.assertEqual(record.name_reads, 1)

    def test_identity_source_result_table_is_exhaustive(self) -> None:
        row = _identity()
        for attempted, state, expected, record_count in product(
            (False, True),
            (
                "complete",
                "observed_empty",
                "partial",
                "credential_blocked",
                "unavailable",
            ),
            (0, 1),
            (0, 1),
        ):
            with self.subTest(
                attempted=attempted,
                state=state,
                expected=expected,
                record_count=record_count,
            ):
                if state in {"credential_blocked", "unavailable"}:
                    expected_acceptance = record_count == 0
                elif not attempted:
                    expected_acceptance = False
                elif state == "complete":
                    expected_acceptance = (expected, record_count) == (1, 1)
                elif state == "observed_empty":
                    expected_acceptance = (expected, record_count) == (0, 0)
                else:
                    expected_acceptance = (
                        state == "partial" and expected == 1 and record_count == 0
                    )
                try:
                    prepare_identity_outcome(
                        IdentityBatchOutcome(
                            operation_id="sse_identity@1",
                            exchange="SSE",
                            attempted=attempted,
                            source_result=state,
                            expected_count=expected,
                            records=[row] if record_count else [],
                        )
                    )
                except ValueError:
                    accepted = False
                else:
                    accepted = True
                self.assertEqual(accepted, expected_acceptance)

    def test_all_proven_listing_states_round_trip_and_unknown_remains_unproven(
        self,
    ) -> None:
        states = (
            "listed",
            "paused_listing",
            "terminated_listing",
            "trading_halt",
            "current",
        )
        for state in states:
            with self.subTest(state=state):
                prepared = prepare_identity_outcome(
                    IdentityBatchOutcome(
                        "sse_identity@1",
                        "SSE",
                        True,
                        "complete",
                        1,
                        [_identity(listing_state=state)],
                    )
                )
                self.assertEqual(prepared.records[0].listing_state, state)
                self.assertEqual(prepared.records[0].effective_from, "1999-11-10")

        for state in (None, "", "   ", "unknown"):
            with self.subTest(state=state):
                prepared = prepare_identity_outcome(
                    IdentityBatchOutcome(
                        "sse_identity@1",
                        "SSE",
                        True,
                        "complete",
                        1,
                        [_identity(listing_state=state)],
                    )
                )
                self.assertIsNone(prepared.records[0].listing_state)

        with self.assertRaises(ValueError) as caught:
            prepare_identity_outcome(
                IdentityBatchOutcome(
                    "sse_identity@1",
                    "SSE",
                    True,
                    "complete",
                    1,
                    [_identity(listing_state=" listed ")],
                )
            )
        self.assertEqual(caught.exception.code, "invalid_listing_state")

    def test_exact_record_types_identity_and_chronology_fail_closed(self) -> None:
        invalid_records = (
            replace(_identity(), code=b"600000"),
            replace(_identity(), name="浦发银行".encode()),
            replace(_identity(), security_category="B_SHARE"),
            replace(_identity(), provenance_uri=""),
            replace(_identity(), observed_at="2026-08-22T10:00:00+08:00"),
            replace(
                _identity(), effective_from="2026-08-21", effective_to="2026-08-20"
            ),
        )
        for record in invalid_records:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):
                    prepare_identity_outcome(
                        IdentityBatchOutcome(
                            "sse_identity@1",
                            "SSE",
                            True,
                            "complete",
                            1,
                            [record],
                        )
                    )


class UniverseInputInstalledCopyTests(unittest.TestCase):
    def test_copied_skill_exports_the_pure_preparation_interface(self) -> None:
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
                        "from datetime import datetime; "
                        "from zoneinfo import ZoneInfo; "
                        "from a_share_research.universe_input import "
                        "UniverseRequest, prepare_universe_request; "
                        "value = prepare_universe_request("
                        "UniverseRequest('2026-08-20', "
                        "'2026-08-21T10:00:00+08:00'), "
                        "research_now=datetime(2026, 8, 21, 10, 0, "
                        "tzinfo=ZoneInfo('Asia/Shanghai'))); "
                        "print(value.screen_date)"
                    ),
                ],
                cwd=temporary_directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "2026-08-20\n")


if __name__ == "__main__":
    unittest.main()
