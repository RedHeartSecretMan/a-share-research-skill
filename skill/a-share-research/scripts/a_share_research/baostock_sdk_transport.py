"""Private isolated official-SDK transport for the dated roster owner.

The parent owns one #119 invocation for each command. The fixed worker refuses
more than one native send per command; imports/health and shutdown perform no
source calls. No application credentials or SDK globals cross the process seam.
"""

from __future__ import annotations

import base64
import json
import os
import select
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import cast

from .baostock_universe_source import BaostockResponse

_MAX_WIRE = 1_500_000


class BaostockSdkTransport:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.process: subprocess.Popen[bytes] | None = None
        self.pending = b""

    def open(self) -> None:
        """Import the locked SDK in a fresh worker, without starting a session."""
        if self.process is not None:
            raise RuntimeError("The SDK worker session is already owned.")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("baostock_sdk_worker.py")),
                str(self.timeout_seconds),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        if self._read() != {"kind": "ready"}:
            raise RuntimeError("The locked anonymous SDK runtime is unavailable.")

    def _read(self) -> dict[str, object]:
        process = self.process
        if process is None or process.stdout is None:
            raise RuntimeError("The SDK worker is unavailable.")
        deadline = monotonic() + self.timeout_seconds
        while b"\n" not in self.pending:
            remaining = deadline - monotonic()
            if (
                remaining <= 0
                or not select.select([process.stdout], [], [], remaining)[0]
            ):
                raise TimeoutError("The SDK worker exceeded its bounded timeout.")
            chunk = os.read(process.stdout.fileno(), 65_536)
            if not chunk:
                raise RuntimeError("The SDK worker ended without a response.")
            self.pending += chunk
            if len(self.pending) > _MAX_WIRE:
                raise RuntimeError("The SDK worker response exceeded its finite bound.")
        line, self.pending = self.pending.split(b"\n", 1)
        value: object = json.loads(line)
        if type(value) is not dict:
            raise RuntimeError("The SDK worker response is invalid.")
        return cast(dict[str, object], value)

    def exchange(
        self, message_type: str, parameters: tuple[str, ...]
    ) -> BaostockResponse:
        process = self.process
        if process is None or process.stdin is None:
            raise RuntimeError("The SDK worker is unavailable.")
        process.stdin.write(
            json.dumps({"type": message_type, "parameters": parameters}).encode()
            + b"\n"
        )
        process.stdin.flush()
        value = self._read()
        if (
            set(value) != {"kind", "payload", "retrieved_at"}
            or value["kind"] != "response"
            or type(value["payload"]) is not str
            or type(value["retrieved_at"]) is not str
        ):
            raise RuntimeError("The SDK source invocation failed.")
        return BaostockResponse(
            base64.b64decode(value["payload"], validate=True),
            datetime.fromisoformat(value["retrieved_at"]),
        )

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()
            # Only observed process exit releases the owning lane. A failed kill
            # cannot be treated as quiescence; the outer lane retains its poison.
            process.wait()
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
