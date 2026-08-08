"""OS-level priority governor for bulk indexing work (UPG-INDEX-RESOURCE-GOVERNOR).

Indexing is CPU-heavy (parallel chunking + batched embedding) and, left
unclamped, runs at full multi-core priority alongside interactive work
sharing the same machine (an editor's own language server, the OS, other
apps). `agent.indexer._core.CodeIndexer` wraps its embed/upsert batch loops
with the two independent, cooperating mechanisms this module provides:

  1. `lowered_priority(enabled, ...)` — a restore-on-exit context manager
     that asks the OS to schedule the CURRENT thread at a lower priority
     class for the duration of the `with` block. macOS: QoS class via
     `pthread_set_qos_class_self_np` (ctypes into libSystem — no compiled
     extension needed). Linux: per-thread niceness via
     `os.setpriority(os.PRIO_PROCESS, <native tid>, ...)` — each Linux
     thread is its own scheduling entity, unlike a process-wide nice value.
     Any other platform, or any failure along the way (missing symbol,
     sandboxed/denied syscall, unknown QoS name), degrades to a no-op —
     indexing must never abort because a priority call failed.

  2. `pace(work_elapsed_s, duty_cycle, min_batch_seconds)` — a deterministic
     post-batch `time.sleep()`, sized as a pure function of the batch's own
     measured wall-clock work time, so the indexing thread yields the CPU
     for the "off" portion of each duty cycle. This is a uniform structural
     transform applied identically to every batch regardless of its
     content — never a function of query text or chunk content — so it is
     not a query-side heuristic.

Both mechanisms are gated by `agent.config.INDEX_GOVERNOR_ENABLED` and the
`--foreground-fast` / `VECTR_FOREGROUND_FAST` override at the call site
(`agent/indexer/_core.py`), not inside this module — this module only knows
how to lower priority and how to pace; it has no opinion on when to.
"""
from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# macOS QoS classes (<sys/qos.h>) — only used when sys.platform == "darwin".
# ---------------------------------------------------------------------------

_QOS_CLASS_USER_INTERACTIVE = 0x21
_QOS_CLASS_USER_INITIATED = 0x19
_QOS_CLASS_DEFAULT = 0x15
_QOS_CLASS_UTILITY = 0x11
_QOS_CLASS_BACKGROUND = 0x09

_MACOS_QOS_BY_NAME: dict[str, int] = {
    "user_interactive": _QOS_CLASS_USER_INTERACTIVE,
    "user_initiated": _QOS_CLASS_USER_INITIATED,
    "default": _QOS_CLASS_DEFAULT,
    "utility": _QOS_CLASS_UTILITY,
    "background": _QOS_CLASS_BACKGROUND,
}


@contextlib.contextmanager
def lowered_priority(
    enabled: bool, *, macos_qos_class: str, linux_nice_increment: int,
):
    """Lower the CURRENT thread's OS scheduling priority for the duration of
    the `with` block, best-effort, and always restore it on exit — including
    on an exception raised inside the block.

    Restore-on-exit (rather than a permanent change) matters because the
    on-demand `/v1/index` REST route dispatches bulk indexing onto
    Starlette's SHARED, reusable request threadpool (see
    `agent/chroma_dispatch.py`'s module docstring) — a change that outlived
    this context would leak a lowered priority onto unrelated future work
    scheduled on the same recycled worker thread.

    `enabled=False` is a plain no-op — no syscalls — the fast path for
    `--foreground-fast` / `VECTR_FOREGROUND_FAST` or
    `INDEX_GOVERNOR_ENABLED=false`, restoring today's unthrottled behaviour
    exactly.
    """
    if not enabled:
        yield
        return

    restore = _lower_current_thread_priority(macos_qos_class, linux_nice_increment)
    try:
        yield
    finally:
        if restore is not None:
            try:
                restore()
            except Exception:
                logger.debug("index governor: failed to restore thread priority", exc_info=True)


