#!/usr/bin/env python3
"""EVAL-LONGITUDINAL-REDISCOVERY -- run one LEG end to end (DESIGN.md section 11, step 3).

A "leg" is one session in a multi-session trajectory (DESIGN.md sections 2, 5). This
script runs in one of two mutually exclusive modes, matching the two shapes of
DESIGN.md 8's artifact tree exactly:

  --shared-leg1   Leg 1 runs ONCE per (scenario, seed) under arm-A conditions (empty
                  store, `--no-inject`, no MCP, no hooks -- DESIGN.md 5.2) and is
                  reused as the starting snapshot for every arm's leg 1. This IS arm
                  A's own leg 1. Writes self-contained artifacts straight into
                  --out-dir (workspace.tar, manifest.sha256, baselines.json,
                  transcript.jsonl, result.json, leg1_id), with no results.jsonl
                  append -- there is no single trajectory this belongs to yet; a
                  driver (run_plan.py, not yet written) decides how each trajectory
                  incorporates it into its own legs/1/.

  (default)       A normal leg k>=2 (or a standalone k==1, useful for direct
                  debugging/probing without a driver) that reads/writes a single
                  trajectory's `<runs-dir>/<trajectory>/legs/<k>/` and appends its
                  result to `<runs-dir>/results.jsonl`.

Isolation and honesty rails mirror `injection_utility/run_harness.py` (one fresh
VECTR_DB_DIR/daemon/proxy process per invocation; scratch ports >= 8899; verify
scripts materialized outside the workspace only after the agent exits) with one
deliberate, DESIGN.md-directed departure: EVERY arm here runs through a
non-injecting `vectr proxy` -- including the hook arms -- because DESIGN.md 4
explicitly requires it ("removing the proxy from the control would confound
injection with proxy transparency"), whereas the trap harness's hook arm has no
proxy on the path at all. Only arm "proxy" (arm C) enables injection.

Non-vacuity gates (DESIGN.md 4.1) are gathered here and handed to
`scorer.leg_non_vacuity` unchanged; the outcome verdict (`scorer.score_run`,
`scorer.leg_metrics`) never sees `arm` at all.

Exit code: 0 whenever the leg ran to completion, INCLUDING an invalid or
task-failing leg. Floors and verdicts belong to report.py's reader, never to this
script.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_THIS_DIR = Path(__file__).resolve().parent
# `benchmarks/injection_utility/` ships its own same-named `scenarios.py`/`scorer.py`.
# A bare `sys.path.insert` + `import scenarios`/`import scorer` keys off those bare
# names, which both harnesses claim; in a shared interpreter (e.g. this module
# imported alongside the trap harness) whichever wins the race silently poisons the
# other's `sys.modules` entry. Loading by explicit file path under a fixed, private,
# cache-checked key sidesteps the collision. `scorer.py` loads its own `scenarios.py`
# under the identical `_vectr_eval_longitudinal_scenarios` key, so this module and
# `scorer` always converge on ONE `scenarios` module object -- and therefore identical
# check-primitive classes -- regardless of which of the two imports first.
_LONGITUDINAL_SCENARIOS_KEY = "_vectr_eval_longitudinal_scenarios"


def _load_local_module(key: str, filename: str):
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, _THIS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scen = _load_local_module(_LONGITUDINAL_SCENARIOS_KEY, "scenarios.py")
scorer = _load_local_module("_vectr_eval_longitudinal_scorer", "scorer.py")

DEFAULT_DAEMON_PORT = 8899
DEFAULT_PROXY_PORT = 8900

# Must match leg_result.schema.json's `arm` / `note_variant` enums exactly.
ARMS = ("none", "mcp", "mcp-bare", "proxy", "hook-sessionstart", "hook-full")
NOTE_VARIANTS = ("none", "plain", "provenance", "verifiable")

# Vectr/IDE-owned paths excluded from the drift-verification manifest and from
# leg_start_baselines -- structural and uniform across every scenario/arm/leg
# (never keyed on query content): `.git` for repeated-hashing efficiency across a
# 4-leg trajectory (no scenario check ever targets git internals); the rest are
# config surfaces `vectr start`/`vectr init` may write that no scenario ever
# declares a check against. This is deliberately NOT arm-conditional -- a real
# scenario-content path such as CLAUDE.md is never excluded, even for arm "mcp"
# (the one arm whose CLAUDE.md vectr may rewrite): if a scenario ever checked it,
# suppressing that byte-level signal would be wrong, and drift verification needs
# no such exclusion since a trajectory's own recorded manifest and its own later
# restore always describe the same, consistently-mutated-or-not state.
_MANIFEST_EXCLUDE_DIRS = (".git", ".claude", ".codex", ".vectr", ".cursor", ".vscode")
_MANIFEST_EXCLUDE_FILES = (".mcp.json",)


# ---------------------------------------------------------------------------
# small process / http helpers -- adapted (not imported) from
# injection_utility/run_harness.py's helpers of the same name. Both directories
# define a module literally named `scenarios.py`; run_harness.py's own top-level
# `import scenarios as scen` would rebind the bare `scenarios` module-cache slot
# out from under this file's own `scenarios` import if the two modules were ever
# loaded into the same process (the identical hazard scenarios.py's own
# `_load_trap_harness_scenarios` docstring documents one level down). Duplicating
# ~80 lines of pure stdlib process/http plumbing is the safe side of that
# tradeoff. `_spawn_env_for_agent` is simplified relative to the trap harness's
# version: every arm here keeps a proxy on the path (see module docstring), so
# there is no `base_url=None` case to handle.
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
    m = _STARTED_PORT.search(text or "")
    return int(m.group(1)) if m else None


def _free_port_at_or_above(start: int, *, avoid: set[int]) -> int:
    for port in range(start, start + 60):
        if port in avoid or _port_pids(port):
            continue
        return port
    raise SystemExit(f"ABORT: no free scratch port at or above {start}")


def _stop_daemon(vectr_bin: str, workspace: Path, port: int, env: dict[str, str]) -> list[int]:
    """`vectr stop` takes `--path`, not `--port` (a `--port` makes argparse exit
    non-zero without stopping anything). The hard-stop fallback closes the gap.
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
    """Env for the spawned eval agent: every CLAUDE*/ANTHROPIC* var is stripped
    first so the child looks like a fresh user invocation rather than a nested
    session of the harness's own; ANTHROPIC_BASE_URL is set back to route through
    the scratch proxy. OAuth still resolves via the keychain.
    """
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
    env["ANTHROPIC_BASE_URL"] = base_url
    return env


