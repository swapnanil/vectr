"""
Tests for UPG-CSYM-REGRESSION: vectr must never silently advertise locate/trace
for a language whose tree-sitter grammar failed to load.

All grammar-missing scenarios are simulated via monkeypatch — no grammar
package is actually uninstalled.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import agent.symbol_graph as _sgmod
from agent.symbol_graph import (
    grammar_available,
    available_symbol_languages,
    SYMBOL_LANGUAGES,
    graph_toolchain_fingerprint,
    supports_symbols,
)


# ---------------------------------------------------------------------------
# grammar_available / available_symbol_languages
# ---------------------------------------------------------------------------

class TestGrammarAvailable:
    """grammar_available probes at call-time via _get_parser; returns False when
    the grammar is missing regardless of what SYMBOL_LANGUAGES declares."""

    def test_returns_true_for_all_installed_grammars(self) -> None:
        """Sanity check: all grammars in the dev environment are loadable."""
        for lang in SYMBOL_LANGUAGES:
            assert grammar_available(lang), f"Expected grammar_available({lang!r}) True in dev env"

    def test_returns_false_when_parser_returns_none(self, monkeypatch) -> None:
        """When _get_parser returns None for 'c', grammar_available('c') is False."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "c":
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        # Also patch through the re-export on agent.symbol_graph for consistency
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        assert grammar_available("c") is False

    def test_returns_true_for_non_c_when_c_missing(self, monkeypatch) -> None:
        """Other grammars still load even when 'c' is missing."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "c":
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        assert grammar_available("python") is True
        assert grammar_available("rust") is True
        assert grammar_available("go") is True

    def test_normalises_display_names(self, monkeypatch) -> None:
        """'C++' and 'CPP' both resolve to 'cpp' before the probe."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "cpp":
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        assert grammar_available("C++") is False
        assert grammar_available("cpp") is False
        assert grammar_available("CPP") is False

    def test_returns_false_for_unknown_language(self) -> None:
        """A language not in SYMBOL_LANGUAGES is always False, no parser call needed."""
        assert grammar_available("cobol") is False
        assert grammar_available("") is False
        assert grammar_available("markdown") is False

    def test_returns_false_for_language_not_in_symbol_languages(self) -> None:
        """Even if _get_parser happens to load something, non-symbol languages return False."""
        # 'markdown' is not in SYMBOL_LANGUAGES, so grammar_available must be False
        # regardless of whether some parser object exists.
        assert grammar_available("markdown") is False
        assert "markdown" not in SYMBOL_LANGUAGES


class TestAvailableSymbolLanguages:
    """available_symbol_languages() returns the subset of SYMBOL_LANGUAGES whose
    grammar actually loads."""

    def test_returns_frozenset(self) -> None:
        result = available_symbol_languages()
        assert isinstance(result, frozenset)

    def test_all_languages_present_when_all_installed(self) -> None:
        """In the dev environment all grammars are installed."""
        avail = available_symbol_languages()
        assert avail == SYMBOL_LANGUAGES

    def test_excludes_language_when_parser_returns_none(self, monkeypatch) -> None:
        """When _get_parser returns None for 'c', 'c' is excluded from the result."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "c":
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        avail = available_symbol_languages()
        assert "c" not in avail
        assert "python" in avail
        assert avail == SYMBOL_LANGUAGES - {"c"}

    def test_excludes_multiple_missing_grammars(self, monkeypatch) -> None:
        """Multiple missing grammars are all excluded."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser
        _missing = {"c", "cpp"}

        def _fake_get_parser(lang):
            if lang in _missing:
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        avail = available_symbol_languages()
        assert "c" not in avail
        assert "cpp" not in avail
        assert avail == SYMBOL_LANGUAGES - _missing


# ---------------------------------------------------------------------------
# graph_toolchain_fingerprint sensitivity to grammar availability
# ---------------------------------------------------------------------------

