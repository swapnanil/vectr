"""UPG-MEM-PROVENANCE-USER-STATED — the machine-checkable middle trust class.

A note that merely transcribes what the user said used to render with the
agent frame ("Memory to verify ... not human-endorsed"), understating it,
while an agent misremembering "the user said X" carried no frame at all. The
fix is deliberately NOT a new self-declared provenance value: an agent
asserting its own authority is the forgery `provenance="human"` is already
rejected for. It is a deterministic string check — a verbatim user excerpt
must actually be quoted inside the note's own content — and only that check
can mint the class.

Covered here: the binding function itself, the store's write-time behaviour
(bound, unbound/adversarial, human-wins, directive interaction, persistence),
the promotion ladder's treatment of a derived class, the rendered frame, and
both caller surfaces (MCP dispatch + REST route).
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from agent.config import MEMORY_WRITE_USER_QUOTE_MIN_CHARS
from agent.proactive.types import NOTE_PROVENANCE_TRUST_RANK
from agent.trigger_engine import frame_prefix
from agent.working_context_store import (
    PROMOTION_LADDER,
    PROMOTION_RANK,
    PROVENANCE_VALUES,
    USER_STATED_PROVENANCE,
    WorkingContextStore,
    bind_user_quote,
    normalize_for_binding,
)

# A user turn long enough to clear the minimum-excerpt floor.
QUOTE = "always run the tests through the venv interpreter"
CONTENT = f'The user said: "{QUOTE}" — global python lacks the C grammars.'


def _store(tmp_path) -> WorkingContextStore:
    return WorkingContextStore(str(tmp_path))


# ---------------------------------------------------------------------------
# bind_user_quote — the deterministic check itself
# ---------------------------------------------------------------------------

class TestBindUserQuote:
    def test_exact_substring_binds(self) -> None:
        bound, reason = bind_user_quote(CONTENT, QUOTE)
        assert bound == QUOTE
        assert reason == ""

    def test_rewrapped_quote_still_binds(self) -> None:
        """A transcribed excerpt is routinely re-wrapped; the words are what
        must match, not the line breaks."""
        bound, reason = bind_user_quote(
            "The user said:\n  always run the tests\n  through the venv interpreter\nso do that.",
            "always run the tests through the venv interpreter",
        )
        assert bound == "always run the tests through the venv interpreter"
        assert reason == ""

    def test_bound_quote_keeps_the_users_own_characters(self) -> None:
        """The stored evidence is the excerpt as given (whitespace-stripped at
        the ends only), never a normalized rewrite of it."""
        bound, _ = bind_user_quote(CONTENT, f"  {QUOTE}  ")
        assert bound == QUOTE

    def test_paraphrase_does_not_bind(self) -> None:
        bound, reason = bind_user_quote(
            "The user wants the venv interpreter used for tests.",
            "always run the tests through the venv interpreter",
        )
        assert bound == ""
        assert "does not appear verbatim" in reason

    def test_check_is_case_sensitive(self) -> None:
        bound, reason = bind_user_quote(CONTENT, QUOTE.upper())
        assert bound == ""
        assert reason != ""

    def test_trivial_excerpt_is_rejected_before_containment(self) -> None:
        """Without a floor, a 1-character 'excerpt' would be a substring of
        almost any content — confirming nothing while upgrading trust."""
        bound, reason = bind_user_quote("a note that certainly contains an a", "a")
        assert bound == ""
        assert str(MEMORY_WRITE_USER_QUOTE_MIN_CHARS) in reason

    @pytest.mark.parametrize("quote", [None, "", "   ", "\n\t "])
    def test_absent_quote_is_no_claim_and_no_rejection(self, quote) -> None:
        assert bind_user_quote(CONTENT, quote) == ("", "")

    def test_normalize_collapses_whitespace_only(self) -> None:
        assert normalize_for_binding("  a\n\tb   c  ") == "a b c"

    def test_is_pure_and_repeatable(self) -> None:
        assert bind_user_quote(CONTENT, QUOTE) == bind_user_quote(CONTENT, QUOTE)


# ---------------------------------------------------------------------------
# WorkingContextStore.remember — write-time class derivation
# ---------------------------------------------------------------------------

class TestStoreUserQuoteBinding:
    def test_bound_quote_stores_user_stated_provenance(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(ws, CONTENT, user_quote=QUOTE)
        note = store.get_note(ws, note_id)
        assert note.provenance == USER_STATED_PROVENANCE
        assert note.user_quote == QUOTE

    def test_unbound_quote_stores_agent_provenance_and_discards_the_quote(self, tmp_path) -> None:
        """THE adversarial case: an agent claims the user said something the
        note does not actually quote. The write succeeds, at the ordinary
        class, and the unverified claim is never persisted."""
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(
            ws, "The user wants the venv used.", user_quote=QUOTE,
        )
        note = store.get_note(ws, note_id)
        assert note.provenance == "agent"
        assert note.user_quote == ""

    def test_unbound_quote_is_not_in_the_database_at_all(self, tmp_path) -> None:
        store = _store(tmp_path)
        store.remember(str(tmp_path), "unrelated content", user_quote=QUOTE)
        conn = sqlite3.connect(str(tmp_path / "working_context.sqlite"))
        row = conn.execute("SELECT user_quote FROM notes LIMIT 1").fetchone()
        conn.close()
        assert row[0] == ""

    def test_omitted_quote_keeps_the_default_class(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note = store.get_note(ws, store.remember(ws, CONTENT))
        assert note.provenance == "agent"
        assert note.user_quote == ""

    def test_provenance_user_stated_cannot_be_declared_directly(self, tmp_path) -> None:
        store = _store(tmp_path)
        with pytest.raises(ValueError) as exc:
            store.remember(str(tmp_path), CONTENT, provenance=USER_STATED_PROVENANCE)
        assert "cannot be set directly" in str(exc.value)
        assert "user_quote" in str(exc.value)

    def test_declared_user_stated_is_rejected_even_with_a_bindable_quote(self, tmp_path) -> None:
        """The rejection is about the DECLARATION, not the evidence — the
        same write succeeds when the caller lets the check do the work."""
        store = _store(tmp_path)
        with pytest.raises(ValueError):
            store.remember(
                str(tmp_path), CONTENT,
                provenance=USER_STATED_PROVENANCE, user_quote=QUOTE,
            )

    def test_human_provenance_outranks_a_binding(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note = store.get_note(ws, store.remember(ws, CONTENT, provenance="human", user_quote=QUOTE))
        assert note.provenance == "human"
        assert note.user_quote == QUOTE

    def test_auto_with_a_bound_quote_becomes_user_stated(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note = store.get_note(ws, store.remember(ws, CONTENT, provenance="auto", user_quote=QUOTE))
        assert note.provenance == USER_STATED_PROVENANCE

    def test_bound_quote_makes_an_auto_directive_writable(self, tmp_path) -> None:
        """The auto/directive guard rejects an UNREVIEWED standing rule. A
        directive carrying the user's own bound words is the opposite of
        that, so the guard must see the effective class."""
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(
            ws, CONTENT, kind="directive", provenance="auto", user_quote=QUOTE,
        )
        assert store.get_note(ws, note_id).provenance == USER_STATED_PROVENANCE

    def test_auto_directive_without_a_binding_is_still_rejected(self, tmp_path) -> None:
        store = _store(tmp_path)
        with pytest.raises(ValueError) as exc:
            store.remember(str(tmp_path), CONTENT, kind="directive", provenance="auto")
        assert "directive" in str(exc.value)

    def test_unbindable_quote_does_not_rescue_an_auto_directive(self, tmp_path) -> None:
        store = _store(tmp_path)
        with pytest.raises(ValueError):
            store.remember(
                str(tmp_path), "content that never quotes the user",
                kind="directive", provenance="auto", user_quote=QUOTE,
            )

    def test_binding_survives_a_fresh_store_instance(self, tmp_path) -> None:
        ws = str(tmp_path)
        note_id = _store(tmp_path).remember(ws, CONTENT, user_quote=QUOTE)
        note = _store(tmp_path).get_note(ws, note_id)
        assert note.provenance == USER_STATED_PROVENANCE
        assert note.user_quote == QUOTE

    def test_bound_quote_is_encrypted_alongside_content(self, tmp_path, monkeypatch) -> None:
        """A bound quote is by construction a substring of content — storing
        it in plaintext would leak exactly what encryption protects."""
        monkeypatch.setenv("VECTR_ENCRYPT_KEY", "user-stated-provenance-test-key")
        ws = str(tmp_path)
        store = WorkingContextStore(ws)
        note_id = store.remember(ws, CONTENT, user_quote=QUOTE)
        conn = sqlite3.connect(str(tmp_path / "working_context.sqlite"))
        row = conn.execute("SELECT user_quote FROM notes WHERE note_id = ?", (note_id,)).fetchone()
        conn.close()
        assert QUOTE not in row[0]
        assert store.get_note(ws, note_id).user_quote == QUOTE

    def test_recall_returns_the_bound_class(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        store.remember(ws, CONTENT, user_quote=QUOTE)
        notes = store.recall(ws)
        assert [n.provenance for n in notes] == [USER_STATED_PROVENANCE]


# ---------------------------------------------------------------------------
# Promotion ladder — a derived class is a source, never a target
# ---------------------------------------------------------------------------

class TestPromotionInteraction:
    def test_user_stated_is_not_a_promotion_target(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(ws, "an ordinary agent note")
        with pytest.raises(ValueError):
            store.promote(ws, note_id, USER_STATED_PROVENANCE)

    def test_user_stated_note_promotes_to_human(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(ws, CONTENT, user_quote=QUOTE)
        assert store.promote(ws, note_id, "human") is True
        assert store.get_note(ws, note_id).provenance == "human"

    def test_user_stated_note_cannot_be_demoted_via_promote(self, tmp_path) -> None:
        """An off-ladder value defaulting to rank 0 would have let an
        'auto -> agent' promotion quietly weaken this note."""
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(ws, CONTENT, user_quote=QUOTE)
        with pytest.raises(ValueError):
            store.promote(ws, note_id, "agent")
        assert store.get_note(ws, note_id).provenance == USER_STATED_PROVENANCE

    def test_existing_ladder_steps_are_unchanged(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        note_id = store.remember(ws, "auto-captured", provenance="auto")
        assert store.promote(ws, note_id, "agent") is True
        assert store.promote(ws, note_id, "human") is True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class TestUserStatedFraming:
    def test_frame_is_distinct_from_agent_and_human(self) -> None:
        user_stated = frame_prefix(USER_STATED_PROVENANCE, "finding")
        assert user_stated != frame_prefix("agent", "finding")
        assert user_stated != frame_prefix("human", "finding")

    def test_frame_names_the_binding_and_the_transcriber(self) -> None:
        text = frame_prefix(USER_STATED_PROVENANCE, "finding")
        assert "User-stated" in text
        assert "verbatim excerpt bound" in text
        assert "AI session" in text

    @pytest.mark.parametrize("kind", ["directive", "task", "gotcha", "finding", "reference"])
    def test_same_frame_for_every_kind(self, kind: str) -> None:
        """The unhedged imperative stays reserved for human provenance — a
        bound excerpt states what was checked, it does not command."""
        assert frame_prefix(USER_STATED_PROVENANCE, kind) == frame_prefix(
            USER_STATED_PROVENANCE, "finding"
        )
        assert "DIRECTIVE" not in frame_prefix(USER_STATED_PROVENANCE, kind)

    def test_full_block_marks_the_class_and_frames_the_content(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        store.remember(ws, CONTENT, user_quote=QUOTE)
        rendered = store.format_notes_for_llm(store.recall(ws), detail="full")
        assert f"[{USER_STATED_PROVENANCE}]" in rendered
        assert "User-stated (verbatim excerpt bound" in rendered

    def test_unbound_claim_renders_with_the_agent_frame(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = str(tmp_path)
        store.remember(ws, "The user definitely told me to do this.", user_quote=QUOTE)
        rendered = store.format_notes_for_llm(store.recall(ws), detail="full")
        assert "User-stated" not in rendered
        assert "[agent]" in rendered


# ---------------------------------------------------------------------------
# Vocabulary + rank drift guards
# ---------------------------------------------------------------------------

class TestVocabularyInvariants:
    def test_user_stated_is_a_valid_stored_value(self) -> None:
        assert USER_STATED_PROVENANCE in PROVENANCE_VALUES

    def test_promotion_ladder_excludes_the_derived_class(self) -> None:
        assert PROMOTION_LADDER == ("auto", "agent", "human")
        assert USER_STATED_PROVENANCE not in PROMOTION_LADDER

    def test_user_stated_ranks_with_agent_on_the_ladder(self) -> None:
        assert PROMOTION_RANK[USER_STATED_PROVENANCE] == PROMOTION_RANK["agent"]
        assert PROMOTION_RANK[USER_STATED_PROVENANCE] < PROMOTION_RANK["human"]

    def test_injection_envelope_rank_is_agent_tier_never_human(self) -> None:
        """The proxy envelope describes who authored the text on the wire —
        still an AI session. Listing it explicitly also keeps a mixed block
        from falling to the weakest tier via the unknown-value default."""
        assert NOTE_PROVENANCE_TRUST_RANK[USER_STATED_PROVENANCE] == (
            NOTE_PROVENANCE_TRUST_RANK["agent"]
        )
        assert NOTE_PROVENANCE_TRUST_RANK[USER_STATED_PROVENANCE] < (
            NOTE_PROVENANCE_TRUST_RANK["human"]
        )


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------

def _mcp_service():
    from app.service import RememberOutcome
    from agent.working_context_store import WorkingNote

    svc = MagicMock()
    svc.remember_with_extras.return_value = RememberOutcome(
        note_id=7, related=[], proxy_anchor_suggestions=[],
    )
    svc.get_note.return_value = WorkingNote(
        note_id=7, workspace="/repo", content=CONTENT, tags=[], priority="medium",
        created_at=0.0, last_accessed=0.0, kind="finding", scope="workspace",
        provenance=USER_STATED_PROVENANCE, user_quote=QUOTE,
    )
    svc.search_only = False
    return svc


class TestMcpRememberUserQuote:
    def test_user_quote_is_passed_through_to_the_service(self) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _mcp_service()
        handle_tools_call(
            "vectr_remember", {"content": CONTENT, "user_quote": QUOTE}, svc,
        )
        assert svc.remember_with_extras.call_args.kwargs["user_quote"] == QUOTE

    def test_bound_quote_is_reported_in_the_confirmation(self) -> None:
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_remember", {"content": CONTENT, "user_quote": QUOTE}, _mcp_service(),
        )
        text = result["content"][0]["text"]
        assert result["isError"] is False
        assert USER_STATED_PROVENANCE in text

    def test_unbound_quote_reports_why_without_failing_the_write(self) -> None:
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call(
            "vectr_remember",
            {"content": "content that never quotes the user", "user_quote": QUOTE},
            _mcp_service(),
        )
        text = result["content"][0]["text"]
        assert result["isError"] is False
        assert "Stored note #7" in text
        assert "does not appear verbatim" in text

    def test_omitted_quote_leaves_the_confirmation_unchanged(self) -> None:
        from integrations.mcp_server import handle_tools_call

        result = handle_tools_call("vectr_remember", {"content": CONTENT}, _mcp_service())
        text = result["content"][0]["text"]
        assert "user_quote" not in text
        assert USER_STATED_PROVENANCE not in text

    def test_non_string_user_quote_is_a_caller_error(self) -> None:
        from integrations.mcp_server import handle_tools_call

        svc = _mcp_service()
        result = handle_tools_call(
            "vectr_remember", {"content": CONTENT, "user_quote": 42}, svc,
        )
        assert result["isError"] is True
        svc.remember_with_extras.assert_not_called()

    def test_tool_schema_declares_user_quote(self) -> None:
        from integrations.mcp_server import _MEMORY_WRITE_TOOLS

        remember = next(t for t in _MEMORY_WRITE_TOOLS if t["name"] == "vectr_remember")
        prop = remember["inputSchema"]["properties"]["user_quote"]
        assert prop["type"] == "string"
        assert "user_quote" not in remember["inputSchema"]["required"]

    def test_tool_schema_never_offers_user_stated_as_a_provenance(self) -> None:
        from integrations.mcp_server import _MEMORY_WRITE_TOOLS

        remember = next(t for t in _MEMORY_WRITE_TOOLS if t["name"] == "vectr_remember")
        assert USER_STATED_PROVENANCE not in (
            remember["inputSchema"]["properties"]["provenance"]["enum"]
        )


# ---------------------------------------------------------------------------
# REST surface
# ---------------------------------------------------------------------------

class TestRestRememberUserQuote:
    def test_bound_quote_round_trips_to_user_stated(self, client_real_memory) -> None:
        resp = client_real_memory.post(
            "/v1/remember", json={"content": CONTENT, "user_quote": QUOTE},
        )
        assert resp.status_code == 200
        assert USER_STATED_PROVENANCE in resp.json()["message"]
        recalled = client_real_memory.post(
            "/v1/recall", json={"note_id": resp.json()["note_id"]},
        ).json()["notes"]
        assert f"[{USER_STATED_PROVENANCE}]" in recalled
        assert "User-stated (verbatim excerpt bound" in recalled

    def test_unbound_quote_returns_200_and_the_reason(self, client_real_memory) -> None:
        resp = client_real_memory.post("/v1/remember", json={
            "content": "content that never quotes the user", "user_quote": QUOTE,
        })
        assert resp.status_code == 200
        assert "does not appear verbatim" in resp.json()["message"]
        recalled = client_real_memory.post(
            "/v1/recall", json={"note_id": resp.json()["note_id"]},
        ).json()["notes"]
        assert "[agent]" in recalled
        assert "User-stated" not in recalled

    def test_omitted_quote_leaves_the_message_unchanged(self, client_real_memory) -> None:
        resp = client_real_memory.post("/v1/remember", json={"content": CONTENT})
        assert "user_quote" not in resp.json()["message"]

    def test_declared_user_stated_provenance_is_rejected_at_the_schema(self, client) -> None:
        resp = client.post("/v1/remember", json={
            "content": CONTENT, "provenance": USER_STATED_PROVENANCE,
        })
        assert resp.status_code == 422

    def test_promote_route_never_accepts_the_derived_class(self, client) -> None:
        resp = client.post("/v1/promote", json={"note_id": 1, "to": USER_STATED_PROVENANCE})
        assert resp.status_code == 422
