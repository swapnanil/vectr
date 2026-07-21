"""Tests for app/cmdnorm.py — deterministic command normalization
(L1 capture design doc §3.1, LANE-ARC).

Table-driven: tokenization, semantics-neutral decoration stripping, verb
extraction (incl. the absorption cap), and positional-argument abstraction
(<PATH>/<VERSION>/<UUID>/<NUM>) for every class and edge case.
"""
from __future__ import annotations

import pytest

from app.cmdnorm import classify_arg, normalize_command, tokenize


class TestTokenize:
    def test_simple_split(self) -> None:
        assert tokenize("ls -la /tmp") == ["ls", "-la", "/tmp"]

    def test_quote_aware_pipe_inside_string(self) -> None:
        # A literal `|` inside a quoted string must never be treated as a
        # pipeline separator by the caller — shlex handles that for us.
        assert tokenize('grep "foo|bar" file.txt') == ["grep", "foo|bar", "file.txt"]

    def test_empty_string(self) -> None:
        assert tokenize("") == []

    def test_unbalanced_quotes_falls_back_to_whitespace_split(self) -> None:
        # Must never raise on a malformed command — a real episode we still
        # need to process, not crash on.
        assert tokenize('echo "unterminated') == ["echo", '"unterminated']


class TestClassifyArg:
    @pytest.mark.parametrize(
        "token,expected",
        [
            ("550e8400-e29b-41d4-a716-446655440000", "<UUID>"),
            ("v1.2.3", "<VERSION>"),
            ("2.7.18", "<VERSION>"),
            ("1.0.0-beta1", "<VERSION>"),
            ("/usr/local/bin", "<PATH>"),
            ("core/camel-core", "<PATH>"),
            (".", "<PATH>"),
            ("..", "<PATH>"),
            ("~/.config", "<PATH>"),
            ("file.txt", "<PATH>"),
            ("*.java", "<PATH>"),
            ("42", "<NUM>"),
            ("-1", "<NUM>"),
            ("3.14", "<NUM>"),
            ("Foo", "Foo"),
            ("--verbose", "--verbose"),
        ],
    )
    def test_classification(self, token: str, expected: str) -> None:
        assert classify_arg(token) == expected

    def test_uuid_beats_version_and_num(self) -> None:
        # A UUID also matches nothing else, but this documents the intended
        # precedence order (uuid > version > path > num > literal).
        assert classify_arg("550e8400-e29b-41d4-a716-446655440000") == "<UUID>"


class TestNormalizeCommandVerb:
    def test_simple_verb(self) -> None:
        assert normalize_command("cat file.txt").verb == "cat"

    def test_two_token_verb_git_commit(self) -> None:
        assert normalize_command('git commit -m "fix bug"').verb == "git commit"

    def test_two_token_verb_relative_binary(self) -> None:
        assert normalize_command("./mvnw test -Dtest=Foo").verb == "./mvnw test"

    def test_three_token_verb_npm_run(self) -> None:
        # All three tokens are bareword (unclassified, not flag-shaped), so
        # absorption continues up to the configured cap (3).
        assert normalize_command("npm run build").verb == "npm run build"

    def test_verb_cap_bounds_runaway_absorption(self) -> None:
        # Every token here is a bareword (unclassified, not flag-shaped), so
        # absorption would otherwise continue indefinitely; the cap
        # (max_verb_tokens=3) stops it after 3 tokens, leaving the rest as
        # genuine positional arguments.
        n = normalize_command("make foo bar baz qux")
        assert n.verb == "make foo bar"
        assert n.args == ("baz", "qux")

    def test_flag_stops_verb_absorption(self) -> None:
        n = normalize_command("mvn test -pl core/camel-core -Dtest=Foo")
        assert n.verb == "mvn test"
        assert "-pl" in n.flags
        assert "-Dtest=Foo" in n.flags


class TestNormalizeCommandDecoration:
    def test_leading_cd_stripped(self) -> None:
        a = normalize_command("cd core/camel-core && mvn test -Dtest=Foo")
        b = normalize_command("mvn test -Dtest=Foo")
        assert a.verb == b.verb
        assert a.flags == b.flags

    def test_repeated_leading_cd_stripped(self) -> None:
        a = normalize_command("cd a && cd b && npm run build")
        assert a.verb == "npm run build"

    def test_trailing_stderr_merge_stripped(self) -> None:
        a = normalize_command("mvn test -q 2>&1 | tail -30")
        b = normalize_command("mvn test -q")
        assert a.verb == b.verb
        assert a.flags == b.flags
        assert a.args == b.args

    def test_trailing_pipe_to_cat_stripped(self) -> None:
        a = normalize_command("mvn test | cat")
        assert a.verb == "mvn test"
        assert a.args == ()

    def test_env_var_prefix_captured_not_left_in_verb(self) -> None:
        n = normalize_command("NODE_ENV=production npm run build")
        assert n.verb == "npm run build"
        assert n.env_prefix_names == ("NODE_ENV",)

    def test_multiple_env_var_prefixes(self) -> None:
        n = normalize_command("FOO=1 BAR=2 make build")
        assert n.env_prefix_names == ("FOO", "BAR")
        assert n.verb == "make build"

    def test_last_segment_of_compound_command_is_normalized(self) -> None:
        # The episode's own rc/outcome reflects the LAST command in a
        # compound chain, so that is what defines the normalized command.
        n = normalize_command("make clean && make build")
        assert n.verb == "make build"


