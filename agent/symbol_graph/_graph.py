"""
SQLite-backed SymbolGraph: locate, trace, format, and persistence.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Literal

from agent.symbol_graph._types import Symbol, LocateResult, CallEdge
from agent.symbol_graph._constants import (
    SNIPPET_LINES,
    SYMBOL_SCHEMA_VERSION,
    _KIND_RANK,
    _KIND_RANK_DEFAULT,
    _BUILTINS,
    graph_toolchain_fingerprint,
)
from agent.config import LOCATE_LARGE_SPAN_THRESHOLD, LOCATE_SMALL_SPAN_THRESHOLD

logger = logging.getLogger(__name__)


def _levenshtein(a: str, b: str) -> int:
    """Compute edit distance between two strings."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def _get_imported_files(caller_file: str, workspace: str) -> list[str]:
    """Parse caller_file's import statements and return workspace-resident file paths it imports."""
    workspace_path = Path(workspace)
    caller_path = Path(caller_file)
    try:
        src = caller_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    # Build stem → path index for workspace Python files
    name_index: dict[str, str] = {}
    for f in workspace_path.rglob("*.py"):
        name_index[f.stem] = str(f)
        try:
            rel = f.relative_to(workspace_path)
            key = str(rel).replace("/", ".").replace("\\", ".").removesuffix(f.suffix)
            name_index[key] = str(f)
        except ValueError:
            pass

    _PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.MULTILINE)
    imported: list[str] = []
    for m in _PY_IMPORT.finditer(src):
        parts = m.group(1).split(".")
        for length in range(len(parts), 0, -1):
            candidate = ".".join(parts[:length])
            if candidate in name_index:
                resolved = name_index[candidate]
                if resolved != str(caller_path):
                    imported.append(resolved)
                break

    return list(dict.fromkeys(imported))  # dedup while preserving order


_CLASS_DEF_RE = re.compile(r"^class\s+(\w+)")

# UPG-15.10: detect enclosing function/method scopes for locate scope-depth ranking.
# Matches `def ` and `async def ` at the start of a stripped line so indented
# method defs inside a class are correctly identified as function scopes.
_DEF_LINE_RE = re.compile(r"^(def |async def )")


def _locate_scope_depth_from_lines(lines: list[str], start_line: int) -> int:
    """Count how many enclosing function/method (def) scopes surround the symbol
    at *start_line* (1-indexed) within a pre-read *lines* list.

    A top-level class definition has scope_depth=0; a class defined inside a test
    method body (e.g. ``def test_invalid_model(self): class Model(...): ...``) has
    scope_depth=1; a class inside a nested function has scope_depth=2; etc.

    Algorithm: walk backward from the symbol's line, collecting the set of distinct
    indentation levels at which a ``def``/``async def`` appears.  Each distinct
    indent level that is strictly less than the symbol's indent level and was not
    yet "consumed" by a shallower ancestor counts as one enclosing function scope.
    The fast path for indent=0 avoids any scanning (top-level symbols can never be
    inside a function).

    This function is called once per unique (file_path, start_line) pair per
    locate_l2 call via ``_locate_scope_depth_batch`` — not once per sort comparison.
    """
    if not lines or start_line < 1:
        return 0
    sym_idx = min(start_line - 1, len(lines) - 1)
    sym_line = lines[sym_idx]
    sym_indent = len(sym_line) - len(sym_line.lstrip())
    if sym_indent == 0:
        return 0  # top-level: fast path, cannot be inside any function

    # Collect distinct def-indentation levels that are strictly less than sym_indent
    # and appear before the symbol in the file.  Each distinct level represents one
    # enclosing function scope (e.g. a class method at indent=4 + a module-level
    # def at indent=0 → two distinct enclosing def levels → depth=2).
    enclosing_def_indents: set[int] = set()
    for idx in range(sym_idx - 1, -1, -1):
        line = lines[idx]
        stripped = line.lstrip()
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        if indent < sym_indent and _DEF_LINE_RE.match(stripped):
            enclosing_def_indents.add(indent)
    return len(enclosing_def_indents)


def _locate_scope_depth_batch(rows: list) -> list[int]:
    """Pre-compute scope depths for all rows in one pass, caching file reads.

    Returns a list of integers parallel to *rows*: ``depths[i]`` is the scope
    depth (number of enclosing function scopes) of ``rows[i]``.  Each source file
    is read at most once so the cost is O(unique_files × file_size), not
    O(rows × file_size).  Unreadable files silently return depth 0.
    """
    file_lines: dict[str, list[str]] = {}
    depths: list[int] = []
    for r in rows:
        fp = r["file_path"]
        if fp not in file_lines:
            try:
                file_lines[fp] = Path(fp).read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()
            except OSError:
                file_lines[fp] = []
        depths.append(_locate_scope_depth_from_lines(file_lines[fp], r["start_line"]))
    return depths


def _enclosing_class_from_lines(lines: list[str], start_line: int) -> str:
    """Scan backward from *start_line* (1-indexed) in a pre-read *lines* list to
    find the immediately enclosing class definition.

    Returns the class name string, or ``""`` when the symbol is not inside a
    class (e.g. a module-level function).  Only examines lines that are at a
    *lesser indentation level* than the symbol's first line, so nested methods
    are correctly attributed to their direct parent class rather than a grandparent.

    Split from ``_enclosing_class_from_file`` (UPG-15.10x/F49) so a batch caller
    can supply already-read lines once per file rather than re-reading it.
    """
    if not lines or start_line < 1:
        return ""

    # 0-indexed line of the symbol itself
    sym_idx = min(start_line - 1, len(lines) - 1)
    sym_line = lines[sym_idx]
    sym_indent = len(sym_line) - len(sym_line.lstrip())

    # Walk backward looking for a class definition at a strictly lesser indent
    for idx in range(sym_idx - 1, -1, -1):
        line = lines[idx]
        stripped = line.lstrip()
        if not stripped:
            continue  # blank line — keep scanning
        indent = len(line) - len(stripped)
        if indent >= sym_indent:
            continue  # same or deeper indent — not a parent scope
        m = _CLASS_DEF_RE.match(stripped)
        if m:
            return m.group(1)
        # Once we find a non-blank line at lesser indent that is NOT a class,
        # we've exited the potential enclosing class scope.
        break

    return ""


