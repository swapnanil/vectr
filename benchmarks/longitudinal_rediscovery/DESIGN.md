# EVAL-LONGITUDINAL-REDISCOVERY — design

Status: DESIGN (no cells run). Implementable by a coder lane without further design
decisions. Every paid run is gated on sentinel approval, tier by tier (§9).

---

## 1. What this measures, and why it is not the trap eval

`benchmarks/injection_utility/` measures whether a planted note changes behaviour
**inside one session**, on scenarios where the repository actively misleads and the
note holds the correction. That harness answered its question: after the title-only
and single-turn fixes landed, utility went from 0/N to 2/3, with the residual null
isolated to *model-side distrust of a claim the executor cannot corroborate*.

Trap scenarios understate the channel. They are adversarial one-shots: the note wins
or loses in a single sitting, and the cost of *not* having the note is bounded by that
sitting. The value working memory actually claims is different and is spread over time:

> On really long, repetitive work — over days, weeks, months — an agent repeats the
> same mistake because its training prior is wrong about *this* workspace, and pays
> the discovery cost again every session.

Two facts from this project's own history are the template:

- the authoritative spec and task list live **outside** the code repository — every
  successive model assumed `spec.md` was in the repo and went looking there;
- PyPI releases go through **GitHub CI only** (tag push), never a local `twine
  upload` — the near-universal prior is the opposite.

Both share a shape: (a) not derivable from the repo alone, or actively contradicted by
the training prior; (b) learned once, at real cost; (c) needed again in every session
of a class. Nothing about them is a trap. They are ordinary needed information, and
the loss is re-discovery, not sabotage.

This eval measures that loss and how much of it a memory channel removes.

**Two headline quantities** (defined mechanically in §6):

| | question |
|---|---|
| **RE-DISCOVERY COST** | turns / tool calls / tokens / $ spent in session *k* re-establishing a fact session 1 already established |
| **MISTAKE-REPETITION RATE** | fraction of sessions *k>1* that commit the same prior-driven error session 1 committed |

**What this eval deliberately does not measure** — stated up front so nobody reads a
number as broader than it is:

- the *write* half. Notes are planted by the harness, identical across arms (§5.3).
  Whether an agent writes a good note unprompted is a separate experiment.
- compaction survival. Synthetic workspaces are small; no session gets near a
  compaction boundary. This measures **session-boundary** survival only, which means
  it *understates* the real-world value of the channel.
- retrieval ranking. One note per store. Precision on a mature store is
  `benchmarks/proactive_injection_precision/`'s question.

---

## 2. Scenario class: LONGITUDINAL NEEDED-INFO FACT

A scenario is admitted only if it satisfies all five criteria:

1. **Workspace-specific fact.** One sentence, true of this workspace, false or
   unknowable in general.
2. **Prior-contradicting or non-derivable.** Either the common training prior
   prescribes the opposite action, or the fact cannot be read off the repo at all.
3. **Repeatedly needed.** The fact is load-bearing in every one of N ≥ 3 sessions,
   and each session's task is independently unsatisfiable without it.
4. **Mechanically observable ground truth.** Following the fact and ignoring it differ
   in bytes on disk or in commands run — never merely in prose. Inherited verbatim
   from the trap harness, along with the no-LLM-judge rule (§6.1).
5. **Synthetic.** The workspace is authored for this eval. No third-party corpus
   content, ever. django / cpython / uv are retrieval witnesses elsewhere and are
   never the domain here.

### 2.1 Two origin classes

How the fact first enters session 1 splits the class in two, and the split determines
which headline metric is primary.

**ORIGIN = DISCOVERED** (corroborable). The fact is recoverable from the workspace at
real cost — several reads, a failing command, a lint message. Session 1 pays that cost.
Because the fact is corroborable, an agent in a later session *can* re-derive it, so
the loss is measurable in turns and dollars.

> Primary metric: **RE-DISCOVERY COST**. Secondary: mistake repetition.

DISCOVERED scenarios carry a **leg-1 forcing step**: a clause appended to session 1's
prompt only (`run make check before you finish`, `make sure the suite passes`) whose
purpose is to make the fact surface rather than stay latent. Without it, session 1 can
end having quietly committed the error and established nothing, and the shared leg-1
(§5.2) that every arm depends on would be worthless. A test asserts the forcing clause
appears in prompt 1 and in no later prompt.

**ORIGIN = TOLD** (uncorroborable). Nothing in the workspace records the fact. It
enters exactly once, from the user, in session 1's prompt — which is precisely how the
two real examples above entered this project. Later prompts never restate it. An agent
in session *k>1* cannot re-derive it; it either carried the fact or it errs.

> Primary metric: **MISTAKE-REPETITION RATE**. Re-discovery cost is censored by
> construction (§6.4).

The TOLD class is the direct successor to the `flaky_test` null: it is exactly the
region where the executor cannot corroborate the claim in-repo. It is therefore where
the verifiable-notes experiment (§7) is decisive.

### 2.2 Residue rule

Within a trajectory the workspace carries forward, so session *k*'s starting state
contains session *k-1*'s output. Scenario authors must ensure that neither the correct
artifact nor the mistaken artifact encodes the fact anywhere a later session naturally
reads — otherwise the *workspace* becomes the memory and the arms converge for the
wrong reason. Per-*k* results are always reported separately so a leak shows up as a
monotone decay in the no-memory arm.

---

## 3. The six scenarios

| # | slug | origin | corroborable | shape | fact in one line |
|---|---|---|---|---|---|
| S1 | `release_via_ci` | DISCOVERED | yes | **PyPI-via-CI-only** | releases are a tag push; CI publishes; no tokens exist for local upload |
| S2 | `spec_lives_outside` | DISCOVERED | yes | **file-lives-elsewhere** | the real spec/tasks are outside the repo; the in-repo copy is a generated mirror |
| S3 | `runner_not_pytest` | DISCOVERED | yes | wrong-tool prior | the suite runs via `./tools/t`; bare `pytest` reports 9 fake collection errors |
| S4 | `secrets_not_dotenv` | DISCOVERED | yes | wrong-mechanism prior | `.env` is a decoy; secrets come from `scripts/envctl` |
| S5 | `deploy_reverted_by_reconciler` | TOLD | **no** | invisible-consequence | `./deploy.sh` looks like it works and is silently reverted; queue via `deploy/queue.yaml` |
| S6 | `bench_box_only` | TOLD | **no** | invisible-consequence | RESULTS.md numbers must come from `make bench REMOTE=1`; laptop numbers are rejected |

Every scenario declares four session prompts. Tiers run legs 1-3; the slope tier (§9)
adds leg 4 to test whether the saving persists or decays.

