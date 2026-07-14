"""Unit tests for agent.trigger_engine — the per-memory trigger engine core
(TRIGGER-ENGINE wave 1, bm2-design-skeleton.md §1/§2/§3/§5).

Covers: trigger validation, kind-default bundles, deterministic evaluation
(composition/conjunction/OR/T-modifiers/tombstone), the one shared total
order, the per-session fire ledger, provenance framing, and the two-tier
injection budget/pack. No filesystem, no SQLite, no embedder — pure logic
only (WorkingContextStore.fire()/check_staleness() integration is covered in
tests/test_memory.py)."""
from __future__ import annotations

import time

import pytest

from agent.trigger_engine import (
    FULL_TEXT_KINDS,
    MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP,
    FireResult,
    PackedItem,
    TriggerFireLedger,
    default_bundle_for_kind,
    evaluate_note,
    frame_prefix,
    pack_injection,
    scope_permits,
    token_estimate,
    total_order_key,
    validate_trigger,
    validate_triggers,
)
from agent.working_context_store._types import WorkingNote


def _note(
    note_id: int = 1,
    kind: str = "finding",
    priority: str = "medium",
    triggers: list[dict] | None = None,
    anchors: list[list[str]] | None = None,
    provenance: str = "agent",
    valid_until: float | None = None,
    last_fired: float | None = None,
    last_accessed: float = 100.0,
    scope: str = "workspace",
    session_id: str | None = None,
    branch: str = "",
) -> WorkingNote:
    return WorkingNote(
        note_id=note_id,
        workspace="/tmp/ws",
        content="some finding",
        tags=[],
        priority=priority,
        created_at=100.0,
        last_accessed=last_accessed,
        kind=kind,
        triggers=triggers or [],
        anchors=anchors or [],
        provenance=provenance,
        valid_until=valid_until,
        last_fired=last_fired,
        scope=scope,
        session_id=session_id,
        branch=branch,
    )


# ---------------------------------------------------------------------------
# validate_trigger / validate_triggers
# ---------------------------------------------------------------------------

class TestValidateTrigger:
    def test_path_only_is_valid(self) -> None:
        validate_trigger({"path": "src/api/**"})

    def test_event_only_is_valid(self) -> None:
        validate_trigger({"event": "pre-edit"})

    def test_path_and_event_is_valid(self) -> None:
        validate_trigger({"path": "src/api/**", "event": "pre-edit"})

    def test_t_modifiers_alone_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="path.*or.*event|never fires alone"):
            validate_trigger({"not_before": 100.0})

    def test_symbol_only_is_valid(self) -> None:
        validate_trigger({"symbol": "WorkspaceLock"})

    def test_symbol_and_path_is_valid(self) -> None:
        validate_trigger({"symbol": "WorkspaceLock", "path": "src/api/**"})

    def test_symbol_and_event_is_valid(self) -> None:
        validate_trigger({"symbol": "WorkspaceLock", "event": "pre-edit"})

    def test_non_string_symbol_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_trigger({"symbol": 123})

    def test_empty_string_symbol_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_trigger({"symbol": ""})

    def test_semantic_only_is_valid(self) -> None:
        validate_trigger({"semantic": True})

    def test_semantic_and_event_is_valid(self) -> None:
        validate_trigger({"semantic": True, "event": "prompt-submit"})

    def test_non_bool_semantic_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_trigger({"semantic": "yes"})

    def test_non_dict_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_trigger("not a dict")  # type: ignore[arg-type]

    def test_non_string_path_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_trigger({"path": 123})

    def test_unknown_event_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_trigger({"event": "not-a-real-event"})

    @pytest.mark.parametrize("key", ["not_before", "expires_visibility", "cooldown"])
    def test_non_numeric_t_modifier_rejected(self, key: str) -> None:
        with pytest.raises(ValueError):
            validate_trigger({"event": "pre-edit", key: "soon"})

    def test_numeric_t_modifiers_accepted(self) -> None:
        validate_trigger({
            "event": "pre-edit", "not_before": 1.0, "expires_visibility": 2.0, "cooldown": 3600,
        })


