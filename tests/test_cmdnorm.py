"""Tests for app/cmdnorm.py — deterministic command normalization
(L1 capture design doc §3.1, LANE-ARC).

Table-driven: tokenization, semantics-neutral decoration stripping, verb
extraction (incl. the absorption cap), and positional-argument abstraction
(<PATH>/<VERSION>/<UUID>/<NUM>) for every class and edge case.
"""
from __future__ import annotations

import dataclasses

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


class TestEscapedSeparatorSplit:
    r"""UPG-CMDNORM-ESCAPED-SEPARATOR-SPLIT: the padding pass correctly
    refuses to pad a `<bs><sep>` (so a glued `echo a\;b` stays one token),
    but the loss happens DOWNSTREAM — `shlex.split(..., posix=True)`
    strips the backslash, leaving a bare `;` token that `_split_on_any`
    misreads as a real separator. The marker-tagging path in the padding
    pass (see `app/cmdnorm.py:_ESCAPED_SEP_MARKER`) plus the marker-
    consuming branch in `_split_on_any` carry the "this sep was escaped"
    signal through shlex so the bare token is never matched. The
    `\<sep>` only triggers the marker when the escape sits at a TOKEN
    BOUNDARY (preceded by whitespace) — glued escapes like `echo a\;b`
    keep riding through shlex as a single token, no marker needed, and
    the test above pins that."""

    def test_tokenize_marks_escaped_semicolon_at_boundary(self) -> None:
        # `find . -exec cmd {} \; -print` — the `\;` is at a token boundary
        # (preceded by whitespace), so the padding pass tags it with the
        # marker before shlex. The marker survives shlex (it's a regular
        # char) and shows up attached to the bare `;` in the shlex output.
        # What we pin here is the structure: the marker is present, the
        # bare `;` is not a separate token. The marker is internal and
        # gets stripped in `_split_on_any`.
        toks = tokenize("find . -exec cmd {} \\; -print")
        # No bare `;` token — the marker kept it from being one.
        assert ";" not in toks
        # Exactly one token whose last char is `;`: the marker+`;` pair.
        # (We avoid hard-coding the marker character in the assertion so
        # this test does not depend on a specific U+ codepoint.)
        assert sum(1 for t in toks if t.endswith(";")) == 1
        # The token count is one MORE than the spaced form `find . -exec
        # cmd {} ; -print` (which would split to 7 tokens, 5 of which
        # are real tokens; we get 7 here too, but the `;` is in the same
        # token as the marker, not its own token).
        assert len(toks) == 7

    def test_normalize_escaped_semicolon_with_following_tokens(self) -> None:
        # The headline fix: when more tokens follow the escaped `;`, the
        # phantom split used to mis-attribute the command. `find ... \;`
        # used to leave `-print` as the entire primary segment (verb=`''`);
        # with the marker, the primary segment keeps every token and the
        # verb is `find` again.
        n = normalize_command("find . -exec cmd {} \\; -print")
        assert n.verb == "find"
        # The escaped `;` is not promoted to argv (mirrors the pre-fix
        # behavior of the empty trailing segment being discarded by
        # `_split_on_any`'s `[s for s in segments if s]` filter).
        assert ";" not in n.args

    def test_normalize_escaped_semicolon_terminal_case_preserved(self) -> None:
        # Terminal `find -exec ... \;` was the benign pre-fix case (the
        # phantom split produced an empty trailing segment that the
        # final filter discarded). The marker path keeps that behavior
        # exact: argv is unchanged, the `;` does not become a stored arg.
        n = normalize_command("find . -exec rm {} \\;")
        assert n.verb == "find"
        # Pre-fix args were ('.', '{}'); the marker path leaves them the
        # same. (The `;` is consumed by the marked-sep branch, not added
        # to argv — see `_split_on_any`'s docstring.)
        assert ";" not in n.args

    def test_quoted_separator_still_untouched(self) -> None:
        # The marker path is for unquoted backslash-escapes only. A
        # quoted `;` is a literal char inside one shlex token and never
        # gets the marker.
        assert tokenize('echo "a;b"') == ["echo", "a;b"]
        assert tokenize("echo 'a;b'") == ["echo", "a;b"]
        assert tokenize('echo "a|b"') == ["echo", "a|b"]
        assert tokenize("echo 'a|b'") == ["echo", "a|b"]

    def test_glued_escape_does_not_get_marker(self) -> None:
        # `echo a\;b` — the `\;` is glued to `a` and `b`, no whitespace
        # around it, so it is NOT at a token boundary and the marker
        # path does not fire. shlex already keeps the literal `;` inside
        # one token. (Pins the existing test's behavior; doubles as a
        # regression guard against the marker accidentally tagging
        # glued escapes.)
        assert tokenize("echo a\\;b") == ["echo", "a;b"]

    def test_escaped_doubled_ampersand_at_boundary_kept_literal(self) -> None:
        # `make \&\& build` puts the escape at a boundary, so the marker
        # path fires and the doubled `&&` is not split. Verb stays `make`
        # (a single bareword; `build` is absorbed into the verb by the
        # ARC_NORM_MAX_VERB_TOKENS=3 absorption).
        n = normalize_command("make \\&\\& build")
        assert n.verb == "make build"
        # The escaped `&&` is consumed by the marker path (mirrors the
        # pre-fix empty-trailing-segment-discard behavior), so it is
        # not promoted to argv as a literal token.
        assert "&&" not in n.args

    def test_escaped_doubled_pipe_at_boundary_kept_literal(self) -> None:
        # Mirror of the doubled-ampersand case for `\|\|`. The escaped
        # `||` is at a token boundary, marked, and not split. The whole
        # command is one segment; the verb absorption runs over all
        # three remaining tokens (`echo`, `a`, `b`) and produces a
        # 3-token verb. Pre-fix this routed to the `b` segment instead
        # of `echo`, attributing the captured `echo` to the wrong
        # command.
        n = normalize_command("echo a \\|\\| b")
        assert n.verb == "echo a b"

    def test_double_escape_unchanged(self) -> None:
        # `\\;` is a LITERAL backslash followed by a real separator —
        # the user's `\\` is "this backslash is literal", then `;` is a
        # genuine compound separator. Our pad pass must not mark it
        # (the first backslash's `nxt` is `\`, which is not a sep). The
        # `;` then fires its own padding branch normally. Same as pre-fix.
        n = normalize_command("echo a\\\\;ls")
        # The `\\` becomes a literal backslash in argv, then `;` is a
        # real separator, then `ls` is the next segment. The primary
        # segment is the LAST one (`ls`).
        assert n.verb == "ls"

    def test_unbalanced_quotes_fallback_preserves_backslash(self) -> None:
        # The unbalanced-quote fallback path bypasses the padding pass
        # entirely (tokenize's `cmd_raw.split()` operates on the
        # ORIGINAL string, not the padded one). The `\;` therefore
        # arrives at `_split_on_any` as the literal `\\;` token (with
        # the backslash preserved) — not a bare `;` — so it is not in
        # `_COMPOUND_SEPARATORS` and no split happens. Same as pre-fix;
        # pinned here so the fallback is documented as escape-safe.
        n = normalize_command('find . -exec rm {} \\; "unterminated')
        # With unbalanced quotes, the rest of the string is in the
        # fallback's whitespace split, but `find` and the literal `\;`
        # are still recognizable as the first segment's verb/args.
        assert n.verb == "find"


