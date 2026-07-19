"""MCP tool schema definitions — exploration, memory write, memory read, utility."""
from __future__ import annotations

MCP_SERVER_INFO = {
    "name": "vectr",
    "version": "1.2.0",
    "description": "Zero-config semantic code search + persistent working memory for AI agents",
    "capabilities": {"tools": {}},
}

# Exploration tools: always shown
_EXPLORATION_TOOLS = [
    # ---- L3: content retrieval ----
    {
        "name": "vectr_search",
        "annotations": {
            "title": "Semantic code search",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
                    "description": (
                        "Number of results to return (default: 5, max: 50). "
                        "Prefer 1–2 for a specific symbol or single-answer lookup "
                        "(\"where is X defined\", \"the function that does Y\") — the top "
                        "hit is usually the answer and fewer results cost far fewer tokens. "
                        "Widen to 5+ only for exploratory or survey queries where several "
                        "distinct implementations are genuinely useful."
                    ),
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
        "name": "vectr_fetch",
        "annotations": {
            "title": "Restore code chunks by id",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Deterministic re-fetch of a code chunk by its exact id — no embedding, "
            "no rerank, just the chunk. Every vectr_search/vectr_locate/vectr_trace "
            "result carries its chunk's id (the `file:start-end` shown in the result "
            "header). Use this to restore a chunk that was cleared from your context "
            "(by tool-result eviction, context compaction, or a context-editing tombstone) "
            "instead of re-running vectr_search or re-reading the whole file. "
            "NOT for finding NEW content — use vectr_search for that."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Chunk ids to restore, exactly as shown in a prior "
                                    "search/locate/trace result (e.g. 'src/auth.py:10-20').",
                },
            },
            "required": ["ids"],
        },
    },
    {
        "name": "vectr_status",
        "annotations": {
            "title": "Index and memory status",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Returns index health (files, chunks, embed model) AND notes_count (number of notes "
            "stored — earlier in this session or in prior sessions). "
            "Call once at the start of any session to decide whether vectr_recall is worth calling: "
            "if notes_count > 0, call vectr_recall(query=...) to retrieve relevant notes. "
            "If notes_count == 0, skip recall entirely. "
            "If your session already shows auto-injected Working Notes (vectr hooks), those ARE "
            "the recall output — do not re-call vectr_recall for them. "
            "Also useful when vectr_search returns nothing and you suspect indexing is still running."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    # ---- L1: codebase map ----
    {
        "name": "vectr_map",
        "annotations": {
            "title": "Codebase overview",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
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
        "annotations": {
            "title": "Save codebase passport",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Save your synthesised codebase summary as the permanent passport. "
            "Call this ONLY after vectr_map returned raw metadata — i.e. on your first visit to a codebase. "
            "NOT when vectr_map already returned a saved summary (passport already exists). "
            "Write a concise plain-English summary: what the codebase does, tech stack, "
            "key modules, entry points, domain terms. Aim for ~200-350 tokens. "
            "Does NOT overwrite an existing passport by default — if one is already saved, the call "
            "is a no-op and returns the existing summary; pass overwrite=true to replace it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Your plain-English codebase summary (~200-350 tokens)",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Set true to replace an already-saved passport. Default false.",
                    "default": False,
                },
            },
            "required": ["summary"],
        },
    },
    # ---- L2: symbol graph ----
    {
        "name": "vectr_locate",
        "annotations": {
            "title": "Locate symbol definition",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Use when you know the SYMBOL NAME but not which file it's in. "
            "Returns file path + line number + kind for every matching definition. "
            "NOT when you're searching by concept or behaviour — use vectr_search instead. "
            "NOT when you want call relationships — use vectr_trace instead. "
            "Example: vectr_locate(name='EvaluateSegments') → 'targeting/evaluator.go:45'"
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
        "annotations": {
            "title": "Symbol call graph",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Use when you know the SYMBOL NAME and need to understand its callers or callees "
            "before modifying it. Traverses the call graph in both directions. "
            "NOT when you don't know the symbol name yet — use vectr_search or vectr_locate first. "
            "NOT when you just want the definition location — use vectr_locate instead. "
            "Example: vectr_trace(name='EvaluateSegments') → 'Called by: RequestBid() in bidder/auction.go'"
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
        "annotations": {
            "title": "Store working-memory note",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": (
            "Save a working note and recall it on demand in <50ms — "
            "whether later this session, through context compaction, or in a future session. "
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
                        "'finding' = a relevance-ranked learning; 'reference' = a pointer (URL/ticket). "
                        "When a new kind='task' note replaces an earlier checkpoint (the work moved on, "
                        "the old note is no longer the current state), pass supersedes=<old note_id> so "
                        "the stale checkpoint stops firing at every future session-start instead of "
                        "piling up alongside the new one."
                    ),
                    "default": "finding",
                    "enum": ["directive", "task", "gotcha", "finding", "reference"],
                },
                "title": {
                    "type": "string",
                    "description": (
                        "Short label for index-tier display (optional, max ~80 chars). "
                        "If omitted, the first non-empty line of content is used as the title. "
                        "Shown in vectr_recall() index output so you can identify notes without reading their bodies."
                    ),
                    "default": "",
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "Optional: your identifier when called by a subagent or orchestrator in a "
                        "multi-agent workflow (e.g. 'coder-2'). Never inferred — set it explicitly if "
                        "you want attribution. Shown in vectr_recall() index output as a tag, e.g. "
                        "'[#12] task/high (coder-2) · title'. A subagent should call vectr_remember "
                        "with its findings BEFORE finishing so the orchestrator can recall them "
                        "instead of re-reading the subagent's full transcript."
                    ),
                    "default": "",
                },
                "triggers": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Optional: explicit overrides for WHEN this note should resurface. Each "
                        "entry may declare 'path' (a glob, e.g. 'src/api/**'), 'event' (one of: "
                        "session-start, prompt-submit, pre-edit, pre-run, pre-commit, "
                        "post-compaction), 'symbol' (a code symbol name — matches when the file "
                        "targeted by the current lifecycle moment defines or references it, "
                        "resolved exactly against the same symbol graph vectr_locate/vectr_trace "
                        "use), and/or 'semantic' (true — matches at prompt-submit when the "
                        "prompt's meaning is close enough to this note's own content, judged by a "
                        "fixed similarity threshold per kind; no keyword matching involved), plus "
                        "optional 'not_before' (epoch seconds), 'expires_visibility' (epoch "
                        "seconds after which the note fades in ranking but still fires), and "
                        "'cooldown' (seconds between re-fires). Entries within one object are "
                        "AND'd together (e.g. 'path' + 'symbol' both required); multiple entries "
                        "in the array are OR'd. Omit this entirely (recommended) and the note's "
                        "kind gets a sensible default: 'directive' fires at session-start and "
                        "after context compaction; 'task' fires at session-start; 'gotcha' fires "
                        "when the anchored file is about to be edited."
                    ),
                },
                "provenance": {
                    "type": "string",
                    "description": (
                        "How much to trust this note when it resurfaces (default 'agent'): "
                        "'agent' = you recorded this yourself, framed as memory to verify; "
                        "'auto' = captured with no reviewing judgment at all, weakest framing, "
                        "and not allowed together with kind='directive'. 'human' is not settable "
                        "here — a note only becomes 'human'-provenance via an explicit promotion "
                        "after a person reviews it."
                    ),
                    "default": "agent",
                    "enum": ["agent", "auto"],
                },
                "scope": {
                    "type": "string",
                    "description": (
                        "Visibility scope, enforced at recall/trigger time. Omit this to get "
                        "the kind's own default: kind='task' defaults to 'branch' (a git branch "
                        "was actually captured at write time; on a non-git workspace or "
                        "detached HEAD it falls back to 'workspace' instead of silently never "
                        "firing again), kind='gotcha' defaults to 'repo', every other kind "
                        "defaults to 'workspace'. Pass a value explicitly to override the "
                        "kind default, including passing 'workspace' explicitly: 'branch' "
                        "restricts firing to the git branch this note was written on; "
                        "'path-subtree' restricts firing to paths under an anchored directory; "
                        "'session' restricts it to the writing session only; 'repo' behaves like "
                        "'workspace' today (cross-worktree sharing is not yet a distinct store "
                        "boundary)."
                    ),
                    "enum": ["workspace", "repo", "path-subtree", "branch", "session"],
                },
                "anchors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: file paths this note is about. Each path's current content is "
                        "hashed now; if that file changes later, the note still recalls/fires but "
                        "carries a visible staleness caveat naming the changed path."
                    ),
                },
                "supersedes": {
                    "type": "integer",
                    "description": (
                        "Optional: the note_id this new note replaces. The old note is retired "
                        "(excluded from recall and from ever firing again) but kept for audit — "
                        "use this instead of vectr_forget when you want the old note's history "
                        "preserved. Especially important for kind='task' checkpoints: a stale "
                        "task note keeps firing at every session-start forever until it is "
                        "explicitly superseded or forgotten, so pass the old note's id here "
                        "whenever a new task note is really just an update to it."
                    ),
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "vectr_evict_hint",
        "annotations": {
            "title": "List re-retrievable chunks",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Lists the code chunks retrieved by THIS session that are safe to drop from "
            "context — each is re-fetchable verbatim in one deterministic call, and the "
            "response includes the exact vectr_fetch(ids=[...]) re-fetch keys. "
            "Use at the exploration → implementation transition, or when context pressure builds. "
            "This is the reverse signal in the vectr protocol: "
            "the AI saves findings (vectr_remember), "
            "vectr signals what it can restore instantly (vectr_evict_hint). "
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
        "annotations": {
            "title": "Recall working-memory notes",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Retrieve notes stored earlier in this session or in prior sessions. "
            "TWO-TIER RECALL (UPG-RECALL-HIERARCHY): "
            "By default returns a crisp one-line index per note (id + kind/priority + title + age) — "
            "token-bounded, safe to call broadly. "
            "To read a note body: pass note_id=N (expand one note, full body) or detail='full' (all bodies). "
            "Use when vectr_status() confirmed notes_count > 0 — notes may have been stored this session "
            "or in a previous one; either way they are immediately useful. "
            "Pass a targeted query to retrieve only the notes relevant to your current task — "
            "do NOT call with no query unless you need everything."
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
                "note_id": {
                    "type": "integer",
                    "description": (
                        "Expand a single note by ID — returns the full body of that note, ignoring query. "
                        "Use after seeing the index output to read the note you care about. "
                        "Get IDs from the [#N] prefix in index output."
                    ),
                    "nullable": True,
                },
                "detail": {
                    "type": "string",
                    "description": (
                        "Detail level: "
                        "'index' (default) = one-line summary per note (id, kind/priority, title, age) — token-bounded; "
                        "'full' = full note bodies (use when you need to read all matching notes)."
                    ),
                    "default": "index",
                    "enum": ["index", "full"],
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
                "sort_by": {
                    "type": "string",
                    "description": (
                        "Sort order: 'relevance' (semantic/trust order, default), "
                        "'recency' (newest first), 'priority' (high→medium→low then newest)."
                    ),
                    "default": "relevance",
                    "enum": ["relevance", "recency", "priority"],
                },
                "max_age_days": {
                    "type": "number",
                    "description": "Time filter: only return notes created within this many days.",
                    "nullable": True,
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
        "annotations": {
            "title": "Checkpoint working memory",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
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
        "annotations": {
            "title": "List memory checkpoints",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "List all saved session snapshots for this workspace, newest first. "
            "Use at session start to find an existing checkpoint if vectr_recall returned nothing "
            "or if you want to resume a specific named session."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "vectr_forget",
        "annotations": {
            "title": "Delete working-memory notes",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Delete working-memory notes. Pass note_id to delete ONE note — the usual case, "
            "when a note is stale or superseded (ids are the [#N] shown by vectr_recall). "
            "Pass all=true to irreversibly clear EVERY note for this workspace (e.g. after a "
            "large refactor). Calling with no arguments deletes nothing. "
            "Snapshots are preserved — only active notes are removed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the single note to delete (the [#N] id from vectr_recall)",
                },
                "all": {
                    "type": "boolean",
                    "description": "Set true to delete ALL notes for this workspace. Irreversible.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "vectr_promote",
        "annotations": {
            "title": "Promote a note's provenance",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Raise an auto-captured note's trust class to 'agent' — e.g. after this session "
            "has reviewed an auto-captured note and confirmed it still holds. This tool only "
            "takes that one step (auto -> agent); it never promotes a note to 'human', because "
            "deciding that a person has endorsed something is not the agent's call to make. "
            "Human endorsement happens on a user-side surface instead (a CLI/UI a person "
            "operates), not through this tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "integer",
                    "description": "ID of the note to promote (the [#N] id from vectr_recall)",
                },
                "to": {
                    "type": "string",
                    "description": "Target provenance. Only 'agent' is available via this tool (auto -> agent); promoting to 'human' is a user-side action, not available here.",
                    "enum": ["agent"],
                },
            },
            "required": ["note_id", "to"],
        },
    },
]  # end _MEMORY_TOOLS

# ingest_traces — not gated by session memory (always available)
_UTILITY_TOOLS = [
    {
        "name": "vectr_ingest_traces",
        "annotations": {
            "title": "Ingest runtime call edges",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": (
            "Import runtime trace events into the symbol graph to enrich static call analysis. "
            "Use when you have runtime profiling data (Python sys.settrace output, JSON trace logs) "
            "that reveals dynamic dispatch patterns the static analyser cannot see: decorators, "
            "__getattr__, dependency injection, monkey-patching, etc. "
            "Pass a list of trace events: [{caller, callee, caller_file?, caller_line?}, ...]. "
            "Dynamic edges are stored with edge_type='dynamic' and appear in vectr_trace results "
            "marked \"(dynamic)\" so you can tell them apart from statically-discovered calls. "
            "A caller/callee name that matches no indexed symbol is still ingested (it may be "
            "external or runtime-only) but is reported back as a warning — check for typos. "
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

# UPG-STDIO-MEMORY-READY: tools backed entirely by WorkingContextStore (SQLite
# write path + best-effort embedding). These never need the embedder,
# indexer, searcher, watcher, or symbol graph, so they are servable as soon
# as phase 1 of VectrService construction completes -- long before phase 2
# (model load, indexing) finishes. Transports key dispatch on the TOOL NAME
# below and on service readiness state only, never on query text content.
MEMORY_READY_TOOLS = frozenset(
    {
        "vectr_remember",
        "vectr_recall",
        "vectr_forget",
        "vectr_promote",
        "vectr_status",
        "vectr_snapshot",
        "vectr_snapshot_list",
    }
)