Common declared fields per scenario (dataclasses in `scenario_stubs.py`):
`slug, origin, corroborable, agent_cwd, files, executable, git_init, fact_sentence,
fact_tokens, note_variants, probe_files, legs[]`. Per leg:
`prompt, checks, primary_check, fact_acquisition, mistake_signature, rediscovery_work,
metrics, verify_scripts, arm_a_expectation, memory_arm_expectation`.

---

### S1 `release_via_ci` — DISCOVERED, corroborable (required shape: PyPI-via-CI-only)

**Workspace** `ledgerkit/` (git repo, `git_init=True`, one initial commit):

```
pyproject.toml                 name=ledgerkit, version="1.3.2"
src/ledgerkit/__init__.py      __version__ = "1.3.2"
src/ledgerkit/rates.py         convert(); FIXME on a bad default
CHANGELOG.md                   ## 1.3.2 ... ## Unreleased (entries already written)
CLAUDE.md                      neutral project notes, no memory content
.github/workflows/ci.yml       tests on pull_request
.github/workflows/release.yml  on: push: tags ['v*']; permissions: id-token: write;
                               build + `pypa/gh-action-pypi-publish`; comment: this
                               project publishes only via Trusted Publishing from this
                               workflow — no API tokens are issued
scripts/preflight.sh           version-consistency check, exits 0 (executable)
```

**Fact sentence** (byte-identical in all note variants):

> Releases of ledgerkit are cut by pushing an annotated tag `vX.Y.Z`;
> `.github/workflows/release.yml` builds and publishes to PyPI via Trusted Publishing.
> This project has no PyPI API tokens, so a local `python -m build` + `twine upload`
> cannot authenticate and must never be attempted.

`fact_tokens = ("Trusted Publishing", "no PyPI API tokens", "annotated tag")` — asserted
absent from every scenario file except `release.yml`, and absent from prompts 2-4.

**Sessions**

1. `Release version 1.4.0 — the changelog's Unreleased section already lists the changes. Run scripts/preflight.sh before you finish.` *(forcing step)*
2. `1.4.0 shipped the bad default flagged by the FIXME in src/ledgerkit/rates.py. Fix it and release 1.4.1.`
3. `Add a convert_bulk() helper to src/ledgerkit/rates.py with a changelog entry, then release 1.5.0.`
4. *(slope)* `Fix the convert() docstring and release 1.5.1.`

**Ground truth**

- `mistake_signature`: `BashAction(r"\btwine\s+upload\b|\buv\s+publish\b|\bpoetry\s+publish\b|\bhatch\s+publish\b|\bflit\s+publish\b")`, plus
  `PathAction(("Write","Edit"), r"\.pypirc$")`, plus
  `ContentAction(("Write","Edit"), r"\.github/workflows/.*\.ya?ml$", r"twine|password|__token__")`.
  A bare `python -m build` is **not** the mistake (a local build is legitimate); only
  the upload is.
- `fact_acquisition`: `BashAction(r"git\s+tag\b[^\n]*\bv?1\.4\.0\b")` (per-leg version),
  `BashAction(r"git\s+push\b[^\n]*(--tags|\bv1\.4\.0\b)")`.
- `rediscovery_work`: `PathAction(("Read","Grep"), r"\.github/workflows/release\.ya?ml$")`,
  `BashAction(r"grep[^\n]*(twine|pypi|publish)")`, `BashAction(r"\bpip\s+show\s+twine\b|which\s+twine")`.
- `primary_check` = `AllOf("released_via_tag", [
  FileMatches("version_bumped_pyproject", "pyproject.toml", r'version\s*=\s*"1\.4\.0"'),
  FileMatches("version_bumped_init", "src/ledgerkit/__init__.py", r'__version__\s*=\s*"1\.4\.0"'),
  FileMatches("changelog_entry", "CHANGELOG.md", r"##\s*1\.4\.0"),
  CommandRan("no_local_upload", r"twine\s+upload|uv\s+publish|poetry\s+publish", want=False)])`
  plus a sibling `VerifyCommand("annotated_tag_exists", ("git","tag","-l","v1.4.0"))`
  whose stdout must be non-empty — implemented as a scenario-owned verify script
  exiting 0/1, so it stays inside the existing `VerifyCommand` primitive.
- Verifiable-note anchor: `.github/workflows/release.yml`; verify hint
  `grep -n "id-token" .github/workflows/release.yml`.

**Pre-registered expectations.** Arm A: leg 1 attempts a local upload before finding
the workflow (`k1_mistake=true`); legs 2-3 repeat it at ≥50%, since nothing carries the
correction forward. Memory arms: legs 2-3 tag directly, `turns_to_fact` ≤ 2, mistake
rate 0.

---

### S2 `spec_lives_outside` — DISCOVERED, corroborable (required shape: file-lives-elsewhere)

**Workspace root contains two trees**; the agent's cwd is the repo, the docs are its
sibling. This is the only structural novelty in the scenario set and it is what
`agent_cwd` exists for:

```
workspace/
  orbit/                      <- agent_cwd; the git repo
    README.md
    src/orbit/scheduler.py
    src/orbit/limits.py       retry policy constants live here
    docs/spec.md              GENERATED MIRROR (stale)
    tools/docs_lint.py        compares sha256(docs/spec.md) with $DOCS_ROOT/spec.md;
                              exits 1: "docs/spec.md is a generated mirror of
                              $DOCS_ROOT/spec.md — edit the source, then `make docs-sync`"
    Makefile                  check: -> docs_lint; docs-sync: -> copy from DOCS_ROOT
    .github/workflows/ci.yml  runs `make check`
    CLAUDE.md                 neutral
  orbit-docs/                 <- OUTSIDE the repo
    spec.md                   authoritative
    tasks.md                  authoritative
```

`DOCS_ROOT` defaults to `../orbit-docs` inside `docs_lint.py`. The mirror carries no
banner: the truth costs a lint run plus a read of the tool.

**Fact sentence**

> The authoritative spec and task list for orbit live outside the repository, at
> `../orbit-docs/spec.md` and `../orbit-docs/tasks.md`. `orbit/docs/spec.md` is a
> generated mirror — editing it is overwritten by `make docs-sync` and fails
> `tools/docs_lint.py` in CI.

**Sessions**

1. `Document the new retry policy (see src/orbit/limits.py) in the spec. Run make check before you finish.` *(forcing step)*
2. `The spec's rate-limit section says 100 req/min but the code says 60. Fix the spec.`
3. `Add a tasks entry for the retry-policy work that references the spec section you wrote.`
4. *(slope)* `Update the spec's scheduler overview to mention the new backoff.`

**Ground truth**

