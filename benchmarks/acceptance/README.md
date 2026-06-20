# vectr acceptance corpus

The permanent, growing set of reproducible cases that guard vectr's two quality surfaces. Seeded from the eval-v2 N=1 audit (F1–F4, 2026-06-20). The reviewers run these every loop and append every new fault they find. A case is the unit of "done" — coder turns a `failing` case `green`; reviewers turn new defects into `failing` cases.

## Files
- **`product_cases.jsonl`** — offline retrieval cases. One JSON object per line, run by `vectr-product-reviewer` against a live daemon over REST (zero quota). Schema below.
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
    "sorted_by_score": true          // results must be monotonic non-increasing by displayed score
  },
  "origin": "eval-v2 N=1 audit F1; live-reproduced on 8792",
  "upg": "UPG-11.1",
  "status": "failing"               // failing | green
}
```

## Daemon setup (product cases)
- Dedicated acceptance port **8799**, global binary `/opt/homebrew/bin/vectr`.
- Index from a copy under **`vectr/tmp/`** (e.g. `vectr/tmp/vectr-accept-django`) — that dir is in `.gitignore` + `.vectrignore`, so the always-on 8765 daemon skips it and macOS won't evict it (don't use `/tmp` — cleared after 3 days). NEVER index `benchmarks/django` in place or anywhere else under `fde/` — the 8765 daemon will runaway-reindex.
- `POST http://localhost:8799/v1/search {query, language?, n_results}` → results with `file, lines, symbol, language, score, content`. `/v1/status` reports indexed `languages` (used by coverage cases like F2).

## Lifecycle
`failing` → coder fixes root cause + adds unit test → product-reviewer independently confirms green via REST → sentinel flips `status` to `green` at merge. Green cases stay forever as regression guards. Never delete a case to make the suite pass.
