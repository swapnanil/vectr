"""FastAPI application entry point. Port: 8765."""
from __future__ import annotations

import hmac
import logging
import os

from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

from app.routes import router


@asynccontextmanager
async def lifespan(application: FastAPI):
    from app.service import VectrService
    from integrations.workspace_detect import validate_workspace_env

    import json as _json
    import threading
    workspace = os.getenv("VECTR_WORKSPACE", ".")
    # UPG-WORKSPACE-ENV-VALIDATE: a typo'd VECTR_WORKSPACE must fail daemon
    # startup loudly, never silently fall back to cwd-based detection — a bad
    # harness env would otherwise index the wrong tree with no visible error.
    validate_workspace_env(workspace)
    port = int(os.getenv("VECTR_PORT", "8765"))
    extra_roots = _json.loads(os.getenv("VECTR_EXTRA_ROOTS", "[]"))
    memory_only = os.getenv("VECTR_MEMORY_ONLY", "") == "1"
    search_only = os.getenv("VECTR_SEARCH_ONLY", "") == "1"
    # UPG-WS-ROOT-MISDETECT: set by `vectr start`/`restart` only when the user
    # gave an explicit workspace path — that path must win over the
    # git-toplevel walk-up VectrService otherwise applies.
    workspace_explicit = os.getenv("VECTR_WORKSPACE_EXPLICIT", "") == "1"
    # UPG-CLI-WRITES-DISCLOSURE follow-through: `vectr start/restart --no-ide-config`
    # sets this to "0" so the daemon's own IDE-config writes (.cursor/mcp.json,
    # .claude/settings.json) honor the same opt-out as the CLI's own 7-file write.
    configure_ide = os.getenv("VECTR_CONFIGURE_IDE", "1") == "1"
    svc = VectrService(
        workspace_root=workspace, port=port, extra_roots=extra_roots,
        memory_only=memory_only, search_only=search_only,
        workspace_explicit=workspace_explicit, configure_ide=configure_ide,
        # UPG-STDIO-MEMORY-READY: phase 1 only here — the server binds and
        # starts accepting connections right after this call returns, well
        # before the embedder/indexer/watcher/symbol graph (phase 2) finish
        # loading in the background thread below. Working-memory routes
        # (remember/recall/forget/status/snapshot) serve immediately;
        # search-touching routes (index/search/fetch/locate/trace/map) stay
        # gated on `svc.fully_ready` until phase 2 completes.
        defer_search_init=True,
    )
    application.state.service = svc

    def _finish_startup() -> None:
        try:
            svc.complete_search_init()
            svc.start_background_index()
        except BaseException:
            # Phase 2 failed — working-memory routes already serve (phase 1
            # succeeded above) and must keep serving. `fully_ready` simply
            # never flips, so search routes keep reporting the same
            # still-initialising response rather than the process crashing.
            logging.getLogger(__name__).exception(
                "vectr daemon: phase 2 (search layer) initialisation failed — "
                "working-memory routes remain available; search routes will "
                "keep reporting still-initialising"
            )

    threading.Thread(
        target=_finish_startup, daemon=True, name="vectr-http-service-init",
    ).start()

    # No internal LLM call at startup. The AI editor calls vectr_map on first use;
    # if no passport is cached, vectr returns raw metadata and prompts the AI to
    # call vectr_map_save with its synthesised summary.
    yield
    svc.shutdown()


app = FastAPI(
    title="Vectr",
    description=(
        "Persistent external memory and semantic code search for AI coding agents, "
        "exposed over MCP and this REST API: codebase search/symbol-graph lookups "
        "(search/locate/trace/map) plus a working-memory store (remember/recall/"
        "snapshot) that survives context compaction and session boundaries."
    ),
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Api-Key", "X-Vectr-Client", "X-Session-ID"],
)


# Optional API key enforcement — only active when VECTR_API_KEY is set.
# Solo dev / personal machine: no key required by default.
# For shared-host or team (central instance) use: set VECTR_API_KEY and share it
# with connecting clients. Middleware reads the env var at request time (not
# import time) so the key can be changed without restarting and tests can patch
# os.environ cleanly.
@app.middleware("http")
async def require_api_key(request: Request, call_next) -> Response:
    api_key = os.getenv("VECTR_API_KEY", "")
    if not api_key:
        return await call_next(request)

    # Allow health check without auth so monitoring tools work
    if request.url.path == "/v1/health":
        return await call_next(request)

    # Accept key in X-Api-Key header or Authorization: Bearer <key>
    provided = (
        request.headers.get("X-Api-Key", "")
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    # Constant-time comparison — never a plain `==`/`!=`, which leaks the key
    # length and prefix via response timing. compare_digest requires both
    # operands be the same type; encode to bytes so a unicode key is handled.
    if not hmac.compare_digest(provided.encode("utf-8"), api_key.encode("utf-8")):
        return Response(
            content='{"error":"unauthorized","detail":"Set X-Api-Key or Authorization: Bearer <VECTR_API_KEY>"}',
            status_code=401,
            media_type="application/json",
        )
    return await call_next(request)


app.include_router(router)