class TestValidateTriggers:
    def test_none_returns_empty_list(self) -> None:
        assert validate_triggers(None) == []

    def test_empty_list_returns_empty_list(self) -> None:
        assert validate_triggers([]) == []

    def test_valid_list_returned_unchanged(self) -> None:
        triggers = [{"event": "session-start"}, {"path": "a/b.py"}]
        assert validate_triggers(triggers) == triggers

    def test_one_malformed_entry_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_triggers([{"event": "session-start"}, {"not_before": 1.0}])


# ---------------------------------------------------------------------------
# Kind-default bundles
# ---------------------------------------------------------------------------

class TestDefaultBundleForKind:
    def test_directive_fires_session_start_and_post_compaction(self) -> None:
        bundle = default_bundle_for_kind("directive", None)
        events = {t["event"] for t in bundle}
        assert events == {"session-start", "post-compaction"}

    def test_task_fires_session_start_at_high_priority(self) -> None:
        bundle = default_bundle_for_kind("task", None, priority="high")
        assert bundle == [{"event": "session-start"}]

    @pytest.mark.parametrize("priority", ["medium", "low", None])
    def test_task_gets_no_default_bundle_below_high_priority(self, priority) -> None:
        """TRIGGER-ENGINE wave 2a fix: the legacy `boot_recall()` this bundle
        replaces filters task notes with `AND priority = 'high'` in SQL — a
        medium/low/unset-priority task note must get NO default session-start
        trigger, or `fire()` would inject strictly MORE task notes than
        boot_recall() ever did (an unintended widening, not the documented
        "reproduces ... exactly")."""
        assert default_bundle_for_kind("task", None, priority=priority) == []

    def test_gotcha_with_anchors_gets_one_pre_edit_trigger_per_anchor(self) -> None:
        anchors = [["src/api/x.py", "abc123"], ["src/api/y.py", None]]
        bundle = default_bundle_for_kind("gotcha", anchors)
        assert bundle == [
            {"path": "src/api/x.py", "event": "pre-edit"},
            {"path": "src/api/y.py", "event": "pre-edit"},
        ]

    def test_gotcha_without_anchors_gets_empty_bundle(self) -> None:
        assert default_bundle_for_kind("gotcha", None) == []
        assert default_bundle_for_kind("gotcha", []) == []

    @pytest.mark.parametrize("kind", ["finding", "reference"])
    def test_finding_and_reference_get_no_default_bundle(self, kind: str) -> None:
        assert default_bundle_for_kind(kind, None) == []


# ---------------------------------------------------------------------------
# evaluate_note — the composition/conjunction/OR/T-modifier/tombstone core
# ---------------------------------------------------------------------------

