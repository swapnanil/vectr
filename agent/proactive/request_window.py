"""Anthropic Messages request <-> ProactiveWindow, and cache-safe injection.

Two jobs, both deterministic:

1. `assemble_window` turns a Messages request body into a `ProactiveWindow`
   (recent user/assistant text + file-path/symbol anchors from tool traffic).
   Extraction is keyed on known tool-input fields only — never a regex scan of
   free text for path- or identifier-shaped strings.

2. `append_context_block` injects a packed context string as the NEWEST content,
   appended AFTER the last `cache_control` breakpoint, so every existing
   prompt-cache prefix (tools, system, earlier messages) stays byte-identical
   and keeps hitting. Earlier messages are never mutated or reordered. The
   injected block carries no `cache_control` of its own (it varies per request,
   so it is deliberately left uncached and reprocessed each turn).
"""
from __future__ import annotations

import copy
import json

from agent.proactive.types import ProactiveWindow

# Tool-input keys whose values are file paths (exact-key extraction only).
_FILE_PATH_KEYS = ("file_path", "path", "notebook_path")
# Tool-input keys whose value, when a bare identifier token, names a symbol.
_SYMBOL_KEYS = ("name", "symbol", "query", "pattern")


def _is_identifier(token: str) -> bool:
    """Deterministic is-identifier check (CamelCase / snake_case / dotted
    Class.method). A structural token test, not a semantic classification."""
    if not token or len(token) > 128:
        return False
    parts = token.split(".")
    for part in parts:
        if not part:
            return False
        if not (part[0].isalpha() or part[0] == "_"):
            return False
        if not all(ch.isalnum() or ch == "_" for ch in part):
            return False
    return True


def _extract_anchors(tool_input: dict, file_paths: list[str], symbols: list[str]) -> None:
    if not isinstance(tool_input, dict):
        return
    for key in _FILE_PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            if val not in file_paths:
                file_paths.append(val)
    for key in _SYMBOL_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and _is_identifier(val.strip()):
            sym = val.strip()
            if sym not in symbols:
                symbols.append(sym)


def _block_text(block: dict, include_thinking: bool) -> str:
    btype = block.get("type")
    if btype == "text":
        return str(block.get("text") or "")
    if btype == "thinking" and include_thinking:
        return str(block.get("thinking") or "")
    return ""


def assemble_window(
    body: dict,
    *,
    max_records: int = 20,
    max_chars: int = 2000,
    include_thinking: bool = False,
) -> ProactiveWindow:
    """Build a bounded, in-memory window from a Messages request body.

    The window is a pure function of the body plus the bounds — no persistence,
    no network. Bounded by record count AND character budget so cost stays flat
    regardless of conversation length.
    """
    if not isinstance(body, dict):
        return ProactiveWindow()
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ProactiveWindow()

    recent = messages[-max_records:] if max_records > 0 else messages
    fragments: list[str] = []
    file_paths: list[str] = []
    symbols: list[str] = []

    for msg in recent:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                fragments.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("text", "thinking"):
                txt = _block_text(block, include_thinking)
                if txt.strip():
                    fragments.append(txt)
            elif btype == "tool_use":
                name = str(block.get("name") or "")
                if name:
                    fragments.append(f"tool:{name}")
                _extract_anchors(block.get("input") or {}, file_paths, symbols)

    text = "\n".join(fragments).strip()
    # Keep the most recent tail within the character budget (recency matters most).
    if max_chars > 0 and len(text) > max_chars:
        text = text[-max_chars:]
    return ProactiveWindow(text=text, file_paths=file_paths, symbols=symbols)


# ---------------------------------------------------------------------------
# Cache-safe injection
# ---------------------------------------------------------------------------

def _system_blocks(body: dict) -> list:
    system = body.get("system")
    if isinstance(system, list):
        return system
    return []


