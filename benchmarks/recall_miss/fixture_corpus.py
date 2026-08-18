"""Small, committed, sanitized fixture for the recall-miss harness
(UPG-RECALL-MISS-FLOOR, part (a)).

Two ingredients, kept explicitly separate so the harness's own correctness
can be pinned in CI without depending on a live database or a live model
download:

  - `NOTES`: a synthetic note corpus, ~30 notes spread across all 7
    `VALID_KINDS`. Every note body below was AUTHORED FOR THIS FIXTURE — it
    illustrates the general shape of a vectr working note (the same shape
    documented in CLAUDE.md/spec.md) but is not a copy of any real note
    content, so nothing private ships in this file.

  - `FIXTURE_QUERIES`: hand-labeled (query, relevant note keys, basis)
    triples. A subset of the query TEXT (never note content) is adapted
    from real `vectr_recall` tool calls mined from this workspace's own
    session transcripts (`~/.claude/projects/**/*.jsonl` — see
    `mine_prompts.py`), each `source="mined-transcript"` and each paired
    HERE with a freshly authored note that genuinely answers it (labels are
    never derived from a retriever's own output — see harness.py's
    docstring for why that would be circular). Queries with personal names
    or workspace-identifying detail from the mined sample were left out
    rather than sanitized-by-redaction, to keep the fixture auditable at a
    glance.

Every label's `basis` is a plain-English sentence a reviewer can check
against `NOTES` directly — this file IS the labeling record, not a pointer
to one.
"""
from __future__ import annotations

from harness import LabeledQuery, NoteInfo

FIXTURE_WORKSPACE = "/fixture-repo"

