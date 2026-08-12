from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capital_contract import (  # noqa: E402
    CapitalQuery,
    CapitalSourceBatch,
)
from a_share_research.research_result import (  # noqa: E402
    UnifiedResearchResult,
    stable_result_id,
)
from a_share_research.research_runtime import ResearchRuntime  # noqa: E402

from tests.test_research_workflows import fixture_runtime  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skill" / "a-share-research"
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


def workflow_request(workflow_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": [{"clue": "工业富联", "issuer_security_class_count": 1}],
        "as_of": "2026-08-02",
        "window": None,
        "parameters": {
            "workflow": {"id": workflow_id, "version": "1.0"},
            "inputs": {"target_pe": "30"},
        },
        "source_policy": {
            "allow_experimental": False,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


def theme_workflow_request(query: str = "AI服务器") -> dict[str, object]:
    request = workflow_request("single_security_valuation")
    request["subjects"] = []
    request["parameters"] = {
        "workflow": {"id": "theme_report_research", "version": "1.0"},
        "inputs": {
            "query": [query],
            "published_from": "2026-05-05",
            "published_to": "2026-08-02",
            "limit": 20,
            "verify_documents": False,
        },
    }
    request["source_policy"] = {
        "allow_experimental": True,
        "allow_credentials": True,
        "allow_fallback": False,
    }
    return request


def valuation_workflow_request() -> dict[str, object]:
    request = workflow_request("single_security_valuation")
    request["source_policy"] = {
        "allow_experimental": True,
        "allow_credentials": False,
        "allow_fallback": True,
    }
    return request


def comparison_workflow_request(clues: list[str]) -> dict[str, object]:
    request = workflow_request("valuation_comparison")
    request["subjects"] = [
        {"clue": clue, "issuer_security_class_count": 1} for clue in clues
    ]
    request["source_policy"] = {
        "allow_experimental": True,
        "allow_credentials": False,
        "allow_fallback": True,
    }
    return request


def new_security_workflow_request() -> dict[str, object]:
    request = workflow_request("single_security_valuation")
    request["parameters"] = {
        "workflow": {"id": "new_security_research", "version": "1.0"},
        "inputs": {
            "target_pe": "30",
            "report_window": {
                "published_from": "2026-05-05",
                "published_to": "2026-08-02",
            },
            "market_window": {
                "observed_from": "2026-07-01",
                "observed_to": "2026-08-02",
            },
            "lockup_window": {
                "observed_from": "2026-08-02",
                "observed_to": "2026-10-31",
            },
            "fund_flow_period": "5d",
            "limit": 20,
            "verify_documents": False,
        },
    }
    return request


def completed_new_security_workflow_result(
    runtime: ResearchRuntime | None = None,
) -> dict[str, object]:
    request = new_security_workflow_request()
    request["source_policy"] = {
        "allow_experimental": True,
        "allow_credentials": False,
        "allow_fallback": True,
    }
    return (runtime or fixture_runtime()).research(request).to_dict()


class EmptyWorkflowCapitalOperation:
    operation_id = "empty_workflow_capital@1"
    supported_data_types = frozenset(
        {"stock_fund_flow", "dragon_tiger", "lockup", "margin_trading"}
    )
    raw_fields = (
        "data.klines[]",
        "result.data[]",
        "SECURITY_CODE",
        "TRADE_DATE",
        "FREE_DATE",
    )

    def collect(self, query: CapitalQuery) -> CapitalSourceBatch:
        return CapitalSourceBatch(
            operation_id=self.operation_id,
            observations=(),
            complete=True,
        )


def invoke_copied_installed_process(
    request: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        installed = root / "installed" / "a-share-research"
        shutil.copytree(SKILL_ROOT, installed)
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-I",
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ],
            cwd=root,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
            capture_output=True,
            text=True,
        )


def invoke_copied_installed(request: dict[str, object]) -> dict[str, object]:
    completed = invoke_copied_installed_process(request)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    if completed.stderr:
        raise AssertionError(completed.stderr)
    if len(completed.stdout.strip().splitlines()) != 1:
        raise AssertionError("public stdout must contain one JSON line")
    loaded = json.loads(completed.stdout)
    if not isinstance(loaded, dict):
        raise AssertionError("public result must be an object")
    return loaded


def _rehash_brief(brief: dict[str, object]) -> tuple[str, str]:
    old_id = str(brief["id"])
    brief["id"] = stable_result_id(
        "brief", {key: value for key, value in brief.items() if key != "id"}
    )
    return old_id, str(brief["id"])


def _replace(values: list[object], old: str, new: str) -> None:
    values[:] = [new if item == old else item for item in values]


def _add_step_record(
    result: UnifiedResearchResult,
    *,
    collection: str,
    record: dict[str, object],
) -> None:
    object.__setattr__(result, collection, (*getattr(result, collection), record))
    reference_field = f"{collection[:-1]}_ids"
    record_id = str(record["id"])
    view = result.task_views["research_workflow"]
    step = view["steps"][0]
    step[reference_field].append(record_id)
    result.coverage["steps"][step["step_id"]][reference_field].append(record_id)
    leaf_brief = next(
        item for item in result.briefs if item["id"] == step["brief_ids"][0]
    )
    leaf_brief[reference_field].append(record_id)
    old_leaf, new_leaf = _rehash_brief(leaf_brief)
    _replace(step["brief_ids"], old_leaf, new_leaf)
    _replace(
        result.coverage["steps"][step["step_id"]]["brief_ids"],
        old_leaf,
        new_leaf,
    )
    main_brief = next(item for item in result.briefs if item["id"] == view["brief_id"])
    main_brief[reference_field].append(record_id)
    view[reference_field].append(record_id)
    old_main, new_main = _rehash_brief(main_brief)
    if view["brief_id"] != old_main:
        raise AssertionError("workflow view did not own the main brief")
    view["brief_id"] = new_main


def _rehash_workflow(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)

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
    for observation in result["observations"]:  # type: ignore[union-attr]
        old_id = observation["id"]
        observation["id"] = stable_result_id(
            "observation",
            {key: nested for key, nested in observation.items() if key != "id"},
        )
        replacements[old_id] = observation["id"]
    replace_references(result, replacements)
    replacements = {}
    for field, prefix in (
        ("claims", "claim"),
        ("calculations", "calculation"),
        ("conflicts", "conflict"),
        ("limitations", "limitation"),
    ):
        for record in result[field]:  # type: ignore[index]
            old_id = record["id"]
            record["id"] = stable_result_id(
                prefix,
                {key: nested for key, nested in record.items() if key != "id"},
            )
            replacements[old_id] = record["id"]
    replace_references(result, replacements)
    replacements = {}
    for brief in result["briefs"]:  # type: ignore[union-attr]
        old_id = brief["id"]
        brief["id"] = stable_result_id(
            "brief", {key: nested for key, nested in brief.items() if key != "id"}
        )
        replacements[old_id] = brief["id"]
    replace_references(result, replacements)
    return result


def _validate_serialized(result: dict[str, object]) -> None:
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


def _rehash_step_leaf_plan(result: dict[str, object], step_id: str) -> None:
    step_registry = result["capability_registry"]["steps"][step_id]  # type: ignore[index]
    plan = step_registry["leaf_plan"]
    leaf_coverage = result["coverage"]["steps"][step_id]["leaf"]  # type: ignore[index]
    dispositions = []
    for capability_id, capability in leaf_coverage.items():
        for operation_id, operation in capability["operations"].items():
            dispositions.append(
                {
                    "capability_id": capability_id,
                    "operation_id": operation_id,
                    "fields": operation["field_dispositions"],
                }
            )
    plan["id"] = stable_result_id(
        "plan",
        {
            "version": plan["version"],
            "task_type": plan["task_type"],
            "operation_obligations": plan["operation_obligations"],
            "research_dependencies": plan["research_dependencies"],
            "field_dispositions": dispositions,
        },
    )


def _workflow_skip_limitation() -> dict[str, object]:
    payload: dict[str, object] = {
        "code": "workflow_step_skipped_research_dependency",
        "message": (
            "The workflow step has a research gap because its identity dependency "
            "was blocked."
        ),
        "references": [],
    }
    return {"id": stable_result_id("limitation", payload), **payload}


def _replace_step_material(
    target: dict[str, object], source: dict[str, object], step_id: str
) -> None:
    target_view = target["task_views"]["research_workflow"]
    source_view = source["task_views"]["research_workflow"]
    target_step = next(
        item for item in target_view["steps"] if item["step_id"] == step_id
    )
    source_step = next(
        item for item in source_view["steps"] if item["step_id"] == step_id
    )
    for collection in (
        "observations",
        "claims",
        "calculations",
        "conflicts",
        "limitations",
        "source_errors",
    ):
        reference_field = f"{collection[:-1]}_ids"
        sibling_ids = {
            item
            for sibling in target_view["steps"]
            if sibling is not target_step
            for item in sibling[reference_field]
        }
        exclusive = set(target_step[reference_field]) - sibling_ids
        target[collection] = [
            item for item in target[collection] if item["id"] not in exclusive
        ]
        known_ids = {item["id"] for item in target[collection]}
        source_ids = set(source_step[reference_field])
        target[collection].extend(
            deepcopy(item)
            for item in source[collection]
            if item["id"] in source_ids and item["id"] not in known_ids
        )
        target_step[reference_field] = deepcopy(source_step[reference_field])
        target_view[reference_field] = [item["id"] for item in target[collection]]
    target_brief_ids = set(target_step["brief_ids"])
    target["briefs"] = [
        item for item in target["briefs"] if item["id"] not in target_brief_ids
    ]
    target["briefs"].extend(
        deepcopy(item)
        for item in source["briefs"]
        if item["id"] in source_step["brief_ids"]
    )
    for field in ("state", "workflow_limitation_ids", "brief_ids"):
        target_step[field] = deepcopy(source_step[field])
    target_registry = target["capability_registry"]["steps"][step_id]
    source_registry = source["capability_registry"]["steps"][step_id]
    for field in ("leaf_plan", "capability_registry"):
        target_registry[field] = deepcopy(source_registry[field])
    target["coverage"]["steps"][step_id] = deepcopy(
        source["coverage"]["steps"][step_id]
    )
    main_brief = next(
        item for item in target["briefs"] if item["id"] == target_view["brief_id"]
    )
    for reference_field in (
        "observation_ids",
        "claim_ids",
        "calculation_ids",
        "conflict_ids",
        "limitation_ids",
        "source_error_ids",
    ):
        main_brief[reference_field] = deepcopy(target_view[reference_field])
    old_id, new_id = _rehash_brief(main_brief)
    if target_view["brief_id"] != old_id:
        raise AssertionError("workflow view did not own its main brief")
    target_view["brief_id"] = new_id


class DirectResearchWorkflowRuntimeTests(unittest.TestCase):
    def test_policy_blocked_comparison_workflow_retains_formal_leaf_plan(self) -> None:
        request = workflow_request("valuation_comparison")
        request["subjects"] = [
            {"clue": "工业富联", "issuer_security_class_count": 1},
            {"clue": "贵州茅台", "issuer_security_class_count": 1},
        ]

        result = ResearchRuntime().research(request).to_dict()

        registry = result["capability_registry"]["steps"]["comparison"]
        leaf_plan = registry["leaf_plan"]
        leaf_coverage = result["coverage"]["steps"]["comparison"]["leaf"]
        self.assertEqual(leaf_plan["task_type"], "valuation_compare")
        self.assertEqual(len(leaf_plan["operation_obligations"]), 6)
        self.assertEqual(set(leaf_coverage), {"valuation_compare_metrics"})
        claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "valuation_compare_diagnostics"
        )
        self.assertEqual(
            [item["name"] for item in claim["payload"]["rows"]],
            ["工业富联", "贵州茅台"],
        )

    def test_policy_blocked_comparison_rejects_leaf_for_other_subjects(self) -> None:
        def blocked(clues: list[str]) -> dict[str, object]:
            request = workflow_request("valuation_comparison")
            request["subjects"] = [
                {"clue": clue, "issuer_security_class_count": 1} for clue in clues
            ]
            return ResearchRuntime().research(request).to_dict()

        target = blocked(["工业富联", "贵州茅台"])
        source = blocked(["中国平安", "蓝色光标"])
        _replace_step_material(target, source, "comparison")

        with self.assertRaisesRegex(
            ValueError, "workflow leaf subject contradicts its request scope"
        ):
            _validate_serialized(_rehash_workflow(target))

    def test_runtime_source_has_no_private_legacy_result_constructor(self) -> None:
        runtime_source = (
            SKILL_ROOT / "scripts/a_share_research/research_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("def _blocked_result(", runtime_source)
        self.assertNotIn("def _complete_result(", runtime_source)
        self.assertNotIn('"degradations"', runtime_source)

    def test_public_docs_do_not_describe_nested_workflow_leaf_results(self) -> None:
        cli_contract = (SKILL_ROOT / "references/cli-contract.md").read_text(
            encoding="utf-8"
        )
        example = (REPOSITORY_ROOT / "examples/industrial-fulian.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "complete `research_task`, and complete leaf `result`", cli_contract
        )
        self.assertNotIn(
            "Top-level evidence, conflicts, source errors, degradations", cli_contract
        )
        self.assertNotIn("每一步都保留完整 ResearchTask、结果", example)

    def test_copied_installed_single_valuation_is_formal_unified_result(self) -> None:
        result = invoke_copied_installed(workflow_request("single_security_valuation"))

        self.assertEqual(set(result), UNIFIED_RESULT_KEYS)
        self.assertEqual(result["plan"]["task_type"], "research_workflow")
        self.assertEqual(result["plan"]["workflow_id"], "single_security_valuation")

    def test_runtime_returns_workflow_unified_result_directly(self) -> None:
        result = ResearchRuntime().research(
            workflow_request("single_security_valuation")
        )

        self.assertIsInstance(result, UnifiedResearchResult)

    def test_workflow_subject_schema_rejects_unknown_fields(self) -> None:
        for workflow_id, subject_count in (
            ("single_security_valuation", 1),
            ("valuation_comparison", 2),
            ("new_security_research", 1),
        ):
            with self.subTest(workflow_id=workflow_id):
                request = (
                    new_security_workflow_request()
                    if workflow_id == "new_security_research"
                    else workflow_request(workflow_id)
                )
                request["subjects"] = [
                    {
                        "clue": f"security-{index}",
                        "issuer_security_class_count": 1,
                        "legacy_field": "x",
                    }
                    for index in range(subject_count)
                ]
                with self.assertRaisesRegex(
                    ValueError, "workflow subject contains unknown fields"
                ):
                    ResearchRuntime().research(request)
        copied = workflow_request("single_security_valuation")
        copied["subjects"][0]["legacy_field"] = "x"
        completed = invoke_copied_installed_process(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: invalid research task: workflow subject contains unknown fields\n",
        )

    def test_workflow_retains_step_scoped_formal_acquisition_lineage(self) -> None:
        result = fixture_runtime().research(theme_workflow_request()).to_dict()

        registry = result["capability_registry"]["steps"]["report_research"]
        self.assertEqual(registry["leaf_plan"]["task_type"], "research_content")
        self.assertIn("research_content_materials", registry["capability_registry"])
        step_coverage = result["coverage"]["steps"]["report_research"]
        operation = step_coverage["leaf"]["research_content_materials"]["operations"][
            "iwencai_content_search@1"
        ]
        self.assertIn(operation["state"], {"failed", "partial"})
        self.assertIsNone(operation["query_complete"])
        self.assertTrue(operation["field_dispositions"])
        self.assertEqual(
            operation["source_error_ids"],
            step_coverage["source_error_ids"],
        )
        self.assertEqual(
            operation["source_error_ids"],
            result["task_views"]["research_workflow"]["steps"][0]["source_error_ids"],
        )

    def test_copied_installed_identity_block_skips_only_true_dependents(self) -> None:
        result = invoke_copied_installed(new_security_workflow_request())

        steps = result["task_views"]["research_workflow"]["steps"]
        self.assertEqual(steps[0]["step_id"], "identity")
        self.assertEqual(steps[0]["state"], "blocked")
        self.assertEqual(
            {item["state"] for item in steps[1:]},
            {"skipped_research_dependency"},
        )
        self.assertTrue(steps[0]["limitation_ids"])
        self.assertTrue(all(item["workflow_limitation_ids"] for item in steps[1:]))
        self.assertEqual(result["status"], "blocked")

    def test_serializer_rejects_rehashed_unknown_workflow_record_kinds(self) -> None:
        observation_result = (
            fixture_runtime().research(theme_workflow_request()).to_dict()
        )
        material = next(
            item
            for item in observation_result["observations"]
            if item["payload"].get("kind") == "research_content_material"
        )
        material["payload"]["kind"] = "unknown_workflow_observation"
        valuation_result = (
            fixture_runtime().research(valuation_workflow_request()).to_dict()
        )
        cases = (
            ("observations", observation_result),
            (
                "claims",
                deepcopy(valuation_result),
            ),
            (
                "calculations",
                deepcopy(valuation_result),
            ),
        )
        cases[1][1]["claims"][0]["kind"] = "unknown_workflow_claim"
        cases[2][1]["calculations"][0]["kind"] = "unknown_workflow_calculation"
        claim_plan = cases[1][1]["capability_registry"]["steps"]["valuation"][
            "leaf_plan"
        ]
        claim_plan["research_dependencies"][0]["consumer_id"] = "unknown_workflow_claim"
        _rehash_step_leaf_plan(cases[1][1], "valuation")
        calculation_plan = cases[2][1]["capability_registry"]["steps"]["valuation"][
            "leaf_plan"
        ]
        original_calculation_kind = valuation_result["calculations"][0]["kind"]
        calculation_dependency = next(
            item
            for item in calculation_plan["research_dependencies"]
            if item["consumer_id"] == original_calculation_kind
        )
        calculation_dependency["consumer_id"] = "unknown_workflow_calculation"
        _rehash_step_leaf_plan(cases[2][1], "valuation")
        for collection, forged in cases:
            with self.subTest(collection=collection):
                expected_error = {
                    "observations": "research content observation kind is unsupported",
                    "claims": "valuation claim kind is invalid",
                    "calculations": "valuation calculation kind is invalid",
                }[collection]
                with self.assertRaisesRegex(ValueError, expected_error):
                    _validate_serialized(_rehash_workflow(forged))

    def test_serializer_rejects_rehashed_dangling_workflow_record_reference(
        self,
    ) -> None:
        original = fixture_runtime().research(valuation_workflow_request()).to_dict()
        cases = {
            "claim": deepcopy(original),
            "calculation": deepcopy(original),
            "limitation": deepcopy(original),
        }
        cases["claim"]["claims"][0]["observation_ids"][0] = "observation-does-not-exist"
        cases["claim"]["claims"][0]["evidence_adoptions"][0]["observation_id"] = (
            "observation-does-not-exist"
        )
        cases["calculation"]["calculations"][0]["input_ids"][0] = (
            "observation-does-not-exist"
        )
        cases["limitation"]["limitations"][0]["references"] = [
            "observation-does-not-exist"
        ]
        for record_kind, forged in cases.items():
            with self.subTest(record_kind=record_kind):
                expected_error = {
                    "claim": "^claim is invalid$",
                    "calculation": "^calculation is invalid$",
                    "limitation": "^limitation is invalid$",
                }[record_kind]
                with self.assertRaisesRegex(ValueError, expected_error):
                    _validate_serialized(_rehash_workflow(forged))

    def test_serializer_rejects_jointly_swapped_capital_step_lineage(self) -> None:
        for swap_stored_scope in (False, True):
            with self.subTest(swap_stored_scope=swap_stored_scope):
                forged = completed_new_security_workflow_result()
                left_id = "fund_flow"
                right_id = "dragon_tiger"
                registries = forged["capability_registry"]["steps"]
                fields = ["leaf_plan", "capability_registry"]
                if swap_stored_scope:
                    fields.append("leaf_request_scope")
                for field in fields:
                    registries[left_id][field], registries[right_id][field] = (
                        registries[right_id][field],
                        registries[left_id][field],
                    )
                coverages = forged["coverage"]["steps"]
                coverages[left_id], coverages[right_id] = (
                    coverages[right_id],
                    coverages[left_id],
                )
                steps = forged["task_views"]["research_workflow"]["steps"]
                left = next(item for item in steps if item["step_id"] == left_id)
                right = next(item for item in steps if item["step_id"] == right_id)
                for field in (
                    "state",
                    "observation_ids",
                    "claim_ids",
                    "calculation_ids",
                    "conflict_ids",
                    "limitation_ids",
                    "source_error_ids",
                    "workflow_limitation_ids",
                    "brief_ids",
                ):
                    left[field], right[field] = right[field], left[field]

                with self.assertRaisesRegex(
                    ValueError,
                    (
                        "workflow leaf request scope contradicts its step"
                        if swap_stored_scope
                        else "workflow leaf facts contradict their request scope"
                    ),
                ):
                    _validate_serialized(forged)

    def test_serializer_rejects_empty_capital_coverage_step_swap(self) -> None:
        runtime = fixture_runtime()
        runtime._capital_operations = (EmptyWorkflowCapitalOperation(),)
        forged = completed_new_security_workflow_result(runtime)
        left_id = "fund_flow"
        right_id = "dragon_tiger"
        registries = forged["capability_registry"]["steps"]
        for field in ("leaf_plan", "capability_registry"):
            registries[left_id][field], registries[right_id][field] = (
                registries[right_id][field],
                registries[left_id][field],
            )
        coverages = forged["coverage"]["steps"]
        coverages[left_id], coverages[right_id] = (
            coverages[right_id],
            coverages[left_id],
        )
        steps = forged["task_views"]["research_workflow"]["steps"]
        left = next(item for item in steps if item["step_id"] == left_id)
        right = next(item for item in steps if item["step_id"] == right_id)
        for field in (
            "state",
            "observation_ids",
            "claim_ids",
            "calculation_ids",
            "conflict_ids",
            "limitation_ids",
            "source_error_ids",
            "workflow_limitation_ids",
            "brief_ids",
        ):
            left[field], right[field] = right[field], left[field]

        with self.assertRaisesRegex(
            ValueError, "workflow leaf coverage contradicts its request scope"
        ):
            _validate_serialized(forged)

    def test_serializer_rejects_child_facts_for_another_security(self) -> None:
        target = completed_new_security_workflow_result()
        alternate_request = new_security_workflow_request()
        alternate_request["subjects"] = [
            {"clue": "贵州茅台", "issuer_security_class_count": 1}
        ]
        alternate_request["source_policy"] = {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": True,
        }
        source = fixture_runtime().research(alternate_request).to_dict()
        _replace_step_material(target, source, "fund_flow")

        with self.assertRaisesRegex(
            ValueError, "workflow leaf subject contradicts its request scope"
        ):
            _validate_serialized(target)

    def test_serializer_rejects_valuation_facts_for_other_natural_clues(self) -> None:
        cases = (
            (
                valuation_workflow_request(),
                valuation_workflow_request(),
                "valuation",
            ),
            (
                comparison_workflow_request(["工业富联", "贵州茅台"]),
                comparison_workflow_request(["中国平安", "蓝色光标"]),
                "comparison",
            ),
        )
        cases[0][1]["subjects"] = [
            {"clue": "贵州茅台", "issuer_security_class_count": 1}
        ]
        for target_request, source_request, step_id in cases:
            with self.subTest(step_id=step_id):
                target = fixture_runtime().research(target_request).to_dict()
                source = fixture_runtime().research(source_request).to_dict()
                _replace_step_material(target, source, step_id)

                with self.assertRaisesRegex(
                    ValueError, "workflow leaf subject contradicts its request scope"
                ):
                    _validate_serialized(target)

    def test_serializer_rejects_valuation_facts_for_other_class_scope(self) -> None:
        forged = fixture_runtime().research(valuation_workflow_request()).to_dict()
        plan = forged["plan"]
        plan["request_scope"]["subjects"][0]["issuer_security_class_count"] = 2
        forged["capability_registry"]["steps"]["valuation"]["leaf_request_scope"][
            "subjects"
        ][0]["issuer_security_class_count"] = 2
        plan["id"] = stable_result_id(
            "plan", {key: value for key, value in plan.items() if key != "id"}
        )

        with self.assertRaisesRegex(
            ValueError, "workflow leaf class scope contradicts its request scope"
        ):
            _validate_serialized(forged)

    def test_serializer_rejects_blocked_comparison_for_other_names(self) -> None:
        target_request = comparison_workflow_request(["工业富联", "贵州茅台"])
        source_request = comparison_workflow_request(["中国平安", "蓝色光标"])
        for request in (target_request, source_request):
            for subject in request["subjects"]:
                subject["issuer_security_class_count"] = 2
        target = fixture_runtime().research(target_request).to_dict()
        source = fixture_runtime().research(source_request).to_dict()
        _replace_step_material(target, source, "comparison")

        with self.assertRaisesRegex(
            ValueError, "workflow leaf subject contradicts its request scope"
        ):
            _validate_serialized(target)

    def test_serializer_rejects_theme_leaf_for_another_query(self) -> None:
        target = fixture_runtime().research(theme_workflow_request("毛利")).to_dict()
        source = fixture_runtime().research(theme_workflow_request("净利润")).to_dict()
        target_titles = {
            item["payload"]["title"]
            for item in target["observations"]
            if item["payload"].get("kind") == "research_content_material"
        }
        source_titles = {
            item["payload"]["title"]
            for item in source["observations"]
            if item["payload"].get("kind") == "research_content_material"
        }
        self.assertTrue(target_titles)
        self.assertTrue(source_titles)
        self.assertTrue(all("毛利" in title for title in target_titles))
        self.assertTrue(all("净利润" in title for title in source_titles))
        self.assertTrue(target_titles.isdisjoint(source_titles))
        _replace_step_material(target, source, "report_research")

        with self.assertRaisesRegex(
            ValueError, "workflow leaf facts contradict their request scope"
        ):
            _validate_serialized(_rehash_workflow(target))

    def test_serializer_rejects_leaf_limitation_as_workflow_limitation(self) -> None:
        original = completed_new_security_workflow_result()
        for step_id in ("institutional_coverage", "valuation", "fund_flow"):
            with self.subTest(step_id=step_id):
                forged = deepcopy(original)
                step = next(
                    item
                    for item in forged["task_views"]["research_workflow"]["steps"]
                    if item["step_id"] == step_id
                )
                limitation_id = step["limitation_ids"][0]
                step["workflow_limitation_ids"] = [limitation_id]
                step["state"] = "blocked"
                forged["coverage"]["steps"][step_id]["workflow_limitation_ids"] = [
                    limitation_id
                ]

                with self.assertRaisesRegex(
                    ValueError, "workflow dependency state contradicts its fixed plan"
                ):
                    _validate_serialized(forged)

    def test_serializer_rejects_forged_skip_after_supported_identity(self) -> None:
        original = completed_new_security_workflow_result()
        for step_id in ("institutional_coverage", "valuation", "fund_flow"):
            with self.subTest(step_id=step_id):
                forged = deepcopy(original)
                view = forged["task_views"]["research_workflow"]
                step = next(
                    item for item in view["steps"] if item["step_id"] == step_id
                )
                for collection in (
                    "observations",
                    "claims",
                    "calculations",
                    "conflicts",
                    "limitations",
                    "source_errors",
                ):
                    reference_field = f"{collection[:-1]}_ids"
                    removed = set(step[reference_field])
                    sibling_references = {
                        item
                        for sibling in view["steps"]
                        if sibling is not step
                        for item in sibling[reference_field]
                    }
                    exclusive = removed - sibling_references
                    forged[collection] = [
                        item
                        for item in forged[collection]
                        if item["id"] not in exclusive
                    ]
                    view[reference_field] = [
                        item for item in view[reference_field] if item not in exclusive
                    ]
                    step[reference_field] = []
                leaf_brief_ids = set(step["brief_ids"])
                forged["briefs"] = [
                    item
                    for item in forged["briefs"]
                    if item["id"] not in leaf_brief_ids
                ]
                step["brief_ids"] = []
                skip_limitation = _workflow_skip_limitation()
                forged["limitations"].append(skip_limitation)
                view["limitation_ids"].append(skip_limitation["id"])
                step["workflow_limitation_ids"] = [skip_limitation["id"]]
                step["state"] = "skipped_research_dependency"
                registry = forged["capability_registry"]["steps"][step_id]
                registry["leaf_plan"] = None
                registry["capability_registry"] = {}
                forged["coverage"]["steps"][step_id] = {
                    "state": "skipped_research_dependency",
                    "leaf": {},
                    "observation_ids": [],
                    "claim_ids": [],
                    "calculation_ids": [],
                    "conflict_ids": [],
                    "limitation_ids": [],
                    "source_error_ids": [],
                    "workflow_limitation_ids": [skip_limitation["id"]],
                    "brief_ids": [],
                }
                main_brief = next(
                    item for item in forged["briefs"] if item["id"] == view["brief_id"]
                )
                for reference_field in (
                    "observation_ids",
                    "claim_ids",
                    "calculation_ids",
                    "conflict_ids",
                    "limitation_ids",
                    "source_error_ids",
                ):
                    main_brief[reference_field] = deepcopy(view[reference_field])
                old_id, new_id = _rehash_brief(main_brief)
                self.assertEqual(view["brief_id"], old_id)
                view["brief_id"] = new_id

                with self.assertRaisesRegex(
                    ValueError, "workflow dependency state contradicts its fixed plan"
                ):
                    _validate_serialized(forged)


if __name__ == "__main__":
    unittest.main()