class TestNormalizeCommandPipelineCollapse:
    """Reviewer finding F2 (2026-07-22): only a TRAILING run of
    display-only stages (cat/tail/head) may be dropped from a pipeline; a
    genuine multi-stage pipeline must keep every non-trailing stage's
    tokens in the comparison set."""

    def test_multistage_pipeline_downstream_tokens_kept_and_differentiate(self) -> None:
        a = normalize_command("cat data.csv | python train.py")
        b = normalize_command("cat data.csv | python eval.py")
        assert a != b
        assert a.args != b.args
        assert "train.py" in a.args
        assert "eval.py" in b.args

    def test_multistage_pipeline_matching_downstream_stage_is_identical(self) -> None:
        a = normalize_command("cat data.csv | python train.py")
        b = normalize_command("cat data.csv | python train.py")
        assert a == b

    def test_trailing_display_only_chain_all_dropped(self) -> None:
        a = normalize_command("mvn test | tail -5 | cat")
        b = normalize_command("mvn test")
        assert a.verb == b.verb
        assert a.args == b.args

    def test_non_trailing_display_verb_not_dropped(self) -> None:
        # `cat` is only display-only when it is the LAST stage; as the
        # first (primary) stage of a real pipeline it is the actual verb.
        n = normalize_command("cat data.csv | python train.py")
        assert n.verb == "cat"


class TestNormalizeCommandWrapperPrefixes:
    """Reviewer finding F3 (2026-07-22): transparent wrapper prefixes are
    stripped iteratively before verb extraction so the wrapped command,
    not the wrapper, is normalized."""

    def test_timeout_duration_value_stripped_before_verb(self) -> None:
        a = normalize_command("timeout 60 curl https://api.example.com/x")
        b = normalize_command("timeout 90 curl https://api.example.com/x")
        assert (a.verb, a.flags, a.args) == (b.verb, b.flags, b.args)
        assert a.verb == "curl"
        assert a.args == ("https://api.example.com/x",)

    def test_env_wrapper_and_bare_assignment_both_captured(self) -> None:
        n = normalize_command("env FOO=bar npm test")
        assert n.verb == "npm test"
        assert n.env_prefix_names == ("FOO",)

    def test_nice_with_niceness_flag_stripped(self) -> None:
        n = normalize_command("nice -n 10 make build")
        assert n.verb == "make build"

    def test_bare_nice_stripped(self) -> None:
        n = normalize_command("nice make build")
        assert n.verb == "make build"

    def test_nohup_stripped(self) -> None:
        n = normalize_command("nohup python script.py")
        assert n.verb == "python"
        assert n.args == ("script.py",)

    def test_stdbuf_dash_flags_stripped(self) -> None:
        n = normalize_command("stdbuf -oL python script.py")
        assert n.verb == "python"
        assert n.args == ("script.py",)

    def test_xargs_is_never_stripped(self) -> None:
        # xargs's argument is a command TEMPLATE, not the command that
        # actually ran — stripping it would misattribute the invocation.
        n = normalize_command("xargs -I{} rm {}")
        assert n.verb == "xargs"


class TestNormalizeCommandEmptyVerb:
    """Reviewer finding F4 (2026-07-22): a command that normalizes to an
    empty verb carries no comparable structure."""

    def test_bare_stderr_merge_token_is_empty_verb(self) -> None:
        n = normalize_command("2>&1")
        assert n.verb == ""

    def test_env_assignment_only_is_empty_verb(self) -> None:
        n = normalize_command("FOO=bar")
        assert n.verb == ""
        assert n.env_prefix_names == ("FOO",)


class TestNormalizeCommandArgs:
    def test_path_arg_classified(self) -> None:
        n = normalize_command("ls -la /tmp")
        assert n.args == ("/tmp",)
        assert n.arg_classes == ("<PATH>",)

    def test_flags_sorted_for_order_invariance(self) -> None:
        a = normalize_command("ls -l -a")
        b = normalize_command("ls -a -l")
        assert a.flags == b.flags

    def test_glob_pattern_arg_classified_as_path(self) -> None:
        # `-type f` is not a `flag=value` token, so "f" surfaces as its own
        # trailing positional arg (a known imprecision of a normalizer
        # that has no per-tool knowledge of which flags take a separate
        # value — acceptable since it affects comparison, not correctness
        # of the concrete command stored alongside).
        n = normalize_command('find . -name "*.java" -type f')
        assert n.arg_classes == ("<PATH>", "<PATH>", "f")

    def test_empty_command(self) -> None:
        n = normalize_command("")
        assert n.verb == ""
        assert n.flags == ()
        assert n.args == ()

    def test_cmd_raw_preserved(self) -> None:
        n = normalize_command("git status")
        assert n.cmd_raw == "git status"
