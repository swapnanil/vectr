# vectr acceptance corpus

The permanent, growing set of reproducible cases that guard vectr's two quality surfaces. Seeded from the eval-v2 N=1 audit (F1–F4, 2026-06-20). The reviewers run these every loop and append every new fault they find. A case is the unit of "done" — coder turns a `failing` case `green`; reviewers turn new defects into `failing` cases.

## Files
- **`product_cases.jsonl`** — offline retrieval cases. One JSON object per line, run by `vectr-product-reviewer` against a live daemon over REST (zero quota) or the MCP JSON-RPC surface (the surface the real caller LLM actually sees). Schema below.
- **`adoption_cases.md`** — caller-behavior cases (metrics + thresholds), run by `vectr-adoption-reviewer` via transcript replay (free) or a live scenario (gated).

## product_cases.jsonl schema
```json
{
  "id": "F1",                       // stable id
  "query": "Field deconstruct ...", // search query
  "language": null,                  // optional language filter, or null
  "n_results": 5,
  "corpus": "django",               // which fixture daemon to hit
  "expect": {                        // assertions (any subset)
    "top_k_contains": {"k": 3, "file": "django/db/models/fields/__init__.py", "symbol": "Field.deconstruct"},
    "top_k_absent":   {"k": 5, "symbol": "RemoveField.deconstruct"},
    "sorted_by_score": true,         // results must be monotonic non-increasing by displayed score
    "low_confidence_absent": true,   // MCP only: the Low confidence banner must NOT fire (UPG-ACCEPTANCE-MCP-MODE)
    "body_present": true             // MCP search only: the rank-1 result must carry a code body, not a pointer
  },
  "origin": "eval-v2 N=1 audit F1; live-reproduced on 8792",
  "upg": "UPG-11.1",
  "status": "failing",              // failing | green | passing (synonym of green)
  "embed_model_stamp": "...",       // embed model the expect was last verified under
  "corpus_revision_stamp": "..."    // witness revision the expect was last verified against:
                                    //   git SHA of the external corpus checkout (full or >=7-char hex),
                                    //   "in-repo" — inputs are fixture files versioned by this repo,
                                    //   "unknown" — verifying revision could not be established.
                                    // Never guess a SHA: a wrong stamp manufactures false attribution.
}
```

### Surface-mode assertions (UPG-ACCEPTANCE-MCP-MODE)

`low_confidence_absent` and `body_present` are only meaningful in MCP mode
(the JSON-RPC surface the real caller LLM actually sees). On a REST run
both are silently skipped with a `[SKIP] ... only meaningful on the MCP
surface` line per case — a known information gap, not a pass. Silently
passing them on REST would be worse: the assertion is structurally
uncheckable there (the low-confidence banner signal and the pointer-mode
rendering are both MCP-only).

A case that ONLY carries these MCP-only assertions on a REST run is
counted in the run's "MCP-only assertion(s) skipped" summary line.

### Surface selection (UPG-ACCEPTANCE-MCP-MODE)

`run_acceptance.py --surface {rest,mcp}` (default `rest`).
- `--surface rest` (legacy) — POST /v1/search, /v1/locate, /v1/status. Bit-for-bit the previous behaviour.
- `--surface mcp` — JSON-RPC POST /mcp with `tools/call` envelopes for `vectr_search` / `vectr_locate`. The surface the real caller LLM actually sees. The `/v1/status` probe stays on REST either way (status is surface-agnostic).

The default is preserved as REST so no existing script silently changes
meaning. To exercise the MCP delivery defects the rest of the corpus
cannot see (F44 / F47 / F74 in particular), run with `--surface mcp`.

### Corpus-revision stamping (UPG-CORPUS-REVISION-STAMP)

A rank label is only interpretable against the corpus bytes it was produced
on. `corpus_revision_stamp` records that revision so a passing→failing flip
is attributable: the harness resolves the served workspace's actual revision
from `/v1/status` `workspace_root` (`git rev-parse HEAD`) plus its dirty state
(`git status --porcelain`) and prints `[REVISION MISMATCH]` / dirty notices
when they disagree with the stamp. Print-only, never a FAIL — same severity
contract as `embed_model_stamp`: it flags a label needing re-verification,
not a product defect. A non-git workspace or unavailable git degrades to a
reported unknown, never to a silent pass. When you (re)verify a case's
expectations, set its stamp to the served revision of THAT run.

### `status` is machine-checked under `--strict-status` (UPG-ACCEPTANCE-MCP-MODE)

Each case's `status` field is documentation pretending to be data — a
label the harness never compared against the observed outcome, and four
labels in this corpus are known to have drifted from reality unnoticed.
`run_acceptance.py --strict-status` (default off) makes the harness
compare the case's recorded `status` against the actual run outcome:
`passing`/`green` recorded vs PASS observed, `failing` recorded vs FAIL
observed. A mismatch is reported as `[STATUS DRIFT]` per case and counted
as a fail (so a reviewer can't land a drifted label as a passing test
gate). Manual cases and skipped cases are not compared.

This is opt-in so the legacy run-mode keeps the legacy verdict; flip
`--strict-status` only after re-stamping the drifted labels (see
`LANE-REPORT.md` for the four cases the reviewer pass found drifted).

## Daemon setup (product cases)
- Dedicated acceptance port **8799**, global binary `/opt/homebrew/bin/vectr`.
- Index from a copy under **`vectr/tmp/`** (e.g. `vectr/tmp/vectr-accept-django`) — that dir is in `.gitignore` + `.vectrignore`, so the always-on 8765 daemon skips it and macOS won't evict it (don't use `/tmp` — cleared after 3 days). NEVER index `benchmarks/django` in place or anywhere else under `fde/` — the 8765 daemon will runaway-reindex.
- `POST http://localhost:8799/v1/search {query, language?, n_results}` → results with `file, lines, symbol, language, score, content`. `/v1/status` reports indexed `languages` (used by coverage cases like F2). The MCP equivalent is `POST /mcp` with `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"vectr_search","arguments":{"query":...,"n_results":...}}}` after an `initialize` handshake that returns a `Mcp-Session-Id` header (the harness does this once per run).

## Lifecycle
`failing` → coder fixes root cause + adds unit test → product-reviewer independently confirms green via REST → sentinel flips `status` to `green` at merge. Green cases stay forever as regression guards. Never delete a case to make the suite pass.
