"""
Tests for agent/identifier_hint.py — the identifier-SHAPE tokenizer that backs
vectr_search's additive symbol-graph hint (UPG-QUERYTYPE-REROUTE).

This is a pure shape-detection transform, not intent/keyword classification —
these tests pin that distinction: an ordinary capitalised sentence word or a
plain word that happens to equal a real symbol's name must NEVER be returned,
while CamelCase/snake_case/dotted forms always are, regardless of the
surrounding sentence's topic.
"""
from __future__ import annotations

from agent.identifier_hint import extract_identifier_tokens


class TestExtractIdentifierTokens:
    def test_plain_english_question_yields_no_tokens(self) -> None:
        """Bare nouns that used to trigger the deleted regex router
        (dependency/override/subclass) must not be treated as identifiers."""
        assert extract_identifier_tokens(
            "what are the dependencies of this module and can I override it"
        ) == []
        assert extract_identifier_tokens("does this class have any subclasses") == []

    def test_capitalised_sentence_word_is_not_camelcase(self) -> None:
        """A single leading capital with no internal case transition (an
        ordinary English word starting a sentence) is not identifier-shaped."""
        assert extract_identifier_tokens("Where is this handled") == []

    def test_camelcase_token_extracted(self) -> None:
        assert extract_identifier_tokens("look at WorkspaceLock please") == ["WorkspaceLock"]

    def test_snake_case_token_extracted(self) -> None:
        assert extract_identifier_tokens("call acquire_lock before writing") == ["acquire_lock"]

    def test_dotted_qualified_token_extracted_as_one_unit(self) -> None:
        tokens = extract_identifier_tokens("what does QuerySet.delete do")
        assert tokens == ["QuerySet.delete"]

    def test_dedup_preserves_first_occurrence_order(self) -> None:
        tokens = extract_identifier_tokens(
            "compare acquire_lock to release_lock, then re-check acquire_lock"
        )
        assert tokens == ["acquire_lock", "release_lock"]

    def test_multiple_distinct_tokens_preserve_query_order(self) -> None:
        tokens = extract_identifier_tokens("WorkspaceLock calls acquire_lock then ReleaseHandle")
        assert tokens == ["WorkspaceLock", "acquire_lock", "ReleaseHandle"]

    def test_plain_word_equal_to_a_symbol_name_is_not_a_token(self) -> None:
        """A lowercase plain word is never identifier-shaped even if it
        happens to be a real symbol's name — only shape matters here, and
        exactness of resolution is left entirely to the caller."""
        assert extract_identifier_tokens("please resolve the timeout quickly") == []

    def test_no_identifier_shaped_words_in_query_returns_empty_list(self) -> None:
        assert extract_identifier_tokens("") == []
        assert extract_identifier_tokens("just plain english words here") == []