# ---------------------------------------------------------------------------
# tree hashing, manifests, tar snapshots
# ---------------------------------------------------------------------------


def _sha256_tree(root: Path, *, extra_ignore: Sequence[str] = ()) -> dict[str, str]:
    """{relpath: sha256} for every regular file under root, skipping the fixed
    vectr/IDE-owned exclusion set plus any scenario-declared `ignore_paths`. Used
    both as `leg_start_baselines` fed to `scorer.score_run`/`leg_metrics` (a
    superset of `scenarios.materialize`'s narrower dict -- also covers files an
    earlier leg's agent created) and as the drift-verification manifest input.
    """
    ignore_files = set(_MANIFEST_EXCLUDE_FILES) | set(extra_ignore)
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _MANIFEST_EXCLUDE_DIRS for part in rel.parts[:-1]):
            continue
        if str(rel) in ignore_files or rel.name in ignore_files:
            continue
        out[str(rel)] = scen.sha256_file(path)
    return out


def _write_manifest(baselines: Mapping[str, str], manifest_path: Path) -> str:
    """Write a sorted `{relpath}  {sha256}` manifest file and return the sha256 OF
    THAT FILE'S BYTES -- a single comparable hash matching
    trajectory_state.schema.json's `end_state_manifest_sha256: string`, while the
    underlying manifest file stays fully inspectable/diffable on disk.
    """
    lines = [f"{rel}  {digest}\n" for rel, digest in sorted(baselines.items())]
    manifest_path.write_text("".join(lines), encoding="utf-8")
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _make_tar(workspace: Path, tar_path: Path) -> None:
    with tarfile.open(tar_path, "w") as tf:
        tf.add(workspace, arcname=".")


