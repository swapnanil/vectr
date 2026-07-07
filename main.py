"""CLI entry point: vectr start / restart / stop / index / search / status / init."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from agent.config import (
    CLI_START_READY_POLL_INTERVAL_S,
    CLI_START_READY_POLL_TIMEOUT_S,
    CLI_START_READY_PROBE_TIMEOUT_S,
)
from agent.instance_registry import (
    InstanceRegistry,
    _is_pid_alive,
    workspace_hash,
)
from agent.prompt_templates import load_template

load_dotenv()

# Legacy single-instance files — removed on first registry write, kept here only
# so migration can clean them up.
_LEGACY_PID_FILE = Path.home() / ".vectr" / "vectr.pid"
_LEGACY_PORT_FILE = Path.home() / ".vectr" / "vectr.port"

# Per-turn recall hook tuning (UPG-9.5). Small N + a relevance floor keep the
# UserPromptSubmit injection tight: only notes genuinely related to the prompt,
# nothing on an off-topic turn. Override via env without re-running init.
_HOOK_RECALL_LIMIT = 3
_HOOK_MIN_SIMILARITY = 0.35

# UPG-11.5 — hook-injected notes announce themselves so the model doesn't also
# self-call vectr_recall for the same purpose. Without this, SessionStart/
# UserPromptSubmit injection and the model's own recall both fire and pay for
# the same memory twice (found in the eval v2 N=1 audit: arm C self-recalled
# on top of its hook injection). Prepended only to the events that duplicate
# what CLAUDE.md's session-start guidance already tells the model to fetch.
_HOOK_NO_DOUBLE_RECALL_LINE = load_template("hook_no_double_recall.txt")
_HOOK_EVENTS_ANNOUNCE_INJECTION = ("SessionStart", "UserPromptSubmit")

_CLAUDE_MD = load_template("claude_md.md")

# UPG-SEARCH-ONLY-MODE — this workspace's daemon runs with working memory
# disabled (no notes DB exists). Only the search/locate/trace/map tools are
# documented; there is no working-memory section, no session-start recall
# instructions, and no mention of vectr_remember/vectr_recall.
_CLAUDE_MD_SEARCH_ONLY = load_template("claude_md_search_only.md")

# Default session-start guidance: no hooks installed, so the model must
# self-call vectr_status/vectr_recall to ever see prior notes.
_SESSION_START_GUIDANCE_DEFAULT = load_template("session_start_guidance_default.txt")

# UPG-11.5 — hook-aware variant: when Claude Code hooks are installed for this
# workspace, SessionStart/UserPromptSubmit already inject recalled notes
# automatically (see `_emit_hook_context`), so repeating "call vectr_recall at
# session start" pays for the same memory twice. Redirect vectr_recall to the
# genuinely on-demand cases instead.
_SESSION_START_GUIDANCE_HOOKS_AWARE = load_template("session_start_guidance_hooks_aware.txt")


def _render_claude_md(hooks_installed: bool, search_only: bool = False) -> str:
    """Render the CLAUDE.md guidance block.

    `search_only` selects the search-only variant (UPG-SEARCH-ONLY-MODE) — no
    working-memory section, no session-start recall instructions, since this
    daemon has no notes DB. Otherwise `hooks_installed` selects the
    session-start guidance matching whether Claude Code hooks are installed
    for this workspace (UPG-11.5)."""
    if search_only:
        return _CLAUDE_MD_SEARCH_ONLY
    guidance = _SESSION_START_GUIDANCE_HOOKS_AWARE if hooks_installed else _SESSION_START_GUIDANCE_DEFAULT
    return _CLAUDE_MD.replace("__SESSION_START_GUIDANCE__", guidance)


_MCP_JSON = load_template("mcp.json.template")

# Cursor omits the "type" key (it infers HTTP from the url scheme)
_CURSOR_MCP_JSON = load_template("cursor_mcp.json.template")

# VSCode 1.99+ / GitHub Copilot Agent Mode uses "servers" (not "mcpServers")
_VSCODE_MCP_JSON = load_template("vscode_mcp.json.template")

_VECTR_BLOCK_START = "<!-- vectr-start -->"
_VECTR_BLOCK_END = "<!-- vectr-end -->"
# Matches the vectr block plus any blank lines immediately before it.
_VECTR_BLOCK_RE = re.compile(
    r"\n*<!-- vectr-start -->.*?<!-- vectr-end -->\n?",
    re.DOTALL,
)

# IDE config files that get the vectr block appended (not created from scratch).
_IDE_CONFIG_APPEND_ONLY: tuple[str, ...] = (
    "AGENTS.md",
    ".cursorrules",
    "GEMINI.md",
    "CODEX.md",
)


def _make_vectr_block(*, hooks_installed: bool = False, search_only: bool = False) -> str:
    """`hooks_installed` selects the session-start guidance variant (UPG-11.5) —
    only meaningful for CLAUDE.md, since Claude Code hooks are the only
    injection path today; other IDE config files always get the default.
    `search_only` selects the no-working-memory variant (UPG-SEARCH-ONLY-MODE)
    and takes precedence over `hooks_installed`."""
    return f"{_VECTR_BLOCK_START}\n{_render_claude_md(hooks_installed, search_only=search_only).rstrip()}\n{_VECTR_BLOCK_END}\n"


def _write_ide_config_merge_safe(
    path: Path, *, create_if_missing: bool, hooks_installed: bool = False, search_only: bool = False,
) -> None:
    """Write the vectr guidance block into an IDE config file.

    - File missing + create_if_missing=True  → create file containing just the block.
    - File missing + create_if_missing=False → no-op.
    - File exists, no vectr block            → append block after existing content.
    - File exists, vectr block present       → replace block in-place (idempotent).
    """
    block = _make_vectr_block(hooks_installed=hooks_installed, search_only=search_only)

    if not path.exists():
        if not create_if_missing:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block, encoding="utf-8")
        print(f"  Created {path}", file=sys.stderr)
        return

    existing = path.read_text(encoding="utf-8")

    if _VECTR_BLOCK_START in existing:
        stripped = _VECTR_BLOCK_RE.sub("", existing).rstrip()
        new_content = f"{stripped}\n\n{block}" if stripped else block
        if new_content == existing:
            return
        path.write_text(new_content, encoding="utf-8")
        print(f"  Updated vectr block in {path}", file=sys.stderr)
    else:
        path.write_text(f"{existing.rstrip()}\n\n{block}", encoding="utf-8")
        print(f"  Appended vectr block to {path}", file=sys.stderr)


def _remove_vectr_block(path: Path) -> None:
    """Remove the vectr block from a file. Delete the file if it becomes empty."""
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if _VECTR_BLOCK_START not in content:
        return
    stripped = _VECTR_BLOCK_RE.sub("", content).rstrip()
    if stripped:
        path.write_text(stripped + "\n", encoding="utf-8")
        print(f"  Removed vectr block from {path}", file=sys.stderr)
    else:
        path.unlink()
        print(f"  Deleted {path} (was vectr-only)", file=sys.stderr)


def _write_cursor_rules(workspace: str, *, search_only: bool = False) -> None:
    """Write .cursor/rules/vectr.mdc for Cursor IDE (vectr-owned file, always current)."""
    path = Path(workspace) / ".cursor" / "rules" / "vectr.mdc"
    content = (
        f"{load_template('cursor_rules_header.txt')}"
        f"{_render_claude_md(hooks_installed=False, search_only=search_only).rstrip()}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if not existed or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        print(f"  {'Updated' if existed else 'Created'} {path}", file=sys.stderr)


def _api_base(port: int) -> str:
    return f"http://localhost:{port}"


def _daemon_error_detail(exc: "httpx.HTTPStatusError") -> str:
    """Extract the server's structured `detail` message from a daemon error
    response, e.g. {"detail": {"error": "memory_only_mode", "detail": "..."}}
    (see app/routes.py's mode-gated 503s). Falls back to the raw status code
    when the body isn't in that shape.
    """
    try:
        body = exc.response.json()
    except Exception:
        return f"Vectr returned HTTP {exc.response.status_code}."
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return detail.get("detail") or detail.get("error") or str(detail)
    if isinstance(detail, str):
        return detail
    return f"Vectr returned HTTP {exc.response.status_code}."


def _handle_daemon_call_error(exc: Exception, port: int) -> None:
    """Uniform CLI error contract for every subcommand that talks to the
    daemon over REST (UPG-CLI-MEMONLY-CRASH): a down daemon and a daemon
    that declines the request (e.g. search/index disabled in memory-only
    mode, memory tools disabled in search-only mode) must both print one
    clean line to stderr and exit 1 — never an unhandled traceback exposing
    internal paths. Re-raises anything it doesn't recognise so a genuine bug
    still surfaces instead of being silently swallowed.
    """
    import httpx
    if isinstance(exc, httpx.ConnectError):
        print(f"Error: Vectr is not running on port {port}. Run: vectr start", file=sys.stderr)
        sys.exit(1)
    if isinstance(exc, httpx.HTTPStatusError):
        print(f"Error: {_daemon_error_detail(exc)}", file=sys.stderr)
        sys.exit(1)
    raise exc


def _is_server_alive(port: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    """Return (alive, workspace_root). Non-blocking within timeout."""
    try:
        import httpx
        resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=timeout)
        resp.raise_for_status()
        return True, resp.json().get("workspace_root")
    except Exception:
        return False, None


def _wait_for_daemon_ready(port: int, pid: int) -> bool:
    """Poll /v1/status until the just-spawned daemon actually answers, or
    `cli.start_ready_poll_timeout_s` elapses (UPG-CLI-START-READY-RACE).
    Returns True only once the daemon is genuinely listening and responding
    — never optimistically. Returns False early (without waiting out the
    full timeout) if the spawned process has already exited, since a dead
    process will never start responding.
    """
    deadline = time.monotonic() + CLI_START_READY_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        alive, _ = _is_server_alive(port, timeout=CLI_START_READY_PROBE_TIMEOUT_S)
        if alive:
            return True
        if not _is_pid_alive(pid):
            return False
        time.sleep(CLI_START_READY_POLL_INTERVAL_S)
    return False


def _get_daemon_mode(port: int, timeout: float = 2.0) -> str | None:
    """Return the live daemon's mode ("full" / "memory-only" / "search-only"),
    or None if the daemon isn't reachable. Used by `vectr init` to decide
    whether hooks make sense (search-only has no working-memory layer to
    inject) — queried live rather than persisted, since mode is a daemon
    property, not workspace-static state."""
    try:
        import httpx
        resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("mode")
    except Exception:
        return None


def _stop_server(pid: int, timeout_s: int = 8) -> bool:
    """SIGTERM → wait → SIGKILL. Returns True if process is gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
    except ProcessLookupError:
        return True
    try:
        os.kill(pid, 0)
        return False  # still alive after SIGKILL — caller should log and continue
    except ProcessLookupError:
        return True


def _write_or_update(path: Path, content: str, label: str) -> None:
    """Write file if missing; overwrite if content changed (port update)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  Created {path}", file=sys.stderr)
    elif path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        print(f"  Updated {path} ({label})", file=sys.stderr)


_IDE_CONFIG_MARKER_REL = Path(".vectr") / "ide_config"


def _ide_config_disabled(workspace: str) -> bool:
    """True if this workspace durably opted out of vectr's automatic IDE
    integration file writes (UPG-CLI-WRITES-DISCLOSURE) via a prior
    `--no-ide-config` run. Persisted at `.vectr/ide_config` so subsequent
    start/restart/init calls honor the choice without repeating the flag —
    delete the file to re-enable."""
    marker = Path(workspace) / _IDE_CONFIG_MARKER_REL
    if not marker.exists():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == "disabled"
    except OSError:
        return False


def _persist_ide_config_disabled(workspace: str) -> None:
    marker = Path(workspace) / _IDE_CONFIG_MARKER_REL
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("disabled\n", encoding="utf-8")


def _maybe_write_workspace_config(
    workspace: str, port: int, args: argparse.Namespace, *, search_only: bool = False,
) -> None:
    """Gate `_write_workspace_config` on `--no-ide-config` or a prior durable
    opt-out (UPG-CLI-WRITES-DISCLOSURE): `start`/`restart`/`init` previously
    wrote 7 IDE integration files into the workspace root on every call with
    no disclosure in --help and no way to opt out. `--no-ide-config` skips
    the writes for this call AND persists the choice (`.vectr/ide_config`)
    so later start/restart calls keep honoring it without repeating the flag.
    """
    if getattr(args, "no_ide_config", False):
        _persist_ide_config_disabled(workspace)
        print(
            f"  Skipped IDE config file writes for {workspace} (--no-ide-config). "
            f"This choice persists at {Path(workspace) / _IDE_CONFIG_MARKER_REL} "
            f"for future start/restart/init calls; delete that file to re-enable.",
            file=sys.stderr,
        )
        return
    if _ide_config_disabled(workspace):
        print(
            f"  IDE config file writes are disabled for {workspace} "
            f"({Path(workspace) / _IDE_CONFIG_MARKER_REL}). Delete that file to re-enable.",
            file=sys.stderr,
        )
        return
    _write_workspace_config(workspace, port, search_only=search_only)


def _write_workspace_config(workspace: str, port: int, *, search_only: bool = False) -> None:
    """Write per-IDE MCP config files and IDE guidance into the workspace root.

    CLAUDE.md's session-start guidance is hook-aware (UPG-11.5): if Claude Code
    hooks are already installed for this workspace, the injected notes make a
    self-recall at session start redundant, so CLAUDE.md is written with the
    hook-aware variant instead. Other IDE config files always get the default
    variant — Claude Code hooks are the only automatic-injection path today.

    `search_only` (UPG-SEARCH-ONLY-MODE) takes precedence over the hook-aware
    variant: this daemon has no working-memory layer, so every IDE config file
    gets the search-only guidance (search tools only, no memory section).
    """
    root = Path(workspace)
    hooks_installed = _hooks_installed(workspace)

    # UPG-13.2: seed a default .vectrignore on first start/init so users get
    # sensible excludes (node_modules, .venv, __pycache__, ...) without hand-
    # authoring them. No-op (never overwrites) if one already exists.
    from integrations.workspace_detect import write_default_vectrignore
    if write_default_vectrignore(workspace):
        print(f"  Created {root / '.vectrignore'} (default excludes)", file=sys.stderr)

    _write_ide_config_merge_safe(
        root / "CLAUDE.md", create_if_missing=True, hooks_installed=hooks_installed, search_only=search_only,
    )
    for _rel in _IDE_CONFIG_APPEND_ONLY:
        _write_ide_config_merge_safe(root / _rel, create_if_missing=False, search_only=search_only)
    _write_ide_config_merge_safe(
        root / ".github" / "copilot-instructions.md", create_if_missing=False, search_only=search_only,
    )
    _write_cursor_rules(workspace, search_only=search_only)

    _write_or_update(root / ".mcp.json", _MCP_JSON.format(port=port), f"port {port}")
    _write_or_update(root / ".cursor" / "mcp.json", _CURSOR_MCP_JSON.format(port=port), f"port {port}")
    _write_or_update(root / ".vscode" / "mcp.json", _VSCODE_MCP_JSON.format(port=port), f"port {port}")

    # Merge-safe (not create-only): UPG-11.5 reordered `vectr init --hooks` to
    # write hooks before workspace config in the same run, so settings.json can
    # already exist (hooks-only) by the time we get here — still needs this key.
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    if not settings.exists():
        settings.write_text('{\n  "enableAllProjectMcpServers": true\n}\n')
        print(f"  Created {settings}", file=sys.stderr)
    else:
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not data.get("enableAllProjectMcpServers"):
            data["enableAllProjectMcpServers"] = True
            settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"  Updated {settings} (enableAllProjectMcpServers)", file=sys.stderr)


def _is_vectr_hook_group(group: dict) -> bool:
    """True if a hook group contains a vectr-managed command (for idempotent re-init)."""
    for h in group.get("hooks", []):
        if isinstance(h, dict) and str(h.get("command", "")).startswith("vectr hook"):
            return True
    return False


def _hooks_installed(workspace: str) -> bool:
    """True if `<workspace>/.claude/settings.json` already has a vectr-managed
    hook group (UPG-11.5) — selects CLAUDE.md's hook-aware session-start
    guidance. Never raises: a missing/malformed settings file just means "no
    hooks installed yet"."""
    settings = Path(workspace) / ".claude" / "settings.json"
    if not settings.exists():
        return False
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except Exception:
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if isinstance(group, dict) and _is_vectr_hook_group(group):
                return True
    return False


def _install_hook_group(hooks: dict, event: str, *, command: str, matcher: str | None = None) -> None:
    """Insert (or replace) the vectr-managed hook group for an event, in place.

    Idempotent: any prior vectr group for this event is dropped first, so
    re-running `vectr init --hooks` never duplicates entries, and non-vectr
    hook groups the user added are left untouched.
    """
    groups = hooks.setdefault(event, [])
    groups[:] = [g for g in groups if not _is_vectr_hook_group(g)]
    group: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        group = {"matcher": matcher, **group}
    groups.append(group)


def _write_claude_hooks(workspace: str) -> None:
    """Merge vectr's hook entries into <workspace>/.claude/settings.json (UPG-9.4+).

    Preserves any existing settings (e.g. enableAllProjectMcpServers) and any
    non-vectr hooks. Each vectr hook calls the `vectr hook <event>` subcommand,
    which owns the Claude Code output contract.
    """
    settings = Path(workspace) / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except Exception:
            data = {}  # malformed — overwrite rather than crash init
    hooks = data.setdefault("hooks", {})

    # UPG-9.4 — SessionStart: inject the boot set (directives + high tasks) before turn 1.
    _install_hook_group(hooks, "SessionStart", matcher="startup|resume|clear|compact",
                        command="vectr hook session-start")
    # UPG-9.5 — UserPromptSubmit (no matcher): per-turn semantic recall keyed to the prompt.
    _install_hook_group(hooks, "UserPromptSubmit", command="vectr hook user-prompt-submit")
    # UPG-9.6 — PreToolUse (Edit|Write): surface the gotcha recorded against the file being edited.
    _install_hook_group(hooks, "PreToolUse", matcher="Edit|Write",
                        command="vectr hook pre-tool-use")
    # UPG-9.7 — PreCompact (manual|auto): snapshot working memory before /compact replaces context.
    _install_hook_group(hooks, "PreCompact", matcher="manual|auto",
                        command="vectr hook pre-compact")

    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  Wrote vectr hooks to {settings}", file=sys.stderr)


def _remove_vectr_hooks(workspace: str) -> None:
    """Strip vectr-managed hook groups from .claude/settings.json (for --reset-config).

    Leaves all other settings and any non-vectr hooks intact; drops now-empty
    hook-event lists and an empty `hooks` key.
    """
    settings = Path(workspace) / ".claude" / "settings.json"
    if not settings.exists():
        return
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except Exception:
        return
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    changed = False
    for event in list(hooks.keys()):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if not _is_vectr_hook_group(g)]
        if len(kept) != len(groups):
            changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    if changed:
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"  Removed vectr hooks from {settings}", file=sys.stderr)


