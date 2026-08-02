"""~/.vectr/instances.json — per-workspace daemon registry.

Tracks all running vectr daemons so multiple IDE windows can each
get their own vectr instance on its own port.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path.home() / ".vectr" / "instances.json"


def workspace_hash(path: str) -> str:
    """sha256(absolute_workspace_path)[:12] — same prefix used for cache dirs."""
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, we lack permission to signal it


def _port_is_free(port: int) -> bool:
    """True if `port` can be bound on 127.0.0.1 right now.

    Sets SO_REUSEADDR on the probe socket before binding — this mirrors
    exactly what uvicorn itself does when it later binds vectr's real listen
    socket (uvicorn.config.Config.bind_socket always sets SO_REUSEADDR).
    Without it, a port whose previous listener just exited reads as "busy"
    for the OS's TIME_WAIT linger window (tens of seconds) even though the
    only kind of bind vectr's own daemon ever performs — a fresh
    SO_REUSEADDR bind — would succeed immediately. `vectr stop` followed by
    `start`/`restart` a moment later sits squarely inside that window on
    every run, so without this the probe misreports the just-released port
    as busy and `find_free_port` below walks to the next port instead of
    reusing it, leaving already-written MCP configs pointing at the wrong
    port with no error naming the cause (UPG-RESTART-PORT-WALK-BREAKS-MCP).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class InstanceRegistry:
    def __init__(self, registry_path: Path = REGISTRY_PATH) -> None:
        self._path = registry_path

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        # ~/.vectr holds the instance registry, logs, and (opt-in) audit log —
        # owner-only on POSIX hosts (see agent/fs_permissions.py).
        from agent.fs_permissions import secure_dir
        secure_dir(self._path.parent)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.rename(tmp, self._path)

    def prune_dead(self) -> None:
        """Remove entries whose PIDs are no longer alive."""
        data = self._read()
        live = {k: v for k, v in data.items() if _is_pid_alive(v["pid"])}
        if len(live) != len(data):
            self._write(live)

    def get(self, ws_hash: str) -> dict[str, Any] | None:
        return self._read().get(ws_hash)

    def list_all(self) -> dict[str, Any]:
        return self._read()

    def register(
        self,
        ws_hash: str,
        workspace: str,
        port: int,
        pid: int,
        extra_roots: list[str] | None = None,
        code_workspace_file: str | None = None,
        mode: str = "full",
        host: str = "127.0.0.1",
    ) -> None:
        data = self._read()
        data[ws_hash] = {
            "workspace": workspace,
            "port": port,
            "pid": pid,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # Extra roots + the originating .code-workspace file (if any) are
            # recorded here so `vectr status` can show what this instance
            # actually serves without re-deriving it from CLI args it never
            # saw (UPG-CLI-STATUS-MODE).
            "extra_roots": extra_roots or [],
            "code_workspace_file": code_workspace_file,
            # UPG-RESTART-DROPS-MODE: the launch configuration a later
            # `restart` has to reproduce. Before this, mode and bind host
            # existed only in the launching argv, so restarting a
            # `--memory-only` daemon silently brought it back in FULL mode
            # (indexing + watcher) unless the operator remembered the flag —
            # which the CLI's own staleness banner never told them to pass.
            # `mode` uses the same vocabulary the daemon reports in
            # `/v1/status` ("full" | "memory-only" | "search-only").
            "mode": mode,
            "host": host,
        }
        self._write(data)

    def unregister(self, ws_hash: str) -> None:
        data = self._read()
        data.pop(ws_hash, None)
        self._write(data)

    def find_free_port(self, ws_hash: str, preferred_port: int) -> int:
        """Allocate a port per spec algorithm:

        1. If ws_hash has a live entry → return its port (caller detects no-op).
        2. If ws_hash has a dead entry → try to reuse its previous port first
           (avoids rewriting .mcp.json), with a short bounded retry
           (UPG-RESTART-PORT-WALK-BREAKS-MCP): the port a daemon just
           released can still refuse an immediate rebind for a brief OS
           linger window even through the SO_REUSEADDR-aware `_port_is_free`
           probe above, and a `vectr stop` + `start`/`restart` a moment
           later sits inside that window on every run — retrying absorbs it
           instead of immediately walking to a different port.
        3. Scan from preferred_port upward until a free port binds (up to 100 tries).
        """
        # Imported lazily (not at module top level): `agent.hook_cli` imports
        # `InstanceRegistry` on every `vectr hook <event>` subprocess dispatch
        # (SessionStart/UserPromptSubmit/PreToolUse/PreCompact — see
        # UPG-HOOK-SUBPROCESS-IMPORT-TAX / tests/test_hook_import_cost.py)
        # but only ever calls `.get(...)`, never `find_free_port`. A
        # module-level `from agent.config import ...` here would force
        # `agent.config`'s full ~40-section YAML load onto that hot path for
        # every hook invocation even though it's only needed by `start`/
        # `restart`, which already pay for `agent.config` elsewhere.
        from agent.config import (
            INSTANCE_REGISTRY_PORT_REUSE_RETRY_ATTEMPTS,
            INSTANCE_REGISTRY_PORT_REUSE_RETRY_DELAY_S,
        )

        entry = self._read().get(ws_hash)

        if entry is not None:
            pid, port = entry["pid"], entry["port"]
            if _is_pid_alive(pid):
                return port  # caller should treat as no-op
            for attempt in range(INSTANCE_REGISTRY_PORT_REUSE_RETRY_ATTEMPTS):
                if _port_is_free(port):
                    return port
                if attempt < INSTANCE_REGISTRY_PORT_REUSE_RETRY_ATTEMPTS - 1:
                    time.sleep(INSTANCE_REGISTRY_PORT_REUSE_RETRY_DELAY_S)

        for offset in range(100):
            candidate = preferred_port + offset
            if _port_is_free(candidate):
                return candidate

        raise RuntimeError(
            f"No free port found in range {preferred_port}–{preferred_port + 99}"
        )