# ---------------------------------------------------------------------------
# Note corpus. Each entry: key -> (kind, content, tags).
# `key` is a stable label used by FIXTURE_QUERIES below; real note_ids are
# only assigned at insert time (see build_fixture_corpus()).
# ---------------------------------------------------------------------------
NOTES: dict[str, tuple[str, str, list[str]]] = {
    # --- directive ---------------------------------------------------
    "directive_one_shot_explore": (
        "directive",
        "Explore a codebase freely only once per area, never re-explore it: "
        "after the first pass, reuse what was stored in working memory instead "
        "of re-reading the same files. Treat exploration state like a "
        "git-style branch — recall, don't repeat.",
        ["exploration", "memory-reuse"],
    ),
    "directive_no_query_heuristics": (
        "directive",
        "Never add query-content keyword or regex logic to the retrieval path "
        "— no classifying, rerouting, or gating on what an incoming query "
        "says. Matching on a note's own declared metadata (tags, anchors, "
        "kind) is a different, allowed category.",
        ["retrieval", "standing-rule"],
    ),
    "directive_run_tests_venv": (
        "directive",
        "Always run the test suite with the project's own virtualenv "
        "interpreter, never the system python — the system interpreter is "
        "missing optional compiled dependencies and produces false failures.",
        ["testing", "environment"],
    ),
    "directive_commit_style": (
        "directive",
        "Commit messages stay factual and technical only — no narrative or "
        "process storytelling, no emoji.",
        ["commits", "style"],
    ),

    # --- task ----------------------------------------------------------
    "task_notes_chroma_vector_store": (
        "task",
        "UPG-NOTES-CHROMA (P1): give the notes store its own vector "
        "collection separate from the code index, so note embeddings and "
        "code-chunk embeddings never share one ChromaDB collection. Task "
        "definition drafted; migration path for existing notes still open.",
        ["notes", "chroma", "upg"],
    ),
    "task_discovery_memoization_l3": (
        "task",
        "Discovery memoization L3 boundary: distillation of open items, "
        "episode capture, and arc-detection status. Remaining open items: "
        "confirm distillation dedups against an already-distilled arc, and "
        "decide the retention window for undistilled episodes.",
        ["memoization", "l3", "episodes"],
    ),
    "task_vectr_release_pypi": (
        "task",
        "vectr release checklist: PyPI publish, MCP directory/registry "
        "submission, version bump. PyPI package builds clean; registry "
        "submission form still needs the maintainer contact field filled in.",
        ["release", "pypi", "publish"],
    ),
    "task_vscode_extension_publish": (
        "task",
        "VS Code extension publish workflow is currently disabled: the VSCE "
        "personal access token needs to be re-issued in Azure DevOps before "
        "the marketplace publish step in CI can run again.",
        ["vscode", "publish", "ci"],
    ),

    # --- gotcha ----------------------------------------------------------
    "gotcha_root_cause_findings": (
        "gotcha",
        "When a bug is found while implementing something else, fix the "
        "root cause in the product or file it as its own task — never "
        "silently route around it in the code path you happen to be "
        "touching.",
        ["root-cause", "process"],
    ),
    "gotcha_pragma_optimize_restart": (
        "gotcha",
        "Run `PRAGMA optimize` on a FRESH connection after a large ANALYZE, "
        "not the connection that just finished writing — a restart is "
        "needed before query-planner statistics from ANALYZE take effect on "
        "extra indexed roots.",
        ["sqlite", "pragma", "performance"],
    ),
    "gotcha_worktree_index_artifact": (
        "gotcha",
        "A coder's worktree lives under a hidden `.claude/worktrees/` "
        "directory; the indexer refuses to index anything under a "
        "dot-prefixed path, so fixtures that index the checkout root come "
        "back empty inside a worktree. Verify the real gate on the main "
        "checkout, not the worktree.",
        ["worktree", "indexing", "ci"],
    ),
    "gotcha_stale_flag_repeat": (
        "gotcha",
        "`stale_flagged` can fire repeatedly on the same note without ever "
        "escalating or resolving if the anchor keeps drifting — watch for a "
        "note with a long flag history before trusting its 'still valid' "
        "framing.",
        ["staleness", "anchors"],
    ),

    # --- finding -----------------------------------------------------
    "finding_reword_internal_process_refs": (
        "finding",
        "Docstrings and comments in product code should never reference "
        "internal build-process concepts like lane names or coder "
        "codenames — those belong in process docs, not in shipped comments.",
        ["hygiene", "comments"],
    ),
    "finding_chunking_markdown_headers": (
        "finding",
        "Search ranking was over-favoring tiny markdown heading-only chunks "
        "over real code — a low-information chunk with a matching header "
        "term scored higher than the implementation it was heading for.",
        ["chunking", "search", "ranking"],
    ),
    "finding_embedding_dilution_gap": (
        "finding",
        "A symbol whose docstring paraphrases the query can still be absent "
        "from the retrieved pool because the embedding of its full body "
        "dilutes the docstring's own similarity to the query — a pool-entry "
        "problem, not a ranking problem.",
        ["embeddings", "retrieval"],
    ),
    "finding_call_edges_leaf_only": (
        "finding",
        "Call-graph edges recorded at index time are leaf-call only, so "
        "centrality scoring cannot distinguish two same-named leaf methods "
        "on different classes without also resolving the receiver type.",
        ["call-graph", "centrality"],
    ),

    # --- reference ---------------------------------------------------
    "reference_spec_doc_location": (
        "reference",
        "The authoritative spec document lives at tools/vectr/docs/spec.md "
        "(not the short stub at tools/vectr/spec.md) — always read the "
        "docs/ path before citing spec behavior.",
        ["spec", "docs"],
    ),
    "reference_acceptance_corpus": (
        "reference",
        "The offline retrieval acceptance corpus is "
        "benchmarks/acceptance/product_cases.jsonl, run by the product "
        "reviewer against a live daemon over REST; adoption cases live "
        "alongside it in adoption_cases.md.",
        ["acceptance", "benchmarks"],
    ),
    "reference_glama_mcp_hub": (
        "reference",
        "MCP hub / registry submission for vectr's PyPI release goes "
        "through Glama's directory in addition to the primary MCP registry "
        "submission form.",
        ["mcp", "registry", "release"],
    ),

    # --- decision ------------------------------------------------------
    "decision_memory_state_machine_anchors": (
        "decision",
        "Decision: treat a note's proxy-anchor drift and content "
        "contradiction as two SEPARATE detection paths in the memory state "
        "machine — drift is a write-time hash re-check against the anchored "
        "file, contradiction is an explicit `contradicts=` write from "
        "another note. Conflating them would let a routine file edit read "
        "as a semantic contradiction.",
        ["memory-state-machine", "anchors"],
    ),
    "decision_kind_dimension_scopes": (
        "decision",
        "Decision: give each memory `kind` its own default trigger bundle "
        "and default scope instead of one global default — a `task` note "
        "defaults to branch scope and a `gotcha` defaults to repo scope, "
        "every other kind keeps workspace scope, because a one-size default "
        "was starving the kinds with the sharpest natural boundary.",
        ["kind", "scope", "triggers"],
    ),
    "decision_dual_vector_purpose_body": (
        "decision",
        "Decision: store a purpose vector (signature + docstring, body "
        "stripped) alongside the full-body vector per symbol, rather than "
        "trying to fix embedding dilution by re-weighting a single vector — "
        "a single vector cannot simultaneously be close to a purpose query "
        "and close to an implementation-detail query.",
        ["embeddings", "dual-vector"],
    ),

    # --- operational -----------------------------------------------------
    "operational_venv_pytest_invocation": (
        "operational",
        "CI and local runs both must invoke pytest through the project's "
        "own `.venv/bin/python -m pytest` — the global interpreter lacks "
        "the optional compiled parser grammars and silently reports false "
        "C/C++ failures.",
        ["pytest", "ci", "environment"],
    ),
    "operational_model_cache_offline": (
        "operational",
        "The embedding and reranker models load from the local Hugging "
        "Face cache with an offline-preferred path — a daemon start should "
        "never make a live network call to re-check for a newer revision "
        "when the model is already fully cached.",
        ["model-cache", "offline", "startup"],
    ),
    "operational_worktree_missing_venv": (
        "operational",
        "A git worktree does not carry `.venv` (it's git-ignored) — run "
        "tests from the worktree directory using the MAIN checkout's "
        "interpreter, `cd <worktree> && <main>/.venv/bin/python -m pytest`, "
        "so `agent/` resolves to the worktree's copy rather than the "
        "editable install pointing at main.",
        ["worktree", "venv", "testing"],
    ),

    # --- noise / distractor notes (not the labeled-relevant answer to any
    # fixture query below) — gives the harness something real to filter
    # through rather than a corpus where every note is a labeled target. ---
    "noise_task_unrelated_1": (
        "task",
        "Still triaging an unrelated flaky test in a different subsystem, "
        "nothing conclusive yet — will circle back after the current lane.",
        ["noise"],
    ),
    "noise_finding_unrelated_1": (
        "finding",
        "Background finding, not tied to any of the fixture's labeled "
        "topics: a third-party dependency bumped its minor version with no "
        "behavior change observed.",
        ["noise"],
    ),
    "noise_gotcha_unrelated_1": (
        "gotcha",
        "Unrelated caveat: a local dev script assumes a specific shell and "
        "breaks under a different one — not part of any measured topic "
        "here.",
        ["noise"],
    ),
    "noise_reference_unrelated_1": (
        "reference",
        "Unrelated pointer: internal style guide lives in a wiki page, not "
        "referenced by any labeled query in this fixture.",
        ["noise"],
    ),
    "noise_operational_unrelated_1": (
        "operational",
        "Unrelated operational note: a CI runner occasionally needs a "
        "manual cache clear after a dependency lockfile change, unrelated "
        "to any labeled fixture topic.",
        ["noise"],
    ),
}


