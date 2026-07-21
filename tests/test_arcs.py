"""Tests for app/arcs.py — deterministic streaming failure->success arc
detection (L1 capture design doc §3, LANE-ARC).

Table-driven: similarity scoring (§3.2), the pending-window state machine
(§3.3: chains, interleaving, TTL aging), edit-mediated vs. flaky
disambiguation (§3.4), and every false-positive trap (§3.5).

Every episode here models a TOOL-CALL record (argv structure / exit
outcome of an already-executed Bash/Edit call) — never prompt/query
content — matching the R5 scope of app/arcs.py itself.
"""
from __future__ import annotations

from typing import Any

from app.arcs import ArcDetector, is_identical_command, is_mutation_band, similarity
from app.cmdnorm import normalize_command as nc


def make_episode(
    session_id: str = "s1",
    ts: float = 0.0,
    cwd: str = "/repo",
    tool: str = "bash",
    cmd_raw: str = "",
    outcome: str | None = None,
    termination: str | None = None,
    markers: list[str] | None = None,
    env_delta_names: list[str] | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    return dict(
        session_id=session_id,
        ts=ts,
        cwd=cwd,
        tool=tool,
        cmd_raw=cmd_raw,
        outcome=outcome,
        termination=termination,
        markers=markers or [],
        env_delta_names=env_delta_names or [],
        file_path=file_path,
    )


# ---------------------------------------------------------------------------
# Similarity (§3.2)
# ---------------------------------------------------------------------------


class TestSimilarity:
    def test_identical_command_scores_one(self) -> None:
        a = nc("git status")
        b = nc("git status")
        assert similarity(a, b) == 1.0
        assert is_identical_command(a, b)
        assert not is_mutation_band(similarity(a, b)), "identical (1.0) is never a mutation"

    def test_flag_value_change_is_mutation(self) -> None:
        a = nc("mvn test -Dtest=Foo")
        b = nc("mvn test -Dtest=Bar")
        score = similarity(a, b)
        assert is_mutation_band(score)
        assert not is_identical_command(a, b)

    def test_verb_typo_soft_match(self) -> None:
        # A single-character verb typo still passes the Levenshtein soft-
        # match ratio and contributes partial (not full) verb score.
        a = nc("gt status")
        b = nc("git status")
        score = similarity(a, b)
        assert score > 0.0
        assert score < 1.0

    def test_unrelated_commands_score_below_band(self) -> None:
        a = nc("git status")
        b = nc("ls -la /tmp")
        assert similarity(a, b) < 0.55
        assert not is_mutation_band(similarity(a, b))

    def test_unrelated_install_targets_not_a_mutation(self) -> None:
        # Different, unrelated package targets under the same tool/verb
        # must NOT look like a mutation of each other.
        a = nc("pip install flask")
        b = nc("pip install django")
        assert not is_mutation_band(similarity(a, b))

    def test_path_swap_same_arity_uses_positional_levenshtein(self) -> None:
        a = nc("cat src/foo.py")
        b = nc("cat src/bar.py")
        score = similarity(a, b)
        assert is_mutation_band(score)

    def test_flag_jaccard_partial_overlap(self) -> None:
        a = nc("ls -l -a")
        b = nc("ls -l -h")
        score = similarity(a, b)
        # verb=1.0*0.5=0.5, flags jaccard({-a,-l} vs {-h,-l})=1/3*0.3=0.1,
        # args both empty -> 1.0*0.2=0.2; total=0.8
        assert abs(score - 0.8) < 1e-9

    def test_different_arity_args_falls_back_to_class_jaccard(self) -> None:
        a = nc("cp src/a.py src/b.py")
        b = nc("cp src/a.py")
        score = similarity(a, b)
        # arity differs (2 vs 1) -> jaccard over arg_classes, both all
        # <PATH> -> jaccard = 1.0 despite different arities.
        assert is_mutation_band(score)


# ---------------------------------------------------------------------------
# Detector state machine — basic mutation arcs (§3.3)
# ---------------------------------------------------------------------------


class TestBasicMutationArc:
    def test_single_failure_then_mutated_success_emits_arc(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="mvn test -Dtest=Foo", outcome="failure"))
        arcs = d.observe(make_episode(ts=1, cmd_raw="mvn test -Dtest=Bar", outcome="success"))
        assert len(arcs) == 1
        arc = arcs[0]
        assert arc.mutation_diff == {"flag": (("-Dtest=Foo",), ("-Dtest=Bar",))}
        assert arc.confidence == "normal"
        assert len(arc.failures_chain) == 1

    def test_matched_failure_is_consumed_from_pending(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="mvn test -Dtest=Foo", outcome="failure"))
        d.observe(make_episode(ts=1, cmd_raw="mvn test -Dtest=Bar", outcome="success"))
        state = d._sessions["s1"]
        assert state.pending == {}

    def test_success_with_no_pending_failure_emits_nothing(self) -> None:
        d = ArcDetector()
        arcs = d.observe(make_episode(ts=0, cmd_raw="git status", outcome="success"))
        assert arcs == []

    def test_unrelated_failure_left_unresolved_by_unrelated_success(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pip install flask", outcome="failure"))
        arcs = d.observe(make_episode(ts=1, cmd_raw="pip install django", outcome="success"))
        assert arcs == []
        state = d._sessions["s1"]
        assert len(state.pending["pip"]) == 1


# ---------------------------------------------------------------------------
# Chains and interleaving (§3.3)
# ---------------------------------------------------------------------------


class TestChainsAndInterleaving:
    def test_chain_backward_through_pending_failures(self) -> None:
        # install -> missing dep -> pin version -> succeeds (§3.3 example).
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pip install requests", outcome="failure"))
        d.observe(make_episode(ts=1, cmd_raw="pip install requests==2.28.0", outcome="failure"))
        arcs = d.observe(make_episode(ts=2, cmd_raw="pip install requests==2.28.1", outcome="success"))
        assert len(arcs) == 1
        arc = arcs[0]
        assert [f["cmd_raw"] for f in arc.failures_chain] == [
            "pip install requests",
            "pip install requests==2.28.0",
        ]

    def test_interleaved_unrelated_failure_is_skipped_not_a_stop_condition(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pip install requests", outcome="failure"))
        d.observe(
            make_episode(
                ts=1,
                cmd_raw="pip install completely-different-package-xyz-not-related-at-all",
                outcome="failure",
            )
        )
        d.observe(make_episode(ts=2, cmd_raw="pip install requests==2.28.0", outcome="failure"))
        arcs = d.observe(make_episode(ts=3, cmd_raw="pip install requests==2.28.1", outcome="success"))
        assert len(arcs) == 1
        chain_cmds = [f["cmd_raw"] for f in arcs[0].failures_chain]
        assert chain_cmds == ["pip install requests", "pip install requests==2.28.0"]
        assert "pip install completely-different-package-xyz-not-related-at-all" not in chain_cmds

    def test_interleaved_unrelated_failure_remains_pending_afterward(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pip install requests", outcome="failure"))
        d.observe(
            make_episode(
                ts=1,
                cmd_raw="pip install completely-different-package-xyz-not-related-at-all",
                outcome="failure",
            )
        )
        d.observe(make_episode(ts=2, cmd_raw="pip install requests==2.28.0", outcome="failure"))
        d.observe(make_episode(ts=3, cmd_raw="pip install requests==2.28.1", outcome="success"))
        state = d._sessions["s1"]
        remaining = [p.episode["cmd_raw"] for p in state.pending.get("pip", [])]
        assert remaining == ["pip install completely-different-package-xyz-not-related-at-all"]

    def test_interleaved_different_family_success_is_ignored(self) -> None:
        # A totally different tool's success in between must not touch or
        # consume the still-pending failure.
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="mvn test -Dtest=Foo", outcome="failure"))
        d.observe(make_episode(ts=1, cmd_raw="git status", outcome="success"))
        arcs = d.observe(make_episode(ts=2, cmd_raw="mvn test -Dtest=Bar", outcome="success"))
        assert len(arcs) == 1

    def test_tie_break_prefers_most_recent_equally_similar_failure(self) -> None:
        # Two pending failures score identically against the success; the
        # more recent one must be picked as the anchor so the chain-walk
        # still reaches back through the older one, rather than stranding
        # it unconsumed while an equally-scored but older failure "wins".
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="git checkout feature-a", outcome="failure"))
        d.observe(make_episode(ts=1, cmd_raw="git checkout feature-b", outcome="failure"))
        arcs = d.observe(make_episode(ts=2, cmd_raw="git checkout feature-c", outcome="success"))
        assert len(arcs) == 1
        state = d._sessions["s1"]
        assert state.pending == {}


