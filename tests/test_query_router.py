"""Tests for QueryRouter — query classification and routing decisions."""
from __future__ import annotations

import pytest
from agent.query_router import QueryType, classify, route


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

class TestClassify:
    def test_structural_which_directory(self) -> None:
        assert classify("which directory contains the database models") == QueryType.STRUCTURAL

    def test_structural_folder(self) -> None:
        assert classify("what folder has the config files") == QueryType.STRUCTURAL

    def test_structural_how_does_fit(self) -> None:
        assert classify("how does the auth module fit into the codebase") == QueryType.STRUCTURAL

    def test_symbol_lookup_where_defined(self) -> None:
        assert classify("where is EvaluateSegments defined") == QueryType.SYMBOL_LOOKUP

    def test_symbol_lookup_find_class(self) -> None:
        assert classify("find class SegmentMatcher") == QueryType.SYMBOL_LOOKUP

    def test_symbol_lookup_find_function(self) -> None:
        assert classify("find function get_embed_provider") == QueryType.SYMBOL_LOOKUP

    def test_call_graph_who_calls(self) -> None:
        assert classify("who calls EvaluateSegments") == QueryType.CALL_GRAPH

    def test_call_graph_callers_of(self) -> None:
        assert classify("callers of search_routed") == QueryType.CALL_GRAPH

    def test_call_graph_what_calls(self) -> None:
        assert classify("what calls the index_file method") == QueryType.CALL_GRAPH

    def test_call_graph_what_does_call(self) -> None:
        assert classify("what does RequestBid call") == QueryType.CALL_GRAPH

    def test_semantic_general_query(self) -> None:
        assert classify("how does segment targeting work") == QueryType.SEMANTIC

    def test_semantic_implement_query(self) -> None:
        assert classify("implement a new caching layer") == QueryType.SEMANTIC

    def test_semantic_error_handling(self) -> None:
        assert classify("error handling in the bid pipeline") == QueryType.SEMANTIC


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------

class TestRoute:
    def test_structural_includes_map_hint(self) -> None:
        decision = route("where is the targeting module")
        assert decision.include_map_hint is True
        assert decision.also_run_symbol_lookup is True

    def test_structural_lower_semantic_weight(self) -> None:
        decision = route("where is the targeting module")
        assert decision.semantic_weight < 0.60

    def test_symbol_lookup_no_map_hint(self) -> None:
        decision = route("find class EmbedProvider")
        assert decision.include_map_hint is False
        assert decision.also_run_symbol_lookup is True

    def test_call_graph_runs_trace(self) -> None:
        decision = route("who calls search_routed")
        assert decision.also_run_trace is True
        assert decision.also_run_symbol_lookup is True

    def test_semantic_uses_base_weight(self) -> None:
        base = 0.65
        decision = route("how is token counting implemented", base_semantic_weight=base)
        assert decision.semantic_weight == base
        assert decision.also_run_symbol_lookup is False
        assert decision.also_run_trace is False

    def test_semantic_default_weight(self) -> None:
        decision = route("how does the indexer work")
        assert decision.semantic_weight == 0.70  # default

    def test_rationale_is_non_empty(self) -> None:
        for query in [
            "where is the module",
            "find class Foo",
            "who calls bar",
            "how does X work",
        ]:
            assert len(route(query).rationale) > 0

    def test_decision_query_type_matches_classify(self) -> None:
        queries = [
            "where is the config module",
            "find function process_bid",
            "callers of evaluate",
            "how is caching implemented",
        ]
        for q in queries:
            expected = classify(q)
            actual = route(q).query_type
            assert actual == expected, f"query='{q}': expected {expected}, got {actual}"