def _migrate_legacy_files() -> None:
    """Remove old single-instance PID/port files if they exist."""
    _LEGACY_PID_FILE.unlink(missing_ok=True)
    _LEGACY_PORT_FILE.unlink(missing_ok=True)


def _parse_code_workspace(path: str) -> list[str]:
    """Parse a .code-workspace file and return the absolute folder paths it lists."""
    ws_file = Path(path).resolve()
    data = json.loads(ws_file.read_text(encoding="utf-8"))
    ws_dir = ws_file.parent
    roots: list[str] = []
    for folder in data.get("folders", []):
        folder_path = folder.get("path", "")
        p = Path(folder_path)
        if not p.is_absolute():
            p = ws_dir / p
        roots.append(str(p.resolve()))
    return roots


def _resolve_workspace_roots(args: argparse.Namespace) -> list[str]:
    """Return ordered list of workspace roots from CLI args.

    Priority:
      1. Positional .code-workspace file  →  all folders listed in the file
      2. Positional directory             →  that single directory
      3. --path flags (one or more)       →  those directories in order
      4. VECTR_WORKSPACE env / default .  →  single directory
    """
    ws = getattr(args, "workspace", None)
    if ws:
        p = Path(ws)
        if str(ws).endswith(".code-workspace"):
            return _parse_code_workspace(ws)
        return [str(p.resolve())]
    paths = getattr(args, "paths", None) or []
    if paths:
        return [str(Path(p).resolve()) for p in paths]
    return [str(Path(os.getenv("VECTR_WORKSPACE", ".")).resolve())]


