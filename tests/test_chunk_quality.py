"""Tests for agent/chunk_quality.py — Wave 1 chunk-quality heuristics."""
from __future__ import annotations

import pytest

from agent.chunk_quality import (
    NAVIGATIONAL_NODE_TYPE,
    is_trivial_chunk,
    is_navigational_chunk,
    is_markdown_heading_only,
    is_doc_language,
    is_vectr_config_file,
    is_generated_file,
    is_test_file,
    query_wants_tests,
    quality_score,
    normalized_content,
)


class TestIsTrivialChunk:
    @pytest.mark.parametrize("content", [
        "}",
        "};",
        ");",
        "#endif",
        "#else",
        "return 0;",
        "return NULL;",
        "return;",
        "pass",
        "...",
        "break;",
        "const log = std.log;",            # zig alias (single import line)
        "use crate::foo::Bar;",            # rust use
        "import os",                        # python import
        "from x import y",
        "#include <stdio.h>",
        "   ",                              # blank
        "// just a comment\n}",            # comment + punctuation
    ])
    def test_trivial(self, content):
        assert is_trivial_chunk(content) is True

    @pytest.mark.parametrize("content", [
        "def foo():\n    return compute(x) + 1",
        "int add(int a, int b) {\n    return a + b;\n}",
        "x = compute_total(items)",         # a real statement
        "pub fn resolve(&self) -> Result<Lock> {\n    self.inner.lock()\n}",
    ])
    def test_not_trivial(self, content):
        assert is_trivial_chunk(content) is False


class TestIsNavigationalChunk:
    def test_rust_reexport_block(self):
        content = "pub use crate::a::A;\npub use crate::b::B;\npub use crate::c::C;"
        assert is_navigational_chunk(content) is True

    def test_python_import_aggregator(self):
        content = "from .a import A\nfrom .b import B\nimport os"
        assert is_navigational_chunk(content) is True

    def test_single_import_is_not_navigational_block(self):
        # one line is handled by is_trivial_chunk, not the multi-line nav rule
        assert is_navigational_chunk("import os") is False

    def test_real_code_not_navigational(self):
        content = "import os\n\ndef main():\n    return os.getcwd()"
        assert is_navigational_chunk(content) is False


class TestMarkdownHeadingOnly:
    def test_heading_only(self):
        assert is_markdown_heading_only("## Analysis") is True

    def test_heading_with_body(self):
        assert is_markdown_heading_only("## Analysis\n\nThis section explains the resolver in detail with examples.") is False


class TestIsVectrConfigFile:
    @pytest.mark.parametrize("path", [
        "/proj/CLAUDE.md",
        "/proj/.cursor/mcp.json",
        "/proj/.vscode/mcp.json",
        "/proj/.mcp.json",
        "/proj/.github/copilot-instructions.md",
        "/proj/AGENTS.md",
    ])
    def test_config_files(self, path):
        assert is_vectr_config_file(path) is True

    @pytest.mark.parametrize("path", [
        "/proj/agent/indexer.py",
        "/proj/README.md",
        "/proj/docs/spec.md",
    ])
    def test_non_config(self, path):
        assert is_vectr_config_file(path) is False


class TestIsGeneratedFile:
    @pytest.mark.parametrize("path", [
        "/cpy/Modules/unicodetype_db.h",
        "/cpy/Include/internal/pycore_uop_metadata.h",
        "/proj/api/service.pb.go",
        "/proj/gen/schema_pb2.py",
        "/proj/static/app.min.js",
        "/cpy/Modules/clinic/gcmodule.c.h",
        "/proj/__generated__/types.ts",
    ])
    def test_generated(self, path):
        assert is_generated_file(path) is True

    @pytest.mark.parametrize("path", [
        "/proj/agent/indexer.py",
        "/proj/src/main.rs",
        "/proj/header.h",
    ])
    def test_not_generated(self, path):
        assert is_generated_file(path) is False


class TestIsTestFile:
    @pytest.mark.parametrize("path", [
        "/proj/tests/test_agent.py",
        "/proj/foo_test.go",
        "/proj/src/widget.test.ts",
        "/proj/src/widget.spec.js",
        "/proj/__tests__/util.js",
        "/proj/test/Helper.java",
    ])
    def test_is_test(self, path):
        assert is_test_file(path) is True

    @pytest.mark.parametrize("path", [
        "/proj/agent/indexer.py",
        "/proj/src/resolver.rs",
        "/proj/testing_utils.py",   # 'testing' in name but file itself isn't a test module
    ])
    def test_not_test(self, path):
        # testing_utils.py: name doesn't start with test_; dir not a test dir
        assert is_test_file(path) is False


