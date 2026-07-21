"""Pydantic v2 request and response models."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class IndexRequest(BaseModel):
    path: str = Field(default=".", description="Absolute or relative path to index")
    force: bool = Field(default=False, description="Force full re-index even if already indexed")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language or code query")
    n_results: int = Field(default=10, ge=1, le=50, description="Number of results to return")
    # UPG-3.1: any indexed language is accepted (no fixed allow-list, no 422).
    # Normalisation (lower/strip, blank→None) lives in CodeSearcher.search — the
    # shared path for both REST and MCP — so behaviour can't diverge by entrypoint.
    language: str | None = Field(
        default=None,
        description="Filter to a specific indexed language (e.g. python, rust, c, zig). "
                    "Any language the index actually contains is accepted; unindexed "
                    "languages return no matches rather than an error.",
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class CodeChunkResult(BaseModel):
    file: str
    lines: str
    symbol: str | None   # None when chunk falls outside any named symbol
    language: str
    score: float
    content: str
    # UPG-11.4: expand-to-symbol affordance — the exact line range of the indexed
    # symbol so callers can read the full definition without a blind whole-file re-read.
    # 0/0 means the chunk was not associated with a named symbol (e.g. a window chunk).
    symbol_start_line: int = 0
    symbol_end_line: int = 0
    # UPG-CTX-EVICT: the exact chunk id — pass verbatim to vectr_fetch /
    # POST /v1/fetch to restore this chunk deterministically, no re-search
    # or file re-read needed.
    id: str = ""
    # UPG-SCORE-DISPLAY-MIXED-SCALE: which scale `score` was drawn from for
    # THIS chunk — "reranker" (a calibrated cross-encoder judgment) or
    # "dense" (a raw bi-encoder cosine similarity). The two scales are not
    # comparable to each other; every result in one response is guaranteed to
    # share the same value (CodeSearcher.search backfills any straggler so a
    # response is never a mix of the two once reranking has run for it).
    score_source: str = "dense"


class SearchResponse(BaseModel):
    results: list[CodeChunkResult]
    query_time_ms: int
    chunks_searched: int
    processing_ms: int
    # UPG-NOTFOUND-FLOOR (F46/F52): true when the query names a concept that
    # has no lexical anchor anywhere in the indexed corpus — at least
    # `ranking.notfound_floor.min_zero_df_tokens` of its content words have
    # zero document frequency across the whole corpus, not just the query's
    # candidate pool. The displayed per-result `score` is a per-query
    # rank-derived value that always looks confident near the top, so this
    # field is the caller's only signal that the whole result set may be a
    # weak/unrelated guess. Results are still returned in full; this never
    # suppresses them.
    low_confidence: bool = False


class IndexResponse(BaseModel):
    indexed_files: int
    total_chunks: int
    processing_ms: int


class FetchRequest(BaseModel):
    ids: list[str] = Field(
        ..., min_length=1,
        description="Chunk ids to re-fetch verbatim — the exact `file:start-end` "
                    "id shown in a search/locate/trace result.",
    )


class FetchEntry(BaseModel):
    id: str
    found: bool
    file_path: str = ""
    lines: str = ""
    symbol: str | None = None
    language: str = ""
    content: str = ""


class FetchResponse(BaseModel):
    results: list[FetchEntry]
    # Shared note, present only when at least one requested id was not found —
    # the most likely cause is the file changed since indexing (the chunk's
    # line range shifted or the symbol was removed), not a transient error.
    note: str | None = None
    processing_ms: int


class LanguageStat(BaseModel):
    """Per-language index coverage + symbol availability (UPG-3.3).

    `symbols=True` means locate/trace work for this language; otherwise it is
    search-only. Lets a REST consumer route the same way the MCP agent does.
    """
    language: str
    files: int
    chunks: int
    symbols: bool


class StatusResponse(BaseModel):
    indexed_files: int
    total_chunks: int
    last_indexed: str
    embed_model: str
    workspace_root: str
    symbol_count: int = 0
    languages: list[LanguageStat] = []
    notes_count: int = 0
    # Symbol-graph build trust signals (UPG-8.7): complete = no files failed
    # extraction; failed_files counts those skipped, so a partial graph is visible.
    symbol_graph_complete: bool = False
    symbol_graph_failed_files: int = 0
    # UPG-STATUS-STALE-GRAPH: staleness signals for the symbol-graph fields above.
    # `stale` = the persisted graph was built by a different toolchain (vectr/
    # parser/model/schema change), left incomplete, or never built — so the
    # symbol_count / symbol_graph_complete served may not reflect current state.
    # `rebuild_in_progress` = a full rebuild is overwriting the graph right now,
    # so those fields still reflect the OLD graph until it lands.
    symbol_graph_stale: bool = True
    symbol_graph_rebuild_in_progress: bool = False
    processing_ms: int
    # Adaptive retrieval strategy. Always populated by VectrService.status()
    # (UPG-8.2): the config-declared defaults before the first index-time
    # fingerprint, the fingerprint-derived values after. Optional here only
    # so a stale/partial mock in a test doesn't fail response validation.
    semantic_weight: float | None = None
    bm25_weight: float | None = None
    graph_first: bool | None = None
    recommended_embed_model: str | None = None
    strategy_rationale: str | None = None
    # Daemon mode: "full" (default), "memory-only" (no indexing/watcher), or
    # "search-only" (no working-memory layer — see UPG-SEARCH-ONLY-MODE)
    mode: str = "full"
    # Set only when the working-memory note vectors are stamped with a
    # different embed model than the one currently configured (should not
    # persist past startup — migration runs synchronously — see
    # UPG-NOTES-EMBED-MIGRATION). None means the two agree.
    notes_embed_model_mismatch: str | None = None
    # UPG-CLI-DAEMON-VERSION-SKEW: package version (+ short git SHA when the
    # daemon runs from a git checkout), stamped once at process startup. The
    # CLI recomputes the same stamp per invocation and warns on mismatch.
    # Optional so a stale/partial mock in a test doesn't fail validation.
    version_stamp: str | None = None
    # Watcher backlog observability (UPG-WATCHER-PRESSURE-GOVERNOR): whether
    # a sustained multi-file edit stream has coalesced into burst mode,
    # outstanding paths not yet re-indexed (per-file debounce + burst
    # collection + queued batch), whether a batch worker is currently
    # running, and the last batch's wall-clock duration — so runaway churn
    # is visible instead of silent.
    watcher_burst_mode: bool = False
    watcher_pending_files: int = 0
    watcher_batch_running: bool = False
    watcher_last_batch_duration_ms: int = 0
    # Hook-driven injection counters (UPG-HOOK-INJECT-OBSERVABILITY): how many
    # times each hook kind's recall actually returned notes to inject, since
    # this daemon process started. Only counts hook-declared calls (see
    # RecallRequest.hook_event); direct vectr_recall/`vectr recall` calls are
    # never counted here. Empty dict means no hook has injected notes yet.
    hook_injection_counts: dict[str, int] = {}
    # Proactive-context injection counters (UPG-PRO): how many times each
    # channel (e.g. "proxy") actually injected packed context since startup.
    # Empty until proactive context has injected anything.
    proactive_injection_counts: dict[str, int] = {}
    # Effective ambient (hook-channel) proactive master opt-in, visible before
    # any injection has happened. The proxy channel injects by launch consent
    # regardless of this flag (UPG-PROXY-HIDDEN-MASTER-SWITCH).
    proactive_enabled: bool = False
    # Org-wide artifact-cache metrics (UPG-PRO caching): hits / misses /
    # hit_rate / entries / est_tokens_saved, or None when the cache is off.
    artifact_cache: dict | None = None
    # UPG-REST-STARVATION: true while bulk index work (an explicit
    # index()/startup index, or the watcher's coalesced batch worker) is
    # running. Backed by a non-blocking lock read + in-memory watcher flags —
    # this field is always cheap to compute, even mid-reindex.
    reindex_in_progress: bool = False
    # UPG-STDIO-MEMORY-READY: additive warm-up signals. `fully_ready` is True
    # once phase 2 (embedder/indexer/searcher/watcher/symbol-graph) has
    # completed — search/locate/trace/map/fetch are gated on it across every
    # transport. `embedder_ready` is True once the embedding model has
    # loaded/attached — vectr_recall's lexical-fallback notice is gated on
    # it. Both default True so an older/partial mock in a test still
    # validates, and both are True immediately for any daemon that wasn't
    # constructed with defer_search_init=True.
    fully_ready: bool = True
    embedder_ready: bool = True
    # UPG-TASK-SUPERSEDES-HYGIENE: count of live (non-superseded) kind="task"
    # notes older than MEMORY_HYGIENE_STALE_TASK_WARN_AGE_DAYS, plus the
    # oldest such note's id (None if there are none or the count is 0). A
    # nudge only — task notes are never auto-superseded or expired.
    stale_task_count: int = 0
    stale_task_oldest_id: int | None = None


class HealthResponse(BaseModel):
    status: str
    embed_model: str
    # Sourced from the same VectrService.last_indexed property as
    # /v1/status so the two endpoints never disagree on freshness (UPG-8.2).
    last_indexed: str


# ---------------------------------------------------------------------------
# Codebase passport
# ---------------------------------------------------------------------------

class MapSaveRequest(BaseModel):
    summary: str = Field(..., min_length=1, description="AI-written plain-English codebase summary")
    # UPG-6.2: vectr_map_save must not silently clobber an existing passport —
    # the caller must explicitly opt in to replace it.
    overwrite: bool = False


class MapSaveResponse(BaseModel):
    message: str
    processing_ms: int
    # False when a passport already existed and overwrite was not set — the
    # request was a no-op and `message` carries the existing summary (UPG-6.2).
    saved: bool = True


# ---------------------------------------------------------------------------
# Memory / working context
# ---------------------------------------------------------------------------

_VALID_KINDS = ("directive", "task", "gotcha", "finding", "reference", "decision")

# TRIGGER-ENGINE wave 1 (bm2-design-skeleton.md §1/§2/§5) — mirrors
# agent.working_context_store._types.SCOPE_VALUES/PROVENANCE_VALUES. Kept as
# a local closed-vocabulary tuple here rather than imported, matching this
# module's existing convention for _VALID_KINDS above (the REST/MCP request
# schema validates independently of the store's own internal validation).
_SCOPE_VALUES = ("workspace", "repo", "path-subtree", "branch", "session")
_PROVENANCE_VALUES = ("human", "agent", "auto")
# TRIGGER-ENGINE wave 2a — mirrors
# agent.working_context_store._types.EVENT_VALUES, same local-tuple
# convention as _SCOPE_VALUES/_PROVENANCE_VALUES above.
_EVENT_VALUES = ("session-start", "prompt-submit", "pre-edit", "pre-run", "pre-commit", "post-compaction")
# 'human' is not settable via vectr_remember/RememberRequest — only 'agent'
# (default) or 'auto' may be declared at write time; promoting to 'human' is
# a separate, explicit call (PromoteRequest below) once a person has endorsed
# the note.
_REST_PROVENANCE_VALUES = ("agent", "auto")


class RememberRequest(BaseModel):
    content: str = Field(..., min_length=1, description="Working note to store")
    tags: list[str] | None = Field(default=None, description="Topic tags")
    priority: str = Field(default="medium", description="high | medium | low")
    kind: str = Field(default="finding", description="directive | task | gotcha | finding | reference | decision")
    session_id: str | None = Field(default=None)
    title: str = Field(default="", description="Short label for index-tier display (optional; derived from first content line if empty)")
    agent: str = Field(
        default="",
        description=(
            "Optional caller-declared identifier for the agent/subagent authoring this "
            "note (e.g. 'coder-2'), for multi-agent shared-memory attribution. Never "
            "inferred. Shown in recall index lines when present (e.g. "
            "'[#12] task/high (coder-2) · title'); absent renders exactly as before."
        ),
    )
    triggers: list[dict] | None = Field(
        default=None,
        description=(
            "TRIGGER-ENGINE wave 1: explicit P/E/T trigger overrides. Each "
            "entry declares 'path' (glob) and/or 'event' (session-start | "
            "prompt-submit | pre-edit | pre-run | pre-commit | "
            "post-compaction) plus optional 'not_before' / "
            "'expires_visibility' / 'cooldown' (T modifiers, never fire "
            "alone). Entries are OR'd together across the list; omitted or "
            "empty uses this note's kind's default trigger bundle, computed "
            "at evaluation time."
        ),
    )
    provenance: str = Field(
        default="agent",
        description=(
            "Trust/endorsement class (TRIGGER-ENGINE wave 1): 'agent' "
            "(default — an AI session recorded this, framed as memory to "
            "verify) or 'auto' (captured with no reviewing judgment, weakest "
            "framing; not allowed on kind='directive'). 'human' is not "
            "settable from this tool — see the separate promote call."
        ),
    )
    scope: str | None = Field(
        default=None,
        description=(
            "workspace | repo | path-subtree | branch | session. Omit "
            "(None) to get this note's kind's own default scope, resolved "
            "at write time (UPG-TRIGGER-SCOPE-KIND-DEFAULTS): kind='task' "
            "-> 'branch' (falls back to 'workspace' when no git branch was "
            "actually captured), kind='gotcha' -> 'repo', every other kind "
            "-> 'workspace'. Pass a value explicitly, including 'workspace', "
            "to override the kind default."
        ),
    )
    anchors: list[str] | None = Field(
        default=None,
        description=(
            "Workspace-relative (or absolute) file paths this note is "
            "anchored to. Each path's current content hash is computed at "
            "write time and re-checked at recall/fire time — a mismatch adds "
            "a visible staleness caveat, the note still fires/recalls "
            "(never silently dropped)."
        ),
    )
    supersedes: int | None = Field(
        default=None,
        description=(
            "note_id this new note explicitly tombstones. The target note "
            "is excluded from recall/fire from now on but retained, "
            "unchanged, for audit. Rejected if the target does not exist in "
            "this workspace."
        ),
    )

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in ("high", "medium", "low"):
            raise ValueError("priority must be high, medium, or low")
        return v

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in _VALID_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_VALID_KINDS)}")
        return v

    @field_validator("provenance")
    @classmethod
    def validate_provenance(cls, v: str) -> str:
        if v not in _REST_PROVENANCE_VALUES:
            raise ValueError(
                f"provenance must be one of: {', '.join(_REST_PROVENANCE_VALUES)} "
                "('human' is not settable here — promote a note to 'human' separately)"
            )
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str | None) -> str | None:
        # None means "omitted" (UPG-TRIGGER-SCOPE-KIND-DEFAULTS) — passed
        # straight through so the store resolves this note's kind's default
        # scope at write time; only a NON-None value is checked against
        # SCOPE_VALUES here.
        if v is not None and v not in _SCOPE_VALUES:
            raise ValueError(f"scope must be one of: {', '.join(_SCOPE_VALUES)}")
        return v


class RememberResponse(BaseModel):
    note_id: int
    message: str
    processing_ms: int


class RecallRequest(BaseModel):
    query: str | None = Field(default=None)
    tags: list[str] | None = Field(default=None)
    priority: str | None = Field(default=None)
    kind: str | None = Field(default=None, description="Filter by kind: directive | task | gotcha | finding | reference | decision")
    limit: int = Field(default=10, ge=1, le=100)
    boot: bool = Field(default=False, description="Boot mode (UPG-9.2): unconditional directives + high-priority tasks; ignores query/tags/priority/kind/limit")
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0, description="Relevance cutoff (UPG-5.1): drop semantic matches below this cosine similarity; only applies with a query")
    file_path: str | None = Field(default=None, description="Path-anchored recall (UPG-9.6): notes recorded against this file (basename/relpath match); for the PreToolUse gotcha hook")
    max_age_days: float | None = Field(default=None, gt=0.0, description="Time filter (UPG-RECALL-HIERARCHY): only return notes created within this many days")
    sort_by: str = Field(default="relevance", description="Sort order (UPG-RECALL-HIERARCHY, UPG-DECISION-TIMELINE): relevance | recency | priority | chronological (oldest-first; index lines render the creation date)")
    detail: str = Field(default="index", description="Detail level (UPG-RECALL-HIERARCHY): 'index' = one-line summary per note (default, token-bounded); 'full' = full bodies")
    note_id: int | None = Field(default=None, description="Expand a single note by ID (UPG-RECALL-HIERARCHY): returns full body, ignores query")
    surface: str = Field(
        default="mcp",
        description=(
            "Caller surface (UPG-CLI-RECALL-HINT): 'mcp' (default) — the "
            "response's expand hint uses the MCP tool-call form, correct for "
            "the MCP dispatch path and for hook-injected context (both are "
            "ultimately read by the editor's LLM). 'cli' — the hint uses the "
            "real shell form; set by `vectr recall`'s own request, whose "
            "reader is a human terminal."
        ),
    )
    hook_event: str | None = Field(
        default=None,
        description=(
            "Caller-declared hook kind (UPG-HOOK-INJECT-OBSERVABILITY): "
            "'SessionStart' | 'UserPromptSubmit' | 'PreToolUse', set only by "
            "`vectr hook`'s own request. When set and this call actually "
            "returns notes, the daemon counts one injection under this hook "
            "kind (surfaced in `vectr status`). None (the default — used by "
            "direct vectr_recall/`vectr recall` calls) records nothing; only "
            "harness-injected recall is counted."
        ),
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "TRIGGER-ENGINE wave 2a: the calling session's identity — for a "
            "hook-injected call this is the harness's own hook-JSON session "
            "id (the same id across a /compact). Enforces scope='session' "
            "notes, threads into the per-session cumulative injection "
            "budget and fire-dedup ledger, and (combined with the current "
            "git branch) enforces scope='branch' notes. None (the default) "
            "reproduces today's ledger-less, budget-less, scope-unenforced "
            "behaviour exactly."
        ),
    )
    events: list[str] | None = Field(
        default=None,
        description=(
            "TRIGGER-ENGINE wave 2a: lifecycle event(s) this recall stands "
            "in for, OR'd together — e.g. ['session-start', "
            "'post-compaction'] for the first SessionStart call after a "
            "/compact, or ['prompt-submit'] for a per-turn recall. Only "
            "notes with an EXPLICIT triggers[] override on one of these "
            "events (or, in boot mode, a kind-default bundle) fire; absent "
            "(the default), boot mode implies ['session-start'] and every "
            "other mode fires nothing extra beyond its own query/file_path "
            "match."
        ),
    )

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_VALID_KINDS)}")
        return v

    @field_validator("surface")
    @classmethod
    def validate_surface(cls, v: str) -> str:
        if v not in ("mcp", "cli"):
            raise ValueError("surface must be one of: mcp, cli")
        return v

    @field_validator("hook_event")
    @classmethod
    def validate_hook_event(cls, v: str | None) -> str | None:
        if v is not None and v not in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
            raise ValueError("hook_event must be one of: SessionStart, UserPromptSubmit, PreToolUse")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for ev in v:
                if ev not in _EVENT_VALUES:
                    raise ValueError(f"each event must be one of: {', '.join(_EVENT_VALUES)}")
        return v


class RecallResponse(BaseModel):
    notes: str
    processing_ms: int


class ProactiveRequest(BaseModel):
    """An already-assembled proactive window (UPG-PRO-7). The caller (the proxy,
    or a future hook adapter) extracts the window; the daemon does the matching
    and gating. Kept structured — no raw request body is sent here."""

    text: str = Field(default="", description="Assembled recent-conversation text (the semantic query)")
    file_paths: list[str] = Field(default_factory=list, description="Deterministic file-path anchors from tool traffic")
    symbols: list[str] = Field(default_factory=list, description="Deterministic symbol anchors from tool traffic")
    session_id: str = Field(default="", description="Per-conversation id for dedup/cooldown")
    channel: str = Field(default="proxy", description="Delivery channel label (e.g. 'proxy'); used for per-channel policy + metrics")
    structural_only: bool = Field(default=False, description="Emit only exact structural matches (a static per-channel policy, not a content decision)")


class ProactiveResponse(BaseModel):
    context: str = Field(default="", description="Packed context to inject ('' = inject nothing)")
    item_count: int = 0
    anchor_ids: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    processing_ms: int = 0


class ForgetRequest(BaseModel):
    note_id: int | None = Field(default=None, description="Delete this one note (the [#N] id from recall)")
    all: bool = Field(default=False, description="Delete ALL notes for this workspace. Irreversible.")


class PromoteRequest(BaseModel):
    """Explicit provenance promotion (TRIGGER-ENGINE wave 1,
    bm2-design-skeleton.md §5): auto -> agent -> human, one step at a time.
    Provenance is immutable at write; this is the one sanctioned way to raise
    it afterward (e.g. a human reviews and endorses an agent-authored note).
    Demotion is impossible."""

    note_id: int = Field(..., description="Note to promote")
    to: str = Field(..., description="Target provenance — must be exactly one step above the note's current provenance (auto -> agent -> human)")

    @field_validator("to")
    @classmethod
    def validate_to(cls, v: str) -> str:
        if v not in _PROVENANCE_VALUES:
            raise ValueError(f"to must be one of: {', '.join(_PROVENANCE_VALUES)}")
        return v


class PromoteResponse(BaseModel):
    note_id: int
    provenance: str
    processing_ms: int


class CommitNoteRequest(BaseModel):
    """UPG-COMMIT-MEMORY-HOOK: the git post-commit hook's own write path —
    called only by `vectr hook post-commit` (main.cmd_hook), never by the
    editor's LLM. Every field is a raw git fact gathered client-side by that
    hook's own `git` subprocess calls; all interpretation (file-list
    capping, active-task lookup, note content/kind/provenance) happens
    server-side in `VectrService.record_commit_note`, the single source of
    truth for the resulting note's shape."""

    sha: str = Field(..., min_length=1, description="Short commit sha (git rev-parse --short HEAD)")
    subject: str = Field(default="", description="Commit subject line (git log -1 --format=%s)")
    branch: str = Field(default="", description="Current branch name (git rev-parse --abbrev-ref HEAD); empty on a detached HEAD")
    files: list[str] = Field(
        default_factory=list,
        description=(
            "Files touched by this commit (git diff-tree --no-commit-id "
            "--name-only -r <sha>), sent uncapped — the server applies the "
            "display cap (agent/config.yaml hooks.commit_note_max_files)."
        ),
    )


