"""Session state and turn-count nudge logic for MCP tool dispatch."""
from __future__ import annotations

from agent.config import (
    BEHAVIOR_REMEMBER_NUDGE_THRESHOLD as _REMEMBER_NUDGE_THRESHOLD,
    BEHAVIOR_REMEMBER_NUDGE_COOLDOWN as _REMEMBER_NUDGE_COOLDOWN,
)

# ---------------------------------------------------------------------------
# Adaptive tool registration — session state
#
# Exploration tools are always visible. Memory tools (recall, snapshot,
# snapshot_list, forget, evict_hint) are only added once either:
#   a) vectr_status() shows notes_count > 0 for the session, OR
#   b) the agent calls vectr_remember() for the first time.
#
# Sessions without an ID get the full tool list for backwards compatibility.
# ---------------------------------------------------------------------------
_memory_enabled_sessions: set[str] = set()

# ---------------------------------------------------------------------------
# Turn-count vectr_remember nudge
#
# After _REMEMBER_NUDGE_THRESHOLD vectr tool calls without a vectr_remember,
# the next vectr_search / vectr_locate / vectr_trace response appends an
# imperative reminder. The counter resets on every vectr_remember call.
# After the threshold fires, it re-fires every _REMEMBER_NUDGE_COOLDOWN
# calls so a single dismissal cannot silence it for the rest of the session.
#
# Fires only when session_id is known (no-op for anonymous sessions).
# Fires only in discovery tool responses (search/locate/trace) — not in
# status, recall, map, or remember responses — because those are the moments
# when the agent has just found something worth saving.
# ---------------------------------------------------------------------------
# UPG-12.1: _REMEMBER_NUDGE_THRESHOLD / _REMEMBER_NUDGE_COOLDOWN are sourced
# from agent/config.yaml (behavior.remember_nudge) via agent/config.py —
# imported above as aliases so all existing call sites work without change.
_session_calls_since_save: dict[str, int] = {}


def _increment_calls_since_save(session_id: str | None) -> int:
    """Increment and return the call count since last vectr_remember."""
    if not session_id:
        return 0
    n = _session_calls_since_save.get(session_id, 0) + 1
    _session_calls_since_save[session_id] = n
    return n


def _reset_calls_since_save(session_id: str | None) -> None:
    if session_id:
        _session_calls_since_save[session_id] = 0


def _should_nudge_remember(session_id: str | None) -> bool:
    if not session_id:
        return False
    n = _session_calls_since_save.get(session_id, 0)
    if n < _REMEMBER_NUDGE_THRESHOLD:
        return False
    excess = n - _REMEMBER_NUDGE_THRESHOLD
    return excess == 0 or excess % _REMEMBER_NUDGE_COOLDOWN == 0


def _remember_nudge_text(session_id: str | None) -> str:
    n = _session_calls_since_save.get(session_id or "", 0)
    return (
        f"\n\n─── vectr_remember reminder ({n} calls since last save) ───\n"
        "If you have found anything non-obvious — a key function body, a design invariant, "
        "an unexpected pattern, a partial stub — call vectr_remember now with the actual code "
        "(not a file pointer). "
        "This note survives /compact and any future session on this codebase. "
        "One call now = no re-discovery later."
    )


def enable_memory_for_session(session_id: str | None) -> None:
    if session_id:
        _memory_enabled_sessions.add(session_id)


def is_memory_enabled(session_id: str | None) -> bool:
    """True if memory tools should be exposed for this session."""
    if session_id is None:
        return True  # no session tracking → show all (backwards compat)
    return session_id in _memory_enabled_sessions