- `mistake_signature`: `FileMutated("orbit/docs/spec.md")` (sha256 differs from the
  **leg-start** baseline) OR `PathAction(("Edit","Write"), r"orbit/docs/spec\.md$")`.
- `fact_acquisition`: first action of any kind whose path or command matches
  `r"orbit-docs/"`.
- `rediscovery_work`: `BashAction(r"find[^\n]*spec\.md|ls\s+\.\.|grep[^\n]*DOCS_ROOT")`,
  `PathAction(("Read","Grep"), r"tools/docs_lint\.py$")`, `BashAction(r"make\s+check|docs.?lint")`.
- `primary_check` = `AllOf("edited_the_source", [
  FileMatches("retry_policy_in_source", "orbit-docs/spec.md", r"(?i)retry"),
  FileUnchanged("mirror_untouched", "orbit/docs/spec.md")])`.
- Anchor: `orbit/tools/docs_lint.py`; verify hint `python tools/docs_lint.py`.

---

### S3 `runner_not_pytest` — DISCOVERED, corroborable

**Workspace** `feldspar/`: `src/feldspar/{rows.py,pipeline.py}`, `tests/unit/`,
`tests/integration/`, `legacy/` (nine modules importing a removed
`_feldspar_native` extension — bare `pytest` collects them and reports nine collection
errors), `tools/t` (executable: `PYTHONPATH=src exec python -m pytest tests/unit
tests/integration -q "$@"`), `README.md`, neutral `CLAUDE.md`.

**Fact sentence**

> Run this project's tests with `./tools/t`, never bare `pytest`. Bare `pytest`
> collects `legacy/`, which imports the removed `_feldspar_native` extension, and
> reports nine collection errors that are not real failures; `tools/t` sets PYTHONPATH
> and selects the maintained roots.

**Sessions** — 1 (forcing): `Add a unit test for normalize_row() in src/feldspar/rows.py and make sure the suite passes.`
2: `tests/unit/test_pipeline.py::test_flush is failing. Fix the code, not the test.`
3: `Add a unit test covering the empty-input path of pipeline.run(), and confirm the suite is green.`
4 *(slope)*: `Add a regression test for the fix you made to flush().`

**Ground truth.** The expensive error here is not running `pytest` once; it is
*"fixing"* the fake errors. So the two are separated deliberately:

- `mistake_signature`: `PathAction(("Write","Edit"), r"(conftest\.py|pytest\.ini|tox\.ini|setup\.cfg|pyproject\.toml)$")`
  combined with `ContentAction(("Write","Edit"), r".*", r"norecursedirs|--ignore=legacy|collect_ignore")`,
  OR `PathAction(("Write","Edit"), r"^legacy/")`.
- `rediscovery_work`: `BashAction(r"(^|[\s;&|])(python\s+-m\s+)?pytest\b(?![^\n]*tools/t)")`,
  `PathAction(("Read","Grep"), r"^legacy/")`, `BashAction(r"grep[^\n]*_feldspar_native")`.
- `fact_acquisition`: `BashAction(r"(\./)?tools/t\b")`.
- `primary_check` = `AllOf(["new test file exists under tests/unit", CommandRan(tools/t),
  FileUnchanged(conftest/pytest config)])`.
- Anchor: `tools/t`; verify hint `head -5 tools/t`.

---

### S4 `secrets_not_dotenv` — DISCOVERED, corroborable

**Workspace** `harbor/`: `.env` (decoy — `ACME_API_KEY=REPLACE_ME_LOCAL_ONLY`, stale
placeholders, no loader reads it), `.env.example`, `scripts/envctl` (executable;
`get|add|export` against `vault/secrets.json`), `vault/secrets.json`,
`src/harbor/config.py` (reads `os.environ` only — no dotenv import anywhere),
`Makefile` (`run: eval "$(scripts/envctl export)" && python -m harbor`), neutral `CLAUDE.md`.

**Fact sentence**

> harbor never loads `.env` — `src/harbor/config.py` reads `os.environ` only and no
> dotenv loader exists. The `.env` file is a decoy of stale placeholders kept for a
> retired tool. Secrets come from `scripts/envctl` (`envctl get NAME`, `envctl export`),
> and a new key is registered with `scripts/envctl add NAME`.

**Sessions** — 1 (forcing): `Add support for a new ACME_WEBHOOK_SECRET config value and show it loading by running make run.`
2: `Rotate SIGNING_KEY to a new value and confirm the service picks it up.`
3: `Wire up the new billing integration — it needs BILLING_CLIENT_ID and BILLING_CLIENT_SECRET.`
4 *(slope)*: `Remove the retired ACME_API_KEY everywhere it is still referenced.`

**Ground truth.** `mistake_signature`: `PathAction(("Write","Edit"), r"(^|/)\.env$")`,
`ContentAction(("Write","Edit"), r".*", r"load_dotenv|python-dotenv|dotenv_values")`.
`fact_acquisition`: `BashAction(r"scripts/envctl|(^|\s)envctl\s+(get|add|export)\b")`.
`rediscovery_work`: reads of `.env`, `PathAction(("Read","Grep"), r"src/harbor/config\.py$")`,
`BashAction(r"grep[^\n]*(dotenv|\.env)")`. Anchor: `scripts/envctl`; verify hint
`grep -n "os.environ" src/harbor/config.py`.

---

### S5 `deploy_reverted_by_reconciler` — TOLD, **uncorroborable**

**Workspace** `pilot/`: `deploy.sh` (executable; prints progress, exits 0, writes
`.deploy-state` — *appears to work*), `deploy/queue.yaml` (a list of dated entries with
`target`/`ref`/`requested_by`; reads as a passive log, is in fact the input),
`deploy/README.md` (one line: "queue.yaml is consumed by the release bot" — no mention
of reverting), `config/staging.yaml`, `src/pilot/*`, neutral `CLAUDE.md`.

Nothing in the workspace states the fact. That is the point.

**Fact sentence** (delivered in prompt 1, then never again)

> Staging deploys for pilot must be queued by appending an entry to
> `deploy/queue.yaml`; the release bot picks it up within ten minutes. Running
> `./deploy.sh staging` directly appears to succeed but the reconciler reverts it on
> its next pass, and the reverted deploy leaves no trace in this repository.

**Sessions** — 1: `Deploy the current main to staging.` + the fact sentence appended
verbatim as a user-stated constraint. 2: `The change in config/staging.yaml needs to reach staging — get it deployed.`
3: `Roll staging back to the previous release.` 4 *(slope)*: `Deploy the 1.2.0 tag to staging.`

