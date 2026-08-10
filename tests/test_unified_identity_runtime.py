from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
IDENTITY_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from a_share_research.identity_resolution import (  # noqa: E402
    build_security_identity_research_result,
)
from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    IdentityOperationRequest,
    build_default_identity_operations,
)
from a_share_research.research_result import (  # noqa: E402
    FieldDisposition,
    OperationOutcome,
    UnifiedResearchResult,
)
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402


def identity_request(*, allow_experimental: bool) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "security_identity",
        "subjects": [{"clue": "600519"}],
        "as_of": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "window": None,
        "parameters": {},
        "source_policy": {
            "allow_experimental": allow_experimental,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def invoke_installed_identity(
    request_payload: dict[str, object],
    *,
    fixture_routes: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        installed = temporary_root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request = temporary_root / "request.json"
        request.write_text(json.dumps(request_payload), encoding="utf-8")
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        command = [sys.executable]
        if fixture_routes is None:
            command.append("-I")
        else:
            route_literals = {
                marker: Path(IDENTITY_FIXTURES, filename).read_bytes()
                for marker, filename in fixture_routes.items()
            }
            sitecustomize = temporary_root / "sitecustomize.py"
            sitecustomize.write_text(
                "\n".join(
                    (
                        "from email.message import Message",
                        "import urllib.request",
                        f"routes = {route_literals!r}",
                        "class FixtureResponse:",
                        "    status = 200",
                        "    def __init__(self, body):",
                        "        self.body = body",
                        "        self.headers = Message()",
                        "        self.headers['Content-Type'] = 'application/json'",
                        "    def __enter__(self): return self",
                        "    def __exit__(self, *args): return None",
                        "    def read(self, limit=-1): return self.body[:limit]",
                        "class FixtureOpener:",
                        "    def open(self, request, timeout):",
                        "        body = next((value for marker, value in routes.items() if marker in request.full_url), None)",
                        "        if body is None: raise AssertionError('unexpected identity source')",
                        "        return FixtureResponse(body)",
                        "urllib.request.build_opener = lambda: FixtureOpener()",
                    )
                ),
                encoding="utf-8",
            )
            environment["PYTHONNOUSERSITE"] = "1"
            environment["PYTHONPATH"] = str(temporary_root)
        command.extend(
            (
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request),
            )
        )
        return subprocess.run(
            command,
            cwd=temporary_root,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )


class DeterministicJsonTransport:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload, ensure_ascii=False).encode()

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        return HttpResponse(
            status=200,
            content_type="application/json",
            body=self.body,
            retrieved_at=datetime(2026, 8, 10, tzinfo=timezone(timedelta(hours=8))),
        )


