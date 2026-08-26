"""Tests for app/cmdnorm.py — deterministic command normalization
(L1 capture design doc §3.1, LANE-ARC).

Table-driven: tokenization, semantics-neutral decoration stripping, verb
extraction (incl. the absorption cap), and positional-argument abstraction
(<PATH>/<VERSION>/<UUID>/<NUM>) for every class and edge case.
"""
from __future__ import annotations

import pytest

from app.cmdnorm import (
    CD_ABSOLUTE,
    CD_NONE,
    CD_RELATIVE,
    CD_SYMBOLIC_HOME,
    CD_UNRESOLVABLE,
    classify_arg,
    leading_cd_resolution,
    leading_cd_target,
    normalize_command,
    tokenize,
)


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


class TestGluedCompoundSeparators:
    """UPG-CMDNORM-GLUED-SEPARATOR: a bare `;`/`&&`/`||` glued to an
    adjacent token (`cd /path;cmd`) must split exactly like its
    whitespace-separated form — in the shell that RAN the command, an
    unquoted separator is a control operator regardless of spacing — while
    quoted text is never touched. Every assertion here fails with the
    pre-fix tokenizer, where shlex yielded `cd /repo-a;make` as ONE token
    and no compound split (hence no cd-strip, hence no effective-cwd
    extraction) ever happened."""

    def test_glued_semicolon_splits(self) -> None:
        assert tokenize("cd /repo-a;make build") == ["cd", "/repo-a", ";", "make", "build"]

    def test_glued_double_ampersand_splits(self) -> None:
        assert tokenize("make clean&&make build") == ["make", "clean", "&&", "make", "build"]

    def test_glued_double_pipe_splits(self) -> None:
        assert tokenize("make build||echo failed") == ["make", "build", "||", "echo", "failed"]

    def test_spaced_separators_are_unchanged(self) -> None:
        # Regression guard: the padding pass must be a no-op on the common
        # already-spaced shapes.
        assert tokenize("cd /repo-a && make build") == ["cd", "/repo-a", "&&", "make", "build"]
        assert tokenize("cd /repo-a ; make build") == ["cd", "/repo-a", ";", "make", "build"]

    def test_leading_cd_target_extracted_from_glued_semicolon(self) -> None:
        # The headline consumer: UPG-ARC-CWD-VS-EFFECTIVE-DIR's
        # leading_cd_target must now see through the glued form. With the
        # bug present this returns None (the whole `cd /repo-a;make`
        # prefix stays glued into one un-splittable segment).
        assert leading_cd_target("cd /repo-a;make build") == "/repo-a"

    def test_normalize_strips_cd_before_glued_separator(self) -> None:
        # `_strip_leading_cd` shares the same tokenizer, so the long-standing
        # verb normalization gains the glued forms too.
        a = normalize_command("cd /repo-a&&mvn test -Dtest=Foo")
        b = normalize_command("mvn test -Dtest=Foo")
        assert a.verb == b.verb
        assert a.flags == b.flags

    def test_quoted_semicolon_never_split(self) -> None:
        assert tokenize('echo "a;b"') == ["echo", "a;b"]

    def test_quoted_double_ampersand_never_split(self) -> None:
        assert tokenize('grep "p&&q" f') == ["grep", "p&&q", "f"]

    def test_single_quoted_semicolon_never_split(self) -> None:
        assert tokenize("echo 'a;b'") == ["echo", "a;b"]

    def test_escaped_semicolon_outside_quotes_not_padded(self) -> None:
        # `\;` is a literal semicolon to the shell (find -exec tails); the
        # padding pass copies the escape pair verbatim so shlex still sees
        # one argument.
        assert tokenize("echo a\\;b") == ["echo", "a;b"]


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


