"""
WorkingContextStore — persists working notes the LLM saves to Vectr.

This is the core of the bidirectional protocol. The LLM calls vectr_remember()
to store what it has learned. Vectr stores it persistently for fast recall.
vectr_recall() brings it back on demand — later in this session or in a future
one. Recall is instant (<50ms) and lossless.

Storage: SQLite in the vectr DB dir — available immediately within the same
session and persists across IDE restarts and reboots.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Rotating audit log
#
# Logs remember/recall/index/forget events to ~/.vectr/audit.log.
# Rotates at 10 MB; keeps 3 backups. Disabled if VECTR_AUDIT_LOG="" is set.
# ---------------------------------------------------------------------------

def _get_audit_logger() -> logging.Logger:
    """Return the vectr audit logger (lazy-initialised, singleton per process)."""
    name = "vectr.audit"
    log = logging.getLogger(name)
    if log.handlers:
        return log

    log_path_str = os.getenv("VECTR_AUDIT_LOG", str(Path.home() / ".vectr" / "audit.log"))
    if not log_path_str:
        log.addHandler(logging.NullHandler())
        return log

    log_path = Path(log_path_str)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        str(log_path), maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def audit(event: str, **kwargs) -> None:
    """Write one audit log entry: `event key=val key=val …`"""
    try:
        parts = [event] + [f"{k}={v}" for k, v in kwargs.items()]
        _get_audit_logger().info(" ".join(parts))
    except Exception:
        pass  # audit failures must never crash the main path


# ---------------------------------------------------------------------------
# Field-level encryption for note content
#
# When VECTR_ENCRYPT_KEY is set, note content is encrypted at write time and
# decrypted at read time using Fernet (AES-128-CBC + HMAC-SHA256).
# The raw passphrase is never stored — a PBKDF2-derived key is used instead.
#
# Requires: pip install vectr[encryption]  (cryptography>=43)
#
# If a note was stored plaintext before encryption was enabled, decrypt()
# detects the invalid token and returns the raw text — no data loss.
# ---------------------------------------------------------------------------

class _NoteEncryptor:
    """Fernet-based field-level encryptor for note content."""

    _SALT = b"vectr-notes-v1\x00"  # fixed, non-secret derivation salt

    def __init__(self, passphrase: str) -> None:
        import base64
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._SALT,
            iterations=480_000,  # OWASP 2023 recommendation for SHA-256
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, stored: str) -> str:
        """Decrypt, or return plaintext unchanged if the value was never encrypted."""
        try:
            return self._fernet.decrypt(stored.encode("ascii")).decode("utf-8")
        except Exception:
            # Note was stored before encryption was enabled — return as-is
            return stored


def _build_encryptor() -> _NoteEncryptor | None:
    """Return a _NoteEncryptor if VECTR_ENCRYPT_KEY is set, else None."""
    key = os.getenv("VECTR_ENCRYPT_KEY", "")
    return _NoteEncryptor(key) if key else None

# Matches file paths in note text — relative (foo/bar.py) and absolute (/usr/local/file.py).
# False positives that don't exist are skipped during staleness stat().
_FILE_PATH_RE = re.compile(
    r'(?<![:/\w])'                                         # not preceded by :, /, or word char
    r'((?:/[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*)+'              # absolute: /foo/bar/baz
    r'|[a-zA-Z0-9_.][a-zA-Z0-9_./\-]*(?:/[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*)+)'  # relative: foo/bar
)


def _extract_file_paths(text: str) -> list[str]:
    """Extract plausible file paths from note text (deduplicated, order-preserving)."""
    seen: set[str] = set()
    result = []
    for raw in _FILE_PATH_RE.findall(text):
        path = raw.rstrip("/.")
        if len(path) > 3 and path not in seen:
            seen.add(path)
            result.append(path)
    return result


# Memory kinds (UPG-9.3). Mirrors the disk-memory split that makes CLAUDE.md
# (unconditional directives) distinct from MEMORY.md (relevance-ranked learnings):
#   directive — must-never-miss rules; injected unconditionally at SessionStart.
#   task      — current-work context; injected in the SessionStart boot set.
#   gotcha    — file/path-anchored caveats; injected at PreToolUse + semantic recall.
#   finding   — relevance-ranked learnings; injected per-prompt at UserPromptSubmit.
#   reference — pointers (URLs/tickets); surfaced on demand only.
VALID_KINDS: tuple[str, ...] = ("directive", "task", "gotcha", "finding", "reference")
DEFAULT_KIND = "finding"


@dataclass
class WorkingNote:
    note_id: int
    workspace: str
    content: str
    tags: list[str]
    priority: str          # "high" | "medium" | "low"
    created_at: float
    last_accessed: float
    session_id: str | None = None
    decay_score: float = 1.0
    kind: str = DEFAULT_KIND  # directive | task | gotcha | finding | reference (UPG-9.3)
    # team/shared notes tri-key model
    author_id: str = ""              # developer/agent identifier
    author_trust_score: float = 1.0  # Bayesian weight per contributor (0.0–1.0)
    valid_from: float = 0.0          # bi-temporal: when the note became valid
    valid_until: float | None = None # bi-temporal: None = still valid; float = superseded
    code_hash: str = ""              # sha256[:16] of the anchored code block at write time
    superseded_by: str | None = None  # author_id that superseded this note
    superseded_at: float | None = None


@dataclass
class SnapshotEntry:
    snapshot_id: str
    workspace: str
    label: str
    notes: list[WorkingNote]
    retrieved_chunks: list[dict]   # {file, lines, symbol, content} of what was in context
    created_at: float


class WorkingContextStore:
    """
    SQLite-backed store for LLM working notes and session snapshots.

    Design principle: the LLM should never be afraid to forget something
    if Vectr has it. This store is the guarantee.

    When embed_fn and notes_chroma_client are provided, note content is embedded
    at remember() time and stored in a ChromaDB 'working_memory' collection.
    recall(query=...) then uses cosine similarity to find relevant notes instead
    of SQL LIKE substring matching. SQL LIKE is retained as a fallback.
    """

    def __init__(
        self,
        db_dir: str,
        embed_fn=None,
        notes_chroma_client=None,
    ) -> None:
        self._db_path = Path(db_dir) / "working_context.sqlite"
        self._encryptor: _NoteEncryptor | None = _build_encryptor()
        # Semantic recall: embed notes at write time, cosine search at recall time
        self._embed_fn = embed_fn   # Callable[[list[str]], list[list[float]]] | None
        self._notes_col = None
        if embed_fn is not None and notes_chroma_client is not None:
            try:
                self._notes_col = notes_chroma_client.get_or_create_collection(
                    name="working_memory",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception:
                pass  # embedding unavailable — fall back to SQL LIKE silently
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
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
    ) -> int:
        """Store a working note. Returns the note_id.

        If code_hash is provided and another non-superseded note exists for the
        same workspace + code_hash (same code anchor), the older note is marked
        superseded before the new note is inserted.

        `kind` is one of VALID_KINDS (directive|task|gotcha|finding|reference);
        an unrecognised value falls back to DEFAULT_KIND.
        """
        now = time.time()
        tags_json = json.dumps(tags or [])
        if kind not in VALID_KINDS:
            kind = DEFAULT_KIND
        stored_content = self._encryptor.encrypt(content) if self._encryptor else content

        with self._conn() as conn:
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
                                   valid_until, code_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, 1.0, ?, NULL, ?)
                """,
                (workspace, stored_content, tags_json, priority, kind, now, now, session_id,
                 author_id, now, code_hash),
            )
            note_id = cur.lastrowid

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
              tags=",".join(tags or []), chars=len(content))
        return note_id  # type: ignore[return-value]

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
    ) -> list[WorkingNote]:
        """Retrieve working notes.

        When query is provided and semantic search is available (embed_fn + ChromaDB
        collection configured), uses cosine similarity to rank notes by relevance.
        Falls back to SQL LIKE substring match when semantic search is unavailable.

        Superseded notes are excluded by default. Pass include_superseded=True
        to see the full history including notes marked as superseded.
        Without a query, results are ordered by author_trust_score DESC, decay_score DESC,
        last_accessed DESC so the highest-trust contributor's notes surface first.
        """
        # Semantic path: embed the query, find cosine-nearest notes, then fetch from SQLite.
        if query and self._notes_col is not None and self._embed_fn is not None:
            try:
                notes = self._semantic_recall(
                    workspace, query, tags, priority, limit, include_superseded, kind,
                    min_similarity,
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

        sql += " ORDER BY author_trust_score DESC, decay_score DESC, last_accessed DESC LIMIT ?"
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
    ) -> list[WorkingNote]:
        """Find the most relevant notes by cosine similarity, then fetch from SQLite.

        When min_similarity is set (UPG-5.1), candidates whose cosine similarity
        falls below the floor are dropped, so an off-topic query recalls nothing
        instead of the nearest-but-irrelevant note. ChromaDB cosine distance is
        `1 - cosine_similarity`, so similarity = `1 - distance`.
        """
        # Cap n_results at collection size to avoid ChromaDB errors on small collections
        col_count = self._notes_col.count()
        if col_count == 0:
            return []
        n_query = min(limit * 3, col_count)

        q_vec = self._embed_fn([query])[0]
        results = self._notes_col.query(query_embeddings=[q_vec], n_results=n_query)

        if not results or not results["ids"] or not results["ids"][0]:
            return []

        raw_ids = results["ids"][0]
        distances = (results.get("distances") or [[None] * len(raw_ids)])[0]
        candidate_ids = [int(id_) for id_ in raw_ids]

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

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        # Preserve semantic rank order (ChromaDB returns by ascending distance)
        id_to_row = {r["note_id"]: r for r in rows}
        ordered = [id_to_row[nid] for nid in candidate_ids if nid in id_to_row][:limit]
        notes = [self._row_to_note(r) for r in ordered]

        if notes:
            ids = [n.note_id for n in notes]
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE notes SET last_accessed = ? WHERE note_id IN ({','.join('?' * len(ids))})",
                    [time.time(), *ids],
                )

        return notes

    def boot_recall(self, workspace: str) -> list[WorkingNote]:
        """Unconditional 'boot set' for harness-injected recall (UPG-9.2).

        Returns ALL directive notes plus high-priority task notes — the
        must-never-miss memory that should reach the model every session
        regardless of the prompt. Deliberately NOT semantic and NOT gated on
        notes_count: a similarity miss on "never push to main" is unacceptable,
        and a SessionStart hook must work on a fresh (0-note) workspace without
        erroring. Returns [] when there is nothing to inject.

        Directives are ordered first (they are imperatives), then high tasks,
        each oldest-first so standing rules stay in a stable order. Does NOT
        bump last_accessed — boot injection is automatic, not an agency-driven
        access, so it must not interfere with decay.
        """
        sql = (
            "SELECT * FROM notes WHERE workspace = ? AND valid_until IS NULL "
            "AND (kind = 'directive' OR (kind = 'task' AND priority = 'high')) "
            "ORDER BY CASE kind WHEN 'directive' THEN 0 ELSE 1 END, created_at ASC "
            "LIMIT 100"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (workspace,)).fetchall()
        notes = [self._row_to_note(r) for r in rows]
        audit("RECALL", workspace=workspace, query="", notes_returned=len(notes), method="boot")
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
        """Clear all notes for a workspace."""
        with self._conn() as conn:
            deleted = conn.execute(
                "DELETE FROM notes WHERE workspace = ?", (workspace,)
            ).rowcount
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
        """Delete ALL notes across ALL workspaces in this SQLite file.

        Used by `vectr forget --all` to give a global clean slate.
        Audit entry logged per deletion.
        """
        with self._conn() as conn:
            deleted = conn.execute("DELETE FROM notes").rowcount
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
        return json.loads(row["payload"])

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

        Returns {note_id: [stale_path/reason, ...]} — only stale notes included.
        """
        import hashlib
        root = Path(workspace_root)
        stale: dict[int, list[str]] = {}

        for note in notes:
            reasons: list[str] = []

            # superseded notes are always stale
            if note.valid_until is not None:
                sup_by = note.superseded_by or "unknown"
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

            if reasons:
                stale[note.note_id] = reasons

        return stale

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
        if self._encryptor:
            content = self._encryptor.decrypt(content)
        keys = row.keys()
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
        )

    def format_notes_for_llm(
        self,
        notes: list[WorkingNote],
        stale_warnings: dict[int, list[str]] | None = None,
    ) -> str:
        """Format recalled notes into a clean LLM-readable string.

        If stale_warnings is provided, notes whose referenced files have changed
        since the note was written are flagged with a [STALE] marker and a warning
        listing which files changed.
        """
        if not notes:
            return "No working notes found."

        stale_warnings = stale_warnings or {}
        stale_count = len(stale_warnings)
        header = f"# Working Notes ({len(notes)} entries"
        if stale_count:
            header += f", {stale_count} may be stale"
        header += ")\n"

        lines = [header]
        for n in notes:
            age_h = (time.time() - n.created_at) / 3600
            age_str = f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h / 24:.0f}d ago"
            tag_str = f"  [{', '.join(n.tags)}]" if n.tags else ""
            author_str = f"  @{n.author_id}" if n.author_id else ""
            stale_files = stale_warnings.get(n.note_id, [])
            stale_marker = " [STALE]" if stale_files else ""

            # superseded badge
            superseded_marker = ""
            if n.valid_until is not None and n.superseded_by:
                import datetime as _dt
                sup_date = _dt.datetime.fromtimestamp(n.superseded_at or n.valid_until).strftime("%Y-%m-%d")
                superseded_marker = f" [superseded by @{n.superseded_by}, {sup_date}]"

            # Surface the kind when it carries injection semantics (UPG-9.3) —
            # 'finding' is the default and adds no signal, so it's left implicit.
            kind_marker = f" [{n.kind.upper()}]" if n.kind and n.kind != DEFAULT_KIND else ""
            lines.append(
                f"[{n.note_id}] [{n.priority.upper()}]{kind_marker}{tag_str}{author_str}  ({age_str})"
                f"{stale_marker}{superseded_marker}"
            )
            lines.append(f"  {n.content}")
            if stale_files:
                changed = ", ".join(stale_files)
                lines.append(f"  WARNING: These files changed after this note was written: {changed}")
                lines.append(f"  WARNING: Verify this note is still accurate before relying on it.")
            lines.append("")
        return "\n".join(lines)
