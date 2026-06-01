"""Tests for the TF-IDF tool-necessity probe (R1)."""
from __future__ import annotations

import pytest
from agent.tool_necessity_probe import (
    ToolNecessityProbe, ProbeResult, classify_query, should_use_tool,
    should_verbalize_first,
)


class TestProbeResult:
    def test_result_has_required_fields(self) -> None:
        r = ProbeResult(query="test", needs_tool=True, confidence=0.8)
        assert r.query == "test"
        assert r.needs_tool is True
        assert 0.0 <= r.confidence <= 1.0

    def test_str_includes_verdict(self) -> None:
        r = ProbeResult(query="q", needs_tool=True, confidence=0.7, trigger_signals=["find"])
        assert "needs_tool" in str(r)

    def test_str_self_sufficient(self) -> None:
        r = ProbeResult(query="q", needs_tool=False, confidence=0.6, self_signals=["python"])
        assert "self_sufficient" in str(r)


class TestToolNecessityProbe:
    def _probe(self) -> ToolNecessityProbe:
        return ToolNecessityProbe()

    # Tool-triggering queries

    def test_where_is_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Where is authenticate_user defined?").needs_tool

    def test_find_function_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Find the function that handles rate limiting").needs_tool

    def test_who_calls_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("What calls the verify_token function?").needs_tool

    def test_callers_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Show me the callers of process_payment").needs_tool

    def test_locate_symbol_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Locate the PaymentService class definition").needs_tool

    def test_call_graph_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Show me the call graph for checkout_handler").needs_tool

    def test_which_file_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Which file contains the database migration logic?").needs_tool

    def test_code_symbol_dot_pattern_needs_tool(self) -> None:
        p = self._probe()
        assert p.predict("Where is stripe.charge_customer defined?").needs_tool

    # Self-sufficient queries

    def test_what_is_decorator_no_tool(self) -> None:
        p = self._probe()
        r = p.predict("What is a Python decorator?")
        assert not r.needs_tool

    def test_explain_asyncio_no_tool(self) -> None:
        p = self._probe()
        assert not p.predict("Explain how asyncio event loop works").needs_tool

    def test_how_to_use_dataclass_no_tool(self) -> None:
        p = self._probe()
        assert not p.predict("How to use Python dataclass with default values?").needs_tool

    def test_difference_between_no_tool(self) -> None:
        p = self._probe()
        assert not p.predict("Difference between list and tuple in Python").needs_tool

    def test_well_known_stdlib_no_tool(self) -> None:
        p = self._probe()
        assert not p.predict("How does os.path.join work?").needs_tool

    # Confidence

    def test_strong_tool_signal_high_confidence(self) -> None:
        p = self._probe()
        r = p.predict("Find the definition of validate_token and show its callers and callees")
        assert r.needs_tool
        assert r.confidence > 0.3

    def test_weak_signal_lower_confidence(self) -> None:
        p = self._probe()
        r = p.predict("Tell me about Python")
        # Low signal either way — confidence should be low
        assert r.confidence < 0.8

    def test_confidence_range(self) -> None:
        p = self._probe()
        for q in ["find function", "what is asyncio", "where is X defined", "explain decorators"]:
            r = p.predict(q)
            assert 0.0 <= r.confidence <= 1.0

    # Trigger signals captured

    def test_trigger_signals_populated(self) -> None:
        p = self._probe()
        r = p.predict("Where is the authenticate function defined?")
        assert r.needs_tool
        assert len(r.trigger_signals) >= 1

    def test_self_signals_populated(self) -> None:
        p = self._probe()
        r = p.predict("What is a Python generator?")
        assert not r.needs_tool
        assert len(r.self_signals) >= 1

    # Edge cases

    def test_empty_query_no_tool(self) -> None:
        p = self._probe()
        r = p.predict("")
        assert isinstance(r.needs_tool, bool)
        assert 0.0 <= r.confidence <= 1.0

    def test_very_long_query_no_error(self) -> None:
        p = self._probe()
        q = "find " * 100
        r = p.predict(q)
        assert isinstance(r.needs_tool, bool)

    def test_case_insensitive_matching(self) -> None:
        p = self._probe()
        lower = p.predict("where is authenticate defined")
        upper = p.predict("WHERE IS AUTHENTICATE DEFINED")
        assert lower.needs_tool == upper.needs_tool


class TestModuleLevelFunctions:
    def test_classify_query_returns_result(self) -> None:
        r = classify_query("Where is validate_token defined?")
        assert isinstance(r, ProbeResult)
        assert r.needs_tool

    def test_should_use_tool_true_for_navigation(self) -> None:
        assert should_use_tool("find the class that handles user authentication")

    def test_should_use_tool_false_for_explanation(self) -> None:
        assert not should_use_tool("explain how Python decorators work")

    def test_should_verbalize_first_for_explanation(self) -> None:
        assert should_verbalize_first("what is a metaclass in Python?")

    def test_should_verbalize_first_false_for_navigation(self) -> None:
        assert not should_verbalize_first("which file contains the login handler?")

    def test_thread_safety_shared_probe(self) -> None:
        import threading
        results = []
        def classify():
            results.append(should_use_tool("find the authentication module"))
        threads = [threading.Thread(target=classify) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert all(r is True for r in results)


class TestSRRAGPattern:
    """Tests for the knowledge-verbalization (SR-RAG) signal."""

    def test_parametric_knowledge_query_triggers_verbalization(self) -> None:
        from agent.tool_necessity_probe import ToolNecessityProbe
        p = ToolNecessityProbe()
        # Well-known API: model already knows this
        assert p.should_suggest_verbalization("how does asyncio gather work?")

    def test_unknown_symbol_query_does_not_trigger_verbalization(self) -> None:
        from agent.tool_necessity_probe import ToolNecessityProbe
        p = ToolNecessityProbe()
        assert not p.should_suggest_verbalization("where is stripe_webhook_handler defined")

    def test_query_with_no_signals_does_not_trigger_verbalization(self) -> None:
        from agent.tool_necessity_probe import ToolNecessityProbe
        p = ToolNecessityProbe()
        # No self-sufficient signals → verbalization does not trigger
        r = p.predict("do the thing")
        if not r.self_signals:
            assert not p.should_suggest_verbalization("do the thing")
