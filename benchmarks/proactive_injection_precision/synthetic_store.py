"""Synthetic note-store generator for the proactive structural-channel
precision harness (UPG-PROXY-INJECT-PRECISION).

Builds a self-contained, ~300-note store with a realistic kind mix (most
notes are kind="task" in-progress chatter, matching a real dev session) and
plants known-ground-truth notes against 20 synthetic "window" files. Ground
truth is a property of THIS GENERATOR, not an automated relevance judge run
at harness time — the harness classifies whatever the real API actually
returns by looking each returned note id up in the dict this module hands
back, never by re-deriving relevance from content.

Recreates the exact starvation mechanism the P0/precision audit diagnosed:
`recall_for_path()`'s own SQL ordering exempts kind="task" notes to a
constant (author_trust_score, decay_score) = (1.0, 1.0), so a freshly
written task note ties every other freshly written note on those columns
and the tie breaks on `created_at DESC, note_id DESC` — i.e. plain recency.
Each window plants its few real (gold) notes FIRST, then several kind="task"
chatter notes AFTER (so they are more recent) — under the OLD undersized
`limit` passed into `recall_for_path()` this pushes every gold note past the
truncation cutoff before the kind-eligibility filter ever runs; under the
new overfetch multiplier the whole matching set fits and the filter can do
its job.
"""
from __future__ import annotations

from dataclasses import dataclass, field

WINDOW_COUNT = 20
TASK_NOISE_PER_WINDOW = 7  # > OLD limit (max_items_per_event * 2 == 6, see
                            # config.yaml's proactive.max_items_per_event),
                            # < NEW limit (min(3 * 4, 60) == 12) together with
                            # the 4 gold/weak notes below (7 + 4 == 11 <= 12).
FILLER_COUNT = 80  # bulk, unrelated to any window file — realistic store size
FILLER_TASK_FRACTION = 0.75  # most filler is task chatter too, matching a
                              # real dev session's kind mix.

REVOKED_WINDOWS = (0, 1)  # a couple of windows get their Tier-A gold note
                           # revoked after the fact, so the harness has real
                           # deterrent-rendered items to count (not just
                           # assertions) — see the assertions-vs-deterrents
                           # split in run_harness.py.

_ELIGIBLE_ROTATION = ("gotcha", "finding", "decision", "operational", "reference")


@dataclass
class GroundTruth:
    """What the generator planted, keyed by note_id, plus per-window lookups.

    `category` values: "gold" (true positive under strict AND charitable),
    "weak_offtopic" (eligible kind, genuine structural match, but the
    content is not actually about this window's file -- false positive
    under strict, true positive under the looser charitable credit),
    "task_noise" (kind="task" chatter -- false positive under both; this is
    exactly what lever 1 must exclude), "filler" (not tied to any window,
    should never be returned for a window query at all).
    """

    category_by_id: dict[int, str] = field(default_factory=dict)
    kind_by_id: dict[int, str] = field(default_factory=dict)
    window_by_id: dict[int, int] = field(default_factory=dict)
    revoked_ids: set[int] = field(default_factory=set)
    window_files: list[str] = field(default_factory=list)
    window_basenames: list[str] = field(default_factory=list)
    total_notes: int = 0
    task_kind_notes: int = 0


