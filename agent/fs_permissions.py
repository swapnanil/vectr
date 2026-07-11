"""Filesystem permission helpers for vectr's local state directories.

On a shared host, vectr's cache and state directories hold a plaintext code
index and working-memory notes. Restricting them to owner-only (0700) stops
other OS users on the same machine from reading them at the filesystem level —
the concrete defense for the scope-isolation model on multi-user hosts.

Best-effort and POSIX-scoped: on Windows, os.chmod cannot express a POSIX 0700
mode, so the chmod is effectively a near-no-op there and full-disk encryption /
NTFS ACLs are the right tool instead. A permission-tightening failure must never
stop the daemon, so every call here is guarded.
"""
from __future__ import annotations

from pathlib import Path


def secure_dir(path: str | Path, mode: int = 0o700) -> Path:
    """Create `path` (and parents) if needed, then restrict it to `mode`.

    Idempotent and safe to call on every startup; never raises. Returns the
    Path so callers can chain. Tightens pre-existing directories too.
    """
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return p
    try:
        p.chmod(mode)
    except OSError:
        pass  # e.g. Windows, or a dir we do not own — best effort only
    return p
