"""Tests for UPG-HOOK-INJECT-OBSERVABILITY.

Hook injection (SessionStart/UserPromptSubmit/PreToolUse) lands silently in
the model's context — a working memory system and a dead one look identical
to the human. Covers:

  (a) VectrService counts one injection per hook kind, only when the
      hook-declared recall actually returned notes, surfaced in status().
  (b) The optional per-firing log line, gated by config `hooks.log_injections`
      (default off); a write failure must never break recall.
  (c) `_write_claude_hooks`'s PreToolUse matcher covers file-reading tools
      (Read), not only Edit/Write — covered in tests/test_main.py alongside
      the rest of the hook-installation suite.

`recall(hook_event=...)` is the caller-declared field the daemon counts on —
never inferred from prompt/query content (see RecallRequest.hook_event).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import _DummyEmbedProvider, _RealVectrService


def _make_service(tmp_path, monkeypatch):
    """Memory-only VectrService — dummy embedder (no real model load) since
    the working-memory layer shares the code index's embed functions."""
    from agent import indexer as idx_module
    monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
    with patch("integrations.vscode_bridge.configure_all"), \
         patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
         patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
        svc = _RealVectrService(workspace_root=str(tmp_path), memory_only=True)
    return svc


# ---------------------------------------------------------------------------
# (a) Per-hook-kind injection counters
# ---------------------------------------------------------------------------

