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
from typing import Any

from tests.test_single_security_research import (
    BOOTSTRAP,
    FIXTURES,
    SKILL_ROOT,
    single_security_request,
    validate_serialized,
)

from a_share_research.research_result import (  # noqa: E402
    DirectTaskPlan,
    EvidenceUsePolicy,
    FieldDisposition,
    OperationExecution,
    OperationObligation,
    OperationOutcome,
    ResearchDependency,
    fold_direct_task_research_result,
)


def error_result() -> dict[str, Any]:
    errors = (
        {"code": "upstream_unavailable", "message": "Source unavailable."},
        {"code": "permission_denied", "message": "Source unavailable."},
        {"code": "upstream_unavailable", "message": "Another public message."},
    )
    outcomes = tuple(
        OperationOutcome(
            capability_id="capital_event_observations",
            operation_id=owner,
            execution=OperationExecution("failed"),
            source_errors=tuple(
                {"source_operation": owner, **error} for error in (*errors, errors[0])
            ),
            field_dispositions=(
                FieldDisposition("payload", "normalized", "observations[].payload"),
            ),
            diagnostics={"source_failure_count": 4},
        )
        for owner in ("first_source@1", "second_source@1")
    )
    return fold_direct_task_research_result(
        DirectTaskPlan(
            task_type="canonical_error_contract",
            as_of="2026-08-02",
            obligations=tuple(
                OperationObligation(
                    capability_id=outcome.capability_id,
                    operation_id=outcome.operation_id,
                    source_role="market_observation",
                    experimental=True,
                    exchange=None,
                    evidence_uses=(
                        EvidenceUsePolicy("capital_activity", "supporting"),
                    ),
                )
                for outcome in outcomes
            ),
            dependencies=(
                ResearchDependency("capital_activity", "capital_activity", 1, True),
            ),
        ),
        outcomes,
        summary="Public canonical error folding regression.",
    ).to_dict()


def invoke_content_cli(
    request: dict[str, Any], scenario: str, *, copied: bool
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installed = root / "installed" / "a-share-research" if copied else SKILL_ROOT
        if copied:
            shutil.copytree(SKILL_ROOT, installed)
        shutil.copytree(FIXTURES, root / "fixtures")
        shutil.copy2(BOOTSTRAP, root / "sitecustomize.py")
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(installed / "scripts" / "entrypoint.py"),
                "run",
                "--request",
                str(request_path),
            ],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "A_SHARE_RESEARCH_INSTALLED_SCRIPTS": str(installed / "scripts"),
                "A_SHARE_RESEARCH_FIXTURES": str(root / "fixtures"),
                "A_SHARE_RESEARCH_TEST_SCENARIO": "single_security_history",
                "A_SHARE_SINGLE_SECURITY_SCENARIO": scenario,
            },
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode or completed.stderr:
        raise AssertionError(completed.stderr)
    result = json.loads(completed.stdout)
    validate_serialized(result)
    return result


def content_request(material_type: str, **parameters: object) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_content",
        "subjects": [{"clue": "工业富联"}],
        "as_of": "2026-08-02",
        "window": {"published_from": "2026-07-01", "published_to": "2026-08-02"},
        "parameters": {
            "material_types": [material_type],
            "verify_documents": False,
            **parameters,
        },
        "source_policy": {
            "allow_experimental": True,
            "allow_credentials": False,
            "allow_fallback": False,
        },
    }


