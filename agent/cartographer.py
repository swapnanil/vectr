"""
CartographerAgent — builds a persistent codebase passport.

Design principle: vectr never calls an LLM internally. The AI editor (Claude Code,
Cursor, etc.) IS the LLM. Vectr collects raw structural metadata and returns it;
the AI synthesises the passport and writes it back via vectr_map_save. Vectr stores
and serves it cheaply from then on.

vectr_map flow:
  - Passport exists  → return cached AI-written summary (~300 tokens, instant)
  - No passport yet  → return raw structural metadata + instruct AI to call vectr_map_save
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_directory_sketch(workspace_root: str, max_files: int = 120) -> dict:
    """
    Walk up to 3 levels deep and collect representative file listing.
    Returns a dict with dirs, file samples, and detected signals — not a string.
    """
    from agent.indexer import EXCLUDED_DIRS

    root = Path(workspace_root)
    excluded = EXCLUDED_DIRS
    structure: dict[str, list[str]] = {}  # rel_dir → [filenames]
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > 3:
            dirnames.clear()
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in excluded and not d.startswith("."))

        rel = str(Path(dirpath).relative_to(root)) or "."
        sample = sorted(filenames)[:15]
        if sample:
            structure[rel] = sample
        file_count += sum(1 for _ in filenames)
        if file_count >= max_files:
            break

    return structure


def _read_readme_snippet(workspace_root: str, max_chars: int = 1500) -> str:
    """Return first 1500 chars of README if present, empty string otherwise."""
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        p = Path(workspace_root) / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")[:max_chars]
            logger.debug("Cartographer: found README at %s (%d chars)", p, len(text))
            return text
    return ""


def _detect_languages(structure: dict[str, list[str]]) -> list[str]:
    """Infer languages from file extensions in the directory sketch."""
    ext_map = {
        ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
        ".java": "Java", ".rs": "Rust", ".rb": "Ruby",
        ".cs": "C#", ".cpp": "C++", ".c": "C", ".kt": "Kotlin",
        ".swift": "Swift", ".php": "PHP", ".scala": "Scala",
    }
    seen: dict[str, int] = {}
    for files in structure.values():
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in ext_map:
                lang = ext_map[ext]
                seen[lang] = seen.get(lang, 0) + 1
    return sorted(seen, key=lambda l: -seen[l])


def _detect_frameworks(workspace_root: str, structure: dict[str, list[str]]) -> list[str]:
    """Detect frameworks from config files and directory names."""
    root = Path(workspace_root)
    signals: list[str] = []

    config_signals = {
        "pyproject.toml": "Python/pyproject", "requirements.txt": "Python",
        "package.json": "Node.js", "go.mod": "Go modules",
        "Cargo.toml": "Rust/Cargo", "pom.xml": "Java/Maven",
        "build.gradle": "Java/Gradle", "Gemfile": "Ruby/Bundler",
        "docker-compose.yml": "Docker", "Dockerfile": "Docker",
        ".proto": "gRPC",
    }
    all_files = [f for files in structure.values() for f in files]
    for fname in all_files:
        for key, label in config_signals.items():
            if fname == key or fname.endswith(key):
                if label not in signals:
                    signals.append(label)

    # check root-level dirs
    dir_signals = {
        "tests": "testing", "test": "testing", "__tests__": "testing",
        "docs": "documentation", "proto": "gRPC", "migrations": "database migrations",
    }
    top_dirs = [d for d in structure.get(".", []) if (root / d).is_dir()]
    for d in top_dirs:
        if d in dir_signals and dir_signals[d] not in signals:
            signals.append(dir_signals[d])

    return signals


def _build_import_graph(workspace_root: str, indexed_files: list[str] | None = None) -> "dict[str, set[str]]":
    """
    T27: Build a file-to-file import graph by parsing import statements.
    Returns {file_path: {imported_file_path, ...}} — edges only to files in the workspace.
    """
    import re as _re
    root = Path(workspace_root)

    # Collect all Python/JS/TS files in workspace
    if indexed_files:
        candidate_files = [Path(f) for f in indexed_files
                           if Path(f).suffix.lower() in {".py", ".js", ".ts", ".jsx", ".tsx"}]
    else:
        candidate_files = []
        for f in root.rglob("*.py"):
            candidate_files.append(f)

    # Build a name → path index for quick resolution
    name_index: dict[str, Path] = {}
    for f in candidate_files:
        # index by stem and by relative path without extension
        stem = f.stem
        name_index[stem] = f
        try:
            rel = f.relative_to(root)
            name_index[str(rel).replace(os.sep, ".").removesuffix(f.suffix)] = f
        except ValueError:
            pass

    _PY_IMPORT = _re.compile(r'^\s*(?:from|import)\s+([\w.]+)', _re.MULTILINE)
    graph: dict[str, set[str]] = {}

    for f in candidate_files:
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        deps: set[str] = set()
        for m in _PY_IMPORT.finditer(src):
            mod = m.group(1).split(".")[0]  # top-level module name
            if mod in name_index:
                target = str(name_index[mod])
                if target != str(f):
                    deps.add(target)

        graph[str(f)] = deps

    return graph


def detect_module_communities(
    workspace_root: str,
    indexed_files: list[str] | None = None,
    min_community_size: int = 2,
) -> list[dict]:
    """
    T27: Detect module communities using Louvain-style greedy modularity
    maximisation (via networkx). Returns a list of community dicts:
        [{"id": 0, "label": "auth", "files": ["auth/models.py", ...], "size": 3}, ...]

    Community label is the most common top-level directory among member files.
    Falls back gracefully if networkx is unavailable.
    """
    import_graph = _build_import_graph(workspace_root, indexed_files)
    if not import_graph:
        return []

    try:
        import networkx as nx
        from networkx.algorithms.community import greedy_modularity_communities

        G = nx.Graph()
        for src, dsts in import_graph.items():
            G.add_node(src)
            for dst in dsts:
                G.add_edge(src, dst)

        if G.number_of_nodes() < 2:
            return []

        raw_communities = list(greedy_modularity_communities(G))
        root = Path(workspace_root)
        communities: list[dict] = []

        for i, members in enumerate(raw_communities):
            files = sorted(members)
            if len(files) < min_community_size:
                continue

            # Derive label from the most common top-level directory
            top_dirs: dict[str, int] = {}
            for f in files:
                try:
                    rel = Path(f).relative_to(root)
                    top = rel.parts[0] if len(rel.parts) > 1 else rel.stem
                except ValueError:
                    top = Path(f).stem
                top_dirs[top] = top_dirs.get(top, 0) + 1
            label = max(top_dirs, key=top_dirs.get) if top_dirs else f"cluster_{i}"

            communities.append({
                "id": i,
                "label": label,
                "files": files,
                "size": len(files),
            })

        return sorted(communities, key=lambda c: -c["size"])

    except Exception as exc:
        logger.debug("Louvain community detection skipped: %s", exc)
        return []


def collect_raw_metadata(workspace_root: str, indexed_files: list[str] | None = None) -> dict:
    """
    Collect raw structural metadata about the workspace.
    No LLM call — pure file system inspection.
    Returns a dict the AI can read and summarise into a passport.
    """
    logger.info("Cartographer: collecting raw metadata for %s", workspace_root)
    structure = _build_directory_sketch(workspace_root)
    readme = _read_readme_snippet(workspace_root)
    languages = _detect_languages(structure)
    frameworks = _detect_frameworks(workspace_root, structure)

    # T27: Louvain community detection — auto-cluster modules
    communities = detect_module_communities(workspace_root, indexed_files)

    metadata = {
        "workspace_name": Path(workspace_root).name,
        "languages": languages,
        "frameworks": frameworks,
        "structure": structure,
        "readme_excerpt": readme,
        "module_communities": communities,
        "collected_at": time.time(),
    }
    logger.debug(
        "Cartographer: collected metadata — languages=%s, frameworks=%s, communities=%d",
        languages, frameworks, len(communities),
    )
    return metadata


def format_raw_metadata_for_llm(metadata: dict) -> str:
    """
    Format raw metadata as a readable string for the AI.
    The AI reads this, synthesises a summary, then calls vectr_map_save.
    """
    parts = [
        f"# Codebase: {metadata.get('workspace_name', 'unknown')}",
        "",
        "No passport cached yet. Read the metadata below, then call vectr_map_save",
        "with a concise (~300 token) plain-English summary so future sessions get",
        "an instant codebase overview without re-reading files.",
        "",
    ]

    if metadata.get("languages"):
        parts.append(f"Languages: {', '.join(metadata['languages'])}")
    if metadata.get("frameworks"):
        parts.append(f"Detected: {', '.join(metadata['frameworks'])}")

    parts.append("\nDirectory structure (up to 3 levels):")
    for rel_dir, files in sorted(metadata.get("structure", {}).items()):
        parts.append(f"  {rel_dir}/")
        for f in files[:8]:
            parts.append(f"    {f}")

    if metadata.get("readme_excerpt"):
        parts.append(f"\nREADME excerpt:\n{metadata['readme_excerpt'][:800]}")

    # T27: include module community clusters if detected
    communities = metadata.get("module_communities", [])
    if communities:
        parts.append("\nModule communities (auto-clustered by import relationships):")
        for c in communities[:8]:  # show top 8 communities
            file_sample = ", ".join(Path(f).name for f in c["files"][:4])
            if len(c["files"]) > 4:
                file_sample += f", +{len(c['files']) - 4} more"
            parts.append(f"  [{c['label']}]  ({c['size']} files)  {file_sample}")

    return "\n".join(parts)


class PassportStore:
    """Persist and retrieve the AI-written codebase passport."""

    def __init__(self, db_dir: str) -> None:
        self._path = Path(db_dir) / "passport.json"
        logger.debug("PassportStore: db path = %s", self._path)

    def load(self) -> dict | None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                logger.debug("PassportStore: loaded passport (workspace=%s)", data.get("_workspace"))
                return data
            except Exception as exc:
                logger.warning("PassportStore: failed to load passport — %s", exc)
                return None
        return None

    def save(self, passport: dict) -> None:
        """Save a full passport dict (backward-compat for any existing callers)."""
        self._path.write_text(json.dumps(passport, indent=2), encoding="utf-8")
        logger.info("PassportStore: passport saved")

    def save_summary(self, summary: str, workspace_root: str) -> None:
        """
        Persist an AI-written passport summary.
        Called via vectr_map_save — the AI has already synthesised the passport.
        """
        passport = {
            "summary": summary,
            "_generated_at": time.time(),
            "_workspace": workspace_root,
            "_source": "ai_editor",
        }
        self.save(passport)
        logger.info("PassportStore: AI-written passport saved for %s (%d chars)", workspace_root, len(summary))

    def exists(self) -> bool:
        return self._path.exists()

    def format_for_llm(self, workspace_root: str) -> str:
        """
        Return passport for AI consumption.
        If cached: return the stored summary.
        If not: collect raw metadata and prompt the AI to call vectr_map_save.
        """
        p = self.load()
        if p and p.get("summary"):
            summary = p["summary"]
            logger.debug("PassportStore: returning cached passport (%d chars)", len(summary))
            return f"# Codebase Passport — {Path(p.get('_workspace', workspace_root)).name}\n\n{summary}"

        # No passport yet — return raw metadata so the AI can synthesise one
        logger.info("PassportStore: no passport found, returning raw metadata for %s", workspace_root)
        metadata = collect_raw_metadata(workspace_root)
        return format_raw_metadata_for_llm(metadata)