class TestHookInjectionCounters:
    def test_hook_declared_recall_with_results_increments_counter(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")

        svc.recall(boot=True, hook_event="SessionStart")

        assert svc.get_hook_injection_counts() == {"SessionStart": 1}

    def test_empty_result_does_not_increment(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)  # no notes stored — boot_recall is empty

        svc.recall(boot=True, hook_event="SessionStart")

        assert svc.get_hook_injection_counts() == {}

    def test_direct_recall_without_hook_event_does_not_increment(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")

        svc.recall(boot=True)  # no hook_event — direct vectr_recall/`vectr recall` shape

        assert svc.get_hook_injection_counts() == {}

    def test_repeated_injections_of_same_kind_accumulate(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")

        svc.recall(boot=True, hook_event="SessionStart")
        svc.recall(boot=True, hook_event="SessionStart")
        svc.recall(boot=True, hook_event="SessionStart")

        assert svc.get_hook_injection_counts() == {"SessionStart": 3}

    def test_different_hook_kinds_tracked_separately(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")
        svc.remember("symbol_graph.py: index_file takes workspace first", kind="gotcha")

        svc.recall(boot=True, hook_event="SessionStart")
        # Path-anchored recall (deterministic substring match, not semantic —
        # see WorkingContextStore.recall_for_path) powers the PreToolUse hook.
        svc.recall(file_path=str(tmp_path / "symbol_graph.py"), kind="gotcha", hook_event="PreToolUse")

        assert svc.get_hook_injection_counts() == {"SessionStart": 1, "PreToolUse": 1}

    def test_status_surfaces_hook_injection_counts(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")

        svc.recall(boot=True, hook_event="SessionStart")

        assert svc.status()["hook_injection_counts"] == {"SessionStart": 1}

    def test_status_hook_injection_counts_empty_dict_when_no_hooks_fired(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        assert svc.status()["hook_injection_counts"] == {}

    def test_get_hook_injection_counts_returns_a_copy(self, tmp_path, monkeypatch):
        """Caller mutating the returned dict must not corrupt internal state."""
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")
        svc.recall(boot=True, hook_event="SessionStart")

        counts = svc.get_hook_injection_counts()
        counts["SessionStart"] = 999

        assert svc.get_hook_injection_counts() == {"SessionStart": 1}


# ---------------------------------------------------------------------------
# (b) Optional per-firing log, gated by hooks.log_injections
# ---------------------------------------------------------------------------

class TestHookInjectionLogGating:
    def test_log_not_written_when_gate_off(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")
        monkeypatch.setattr("app.service.HOOKS_LOG_INJECTIONS", False)
        monkeypatch.setenv("HOME", str(tmp_path))

        svc.recall(boot=True, hook_event="SessionStart")

        assert not (tmp_path / ".vectr" / "logs").exists()

    def test_log_written_when_gate_on(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")
        monkeypatch.setattr("app.service.HOOKS_LOG_INJECTIONS", True)
        monkeypatch.setenv("HOME", str(tmp_path))

        svc.recall(boot=True, hook_event="SessionStart")

        from agent.instance_registry import workspace_hash
        log_path = tmp_path / ".vectr" / "logs" / f"{workspace_hash(svc._workspace_root)}.hooks.log"
        assert log_path.exists()
        line = log_path.read_text().strip()
        assert "SessionStart" in line
        assert "tokens=" in line

    def test_log_gate_off_by_default(self):
        """config.yaml default — hooks.log_injections must ship false."""
        import agent.config as cfg
        assert cfg.HOOKS_LOG_INJECTIONS is False

    def test_chars_per_token_default(self):
        import agent.config as cfg
        assert cfg.HOOKS_LOG_CHARS_PER_TOKEN == 4

    def test_log_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        """Failure to write the optional log must never break recall."""
        svc = _make_service(tmp_path, monkeypatch)
        svc.remember("never push to main", kind="directive", priority="high")
        monkeypatch.setattr("app.service.HOOKS_LOG_INJECTIONS", True)
        monkeypatch.setattr(
            "agent.instance_registry.workspace_hash",
            lambda *_: (_ for _ in ()).throw(OSError("boom")),
        )

        notes = svc.recall(boot=True, hook_event="SessionStart")

        assert "never push to main" in notes
        assert svc.get_hook_injection_counts() == {"SessionStart": 1}


# ---------------------------------------------------------------------------
# (d) Server-side min_similarity default for hook-declared recalls
#     (UPG-HOOK-SUBPROCESS-IMPORT-TAX)
# ---------------------------------------------------------------------------

class TestHookRecallMinSimilarityServerDefault:
    """`vectr hook user-prompt-submit` used to send an explicit
    `min_similarity=HOOKS_MIN_SIMILARITY` on every request, which meant the
    hook subprocess had to import `agent.config` on its hottest path just to
    read one float. `VectrService.recall` now applies that same floor itself
    whenever `hook_event` is set and the caller omitted `min_similarity` —
    the CLI can omit the field entirely and the daemon (which already pays
    `agent.config`'s import cost once, at startup) fills in the canonical
    default. This is the regression coverage for the value itself (raised
    0.35 -> 0.72, adversarial review 2026-07-15) now that it is asserted
    here rather than in the CLI's outbound payload (tests/test_main.py's
    TestCmdHookUserPromptSubmit covers the CLI omitting the field)."""

    def test_hook_event_with_omitted_min_similarity_uses_config_floor(self, tmp_path, monkeypatch):
        svc = _make_service(tmp_path, monkeypatch)
        with patch.object(svc, "_recall_impl", wraps=svc._recall_impl) as mock_impl:
            svc.recall(query="lock flow", hook_event="UserPromptSubmit")
        import agent.config as cfg
        assert mock_impl.call_args.kwargs["min_similarity"] == cfg.HOOKS_MIN_SIMILARITY
        assert cfg.HOOKS_MIN_SIMILARITY == pytest.approx(0.72)

    def test_hook_event_with_explicit_min_similarity_is_not_overridden(self, tmp_path, monkeypatch):
        """An explicit override (VECTR_HOOK_MIN_SIMILARITY env var, threaded
        through by the CLI) still wins — the server only fills the gap, it
        never clobbers a caller-supplied value."""
        svc = _make_service(tmp_path, monkeypatch)
        with patch.object(svc, "_recall_impl", wraps=svc._recall_impl) as mock_impl:
            svc.recall(query="lock flow", hook_event="UserPromptSubmit", min_similarity=0.1)
        assert mock_impl.call_args.kwargs["min_similarity"] == 0.1

    def test_direct_recall_without_hook_event_still_means_no_floor(self, tmp_path, monkeypatch):
        """A direct vectr_recall/`vectr recall` call (hook_event=None) is
        unaffected — omitted min_similarity still means "no floor," exactly
        as before this change."""
        svc = _make_service(tmp_path, monkeypatch)
        with patch.object(svc, "_recall_impl", wraps=svc._recall_impl) as mock_impl:
            svc.recall(query="lock flow")
        assert mock_impl.call_args.kwargs["min_similarity"] is None


# ---------------------------------------------------------------------------
# RecallRequest.hook_event validation (Pydantic model, no HTTP round trip)
# ---------------------------------------------------------------------------

class TestRecallRequestHookEventValidation:
    def test_none_is_valid(self):
        from app.models import RecallRequest
        assert RecallRequest(hook_event=None).hook_event is None

    @pytest.mark.parametrize("value", ["SessionStart", "UserPromptSubmit", "PreToolUse"])
    def test_valid_values_accepted(self, value):
        from app.models import RecallRequest
        assert RecallRequest(hook_event=value).hook_event == value

    def test_invalid_value_rejected(self):
        from pydantic import ValidationError
        from app.models import RecallRequest
        with pytest.raises(ValidationError):
            RecallRequest(hook_event="PreCompact")

    def test_arbitrary_string_rejected(self):
        """Never a free-form/query-derived value — must be exactly one of
        the three declared hook kinds (UPG-HOOK-INJECT-OBSERVABILITY)."""
        from pydantic import ValidationError
        from app.models import RecallRequest
        with pytest.raises(ValidationError):
            RecallRequest(hook_event="session-start")  # CLI arg spelling, not the enum


# ---------------------------------------------------------------------------
# StatusResponse default shape
# ---------------------------------------------------------------------------

class TestStatusResponseHookInjectionCountsDefault:
    def test_defaults_to_empty_dict(self):
        from app.models import StatusResponse
        resp = StatusResponse(
            indexed_files=0, total_chunks=0, last_indexed="never",
            embed_model="x", workspace_root="/repo", processing_ms=0, model="x",
        )
        assert resp.hook_injection_counts == {}
