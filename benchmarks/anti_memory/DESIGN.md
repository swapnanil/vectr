# EVAL-ANTI-MEMORY — design

Status: DESIGN (no cells run, no product code changed). An extension of
`benchmarks/longitudinal_rediscovery/DESIGN.md`, which is the substrate: every
primitive named without definition here is defined there. Implementable by a coder
lane without further design decisions except the ones listed in §16. Every paid run
is gated on sentinel approval, tier by tier (§13).

---

## 1. What this measures, and why it is not the longitudinal eval

The longitudinal harness measures what a *correct* note buys across sessions: an agent
that lacks a fact pays to re-discover it, or errs. Every note in it is true, and the
workspace is authored so it never becomes a competing memory (that harness's §2.2
residue rule exists to *prevent* the workspace teaching the fact).

This eval inverts exactly that rule. Here the workspace **is** a competing memory, and
it teaches something **false**:

> A belief the agent once held was falsified. The memory system knows. The workspace
> does not — its docs, comments, vendored copies and legacy call sites still teach the
> old fact, and nothing in it says the old fact is wrong. The task at hand tempts the
> agent to read one of those artifacts and act on it.

This is the workspace-mediated arm of what Agentic Unlearning (arXiv:2602.17692) names
**backflow**: a fact removed from one place re-enters the agent's context from another
and is acted on, then written back. That paper names the problem and solves it in the
weights; it does not model external workspace artifacts as the re-entry path, and it
explicitly does not consider injecting a retraction record as context. STALE
(arXiv:2605.06527) supplies the task shape — *implicit conflict*, where a later
observation invalidates an earlier belief without explicitly negating it — and the
headroom argument: the best frontier model reaches 55.2%.

In vectr's own terms this is context debt of the worst kind. Ordinary context debt is
paid in turns: the agent re-reads what it already knew. Here the debt is paid in
**wrong bytes**: the agent re-derives a falsified fact and ships it, and the artifact
it ships becomes the next session's stale source.

**The headline quantity** (defined mechanically in §7):

| | question |
|---|---|
| **BACKFLOW-SHIPPED RATE** | fraction of sessions whose *final workspace bytes* encode the falsified fact |

**The scientific question**, stated so it can be answered "no":

> Does proactively re-injecting a revocation record — the falsified claim, its reason,
> its actor, its date, and a do-not-re-derive instruction — reduce backflow relative to
> (a) deleting the note, (b) keeping the revocation but only answering it on demand,
> and (c) simply storing the corrected fact instead?

Contrast (c) is the one that can kill the mechanism, and it is in the headline tier for
that reason. See §5.3.

**What this eval deliberately does not measure**, stated up front:

- **the write half.** All notes and all lifecycle transitions are performed by the
  harness over REST, identical across arms (§6.3). Whether an agent *proposes* a
  revocation is a separate experiment.
- **retrieval ranking.** At most two notes per store.
- **parametric backflow.** The model's own prior is held constant across arms and is
  not manipulated; this measures the workspace-artifact re-entry path only.
- **staleness detection.** The revocation is planted, not discovered by anchor drift.
  Proxy-anchor drift (the deterministic staleness signal) is a separate question.

---

## 2. The mechanism as shipped — what renders, where

Read from the worktree copy of vectr before designing. This section is the ground truth
the gates in §5.4 and the T0 probe in §13 assert against; anything here that turns out
to be false at implementation time is a defect in this document, not a thing to work
around.

**State machine** (`agent/working_context_store/_events.py`). Append-only
`note_events` log; `fold()` derives lifecycle state from it, ordered by the log's
monotonic `id`, never by `ts`. `NOTE_EVENT_KINDS = (created, superseded, revoked,
stale_flagged, reinstated, promoted)`; `_LIFECYCLE_STATE_AFTER` maps only
`created|superseded|revoked|reinstated` to a state, so `stale_flagged` and `promoted`
are audit-only. `NOTE_EVENT_ACTORS = (agent, human, system)`. `fold()` returns
`{state, reason, actor, ts}` of the last state-changing event — the deterrent's payload,
with no second query.

**The deterrent template** (`agent/working_context_store/_store.py`,
`_ANTI_MEMORY_TEMPLATE`), verbatim:

```
Previously believed (recorded {created_date}, revoked {revoked_date}, reason: {reason}):
"{summary}". Do not re-derive this from other sources without verification.
```

**Where a revoked note renders, and as what:**

| surface | code | rendering |
|---|---|---|
| `vectr_recall` index tier | `_format_index_line` | ` [REVOKED]` marker appended; content never shown at this tier |
| `vectr_recall` full tier / expand | `_format_full_block` revoked branch | the deterrent block **replaces** the raw content entirely; takes precedence over kind/provenance/scope/superseded markers |
| hook / trigger firing | `fire_and_format` → `_format_full_block(injected=True)` | same deterrent block; the `injected` frame does **not** apply to the revoked branch |
| boot recall | same store formatter | same |
| proxy injection | `agent/proactive/matcher.py` `_revoked_summary` | single-line; same template, then `rpartition(". ")` moves the trailing do-not-re-derive clause to the **front** so `_cap()`'s right-truncation cannot remove the warning |
| related-notes | `_related.py` | revoked notes are **not** excluded |

**Five implementation facts that constrain this design.** Each was verified in the
worktree copy; each changes the design.

1. **The deterrent quotes the note's TITLE, not its body.** Both
   `_ANTI_MEMORY_TEMPLATE` call sites pass `summary=_note_title(n)`, and `_note_title`
   is `note.title or note.content.strip().splitlines()[0][:80]`. So the falsified claim
   the agent sees is at most ~80 characters, and **the revocation `reason` is the only
   unbounded payload channel in the whole mechanism.** Consequences: (i) every scenario's
   falsified note must carry its claim in an explicit `title`; (ii) the `reason` field
   is a first-class scenario field with its own variant ladder (§10), not boilerplate.
2. **The proxy channel is trigger-agnostic.** `VectrService.proactive_context()` selects
   candidates through `recall_for_path` (structural) and `recall_scored` (semantic).
   Neither consults `triggers`, so `not_before` **cannot** suppress proxy injection.
   The hook/trigger surface (`fire_and_format` → `effective_triggers`/`evaluate_note`)
   **does** honour it. This is what makes a per-note passive ablation possible on the
   hook channel and impossible on the proxy channel — see §5.5.
3. **The structural channel filters by kind.** `structural_kinds` defaults to
   `(gotcha, finding, decision, operational, reference)` — `task` and `directive` are
   excluded — and the proxy channel additionally drops `directive` notes wholesale
   (`proxy_exclude_directive_notes`). Planted notes are therefore `kind="gotcha"`
   (a file-anchored caveat, the natural fit and structurally eligible).
4. **The proxy's fold fails closed.** `_ServiceMatchSource.note_states` is deliberately
   un-wrapped: a fold failure propagates and `ProactiveMatcher.match()` drops the note
   candidates rather than injecting a revoked note's raw content as fact. Operationally
   that means *a broken fold and "nothing matched" both look like zero injection*, so
   the non-vacuity gates must read `PROACTIVE_INJECT` audit lines and the preflight
   probe's returned context — never `item_count` alone.
5. **On the proxy surface the reason is the first thing truncated.** After the clause
   reordering, the injected line reads `Do not re-derive this from other sources without
   verification. Previously believed (recorded …, revoked …, reason: …): "…".` and
   `_cap()` cuts from the right at a sentence boundary, against a
   `max_chars_per_event: 800` budget shared by up to `max_items_per_event: 3` items.
   The deterrent instruction is protected by construction; **the reason and the quoted
   claim are not.** T0 therefore measures the surviving character count and the
   scenario-authoring reason-length rule is set from that measurement, never guessed
   (§13, T0-4).

Both `"Previously believed ("` and `"Do not re-derive this from other sources without
verification."` appear on **both** surfaces (only their order differs), so a single
substring assertion is valid on either — but the *reason* assertion is surface-specific.

**Control plane the harness needs — all shipped, all REST, no product change:**

