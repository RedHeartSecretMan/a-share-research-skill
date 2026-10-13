from __future__ import annotations

from email.message import Message
from pathlib import Path
from io import BytesIO
import unittest
from urllib.request import HTTPRedirectHandler, Request
from unittest.mock import patch

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import (  # noqa: E402
    HttpResponse,
    UrlLibTransport,
    IdentityOperationRequest,
    build_default_identity_operations,
)
from a_share_research.provider_lane import (  # noqa: E402
    ProviderInvocation,
    ProviderLanePolicy,
    ProviderLanes,
)


class FixtureResponse(BytesIO):
    status = 200

    def __init__(self) -> None:
        super().__init__(b"{}")
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"


class ProviderTransportLaneTests(unittest.TestCase):
    def test_default_http_redirect_cannot_hide_a_second_leaf_attempt(self) -> None:
        calls: list[str] = []

        class FixtureOpener:
            def __init__(self, handlers: tuple[HTTPRedirectHandler, ...]) -> None:
                self.redirect = handlers[0] if handlers else HTTPRedirectHandler()
                self.redirect.parent = self  # type: ignore[assignment]

            def open(self, request: Request, *, timeout: float) -> FixtureResponse:
                del timeout
                calls.append(request.full_url)
                if request.full_url.endswith("/start"):
                    headers = Message()
                    headers["Location"] = "/end"
                    request.timeout = 1  # type: ignore[attr-defined]
                    return self.redirect.http_error_302(
                        request, BytesIO(b""), 302, "Found", headers
                    )
                return FixtureResponse()

        class OwnedHttpAdapter:
            def execute(self, invocation: ProviderInvocation) -> HttpResponse:
                transport = UrlLibTransport(provider_invocation=invocation)
                return transport.get("https://example.test/start", {})

        with patch(
            "a_share_research.identity_sources.build_opener",
            side_effect=lambda *handlers: FixtureOpener(handlers),
        ):
            result = ProviderLanes().run(
                provider_group="sse",
                adapter=OwnedHttpAdapter(),
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            )

        self.assertEqual(calls, ["https://example.test/start"])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")

    def test_get_bounded_get_and_post_share_the_owned_leaf_budget(self) -> None:
        calls: list[str] = []

        class FixtureOpener:
            def open(self, request: Request, *, timeout: float) -> FixtureResponse:
                calls.append(request.get_method())
                return FixtureResponse()

        class OwnedHttpAdapter:
            def execute(self, invocation: ProviderInvocation) -> str:
                transport = UrlLibTransport(provider_invocation=invocation)
                transport.get("https://example.test/one", {})
                transport.get_with_timeout("https://example.test/two", {}, 1)
                transport.post("https://example.test/three", {}, b"safe fixture")
                return "complete"

        with patch(
            "a_share_research.identity_sources.build_opener",
            return_value=FixtureOpener(),
        ):
            result = ProviderLanes().run(
                provider_group="methods",
                adapter=OwnedHttpAdapter(),
                policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            )
        self.assertEqual(calls, ["GET", "GET"])
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")

    def test_redirect_can_complete_when_both_requests_fit_the_budget(self) -> None:
        calls: list[str] = []

        class FixtureOpener:
            def __init__(self, handler: HTTPRedirectHandler) -> None:
                self.handler = handler
                self.handler.parent = self  # type: ignore[assignment]

            def open(self, request: Request, *, timeout: float) -> FixtureResponse:
                calls.append(request.get_method())
                if len(calls) == 1:
                    headers = Message()
                    headers["Location"] = "/end"
                    request.timeout = timeout  # type: ignore[attr-defined]
                    return self.handler.http_error_302(
                        request, BytesIO(), 303, "See Other", headers
                    )
                return FixtureResponse()

        class OwnedHttpAdapter:
            def execute(self, invocation: ProviderInvocation) -> HttpResponse:
                return UrlLibTransport(provider_invocation=invocation).post(
                    "https://example.test/start", {}, b"safe fixture"
                )

        with patch(
            "a_share_research.identity_sources.build_opener",
            side_effect=lambda handler: FixtureOpener(handler),
        ):
            result = ProviderLanes().run(
                provider_group="redirect-complete",
                adapter=OwnedHttpAdapter(),
                policy=ProviderLanePolicy(actual_attempt_budget=2, timeout_seconds=1),
            )
        self.assertEqual(calls, ["POST", "GET"])
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.status, "succeeded")

    def test_transparent_in_process_subclass_counts_each_real_redirect_leaf(
        self,
    ) -> None:
        calls: list[str] = []
        body = (
            Path(__file__).parent / "fixtures" / "identity_sources" / "sse_600519.json"
        ).read_bytes()

        class TransparentTransport(UrlLibTransport):
            pass

        class Response(BytesIO):
            status = 200

            def __init__(self) -> None:
                super().__init__(body)
                self.headers = Message()
                self.headers["Content-Type"] = "application/json"

        class FixtureOpener:
            def __init__(self, handler: HTTPRedirectHandler) -> None:
                self.handler = handler
                self.handler.parent = self  # type: ignore[assignment]

            def open(self, request: Request, *, timeout: float):
                calls.append(request.full_url)
                if len(calls) == 1:
                    headers = Message()
                    headers["Location"] = "https://example.test/end"
                    request.timeout = timeout  # type: ignore[attr-defined]
                    return self.handler.http_error_302(
                        request, BytesIO(), 302, "Found", headers
                    )
                return Response()

        with patch(
            "a_share_research.identity_sources.build_opener",
            side_effect=lambda handler: FixtureOpener(handler),
        ):
            production = build_default_identity_operations(TransparentTransport())[0]

            class OwnedAdapter:
                def execute(self, invocation: ProviderInvocation):
                    return production.execute(
                        IdentityOperationRequest("600519", "2026-08-12"),
                        1,
                        provider_invocation=invocation,
                    )

            result = ProviderLanes().run(
                provider_group="subclass-redirect",
                adapter=OwnedAdapter(),
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            )
        self.assertEqual(production.execution_backend, "in_process")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")

    def test_in_process_owned_post_and_get_share_budget_without_outer_count(
        self,
    ) -> None:
        calls: list[str] = []

        class MultiMethodTransport(UrlLibTransport):
            def get_with_timeout(
                self, url: str, headers: dict[str, str], timeout_seconds: float
            ):
                self.post("https://example.test/prepare", {}, b"safe fixture")
                return super().get_with_timeout(url, headers, timeout_seconds)

        class FixtureOpener:
            def open(self, request: Request, *, timeout: float) -> FixtureResponse:
                calls.append(request.get_method())
                return FixtureResponse()

        with patch(
            "a_share_research.identity_sources.build_opener",
            return_value=FixtureOpener(),
        ):
            production = build_default_identity_operations(MultiMethodTransport())[0]

            class OwnedAdapter:
                def execute(self, invocation: ProviderInvocation):
                    return production.execute(
                        IdentityOperationRequest("600519", "2026-08-12"),
                        1,
                        provider_invocation=invocation,
                    )

            result = ProviderLanes().run(
                provider_group="subclass-methods",
                adapter=OwnedAdapter(),
                policy=ProviderLanePolicy(actual_attempt_budget=1, timeout_seconds=1),
            )
        self.assertEqual(calls, ["POST"])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.failure_code, "actual_attempt_budget_exhausted")


if __name__ == "__main__":
    unittest.main()
