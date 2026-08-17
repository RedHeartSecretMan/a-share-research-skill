from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
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
from a_share_research.cli import main as cli_main  # noqa: E402
from a_share_research.close_observation import execute_close_research  # noqa: E402
from a_share_research.etf_market import build_etf_market_result  # noqa: E402
from a_share_research.identity_sources import HttpResponse  # noqa: E402
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
    _NoIoHttpTransport,
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


class FutureAdjustedTransport:
    def __init__(self, scenario: str = "future_sibling") -> None:
        self._delegate = load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport")
        self._scenario = scenario

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        response = self._delegate.get(url, headers)
        if "push2his.eastmoney.com" not in url:
            return response
        payload = json.loads(response.body)
        future = "2026-08-03,14.40,14.60,14.80,14.20,1000,14500.00,1.00,1.00,1.00,1.00"
        if self._scenario == "future_sibling":
            payload["data"]["klines"].append(future)
        elif self._scenario == "all_future":
            payload["data"]["klines"] = [future]
        elif self._scenario == "malformed_future":
            payload["data"]["klines"].append("2026-08-03,14.40")
        else:
            raise AssertionError(self._scenario)
        return HttpResponse(
            status=response.status,
            content_type=response.content_type,
            body=json.dumps(payload).encode(),
            retrieved_at=response.retrieved_at,
        )


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


def rehash_direct_plan(result: UnifiedResearchResult) -> None:
    dispositions = [
        {
            "capability_id": capability_id,
            "operation_id": operation_id,
            "fields": operation["field_dispositions"],
        }
        for capability_id, capability in result.coverage.items()
        for operation_id, operation in capability["operations"].items()
    ]
    result.plan["id"] = stable_result_id(
        "plan",
        {
            "version": result.plan["version"],
            "task_type": result.plan["task_type"],
            "operation_obligations": result.plan["operation_obligations"],
            "research_dependencies": result.plan["research_dependencies"],
            "field_dispositions": dispositions,
        },
    )


def aggregate_state(operations: dict[str, dict[str, object]]) -> str:
    states = [operation["state"] for operation in operations.values()]
    if len(set(states)) == 1:
        return states[0]  # type: ignore[return-value]
    if "failed" in states or "partial" in states:
        return "failed" if all(state == "failed" for state in states) else "partial"
    if any(
        state in {"not_requested", "skipped_policy", "skipped_research_dependency"}
        for state in states
    ):
        return "partial"
    if "complete" in states:
        return "complete"
    return "observed_empty"


def move_operation_capability(
    result: UnifiedResearchResult,
    operation_id: str,
    source_capability: str,
    target_capability: str,
) -> None:
    source_coverage = result.coverage[source_capability]
    target_coverage = result.coverage[target_capability]
    operation = source_coverage["operations"].pop(operation_id)
    operation["capability_id"] = target_capability
    for disposition in operation["field_dispositions"]:
        destination = disposition["destination"]
        if isinstance(destination, str) and destination.startswith(
            f"coverage.{source_capability}."
        ):
            disposition["destination"] = destination.replace(
                f"coverage.{source_capability}.",
                f"coverage.{target_capability}.",
                1,
            )
    target_coverage["operations"][operation_id] = operation
    source_coverage["state"] = aggregate_state(source_coverage["operations"])
    target_coverage["state"] = aggregate_state(target_coverage["operations"])

    source_registration = result.capability_registry[source_capability]
    target_registration = result.capability_registry[target_capability]
    registration = next(
        item for item in source_registration["operations"] if item["id"] == operation_id
    )
    source_registration["operations"].remove(registration)
    target_registration["operations"].append(registration)
    next(
        item
        for item in result.plan["operation_obligations"]
        if item["operation_id"] == operation_id
        and item["capability_id"] == source_capability
    )["capability_id"] = target_capability
    rehash_direct_plan(result)


