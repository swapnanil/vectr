"""
Shared fixtures for the vectr test suite.

Key fixture: `indexer` — real CodeIndexer backed by an in-memory-style ChromaDB
(stored in tmp_path) with the heavy sentence-transformers model replaced by a
deterministic dummy embedder. No model download required; tests run in <1 s.
"""
from __future__ import annotations

import os
import sys
import textwrap
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

# langchain_community 0.4+ removed chat_models.vertexai (moved to langchain-google-vertexai).
# Stub it so ragas can be imported without requiring the VertexAI extras.
if "langchain_community.chat_models.vertexai" not in sys.modules:
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["langchain_community.chat_models.vertexai"] = MagicMock()

# Disable cross-encoder reranker before any searcher import so tests never
# trigger a model download.
os.environ["VECTR_RERANKER_MODEL"] = ""

import numpy as np
import pytest
from fastapi.testclient import TestClient

# Saved at collection time (before any fixture patches app.service.VectrService).
# real_service_client patches that name session-wide; test_ragas_eval uses this
# reference so it always gets the real constructor, not the mock.
from app.service import VectrService as _RealVectrService


# ---------------------------------------------------------------------------
# Dummy embed provider — deterministic, zero-download
# ---------------------------------------------------------------------------

class _DummyEmbedProvider:
    """Deterministic 768-dim embedder for unit tests. Matches nomic-embed-code dim."""
    DIM = 768

    def encode(self, texts: list[str]) -> np.ndarray:
        out = []
        for text in texts:
            seed = abs(hash(text[:80])) % (2**31)
            rng = np.random.RandomState(seed)
            v = rng.randn(self.DIM).astype(np.float32)
            norm = np.linalg.norm(v)
            out.append(v / (norm + 1e-8))
        return np.array(out)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.encode(texts).tolist()


@pytest.fixture
def indexer(tmp_path, monkeypatch):
    """
    CodeIndexer backed by a fresh ChromaDB in tmp_path.
    The embed provider is replaced with _DummyEmbedProvider — no model download.
    """
    from agent import indexer as idx_module
    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _model: _DummyEmbedProvider())
    from agent.indexer import CodeIndexer
    return CodeIndexer(
        workspace_root=str(tmp_path),
        db_path=str(tmp_path / "chroma"),
    )


@pytest.fixture
def searcher(indexer):
    """CodeSearcher wrapping a mocked-embedder CodeIndexer."""
    from agent.searcher import CodeSearcher
    return CodeSearcher(indexer)


# ---------------------------------------------------------------------------
# Real-service fixture — full pipeline with dummy embedder
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def real_service_client(tmp_path_factory):
    """
    FastAPI TestClient backed by a REAL VectrService with dummy embedder.

    Unlike `client` (which mocks the entire service), this exercises the full
    pipeline: HTTP → routes → VectrService → CodeIndexer → ChromaDB →
    CodeSearcher (BM25 + vector) → memory store.

    The embed provider is the deterministic dummy so no model download is
    needed, but everything else is production code.

    Important: the lifespan handler in api.py creates its own VectrService on
    TestClient entry and sets app.state.service.  We prevent that from clobbering
    our pre-built svc by patching VectrService in app.service so the lifespan
    call returns *our* svc instead of creating a fresh one pointed at the repo.
    """
    tmp = tmp_path_factory.mktemp("real_svc")

    with patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp), "VECTR_EMBED_MODEL": "dummy"}):
        from app.service import VectrService
        from api import app

        svc = VectrService(workspace_root=str(tmp))

        with patch("app.service.VectrService", return_value=svc), \
             TestClient(app, raise_server_exceptions=True) as c:
            yield c, svc, str(tmp)


# ---------------------------------------------------------------------------
# Integration fixture — real nomic-embed-code model
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def integration_indexer(tmp_path_factory):
    """
    CodeIndexer with the production Snowflake/snowflake-arctic-embed-m-v1.5 model.

    Downloads once (~440 MB), then cached at ~/.cache/vectr/models.
    Used only by @pytest.mark.integration tests.  Run with: pytest -m integration
    """
    import os as _os
    tmp = tmp_path_factory.mktemp("integration")
    model = _os.getenv("VECTR_EMBED_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")
    from agent.indexer import CodeIndexer
    return CodeIndexer(
        workspace_root=str(tmp),
        embed_model=model,
        db_path=str(tmp / "chroma"),
    )


# ---------------------------------------------------------------------------
# Python file factory helper
# ---------------------------------------------------------------------------

def make_py(tmp_path: Path, name: str, content: str) -> str:
    """Write a Python file into tmp_path and return its absolute path."""
    f = tmp_path / name
    f.write_text(textwrap.dedent(content))
    return str(f)


# ---------------------------------------------------------------------------
# FastAPI client fixtures
# ---------------------------------------------------------------------------

def _base_mock_service():
    """Mock VectrService with sensible defaults for API route tests."""
    from agent.searcher import SearchResult
    from agent.query_router import RoutingDecision, QueryType

    svc = MagicMock()
    svc._embed_model = "BAAI/bge-base-en-v1.5"
    svc.total_chunks = 100

    _result = SearchResult(
        file_path="src/auth.py", lines="10-30", symbol_name="verify_token",
        language="python", score=0.91, content="def verify_token(): ...",
    )
    _decision = RoutingDecision(
        query_type=QueryType.SEMANTIC, semantic_weight=0.70,
        also_run_symbol_lookup=False, also_run_trace=False,
        include_map_hint=False, rationale="semantic",
    )
    svc.search.return_value = ([_result], 15)
    svc.search_routed.return_value = ([_result], 15, _decision, [], [])
    svc.index.return_value = (5, 100, 120)
    svc.status.return_value = {
        "indexed_files": 5, "total_chunks": 100,
        "last_indexed": "2026-01-01T00:00:00Z",
        "embed_model": "BAAI/bge-base-en-v1.5",
        "workspace_root": "/repo", "symbol_count": 20,
    }
    svc.get_map.return_value = "# Passport\nPython FastAPI service."
    svc.locate_with_snippets.return_value = []
    svc.format_locate.return_value = "No results."
    svc.trace_with_snippets.return_value = {}
    svc.format_trace.return_value = "No trace."
    svc.should_evict.return_value = False
    svc.eviction_hint.return_value = ""
    svc.remember.return_value = 1
    svc.recall.return_value = "# Working Notes (1 entries)\n\n[1] [HIGH] test content\n"
    svc.snapshot_session.return_value = "snap_abc123"
    svc.list_snapshots.return_value = [{"snapshot_id": "snap_abc123", "label": "test", "created_at": 0.0}]
    # Default mode is full (not memory-only); must be an explicit bool, not a MagicMock.
    svc.memory_only = False
    return svc


@pytest.fixture
def client():
    """FastAPI TestClient with fully-mocked VectrService. Fast — no model loading."""
    from api import app
    svc = _base_mock_service()
    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.service = svc
            yield c


@pytest.fixture
def client_real_memory(tmp_path):
    """
    FastAPI TestClient where search is mocked but WorkingContextStore is REAL.
    Used to test the full remember → recall round-trip through HTTP without
    loading the embedding model.
    """
    from api import app
    from agent.working_context_store import WorkingContextStore

    svc = _base_mock_service()
    real_store = WorkingContextStore(str(tmp_path))
    ws = str(tmp_path)

    svc.remember.side_effect = lambda content, tags=None, priority="medium", session_id=None, kind="finding": \
        real_store.remember(ws, content, tags, priority, session_id, kind=kind)

    def _recall(query=None, tags=None, priority=None, limit=10, kind=None, boot=False,
                min_similarity=None, file_path=None):
        if boot:
            boot_notes = real_store.boot_recall(ws)
            return real_store.format_notes_for_llm(boot_notes) if boot_notes else ""
        if file_path:
            path_notes = real_store.recall_for_path(ws, file_path, kind=kind, limit=limit)
            return real_store.format_notes_for_llm(path_notes) if path_notes else ""
        return real_store.format_notes_for_llm(
            real_store.recall(ws, query, tags, priority, limit, kind=kind, min_similarity=min_similarity))

    svc.recall.side_effect = _recall
    svc.snapshot_session.side_effect = lambda label, session_id=None: \
        real_store.snapshot(ws, label=label)
    svc.list_snapshots.side_effect = lambda: real_store.list_snapshots(ws)

    with patch("app.service.VectrService", return_value=svc):
        with TestClient(app, raise_server_exceptions=True) as c:
            app.state.service = svc
            yield c