def build_fixture_corpus(store, workspace: str = FIXTURE_WORKSPACE) -> dict[str, NoteInfo]:
    """Insert every NOTES entry via the real `remember()` and return
    key -> NoteInfo (the real assigned note_id, kind, content)."""
    key_to_note: dict[str, NoteInfo] = {}
    for key, (kind, content, tags) in NOTES.items():
        note_id = store.remember(workspace, content, tags=tags, kind=kind)
        key_to_note[key] = NoteInfo(note_id=note_id, kind=kind, content=content)
    return key_to_note


# ---------------------------------------------------------------------------
# Labeled queries.
# ---------------------------------------------------------------------------
FIXTURE_QUERIES: list[LabeledQuery] = [
    # --- mined-transcript queries (real vectr_recall query strings from
    # this workspace's own session transcripts), each paired with the one
    # fixture note authored to answer it. ---------------------------------
    LabeledQuery(
        query="root cause findings gotcha",
        relevant_keys=("gotcha_root_cause_findings",),
        basis="Query text is a real mined vectr_recall call; "
              "gotcha_root_cause_findings is the note authored to answer "
              "exactly this topic (root-cause-fix-not-workaround rule).",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="UPG-NOTES-CHROMA notes vector store chroma P1 task definition",
        relevant_keys=("task_notes_chroma_vector_store",),
        basis="Query names the UPG id verbatim; task_notes_chroma_vector_store "
              "is the fixture note carrying that exact UPG id and topic.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="discovery memoization L3 boundary distillation open items episode capture arc detection status",
        relevant_keys=("task_discovery_memoization_l3",),
        basis="Query is a real mined vectr_recall call about L3 memoization "
              "status; the paired note is authored to be its current-state "
              "answer.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="memory state machine proxy-anchor drift detection write-time contradiction detection open items",
        relevant_keys=("decision_memory_state_machine_anchors",),
        basis="Query is a real mined vectr_recall call; the paired decision "
              "note records exactly the anchor-drift-vs-contradiction split "
              "the query is asking about.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="vscode extension publish workflow disabled VSCE PAT azure devops",
        relevant_keys=("task_vscode_extension_publish",),
        basis="Query text matches the note's topic almost verbatim (VSCE "
              "PAT / Azure DevOps publish blocker).",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="explore freely only once, never re-explore; one-shot exploration then memory reuse; git-style state machine memory branch revert commit",
        relevant_keys=("directive_one_shot_explore",),
        basis="Query is a real mined vectr_recall call; "
              "directive_one_shot_explore is the standing rule it is "
              "recalling.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="vectr release PyPI publish MCP directory registry submission version bump",
        relevant_keys=("task_vectr_release_pypi", "reference_glama_mcp_hub"),
        basis="Query covers both the release task itself and the registry "
              "reference pointer — two fixture notes both genuinely answer "
              "different parts of the same query.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="PRAGMA optimize fresh connection ANALYZE restart extra roots",
        relevant_keys=("gotcha_pragma_optimize_restart",),
        basis="Query is a real mined vectr_recall call naming the exact "
              "PRAGMA/ANALYZE gotcha the paired note documents.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="reword internal build-process references product comments hygiene lane codenames docstrings pass",
        relevant_keys=("finding_reword_internal_process_refs",),
        basis="Query is a real mined vectr_recall call about the comment "
              "hygiene pass; the paired finding is that exact rule.",
        source="mined-transcript",
    ),
    LabeledQuery(
        query="chunking ranking search retrieval markdown headers low-info chunks scoring",
        relevant_keys=("finding_chunking_markdown_headers",),
        basis="Query is a real mined vectr_recall call; the paired finding "
              "documents the markdown-header over-ranking defect it is "
              "asking about.",
        source="mined-transcript",
    ),

    # --- synthetic exact-content queries: query text equals the note's own
    # content verbatim, guaranteeing a top-rank hit under ANY embedder
    # (including a hash-based dummy one) that embeds identical text
    # identically. One per remaining uncovered kind, so every kind has at
    # least one scored hit case. ------------------------------------------
    LabeledQuery(
        query="Commit messages stay factual and technical only — no narrative or "
              "process storytelling, no emoji.",
        relevant_keys=("directive_commit_style",),
        basis="Query is the note's own content verbatim — exact-match "
              "sanity case.",
        source="synthetic",
    ),
    LabeledQuery(
        query="The authoritative spec document lives at tools/vectr/docs/spec.md "
              "(not the short stub at tools/vectr/spec.md) — always read the "
              "docs/ path before citing spec behavior.",
        relevant_keys=("reference_spec_doc_location",),
        basis="Query is the note's own content verbatim — exact-match "
              "sanity case.",
        source="synthetic",
    ),
    LabeledQuery(
        query="Decision: give each memory `kind` its own default trigger bundle "
              "and default scope instead of one global default — a `task` note "
              "defaults to branch scope and a `gotcha` defaults to repo scope, "
              "every other kind keeps workspace scope, because a one-size default "
              "was starving the kinds with the sharpest natural boundary.",
        relevant_keys=("decision_kind_dimension_scopes",),
        basis="Query is the note's own content verbatim — exact-match "
              "sanity case.",
        source="synthetic",
    ),
    LabeledQuery(
        query="CI and local runs both must invoke pytest through the project's "
              "own `.venv/bin/python -m pytest` — the global interpreter lacks "
              "the optional compiled parser grammars and silently reports false "
              "C/C++ failures.",
        relevant_keys=("operational_venv_pytest_invocation",),
        basis="Query is the note's own content verbatim — exact-match "
              "sanity case.",
        source="synthetic",
    ),

    # --- deliberate paraphrase queries: worded close in meaning but far in
    # surface text from the target note, intended to MISS under a
    # surface-hash embedder — exercises the miss-recording/kind-grouping
    # path against the real store, not just the pure-logic mock. ----------
    LabeledQuery(
        query="how does vectr keep call-graph centrality from confusing two "
              "same-named methods on different classes",
        relevant_keys=("finding_call_edges_leaf_only",),
        basis="Genuinely the right note (leaf-only call edges explain the "
              "centrality limitation), but worded as a paraphrase rather "
              "than a term-overlap query — a fair miss case for a "
              "surface-similarity embedder.",
        source="synthetic",
    ),
    LabeledQuery(
        query="why would a symbol with a docstring that clearly matches the "
              "search intent still not show up in results at all",
        relevant_keys=("finding_embedding_dilution_gap",),
        basis="Genuinely the right note (embedding dilution explains a "
              "docstring-relevant symbol missing from the pool), worded as "
              "a paraphrase rather than a term-overlap query.",
        source="synthetic",
    ),
    LabeledQuery(
        query="what happens if the same working note keeps getting marked "
              "possibly-outdated over and over without anyone resolving it",
        relevant_keys=("gotcha_stale_flag_repeat",),
        basis="Genuinely the right note (repeat stale_flagged events), "
              "worded as a paraphrase rather than a term-overlap query.",
        source="synthetic",
    ),
]