def _extract_tar(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r") as tf:
        tf.extractall(dest, filter="data")


def _scrub_ide_config_marker(workspace: Path) -> None:
    """Delete `.vectr/ide_config` (main.py's `_IDE_CONFIG_MARKER_REL`) if a
    restored snapshot carries one forward.

    That marker PERSISTS a workspace's IDE-config choice across separate
    `vectr start` calls for a real interactive user
    (`_persist_ide_config_disabled` / `_ide_config_disabled`, main.py) -- exactly
    the wrong semantics here, where every leg re-passes its own arm-appropriate
    `--no-ide-config` choice on every `vectr start` call. Left in place, leg 1's
    `--no-ide-config` (arm-A conditions, DESIGN.md 5.2) would silently suppress
    arm "mcp"'s CLAUDE.md/.mcp.json auto-write on every one of that trajectory's
    legs, since every trajectory's leg 1 starts from either the shared leg-1 tar
    or (for k>1) its own prior leg's restored snapshot.
    """
    (workspace / ".vectr" / "ide_config").unlink(missing_ok=True)


def _proactive_probe(
    daemon_port: int, leg: "scen.LegSpec", workspace: Path, session_prefix: str,
) -> tuple[dict, dict]:
    """Two daemon-side `/v1/proactive` reachability probes, mirroring
    `injection_utility/run_harness.py`'s `Cell.probe`: turn 1 is text-only (no
    structural anchors exist before any tool call -- `assemble_window` reads paths
    only from tool-input keys, never free text); turn 2 adds the leg's declared
    `probe_files` as file-path anchors. Session ids are synthetic and never
    collide with a real agent session, and this never touches the proxy (a
    daemon-only call), so it cannot consume the proxy's own cooldown ledger.
    """
    base = f"http://127.0.0.1:{daemon_port}/v1/proactive"
    turn1 = _http_json("POST", base, {
        "text": leg.prompt, "file_paths": [], "symbols": [],
        "session_id": f"{session_prefix}-turn1", "channel": "proxy",
    }, timeout=60)
    turn2 = _http_json("POST", base, {
        "text": leg.prompt,
        "file_paths": [str(workspace / p) for p in leg.probe_files],
        "symbols": [],
        "session_id": f"{session_prefix}-turn2", "channel": "proxy",
    }, timeout=60)
    return turn1, turn2


def _enforce_hook_attestation(arm: str, attestation_path: str | None) -> dict | None:
    """DESIGN.md 4: arm `hook-full` (D2) is SKIPPED, never run/scored, without a
    fresh canary attestation -- D2's UserPromptSubmit/PreToolUse additionalContext
    never renders in a stream-json transcript (UPG-IU-HOOK-NONVACUITY-CANARY), so
    delivery must be independently attested rather than read from the transcript.
    """
    if arm != "hook-full":
        return None
    if not attestation_path:
        raise SystemExit(
            "ABORT: --hook-attestation is required for --arm hook-full "
            "(DESIGN.md 4); refusing to run an unattested D2 leg."
        )
    path = Path(attestation_path)
    if not path.is_file():
        raise SystemExit(f"ABORT: --hook-attestation file not found: {path}")
    try:
        att = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ABORT: --hook-attestation is not valid JSON: {exc}")
    if att.get("verified") is not True:
        raise SystemExit(
            f"ABORT: --hook-attestation {path} does not have verified:true; "
            f"arm hook-full is SKIPPED, never run, without a fresh canary attestation."
        )
    return att


# ---------------------------------------------------------------------------
# LegRunner
# ---------------------------------------------------------------------------


class LegRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scenario = scen.get(args.scenario)
        self.seed = args.seed
        self.shared_leg1 = bool(args.shared_leg1)
        self.k = 1 if self.shared_leg1 else args.k
        self.arm = "none" if self.shared_leg1 else args.arm
        self.note_variant = "none" if self.shared_leg1 else args.note_variant
        if not (1 <= self.k <= len(self.scenario.legs)):
            raise SystemExit(
                f"ABORT: scenario {self.scenario.slug!r} has {len(self.scenario.legs)} "
                f"legs; k={self.k} out of range"
            )
        self.leg_spec = self.scenario.legs[self.k - 1]
        # leg_result.schema.json documents trajectory_id as
        # "<scenario>-<arm>-<variant>-s<seed>" -- shared-leg1 has already forced
        # arm="none"/note_variant="none" above, so this is the SAME formula for
        # both modes: shared-leg1 IS conceptually k=1 of the "none" trajectory,
        # which is exactly what every other arm's own leg 1 restores from.
        self.trajectory_id = (
            args.trajectory_id if not self.shared_leg1
            else f"{self.scenario.slug}-none-none-s{self.seed}"
        )

        self.root = Path(args.out_dir if self.shared_leg1 else args.leg_dir).resolve()
        self.workspace = self.root / "workspace"
        self.artifacts = self.root if self.shared_leg1 else (self.root / "artifacts")
        self.verify_dir = self.root / "verify"
        self.audit_log = self.artifacts / "audit.log"
        self.db_dir = (
            (self.root / "_scratch_db") if self.shared_leg1 else Path(args.db_dir).resolve()
        )

        self.daemon_port = args.daemon_port
        self.proxy_port = args.proxy_port
        self.vectr = args.vectr_bin
        self.proxy_proc: subprocess.Popen | None = None
        self._proxy_stderr = None

        self.note_id: int | None = None if self.shared_leg1 else args.planted_note_id
        self.planted_anchor: str | None = None if self.shared_leg1 else args.planted_anchor
        self.audit_offset = 0
        self.leg_start_baselines: dict[str, str] = {}
        self.notes_count_at_start: int | None = None
        self.restored_manifest_ok: bool | None = None
        self.trail_text_delivered: bool | None = None
        self.recall_probe_returned_note: bool | None = None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # leg_result.schema.json documents leg_id as
        # "<stamp>-<scenario>-<arm>-<variant>-s<seed>-k<k>".
        leg_id = f"{stamp}-{self.scenario.slug}-{self.arm}-{self.note_variant}-s{self.seed}-k{self.k}"
        self.record: dict[str, Any] = {
            "leg_id": leg_id,
            "trajectory_id": self.trajectory_id,
            "scenario": self.scenario.slug,
            "arm": self.arm,
            "note_variant": self.note_variant,
            "seed": self.seed,
            "k": self.k,
            "model": args.model,
            "started_utc": stamp,
            "origin": self.scenario.origin,
            # Nullable/optional per schema; pre-populated so every result.json has
            # a stable key set for report.py's reader regardless of which methods
            # this particular leg ends up calling.
            "leg1_id": None,
            "restored_manifest_ok": None,
            "planted_note_id": self.note_id,
            "planted_anchor": self.planted_anchor,
            "notes_in_store_at_start": None,
        }

    # -- small path helper -------------------------------------------------

    def _out(self, name: str) -> Path:
        return self.artifacts / name

    def _daemon_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["VECTR_DB_DIR"] = str(self.db_dir)
        env["VECTR_AUDIT_LOG"] = str(self.audit_log)
        env["VECTR_PORT"] = str(self.daemon_port)
        env["VECTR_WORKSPACE"] = str(self.workspace)
        return env

    def _extra_ignore_paths(self) -> tuple[str, ...]:
        return tuple(self.scenario.ignore_paths)

    def _find_note_variant(self) -> "scen.NoteVariant":
        for v in self.scenario.note_variants:
            if v.variant == self.note_variant:
                return v
        raise SystemExit(
            f"ABORT: scenario {self.scenario.slug!r} has no note_variant "
            f"{self.note_variant!r} (has: {[v.variant for v in self.scenario.note_variants]})"
        )

    # -- setup --------------------------------------------------------------

    def prepare(self) -> None:
        if self.root.exists() and any(self.root.iterdir()) and not self.args.probe_only:
            raise SystemExit(
                f"ABORT: output dir already has content: {self.root} -- resumability "
                f"is the driver's job; it must not re-invoke run_leg.py for a leg "
                f"whose result already exists (DESIGN.md 8)"
            )
        for d in (self.workspace, self.artifacts, self.verify_dir):
            d.mkdir(parents=True, exist_ok=True)

        if self.k == 1 or (self.args.probe_only and not self.args.restore_tar):
            self.leg_start_baselines = scen.materialize(self.scenario, self.workspace)
        else:
            self._restore_and_verify()

        if not self.shared_leg1:
            self.db_dir.mkdir(parents=True, exist_ok=True)

        _scrub_ide_config_marker(self.workspace)
        self._out("baselines.json").write_text(json.dumps(self.leg_start_baselines, indent=2))
        self._out("scenario.json").write_text(json.dumps({
            "slug": self.scenario.slug,
            "title": self.scenario.title,
            "origin": self.scenario.origin,
            "corroborable": self.scenario.corroborable,
            "k": self.k,
            "prompt": self.leg_spec.prompt,
            "primary_check": self.leg_spec.primary_check,
            "checks": [repr(c) for c in self.leg_spec.checks],
            "is_forcing_leg": self.leg_spec.is_forcing_leg,
        }, indent=2))

    def _restore_and_verify(self) -> None:
        tar_path = Path(self.args.restore_tar).resolve()
        if not tar_path.is_file():
            raise SystemExit(f"ABORT: --restore-tar not found: {tar_path}")
        _extract_tar(tar_path, self.workspace)
        _scrub_ide_config_marker(self.workspace)
        baselines = _sha256_tree(self.workspace, extra_ignore=self._extra_ignore_paths())
        manifest_hash = _write_manifest(baselines, self._out("restored-manifest.sha256"))
        expected = self.args.restore_manifest_sha256
        ok = manifest_hash == expected
        self.restored_manifest_ok = ok
        self.record["restored_manifest_ok"] = ok
        if not ok and not self.args.allow_manifest_mismatch:
            raise SystemExit(
                f"ABORT: restored snapshot manifest mismatch for {tar_path} "
                f"(got {manifest_hash}, expected {expected}) -- refusing to continue "
                f"on drifted state; pass --allow-manifest-mismatch to override for "
                f"debugging"
            )
        self.leg_start_baselines = baselines

    def start_daemon(self) -> None:
        if _port_pids(self.daemon_port):
            raise SystemExit(
                f"ABORT: port {self.daemon_port} already in use; refusing to collide "
                f"with an existing daemon"
            )
        cmd = [
            self.vectr, "start", str(self.workspace),
            "--port", str(self.daemon_port), "--memory-only",
        ]
        # Every arm except "mcp" runs with --no-ide-config (the honesty rail: no
        # vectr guidance reaches an agent unless that is the exact channel under
        # test). Arm "mcp" omits it so `vectr start`'s own internal
        # `_maybe_write_workspace_config` writes the real CLAUDE.md/.mcp.json --
        # arm "mcp" gets vectr's actual guidance artifacts, not a synthesized
        # approximation.
        if self.arm != "mcp":
            cmd.append("--no-ide-config")
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=self._daemon_env()
        )
        out = f"{proc.stdout}\n{proc.stderr}"
        self._out("daemon-start.txt").write_text(f"$ {' '.join(cmd)}\nrc={proc.returncode}\n\n{out}")
        actual = _parse_started_port(out)
        if actual is not None and actual != self.daemon_port:
            print(
                f"[run_leg] daemon bound port {actual}, not the requested "
                f"{self.daemon_port}; following the real port", file=sys.stderr,
            )
            self.daemon_port = actual
        if self.daemon_port < 8899:
            raise SystemExit(
                f"ABORT: daemon landed on port {self.daemon_port}, below the 8899 "
                f"scratch floor -- refusing to talk to a possibly-live daemon"
            )
        self.record["daemon_port"] = self.daemon_port
        status = _wait_for(f"http://127.0.0.1:{self.daemon_port}/v1/status", 180.0, "scratch daemon")
        self._out("daemon-status.json").write_text(json.dumps(status, indent=2))
        self.notes_count_at_start = status.get("notes_count")
        self.record["notes_in_store_at_start"] = self.notes_count_at_start

    def plant_note(self) -> None:
        variant = self._find_note_variant()
        payload: dict[str, Any] = {
            "content": variant.content,
            "title": variant.title,
            "kind": variant.kind,
            "priority": variant.priority,
            "tags": list(variant.tags),
            "agent": "longitudinal-harness",
        }
        if variant.trigger_paths:
            payload["triggers"] = [{"path": p} for p in variant.trigger_paths]
        if variant.anchors:
            payload["anchors"] = list(variant.anchors)
        out = _http_json(
            "POST", f"http://127.0.0.1:{self.daemon_port}/v1/remember", payload, timeout=60
        )
        nid = out.get("note_id")
        if not isinstance(nid, int):
            raise SystemExit(f"ABORT: could not plant note: {out}")
        self.note_id = nid
        self.planted_anchor = f"note:{nid}"
        self.record["planted_note_id"] = nid
        self.record["planted_anchor"] = self.planted_anchor

    def probe(self) -> None:
        """Daemon-side reachability probes (DESIGN.md 4.1, 7.3). Never touches the
        proxy -- see `_proactive_probe`'s docstring.
        """
        memory_arm = self.arm != "none"
        if memory_arm and (self.note_id is not None or self.planted_anchor):
            turn1, turn2 = _proactive_probe(
                self.daemon_port, self.leg_spec, self.workspace,
                f"longprobe-{self.scenario.slug}-k{self.k}",
            )
            anchor = self.planted_anchor
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
            self._out("preflight.json").write_text(json.dumps(preflight, indent=2))
            self.record["preflight"] = preflight
            reachable = (
                preflight["turn1_text_only"]["planted_present"]
                or preflight["turn2_with_file_anchor"]["planted_present"]
            )
            self.record["planted_note_reachable_preflight"] = reachable

            variant = self._find_note_variant()
            if variant.variant in ("provenance", "verifiable"):
                trail_text = variant.content.replace(self.scenario.fact_sentence, "", 1).strip()
                found = bool(trail_text) and (
                    trail_text in (turn1.get("context") or "")
                    or trail_text in (turn2.get("context") or "")
                )
                self.trail_text_delivered = found
                self.record["trail_text_delivered"] = found

            if self.arm in ("mcp", "mcp-bare"):
                recall_out = _http_json(
                    "POST", f"http://127.0.0.1:{self.daemon_port}/v1/recall",
                    {"query": self.leg_spec.prompt}, timeout=60,
                )
                notes_text = recall_out.get("notes") or ""
                returned = (f"[#{self.note_id}]" in notes_text) if self.note_id is not None else None
                self.recall_probe_returned_note = returned
                self.record["recall_probe_returned_note"] = returned

            if not reachable and not self.args.allow_unreachable:
                raise SystemExit(
                    "ABORT: the planted note is not retrievable on EITHER daemon-side "
                    "probe, so this leg cannot test its memory channel. Fix the "
                    "scenario (not the score); re-run with --allow-unreachable to "
                    "record it anyway."
                )

        # Everything written up to here is this leg's own preflight traffic; wait
        # for the audit log to settle so non-vacuity counts only what follows
        # (mirrors injection_utility/run_harness.py's `_settled_audit_size`).
        self.audit_offset = self._settled_audit_size()
        self.record["audit_offset_after_preflight"] = self.audit_offset

    def _settled_audit_size(self, *, quiet_s: float = 0.6, limit_s: float = 8.0) -> int:
        deadline = time.time() + limit_s
        last = self.audit_log.stat().st_size if self.audit_log.exists() else 0
        while time.time() < deadline:
            time.sleep(quiet_s)
            now = self.audit_log.stat().st_size if self.audit_log.exists() else 0
            if now == last:
                return now
            last = now
        return last

    def start_proxy(self) -> None:
        self.proxy_port = _free_port_at_or_above(max(self.proxy_port, 8899), avoid={self.daemon_port})
        self.record["proxy_port"] = self.proxy_port
        cmd = [
            self.vectr, "proxy",
            "--port", str(self.proxy_port),
            "--daemon-port", str(self.daemon_port),
            "--path", str(self.workspace),
        ]
        # Only arm "proxy" (arm C) has injection enabled -- DESIGN.md 4. Every
        # other arm, including the hook arms, keeps a transparent proxy on the
        # path (see module docstring).
        if self.arm != "proxy":
            cmd.append("--no-inject")
        stderr_path = self._out("proxy.stderr")
        self._proxy_stderr = stderr_path.open("w")
        self.proxy_proc = subprocess.Popen(
            cmd, stdout=self._proxy_stderr, stderr=subprocess.STDOUT, env=self._daemon_env()
        )
        health = _wait_for(f"http://127.0.0.1:{self.proxy_port}/__vectr_proxy/health", 90.0, "scratch proxy")
        self.record["proxy_instance_id"] = health.get("instance_id")
        self.record["proxy_command"] = " ".join(cmd)

    def write_hooks(self) -> None:
        """Install Claude Code hook entries for arms "hook-sessionstart"/"hook-full".

        `vectr init --hooks` has no CLI-level granularity to install a single hook
        group (main.py's `_write_claude_hooks` always writes all six events) --
        for "hook-sessionstart" (D1), the full set is installed and then this
        method deletes every `.claude/settings.json` hook key except
        "SessionStart" directly. This is a scratch-workspace harness artifact
        edit, the same category as arm "mcp-bare"'s hand-built mcp.json -- not a
        product-code change and not a workaround for a vectr defect.
        """
        cmd = [self.vectr, "init", "--path", str(self.workspace), "--hooks", "--no-ide-config"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=self._daemon_env())
        out = f"{proc.stdout}\n{proc.stderr}"
        self._out("init-hooks.txt").write_text(f"$ {' '.join(cmd)}\nrc={proc.returncode}\n\n{out}")
        settings_path = self.workspace / ".claude" / "settings.json"
        if proc.returncode != 0 or not settings_path.exists():
            raise SystemExit(
                f"ABORT: `vectr init --hooks` did not produce {settings_path} "
                f"(rc={proc.returncode}); see {self._out('init-hooks.txt')}"
            )
        if self.arm == "hook-sessionstart":
            data = json.loads(settings_path.read_text())
            hooks = data.get("hooks") or {}
            for event in list(hooks):
                if event != "SessionStart":
                    del hooks[event]
            data["hooks"] = hooks
            settings_path.write_text(json.dumps(data, indent=2))
            self.record["hooks_pruned_to"] = ["SessionStart"]
        self.record["hooks_settings_path"] = str(settings_path)

    def _mcp_config_path(self) -> Path | None:
        """Path to pass via `--mcp-config` for the two MCP arms, else None (no
        config file at all; `--strict-mcp-config` alone then yields zero servers).

        Arm "mcp" uses vectr's OWN auto-written `<workspace>/.mcp.json` (from
        `start_daemon` omitting `--no-ide-config`) -- the real artifact a user
        gets, not a synthesized approximation. Arm "mcp-bare" (tools, no CLAUDE.md
        guidance) keeps `--no-ide-config`, so nothing is auto-written; this hand-
        builds the identical template `main.py` uses (`{"mcpServers": {"vectr":
        {"type": "http", "url": ...}}}`) into `artifacts/mcp.json`, OUTSIDE the
        workspace so it never pollutes workspace state/manifests.
        """
        if self.arm == "mcp":
            p = self.workspace / ".mcp.json"
            if not p.is_file():
                raise SystemExit(
                    f"ABORT: arm 'mcp' expected vectr to auto-write {p} "
                    f"(start_daemon omits --no-ide-config for this arm) but it is missing"
                )
            return p
        if self.arm == "mcp-bare":
            p = self._out("mcp.json")
            p.write_text(json.dumps({
                "mcpServers": {"vectr": {"type": "http", "url": f"http://localhost:{self.daemon_port}/mcp"}}
            }, indent=2))
            return p
        return None

    # -- the measured run -----------------------------------------------

    def run_agent(self) -> None:
        claude = shutil.which("claude") or "claude"
        cwd = self.workspace if self.scenario.agent_cwd == "." else self.workspace / self.scenario.agent_cwd
        cmd = [
            claude, "-p", self.leg_spec.prompt,
            "--output-format", "stream-json", "--verbose",
            "--model", self.args.model,
            "--max-turns", str(self.args.max_turns),
            "--dangerously-skip-permissions",
            "--strict-mcp-config",
        ]
        mcp_config = self._mcp_config_path()
        if mcp_config is not None:
            cmd += ["--mcp-config", str(mcp_config)]
        transcript = self._out("transcript.jsonl")
        base_url = f"http://127.0.0.1:{self.proxy_port}"
        started = time.time()
        with transcript.open("w") as out:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd), stdout=out, stderr=subprocess.PIPE, text=True,
                env=_spawn_env_for_agent(base_url),
            )
            try:
                _, stderr = proc.communicate(timeout=self.args.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate()
                stderr = (stderr or "") + f"\n[run_leg] timed out after {self.args.timeout_s}s"
        self._out("agent.stderr").write_text(stderr or "")
        self.record["agent_returncode"] = proc.returncode
        self.record["agent_wall_s"] = round(time.time() - started, 1)

    # -- teardown + scoring ------------------------------------------------

    def capture_and_teardown(self) -> None:
        if self.arm in ("hook-sessionstart", "hook-full"):
            status = _http_json("GET", f"http://127.0.0.1:{self.daemon_port}/v1/status", timeout=10)
            self._out("daemon-status-final.json").write_text(json.dumps(status, indent=2))
            self.record["hook_injection_counts"] = status.get("hook_injection_counts")

        health = _http_json("GET", f"http://127.0.0.1:{self.proxy_port}/__vectr_proxy/health", timeout=10)
        self._out("proxy-health.json").write_text(json.dumps(health, indent=2))
        self.record["proxy_metrics"] = health.get("metrics")

        if self.proxy_proc is not None:
            self.proxy_proc.terminate()
            try:
                self.proxy_proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proxy_proc.kill()
        if self._proxy_stderr is not None:
            try:
                self._proxy_stderr.close()
            except Exception:
                pass
        self.record["proxy_hard_killed"] = _hard_stop_port(self.proxy_port)
        self.record["daemon_hard_killed"] = _stop_daemon(
            self.vectr, self.workspace, self.daemon_port, self._daemon_env()
        )

    def score(self) -> None:
        scen.materialize_verifiers(self.leg_spec, self.verify_dir)
        events = scorer.load_transcript(self._out("transcript.jsonl"))
        actions = scorer.build_action_stream(events)

        run_score = scorer.score_run(
            self.leg_spec, workspace=self.workspace,
            leg_start_baselines=self.leg_start_baselines,
            transcript=events, verify_dir=self.verify_dir,
        )
        cost = scorer.cost_metrics(events)
        metrics = scorer.leg_metrics(
            self.leg_spec, events=events, actions=actions, workspace=self.workspace,
            leg_start_baselines=self.leg_start_baselines, k=self.k, origin=self.scenario.origin,
            session_usd=cost.get("session_usd"),
            billable_tokens_session=cost.get("billable_tokens_session"),
        )
        variant = self._find_note_variant() if self.note_variant != "none" else None
        metrics.update(
            scorer.t3_metrics(variant, fact_sentence=self.scenario.fact_sentence, actions=actions)
        )

        nv = scorer.leg_non_vacuity(
            arm=self.arm, k=self.k, events=events,
            notes_count_at_start=self.notes_count_at_start,
            restored_manifest_ok=self.restored_manifest_ok,
            audit_log=self.audit_log, audit_since_offset=self.audit_offset,
            proxy_injected=(self.record.get("proxy_metrics") or {}).get("injected"),
            planted_anchor=self.planted_anchor,
            hook_injection_counts=self.record.get("hook_injection_counts"),
            transcript_path=self._out("transcript.jsonl"),
            planted_note_content=(variant.content if variant is not None else None),
            recall_probe_returned_note=self.recall_probe_returned_note,
            trail_text_delivered=self.trail_text_delivered,
        )

        self.record["score"] = run_score
        self.record["metrics"] = metrics
        self.record["cost"] = cost
        self.record["valid"] = nv["valid"]
        self.record["invalid_reason"] = nv["invalid_reason"]
        self.record["non_vacuity"] = nv["non_vacuity"]

    def snapshot(self) -> None:
        end_baselines = _sha256_tree(self.workspace, extra_ignore=self._extra_ignore_paths())
        tar_path = self.root / ("workspace.tar" if self.shared_leg1 else "end-state.tar")
        manifest_path = self.root / "manifest.sha256"
        _make_tar(self.workspace, tar_path)
        manifest_hash = _write_manifest(end_baselines, manifest_path)
        self.record["end_state_manifest_sha256"] = manifest_hash
        if self.shared_leg1:
            leg1_id = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            (self.root / "leg1_id").write_text(leg1_id)
            self.record["leg1_id"] = leg1_id
        else:
            self.record["end_state_tar"] = str(tar_path)

    def write(self) -> None:
        self._out("result.json").write_text(json.dumps(self.record, indent=2))
        if not self.shared_leg1:
            runs_dir = self.root.parents[2]
            with (runs_dir / "results.jsonl").open("a") as fh:
                fh.write(json.dumps(self.record) + "\n")

    def report(self) -> None:
        r, s, m, cost = self.record, self.record.get("score", {}), self.record.get("metrics", {}), self.record.get("cost", {})
        print("=" * 72)
        print(f"leg {r['leg_id']}")
        print(f"  scenario : {r['scenario']} ({r['origin']})  trajectory: {r.get('trajectory_id')}")
        print(f"  arm      : {r['arm']}   note_variant: {r['note_variant']}   k={r['k']}   model: {r['model']}")
        print(f"  valid    : {r.get('valid')}" + (f"  -- {r.get('invalid_reason')}" if r.get("invalid_reason") else ""))
        print()
        print(f"  PRIMARY CHECK  {s.get('primary_check')} -> fact_used={s.get('fact_used')}")
        for c in s.get("checks", []):
            print(f"    [{'PASS' if c['passed'] else 'FAIL'}] {c['name']:32s} {c['detail']}")
        print()
        print(
            f"  censored={m.get('censored')} mistake_committed={m.get('mistake_committed')} "
            f"turns_to_fact={m.get('turns_to_fact')} tool_calls_to_fact={m.get('tool_calls_to_fact')} "
            f"vectr_tool_calls={m.get('vectr_tool_calls')}"
        )
        print(
            f"  cost: turns={cost.get('session_turns')} tool_calls={cost.get('tool_calls')} "
            f"usd={cost.get('session_usd')} wall={r.get('agent_wall_s')}s"
        )
        print(f"  artifacts: {self.root}")
        print("=" * 72)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=sorted(scen.SCENARIOS))
    ap.add_argument("--seed", type=int, default=0)

    ap.add_argument("--shared-leg1", action="store_true", help=(
        "Run leg 1 under arm-A conditions and write self-contained artifacts to "
        "--out-dir (DESIGN.md 5.2, 8). Mutually exclusive with --k/--arm/--leg-dir/"
        "--trajectory-id/--db-dir/--note-variant/--plant-note."
    ))
    ap.add_argument("--out-dir", help="Output dir for --shared-leg1 mode.")

    ap.add_argument("--k", type=int, help="Leg number (>= 1) for normal mode.")
    ap.add_argument("--leg-dir", help="Output dir for a normal leg, e.g. <runs-dir>/<trajectory>/legs/<k>/.")
    ap.add_argument("--trajectory-id")
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--note-variant", choices=NOTE_VARIANTS, default="none")
    ap.add_argument("--plant-note", action="store_true", help=(
        "Plant the canonical note for --note-variant on THIS leg (exactly once per "
        "trajectory, immediately after leg 1, for every memory arm -- DESIGN.md 5.3)."
    ))
    ap.add_argument("--planted-note-id", type=int, default=None, help=(
        "Pass-through note id for a leg that did not itself plant. Ignored if --plant-note is given."
    ))
    ap.add_argument("--planted-anchor", default=None, help=(
        "Pass-through 'note:<id>' anchor string, same rules as --planted-note-id."
    ))
    ap.add_argument("--db-dir", help="VECTR_DB_DIR to create (if new) or reuse across this trajectory's legs.")
    ap.add_argument("--restore-tar", help="Snapshot tar to restore into workspace/ before this leg (required for k > 1).")
    ap.add_argument("--restore-manifest-sha256", help="Expected manifest sha256 for --restore-tar; mismatch aborts.")
    ap.add_argument("--allow-manifest-mismatch", action="store_true", help=(
        "Continue past a restored-snapshot manifest mismatch instead of aborting (debugging only)."
    ))
    ap.add_argument("--hook-attestation", help=(
        "Path to a JSON {verified,date,method,claude_code_version} file; required for --arm hook-full."
    ))

    ap.add_argument("--daemon-port", type=int, default=DEFAULT_DAEMON_PORT)
    ap.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--vectr-bin", default=shutil.which("vectr") or "vectr")
    ap.add_argument("--allow-unreachable", action="store_true", help=(
        "Record a leg even when the planted note fails both daemon-side preflight probes."
    ))
    ap.add_argument("--probe-only", action="store_true", help=(
        "ZERO-QUOTA: materialize/restore, start the daemon, optionally plant, run "
        "the daemon-side probes, tear down, print a summary. Spawns no agent, writes "
        "no result.json/snapshot. With k > 1 and no --restore-tar, materializes "
        "fresh instead of requiring a real prior-leg snapshot -- the scenario/note "
        "reachability smoke check T0 uses (DESIGN.md 9)."
    ))
    return ap


