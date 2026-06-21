"""Tests for agent/config.py — bundled-config loader (UPG-11.1 refactor-to-config).

Verifies that:
1. config.yaml is loaded and exposes the correct numeric knobs with expected defaults.
2. Both stopword files (english_stopwords.txt and prog_stopwords.txt) are merged.
3. A known NLTK English word ('because') and a known prog word ('idx') are both present.
4. Specific method names like 'deconstruct' are NOT in the stop-word set.
5. The loader works via importlib.resources (not cwd-relative open()), confirmed by
   importing the module directly (same path the installed binary uses).
6. Quality priors, rerank pool sizes, indexing tunables, output knobs, and behaviour
   nudge thresholds all load correctly from the new config blocks (UPG-12.1).
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


class TestConfigLoaderForcedInclusion:
    """ranking.forced_inclusion values must load correctly from config.yaml (UPG-11.7/11.12)."""

    def test_forced_inclusion_max_default(self) -> None:
        assert cfg.FORCED_INCLUSION_MAX == 200, (
            f"FORCED_INCLUSION_MAX should be 200, got {cfg.FORCED_INCLUSION_MAX}"
        )

    def test_forced_inclusion_min_identifier_len_default(self) -> None:
        assert cfg.FORCED_INCLUSION_MIN_IDENTIFIER_LEN == 7, (
            f"FORCED_INCLUSION_MIN_IDENTIFIER_LEN should be 7, "
            f"got {cfg.FORCED_INCLUSION_MIN_IDENTIFIER_LEN}"
        )

    def test_forced_inclusion_nontrigger_bm25_floor_default(self) -> None:
        assert cfg.FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR == 0.05, (
            f"FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR should be 0.05, "
            f"got {cfg.FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR}"
        )

    def test_forced_inclusion_vec_sim_floor_default(self) -> None:
        assert cfg.FORCED_INCLUSION_VEC_SIM_FLOOR == 0.52, (
            f"FORCED_INCLUSION_VEC_SIM_FLOOR should be 0.52, "
            f"got {cfg.FORCED_INCLUSION_VEC_SIM_FLOOR}"
        )

    def test_forced_inclusion_max_is_int(self) -> None:
        assert isinstance(cfg.FORCED_INCLUSION_MAX, int)

    def test_forced_inclusion_min_identifier_len_is_int(self) -> None:
        assert isinstance(cfg.FORCED_INCLUSION_MIN_IDENTIFIER_LEN, int)

    def test_forced_inclusion_nontrigger_bm25_floor_is_float(self) -> None:
        assert isinstance(cfg.FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR, float)

    def test_forced_inclusion_vec_sim_floor_is_float(self) -> None:
        assert isinstance(cfg.FORCED_INCLUSION_VEC_SIM_FLOOR, float)

    def test_searcher_imports_from_config(self) -> None:
        """searcher.py must import forced_inclusion values from config, not define its own."""
        import agent.searcher as searcher_mod
        # The aliases in searcher.py must resolve to the same objects as config.py exports.
        assert searcher_mod._FORCED_INCLUSION_MAX is cfg.FORCED_INCLUSION_MAX
        assert searcher_mod._FORCED_INCLUSION_MIN_IDENTIFIER_LEN is cfg.FORCED_INCLUSION_MIN_IDENTIFIER_LEN
        assert searcher_mod._FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR is cfg.FORCED_INCLUSION_NONTRIGGER_BM25_FLOOR
        assert searcher_mod._FORCED_INCLUSION_VEC_SIM_FLOOR is cfg.FORCED_INCLUSION_VEC_SIM_FLOOR


class TestConfigLoaderQualityPriors:
    """ranking.quality_priors values must load correctly from config.yaml (UPG-12.1)."""

    def test_trivial_default(self) -> None:
        assert cfg.QUALITY_TRIVIAL == 0.15, f"QUALITY_TRIVIAL should be 0.15, got {cfg.QUALITY_TRIVIAL}"

    def test_navigational_default(self) -> None:
        assert cfg.QUALITY_NAVIGATIONAL == 0.35, f"QUALITY_NAVIGATIONAL should be 0.35, got {cfg.QUALITY_NAVIGATIONAL}"

    def test_heading_only_default(self) -> None:
        assert cfg.QUALITY_HEADING_ONLY == 0.40, f"QUALITY_HEADING_ONLY should be 0.40, got {cfg.QUALITY_HEADING_ONLY}"

    def test_generated_default(self) -> None:
        assert cfg.QUALITY_GENERATED == 0.45, f"QUALITY_GENERATED should be 0.45, got {cfg.QUALITY_GENERATED}"

    def test_vectr_config_default(self) -> None:
        assert cfg.QUALITY_VECTR_CONFIG == 0.10, f"QUALITY_VECTR_CONFIG should be 0.10, got {cfg.QUALITY_VECTR_CONFIG}"

    def test_test_deprioritised_default(self) -> None:
        assert cfg.QUALITY_TEST_DEPRIORITISED == 0.55, (
            f"QUALITY_TEST_DEPRIORITISED should be 0.55, got {cfg.QUALITY_TEST_DEPRIORITISED}"
        )

    def test_doc_prose_default(self) -> None:
        assert cfg.QUALITY_DOC_PROSE == 0.70, f"QUALITY_DOC_PROSE should be 0.70, got {cfg.QUALITY_DOC_PROSE}"

    def test_short_penalty_default(self) -> None:
        assert cfg.QUALITY_SHORT_PENALTY == 0.80, f"QUALITY_SHORT_PENALTY should be 0.80, got {cfg.QUALITY_SHORT_PENALTY}"

    def test_all_are_floats(self) -> None:
        for name in (
            "QUALITY_TRIVIAL", "QUALITY_NAVIGATIONAL", "QUALITY_HEADING_ONLY",
            "QUALITY_GENERATED", "QUALITY_VECTR_CONFIG", "QUALITY_TEST_DEPRIORITISED",
            "QUALITY_DOC_PROSE", "QUALITY_SHORT_PENALTY",
        ):
            assert isinstance(getattr(cfg, name), float), f"{name} must be float"

    def test_all_in_open_unit_interval(self) -> None:
        """All quality priors must be in (0, 1] — 0 would silence chunks entirely."""
        for name in (
            "QUALITY_TRIVIAL", "QUALITY_NAVIGATIONAL", "QUALITY_HEADING_ONLY",
            "QUALITY_GENERATED", "QUALITY_VECTR_CONFIG", "QUALITY_TEST_DEPRIORITISED",
            "QUALITY_DOC_PROSE", "QUALITY_SHORT_PENALTY",
        ):
            val = getattr(cfg, name)
            assert 0 < val <= 1.0, f"{name}={val} must be in (0, 1]"

    def test_chunk_quality_aliases_match_config(self) -> None:
        """chunk_quality.py _Q_* aliases must equal config exports (UPG-12.1)."""
        from agent.chunk_quality import (
            _Q_TRIVIAL, _Q_NAVIGATIONAL, _Q_HEADING_ONLY, _Q_GENERATED,
            _Q_VECTR_CONFIG, _Q_TEST_DEPRIORITISED, _Q_DOC_PROSE, _Q_SHORT_PENALTY,
        )
        assert _Q_TRIVIAL is cfg.QUALITY_TRIVIAL
        assert _Q_NAVIGATIONAL is cfg.QUALITY_NAVIGATIONAL
        assert _Q_HEADING_ONLY is cfg.QUALITY_HEADING_ONLY
        assert _Q_GENERATED is cfg.QUALITY_GENERATED
        assert _Q_VECTR_CONFIG is cfg.QUALITY_VECTR_CONFIG
        assert _Q_TEST_DEPRIORITISED is cfg.QUALITY_TEST_DEPRIORITISED
        assert _Q_DOC_PROSE is cfg.QUALITY_DOC_PROSE
        assert _Q_SHORT_PENALTY is cfg.QUALITY_SHORT_PENALTY


class TestConfigLoaderRerank:
    """ranking.rerank values must load correctly from config.yaml (UPG-12.1)."""

    def test_top_k_default(self) -> None:
        assert cfg.RERANK_TOP_K == 40, f"RERANK_TOP_K should be 40, got {cfg.RERANK_TOP_K}"

    def test_top_k_unfiltered_default(self) -> None:
        assert cfg.RERANK_TOP_K_UNFILTERED == 60, (
            f"RERANK_TOP_K_UNFILTERED should be 60, got {cfg.RERANK_TOP_K_UNFILTERED}"
        )

    def test_top_k_is_int(self) -> None:
        assert isinstance(cfg.RERANK_TOP_K, int)

    def test_top_k_unfiltered_is_int(self) -> None:
        assert isinstance(cfg.RERANK_TOP_K_UNFILTERED, int)

    def test_unfiltered_exceeds_filtered(self) -> None:
        """Unfiltered pool must be strictly larger than the filtered pool."""
        assert cfg.RERANK_TOP_K_UNFILTERED > cfg.RERANK_TOP_K, (
            f"RERANK_TOP_K_UNFILTERED ({cfg.RERANK_TOP_K_UNFILTERED}) must exceed "
            f"RERANK_TOP_K ({cfg.RERANK_TOP_K})"
        )

    def test_searcher_rerank_aliases_from_config(self) -> None:
        """searcher.py must import rerank pool sizes from config, not define its own."""
        import agent.searcher as searcher_mod
        assert searcher_mod._RERANK_TOP_K is cfg.RERANK_TOP_K
        assert searcher_mod._RERANK_TOP_K_UNFILTERED is cfg.RERANK_TOP_K_UNFILTERED


class TestConfigLoaderIndexing:
    """indexing.* values must load correctly from config.yaml (UPG-12.1)."""

    def test_max_chunk_lines_default(self) -> None:
        assert cfg.INDEXING_MAX_CHUNK_LINES == 150, (
            f"INDEXING_MAX_CHUNK_LINES should be 150, got {cfg.INDEXING_MAX_CHUNK_LINES}"
        )

    def test_class_header_lines_default(self) -> None:
        assert cfg.INDEXING_CLASS_HEADER_LINES == 40, (
            f"INDEXING_CLASS_HEADER_LINES should be 40, got {cfg.INDEXING_CLASS_HEADER_LINES}"
        )

    def test_max_chunk_lines_is_int(self) -> None:
        assert isinstance(cfg.INDEXING_MAX_CHUNK_LINES, int)

    def test_class_header_lines_is_int(self) -> None:
        assert isinstance(cfg.INDEXING_CLASS_HEADER_LINES, int)

    def test_class_header_lines_smaller_than_max_chunk_lines(self) -> None:
        """Class header must be a strict subset of max chunk size."""
        assert cfg.INDEXING_CLASS_HEADER_LINES < cfg.INDEXING_MAX_CHUNK_LINES, (
            f"INDEXING_CLASS_HEADER_LINES ({cfg.INDEXING_CLASS_HEADER_LINES}) must be "
            f"< INDEXING_MAX_CHUNK_LINES ({cfg.INDEXING_MAX_CHUNK_LINES})"
        )

    def test_indexer_aliases_from_config(self) -> None:
        """indexer.py must import chunk line limits from config, not define its own."""
        import agent.indexer as indexer_mod
        assert indexer_mod._MAX_CHUNK_LINES is cfg.INDEXING_MAX_CHUNK_LINES
        assert indexer_mod._CLASS_HEADER_LINES is cfg.INDEXING_CLASS_HEADER_LINES


class TestConfigLoaderOutput:
    """output.* values must load correctly from config.yaml (UPG-12.1)."""

    def test_snippet_lines_default(self) -> None:
        assert cfg.OUTPUT_SNIPPET_LINES == 12, (
            f"OUTPUT_SNIPPET_LINES should be 12, got {cfg.OUTPUT_SNIPPET_LINES}"
        )

    def test_snippet_lines_is_int(self) -> None:
        assert isinstance(cfg.OUTPUT_SNIPPET_LINES, int)

    def test_symbol_graph_snippet_lines_from_config(self) -> None:
        """symbol_graph.py SNIPPET_LINES must equal config export (UPG-12.1)."""
        from agent.symbol_graph import SNIPPET_LINES
        assert SNIPPET_LINES is cfg.OUTPUT_SNIPPET_LINES


class TestConfigLoaderBehavior:
    """behavior.remember_nudge values must load correctly from config.yaml (UPG-12.1)."""

    def test_threshold_default(self) -> None:
        assert cfg.BEHAVIOR_REMEMBER_NUDGE_THRESHOLD == 10, (
            f"BEHAVIOR_REMEMBER_NUDGE_THRESHOLD should be 10, got {cfg.BEHAVIOR_REMEMBER_NUDGE_THRESHOLD}"
        )

    def test_cooldown_default(self) -> None:
        assert cfg.BEHAVIOR_REMEMBER_NUDGE_COOLDOWN == 5, (
            f"BEHAVIOR_REMEMBER_NUDGE_COOLDOWN should be 5, got {cfg.BEHAVIOR_REMEMBER_NUDGE_COOLDOWN}"
        )

    def test_threshold_is_int(self) -> None:
        assert isinstance(cfg.BEHAVIOR_REMEMBER_NUDGE_THRESHOLD, int)

    def test_cooldown_is_int(self) -> None:
        assert isinstance(cfg.BEHAVIOR_REMEMBER_NUDGE_COOLDOWN, int)

    def test_cooldown_smaller_than_threshold(self) -> None:
        """Cooldown re-fire interval must be strictly less than the initial threshold."""
        assert cfg.BEHAVIOR_REMEMBER_NUDGE_COOLDOWN < cfg.BEHAVIOR_REMEMBER_NUDGE_THRESHOLD, (
            f"BEHAVIOR_REMEMBER_NUDGE_COOLDOWN ({cfg.BEHAVIOR_REMEMBER_NUDGE_COOLDOWN}) must be "
            f"< BEHAVIOR_REMEMBER_NUDGE_THRESHOLD ({cfg.BEHAVIOR_REMEMBER_NUDGE_THRESHOLD})"
        )

    def test_mcp_server_aliases_from_config(self) -> None:
        """mcp_server.py must import nudge tunables from config, not define its own."""
        from integrations.mcp_server import (
            _REMEMBER_NUDGE_THRESHOLD,
            _REMEMBER_NUDGE_COOLDOWN,
        )
        assert _REMEMBER_NUDGE_THRESHOLD is cfg.BEHAVIOR_REMEMBER_NUDGE_THRESHOLD
        assert _REMEMBER_NUDGE_COOLDOWN is cfg.BEHAVIOR_REMEMBER_NUDGE_COOLDOWN


class TestConfigLoaderDocIntent:
    """ranking.doc_intent values must load correctly from config.yaml (UPG-11.11)."""

    def test_suppress_forced_inclusion_default(self) -> None:
        """suppress_forced_inclusion defaults to True — doc-intent queries must
        suppress forced-inclusion so symbol-name tokens don't flood the pool."""
        assert cfg.DOC_INTENT_SUPPRESS_FORCED_INCLUSION is True, (
            f"DOC_INTENT_SUPPRESS_FORCED_INCLUSION should be True, "
            f"got {cfg.DOC_INTENT_SUPPRESS_FORCED_INCLUSION}"
        )

    def test_suppress_forced_inclusion_is_bool(self) -> None:
        assert isinstance(cfg.DOC_INTENT_SUPPRESS_FORCED_INCLUSION, bool)

    def test_doc_prose_multiplier_default(self) -> None:
        """doc_prose_multiplier defaults to 1.0 — no doc penalty on doc-intent queries."""
        assert cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER == pytest.approx(1.0, abs=0.001), (
            f"DOC_INTENT_DOC_PROSE_MULTIPLIER should be 1.0, "
            f"got {cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER}"
        )

    def test_doc_prose_multiplier_is_float(self) -> None:
        assert isinstance(cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER, float)

    def test_doc_prose_multiplier_exceeds_quality_doc_prose(self) -> None:
        """The doc-intent multiplier must be >= the normal doc_prose multiplier so
        doc chunks are not demoted below code on doc-intent queries."""
        assert cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER >= cfg.QUALITY_DOC_PROSE, (
            f"DOC_INTENT_DOC_PROSE_MULTIPLIER ({cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER}) "
            f"must be >= QUALITY_DOC_PROSE ({cfg.QUALITY_DOC_PROSE}) so documentation "
            f"can compete on doc-intent queries (UPG-11.11)."
        )

    def test_chunk_quality_imports_doc_intent_multiplier(self) -> None:
        """chunk_quality.py must import DOC_INTENT_DOC_PROSE_MULTIPLIER from config."""
        from agent.chunk_quality import _Q_DOC_PROSE_DOC_INTENT
        assert _Q_DOC_PROSE_DOC_INTENT is cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER

    def test_searcher_imports_doc_intent_suppress(self) -> None:
        """searcher.py must import DOC_INTENT_SUPPRESS_FORCED_INCLUSION from config."""
        from agent.searcher import _DOC_INTENT_SUPPRESS_FORCED_INCLUSION
        assert _DOC_INTENT_SUPPRESS_FORCED_INCLUSION is cfg.DOC_INTENT_SUPPRESS_FORCED_INCLUSION
