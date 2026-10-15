"""Fixed private worker; only the official SDK constructs provider messages."""

from __future__ import annotations

import base64
import contextlib
import importlib
import importlib.metadata
import io
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_FRAME = 1_048_576
_END = b"<![CDATA[]]>\n"


def _write(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    scripts = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(scripts))
    from _runtime_path import add_bundled_runtime, bundled_runtime_path

    if not add_bundled_runtime():
        return 1
    runtime = bundled_runtime_path()
    distributions = importlib.metadata.distributions(path=[str(runtime)])
    if not any(
        d.metadata.get("Name", "").lower() == "baostock" and d.version == "0.9.4"
        for d in distributions
    ):
        return 1
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        sdk: Any = importlib.import_module("baostock")
        sockets: Any = importlib.import_module("baostock.util.socketutil")
        context: Any = importlib.import_module("baostock.common.context")
    for loaded in (sdk, sockets, context):
        origin = Path(loaded.__file__).resolve()
        if not origin.is_relative_to(runtime.resolve()):
            return 1
    # The fresh process imports the artifact runtime exclusively. Explicit default
    # context is independent of any parent interpreter's authenticated SDK state.
    context.apiKey = "0"
    timeout = float(sys.argv[1])
    active_socket: socket.socket | None = None
    frames: list[bytes] = []
    retrieved: list[str] = []
    sends = 0

    def connect(_self: object, api_key: str) -> None:
        nonlocal active_socket
        if api_key != "0" or active_socket is not None:
            raise RuntimeError("Invalid anonymous SDK session.")
        active_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        active_socket.settimeout(timeout)
        active_socket.connect(("public-api.baostock.com", 10030))
        context.default_socket = active_socket

    def send_msg(message: str) -> str:
        nonlocal sends
        if active_socket is None or sends:
            raise RuntimeError("Only one source message is allowed per invocation.")
        sends += 1
        active_socket.sendall((message + "\n").encode("utf-8"))
        chunks = bytearray()
        while not chunks.endswith(_END):
            chunk = active_socket.recv(min(8192, _MAX_FRAME + 1 - len(chunks)))
            if not chunk or len(chunks) + len(chunk) > _MAX_FRAME:
                raise RuntimeError("The bounded source frame is incomplete.")
            chunks.extend(chunk)
        payload = bytes(chunks)
        frames.append(payload)
        retrieved.append(datetime.now(timezone.utc).isoformat())
        return payload.decode("utf-8")

    sockets.SocketUtil.connect = connect
    sockets.send_msg = send_msg
    result: Any = None
    next_page = 1
    day: str | None = None
    _write({"kind": "ready"})
    try:
        for line in sys.stdin:
            if len(line) > 4096:
                return 1
            command = json.loads(line)
            kind, parameters = command["type"], command["parameters"]
            frames.clear()
            retrieved.clear()
            sends = 0
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                if kind == "00" and parameters == ["login", "anonymous"]:
                    sdk.login()
                elif kind == "35":
                    if (
                        len(parameters) != 5
                        or parameters[:2] != ["query_all_stock", "anonymous"]
                        or parameters[3] != "2000"
                        or parameters[2] != str(next_page)
                    ):
                        return 1
                    if next_page == 1:
                        day = parameters[4]
                        result = sdk.query_all_stock(day=day)
                    else:
                        if parameters[4] != day or result is None:
                            return 1
                        # Official next/get_row_data path; get_data() and removed
                        # pandas.DataFrame.append are never called.
                        while result.get_row_data():
                            pass
                        result.next()
                    next_page += 1
                elif kind == "02" and parameters == ["logout", "anonymous"]:
                    sdk.logout()
                else:
                    return 1
            if len(frames) != 1:
                return 1
            _write(
                {
                    "kind": "response",
                    "payload": base64.b64encode(frames[0]).decode("ascii"),
                    "retrieved_at": retrieved[0],
                }
            )
    finally:
        if active_socket is not None:
            active_socket.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1) from None
