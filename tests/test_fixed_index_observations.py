from __future__ import annotations

import unittest
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
from collections.abc import Iterator, Mapping
from decimal import localcontext

from tests.skill_imports import add_skill_scripts_to_path
from tests.test_market_session_context import BINDINGS, TZ, calendars

add_skill_scripts_to_path()

from a_share_research.fixed_index_observations import (  # noqa: E402
    DeterministicFixedIndexAdapter,
    FixedIndexOperationRequest,
)
from a_share_research.research_result import (  # noqa: E402
    DirectTaskPlan,
    DirectTaskClaim,
    ResearchDependency,
    fold_direct_task_research_result,
)
from a_share_research.market_session_context import (  # noqa: E402
    DeterministicMarketSessionAdapter,
    MarketSessionOperationRequest,
)

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)


def request() -> FixedIndexOperationRequest:
    return FixedIndexOperationRequest(
        MarketSessionOperationRequest("2026-10-14", BINDINGS), purpose="current_session"
    )


def rows() -> list[dict[str, object]]:
    # Explicit synthetic normalized input, never a captured provider response.
    return [
        {
            "canonical_id": canonical_id,
            "value": "101.25",
            "previous_close": "100",
            "change": "1.25",
            "change_percent": "1.25",
            "unit": "index_point",
            "price_semantics": "current_value",
            "baseline_semantics": "previous_session_close",
            "session_date": "2026-10-14",
            "observed_at": "2026-10-14T09:59:55+08:00",
            "retrieved_at": "2026-10-14T10:00:00+08:00",
            "provenance_uri": f"fixture:index:{canonical_id}",
            "source_version": "synthetic-1",
            "synthetic": True,
        }
        for canonical_id in ("SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050")
    ]


