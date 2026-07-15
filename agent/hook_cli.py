"""Stdlib-only implementation of `vectr hook <event>` (UPG-HOOK-SUBPROCESS-
IMPORT-TAX).

A hook fires on every SessionStart / UserPromptSubmit / PreToolUse /
PreCompact event in an editor session — of every vectr entry point, this is
the one invoked most often per turn, and it runs as a fresh subprocess each
time. Its own import cost is pure per-event latency the harness (and the
person waiting on it) pays before the hook can even read stdin. The main
CLI module (`main.py`) pulls in `dotenv`, the full `agent.config` surface
(every tunable across the whole product, not just the one float a hook
needs), argparse's subcommand tree, and (lazily, per network call) `httpx`
— together well over the <20ms budget this path is held to.

This module reimplements exactly the 4 hook branches `main.cmd_hook`
defines — same request shapes, same `hookSpecificOutput` envelope, same
"never raise" resilience contract — using only the standard library plus
`agent.instance_registry` (itself stdlib-only) to resolve which daemon
serves this workspace. `main.py`'s own `cmd_hook` remains the canonical,
exhaustively-tested implementation this mirrors; the two are kept in sync
by tests/test_hook_cli_parity.py, which drives both against the same fixed
input and diffs their captured stdout/stderr/exit-code byte-for-byte,
rather than by sharing one module-level implementation — sharing a single
implementation across both the always-imported `main.py` and this
stdlib-only path would re-import everything this module exists to avoid.

The one tunable the slower path used to read from agent/config.yaml
(`hooks.min_similarity`, the per-turn recall relevance floor) is not read
here at all: the daemon itself applies that default server-side whenever a
`/v1/recall` request carries `hook_event` but omits `min_similarity` (see
`VectrService.recall`) — the single source of truth lives in the one place
that already pays `agent.config`'s import cost exactly once, at daemon
startup, rather than being re-derived by every short-lived hook subprocess.

`_post_json` speaks raw HTTP/1.1 over a plain `socket` rather than using
`urllib.request`: on this interpreter, `urllib.response.addbase` subclasses
`tempfile._TemporaryFileWrapper`, which drags in `shutil` and, through it,
the `bz2`/`lzma`/`zstd` compression stack — none of it reachable code for a
same-machine JSON POST, but still ~15ms of import cost paid on every
invocation. The daemon is always plain HTTP on localhost (no TLS,
redirects, or cookies), and every response is a small, non-chunked JSON
body vectr's own routes.py produces — a full HTTP client is substantially
more machinery than that needs.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

from agent.instance_registry import InstanceRegistry, workspace_hash
from agent.prompt_templates import load_template

# Mirrors main.py's _HOOK_RECALL_LIMIT / _HOOK_NO_DOUBLE_RECALL_LINE /
# _HOOK_EVENTS_ANNOUNCE_INJECTION exactly (see tests/test_hook_cli_parity.py).
_HOOK_RECALL_LIMIT = 3
_HOOK_NO_DOUBLE_RECALL_LINE = load_template("hook_no_double_recall.txt")
_HOOK_EVENTS_ANNOUNCE_INJECTION = ("SessionStart", "UserPromptSubmit")


def _recv_full_response(sock: socket.socket) -> bytes:
    """Read from `sock` until either the declared Content-Length body is
    fully in hand or the peer closes the connection — whichever comes
    first, so a keep-alive-capable server doesn't force us to wait for a
    close that may never come, and a close-on-response server doesn't force
    us to keep reading past a body we've already fully received."""
    chunks: list[bytes] = []
    header_end = -1
    content_length: int | None = None
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        joined = b"".join(chunks)
        if header_end == -1:
            header_end = joined.find(b"\r\n\r\n")
            if header_end != -1:
                header_text = joined[:header_end].decode("iso-8859-1", errors="replace")
                for line in header_text.split("\r\n")[1:]:
                    name, _, value = line.partition(":")
                    if name.strip().lower() == "content-length":
                        content_length = int(value.strip())
                        break
        if header_end != -1 and content_length is not None:
            if len(joined) >= header_end + 4 + content_length:
                return joined
    return b"".join(chunks)


def _parse_http_response(raw: bytes) -> tuple[int, bytes] | None:
    """Split `raw` into (status_code, body_bytes); None if it isn't even a
    well-formed HTTP response head."""
    header_end = raw.find(b"\r\n\r\n")
    if header_end == -1:
        return None
    status_line = raw[:header_end].split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    parts = status_line.split(" ", 2)
    if len(parts) < 2:
        return None
    try:
        status_code = int(parts[1])
    except ValueError:
        return None
    return status_code, raw[header_end + 4:]


