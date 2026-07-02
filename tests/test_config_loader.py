"""Tests for agent/config.py — bundled-config loader (UPG-11.1 refactor-to-config).

Verifies that:
1. config.yaml is loaded and exposes the correct numeric knobs with expected defaults.
2. The loader works via importlib.resources (not cwd-relative open()), confirmed by
   importing the module directly (same path the installed binary uses).
3. Quality priors, rerank pool sizes, indexing tunables, output knobs, and behaviour
   nudge thresholds all load correctly from the new config blocks (UPG-12.1).
"""
from __future__ import annotations

import pytest

import agent.config as cfg


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
        # UPG-15.7: top_k_unfiltered reverted to 60 (the reranker pool size).
        # Trivial HTML/TXT flooding is now prevented by the pool-entry filter
        # (pre_filter_fetch_k over-fetches, drops trivial, trims to top_k_unfiltered).
        assert cfg.RERANK_TOP_K_UNFILTERED == 60, (
            f"RERANK_TOP_K_UNFILTERED should be 60 (UPG-15.7), got {cfg.RERANK_TOP_K_UNFILTERED}"
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

    def test_pre_filter_fetch_k_default(self) -> None:
        # UPG-15.7: over-fetch depth for the pool-entry trivial filter.
        # Must be strictly larger than top_k_unfiltered so there is room to filter
        # trivial chunks and still fill the rerank pool.
        assert cfg.RERANK_PRE_FILTER_FETCH_K >= 200, (
            f"RERANK_PRE_FILTER_FETCH_K should be ≥ 200 (UPG-15.7), got {cfg.RERANK_PRE_FILTER_FETCH_K}"
        )

    def test_pre_filter_fetch_k_is_int(self) -> None:
        assert isinstance(cfg.RERANK_PRE_FILTER_FETCH_K, int)

    def test_pre_filter_fetch_k_exceeds_top_k_unfiltered(self) -> None:
        """pre_filter_fetch_k must exceed top_k_unfiltered so there is room to filter."""
        assert cfg.RERANK_PRE_FILTER_FETCH_K > cfg.RERANK_TOP_K_UNFILTERED, (
            f"RERANK_PRE_FILTER_FETCH_K ({cfg.RERANK_PRE_FILTER_FETCH_K}) must exceed "
            f"RERANK_TOP_K_UNFILTERED ({cfg.RERANK_TOP_K_UNFILTERED})"
        )

    def test_searcher_rerank_aliases_from_config(self) -> None:
        """searcher.py must import rerank pool sizes from config, not define its own."""
        import agent.searcher as searcher_mod
        assert searcher_mod._RERANK_TOP_K is cfg.RERANK_TOP_K
        assert searcher_mod._RERANK_TOP_K_UNFILTERED is cfg.RERANK_TOP_K_UNFILTERED
        assert searcher_mod._RERANK_PRE_FILTER_FETCH_K is cfg.RERANK_PRE_FILTER_FETCH_K


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


class TestConfigLoaderWorkspaceAndWatcher:
    """workspace.* / watcher.* values must load correctly from config.yaml (UPG-13.1/13.2)."""

    def test_default_vectrignore_dirs_is_nonempty_tuple(self) -> None:
        assert isinstance(cfg.WORKSPACE_DEFAULT_VECTRIGNORE_DIRS, tuple)
        assert len(cfg.WORKSPACE_DEFAULT_VECTRIGNORE_DIRS) > 0

    def test_default_vectrignore_dirs_covers_common_excludes(self) -> None:
        expected = {
            "node_modules", ".venv", "venv", "env", "__pycache__", ".git",
            "dist", "build", "target", ".mypy_cache", ".pytest_cache",
            ".ruff_cache", "htmlcov", "coverage", ".tox", ".cache", "tmp",
            "vendor", ".next", ".nuxt", "out",
        }
        assert expected <= set(cfg.WORKSPACE_DEFAULT_VECTRIGNORE_DIRS)

    def test_default_vectrignore_dirs_entries_are_strings(self) -> None:
        for d in cfg.WORKSPACE_DEFAULT_VECTRIGNORE_DIRS:
            assert isinstance(d, str)

    def test_top_level_rescan_interval_is_positive_float(self) -> None:
        assert isinstance(cfg.WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S, float)
        assert cfg.WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S > 0

    def test_watcher_aliases_from_config(self) -> None:
        """watcher.py must import the rescan interval from config, not hardcode it."""
        from agent.watcher import WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S
        assert WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S is cfg.WATCHER_TOP_LEVEL_RESCAN_INTERVAL_S