def _enclosing_class_from_file(file_path: str, start_line: int) -> str:
    """Scan *file_path* for the class immediately enclosing the symbol at
    *start_line* (1-indexed).  See ``_enclosing_class_from_lines`` for the
    scanning algorithm; this wrapper reads the file for one-off callers.

    This complements ``extract_class_from_content`` (which reads the
    ``# class: X`` prefix that the *indexer* injects into ChromaDB chunks).
    For the symbol-graph locate surface, snippets come directly from raw file
    content and carry no such prefix — hence the file-scan path here.
    """
    try:
        lines = Path(file_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    return _enclosing_class_from_lines(lines, start_line)


def _locate_class_enclosed_batch(rows: list) -> list[bool]:
    """Pre-compute, for each row, whether the symbol's IMMEDIATE enclosing scope
    is a class body (used by the F49 locate ranking signal below). Each source
    file is read at most once — mirrors ``_locate_scope_depth_batch``'s caching.

    A method directly inside a class (e.g. ``class ForNode: def render(self):``)
    returns True; a module-level function (e.g. ``def render(request, ...):``)
    returns False. A symbol whose immediate enclosing scope is a function body
    (e.g. a class defined inside a test method, per UPG-15.10/F29) also returns
    False here — that case is already penalised by ``scope_depth`` and
    ``test_penalty``; this signal targets the orthogonal "bare function vs.
    class method" collision that scope_depth alone does not discriminate,
    because Python's tree-sitter grammar assigns both the same symbol kind
    (`function`), and even languages with a distinct `method` kind rank it
    equal to `function` in `_KIND_RANK` (a method is a legitimate top-level
    result, e.g. `locate('save')` on a single canonical model).
    """
    file_lines: dict[str, list[str]] = {}
    result: list[bool] = []
    for r in rows:
        fp = r["file_path"]
        if fp not in file_lines:
            try:
                file_lines[fp] = Path(fp).read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()
            except OSError:
                file_lines[fp] = []
        result.append(bool(_enclosing_class_from_lines(file_lines[fp], r["start_line"])))
    return result


# UPG-15.16: SQL pre-ranking order applied BEFORE the exact/suffix LIMIT cap so
# the canonical definition of a frequently-defined name enters the candidate pool
# that _partial_match_key then ranks. In any large codebase a common class or
# function name can have more exact matches than the cap — typically many
# test/fixture stubs plus one real library definition. Without ordering, SQLite
# returns an arbitrary rowid-ordered subset that can omit the real definition
# entirely, and that subset shifts across re-indexes as rowids change, so locate
# is both wrong and non-deterministic. Order non-test files first (tests/ dirs and
# test_*.py basenames), then larger line-span first (real defs tend to be large,
# stubs tiny); _partial_match_key does the precise ranking within the cap.
# (Witnessed by the django benchmark corpus: the name "Model" has 236 matches,
# mostly inner test classes, so the library definition fell outside a 200 cap.)
_CANONICAL_FETCH_ORDER = (
    "ORDER BY (CASE WHEN file_path LIKE '%/tests/%' "
    "OR file_path LIKE '%/test\\_%' ESCAPE '\\' THEN 1 ELSE 0 END), "
    "(end_line - start_line) DESC"
)


def _partial_match_key(
    row, query_lower: str, scope_depth: int = 0, class_enclosed: bool = False,
) -> tuple:
    """Sort key for `locate` matches (used in every strategy via ``_ranked_result``).

    Ordering, most→least preferred (lower tuple = better rank):
      1. match position — exact (case-insensitive) > prefix > interior substring
      2. canonical kind — def > impl/alias (see _KIND_RANK)
      3. not a test/private file
      4. fewer enclosing function scopes — top-level (0) beats inner test class (1+)
         (UPG-15.10: discriminates inner test-scope stubs from canonical library defs)
      5. not enclosed in a class — a bare module-level definition beats a
         same-named class method (UPG-15.10x/F49: `shortcuts.render` — a short,
         module-level function — must beat `ForNode.render`, a same-named method
         whose larger body would otherwise win on span alone; placed BEFORE span
         so a small canonical function is never outranked by a bigger method
         look-alike). A no-op when every same-name candidate is a method (e.g.
         `locate('save')` across many model classes) — falls through to span_bucket.
      6. larger line span bucket — large (0) > medium (1) > tiny stub (2)
         (UPG-15.10: canonical 1400-line base class beats 3-line test stub)
      7. shorter name (closer to the query)
      8. file_path (stable tiebreak)
    """
    name = row["name"]
    nl = name.lower()
    if nl == query_lower:
        pos = 0
    elif nl.startswith(query_lower):
        pos = 1
    else:
        pos = 2
    kind_rank = _KIND_RANK.get(row["kind"], _KIND_RANK_DEFAULT)
    fp = row["file_path"]
    fp_low = fp.replace("\\", "/").lower()
    segments = fp_low.split("/")
    base = segments[-1]
    stem = base.rsplit(".", 1)[0]
    # Test-file / private-file detection must look at the basename and exact path
    # segments only — substring "test" in a path (e.g. pytest tmp dirs, a
    # "my_test_project" root) must NOT penalise an otherwise-canonical symbol.
    is_test = (
        stem.startswith(("test_", "test-")) or stem in ("test", "tests")
        or stem.endswith(("_test", ".test", ".spec", "_spec"))
        or any(seg in ("test", "tests", "testing", "__tests__") for seg in segments[:-1])
    )
    is_private = base.startswith("_")
    test_penalty = 1 if (is_test or is_private) else 0
    # UPG-15.10: line-span bucket — larger is more canonical.
    # ``row`` may be a sqlite3.Row (no .get()) or a plain dict; handle both.
    try:
        span = max(0, int(row["end_line"] or 0) - int(row["start_line"] or 0))
    except (KeyError, TypeError, IndexError):
        span = 0  # row missing line fields (synthetic test rows) → treat as tiny
    if span >= LOCATE_LARGE_SPAN_THRESHOLD:
        span_bucket = 0   # large canonical definition
    elif span >= LOCATE_SMALL_SPAN_THRESHOLD:
        span_bucket = 1   # medium
    else:
        span_bucket = 2   # tiny stub (inner test class, single-line function, etc.)
    # UPG-15.10: scope_depth — 0 = top-level, 1+ = nested inside function body.
    # Capped at 3 so deeply-nested test utilities don't dominate the key ordering.
    scope_penalty = min(scope_depth, 3)
    # UPG-15.10x/F49: class_enclosed — 0 = bare module-level def, 1 = class method.
    class_penalty = 1 if class_enclosed else 0
    return (pos, kind_rank, test_penalty, scope_penalty, class_penalty, span_bucket, len(name), fp)


# ---------------------------------------------------------------------------
# SQLite-backed SymbolGraph
# ---------------------------------------------------------------------------

class SymbolGraph:
    """
    Persistent symbol and call graph store.

    Answers:
      locate(name)  → which files define or reference a symbol
      callers(name) → which symbols call this one
      callees(name) → which symbols this one calls
    """

    def __init__(self, db_dir: str) -> None:
        self._db_path = Path(db_dir) / "symbol_graph.sqlite"
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace   TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    kind        TEXT NOT NULL,
                    file_path   TEXT NOT NULL,
                    start_line  INTEGER NOT NULL,
                    end_line    INTEGER NOT NULL,
                    description TEXT,
                    indexed_at  REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sym_workspace ON symbols(workspace);
                CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file_path);

                CREATE TABLE IF NOT EXISTS edges (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace    TEXT NOT NULL,
                    from_file    TEXT NOT NULL,
                    from_symbol  TEXT NOT NULL,
                    from_line    INTEGER NOT NULL,
                    to_symbol    TEXT NOT NULL,
                    edge_type    TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_edge_workspace ON edges(workspace);
                CREATE INDEX IF NOT EXISTS idx_edge_from ON edges(from_symbol);
                CREATE INDEX IF NOT EXISTS idx_edge_to ON edges(to_symbol);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_unique
                    ON edges(workspace, from_file, from_symbol, from_line, to_symbol, edge_type);

                CREATE TABLE IF NOT EXISTS graph_meta (
                    workspace    TEXT NOT NULL,
                    key          TEXT NOT NULL,
                    value        TEXT,
                    PRIMARY KEY (workspace, key)
                );

                CREATE TABLE IF NOT EXISTS symbol_importance (
                    workspace   TEXT NOT NULL,
                    file_path   TEXT NOT NULL,
                    score       REAL NOT NULL,
                    PRIMARY KEY (workspace, file_path)
                );
            """)

    # ------------------------------------------------------------------
    # Build / update
    # ------------------------------------------------------------------

    def index_file(self, workspace: str, file_path: str) -> int:
        """
        Index one file: extract symbols and call edges, store in DB.
        Returns number of symbols found.
        Replaces any previous index for this file.

        Calls extract_symbols_from_file through the package namespace so that
        test-time monkeypatching of agent.symbol_graph.extract_symbols_from_file
        is reflected here (identical to the original flat-module behaviour).
        """
        import agent.symbol_graph as _sg
        symbols, edges = _sg.extract_symbols_from_file(file_path)
        now = time.time()

        with self._conn() as conn:
            # delete previous entries for this file
            conn.execute(
                "DELETE FROM symbols WHERE workspace = ? AND file_path = ?",
                (workspace, file_path),
            )
            conn.execute(
                "DELETE FROM edges WHERE workspace = ? AND from_file = ?",
                (workspace, file_path),
            )

            for s in symbols:
                conn.execute(
                    """
                    INSERT INTO symbols (workspace, name, kind, file_path, start_line, end_line, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (workspace, s["name"], s["kind"], file_path,
                     s["start_line"], s["end_line"], now),
                )

            for e in edges:
                conn.execute(
                    """
                    INSERT INTO edges (workspace, from_file, from_symbol, from_line, to_symbol, edge_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (workspace, e["from_file"], e["from_symbol"],
                     e["from_line"], e["to_symbol"], e["edge_type"]),
                )

        return len(symbols)

    def delete_file(self, workspace: str, file_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM symbols WHERE workspace = ? AND file_path = ?",
                (workspace, file_path),
            )
            conn.execute(
                "DELETE FROM edges WHERE workspace = ? AND from_file = ?",
                (workspace, file_path),
            )
            conn.execute(
                "DELETE FROM symbol_importance WHERE workspace = ? AND file_path = ?",
                (workspace, file_path),
            )

    def build_for_workspace(
        self, workspace: str, file_paths: list[str], embed_model: str = "",
    ) -> dict:
        """
        Index all files in a workspace. Called after the main vector index is built.
        Returns {"symbols": int, "edges": int, "files": int, "failed": int, "complete": bool}

        Per-file resilient (UPG-8.7): a file that raises during extraction (e.g. a
        pathological AST that hits the recursion guard, an unreadable file) is
        skipped and counted — it can no longer abort the whole loop and silently
        leave every *later* file without symbols (the real cause of the observed
        "5531 symbols across 154 files" partial graph). After the build, the
        toolchain fingerprint + completeness are stamped so an upgrade is
        detectable and a partial build is never mistaken for a trustworthy one.
        """
        total_symbols = 0
        failed: list[str] = []
        for fp in file_paths:
            try:
                total_symbols += self.index_file(workspace, fp)
            except Exception:
                failed.append(fp)
                logger.warning("Symbol extraction failed for %s — skipped", fp, exc_info=True)

        with self._conn() as conn:
            edge_count = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE workspace = ?", (workspace,)
            ).fetchone()[0]

        # ARCH-1a: compute file-level PageRank importance and persist it.
        self._compute_and_store_importance(workspace)

        if failed:
            logger.warning(
                "Symbol graph: %d/%d files failed extraction (e.g. %s) — graph is PARTIAL",
                len(failed), len(file_paths), ", ".join(Path(f).name for f in failed[:3]),
            )

        complete = not failed
        self._write_meta(workspace, {
            "fingerprint": graph_toolchain_fingerprint(embed_model),
            "schema_version": str(SYMBOL_SCHEMA_VERSION),
            "embed_model": embed_model,
            "files": str(len(file_paths)),
            "symbols": str(total_symbols),
            "failed": str(len(failed)),
            "complete": "1" if complete else "0",
            "built_at": str(time.time()),
        })

        return {
            "symbols": total_symbols, "edges": edge_count, "files": len(file_paths),
            "failed": len(failed), "complete": complete,
        }

    # ------------------------------------------------------------------
    # Build metadata / version stamp (UPG-8.7)
    # ------------------------------------------------------------------

    def _write_meta(self, workspace: str, meta: dict[str, str]) -> None:
        with self._conn() as conn:
            for k, v in meta.items():
                conn.execute(
                    "INSERT INTO graph_meta (workspace, key, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(workspace, key) DO UPDATE SET value = excluded.value",
                    (workspace, k, v),
                )

    def graph_meta(self, workspace: str) -> dict[str, str]:
        """Stored build stamp for this workspace ({} if never built)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM graph_meta WHERE workspace = ?", (workspace,)
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def is_stale(self, workspace: str, embed_model: str = "") -> bool:
        """True if the persisted graph was built by a different toolchain
        (vectr upgrade / parser change / model change) or left incomplete, so a
        full rebuild is warranted. A never-built graph is stale. (UPG-8.7)"""
        meta = self.graph_meta(workspace)
        if not meta:
            return True
        if meta.get("complete") != "1":
            return True
        return meta.get("fingerprint") != graph_toolchain_fingerprint(embed_model)

    # ------------------------------------------------------------------
    # ARCH-1a: file-level PageRank importance
    # ------------------------------------------------------------------

    def _compute_and_store_importance(self, workspace: str) -> None:
        """Compute file-level PageRank over the def<->ref graph and persist scores.

        Algorithm (mirrors pagerank_spike.py):
          1. Build a leaf-name -> set(defining file_path) map from symbols.
          2. For each edge, look up the leaf of to_symbol; distribute weight
             1/|defs| to each defining file (skip self-edges).
          3. Run power-iteration PageRank (damping=0.85, 60 iterations).
          4. Normalize scores to [0,1] by dividing by the max score.
          5. Bulk-insert into symbol_importance (delete-first for idempotency).

        Pure stdlib — no numpy/networkx/scipy.
        """
        import collections

        with self._conn() as conn:
            # 1. leaf name -> set of defining file_paths
            name_to_files: dict[str, set[str]] = collections.defaultdict(set)
            for name, fp in conn.execute(
                "SELECT name, file_path FROM symbols WHERE workspace = ?", (workspace,)
            ):
                leaf = name.split(".")[-1]
                name_to_files[leaf].add(fp)

            # All distinct files that have at least one symbol
            files: set[str] = set(
                fp for (fp,) in conn.execute(
                    "SELECT DISTINCT file_path FROM symbols WHERE workspace = ?", (workspace,)
                )
            )

            if not files:
                # No symbols → nothing to compute; clear any stale rows.
                conn.execute(
                    "DELETE FROM symbol_importance WHERE workspace = ?", (workspace,)
                )
                return

            # 2. Build weighted file->file adjacency: out[from_file][to_file] += weight
            out: dict[str, dict[str, float]] = collections.defaultdict(
                lambda: collections.defaultdict(float)
            )
            for from_file, to_symbol in conn.execute(
                "SELECT from_file, to_symbol FROM edges WHERE workspace = ?", (workspace,)
            ):
                leaf = to_symbol.split(".")[-1]
                defs = name_to_files.get(leaf)
                if not defs:
                    continue
                w = 1.0 / len(defs)
                for df in defs:
                    if df == from_file:
                        continue  # skip self-edges
                    out[from_file][df] += w

        # 3. Power-iteration PageRank (damping=0.85, 60 iterations)
        nodes = list(files)
        N = len(nodes)
        d = 0.85

        # Pre-compute total out-weight per node
        outw: dict[str, float] = {f: sum(t.values()) for f, t in out.items()}

        # Initialize uniform PageRank
        pr: dict[str, float] = {f: 1.0 / N for f in nodes}

        for _ in range(60):
            new: dict[str, float] = {f: (1.0 - d) / N for f in nodes}

            # Dangling nodes: files with no outgoing edges contribute their PR
            # mass redistributed uniformly (standard dangling-node handling).
            dangling = 0.0
            for f in nodes:
                if outw.get(f, 0.0) == 0.0:
                    dangling += pr[f]
            dshare = d * dangling / N

            for f, targets in out.items():
                if f not in pr:
                    # from_file has no symbols (not in the nodes set) — skip
                    continue
                base = d * pr[f] / outw[f]
                for tf, w in targets.items():
                    if tf in new:
                        new[tf] += base * w

            for f in nodes:
                new[f] += dshare

            # Re-normalize to sum=1 to keep numerical stability
            s = sum(new.values())
            pr = {f: v / s for f, v in new.items()}

        # 4. Normalize to [0,1] by dividing by max score
        max_score = max(pr.values()) if pr else 0.0
        if max_score <= 0.0:
            # Degenerate: uniform graph or single node — store nothing meaningful
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM symbol_importance WHERE workspace = ?", (workspace,)
                )
            return

        normalized: dict[str, float] = {f: v / max_score for f, v in pr.items()}

        # 5. Persist: delete old rows first, then bulk-insert in one transaction.
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM symbol_importance WHERE workspace = ?", (workspace,)
            )
            conn.executemany(
                "INSERT INTO symbol_importance (workspace, file_path, score) VALUES (?, ?, ?)",
                ((workspace, fp, score) for fp, score in normalized.items()),
            )

        logger.debug(
            "ARCH-1a importance: workspace=%s files=%d max_raw=%.6f",
            workspace, N, max_score,
        )

    def file_importance(self, workspace: str) -> dict[str, float]:
        """Return {file_path: score} for all files in *workspace* where score is
        the normalized (0,1] file-level PageRank importance computed at index time.

        Returns an empty dict if importance has not been computed yet (e.g. the
        workspace was indexed before ARCH-1a, or contains no symbols).

        This is the read API consumed by ARCH-1b (searcher reranking) — do not
        wire into the searcher here; expose only.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT file_path, score FROM symbol_importance WHERE workspace = ?",
                (workspace,),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def symbol_count(self, workspace: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE workspace = ?", (workspace,)
            ).fetchone()[0]

    def ingest_trace_data(
        self,
        workspace: str,
        trace_events: list[dict],
    ) -> dict:
        """Ingest runtime trace events and add dynamic call edges to the graph.

        Accepts a list of trace event dicts. Recognised fields:
          caller      — name of the calling function/symbol (required)
          callee      — name of the called function/symbol (required)
          caller_file — source file of the caller (optional, empty string if unknown)
          caller_line — line number of the call site (optional, 0 if unknown)

        Dynamic edges use edge_type="dynamic" to distinguish them from static
        analysis edges. This bridges the dynamic dispatch gap: calls via
        __getattr__, decorators, dependency injection, etc. that static analysis
        misses are captured here.

        Returns {"ingested": int, "skipped_invalid": int}.
        """
        ingested = 0
        skipped = 0

        with self._conn() as conn:
            for ev in trace_events:
                caller = str(ev.get("caller", "")).strip()
                callee = str(ev.get("callee", "")).strip()
                if not caller or not callee:
                    skipped += 1
                    continue

                caller_file = str(ev.get("caller_file", "")).strip()
                caller_line = int(ev.get("caller_line", 0))

                conn.execute(
                    """
                    INSERT OR IGNORE INTO edges
                        (workspace, from_file, from_symbol, from_line, to_symbol, edge_type)
                    VALUES (?, ?, ?, ?, ?, 'dynamic')
                    """,
                    (workspace, caller_file, caller, caller_line, callee),
                )
                ingested += 1

        logger.info(
            "ingest_traces: %d edges added, %d skipped (workspace=%s)",
            ingested, skipped, workspace,
        )
        return {"ingested": ingested, "skipped_invalid": skipped}

    # ------------------------------------------------------------------
    # Query: locate
    # ------------------------------------------------------------------

    def locate(self, workspace: str, name: str, limit: int = 10) -> list[Symbol]:
        """
        Find where a symbol is defined. Supports partial match.
        Returns definition sites (start_line of the defining node).
        """
        sql = """
            SELECT * FROM symbols
            WHERE workspace = ? AND name LIKE ?
            ORDER BY
                CASE WHEN name = ? THEN 0 ELSE 1 END,
                length(name),
                file_path
            LIMIT ?
        """
        pattern = f"%{name}%"
        with self._conn() as conn:
            rows = conn.execute(sql, (workspace, pattern, name, limit)).fetchall()
        symbols = [self._row_to_symbol(r) for r in rows]
        for sym in symbols:
            sym.snippet = self.get_snippet(sym.file_path, sym.start_line, sym.end_line)
        logger.debug("locate '%s': %d results", name, len(symbols))
        return symbols

    def locate_l2(
        self,
        workspace: str,
        name: str,
        limit: int = 10,
        caller_file: str | None = None,
    ) -> LocateResult:
        """
        Multi-strategy L2 call resolution. Falls back through 5 strategies
        when exact name match fails.

        Strategies tried in order:
          0 exact       — name = ?
          1 suffix      — strip qualifier prefix (module.Foo → Foo)
          2 same_module — symbols in same directory as caller_file
          3 import_chain— symbols in files imported by caller_file
          4 substring   — name contained as substring, ranked canonical-first
                          (prefix > interior, def > impl/alias) — UPG-4.5
          5 fuzzy       — edit distance ≤ length-scaled threshold, last resort

        UPG-11.10-b: when *name* contains a class qualifier (``"Class.method"``
        form), strategies 1–5 use the bare leaf for the DB lookup and then
        filter the results to symbols whose enclosing class matches the
        qualifier.  The qualified ``Class.method`` form is also populated on
        the returned symbol's ``name`` field whenever the symbol lives inside
        a class (parity with the searcher's qualified display).  Bare-leaf
        queries (no ``.``) are unchanged.
        """
        # UPG-11.10-b: detect "Class.method" qualifier in the query.
        # We re-use the same [.:] split convention used elsewhere in the codebase.
        _class_qualifier: str = ""   # e.g. "Field"  (empty = unqualified query)
        _leaf: str = name            # e.g. "deconstruct" (the bare DB lookup key)
        if "." in name or ":" in name:
            import re as _re
            parts = _re.split(r"[.:]", name, maxsplit=1)
            if len(parts) == 2 and parts[0] and parts[1]:
                # Only treat as a class-qualified locate when the LHS looks like
                # a class name (starts with uppercase) or is an explicit
                # Class.method form (two non-empty parts).  This avoids treating
                # dotted module paths like "os.path" as class qualifiers when
                # the user really wants "module.Foo" suffix-stripping (strategy 1).
                # Heuristic: if LHS is UpperCamelCase, treat as class qualifier;
                # otherwise fall through to existing suffix-strip strategy 1.
                if parts[0][0].isupper():
                    _class_qualifier = parts[0]
                    _leaf = parts[1]

        def _with_snippets(rows: list) -> list[Symbol]:
            syms = [self._row_to_symbol(r) for r in rows]
            for s in syms:
                s.snippet = self.get_snippet(s.file_path, s.start_line, s.end_line)
                # UPG-11.10-b: populate qualified name on the symbol so callers
                # see "Class.method" rather than bare "method" — parity with
                # the searcher's qualified display via extract_class_from_content.
                if "." not in s.name and "::" not in s.name:
                    cls = _enclosing_class_from_file(s.file_path, s.start_line)
                    if cls:
                        s.name = f"{cls}.{s.name}"
            return syms

        def _filter_by_class(syms: list[Symbol]) -> list[Symbol]:
            """Keep only symbols whose enclosing class matches *_class_qualifier*.
            Returns *syms* unmodified when no qualifier is active."""
            if not _class_qualifier:
                return syms
            return [
                s for s in syms
                # After _with_snippets, qualified symbols have "Class.method"
                if s.name.split(".")[0] == _class_qualifier
                or s.name.split("::")[0] == _class_qualifier
            ]

        name_lower = name.lower()
        leaf_lower = _leaf.lower()

        def _ranked_result(rows: list, strategy: str) -> LocateResult:
            # Canonical-first ordering (UPG-4.5 + UPG-15.10 + UPG-15.10x/F49): even
            # within an exact-name hit, lead with the type/fn definition and bury
            # impl blocks / aliases / class-method look-alikes that share the name.
            # Pre-compute scope depths and class-enclosure (each a file read, cached
            # per file) so the sort key includes both signals without re-reading files
            # per comparison.
            scope_depths = _locate_scope_depth_batch(rows)
            class_enclosed = _locate_class_enclosed_batch(rows)
            ranked = sorted(
                range(len(rows)),
                key=lambda i: _partial_match_key(
                    rows[i], leaf_lower, scope_depths[i], class_enclosed[i],
                ),
            )
            top_rows = [rows[i] for i in ranked[:limit]]
            syms = _with_snippets(top_rows)
            filtered = _filter_by_class(syms)
            # When the qualifier is active and all results were filtered out,
            # fall through (return None so the caller tries the next strategy).
            if _class_qualifier and not filtered:
                return None  # type: ignore[return-value]
            return LocateResult(
                symbols=filtered if _class_qualifier else syms,
                resolution_strategy=strategy,
                query=name,
            )

        # Strategy 0: exact name match (uses _leaf when qualifier is active)
        lookup_name = _leaf if _class_qualifier else name
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name = ? "
                + _CANONICAL_FETCH_ORDER + " LIMIT ?",
                (workspace, lookup_name, 200),
            ).fetchall()
        if rows:
            result = _ranked_result(rows, "exact")
            if result is not None:
                return result

        # Strategy 1: suffix match — strip qualifier prefix (e.g. "module.Foo" → "Foo").
        # When a class qualifier was already parsed above, strategy 1 is skipped
        # (the leaf is already extracted) to avoid double-stripping.
        if not _class_qualifier:
            suffix = name
            for sep in (":", "."):
                if sep in name:
                    suffix = name.rsplit(sep, 1)[-1]
                    break
            if suffix != name:
                with self._conn() as conn:
                    rows = conn.execute(
                        "SELECT * FROM symbols WHERE workspace = ? AND name = ? "
                        + _CANONICAL_FETCH_ORDER + " LIMIT ?",
                        (workspace, suffix, 200),
                    ).fetchall()
                if rows:
                    result = _ranked_result(rows, "suffix")
                    if result is not None:
                        return result

        # Strategy 2: same-module — symbols in the same directory as caller_file
        if caller_file:
            caller_dir = str(Path(caller_file).parent)
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                    "AND file_path LIKE ? LIMIT ?",
                    (workspace, f"%{lookup_name}%", f"{caller_dir}/%", 200),
                ).fetchall()
            if rows:
                result = _ranked_result(rows, "same_module")
                if result is not None:
                    return result

        # Strategy 3: import-chain — symbols in files imported by caller_file
        if caller_file:
            imported = _get_imported_files(caller_file, workspace)
            if imported:
                ph = ", ".join("?" * len(imported))
                with self._conn() as conn:
                    rows = conn.execute(
                        f"SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                        f"AND file_path IN ({ph}) LIMIT ?",
                        (workspace, f"%{lookup_name}%", *imported, 200),
                    ).fetchall()
                if rows:
                    result = _ranked_result(rows, "import_chain")
                    if result is not None:
                        return result

        # Strategy 4: substring — any symbol whose name contains the query. Always
        # fires when there's at least one match, so fuzzy is a true last resort.
        # Prefix matches lead interior ones, and canonical defs lead impls/aliases
        # (UPG-4.5: `rand` → randint/randfraction before any fuzzy junk). SQL surfaces
        # prefix matches first into the fetch cap; _partial_match_key does the rest.
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name LIKE ? "
                "ORDER BY (CASE WHEN name LIKE ? THEN 0 ELSE 1 END), length(name) LIMIT ?",
                (workspace, f"%{lookup_name}%", f"{lookup_name}%", 200),
            ).fetchall()
        if rows:
            result = _ranked_result(rows, "substring")
            if result is not None:
                return result

        # Strategy 5: fuzzy — edit distance within a length-scaled threshold, and
        # only against names that share the first character. Short queries get a
        # tighter budget so `rand` (len 4) can't match `nan`/`add` (UPG-4.5).
        max_dist = 1 if len(lookup_name) <= 4 else 2
        first = leaf_lower[0] if leaf_lower else ""
        with self._conn() as conn:
            all_rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND ABS(LENGTH(name) - ?) <= ?",
                (workspace, len(lookup_name), max_dist),
            ).fetchall()
        fuzzy = [
            r for r in all_rows
            if r["name"] and r["name"][0].lower() == first
            and _levenshtein(leaf_lower, r["name"].lower()) <= max_dist
        ]
        if fuzzy:
            fuzzy.sort(key=lambda r: (_levenshtein(leaf_lower, r["name"].lower()), len(r["name"])))
            syms = _with_snippets(fuzzy[:limit])
            filtered = _filter_by_class(syms)
            if filtered or not _class_qualifier:
                return LocateResult(
                    symbols=filtered if _class_qualifier else syms,
                    resolution_strategy="fuzzy",
                    query=name,
                )

        return LocateResult(symbols=[], resolution_strategy="none", query=name)

    # ------------------------------------------------------------------
    # Query: trace (call graph)
    # ------------------------------------------------------------------

    # UPG-4.2: pull a wide candidate set so dedup + relevance ranking happens
    # over ALL edges, not a pre-truncated alphabetical slice (the old
    # `ORDER BY name LIMIT 20` dropped important callees by name, not relevance).
    _EDGE_FETCH_CAP = 1000

    def _edges(
        self,
        workspace: str,
        column: str,
        name: str,
        group: Literal["from_symbol", "to_symbol"],
        limit: int,
        rank_repo_defined: bool,
        include_builtins: bool = True,
        exclude_uses: bool = False,
    ) -> tuple[list[CallEdge], int]:
        """Fetch edges by exact `column` match; fall back to partial (LIKE) only
        when no exact-named edge exists. Exact-first kills the substring
        conflation that merged unrelated symbols — `trace compare` no longer
        pulls in `compare_stacks` / `_Py_atomic_compare_exchange_*` (UPG-4.1).
        Results are deduped and ranked by relevance, then truncated (UPG-4.2).
        Returns `(edges, hidden_builtins)` — the count of builtin/stdlib callees
        suppressed before truncation when `include_builtins` is False (UPG-4.3).
        `exclude_uses` drops type-usage edges (UPG-4.4) — set on the callees
        direction so "Calls:" stays function calls, not the types a function
        mentions; left off for callers so `trace <Type>` finds its usage sites.
        `column` is a fixed internal literal, never user input."""
        uses_clause = " AND edge_type != 'uses'" if exclude_uses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM edges WHERE workspace = ? AND {column} = ?{uses_clause} LIMIT ?",
                (workspace, name, self._EDGE_FETCH_CAP),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    f"SELECT * FROM edges WHERE workspace = ? AND {column} LIKE ?{uses_clause} LIMIT ?",
                    (workspace, f"%{name}%", self._EDGE_FETCH_CAP),
                ).fetchall()
        edges = [self._row_to_edge(r) for r in rows]
        return self._aggregate_edges(workspace, edges, group, limit, rank_repo_defined, include_builtins)

    @staticmethod
    def _is_builtin_call(name: str, from_file: str, repo: set[str]) -> bool:
        """A callee is builtin noise only if it's a known language builtin AND not
        defined as a symbol in this repo (so a repo's own `len`/`map` stays). The
        language is inferred from the calling file's extension (UPG-4.3)."""
        if name in repo:
            return False
        from agent.indexer import LANG_BY_EXT
        lang = LANG_BY_EXT.get(Path(from_file).suffix.lower(), "")
        return name in _BUILTINS.get(lang, frozenset())

    def _aggregate_edges(
        self,
        workspace: str,
        edges: list[CallEdge],
        group: Literal["from_symbol", "to_symbol"],
        limit: int,
        rank_repo_defined: bool,
        include_builtins: bool = True,
    ) -> tuple[list[CallEdge], int]:
        """Collapse edges that share the same caller/callee name into one entry
        carrying a `call_count` of distinct call sites, then rank by relevance
        — repo-defined first (callees only), then call frequency, then name —
        and truncate to `limit`. Replaces the alphabetical-then-truncate path so
        important, repeatedly-called targets survive the cut (UPG-4.2).

        When `not include_builtins` (callee path only), language-builtin/stdlib
        callees are dropped *before* truncation so they can't push repo-internal
        calls out of the window; returns the count hidden (UPG-4.3). Callers are
        never filtered — a caller is by definition a repo-defined function."""
        groups: dict[str, dict] = {}
        for e in edges:
            k = e.from_symbol if group == "from_symbol" else e.to_symbol
            site = (e.from_file, e.from_line, e.to_symbol)
            g = groups.get(k)
            if g is None:
                groups[k] = {"edge": e, "sites": {site}}
            else:
                g["sites"].add(site)
        # Repo-defined ranking only matters for callees (the *from_symbol* of a
        # caller is by definition a function in this repo). Skipping the lookup
        # for callers also avoids a needless symbols-table scan.
        repo = self._known_symbol_names(workspace, list(groups)) if rank_repo_defined else set(groups)
        suppress = rank_repo_defined and not include_builtins
        ranked: list[tuple] = []
        hidden = 0
        for k, g in groups.items():
            e = g["edge"]
            if suppress and self._is_builtin_call(k, e.from_file, repo):
                hidden += 1
                continue
            e.call_count = len(g["sites"])
            ranked.append((0 if k in repo else 1, -e.call_count, k, e))
        ranked.sort(key=lambda t: (t[0], t[1], t[2]))
        return [t[3] for t in ranked[:limit]], hidden

    def _known_symbol_names(self, workspace: str, names: list[str]) -> set[str]:
        """Subset of `names` that are defined as symbols in this workspace —
        used to rank repo-internal calls ahead of builtins/externals (UPG-4.2)."""
        if not names:
            return set()
        found: set[str] = set()
        with self._conn() as conn:
            for i in range(0, len(names), 500):  # stay under SQLite's bound-var limit
                chunk = names[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT DISTINCT name FROM symbols WHERE workspace = ? "
                    f"AND name IN ({placeholders})",
                    (workspace, *chunk),
                ).fetchall()
                found.update(r["name"] for r in rows)
        return found

    def callers(self, workspace: str, symbol_name: str, limit: int = 20) -> list[CallEdge]:
        """Who calls this symbol? Exact name match preferred (partial fallback).
        Deduped by calling function, ranked by call frequency (UPG-4.2)."""
        edges, _ = self._edges(workspace, "to_symbol", symbol_name, "from_symbol", limit, rank_repo_defined=False)
        return edges

    def callees(
        self, workspace: str, symbol_name: str, limit: int = 20, include_builtins: bool = True
    ) -> list[CallEdge]:
        """What does this symbol call? Exact name match preferred (partial fallback).
        Deduped by callee, repo-internal calls ranked ahead of builtins (UPG-4.2);
        builtin/stdlib callees suppressed unless `include_builtins` (UPG-4.3)."""
        edges, _ = self._edges(
            workspace, "from_symbol", symbol_name, "to_symbol", limit,
            rank_repo_defined=True, include_builtins=include_builtins,
            exclude_uses=True,
        )
        return edges

    def _exact_definitions(self, workspace: str, name: str, limit: int = 20) -> list[Symbol]:
        """Definition sites whose name matches `name` exactly. Each (file_path,
        name) is a distinct node — this is the fully-qualified identity that
        keeps same-named symbols in different modules from merging (UPG-4.1)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE workspace = ? AND name = ? "
                "ORDER BY file_path, start_line LIMIT ?",
                (workspace, name, limit),
            ).fetchall()
        return [self._row_to_symbol(r) for r in rows]

    @staticmethod
    def _module_label(file_path: str, workspace: str) -> str:
        """Repo-relative path used to qualify a definition in trace output."""
        try:
            return str(Path(file_path).relative_to(workspace))
        except ValueError:
            return Path(file_path).name

    def trace(
        self,
        workspace: str,
        symbol_name: str,
        direction: Literal["callers", "callees", "both"] = "both",
        limit: int = 20,
        include_builtins: bool = False,
    ) -> dict:
        """Combined callers + callees lookup.

        UPG-4.1: when `symbol_name` has more than one definition across modules,
        the callees are scoped per definition (by `from_file`) so they are shown
        separately instead of merged into one node. Callees are exactly
        attributable (an edge carries the calling definition's file); callers are
        not (a call site doesn't record which definition it bound), so callers
        stay a flat list with an ambiguity note in the formatter.

        UPG-4.3: builtin/stdlib callees are hidden by default; the count hidden is
        recorded under `hidden_builtins` (flat) / per `by_definition` entry so the
        formatter can offer `include_builtins`.
        """
        result: dict = {}
        if direction in ("callers", "both"):
            result["callers"] = self.callers(workspace, symbol_name, limit)
        if direction in ("callees", "both"):
            callees, hidden = self._edges(
                workspace, "from_symbol", symbol_name, "to_symbol", limit,
                rank_repo_defined=True, include_builtins=include_builtins,
                exclude_uses=True,
            )
            result["callees"] = callees
            result["hidden_builtins"] = hidden

        defs = self._exact_definitions(workspace, symbol_name)
        result["definitions"] = defs
        if direction in ("callees", "both") and len(defs) > 1:
            by_def = []
            with self._conn() as conn:
                for d in defs:
                    rows = conn.execute(
                        "SELECT * FROM edges WHERE workspace = ? AND from_symbol = ? "
                        "AND from_file = ? AND edge_type != 'uses' LIMIT ?",
                        (workspace, symbol_name, d.file_path, self._EDGE_FETCH_CAP),
                    ).fetchall()
                    edges = [self._row_to_edge(r) for r in rows]
                    # dedup + relevance-rank + builtin-suppress this def's callees
                    cs, hidden = self._aggregate_edges(
                        workspace, edges, "to_symbol", limit,
                        rank_repo_defined=True, include_builtins=include_builtins,
                    )
                    by_def.append({
                        "definition": d,
                        "module": self._module_label(d.file_path, workspace),
                        "callees": cs,
                        "hidden_builtins": hidden,
                    })
            result["by_definition"] = by_def
        return result

    def get_snippet(self, file_path: str, start_line: int, end_line: int) -> str:
        """
        Read up to SNIPPET_LINES from a file starting at start_line (1-indexed).
        Returns the raw code so the AI editor can read and understand it directly.
        """
        try:
            lines = Path(file_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            s = max(0, start_line - 1)
            e = min(len(lines), s + SNIPPET_LINES)
            snippet = "\n".join(lines[s:e])
            logger.debug("get_snippet: %s:%d-%d (%d lines)", file_path, start_line, end_line, e - s)
            return snippet
        except OSError as exc:
            logger.warning("get_snippet: could not read %s — %s", file_path, exc)
            return ""

    # ------------------------------------------------------------------
    # Formatting for LLM
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_description(query: str) -> bool:
        """A no-match query that reads like a natural-language description rather
        than a symbol name — the LLM likely misrouted a `vectr_search` query to
        `locate`. Whitespace between tokens is the tell: `is_prime` won't fire,
        but "function that checks whether a number is prime" will (UPG-4.6)."""
        return len(query.split()) > 1

    def _no_match_text(self, query: str) -> str:
        """Empty-locate message that ALWAYS hands the LLM a path forward — never a
        dead end (UPG-10.3, extends UPG-4.6). A silent no-match trains the model
        to abandon `locate` and fall back to grep; a redirect keeps it on a vectr
        tool. Description-shaped misses point at the misroute (UPG-4.6); a plain
        single-token miss points at content search, since the name may be a kind
        the symbol graph doesn't make locatable or simply isn't present."""
        base = f"No symbol matching '{query}' found in the indexed codebase."
        if self._looks_like_description(query):
            return base + (" This looks like a description, not a symbol name — "
                           "try vectr_search for concept/semantic lookup.")
        return base + (f' Try vectr_search("{query}") to find it by content — it '
                       "may be defined under a different name or not indexed as a "
                       "locatable symbol.")

    def format_locate_for_llm(self, symbols: list[Symbol], name: str) -> str:
        if not symbols:
            return self._no_match_text(name)
        lines = [f"Symbol locations for '{name}' ({len(symbols)} match{'es' if len(symbols) != 1 else ''}):\n"]
        for s in symbols:
            lines.append(f"  [{s.kind}] {s.name}  {s.file_path}:{s.start_line}")
            if s.snippet:
                for ln in s.snippet.splitlines()[:SNIPPET_LINES]:
                    lines.append(f"    {ln}")
                lines.append("")
        return "\n".join(lines)

    def format_locate_l2_for_llm(self, result: LocateResult) -> str:
        if not result.symbols:
            return self._no_match_text(result.query)
        _labels = {
            "exact":        "exact name match",
            "suffix":       "suffix match (qualifier stripped)",
            "same_module":  "same-module resolution",
            "substring":    "partial-name match (canonical defs first)",
            "import_chain": "import-chain resolution",
            "fuzzy":        "fuzzy name match (edit-distance)",
        }
        label = _labels.get(result.resolution_strategy, result.resolution_strategy)
        n = len(result.symbols)
        lines = [
            f"Symbol locations for '{result.query}' "
            f"({n} match{'es' if n != 1 else ''} via {label}):\n"
        ]
        for s in result.symbols:
            lines.append(f"  [{s.kind}] {s.name}  {s.file_path}:{s.start_line}")
            if s.snippet:
                for ln in s.snippet.splitlines()[:SNIPPET_LINES]:
                    lines.append(f"    {ln}")
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _count_suffix(edge: CallEdge) -> str:
        """' ×N' when an aggregated edge stands for multiple call sites (UPG-4.2)."""
        return f"  ×{edge.call_count}" if edge.call_count > 1 else ""

    @staticmethod
    def _caller_verb(callers: list) -> str:
        """'Used by' when every reference is a type-usage edge (UPG-4.4) — e.g.
        tracing a Rust struct that's only passed/returned, never free-called;
        'Called/used by' when mixed; 'Called by' otherwise."""
        kinds = {getattr(e, "edge_type", "calls") for e in callers}
        if kinds == {"uses"}:
            return "Used by"
        if "uses" in kinds:
            return "Called/used by"
        return "Called by"

    @staticmethod
    def _hidden_builtins_note(n: int) -> str:
        """Footer telling the LLM repo-internal calls are shown and how to see the
        rest — the suppressed builtin/stdlib calls (UPG-4.3)."""
        if n <= 0:
            return ""
        return (f"    (+{n} builtin/stdlib call{'s' if n != 1 else ''} hidden — "
                f"pass include_builtins=true to show)")

    def format_trace_for_llm(self, trace_result: dict, symbol_name: str) -> str:
        lines = [f"Call graph trace for '{symbol_name}':\n"]

        # UPG-4.1: ambiguous symbol — show callees separated per definition so
        # the LLM sees e.g. resolver `Lock` vs sync `Lock` as distinct, not merged.
        by_def = trace_result.get("by_definition")
        if by_def and len(by_def) > 1:
            lines.append(
                f"⚠ '{symbol_name}' has {len(by_def)} definitions across modules — "
                f"calls are shown per definition. (Callers below match the name only "
                f"and can't be attributed to one definition by static analysis.)\n"
            )
            for entry in by_def:
                d = entry["definition"]
                mod = entry.get("module") or d.file_path
                cs = entry["callees"]
                lines.append(f"[{d.kind}] {symbol_name} @ {mod}:{d.start_line} — calls ({len(cs)}):")
                if cs:
                    for e in cs:
                        lines.append(f"    {e.to_symbol}{self._count_suffix(e)}")
                else:
                    lines.append("    (none found in index)")
                note = self._hidden_builtins_note(entry.get("hidden_builtins", 0))
                if note:
                    lines.append(note)
                lines.append("")
            callers = trace_result.get("callers")
            if callers is not None:
                if callers:
                    lines.append(f"{self._caller_verb(callers)} — any '{symbol_name}' ({len(callers)}):")
                    for e in callers:
                        lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}{self._count_suffix(e)}")
                else:
                    lines.append(f"Called by — any '{symbol_name}': (none found in index)")
            return "\n".join(lines)

        callers = trace_result.get("callers", [])
        if callers is not None:
            if callers:
                lines.append(f"{self._caller_verb(callers)} ({len(callers)}):")
                for e in callers:
                    lines.append(f"  {e.from_symbol}  in {e.from_file}:{e.from_line}{self._count_suffix(e)}")
            else:
                lines.append("Called by: (none found in index)")

        callees = trace_result.get("callees", [])
        if callees is not None:
            if callees:
                lines.append(f"\nCalls ({len(callees)}):")
                for e in callees:
                    lines.append(f"  {e.to_symbol}{self._count_suffix(e)}")
            else:
                lines.append("\nCalls: (none found in index)")
            note = self._hidden_builtins_note(trace_result.get("hidden_builtins", 0))
            if note:
                lines.append(note)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_symbol(row: sqlite3.Row) -> Symbol:
        return Symbol(
            symbol_id=row["symbol_id"],
            workspace=row["workspace"],
            name=row["name"],
            kind=row["kind"],
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            # snippet is populated by locate() after DB fetch — not stored in DB
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> CallEdge:
        return CallEdge(
            from_file=row["from_file"],
            from_symbol=row["from_symbol"],
            from_line=row["from_line"],
            to_symbol=row["to_symbol"],
            edge_type=row["edge_type"],
        )
