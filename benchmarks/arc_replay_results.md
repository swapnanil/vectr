# Arc replay (G1 evidence gate) — LANE-ARC

Replays `app/arcs.py`'s `ArcDetector` over real agent-editor transcripts via
`benchmarks/arc_replay.py`, per the L1 capture design doc's §7 gate G1.

Command:

```
./.venv/bin/python benchmarks/arc_replay.py \
  "results/vectr-vs-bash/camel/6b422df/t2/*.jsonl" \
  "results/vectr-vs-bash/camel/1595096/t1c/*.jsonl"
```

## Corpora replayed

| Corpus | Sessions | Bash episodes | Edit episodes |
|---|---:|---:|---:|
| `camel/1595096/t1c` (C01–C06, bash+vectr arms) | 12 | 113 | 0 |
| `camel/6b422df/t2` (T2-01..04, bash+vectr arms) | 8 | 133 | 9 |
| **Total** | **20** | **246** | **9** |

## Result: 0 arcs emitted across all 20 sessions

```
TOTAL arcs across 20 sessions: 0
```

Per-session breakdown (arcs / bash-failures-or-soft-failures):

| Session | Bash | Edit | Failures | Arcs |
|---|---:|---:|---:|---:|
| C01_bash | 39 | 0 | 0 | 0 |
| C01_vectr | 20 | 0 | 0 | 0 |
| C02_bash | 14 | 0 | 0 | 0 |
| C02_vectr | 14 | 0 | 0 | 0 |
| C03_bash | 1 | 0 | 0 | 0 |
| C03_vectr | 2 | 0 | 0 | 0 |
| C04_bash | 8 | 0 | 0 | 0 |
| C04_vectr | 0 | 0 | 0 | 0 |
| C05_bash | 9 | 0 | 0 | 0 |
| C05_vectr | 12 | 0 | 0 | 0 |
| C06_bash | 13 | 0 | 0 | 0 |
| C06_vectr | 11 | 0 | 0 | 0 |
| T2-01_bash | 25 | 1 | 0 | 0 |
| T2-01_vectr | 26 | 0 | 0 | 0 |
| T2-02_bash | 9 | 2 | 0 | 0 |
| T2-02_vectr | 11 | 2 | 0 | 0 |
| T2-03_bash | 7 | 1 | 0 | 0 |
| T2-03_vectr | 4 | 1 | 0 | 0 |
| T2-04_bash | 9 | 1 | 0 | 0 |
| T2-04_vectr | 12 | 1 | 0 | 0 |

## Manual audit: this is a genuine zero-failure corpus, not a detector gap

Before accepting "0 arcs" as a pass, every one of the 246 Bash episodes across
all 20 sessions was independently checked (outside the detector, directly
against the raw transcripts) for any of: `is_error=True`, `BUILD FAILURE`,
a JUnit `Tests run: N, Failures: M` summary with `M>0`, a pytest `N failed`
line, `Traceback`, or `command not found`.

Findings:
- **`is_error=True` count: 0 / 246.** No Bash tool call in this corpus was
  ever flagged as an error by the tool layer.
- **35 content-marker hits, every single one paired with `BUILD SUCCESS`.**
  All 35 are `mvn test ...` invocations that passed on the first attempt —
  e.g. `mvn test -pl core/camel-core -Dtest=RouteTemplateLocalBeanTest ...`
  → `BUILD SUCCESS` / `Tests run: N, Failures: 0`. Zero maven invocations in
  this corpus ever failed.
- **T2 sessions (the ones with Edit calls) show single-shot success**: in
  every T2 session, the maven verification command that follows an `Edit`
  passes on its first invocation. There is no `fail → edit → rerun → pass`
  loop anywhere in this corpus for the detector to find.
- **T1c sessions (C01–C06) contain zero `Edit`/`Write` calls at all** — pure
  exploration (grep/find/locate), so an edit-mediated arc is structurally
  impossible there regardless of detector behavior.

