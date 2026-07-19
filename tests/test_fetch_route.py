"""Tests for the deterministic re-fetch-by-chunk-id contract (UPG-CTX-EVICT part a).

Covers:
- VectrService.fetch(): delegates to the indexer, raises the same clean
  memory-only error as /v1/index in memory-only mode
- REST POST /v1/fetch: found, missing (with shared note), 503 memory-only,
  422 cap-exceeded, and CodeChunkResult.id population on /v1/search
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import _base_mock_service


class TestServiceFetch:
    @staticmethod
    def _svc(workspace_root: str = ""):
        from app.service import VectrService
        svc = VectrService.__new__(VectrService)
        svc._memory_only = False
        svc._workspace_root = workspace_root
        svc._indexer = type("_I", (), {})()
        calls = []
        svc._indexer.fetch_chunks = lambda ids: calls.append(ids) or [
            {"id": ids[0], "found": True, "file_path": "a.py", "start_line": 1,
             "end_line": 2, "symbol_name": "", "language": "python", "content": "x"}
        ]
        return svc, calls

    def test_delegates_to_indexer_fetch_chunks(self) -> None:
        svc, calls = self._svc()
        result = svc.fetch(["a.py:1-2"])
        assert calls == [["a.py:1-2"]]
        assert result[0]["found"] is True

    def test_resolves_relative_id_to_absolute_before_lookup(self) -> None:
        """UPG-RELATIVE-PATH-RENDER: search/evict now emit workspace-relative
        chunk ids, but the index stores absolute ones — a relative id must be
        joined onto the workspace root before the ChromaDB lookup."""
        svc, calls = self._svc(workspace_root="/repo")
        svc.fetch(["django/db/base.py:10-20"])
        assert calls == [["/repo/django/db/base.py:10-20"]]

    def test_absolute_id_passes_through_unchanged(self) -> None:
        """Back-compat: an absolute id (what existing sessions hold) is not
        re-rooted — it fetches exactly as before."""
        svc, calls = self._svc(workspace_root="/repo")
        svc.fetch(["/repo/django/db/base.py:10-20"])
        assert calls == [["/repo/django/db/base.py:10-20"]]

    def test_raises_memory_only_message_in_memory_only_mode(self) -> None:
        from app.service import VectrService, _MEMORY_ONLY_MSG

        svc = VectrService.__new__(VectrService)
        svc._memory_only = True
        with pytest.raises(RuntimeError) as exc_info:
            svc.fetch(["a.py:1-2"])
        assert str(exc_info.value) == _MEMORY_ONLY_MSG


class TestRestFetchRoute:
    def _client(self, svc):
        from fastapi.testclient import TestClient
        from api import app
        with patch("app.service.VectrService", return_value=svc):
            with TestClient(app, raise_server_exceptions=False) as c:
                app.state.service = svc
                yield c

    def test_found_ids_return_content(self) -> None:
        svc = _base_mock_service()
        svc.memory_only = False
        svc.fetch.return_value = [
            {"id": "a.py:1-5", "found": True, "file_path": "a.py", "start_line": 1,
             "end_line": 5, "symbol_name": "foo", "language": "python", "content": "def foo(): pass"},
        ]
        for c in self._client(svc):
            resp = c.post("/v1/fetch", json={"ids": ["a.py:1-5"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"][0]["found"] is True
        assert body["results"][0]["content"] == "def foo(): pass"
        assert body["results"][0]["lines"] == "1-5"
        assert body["note"] is None

    def test_missing_id_reports_found_false_with_shared_note(self) -> None:
        svc = _base_mock_service()
        svc.memory_only = False
        svc.fetch.return_value = [{"id": "gone.py:1-5", "found": False}]
        for c in self._client(svc):
            resp = c.post("/v1/fetch", json={"ids": ["gone.py:1-5"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"][0]["found"] is False
        assert body["note"]

    def test_memory_only_returns_503(self) -> None:
        svc = _base_mock_service()
        svc.memory_only = True
        for c in self._client(svc):
            resp = c.post("/v1/fetch", json={"ids": ["a.py:1-5"]})
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "memory_only_mode"

    def test_cap_exceeded_returns_422(self) -> None:
        svc = _base_mock_service()
        svc.memory_only = False
        svc.fetch.side_effect = ValueError("Too many ids requested (999) — vectr_fetch accepts at most 20 per call.")
        for c in self._client(svc):
            resp = c.post("/v1/fetch", json={"ids": ["x"] * 999})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "too_many_ids"

    def test_empty_ids_returns_422_validation_error(self) -> None:
        svc = _base_mock_service()
        svc.memory_only = False
        for c in self._client(svc):
            resp = c.post("/v1/fetch", json={"ids": []})
        assert resp.status_code == 422

    def test_search_route_populates_chunk_id(self) -> None:
        from agent.searcher import SearchResult

        svc = _base_mock_service()
        svc.memory_only = False
        result = SearchResult(
            file_path="src/auth.py", lines="10-30", symbol_name="verify_token",
            language="python", score=0.91, content="def verify_token(): ...",
            chunk_id="src/auth.py:10-30",
        )
        svc.search.return_value = ([result], 15)
        for c in self._client(svc):
            resp = c.post("/v1/search", json={"query": "auth flow"})
        assert resp.status_code == 200
        assert resp.json()["results"][0]["id"] == "src/auth.py:10-30"
