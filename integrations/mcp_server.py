"""MCP protocol implementation — exposes vectr tools."""
from __future__ import annotations

import time
from typing import Any

MCP_SERVER_INFO = {
    "name": "vectr",
    "version": "2.0.0",
    "description": "Zero-config semantic codebase search with layered memory (L1 map + L2 symbols + L3 content)",
    "capabilities": {"tools": {}},
}

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
_REMEMBER_NUDGE_THRESHOLD = 10
_REMEMBER_NUDGE_COOLDOWN = 5
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


# Exploration tools: always shown
_EXPLORATION_TOOLS = [
    # ---- L3: content retrieval ----
    {
        "name": "vectr_search",
        "description": (
            "Use when you know WHAT you're looking for but not WHERE it is or WHAT it's called. "
            "Hybrid semantic + BM25 search — finds code by concept, behaviour, or description. "
            "Returns function/class bodies with file paths and line numbers. "
            "NOT when you already know the symbol name — use vectr_locate instead. "
            "NOT when you want call relationships — use vectr_trace instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language or code description of what you're looking for",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, max: 50)",
                    "default": 5,
                },
                "language": {
                    "type": "string",
                    "description": "Filter to a specific indexed language (e.g. python, rust, c, zig). "
                                   "Any language the index contains is accepted; an unindexed "
                                   "language returns no results plus the list of indexed languages.",
                    "nullable": True,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "vectr_status",
        "description": (
            "Returns index health (files, chunks, embed model) AND notes_count (number of notes "
            "stored — earlier in this session or in prior sessions). "
            "Call once at the start of any session to decide whether vectr_recall is worth calling: "
            "if notes_count > 0, call vectr_recall(query=...) to retrieve relevant notes. "
            "If notes_count == 0, skip recall entirely. "
            "Also useful when vectr_search returns nothing and you suspect indexing is still running."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    # ---- L1: codebase map ----
    {
        "name": "vectr_map",
        "description": (
            "Use at the start of a session on an UNFAMILIAR codebase to get a structural overview "
            "without reading any files. "
            "If a passport has been saved: returns a compact (~300 token) plain-English summary instantly. "
            "If not yet saved: returns raw structural metadata (dir tree, languages, frameworks) "
            "and instructs you to call vectr_map_save with your synthesised summary. "
            "NOT needed if you already know the codebase structure. "
            "NOT a substitute for vectr_recall — call vectr_status first to check for prior notes."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "vectr_map_save",
        "description": (
            "Save your synthesised codebase summary as the permanent passport. "
            "Call this ONLY after vectr_map returned raw metadata — i.e. on your first visit to a codebase. "
            "NOT when vectr_map already returned a saved summary (passport already exists). "
            "Write a concise plain-English summary: what the codebase does, tech stack, "
            "key modules, entry points, domain terms. Aim for ~200-350 tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Your plain-English codebase summary (~200-350 tokens)",
                },
            },
            "required": ["summary"],
        },
    },
    # ---- L2: symbol graph ----
    {
        "name": "vectr_locate",
        "description": (
            "Use when you know the SYMBOL NAME but not which file it's in. "
            "Returns file path + line number + kind for every matching definition. "
            "NOT when you're searching by concept or behaviour — use vectr_search instead. "
            "NOT when you want call relationships — use vectr_trace instead. "
            "Example: vectr_locate('EvaluateSegments') → 'targeting/evaluator.go:45'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name or partial name to locate (case-sensitive partial match)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 10)",
                    "default": 10,
                },
                "caller_file": {
                    "type": "string",
                    "description": "Absolute path of the file containing the call site. "
                                   "Enables same-module and import-chain fallback strategies.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "vectr_trace",
        "description": (
            "Use when you know the SYMBOL NAME and need to understand its callers or callees "
            "before modifying it. Traverses the call graph in both directions. "
            "NOT when you don't know the symbol name yet — use vectr_search or vectr_locate first. "
            "NOT when you just want the definition location — use vectr_locate instead. "
            "Example: vectr_trace('EvaluateSegments') → 'Called by: RequestBid() in bidder/auction.go'"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name to trace",
                },
                "direction": {
                    "type": "string",
                    "description": "'callers' (who calls this), 'callees' (what it calls), or 'both' (default)",
                    "default": "both",
                    "enum": ["callers", "callees", "both"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results per direction (default: 20)",
                    "default": 20,
                },
                "include_builtins": {
                    "type": "boolean",
                    "description": (
                        "Include language builtins/stdlib in the 'calls' list (len, assert, "
                        "Ok, Some, malloc, …). Default false — only repo-internal calls are "
                        "shown, with a count of how many builtins were hidden."
                    ),
                    "default": False,
                },
            },
            "required": ["name"],
        },
    },
]  # end _EXPLORATION_TOOLS

# Always-on memory write tools — visible from turn 1, no notes required.
# vectr_remember: only way to create notes; hiding it is a catch-22.
# vectr_evict_hint: fires on retrieval pressure, not note count.
_MEMORY_WRITE_TOOLS = [
    {
        "name": "vectr_remember",
        "description": (
            "Save a working note and recall it on demand in <50ms — "
            "whether later this session, through /compact, or in a future session. "
            "Use the moment you discover something non-obvious: a key file path, a call pattern, a gotcha, "
            "a partial stub, task progress. "
            "Store the actual code or finding — vectr returns it in <50ms; "
            "re-reading the file costs tokens and turns. "
            "Do NOT store obvious or easily re-derivable facts (e.g. 'the main file is main.py'). "
            "Retrieve with vectr_recall(query='what you need') — any time, same session or later."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The note to store. Store whatever you would need to avoid re-reading the file later. "
                        "If you found a function you'll call or modify — paste its signature and body. "
                        "If you found a pattern you'll need to replicate — paste the pattern. "
                        "If you found a location — include the file:line AND the relevant excerpt, not just the pointer. "
                        "Prose descriptions send the next conversation back to the file; actual code does not."
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Topic tags for later recall (e.g. ['segment-targeting', 'wip'])",
                },
                "priority": {
                    "type": "string",
                    "description": "Note priority: 'high' | 'medium' (default) | 'low'",
                    "default": "medium",
                    "enum": ["high", "medium", "low"],
                },
                "kind": {
                    "type": "string",
                    "description": (
                        "Memory kind, controlling how the note is injected (default 'finding'): "
                        "'directive' = a must-never-miss rule, injected unconditionally every session; "
                        "'task' = current-work context; 'gotcha' = a file/path-anchored caveat; "
                        "'finding' = a relevance-ranked learning; 'reference' = a pointer (URL/ticket)."
                    ),
                    "default": "finding",
                    "enum": ["directive", "task", "gotcha", "finding", "reference"],
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "vectr_evict_hint",
        "description": (
            "Vectr lists which retrieved code chunks it can re-retrieve in <50ms — "
            "you do not need to re-read those files. "
            "Use at the exploration → implementation transition to avoid unnecessary re-reads. "
            "This is the reverse signal in the vectr protocol: "
            "the AI saves findings (vectr_remember), "
            "vectr signals what it can recall instantly (vectr_evict_hint). "
            "NOT needed on short sessions — most useful after many vectr_search/vectr_locate calls."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]  # end _MEMORY_WRITE_TOOLS

# Memory read/manage tools: only useful when notes already exist.
# Exposed once notes_count > 0 or vectr_remember has been called this session.
_MEMORY_TOOLS = [
    {
        "name": "vectr_recall",
        "description": (
            "Retrieve notes stored earlier in this session or in prior sessions. "
            "Use when vectr_status() confirmed notes_count > 0 — notes may have been stored this session "
            "or in a previous one; either way they are immediately useful. "
            "Pass a targeted query to retrieve only the notes relevant to your current task — "
            "do NOT call with no query unless you need everything (a broad recall inflates context unnecessarily). "
            "Do NOT call if vectr_status() returned notes_count == 0."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language query to retrieve only relevant notes "
                        "(e.g. 'set cartesian product frozenset' returns notes about that task only). "
                        "Omit only when you need all stored notes."
                    ),
                    "nullable": True,
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags",
                },
                "priority": {
                    "type": "string",
                    "description": "Filter by priority: 'high' | 'medium' | 'low'",
                    "nullable": True,
                },
                "kind": {
                    "type": "string",
                    "description": "Filter to one memory kind: 'directive' | 'task' | 'gotcha' | 'finding' | 'reference'",
                    "nullable": True,
                    "enum": ["directive", "task", "gotcha", "finding", "reference"],
                },
                "boot": {
                    "type": "boolean",
                    "description": (
                        "Boot mode: return ALL directives + high-priority task notes unconditionally "
                        "(no semantic filter, safe on a fresh workspace). Ignores query/tags/priority/kind/limit."
                    ),
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notes to return (default: 10)",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "vectr_snapshot",
        "description": (
            "Seal all current vectr_remember() notes as a named checkpoint. "
            "Use when you've stored multiple notes and want to mark a milestone you can return to "
            "(e.g. 'auth-refactor-wip', 'segment-targeting-done'). "
            "The next time you work on this, vectr_recall will return these notes. "
            "NOT required if you only stored 1-2 notes — vectr_recall retrieves all notes regardless."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Human-readable label for this snapshot (e.g. 'segment-targeting-wip')",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session identifier",
                    "nullable": True,
                },
            },
            "required": ["label"],
        },
    },
    {
        "name": "vectr_snapshot_list",
        "description": (
            "List all saved session snapshots for this workspace, newest first. "
            "Use at session start to find an existing checkpoint if vectr_recall returned nothing "
            "or if you want to resume a specific named session."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "vectr_forget",
        "description": (
            "Delete all working-memory notes for this workspace. "
            "Use when notes are stale after a large refactor, when you want a clean slate, "
            "or when vectr_recall returns notes that are consistently wrong. "
            "Snapshots are preserved — only active notes are removed. This is irreversible."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]  # end _MEMORY_TOOLS

# ingest_traces — not gated by session memory (always available)
_UTILITY_TOOLS = [
    {
        "name": "vectr_ingest_traces",
        "description": (
            "Import runtime trace events into the symbol graph to enrich static call analysis. "
            "Use when you have runtime profiling data (Python sys.settrace output, JSON trace logs) "
            "that reveals dynamic dispatch patterns the static analyser cannot see: decorators, "
            "__getattr__, dependency injection, monkey-patching, etc. "
            "Pass a list of trace events: [{caller, callee, caller_file?, caller_line?}, ...]. "
            "Dynamic edges are stored with edge_type='dynamic' and appear in vectr_trace results. "
            "NOT needed if static analysis (vectr_trace) already shows the call relationships."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "description": "List of trace events. Each event: {caller, callee, caller_file?, caller_line?}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "caller":      {"type": "string", "description": "Calling function/symbol name"},
                            "callee":      {"type": "string", "description": "Called function/symbol name"},
                            "caller_file": {"type": "string", "description": "Source file of the caller"},
                            "caller_line": {"type": "integer", "description": "Line number of the call site"},
                        },
                        "required": ["caller", "callee"],
                    },
                },
            },
            "required": ["events"],
        },
    },
]

# Full list for serialization / backwards compat
MCP_TOOLS = _EXPLORATION_TOOLS + _MEMORY_WRITE_TOOLS + _MEMORY_TOOLS + _UTILITY_TOOLS


def handle_tools_list(session_id: str | None = None, service: Any = None) -> dict:
    """Return tools appropriate for this session.

    Always shown: exploration tools + vectr_remember + vectr_evict_hint.
    Gated on notes existing: vectr_recall, vectr_forget, vectr_snapshot, vectr_snapshot_list.
    """
    # Pre-enable memory read tools if notes already exist
    if session_id and service and not is_memory_enabled(session_id):
        try:
            notes_count = service.count_notes() if hasattr(service, "count_notes") else 0
            if notes_count > 0:
                enable_memory_for_session(session_id)
        except Exception:
            pass

    base = _EXPLORATION_TOOLS + _MEMORY_WRITE_TOOLS + _UTILITY_TOOLS
    if is_memory_enabled(session_id):
        return {"tools": base + _MEMORY_TOOLS}
    return {"tools": base}


def handle_tools_call(
    tool_name: str,
    arguments: dict,
    service: Any,
    session_id: str | None = None,
) -> dict:
    """Dispatch an MCP tool call. `service` is the VectrService instance."""
    # Count every tool call — used by the tool-call-count eviction trigger
    try:
        service._eviction_advisor.increment_tool_call()
    except Exception:
        pass

    # Count per-tool calls for benchmark metrics (accurate across parent + sub-agents)
    try:
        service.increment_call_count(tool_name)
    except Exception:
        pass

    # Increment turn-count nudge counter for all tool calls
    _increment_calls_since_save(session_id)

    # Count retrieval-specific calls (search/locate/trace) for the retrieval-count trigger
    if tool_name in ("vectr_search", "vectr_locate", "vectr_trace"):
        try:
            service._eviction_advisor.increment_retrieval_call()
        except Exception:
            pass

    # ---- vectr_search ----
    if tool_name == "vectr_search":
        query = arguments.get("query", "")
        n_results = min(int(arguments.get("n_results", 5)), 50)
        language = arguments.get("language") or None

        if not query:
            return _mcp_error("query is required")

        if service.total_chunks == 0:
            return {
                "content": [{"type": "text", "text": "Vectr is still indexing the codebase. Try again in a few moments."}],
                "isError": False,
            }

        results, query_ms, decision, aug_symbols, aug_trace = service.search_routed(
            query, n_results=n_results, language=language
        )

        # Record chunks so the eviction advisor can reference them in the hint
        try:
            service._eviction_advisor.record_results(results)
        except Exception:
            pass

        sections: list[str] = []

        # UPG-3.1: a language filter that matched nothing because that language
        # isn't indexed (not merely a query miss) should tell the caller what IS
        # indexable, rather than silently returning empty.
        if language and not results:
            indexed = service.indexed_languages()
            if language.lower() not in {l.lower() for l in indexed}:
                avail = ", ".join(indexed) if indexed else "(none yet)"
                sections.append(
                    f"─── No results: language={language!r} is not indexed ───\n"
                    f"Indexed languages: {avail}.\n"
                    f"Re-run without the language filter, or use one of the above."
                )

        # L1 map hint for structural queries
        if decision.include_map_hint:
            map_text = service.get_map()
            if map_text and "Set ANTHROPIC_API_KEY" not in map_text:
                sections.append(f"─── Codebase map (structural context) ───\n{map_text}")

        # L2 symbol augmentation — snippets included so AI reads code directly
        if aug_symbols:
            sym_lines = [f"─── Symbol locations (query type: {decision.query_type.value}) ───"]
            for s in aug_symbols:
                sym_lines.append(f"  [{s.kind}] {s.name}  {s.file_path}:{s.start_line}")
                if s.snippet:
                    for ln in s.snippet.splitlines()[:6]:  # brief preview in search results
                        sym_lines.append(f"    {ln}")
            sections.append("\n".join(sym_lines))

        if aug_trace:
            trace_lines = ["─── Callers ───"]
            for e in aug_trace:
                suffix = f"  ×{e.call_count}" if getattr(e, "call_count", 1) > 1 else ""
                trace_lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}{suffix}")
            sections.append("\n".join(trace_lines))

        # L3 chunks
        sections.append(_format_search_results(results, query, query_ms, service.total_chunks))

        # routing footnote
        sections.append(f"─── Routing: {decision.rationale} ───")

        content_text = "\n\n".join(sections)

        # auto-append eviction hint only on a FRESH context-pressure escalation
        # (UPG-7.1) — gated so it can't repeat on every response
        hint = service.auto_eviction_hint()
        if hint:
            content_text += f"\n\n─── Context management hint ───\n{hint}"

        if _should_nudge_remember(session_id):
            content_text += _remember_nudge_text(session_id)

        return {"content": [{"type": "text", "text": content_text}], "isError": False}

    # ---- vectr_status ----
    if tool_name == "vectr_status":
        status = service.status()
        notes_count = status.get("notes_count", 0)

        # if this session has prior notes, enable memory tools immediately
        if notes_count > 0:
            enable_memory_for_session(session_id)

        recall_hint = (
            f"  → call vectr_recall(query=...) to retrieve them"
            if notes_count > 0
            else "  → no prior notes; skip vectr_recall"
        )
        lines = [
            "Vectr status",
            f"  Indexed files  : {status['indexed_files']}",
            f"  Total chunks   : {status['total_chunks']}",
            f"  Symbols indexed: {status.get('symbol_count', 'n/a')}",
            f"  Prior notes    : {notes_count}{recall_hint}",
            f"  Last indexed   : {status['last_indexed']}",
            f"  Embed model    : {status['embed_model']}",
            f"  Workspace      : {status['workspace_root']}",
        ]

        # Per-language coverage + symbol availability (UPG-3.3). Tells the agent
        # where locate/trace will work (symbol graph) vs. where to use search only.
        langs = status.get("languages") or []
        if langs:
            lines.append("  Languages      : (✓ = locate/trace available; others are search-only)")
            for L in langs[:8]:
                mark = "✓ locate/trace" if L["symbols"] else "search-only"
                lines.append(f"      {L['language']:<12} {L['files']:>5} files   {mark}")
            if len(langs) > 8:
                lines.append(f"      … +{len(langs) - 8} more")
            top = langs[0]
            if not top["symbols"]:
                lines.append(
                    f"  → Primary language ({top['language']}) has no symbol graph — "
                    "prefer vectr_search; locate/trace will be empty here."
                )
        if status.get("semantic_weight") is not None:
            lines.append(
                f"  Retrieval      : semantic={status['semantic_weight']:.0%}  "
                f"bm25={status['bm25_weight']:.0%}  "
                f"graph_first={status['graph_first']}"
            )
            if status.get("strategy_rationale"):
                lines.append(f"  Strategy why   : {status['strategy_rationale']}")

        # inject adaptive instruction style hint at session start
        try:
            style = service.suggest_instruction_style()
            style_hints = {
                "additive":    "Use vectr tools when they'd be faster than reading files — see CLAUDE.md.",
                "directed":    "This is a large/unfamiliar codebase. Use vectr_map → vectr_search → vectr_locate before reading files.",
                "memory-only": "Prior notes exist. Call vectr_recall(query=...) first; use search only to fill gaps.",
            }
            hint = style_hints.get(style, "")
            if hint:
                lines.append(f"  Tool style     : [{style}] {hint}")
        except Exception:
            pass

        text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_map ----
    if tool_name == "vectr_map":
        text = service.get_map()
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_map_save ----
    if tool_name == "vectr_map_save":
        summary = arguments.get("summary", "").strip()
        if not summary:
            return _mcp_error("summary is required")
        service.save_map(summary)
        return {
            "content": [{"type": "text", "text": (
                f"Passport saved ({len(summary)} chars). "
                "Future vectr_map calls will return this summary instantly."
            )}],
            "isError": False,
        }

    # ---- vectr_locate ----
    if tool_name == "vectr_locate":
        name = arguments.get("name", "").strip()
        if not name:
            return _mcp_error("name is required")
        limit = int(arguments.get("limit", 10))
        caller_file = arguments.get("caller_file", "").strip() or None
        symbols = service.locate_with_snippets(name, limit=limit, caller_file=caller_file)
        text = service.format_locate(symbols, name)
        hint = service.auto_eviction_hint()  # UPG-7.1: gated, not every response
        if hint:
            text += f"\n\n─── Context management hint ───\n{hint}"
        if _should_nudge_remember(session_id):
            text += _remember_nudge_text(session_id)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_trace ----
    if tool_name == "vectr_trace":
        name = arguments.get("name", "").strip()
        if not name:
            return _mcp_error("name is required")
        direction = arguments.get("direction", "both")
        if direction not in ("callers", "callees", "both"):
            direction = "both"
        limit = int(arguments.get("limit", 20))
        include_builtins = bool(arguments.get("include_builtins", False))
        trace_result = service.trace_with_snippets(
            name, direction=direction, limit=limit, include_builtins=include_builtins
        )
        text = service.format_trace(trace_result, name)
        hint = service.auto_eviction_hint()  # UPG-7.1: gated, not every response
        if hint:
            text += f"\n\n─── Context management hint ───\n{hint}"
        if _should_nudge_remember(session_id):
            text += _remember_nudge_text(session_id)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_remember ----
    if tool_name == "vectr_remember":
        content = arguments.get("content", "").strip()
        if not content:
            return _mcp_error("content is required")
        tags = arguments.get("tags") or None
        priority = arguments.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        kind = arguments.get("kind", "finding")
        note_id = service.remember(content=content, tags=tags, priority=priority, kind=kind)
        # reset the turn-count nudge and enable memory tools for this session
        _reset_calls_since_save(session_id)
        enable_memory_for_session(session_id)
        return {
            "content": [{"type": "text", "text": f"Stored note #{note_id}. Recall with vectr_recall — <50ms, verbatim, any time."}],
            "isError": False,
        }

    # ---- vectr_recall ----
    if tool_name == "vectr_recall":
        query = arguments.get("query") or None
        tags = arguments.get("tags") or None
        priority = arguments.get("priority") or None
        kind = arguments.get("kind") or None
        boot = bool(arguments.get("boot", False))
        limit = int(arguments.get("limit", 10))
        text = service.recall(query=query, tags=tags, priority=priority, limit=limit, kind=kind, boot=boot)
        hint = service.auto_eviction_hint()  # UPG-7.1: gated, not every response
        if hint:
            text += f"\n\n─── Context management hint ───\n{hint}"
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_evict_hint ----
    if tool_name == "vectr_evict_hint":
        hint = service.eviction_hint()
        if not hint:
            hint = "No retrieved chunks to evict. Context window is clean."
        return {"content": [{"type": "text", "text": hint}], "isError": False}

    # ---- vectr_snapshot ----
    if tool_name == "vectr_snapshot":
        label = arguments.get("label", "").strip()
        if not label:
            return _mcp_error("label is required")
        session_id = arguments.get("session_id") or None
        snapshot_id = service.snapshot_session(label=label, session_id=session_id)
        return {
            "content": [{"type": "text", "text": f"Snapshot saved: {snapshot_id}\nLabel: {label}\nNotes are available via vectr_recall any time — later in this session or in future sessions."}],
            "isError": False,
        }

    # ---- vectr_snapshot_list ----
    if tool_name == "vectr_snapshot_list":
        snapshots = service.list_snapshots()
        if not snapshots:
            text = "No snapshots saved for this workspace yet. Use vectr_snapshot to save one."
        else:
            import datetime
            lines = [f"Saved snapshots ({len(snapshots)}):\n"]
            for s in snapshots:
                ts = datetime.datetime.fromtimestamp(s["created_at"]).strftime("%Y-%m-%d %H:%M")
                lines.append(f"  {s['snapshot_id']}  [{ts}]  {s['label']}")
            lines.append("\nUse vectr_recall to retrieve the notes from any session.")
            text = "\n".join(lines)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---- vectr_forget ----
    if tool_name == "vectr_forget":
        deleted = service.forget_all()
        return {
            "content": [{"type": "text", "text": f"Deleted {deleted} working-memory notes. Starting fresh."}],
            "isError": False,
        }

    # ---- vectr_ingest_traces ----
    if tool_name == "vectr_ingest_traces":
        events = arguments.get("events")
        if not isinstance(events, list):
            return _mcp_error("events must be a list of trace event dicts")
        result = service.ingest_traces(events)
        return {
            "content": [{"type": "text", "text": (
                f"Ingested {result['ingested']} dynamic call edges into the symbol graph. "
                f"({result['skipped_invalid']} events skipped — missing caller or callee field.) "
                "Dynamic edges now appear in vectr_trace results alongside static edges."
            )}],
            "isError": False,
        }

    return _mcp_error(f"Unknown tool: {tool_name}")


