# ARCH-4-DEBUG diagnosis checkpoint (working notes, in-progress)

Worktree: loop/arch4-debug @ c77beea. Live daemon queried (read-only) at
localhost:8766, serving tmp/vectr-accept-django fixture. Chroma db at
~/.cache/vectr/13b246952102/chroma (code_chunks: 40417, code_chunks_purpose: 38163).
Embedder: Snowflake/snowflake-arctic-embed-m-v1.5, loaded directly via
agent.indexer._types.get_embed_provider for offline cosine spikes (no daemon
restart, no reindex).

## Method

For each of the 3 witness chunks: fetched stored body doc + stored purpose doc
from Chroma by chunk id, confirmed `build_purpose_text(body, symbol, node_type,
"python")` reproduces the stored purpose doc byte-for-byte (write path is
deterministic — no serialization/encoding bug), then embedded query + body +
stored-purpose + candidate-alt-purpose texts and compared cosines. Also ran
`purpose_collection.query()` / `body_collection.query()` with n_results=200 to
get the ACTUAL pool rank (not just an absolute cosine number) for each id.

## Case 1 — QuerySet.get-shaped symbol (method w/ single-paragraph docstring, F23)

Chunk: query.py:660-693, symbol_name `get`, class-qualified via `# class:` prefix line.
Query under test: "get object by primary key" (product_cases.jsonl id
F23-shortverb-get-floods-cache-not-queryset).

- Stored purpose doc: `"QuerySet.get\ndef get(self, *args, **kwargs):\nPerform the query and return a single object matching the given\n        keyword arguments."`
- `build_purpose_text` rebuild == stored doc (verified, no drift).
- cos(query, body)            = 0.5797
- cos(query, purpose_stored)  = 0.6832
- Purpose NOT in purpose-collection top-200 (top-1 = 0.808, unrelated
  fixture-model classes literally named `PrimaryKeyWith*` win on lexical overlap
  with "primary key").
- Body NOT in body-collection top-200 either.
- Cross-check against the STEP-0 spike's ORIGINAL tested phrasings on this
  exact stored purpose text (not a re-derivation — literal reuse of the
  productized text):
  - "get a single object from the database" -> 0.7033 (spike: 0.706, matches)
  - "fetch one row matching criteria"        -> 0.5948 (spike: 0.606, matches)
  - "retrieve a single record by lookup"     -> 0.6260 (spike: 0.625, matches)
  - "return exactly one matching object..."  -> 0.6806 (spike: 0.678, matches)
- **Conclusion: NO productization delta for this symbol.** The write path
  matches the STEP-0 spike almost exactly on the spike's own phrasings. The
  acceptance-case phrasing "get object by primary key" was never one of the
  spike's tested phrasings and is objectively harder: the canonical docstring
  ("Perform the query and return a single object matching the given keyword
  arguments") never mentions "primary key" (Django's `.get()` is a generic
  filter-by-any-kwarg method), while the corpus has many `PrimaryKeyWith*`
  test-fixture model classes whose class name literally contains the query's
  words. This is a genuine embedding-space competition problem, not a
  distillation/truncation bug. Pool entry is NOT reached for this specific
  phrasing even with a byte-perfect spike-equivalent purpose text.

## Case 2 — large-class docstring w/ multi-paragraph structured body (F44, Signal-shaped)

Chunk: dispatcher.py:68-533 (`class_definition`, symbol `Signal`). Note: the
STORED body text for a class-level chunk is already capped upstream at
`indexing.class_header_lines` (=40 lines) — this is NOT "a chunk spanning
hundreds of lines" reaching the embedder; the chunker already truncates class
bodies before ARCH-4 ever sees them. What actually reaches
`build_purpose_text` is a ~39-line header (class def + full docstring +
truncated `__init__`/`connect` signature spillover).

Query under test: "signal dispatcher implementation".

- Stored purpose doc (596 chars, hits `max_docstring_chars=600` mid-token):
  `"Signal\nclass Signal:\nBase class for all signals\n\n    Internal attributes:\n\n        receivers:\n            [\n                (\n                    (id(receiver), id(sender)),\n                    ref(receiver),\n                    ref(sender),\n                    is_async,\n                )"`
- `build_purpose_text` rebuild == stored doc (verified).
- cos(query, body)           = 0.6689 — body rank 94/200 in body collection (in pool, but deep).
- cos(query, purpose_stored) = 0.7181 — purpose rank: **NOT in top-200** (worse
  than body despite being the "purpose" vector!).
