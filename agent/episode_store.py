"""EpisodeStore — SQLite-backed store for L1 tool-call episodes
(memoization-l1-capture-design §2).

Structurally quarantined from working memory: episodes live in their own
`episodes` table, in the SAME db file `WorkingContextStore` already writes
(`working_context.sqlite`, one file per workspace db_dir), but through this
module's own connection. Neither `agent/searcher.py` nor
`agent/working_context_store` ever imports this module — there is no code
path from an episode row into a `vectr_search`/`vectr_recall` result or a
hook-injected context, by construction, not by an extra filter. The only
readers are `GET /v1/episodes` (`app/routes.py`) and the aggregate counts
folded into `vectr_status` (`app/service.py`).

No embedding, ever — episodes are keyed/temporal rows, not a semantic-search
corpus (memoization-l1-capture-design §2.2).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_SQLITE_BUSY_TIMEOUT_S = 30.0  # mirrors WorkingContextStore's own busy timeout


class EpisodeStore:
    def __init__(self, db_dir: str) -> None:
        self._db_path = Path(db_dir) / "working_context.sqlite"
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=_SQLITE_BUSY_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={int(_SQLITE_BUSY_TIMEOUT_S * 1000)}")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace       TEXT NOT NULL,
                    session_id      TEXT,
                    ts              REAL NOT NULL,
                    cwd             TEXT NOT NULL DEFAULT '',
                    tool            TEXT NOT NULL,
                    cmd_raw         TEXT NOT NULL DEFAULT '',
                    verb            TEXT NOT NULL DEFAULT '',
                    flags_json      TEXT NOT NULL DEFAULT '[]',
                    args_json       TEXT NOT NULL DEFAULT '[]',
                    rc              INTEGER,
                    termination     TEXT NOT NULL DEFAULT 'unknown',
                    outcome         TEXT NOT NULL DEFAULT 'unknown',
                    stdout_digest   TEXT NOT NULL DEFAULT '',
                    stderr_digest   TEXT NOT NULL DEFAULT '',
                    markers_json    TEXT NOT NULL DEFAULT '[]',
                    env_delta_names TEXT NOT NULL DEFAULT '[]',
                    file_path       TEXT,
                    arc_id          INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_episodes_workspace_ts
                    ON episodes(workspace, ts);
                CREATE INDEX IF NOT EXISTS idx_episodes_session
                    ON episodes(workspace, session_id);
                CREATE INDEX IF NOT EXISTS idx_episodes_arc
                    ON episodes(workspace, arc_id);
                """
            )

    def insert(
        self,
        workspace: str,
        *,
        session_id: str | None,
        ts: float,
        cwd: str,
        tool: str,
        cmd_raw: str,
        verb: str,
        flags: list[str],
        args: list[dict],
        rc: int | None,
        termination: str,
        outcome: str,
        stdout_digest: str,
        stderr_digest: str,
        markers_matched: list[str],
        env_delta_names: list[str],
        file_path: str | None,
        max_rows: int,
        ttl_days: float,
    ) -> int:
        """Insert one episode row and enforce retention; returns the new
        row's id. `max_rows`/`ttl_days` are passed in by the caller (from
        agent/config.py's EPISODES_MAX_ROWS / EPISODES_TTL_DAYS) rather than
        defaulted here, so this module carries no tunable of its own."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO episodes (
                    workspace, session_id, ts, cwd, tool, cmd_raw, verb,
                    flags_json, args_json, rc, termination, outcome,
                    stdout_digest, stderr_digest, markers_json,
                    env_delta_names, file_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace, session_id, ts, cwd, tool, cmd_raw, verb,
                    json.dumps(flags), json.dumps(args), rc, termination, outcome,
                    stdout_digest, stderr_digest, json.dumps(markers_matched),
                    json.dumps(env_delta_names), file_path,
                ),
            )
            episode_id = cur.lastrowid
        self._enforce_retention(workspace, max_rows=max_rows, ttl_days=ttl_days)
        return episode_id

    def _enforce_retention(self, workspace: str, *, max_rows: int, ttl_days: float) -> None:
        """Per-workspace ring buffer (keep the newest `max_rows`) + TTL
        (drop rows older than `ttl_days`). Both are cheap, indexed deletes —
        the table never grows past `max_rows` + 1 between enforcements."""
        cutoff = time.time() - (ttl_days * 86400)
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM episodes WHERE workspace = ? AND ts < ?",
                (workspace, cutoff),
            )
            conn.execute(
                """
                DELETE FROM episodes WHERE workspace = ? AND id NOT IN (
                    SELECT id FROM episodes WHERE workspace = ?
                    ORDER BY ts DESC LIMIT ?
                )
                """,
                (workspace, workspace, max_rows),
            )

    def list_episodes(
        self,
        workspace: str,
        *,
        session_id: str | None = None,
        arc_id: int | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Newest-first episode rows for `workspace`, optionally filtered.
        The only non-status reader of this table (GET /v1/episodes) —
        deliberately never touched by search/recall code."""
        clauses = ["workspace = ?"]
        params: list = [workspace]
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if arc_id is not None:
            clauses.append("arc_id = ?")
            params.append(arc_id)
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
        if until_ts is not None:
            clauses.append("ts <= ?")
            params.append(until_ts)
        where = " AND ".join(clauses)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM episodes WHERE {where} ORDER BY ts DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def count_episodes(self, workspace: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM episodes WHERE workspace = ?", (workspace,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def count_arcs_pending_distill(self, workspace: str) -> int:
        """Best-effort count of arcs awaiting L3 distillation, from the
        `arcs` table a separate lane (arc detection, §3) owns. This lane
        does not create that table or define what "pending" means for it —
        structurally absent (0, never an error) until it exists, matching
        the spec's "reads an arcs table count if present, else 0"
        instruction. Reconcile the exact WHERE clause with the arc
        detector's real schema once both land."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM arcs WHERE workspace = ?", (workspace,)
                ).fetchone()
            return int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            return 0  # no `arcs` table (or schema mismatch) yet


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["flags"] = json.loads(d.pop("flags_json") or "[]")
    d["args"] = json.loads(d.pop("args_json") or "[]")
    d["markers_matched"] = json.loads(d.pop("markers_json") or "[]")
    d["env_delta_names"] = json.loads(d.pop("env_delta_names") or "[]")
    return d
