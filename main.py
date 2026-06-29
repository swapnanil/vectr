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

from agent.instance_registry import (
    InstanceRegistry,
    _is_pid_alive,
    workspace_hash,
)

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

_CLAUDE_MD = """\
# Vectr — semantic search + reliable working memory

Vectr gives you two capabilities:

- **Semantic search**: find any symbol, pattern, or concept in this codebase by describing it in plain English — faster than grep, without knowing where to look.
- **Working memory**: store findings and recall them in <50ms on demand — whether later in this session, through `/compact`, or in a future session. Saving is a gain, not a risk.

> **Loading the tools first.** Vectr's tools may be deferred behind a tool-search step. If `vectr_search` / `vectr_locate` / `vectr_remember` are not directly callable yet, load them once with `ToolSearch("select:mcp__vectr__vectr_search,mcp__vectr__vectr_locate,mcp__vectr__vectr_remember,mcp__vectr__vectr_status,mcp__vectr__vectr_recall")`, then call them **as tools**. Never run an `mcp__vectr__*` name as a shell/bash command — that is not an executable and always fails.

> **This workspace's working memory IS vectr — not files.** Record every finding, decision, and gotcha with `vectr_remember`. Do **not** write them to scratch `.md` files or a `~/.claude` memory directory: only vectr notes are re-injected automatically after `/compact` and recalled in <50ms — ad-hoc files are not, and they fragment your memory across two places. Any time you are about to "save a note to a file", call `vectr_remember` instead.

## Semantic search — 5 tools

The codebase is fully indexed. One `vectr_search` call returns ranked, relevant code chunks — no grep loops across hundreds of files, no wasted turns reading the wrong files. Use these for all exploration; use Read only to read a specific file that vectr has already pointed you to.

| Tool | Purpose | Example |
|---|---|---|
| `vectr_search("query")` | Semantic search — describe what you're looking for, get ranked code chunks back. Replaces grep + blind file reads. | `vectr_search("workspace lock acquisition and release")` |
| `vectr_locate("SymbolName")` | Symbol graph lookup — name → file:line in one call. Replaces find + grep for definitions. | `vectr_locate("WorkspaceLock")` → `resolver.rs:214` |
| `vectr_trace("symbol")` | Call graph — who calls this symbol, and what does it call. | `vectr_trace("acquire_lock")` |
| `vectr_map()` | Codebase overview — file tree + module summaries. Call once on an unfamiliar repo; follow with `vectr_map_save` if it returns raw metadata. | First visit to an unknown repo |
| `vectr_map_save(summary)` | Save a plain-English codebase summary (~200–350 tokens) as a permanent passport. Only call when `vectr_map` returned raw metadata. | `vectr_map_save("uv is a Rust-based Python package manager…")` |

## Working memory — 7 tools

A note stored with `vectr_remember` is the only finding that survives three things: (1) re-reading the file costs tokens — recalling the note costs almost none; (2) `/compact` replaces the conversation with a summary that loses exact signatures and line numbers — your note does not; (3) a new session starts with zero context — your note is there from turn 1. `vectr_recall` retrieves it in <50ms, verbatim, any time.

**Always available:**

| Tool | Purpose | Example |
|---|---|---|
| `vectr_status()` | Note count + index state. **Always call first at session start.** | `vectr_status()` → `notes_count: 3` → call `vectr_recall` |
| `vectr_remember(content, tags, priority)` | Save a key finding — actual code or pattern, not a file pointer. | `vectr_remember("lock_workspace() at resolver.rs:214 acquires PID-scoped lock; drops on scope exit.", tags=["lock", "resolver"], priority="high")` |
| `vectr_evict_hint()` | Lists retrieved chunks that vectr can re-retrieve in <50ms — no need to re-read those files later. | At exploration → implementation transition |

**Unlocked after your first `vectr_remember` call (or when prior notes exist):**

| Tool | Purpose | Example |
|---|---|---|
| `vectr_recall(query)` | Retrieve notes relevant to your task. Replaces re-reading already-explored files. | `vectr_recall("workspace lock resolution flow")` |
| `vectr_forget(note_id)` | Delete a stale or superseded note by ID. | `vectr_forget("note_abc123")` |
| `vectr_snapshot("label")` | Seal current notes as a named checkpoint. | `vectr_snapshot("lock-cycle-mapped")` |
| `vectr_snapshot_list()` | List saved checkpoints. Use at session start if `vectr_recall` returned nothing useful. | `vectr_snapshot_list()` |

## When to use each capability

**Before calling `vectr_search` on a well-known API or framework:** write out what you already know — function signatures, key types, parameter names — and only call `vectr_search` if genuine gaps remain after that verbalization. Reduces unnecessary search calls 26–40% on familiar codebases.

**At session start (always):** call `vectr_status()` first.
- `notes_count > 0` → prior work on this codebase is saved; call `vectr_recall(query="<your task>")` before opening any files.
- `notes_count == 0` → skip recall and proceed.

**The moment you find a key definition, pattern, or non-obvious detail:** call `vectr_remember(content, tags=[...], priority="high"|"medium"|"low")` — store the actual code block or finding, not a file pointer. Treat every `vectr_search` or `vectr_locate` call as a **pair**: search, then immediately save the key finding before your next retrieval. If `/compact` runs later, the conversation summary loses exact details — your note does not. If a new session starts, your note is the only thing that carries forward. One note now = no re-discovery later.

**Before writing any final output:** call `vectr_remember` at least once with the key type names, entry points, and non-obvious patterns you confirmed. The output file captures what you built; notes capture what you learned — and what you learned is what the next session needs.

**At exploration → implementation transition:** call `vectr_evict_hint()` — lists retrieved chunks that vectr can re-retrieve in <50ms if you need them again. Follow with `vectr_remember` for any synthesized understanding not yet stored.

**If recalled notes already contain what you need:** work from them directly. Use `vectr_search` or Read only to fill genuine gaps.
"""