class UnifiedIdentityPublicSeamTests(unittest.TestCase):
    def test_installed_entrypoint_has_no_identity_specific_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            installed = temporary_root / "installed" / "a-share-research"
            shutil.copytree(SKILL_ROOT, installed)
            entrypoint = installed / "scripts" / "entrypoint.py"
            help_result = subprocess.run(
                [sys.executable, "-I", str(entrypoint), "--help"],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )
            resolve_result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(entrypoint),
                    "resolve",
                    "--query",
                    "600519",
                    "--as-of",
                    "2026-08-10",
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertNotIn("resolve", help_result.stdout)
        self.assertNotEqual(resolve_result.returncode, 0)
        self.assertEqual(resolve_result.stdout, "")

    def test_installed_identity_policy_block_uses_the_unified_result(self) -> None:
        request_payload = identity_request(allow_experimental=False)
        completed = invoke_installed_identity(request_payload)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(
            set(result),
            {
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
            },
        )
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["research_anchor"],
            {"as_of": request_payload["as_of"], "timezone": "Asia/Shanghai"},
        )
        self.assertEqual(result["plan"]["task_type"], "security_identity")
        self.assertNotIn("task_type", result)
        view = result["task_views"]["security_identity"]
        self.assertEqual(
            set(view),
            {
                "status",
                "observation_ids",
                "claim_ids",
                "calculation_ids",
                "conflict_ids",
                "limitation_ids",
                "source_error_ids",
                "brief_id",
            },
        )
        self.assertEqual(view["status"], "blocked")

    def test_installed_identity_success_retains_operation_owned_observations(
        self,
    ) -> None:
        request_payload = identity_request(allow_experimental=True)
        completed = invoke_installed_identity(
            request_payload,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["research_anchor"],
            {"as_of": request_payload["as_of"], "timezone": "Asia/Shanghai"},
        )
        self.assertEqual(
            result["coverage"]["security_identity_resolution"]["state"], "complete"
        )
        operations = result["coverage"]["security_identity_resolution"]["operations"]
        self.assertEqual(
            {operation["state"] for operation in operations.values()},
            {"complete", "observed_empty"},
        )
        for operation_id, operation in operations.items():
            self.assertEqual(operation["capability_id"], "security_identity_resolution")
            self.assertEqual(operation["operation_id"], operation_id)
            self.assertIn("field_dispositions", operation)
            self.assertIn("diagnostics", operation)
        self.assertEqual(len(result["observations"]), 2)
        for observation in result["observations"]:
            owner = operations[observation["operation_id"]]
            self.assertIn(observation["id"], owner["observation_ids"])
        self.assertEqual(len(result["claims"]), 1)
        claim = result["claims"][0]
        self.assertEqual(claim["payload"]["security"]["exchange"], "SSE")
        self.assertEqual(claim["payload"]["security"]["code"], "600519")
        view = result["task_views"]["security_identity"]
        self.assertEqual(
            view["observation_ids"], [item["id"] for item in result["observations"]]
        )
        self.assertEqual(view["claim_ids"], [claim["id"]])
        cninfo_dispositions = {
            item["field"]: item
            for item in operations["cninfo_security_dictionary@1"]["field_dispositions"]
        }
        self.assertEqual(
            cninfo_dispositions["pinyin"],
            {
                "field": "pinyin",
                "disposition": "excluded",
                "destination": None,
                "reason": "provider_search_alias_not_needed_for_identity",
            },
        )
        self.assertNotIn("gzmt", completed.stdout)

    def test_installed_identity_local_source_failure_is_limited(self) -> None:
        request_payload = identity_request(allow_experimental=True)
        request_payload["subjects"] = [{"clue": "SSE:600519"}]
        completed = invoke_installed_identity(
            request_payload,
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "empty_body.txt",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(
            result["research_anchor"],
            {"as_of": request_payload["as_of"], "timezone": "Asia/Shanghai"},
        )
        capability = result["coverage"]["security_identity_resolution"]
        self.assertEqual(capability["state"], "partial")
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(len(result["source_errors"]), 1)
        error = result["source_errors"][0]
        self.assertEqual(error["operation_id"], "szse_stock_list@1")
        self.assertIn(
            error["id"],
            capability["operations"]["szse_stock_list@1"]["source_error_ids"],
        )

    def test_installed_szse_identity_does_not_promote_cninfo_org_id(self) -> None:
        request_payload = identity_request(allow_experimental=True)
        request_payload["subjects"] = [{"clue": "000001"}]
        completed = invoke_installed_identity(
            request_payload,
            fixture_routes={
                "query.sse.com.cn": "sse_empty.json",
                "www.szse.cn": "szse_000001.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        claim = result["claims"][0]["payload"]
        self.assertEqual(claim["security"]["exchange"], "SZSE")
        self.assertEqual(claim["issuer"]["identifier"], None)
        self.assertEqual(
            claim["issuer"]["security_relationship"],
            "unverified",
        )
        cninfo = next(
            item
            for item in result["observations"]
            if item["operation_id"] == "cninfo_security_dictionary@1"
        )
        self.assertEqual(cninfo["payload"]["observation"]["exchange"], None)
        self.assertEqual(
            cninfo["payload"]["observation"]["issuer_identifier"],
            "gssz0000001",
        )
        self.assertIs(
            cninfo["payload"]["observation"]["issuer_relationship_verified"],
            False,
        )

    def test_installed_identity_name_discovery_stays_on_the_unified_path(
        self,
    ) -> None:
        request_payload = identity_request(allow_experimental=True)
        request_payload["subjects"] = [{"clue": "贵州茅台"}]
        completed = invoke_installed_identity(
            request_payload,
            fixture_routes={
                "productid=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0": "sse_empty.json",
                "productid=600519": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_stocks.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "limited")
        security = result["claims"][0]["payload"]["security"]
        self.assertEqual(security["exchange"], "SSE")
        self.assertEqual(security["code"], "600519")
        self.assertEqual(security["type"], "A_SHARE")
        sse_coverage = result["coverage"]["security_identity_resolution"]["operations"][
            "sse_stock_list@1"
        ]
        self.assertEqual(sse_coverage["state"], "complete")
        self.assertEqual(sse_coverage["diagnostics"]["attempts"], 2)

    def test_installed_identity_conflict_is_zero_exit_blocked(self) -> None:
        completed = invoke_installed_identity(
            identity_request(allow_experimental=True),
            fixture_routes={
                "query.sse.com.cn": "sse_600519.json",
                "www.szse.cn": "szse_empty.json",
                "www.cninfo.com.cn": "cninfo_name_conflict.json",
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(result["claims"], [])
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["code"], "source_identity_conflict")
        self.assertEqual(
            set(conflict["observation_ids"]),
            {item["id"] for item in result["observations"]},
        )
        self.assertIn(
            conflict["id"],
            result["task_views"]["security_identity"]["conflict_ids"],
        )

    def test_installed_identity_rejects_unknown_and_legacy_request_fields(
        self,
    ) -> None:
        requests: dict[str, dict[str, object]] = {}
        unknown_top = identity_request(allow_experimental=False)
        unknown_top["private_token_SECRET_VALUE"] = True
        requests["unknown_top"] = unknown_top

        unknown_policy = identity_request(allow_experimental=False)
        policy = unknown_policy["source_policy"]
        assert isinstance(policy, dict)
        policy["private_token_SECRET_VALUE"] = True
        requests["unknown_policy"] = unknown_policy

        unknown_subject = identity_request(allow_experimental=False)
        subjects = unknown_subject["subjects"]
        assert isinstance(subjects, list)
        assert isinstance(subjects[0], dict)
        subjects[0]["private_token_SECRET_VALUE"] = True
        requests["unknown_subject"] = unknown_subject

        unknown_parameters = identity_request(allow_experimental=False)
        parameters = unknown_parameters["parameters"]
        assert isinstance(parameters, dict)
        parameters["legacy_mode"] = True
        requests["unknown_identity_parameters"] = unknown_parameters

        legacy_subject = identity_request(allow_experimental=False)
        legacy_subject["subjects"] = [{"query": "600519"}]
        requests["legacy_subject_shape"] = legacy_subject

        for case, request_payload in requests.items():
            with self.subTest(case=case):
                completed = invoke_installed_identity(request_payload)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertTrue(
                    completed.stderr.startswith("error: invalid research task:")
                )
                self.assertNotIn("SECRET_VALUE", completed.stderr)


class OperationOutcomeAdapterTests(unittest.TestCase):
    def test_sse_adapter_fails_closed_on_an_undeclared_actual_field(self) -> None:
        payload = {
            "result": [
                {
                    "SECURITY_CODE_A": "600519",
                    "SECURITY_ABBR_A": "贵州茅台",
                    "FULLNAME": "贵州茅台酒股份有限公司",
                    "STATE_CODE_A_DESC": "上市",
                    "COMPANY_CODE": "600519",
                    "UNDECLARED_ACTUAL_FIELD": "must-not-disappear",
                }
            ]
        }

        adapter = next(
            item
            for item in build_default_identity_operations(
                DeterministicJsonTransport(payload)
            )
            if item.operation_id == "sse_stock_list@1"
        )

        outcome = adapter.execute(
            IdentityOperationRequest(query="600519", as_of="2026-08-10"),
            timeout_seconds=1.0,
        )

        self.assertEqual(outcome.coverage, "failed")
        self.assertEqual(outcome.observations, ())
        self.assertEqual(outcome.source_errors[0]["code"], "unknown_schema")

    def test_szse_and_cninfo_adapters_fail_closed_on_undeclared_fields(self) -> None:
        cases = (
            (
                "szse_stock_list@1",
                "000001",
                [
                    {
                        "metadata": {
                            "catalogid": "1110",
                            "tabkey": "tab1",
                        },
                        "data": [
                            {
                                "agdm": "000001",
                                "agjc": "平安银行",
                                "agssrq": "1991-04-03",
                                "UNDECLARED_ACTUAL_FIELD": "must-not-disappear",
                            }
                        ],
                    }
                ],
            ),
            (
                "cninfo_security_dictionary@1",
                "000001",
                {
                    "stockList": [
                        {
                            "code": "000001",
                            "zwjc": "平安银行",
                            "category": "A股",
                            "orgId": "gssz0000001",
                            "pinyin": "payh",
                            "UNDECLARED_ACTUAL_FIELD": "must-not-disappear",
                        }
                    ]
                },
            ),
        )
        for operation_id, query, payload in cases:
            with self.subTest(operation_id=operation_id):
                adapter = next(
                    item
                    for item in build_default_identity_operations(
                        DeterministicJsonTransport(payload)
                    )
                    if item.operation_id == operation_id
                )
                outcome = adapter.execute(
                    IdentityOperationRequest(query=query, as_of="2026-08-10"),
                    timeout_seconds=1.0,
                )

                self.assertEqual(outcome.coverage, "failed")
                self.assertEqual(outcome.observations, ())
                self.assertEqual(
                    outcome.source_errors[0]["code"],
                    "unknown_schema",
                )

    def test_successful_actual_fields_all_have_a_disposition(self) -> None:
        cases = (
            (
                "sse_stock_list@1",
                "600519",
                {
                    "result": [
                        {
                            "SECURITY_CODE_A": "600519",
                            "SECURITY_ABBR_A": "贵州茅台",
                            "FULLNAME": "贵州茅台酒股份有限公司",
                            "STATE_CODE_A_DESC": "上市",
                            "COMPANY_CODE": "600519",
                        }
                    ]
                },
            ),
            (
                "szse_stock_list@1",
                "000001",
                [
                    {
                        "metadata": {"catalogid": "1110", "tabkey": "tab1"},
                        "data": [
                            {
                                "agdm": "000001",
                                "agjc": "平安银行",
                                "agssrq": "1991-04-03",
                            }
                        ],
                    }
                ],
            ),
            (
                "cninfo_security_dictionary@1",
                "000001",
                {
                    "stockList": [
                        {
                            "code": "000001",
                            "zwjc": "平安银行",
                            "category": "A股",
                            "orgId": "gssz0000001",
                            "pinyin": "payh",
                        }
                    ]
                },
            ),
        )
        for operation_id, query, payload in cases:
            with self.subTest(operation_id=operation_id):
                actual_row = (
                    payload[0]["data"][0]
                    if isinstance(payload, list)
                    else next(iter(payload.values()))[0]
                )

                adapter = next(
                    item
                    for item in build_default_identity_operations(
                        DeterministicJsonTransport(payload)
                    )
                    if item.operation_id == operation_id
                )
                outcome = adapter.execute(
                    IdentityOperationRequest(query=query, as_of="2026-08-10"),
                    timeout_seconds=1.0,
                )

                self.assertEqual(outcome.coverage, "complete")
                self.assertEqual(
                    {item.field for item in outcome.field_dispositions},
                    set(actual_row),
                )
                self.assertLessEqual(
                    {item.disposition for item in outcome.field_dispositions},
                    {"normalized", "cross_check_only", "lineage_only", "excluded"},
                )

    def test_runtime_returns_the_identity_unified_result_directly(self) -> None:
        class NeverTransport:
            def get(self, url: str, headers: dict[str, str]) -> object:
                raise AssertionError("policy-blocked identity must not use transport")

        runtime = ResearchRuntime(identity_transport=NeverTransport())  # type: ignore[arg-type]
        result = runtime.research(identity_request(allow_experimental=False))

        self.assertIsInstance(result, UnifiedResearchResult)
        self.assertFalse(hasattr(runtime, "_trace_sink"))

    def test_deterministic_adapter_returns_one_owned_outcome(self) -> None:
        class DeterministicAdapter:
            capability_id = "security_identity_resolution"
            operation_id = "fixture_identity@1"
            source_role = "authoritative_disclosure"
            field_dispositions = ()

            def execute(
                self,
                request: IdentityOperationRequest,
                timeout_seconds: float,
            ) -> OperationOutcome:
                return OperationOutcome(
                    capability_id=self.capability_id,
                    operation_id=self.operation_id,
                    coverage="complete",
                    observations=(
                        {
                            "source_operation": self.operation_id,
                            "query": request.query,
                        },
                    ),
                    field_dispositions=(
                        FieldDisposition(
                            "code",
                            "normalized",
                            "observations[].payload.observation.code",
                        ),
                    ),
                    diagnostics={"attempts": 1, "timeout_seconds": timeout_seconds},
                )

        outcome = DeterministicAdapter().execute(
            IdentityOperationRequest(query="600519", as_of="2026-08-10"),
            timeout_seconds=1.0,
        )

        self.assertEqual(outcome.capability_id, "security_identity_resolution")
        self.assertEqual(outcome.operation_id, "fixture_identity@1")
        self.assertEqual(outcome.coverage, "complete")
        self.assertEqual(outcome.observations[0]["query"], "600519")

    def test_identity_fold_accepts_only_the_supplied_deterministic_adapters(
        self,
    ) -> None:
        class DeterministicAdapter:
            capability_id = "security_identity_resolution"
            field_dispositions = ()

            def __init__(
                self,
                operation_id: str,
                source_role: str,
                identity: dict[str, object],
            ) -> None:
                self.operation_id = operation_id
                self.source_role = source_role
                self.identity = identity

            def execute(
                self,
                request: IdentityOperationRequest,
                timeout_seconds: float,
            ) -> OperationOutcome:
                return OperationOutcome(
                    capability_id=self.capability_id,
                    operation_id=self.operation_id,
                    coverage="complete",
                    observations=(
                        {
                            "source_role": self.source_role,
                            "source_operation": self.operation_id,
                            "experimental": True,
                            "subject": {"issuer": "贵州茅台股份有限公司"},
                            "observation": self.identity,
                            "retrieved_at": f"{request.as_of}T10:30:00+08:00",
                        },
                    ),
                    diagnostics={
                        "attempts": 1,
                        "timeout_seconds": timeout_seconds,
                    },
                )

        request = identity_request(allow_experimental=True)
        operations = (
            DeterministicAdapter(
                "sse_stock_list@1",
                "authoritative_disclosure",
                {
                    "exchange": "SSE",
                    "code": "600519",
                    "name": "贵州茅台",
                    "valid_from": "2001-08-27",
                    "issuer_relationship_verified": True,
                },
            ),
            DeterministicAdapter(
                "cninfo_security_dictionary@1",
                "market_observation",
                {
                    "exchange": "SSE",
                    "code": "600519",
                    "name": "贵州茅台",
                    "issuer_identifier": "gssh0600519",
                    "issuer_relationship_verified": True,
                },
            ),
        )

        result = build_security_identity_research_result(request, operations).to_dict()

        self.assertEqual(result["status"], "limited")
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(
            set(result["coverage"]["security_identity_resolution"]["operations"]),
            {"sse_stock_list@1", "cninfo_security_dictionary@1"},
        )

    def test_operation_outcome_rejects_sibling_owned_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "sibling observation"):
            OperationOutcome(
                capability_id="security_identity_resolution",
                operation_id="fixture_identity@1",
                coverage="complete",
                observations=(
                    {"source_operation": "sibling_identity@1", "code": "600519"},
                ),
            )
        with self.assertRaisesRegex(ValueError, "sibling source error"):
            OperationOutcome(
                capability_id="security_identity_resolution",
                operation_id="fixture_identity@1",
                coverage="failed",
                source_errors=(
                    {
                        "source_operation": "sibling_identity@1",
                        "code": "failed",
                        "message": "safe",
                    },
                ),
            )

    def test_operation_outcome_rejects_nested_sensitive_diagnostics(self) -> None:
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            OperationOutcome(
                capability_id="security_identity_resolution",
                operation_id="fixture_identity@1",
                coverage="observed_empty",
                diagnostics={"network": {"authorization_header": "SECRET_VALUE"}},
            )

    def test_operation_outcome_rechecks_ownership_before_serialization(self) -> None:
        outcome = OperationOutcome(
            capability_id="security_identity_resolution",
            operation_id="fixture_identity@1",
            coverage="complete",
            observations=(
                {
                    "source_operation": "fixture_identity@1",
                    "code": "600519",
                },
            ),
        )
        observation = outcome.observations[0]
        assert isinstance(observation, dict)
        observation["source_operation"] = "sibling_identity@1"

        with self.assertRaisesRegex(ValueError, "sibling observation"):
            outcome.to_coverage_dict(
                observation_ids=["observation-one"],
                source_error_ids=[],
            )

    def test_public_serialization_rechecks_operation_ownership(self) -> None:
        result = UnifiedResearchResult(
            status="blocked",
            research_anchor={"as_of": "2026-08-10", "timezone": "Asia/Shanghai"},
            plan={
                "id": "security_identity",
                "version": "1.0",
                "task_type": "security_identity",
                "capabilities": ["security_identity_resolution"],
            },
            capability_registry={},
            coverage={
                "security_identity_resolution": {
                    "state": "complete",
                    "operations": {
                        "fixture_identity@1": {
                            "capability_id": "security_identity_resolution",
                            "operation_id": "fixture_identity@1",
                            "state": "complete",
                            "observation_ids": ["observation-sibling"],
                            "source_error_ids": [],
                            "field_dispositions": [],
                            "diagnostics": {},
                        }
                    },
                }
            },
            observations=(
                {
                    "id": "observation-sibling",
                    "capability_id": "security_identity_resolution",
                    "operation_id": "sibling_identity@1",
                    "payload": {"source_operation": "sibling_identity@1"},
                },
            ),
            claims=(),
            calculations=(),
            conflicts=(),
            limitations=(
                {
                    "id": "limitation-one",
                    "code": "blocked",
                    "message": "blocked",
                    "references": [],
                },
            ),
            source_errors=(),
            task_views={
                "security_identity": {
                    "status": "blocked",
                    "observation_ids": ["observation-sibling"],
                    "claim_ids": [],
                    "calculation_ids": [],
                    "conflict_ids": [],
                    "limitation_ids": ["limitation-one"],
                    "source_error_ids": [],
                    "brief_id": "brief-one",
                }
            },
            briefs=(
                {
                    "id": "brief-one",
                    "status": "blocked",
                    "summary": "blocked",
                    "observation_ids": ["observation-sibling"],
                    "claim_ids": [],
                    "calculation_ids": [],
                    "conflict_ids": [],
                    "limitation_ids": ["limitation-one"],
                    "source_error_ids": [],
                },
            ),
        )

        with self.assertRaisesRegex(ValueError, "sibling observation"):
            result.to_dict()

    def test_public_serialization_rejects_unknown_response_fields(self) -> None:
        class UnusedAdapter:
            capability_id = "security_identity_resolution"
            operation_id = "fixture_identity@1"
            source_role = "authoritative_disclosure"
            field_dispositions = ()

            def execute(
                self,
                request: IdentityOperationRequest,
                timeout_seconds: float,
            ) -> OperationOutcome:
                raise AssertionError("policy-blocked requests must not execute")

        result = build_security_identity_research_result(
            identity_request(allow_experimental=False),
            (UnusedAdapter(),),
        )
        brief = result.briefs[0]
        assert isinstance(brief, dict)
        brief["legacy_evidence"] = []

        with self.assertRaisesRegex(ValueError, "strict result schema"):
            result.to_dict()

    def test_public_serialization_rejects_unknown_research_anchor_fields(
        self,
    ) -> None:
        class UnusedAdapter:
            capability_id = "security_identity_resolution"
            operation_id = "fixture_identity@1"
            source_role = "authoritative_disclosure"
            field_dispositions = ()

            def execute(
                self,
                request: IdentityOperationRequest,
                timeout_seconds: float,
            ) -> OperationOutcome:
                raise AssertionError("policy-blocked requests must not execute")

        result = build_security_identity_research_result(
            identity_request(allow_experimental=False),
            (UnusedAdapter(),),
        )
        anchor = result.research_anchor
        assert isinstance(anchor, dict)
        anchor["legacy_time"] = "00:00:00"

        with self.assertRaisesRegex(ValueError, "strict result schema"):
            result.to_dict()


if __name__ == "__main__":
    unittest.main()