class TestFingerprintGrammarSensitivity:
    """The fingerprint must change when available_symbol_languages() changes, so
    that installing or removing a grammar triggers is_stale → rebuild."""

    def test_fingerprint_changes_when_c_unavailable(self, monkeypatch) -> None:
        """Removing 'c' from available_symbol_languages changes the fingerprint."""
        fp_full = graph_toolchain_fingerprint("model")

        # Patch available_symbol_languages on the package namespace — this is where
        # graph_toolchain_fingerprint reads it from (via _sg.available_symbol_languages).
        monkeypatch.setattr(
            _sgmod,
            "available_symbol_languages",
            lambda: SYMBOL_LANGUAGES - {"c"},
        )

        fp_no_c = graph_toolchain_fingerprint("model")
        assert fp_full != fp_no_c, (
            "Fingerprint must differ when grammar 'c' is unavailable — "
            "otherwise installing tree-sitter-c later won't trigger a graph rebuild"
        )

    def test_fingerprint_changes_when_multiple_grammars_unavailable(
        self, monkeypatch
    ) -> None:
        fp_full = graph_toolchain_fingerprint("model")

        monkeypatch.setattr(
            _sgmod,
            "available_symbol_languages",
            lambda: SYMBOL_LANGUAGES - {"c", "cpp"},
        )

        fp_no_c_cpp = graph_toolchain_fingerprint("model")
        assert fp_full != fp_no_c_cpp

    def test_fingerprint_stable_when_all_grammars_present(self) -> None:
        """Repeated calls without patching give the same result."""
        assert graph_toolchain_fingerprint("m") == graph_toolchain_fingerprint("m")

    def test_fingerprint_restores_after_monkeypatch(self, monkeypatch) -> None:
        """After monkeypatch undoes the patch the fingerprint returns to normal."""
        fp_before = graph_toolchain_fingerprint("m")

        monkeypatch.setattr(
            _sgmod,
            "available_symbol_languages",
            lambda: SYMBOL_LANGUAGES - {"c"},
        )
        fp_patched = graph_toolchain_fingerprint("m")
        assert fp_patched != fp_before

        monkeypatch.undo()
        fp_after = graph_toolchain_fingerprint("m")
        assert fp_after == fp_before

    def test_fingerprint_differs_from_static_symbol_languages_when_c_missing(
        self, monkeypatch
    ) -> None:
        """Regression guard: old static fingerprint ('parsers=' + sorted(SYMBOL_LANGUAGES))
        and new dynamic fingerprint ('parsers=' + sorted(available_symbol_languages()))
        diverge when a grammar is missing — proving the fix is effective."""
        import hashlib
        import agent.symbol_graph as _sg

        monkeypatch.setattr(
            _sgmod,
            "available_symbol_languages",
            lambda: SYMBOL_LANGUAGES - {"c"},
        )

        dynamic_fp = graph_toolchain_fingerprint("m")

        # Compute the OLD static fingerprint manually
        static_parts = [
            f"schema={_sg.SYMBOL_SCHEMA_VERSION}",
            "parsers=" + ",".join(sorted(SYMBOL_LANGUAGES)),
            "embed=m",
        ]
        static_fp = hashlib.sha256("|".join(static_parts).encode()).hexdigest()[:16]

        assert dynamic_fp != static_fp, (
            "Dynamic fingerprint must differ from static one when 'c' grammar is absent"
        )


# ---------------------------------------------------------------------------
# _language_coverage: symbols flag gated on grammar availability
# ---------------------------------------------------------------------------

