from __future__ import annotations

import unittest
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import json
import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.market_session_context import (  # noqa: E402
    CalendarDay,
    CalendarEvidence,
    ContextBindings,
    DeterministicMarketSessionAdapter,
    MarketSessionOperationRequest,
)

from a_share_research.research_result import (  # noqa: E402
    DirectTaskPlan,
    DirectTaskClaim,
    EvidenceUsePolicy,
    OperationObligation,
    ResearchDependency,
    fold_direct_task_research_result,
)

TZ = ZoneInfo("Asia/Shanghai")
BINDINGS = ContextBindings("1" * 64, "2" * 64, "3" * 64, "4" * 64, "artifact-test")


def calendars() -> tuple[CalendarEvidence, ...]:
    # Explicit synthetic days: these are not a captured exchange response.
    days = tuple(
        CalendarDay(day, status)
        for day, status in (
            ("2026-10-08", "trading"),
            ("2026-10-09", "trading"),
            ("2026-10-10", "weekend"),
            ("2026-10-11", "weekend"),
            ("2026-10-12", "trading"),
            ("2026-10-13", "trading"),
            ("2026-10-14", "trading"),
            ("2026-10-15", "holiday"),
        )
    )
    return tuple(
        CalendarEvidence(
            exchange=exchange,
            evidence_id=f"synthetic-calendar-{exchange}",
            version="synthetic-1",
            provenance_uri=f"fixture:{exchange}:calendar",
            available_at="2026-10-01T00:00:00+08:00",
            retrieved_at="2026-10-08T00:00:00+08:00",
            effective_from="2026-10-08",
            effective_to="2026-10-15",
            coverage_start="2026-10-08",
            coverage_end="2026-10-15",
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
            rule_evidence_id=f"synthetic-rule-{exchange}",
            rule_provenance_uri=f"fixture:{exchange}:rule",
            synthetic=True,
            trading_day_completion_time="15:30",
            completion_evidence_id=f"synthetic-completion-{exchange}",
        )
        for exchange in ("SSE", "SZSE", "BSE")
    )