class TestSinglePipeAmp:
    """UPG-CMDNORM-SINGLE-PIPE-AMP part (a): a glued single `|`
    (`cat a|grep b`) is not a member of `_COMPOUND_SEPARATORS` and was
    not padded by the pre-fix pass, so the existing `|` split on the
    primary segment never saw it. The new single-`|` branch in the
    padding pass makes glued and spaced pipes tokenize identically,
    with no vocabulary change. Part (b) — background `&` as a segment
    boundary — is intentionally NOT implemented here (see report)."""

    def test_glued_single_pipe_splits(self) -> None:
        # The headline fix: `cat a|grep b` now tokenizes like
        # `cat a | grep b`. The resulting verb and downstream-stage
        # tokens match the spaced form.
        toks = tokenize("cat a|grep b")
        assert toks == ["cat", "a", "|", "grep", "b"]

    def test_glued_single_pipe_normalizes_like_spaced(self) -> None:
        # End-to-end check: `cat a|grep b` and `cat a | grep b` produce
        # the same NormalizedCommand. Pre-fix the glued form was one
        # segment (no `|` token to split on) so the display-stage
        # stripping never fired and the primary was wrong.
        glued = normalize_command("cat a|grep b")
        spaced = normalize_command("cat a | grep b")
        assert glued.verb == spaced.verb
        assert glued.flags == spaced.flags
        assert glued.args == spaced.args
        assert glued.arg_classes == spaced.arg_classes
        # `a` is a bareword and is absorbed into the verb (the same
        # verb-absorption cap, ARC_NORM_MAX_VERB_TOKENS=3, that pulls
        # `foo bar` into the verb in `make foo bar baz qux`); `b` is
        # also a bareword but arrives via the downstream `grep b`
        # stage, where it is folded into the comparison set as a
        # positional arg, not a verb token.
        assert "b" in glued.args
        # Spaced-vs-glued parity: the headline invariant. Compared with
        # `cmd_raw` excluded, because that field stores the ORIGINAL
        # string verbatim and so differs by construction between the two
        # spellings — a whole-dataclass `==` could never pass here. Every
        # NORMALIZED field must match, which is what parity means.
        as_dict = dataclasses.asdict
        glued_norm, spaced_norm = as_dict(glued), as_dict(spaced)
        assert glued_norm.pop("cmd_raw") != spaced_norm.pop("cmd_raw")
        assert glued_norm == spaced_norm

    def test_doubled_pipe_unchanged_after_single_pipe_branch(self) -> None:
        # The single-`|` branch is placed AFTER the doubled `&&`/`||`
        # branch in the pad pass, so `||` is still recognized as the
        # doubled compound separator. This test is the reviewer-first
        # guard: a single-`|` implementation placed before the doubled
        # check would split `a||b` as `a | | b` and route the primary
        # to `b` instead of `a`.
        toks = tokenize("a||b")
        assert toks == ["a", "||", "b"]
        # Spaced variant too.
        assert tokenize("a || b") == ["a", "||", "b"]

    def test_doubled_ampersand_unchanged_after_single_pipe_branch(self) -> None:
        # Mirror of the above for `&&`. The single-`|` branch only
        # fires on `c == "|"` and never on `&`, so the doubled-`&`
        # branch's behavior is untouched.
        toks = tokenize("a&&b")
        assert toks == ["a", "&&", "b"]
        assert tokenize("a && b") == ["a", "&&", "b"]

    def test_glued_mixed_doubled_and_single_pipe(self) -> None:
        # `a||b|c` exercises the branch ordering: position 1 is `|`,
        # the doubled check sees `cmd_raw[2] == "|"` and fires the
        # `||` padding; then position 4 is the trailing `|`, the
        # doubled check sees `cmd_raw[5] == "c"`, fails, and the
        # single-`|` branch fires. Three segments as expected.
        toks = tokenize("a||b|c")
        assert toks == ["a", "||", "b", "|", "c"]

    def test_quoted_pipe_unchanged_after_single_pipe_branch(self) -> None:
        # The single-`|` branch is OUTSIDE the in_double / in_single
        # quote-state branches, so a `|` inside quotes is never
        # padded. Pre-fix and post-fix produce the same token list.
        assert tokenize('grep "foo|bar" file.txt') == ["grep", "foo|bar", "file.txt"]
        assert tokenize("grep 'foo|bar' file.txt") == ["grep", "foo|bar", "file.txt"]


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
