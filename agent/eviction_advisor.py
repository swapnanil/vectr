"""
EvictionAdvisor — tells the LLM what it can safely drop from its context window.

Core guarantee: "If Vectr says you can forget it, you can get it back in <50ms."
This is the other side of the bidirectional protocol. The LLM calls vectr_remember
to offload notes; the EvictionAdvisor proactively signals when retrieved content
can be dropped because it's guaranteed retrievable.

The advisor tracks which chunks have been retrieved in the current session and
estimates their token cost. When the session hits a threshold, it fires an
eviction hint.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RetrievedChunk:
    file_path: str
    lines: str
    symbol_name: str
    content: str
    retrieved_at: float = field(default_factory=time.time)

    @property
    def estimated_tokens(self) -> int:
        return max(1, len(self.content) // 4)


class EvictionAdvisor:
    """
    Tracks what the LLM has retrieved this session and advises on eviction.

    Usage:
        advisor = EvictionAdvisor(eviction_threshold_tokens=4000)
        advisor.record(chunk)          # call after every vectr_search result
        hint = advisor.eviction_hint() # returns text the LLM can act on
    """

    def __init__(self, eviction_threshold_tokens: int = 4000) -> None:
        self._threshold = eviction_threshold_tokens
        self._chunks: list[RetrievedChunk] = []

    def record(self, file_path: str, lines: str, symbol_name: str, content: str) -> None:
        """Record a chunk that was delivered to the LLM this session."""
        # avoid duplicate tracking for the same file:lines
        key = f"{file_path}:{lines}"
        if any(f"{c.file_path}:{c.lines}" == key for c in self._chunks):
            return
        self._chunks.append(RetrievedChunk(
            file_path=file_path,
            lines=lines,
            symbol_name=symbol_name,
            content=content,
        ))

    def record_results(self, results: list) -> None:
        """Record a batch of SearchResult objects (from searcher.py)."""
        for r in results:
            self.record(
                file_path=r.file_path,
                lines=str(r.lines),
                symbol_name=r.symbol_name or "",
                content=r.content,
            )

    def total_tokens_in_session(self) -> int:
        return sum(c.estimated_tokens for c in self._chunks)

    def should_evict(self) -> bool:
        return self.total_tokens_in_session() >= self._threshold

    def eviction_hint(self) -> str:
        """
        Return a message the LLM can act on to free its context window.
        Always safe to call — returns an empty hint if nothing has been retrieved.
        """
        if not self._chunks:
            return ""

        total_tokens = self.total_tokens_in_session()
        by_file: dict[str, list[RetrievedChunk]] = {}
        for c in self._chunks:
            by_file.setdefault(c.file_path, []).append(c)

        lines = [
            f"Vectr has {len(self._chunks)} retrieved chunks (~{total_tokens} tokens) "
            "fully indexed and instantly retrievable.",
            "You can safely drop these from your context window:",
            "",
        ]
        for fpath, chunks in by_file.items():
            ranges = ", ".join(
                f"lines {c.lines}" + (f" ({c.symbol_name})" if c.symbol_name else "")
                for c in chunks
            )
            lines.append(f"  {fpath}  [{ranges}]")

        lines += [
            "",
            "To retrieve any of them: vectr_search('<symbol name or description>')",
            "Recall latency: <50ms. Nothing will be lost.",
        ]
        return "\n".join(lines)

    def clear_session(self) -> None:
        """Reset for a new session."""
        self._chunks.clear()

    def as_chunk_dicts(self) -> list[dict]:
        """Serialisable form for snapshot storage."""
        return [
            {
                "file": c.file_path,
                "lines": c.lines,
                "symbol": c.symbol_name,
                "content": c.content,
            }
            for c in self._chunks
        ]