class TestEvaluateNote:
    def test_tombstoned_note_never_fires(self) -> None:
        note = _note(kind="directive", valid_until=500.0)
        result = evaluate_note(note, event="session-start")
        assert result.fired is False
        assert "superseded" in result.explanation

    def test_no_triggers_and_no_default_bundle_never_fires(self) -> None:
        note = _note(kind="finding")  # no explicit triggers, no default bundle
        result = evaluate_note(note, event="session-start", file_path="a.py")
        assert result.fired is False

    def test_directive_default_bundle_fires_on_session_start(self) -> None:
        note = _note(kind="directive")
        result = evaluate_note(note, event="session-start")
        assert result.fired is True
        assert result.trigger_index == 0
        assert "fired: trigger 1" in result.explanation

    def test_directive_default_bundle_fires_on_post_compaction(self) -> None:
        note = _note(kind="directive")
        result = evaluate_note(note, event="post-compaction")
        assert result.fired is True
        assert result.trigger_index == 1
        assert "fired: trigger 2" in result.explanation

    def test_directive_does_not_fire_on_unrelated_event(self) -> None:
        note = _note(kind="directive")
        result = evaluate_note(note, event="pre-edit", file_path="a.py")
        assert result.fired is False

    def test_path_only_trigger_requires_glob_match(self) -> None:
        note = _note(triggers=[{"path": "src/api/**"}])
        assert evaluate_note(note, file_path="src/api/handlers.py").fired is True
        assert evaluate_note(note, file_path="src/other/x.py").fired is False

    def test_conjunction_requires_both_path_and_event(self) -> None:
        note = _note(triggers=[{"path": "src/api/**", "event": "pre-edit"}])
        # path matches, event doesn't
        assert evaluate_note(note, event="pre-run", file_path="src/api/x.py").fired is False
        # event matches, path doesn't
        assert evaluate_note(note, event="pre-edit", file_path="src/other/x.py").fired is False
        # both match
        result = evaluate_note(note, event="pre-edit", file_path="src/api/x.py")
        assert result.fired is True
        assert "path src/api/** at pre-edit" in result.explanation

    def test_symbol_only_trigger_matches_when_symbol_is_resolved(self) -> None:
        note = _note(triggers=[{"symbol": "WorkspaceLock"}])
        result = evaluate_note(
            note, file_path="src/api/resolver.py",
            resolved_symbols=frozenset({"WorkspaceLock", "acquire_lock"}),
        )
        assert result.fired is True
        assert "symbol WorkspaceLock" in result.explanation

    def test_symbol_only_trigger_does_not_match_an_unresolved_symbol(self) -> None:
        note = _note(triggers=[{"symbol": "WorkspaceLock"}])
        result = evaluate_note(
            note, file_path="src/api/resolver.py",
            resolved_symbols=frozenset({"some_other_symbol"}),
        )
        assert result.fired is False

    def test_symbol_trigger_never_fires_when_resolved_symbols_is_none(self) -> None:
        """Degradation (bm2-design-skeleton.md §2): a memory-only daemon or a
        warm-up window before the symbol graph is built has no resolved
        symbols at all — a symbol trigger deterministically does not fire,
        never an error."""
        note = _note(triggers=[{"symbol": "WorkspaceLock"}])
        result = evaluate_note(note, file_path="src/api/resolver.py", resolved_symbols=None)
        assert result.fired is False

    def test_symbol_and_path_conjunction_requires_both(self) -> None:
        note = _note(triggers=[{"symbol": "WorkspaceLock", "path": "src/api/**"}])
        both = frozenset({"WorkspaceLock"})
        # symbol resolves, path doesn't
        assert evaluate_note(note, file_path="src/other/x.py", resolved_symbols=both).fired is False
        # path matches, symbol doesn't resolve
        assert evaluate_note(note, file_path="src/api/x.py", resolved_symbols=frozenset()).fired is False
        # both match
        result = evaluate_note(note, file_path="src/api/x.py", resolved_symbols=both)
        assert result.fired is True
        assert "path src/api/** + symbol WorkspaceLock" in result.explanation

    def test_semantic_only_trigger_matches_when_precomputed_true(self) -> None:
        note = _note(triggers=[{"semantic": True}])
        result = evaluate_note(note, event="prompt-submit", semantic_matched=True)
        assert result.fired is True
        assert "semantic" in result.explanation

    def test_semantic_only_trigger_does_not_match_when_precomputed_false(self) -> None:
        note = _note(triggers=[{"semantic": True}])
        result = evaluate_note(note, event="prompt-submit", semantic_matched=False)
        assert result.fired is False

    def test_semantic_trigger_never_fires_when_semantic_matched_is_none(self) -> None:
        """Degradation: no embedder attached, embedder still warming up, or
        the caller never computed a cosine for this note — a semantic
        trigger deterministically does not fire, never an error."""
        note = _note(triggers=[{"semantic": True}])
        result = evaluate_note(note, event="prompt-submit", semantic_matched=None)
        assert result.fired is False

    def test_semantic_and_event_conjunction_requires_both(self) -> None:
        note = _note(triggers=[{"semantic": True, "event": "prompt-submit"}])
        # semantic matches, event doesn't
        assert evaluate_note(note, event="pre-edit", semantic_matched=True).fired is False
        # event matches, semantic doesn't
        assert evaluate_note(note, event="prompt-submit", semantic_matched=False).fired is False
        # both match
        result = evaluate_note(note, event="prompt-submit", semantic_matched=True)
        assert result.fired is True
        assert "semantic at prompt-submit" in result.explanation

    def test_or_composition_first_match_wins(self) -> None:
        note = _note(triggers=[{"event": "pre-run"}, {"event": "pre-edit"}])
        result = evaluate_note(note, event="pre-edit", file_path="a.py")
        assert result.fired is True
        assert result.trigger_index == 1
        assert "fired: trigger 2" in result.explanation

    def test_explicit_triggers_replace_not_merge_with_kind_default(self) -> None:
        # kind='directive' would default to session-start/post-compaction, but
        # an explicit triggers[] fully replaces that default bundle.
        note = _note(kind="directive", triggers=[{"event": "pre-run"}])
        assert evaluate_note(note, event="session-start").fired is False
        assert evaluate_note(note, event="pre-run").fired is True

    def test_not_before_withholds_until_the_given_time(self) -> None:
        note = _note(triggers=[{"event": "pre-edit", "not_before": 1000.0}])
        assert evaluate_note(note, event="pre-edit", now=500.0).fired is False
        assert evaluate_note(note, event="pre-edit", now=1500.0).fired is True

    def test_cooldown_withholds_until_elapsed_since_last_fired(self) -> None:
        note = _note(triggers=[{"event": "pre-edit", "cooldown": 3600}], last_fired=1000.0)
        assert evaluate_note(note, event="pre-edit", now=1500.0).fired is False  # 500s < 3600s
        assert evaluate_note(note, event="pre-edit", now=5000.0).fired is True   # 4000s >= 3600s

    def test_cooldown_with_no_prior_fire_does_not_withhold(self) -> None:
        note = _note(triggers=[{"event": "pre-edit", "cooldown": 3600}], last_fired=None)
        assert evaluate_note(note, event="pre-edit", now=1.0).fired is True

    def test_expires_visibility_never_blocks_but_marks_faded(self) -> None:
        note = _note(triggers=[{"event": "pre-edit", "expires_visibility": 1000.0}])
        before = evaluate_note(note, event="pre-edit", now=500.0)
        assert before.fired is True
        assert before.faded is False
        after = evaluate_note(note, event="pre-edit", now=1500.0)
        assert after.fired is True
        assert after.faded is True

    def test_evaluation_is_deterministic_across_repeated_calls(self) -> None:
        note = _note(kind="directive")
        r1 = evaluate_note(note, event="session-start", now=42.0)
        r2 = evaluate_note(note, event="session-start", now=42.0)
        assert r1 == r2

    def test_out_of_scope_note_never_fires_even_with_a_matching_trigger(self) -> None:
        """Scope is checked BEFORE the trigger loop (bm2-design-skeleton.md §1)
        — a trigger that would otherwise match must never fire a note whose
        scope excludes this lifecycle context."""
        note = _note(kind="directive", scope="session", session_id="writer-session")
        result = evaluate_note(note, event="session-start", session_id="other-session")
        assert result.fired is False
        assert "scope" in result.explanation


