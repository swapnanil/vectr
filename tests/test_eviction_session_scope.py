"""Tests for UPG-EVICT-SESSION-SCOPE (F62 both facets).

Covers:
  a) EvictionAdvisor is per-session, not daemon-global — one MCP session
     never sees chunks another session retrieved.
  b) The advisor records only chunks actually rendered into a tool response
     (post-truncation), not the internal pre-truncation candidate pool.
  c) The one recording site is render time (MCP dispatch), not VectrService.search()
     itself — a REST /v1/search caller (no session_id) never feeds any advisor.
  d) eviction_hint() lists exact chunk ids as vectr_fetch(ids=[...]) re-fetch keys.
  e) The per-session advisor registry is LRU-bounded.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_py, _DummyEmbedProvider, _RealVectrService


def _make_service(tmp_path, monkeypatch, num_files: int = 1):
    from agent import indexer as idx_module
    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    for i in range(num_files):
        make_py(
            tmp_path, f"mod{i}.py",
            f"def handler_{i}():\n    \"\"\"Handles request type {i}.\"\"\"\n    return {i}\n",
        )
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        svc = _RealVectrService(workspace_root=str(tmp_path))
    svc.index(str(tmp_path))
    return svc


# ---------------------------------------------------------------------------
# VectrService._advisor_for — session isolation + anonymous sharing
# ---------------------------------------------------------------------------

class TestAdvisorFor:
    def test_different_sessions_get_different_advisor_instances(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        a = svc._advisor_for("session-a")
        b = svc._advisor_for("session-b")
        assert a is not b

    def test_same_session_returns_same_advisor_instance(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        a1 = svc._advisor_for("session-a")
        a2 = svc._advisor_for("session-a")
        assert a1 is a2

    def test_none_session_id_returns_shared_anonymous_advisor(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        assert svc._advisor_for(None) is svc._eviction_advisor
        assert svc._advisor_for(None) is svc._advisor_for(None)

    def test_session_scoped_advisor_is_never_the_anonymous_one(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        assert svc._advisor_for("session-a") is not svc._eviction_advisor


# ---------------------------------------------------------------------------
# Registry bound (LRU)
# ---------------------------------------------------------------------------

class TestAdvisorRegistryBound:
    def test_registry_drops_oldest_session_beyond_bound(self, tmp_path, monkeypatch):
        from agent.config import EVICTION_MAX_TRACKED_SESSIONS
        svc = _make_service(tmp_path, monkeypatch)

        first_advisor = svc._advisor_for("session-0")
        for i in range(1, EVICTION_MAX_TRACKED_SESSIONS + 1):
            svc._advisor_for(f"session-{i}")

        # "session-0" was the oldest and must have been evicted — a fresh
        # lookup returns a NEW advisor instance (state loss is the intended
        # LRU-drop signal for a bound we never want to grow unboundedly).
        assert svc._advisor_for("session-0") is not first_advisor

    def test_registry_never_exceeds_bound(self, tmp_path, monkeypatch):
        from agent.config import EVICTION_MAX_TRACKED_SESSIONS
        svc = _make_service(tmp_path, monkeypatch)
        for i in range(EVICTION_MAX_TRACKED_SESSIONS + 10):
            svc._advisor_for(f"session-{i}")
        assert len(svc._session_advisors) <= EVICTION_MAX_TRACKED_SESSIONS


# ---------------------------------------------------------------------------
# record_results / record_chunk — session-scoped, rendered-only
# ---------------------------------------------------------------------------

class TestRecordResultsSessionScoped:
    def test_record_results_writes_only_to_the_named_session(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        results, _ = svc.search("handler", n_results=5)
        assert results, "fixture must produce at least one search result"

        svc.record_results(results, session_id="session-a")

        assert len(svc._advisor_for("session-a")._chunks) == len(results)
        assert len(svc._advisor_for("session-b")._chunks) == 0
        assert len(svc._eviction_advisor._chunks) == 0

    def test_record_results_rendered_count_matches_truncated_list(self, tmp_path, monkeypatch):
        """If the caller truncates the pool before rendering (e.g. n_results
        caps below the internal candidate depth), only the rendered list is
        recorded — never the deeper internal pool."""
        svc = _make_service(tmp_path, monkeypatch, num_files=5)
        full_results, _ = svc.search("handler", n_results=5)
        rendered = full_results[:2]  # simulate a caller rendering fewer than retrieved

        svc.record_results(rendered, session_id="session-a")

        assert len(svc._advisor_for("session-a")._chunks) == len(rendered)

    def test_record_chunk_writes_only_to_the_named_session(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.record_chunk(
            file_path="f.py", lines="1-5", symbol_name="fn", content="x",
            chunk_id="f.py:1-5", session_id="session-a",
        )
        assert len(svc._advisor_for("session-a")._chunks) == 1
        assert len(svc._advisor_for("session-b")._chunks) == 0


# ---------------------------------------------------------------------------
# VectrService.search() no longer records into any advisor
# ---------------------------------------------------------------------------

class TestSearchDoesNotRecord:
    def test_search_alone_does_not_populate_any_advisor(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        results, _ = svc.search("handler", n_results=5)
        assert results
        assert len(svc._eviction_advisor._chunks) == 0
        assert len(svc._session_advisors) == 0


# ---------------------------------------------------------------------------
# REST /v1/search feeds no advisor (mock-service route test)
# ---------------------------------------------------------------------------

class TestRestSearchFeedsNoAdvisor:
    def test_rest_search_never_calls_record_results_or_record_chunk(self):
        from fastapi.testclient import TestClient
        from api import app
        from tests.conftest import _base_mock_service

        svc = _base_mock_service()
        with patch("app.service.VectrService", return_value=svc):
            with TestClient(app, raise_server_exceptions=True) as c:
                app.state.service = svc
                resp = c.post("/v1/search", json={"query": "auth flow"})
        assert resp.status_code == 200
        svc.record_results.assert_not_called()
        svc.record_chunk.assert_not_called()


# ---------------------------------------------------------------------------
# MCP dispatch — two-session isolation through handle_tools_call
# ---------------------------------------------------------------------------

class TestDispatchTwoSessionIsolation:
    def test_vectr_search_records_into_calling_session_only(self, tmp_path, monkeypatch):
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        handle_tools_call("vectr_search", {"query": "handler"}, svc, session_id="session-a")

        assert len(svc._advisor_for("session-a")._chunks) > 0
        assert len(svc._advisor_for("session-b")._chunks) == 0

    def test_vectr_evict_hint_only_sees_calling_sessions_chunks(self, tmp_path, monkeypatch):
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        handle_tools_call("vectr_search", {"query": "handler"}, svc, session_id="session-a")

        hint_a = handle_tools_call("vectr_evict_hint", {}, svc, session_id="session-a")
        hint_b = handle_tools_call("vectr_evict_hint", {}, svc, session_id="session-b")

        text_a = hint_a["content"][0]["text"]
        text_b = hint_b["content"][0]["text"]
        assert "mod0.py" in text_a or "handler_0" in text_a
        assert "No retrieved chunks to evict" in text_b

    def test_vectr_locate_records_snippet_into_calling_session_only(self, tmp_path, monkeypatch):
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        handle_tools_call("vectr_locate", {"name": "handler_0"}, svc, session_id="session-a")

        assert len(svc._advisor_for("session-a")._chunks) > 0
        assert len(svc._advisor_for("session-b")._chunks) == 0
        # A symbol-graph snippet's line range isn't guaranteed to match a
        # stored chunk id — never advertised as a vectr_fetch key.
        assert all(c.chunk_id == "" for c in svc._advisor_for("session-a")._chunks)

    def test_anonymous_sessions_share_one_advisor(self, tmp_path, monkeypatch):
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        handle_tools_call("vectr_search", {"query": "handler"}, svc)  # no session_id
        hint = handle_tools_call("vectr_evict_hint", {}, svc)  # no session_id
        text = hint["content"][0]["text"]
        assert "No retrieved chunks to evict" not in text


# ---------------------------------------------------------------------------
# eviction_hint() lists exact chunk ids as vectr_fetch re-fetch keys
# ---------------------------------------------------------------------------

class TestHintListsExactIds:
    def test_hint_lists_the_exact_chunk_id_recorded(self):
        from agent.eviction_advisor import EvictionAdvisor

        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "x" * 40, chunk_id="f.py:1-5")
        hint = adv.eviction_hint()
        assert "vectr_fetch(ids=" in hint
        assert '"f.py:1-5"' in hint

    def test_hint_omits_fetch_line_when_no_chunk_has_an_id(self):
        from agent.eviction_advisor import EvictionAdvisor

        adv = EvictionAdvisor()
        adv.record("f.py", "1-5", "fn", "x" * 40)  # no chunk_id — e.g. locate-sourced
        hint = adv.eviction_hint()
        assert "Re-fetch keys:" not in hint

    def test_hint_caps_fetch_ids_at_configured_max(self):
        from agent.config import EVICTION_HINT_MAX_IDS
        from agent.eviction_advisor import EvictionAdvisor

        adv = EvictionAdvisor()
        for i in range(EVICTION_HINT_MAX_IDS + 5):
            adv.record(f"f{i}.py", "1-5", "fn", "x" * 40, chunk_id=f"f{i}.py:1-5")
        hint = adv.eviction_hint()
        assert hint.count(".py:1-5") <= EVICTION_HINT_MAX_IDS + (
            EVICTION_HINT_MAX_IDS + 5  # the by-file listing above also names each file
        )
        # Precisely: the re-fetch key line itself lists at most the configured cap.
        fetch_line = next(l for l in hint.splitlines() if l.startswith("Re-fetch keys"))
        assert fetch_line.count('"') // 2 == EVICTION_HINT_MAX_IDS

    def test_vectr_search_response_hint_references_a_real_fetch_id(self, tmp_path, monkeypatch):
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path, monkeypatch)
        svc._session_advisors["session-a"] = svc._advisor_for("session-a")
        advisor = svc._advisor_for("session-a")
        advisor._retrieval_call_threshold = 0
        advisor._retrieved_token_gate = 0
        advisor._remember_escalation_chunks = 0

        result = handle_tools_call(
            "vectr_search", {"query": "handler"}, svc, session_id="session-a"
        )
        text = result["content"][0]["text"]
        assert "Context management hint" in text
        assert "vectr_fetch(ids=" in text


# ---------------------------------------------------------------------------
# vectr_snapshot defaults to the calling MCP session, not the anonymous one
# ---------------------------------------------------------------------------

class TestSnapshotDefaultsToCallingSession:
    def test_snapshot_uses_transport_session_id_when_argument_omits_it(self):
        from integrations.mcp_server import handle_tools_call

        svc = MagicMock()
        svc.search_only = False
        svc.snapshot_session.return_value = "snap_1"

        handle_tools_call("vectr_snapshot", {"label": "l"}, svc, session_id="transport-sess")
        svc.snapshot_session.assert_called_once_with(label="l", session_id="transport-sess")

    def test_snapshot_argument_session_id_overrides_transport(self):
        from integrations.mcp_server import handle_tools_call

        svc = MagicMock()
        svc.search_only = False
        svc.snapshot_session.return_value = "snap_1"

        handle_tools_call(
            "vectr_snapshot", {"label": "l", "session_id": "explicit"}, svc,
            session_id="transport-sess",
        )
        svc.snapshot_session.assert_called_once_with(label="l", session_id="explicit")