def invoke_copied_installed(
    request_payload: dict[str, object],
    *,
    market_fixtures: bool = False,
    valuation_fixtures: bool = False,
    fail_url_contains: str | None = None,
    fixture_scenario: str | None = None,
    fixture_research_now: str | None = None,
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
            clock_lines = (
                (
                    "from datetime import datetime",
                    f"sys.path.insert(0, {str(installed / 'scripts')!r})",
                    "import a_share_research.cli as cli",
                    "import a_share_research.identity_sources as identity_sources",
                    "class FixtureDateTime(datetime):",
                    "    @classmethod",
                    "    def now(cls, tz=None):",
                    f"        value = datetime.fromisoformat({fixture_research_now!r})",
                    "        return value if tz is None else value.astimezone(tz)",
                    "identity_sources.datetime = FixtureDateTime",
                    "original_research = cli.research",
                    "def fixture_research(request, **kwargs):",
                    f"    kwargs['research_now'] = datetime.fromisoformat({fixture_research_now!r})",
                    "    return original_research(request, **kwargs)",
                    "cli.research = fixture_research",
                )
                if fixture_research_now is not None
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
                        *clock_lines,
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


def build_price_blocked_comparison() -> UnifiedResearchResult:
    request = {
        "schema_version": "1.0",
        "task_type": "valuation_compare",
        "subjects": [
            {"clue": "601138", "issuer_security_class_count": 1},
            {"clue": "000001", "issuer_security_class_count": 1},
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
    with patch.dict(
        os.environ,
        {"A_SHARE_RESEARCH_TEST_SCENARIO": "missing_price"},
    ):
        return build_valuation_comparison_result(
            request,
            load_fixture_transport(
                VALUATION_FIXTURE_CLI,
                "ValuationFixtureTransport",
            ),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        )


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
    def run_future_adjusted_public(
        self, transport: FutureAdjustedTransport
    ) -> dict[str, object]:
        request = {
            "schema_version": "1.0",
            "task_type": "market_trend",
            "subjects": [{"clue": "蓝色光标"}],
            "as_of": "2026-08-02",
            "window": {"trading_days": 10},
            "parameters": {"adjustment": "forward_adjusted"},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            request_path = Path(temporary_directory, "request.json")
            request_path.write_text(
                json.dumps(request, ensure_ascii=False), encoding="utf-8"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = cli_main(
                    ["run", "--request", str(request_path)],
                    identity_transport=transport,
                    research_now=datetime(
                        2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))
                    ),
                    available_optional_dependencies=set(),
                )
        self.assertEqual(return_code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        loaded = json.loads(stdout.getvalue())
        self.assertIsInstance(loaded, dict)
        return loaded

    def test_public_eastmoney_adjusted_owner_rejects_a_future_response_row(
        self,
    ) -> None:
        result = self.run_future_adjusted_public(FutureAdjustedTransport())

        self.assertEqual(result["status"], "limited")
        eastmoney = result["coverage"]["market_trend_series"]["operations"][
            "eastmoney_forward_adjusted_daily_line@1"
        ]
        self.assertEqual(
            (eastmoney["execution_state"], eastmoney["state"]),
            ("succeeded", "complete"),
        )
        eastmoney_dates = {
            item["payload"]["observation"]["trading_date"]
            for item in result["observations"]
            if item["operation_id"] == "eastmoney_forward_adjusted_daily_line@1"
        }
        self.assertIn("2026-07-31", eastmoney_dates)
        self.assertNotIn("2026-08-03", eastmoney_dates)
        tencent = result["coverage"]["market_trend_series"]["operations"][
            "tencent_forward_adjusted_daily_line@1"
        ]
        self.assertEqual(
            (tencent["execution_state"], tencent["state"]),
            ("succeeded", "complete"),
        )

    def test_public_eastmoney_adjusted_owner_fails_when_every_row_is_future(
        self,
    ) -> None:
        result = self.run_future_adjusted_public(FutureAdjustedTransport("all_future"))

        eastmoney = result["coverage"]["market_trend_series"]["operations"][
            "eastmoney_forward_adjusted_daily_line@1"
        ]
        self.assertEqual(
            (eastmoney["execution_state"], eastmoney["state"]),
            ("failed", "failed"),
        )
        self.assertEqual(
            {
                item["code"]
                for item in result["source_errors"]
                if item["operation_id"] == "eastmoney_forward_adjusted_daily_line@1"
            },
            {"empty_observation"},
        )
        self.assertFalse(
            any(
                item["operation_id"] == "eastmoney_forward_adjusted_daily_line@1"
                for item in result["observations"]
            )
        )
        self.assertEqual(
            result["coverage"]["market_trend_series"]["operations"][
                "tencent_forward_adjusted_daily_line@1"
            ]["state"],
            "complete",
        )

    def test_public_eastmoney_adjusted_owner_does_not_hide_future_schema_failure(
        self,
    ) -> None:
        result = self.run_future_adjusted_public(
            FutureAdjustedTransport("malformed_future")
        )

        eastmoney = result["coverage"]["market_trend_series"]["operations"][
            "eastmoney_forward_adjusted_daily_line@1"
        ]
        self.assertEqual(
            (eastmoney["execution_state"], eastmoney["state"]),
            ("failed", "failed"),
        )
        self.assertEqual(
            {
                item["code"]
                for item in result["source_errors"]
                if item["operation_id"] == "eastmoney_forward_adjusted_daily_line@1"
            },
            {"unknown_schema"},
        )
        self.assertEqual(
            result["coverage"]["market_trend_series"]["operations"][
                "tencent_forward_adjusted_daily_line@1"
            ]["state"],
            "complete",
        )

    def test_copied_installed_tencent_provider_values_are_explicitly_excluded(
        self,
    ) -> None:
        result = invoke_copied_installed(
            {
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
            },
            valuation_fixtures=True,
            fixture_research_now="2026-08-02T18:30:00+08:00",
        )

        tencent = result["coverage"]["valuation_close"]["operations"][
            "tencent_daily_line@1"
        ]
        dispositions = {item["field"]: item for item in tencent["field_dispositions"]}
        provider_fields = {"qt[39]", "qt[44]", "qt[45]", "qt[46]"}
        self.assertEqual(
            {
                field: {
                    "disposition": dispositions[field]["disposition"],
                    "destination": dispositions[field]["destination"],
                }
                for field in provider_fields
            },
            {
                field: {"disposition": "excluded", "destination": None}
                for field in provider_fields
            },
        )
        tencent_observation_ids = {
            item["id"]
            for item in result["observations"]
            if item["operation_id"] == "tencent_daily_line@1"
        }
        self.assertTrue(tencent_observation_ids)
        self.assertTrue(
            all(
                tencent_observation_ids.isdisjoint(calculation["input_ids"])
                for calculation in result["calculations"]
            )
        )
        serialized = json.dumps(result, sort_keys=True)
        for provider_value in (
            "910039.39",
            "910044.44",
            "910045.45",
            "910046.46",
        ):
            self.assertNotIn(provider_value, serialized)
        calculations = {item["kind"]: item for item in result["calculations"]}
        self.assertEqual(
            calculations["market_capitalization"]["payload"],
            {
                "formula": "completed_unadjusted_close * effective_total_shares",
                "operands": {"price": "56.700", "shares": "19844092284"},
            },
        )
        self.assertEqual(
            calculations["pe_ttm"]["payload"]["formula"],
            "market_capitalization / ttm_attributable_profit",
        )
        self.assertEqual(
            calculations["pb_mrq"]["payload"]["formula"],
            "market_capitalization / mrq_attributable_equity",
        )

    def test_known_identity_and_market_operations_reject_capability_rebinding(
        self,
    ) -> None:
        for operation_id, source_capability, target_capability in (
            (
                "sse_stock_list@1",
                "security_identity_resolution",
                "market_trend_series",
            ),
            (
                "sse_daily_line@1",
                "market_trend_series",
                "security_identity_resolution",
            ),
        ):
            with self.subTest(operation_id=operation_id):
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
                    load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport"),
                    datetime(
                        2026,
                        8,
                        2,
                        18,
                        30,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                )
                move_operation_capability(
                    result, operation_id, source_capability, target_capability
                )

                with self.assertRaisesRegex(ValueError, "operation capability"):
                    result.to_dict()

    def test_natural_identity_cross_checks_resolve_to_owned_public_facts(self) -> None:
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
            load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport"),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        ).to_dict()

        operations = result["coverage"]["security_identity_resolution"]["operations"]
        sse = {
            item["field"]: item["destination"]
            for item in operations["sse_stock_list@1"]["field_dispositions"]
        }
        cninfo = {
            item["field"]: item["destination"]
            for item in operations["cninfo_security_dictionary@1"]["field_dispositions"]
        }
        self.assertEqual(
            sse["STATE_CODE_A_DESC"],
            "observations[].payload.observation.listing_status",
        )
        self.assertEqual(
            sse["COMPANY_CODE"],
            "observations[].payload.observation.issuer_relationship_verified",
        )
        self.assertEqual(
            cninfo["category"],
            "observations[].payload.observation.security_type",
        )

    def test_natural_valuation_destinations_resolve_to_owned_public_facts(self) -> None:
        result = build_security_valuation_result(
            {
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
            },
            load_fixture_transport(VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        ).to_dict()

        operations = result["coverage"]["security_valuation_metrics"]["operations"]
        statements = {
            item["field"]: item["destination"]
            for item in operations["sina_financial_statements@1"]["field_dispositions"]
        }
        consensus = {
            item["field"]: item["destination"]
            for item in operations["ths_consensus_eps@1"]["field_dispositions"]
        }
        self.assertEqual(
            statements["publish_date"],
            "observations.payload.report.publication_date",
        )
        self.assertEqual(
            statements["item_value"],
            "observations.payload.full_statement_items[].value",
        )
        self.assertEqual(
            consensus["forecast_year"],
            "observations.payload.observed_value.value[].year",
        )

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
                "qt[39]",
                "qt[44]",
                "qt[45]",
                "qt[46]",
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
                "qt[39]",
                "qt[44]",
                "qt[45]",
                "qt[46]",
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
    def test_policy_blocked_comparison_retains_member_producer_contracts(self) -> None:
        transport = _NoIoHttpTransport()
        result = build_valuation_comparison_result(
            {
                "schema_version": "1.0",
                "task_type": "valuation_compare",
                "subjects": [
                    {"clue": "工业富联", "issuer_security_class_count": 1},
                    {"clue": "贵州茅台", "issuer_security_class_count": 1},
                ],
                "as_of": "2026-08-12",
                "window": None,
                "parameters": {"target_pe": "30"},
                "source_policy": {
                    "allow_experimental": False,
                    "allow_credentials": False,
                    "allow_fallback": False,
                },
            },
            transport,
            datetime(2026, 8, 12, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        ).to_dict()

        obligations = result["plan"]["operation_obligations"]
        self.assertEqual(len(obligations), 6)
        self.assertEqual(
            {item["operation_id"].partition("::")[0] for item in obligations},
            {"member-0", "member-1"},
        )
        self.assertTrue(
            all(
                item["producer_operation_id"] == item["operation_id"].partition("::")[2]
                for item in obligations
            )
        )
        self.assertEqual(set(result["coverage"]), {"valuation_compare_metrics"})
        self.assertEqual(transport.calls, [])

    def test_ordinary_direct_serializer_rejects_rehashed_producer_identity(
        self,
    ) -> None:
        result = build_market_trend_result(
            {
                "schema_version": "1.0",
                "task_type": "market_trend",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {"trading_days": 10},
                "parameters": {"adjustment": "unadjusted"},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                },
            },
            load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport"),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        obligation = result.plan["operation_obligations"][0]
        obligation["producer_operation_id"] = "forged_market_operation@1"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(
            ValueError, "producer operation contradicts source authority"
        ):
            result.to_dict()

    def test_comparison_serializer_rejects_rehashed_producer_identity(self) -> None:
        result = build_valuation_comparison_result(
            {
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
            },
            load_fixture_transport(VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        obligation = next(
            item
            for item in result.plan["operation_obligations"]
            if item["operation_id"] == "member-0::sina_financial_statements@1"
        )
        obligation["producer_operation_id"] = "forged_financials@1"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(
            ValueError, "producer operation contradicts source authority"
        ):
            result.to_dict()

    def test_default_contract_authorities_construct_without_transport_io(self) -> None:
        from a_share_research.capital_registry import build_default_capital_operations
        from a_share_research.content_registry import build_default_content_operations
        from a_share_research.etf_option_registry import (
            build_default_etf_option_operations,
        )
        from a_share_research.identity_sources import build_default_identity_operations
        from a_share_research.market_signal_registry import (
            build_default_market_signal_operations,
        )

        transport = _NoIoHttpTransport()
        self.assertEqual(len(build_default_identity_operations(transport)), 3)
        self.assertEqual(len(build_default_etf_option_operations(transport)), 1)
        self.assertEqual(len(build_default_capital_operations(transport)), 7)
        self.assertEqual(len(build_default_market_signal_operations(transport)), 8)
        self.assertEqual(
            len(
                build_default_content_operations(
                    transport,
                    allow_credentials=True,
                    allow_fallback=True,
                    research_now=None,
                )
            ),
            11,
        )
        self.assertEqual(transport.calls, [])

    def test_dependency_satisfaction_is_scoped_by_consumer_and_evidence_use(
        self,
    ) -> None:
        missing_use = build_invariant_result()
        complete_pairs = build_invariant_result(include_second_claim=True)

        self.assertEqual(missing_use.status, "blocked")
        self.assertEqual(complete_pairs.status, "limited")

    def test_destination_validation_preserves_heterogeneous_zero_empty_and_failed(
        self,
    ) -> None:
        dispositions = (
            FieldDisposition("alpha", "normalized", "observations.payload.alpha"),
            FieldDisposition("beta", "normalized", "observations.payload.beta"),
            FieldDisposition(
                "unused",
                "excluded",
                None,
                "The fixture field is intentionally not retained.",
            ),
        )
        plan = DirectTaskPlan(
            task_type="destination_guard_task",
            as_of="2026-08-11",
            obligations=(
                OperationObligation(
                    capability_id="example_capability",
                    operation_id="example_operation@1",
                    source_role="market_observation",
                    experimental=False,
                    exchange=None,
                    evidence_uses=(EvidenceUsePolicy("example_use", "supporting"),),
                ),
            ),
            dependencies=(
                ResearchDependency("example_consumer", "example_use", 1, True),
            ),
        )

        heterogeneous = fold_direct_task_research_result(
            plan,
            (
                OperationOutcome(
                    capability_id="example_capability",
                    operation_id="example_operation@1",
                    execution=OperationExecution("succeeded", query_complete=True),
                    observations=(
                        {
                            "id": "alpha",
                            "source_operation": "example_operation@1",
                            "alpha": 0,
                        },
                        {
                            "id": "beta",
                            "source_operation": "example_operation@1",
                            "beta": "",
                        },
                    ),
                    field_dispositions=dispositions,
                ),
            ),
            summary="Heterogeneous destination fixture.",
        ).to_dict()
        self.assertEqual(
            heterogeneous["coverage"]["example_capability"]["state"], "complete"
        )

        for execution, errors, expected_state in (
            (
                OperationExecution("succeeded", query_complete=True),
                (),
                "observed_empty",
            ),
            (
                OperationExecution("failed"),
                (
                    {
                        "source_operation": "example_operation@1",
                        "code": "fixture_failure",
                        "message": "The fixture operation failed.",
                    },
                ),
                "failed",
            ),
        ):
            with self.subTest(expected_state=expected_state):
                result = fold_direct_task_research_result(
                    plan,
                    (
                        OperationOutcome(
                            capability_id="example_capability",
                            operation_id="example_operation@1",
                            execution=execution,
                            source_errors=errors,
                            field_dispositions=dispositions,
                        ),
                    ),
                    summary="Empty destination fixture.",
                ).to_dict()
                self.assertEqual(
                    result["coverage"]["example_capability"]["state"],
                    expected_state,
                )

    def test_failed_operation_rejects_an_unsupported_field_destination(self) -> None:
        plan = DirectTaskPlan(
            task_type="destination_guard_task",
            as_of="2026-08-11",
            obligations=(
                OperationObligation(
                    capability_id="example_capability",
                    operation_id="example_operation@1",
                    source_role="market_observation",
                    experimental=False,
                    exchange=None,
                    evidence_uses=(EvidenceUsePolicy("example_use", "supporting"),),
                ),
            ),
            dependencies=(
                ResearchDependency("example_consumer", "example_use", 1, True),
            ),
        )
        result = fold_direct_task_research_result(
            plan,
            (
                OperationOutcome(
                    capability_id="example_capability",
                    operation_id="example_operation@1",
                    execution=OperationExecution("failed"),
                    source_errors=(
                        {
                            "source_operation": "example_operation@1",
                            "code": "fixture_failure",
                            "message": "The fixture operation failed.",
                        },
                    ),
                    field_dispositions=(
                        FieldDisposition(
                            "alpha", "normalized", "totally.invalid.destination"
                        ),
                    ),
                ),
            ),
            summary="Failed destination fixture.",
        )

        with self.assertRaisesRegex(ValueError, "destination is unsupported"):
            result.to_dict()

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

    def test_serializer_rejects_rehashed_unresolved_field_destination(self) -> None:
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
        operation = result.coverage["market_trend_series"]["operations"][
            "szse_daily_line@1"
        ]
        disposition = next(
            item
            for item in operation["field_dispositions"]
            if item["field"] == "row[0]"
        )
        disposition["destination"] = "observations.payload.forged_but_nonempty"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(ValueError, "destination does not resolve"):
            result.to_dict()

    def test_serializer_rejects_rehashed_existing_sibling_field_destination(
        self,
    ) -> None:
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
        operation = result.coverage["market_trend_series"]["operations"][
            "szse_daily_line@1"
        ]
        disposition = next(
            item
            for item in operation["field_dispositions"]
            if item["field"] == "row[0]"
        )
        disposition["destination"] = "observations.payload.observed_value.value.close"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(ValueError, "field disposition contract"):
            result.to_dict()

    def test_shared_identity_authority_rejects_rehashed_sibling_destination(
        self,
    ) -> None:
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
            load_fixture_transport(MARKET_FIXTURE_CLI, "FixtureTransport"),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        operation = result.coverage["security_identity_resolution"]["operations"][
            "szse_stock_list@1"
        ]
        next(
            item for item in operation["field_dispositions"] if item["field"] == "agdm"
        )["destination"] = "observations[].payload.observation.name"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(ValueError, "field disposition contract"):
            result.to_dict()

    def test_task_authorities_reject_rehashed_existing_sibling_destinations(
        self,
    ) -> None:
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        policy = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        }
        market_transport = load_fixture_transport(
            MARKET_FIXTURE_CLI, "FixtureTransport"
        )
        valuation_transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
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
            research_now,
        )
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
        )
        comparison = build_valuation_comparison_result(
            {
                "schema_version": "1.0",
                "task_type": "valuation_compare",
                "subjects": [
                    {"clue": "工业富联", "issuer_security_class_count": 1},
                    {"clue": "贵州茅台", "issuer_security_class_count": 1},
                ],
                "as_of": "2026-08-02",
                "window": None,
                "parameters": {"target_pe": "30"},
                "source_policy": policy,
            },
            valuation_transport,
            research_now,
        )
        cases = (
            (
                etf,
                "etf_market_quote",
                "sse_etf_snapshot@1",
                "last",
                "observations.payload.observed_value.value.open",
            ),
            (
                valuation,
                "security_valuation_metrics",
                "sina_financial_statements@1",
                "publish_date",
                "observations.payload.report.scope",
            ),
            (
                comparison,
                "valuation_compare_metrics",
                "member-0::sina_financial_statements@1",
                "publish_date",
                "observations.payload.report.scope",
            ),
        )
        for result, capability_id, operation_id, field, destination in cases:
            with self.subTest(task_type=result.plan["task_type"]):
                dispositions = result.coverage[capability_id]["operations"][
                    operation_id
                ]["field_dispositions"]
                next(item for item in dispositions if item["field"] == field)[
                    "destination"
                ] = destination
                rehash_direct_plan(result)
                with self.assertRaisesRegex(ValueError, "field disposition contract"):
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

    def test_comparison_serializer_rejects_rehashed_blocked_row_identity_loss(
        self,
    ) -> None:
        result = build_price_blocked_comparison()
        baseline = result.to_dict()
        claim_index = next(
            index
            for index, item in enumerate(result.claims)
            if item["kind"] == "valuation_compare_members"
        )
        blocked = result.claims[claim_index]["payload"]["rows"][0]

        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["security"], "SSE:601138")
        self.assertEqual(
            baseline["claims"][claim_index]["payload"]["subjects"][0],
            {"security": "SSE:601138", "name": "工业富联"},
        )
        blocked["security"] = None
        blocked["name"] = "601138"
        rehash_direct_consumer(result, collection="claims", index=claim_index)

        with self.assertRaisesRegex(ValueError, "comparison domain facts"):
            result.to_dict()

    def test_comparison_serializer_rejects_rehashed_blocked_subject_rebinding(
        self,
    ) -> None:
        result = build_price_blocked_comparison()
        result.to_dict()
        claim_index = next(
            index
            for index, item in enumerate(result.claims)
            if item["kind"] == "valuation_compare_members"
        )
        payload = result.claims[claim_index]["payload"]
        forged_subject = {"security": "SSE:999999", "name": "错绑证券"}
        payload["subjects"][0] = deepcopy(forged_subject)
        payload["rows"][0]["security"] = forged_subject["security"]
        payload["rows"][0]["name"] = forged_subject["name"]
        rehash_direct_consumer(result, collection="claims", index=claim_index)

        with self.assertRaisesRegex(ValueError, "comparison domain facts"):
            result.to_dict()

    def test_comparison_serializer_rejects_self_reported_blocked_member(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "601138", "issuer_security_class_count": 1},
                {"clue": "000001", "issuer_security_class_count": 1},
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
        result = build_valuation_comparison_result(
            request,
            load_fixture_transport(
                VALUATION_FIXTURE_CLI,
                "ValuationFixtureTransport",
            ),
            datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        result.to_dict()
        removed_ids = {
            item["id"]
            for item in result.calculations
            if item["kind"].startswith("member-0::")
        }
        object.__setattr__(
            result,
            "calculations",
            tuple(
                item for item in result.calculations if item["id"] not in removed_ids
            ),
        )
        result.task_views["valuation_compare"]["calculation_ids"] = [
            item
            for item in result.task_views["valuation_compare"]["calculation_ids"]
            if item not in removed_ids
        ]
        result.briefs[0]["calculation_ids"] = [
            item
            for item in result.briefs[0]["calculation_ids"]
            if item not in removed_ids
        ]
        claim_index = next(
            index
            for index, item in enumerate(result.claims)
            if item["kind"] == "valuation_compare_members"
        )
        row = result.claims[claim_index]["payload"]["rows"][0]
        row["status"] = "blocked"
        row["trading_date"] = None
        row["metrics"] = {
            metric: {
                "status": "not_calculable",
                "reason": "valuation_price_not_established",
            }
            for metric in (
                "market_capitalization",
                "pe_ttm",
                "pb_mrq",
                "forward_pe",
                "forecast_eps_growth",
                "peg",
                "pe_digestion_years",
            )
        }
        row["limitation_codes"] = ["valuation_price_not_established"]
        rehash_direct_consumer(result, collection="claims", index=claim_index)

        with self.assertRaisesRegex(
            ValueError,
            "comparison domain facts forge member status",
        ):
            result.to_dict()

    def test_missing_shares_serializer_rejects_rehashed_calculated_claim_forgery(
        self,
    ) -> None:
        transport = load_fixture_transport(
            VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
        )
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
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        for field, forged_value in (
            (
                "effective_total_shares",
                {
                    "value": "1",
                    "unit": "shares",
                    "effective_at": None,
                    "observed_at": "2026-08-02T18:30:00+08:00",
                    "effective_status": "current_snapshot_observation",
                },
            ),
            (
                "market_capitalization",
                {
                    "status": "calculated",
                    "value": "56.700",
                    "unit": "CNY",
                    "formula": "completed_unadjusted_close * effective_total_shares",
                },
            ),
        ):
            with self.subTest(field=field):
                with patch.dict(
                    os.environ,
                    {"A_SHARE_RESEARCH_TEST_SCENARIO": "missing_shares"},
                ):
                    result = build_security_valuation_result(
                        request, transport, research_now
                    )
                claim = result.claims[0]
                if field == "effective_total_shares":
                    claim["payload"]["valuation_basis"][field] = forged_value
                else:
                    claim["payload"]["metrics"][field] = forged_value
                rehash_direct_consumer(result, collection="claims", index=0)

                with self.assertRaisesRegex(ValueError, "valuation domain facts"):
                    result.to_dict()

    def test_valuation_serializer_rejects_rehashed_claim_projection_forgeries(
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
        research_now = datetime(2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8)))
        cases = (
            ("default", ("subjects", 0, "issuer", "name"), "伪造发行人"),
            ("default", ("subjects", 0, "security", "type"), "ETF"),
            ("default", ("research", "as_of"), "2099-01-01"),
            ("default", ("valuation_basis", "price", "unit"), "USD/share"),
            (
                "default",
                ("financial_statements", "income", 0, "publication_date"),
                "2026-08-01",
            ),
            (
                "default",
                ("financial_statements", "income", 0, "scope"),
                "standalone",
            ),
            (
                "default",
                ("forecast", "consensus_eps", 0, "institutions"),
                999,
            ),
            (
                "default",
                ("reported_financials", "mrq_attributable_equity", "value"),
                "1",
            ),
            (
                "missing_financials",
                ("metrics", "pe_ttm"),
                {"status": "calculated", "value": "1.0000", "unit": "ratio"},
            ),
            (
                "missing_forecast",
                ("metrics", "forward_pe"),
                {"status": "calculated", "value": "1.0000", "unit": "ratio"},
            ),
        )
        for scenario, path, forged_value in cases:
            with self.subTest(scenario=scenario, path=path):
                transport = load_fixture_transport(
                    VALUATION_FIXTURE_CLI, "ValuationFixtureTransport"
                )
                with patch.dict(
                    os.environ,
                    {"A_SHARE_RESEARCH_TEST_SCENARIO": scenario},
                ):
                    result = build_security_valuation_result(
                        request, transport, research_now
                    )
                original_observations = deepcopy(result.observations)
                target = result.claims[0]["payload"]
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = forged_value
                rehash_direct_consumer(result, collection="claims", index=0)

                self.assertEqual(result.observations, original_observations)
                with self.assertRaisesRegex(ValueError, "valuation domain facts"):
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

    def test_price_failure_still_attempts_independent_valuation_sources(self) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "security_valuation",
            "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
            "as_of": "2026-08-15",
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
            fixture_scenario="missing_price",
            fixture_research_now="2026-08-15T18:30:00+08:00",
        )

        self.assertEqual(result["status"], "blocked")
        operations = result["coverage"]["security_valuation_metrics"]["operations"]
        independent_owners = {
            "eastmoney_stock_info@1",
            "sina_financial_statements@1",
            "ths_consensus_eps@1",
        }
        self.assertEqual(
            {
                owner: (
                    operations[owner]["execution_state"],
                    operations[owner]["state"],
                )
                for owner in independent_owners
            },
            {owner: ("succeeded", "complete") for owner in independent_owners},
        )
        self.assertTrue(
            independent_owners.issubset(
                {item["operation_id"] for item in result["observations"]}
            )
        )

        conflict = invoke_copied_installed(
            request,
            valuation_fixtures=True,
            fixture_scenario="missing_price_identity_conflict",
            fixture_research_now="2026-08-15T18:30:00+08:00",
        )
        self.assertEqual(conflict["status"], "blocked")
        self.assertIn(
            "stock_information_identity_conflict",
            {item["code"] for item in conflict["conflicts"]},
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

    def test_copied_installed_comparison_retains_price_blocked_member_identity(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "valuation_compare",
            "subjects": [
                {"clue": "601138", "issuer_security_class_count": 1},
                {"clue": "000001", "issuer_security_class_count": 1},
            ],
            "as_of": "2026-08-15",
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
            fixture_scenario="missing_price",
            fixture_research_now="2026-08-15T18:30:00+08:00",
        )

        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "valuation_compare_members"
        )
        self.assertEqual(
            claim["payload"]["subjects"],
            [
                {"security": "SSE:601138", "name": "工业富联"},
                {"security": "SZSE:000001", "name": "平安银行"},
            ],
        )
        blocked, sibling = claim["payload"]["rows"]
        self.assertEqual(
            (blocked["security"], blocked["name"], blocked["status"]),
            ("SSE:601138", "工业富联", "blocked"),
        )
        self.assertIn("valuation_price_not_established", blocked["limitation_codes"])
        self.assertEqual(
            (sibling["security"], sibling["name"], sibling["status"]),
            ("SZSE:000001", "平安银行", "limited"),
        )
        self.assertEqual(
            sibling["metrics"]["market_capitalization"]["status"], "calculated"
        )
        observation_ids = {item["id"] for item in result["observations"]}
        self.assertTrue(set(blocked["evidence_ids"]).issubset(observation_ids))
        self.assertTrue(set(sibling["evidence_ids"]).issubset(observation_ids))

    def test_copied_installed_workflow_retains_price_blocked_comparison_member(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "research_workflow",
            "subjects": [
                {"clue": "601138", "issuer_security_class_count": 1},
                {"clue": "000001", "issuer_security_class_count": 1},
            ],
            "as_of": "2026-08-15",
            "window": None,
            "parameters": {
                "workflow": {"id": "valuation_comparison", "version": "1.0"},
                "inputs": {"target_pe": "30"},
            },
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }

        result = invoke_copied_installed(
            request,
            valuation_fixtures=True,
            fixture_scenario="missing_price",
            fixture_research_now="2026-08-15T18:30:00+08:00",
        )

        self.assertEqual(result["plan"]["workflow_id"], "valuation_comparison")
        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "valuation_compare_members"
        )
        self.assertEqual(
            [
                (row["security"], row["name"], row["status"])
                for row in claim["payload"]["rows"]
            ],
            [
                ("SSE:601138", "工业富联", "blocked"),
                ("SZSE:000001", "平安银行", "limited"),
            ],
        )
        view = result["task_views"]["research_workflow"]
        step = next(item for item in view["steps"] if item["step_id"] == "comparison")
        self.assertIn(claim["id"], step["claim_ids"])
        self.assertTrue(set(step["brief_ids"]))
        self.assertTrue(
            set(step["brief_ids"]).issubset({item["id"] for item in result["briefs"]})
        )

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