class TestEvaluateNotePathCandidates:
    """`file_path` accepts either a single string (legacy/common case) or a
    tuple of equivalent candidate forms for the SAME file — the P primitive
    matches a trigger's `path` glob against ANY candidate
    (`WorkingContextStore.fire()`'s real caller passes (as_given,
    workspace_relative); this pure-logic test exercises evaluate_note()'s own
    handling of that tuple without any filesystem/workspace involved)."""

    def test_single_string_file_path_still_works(self) -> None:
        note = _note(triggers=[{"path": "src/api/**"}])
        assert evaluate_note(note, file_path="src/api/handlers.py").fired is True

    def test_pattern_matches_second_candidate_in_the_tuple(self) -> None:
        """Simulates the exact bug this fixes: a relatively-anchored pattern
        against an absolute-looking first candidate that only the SECOND
        (workspace-relative) candidate satisfies."""
        note = _note(triggers=[{"path": "src/api/**", "event": "pre-edit"}])
        result = evaluate_note(
            note, event="pre-edit",
            file_path=("/abs/workspace/src/api/handlers.py", "src/api/handlers.py"),
        )
        assert result.fired is True

    def test_no_candidate_matches_never_fires(self) -> None:
        note = _note(triggers=[{"path": "src/api/**", "event": "pre-edit"}])
        result = evaluate_note(
            note, event="pre-edit",
            file_path=("/abs/workspace/src/other/x.py", "src/other/x.py"),
        )
        assert result.fired is False

    def test_empty_tuple_never_fires_a_path_trigger(self) -> None:
        note = _note(triggers=[{"path": "src/api/**"}])
        assert evaluate_note(note, file_path=()).fired is False

    def test_scope_path_subtree_uses_first_candidate_as_primary(self) -> None:
        """Non-P uses of the path (scope's path-subtree check here) use the
        FIRST candidate — the path exactly as the caller gave it."""
        note = _note(
            kind="gotcha", scope="path-subtree", anchors=[["src/api/x.py", None]],
            triggers=[{"path": "src/api/**"}],
        )
        result = evaluate_note(note, file_path=("src/api/x.py", "src/api/x.py"))
        assert result.fired is True