class CommitNoteResponse(BaseModel):
    note_id: int
    processing_ms: int


class SnapshotRequest(BaseModel):
    label: str = Field(..., min_length=1)
    session_id: str | None = Field(default=None)


class SnapshotResponse(BaseModel):
    snapshot_id: str
    label: str
    processing_ms: int


class ResumeNoteSummary(BaseModel):
    """One resume-surface note, index-tier only (UPG-RESUME-SURFACE) — same
    bounded fields (id, kind/priority, title, age, staleness) the CLI/MCP
    text render shows; expand the full body with
    vectr_recall(note_id=...)/`vectr recall --id <note_id>`."""
    note_id: int
    kind: str
    priority: str
    title: str
    created_at: float
    anchors: list[str] = []
    stale: bool = False


class ResumeSnapshotSummary(BaseModel):
    snapshot_id: str
    label: str
    created_at: float
    # None when the snapshot payload could not be decrypted/read (see
    # WorkingContextStore.restore_snapshot) — distinct from an empty snapshot.
    note_count: int | None = None


class ResumeResponse(BaseModel):
    """GET /v1/resume (UPG-RESUME-SURFACE): deterministic 'pick up where you
    left off' — the most recent high-priority task note (the SAME selection
    SessionStart boot injection uses), the latest saved snapshot, and open
    gotchas. Any of the three is None/empty when there is nothing to show —
    never an error."""
    last_task: ResumeNoteSummary | None = None
    snapshot: ResumeSnapshotSummary | None = None
    gotchas: list[ResumeNoteSummary] = []
    # True when more open gotchas exist beyond the configured cap
    # (behavior.resume.max_gotchas) — the formatted text discloses this too.
    gotchas_truncated: bool = False
    # Human/LLM-readable sectioned rendering of the fields above — the same
    # text the CLI prints and the MCP tool returns.
    formatted: str
    processing_ms: int


