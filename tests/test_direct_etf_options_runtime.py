from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
OPTION_FIXTURE_MODULE = REPOSITORY_ROOT / "tests/test_etf_option_sources.py"
sys.path.insert(0, str(SCRIPTS_ROOT))

from a_share_research.etf_option_contract import (  # noqa: E402
    OptionAcquisitionOwners,
    OptionCoverage,
    OptionSourceBatch,
    OptionSourceFailure,
)
from a_share_research.etf_option_sources import (  # noqa: E402
    SinaEtfOptionSnapshotOperation,
)
from a_share_research.research_result import (  # noqa: E402
    UnifiedResearchResult,
    stable_result_id,
)
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

from tests.test_etf_option_sources import (  # noqa: E402
    DiagnosticGate,
    FixtureTransport,
    query,
    text_response,
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


def option_request(*, allow_experimental: bool = False) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "etf_options",
        "subjects": [{"clue": "510050"}],
        "as_of": "2026-08-03",
        "window": {
            "observed_from": "2026-08-03",
            "observed_to": "2026-08-03",
        },
        "parameters": {
            "view": "atm",
            "expiry": {"mode": "nearest_unexpired"},
            "quote_mode": "latest",
        },
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def invoke_copied_installed(
    request_payload: dict[str, object],
    *,
    option_fixtures: bool = False,
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
        if option_fixtures:
            sitecustomize = temporary_root / "sitecustomize.py"
            sitecustomize.write_text(
                "\n".join(
                    (
                        "from email.message import Message",
                        "import importlib.util",
                        "import io",
                        "import sys",
                        "import time",
                        "import urllib.request",
                        f"repository_root = {str(REPOSITORY_ROOT)!r}",
                        f"fixture_path = {str(OPTION_FIXTURE_MODULE)!r}",
                        "time.sleep = lambda _seconds: None",
                        "sys.path.insert(0, repository_root)",
                        "spec = importlib.util.spec_from_file_location('_option_fixture', fixture_path)",
                        "module = importlib.util.module_from_spec(spec)",
                        "spec.loader.exec_module(module)",
                        (
                            "analytics_text = (module.FIXTURES / 'analytics_510050_2608.txt').read_text(encoding='utf-8')"
                            if fixture_scenario == "analytics_missing_put"
                            else "analytics_text = None"
                        ),
                        (
                            "transport = module.FixtureTransport(overrides={'CON_SO_': module.text_response(analytics_text.splitlines()[0] + '\\n')})"
                            if fixture_scenario == "analytics_missing_put"
                            else "transport = module.FixtureTransport()"
                        ),
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
                        "urllib.request.build_opener = lambda *handlers: FixtureOpener()",
                    )
                ),
                encoding="utf-8",
            )
            environment["PYTHONPATH"] = str(temporary_root)
            environment["PYTHONNOUSERSITE"] = "1"
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
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise AssertionError("public result must be a JSON object")
    return result


def invoke_injected_batch(
    batch: OptionSourceBatch, request: dict[str, object]
) -> object:
    class InjectedOperation:
        operation_id = "sina_etf_option_snapshot@1"
        acquisition_owners = OptionAcquisitionOwners(
            executor_operation=operation_id,
            identity_operation="sse_etf_list@1",
            reference_operation="sse_etf_snapshot@1",
            option_operation=operation_id,
        )
        raw_fields = SinaEtfOptionSnapshotOperation.raw_fields

        def collect(self, _query: object) -> OptionSourceBatch:
            return batch

    return ResearchRuntime(etf_option_operations=[InjectedOperation()]).research(
        request
    )


def rehash_observation_closure(
    result: UnifiedResearchResult,
    observation: dict[str, object],
    mutate: Callable[[dict[str, object]], None],
) -> None:
    old_observation_id = observation["id"]
    mutate(observation["payload"])
    observation["id"] = stable_result_id(
        "observation",
        {
            "capability_id": observation["capability_id"],
            "operation_id": observation["operation_id"],
            "payload": observation["payload"],
        },
    )
    new_observation_id = observation["id"]

    def replace_reference(value: object, old_id: str, new_id: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if nested == old_id:
                    value[key] = new_id
                else:
                    replace_reference(nested, old_id, new_id)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                if nested == old_id:
                    value[index] = new_id
                else:
                    replace_reference(nested, old_id, new_id)

    operation = result.coverage[observation["capability_id"]]["operations"][
        observation["operation_id"]
    ]
    replace_reference(operation, old_observation_id, new_observation_id)
    record_id_maps: list[tuple[str, str]] = []
    for kind, records in (
        ("claim", result.claims),
        ("calculation", result.calculations),
        ("conflict", result.conflicts),
        ("limitation", result.limitations),
    ):
        for record in records:
            old_record_id = record["id"]
            replace_reference(record, old_observation_id, new_observation_id)
            record["id"] = stable_result_id(
                kind, {key: value for key, value in record.items() if key != "id"}
            )
            record_id_maps.append((old_record_id, record["id"]))
    brief = result.briefs[0]
    replace_reference(brief, old_observation_id, new_observation_id)
    for old_record_id, new_record_id in record_id_maps:
        replace_reference(brief, old_record_id, new_record_id)
    brief["id"] = stable_result_id(
        "brief", {key: value for key, value in brief.items() if key != "id"}
    )
    view = result.task_views["etf_options"]
    replace_reference(view, old_observation_id, new_observation_id)
    for old_record_id, new_record_id in record_id_maps:
        replace_reference(view, old_record_id, new_record_id)
    view["brief_id"] = brief["id"]


def rehash_direct_plan(result: UnifiedResearchResult) -> None:
    result.plan["id"] = stable_result_id(
        "plan",
        {
            "version": result.plan["version"],
            "task_type": result.plan["task_type"],
            "operation_obligations": result.plan["operation_obligations"],
            "research_dependencies": result.plan["research_dependencies"],
            "field_dispositions": [
                {
                    "capability_id": capability_id,
                    "operation_id": operation_id,
                    "fields": operation["field_dispositions"],
                }
                for capability_id, capability in result.coverage.items()
                for operation_id, operation in capability["operations"].items()
            ],
        },
    )


class DirectEtfOptionsRuntimeTests(unittest.TestCase):
    def test_serializer_rejects_rehashed_unlisted_contract_error_scope(self) -> None:
        quote_text = (
            Path(__file__).parent / "fixtures/etf_options/tquotes_510050_2608.txt"
        ).read_text(encoding="utf-8")
        prior_day_quotes = quote_text.replace(
            "2026-07-31 15:00:00", "2026-07-30 15:00:00"
        )
        operation = SinaEtfOptionSnapshotOperation(
            FixtureTransport(overrides={"CON_OP_": text_response(prior_day_quotes)}),
            request_gate=DiagnosticGate(),
        )
        batch = operation.collect(query())
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        result = invoke_injected_batch(batch, request)
        result.to_dict()

        source_error = next(
            item
            for item in result.source_errors
            if item["code"] == "quote_date_mismatch"
        )
        old_error_id = source_error["id"]
        source_error["details"] = {"contract_codes": ["99999999"]}
        source_error["id"] = stable_result_id(
            "source-error",
            {key: value for key, value in source_error.items() if key != "id"},
        )
        new_error_id = source_error["id"]

        def replace_reference(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if nested == old_error_id:
                        value[key] = new_error_id
                    else:
                        replace_reference(nested)
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    if nested == old_error_id:
                        value[index] = new_error_id
                    else:
                        replace_reference(nested)

        replace_reference(result.coverage)
        replace_reference(result.task_views)
        replace_reference(result.briefs)
        brief = result.briefs[0]
        old_brief_id = brief["id"]
        brief["id"] = stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        )
        view = result.task_views["etf_options"]
        if view.get("brief_id") == old_brief_id:
            view["brief_id"] = brief["id"]

        with self.assertRaisesRegex(ValueError, "acquired contract listings"):
            result.to_dict()

    def test_option_authority_rejects_rehashed_existing_sibling_destination(
        self,
    ) -> None:
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        result = ResearchRuntime(
            etf_option_operations=[SinaEtfOptionSnapshotOperation(FixtureTransport())]
        ).research(request)
        operation = result.coverage["etf_option_contracts"]["operations"][
            "sina_etf_option_snapshot@1"
        ]
        next(
            item
            for item in operation["field_dispositions"]
            if item["field"] == "quote[2]"
        )["destination"] = "observations.payload.quote.bid"
        rehash_direct_plan(result)

        with self.assertRaisesRegex(ValueError, "field disposition contract"):
            result.to_dict()

    def test_natural_option_destinations_resolve_or_are_explicitly_excluded(
        self,
    ) -> None:
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        result = (
            ResearchRuntime(
                etf_option_operations=[
                    SinaEtfOptionSnapshotOperation(FixtureTransport())
                ]
            )
            .research(request)
            .to_dict()
        )

        operations = {
            operation_id: operation
            for capability in result["coverage"].values()
            for operation_id, operation in capability["operations"].items()
        }
        reference = {
            item["field"]: item
            for item in operations["sse_etf_snapshot@1"]["field_dispositions"]
        }
        option = {
            item["field"]: item
            for item in operations["sina_etf_option_snapshot@1"]["field_dispositions"]
        }
        self.assertEqual(reference["cpxxextendname"]["disposition"], "excluded")
        self.assertEqual(option["months.result.status.code"]["disposition"], "excluded")
        self.assertEqual(option["analytics[1]"]["disposition"], "excluded")
        self.assertEqual(
            option["months.result.data.stockId"]["destination"],
            "observations.payload.subject_code",
        )
        self.assertEqual(
            option["analytics[12]"]["destination"],
            "observations.payload.contract.security.code",
        )

    def test_copied_installed_etf_options_policy_block_is_formal_unified_result(
        self,
    ) -> None:
        result = invoke_copied_installed(option_request())

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["plan"]["task_type"], "etf_options")
        self.assertEqual(result["status"], "blocked")

    def test_copied_installed_success_retains_all_acquisition_facts_by_owner(
        self,
    ) -> None:
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        result = invoke_copied_installed(request, option_fixtures=True)

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["status"], "limited")
        by_kind: dict[str, list[dict[str, object]]] = {}
        for record in result["observations"]:
            payload = record["payload"]
            by_kind.setdefault(payload["kind"], []).append(record)
            self.assertNotIn("evidence_id", payload)
            self.assertNotIn("evidence_ids", payload)
        self.assertEqual(
            {kind: len(records) for kind, records in by_kind.items()},
            {
                "etf_option_identity": 1,
                "etf_option_reference": 1,
                "etf_option_contract_months": 1,
                "etf_option_contract_listing": 2,
                "etf_option_quote": 2,
                "etf_option_analytics": 2,
            },
        )
        self.assertEqual(
            {record["operation_id"] for record in by_kind["etf_option_identity"]},
            {"sse_etf_list@1"},
        )
        self.assertEqual(
            {record["operation_id"] for record in by_kind["etf_option_reference"]},
            {"sse_etf_snapshot@1"},
        )
        self.assertEqual(
            {
                record["operation_id"]
                for kind in (
                    "etf_option_contract_months",
                    "etf_option_contract_listing",
                    "etf_option_quote",
                    "etf_option_analytics",
                )
                for record in by_kind[kind]
            },
            {"sina_etf_option_snapshot@1"},
        )
        self.assertIn(
            "source_request_paced",
            {item["code"] for item in result["limitations"]},
        )

    def test_copied_installed_partial_analytics_retains_quotes_atm_and_sibling(
        self,
    ) -> None:
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        result = invoke_copied_installed(
            request,
            option_fixtures=True,
            fixture_scenario="analytics_missing_put",
        )

        self.assertEqual(result["status"], "limited")
        kinds = [item["payload"]["kind"] for item in result["observations"]]
        self.assertEqual(kinds.count("etf_option_quote"), 2)
        self.assertEqual(kinds.count("etf_option_analytics"), 1)
        self.assertEqual(
            {item["kind"] for item in result["claims"]},
            {
                "etf_option_identity",
                "etf_option_selection",
                "etf_option_quote",
                "etf_option_analytics",
            },
        )
        self.assertEqual(
            [item["kind"] for item in result["calculations"]],
            ["etf_option_atm"],
        )
        self.assertEqual(len(result["source_errors"]), 1)
        self.assertEqual(
            result["source_errors"][0]["operation_id"],
            "sina_etf_option_snapshot@1",
        )

    def test_field_scoped_partial_analytics_retains_siblings_but_rejects_nonlocal_failures(
        self,
    ) -> None:
        analytics_text = (
            Path(__file__).parent / "fixtures/etf_options/analytics_510050_2608.txt"
        ).read_text(encoding="utf-8")
        invalid_gamma = analytics_text.replace("2.2988", "oops", 1)
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(overrides={"CON_SO_": text_response(invalid_gamma)}),
            request_gate=DiagnosticGate(),
        )
        batch = production.collect(query())

        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        def public(candidate: OptionSourceBatch) -> dict[str, object]:
            class InjectedOperation:
                operation_id = production.operation_id
                acquisition_owners = production.acquisition_owners
                raw_fields = production.raw_fields

                def collect(self, _query: object) -> OptionSourceBatch:
                    return candidate

            return (
                ResearchRuntime(etf_option_operations=[InjectedOperation()])
                .research(request)
                .to_dict()
            )

        result = public(batch)

        self.assertEqual(result["status"], "limited")
        by_kind: dict[str, list[dict[str, object]]] = {}
        for record in result["observations"]:
            payload = record["payload"]
            by_kind.setdefault(payload["kind"], []).append(record)
        self.assertEqual(len(by_kind["etf_option_quote"]), 2)
        self.assertEqual(len(by_kind["etf_option_analytics"]), 2)
        analytics_by_code = {
            record["payload"]["contract"]["security"]["code"]: set(
                record["payload"]["analytics"]
            )
            for record in by_kind["etf_option_analytics"]
        }
        self.assertEqual(
            analytics_by_code["10011855"],
            {
                "delta",
                "theta",
                "vega",
                "implied_volatility",
                "theoretical_value",
            },
        )
        self.assertEqual(
            analytics_by_code["10011864"],
            analytics_by_code["10011855"] | {"gamma"},
        )
        self.assertEqual(
            {item["kind"] for item in result["claims"]},
            {
                "etf_option_identity",
                "etf_option_selection",
                "etf_option_quote",
                "etf_option_analytics",
            },
        )
        self.assertEqual(
            [item["kind"] for item in result["calculations"]],
            ["etf_option_atm"],
        )
        self.assertEqual(len(result["source_errors"]), 1)
        self.assertEqual(
            result["source_errors"][0]["operation_id"],
            "sina_etf_option_snapshot@1",
        )

        first, second = batch.contracts
        no_quote = replace(
            batch,
            contracts=(
                replace(first, quote_state="no_quote", bid=None, ask=None),
                second,
            ),
        )
        quote_error = replace(
            batch,
            coverage={
                **batch.coverage,
                "option_quotes": OptionCoverage(
                    "partial", expected_count=2, observed_count=1
                ),
            },
        )
        non_option_error = replace(
            batch,
            source_errors=(
                OptionSourceFailure(
                    production.acquisition_owners.reference_operation,
                    "batch_response_incomplete",
                    "A non-option owner reported an unrelated failure.",
                    {"contract_codes": ("10011855",)},
                ),
            ),
        )
        for label, rejected in (
            ("no_quote", no_quote),
            ("quote_error", quote_error),
            ("non_option_owner", non_option_error),
        ):
            with self.subTest(label=label):
                blocked = public(rejected)
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(blocked["claims"], [])
                self.assertEqual(blocked["calculations"], [])

    def test_copied_installed_exact_expiry_mismatch_blocks_but_retains_quotes(
        self,
    ) -> None:
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"] = {
            "view": "atm",
            "expiry": {"mode": "exact", "date": "2026-08-25"},
            "quote_mode": "latest_completed",
        }

        result = invoke_copied_installed(request, option_fixtures=True)

        self.assertEqual(result["status"], "blocked")
        kinds = [item["payload"]["kind"] for item in result["observations"]]
        self.assertEqual(kinds.count("etf_option_reference"), 1)
        self.assertEqual(kinds.count("etf_option_quote"), 2)
        self.assertNotIn(
            "etf_option_selection", {item["kind"] for item in result["claims"]}
        )
        self.assertIn(
            "option_expiry_not_available",
            {item["code"] for item in result["limitations"]},
        )

    def test_blocked_serializer_rejects_rehashed_unselected_quote_identity(
        self,
    ) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"] = {
            "view": "atm",
            "expiry": {"mode": "exact", "date": "2026-08-25"},
            "quote_mode": "latest_completed",
        }
        result = invoke_injected_batch(production.collect(query()), request)
        quote = next(
            item
            for item in result.observations
            if item["payload"]["kind"] == "etf_option_quote"
            and item["payload"]["contract"]["security"]["code"] == "10011855"
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.claims)
        rehash_observation_closure(
            result,
            quote,
            lambda payload: payload["contract"]["security"].__setitem__(
                "code", "FORGED"
            ),
        )

        with self.assertRaisesRegex(ValueError, "ETF-option domain facts"):
            result.to_dict()

    def test_atm_serializer_rejects_rehashed_unadopted_quote_analytics_mismatch(
        self,
    ) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        trusted = production.collect(query())
        call, put = trusted.contracts
        far_call = replace(
            call,
            security={**call.security, "code": "10011856"},
            strike="4.00",
            evidence_id="provider-far-call",
            analytics_evidence_id="provider-far-call-analytics",
        )
        far_put = replace(
            put,
            security={**put.security, "code": "10011865"},
            strike="4.00",
            evidence_id="provider-far-put",
            analytics_evidence_id="provider-far-put-analytics",
        )
        call_listing, put_listing = trusted.listing_evidence
        batch = replace(
            trusted,
            contracts=(call, put, far_call, far_put),
            coverage={
                "contract_listing": OptionCoverage("partial", observed_count=4),
                "option_quotes": OptionCoverage(
                    "observed_nonempty", expected_count=4, observed_count=4
                ),
                "provider_analytics": OptionCoverage(
                    "observed_nonempty", expected_count=4, observed_count=4
                ),
            },
            listing_evidence=(
                replace(
                    call_listing,
                    observed_count=2,
                    contract_codes=("10011855", "10011856"),
                ),
                replace(
                    put_listing,
                    observed_count=2,
                    contract_codes=("10011864", "10011865"),
                ),
            ),
        )
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        def build_result() -> UnifiedResearchResult:
            result = invoke_injected_batch(batch, request)
            selection = next(
                item for item in result.claims if item["kind"] == "etf_option_selection"
            )
            selected_codes = {
                item["contract"]["security"]["code"]
                for item in selection["payload"]["contracts"]
            }
            self.assertEqual(selected_codes, {"10011855", "10011864"})
            return result

        def observation(
            result: UnifiedResearchResult, kind: str, code: str | None = None
        ) -> dict[str, object]:
            return next(
                item
                for item in result.observations
                if item["payload"]["kind"] == kind
                and (
                    code is None
                    or item["payload"]
                    .get("contract", {})
                    .get("security", {})
                    .get("code")
                    == code
                )
            )

        def tamper_synchronized_month(result: UnifiedResearchResult) -> None:
            for kind in ("etf_option_quote", "etf_option_analytics"):
                rehash_observation_closure(
                    result,
                    observation(result, kind, "10011856"),
                    lambda payload: payload["contract"].update(
                        {
                            "contract_month": "2099-12",
                            "expiry_date": "2099-12-31",
                        }
                    ),
                )
            call_listing = next(
                item
                for item in result.observations
                if item["payload"]["kind"] == "etf_option_contract_listing"
                and item["payload"]["option_type"] == "call"
            )
            rehash_observation_closure(
                result,
                call_listing,
                lambda payload: payload.__setitem__("contract_month", "2099-12"),
            )

        def tamper_listing_side(result: UnifiedResearchResult) -> None:
            call_listing = next(
                item
                for item in result.observations
                if item["payload"]["kind"] == "etf_option_contract_listing"
                and item["payload"]["option_type"] == "call"
            )
            rehash_observation_closure(
                result,
                call_listing,
                lambda payload: payload.__setitem__("option_type", "put"),
            )

        def tamper_analytics(
            result: UnifiedResearchResult,
            mutate: Callable[[dict[str, object]], None],
        ) -> None:
            rehash_observation_closure(
                result,
                observation(result, "etf_option_analytics", "10011856"),
                lambda payload: mutate(payload["analytics"]),
            )

        tampers = {
            "expiry": lambda result: rehash_observation_closure(
                result,
                observation(result, "etf_option_quote", "10011856"),
                lambda payload: payload["contract"].__setitem__(
                    "expiry_date", "2099-12-31"
                ),
            ),
            "synchronized_month": tamper_synchronized_month,
            "listing_side": tamper_listing_side,
            "quote_state": lambda result: rehash_observation_closure(
                result,
                observation(result, "etf_option_quote", "10011856"),
                lambda payload: payload["quote"].__setitem__("state", "forged"),
            ),
            "quote_last_unit": lambda result: rehash_observation_closure(
                result,
                observation(result, "etf_option_quote", "10011856"),
                lambda payload: payload["quote"]["last"].__setitem__(
                    "unit", "contract"
                ),
            ),
            "quote_schema": lambda result: rehash_observation_closure(
                result,
                observation(result, "etf_option_quote", "10011856"),
                lambda payload: payload["quote"].__setitem__("status", "quoted"),
            ),
            "quote_observed_at": lambda result: rehash_observation_closure(
                result,
                observation(result, "etf_option_quote", "10011856"),
                lambda payload: payload["quote"].__setitem__(
                    "observed_at", "2099-12-31T15:00:00+08:00"
                ),
            ),
            "analytics_name": lambda result: tamper_analytics(
                result,
                lambda analytics: analytics.__setitem__(
                    "rho", dict(analytics["delta"])
                ),
            ),
            "analytics_unit": lambda result: tamper_analytics(
                result,
                lambda analytics: analytics["delta"].__setitem__("unit", "CNY/share"),
            ),
            "analytics_status": lambda result: tamper_analytics(
                result,
                lambda analytics: analytics["delta"].__setitem__(
                    "status", "calculated"
                ),
            ),
        }
        for dimension, tamper in tampers.items():
            with self.subTest(dimension=dimension):
                result = build_result()
                tamper(result)
                with self.assertRaisesRegex(ValueError, "ETF-option domain facts"):
                    result.to_dict()

    def test_external_expected_owner_mismatch_quarantines_the_entire_batch(
        self,
    ) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        trusted = production.collect(query())
        forged = OptionAcquisitionOwners(
            executor_operation="payload_option@1",
            identity_operation="payload_identity@1",
            reference_operation="payload_reference@1",
            option_operation="payload_option@1",
        )
        forged_batch = OptionSourceBatch(
            operation_id=forged.executor_operation,
            owners=forged,
            subject=replace(
                trusted.subject,
                identity_source_operation=forged.identity_operation,
                identity_evidence_id="forged-identity",
            ),
            session=replace(
                trusted.session,
                reference_source_operation=forged.reference_operation,
                reference_evidence_id="forged-reference",
            ),
            contracts=tuple(
                replace(
                    item,
                    source_operation=forged.option_operation,
                    evidence_id=f"forged-quote-{index}",
                    analytics_evidence_id=f"forged-analytics-{index}",
                )
                for index, item in enumerate(trusted.contracts)
            ),
            coverage=trusted.coverage,
            source_errors=(),
            degradations=tuple(
                replace(item, source_operation=forged.option_operation)
                for item in trusted.degradations
            ),
            limitations=trusted.limitations,
            listing_evidence=tuple(
                replace(item, source_operation=forged.option_operation)
                for item in trusted.listing_evidence
            ),
            month_evidence=replace(
                trusted.month_evidence, source_operation=forged.option_operation
            ),
        )

        class InjectedOperation:
            operation_id = production.operation_id
            acquisition_owners = production.acquisition_owners
            raw_fields = production.raw_fields

            def collect(self, _query: object) -> OptionSourceBatch:
                return forged_batch

        request = option_request(allow_experimental=True)
        result = ResearchRuntime(etf_option_operations=[InjectedOperation()]).research(
            request
        )
        public = result.to_dict()

        self.assertEqual(public["status"], "blocked")
        self.assertEqual(public["observations"], [])
        self.assertEqual(len(public["source_errors"]), 1)
        self.assertEqual(
            public["source_errors"][0]["operation_id"],
            "sina_etf_option_snapshot@1",
        )
        self.assertEqual(
            public["source_errors"][0]["code"], "option_source_owner_mismatch"
        )
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("payload_option@1", serialized)
        self.assertNotIn("forged-identity", serialized)
        self.assertNotIn("forged-reference", serialized)

    def test_serializer_recomputes_etf_option_domain_facts_after_rehash(self) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        batch = production.collect(query())

        class InjectedOperation:
            operation_id = production.operation_id
            acquisition_owners = production.acquisition_owners
            raw_fields = production.raw_fields

            def collect(self, _query: object) -> OptionSourceBatch:
                return batch

        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        result = ResearchRuntime(etf_option_operations=[InjectedOperation()]).research(
            request
        )
        selection = next(
            item for item in result.claims if item["kind"] == "etf_option_selection"
        )
        old_claim_id = selection["id"]
        selection["payload"]["contract_set"]["expiry_date"] = "2026-09-23"
        selection["id"] = stable_result_id(
            "claim", {key: value for key, value in selection.items() if key != "id"}
        )
        new_claim_id = selection["id"]
        brief = result.briefs[0]
        brief["claim_ids"] = [
            new_claim_id if item == old_claim_id else item
            for item in brief["claim_ids"]
        ]
        brief["id"] = stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        )
        view = result.task_views["etf_options"]
        view["claim_ids"] = [
            new_claim_id if item == old_claim_id else item for item in view["claim_ids"]
        ]
        view["brief_id"] = brief["id"]

        with self.assertRaisesRegex(ValueError, "ETF-option domain facts"):
            result.to_dict()

    def test_partial_serializer_rejects_rehashed_analytics_sibling_loss(self) -> None:
        analytics_text = (
            Path(__file__).parent / "fixtures/etf_options/analytics_510050_2608.txt"
        ).read_text(encoding="utf-8")
        transport = FixtureTransport(
            overrides={
                "CON_SO_": __import__(
                    "tests.test_etf_option_sources", fromlist=["text_response"]
                ).text_response(analytics_text.splitlines()[0] + "\n")
            }
        )
        production = SinaEtfOptionSnapshotOperation(
            transport, request_gate=DiagnosticGate()
        )
        batch = production.collect(query())

        class InjectedOperation:
            operation_id = production.operation_id
            acquisition_owners = production.acquisition_owners
            raw_fields = production.raw_fields

            def collect(self, _query: object) -> OptionSourceBatch:
                return batch

        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        result = ResearchRuntime(etf_option_operations=[InjectedOperation()]).research(
            request
        )
        analytics_claim = next(
            item for item in result.claims if item["kind"] == "etf_option_analytics"
        )
        old_claim_id = analytics_claim["id"]
        analytics_claim["payload"]["contracts"] = []
        analytics_claim["id"] = stable_result_id(
            "claim",
            {key: value for key, value in analytics_claim.items() if key != "id"},
        )
        new_claim_id = analytics_claim["id"]
        brief = result.briefs[0]
        brief["claim_ids"] = [
            new_claim_id if item == old_claim_id else item
            for item in brief["claim_ids"]
        ]
        brief["id"] = stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        )
        view = result.task_views["etf_options"]
        view["claim_ids"] = [
            new_claim_id if item == old_claim_id else item for item in view["claim_ids"]
        ]
        view["brief_id"] = brief["id"]

        with self.assertRaisesRegex(ValueError, "ETF-option domain facts"):
            result.to_dict()

    def test_serializer_rejects_rehashed_request_session_month_listing_quote_analytics_and_selection_tamper(
        self,
    ) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        batch = production.collect(query())
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        def rehash_selection(
            result: UnifiedResearchResult,
            mutate: Callable[[dict[str, object]], None],
        ) -> None:
            selection = next(
                item for item in result.claims if item["kind"] == "etf_option_selection"
            )
            old_claim_id = selection["id"]
            mutate(selection["payload"])
            selection["id"] = stable_result_id(
                "claim",
                {key: value for key, value in selection.items() if key != "id"},
            )
            new_claim_id = selection["id"]
            brief = result.briefs[0]
            brief["claim_ids"] = [
                new_claim_id if item == old_claim_id else item
                for item in brief["claim_ids"]
            ]
            brief["id"] = stable_result_id(
                "brief", {key: value for key, value in brief.items() if key != "id"}
            )
            view = result.task_views["etf_options"]
            view["claim_ids"] = [
                new_claim_id if item == old_claim_id else item
                for item in view["claim_ids"]
            ]
            view["brief_id"] = brief["id"]

        mutations = {
            "request": lambda payload: payload["request"].__setitem__(
                "subject_clue", "510300"
            ),
            "session": lambda payload: payload["session"].__setitem__(
                "trading_date", "2026-07-30"
            ),
            "month": lambda payload: payload["contract_set"].__setitem__(
                "contract_months", ["2026-09"]
            ),
            "listing": lambda payload: payload["contract_set"].__setitem__(
                "listing_evidence_ids", []
            ),
            "quote": lambda payload: payload["contracts"][0]["quote"][
                "last"
            ].__setitem__("value", "9.9999"),
            "analytics": lambda payload: payload["contracts"][0]["analytics"][
                "delta"
            ].__setitem__("value", "0.1234"),
            "selection": lambda payload: payload["atm"].__setitem__(
                "strike_candidates", ["99.99"]
            ),
            "t_quote": lambda payload: payload["t_quote"]["rows"][0]["call"]["quote"][
                "last"
            ].__setitem__("value", "8.8888"),
        }
        for dimension, mutate in mutations.items():
            with self.subTest(dimension=dimension):
                result = invoke_injected_batch(batch, request)
                rehash_selection(result, mutate)
                with self.assertRaisesRegex(ValueError, "ETF-option domain facts"):
                    result.to_dict()

    def test_duplicate_provider_ids_are_stable_reversible_and_exact_duplicates_dedupe(
        self,
    ) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        trusted = production.collect(query())
        call, put = trusted.contracts
        conflicting_call = replace(call, last="0.0999")
        call_listing, put_listing = trusted.listing_evidence

        def run(contracts: tuple[object, ...]) -> dict[str, object]:
            batch = replace(
                trusted,
                contracts=contracts,
                coverage={
                    "contract_listing": OptionCoverage(
                        "partial", observed_count=len(contracts)
                    ),
                    "option_quotes": OptionCoverage(
                        "observed_nonempty",
                        expected_count=len(contracts),
                        observed_count=len(contracts),
                    ),
                    "provider_analytics": OptionCoverage(
                        "observed_nonempty",
                        expected_count=len(contracts),
                        observed_count=len(contracts),
                    ),
                },
                listing_evidence=(
                    replace(
                        call_listing,
                        observed_count=len(contracts) - 1,
                        contract_codes=tuple(
                            str(item.security["code"])
                            for item in contracts
                            if item.option_type == "call"
                        ),
                    ),
                    put_listing,
                ),
            )

            class InjectedOperation:
                operation_id = production.operation_id
                acquisition_owners = production.acquisition_owners
                raw_fields = production.raw_fields

                def collect(self, _query: object) -> OptionSourceBatch:
                    return batch

            request = option_request(allow_experimental=True)
            request["as_of"] = "2026-08-02"
            request["window"] = {
                "observed_from": "2026-07-31",
                "observed_to": "2026-07-31",
            }
            request["parameters"]["quote_mode"] = "latest_completed"
            result = ResearchRuntime(
                etf_option_operations=[InjectedOperation()]
            ).research(request)
            return result.to_dict()

        forward = run((call, conflicting_call, put))
        reversed_result = run((put, conflicting_call, call))
        exact_duplicate = run((call, call, put))

        def quote_ids(result: dict[str, object]) -> set[str]:
            return {
                item["id"]
                for item in result["observations"]
                if item["payload"]["kind"] == "etf_option_quote"
            }

        self.assertEqual(quote_ids(forward), quote_ids(reversed_result))
        self.assertEqual(len(quote_ids(forward)), 3)
        self.assertEqual(len(quote_ids(exact_duplicate)), 2)
        self.assertIn(
            "duplicate_option_contract_conflict",
            {item["code"] for item in forward["conflicts"]},
        )

    def test_success_closes_formal_references_and_static_raw_dispositions(self) -> None:
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"
        result = invoke_copied_installed(request, option_fixtures=True)
        observation_ids = {item["id"] for item in result["observations"]}

        def assert_closed(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key.endswith("evidence_ids"):
                        self.assertTrue(set(nested).issubset(observation_ids))
                    assert_closed(nested)
            elif isinstance(value, list):
                for nested in value:
                    assert_closed(nested)

        assert_closed(result["claims"])
        assert_closed(result["calculations"])
        operations = {
            operation_id: operation
            for capability in result["coverage"].values()
            for operation_id, operation in capability["operations"].items()
        }
        self.assertEqual(
            {
                item["field"]
                for item in operations["sina_etf_option_snapshot@1"][
                    "field_dispositions"
                ]
            },
            set(SinaEtfOptionSnapshotOperation.raw_fields),
        )
        self.assertTrue(
            all(operation["field_dispositions"] for operation in operations.values())
        )
        dependencies = {
            (item["consumer_id"], item["evidence_use"])
            for item in result["plan"]["research_dependencies"]
        }
        self.assertEqual(
            dependencies,
            {
                ("etf_option_identity", "etf_option_identity"),
                ("etf_option_selection", "etf_option_selection"),
                ("etf_option_quote", "etf_option_quote"),
                ("etf_option_analytics", "etf_option_analytics"),
                ("etf_option_atm", "etf_option_atm"),
            },
        )

    def test_three_owner_raw_field_dispositions_close_in_success_skip_and_failure(
        self,
    ) -> None:
        success_request = option_request(allow_experimental=True)
        success_request["as_of"] = "2026-08-02"
        success_request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        success_request["parameters"]["quote_mode"] = "latest_completed"
        success = invoke_copied_installed(success_request, option_fixtures=True)
        skipped = invoke_copied_installed(option_request())

        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        forged_owners = OptionAcquisitionOwners(
            executor_operation="payload_option@1",
            identity_operation="payload_identity@1",
            reference_operation="payload_reference@1",
            option_operation="payload_option@1",
        )
        forged_batch = OptionSourceBatch(
            operation_id=forged_owners.executor_operation,
            owners=forged_owners,
        )

        class InjectedOperation:
            operation_id = production.operation_id
            acquisition_owners = production.acquisition_owners
            raw_fields = production.raw_fields

            def collect(self, _query: object) -> OptionSourceBatch:
                return forged_batch

        failed = (
            ResearchRuntime(etf_option_operations=[InjectedOperation()])
            .research(option_request(allow_experimental=True))
            .to_dict()
        )

        expected = {
            "sse_etf_list@1": {
                "fundCode": "normalized",
                "fundAbbr": "excluded",
                "secNameFull": "normalized",
                "companyName": "excluded",
                "listingDate": "normalized",
                "fundType": "excluded",
                "subClass": "excluded",
            },
            "sse_etf_snapshot@1": {
                "name": "excluded",
                "cpxxextendname": "excluded",
                "open": "excluded",
                "high": "excluded",
                "low": "excluded",
                "last": "normalized",
                "prev_close": "excluded",
                "chg_rate": "excluded",
                "volume": "excluded",
                "amount": "excluded",
                "tradephase": "excluded",
                "code": "cross_check_only",
                "date": "normalized",
                "time": "normalized",
            },
            "sina_etf_option_snapshot@1": {
                **{
                    field: "normalized"
                    for field in (
                        "months.result.data.contractMonth[]",
                        "listing[]",
                        "quote[0]",
                        "quote[1]",
                        "quote[2]",
                        "quote[3]",
                        "quote[4]",
                        "quote[5]",
                        "quote[7]",
                        "quote[32]",
                        "quote[41]",
                        "quote[43]",
                        "quote[45]",
                        "quote[46]",
                        "analytics[5]",
                        "analytics[6]",
                        "analytics[7]",
                        "analytics[8]",
                        "analytics[9]",
                        "analytics[15]",
                    )
                },
                **{
                    field: "cross_check_only"
                    for field in (
                        "months.result.data.stockId",
                        "quote[36]",
                        "analytics[4]",
                        "analytics[12]",
                        "analytics[13]",
                        "analytics[14]",
                        "analytics[16]",
                    )
                },
                **{
                    field: "excluded"
                    for field in (
                        "months.result.status.code",
                        "analytics[1]",
                        "analytics[2]",
                        "analytics[3]",
                    )
                },
            },
        }

        def operations(result: dict[str, object]) -> dict[str, dict[str, object]]:
            return {
                operation_id: operation
                for capability in result["coverage"].values()
                for operation_id, operation in capability["operations"].items()
            }

        for label, result, states in (
            (
                "success",
                success,
                {
                    "sse_etf_list@1": "complete",
                    "sse_etf_snapshot@1": "complete",
                    "sina_etf_option_snapshot@1": "partial",
                },
            ),
            (
                "skip",
                skipped,
                {
                    "sse_etf_list@1": "skipped_policy",
                    "sse_etf_snapshot@1": "skipped_policy",
                    "sina_etf_option_snapshot@1": "skipped_policy",
                },
            ),
            (
                "failure",
                failed,
                {
                    "sse_etf_list@1": "skipped_research_dependency",
                    "sse_etf_snapshot@1": "skipped_research_dependency",
                    "sina_etf_option_snapshot@1": "failed",
                },
            ),
        ):
            with self.subTest(label=label):
                actual = operations(result)
                self.assertEqual(
                    {
                        operation_id: item["state"]
                        for operation_id, item in actual.items()
                    },
                    states,
                )
                self.assertEqual(
                    {
                        operation_id: {
                            disposition["field"]: disposition["disposition"]
                            for disposition in item["field_dispositions"]
                        }
                        for operation_id, item in actual.items()
                    },
                    expected,
                )

    def test_decimal_equivalent_strikes_and_atm_tie_are_recomputed(self) -> None:
        production = SinaEtfOptionSnapshotOperation(
            FixtureTransport(), request_gate=DiagnosticGate()
        )
        trusted = production.collect(query())
        call, put = trusted.contracts
        second_call = replace(
            call,
            security={**call.security, "code": "10011856"},
            strike="3.0600",
            evidence_id="provider-duplicate-id",
            analytics_evidence_id="provider-analytics-duplicate-id",
        )
        second_put = replace(
            put,
            security={**put.security, "code": "10011865"},
            strike="3.0600",
            evidence_id="provider-duplicate-id",
            analytics_evidence_id="provider-analytics-duplicate-id",
        )
        call_listing, put_listing = trusted.listing_evidence
        batch = replace(
            trusted,
            session=replace(trusted.session, reference_price="3.03"),
            contracts=(
                replace(call, strike="3.0"),
                replace(put, strike="3.00"),
                second_call,
                second_put,
            ),
            coverage={
                "contract_listing": OptionCoverage("partial", observed_count=4),
                "option_quotes": OptionCoverage(
                    "observed_nonempty", expected_count=4, observed_count=4
                ),
                "provider_analytics": OptionCoverage(
                    "observed_nonempty", expected_count=4, observed_count=4
                ),
            },
            listing_evidence=(
                replace(
                    call_listing,
                    observed_count=2,
                    contract_codes=("10011855", "10011856"),
                ),
                replace(
                    put_listing,
                    observed_count=2,
                    contract_codes=("10011864", "10011865"),
                ),
            ),
        )
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        result = invoke_injected_batch(batch, request).to_dict()
        selection = next(
            item for item in result["claims"] if item["kind"] == "etf_option_selection"
        )

        self.assertEqual(selection["payload"]["atm"]["status"], "tie")
        self.assertEqual(
            selection["payload"]["atm"]["strike_candidates"],
            ["3.0", "3.0600"],
        )
        self.assertEqual(len(selection["payload"]["contracts"]), 4)

    def test_no_quote_or_completed_mode_mismatch_blocks_without_losing_facts(
        self,
    ) -> None:
        quote_text = (
            Path(__file__).parent / "fixtures/etf_options/tquotes_510050_2608.txt"
        ).read_text(encoding="utf-8")
        no_call_quote = quote_text.replace(
            '="2,0.0710,0.0712,0.0712,25',
            '="2,0,0,0,25',
            1,
        )
        no_quote = SinaEtfOptionSnapshotOperation(
            FixtureTransport(overrides={"CON_OP_": text_response(no_call_quote)}),
            request_gate=DiagnosticGate(),
        ).collect(query())
        request = option_request(allow_experimental=True)
        request["as_of"] = "2026-08-02"
        request["window"] = {
            "observed_from": "2026-07-31",
            "observed_to": "2026-07-31",
        }
        request["parameters"]["quote_mode"] = "latest_completed"

        no_quote_result = invoke_injected_batch(no_quote, request).to_dict()
        intraday = replace(
            SinaEtfOptionSnapshotOperation(
                FixtureTransport(), request_gate=DiagnosticGate()
            ).collect(query()),
            session=replace(no_quote.session, market_state="intraday"),
        )
        intraday_result = invoke_injected_batch(intraday, request).to_dict()

        self.assertEqual(no_quote_result["status"], "blocked")
        self.assertEqual(intraday_result["status"], "blocked")
        self.assertEqual(
            sum(
                item["payload"]["kind"] == "etf_option_quote"
                for item in no_quote_result["observations"]
            ),
            2,
        )
        self.assertEqual(
            sum(
                item["payload"]["kind"] == "etf_option_quote"
                for item in intraday_result["observations"]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
