"""CLI entry point: vectr start / restart / stop / index / search / status / init."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PID_FILE = Path.home() / ".vectr" / "vectr.pid"
PORT_FILE = Path.home() / ".vectr" / "vectr.port"

_CLAUDE_MD = """\
# Vectr tools — available alongside Read and Bash

This workspace is indexed by vectr. Use vectr tools when they'd be faster than reading
files directly.

## Exploration tools — use when you don't already know where to look

| Situation | Tool |
|---|---|
| Don't know which file contains a class/function | `vectr_locate("ClassName")` |
| Looking for code by concept or behaviour | `vectr_search("what you're looking for")` |
| Want to see what calls or is called by a symbol | `vectr_trace("function_name")` |
| First contact with an unfamiliar codebase | `vectr_map()` for structural overview |

If you already know the file or symbol, Read is fine — no need to use vectr.

## Memory tools — always use for cross-session continuity

The next session starts cold and won't have your current context:

- `vectr_remember(content, tags=["tag"], priority="high"|"normal")` — store each key
  finding immediately: file paths, signatures, call patterns, gotchas
- `vectr_snapshot("label")` — seal all notes at the end of a research session
- `vectr_recall()` — retrieve all stored notes at the start of an implementation session
"""

_MCP_JSON = """\
{
  "mcpServers": {
    "vectr": {
      "type": "http",
      "url": "http://localhost:8765/mcp"
    }
  }
}
"""


def _get_running_port() -> int | None:
    if PORT_FILE.exists():
        try:
            return int(PORT_FILE.read_text().strip())
        except ValueError:
            return None
    return None


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
        return True  # already gone
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(0.3)
        try:
            os.kill(pid, 0)  # probe
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
    except ProcessLookupError:
        pass
    return True


def _write_workspace_config(workspace: str, port: int) -> None:
    """Write CLAUDE.md and .mcp.json into the workspace root."""
    root = Path(workspace)

    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(_CLAUDE_MD)
        print(f"  Created {claude_md}", file=sys.stderr)

    mcp_json = root / ".mcp.json"
    if not mcp_json.exists():
        content = _MCP_JSON.replace("8765", str(port))
        mcp_json.write_text(content)
        print(f"  Created {mcp_json}", file=sys.stderr)

    settings = root / ".claude" / "settings.json"
    if not settings.exists():
        settings.parent.mkdir(exist_ok=True)
        settings.write_text('{\n  "enableAllProjectMcpServers": true\n}\n')
        print(f"  Created {settings}", file=sys.stderr)


def _do_start(workspace: str, port: int) -> None:
    import subprocess

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port))

    env = {**os.environ, "VECTR_WORKSPACE": workspace, "VECTR_PORT": str(port)}
    vectr_dir = Path(__file__).resolve().parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", str(port)],
        env=env,
        cwd=str(vectr_dir),
    )
    if proc.pid is not None:
        PID_FILE.write_text(str(proc.pid))
    print(f"Vectr started (PID {proc.pid}) on port {port}", file=sys.stderr)
    print(f"Workspace : {workspace}", file=sys.stderr)
    print(f"MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    port = args.port
    workspace = str(Path(args.path).resolve())

    alive, running_ws = _is_server_alive(port)
    if alive:
        if running_ws == workspace:
            print(
                f"Vectr is already running on port {port} with this workspace.",
                file=sys.stderr,
            )
            print(f"  Workspace : {running_ws}", file=sys.stderr)
            print(f"  MCP URL   : http://localhost:{port}/mcp", file=sys.stderr)
            return
        else:
            print(
                f"Error: Vectr is already running on port {port} with a different workspace:",
                file=sys.stderr,
            )
            print(f"  Running   : {running_ws}", file=sys.stderr)
            print(f"  Requested : {workspace}", file=sys.stderr)
            print(
                f"\nTo switch workspaces, run:  vectr restart --path {workspace}",
                file=sys.stderr,
            )
            sys.exit(1)

    _write_workspace_config(workspace, port)
    _do_start(workspace, port)


def cmd_index(args: argparse.Namespace) -> None:
    import httpx

    port = _get_running_port() or args.port
    path = str(Path(args.path).resolve())
    try:
        resp = httpx.post(f"{_api_base(port)}/v1/index", json={"path": path, "force": args.force}, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        print(json.dumps(data, indent=2))
    except httpx.ConnectError:
        print(f"Error: Vectr is not running on port {port}. Run: vectr start", file=sys.stderr)
        sys.exit(1)


def cmd_search(args: argparse.Namespace) -> None:
    import httpx

    port = _get_running_port() or args.port
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

    port = _get_running_port() or args.port
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
    if not PID_FILE.exists():
        print("No Vectr PID file found — is it running?", file=sys.stderr)
        return
    pid = int(PID_FILE.read_text().strip())
    gone = _stop_server(pid)
    PID_FILE.unlink(missing_ok=True)
    PORT_FILE.unlink(missing_ok=True)
    if gone:
        print(f"Vectr stopped (PID {pid})")
    else:
        print(f"Warning: could not confirm PID {pid} stopped.", file=sys.stderr)


def cmd_restart(args: argparse.Namespace) -> None:
    port = args.port
    workspace = str(Path(args.path).resolve())

    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        print(f"Stopping PID {pid}...", file=sys.stderr)
        _stop_server(pid)
        PID_FILE.unlink(missing_ok=True)
        PORT_FILE.unlink(missing_ok=True)
    else:
        alive, _ = _is_server_alive(port)
        if alive:
            print(
                f"Warning: server running on port {port} but no PID file found. "
                "Cannot stop it cleanly — kill manually if needed.",
                file=sys.stderr,
            )

    _write_workspace_config(workspace, port)
    _do_start(workspace, port)


def cmd_init(args: argparse.Namespace) -> None:
    port = _get_running_port() or args.port
    workspace = str(Path(args.path).resolve())
    _write_workspace_config(workspace, port)
    print(f"Workspace configured: {workspace}", file=sys.stderr)
    print(f"  Run 'vectr start --path {workspace}' to index and start the server.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="vectr", description="Zero-config semantic codebase indexer")
    parser.add_argument("--port", type=int, default=int(os.getenv("VECTR_PORT", "8765")))
    sub = parser.add_subparsers(dest="command")

    _default_path = os.getenv("VECTR_WORKSPACE", ".")

    p_start = sub.add_parser("start", help="Start the Vectr daemon and index the workspace")
    p_start.add_argument(
        "--path", default=_default_path,
        help="Workspace root to index (default: $VECTR_WORKSPACE or .)",
    )
    p_start.add_argument("--port", type=int, default=int(os.getenv("VECTR_PORT", "8765")))

    p_restart = sub.add_parser("restart", help="Stop the running daemon and start with a new workspace")
    p_restart.add_argument(
        "--path", default=_default_path,
        help="Workspace root to index (default: $VECTR_WORKSPACE or .)",
    )
    p_restart.add_argument("--port", type=int, default=int(os.getenv("VECTR_PORT", "8765")))

    p_init = sub.add_parser("init", help="Write CLAUDE.md and .mcp.json to a workspace (no server)")
    p_init.add_argument(
        "--path", default=_default_path,
        help="Workspace root (default: $VECTR_WORKSPACE or .)",
    )

    p_index = sub.add_parser("index", help="(Re)index a directory or file")
    p_index.add_argument("--path", default=".", help="Path to index")
    p_index.add_argument("--force", action="store_true", help="Force full re-index")

    p_search = sub.add_parser("search", help="Semantic search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--n", type=int, default=10, help="Number of results (default: 10)")
    p_search.add_argument("--language", help="Filter by language")

    sub.add_parser("status", help="Show indexing status")
    sub.add_parser("stop", help="Stop the running daemon")

    args = parser.parse_args()
    dispatch = {
        "start":   cmd_start,
        "restart": cmd_restart,
        "init":    cmd_init,
        "index":   cmd_index,
        "search":  cmd_search,
        "status":  cmd_status,
        "stop":    cmd_stop,
    }
    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