class TriggerResetRequest(BaseModel):
    """Reset one session's trigger-engine state (TRIGGER-ENGINE wave 2a,
    bm2-design-skeleton.md §3: "cleared on compaction"): the per-session fire
    ledger (so a previously-fired memory becomes re-eligible) and the
    cumulative injection token budget both zero out. Called by the
    PreCompact hook, right before the SessionStart hook that follows a
    /compact re-delivers the boot set. A session_id with no tracked ledger
    yet (or None) is a no-op, never an error — mirrors
    `VectrService.reset_trigger_ledger()`'s own contract."""

    session_id: str | None = Field(default=None)


class TriggerResetResponse(BaseModel):
    reset: bool
    processing_ms: int


# ---------------------------------------------------------------------------
# L1 episode capture (memoization-l1-capture-design §2)
# ---------------------------------------------------------------------------

class EpisodeRequest(BaseModel):
    """PostToolUse hook's own write path — called only by `vectr hook
    post-tool-use` (main.cmd_hook / agent.hook_cli.run_hook)'s detached
    worker, never by the editor's LLM directly. Every field optional except
    `tool`: the hook forwards whatever the editor's PostToolUse payload
    actually contains, defensively (payload shape across editor versions is
    not fully known — see the L1 capture design spec's G0 gate). ALL
    interpretation (normalization, outcome derivation, digest
    canonicalization) happens server-side in `VectrService.record_episode`,
    the single source of truth for the resulting row's shape — mirroring
    `CommitNoteRequest`'s "gather raw facts client-side, interpret
    server-side" split."""

    session_id: str | None = Field(default=None)
    ts: float | None = Field(default=None, description="Client-observed event time; server stamps its own if omitted")
    cwd: str = Field(default="")
    tool: str = Field(..., min_length=1, description="'bash' or 'edit'")
    command: str | None = Field(default=None, description="tool_input.command (Bash only)")
    description: str | None = Field(default=None, description="tool_input.description (Bash only)")
    file_path: str | None = Field(default=None, description="tool_input.file_path (Edit/Write/MultiEdit only) — path only, never content")
    rc: int | None = Field(default=None, description="Exit code, if the editor's tool_response ever includes one")
    is_error: bool = Field(default=False)
    interrupted: bool = Field(default=False)
    stdout_tail: str = Field(default="", description="Client-truncated stdout text")
    stderr_tail: str = Field(default="", description="Client-truncated stderr text")