def _lower_current_thread_priority(
    macos_qos_class: str, linux_nice_increment: int,
) -> Callable[[], None] | None:
    """Best-effort priority lowering for the calling thread. Returns a
    zero-arg restore callable, or None if lowering did not take effect
    (unsupported platform, or the call failed) — callers check for None
    rather than relying on the attempt itself raising, so an unrecognised
    platform degrades to "did nothing" rather than a hard error."""
    if sys.platform == "darwin":
        return _lower_macos_qos(macos_qos_class)
    if sys.platform.startswith("linux"):
        return _lower_linux_niceness(linux_nice_increment)
    logger.debug(
        "index governor: no priority mechanism for platform %r — running unclamped",
        sys.platform,
    )
    return None


def _lower_macos_qos(qos_class_name: str) -> Callable[[], None] | None:
    target = _MACOS_QOS_BY_NAME.get(qos_class_name)
    if target is None:
        logger.warning(
            "index governor: unknown macos_qos_class %r — running unclamped", qos_class_name,
        )
        return None

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.pthread_self.restype = ctypes.c_void_p
        libc.pthread_get_qos_class_np.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_int),
        ]
        libc.pthread_get_qos_class_np.restype = ctypes.c_int
        libc.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint32, ctypes.c_int]
        libc.pthread_set_qos_class_self_np.restype = ctypes.c_int

        self_thread = libc.pthread_self()
        current_class = ctypes.c_uint32(0)
        current_relprio = ctypes.c_int(0)
        rc = libc.pthread_get_qos_class_np(
            self_thread, ctypes.byref(current_class), ctypes.byref(current_relprio),
        )
        if rc != 0:
            logger.debug(
                "index governor: pthread_get_qos_class_np failed (rc=%d) — running unclamped", rc,
            )
            return None
        rc = libc.pthread_set_qos_class_self_np(target, 0)
        if rc != 0:
            logger.debug(
                "index governor: pthread_set_qos_class_self_np failed (rc=%d) — running unclamped", rc,
            )
            return None
    except (OSError, AttributeError, ValueError):
        logger.debug("index governor: macOS QoS clamp unavailable — running unclamped", exc_info=True)
        return None

    saved_class = current_class.value

    def _restore() -> None:
        libc.pthread_set_qos_class_self_np(saved_class, 0)

    return _restore


def _lower_linux_niceness(nice_increment: int) -> Callable[[], None] | None:
    try:
        tid = threading.get_native_id()
        current = os.getpriority(os.PRIO_PROCESS, tid)
        os.setpriority(os.PRIO_PROCESS, tid, current + nice_increment)
    except (OSError, AttributeError):
        logger.debug("index governor: setpriority unavailable — running unclamped", exc_info=True)
        return None

    def _restore() -> None:
        try:
            os.setpriority(os.PRIO_PROCESS, tid, current)
        except OSError:
            logger.debug("index governor: failed to restore thread niceness", exc_info=True)

    return _restore


def pace(work_elapsed_s: float, duty_cycle: float, min_batch_seconds: float) -> None:
    """Sleep after a unit of indexing work so the thread spends roughly
    `duty_cycle` of wall-clock time working and `1 - duty_cycle` idle,
    yielding the CPU to interactive work between batches.

    Deterministic and content-blind: `work_elapsed_s` (the batch's own
    measured wall-clock work time) is the only input — never a function of
    query text, file content, or any classification of what was indexed, so
    this is a uniform structural transform, not a query-side heuristic.

    A `duty_cycle` of 1.0 never sleeps (pacing dialed off entirely without
    touching the priority-clamp mechanism above). A batch whose own work
    took less than `min_batch_seconds` never sleeps either — this keeps
    tiny batches (a handful of files in a test fixture, the tail batch of a
    real run) from paying a pacing sleep that would dwarf the work it is
    supposed to be proportional to.
    """
    if duty_cycle >= 1.0 or work_elapsed_s < min_batch_seconds:
        return
    duty_cycle = max(duty_cycle, 1e-6)  # guard divide-by-zero from a misconfigured 0.0
    sleep_s = work_elapsed_s * (1.0 - duty_cycle) / duty_cycle
    if sleep_s > 0:
        time.sleep(sleep_s)