# ---------------------------------------------------------------------------
# TTL / command-count aging (§3.3)
# ---------------------------------------------------------------------------


class TestAging:
    def test_pending_failure_ages_out_by_ttl(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="failure"))
        arcs = d.observe(make_episode(ts=700, cmd_raw="pytest tests/test_foo.py", outcome="success"))
        assert arcs == []

    def test_pending_failure_survives_within_ttl(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="failure"))
        arcs = d.observe(make_episode(ts=599, cmd_raw="pytest tests/test_foo.py -v", outcome="success"))
        assert len(arcs) == 1

    def test_pending_failure_ages_out_by_command_count(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="failure"))
        for i in range(1, 22):
            d.observe(make_episode(ts=float(i), cmd_raw=f"echo noop{i}", outcome="success"))
        arcs = d.observe(make_episode(ts=25.0, cmd_raw="pytest tests/test_foo.py", outcome="success"))
        assert arcs == []

    def test_pending_failure_survives_within_command_count(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="failure"))
        for i in range(1, 19):
            d.observe(make_episode(ts=float(i), cmd_raw=f"echo noop{i}", outcome="success"))
        arcs = d.observe(make_episode(ts=20.0, cmd_raw="pytest tests/test_foo.py -v", outcome="success"))
        assert len(arcs) == 1


