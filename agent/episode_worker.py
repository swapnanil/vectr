"""Detached worker entrypoint for the PostToolUse episode-capture hook
(memoization-l1-capture-design §2).

`main.cmd_hook`'s "post-tool-use" branch (and `agent.hook_cli.run_hook`'s
mirror of it) spawns this module via `python -m agent.episode_worker
<payload-file>` with `start_new_session=True` and returns immediately
WITHOUT waiting — the hook's own foreground budget is <=50ms p95 (spec
section 7 gate G2), which neither an HTTP round-trip to the daemon nor this
module's own `agent.config` import (measured ~50-90ms alone) can reliably
meet. This process does the actual truncate-then-`POST /v1/episode` work on
its own time, fully detached from the editor's turn loop.

Contract: argv[1] is the path to a private temp file (written by the caller
via `tempfile.mkstemp`) containing one JSON object
`{"port": int, "payload": {...}}`; this process reads it, deletes it, caps
`payload["stdout_tail"]`/`payload["stderr_tail"]` to the config-declared
`EPISODES_CLIENT_TRUNCATE_CHARS` (the single source of truth for that cap —
see agent/config.yaml's `episodes.client_truncate_chars`), POSTs the result
to `http://localhost:<port>/v1/episode`, and exits 0 regardless of outcome.
Never writes to stdout/stderr — nothing reads this process's output. Any
failure (missing/malformed file, daemon down, timeout, non-2xx response)
silently drops this one episode; a dropped episode is never worse than a
noisy or hung tool call.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> None:
    if len(sys.argv) < 2:
        return
    payload_path = sys.argv[1]
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            envelope = json.load(f)
    except Exception:
        return
    finally:
        try:
            os.remove(payload_path)
        except OSError:
            pass  # already gone, or never existed — nothing left to clean up

    if not isinstance(envelope, dict):
        return
    port = envelope.get("port")
    payload = envelope.get("payload")
    if not isinstance(port, int) or not isinstance(payload, dict):
        return

    from agent.config import EPISODES_CLIENT_TRUNCATE_CHARS, EPISODES_POST_TIMEOUT_S

    for field in ("stdout_tail", "stderr_tail"):
        value = payload.get(field)
        if isinstance(value, str) and len(value) > EPISODES_CLIENT_TRUNCATE_CHARS:
            payload[field] = value[:EPISODES_CLIENT_TRUNCATE_CHARS]

    try:
        import httpx
        httpx.post(
            f"http://localhost:{port}/v1/episode",
            json=payload,
            timeout=EPISODES_POST_TIMEOUT_S,
        )
    except Exception:
        return  # daemon down/slow/erroring — drop this one episode silently


if __name__ == "__main__":
    main()
