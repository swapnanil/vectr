"""Matching-engine tests (UPG-PRO-4). Fakes return the REAL WorkingNote /
SearchResult types the daemon would return."""
from __future__ import annotations

import time

from agent.proactive.matcher import ProactiveMatcher
from agent.proactive.types import ProactiveWindow
from agent.searcher import SearchResult
from agent.working_context_store._types import WorkingNote


def _note(note_id, content, kind="finding", title=""):
    return WorkingNote(
        note_id=note_id, workspace="/ws", content=content, tags=[], priority="medium",
        created_at=time.time(), last_accessed=time.time(), kind=kind, title=title,
    )


class _Source:
    """Deliberately does NOT implement note_states() -- the pre-UPG-PROXY-
    REVOKED-LEAK MatchSource shape. Every test using this fake exercises
    the backward-compat degrade-to-{} path (a missing note_states() call
    means every note is treated as active)."""

    def __init__(self, structural=None, semantic=None, code=None):
        self._structural = structural or []
        self._semantic = semantic or []
        self._code = code or []
        self.calls = []

    def structural_notes(self, file_paths):
        self.calls.append(("structural", tuple(file_paths)))
        return list(self._structural)

    def semantic_notes(self, text, min_similarity, limit):
        self.calls.append(("semantic", text))
        return [(n, s) for (n, s) in self._semantic if s >= min_similarity]

    def code_search(self, text, n_results):
        self.calls.append(("code", text))
        return list(self._code)


class _StatefulSource(_Source):
    """Same as _Source but implements note_states() -- used by revoked/
    superseded lifecycle tests."""

    def __init__(self, structural=None, semantic=None, code=None, states=None):
        super().__init__(structural=structural, semantic=semantic, code=code)
        self._states = states or {}

    def note_states(self, notes):
        self.calls.append(("note_states", tuple(n.note_id for n in notes)))
        return dict(self._states)


def _matcher(source, **kw):
    defaults = dict(min_similarity=0.35, max_chars_per_event=800,
                    structural_note=True, semantic_note=True, code_search=True)
    defaults.update(kw)
    return ProactiveMatcher(source, **defaults)


def test_structural_note_scores_one_and_anchors():
    """UPG-PROXY-SUBSTRING-ANCHOR/2b: this note has no DECLARED anchor --
    it only matched because window file_paths' basename ("resolver.py")
    appears in its content. That is a "mentions" claim, not "anchored
    to" -- see test_structural_note_declared_anchor_says_anchored_to for
    the genuine declared-anchor case."""
    n = _note(1, "gotcha: resolver.py lock drops on scope exit")
    src = _Source(structural=[n])
    w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])
    cands = _matcher(src, semantic_note=False, code_search=False).match(w)
    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "note_structural" and c.score == 1.0 and c.is_structural
    assert "mentions resolver.py" in c.line
    assert c.anchor_id == "note:1"


def test_structural_note_declared_anchor_says_anchored_to():
    """A note whose declared `anchors` column actually contains the
    window file gets the stronger "anchored to X" wording -- even when
    its prose content never spells the filename out."""
    n = _note(12, "the backoff cap here needs tuning")
    n.anchors = [["resolver.py", None]]
    src = _Source(structural=[n])
    w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])
    cands = _matcher(src, semantic_note=False, code_search=False).match(w)
    assert len(cands) == 1
    assert "anchored to resolver.py" in cands[0].line
    assert "mentions" not in cands[0].line


def test_semantic_note_respects_floor():
    src = _Source(semantic=[(_note(2, "workspace lock flow"), 0.8), (_note(3, "off topic"), 0.10)])
    w = ProactiveWindow(text="how does the workspace lock work")
    cands = _matcher(src, structural_note=False, code_search=False).match(w)
    ids = {c.anchor_id for c in cands}
    assert "note:2" in ids
    assert "note:3" not in ids  # below floor, dropped by the source's threshold


def test_code_search_candidates():
    r = SearchResult(file_path="resolver.py", lines="10-20", symbol_name="lock",
                     language="python", score=0.72, content="def lock():\n    ...")
    src = _Source(code=[r])
    w = ProactiveWindow(text="lock acquisition")
    cands = _matcher(src, structural_note=False, semantic_note=False).match(w)
    assert len(cands) == 1
    assert cands[0].kind == "code_semantic"
    assert cands[0].anchor_id == "chunk:resolver.py:10-20"
    assert cands[0].score == 0.72


def test_all_matchers_run_unconditionally():
    n = _note(1, "resolver.py note")
    r = SearchResult(file_path="a.py", lines="1-2", symbol_name="", language="python",
                     score=0.6, content="code")
    src = _Source(structural=[n], semantic=[(_note(2, "sem"), 0.9)], code=[r])
    w = ProactiveWindow(text="lock", file_paths=["/x/resolver.py"], symbols=[])
    cands = _matcher(src).match(w)
    kinds = {c.kind for c in cands}
    assert kinds == {"note_structural", "note_semantic", "code_semantic"}
    # All three matchers were consulted regardless of window content.
    call_kinds = {c[0] for c in src.calls}
    assert call_kinds == {"structural", "semantic", "code"}


def test_disabled_matcher_not_called():
    src = _Source(semantic=[(_note(2, "sem"), 0.9)])
    w = ProactiveWindow(text="lock", file_paths=["/x/resolver.py"])
    _matcher(src, structural_note=False, code_search=False).match(w)
    # Only the semantic matcher ran; disabling is a static toggle, not a content read.
    assert {c[0] for c in src.calls} == {"semantic"}