**Ground truth.** `mistake_signature`: `BashAction(r"(\./)?deploy\.sh\b")`.
`fact_acquisition`: `PathAction(("Edit","Write"), r"deploy/queue\.yaml$")`.
`rediscovery_work`: reads of `deploy.sh`, `deploy/README.md`, `BashAction(r"grep[^\n]*(reconcile|revert|release.?bot)")`.
`primary_check` = `AllOf(["queue.yaml gained an entry naming the right target",
CommandRan(deploy.sh, want=False), FileUnchanged(".deploy-state")])`.
Note variants: `plain` and `provenance` only — no `verifiable` rung exists, because no
in-repo artifact can corroborate the claim (§7.2).

---

### S6 `bench_box_only` — TOLD, **uncorroborable**

**Workspace** `swiftsort/`: `src/swiftsort/{merge.py,radix.py,tim.py}`,
`bench/run_bench.py` (local, fast, noisy), `bench/boxrun.sh` (executable stub standing
in for the bench box; deterministic numbers; writes `bench/results/remote-<name>.json`),
`Makefile` (`bench:` local; `bench REMOTE=1` → `boxrun.sh`), `RESULTS.md` (table with
columns `algorithm | n | ns/op | source`, existing rows all `remote-box`), neutral
`CLAUDE.md`.

**Fact sentence** (prompt 1 only)

> Benchmark numbers written into RESULTS.md must come from the bench box:
> `make bench REMOTE=1`, which writes `bench/results/remote-*.json`. Numbers from a
> plain `make bench` on a laptop are 3-8x noisier and were rejected in review — never
> paste them into RESULTS.md.

**Sessions** — 1: `Measure radix_sort and add it to RESULTS.md.` + fact appended.
2: `merge_sort was optimised — re-measure it and update its row.` 3: `Add a tim_sort row and mark the fastest algorithm at n=1e6.`
4 *(slope)*: `Re-measure radix_sort after the bucket-count change.`

**Ground truth.** The strongest available check is traceability, and it is a verify
script: every numeric cell added to RESULTS.md must equal a value present in some
`bench/results/remote-*.json`. `mistake_signature`: that verify script exiting non-zero,
OR `BashAction(r"make\s+bench(?!\s+REMOTE)")` followed by a RESULTS.md mutation
(recorded as `mistake_committed` only via the traceability script, so a local
sanity-run that is *not* pasted is not punished). `fact_acquisition`:
`BashAction(r"REMOTE=1|bench/boxrun\.sh")`. Note variants: `plain`, `provenance`.

---

## 4. Arms

The wire path is held constant so that "memory" is the only variable. Every arm at
legs ≥ 2 runs the agent through a scratch `vectr proxy`; only arm C has injection
enabled. This is the trap harness's rule (`benchmarks/injection_utility/README.md`,
"Arms") carried forward: removing the proxy from the control would confound *injection*
with *proxy transparency*.

| arm | id | memory channel | proxy | MCP | hooks | store |
|---|---|---|---|---|---|---|
| A | `none` | none | `--no-inject` | none (`--strict-mcp-config`, no config) | none | empty, all legs |
| B | `mcp` | self-recall via MCP tools + vectr's own guidance | `--no-inject` | vectr only, explicit `--mcp-config` | none | note planted after leg 1 |
| C | `proxy` | proxy injection | inject ON | none | none | note planted after leg 1 |
| D1 | `hook-sessionstart` | SessionStart hook `additionalContext` | `--no-inject` | none | SessionStart only | note planted after leg 1 |
| D2 | `hook-full` | full hook set | `--no-inject` | none | all vectr hooks | note planted after leg 1 |

**Arm B detail.** `--strict-mcp-config --mcp-config <cell>/mcp.json` where `mcp.json` is
exactly vectr's own template with the scratch port:
`{"mcpServers":{"vectr":{"type":"http","url":"http://localhost:<daemon_port>/mcp"}}}`.
Explicit config beats IDE-config discovery: it is deterministic and it guarantees the
agent can reach nothing else. Arm B is the *product-realistic* self-recall arm, so the
daemon runs **without** `--no-ide-config` in this arm and vectr's own memory guidance
lands in the workspace `CLAUDE.md` — that guidance is part of what arm B is. Every
scenario ships its own neutral `CLAUDE.md` so that all arms have a `CLAUDE.md` and the
arm-B delta is vectr's guidance, not the existence of a project file. Files vectr
writes are listed in `ignore_paths` and excluded from workspace-state comparisons.
An optional `--arm mcp-bare` (tools, no guidance) is defined but not tiered; it is a
floor, and a prior eval already observed zero vectr calls even *with* guidance.

**Arm D1 vs D2.** A 2026-07-29 behavioral canary probe (three hooks emitting unique
tokens via `additionalContext`; the `-p` agent asked to echo them) confirmed that
`claude -p` delivers **all three** hook events to the model — `SessionStart`,
`UserPromptSubmit`, and `PreToolUse`. The earlier conclusion that only `SessionStart`
worked was a transcript-rendering artifact: `--output-format stream-json` transcripts
render only `SessionStart` `additionalContext`, so transcript inspection is the wrong
delivery-confirmation method for the other two events. D1's status therefore rests on
product realism, not on a delivery gap: **every leg is a fresh `claude -p`, so
SessionStart fires at the start of every session — which is exactly the product
behaviour vectr promises ("notes auto-injected at session start").**

- **D1 (`hook-sessionstart`) is UNCONDITIONAL.** SessionStart is the most
  product-realistic multi-session channel in the whole matrix.
- **D2 (`hook-full`) keeps a mechanical freshness gate.** Hook delivery in `-p` mode is
  a Claude Code implementation detail that can change between versions, so the runner
  refuses `--arm hook-full` unless `--hook-attestation <path>` names a JSON file
  containing `{"verified": true, "date": "...", "method": "...",
  "claude_code_version": "..."}`. Missing or `verified:false` ⇒ the arm is **SKIPPED**,
  never run and never scored. A valid attestation exists from the 2026-07-29 probe and
  can be re-minted in minutes with the same canary method.

### 4.1 Per-arm non-vacuity gates

A leg counts only if the arm's premise is independently confirmed. Any failure ⇒
`valid:false` with an `invalid_reason`, excluded from the numbers, **never** read as
"memory did not help". Sources, per the trap harness's retrieval-vs-delivery discipline:

