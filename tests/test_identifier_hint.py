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


# ---------------------------------------------------------------------------
# UPG-HINT-LOWERCAMEL: the CamelCase alternative required a leading capital,
# so lowerCamelCase — the dominant function-naming convention in JS/TS/Java/
# Kotlin/Swift — was never detected. These tests pin the added shape
# alternative and its exclusions (still shape-only, no word list).
# ---------------------------------------------------------------------------

class TestLowerCamelCaseTokens:
    def test_lower_camelcase_token_extracted(self) -> None:
        assert extract_identifier_tokens(
            "why does scheduleUpdateOnFiber run twice"
        ) == ["scheduleUpdateOnFiber"]
        assert extract_identifier_tokens(
            "look at commitPassiveMountEffects please"
        ) == ["commitPassiveMountEffects"]

    def test_plain_lowercase_words_not_matched(self) -> None:
        assert extract_identifier_tokens("where does the value come from") == []
        assert extract_identifier_tokens("this does nothing special") == []

    def test_capitalised_sentence_starter_still_not_matched(self) -> None:
        assert extract_identifier_tokens("Where is this handled") == []

    def test_allcaps_acronym_not_matched(self) -> None:
        assert extract_identifier_tokens("this uses HTML and the API a lot") == []

    def test_single_letter_prefix_brand_word_not_matched(self) -> None:
        """A single lowercase letter before the case transition (the common
        brand-name shape, e.g. "iPhone"/"eBay") is excluded — the shape
        requires at least two lower/digit characters before the transition,
        which also happens to keep this class of common English proper noun
        out of the identifier-hint path without any word-specific list."""
        assert extract_identifier_tokens("does this look iPhone-like to you") == []
        assert extract_identifier_tokens("what about eBay integration") == []

    def test_lower_camelcase_mixed_with_other_shapes_preserves_order(self) -> None:
        tokens = extract_identifier_tokens(
            "compare scheduleUpdateOnFiber to WorkspaceLock and acquire_lock"
        )
        assert tokens == ["scheduleUpdateOnFiber", "WorkspaceLock", "acquire_lock"]

    def test_dotted_form_still_tried_first_with_lower_camelcase_leaf(self) -> None:
        """Ordering regression: the dotted alternative must still win over the
        lowerCamelCase alternative when both could apply to a substring of
        the match, so a qualified call stays one token, not two."""
        tokens = extract_identifier_tokens("what does fiber.scheduleUpdate do")
        assert tokens == ["fiber.scheduleUpdate"]
