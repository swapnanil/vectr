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
# T13: Adaptive tool registration — session state
#
# Exploration tools are always visible. Memory tools (recall, snapshot,
# snapshot_list, forget, evict_hint) are only added once either:
#   a) vectr_status() shows notes_count > 0 for the session, OR
#   b) the agent calls vectr_remember() for the first time.
#
# Sessions without an ID get the full tool list for backwards compatibility.
# ---------------------------------------------------------------------------
_memory_enabled_sessions: set[str] = set()


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
                    "description": "Number of results to return (default: 10, max: 50)",
                    "default": 10,
                },
                "language": {
                    "type": "string",
                    "description": "Filter to a specific language (python, javascript, typescript, go, rust, java)",
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
            "stored from prior sessions). "
            "Call once at the start of any session to decide whether vectr_recall is worth calling: "
            "if notes_count > 0 and you are continuing prior work, call vectr_recall(query=...). "
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
            },
            "required": ["name"],
        },
    },
]  # end _EXPLORATION_TOOLS

# Memory tools: exposed on-demand (see is_memory_enabled / enable_memory_for_session)
_MEMORY_TOOLS = [
    {
        "name": "vectr_remember",
        "description": (
            "Store a working note that survives IDE restarts and session boundaries. "
            "Use when you've discovered something non-obvious that would save significant re-exploration later: "
            "a non-obvious file path, a call pattern, a gotcha, a partial stub, task progress. "
            "Store the note BEFORE dropping the related code from context — it replaces the need to re-read. "
            "Do NOT store obvious or easily re-derivable facts (e.g. 'the main file is main.py'). "
            "Retrieve in future sessions with vectr_recall(query='what you need')."
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
            },
            "required": ["content"],
        },
    },
    {
        "name": "vectr_recall",
        "description": (
            "Retrieve stored notes from previous sessions. "
            "Use when continuing prior work on this codebase AND vectr_status() confirmed notes_count > 0. "
            "Pass a targeted query to retrieve only the notes relevant to your current task — "
            "do NOT call with no query unless you need everything (a broad recall inflates context unnecessarily). "
            "Do NOT call if vectr_status() returned notes_count == 0, or if this is a fresh task "
            "with no continuity from prior sessions."
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
        "name": "vectr_evict_hint",
        "description": (
            "Vectr tells you which retrieved code chunks you can safely drop from your context window — "
            "any evicted chunk is guaranteed to be retrievable in <50ms on demand via vectr_search. "
            "Use when your context is large and you need to reclaim space without losing information. "
            "This is the bidirectional half of the vectr protocol: "
            "the AI tells vectr what to store (vectr_remember), "
            "and vectr tells the AI what it can safely forget (vectr_evict_hint). "
            "NOT needed on short sessions — only when context is approaching the limit."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
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

# Full list for serialization / backwards compat
MCP_TOOLS = _EXPLORATION_TOOLS + _MEMORY_TOOLS


def handle_tools_list(session_id: str | None = None, service: Any = None) -> dict:
    """Return tools appropriate for this session.

    Sessions with an ID start with exploration tools only; memory tools are
    added once the session has notes or has called vectr_remember (T13).
    Sessions without an ID get the full list for backwards compatibility.
    """
    # If the session already has notes (checked via service), pre-enable memory tools
    if session_id and service and not is_memory_enabled(session_id):
        try:
            notes_count = service.count_notes() if hasattr(service, "count_notes") else 0
            if notes_count > 0:
                enable_memory_for_session(session_id)
        except Exception:
            pass

    if is_memory_enabled(session_id):
        return {"tools": MCP_TOOLS}
    return {"tools": _EXPLORATION_TOOLS}


def handle_tools_call(
    tool_name: str,
    arguments: dict,
    service: Any,
    session_id: str | None = None,
) -> dict:
    """Dispatch an MCP tool call. `service` is the VectrService instance."""

    # ---- vectr_search ----
    if tool_name == "vectr_search":
        query = arguments.get("query", "")
        n_results = min(int(arguments.get("n_results", 10)), 50)
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

        sections: list[str] = []

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
                trace_lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}")
            sections.append("\n".join(trace_lines))

        # L3 chunks
        sections.append(_format_search_results(results, query, query_ms, service.total_chunks))

        # routing footnote
        sections.append(f"─── Routing: {decision.rationale} ───")

        content_text = "\n\n".join(sections)

        # auto-append eviction hint when context pressure crosses the threshold
        if service.should_evict():
            hint = service.eviction_hint()
            if hint:
                content_text += f"\n\n─── Context management hint ───\n{hint}"

        return {"content": [{"type": "text", "text": content_text}], "isError": False}

    # ---- vectr_status ----
    if tool_name == "vectr_status":
        status = service.status()
        notes_count = status.get("notes_count", 0)

        # T13: if this session has prior notes, enable memory tools immediately
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
        if status.get("semantic_weight") is not None:
            lines.append(
                f"  Retrieval      : semantic={status['semantic_weight']:.0%}  "
                f"bm25={status['bm25_weight']:.0%}  "
                f"graph_first={status['graph_first']}"
            )
            if status.get("strategy_rationale"):
                lines.append(f"  Strategy why   : {status['strategy_rationale']}")
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
        symbols = service.locate_with_snippets(name, limit=limit)
        text = service.format_locate(symbols, name)
        if service.should_evict():
            hint = service.eviction_hint()
            if hint:
                text += f"\n\n─── Context management hint ───\n{hint}"
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
        trace_result = service.trace_with_snippets(name, direction=direction, limit=limit)
        text = service.format_trace(trace_result, name)
        if service.should_evict():
            hint = service.eviction_hint()
            if hint:
                text += f"\n\n─── Context management hint ───\n{hint}"
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
        note_id = service.remember(content=content, tags=tags, priority=priority)
        # T13: first vectr_remember call enables memory tools for this session
        enable_memory_for_session(session_id)
        return {
            "content": [{"type": "text", "text": f"Stored note #{note_id}. You can safely drop the related code chunks from your context."}],
            "isError": False,
        }

    # ---- vectr_recall ----
    if tool_name == "vectr_recall":
        query = arguments.get("query") or None
        tags = arguments.get("tags") or None
        priority = arguments.get("priority") or None
        limit = int(arguments.get("limit", 10))
        text = service.recall(query=query, tags=tags, priority=priority, limit=limit)
        if service.should_evict():
            hint = service.eviction_hint()
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
            "content": [{"type": "text", "text": f"Session snapshot saved: {snapshot_id}\nLabel: {label}\nRestore with vectr_recall (your notes will be there next session)."}],
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

    return _mcp_error(f"Unknown tool: {tool_name}")


def _format_search_results(results, query: str, query_ms: int, chunks_searched: int) -> str:
    if not results:
        return f"No results found for: {query}"
    lines = [f"Found {len(results)} results for '{query}' ({query_ms}ms, {chunks_searched} chunks searched)\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{'─' * 60}")
        lines.append(f"[{i}] {r.file_path}  lines {r.lines}  score {r.score:.3f}")
        if r.symbol_name:
            lines.append(f"    symbol: {r.symbol_name}  language: {r.language}")
        lines.append("")
        lines.append(r.content)
        lines.append("")
    return "\n".join(lines)


def _mcp_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }
