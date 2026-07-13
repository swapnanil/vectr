"""MCP stdio transport — newline-delimited JSON-RPC 2.0 on stdin/stdout.

For MCP clients and hosting platforms that spawn the server as a subprocess
rather than connecting to a listening HTTP port. One JSON object per line, no
Content-Length framing, a single implicit session per process (there is
exactly one client: whatever spawned this process).

This module is transport-only — it reuses the same tool schemas and dispatch
logic as the Streamable-HTTP transport (`integrations/mcp_server/_dispatch.py`,
`app/routes.py`'s `POST /mcp`) rather than duplicating them.
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any, TextIO

from integrations.mcp_server._dispatch import handle_tools_call, handle_tools_list
from integrations.mcp_server._schemas import MCP_SERVER_INFO, MEMORY_READY_TOOLS

_PROTOCOL_VERSION = "2024-11-05"

_STILL_STARTING_MSG = (
    "Vectr is still starting up (loading models / building the workspace "
    "index). Try again in a few moments."
)


class ServiceHandle:
    """Thread-safe holder for a VectrService under construction on a background
    thread.

    The stdio read loop never blocks on service construction: `initialize`,
    `notifications/initialized`, `ping`, and `tools/list` all answer without
    touching the service; `tools/call` reads `.service`/`.error` through this
    holder and returns a graceful "still starting up" response — mirroring the
    existing "still indexing" degradation in `handle_tools_call`'s
    `vectr_search` branch — until construction finishes.

    `is_ready` flips as soon as `set_service` is called, which happens right
    after phase 1 of `VectrService` construction (fast: no embedder/indexer
    load) — see `app/service.py`'s `defer_search_init`. From that point,
    working-memory tools (`MEMORY_READY_TOOLS`) are dispatched immediately;
    search-side tools additionally wait for `service.fully_ready` (phase 2:
    embedder, indexer, searcher, watcher, symbol graph) before dispatching —
    keyed on tool NAME and service STATE only, never on request content.
    """

    def __init__(self) -> None:
        self._ready = threading.Event()
        self.service: Any = None
        self.error: BaseException | None = None

    def set_service(self, service: Any) -> None:
        self.service = service
        self._ready.set()

    def set_error(self, error: BaseException) -> None:
        self.error = error
        self._ready.set()

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()


def _ok(jsonrpc_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": jsonrpc_id, "result": result}


def _err(jsonrpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": code, "message": message}}


def dispatch_line(body: dict, handle: ServiceHandle, session_id: str) -> dict | None:
    """Dispatch one decoded JSON-RPC request against `handle`.

    Returns the response dict to write, or None for notifications (per the
    JSON-RPC 2.0 spec, a notification receives no response body).
    """
    jsonrpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return _ok(jsonrpc_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_INFO["name"], "version": MCP_SERVER_INFO["version"]},
        })

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _ok(jsonrpc_id, {})

    if method == "tools/list":
        return _ok(jsonrpc_id, handle_tools_list(session_id=session_id, service=handle.service))

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not tool_name:
            return _err(jsonrpc_id, -32602, "Missing required param: name")
        if handle.error is not None:
            return _ok(jsonrpc_id, {
                "content": [{"type": "text", "text": f"Vectr failed to start: {handle.error}"}],
                "isError": True,
            })
        if not handle.is_ready:
            return _ok(jsonrpc_id, {
                "content": [{"type": "text", "text": _STILL_STARTING_MSG}],
                "isError": False,
            })
        # UPG-STDIO-MEMORY-READY: working-memory tools are servable as soon as
        # the service object exists (phase 1 done); search-side tools still
        # wait for phase 2 (embedder/indexer/searcher/watcher/symbol graph).
        if tool_name not in MEMORY_READY_TOOLS and not getattr(handle.service, "fully_ready", True):
            return _ok(jsonrpc_id, {
                "content": [{"type": "text", "text": _STILL_STARTING_MSG}],
                "isError": False,
            })
        return _ok(jsonrpc_id, handle_tools_call(
            tool_name, arguments, handle.service, session_id=session_id, client_label="",
        ))

    return _err(jsonrpc_id, -32601, f"Method not found: {method}")


def run_stdio_loop(handle: ServiceHandle, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Read newline-delimited JSON-RPC requests from `stdin`, dispatch each
    against `handle`, and write single-line JSON responses to `stdout`,
    flushing after every write. EOF on stdin ends the loop (clean shutdown).
    """
    import uuid

    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    session_id = uuid.uuid4().hex

    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_err(None, -32700, "Parse error: invalid JSON")) + "\n")
            stdout.flush()
            continue

        response = dispatch_line(body, handle, session_id)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
