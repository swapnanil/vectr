"""FastAPI application entry point. Port: 8765."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import FastAPI
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

    workspace = os.getenv("VECTR_WORKSPACE", ".")
    port = int(os.getenv("VECTR_PORT", "8765"))
    svc = VectrService(workspace_root=workspace, port=port)
    svc.start_background_index()
    application.state.service = svc
    # No internal LLM call at startup. The AI editor calls vectr_map on first use;
    # if no passport is cached, vectr returns raw metadata and prompts the AI to
    # call vectr_map_save with its synthesised summary.
    yield
    svc.shutdown()


app = FastAPI(
    title="Vectr",
    description="Zero-config semantic codebase indexer with MCP protocol",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