class TestQueryWantsTests:
    @pytest.mark.parametrize("q", ["where is the test for resolver", "pytest fixture for db", "install scenario"])
    def test_wants(self, q):
        assert query_wants_tests(q) is True

    @pytest.mark.parametrize("q", ["how does dependency resolution work", "lock acquisition flow"])
    def test_not_wants(self, q):
        assert query_wants_tests(q) is False


class TestQualityScore:
    def test_real_code_is_high(self):
        s = quality_score("def f():\n    return compute(x)\n    # more\n    y = 1", file_path="/p/a.py")
        assert s >= 0.8

    def test_ordering(self):
        code = quality_score("def f():\n    return compute(x)\n    y=2\n    z=3", file_path="/p/a.py")
        test = quality_score("def test_f():\n    assert f() == 1\n    x=2\n    y=3", file_path="/p/tests/test_a.py")
        gen = quality_score("int table[] = {1,2,3};\nint b=4;\nint c=5;", file_path="/p/foo_db.h")
        nav = quality_score("pub use a::A;\npub use b::B;", file_path="/p/lib.rs")
        triv = quality_score("}", file_path="/p/a.c")
        cfg = quality_score("# vectr", file_path="/p/CLAUDE.md")
        assert code > test > gen
        assert gen > nav > triv
        assert cfg < triv  # vectr config is the lowest

    def test_test_file_not_penalised_when_query_targets_tests(self):
        path = "/p/tests/test_a.py"
        body = "def test_f():\n    assert f() == 1\n    x=2\n    y=3"
        normal = quality_score(body, file_path=path, query_targets_tests=False)
        wanted = quality_score(body, file_path=path, query_targets_tests=True)
        assert wanted > normal

    def test_navigational_node_type_short_circuits(self):
        s = quality_score("anything at all here", node_type=NAVIGATIONAL_NODE_TYPE)
        assert s == pytest.approx(0.35)


class TestIsDocLanguage:
    @pytest.mark.parametrize("lang", ["markdown", "md", "html", "htm", "rst", "text", "txt", "mdx", "HTML", "Markdown"])
    def test_doc_languages(self, lang):
        assert is_doc_language(lang) is True

    @pytest.mark.parametrize("lang", ["python", "rust", "c", "zig", "go", "javascript", "typescript", ""])
    def test_code_languages(self, lang):
        assert is_doc_language(lang) is False


class TestDocProseDemotion:
    """Substantive doc prose should rank below comparable implementation code (UPG-2.1 tuning)."""

    def test_doc_prose_demoted_below_code(self):
        body = (
            "This section walks through how the resolver acquires the workspace lock,\n"
            "what happens on contention, and how the PID-scoped guard is released.\n"
            "It is a real, substantive paragraph of documentation prose."
        )
        doc = quality_score(body, file_path="/p/guide.md", language="markdown")
        code = quality_score(
            "def f():\n    return compute(x)\n    y = 1\n    z = 2", file_path="/p/a.py", language="python"
        )
        assert code > doc
        # mild, not destructive: real prose stays well above trivial/navigational tiers
        assert doc == pytest.approx(0.70, abs=0.01)

    def test_html_prose_also_demoted(self):
        # 3+ meaningful lines so the ≤2-line short penalty doesn't also apply
        html = quality_score(
            "<section><p>A long descriptive paragraph about vector databases.</p>\n"
            "<p>A second paragraph describing nearest-neighbour search.</p>\n"
            "<p>A third paragraph on indexing.</p></section>",
            file_path="/p/index.html",
            language="html",
        )
        assert html == pytest.approx(0.70, abs=0.01)

    def test_generated_doc_compounds(self):
        # a generated markdown file is both doc-prose and generated → multipliers compound
        s = quality_score(
            "Auto-generated API reference paragraph one.\n"
            "Auto-generated paragraph two here.\n"
            "Auto-generated paragraph three closes it out.",
            file_path="/p/api.generated.md",
            language="markdown",
        )
        assert s == pytest.approx(0.45 * 0.70, abs=0.01)


class TestNormalizedContent:
    def test_collapses_whitespace_and_case(self):
        assert normalized_content("## Create   accounts\n\n") == "## create accounts"

    def test_identical_after_normalize(self):
        a = normalized_content("## Prompts used\n")
        b = normalized_content("##   Prompts Used")
        assert a == b
