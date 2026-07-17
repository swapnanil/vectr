# bm5 — re-exploration waste measurement

How much does an agent re-pay for content it already retrieved earlier
in the same session — and how much of that waste sits at compaction
boundaries? The literature quantifies neither; this directory defines
the metric and measures it over the nine BM-4 pilot transcripts.

Headline (paper §5.5): **61 intra-session re-reads, of which 24 — 39%
— re-read content the session had already read before a compaction
boundary**; ~78k result tokens re-paid (chars/4 proxy); pooled
re-exploration share 10.9% of exploration calls (range 0–29.1%); the
worst single run re-paid ~31.6k tokens. The three heaviest runs hold
55 of 61 re-reads and 21 of 24 cross-compaction re-reads (the
remaining three sit in a fourth compacted run); every run compacted
at least once, yet four finished with zero re-reads — waste
concentrates at context-loss boundaries when it occurs.

## Files

- [`definition.md`](definition.md) — the metric definition the
  analyzer implements (what counts as a re-read, the R-class
  taxonomy, the honesty ledger of known blind spots).
- [`analyzer.py`](analyzer.py) — the analyzer, v1:

  ```
  python3 analyzer.py <transcript.jsonl> [...] [--json out.json]
  ```

  v1 improvements over v0 (documented in the file header): compound
  Bash commands are tokenized quote-aware and split on `&&`/`||`/`;`/`|`
  with each read-only segment classified independently; shell-side
  mutations (`>`, `sed -i`, `tee`, `mv`, `cp`, `rm`) reset the read
  ledger so verification reads after edits don't count; waste is
  attributed conservatively (a call contributes zero waste unless
  *every* classified read segment is a repeat).
- [`v0-baseline.json`](v0-baseline.json) /
  [`v1-results.json`](v1-results.json) — per-run outputs of both
  analyzer versions over the pilot transcripts (the paper cites v1;
  v0 is retained to show the definition's sensitivity to the
  compound-command handling).
- [`inventory.md`](inventory.md) — which transcripts were analyzed.

## Known limits (v1 floor)

Commands containing command substitution (`$( )`, backticks) stay
opaque; relative-path aliasing across `cd` is not resolved; token
costs use a chars/4 proxy over tool-result text. All three make the
measurement a floor, not a ceiling. Transcripts live in
[`../../proactive-gate/results/`](../../proactive-gate/results/)
(large files gzip-compressed).