_MCP_JSON = """\
{{
  "mcpServers": {{
    "vectr": {{
      "type": "http",
      "url": "http://localhost:{port}/mcp"
    }}
  }}
}}
"""

# Cursor omits the "type" key (it infers HTTP from the url scheme)
_CURSOR_MCP_JSON = """\
{{
  "mcpServers": {{
    "vectr": {{
      "url": "http://localhost:{port}/mcp"
    }}
  }}
}}
"""

# VSCode 1.99+ / GitHub Copilot Agent Mode uses "servers" (not "mcpServers")
_VSCODE_MCP_JSON = """\
{{
  "servers": {{
    "vectr": {{
      "type": "http",
      "url": "http://localhost:{port}/mcp"
    }}
  }}
}}
"""

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


def _make_vectr_block() -> str:
    return f"{_VECTR_BLOCK_START}\n{_CLAUDE_MD.rstrip()}\n{_VECTR_BLOCK_END}\n"


def _write_ide_config_merge_safe(path: Path, *, create_if_missing: bool) -> None:
    """Write the vectr guidance block into an IDE config file.

    - File missing + create_if_missing=True  → create file containing just the block.
    - File missing + create_if_missing=False → no-op.
    - File exists, no vectr block            → append block after existing content.
    - File exists, vectr block present       → replace block in-place (idempotent).
    """
    block = _make_vectr_block()

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


def _write_cursor_rules(workspace: str) -> None:
    """Write .cursor/rules/vectr.mdc for Cursor IDE (vectr-owned file, always current)."""
    path = Path(workspace) / ".cursor" / "rules" / "vectr.mdc"
    content = (
        "---\n"
        "description: Vectr tool usage rules for AI-assisted development\n"
        "alwaysApply: true\n"
        "---\n\n"
        f"{_CLAUDE_MD.rstrip()}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if not existed or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        print(f"  {'Updated' if existed else 'Created'} {path}", file=sys.stderr)


def _api_base(port: int) -> str:
    return f"http://localhost:{port}"


def _is_server_alive(port: int, timeout: float = 2.0) -> tuple[bool, str | None]:
    """Return (alive, workspace_root). Non-blocking within timeout."""
    try:
        import httpx
        resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=timeout)
        resp.raise_for_status()
        return True, resp.json().get("workspace_root")
    except Exception:
        return False, None


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


def _write_workspace_config(workspace: str, port: int) -> None:
    """Write per-IDE MCP config files and IDE guidance into the workspace root."""
    root = Path(workspace)

    _write_ide_config_merge_safe(root / "CLAUDE.md", create_if_missing=True)
    for _rel in _IDE_CONFIG_APPEND_ONLY:
        _write_ide_config_merge_safe(root / _rel, create_if_missing=False)
    _write_ide_config_merge_safe(
        root / ".github" / "copilot-instructions.md", create_if_missing=False
    )
    _write_cursor_rules(workspace)

    _write_or_update(root / ".mcp.json", _MCP_JSON.format(port=port), f"port {port}")
    _write_or_update(root / ".cursor" / "mcp.json", _CURSOR_MCP_JSON.format(port=port), f"port {port}")
    _write_or_update(root / ".vscode" / "mcp.json", _VSCODE_MCP_JSON.format(port=port), f"port {port}")

    settings = root / ".claude" / "settings.json"
    if not settings.exists():
        settings.parent.mkdir(exist_ok=True)
        settings.write_text('{\n  "enableAllProjectMcpServers": true\n}\n')
        print(f"  Created {settings}", file=sys.stderr)


