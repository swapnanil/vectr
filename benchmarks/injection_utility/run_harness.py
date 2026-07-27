#!/usr/bin/env python3
"""Injection-UTILITY eval — run one (scenario, arm) cell end to end.

Injection RELEVANCE is already measured (see
`benchmarks/proactive_injection_precision/`). This harness measures the other
half: does an injected note actually CHANGE what an agent does?

The A/B
-------
Both arms are the SAME agent, the SAME task, the SAME synthetic workspace, the
SAME scratch daemon with the SAME planted note in its store, and the SAME
`vectr proxy` on the request path via `ANTHROPIC_BASE_URL`. Exactly one thing
differs:

  arm A (`--arm inject`)   : `vectr proxy`
  arm B (`--arm no-inject`): `vectr proxy --no-inject`

Arm B keeps the proxy so that forwarding, streaming, connection reuse and
latency are identical across arms and the only variable left is the appended
context block. (Dropping the proxy entirely in arm B would confound injection
with proxy transparency, which is a separate, already-gated question.)

Isolation
---------
One fresh `VECTR_DB_DIR`, one fresh daemon and one fresh proxy PROCESS per cell.
The proxy mints its cooldown session identity once per process (`proxy-<uuid>`),
and that ledger is a 30-anchor ring with no time decay and no in-process reset —
so reusing a proxy across cells would let one cell's injections cool down the
next cell's planted note. A fresh process per cell is the only clean answer.

Honesty rails
-------------
* The agent is told the TASK and nothing else. It is never told that a note was
  planted, that anything is injected, or that it is being measured.
* The agent gets no vectr tools: the daemon is started `--no-ide-config` (so no
  CLAUDE.md / .mcp.json is written into the workspace) and the CLI runs with
  `--strict-mcp-config` and no config, so zero MCP servers load. Otherwise arm B
  could simply recall the planted note itself and the arms would not differ.
* Scenario verify scripts are materialized OUTSIDE the workspace and only after
  the agent finishes, so they cannot leak the planted fact to arm B.
* NON-VACUITY IS MANDATORY. An arm-A cell whose audit log never shows the
  planted anchor injected is reported `valid: false` and must be excluded, not
  counted as "the note did not help".

Exit code: 0 whenever the cell ran to completion, INCLUDING when the agent
failed the task or the run is invalid. This is a measurement tool; floors and
verdicts belong to the reviewer reading the numbers, never to this script.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scenarios as scen  # noqa: E402
import scorer  # noqa: E402

DEFAULT_DAEMON_PORT = 8899
DEFAULT_PROXY_PORT = 8900


# ---------------------------------------------------------------------------
# small process / http helpers (stdlib only, same posture as the sibling harness)
# ---------------------------------------------------------------------------


def _http_json(method: str, url: str, payload: dict | None = None, timeout: float = 30.0):
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "_body": exc.read().decode("utf-8", "replace")[:400]}
    except Exception as exc:
        return {"_error": type(exc).__name__}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body[:400]}


def _wait_for(url: str, timeout_s: float, label: str) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        last = _http_json("GET", url, timeout=10.0)
        if "_error" not in last and "_http_error" not in last:
            return last
        time.sleep(1.0)
    raise SystemExit(f"ABORT: {label} did not become ready at {url} within {timeout_s:.0f}s: {last}")


def _port_pids(port: int) -> list[int]:
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in out.split() if p.strip().isdigit()]


def _hard_stop_port(port: int) -> list[int]:
    """Last-resort teardown so no scratch listener is ever left running."""
    killed: list[int] = []
    for pid in _port_pids(port):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            time.sleep(1.0)
            if not _port_pids(port):
                break
        killed.append(pid)
    return killed


_STARTED_PORT = re.compile(r"on port (\d+)")


def _parse_started_port(text: str) -> int | None:
    """The port `vectr start` reports it actually bound (see start_daemon)."""
    m = _STARTED_PORT.search(text or "")
    return int(m.group(1)) if m else None


def _free_port_at_or_above(start: int, *, avoid: set[int]) -> int:
    for port in range(start, start + 60):
        if port in avoid or _port_pids(port):
            continue
        return port
    raise SystemExit(f"ABORT: no free scratch port at or above {start}")


def _stop_daemon(vectr_bin: str, workspace: Path, port: int, env: dict[str, str]) -> list[int]:
    """Stop a scratch daemon and guarantee the port is released.

    `vectr stop` takes `--path` and has NO `--port` flag; passing one makes
    argparse exit non-zero without stopping anything, which silently leaks the
    daemon. The hard-stop fallback then closes the gap for good.
    """
    subprocess.run(
        [vectr_bin, "stop", "--path", str(workspace)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    for _ in range(10):
        if not _port_pids(port):
            return []
        time.sleep(1.0)
    return _hard_stop_port(port)


def _spawn_env_for_agent(base_url: str) -> dict[str, str]:
    """Env for the spawned eval agent.

    Every CLAUDE*/ANTHROPIC* var is stripped first so the spawned session looks
    like a fresh user invocation rather than a child of the harness's own
    session (the harness runs inside Claude Code, whose CLAUDE_CODE_* markers
    otherwise flip the child into nested-session behavior no real user hits).
    `ANTHROPIC_BASE_URL` is then set back, deliberately, to route the agent
    through the proxy under test. OAuth still resolves via the keychain.
    """
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
    env["ANTHROPIC_BASE_URL"] = base_url
    return env


# ---------------------------------------------------------------------------
# cell
# ---------------------------------------------------------------------------


class Cell:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scenario = scen.get(args.scenario)
        self.arm = args.arm
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"{stamp}-{self.scenario.slug}-{self.arm}"
        self.root = Path(args.runs_dir).resolve() / self.run_id
        self.workspace = self.root / "workspace"
        self.db_dir = self.root / "db"
        self.verify_dir = self.root / "verify"
        self.artifacts = self.root / "artifacts"
        self.audit_log = self.artifacts / "audit.log"
        self.daemon_port = args.daemon_port
        self.proxy_port = args.proxy_port
        self.vectr = args.vectr_bin
        self.proxy_proc: subprocess.Popen | None = None
        self.note_id: int | None = None
        self.baselines: dict[str, str] = {}
        self.record: dict[str, Any] = {
            "run_id": self.run_id,
            "scenario": self.scenario.slug,
            "utility_class": self.scenario.utility_class,
            "arm": self.arm,
            "model": args.model,
            "started_utc": stamp,
        }

    # -- setup ----------------------------------------------------------

    def prepare(self) -> None:
        if self.root.exists():
            raise SystemExit(f"ABORT: run dir already exists: {self.root}")
        for d in (self.workspace, self.db_dir, self.verify_dir, self.artifacts):
            d.mkdir(parents=True, exist_ok=True)
        self.baselines = scen.materialize(self.scenario, self.workspace)
        (self.artifacts / "baselines.json").write_text(json.dumps(self.baselines, indent=2))
        (self.artifacts / "scenario.json").write_text(json.dumps({
            "slug": self.scenario.slug,
            "title": self.scenario.title,
            "utility_class": self.scenario.utility_class,
            "task_prompt": self.scenario.task_prompt,
            "primary_check": self.scenario.primary_check,
            "checks": [repr(c) for c in self.scenario.checks],
            "metrics": [repr(m) for m in self.scenario.metrics],
            "naive_expectation": self.scenario.naive_expectation,
            "note_following_expectation": self.scenario.note_following_expectation,
            "planted_note": self.scenario.note.content,
        }, indent=2))

    def _daemon_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["VECTR_DB_DIR"] = str(self.db_dir)
        env["VECTR_AUDIT_LOG"] = str(self.audit_log)
        env["VECTR_PORT"] = str(self.daemon_port)
        env["VECTR_WORKSPACE"] = str(self.workspace)
        return env

    def start_daemon(self) -> None:
        if _port_pids(self.daemon_port):
            raise SystemExit(
                f"ABORT: port {self.daemon_port} already in use; refusing to collide "
                f"with an existing daemon"
            )
        cmd = [
            self.vectr, "start", str(self.workspace),
            "--port", str(self.daemon_port),
            "--memory-only", "--no-ide-config",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=self._daemon_env()
        )
        out = f"{proc.stdout}\n{proc.stderr}"
        (self.artifacts / "daemon-start.txt").write_text(
            f"$ {' '.join(cmd)}\nrc={proc.returncode}\n\n{out}"
        )
        # `--port` is only a PREFERENCE: cmd_start resolves the real port through
        # `InstanceRegistry.find_free_port(ws_hash, preferred_port)`, which walks
        # past a busy port. Assuming the requested port silently pointed every
        # subsequent call (status poll, proxy --daemon-port, teardown) at the
        # wrong listener, so read the port the daemon actually reports.
        actual = _parse_started_port(out)
        if actual is not None and actual != self.daemon_port:
            print(
                f"[harness] daemon bound port {actual}, not the requested "
                f"{self.daemon_port}; following the real port",
                file=sys.stderr,
            )
            self.daemon_port = actual
        if self.daemon_port < 8899:
            raise SystemExit(
                f"ABORT: daemon landed on port {self.daemon_port}, below the 8899 scratch "
                f"floor — refusing to talk to a possibly-live daemon"
            )
        self.record["daemon_port"] = self.daemon_port
        status = _wait_for(
            f"http://127.0.0.1:{self.daemon_port}/v1/status", 180.0, "scratch daemon"
        )
        (self.artifacts / "daemon-status.json").write_text(json.dumps(status, indent=2))
        self.record["daemon_workspace"] = status.get("workspace_root") or status.get("workspace")

    def plant_note(self) -> None:
        note = self.scenario.note
        payload: dict[str, Any] = {
            "content": note.content,
            "title": note.title,
            "kind": note.kind,
            "priority": note.priority,
            "tags": list(note.tags),
            "agent": "injection-utility-harness",
        }
        if note.trigger_paths:
            payload["triggers"] = [{"path": p} for p in note.trigger_paths]
        out = _http_json(
            "POST", f"http://127.0.0.1:{self.daemon_port}/v1/remember", payload, timeout=60
        )
        nid = out.get("note_id")
        if not isinstance(nid, int):
            raise SystemExit(f"ABORT: could not plant note: {out}")
        self.note_id = nid
        self.record["planted_note_id"] = nid
        self.record["planted_anchor"] = f"note:{nid}"

        count = _http_json("GET", f"http://127.0.0.1:{self.daemon_port}/v1/status", timeout=30)
        self.record["notes_in_store"] = count.get("notes_count")

    def probe(self) -> None:
        """Pre-run reachability probes -- do NOT touch the proxy's ledger.

        Both use a `utilprobe-` session id. The proxy's own identity is always
        `proxy-<uuid>`, so these can never consume a cooldown slot that the
        measured run would otherwise have had.

        probe_turn1 mirrors the FIRST proxied request, whose window carries task
        TEXT ONLY: `assemble_window` extracts file paths and symbols exclusively
        from tool-input keys, never from free text, so nothing structural exists
        before the agent's first tool call. probe_turn2 mirrors a later request,
        after a tool call has put a file path in the window.
        """
        base = f"http://127.0.0.1:{self.daemon_port}/v1/proactive"
        turn1 = _http_json("POST", base, {
            "text": self.scenario.task_prompt,
            "file_paths": [], "symbols": [],
            "session_id": f"utilprobe-turn1-{self.scenario.slug}",
            "channel": "proxy",
        }, timeout=60)
        turn2 = _http_json("POST", base, {
            "text": self.scenario.task_prompt,
            "file_paths": [str(self.workspace / self.scenario.probe_file)],
            "symbols": [],
            "session_id": f"utilprobe-turn2-{self.scenario.slug}",
            "channel": "proxy",
        }, timeout=60)
        anchor = self.record.get("planted_anchor")
        preflight = {
            "turn1_text_only": {
                "item_count": turn1.get("item_count"),
                "chars": len(turn1.get("context") or ""),
                "anchor_ids": turn1.get("anchor_ids"),
                "planted_present": anchor in (turn1.get("anchor_ids") or []),
            },
            "turn2_with_file_anchor": {
                "item_count": turn2.get("item_count"),
                "chars": len(turn2.get("context") or ""),
                "anchor_ids": turn2.get("anchor_ids"),
                "planted_present": anchor in (turn2.get("anchor_ids") or []),
            },
        }
        (self.artifacts / "preflight.json").write_text(json.dumps(preflight, indent=2))
        self.record["preflight"] = preflight
        reachable = (
            preflight["turn1_text_only"]["planted_present"]
            or preflight["turn2_with_file_anchor"]["planted_present"]
        )
        self.record["planted_note_reachable_preflight"] = reachable
        if not reachable and not self.args.allow_unreachable:
            raise SystemExit(
                "ABORT: the planted note is not retrievable on EITHER probe, so this "
                "scenario cannot test injection utility. Fix the scenario (not the "
                "score); re-run with --allow-unreachable to record it anyway."
            )

    def start_proxy(self) -> None:
        # The daemon may have taken the port this proxy would have used.
        self.proxy_port = _free_port_at_or_above(
            max(self.proxy_port, 8899), avoid={self.daemon_port}
        )
        self.record["proxy_port"] = self.proxy_port
        cmd = [
            self.vectr, "proxy",
            "--port", str(self.proxy_port),
            "--daemon-port", str(self.daemon_port),
            "--path", str(self.workspace),
        ]
        if self.arm == "no-inject":
            cmd.append("--no-inject")
        stderr_path = self.artifacts / "proxy.stderr"
        self._proxy_stderr = stderr_path.open("w")
        self.proxy_proc = subprocess.Popen(
            cmd, stdout=self._proxy_stderr, stderr=subprocess.STDOUT,
            env=self._daemon_env(),
        )
        health = _wait_for(
            f"http://127.0.0.1:{self.proxy_port}/__vectr_proxy/health", 90.0, "scratch proxy"
        )
        self.record["proxy_instance_id"] = health.get("instance_id")
        self.record["proxy_command"] = " ".join(cmd)

    # -- the measured run ------------------------------------------------

    def run_agent(self) -> None:
        claude = shutil.which("claude") or "claude"
        cmd = [
            claude, "-p", self.scenario.task_prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--model", self.args.model,
            "--max-turns", str(self.args.max_turns),
            "--dangerously-skip-permissions",
            # No MCP servers at all: the eval agent must not be able to reach
            # any vectr tool, or arm B could recall the planted note itself.
            "--strict-mcp-config",
        ]
        transcript = self.artifacts / "transcript.jsonl"
        started = time.time()
        with transcript.open("w") as out:
            proc = subprocess.Popen(
                cmd, cwd=str(self.workspace), stdout=out, stderr=subprocess.PIPE,
                text=True, env=_spawn_env_for_agent(f"http://127.0.0.1:{self.proxy_port}"),
            )
            try:
                _, stderr = proc.communicate(timeout=self.args.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate()
                stderr = (stderr or "") + f"\n[harness] timed out after {self.args.timeout_s}s"
        (self.artifacts / "agent.stderr").write_text(stderr or "")
        self.record["agent_returncode"] = proc.returncode
        self.record["agent_wall_s"] = round(time.time() - started, 1)

    # -- teardown + scoring ----------------------------------------------

    def capture_and_teardown(self) -> None:
        health = _http_json(
            "GET", f"http://127.0.0.1:{self.proxy_port}/__vectr_proxy/health", timeout=10
        )
        (self.artifacts / "proxy-health.json").write_text(json.dumps(health, indent=2))
        self.record["proxy_metrics"] = health.get("metrics")

        if self.proxy_proc is not None:
            self.proxy_proc.terminate()
            try:
                self.proxy_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proxy_proc.kill()
        try:
            self._proxy_stderr.close()
        except Exception:
            pass
        self.record["proxy_hard_killed"] = _hard_stop_port(self.proxy_port)
        self.record["daemon_hard_killed"] = _stop_daemon(
            self.vectr, self.workspace, self.daemon_port, self._daemon_env()
        )

    def score(self) -> None:
        scen.materialize_verifiers(self.scenario, self.verify_dir)
        transcript = scorer.load_transcript(self.artifacts / "transcript.jsonl")
        result = scorer.score_run(
            self.scenario,
            workspace=self.workspace,
            baselines=self.baselines,
            transcript=transcript,
            verify_dir=self.verify_dir,
        )
        self.record["score"] = result

        nv = scorer.non_vacuity(self.audit_log, self.record.get("planted_anchor", ""))
        self.record["non_vacuity"] = nv

        if self.arm == "inject":
            valid = bool(nv["planted_note_injected"])
            reason = "" if valid else (
                "arm A ran but the planted note never appears in a PROACTIVE_INJECT "
                "audit event — the injection path did not fire, so this cell tests "
                "nothing and must be excluded, not read as 'the note did not help'"
            )
        else:
            valid = nv["inject_events"] == 0
            reason = "" if valid else (
                "arm B recorded PROACTIVE_INJECT events — the no-injection control is "
                "contaminated and the pair must be discarded"
            )
        self.record["valid"] = valid
        self.record["invalid_reason"] = reason

    def write(self) -> None:
        (self.artifacts / "result.json").write_text(json.dumps(self.record, indent=2))
        latest = Path(self.args.runs_dir).resolve() / "results.jsonl"
        with latest.open("a") as fh:
            fh.write(json.dumps(self.record) + "\n")

    def report(self) -> None:
        r = self.record
        s = r.get("score", {})
        print("=" * 72)
        print(f"cell {r['run_id']}")
        print(f"  scenario : {r['scenario']}  ({r['utility_class']})")
        print(f"  arm      : {r['arm']}   model: {r['model']}")
        print(f"  valid    : {r.get('valid')}"
              + (f"  -- {r.get('invalid_reason')}" if r.get("invalid_reason") else ""))
        print()
        pf = r.get("preflight", {})
        if pf:
            t1, t2 = pf["turn1_text_only"], pf["turn2_with_file_anchor"]
            print("  preflight reachability (daemon-side, does not touch proxy ledger):")
            print(f"    turn-1 text-only    : items={t1['item_count']} chars={t1['chars']} "
                  f"planted={t1['planted_present']}")
            print(f"    turn-2 +file anchor : items={t2['item_count']} chars={t2['chars']} "
                  f"planted={t2['planted_present']}")
        nv = r.get("non_vacuity", {})
        print()
        print("  NON-VACUITY (durable audit log):")
        print(f"    PROACTIVE_INJECT events        : {nv.get('inject_events')}")
        print(f"    events carrying planted anchor : {nv.get('planted_anchor_injections')}")
        print(f"    chars injected (all events)    : {nv.get('total_chars_injected')}")
        print(f"    proxy metrics                  : {r.get('proxy_metrics')}")
        print()
        print(f"  PRIMARY CHECK  {s.get('primary_check')} -> utility_hit={s.get('utility_hit')}")
        for c in s.get("checks", []):
            print(f"    [{'PASS' if c['passed'] else 'FAIL'}] {c['name']:32s} {c['detail']}")
        for m in s.get("metrics", []):
            print(f"    [metric] {m['name']:32s} {m['value']}")
        cost = s.get("cost", {})
        print()
        print(f"  cost: turns={cost.get('num_turns')} tool_calls={cost.get('tool_calls')} "
              f"in={cost.get('input_tokens')} out={cost.get('output_tokens')} "
              f"cache_read={cost.get('cache_read_input_tokens')} "
              f"usd={cost.get('total_cost_usd')} wall={r.get('agent_wall_s')}s")
        print(f"  artifacts: {self.artifacts}")
        print("=" * 72)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=sorted(scen.SCENARIOS))
    ap.add_argument("--arm", required=True, choices=["inject", "no-inject"])
    ap.add_argument("--runs-dir", required=True,
                    help="Directory for run artifacts. MUST be outside any indexed vectr "
                         "workspace so a live daemon never re-indexes scratch runs.")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--daemon-port", type=int, default=DEFAULT_DAEMON_PORT)
    ap.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--vectr-bin", default=shutil.which("vectr") or "vectr")
    ap.add_argument("--allow-unreachable", action="store_true",
                    help="Record a cell even when the planted note fails both preflight probes.")
    ap.add_argument("--probe-only", action="store_true",
                    help="ZERO-QUOTA scenario check: start the daemon, plant the note, run "
                         "both reachability probes, tear down, and report. Spawns no agent. "
                         "Run this after authoring or editing a scenario -- a note that is "
                         "unreachable here can never produce a non-vacuous arm-A cell.")
    args = ap.parse_args()

    if args.daemon_port < 8899 or args.proxy_port < 8899:
        raise SystemExit("ABORT: scratch ports must be >= 8899 (8765/8800 are live daemons)")

    cell = Cell(args)

    if args.probe_only:
        try:
            cell.prepare()
            cell.start_daemon()
            cell.plant_note()
            cell.probe()
        finally:
            _stop_daemon(
                args.vectr_bin, cell.workspace, cell.daemon_port, cell._daemon_env()
            )
        pf = cell.record["preflight"]
        print(f"probe-only {cell.scenario.slug}: note={cell.record['planted_anchor']}")
        print(f"  turn-1 text-only    : {pf['turn1_text_only']}")
        print(f"  turn-2 +file anchor : {pf['turn2_with_file_anchor']}")
        print(f"  reachable           : {cell.record['planted_note_reachable_preflight']}")
        return

    try:
        cell.prepare()
        cell.start_daemon()
        cell.plant_note()
        cell.probe()
        cell.start_proxy()
        cell.run_agent()
    finally:
        try:
            cell.capture_and_teardown()
        except Exception as exc:  # teardown must never mask a run
            print(f"[harness] teardown issue: {type(exc).__name__}: {exc}", file=sys.stderr)
            _hard_stop_port(args.proxy_port)
            _hard_stop_port(args.daemon_port)
    cell.score()
    cell.write()
    cell.report()


if __name__ == "__main__":
    main()
