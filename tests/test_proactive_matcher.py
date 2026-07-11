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


def _matcher(source, **kw):
    defaults = dict(min_similarity=0.35, max_chars_per_event=800,
                    structural_note=True, semantic_note=True, code_search=True)
    defaults.update(kw)
    return ProactiveMatcher(source, **defaults)


def test_structural_note_scores_one_and_anchors():
    n = _note(1, "gotcha: resolver.py lock drops on scope exit")
    src = _Source(structural=[n])
    w = ProactiveWindow(text="", file_paths=["/abs/resolver.py"], symbols=[])
    cands = _matcher(src, semantic_note=False, code_search=False).match(w)
    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "note_structural" and c.score == 1.0 and c.is_structural
    assert "anchored to resolver.py" in c.line
    assert c.anchor_id == "note:1"


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