# ---------------------------------------------------------------------------
# scope_permits — the five SCOPE_VALUES (bm2-design-skeleton.md §1)
# ---------------------------------------------------------------------------

class TestScopePermits:
    def test_workspace_scope_is_always_permitted(self) -> None:
        note = _note(scope="workspace")
        assert scope_permits(note)[0] is True
        assert scope_permits(note, session_id="s1", branch="main", file_path="a.py")[0] is True

    def test_repo_scope_is_a_no_op_like_workspace(self) -> None:
        """UPG-TRIGGER-SCOPE-REPO-CROSSSTORE: "repo" is recorded faithfully at
        write time but enforced identically to "workspace" until the store is
        keyed by git-common-dir instead of by workspace path."""
        note = _note(scope="repo")
        assert scope_permits(note)[0] is True

    def test_notes_written_before_this_wave_default_to_workspace_scope(self) -> None:
        """Backward compat: the WorkingNote dataclass default (and every note
        written before scope existed) is scope="workspace" — unaffected by
        enforcement, never newly excluded."""
        note = WorkingNote(
            note_id=99, workspace="/tmp/ws", content="pre-wave note", tags=[],
            priority="medium", created_at=1.0, last_accessed=1.0,
        )
        assert note.scope == "workspace"
        assert scope_permits(note)[0] is True

    def test_session_scope_permits_only_the_writing_session(self) -> None:
        note = _note(scope="session", session_id="writer-session")
        assert scope_permits(note, session_id="writer-session")[0] is True
        assert scope_permits(note, session_id="other-session")[0] is False
        assert scope_permits(note)[0] is False  # no session_id supplied at all

    def test_branch_scope_permits_only_the_recorded_branch(self) -> None:
        note = _note(scope="branch", branch="feature/x")
        assert scope_permits(note, branch="feature/x")[0] is True
        assert scope_permits(note, branch="main")[0] is False
        assert scope_permits(note)[0] is False  # no branch supplied at all

    def test_path_subtree_scope_permits_files_under_a_declared_anchor(self) -> None:
        note = _note(scope="path-subtree", anchors=[["src/api/x.py", None]])
        assert scope_permits(note, file_path="src/api/x.py")[0] is True
        assert scope_permits(note, file_path="src/api/y.py")[0] is True   # same directory
        assert scope_permits(note, file_path="src/other/z.py")[0] is False
        assert scope_permits(note)[0] is False  # no file_path supplied at all

    def test_path_subtree_scope_with_no_anchors_is_never_permitted(self) -> None:
        note = _note(scope="path-subtree", anchors=[])
        assert scope_permits(note, file_path="src/api/x.py")[0] is False

    def test_unrecognised_scope_value_never_blocks(self) -> None:
        note = _note(scope="bogus-future-value")
        assert scope_permits(note)[0] is True


# ---------------------------------------------------------------------------
# total_order_key — one implementation, reused for fire/injection/eviction
# ---------------------------------------------------------------------------

