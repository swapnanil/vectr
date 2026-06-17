"""FastAPI route handlers: REST API + MCP protocol endpoints."""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException, Request

from agent.llm_client import get_model
from app.models import (
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
    )


@router.post("/v1/index", response_model=IndexResponse)
async def index(body: IndexRequest, request: Request) -> IndexResponse:
    svc = _service(request)
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
            )
            for r in results
        ],
        query_time_ms=query_ms,
        chunks_searched=svc.total_chunks,
        processing_ms=int((time.monotonic() - t0) * 1000),
        model=get_model(),
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
    svc.save_map(body.summary)
    return MapSaveResponse(
        message=f"Passport saved ({len(body.summary)} chars). Future vectr_map calls return this instantly.",
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# REST API — L2 symbol graph
# ---------------------------------------------------------------------------

@router.post("/v1/locate", response_model=LocateResponse)
async def locate(body: LocateRequest, request: Request) -> LocateResponse:
    t0 = time.monotonic()
    svc = _service(request)
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
    note_id = svc.remember(
        content=body.content,
        tags=body.tags,
        priority=body.priority,
        session_id=body.session_id,
        kind=body.kind,
    )
    return RememberResponse(
        note_id=note_id,
        message=f"Stored note #{note_id}. Recall with vectr_recall — <50ms, verbatim, any time.",
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/recall", response_model=RecallResponse)
async def recall(body: RecallRequest, request: Request) -> RecallResponse:
    t0 = time.monotonic()
    svc = _service(request)
    notes_text = svc.recall(
        query=body.query,
        tags=body.tags,
        priority=body.priority,
        limit=body.limit,
        kind=body.kind,
        boot=body.boot,
        min_similarity=body.min_similarity,
    )
    return RecallResponse(
        notes=notes_text,
        processing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/v1/snapshot", response_model=SnapshotResponse)
async def snapshot(body: SnapshotRequest, request: Request) -> SnapshotResponse:
    t0 = time.monotonic()
    svc = _service(request)
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
    deleted = svc.forget_all()
    return {"deleted": deleted}


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
async def mcp_jsonrpc(request: Request, body: dict = Body(...)) -> dict:
    """Standard MCP JSON-RPC transport — handles initialize / tools/list / tools/call / ping."""
    jsonrpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    def _ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": jsonrpc_id, "result": result}

    def _err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": jsonrpc_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return _ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_INFO["name"], "version": MCP_SERVER_INFO["version"]},
        })

    if method in ("notifications/initialized", "initialized"):
        return {}  # notification — no response body

    if method == "ping":
        return _ok({})

    # extract session ID from _meta for adaptive tool registration
    meta = params.get("_meta") or body.get("_meta") or {}
    session_id = meta.get("sessionId") or request.headers.get("X-Session-ID") or None

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