| endpoint | used for |
|---|---|
| `POST /v1/remember` (`contradicts`, `triggers`, `anchors`, `kind`, `title`, `agent`, `scope`) | plant notes; `contradicts=N` writes the new note and revokes N in one call |
| `POST /v1/revoke` (`note_id`, `reason`, `actor`) | explicit revocation with a custom reason |
| `POST /v1/reinstate` (`note_id`, `reason`, `actor`) | the §9 probe |
| `POST /v1/forget` (`note_id` \| `all`) | ARM-DEL and ARM-REPLACE store states |
| `POST /v1/proactive` | the $0 preflight probe — returns `context`, `item_count`, `anchor_ids` |
| `POST /v1/recall`, `GET /v1/status` | ARM-AUDIT reachability probe; `notes_count` gates |
| `vectr proxy --no-inject` | the delivery-initiative factor |

---

## 3. Scenario class: STALE-STYLE IMPLICIT WORKSPACE CONFLICT

A scenario is admitted only if it satisfies all seven criteria. 1-4 are inherited from
the longitudinal harness's admission rules; 5-7 are what make it an anti-memory
scenario.

1. **Mechanically observable ground truth.** Acting on the old fact and acting on the
   new fact differ in bytes on disk or in commands run, never merely in prose. The
   no-LLM-judge rule is inherited verbatim.
2. **Synthetic.** Every workspace is authored for this eval. No third-party corpus
   content, no names or facts from django / cpython / uv / camel / TigerBeetle, and the
   STALE dataset itself is never imported — style inspiration only.
3. **Neutral `CLAUDE.md`.** Every scenario ships one, so no arm differs by the existence
   of a project file.
4. **No fact token leaks.** A test asserts that no scenario file and no prompt contains
   any of the scenario's declared `new_fact_tokens`, except in the declared truth-source
   file.
5. **The falsified fact `F_old` was true and is now false**, and **`F_new` is true.**
6. **Stale artifacts still teach `F_old`.** At least one prominent, naturally-reached
   workspace file states or demonstrates `F_old`. These are the backflow sources and are
   enumerated in a declared `stale_artifacts` list.
7. **The conflict is IMPLICIT.** Nothing in the workspace negates `F_old`. No deprecation
   warning, no "as of vX", no comment saying the old path is dead. This is STALE's
   defining property and the single easiest criterion to violate by accident; a test
   asserts the workspace contains none of a declared `explicit_negation_tokens` list
   (`deprecat`, `no longer`, `removed in`, `use .* instead`, `legacy`, `obsolete`).

### 3.1 Recoverability, and why it is the class's primary axis

Whether `F_new` is present in the workspace at all splits the class in two, exactly as
DISCOVERED/TOLD splits the longitudinal class. It is declared per scenario as
`recoverable: bool` and it determines what a null means.

**RECOVERABLE.** Both `F_old` (stale, prominent, cheap) and `F_new` (current, real,
less prominent) are in the workspace. A careful agent gets it right with no memory at
all. This is STALE's implicit-conflict proper, it is the **primary** class, and it is the
only class in which the no-memory floor rate is informative: `backflow(ARM-DEL)` measures
how often the stale artifact out-votes the live code.

**UNRECOVERABLE.** Only `F_old` is in the workspace; `F_new` exists only in memory.
`backflow(ARM-DEL)` is ~100% by construction, which makes the floor uninteresting — but
it maximises **artifact pressure**, and §8's mechanism-specific prediction is that the
deterrent's advantage over a corrective note *grows* with artifact pressure. This class
is where that prediction is tested.

### 3.2 Artifact pressure, declared and frozen

Each scenario declares `artifact_pressure: low | medium | high`, frozen in the scenario
file before any cell, with an operational definition so it is not a post-hoc label:

```
pressure = f(number of stale artifacts teaching F_old,
             whether the most prominent doc in the repo teaches F_old,
             whether F_new appears in the workspace at all,
             whether the F_old action appears to succeed)
low     1-2 stale artifacts, F_new plainly visible in the obvious source file
medium  3+ stale artifacts including the top-level doc, F_new present but off the path
high    F_new absent from the workspace, or the F_old action exits 0 and looks correct
```

This is a scenario-authoring property, never anything the serving path reads.

### 3.3 Residue: a threat there, the independent variable here

The longitudinal harness's §2.2 residue rule says a later session must not be able to
read the fact off an earlier session's output. This design engineers the opposite: the
stale artifact **is** engineered residue. Two consequences:

- In **REPLICATE mode** (§6.1, primary) residue cannot accumulate: every cell restores
  the authored fixture. The stale artifact is byte-identical across every arm and every
  replicate, which is a strictly stronger control than the parent's shared-leg-1 tar.
- In **CHAIN mode** (§6.1, secondary, T7) residue is the measurement: a leg that ships
  backflow writes a *new* stale artifact, raising pressure for leg *k+1*. That is the
  full recontamination loop and it is the strongest single observation this design can
  produce — but it is a slower, more expensive, more confounded mode, so it is a late
  tier and never the headline.

---

## 4. The four scenarios

| # | slug | recoverable | pressure | falsification shape | `F_old` → `F_new` |
|---|---|---|---|---|---|
| A1 | `retired_flag_api` | yes | medium | **path + API moved** | `flags.get(name)` over `config/flags.yaml` → `flags.resolve(name, ctx)` over `config/flags.d/*.toml` |
| A2 | `inverted_default` | yes | low | **semantics inverted in place** | `normalize()` strips trailing whitespace by default → preserves it unless `strip=True` |
| A3 | `noop_make_target` | yes | medium | **command silently became a no-op** | `make seed` seeds the dev DB → `make seed` exits 0 doing nothing; `tools/fixtures load` is the real path |
| A4 | `handwritten_notes` | **no** | high | **process replaced, unrecorded** | release notes are hand-written into `NOTES.md` → generated from commit trailers; hand edits are overwritten |

