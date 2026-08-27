# Changelog

## 1.11.0 - 2026-08-27

A stored note can be retired after the fact without the false "proven wrong"
framing, and reinstating one actually reverses it instead of leaving a zombie;
anchors can be attached to a note that already exists, and vectr now says
whether the writing agent ever actually read the file it anchored; working
memory becomes editable in your own `$EDITOR` through the append-only
lifecycle rather than around it; the default reranker changes model; a
low-confidence result set stops withholding the body of its own best guess;
Rust trait impls stop being indexed under the wrong name; and a long single
line stops defeating every chunk size cap.

### Working memory

- Post-hoc retirement, distinct from revocation. `vectr_supersede` and
  `POST /v1/supersede` retire a stored note without deleting it and without
  revoke's "proven wrong" framing. A new `superseded_post_hoc` event kind
  folds to the existing `superseded` state, so the audit trail can tell
  post-hoc retirement from write-time supersession while recall exclusion,
  trigger firing, the export mirror and badge rendering all behave
  identically. The successor is validated before any mutation;
  self-supersession and `actor="system"` are rejected, and an agent-actor
  call may not retire a human-provenance note.
- Reinstatement now genuinely reverses. `reinstate_note()` appended its event
  and folded state back to `active` while leaving `valid_until`,
  `superseded_at` and `superseded_by_note_id` set, so a reinstated note
  stayed excluded from default recall and could never fire again: a zombie,
  against a docstring promising reversal. Those columns are now cleared when
  the pre-fold state is `superseded`. This changes write-time `supersedes=`
  behavior too, not only post-hoc retirements.
- Anchors attach to an existing note. `vectr_anchor`, `POST /v1/anchor` and
  `vectr anchor --id N PATH...` add file anchors to a stored note without
  re-storing it, using `remember()`'s own pair format and write-time hashing.
  Idempotent, emits no lifecycle event, and rejects an empty anchor list on
  every surface. The proxy-anchor suggestion copy now advertises it instead
  of the re-store-with-`supersedes` workaround.
- An anchor records whether the file was actually observed. `remember()`
  hashed every declared anchor unconditionally, storing vectr's own read of
  the file rather than evidence the writing agent ever opened it, so a note
  anchored to a file nobody read rendered identically to a properly bound
  one. A per-session in-memory observation ledger, populated exclusively from
  the `PreToolUse` file path already reaching `fire()` and never from prompt
  or note content, gives each anchor a tri-state verdict: observed, not
  observed, or unknown. Unknown covers no session id, unwired hooks, stdio
  transport, team-mode bind and a daemon restarted mid-session; an absent
  ledger is never read as evidence of non-observation. Only a literal "not
  observed" renders a caveat, additive to and independent of the existing
  drift caveat, so a note can be both drifted and unobserved without the two
  being merged into one "stale" flag. Legacy two-element anchor rows render
  byte-identically to before.
- An explicit non-relevance recall sort returns the true top-`limit`.
  `recency`, `priority` and `chronological` now re-query the store under the
  requested ordering, so the genuine top rows by that key are present even
  when they never entered the semantic candidate pool: a prefetch pinned to
  that pool cannot reach what missed it. Metadata filters are built once and
  shared; only the candidate fetch appends the semantic `note_id IN` clause.
  Membership for an explicit sort follows the requested key rather than query
  similarity, and `min_similarity` still applies within the measured pool
  only. The `relevance` default is untouched.
- `sort_by` is validated on every surface. REST returns 422 for a value
  outside `relevance` | `recency` | `priority` | `chronological`, and the MCP
  `vectr_recall` handler checks the same closed set at dispatch. Previously
  MCP, the surface that actually serves AI callers, forwarded arbitrary
  strings one layer down, where an unknown value degraded silently to
  `relevance`.
- A malformed `note_id` is an error, not a silent fall-through. `vectr_recall`
  with an unparseable `note_id` returns a tool error naming the offending
  value instead of quietly serving the ranked index path. Across all eight
  MCP tools that take a `note_id`, `bool` is rejected before the `int()`
  conversion: JSON `true`/`false` deserialize to Python `bool`, which is an
  `int` subclass, so `int(True) == 1` meant a caller passing `note_id=true`
  silently operated on note #1. On `vectr_forget` that was destructive.
- `content_file` containment is widenable by the operator, never by the
  caller. Reads resolved only against the primary workspace root and
  `extra_roots`, so an agent instructed to stage a large note body in a
  directory the workspace does not own (a harness scratchpad, a per-session
  temp dir) could not read it back, defeating the reason `content_file`
  exists. A new `memory_write.content_file.additional_readable_roots`
  allowlist is read from config at startup rather than accepted as a per-call
  argument, so a calling agent cannot widen its own boundary. Empty by
  default: nothing changes until an operator opts in. Symlink resolution
  still runs before the containment check, and relative paths still resolve
  against the primary root only.
- The stale-task line stops nominating deletion. Both emitters (MCP
  `vectr_status` and the SessionStart boot injection) dropped the WARNING
  prefix and no longer suggest `vectr_forget` for notes flagged on age. The
  line reports a neutral inventory fact, names supersession as the
  remediation, and states the rule in itself: age alone never retires a note.
  The flagged volume is unchanged.
- `vectr_remember` warns when a task note omits priority. Boot and resume
  share one `priority='high'` task query; admitting medium would flood a
  capped token budget with defaulted checkpoints and evict the ones that
  explicitly asked to be picked up. So the hint fires only when priority was
  omitted for a task note. An explicitly chosen medium stays silent.

### Memory legibility

- `vectr memory edit` opens working memory in `$EDITOR` and writes back
  through the lifecycle, never around it. A body change becomes
  `remember(supersedes=...)`, never an in-place content mutation; a
  kind/priority/tags change with the body unchanged is a column update; a new
  block with no `[#N]` is a fresh note; a deleted block is a revoke,
  reversible with `vectr_reinstate`. A hard delete is reachable only through
  an explicit `- forget: PERMANENTLY-DELETE-CONFIRMED` bullet on a
  still-present block, documented in the buffer's own header. Conflict
  detection re-reads and re-fingerprints every note the plan touches
  immediately before the first write and applies nothing if any drifted; a
  note the buffer never touched is never re-read, so its concurrent drift is
  not a conflict. The buffer parser, the diff/plan translation and the single
  writing function are separate from the `$EDITOR`-launching wrapper.

### Retrieval and ranking

- Default reranker is now `Alibaba-NLP/gte-reranker-modernbert-base`, with
  `ranking.rerank.top_k_unfiltered` lowered from 60 to 40. On the django
  acceptance corpus under shipped defaults: 43/57 passing and 5/6 must-pass,
  against 35/57 and 3/6 for the previous `BAAI/bge-reranker-base`. Rerank
  latency roughly 1634ms against 1957ms, model cache 574MB against 1.1GB, no
  `trust_remote_code`. `top_k_unfiltered` was swept over {10,20,30,40,60};
  must-pass is non-monotonic across that sweep, so 40 was chosen as the low
  end of a contiguous plateau rather than the isolated peak at 20. `top_k`
  is unchanged: it governs only the language-filtered branch, which no
  django case exercises. Known cost at pool 40: F30 regresses.
- Reranker `batch_size` and `max_length` are config-driven. `predict()` was
  called with no `batch_size`, silently inheriting sentence-transformers'
  default of 32, which a 40-query sweep with realistic 59-to-92-pair
  candidate pools found to be the worst value measured on mps for both
  shipped models, with zero rank movement at any batch size. `batch_size` is
  now a device-keyed map resolved from the loaded CrossEncoder's own device
  rather than a second detection path. A 10-query CPU sweep under the
  production 5-thread cap confirms 4 as the CPU optimum and 32 as the worst
  for both models: bge-reranker-base 1.70x (4450ms to 2610ms),
  gte-reranker-modernbert-base 1.49x (6297ms to 4219ms). Measured on Apple
  Silicon; cuda and the fallback keep the library default, undocumented as
  untested on real CUDA hardware.
- A low-confidence result set no longer withholds the body of its own best
  guess. `pointer_mode_retain.min_relevance` was pinned to the same scalar as
  `notfound_floor.min_top_relevance` (0.30), which is a structural deadlock
  rather than a coincidence: when `low_confidence` fires via the ce-floor
  sub-signal, it fires *because* rank-1's own `ce_relevance` sits below that
  bar, so a retain floor at the same value can never pass for the very result
  that triggered the flag, on any reranker or corpus. Measured live at
  rank-1-correct, `ce_relevance` 0.024, banner fired, zero code delivered,
  forcing a second `vectr_fetch` round trip. New `retain_rank1_always`
  (default true) keeps the composite-top result's excerpt regardless of
  score, mirroring the existing `result_floor` precedent and extending it
  from "stays in the list" to "keeps its body". Being rank-based, it needs no
  calibrated constant and survives a reranker swap. `min_relevance` stays at
  0.30 for ranks 2+ until separation evidence exists.
