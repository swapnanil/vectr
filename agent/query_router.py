"""
QueryRouter — classify incoming queries and produce a RoutingDecision.

Every call to vectr_search goes through this router. The router:
  1. Classifies the query into one of four types via regex heuristics.
  2. Returns adjusted hybrid weights + flags controlling whether L2 symbol
     results should be blended in alongside L3 chunk results.

Query types
-----------
STRUCTURAL    "what's in the targeting module?", "how is this repo organised?"
              → BM25 heavy (keywords match module/dir names), include L1 map hint
SYMBOL_LOOKUP "where is EvaluateSegments defined?", "find the JWT validator"
              → balanced weights, augment with L2 locate results
CALL_GRAPH    "what calls EvaluateSegments?", "what does RequestBid depend on?"
              → augment with L2 trace results
SEMANTIC      "how does bid floor pricing work?" (default, no strong signals)
              → normal hybrid weights
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class QueryType(Enum):
    STRUCTURAL   = "structural"
    SYMBOL_LOOKUP = "symbol_lookup"
    CALL_GRAPH   = "call_graph"
    SEMANTIC     = "semantic"


@dataclass
class RoutingDecision:
    query_type: QueryType
    semantic_weight: float        # adjusted for this specific query
    also_run_symbol_lookup: bool  # blend L2 locate results above L3 chunks
    also_run_trace: bool          # blend L2 trace results
    include_map_hint: bool        # prepend L1 map excerpt for structural queries
    rationale: str


# ---------------------------------------------------------------------------
# Compiled pattern sets
# ---------------------------------------------------------------------------

_STRUCTURAL = [
    r"\bhow (is|are) .{0,40} (structured?|organiz|laid? out|arranged)\b",
    r"\bwhat.{0,20} (module|package|director|folder|component|service)s?\b",
    r"\b(overview|architecture|structure|layout|map)\b",
    r"\bwhat.{0,15} (in|inside|contains?) .{0,30}(module|package|director)\b",
    r"\bhow does .{0,30} fit (in|into|together)\b",
    r"\bwhere (do|does|should) .{0,20} (live|go|belong)\b",
    # "which directory/folder/module contains X" — asking about location of a category
    r"\bwhich (directory|folder|module|package|area|layer|component)\b",
    # "where is the X module/package/directory" — locating a structural unit, not a symbol
    r"\bwhere.{0,20}(module|package|director|folder|layer|component)\b",
]

_SYMBOL = [
    r"\bwhere (is|are|can i find)\b",
    r"\b(find|locate|show me) .{0,20}(function|class|method|interface|struct|type|impl|def)\b",
    r"\b(definition|declaration|implementation) of\b",
    r"\bwhere .{0,30}(defined|implemented|declared)\b",
    r"\bwhich (file|module|class) (contains?|has|defines?|implements?)\b",
]

_CALL_GRAPH = [
    r"\bwhat (calls?|invokes?|uses?|calls into)\b",
    r"\bwho (calls?|uses?|invokes?|implements?)\b",
    r"\bcall(er|graph|chain|stack|tree)s?\b",
    r"\bwhat does .{0,30} (depend on|call|use|import)\b",
    r"\b(dependenc(y|ies)|downstream|upstream)\b",
    # F50: "implementations? of X" / "implements the interface" are genuinely
    # structural (asking the graph for all implementers of an interface/type) —
    # kept. A BARE "implementation(s)" noun (e.g. "caching framework
    # implementation") is NOT a call-graph signal on its own and previously
    # misrouted ordinary conceptual queries to CALL_GRAPH; removed rather than
    # narrowed further (heuristic-removal, not a compensating keyword).
    r"\bimplementations? of\b",
    r"\bimplements? the interface\b",
    r"\bextend(s|ed by)\b",
    r"\bsubclass(es)?\b",
    r"\boverride\b",
    r"\bwhat (imports?|requires?|needs?|includes?) .{0,30}\b",
]


def _matches(query_lower: str, patterns: list[str]) -> bool:
    return any(re.search(p, query_lower) for p in patterns)


def classify(query: str) -> QueryType:
    """Classify a query string into a QueryType via heuristic patterns."""
    q = query.lower()
    # Call-graph checked first — most specific signal
    if _matches(q, _CALL_GRAPH):
        return QueryType.CALL_GRAPH
    # Structural before symbol: "where is the X module" is structural, not symbol lookup
    if _matches(q, _STRUCTURAL):
        return QueryType.STRUCTURAL
    if _matches(q, _SYMBOL):
        return QueryType.SYMBOL_LOOKUP
    return QueryType.SEMANTIC


def route(query: str, base_semantic_weight: float = 0.70) -> RoutingDecision:
    """Return a RoutingDecision that controls how vectr_search executes."""
    qtype = classify(query)

    if qtype == QueryType.STRUCTURAL:
        return RoutingDecision(
            query_type=qtype,
            semantic_weight=0.40,
            also_run_symbol_lookup=True,
            also_run_trace=False,
            include_map_hint=True,
            rationale=(
                "structural query — BM25 weighted up (dir/module names are keywords); "
                "L1 map hint prepended; L2 symbol results augmented"
            ),
        )

    if qtype == QueryType.SYMBOL_LOOKUP:
        return RoutingDecision(
            query_type=qtype,
            semantic_weight=0.50,
            also_run_symbol_lookup=True,
            also_run_trace=False,
            include_map_hint=False,
            rationale=(
                "symbol lookup — balanced weights (symbol names match both semantics and keywords); "
                "L2 locate results prepended"
            ),
        )

    if qtype == QueryType.CALL_GRAPH:
        return RoutingDecision(
            query_type=qtype,
            semantic_weight=0.55,
            also_run_symbol_lookup=True,
            also_run_trace=True,
            include_map_hint=False,
            rationale=(
                "call graph query — L2 locate + trace results prepended; "
                "semantic weight slightly reduced for symbol-name BM25 boost"
            ),
        )

    # SEMANTIC — default path
    return RoutingDecision(
        query_type=qtype,
        semantic_weight=base_semantic_weight,
        also_run_symbol_lookup=False,
        also_run_trace=False,
        include_map_hint=False,
        rationale="semantic query — standard adaptive hybrid weights",
    )
