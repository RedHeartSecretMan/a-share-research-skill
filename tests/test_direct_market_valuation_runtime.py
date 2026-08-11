from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import a_share_research.automatic_valuation as automatic_valuation_module  # noqa: E402
from a_share_research.automatic_valuation import (  # noqa: E402
    build_security_valuation_result,
    build_valuation_comparison_result,
)
from a_share_research.close_observation import execute_close_research  # noqa: E402
from a_share_research.etf_market import build_etf_market_result  # noqa: E402
from a_share_research.market_series import build_market_trend_result  # noqa: E402
from a_share_research.research_result import (  # noqa: E402
    DirectTaskClaim,
    DirectTaskLimitation,
    DirectTaskPlan,
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    UnifiedResearchResult,
    fold_direct_task_research_result,
    stable_result_id,
)

MARKET_FIXTURE_CLI = REPOSITORY_ROOT / "tests/fixtures/market_series/fixture_cli.py"
VALUATION_FIXTURE_CLI = (
    REPOSITORY_ROOT / "tests/fixtures/valuation_research/fixture_cli.py"
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


def load_fixture_transport(path: Path, class_name: str) -> object:
    spec = importlib.util.spec_from_file_location(f"_{class_name}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load fixture transport from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)()


def destination_exists(observation: dict[str, object], destination: str) -> bool:
    parts = destination.split(".")
    if not parts or parts[0] != "observations":
        return False
    value: object = observation
    for part in parts[1:]:
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def rehash_direct_consumer(
    result: UnifiedResearchResult,
    *,
    collection: str,
    index: int,
) -> None:
    records = getattr(result, collection)
    record = records[index]
    old_id = record["id"]
    prefix = "claim" if collection == "claims" else "calculation"
    reference_field = "claim_ids" if collection == "claims" else "calculation_ids"
    record["id"] = stable_result_id(
        prefix, {key: value for key, value in record.items() if key != "id"}
    )
    new_id = record["id"]
    brief = result.briefs[0]
    brief[reference_field] = [
        new_id if item == old_id else item for item in brief[reference_field]
    ]
    brief["id"] = stable_result_id(
        "brief", {key: value for key, value in brief.items() if key != "id"}
    )
    task_type = result.plan["task_type"]
    view = result.task_views[task_type]
    view[reference_field] = [
        new_id if item == old_id else item for item in view[reference_field]
    ]
    view["brief_id"] = brief["id"]


def invoke_copied_installed(
    request_payload: dict[str, object],
    *,
    market_fixtures: bool = False,
    valuation_fixtures: bool = False,
    fail_url_contains: str | None = None,
    fixture_scenario: str | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        installed = temporary_root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = temporary_root / "request.json"
        request_path.write_text(json.dumps(request_payload), encoding="utf-8")
        environment = os.environ.copy()
        command = [sys.executable]
        if market_fixtures or valuation_fixtures:
            fixture_path = (
                VALUATION_FIXTURE_CLI if valuation_fixtures else MARKET_FIXTURE_CLI
            )
            transport_factory = (
                "module.ValuationFixtureTransport()"
                if valuation_fixtures
                else "module.FixtureTransport()"
            )
            failure_lines = (
                (
                    "base_transport = transport",
                    "class FailingTransport:",
                    "    def get(self, url, headers):",
                    f"        if {fail_url_contains!r} in url:",
                    "            response = base_transport.get(url, headers)",
                    "            return module.HttpResponse(status=503, content_type=response.content_type, body=response.body, retrieved_at=response.retrieved_at)",
                    "        return base_transport.get(url, headers)",
                    "transport = FailingTransport()",
                )
                if fail_url_contains is not None
                else ()
            )
            sitecustomize = temporary_root / "sitecustomize.py"
            sitecustomize.write_text(
                "\n".join(
                    (
                        "from email.message import Message",
                        "import importlib.util",
                        "import io",
                        "import sys",
                        "import urllib.request",
                        f"repository_root = {str(REPOSITORY_ROOT)!r}",
                        f"fixture_path = {str(fixture_path)!r}",
                        "sys.path.insert(0, repository_root)",
                        "spec = importlib.util.spec_from_file_location('_market_fixture', fixture_path)",
                        "module = importlib.util.module_from_spec(spec)",
                        "spec.loader.exec_module(module)",
                        f"transport = {transport_factory}",
                        *failure_lines,
                        "for name in tuple(sys.modules):",
                        "    if name == 'a_share_research' or name.startswith('a_share_research.'):",
                        "        del sys.modules[name]",
                        "sys.path[:] = [item for item in sys.path if repository_root not in item]",
                        "class FixtureResponse:",
                        "    def __init__(self, response):",
                        "        self.status = response.status",
                        "        self.stream = io.BytesIO(response.body)",
                        "        self.headers = Message()",
                        "        self.headers['Content-Type'] = response.content_type",
                        "    def __enter__(self): return self",
                        "    def __exit__(self, *args): return None",
                        "    def read(self, limit=-1): return self.stream.read(limit)",
                        "class FixtureOpener:",
                        "    def open(self, request, timeout):",
                        "        return FixtureResponse(transport.get(request.full_url, dict(request.header_items())))",
                        "urllib.request.build_opener = lambda: FixtureOpener()",
                    )
                ),
                encoding="utf-8",
            )
            environment["PYTHONPATH"] = str(temporary_root)
            environment["PYTHONNOUSERSITE"] = "1"
            if fixture_scenario is not None:
                environment["A_SHARE_RESEARCH_TEST_SCENARIO"] = fixture_scenario
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


def build_invariant_result(*, include_second_claim: bool = False):
    observation = {
        "id": "local-observation",
        "source_operation": "example_operation@1",
        "value": "1",
    }
    outcome = OperationOutcome(
        capability_id="example_capability",
        operation_id="example_operation@1",
        execution=OperationExecution("succeeded", query_complete=True),
        observations=(observation,),
        field_dispositions=(
            FieldDisposition("value", "normalized", "observations.payload.value"),
        ),
    )
    claims = [
        DirectTaskClaim(
            kind="same_consumer",
            evidence_use="use_a",
            observation_keys=("local-observation",),
            payload={"value": "1"},
        )
    ]
    if include_second_claim:
        claims.append(
            DirectTaskClaim(
                kind="same_consumer",
                evidence_use="use_b",
                observation_keys=("local-observation",),
                payload={"value": "1"},
            )
        )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="invariant_task",
            as_of="2026-08-11",
            obligations=(
                OperationObligation(
                    capability_id="example_capability",
                    operation_id="example_operation@1",
                    source_role="market_observation",
                    experimental=False,
                    exchange=None,
                    evidence_uses=(
                        EvidenceUsePolicy("use_a", "supporting"),
                        EvidenceUsePolicy("use_b", "supporting"),
                    ),
                ),
            ),
            dependencies=(
                ResearchDependency("same_consumer", "use_a", 1, True),
                ResearchDependency("same_consumer", "use_b", 1, True),
            ),
        ),
        (outcome,),
        claims=claims,
        limitations=(DirectTaskLimitation("explicit_limitation", "Test limitation."),),
        summary="Invariant test result.",
    )


class DirectMarketValuationPublicSeamTests(unittest.TestCase):
    def test_market_trend_consumes_typed_identity_without_public_serialization(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "market_trend",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": "2026-08-02",
            "window": {"trading_days": 10},
            "parameters": {"adjustment": "unadjusted"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": False,
            },
        }
        transport = load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport")

        with patch.object(
            UnifiedResearchResult,
            "to_dict",
            side_effect=AssertionError("domain code serialized the identity result"),
        ):
            result = build_market_trend_result(
                request,
                transport,
                datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
            )

        self.assertIsInstance(result, UnifiedResearchResult)

    def test_valuation_consumes_typed_identity_without_public_serialization(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )

        with patch.object(
            UnifiedResearchResult,
            "to_dict",
            side_effect=AssertionError("domain code serialized the identity result"),
        ):
            result = build_security_valuation_result(
                request,
                transport,
                datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
            )

        self.assertIsInstance(result, UnifiedResearchResult)

    def test_active_raw_fields_have_complete_resolvable_dispositions(self) -> None:
        market_transport = load_fixture_transport(
            MARKET_FIXTURE_CLI, "FixtureTransport"
        )
        valuation_transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        source_policy = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        }
        unadjusted = build_market_trend_result(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {"trading_days": 5},
                "parameters": {"adjustment": "unadjusted"},
                "source_policy": source_policy,
            },
            market_transport,
            research_now,
        ).to_dict()
        adjusted = build_market_trend_result(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {"trading_days": 5},
                "parameters": {"adjustment": "forward_adjusted"},
                "source_policy": source_policy,
            },
            market_transport,
            research_now,
        ).to_dict()
        etf = build_etf_market_result(
            {
                "schema_version": "1.0",
                "task_type": "etf_market",
                "subjects": [{"clue": "510050"}],
                "as_of": "2026-08-02",
                "window": None,
                "parameters": {},
                "source_policy": source_policy,
            },
            market_transport,
            research_now,
        ).to_dict()
        valuation = build_security_valuation_result(
            {
                "schema_version": "1.0",
                "task_type": "security_valuation",
                "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
                "as_of": "2026-08-02",
                "window": None,
                "parameters": {"target_pe": "30"},
                "source_policy": source_policy,
            },
            valuation_transport,
            research_now,
        ).to_dict()
        results = (unadjusted, adjusted, etf, valuation)
        expected_fields = {
            "sse_daily_line@1": {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            },
            "szse_daily_line@1": {f"row[{index}]" for index in range(9)},
            "tencent_daily_line@1": {
                *(f"day[{index}]" for index in range(7)),
                "qt[2]",
                "qt[3]",
                "qt[4]",
                "qt[6]",
                "qt[30]",
                "qt[31]",
                "qt[32]",
                "qt[33]",
                "qt[34]",
            },
            "eastmoney_forward_adjusted_daily_line@1": {
                *(f"f{index}" for index in range(1, 7)),
                *(f"f{index}" for index in range(51, 62)),
            },
            "tencent_forward_adjusted_daily_line@1": {
                *(f"qfqday[{index}]" for index in range(7)),
                "qt[2]",
                "qt[30]",
            },
            "sse_etf_list@1": {
                "fundCode",
                "fundAbbr",
                "secNameFull",
                "companyName",
                "listingDate",
                "fundType",
                "subClass",
            },
            "sse_etf_snapshot@1": {
                "name",
                "cpxxextendname",
                "open",
                "high",
                "low",
                "last",
                "prev_close",
                "chg_rate",
                "volume",
                "amount",
                "tradephase",
                "code",
                "date",
                "time",
            },
            "eastmoney_stock_info@1": {
                "f57",
                "f58",
                "f84",
                "f85",
                "f116",
                "f117",
                "f189",
                "f43",
            },
        }
        operation_instances = [
            (operation_id, result, operation)
            for result in results
            for capability in result["coverage"].values()
            for operation_id, operation in capability["operations"].items()
            if operation_id in expected_fields
        ]

        self.assertEqual(
            {operation_id for operation_id, _, _ in operation_instances},
            set(expected_fields),
        )
        for operation_id, fields in expected_fields.items():
            for _, result, operation in (
                item for item in operation_instances if item[0] == operation_id
            ):
                with self.subTest(
                    operation_id=operation_id,
                    execution_state=operation["execution_state"],
                ):
                    dispositions = operation["field_dispositions"]
                    self.assertEqual({item["field"] for item in dispositions}, fields)
                    owned_observations = [
                        item
                        for item in result["observations"]
                        if item["operation_id"] == operation_id
                    ]
                    for disposition in dispositions:
                        if disposition["disposition"] == "excluded":
                            self.assertIsNone(disposition["destination"])
                            self.assertTrue(disposition["reason"])
                        elif owned_observations:
                            self.assertTrue(
                                any(
                                    destination_exists(item, disposition["destination"])
                                    for item in owned_observations
                                ),
                                (operation_id, disposition),
                            )

    def test_copied_installed_market_trend_policy_block_is_formal_unified_result(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "market_trend",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": "2026-08-02",
            "window": {"trading_days": 10},
            "parameters": {"adjustment": "unadjusted"},
            "source_policy": {
                "allow_experimental": False,
                "allow_credentials": False,
                "allow_fallback": False,
            },
        }

        result = invoke_copied_installed(request)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["research_anchor"],
            {"as_of": "2026-08-02", "timezone": "Asia/Shanghai"},
        )
        self.assertEqual(result["plan"]["task_type"], "market_trend")
        self.assertNotIn("task_type", result)
        view = result["task_views"]["market_trend"]
        self.assertEqual(view["status"], "blocked")
        self.assertEqual(view["limitation_ids"], [result["limitations"][0]["id"]])
        self.assertEqual(view["brief_id"], result["briefs"][0]["id"])

    def test_copied_installed_market_trend_retains_owned_observations_and_refs(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "market_trend",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": date.today().isoformat(),
            "window": {"trading_days": 10},
            "parameters": {"adjustment": "unadjusted"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": False,
            },
        }

        result = invoke_copied_installed(request, market_fixtures=True)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        self.assertTrue(result["observations"])
        observation_ids = {item["id"] for item in result["observations"]}
        for capability in result["coverage"].values():
            for operation_id, operation in capability["operations"].items():
                for observation_id in operation["observation_ids"]:
                    observation = next(
                        item
                        for item in result["observations"]
                        if item["id"] == observation_id
                    )
                    self.assertEqual(observation["operation_id"], operation_id)
                self.assertTrue(operation["field_dispositions"])
        self.assertTrue(result["claims"])
        self.assertTrue(result["calculations"])
        self.assertTrue(
            all(
                set(claim["observation_ids"]).issubset(observation_ids)
                for claim in result["claims"]
            )
        )
        self.assertEqual(
            set(result["task_views"]["market_trend"]["observation_ids"]),
            observation_ids,
        )
        daily_fields = {
            "szse_daily_line@1": {f"row[{index}]" for index in range(9)},
            "tencent_daily_line@1": {
                *(f"day[{index}]" for index in range(7)),
                "qt[2]",
                "qt[3]",
                "qt[4]",
                "qt[6]",
                "qt[30]",
                "qt[31]",
                "qt[32]",
                "qt[33]",
                "qt[34]",
            },
        }
        for operation_id, fields in daily_fields.items():
            self.assertEqual(
                {
                    item["field"]
                    for item in result["coverage"]["market_trend_series"]["operations"][
                        operation_id
                    ]["field_dispositions"]
                },
                fields,
            )


