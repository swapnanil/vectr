"""Pydantic v2 request and response models."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class IndexRequest(BaseModel):
    path: str = Field(default=".", description="Absolute or relative path to index")
    force: bool = Field(default=False, description="Force full re-index even if already indexed")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language or code query")
    n_results: int = Field(default=10, ge=1, le=50, description="Number of results to return")
    # UPG-3.1: any indexed language is accepted (no fixed allow-list, no 422).
    # Normalisation (lower/strip, blank→None) lives in CodeSearcher.search — the
    # shared path for both REST and MCP — so behaviour can't diverge by entrypoint.
    language: str | None = Field(
        default=None,
        description="Filter to a specific indexed language (e.g. python, rust, c, zig). "
                    "Any language the index actually contains is accepted; unindexed "
                    "languages return no matches rather than an error.",
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class CodeChunkResult(BaseModel):
    file: str
    lines: str
    symbol: str | None   # None when chunk falls outside any named symbol
    language: str
    score: float
    content: str
    # UPG-11.4: expand-to-symbol affordance — the exact line range of the indexed
    # symbol so callers can read the full definition without a blind whole-file re-read.
    # 0/0 means the chunk was not associated with a named symbol (e.g. a window chunk).
    symbol_start_line: int = 0
    symbol_end_line: int = 0


class SearchResponse(BaseModel):
    results: list[CodeChunkResult]
    query_time_ms: int
    chunks_searched: int
    processing_ms: int
    model: str


class IndexResponse(BaseModel):
    indexed_files: int
    total_chunks: int
    processing_ms: int
    model: str


class LanguageStat(BaseModel):
    """Per-language index coverage + symbol availability (UPG-3.3).

    `symbols=True` means locate/trace work for this language; otherwise it is
    search-only. Lets a REST consumer route the same way the MCP agent does.
    """
    language: str
    files: int
    chunks: int
    symbols: bool


class StatusResponse(BaseModel):
    indexed_files: int
    total_chunks: int
    last_indexed: str
    embed_model: str
    workspace_root: str
    symbol_count: int = 0
    languages: list[LanguageStat] = []
    notes_count: int = 0
    # Symbol-graph build trust signals (UPG-8.7): complete = no files failed
    # extraction; failed_files counts those skipped, so a partial graph is visible.
    symbol_graph_complete: bool = False
    symbol_graph_failed_files: int = 0
    processing_ms: int
    model: str
    # Adaptive retrieval strategy (populated after first index)
    semantic_weight: float | None = None
    bm25_weight: float | None = None
    graph_first: bool | None = None
    recommended_embed_model: str | None = None
    strategy_rationale: str | None = None


class HealthResponse(BaseModel):
    status: str
    model: str
    embed_model: str


# ---------------------------------------------------------------------------
# Codebase passport
# ---------------------------------------------------------------------------

class MapSaveRequest(BaseModel):
    summary: str = Field(..., min_length=1, description="AI-written plain-English codebase summary")


class MapSaveResponse(BaseModel):
    message: str
    processing_ms: int


# ---------------------------------------------------------------------------
# Memory / working context
# ---------------------------------------------------------------------------

_VALID_KINDS = ("directive", "task", "gotcha", "finding", "reference")


class RememberRequest(BaseModel):
    content: str = Field(..., min_length=1, description="Working note to store")
    tags: list[str] | None = Field(default=None, description="Topic tags")
    priority: str = Field(default="medium", description="high | medium | low")
    kind: str = Field(default="finding", description="directive | task | gotcha | finding | reference")
    session_id: str | None = Field(default=None)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in ("high", "medium", "low"):
            raise ValueError("priority must be high, medium, or low")
        return v

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in _VALID_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_VALID_KINDS)}")
        return v


class RememberResponse(BaseModel):
    note_id: int
    message: str
    processing_ms: int


class RecallRequest(BaseModel):
    query: str | None = Field(default=None)
    tags: list[str] | None = Field(default=None)
    priority: str | None = Field(default=None)
    kind: str | None = Field(default=None, description="Filter by kind: directive | task | gotcha | finding | reference")
    limit: int = Field(default=10, ge=1, le=100)
    boot: bool = Field(default=False, description="Boot mode (UPG-9.2): unconditional directives + high-priority tasks; ignores query/tags/priority/kind/limit")
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0, description="Relevance cutoff (UPG-5.1): drop semantic matches below this cosine similarity; only applies with a query")
    file_path: str | None = Field(default=None, description="Path-anchored recall (UPG-9.6): notes recorded against this file (basename/relpath match); for the PreToolUse gotcha hook")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(_VALID_KINDS)}")
        return v


class RecallResponse(BaseModel):
    notes: str
    processing_ms: int


class SnapshotRequest(BaseModel):
    label: str = Field(..., min_length=1)
    session_id: str | None = Field(default=None)


class SnapshotResponse(BaseModel):
    snapshot_id: str
    label: str
    processing_ms: int


# ---------------------------------------------------------------------------
# Symbol graph
# ---------------------------------------------------------------------------

class LocateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class SymbolResult(BaseModel):
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str = ""   # first ~12 lines of symbol body — AI reads this directly


class LocateResponse(BaseModel):
    results: list[SymbolResult]
    formatted: str
    processing_ms: int


class TraceRequest(BaseModel):
    name: str = Field(..., min_length=1)
    direction: str = Field(default="both")
    limit: int = Field(default=20, ge=1, le=100)
    include_builtins: bool = Field(default=False)

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        if v not in ("callers", "callees", "both"):
            raise ValueError("direction must be callers, callees, or both")
        return v


class TraceResponse(BaseModel):
    formatted: str
    processing_ms: int
