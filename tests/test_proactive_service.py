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


# -- loopback bind enforcement (UPG-PROXY-LOOPBACK-BYPASS) ------------------

def test_proactive_refuses_non_loopback_bind_on_proxy_channel(tmp_path, monkeypatch):
    # B4: channel="proxy" is the field's DEFAULT and was exempt from BOTH the
    # master switch and the bind check. The bind refusal must be
    # unconditional and channel-independent: it fires even for the proxy
    # channel's own launch-is-consent exemption, and even with the master
    # switch on.
    monkeypatch.setenv("VECTR_BIND_HOST", "0.0.0.0")
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    svc.remember("resolver.py holds the workspace lock; drops on scope exit", kind="gotcha")
    out = svc.proactive_context(text="", file_paths=["/abs/resolver.py"], session_id="s1", channel="proxy")
    assert out == {"context": "", "item_count": 0, "anchor_ids": [], "scores": []}


def test_proactive_refuses_non_loopback_bind_regardless_of_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("VECTR_BIND_HOST", "10.0.0.5")
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    svc.remember("resolver.py holds the workspace lock; drops on scope exit", kind="gotcha")
    for channel in ("proxy", "hook", "SessionStart", "anything-a-client-sends"):
        out = svc.proactive_context(
            text="", file_paths=["/abs/resolver.py"], session_id="s1", channel=channel
        )
        assert out["item_count"] == 0, channel


def test_proactive_still_injects_on_loopback_bind(tmp_path, monkeypatch):
    # Regression guard: the new bind check must not break the ordinary,
    # default (loopback) posture — VECTR_BIND_HOST unset falls back to
    # 127.0.0.1, which is loopback.
    monkeypatch.delenv("VECTR_BIND_HOST", raising=False)
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    nid = svc.remember("resolver.py holds the workspace lock; drops on scope exit", kind="gotcha")
    out = svc.proactive_context(text="", file_paths=["/abs/resolver.py"], session_id="s1", channel="proxy")
    assert f"note:{nid}" in out["anchor_ids"]


# -- cross-channel dedup (UPG-PROXY-CROSS-CHANNEL-DEDUP) --------------------

def test_proxy_and_hook_channels_share_one_turn_ledger(tmp_path, monkeypatch):
    """Integration proof (service level) that `proactive_context` (proxy)
    and `recall`/`_recall_impl` (hook/trigger-engine surface) consult the
    SAME per-session `TurnInjectionLedger` via `_turn_ledger_for` — the same
    note must not inject twice across both channels in one turn under a
    shared session id."""
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    (tmp_path / "resolver.py").write_text("# lock resolver\n")
    abs_path = str(tmp_path / "resolver.py")
    nid = svc.remember(
        "resolver.py holds the workspace lock; drops on scope exit",
        kind="gotcha",
        anchors=["resolver.py"],
    )
    sid = "shared-session"

    # Surface 1 (hook/trigger-engine): PreToolUse file-anchored delivery
    # fires the note's declared pre-edit trigger and claims it.
    hook_text = svc.recall(file_path=abs_path, session_id=sid, hook_event="PreToolUse")
    assert "resolver.py" in hook_text

    # Surface 2 (proxy channel), SAME session/turn: already claimed by
    # surface 1 this turn -> must NOT re-inject.
    proxy_out = svc.proactive_context(
        text="", file_paths=[abs_path], session_id=sid, channel="proxy",
    )
    assert f"note:{nid}" not in proxy_out["anchor_ids"]

    # Non-vacuous: a FRESH session's proxy call (ledger untouched) DOES
    # inject the same note.
    fresh_out = svc.proactive_context(
        text="", file_paths=[abs_path], session_id="fresh-session", channel="proxy",
    )
    assert f"note:{nid}" in fresh_out["anchor_ids"]

    # After the shared session's turn boundary (a real UserPromptSubmit),
    # the proxy channel becomes eligible again.
    svc.reset_turn_ledger(sid)
    reset_out = svc.proactive_context(
        text="", file_paths=[abs_path], session_id=sid, channel="proxy",
    )
    assert f"note:{nid}" in reset_out["anchor_ids"]


def test_proxy_call_with_unseen_session_id_does_not_allocate_turn_ledger(tmp_path, monkeypatch):
    """`proactive_context` must consult the shared TurnInjectionLedger
    LOOKUP-ONLY (`_existing_turn_ledger`), never allocate one
    (`_turn_ledger_for`). The proxy channel's session_id (sha256 of the
    first user message) is, today, disjoint from the editor-supplied
    session id hook/trigger-engine surfaces use — if a proxy call allocated
    a ledger no hook surface will ever look up, that churn would consume
    slots in the EVICTION_MAX_TRACKED_SESSIONS-bounded `_turn_ledgers` LRU
    for zero dedup benefit, risking eviction of a live editor session's real
    ledger under heavy multi-subagent session churn."""
    svc = _service(tmp_path, monkeypatch, VECTR_PROACTIVE="1")
    svc.remember("resolver.py holds the workspace lock; drops on scope exit", kind="gotcha")
    assert len(svc._turn_ledgers) == 0

    svc.proactive_context(
        text="", file_paths=["/abs/resolver.py"], session_id="never-seen-before", channel="proxy",
    )
    assert len(svc._turn_ledgers) == 0
    assert "never-seen-before" not in svc._turn_ledgers


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