def _format_search_results(results, query: str, query_ms: int, chunks_searched: int) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Found {len(results)} results for '{query}' ({query_ms}ms, {chunks_searched} chunks searched)\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{'─' * 60}")
        dup = f"  (+{r.dup_count} more identical)" if getattr(r, "dup_count", 0) else ""
        lines.append(f"[{i}] {r.file_path}  lines {r.lines}  score {r.score:.3f}{dup}")
        if r.symbol_name:
            # UPG-11.4: include symbol line-range so caller can expand to full definition
            # without a blind whole-file re-read: Read(file_path, offset=symbol_start_line-1)
            sym_range = ""
            s_start = getattr(r, "symbol_start_line", 0)
            s_end = getattr(r, "symbol_end_line", 0)
            if s_start and s_end:
                sym_range = f"  [lines {s_start}–{s_end}]"
            lines.append(f"    symbol: {r.symbol_name}{sym_range}  language: {r.language}")
        lines.append("")
        content_lines = r.content.splitlines()
        if len(content_lines) > 80:
            lines.append("\n".join(content_lines[:80]))
            start = str(r.lines).split("-")[0] if "-" in str(r.lines) else str(r.lines)
            lines.append(f"... {len(content_lines) - 80} more lines — Read({r.file_path}, offset={start}) for full context")
        else:
            lines.append(r.content)
        lines.append("")
    return "\n".join(lines)


def _mcp_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }
