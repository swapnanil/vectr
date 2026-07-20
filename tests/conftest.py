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

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        # Symmetric stand-in — no registered query prompt, so query-mode embedding
        # is identical to document-mode embedding (matches most real embed models).
        return self.embed(texts)


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
# UPG-CONFTEST-SERVICE-CLOBBER: universal app.state.service snapshot/restore.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_app_state_service():
    """Snapshot and restore ``app.state.service`` around every test so no fixture
    or inline ``TestClient`` block can leave a mock/partial service installed for
    a later test to exercise vacuously (the "lying mock" class).

    The session-scoped ``real_service_client`` sets ``app.state.service`` once and
    relies on it persisting; a mock-based test that ran in between used to clobber
    it and never restore, so a later real-service REST test silently exercised the
    wrong service. Rolling the value back after every test keeps whatever was
    installed at each test's start (the real service, once ``real_service_client``
    is set up) authoritative — which is what makes removing the local
    ``_reaffirm_real_service`` workaround safe. Higher-scoped fixtures set up
    before this function-scoped autouse, so the snapshot already reflects them."""
    from api import app
    saved = getattr(app.state, "service", None)
    try:
        yield
    finally:
        app.state.service = saved


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

        # Patch `app.service.VectrService` only across TestClient startup, where the
        # `lifespan` handler's own `VectrService(...)` call must be intercepted to
        # return our pre-built `svc` instead of constructing a fresh one pointed at
        # the real repo. Scoping the patch to just __enter__() (rather than wrapping
        # it around the whole `with` block, which — for a session-scoped generator
        # fixture — would keep the patch active for the REST OF THE TEST SESSION)
        # prevents every later test's unrelated `VectrService(...)` construction
        # from silently being redirected to this one shared, ever-growing instance.
        c = TestClient(app, raise_server_exceptions=True)
        with patch("app.service.VectrService", return_value=svc):
            c.__enter__()
        try:
            yield c, svc, str(tmp)
        finally:
            c.__exit__(None, None, None)
            svc.shutdown()  # release the indexer's ChromaDB client at session end


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
    from agent.symbol_graph import LocateResult

    svc = MagicMock()
    svc._embed_model = "BAAI/bge-base-en-v1.5"
    svc.total_chunks = 100
    # UPG-8.2: /v1/health sources last_indexed from the same VectrService
    # property that populates svc.status()["last_indexed"] below.
    svc.last_indexed = "2026-01-01T00:00:00Z"

    _result = SearchResult(
        file_path="src/auth.py", lines="10-30", symbol_name="verify_token",
        language="python", score=0.91, content="def verify_token(): ...",
    )
    svc.search.return_value = ([_result], 15)
    # UPG-QUERYTYPE-REROUTE: additive symbol-graph hint — no exact identifier
    # match by default, so no hint section is appended in the common case.
    svc.identifier_hint_symbols.return_value = []
    svc.index.return_value = (5, 100, 120)
    svc.status.return_value = {
        "indexed_files": 5, "total_chunks": 100,
        "last_indexed": "2026-01-01T00:00:00Z",
        "embed_model": "BAAI/bge-base-en-v1.5",
        "workspace_root": "/repo", "symbol_count": 20,
    }
    svc.get_map.return_value = "# Passport\nPython FastAPI service."
    # Real locate_with_snippets() returns a LocateResult, not a bare list —
    # a mock returning [] made /v1/locate 500 on `result.symbols` for any
    # test that actually asserted a 200 (caught while adding search-only
    # REST coverage; conftest mock was the lone type mismatch here).
    svc.locate_with_snippets.return_value = LocateResult(symbols=[], resolution_strategy="none", query="")
    svc.format_locate.return_value = "No results."
    svc.trace_with_snippets.return_value = {}
    svc.format_trace.return_value = "No trace."
    svc.should_evict.return_value = False
    svc.eviction_hint.return_value = ""
    svc.remember.return_value = 1
    svc.promote_note.return_value = True
    svc.recall.return_value = "# Working Notes (1 entries)\n\n[1] [HIGH] test content\n"
    svc.snapshot_session.return_value = "snap_abc123"
    svc.list_snapshots.return_value = [{"snapshot_id": "snap_abc123", "label": "test", "created_at": 0.0}]
    # UPG-RESUME-SURFACE: real VectrService.resume() shape (last_task/gotchas/
    # snapshot/formatted) — a bare MagicMock would fail `ResumeResponse(**data)`
    # at the route (mocks must return the REAL type, not a stand-in).
    svc.resume.return_value = {
        "last_task": None,
        "gotchas": [],
        "snapshot": None,
        "formatted": (
            "Nothing to resume yet — no task notes, snapshots, or gotchas "
            "recorded for this workspace. Use vectr_remember(kind='task', ...) "
            "to start one."
        ),
    }
    # Default mode is full (not memory-only / not search-only); must be an
    # explicit bool, not a MagicMock (bare MagicMock attrs are truthy by default).
    svc.memory_only = False
    svc.search_only = False
    return svc


@pytest.fixture
def client():
    """FastAPI TestClient with fully-mocked VectrService. Fast — no model loading."""
    from api import app
    svc = _base_mock_service()
    # UPG-CONFTEST-SERVICE-CLOBBER: save/restore app.state.service so this mock
    # never persists past the fixture and clobber a later real-service test that
    # runs after it (a REST test could otherwise exercise a mock and pass
    # vacuously depending on execution order).
    _prior_service = getattr(app.state, "service", None)
    try:
        with patch("app.service.VectrService", return_value=svc):
            with TestClient(app, raise_server_exceptions=True) as c:
                app.state.service = svc
                yield c
    finally:
        app.state.service = _prior_service


@pytest.fixture
def client_real_memory(tmp_path):
    """
    FastAPI TestClient where search is mocked but WorkingContextStore is REAL.
    Used to test the full remember → recall round-trip through HTTP without
    loading the embedding model.
    """
    from api import app
    from agent.working_context_store import WorkingContextStore
    from agent.trigger_engine import TriggerFireLedger

    svc = _base_mock_service()
    real_store = WorkingContextStore(str(tmp_path))
    ws = str(tmp_path)

    def _remember(content, tags=None, priority="medium", session_id=None, kind="finding", title="",
                  agent="", triggers=None, provenance="agent", scope=None, anchors=None,
                  supersedes=None):
        return real_store.remember(
            ws, content, tags, priority, session_id, kind=kind, title=title, author_id=agent,
            triggers=triggers, provenance=provenance, scope=scope, anchors=anchors,
            supersedes=supersedes,
        )

    svc.remember.side_effect = _remember
    svc.promote_note.side_effect = lambda note_id, to: real_store.promote(ws, note_id, to)

    # TRIGGER-ENGINE wave 2a: a minimal per-session ledger registry mirroring
    # `VectrService._ledger_for`/`reset_trigger_ledger` so REST-level tests
    # against this REAL store can exercise fire-dedup and cumulative budget
    # through the actual `/v1/recall` request/response cycle, not just a
    # stand-in that silently accepts and drops `session_id`/`events`.
    _ledgers: dict[str, TriggerFireLedger] = {}

    def _ledger_for(session_id):
        if not session_id:
            return None
        return _ledgers.setdefault(session_id, TriggerFireLedger())

    def _reset_trigger_ledger(session_id):
        if session_id and session_id in _ledgers:
            _ledgers[session_id].reset()

    svc.reset_trigger_ledger.side_effect = _reset_trigger_ledger

    def _recall(query=None, tags=None, priority=None, limit=10, kind=None, boot=False,
                min_similarity=None, file_path=None, max_age_days=None, sort_by="relevance",
                detail="index", note_id=None, surface="mcp", hook_event=None,
                session_id=None, events=None):
        if note_id is not None:
            note = real_store.get_note(ws, note_id)
            if note is None:
                return f"Note #{note_id} not found."
            stale = real_store.check_staleness([note], ws)
            return real_store.format_notes_for_llm([note], stale_warnings=stale, detail="full", surface=surface)
        if boot:
            events_to_fire = events if events else ["session-start"]
            fire_text, _ = real_store.fire_and_format(
                ws, events=events_to_fire, session_id=session_id,
                ledger=_ledger_for(session_id), surface=surface,
            )
            return fire_text
        if file_path:
            fire_text, fired_ids = real_store.fire_and_format(
                ws, event="pre-edit", file_path=file_path, session_id=session_id,
                ledger=_ledger_for(session_id), surface=surface,
            )
            path_notes = real_store.recall_for_path(ws, file_path, kind=kind, limit=limit, session_id=session_id)
            path_notes = [n for n in path_notes if n.note_id not in fired_ids]
            legacy_text = real_store.format_notes_for_llm(path_notes, detail=detail, surface=surface) if path_notes else ""
            if fire_text and legacy_text:
                return fire_text + "\n\n" + legacy_text
            return fire_text or legacy_text
        fire_text, fired_ids = "", set()
        if events:
            fire_text, fired_ids = real_store.fire_and_format(
                ws, events=events, session_id=session_id,
                ledger=_ledger_for(session_id), surface=surface,
            )
        notes = real_store.recall(ws, query, tags, priority, limit, kind=kind, min_similarity=min_similarity,
                                  max_age_days=max_age_days, sort_by=sort_by, session_id=session_id)
        if fired_ids:
            notes = [n for n in notes if n.note_id not in fired_ids]
        formatted = real_store.format_notes_for_llm(notes, detail=detail, surface=surface, sort_by=sort_by)
        if fire_text and formatted:
            return fire_text + "\n\n" + formatted
        return fire_text or formatted

    svc.recall.side_effect = _recall
    svc.forget_note.side_effect = lambda note_id: real_store.forget(ws, note_id)
    svc.forget_all.side_effect = lambda: real_store.forget_all(ws)
    svc.snapshot_session.side_effect = lambda label, session_id=None: \
        real_store.snapshot(ws, label=label)
    svc.list_snapshots.side_effect = lambda: real_store.list_snapshots(ws)

    def _resume(session_id=None, surface="mcp"):
        # Mirrors VectrService.resume() exactly (see app/service.py) against
        # the REAL store, the same way `_recall`/`_remember` above do for
        # their routes — a REST test exercising this fixture goes through
        # the real selection/rendering, not a hollow mock return.
        from agent.working_context_store import _note_title
        state = real_store.resume_state(ws, session_id=session_id)
        last_task = state["last_task"]
        gotchas = state["gotchas"]
        snapshot = state["snapshot"]
        notes_for_staleness = ([last_task] if last_task else []) + gotchas
        stale = real_store.check_staleness(notes_for_staleness, ws) if notes_for_staleness else {}

        def _summary(note):
            return {
                "note_id": note.note_id, "kind": note.kind, "priority": note.priority,
                "title": _note_title(note), "created_at": note.created_at,
                "anchors": [a[0] for a in (note.anchors or []) if a],
                "stale": note.note_id in stale,
            }

        formatted = real_store.format_resume(state, ws, stale_warnings=stale, surface=surface)
        return {
            "last_task": _summary(last_task) if last_task else None,
            "gotchas": [_summary(g) for g in gotchas],
            "snapshot": snapshot,
            "gotchas_truncated": state["gotchas_truncated"],
            "formatted": formatted,
        }

    svc.resume.side_effect = _resume

    # UPG-COMMIT-MEMORY-HOOK: mirrors VectrService.record_commit_note's own
    # logic against the REAL store (same active-task lookup via boot_recall,
    # same content formatting) so /v1/commit-note REST tests exercise the
    # real remember() write path, not a stub that always returns a bare int.
    def _record_commit_note(sha, subject, branch, files):
        from agent.config import HOOKS_COMMIT_NOTE_MAX_SUBJECT_CHARS
        from app.service import _format_commit_note_content, _COMMIT_NOTE_TAG, _COMMIT_NOTE_AGENT_ID

        task_note = None
        for note in real_store.boot_recall(ws):
            if note.kind == "task":
                task_note = note
                break
        content = _format_commit_note_content(sha, subject, branch, files, task_note)
        return real_store.remember(
            ws, content, [_COMMIT_NOTE_TAG], "low", None, kind="finding",
            title=f"Commit {sha}: {subject[:HOOKS_COMMIT_NOTE_MAX_SUBJECT_CHARS]}",
            author_id=_COMMIT_NOTE_AGENT_ID, provenance="auto",
        )

    svc.record_commit_note.side_effect = _record_commit_note

    # UPG-CONFTEST-SERVICE-CLOBBER: save/restore app.state.service (see the
    # `client` fixture) so this partial-real service does not persist into a
    # later test that relies on a different app.state.service.
    _prior_service = getattr(app.state, "service", None)
    try:
        with patch("app.service.VectrService", return_value=svc):
            with TestClient(app, raise_server_exceptions=True) as c:
                app.state.service = svc
                yield c
    finally:
        app.state.service = _prior_service