def _code_workspace_file_arg(args: argparse.Namespace) -> str | None:
    """The resolved `.code-workspace` file path the user gave `start`/`restart`,
    or None if they gave a plain directory / --path flags / nothing.

    Recorded in the InstanceRegistry so `vectr status` can show the file the
    instance was actually launched from instead of just its primary folder
    (UPG-CLI-STATUS-MODE).
    """
    ws = getattr(args, "workspace", None)
    if ws and str(ws).endswith(".code-workspace"):
        return str(Path(ws).resolve())
    return None


def _is_explicit_workspace(args: argparse.Namespace) -> bool:
    """True if the user gave an explicit workspace path to start/restart
    (positional `.code-workspace`/directory arg, or one or more --path
    flags), as opposed to defaulting to cwd/VECTR_WORKSPACE.

    UPG-WS-ROOT-MISDETECT: an explicit path must always win over the
    git-toplevel walk-up in `find_workspace_root` — that walk-up is only
    appropriate when the user gave vectr no path to go on at all.
    """
    return bool(getattr(args, "workspace", None)) or bool(getattr(args, "paths", None) or [])


def _warn_if_enclosing_repo(workspace: str) -> None:
    """One-line warning when an explicitly-given workspace path is not
    itself a git repo root but is nested inside an enclosing one
    (UPG-WS-ROOT-MISDETECT). Informational only: the enclosing repo is
    never indexed instead of the path the user gave.
    """
    from integrations.workspace_detect import find_workspace_root

    resolved = str(Path(workspace).resolve())
    enclosing = find_workspace_root(resolved)
    if enclosing != resolved:
        print(
            f"Note: {resolved} is not itself a git repo root; it is nested inside "
            f"the git repo at {enclosing}. Indexing only the given path, not the "
            f"enclosing repo.",
            file=sys.stderr,
        )


def _apply_exclude_args(workspace: str, exclude_entries: list[str]) -> None:
    """Append `--exclude` entries to .vectrignore (shared by `init` and `start`).

    Same repeatable append semantics either way — a directory name, a file
    glob, or a `re:<pattern>` path regex (UPG-EXCLUDE-REGEX). Every `re:`
    entry is validated with re.compile BEFORE anything is written: a bad
    pattern must exit non-zero with a clear message rather than land silently
    in .vectrignore (where the daemon would then have to warn and skip it).
    """
    if not exclude_entries:
        return
    from integrations.workspace_detect import write_vectrignore, VECTRIGNORE_REGEX_PREFIX

    for entry in exclude_entries:
        if entry.startswith(VECTRIGNORE_REGEX_PREFIX):
            pattern_text = entry[len(VECTRIGNORE_REGEX_PREFIX):]
            try:
                re.compile(pattern_text)
            except re.error as exc:
                print(f"Error: invalid regex in --exclude {entry!r}: {exc}", file=sys.stderr)
                sys.exit(1)

    write_vectrignore(workspace, exclude_entries)
    print(f"  Added to .vectrignore: {', '.join(exclude_entries)}", file=sys.stderr)


# Map from normalised language key to the Python module name of its grammar
# (used to derive the pip package name — module_name.replace("_", "-")).
# Only languages declared in SYMBOL_LANGUAGES are relevant; this covers all of them.
_GRAMMAR_MODULE: dict[str, str] = {
    "python":     "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "go":         "tree_sitter_go",
    "rust":       "tree_sitter_rust",
    "java":       "tree_sitter_java",
    "zig":        "tree_sitter_zig",
    "c":          "tree_sitter_c",
    "cpp":        "tree_sitter_cpp",
}


def _preflight_install_grammar(
    lang: str,
    pinned_req: str,
    *,
    _run_pip=None,
) -> bool:
    """Attempt to install one missing grammar package. Returns True on success.

    `pinned_req` is the requirement string (e.g. 'tree-sitter-c==0.24.2').
    `_run_pip` is injectable for tests (defaults to subprocess.run on sys.executable).
    """
    if _run_pip is None:
        def _run_pip(req: str):
            return subprocess.run(
                [sys.executable, "-m", "pip", "install", req],
                capture_output=True, text=True,
            )

    result = _run_pip(pinned_req)
    if result.returncode == 0:
        # Re-verify: clear parser cache so the re-import picks up the newly installed module.
        try:
            from agent.indexer._chunking import _PARSER_CACHE
            _PARSER_CACHE.clear()
        except Exception:
            pass
        from agent.symbol_graph import grammar_available
        if grammar_available(lang):
            print(f"  [vectr] grammar installed: {pinned_req}", file=sys.stderr)
            return True
    return False