- Candidate fix — same qualified name + signature, but docstring cut at the
  FIRST BLANK LINE (i.e. just the PEP-257 summary sentence "Base class for all
  signals") instead of raw line/char budget:
  `"Signal\nclass Signal:\nBase class for all signals"`
  -> cos(query, purpose_alt) = **0.7918**.
  Re-ranked against the same 200-deep purpose pool: this places at
  **rank ~6/200** (pool floor at rank 200 = 0.726, rank 60 = 0.743) —
  comfortably inside both the pre-filter fetch depth AND the rerank pool
  (top_k_unfiltered=60).
- **Root cause identified (real productization/design gap, general, not
  corpus-specific): docstring truncation is line-count/char-count based and
  truncates MID-STRUCTURE for PEP-257/Google/NumPy-style multi-paragraph
  docstrings** (summary line, blank line, structured details section e.g.
  "Args:"/"Internal attributes:"/attribute-list blocks). The raw truncation
  keeps low-signal structured boilerplate ("Internal attributes: receivers: [
  (id(receiver)...") ahead of/instead of prioritizing the high-signal summary
  sentence, which is exactly the kind of embedding dilution ARCH-4 exists to
  defeat — just recurring one level down, WITHIN the purpose text itself for
  chunks with structured multi-paragraph docstrings.
- Fix approach: extract only the FIRST PARAGRAPH of the docstring (up to the
  first blank line) as the default purpose-text docstring; existing
  max_docstring_lines/max_docstring_chars remain a safety-net cap on that
  paragraph (handles the case of a very long single-paragraph summary with no
  blank line). This is a structural convention (PEP 257: summary line, blank
  line, elaboration), not a keyword rule, and degrades safely for docstrings
  with no blank line (unchanged behavior — most existing single-paragraph
  docstrings, including Case 1 and Case 3's own summary line, are unaffected
  or improved).

## Case 3 — short module-level function, 3-paragraph docstring (F48, get_object_or_404-shaped)

Chunk: shortcuts.py:79-107, symbol `get_object_or_404`, function_definition,
no class context (bare name, no `Class.` prefix). Query under test: "shortcut
to get an object or raise 404".

- Stored purpose doc includes the FULL 3-paragraph docstring (short enough
  that neither the 12-line nor 600-char cap ever engages — no truncation at
  all here, write path is not "buggy" in the truncation sense for this chunk).
- cos(query, body)           = 0.5374 — NOT in body top-200.
- cos(query, purpose_stored) = 0.6219 — NOT in purpose top-200 (top-1 = 0.7595).
- Candidate: first-paragraph-only purpose text (drop paragraphs 2 ["klass may
  be a Model, Manager, or QuerySet..."] and 3 ["Like with QuerySet.get()..."]):
  `"get_object_or_404\ndef get_object_or_404(klass, *args, **kwargs):\nUse get() to return an object, or raise an Http404 exception if the object\n    does not exist."`
  -> cos(query, purpose_alt) = 0.6928 (+0.071 over the current 3-paragraph
  text — confirms the SAME dilution principle: even short, correct elaboration
  paragraphs measurably dilute the summary-sentence signal).
  Re-ranked against the 200-deep purpose pool for this query: **still NOT in
  top-200** (200th-place sim = 0.7239 > 0.6928). The fix helps (proves the
  general mechanism generalizes to this shape too) but is NOT sufficient on
  its own to reach pool entry for this specific adversarial phrasing — same
  class of problem as Case 1: a hard phrasing where many unrelated corpus
  chunks score higher on pure lexical/semantic proximity ("404", "object",
  admin/test fixtures) than the true canonical answer. Root cause is
  genuinely a HARD QUERY for embedding-only retrieval, not a fixable
  write-path defect; the first-paragraph fix is still the right general
  change (proven win on Case 2, non-regressing/marginal-improvement on Cases 1
  and 3), and pool-entry-completeness for Cases 1/3 is an evidence-gated
  finding to report honestly, not force with a corpus-specific hack.

## Summary verdict

- **Real, general, fixable write-path defect found and being fixed:** the
  docstring capture in `agent/chunk_quality.py` (`_extract_python_docstring`)
  truncates by raw line/char budget instead of by paragraph (PEP-257 summary
  convention), diluting the purpose vector for any structured multi-paragraph
  docstring. Confirmed the fix moves Case 2 (Signal-shaped) from
  NOT-IN-TOP-200 to rank ~6/200 — clears pool entry with real evidence, not
  guesswork.
- Cases 1 and 3 do NOT show a productization delta vs. the STEP-0 spike (write
  path already matches spike behavior for spike-tested phrasings); their
  specific acceptance-case phrasings are genuinely hard for a pure dense
  embedding match regardless of purpose-text quality — reported honestly as a
  parallel, NOT dual-vector-write-path, gap (candidate for a
  lexical-boost/BM25-fusion or class-importance follow-on, out of this task's
  scope per the acceptance bar: "in-pool promotion is a parallel task").
- **Also found while investigating (separate, smaller general bug):** the
  `leading_doc` (non-Python leading comment/decorator block, e.g. JSDoc) branch
  of `build_purpose_text` is currently UNCAPPED — no max_lines/max_chars limit
  is applied to it at all, unlike the Python docstring branch. Same dilution
  risk for long JSDoc/rustdoc blocks. Fixing alongside for consistency (general
  mechanism, same config-driven caps, not corpus-specific).

## F50 (query_router.py `_CALL_GRAPH` bare "implementation(s)" trigger) — FIXED

`agent/query_router.py`: replaced the single alternation
`(implement(ors?|ations?)|extend(s|ed by)|subclass(es)?|override)` with
targeted patterns — `implementations? of`, `implements? the interface`, and
`implements?` folded into the existing "who (calls|uses|invokes)" verb group —
plus unchanged `extend(s|ed by)`/`subclass(es)?`/`override` entries. Verified
before/after with the live router:

| query | before | after |
|---|---|---|
| "caching framework implementation" | CALL_GRAPH (w=0.55) | SEMANTIC (w=0.70) |
| "signal dispatcher implementation" | CALL_GRAPH (w=0.55) | SEMANTIC (w=0.70) |
| "who implements the Comparable interface" | SEMANTIC (w=0.70) | CALL_GRAPH (w=0.55) |
| "implementations of PaymentProcessor" | CALL_GRAPH (w=0.55) | CALL_GRAPH (w=0.55, unchanged) |
| "implements the interface" | SEMANTIC (w=0.70) | CALL_GRAPH (w=0.55) |
| "implement a new caching layer" | SEMANTIC (w=0.70) | SEMANTIC (w=0.70, unchanged) |

Tests added in `tests/test_query_router.py` (`TestClassify`): 2 new SEMANTIC
non-regression cases for the bare noun, 3 new CALL_GRAPH cases for the kept
structural phrasings.

**Compounding effect on Case 2 (Signal-shaped, F44):** the misrouting bug
directly hurt that same query — "signal dispatcher implementation" used to be
misrouted to CALL_GRAPH, cutting `semantic_weight` 0.70→0.55, i.e. reducing
the dense (body ⊔ purpose) vector's contribution to the final hybrid score
right when the purpose-vector rescue needed its full weight. Fixing F50 and
the docstring-distillation defect together should compound favourably for
that acceptance case; the sentinel's post-merge corpus gate re-run will
confirm the combined effect end-to-end.

## Implemented fixes (this task)

1. `agent/chunk_quality.py` — `_extract_python_docstring` now keeps only the
   FIRST PARAGRAPH of a docstring (`_first_paragraph()`, splits at the first
   blank line) before applying the existing `max_docstring_lines`/
   `max_docstring_chars` safety-net caps. General, structural (PEP-257
   summary-line convention), degrades to previous behavior for single-paragraph
   docstrings (the common case, and Case 1/3's own summary sentence).
2. `agent/chunk_quality.py` — `build_purpose_text`'s non-Python `leading_doc`
   branch (JSDoc/rustdoc/godoc header blocks) is now capped by the same
   `max_docstring_lines`/`max_docstring_chars` limits — previously UNCAPPED,
   a separate general bug found during this investigation (same dilution risk
   class, just for non-Python leading-comment chunks instead of Python
   docstrings).
3. `agent/indexer/_constants.py` — `INDEXING_SCHEMA_VERSION` bumped 2 → 3
   (purpose-text content changed; existing purpose vectors are stale relative
   to a fresh index and must be rebuilt — reuses the existing schema-version
   mtime-cache-invalidation mechanism, no new code path).
4. `agent/query_router.py` — F50 fix, see above.

## Verified real-embedder evidence AFTER the fix (same live daemon Chroma db, read-only)

| case | query | old purpose cos | new purpose cos | new purpose pool rank (of live 200-deep pool) |
|---|---|---|---|---|
| Signal-shaped class def (F44) | "signal dispatcher implementation" | 0.7181 | **0.7918** | **~6/200** — clears BOTH pre-filter fetch depth (200) and rerank pool (top_k_unfiltered=60) |
| QuerySet.get-shaped method (F23) | "get object by primary key" | 0.6832 | 0.6832 (unchanged — single-paragraph docstring, no blank line to split on) | still not in top-200 |
| get_object_or_404-shaped function (F48) | "shortcut to get an object or raise 404" | 0.6219 | 0.6928 (+0.071) | still not in top-200 (200th-place sim 0.7239) |

**Honest conclusion:** the docstring-distillation defect is real, general, and
now fixed with a directly-measured pool-entry win for the Signal-shaped case
(F44) — this is the one case where the productized purpose text WAS
measurably worse than what the mechanism could deliver, i.e. a genuine
write-path defect. The other two cases (F23, F48) were re-verified against the
STEP-0 spike's OWN tested phrasings and matched it almost exactly — i.e. no
productization delta vs. the spike for those phrasings. Their specific
acceptance-case query phrasings ("get object by primary key", "shortcut to get
an object or raise 404") are objectively harder than anything the spike
tested: they lose to unrelated corpus chunks that have strong LEXICAL overlap
with the query words (test-fixture model classes literally named
`PrimaryKeyWith*`; admin/test fixtures scoring 0.72-0.76 on "404"/"object").
The first-paragraph fix still measurably helps F48 (+0.071, proving the
mechanism generalizes) but is not sufficient alone to reach pool entry for
that adversarial phrasing — promoting these into the pool is a different,
parallel lever (lexical/BM25 fusion recall, or class/file-importance
promotion), consistent with the acceptance bar's own framing that "in-pool
promotion is a parallel task."

## Status: DONE. Full suite run next, then commit.
