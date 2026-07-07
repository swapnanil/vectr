"""Tests for UPG-SUBAGENT-MEMORY.

Working memory (remember/recall) is a shared bus, not a per-session store:
unlike EvictionAdvisor (UPG-EVICT-SESSION-SCOPE, deliberately per-session so
one caller's retrieved-chunk bookkeeping never bleeds into another's), notes
written under one MCP session id must be visible to recall from a different
session id — that's the mechanism an orchestrator/subagent pair relies on to
share findings through the same daemon + workspace.

Covers:
  (a) two distinct session ids (the Mcp-Session-Id handshake, UPG-MCP-SESSION-
      ID-HANDSHAKE) both see each other's notes via handle_tools_call.
  (b) an optional caller-declared `agent` identifier is stored on the note and
      rendered as an attribution tag in recall index lines — never inferred,
      and absent renders exactly as before this feature.
"""
from __future__ import annotations

from unittest.mock import patch

from tests.conftest import _DummyEmbedProvider, _RealVectrService


def _make_service(tmp_path):
    from unittest.mock import patch as _patch
    with _patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()), \
         _patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path), "VECTR_EMBED_MODEL": "dummy"}):
        svc = _RealVectrService(workspace_root=str(tmp_path))
    return svc


# ---------------------------------------------------------------------------
# (a) Cross-session note visibility — the orchestrator/subagent shared bus
# ---------------------------------------------------------------------------

class TestCrossSessionNoteSharing:
    def test_note_written_by_one_session_is_recalled_by_another(self, tmp_path) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path)
        handle_tools_call(
            "vectr_remember",
            {"content": "subagent finding: the parser bug is in tokenize()"},
            svc, session_id="subagent-session",
        )

        result = handle_tools_call("vectr_recall", {}, svc, session_id="orchestrator-session")
        text = result["content"][0]["text"]
        assert "tokenize" in text

    def test_notes_from_both_sessions_are_visible_to_both(self, tmp_path) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path)
        handle_tools_call(
            "vectr_remember", {"content": "orchestrator directive: use small batches"},
            svc, session_id="orchestrator-session",
        )
        handle_tools_call(
            "vectr_remember", {"content": "subagent finding: batch size caps at 50"},
            svc, session_id="subagent-session",
        )

        recall_from_orchestrator = handle_tools_call(
            "vectr_recall", {}, svc, session_id="orchestrator-session"
        )["content"][0]["text"]
        recall_from_subagent = handle_tools_call(
            "vectr_recall", {}, svc, session_id="subagent-session"
        )["content"][0]["text"]

        for text in (recall_from_orchestrator, recall_from_subagent):
            assert "use small batches" in text or "small batches" in text
            assert "batch size caps" in text or "caps at 50" in text

    def test_anonymous_and_named_sessions_share_the_same_notes(self, tmp_path) -> None:
        """No session id at all (e.g. a REST caller) still shares the same
        workspace-scoped note store as any MCP session — memory is not
        session-scoped, only the eviction advisor is."""
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path)
        handle_tools_call("vectr_remember", {"content": "anonymous caller finding"}, svc)

        result = handle_tools_call("vectr_recall", {}, svc, session_id="named-session")
        assert "anonymous caller finding" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# (b) Caller-declared agent attribution
# ---------------------------------------------------------------------------

class TestAgentAttribution:
    def test_recall_shows_attribution_for_agent_declared_note(self, tmp_path) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path)
        handle_tools_call(
            "vectr_remember",
            {"content": "found the off-by-one in the loop", "agent": "coder-2"},
            svc, session_id="subagent-session",
        )

        result = handle_tools_call("vectr_recall", {}, svc, session_id="orchestrator-session")
        text = result["content"][0]["text"]
        assert "(coder-2)" in text

    def test_recall_omits_attribution_when_agent_not_declared(self, tmp_path) -> None:
        """Backwards compatible: no `agent` argument renders exactly as
        before this feature — never inferred."""
        from integrations.mcp_server import handle_tools_call

        svc = _make_service(tmp_path)
        handle_tools_call(
            "vectr_remember", {"content": "a note with no declared agent"},
            svc, session_id="subagent-session",
        )

        result = handle_tools_call("vectr_recall", {}, svc, session_id="orchestrator-session")
        text = result["content"][0]["text"]
        assert "a note with no declared agent" in text
        assert "()" not in text

    def test_boot_recall_also_carries_agent_attribution(self, tmp_path) -> None:
        """The SessionStart boot injection path renders directives at full
        detail, which already carries author_id as `@agent` (pre-existing
        format); index-tier notes carry it as `(agent)` (this feature)."""
        svc = _make_service(tmp_path)
        svc.remember("standing rule from a subagent", kind="directive", agent="coder-1")

        result = svc.recall(boot=True)
        assert "@coder-1" in result

    def test_boot_recall_task_note_uses_index_tier_attribution(self, tmp_path) -> None:
        """High-priority task notes in the boot set render at index tier —
        this feature's `(agent)` tag, not the full-detail `@agent` form."""
        svc = _make_service(tmp_path)
        svc.remember("in-progress task state", kind="task", priority="high", agent="coder-2")

        result = svc.recall(boot=True)
        assert "(coder-2)" in result