def _preflight_grammars(*, _run_pip=None) -> None:
    """Check declared tree-sitter grammars and auto-install any that are missing.

    Called before starting the daemon (cmd_start) and before indexing (cmd_watch)
    so a missing grammar is repaired before the symbol graph is first built.

    Strategy:
    1. Compute missing = SYMBOL_LANGUAGES - available_symbol_languages().
    2. For each missing language, derive the pinned requirement from the installed
       vectr package metadata (importlib.metadata.requires("vectr")). Falls back
       to an unpinned package name if metadata lookup fails.
    3. Attempt pip install. On success, re-verify via grammar_available().
    4. On failure (offline / externally-managed env / permissions): print a clear
       remediation message and CONTINUE — the language is search-only for this run.
       Never crash, never silently add --break-system-packages.
    """
    from agent.symbol_graph import SYMBOL_LANGUAGES, available_symbol_languages

    missing_langs = sorted(SYMBOL_LANGUAGES - available_symbol_languages())
    if not missing_langs:
        return  # all grammars present — nothing to do

    # Derive pinned requirements from installed package metadata.
    pinned: dict[str, str] = {}
    try:
        import importlib.metadata as _meta
        reqs = _meta.requires("vectr") or []
        for req in reqs:
            # req looks like 'tree-sitter-c>=0.24.2' or 'tree-sitter-c==0.24.2'
            # We want the whole string (including the version constraint).
            req_name = req.split(";")[0].strip()  # strip environment markers
            if req_name.lower().startswith("tree-sitter-"):
                # Normalise: tree-sitter-cpp -> tree_sitter_cpp
                module = req_name.split(">=")[0].split("==")[0].split("!=")[0].strip()
                module_key = module.replace("-", "_").lower()
                pinned[module_key] = req_name  # e.g. "tree_sitter_c" -> "tree-sitter-c>=0.24.2"
    except Exception:
        pass  # metadata unavailable — will fall back to bare package names below

    failed: list[str] = []
    for lang in missing_langs:
        module_name = _GRAMMAR_MODULE.get(lang, f"tree_sitter_{lang}")
        pip_name = module_name.replace("_", "-")
        # Use pinned requirement if found, otherwise bare package name.
        req = pinned.get(module_name, pip_name)

        print(
            f"  [vectr] tree-sitter grammar missing for '{lang}' — installing {req!r} ...",
            file=sys.stderr,
        )
        success = _preflight_install_grammar(lang, req, _run_pip=_run_pip)
        if not success:
            failed.append((lang, req))

    if failed:
        lang_list = ", ".join(l for l, _ in failed)
        pip_cmd = " ".join(r for _, r in failed)
        print(
            f"\n[vectr] WARNING: could not auto-install grammar(s) for: {lang_list}",
            file=sys.stderr,
        )
        print(
            f"  locate/trace will be DISABLED for these languages in this session.",
            file=sys.stderr,
        )
        print(
            f"  To fix, run:  pip install {pip_cmd}",
            file=sys.stderr,
        )
        print(
            "  Note: externally-managed environments (Homebrew/system Python) may need "
            "--break-system-packages or use a virtualenv.",
            file=sys.stderr,
        )
        print(
            "  Continuing startup — affected languages are search-only.\n",
            file=sys.stderr,
        )


