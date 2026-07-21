"""FastAPI route handlers: REST API + MCP protocol endpoints."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.models import (
    CommitNoteRequest,
    CommitNoteResponse,
    EpisodeRecord,
    EpisodeRequest,
    EpisodeResponse,
    EpisodesResponse,
    FetchEntry,
    FetchRequest,
    FetchResponse,
    ForgetRequest,
    HealthResponse,
    IndexRequest,
    IndexResponse,
    LocateRequest,
    LocateResponse,
    MapSaveRequest,
    MapSaveResponse,
    ProactiveRequest,
    ProactiveResponse,
    PromoteRequest,
    PromoteResponse,
    RecallRequest,
    RecallResponse,
    RememberRequest,
    RememberResponse,
    ResumeResponse,
    SearchRequest,
    SearchResponse,
    SnapshotRequest,
    SnapshotResponse,
    StatusResponse,
    TraceRequest,
    TraceResponse,
    TriggerResetRequest,
    TriggerResetResponse,
    CodeChunkResult,
    SymbolResult,
)
from integrations.mcp_server import (
    MCP_SERVER_INFO,
    handle_tools_call,
    handle_tools_list,
)
from integrations.mcp_server._schemas import MEMORY_READY_TOOLS

router = APIRouter()


def _service(request: Request):
    return request.app.state.service


def _require_fully_ready(svc) -> None:
    """503 gate for search-touching REST routes (UPG-STDIO-MEMORY-READY):
    index/search/fetch/locate/trace/map/evict-hint all read the indexer,
    searcher, watcher, or symbol graph, which only exist once phase 2 of
    `VectrService` construction has completed. Working-memory routes
    (remember/recall/forget/snapshot/status) never call this — they are
    servable from process start. Keyed purely on service state, mirroring
    the existing memory_only/search_only 503 pattern below."""
    if not getattr(svc, "fully_ready", True):
        from app.service import _STILL_INITIALIZING_MSG
        raise HTTPException(
            status_code=503,
            detail={"error": "still_initialising", "detail": _STILL_INITIALIZING_MSG},
        )


def _mcp_tool_still_initialising(svc, tool_name: str) -> dict | None:
    """Per-tool readiness gate for the MCP transport (UPG-STDIO-MEMORY-READY),
    keyed on TOOL NAME and service STATE only, never on call arguments.
    Working-memory tools (`MEMORY_READY_TOOLS`) are servable the moment the
    service object exists (phase 1); every other tool additionally needs
    `svc.fully_ready` (phase 2 — embedder/indexer/searcher/watcher/symbol
    graph). Returns the graceful tool-call response to send verbatim when
    still gated, or None when the call may proceed."""
    if tool_name in MEMORY_READY_TOOLS or getattr(svc, "fully_ready", True):
        return None
    from app.service import _STILL_INITIALIZING_MSG
    return {"content": [{"type": "text", "text": _STILL_INITIALIZING_MSG}], "isError": False}


# ---------------------------------------------------------------------------
# REST API — L3 (existing)
# ---------------------------------------------------------------------------

@router.get("/v1/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    svc = _service(request)
    return HealthResponse(
        status="ok",
        embed_model=svc._embed_model,
        last_indexed=svc.last_indexed,
    )


@router.post("/v1/index", response_model=IndexResponse)
async def index(body: IndexRequest, request: Request) -> IndexResponse:
    svc = _service(request)
    _require_fully_ready(svc)
    if getattr(svc, "memory_only", False):
        from app.service import _MEMORY_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "memory_only_mode", "detail": _MEMORY_ONLY_MSG})
    try:
        # UPG-REST-STARVATION requirement #1: a full-workspace index is
        # bulk chroma/embedding work — run it off the event-loop thread so a
        # large `/v1/index` call never blocks every other request for its
        # full duration (previously `svc.index()` ran directly on the async
        # handler, holding the event loop for as long as indexing took).
        files, chunks, elapsed = await run_in_threadpool(svc.index, body.path, force=body.force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "index_failed", "detail": str(exc)})
    return IndexResponse(
        indexed_files=files,
        total_chunks=chunks,
        processing_ms=elapsed,
    )


@router.post("/v1/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    t0 = time.monotonic()
    svc = _service(request)
    _require_fully_ready(svc)
    if getattr(svc, "memory_only", False):
        from app.service import _MEMORY_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "memory_only_mode", "detail": _MEMORY_ONLY_MSG})
    try:
        results, query_ms = svc.search(body.query, n_results=body.n_results, language=body.language)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "search_failed", "detail": str(exc)})

    return SearchResponse(
        results=[
            CodeChunkResult(
                file=r.file_path,
                lines=r.lines,
                symbol=r.symbol_name or None,
                language=r.language,
                score=r.score,
                content=r.content,
                symbol_start_line=getattr(r, "symbol_start_line", 0),
                symbol_end_line=getattr(r, "symbol_end_line", 0),
                id=getattr(r, "chunk_id", ""),
                score_source=getattr(r, "score_source", "dense"),
            )
            for r in results
        ],
        query_time_ms=query_ms,
        chunks_searched=svc.total_chunks,
        processing_ms=int((time.monotonic() - t0) * 1000),
        low_confidence=getattr(results, "low_confidence", False),
    )


@router.post("/v1/fetch", response_model=FetchResponse)
async def fetch(body: FetchRequest, request: Request) -> FetchResponse:
    """Deterministic re-fetch by chunk id (UPG-CTX-EVICT part a) — no
    embedding, no rerank, just the exact chunk(s) named by id."""
    t0 = time.monotonic()
    svc = _service(request)
    _require_fully_ready(svc)
    if getattr(svc, "memory_only", False):
        from app.service import _MEMORY_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "memory_only_mode", "detail": _MEMORY_ONLY_MSG})
    try:
        entries = svc.fetch(body.ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "too_many_ids", "detail": str(exc)})
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "fetch_failed", "detail": str(exc)})

    from app.service import _FETCH_NOT_FOUND_NOTE
    any_missing = any(not e["found"] for e in entries)
    return FetchResponse(
        results=[
            FetchEntry(
                id=e["id"],
                found=e["found"],
                file_path=e.get("file_path", ""),
                lines=f"{e['start_line']}-{e['end_line']}" if e["found"] else "",
                symbol=e.get("symbol_name") or None if e["found"] else None,
                language=e.get("language", ""),
                content=e.get("content", ""),
            )
            for e in entries
        ],
        note=_FETCH_NOT_FOUND_NOTE if any_missing else None,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.get("/v1/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    t0 = time.monotonic()
    svc = _service(request)
    data = svc.status()
    return StatusResponse(
        **data,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.get("/v1/call_counts")
async def get_call_counts(request: Request) -> dict:
    return _service(request).get_call_counts()


@router.delete("/v1/call_counts")
async def reset_call_counts(request: Request) -> dict:
    return {"reset": True, "previous_counts": _service(request).reset_call_counts()}


# ---------------------------------------------------------------------------
# REST API — L1 codebase map
# ---------------------------------------------------------------------------

@router.get("/v1/map")
async def map_workspace(request: Request) -> dict:
    svc = _service(request)
    _require_fully_ready(svc)
    return {"map": svc.get_map()}


@router.post("/v1/map", response_model=MapSaveResponse)
async def map_save(body: MapSaveRequest, request: Request) -> MapSaveResponse:
    t0 = time.monotonic()
    svc = _service(request)
    _require_fully_ready(svc)
    result = svc.save_map(body.summary, overwrite=body.overwrite)
    if not result["saved"]:
        return MapSaveResponse(
            message=(
                "A passport already exists for this workspace — not overwritten. "
                "Pass overwrite=true to replace it. Existing summary:\n\n"
                f"{result['existing_summary']}"
            ),
            processing_ms=int((time.monotonic() - t0) * 1000),
            saved=False,
        )
    return MapSaveResponse(
        message=f"Passport saved ({len(body.summary)} chars). Future vectr_map calls return this instantly.",
        processing_ms=int((time.monotonic() - t0) * 1000),
        saved=True,
    )


# ---------------------------------------------------------------------------
# REST API — L2 symbol graph
# ---------------------------------------------------------------------------

@router.post("/v1/locate", response_model=LocateResponse)
async def locate(body: LocateRequest, request: Request) -> LocateResponse:
    t0 = time.monotonic()
    svc = _service(request)
    _require_fully_ready(svc)
    if getattr(svc, "memory_only", False):
        from app.service import _MEMORY_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "memory_only_mode", "detail": _MEMORY_ONLY_MSG})
    result = svc.locate_with_snippets(body.name, limit=body.limit)
    return LocateResponse(
        results=[
            SymbolResult(
                name=s.name,
                kind=s.kind,
                file_path=s.file_path,
                start_line=s.start_line,
                end_line=s.end_line,
                snippet=s.snippet,
            )
            for s in result.symbols
        ],
        formatted=svc.format_locate(result, body.name),
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/trace", response_model=TraceResponse)
async def trace(body: TraceRequest, request: Request) -> TraceResponse:
    t0 = time.monotonic()
    svc = _service(request)
    _require_fully_ready(svc)
    if getattr(svc, "memory_only", False):
        from app.service import _MEMORY_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "memory_only_mode", "detail": _MEMORY_ONLY_MSG})
    trace_result = svc.trace_with_snippets(
        body.name, direction=body.direction, limit=body.limit,
        include_builtins=body.include_builtins,
    )
    return TraceResponse(
        formatted=svc.format_trace(trace_result, body.name),
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# REST API — Memory layer
# ---------------------------------------------------------------------------

@router.post("/v1/remember", response_model=RememberResponse)
async def remember(body: RememberRequest, request: Request) -> RememberResponse:
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    try:
        note_id = svc.remember(
            content=body.content,
            tags=body.tags,
            priority=body.priority,
            session_id=body.session_id,
            kind=body.kind,
            title=body.title,
            agent=body.agent,
            triggers=body.triggers,
            provenance=body.provenance,
            scope=body.scope,
            anchors=body.anchors,
            supersedes=body.supersedes,
        )
    except ValueError as exc:
        # TRIGGER-ENGINE wave 1: malformed triggers, an unrecognised
        # provenance/scope combination (e.g. provenance='auto' +
        # kind='directive'), or a `supersedes` target that does not exist —
        # all caller input errors, never a server fault.
        raise HTTPException(status_code=422, detail={"error": "invalid_memory_object", "detail": str(exc)})
    return RememberResponse(
        note_id=note_id,
        # CLI-form hint (UPG-CLI-RECALL-HINT): this route is the CLI's `vectr
        # remember`, not the MCP tool — the MCP dispatch builds its own
        # separate confirmation text in `vectr_remember(note_id=N)` form.
        message=f"Stored note #{note_id}. Recall with: vectr recall --id {note_id}",
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/recall", response_model=RecallResponse)
async def recall(body: RecallRequest, request: Request) -> RecallResponse:
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    notes_text = svc.recall(
        query=body.query,
        tags=body.tags,
        priority=body.priority,
        limit=body.limit,
        kind=body.kind,
        boot=body.boot,
        min_similarity=body.min_similarity,
        file_path=body.file_path,
        max_age_days=body.max_age_days,
        sort_by=body.sort_by,
        detail=body.detail,
        note_id=body.note_id,
        surface=body.surface,
        hook_event=body.hook_event,
        session_id=body.session_id,
        events=body.events,
    )
    return RecallResponse(
        notes=notes_text,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.get("/v1/resume", response_model=ResumeResponse)
async def resume(
    request: Request,
    session_id: str | None = Query(default=None),
) -> ResumeResponse:
    """UPG-RESUME-SURFACE: deterministic 'pick up where you left off' — the
    CLI/MCP-equivalent view of the same selection SessionStart boot injection
    already replays (see VectrService.resume / WorkingContextStore.resume_state).
    GET, no body: this reads existing state, it stores nothing."""
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    data = svc.resume(session_id=session_id, surface="cli")
    return ResumeResponse(
        **data,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/promote", response_model=PromoteResponse)
async def promote(body: PromoteRequest, request: Request) -> PromoteResponse:
    """Explicit provenance promotion (TRIGGER-ENGINE wave 1,
    bm2-design-skeleton.md §5): auto -> agent -> human, one step at a time.
    Provenance is immutable at write; this is the one sanctioned way to raise
    it afterward.

    This is the user-side promotion surface — a CLI/UI a person operates
    calls this route directly, so it supports the full one-step chain
    including the final agent -> human step. The MCP tool (`vectr_promote`,
    the AI's own surface) deliberately does NOT expose that last step: an
    agent deciding on its own that a person has endorsed a note would reopen
    the trust-inversion hole §5 closes structurally, so it only allows
    auto -> agent and returns a tool error for 'human'."""
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    try:
        promoted = svc.promote_note(body.note_id, body.to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_promotion", "detail": str(exc)})
    if not promoted:
        raise HTTPException(status_code=404, detail={"error": "note_not_found", "detail": f"Note #{body.note_id} not found."})
    return PromoteResponse(
        note_id=body.note_id,
        provenance=body.to,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/commit-note", response_model=CommitNoteResponse)
async def commit_note(body: CommitNoteRequest, request: Request) -> CommitNoteResponse:
    """Git post-commit hook write path (UPG-COMMIT-MEMORY-HOOK): one
    deterministic, zero-inference working-memory note per commit, capturing
    the commit's identity, touched files, and active task context — git
    records WHAT changed, this records the context for WHY. Called only by
    `vectr hook post-commit` (main.cmd_hook), never by the editor's LLM."""
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    note_id = svc.record_commit_note(
        sha=body.sha, subject=body.subject, branch=body.branch, files=body.files,
    )
    return CommitNoteResponse(
        note_id=note_id,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/proactive", response_model=ProactiveResponse)
async def proactive(body: ProactiveRequest, request: Request) -> ProactiveResponse:
    """Packed proactive context for an assembled window (UPG-PRO-7 subset).

    Hook-facing / proxy-facing: never errors the caller. When proactive is
    disabled, the memory layer is absent, or nothing clears the floor + budget,
    it returns an empty result so the caller forwards the request unmodified.
    """
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        # No working-memory layer to draw from; inject nothing (never 503 here).
        return ProactiveResponse(processing_ms=int((time.monotonic() - t0) * 1000))
    try:
        result = svc.proactive_context(
            text=body.text,
            file_paths=body.file_paths,
            symbols=body.symbols,
            session_id=body.session_id,
            channel=body.channel,
            structural_only=body.structural_only,
        )
    except Exception:
        result = {"context": "", "item_count": 0, "anchor_ids": [], "scores": []}
    return ProactiveResponse(
        context=result["context"],
        item_count=result["item_count"],
        anchor_ids=result["anchor_ids"],
        scores=result["scores"],
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/snapshot", response_model=SnapshotResponse)
async def snapshot(body: SnapshotRequest, request: Request) -> SnapshotResponse:
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    snapshot_id = svc.snapshot_session(label=body.label, session_id=body.session_id)
    return SnapshotResponse(
        snapshot_id=snapshot_id,
        label=body.label,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/trigger/reset", response_model=TriggerResetResponse)
async def trigger_reset(body: TriggerResetRequest, request: Request) -> TriggerResetResponse:
    """Reset one session's trigger-engine state (TRIGGER-ENGINE wave 2a) —
    called by the PreCompact hook right before the SessionStart hook that
    follows a /compact re-delivers the boot set. Never 503s even in
    search-only mode: the per-session ledger lives in `VectrService` itself,
    not the working-memory store, so there is nothing to gate on."""
    t0 = time.monotonic()
    svc = _service(request)
    svc.reset_trigger_ledger(body.session_id)
    return TriggerResetResponse(
        reset=True,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/memory/clear")
async def memory_clear(request: Request) -> dict:
    """Delete all working-memory notes and snapshots for the current workspace."""
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    deleted = svc.forget_all()
    return {"deleted": deleted}


@router.post("/v1/forget")
async def forget(body: ForgetRequest, request: Request) -> dict:
    """Delete one note by id, or all notes when all=true. No arguments deletes nothing."""
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    if body.note_id is not None:
        deleted = svc.forget_note(body.note_id)
        return {"deleted": 1 if deleted else 0, "note_id": body.note_id, "found": deleted}
    if body.all:
        return {"deleted": svc.forget_all()}
    raise HTTPException(
        status_code=422,
        detail="Pass note_id to delete one note, or all=true to clear every note.",
    )


@router.post("/v1/episode", response_model=EpisodeResponse)
async def episode(body: EpisodeRequest, request: Request) -> EpisodeResponse:
    """PostToolUse hook write path (L1 episode capture,
    memoization-l1-capture-design §2): one deterministic, zero-inference row
    per Bash/Edit/Write/MultiEdit tool call. Called only by `vectr hook
    post-tool-use`'s detached worker, never by the editor's LLM directly.

    Quarantined by construction: this route is the ONLY write path into the
    `episodes` table (via `EpisodeStore`, a module neither `agent/searcher.py`
    nor `agent/working_context_store` ever imports) — episode rows can never
    surface in `vectr_search`/`vectr_recall` results or any hook-injected
    context. No embedding happens here or anywhere on this path."""
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    episode_id = svc.record_episode(
        session_id=body.session_id,
        ts=body.ts,
        cwd=body.cwd,
        tool=body.tool,
        command=body.command,
        description=body.description,
        file_path=body.file_path,
        rc=body.rc,
        is_error=body.is_error,
        interrupted=body.interrupted,
        stdout_tail=body.stdout_tail,
        stderr_tail=body.stderr_tail,
    )
    return EpisodeResponse(
        episode_id=episode_id,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.get("/v1/episodes", response_model=EpisodesResponse)
async def episodes(
    request: Request,
    session_id: str | None = Query(default=None),
    arc_id: int | None = Query(default=None),
    since_ts: float | None = Query(default=None),
    until_ts: float | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> EpisodesResponse:
    """The only bulk reader of the `episodes` table besides the aggregate
    counts folded into `vectr_status` — deliberately not part of the
    vectr_search/vectr_recall surface (memoization-l1-capture-design §2.5)."""
    t0 = time.monotonic()
    svc = _service(request)
    if getattr(svc, "search_only", False):
        from app.service import _SEARCH_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "search_only_mode", "detail": _SEARCH_ONLY_MSG})
    rows = svc.list_episodes(
        session_id=session_id, arc_id=arc_id, since_ts=since_ts, until_ts=until_ts, limit=limit,
    )
    return EpisodesResponse(
        episodes=[EpisodeRecord(**row) for row in rows],
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.get("/v1/evict-hint")
async def evict_hint(request: Request) -> dict:
    svc = _service(request)
    _require_fully_ready(svc)
    # UPG-7.2: an explicit GET is a deliberate ask — on-demand, eviction-focused
    # framing, distinct from the gated auto-footer's remember alarm.
    hint = svc.eviction_hint(on_demand=True)
    return {"hint": hint or "No retrieved chunks to evict.", "should_evict": svc.should_evict()}


# ---------------------------------------------------------------------------
# MCP protocol
# ---------------------------------------------------------------------------

@router.get("/mcp")
async def mcp_info() -> dict:
    return MCP_SERVER_INFO


@router.post("/mcp")
async def mcp_jsonrpc(request: Request, response: Response, body: dict = Body(...)) -> dict:
    """Standard MCP JSON-RPC transport — handles initialize / tools/list / tools/call / ping.

    Session identity (UPG-MCP-SESSION-ID-HANDSHAKE): per the MCP streamable-HTTP
    transport, the server assigns a session id by returning it in the
    `Mcp-Session-Id` response header on `initialize`; a compliant client echoes
    that same header on every subsequent request. We honor that header first
    (case-insensitive per HTTP, handled by Starlette), then fall back to
    vectr-invented mechanisms (`_meta.sessionId`, `X-Session-ID`) for callers —
    tests, curl, older shims — that predate the handshake.
    """
    jsonrpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    def _ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": jsonrpc_id, "result": result}

    def _err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        response.headers["Mcp-Session-Id"] = uuid.uuid4().hex
        return _ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_INFO["name"], "version": MCP_SERVER_INFO["version"]},
        })

    if method in ("notifications/initialized", "initialized"):
        return {}  # notification — no response body

    if method == "ping":
        return _ok({})

    # Resolve session id: server-assigned handshake header first, then the
    # vectr-invented fallbacks kept for backwards compatibility.
    meta = params.get("_meta") or body.get("_meta") or {}
    session_id = (
        request.headers.get("Mcp-Session-Id")
        or meta.get("sessionId")
        or request.headers.get("X-Session-ID")
        or None
    )

    if method == "tools/list":
        svc = _service(request)
        return _ok(handle_tools_list(session_id=session_id, service=svc))

    if method == "tools/call":
        tool_name = params.get("name")
        arguments  = params.get("arguments") or {}
        if not tool_name:
            return _err(-32602, "Missing required param: name")
        svc = _service(request)
        still_initialising = _mcp_tool_still_initialising(svc, tool_name)
        if still_initialising is not None:
            return _ok(still_initialising)
        client_label = request.headers.get("X-Vectr-Client", "") or ""
        # UPG-EMBED-THREAD-CONTENTION: a tool call may embed a query
        # (search/recall) — CPU-bound work that must not run on the event
        # loop thread, the same reasoning /v1/index already applies to a
        # full-workspace index. Off the event loop, a CPU-saturated moment
        # degrades this call to slow instead of black-holing every other
        # request the daemon serves concurrently.
        result = await run_in_threadpool(
            handle_tools_call, tool_name, arguments, svc,
            session_id=session_id, client_label=client_label,
        )
        return _ok(result)

    return _err(-32601, f"Method not found: {method}")


@router.post("/mcp/tools/list")
async def mcp_tools_list(request: Request, body: dict = Body(default={})) -> dict:
    svc = _service(request)
    session_id = request.headers.get("X-Session-ID") or None
    return handle_tools_list(session_id=session_id, service=svc)


@router.post("/mcp/tools/call")
async def mcp_tools_call(request: Request, body: dict = Body(...)) -> dict:
    tool_name = body.get("name") or body.get("tool")
    arguments = body.get("arguments") or body.get("input") or {}
    if not tool_name:
        raise HTTPException(
            status_code=400,
            detail={"error": "missing_tool_name", "detail": "Request body must include 'name'"},
        )
    svc = _service(request)
    still_initialising = _mcp_tool_still_initialising(svc, tool_name)
    if still_initialising is not None:
        return still_initialising
    session_id = request.headers.get("X-Session-ID") or None
    client_label = request.headers.get("X-Vectr-Client", "") or ""
    # UPG-EMBED-THREAD-CONTENTION: see the /mcp tools/call branch above —
    # same legacy-route dispatch, same off-event-loop requirement.
    return await run_in_threadpool(
        handle_tools_call, tool_name, arguments, svc,
        session_id=session_id, client_label=client_label,
    )
