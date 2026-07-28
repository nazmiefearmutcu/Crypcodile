"""The JSON-RPC transport for the MCP projection, and nothing that knows a tool's name.

:mod:`crocodile.surfaces.mcp` is deliberately transport-free — it produces a ``tools/list``
payload and a ``tools/call`` implementation and never touches a socket — so this module is
where reading stdin lives. The split is what makes the projection testable without starting
a server, which is how the legacy handlers ended up with almost no coverage.

The legacy loop was 500 lines of ``elif tool_name == ...``, one branch per tool, each with
its own argument unpacking and its own ``except Exception`` writing a differently-worded
error string. Every one of those branches is now :func:`crocodile.surfaces.mcp.call_tool`,
so adding a capability adds a tool here with no edit at all.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from crocodile.core.errors import CrocodileError
from crocodile.surfaces import dispatch, mcp

__all__ = ["handle_request", "serve_stdio"]

_PROTOCOL_VERSION = "2024-11-05"

_REPORTED: tuple[type[BaseException], ...] = (
    CrocodileError,
    TypeError,
    *dispatch.BAD_REQUEST,
    *dispatch.REFUSED,
    *dispatch.UNAVAILABLE,
)
"""What is answered inside the tool result rather than as a JSON-RPC error.

``TypeError`` is here for one specific reason: it is what escapes when the result cannot be
encoded, and that is a fact about this transport rather than about the request, so the agent
that asked has to be told in the channel it reads. Everything else is the caller's own — a
bad argument, an unimplemented asset class, a refusal.
"""


def handle_request(request: dict[str, Any], *, data_dir: Path | None = None) -> dict[str, Any]:
    """Answer one JSON-RPC request.

    Separate from the read loop so the protocol can be tested by calling it, rather than by
    spawning a process and writing to its stdin — which is the only way the six legacy
    servers could be exercised, and the reason they mostly were not.
    """
    from crocodile import __version__

    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "crocodile-mcp", "version": __version__},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": mcp.tool_definitions()}}

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        try:
            result: Any = mcp.call_tool(name, params.get("arguments") or {}, data_dir=data_dir)
            # Inside the try, because serialising the result is part of answering the call.
            # It sat outside, so a value this transport cannot encode — every lake read
            # carries a `date` cell — escaped to the read loop as `-32603 Internal error`,
            # which tells an agent the *call* failed rather than what about its request
            # could not be answered. The encoder is `json.dumps` and not msgspec's on
            # purpose: it is the one every MCP client reads with, so a type it refuses is a
            # type this projection must not put on the wire.
            text = json.dumps(result, indent=2)
        except KeyError:
            text = json.dumps({"error": f"Tool {name} not found"}, indent=2)
        except _REPORTED as exc:
            # A bad argument, an asset class with no implementation, a write this surface is
            # not trusted to start: the caller's problem, reported in the tool result, which
            # is what an agent reads. A protocol-level error would tell it the *call* failed
            # rather than what about the ask could not be answered — and a refusal reported
            # that way reads as a transport fault an agent will retry.
            text = json.dumps({"error": f"{name} failed: {exc}"}, indent=2)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method {method} not found"},
    }


def _read_line() -> str:
    """Read one line off stdin's binary buffer.

    Binary rather than text: a closed pipe reports EOF as empty bytes reliably, while
    text-mode ``readline`` has hung on interpreter shutdown with a closed pipe.
    """
    try:
        raw = sys.stdin.buffer.readline()
    except (AttributeError, ValueError, OSError):
        return ""
    return raw.decode("utf-8", errors="replace") if raw else ""


async def serve_stdio(data_dir: Path | None = None) -> None:
    """Run the JSON-RPC loop over stdin and stdout until the peer closes stdin.

    Stdin is read on a *private* executor, never the asyncio default one:
    ``asyncio.run`` joins the default executor on the way out with no timeout, so a thread
    still blocked on stdin would hang the process at exit rather than end it.
    """
    import logging

    # Anything logged to stdout would land inside the JSON-RPC stream and desynchronise the
    # peer's parser, so every handler is moved to stderr before the first line is read.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    for handler in logging.root.handlers:
        if getattr(handler, "stream", None) is sys.stdout:
            handler.stream = sys.stderr  # type: ignore[attr-defined]

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-stdin")
    try:
        while True:
            try:
                line = await loop.run_in_executor(executor, _read_line)
            except RuntimeError:  # the executor shut down while this was waiting
                break
            if not line:
                break
            try:
                request = json.loads(line.strip())
                if not isinstance(request, dict) or "method" not in request:
                    continue
                response = handle_request(request, data_dir=data_dir)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {exc}",
                        "data": traceback.format_exc(),
                    },
                }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    finally:
        # Never join a thread that may still be blocked on stdin.
        executor.shutdown(wait=False, cancel_futures=True)
