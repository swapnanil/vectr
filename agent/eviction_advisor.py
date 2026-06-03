"""
EvictionAdvisor — tells the LLM what it can safely drop from its context window.

Core guarantee: "If Vectr says you can forget it, you can get it back in <50ms."
This is the other side of the bidirectional protocol. The LLM calls vectr_remember
to offload notes; the EvictionAdvisor proactively signals when retrieved content
can be dropped because it's guaranteed retrievable.

The advisor tracks which chunks have been retrieved in the current session and
estimates their token cost. When the session hits a threshold, it fires an
eviction hint.

Limitation — Read/Bash blind spot (E2):
The advisor only tracks content delivered through vectr's own tools (vectr_search,
vectr_locate, vectr_trace, vectr_recall). Code the agent reads via the IDE's native
Read or Bash tools is invisible here. Token estimates are therefore a lower bound;
eviction may fire later than ideal on sessions that mix vectr retrieval with direct
file reading. The gap shrinks as agents adopt vectr_search as their primary navigation
path (Problem 3). Track `vectr_evict_hint_triggered` in benchmarks (E5) to monitor
real-world eviction uptake.
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

    def __init__(
        self,
        eviction_threshold_tokens: int = 40_000,
        tool_call_threshold: int = 10,
        retrieval_call_threshold: int = 1,
        time_threshold_seconds: float = 180.0,
    ) -> None:
        # Fire when ANY of these conditions is met:
        #   cumulative injected chars ÷ 4 >= 40K  (vectr-tracked content)
        #   tool_call_count > 10                   (all MCP calls this session)
        #   retrieval_call_count > 1               (search/locate/trace calls only)
        #   elapsed seconds >= 180                 (wall-clock since session start)
        #
        # Why retrieval_call_threshold: the tool_call_count trigger counts all vectr MCP
        # calls, but the LLM typically makes only 3-4 per task (status + 1 locate + 2 search)
        # so threshold=10 never fires. Counting only retrieval calls (search/locate/trace)
        # and firing on the 2nd one gives a natural mid-task trigger: the LLM has found
        # something worth keeping before it switches into implementation mode.
        #
        # Why time_threshold: the eviction protocol requires vectr to see every retrieval
        # to track real context pressure (spec line 78), but the LLM uses native Read/Bash
        # for most reading — those calls are invisible here. A time-based trigger fires
        # regardless of tool mix, catching sessions where the LLM barely uses vectr at all.
        # arXiv:2310.08560, arXiv:2510.24699.
        self._threshold = eviction_threshold_tokens
        self._tool_call_threshold = tool_call_threshold
        self._retrieval_call_threshold = retrieval_call_threshold
        self._time_threshold_seconds = time_threshold_seconds
        self._chunks: list[RetrievedChunk] = []
        self._tool_call_count: int = 0
        self._retrieval_call_count: int = 0
        self._session_started_at: float = time.time()

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

    def increment_tool_call(self) -> None:
        """Increment the total MCP tool call counter for this session."""
        self._tool_call_count += 1

    def increment_retrieval_call(self) -> None:
        """Increment the retrieval-specific call counter (search/locate/trace only)."""
        self._retrieval_call_count += 1

    def total_tokens_in_session(self) -> int:
        return sum(c.estimated_tokens for c in self._chunks)

    def should_evict(self) -> bool:
        elapsed = time.time() - self._session_started_at
        return (
            self.total_tokens_in_session() >= self._threshold
            or self._tool_call_count > self._tool_call_threshold
            or self._retrieval_call_count > self._retrieval_call_threshold
            or elapsed >= self._time_threshold_seconds
        )

    def eviction_hint(self) -> str:
        """
        Return a message the LLM can act on to free its context window.
        Always safe to call — returns an empty hint if nothing has been retrieved
        and no time-based pressure exists.
        """
        if not self._chunks:
            # No vectr-tracked chunks, but time pressure still warrants a nudge
            elapsed = time.time() - self._session_started_at
            if elapsed >= self._time_threshold_seconds:
                return (
                    "─── ACTION REQUIRED ───\n"
                    "Call vectr_remember(content, tags=[...]) NOW before continuing.\n"
                    "Save: key type names, module paths, entry points, non-obvious patterns.\n"
                    "Your synthesized understanding does not persist automatically.\n"
                    "Call vectr_remember now, then continue your task."
                )
            return ""

        total_tokens = self.total_tokens_in_session()
        by_file: dict[str, list[RetrievedChunk]] = {}
        for c in self._chunks:
            by_file.setdefault(c.file_path, []).append(c)

        file_items = list(by_file.items())
        shown = file_items[:5]
        overflow = len(file_items) - len(shown)

        lines = [
            "─── ACTION REQUIRED ───",
            "Call vectr_remember(content, tags=[...]) NOW before continuing.",
            "Save: key type names, module paths, entry points, non-obvious patterns.",
            "Your synthesized understanding does not persist automatically — the output",
            "file captures findings, not the navigational path to reach them.",
            "",
            f"Vectr has {len(self._chunks)} retrieved chunks (~{total_tokens} tokens)"
            " fully indexed and re-retrievable in <50ms after you save notes:",
            "",
        ]
        for fpath, chunks in shown:
            ranges = ", ".join(
                f"lines {c.lines}" + (f" ({c.symbol_name})" if c.symbol_name else "")
                for c in chunks
            )
            lines.append(f"  {fpath}  [{ranges}]")
        if overflow:
            lines.append(f"  ... and {overflow} more file(s). All retrievable via vectr_search('<description>').")

        lines += [
            "",
            "Call vectr_remember now, then continue your task.",
        ]
        return "\n".join(lines)

    def clear_session(self) -> None:
        """Reset for a new session."""
        self._chunks.clear()
        self._tool_call_count = 0
        self._retrieval_call_count = 0
        self._session_started_at = time.time()

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