- The score-order explanation gate is expressed as headroom share. It used a
  fixed ratio (`score >= 1.5 * top_score`), unsatisfiable whenever rank-1
  scored 2/3 or above since displayed scores are bounded in [0, 1], leaving
  confident-query inversions structurally unannotatable (witness: rank-1
  0.864 against rank-5 0.969). The condition is now
  `score - top_score >= min_headroom_ratio * (1 - top_score)` at 0.65, so the
  required absolute gap shrinks as rank-1 approaches the ceiling. That
  separates 0.80 to 0.90 (50% of the remaining range, unlabeled) from 0.864
  to 0.969 (77%, labeled) despite near-identical absolute gaps.
- The "composite ranking prior" fallback annotation is gone. An empty
  `quality_reason` means the chunk carries no demotion at all, so the cause
  of its lower rank lies on the rank-1 side or in pool order, neither
  observable from the render. The annotation is suppressed rather than filled
  with a mechanism name implying an explanation exists.

### Symbol graph, trace and locate

- Rust `impl Trait for Type` is indexed under the type, not the trait. The
  positional child scan took the first type-ish node, which in a trait impl
  is the trait; resolution now reads the grammar's `type` field. Generic
  trait impls (`impl<T> Tr<T> for Bag<T>`) previously resolved to the empty
  string and were dropped from the graph entirely. The chunker carried its
  own copy of the same resolver and the same bug, so trait-impl members
  embedded a wrong `# class:` line.
- Java constructors are symbols. `constructor_declaration` was absent from
  all three node-type maps, so a class's constructors existed only as lines
  inside the class chunk: no symbol, no definition chunk.
- Container and leaf truncation no longer drops the tail. A constants-only
  Java enum of 160 entries kept 150 lines and the rest were unreachable from
  any chunk. Overflow chunks now re-emit the complement of what was kept, so
  tail constants and fields are searchable without duplicating content
  already covered by a member chunk.
- `vectr_trace` distinguishes "never looked" from "looked and found nothing".
  An un-queried direction was recorded by omitting its key and read back with
  `.get(key, [])`, so the gate meant to separate the two was always true and
  a `direction="callees"` trace rendered "Called by: (none found in index)"
  about a lookup that never ran. The un-queried direction is now recorded as
  `None`. `direction="both"` still renders both miss lines when both lookups
  genuinely returned empty.
- Trace truncation counts are honest about their own cap. An internal edge
  fetch cap bounds the raw fetch upstream of aggregation, so once it is hit
  the truncated count is a lower bound. The disclosure now reads "at least N
  more" instead of claiming an exact count and advising a higher `limit` that
  cannot move the underlying cap. No extra query on the trace path.
- Trace discloses what the user-facing limit dropped. The aggregation layer
  reports how many aggregated entries the cut removed alongside the
  hidden-builtins count, and every render path shows it: flat caller and
  callee lists and each per-definition callee list gain a footer, and a
  capped definitions fetch adds a definitions-beyond-limit line. That line is
  suppressed for a resolved qualified query, where beyond-cap definitions are
  other-class sites excluded by design. Default limits unchanged.
- A `vectr_locate` miss no longer dead-ends. `locate` now populates near-miss
  candidates for an unresolved name on MCP and REST, using the same fallback
  stages the search-hint path already had. The graph-level flag defaults off
  so per-token search-hint paths keep the cheap no-suggestion path, and the
  plain miss text is unchanged when no candidate exists.

### Indexing

- Every chunker path is bounded in bytes, not only in lines.
  `indexing.max_chunk_chars` (default 32000) applies alongside
  `max_chunk_lines`, since a single long line (a minified bundle, a generated
  data blob) defeats every line-based cap. Oversized chunks are sub-split
  into disjoint bounded pieces rather than truncated; a line longer than the
  cap is hard-split into cap-sized runs with `#<k>`-suffixed ids.
  Sub-splitting runs after chunk-hygiene keep/drop decisions, and pieces are
  never re-judged.
- Warm start no longer starves the strategy fingerprint. Indexed file paths
  derive from the persisted collection's chunk metadata, the same source as
  the indexed file count, so the fingerprint, the symbol-graph seed and
  `vectr_map`'s import graph see the restored corpus immediately after a
  daemon restart instead of an empty list until the first in-process index
  run.
- `/v1/status` stops reporting zero indexed files beside a populated chunk
  count. The file count derives from the same seeded per-language metadata
  cache the language stats read, rather than the process-local walk set, so
  both counters move together across the warm-start window.
- Purpose vectors are resumable and their gap is visible. A per-file purpose
  completion cache, tracked separately from the mtime cache, lets an ordinary
  non-force index run detect and backfill files whose body chunks are current
  but whose purpose vectors were never written: an interrupted deferred pass,
  a crash mid-pass, or a dispatch that never went out. A one-time migration
  seed means upgrading does not force a corpus-wide purpose reindex.
  `total_purpose_chunks` and `purpose_backfill_pending_files` are exposed on
  both `/v1/status` and `vectr_status`, with an explicit warning line when a
  run leaves an open gap, so this class of silent hole cannot hide behind a
  green status.
- A partial-file chunk delete no longer drops language-bucket membership. The
  decrement path removes a file from its bucket only when its last chunk
  goes, tracked per file. Latent today, since every current delete site is
  whole-file.
- Excludes are matched workspace-relative. `should_index_file` passed the
  absolute path to the config-file, generated-file and build-artifact
  matchers, and those scan every component of the path they are given, so an
  ancestor of the workspace decided exclusion: a checkout living under a
  directory named `generated`, `node_modules`, `.vscode` or `*.egg-info` lost
  its entire tree.
- Schema versions bumped so existing indexes rebuild through the normal
  staleness paths rather than serving pre-fix data: symbol schema 12 to 13
  (Rust trait-impl names, new constructor rows), indexing schema 5 to 7
  (byte-capped chunks, then chunk symbol names, `# class:` text, constructor
  and overflow chunks).

### Proactive delivery

- A relative hook path reaches a note with an absolute declared anchor. Path
  trigger candidates now derive a workspace-rooted absolute form for relative
  inputs, rooted at the workspace root and never at the process cwd.
  Absolute inputs keep their historical two-form candidate set, so this is a
  pure widening.
- Weak-tier candidates have a deterministic tie-break. Tier-C candidates all
  score a flat 0.6, so the per-event weak cap admitted whichever equal-score
  item note creation order happened to surface. Two tie-break-only fields
  (path mention count, first mention offset) now sort between score and the
  legacy chain. They are never read against the floor and never folded into
  the score, and hand-built candidates keep their prior ordering.
- Cooldown suppressions age out on a quiet process. The session ledger stored
  bare anchor ids, so under a process-scoped session key a suppression could
  never expire. Wall-clock TTL decay (`proactive.cooldown_ttl_seconds`,
  default 3600) now sits on top of the existing count ring, with an
  injectable clock so no test sleeps. A non-positive value normalizes to
  `None`, restoring the previous behavior exactly.
- Config-gated proactive machinery that was never live is removed:
  `proactive_enabled()`, `enforce_proactive_bind()`, `ProactiveRefused`, the
  `proxy_enabled` field, `PROACTIVE_PROXY_ENABLED` and the
  `VECTR_PROACTIVE_PROXY` env name. All had zero production callers, and the
  bind security property is enforced unconditionally at runtime, so the
  config-gated pair read as live machinery that was not.

### Episodes and arcs

- The Go test-failure marker stops matching pytest output. `^FAIL` fired
  `go_test.fail` on every failing pytest run, since the short summary prints
  `FAILED tests/x.py::test_y`. The verdict was unaffected, both markers being
  failures, but `markers_matched` is the record of which tool reported what,
  and a Go marker on a Python run makes that record wrong. `^FAIL(\s|$)`
  keeps every real Go form and drops `FAILED`; the marker table version bumps
  1 to 2 so values persisted under the old table stay distinguishable.
- Arc bucketing refuses to key on a known-wrong directory. Four residual
  holes still permitted the cross-directory false pairing effective-dir
  bucketing exists to kill: a relative `cd` target was returned textually and
  never composed against the episode cwd, so `cd sub && mvn test` under
  `/work/A` and `/work/B` keyed identically; `cd -` returned `-` as a bucket
  key though `$OLDPWD` differs per call site; a bare `cd` fell back to the
  episode cwd, provably wrong since a bare `cd` runs in `$HOME`; and env-var
  and flagged `cd` shapes fell back the same way. Refusing to bucket is now
  preferred over bucketing on a value known to be wrong.
