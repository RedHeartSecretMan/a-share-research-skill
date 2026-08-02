"""Shared serial request pacing for rate-sensitive source operations."""

from __future__ import annotations

from dataclasses import dataclass
from random import uniform
from threading import Lock
from time import monotonic, sleep
from typing import Callable, Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RequestGateDiagnostic:
    """Non-sensitive evidence that a bounded pacing action occurred."""

    code: str
    delay_seconds: float
    attempt: int | None = None

    @property
    def message(self) -> str:
        if self.code == "rate_limit_backoff":
            return "The source request used a bounded rate-limit backoff."
        return "The source request was paced to respect its rate limit."

    def details(self) -> dict[str, str]:
        details = {"delay_seconds": format(self.delay_seconds, ".3f")}
        if self.attempt is not None:
            details["attempt"] = str(self.attempt)
        return details


class RequestGate(Protocol):
    """Injectable request execution seam used by source operations."""

    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]: ...


class RequestGateError(Exception):
    """Terminal rate-limit error with diagnostics from completed gate actions."""

    def __init__(
        self,
        cause: Exception,
        diagnostics: tuple[RequestGateDiagnostic, ...],
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.diagnostics = diagnostics


class SerialRequestGate:
    """Serialize source calls while applying bounded pacing and retry delays."""

    def __init__(
        self,
        *,
        minimum_interval_seconds: float = 1.0,
        jitter_bounds: tuple[float, float] = (0.1, 0.5),
        rate_limit_backoffs: tuple[float, ...] = (1.0, 2.0),
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
        jitter: Callable[[float, float], float] = uniform,
    ) -> None:
        lower, upper = jitter_bounds
        if (
            minimum_interval_seconds < 0
            or lower < 0
            or upper < lower
            or upper > 5.0
            or len(rate_limit_backoffs) > 3
            or any(delay < 0 or delay > 60.0 for delay in rate_limit_backoffs)
        ):
            raise ValueError("request gate timing bounds are invalid")
        self._minimum_interval_seconds = minimum_interval_seconds
        self._jitter_bounds = jitter_bounds
        self._rate_limit_backoffs = rate_limit_backoffs
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter
        self._lock = Lock()
        self._last_request_at: float | None = None

    def run(
        self, request: Callable[[], T]
    ) -> tuple[T, tuple[RequestGateDiagnostic, ...]]:
        """Run one request serially, retrying only a bounded rate-limit error."""

        diagnostics: list[RequestGateDiagnostic] = []
        with self._lock:
            backoff_index = 0
            while True:
                paced = self._pace()
                if paced is not None:
                    diagnostics.append(paced)
                self._last_request_at = self._clock()
                try:
                    result = request()
                except Exception as error:
                    if getattr(error, "code", None) != "rate_limited":
                        raise
                    if backoff_index >= len(self._rate_limit_backoffs):
                        if diagnostics:
                            raise RequestGateError(error, tuple(diagnostics)) from error
                        raise
                    attempt = backoff_index + 1
                    delay = self._delay(self._rate_limit_backoffs[backoff_index])
                    self._sleeper(delay)
                    diagnostics.append(
                        RequestGateDiagnostic(
                            code="rate_limit_backoff",
                            delay_seconds=delay,
                            attempt=attempt,
                        )
                    )
                    backoff_index += 1
                    continue
                return result, tuple(diagnostics)

    def _pace(self) -> RequestGateDiagnostic | None:
        if self._last_request_at is None:
            return None
        remaining = self._minimum_interval_seconds - (
            self._clock() - self._last_request_at
        )
        if remaining <= 0:
            return None
        delay = self._delay(remaining)
        self._sleeper(delay)
        return RequestGateDiagnostic(
            code="source_request_paced",
            delay_seconds=delay,
        )

    def _delay(self, base: float) -> float:
        lower, upper = self._jitter_bounds
        jitter = self._jitter(lower, upper)
        if jitter < lower or jitter > upper:
            raise ValueError("request gate jitter escaped its configured bounds")
        return round(base + jitter, 9)


EASTMONEY_REQUEST_GATE = SerialRequestGate()
