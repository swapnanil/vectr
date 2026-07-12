"""Service-level proactive + artifact-cache tests (UPG-PRO-1/4/5/7 + caching).

Uses a memory-only VectrService with the dummy embedder — no reranker, no model
download, no network — so the wiring (matcher -> gate, scored recall, cache
invalidation) is exercised end to end without the heavy full-mode stack.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.searcher import SearchResult, SearchResultList


def _service(tmp_path, monkeypatch, **env):
    from agent import indexer as idx_module
    from tests.conftest import _DummyEmbedProvider

    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        from app.service import VectrService
        return VectrService(workspace_root=str(tmp_path), memory_only=True)


# -- proactive_context ------------------------------------------------------

def test_proactive_disabled_returns_empty_for_hook_channel(tmp_path, monkeypatch):
    # The master opt-in still gates AMBIENT surfaces: hook-channel requests
    # inject nothing while proactive.enabled is off.
    monkeypatch.delenv("VECTR_PROACTIVE", raising=False)
    svc = _service(tmp_path, monkeypatch)
    svc.remember("resolver.py: the lock drops on scope exit", kind="gotcha")
    out = svc.proactive_context(text="lock", file_paths=["/x/resolver.py"], channel="hook")
    assert out == {"context": "", "item_count": 0, "anchor_ids": [], "scores": []}


def test_proactive_proxy_channel_injects_without_master_switch(tmp_path, monkeypatch):
    # UPG-PROXY-HIDDEN-MASTER-SWITCH: launching the proxy IS the consent for
    # the proxy channel — it must inject even with the master opt-in unset.
    monkeypatch.delenv("VECTR_PROACTIVE", raising=False)
    svc = _service(tmp_path, monkeypatch)
    nid = svc.remember("resolver.py holds the workspace lock; drops on scope exit", kind="gotcha")
    out = svc.proactive_context(text="", file_paths=["/abs/resolver.py"], session_id="s1", channel="proxy")
    assert out["item_count"] >= 1
    assert f"note:{nid}" in out["anchor_ids"]
    assert svc.get_proactive_injection_counts().get("proxy", 0) == 1


def test_status_exposes_proactive_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VECTR_PROACTIVE", raising=False)
    svc = _service(tmp_path, monkeypatch)
    assert svc.status()["proactive_enabled"] is False
    monkeypatch.setenv("VECTR_PROACTIVE", "1")
    assert svc.status()["proactive_enabled"] is True


def test_proactive_structural_note_injected(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    nid = svc.remember("resolver.py holds the workspace lock; drops on scope exit", kind="gotcha")
    out = svc.proactive_context(text="", file_paths=["/abs/resolver.py"], session_id="s1")
    assert out["item_count"] >= 1
    assert f"note:{nid}" in out["anchor_ids"]
    assert "resolver.py" in out["context"]
    # A metadata-only injection was counted.
    assert svc.get_proactive_injection_counts().get("proxy", 0) == 1


def test_proactive_no_match_returns_empty(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    svc.remember("resolver.py note", kind="gotcha")
    out = svc.proactive_context(text="", file_paths=["/x/unrelated_file.py"], session_id="s1")
    assert out["item_count"] == 0
    assert out["context"] == ""


def test_proactive_dedup_cooldown_across_calls(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    svc.remember("resolver.py holds the lock", kind="gotcha")
    first = svc.proactive_context(file_paths=["/x/resolver.py"], session_id="sess")
    second = svc.proactive_context(file_paths=["/x/resolver.py"], session_id="sess")
    assert first["item_count"] == 1
    assert second["item_count"] == 0  # cooldown suppresses the repeat


# -- recall_scored (UPG-PRO-1) ----------------------------------------------

def test_recall_scored_returns_scores(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    content = "handle_legacy_finalizers appends to gc.garbage when tp_del is set here now"
    svc.remember(content)
    scored = svc.recall_scored(query=content, limit=5)
    assert scored
    note, score = scored[0]
    assert note.content == content
    assert score is not None and 0.0 <= score <= 1.0001


def test_recall_scored_sql_fallback_none(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.remember("some note body")
    scored = svc.recall_scored(query=None, limit=5)  # no query -> SQL path
    assert scored
    assert all(s is None for (_n, s) in scored)  # never a fabricated number


# -- artifact cache (org-wide caching) --------------------------------------

def test_search_cache_hit_and_metrics(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE_CACHE="1")
    assert svc.cache_metrics() is not None  # cache is enabled

    calls = {"n": 0}
    result = SearchResultList([
        SearchResult(file_path="a.py", lines="1-2", symbol_name="foo",
                     language="python", score=0.9, content="def foo(): ...")
    ])

    def _fake_search(query, n_results, language, semantic_weight):
        calls["n"] += 1
        return result, 12

    monkeypatch.setattr(svc._searcher, "search", _fake_search)

    r1, ms1 = svc.search("lock", n_results=5)
    r2, ms2 = svc.search("lock", n_results=5)  # identical -> cache hit
    assert calls["n"] == 1                      # searcher ran once
    assert ms2 == 0                             # hit reports no search time
    assert list(r1) == list(r2)
    m = svc.cache_metrics()
    assert m["hits"] == 1 and m["misses"] == 1


def test_recall_scored_cache_invalidates_on_note_change(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE_CACHE="1")
    svc.remember("alpha note about the workspace lock resolver flow here")
    q = "alpha note about the workspace lock resolver flow here"
    svc.recall_scored(query=q)          # miss -> stored
    svc.recall_scored(query=q)          # hit
    m1 = svc.cache_metrics()
    assert m1["hits"] == 1
    # A new note bumps the notes epoch -> the prior cache key can never match.
    svc.remember("beta note changes the notes epoch")
    svc.recall_scored(query=q)          # miss again (epoch changed)
    m2 = svc.cache_metrics()
    assert m2["misses"] == m1["misses"] + 1