- Command normalization splits glued shell separators. Tokenization gains a
  quote-aware pre-pass padding unquoted `;`, `&&` and `||` so they become
  their own tokens: an unquoted separator is a control operator in the shell
  that ran the command regardless of surrounding whitespace. Quote state is
  tracked per character and backslash escapes are honored, so quoted text is
  never touched, and the unbalanced-quote fallback splits the original string
  rather than the padded one.

### CLI and editor integration

- The daemon writes `.vscode/mcp.json` itself. Startup configuration wrote
  `.cursor/mcp.json` and `.claude/settings.json` but not the VS Code file,
  even though the CLI writes that path at start and the config checker lists
  it. The only writer keeping it current was the editor extension's own sync,
  which runs solely after the extension itself spawned the daemon, so any
  other route to a port change left VS Code pointing at a dead port
  indefinitely while the other two configs self-healed. Reproduced live with
  two configs on 8765 and `.vscode/mcp.json` on 8768 with nothing listening.
  VS Code keys its entry under `servers` rather than `mcpServers`, so it
  carries its own owned-key path. The extension's sync is now redundant
  rather than load-bearing.
- `vectr start` text output adopts the `--json` exit contract: exit 1 exactly
  when status is `failed`, with both `ready` and `not_ready` exiting 0.
  `vectr restart` inherits it.
- `vectr init` handles aliased guidance files. `CLAUDE.md`/`AGENTS.md` pairs
  that are symlinks or hardlinks to one another get one merged write per
  distinct underlying file, grouped by device and inode with a realpath
  fallback for dangling pairs.
- Audit log timestamps are true UTC. The log formatter's converter is pinned
  to `gmtime`, so the literal `Z` suffix in the date format no longer stamps
  local time.
- `scripts/release.sh` enforces all five version bumps. Preflight checked
  only `pyproject.toml` and `CHANGELOG.md`, so a missed bump in `README.md`,
  `server.json` or `vscode-extension/package.json` passed silently: that is
  how the extension manifest drifted from 1.6.0 to 1.10.0. All five are
  checked now, `server.json` requiring all three of its version fields. The
  completion message also claimed the tag triggers the MCP registry workflow.
  It does not; that workflow is `workflow_dispatch` only.

### Internals

- The over-fetch invariant for the global working-memory collection is
  codified in one function, with all three vector-query paths sizing
  `n_results` through it.
- `app/service.py` reads the pre-compact boundary config as module attributes
  at request time rather than binding them by value at import.
- The proactive gate takes an injectable clock, making cooldown TTL expiry
  provable at service level.
- Test isolation is enforced session-wide rather than per test. Loopback
  socket connects are refused unless the test registered its own ephemeral
  port, since port 8765 serves a live session and a unit test reaching a real
  listener is a defect rather than a strategy. Product-code model loading
  that would hit the network raises rather than downloading, gated on the
  exact conjunction of a product-defined builder, a real model class and an
  uncached model, with the hub download functions wrapped as a backstop and
  the stdio child pinned offline. The deferred purpose pass is awaited by
  submitting a sentinel to the same single-worker queue, so returning proves
  every queued pass finished instead of polling a counter that expires
  silently. Source-tree wheel builds are serialized across concurrent suites.
  Both guards opt out with `VECTR_TEST_ALLOW_REAL_NETWORK=1`.
- The acceptance runner stamps a per-case corpus revision and prints a
  comparison against the served workspace revision.

## 1.10.0 - 2026-08-18

Expiry stops deleting notes and becomes a kind-scoped state transition that
directives are exempt from; a note can be pinned so recall returns it whatever
the query says; working memory renders to a read-only `MEMORY.md` a human can
actually read; a revoked near-duplicate warns you at write time instead of on
some later recall; prose docs stop being chunked with a window sized for code;
and bulk indexing yields to interactive work instead of saturating every core.

### Working memory
- Note expiry is a state transition, not a delete. `purge_expired_notes()` ran a
  flat `DELETE FROM notes WHERE created_at < cutoff` regardless of kind, which
  could remove directives and revoked-note deterrents, and destroyed the
  `note_events` audit trail the append-only state machine depends on. It now
  appends a system-actor `expired` transition and leaves the row in place, is
  kind-scoped through `MEMORY_DECAY_TTL_DAYS_BY_KIND` (directives exempt at any
  age, including under an explicit `ttl_days` override), and is idempotent: a
  note already `expired` or `revoked` is skipped, so a revoked note's deterrent
  stays visible in default `recall()` and `fire()` forever.
- Ranking decay is wired up and no longer compounds. `decay_old_notes()` had no
  production caller and multiplied `decay_score` onto its own prior value on
  every call, so two calls at the same clock reading silently produced 0.5 then
  0.25. It now computes `decay_score = 0.5 ** (elapsed / half_life)` fresh from
  elapsed time, is kind-scoped through `MEMORY_DECAY_HALF_LIFE_DAYS_BY_KIND`,
  and runs from `start_background_index()`. It is a ranking tie-break only and
  never removes anything.

### Recall floor and pinning
- New pin surface: `vectr_pin` (MCP), `POST /v1/pin`, a `pin=` parameter on
  `remember`, and a `notes.pinned` column. A pinned note comes back from
  `recall(apply_floor=True)` unconditionally, bounded by the
  `recall_floor.tier0_*` caps, whether or not the query matches it. Verified end
  to end at limits 1, 3, 5 and 10.
- Directive notes are deliberately **not** part of the floor. `boot_recall()`
  already returns every directive unconditionally at each session start, so a
  second delivery charged query budget for a guarantee that already existed.
- The deterministic tag/anchor/symbol/title channel ships **default-off**
  (`recall_floor.deterministic_enabled: false`) as a measured negative result.
  Against a 38-query hand-labeled corpus its predicate matched a mean of 50.2
  notes per query against roughly one labeled-relevant note (tag 1.6% precision,
  title 4.6%, anchor 0%), and a floor-budget sweep showed its effect on recall
  is not monotonic in budget, which is the signature of noise rather than
  signal. The code, cap formula and specificity ordering are retained and tested
  behind the flag pending a tighter predicate.
- `benchmarks/recall_miss/` measures `WorkingContextStore.recall()` against a
  hand-labeled query set over a read-only snapshot of a real note corpus, and
  reports recall@k with misses grouped by note kind. It asserts `recall()` never
  returns more than `limit` notes, so a composition that appends past the
  caller's limit cannot inflate recall@k without improving ranking.

### Memory legibility
- `vectr memory export` renders a workspace's notes to a deterministic,
  read-only `MEMORY.md`, and a debounced post-write hook keeps it current once
  configured. Nothing reads the file back: the database stays canonical.
- `vectr memory export --disable` removes the marker so subsequent note writes
  stop scheduling a re-render. The already-exported file is left in place.

### Provenance and trust
- `bind_user_quote_auto()` binds the longest verbatim span of the captured user
  turn that appears in the note content, instead of requiring the note's whole
  body to appear in the prompt. Whole-body containment bound at most 1 of 45
  directives on the live corpus. The span floor is 30 characters with search
  cost bounded on the length product, and `frame_prefix()` renders a qualified
  attribution frame when the bound quote is a strict sub-span. Explicit
  `user_quote` binding still takes precedence, and human provenance remains
  reachable only by explicit promotion.
- A revoked near-duplicate is surfaced at write time. `revoked_related_notes()`
  reports the nearest revoked match through `RememberOutcome`, REST, and the MCP
  confirmation suffix, so you learn a claim was already disproven while writing
  it rather than on a later recall.
- The revoked-note deterrent survives truncation. `_ANTI_MEMORY_TEMPLATE` field
  order puts the warning first, so right-truncation on both rendering surfaces
  drops the quoted summary before the reason, and the reason before the warning.
- `revoked_related_notes()` takes its candidate-pool depth from
  `memory_write.related_notes.revoked_query_floor` (default 100) instead of
  `limit * 3`, so a rare target class is not starved by a render limit of 1.

### Proactive delivery
- Declared `triggers[].path` globs are honored on the structural channel, and a
  bare-basename trigger glob now matches nested files rather than top-level ones
  only.
- The `recall_for_path()` declared-trigger arm can no longer starve the content
  and anchor arms out of the candidate pool.

### Indexing
- Bulk indexing has a resource governor (`indexing.index_governor`). A
  restore-on-exit context manager lowers the indexing thread's OS scheduling
  priority for the duration of a governed block (macOS QoS, Linux per-thread
  niceness), and a pacing helper sleeps after each batch in proportion to
  measured work time to hold a configured duty cycle. Both degrade to a no-op on
  an unsupported platform, and neither can abort indexing. It is a context
  manager rather than a permanent mutation because bulk `/v1/index` runs on
  Starlette's shared threadpool, where a permanent priority change would leak
  onto unrelated later work on a recycled thread.