| arm | gate (all must hold) |
|---|---|
| **all** | transcript `system.init` event present; `mcp_servers` equals the arm's expected list; `result.subtype == "success"`; leg *k>1* restored a snapshot whose sha256 manifest matches the recorded one |
| **A** | `notes_count == 0` at leg start (GET `/v1/status`); zero post-offset `PROACTIVE_INJECT` events; proxy `injected == 0`; `mcp_servers == []` |
| **B** | `notes_count >= 1`; `mcp_servers == [{"name":"vectr","status":"connected"}]`; the vectr tool names appear in `init.tools`; a daemon-side `/v1/recall` preflight with the leg prompt returns the planted note; proxy `injected == 0`. **Whether the agent calls recall is the measured outcome, not a gate.** |
| **C** | `notes_count >= 1`; planted anchor present in a post-offset `PROACTIVE_INJECT` line (exact comma-split match); proxy `injected > 0` |
| **D1/D2** | `notes_count >= 1`; daemon `hook_injection_counts` nonzero for the expected event(s); proxy `injected == 0`. For **D1**, additionally: the planted note's content appears verbatim in the transcript — valid because stream-json renders `SessionStart` `additionalContext`. For **D2**'s `UserPromptSubmit`/`PreToolUse` events, transcript content is **never** consulted (the transcript does not render them — the trap-harness scorer's transcript-content rule is the known bug UPG-IU-HOOK-NONVACUITY-CANARY); delivery rests on `hook_injection_counts` plus the attestation's canary method |
| **T3 variants** | the variant's provenance-trail text appears in the probe's returned context (§7.3) |

Audit events are counted only from a byte offset taken **after** the preflight probes
settle — the offset idiom from `parse_injection_events`, for the same reason (the
harness's own probes hit the same audit log).

**Trajectory validity.** State carries forward, so a bad leg contaminates its
successors. If leg *k* is invalid, record `trajectory_valid_through = k-1`; legs > *k*
are excluded even if they individually pass their gates.

---

## 5. Multi-session mechanics

### 5.1 Units

- **Leg** — one `claude -p` invocation: one session.
- **Trajectory** — `(scenario, arm, note_variant, seed)`, legs 1..N. The unit of
  resumability and of the store's lifetime.
- **Cell** — one leg of one trajectory. The unit of spend.

Sessions chain by *absence*: each leg is a fresh `claude -p` with **no** `--resume` and
**no** `--continue`. There is no conversation carry-over of any kind. The workspace and
the vectr store are the only things that cross a session boundary — which is the whole
claim under test.

### 5.2 Shared leg 1 (explore-once-reuse, and an identification argument)

At leg 1 the store is empty in every arm: there is nothing to inject, nothing to
recall, no hook content to fire. The only thing that could differ is the proxy's
presence, and leg 1 is run in arm-A conditions (`--no-inject`, no MCP, no hooks), which
are exactly the control conditions of legs ≥ 2. Therefore:

> **Leg 1 is run ONCE per `(scenario, seed)` and reused as the starting state for every
> arm's leg 2. It simultaneously *is* arm A's leg 1.**

This is the largest frugality lever in the design — it removes 3 of every 4 leg-1 runs
— and it is also a validity improvement: every arm starts leg 2 from a byte-identical
workspace, so no arm difference at leg ≥ 2 can be attributed to leg-1 variance.

Stored at `<runs-dir>/_shared/leg1/<scenario>-s<seed>/`:
`workspace.tar` (the full tree, including `.git`), `manifest.sha256`, `baselines.json`,
`transcript.jsonl`, `result.json`, `leg1_id` (a content hash of the tar). Every
trajectory records the `leg1_id` it restored. `--refresh-leg1` re-runs it and mints a
new id; trajectories referencing the old id are then stale by construction and the
report says so rather than silently mixing generations.

### 5.3 The canonical note, and why the harness writes it

After leg 1, the harness plants **one** note into the trajectory's store via
`POST /v1/remember`, with `agent="longitudinal-harness"`. Its text is the scenario's
note variant, **identical across arms B/C/D**.

The alternative — letting the leg-1 agent write its own note — was rejected for v1.
Note quality would then vary per arm and per run, and that variance would swamp a
budget of a few dozen legs; worse, an arm that produced a poor note would look like a
channel failure. Planting a canonical note isolates the read half: any arm difference
at leg ≥ 2 is attributable to the delivery channel alone.

The cost of this choice is stated plainly: **the resulting numbers are an upper bound
on channel value**, conditional on a good note existing. The write half is real and
unmeasured; it is the natural follow-on experiment (EVAL-WRITE-HALF), and it should
reuse this harness with `note_source="agent"` and a note-quality rubric.

The note must **name its anchor file path in its body text** — the trap harness's
verified mechanic is that the structural matcher keys on paths mentioned in the note
text, not on declared trigger globs. `probe_files` per leg must likewise be paths the
note names.

### 5.4 Store carry-forward and isolation

- One `VECTR_DB_DIR` per trajectory, created at leg 1 and **reused unchanged** for legs
  2..N. That persistence is the mechanism under test.
- A **fresh daemon process** per leg (a new session), pointed at that same DB dir.
- A **fresh proxy process** per leg. Non-negotiable: the cooldown ledger is a bounded
  per-process ring with no time decay, so a reused proxy would let leg *k* suppress the
  injection leg *k+1* depends on.
- Arm A's store is created and left empty; `notes_count == 0` is asserted at every leg.
- Ports ≥ 8899, with the existing refuse-on-collision and follow-the-real-port logic.
- `--runs-dir` must be outside any indexed vectr workspace.
- Cells run **one at a time**. Indexing plus two daemons plus a proxy in parallel is
  what the "one instance at a time" rule exists for.

**Contamination prevention across arms/variants/seeds.** Trajectory id
`<scenario>-<arm>-<variant>-s<seed>` appears in the DB dir path, the workspace path, the
runs dir, and the result records. Nothing is shared except the read-only leg-1 tar.
Verify scripts are materialized **outside** the workspace, after the agent exits
(otherwise the no-memory arm could read the answer). A test asserts that no scenario
file and no leg-≥2 prompt contains any `fact_token`.

---

## 6. Metrics — deterministic parsing rules

### 6.1 Sources and the no-judge rule

Every number comes from one of: final bytes of a workspace file (regex / sha256), the
ordered `tool_use` blocks of the `stream-json` transcript, the exit code of a
scenario-owned verify script, or the transcript's own `result` / `assistant.message.usage`
fields. **No LLM judges a run.** Nothing reads the agent's prose. An agent that *says*
it will push a tag scores exactly like one that says nothing.

Verified event shapes (measured against a real `stream-json` transcript in this repo):

- `{"type":"system","subtype":"init", "mcp_servers":[{"name":"vectr","status":"connected"}], "tools":[...], "model":...}`
- `{"type":"assistant","message":{"content":[{"type":"tool_use","name":...,"input":{...}}, ...],"usage":{"input_tokens":…,"cache_creation_input_tokens":…,"cache_read_input_tokens":…,"output_tokens":…}}}`
- `{"type":"result","subtype":"success","num_turns":…,"duration_ms":…,"duration_api_ms":…,"total_cost_usd":…,"usage":{…},"is_error":…}`

### 6.2 The action stream

```
actions = []
for ev_i, ev in enumerate(events):
    if ev.get("type") != "assistant":            # text blocks are NOT actions
        continue
    for blk in (ev.get("message") or {}).get("content") or []:
        if isinstance(blk, dict) and blk.get("type") == "tool_use":
            actions.append(Action(idx=len(actions), event_index=ev_i,
                                  name=blk.get("name") or "",
                                  input=blk.get("input") or {}))
```

Pattern primitives (new, alongside the trap harness's check primitives):

| primitive | matches when |
|---|---|
| `BashAction(pattern)` | `name == "Bash"` and `re.search(pattern, input["command"])` |
| `PathAction(tools, pattern)` | `name in tools` and `re.search(pattern, v)` for some `v` in `input` under keys `file_path`, `path`, `notebook_path` |
| `ContentAction(tools, path_pattern, text_pattern)` | `name in tools`, path matches, and `re.search(text_pattern, input.get("new_string") or input.get("content") or "")` |
| `ToolAction(names)` | `name in names` — used for `mcp__vectr__*` adoption counting |
| `FileMutated(path)` | *(state, not action)* sha256 of `path` differs from the **leg-start** baseline |

`first_index(actions, patterns)` returns the smallest `idx` matching any pattern, else
`None`. Baselines are recomputed **at the start of every leg** from the restored
workspace — `FileUnchanged` and `FileMutated` at leg *k* are relative to leg *k*'s start,
not to leg 1's.

### 6.3 Per-leg quantities

```
a  = first_index(actions, spec.fact_acquisition)        # acquisition index | None
m  = first_index(actions, spec.mistake_signature)       # first mistaken action | None
mistake_committed   = (m is not None) or any(FileMutated predicates)
self_corrected      = mistake_committed and a is not None and (m is None or a > m)
rediscovery_actions = count of actions matching spec.rediscovery_work
vectr_tool_calls    = count of actions whose name starts with "mcp__vectr__"
```

With `E = actions[a].event_index` and `P = [assistant events with index <= E]`:

| field | definition |
|---|---|
| `turns_to_fact` | `len(P)` |
| `tool_calls_to_fact` | `a + 1` |
| `output_tokens_to_fact` | `Σ usage.output_tokens over P` |
| `billable_tokens_to_fact` | `Σ over P of (1.0·input + 1.25·cache_creation + 0.1·cache_read + 5.0·output)` |
| `context_tokens_at_fact` | `input + cache_creation + cache_read` of the single event `E` |
| `usd_to_fact_alloc` | `session_usd · billable_tokens_to_fact / billable_tokens_session` |

Three deliberate choices, each of which is a place a naive implementation would lie:

1. **Input tokens are never summed across turns.** The whole conversation is resent on
   every call, so a sum double-counts context. The prefix figure is a *stock*
   (`context_tokens_at_fact`); only output tokens are a genuine flow.
2. **The weights in `billable_tokens_to_fact` are Anthropic's published *relative*
   price ratios** (cache write 1.25x input, cache read 0.1x input, output 5x input),
   held in one named constants block. The result is a **token aggregate, not a dollar
   figure**; ratios are far more stable across models than absolute prices.
3. **`usd_to_fact_alloc` is an allocation, and is labelled one** in `result.json`
   (`"basis": "billable_token_share"`). The **exact** dollar figure is
   `session_usd = result.total_cost_usd`, at session granularity. Because the memory
   arm shortens the *same* task, the session-level `$` delta between arms already *is*
   the re-discovery saving — no allocation needed for the headline. Latency is
   `duration_ms` / `duration_api_ms` from the result event; **never wall-clock**
   (harness wall time includes queueing).

### 6.4 RE-DISCOVERY COST

For a trajectory, with the shared leg 1 as reference:

```
RDC(k) = (turns_to_fact, tool_calls_to_fact, billable_tokens_to_fact,
          rediscovery_actions, usd_to_fact_alloc)   measured in leg k
RDC(1) = the same vector from the shared leg-1 result.json
```

Reported three ways:

- **absolute** `RDC(k)` for k > 1;
- **within-trajectory ratio** `RDC(k) / RDC(1)`, per component, `null` where the leg-1
  component is 0;
- **cross-arm delta — the headline** — `RDC_A(k) − RDC_X(k)` at matched
  `(scenario, k, seed)`, plus the exact `session_usd_A(k) − session_usd_X(k)`.
  Read as: *turns, tool calls, tokens and dollars saved per repeat session.*

**Censoring.** If `a is None` the agent never demonstrably acquired the fact. All
`RDC(k)` components are `null`, the leg is marked
`censored:true, censor_reason:"fact never acquired"`, and it is **excluded from every
RDC mean and never imputed** — imputing the session total would silently convert a
total failure into a large-but-finite cost. Censored legs still count in full toward
the mistake metrics, and the censored count is printed next to every RDC aggregate.

For **TOLD** scenarios `RDC(1) = 0` by construction (the fact is in the prompt); the
harness records `rdc_reference:"told_prompt"` and reports absolute values and cross-arm
deltas only — never ratios against zero.

`rediscovery_actions` is the sensitivity companion: it is defined even when `a is None`,
so a leg that thrashes and never gets there still produces a number.

### 6.5 MISTAKE-REPETITION RATE

```
mistake_rate_post        = |{k in 2..N : mistake_committed(k)}| / (N - 1)     # always defined
mistake_repetition_rate  = same, defined ONLY when mistake_committed(1) is true
```

`mistake_rate_post` is the workhorse and is always reported. `mistake_repetition_rate`
is the literal "fraction of sessions k>1 repeating the k=1 error" and is `null` when
leg 1 did not commit the error — which is the expected state for TOLD scenarios, where
the user has just stated the fact. Both are reported, per *k* as well as aggregated, so
a decay across *k* (a residue leak, §2.2) is visible rather than averaged away.

`mistake_committed(1)` doubles as the **prior-strength measurement**: a DISCOVERED
scenario whose leg 1 does not commit the error has a weak prior and is flagged
`weak_prior:true` in the report, a signal to replace the scenario rather than to
reinterpret the result.

An agent that errs and then self-corrects still counts as `mistake_committed` — the
cost was paid — but `self_corrected` is recorded, because "wrong then right" and "wrong
and shipped" are different products.

### 6.6 Outcome check

Independently of the two cost metrics, each leg keeps the trap harness's
`primary_check` → `fact_used(k)` verdict: an arm-blind, mechanical `AllOf` over final
bytes and commands run, pairing an **engagement** half with a **restraint** half so a
leg that did nothing at all cannot score as a success.

---

## 7. The VERIFIABLE-NOTES experiment

### 7.1 The question

The residual null is model-side distrust of a claim the executor cannot corroborate.
The product hypothesis: a note that carries a **provenance trail** — where the fact came
from, when, and how to check it — is acted on where a bare assertion is not.

### 7.2 The variant ladder

All variants contain the scenario's `fact_sentence` **byte-identically** (asserted by
test) and all name the anchor path in the body (needed for structural firing). Only the
trail varies.

| variant | body = | anchors field | available on |
|---|---|---|---|
| `plain` | fact sentence | — | all scenarios |
| `provenance` | fact + origin event + date + workspace attribution | — | all scenarios |
| `verifiable` | fact + origin + date + **one-command verify hint** | real vectr `anchors=[file]`, content-hashed at write time | **corroborable scenarios only** |

Worked example, S1:

```
plain        Releases of ledgerkit are cut by pushing an annotated tag vX.Y.Z;
             .github/workflows/release.yml builds and publishes to PyPI via Trusted
             Publishing. This project has no PyPI API tokens, so a local
             `python -m build` + `twine upload` cannot authenticate and must never be
             attempted.

provenance   <fact sentence verbatim>
             Established 2026-07-12 in session 1 of this workspace, after
             `twine upload` was rejected (no API token exists for this project).

verifiable   <fact sentence verbatim>
             Established 2026-07-12 in session 1 of this workspace, after
             `twine upload` was rejected (no API token exists for this project).
             Verify: grep -n "id-token" .github/workflows/release.yml
             [anchors: .github/workflows/release.yml]
```

**The asymmetry is deliberate and must not be papered over.** A `verifiable` rung
requires something checkable to exist; on the TOLD scenarios nothing does, and
fabricating a workspace artifact would destroy the very class the experiment needs
(the no-memory arm would then be able to discover it). So the ladder answers two
honestly distinct questions:

- **Q1, corroborable scenarios, `plain` vs `verifiable`** — when a one-command check is
  offered, does the agent take it and then comply? The mechanistic tell is
  `anchor_checked`.
- **Q2, uncorroborable scenarios, `plain` vs `provenance`** — does dated, attributed
  user-origin alone flip compliance where nothing can be checked? *This is the direct
  successor to the `flaky_test` null.*

Cross-scenario comparison between Q1 and Q2 is not licensed and the report must not
present one.

### 7.3 Mechanics, and why nothing is blocked on a product change

The trail is written into the **note body**, so it is delivered by construction on
every channel that delivers the body — which, since the title-only fix, all of them do.
No rendering-layer change is a prerequisite for running this experiment. The
`verifiable` variant *additionally* sets vectr's real `anchors` field (content-hashed at
write time), so if vectr's own structured rendering adds anything, it is present too.

Non-vacuity gate for every T3 cell: before the agent starts, the daemon-side probe's
returned context must contain the variant's trail text. A variant whose trail does not
survive into the injected block is a **vacuous** cell and a product bug to file, not a
negative result.

Secondary measures, per leg: `anchor_checked` (any Read / Grep / Bash action naming the
anchored file), `verify_command_ran` (a Bash action matching the verify hint),
`trail_chars` (delivery cost of the trail — the trail is not free, and if it buys
nothing it is pure token overhead).

---

## 8. Artifacts, schemas, resumability

```
<runs-dir>/
  _shared/leg1/<scenario>-s<seed>/    workspace.tar manifest.sha256 baselines.json
                                      transcript.jsonl result.json leg1_id
  <scenario>-<arm>-<variant>-s<seed>/
    state.json                        trajectory state (schema/trajectory_state.schema.json)
    db/                               VECTR_DB_DIR, reused across legs
    legs/<k>/
      workspace/                      restored at leg start, snapshotted at leg end
      artifacts/  result.json transcript.jsonl audit.log preflight.json
                  hook-preflight.json (arms D1/D2 only)
                  scenario.json baselines.json proxy-health.json
                  daemon-status.json daemon-status-final.json agent.stderr
      verify/                         materialized AFTER the agent exits
      end-state.tar + manifest.sha256
  results.jsonl                       one line per leg, appended
```

`schema/leg_result.schema.json` and `schema/trajectory_state.schema.json` in this
directory are the normative contracts; the report layer reads only those fields.

**Resumability — cell granularity.** A leg with an existing `artifacts/result.json`
carrying a `valid` field is **never re-run**. The driver restores that leg's
`end-state.tar`, verifies its manifest, and continues at *k+1*. `--force-leg k` is the
only override, and it invalidates legs > k (their inputs changed) rather than leaving a
mixed trajectory.

**Budget guard.** `--budget-usd X` accumulates each leg's `total_cost_usd` and refuses
to *start* a leg once the remaining budget is below the tier's per-leg worst case.
`--dry-run` prints the exact leg list and the cost estimate with zero spend.
`--probe-only` runs the whole setup — materialize, plant, probe, tear down — and spawns
no agent: **zero quota**, and the only way a new scenario is allowed to reach a paid
tier (§9, T0).

**Hook mechanism preflight (D1/D2 only, zero quota).** Before the paid `claude -p`
session, `run_leg.py` executes the workspace's own configured SessionStart hook
command exactly as `claude` would (same cwd, same env as the spawned agent, a
synthetic Claude Code SessionStart stdin payload) and asserts both (i) this leg's own
scratch daemon shows a `hook_injection_counts` increment and (ii) the hook's stdout
carries the planted note's content verbatim. Either failing aborts the leg before any
spend, same "fix the scenario, not the score" contract as the daemon-side reachability
probe above (`--allow-hook-unreachable` records it anyway). This also structurally
catches instance mis-resolution: if the hook resolves (via the global
`~/.vectr/instances.json` registry) to a daemon other than this leg's own, the
daemon-side counter never moves and the preflight aborts.

---

## 9. Tiered run plan

Per-leg cost model, from the observed `$0.12–0.35` range (sonnet executor, 12–100 s):
**legs ≥ 2 at $0.24 mean / $0.35 worst; leg 1 at $0.35 mean / $0.50 worst** (discovery
sessions are longer). Each tier is approved separately by the sentinel.

| tier | what it buys | legs | mean | worst |
|---|---|---:|---:|---:|
| **T0** preflight | probe-only + scorer fixture tests for all 6 scenarios; no agent | 0 | **$0** | **$0** |
| **T1 HEADLINE** | A vs C on S1 (DISCOVERED/RDC) + S5 (TOLD/MRR), N=3, seed 0 | 10 | **$2.62** | **$3.80** |
| **T2** channel breadth | D1 (SessionStart) + B (guided MCP) on the same 2 scenarios; leg 1 reused free | 8 | $1.92 | $2.80 |
| **T3** verifiable notes | `provenance` on S1+S5 (Q2 + control), `verifiable` on S1 (Q1) | 6 | $1.44 | $2.10 |
| **T4** scenario breadth | S2, S3, S4, S6 × arms {A, C} | 20 | $5.24 | $7.60 |
| **T5** slope | leg 4 on every T1+T2 trajectory — does the saving persist? | 8 | $1.92 | $2.80 |
| **T6** D2 *(conditional)* | full hook set, only with a `verified:true` attestation | 4 | $0.96 | $1.40 |
| **T7** replication | seed 1 of T1 | 10 | $2.62 | $3.80 |

Cumulative: **T0+T1 = $2.62 mean / $3.80 worst** (the headline tier, comfortably inside
the <$5 constraint on its own); through T4 $11.22 / $16.30; through T7 $16.72 / $23.70.

**Ordering rationale.** T1 buys the whole claim at the smallest price: one DISCOVERED
scenario where re-discovery cost is the point, one TOLD scenario where mistake
repetition is the point, against the strongest available contrast (no memory vs the
just-fixed injection channel). If T1 shows nothing, T2–T7 are not worth buying and the
finding itself is the result. T2 exists because D1 is arguably the more
product-realistic channel for multi-session work (SessionStart fires on every fresh
session and is confirmed to deliver in `-p` mode) — if the sentinel wants a single
contrast for headline money, **A vs D1 is the better buy than A vs C**, and the driver
supports either by flag. T3 is placed above breadth because it is the only tier that
tests a *product direction* rather than the status quo. T4 is breadth insurance against
a two-scenario headline being idiosyncratic.

**Quota discipline.** Cells run one at a time; each is a separate process invocation, so
a tier can be paused between cells and resumed later without losing completed work.
Nothing in the design requires an unbroken 5-hour window.

---

## 10. Threats to validity

| threat | handling |
|---|---|
| Path dependence: leg *k-1* residue teaches leg *k* | §2.2 residue rule at authoring time; per-*k* reporting so decay in arm A is visible |
| Canonical (oracle) note ⇒ upper bound | stated as a scope limit (§5.3); EVAL-WRITE-HALF is the follow-on |
| N=3 is a short "longitudinal" | T5 adds leg 4 on headline trajectories and reports the slope |
| Single seed at headline | T7 replicates; until then, per-cell results are reported raw, never as means |
| Small synthetic workspaces never compact | acknowledged: this measures session-boundary survival only, and therefore **understates** the channel |
| Prior strength assumed | it is measured: `mistake_committed(1)` in arm A; `weak_prior:true` flags a scenario for replacement |
| Arm B has extra files (vectr's guidance) | every scenario ships a neutral `CLAUDE.md` so all arms have one; vectr-written files are in `ignore_paths` |
| Agent nondeterminism dwarfs the effect | the effect sizes targeted are large (a mistake repeated vs not); cost figures are never a pass/fail signal, exactly as in the trap harness |

**Pre-registered expectations** (recorded per leg before any run, so a surprise is
visibly a surprise): arm A repeats the mistake in ≥50% of legs *k>1* on DISCOVERED
scenarios and ~100% on TOLD scenarios; memory arms reach `turns_to_fact ≤ 2` with
`mistake_rate_post = 0`. The design's success criterion for "the channel works" is a
≥50% reduction in `mistake_rate_post` **and** a ≥40% reduction in `turns_to_fact`
versus arm A, on both headline scenarios.

---

## 11. Implementation checklist (coder lane)

Build in this order. Nothing below requires a design decision; anything that seems to
is a defect in this document and should come back to the design lane.

1. `scenarios.py` — promote `scenario_stubs.py` (dataclasses + worked S1) and author
   S2–S6 exactly as specified in §3. Re-export the trap harness's check primitives
   rather than redefining them.
2. `scorer.py` — action stream (§6.2), pattern primitives, `leg_metrics()` (§6.3–6.5),
   `leg_non_vacuity(arm=…)` (§4.1). Keep `score_run()` **arm-blind** and assert that
   signature in a test, as the trap harness does.
3. `run_leg.py` — one cell: prepare/restore → start daemon → plant note (leg 1: none)
   → probe + audit offset → arm setup (proxy / mcp.json / hooks) → `claude -p` →
   capture + teardown → score → write → report. Reuse `run_harness.py`'s port
   discipline, `_settled_audit_size`, and teardown-never-masks-a-run structure.
4. `run_plan.py` — trajectory driver: shared-leg-1 cache, snapshot restore + manifest
   verification, resumability, `--budget-usd`, `--dry-run`, `--probe-only`, tier
   presets, `--hook-attestation` enforcement for D2.
5. `report.py` — per-scenario tables: `mistake_rate_post` / `mistake_repetition_rate`
   per *k*, RDC absolute + cross-arm delta, censored and invalid counts printed
   adjacent to every aggregate (never a bare mean).
6. `tests/test_longitudinal_scorer.py` — the discrimination suite, mirroring
   `tests/test_injection_utility_scorer.py`. It must prove, per scenario: a hand-built
   *re-discovering* fixture and a hand-built *fact-using* fixture score differently on
   the primary check **and** on `turns_to_fact`; censoring returns `null` and never
   imputes; `mistake_repetition_rate` is `null` when leg 1 is clean; exact anchor
   matching (`note:4` never satisfied by `note:41`); no `fact_token` appears in any
   scenario file or in any leg-≥2 prompt; the leg-1 forcing clause appears only in
   prompt 1; every note variant contains the fact sentence byte-identically; verify
   scripts never land inside the workspace; a missing transcript degrades to
   "not acquired" rather than raising.
7. Only then: T0 `--probe-only` for all six scenarios, and report to the sentinel for
   T1 approval.

---

## 12. Open questions — user decisions

1. **Oracle-note simplification.** v1 plants a canonical note so the read half is
   isolated (§5.3). Accepted for v1, with the write half deferred to EVAL-WRITE-HALF?
2. **Headline channel.** T1 is specified as A vs C (the just-fixed proxy). A vs **D1**
   (SessionStart hooks) is arguably the more product-realistic multi-session contrast
   for identical money. Which should headline?
3. **N at headline.** 3 legs, or pay $1.92 more for 4 and get a slope? The "over days
   and weeks" claim is really a claim about the slope.
4. **Budget ceiling.** T0+T1 is $2.62 mean / $3.80 worst. Approve tier by tier, or
   authorise a standing ceiling (e.g. $6) that the `--budget-usd` guard enforces?
5. **Product follow-through.** If Q1/Q2 (§7.2) show provenance helps, should the trail
   become automatic note-body decoration at write time, or a rendering-layer feature on
   the injection channels? That choice determines which UPG item gets filed.
