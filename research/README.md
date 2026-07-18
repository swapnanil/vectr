# Research artifacts: brain memory for coding agents

Run archives and analysis for the cue-anchored working-memory research
("Brain Memory for Coding Agents: Cue-Anchored Working Memory as a
Harness Property" — `brain-memory/delivery-not-storage.md`).

## Layout

- `proactive-gate/` — the evaluation harness and all run archives.
  - `protocol.md`, `bm4-protocol.md` — controlled-matrix protocol
    (arms A/B/C/CS/H/V) and per-arm verdicts.
  - `decay-protocol.md`, `decay-probe.sh`, `decay-files.txt`,
    `decay-seeds.jsonl` — the forced-compaction decay probe (arms N/M),
    including the complete run log with every aborted or invalidated
    launch and its diagnosis.
  - `results/<run-id>/` — one directory per launch: transcripts,
    session files, launch-posture assertions, graders' outputs, audit
    reports. Directories suffixed `-INVALID-*` or `-ABORTED-*` are
    retained failed launches; the protocol run logs explain each. The
    two gated decay runs are `decay-N-20260717-030703` and
    `decay-M-20260717-192249`.
- `brain-memory/` — the paper, the re-exploration analyzer
  (`bm5/analyzer.py`), and the measurement note and data it produced.

## Sanitization

These archives contain full agent transcripts. Before publication, the
following privacy rewrites were applied uniformly (byte-level, order as
listed); no other content was altered:

1. corporate Maven-mirror hostnames → `redacted.example.com`, and one
   corporate directory subtree → `/home/user/redacted`
2. the operator's home directory (all forms, including regex-escaped
   and display-truncated variants) → `/home/user`
3. the operator's local username in captured `ls -l` owner columns →
   `user`
4. harness-encoded project-directory names containing the above →
   equivalently rewritten

`maven-settings.xml` (a corporate mirror configuration used only to
speed dependency resolution during runs) is excluded from the copy;
builds reproduce with any standard Maven mirror.

## Compression

Files larger than 1 MB (transcripts and session files) are
gzip-compressed in place (`.gz`); `gunzip -k <file>.gz` restores them.
Graders' outputs, protocols, and reports remain plain text.