def _validate_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.shared_leg1:
        if not args.out_dir:
            ap.error("--out-dir is required with --shared-leg1")
        for flag, val in (
            ("--k", args.k), ("--arm", args.arm), ("--leg-dir", args.leg_dir),
            ("--trajectory-id", args.trajectory_id), ("--db-dir", args.db_dir),
            ("--restore-tar", args.restore_tar), ("--planted-note-id", args.planted_note_id),
            ("--planted-anchor", args.planted_anchor),
        ):
            if val is not None:
                ap.error(f"{flag} is not accepted with --shared-leg1")
        if args.note_variant != "none":
            ap.error("--note-variant is not accepted with --shared-leg1 (leg 1 never plants a note)")
        if args.plant_note:
            ap.error("--plant-note is not accepted with --shared-leg1 (leg 1 never plants a note)")
        return

    if args.k is None:
        ap.error("--k is required unless --shared-leg1")
    if args.k < 1:
        ap.error("--k must be >= 1")
    if not args.leg_dir:
        ap.error("--leg-dir is required unless --shared-leg1")
    if not args.arm:
        ap.error("--arm is required unless --shared-leg1")
    if not args.trajectory_id:
        ap.error("--trajectory-id is required unless --shared-leg1")
    if not args.db_dir:
        ap.error("--db-dir is required unless --shared-leg1")
    if args.k > 1 and not args.restore_tar and not args.probe_only:
        ap.error("--restore-tar is required for k > 1 (unless --probe-only)")
    if args.restore_tar and not args.restore_manifest_sha256:
        ap.error("--restore-manifest-sha256 is required together with --restore-tar")
    if args.plant_note and args.arm == "none":
        ap.error("--plant-note is invalid for arm 'none' (arm A never has memory)")
    if args.plant_note and args.note_variant == "none":
        ap.error("--plant-note requires --note-variant plain|provenance|verifiable")
    if args.arm == "proxy" and args.k > 1 and not args.plant_note and not args.planted_anchor and not args.probe_only:
        ap.error(
            "--planted-anchor is required for arm 'proxy' at k > 1 when this leg "
            "does not itself plant the note (needed to match PROACTIVE_INJECT lines)"
        )


