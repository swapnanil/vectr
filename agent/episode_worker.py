"""Detached worker entrypoint for the PostToolUse episode-capture hook.

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
see agent/config.yaml's `episodes.client_truncate_chars`), KEEPING THE TAIL
(not the head — a failure marker near the end of a long output must survive
this cap), POSTs the result
to `http://localhost:<port>/v1/episode`, and exits 0 regardless of outcome.
Never writes to stdout/stderr — nothing reads this process's output. Any
failure (missing/malformed file, daemon down, timeout, non-2xx response)
silently drops this one episode; a dropped episode is never worse than a
noisy or hung tool call.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
import time


def _sweep_stale_temp_files(max_age_s: float) -> None:
    """Best-effort cleanup of orphaned `vectr-episode-*.json` payload files.
    The normal case never needs this: this process deletes its OWN payload
    file above, unconditionally, before
    doing anything else. This only catches the rarer case where
    `_spawn_episode_worker` (main.py / agent/hook_cli.py) wrote a temp file
    and then failed to actually launch a worker to consume it (e.g.
    `subprocess.Popen` raising) — nothing else in that path is ever able to
    clean it up. Runs opportunistically, once, on this already-detached
    process's own time — never on the PostToolUse hook's foreground path
    (spec §7 gate G2), and never raises."""
    try:
        pattern = os.path.join(tempfile.gettempdir(), "vectr-episode-*.json")
        cutoff = time.time() - max_age_s
        for path in glob.glob(pattern):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass  # another worker already claimed/removed it — fine
    except Exception:
        pass  # sweep is best-effort; never let it affect this episode's post


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

    from agent.config import (
        EPISODES_CLIENT_TRUNCATE_CHARS,
        EPISODES_POST_TIMEOUT_S,
        EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S,
    )

    _sweep_stale_temp_files(EPISODES_STALE_TEMP_FILE_SWEEP_AGE_S)

    if not isinstance(envelope, dict):
        return
    port = envelope.get("port")
    payload = envelope.get("payload")
    if not isinstance(port, int) or not isinstance(payload, dict):
        return

    for field in ("stdout_tail", "stderr_tail"):
        value = payload.get(field)
        if isinstance(value, str) and len(value) > EPISODES_CLIENT_TRUNCATE_CHARS:
            # Keep the TAIL, not the head: failure markers characteristically
            # appear near the end of long output (agent/outcome.py), and
            # agent/episode_canon.py's
            # own digest step is head+tail-preserving on the assumption that
            # truncation upstream hasn't already discarded the tail. Slicing
            # the head here would silently drop exactly the signal that
            # determines outcome for any output longer than the cap.
            payload[field] = value[-EPISODES_CLIENT_TRUNCATE_CHARS:]

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
