from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

import a_share_research.workflow_contracts as workflow_contracts_module  # noqa: E402
from a_share_research.bounded_public_data import (  # noqa: E402
    MAX_PUBLIC_DATA_DEPTH,
    MAX_PUBLIC_DATA_ITEMS,
)
from a_share_research.workflow_contracts import (  # noqa: E402
    REGISTERED_WORKFLOW_VERSIONS,
    V040_WORKFLOW_VERSIONS,
    validate_registered_workflow_request,
    validate_v040_workflow_public_scope,
    validate_v040_workflow_request,
)

POLICY = {
    "allow_experimental": False,
    "allow_credentials": False,
    "allow_fallback": True,
}
ARTIFACT_PATH = str(Path.cwd() / ".test-artifacts" / "a-share-screen-2026-08-21")
REINSERTED_ARTIFACT_PATH = str(
    Path.cwd() / ".test-artifacts" / "reinserted-screen-result"
)
TRAVERSAL_ARTIFACT_PATH = str(
    Path.cwd() / ".test-artifacts" / ".." / "escaped-screen-result"
)
ROOT_ARTIFACT_PATH = str(Path(Path.cwd().anchor))
_OVERVIEW_STRUCTURAL_PROBE_FIXED_ITEMS = 16


class _StringCollidingKey:
    def __init__(self, value: str) -> None:
        self._value = value

    def __hash__(self) -> int:
        return hash(self._value)

    def __eq__(self, other: object) -> bool:
        return other == self._value

    def __str__(self) -> str:
        return self._value


class _CollidingWorkflowIdentity(Mapping[object, object]):
    def __init__(self) -> None:
        self._colliding_id = _StringCollidingKey("id")

    def __getitem__(self, key: object) -> object:
        if key is self._colliding_id:
            return "forged"
        if key == "id":
            return "market_overview_research"
        if key == "version":
            return "2.0"
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return iter(("id", self._colliding_id, "version"))

    def __len__(self) -> int:
        return 3


class _DuplicateWorkflowIdentity(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        if key == "id":
            return "market_overview_research"
        if key == "version":
            return "2.0"
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("id", "id", "version"))

    def __len__(self) -> int:
        return 3


class _ValidationThenCopyInputs(Mapping[str, object]):
    def __init__(self) -> None:
        self._iterations = 0

    def __getitem__(self, key: str) -> object:
        if key != "completeness_mode":
            raise KeyError(key)
        if self._iterations >= 6:
            return {"mutable"}
        return "standard"

    def __iter__(self) -> Iterator[str]:
        self._iterations += 1
        return iter(("completeness_mode",))

    def __len__(self) -> int:
        return 1


class _AliasedWorkflowAndPlan(Mapping[str, object]):
    def __init__(self) -> None:
        self.iterations = 0

    def __getitem__(self, key: str) -> object:
        if key == "id":
            return (
                "market_screen_research"
                if self.iterations == 1
                else "fundamental_research_priority"
            )
        if key == "version":
            return "1.0"
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        return iter(("id", "version"))

    def __len__(self) -> int:
        return 2


class _DisguisedString(str):
    def __new__(cls, serialized: str, accepted_as: str) -> _DisguisedString:
        value = super().__new__(cls, serialized)
        value._accepted_as = accepted_as
        return value

    def __hash__(self) -> int:
        return hash(self._accepted_as)

    def __eq__(self, other: object) -> bool:
        return other == self._accepted_as

    def __ne__(self, other: object) -> bool:
        return not self == other


class _DisguisedInt(int):
    def __new__(cls) -> _DisguisedInt:
        return super().__new__(cls, 999)

    def __ge__(self, other: object) -> bool:
        return True

    def __le__(self, other: object) -> bool:
        return True


class _DisguisedFloat(float):
    def __new__(cls) -> _DisguisedFloat:
        return super().__new__(cls, 1.5)

    def __hash__(self) -> int:
        return hash("market_wide_intraday")

    def __eq__(self, other: object) -> bool:
        return other == "market_wide_intraday"

    def __ne__(self, other: object) -> bool:
        return not self == other