- The purpose-vector pass runs in the background. It roughly doubles total embed
  time on a large corpus, so `index_workspace()` hands it to a single-worker
  executor and returns once content embedding is checkpointed. Search degrades
  gracefully to body-only scoring while a chunk has no purpose vector, and the
  pass is idempotent by `chunk_id`, so a crash never re-embeds already-persisted
  content.
- `.txt` and `.rst` prose is chunked at reStructuredText setext headings.
  Documentation with no tree-sitter grammar previously fell to 200-line window
  chunks sized for code, merging unrelated subsections into one diluted chunk.
  Oversized sections sub-split at `INDEXING_MAX_CHUNK_LINES` rather than being
  truncated, and a file with no heading structure still falls back to window
  chunks. On the django corpus (724 `.txt`/`.rst` files) this goes from 1603 to
  6666 chunks. `INDEXING_SCHEMA_VERSION` 4 -> 5, so existing indexes re-chunk on
  upgrade.
- A schema-version bump now forces a full re-chunk. `index_workspace()` cleared
  the mtime cache on a mismatch but never set `force=True`, so Phase 2's
  delete-before-reinsert check was False for every file, and a chunking-logic
  bump left stale chunks as orphans alongside freshly chunked content instead of
  performing a clean rebuild.
- Near-duplicate docstring dedup no longer collapses distinct code. Chunks
  sharing a leading docstring key are collapsed only when their normalized
  bodies also exceed `ranking.docstring_dedup.body_similarity_min_ratio` (0.75)
  and their `node_type` agrees; key-alone collapse merged a trait declaration
  with its impl, and distinct constructors sharing a copy-pasted doc summary.
  Each candidate is compared against at most
  `ranking.docstring_dedup.max_reps_compared` (3) already-kept representatives
  per doc_key: unbounded, the loop is O(n^2) `SequenceMatcher` calls, measured
  3.1s at n=60 and 35.0s at n=200, and bounded it is 0.30s and 1.07s. The cap
  fails only toward under-collapse, never toward a false collapse, and does not
  truncate content.
- Embed-model selection is a real three-tier mechanism: explicit
  `VECTR_EMBED_MODEL` > an evidence-table row keyed on the corpus fingerprint >
  the configured default. The evidence table ships **empty**, because the C/C++
  row it was designed for was invalidated by a same-index A/B. This removes the
  unmeasured "the default is optimal" rationale `vectr_status` used to surface.

### CLI and API
- `vectr restart` no longer drops extra roots. `cmd_restart` built its root list
  purely from the resolved arguments and never consulted the registry entry it
  already held for mode and host inheritance, so a bare
  `vectr restart <primary-path>` on a multi-root instance came back with only
  the primary root. Precedence mirrors mode inheritance: an explicit
  `.code-workspace` file or any `--path` flag wins outright with no merge, while
  a bare positional directory or the `VECTR_WORKSPACE`/cwd default inherits the
  previous instance's recorded `extra_roots`. Inheritance is announced on
  stderr, never silent. The staleness banner's suggested restart command now
  reproduces every root literally.
- `vectr_remember` accepts a `content_file` parameter (MCP and REST), so a large
  or escape-dense note body is read from a file instead of streaming as one long
  JSON argument. Containment is checked against every served workspace root
  rather than the primary root alone, which had rejected legitimate paths under
  a multi-root instance's extra roots. Resolution order is unchanged, so a
  symlink inside a served root pointing outside every served root is still
  rejected: the security property applies per-root now, it does not weaken.

### Internals
- Failure-to-success arcs bucket pending failures on the effective working
  directory rather than the recorded cwd.
- The eval harness got a correctness batch: assistant messages are deduped
  before summing token usage, `anchor_checked` is computed for every arm and
  variant, anchors match by path component or shell token instead of by raw
  substring, hook injection counts are recorded as an agent-session delta rather
  than an end-of-leg cumulative, `conversational_turns_to_fact` is reported
  alongside `turns_to_fact`, S3/S4 scenario anchors moved off their own
  fact-acquisition executables, and git identity is isolated to a synthetic
  config file. All 48 eligible longitudinal legs were rescored with the current
  scorer.
- Two test-isolation fixes: a session-scoped fixture leaking `VECTR_EMBED_MODEL`
  into every later test file, and a purpose-vector prune test that asserted on
  the purpose collection without waiting for the now-deferred background pass.

## 1.9.0 - 2026-08-07

A trace answer stops hiding real callers behind a same-named one and becomes
chainable into `vectr_fetch`; a mistyped qualified name gets the same
near-miss help a mistyped bare name already got; a failed fetch says which of
its two very different causes it hit; `--path` resolves from anywhere inside a
workspace; and shell episodes stop being classified `unknown` on hosts that
report success without an exit code.

### Retrieval
- `vectr_trace` collapses callers by `(name, file)` instead of by name alone.
  Two unrelated functions sharing a leaf name in different files (two classes
  each defining `__call__`) used to merge under one elected representative
  edge, so a real caller in the second file never appeared in the rendered
  trace even though its calls were counted in the `xN` suffix. Same-file call
  sites still fold into one entry with a count, which is a genuine ambiguity a
  receiver-blind static graph cannot resolve further. Callees keep the
  name-only collapse.
- Trace paths render workspace-relative, and every call site carries a
  `vectr_fetch`-ready chunk id, so reading the code around a caller is one
  chained call instead of a path fix-up plus a file read.
- Qualified-name near-miss now covers a typo in the **leaf** segment, not only
  in the qualifier (`QuerySt.get_or_creat`, both halves misspelled). The
  fallback resolves the bare leaf through the same per-segment machinery an
  unqualified token already gets, then ranks by qualifier similarity, so a
  doubly-mistyped token still surfaces its real symbol as a labeled-inexact
  suggestion. The widen step is also guarded so it can no longer displace an
  exact resolution that already succeeded.
- `vectr_fetch` distinguishes its two miss shapes. A requested id whose line
  range does not align to a stored chunk span, in a file that still has chunks
  indexed, returns `reason="misaligned"` plus `nearest_ids` naming the closest
  real chunk ids in that file, so the caller's next fetch succeeds. Only a file
  with no indexed chunks at all returns `reason="file_changed"`. New config
  `fetch.misalign_nearest_max` (default 5) caps the suggestion list. Callers
  reading the old shape are unaffected: the field defaults to `file_changed`.
- The caller in-degree tiebreak query now has index statistics to plan with. A
  full graph build gathers them once via a bounded `ANALYZE`, which flips a
  selective `to_symbol IN (...)` lookup from a whole-workspace covering-index
  scan to a direct index seek (measured ~18.6ms to ~1.0ms for a 50-name lookup
  on a synthetic 300k-edge graph). Enclosing-container lookups during
  qualifier scoring are batched per file rather than issued per candidate.

### CLI
- `vectr start --path` / query subcommands resolve a path through the
  workspace instance that **owns** it: pointing at a subdirectory of an
  indexed workspace now finds that workspace's daemon instead of reporting
  nothing running.
- `vectr stop --port N` stops the instance bound to a port, bypassing
  workspace-path resolution entirely and exiting non-zero if nothing is
  registered there. Deterministic for scripts and harnesses that manage
  scratch daemons by port.
- `vectr start --strict-port` fails instead of walking to the next free port
  when the requested one is taken, and `vectr start --json` prints a
  machine-readable result (port, workspace, mode, whether an existing instance
  was reused). Exit codes are reliable on the `--json` surface.

### Working-memory delivery
- The trigger-engine per-injection token cap now **trims** an oversized note
  body to the cap instead of demoting the note to title-only. A capped note
  previously delivered its label and none of its guidance. Truncation backs off
  to a sentence or word boundary, controlled by the new
  `memory_triggers.injection.body_truncation_min_boundary_fraction` (default
  0.5), rather than cutting mid-word.
- The proactive request window is built from user and assistant text only.
  Host-injected system reminders could be an order of magnitude longer than the
  user's actual message, so the tail-window slice was filled with boilerplate
  and the real task text was evicted before scoring ever saw it.

### Episode capture
- Bash episodes are classified from the host's success signal when no exit code
  is available. Claude Code's post-tool event carries no `rc`, which left the
  outcome cascade with nothing to key on for most everyday commands: measured
  across the live episode store, `outcome=unknown` fell from 91.6% to 0.0% and
  detected failure-to-success arcs rose from 0.675 to 1.605 per 100 episodes.
  The new signal sits below markers and `rc` in the cascade, and below
  `is_error`, since some hosts report one event name for both outcomes. No
  previously non-`unknown` classification changes.

