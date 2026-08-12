from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from ast import Compare, Name, NotIn, Set, parse, walk
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from a_share_research.evidence_store import (  # noqa: E402
    _MAX_FRESH_ACQUISITION_CLOCK_SKEW,
    EvidenceReusePolicy,
    EvidenceStoreRequest,
    _record_id,
    parse_evidence_store_request,
    resolve_operation_evidence,
)
from a_share_research.identity_sources import HttpResponse  # noqa: E402
from a_share_research.report_sources import EastmoneyReportOperation  # noqa: E402
from a_share_research.research_result import (  # noqa: E402
    OperationExecution,
    OperationOutcome,
    UnifiedResearchResult,
    stable_result_id,
)
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402
from a_share_research.source_throttle import SerialRequestGate  # noqa: E402

from tests.test_direct_market_valuation_runtime import (  # noqa: E402
    invoke_copied_installed as invoke_installed_valuation,
)

IDENTITY_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "identity_sources"
MARKET_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "market_series"
REPORT_FIXTURES = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "research_content" / "reports"
)


def rehydrate_result(payload: dict[str, object]) -> UnifiedResearchResult:
    return UnifiedResearchResult(
        status=payload["status"],  # type: ignore[arg-type]
        research_anchor=payload["research_anchor"],  # type: ignore[arg-type]
        plan=payload["plan"],  # type: ignore[arg-type]
        capability_registry=payload["capability_registry"],  # type: ignore[arg-type]
        coverage=payload["coverage"],  # type: ignore[arg-type]
        observations=tuple(payload["observations"]),  # type: ignore[arg-type]
        claims=tuple(payload["claims"]),  # type: ignore[arg-type]
        calculations=tuple(payload["calculations"]),  # type: ignore[arg-type]
        conflicts=tuple(payload["conflicts"]),  # type: ignore[arg-type]
        limitations=tuple(payload["limitations"]),  # type: ignore[arg-type]
        source_errors=tuple(payload["source_errors"]),  # type: ignore[arg-type]
        task_views=payload["task_views"],  # type: ignore[arg-type]
        briefs=tuple(payload["briefs"]),  # type: ignore[arg-type]
    )


def rehash_evidence_plan(payload: dict[str, object]) -> None:
    plan = payload["plan"]
    coverage = payload["coverage"]
    assert isinstance(plan, dict)
    assert isinstance(coverage, dict)
    dispositions = [
        {
            "capability_id": capability_id,
            "operation_id": operation_id,
            "fields": operation["field_dispositions"],
        }
        for capability_id, capability in coverage.items()
        if isinstance(capability, dict)
        for operations in (capability["operations"],)
        if isinstance(operations, dict)
        for operation_id, operation in operations.items()
        if isinstance(operation, dict)
    ]
    identity = {
        "version": plan["version"],
        "task_type": plan["task_type"],
        "operation_obligations": plan["operation_obligations"],
        "research_dependencies": plan["research_dependencies"],
        "field_dispositions": dispositions,
        "evidence_storage": plan["evidence_storage"],
    }
    plan["id"] = stable_result_id("plan", identity)


def rehash_operation_audit_ids(payload: dict[str, object]) -> None:
    plan = payload["plan"]
    coverage = payload["coverage"]
    assert isinstance(plan, dict)
    assert isinstance(coverage, dict)
    plan_audit = plan["evidence_storage"]
    assert isinstance(plan_audit, dict)
    request_scope = plan_audit["request_scope"]
    assert isinstance(request_scope, dict)
    for capability_id, capability in coverage.items():
        assert isinstance(capability, dict)
        operations = capability["operations"]
        assert isinstance(operations, dict)
        for operation_id, operation in operations.items():
            assert isinstance(operation, dict)
            audit = operation.get("evidence_storage")
            if not isinstance(audit, dict):
                continue
            source_version = audit["source_version"]
            subject_identity = audit["subject_identity"]
            assert isinstance(source_version, str)
            assert isinstance(subject_identity, str)
            audit["record_id"] = _record_id(
                request_scope,
                EvidenceReusePolicy(
                    capability_id=capability_id,
                    operation_id=operation_id,
                    source_version=source_version,
                    source_role="full_rehash_test",
                    experimental=False,
                    field_dispositions=(),
                    subject_identity=subject_identity,
                    evidence_scope="completed_history",
                    max_age=timedelta(seconds=1),
                ),
            )