class ContentErrorFoldingTests(unittest.TestCase):
    def test_public_fold_keeps_distinct_payloads_owners_order_and_failure_count(
        self,
    ) -> None:
        result = error_result()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["source_errors"]), 6)
        for owner in ("first_source@1", "second_source@1"):
            errors = [
                item
                for item in result["source_errors"]
                if item["operation_id"] == owner
            ]
            self.assertEqual(
                [item["code"] for item in errors],
                [
                    "upstream_unavailable",
                    "permission_denied",
                    "upstream_unavailable",
                ],
            )
            self.assertEqual(
                [item["message"] for item in errors],
                [
                    "Source unavailable.",
                    "Source unavailable.",
                    "Another public message.",
                ],
            )
            operation = result["coverage"]["capital_event_observations"]["operations"][
                owner
            ]
            self.assertEqual(
                operation["source_error_ids"], [item["id"] for item in errors]
            )
            self.assertEqual(operation["diagnostics"], {"source_failure_count": 4})
            self.assertEqual(operation["execution_state"], "failed")
        validate_serialized(result)

    def test_public_capital_consumer_keeps_distinct_public_details(self) -> None:
        from a_share_research.capital_contract import (
            CapitalSourceBatch,
            CapitalSourceFailure,
        )
        from tests.test_capital_events import (
            BluefocusIdentityTransport,
            build_capital_events_result,
            capital_request,
        )

        class FailingCapitalOperation:
            operation_id = "details_source@1"
            supported_data_types = frozenset({"stock_fund_flow", "dragon_tiger"})
            raw_fields = ("result.data[]", "TRADE_DATE", "NET_DEAL_AMT")

            def collect(self, query):
                return CapitalSourceBatch(
                    operation_id=self.operation_id,
                    source_errors=tuple(
                        CapitalSourceFailure(
                            self.operation_id,
                            "upstream_unavailable",
                            "Source unavailable.",
                            {"data_type": data_type},
                        )
                        for data_type in (
                            "stock_fund_flow",
                            "dragon_tiger",
                            "stock_fund_flow",
                        )
                    ),
                    complete=False,
                )

        result = build_capital_events_result(
            capital_request(
                ["stock_fund_flow", "dragon_tiger"], subjects=[{"clue": "蓝色光标"}]
            ),
            [FailingCapitalOperation()],
            BluefocusIdentityTransport(),
        )
        errors = [
            item
            for item in result["source_errors"]
            if item["operation_id"] == "details_source@1"
        ]
        self.assertEqual(
            [item["details"] for item in errors],
            [
                {"data_type": "stock_fund_flow"},
                {"data_type": "dragon_tiger"},
            ],
        )
        operation = result["coverage"]["capital_event_observations"]["operations"][
            "details_source@1"
        ]
        self.assertEqual(operation["source_error_ids"], [item["id"] for item in errors])
        validate_serialized(result)

    def test_external_duplicate_foreign_orphan_and_payload_drift_still_fail_closed(
        self,
    ) -> None:
        original = error_result()
        for mutation in (
            "duplicate-id",
            "foreign-reference",
            "orphan",
            "stable-id-drift",
            "private-details",
        ):
            with self.subTest(mutation=mutation):
                result = deepcopy(original)
                errors = result["source_errors"]
                operation = result["coverage"]["capital_event_observations"][
                    "operations"
                ]["first_source@1"]
                if mutation == "duplicate-id":
                    errors.append(deepcopy(errors[0]))
                elif mutation == "foreign-reference":
                    operation["source_error_ids"].append(errors[3]["id"])
                elif mutation == "orphan":
                    operation["source_error_ids"].pop(0)
                elif mutation == "stable-id-drift":
                    errors[0]["message"] = "Changed public message."
                else:
                    errors[0]["details"] = {"category": "private-context"}
                with self.assertRaises(ValueError):
                    validate_serialized(result)

    def test_public_f10_equivalent_category_failures_return_one_error(self) -> None:
        for copied in (False, True):
            with self.subTest(copied=copied):
                result = invoke_content_cli(
                    content_request(
                        "issuer_profile", profile_categories=["公司概况", "股本结构"]
                    ),
                    "f10_exception",
                    copied=copied,
                )
                errors = [
                    item
                    for item in result["source_errors"]
                    if item["operation_id"] == "mootdx_f10@1"
                ]
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["code"], "upstream_unavailable")
                self.assertNotIn("details", errors[0])
                operation = result["coverage"]["research_content_materials"][
                    "operations"
                ]["mootdx_f10@1"]
                self.assertEqual(operation["source_error_ids"], [errors[0]["id"]])
                self.assertEqual(operation["execution_state"], "failed")
                self.assertIsNone(operation["query_complete"])
                self.assertEqual(result["status"], "blocked")

    def test_public_f10_controls_preserve_distinct_errors_and_materials(self) -> None:
        cases = (
            ("f10_exception", ["公司概况"], True, ["upstream_unavailable"], 0),
            ("f10_empty", ["公司概况", "股本结构"], True, ["empty_response"], 0),
            (
                "f10_distinct",
                ["公司概况", "股本结构"],
                True,
                ["upstream_unavailable", "empty_response"],
                0,
            ),
            (
                "f10_exception",
                ["公司概况", "股本结构"],
                False,
                ["upstream_unavailable"],
                0,
            ),
            ("f10_mixed", ["公司概况", "股本结构"], False, ["upstream_unavailable"], 1),
        )
        for copied in (False, True):
            for scenario, categories, verify, codes, count in cases:
                with self.subTest(copied=copied, scenario=scenario, verify=verify):
                    result = invoke_content_cli(
                        content_request(
                            "issuer_profile",
                            profile_categories=categories,
                            verify_documents=verify,
                        ),
                        scenario,
                        copied=copied,
                    )
                    errors = [
                        item
                        for item in result["source_errors"]
                        if item["operation_id"] == "mootdx_f10@1"
                    ]
                    self.assertEqual([item["code"] for item in errors], codes)
                    operation = result["coverage"]["research_content_materials"][
                        "operations"
                    ]["mootdx_f10@1"]
                    self.assertEqual(
                        operation["source_error_ids"], [item["id"] for item in errors]
                    )
                    self.assertEqual(operation["execution_state"], "failed")
                    self.assertEqual(len(operation["observation_ids"]), count)
                    self.assertEqual(
                        operation["state"], "partial" if count else "failed"
                    )
                    self.assertEqual(
                        result["status"], "limited" if count else "blocked"
                    )

    def test_public_qa_questions_keep_materials_and_one_canonical_error(self) -> None:
        for copied in (False, True):
            for scenario, count in (("qa_one", 1), ("qa_two", 2), ("qa_mixed", 3)):
                with self.subTest(copied=copied, scenario=scenario):
                    result = invoke_content_cli(
                        content_request("investor_qa"), scenario, copied=copied
                    )
                    self.assertEqual(result["status"], "limited")
                    errors = [
                        item
                        for item in result["source_errors"]
                        if item["operation_id"] == "cninfo_investor_qa@1"
                    ]
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(
                        errors[0]["code"], "answer_publication_time_missing"
                    )
                    self.assertNotIn("details", errors[0])
                    operation = result["coverage"]["research_content_materials"][
                        "operations"
                    ]["cninfo_investor_qa@1"]
                    self.assertEqual(operation["source_error_ids"], [errors[0]["id"]])
                    self.assertEqual(operation["execution_state"], "failed")
                    self.assertEqual(operation["state"], "partial")
                    self.assertEqual(len(operation["observation_ids"]), count)

    def test_complete_single_security_qa_consumer_passes_inventory_and_workflow(
        self,
    ) -> None:
        for copied in (False, True):
            for scenario, count in (("qa_one", 1), ("qa_two", 2), ("qa_mixed", 3)):
                with self.subTest(copied=copied, scenario=scenario):
                    result = invoke_content_cli(
                        single_security_request(), scenario, copied=copied
                    )
                    self.assertEqual(result["status"], "limited")
                    self.assertEqual(
                        set(result["coverage"]["steps"]),
                        {
                            "identity",
                            "market_trend",
                            "valuation",
                            "materials",
                            "capital_activity",
                            "lockup_events",
                            "market_context",
                        },
                    )
                    leaf = result["coverage"]["steps"]["materials"]
                    self.assertEqual(leaf["state"], "complete")
                    errors = [
                        item
                        for item in result["source_errors"]
                        if item["operation_id"] == "cninfo_investor_qa@1"
                    ]
                    self.assertEqual(len(errors), 1)
                    operation = leaf["leaf"]["research_content_materials"][
                        "operations"
                    ]["cninfo_investor_qa@1"]
                    self.assertEqual(operation["source_error_ids"], [errors[0]["id"]])
                    self.assertEqual(operation["execution_state"], "failed")
                    self.assertEqual(operation["state"], "partial")
                    self.assertEqual(len(operation["observation_ids"]), count)


if __name__ == "__main__":
    unittest.main()
