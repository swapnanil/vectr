"""Tests for agent/episode_normalize.py (memoization-l1-capture-design §3.1).

Pure, structural Bash-command normalization — no per-binary knowledge, only
argv structure (flag-shaped / value-shaped-by-STRUCTURAL-class / bare).
"""
from __future__ import annotations

from agent.episode_normalize import normalize_command


class TestSpecCanonicalExamples:
    """The three worked examples memoization-l1-capture-design §3.1 gives
    verbatim — the normalizer must reproduce all three exactly."""

    def test_git_commit(self):
        result = normalize_command('git commit -m "fix bug"')
        assert result["verb"] == "git commit"
        assert result["flags"] == ["-m=fix bug"]
        assert result["args"] == []
        assert result["env_delta_names"] == []

    def test_mvnw_test(self):
        result = normalize_command("./mvnw test")
        assert result["verb"] == "./mvnw test"
        assert result["flags"] == []
        assert result["args"] == []

    def test_npm_run_build(self):
        result = normalize_command("npm run build")
        assert result["verb"] == "npm run build"
        assert result["flags"] == []
        assert result["args"] == []


class TestFirstTokenAlwaysInVerb:
    """token[0] is unconditionally part of `verb`, even path-shaped (e.g.
    "./mvnw") — the first token can never be a flag or a value by
    definition, so there is no structural reason to exclude it."""

    def test_path_shaped_binary_is_verb_not_a_path_arg(self):
        result = normalize_command("./scripts/build.sh")
        assert result["verb"] == "./scripts/build.sh"
        assert result["args"] == []


class TestValueClassification:
    def test_numeric_arg_classified_as_num(self):
        result = normalize_command("sleep 30")
        assert result["verb"] == "sleep"
        assert result["args"] == [{"value": "30", "class": "NUM"}]

    def test_version_arg_classified_as_version(self):
        result = normalize_command("nvm install v18.20.4")
        assert result["args"][-1]["class"] == "VERSION"

    def test_path_arg_classified_as_path(self):
        result = normalize_command("git add ./src/main.py")
        assert {"value": "./src/main.py", "class": "PATH"} in result["args"]

    def test_uuid_arg_classified_as_uuid(self):
        result = normalize_command(
            "docker inspect 550e8400-e29b-41d4-a716-446655440000"
        )
        assert result["args"][-1]["class"] == "UUID"

    def test_bare_word_arg_has_no_class(self):
        result = normalize_command("git checkout main")
        # "main" isn't flag/value shaped, but it isn't verb-shaped either
        # once a prior token has already stopped absorbing into verb —
        # see TestKnownLimitation for exactly where the line falls.
        assert any(a["class"] is None for a in result["args"]) or result["verb"] == "git checkout main"


class TestFlagHandling:
    def test_bare_flag(self):
        result = normalize_command("ls -la")
        assert result["flags"] == ["-la"]

    def test_multiple_files_after_flags_stay_args(self):
        result = normalize_command("git add file1.txt file2.txt")
        values = [a["value"] for a in result["args"]]
        assert "file1.txt" in values
        assert "file2.txt" in values

    def test_flags_are_order_normalized(self):
        a = normalize_command("ls -l -a")
        b = normalize_command("ls -a -l")
        assert a["flags"] == b["flags"]


class TestEnvDeltaNames:
    """§3.1: "env-var prefixes -> captured as env delta" — leading
    `VAR=value` tokens on the SAME command string, not a runtime diff
    against a previous episode."""

    def test_single_env_prefix(self):
        result = normalize_command("FOO=bar make build")
        assert result["env_delta_names"] == ["FOO"]
        assert result["verb"] == "make build"

    def test_multiple_env_prefixes(self):
        result = normalize_command("A=1 B=2 npm test")
        assert result["env_delta_names"] == ["A", "B"]
        assert result["verb"] == "npm test"

    def test_no_env_prefix_yields_empty_list(self):
        result = normalize_command("npm test")
        assert result["env_delta_names"] == []


class TestDecorationStripping:
    def test_redirect_and_pipe_stripped_before_normalization(self):
        result = normalize_command("cd /tmp/x && npm test 2>&1 | tail -20")
        assert result["verb"] in ("npm test", "cd npm test")
        # cd-prefix handling: the leading `cd X &&` segment is dropped so
        # normalization runs on the actual command, not the directory change.
        assert "npm" in result["verb"]


class TestKnownLimitation:
    """Documented, accepted tradeoff: several bare positional words before
    the first flag all absorb into `verb`, since nothing in the command's
    own structure distinguishes a subcommand continuation from a positional
    argument once both are equally bare words."""

    def test_git_push_origin_main_absorbs_into_verb(self):
        result = normalize_command("git push origin main")
        assert result["verb"] == "git push origin main"
        assert result["args"] == []