class DirectResultInvariantTests(unittest.TestCase):
    def test_dependency_satisfaction_is_scoped_by_consumer_and_evidence_use(
        self,
    ) -> None:
        missing_use = build_invariant_result()
        complete_pairs = build_invariant_result(include_second_claim=True)

        self.assertEqual(missing_use.status, "blocked")
        self.assertEqual(complete_pairs.status, "limited")

    def test_serializer_rejects_forged_status_ids_refs_and_coverage(self) -> None:
        status_tamper = build_invariant_result(include_second_claim=True)
        object.__setattr__(status_tamper, "status", "supported")
        with self.assertRaises(ValueError):
            status_tamper.to_dict()

        id_tamper = build_invariant_result(include_second_claim=True)
        id_tamper.observations[0]["id"] = "observation-forged"
        with self.assertRaises(ValueError):
            id_tamper.to_dict()

        ref_tamper = build_invariant_result(include_second_claim=True)
        ref_tamper.claims[0]["observation_ids"] = ["observation-forged"]
        with self.assertRaises(ValueError):
            ref_tamper.to_dict()

        legacy_ref_tamper = build_invariant_result(include_second_claim=True)
        legacy_ref_tamper.claims[0]["payload"]["evidence_id"] = "local-observation"
        legacy_ref_tamper.claims[0]["id"] = stable_result_id(
            "claim",
            {
                key: value
                for key, value in legacy_ref_tamper.claims[0].items()
                if key != "id"
            },
        )
        with self.assertRaises(ValueError):
            legacy_ref_tamper.to_dict()

        disposition_tamper = build_invariant_result(include_second_claim=True)
        disposition_tamper.coverage["example_capability"]["operations"][
            "example_operation@1"
        ]["field_dispositions"] = []
        with self.assertRaises(ValueError):
            disposition_tamper.to_dict()

        execution_tamper = build_invariant_result(include_second_claim=True)
        execution_tamper.coverage["example_capability"]["operations"][
            "example_operation@1"
        ]["execution_state"] = "failed"
        with self.assertRaises(ValueError):
            execution_tamper.to_dict()

        view_tamper = build_invariant_result(include_second_claim=True)
        view_tamper.task_views["forged_task"] = dict(
            view_tamper.task_views["invariant_task"]
        )
        with self.assertRaises(ValueError):
            view_tamper.to_dict()

    def test_serializer_rejects_rehashed_inner_operation_ownership(self) -> None:
        result = build_invariant_result(include_second_claim=True)
        observation = result.observations[0]
        old_observation_id = observation["id"]
        observation["payload"]["source_operation"] = "sibling_operation@1"
        observation["id"] = stable_result_id(
            "observation",
            {
                "capability_id": observation["capability_id"],
                "operation_id": observation["operation_id"],
                "payload": observation["payload"],
            },
        )
        new_observation_id = observation["id"]
        operation = result.coverage["example_capability"]["operations"][
            "example_operation@1"
        ]
        operation["observation_ids"] = [new_observation_id]
        claim_ids = []
        for claim in result.claims:
            claim["observation_ids"] = [new_observation_id]
            claim["evidence_adoptions"][0]["observation_id"] = new_observation_id
            claim["id"] = stable_result_id(
                "claim", {key: value for key, value in claim.items() if key != "id"}
            )
            claim_ids.append(claim["id"])
        brief = result.briefs[0]
        brief["observation_ids"] = [new_observation_id]
        brief["claim_ids"] = claim_ids
        brief["id"] = stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        )
        view = result.task_views["invariant_task"]
        view["observation_ids"] = [new_observation_id]
        view["claim_ids"] = claim_ids
        view["brief_id"] = brief["id"]
        self.assertNotEqual(old_observation_id, new_observation_id)

        with self.assertRaisesRegex(ValueError, "inner source operation"):
            result.to_dict()

    def test_business_claims_adopt_identity_observations(self) -> None:
        market_transport = load_fixture_transport(
            MARKET_FIXTURE_CLI, "FixtureTransport"
        )
        valuation_transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        policy = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        }
        market = build_market_trend_result(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {"trading_days": 5},
                "parameters": {"adjustment": "unadjusted"},
                "source_policy": policy,
            },
            market_transport,
            research_now,
        ).to_dict()
        valuation = build_security_valuation_result(
            {
                "schema_version": "1.0",
                "task_type": "security_valuation",
                "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
                "as_of": "2026-08-02",
                "window": None,
                "parameters": {"target_pe": "30"},
                "source_policy": policy,
            },
            valuation_transport,
            research_now,
        ).to_dict()
        for result, claim_kind in (
            (market, "market_trend_summary"),
            (valuation, "security_valuation_metrics"),
        ):
            identity_ids = {
                item["id"]
                for item in result["observations"]
                if item["capability_id"] == "security_identity_resolution"
            }
            claim = next(
                item for item in result["claims"] if item["kind"] == claim_kind
            )
            self.assertTrue(identity_ids)
            self.assertTrue(identity_ids.intersection(claim["observation_ids"]))

    def test_serializer_rejects_rehashed_forged_market_calculation(self) -> None:
        transport = load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport")
        result = build_market_trend_result(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {"trading_days": 5},
                "parameters": {"adjustment": "unadjusted"},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                },
            },
            transport,
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        calculation_index = next(
            index
            for index, item in enumerate(result.calculations)
            if item["kind"] == "market_trend_calculation:cumulative_return"
        )
        calculation = result.calculations[calculation_index]
        calculation["payload"]["formula"] = "forged_formula"
        calculation["payload"]["operands"] = {
            "first_close": "1",
            "last_close": "999",
        }
        rehash_direct_consumer(
            result, collection="calculations", index=calculation_index
        )

        with self.assertRaisesRegex(ValueError, "market domain facts"):
            result.to_dict()

    def test_close_material_tamper_cannot_change_valuation_price(self) -> None:
        transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        close_execution = execute_close_research(
            "SSE:601138", "2026-08-02", transport, research_now
        )
        assert close_execution.material.selected_close is not None
        object.__setattr__(close_execution.material.selected_close, "value", "1")
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        with patch.object(
            automatic_valuation_module,
            "execute_close_research",
            return_value=close_execution,
        ):
            with self.assertRaisesRegex(ValueError, "close material"):
                build_security_valuation_result(request, transport, research_now)

    def test_comparison_ignores_tampered_child_public_result(self) -> None:
        transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        original_execute = automatic_valuation_module._execute_security_valuation

        def tampered_execute(*args: object, **kwargs: object) -> object:
            execution = original_execute(*args, **kwargs)
            object.__setattr__(execution.result, "status", "blocked")
            execution.result.claims[0]["payload"] = {"forged": True}
            return execution

        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "工业富联", "issuer_security_class_count": 1},
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
            ],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with patch.object(
            automatic_valuation_module,
            "_execute_security_valuation",
            side_effect=tampered_execute,
        ):
            result = build_valuation_comparison_result(
                request, transport, research_now
            ).to_dict()
        rows = result["claims"][0]["payload"]["rows"]

        self.assertEqual([row["status"] for row in rows], ["limited", "limited"])

    def test_serializer_revalidates_etf_valuation_and_comparison_facts(self) -> None:
        market_transport = load_fixture_transport(
            MARKET_FIXTURE_CLI, "FixtureTransport"
        )
        valuation_transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
        now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        policy = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        }
        etf = build_etf_market_result(
            {
                "schema_version": "1.0",
                "task_type": "etf_market",
                "subjects": [{"clue": "510050"}],
                "as_of": "2026-08-02",
                "window": None,
                "parameters": {},
                "source_policy": policy,
            },
            market_transport,
            now,
        )
        etf.claims[0]["payload"]["quote"]["last"] = "999.0000"
        rehash_direct_consumer(etf, collection="claims", index=0)

        valuation_request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": policy,
        }
        valuation = build_security_valuation_result(
            valuation_request, valuation_transport, now
        )
        market_cap_index = next(
            index
            for index, item in enumerate(valuation.calculations)
            if item["kind"] == "market_capitalization"
        )
        valuation.calculations[market_cap_index]["payload"]["operands"]["price"] = "1"
        rehash_direct_consumer(
            valuation, collection="calculations", index=market_cap_index
        )

        comparison = build_valuation_comparison_result(
            {
                **valuation_request,
                "task_type": "valuation_compare",
                "subjects": [
                    {"clue": "工业富联", "issuer_security_class_count": 1},
                    {"clue": "贵州茅台", "issuer_security_class_count": 1},
                ],
            },
            valuation_transport,
            now,
        )
        comparison.claims[0]["payload"]["rows"][0]["status"] = "blocked"
        comparison.claims[0]["payload"]["rows"][0]["security"] = "SSE:999999"
        rehash_direct_consumer(comparison, collection="claims", index=0)

        for result, message in (
            (etf, "ETF domain facts"),
            (valuation, "valuation domain facts"),
            (comparison, "comparison domain facts"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    result.to_dict()

    def test_copied_installed_etf_market_is_a_direct_unified_result(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "etf_market",
            "subjects": [{"clue": "510050"}],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": False,
            },
        }

        result = invoke_copied_installed(request, market_fixtures=True)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["plan"]["task_type"], "etf_market")
        self.assertTrue(result["observations"])
        self.assertTrue(result["claims"])
        operations = result["coverage"]["etf_market_quote"]["operations"]
        snapshot = operations["sse_etf_snapshot@1"]
        disposition_fields = {item["field"] for item in snapshot["field_dispositions"]}
        self.assertTrue(
            {
                "name",
                "cpxxextendname",
                "open",
                "high",
                "low",
                "last",
                "prev_close",
                "chg_rate",
                "volume",
                "amount",
                "tradephase",
            }.issubset(disposition_fields)
        )
        self.assertEqual(
            {
                item["field"]
                for item in operations["sse_etf_list@1"]["field_dispositions"]
            },
            {
                "fundCode",
                "fundAbbr",
                "secNameFull",
                "companyName",
                "listingDate",
                "fundType",
                "subClass",
            },
        )

    def test_copied_installed_security_valuation_is_a_direct_unified_result(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(request, valuation_fixtures=True)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["plan"]["task_type"], "security_valuation")
        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "security_valuation_metrics"
        )
        basis = claim["payload"]["valuation_basis"]
        self.assertEqual(basis["price"]["unit"], "CNY/share")
        self.assertEqual(basis["effective_total_shares"]["unit"], "shares")
        market_cap = next(
            item
            for item in result["calculations"]
            if item["kind"] == "market_capitalization"
        )
        self.assertEqual(
            market_cap["payload"]["formula"],
            "completed_unadjusted_close * effective_total_shares",
        )
        operations = result["coverage"]["security_valuation_metrics"]["operations"]
        stock_fields = {
            item["field"]
            for item in operations["eastmoney_stock_info@1"]["field_dispositions"]
        }
        self.assertEqual(
            stock_fields,
            {"f57", "f58", "f84", "f85", "f116", "f117", "f189", "f43"},
        )
        statement_dispositions = {
            item["field"]: item["disposition"]
            for item in operations["sina_financial_statements@1"]["field_dispositions"]
        }
        self.assertEqual(statement_dispositions["audit_opinion"], "excluded")
        self.assertEqual(statement_dispositions["item_tongbi"], "excluded")
        self.assertEqual(
            set(statement_dispositions),
            {
                "publish_date",
                "rType",
                "rCurrency",
                "is_audit",
                "audit_opinion",
                "data_source",
                "update_time",
                "item_title",
                "item_value",
                "item_tongbi",
            },
        )
        self.assertEqual(
            {
                item["field"]
                for item in operations["ths_consensus_eps@1"]["field_dispositions"]
            },
            {"forecast_year", "institutions", "minimum", "mean", "maximum"},
        )

    def test_etf_partial_failure_retains_successful_sibling_observations(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "etf_market",
            "subjects": [{"clue": "510050"}],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": False,
            },
        }

        result = invoke_copied_installed(
            request,
            market_fixtures=True,
            fail_url_contains="web.ifzq.gtimg.cn",
        )

        self.assertEqual(result["status"], "blocked")
        operations = result["coverage"]["etf_market_quote"]["operations"]
        self.assertEqual(operations["sse_etf_list@1"]["state"], "complete")
        self.assertEqual(operations["sse_etf_snapshot@1"]["state"], "complete")
        self.assertEqual(operations["tencent_daily_line@1"]["state"], "failed")
        retained_ids = {
            item["id"]
            for item in result["observations"]
            if item["operation_id"] in {"sse_etf_list@1", "sse_etf_snapshot@1"}
        }
        self.assertEqual(len(retained_ids), 2)
        self.assertEqual(
            operations["tencent_daily_line@1"]["source_error_ids"],
            [result["source_errors"][0]["id"]],
        )

    def test_valuation_partial_failure_retains_completed_inputs(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(
            request,
            valuation_fixtures=True,
            fixture_scenario="missing_forecast",
        )

        self.assertEqual(result["status"], "limited")
        operations = result["coverage"]["security_valuation_metrics"]["operations"]
        self.assertEqual(operations["eastmoney_stock_info@1"]["state"], "complete")
        self.assertEqual(operations["sina_financial_statements@1"]["state"], "complete")
        self.assertEqual(operations["ths_consensus_eps@1"]["state"], "failed")
        retained_operations = {item["operation_id"] for item in result["observations"]}
        self.assertTrue(
            {"eastmoney_stock_info@1", "sina_financial_statements@1"}.issubset(
                retained_operations
            )
        )
        self.assertEqual(
            {item["kind"] for item in result["calculations"]},
            {"market_capitalization", "pe_ttm", "pb_mrq"},
        )
        observation_ids = {item["id"] for item in result["observations"]}
        self.assertTrue(
            all(
                item["input_ids"] and set(item["input_ids"]).issubset(observation_ids)
                for item in result["calculations"]
            )
        )

    def test_copied_installed_valuation_compare_is_member_scoped_unified_result(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "工业富联", "issuer_security_class_count": 1},
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
            ],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(request, valuation_fixtures=True)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["plan"]["task_type"], "valuation_compare")
        operation_ids = {item["operation_id"] for item in result["observations"]}
        self.assertTrue(any(item.startswith("member-0::") for item in operation_ids))
        self.assertTrue(any(item.startswith("member-1::") for item in operation_ids))
        self.assertEqual(
            len({item["id"] for item in result["observations"]}),
            len(result["observations"]),
        )
        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "valuation_compare_members"
        )
        self.assertEqual(len(claim["payload"]["rows"]), 2)
        observation_ids = {item["id"] for item in result["observations"]}
        for row in claim["payload"]["rows"]:
            self.assertTrue(set(row["evidence_ids"]).issubset(observation_ids))

    def test_valuation_compare_isolates_one_member_source_failure(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "工业富联", "issuer_security_class_count": 1},
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
            ],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(
            request,
            valuation_fixtures=True,
            fixture_scenario="one_missing_forecast",
        )

        self.assertEqual(result["status"], "limited")
        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "valuation_compare_members"
        )
        self.assertEqual(
            [row["status"] for row in claim["payload"]["rows"]],
            ["limited", "limited"],
        )
        self.assertTrue(result["source_errors"])
        self.assertTrue(
            all(
                item["operation_id"].startswith("member-0::")
                for item in result["source_errors"]
            )
        )
        member_one_operations = {
            item["operation_id"]
            for item in result["observations"]
            if item["operation_id"].startswith("member-1::")
        }
        self.assertIn("member-1::ths_consensus_eps@1", member_one_operations)

    def test_valuation_missing_financials_keeps_other_owned_inputs(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(
            request,
            valuation_fixtures=True,
            fixture_scenario="missing_financials",
        )

        self.assertEqual(result["status"], "limited")
        operations = result["coverage"]["security_valuation_metrics"]["operations"]
        self.assertEqual(operations["sina_financial_statements@1"]["state"], "failed")
        self.assertEqual(operations["ths_consensus_eps@1"]["state"], "complete")
        self.assertEqual(
            {item["kind"] for item in result["calculations"]},
            {
                "market_capitalization",
                "forward_pe",
                "forecast_eps_growth",
                "peg",
                "pe_digestion_years",
            },
        )
        observation_ids = {item["id"] for item in result["observations"]}
        self.assertTrue(
            all(
                item["input_ids"] and set(item["input_ids"]).issubset(observation_ids)
                for item in result["calculations"]
            )
        )

    def test_valuation_compare_conflicts_are_member_scoped(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "工业富联", "issuer_security_class_count": 1},
                {"clue": "贵州茅台", "issuer_security_class_count": 1},
            ],
            "as_of": date.today().isoformat(),
            "window": None,
            "parameters": {"target_pe": "30"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(
            request,
            valuation_fixtures=True,
            fixture_scenario="provider_mcap_conflict",
        )

        self.assertEqual(len(result["conflicts"]), 2)
        self.assertEqual(
            {item["code"].split("::", 1)[0] for item in result["conflicts"]},
            {"member-0", "member-1"},
        )
        observation_ids = {item["id"] for item in result["observations"]}
        self.assertTrue(
            all(
                set(item["observation_ids"]).issubset(observation_ids)
                for item in result["conflicts"]
            )
        )


if __name__ == "__main__":
    unittest.main()