class FixedIndexObservationTests(unittest.TestCase):
    def test_shuffled_fixed_set_is_projected_in_canonical_order(self) -> None:
        outcome = DeterministicFixedIndexAdapter(
            list(reversed(rows())),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(
            [
                row["payload"]["observation"]["canonical_id"]
                for row in outcome.to_dict()["observations"]
            ],
            ["SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050"],
        )

    def test_duplicate_index_is_conflicting_and_other_indices_are_retained(
        self,
    ) -> None:
        material = rows()
        material.append(dict(material[0]))
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(
            [
                row["payload"]["observation"]["canonical_id"]
                for row in outcome.to_dict()["observations"]
            ],
            ["SZSE:399001", "SZSE:399006", "BSE:899050"],
        )
        self.assertEqual(outcome.source_errors[0]["code"], "index_identity_duplicate")

    def test_missing_index_never_turns_fixed_set_into_complete_or_empty(self) -> None:
        for material in (rows()[:-1], []):
            with self.subTest(count=len(material)):
                outcome = DeterministicFixedIndexAdapter(
                    material,
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: NOW
                    ),
                    clock=lambda: NOW,
                ).execute(request(), 1)
                self.assertEqual(outcome.coverage, "partial" if material else "failed")
                self.assertEqual(
                    outcome.source_errors[-1]["code"], "index_identity_missing"
                )

    def test_impossible_source_chronology_is_partial_without_raw_fallback(self) -> None:
        for changes in (
            {"observed_at": "2020-01-02T10:00:00+08:00"},
            {"observed_at": "2026-10-14T10:00:01+08:00"},
            {"retrieved_at": "2026-10-14T10:00:01+08:00"},
            {"observed_at": "2026-10-14T09:59:55"},
            {"observed_at": None},
        ):
            with self.subTest(changes=changes):
                material = rows()
                material[0].update(changes)
                outcome = DeterministicFixedIndexAdapter(
                    material,
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: NOW
                    ),
                    clock=lambda: NOW,
                ).execute(request(), 1)
                self.assertEqual(outcome.coverage, "partial")
                self.assertEqual(len(outcome.observations), 3)
                self.assertEqual(
                    outcome.source_errors[0]["code"], "index_timestamp_unproved"
                )

    def test_decimal_units_and_arithmetic_fail_closed_per_index(self) -> None:
        for changes in (
            {"value": True},
            {"value": "NaN"},
            {"value": "Infinity"},
            {"value": "0"},
            {"previous_close": "0"},
            {"value": "1e1000000"},
            {"unit": "CNY"},
            {"change": "9"},
            {"change_percent": "125"},
            {"baseline_semantics": "unknown"},
        ):
            with self.subTest(changes=changes):
                material = rows()
                material[0].update(changes)
                outcome = DeterministicFixedIndexAdapter(
                    material,
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: NOW
                    ),
                    clock=lambda: NOW,
                ).execute(request(), 1)
                self.assertEqual(outcome.coverage, "partial")
                self.assertEqual(len(outcome.observations), 3)
                self.assertIn(
                    outcome.source_errors[0]["code"],
                    (
                        "index_value_invalid",
                        "index_unit_mismatch",
                        "index_arithmetic_mismatch",
                    ),
                )

    def test_unprojectable_row_is_rejected_without_discarding_independent_indices(
        self,
    ) -> None:
        material = rows()
        material[0]["provenance_uri"] = "fixture:" + "x" * 70000
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(len(outcome.observations), 3)
        self.assertEqual(outcome.source_errors[0]["code"], "index_projection_invalid")
        self.assertLess(len(str(outcome.to_dict())), 10000)

    def test_proof_reuses_recursively_immutable_and_detached_projection_contract(
        self,
    ) -> None:
        material = rows()
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        with self.assertRaises(TypeError):
            dict.__setitem__(outcome.observations[0], "payload", {})
        with self.assertRaises(TypeError):
            dict.__setitem__(
                outcome.observations[0]["payload"]["observation"], "value", "999"
            )
        projected = outcome.to_dict()
        projected["observations"][0]["payload"]["observation"]["value"] = "999"
        material[0]["value"] = "0"
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"]["value"],
            "101.25",
        )

    def test_historical_request_consumes_frozen_completed_context_even_if_current_is_unproved(
        self,
    ) -> None:
        for instant in ("2026-10-14T10:00:00+08:00", "2026-10-16T10:00:00+08:00"):
            with self.subTest(instant=instant):
                checked = datetime.fromisoformat(instant)
                material = rows()
                for row in material:
                    row.update(
                        session_date="2026-10-13",
                        observed_at="2026-10-13T15:30:00+08:00",
                        retrieved_at=instant,
                        price_semantics="completed_close",
                    )
                outcome = DeterministicFixedIndexAdapter(
                    material,
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: checked
                    ),
                    clock=lambda: checked,
                ).execute(
                    FixedIndexOperationRequest(
                        MarketSessionOperationRequest("2026-10-13", BINDINGS)
                    ),
                    1,
                )
                self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
                self.assertEqual(
                    outcome.to_dict()["observations"][0]["payload"]["temporal_basis"],
                    "frozen_completed_session",
                )
                self.assertEqual(
                    len(
                        outcome.to_dict()["observations"][0]["payload"][
                            "context_digest"
                        ]
                    ),
                    64,
                )

    def test_current_value_cannot_be_proxied_by_completed_close_or_stale_same_day_quote(
        self,
    ) -> None:
        for changes, code in (
            ({"price_semantics": "completed_close"}, "index_price_semantics_mismatch"),
            ({"observed_at": "2026-10-14T09:58:59+08:00"}, "index_observation_stale"),
        ):
            with self.subTest(changes=changes):
                material = rows()
                material[0].update(changes)
                outcome = DeterministicFixedIndexAdapter(
                    material,
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: NOW
                    ),
                    clock=lambda: NOW,
                ).execute(request(), 1)
                self.assertEqual(outcome.coverage, "partial")
                self.assertEqual(len(outcome.observations), 3)
                self.assertEqual(outcome.source_errors[0]["code"], code)

    def test_public_result_references_use_canonical_observation_ids(self) -> None:
        adapter = DeterministicFixedIndexAdapter(
            rows(),
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(request(), 1)
        local_ids = tuple(
            "fixed_four_indices@1:current_session:" + identity
            for identity in ("SSE:000001", "SZSE:399001", "SZSE:399006", "BSE:899050")
        )
        plan = DirectTaskPlan(
            task_type="fixed_index_contract_test",
            as_of="2026-10-14",
            obligations=(adapter.obligation,),
            dependencies=(
                ResearchDependency(
                    "major_broad_indices", "major_broad_indices", 4, True
                ),
            ),
        )
        result = fold_direct_task_research_result(
            plan,
            (outcome,),
            claims=(
                DirectTaskClaim(
                    "fixed_index_summary",
                    "major_broad_indices",
                    local_ids,
                    {"index_evidence_ids": list(local_ids)},
                ),
            ),
            summary="Synthetic fixed-index contract.",
        ).to_dict()
        self.assertEqual(
            result["claims"][0]["payload"]["index_evidence_ids"],
            [row["id"] for row in result["observations"]],
        )
        self.assertEqual(len(result["observations"]), 4)

    def test_noncanonical_extra_row_cannot_silently_change_the_fixed_set(self) -> None:
        material = rows()
        extra = dict(material[0])
        extra["canonical_id"] = "SSE:000016"
        material.append(extra)
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(len(outcome.observations), 4)
        self.assertEqual(outcome.source_errors[0]["code"], "index_identity_unexpected")

    def test_clean_copied_install_executes_the_same_synthetic_adapter(self) -> None:
        source = Path(__file__).resolve().parents[1] / "skill" / "a-share-research"
        material = {
            "rows": rows(),
            "calendars": [asdict(item) for item in calendars()],
            "bindings": asdict(BINDINGS),
        }
        program = """
import json,sys
from datetime import datetime
from collections.abc import Iterator, Mapping
from decimal import localcontext
sys.path.insert(0,sys.argv[1])
from a_share_research.fixed_index_observations import DeterministicFixedIndexAdapter,FixedIndexOperationRequest
from a_share_research.market_session_context import CalendarDay,CalendarEvidence,ContextBindings,DeterministicMarketSessionAdapter,MarketSessionOperationRequest
material=json.load(sys.stdin)
for item in material['calendars']:
    item['days']=tuple(CalendarDay(**day) for day in item['days'])
    item['auction_times']=tuple(item['auction_times'])
now=datetime.fromisoformat('2026-10-14T10:00:00+08:00')
sessions=DeterministicMarketSessionAdapter(tuple(CalendarEvidence(**item) for item in material['calendars']),clock=lambda:now)
adapter=DeterministicFixedIndexAdapter(material['rows'],session_adapter=sessions,clock=lambda:now)
outcome=adapter.execute(FixedIndexOperationRequest(MarketSessionOperationRequest('2026-10-14',ContextBindings(**material['bindings'])),purpose='current_session'),1)
print(json.dumps({'coverage':outcome.coverage,'identities':[row['payload']['observation']['canonical_id'] for row in outcome.to_dict()['observations']]}))
"""
        with tempfile.TemporaryDirectory(prefix="asr-118-copied-install-") as temp:
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
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "coverage": "complete",
                "identities": [
                    "SSE:000001",
                    "SZSE:399001",
                    "SZSE:399006",
                    "BSE:899050",
                ],
            },
        )

    def test_missing_fields_and_unproved_provenance_remain_sanitized_partial(
        self,
    ) -> None:
        for key, value in (
            ("session_date", None),
            ("provenance_uri", ""),
            ("source_version", ""),
            ("provenance_uri", "https://user:password@example.invalid/index"),
        ):
            with self.subTest(key=key, value=value):
                material = rows()
                if value is None:
                    del material[0][key]
                else:
                    material[0][key] = value
                outcome = DeterministicFixedIndexAdapter(
                    material,
                    session_adapter=DeterministicMarketSessionAdapter(
                        calendars(), clock=lambda: NOW
                    ),
                    clock=lambda: NOW,
                ).execute(request(), 1)
                self.assertEqual(outcome.coverage, "partial")
                self.assertEqual(len(outcome.observations), 3)
                self.assertEqual(
                    outcome.source_errors[0]["code"],
                    "index_provenance_unproved"
                    if key in ("provenance_uri", "source_version")
                    else "index_row_invalid",
                )
                self.assertNotIn("password", str(outcome.to_dict()))

    def test_missing_indices_have_distinct_public_error_references(self) -> None:
        adapter = DeterministicFixedIndexAdapter(
            rows()[:2],
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(request(), 1)
        self.assertEqual(
            [
                error["message"].split(" Index: ")[-1]
                for error in outcome.to_dict()["source_errors"]
            ],
            ["SZSE:399006.", "BSE:899050."],
        )
        plan = DirectTaskPlan(
            task_type="fixed_index_contract_test",
            as_of="2026-10-14",
            obligations=(adapter.obligation,),
            dependencies=(
                ResearchDependency(
                    "major_broad_indices", "major_broad_indices", 4, True
                ),
            ),
        )
        result = fold_direct_task_research_result(
            plan, (outcome,), summary="Synthetic fixed-index partial contract."
        ).to_dict()
        self.assertEqual(len({error["id"] for error in result["source_errors"]}), 2)
        self.assertEqual(len(result["observations"]), 2)

    def test_decimal_precision_and_negative_change_are_independent_of_caller_context(
        self,
    ) -> None:
        material = rows()
        material[0].update(
            value="1000.0000000000000000000000000001",
            previous_close="1000",
            change="1e-28",
            change_percent="1e-29",
        )
        material[1].update(
            value="98.75", previous_close="100", change="-1.25", change_percent="-1.25"
        )
        with localcontext() as context:
            context.prec = 2
            outcome = DeterministicFixedIndexAdapter(
                material,
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: NOW
                ),
                clock=lambda: NOW,
            ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"]["value"],
            "1000.0000000000000000000000000001",
        )
        self.assertEqual(
            outcome.to_dict()["observations"][1]["payload"]["observation"]["change"],
            "-1.25",
        )

    def test_midday_hold_preserves_morning_observation_without_extending_active_age_rule(
        self,
    ) -> None:
        now = datetime.fromisoformat("2026-10-14T12:00:00+08:00")
        material = rows()
        for row in material:
            row.update(
                observed_at="2026-10-14T11:30:00+08:00",
                retrieved_at=now.isoformat(),
                observation_boundary="latest_source_quote",
            )
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: now
            ),
            clock=lambda: now,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"][
                "observed_at"
            ],
            "2026-10-14T11:30:00+08:00",
        )

    def test_restore_cannot_retarget_original_current_use_to_completed_values(
        self,
    ) -> None:
        session_adapter = DeterministicMarketSessionAdapter(
            calendars(), clock=lambda: NOW
        )
        original = session_adapter.execute(
            MarketSessionOperationRequest("2026-10-14", BINDINGS), 1
        )
        context = original.to_dict()["observations"][0]["payload"]["context"]
        later = datetime.fromisoformat("2026-10-14T10:10:00+08:00")
        restored = MarketSessionOperationRequest(
            "2026-10-14",
            BINDINGS,
            frozen_context=context,
            expected_context_digest=context["digest"],
        )
        material = rows()
        for row in material:
            row.update(
                session_date="2026-10-13",
                observed_at="2026-10-13T15:30:00+08:00",
                retrieved_at=later.isoformat(),
                price_semantics="completed_close",
            )
        adapter = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: later
            ),
            clock=lambda: later,
        )
        current = adapter.execute(
            FixedIndexOperationRequest(restored, purpose="current_session"), 1
        )
        self.assertEqual(current.coverage, "failed")
        self.assertEqual(
            current.source_errors[0]["code"], "index_current_session_unproved"
        )
        completed = adapter.execute(
            FixedIndexOperationRequest(restored, purpose="completed_session"), 1
        )
        self.assertEqual(completed.coverage, "complete")
        self.assertEqual(
            completed.to_dict()["observations"][0]["payload"]["context_digest"],
            context["digest"],
        )

    def test_row_is_observed_once_before_validation_and_projection(self) -> None:
        class ChangingValue(Mapping[str, object]):
            def __init__(self, original: dict[str, object]) -> None:
                self.original = original
                self.reads = 0

            def __iter__(self) -> Iterator[str]:
                return iter(self.original)

            def __len__(self) -> int:
                return len(self.original)

            def __getitem__(self, key: str) -> object:
                if key == "value":
                    self.reads += 1
                    return "101.25" if self.reads == 1 else "777"
                return self.original[key]

        material: list[Mapping[str, object]] = rows()
        material[0] = ChangingValue(rows()[0])
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "complete")
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"]["value"],
            "101.25",
        )

    def test_unknown_internal_purpose_cannot_fall_back_to_completed_session(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS), purpose="unknown"
            )  # type: ignore[arg-type]

    def test_nonobject_source_row_is_partial_without_attribute_exception(self) -> None:
        material: list[object] = list(rows())
        material[0] = None
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)  # type: ignore[arg-type]
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(len(outcome.observations), 3)
        self.assertEqual(outcome.source_errors[0]["code"], "index_projection_invalid")

    def test_extreme_timezone_conversion_is_a_partial_timestamp_failure(self) -> None:
        material = rows()
        material[0]["observed_at"] = "9999-12-31T23:59:59+00:00"
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(len(outcome.observations), 3)
        self.assertEqual(outcome.source_errors[0]["code"], "index_timestamp_unproved")

    def test_decimal_exponent_limits_and_traps_do_not_leak_caller_arithmetic_context(
        self,
    ) -> None:
        with localcontext() as context:
            context.Emax = 1
            outcome = DeterministicFixedIndexAdapter(
                rows(),
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: NOW
                ),
                clock=lambda: NOW,
            ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "complete")

    def test_reported_rounding_is_preserved_with_bounded_display_consistency(
        self,
    ) -> None:
        material = rows()
        material[0].update(
            value="4.00",
            previous_close="3.00",
            change="1.00",
            change_percent="33.33",
            arithmetic_basis="source_reported",
        )
        adapter = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(request(), 1)
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"][
                "change_percent"
            ],
            "33.33",
        )
        material[0]["change_percent"] = "35.00"
        invalid = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(invalid.coverage, "partial")
        self.assertEqual(invalid.source_errors[0]["code"], "index_arithmetic_mismatch")

    def test_core_latest_index_session_is_not_proxied_by_screen_date_between_closes(
        self,
    ) -> None:
        instant = datetime.fromisoformat("2026-10-14T15:10:00+08:00")
        material = rows()
        for row in material:
            row.update(
                price_semantics="completed_session_reported_value",
                observed_at="2026-10-14T15:05:00+08:00",
                retrieved_at=instant.isoformat(),
            )
        adapter = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: instant
            ),
            clock=lambda: instant,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-14", BINDINGS),
                purpose="completed_session",
                completed_basis="latest_index_session",
            ),
            1,
        )
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["temporal_basis"],
            "latest_regular_index_session",
        )
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"][
                "session_date"
            ],
            "2026-10-14",
        )

    def test_completed_day_precision_does_not_require_a_fabricated_observation_instant(
        self,
    ) -> None:
        material = rows()
        for row in material:
            row.update(
                session_date="2026-10-13",
                price_semantics="completed_close",
                observed_at=None,
                source_time_precision="day",
            )
        adapter = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        )
        outcome = adapter.execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-13", BINDINGS)
            ),
            1,
        )
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        self.assertIsNone(
            outcome.to_dict()["observations"][0]["payload"]["observation"][
                "observed_at"
            ]
        )
        self.assertEqual(
            outcome.to_dict()["observations"][0]["payload"]["observation"][
                "source_time_precision"
            ],
            "day",
        )

    def test_ancient_facts_with_current_retrieval_cannot_complete_current_indices(
        self,
    ) -> None:
        material = rows()
        material[0]["session_date"] = "2020-01-02"
        material[0]["observed_at"] = "2020-01-02T10:00:00+08:00"
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "partial")
        self.assertEqual(len(outcome.observations), 3)
        self.assertEqual(outcome.source_errors[0]["code"], "index_session_mismatch")

    def test_completed_bases_have_distinct_internal_observation_identities(
        self,
    ) -> None:
        instant = datetime(2026, 10, 14, 16, tzinfo=TZ)
        material = rows()
        for row in material:
            row.update(
                price_semantics="completed_close",
                observed_at="2026-10-14T15:05:00+08:00",
                retrieved_at=instant.isoformat(),
            )
        adapter = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: instant
            ),
            clock=lambda: instant,
        )
        ids = []
        for basis in ("frozen_context", "latest_index_session"):
            outcome = adapter.execute(
                FixedIndexOperationRequest(
                    MarketSessionOperationRequest("2026-10-14", BINDINGS),
                    completed_basis=basis,
                ),
                1,
            )
            self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
            ids.append(outcome.to_dict()["observations"][0]["id"])
        self.assertNotEqual(ids[0], ids[1])

    def test_pre_auction_completed_clock_cannot_be_relabelled_as_completed_close(
        self,
    ) -> None:
        material = rows()
        for row in material:
            row.update(
                session_date="2026-10-13",
                price_semantics="completed_close",
                observed_at="2026-10-13T10:00:00+08:00",
            )
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(
            FixedIndexOperationRequest(
                MarketSessionOperationRequest("2026-10-13", BINDINGS)
            ),
            1,
        )
        self.assertEqual(outcome.coverage, "failed")
        self.assertTrue(
            all(
                error["code"] == "index_timestamp_unproved"
                for error in outcome.source_errors
            )
        )

    def test_positive_reported_baseline_below_display_quantum_keeps_valid_evidence(
        self,
    ) -> None:
        material = rows()
        for row in material:
            row.update(
                value="0.005",
                previous_close="0.005",
                change="0.00",
                change_percent="0.00",
                arithmetic_basis="source_reported",
            )
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "complete", outcome.source_errors)
        self.assertEqual(len(outcome.observations), 4)

    def test_reported_intervals_cannot_pass_only_through_zero_underlying_points(
        self,
    ) -> None:
        material = rows()
        for row in material:
            row.update(
                value="0.01",
                previous_close="1.00",
                change="-1.00",
                change_percent="-100.01",
                arithmetic_basis="source_reported",
            )
        outcome = DeterministicFixedIndexAdapter(
            material,
            session_adapter=DeterministicMarketSessionAdapter(
                calendars(), clock=lambda: NOW
            ),
            clock=lambda: NOW,
        ).execute(request(), 1)
        self.assertEqual(outcome.coverage, "failed")
        self.assertTrue(
            all(
                error["code"] == "index_arithmetic_mismatch"
                for error in outcome.source_errors
            )
        )

    def test_midday_cannot_accept_midnight_preopen_afternoon_or_missing_latest_boundary(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 12, tzinfo=TZ)
        for timestamp in (
            "2026-10-14T00:00:00+08:00",
            "2026-10-14T09:14:59+08:00",
            "2026-10-14T09:25:00+08:00",
            "2026-10-14T11:30:00+08:00",
            "2026-10-14T12:00:00+08:00",
            "2026-10-14T13:00:00+08:00",
        ):
            material = rows()
            for row in material:
                row.update(observed_at=timestamp, retrieved_at=now.isoformat())
            outcome = DeterministicFixedIndexAdapter(
                material,
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: now
                ),
                clock=lambda: now,
            ).execute(request(), 1)
            with self.subTest(timestamp=timestamp):
                self.assertEqual(outcome.coverage, "failed")

    def test_latest_quote_boundary_cannot_make_a_nonmorning_clock_compatible(
        self,
    ) -> None:
        now = datetime(2026, 10, 14, 12, tzinfo=TZ)
        for timestamp in (
            "2026-10-14T00:00:00+08:00",
            "2026-10-14T09:29:59+08:00",
            "2026-10-14T11:30:01+08:00",
            "2026-10-14T12:00:00+08:00",
        ):
            material = rows()
            for row in material:
                row.update(
                    observed_at="2026-10-14T11:29:55+08:00",
                    retrieved_at=now.isoformat(),
                    observation_boundary="latest_source_quote",
                )
            material[0]["observed_at"] = timestamp
            outcome = DeterministicFixedIndexAdapter(
                material,
                session_adapter=DeterministicMarketSessionAdapter(
                    calendars(), clock=lambda: now
                ),
                clock=lambda: now,
            ).execute(request(), 1)
            with self.subTest(timestamp=timestamp):
                self.assertEqual(outcome.coverage, "partial")
                self.assertEqual(len(outcome.observations), 3)
                self.assertEqual(
                    outcome.source_errors[0]["code"],
                    "index_morning_observation_unproved",
                )


if __name__ == "__main__":
    unittest.main()
