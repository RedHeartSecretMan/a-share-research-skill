from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from a_share_research.close_sources import TencentDailyLineOperation  # noqa: E402
from a_share_research.intraday_sources import TencentIntradayOperation  # noqa: E402
from a_share_research.research_result import (  # noqa: E402
    UnifiedResearchResult,
    stable_result_id,
)

SNAPSHOT_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "intraday_snapshot" / "fixture_cli.py"
)
REPLAY_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "intraday_replay" / "fixture_cli.py"
)
UNIFIED_RESULT_KEYS = {
    "schema_version",
    "status",
    "research_anchor",
    "plan",
    "capability_registry",
    "coverage",
    "observations",
    "claims",
    "calculations",
    "conflicts",
    "limitations",
    "source_errors",
    "task_views",
    "briefs",
}


def intraday_request(task_type: str, *, allow_experimental: bool) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": task_type,
        "subjects": [{"security": "SSE:600519"}],
        "as_of": "2026-08-03"
        if task_type == "intraday_market_signal"
        else "2026-08-04",
        "window": (
            None
            if task_type == "intraday_market_signal"
            else {
                "observed_from": "2026-08-03",
                "observed_to": "2026-08-03",
            }
        ),
        "parameters": {},
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def invoke_copied_installed(
    request: dict[str, object],
    *,
    fixture: bool = False,
    fixture_environment: dict[str, str] | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        installed = temporary_root / "skill" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = temporary_root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        environment = os.environ.copy()
        command = [sys.executable]
        if fixture:
            snapshot_copy = (
                temporary_root
                / "tests"
                / "fixtures"
                / "intraday_snapshot"
                / "fixture_cli.py"
            )
            replay_copy = (
                temporary_root
                / "tests"
                / "fixtures"
                / "intraday_replay"
                / "fixture_cli.py"
            )
            snapshot_copy.parent.mkdir(parents=True)
            replay_copy.parent.mkdir(parents=True)
            shutil.copy2(SNAPSHOT_FIXTURE, snapshot_copy)
            shutil.copy2(REPLAY_FIXTURE, replay_copy)
            sitecustomize = temporary_root / "sitecustomize.py"
            sitecustomize.write_text(
                textwrap.dedent(
                    f"""
                    import importlib.util
                    import sys

                    scripts = {str(installed / "scripts")!r}
                    sys.path.insert(0, scripts)

                    def load(name, path):
                        spec = importlib.util.spec_from_file_location(name, path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        return module

                    snapshot = load('_snapshot_fixture', {str(snapshot_copy)!r})
                    replay = load('_replay_fixture', {str(replay_copy)!r})
                    import a_share_research.cli as cli
                    original_research = cli.research

                    def fixture_research(request, **kwargs):
                        if request['task_type'] == 'intraday_market_signal':
                            operations = (
                                snapshot.TongdaxinIntradayOperation(snapshot._client_factory),
                                snapshot.FixtureTencentIntradayOperation(snapshot.FixtureTransport()),
                            )
                            return original_research(
                                request,
                                research_now=snapshot.RETRIEVED_AT,
                                available_optional_dependencies={{'mootdx==0.11.7'}},
                                intraday_operations=operations,
                            )
                        daily = (replay.FixtureIntradayReplayDailyOperation(),)
                        return original_research(
                            request,
                            research_now=replay._research_now(),
                            intraday_replay_operations=(replay.FixtureIntradayReplayOperation(),),
                            intraday_replay_daily_operations=daily,
                        )

                    cli.research = fixture_research
                    """
                ),
                encoding="utf-8",
            )
            environment["PYTHONPATH"] = str(temporary_root)
            environment["PYTHONNOUSERSITE"] = "1"
            environment["A_SHARE_INTRADAY_REPLAY_SCENARIO"] = "daily_agreement"
            environment.update(fixture_environment or {})
        else:
            command.append("-I")
        command.extend(
            [
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ]
        )
        completed = subprocess.run(
            command,
            cwd=temporary_root,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    loaded = json.loads(completed.stdout)
    if not isinstance(loaded, dict):
        raise AssertionError("public result must be a JSON object")
    return loaded


def rehash_result(result: dict[str, object]) -> dict[str, object]:
    value = deepcopy(result)

    def replace_references(item: object, replacements: dict[str, str]) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if isinstance(nested, str) and nested in replacements:
                    item[key] = replacements[nested]
                else:
                    replace_references(nested, replacements)
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                if isinstance(nested, str) and nested in replacements:
                    item[index] = replacements[nested]
                else:
                    replace_references(nested, replacements)

    replacements: dict[str, str] = {}
    for observation in value["observations"]:  # type: ignore[union-attr]
        old = observation["id"]
        observation["id"] = stable_result_id(
            "observation",
            {
                "capability_id": observation["capability_id"],
                "operation_id": observation["operation_id"],
                "payload": observation["payload"],
            },
        )
        replacements[old] = observation["id"]
    replace_references(value, replacements)
    replacements = {}
    for field, kind in (
        ("claims", "claim"),
        ("calculations", "calculation"),
        ("conflicts", "conflict"),
        ("limitations", "limitation"),
    ):
        for item in value[field]:  # type: ignore[index]
            old = item["id"]
            item["id"] = stable_result_id(
                kind, {key: nested for key, nested in item.items() if key != "id"}
            )
            replacements[old] = item["id"]
    replace_references(value, replacements)
    brief = value["briefs"][0]  # type: ignore[index]
    old_brief = brief["id"]
    brief["id"] = stable_result_id(
        "brief", {key: nested for key, nested in brief.items() if key != "id"}
    )
    replace_references(value, {old_brief: brief["id"]})
    return value


def validate_serialized(result: dict[str, object]) -> None:
    UnifiedResearchResult(
        status=result["status"],  # type: ignore[arg-type]
        research_anchor=result["research_anchor"],  # type: ignore[arg-type]
        plan=result["plan"],  # type: ignore[arg-type]
        capability_registry=result["capability_registry"],  # type: ignore[arg-type]
        coverage=result["coverage"],  # type: ignore[arg-type]
        observations=tuple(result["observations"]),  # type: ignore[arg-type]
        claims=tuple(result["claims"]),  # type: ignore[arg-type]
        calculations=tuple(result["calculations"]),  # type: ignore[arg-type]
        conflicts=tuple(result["conflicts"]),  # type: ignore[arg-type]
        limitations=tuple(result["limitations"]),  # type: ignore[arg-type]
        source_errors=tuple(result["source_errors"]),  # type: ignore[arg-type]
        task_views=result["task_views"],  # type: ignore[arg-type]
        briefs=tuple(result["briefs"]),  # type: ignore[arg-type]
    ).to_dict()


class DirectIntradayPublicSeamTests(unittest.TestCase):
    def assert_formal_result(self, result: dict[str, object], task_type: str) -> None:
        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["plan"]["task_type"], task_type)  # type: ignore[index]
        self.assertEqual(
            result["research_anchor"],
            {
                "as_of": "2026-08-03"
                if task_type == "intraday_market_signal"
                else "2026-08-04",
                "timezone": "Asia/Shanghai",
            },
        )
        self.assertNotIn("task_type", result)

    def disposition_fields(
        self, result: dict[str, object], capability: str
    ) -> dict[str, set[str]]:
        operations = result["coverage"][capability]["operations"]  # type: ignore[index]
        return {
            operation_id: {item["field"] for item in operation["field_dispositions"]}
            for operation_id, operation in operations.items()
        }

    def operation_dispositions(
        self, result: dict[str, object], capability: str
    ) -> list[dict[str, object]]:
        operations = result["coverage"][capability]["operations"]  # type: ignore[index]
        return next(iter(operations.values()))["field_dispositions"]

    def assert_destination_resolves(
        self,
        result: dict[str, object],
        operation_id: str,
        destination: str,
    ) -> None:
        prefix = "observations.payload."
        self.assertTrue(destination.startswith(prefix))
        components = destination.removeprefix(prefix).split(".")
        payloads = [
            item["payload"]
            for item in result["observations"]  # type: ignore[union-attr]
            if item["operation_id"] == operation_id
            or item["operation_id"].endswith(f"::{operation_id}")
        ]

        def resolves(value: object, remaining: list[str]) -> bool:
            if not remaining:
                return True
            if isinstance(value, list):
                return bool(value) and all(resolves(item, remaining) for item in value)
            return (
                isinstance(value, dict)
                and remaining[0] in value
                and resolves(value[remaining[0]], remaining[1:])
            )

        self.assertTrue(
            any(resolves(payload, components) for payload in payloads),
            destination,
        )

    def test_copied_installed_snapshot_policy_block_is_exact_formal_result(
        self,
    ) -> None:
        result = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=False)
        )

        self.assert_formal_result(result, "intraday_market_signal")
        self.assertEqual(result["status"], "blocked")

    def test_copied_installed_snapshot_fixture_is_exact_formal_result(self) -> None:
        result = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
        )

        self.assert_formal_result(result, "intraday_market_signal")
        self.assertEqual(result["status"], "limited")
        self.assertTrue(result["observations"])

    def test_snapshot_raw_dispositions_are_stable_across_execution_states(self) -> None:
        policy = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=False)
        )
        success = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
        )
        failure = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_SCENARIO": "tencent_failure"},
        )
        expected = self.disposition_fields(policy, "intraday_market_snapshot")
        self.assertEqual(
            self.disposition_fields(success, "intraday_market_snapshot"), expected
        )
        self.assertEqual(
            self.disposition_fields(failure, "intraday_market_snapshot"), expected
        )

    def test_snapshot_tencent_consumes_the_versioned_typed_daily_seam(self) -> None:
        self.assertEqual(
            {item.field for item in TencentIntradayOperation.field_dispositions},
            {item.field for item in TencentDailyLineOperation.field_dispositions},
        )
        self.assertIs(
            TencentIntradayOperation.collect.__globals__["TencentDailyLineOperation"],
            TencentDailyLineOperation,
        )

    def test_copied_installed_replay_policy_block_is_exact_formal_result(self) -> None:
        result = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=False)
        )

        self.assert_formal_result(result, "intraday_replay")
        self.assertEqual(result["status"], "blocked")

    def test_copied_installed_replay_dependency_limitation_is_capability_scoped(
        self,
    ) -> None:
        result = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True)
        )

        self.assert_formal_result(result, "intraday_replay")
        limitation = next(
            item
            for item in result["limitations"]  # type: ignore[union-attr]
            if item["code"] == "missing_optional_dependency"
        )
        self.assertIn("capability=intraday_replay", limitation["message"])
        self.assertIn("dependency=mootdx", limitation["message"])
        self.assertIn("required_version=0.11.7", limitation["message"])

    def test_copied_installed_replay_fixture_is_exact_formal_result(self) -> None:
        result = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )

        self.assert_formal_result(result, "intraday_replay")
        self.assertEqual(result["status"], "limited")
        self.assertTrue(result["observations"])

    def test_intraday_locators_are_sanitized_before_public_outcomes(self) -> None:
        snapshot = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_SCENARIO": "unsafe_locator"},
        )
        replay = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "unsafe_locator"},
        )
        for result in (snapshot, replay):
            serialized = json.dumps(result, sort_keys=True)
            self.assertNotIn("internal.invalid", serialized)
            self.assertNotIn("SECRET_76", serialized)
            self.assertNotIn("token", serialized.casefold())

    def test_serializer_rejects_rehashed_unsafe_intraday_locators(self) -> None:
        snapshot = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
        )
        replay = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )
        forged_snapshot = deepcopy(snapshot)
        forged_snapshot["observations"][0]["payload"]["provenance"][0]["locator"] = {
            "uri": "https://internal.invalid/q?token=SNAPSHOT_SECRET_76"
        }
        with self.assertRaises(ValueError):
            validate_serialized(rehash_result(forged_snapshot))
        forged_replay = deepcopy(replay)
        raw = next(
            item["payload"]
            for item in forged_replay["observations"]
            if item["payload"].get("kind") == "intraday_replay_source_batch"
        )
        raw["source_records"][0]["evidence_locator"] = (
            "https://internal.invalid/bars?token=SYNTHETIC_SECRET_76"
        )
        with self.assertRaises(ValueError):
            validate_serialized(rehash_result(forged_replay))

    def test_replay_raw_dispositions_are_stable_across_five_execution_states(
        self,
    ) -> None:
        policy = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=False)
        )
        dependency = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True)
        )
        success = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )
        empty = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "empty_response"},
        )
        failure = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "source_failure"},
        )
        field_sets = [
            next(
                iter(
                    self.disposition_fields(result, "intraday_replay_sequence").values()
                )
            )
            for result in (policy, dependency, success, empty, failure)
        ]
        self.assertTrue(field_sets[0])
        self.assertTrue(all(fields == field_sets[0] for fields in field_sets[1:]))
        self.assertNotIn("operation_id", field_sets[0])
        self.assertNotIn("contract_version", field_sets[0])
        self.assertIn("datetime", field_sets[0])
        self.assertIn("open", field_sets[0])
        self.assertIn("amount_scale", field_sets[0])
        disposition_sets = [
            self.operation_dispositions(result, "intraday_replay_sequence")
            for result in (policy, dependency, success, empty, failure)
        ]
        self.assertTrue(
            all(items == disposition_sets[0] for items in disposition_sets[1:])
        )
        by_field = {item["field"]: item for item in disposition_sets[0]}
        self.assertEqual(
            by_field["price_unit"]["destination"],
            "observations.payload.contract.price_unit",
        )
        self.assertEqual(
            by_field["session_contract"]["destination"],
            "observations.payload.contract.session_contract",
        )
        self.assertEqual(
            by_field["coverage_bound"]["destination"],
            "observations.payload.contract.coverage_bound",
        )
        self.assertEqual(
            by_field["timestamp_semantics"]["destination"],
            "observations.payload.source_records.timestamp_semantics",
        )
        for item in disposition_sets[0]:
            if item["disposition"] == "excluded":
                self.assertIsNone(item.get("destination"))
                self.assertTrue(item.get("reason"))
            else:
                self.assert_destination_resolves(
                    success,
                    "fixture_intraday_replay@1",
                    item["destination"],
                )

    def test_replay_retains_valid_batch_when_calendar_adoption_fails(self) -> None:
        result = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "short_calendar"},
        )

        self.assert_formal_result(result, "intraday_replay")
        self.assertEqual(result["status"], "blocked")
        replay_observations = [
            item
            for item in result["observations"]  # type: ignore[union-attr]
            if item["payload"]["kind"] == "intraday_replay_source_batch"
        ]
        self.assertEqual(len(replay_observations), 1)
        self.assertTrue(replay_observations[0]["payload"]["source_records"])
        self.assertEqual(result["source_errors"], [])

    def test_serializer_rejects_rehashed_blocked_retained_replay_tampering(
        self,
    ) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "daily_conflict"},
        )

        def normalized(result: dict[str, object]) -> dict[str, Any]:
            return next(
                item["payload"]
                for item in result["observations"]  # type: ignore[union-attr]
                if item["payload"].get("kind") == "intraday_replay_normalized_records"
            )

        mutations = {
            "ohlc": lambda result: normalized(result)["records"][0]["ohlc"][
                "close"
            ].update(value="999.99"),
            "timestamp": lambda result: normalized(result)["records"][0].update(
                source_timestamp="2026-08-03T09:35:00+08:00"
            ),
            "trade_state": lambda result: normalized(result)["records"][0].update(
                trade_state="no_trade"
            ),
            "unit": lambda result: normalized(result)["records"][0]["volume"].update(
                unit="hands"
            ),
            "coverage_operand": lambda result: normalized(result)["coverage"][
                "coverage_ratio"
            ].update(covered_minutes=999),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                forged = deepcopy(original)
                mutate(forged)
                with self.assertRaises(ValueError):
                    validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_formula_and_operand_tampering(self) -> None:
        snapshot = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_SCENARIO": "comparable_prev_close"},
        )
        replay = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )
        for result, kind, field, forged_value in (
            (snapshot, "intraday_change_amount", "formula", "forged"),
            (snapshot, "intraday_change_percent", "operands", {}),
            (replay, "intraday_replay_coverage", "formula", "forged"),
            (replay, "intraday_replay_summary", "operands", {}),
            (replay, "intraday_daily_boundary_cross_check", "formula", "forged"),
        ):
            with self.subTest(kind=kind, field=field):
                forged = deepcopy(result)
                calculation = next(
                    item for item in forged["calculations"] if item["kind"] == kind
                )
                calculation["payload"][field] = forged_value
                with self.assertRaises(ValueError):
                    validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_snapshot_owned_fact_tampering(self) -> None:
        success = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
        )
        blocked = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_SCENARIO": "core_price_mismatch"},
        )
        mutations = (
            (
                success,
                lambda result: result["observations"][0]["payload"]["subject"].update(
                    security="SSE:600000"
                ),
            ),
            (
                success,
                lambda result: result["observations"][0]["payload"].update(
                    trading_date="2026-08-02"
                ),
            ),
            (
                blocked,
                lambda result: result["observations"][0]["payload"][
                    "cumulative"
                ].update(volume="999"),
            ),
        )
        for original, mutate in mutations:
            forged = deepcopy(original)
            mutate(forged)
            with self.assertRaises(ValueError):
                validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_snapshot_adjudication_tampering(self) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_market_signal", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_SCENARIO": "comparable_prev_close"},
        )

        def claim(result: dict[str, Any]) -> dict[str, Any]:
            return next(
                item["payload"]
                for item in result["claims"]
                if item["kind"] == "intraday_market_snapshot"
            )

        mutations = {
            "session_state": lambda result: claim(result).update(
                session_state="closing_auction"
            ),
            "trading_status": lambda result: claim(result).update(
                trading_status="auction"
            ),
            "price_type": lambda result: claim(result).update(price_type="indicative"),
            "cumulative_amount": lambda result: claim(result)["snapshot"][
                "cumulative_amount"
            ].update(value="999.00"),
            "previous_close_basis": lambda result: claim(result)["snapshot"][
                "previous_close"
            ].update(basis="ex_right_reference"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                forged = deepcopy(original)
                mutate(forged)
                with self.assertRaises(ValueError):
                    validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_replay_identity_and_daily_tampering(
        self,
    ) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )

        def replay_claim(result: dict[str, Any]) -> dict[str, Any]:
            return next(
                item["payload"]
                for item in result["claims"]
                if item["kind"] == "intraday_replay"
            )

        mutations = {
            "security": lambda result: replay_claim(result)["replay"].update(
                security="SSE:600000"
            ),
            "trading_date": lambda result: replay_claim(result)["replay"].update(
                trading_date="2026-08-02"
            ),
            "adjustment": lambda result: replay_claim(result)["replay"].update(
                adjustment="forward_adjusted"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                forged = deepcopy(original)
                mutate(forged)
                with self.assertRaises(ValueError):
                    validate_serialized(rehash_result(forged))

        forged = deepcopy(original)
        forged_boundary = deepcopy(replay_claim(forged)["daily_boundary"])
        forged_boundary["open"]["value"] = "99.99"
        replay_claim(forged)["daily_boundary"] = forged_boundary
        next(
            item
            for item in forged["claims"]
            if item["kind"] == "intraday_daily_boundary"
        )["payload"] = deepcopy(forged_boundary)
        next(
            item
            for item in forged["calculations"]
            if item["kind"] == "intraday_daily_boundary_cross_check"
        )["payload"]["result"] = deepcopy(forged_boundary)
        with self.assertRaises(ValueError):
            validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_unadopted_raw_row_schema_tampering(
        self,
    ) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "short_calendar"},
        )

        def raw_row(result: dict[str, Any]) -> dict[str, Any]:
            return next(
                item["payload"]["source_records"][0]
                for item in result["observations"]
                if item["payload"].get("kind") == "intraday_replay_source_batch"
            )

        mutations = {
            "trade_state": lambda result: raw_row(result).update(trade_state="forged"),
            "timestamp_semantics": lambda result: raw_row(result).update(
                timestamp_semantics="forged"
            ),
            "price": lambda result: raw_row(result).update(open_price="NaN"),
            "volume": lambda result: raw_row(result).update(volume="not-a-decimal"),
            "amount": lambda result: raw_row(result).update(amount="Infinity"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                forged = deepcopy(original)
                mutate(forged)
                with self.assertRaises(ValueError):
                    validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_raw_value_and_provider_id_tampering(
        self,
    ) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
            fixture_environment={"A_SHARE_INTRADAY_REPLAY_SCENARIO": "short_calendar"},
        )
        forged = deepcopy(original)
        raw = next(
            item["payload"]
            for item in forged["observations"]
            if item["payload"].get("kind") == "intraday_replay_source_batch"
        )
        raw["source_records"][0]["open_price"] = "999.99"
        raw["source_records"][0]["provider_observation_id"] = (
            "fixture_intraday_replay@1:provider:forged"
        )
        with self.assertRaises(ValueError):
            validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_replay_interval_outside_session(self) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )
        forged = deepcopy(original)
        normalized = next(
            item["payload"]
            for item in forged["observations"]
            if item["payload"].get("kind") == "intraday_replay_normalized_records"
        )
        normalized["records"][0]["interval_start"] = "2026-08-03T09:29:00+08:00"
        normalized["records"][0]["interval_end"] = "2026-08-03T09:30:00+08:00"
        replay_claim = next(
            item["payload"]
            for item in forged["claims"]
            if item["kind"] == "intraday_replay"
        )
        replay_claim["records"] = deepcopy(normalized["records"])
        with self.assertRaises(ValueError):
            validate_serialized(rehash_result(forged))

    def test_serializer_rejects_rehashed_replay_summary_metric_tampering(self) -> None:
        original = invoke_copied_installed(
            intraday_request("intraday_replay", allow_experimental=True),
            fixture=True,
        )
        forged = deepcopy(original)
        replay_claim = next(
            item["payload"]
            for item in forged["claims"]
            if item["kind"] == "intraday_replay"
        )
        replay_claim["summary"]["metrics"]["high"]["value"] = "999.99"
        summary_calculation = next(
            item
            for item in forged["calculations"]
            if item["kind"] == "intraday_replay_summary"
        )
        summary_calculation["payload"]["result"] = deepcopy(replay_claim["summary"])
        with self.assertRaises(ValueError):
            validate_serialized(rehash_result(forged))


if __name__ == "__main__":
    unittest.main()