### Internals
- Cache path resolution is centralized and reads `VECTR_CACHE_DIR` at call
  time rather than import time. Precedence is `VECTR_DB_DIR` >
  `VECTR_CACHE_DIR` > `~/.cache/vectr`. Test sessions now get an isolated cache
  root instead of writing into the real one.

## 1.8.0 - 2026-08-03

A restart now means "the same daemon again", version staleness is decided by
the code revision rather than the version string, and a note that transcribes
a direct user statement can now prove it.

### CLI
- `vectr restart <workspace>` inherits the recorded mode (full, memory-only,
  or search-only) and bind host of the instance it replaces. Explicit
  `--memory-only`/`--search-only` still win, and a new `--full` flag exits an
  inherited reduced mode on purpose. Previously a memory-only daemon came
  back in full mode (indexing and file watcher on) unless the flag was
  re-passed, and the staleness banner recommended exactly the bare command
  that caused the flip; the banner now prints the mode-complete command.
- The version-staleness warning keys on the git revision, not the version
  string. Same revision with differing packaged versions reports a "version
  metadata mismatch ... no restart needed" (the usual cause is a stale
  editable install). "Older" is never claimed from a lexical version compare,
  and abbreviated revision ids of the same commit are not a mismatch.

### Working memory
- New derived provenance class `user-stated` for notes that transcribe a
  direct user statement. A write may attach a `user_quote`; the store binds
  it only when the excerpt appears verbatim in the note content
  (whitespace-insensitive, case-sensitive, minimum 12 characters: a
  deterministic string check, never a semantic one). A bound note renders
  with its own recall frame naming the verbatim excerpt; an excerpt that
  fails the check is discarded and the note stays in the ordinary
  agent-recorded class, with the reason surfaced in the write response. The
  class cannot be claimed directly, only earned by the check. Available over
  MCP and REST.

## 1.7.0 — 2026-07-29

Proactive delivery becomes worth reading: an injected item now carries the
note's actual guidance instead of just its label, a file-anchored note
retires when its file is actually edited rather than on a timer, and the
framing envelope scales with note provenance. Also: `vectr stop` followed by
`start`/`restart` keeps the workspace's previous port so already-written
editor MCP configs stay valid, and `vectr init --hooks` survives an
unwritable git hooks directory.

### Proactive context
- Injected items now carry the note's **body**, not just its title. A titled
  note previously delivered only the title, leaving the actual guidance —
  the caveat, the fix, the "do not do X" reason — in `content` and never on
  the wire. Rendering is now `title: body` (the title is skipped when empty
  or when it is already a prefix of the body), and character-budget
  truncation backs off to a sentence or word boundary before resorting to a
  hard cut.
- **Event-anchored retirement** for file-anchored matches. A note delivered
  because the request touched its declared anchor file used to enter a fixed
  cooldown whether or not the agent had acted on it yet — measured as "too
  early, then evicted" in six of seven benchmark cells. Such a note now
  stays eligible for re-delivery while its file is merely being read, and
  retires the first time a request's window shows the file actually edited.
- The injection envelope's opening clause now scales with **note
  provenance**: the weakest provenance among the notes selected for one
  delivery (auto < agent < human) picks the wording, so an unreviewed
  auto-capture is introduced more skeptically than a rule a human wrote. The
  shared no-authority / verify-before-relying clause is identical across all
  tiers, since provenance is a caller-declared field, not a verified
  identity claim. Wording is configurable via `proactive.envelope` in
  `config.yaml`.

### Reliability
- `vectr stop` followed by `start` or `restart` now reuses the workspace's
  previous port instead of walking to a new one that already-written editor
  MCP config files don't point at. The port-free probe now treats a socket
  in TCP `TIME_WAIT` — the state every `vectr stop` leaves behind — as
  reusable, with a short bounded retry
  (`instance_registry.port_reuse_retry_attempts` /
  `port_reuse_retry_delay_s`), and `restart` no longer erases the registry
  entry the reuse step depends on before consulting it.
- Editor config writes now always sync vectr's **own** MCP server entry to
  the current port, while continuing to never overwrite user-added keys.
  Previously the merge-only-add rule kept the file pointing at the old port
  after a port change. After `start`/`restart`, vectr also prints an
  explicit warning naming any known editor config file under the workspace
  that still points at a different port, instead of leaving the editor's
  MCP connection to fail silently.
- `vectr init --hooks` no longer aborts when the git hooks directory is
  unwritable (for example a stale or misconfigured `core.hooksPath`): the
  git post-commit writer skips with a disclosed message like every other
  precondition failure in that path, and the editor hook installers that
  already succeeded stay in place.

### Research artifacts
- Injection-utility benchmark: six trap scenarios whose correct answer
  exists only in working memory rather than the workspace, an A/B analysis
  pipeline (arm hit rates, inject-vs-control efficiency, note-awareness
  scoring), and a hook-channel arm so proxy-versus-hook delivery is measured
  rather than assumed. The first seed-0 measured run is recorded, including
  a delivery-side finding: in the agent CLI's non-interactive print mode,
  prompt-time hook injections do not render into the machine-readable
  transcript (session-start injections do), so hook-channel utility must be
  verified against an interactive session.

## 1.6.0 — 2026-07-28

Proactive context — surfacing the relevant working-memory note at a
sanctioned delivery point instead of waiting to be asked — becomes the
default on local instances, together with the precision, provenance, consent,
and audit work that makes that default defensible. Also: write-time
related-note and process-anchor offers on every note write, a
compaction-boundary preservation surface, and a gitignore-matching fix that
could silently exclude an entire workspace from the index.

### Proactive context
- `proactive.enabled` now defaults to **true**. It gates ambient delivery
  channels — a hook surface that reads the transcript with no per-session
  user action. The `vectr proxy` channel is exempt from the switch by launch
  consent: starting the proxy and pointing an editor at it is itself the
  opt-in for that channel. Proactive context is still refused outright on a
  non-loopback (team / shared-instance) bind, independent of this key — the
  bind check runs before any config or channel branch. Turn off with
  `VECTR_PROACTIVE=0` or `proactive.enabled: false`.
- Structural (exact file-path) matches no longer bypass the similarity floor.
  They are scored in tiers instead: the note declares the file in its own
  `anchors` (1.0), a `gotcha` mentions it (0.9), any other eligible kind
  merely name-drops it (0.6). At most one weak-mention item is selected per
  delivery, so low-confidence file mentions can no longer fill the item
  budget ahead of a genuine semantic match. New config:
  `proactive.structural_scores`, `proactive.max_weak_structural_items`.
- The structural channel is restricted to kinds that state a durable fact
  about a file (`proactive.structural_kinds`: gotcha, finding, decision,
  operational, reference). `task` notes — a moment in time that happens to
  name a file — are excluded, and the channel now over-fetches before
  filtering so that exclusion cannot starve it
  (`proactive.structural_overfetch_multiplier` / `_ceiling`).
- Injected context carries a role-provenance envelope and a per-item
  provenance marker, so a recalled note reads as recalled memory rather than
  as an instruction from the current turn. `directive` notes are excluded
  from the proxy channel by default
  (`proactive.proxy.exclude_directive_notes`); session-start injection
  delivers them through its own path and is unaffected.
- A revoked note reaching the proactive matcher renders with the same
  deterrent framing recall uses, and delivery fails closed when a note's
  lifecycle state cannot be read rather than emitting the original content.
- Cooldown identity is the proxy process, not a hash of the conversation's
  first user message. One proxy serves one editor session, so every request
  through it — subagent turns included — shares one cooldown ledger.
  Previously each new first message minted a fresh ledger, and a note could
  be re-injected seconds after its last emission.
- Cooldown slots are charged on confirmed delivery, not on retrieval. The
  proxy checks up front whether a request can carry an appended block at all
  and skips retrieval entirely when it cannot, then confirms delivery with an
  opaque token. A caller that passes no token keeps retrieval-time charging
  exactly as before.
- Audit split to match: `PROACTIVE_RETRIEVE` is written at selection,
  `PROACTIVE_INJECT` only once the block is confirmed delivered — so the
  injection count is an honest lower bound on what reached the model instead
  of counting blocks that were never appended. `PROACTIVE_INJECT` lines now
  also carry `chars=` and per-item `states=`, recording delivered size and
  each item's lifecycle state.
- `vectr status` / `GET /v1/status` report the **effective** proactive state
  (the config flag ANDed with the bind gate) instead of the bare config
  value, which on a non-loopback bind claimed injection was on where it was
  in fact refused.
