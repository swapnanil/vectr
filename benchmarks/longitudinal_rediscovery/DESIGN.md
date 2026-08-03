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
`deploy/README.md` (one line naming `deploy/queue.yaml`'s fields — mechanical only,
never who reads the file or that it is the deploy channel, as of DEFECT 12 below),
`config/staging.yaml`, `src/pilot/*`, neutral `CLAUDE.md`.

Nothing in the workspace states the fact. That is the point — see "Fixture surface
re-baseline (DEFECT 12)" below for the period in which this was not quite true.

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

### Fixture surface re-baseline (DEFECT 12)

S5's hidden fact has two halves: descriptive (the reconciler silently reverts a direct
`deploy.sh` run) and prescriptive (`deploy/queue.yaml` is the sanctioned channel). Before
this commit, `deploy/README.md` read `"queue.yaml is consumed by the release bot."` — a
fixture-reading agent could derive the correct action (use `queue.yaml`, not `deploy.sh`)
from the workspace alone, with no note, weakening the action contrast the note-variant
ladder (§7.2) exists to measure. Only the descriptive half was ever truly unrecorded.

**From this commit `deploy/README.md` is mechanical only**: it names `deploy/queue.yaml`'s
fields (`date`, `target`, `ref`, `requested_by`) and says nothing about who reads the file
or that it is the way to deploy. The prescriptive half now lives only in the note variants
(`plain`, `provenance`) and the fact sentence delivered in prompt 1. `deploy/queue.yaml`'s
seed bytes (`_S5_QUEUE_SEED`) were unchanged at this first boundary, including its own
in-file comment — that string is declared in `critical_residue_paths` and restored
verbatim at every k>=2 leg start (§6.5, DEFECT 10), so editing it would silently change
the workspace surface mid-trajectory for any recorded S5 trajectory a later leg extends.

**Second boundary (queue seed).** The seed's own former first line — `"# Consumed by the
release bot. Entries are appended, never edited in place."` — carried the same
prescriptive leak as the README, and could not change in the same commit: at the first
boundary, pre-fix S5 trajectories still had extensions ahead of them (T5's leg 4 and any
hook-full legs at seed 0), and the critical-residue reset writes the *current* seed at
every leg start, so an earlier seed edit would have mixed surfaces inside those
trajectories. The seed line was therefore de-prescribed in a second commit that landed
only after the last pre-fix S5 extension had completed. From that commit on the S5
surface is fully de-prescribed (README and queue seed), and T7's seed-1 S5 cells are the
first to run on it. A hook-full S5 cell revived by a late attestation *after* the second
boundary would restore the post-fix seed at k>=2 against a pre-fix leg-1 snapshot; such a
cell is mixed-surface and must be disclosed as such, or re-run from a fresh leg 1.

**The fixture surface is re-baselined at this commit, not retroactively rewritten.** A
trajectory's surface is fixed at its leg-1 materialization and held uniform across all its
legs (legs ≥2 restore from that trajectory's own prior snapshots — except the declared
`critical_residue_paths`, which `run_leg.py::_apply_critical_residue_reset` rewrites to the
*current* `scenarios.py` seed string at every leg start; uniformity for extended pre-fix
trajectories therefore also rests on those seed strings never changing, which is why
`_S5_QUEUE_SEED` is pinned byte-for-byte): every trajectory whose leg 1 was recorded before this commit — including T5's
leg-4 extension of a pre-fix trajectory and T2's reuse of a pre-fix shared leg 1 — stays on
the pre-fix surface (fixture-leaked prescriptive half) for all its legs. Only a fresh leg-1
materialization from this commit on gets the de-prescribed surface; T7's seed-1-and-later
S5 cells are the first to run on it. No recorded artifact is rewritten, regenerated,
deleted, or rescored by this fix — this is a fixture-authoring change, not a scoring change,
so `rescore.py` has nothing to do here. Any cross-boundary comparison of S5 cells (e.g. a
future aggregate mixing T1/T2's pre-fix data with T7's post-fix data) must state which side
of this boundary each cell came from; the pre-fix surface is already disclosed in the
campaign record via this section, so it needs no further caveat, only citation. T7's S5 half
is therefore a replication **plus** the first fixed-surface data — T7's S1 half remains a
pure replication, since S1 (DISCOVERED) has no fixture-prescription question.

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
| D2' | `hook-userpromptsubmit` | UserPromptSubmit hook `additionalContext` (per-prompt semantic recall) | `--no-inject` | none | UserPromptSubmit only | note planted after leg 1 |

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
  never run and never scored. Attestation validity is VERSION-BOUND: the 2026-07-29
  canary probe minted a valid attestation, but the same canary on Claude Code 2.1.220
  (2026-08-03) found PreToolUse delivery dead in headless mode — an attestation must be
  re-minted with the canary method against the exact installed version before any
  `hook-full` run, and a stale one must not be reused across Claude Code updates.