def _do_start(
    workspace: str,
    port: int,
    ws_hash: str,
    extra_roots: list[str] | None = None,
    memory_only: bool = False,
    search_only: bool = False,
    workspace_explicit: bool = False,
    code_workspace_file: str | None = None,
) -> None:
    if memory_only and search_only:
        raise ValueError("Cannot start vectr in both --memory-only and --search-only mode simultaneously")

    log_dir = Path.home() / ".vectr" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{ws_hash}.log"

    env = {
        **os.environ,
        "VECTR_WORKSPACE": workspace,
        "VECTR_PORT": str(port),
        "VECTR_EXTRA_ROOTS": json.dumps(extra_roots or []),
    }
    if memory_only:
        env["VECTR_MEMORY_ONLY"] = "1"
    if search_only:
        env["VECTR_SEARCH_ONLY"] = "1"
    if workspace_explicit:
        # UPG-WS-ROOT-MISDETECT: tells VectrService to trust `workspace`
        # verbatim instead of walking up to the nearest enclosing .git root.
        env["VECTR_WORKSPACE_EXPLICIT"] = "1"
    vectr_dir = Path(__file__).resolve().parent
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            cwd=str(vectr_dir),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
        )

    _migrate_legacy_files()
    InstanceRegistry().register(
        ws_hash, workspace, port, proc.pid,
        extra_roots=extra_roots, code_workspace_file=code_workspace_file,
    )
    mode_tag = " [memory-only]" if memory_only else (" [search-only]" if search_only else "")

    # UPG-CLI-START-READY-RACE: block until the daemon is actually reachable
    # (or the readiness window elapses) before printing a success message.
    # Before this fix, `start` printed success right after Popen() returned —
    # only proof a process was spawned, not that it had bound its port or
    # finished loading the embedder in its FastAPI lifespan. Running
    # `vectr status` right after that "success" line could still see a
    # connection refused, reading as start having lied.
    if _wait_for_daemon_ready(port, proc.pid):
        print(f"Vectr started{mode_tag} (PID {proc.pid}) on port {port}", file=sys.stderr)
        print(f"Workspace : {workspace}", file=sys.stderr)
        if extra_roots:
            for r in extra_roots:
                print(f"          + {r}", file=sys.stderr)
        print(f"MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)
        print(f"Logs      : {log_path}", file=sys.stderr)
        if memory_only:
            print(
                f"Mode      : memory-only (no code indexing/watcher; memory tools + hooks active)",
                file=sys.stderr,
            )
        elif search_only:
            print(
                f"Mode      : search-only (no working-memory layer; search/locate/trace/map active)",
                file=sys.stderr,
            )
        else:
            print(f"Check indexing progress: vectr status --path {workspace}", file=sys.stderr)
    elif _is_pid_alive(proc.pid):
        print(
            f"Vectr started{mode_tag} (PID {proc.pid}) on port {port}, but has not "
            f"started responding yet after {CLI_START_READY_POLL_TIMEOUT_S:.0f}s.",
            file=sys.stderr,
        )
        print(
            "It may still be loading (large workspace / first-run embedding-model "
            "download) — this is not necessarily a failure.",
            file=sys.stderr,
        )
        print(f"Logs      : {log_path}", file=sys.stderr)
        print(f"Poll readiness with: vectr status --path {workspace}", file=sys.stderr)
    else:
        print(
            f"Vectr failed to start: the process exited before becoming ready.",
            file=sys.stderr,
        )
        print(f"Logs      : {log_path}", file=sys.stderr)


def _get_port_for_workspace(workspace: str, fallback: int) -> int:
    entry = InstanceRegistry().get(workspace_hash(workspace))
    return entry["port"] if entry is not None else fallback


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    memory_only = getattr(args, "memory_only", False)
    search_only = getattr(args, "search_only", False)
    if memory_only and search_only:
        print("Error: --memory-only and --search-only are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    roots = _resolve_workspace_roots(args)
    workspace = roots[0]
    extra_roots = roots[1:]
    ws_hash = workspace_hash(workspace)
    preferred_port = args.port
    explicit = _is_explicit_workspace(args)
    if explicit:
        _warn_if_enclosing_repo(workspace)

    # write user-defined exclusions to .vectrignore before indexing starts
    _apply_exclude_args(workspace, getattr(args, "exclude", None) or [])

    registry = InstanceRegistry()
    registry.prune_dead()

    entry = registry.get(ws_hash)
    if entry is not None and _is_pid_alive(entry["pid"]):
        port = entry["port"]
        for root in roots:
            _maybe_write_workspace_config(root, port, args, search_only=search_only)
        print("Vectr is already running for this workspace.", file=sys.stderr)
        print(f"  Workspace : {workspace}", file=sys.stderr)
        print(f"  Port      : {port}", file=sys.stderr)
        print(f"  MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)
        return

    port = registry.find_free_port(ws_hash, preferred_port)
    for root in roots:
        _maybe_write_workspace_config(root, port, args, search_only=search_only)
    if not memory_only:
        _preflight_grammars()
    _do_start(
        workspace, port, ws_hash, extra_roots=extra_roots,
        memory_only=memory_only, search_only=search_only, workspace_explicit=explicit,
        code_workspace_file=_code_workspace_file_arg(args),
    )


def cmd_index(args: argparse.Namespace) -> None:
    import httpx

    workspace = str(Path(args.path).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    try:
        resp = httpx.post(
            f"{_api_base(port)}/v1/index",
            json={"path": workspace, "force": args.force},
            timeout=600,
        )
        resp.raise_for_status()
        data = resp.json()
        # UPG-CLI-SMALL-UX: human-readable text, matching every sibling
        # subcommand, instead of a raw json.dumps of IndexResponse. `model`
        # (the embedding model name) is dropped here — it answers "which
        # model did this?", not "how did indexing go?"; still present on
        # the REST response for callers that do want it.
        print(f"Indexed {data['indexed_files']} files, {data['total_chunks']} chunks "
              f"in {data['processing_ms']}ms.")
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        _handle_daemon_call_error(exc, port)


def cmd_search(args: argparse.Namespace) -> None:
    import httpx

    workspace = str(Path(os.getenv("VECTR_WORKSPACE", ".")).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    payload: dict = {"query": args.query, "n_results": args.n}
    if args.language:
        payload["language"] = args.language
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/search", json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            print("No results found.", file=sys.stderr)
            return
        # UPG-CLI-SEARCH-FLOOR: `low_confidence` is an EXISTING signal — the
        # MCP surface has rendered it as a banner since UPG-NOTFOUND-FLOOR;
        # the CLI silently dropped it, so a nonsense query printed 10
        # formatted results indistinguishable from a real hit. Render the
        # same signal here, in CLI-appropriate wording (no `vectr_locate`
        # reference — no such subcommand exists).
        if data.get("low_confidence"):
            from agent.config import NOTFOUND_FLOOR_BANNER_CLI
            print(f"\n--- Low confidence ---\n{NOTFOUND_FLOOR_BANNER_CLI}", file=sys.stderr)
        for i, r in enumerate(results, 1):
            print(f"\n[{i}] {r['file']}  lines {r['lines']}  score {r['score']:.3f}")
            if r["symbol"]:
                print(f"    {r['symbol']}  ({r['language']})")
            print()
            print(r["content"][:1000])
        print(f"\n— {data['query_time_ms']}ms  {data['chunks_searched']} chunks searched", file=sys.stderr)
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        _handle_daemon_call_error(exc, port)


def cmd_fetch(args: argparse.Namespace) -> None:
    """Re-fetch one or more chunks by exact id, verbatim (UPG-CTX-EVICT).

    Shell path to the same deterministic re-fetch surface as the MCP
    `vectr_fetch` tool and `POST /v1/fetch` — restores a chunk shown in an
    earlier `vectr search`/`vectr_locate`/`vectr_trace` result after it has
    left context, with no re-search or file re-read.
    """
    import httpx

    workspace = str(Path(os.getenv("VECTR_WORKSPACE", ".")).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/fetch", json={"ids": args.ids}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("results", []):
            if not entry.get("found"):
                print(f"\n[{entry['id']}] not found", file=sys.stderr)
                continue
            print(f"\n[{entry['id']}]  {entry['file_path']}  lines {entry['lines']}")
            if entry.get("symbol"):
                print(f"    {entry['symbol']}  ({entry['language']})")
            print()
            print(entry["content"])
        if data.get("note"):
            print(f"\n{data['note']}", file=sys.stderr)
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        _handle_daemon_call_error(exc, port)


def cmd_remember(args: argparse.Namespace) -> None:
    """Store a working-memory note via the workspace daemon (UPG-9.1).

    Gives `command`-type hooks (and humans) a shell path to the note store,
    mirroring the MCP `vectr_remember` tool.
    """
    import httpx

    workspace = str(Path(args.path).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    payload: dict = {"content": args.content, "priority": args.priority}
    if getattr(args, "kind", None):
        payload["kind"] = args.kind
    if args.tags:
        payload["tags"] = args.tags
    if getattr(args, "title", None):
        payload["title"] = args.title
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/remember", json=payload, timeout=30)
        resp.raise_for_status()
        print(resp.json().get("message", "Stored note."))
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        _handle_daemon_call_error(exc, port)


def cmd_recall(args: argparse.Namespace) -> None:
    """Print recalled working-memory notes to stdout (UPG-9.1).

    This is the human-facing subcommand — `vectr recall` at a shell prompt or
    in a script — and follows the same error contract as every sibling
    subcommand (daemon down or declining the request -> stderr message,
    exit 1). It is NOT the hook path: hook entries written by
    `_write_claude_hooks` invoke `vectr hook <event>` (see cmd_hook), which
    resolves its own daemon connection via `_fetch_recall` and never raises
    regardless of daemon state — that resilience lives entirely in cmd_hook,
    not here, so a down daemon can never look like a successful recall to a
    script that shells out to this command (UPG-CLI-RECALL-EXITCODE).
    """
    import httpx

    workspace = str(Path(args.path).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    payload: dict = {"limit": args.limit}
    if getattr(args, "boot", False):
        # Boot mode ignores all filters server-side; send only the flag.
        payload = {"boot": True}
    else:
        if getattr(args, "note_id", None) is not None:
            # Single-note expand path — send only the note_id, detail=full.
            payload = {"note_id": args.note_id, "detail": "full"}
        else:
            if args.query:
                payload["query"] = args.query
            if args.tags:
                payload["tags"] = args.tags
            if args.priority:
                payload["priority"] = args.priority
            if getattr(args, "kind", None):
                payload["kind"] = args.kind
            if getattr(args, "min_similarity", None) is not None:
                payload["min_similarity"] = args.min_similarity
            if getattr(args, "max_age_days", None) is not None:
                payload["max_age_days"] = args.max_age_days
            if getattr(args, "sort_by", None):
                payload["sort_by"] = args.sort_by
            detail = getattr(args, "detail", "index") or "index"
            payload["detail"] = detail
    # UPG-CLI-RECALL-HINT: this is the human terminal surface — the response's
    # expand hint must be the real `vectr recall --id N` shell form, not the
    # MCP tool-call form the daemon renders by default.
    payload["surface"] = "cli"
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/recall", json=payload, timeout=30)
        resp.raise_for_status()
        notes = resp.json().get("notes", "")
        if notes:
            print(notes)
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        _handle_daemon_call_error(exc, port)


def _fetch_recall(port: int, payload: dict) -> str:
    """POST /v1/recall and return the notes text, or '' on ANY failure.

    Never raises — this feeds harness-injected hook context and must not break
    the session if the daemon is down, slow, or returns an error.
    """
    import httpx
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/recall", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("notes", "") or ""
    except Exception:
        return ""


def _post_snapshot(port: int, label: str) -> bool:
    """POST /v1/snapshot; True on success, False on any failure (never raises).

    Used by the PreCompact hook to seal working memory before context is
    replaced — a snapshot failure must never block compaction.
    """
    import httpx
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/snapshot", json={"label": label}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception:
        return False


def _read_hook_stdin() -> dict:
    """Read the Claude Code hook event JSON from stdin; {} if absent/invalid."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _emit_hook_context(event_name: str, text: str) -> None:
    """Print the Claude Code additionalContext envelope — only when there's text.

    Emitting nothing (instead of an empty envelope) means a fresh workspace
    injects nothing rather than noise. SessionStart/UserPromptSubmit injections
    are prefixed with a one-line notice (UPG-11.5) so the model doesn't also
    self-call vectr_recall for notes it was just handed.
    """
    if not text.strip():
        return
    if event_name in _HOOK_EVENTS_ANNOUNCE_INJECTION:
        text = f"{_HOOK_NO_DOUBLE_RECALL_LINE}\n\n{text}"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }))


def _resolve_hook_instance(cwd: str) -> dict | None:
    """Find the running daemon serving `cwd`, or None.

    Multi-instance-safe with NO hardcoded port: the InstanceRegistry keys each
    `vectr start` workspace by its resolved path, so different folders resolve
    to different ports purely from cwd. We try the exact cwd first, then walk up
    parent directories so a hook fired from a subdirectory still finds the
    enclosing registered workspace. We never fall back to a default port — that
    could belong to an unrelated workspace and leak its memory into this session.
    """
    registry = InstanceRegistry()
    here = Path(cwd).resolve()
    for d in (here, *here.parents):
        entry = registry.get(workspace_hash(str(d)))
        if entry is not None:
            return entry
    return None


def cmd_hook(args: argparse.Namespace) -> None:
    """Emit Claude Code hook output for harness-injected vectr memory (UPG-9.4+).

    Invoked by the hook entries that `vectr init --hooks` writes — not meant to
    be called by hand. Resolves the workspace from the event's cwd (Claude runs
    hooks at the project root), then injects the right memory for the event.
    ALWAYS exits 0 and never raises: a hook must never break the session.
    """
    try:
        event = _read_hook_stdin()
        cwd = event.get("cwd") or os.getcwd()
        entry = _resolve_hook_instance(cwd)
        if entry is None:
            return  # no daemon serves this workspace → inject nothing
        port = entry["port"]

        if args.hook_event == "session-start":
            # Unconditional boot set: directives + high-priority tasks (UPG-9.2),
            # the MEMORY.md equivalent — present before turn 1, zero model agency.
            # detail is NOT sent for boot=True because the service renders directives
            # at full and tasks at index automatically in the boot path.
            notes = _fetch_recall(port, {"boot": True})
            _emit_hook_context("SessionStart", notes)

        elif args.hook_event == "user-prompt-submit":
            # Per-turn semantic recall (UPG-9.5): recall notes keyed to THIS prompt
            # and inject them before the model sees it. The relevance cutoff
            # (UPG-5.1) keeps an off-topic prompt from injecting anything.
            # detail="index" keeps the injected context token-bounded (UPG-RECALL-HIERARCHY).
            prompt = (event.get("prompt") or "").strip()
            if not prompt:
                return
            limit = int(os.getenv("VECTR_HOOK_RECALL_LIMIT", str(_HOOK_RECALL_LIMIT)))
            min_sim = float(os.getenv("VECTR_HOOK_MIN_SIMILARITY", str(_HOOK_MIN_SIMILARITY)))
            notes = _fetch_recall(port, {
                "query": prompt, "limit": limit, "min_similarity": min_sim, "detail": "index",
            })
            _emit_hook_context("UserPromptSubmit", notes)

        elif args.hook_event == "pre-tool-use":
            # Gotcha injection (UPG-9.6): about to Edit/Write a file — surface any
            # caveat recorded against THAT file, at the moment of the edit. Static
            # .claude/rules path-scoping can't do this; the gotcha is accrued + semantic.
            file_path = ((event.get("tool_input") or {}).get("file_path") or "").strip()
            if not file_path:
                return
            notes = _fetch_recall(port, {"file_path": file_path, "kind": "gotcha"})
            _emit_hook_context("PreToolUse", notes)

        elif args.hook_event == "pre-compact":
            # Seal working memory before /compact replaces the conversation (UPG-9.7).
            # No context is emitted — compaction discards it anyway; the boot set is
            # re-injected afterwards by the SessionStart `compact` matcher (UPG-9.4).
            trigger = (event.get("trigger") or "manual").strip() or "manual"
            label = f"pre-compact-{trigger}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            _post_snapshot(port, label)
    except Exception:
        pass  # hook safety: never propagate


def _workspace_display_lines(entry: dict, fallback_workspace: str, label: str) -> list[str]:
    """Format the "Workspace" line(s) for `status` output.

    Shows the originating `.code-workspace` file when the instance was
    started from one (recorded in the registry at `start`/`restart` time),
    otherwise the primary folder plus any extra roots — never just the
    primary folder alone when there's more to the workspace than that
    (UPG-CLI-STATUS-MODE).
    """
    code_workspace_file = (entry or {}).get("code_workspace_file")
    primary = (entry or {}).get("workspace") or fallback_workspace
    if code_workspace_file:
        return [f"{label} : {code_workspace_file}"]
    lines = [f"{label} : {primary}"]
    for extra in (entry or {}).get("extra_roots") or []:
        lines.append(f"{' ' * len(label)}   + {extra}")
    return lines


def _mode_and_index_lines(
    data: dict, files_label: str, chunks_label: str, last_indexed_label: str | None,
    mode_label: str = "Mode         ",
) -> list[str]:
    """Format the Mode + indexed-files/chunks/last-indexed lines.

    In memory-only/search-only mode, indexing never runs in this process —
    `indexed_files` reflects only files this process has walked (0, since
    the startup walk is skipped), while `total_chunks` reads the persisted
    index and can be nonzero from an earlier full-mode run. Framing both as
    if they were live counts is misleading (UPG-CLI-STATUS-MODE): the mode
    line makes the gap self-explanatory, and the row wording says the
    figures are what's persisted, not what this run has done.
    """
    mode = data.get("mode", "full")
    lines = [f"{mode_label} : {mode}"]
    if mode == "full":
        lines.append(f"{files_label} : {data['indexed_files']}")
        lines.append(f"{chunks_label} : {data['total_chunks']}")
        if last_indexed_label:
            lines.append(f"{last_indexed_label} : {data['last_indexed']}")
    else:
        lines.append(
            f"{chunks_label} : {data['total_chunks']} "
            f"(persisted from the last full index; indexing is disabled in {mode} mode)"
        )
    return lines


def cmd_status(args: argparse.Namespace) -> None:
    import httpx

    registry = InstanceRegistry()

    if getattr(args, "all", False):
        registry.prune_dead()
        instances = registry.list_all()
        if not instances:
            print("No running Vectr instances.")
            return
        for entry in instances.values():
            port = entry["port"]
            print()
            for line in _workspace_display_lines(entry, entry["workspace"], "Workspace"):
                print(line)
            print(f"Port      : {port}")
            print(f"PID       : {entry['pid']}")
            print(f"Started   : {entry.get('started_at', 'unknown')}")
            try:
                resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=2)
                resp.raise_for_status()
                d = resp.json()
                for line in _mode_and_index_lines(d, "Files    ", "Chunks   ", None, mode_label="Mode     "):
                    print(line)
            except httpx.ConnectError:
                print("  (not listening — not running)")
            except httpx.HTTPError:
                print("  (listening, but not responding yet — still starting up)")
        return

    workspace = str(Path(args.path).resolve())
    entry = registry.get(workspace_hash(workspace))
    port = _get_port_for_workspace(workspace, args.port)
    try:
        resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for line in _workspace_display_lines(entry, data["workspace_root"], "Workspace    "):
            print(line)
        for line in _mode_and_index_lines(
            data, "Indexed files", "Total chunks ", "Last indexed ", mode_label="Mode         ",
        ):
            print(line)
        print(f"Embed model   : {data['embed_model']}")
    except httpx.ConnectError:
        # UPG-CLI-START-READY-RACE: nothing is listening on this port at all
        # — genuinely not running, as opposed to the case below.
        print(f"Vectr is not listening on port {port} (not running). Run: vectr start", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"Vectr is listening on port {port} but returned an error: {_daemon_error_detail(exc)}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError:
        # Connection accepted but the request didn't complete (timeout,
        # dropped connection, ...) — the daemon is listening but not yet
        # ready to serve requests, e.g. still inside FastAPI lifespan
        # startup loading the embedder. Distinct from "not running": the
        # process exists, it just isn't answering yet.
        print(
            f"Vectr is listening on port {port} but is not responding yet "
            f"(still starting up). Try again in a few seconds.",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_stop(args: argparse.Namespace) -> None:
    registry = InstanceRegistry()

    if getattr(args, "all", False):
        instances = registry.list_all()
        if not instances:
            print("No running Vectr instances.", file=sys.stderr)
            return
        for ws_hash, entry in list(instances.items()):
            pid = entry["pid"]
            print(f"Stopping {entry['workspace']} (PID {pid})...", file=sys.stderr)
            _stop_server(pid)
            registry.unregister(ws_hash)
            print(f"  Stopped PID {pid}")
        return

    workspace = str(Path(args.path).resolve())
    ws_hash = workspace_hash(workspace)
    registry.prune_dead()
    entry = registry.get(ws_hash)
    if entry is None:
        print(f"No registered instance for workspace: {workspace}", file=sys.stderr)
        return
    pid = entry["pid"]
    _stop_server(pid)
    registry.unregister(ws_hash)
    print(f"Vectr stopped (PID {pid})")


def cmd_restart(args: argparse.Namespace) -> None:
    memory_only = getattr(args, "memory_only", False)
    search_only = getattr(args, "search_only", False)
    if memory_only and search_only:
        print("Error: --memory-only and --search-only are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    roots = _resolve_workspace_roots(args)
    workspace = roots[0]
    extra_roots = roots[1:]
    ws_hash = workspace_hash(workspace)
    preferred_port = args.port
    explicit = _is_explicit_workspace(args)
    if explicit:
        _warn_if_enclosing_repo(workspace)

    registry = InstanceRegistry()
    entry = registry.get(ws_hash)
    if entry is not None:
        pid = entry["pid"]
        print(f"Stopping PID {pid}...", file=sys.stderr)
        _stop_server(pid)
        registry.unregister(ws_hash)

    port = registry.find_free_port(ws_hash, preferred_port)
    for root in roots:
        _maybe_write_workspace_config(root, port, args, search_only=search_only)
    _do_start(
        workspace, port, ws_hash, extra_roots=extra_roots,
        memory_only=memory_only, search_only=search_only, workspace_explicit=explicit,
        code_workspace_file=_code_workspace_file_arg(args),
    )


def cmd_forget(args: argparse.Namespace) -> None:
    import httpx

    # --all clears notes across ALL workspaces directly via SQLite,
    # bypassing the running server (server may be down, or multiple instances).
    if getattr(args, "all", False):
        from agent.working_context_store import WorkingContextStore
        import glob
        cache_root = Path.home() / ".cache" / "vectr" / "db"
        db_files = list(cache_root.glob("*/working_context.sqlite"))
        total = 0
        for db_file in db_files:
            store = WorkingContextStore(str(db_file.parent))
            total += store.forget_all_workspaces()
        print(f"Deleted {total} working-memory notes across {len(db_files)} workspace databases.")
        return

    workspace = str(Path(args.path).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/memory/clear", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"Deleted {data['deleted']} working-memory notes for {workspace}")
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        _handle_daemon_call_error(exc, port)


def cmd_init(args: argparse.Namespace) -> None:
    workspace = str(Path(args.path).resolve())

    if getattr(args, "reset_config", False):
        root = Path(workspace)
        for _rel in ("CLAUDE.md", *_IDE_CONFIG_APPEND_ONLY):
            _remove_vectr_block(root / _rel)
        _remove_vectr_block(root / ".github" / "copilot-instructions.md")
        cursor_mdc = root / ".cursor" / "rules" / "vectr.mdc"
        if cursor_mdc.exists():
            cursor_mdc.unlink()
            print(f"  Deleted {cursor_mdc}", file=sys.stderr)
        _remove_vectr_hooks(workspace)
        print(f"Vectr config reset for: {workspace}", file=sys.stderr)
        return

    entry = InstanceRegistry().get(workspace_hash(workspace))
    port_is_provisional = entry is None
    port = entry["port"] if entry is not None else int(os.getenv("VECTR_PORT", "8765"))
    if port_is_provisional:
        # UPG-CLI-SMALL-UX: no instance is registered for this workspace yet,
        # so this port is a guess (VECTR_PORT or the 8765 fallback), not a
        # confirmed bound port — a real collision target if something else
        # already owns it. `vectr start` always finds an actually-free port
        # and overwrites these same files with it (see the CLI table entry
        # for `vectr init`), so this is silently self-correcting, but a user
        # who never runs `start` right after `init` would otherwise have no
        # indication the written port is unconfirmed.
        print(
            f"  Note: no vectr instance is running for this workspace yet — "
            f"MCP config files below use provisional port {port}. "
            f"`vectr start` will assign the real free port and correct them "
            f"automatically if it differs.",
            file=sys.stderr,
        )

    # Search-only mode (UPG-SEARCH-ONLY-MODE) has no working-memory layer to
    # inject — detected live from the running daemon (mode is a daemon
    # property, not a static workspace marker) rather than a CLI flag on init.
    search_only = _get_daemon_mode(port) == "search-only"

    # Hooks are written BEFORE the workspace config (UPG-11.5): CLAUDE.md's
    # session-start guidance is hook-aware, detected by reading back
    # .claude/settings.json — so within a single `vectr init --hooks` run,
    # the hooks must already be on disk when _write_workspace_config runs,
    # or CLAUDE.md would ship the pre-hooks (double-recall) guidance.
    if getattr(args, "hooks", False):
        if search_only:
            print(
                "Warning: this workspace's vectr daemon runs in search-only mode — "
                "there is no working-memory layer for hooks to inject notes from. "
                "Skipping hook installation. Restart without --search-only to use hooks.",
                file=sys.stderr,
            )
        else:
            _write_claude_hooks(workspace)

    _maybe_write_workspace_config(workspace, port, args, search_only=search_only)

    # write user-defined exclusions to .vectrignore
    _apply_exclude_args(workspace, getattr(args, "exclude", None) or [])

    # write style override if --style is specified
    if getattr(args, "style", None):
        style = args.style
        if style not in ("additive", "directed", "memory-only"):
            print(f"Error: --style must be one of: additive, directed, memory-only", file=sys.stderr)
            sys.exit(1)
        style_dir = Path(workspace) / ".vectr"
        style_dir.mkdir(parents=True, exist_ok=True)
        (style_dir / "style").write_text(style, encoding="utf-8")
        print(f"  Instruction style set: {style}", file=sys.stderr)

    print(f"Workspace configured: {workspace}", file=sys.stderr)
    print(f"  Run 'vectr start --path {workspace}' to index and start the server.", file=sys.stderr)


def cmd_watch(args: argparse.Namespace) -> None:
    """Index workspace(s) and start filesystem watcher without launching the MCP server."""
    import hashlib
    from agent.config import EMBEDDING_DEFAULT_MODEL
    from agent.indexer import CodeIndexer
    from agent.watcher import CodeWatcher
    from integrations.workspace_detect import find_workspace_root

    _preflight_grammars()

    roots = _resolve_workspace_roots(args)
    # UPG-WS-ROOT-MISDETECT: an explicit path/positional workspace arg wins
    # verbatim; the git-toplevel walk-up only applies when none was given.
    if _is_explicit_workspace(args):
        workspace = str(Path(roots[0]).resolve())
        _warn_if_enclosing_repo(workspace)
    else:
        workspace = find_workspace_root(roots[0])
    extra_roots = roots[1:]
    embed_model = os.getenv("VECTR_EMBED_MODEL", EMBEDDING_DEFAULT_MODEL)

    # Use same db layout as VectrService so a later `vectr start` shares the index.
    db_hash = hashlib.md5(workspace.encode()).hexdigest()[:12]
    db_dir = Path.home() / ".cache" / "vectr" / db_hash
    db_dir.mkdir(parents=True, exist_ok=True)

    indexer = CodeIndexer(workspace, embed_model=embed_model, db_path=str(db_dir / "chroma"),
                          extra_roots=extra_roots)
    watcher = CodeWatcher(indexer)

    all_roots_str = ", ".join([workspace] + extra_roots)
    print(f"Indexing {all_roots_str} ...", file=sys.stderr)
    files, chunks = indexer.index_workspace()
    print(f"  Indexed {files} files, {chunks} chunks", file=sys.stderr)
    print(f"Watching for changes. Press Ctrl+C to stop.", file=sys.stderr)
    print(f"  Run 'vectr start --path {workspace}' to also serve MCP.", file=sys.stderr)

    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        print("\nWatcher stopped.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

# Shared --exclude help text (init and start accept identical entry forms —
# UPG-EXCLUDE-REGEX). PATTERN accepts a bare directory name, a file glob
# (e.g. *.generated.py), or a `re:<pattern>` path regex matched against the
# workspace-relative path (e.g. re:legacy/.* or re:.*_(backup|old)\.\w+$).
_EXCLUDE_HELP = (
    "Append a directory name, file glob, or `re:<pattern>` path regex to "
    ".vectrignore (repeatable). Examples: --exclude vendor "
    "--exclude '*.generated.py' --exclude 're:legacy/.*'"
)

# Shared IDE-config-writes disclosure (UPG-CLI-WRITES-DISCLOSURE): start,
# restart, and init all write IDE integration files into the workspace root
# as a side effect, with no prior --help mention and no opt-out.
_IDE_CONFIG_WRITES_DISCLOSURE = (
    "On first run for a workspace, vectr writes IDE integration files into "
    "the workspace root so an AI editor can auto-discover the MCP server: "
    "CLAUDE.md, .cursor/rules/vectr.mdc, .mcp.json, .cursor/mcp.json, "
    ".vscode/mcp.json, .claude/settings.json, and .vectrignore (default "
    "excludes) — 7 files. Files for other editors/agents (AGENTS.md, "
    ".cursorrules, GEMINI.md, CODEX.md, .github/copilot-instructions.md) "
    "only get a vectr guidance block appended if the file already exists — "
    "they are never created from scratch. Pass --no-ide-config to skip all "
    "of this; the choice persists at .vectr/ide_config for future start/"
    "restart/init calls on this workspace (delete that file to re-enable). "
    "See `vectr init --reset-config` to remove already-written blocks."
)

_NO_IDE_CONFIG_HELP = (
    "Skip writing IDE integration config files for this workspace and "
    "persist that choice (see the command description above). Delete "
    ".vectr/ide_config to re-enable on a later run."
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vectr",
        description=(
            "Vectr: persistent external memory and semantic code search for AI coding "
            "agents. Indexes a codebase for fast search/symbol/call-graph lookups over "
            "MCP (search/locate/trace/map), and provides working memory (remember/"
            "recall/snapshot) that survives context compaction and session restarts. "
            "This CLI runs the daemon (start/stop/status) and also exposes search/"
            "remember/recall directly from a shell."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    _default_path = os.getenv("VECTR_WORKSPACE", ".")
    _default_port = int(os.getenv("VECTR_PORT", "8765"))

    p_start = sub.add_parser(
        "start",
        help="Start the Vectr daemon and index the workspace. "
             "Accepts a .code-workspace file, one or more --path flags, or defaults to cwd.",
        description=_IDE_CONFIG_WRITES_DISCLOSURE,
    )
    p_start.add_argument(
        "workspace", nargs="?", default=None,
        help="Path to a .code-workspace file or a single workspace directory",
    )
    p_start.add_argument(
        "--path", action="append", dest="paths", metavar="DIR",
        help="Workspace root to index (repeatable for multi-root). "
             "Example: vectr start --path dir1 --path dir2",
    )
    p_start.add_argument("--port", type=int, default=_default_port)
    p_start.add_argument(
        "--exclude", action="append", metavar="PATTERN", dest="exclude",
        help=_EXCLUDE_HELP,
    )
    p_start.add_argument(
        "--memory-only",
        action="store_true",
        default=False,
        dest="memory_only",
        help=(
            "Run the daemon for working memory + Claude Code hooks WITHOUT "
            "indexing, embedding, or watching the codebase. "
            "Memory tools (remember/recall/snapshot) and hooks remain active; "
            "search/locate/trace are disabled. Useful on actively-edited projects "
            "where the full code index + watcher cause performance issues."
        ),
    )
    p_start.add_argument(
        "--search-only",
        action="store_true",
        default=False,
        dest="search_only",
        help=(
            "Run the daemon for semantic search + symbol graph + codebase map "
            "WITHOUT the working-memory layer — no notes DB is created for this "
            "workspace, and remember/recall/forget/snapshot are disabled. "
            "Indexing and the file watcher run normally. Useful for read-only "
            "consumers of an indexed codebase (reviewers, CI, a shared search "
            "server) where nothing should be written to a per-workspace note "
            "store. Mutually exclusive with --memory-only."
        ),
    )
    p_start.add_argument(
        "--no-ide-config",
        action="store_true",
        default=False,
        dest="no_ide_config",
        help=_NO_IDE_CONFIG_HELP,
    )

    p_stop = sub.add_parser("stop", help="Stop the daemon for a workspace")
    p_stop.add_argument("--path", default=_default_path)
    p_stop.add_argument("--all", action="store_true", help="Stop all running instances")

    p_restart = sub.add_parser(
        "restart",
        help="Stop and restart the daemon for a workspace",
        description=_IDE_CONFIG_WRITES_DISCLOSURE,
    )
    p_restart.add_argument(
        "workspace", nargs="?", default=None,
        help="Path to a .code-workspace file or a single workspace directory",
    )
    p_restart.add_argument("--path", action="append", dest="paths", metavar="DIR")
    p_restart.add_argument("--port", type=int, default=_default_port)
    p_restart.add_argument(
        "--memory-only",
        action="store_true",
        default=False,
        dest="memory_only",
        help="Restart in memory-only mode (no indexing/watcher; see vectr start --memory-only).",
    )
    p_restart.add_argument(
        "--search-only",
        action="store_true",
        default=False,
        dest="search_only",
        help="Restart in search-only mode (no working-memory layer; see vectr start --search-only).",
    )
    p_restart.add_argument(
        "--no-ide-config",
        action="store_true",
        default=False,
        dest="no_ide_config",
        help=_NO_IDE_CONFIG_HELP,
    )

    p_forget = sub.add_parser("forget", help="Delete working-memory notes for a workspace")
    p_forget.add_argument("--path", default=_default_path)
    p_forget.add_argument("--port", type=int, default=_default_port)
    p_forget.add_argument(
        "--all", action="store_true",
        help="Delete notes across ALL workspaces (operates directly on SQLite, no server needed)",
    )

    p_watch = sub.add_parser("watch", help="Index workspace(s) and watch for changes (no MCP server)")
    p_watch.add_argument("workspace", nargs="?", default=None,
        help="Path to a .code-workspace file or a single workspace directory")
    p_watch.add_argument("--path", action="append", dest="paths", metavar="DIR")

    p_init = sub.add_parser(
        "init",
        help="Write CLAUDE.md and .mcp.json to a workspace (no server)",
        description=_IDE_CONFIG_WRITES_DISCLOSURE,
    )
    p_init.add_argument("--path", default=_default_path)
    p_init.add_argument(
        "--exclude", action="append", metavar="PATTERN", dest="exclude",
        help=_EXCLUDE_HELP,
    )
    p_init.add_argument(
        "--no-ide-config",
        action="store_true",
        default=False,
        dest="no_ide_config",
        help=_NO_IDE_CONFIG_HELP,
    )
    p_init.add_argument(
        "--style",
        choices=["additive", "directed", "memory-only"],
        default=None,
        help="Override adaptive instruction style (T14). Stored in .vectr/style.",
    )
    p_init.add_argument(
        "--reset-config",
        action="store_true",
        default=False,
        dest="reset_config",
        help="Remove all vectr blocks from IDE config files in the workspace.",
    )
    p_init.add_argument(
        "--hooks",
        action="store_true",
        default=False,
        help="Also write Claude Code hook entries (.claude/settings.json) for "
             "harness-injected vectr memory (SessionStart boot recall, etc.).",
    )

    p_hook = sub.add_parser(
        "hook",
        help="Emit Claude Code hook output (invoked by `vectr init --hooks` entries; not called directly)",
    )
    p_hook.add_argument("hook_event",
                        choices=["session-start", "user-prompt-submit", "pre-tool-use", "pre-compact"],
                        help="Which hook event to emit output for")

    p_index = sub.add_parser("index", help="(Re)index a directory or file")
    p_index.add_argument("--path", default=_default_path)
    p_index.add_argument("--port", type=int, default=_default_port)
    p_index.add_argument("--force", action="store_true", help="Force full re-index")

    p_search = sub.add_parser(
        "search",
        help="Semantic search",
        description=(
            "Semantic search over the indexed codebase. Each result's `score` "
            "is an absolute per-(query, result) relevance value in [0, 1], not "
            "a rank-derived percentile — it can legitimately be low even for "
            "the best available match if nothing in the codebase is a strong "
            "fit. A '--- Low confidence ---' banner is printed above the "
            "results when the query itself has no lexical anchor anywhere in "
            "the index, or the top result's own relevance score is below the "
            "configured floor (`ranking.notfound_floor.*` in config.yaml) — "
            "results are still shown in full even when it fires."
        ),
    )
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--n", type=int, default=10)
    p_search.add_argument("--language", help="Filter by language")
    p_search.add_argument("--port", type=int, default=_default_port)

    p_fetch = sub.add_parser(
        "fetch",
        help="Re-fetch chunks by exact id, verbatim",
        description=(
            "Deterministic re-fetch by chunk id — restores a chunk shown in an "
            "earlier search/locate/trace result after it has left context, with "
            "no re-search or file re-read. Ids not present in the index are "
            "reported as not found (the file likely changed since indexing)."
        ),
    )
    p_fetch.add_argument("ids", nargs="+", help="Chunk id(s), e.g. path/to/file.py:10-42")
    p_fetch.add_argument("--port", type=int, default=_default_port)

    p_remember = sub.add_parser("remember", help="Store a working-memory note (shell path to the note store)")
    p_remember.add_argument("content", help="The note content to store")
    p_remember.add_argument("--tags", action="append", metavar="TAG", help="Topic tag (repeatable)")
    p_remember.add_argument("--priority", choices=["high", "medium", "low"], default="medium")
    p_remember.add_argument("--kind", choices=["directive", "task", "gotcha", "finding", "reference"],
                            default="finding", help="Memory kind (controls injection policy)")
    p_remember.add_argument("--title", default="", help="Short label for index-tier display (optional; derived from first content line if empty)")
    p_remember.add_argument("--path", default=_default_path)
    p_remember.add_argument("--port", type=int, default=_default_port)

    p_recall = sub.add_parser("recall", help="Print recalled working-memory notes to stdout")
    p_recall.add_argument("query", nargs="?", default=None, help="Semantic recall query (optional)")
    p_recall.add_argument("--tags", action="append", metavar="TAG", help="Filter by tag (repeatable)")
    p_recall.add_argument("--priority", choices=["high", "medium", "low"], default=None)
    p_recall.add_argument("--kind", choices=["directive", "task", "gotcha", "finding", "reference"],
                          default=None, help="Filter to one memory kind")
    p_recall.add_argument("--boot", action="store_true",
                          help="Boot mode: unconditional directives + high-priority tasks (for SessionStart hooks)")
    p_recall.add_argument("--min-similarity", type=float, default=None, dest="min_similarity",
                          help="Relevance cutoff [0..1]: drop semantic matches below this cosine similarity")
    p_recall.add_argument("--max-age-days", type=float, default=None, dest="max_age_days",
                          help="Time filter: only return notes created within this many days")
    p_recall.add_argument("--sort-by", choices=["relevance", "recency", "priority"], default="relevance",
                          dest="sort_by", help="Sort order: relevance | recency | priority")
    p_recall.add_argument("--detail", choices=["index", "full"], default="index",
                          help="Detail level: 'index' = one-line summaries (default); 'full' = bodies")
    p_recall.add_argument("--id", type=int, default=None, dest="note_id",
                          help="Expand a single note by ID (returns full body)")
    p_recall.add_argument("--limit", type=int, default=10)
    p_recall.add_argument("--path", default=_default_path)
    p_recall.add_argument("--port", type=int, default=_default_port)

    p_status = sub.add_parser("status", help="Show status for a workspace")
    p_status.add_argument("--path", default=_default_path)
    p_status.add_argument("--port", type=int, default=_default_port)
    p_status.add_argument("--all", action="store_true", help="List all running instances")

    args = parser.parse_args()
    dispatch = {
        "start":   cmd_start,
        "restart": cmd_restart,
        "watch":   cmd_watch,
        "init":    cmd_init,
        "index":   cmd_index,
        "search":  cmd_search,
        "fetch":   cmd_fetch,
        "status":  cmd_status,
        "stop":    cmd_stop,
        "forget":  cmd_forget,
        "remember": cmd_remember,
        "recall":  cmd_recall,
        "hook":    cmd_hook,
    }
    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