# ---------------------------------------------------------------------------
# Edit-mediated vs. flaky retry (§3.4)
# ---------------------------------------------------------------------------


class TestEditMediatedVsFlaky:
    def test_intervening_edit_produces_edit_mediated_arc(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="failure"))
        d.observe(make_episode(ts=1, tool="edit", file_path="app/foo.py"))
        arcs = d.observe(make_episode(ts=2, cmd_raw="pytest tests/test_foo.py", outcome="success"))
        assert len(arcs) == 1
        assert arcs[0].mutation_diff == {"files": ((), ("app/foo.py",))}

    def test_multiple_intervening_edits_collected(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="failure"))
        d.observe(make_episode(ts=1, tool="edit", file_path="app/foo.py"))
        d.observe(make_episode(ts=2, tool="edit", file_path="app/bar.py"))
        arcs = d.observe(make_episode(ts=3, cmd_raw="pytest tests/test_foo.py", outcome="success"))
        assert len(arcs) == 1
        assert arcs[0].mutation_diff["files"] == ((), ("app/bar.py", "app/foo.py"))

    def test_identical_retry_no_edit_no_delta_is_flaky_suppressed(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="curl https://api.example.com/health", outcome="failure"))
        arcs = d.observe(make_episode(ts=1, cmd_raw="curl https://api.example.com/health", outcome="success"))
        assert arcs == []
        state = d._sessions["s1"]
        sig = ("curl", frozenset(), ("https://api.example.com/health",))
        assert state.flake_counts[sig] == 1
        assert state.pending == {}

    def test_identical_retry_with_cwd_delta_is_not_flaky(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cwd="/repo/a", cmd_raw="make build", outcome="failure"))
        arcs = d.observe(make_episode(ts=1, cwd="/repo/b", cmd_raw="make build", outcome="success"))
        assert len(arcs) == 1
        assert arcs[0].mutation_diff == {"cwd": ("/repo/a", "/repo/b")}

    def test_identical_retry_with_env_delta_is_not_flaky(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="make build", outcome="failure", env_delta_names=[]))
        arcs = d.observe(
            make_episode(ts=1, cmd_raw="make build", outcome="success", env_delta_names=["CC"])
        )
        assert len(arcs) == 1
        assert arcs[0].mutation_diff == {"env": ((), ("CC",))}

    def test_near_threshold_match_suppressed_once_proven_flaky(self) -> None:
        d = ArcDetector()
        cmd = "curl https://api.example.com/health"
        # Two identical flaky retries prove the command flaky.
        d.observe(make_episode(ts=0, cmd_raw=cmd, outcome="failure"))
        d.observe(make_episode(ts=1, cmd_raw=cmd, outcome="success"))
        d.observe(make_episode(ts=2, cmd_raw=cmd, outcome="failure"))
        d.observe(make_episode(ts=3, cmd_raw=cmd, outcome="success"))
        state = d._sessions["s1"]
        sig = ("curl", frozenset(), ("https://api.example.com/health",))
        assert state.flake_counts[sig] == 2

        # A near-identical (trailing slash) success now arrives after
        # another identical fail — must be suppressed as flaky noise, not
        # emitted as a real mutation arc.
        d.observe(make_episode(ts=4, cmd_raw=cmd, outcome="failure"))
        arcs = d.observe(make_episode(ts=5, cmd_raw=cmd + "/", outcome="success"))
        assert arcs == []
        assert state.pending == {}

    def test_near_threshold_match_not_suppressed_before_flaky_is_proven(self) -> None:
        d = ArcDetector()
        cmd = "curl https://api.example.com/health"
        # Only ONE flaky retry so far — below suppress_min_count (2).
        d.observe(make_episode(ts=0, cmd_raw=cmd, outcome="failure"))
        d.observe(make_episode(ts=1, cmd_raw=cmd, outcome="success"))
        d.observe(make_episode(ts=2, cmd_raw=cmd, outcome="failure"))
        arcs = d.observe(make_episode(ts=3, cmd_raw=cmd + "/", outcome="success"))
        assert len(arcs) == 1
        assert arcs[0].mutation_diff == {
            "arg": (
                ("https://api.example.com/health",),
                ("https://api.example.com/health/",),
            )
        }


