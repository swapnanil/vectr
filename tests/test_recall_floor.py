"""Tests for UPG-RECALL-MISS-FLOOR parts (b) and (c):

  (b) Tier 0 — kind='directive' notes plus explicitly pinned notes, injected
      unconditionally on every WorkingContextStore.recall(apply_floor=True)
      call, never scored against the query, bounded by
      agent/config.yaml's recall_floor.tier0_max_notes.
  (c) Deterministic channels — a note whose OWN declared tag, anchor
      basename, trigger symbol, or title token set is found (containment)
      in the query text is guaranteed a slot ahead of the ranked
      (semantic/SQL) result, bounded by recall_floor.deterministic_max_notes.

Every channel here matches a NOTE's OWN stored metadata against the query
string (symmetric containment) — never a classification of the query's
content/intent/type. See _note_matches_declared_metadata()'s docstring in
agent/working_context_store/_store.py for the same rail stated at the
implementation site.

apply_floor defaults False (WorkingContextStore.recall()'s pre-existing
behavior, unchanged for every caller that predates this parameter); these
tests exercise apply_floor=True explicitly, matching the one production
call site that passes it (VectrService._recall_impl()'s ordinary query
branch, app/service.py).
"""
from __future__ import annotations

import pytest


def _store(tmp_path):
    from agent.working_context_store import WorkingContextStore
    return WorkingContextStore(str(tmp_path))


def _semantic_store(tmp_path):
    """Deterministic hash-based embedder — same pattern as
    tests/test_memory.py's _semantic_store / tests/test_recall_miss_harness.py's
    _build_dummy_fixture_store, so semantic-path composition is covered too,
    not just the SQL LIKE fallback path."""
    import hashlib

    import chromadb

    from agent.working_context_store import WorkingContextStore

    def _dummy_embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.md5(t.encode()).digest()
            vec = [(b / 255.0 - 0.5) for b in (h * 48)]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return WorkingContextStore(str(tmp_path), embed_fn=_dummy_embed, notes_chroma_client=client)


