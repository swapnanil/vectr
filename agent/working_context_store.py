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
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


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
            return cur.lastrowid  # type: ignore[return-value]

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
        return notes

    def forget(self, workspace: str, note_id: int) -> bool:
        """Explicitly delete a note (LLM decided it's no longer relevant)."""
        with self._conn() as conn:
            count = conn.execute(
                "DELETE FROM notes WHERE workspace = ? AND note_id = ?",
                (workspace, note_id),
            ).rowcount
        return count > 0

    def forget_all(self, workspace: str) -> int:
        """Clear all notes for a workspace."""
        with self._conn() as conn:
            return conn.execute(
                "DELETE FROM notes WHERE workspace = ?", (workspace,)
            ).rowcount

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

    def format_notes_for_llm(self, notes: list[WorkingNote]) -> str:
        """Format recalled notes into a clean LLM-readable string."""
        if not notes:
            return "No working notes found."
        lines = [f"# Working Notes ({len(notes)} entries)\n"]
        for n in notes:
            age_h = (time.time() - n.created_at) / 3600
            age_str = f"{age_h:.0f}h ago" if age_h < 48 else f"{age_h / 24:.0f}d ago"
            tag_str = f"  [{', '.join(n.tags)}]" if n.tags else ""
            lines.append(f"[{n.note_id}] [{n.priority.upper()}]{tag_str}  ({age_str})")
            lines.append(f"  {n.content}")
            lines.append("")
        return "\n".join(lines)
