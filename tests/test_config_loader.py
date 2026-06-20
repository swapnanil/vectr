"""Tests for agent/config.py — bundled-config loader (UPG-11.1 refactor-to-config).

Verifies that:
1. config.yaml is loaded and exposes the correct numeric knobs with expected defaults.
2. Both stopword files (english_stopwords.txt and prog_stopwords.txt) are merged.
3. A known NLTK English word ('because') and a known prog word ('idx') are both present.
4. Specific method names like 'deconstruct' are NOT in the stop-word set.
5. The loader works via importlib.resources (not cwd-relative open()), confirmed by
   importing the module directly (same path the installed binary uses).
"""
from __future__ import annotations

import pytest

import agent.config as cfg


class TestConfigLoaderNumericKnobs:
    """The YAML numeric defaults must match the expected values."""

    def test_qualified_boost_default(self) -> None:
        assert cfg.SYMBOL_QUALIFIED_BOOST == 0.20, (
            f"SYMBOL_QUALIFIED_BOOST should be 0.20, got {cfg.SYMBOL_QUALIFIED_BOOST}"
        )

    def test_leaf_boost_default(self) -> None:
        assert cfg.SYMBOL_LEAF_BOOST == 0.10, (
            f"SYMBOL_LEAF_BOOST should be 0.10, got {cfg.SYMBOL_LEAF_BOOST}"
        )

    def test_min_leaf_len_default(self) -> None:
        assert cfg.SYMBOL_MIN_LEAF_LEN == 4, (
            f"SYMBOL_MIN_LEAF_LEN should be 4, got {cfg.SYMBOL_MIN_LEAF_LEN}"
        )

    def test_qualified_boost_is_float(self) -> None:
        assert isinstance(cfg.SYMBOL_QUALIFIED_BOOST, float)

    def test_leaf_boost_is_float(self) -> None:
        assert isinstance(cfg.SYMBOL_LEAF_BOOST, float)

    def test_min_leaf_len_is_int(self) -> None:
        assert isinstance(cfg.SYMBOL_MIN_LEAF_LEN, int)


class TestConfigLoaderStopWords:
    """Stop-word set must be a merged union of NLTK English + prog supplement."""

    def test_stopwords_is_frozenset(self) -> None:
        assert isinstance(cfg.SYMBOL_STOP_WORDS, frozenset)

    def test_stopwords_nonempty(self) -> None:
        assert len(cfg.SYMBOL_STOP_WORDS) > 50, (
            f"Expected >50 stop words, got {len(cfg.SYMBOL_STOP_WORDS)}"
        )

    # --- NLTK English words (from english_stopwords.txt) ---
    @pytest.mark.parametrize("word", [
        "because",   # explicitly required by the task
        "before",
        "about",
        "above",
        "after",
        "again",
        "should",
        "while",
        "which",
        "the",
        "and",
        "with",
    ])
    def test_nltk_english_word_present(self, word: str) -> None:
        assert word in cfg.SYMBOL_STOP_WORDS, (
            f"NLTK stop word '{word}' not found in SYMBOL_STOP_WORDS"
        )

    # --- Programming supplement words (from prog_stopwords.txt) ---
    @pytest.mark.parametrize("word", [
        "idx",   # explicitly required by the task
        "get",
        "set",
        "run",
        "add",
        "db",
        "id",
        "ok",
        "str",
        "int",
        "len",
        "num",
    ])
    def test_prog_word_present(self, word: str) -> None:
        assert word in cfg.SYMBOL_STOP_WORDS, (
            f"Prog stop word '{word}' not found in SYMBOL_STOP_WORDS"
        )

    # --- Specific method names must NOT be stop words ---
    @pytest.mark.parametrize("word", [
        "deconstruct",
        "from_db_value",
        "get_db_prep_value",
        "migrate",
        "apply",
        "reverse",
    ])
    def test_specific_method_not_stop_word(self, word: str) -> None:
        assert word not in cfg.SYMBOL_STOP_WORDS, (
            f"Method name '{word}' should NOT be a stop word"
        )

    def test_all_stopwords_lowercased(self) -> None:
        """Every entry in the stop-word set must be lowercase (loader normalises)."""
        for word in cfg.SYMBOL_STOP_WORDS:
            assert word == word.lower(), (
                f"Stop word '{word}' is not lowercase — loader must normalise"
            )


class TestConfigLoaderConsistencyWithChunkQuality:
    """chunk_quality.py must get its constants from config, not re-define them."""

    def test_chunk_quality_aliases_match_config(self) -> None:
        """The _SYM_* aliases re-exported from chunk_quality must equal config values."""
        from agent.chunk_quality import (
            _SYM_QUALIFIED_BOOST,
            _SYM_LEAF_BOOST,
            _SYM_STOP_WORDS,
            _SYM_MIN_LEAF_LEN,
        )
        assert _SYM_QUALIFIED_BOOST == cfg.SYMBOL_QUALIFIED_BOOST
        assert _SYM_LEAF_BOOST == cfg.SYMBOL_LEAF_BOOST
        assert _SYM_STOP_WORDS == cfg.SYMBOL_STOP_WORDS
        assert _SYM_MIN_LEAF_LEN == cfg.SYMBOL_MIN_LEAF_LEN