class EpisodeResponse(BaseModel):
    episode_id: int
    processing_ms: int


class EpisodeRecord(BaseModel):
    """One row as returned by GET /v1/episodes — the only bulk reader of the
    `episodes` table besides the aggregate counts in `vectr_status`."""

    id: int
    session_id: str | None = None
    ts: float
    cwd: str
    tool: str
    cmd_raw: str
    verb: str
    flags: list[str]
    args: list[dict]
    rc: int | None = None
    termination: str
    outcome: str
    stdout_digest: str
    stderr_digest: str
    markers_matched: list[str]
    env_delta_names: list[str]
    file_path: str | None = None
    arc_id: int | None = None


class EpisodesResponse(BaseModel):
    episodes: list[EpisodeRecord]
    processing_ms: int


# ---------------------------------------------------------------------------
# Symbol graph
# ---------------------------------------------------------------------------

class LocateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class SymbolResult(BaseModel):
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str = ""   # first ~12 lines of symbol body — AI reads this directly


class LocateResponse(BaseModel):
    results: list[SymbolResult]
    formatted: str
    processing_ms: int


class TraceRequest(BaseModel):
    name: str = Field(..., min_length=1)
    direction: str = Field(default="both")
    limit: int = Field(default=20, ge=1, le=100)
    include_builtins: bool = Field(default=False)

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        if v not in ("callers", "callees", "both"):
            raise ValueError("direction must be callers, callees, or both")
        return v


class TraceResponse(BaseModel):
    formatted: str
    processing_ms: int