- The proxy's counters distinguish `inject_skipped_not_appendable` — the
  request could carry no block, so nothing was ever asked for — from the
  general skipped count.

### Working memory
- `vectr_remember` returns write-time offers alongside the stored note id:
  the nearest existing active notes by similarity, so a superseded one can be
  corrected in the same turn, and — for an `operational` note written without
  anchors — the process files actually present in the workspace it could be
  anchored to. Both are additive; neither gates, rewrites, or reorders the
  write, and similarity is closeness, never a contradiction verdict. Config:
  `memory_write.related_notes`, `memory_write.proxy_anchor_suggestions`.
  `POST /v1/remember` gains `related[]` (with each candidate's priority) and
  `proxy_anchor_suggestions[]`.
- New versioned proxy-anchor manifest (`agent/proxy_anchors.yaml`) mapping
  process domains — how dependencies are pinned, how CI runs, how the app is
  containerized, built, and deployed — to the ecosystem-standard files that
  encode them. Presence is glob-detected only; no file content is read.
- Drift on an anchored `operational` note now renders honestly: "changed
  since — verify; <file> is a proxy for this process, last confirmed <date>",
  rather than asserting the note is wrong. A changed process file means the
  process *may* have changed; the judgment stays caller-side.
- Stale-anchor flagging is keyed on a signature of the drifted anchors rather
  than on the most recent lifecycle event of any kind. An unrelated event in
  between no longer admits a duplicate audit row, and a genuinely new drift
  of the same anchor is no longer permanently suppressed.
- `recall_for_path()` matches on path boundaries instead of raw substrings —
  a note about `gate.py` no longer matches when `regate.py` is touched — and
  over-fetches candidates before that filter, so a true match can no longer
  be dropped from a too-small candidate pool. New config block:
  `memory_recall_for_path`.

### Compaction boundary
- New `GET /v1/boundary/precompact` and a PreCompact hook branch: on editors
  with session hooks, vectr emits a short boundary-preservation nudge (plus
  the count of arcs still awaiting distillation) as plain text before the
  conversation is compacted, which the harness appends to its own compact
  instructions. Emitted as plain text, never the JSON hook envelope other
  events use. Config: `episodes.boundary_precompact_enabled` (default true),
  `episodes.boundary_precompact_token_cap` (default 200).

### Indexing
- gitignore patterns are matched against the workspace-relative path, not the
  absolute one. A directory-style entry such as `tmp/` previously matched
  ancestor components of the workspace root itself, so a workspace checked
  out under a matching prefix had every one of its files silently excluded
  from the index. Path-scoped entries such as `docs/*` consequently now match
  as gitignore intends. Fixed in both the initial scan and the file watcher.

### Reliability
- Working-memory note operations — remember, recall, forget, commit notes,
  and proactive selection — dispatch off the request event loop, extending
  the 1.5.0 vector-store fix to the notes collection. A slow store call no
  longer stalls every other HTTP and MCP endpoint.
- The proxy-anchor workspace walk is bounded (depth, directory budget, and
  pruned dependency/build trees) and runs once per call instead of once per
  recursive glob: ~600ms → ~22ms on a 3,600-directory tree. It also no longer
  offers files from inside `node_modules/`, `.venv/`, or `target/`, which
  encode a dependency's process rather than the workspace's own.

### Editor extension
- The extension now finds a CLI installed by `pip install --user`. After the
  PATH probe fails it checks the per-user script directories a GUI
  application's inherited PATH commonly omits — `~/Library/Python/<X.Y>/bin`
  on macOS, `%APPDATA%\Python\Python<XY>\Scripts` on Windows, `~/.local/bin`
  elsewhere — before falling back to `python -m vectr`. Candidates are
  stat-checked before they are executed, so a machine with none of them costs
  no extra process spawns.
- The not-found message lists every location searched instead of claiming
  only PATH was checked, and the output channel names which CLI was launched.
- The `python -m vectr` fallback now spawns with its `-m vectr` arguments
  intact; it previously reported success and then spawned the bare
  interpreter with vectr's arguments.

### Hygiene
- Generated editor-guidance files list `vectr_distill`, `vectr_revoke`, and
  `vectr_reinstate`, which shipped in 1.5.0 but were missing from the tool
  tables.
- The proxy-anchor manifest version is derived from the manifest file instead
  of being duplicated in code, so the two cannot silently drift.
- New injection-utility A/B harness under `benchmarks/` — scenario set,
  mechanical scorer, and runner — with its non-vacuity check gated on
  confirmed delivery rather than on retrieval alone.

## 1.5.0 — 2026-07-23

Automatic episode capture with arc distillation, a note lifecycle with
revoke/reinstate, session resume, and an off-event-loop vector-store fix that
keeps the daemon responsive while its index compacts. MCP surface grows from
16 tools to 19.

### Working memory
- Episode capture: on editors with session hooks, each tool invocation is
  recorded as a deterministic local episode (command, outcome, failure
  markers) — no model calls involved. A streaming detector groups episodes
  into **arcs**: failure→success moments, e.g. a command that failed twice
  and then passed after an edit. `vectr status` / `GET /v1/status` report
  `episodes_count` and `arcs_pending_distill`.
- New `vectr_distill` MCP tool + `GET /v1/arcs`: review pending arcs
  (oldest and most-confident first, token-bounded), persist the ones worth
  keeping as notes via `vectr_remember(..., distilled_from=[arc_id])`, and
  dismiss the rest with a reason.
- Note lifecycle: `vectr_revoke` flags a note as wrong **without deleting
  it** — future recall shows a deterrent framing ("previously believed …,
  revoked …") instead of the original content, so the mistake is not
  silently re-derived. `vectr_reinstate` reverses it.
  `vectr_remember(contradicts=N)` records a correction and revokes the old
  note in one step. All transitions land in a note lifecycle event log.
- Serving policy: new `operational` note kind with `command` triggers,
  delivered on the command lane's hook fast path; a per-turn injection
  ledger keeps any note from firing twice in the same turn, and fires are
  recorded only when actually delivered.
- New `vectr_resume` MCP tool + `vectr resume` CLI + `GET /v1/resume`:
  the most recent task note, the latest snapshot, and open gotchas with file
  anchors in one deterministic call, selected by the same shared helper
  session-start injection uses. Config: `behavior.resume.max_gotchas`
  (default 5).
- New note kind `decision` and recall sort `chronological` (oldest-first,
  dated index lines): architectural decisions accrue as notes and recall as
  an ADR-style timeline. Decisions are not auto-injected at session start.
- Commit provenance: a git post-commit hook records each commit's message
  and touched files as a working-memory note, so "when did we change X"
  recalls instead of re-running `git log`.
- Note ages under an hour render as seconds/minutes in recall output
  instead of rounding to "0h".

### Search correctness
- Java `enum` methods now chunk correctly (the chunker descends through the
  enum member-group wrapper node), and the chunker recurses into any AST
  container node rather than class bodies only.
- Rust `impl`-block members resolve for qualified `Type.method` locate and
  trace.
- `vectr_trace` with a qualified `Class.method` validates the class
  qualifier before answering the callers path — no more caller lists for a
  same-named method on a different class.
- Near-miss suggestions for a misremembered qualified name are ranked by
  similarity, and caller ties are broken beyond name identity.
- Pointer-mode search keeps a result's code body when that result's own
  score clears the confidence floor, instead of demoting it with the rest.

### Reliability
- Vector-store calls no longer run on the request event loop: search/fetch
  dispatch through a dedicated executor, chunk counts are served from
  caches refreshed on every mutation and embed batch, and any store call
  slower than 5s logs a warning. Previously a single store call issued
  during the store's internal compaction could freeze every HTTP and MCP
  endpoint (observed: 41 minutes on a 2.6 GB index). New config:
  `indexing.vector_store_bridge.dispatch_max_workers` (must stay 1) and
  `indexing.vector_store_bridge.slow_call_warn_seconds`.
- Version stamps no longer absorb a foreign git SHA when vectr is installed
  editable under an unrelated repository checkout.
- The MCP server reports its version from package metadata instead of a
  hardcoded constant.

### Hygiene
- Two staleness tests pinned file mtimes explicitly, fixing a CI-only
  coarse-clock flake.
- GitHub release creation is skipped when the tag's release already exists,
  making the release workflow re-runnable.

## 1.4.0 — 2026-07-20

Search-correctness fixes, leaner MCP responses, and acceptance-corpus hardening.

### Search correctness
- Zig: struct- and enum-scoped `const`/`var` declarations are now extracted as
  symbol-graph members (previously silently dropped; function-locals stay
  excluded). Symbol schema v12.
- Qualified `Class.method` locate now resolves modifier-prefixed class
  declarations (`public class`, `export default class`, `export abstract
  class`, …) across Java/TypeScript/JavaScript.
- The low-confidence "may be unrelated" banner no longer false-fires on
  high-confidence paraphrase matches: new config key
  `ranking.notfound_floor.ce_override_min_relevance` (default 0.70) suppresses
  the zero-vocabulary trigger when the top result's cross-encoder relevance is
  confidently high. Genuine misses still banner; set above 1.0 to disable.
- `class_importance` no longer counts barrel re-export lines (`export { X }
  from`, `export * from`) as usage, removing a display-order inversion in
  re-export-heavy JS/TS codebases.
- `ranking.class_importance.lambda` default raised 0.25 → 0.35 after a
  full-corpus regression audit (recovers two known ranking regressions with
  zero regressions elsewhere).

### Leaner MCP responses
- All MCP text output renders workspace-relative paths, with the absolute
  root printed once per response header. `vectr_fetch` accepts both relative
  (new canonical) and absolute (back-compat) chunk ids. The absolute-path
  prefix previously accounted for ~9% of a default search response, ~26% of
  pointer mode, and ~42% of `vectr_evict_hint`.
- `vectr_evict_hint` renders each chunk once, in id-ready form
  `relpath:start-end  (symbol)`, with a single fetch template — ~70% of the
  old payload was duplicate serialization.
- Low-confidence pointer mode is now actually slim: the duplicated
  symbol-graph section is deduped against the pointer list and the re-fetch
  footer is dropped.
- `vectr_remember`'s tool schema trimmed (−317 tokens on tools/list) with no
  parameter, enum, default, or required-field changes.

### Hygiene
- Acceptance corpus cases now carry an `embed_model_stamp`; the harness
  reports stamp/embedder mismatches (informational) so a future embedder swap
  cannot silently stale-ify verified labels.
- README version/tool counts refreshed (15 MCP tools); generated-guidance
  template now lists `vectr_promote` and documents `vectr_remember`'s
  `triggers` argument.
- `scripts/release.sh`: one-command tag + push + GitHub release.

## 1.3.0 — 2026-07-18

Per-memory trigger engine, stdio MCP transport, instant memory readiness.

### Per-memory trigger engine
- `vectr_remember` accepts `triggers`: explicit per-note conditions for when
  a note resurfaces — `path` globs, lifecycle `event`s (session-start,
  prompt-submit, pre-edit, pre-run, pre-commit, post-compaction), exact
  `symbol` references resolved against the code symbol graph, `semantic`
  prompt-similarity with a fixed per-kind threshold, and temporal guards
  (`not_before`, `expires_visibility`, `cooldown`). Conditions AND within an
  entry and OR across entries; omitting `triggers` keeps the kind defaults.
- Trigger evaluation is wired into the live hook pipeline (session-start /
  prompt-submit / pre-tool-use / pre-compact) with a per-session fire
  ledger, cumulative injection budgets, scope enforcement, and
  double-injection prevention against the legacy relevance path.
- Provenance classes on notes with framing gates: auto-captured content is
  injected as epistemic memory, never as imperative instruction; forged
  human-provenance writes are rejected at both MCP and store boundaries.
- New `vectr_promote` MCP tool (the 15th): raise a reviewed auto-captured
  note's trust class one step (`auto` → `agent`). Promotion to `human` is
  reserved for user-side surfaces; the full chain remains available on REST.
- Kind-default scopes are resolved at write time; path triggers match both
  absolute and workspace-relative forms.

### Transport and readiness
- New foreground stdio MCP transport: `vectr mcp-stdio`.
- Two-phase service init: memory tools are live from process start on every
  transport, and warm-up notes are vector-backfilled when the embedder
  attaches — remember/recall no longer wait on model load.
- `vectr hook <event>` runs a stdlib-only fast path, cutting the per-hook
  subprocess import tax.

### Availability and correctness
- Embedding provider's torch thread pool is capped and MCP tool dispatch
  runs off the event loop; a full-workspace index or embed burst no longer
  starves concurrent requests.
- Fixed a shutdown-vs-init race in two-phase startup.
- `VECTR_WORKSPACE` pointing at a nonexistent path now fails loudly instead
  of silently indexing nothing.
- `vectr_map`'s raw-metadata path walks only indexed files (no more venv
  walks); the workspace fingerprint scan honors indexer exclusions.
- Injection packing stops at the first eviction and never backfills
  lower-ranked items; the per-turn recall relevance floor is config-driven
  (default 0.72).

### Tooling and display
- One displayed score scale per result set; resolved note scope is surfaced
  back to the caller; `vectr_status` nudges about stale task notes; the
  instruction-style label is renamed `memory-first`; a failing acceptance
  case can no longer crash the harness run.

### Research artifacts
- New `research/` directory: published evaluation artifacts for the
  brain-memory work — controlled-matrix and forced-compaction decay-probe
  protocols, graders, and complete run archives with per-directory READMEs.
  Not part of the PyPI package.

## 1.2.0 — 2026-07-13

Proactive context injection and daemon availability under load. All new
behavior is opt-in; with no new flags or consent given, behavior is
unchanged.

### Proactive context (new, opt-in)
- New `vectr proxy` command: a localhost, Anthropic-API-shaped proxy that
  deterministically injects relevant working-memory notes into an AI
  agent's request context when their trigger conditions match — no reliance
  on the agent voluntarily calling recall. Injection is consent-gated (a
  proactive master switch plus per-launch consent for the proxy channel)
  and fully observable: injection counts and end-to-end state are exposed
  in `/v1/status` and rendered in the proxy banner.
- Scored recall behind a new `/v1/proactive` endpoint; injections are
  budgeted, deduplicated, and fail open (an injection-path error never
  blocks the underlying request — it is logged and bypassed).
- Org-wide artifact cache and exact-match response cache wired into the
  proactive path.

### Daemon availability
- `/v1/index` now runs off the event loop (threadpool), so a full-workspace
  index call no longer blocks every other request for its duration.
- New `reindex_in_progress` field in `/v1/status`: a lock-free, always-cheap
  signal that bulk index work (an explicit index or the watcher's coalesced
  batch) is running right now.
- Per-language index statistics no longer trigger a full metadata scan on
  every status call while the index is changing; chunk totals read the
  vector store's native count directly.

### Watcher
- Live file events now honor `.gitignore` exactly like the bulk indexer
  (previously only `.vectrignore` was consulted on create/modify/delete/move
  events, so a gitignored file could enter the index through a live edit).

### Fixes
- `vectr forget --all` sweeps the current cache layout, not only the legacy
  nested layout.
- The proxy banner's status probe retries with a longer timeout instead of
  reporting a transient failure.

## 1.1.1 — 2026-07-12

### Indexing
- Indexing now streams embeddings into the vector store batch by batch
  instead of accumulating every embedding for the whole workspace in memory
  before writing. Both embedding passes (content and purpose vectors)
  previously held full-workspace embedding lists concurrently — gigabytes of
  memory on large workspaces, pushing memory-constrained machines into swap
  and slowing the second pass several-fold while it paged. Peak memory for
  this step is now one embedding batch. Indexed content is unchanged: chunk
  ids, documents, metadata, and embedding values are identical, so existing
  indexes remain valid and no reindex is required.

## 1.1.0 — 2026-07-11

Security and shared-deployment features. All opt-in; with none of the new
environment variables or flags set, behavior is unchanged — local, keyless,
zero-config stays the default.

### Authentication
- API key comparison now uses a constant-time comparison (`hmac.compare_digest`)
  instead of `!=`, removing a response-timing side channel. `VECTR_API_KEY`
  protects both the REST `/v1` routes and the `/mcp` endpoint; `/v1/health`
  stays open for liveness probes; the key is never echoed in responses or logs.
- New `vectr key` command prints a fresh high-entropy key (stdout) with usage
  guidance (stderr); vectr never persists it. Generated keys never start with
  `-` (a leading dash made `--api-key <key>` parse as a flag and fail); the
  usage guidance shows the always-safe `--api-key=<key>` form.
- When `VECTR_API_KEY` is set at start time, the editor MCP configs vectr
  writes (`.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`) include the
  `X-Api-Key` header so the editor keeps reaching its own authenticated daemon.
  These files then hold the key in plaintext; the CLI and docs warn to treat
  them as secrets and keep them out of shared version control.

### Team mode (shared central instance)
- `vectr start --host <addr>` selects the daemon bind address (default
  `127.0.0.1`). A non-loopback bind refuses to start unless `VECTR_API_KEY` is
  set — a network-reachable index is never served unauthenticated.
- New `vectr connect --url <url> [--api-key <key>] [--label <name>]` configures
  the local editor to use a remote vectr instance: writes the MCP configs with
  the auth header (and optional `X-Vectr-Client` attribution label) and the
  guidance block, spawning no local daemon. Working memory on the central
  instance is shared: a note one connected agent stores, every other can
  recall. Search results reference the server's indexed checkout.
- The client label attributes notes (author) and audit-log lines.
- Notes DB connections set an SQLite busy timeout so concurrent writes from
  multiple clients wait for the lock instead of failing.

### Encryption at rest
- `VECTR_ENCRYPT_KEY` now also encrypts note titles (previously plaintext, and
  derived from the first content line — leaking note content) and snapshot
  payloads (previously plaintext JSON embedding full decrypted note text).
  Reads are tolerant: pre-existing plaintext rows keep working.
- The passphrase can be stored in the OS keychain (service `vectr`, username
  `encrypt-key`) instead of the environment; the env var wins when both exist.
- New `VECTR_ENCRYPT_DISABLE_NOTE_VECTORS=1` omits note embedding vectors (a
  lossy plaintext projection of note text) for the strictest posture; recall
  falls back to exact-text matching.
- Honest boundary, unchanged: the code index (chunk text + vectors) is NOT
  encrypted — documented in docs/data-handling.md and the README.

### Data handling and retention
- The audit log is now strictly opt-in: `VECTR_AUDIT_LOG` unset means nothing
  is recorded (previously it silently defaulted to `~/.vectr/audit.log`,
  recording every recall query undisclosed). When enabled it also records
  INDEX and SEARCH events, and rotating-handler initialization is now
  race-free under concurrent writes.
- Purge is now complete: `vectr_forget(all=true)` / `POST /v1/forget` /
  `/v1/memory/clear` also delete the workspace's snapshots (whose payloads
  embed note contents), and `vectr forget --all` additionally clears snapshots
  and note embedding vectors across all workspaces. Previously all of these
  deleted only the notes table.