def _post_json(port: int, path: str, payload: dict, timeout: float = 30) -> dict | None:
    """POST `payload` as JSON to `http://localhost:<port><path>`, return the
    parsed response body, or None on ANY failure (connection refused,
    timeout, non-2xx status, malformed response) — never raises."""
    data = json.dumps(payload).encode("utf-8")
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + data
    try:
        with socket.create_connection(("localhost", port), timeout=timeout) as sock:
            sock.sendall(request)
            raw = _recv_full_response(sock)
        parsed = _parse_http_response(raw)
        if parsed is None:
            return None
        status_code, body = parsed
        if status_code >= 400:
            return None
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _read_hook_stdin() -> dict:
    """Read the Claude Code hook event JSON from stdin; {} if absent/invalid."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _emit_hook_context(event_name: str, text: str) -> None:
    """Print the Claude Code additionalContext envelope — only when there's
    text. Mirrors main.py's `_emit_hook_context` exactly (UPG-11.5 notice on
    SessionStart/UserPromptSubmit)."""
    if not text.strip():
        return
    if event_name in _HOOK_EVENTS_ANNOUNCE_INJECTION:
        text = f"{_HOOK_NO_DOUBLE_RECALL_LINE}\n\n{text}"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))


def _resolve_hook_instance(cwd: str) -> dict | None:
    """Find the running daemon serving `cwd`, or None. Mirrors main.py's
    `_resolve_hook_instance` exactly (exact-cwd first, then walk parents;
    never falls back to a default port)."""
    registry = InstanceRegistry()
    here = Path(cwd).resolve()
    for d in (here, *here.parents):
        entry = registry.get(workspace_hash(str(d)))
        if entry is not None:
            return entry
    return None


def _fetch_recall(port: int, payload: dict) -> str:
    """POST /v1/recall and return the notes text, or '' on ANY failure.
    Never raises — this feeds harness-injected hook context and must not
    break the session if the daemon is down, slow, or returns an error."""
    body = _post_json(port, "/v1/recall", payload)
    if body is None:
        return ""
    return body.get("notes", "") or ""


def _post_snapshot(port: int, label: str) -> bool:
    """POST /v1/snapshot; True on success, False on any failure (never raises)."""
    return _post_json(port, "/v1/snapshot", {"label": label}) is not None


def _post_trigger_reset(port: int, session_id: str) -> bool:
    """POST /v1/trigger/reset; True on success, False on any failure (never
    raises)."""
    return _post_json(port, "/v1/trigger/reset", {"session_id": session_id}) is not None


def run_hook(hook_event: str) -> None:
    """Same behavior as `main.cmd_hook(argparse.Namespace(hook_event=...))`
    for the 4 declared hook events (session-start / user-prompt-submit /
    pre-tool-use / pre-compact) — never raises, always "exits" (returns)
    cleanly regardless of daemon state. See `main.cmd_hook`'s docstring for
    the full event → engine-event → REST-call mapping this reimplements."""
    try:
        event = _read_hook_stdin()
        cwd = event.get("cwd") or os.getcwd()
        entry = _resolve_hook_instance(cwd)
        if entry is None:
            return  # no daemon serves this workspace -> inject nothing
        port = entry["port"]
        session_id = (event.get("session_id") or "").strip() or None

        if hook_event == "session-start":
            payload = {"boot": True, "hook_event": "SessionStart"}
            if session_id:
                payload["session_id"] = session_id
            source = (event.get("source") or "").strip()
            if source == "compact":
                payload["events"] = ["session-start", "post-compaction"]
            notes = _fetch_recall(port, payload)
            _emit_hook_context("SessionStart", notes)

        elif hook_event == "user-prompt-submit":
            prompt = (event.get("prompt") or "").strip()
            if not prompt:
                return
            limit = int(os.getenv("VECTR_HOOK_RECALL_LIMIT", str(_HOOK_RECALL_LIMIT)))
            payload = {
                "query": prompt, "limit": limit, "detail": "index",
                "hook_event": "UserPromptSubmit", "events": ["prompt-submit"],
            }
            # min_similarity omitted by default: the daemon applies its own
            # HOOKS_MIN_SIMILARITY floor for any hook_event-bearing request
            # (see VectrService.recall) — the single source of truth for
            # that value. VECTR_HOOK_MIN_SIMILARITY still overrides it
            # explicitly, mirroring main.py's slower path exactly.
            env_min_sim = os.getenv("VECTR_HOOK_MIN_SIMILARITY")
            if env_min_sim is not None:
                payload["min_similarity"] = float(env_min_sim)
            if session_id:
                payload["session_id"] = session_id
            notes = _fetch_recall(port, payload)
            _emit_hook_context("UserPromptSubmit", notes)

        elif hook_event == "pre-tool-use":
            file_path = ((event.get("tool_input") or {}).get("file_path") or "").strip()
            if not file_path:
                return
            payload = {"file_path": file_path, "kind": "gotcha", "hook_event": "PreToolUse"}
            if session_id:
                payload["session_id"] = session_id
            notes = _fetch_recall(port, payload)
            _emit_hook_context("PreToolUse", notes)

        elif hook_event == "pre-compact":
            trigger = (event.get("trigger") or "manual").strip() or "manual"
            label = f"pre-compact-{trigger}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            _post_snapshot(port, label)
            if session_id:
                _post_trigger_reset(port, session_id)
    except Exception:
        pass  # hook safety: never propagate