class TestLanguageCoverageGrammarGated:
    """_language_coverage must report symbols=False for a language whose grammar
    is missing, even though supports_symbols() statically returns True."""

    def _make_service(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())

        # Write a C-like file so the indexer records 'c' in its language stats.
        # We use .c extension; the indexer records it as 'c'.
        c_file = tmp_path / "stub.c"
        c_file.write_text("int foo(void) { return 0; }\n")
        py_file = tmp_path / "stub.py"
        py_file.write_text("def foo(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))

        svc.index(str(tmp_path))
        return svc

    def test_symbols_false_when_c_grammar_unavailable(self, tmp_path, monkeypatch) -> None:
        """When 'c' grammar is not importable, the coverage row for 'c' has symbols=False."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "c":
                return None
            return original_get_parser(lang)

        # Apply patch before creating the service so all calls see it
        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        svc = self._make_service(tmp_path, monkeypatch)

        # Verify the static supports_symbols check would say True (confirming the
        # grammar_available gate is doing the work, not supports_symbols)
        assert supports_symbols("c") is True

        coverage = svc._language_coverage()
        c_rows = [row for row in coverage if row["language"] == "c"]
        if c_rows:
            assert c_rows[0]["symbols"] is False, (
                "symbols must be False for 'c' when the grammar is unavailable, "
                f"got {c_rows[0]}"
            )

    def test_symbols_true_for_python_even_when_c_grammar_unavailable(
        self, tmp_path, monkeypatch
    ) -> None:
        """Python grammar availability is independent of C grammar availability."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "c":
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        svc = self._make_service(tmp_path, monkeypatch)

        coverage = svc._language_coverage()
        py_rows = [row for row in coverage if row["language"] == "python"]
        assert py_rows, "Expected python in language coverage"
        assert py_rows[0]["symbols"] is True, (
            f"Python should still have symbols=True when C grammar is missing, got {py_rows[0]}"
        )


# ---------------------------------------------------------------------------
# status() surfaces grammars_unavailable
# ---------------------------------------------------------------------------

class TestStatusGrammarsUnavailable:
    """status() must include 'grammars_unavailable' key listing missing grammars."""

    def _make_service(self, tmp_path, monkeypatch):
        from agent import indexer as idx_module
        from tests.conftest import _DummyEmbedProvider

        monkeypatch.setattr(idx_module, "get_embed_provider", lambda _: _DummyEmbedProvider())
        py_file = tmp_path / "stub.py"
        py_file.write_text("def foo(): pass\n")

        with patch("integrations.vscode_bridge.configure_all"), \
             patch("integrations.workspace_detect.find_workspace_root", return_value=str(tmp_path)), \
             patch.dict("os.environ", {"VECTR_DB_DIR": str(tmp_path / "db")}):
            from app.service import VectrService
            svc = VectrService(workspace_root=str(tmp_path))

        return svc

    def test_grammars_unavailable_key_present_always(self, tmp_path, monkeypatch) -> None:
        """The key is always present in the status dict."""
        svc = self._make_service(tmp_path, monkeypatch)
        status = svc.status()
        assert "grammars_unavailable" in status

    def test_grammars_unavailable_empty_when_all_present(self, tmp_path, monkeypatch) -> None:
        """When all grammars load, grammars_unavailable is empty."""
        svc = self._make_service(tmp_path, monkeypatch)
        status = svc.status()
        assert status["grammars_unavailable"] == [], (
            f"Expected empty list when all grammars present, got {status['grammars_unavailable']}"
        )

    def test_grammars_unavailable_lists_missing_grammar(self, tmp_path, monkeypatch) -> None:
        """When 'c' grammar is missing, grammars_unavailable includes 'c'."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang == "c":
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        svc = self._make_service(tmp_path, monkeypatch)
        status = svc.status()
        assert "c" in status["grammars_unavailable"], (
            f"Expected 'c' in grammars_unavailable, got {status['grammars_unavailable']}"
        )

    def test_grammars_unavailable_sorted(self, tmp_path, monkeypatch) -> None:
        """The list is sorted for deterministic output."""
        import agent.indexer as _idx

        original_get_parser = _idx._get_parser

        def _fake_get_parser(lang):
            if lang in {"c", "cpp"}:
                return None
            return original_get_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_get_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_get_parser)

        svc = self._make_service(tmp_path, monkeypatch)
        status = svc.status()
        missing = status["grammars_unavailable"]
        assert missing == sorted(missing), f"grammars_unavailable should be sorted, got {missing}"
        assert "c" in missing
        assert "cpp" in missing


# ---------------------------------------------------------------------------
# MCP status output surfaces grammar warning
# ---------------------------------------------------------------------------

class TestMCPStatusGrammarWarning:
    """vectr_status MCP output must include a visible warning when grammars are missing."""

    def test_warning_line_present_when_grammar_missing(self) -> None:
        """When grammars_unavailable is non-empty, status output contains a warning."""
        from integrations.mcp_server._dispatch import handle_tools_call

        svc = MagicMock()
        svc.status.return_value = {
            "indexed_files": 100,
            "total_chunks": 500,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "test-model",
            "workspace_root": "/repo",
            "symbol_count": 200,
            "notes_count": 0,
            "languages": [
                {"language": "python", "files": 80, "chunks": 400, "symbols": True},
                {"language": "c", "files": 20, "chunks": 100, "symbols": False},
            ],
            "grammars_unavailable": ["c"],
        }
        svc.count_notes.return_value = 0
        svc.suggest_instruction_style.return_value = "additive"
        svc._eviction_advisor = MagicMock()

        result = handle_tools_call("vectr_status", {}, svc, session_id="test")
        text = result["content"][0]["text"]

        assert "WARNING" in text or "warning" in text.lower() or "not importable" in text, (
            f"Expected grammar warning in status output, got:\n{text}"
        )
        assert "c" in text

    def test_no_warning_when_all_grammars_present(self) -> None:
        """When grammars_unavailable is empty, no warning line is added."""
        from integrations.mcp_server._dispatch import handle_tools_call

        svc = MagicMock()
        svc.status.return_value = {
            "indexed_files": 100,
            "total_chunks": 500,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "test-model",
            "workspace_root": "/repo",
            "symbol_count": 200,
            "notes_count": 0,
            "languages": [
                {"language": "python", "files": 80, "chunks": 400, "symbols": True},
            ],
            "grammars_unavailable": [],
        }
        svc.count_notes.return_value = 0
        svc.suggest_instruction_style.return_value = "additive"
        svc._eviction_advisor = MagicMock()

        result = handle_tools_call("vectr_status", {}, svc, session_id="test")
        text = result["content"][0]["text"]

        assert "not importable" not in text, (
            f"Expected no grammar warning when all grammars present, got:\n{text}"
        )

    def test_warning_includes_remediation(self) -> None:
        """The warning line must include the remediation command."""
        from integrations.mcp_server._dispatch import handle_tools_call

        svc = MagicMock()
        svc.status.return_value = {
            "indexed_files": 10,
            "total_chunks": 50,
            "last_indexed": "2026-01-01T00:00:00Z",
            "embed_model": "test-model",
            "workspace_root": "/repo",
            "symbol_count": 0,
            "notes_count": 0,
            "languages": [],
            "grammars_unavailable": ["c", "cpp"],
        }
        svc.count_notes.return_value = 0
        svc.suggest_instruction_style.return_value = "additive"
        svc._eviction_advisor = MagicMock()

        result = handle_tools_call("vectr_status", {}, svc, session_id="test")
        text = result["content"][0]["text"]

        assert "pip install" in text or "reinstall" in text, (
            f"Expected remediation hint in status warning, got:\n{text}"
        )
        assert "c" in text
        assert "cpp" in text


# ---------------------------------------------------------------------------
# _preflight_grammars: auto-install missing grammars at vectr start
# ---------------------------------------------------------------------------

class TestPreflightGrammars:
    """_preflight_grammars must auto-install missing grammars or warn and continue."""

    def _make_fake_parser(self, missing_langs):
        """Return a fake _get_parser that returns None for missing_langs."""
        import agent.indexer as _idx
        original = _idx._get_parser

        def _fake(lang):
            if lang in missing_langs:
                return None
            return original(lang)

        return _fake

    def test_no_op_when_all_grammars_present(self, monkeypatch, capsys) -> None:
        """When all grammars are present, _preflight_grammars does nothing."""
        # No monkeypatching of _get_parser — all grammars are present.
        pip_calls: list[str] = []

        def _fake_pip(req: str):
            pip_calls.append(req)
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        from main import _preflight_grammars
        _preflight_grammars(_run_pip=_fake_pip)

        assert pip_calls == [], f"Expected no pip call when all grammars present, got {pip_calls}"
        captured = capsys.readouterr()
        assert captured.err == "" or "grammar" not in captured.err.lower(), (
            f"Expected no grammar output when all present, got: {captured.err}"
        )

    def test_installs_missing_grammar_on_success(self, monkeypatch, capsys) -> None:
        """When 'c' grammar is missing and pip succeeds, it is installed and verified."""
        import agent.indexer as _idx

        original_parser = _idx._get_parser
        # Track whether install was called; after install, fake that 'c' loads.
        installed: list[str] = []

        def _fake_parser(lang):
            if lang == "c" and lang not in installed:
                return None
            return original_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_parser)

        def _fake_pip(req: str):
            installed.append("c")  # mark as installed
            class R:
                returncode = 0
                stdout = "Successfully installed"
                stderr = ""
            return R()

        from main import _preflight_grammars
        _preflight_grammars(_run_pip=_fake_pip)

        assert len(installed) > 0, "Expected pip install to be attempted"
        captured = capsys.readouterr()
        # Should show install attempt
        assert "c" in captured.err or "tree-sitter" in captured.err, (
            f"Expected install message for 'c', got: {captured.err}"
        )

    def test_warns_and_continues_on_install_failure(self, monkeypatch, capsys) -> None:
        """When pip install fails, _preflight_grammars prints a warning and returns (no crash)."""
        import agent.indexer as _idx

        original_parser = _idx._get_parser

        def _fake_parser(lang):
            if lang == "c":
                return None
            return original_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_parser)

        def _failing_pip(req: str):
            class R:
                returncode = 1
                stdout = ""
                stderr = "error: externally-managed-environment"
            return R()

        from main import _preflight_grammars
        # Must not raise or exit
        _preflight_grammars(_run_pip=_failing_pip)

        captured = capsys.readouterr()
        # Should print the remediation message
        assert "WARNING" in captured.err or "could not" in captured.err.lower(), (
            f"Expected warning on install failure, got: {captured.err}"
        )
        assert "pip install" in captured.err, (
            f"Expected pip install remediation in output, got: {captured.err}"
        )
        assert "c" in captured.err

    def test_remediation_message_mentions_externally_managed(self, monkeypatch, capsys) -> None:
        """Failure message must mention externally-managed environments."""
        import agent.indexer as _idx

        original_parser = _idx._get_parser

        def _fake_parser(lang):
            if lang == "c":
                return None
            return original_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_parser)

        def _failing_pip(req: str):
            class R:
                returncode = 1
                stdout = ""
                stderr = "error"
            return R()

        from main import _preflight_grammars
        _preflight_grammars(_run_pip=_failing_pip)

        captured = capsys.readouterr()
        assert "externally-managed" in captured.err or "virtualenv" in captured.err or "venv" in captured.err, (
            f"Expected externally-managed environment note in failure message, got: {captured.err}"
        )

    def test_does_not_add_break_system_packages_automatically(self, monkeypatch, capsys) -> None:
        """The preflight must NEVER pass --break-system-packages to pip itself."""
        import agent.indexer as _idx

        original_parser = _idx._get_parser

        def _fake_parser(lang):
            if lang == "c":
                return None
            return original_parser(lang)

        monkeypatch.setattr(_idx, "_get_parser", _fake_parser)
        monkeypatch.setattr(_sgmod, "_get_parser", _fake_parser)

        pip_args: list[str] = []

        def _capture_pip(req: str):
            pip_args.append(req)
            class R:
                returncode = 1
                stdout = ""
                stderr = "error"
            return R()

        from main import _preflight_grammars
        _preflight_grammars(_run_pip=_capture_pip)

        for arg in pip_args:
            assert "--break-system-packages" not in arg, (
                f"_preflight_grammars must not add --break-system-packages: {arg}"
            )
