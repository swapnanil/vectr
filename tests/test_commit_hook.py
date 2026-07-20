"""Tests for the git post-commit commit-provenance hook (UPG-COMMIT-MEMORY-HOOK).

Covers: hooks-dir resolution (plain repo, linked worktree, non-git), the
shebang-compatibility gate, install/uninstall (merge-safe append/strip),
`cmd_hook`'s "post-commit" branch (fail-safety on a down/unreachable daemon),
deterministic note-content formatting (file-list capping, active-task
inclusion, subject truncation, detached HEAD), the REST write path
(POST /v1/commit-note), and structural boot-injection exclusion.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import main as m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_git_repo(tmp_path: Path, branch: str = "main") -> None:
    _run_git(tmp_path, "init", "-q")
    _run_git(tmp_path, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    _run_git(tmp_path, "config", "user.email", "test@test.com")
    _run_git(tmp_path, "config", "user.name", "test")
    _run_git(tmp_path, "commit", "--allow-empty", "-q", "-m", "init")


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {"path": "/project/a", "reset_config": False, "hooks": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _git_hooks_dir
# ---------------------------------------------------------------------------

class TestGitHooksDirResolution:
    def test_plain_repo_resolves_dot_git_hooks(self, tmp_path):
        _init_git_repo(tmp_path)
        hooks_dir = m._git_hooks_dir(str(tmp_path))
        assert hooks_dir == (tmp_path / ".git" / "hooks").resolve()

    def test_non_git_workspace_returns_none(self, tmp_path):
        assert m._git_hooks_dir(str(tmp_path)) is None

    def test_linked_worktree_resolves_shared_common_hooks_dir(self, tmp_path):
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        _init_git_repo(main_repo)
        worktree = tmp_path / "wt"
        _run_git(main_repo, "worktree", "add", "-q", "-b", "feature", str(worktree))

        main_hooks = m._git_hooks_dir(str(main_repo))
        wt_hooks = m._git_hooks_dir(str(worktree))
        assert main_hooks is not None
        assert wt_hooks == main_hooks  # hooks are shared across worktrees


# ---------------------------------------------------------------------------
# _shell_shebang_compatible
# ---------------------------------------------------------------------------

class TestShellShebangCompatible:
    @pytest.mark.parametrize("first_line", [
        "#!/bin/sh",
        "#!/bin/bash",
        "#!/usr/bin/env bash",
        "#!/usr/bin/env sh",
        "#!/opt/homebrew/bin/zsh",
    ])
    def test_compatible_shells_accepted(self, first_line):
        assert m._shell_shebang_compatible(first_line) is True

    @pytest.mark.parametrize("first_line", [
        "#!/usr/bin/env python3",
        "#!/usr/bin/env node",
        "#!/usr/bin/env pwsh",
        "#!/usr/bin/perl",
        "",
        "echo hello",  # no shebang at all
    ])
    def test_incompatible_or_missing_shebangs_rejected(self, first_line):
        assert m._shell_shebang_compatible(first_line) is False


# ---------------------------------------------------------------------------
# _write_git_post_commit_hook — install
# ---------------------------------------------------------------------------

class TestWriteGitPostCommitHookInstall:
    def test_fresh_repo_creates_executable_hook(self, tmp_path):
        _init_git_repo(tmp_path)
        m._write_git_post_commit_hook(str(tmp_path))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_path.exists()
        content = hook_path.read_text()
        assert content.startswith("#!/bin/sh\n")
        assert "vectr hook post-commit" in content
        assert "# vectr-start" in content and "# vectr-end" in content
        # executable bit set
        assert hook_path.stat().st_mode & 0o111

    def test_pre_existing_compatible_user_hook_content_preserved(self, tmp_path):
        _init_git_repo(tmp_path)
        hooks_dir = tmp_path / ".git" / "hooks"
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/bash\necho 'my custom hook'\n")
        m._write_git_post_commit_hook(str(tmp_path))
        content = hook_path.read_text()
        assert "echo 'my custom hook'" in content
        assert "#!/bin/bash" in content
        assert "vectr hook post-commit" in content
        assert "# vectr-start" in content and "# vectr-end" in content

    def test_incompatible_shebang_skips_and_discloses(self, tmp_path, capsys):
        _init_git_repo(tmp_path)
        hooks_dir = tmp_path / ".git" / "hooks"
        hook_path = hooks_dir / "post-commit"
        original = "#!/usr/bin/env python3\nprint('custom')\n"
        hook_path.write_text(original)
        m._write_git_post_commit_hook(str(tmp_path))
        assert hook_path.read_text() == original  # untouched
        err = capsys.readouterr().err
        assert "Skipped" in err
        assert "post-commit" in err

    def test_non_git_workspace_skips_silently_with_disclosure(self, tmp_path, capsys):
        m._write_git_post_commit_hook(str(tmp_path))
        assert not (tmp_path / ".git").exists()
        err = capsys.readouterr().err
        assert "Skipped" in err
        assert "not a git working tree" in err

    def test_idempotent_no_duplicate_vectr_blocks(self, tmp_path):
        _init_git_repo(tmp_path)
        m._write_git_post_commit_hook(str(tmp_path))
        m._write_git_post_commit_hook(str(tmp_path))
        content = (tmp_path / ".git" / "hooks" / "post-commit").read_text()
        assert content.count("# vectr-start") == 1
        assert content.count("# vectr-end") == 1

    def test_linked_worktree_installs_into_shared_hooks_dir(self, tmp_path):
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        _init_git_repo(main_repo)
        worktree = tmp_path / "wt"
        _run_git(main_repo, "worktree", "add", "-q", "-b", "feature", str(worktree))

        m._write_git_post_commit_hook(str(worktree))
        # installed hook lives in the shared common hooks dir, reachable from
        # either worktree's `git rev-parse --git-path hooks`
        hook_path = m._git_hooks_dir(str(main_repo)) / "post-commit"
        assert hook_path.exists()
        assert "vectr hook post-commit" in hook_path.read_text()


# ---------------------------------------------------------------------------
# _remove_git_post_commit_hook — uninstall
# ---------------------------------------------------------------------------

class TestRemoveGitPostCommitHook:
    def test_vectr_only_hook_deletes_file(self, tmp_path):
        _init_git_repo(tmp_path)
        m._write_git_post_commit_hook(str(tmp_path))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_path.exists()
        m._remove_git_post_commit_hook(str(tmp_path))
        assert not hook_path.exists()

    def test_vectr_block_removed_from_mixed_hook_user_content_preserved(self, tmp_path):
        _init_git_repo(tmp_path)
        hooks_dir = tmp_path / ".git" / "hooks"
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/bash\necho 'my custom hook'\n")
        m._write_git_post_commit_hook(str(tmp_path))
        m._remove_git_post_commit_hook(str(tmp_path))
        content = hook_path.read_text()
        assert "echo 'my custom hook'" in content
        assert "vectr hook post-commit" not in content
        assert "# vectr-start" not in content

    def test_non_git_workspace_is_a_noop(self, tmp_path):
        m._remove_git_post_commit_hook(str(tmp_path))  # must not raise

    def test_missing_hook_file_is_a_noop(self, tmp_path):
        _init_git_repo(tmp_path)
        m._remove_git_post_commit_hook(str(tmp_path))  # must not raise

    def test_foreign_hook_with_no_vectr_block_untouched(self, tmp_path):
        _init_git_repo(tmp_path)
        hooks_dir = tmp_path / ".git" / "hooks"
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/bash\necho 'never touched by vectr'\n")
        m._remove_git_post_commit_hook(str(tmp_path))
        assert "never touched by vectr" in hook_path.read_text()


# ---------------------------------------------------------------------------
# cmd_init wiring — --hooks installs, --reset-config removes
# ---------------------------------------------------------------------------

class TestCmdInitGitHookWiring:
    def test_init_hooks_installs_git_post_commit_hook(self, tmp_path):
        _init_git_repo(tmp_path)
        with patch("main._get_daemon_mode", return_value=None):
            m.cmd_init(_make_args(path=str(tmp_path), hooks=True))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_path.exists()
        assert "vectr hook post-commit" in hook_path.read_text()

    def test_init_without_hooks_flag_does_not_install_git_hook(self, tmp_path):
        _init_git_repo(tmp_path)
        with patch("main._get_daemon_mode", return_value=None):
            m.cmd_init(_make_args(path=str(tmp_path), hooks=False))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert not hook_path.exists()

    def test_reset_config_removes_git_post_commit_hook(self, tmp_path):
        _init_git_repo(tmp_path)
        with patch("main._get_daemon_mode", return_value=None):
            m.cmd_init(_make_args(path=str(tmp_path), hooks=True))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_path.exists()
        m.cmd_init(_make_args(path=str(tmp_path), reset_config=True))
        assert not hook_path.exists()

    def test_init_hooks_search_only_mode_skips_git_hook_too(self, tmp_path):
        _init_git_repo(tmp_path)
        with patch("main._get_daemon_mode", return_value="search-only"):
            m.cmd_init(_make_args(path=str(tmp_path), hooks=True, no_ide_config=True))
        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert not hook_path.exists()


# ---------------------------------------------------------------------------
# _git_fact
# ---------------------------------------------------------------------------

class TestGitFact:
    def test_returns_trimmed_stdout_on_success(self, tmp_path):
        _init_git_repo(tmp_path)
        sha = m._git_fact(str(tmp_path), "rev-parse", "--short", "HEAD")
        assert sha and len(sha) >= 4
        assert "\n" not in sha

    def test_returns_empty_string_on_non_git_dir(self, tmp_path):
        assert m._git_fact(str(tmp_path), "rev-parse", "--short", "HEAD") == ""

    def test_returns_empty_string_when_subprocess_raises(self, tmp_path):
        with patch("subprocess.run", side_effect=OSError("git not found")):
            assert m._git_fact(str(tmp_path), "status") == ""

    def test_returns_empty_string_on_timeout(self, tmp_path):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2)):
            assert m._git_fact(str(tmp_path), "status") == ""


# ---------------------------------------------------------------------------
# cmd_hook "post-commit" branch — fail-safety + gathered facts
# ---------------------------------------------------------------------------

class TestCmdHookPostCommitBranch:
    def test_no_daemon_serving_workspace_is_silent_noop(self, tmp_path, monkeypatch):
        _init_git_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        with patch("main._resolve_hook_instance", return_value=None), \
             patch("main._post_commit_note") as mock_post:
            m.cmd_hook(argparse.Namespace(hook_event="post-commit"))  # must not raise
        mock_post.assert_not_called()

    def test_daemon_registered_but_unreachable_never_raises(self, tmp_path, monkeypatch):
        _init_git_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        import httpx
        with patch("main._resolve_hook_instance", return_value={"port": 8765}), \
             patch("httpx.post", side_effect=httpx.ConnectError("down")):
            m.cmd_hook(argparse.Namespace(hook_event="post-commit"))  # must not raise/exit non-zero

    def test_non_git_directory_is_silent_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("main._resolve_hook_instance", return_value={"port": 8765}), \
             patch("main._post_commit_note") as mock_post:
            m.cmd_hook(argparse.Namespace(hook_event="post-commit"))
        mock_post.assert_not_called()

    def test_gathers_sha_subject_branch_files_and_posts(self, tmp_path, monkeypatch):
        _init_git_repo(tmp_path, branch="feature-x")
        (tmp_path / "a.py").write_text("x = 1\n")
        _run_git(tmp_path, "add", "a.py")
        _run_git(tmp_path, "commit", "-q", "-m", "add a.py")
        monkeypatch.chdir(tmp_path)

        with patch("main._resolve_hook_instance", return_value={"port": 8765}), \
             patch("main._post_commit_note") as mock_post:
            m.cmd_hook(argparse.Namespace(hook_event="post-commit"))

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        port, sha, subject, branch, files = args
        assert port == 8765
        assert len(sha) >= 4
        assert subject == "add a.py"
        assert branch == "feature-x"
        assert files == ["a.py"]

    def test_detached_head_reports_empty_branch(self, tmp_path, monkeypatch):
        _init_git_repo(tmp_path)
        sha_full = _run_git(tmp_path, "rev-parse", "HEAD").stdout.strip()
        _run_git(tmp_path, "checkout", "-q", sha_full)  # detach HEAD
        monkeypatch.chdir(tmp_path)

        with patch("main._resolve_hook_instance", return_value={"port": 8765}), \
             patch("main._post_commit_note") as mock_post:
            m.cmd_hook(argparse.Namespace(hook_event="post-commit"))

        mock_post.assert_called_once()
        _, _, _, branch, _ = mock_post.call_args.args
        assert branch == ""


# ---------------------------------------------------------------------------
# _format_commit_note_content — deterministic content shape
# ---------------------------------------------------------------------------

class TestFormatCommitNoteContent:
    def test_basic_shape_no_files_no_task(self):
        from app.service import _format_commit_note_content
        content = _format_commit_note_content("abc1234", "fix off-by-one", "main", [], None)
        assert content == "Commit abc1234 on main: fix off-by-one"

    def test_includes_file_list_under_cap(self):
        from app.service import _format_commit_note_content
        content = _format_commit_note_content(
            "abc1234", "small change", "main", ["a.py", "b.py"], None,
        )
        assert "Files (2): a.py, b.py" in content

    def test_caps_file_list_and_shows_remainder(self):
        from app.service import _format_commit_note_content
        from agent.config import HOOKS_COMMIT_NOTE_MAX_FILES
        files = [f"file{i}.py" for i in range(HOOKS_COMMIT_NOTE_MAX_FILES + 5)]
        content = _format_commit_note_content("abc1234", "big refactor", "main", files, None)
        shown = files[:HOOKS_COMMIT_NOTE_MAX_FILES]
        assert f"Files ({len(files)}): " + ", ".join(shown) + ", +5 more" in content

    def test_active_task_included_when_present(self):
        from app.service import _format_commit_note_content
        from agent.working_context_store._types import WorkingNote

        task_note = WorkingNote(
            note_id=42, workspace="/repo", content="working on segment targeting",
            tags=[], priority="high", created_at=0.0, last_accessed=0.0,
            kind="task", title="segment targeting rollout",
        )
        content = _format_commit_note_content("abc1234", "wip", "main", [], task_note)
        assert "Active task: [#42] segment targeting rollout" in content

    def test_active_task_absent_renders_no_task_line(self):
        from app.service import _format_commit_note_content
        content = _format_commit_note_content("abc1234", "wip", "main", [], None)
        assert "Active task:" not in content

    def test_subject_truncated_at_config_limit(self):
        from app.service import _format_commit_note_content
        from agent.config import HOOKS_COMMIT_NOTE_MAX_SUBJECT_CHARS
        long_subject = "x" * (HOOKS_COMMIT_NOTE_MAX_SUBJECT_CHARS + 50)
        content = _format_commit_note_content("abc1234", long_subject, "main", [], None)
        first_line = content.splitlines()[0]
        assert len(first_line) <= len("Commit abc1234 on main: ") + HOOKS_COMMIT_NOTE_MAX_SUBJECT_CHARS

    def test_detached_head_branch_renders_placeholder(self):
        from app.service import _format_commit_note_content
        content = _format_commit_note_content("abc1234", "wip", "", [], None)
        assert content.startswith("Commit abc1234 on (detached HEAD): wip")


# ---------------------------------------------------------------------------
# VectrService.record_commit_note — real store integration
# ---------------------------------------------------------------------------

class TestRecordCommitNoteIntegration:
    def _make_service(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))
        return svc

    def test_writes_note_with_finding_kind_and_auto_provenance(self, tmp_path, monkeypatch):
        svc = self._make_service(tmp_path, monkeypatch)
        note_id = svc.record_commit_note("abc1234", "fix bug", "main", ["a.py"])
        assert isinstance(note_id, int)
        note = svc._context_store.get_note(str(tmp_path), note_id)
        assert note.kind == "finding"
        assert note.provenance == "auto"
        assert "auto-provenance" in note.tags
        assert note.author_id == "git-post-commit-hook"
        assert "Commit abc1234 on main: fix bug" in note.content

    def test_active_task_note_is_included_when_high_priority_task_exists(self, tmp_path, monkeypatch):
        svc = self._make_service(tmp_path, monkeypatch)
        task_id = svc.remember(
            content="implementing segment targeting", kind="task", priority="high",
            title="segment targeting",
        )
        note_id = svc.record_commit_note("abc1234", "wip", "main", [])
        note = svc._context_store.get_note(str(tmp_path), note_id)
        assert f"Active task: [#{task_id}] segment targeting" in note.content

    def test_no_active_task_note_when_none_exists(self, tmp_path, monkeypatch):
        svc = self._make_service(tmp_path, monkeypatch)
        note_id = svc.record_commit_note("abc1234", "wip", "main", [])
        note = svc._context_store.get_note(str(tmp_path), note_id)
        assert "Active task:" not in note.content

    def test_low_priority_task_note_is_not_treated_as_active(self, tmp_path, monkeypatch):
        # boot_recall (which _current_task_note reuses) only surfaces
        # priority='high' task notes — matches SessionStart injection exactly.
        svc = self._make_service(tmp_path, monkeypatch)
        svc.remember(content="minor task", kind="task", priority="medium")
        note_id = svc.record_commit_note("abc1234", "wip", "main", [])
        note = svc._context_store.get_note(str(tmp_path), note_id)
        assert "Active task:" not in note.content


# ---------------------------------------------------------------------------
# Boot-injection exclusion — structural, via default_bundle_for_kind
# ---------------------------------------------------------------------------

class TestBootInjectionExclusion:
    def test_commit_note_absent_from_boot_recall(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))

        # A high-priority directive AND task also exist, to prove the commit
        # note is excluded specifically, not that boot_recall returns nothing.
        svc.remember(content="never push to main", kind="directive", priority="high")
        svc.remember(content="current sprint work", kind="task", priority="high")
        svc.record_commit_note("abc1234", "fix bug", "main", ["a.py"])

        boot_notes = svc._context_store.boot_recall(str(tmp_path))
        assert all("auto-provenance" not in (n.tags or []) for n in boot_notes)
        assert all(n.kind != "finding" for n in boot_notes)

    def test_default_bundle_for_finding_kind_is_always_empty(self):
        from agent.trigger_engine import default_bundle_for_kind
        for priority in ("low", "medium", "high", None):
            assert default_bundle_for_kind("finding", anchors=None, priority=priority) == []


# ---------------------------------------------------------------------------
# REST — POST /v1/commit-note
# ---------------------------------------------------------------------------

class TestCommitNoteRoute:
    def test_happy_path_returns_note_id(self, client_real_memory):
        resp = client_real_memory.post(
            "/v1/commit-note",
            json={"sha": "abc1234", "subject": "fix bug", "branch": "main", "files": ["a.py"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["note_id"], int)
        assert "processing_ms" in data

    def test_missing_sha_returns_422(self, client_real_memory):
        resp = client_real_memory.post("/v1/commit-note", json={"subject": "no sha"})
        assert resp.status_code == 422

    def test_defaults_for_optional_fields(self, client_real_memory):
        resp = client_real_memory.post("/v1/commit-note", json={"sha": "abc1234"})
        assert resp.status_code == 200

    def test_written_note_recallable_by_id(self, client_real_memory):
        resp = client_real_memory.post(
            "/v1/commit-note",
            json={"sha": "abc1234", "subject": "fix bug", "branch": "main", "files": ["a.py"]},
        )
        note_id = resp.json()["note_id"]
        recall_resp = client_real_memory.post("/v1/recall", json={"note_id": note_id})
        assert recall_resp.status_code == 200
        assert "fix bug" in recall_resp.json()["notes"]

    def test_search_only_mode_returns_503(self, client):
        from api import app
        app.state.service.search_only = True
        try:
            resp = client.post("/v1/commit-note", json={"sha": "abc1234", "subject": "wip"})
            assert resp.status_code == 503
            assert resp.json()["detail"]["error"] == "search_only_mode"
        finally:
            app.state.service.search_only = False