**`hook-userpromptsubmit` (D2', a UserPromptSubmit-only variant of D2) is
UNCONDITIONAL, like D1 — user decision, 2026-08-03.** A subsequent canary
probe (real `Read`/`Bash` tool calls inside a headless `claude -p` session,
`hook_injection_counts` captured before/after) found `PreToolUse` to be
**version-dependent**: on Claude Code 2.1.220, the daemon's `PreToolUse`
counter stayed flat (1→1) across genuine tool calls, while `SessionStart`
(1→2→3) and `UserPromptSubmit` (0→1→2) both incremented headless, and a
direct CLI invocation (`vectr hook pre-tool-use`) still fires and counts —
so the mechanism itself works, but `claude -p` does not reliably invoke it
on this version. `hook-full`'s existing attestation gate already exists for
exactly this kind of version drift and stays exactly as it was (unchanged,
still gating on `PreToolUse` alongside the other events). Rather than
interactively re-attesting `hook-full` each time Claude Code's headless
hook-invocation behavior shifts, or leaving D2 entirely parked pending a
fix, the chosen third option isolates the one channel independently proven
to both fire and deliver headless on the current version — `UserPromptSubmit`
— as its own arm, with no `SessionStart` and no `PreToolUse` hook wired at
all. This also has genuine product-realism value beyond working around the
version gap: it isolates the *per-prompt semantic-recall* injection path
(`VectrService._recall_impl`'s ordinary ranked `recall()` call, made on
every prompt submission) as its own measurement, distinct from D1's
once-per-session boot injection over the same store — the two exercise
different parts of the same working-memory system. UserPromptSubmit's
`additionalContext` still never renders into a `stream-json` transcript
(the same rendering gap the D1/D2 row below documents for
`UserPromptSubmit`/`PreToolUse`), so this arm's own non-vacuity gate (§4.1)
never consults transcript content either — firing/delivery evidence is
daemon-side `hook_injection_counts['UserPromptSubmit']` deltas only, never
a transcript grep for hook content.

### 4.1 Per-arm non-vacuity gates

A leg counts only if the arm's premise is independently confirmed. Any failure ⇒
`valid:false` with an `invalid_reason`, excluded from the numbers, **never** read as
"memory did not help". Sources, per the trap harness's retrieval-vs-delivery discipline:

| arm | gate (all must hold) |
|---|---|
| **all** | transcript `system.init` event present; `mcp_servers` equals the arm's expected list (arm **B**, mcp/mcp-bare, uses a presence+status rule instead — see DEFECT 13 below); `result.subtype == "success"`; leg *k>1* restored a snapshot whose sha256 manifest matches the recorded one |
| **A** | `notes_count == 0` at leg start (GET `/v1/status`); zero post-offset `PROACTIVE_INJECT` events; proxy `injected == 0`; `mcp_servers == []` |
| **B** | `notes_count >= 1`; an MCP server named `"vectr"` present in `mcp_servers` with status `"connected"` (or the transitional `"pending"`, provisionally accepted — DEFECT 13); vectr-tool evidence from ANY of `mcp__vectr__*` in `init.tools` (legacy shape), a real `tool_use`/`tool_result` pair for an `mcp__vectr__*` tool in the transcript, or the harness's own pre-session `/mcp` handshake probe; the polled daemon-side `/v1/recall` preflight either returns the planted note, or (if it doesn't) the transcript itself shows in-session delivery evidence; proxy `injected == 0`. **Whether the agent calls recall is the measured outcome, not a gate.** |
| **C** | `notes_count >= 1`; planted anchor present in a post-offset `PROACTIVE_INJECT` line (exact comma-split match); proxy `injected > 0` |
| **D1/D2** | `notes_count >= 1`; daemon `hook_injection_counts` nonzero for the expected event(s); proxy `injected == 0`. For **D1**, additionally: the planted note's content is found in the transcript (whitespace-normalized and JSON-escape-aware, DEFECT 8 — see below) — valid because stream-json renders `SessionStart` `additionalContext`. For **D2**'s `UserPromptSubmit`/`PreToolUse` events, transcript content is **never** consulted (the transcript does not render them — the trap-harness scorer's transcript-content rule is the known bug UPG-IU-HOOK-NONVACUITY-CANARY); delivery rests on `hook_injection_counts` plus the attestation's canary method |
| **D2'** (`hook-userpromptsubmit`) | `notes_count >= 1`; proxy `injected == 0`; a `user_prompt_submit_injection_delta >= 1` — the daemon's `hook_injection_counts['UserPromptSubmit']` counter captured immediately before and after THIS leg's own agent session (`run_leg.py::run_agent`, never the whole leg's cumulative count). No attestation required (UserPromptSubmit is independently canary-verified to fire and deliver headless — see §4's rationale paragraph). Transcript content is **never** consulted, same reason as D2's row above |
| **T3 variants** | the variant's provenance-trail text appears in the probe's returned context (§7.3) |

Audit events are counted only from a byte offset taken **after** the preflight probes
settle — the offset idiom from `parse_injection_events`, for the same reason (the
harness's own probes hit the same audit log).

**Pre-spend reachability probe is channel-matched per arm (DEFECT 7).** Before this
table's post-hoc gates ever run, `run_leg.py`'s `probe()` independently checks — before
any `claude -p` spend — that the planted note is actually retrievable, aborting the leg
if not (§8, "Reachability probe (all memory arms, zero quota)"). Arms B/C query the
daemon's proactive/proxy channel (`/v1/proactive`); arms D1/D2 plant a `kind="directive"`
note (§8's hook-channel delivery metadata paragraph) to fit the SessionStart channel's
content budget, and `proactive.structural_kinds`/`proactive.proxy.exclude_directive_notes`
(agent/config.yaml) both deliberately exclude "directive" from the proactive/proxy
channel — so D1/D2 judge reachability against the SessionStart channel itself
(`boot=True, hook_event="SessionStart"` against `/v1/recall`), not the proactive one. This
mirrors the same channel split as this table's own D1/D2 row (`hook_injection_counts`,
not `PROACTIVE_INJECT`) one layer earlier, before any spend.

Arm D2' (`hook-userpromptsubmit`) is a third case: `plant_note()` does **not** force it
to `kind="directive"` (it delivers via `_recall_impl`'s ordinary ranked `recall()` pass,
un-gated by note kind or trigger config — every planted note is naturally eligible
there, unlike SessionStart's boot-only call), and `/v1/proactive` is not its reachability
channel either — every non-`proxy` arm passes `--no-inject` to the scratch proxy, so even
a non-directive note visible to `/v1/proactive` in principle never actually ships through
that channel for this arm during the real session. `_user_prompt_submit_probe` issues the
identical `{"query": <leg prompt>, "hook_event": "UserPromptSubmit", "events":
["prompt-submit"]}` `/v1/recall` payload a real hook invocation sends, judging
reachability against the channel this arm actually exercises.

**Delivery-containment checks are whitespace-normalized, and JSON-escape-aware where
the haystack is raw JSON text (DEFECT 8).** Every channel that renders a note into
injected/hook context collapses it to one line first — `agent/proactive/matcher.py`'s
`_one_line()` (`" ".join(text.split())`) is deliberate product behavior, confirmed by
the anti-memory lane, not a bug to "fix" here. A `NoteVariant.content`/provenance-trail
string, however, is authored text and may legitimately contain internal newlines (e.g.
`release_via_ci`'s "verifiable" trail: a sentence, then a newline, then a `Verify: grep
...` line). A literal `x in y` containment check against the delivered text is then
structurally false even when delivery succeeded, because the harness compares an
un-collapsed authored string against a collapsed rendering of it. Every site in this
harness that asserts "was this content delivered" now collapses **both** sides before
comparing, rather than relying on the accident that today's variant strings happen to
be single-line: `run_leg.py`'s `probe()` (the `session_start_channel` reachability check
and the T3 trail-text check, §7.3 — both compare against already-JSON-decoded REST
response text, so plain whitespace-collapse suffices) and, one layer deeper,
`run_leg.py`'s `hook_preflight()` and `scorer.py`'s D1 transcript check (both compare
against **raw** text that itself embeds a JSON-encoded string field —
`_emit_hook_context`'s `print(json.dumps({"hookSpecificOutput": {...,
"additionalContext": text}}))` — where a real newline in the source content survives
JSON-escaped as the two literal characters `\n`, which whitespace-collapse alone cannot
reverse; these two sites additionally compare against the content's JSON-string-escaped
form, both sides collapsed). `run_leg.py` and `scorer.py` each carry their own copy of
this compound check (`_content_delivered_in_json_text`) rather than importing one
another's — the two files never import each other's internals (each is loaded
independently, and `run_leg.py` runs as a fresh subprocess per leg rather than an
in-process import), the same reason `_workspace_hash` and other small helpers are
duplicated rather than shared.

**Arm B (mcp/mcp-bare): `system.init`-only evidence produces false negatives
(DEFECT 13).** Headless `claude -p` emits `system.init` before its async HTTP MCP
connect settles, so an http-type server legitimately reads `status: "pending"` there
while still connecting — not evidence of a dead server. Current Claude Code headless
versions additionally defer MCP tool *schemas* behind a `ToolSearch` tool:
`mcp__vectr__*` names never appear in `system.init.tools`, even for a connected,
working server, once the agent calls `ToolSearch` and then uses the tools directly via
ordinary `tool_use`/`tool_result` pairs. Two burned k=2 legs (both verified live before
this fix, not by re-running a paid command) were marked invalid purely from these two
false negatives, plus a third, independent race: `run_leg.py`'s `/v1/recall` preflight
probe used to fire a single shot ~2s after daemon start — before the embedder finished
warming — while the same query 26s later (the agent's own in-session recall) succeeded
with `method=semantic`.

`scorer.leg_non_vacuity`'s B-row gate now has three independent legs:
1. `mcp_servers`: an entry named `"vectr"` must be present; `"connected"` passes
   outright; `"pending"` is provisionally accepted, standing or falling on evidence
   class 2 below; any other status, or no `"vectr"` entry at all, fails. Recorded as
   `mcp_server_status` in `non_vacuity`.
2. `vectr_tools_evidence` (recorded verbatim in `non_vacuity`): an evidence hierarchy
   of `"init-tools"` (the pre-fix signal, legacy shape, still sufficient alone) →
   `"tool-use"` (`_mcp_tool_use_evidence`: a real assistant `tool_use` block named
   `mcp__vectr__*` matched to a non-error `tool_result` in the transcript) →
   `"handshake"` (the harness's own pre-session `/mcp` JSON-RPC `initialize`/
   `tools/list` probe, `run_leg.py`'s `_mcp_handshake_probe`, run before `claude -p`
   even spawns — an ABORT-before-spend gate on its own, unless `--allow-unreachable`).
   None of the three present ⇒ invalid; a genuinely dead server still fails.
3. Recall delivery: `recall_probe_returned_note=False` no longer fails the leg by
   itself — `run_leg.py` now polls `/v1/recall` every 2s for up to 30s
   (`_poll_recall_probe`) rather than a single shot, closing most of the race, but a
   leftover miss only fails the leg when the transcript ALSO shows no delivery
   evidence (`_content_delivered_in_json_text` against the raw transcript, plus, when
   `note_id` is known, the literal `"[#<note_id>]"` marker).

`leg_non_vacuity` stays arm-blind about outcomes throughout: evidence class 2's
hierarchy records WHICH signal fired, never which one the agent chose to call —
tool-usage counts are diagnostic, not gating, beyond "did at least one class fire".
`revalidate.py` recomputes this gate at $0 against a preserved leg's `result.json` +
`transcript.jsonl` (no daemon/network/model call), writing `result.revalidated.json`
as a new sibling file — `result.json` itself is never mutated; see that script's own
module docstring for the full contract (mirrors `rescore.py`'s DEFECT 9 pattern, but
for the non-vacuity gate instead of the outcome verdict).

**Pre-spend reachability is a by-id integrity check, not a channel-ranking check, for
arms B/C (UPG-EVAL-PLANT-DISPLACEMENT).** A live *k*≥2 leg of S1 (`release_via_ci`, arm
mcp) aborted pre-spend ($0) because the agent's OWN note from a prior leg — an anchored,
`kind="gotcha"`, `priority="high"` note with real file anchors — legitimately outranked
the unanchored planted directive at the proactive channel's default item budget
(`items=1`). Turn 2 of `_proactive_probe` returned `anchor_ids=["note:2"]`,
`planted_present=false`. **This ranking is the product working as intended**: an
agent-authored, anchored, high-priority note winning a scarce proactive slot over an
unanchored planted one is exactly what `ProactiveGate.select()`'s deterministic sort is
for. Treating that outcome the same as "the note is gone" was the actual defect —
every mcp/proxy trajectory whose agent writes a strong note during leg *k* would
otherwise self-terminate at leg *k+1*, silently truncating exactly the trajectories most
worth measuring (the ones where the agent is doing real work with memory).

The fix separates two questions the old single proactive-channel probe conflated:
*is the note retrievable at all* (reachability — an instrument concern) vs. *does it win
the default-budget slot right now* (delivery under contention — a genuine, scenario-
dependent property of this run). `run_leg.py`'s `probe()` now gates non-hook memory arms
(B, C) on a **direct by-id existence/integrity check** (`_note_by_id_probe`, `POST
/v1/recall` with `note_id` set — bypasses ranking entirely, checks only that the note
exists and its content matches what was planted) as the PRIMARY reachability test; the
proactive-channel probe (`_proactive_probe`, still run and recorded) is corroborating
diagnostics only for these two arms. Hook arms (D1/D2) are unaffected — DEFECT 7 already
routes their reachability gate through the SessionStart channel, and a `kind="directive"`
note is structurally excluded from the proactive channel's ranking in the first place
(§4.1's DEFECT 7 paragraph above), so this specific displacement cannot occur there.

When the by-id check passes but the note is absent from the channel's default-budget
response, `_proactive_rank_probe` locates the note's actual rank by paging the proactive
channel with a synthetic, per-probe `session_id`: since an already-selected `anchor_id`
is cooldown-suppressed on the next call to the same session (the existing product
cooldown-ledger mechanism, not new product behavior), repeated calls reveal successive
ranked batches up to a cap of `min(notes_count_at_start, 10)` calls. The leg then RUNS
(never aborts) and records four additive fields, written into both `preflight.json` and
`result.json`:

- `planted_rank` — the note's 1-based rank on the channel, or `null` if never found
  within the probed cap.
- `displaced_by` — `[{note_id, kind, priority, anchored}, ...]` for every note that
  ranked above it, in rank order.
- `delivered_at_default` — `true` when the note was present at the default budget
  (`items=1`, i.e. rank 1) without needing the rank probe at all.
- `channel_delivery` — `"delivered_default"` / `"displaced"` / `"unreachable"`, a
  pass-through summary consumed by `scorer.leg_non_vacuity`'s arm-C branch: a leg
  annotated `"displaced"` is exempted from that branch's own planted-anchor-injection
  expectation (§4.1's C row above), because the same contention observed at preflight is
  expected to persist into the real session — re-invalidating it there would just be the
  same false abort moved one step later. No other arm-C gate is affected, and no other
  arm ever reads this field.

**ABORT is now reserved for genuine unreachability only:** the by-id check finds the
note absent or content-corrupt (`preflight["reachable_channel"] == "by_id"`), or every
probe path fails at the transport level — daemon unreachable, or the embedder never
comes up within the polling window (`preflight["infra_unreachable"] = true`, true only
when the by-id probe AND both proactive-probe turns all fail at the transport layer, not
merely return an empty/negative result). `--allow-unreachable` now only bypasses this
narrowed genuine-unreachability abort; it has no bearing on `channel_delivery=
"displaced"`, which never aborts regardless of the flag.

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

Stored at `<runs-dir>/_shared/leg1/<run-dir>/`, where `<run-dir>` is an opaque,
hash-derived name (`run_plan.py`'s `_shared_leg1_dir`/`_opaque_run_dir_name`) rather
than the literal `<scenario>-s<seed>` — see 5.4's contamination-prevention note for why
(UPG-EVAL-PATH-SLUG-LEAK). A pre-fix campaign's existing `<scenario>-s<seed>`-named
directory is reused as-is rather than renamed. Contents:
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
- Every workspace's git identity is pinned to a synthetic pair (`scenarios.py`'s
  `pin_synthetic_git_identity`, repo-local `git config`) at materialization and again at
  every leg's restore, and the agent subprocess's `GIT_AUTHOR_*`/`GIT_COMMITTER_*` env
  vars are stripped and reset to the same pair (`run_leg.py`'s `_spawn_env_for_agent`).
  Otherwise an agent's own `git commit` falls through to the operator's real global
  `~/.gitconfig`, and the operator's name/email end up baked into a scenario's git log —
  a real leak this harness had until UPG-EVAL-PATH-SLUG-LEAK's PII-hygiene fix.

**Contamination prevention across arms/variants/seeds.** Trajectory id
`<scenario>-<arm>-<variant>-s<seed>` is unique per trajectory and appears in the result
records (`state.json`, `results.jsonl`), so nothing on the metrics side is shared across
cells. Nothing on the workspace side is shared either, except the read-only leg-1 tar.
As of UPG-EVAL-PATH-SLUG-LEAK the trajectory id is *not* used verbatim as the on-disk
directory name any more: the DB dir, the workspace path, and the run dir under
`<runs-dir>` are all named from an opaque hash of the trajectory id
(`run_plan.py`'s `resolve_traj_dir`), so an agent's own `cwd` cannot read its scenario,
arm, or note variant off its own filesystem path — a real failure mode this harness
originally had (S5's told-scenario agent inferring "this environment is explicitly
testing reconciler behavior" from a slug-named cwd with zero corroborating file
content). The mapping from opaque dir back to trajectory id lives only in
`state.json`'s `run_dir`/`trajectory_id` fields and `results.jsonl`, which the agent
never reads. Pre-fix campaigns' slug-named directories are left in place unchanged
(never renamed) and continue to resolve correctly. Verify scripts are materialized
**outside** the workspace, after the agent exits (otherwise the no-memory arm could
read the answer). A test asserts that no scenario file and no leg-≥2 prompt contains
any `fact_token`.

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

**Mistake/acquisition signatures are matched at genuine command-execution positions,
not anywhere in a Bash command string (DEFECT 9).** `BashAction`'s `pattern` (S1/S3/S4/S5's
`mistake_signature`/`fact_acquisition`, and the trap harness's `CommandRan`) was matched
with an unanchored `re.search` against the whole command string, so a read-only mention —
`cat deploy.sh`, `grep -n foo deploy.sh`, `less deploy.sh` — satisfied the same pattern as
a genuine execution (`./deploy.sh`, `bash deploy.sh`). Live evidence from the 2026-07-29
campaign (`/Users/swapnanilsaha/vectr-eval-runs/longitudinal-s0/`): every S5 leg scored
`mistake_committed=true` also scored `deploy_state_untouched=PASS` (the deploy.sh-mutated
file's sha256 unchanged from the leg's own start-of-leg baseline) — the scorer's own two
checks contradicted each other in every affected leg, and no leg in the whole campaign
ever actually ran `deploy.sh`. Fixed by `exec_anchor=True` on the signature declaration
(`BashAction(pattern, exec_anchor=True)` — a property of the individual signature, not
global matching behavior; the trap harness's `CommandRan` carries the same field), matched
via `re.match` at each command's genuine execution boundary (`_matches_at_exec_position`,
`scorer.py`) after stripping leading whitespace and interpreter/env-assignment prefixes
(`env FOO=bar`, `timeout 30`, `sh -c`, `FOO=bar BAZ=qux`) so `./deploy.sh`, `bash deploy.sh`,
and a chained `cd x && ./deploy.sh` still match while `cat`/`grep`/`less`/`stat`/`echo`
mentions do not. Every S1/S3/S4/S5/S6 `mistake_signature`/`fact_acquisition` `BashAction`
and the trap harness's `CommandRan` uses were re-audited for the same class and anchored
where the semantic is "the agent performed the action"; one deliberate exception is
documented in-line in `scenarios.py` (a signature whose own text makes anchoring provably
unnecessary — see the DEFECT 9 audit comment there, not repeated here). A **contradiction
guard** (`scorer.detect_contradictions`) now runs alongside every leg's score: when a
mistake signature fires but the scenario's own state-mutation sub-check (a leg's
`mistake_state_check`, e.g. S5's `deploy_state_untouched`) shows no mutation, the leg's
`score.contradictions` carries a machine-readable `mistake_action_without_state_mutation`
entry, surfaced by `report.py`'s human-readable output — loud, never silently resolved one
way or the other, because a contradiction is itself evidence the signature (or the
state check) needs authoring attention, not something the harness should guess through.
`benchmarks/longitudinal_rediscovery/rescore.py` re-scores every preserved leg's
`transcript.jsonl`/`baselines.json`/end-of-leg workspace snapshot against today's scorer
at $0 (no agent, no daemon, no new spend), writing `result.rescored.json` beside each
untouched original — re-run over the 2026-07-29 campaign, every TOLD S5 leg's
`mistake_committed` flips `true → false` and `mistake_repetition_rate` becomes `null`
throughout (no leg 1 committed the error post-fix, so "repetition" is undefined); the
DISCOVERED scenario's (S1) metrics are byte-identical before and after, as expected for a
fix that only touches Bash-command-string matching.

**DEFECT 10 (RESOLVED, direction 1, user decision 2026-07-30 — vectr note #680):** the
open question below this paragraph in earlier revisions — what a k>=2 leg's *own*
end-of-leg snapshot should be built from, when the trajectory-root `workspace/`
directory's cross-leg residue can make a later leg's check vacuous — is resolved as a
**per-leg reset of scenario-declared critical residue**, not a change to the snapshot
mechanism itself. `LongitudinalScenario.critical_residue_paths` (a tuple of
workspace-relative paths, `scenarios.py`) names files whose content a completed leg k-1
may leave in a state that pre-satisfies a LATER leg's own primary check or fact — two
cases exist in the current six scenarios: S5's `deploy/queue.yaml` (every leg deploys to
the same target) and S6's `RESULTS.md` (UPG-EVAL-S6-LEG4-VACUOUS: §3's session 4
re-measures session 1's algorithm and `boxrun.sh` is deterministic, so a compliant leg 4
reproduces leg 1's end state byte-for-byte and the traceability script exits 0 on leg 1's
row alone — the DEFECT 10 coder lane's audit examined legs 1-3 only and recorded S6 as
needing no reset, which was wrong at leg 4). S1-S4 each target a distinct
artifact/value per leg, so no leg's residue can pre-satisfy a later one there.
`run_leg.py`'s
`LegRunner._apply_critical_residue_reset()` restores exactly those declared paths to
their `files` seed content at the START of every k>=2 leg — AFTER `_restore_and_verify`'s
manifest integrity check (so DEFECT 9-adjacent tar-fidelity verification still covers the
raw, un-reset restore) and before the agent runs — then recomputes
`leg_start_baselines` from the reset tree so `baselines.json` and every
`FileUnchanged`/`FileMutated` check reflect what the agent actually sees. Every other
path, declared or not, keeps its natural cross-leg residue: `snapshot()`'s own mechanism
(the end-of-leg tar/manifest a leg's compliant work produces) is unchanged, and the reset
is layered on top of the NEXT leg's restore, not on the snapshot that feeds it. As a
direct consequence, S5's `queue_gained_staging_entry` check (§6.6) changes from a
per-leg CUMULATIVE minimum (1, 2, 3, 4 staging entries) to a uniform `minimum=1`: since
every leg now starts from the same zero-staging-entries seed, any staging entry present
at leg-end was necessarily added THIS leg, so a flat minimum=1 measures the leg's own
addition rather than accumulated history — still an `AllOf` pairing an engagement half
(`FileMatchCountAtLeast`, minimum=1) with a restraint half (`CommandRan(..., want=False)`
+ `FileUnchanged`), arm-blind and mechanical throughout. This fix is **not retroactive**:
the preserved trajectories under `/Users/swapnanilsaha/vectr-eval-runs/longitudinal-s0/`
are not re-generated or re-scored under it — the 2026-07-29 campaign's S5 TOLD data
remains null/invalid exactly as DEFECT 9's `rescore.py` pass already left it (leg 1
components no longer commit the error, so `mistake_repetition_rate` is `null`
throughout); a fresh run under this fix is required to produce a discriminating S5
result. `critical_residue_paths` is a narrower, opt-in complement to the general
residue rule (§2.2) and to `ignore_paths`, not a replacement for either.

S6's `RESULTS.md` entry is non-retroactive on the same terms, and no recorded artifact
is affected in any case: at the time of that fix the slope tier (§9 T5) extended the
T1+T2 trajectories, which are S1 and S5 only, so **no tier as then defined ran S6's leg
4** — every scenario authors four legs per §3, but only the two headline scenarios were
ever run past leg 3. S6's leg-4 defect was therefore latent, not present in any campaign
output, and remains absent from every artifact recorded before this document's §9
widening. That widening has since been taken as its own cost-model decision
(UPG-EVAL-S6-LEG4-UNREACHABLE, 2026-08-03): **T5 now also extends S6's two T4
trajectories**, so the reset above is exercised by a tier and not only by unit tests.
`tests/test_longitudinal_plan.py::test_leg_reachability_by_tier_is_explicit` pins the
per-scenario depth so the choice — then and now — stays visible.

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
  _shared/leg1/<run-dir>/              workspace.tar manifest.sha256 baselines.json
                                        transcript.jsonl result.json leg1_id
  <run-dir>/
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

As of UPG-EVAL-PATH-SLUG-LEAK, `<run-dir>` is an opaque, hash-derived name
(`run-<16 hex chars>`, `run_plan.py`'s `resolve_traj_dir` / `_shared_leg1_dir`) rather
than the literal `<scenario>-<arm>-<variant>-s<seed>` / `<scenario>-s<seed>` — the
directory an agent's own `cwd` resolves under must not spell out its scenario, arm, or
note variant. `state.json`'s `trajectory_id` field still records the full mapping, and
`run_dir` records which directory holds it, for harness-side tooling only. A directory
already on disk under the old literal-slug name (a pre-fix campaign) is reused as-is,
never renamed — `resolve_traj_dir` checks for the legacy directory first.

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

**Hook-channel delivery metadata (D1/D2 only).** Note-kind and trigger eligibility
for the SessionStart/PreToolUse hook channels are **plant-time delivery
configuration**, distinct from the note's advisory text: `NoteVariant.content`/
`title` in `scenarios.py` are byte-identical across every arm (channel parity —
what varies is only how the memory layer is configured to deliver the same
advisory). `run_leg.py`'s `plant_note()` sends every variant's own
`trigger_paths` as explicit path-only triggers, which — because an explicit
`triggers` list on a note fully replaces (never merges with) its `kind`'s default
trigger bundle — never fire on the SessionStart/boot delivery path (no
`file_path` is ever supplied there, so a path-bearing trigger cannot match,
independent of `kind`). For arms `hook-sessionstart`/`hook-full` only,
`LegRunner._apply_hook_delivery_metadata()` appends an explicit
`{"event": "session-start"}` trigger to the same list (restoring eligibility)
and switches `kind` from `gotcha` to `directive` — not because the trigger fix
alone was insufficient to fire, but because `gotcha`'s per-kind injection token
budget (100 tokens) is smaller than every scenario's real full-text render
(101–181 tokens measured across all 16 variants), so a fired gotcha-kind note
would still silently degrade to its index-tier, title-only line; `directive`'s
400-token budget clears every case. Non-hook arms (`none`/`mcp`/`mcp-bare`/
`proxy`) are unaffected — this is a no-op for them, and `scenarios.py`'s 16
`NoteVariant` sites are never edited. Arm `hook-userpromptsubmit` is ALSO a
no-op here despite being a hook arm: it delivers via `_recall_impl`'s
ordinary ranked `recall()` pass (§4's rationale paragraph), which is never
trigger-gated and never kind-budget-capped the way the SessionStart boot
branch is, so the planted note ships with its scenario-authored `kind`/
`triggers` unmodified for this arm.

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
| **T5** slope | leg 4 on every T1+T2 trajectory **and on S6's two T4 trajectories** — does the saving persist? | 10 | $2.40 | $3.50 |
| **T6** D2 *(conditional)* | full hook set, only with a `verified:true` attestation | 4 | $0.96 | $1.40 |
| **T7** replication | seed 1 of T1 | 10 | $2.62 | $3.80 |

Cumulative: **T0+T1 = $2.62 mean / $3.80 worst** (the headline tier, comfortably inside
the <$5 constraint on its own); through T4 $11.22 / $16.30; through T7 $17.20 / $25.00.
(Through-T7 moves by +$0.48 mean / +$0.70 worst for T5's two added S6 legs. Both totals
are now the exact sums of the rows above; the previous worst-case figure of $23.70 was
$0.60 below its own row sum even before this change.)

**T5 scope (widened 2026-08-03, UPG-EVAL-S6-LEG4-UNREACHABLE).** T5 originally extended
the T1+T2 trajectories only — S1 and S5 — so **no tier ran S6's leg 4**, and §6.5's
`critical_residue_paths` fix (the thing that makes that leg non-vacuous at all) was
exercised by unit tests but by no campaign cell. T5 now also extends S6's own two T4
trajectories (arms A and C, `bench_box_only`), reusing those exact trajectory
identities: legs 1–3 are already recorded, so the widening buys **2 legs, +$0.48 mean /
+$0.70 worst**, on the same per-leg basis as every other row. S2/S3/S4 still author a
leg 4 that no tier runs; `tests/test_longitudinal_plan.py::
test_leg_reachability_by_tier_is_explicit` pins the per-scenario deepest leg, so any
further widening is again a visible, reviewed edit.

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
| N=3 is a short "longitudinal" | T5 adds leg 4 on the headline trajectories and on S6, and reports the slope |
| Single seed at headline | T7 replicates; until then, per-cell results are reported raw, never as means |
| Small synthetic workspaces never compact | acknowledged: this measures session-boundary survival only, and therefore **understates** the channel |
| Prior strength assumed | it is measured: `mistake_committed(1)` in arm A; `weak_prior:true` flags a scenario for replacement |
| Arm B has extra files (vectr's guidance) | every scenario ships a neutral `CLAUDE.md` so all arms have one; vectr-written files are in `ignore_paths` |
| Agent nondeterminism dwarfs the effect | the effect sizes targeted are large (a mistake repeated vs not); cost figures are never a pass/fail signal, exactly as in the trap harness |
| S5 fixture leaked its prescriptive half pre-DEFECT-12 | "Fixture surface re-baseline (DEFECT 12)" above (end of §3); trajectories are uniform pre- or post-fix per their own leg-1 date, never mixed within one trajectory |

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
