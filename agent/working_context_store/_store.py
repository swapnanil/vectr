"""
WorkingContextStore — SQLite-backed store for LLM working notes and session snapshots.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from agent.working_context_store._audit import audit
from agent.working_context_store._encryption import _build_encryptor, _extract_file_paths, _NoteEncryptor
from agent.working_context_store._types import (
    DEFAULT_KIND,
    DEFAULT_PROVENANCE,
    DEFAULT_SCOPE,
    PROVENANCE_VALUES,
    SCOPE_VALUES,
    VALID_KINDS,
    SnapshotEntry,
    WorkingNote,
)

logger = logging.getLogger(__name__)


def _hash_path_content(root: Path, raw_path: str) -> str | None:
    """sha256[:16] of a workspace-relative (or absolute) path's current file
    content, or None if it cannot be read (missing, directory, permission
    error). Shared by `remember()`'s anchor-hash-at-write and
    `check_staleness()`'s anchor re-hash-at-fire-time (TRIGGER-ENGINE wave 1,
    bm2-design-skeleton.md §5) so both apply the exact same rule — this is
    the same sha256[:16] shape as the pre-existing `code_hash` staleness
    check just below, generalised to any anchor path rather than one
    extension allowlist."""
    path = Path(raw_path)
    resolved = path if path.is_absolute() else root / path
    try:
        return hashlib.sha256(resolved.read_bytes()).hexdigest()[:16]
    except OSError:
        return None

# Metadata key stamped on the 'working_memory' ChromaDB collection recording
# the embed model that produced its CURRENT vectors (UPG-NOTES-EMBED-MIGRATION).
# Mirrors CodeIndexer's embed-model stamp for the code index (see
# agent/indexer/_constants.py's _EMBED_MODEL_STAMP_FILE docstring), but notes
# are irreplaceable user memory rather than a rebuildable derived index, so a
# stamp mismatch here triggers an in-place re-embed + vector update instead of
# a drop-and-rebuild.
_NOTES_EMBED_MODEL_KEY = "embed_model"

# Intentionally NOT in config.yaml (Tier-3, same category as the indexer's
# _EMBED_BATCH_SIZE): a pure throughput lever with no effect on ranking,
# recall behavior, or note content — only how many texts are handed to
# embed_fn per call during a one-time startup migration.
_NOTES_REEMBED_BATCH_SIZE = 256

# SQLite busy-wait for a contended write lock (team mode: concurrent clients +
# the CLI can share one workspace's notes DB). Intentionally NOT in config.yaml
# — a robustness/timeout knob, same category as the throughput constants above.
_SQLITE_BUSY_TIMEOUT_S = 5.0


class WorkingContextStore:
    """
    SQLite-backed store for LLM working notes and session snapshots.

    Design principle: the LLM should never be afraid to forget something
    if Vectr has it. This store is the guarantee.

    When embed_fn and notes_chroma_client are provided, note content is embedded
    at remember() time and stored in a ChromaDB 'working_memory' collection.
    recall(query=...) then uses cosine similarity to find relevant notes instead
    of SQL LIKE substring matching. SQL LIKE is retained as a fallback.

    embed_fn embeds document-side text (note content being stored); embed_query_fn
    embeds the recall query. These are kept distinct because asymmetric embedding
    models require a different prompt for queries than for the passages they're
    matched against — reusing embed_fn for both would silently skip that prompt on
    every recall. Callers that don't care about the distinction (e.g. tests with a
    plain symmetric stand-in) may omit embed_query_fn; it then defaults to embed_fn.

    embed_model, when given, identifies the model embed_fn/embed_query_fn currently
    embed with (e.g. "ibm-granite/granite-embedding-english-r2"). It is stamped onto
    the 'working_memory' collection's metadata (UPG-NOTES-EMBED-MIGRATION). If the
    stamp on an existing collection differs from embed_model — including a MISSING
    stamp on a collection that already holds vectors, since we cannot know what
    model produced those — every active note's content is re-embedded with the
    current embed_fn and the collection's vectors are updated in place before the
    constructor returns, so note vectors and query vectors are never silently drawn
    from two different embedding spaces. This mirrors CodeIndexer's embed-model
    stamp for the code index, but migrates rather than drops: notes are
    irreplaceable user memory, and re-embedding a few hundred short texts is cheap.
    embed_model is optional and defaults to None, in which case no stamping or
    migration happens at all (existing constructions and tests are unaffected).
    """

    def __init__(
        self,
        db_dir: str,
        embed_fn=None,
        notes_chroma_client=None,
        embed_query_fn=None,
        embed_model: str | None = None,
    ) -> None:
        self._db_path = Path(db_dir) / "working_context.sqlite"
        self._encryptor: _NoteEncryptor | None = _build_encryptor()
        # Semantic recall: embed notes at write time, cosine search at recall time
        self._embed_fn = embed_fn   # Callable[[list[str]], list[list[float]]] | None
        self._embed_query_fn = embed_query_fn or embed_fn  # query-mode embed, defaults to embed_fn
        self._embed_model = embed_model  # current model name, for the embed-model stamp/migration
        self._notes_col = None
        # Guards attach_embedder() (UPG-STDIO-MEMORY-READY): the store can be
        # constructed with embed_fn=None (memory tools live before the
        # embedding model has loaded) and upgraded to a real embedder later,
        # from a background thread, once phase-2 service init completes. The
        # lock makes that upgrade idempotent and keeps a concurrent
        # remember()/recall() from ever observing self._notes_col set while
        # self._embed_fn is still the old value (or vice versa).
        self._attach_lock = threading.Lock()
        if embed_fn is not None and notes_chroma_client is not None and not self._vectors_disabled():
            try:
                self._notes_col = notes_chroma_client.get_or_create_collection(
                    name="working_memory",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception:
                pass  # embedding unavailable — fall back to SQL LIKE silently
        self._init_db()
        if self._notes_col is not None and self._embed_model:
            try:
                self._reconcile_embed_model_stamp()
            except Exception:
                logger.warning(
                    "vectr: working-memory embed-model migration failed — "
                    "note vectors may still be in a stale embedding space; "
                    "will retry on next startup",
                    exc_info=True,
                )

    def _vectors_disabled(self) -> bool:
        """Strict encryption posture (VECTR_ENCRYPT_DISABLE_NOTE_VECTORS): when
        encryption is on, the note embedding vectors are a lossy plaintext
        projection of note content living in the Chroma store. Setting this
        omits them entirely — recall falls back to lexical SQL LIKE — so no
        representation of note content leaves the encrypted SQLite column.
        Shared by __init__ and attach_embedder() so both paths honor it the
        same way regardless of when the embedder becomes available."""
        return (
            self._encryptor is not None
            and os.getenv("VECTR_ENCRYPT_DISABLE_NOTE_VECTORS", "") == "1"
        )

    @property
    def embedder_ready(self) -> bool:
        """True once an embedder is attached — either passed to __init__ or
        via a later attach_embedder() call (UPG-STDIO-MEMORY-READY).

        False means remember()/recall() are lexical/SQL-only for now: notes
        still write and read correctly, just without semantic ranking or
        vectors, until an embedder attaches."""
        return self._embed_fn is not None

    def attach_embedder(
        self,
        embed_fn,
        notes_chroma_client,
        embed_query_fn=None,
        embed_model: str | None = None,
    ) -> None:
        """Upgrade an embedder-less store (constructed with embed_fn=None) to
        semantic recall, once an embedding model becomes available
        (UPG-STDIO-MEMORY-READY).

        Lets memory tools (remember/recall/forget/status/snapshot) work from
        process start, before the embedding model has finished loading or
        downloading, on every transport — the store itself never needs an
        embedder to read or write a note. This method is how the store is
        upgraded once phase-2 service init (CodeIndexer's embed provider)
        completes in the background, without re-writing or losing any note
        recorded during the window it was missing.

        Idempotent: a second call is a no-op once an embedder is already
        attached. Thread-safe: `self._notes_col` and `self._embed_fn` are
        only ever set together inside `_attach_lock`, and `self._embed_fn`
        (the field every reader gates on) is set last — a concurrent
        remember()/recall() either sees the fully-attached state or the
        original embedder-less state, never a half-attached mix.

        After attaching, runs the existing embed-model stamp reconcile (in
        case the configured model differs from what a previous run stamped)
        and then backfills a vector for every note that doesn't have one yet
        — the notes written during the window this store had no embedder at
        all. Both steps are best-effort: a failure here never raises, since
        the store is already fully usable via the SQL fallback.
        """
        with self._attach_lock:
            if self._embed_fn is not None:
                return  # already attached
            if embed_fn is None or notes_chroma_client is None or self._vectors_disabled():
                return
            try:
                notes_col = notes_chroma_client.get_or_create_collection(
                    name="working_memory",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception:
                return  # embedding still unavailable — stay on SQL LIKE fallback
            self._embed_query_fn = embed_query_fn or embed_fn
            self._embed_model = embed_model
            self._notes_col = notes_col
            self._embed_fn = embed_fn

        if self._embed_model:
            try:
                self._reconcile_embed_model_stamp()
            except Exception:
                logger.warning(
                    "vectr: working-memory embed-model migration failed after "
                    "attach — note vectors may still be in a stale embedding "
                    "space; will retry on next startup",
                    exc_info=True,
                )
        try:
            self.backfill_missing_vectors()
        except Exception:
            logger.warning(
                "vectr: working-memory vector backfill failed after attach — "
                "notes written before the embedder was ready may not be "
                "semantically recallable until the next restart",
                exc_info=True,
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=_SQLITE_BUSY_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # Team mode: several clients (and the CLI) can hit one workspace's notes
        # DB concurrently. WAL allows concurrent readers + one writer; busy_timeout
        # makes a would-be second writer wait for the lock instead of immediately
        # raising "database is locked". note_id is AUTOINCREMENT, so IDs stay
        # unique under concurrent inserts once writes are serialized by the lock.
        conn.execute(f"PRAGMA busy_timeout={int(_SQLITE_BUSY_TIMEOUT_S * 1000)}")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS notes (
                    note_id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace           TEXT NOT NULL,
                    content             TEXT NOT NULL,
                    tags                TEXT NOT NULL DEFAULT '[]',
                    priority            TEXT NOT NULL DEFAULT 'medium',
                    kind                TEXT NOT NULL DEFAULT 'finding',
                    created_at          REAL NOT NULL,
                    last_accessed       REAL NOT NULL,
                    session_id          TEXT,
                    decay_score         REAL NOT NULL DEFAULT 1.0,
                    author_id           TEXT NOT NULL DEFAULT '',
                    author_trust_score  REAL NOT NULL DEFAULT 1.0,
                    valid_from          REAL NOT NULL DEFAULT 0.0,
                    valid_until         REAL,
                    code_hash           TEXT NOT NULL DEFAULT '',
                    superseded_by       TEXT,
                    superseded_at       REAL
                );

                CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes(workspace);
                CREATE INDEX IF NOT EXISTS idx_notes_tags ON notes(tags);

                CREATE TABLE IF NOT EXISTS author_trust (
                    workspace           TEXT NOT NULL,
                    author_id           TEXT NOT NULL,
                    trust_score         REAL NOT NULL DEFAULT 1.0,
                    note_count          INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (workspace, author_id)
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id  TEXT PRIMARY KEY,
                    workspace    TEXT NOT NULL,
                    label        TEXT NOT NULL,
                    payload      TEXT NOT NULL,
                    created_at   REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_snap_workspace ON snapshots(workspace);
            """)
            # P4: migrate existing databases that predate P4 columns
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
            p4_cols = {
                "author_id":          "TEXT NOT NULL DEFAULT ''",
                "author_trust_score": "REAL NOT NULL DEFAULT 1.0",
                "valid_from":         "REAL NOT NULL DEFAULT 0.0",
                "valid_until":        "REAL",
                "code_hash":          "TEXT NOT NULL DEFAULT ''",
                "superseded_by":      "TEXT",
                "superseded_at":      "REAL",
                # UPG-9.3: memory kind dimension — existing rows default to 'finding'.
                "kind":               "TEXT NOT NULL DEFAULT 'finding'",
                # UPG-RECALL-HIERARCHY: per-note title for index-tier display.
                "title":              "TEXT NOT NULL DEFAULT ''",
                # TRIGGER-ENGINE wave 1 (bm2-design-skeleton.md §1) — additive,
                # fully backward-compatible columns. Existing rows default to
                # DEFAULT_PROVENANCE/DEFAULT_SCOPE/no triggers/no anchors, so an
                # existing note's evaluation-time behaviour is unchanged: empty
                # triggers falls back to its kind's default bundle exactly as a
                # brand-new note with no explicit triggers would.
                "triggers":              "TEXT NOT NULL DEFAULT '[]'",
                "provenance":            "TEXT NOT NULL DEFAULT 'agent'",
                "scope":                 "TEXT NOT NULL DEFAULT 'workspace'",
                "anchors":               "TEXT NOT NULL DEFAULT '[]'",
                "supersedes":            "INTEGER",
                # Distinct from the existing `superseded_by` (TEXT author_id,
                # set by the code_hash-conflict path above) — this records the
                # note_id of the memory that explicitly superseded this one via
                # the new `supersedes` write-time parameter.
                "superseded_by_note_id": "INTEGER",
                "last_fired":            "REAL",
            }
            for col, typedef in p4_cols.items():
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {typedef}")

            # Create indexes that depend on migrated columns — must run AFTER migration
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_code_hash ON notes(code_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_valid ON notes(valid_until)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_kind ON notes(kind)")

    # ------------------------------------------------------------------
    # Notes — vectr_remember / vectr_recall / vectr_forget
    # ------------------------------------------------------------------

    def remember(
        self,
        workspace: str,
        content: str,
        tags: list[str] | None = None,
        priority: str = "medium",
        session_id: str | None = None,
        author_id: str = "",
        code_hash: str = "",
        kind: str = DEFAULT_KIND,
        title: str = "",
        triggers: list[dict] | None = None,
        provenance: str = DEFAULT_PROVENANCE,
        scope: str = DEFAULT_SCOPE,
        anchors: list[str] | None = None,
        supersedes: int | None = None,
    ) -> int:
        """Store a working note. Returns the note_id.

        If code_hash is provided and another non-superseded note exists for the
        same workspace + code_hash (same code anchor), the older note is marked
        superseded before the new note is inserted.

        `kind` is one of VALID_KINDS (directive|task|gotcha|finding|reference);
        an unrecognised value falls back to DEFAULT_KIND.

        `title` is a short label for index-tier display (UPG-RECALL-HIERARCHY).
        When empty, a fallback is derived from the first non-empty line of content,
        stripped and truncated to 80 characters.

        Trigger engine wave 1 (TRIGGER-ENGINE, bm2-design-skeleton.md §1/§2/§5)
        — all additive, all optional, one call, zero trigger literacy required:

        `triggers`: explicit P/E/T trigger overrides (see agent/trigger_engine
        .validate_trigger for the shape). Raises ValueError if malformed.
        Omitted/empty means "use this kind's default bundle at evaluation
        time" — evaluation-time, never baked into storage.

        `provenance`: one of PROVENANCE_VALUES ("human"|"agent"|"auto").
        Raises ValueError if not one of those, OR if provenance="auto" and
        kind="directive" — an unreviewed standing rule is a contradiction in
        terms and is rejected outright rather than silently downgraded.

        `scope`: one of SCOPE_VALUES. Raises ValueError if not recognised.

        `anchors`: a list of workspace-relative (or absolute) file paths this
        note is anchored to. Each path's current content hash is computed
        HERE, at write time (never supplied by the caller) and stored
        alongside the path — `check_staleness()` re-hashes at fire/recall
        time and raises a visible (never silent) staleness caveat on
        mismatch. A path that cannot be read yet (e.g. a file not created
        yet) stores a null hash and is simply never flagged stale until it
        exists.

        `supersedes`: the note_id this note explicitly tombstones. The
        target note (looked up in this workspace) has `valid_until`/
        `superseded_at` set (excluding it from recall() by default and from
        ever firing again, per evaluate_note) and `superseded_by_note_id` set
        to this new note's id — kept for audit, never deleted. Raises
        ValueError if the target note does not exist in this workspace.
        Distinct from the pre-existing `superseded_by` (author_id) column,
        which is set only by the unrelated code_hash-conflict path above.
        """
        from agent.trigger_engine import validate_triggers

        now = time.time()
        tags_json = json.dumps(tags or [])
        if kind not in VALID_KINDS:
            kind = DEFAULT_KIND

        if provenance not in PROVENANCE_VALUES:
            raise ValueError(f"provenance must be one of: {', '.join(PROVENANCE_VALUES)}")
        if provenance == "auto" and kind == "directive":
            raise ValueError(
                "provenance='auto' is not allowed on kind='directive' — an "
                "unreviewed standing rule is a contradiction in terms; use "
                "provenance='agent' (or have a human endorse it) instead"
            )
        if scope not in SCOPE_VALUES:
            raise ValueError(f"scope must be one of: {', '.join(SCOPE_VALUES)}")

        triggers_list = validate_triggers(triggers)
        triggers_json = json.dumps(triggers_list)

        root = Path(workspace)
        anchor_pairs = [[p, _hash_path_content(root, p)] for p in (anchors or [])]
        anchors_json = json.dumps(anchor_pairs)

        # Derive title fallback from first non-empty content line (80-char cap).
        if not title:
            for line in content.splitlines():
                stripped = line.strip()
                if stripped:
                    title = stripped[:80]
                    break
        # Encrypt BOTH content and the (possibly content-derived) title: the
        # default title is the first content line, so a plaintext title column
        # would leak the very text encryption is meant to protect.
        if self._encryptor:
            stored_content = self._encryptor.encrypt(content)
            stored_title = self._encryptor.encrypt(title)
        else:
            stored_content = content
            stored_title = title

        with self._conn() as conn:
            # supersedes (TRIGGER-ENGINE): validate the target BEFORE insert so
            # a bad note_id never leaves a half-applied write.
            if supersedes is not None:
                target = conn.execute(
                    "SELECT note_id FROM notes WHERE workspace = ? AND note_id = ?",
                    (workspace, supersedes),
                ).fetchone()
                if target is None:
                    raise ValueError(f"supersedes references note #{supersedes}, which does not exist in this workspace")

            # conflict resolution: if another note anchors the same code block, supersede it
            if code_hash:
                conflicting = conn.execute(
                    """SELECT note_id FROM notes
                       WHERE workspace = ? AND code_hash = ?
                       AND valid_until IS NULL""",
                    (workspace, code_hash),
                ).fetchall()
                if conflicting:
                    conn.execute(
                        """UPDATE notes SET valid_until = ?, superseded_by = ?, superseded_at = ?
                           WHERE note_id IN ({})""".format(",".join("?" * len(conflicting))),
                        [now, author_id or "unknown", now] + [r[0] for r in conflicting],
                    )

            cur = conn.execute(
                """
                INSERT INTO notes (workspace, content, tags, priority, kind, created_at,
                                   last_accessed, session_id, decay_score,
                                   author_id, author_trust_score, valid_from,
                                   valid_until, code_hash, title,
                                   triggers, provenance, scope, anchors, supersedes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, 1.0, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (workspace, stored_content, tags_json, priority, kind, now, now, session_id,
                 author_id, now, code_hash, stored_title,
                 triggers_json, provenance, scope, anchors_json, supersedes),
            )
            note_id = cur.lastrowid

            # Explicit tombstone (TRIGGER-ENGINE §1): the superseded note is
            # excluded from recall() by default (valid_until IS NULL filter,
            # pre-existing) and never fires again (evaluate_note checks
            # valid_until), but is retained with its full provenance for audit.
            if supersedes is not None:
                conn.execute(
                    """UPDATE notes SET valid_until = ?, superseded_at = ?, superseded_by_note_id = ?
                       WHERE workspace = ? AND note_id = ?""",
                    (now, now, note_id, workspace, supersedes),
                )

            # update author trust score registry (Bayesian: count-weighted)
            if author_id:
                conn.execute(
                    """INSERT INTO author_trust (workspace, author_id, trust_score, note_count)
                       VALUES (?, ?, 1.0, 1)
                       ON CONFLICT(workspace, author_id) DO UPDATE SET
                           note_count = note_count + 1,
                           trust_score = MIN(1.0, trust_score + 0.05)""",
                    (workspace, author_id),
                )

        # Embed and store in the vector index so recall(query=...) can use cosine similarity.
        # content is the plaintext (before encryption) — embeddings are over raw text.
        if self._notes_col is not None and self._embed_fn is not None:
            try:
                vec = self._embed_fn([content])[0]
                self._notes_col.upsert(ids=[str(note_id)], embeddings=[vec])
            except Exception:
                pass  # embedding failure never blocks the write path

        audit("REMEMBER", workspace=workspace, note_id=note_id, priority=priority,
              kind=kind, author_id=author_id, code_hash=code_hash[:8] if code_hash else "",
              tags=",".join(tags or []), chars=len(content), provenance=provenance, scope=scope)
        return note_id  # type: ignore[return-value]

    def promote(self, workspace: str, note_id: int, to: str) -> bool:
        """Explicit provenance promotion (bm2-design-skeleton.md §5):
        auto -> agent -> human only, one step at a time. Provenance is
        immutable at write; this is the one sanctioned, explicit way to
        raise it after the fact (e.g. a human reviews and endorses an
        agent-authored note). Demotion is impossible — `to` must be exactly
        one rank above the note's current provenance.

        Returns True if promoted, False if the note does not exist. Raises
        ValueError if `to` is not a valid single-step promotion from the
        note's current provenance.
        """
        note = self.get_note(workspace, note_id)
        if note is None:
            return False
        order = PROVENANCE_VALUES[::-1]  # ("auto", "agent", "human") — promotion direction
        try:
            current_rank = order.index(note.provenance)
        except ValueError:
            current_rank = 0
        if to not in PROVENANCE_VALUES:
            raise ValueError(f"'to' must be one of: {', '.join(PROVENANCE_VALUES)}")
        to_rank = order.index(to)
        if to_rank != current_rank + 1:
            raise ValueError(
                f"promote() only allows a single step up from '{note.provenance}' "
                f"(auto -> agent -> human); '{to}' is not that step"
            )
        with self._conn() as conn:
            conn.execute(
                "UPDATE notes SET provenance = ? WHERE workspace = ? AND note_id = ?",
                (to, workspace, note_id),
            )
        audit("PROMOTE", workspace=workspace, note_id=note_id, provenance=to)
        return True

    def recall(
        self,
        workspace: str,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        kind: str | None = None,
        min_similarity: float | None = None,
        max_age_days: float | None = None,
        sort_by: str = "relevance",
    ) -> list[WorkingNote]:
        """Retrieve working notes.

        When query is provided and semantic search is available (embed_fn + ChromaDB
        collection configured), uses cosine similarity to rank notes by relevance.
        Falls back to SQL LIKE substring match when semantic search is unavailable.

        Superseded notes are excluded by default. Pass include_superseded=True
        to see the full history including notes marked as superseded.
        Without a query, results are ordered by author_trust_score DESC, decay_score DESC,
        created_at DESC, note_id DESC so the highest-trust contributor's notes surface
        first, with a fully deterministic tie-break (UPG-RECALL-ORDER-CHURN: last_accessed
        is intentionally excluded from this ordering — recall itself updates
        last_accessed on every note it returns, so using it as a sort key made
        two back-to-back identical calls read-your-own-writes into a different
        order each time). kind='task' notes are exempted from the trust/decay
        part of that ordering (UPG-TASK-NOTE-INJECTION-RECENCY): a task note is
        current-work state, not a relevance-ranked learning, so an older task
        note with a higher author_trust_score/decay_score must never outrank a
        newer one — for kind='task' rows the trust/decay columns are treated
        as equal and created_at DESC (then note_id DESC) decides the order
        directly. Every other kind is unaffected.

        max_age_days: when set, only notes created within the last max_age_days days
        are returned (UPG-RECALL-HIERARCHY time filter).

        sort_by: one of 'relevance' (semantic/default SQL order), 'recency'
        (created_at DESC), or 'priority' (high>medium>low, then created_at DESC).
        In the semantic path, recency/priority are applied as a re-sort after
        candidate fetch (relevance = semantic order is unchanged).
        """
        # Semantic path: embed the query, find cosine-nearest notes, then fetch from SQLite.
        if query and self._notes_col is not None and self._embed_fn is not None:
            try:
                notes = self._semantic_recall(
                    workspace, query, tags, priority, limit, include_superseded, kind,
                    min_similarity, max_age_days, sort_by,
                )
                audit("RECALL", workspace=workspace, query=query, notes_returned=len(notes),
                      method="semantic")
                return notes
            except Exception:
                pass  # fall through to SQL LIKE

        # SQL path: used when no query, or when semantic search is unavailable/errored.
        sql = "SELECT * FROM notes WHERE workspace = ?"
        params: list = [workspace]

        if not include_superseded:
            sql += " AND valid_until IS NULL"

        if priority:
            sql += " AND priority = ?"
            params.append(priority)

        if kind:
            sql += " AND kind = ?"
            params.append(kind)

        if tags:
            tag_clauses = " OR ".join(["tags LIKE ?" for _ in tags])
            sql += f" AND ({tag_clauses})"
            params.extend([f'%"{t}"%' for t in tags])

        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")

        if max_age_days is not None:
            cutoff = time.time() - max_age_days * 86400
            sql += " AND created_at >= ?"
            params.append(cutoff)

        # sort_by applies to the SQL path's ORDER BY clause.
        if sort_by == "recency":
            sql += " ORDER BY created_at DESC LIMIT ?"
        elif sort_by == "priority":
            sql += (
                " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
                " created_at DESC LIMIT ?"
            )
        else:
            # relevance (default): trust/decay ordering, then a deterministic
            # tie-break (UPG-RECALL-ORDER-CHURN — last_accessed excluded, see
            # the recall() docstring above). kind='task' notes are current-work
            # state, not relevance-ranked learnings — author_trust_score/
            # decay_score are neutralised to a constant for them so recency
            # dominates regardless of trust (UPG-TASK-NOTE-INJECTION-RECENCY);
            # every other kind keeps the unmodified trust/decay ordering.
            sql += (
                " ORDER BY"
                " (CASE WHEN kind = 'task' THEN 1.0 ELSE author_trust_score END) DESC,"
                " (CASE WHEN kind = 'task' THEN 1.0 ELSE decay_score END) DESC,"
                " created_at DESC, note_id DESC LIMIT ?"
            )
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        notes = [self._row_to_note(r) for r in rows]

        if notes:
            ids = [n.note_id for n in notes]
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE notes SET last_accessed = ? WHERE note_id IN ({','.join('?' * len(ids))})",
                    [time.time(), *ids],
                )

        audit("RECALL", workspace=workspace, query=query or "", notes_returned=len(notes),
              method="sql")
        return notes

    def recall_scored(
        self,
        workspace: str,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        kind: str | None = None,
        min_similarity: float | None = None,
        max_age_days: float | None = None,
        sort_by: str = "relevance",
    ) -> list[tuple[WorkingNote, float | None]]:
        """Like recall(), but each note is paired with its cosine similarity
        (UPG-PRO-1) so a gating layer can budget/threshold on it.

        Semantic path returns real similarities (1 - cosine distance). The SQL
        LIKE fallback has no cosine to report and returns None per note — never
        a fabricated number. Ordering matches the scoreless recall() exactly.
        """
        if query and self._notes_col is not None and self._embed_fn is not None:
            try:
                return self._semantic_recall(
                    workspace, query, tags, priority, limit, include_superseded, kind,
                    min_similarity, max_age_days, sort_by, return_scores=True,
                )
            except Exception:
                pass  # fall through to SQL LIKE (scoreless)
        notes = self.recall(
            workspace, query=query, tags=tags, priority=priority, limit=limit,
            include_superseded=include_superseded, kind=kind,
            min_similarity=min_similarity, max_age_days=max_age_days, sort_by=sort_by,
        )
        return [(n, None) for n in notes]

    def _semantic_recall(
        self,
        workspace: str,
        query: str,
        tags: list[str] | None,
        priority: str | None,
        limit: int,
        include_superseded: bool,
        kind: str | None = None,
        min_similarity: float | None = None,
        max_age_days: float | None = None,
        sort_by: str = "relevance",
        return_scores: bool = False,
    ) -> list:
        """Find the most relevant notes by cosine similarity, then fetch from SQLite.

        When min_similarity is set (UPG-5.1), candidates whose cosine similarity
        falls below the floor are dropped, so an off-topic query recalls nothing
        instead of the nearest-but-irrelevant note. ChromaDB cosine distance is
        `1 - cosine_similarity`, so similarity = `1 - distance`.

        max_age_days: applied as a SQL filter after candidate fetch (UPG-RECALL-HIERARCHY).
        sort_by: 'relevance' preserves semantic order; 'recency'/'priority' re-sorts
        the candidate set after fetch (UPG-RECALL-HIERARCHY).
        """
        # Cap n_results at collection size to avoid ChromaDB errors on small collections
        col_count = self._notes_col.count()
        if col_count == 0:
            return []
        n_query = min(limit * 3, col_count)

        q_vec = self._embed_query_fn([query])[0]
        results = self._notes_col.query(query_embeddings=[q_vec], n_results=n_query)

        if not results or not results["ids"] or not results["ids"][0]:
            return []

        raw_ids = results["ids"][0]
        distances = (results.get("distances") or [[None] * len(raw_ids)])[0]
        candidate_ids = [int(id_) for id_ in raw_ids]
        # Per-note cosine similarity (1 - distance), kept for the scored recall
        # path (UPG-PRO-1). Computed here where the distances are still in hand;
        # None when ChromaDB returned no distance for a candidate.
        id_to_sim: dict[int, float | None] = {
            int(i): (1.0 - d) if d is not None else None
            for i, d in zip(raw_ids, distances)
        }

        # Relevance cutoff (UPG-5.1) — withhold candidates below the similarity floor.
        if min_similarity is not None:
            id_dist = {int(i): d for i, d in zip(raw_ids, distances)}
            candidate_ids = [
                nid for nid in candidate_ids
                if id_dist.get(nid) is None or (1.0 - id_dist[nid]) >= min_similarity
            ]
            if not candidate_ids:
                return []

        # Fetch from SQLite by semantic candidate IDs, applying metadata filters
        placeholders = ",".join("?" * len(candidate_ids))
        sql = f"SELECT * FROM notes WHERE workspace = ? AND note_id IN ({placeholders})"
        params: list = [workspace, *candidate_ids]

        if not include_superseded:
            sql += " AND valid_until IS NULL"

        if priority:
            sql += " AND priority = ?"
            params.append(priority)

        if kind:
            sql += " AND kind = ?"
            params.append(kind)

        if tags:
            tag_clauses = " OR ".join(["tags LIKE ?" for _ in tags])
            sql += f" AND ({tag_clauses})"
            params.extend([f'%"{t}"%' for t in tags])

        if max_age_days is not None:
            cutoff = time.time() - max_age_days * 86400
            sql += " AND created_at >= ?"
            params.append(cutoff)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        # Preserve semantic rank order by default (ChromaDB returns by ascending distance)
        id_to_row = {r["note_id"]: r for r in rows}
        if sort_by == "relevance":
            ordered = [id_to_row[nid] for nid in candidate_ids if nid in id_to_row][:limit]
            notes = [self._row_to_note(r) for r in ordered]
        else:
            # Convert all candidates to notes, then re-sort.
            all_notes = [self._row_to_note(r) for r in rows]
            if sort_by == "recency":
                all_notes.sort(key=lambda n: n.created_at, reverse=True)
            elif sort_by == "priority":
                _prio_rank = {"high": 0, "medium": 1, "low": 2}
                all_notes.sort(key=lambda n: (_prio_rank.get(n.priority, 1), -n.created_at))
            notes = all_notes[:limit]

        if notes:
            ids = [n.note_id for n in notes]
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE notes SET last_accessed = ? WHERE note_id IN ({','.join('?' * len(ids))})",
                    [time.time(), *ids],
                )

        if return_scores:
            return [(n, id_to_sim.get(n.note_id)) for n in notes]
        return notes

    # ------------------------------------------------------------------
    # Embed-model stamp + migration (UPG-NOTES-EMBED-MIGRATION)
    # ------------------------------------------------------------------

    def _stored_notes_embed_model(self) -> str | None:
        """The embed model stamped on the 'working_memory' collection's
        metadata, or None if no stamp exists yet (fresh collection, or one
        created before this mechanism existed)."""
        if self._notes_col is None:
            return None
        metadata = self._notes_col.metadata or {}
        model = metadata.get(_NOTES_EMBED_MODEL_KEY)
        return str(model) if model else None

    def _write_notes_embed_model_stamp(self) -> None:
        """Stamp the 'working_memory' collection with the embed model that
        produced its CURRENT vectors.

        Two ChromaDB gotchas apply to `collection.modify(metadata=...)`, both
        confirmed against the installed client version:
          - It REPLACES the collection's metadata wholesale rather than
            merging into it, so passing only the changed key would drop
            every other existing metadata entry. The existing metadata is
            always merged with the new stamp first.
          - It unconditionally rejects an `hnsw:space` key in the passed
            metadata with a ValueError — "changing the distance function...
            is not supported" — even when the value is unchanged, so
            `hnsw:space` must be stripped from the merged dict before the
            call. This is safe: the collection's actual distance function is
            pinned at creation independent of the metadata dict shown
            afterward, so dropping the key from displayed metadata does not
            change query behavior (verified: cosine ordering is identical
            before and after a modify() call that omits `hnsw:space`).
        """
        if self._notes_col is None or not self._embed_model:
            return
        merged = {
            k: v for k, v in (self._notes_col.metadata or {}).items()
            if k != "hnsw:space"
        }
        merged[_NOTES_EMBED_MODEL_KEY] = self._embed_model
        try:
            self._notes_col.modify(metadata=merged)
        except Exception:
            pass

    def _reconcile_embed_model_stamp(self) -> None:
        """Migrate note vectors when the configured embed model differs from
        the stamp recorded on the 'working_memory' collection.

        A missing stamp on a collection that already holds vectors is treated
        as a mismatch, not a match: we cannot know what model produced those
        vectors, so re-embedding with the current model is the only way to
        make the space self-consistent either way. An empty/new collection
        has nothing to migrate and is simply stamped.
        """
        stamped = self._stored_notes_embed_model()
        if stamped == self._embed_model:
            return
        count = self._notes_col.count()
        if count == 0:
            self._write_notes_embed_model_stamp()
            return
        start = time.time()
        migrated = self._reembed_all_notes()
        self._write_notes_embed_model_stamp()
        logger.info(
            "vectr: migrated %d working-memory note vector(s) from embed model "
            "%r to %r in %.2fs",
            migrated, stamped, self._embed_model, time.time() - start,
        )

    def _reembed_all_notes(self) -> int:
        """Re-embed every note row's content with the current embed_fn and
        overwrite its vector in the 'working_memory' collection (same id).

        Operates across ALL workspaces in this SQLite file — the collection
        keys vectors by note_id alone, with no workspace scoping — and
        includes superseded notes: they remain queryable via
        `recall(include_superseded=True)`, so their vectors must stay in the
        current embedding space too. Note content, ids, and every other SQL
        column are untouched; only the vector side is rewritten.
        """
        with self._conn() as conn:
            rows = conn.execute("SELECT note_id, content FROM notes").fetchall()
        if not rows:
            return 0

        ids: list[str] = []
        contents: list[str] = []
        for row in rows:
            content = row["content"]
            if self._encryptor:
                try:
                    content = self._encryptor.decrypt(content)
                except Exception:
                    continue  # skip a row that can't be decrypted rather than abort the migration
            ids.append(str(row["note_id"]))
            contents.append(content)
        if not contents:
            return 0

        for start in range(0, len(contents), _NOTES_REEMBED_BATCH_SIZE):
            batch_ids = ids[start:start + _NOTES_REEMBED_BATCH_SIZE]
            batch_contents = contents[start:start + _NOTES_REEMBED_BATCH_SIZE]
            vectors = self._embed_fn(batch_contents)
            self._notes_col.upsert(ids=batch_ids, embeddings=vectors)
        return len(contents)

    def backfill_missing_vectors(self) -> int:
        """Embed and store a vector for every note that doesn't have one yet
        (UPG-STDIO-MEMORY-READY).

        A note can be missing a vector entirely — as opposed to having a
        stale one (`_reembed_all_notes` above) — when it was written while
        `self._embed_fn` was still None, i.e. during the window between
        process start and the embedding model finishing load/download. Those
        notes are never lost (they wrote to SQLite immediately, per
        remember()'s "embedding failure never blocks the write path"
        contract) but they need this pass once an embedder becomes available
        so they become semantically recallable without any re-write.

        Idempotent: a note already present in the 'working_memory'
        collection's id set is left untouched and never re-embedded — safe
        to call after every attach_embedder(), and more than once. Same
        cross-workspace scope as `_reembed_all_notes`: the collection keys
        vectors by note_id alone, with no workspace scoping, and includes
        superseded notes for the same reason (see that method's docstring).
        """
        if self._notes_col is None or self._embed_fn is None:
            return 0
        with self._conn() as conn:
            rows = conn.execute("SELECT note_id, content FROM notes").fetchall()
        if not rows:
            return 0

        existing_ids: set[str] = set(self._notes_col.get(include=[])["ids"])

        ids: list[str] = []
        contents: list[str] = []
        for row in rows:
            note_id = str(row["note_id"])
            if note_id in existing_ids:
                continue  # already has a current vector — never re-embedded
            content = row["content"]
            if self._encryptor:
                try:
                    content = self._encryptor.decrypt(content)
                except Exception:
                    continue  # skip a row that can't be decrypted rather than abort the backfill
            ids.append(note_id)
            contents.append(content)
        if not contents:
            return 0

        for start in range(0, len(contents), _NOTES_REEMBED_BATCH_SIZE):
            batch_ids = ids[start:start + _NOTES_REEMBED_BATCH_SIZE]
            batch_contents = contents[start:start + _NOTES_REEMBED_BATCH_SIZE]
            vectors = self._embed_fn(batch_contents)
            self._notes_col.upsert(ids=batch_ids, embeddings=vectors)
        logger.info("vectr: backfilled %d working-memory note vector(s)", len(contents))
        return len(contents)

    def embed_model_stamp_mismatch(self) -> str | None:
        """Return the stamped embed model if it still differs from the
        configured one, else None.

        Migration runs synchronously in the constructor, so under normal
        operation this returns None by the time __init__ has returned. It
        exists so `vectr status` can surface a mid-failure state (e.g. the
        embedder was unavailable during the migration attempt) instead of
        silently masking a stale note-vector space.
        """
        if self._notes_col is None or not self._embed_model:
            return None
        stamped = self._stored_notes_embed_model()
        if stamped != self._embed_model:
            return stamped or "unknown (unstamped)"
        return None

    def boot_recall(self, workspace: str) -> list[WorkingNote]:
        """Unconditional 'boot set' for harness-injected recall (UPG-9.2).

        Returns ALL directive notes plus high-priority task notes — the
        must-never-miss memory that should reach the model every session
        regardless of the prompt. Deliberately NOT semantic and NOT gated on
        notes_count: a similarity miss on "never push to main" is unacceptable,
        and a SessionStart hook must work on a fresh (0-note) workspace without
        erroring. Returns [] when there is nothing to inject.

        Directives are returned first (they are imperatives), oldest-first so
        standing rules stay in a stable order, capped at
        config.BOOT_MAX_DIRECTIVE_NOTES. High-priority task notes follow,
        ordered newest-first — created_at DESC, note_id DESC tie-break — and
        capped at config.BOOT_MAX_TASK_NOTES (UPG-TASK-NOTE-INJECTION-RECENCY):
        a task note is current-work state, so the boot set must surface the
        latest checkpoint first rather than whichever task note happens to be
        oldest, and must not grow unbounded as task notes accumulate over a
        long-running workspace. Does NOT bump last_accessed — boot injection
        is automatic, not an agency-driven access, so it must not interfere
        with decay.
        """
        from agent.config import BOOT_MAX_DIRECTIVE_NOTES, BOOT_MAX_TASK_NOTES

        with self._conn() as conn:
            directive_rows = conn.execute(
                "SELECT * FROM notes WHERE workspace = ? AND valid_until IS NULL "
                "AND kind = 'directive' ORDER BY created_at ASC LIMIT ?",
                (workspace, BOOT_MAX_DIRECTIVE_NOTES),
            ).fetchall()
            task_rows = conn.execute(
                "SELECT * FROM notes WHERE workspace = ? AND valid_until IS NULL "
                "AND kind = 'task' AND priority = 'high' "
                "ORDER BY created_at DESC, note_id DESC LIMIT ?",
                (workspace, BOOT_MAX_TASK_NOTES),
            ).fetchall()
        notes = [self._row_to_note(r) for r in directive_rows] + \
                [self._row_to_note(r) for r in task_rows]
        audit("RECALL", workspace=workspace, query="", notes_returned=len(notes), method="boot")
        return notes

    def get_note(self, workspace: str, note_id: int) -> "WorkingNote | None":
        """Fetch a single note by ID for the expand path (UPG-RECALL-HIERARCHY).

        Returns None if the note does not exist or belongs to a different workspace.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE workspace = ? AND note_id = ?",
                (workspace, note_id),
            ).fetchone()
        return self._row_to_note(row) if row is not None else None

    def recall_for_path(
        self,
        workspace: str,
        file_path: str,
        kind: str | None = None,
        limit: int = 10,
    ) -> list[WorkingNote]:
        """Recall notes anchored to a specific file (UPG-9.6).

        Matches notes whose content mentions the file's basename or its
        workspace-relative path — the anchor a typed gotcha actually carries
        ("index_file in symbol_graph.py takes workspace first"). Combined with
        `kind="gotcha"` this powers the PreToolUse hook: editing a file surfaces
        the caveat recorded against it, and an unrelated file matches nothing.
        Not semantic — a substring anchor avoids false "nearby file" hits.
        """
        basename = Path(file_path).name
        if not basename:
            return []
        try:
            relpath = str(Path(file_path).resolve().relative_to(Path(workspace).resolve()))
        except (ValueError, OSError):
            relpath = ""

        sql = ("SELECT * FROM notes WHERE workspace = ? AND valid_until IS NULL "
               "AND (content LIKE ? OR content LIKE ?)")
        params: list = [workspace, f"%{basename}%", f"%{relpath}%" if relpath else f"%{basename}%"]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        # UPG-RECALL-ORDER-CHURN: same deterministic tie-break as recall()'s
        # default SQL path — last_accessed excluded (recall() bumps it on
        # every note it returns, which would otherwise reorder these ties on
        # the very next call). UPG-TASK-NOTE-INJECTION-RECENCY: same kind='task'
        # trust/decay exemption as recall() — see that method's docstring.
        sql += (
            " ORDER BY"
            " (CASE WHEN kind = 'task' THEN 1.0 ELSE author_trust_score END) DESC,"
            " (CASE WHEN kind = 'task' THEN 1.0 ELSE decay_score END) DESC,"
            " created_at DESC, note_id DESC LIMIT ?"
        )
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        notes = [self._row_to_note(r) for r in rows]
        audit("RECALL", workspace=workspace, query=basename, notes_returned=len(notes), method="path")
        return notes

    def forget(self, workspace: str, note_id: int) -> bool:
        """Explicitly delete a note (LLM decided it's no longer relevant)."""
        with self._conn() as conn:
            count = conn.execute(
                "DELETE FROM notes WHERE workspace = ? AND note_id = ?",
                (workspace, note_id),
            ).rowcount
        if count > 0 and self._notes_col is not None:
            try:
                self._notes_col.delete(ids=[str(note_id)])
            except Exception:
                pass
        return count > 0

    def count_notes(self, workspace: str) -> int:
        """Return the number of notes stored for this workspace."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM notes WHERE workspace = ?", (workspace,)
            ).fetchone()
        return row[0] if row else 0

    def forget_all(self, workspace: str) -> int:
        """Clear all notes AND snapshots for a workspace.

        Snapshots embed full note contents in their payload, so a purge that
        deleted only the notes table would silently keep every note's text
        alive in `snapshots` — "delete everything" must mean everything,
        including the note embedding vectors in the Chroma collection."""
        with self._conn() as conn:
            deleted = conn.execute(
                "DELETE FROM notes WHERE workspace = ?", (workspace,)
            ).rowcount
            conn.execute("DELETE FROM snapshots WHERE workspace = ?", (workspace,))
        if deleted > 0 and self._notes_col is not None:
            try:
                existing_ids = self._notes_col.get(include=[])["ids"]
                if existing_ids:
                    self._notes_col.delete(ids=existing_ids)
            except Exception:
                pass
        audit("FORGET_ALL", workspace=workspace, deleted=deleted)
        return deleted

    def forget_all_workspaces(self) -> int:
        """Delete ALL notes, snapshots, and note vectors across ALL workspaces
        in this SQLite file.

        Used by `vectr forget --all` to give a global clean slate — the same
        "everything means everything" contract as forget_all above.
        Audit entry logged per deletion.
        """
        with self._conn() as conn:
            deleted = conn.execute("DELETE FROM notes").rowcount
            conn.execute("DELETE FROM snapshots")
        if self._notes_col is not None:
            try:
                existing_ids = self._notes_col.get(include=[])["ids"]
                if existing_ids:
                    self._notes_col.delete(ids=existing_ids)
            except Exception:
                pass
        audit("FORGET_ALL_WORKSPACES", deleted=deleted)
        return deleted

    def purge_expired_notes(self, workspace: str, ttl_days: float) -> int:
        """Delete notes older than ttl_days regardless of decay_score.

        Called at startup when VECTR_NOTES_TTL_DAYS is set. Returns the number
        of notes deleted.
        """
        cutoff = time.time() - ttl_days * 86400
        with self._conn() as conn:
            deleted = conn.execute(
                "DELETE FROM notes WHERE workspace = ? AND created_at < ?",
                (workspace, cutoff),
            ).rowcount
        if deleted:
            audit("PURGE_EXPIRED", workspace=workspace, ttl_days=ttl_days, deleted=deleted)
        return deleted

    def decay_old_notes(self, workspace: str, half_life_days: float = 14.0) -> None:
        """
        Apply time-based decay to note relevance scores.
        Notes older than half_life_days have their decay_score halved.
        Notes with decay_score < 0.1 are deleted automatically.
        """
        now = time.time()
        half_life_s = half_life_days * 86400
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE notes
                SET decay_score = decay_score * pow(0.5, (? - created_at) / ?)
                WHERE workspace = ?
                """,
                (now, half_life_s, workspace),
            )
            conn.execute(
                "DELETE FROM notes WHERE workspace = ? AND decay_score < 0.1",
                (workspace,),
            )

    # ------------------------------------------------------------------
    # Snapshots — vectr_snapshot / vectr_restore
    # ------------------------------------------------------------------

    def snapshot(
        self,
        workspace: str,
        label: str,
        retrieved_chunks: list[dict] | None = None,
        session_id: str | None = None,
    ) -> str:
        """
        Save a session snapshot: all current notes + what was in context.
        Returns snapshot_id.
        """
        import hashlib
        snapshot_id = hashlib.md5(f"{workspace}{label}{time.time()}".encode()).hexdigest()[:12]
        notes = self.recall(workspace, limit=100)
        payload = json.dumps({
            "notes": [
                {
                    "note_id": n.note_id,
                    "content": n.content,
                    "tags": n.tags,
                    "priority": n.priority,
                }
                for n in notes
            ],
            "retrieved_chunks": retrieved_chunks or [],
            "session_id": session_id,
        })
        # The payload embeds decrypted note contents (recall() decrypts), so a
        # plaintext snapshots table would bypass note encryption entirely.
        # Encrypt the whole payload under the same key as note content.
        if self._encryptor:
            payload = self._encryptor.encrypt(payload)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (snapshot_id, workspace, label, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, workspace, label, payload, time.time()),
            )
        return snapshot_id

    def list_snapshots(self, workspace: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT snapshot_id, label, created_at FROM snapshots WHERE workspace = ? ORDER BY created_at DESC",
                (workspace,),
            ).fetchall()
        return [{"snapshot_id": r["snapshot_id"], "label": r["label"], "created_at": r["created_at"]} for r in rows]

    def restore_snapshot(self, snapshot_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            return None
        payload = row["payload"]
        if self._encryptor:
            # Tolerant decrypt: snapshots written before payload encryption (or
            # before a key was configured) pass through unchanged.
            payload = self._encryptor.decrypt(payload)
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            # Ciphertext without the (correct) key configured — unreadable by
            # design; treat as not restorable rather than crashing the caller.
            logger.warning("snapshot %s payload is not readable (encrypted with a different key?)", snapshot_id)
            return None

    # ------------------------------------------------------------------
    # Eviction hints — which chunks can vectr re-retrieve in <50ms?
    # ------------------------------------------------------------------

    def build_eviction_hint(
        self,
        workspace: str,
        session_retrieved_chunks: list[dict],
    ) -> str:
        """
        Given a list of chunks the LLM has retrieved this session,
        return a message listing which chunks vectr can re-retrieve in <50ms.

        The guarantee: anything listed here is fully indexed, re-retrievable in <50ms.
        """
        if not session_retrieved_chunks:
            return "No retrieved chunks to evict."

        # estimate token cost (rough: 1 token ≈ 4 chars)
        total_chars = sum(len(c.get("content", "")) for c in session_retrieved_chunks)
        est_tokens = total_chars // 4

        by_file: dict[str, list[dict]] = {}
        for chunk in session_retrieved_chunks:
            f = chunk.get("file", "unknown")
            by_file.setdefault(f, []).append(chunk)

        lines = [
            f"Vectr has {len(session_retrieved_chunks)} chunks (~{est_tokens} tokens) indexed and instantly retrievable.",
            "Vectr can re-retrieve these in <50ms — no need to re-read them:",
            "",
        ]
        for fpath, chunks in by_file.items():
            line_ranges = ", ".join(f"lines {c.get('lines', '?')}" for c in chunks)
            lines.append(f"  {fpath}  [{line_ranges}]")

        lines += [
            "",
            "To retrieve any of them: vectr_search('<symbol name or description>')",
            "Recall latency: <50ms. Nothing will be lost.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Staleness detection
    # ------------------------------------------------------------------

    def check_staleness(
        self,
        notes: list[WorkingNote],
        workspace_root: str,
    ) -> dict[int, list[str]]:
        """Identify notes whose referenced files have changed since the note was written.

        Composite staleness fires the stale flag when ANY of:
          - A referenced file's mtime > note.created_at (original mtime check)
          - note.code_hash != sha256[:16] of the current file content (code moved/changed)
          - Note is marked superseded (valid_until is set)
          - A declared anchor's (TRIGGER-ENGINE wave 1, bm2-design-skeleton.md
            §5) content hash no longer matches its hash-at-write — this is a
            VISIBLE caveat only, never a silent drop: the memory still fires/
            recalls, this dict just flags it. Reuses `_hash_path_content()`,
            the same sha256[:16] helper `remember()` uses to compute the
            anchor's original hash, so both sides apply the identical rule.

        Returns {note_id: [stale_path/reason, ...]} — only stale notes included.
        """
        root = Path(workspace_root)
        stale: dict[int, list[str]] = {}

        for note in notes:
            reasons: list[str] = []

            # superseded notes are always stale
            if note.valid_until is not None:
                sup_by = note.superseded_by or (
                    f"note#{note.superseded_by_note_id}" if note.superseded_by_note_id else "unknown"
                )
                reasons.append(f"[superseded by @{sup_by}]")

            for raw_path in _extract_file_paths(note.content):
                path = Path(raw_path)
                resolved = path if path.is_absolute() else root / path
                try:
                    stat = resolved.stat()
                except OSError:
                    continue

                # mtime staleness (original signal)
                if stat.st_mtime > note.created_at:
                    reasons.append(raw_path)

                # code_hash staleness — detect if the anchored code changed
                if note.code_hash and resolved.suffix.lower() in {".py", ".c", ".h", ".go", ".rs"}:
                    try:
                        current_hash = hashlib.sha256(
                            resolved.read_bytes()
                        ).hexdigest()[:16]
                        if current_hash != note.code_hash and raw_path not in reasons:
                            reasons.append(f"{raw_path}[code_hash_changed]")
                    except OSError:
                        pass

            # Declarative anchor re-hash (TRIGGER-ENGINE §5) — independent of
            # the content-prose path extraction above; anchors are structured
            # (path, hash) pairs set explicitly at write time.
            for anchor in note.anchors:
                if not anchor or len(anchor) < 2:
                    continue
                anchor_path, anchor_hash = anchor[0], anchor[1]
                if not anchor_path or not anchor_hash:
                    continue  # no baseline recorded at write time — nothing to compare
                current_hash = _hash_path_content(root, anchor_path)
                if current_hash is not None and current_hash != anchor_hash:
                    reasons.append(f"{anchor_path}[anchor_changed]")

            if reasons:
                stale[note.note_id] = reasons

        return stale

    def fire(
        self,
        workspace: str,
        *,
        event: str | None = None,
        file_path: str | None = None,
        ledger: "TriggerFireLedger | None" = None,
        now: float | None = None,
    ) -> list["FireResult"]:
        """Live evaluation entry point (TRIGGER-ENGINE wave 1,
        bm2-design-skeleton.md §2/§4): evaluate every non-tombstoned note in
        `workspace` against one lifecycle moment (`event` and/or `file_path`
        at time `now`), fold in `check_staleness()`'s anchor/file caveats, and
        return only the notes that fired — ordered by the single shared
        `total_order_key` (fire precedence == injection ordering == budget
        eviction order, per the design doc's "one total order").

        `ledger`, if given (a per-session `TriggerFireLedger` — see
        `VectrService`'s per-session registry, mirroring its existing
        per-session `EvictionAdvisor` pattern), suppresses a note whose
        matched trigger index already fired this session on that SAME axis; a
        fresh fire is recorded into the ledger before returning. Passing no
        ledger evaluates statelessly with no suppression (used by tests and
        any one-shot caller that manages its own dedup).

        Every note that actually fires has its `last_fired` column stamped to
        `now` — this is what makes a trigger's `cooldown` T-modifier
        meaningful on the NEXT evaluation (evaluate_note() reads `last_fired`
        directly off the note)."""
        from agent.trigger_engine import evaluate_note, total_order_key

        if now is None:
            now = time.time()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notes WHERE workspace = ? AND valid_until IS NULL",
                (workspace,),
            ).fetchall()
        notes = [self._row_to_note(row) for row in rows]
        notes_by_id = {n.note_id: n for n in notes}

        stale = self.check_staleness(notes, workspace)

        fired_ids: list[int] = []
        results = []
        for note in notes:
            result = evaluate_note(note, event=event, file_path=file_path, now=now)
            if not result.fired:
                continue
            if (
                ledger is not None
                and result.trigger_index is not None
                and not ledger.eligible(note.note_id, result.trigger_index)
            ):
                continue
            result.stale_paths = stale.get(note.note_id, [])
            results.append(result)
            fired_ids.append(note.note_id)
            if ledger is not None and result.trigger_index is not None:
                ledger.record_fire(note.note_id, result.trigger_index)

        results.sort(key=lambda r: total_order_key(notes_by_id[r.note_id]))

        if fired_ids:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE notes SET last_fired = ? WHERE note_id IN ({})".format(
                        ",".join("?" * len(fired_ids))
                    ),
                    [now] + fired_ids,
                )

        audit(
            "TRIGGER_FIRE", workspace=workspace, trigger_event=event or "",
            file_path=file_path or "", fired=len(results),
        )
        return results

    def get_author_trust(self, workspace: str, author_id: str) -> float:
        """Return the Bayesian trust score for an author in this workspace."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT trust_score FROM author_trust WHERE workspace = ? AND author_id = ?",
                (workspace, author_id),
            ).fetchone()
        return row[0] if row else 1.0

    def list_authors(self, workspace: str) -> list[dict]:
        """Return all authors with their trust scores and note counts."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT author_id, trust_score, note_count FROM author_trust WHERE workspace = ? ORDER BY trust_score DESC",
                (workspace,),
            ).fetchall()
        return [{"author_id": r[0], "trust_score": r[1], "note_count": r[2]} for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_note(self, row: sqlite3.Row) -> WorkingNote:
        content = row["content"]
        keys = row.keys()
        title = row["title"] if "title" in keys else ""
        if self._encryptor:
            content = self._encryptor.decrypt(content)
            # Tolerant decrypt: titles written before title-encryption (or before
            # encryption was enabled at all) are returned unchanged.
            title = self._encryptor.decrypt(title)
        return WorkingNote(
            note_id=row["note_id"],
            workspace=row["workspace"],
            content=content,
            tags=json.loads(row["tags"]),
            priority=row["priority"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            session_id=row["session_id"],
            decay_score=row["decay_score"],
            kind=row["kind"] if "kind" in keys else DEFAULT_KIND,
            # team notes fields (present in all new DBs; guarded for old DBs without migration)
            author_id=row["author_id"] if "author_id" in keys else "",
            author_trust_score=row["author_trust_score"] if "author_trust_score" in keys else 1.0,
            valid_from=row["valid_from"] if "valid_from" in keys else 0.0,
            valid_until=row["valid_until"] if "valid_until" in keys else None,
            code_hash=row["code_hash"] if "code_hash" in keys else "",
            superseded_by=row["superseded_by"] if "superseded_by" in keys else None,
            superseded_at=row["superseded_at"] if "superseded_at" in keys else None,
            title=title,
            # TRIGGER-ENGINE wave 1 fields — guarded for pre-migration DBs.
            triggers=json.loads(row["triggers"]) if "triggers" in keys and row["triggers"] else [],
            provenance=row["provenance"] if "provenance" in keys and row["provenance"] else DEFAULT_PROVENANCE,
            scope=row["scope"] if "scope" in keys and row["scope"] else DEFAULT_SCOPE,
            anchors=json.loads(row["anchors"]) if "anchors" in keys and row["anchors"] else [],
            supersedes=row["supersedes"] if "supersedes" in keys else None,
            superseded_by_note_id=row["superseded_by_note_id"] if "superseded_by_note_id" in keys else None,
            last_fired=row["last_fired"] if "last_fired" in keys else None,
        )

    def format_notes_for_llm(
        self,
        notes: list[WorkingNote],
        stale_warnings: dict[int, list[str]] | None = None,
        detail: str = "index",
        surface: str = "mcp",
    ) -> str:
        """Format recalled notes into a clean LLM-readable string.

        detail='index' (default, UPG-RECALL-HIERARCHY): renders ONE crisp line per note:
            [#<note_id>] <kind>/<priority> · <title>  (<relative age>)
          No body is included. Token-bounded for hook injection and default recall.
          When the note carries a caller-declared agent/subagent identifier
          (`author_id`, set via vectr_remember's optional `agent` argument —
          UPG-SUBAGENT-MEMORY), it renders as an attribution tag right after
          priority: `[#12] task/high (coder-2) · title  (2h)`. Never inferred;
          a note with no `agent` renders exactly as before this feature shipped.

        detail='full': renders the full body format (pre-existing behaviour).
          Use for explicit vectr_recall(detail='full') or single-note expand (note_id path).

        If stale_warnings is provided (full detail only), notes whose referenced files
        have changed are flagged with a [STALE] marker and a warning.

        surface='mcp' (default): the expand hint uses the MCP tool-call form
        (`vectr_recall(note_id=N)`) — correct for the MCP dispatch path, whose
        caller is an editor's LLM. surface='cli': the expand hint uses the
        actual shell form (`vectr recall --id N`) — used by the REST route
        `vectr recall`/`vectr remember` go through, whose caller is a human
        terminal (UPG-CLI-RECALL-HINT: MCP tool syntax is meaningless there).

        surface also controls how each note's id is rendered in the index
        listing (UPG-CLI-RECALL-ID-FOOTGUN): 'mcp' keeps `[#N]` — the `#` is
        harmless there since the editor's LLM never pastes it into a shell.
        'cli' renders the bare `[N]` instead — a terminal user who copies
        `[#125]` into `vectr recall #125` hits zsh's `interactive_comments`,
        which silently strips `#125` as a comment and leaves a bare
        `vectr recall` (a semantic-query no-op) with no error. The 'cli'
        header hint also names a real id from the current results
        (`vectr recall --id <that id>`) rather than a generic placeholder,
        so it is directly copy-pasteable.
        """
        if not notes:
            return "No working notes found."

        stale_warnings = stale_warnings or {}

        def _age_str(created_at: float) -> str:
            age_h = (time.time() - created_at) / 3600
            return f"{age_h:.0f}h" if age_h < 48 else f"{age_h / 24:.0f}d"

        if detail == "index":
            if surface == "mcp":
                expand_hint = "use vectr_recall(note_id=N) to expand"
            else:
                expand_hint = f"run `vectr recall --id {notes[0].note_id}` to expand"
            header = f"# Working Notes — index ({len(notes)} entries; {expand_hint})\n"
            lines = [header]
            for n in notes:
                kind_label = n.kind if n.kind else DEFAULT_KIND
                title = n.title or (n.content.strip().splitlines()[0][:80] if n.content.strip() else "(no title)")
                stale_marker = " [STALE]" if n.note_id in stale_warnings else ""
                id_str = f"#{n.note_id}" if surface == "mcp" else f"{n.note_id}"
                # UPG-SUBAGENT-MEMORY: caller-declared agent/subagent attribution
                # (author_id) — never inferred. Absent renders exactly as before.
                agent_marker = f" ({n.author_id})" if n.author_id else ""
                lines.append(
                    f"[{id_str}] {kind_label}/{n.priority}{agent_marker} · {title}"
                    f"  ({_age_str(n.created_at)}){stale_marker}"
                )
            return "\n".join(lines)

        # detail == "full" (original behaviour, with stale warnings)
        stale_count = len(stale_warnings)
        header = f"# Working Notes ({len(notes)} entries"
        if stale_count:
            header += f", {stale_count} may be stale"
        header += ")\n"

        lines = [header]
        for n in notes:
            age_str = _age_str(n.created_at) + " ago"
            tag_str = f"  [{', '.join(n.tags)}]" if n.tags else ""
            author_str = f"  @{n.author_id}" if n.author_id else ""
            stale_files = stale_warnings.get(n.note_id, [])
            stale_marker = " [STALE]" if stale_files else ""

            # superseded badge
            superseded_marker = ""
            if n.valid_until is not None:
                sup_by = n.superseded_by or (
                    f"note#{n.superseded_by_note_id}" if n.superseded_by_note_id else None
                )
                if sup_by:
                    import datetime as _dt
                    sup_date = _dt.datetime.fromtimestamp(n.superseded_at or n.valid_until).strftime("%Y-%m-%d")
                    superseded_marker = f" [superseded by @{sup_by}, {sup_date}]"

            # Surface the kind when it carries injection semantics (UPG-9.3) —
            # 'finding' is the default and adds no signal, so it's left implicit.
            kind_marker = f" [{n.kind.upper()}]" if n.kind and n.kind != DEFAULT_KIND else ""
            # Provenance class (TRIGGER-ENGINE wave 1, bm2-design-skeleton.md
            # §5) — marked on every full-tier block, unlike kind_marker above,
            # since the caller's trust posture depends on it regardless of
            # whether provenance is the default ("agent").
            provenance_marker = f" [{n.provenance}]"
            lines.append(
                f"[{n.note_id}] [{n.priority.upper()}]{kind_marker}{provenance_marker}{tag_str}{author_str}  ({age_str})"
                f"{stale_marker}{superseded_marker}"
            )
            # Provenance framing (§5): only a human-provenance directive ever
            # renders as an unhedged imperative; agent-provenance is framed as
            # memory to verify; auto-provenance carries the weakest framing.
            from agent.trigger_engine import frame_prefix
            lines.append(f"  {frame_prefix(n.provenance, n.kind)}{n.content}")
            if stale_files:
                changed = ", ".join(stale_files)
                lines.append(f"  WARNING: These files changed after this note was written: {changed}")
                lines.append(f"  WARNING: Verify this note is still accurate before relying on it.")
            lines.append("")
        return "\n".join(lines)
