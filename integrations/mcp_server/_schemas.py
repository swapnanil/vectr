"""MCP tool schema definitions — exploration, memory write, memory read, utility."""
from __future__ import annotations

MCP_SERVER_INFO = {
    "name": "vectr",
    "version": "2.0.0",
    "description": "Zero-config semantic codebase search with layered memory (L1 map + L2 symbols + L3 content)",
    "capabilities": {"tools": {}},
}

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