class MarketSessionContextTests(unittest.TestCase):
    def test_current_intraday_freezes_original_clock_and_common_completed_day(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)
        adapter = DeterministicMarketSessionAdapter(calendars(), clock=lambda: now)
        outcome = adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 1
        )
        self.assertEqual(outcome.coverage, "complete")
        payload = outcome.to_dict()["observations"][0]["payload"]
        self.assertEqual(payload["session"]["phase"], "continuous_auction")
        context = payload["context"]
        self.assertEqual(context["research_now"], "2026-10-14T10:00:00+08:00")
        self.assertEqual(context["information_cutoff"], "2026-10-14T10:00:00+08:00")
        self.assertEqual(context["screen_date"], "2026-10-13")
        self.assertEqual(
            [proof["exchange"] for proof in context["calendar_proofs"]],
            ["SSE", "SZSE", "BSE"],
        )

    def test_all_regular_auction_phases_and_proved_closed_days(self) -> None:
        for timestamp, phase, screen_date in (
            ("2026-10-14T09:00:00+08:00", "pre_open", "2026-10-13"),
            ("2026-10-14T09:15:00+08:00", "opening_auction", "2026-10-13"),
            ("2026-10-14T09:25:00+08:00", "opening_intermission", "2026-10-13"),
            ("2026-10-14T09:30:00+08:00", "continuous_auction", "2026-10-13"),
            ("2026-10-14T11:30:00+08:00", "midday_break", "2026-10-13"),
            ("2026-10-14T13:00:00+08:00", "continuous_auction", "2026-10-13"),
            ("2026-10-14T14:57:00+08:00", "closing_auction", "2026-10-13"),
            ("2026-10-14T15:00:00+08:00", "post_close", "2026-10-13"),
            ("2026-10-14T15:30:00+08:00", "post_close", "2026-10-14"),
            ("2026-10-11T12:00:00+08:00", "weekend", "2026-10-09"),
            ("2026-10-15T12:00:00+08:00", "holiday", "2026-10-14"),
        ):
            with self.subTest(timestamp=timestamp):
                now = datetime.fromisoformat(timestamp)
                outcome = DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: now
                ).execute(MarketSessionOperationRequest(timestamp[:10], BINDINGS), 1)
                payload = outcome.to_dict()["observations"][0]["payload"]
                self.assertEqual(payload["session"]["phase"], phase)
                self.assertEqual(payload["context"]["screen_date"], screen_date)

    def test_three_exchange_missing_gaps_conflicts_and_unproved_authority_fail_closed(
        self,
    ) -> None:
        original = calendars()
        conflict_days = tuple(
            replace(day, status="holiday") if day.session_date == "2026-10-13" else day
            for day in original[2].days
        )
        variants = (
            (original[:2], "calendar_exchange_gap"),
            (
                (
                    original[0],
                    original[1],
                    replace(
                        original[2], days=original[2].days[:-2] + original[2].days[-1:]
                    ),
                ),
                "calendar_coverage_gap",
            ),
            (
                (original[0], original[1], replace(original[2], days=conflict_days)),
                "calendar_session_conflict",
            ),
            (
                (original[0], original[1], replace(original[2], authority="unknown")),
                "calendar_authority_unproved",
            ),
            (
                (original[0], original[1], replace(original[2], authority="stale")),
                "calendar_authority_unproved",
            ),
            (
                (
                    original[0],
                    original[1],
                    replace(original[2], effective_to="2026-10-13"),
                ),
                "calendar_authority_unproved",
            ),
        )
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        for evidence, code in variants:
            with self.subTest(code=code):
                outcome = DeterministicMarketSessionAdapter(
                    evidence, clock=lambda: now
                ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
                self.assertNotEqual(outcome.coverage, "complete")
                self.assertEqual(outcome.source_errors[0]["code"], code)
                for observation in outcome.observations:
                    self.assertNotIn("context", observation["payload"])

    def test_resume_preserves_original_context_and_records_real_retrieval_time(
        self,
    ) -> None:
        created_at = datetime(2026, 10, 9, 10, tzinfo=TZ)
        created = (
            DeterministicMarketSessionAdapter(calendars(), clock=lambda: created_at)
            .execute(MarketSessionOperationRequest("2026-10-09", BINDINGS), 1)
            .to_dict()["observations"][0]["payload"]["context"]
        )
        for timestamp in (
            "2026-10-09T14:00:00+08:00",
            "2026-10-09T16:00:00+08:00",
            "2026-10-12T10:00:00+08:00",
        ):
            with self.subTest(timestamp=timestamp):
                resumed_at = datetime.fromisoformat(timestamp)
                resumed = DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: resumed_at
                ).execute(
                    MarketSessionOperationRequest(
                        "2026-10-09",
                        BINDINGS,
                        frozen_context=created,
                        expected_context_digest=created["digest"],
                    ),
                    1,
                )
                projection = resumed.to_dict()["observations"][0]
                self.assertEqual(projection["payload"]["context"], created)
                self.assertEqual(projection["retrieved_at"], timestamp)
                self.assertEqual(
                    created["information_cutoff"], "2026-10-09T10:00:00+08:00"
                )
                self.assertEqual(created["screen_date"], "2026-10-08")

    def test_resume_semantic_tampering_stays_rejected_after_local_digest_recalculation(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        original = (
            DeterministicMarketSessionAdapter(calendars(), clock=lambda: now)
            .execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
            .to_dict()["observations"][0]["payload"]["context"]
        )
        for field, value in (
            ("information_cutoff", "2026-10-14T23:59:59+08:00"),
            ("screen_date", "2026-10-14"),
            ("calendar_proofs", []),
        ):
            with self.subTest(field=field):
                tampered = deepcopy(original)
                tampered[field] = value
                del tampered["digest"]
                digest = hashlib.sha256(
                    json.dumps(
                        tampered,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
                tampered["digest"] = digest
                # #108 also binds the original digest independently. Even when
                # a local digest is recalculated, #117 must reject semantics.
                outcome = DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: now
                ).execute(
                    MarketSessionOperationRequest(
                        "2026-10-14",
                        BINDINGS,
                        frozen_context=tampered,
                        expected_context_digest=digest,
                    ),
                    1,
                )
                self.assertEqual(outcome.coverage, "failed")
                self.assertFalse(outcome.observations)

    def test_historical_cutoff_future_rejection_and_aware_clock(self) -> None:
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        outcome = DeterministicMarketSessionAdapter(
            calendars(), clock=lambda: now
        ).execute(MarketSessionOperationRequest("2026-10-11", BINDINGS), 1)
        context = outcome.to_dict()["observations"][0]["payload"]["context"]
        self.assertEqual(context["information_cutoff"], "2026-10-11T23:59:59+08:00")
        self.assertEqual(context["screen_date"], "2026-10-09")
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["session"][
                "latest_completed_session"
            ],
            "2026-10-13",
        )
        for clock, requested, code in (
            (lambda: now, "2026-10-15", "future_requested_date"),
            (lambda: now.replace(tzinfo=None), "2026-10-14", "invalid_timestamp"),
        ):
            failed = DeterministicMarketSessionAdapter(
                calendars(), clock=clock
            ).execute(MarketSessionOperationRequest(requested, BINDINGS), 1)
            self.assertEqual(failed.source_errors[0]["code"], code)
            self.assertFalse(failed.observations)

    def test_current_retrieval_cannot_repair_old_calendar_or_unproved_full_day(
        self,
    ) -> None:
        original = calendars()
        now = datetime(2026, 10, 14, 15, 10, tzinfo=TZ)
        for evidence, code in (
            (
                tuple(
                    replace(
                        item, retrieved_at=now.isoformat(), effective_to="2026-10-13"
                    )
                    for item in original
                ),
                "calendar_authority_unproved",
            ),
            (
                tuple(
                    replace(
                        item,
                        trading_day_completion_time=None,
                        completion_evidence_id=None,
                    )
                    for item in original
                ),
                "trading_day_completion_unproved",
            ),
            (
                tuple(
                    replace(item, retrieved_at="2026-10-15T10:00:00+08:00")
                    for item in original
                ),
                "calendar_timestamp_unproved",
            ),
        ):
            failed = DeterministicMarketSessionAdapter(
                evidence, clock=lambda: now
            ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
            self.assertEqual(failed.source_errors[0]["code"], code)
            self.assertFalse(failed.observations)

    def test_caller_mutation_and_projection_do_not_change_frozen_evidence(self) -> None:
        mutable_days = list(calendars()[0].days)
        source = (replace(calendars()[0], days=mutable_days),) + calendars()[1:]
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        adapter = DeterministicMarketSessionAdapter(source, clock=lambda: now)
        mutable_days.clear()
        outcome = adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 1
        )
        first = outcome.to_dict()
        expected = json.dumps(first, sort_keys=True)
        first["observations"][0]["payload"]["context"]["calendar_proofs"].clear()
        self.assertEqual(json.dumps(outcome.to_dict(), sort_keys=True), expected)
        with self.assertRaises(TypeError):
            outcome.observations[0]["payload"]["context"]["screen_date"] = "2099-01-01"

    def test_recomputed_calendar_reference_and_changed_bindings_cannot_resume(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        adapter = DeterministicMarketSessionAdapter(calendars(), clock=lambda: now)
        original = adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 1
        ).to_dict()["observations"][0]["payload"]["context"]
        tampered = deepcopy(original)
        tampered["calendar_proofs"][2]["rule_evidence_id"] = "unrelated-calendar"
        del tampered["digest"]
        digest = hashlib.sha256(
            json.dumps(
                tampered, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        tampered["digest"] = digest
        failed = adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS, tampered, digest), 1
        )
        self.assertEqual(failed.source_errors[0]["code"], "context_semantic_mismatch")
        failed = adapter.execute(
            MarketSessionOperationRequest(
                "2026-10-14",
                replace(BINDINGS, artifact_id="other-artifact"),
                original,
                original["digest"],
            ),
            1,
        )
        self.assertEqual(failed.source_errors[0]["code"], "context_binding_mismatch")

    def test_calendar_reference_safety_is_checked_before_public_projection(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        unsafe = (
            replace(calendars()[0], provenance_uri="/private/secret/calendar"),
        ) + calendars()[1:]
        outcome = DeterministicMarketSessionAdapter(unsafe, clock=lambda: now).execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 1
        )
        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(
            outcome.source_errors[0]["code"], "calendar_reference_unproved"
        )
        self.assertNotIn("/private/secret", json.dumps(outcome.to_dict()))

    def test_public_projection_resolves_context_evidence_reference(self) -> None:
        now = datetime(2026, 10, 14, 10, tzinfo=TZ)
        outcome = DeterministicMarketSessionAdapter(
            calendars(), clock=lambda: now
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
        plan = DirectTaskPlan(
            task_type="session_context_contract_test",
            as_of="2026-10-14",
            obligations=(
                OperationObligation(
                    "market_trading_session",
                    "market_session_context@1",
                    "exchange_document",
                    True,
                    None,
                    (EvidenceUsePolicy("session_context", "supporting"),),
                ),
            ),
            dependencies=(
                ResearchDependency("session_context", "session_context", 1, True),
            ),
        )
        local_key = "market_session_context@1:0"
        result = fold_direct_task_research_result(
            plan,
            (outcome,),
            claims=(
                DirectTaskClaim(
                    "session_context",
                    "session_context",
                    (local_key,),
                    {"calendar_evidence_ids": [local_key]},
                ),
            ),
            summary="Synthetic session context contract test.",
        ).to_dict()
        self.assertEqual(
            result["claims"][0]["payload"]["calendar_evidence_ids"],
            [result["observations"][0]["id"]],
        )
        self.assertEqual(
            result["coverage"]["market_trading_session"]["operations"][
                "market_session_context@1"
            ]["observation_ids"],
            [result["observations"][0]["id"]],
        )

    def test_clean_copied_install_loads_same_context_module(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "a-share-research"
            shutil.copytree(
                source_root,
                installed,
                ignore=shutil.ignore_patterns(
                    ".runtime", ".state", "__pycache__", "*.pyc"
                ),
            )
            program = """
import json,sys
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from a_share_research.market_session_context import CalendarDay,CalendarEvidence,ContextBindings,DeterministicMarketSessionAdapter,MarketSessionOperationRequest
proofs=json.load(sys.stdin)
for item in proofs:
    item['days']=tuple(CalendarDay(**day) for day in item['days'])
    item['auction_times']=tuple(item['auction_times'])
calendars=tuple(CalendarEvidence(**item) for item in proofs)
now=datetime.fromisoformat('2026-10-14T10:00:00+08:00')
outcome=DeterministicMarketSessionAdapter(calendars,clock=lambda:now).execute(MarketSessionOperationRequest('2026-10-14',ContextBindings('1'*64,'2'*64,'3'*64,'4'*64,'artifact-test')),1)
print(json.dumps(outcome.to_dict(),sort_keys=True))
"""
            completed = subprocess.run(
                [sys.executable, "-I", "-c", program, str(installed / "scripts")],
                cwd=temporary,
                input=json.dumps([item.to_dict() for item in calendars()]),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            installed_outcome = json.loads(completed.stdout)
            self.assertEqual(
                installed_outcome["observations"][0]["payload"]["context"][
                    "screen_date"
                ],
                "2026-10-13",
            )
            self.assertFalse((installed / ".runtime").exists())
            self.assertFalse((installed / ".state").exists())

    def test_scheduled_completion_does_not_claim_actual_data_completion(self) -> None:
        now = datetime(2026, 10, 14, 15, 30, tzinfo=TZ)
        outcome = DeterministicMarketSessionAdapter(
            calendars(), clock=lambda: now
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
        session = outcome.to_dict()["observations"][0]["payload"]["session"]
        self.assertEqual(
            session["latest_completed_session_basis"], "calendar_scheduled_completion"
        )
        self.assertFalse(session["actual_trade_or_quote_completeness_proved"])
        self.assertEqual(
            session["latest_regular_auction_completed_session"], "2026-10-14"
        )

    def test_midday_preserves_current_session_hold_without_an_age_threshold(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)
        outcome = DeterministicMarketSessionAdapter(
            calendars(), clock=lambda: now
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
        session = outcome.to_dict()["observations"][0]["payload"]["session"]
        self.assertTrue(session["current_session_applicable"])
        self.assertEqual(session["freshness_basis"], "current_session_hold")

    def test_historical_C_survives_missing_present_day_calendar_as_explicit_partial(
        self,
    ) -> None:
        now = datetime(2028, 10, 14, 10, tzinfo=TZ)
        outcome = DeterministicMarketSessionAdapter(
            calendars(), clock=lambda: now
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
        self.assertEqual(outcome.coverage, "partial")
        payload = outcome.to_dict()["observations"][0]["payload"]
        self.assertEqual(payload["context"]["screen_date"], "2026-10-14")
        self.assertEqual(
            payload["context"]["information_cutoff"], "2026-10-14T23:59:59+08:00"
        )
        self.assertEqual(payload["session"]["phase"], "unproved")
        self.assertIsNone(payload["session"]["latest_completed_session"])
        self.assertFalse(payload["session"]["current_session_applicable"])

    def test_acquisition_after_frozen_clock_preserves_real_times_and_can_resume(
        self,
    ) -> None:
        first = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)
        checked = datetime(2026, 10, 14, 10, 0, 3, tzinfo=TZ)
        evidence = tuple(
            replace(item, retrieved_at="2026-10-14T10:00:02+08:00")
            for item in calendars()
        )
        outcome = DeterministicMarketSessionAdapter(
            evidence, clock=lambda: first, execution_clock=lambda: checked
        ).execute(MarketSessionOperationRequest("2026-10-14", BINDINGS), 1)
        self.assertEqual(outcome.coverage, "complete")
        projection = outcome.to_dict()["observations"][0]
        context = projection["payload"]["context"]
        self.assertEqual(context["information_cutoff"], first.isoformat())
        self.assertEqual(context["evidence_checked_at"], checked.isoformat())
        self.assertEqual(projection["retrieved_at"], checked.isoformat())
        later = datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
        resumed = DeterministicMarketSessionAdapter(
            evidence, clock=lambda: later
        ).execute(
            MarketSessionOperationRequest(
                "2026-10-14", BINDINGS, context, context["digest"]
            ),
            1,
        )
        self.assertEqual(
            resumed.to_dict()["observations"][0]["payload"]["context"], context
        )


if __name__ == "__main__":
    unittest.main()