Declared fields per scenario (dataclasses in `scenarios.py`, re-exporting the
longitudinal stubs' primitives):
`slug, recoverable, artifact_pressure, agent_cwd, files, executable, git_init,
old_fact_title, old_fact_body, new_fact_sentence, revocation_reasons{bare,causal,
corrective}, stale_artifacts[], truth_source, explicit_negation_tokens,
new_fact_tokens, prompt, backflow_signature, backflow_content, correct_check,
stale_artifact_read, truth_source_read, verify_scripts, pre_registered_expectations`.

---

### A1 `retired_flag_api` — RECOVERABLE, pressure MEDIUM (the headline scenario)

**Workspace** `quillstream/` (git repo, one initial commit):

```
README.md                          links docs/flags.md as "how flags work"
docs/flags.md                      STALE. Teaches flags.get(name) + config/flags.yaml,
                                   with a worked example. No deprecation note anywhere.
config/flags.yaml                  STALE. Four entries, still parseable, read by nothing.
config/flags.d/checkout_v2.toml    LIVE. Real entries in the new format.
config/flags.d/slow_search.toml    LIVE.
src/quillstream/flags.py           resolve(name, ctx) — the live path.
                                   get(name) — a shim whose body returns the declared
                                   default. Its docstring says so plainly. It does NOT
                                   warn, does NOT say "deprecated", is NOT decorated.
src/quillstream/api/export.py      the file to edit; imports resolve
src/quillstream/api/search.py      uses resolve (live call site)
src/quillstream/legacy/report.py   uses get  (stale call site)
src/quillstream/legacy/digest.py   uses get  (stale call site)
src/quillstream/legacy/mailer.py   uses get  (stale call site, with a
                                   "# see docs/flags.md" comment)
tests/test_flags.py                covers resolve only
CLAUDE.md                          neutral
```

Three stale call sites against two live ones plus a stale top-level doc: the majority
vote in the workspace points at `F_old`, while the truth is one file away. That is the
pressure calibration, and §8's G0 gate measures whether it worked.

**`F_old`** (the planted note's `title`, ≤80 chars by construction):

> Feature flags are read with flags.get(name) from config/flags.yaml.

**`F_old` body** (names the stale artifacts so the structural matcher fires on them —
the longitudinal harness's verified mechanic is that the matcher keys on paths mentioned
in the note text):

> Feature flags are read with `flags.get(name)` from `config/flags.yaml`. The flag set
> is declared in `config/flags.yaml` and documented in `docs/flags.md`; add a new flag
> by adding a key there and reading it with `flags.get`.

**`F_new`** (the corrected note, active, planted in ARM-REPLACE and ARM-DETERRENT):

> Feature flags are resolved with `flags.resolve(name, ctx)` against
> `config/flags.d/*.toml`, one file per flag. `flags.get` is a compatibility shim that
> always returns the declared default and reads nothing.

**Revocation reasons** (§10 ladder; the `corrective` rung is the T1 default):

```
bare        no longer true
causal      config/flags.yaml was migrated to config/flags.d/*.toml on 2026-05-14
corrective  config/flags.yaml was migrated to config/flags.d/*.toml on 2026-05-14;
            flags.get is now a shim that always returns the default.
            Verify: grep -n "def get" src/quillstream/flags.py
```

**Prompt** (identical in every arm and every replicate):

> Add a `beta_export` feature flag, default off, and gate the CSV export endpoint in
> `src/quillstream/api/export.py` on it.

**Ground truth**

- `backflow_signature` (action stream, → `backflow_attempted`):
  `ContentAction(("Write","Edit"), r".*", r"\bflags\.get\s*\(")`,
  `PathAction(("Write","Edit"), r"config/flags\.yaml$")`.
- `backflow_content` (final bytes, → `backflow_shipped`, the primary):
  `FileMatches("*.py under src/", r"\bflags\.get\s*\(\s*[\"']beta_export")` OR
  `FileMutated("config/flags.yaml")`.
- `correct_check` = `AllOf("gated_on_new_api", [
  FileExists("config/flags.d/beta_export.toml"),
  FileMatches("src/quillstream/api/export.py", r"flags\.resolve\s*\(\s*[\"']beta_export"),
  FileUnchanged("config/flags.yaml")])`.
- `stale_artifact_read`: `PathAction(("Read","Grep"), r"(docs/flags\.md|config/flags\.yaml|src/quillstream/legacy/)")`.
- `truth_source_read`: `PathAction(("Read","Grep"), r"src/quillstream/flags\.py$")`,
  `BashAction(r"ls[^\n]*config/flags\.d|grep[^\n]*def resolve")`.
- `truth_source` = `src/quillstream/flags.py`.
- `new_fact_tokens` = `("flags.d", "resolve(")` — asserted absent from every file except
  the live ones, and absent from the prompt.

---

### A2 `inverted_default` — RECOVERABLE, pressure LOW

**Workspace** `slatepipe/`: `src/slatepipe/pipeline.py` (live —
`normalize(value, *, strip: bool = False)`), `vendor/slatepipe_0_4/pipeline.py` (a
vendored copy of the 0.4 implementation whose docstring states the old default and whose
body performs it), `docs/api.md` (stale — documents the old default with an example),
`src/slatepipe/importers/csv_import.py` (the file to edit), `tests/`, neutral
`CLAUDE.md`. Nothing anywhere says the default changed.

`F_old` title: *`normalize()` strips trailing whitespace by default.*
`F_new`: *`normalize()` preserves whitespace since 0.5; pass `strip=True` to strip.*

**Prompt**: `Imported names from the CSV importer still have trailing spaces. Fix it.`

`backflow_content`: final bytes contain a `normalize(` call on the imported name with no
`strip=` argument and no separate strip, OR a test asserting the old default.
`correct_check`: `normalize(..., strip=True)` present (or an explicit `.rstrip()`) and
the importer's round-trip verify script exits 0.

Low pressure by construction (one stale doc plus one vendored copy; the live signature
is in the obvious file). It is the design's **floor calibrator**: if even A2 produces a
high `backflow(ARM-DEL)`, the model's tendency to trust stale artifacts is stronger than
assumed and A1/A4's results should be read in that light.

---

### A3 `noop_make_target` — RECOVERABLE, pressure MEDIUM, *silent* failure

**Workspace** `beaconctl/`: `README.md` and `CONTRIBUTING.md` (both stale — both say
`make seed`), `Makefile` (`seed:` body is `@echo "seed: nothing to do"` — visible if
read, silent if run, exits 0), `db/seeds/` (stale directory, three old `.sql` files, read
by nothing), `tools/fixtures` (executable; `load <name>` against `tools/fixtures/data/`),
`tools/fixtures/data/*.json` (live), `src/beaconctl/`, neutral `CLAUDE.md`.

`F_old` title: *Seed the dev database with `make seed`; seed data lives in `db/seeds/`.*
`F_new`: *Seed data is loaded with `tools/fixtures load <name>` from
`tools/fixtures/data/`; the `make seed` target is an empty compatibility stub.*

**Prompt**: `Add seed data for the new regions table and load it into a fresh dev DB.`

`backflow_signature`: `BashAction(r"make\s+seed\b")`, `PathAction(("Write","Edit"), r"^db/seeds/")`.
`backflow_content`: a new file under `db/seeds/`, OR the `Makefile`'s `seed` target
mutated. `correct_check`: a new `tools/fixtures/data/regions.json` **and**
`CommandRan(r"tools/fixtures\s+load")` **and** `FileUnchanged("Makefile")`.

Carries the longitudinal S5 property — the mistaken action appears to succeed — which
makes it the scenario where self-correction is least likely and the deterrent's value,
if any, should be most visible.

---

### A4 `handwritten_notes` — UNRECOVERABLE, pressure HIGH

**Workspace** `cinderbox/`: `NOTES.md` (a long, well-formed release-notes file with six
past versions, all hand-written in an obvious house style — the artifact is maximally
persuasive precisely because it is a *record of the old process having been followed*),
`.git` with real commit history carrying `Release-Note:` trailers on recent commits (the
only trace of `F_new`, and it is a *trace*, not a statement), `src/cinderbox/`,
`CHANGELOG.md`, neutral `CLAUDE.md`. There is no generator script in the workspace and
no CI file naming one.

`F_old` title: *Release notes for cinderbox are written by hand into NOTES.md before tagging.*
`F_new`: *Release notes are generated from `Release-Note:` commit trailers; hand edits to
`NOTES.md` are overwritten by the release job.*

**Prompt**: `Write the release notes for 2.3.0.`

`backflow_content`: `FileMutated("NOTES.md")`.
`correct_check`: `AllOf([FileUnchanged("NOTES.md"), a commit or amend carrying a
"Release-Note:" trailer for 2.3.0])`, via a scenario-owned verify script over `git log`.

**A4 is the scenario most hostile to a fair reading and must be reported with that
caveat attached.** `F_new` is unrecoverable, so an agent in ARM-DEL cannot possibly
comply; the only interesting question in A4 is whether a proactively injected
*deterrent* moves outcomes that a proactively injected *corrective note* does not, when
the stale artifact is a six-entry history of the old process. It is the sharpest test of
§8's pressure prediction and the weakest test of everything else.

---

## 5. Arms

### 5.1 The two factors

Two orthogonal factors are being crossed, and each arm is a faithful instantiation of a
real system rather than an arbitrary cell:

**Factor S — what the memory system did with the falsified belief.**

| | store state after setup |
|---|---|
| `S-DEL` | `F_old` note hard-deleted. Store empty. |
| `S-REPLACE` | `F_old` note hard-deleted; `F_new` note active. |
| `S-REVOKE` | `F_old` note **revoked** with reason + actor + ts; `F_new` note active. |

`S-REVOKE` is exactly what shipped `vectr_remember(content=F_new, contradicts=N)`
produces in one call. `S-REPLACE` differs from it by **exactly one thing**: the
revocation record does not exist.

**Factor D — delivery initiative.**

| | wiring |
|---|---|
| `D-PASSIVE` | `vectr proxy --no-inject`, no hooks. MCP tools present; the agent may query. |
| `D-PROACTIVE` | proxy injection ON, no hooks. |

### 5.2 The four arms

| arm | id | store | delivery | the system it stands for |
|---|---|---|---|---|
| **ARM-DEL** | `del` | `S-DEL` | `D-PROACTIVE` (nothing to inject) | mem0 `DELETE`, LangMem consolidation-overwrite, AGM contraction (Kumiho 2603.17244) |
| **ARM-AUDIT** | `audit` | `S-REVOKE` | `D-PASSIVE` | TOKI 2606.06240 bitemporal audit, Zep/Graphiti `t_invalid`, mem0 history endpoint |
| **ARM-REPLACE** | `replace` | `S-REPLACE` | `D-PROACTIVE` | mem0 `UPDATE`, Supersede 2606.27472 |
| **ARM-DETERRENT** | `deterrent` | `S-REVOKE` | `D-PROACTIVE` | vectr as shipped |

**The wire path is held constant.** Every arm runs the agent through a scratch
`vectr proxy` and every arm receives the identical explicit MCP config
(`--strict-mcp-config --mcp-config <cell>/mcp.json` naming only vectr at the scratch
port). This is the longitudinal harness's rule carried forward, and it is stricter here:
in that harness MCP presence itself varied by arm, whereas here **only the store state
and the single `--no-inject` flag differ.** ARM-DEL keeps injection enabled with an empty
store precisely so its `injected == 0` is *evidence the store is empty*, not an artifact
of a flag.

### 5.3 The two decisive contrasts

| contrast | holds constant | isolates | answers |
|---|---|---|---|
| **ARM-DETERRENT vs ARM-AUDIT** | store state | delivery initiative | Is *proactive re-injection* the thing that matters? This is the component the novelty sweep found **unclaimed anywhere** (§Q8(c): "Nothing found"), so this contrast carries the paper's novelty. |
| **ARM-DETERRENT vs ARM-REPLACE** | delivery, wire, and the `F_new` note | the revocation record's existence | Does keeping the retraction buy anything *over simply storing the correction*? This is the skeptic's contrast and it is the one that can kill the mechanism. |

ARM-DEL is the floor and the scenario-validity gate, not a contrast of interest.

**The asymmetry is stated plainly and is not papered over.** ARM-DETERRENT delivers
strictly more content than ARM-REPLACE — the same `F_new` note plus a deterrent block —
so a DETERRENT win is confounded with injected volume. Three responses, in order:

1. `injected_chars` is recorded per leg as a covariate and printed next to every rate.
2. The asymmetry is **intrinsic to the mechanism**: the record *is* the extra content.
   Removing it would make ARM-REPLACE an unfaithful representation of mem0/Supersede.
3. A length-matched placebo arm (§5.6) is pre-priced as a **conditional tier that only
   spends money if there is an effect to explain.**

And the deterrent is, informationally, the *weaker* payload: it names the falsified
claim and forbids re-deriving it, but the corrective content rides only in the
`reason` field (§2, fact 1). ARM-REPLACE's note says what is true, in full, unbounded.
**The design therefore starts with the deterrent at a disadvantage, which is the
intended posture.**

### 5.4 Per-arm non-vacuity gates

A leg counts only if its arm's premise is independently confirmed. Any failure ⇒
`valid:false` with an `invalid_reason`, excluded from every rate, **never** read as "the
deterrent did not help". Audit events are counted from a byte offset taken after the
preflight probes settle (the longitudinal harness's `_settled_audit_size` idiom).

| arm | gate (all must hold) |
|---|---|
| **all** | `system.init` present; `mcp_servers == [{"name":"vectr","status":"connected"}]`; `result.subtype == "success"`; the restored workspace's sha256 manifest equals the fixture's recorded manifest |
| **ARM-DEL** | `notes_count == 0`; zero post-offset `PROACTIVE_INJECT`; proxy `injected == 0` |
| **ARM-AUDIT** | `notes_count == 2`; a daemon-side `POST /v1/recall` preflight with the leg prompt returns **both** notes and the revoked one renders the deterrent (contains `"Previously believed ("`); zero post-offset `PROACTIVE_INJECT`; proxy `injected == 0` |
| **ARM-REPLACE** | `notes_count == 1`; the `/v1/proactive` preflight context **contains** `F_new`'s text and **contains no occurrence of** `"Previously believed ("`; proxy `injected > 0` |
| **ARM-DETERRENT** | `notes_count == 2`; the `/v1/proactive` preflight context contains `"Previously believed ("` **and** the scenario's revocation `reason` verbatim (fact 5 of §2 — the reason is the first thing truncated); proxy `injected > 0` |

The ARM-REPLACE **negative** gate is new relative to the substrate, which has only
positive gates, and it is the mechanical proof that the ablation is real. It costs $0
and it runs at T0.

These are harness-side assertions on *rendered output strings*. Nothing here inspects a
prompt or a query, and nothing here is proposed as serving behaviour: no query-side
heuristic is introduced anywhere in this design.

### 5.5 The ARM-AUDIT ablation: how it is achieved without touching product code

The brief frames this as "suppressing the deterrent rendering". That framing is the
trap. What ARM-AUDIT must suppress is not the *rendering* — TOKI and Zep both render
their contradiction records perfectly well when you ask — but the *proactive delivery*.
Their design is "queryable on demand, never automatically re-injected". Rendering the
deterrent **when the agent explicitly calls recall is the passive-audit behaviour**, not
a violation of it.

So the three options in the brief evaluate as:

| option | verdict |
|---|---|
| **(a) harness-level wire control** — `vectr proxy --no-inject`, no hooks, MCP present | **ADOPTED.** Both flags are shipped, both are already used by the longitudinal harness's arms A and B, and the resulting cell is a *more* faithful TOKI/Zep than any rendering ablation would be. Zero product change. |
| (b) planting notes in states that naturally don't fire | Available but **not needed, and not sufficient on the headline channel.** §2 fact 2: the proxy's candidate selection never reads `triggers`, so `not_before` cannot suppress proxy injection. It *can* suppress the hook/trigger surface — which is what makes the optional matched cell in §5.6 possible. |
| (c) a product-side ablation flag | **NOT PROPOSED, and recommended against** — see §16.4. |

One residual question option (a) cannot answer, stated so it is not mistaken for
solved: ARM-AUDIT withholds proactive delivery of *both* notes, so it is not a
pure "the revocation specifically was not injected" cell. That purer cell is §5.6's
`ARM-AUDIT-MATCHED`, it is achievable with zero product change on the hook channel only
(plant `F_old` with `triggers=[{"event":"prompt-submit","semantic":true,"not_before":
<far future>}]` and `F_new` with its kind default; revoke `F_old`), and it is a late,
optional tier. For the headline, ARM-AUDIT-as-specified is the right cell because it is
what the prior art actually does: passive stores deliver nothing proactively.

### 5.6 Optional arms (tier-gated, never in the headline)

| arm | id | purpose | tier |
|---|---|---|---|
| **ARM-PLACEBO** | `placebo` | `S-REPLACE` + one length-matched, topically irrelevant proactively-injected note, padded to ARM-DETERRENT's measured `injected_chars`. Rules out injected-volume as the explanation. | T5, **conditional** on ARM-DETERRENT beating ARM-REPLACE at T1 |
| **ARM-AUDIT-MATCHED** | `audit-matched` | hook channel; `F_new` fires, revocation withheld by `not_before`. The pure per-note delivery ablation. | T6b, optional |
| **ARM-DETERRENT-H** | `deterrent-hook` | ARM-DETERRENT delivered by SessionStart hooks instead of the proxy. Channel robustness. | T6 |

### 5.7 Why there is no separate no-memory-ever arm

**ARM-DEL is the no-memory arm.** At the moment the agent starts, its store is empty,
`notes_count == 0`, zero injection occurs, and a `vectr_recall` call returns nothing.
Operationally it is indistinguishable from a workspace that never had a memory system,
and it carries the *additional* realism that a memory system was present and chose
deletion — which is the actual mem0/LangMem behaviour we want to compare against.

A separate no-vectr-at-all arm would differ only in the wire path (no proxy, no MCP),
which the substrate deliberately holds constant across arms; adding it would reintroduce
exactly the confound (proxy/MCP transparency vs memory) that the substrate's arm rule
exists to remove. It is therefore **not included**.

One honest difference is recorded rather than assumed away: ARM-DEL's agent *can* call
`vectr_recall` and get nothing, which a no-vectr agent cannot. `recall_called` is
recorded for every arm and printed, so an ARM-DEL leg that burned turns on an empty
store is visible in the data rather than hidden inside a rate.

---

## 6. Cells, replicates, and store-state construction

### 6.1 Two modes

**REPLICATE mode (primary).** A cell is one `claude -p` invocation against a **freshly
materialized** fixture workspace and a **freshly built** store. `R` replicates per
`(scenario, arm, reason_variant)`. No state crosses a cell boundary: no workspace
carry-forward, no store carry-forward, no `--resume`, no `--continue`.

The substrate's shared-leg-1 cache is **inapplicable and must be bypassed, not
repurposed**: arms diverge from the very first session here, because the store state
*is* the manipulation and there is no shared discovery phase. Its replacement is
cheaper and stronger — an authored fixture directory with a recorded sha256 manifest,
byte-identical across every arm and replicate, materialized in milliseconds for $0.

The consequence is that replicates are **independent**, not sequentially contaminated,
so a rate over `R` replicates is a real rate rather than a path-dependent trajectory.
This is the single largest methodological gain over the substrate, and it is available
only because the fact is planted rather than discovered.

**CHAIN mode (secondary, T7).** A 3-leg trajectory where the workspace carries forward
and the store persists, exactly as in the substrate. Answers two questions REPLICATE
mode cannot: (i) does the deterrent keep firing across sessions, or does cooldown /
dedup silence it — the substrate's fresh-proxy-per-leg rule predicts it should keep
firing, and that is a testable prediction, not an assumption; (ii) does a leg that
ships backflow *raise* pressure for the next leg, closing the recontamination loop.

`run_plan.py` takes `--mode replicate|chain`; a chain of length 1 against a fresh
fixture *is* a replicate, so one runner serves both.

### 6.2 Isolation

Inherited verbatim from the substrate §5.4, with the store lifetime shortened:

- One `VECTR_DB_DIR` per **cell** in REPLICATE mode (per trajectory in CHAIN mode).
- A fresh daemon process and a fresh proxy process per leg. Non-negotiable: the
  cooldown ledger is a bounded per-process ring with no time decay.
- Ports ≥ 8899, refuse-on-collision, follow-the-real-port.
- `--runs-dir` outside any indexed vectr workspace.
- Cells run **one at a time**.
- Cell id `<scenario>-<arm>-<reason_variant>-r<replicate>` appears in the DB dir path,
  the workspace path, the runs dir and every result record.
- Verify scripts are materialized **outside** the workspace, after the agent exits.

### 6.3 Store-state construction (`arm_store.py`)

All four states are built over REST against the cell's own daemon, before the agent
starts, with `agent="antimemory-harness"`. Sequences are fixed and asserted by test:

```
S-DEL        remember(F_old) -> id1 ; forget(id1)                    ; assert notes_count == 0
S-REPLACE    remember(F_old) -> id1 ; forget(id1) ; remember(F_new)  ; assert notes_count == 1
S-REVOKE     remember(F_old) -> id1 ; remember(F_new, contradicts=id1)
                                                                      ; assert notes_count == 2
                                                                      ; assert fold(id1).state == "revoked"
             then, when reason_variant != the contradicts= default:
             revoke(id1, reason=<variant text>, actor="agent")
```

`F_old` is always written first and always identically, so every arm's history begins
with the same belief having been held; the arms differ only in what was done about it.
`F_old` is `kind="gotcha"` (structurally eligible per §2 fact 3, and the correct kind
for a file-anchored caveat), `title` set explicitly (§2 fact 1), body naming the stale
artifact paths so the structural matcher can fire on them. `F_new` is `kind="gotcha"`
too — never `directive`, which the proxy channel drops wholesale.

The harness writes every note and every transition. Letting an agent propose the
revocation was rejected for v1 for the substrate's reason (§5.3 there): revocation
quality would vary per arm and per run and swamp a budget of a few dozen legs. **The
resulting numbers are an upper bound on the mechanism's value, conditional on a correct
revocation existing.** The write half is the natural follow-on and should reuse this
harness with `revocation_source="agent"`.

---

## 7. Metrics — deterministic parsing rules

### 7.1 Sources and the no-judge rule

Inherited verbatim from the substrate §6.1. Every number comes from final workspace
bytes (regex / sha256), the ordered `tool_use` blocks of the `stream-json` transcript,
the exit code of a scenario-owned verify script, or the transcript's own `result` /
`usage` fields. **No LLM judges a run.** Nothing reads the agent's prose. The action
stream is built exactly as in substrate §6.2, and the pattern primitives
(`BashAction`, `PathAction`, `ContentAction`, `ToolAction`, `FileMutated`) are
re-exported, not redefined.

### 7.2 The three-way outcome

Exclusive and exhaustive, evaluated on **final bytes** and pre-registered before any cell:

```
backflow_shipped = any(spec.backflow_content matched against final bytes)
correct          = spec.correct_check passes  AND NOT backflow_shipped
outcome          = "BACKFLOW" if backflow_shipped
                   else "CORRECT" if correct
                   else "NEITHER"
```

A binary would be a lie here, because the two treatments have **different predicted
directions** and a binary would hide it:

- ARM-REPLACE's note says what is true, so its predicted effect is `BACKFLOW → CORRECT`.
- ARM-DETERRENT's deterrent says what is *false* and forbids re-deriving it. It does not,
  by itself, say what is true. Its predicted effect is `BACKFLOW → NEITHER`.

**This is pre-registered, not a post-hoc rescue.** If the paper is to claim the deterrent
is valuable on the strength of a `BACKFLOW → NEITHER` shift, the argument that a stalled
task is better than a wrong artifact shipped must be made *before* the data exists.
It is made here: in the workspace-mediated backflow loop, a wrong artifact shipped
becomes the next session's stale source and compounds, whereas a stalled task does not.
That argument is the design's, and a reviewer is free to reject it — but it cannot be
invented after the fact.

### 7.3 Per-leg fields

| field | definition |
|---|---|
| `outcome` | §7.2, three-valued |
| `backflow_shipped` | primary. `spec.backflow_content` against final bytes |
| `backflow_attempted` | `first_index(actions, spec.backflow_signature) is not None` |
| `self_corrected` | `backflow_attempted and not backflow_shipped` |
| `stale_read` | any action matching `spec.stale_artifact_read` — the temptation covariate |
| `truth_read` | any action matching `spec.truth_source_read` |
| `verified_before_acting` | `truth_read` occurred at a lower action index than the first backflow or correct action. The deterrent's literal instruction is "do not re-derive without verification", so this is the compliance measure most tightly coupled to the injected text |
| `recall_called` | count of `mcp__vectr__vectr_recall` actions — ARM-AUDIT's adoption measure and ARM-DEL's wasted-turn measure |
| `recontamination_write` | any `mcp__vectr__vectr_remember` action whose `content` matches the scenario's `F_old` signature. Free instrumentation on the *second* arm of backflow; observed, never manipulated (§16.8) |
| `injected_chars` | total characters of proactive context delivered, from the audit log — the volume covariate for §5.3 |
| `deterrent_delivered` | post-offset `PROACTIVE_INJECT` line containing `"Previously believed ("`. Gate input, and the mechanical answer to "did the agent actually see it" |
| `session_usd`, `num_turns`, `duration_api_ms` | from the `result` event, unchanged from the substrate |

`context_tokens_at_fact` / `billable_tokens_to_fact` / the RDC allocation math from the
substrate §6.3 are **not** used: re-discovery cost is not this design's question. Only
`session_usd` is carried, for budget accounting.

### 7.4 Rates, censoring, and what is never imputed

```
backflow_rate(arm)  = |{valid legs with outcome == BACKFLOW}| / |valid legs|
correct_rate(arm)   = |{valid legs with outcome == CORRECT}|  / |valid legs|
neither_rate(arm)   = |{valid legs with outcome == NEITHER}|  / |valid legs|
verification_rate(arm) = |{valid legs with verified_before_acting}| / |valid legs|
```

- Invalid legs (§5.4) are **excluded and never imputed**, and the invalid count is
  printed adjacent to every rate — never a bare rate. Inherited from the substrate.
- A leg with `stale_read == false` was **never tempted**. It is **not excluded** — the
  deterrent causing an agent not to open the stale doc is a legitimate win, and dropping
  those legs would delete the mechanism's best effect. It is reported as a covariate,
  and the tempted-subgroup rate is printed adjacent to every headline rate so a reader
  can see whether the effect is a temptation effect or an outcome effect.
- No missing transcript ever raises: it degrades to `valid:false`.

---

## 8. The pre-registered decision rule — frozen before any paid cell

Written to `decision_rule.json` in this directory, sha256-hashed, and the hash is
stamped into every `result.json` the runner writes. A run whose stamped hash does not
match the file the report reads is reported as **unpre-registered** and its verdict is
suppressed. This is stricter than the substrate, which pre-registers in prose.

Let `B_X` = the count of `BACKFLOW` outcomes among arm `X`'s `R = 3` valid legs on the
headline scenario.

**G0 — scenario validity (must hold, or nothing else is interpreted).**
`B_DEL ≥ 2`. If `B_DEL ≤ 1` the scenario does not tempt backflow: the leg results are
reported as `weak_temptation:true`, **the scenario is replaced rather than the result
reinterpreted**, and no arm comparison is printed. (The substrate's `weak_prior:true`
discipline, transplanted.)

**G1 — proactivity (the novelty-bearing contrast).**
`B_DETERRENT ≤ B_AUDIT − 2`.

**G2 — marginal value of the record (the contrast that can kill it).**
`B_DETERRENT ≤ B_REPLACE − 2`.

**Verdict.**

| | condition |
|---|---|
| **SUPPORTED** | G0 ∧ G1 ∧ G2 |
| **REFUTED** | G0 ∧ (`B_DETERRENT ≥ B_AUDIT` ∨ `B_DETERRENT ≥ B_REPLACE`) |
| **INCONCLUSIVE** | otherwise |

**Escalation, frozen so it is not a garden of forking paths.** INCONCLUSIVE permits
**exactly one** escalation: a pre-priced replication block taking `R` to 6 on the same
four arms and the same scenario, re-evaluated with the margin scaled to `≥ 4 of 6`.
There is no second escalation and no threshold renegotiation. If the escalated block is
still INCONCLUSIVE, that is the reported result.

**Ceiling valve.** If `B_REPLACE == 0` and `B_DEL == 3`, the G2 contrast is reported as
`uninformative_ceiling`, **not** as a null: a corrective note alone already solved the
scenario, so the record's marginal value is unmeasurable there. The designated follow-up
is the higher-pressure scenario A4, and this is the specific case §3.1 and §3.2 exist
to have anticipated. (This valve is the direct analogue of the first paper's G4
utility-ceiling case, where both arms passed and the oracle ratio collapsed to 1.0.)

**Secondaries — always reported, never able to overturn the verdict.**
The full three-way outcome distribution per arm; `verification_rate`;
`recall_called`; `recontamination_write`; `injected_chars`; the tempted-subgroup rates.

### 8.1 Pre-registered directional expectations

Recorded before any cell so a surprise is visibly a surprise.

| arm | expected | why it is diagnostic |
|---|---|---|
| **ARM-DEL** | mostly `BACKFLOW` | if not, G0 fails and the scenario is replaced |
| **ARM-AUDIT** | mostly `BACKFLOW`, with `recall_called` **low (< 1/3)** | the predicted *mechanism* of passive failure is adoption: an agent that believes it already knows the fact has no reason to query memory. **If `recall_called` is high while backflow is also high, passive audit failed on trust, not adoption — a different finding, and it must be reported as such rather than folded into "passive audit loses".** |
| **ARM-REPLACE** | mass moves `BACKFLOW → CORRECT` | |
| **ARM-DETERRENT** | mass moves `BACKFLOW → NEITHER`, plus some `CORRECT` (the shipped `contradicts=` store also carries `F_new`) | |

**The mechanism-specific prediction (tested at T2, not T1).** The deterrent's advantage
over ARM-REPLACE is predicted to be **largest where artifact pressure is highest**,
because the deterrent's distinctive content is precisely a counter to "the workspace
out-votes memory". Ordered prediction across scenarios:
`(B_REPLACE − B_DETERRENT)` on A4 (high) `>` A1/A3 (medium) `>` A2 (low).
If the ordering is flat or reversed, the mechanism is doing something other than what it
claims, and that is the interesting result.

### 8.2 Designing so the deterrent can lose

Stated explicitly because it is the lane's hard rail:

- ARM-REPLACE carries strictly more corrective information than the deterrent (§5.3) and
  is proactively delivered on the same channel. It is the favourite.
- The deterrent's corrective payload is capped by an ~80-character title plus a `reason`
  that is the first thing truncated on the headline channel (§2 facts 1 and 5).
- G2 is a **conjunctive** requirement: a deterrent that beats passive audit but not a
  corrective note is REFUTED, not partially supported.
- The scenarios are authored so the stale artifact is what a competent agent would
  naturally read first, not so the correct answer is signposted.
- A REFUTED verdict is a publishable result and the write-up plan treats it as one:
  "proactive re-injection of a retraction record does not reduce backflow beyond
  asserting the correction" is a directly useful negative for a field with ~10
  concurrent memory-governance system papers and no measurements.

---

## 9. The reinstatement probe

The strongest single argument for the state machine's third component is that the
autonomous-agent-memory survey (2603.07670) **names the failure it solves and nobody
addresses**: *"If the agent incorrectly concludes 'API X always returns errors with
parameter Y', it will avoid that call path forever, never collecting evidence to
overturn the false belief."* A deterrent that cannot be lifted is that failure with
extra steps.

Design: three single-leg cells on A1, differing **only** in the store state, sharing a
prompt that *requires* acting on `F_old` — which by then is true again, because the
migration was rolled back.

| cell | store state | prediction |
|---|---|---|
| `never-revoked` | `F_old` active, never revoked. Positive control. | high compliance with `F_old` |
| `reinstated` | `F_old` revoked, then `POST /v1/reinstate` with reason `"rolled back on 2026-06-02; config/flags.yaml is authoritative again"` | high compliance — **the reinstatement path works** |
| `still-revoked` | `F_old` revoked, not reinstated. Negative control. | low compliance — **the deterrent has any effect at all** |

Two things fall out of one 3-cell design, which is why it is worth its money:
`still-revoked` vs `never-revoked` is an independent positive control that the deterrent
changes behaviour at all (a cheap sanity check on the whole eval), and `reinstated` vs
`still-revoked` is the reinstatement result. Pre-registered failure mode:
**if `reinstated` behaves like `still-revoked`, the deterrent persists past its own
reversal — the self-reinforcing-error risk is not solved, which is both a publishable
negative and a product bug to file.**

The A1 workspace needs one variant fixture for this probe (`config/flags.yaml`
re-populated, `config/flags.d/` removed) so that `F_old` really is true again; it is
declared as `A1-rollback` and shares A1's scenario file.

No chaining is required. Three cells, `R` replicates each.

---

## 10. The revocation-reason ladder

§2 fact 1 makes this unavoidable rather than optional: the `reason` is the deterrent's
only unbounded payload, so *what goes in it* is a design variable of the mechanism, not
decoration. The ladder mirrors the substrate's `note_variant` ladder exactly, and it is
the direct bridge to the first paper's C4 (verifiable-notes) result.

| variant | `reason` = | tests |
|---|---|---|
| `bare` | a minimal negation ("no longer true") | the floor: does a revocation with no content do anything? |
| `causal` | what changed, and when | does knowing *why* it was falsified help? |
| `corrective` | what changed, when, what is true now, **and a one-command verify hint** | the C4 corroboration affordance, transplanted to a retraction |

All three contain the same falsified claim (in the note's `title`, byte-identical,
asserted by test) and differ only in the `reason`. `corrective` is the T1 default,
because it is what a caller using `vectr_remember(contradicts=)` would naturally write
and because it is the shipped-behaviour rung.

`trail_chars` (substrate §7.3) is recorded per variant: a reason that buys nothing is
pure token overhead delivered on every fire, and on the proxy channel it is *also* the
thing crowding out the quoted claim under `_cap()`.

---

## 11. Artifacts, schemas, resumability

```
<runs-dir>/
  _fixtures/<scenario>/            authored workspace + manifest.sha256   (checked in, $0)
  decision_rule.json               frozen §8 rule; sha256 stamped into every result
  <scenario>-<arm>-<variant>-r<n>/
    state.json                     cell state
    db/                            VECTR_DB_DIR
    legs/<k>/
      workspace/                   materialized from _fixtures at leg start
      artifacts/  result.json transcript.jsonl audit.log preflight.json
                  store-state.json scenario.json baselines.json proxy-health.json
                  daemon-status.json daemon-status-final.json agent.stderr
      verify/                      materialized AFTER the agent exits
      end-state.tar + manifest.sha256
  results.jsonl                    one line per leg, appended
```

`schema/leg_result.schema.json` extends the substrate's schema with the §7.3 fields and
the `decision_rule_sha256` stamp; `store-state.json` records the exact REST call
sequence and the resulting `notes_count` / fold states, so an arm's premise is auditable
after the fact and not only at gate time.

**Resumability — cell granularity**, inherited: a leg with an existing
`artifacts/result.json` carrying a `valid` field is never re-run. In REPLICATE mode
cells are fully independent, so resumption is trivially safe and a tier can be paused
between any two cells.

**Budget guard**, inherited: `--budget-usd X` refuses to *start* a cell once the
remaining budget is below the tier's per-cell worst case; `--dry-run` prints the exact
cell list and cost estimate with zero spend; `--probe-only` runs materialize → build
store → probe → tear down and spawns no agent — **zero quota**, and the only way a new
scenario reaches a paid tier.

---

## 12. What the extension needs that the substrate lacks

Everything not listed here is reused unchanged.

**Needs building:**

1. `arm_store.py` — the §6.3 store-state builder. The substrate plants exactly one note
   via `/v1/remember`; this needs two notes plus `contradicts` / `revoke` / `reinstate` /
   `forget` sequences, with post-condition assertions on `notes_count` and the fold.
2. `--mode replicate` — fresh fixture + fresh store per cell, `--replicates R`. The
   substrate's `_shared/leg1` cache is **inapplicable** (§6.1) and must be bypassed.
3. Authored fixture directories with recorded sha256 manifests, replacing the shared
   leg-1 tar as the leg-start baseline.
4. Scorer additions: the three-way `outcome`, `backflow_shipped` vs `backflow_attempted`,
   `stale_read`, `truth_read`, `verified_before_acting`, `recall_called`,
   `recontamination_write`, `injected_chars`, `deterrent_delivered`.
5. **Negative** non-vacuity gates. The substrate has only positive ones; ARM-REPLACE's
   "the preflight context contains no occurrence of `Previously believed (`" is the
   ablation's proof and has no precedent to copy.
6. A surface-aware gate string table — `"Previously believed ("` is valid on both
   surfaces, but the *reason-survived* assertion is proxy-specific (§2 fact 5).
7. `decision_rule.json` + sha256 stamping + the report's unpre-registered suppression.
8. `reason_variant` as a first-class field on the scenario and on the cell id.
9. Report layer: a 4-arm × 3-outcome distribution table, the G0/G1/G2 verdict line, and
   invalid / untempted counts printed adjacent to every rate.
10. `A1-rollback` fixture variant for §9.

**Explicitly NOT needed:** the shared-leg-1 cache; the DISCOVERED/TOLD forcing-clause
machinery (there is no discovery leg); the RDC token-allocation math (§7.3); the hook
attestation (unless T6 runs, in which case the substrate's `--hook-attestation`
enforcement is reused verbatim).

**Zero product code changes.** Every lever this design uses — `contradicts`, `revoke`,
`reinstate`, `forget`, `triggers`, `--no-inject`, `/v1/proactive`, `/v1/recall` — is
shipped and documented.

---

## 13. Tiered run plan

Per-cell cost basis: **$0.24 mean / $0.35 worst**, the substrate's figure for legs ≥ 2
(a single scoped task, no discovery phase), from its observed $0.12-0.35 range on a
sonnet executor. Each tier is approved separately by the sentinel.

| tier | what it buys | cells | mean | worst |
|---|---|---:|---:|---:|
| **T0** preflight | probe-only across 4 scenarios × 4 arm states + scorer fixture tests; no agent | 0 | **$0** | **$0** |
| **T1 HEADLINE** | A1, all four arms, `corrective` reason, R=3 | 12 | **$2.88** | **$4.20** |
| **T2** pressure prediction | A4 (high) + A2 (low), arms {DEL, REPLACE, DETERRENT}, R=2 | 12 | $2.88 | $4.20 |
| **T3** reinstatement | §9's three cells on A1-rollback, R=3 | 9 | $2.16 | $3.15 |
| **T4** reason ladder | `bare` + `causal` on ARM-DETERRENT, A1, R=3 (`corrective` already bought at T1) | 6 | $1.44 | $2.10 |
| **T5** placebo *(conditional)* | ARM-PLACEBO, A1, R=3. **Only runs if T1 shows ARM-DETERRENT < ARM-REPLACE** | 3 | $0.72 | $1.05 |
| **T6** channel | ARM-DETERRENT-H + ARM-REPLACE on hooks, A1, R=3 | 6 | $1.44 | $2.10 |
| **T7** chain / recontamination | CHAIN mode, A1, {DEL, DETERRENT} × 3 legs × 2 trajectories | 12 | $2.88 | $4.20 |
| **T8** replication | T1's four arms, second replicate block (also the §8 escalation block) | 12 | $2.88 | $4.20 |

Cumulative: **T0 + T1 = $2.88 mean / $4.20 worst.** Through T3 = $7.92 / $11.55.
Everything = $17.28 / $25.20.

**T0 is doing more work than its price suggests, and is a hard gate.** It proves the
entire ARM-AUDIT ablation mechanically, for free, before any quota is spent:

- **T0-1** all four store states build and hold their post-conditions (`notes_count`,
  fold state) on all four scenarios.
- **T0-2** ARM-DETERRENT's `/v1/proactive` preflight context contains
  `"Previously believed ("`.
- **T0-3** ARM-REPLACE's preflight context contains `F_new` and contains **no**
  occurrence of `"Previously believed ("`.
- **T0-4** ARM-DETERRENT's preflight context contains the `corrective` reason
  **verbatim**. If it does not, `_cap()` truncated it (§2 fact 5) — the measured
  surviving character count becomes the scenario-authoring reason-length rule, the
  reasons are shortened, and T0-4 is re-run. **A tier that cannot pass T0-4 must switch
  its headline channel to hooks rather than proceed with a truncated deterrent**, because
  a truncated reason means the arm is not delivering the thing under test.
- **T0-5** ARM-AUDIT's `/v1/recall` preflight returns both notes with the deterrent
  rendering, and its `/v1/proactive` preflight returns empty.
- **T0-6** ARM-DEL: `notes_count == 0`, `/v1/proactive` empty.
- **T0-7** scenario lint: no `explicit_negation_tokens` anywhere in any workspace
  (§3, criterion 7); no `new_fact_tokens` outside the declared truth source; every
  reason variant carries the same `title` byte-identically; verify scripts never land
  inside a workspace.
- **T0-8** scorer discrimination fixtures: a hand-built backflow transcript and a
  hand-built correct transcript score differently on `outcome` **and** on
  `verified_before_acting`; a missing transcript degrades to `valid:false` and never
  raises; `score_run()` is arm-blind, asserted by test signature as in the substrate.

**Ordering rationale.** T1 buys the entire claim at the smallest price: both decisive
contrasts (§5.3) plus the floor gate, on one scenario, at the reason rung a real caller
would write. If T1 returns REFUTED, T2-T8 are not bought and **the null is the result**.
T2 comes next only because §8.1's ordered pressure prediction is the design's one
mechanism-specific, falsifiable claim, and it needs a second and third pressure level.
T3 is placed above the ladder and the channel work because it is the component with a
named-open-problem citation behind it and the cheapest independent positive control in
the design. T5 exists to be *skipped*: it only spends money when there is an effect that
needs ruling out.

**Quota discipline.** Cells run one at a time, each a separate process invocation. In
REPLICATE mode nothing crosses a cell boundary, so a tier can be interrupted anywhere
and resumed with zero loss. Nothing in this design requires an unbroken window, and
live cells here serialize behind the first paper's T0 → T1 per the parallel-tracks
decision.

---

## 14. Threats to validity

| threat | handling |
|---|---|
| **Injected-volume confound**: ARM-DETERRENT delivers more characters than ARM-REPLACE | `injected_chars` recorded and printed with every rate; T5's length-matched placebo is pre-priced and conditional on there being an effect to explain (§5.3) |
| **Deterrent payload is title-capped**; the reason truncates first on the proxy surface | T0-4 asserts the reason survives verbatim and sets the authoring length rule from measurement; failure switches the headline channel to hooks rather than proceeding (§13) |
| **Oracle revocation** ⇒ upper bound | stated as a scope limit (§6.3); the write half is the follow-on with `revocation_source="agent"` |
| **R = 3 is three bits per arm** | acknowledged in the rule itself: G1/G2 require a margin of 2 of 3, so only large effects are detectable, deliberately. One pre-priced escalation to R=6 with a scaled margin, and no more (§8) |
| **Scenario authored to flatter the mechanism** | G0 makes ARM-DEL's failure rate a *gate*, not a result; the ceiling valve prevents an easy scenario reading as a G2 null; §3's criterion 7 is lint-enforced; §8.2 states the ways the deterrent is set up to lose |
| **The stale artifact is not actually tempting** | `stale_read` recorded per leg; tempted-subgroup rates printed adjacent to headline rates; G0 fails loudly if the workspace does not tempt |
| **ARM-AUDIT loses for the wrong reason** | `recall_called` distinguishes adoption failure from trust failure and the two are reported as different findings (§8.1) |
| **Model nondeterminism dwarfs the effect** | the targeted effect is large (a falsified fact shipped vs not); `session_usd` and turns are never a pass/fail signal |
| **Cross-session cooldown silences the deterrent** | a fresh proxy per leg is mandatory (§6.2), and T7 tests the prediction that the deterrent keeps firing rather than assuming it |
| **Fold failure looks like "nothing matched"** | §2 fact 4: gates read `PROACTIVE_INJECT` audit lines and preflight context, never `item_count` alone |
| **Post-hoc reinterpretation** | `decision_rule.json` is hashed into every result and the report suppresses verdicts whose stamp does not match (§8) |
| **A4 cannot fairly test compliance** | flagged in-scenario; A4's only licensed contrast is DETERRENT vs REPLACE under maximum artifact pressure, and the report must say so |

---

## 15. Implementation checklist (coder lane)

Build in this order. Nothing below requires a design decision except the §16 items;
anything that seems to is a defect in this document and comes back to the design lane.

1. `scenarios.py` — the four scenarios of §4 plus the `A1-rollback` variant, re-exporting
   the substrate's and the trap harness's check primitives rather than redefining them.
2. `arm_store.py` — §6.3, with post-condition assertions and `store-state.json`.
3. `scorer.py` — action stream (substrate §6.2), §7.2's three-way outcome, §7.3's fields,
   `leg_non_vacuity(arm=…)` per §5.4 including the negative gate. `score_run()` stays
   **arm-blind**, asserted by test.
4. `run_cell.py` — one cell: materialize fixture → start daemon → build store state →
   probe + audit offset → arm wiring (proxy flag / mcp.json / hooks) → `claude -p` →
   capture + teardown → score → write. Reuses `run_harness.py`'s port discipline,
   `_settled_audit_size`, and teardown-never-masks-a-run structure.
5. `run_plan.py` — `--mode replicate|chain`, `--replicates`, `--budget-usd`, `--dry-run`,
   `--probe-only`, tier presets, `decision_rule.json` freeze + stamp.
6. `report.py` — the 4×3 outcome table, G0/G1/G2 verdict line, invalid and untempted
   counts adjacent to every rate, never a bare rate.
7. `tests/test_anti_memory_scorer.py` — the T0-7 and T0-8 suites.
8. Only then: T0 `--probe-only` across all four scenarios, and report to the sentinel for
   T1 approval.

---

## 16. Open decisions — user answers these

1. **Headline scenario.** T1 is specified on **A1** (`retired_flag_api`, RECOVERABLE,
   medium pressure) because it has the cleanest byte-level discrimination and an
   informative no-memory floor. **A4** (UNRECOVERABLE, high pressure) is where §8.1's
   mechanism-specific prediction is strongest but where the floor is uninformative by
   construction. Same money either way. Which headlines?

   **RESOLVED (user, 2026-07-30): A4 (`handwritten_notes`) headlines the reported
   narrative.** Scenario order across tiers is A4 → A1 → A2 → A3. This overrides this
   section's own literal text above, which names A1 the headline. The frozen decision
   rule in §8 is unaffected: it still runs its G0/G1/G2 arithmetic on A1's (
   `retired_flag_api`'s) `B_X` counts, per §13's own literal T1 tier spec — the
   resolution changes which scenario's narrative leads the report, not which scenario
   the pre-registered rule is computed on. Implemented in `scenarios.py`'s
   `SCENARIO_ORDER`, `HEADLINE_SCENARIO`, and `DECISION_RULE_SCENARIO`.

2. **R at headline.** R=3 gives three binary observations per arm and the whole verdict
   rests on them; R=4 costs $0.96 more and makes a 3-of-4 margin available. The design
   is written for R=3 with one pre-priced escalation to R=6. Accept, or start at R=4?

   **RESOLVED (user, 2026-07-30): R=3 confirmed, with the pre-priced escalation to R=6
   as already specified in §8.** No change to the design.

3. **Content parity between ARM-DETERRENT and ARM-REPLACE.** ARM-DETERRENT carries the
   `F_new` note *and* the deterrent, because that is what shipped `contradicts=`
   produces. The alternative is to drop `F_new` from ARM-DETERRENT so the arms are
   content-matched and the deterrent is tested alone — cleaner identification, but it
   tests a configuration the product never ships. **Recommendation: keep `F_new` in both
   (shipped behaviour) and buy T5's placebo only if there is an effect to explain.**
   Confirm?

   **RESOLVED (user, 2026-07-30): confirmed as recommended.** ARM-DETERRENT keeps
   `F_new` plus the deterrent (shipped `contradicts=` behaviour); the length-matched
   placebo (§5.6, T5) remains conditional on T1 showing ARM-DETERRENT losing to
   ARM-REPLACE. No change to the design.

4. **Product ablation flag: not proposed.** ARM-AUDIT needs no product change (§5.5).
   The *only* cell that would require one is a rendering-isolation arm — "a revoked note
   proactively injected as raw content" — which would isolate the deterrent's *prose*
   from its *delivery*. **Recommendation: do not build it.** It tests a configuration
   the product never ships (the revoked→deterrent coupling in `matcher._note_summary`
   and `_format_full_block` is unconditional by design, and note #433's trust-boundary
   review treats that coupling as a safety property), so a result about it would not be
   a result about vectr. Confirm, or should it be filed as an OPEN product task?

   **RESOLVED (user, 2026-07-30): the rendering-isolation flag is NOT built.** The
   sentinel files it separately as UPG-DETERRENT-TITLE-ONLY for product-side
   consideration; this eval's coder lane makes no product code change for it and none
   is made in this implementation.

5. **Null publication.** §8.2 commits to writing up a REFUTED verdict as a result. Does
   the user want that, and under what framing — a standalone negative, or folded into
   the C4 paper's apparatus section? This determines whether T2-T8 have any value after
   a REFUTED T1.

   **RESOLVED (user, 2026-07-30): no pre-commitment now; learning-first.** The framing
   question is deferred to whenever a T1 verdict actually exists rather than decided in
   the abstract. No change to the design or to T0's scope.

6. **Budget.** T0+T1 is $2.88 mean / $4.20 worst. Does paper two share paper one's $6
   standing ceiling (leaving roughly $2.40 of headroom after the first paper's T1), get
   its own ceiling, or run tier-by-tier with no standing authorisation?

   **RESOLVED (user, 2026-07-30): paid tiers run under the sentinel's own soft ceiling
   (~$10), approved tier by tier per §13.** Irrelevant to T0, which is $0.

7. **A shipped-behaviour question surfaced by the code read.** The deterrent quotes only
   the note's `title` (≤80 chars) and the `reason` is its only unbounded field, which on
   the proxy surface is also the first thing `_cap()` truncates (§2 facts 1 and 5). Is
   that correct as shipped — the design works within it either way — or should it be
   filed as a UPG item (deterrent should carry more of the revoked note's content, and
   the reason should be truncation-protected the way the do-not-re-derive clause already
   is)? This is a product question this eval surfaced, not an eval blocker.

   **RESOLVED (user, 2026-07-30): filed as UPG-DETERRENT-TITLE-ONLY, owned by the
   sentinel, tracked separately from this eval.** This coder lane makes no product code
   change for it.

8. **Recontamination as observation or manipulation.** `recontamination_write` is
   recorded for free from the tool-use stream, closing the second arm of backflow (the
   agent writes the falsified fact back into memory). Promoting it to a manipulation
   would mean letting agents write notes, which reintroduces the write-half variance
   §6.3 excludes. **Recommendation: keep it an observation in v1.** Confirm?

   **RESOLVED (user, 2026-07-30): confirmed as recommended.** `recontamination_write`
   stays an observation-only field in v1; no manipulation is added. No change to the
   design.

9. **Sequencing.** This design is complete and unblocked, but its live cells serialize
   behind the first paper's T0 → T1 on budget and quota. Does the sentinel want T0
   (which is $0 and proves the ablation) run immediately on merge, or held until the
   first paper's T1 lands?

   **RESOLVED (user, 2026-07-30): build and run T0 now (it is $0 and blocks nothing);
   paid cells (T1 and beyond) serialize behind the longitudinal harness's own tiers, as
   already stated in §13's quota-discipline paragraph.** This is confirmatory of what
   §13 already says, not a change to it.