def _is_vectr_hook_group(group: dict) -> bool:
    """True if a hook group contains a vectr-managed command (for idempotent re-init)."""
    for h in group.get("hooks", []):
        if isinstance(h, dict) and str(h.get("command", "")).startswith("vectr hook"):
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
) -> None:
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
    InstanceRegistry().register(ws_hash, workspace, port, proc.pid)
    mode_tag = " [memory-only]" if memory_only else ""
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
    else:
        print(f"Check indexing progress: vectr status --path {workspace}", file=sys.stderr)


def _get_port_for_workspace(workspace: str, fallback: int) -> int:
    entry = InstanceRegistry().get(workspace_hash(workspace))
    return entry["port"] if entry is not None else fallback


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    roots = _resolve_workspace_roots(args)
    workspace = roots[0]
    extra_roots = roots[1:]
    ws_hash = workspace_hash(workspace)
    preferred_port = args.port

    registry = InstanceRegistry()
    registry.prune_dead()

    entry = registry.get(ws_hash)
    if entry is not None and _is_pid_alive(entry["pid"]):
        port = entry["port"]
        for root in roots:
            _write_workspace_config(root, port)
        print("Vectr is already running for this workspace.", file=sys.stderr)
        print(f"  Workspace : {workspace}", file=sys.stderr)
        print(f"  Port      : {port}", file=sys.stderr)
        print(f"  MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)
        return

    port = registry.find_free_port(ws_hash, preferred_port)
    for root in roots:
        _write_workspace_config(root, port)
    memory_only = getattr(args, "memory_only", False)
    if not memory_only:
        _preflight_grammars()
    _do_start(workspace, port, ws_hash, extra_roots=extra_roots, memory_only=memory_only)


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
        print(json.dumps(resp.json(), indent=2))
    except httpx.ConnectError:
        print(f"Error: Vectr is not running on port {port}. Run: vectr start", file=sys.stderr)
        sys.exit(1)


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
        for i, r in enumerate(results, 1):
            print(f"\n[{i}] {r['file']}  lines {r['lines']}  score {r['score']:.3f}")
            if r["symbol"]:
                print(f"    {r['symbol']}  ({r['language']})")
            print()
            print(r["content"][:1000])
        print(f"\n— {data['query_time_ms']}ms  {data['chunks_searched']} chunks searched", file=sys.stderr)
    except httpx.ConnectError:
        print(f"Error: Vectr is not running on port {port}. Run: vectr start", file=sys.stderr)
        sys.exit(1)


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
    except httpx.ConnectError:
        print(f"Error: Vectr is not running on port {port}. Run: vectr start", file=sys.stderr)
        sys.exit(1)


def cmd_recall(args: argparse.Namespace) -> None:
    """Print recalled working-memory notes to stdout (UPG-9.1).

    stdout is the field SessionStart / UserPromptSubmit hooks inject into the
    model's context, so this command writes notes to stdout and nothing else.
    It is intentionally resilient: if the daemon is down it emits no notes and
    still exits 0, so a hook that shells out can never break the session.
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
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/recall", json=payload, timeout=30)
        resp.raise_for_status()
        notes = resp.json().get("notes", "")
        if notes:
            print(notes)
    except httpx.ConnectError:
        print(f"Vectr not running on port {port}; no notes recalled.", file=sys.stderr)


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
    injects nothing rather than noise.
    """
    if not text.strip():
        return
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
            print(f"\nWorkspace : {entry['workspace']}")
            print(f"Port      : {port}")
            print(f"PID       : {entry['pid']}")
            print(f"Started   : {entry.get('started_at', 'unknown')}")
            try:
                resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=2)
                resp.raise_for_status()
                d = resp.json()
                print(f"Files     : {d['indexed_files']}")
                print(f"Chunks    : {d['total_chunks']}")
            except Exception:
                print("  (server not responding)")
        return

    workspace = str(Path(args.path).resolve())
    port = _get_port_for_workspace(workspace, args.port)
    try:
        resp = httpx.get(f"{_api_base(port)}/v1/status", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"Workspace     : {data['workspace_root']}")
        print(f"Indexed files : {data['indexed_files']}")
        print(f"Total chunks  : {data['total_chunks']}")
        print(f"Last indexed  : {data['last_indexed']}")
        print(f"Embed model   : {data['embed_model']}")
    except httpx.ConnectError:
        print(f"Vectr is not running on port {port}.", file=sys.stderr)
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
    roots = _resolve_workspace_roots(args)
    workspace = roots[0]
    extra_roots = roots[1:]
    ws_hash = workspace_hash(workspace)
    preferred_port = args.port

    registry = InstanceRegistry()
    entry = registry.get(ws_hash)
    if entry is not None:
        pid = entry["pid"]
        print(f"Stopping PID {pid}...", file=sys.stderr)
        _stop_server(pid)
        registry.unregister(ws_hash)

    port = registry.find_free_port(ws_hash, preferred_port)
    for root in roots:
        _write_workspace_config(root, port)
    memory_only = getattr(args, "memory_only", False)
    _do_start(workspace, port, ws_hash, extra_roots=extra_roots, memory_only=memory_only)


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
    except httpx.ConnectError:
        print(f"Error: Vectr is not running on port {port}. Run: vectr start", file=sys.stderr)
        sys.exit(1)


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
    port = entry["port"] if entry is not None else int(os.getenv("VECTR_PORT", "8765"))

    _write_workspace_config(workspace, port)

    # write Claude Code hook entries (UPG-9.4+) — opt-in via --hooks
    if getattr(args, "hooks", False):
        _write_claude_hooks(workspace)

    # write user-defined exclusions to .vectrignore
    exclude_dirs: list[str] = getattr(args, "exclude", None) or []
    if exclude_dirs:
        from integrations.workspace_detect import write_vectrignore
        write_vectrignore(workspace, exclude_dirs)
        print(f"  Added to .vectrignore: {', '.join(exclude_dirs)}", file=sys.stderr)

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
    from agent.indexer import CodeIndexer
    from agent.watcher import CodeWatcher
    from integrations.workspace_detect import find_workspace_root

    _preflight_grammars()

    roots = _resolve_workspace_roots(args)
    workspace = find_workspace_root(roots[0])
    extra_roots = roots[1:]
    embed_model = os.getenv("VECTR_EMBED_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")

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

