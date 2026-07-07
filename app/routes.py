"""FastAPI route handlers: REST API + MCP protocol endpoints."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Body, HTTPException, Request, Response

from agent.llm_client import get_model
from app.models import (
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
    RecallRequest,
    RecallResponse,
    RememberRequest,
    RememberResponse,
    SearchRequest,
    SearchResponse,
    SnapshotRequest,
    SnapshotResponse,
    StatusResponse,
    TraceRequest,
    TraceResponse,
    CodeChunkResult,
    SymbolResult,
)
from integrations.mcp_server import (
    MCP_SERVER_INFO,
    handle_tools_call,
    handle_tools_list,
)

router = APIRouter()


def _service(request: Request):
    return request.app.state.service


# ---------------------------------------------------------------------------
# REST API — L3 (existing)
# ---------------------------------------------------------------------------

@router.get("/v1/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    svc = _service(request)
    return HealthResponse(
        status="ok",
        model=get_model(),
        embed_model=svc._embed_model,
        last_indexed=svc.last_indexed,
    )


@router.post("/v1/index", response_model=IndexResponse)
async def index(body: IndexRequest, request: Request) -> IndexResponse:
    svc = _service(request)
    if getattr(svc, "memory_only", False):
        from app.service import _MEMORY_ONLY_MSG
        raise HTTPException(status_code=503, detail={"error": "memory_only_mode", "detail": _MEMORY_ONLY_MSG})
    try:
        files, chunks, elapsed = svc.index(body.path, force=body.force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": "index_failed", "detail": str(exc)})
    return IndexResponse(
        indexed_files=files,
        total_chunks=chunks,
        processing_ms=elapsed,
        model=get_model(),
    )


@router.post("/v1/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    t0 = time.monotonic()
    svc = _service(request)
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
            )
            for r in results
        ],
        query_time_ms=query_ms,
        chunks_searched=svc.total_chunks,
        processing_ms=int((time.monotonic() - t0) * 1000),
        model=get_model(),
        low_confidence=getattr(results, "low_confidence", False),
    )


@router.post("/v1/fetch", response_model=FetchResponse)
async def fetch(body: FetchRequest, request: Request) -> FetchResponse:
    """Deterministic re-fetch by chunk id (UPG-CTX-EVICT part a) — no
    embedding, no rerank, just the exact chunk(s) named by id."""
    t0 = time.monotonic()
    svc = _service(request)
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
        model=get_model(),
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
    return {"map": svc.get_map()}


@router.post("/v1/map", response_model=MapSaveResponse)
async def map_save(body: MapSaveRequest, request: Request) -> MapSaveResponse:
    t0 = time.monotonic()
    svc = _service(request)
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
    note_id = svc.remember(
        content=body.content,
        tags=body.tags,
        priority=body.priority,
        session_id=body.session_id,
        kind=body.kind,
        title=body.title,
        agent=body.agent,
    )
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
    )
    return RecallResponse(
        notes=notes_text,
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


@router.get("/v1/evict-hint")
async def evict_hint(request: Request) -> dict:
    svc = _service(request)
    hint = svc.eviction_hint()
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
        return _ok(handle_tools_call(tool_name, arguments, svc, session_id=session_id))

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
    session_id = request.headers.get("X-Session-ID") or None
    return handle_tools_call(tool_name, arguments, svc, session_id=session_id)
