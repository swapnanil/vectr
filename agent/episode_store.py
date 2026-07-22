"""EpisodeStore — SQLite-backed store for tool-call episodes.

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
corpus.

This module also owns the `arcs` table: one row per chain/diff/confidence/cwd
for each `app.arcs.ArcDetector`-emitted discovery moment (`app/service.py`'s
`record_episode`/`_persist_arc`), quarantined exactly like `episodes` —
episode capture itself never writes notes, and an arc row is still never a
semantic-search or note-store input.
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

                CREATE TABLE IF NOT EXISTS arcs (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace                TEXT NOT NULL,
                    session_id               TEXT NOT NULL,
                    cwd                      TEXT NOT NULL DEFAULT '',
                    ts                       REAL NOT NULL,
                    confidence               TEXT NOT NULL DEFAULT 'normal',
                    mutation_diff_json       TEXT NOT NULL DEFAULT '{}',
                    failure_episode_ids_json TEXT NOT NULL DEFAULT '[]',
                    success_episode_id       INTEGER,
                    distilled_at             REAL
                );

                CREATE INDEX IF NOT EXISTS idx_arcs_workspace_ts
                    ON arcs(workspace, ts);
                """
            )
            self._migrate_arcs_columns(conn)

    def _migrate_arcs_columns(self, conn: sqlite3.Connection) -> None:
        """Additive migration: a pre-existing `arcs` table created
        before the distillation write-back columns existed gets them
        added via `ALTER TABLE ... ADD COLUMN`
        (nullable, no rewrite). `PRAGMA table_info` makes this tolerant of
        both an already-migrated table and a brand-new one — always safe
        to call unconditionally from `_init_db`."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(arcs)")}
        if "distilled_note_id" not in existing:
            conn.execute("ALTER TABLE arcs ADD COLUMN distilled_note_id INTEGER")
        if "dismissed_reason" not in existing:
            conn.execute("ALTER TABLE arcs ADD COLUMN dismissed_reason TEXT")

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
        args: list[str],
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

    def insert_arc(
        self,
        workspace: str,
        *,
        session_id: str,
        cwd: str,
        ts: float,
        confidence: str,
        mutation_diff: dict,
        failure_episode_ids: list[int],
        success_episode_id: int | None,
    ) -> int:
        """Insert one `app.arcs.ArcDetector`-emitted arc — a discovery
        moment (one or more failed attempts resolved by a success),
        quarantined in its own `arcs` table exactly like `episodes` (never
        notes, never embedded). Returns the new row's id."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO arcs (
                    workspace, session_id, cwd, ts, confidence,
                    mutation_diff_json, failure_episode_ids_json,
                    success_episode_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace, session_id, cwd, ts, confidence,
                    json.dumps(mutation_diff), json.dumps(failure_episode_ids),
                    success_episode_id,
                ),
            )
            return cur.lastrowid

    def mark_episode_arc(self, episode_id: int, arc_id: int) -> None:
        """Stamp `arc_id` onto one episode row (called once per episode
        that participated in a just-emitted arc) so `list_episodes(arc_id=
        ...)` and the `episodes` table's own `idx_episodes_arc` index can
        find every episode belonging to a discovered discovery moment."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE episodes SET arc_id = ? WHERE id = ?", (arc_id, episode_id)
            )

    def count_arcs_pending_distill(self, workspace: str) -> int:
        """Count of `workspace`'s arcs not yet distilled (`distilled_at
        IS NULL`) — folded into `vectr_status`'s `arcs_pending_distill`
        field. The `arcs` table is always created by `_init_db`; the
        try/except remains only as a defensive guard against a
        pre-existing db file created before the `arcs` table existed,
        never a normal-path outcome."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM arcs "
                    "WHERE workspace = ? AND distilled_at IS NULL",
                    (workspace,),
                ).fetchone()
            return int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            return 0  # no `arcs` table (or schema mismatch) — defensive only

    def list_arcs(
        self,
        workspace: str,
        *,
        status: str = "pending",
        limit: int = 100,
    ) -> list[dict]:
        """Arc rows for `workspace`, joined with their episodes' summary
        fields — per arc: id/ts/cwd/confidence, the failure chain (each
        failure episode's verb/outcome/matched markers), the mutation diff,
        and the success episode's verb/command. `status` selects `pending`
        (`distilled_at IS NULL`, the default), `resolved` (`distilled_at
        IS NOT NULL`), or `all`. Ordered confidence-first (`normal` before
        any other value) then oldest-first — the order `vectr_distill()`
        renders pending arcs in for review."""
        if status not in ("pending", "resolved", "all"):
            raise ValueError(f"unknown status: {status!r}")
        clauses = ["workspace = ?"]
        params: list = [workspace]
        if status == "pending":
            clauses.append("distilled_at IS NULL")
        elif status == "resolved":
            clauses.append("distilled_at IS NOT NULL")
        where = " AND ".join(clauses)
        out: list[dict] = []
        with self._conn() as conn:
            arc_rows = conn.execute(
                f"SELECT * FROM arcs WHERE {where} "
                "ORDER BY (confidence != 'normal'), ts ASC LIMIT ?",
                (*params, limit),
            ).fetchall()
            for arc in arc_rows:
                failure_ids = json.loads(arc["failure_episode_ids_json"] or "[]")
                failures = []
                for fid in failure_ids:
                    erow = conn.execute(
                        "SELECT id, verb, outcome, markers_json FROM episodes WHERE id = ?",
                        (fid,),
                    ).fetchone()
                    if erow is not None:
                        failures.append({
                            "episode_id": erow["id"],
                            "verb": erow["verb"],
                            "outcome": erow["outcome"],
                            "markers_matched": json.loads(erow["markers_json"] or "[]"),
                        })
                success = None
                if arc["success_episode_id"] is not None:
                    srow = conn.execute(
                        "SELECT id, verb, cmd_raw FROM episodes WHERE id = ?",
                        (arc["success_episode_id"],),
                    ).fetchone()
                    if srow is not None:
                        success = {
                            "episode_id": srow["id"],
                            "verb": srow["verb"],
                            "cmd_raw": srow["cmd_raw"],
                        }
                out.append({
                    "id": arc["id"],
                    "ts": arc["ts"],
                    "cwd": arc["cwd"],
                    "confidence": arc["confidence"],
                    "mutation_diff": json.loads(arc["mutation_diff_json"] or "{}"),
                    "failures": failures,
                    "success": success,
                    "distilled_at": arc["distilled_at"],
                    "distilled_note_id": arc["distilled_note_id"],
                    "dismissed_reason": arc["dismissed_reason"],
                })
        return out

    def resolve_arcs_distilled(self, workspace: str, arc_ids: list[int], note_id: int) -> dict:
        """Mark `arc_ids` as distilled into note `note_id` (the
        `vectr_remember(..., distilled_from=[...])` write-back)."""
        return self._resolve_arcs(
            workspace, arc_ids, distilled_note_id=note_id, dismissed_reason=None,
        )

    def resolve_arcs_dismissed(self, workspace: str, arc_ids: list[int], reason: str) -> dict:
        """Mark `arc_ids` as dismissed with `reason` (the
        `vectr_distill(dismiss=[...], reason=...)` write-back)."""
        return self._resolve_arcs(
            workspace, arc_ids, distilled_note_id=None, dismissed_reason=reason,
        )

    def _resolve_arcs(
        self,
        workspace: str,
        arc_ids: list[int],
        *,
        distilled_note_id: int | None,
        dismissed_reason: str | None,
    ) -> dict:
        """Idempotent bulk resolve shared by both write-back verdicts: sets
        `distilled_at = now` plus exactly one of `distilled_note_id` /
        `dismissed_reason` on each row in `arc_ids` that exists in
        `workspace` and is still pending (`distilled_at IS NULL`) — the
        audit invariant (a resolved arc always shows HOW it resolved, never
        both or neither). Ids that don't exist or are already resolved are
        returned in `unresolved`, never raised as an error."""
        now = time.time()
        resolved: list[int] = []
        unresolved: list[int] = []
        with self._conn() as conn:
            for arc_id in arc_ids:
                row = conn.execute(
                    "SELECT id FROM arcs WHERE id = ? AND workspace = ? AND distilled_at IS NULL",
                    (arc_id, workspace),
                ).fetchone()
                if row is None:
                    unresolved.append(arc_id)
                    continue
                conn.execute(
                    "UPDATE arcs SET distilled_at = ?, distilled_note_id = ?, "
                    "dismissed_reason = ? WHERE id = ?",
                    (now, distilled_note_id, dismissed_reason, arc_id),
                )
                resolved.append(arc_id)
        return {"resolved": resolved, "unresolved": unresolved}


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["flags"] = json.loads(d.pop("flags_json") or "[]")
    d["args"] = json.loads(d.pop("args_json") or "[]")
    d["markers_matched"] = json.loads(d.pop("markers_json") or "[]")
    d["env_delta_names"] = json.loads(d.pop("env_delta_names") or "[]")
    return d