def cache_prefix_signature(body: dict) -> str:
    """Canonical serialization of every block up to AND INCLUDING the last
    `cache_control` breakpoint, in the wire hierarchy tools -> system ->
    messages. This is the byte-region a cache read must match; a correct
    injection leaves it identical. Used by the cache-stability tests and as an
    internal invariant guard. When no breakpoint exists, returns the tools +
    system prefix (the always-cacheable head), which injection also must not
    touch.
    """
    tools = body.get("tools") if isinstance(body.get("tools"), list) else []
    system = _system_blocks(body)
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []

    # Walk in wire order recording each block; note the index of the last one
    # that carries cache_control.
    ordered: list = []
    last_bp = -1
    for t in tools:
        ordered.append(("tools", t))
        if isinstance(t, dict) and t.get("cache_control") is not None:
            last_bp = len(ordered) - 1
    for s in system:
        ordered.append(("system", s))
        if isinstance(s, dict) and s.get("cache_control") is not None:
            last_bp = len(ordered) - 1
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        blocks = content if isinstance(content, list) else [content]
        for b in blocks:
            ordered.append(("message", b))
            if isinstance(b, dict) and b.get("cache_control") is not None:
                last_bp = len(ordered) - 1

    if last_bp < 0:
        # No message-level breakpoint: the protected prefix is tools + system.
        prefix = [("tools", t) for t in tools] + [("system", s) for s in system]
    else:
        prefix = ordered[: last_bp + 1]
    return json.dumps(prefix, sort_keys=True, ensure_ascii=False, default=str)


def can_append_context(body: dict) -> bool:
    """Whether `append_context_block` can append to this request body.

    PURELY STRUCTURAL. This predicate reads only the request's SHAPE — the
    presence and type of `messages`, and the `role` field of the trailing
    message. It never reads message text, tool inputs, note content, or any
    other payload, and it never classifies, routes, or gates on what the
    conversation is ABOUT. (Same hard rule as `assemble_window`'s exact-key
    anchor extraction above: no query-side heuristics anywhere in the
    request path.)

    Split out as its own predicate so the proxy can ask "would an injection
    land?" BEFORE spending a retrieval on this request (see
    `agent/proactive/proxy.py`'s `_maybe_inject`) — the alternative is
    retrieving, being refused here, and burning the note's cooldown slot on a
    request that structurally cannot receive it (UPG-PROXY-APPEND-BURNS-
    COOLDOWN). `append_context_block` delegates to this function, so there is
    exactly ONE definition of appendability and the pre-check can never drift
    from the append itself.

    Only a trailing `user` message is appendable, and that restriction is
    load-bearing rather than conservative — measured against real editor
    request shapes:

      * The editor's FIRST request ends with a `system`-role message (an
        8.7 KB harness system-reminder) that CARRIES the last `cache_control`
        breakpoint. Appending into the last *user* message there — which sits
        BEFORE that breakpoint — provably changes `cache_prefix_signature()`
        and destroys the cached prefix.
      * Appending into the trailing `system` message itself is cache-safe but
        would give injected content harness/system-role provenance, a
        regression of the role-provenance envelope (see `gate.py`).
      * Synthesising a new trailing `user` message is cache-safe but fabricates
        a conversation turn the user never sent.

    So a non-appendable request is DEFERRED, not reshaped: nothing is
    retrieved, nothing is charged, and the note is delivered on the next
    appendable request instead (one model turn later).
    """
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not messages:
        return False
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return False
    # A content shape `append_context_block` cannot extend is not appendable
    # either — kept here so the pre-check and the append agree exactly.
    return isinstance(last.get("content"), (str, list))


def append_context_block(body: dict, context: str) -> tuple[dict, bool]:
    """Return a copy of `body` with `context` appended as the newest content,
    after the last cache breakpoint. Returns (new_body, injected).

    Injection appends a plain text block (no cache_control) to the LAST message
    when that message is a `user` turn — the canonical newest-content position.
    If there is no appendable trailing user message, the body is returned
    unchanged and injected=False (fail-open: forward as-is rather than risk
    reshaping the conversation). Appendability is decided by
    `can_append_context` above — see there for why only a trailing user turn
    qualifies. The input `body` is never mutated.
    """
    if not context or not context.strip():
        return body, False
    if not can_append_context(body):
        return body, False

    new_body = copy.deepcopy(body)
    last_msg = new_body["messages"][-1]
    content = last_msg.get("content")
    injected_block = {"type": "text", "text": context}

    if isinstance(content, str):
        # A string content carries no cache_control; represent it as the same
        # text block, then append the injected block after it. Earlier
        # (cached) sections are untouched.
        last_msg["content"] = [{"type": "text", "text": content}, injected_block]
    elif isinstance(content, list):
        last_msg["content"] = list(content) + [injected_block]
    else:  # pragma: no cover - `can_append_context` already excludes this shape
        return body, False
    return new_body, True