def main() -> None:
    parser = argparse.ArgumentParser(prog="vectr", description="Zero-config semantic codebase indexer")
    sub = parser.add_subparsers(dest="command")

    _default_path = os.getenv("VECTR_WORKSPACE", ".")
    _default_port = int(os.getenv("VECTR_PORT", "8765"))

    p_start = sub.add_parser(
        "start",
        help="Start the Vectr daemon and index the workspace. "
             "Accepts a .code-workspace file, one or more --path flags, or defaults to cwd.",
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

    p_stop = sub.add_parser("stop", help="Stop the daemon for a workspace")
    p_stop.add_argument("--path", default=_default_path)
    p_stop.add_argument("--all", action="store_true", help="Stop all running instances")

    p_restart = sub.add_parser("restart", help="Stop and restart the daemon for a workspace")
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

    p_init = sub.add_parser("init", help="Write CLAUDE.md and .mcp.json to a workspace (no server)")
    p_init.add_argument("--path", default=_default_path)
    p_init.add_argument(
        "--exclude", action="append", metavar="DIR", dest="exclude",
        help="Append a directory name to .vectrignore (repeatable). "
             "Example: vectr init --exclude vendor --exclude generated",
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

    p_search = sub.add_parser("search", help="Semantic search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--n", type=int, default=10)
    p_search.add_argument("--language", help="Filter by language")
    p_search.add_argument("--port", type=int, default=_default_port)

    p_remember = sub.add_parser("remember", help="Store a working-memory note (shell path to the note store)")
    p_remember.add_argument("content", help="The note content to store")
    p_remember.add_argument("--tags", action="append", metavar="TAG", help="Topic tag (repeatable)")
    p_remember.add_argument("--priority", choices=["high", "medium", "low"], default="medium")
    p_remember.add_argument("--kind", choices=["directive", "task", "gotcha", "finding", "reference"],
                            default="finding", help="Memory kind (controls injection policy)")
    p_remember.add_argument("--title", default="", help="Short label for index-tier display (optional; derived from first content line if empty)")
    p_remember.add_argument("--path", default=_default_path)
    p_remember.add_argument("--port", type=int, default=_default_port)

    p_recall = sub.add_parser("recall", help="Print recalled working-memory notes to stdout (for hooks)")
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