# ---------------------------------------------------------------------------
# False-positive traps (§3.5)
# ---------------------------------------------------------------------------


class TestTraps:
    def test_transient_marker_tags_low_confidence(self) -> None:
        d = ArcDetector()
        d.observe(
            make_episode(
                ts=0,
                cmd_raw="curl https://api.example.com/data",
                outcome="failure",
                markers=["generic.timeout"],
            )
        )
        arcs = d.observe(
            make_episode(ts=1, cmd_raw="curl https://api.example.com/data --retry 3", outcome="success")
        )
        assert len(arcs) == 1
        assert arcs[0].confidence == "low"

    def test_no_transient_marker_is_normal_confidence(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="mvn test -Dtest=Foo", outcome="failure", markers=["maven.build_failure"]))
        arcs = d.observe(make_episode(ts=1, cmd_raw="mvn test -Dtest=Bar", outcome="success"))
        assert len(arcs) == 1
        assert arcs[0].confidence == "normal"

    def test_interrupted_never_enters_pending(self) -> None:
        d = ArcDetector()
        d.observe(
            make_episode(
                ts=0, cmd_raw="pytest tests/test_foo.py", outcome="interrupted", termination="interrupted"
            )
        )
        state = d._sessions["s1"]
        assert state.pending == {}
        arcs = d.observe(make_episode(ts=1, cmd_raw="pytest tests/test_foo.py", outcome="success"))
        assert arcs == []

    def test_unknown_outcome_never_enters_pending(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="pytest tests/test_foo.py", outcome="unknown"))
        state = d._sessions["s1"]
        assert state.pending == {}

    def test_soft_failure_enters_pending_like_failure(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(ts=0, cmd_raw="mvn test -Dtest=Foo", outcome="soft_failure"))
        arcs = d.observe(make_episode(ts=1, cmd_raw="mvn test -Dtest=Bar", outcome="success"))
        assert len(arcs) == 1


# ---------------------------------------------------------------------------
# Episode/session plumbing
# ---------------------------------------------------------------------------


class TestEpisodePlumbing:
    def test_missing_session_id_ignored(self) -> None:
        d = ArcDetector()
        arcs = d.observe(make_episode(session_id="", ts=0, cmd_raw="git status", outcome="failure"))
        assert arcs == []

    def test_non_bash_non_edit_tool_ignored(self) -> None:
        d = ArcDetector()
        arcs = d.observe(make_episode(ts=0, tool="read", cmd_raw="git status", outcome="failure"))
        assert arcs == []
        assert d._sessions == {}

    def test_sessions_are_isolated(self) -> None:
        d = ArcDetector()
        d.observe(make_episode(session_id="s1", ts=0, cmd_raw="mvn test -Dtest=Foo", outcome="failure"))
        arcs = d.observe(make_episode(session_id="s2", ts=1, cmd_raw="mvn test -Dtest=Bar", outcome="success"))
        assert arcs == [], "a different session must never resolve another session's pending failure"

    def test_edit_episode_never_emits_an_arc_itself(self) -> None:
        d = ArcDetector()
        arcs = d.observe(make_episode(ts=0, tool="edit", file_path="app/foo.py"))
        assert arcs == []