- `VECTR_NOTES_TTL_DAYS` (existing startup TTL purge) is now documented and
  covered by tests; unset = notes are kept until deleted.
- New data-handling policy: docs/data-handling.md — what vectr stores, where,
  plaintext vs encrypted, retention, deletion, team-mode caveats.

### Filesystem
- `~/.cache/vectr/` and `~/.vectr/` (and per-workspace subdirectories) are
  created owner-only (0700) on POSIX systems; existing directories are
  tightened at startup.

### Proactive context (experimental, off by default)
- New `vectr proxy` command runs a localhost Anthropic-shaped API proxy the
  agent harness targets with `ANTHROPIC_BASE_URL`. It forwards `/v1/messages`
  (and everything else) to the real API transparently — streaming SSE and
  tool_use pass through byte-for-byte, and the upstream API key is forwarded
  untouched and never stored or logged. Localhost-only: a non-loopback bind is
  refused. Bypass it at any time by unsetting `ANTHROPIC_BASE_URL`.
- When injection is enabled and the workspace daemon is running, the proxy
  appends deterministic proactive context (matched working-memory notes /
  structural matches) **after the last prompt-cache breakpoint**, so existing
  cache prefixes are never invalidated. Injection is fail-open: if the
  intelligence layer errors or exceeds a tight time budget, the request is
  forwarded unmodified; a proxy that cannot reach upstream returns an honest
  upstream-shaped error.
