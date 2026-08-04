# Longitudinal rediscovery: run directory

This is the run directory for the longitudinal rediscovery evaluation. Every table, quote, and
count in the accompanying paper derives from these files. The harness that produced it lives in
`benchmarks/longitudinal_rediscovery/` in this repository; `DESIGN.md` there defines the
scenarios, arms, variants, defect record, and scoring.

## Layout

- One directory per trajectory. Early trajectories are named `<scenario>-<arm>-<variant>-<seed>`.
  Trajectories run after the workspace-path fix (DEFECT 11 in `DESIGN.md`) use opaque
  `run-<16 hex>` names; resolve them by reading the `trajectory_id` field of the `state.json`
  inside.
- Each trajectory holds `legs/<k>/artifacts/` with `result.json`, a corrected
  `result.rescored.json` written alongside where a re-score occurred (originals are never
  overwritten), `transcript.jsonl`, `preflight.json`, and a per-leg `end-state.tar` workspace
  snapshot. The trajectory-level `workspace/` is the live carried-forward workspace.
- Leg 1 is shared per scenario and seed and lives under `_shared/leg1/`, together with the
  scenario definition used for the run.
- Top-level `results.jsonl` and `results.rescored.jsonl` aggregate the trajectory legs; the
  shared first sessions keep their records under `_shared/leg1/`.
- Superseded and invalidated trajectories are retained beside the live ones, with the
  invalidation reason carried in the directory name.

## Substitutions made for release

This is a copy of the private run directory with three substitutions; everything else is
byte-identical.

1. A real requester email address was replaced with `mary.doe@example.com` everywhere it
   appears: scenario fixtures, transcripts, archives, and the commit and tag identities of the
   git repositories inside workspace snapshots. Those repositories were rewritten with
   `git fast-export` and `git fast-import`, so commit and tag ids inside released workspaces
   differ from the ids quoted in transcripts. Recorded content hashes (`manifest.sha256`,
   baselines, end-state manifests) verify against the private originals, not against this copy.
   Archives containing affected files were rebuilt member for member; member timestamps are
   preserved, ownership metadata is not.
2. A local editor hook path and a plugin reference captured in session events were replaced with
   `/redacted/session-end-hook` and `/redacted/plugin`.
3. Git metadata directories inside on-disk workspace snapshots are shipped renamed from `.git`
   to `_git` so that this repository does not treat them as embedded repositories. To restore
   them for local inspection: `find . -type d -name _git -execdir mv _git .git \;`
   Archives keep the `.git` name internally.