class TestTotalOrderKey:
    def test_kind_priority_directive_before_gotcha_before_task_before_finding_before_reference(self) -> None:
        notes = [
            _note(note_id=1, kind="reference"),
            _note(note_id=2, kind="finding"),
            _note(note_id=3, kind="task"),
            _note(note_id=4, kind="gotcha"),
            _note(note_id=5, kind="directive"),
        ]
        ordered = sorted(notes, key=total_order_key)
        assert [n.kind for n in ordered] == ["directive", "gotcha", "task", "finding", "reference"]

    def test_priority_breaks_ties_within_same_kind(self) -> None:
        notes = [
            _note(note_id=1, kind="finding", priority="low"),
            _note(note_id=2, kind="finding", priority="high"),
            _note(note_id=3, kind="finding", priority="medium"),
        ]
        ordered = sorted(notes, key=total_order_key)
        assert [n.priority for n in ordered] == ["high", "medium", "low"]

    def test_more_recently_used_note_sorts_first_among_ties(self) -> None:
        older = _note(note_id=1, kind="finding", priority="medium", last_accessed=100.0)
        newer = _note(note_id=2, kind="finding", priority="medium", last_accessed=200.0)
        ordered = sorted([older, newer], key=total_order_key)
        assert [n.note_id for n in ordered] == [2, 1]

    def test_last_fired_preferred_over_last_accessed(self) -> None:
        # last_accessed says note 1 is more recent, but last_fired (set only by
        # this engine's own fires) says note 2 fired more recently — last_fired wins.
        n1 = _note(note_id=1, kind="finding", last_accessed=500.0, last_fired=None)
        n2 = _note(note_id=2, kind="finding", last_accessed=100.0, last_fired=999.0)
        ordered = sorted([n1, n2], key=total_order_key)
        assert [n.note_id for n in ordered] == [2, 1]

    def test_note_id_is_the_final_deterministic_tiebreak(self) -> None:
        a = _note(note_id=5, kind="finding", priority="medium", last_accessed=100.0)
        b = _note(note_id=2, kind="finding", priority="medium", last_accessed=100.0)
        ordered = sorted([a, b], key=total_order_key)
        assert [n.note_id for n in ordered] == [2, 5]

    def test_unrecognised_kind_or_priority_sorts_last_not_error(self) -> None:
        weird = _note(note_id=1, kind="not-a-real-kind", priority="not-a-real-priority")
        normal = _note(note_id=2, kind="finding", priority="medium")
        ordered = sorted([weird, normal], key=total_order_key)
        assert [n.note_id for n in ordered] == [2, 1]

    def test_task_kind_orders_newest_note_id_first_even_on_shared_last_fired(self) -> None:
        """UPG-TASK-NOTE-INJECTION-RECENCY: fire() stamps every note that
        fires together in one call with the SAME last_fired reading (one
        shared clock per evaluation), which ties last_used between two task
        notes the moment both have fired once. A task note is current-work
        state — the newer checkpoint (higher note_id) must still outrank the
        older one after that tie, unlike the generic ascending tie-break
        every other kind uses (test_note_id_is_the_final_deterministic_tiebreak
        above)."""
        older = _note(note_id=1, kind="task", priority="high", last_fired=500.0, last_accessed=500.0)
        newer = _note(note_id=2, kind="task", priority="high", last_fired=500.0, last_accessed=500.0)
        ordered = sorted([older, newer], key=total_order_key)
        assert [n.note_id for n in ordered] == [2, 1]

    def test_task_kind_ignores_last_used_entirely_older_last_fired_still_loses(self) -> None:
        """Even when the OLDER task note has a strictly greater last_fired/
        last_accessed than the newer one (e.g. it was re-fired in a later,
        separate session-start call after the newer note's own last fire),
        note_id recency still wins — task ordering never depends on
        last_used, exactly mirroring recall()'s SQL ordering for this kind."""
        older_but_refired = _note(note_id=1, kind="task", priority="high", last_fired=999.0)
        newer = _note(note_id=2, kind="task", priority="high", last_fired=100.0)
        ordered = sorted([older_but_refired, newer], key=total_order_key)
        assert [n.note_id for n in ordered] == [2, 1]