def test_source_error_is_swallowed():
    class _Boom:
        def structural_notes(self, fp):
            raise RuntimeError("db down")

        def semantic_notes(self, t, m, l):
            raise RuntimeError("embed down")

        def code_search(self, t, n):
            raise RuntimeError("index down")

    w = ProactiveWindow(text="lock", file_paths=["/x/a.py"])
    cands = _matcher(_Boom()).match(w)
    assert cands == []  # a failing source degrades to no candidates, never raises


def test_source_without_note_states_degrades_gracefully_to_active():
    """A MatchSource that predates note_states() (an older fake or backend)
    must not break -- a missing note_states() call is treated as every
    note being active, matching note_event_states()'s own contract for a
    note_id absent from the fold."""
    n = _note(10, "some finding")
    src = _Source(structural=[n])  # _Source deliberately has no note_states
    w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])
    cands = _matcher(src, semantic_note=False, code_search=False).match(w)
    assert len(cands) == 1
    assert "REVOKED" not in cands[0].line


def test_note_states_called_once_for_the_union_of_matched_notes():
    """note_states() must be called exactly once per match(), for the
    UNION of every note either the structural or semantic matcher found
    -- never once per note, never once per matcher."""
    shared = _note(20, "shared note matched by both matchers")
    structural_only = _note(21, "structural only")
    src = _StatefulSource(
        structural=[shared, structural_only],
        semantic=[(shared, 0.9)],
    )
    w = ProactiveWindow(text="shared note", file_paths=["/abs/resolver.py"], symbols=[])
    _matcher(src).match(w)
    note_states_calls = [c for c in src.calls if c[0] == "note_states"]
    assert len(note_states_calls) == 1
    assert set(note_states_calls[0][1]) == {20, 21}


class TestRevokedNoteDeterrentRendering:
    """UPG-PROXY-REVOKED-LEAK: a revoked note must never inject its raw
    content as active fact -- it still injects (catching an agent about
    to re-assert a wrong belief is the highest-value injection), just
    rendered through the anti-memory deterrent instead."""

    def test_structural_revoked_note_renders_deterrent_first(self):
        n = _note(30, "the retry timeout is 30 seconds", kind="gotcha")
        w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])

        # Non-vacuity: the SAME note, while still active, surfaces its raw
        # content verbatim -- proving the match itself is real, not just
        # an artifact of the revoked-state rendering path.
        active_cands = _matcher(
            _Source(structural=[n]), semantic_note=False, code_search=False
        ).match(w)
        assert len(active_cands) == 1
        assert "the retry timeout is 30 seconds" in active_cands[0].line

        states = {30: {"state": "revoked", "reason": "wrong assumption",
                       "actor": "agent", "ts": time.time()}}
        revoked_cands = _matcher(
            _StatefulSource(structural=[n], states=states),
            semantic_note=False, code_search=False,
        ).match(w)
        assert len(revoked_cands) == 1
        line = revoked_cands[0].line
        assert "REVOKED" in line
        assert "Do not re-derive" in line
        # The deterrent clause is placed FIRST so _cap() truncation can
        # never remove it while leaving the raw claim still legible.
        assert line.index("Do not re-derive") < line.index("Previously believed")

    def test_semantic_revoked_note_renders_deterrent_first(self):
        n = _note(31, "cache eviction runs every 5 minutes", kind="gotcha")
        w = ProactiveWindow(text="how does cache eviction work")

        active_cands = _matcher(
            _Source(semantic=[(n, 0.9)]), structural_note=False, code_search=False
        ).match(w)
        assert len(active_cands) == 1
        assert "cache eviction runs every 5 minutes" in active_cands[0].line

        states = {31: {"state": "revoked", "reason": "outdated",
                       "actor": "agent", "ts": time.time()}}
        revoked_cands = _matcher(
            _StatefulSource(semantic=[(n, 0.9)], states=states),
            structural_note=False, code_search=False,
        ).match(w)
        assert len(revoked_cands) == 1
        line = revoked_cands[0].line
        assert "REVOKED" in line
        assert line.index("Do not re-derive") < line.index("Previously believed")

    def test_truncation_preserves_deterrent_over_raw_quote(self):
        """A tight per-event char budget must cut the tail-positioned raw
        quoted claim before it ever cuts the front-positioned deterrent
        warning."""
        n = _note(32, "a very specific raw claim about default timeout values", kind="gotcha")
        w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])
        states = {32: {"state": "revoked", "reason": "wrong",
                       "actor": "agent", "ts": time.time()}}

        # Uncapped line, to locate exactly where the deterrent clause ends.
        uncapped = _matcher(
            _StatefulSource(structural=[n], states=states),
            semantic_note=False, code_search=False, max_chars_per_event=10_000,
        ).match(w)
        full_line = uncapped[0].line
        deterrent_end = full_line.index("without verification.") + len("without verification.")
        assert full_line.index("Do not re-derive") < full_line.index(
            '"a very specific raw claim'
        )

        # A budget that fits the deterrent clause but not the raw quote.
        tight = _matcher(
            _StatefulSource(structural=[n], states=states),
            semantic_note=False, code_search=False, max_chars_per_event=deterrent_end + 5,
        ).match(w)
        tight_line = tight[0].line
        assert "Do not re-derive this from other sources without verification." in tight_line
        assert "a very specific raw claim about default timeout values" not in tight_line

    def test_superseded_note_reaching_renderer_is_skipped(self):
        """Superseded notes are normally excluded via valid_until before
        ever reaching the matcher; this is a defensive check that if one
        still reaches here, it is skipped rather than rendered as an
        active fact."""
        n = _note(33, "old cache size was 128mb")
        w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])
        states = {33: {"state": "superseded", "reason": None,
                       "actor": "agent", "ts": time.time()}}
        cands = _matcher(
            _StatefulSource(structural=[n], states=states),
            semantic_note=False, code_search=False,
        ).match(w)
        assert cands == []