- Injection triggering is deterministic — structural exact file-path matches +
  a numeric cosine floor + additive packing — with a per-request item/char
  budget and per-session dedup/cooldown. No keyword/regex classification of
  conversation content anywhere.
- New `POST /v1/proactive` daemon route returns packed context for an assembled
  window (used by the proxy); scored recall (`recall_scored`) surfaces the
  per-note cosine similarity the semantic path already computes.
- Org-wide artifact cache (`proactive.cache`, off by default): caches
  `/v1/search` and scored-recall results keyed by exact identity + the current
  index epoch, so a re-index or note change invalidates automatically. On a
  team instance the cache is shared by every connected client. Exact-match
  local LLM-response caching in the proxy (`proactive.cache.response_cache`,
  off by default) serves a cached response only for a byte-identical request
  within a TTL; semantic-similarity response caching is deliberately not
  offered (see the design doc's cache-safety analysis).
- `vectr status` gains proactive-injection counts and artifact-cache metrics
  (hits/misses/hit-rate/entries/estimated tokens saved) when either is active.
  A `PROACTIVE_INJECT` audit event records metadata only (channel, item count,
  anchor ids) — never conversation text or note bodies.
- Config: new `proactive:` block in `config.yaml`; env overrides under the
  `VECTR_PROACTIVE*` prefix. All off by default; with nothing set, behavior is
  unchanged.

## 1.0.3 — 2026-07-11

- Added the MCP Registry ownership marker (`mcp-name: io.github.swapnanil/vectr`) to the README as an HTML comment — required by registry.modelcontextprotocol.io to validate that the PyPI package and the registry entry belong to the same publisher.
- `server.json` added for the Official MCP Registry listing (`pypi` package, `streamable-http` transport at `http://localhost:{port}/mcp`).
- README version badge and version line corrected (still said 1.0.0).

## 1.0.2 — 2026-07-11

- PyPI project page now renders the README (`readme` was missing from package metadata), plus license, author, and project URL metadata.
- Removed the vestigial `LLM_MODEL` env var, `agent/llm_client.py`, and the dead `model` field it echoed into `/v1/health`, `/v1/index`, `/v1/search`, and `/v1/status` responses. Vectr makes no LLM calls; the field had no consumers.
- `.env.example` updated to the shipped defaults: `ibm-granite/granite-embedding-english-r2` embedding model; removed the unused `ANTHROPIC_API_KEY` entry.
- `.vectrignore` untracked (repository-local indexing config, not product content).

## 1.0.1 — 2026-07-11

- Package description now covers both halves of the product: semantic codebase search and persistent working memory. The 1.0.0 description mentioned only the indexer.
- Install instructions updated to `pip install vectr` (PyPI release) in README, extension README, and docs page.
- Personal development configuration untracked from the repository (`CLAUDE.md`, `.mcp.json`, `.cursor/mcp.json`); local absolute paths in benchmark data and harness scripts genericized.

## 1.0.0 — 2026-07-08

**Semantic search**
- Hybrid dense (`ibm-granite/granite-embedding-english-r2`, local and overridable) + BM25 retrieval, weighted by codebase size/documentation characteristics.
- Dual-vector indexing: a body-stripped "purpose" vector (signature + docstring) alongside the full-body vector, closing the embedding-dilution gap where a symbol's own doc paraphrase of a query fails to surface it in the dense pool.
- Symbol graph with call edges, import chains, and HTTP route extraction (Flask/FastAPI/Express/Spring); `vectr_locate` (5 fallback strategies) and `vectr_trace` for callers/callees.
- AST-aware chunking across 7 languages (Python, JavaScript, TypeScript, Go, Rust, Java, Zig) plus C and C++; all other file types fall back to overlapping window chunking.

**Working memory**
- Five note kinds (`directive`, `task`, `gotcha`, `finding`, `reference`), each with distinct injection semantics — directives fire unconditionally, gotchas resurface when their anchored file is touched.
- Two-tier `vectr_recall`: a token-bounded index by default, full note bodies on request (`note_id=` or `detail='full'`).
- Session-start boot injection of directives and high-priority tasks, recency-ordered.
- `vectr_snapshot` / `vectr_snapshot_list` to checkpoint and browse working-memory state.
- Multi-agent shared memory: workspace-scoped notes act as a shared bus for orchestrator/subagent handoff, with explicit (never inferred) `agent` attribution on `vectr_remember`.

**Context relief**
- `vectr_fetch`: deterministic, byte-verbatim re-fetch of a previously-seen chunk by id, with a truncation warning when the index's own storage cap capped the original chunk.
- `vectr_evict_hint`, with recency-ordered re-fetch ids, so the calling model knows what it can safely drop from context.
- Remember nudges with dual-gate escalation (chunk count and token count) so a light reminder scales into a stronger banner only once both thresholds are crossed.

**Editor integration**
- Zero-config MCP setup: auto-written config for Claude Code, Cursor, and VS Code / GitHub Copilot; manual config documented for Windsurf, Cline, and Continue.
- Auto-generated `CLAUDE.md` guidance template describing the tool surface and when to use each tool.
- Claude Code session hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PreCompact`) for automatic recall injection, with an injection-observability status line.

**Ops**
- `--memory-only` daemon mode: working memory + hooks without code indexing or the file watcher, for actively-edited or performance-sensitive projects.
- File watcher burst governor and RSS self-limit to bound resource use during large or rapid file-change bursts.
- Multi-workspace instance registry (`vectr status --all`, `vectr stop --all`) and per-workspace status surfaces.