# ---------------------------------------------------------------------------
# TriggerFireLedger — per-session dedup
# ---------------------------------------------------------------------------

class TestTriggerFireLedger:
    def test_fresh_ledger_is_eligible_for_anything(self) -> None:
        ledger = TriggerFireLedger()
        assert ledger.eligible(note_id=1, trigger_index=0) is True

    def test_recording_a_fire_suppresses_the_same_axis(self) -> None:
        ledger = TriggerFireLedger()
        ledger.record_fire(note_id=1, trigger_index=0)
        assert ledger.eligible(note_id=1, trigger_index=0) is False

    def test_a_different_trigger_index_on_the_same_note_is_still_eligible(self) -> None:
        ledger = TriggerFireLedger()
        ledger.record_fire(note_id=1, trigger_index=0)
        assert ledger.eligible(note_id=1, trigger_index=1) is True

    def test_a_different_note_is_unaffected(self) -> None:
        ledger = TriggerFireLedger()
        ledger.record_fire(note_id=1, trigger_index=0)
        assert ledger.eligible(note_id=2, trigger_index=0) is True

    def test_reset_clears_all_suppression(self) -> None:
        ledger = TriggerFireLedger()
        ledger.record_fire(note_id=1, trigger_index=0)
        ledger.reset()
        assert ledger.eligible(note_id=1, trigger_index=0) is True

    def test_fresh_ledger_has_the_full_per_session_budget(self) -> None:
        ledger = TriggerFireLedger()
        assert ledger.remaining_budget() == MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP

    def test_spend_is_cumulative_across_multiple_record_spend_calls(self) -> None:
        """§3: the per-session token cap bounds the TOTAL tokens injected across
        every fire_and_format call in one session, not each call in isolation —
        a second delivery must see less budget than the first."""
        ledger = TriggerFireLedger()
        ledger.record_spend(100)
        ledger.record_spend(50)
        assert ledger.remaining_budget() == MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP - 150

    def test_spend_never_drives_remaining_budget_negative(self) -> None:
        ledger = TriggerFireLedger()
        ledger.record_spend(MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP + 5_000)
        assert ledger.remaining_budget() == 0

    def test_reset_also_zeroes_cumulative_spend(self) -> None:
        """Compaction (§3 "cleared on compaction") must restore the full budget,
        not just fire-dedup eligibility — a stale spend total would leave a
        post-compaction session permanently under-budget."""
        ledger = TriggerFireLedger()
        ledger.record_spend(MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP)
        assert ledger.remaining_budget() == 0
        ledger.reset()
        assert ledger.remaining_budget() == MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP


# ---------------------------------------------------------------------------
# Provenance framing
# ---------------------------------------------------------------------------

class TestFramePrefix:
    def test_human_directive_gets_imperative_framing(self) -> None:
        text = frame_prefix("human", "directive")
        assert "DIRECTIVE" in text
        assert "follow it" in text

    def test_human_non_directive_gets_recorded_by_user_framing(self) -> None:
        text = frame_prefix("human", "finding")
        assert "DIRECTIVE" not in text
        assert "Recorded by the user" in text

    @pytest.mark.parametrize("kind", ["directive", "task", "gotcha", "finding", "reference"])
    def test_agent_gets_verify_framing_regardless_of_kind(self, kind: str) -> None:
        text = frame_prefix("agent", kind)
        assert "verify" in text.lower()

    @pytest.mark.parametrize("kind", ["directive", "task", "gotcha", "finding", "reference"])
    def test_auto_gets_weakest_framing_regardless_of_kind(self, kind: str) -> None:
        text = frame_prefix("auto", kind)
        assert "weakest" in text.lower() or "no reviewing judgment" in text.lower()

    def test_unrecognised_provenance_falls_back_to_agent_framing(self) -> None:
        assert frame_prefix("bogus", "finding") == frame_prefix("agent", "finding")

    def test_never_raises_on_any_combination(self) -> None:
        for provenance in ("human", "agent", "auto", ""):
            for kind in ("directive", "task", "gotcha", "finding", "reference", ""):
                frame_prefix(provenance, kind)  # must not raise