def build_synthetic_store(svc) -> GroundTruth:
    """Populate `svc` (a memory-only VectrService) and return the ground
    truth the harness scores injections against. Deterministic: same input
    service, same notes, same ids, every run."""
    gt = GroundTruth()

    for i in range(WINDOW_COUNT):
        basename = f"module_{i:02d}.py"
        window_path = f"/repo/src/{basename}"
        gt.window_files.append(window_path)
        gt.window_basenames.append(basename)
        eligible_kind = _ELIGIBLE_ROTATION[i % len(_ELIGIBLE_ROTATION)]

        # Tier A: declared anchor, genuinely on-topic, eligible kind.
        nid = svc.remember(
            f"{basename}: the connection-pool ceiling here is capped at "
            f"{20 + i} concurrent handles, tune via POOL_CAP_{i:02d}.",
            kind=eligible_kind,
            anchors=[basename],
        )
        gt.category_by_id[nid] = "gold"
        gt.kind_by_id[nid] = eligible_kind
        gt.window_by_id[nid] = i
        if i in REVOKED_WINDOWS:
            gt.revoked_ids.add(nid)
            svc.revoke_note(nid, reason="the cap moved to a runtime config value")

        # Tier B: content-mention only, kind="gotcha", genuinely on-topic.
        nid = svc.remember(
            f"while debugging {basename} we found the retry lock is not "
            f"released on an early return -- watch for that.",
            kind="gotcha",
        )
        gt.category_by_id[nid] = "gold"
        gt.kind_by_id[nid] = "gotcha"
        gt.window_by_id[nid] = i

        # Tier C: content-mention only, eligible kind, genuinely on-topic
        # (weaker evidence than A/B, but still actually about this file).
        nid = svc.remember(
            f"a note about {basename}: the default timeout there was "
            f"raised from 5s to {10 + i}s last sprint.",
            kind="finding",
        )
        gt.category_by_id[nid] = "gold"
        gt.kind_by_id[nid] = "finding"
        gt.window_by_id[nid] = i

        # Tier C: content-mention only, eligible kind, but OFF-topic -- the
        # basename appears only incidentally in a note that is really about
        # something else (a batch refactor note listing touched files).
        # `other_a`/`other_b` deliberately use a namespace disjoint from
        # "module_NN.py" (the real window basenames) -- reusing that pattern
        # here would make window i's off-topic note ALSO substring-match
        # whichever other windows' basenames happen to land on it, silently
        # inflating THEIR recall_for_path() pool by notes that have nothing
        # to do with this window and were never accounted for in this
        # module's noise budget (see TASK_NOISE_PER_WINDOW below).
        other_a = f"legacy_{i:02d}_a.py"
        other_b = f"legacy_{i:02d}_b.py"
        nid = svc.remember(
            f"refactor touched {other_a}, {basename}, {other_b}; no "
            f"behavior change intended, just import cleanup.",
            kind="finding",
        )
        gt.category_by_id[nid] = "weak_offtopic"
        gt.kind_by_id[nid] = "finding"
        gt.window_by_id[nid] = i

        # kind="task" chatter, created LAST (most recent) so recall_for_path's
        # recency tie-break puts these ahead of the gold/weak notes above --
        # the exact starvation mechanism lever 1b's overfetch must survive.
        for j in range(TASK_NOISE_PER_WINDOW):
            nid = svc.remember(
                f"still working through {basename}, not done yet "
                f"(session {j}) -- will circle back after the current fix.",
                kind="task",
            )
            gt.category_by_id[nid] = "task_noise"
            gt.kind_by_id[nid] = "task"
            gt.window_by_id[nid] = i

    n_filler_task = int(FILLER_COUNT * FILLER_TASK_FRACTION)
    for k in range(FILLER_COUNT):
        other_basename = f"other_{k:03d}.py"
        if k < n_filler_task:
            nid = svc.remember(
                f"still triaging {other_basename}, nothing conclusive yet.",
                kind="task",
            )
            kind = "task"
        else:
            kind = _ELIGIBLE_ROTATION[k % len(_ELIGIBLE_ROTATION)]
            nid = svc.remember(
                f"{other_basename}: unrelated background finding, not tied "
                f"to any of the 20 measured windows.",
                kind=kind,
                anchors=[other_basename],
            )
        gt.category_by_id[nid] = "filler"
        gt.kind_by_id[nid] = kind
        gt.window_by_id[nid] = -1

    gt.total_notes = len(gt.category_by_id)
    gt.task_kind_notes = sum(1 for k in gt.kind_by_id.values() if k == "task")
    return gt
