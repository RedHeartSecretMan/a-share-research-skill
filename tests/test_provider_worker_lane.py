from __future__ import annotations

import ast
import json
import os
import signal
from pathlib import Path
import tempfile
import unittest
from typing import TypeVar, cast
from unittest.mock import patch

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.capability_execution import (  # noqa: E402
    BoundedOperationRequest,
    _spawn_attempt,
    compile_capability_execution_plan,
)
from a_share_research.identity_sources import (  # noqa: E402
    UrlLibTransport,
    build_default_identity_operations,
)
from a_share_research.provider_lane import (  # noqa: E402
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLanes,
    ProviderLaneResult,
    ProviderCallAdapter,
)
from a_share_research.research_result import OperationOutcome  # noqa: E402


T = TypeVar("T")


class FixtureInvocationGuard:
    """Deny a leaf grant until the child's offline transport is registered."""

    def __init__(self, invocation: ProviderInvocation, marker: Path) -> None:
        self._invocation = invocation
        self._marker = marker

    def call(self, adapter: ProviderCallAdapter[T]) -> T:
        if not self._marker.is_file():
            raise RuntimeError("The offline worker fixture is not registered.")
        return self._invocation.call(adapter)


class ProviderWorkerLaneTests(unittest.TestCase):
    def test_fixed_production_worker_authorizes_each_http_leaf_with_parent_budget(
        self,
    ) -> None:
        operations = build_default_identity_operations(UrlLibTransport())
        plan = compile_capability_execution_plan(
            version="1.0", completeness_mode="standard", operations=operations
        )
        operation = plan.operations[0]
        request = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )
        fixture = (
            Path(__file__).parent / "fixtures" / "identity_sources" / "sse_600519.json"
        )
        body = fixture.read_bytes()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            calls = root / "calls.jsonl"
            marker = root / "fixture-ready"
            (root / "sitecustomize.py").write_text(
                "\n".join(
                    (
                        "from email.message import Message",
                        "from io import BytesIO",
                        "import json, os, time, urllib.request",
                        f"calls = {str(calls)!r}",
                        f"body = {body!r}",
                        f"marker = {str(marker)!r}",
                        "class Response(BytesIO):",
                        "    status = 200",
                        "    def __init__(self):",
                        "        super().__init__(body)",
                        "        self.headers = Message()",
                        '        self.headers["Content-Type"] = "application/json"',
                        "class Opener:",
                        "    def __init__(self, handlers):",
                        "        self.redirect = handlers[0] if handlers else urllib.request.HTTPRedirectHandler()",
                        "        self.redirect.parent = self",
                        "    def open(self, request, timeout):",
                        '        with open(calls, "a", encoding="utf-8") as target: target.write(json.dumps({"pid": os.getpid(), "url": request.full_url}) + "\\n")',
                        '        if "query.sse.com.cn" in request.full_url:',
                        "            headers = Message()",
                        '            headers["Location"] = "https://example.test/redirect"',
                        "            request.timeout = timeout",
                        '            return self.redirect.http_error_302(request, BytesIO(b""), 302, "Found", headers)',
                        "        return Response()",
                        "urllib.request.build_opener = lambda *handlers: Opener(handlers)",
                        "with open(marker, 'w', encoding='utf-8') as target: target.write('registered')",
                    )
                ),
                encoding="utf-8",
            )

            ast.parse((root / "sitecustomize.py").read_text())

            class FixedWorkerAdapter:
                def execute(self, invocation: ProviderInvocation) -> OperationOutcome:
                    return _spawn_attempt(
                        operation,
                        request,
                        5,
                        False,
                        provider_invocation=cast(
                            ProviderInvocation,
                            FixtureInvocationGuard(invocation, marker),
                        ),
                    )

            with patch.dict(os.environ, {"PYTHONPATH": str(root)}):
                result = ProviderLanes().run(
                    provider_group="sse",
                    adapter=FixedWorkerAdapter(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=1, timeout_seconds=5
                    ),
                )
            observed = [json.loads(line) for line in calls.read_text().splitlines()]

        self.assertEqual(len(observed), 1)
        self.assertNotEqual(observed[0]["pid"], os.getpid())
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.quiescence.state, "quiescent")
        self.assertIsNotNone(result.value)
        assert result.value is not None
        self.assertEqual(
            result.value.source_errors[0]["code"], "actual_attempt_budget_exhausted"
        )

    def _single_worker_probe(
        self,
        *,
        crash: bool,
        budget: int,
        delay_seconds: float = 0.0,
        call_timeout_seconds: float | None = None,
    ) -> tuple[ProviderLaneResult[OperationOutcome], list[dict[str, object]]]:
        operation = compile_capability_execution_plan(
            version="1.0",
            completeness_mode="standard",
            operations=build_default_identity_operations(UrlLibTransport()),
        ).operations[0]
        request = BoundedOperationRequest(
            "security_identity", {"query": "600519", "as_of": "2026-08-12"}
        )
        body = (
            Path(__file__).parent / "fixtures" / "identity_sources" / "sse_600519.json"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            calls = root / "calls.jsonl"
            marker = root / "fixture-ready"
            (root / "sitecustomize.py").write_text(
                "\n".join(
                    (
                        "from email.message import Message",
                        "from io import BytesIO",
                        "from urllib.error import HTTPError",
                        "import json, os, time, urllib.request",
                        f"calls = {str(calls)!r}",
                        f"body = {body!r}",
                        f"marker = {str(marker)!r}",
                        f"crash = {crash!r}",
                        f"delay_seconds = {delay_seconds!r}",
                        "class Response(BytesIO):",
                        "    status = 200",
                        "    def __init__(self):",
                        "        super().__init__(body)",
                        "        self.headers = Message()",
                        "        self.headers['Content-Type'] = 'application/json'",
                        "class Opener:",
                        "    def open(self, request, timeout):",
                        "        with open(calls, 'a', encoding='utf-8') as target: target.write(json.dumps({'pid': os.getpid(), 'method': request.get_method()}) + '\\n')",
                        "        time.sleep(delay_seconds)",
                        "        if crash: os._exit(7)",
                        "        if len(open(calls, encoding='utf-8').readlines()) == 1: raise HTTPError(request.full_url, 429, 'rate limited', Message(), BytesIO())",
                        "        return Response()",
                        "urllib.request.build_opener = lambda *handlers: Opener()",
                        "with open(marker, 'w', encoding='utf-8') as target: target.write('registered')",
                    )
                ),
                encoding="utf-8",
            )

            ast.parse((root / "sitecustomize.py").read_text())

            class FixedWorkerAdapter:
                def execute(self, invocation: ProviderInvocation) -> OperationOutcome:
                    return _spawn_attempt(
                        operation,
                        request,
                        5,
                        False,
                        provider_invocation=cast(
                            ProviderInvocation,
                            FixtureInvocationGuard(invocation, marker),
                        ),
                    )

            with patch.dict(os.environ, {"PYTHONPATH": str(root)}):
                result = ProviderLanes().run(
                    provider_group="sse-probe",
                    adapter=FixedWorkerAdapter(),
                    policy=ProviderLanePolicy(
                        actual_attempt_budget=budget,
                        timeout_seconds=5,
                        rate_limit_backoffs=(0,),
                        call_timeout_seconds=call_timeout_seconds,
                        cancellation_grace_seconds=0.5,
                    ),
                )
            observed = [json.loads(line) for line in calls.read_text().splitlines()]
        return result, observed

    def test_worker_internal_rate_retry_is_authorized_twice_by_the_parent(self) -> None:
        result, observed = self._single_worker_probe(crash=False, budget=2)
        self.assertEqual(len(observed), 2)
        self.assertEqual(
            [item.state for item in result.attempts], ["failed", "succeeded"]
        )
        self.assertEqual(result.quiescence.state, "quiescent")
        self.assertIsNotNone(result.value)
        assert result.value is not None
        self.assertEqual(result.value.execution.state, "succeeded")

    def test_missing_worker_terminal_receipt_never_erases_the_charged_attempt(
        self,
    ) -> None:
        result, observed = self._single_worker_probe(crash=True, budget=1)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].state, "failed")
        self.assertEqual(result.quiescence.state, "quiescent")
        self.assertIsNotNone(result.value)
        assert result.value is not None
        self.assertEqual(
            result.value.source_errors[0]["code"], "operation_isolation_failed"
        )
        self.assertNotIn("SECRET", repr(result))

    def test_worker_timeout_releases_group_only_after_native_process_exit(self) -> None:
        result, observed = self._single_worker_probe(
            crash=False, budget=1, delay_seconds=2, call_timeout_seconds=0.2
        )
        try:
            self.assertEqual(len(observed), 1)
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.status, "timed_out")
            self.assertEqual(result.quiescence.state, "quiescent")
            self.assertEqual(result.attempts[0].state, "failed")
        finally:
            if result.quiescence.state == "poisoned":
                # Negative controls retain this known test child; terminate it
                # explicitly instead of leaving a detached fixture process.
                for entry in observed:
                    try:
                        os.kill(entry["pid"], signal.SIGTERM)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