def workflow_request(
    workflow_id: str,
    version: str,
    *,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": [],
        "as_of": "2026-08-21",
        "window": None,
        "parameters": {
            "workflow": {"id": workflow_id, "version": version},
            "inputs": inputs,
        },
        "source_policy": copy.deepcopy(POLICY),
    }


class WorkflowContractTests(unittest.TestCase):
    def test_public_scope_validation_exposes_only_fixed_registry_authority(
        self,
    ) -> None:
        public_names = set(workflow_contracts_module.__all__)

        self.assertNotIn("validate_workflow_public_scope", public_names)
        self.assertFalse(
            hasattr(workflow_contracts_module, "validate_workflow_public_scope")
        )
        self.assertIn("validate_registered_workflow_public_scope", public_names)
        self.assertIn("validate_v040_workflow_public_scope", public_names)

        valid_scope = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
            },
        )
        validated = validate_v040_workflow_public_scope(valid_scope)
        self.assertEqual(
            (validated.workflow_id, validated.workflow_version),
            (
                valid_scope["parameters"]["workflow"]["id"],
                valid_scope["parameters"]["workflow"]["version"],
            ),
        )

        forged_scope = copy.deepcopy(valid_scope)
        forged_scope["parameters"]["workflow"]["id"] = "forged"
        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow id is not supported$",
        ):
            validate_v040_workflow_public_scope(forged_scope)

    def test_trusted_workflow_values_have_no_public_constructors(self) -> None:
        public_names = set(workflow_contracts_module.__all__)

        for name in (
            "ResultArtifactConfiguration",
            "ValidatedWorkflowRequest",
            "WorkflowContract",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, public_names)
                self.assertFalse(hasattr(workflow_contracts_module, name))

    def test_non_string_and_colliding_mapping_keys_fail_before_trusted_state(
        self,
    ) -> None:
        non_string_key = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"completeness_mode": "standard"},
        )
        non_string_key["source_policy"][1] = False

        colliding_key = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"completeness_mode": "standard"},
        )
        colliding_key["parameters"]["workflow"] = _CollidingWorkflowIdentity()

        for request in (non_string_key, colliding_key):
            with self.subTest(request=request):
                with self.assertRaisesRegex(
                    ValueError,
                    "^research_workflow mapping keys must be strings$",
                ):
                    validate_v040_workflow_request(request)

    def test_duplicate_string_mapping_keys_fail_before_trusted_state(self) -> None:
        request = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"completeness_mode": "standard"},
        )
        request["parameters"]["workflow"] = _DuplicateWorkflowIdentity()

        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow mapping keys must be unique$",
        ):
            validate_v040_workflow_request(request)

    def test_stateful_mapping_cannot_mutate_between_validation_and_copy(self) -> None:
        request = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"completeness_mode": "standard"},
        )
        request["parameters"]["inputs"] = _ValidationThenCopyInputs()

        validated = validate_v040_workflow_request(request)

        self.assertEqual(validated.inputs["completeness_mode"], "standard")
        with self.assertRaises(TypeError):
            validated.inputs["completeness_mode"] = "fast"

    def test_aliased_stateful_mapping_is_snapshotted_once_before_validation(
        self,
    ) -> None:
        aliased_identity = _AliasedWorkflowAndPlan()
        request = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": aliased_identity,
                "completeness_mode": "fast",
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )
        request["parameters"]["workflow"] = aliased_identity

        with self.assertRaisesRegex(
            ValueError,
            "^market_screen_research screening_plan is unsupported$",
        ):
            validate_v040_workflow_request(request)

        self.assertEqual(aliased_identity.iterations, 1)

    def test_cyclic_mapping_and_list_fail_closed_before_trusted_state(self) -> None:
        cyclic_inputs: dict[str, Any] = {"completeness_mode": "standard"}
        cyclic_inputs["cycle"] = cyclic_inputs
        mapping_request = workflow_request(
            "market_overview_research",
            "2.0",
            inputs=cyclic_inputs,
        )

        cyclic_extensions: list[Any] = ["market_wide_intraday"]
        cyclic_extensions.append(cyclic_extensions)
        list_request = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"deep_extensions": cyclic_extensions},
        )

        for container_kind, request in (
            ("mapping", mapping_request),
            ("list", list_request),
        ):
            with self.subTest(container_kind=container_kind):
                with self.assertRaisesRegex(
                    ValueError,
                    "^research_workflow request contains a cyclic container$",
                ):
                    validate_v040_workflow_request(request)

    def test_deep_acyclic_request_fails_before_interpreter_recursion(self) -> None:
        nested: object = None
        for _ in range(sys.getrecursionlimit() + MAX_PUBLIC_DATA_DEPTH):
            nested = [nested]
        request = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"structural_probe": nested},
        )

        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow request exceeds its structural bounds$",
        ):
            validate_v040_workflow_request(request)

    def test_public_snapshot_depth_limit_is_inclusive(self) -> None:
        requests: dict[str, dict[str, Any]] = {}
        for name, deepest_depth in (
            ("at_limit", MAX_PUBLIC_DATA_DEPTH),
            ("one_over", MAX_PUBLIC_DATA_DEPTH + 1),
        ):
            nested: object = None
            # Root -> parameters -> inputs -> structural_probe occupies depth 3.
            for _ in range(deepest_depth - 3):
                nested = [nested]
            requests[name] = workflow_request(
                "market_overview_research",
                "2.0",
                inputs={"structural_probe": nested},
            )

        with self.assertRaisesRegex(
            ValueError,
            "^market_overview_research inputs do not match the workflow contract$",
        ):
            validate_v040_workflow_request(requests["at_limit"])
        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow request exceeds its structural bounds$",
        ):
            validate_v040_workflow_request(requests["one_over"])

    def test_public_contract_matrix_is_recursion_budget_independent(
        self,
    ) -> None:
        skill_scripts = Path(workflow_contracts_module.__file__).resolve().parents[1]
        probe = """
import copy
import json
import sys

sys.path.insert(0, sys.argv[1])

from a_share_research.workflow_contracts import validate_v040_workflow_request


def workflow_request(workflow_id, version, inputs):
    return {
        "schema_version": "1.0",
        "task_type": "research_workflow",
        "subjects": [],
        "as_of": "2026-08-21",
        "window": None,
        "parameters": {
            "workflow": {
                "id": workflow_id,
                "version": version,
            },
            "inputs": inputs,
        },
        "source_policy": {
            "allow_experimental": False,
            "allow_credentials": False,
            "allow_fallback": True,
        },
    }


def nested_list(relative_depth):
    nested = None
    for _ in range(relative_depth):
        nested = [nested]
    return nested


def request_with_depth(deepest_depth):
    # Root -> parameters -> inputs -> structural_probe occupies depth 3.
    return workflow_request(
        "market_overview_research",
        "2.0",
        {"structural_probe": nested_list(deepest_depth - 3)},
    )


def error_for(request):
    try:
        validate_v040_workflow_request(request)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return None


def valid_state_check():
    request = workflow_request(
        "market_overview_research",
        "2.0",
        {
            "completeness_mode": "standard",
            "deep_extensions": ["market_wide_intraday"],
        },
    )
    expected_request = copy.deepcopy(request)
    expected_inputs = expected_request["parameters"]["inputs"]
    validated = validate_v040_workflow_request(request)
    validated_inputs = validated.inputs_copy()
    validated_public_request = validated.public_request_copy()

    mapping_is_immutable = False
    sequence_is_immutable = False
    try:
        validated.public_request["parameters"]["workflow"]["version"] = "9.0"
    except TypeError:
        mapping_is_immutable = True
    try:
        validated.inputs["deep_extensions"].append("market_wide_intraday")
    except (AttributeError, TypeError):
        sequence_is_immutable = True

    inputs_a = validated.inputs_copy()
    inputs_b = validated.inputs_copy()
    public_a = validated.public_request_copy()
    public_b = validated.public_request_copy()
    fresh = (
        inputs_a is not inputs_b
        and inputs_a["deep_extensions"] is not inputs_b["deep_extensions"]
        and public_a is not public_b
        and public_a["parameters"] is not public_b["parameters"]
        and public_a["parameters"]["inputs"]["deep_extensions"]
        is not public_b["parameters"]["inputs"]["deep_extensions"]
    )

    inputs_a["deep_extensions"].clear()
    public_a["parameters"]["workflow"]["version"] = "9.0"
    public_a["parameters"]["inputs"]["deep_extensions"].append(
        "market_wide_intraday"
    )
    isolated = (
        request == expected_request
        and validated.inputs_copy() == expected_inputs
        and validated.public_request_copy() == expected_request
        and inputs_b == expected_inputs
        and public_b == request
        and inputs_a["deep_extensions"] == []
        and public_a["parameters"]["workflow"]["version"] == "9.0"
        and len(public_a["parameters"]["inputs"]["deep_extensions"]) == 2
    )
    return {
        "content": (
            request == expected_request
            and validated_inputs == expected_inputs
            and validated_public_request == expected_request
        ),
        "immutable": mapping_is_immutable and sequence_is_immutable,
        "fresh": fresh,
        "isolated": isolated,
    }


sys.setrecursionlimit(int(sys.argv[2]))

ordinary_shape_error = (
    "ValueError: market_overview_research inputs do not match the workflow contract"
)
structural_bound_error = (
    "ValueError: research_workflow request exceeds its structural bounds"
)

outcomes = {"cleanup": {}}
outcomes["depth"] = {
    "at_limit": error_for(request_with_depth(32)),
    "one_over": error_for(request_with_depth(33)),
}
outcomes["cleanup"]["depth"] = valid_state_check()

# The empty-probe envelope is 16 logical items. Each shared subtree is
# 4,990 items: its list root plus 4,989 scalar values.
shared_at_limit = [None for _ in range(4_989)]
item_at_limit = workflow_request(
    "market_overview_research",
    "2.0",
    {
        "structural_probe": [
            shared_at_limit,
            shared_at_limit,
            None,
            None,
            None,
            None,
        ]
    },
)
shared_one_over = [None for _ in range(4_989)]
item_one_over = workflow_request(
    "market_overview_research",
    "2.0",
    {
        "structural_probe": [
            shared_one_over,
            shared_one_over,
            None,
            None,
            None,
            None,
            None,
        ]
    },
)
outcomes["aliased_items"] = {
    "at_limit": error_for(item_at_limit),
    "one_over": error_for(item_one_over),
}
outcomes["cleanup"]["aliased_items"] = valid_state_check()

shared_at_limit = nested_list(16)
placed_at_limit = shared_at_limit
for _ in range(12):
    placed_at_limit = [placed_at_limit]
alias_depth_at_limit = workflow_request(
    "market_overview_research",
    "2.0",
    {"structural_probe": [shared_at_limit, placed_at_limit]},
)
shared_one_over = nested_list(16)
placed_one_over = shared_one_over
for _ in range(13):
    placed_one_over = [placed_one_over]
alias_depth_one_over = workflow_request(
    "market_overview_research",
    "2.0",
    {"structural_probe": [shared_one_over, placed_one_over]},
)
outcomes["aliased_depth"] = {
    "at_limit": error_for(alias_depth_at_limit),
    "one_over": error_for(alias_depth_one_over),
}
outcomes["cleanup"]["aliased_depth"] = valid_state_check()

# The first occurrence is completed and memoized. Traversing the second
# container hits that alias before reaching its active self-cycle.
completed_alias = []
active_cycle = [completed_alias]
active_cycle.append(active_cycle)
cycle_request = workflow_request(
    "market_overview_research",
    "2.0",
    {"structural_probe": [completed_alias, active_cycle]},
)
outcomes["cycle"] = error_for(cycle_request)
outcomes["cleanup"]["cycle"] = valid_state_check()
outcomes["trusted_state"] = valid_state_check()

assert outcomes["depth"] == {
    "at_limit": ordinary_shape_error,
    "one_over": structural_bound_error,
}
assert outcomes["aliased_items"] == {
    "at_limit": ordinary_shape_error,
    "one_over": structural_bound_error,
}
assert outcomes["aliased_depth"] == {
    "at_limit": ordinary_shape_error,
    "one_over": structural_bound_error,
}
print(json.dumps(outcomes, sort_keys=True))
"""
        valid_state = {
            "content": True,
            "fresh": True,
            "immutable": True,
            "isolated": True,
        }
        expected = {
            "aliased_depth": {
                "at_limit": (
                    "ValueError: market_overview_research inputs do not match "
                    "the workflow contract"
                ),
                "one_over": (
                    "ValueError: research_workflow request exceeds its structural "
                    "bounds"
                ),
            },
            "aliased_items": {
                "at_limit": (
                    "ValueError: market_overview_research inputs do not match "
                    "the workflow contract"
                ),
                "one_over": (
                    "ValueError: research_workflow request exceeds its structural "
                    "bounds"
                ),
            },
            "cleanup": {
                "aliased_depth": valid_state,
                "aliased_items": valid_state,
                "cycle": valid_state,
                "depth": valid_state,
            },
            "cycle": (
                "ValueError: research_workflow request contains a cyclic container"
            ),
            "depth": {
                "at_limit": (
                    "ValueError: market_overview_research inputs do not match "
                    "the workflow contract"
                ),
                "one_over": (
                    "ValueError: research_workflow request exceeds its structural "
                    "bounds"
                ),
            },
            "trusted_state": valid_state,
        }

        for recursion_limit in (60, 80):
            with self.subTest(recursion_limit=recursion_limit):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        probe,
                        str(skill_scripts),
                        str(recursion_limit),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(json.loads(completed.stdout), expected)

    def test_public_snapshot_item_limit_counts_root_containers_and_values(self) -> None:
        # With an empty structural_probe list, this worked envelope contains 16
        # visited values: its root, containers, and scalar values; keys are not items.
        at_limit = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={
                "structural_probe": [
                    None
                    for _ in range(
                        MAX_PUBLIC_DATA_ITEMS - _OVERVIEW_STRUCTURAL_PROBE_FIXED_ITEMS
                    )
                ]
            },
        )
        one_over = copy.deepcopy(at_limit)
        one_over["parameters"]["inputs"]["structural_probe"].append(None)

        with self.assertRaisesRegex(
            ValueError,
            "^market_overview_research inputs do not match the workflow contract$",
        ):
            validate_v040_workflow_request(at_limit)
        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow request exceeds its structural bounds$",
        ):
            validate_v040_workflow_request(one_over)

    def test_public_snapshot_aliases_charge_each_logical_item_occurrence(
        self,
    ) -> None:
        # The empty-probe envelope is 16 items. Each shared subtree is 4,990:
        # its list root plus 4,989 scalar values. Two placements plus four
        # trailing scalars therefore exercise exactly 10,000 logical items.
        shared_at_limit = [None for _ in range(4_989)]
        at_limit = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={
                "structural_probe": [
                    shared_at_limit,
                    shared_at_limit,
                    None,
                    None,
                    None,
                    None,
                ]
            },
        )
        shared_one_over = [None for _ in range(4_989)]
        one_over = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={
                "structural_probe": [
                    shared_one_over,
                    shared_one_over,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "^market_overview_research inputs do not match the workflow contract$",
        ):
            validate_v040_workflow_request(at_limit)
        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow request exceeds its structural bounds$",
        ):
            validate_v040_workflow_request(one_over)

    def test_public_snapshot_aliases_apply_relative_depth_at_each_placement(
        self,
    ) -> None:
        def nested_list(relative_depth: int) -> object:
            nested: object = None
            for _ in range(relative_depth):
                nested = [nested]
            return nested

        shared_at_limit = nested_list(16)
        placed_at_limit = shared_at_limit
        for _ in range(12):
            placed_at_limit = [placed_at_limit]
        at_limit = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"structural_probe": [shared_at_limit, placed_at_limit]},
        )

        shared_one_over = nested_list(16)
        placed_one_over = shared_one_over
        for _ in range(13):
            placed_one_over = [placed_one_over]
        one_over = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"structural_probe": [shared_one_over, placed_one_over]},
        )

        with self.assertRaisesRegex(
            ValueError,
            "^market_overview_research inputs do not match the workflow contract$",
        ):
            validate_v040_workflow_request(at_limit)
        with self.assertRaisesRegex(
            ValueError,
            "^research_workflow request exceeds its structural bounds$",
        ):
            validate_v040_workflow_request(one_over)

    def test_json_primitive_subclasses_fail_before_fixed_contract_checks(
        self,
    ) -> None:
        disguised_string = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "fast",
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )
        disguised_string["parameters"]["workflow"]["id"] = _DisguisedString(
            "serialized-workflow-id",
            "market_screen_research",
        )

        disguised_int = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
                "candidate_limit": _DisguisedInt(),
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )

        disguised_float = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={"deep_extensions": [_DisguisedFloat()]},
        )

        disguised_key = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    _DisguisedString("serialized-key", "id"): _DisguisedString(
                        "serialized-plan-id",
                        "fundamental_research_priority",
                    ),
                    "version": "1.0",
                },
                "completeness_mode": "fast",
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )

        for primitive_kind, request in (
            ("str", disguised_string),
            ("int", disguised_int),
            ("float", disguised_float),
            ("str-key", disguised_key),
        ):
            with self.subTest(primitive_kind=primitive_kind):
                with self.assertRaises(ValueError):
                    validate_v040_workflow_request(request)

    def test_market_overview_v2_contract_accepts_its_final_request_shape(self) -> None:
        request = workflow_request(
            "market_overview_research",
            "2.0",
            inputs={
                "completeness_mode": "standard",
                "deep_extensions": ["market_wide_intraday"],
            },
        )

        validated = validate_v040_workflow_request(request)

        self.assertEqual(validated.workflow_id, "market_overview_research")
        self.assertEqual(validated.workflow_version, "2.0")
        self.assertEqual(validated.public_request_copy(), request)
        self.assertIsNone(validated.result_artifact)

    def test_market_screen_contract_separates_artifact_transport_from_public_request(
        self,
    ) -> None:
        request = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
                "candidate_limit": 5,
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )

        validated = validate_v040_workflow_request(request)

        self.assertEqual(validated.workflow_id, "market_screen_research")
        self.assertEqual(validated.workflow_version, "1.0")
        self.assertEqual(
            validated.inputs,
            {
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
                "candidate_limit": 5,
            },
        )
        expected_public_request = copy.deepcopy(request)
        expected_public_request["parameters"]["inputs"].pop("result_artifact")
        self.assertEqual(validated.public_request_copy(), expected_public_request)
        self.assertIsNotNone(validated.result_artifact)
        self.assertEqual(validated.result_artifact.path, Path(ARTIFACT_PATH))
        self.assertEqual(validated.result_artifact.mode, "create")

    def test_market_screen_public_scope_round_trips_without_artifact_transport(
        self,
    ) -> None:
        request = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )
        ingress = validate_v040_workflow_request(request)

        public_scope = validate_v040_workflow_public_scope(ingress.public_request)

        self.assertEqual(public_scope.workflow_id, "market_screen_research")
        self.assertEqual(public_scope.workflow_version, "1.0")
        self.assertEqual(public_scope.inputs["candidate_limit"], 5)
        self.assertEqual(
            public_scope.public_request_copy(),
            ingress.public_request_copy(),
        )
        self.assertIsNone(public_scope.result_artifact)
        with self.assertRaises(TypeError):
            public_scope.inputs["screening_plan"]["version"] = "9.0"
        with self.assertRaises(TypeError):
            public_scope.public_request["parameters"]["inputs"]["result_artifact"] = {
                "path": REINSERTED_ARTIFACT_PATH,
                "mode": "create",
            }

        transport_scope = ingress.public_request_copy()
        transport_scope["parameters"]["inputs"]["result_artifact"] = {
            "path": REINSERTED_ARTIFACT_PATH,
            "mode": "create",
        }
        with self.assertRaisesRegex(
            ValueError,
            "market_screen_research public inputs do not match the workflow contract",
        ):
            validate_v040_workflow_public_scope(transport_scope)

    def test_market_screen_contract_resolves_bounded_candidate_defaults(self) -> None:
        cases = (("fast", None, 0), ("fast", 0, 0), ("standard", None, 5))
        for mode, requested_limit, expected_limit in cases:
            with self.subTest(mode=mode, requested_limit=requested_limit):
                inputs: dict[str, Any] = {
                    "screening_plan": {
                        "id": "fundamental_research_priority",
                        "version": "1.0",
                    },
                    "completeness_mode": mode,
                    "result_artifact": {
                        "path": ARTIFACT_PATH,
                        "mode": "resume",
                    },
                }
                if requested_limit is not None:
                    inputs["candidate_limit"] = requested_limit

                validated = validate_v040_workflow_request(
                    workflow_request(
                        "market_screen_research",
                        "1.0",
                        inputs=inputs,
                    )
                )

                self.assertEqual(validated.inputs["candidate_limit"], expected_limit)

    def test_validated_contract_data_is_recursively_immutable_and_isolated(
        self,
    ) -> None:
        request = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
                "candidate_limit": 5,
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )
        validated = validate_v040_workflow_request(request)

        with self.assertRaises(TypeError):
            validated.inputs["candidate_limit"] = 20
        with self.assertRaises(TypeError):
            validated.inputs["screening_plan"]["version"] = "9.0"
        with self.assertRaises(TypeError):
            validated.public_request["parameters"]["workflow"]["version"] = "9.0"
        with self.assertRaises(TypeError):
            validated.public_request["parameters"]["inputs"]["result_artifact"] = {
                "path": REINSERTED_ARTIFACT_PATH,
                "mode": "create",
            }

        request["parameters"]["workflow"]["version"] = "9.0"
        request["parameters"]["inputs"]["candidate_limit"] = 20
        self.assertEqual(validated.workflow_version, "1.0")
        self.assertEqual(validated.inputs["candidate_limit"], 5)
        self.assertNotIn(
            "result_artifact",
            validated.public_request["parameters"]["inputs"],
        )

        overview = validate_v040_workflow_request(
            workflow_request(
                "market_overview_research",
                "2.0",
                inputs={"deep_extensions": ["market_wide_intraday"]},
            )
        )
        with self.assertRaises((AttributeError, TypeError)):
            overview.inputs["deep_extensions"].append("market_wide_intraday")

    def test_defined_contracts_reject_unknown_fields(self) -> None:
        base = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "fast",
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )
        requests: list[dict[str, Any]] = []
        unknown_envelope = copy.deepcopy(base)
        unknown_envelope["provider"] = "forbidden"
        requests.append(unknown_envelope)
        unknown_parameters = copy.deepcopy(base)
        unknown_parameters["parameters"]["steps"] = []
        requests.append(unknown_parameters)
        unknown_workflow = copy.deepcopy(base)
        unknown_workflow["parameters"]["workflow"]["fallback_version"] = "1.0"
        requests.append(unknown_workflow)
        unknown_inputs = copy.deepcopy(base)
        unknown_inputs["parameters"]["inputs"]["ranking_formula"] = "caller-owned"
        requests.append(unknown_inputs)
        unknown_artifact = copy.deepcopy(base)
        unknown_artifact["parameters"]["inputs"]["result_artifact"]["overwrite"] = True
        requests.append(unknown_artifact)

        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    validate_v040_workflow_request(request)

    def test_market_screen_rejects_unknown_values_and_unsafe_artifact_paths(
        self,
    ) -> None:
        base = workflow_request(
            "market_screen_research",
            "1.0",
            inputs={
                "screening_plan": {
                    "id": "fundamental_research_priority",
                    "version": "1.0",
                },
                "completeness_mode": "standard",
                "candidate_limit": 5,
                "result_artifact": {
                    "path": ARTIFACT_PATH,
                    "mode": "create",
                },
            },
        )
        invalid_requests: list[dict[str, Any]] = []
        for field, value in (
            ("workflow_version", "2.0"),
            ("plan_id", "caller_formula"),
            ("plan_version", "2.0"),
            ("mode", "overwrite"),
            ("candidate_limit", 21),
            ("candidate_limit", True),
            ("path", "relative/artifact"),
            ("path", TRAVERSAL_ARTIFACT_PATH),
            ("path", ROOT_ARTIFACT_PATH),
        ):
            invalid = copy.deepcopy(base)
            workflow = invalid["parameters"]["workflow"]
            inputs = invalid["parameters"]["inputs"]
            if field == "workflow_version":
                workflow["version"] = value
            elif field == "plan_id":
                inputs["screening_plan"]["id"] = value
            elif field == "plan_version":
                inputs["screening_plan"]["version"] = value
            elif field in {"mode", "path"}:
                inputs["result_artifact"][field] = value
            else:
                inputs[field] = value
            invalid_requests.append(invalid)

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    validate_v040_workflow_request(request)

    def test_registry_separates_current_runtime_from_v040_cutover_contracts(
        self,
    ) -> None:
        self.assertEqual(
            dict(REGISTERED_WORKFLOW_VERSIONS),
            {
                "market_overview_research": "2.0",
                "new_security_research": "1.0",
                "single_security_research": "1.0",
                "single_security_valuation": "1.0",
                "theme_report_research": "1.0",
                "valuation_comparison": "1.0",
            },
        )
        self.assertEqual(
            dict(V040_WORKFLOW_VERSIONS),
            {
                "market_overview_research": "2.0",
                "market_screen_research": "1.0",
                "new_security_research": "1.0",
                "single_security_research": "1.0",
                "single_security_valuation": "1.0",
                "theme_report_research": "1.0",
                "valuation_comparison": "1.0",
            },
        )
        current = workflow_request(
            "single_security_valuation",
            "1.0",
            inputs={"target_pe": "30"},
        )
        current["subjects"] = [{"clue": "工业富联", "issuer_security_class_count": 1}]
        validated = validate_registered_workflow_request(current)
        self.assertEqual(validated.workflow_id, "single_security_valuation")
        self.assertEqual(validated.workflow_version, "1.0")

        for future_request in (
            workflow_request(
                "market_overview_research",
                "1.0",
                inputs={"completeness_mode": "standard"},
            ),
            workflow_request(
                "market_screen_research",
                "1.0",
                inputs={
                    "screening_plan": {
                        "id": "fundamental_research_priority",
                        "version": "1.0",
                    },
                    "completeness_mode": "fast",
                    "result_artifact": {
                        "path": ARTIFACT_PATH,
                        "mode": "create",
                    },
                },
            ),
        ):
            with self.subTest(future_request=future_request):
                with self.assertRaises(ValueError):
                    validate_registered_workflow_request(future_request)

    def test_active_registry_rejects_malformed_future_requests_uniformly(
        self,
    ) -> None:
        malformed_future_requests = (
            (
                workflow_request(
                    "market_overview_research",
                    "2.0",
                    inputs={"completeness_mode": "deep"},
                ),
                "market_overview_research completeness_mode must be standard or fast",
            ),
            (
                workflow_request(
                    "market_screen_research",
                    "1.0",
                    inputs={},
                ),
                "research_workflow id is not supported",
            ),
            (
                workflow_request(
                    "market_screen_research",
                    "1.0",
                    inputs={
                        "screening_plan": {
                            "id": "caller_formula",
                            "version": "9.0",
                        },
                        "completeness_mode": "standard",
                        "candidate_limit": 999,
                        "result_artifact": {
                            "path": "relative/future-screen",
                            "mode": "overwrite",
                        },
                    },
                ),
                "research_workflow id is not supported",
            ),
        )

        for request, expected_error in malformed_future_requests:
            with self.subTest(request=request):
                with self.assertRaisesRegex(ValueError, f"^{expected_error}$"):
                    validate_registered_workflow_request(request)


if __name__ == "__main__":
    unittest.main()