def replace_result_ids(value: object, replacements: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(nested, str) and nested in replacements:
                value[key] = replacements[nested]
            else:
                replace_result_ids(nested, replacements)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            if isinstance(nested, str) and nested in replacements:
                value[index] = replacements[nested]
            else:
                replace_result_ids(nested, replacements)


def rehash_all_result_records(payload: dict[str, object]) -> None:
    for collection_name, prefix in (
        ("observations", "observation"),
        ("source_errors", "source-error"),
        ("claims", "claim"),
        ("calculations", "calculation"),
        ("conflicts", "conflict"),
        ("limitations", "limitation"),
    ):
        collection = payload[collection_name]
        assert isinstance(collection, list)
        replacements: dict[str, str] = {}
        for record in collection:
            assert isinstance(record, dict)
            old_id = record["id"]
            assert isinstance(old_id, str)
            identity = {key: value for key, value in record.items() if key != "id"}
            new_id = stable_result_id(prefix, identity)
            record["id"] = new_id
            replacements[old_id] = new_id
        replace_result_ids(payload, replacements)
    briefs = payload["briefs"]
    assert isinstance(briefs, list)
    brief_replacements: dict[str, str] = {}
    for brief in briefs:
        assert isinstance(brief, dict)
        old_id = brief["id"]
        assert isinstance(old_id, str)
        new_id = stable_result_id(
            "brief", {key: value for key, value in brief.items() if key != "id"}
        )
        brief["id"] = new_id
        brief_replacements[old_id] = new_id
    replace_result_ids(payload, brief_replacements)
    rehash_operation_audit_ids(payload)
    rehash_evidence_plan(payload)


class CanonicalReportTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        del headers
        page = int(parse_qs(urlparse(url).query)["pageNo"][0])
        return HttpResponse(
            status=200,
            content_type="text/plain",
            body=(REPORT_FIXTURES / f"eastmoney_stock_page_{page}.json").read_bytes(),
            retrieved_at=datetime(
                2026, 8, 2, 18, 30, tzinfo=timezone(timedelta(hours=8))
            ),
        )


def completed_market_trend_request(
    store_path: Path | None = None,
    *,
    store_mode: str = "read_write",
) -> dict[str, object]:
    request: dict[str, object] = {
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
    }
    if store_path is not None:
        source_policy = request["source_policy"]
        assert isinstance(source_policy, dict)
        source_policy["evidence_store"] = {
            "mode": store_mode,
            "path": str(store_path),
        }
    return request


def invoke_installed_market_trend(
    request_payload: dict[str, object],
    *,
    forbidden_markers: tuple[str, ...] = (),
    route_overrides: dict[str, bytes] | None = None,
    force_empty_official: bool = False,
    force_replace_failure: bool = False,
    force_unavailable_store: bool = False,
    replace_parent_swap: tuple[Path, Path] | None = None,
    acquire_parent_swap: tuple[Path, Path] | None = None,
    acquire_target_replacement: Path | None = None,
    source_retrieved_at: str = "2026-08-02T10:30:00+08:00",
    fixed_research_now: str | None = None,
    forbid_content_collect: bool = False,
    source_delay_seconds: float = 0,
    source_started_path: Path | None = None,
    append_intraday_official: bool = False,
    isolated_home: Path | None = None,
    content_license: str | None = "source_terms",
    dynamic_content_license: bool = False,
    content_batch_limitations: tuple[str, ...] = (),
    content_batch_degradations: tuple[tuple[str, str], ...] = (),
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        installed = temporary_root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = temporary_root / "request.json"
        request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False), encoding="utf-8"
        )
        route_literals = {
            "query.sse.com.cn": (IDENTITY_FIXTURES / "sse_empty.json").read_bytes(),
            "www.szse.cn/api/report/ShowReport": (
                IDENTITY_FIXTURES / "szse_300058.json"
            ).read_bytes(),
            "www.cninfo.com.cn": (
                IDENTITY_FIXTURES / "cninfo_current_orgs.json"
            ).read_bytes(),
            "getHistoryData": (MARKET_FIXTURES / "szse_300058_daily.json").read_bytes(),
            "push2his.eastmoney.com": (
                MARKET_FIXTURES / "eastmoney_sz300058_forward_adjusted.json"
            ).read_bytes(),
            "web.ifzq.gtimg.cn": (
                MARKET_FIXTURES / "tencent_sz300058_daily.json"
            ).read_bytes(),
            "fqkline": (
                MARKET_FIXTURES / "tencent_sz300058_forward_adjusted.json"
            ).read_bytes(),
        }
        route_literals.update(route_overrides or {})
        sitecustomize = temporary_root / "sitecustomize.py"
        sitecustomize.write_text(
            "\n".join(
                (
                    "import sys",
                    "import time",
                    "from datetime import date, datetime, timedelta, timezone",
                    f"sys.path.insert(0, {str(installed / 'scripts')!r})",
                    "from a_share_research.identity_sources import HttpResponse, UrlLibTransport",
                    "from a_share_research.close_sources import DailyBarObservation, SzseDailyLineOperation",
                    "from a_share_research.content_contract import ContentEvidencePersistenceDeclaration, ContentObservation, EvidencePersistableContentSourceOperation, SourceBatch, SourceFailure",
                    "import a_share_research.evidence_store as evidence_store_module",
                    "import a_share_research.research_runtime as runtime_module",
                    "from pathlib import Path as FixturePath",
                    f"routes = {route_literals!r}",
                    f"forbidden_markers = {forbidden_markers!r}",
                    f"force_empty_official = {force_empty_official!r}",
                    f"force_replace_failure = {force_replace_failure!r}",
                    f"force_unavailable_store = {force_unavailable_store!r}",
                    f"replace_parent_swap = {None if replace_parent_swap is None else tuple(map(str, replace_parent_swap))!r}",
                    f"acquire_parent_swap = {None if acquire_parent_swap is None else tuple(map(str, acquire_parent_swap))!r}",
                    f"acquire_target_replacement = {None if acquire_target_replacement is None else str(acquire_target_replacement)!r}",
                    f"source_retrieved_at = {source_retrieved_at!r}",
                    f"fixed_research_now = {fixed_research_now!r}",
                    f"forbid_content_collect = {forbid_content_collect!r}",
                    f"source_delay_seconds = {source_delay_seconds!r}",
                    f"source_started_path = {None if source_started_path is None else str(source_started_path)!r}",
                    f"append_intraday_official = {append_intraday_official!r}",
                    f"content_license = {content_license!r}",
                    f"dynamic_content_license = {dynamic_content_license!r}",
                    f"content_batch_limitations = {content_batch_limitations!r}",
                    f"content_batch_degradations = {content_batch_degradations!r}",
                    "class ExplicitContentPersistence(EvidencePersistableContentSourceOperation):",
                    "    @property",
                    "    def evidence_persistence(self): return ContentEvidencePersistenceDeclaration(content_license)",
                    "class DynamicContentPersistence:",
                    "    def __getattr__(self, name):",
                    "        if name in {'evidence_persistence', 'evidence_persistence_license'}: return ContentEvidencePersistenceDeclaration(content_license)",
                    "        raise AttributeError(name)",
                    "content_operation_base = DynamicContentPersistence if dynamic_content_license else ExplicitContentPersistence if content_license is not None else object",
                    "def fixture_open(self, request, timeout_seconds):",
                    "    if any(marker in request.full_url for marker in forbidden_markers): raise AssertionError('persisted operation unexpectedly called its source')",
                    "    if source_delay_seconds and 'getHistoryData' in request.full_url:",
                    "        if source_started_path: FixturePath(source_started_path).write_text('started')",
                    "        time.sleep(source_delay_seconds)",
                    "    body = next((value for marker, value in reversed(tuple(routes.items())) if marker in request.full_url), None)",
                    "    if body is None: raise AssertionError('unexpected source request')",
                    "    content_type = 'text/html' if 'ifzq.gtimg.cn' in request.full_url else 'application/json'",
                    "    retrieved_at = datetime.fromisoformat(source_retrieved_at) if 'getHistoryData' in request.full_url else datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8)))",
                    "    return HttpResponse(status=200, content_type=content_type, body=body, retrieved_at=retrieved_at)",
                    "UrlLibTransport._open = fixture_open",
                    "original_szse_observe = SzseDailyLineOperation.observe",
                    "def szse_observe_with_intraday(self, security, transport):",
                    "    rows = list(original_szse_observe(self, security, transport))",
                    "    if acquire_parent_swap:",
                    "        parent, external = map(FixturePath, acquire_parent_swap)",
                    "        detached = parent.with_name(parent.name + '-detached')",
                    "        parent.rename(detached)",
                    "        external.rename(parent)",
                    "    if acquire_target_replacement:",
                    "        target = FixturePath(acquire_target_replacement)",
                    "        concurrent = target.with_suffix('.concurrent')",
                    "        if target.exists(): target.rename(concurrent)",
                    "        target.write_text('concurrent sentinel', encoding='utf-8')",
                    "    if append_intraday_official:",
                    "        rows.append(DailyBarObservation(source_operation=self.operation_id, source_uri='https://fixture.test/intraday', security=security, trading_date=date(2026, 8, 2), open_value='10.00', high_value='10.20', low_value='9.90', close_value='10.10', volume_shares='1000', price_type='intraday_last', trading_status='traded', evidence_time=datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))), available_at=datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))), retrieved_at=datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))), availability_status='intraday_observation'))",
                    "    return rows",
                    "SzseDailyLineOperation.observe = szse_observe_with_intraday",
                    "if force_empty_official: SzseDailyLineOperation.observe = lambda self, security, transport: []",
                    "if force_unavailable_store: evidence_store_module._USE_ANCHORED_DIR_FD = False",
                    "if force_replace_failure: evidence_store_module.os.replace = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('injected atomic replace failure'))",
                    "if replace_parent_swap:",
                    "    original_replace = evidence_store_module.os.replace",
                    "    def swapped_replace(*args, **kwargs):",
                    "        parent, external = map(FixturePath, replace_parent_swap)",
                    "        detached = parent.with_name(parent.name + '-detached')",
                    "        parent.rename(detached)",
                    "        parent.symlink_to(external, target_is_directory=True)",
                    "        return original_replace(*args, **kwargs)",
                    "    evidence_store_module.os.replace = swapped_replace",
                    "class FixedContentOperation(content_operation_base):",
                    "    operation_id = 'fixture_content@1'",
                    "    supported_material_types = frozenset({'announcement', 'research_report', 'stock_news', 'market_flash', 'investor_qa', 'issuer_profile', 'consensus_material'})",
                    "    raw_fields = ('report_id', 'title', 'published_at', 'summary', 'locator')",
                    "    def collect(self, query):",
                    "        if forbid_content_collect: raise AssertionError('persisted content operation unexpectedly called its source')",
                    "        roles = {'announcement': 'authoritative_disclosure', 'research_report': 'attributed_opinion', 'stock_news': 'attributed_opinion', 'market_flash': 'market_signal', 'investor_qa': 'attributed_opinion', 'issuer_profile': 'market_observation', 'consensus_material': 'attributed_opinion'}",
                    "        observations = tuple(ContentObservation(material_type=kind, source_operation=self.operation_id, source_role=roles[kind], source_document_namespace=f'fixture-{kind}', source_document_id=f'{kind}-1', title=f'{kind} title', published_at='2026-07-31T09:00:00+08:00', retrieved_at=datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8))), locator_uri=f'https://fixture.test/{kind}-1', subject=query.subject, author='Fixture Research', summary=f'Auditable {kind} summary', document_locator=f'https://fixture.test/{kind}-1.pdf', attributes=({'license': content_license} if content_license is not None else {}), limitations=(), content=None) for kind in query.material_types)",
                    "        return SourceBatch(operation_id=self.operation_id, observations=observations, degradations=tuple(SourceFailure(self.operation_id, code, message) for code, message in content_batch_degradations), limitations=tuple(content_batch_limitations), complete=True)",
                    "runtime_module.build_default_content_operations = lambda *args, **kwargs: (FixedContentOperation(),)",
                    "if fixed_research_now:",
                    "    original_runtime_init = runtime_module.ResearchRuntime.__init__",
                    "    def fixed_runtime_init(self, *args, **kwargs):",
                    "        if kwargs.get('research_now') is None: kwargs['research_now'] = datetime.fromisoformat(fixed_research_now)",
                    "        original_runtime_init(self, *args, **kwargs)",
                    "    runtime_module.ResearchRuntime.__init__ = fixed_runtime_init",
                )
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = str(temporary_root)
        if isolated_home is not None:
            environment["HOME"] = str(isolated_home)
            environment["USERPROFILE"] = str(isolated_home)
        return subprocess.run(
            [
                sys.executable,
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ],
            cwd=temporary_root,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )


class EvidenceStorePublicSeamTests(unittest.TestCase):
    def test_parser_rejects_explicit_null_and_every_noncanonical_store_shape(
        self,
    ) -> None:
        base = completed_market_trend_request()
        cases: tuple[tuple[object, str], ...] = (
            (None, "invalid shape"),
            ([], "invalid shape"),
            ({}, "invalid shape"),
            ({"mode": "read_write"}, "invalid shape"),
            (
                {"mode": "read_write", "path": "/tmp/store", "extra": True},
                "invalid shape",
            ),
            ({"mode": None, "path": "/tmp/store"}, "fields must be strings"),
            ({"mode": "read_write", "path": None}, "fields must be strings"),
            ({"mode": "read_write", "path": True}, "fields must be strings"),
            ({"mode": "read_write", "path": ""}, "non-empty path"),
            ({"mode": "read_write", "path": "   "}, "non-empty path"),
            ({"mode": "read_write", "path": "relative.json"}, "must be absolute"),
            ({"mode": "read_only", "path": "/tmp/store"}, "mode is unsupported"),
        )
        for configuration, message in cases:
            with self.subTest(configuration=configuration):
                request = copy.deepcopy(base)
                source_policy = request["source_policy"]
                assert isinstance(source_policy, dict)
                source_policy["evidence_store"] = configuration
                with self.assertRaisesRegex(ValueError, message):
                    parse_evidence_store_request(request)

    def test_runtime_has_no_task_allowlist_and_unsupported_task_never_probes_path(
        self,
    ) -> None:
        runtime_path = (
            SKILL_ROOT / "scripts" / "a_share_research" / "research_runtime.py"
        )
        tree = parse(runtime_path.read_text(encoding="utf-8"))
        task_support_sets = [
            node
            for node in walk(tree)
            if isinstance(node, Compare)
            and isinstance(node.left, Name)
            and node.left.id == "task_type"
            and any(isinstance(operator, NotIn) for operator in node.ops)
            and any(isinstance(comparator, Set) for comparator in node.comparators)
        ]
        self.assertEqual(
            task_support_sets,
            [],
            "evidence-store support must not be duplicated in a task allowlist",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_parent = Path(temporary_directory).resolve() / "must-not-exist"
            request = {
                "schema_version": "1.0",
                "task_type": "etf_market",
                "subjects": [{"clue": "510050"}],
                "as_of": "2026-08-02",
                "window": None,
                "parameters": {},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(missing_parent / "store.json"),
                    },
                },
            }

            completed = invoke_installed_market_trend(
                request,
                forbidden_markers=("",),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "etf_market does not permit persisted research evidence",
                completed.stderr,
            )
            self.assertFalse(missing_parent.exists())

    def test_serializer_rejects_rehashed_evidence_audit_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            original = json.loads(completed.stdout)

        forged_scope = copy.deepcopy(original)
        plan = forged_scope["plan"]
        assert isinstance(plan, dict)
        plan_audit = plan["evidence_storage"]
        assert isinstance(plan_audit, dict)
        request_scope = plan_audit["request_scope"]
        assert isinstance(request_scope, dict)
        request_scope["window"] = {"trading_days": 9}
        rehash_evidence_plan(forged_scope)
        with self.assertRaisesRegex(ValueError, "record ID"):
            rehydrate_result(forged_scope).to_dict()

        forged_version = copy.deepcopy(original)
        version_operation = forged_version["coverage"]["market_trend_series"][
            "operations"
        ]["szse_daily_line@1"]
        version_operation["evidence_storage"]["source_version"] = (
            "market-series-contract/9.9"
        )
        rehash_evidence_plan(forged_version)
        with self.assertRaisesRegex(ValueError, "record ID"):
            rehydrate_result(forged_version).to_dict()

        forged_state = copy.deepcopy(original)
        state_operation = forged_state["coverage"]["market_trend_series"]["operations"][
            "szse_daily_line@1"
        ]
        state_operation["evidence_storage"].update(
            {
                "lookup_state": "hit",
                "write_state": "written",
                "stored_source_version": "market-series-contract/1.0",
            }
        )
        rehash_evidence_plan(forged_state)
        with self.assertRaisesRegex(ValueError, "audit facts"):
            rehydrate_result(forged_state).to_dict()

    def test_serializer_rejects_fully_rehashed_market_scope_authority_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            first = invoke_installed_market_trend(request)
            second = invoke_installed_market_trend(
                request,
                forbidden_markers=("getHistoryData",),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            original = json.loads(second.stdout)

        forged_scope = copy.deepcopy(original)
        plan = forged_scope["plan"]
        assert isinstance(plan, dict)
        plan_audit = plan["evidence_storage"]
        assert isinstance(plan_audit, dict)
        request_scope = plan_audit["request_scope"]
        assert isinstance(request_scope, dict)
        request_scope["window"] = {"trading_days": 9}
        rehash_operation_audit_ids(forged_scope)
        rehash_evidence_plan(forged_scope)
        with self.assertRaisesRegex(ValueError, "market evidence storage authority"):
            rehydrate_result(forged_scope).to_dict()

    def test_serializer_rejects_fully_rehashed_market_version_authority_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            first = invoke_installed_market_trend(request)
            second = invoke_installed_market_trend(
                request,
                forbidden_markers=("getHistoryData",),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            original = json.loads(second.stdout)

        forged_version = copy.deepcopy(original)
        version_operation = forged_version["coverage"]["market_trend_series"][
            "operations"
        ]["szse_daily_line@1"]
        version_audit = version_operation["evidence_storage"]
        version_audit["source_version"] = "market-series-contract/9.9"
        version_audit["stored_source_version"] = "market-series-contract/9.9"
        rehash_operation_audit_ids(forged_version)
        rehash_evidence_plan(forged_version)
        with self.assertRaisesRegex(ValueError, "market evidence storage authority"):
            rehydrate_result(forged_version).to_dict()

    def test_serializer_rejects_fully_rehashed_content_version_authority_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            first = invoke_installed_market_trend(request)
            second = invoke_installed_market_trend(
                request,
                forbid_content_collect=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            forged = json.loads(second.stdout)

        operation = forged["coverage"]["research_content_materials"]["operations"][
            "fixture_content@1"
        ]
        audit = operation["evidence_storage"]
        audit["source_version"] = "research-content-contract/9.9"
        audit["stored_source_version"] = "research-content-contract/9.9"
        rehash_operation_audit_ids(forged)
        rehash_evidence_plan(forged)
        with self.assertRaisesRegex(ValueError, "content evidence storage authority"):
            rehydrate_result(forged).to_dict()

    def test_serializer_rejects_fully_rehashed_canonical_content_license_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-05-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["research_report"],
                    "query": ["AI服务器"],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            operation = EastmoneyReportOperation(
                CanonicalReportTransport(),
                request_gate=SerialRequestGate(
                    minimum_interval_seconds=0,
                    jitter_bounds=(0, 0),
                    rate_limit_backoffs=(),
                ),
            )
            original = (
                ResearchRuntime(
                    content_operations=(operation,),
                    research_now=datetime(
                        2026,
                        8,
                        2,
                        19,
                        30,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                )
                .research(request)
                .to_dict()
            )

            self.assertFalse(store_path.exists())
            original_operation = original["coverage"]["research_content_materials"][
                "operations"
            ]["eastmoney_reports@1"]
            self.assertEqual(
                original_operation["evidence_storage"]["lookup_state"],
                "not_permitted",
            )
            owned_observations = [
                item
                for item in original["observations"]
                if item["operation_id"] == "eastmoney_reports@1"
            ]
            self.assertTrue(owned_observations)
            self.assertEqual(
                {
                    item["payload"]["license_boundary"]["status"]
                    for item in owned_observations
                },
                {"not_established"},
            )

        forged = copy.deepcopy(original)
        for observation in forged["observations"]:
            if observation["operation_id"] == "eastmoney_reports@1":
                observation["payload"]["license_boundary"]["status"] = "source_terms"
        audit = forged["coverage"]["research_content_materials"]["operations"][
            "eastmoney_reports@1"
        ]["evidence_storage"]
        audit.update(
            {
                "stored_source_version": audit["source_version"],
                "lookup_state": "hit",
                "write_state": "not_written",
                "freshness": "fresh",
                "fitness_for_use": "fit",
                "reason": None,
            }
        )
        rehash_all_result_records(forged)

        with self.assertRaisesRegex(ValueError, "content evidence storage authority"):
            rehydrate_result(forged).to_dict()

    def test_serializer_rejects_fully_rehashed_content_reuse_fact_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-05-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["research_report"],
                    "query": ["AI服务器"],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            operation = EastmoneyReportOperation(
                CanonicalReportTransport(),
                request_gate=SerialRequestGate(
                    minimum_interval_seconds=0,
                    jitter_bounds=(0, 0),
                    rate_limit_backoffs=(),
                ),
            )
            original = (
                ResearchRuntime(
                    content_operations=(operation,),
                    research_now=datetime(
                        2026,
                        8,
                        2,
                        19,
                        30,
                        tzinfo=timezone(timedelta(hours=8)),
                    ),
                )
                .research(request)
                .to_dict()
            )

        original_audit = original["coverage"]["research_content_materials"][
            "operations"
        ]["eastmoney_reports@1"]["evidence_storage"]
        self.assertEqual(
            original_audit["reuse_facts"],
            {
                "limitation_codes": [
                    "theme_report_universe_incomplete",
                    "title_keyword_filter_not_semantic_search",
                ],
                "degradation_codes": [],
            },
        )
        for case in ("missing", "wrong_category"):
            with self.subTest(case=case):
                forged = copy.deepcopy(original)
                audit = forged["coverage"]["research_content_materials"]["operations"][
                    "eastmoney_reports@1"
                ]["evidence_storage"]
                if case == "missing":
                    audit.pop("reuse_facts")
                else:
                    audit["reuse_facts"] = {
                        "limitation_codes": ["theme_report_universe_incomplete"],
                        "degradation_codes": [
                            "title_keyword_filter_not_semantic_search"
                        ],
                    }
                rehash_all_result_records(forged)
                with self.assertRaisesRegex(
                    ValueError, "content evidence storage authority"
                ):
                    rehydrate_result(forged).to_dict()

    def test_serializer_rejects_fully_rehashed_financial_version_authority_forgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
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
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            invoke_installed_valuation(request, valuation_fixtures=True)
            forged = invoke_installed_valuation(
                request,
                valuation_fixtures=True,
                fail_url_contains="CompanyFinanceService.getFinanceReport2022",
            )

        operation = forged["coverage"]["security_valuation_metrics"]["operations"][
            "sina_financial_statements@1"
        ]
        audit = operation["evidence_storage"]
        audit["source_version"] = "valuation-financial-statements/9.9"
        audit["stored_source_version"] = "valuation-financial-statements/9.9"
        rehash_operation_audit_ids(forged)
        rehash_evidence_plan(forged)
        with self.assertRaisesRegex(ValueError, "valuation evidence storage authority"):
            rehydrate_result(forged).to_dict()

    def test_serializer_requires_audit_for_each_attempted_persistence_owner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            market_completed = invoke_installed_market_trend(
                completed_market_trend_request(root / "market.json")
            )
            self.assertEqual(market_completed.returncode, 0, market_completed.stderr)
            market = json.loads(market_completed.stdout)

            content_request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(root / "content.json"),
                    },
                },
            }
            content_completed = invoke_installed_market_trend(content_request)
            self.assertEqual(content_completed.returncode, 0, content_completed.stderr)
            content = json.loads(content_completed.stdout)

            valuation_request = {
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
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(root / "valuation.json"),
                    },
                },
            }
            valuation = invoke_installed_valuation(
                valuation_request, valuation_fixtures=True
            )

        cases = (
            (
                market,
                "market_trend_series",
                "szse_daily_line@1",
                "market evidence storage authority",
            ),
            (
                content,
                "research_content_materials",
                "fixture_content@1",
                "content evidence storage authority",
            ),
            (
                valuation,
                "security_valuation_metrics",
                "sina_financial_statements@1",
                "valuation evidence storage authority",
            ),
        )
        for original, capability_id, operation_id, message in cases:
            with self.subTest(operation_id=operation_id):
                forged = copy.deepcopy(original)
                operation = forged["coverage"][capability_id]["operations"][
                    operation_id
                ]
                self.assertIn("evidence_storage", operation)
                operation.pop("evidence_storage")
                rehash_all_result_records(forged)
                with self.assertRaisesRegex(ValueError, message):
                    rehydrate_result(forged).to_dict()

    def test_serializer_rejects_fully_rehashed_unsupported_task_plan_audit(
        self,
    ) -> None:
        request = {
            "schema_version": "1.0",
            "task_type": "etf_market",
            "subjects": [{"clue": "510050"}],
            "as_of": "2026-08-02",
            "window": None,
            "parameters": {},
            "source_policy": {
                "allow_experimental": True,
                "allow_credentials": False,
                "allow_fallback": True,
            },
        }
        original = invoke_installed_valuation(request, market_fixtures=True)
        forged = copy.deepcopy(original)
        request_scope = copy.deepcopy(request)
        request_scope.pop("schema_version")
        forged["plan"]["evidence_storage"] = {
            "schema_version": "1.0",
            "policy_version": "core-v0.3.0",
            "mode": "read_write",
            "request_scope": request_scope,
        }
        rehash_evidence_plan(forged)

        with self.assertRaisesRegex(ValueError, "ETF evidence storage authority"):
            rehydrate_result(forged).to_dict()

    def test_fresh_clock_tolerance_never_makes_future_stored_evidence_fit(
        self,
    ) -> None:
        now = datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=8)))
        operation_id = "clock_boundary@1"
        policy = EvidenceReusePolicy(
            capability_id="clock_boundary",
            operation_id=operation_id,
            source_version="clock-boundary/1.0",
            source_role="market_observation",
            experimental=False,
            field_dispositions=(),
            subject_identity="SZSE:300058",
            evidence_scope="completed_history",
            max_age=timedelta(days=1),
        )

        def outcome(acquired_at: datetime) -> OperationOutcome:
            return OperationOutcome(
                capability_id=policy.capability_id,
                operation_id=operation_id,
                execution=OperationExecution("succeeded", query_complete=True),
                observations=(
                    {
                        "id": "clock-observation",
                        "source_operation": operation_id,
                        "retrieved_at": acquired_at.isoformat(),
                    },
                ),
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            store = EvidenceStoreRequest(
                path=root / "within-tolerance.json",
                mode="read_write",
                request_scope={"task_type": "clock_boundary"},
            )
            boundary = now + _MAX_FRESH_ACQUISITION_CLOCK_SKEW

            written = resolve_operation_evidence(
                store,
                policy,
                lambda: outcome(boundary),
                now=now,
            )

            self.assertEqual(written.audit["write_state"], "written")
            with self.assertRaisesRegex(
                ValueError, "stored acquisition time cannot be in the future"
            ):
                resolve_operation_evidence(
                    store,
                    policy,
                    lambda: (_ for _ in ()).throw(
                        AssertionError("future stored evidence must not hit")
                    ),
                    now=now,
                )

            beyond_store = EvidenceStoreRequest(
                path=root / "beyond-tolerance.json",
                mode="read_write",
                request_scope={"task_type": "clock_boundary"},
            )
            with self.assertRaisesRegex(
                ValueError, "acquired operation evidence time cannot be in the future"
            ):
                resolve_operation_evidence(
                    beyond_store,
                    policy,
                    lambda: outcome(boundary + timedelta(microseconds=1)),
                    now=now,
                )
            self.assertFalse(beyond_store.path.exists())

    def test_windows_import_keeps_only_stateless_and_not_permitted_paths(self) -> None:
        script = "\n".join(
            (
                "import importlib.abc",
                "import subprocess",
                "import sys",
                "import tempfile",
                "sys.modules.pop('fcntl', None)",
                "class RejectFcntl(importlib.abc.MetaPathFinder):",
                "    def find_spec(self, fullname, path, target=None):",
                "        if fullname == 'fcntl': raise ImportError('fcntl unavailable')",
                "        return None",
                "sys.meta_path.insert(0, RejectFcntl())",
                "sys.platform = 'win32'",
                f"sys.path.insert(0, {str(SKILL_ROOT / 'scripts')!r})",
                "from a_share_research import evidence_store",
                "from a_share_research.research_result import OperationExecution, OperationOutcome",
                "assert evidence_store._USE_ANCHORED_DIR_FD is False",
                "request = {'schema_version': '1.0', 'task_type': 'market_trend', 'subjects': [], 'as_of': '2026-08-02', 'window': {'trading_days': 10}, 'parameters': {}, 'source_policy': {}}",
                "sanitized, absent = evidence_store.parse_evidence_store_request(request)",
                "assert sanitized == request and absent is None",
                "calls = []",
                "outcome = OperationOutcome(capability_id='market', operation_id='owner@1', execution=OperationExecution('succeeded', query_complete=True), observations=(), source_errors=(), field_dispositions=(), diagnostics={})",
                "nonpermitted = evidence_store.EvidenceReusePolicy(capability_id='market', operation_id='owner@1', source_version='1', source_role='market_observation', experimental=False, field_dispositions=(), subject_identity='SZSE:300058', evidence_scope='completed_history', max_age=evidence_store.timedelta(days=1), persistence_permitted=False)",
                "with tempfile.TemporaryDirectory() as directory:",
                "    parent = evidence_store.Path(directory) / 'missing-parent'",
                "    store_path = parent / 'store.json'",
                "    store = evidence_store.EvidenceStoreRequest(path=store_path, mode='read_write', request_scope={'task_type': 'market_trend'})",
                "    resolved = evidence_store.resolve_operation_evidence(store, nonpermitted, lambda: calls.append('acquire') or outcome, now=evidence_store.datetime.fromisoformat('2026-08-02T18:30:00+08:00'))",
                "    assert calls == ['acquire']",
                "    assert resolved.audit['lookup_state'] == 'not_permitted'",
                "    assert not parent.exists()",
                "    permitted = evidence_store.replace(nonpermitted, persistence_permitted=True)",
                "    for mode in ('read_write', 'delete'):",
                "        explicit = evidence_store.EvidenceStoreRequest(path=store_path, mode=mode, request_scope={'task_type': 'market_trend'})",
                "        try: evidence_store.resolve_operation_evidence(explicit, permitted, lambda: (_ for _ in ()).throw(AssertionError('source must not run')), now=evidence_store.datetime.fromisoformat('2026-08-02T18:30:00+08:00'))",
                "        except ValueError as error: assert str(error) == 'research evidence_store persistence is unavailable on this platform'",
                "        else: raise AssertionError('explicit persistence must fail closed')",
                "        assert not parent.exists()",
            )
        )

        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_installed_default_request_keeps_the_original_stateless_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_home = Path(temporary_directory).resolve() / "unused-home"
            completed = invoke_installed_market_trend(
                completed_market_trend_request(), isolated_home=missing_home
            )

            self.assertFalse(missing_home.exists())

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
        self.assertEqual(
            set(result["plan"]),
            {
                "id",
                "version",
                "task_type",
                "capabilities",
                "operation_obligations",
                "research_dependencies",
            },
        )
        for capability in result["coverage"].values():
            for operation in capability["operations"].values():
                self.assertEqual(
                    set(operation),
                    {
                        "capability_id",
                        "operation_id",
                        "execution_state",
                        "state",
                        "query_complete",
                        "observation_ids",
                        "source_error_ids",
                        "field_dispositions",
                        "diagnostics",
                    },
                )

    def test_installed_completed_history_explicit_store_writes_auditable_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            self.assertTrue(store_path.is_file())
            self.assertEqual(
                result["plan"]["evidence_storage"],
                {
                    "schema_version": "1.0",
                    "policy_version": "core-v0.3.0",
                    "mode": "read_write",
                    "request_scope": {
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
                },
            )
            operations = result["coverage"]["market_trend_series"]["operations"]
            self.assertIn("szse_daily_line@1", operations)
            audit = operations["szse_daily_line@1"]["evidence_storage"]
            self.assertEqual(
                audit,
                {
                    "record_id": audit["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": None,
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "miss",
                    "write_state": "written",
                    "original_acquired_at": "2026-08-02T10:30:00+08:00",
                    "result_acquired_at": "2026-08-02T10:30:00+08:00",
                    "freshness": "fresh",
                    "fitness_for_use": "fit",
                    "reason": None,
                },
            )
            self.assertRegex(audit["record_id"], r"^[0-9a-f]{24}$")

    def test_installed_completed_history_hit_reuses_owned_normalized_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            first = invoke_installed_market_trend(request)
            second = invoke_installed_market_trend(
                request,
                forbidden_markers=("getHistoryData",),
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stderr, "")
            first_result = json.loads(first.stdout)
            second_result = json.loads(second.stdout)
            first_operation = first_result["coverage"]["market_trend_series"][
                "operations"
            ]["szse_daily_line@1"]
            second_operation = second_result["coverage"]["market_trend_series"][
                "operations"
            ]["szse_daily_line@1"]
            self.assertEqual(
                second_operation["evidence_storage"],
                {
                    **first_operation["evidence_storage"],
                    "stored_source_version": "market-series-contract/1.0",
                    "lookup_state": "hit",
                    "write_state": "not_written",
                },
            )
            self.assertEqual(
                first_operation["observation_ids"],
                second_operation["observation_ids"],
            )
            first_claim = copy.deepcopy(first_result["claims"][0])
            second_claim = copy.deepcopy(second_result["claims"][0])
            first_claim.pop("id")
            second_claim.pop("id")
            first_claim["payload"]["research"].pop("retrieved_at")
            second_claim["payload"]["research"].pop("retrieved_at")
            self.assertEqual(first_claim, second_claim)
            self.assertEqual(
                first_result["calculations"], second_result["calculations"]
            )

    def test_installed_expired_record_is_disclosed_and_replaced_from_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            first = invoke_installed_market_trend(request)

            self.assertEqual(first.returncode, 0, first.stderr)
            store = json.loads(store_path.read_text(encoding="utf-8"))
            record = next(iter(store["records"].values()))
            record["original_acquired_at"] = "2026-01-01T10:30:00+08:00"
            for observation in record["observations"]:
                observation["retrieved_at"] = "2026-01-01T10:30:00+08:00"
            store_path.write_text(
                json.dumps(store, ensure_ascii=False), encoding="utf-8"
            )

            second = invoke_installed_market_trend(request)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stderr, "")
            result = json.loads(second.stdout)
            operation = result["coverage"]["market_trend_series"]["operations"][
                "szse_daily_line@1"
            ]
            audit = operation["evidence_storage"]
            self.assertEqual(
                audit,
                {
                    "record_id": audit["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "subject_identity": "SZSE:300058",
                    "stored_source_version": "market-series-contract/1.0",
                    "lookup_state": "expired",
                    "write_state": "replaced",
                    "original_acquired_at": "2026-01-01T10:30:00+08:00",
                    "result_acquired_at": "2026-08-02T10:30:00+08:00",
                    "freshness": "expired",
                    "fitness_for_use": "unfit",
                    "reason": "freshness_expired",
                },
            )
            self.assertEqual(
                {
                    observation["payload"]["retrieved_at"]
                    for observation in result["observations"]
                    if observation["operation_id"] == "szse_daily_line@1"
                },
                {"2026-08-02T10:30:00+08:00"},
            )
            refreshed = json.loads(store_path.read_text(encoding="utf-8"))
            refreshed_record = next(iter(refreshed["records"].values()))
            self.assertEqual(
                refreshed_record["original_acquired_at"],
                "2026-08-02T10:30:00+08:00",
            )

    def test_installed_expired_record_survives_failed_replacement_acquisition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            seeded = invoke_installed_market_trend(request)
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            store = json.loads(store_path.read_text(encoding="utf-8"))
            record = next(iter(store["records"].values()))
            record["original_acquired_at"] = "2026-01-01T10:30:00+08:00"
            for observation in record["observations"]:
                observation["retrieved_at"] = "2026-01-01T10:30:00+08:00"
            store_path.write_text(
                json.dumps(store, ensure_ascii=False), encoding="utf-8"
            )
            expired_bytes = store_path.read_bytes()

            completed = invoke_installed_market_trend(
                request,
                route_overrides={"getHistoryData": b"{"},
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            operations = result["coverage"]["market_trend_series"]["operations"]
            failed = operations["szse_daily_line@1"]
            self.assertEqual(failed["execution_state"], "failed")
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(
                failed["evidence_storage"],
                {
                    "record_id": failed["evidence_storage"]["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": "market-series-contract/1.0",
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "expired",
                    "write_state": "not_written",
                    "original_acquired_at": "2026-01-01T10:30:00+08:00",
                    "result_acquired_at": None,
                    "freshness": "expired",
                    "fitness_for_use": "unfit",
                    "reason": "replacement_outcome_not_reusable",
                },
            )
            self.assertEqual(
                operations["tencent_daily_line@1"]["execution_state"], "succeeded"
            )
            self.assertEqual(store_path.read_bytes(), expired_bytes)

    def test_installed_stale_fresh_acquisition_is_retained_but_not_persisted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"

            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                source_retrieved_at="2026-06-01T10:30:00+08:00",
                fixed_research_now="2026-08-02T18:30:00+08:00",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(store_path.exists())
            result = json.loads(completed.stdout)
            operation = result["coverage"]["market_trend_series"]["operations"][
                "szse_daily_line@1"
            ]
            self.assertEqual(operation["execution_state"], "succeeded")
            self.assertEqual(
                operation["evidence_storage"],
                {
                    "record_id": operation["evidence_storage"]["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": None,
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "miss",
                    "write_state": "not_written",
                    "original_acquired_at": "2026-06-01T10:30:00+08:00",
                    "result_acquired_at": "2026-06-01T10:30:00+08:00",
                    "freshness": "expired",
                    "fitness_for_use": "unfit",
                    "reason": "acquired_evidence_stale",
                },
            )

    def test_installed_stale_acquisition_never_replaces_prior_record(self) -> None:
        for replacement_state in ("expired", "version_mismatch"):
            with (
                self.subTest(replacement_state=replacement_state),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                store_path = (
                    Path(temporary_directory).resolve() / "research-evidence.json"
                )
                request = completed_market_trend_request(store_path)
                seeded = invoke_installed_market_trend(
                    request,
                    fixed_research_now="2026-08-02T18:30:00+08:00",
                )
                self.assertEqual(seeded.returncode, 0, seeded.stderr)
                store = json.loads(store_path.read_text(encoding="utf-8"))
                record = next(iter(store["records"].values()))
                stored_acquired_at = "2026-08-02T10:30:00+08:00"
                stored_version = "market-series-contract/1.0"
                if replacement_state == "expired":
                    stored_acquired_at = "2026-01-01T10:30:00+08:00"
                    record["original_acquired_at"] = stored_acquired_at
                    for observation in record["observations"]:
                        observation["retrieved_at"] = stored_acquired_at
                else:
                    stored_version = "market-series-contract/0.9"
                    record["source_version"] = stored_version
                    identity = {
                        "schema_version": record["schema_version"],
                        "policy_version": record["policy_version"],
                        "request_scope": record["request_scope"],
                        "capability_id": record["capability_id"],
                        "operation_id": record["operation_id"],
                        "source_version": stored_version,
                        "subject_identity": record["subject_identity"],
                    }
                    old_record_id = hashlib.sha256(
                        json.dumps(
                            identity,
                            ensure_ascii=True,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()[:24]
                    record["record_id"] = old_record_id
                    store["records"] = {old_record_id: record}
                store_path.write_text(
                    json.dumps(store, ensure_ascii=False), encoding="utf-8"
                )
                original = store_path.read_bytes()

                completed = invoke_installed_market_trend(
                    request,
                    source_retrieved_at="2026-06-01T10:30:00+08:00",
                    fixed_research_now="2026-08-02T18:30:00+08:00",
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                result = json.loads(completed.stdout)
                operation = result["coverage"]["market_trend_series"]["operations"][
                    "szse_daily_line@1"
                ]
                self.assertEqual(operation["execution_state"], "succeeded")
                self.assertEqual(
                    operation["evidence_storage"],
                    {
                        "record_id": operation["evidence_storage"]["record_id"],
                        "source_version": "market-series-contract/1.0",
                        "stored_source_version": stored_version,
                        "subject_identity": "SZSE:300058",
                        "lookup_state": replacement_state,
                        "write_state": "not_written",
                        "original_acquired_at": stored_acquired_at,
                        "result_acquired_at": "2026-06-01T10:30:00+08:00",
                        "freshness": (
                            "expired" if replacement_state == "expired" else "fresh"
                        ),
                        "fitness_for_use": "unfit",
                        "reason": "replacement_outcome_not_reusable",
                    },
                )
                self.assertEqual(store_path.read_bytes(), original)

    def test_installed_observed_empty_outcome_is_retained_but_not_persisted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                force_empty_official=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(store_path.exists())
            result = json.loads(completed.stdout)
            operation = result["coverage"]["market_trend_series"]["operations"][
                "szse_daily_line@1"
            ]
            self.assertEqual(operation["execution_state"], "succeeded")
            self.assertTrue(operation["query_complete"])
            self.assertEqual(operation["state"], "observed_empty")
            self.assertEqual(
                operation["evidence_storage"],
                {
                    "record_id": operation["evidence_storage"]["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": None,
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "miss",
                    "write_state": "not_written",
                    "original_acquired_at": None,
                    "result_acquired_at": None,
                    "freshness": "not_assessed",
                    "fitness_for_use": "unfit",
                    "reason": "outcome_not_reusable",
                },
            )
            sibling = result["coverage"]["market_trend_series"]["operations"][
                "tencent_daily_line@1"
            ]
            self.assertEqual(sibling["execution_state"], "succeeded")
            self.assertEqual(sibling["state"], "complete")

    def test_installed_failed_outcome_and_successful_sibling_are_retained_without_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                route_overrides={"getHistoryData": b"{"},
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(store_path.exists())
            result = json.loads(completed.stdout)
            operations = result["coverage"]["market_trend_series"]["operations"]
            failed = operations["szse_daily_line@1"]
            self.assertEqual(failed["execution_state"], "failed")
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["evidence_storage"]["write_state"], "not_written")
            self.assertEqual(
                failed["evidence_storage"]["reason"], "outcome_not_reusable"
            )
            self.assertEqual(
                operations["tencent_daily_line@1"]["execution_state"], "succeeded"
            )

    def test_installed_source_version_mismatch_is_disclosed_and_replaced(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            first = invoke_installed_market_trend(request)

            self.assertEqual(first.returncode, 0, first.stderr)
            store = json.loads(store_path.read_text(encoding="utf-8"))
            record = next(iter(store["records"].values()))
            old_source_version = "market-series-contract/0.9"
            record["source_version"] = old_source_version
            identity = {
                "schema_version": record["schema_version"],
                "policy_version": record["policy_version"],
                "request_scope": record["request_scope"],
                "capability_id": record["capability_id"],
                "operation_id": record["operation_id"],
                "source_version": old_source_version,
                "subject_identity": record["subject_identity"],
            }
            old_record_id = hashlib.sha256(
                json.dumps(
                    identity,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:24]
            record["record_id"] = old_record_id
            store["records"] = {old_record_id: record}
            store_path.write_text(
                json.dumps(store, ensure_ascii=False), encoding="utf-8"
            )

            second = invoke_installed_market_trend(request)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stderr, "")
            result = json.loads(second.stdout)
            operation = result["coverage"]["market_trend_series"]["operations"][
                "szse_daily_line@1"
            ]
            audit = operation["evidence_storage"]
            self.assertEqual(
                audit,
                {
                    "record_id": audit["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": old_source_version,
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "version_mismatch",
                    "write_state": "replaced",
                    "original_acquired_at": "2026-08-02T10:30:00+08:00",
                    "result_acquired_at": "2026-08-02T10:30:00+08:00",
                    "freshness": "fresh",
                    "fitness_for_use": "unfit",
                    "reason": "source_version_mismatch",
                },
            )
            rewritten = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(set(rewritten["records"]), {audit["record_id"]})
            self.assertNotIn(old_record_id, rewritten["records"])

    def test_installed_old_version_record_survives_failed_replacement_acquisition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            seeded = invoke_installed_market_trend(request)
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            store = json.loads(store_path.read_text(encoding="utf-8"))
            record = next(iter(store["records"].values()))
            old_source_version = "market-series-contract/0.9"
            record["source_version"] = old_source_version
            identity = {
                "schema_version": record["schema_version"],
                "policy_version": record["policy_version"],
                "request_scope": record["request_scope"],
                "capability_id": record["capability_id"],
                "operation_id": record["operation_id"],
                "source_version": old_source_version,
                "subject_identity": record["subject_identity"],
            }
            old_record_id = hashlib.sha256(
                json.dumps(
                    identity,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:24]
            record["record_id"] = old_record_id
            store["records"] = {old_record_id: record}
            store_path.write_text(
                json.dumps(store, ensure_ascii=False), encoding="utf-8"
            )
            old_version_bytes = store_path.read_bytes()

            completed = invoke_installed_market_trend(
                request,
                route_overrides={"getHistoryData": b"{"},
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            operations = result["coverage"]["market_trend_series"]["operations"]
            failed = operations["szse_daily_line@1"]
            self.assertEqual(failed["execution_state"], "failed")
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(
                failed["evidence_storage"],
                {
                    "record_id": failed["evidence_storage"]["record_id"],
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": old_source_version,
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "version_mismatch",
                    "write_state": "not_written",
                    "original_acquired_at": "2026-08-02T10:30:00+08:00",
                    "result_acquired_at": None,
                    "freshness": "fresh",
                    "fitness_for_use": "unfit",
                    "reason": "replacement_outcome_not_reusable",
                },
            )
            self.assertEqual(
                operations["tencent_daily_line@1"]["execution_state"], "succeeded"
            )
            self.assertEqual(store_path.read_bytes(), old_version_bytes)

    def test_installed_delete_removes_only_the_current_canonical_record(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            first = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            store = json.loads(store_path.read_text(encoding="utf-8"))
            canonical_record_id = next(iter(store["records"]))
            store["records"]["unrelated-record"] = {
                "owner": "another request",
                "value": "must remain untouched",
            }
            store_path.write_text(
                json.dumps(store, ensure_ascii=False), encoding="utf-8"
            )

            deleted = invoke_installed_market_trend(
                completed_market_trend_request(store_path, store_mode="delete")
            )

            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            self.assertEqual(deleted.stderr, "")
            result = json.loads(deleted.stdout)
            self.assertEqual(result["plan"]["evidence_storage"]["mode"], "delete")
            operation = result["coverage"]["market_trend_series"]["operations"][
                "szse_daily_line@1"
            ]
            audit = operation["evidence_storage"]
            self.assertEqual(
                audit,
                {
                    "record_id": canonical_record_id,
                    "source_version": "market-series-contract/1.0",
                    "stored_source_version": "market-series-contract/1.0",
                    "subject_identity": "SZSE:300058",
                    "lookup_state": "deleted",
                    "write_state": "deleted",
                    "original_acquired_at": "2026-08-02T10:30:00+08:00",
                    "result_acquired_at": "2026-08-02T10:30:00+08:00",
                    "freshness": "fresh",
                    "fitness_for_use": "fit",
                    "reason": "explicit_delete",
                },
            )
            remaining = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(
                remaining["records"],
                {
                    "unrelated-record": {
                        "owner": "another request",
                        "value": "must remain untouched",
                    }
                },
            )

    def test_installed_intraday_rejects_persistence_without_touching_the_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_parent = (
                Path(temporary_directory).resolve() / "must-not-be-inspected"
            )
            store_path = missing_parent / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "intraday_market_signal",
                "subjects": [{"security": "SSE:600519"}],
                "as_of": "2026-08-03",
                "window": None,
                "parameters": {},
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": False,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }

            completed = invoke_installed_market_trend(
                request,
                forbidden_markers=("",),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                (
                    "error: invalid research task: intraday_market_signal does not "
                    "permit persisted research evidence\n"
                ),
            )
            self.assertNotIn(str(store_path), completed.stderr)
            self.assertFalse(missing_parent.exists())

    def test_installed_malformed_store_fails_safely_without_path_or_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = (
                Path(temporary_directory).resolve() / "secret-project-name.json"
            )
            store_path.write_text("{", encoding="utf-8")

            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                forbidden_markers=("getHistoryData",),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                (
                    "error: invalid research task: research evidence_store could not "
                    "be read safely\n"
                ),
            )
            self.assertNotIn(str(store_path), completed.stderr)
            self.assertNotIn(store_path.name, completed.stderr)
            self.assertEqual(store_path.read_text(encoding="utf-8"), "{")

    def test_installed_store_rejects_symlink_file_and_parent_without_following(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            external_file = root / "external.json"
            external_file.write_text("external bytes", encoding="utf-8")
            symlink_file = root / "store.json"
            symlink_file.symlink_to(external_file)

            file_result = invoke_installed_market_trend(
                completed_market_trend_request(symlink_file),
                forbidden_markers=("getHistoryData",),
            )

            self.assertEqual(file_result.returncode, 2)
            self.assertNotIn(str(symlink_file), file_result.stderr)
            self.assertEqual(
                external_file.read_text(encoding="utf-8"), "external bytes"
            )

            real_parent = root / "real-parent"
            real_parent.mkdir()
            symlink_parent = root / "linked-parent"
            symlink_parent.symlink_to(real_parent, target_is_directory=True)
            parent_result = invoke_installed_market_trend(
                completed_market_trend_request(symlink_parent / "store.json"),
                forbidden_markers=("getHistoryData",),
            )

            self.assertEqual(parent_result.returncode, 2)
            self.assertNotIn(str(symlink_parent), parent_result.stderr)
            self.assertFalse((real_parent / "store.json").exists())

    def test_installed_store_rejects_unbounded_structure_before_acquisition(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            nested: object = "leaf"
            for _ in range(40):
                nested = {"child": nested}
            original = json.dumps(
                {
                    "schema_version": "1.0",
                    "records": {"unrelated": nested},
                },
                ensure_ascii=False,
            )
            store_path.write_text(original, encoding="utf-8")

            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                forbidden_markers=("getHistoryData",),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("structural bounds", completed.stderr)
            self.assertNotIn(str(store_path), completed.stderr)
            self.assertEqual(store_path.read_text(encoding="utf-8"), original)

    def test_installed_atomic_replace_failure_preserves_existing_store_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            first = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            original = store_path.read_bytes()
            different_request = completed_market_trend_request(store_path)
            different_request["window"] = {"trading_days": 9}

            failed = invoke_installed_market_trend(
                different_request,
                force_replace_failure=True,
            )

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(
                failed.stderr,
                (
                    "error: invalid research task: research evidence_store could not "
                    "be written safely\n"
                ),
            )
            self.assertNotIn(str(store_path), failed.stderr)
            self.assertEqual(store_path.read_bytes(), original)
            recovered = invoke_installed_market_trend(different_request)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)

    def test_installed_parent_swap_during_replace_never_overwrites_external_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            parent = root / "store-parent"
            parent.mkdir()
            external = root / "external-parent"
            external.mkdir()
            external_target = external / "research-evidence.json"
            external_target.write_text("external sentinel", encoding="utf-8")
            store_path = parent / "research-evidence.json"
            seeded = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            original = store_path.read_bytes()
            changed_request = completed_market_trend_request(store_path)
            changed_request["window"] = {"trading_days": 9}

            completed = invoke_installed_market_trend(
                changed_request,
                replace_parent_swap=(parent, external),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                (
                    "error: invalid research task: research evidence_store could not "
                    "be written safely\n"
                ),
            )
            self.assertEqual(
                external_target.read_text(encoding="utf-8"), "external sentinel"
            )
            self.assertEqual(
                (root / "store-parent-detached" / store_path.name).read_bytes(),
                original,
            )

    def test_installed_unavailable_platform_fails_before_path_or_source(self) -> None:
        for mode in ("read_write", "delete"):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                missing_parent = Path(temporary_directory).resolve() / "must-not-exist"
                store_path = missing_parent / "research-evidence.json"

                completed = invoke_installed_market_trend(
                    completed_market_trend_request(store_path, store_mode=mode),
                    force_unavailable_store=True,
                    forbidden_markers=("getHistoryData",),
                )

                self.assertEqual(completed.returncode, 2)
                self.assertEqual(
                    completed.stderr,
                    (
                        "error: invalid research task: research evidence_store "
                        "persistence is unavailable on this platform\n"
                    ),
                )
                self.assertFalse(missing_parent.exists())

    def test_installed_acquire_parent_swap_keeps_one_locked_directory_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            parent = root / "store-parent"
            parent.mkdir()
            external = root / "external-parent"
            external.mkdir()
            external_target = external / "research-evidence.json"
            external_target.write_text("external sentinel", encoding="utf-8")
            store_path = parent / "research-evidence.json"
            seeded = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            original = store_path.read_bytes()
            changed_request = completed_market_trend_request(store_path)
            changed_request["window"] = {"trading_days": 9}

            completed = invoke_installed_market_trend(
                changed_request,
                acquire_parent_swap=(parent, external),
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                (
                    "error: invalid research task: research evidence_store could not "
                    "be written safely\n"
                ),
            )
            self.assertEqual(
                (parent / store_path.name).read_text(encoding="utf-8"),
                "external sentinel",
            )
            self.assertEqual(
                (root / "store-parent-detached" / store_path.name).read_bytes(),
                original,
            )

    def test_installed_acquire_leaf_swap_never_overwrites_noncooperating_writer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            seeded = invoke_installed_market_trend(
                completed_market_trend_request(store_path)
            )
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            original = store_path.read_bytes()
            changed_request = completed_market_trend_request(store_path)
            changed_request["window"] = {"trading_days": 9}

            completed = invoke_installed_market_trend(
                changed_request,
                acquire_target_replacement=store_path,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                (
                    "error: invalid research task: research evidence_store could not "
                    "be written safely\n"
                ),
            )
            self.assertEqual(
                store_path.read_text(encoding="utf-8"), "concurrent sentinel"
            )
            self.assertEqual(
                store_path.with_suffix(".concurrent").read_bytes(), original
            )

    def test_installed_acquire_leaf_creation_preserves_initial_absence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"

            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                acquire_target_replacement=store_path,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                (
                    "error: invalid research task: research evidence_store could not "
                    "be written safely\n"
                ),
            )
            self.assertEqual(
                store_path.read_text(encoding="utf-8"), "concurrent sentinel"
            )
            self.assertFalse(store_path.with_suffix(".concurrent").exists())

    def test_installed_announcements_and_research_reports_write_then_hit(
        self,
    ) -> None:
        for material_type in ("announcement", "research_report"):
            with (
                self.subTest(material_type=material_type),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                store_path = (
                    Path(temporary_directory).resolve() / "research-evidence.json"
                )
                request: dict[str, object] = {
                    "schema_version": "1.0",
                    "task_type": "research_content",
                    "subjects": (
                        [{"clue": "蓝色光标"}]
                        if material_type == "announcement"
                        else []
                    ),
                    "as_of": "2026-08-02",
                    "window": {
                        "published_from": "2026-07-01",
                        "published_to": "2026-08-02",
                    },
                    "parameters": {
                        "material_types": [material_type],
                        "query": ["研究"],
                        "limit": 20,
                    },
                    "source_policy": {
                        "allow_experimental": True,
                        "allow_credentials": False,
                        "allow_fallback": True,
                        "evidence_store": {
                            "mode": "read_write",
                            "path": str(store_path),
                        },
                    },
                }

                first = invoke_installed_market_trend(request)
                second = invoke_installed_market_trend(
                    request,
                    forbid_content_collect=True,
                )

                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                first_result = json.loads(first.stdout)
                second_result = json.loads(second.stdout)
                first_operation = first_result["coverage"][
                    "research_content_materials"
                ]["operations"]["fixture_content@1"]
                second_operation = second_result["coverage"][
                    "research_content_materials"
                ]["operations"]["fixture_content@1"]
                self.assertEqual(
                    first_operation["evidence_storage"]["lookup_state"], "miss"
                )
                self.assertEqual(
                    second_operation["evidence_storage"]["lookup_state"], "hit"
                )
                self.assertEqual(
                    second_operation["evidence_storage"]["original_acquired_at"],
                    "2026-08-02T10:30:00+08:00",
                )
                self.assertEqual(
                    first_operation["observation_ids"],
                    second_operation["observation_ids"],
                )
                stored_text = store_path.read_text(encoding="utf-8")
                self.assertNotIn("Authorization", stored_text)
                self.assertNotIn("request_headers", stored_text)
                self.assertNotIn('"content":"', stored_text)

    def test_installed_content_hit_retains_operation_owned_batch_limitations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request: dict[str, object] = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["research_report"],
                    "query": ["研究"],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            operation_codes = {
                "title_keyword_filter_not_semantic_search",
                "theme_report_universe_incomplete",
            }
            batch_limitations = tuple(sorted(operation_codes))

            first = invoke_installed_market_trend(
                request,
                content_batch_limitations=batch_limitations,
            )
            second = invoke_installed_market_trend(
                request,
                forbid_content_collect=True,
                content_batch_limitations=batch_limitations,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_result = json.loads(first.stdout)
            second_result = json.loads(second.stdout)
            first_operation = first_result["coverage"]["research_content_materials"][
                "operations"
            ]["fixture_content@1"]
            second_operation = second_result["coverage"]["research_content_materials"][
                "operations"
            ]["fixture_content@1"]
            self.assertEqual(
                first_operation["evidence_storage"]["lookup_state"], "miss"
            )
            self.assertEqual(
                second_operation["evidence_storage"]["lookup_state"], "hit"
            )
            expected_reuse_facts = {
                "limitation_codes": list(batch_limitations),
                "degradation_codes": [],
            }
            self.assertEqual(
                first_operation["evidence_storage"]["reuse_facts"],
                expected_reuse_facts,
            )
            self.assertEqual(
                second_operation["evidence_storage"]["reuse_facts"],
                first_operation["evidence_storage"]["reuse_facts"],
            )
            first_owned = {
                limitation["code"]: limitation
                for limitation in first_result["limitations"]
                if limitation["code"] in operation_codes
            }
            second_owned = {
                limitation["code"]: limitation
                for limitation in second_result["limitations"]
                if limitation["code"] in operation_codes
            }
            self.assertEqual(set(first_owned), operation_codes)
            self.assertEqual(second_owned, first_owned)

    def test_installed_content_hit_retains_sibling_batch_facts(self) -> None:
        cases = (
            (
                "announcement",
                ("feed_completeness_unproven",),
                (),
            ),
            (
                "research_report",
                (),
                (
                    (
                        "future_material_rejected",
                        "A future material was rejected.",
                    ),
                ),
            ),
        )
        for material_type, batch_limitations, batch_degradations in cases:
            with (
                self.subTest(material_type=material_type),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                store_path = (
                    Path(temporary_directory).resolve() / "research-evidence.json"
                )
                request: dict[str, object] = {
                    "schema_version": "1.0",
                    "task_type": "research_content",
                    "subjects": (
                        [{"clue": "蓝色光标"}]
                        if material_type == "announcement"
                        else []
                    ),
                    "as_of": "2026-08-02",
                    "window": {
                        "published_from": "2026-07-01",
                        "published_to": "2026-08-02",
                    },
                    "parameters": {
                        "material_types": [material_type],
                        "query": ["研究"],
                        "limit": 20,
                    },
                    "source_policy": {
                        "allow_experimental": True,
                        "allow_credentials": False,
                        "allow_fallback": True,
                        "evidence_store": {
                            "mode": "read_write",
                            "path": str(store_path),
                        },
                    },
                }
                first = invoke_installed_market_trend(
                    request,
                    content_batch_limitations=batch_limitations,
                    content_batch_degradations=batch_degradations,
                )
                second = invoke_installed_market_trend(
                    request,
                    forbid_content_collect=True,
                    content_batch_limitations=batch_limitations,
                    content_batch_degradations=batch_degradations,
                )

                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                stored_text = store_path.read_text(encoding="utf-8")
                for _, diagnostic_message in batch_degradations:
                    self.assertNotIn(diagnostic_message, stored_text)
                first_result = json.loads(first.stdout)
                second_result = json.loads(second.stdout)
                first_operation = first_result["coverage"][
                    "research_content_materials"
                ]["operations"]["fixture_content@1"]
                second_operation = second_result["coverage"][
                    "research_content_materials"
                ]["operations"]["fixture_content@1"]
                expected_reuse_facts = {
                    "limitation_codes": list(batch_limitations),
                    "degradation_codes": sorted(code for code, _ in batch_degradations),
                }
                self.assertEqual(
                    first_operation["evidence_storage"]["reuse_facts"],
                    expected_reuse_facts,
                )
                self.assertEqual(
                    second_operation["evidence_storage"]["reuse_facts"],
                    expected_reuse_facts,
                )
                operation_codes = set(batch_limitations) | {
                    code for code, _ in batch_degradations
                }
                first_limitations = {
                    limitation["code"]: limitation
                    for limitation in first_result["limitations"]
                    if limitation["code"] in operation_codes
                }
                second_limitations = {
                    limitation["code"]: limitation
                    for limitation in second_result["limitations"]
                    if limitation["code"] in operation_codes
                }
                self.assertEqual(set(first_limitations), operation_codes)
                self.assertEqual(second_limitations, first_limitations)

    def test_installed_content_store_rejects_unknown_reuse_fact_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request: dict[str, object] = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["research_report"],
                    "query": ["研究"],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            first = invoke_installed_market_trend(
                request,
                content_batch_limitations=("title_keyword_filter_not_semantic_search",),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            stored = json.loads(store_path.read_text(encoding="utf-8"))
            record = next(iter(stored["records"].values()))
            record["reuse_facts"]["limitation_codes"] = ["private_provider_diagnostic"]
            store_path.write_text(
                json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tampered_bytes = store_path.read_bytes()

            completed = invoke_installed_market_trend(
                request,
                forbid_content_collect=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertIn(
                "stored operation evidence does not satisfy its reuse policy",
                completed.stderr,
            )
            self.assertNotIn(str(store_path), completed.stderr)
            self.assertNotIn(store_path.name, completed.stderr)
            self.assertEqual(store_path.read_bytes(), tampered_bytes)

    def test_installed_content_without_established_license_is_not_persisted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_parent = Path(temporary_directory).resolve() / "must-not-exist"
            store_path = missing_parent / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }

            completed = invoke_installed_market_trend(
                request,
                content_license=None,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            operation = result["coverage"]["research_content_materials"]["operations"][
                "fixture_content@1"
            ]
            self.assertEqual(operation["state"], "complete")
            self.assertEqual(
                operation["evidence_storage"],
                {
                    **operation["evidence_storage"],
                    "lookup_state": "not_permitted",
                    "write_state": "not_written",
                    "freshness": "not_assessed",
                    "fitness_for_use": "unfit",
                    "reason": "policy_not_permitted",
                },
            )
            self.assertFalse(missing_parent.exists())

    def test_installed_dynamic_license_attribute_cannot_opt_in_to_persistence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_parent = Path(temporary_directory).resolve() / "must-not-exist"
            store_path = missing_parent / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }

            completed = invoke_installed_market_trend(
                request,
                content_license="source_terms",
                dynamic_content_license=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            operation = json.loads(completed.stdout)["coverage"][
                "research_content_materials"
            ]["operations"]["fixture_content@1"]
            self.assertEqual(
                operation["evidence_storage"]["lookup_state"], "not_permitted"
            )
            self.assertEqual(
                operation["evidence_storage"]["write_state"], "not_written"
            )
            self.assertFalse(missing_parent.exists())

    def test_installed_content_hit_revalidates_established_license_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = {
                "schema_version": "1.0",
                "task_type": "research_content",
                "subjects": [{"clue": "蓝色光标"}],
                "as_of": "2026-08-02",
                "window": {
                    "published_from": "2026-07-01",
                    "published_to": "2026-08-02",
                },
                "parameters": {
                    "material_types": ["announcement"],
                    "query": [],
                    "limit": 20,
                },
                "source_policy": {
                    "allow_experimental": True,
                    "allow_credentials": False,
                    "allow_fallback": True,
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }
            first = invoke_installed_market_trend(request)
            self.assertEqual(first.returncode, 0, first.stderr)
            stored = json.loads(store_path.read_text(encoding="utf-8"))
            records = stored["records"]
            self.assertEqual(len(records), 1)
            record = next(iter(records.values()))
            record["observations"][0]["license_boundary"]["status"] = "not_established"
            store_path.write_text(
                json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )

            completed = invoke_installed_market_trend(
                request,
                forbid_content_collect=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "stored operation evidence does not satisfy its reuse policy",
                completed.stderr,
            )
            self.assertNotIn(str(store_path), completed.stderr)

    def test_installed_financial_reports_hit_without_reusing_current_observations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
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
                    "evidence_store": {
                        "mode": "read_write",
                        "path": str(store_path),
                    },
                },
            }

            first = invoke_installed_valuation(request, valuation_fixtures=True)
            second = invoke_installed_valuation(
                request,
                valuation_fixtures=True,
                fail_url_contains="CompanyFinanceService.getFinanceReport2022",
            )

            first_operations = first["coverage"]["security_valuation_metrics"][
                "operations"
            ]
            second_operations = second["coverage"]["security_valuation_metrics"][
                "operations"
            ]
            first_financial = first_operations["sina_financial_statements@1"]
            second_financial = second_operations["sina_financial_statements@1"]
            self.assertEqual(
                first_financial["evidence_storage"]["lookup_state"], "miss"
            )
            self.assertEqual(
                second_financial["evidence_storage"]["lookup_state"], "hit"
            )
            self.assertEqual(
                first_financial["observation_ids"], second_financial["observation_ids"]
            )
            for current_operation in (
                "eastmoney_stock_info@1",
                "ths_consensus_eps@1",
                "sse_daily_line@1",
            ):
                if current_operation in second_operations:
                    self.assertNotIn(
                        "evidence_storage", second_operations[current_operation]
                    )
            store = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(len(store["records"]), 1)
            financial_record = next(iter(store["records"].values()))
            self.assertEqual(
                financial_record["operation_id"], "sina_financial_statements@1"
            )
            self.assertTrue(
                all(
                    observation["source_operation"] == "sina_financial_statements@1"
                    for observation in financial_record["observations"]
                )
            )

    def test_installed_forward_adjusted_completed_history_writes_then_hits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"
            request = completed_market_trend_request(store_path)
            request["parameters"] = {"adjustment": "forward_adjusted"}

            first = invoke_installed_market_trend(request)
            second = invoke_installed_market_trend(
                request,
                forbidden_markers=("push2his.eastmoney.com",),
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_result = json.loads(first.stdout)
            second_result = json.loads(second.stdout)
            operation_id = "eastmoney_forward_adjusted_daily_line@1"
            first_operation = first_result["coverage"]["market_trend_series"][
                "operations"
            ][operation_id]
            second_operation = second_result["coverage"]["market_trend_series"][
                "operations"
            ][operation_id]
            self.assertEqual(
                first_operation["evidence_storage"]["lookup_state"], "miss"
            )
            self.assertEqual(
                second_operation["evidence_storage"]["lookup_state"], "hit"
            )
            self.assertEqual(
                first_operation["observation_ids"], second_operation["observation_ids"]
            )
            self.assertEqual(
                second_operation["evidence_storage"]["original_acquired_at"],
                "2026-08-02T10:30:00+08:00",
            )

    def test_installed_intraday_row_is_never_written_as_completed_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory).resolve() / "research-evidence.json"

            completed = invoke_installed_market_trend(
                completed_market_trend_request(store_path),
                append_intraday_official=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            operation = result["coverage"]["market_trend_series"]["operations"][
                "szse_daily_line@1"
            ]
            self.assertEqual(operation["evidence_storage"]["write_state"], "written")
            store = json.loads(store_path.read_text(encoding="utf-8"))
            record = next(iter(store["records"].values()))
            self.assertFalse(
                any(
                    observation.get("locator", {}).get("uri")
                    == "https://fixture.test/intraday"
                    for observation in record["observations"]
                )
            )

    def test_installed_noneligible_content_types_are_fresh_with_no_store_access(
        self,
    ) -> None:
        cases = (
            ("stock_news", True),
            ("market_flash", False),
            ("investor_qa", True),
            ("issuer_profile", True),
            ("consensus_material", True),
        )
        for material_type, requires_subject in cases:
            with (
                self.subTest(material_type=material_type),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                missing_parent = Path(temporary_directory).resolve() / "must-not-exist"
                store_path = missing_parent / "research-evidence.json"
                request = {
                    "schema_version": "1.0",
                    "task_type": "research_content",
                    "subjects": [{"clue": "蓝色光标"}] if requires_subject else [],
                    "as_of": "2026-08-02",
                    "window": {
                        "published_from": "2026-07-01",
                        "published_to": "2026-08-02",
                    },
                    "parameters": {
                        "material_types": [material_type],
                        "query": [],
                        "limit": 20,
                    },
                    "source_policy": {
                        "allow_experimental": True,
                        "allow_credentials": False,
                        "allow_fallback": True,
                        "evidence_store": {
                            "mode": "read_write",
                            "path": str(store_path),
                        },
                    },
                }

                completed = invoke_installed_market_trend(request)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                result = json.loads(completed.stdout)
                audit = result["coverage"]["research_content_materials"]["operations"][
                    "fixture_content@1"
                ]["evidence_storage"]
                self.assertEqual(audit["lookup_state"], "not_permitted")
                self.assertEqual(audit["write_state"], "not_written")
                self.assertEqual(audit["reason"], "policy_not_permitted")
                self.assertFalse(missing_parent.exists())

    def test_installed_concurrent_processes_preserve_distinct_canonical_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            store_path = root / "research-evidence.json"
            source_started = root / "first-source-started"
            first_request = completed_market_trend_request(store_path)
            second_request = completed_market_trend_request(store_path)
            second_request["window"] = {"trading_days": 9}

            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    invoke_installed_market_trend,
                    first_request,
                    source_delay_seconds=1.0,
                    source_started_path=source_started,
                )
                deadline = time.monotonic() + 10
                while not source_started.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(source_started.exists(), "first writer did not acquire")
                second_future = executor.submit(
                    invoke_installed_market_trend,
                    second_request,
                )
                first = first_future.result(timeout=20)
                second = second_future.result(timeout=20)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            store = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(len(store["records"]), 2)
            request_windows = {
                record["request_scope"]["window"]["trading_days"]
                for record in store["records"].values()
            }
            self.assertEqual(request_windows, {9, 10})


if __name__ == "__main__":
    unittest.main()