# ---------------------------------------------------------------------------
# token_estimate + pack_injection — the two-tier budget
# ---------------------------------------------------------------------------

class TestTokenEstimate:
    def test_empty_string_still_estimates_at_least_one_token(self) -> None:
        assert token_estimate("") >= 1

    def test_estimate_scales_with_length(self) -> None:
        assert token_estimate("x" * 400) > token_estimate("x" * 40)


class TestPackInjection:
    def test_full_text_kinds_prefer_full_text(self) -> None:
        for kind in FULL_TEXT_KINDS:
            note = _note(note_id=1, kind=kind)
            packed = pack_injection([(note, "FULL BODY", "index one-liner")])
            assert packed[0].tier == "full"
            assert packed[0].text == "FULL BODY"

    def test_non_full_text_kinds_get_index_tier(self) -> None:
        note = _note(note_id=1, kind="finding")
        packed = pack_injection([(note, "FULL BODY", "index one-liner")])
        assert packed[0].tier == "index"
        assert packed[0].text == "index one-liner"

    def test_output_is_ordered_by_total_order_key(self) -> None:
        directive = _note(note_id=1, kind="directive")
        finding = _note(note_id=2, kind="finding")
        packed = pack_injection([
            (finding, "full finding", "idx finding"),
            (directive, "full directive", "idx directive"),
        ])
        assert [p.note_id for p in packed] == [1, 2]

    def test_a_memory_is_never_partially_truncated(self) -> None:
        note = _note(note_id=1, kind="directive")
        full_text = "X" * 100
        index_text = "short"
        packed = pack_injection([(note, full_text, index_text)])
        assert packed[0].text in (full_text, index_text)
        assert len(packed[0].text) not in range(1, len(full_text))  # never a partial slice of full_text
        assert packed[0].text != full_text[: len(full_text) // 2]

    def test_full_text_over_per_injection_cap_downgrades_to_index(self) -> None:
        from agent.config import MEMORY_TRIGGER_PER_INJECTION_TOKEN_CAP, MEMORY_TRIGGER_CHARS_PER_TOKEN
        note = _note(note_id=1, kind="directive")
        oversized_full = "X" * (MEMORY_TRIGGER_PER_INJECTION_TOKEN_CAP * MEMORY_TRIGGER_CHARS_PER_TOKEN * 2)
        packed = pack_injection([(note, oversized_full, "short index line")])
        assert packed[0].tier == "index"
        assert packed[0].text == "short index line"

    def test_per_session_budget_evicts_from_the_bottom_of_total_order(self) -> None:
        from agent.config import MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP, MEMORY_TRIGGER_CHARS_PER_TOKEN
        # The higher-precedence note alone consumes the entire per-session
        # budget (at whichever tier it lands on); a second, lower-precedence
        # note must then be evicted entirely rather than the first note being
        # truncated to make room.
        huge_text = "Y" * (MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP * MEMORY_TRIGGER_CHARS_PER_TOKEN)
        high = _note(note_id=1, kind="directive")
        low = _note(note_id=2, kind="reference")
        packed = pack_injection([
            (low, "full low", "idx low"),
            (high, huge_text, huge_text),
        ])
        note_ids = [p.note_id for p in packed]
        assert 1 in note_ids  # the higher-precedence note is kept
        assert 2 not in note_ids  # the lower-precedence note is evicted, not truncated

    def test_note_that_fits_at_neither_tier_is_evicted_entirely(self) -> None:
        from agent.config import MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP, MEMORY_TRIGGER_CHARS_PER_TOKEN
        note = _note(note_id=1, kind="finding")
        oversized_index = "Z" * (MEMORY_TRIGGER_PER_SESSION_TOKEN_CAP * MEMORY_TRIGGER_CHARS_PER_TOKEN * 2)
        packed = pack_injection([(note, "full text", oversized_index)])
        assert packed == []

    def test_empty_items_returns_empty_list(self) -> None:
        assert pack_injection([]) == []