def main() -> None:
    ap = _build_argparser()
    args = ap.parse_args()
    _validate_args(ap, args)

    if args.daemon_port < 8899 or args.proxy_port < 8899:
        raise SystemExit("ABORT: scratch ports must be >= 8899 (8765/8800 are live daemons)")

    hook_attestation = _enforce_hook_attestation(
        args.arm if not args.shared_leg1 else "none", args.hook_attestation
    )

    runner = LegRunner(args)
    if hook_attestation is not None:
        runner.record["hook_attestation"] = hook_attestation

    if args.probe_only:
        try:
            runner.prepare()
            runner.start_daemon()
            if args.plant_note:
                runner.plant_note()
            runner.probe()
        finally:
            _stop_daemon(args.vectr_bin, runner.workspace, runner.daemon_port, runner._daemon_env())
        print(
            f"probe-only {runner.scenario.slug} k={runner.k} arm={runner.arm} "
            f"note_variant={runner.note_variant}"
        )
        print(f"  planted_anchor       : {runner.planted_anchor}")
        print(f"  reachable            : {runner.record.get('planted_note_reachable_preflight')}")
        print(f"  trail_text_delivered : {runner.record.get('trail_text_delivered')}")
        print(f"  recall_probe         : {runner.record.get('recall_probe_returned_note')}")
        return

    try:
        runner.prepare()
        runner.start_daemon()
        if args.plant_note:
            runner.plant_note()
        runner.probe()
        runner.start_proxy()
        if runner.arm in ("hook-sessionstart", "hook-full"):
            runner.write_hooks()
        runner.run_agent()
    finally:
        try:
            runner.capture_and_teardown()
        except Exception as exc:  # teardown must never mask a run
            print(f"[run_leg] teardown issue: {type(exc).__name__}: {exc}", file=sys.stderr)
            _hard_stop_port(runner.proxy_port)
            _hard_stop_port(runner.daemon_port)

    runner.score()
    runner.snapshot()
    runner.write()
    runner.report()


if __name__ == "__main__":
    main()
