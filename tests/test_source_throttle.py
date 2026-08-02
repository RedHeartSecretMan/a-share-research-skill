from __future__ import annotations

import unittest

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import TransportError  # noqa: E402
from a_share_research.source_throttle import (  # noqa: E402
    RequestGateError,
    SerialRequestGate,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class SerialRequestGateTests(unittest.TestCase):
    def test_session_pacing_and_rate_limit_backoff_are_bounded_and_diagnostic(
        self,
    ) -> None:
        clock = FakeClock()
        gate = SerialRequestGate(
            minimum_interval_seconds=1.0,
            jitter_bounds=(0.2, 0.2),
            rate_limit_backoffs=(0.5,),
            clock=clock,
            sleeper=clock.sleep,
            jitter=lambda lower, upper: lower,
        )
        attempts = 0

        first, first_diagnostics = gate.run(lambda: "first")

        def rate_limited_once() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TransportError("rate_limited", "sanitized")
            return "second"

        second, second_diagnostics = gate.run(rate_limited_once)

        self.assertEqual(first, "first")
        self.assertEqual(first_diagnostics, ())
        self.assertEqual(second, "second")
        self.assertEqual(attempts, 2)
        self.assertEqual(clock.sleeps, [1.2, 0.7, 0.5])
        self.assertEqual(
            [item.code for item in second_diagnostics],
            [
                "source_request_paced",
                "rate_limit_backoff",
                "source_request_paced",
            ],
        )
        self.assertEqual(
            [item.delay_seconds for item in second_diagnostics],
            [1.2, 0.7, 0.5],
        )
        self.assertEqual(second_diagnostics[1].attempt, 1)

    def test_exhausted_backoff_preserves_rate_limited_error(self) -> None:
        gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(),
            jitter=lambda lower, upper: lower,
        )

        with self.assertRaises(TransportError) as caught:
            gate.run(
                lambda: (_ for _ in ()).throw(
                    TransportError("rate_limited", "sanitized")
                )
            )

        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(str(caught.exception), "sanitized")

    def test_exhausted_retry_exposes_completed_backoff_and_original_cause(self) -> None:
        attempts = 0
        gate = SerialRequestGate(
            minimum_interval_seconds=0,
            jitter_bounds=(0, 0),
            rate_limit_backoffs=(0.5,),
            sleeper=lambda seconds: None,
            jitter=lambda lower, upper: lower,
        )

        def always_rate_limited() -> str:
            nonlocal attempts
            attempts += 1
            raise TransportError("rate_limited", "sanitized")

        with self.assertRaises(RequestGateError) as caught:
            gate.run(always_rate_limited)

        self.assertEqual(attempts, 2)
        self.assertIs(caught.exception.__cause__, caught.exception.cause)
        self.assertIsInstance(caught.exception.cause, TransportError)
        self.assertEqual(caught.exception.cause.code, "rate_limited")
        self.assertEqual(
            [diagnostic.code for diagnostic in caught.exception.diagnostics],
            ["rate_limit_backoff"],
        )


if __name__ == "__main__":
    unittest.main()
