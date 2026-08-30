#!/usr/bin/env python3
"""Re-measure the README claims that need no daemon and no model.

Covers C01 (MCP tool count) and C02 (embedding model download size). Every
other claim needs either a live daemon (see measure_live.py) or agent
sessions (see the sprint harness), because a number you cannot reproduce
from a cold checkout is not a measurement.

Output is JSON on stdout plus a readable table on stderr, so this can be
piped into a report or read directly.

Usage:
    python3 benchmarks/readme_claims/measure_static.py
    python3 benchmarks/readme_claims/measure_static.py --json-only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _fail(claim_id: str, why: str) -> dict:
    """A measurement that could not run is NOT a passing measurement.

    Recording 'unknown' rather than silently omitting the row is the whole
    point: a claim nobody could re-measure is exactly how the README drifted
    in the first place.
    """
    return {"id": claim_id, "measured": None, "ok": False, "why": why}


def measure_tool_count() -> dict:
    """C01: how many vectr_* tools does the MCP surface actually declare."""
    schema = REPO / "integrations" / "mcp_server" / "_schemas.py"
    if not schema.is_file():
        return _fail("C01", f"schema not found at {schema}")
    text = schema.read_text(encoding="utf-8", errors="replace")
    # The tool list is the set of distinct vectr_* identifiers appearing as a
    # declared tool name. Counting distinct names rather than occurrences,
    # since a name is referenced from both the schema and the dispatch table.
    names = sorted(set(re.findall(r"\bvectr_[a-z_]+\b", text)))
    return {
        "id": "C01",
        "measured": len(names),
        "claimed": 22,
        "ok": True,
        "names": names,
        "source": str(schema.relative_to(REPO)),
    }


def _snapshot_bytes(model_dir: Path) -> int:
    """Bytes a FRESH install actually downloads for one model.

    Sizing the whole cache directory is wrong and overcounts badly: a
    HuggingFace cache keeps `blobs/` alongside `snapshots/`, can hold several
    revisions, and often carries both safetensors and a .bin of the same
    weights. Measuring the resolved files of the current snapshot is what a
    new user actually pays. Snapshot entries are symlinks into blobs, so
    follow them (stat, not lstat).
    """
    snaps = model_dir / "snapshots"
    if not snaps.is_dir():
        return 0
    total = 0
    for f in snaps.rglob("*"):
        try:
            if f.is_file():          # is_file() follows symlinks
                total += f.stat().st_size
        except OSError:
            continue                 # a dangling link is not a download
    return total


def _configured(key_path: list[str]) -> str | None:
    """Read a dotted key out of agent/config.yaml without a yaml dependency."""
    cfg = REPO / "agent" / "config.yaml"
    if not cfg.is_file():
        return None
    depth = 0
    want = list(key_path)
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if depth < len(want) - 1:
            if line.lstrip().startswith(want[depth] + ":") and indent == depth * 2:
                depth += 1
            continue
        m = re.match(rf"\s*{re.escape(want[-1])}:\s*['\"]?([\w\-./]+)", line)
        if m:
            return m.group(1)
    return None


def measure_model_size() -> dict:
    """C02: what a fresh install downloads.

    The README claims "about 290 MB", which is the EMBEDDER alone. vectr now
    ships a cross-encoder reranker by default too, and that is a second
    download on first run, so the user-facing number is the sum.
    """
    cache = Path.home() / ".cache" / "vectr" / "models"
    if not cache.is_dir():
        return _fail("C02", f"model cache not present at {cache}; "
                            "run an index once to populate it")

    embed = _configured(["embedding", "model"]) or "ibm-granite/granite-embedding-english-r2"
    rerank = _configured(["ranking", "rerank", "model"]) or "Alibaba-NLP/gte-reranker-modernbert-base"

    def _find(model_id: str) -> tuple[str | None, int]:
        slug = "models--" + model_id.replace("/", "--")
        d = cache / slug
        if d.is_dir():
            return slug, _snapshot_bytes(d)
        return None, 0

    e_dir, e_bytes = _find(embed)
    r_dir, r_bytes = _find(rerank)
    missing = [n for n, d in ((embed, e_dir), (rerank, r_dir)) if d is None]
    if missing:
        return _fail("C02", "not in the local cache, so its download size "
                            f"cannot be measured here: {', '.join(missing)}")

    mb = lambda b: round(b / (1024 * 1024), 1)
    return {
        "id": "C02",
        "measured_mb": mb(e_bytes + r_bytes),
        "claimed_mb": 290,
        "ok": True,
        "breakdown": [
            {"role": "embedding", "model": embed, "mb": mb(e_bytes)},
            {"role": "reranker", "model": rerank, "mb": mb(r_bytes)},
        ],
        "note": ("the claimed 290 MB matches the EMBEDDER alone; a fresh "
                 "install also pulls the default reranker"),
        "method": "resolved files under snapshots/, symlinks followed",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-only", action="store_true",
                    help="suppress the human-readable table on stderr")
    args = ap.parse_args(argv)

    results = [measure_tool_count(), measure_model_size()]

    if not args.json_only:
        print("README static claims", file=sys.stderr)
        print("=" * 64, file=sys.stderr)
        for r in results:
            if not r.get("ok"):
                print(f"  {r['id']}  UNMEASURED  {r['why']}", file=sys.stderr)
                continue
            if r["id"] == "C01":
                verdict = "MATCHES" if r["measured"] == r["claimed"] else "STALE"
                print(f"  C01  MCP tools        claimed {r['claimed']}  "
                      f"measured {r['measured']}  {verdict}", file=sys.stderr)
                if verdict == "STALE":
                    print(f"       names: {', '.join(r['names'])}", file=sys.stderr)
            elif r["id"] == "C02":
                delta = r["measured_mb"] - r["claimed_mb"]
                verdict = "MATCHES" if abs(delta) <= 15 else "STALE"
                print(f"  C02  first download   claimed {r['claimed_mb']} MB  "
                      f"measured {r['measured_mb']} MB  {verdict}", file=sys.stderr)
                for b in r["breakdown"]:
                    print(f"       {b['role']:<10} {b['mb']:>7} MB  {b['model']}",
                          file=sys.stderr)
                print(f"       note: {r['note']}", file=sys.stderr)
        print("=" * 64, file=sys.stderr)

    json.dump({"results": results}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