class TestLeadingCdTarget:
    """UPG-ARC-CWD-VS-EFFECTIVE-DIR: `leading_cd_target` extracts the
    directory a leading `cd` chain sets, for `app.arcs._bucket_key` to use
    as the EFFECTIVE cwd instead of trusting the episode's raw `cwd`
    field. Covers the shell shapes this fix explicitly does and does not
    handle (see the function's own docstring for the full rationale)."""

    def test_and_separator(self) -> None:
        assert leading_cd_target("cd /repo-a && make build") == "/repo-a"

    def test_semicolon_separator(self) -> None:
        # Both the spaced and the GLUED form (`;` without whitespace) must
        # work — the glued form is UPG-CMDNORM-GLUED-SEPARATOR's fix, now
        # covered in depth by TestGluedCompoundSeparators above.
        assert leading_cd_target("cd /repo-a ; make build") == "/repo-a"
        assert leading_cd_target("cd /repo-a;make build") == "/repo-a"

    def test_quoted_path(self) -> None:
        assert leading_cd_target('cd "/path with spaces" && make build') == "/path with spaces"

    def test_repeated_leading_cd_uses_last_target(self) -> None:
        assert leading_cd_target("cd a && cd b && npm run build") == "b"

    def test_relative_path_returned_unresolved(self) -> None:
        assert leading_cd_target("cd ../other-repo && make build") == "../other-repo"

    def test_no_leading_cd_returns_none(self) -> None:
        assert leading_cd_target("make build") is None

    def test_bare_cd_yields_symbolic_home(self) -> None:
        # CHANGED by the UPG-ARC-CWD-VS-EFFECTIVE-DIR refinement (was
        # `is None` + "caller falls back to the episode's own cwd field"):
        # a bare `cd` goes HOME, so falling back to the cwd field keyed a
        # command that ran in $HOME as if it ran in the harness cwd —
        # exactly the field-vs-effective divergence this item exists to
        # kill. The tilde-form returned here is a symbolic, per-machine-
        # constant key (see leading_cd_resolution's CD_SYMBOLIC_HOME).
        assert leading_cd_target("cd && make build") == "~"

    def test_flagged_cd_not_recognized(self) -> None:
        # `cd -- <path>` is a 3-token leading segment; `_strip_leading_cd`
        # already only recognizes bare `cd`/`cd <path>` (<= 2 tokens), and
        # this function mirrors that exactly rather than diverging on it.
        assert leading_cd_target("cd -- /repo-a && make build") is None

    def test_non_leading_cd_not_recognized(self) -> None:
        # Only a LEADING cd is unambiguous prefix decoration; a `cd`
        # appearing after another command in the chain is deliberately
        # out of scope (see docstring).
        assert leading_cd_target("mkdir -p /repo-a && cd /repo-a && make build") is None

    def test_pushd_not_recognized(self) -> None:
        assert leading_cd_target("pushd /repo-a && make build") is None


class TestLeadingCdResolution:
    """UPG-ARC-CWD-VS-EFFECTIVE-DIR refinement: `leading_cd_resolution`
    classifies a leading cd chain so `app.arcs` can COMPOSE relative
    targets with the episode cwd field, key tilde/$HOME forms symbolically,
    and REFUSE TO PAIR on shapes whose effective directory is not
    statically derivable — instead of the shipped behavior of returning a
    raw textual token (or None) and letting every caller fall back."""

    def test_no_cd_is_none(self) -> None:
        assert leading_cd_resolution("mvn test") == (CD_NONE, None)

    def test_absolute_target(self) -> None:
        assert leading_cd_resolution("cd /repo-a && make build") == (CD_ABSOLUTE, "/repo-a")

    def test_relative_target(self) -> None:
        assert leading_cd_resolution("cd sub/dir && make build") == (CD_RELATIVE, "sub/dir")

    def test_bare_cd_is_symbolic_home(self) -> None:
        assert leading_cd_resolution("cd && make build") == (CD_SYMBOLIC_HOME, "~")
        assert leading_cd_resolution("cd ~ && make build") == (CD_SYMBOLIC_HOME, "~")

    def test_tilde_targets_are_symbolic_home(self) -> None:
        assert leading_cd_resolution("cd ~/repo && make") == (CD_SYMBOLIC_HOME, "~/repo")
        assert leading_cd_resolution("cd ~other/repo && make") == (CD_SYMBOLIC_HOME, "~other/repo")

    def test_oldpwd_dash_is_unresolvable(self) -> None:
        # $OLDPWD differs per call site — no truthful bucket key exists.
        assert leading_cd_resolution("cd - && make build") == (CD_UNRESOLVABLE, None)

    def test_env_var_target_is_unresolvable(self) -> None:
        # The VALUE varies per call site even when the NAME recurs; a
        # textual key would falsely group two different repos.
        assert leading_cd_resolution('cd "$REPO" && make build') == (CD_UNRESOLVABLE, None)
        assert leading_cd_resolution("cd $REPO && make build") == (CD_UNRESOLVABLE, None)

    def test_flagged_cd_declined_shape_is_unresolvable(self) -> None:
        # A leading cd EXISTS here, so the episode cwd field is KNOWN-wrong
        # as the effective dir: UNRESOLVABLE (refuse pairing), never NONE
        # (trust the field). This is the verdict `_strip_leading_cd`'s shape
        # parity alone could not express.
        assert leading_cd_resolution("cd -- /repo-a && make build") == (CD_UNRESOLVABLE, None)
        assert leading_cd_resolution("cd -L /repo-a && make build") == (CD_UNRESOLVABLE, None)

    def test_pushd_and_non_leading_cd_are_none(self) -> None:
        # Not recognized as a leading cd AT ALL — the cwd field stays the
        # caller's best information, same as before UPG-ARC-CWD-VS-
        # EFFECTIVE-DIR.
        assert leading_cd_resolution("pushd /repo-a && make build") == (CD_NONE, None)
        assert leading_cd_resolution("mkdir -p /repo-a && cd /repo-a && make build") == (CD_NONE, None)

    def test_last_cd_in_chain_wins(self) -> None:
        assert leading_cd_resolution("cd a && cd b && npm run build") == (CD_RELATIVE, "b")
        assert leading_cd_resolution("cd a && cd /abs && x") == (CD_ABSOLUTE, "/abs")
        # A bare cd LAST in the chain means the command runs in $HOME,
        # overriding any earlier concrete target.
        assert leading_cd_resolution("cd a && cd && x") == (CD_SYMBOLIC_HOME, "~")


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