class TestApplyFloorDefaultOff:
    """apply_floor's default (False) must reproduce recall()'s exact
    pre-existing behavior — no floor notes, no change in result set or
    ordering, for both the SQL and (in a later class) the semantic path."""

    def test_default_omits_directive_when_off_topic(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        store.remember(ws, "never push straight to main", kind="directive")
        store.remember(ws, "completely unrelated content about zebras")
        notes = store.recall(ws, query="zebras")
        assert {n.content for n in notes} == {"completely unrelated content about zebras"}

    def test_default_omits_pinned_when_off_topic(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(ws, "workspace lock acquisition pattern")
        store.set_pinned(ws, nid, True)
        store.remember(ws, "completely unrelated content about zebras")
        notes = store.recall(ws, query="zebras")
        assert {n.content for n in notes} == {"completely unrelated content about zebras"}


class TestTier0UnconditionalInjection:
    """Part (b): directive + pinned notes surface on EVERY query, including
    one that shares no vocabulary with the note at all, only when
    apply_floor=True."""

    def test_directive_injected_regardless_of_query(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        store.remember(ws, "never push straight to main", kind="directive")
        store.remember(ws, "some other completely unrelated finding about zebras")
        notes = store.recall(ws, query="zebras", apply_floor=True)
        assert any(n.kind == "directive" for n in notes)

    def test_pinned_note_injected_regardless_of_query(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(ws, "workspace lock acquisition pattern")
        store.set_pinned(ws, nid, True)
        store.remember(ws, "completely unrelated content about giraffes")
        notes = store.recall(ws, query="giraffes", apply_floor=True)
        assert nid in {n.note_id for n in notes}

    def test_pin_at_write_time_equivalent_to_set_pinned(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(ws, "pinned at write time", pin=True)
        note = store.get_note(ws, nid)
        assert note is not None
        assert note.pinned is True
        store.remember(ws, "unrelated content about kangaroos")
        notes = store.recall(ws, query="kangaroos", apply_floor=True)
        assert nid in {n.note_id for n in notes}

    def test_unpinned_regular_note_not_injected_off_topic(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        store.remember(ws, "an ordinary finding, never pinned, never a directive")
        store.remember(ws, "completely unrelated content about elephants")
        notes = store.recall(ws, query="elephants", apply_floor=True)
        assert {n.content for n in notes} == {"completely unrelated content about elephants"}

    def test_set_pinned_unpin_removes_from_tier0(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(ws, "workspace lock acquisition pattern", pin=True)
        store.set_pinned(ws, nid, False)
        store.remember(ws, "completely unrelated content about wombats")
        notes = store.recall(ws, query="wombats", apply_floor=True)
        assert nid not in {n.note_id for n in notes}

    def test_set_pinned_returns_false_for_missing_note(self, tmp_path) -> None:
        store = _store(tmp_path)
        assert store.set_pinned("/ws", 99999, True) is False

    def test_set_pinned_idempotent(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(ws, "some note")
        assert store.set_pinned(ws, nid, True) is True
        assert store.set_pinned(ws, nid, True) is True  # already pinned, still True

    def test_tier0_bounded_by_config(self, tmp_path) -> None:
        from agent.config import RECALL_FLOOR_TIER0_MAX_NOTES

        store = _store(tmp_path)
        ws = "/ws"
        for i in range(RECALL_FLOOR_TIER0_MAX_NOTES + 5):
            store.remember(ws, f"standing rule number {i}", kind="directive")
        store.remember(ws, "unrelated content about penguins")
        notes = store.recall(ws, query="penguins", apply_floor=True, limit=50)
        directive_count = sum(1 for n in notes if n.kind == "directive")
        assert directive_count <= RECALL_FLOOR_TIER0_MAX_NOTES

    def test_semantic_path_also_composes_tier0(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/ws"
        store.remember(ws, "never push straight to main", kind="directive")
        store.remember(ws, "some other finding about database connection pooling")
        notes = store.recall(ws, query="something entirely unrelated to either note", apply_floor=True)
        assert any(n.kind == "directive" for n in notes)


class TestDeterministicChannels:
    """Part (c): a note whose own tag/anchor/symbol/title metadata is found
    in the query text is guaranteed a slot, even when its content shares no
    vocabulary with the query (so plain SQL LIKE / semantic similarity would
    otherwise miss it)."""

    def test_tag_channel(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(
            ws, "some content with no lexical overlap to the query below",
            tags=["workspace-lock"],
        )
        store.remember(ws, "an unrelated note")
        notes = store.recall(ws, query="explain the workspace-lock behavior", apply_floor=True)
        assert nid in {n.note_id for n in notes}

    def test_anchor_channel(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(
            ws, "some content with no lexical overlap to the query below",
            anchors=["agent/resolver.py"],
        )
        store.remember(ws, "an unrelated note")
        notes = store.recall(ws, query="what does resolver.py do", apply_floor=True)
        assert nid in {n.note_id for n in notes}

    def test_symbol_channel(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(
            ws, "some content with no lexical overlap to the query below",
            triggers=[{"symbol": "WorkspaceLock"}],
        )
        store.remember(ws, "an unrelated note")
        notes = store.recall(ws, query="how is WorkspaceLock constructed", apply_floor=True)
        assert nid in {n.note_id for n in notes}

    def test_symbol_channel_is_case_sensitive(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(
            ws, "some content with no lexical overlap to the query below",
            triggers=[{"symbol": "WorkspaceLock"}],
        )
        store.remember(ws, "an unrelated note")
        # lowercase 'workspacelock' does not match the declared symbol
        # 'WorkspaceLock' verbatim -- code identifiers are case-sensitive.
        notes = store.recall(ws, query="how is workspacelock constructed", apply_floor=True)
        assert nid not in {n.note_id for n in notes}

    def test_title_channel_requires_min_overlap(self, tmp_path) -> None:
        from agent.config import RECALL_FLOOR_TITLE_MIN_OVERLAP_TOKENS

        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(
            ws, "some content with no lexical overlap to the query below",
            title="workspace lock acquisition and release",
        )
        store.remember(ws, "an unrelated note")
        if RECALL_FLOOR_TITLE_MIN_OVERLAP_TOKENS <= 2:
            notes = store.recall(ws, query="workspace lock behavior", apply_floor=True)
            assert nid in {n.note_id for n in notes}
        # A query overlapping only a single title token must not trip the
        # channel when the configured floor requires more than one token.
        if RECALL_FLOOR_TITLE_MIN_OVERLAP_TOKENS >= 2:
            notes_single = store.recall(ws, query="workspace unrelated single token", apply_floor=True)
            assert nid not in {n.note_id for n in notes_single}

    def test_no_channel_matches_no_declared_metadata(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        store.remember(ws, "some content with no lexical overlap to the query below")
        store.remember(ws, "an unrelated note about penguins")
        notes = store.recall(ws, query="explain the workspace-lock behavior", apply_floor=True)
        assert notes == []

    def test_deterministic_bounded_by_config(self, tmp_path) -> None:
        from agent.config import RECALL_FLOOR_DETERMINISTIC_MAX_NOTES

        store = _store(tmp_path)
        ws = "/ws"
        for i in range(RECALL_FLOOR_DETERMINISTIC_MAX_NOTES + 5):
            store.remember(ws, f"content {i} with no lexical overlap", tags=["shared-tag"])
        notes = store.recall(ws, query="something about shared-tag", apply_floor=True, limit=50)
        assert len(notes) <= RECALL_FLOOR_DETERMINISTIC_MAX_NOTES

    def test_semantic_path_also_composes_deterministic_channel(self, tmp_path) -> None:
        store = _semantic_store(tmp_path)
        ws = "/ws"
        nid = store.remember(
            ws, "some content with no lexical overlap to the query below",
            tags=["workspace-lock"],
        )
        store.remember(ws, "a completely different finding about database pooling")
        notes = store.recall(ws, query="explain workspace-lock please", apply_floor=True)
        assert nid in {n.note_id for n in notes}


class TestLimitInvariant:
    """The composed result must never exceed `limit`, regardless of how many
    Tier 0 + deterministic + ranked candidates are available — this is the
    exact invariant benchmarks/recall_miss/harness.py's STEP 0 assertion
    guards against a regression in."""

    def test_never_exceeds_limit_with_many_candidates(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        for i in range(10):
            store.remember(ws, f"standing rule {i}", kind="directive")
        for i in range(10):
            store.remember(ws, f"content {i} about shared-topic", tags=["shared-topic"])
        for i in range(10):
            store.remember(ws, f"ranked content {i} mentions shared-topic directly")
        notes = store.recall(ws, query="tell me about shared-topic", apply_floor=True, limit=5)
        assert len(notes) <= 5

    def test_dedup_no_double_counting_across_tiers(self, tmp_path) -> None:
        """A note that is BOTH a directive AND tag-matches the query must
        only ever appear once in the composed result."""
        store = _store(tmp_path)
        ws = "/ws"
        nid = store.remember(ws, "a standing rule", kind="directive", tags=["shared-tag"])
        notes = store.recall(ws, query="something about shared-tag", apply_floor=True, limit=10)
        ids = [n.note_id for n in notes]
        assert ids.count(nid) == 1


class TestFloorReservesRankedRoom:
    """UPG-RECALL-MISS-FLOOR bugfix, found while implementing this task and
    confirmed live (benchmarks/recall_miss/run_live.py --apply-floor
    against a real 40-note-labeled corpus): recall_floor.tier0_max_notes +
    deterministic_max_notes (5 + 5) exactly equalled recall()'s own
    default limit=10, so a workspace with enough directive and
    tag/anchor/symbol/title-matched notes to saturate BOTH absolute caps
    let the combined floor claim the entire budget and displace the
    ranked result completely -- measured live, recall@10 fell from 0.725
    to 0.100 (29/40 hits collapsed to 4/40) with the two absolute caps
    alone. Each channel is now ALSO capped to its own fraction of the
    caller's `limit` (recall_floor.tier0_max_fraction_of_limit /
    deterministic_max_fraction_of_limit -- see `_recall_floor_notes()` in
    agent/working_context_store/_store.py), so a genuinely relevant
    ranked-only note always keeps room in the composed result, at any
    `limit` a caller passes -- not just the ones this was measured
    against."""

    def test_ranked_note_survives_when_floor_channels_are_saturated(self, tmp_path) -> None:
        from agent.config import (
            RECALL_FLOOR_DETERMINISTIC_MAX_NOTES,
            RECALL_FLOOR_TIER0_MAX_NOTES,
        )

        store = _store(tmp_path)
        ws = "/ws"
        # Saturate Tier 0's absolute cap with directive notes...
        for i in range(RECALL_FLOOR_TIER0_MAX_NOTES + 3):
            store.remember(ws, f"standing rule {i}", kind="directive")
        # ...and the deterministic tag channel's absolute cap...
        for i in range(RECALL_FLOOR_DETERMINISTIC_MAX_NOTES + 3):
            store.remember(ws, f"content {i} unrelated topic", tags=["shared-tag"])
        # ...while a single note only reachable via the ranked (SQL LIKE
        # fallback) path -- no directive kind, no shared tag, and an
        # explicit short title (so the store's own "derive a title from
        # the first content line" fallback can't accidentally trip the
        # title-overlap channel too) -- sits alongside them and must
        # still survive the composition.
        ranked_nid = store.remember(
            ws, "note about shared-tag important-marker topic", title="x",
        )
        notes = store.recall(ws, query="shared-tag important-marker", apply_floor=True, limit=10)
        assert ranked_nid in {n.note_id for n in notes}

    def test_floor_channels_combined_never_reach_default_limit(self, tmp_path) -> None:
        """Symbolic invariant (config values, not a specific note count):
        at recall()'s own default limit=10, the two channels' fraction-
        scaled caps must sum to strictly less than 10, so ranked fill
        always has room left -- this is exactly the relationship whose
        violation (5 + 5 == 10) caused the regression above. Guards
        against a future config edit silently reintroducing it."""
        from agent.config import (
            RECALL_FLOOR_DETERMINISTIC_MAX_FRACTION_OF_LIMIT,
            RECALL_FLOOR_TIER0_MAX_FRACTION_OF_LIMIT,
        )

        default_limit = 10
        tier0_cap = int(default_limit * RECALL_FLOOR_TIER0_MAX_FRACTION_OF_LIMIT)
        det_cap = int(default_limit * RECALL_FLOOR_DETERMINISTIC_MAX_FRACTION_OF_LIMIT)
        assert tier0_cap + det_cap < default_limit


class TestApplyFloorIgnoredWhenKindNarrows:
    """apply_floor is a no-op when the caller explicitly narrows `kind` —
    an explicit kind filter means the caller asked for exactly that kind,
    unmodified, same as apply_floor=False (see recall()'s docstring)."""

    def test_kind_filter_excludes_floor_notes_of_other_kinds(self, tmp_path) -> None:
        store = _store(tmp_path)
        ws = "/ws"
        store.remember(ws, "a standing rule", kind="directive")
        # Plain (no-embedder) store: the SQL LIKE fallback requires `query`
        # to be a literal substring of `content` — "regular finding" here
        # matches the finding note's own content, not a query-classification
        # keyword.
        nid = store.remember(ws, "a regular finding", kind="finding")
        notes = store.recall(ws, query="regular finding", kind="finding", apply_floor=True)
        assert {n.note_id for n in notes} == {nid}


class TestPinServiceLayer:
    """VectrService.pin_note() / remember(pin=...) — the surface behind the
    REST /v1/pin route and the vectr_pin / vectr_remember(pin=...) MCP
    tools."""

    def _service(self, tmp_path):
        from app.service import VectrService
        return VectrService(workspace_root=str(tmp_path), memory_only=True)

    def test_pin_note_toggles(self, tmp_path) -> None:
        svc = self._service(tmp_path)
        note_id = svc.remember("a note to pin")
        assert svc.pin_note(note_id, True) is True
        note = svc.get_note(note_id)
        assert note is not None
        assert note.pinned is True
        assert svc.pin_note(note_id, False) is True
        note = svc.get_note(note_id)
        assert note.pinned is False

    def test_pin_note_missing_id_returns_false(self, tmp_path) -> None:
        svc = self._service(tmp_path)
        assert svc.pin_note(99999, True) is False

    def test_remember_pin_kwarg(self, tmp_path) -> None:
        svc = self._service(tmp_path)
        note_id = svc.remember("pinned at write time", pin=True)
        note = svc.get_note(note_id)
        assert note is not None
        assert note.pinned is True
