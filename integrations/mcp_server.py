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

MCP_TOOLS = [
    # ---- L3: content retrieval (original) ----
    {
        "name": "vectr_search",
        "description": (
            "Semantically search the indexed codebase and return the most relevant code chunks. "
            "Use this instead of grep/ripgrep when you need to find code by meaning, not exact text. "
            "Returns function/class bodies with file paths and line numbers."
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
        "description": "Get the current indexing status of the Vectr daemon.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    # ---- L1: codebase map ----
    {
        "name": "vectr_map",
        "description": (
            "Return the codebase passport. "
            "If a passport has been saved: returns a compact (~300 token) plain-English summary instantly. "
            "If not yet saved: returns raw structural metadata (dir tree, languages, frameworks) "
            "and instructs you to call vectr_map_save with your synthesised summary. "
            "Call this FIRST in every session to orient yourself without reading any files."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "vectr_map_save",
        "description": (
            "Save your synthesised codebase summary as the passport. "
            "Call this after vectr_map returns raw metadata (first session on a new codebase). "
            "Write a concise plain-English summary covering: what the codebase does, tech stack, "
            "key modules and their purpose, entry points, domain terms. "
            "Aim for ~200-350 tokens. All future vectr_map calls return this instantly."
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
            "Find where a symbol (function, class, method) is defined, without returning code content. "
            "Returns file path + line number + kind. Use before vectr_search to narrow your target. "
            "Example: vectr_locate('EvaluateSegments') → 'targeting/segment/evaluator.go:45'"
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
            "Trace the call graph for a symbol: who calls it, what it calls. "
            "Use to understand dependencies before modifying a function. "
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
    # ---- Memory layer ----
    {
        "name": "vectr_remember",
        "description": (
            "Offload a working note to Vectr's persistent memory. "
            "Use this to record what you've learned about a task so you can drop the code chunks from your context. "
            "Notes survive IDE restarts and are recalled next session with vectr_recall."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The working note to store (1-3 sentences: what you know, key files, what's left)",
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
            "Retrieve working notes from a previous session. "
            "Call this at the start of a session to pick up where you left off. "
            "Optionally filter by query text or tags."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to search for in note content",
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
            "Ask Vectr what code chunks you can safely drop from your context window. "
            "Vectr guarantees it can return any listed chunk in <50ms. "
            "Call this when your context is getting large to free space without losing information."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "vectr_snapshot",
        "description": (
            "Save a named snapshot of your current session: all working notes + retrieved code context. "
            "Use before ending a session so vectr_recall can restore your exact state next time."
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
            "Use this to find a snapshot_id before restoring, or to review past sessions."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def handle_tools_list() -> dict:
    return {"tools": MCP_TOOLS}


def handle_tools_call(tool_name: str, arguments: dict, service: Any) -> dict:
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
        text = (
            f"Vectr status\n"
            f"  Indexed files  : {status['indexed_files']}\n"
            f"  Total chunks   : {status['total_chunks']}\n"
            f"  Symbols indexed: {status.get('symbol_count', 'n/a')}\n"
            f"  Last indexed   : {status['last_indexed']}\n"
            f"  Embed model    : {status['embed_model']}\n"
            f"  Workspace      : {status['workspace_root']}"
        )
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
