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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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
           (avoids rewriting .mcp.json).
        3. Scan from preferred_port upward until a free port binds (up to 100 tries).
        """
        entry = self._read().get(ws_hash)

        if entry is not None:
            pid, port = entry["pid"], entry["port"]
            if _is_pid_alive(pid):
                return port  # caller should treat as no-op
            if _port_is_free(port):
                return port

        for offset in range(100):
            candidate = preferred_port + offset
            if _port_is_free(candidate):
                return candidate

        raise RuntimeError(
            f"No free port found in range {preferred_port}–{preferred_port + 99}"
        )
