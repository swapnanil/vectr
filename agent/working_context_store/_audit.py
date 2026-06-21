"""
Rotating audit logger for vectr working-context events.

Logs remember/recall/index/forget events to ~/.vectr/audit.log.
Rotates at 10 MB; keeps 3 backups. Disabled if VECTR_AUDIT_LOG="" is set.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


def _get_audit_logger() -> logging.Logger:
    """Return the vectr audit logger (lazy-initialised, singleton per process)."""
    name = "vectr.audit"
    log = logging.getLogger(name)
    if log.handlers:
        return log

    log_path_str = os.getenv("VECTR_AUDIT_LOG", str(Path.home() / ".vectr" / "audit.log"))
    if not log_path_str:
        log.addHandler(logging.NullHandler())
        return log

    log_path = Path(log_path_str)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        str(log_path), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def audit(event: str, **kwargs) -> None:
    """Write one audit log entry: `event key=val key=val …`"""
    try:
        parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
        _get_audit_logger().info(" ".join(parts))
    except Exception:
        pass  # audit failures must never crash the main path