**Conclusion: 0 arcs is the correct, audited answer for this specific
corpus.** It satisfies G1's zero-false-positive criterion (there is nothing
present that a correct detector could mistake for a mutation or an
edit-mediated fix), but this corpus cannot exercise G1's other criterion
("edit-mediated arcs found where transcripts actually contain fail→edit→
rerun-pass loops") — that loop does not occur here. The complementary
evidence (that the mechanism correctly finds such a loop when one exists,
and correctly suppresses genuine flaky retries) is carried by the
synthetic, table-driven unit suite instead (`tests/test_arcs.py`,
`TestEditMediatedVsFlaky` — 8 tests using constructed fail→edit→identical-
retry and fail→identical-retry-no-edit sequences), since no corpus
available at gate time contains a real one.

No suppressions were logged either (0 flaky-retry suppressions, 0
near-threshold suppressions) — there was nothing pending to suppress.

## Threshold/formula changes made while building this lane

None of the below were tuned against this corpus (it has no failures to
tune against) — all three were found and fixed while building the
synthetic unit-test suite, against constructed cases, not this replay
corpus. All spec-stated numeric defaults (mutation band 0.55–0.999, weights
0.5/0.3/0.2, verb soft-match ratio 0.8 → score 0.7) are unchanged from
`memoization-l1-capture-design.md` §3.2.

1. **`app/cmdnorm.py` `classify_arg` precedence** — the loose
   extension-shaped path check (`name.ext`) was tried before the numeric
   check, so a bare decimal like `3.14` misclassified as `<PATH>` instead
   of `<NUM>` (it satisfies both patterns). Reordered so num is checked
   first; the unambiguous path indicators (`/`, `.`, `..`, `~`-prefix)
   still take precedence over num, since they never collide with a number.
2. **`app/cmdnorm.py` `path_extension_regex`** — broadened to tolerate glob
   wildcards (`*`, `?`) in the filename-stem portion, so `find . -name
   "*.java"`'s glob argument classifies as `<PATH>` like any other
   extensioned filename instead of staying an unclassified literal.
3. **`app/arcs.py` verb-family bucketing** — pending buckets were
   originally keyed by the *full* normalized verb string. The verb
   absorption cap (`max_verb_tokens=3`, spec's own `npm run build`
   example) folds a trailing bareword target into the verb for shapes
   like `pip install requests`, so two retries with different package
   names/branches/scripts almost never share a bucket key and the
   flag/arg similarity comparison never runs. Fixed by bucketing on the
   verb-family (first token = the invoked binary) while keeping the full
   verb string in the similarity formula itself — decouples coarse
   windowing from fine-grained match scoring.
4. **`app/arcs.py` `_args_component` different-arity fallback** — jaccard
   over abstracted arg classes can reach a perfect 1.0 purely because the
   *set* of classes is unchanged (e.g. adding a second `<PATH>` arg), but
   1.0 is reserved for `is_identical_command`'s exact-equality check —
   an unpenalized 1.0 here silently made "added a missing argument" fixes
   invisible to the mutation-band check. Penalized by the relative arity
   ratio (`min(len)/max(len)`) so a differing arg count can never present
   as a perfect match.
5. **`app/arcs.py` anchor tie-break** — when two pending failures score
   identically against a success, a plain `sort(reverse=True)` (stable)
   kept the *oldest* tied candidate as the anchor, stranding a more-recent
   equally-similar failure in pending unconsumed. Tie-break now prefers
   recency (`sort(key=(score, command_index), reverse=True)`).

All five are covered by a dedicated unit test asserting the corrected
behavior (see `tests/test_cmdnorm.py::TestClassifyArg`,
`TestNormalizeCommandArgs::test_glob_pattern_arg_classified_as_path`,
`tests/test_arcs.py::TestChainsAndInterleaving::test_chain_backward_
through_pending_failures`,
`test_tie_break_prefers_most_recent_equally_similar_failure`, and
`TestSimilarity::test_different_arity_args_falls_back_to_class_jaccard`).

## Adversarial review 2026-07-22 (bcbcb50) — five further fixes, re-run confirms 0 arcs unchanged

An Opus review of the initial landing found five additional defects, all
in constructed corner cases the two corpora above never happen to exercise
(neither corpus has multi-stage pipelines, wrapper-prefixed commands,
cross-cwd retries, empty-verb episodes, or missing timestamps). The design
spec (`memoization-l1-capture-design.md`) was amended (`e736c24`) to
resolve the two genuine spec ambiguities (cwd's role, pipeline collapse)
before these were coded:

1. **`app/arcs.py` pending-bucket key** — was verb-family alone; a failure
   in one cwd and an identical/similar-scoring success in a *different*
   cwd could falsely bind into an arc (unrelated repos' builds). Spec
   ruling: cwd is a bucket key, not a mutation axis (it cannot differ
   within a bucket by construction) — `mutation_diff`'s `cwd` axis is
   removed accordingly. Bucket key is now `(verb-family, cwd)`.
2. **`app/cmdnorm.py` pipeline-stage collapse** — collapsed unconditionally
   to pipeline stage 0, so `cat data.csv | python train.py` and `cat
   data.csv | python eval.py` normalized identically. Fixed: only a
   *trailing* run of display-only stages (`cat`/`tail`/`head`) is dropped;
   any remaining non-trailing stage's tokens stay in the comparison set.
3. **`app/cmdnorm.py` wrapper-prefix stripping** — `timeout N`, `env
   VAR=...`, `nice [-n N]`, `nohup`, `stdbuf -xX` were left as part of the
   verb, so `timeout 60 curl X` and `timeout 90 curl X` scored as a
   distinct mutation instead of an identical retry. Fixed: these five
   wrapper prefixes are stripped iteratively before verb extraction (config
   keys `arc_detection.normalization.wrapper_prefixes` / `nice_niceness_
   flag`); `xargs` is deliberately never stripped (its argument is a
   command template, not the command that ran).
4. **`app/arcs.py` empty-verb handling** — an episode normalizing to an
   empty verb (`2>&1` alone, an env-assignment-only command) carries no
   comparable structure but was still reaching the pending/match logic.
   Fixed: treated like outcome `unknown` in `ArcDetector.observe` — never
   enters pending, never resolves one.
5. **`app/arcs.py` `observe()` timestamp robustness** — `_parse_ts(episode
   ["ts"])` raised on a missing key or an explicit `None` value. Fixed:
   `_resolve_ts` falls back to a small monotonic step past the session's
   own last-seen ts (config key `arc_detection.window.
   ts_monotonic_fallback_seconds`) instead of raising.

All five are config/logic fixes only — no numeric default from §3.2
(mutation band, similarity weights, soft-match ratio) changed. 19 new
unit tests were added across `tests/test_cmdnorm.py` (pipeline collapse,
wrapper prefixes, empty verb) and `tests/test_arcs.py` (cross-cwd
isolation for both the identical-command and mutation-band paths,
multi-stage-pipeline edit-mediation, wrapper-prefix flaky suppression,
empty-verb plumbing, missing/`None` `ts`).

**Re-ran `benchmarks/arc_replay.py` over the identical two corpora after
all five fixes: result unchanged — `TOTAL arcs across 20 sessions: 0`,
identical per-session bash/edit/failure counts to the table above.** None
of the five findings are exercised by this corpus (confirmed above: no
pipelines, no wrapper prefixes, single cwd throughout, no empty-verb
episodes, real timestamps on every episode) — regression coverage for all
five lives in the unit suite, per the same evidence-gate reasoning as the
original three fixes.
