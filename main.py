"""CLI entry point: vectr start / index / search / status / stop."""
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


def _get_running_port() -> int | None:
    if PORT_FILE.exists():
        try:
            return int(PORT_FILE.read_text().strip())
        except ValueError:
            return None
    return None


def _api_base(port: int) -> str:
    return f"http://localhost:{port}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    import subprocess

    port = args.port
    workspace = str(Path(args.path).resolve())

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port))

    env = {**os.environ, "VECTR_WORKSPACE": workspace, "VECTR_PORT": str(port)}
    # Run uvicorn from the vectr source directory so api.py is importable
    # regardless of where the user calls `vectr start` from.
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
        print("No Vectr PID file found.", file=sys.stderr)
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}")
    except ProcessLookupError:
        print(f"Process {pid} not found — already stopped?")
    PID_FILE.unlink(missing_ok=True)
    PORT_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="vectr", description="Zero-config semantic codebase indexer")
    parser.add_argument("--port", type=int, default=int(os.getenv("VECTR_PORT", "8765")))
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Start the Vectr daemon and index the workspace")
    p_start.add_argument("--path", default=".", help="Workspace root to index (default: .)")
    p_start.add_argument("--port", type=int, default=int(os.getenv("VECTR_PORT", "8765")))

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
        "start": cmd_start,
        "index": cmd_index,
        "search": cmd_search,
        "status": cmd_status,
        "stop": cmd_stop,
    }
    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
