"""Tests for agent/episode_canon.py (memoization-l1-capture-design §2.3).

Pure digest canonicalization — no config dependency, caps passed in by the
caller (mirroring the actual EPISODES_DIGEST_* config values in
agent/config.yaml, but tested standalone here since the module itself takes
no config import)."""
from __future__ import annotations

from agent.episode_canon import canonicalize_digest


class TestUnderCapText:
    def test_short_text_passes_through_unchanged(self):
        # Line-based reassembly (join by "\n") does not preserve a single
        # trailing newline — content is unchanged, only the trailing
        # newline itself is not round-tripped.
        assert canonicalize_digest("all good\n", 200, 20, 20) == "all good"

    def test_empty_text(self):
        assert canonicalize_digest("", 200, 20, 20) == ""


class TestRepeatedLineCollapse:
    def test_consecutive_duplicates_collapse(self):
        text = "\n".join(["line"] * 10)
        out = canonicalize_digest(text, 2000, 20, 20)
        assert "line [x10]" in out
        assert out.count("line\n") == 0  # no longer 10 separate "line" lines

    def test_non_consecutive_duplicates_not_collapsed(self):
        text = "\n".join(["a", "b", "a", "b"])
        out = canonicalize_digest(text, 2000, 20, 20)
        assert "[x" not in out  # no run of length > 1 anywhere


class TestOverCapElision:
    def test_over_cap_keeps_head_and_tail(self):
        lines = [f"line {i}" for i in range(200)]
        text = "\n".join(lines)
        out = canonicalize_digest(text, max_chars=300, head_lines=3, tail_lines=3)
        assert "line 0" in out
        assert "line 199" in out
        assert "elided" in out

    def test_elision_marker_is_hash_tagged(self):
        lines = [f"line {i}" for i in range(200)]
        text = "\n".join(lines)
        out = canonicalize_digest(text, max_chars=300, head_lines=3, tail_lines=3)
        assert "sha256:" in out

    def test_over_cap_result_respects_char_backstop(self):
        # A single pathologically long line with no newlines at all — the
        # head/tail line-based elision can't help; the char-level backstop
        # must still bound the result.
        text = "x" * 100000
        out = canonicalize_digest(text, max_chars=500, head_lines=5, tail_lines=5)
        assert len(out) <= 600  # generous slack for the elision marker text


class TestDeterminism:
    def test_same_input_same_output(self):
        text = "\n".join(["a"] * 5 + [f"line {i}" for i in range(50)] + ["b"] * 5)
        out1 = canonicalize_digest(text, 200, 5, 5)
        out2 = canonicalize_digest(text, 200, 5, 5)
        assert out1 == out2
