"""Parity coverage for UPG-HOOK-SUBPROCESS-IMPORT-TAX.

`agent/hook_cli.py`'s stdlib-only `run_hook()` is a second implementation of
`main.py`'s `cmd_hook` — deliberately, so the `vectr` binary's real hook
subprocess invocation never pays `main.py`'s full import cost (see that
module's docstring for why a shared import would defeat the point). A
second implementation that silently drifts from the one it mirrors is worse
than no fast path at all, so this file drives BOTH implementations against
a real local HTTP server with the same fixture stdin and asserts their
captured stdout is byte-for-byte identical, across every hook event and the
representative edge cases (empty prompt, no file path, no daemon
registered, session_id present/absent, PreCompact triggers).
"""
from __future__ import annotations

import argparse
import io
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import main as m
from agent import hook_cli
from agent.instance_registry import InstanceRegistry, workspace_hash


class _FastBindHTTPServer(ThreadingHTTPServer):
    """`HTTPServer.server_bind()` calls `socket.getfqdn(host)`, a reverse-DNS
    lookup that can hang for many seconds on hosts with slow or
    VPN-shadowed DNS — observed locally binding even 127.0.0.1. The test
    only needs a bound loopback socket, not a resolved hostname, so bind via
    `TCPServer`'s own `server_bind` and skip the FQDN lookup entirely."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class _StubDaemonHandler(BaseHTTPRequestHandler):
    """Canned JSON bodies for /v1/recall, /v1/snapshot, /v1/trigger/reset —
    enough of the real daemon's contract for both httpx (main.cmd_hook) and
    urllib (agent.hook_cli.run_hook) to round-trip through identically."""

    RESPONSES = {
        "/v1/recall": {"notes": "[1] lock_workspace() at resolver.rs:214"},
        "/v1/snapshot": {"ok": True},
        "/v1/trigger/reset": {"ok": True},
    }

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain the request body
        body = json.dumps(self.RESPONSES.get(self.path, {})).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep test output quiet


@pytest.fixture
def stub_daemon():
    server = _FastBindHTTPServer(("127.0.0.1", 0), _StubDaemonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _registry_pointing_at(tmp_path, port):
    reg = InstanceRegistry(registry_path=tmp_path / "instances.json")
    reg.register(workspace_hash("/p"), "/p", port, 12345)
    return reg


# (hook_event, stdin JSON) — one representative fixture per branch of
# main.cmd_hook / agent.hook_cli.run_hook, including the edge cases each
# branch early-returns on.
FIXTURES = [
    ("session-start", '{"cwd": "/p"}'),
    ("session-start", '{"cwd": "/p", "source": "compact"}'),
    ("session-start", '{"cwd": "/p", "session_id": "abc-123"}'),
    ("user-prompt-submit", '{"cwd": "/p", "prompt": "fix the workspace lock"}'),
    ("user-prompt-submit", '{"cwd": "/p", "prompt": "   "}'),
    ("user-prompt-submit", '{"cwd": "/p", "prompt": "lock flow", "session_id": "abc-123"}'),
    ("pre-tool-use", '{"cwd": "/p", "tool_input": {"file_path": "/p/agent/symbol_graph.py"}}'),
    ("pre-tool-use", '{"cwd": "/p", "tool_input": {}}'),
    ("pre-compact", '{"cwd": "/p", "trigger": "auto", "session_id": "abc-123"}'),
    ("pre-compact", '{"cwd": "/p"}'),
    ("session-start", '{"cwd": "/nowhere"}'),  # no daemon registered
]


class TestHookCliParity:
    @pytest.mark.parametrize("hook_event,stdin_json", FIXTURES)
    def test_slim_path_matches_canonical_cmd_hook(
        self, hook_event, stdin_json, tmp_path, stub_daemon, monkeypatch, capsys,
    ):
        reg = _registry_pointing_at(tmp_path, stub_daemon)
        monkeypatch.setattr(m, "InstanceRegistry", lambda *a, **k: reg)
        monkeypatch.setattr(hook_cli, "InstanceRegistry", lambda *a, **k: reg)

        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        m.cmd_hook(argparse.Namespace(hook_event=hook_event))
        canonical_out = capsys.readouterr().out

        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_json))
        hook_cli.run_hook(hook_event)
        slim_out = capsys.readouterr().out

        assert slim_out == canonical_out
