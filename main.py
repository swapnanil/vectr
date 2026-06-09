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

_CLAUDE_MD = """\
# Vectr — semantic search + reliable working memory

Vectr gives you two capabilities:

- **Semantic search**: find any symbol, pattern, or concept in this codebase by describing it in plain English — faster than grep, without knowing where to look.
- **Working memory**: store findings and recall them in <50ms on demand — whether later in this session, through `/compact`, or in a future session. Saving is a gain, not a risk.

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


def _do_start(workspace: str, port: int, ws_hash: str, extra_roots: list[str] | None = None) -> None:
    log_dir = Path.home() / ".vectr" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{ws_hash}.log"

    env = {
        **os.environ,
        "VECTR_WORKSPACE": workspace,
        "VECTR_PORT": str(port),
        "VECTR_EXTRA_ROOTS": json.dumps(extra_roots or []),
    }
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
    print(f"Vectr started (PID {proc.pid}) on port {port}", file=sys.stderr)
    print(f"Workspace : {workspace}", file=sys.stderr)
    if extra_roots:
        for r in extra_roots:
            print(f"          + {r}", file=sys.stderr)
    print(f"MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)
    print(f"Logs      : {log_path}", file=sys.stderr)
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
    _do_start(workspace, port, ws_hash, extra_roots=extra_roots)


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
    _do_start(workspace, port, ws_hash, extra_roots=extra_roots)


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
        print(f"Vectr config reset for: {workspace}", file=sys.stderr)
        return

    entry = InstanceRegistry().get(workspace_hash(workspace))
    port = entry["port"] if entry is not None else int(os.getenv("VECTR_PORT", "8765"))

    _write_workspace_config(workspace, port)

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

    p_index = sub.add_parser("index", help="(Re)index a directory or file")
    p_index.add_argument("--path", default=_default_path)
    p_index.add_argument("--port", type=int, default=_default_port)
    p_index.add_argument("--force", action="store_true", help="Force full re-index")

    p_search = sub.add_parser("search", help="Semantic search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--n", type=int, default=10)
    p_search.add_argument("--language", help="Filter by language")
    p_search.add_argument("--port", type=int, default=_default_port)

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
    }
    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
