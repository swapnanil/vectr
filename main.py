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
# Vectr tools — available alongside Read and Bash

Vectr gives you two things: semantic search over this codebase and a context offload layer.
Use it to drop explored code from your context window and recall it on demand — whether you need
it 10 turns later in this same session, or in a fresh session next week. The session boundary
is irrelevant: offload → free context → recall on demand, any time.

Use vectr tools in place of grep, find, and Read when you don't already know where to look —
each tool targets a specific gap in what you can address directly.

## Exploration tools

| Tool | Purpose |
|---|---|
| `vectr_search("natural language description")` | Semantic search — describe what you're looking for, get the most relevant code chunks back. Replaces grep + cat loops. |
| `vectr_locate("SymbolName")` | Symbol graph lookup — name → definition file and line. Faster than any file scan. |
| `vectr_trace("symbol_name")` | Call graph — callers and callees of a symbol, without reading files. |
| `vectr_map()` | Codebase overview — file tree + module summaries. Use once on a completely unknown repo. On a first visit, it returns raw metadata and you should follow up with `vectr_map_save(summary)` to store your synthesised description as a permanent passport. |
| `vectr_map_save(summary)` | Save your plain-English codebase summary (~200–350 tokens) as the permanent passport. Only call this when `vectr_map` returned raw metadata (i.e. no passport exists yet). |

If you already know the file path, use Read directly — don't search for what you can address directly.

## Working memory tools

Notes are findings you've stored — earlier in this session or in prior sessions. Reading a note
costs nothing; re-reading the file it describes costs tokens and turns.

**At session start:** `vectr_status()` — always call this first.
- `notes_count > 0` → prior work on this codebase is saved; call `vectr_recall(query="<your task in plain English>")` once, before opening any files
- `notes_count == 0` → skip recall and proceed

**The moment you find a key definition, pattern, or non-obvious detail:** `vectr_remember(content, tags=[...], priority="high"|"medium"|"low")` — store the actual code block, not a file pointer. Once stored, drop the file from context: vectr returns it in <50ms when you need it again. One note now = 3–5 fewer re-discovery calls later — in this session or the next.

**Before writing any final output (file, patch, or answer):** call `vectr_remember` at least once with the key type names, entry points, and non-obvious patterns you confirmed. This is the only step that persists your navigational understanding — the output file captures your findings, but not the path you took to reach them. Future sessions (or a later task in this run) cannot recall what was never stored.

**After heavy file reading or at a natural breakpoint (exploration → implementation):** `vectr_evict_hint()` — vectr lists which retrieved chunks are fully indexed (safe to drop from your context window) and prompts you to persist your synthesized findings via `vectr_remember`. Retrieved code is re-retrievable in <50ms; your analysis is not.

**When context is getting full or at natural breakpoints:** `vectr_snapshot("label")` — seals current notes as a checkpoint. Once a finding is in a note, you no longer need the source file in your context window. Any future session (or a later point in this one) recalls exactly what it needs — without re-reading everything this session explored. Use `vectr_snapshot_list()` at session start to find an existing checkpoint if `vectr_recall` returned nothing useful.

**If recalled notes already contain what you need:** work from them directly.
Use vectr_search or Read only to fill genuine gaps — not to re-discover what notes already say.
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


def _do_start(workspace: str, port: int, ws_hash: str) -> None:
    log_dir = Path.home() / ".vectr" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{ws_hash}.log"

    env = {**os.environ, "VECTR_WORKSPACE": workspace, "VECTR_PORT": str(port)}
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
    workspace = str(Path(args.path).resolve())
    ws_hash = workspace_hash(workspace)
    preferred_port = args.port

    registry = InstanceRegistry()
    registry.prune_dead()

    entry = registry.get(ws_hash)
    if entry is not None and _is_pid_alive(entry["pid"]):
        port = entry["port"]
        _write_workspace_config(workspace, port)
        print("Vectr is already running for this workspace.", file=sys.stderr)
        print(f"  Workspace : {workspace}", file=sys.stderr)
        print(f"  Port      : {port}", file=sys.stderr)
        print(f"  MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)
        return

    port = registry.find_free_port(ws_hash, preferred_port)
    _write_workspace_config(workspace, port)
    _do_start(workspace, port, ws_hash)


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
    workspace = str(Path(args.path).resolve())
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
    _write_workspace_config(workspace, port)
    _do_start(workspace, port, ws_hash)


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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="vectr", description="Zero-config semantic codebase indexer")
    sub = parser.add_subparsers(dest="command")

    _default_path = os.getenv("VECTR_WORKSPACE", ".")
    _default_port = int(os.getenv("VECTR_PORT", "8765"))

    p_start = sub.add_parser("start", help="Start the Vectr daemon and index the workspace")
    p_start.add_argument("--path", default=_default_path)
    p_start.add_argument("--port", type=int, default=_default_port)

    p_stop = sub.add_parser("stop", help="Stop the daemon for a workspace")
    p_stop.add_argument("--path", default=_default_path)
    p_stop.add_argument("--all", action="store_true", help="Stop all running instances")

    p_restart = sub.add_parser("restart", help="Stop and restart the daemon for a workspace")
    p_restart.add_argument("--path", default=_default_path)
    p_restart.add_argument("--port", type=int, default=_default_port)

    p_forget = sub.add_parser("forget", help="Delete working-memory notes for a workspace")
    p_forget.add_argument("--path", default=_default_path)
    p_forget.add_argument("--port", type=int, default=_default_port)
    p_forget.add_argument(
        "--all", action="store_true",
        help="Delete notes across ALL workspaces (operates directly on SQLite, no server needed)",
    )

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
