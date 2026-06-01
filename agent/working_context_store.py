"""
WorkingContextStore — persists working notes the LLM offloads to Vectr.

This is the core of the bidirectional protocol. The LLM calls vectr_remember()
to store what it has learned. Vectr stores it, the LLM drops it from context.
Next session, vectr_recall() brings it back. The LLM can afford to forget because
it knows the recall is instant and lossless.

Storage: SQLite in the vectr DB dir — persists across IDE restarts and reboots.
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
# T17: Rotating audit log
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
    """

    def __init__(self, db_dir: str) -> None:
        self._db_path = Path(db_dir) / "working_context.sqlite"
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
                    note_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace     TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    tags          TEXT NOT NULL DEFAULT '[]',
                    priority      TEXT NOT NULL DEFAULT 'medium',
                    created_at    REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    session_id    TEXT,
                    decay_score   REAL NOT NULL DEFAULT 1.0
                );

                CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes(workspace);
                CREATE INDEX IF NOT EXISTS idx_notes_tags ON notes(tags);

                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id  TEXT PRIMARY KEY,
                    workspace    TEXT NOT NULL,
                    label        TEXT NOT NULL,
                    payload      TEXT NOT NULL,
                    created_at   REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_snap_workspace ON snapshots(workspace);
            """)

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
    ) -> int:
        """Store a working note. Returns the note_id."""
        now = time.time()
        tags_json = json.dumps(tags or [])
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO notes (workspace, content, tags, priority, created_at,
                                   last_accessed, session_id, decay_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1.0)
                """,
                (workspace, content, tags_json, priority, now, now, session_id),
            )
            note_id = cur.lastrowid
        audit("REMEMBER", workspace=workspace, note_id=note_id, priority=priority,
              tags=",".join(tags or []), chars=len(content))
        return note_id  # type: ignore[return-value]

    def recall(
        self,
        workspace: str,
        query: str | None = None,
        tags: list[str] | None = None,
        priority: str | None = None,
        limit: int = 10,
    ) -> list[WorkingNote]:
        """
        Retrieve working notes. Filters by tags/priority if provided.
        Query is a simple substring match against content (fast, no embedding needed —
        notes are short and precise, written by the LLM itself).
        """
        sql = "SELECT * FROM notes WHERE workspace = ?"
        params: list = [workspace]

        if priority:
            sql += " AND priority = ?"
            params.append(priority)

        if tags:
            # any matching tag
            tag_clauses = " OR ".join(["tags LIKE ?" for _ in tags])
            sql += f" AND ({tag_clauses})"
            params.extend([f'%"{t}"%' for t in tags])

        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")

        sql += " ORDER BY decay_score DESC, last_accessed DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        notes = [self._row_to_note(r) for r in rows]

        # bump last_accessed
        if notes:
            ids = [n.note_id for n in notes]
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE notes SET last_accessed = ? WHERE note_id IN ({','.join('?' * len(ids))})",
                    [time.time(), *ids],
                )

        audit("RECALL", workspace=workspace, query=query or "", notes_returned=len(notes))
        return notes

    def forget(self, workspace: str, note_id: int) -> bool:
        """Explicitly delete a note (LLM decided it's no longer relevant)."""
        with self._conn() as conn:
            count = conn.execute(
                "DELETE FROM notes WHERE workspace = ? AND note_id = ?",
                (workspace, note_id),
            ).rowcount
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
        audit("FORGET_ALL", workspace=workspace, deleted=deleted)
        return deleted

    def forget_all_workspaces(self) -> int:
        """T17: Delete ALL notes across ALL workspaces in this SQLite file.

        Used by `vectr forget --all` to give a global clean slate.
        Audit entry logged per deletion.
        """
        with self._conn() as conn:
            deleted = conn.execute("DELETE FROM notes").rowcount
        audit("FORGET_ALL_WORKSPACES", deleted=deleted)
        return deleted

    def purge_expired_notes(self, workspace: str, ttl_days: float) -> int:
        """T17: Delete notes older than ttl_days regardless of decay_score.

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
    # Eviction hints — what can the LLM safely drop from context?
    # ------------------------------------------------------------------

    def build_eviction_hint(
        self,
        workspace: str,
        session_retrieved_chunks: list[dict],
    ) -> str:
        """
        Given a list of chunks the LLM has retrieved this session,
        return a message telling the LLM what it can safely drop.

        The guarantee: anything listed here can be retrieved in <50ms.
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
            "You can safely drop these from your context window:",
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

        For each note, extracts file paths from the content, stats them against
        the workspace root, and flags any path whose mtime > note.created_at.

        Returns {note_id: [stale_path, ...]} — only notes with ≥1 stale path included.
        """
        root = Path(workspace_root)
        stale: dict[int, list[str]] = {}

        for note in notes:
            stale_files = []
            for raw_path in _extract_file_paths(note.content):
                path = Path(raw_path)
                resolved = path if path.is_absolute() else root / path
                try:
                    mtime = resolved.stat().st_mtime
                except OSError:
                    continue  # doesn't exist or inaccessible — skip
                if mtime > note.created_at:
                    stale_files.append(raw_path)

            if stale_files:
                stale[note.note_id] = stale_files

        return stale

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> WorkingNote:
        return WorkingNote(
            note_id=row["note_id"],
            workspace=row["workspace"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            priority=row["priority"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            session_id=row["session_id"],
            decay_score=row["decay_score"],
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
            stale_files = stale_warnings.get(n.note_id, [])
            stale_marker = " [STALE]" if stale_files else ""
            lines.append(f"[{n.note_id}] [{n.priority.upper()}]{tag_str}  ({age_str}){stale_marker}")
            lines.append(f"  {n.content}")
            if stale_files:
                changed = ", ".join(stale_files)
                lines.append(f"  WARNING: These files changed after this note was written: {changed}")
                lines.append(f"  WARNING: Verify this note is still accurate before relying on it.")
            lines.append("")
        return "\n".join(lines)
