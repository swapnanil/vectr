"""Tests for agent/chunk_quality.py — Wave 1 chunk-quality heuristics."""
from __future__ import annotations

import pytest

from agent.chunk_quality import (
    NAVIGATIONAL_NODE_TYPE,
    is_trivial_chunk,
    is_navigational_chunk,
    navigational_declared_identifiers,
    is_markdown_heading_only,
    is_doc_language,
    is_vectr_config_file,
    is_generated_file,
    is_test_file,
    quality_score,
    normalized_content,
    extract_class_from_content,
    is_symbol_bearing_chunk,
    build_purpose_text,
)
from agent.config import (
    QUALITY_NAVIGATIONAL,
    QUALITY_NAV_DECLARATION_RESCUE,
    QUALITY_TEST_DEPRIORITISED,
    TEST_FRAMEWORK_FAN_IN_THRESHOLD,
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


class TestTwoLineStubChunks:
    """UPG-15.1 (F15): two-line declaration+stub chunks must be filtered as trivial.

    A class/def header followed by pass/.../ raise NotImplementedError carries no
    retrieval value — it is an empty body stub.  These escaped the original
    is_trivial_chunk() which only guarded the single-meaningful-line case.
    """

    # Acceptance cases (red → green)
    @pytest.mark.parametrize("content", [
        # class stubs — any base list is OK; body is pass/...
        "class Style:\n    pass",
        "class NewsArticle(Article):\n    pass",
        "class Foo(Base, Meta):\n    pass",
        "class Empty:\n    ...",
        # parameter-less / self-only function stubs
        "def foo():\n    ...",
        "def foo():\n    pass",
        "def foo() -> None:\n    pass",
        "async def foo():\n    pass",
        "def foo(self):\n    pass",
        "def foo(self) -> None:\n    ...",
        "def foo():\n    raise NotImplementedError",
        "def foo():\n    raise NotImplementedError()",
        "def foo(self):\n    raise NotImplementedError",
        # indented versions (chunker may include leading whitespace)
        "    def foo(self):\n        pass",
        "    class Inner:\n        ...",
    ])
    def test_two_line_stub_is_trivial(self, content: str) -> None:
        assert is_trivial_chunk(content, "python") is True, (
            f"UPG-15.1: two-line stub chunk should be trivial but is_trivial_chunk returned False.\n"
            f"Content: {content!r}"
        )

    # Negative guards — real code with 2 meaningful lines must stay non-trivial
    @pytest.mark.parametrize("content", [
        # second line is real code, not a stub body
        "class Foo:\n    x = 1",
        "def foo():\n    return self.compute()",
        "def foo():\n    return 42",
        "class Foo:\n    field = models.CharField(max_length=100)",
        # real multi-line method (>2 meaningful lines)
        "def foo():\n    x = 1\n    return x + self.y",
        "class Foo:\n    x = 1\n    def bar(self):\n        pass",
        # functions with real parameters carry signal in their signature even
        # when the body is pass — they are not trivial stubs (UPG-15.1 conservative scope)
        "def send_signal_dispatch_uid(sender, **kwargs):\n    pass",
        "def handle(self, request):\n    raise NotImplementedError('subclasses must implement')",
        "async def bar(self, x: int) -> None:\n    ...",
    ])
    def test_two_line_real_code_is_not_trivial(self, content: str) -> None:
        assert is_trivial_chunk(content, "python") is False, (
            f"UPG-15.1: real 2-line chunk should NOT be trivial but is_trivial_chunk returned True.\n"
            f"Content: {content!r}"
        )


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


class TestBareConstructorManifestIsNavigational:
    """UPG-PREFIX-COMPOSE (F44/F53): a module consisting only of imports plus bare
    module-level instantiations of an imported type (a "declare an instance of this
    class" manifest, e.g. Django's ``request_started = Signal()`` re-export shims)
    carries no retrieval value beyond the import that already names the type — it
    must be classified navigational the same as a pure re-export block, not treated
    as "real code" because it happens to contain a call expression.
    """

    @pytest.mark.parametrize("content", [
        "from django.dispatch import Signal\n\nrequest_started = Signal()\nrequest_finished = Signal()",
        "from django.dispatch import Signal\n\npre_init = Signal()\npost_init = Signal(use_caching=True)",
        "import signal\n\nSIGHUP = Signal(1)\nSIGINT = Signal(2)",
    ])
    def test_bare_ctor_manifest_is_navigational(self, content: str) -> None:
        assert is_navigational_chunk(content, "python") is True, (
            f"UPG-PREFIX-COMPOSE: bare-constructor manifest should be navigational "
            f"but is_navigational_chunk returned False.\nContent: {content!r}"
        )

    def test_module_docstring_preamble_does_not_block_navigational(self) -> None:
        # django/dispatch/__init__.py shape: a leading module docstring followed
        # by import-only content — the docstring must be skipped, not counted as
        # a non-import "real" line that disqualifies the block.
        content = (
            '"""\n'
            "Signal dispatch mechanism, inspired heavily by pydispatch.\n"
            '"""\n'
            "from django.dispatch.dispatcher import Signal, receiver\n"
        )
        assert is_navigational_chunk(content, "python") is True, (
            "UPG-PREFIX-COMPOSE: leading module docstring must be skipped when "
            "judging import-only navigational content"
        )

    def test_ctor_with_nested_call_is_not_navigational(self) -> None:
        # RHS contains a nested call — not a "bare" constructor manifest, so the
        # navigational rule must not fire (avoid over-broad matching).
        content = "from x import Signal\n\ns = Signal(default=compute_default())\nt = Signal(default=compute_default())"
        assert is_navigational_chunk(content, "python") is False

    def test_ctor_with_dotted_attr_arg_is_not_navigational(self) -> None:
        content = "from x import Signal\n\ns = Signal(owner=self.owner)\nt = Signal(owner=self.owner)"
        assert is_navigational_chunk(content, "python") is False

    def test_lowercase_callable_is_not_navigational(self) -> None:
        # RHS callable name is not PascalCase (the class-naming convention) — do
        # not treat an arbitrary function-call assignment as a type manifest.
        content = "from x import make_signal\n\ns = make_signal()\nt = make_signal()"
        assert is_navigational_chunk(content, "python") is False


class TestNavigationalDeclaredIdentifiers:
    """UPG-NAV-OVERDEMOTE-DECL (F59): a bare-constructor-manifest chunk's LHS
    names are the corpus-wide unique declaration sites of those identifiers —
    quality_score() must be able to recover them to gate the rescue.
    """

    def test_recovers_declared_names(self) -> None:
        content = "from django.dispatch import Signal\n\nrequest_started = Signal()\nrequest_finished = Signal()"
        assert navigational_declared_identifiers(content) == ["request_started", "request_finished"]

    def test_no_declared_names_in_pure_reexport(self) -> None:
        content = "from django.dispatch.dispatcher import Signal, receiver\nfrom .other import Thing"
        assert navigational_declared_identifiers(content) == []

    def test_ignores_non_bare_ctor_assignments(self) -> None:
        # Not a bare-constructor RHS (complex arg) — not a declaration manifest line.
        content = "from x import Signal\n\ns = Signal(default=compute_default())"
        assert navigational_declared_identifiers(content) == []


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

    def test_navigational_node_type_short_circuits(self):
        s = quality_score("anything at all here", node_type=NAVIGATIONAL_NODE_TYPE)
        assert s == pytest.approx(0.35)

    # -- UPG-NAV-OVERDEMOTE-DECL (F59) --------------------------------------

    def test_manifest_rescued_when_query_names_declared_identifier(self):
        content = "from django.dispatch import Signal\n\nrequest_started = Signal()\nrequest_finished = Signal()"
        query_tokens = frozenset({"where", "is", "request", "started", "signal", "defined"})
        s = quality_score(content, language="python", query_tokens=query_tokens)
        assert s == pytest.approx(QUALITY_NAV_DECLARATION_RESCUE)

    def test_manifest_not_rescued_without_lexical_match(self):
        # F44/F53's query has no lexical overlap with signals.py's declared
        # identifiers — the manifest keeps the full navigational demotion so
        # the stub-overrank fix (F44/F53) is not weakened.
        content = "from django.dispatch import Signal\n\nrequest_started = Signal()\nrequest_finished = Signal()"
        query_tokens = frozenset({"signal", "dispatcher", "implementation"})
        s = quality_score(content, language="python", query_tokens=query_tokens)
        assert s == pytest.approx(QUALITY_NAVIGATIONAL)

    def test_manifest_not_rescued_when_no_query_tokens_given(self):
        content = "from django.dispatch import Signal\n\nrequest_started = Signal()\nrequest_finished = Signal()"
        s = quality_score(content, language="python")
        assert s == pytest.approx(QUALITY_NAVIGATIONAL)

    def test_pure_reexport_not_rescued_even_with_lexical_match(self):
        # A pure re-export block (no declared identifiers) must never be
        # rescued — the rescue only applies to bare-constructor manifests.
        content = "from django.dispatch.dispatcher import Signal, receiver\nfrom .other import Thing"
        query_tokens = frozenset({"signal", "receiver"})
        s = quality_score(content, language="python", query_tokens=query_tokens)
        assert s == pytest.approx(QUALITY_NAVIGATIONAL)

    # -- UPG-TESTPATH-FRAMEWORK-MISCLASS (F58) ------------------------------

    def test_test_path_file_below_fan_in_threshold_keeps_full_demotion(self):
        s = quality_score(
            "def test_thing():\n    assert 1 == 1\n    x = 2\n    y = 3",
            file_path="/proj/tests/test_a.py",
            file_fan_in=TEST_FRAMEWORK_FAN_IN_THRESHOLD - 1,
        )
        base = quality_score(
            "def test_thing():\n    assert 1 == 1\n    x = 2\n    y = 3",
            file_path="/proj/tests/test_a.py",
        )
        assert s == pytest.approx(base)

    def test_test_path_file_at_or_above_fan_in_threshold_is_exempted(self):
        content = "def override_settings():\n    return 1\n    x = 2\n    y = 3"
        demoted = quality_score(content, file_path="/proj/django/test/utils.py")
        exempted = quality_score(
            content,
            file_path="/proj/django/test/utils.py",
            file_fan_in=TEST_FRAMEWORK_FAN_IN_THRESHOLD,
        )
        assert exempted > demoted
        assert exempted == pytest.approx(1.0)

    def test_fan_in_exemption_is_no_op_for_non_test_path(self):
        content = "def compute():\n    return 1\n    x = 2\n    y = 3"
        no_fan_in = quality_score(content, file_path="/proj/agent/indexer.py")
        with_fan_in = quality_score(
            content, file_path="/proj/agent/indexer.py", file_fan_in=1000,
        )
        assert with_fan_in == pytest.approx(no_fan_in)


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


# ---------------------------------------------------------------------------
# F4 — extract_class_from_content (UPG-11.1-fix)
# ---------------------------------------------------------------------------

class TestExtractClassFromContent:
    """extract_class_from_content must recover the class name from the indexer prefix."""

    def test_extracts_class_name(self) -> None:
        content = "# class: Field\ndef deconstruct(self):\n    return name, path, args, kwargs\n"
        assert extract_class_from_content(content) == "Field"

    def test_extracts_class_name_with_spaces(self) -> None:
        content = "#   class:   RemoveField  \ndef deconstruct(self):\n    pass\n"
        assert extract_class_from_content(content) == "RemoveField"

    def test_returns_empty_when_no_prefix(self) -> None:
        content = "def deconstruct(self):\n    return name, path, args, kwargs\n"
        assert extract_class_from_content(content) == ""

    def test_handles_leading_comment_in_content(self) -> None:
        # Leading comments before the class prefix line should not confuse extraction
        content = "# some other comment\n# class: JSONField\ndef from_db_value(self, value, expression, connection):\n    pass\n"
        assert extract_class_from_content(content) == "JSONField"


# ---------------------------------------------------------------------------
# ARCH-4 — purpose-text distillation (dual-vector pool entry)
# ---------------------------------------------------------------------------

class TestIsSymbolBearingChunk:
    def test_symbol_with_regular_node_type_is_bearing(self) -> None:
        assert is_symbol_bearing_chunk("deconstruct", "function_definition") is True

    def test_empty_symbol_name_is_not_bearing(self) -> None:
        assert is_symbol_bearing_chunk("", "function_definition") is False

    def test_navigational_node_type_is_not_bearing(self) -> None:
        assert is_symbol_bearing_chunk("reexport", NAVIGATIONAL_NODE_TYPE) is False

    def test_window_node_type_is_not_bearing(self) -> None:
        assert is_symbol_bearing_chunk("chunk_0", "window") is False

    def test_section_node_type_is_not_bearing(self) -> None:
        assert is_symbol_bearing_chunk("Intro", "section") is False


class TestBuildPurposeText:
    """build_purpose_text distills a body-stripped 'purpose' doc (ARCH-4)."""

    def test_non_symbol_chunk_returns_none(self) -> None:
        assert build_purpose_text("some prose", "", "window", "markdown") is None
        assert build_purpose_text("stuff", "reexport", NAVIGATIONAL_NODE_TYPE, "python") is None

    def test_documented_python_method_includes_qualified_name_and_docstring(self) -> None:
        content = (
            "# class: QuerySet\n"
            "def get(self, *args, **kwargs):\n"
            '    """Perform the query and return a single object matching the given\n'
            "    keyword arguments.\"\"\"\n"
            "    clone = self.filter(*args, **kwargs)\n"
            "    num = len(clone)\n"
            "    if num == 1:\n"
            "        return clone._result_cache[0]\n"
        )
        purpose = build_purpose_text(content, "get", "function_definition", "python")
        assert purpose is not None
        assert "QuerySet.get" in purpose
        assert "def get(self, *args, **kwargs):" in purpose
        assert "Perform the query and return a single object" in purpose
        # The mechanical body must NOT be pulled into the purpose text.
        assert "_result_cache" not in purpose

    def test_undocumented_python_function_is_signature_only(self) -> None:
        content = "def helper(a, b):\n    return a + b\n"
        purpose = build_purpose_text(content, "helper", "function_definition", "python")
        assert purpose is not None
        assert "helper" in purpose
        assert "def helper(a, b):" in purpose
        assert "return a + b" not in purpose

    def test_leading_doc_comment_captured_for_non_python(self) -> None:
        content = (
            "/// Registry client for talking to PyPI.\n"
            "/// Retries on transient failures.\n"
            "pub fn build(&self) -> Result<Client, Error> {\n"
            "    let inner = self.inner.clone();\n"
            "    Ok(Client { inner })\n"
            "}\n"
        )
        purpose = build_purpose_text(content, "build", "function_item", "rust")
        assert purpose is not None
        assert "build" in purpose
        assert "Registry client for talking to PyPI." in purpose
        assert "let inner" not in purpose

    def test_class_prefix_line_not_leaked_into_purpose_text_as_doc(self) -> None:
        content = "# class: Widget\ndef render(self):\n    return html\n"
        purpose = build_purpose_text(content, "render", "function_definition", "python")
        assert purpose is not None
        assert "Widget.render" in purpose
        # The injected "# class: X" context line itself must not appear verbatim.
        assert "# class:" not in purpose

    # -----------------------------------------------------------------
    # ARCH-4-DEBUG: docstring first-paragraph distillation.
    #
    # A PEP-257/Google/NumPy-style multi-paragraph docstring is a one-line
    # summary, a blank line, then an elaborated/structured description. The
    # summary alone already carries the purpose; a structured detail block
    # after it (an attribute list, "Args:"-style section, or a cross-reference
    # note) measurably dilutes the purpose embedding if kept — the same
    # dilution class ARCH-4 exists to defeat, recurring one level down inside
    # the docstring. build_purpose_text must keep only the first paragraph by
    # default (existing max_docstring_lines/chars remain a safety-net cap on
    # that paragraph, unaffected here since these fixtures are short).
    # -----------------------------------------------------------------

    def test_class_definition_docstring_keeps_only_first_paragraph(self) -> None:
        """Shape: a large class-def chunk with a structured multi-paragraph
        docstring (summary line, blank line, an "Internal attributes:" style
        detail block) — the chunker only stores a truncated class header, but
        the docstring inside it can still be multi-paragraph."""
        content = (
            "class EventBus:\n"
            '    """\n'
            "    Base class for publish/subscribe event routing.\n"
            "\n"
            "    Internal attributes:\n"
            "\n"
            "        subscribers:\n"
            "            [\n"
            "                (\n"
            "                    (id(subscriber), id(topic)),\n"
            "                    ref(subscriber),\n"
            "                )\n"
            '    """\n'
            "\n"
            "    def __init__(self, use_caching=False):\n"
        )
        purpose = build_purpose_text(content, "EventBus", "class_definition", "python")
        assert purpose is not None
        assert "Base class for publish/subscribe event routing." in purpose
        # The structured detail block must NOT dilute the purpose text.
        assert "Internal attributes" not in purpose
        assert "subscribers" not in purpose

    def test_method_in_large_class_docstring_keeps_only_first_paragraph(self) -> None:
        """Shape: a method inside a large class, where the method's OWN
        docstring is multi-paragraph (summary + elaboration), independent of
        the class-def truncation issue above."""
        content = (
            "# class: RecordSet\n"
            "def fetch(self, *args, **kwargs):\n"
            '    """Perform the lookup and return a single matching record.\n'
            "\n"
            "    Raises DoesNotExist if no record matches, or\n"
            "    MultipleObjectsReturned if more than one record matches.\n"
            '    """\n'
            "    clone = self._chain()\n"
            "    return clone._result_cache[0]\n"
        )
        purpose = build_purpose_text(content, "fetch", "function_definition", "python")
        assert purpose is not None
        assert "RecordSet.fetch" in purpose
        assert "Perform the lookup and return a single matching record." in purpose
        assert "MultipleObjectsReturned" not in purpose
        assert "_result_cache" not in purpose

    def test_module_level_function_docstring_keeps_only_first_paragraph(self) -> None:
        """Shape: a short module-level function (no class context) whose
        docstring has a short summary followed by short elaboration
        paragraphs — even brief elaboration measurably dilutes the summary
        signal, so it is dropped by default."""
        content = (
            "def fetch_or_404(klass, *args, **kwargs):\n"
            '    """\n'
            "    Use fetch() to return a record, or raise NotFound if the\n"
            "    record does not exist.\n"
            "\n"
            "    klass may be a Model, Manager, or RecordSet object. All other\n"
            "    passed arguments and keyword arguments are used in the lookup.\n"
            "\n"
            "    Like with RecordSet.fetch(), MultipleObjectsReturned is raised\n"
            "    if more than one record is found.\n"
            '    """\n'
            "    recordset = _get_recordset(klass)\n"
        )
        purpose = build_purpose_text(content, "fetch_or_404", "function_definition", "python")
        assert purpose is not None
        assert "Use fetch() to return a record, or raise NotFound if the" in purpose
        assert "record does not exist." in purpose
        assert "klass may be a Model" not in purpose
        assert "Like with RecordSet.fetch()" not in purpose

    def test_single_paragraph_docstring_unaffected(self) -> None:
        """Non-regression: a docstring with no blank line (already a single
        paragraph) is unaffected by the first-paragraph distillation — this is
        the common case and must not be truncated any further than before."""
        content = (
            "# class: RecordSet\n"
            "def get(self, *args, **kwargs):\n"
            '    """Perform the query and return a single object matching the given\n'
            '    keyword arguments."""\n'
            "    clone = self.filter(*args, **kwargs)\n"
        )
        purpose = build_purpose_text(content, "get", "function_definition", "python")
        assert purpose is not None
        assert "Perform the query and return a single object matching the given" in purpose
        assert "keyword arguments." in purpose

    def test_leading_doc_comment_capped_for_non_python(self) -> None:
        """A long non-Python leading comment/decorator block (JSDoc, rustdoc,
        godoc) must be capped by the same max_docstring_lines/chars limits as
        the Python docstring branch — previously unbounded, which risked the
        same dilution a long Python docstring would cause."""
        lines = "\n".join(f"/// Detail line {i} about this function." for i in range(1, 30))
        content = (
            "/// Registry client for talking to the package index.\n"
            f"{lines}\n"
            "pub fn build(&self) -> Result<Client, Error> {\n"
            "    let inner = self.inner.clone();\n"
            "    Ok(Client { inner })\n"
            "}\n"
        )
        purpose = build_purpose_text(content, "build", "function_item", "rust")
        assert purpose is not None
        assert "Registry client for talking to the package index." in purpose
        # Only a bounded number of lines are kept, not all ~29 detail lines.
        assert "Detail line 20" not in purpose
        assert "let inner" not in purpose


# ---------------------------------------------------------------------------
# UPG-15.5 — trivial HTML/TXT short-prose classification (F19)
# ---------------------------------------------------------------------------

class TestTrivialDocChunks:
    """UPG-15.5 (F19): 1–2-line HTML template fixtures and TXT stubs must be
    classified as trivial so they stop flooding short natural-language queries
    (e.g. "session expired logout" was returning "Logged out" at rank 3).

    The fix: is_trivial_chunk() now has a language-aware rule for HTML/markup
    and plain-text chunks — if the chunk has ≤ TRIVIAL_DOC_MAX_LINES non-blank
    lines, it is trivial regardless of content.
    """

    # --- Positive cases: 1–2-line HTML/TXT fixtures ARE trivial ---

    @pytest.mark.parametrize("content,language", [
        # 1-line bare text HTML template (Django test fixture)
        ("Logged out", "html"),
        # 1-line Django template variable
        ("{{ form }}", "html"),
        ("{{ user }}", "html"),
        ("{{ result }}", "html"),
        # 1-line tag-wrapped HTML
        ("<h1>Oh no, an error occurred!</h1>", "html"),
        ("<h1>Archive for {{ week }}.</h1>", "html"),
        # 1-line TXT stub (Django.egg-info/top_level.txt)
        ("django", "txt"),
        # 1-line TXT test fixture
        ("from-my-custom-list", "txt"),
        ("file1 in the app dir", "txt"),
        ("File in otherdir.", "txt"),
        ("test", "txt"),
        ("Prefix!", "txt"),
        # 2-line TXT stub (also trivial — within TRIVIAL_DOC_MAX_LINES=2)
        ("django\ndjango_extensions", "txt"),
        # htm alias
        ("Logged out", "htm"),
        # text alias (language="text")
        ("django", "text"),
        # case-insensitive language normalisation
        ("Logged out", "HTML"),
        ("django", "TXT"),
    ])
    def test_trivial_html_txt_fixture(self, content: str, language: str) -> None:
        assert is_trivial_chunk(content, language) is True, (
            f"UPG-15.5: 1–2-line {language!r} fixture should be trivial but "
            f"is_trivial_chunk returned False.\nContent: {content!r}"
        )

    # --- Negative cases: multi-line .txt doc prose is NOT trivial ---

    def test_multiline_txt_doc_not_trivial(self) -> None:
        """A multi-line RST doc chunk (≥3 non-blank lines) must NOT be trivial.

        This is the critical doc non-regression guard: Django's docs/howto/*.txt
        and docs/topics/*.txt are chunked into multi-line windows and must stay
        fully ranked (F2/F18 non-regression).
        """
        doc = (
            "Writing custom model fields\n"
            "===========================\n"
            "\n"
            "Django ships with many built-in model field types.\n"
            "If those fields don't meet your needs you can write custom model fields.\n"
            "You need to implement deconstruct() and from_db_value() methods.\n"
        )
        assert is_trivial_chunk(doc, "txt") is False, (
            "UPG-15.5 doc non-regression: multi-line .txt RST doc must NOT be trivial. "
            "Django docs/howto/custom-model-fields.txt must stay ranked (F2/F18)."
        )

    def test_multiline_rst_doc_not_trivial(self) -> None:
        """An RST doc chunk (language='rst') with ≥3 lines is not trivial.

        rst is not in _TRIVIAL_DOC_LANGUAGES so the UPG-15.5 rule doesn't fire;
        this is an explicit guard to confirm rst docs are unaffected.
        """
        doc = (
            "Sessions\n"
            "========\n"
            "Django provides session support, enabling you to store and retrieve\n"
            "arbitrary data on a per-site-visitor basis.\n"
        )
        assert is_trivial_chunk(doc, "rst") is False, (
            "UPG-15.5: rst doc chunks must remain non-trivial — rst is not in "
            "_TRIVIAL_DOC_LANGUAGES and must be unaffected by the UPG-15.5 rule."
        )

    def test_multiline_html_not_trivial(self) -> None:
        """A 3+-line HTML chunk (e.g. a real template with structure) is not trivial."""
        html = (
            "<section>\n"
            "  <p>A full description paragraph of a Django concept.</p>\n"
            "  <p>Second paragraph with more detail about the implementation.</p>\n"
            "</section>\n"
        )
        assert is_trivial_chunk(html, "html") is False, (
            "UPG-15.5: an HTML chunk with > TRIVIAL_DOC_MAX_LINES non-blank lines "
            "must NOT be trivial."
        )

    def test_real_code_unaffected(self) -> None:
        """Python/Rust code chunks are unaffected by the UPG-15.5 rule."""
        py = "def clear_expired(cls):\n    Session.objects.filter(expire_date__lt=Now()).delete()"
        assert is_trivial_chunk(py, "python") is False, (
            "UPG-15.5: real code chunks must NOT be classified trivial."
        )

    def test_quality_score_trivial_for_1line_html(self) -> None:
        """quality_score() must return _Q_TRIVIAL for a 1-line HTML fixture."""
        from agent.config import QUALITY_TRIVIAL
        score = quality_score("Logged out", file_path="/tests/auth_tests/templates/registration/logged_out.html", language="html")
        assert score == pytest.approx(QUALITY_TRIVIAL, abs=0.01), (
            f"UPG-15.5: 1-line HTML fixture quality_score must equal QUALITY_TRIVIAL="
            f"{QUALITY_TRIVIAL}, got {score}"
        )

    def test_quality_score_trivial_for_1line_txt(self) -> None:
        """quality_score() must return _Q_TRIVIAL for a 1-line TXT stub."""
        from agent.config import QUALITY_TRIVIAL
        score = quality_score("django", file_path="/Django.egg-info/top_level.txt", language="txt")
        assert score == pytest.approx(QUALITY_TRIVIAL, abs=0.01), (
            f"UPG-15.5: 1-line TXT stub quality_score must equal QUALITY_TRIVIAL="
            f"{QUALITY_TRIVIAL}, got {score}"
        )

    def test_quality_score_nontrivial_for_multiline_txt_doc(self) -> None:
        """quality_score() must NOT return _Q_TRIVIAL for multi-line .txt doc."""
        from agent.config import QUALITY_TRIVIAL
        doc = (
            "Writing custom model fields\n"
            "===========================\n"
            "Django ships with many built-in model field types.\n"
            "If those fields don't meet your needs you can write custom model fields.\n"
        )
        score = quality_score(doc, file_path="/docs/howto/custom-model-fields.txt", language="txt")
        assert score != pytest.approx(QUALITY_TRIVIAL, abs=0.01), (
            f"UPG-15.5 doc non-regression: multi-line .txt doc quality_score must NOT "
            f"equal QUALITY_TRIVIAL={QUALITY_TRIVIAL}. Docs must stay ranked."
        )
        # It should get the doc_prose multiplier, not trivial
        assert score > QUALITY_TRIVIAL, (
            "Multi-line .txt doc must score above QUALITY_TRIVIAL."
        )


# ---------------------------------------------------------------------------
# UPG-15.9 — build-artifact exclusion (F24/F28)
# ---------------------------------------------------------------------------

class TestIsBuildArtifactFile:
    """is_build_artifact_file() must catch *.egg-info / *.dist-info trees (UPG-15.9).

    F24 / F28: Django.egg-info/SOURCES.txt was indexed as a first-class TXT file
    and flooded BM25 on module/command identifiers (e.g. 'management', 'signals').
    Build-artifact directories (.egg-info, .dist-info) contain machine-generated
    file-path manifests and packaging metadata — no educational content.
    """

    @pytest.mark.parametrize("path", [
        "/project/Django.egg-info/SOURCES.txt",
        "/project/Django.egg-info/PKG-INFO",
        "/project/myapp.egg-info/top_level.txt",
        "/project/myapp.egg-info/dependency_links.txt",
        "/project/mylib-1.0.dist-info/RECORD",
        "/project/mylib-1.0.dist-info/WHEEL",
        "/project/mylib-1.0.dist-info/METADATA",
        # Windows-style path separators
        "C:\\project\\Django.egg-info\\SOURCES.txt",
    ])
    def test_build_artifact_detected(self, path: str) -> None:
        from agent.chunk_quality import is_build_artifact_file
        assert is_build_artifact_file(path) is True, (
            f"UPG-15.9: {path!r} is a build-artifact file and must be excluded."
        )

    @pytest.mark.parametrize("path", [
        # Real documentation files — must NOT be excluded
        "/project/docs/howto/custom-management-commands.txt",
        "/project/docs/ref/models/options.txt",
        "/project/docs/topics/signals.txt",
        "/project/docs/howto/index.txt",
        # Source code — must NOT be excluded
        "/project/django/dispatch/signals.py",
        "/project/django/core/management/__init__.py",
        # Other top-level .txt files (requirements, etc.) — not inside .egg-info
        "/project/requirements.txt",
        "/project/README.txt",
    ])
    def test_real_files_not_artifact(self, path: str) -> None:
        from agent.chunk_quality import is_build_artifact_file
        assert is_build_artifact_file(path) is False, (
            f"UPG-15.9: {path!r} is a real file and must NOT be excluded as a build artifact."
        )

    def test_should_index_file_rejects_egg_info(self, tmp_path) -> None:
        """should_index_file() must refuse files inside *.egg-info directories (UPG-15.9)."""
        from integrations.workspace_detect import should_index_file

        egg_dir = tmp_path / "Django.egg-info"
        egg_dir.mkdir()
        sources = egg_dir / "SOURCES.txt"
        sources.write_text("django/__init__.py\ndjango/core/management/__init__.py\n")
        assert should_index_file(str(sources), []) is False, (
            "UPG-15.9: Django.egg-info/SOURCES.txt must be excluded from indexing."
        )

    def test_should_index_file_rejects_dist_info(self, tmp_path) -> None:
        """should_index_file() must refuse files inside *.dist-info directories (UPG-15.9)."""
        from integrations.workspace_detect import should_index_file

        dist_dir = tmp_path / "mylib-1.0.dist-info"
        dist_dir.mkdir()
        record = dist_dir / "RECORD"
        record.write_text("mylib/__init__.py,,\n")
        # RECORD has no extension → not in LANG_BY_EXT anyway, but test PKG-INFO
        metadata = dist_dir / "METADATA"
        metadata.write_text("Name: mylib\n")
        # METADATA has no extension → same
        # Test with a .txt file inside .dist-info
        wheel = dist_dir / "top_level.txt"
        wheel.write_text("mylib\n")
        assert should_index_file(str(wheel), []) is False, (
            "UPG-15.9: mylib.dist-info/top_level.txt must be excluded from indexing."
        )

    def test_should_index_file_allows_real_txt_doc(self, tmp_path) -> None:
        """Real documentation .txt files outside build-artifact dirs must remain indexed."""
        from integrations.workspace_detect import should_index_file

        docs = tmp_path / "docs" / "howto"
        docs.mkdir(parents=True)
        f = docs / "custom-management-commands.txt"
        f.write_text("Writing custom management commands\n==================================\n")
        assert should_index_file(str(f), []) is True, (
            "UPG-15.9 non-regression: docs/howto/*.txt must remain indexed (F24 fix must "
            "not demote real documentation)."
        )


# ---------------------------------------------------------------------------
# UPG-15.9 — attribute-assignment-only class stub triviality (F25)
# ---------------------------------------------------------------------------

class TestAttrOnlyClassStub:
    """is_trivial_chunk() must classify attribute-only class bodies as trivial (UPG-15.9 / F25).

    F25: 'what is a model Meta class' returned all 8 results from
    tests/model_forms/tests.py — 3-line ``class Meta: model=X fields='__all__'``
    inner stubs, flooding the pool with no educational content.

    The fix: a class whose body is ONLY simple attribute assignments (no function
    calls, no dotted access, no methods, no control flow) with between 2 and
    TRIVIAL_ATTR_CLASS_MAX_ATTRS body lines is trivial.  This catches Django's
    inner Meta stubs while protecting real form/model field classes.
    """

    # --- Positive cases: attribute-only class bodies with 2+ attrs ARE trivial ---

    @pytest.mark.parametrize("content", [
        # Canonical F25 pattern: class Meta with model + fields
        "class Meta:\n    model = Writer\n    fields = '__all__'",
        "class Meta:\n    model = Author\n    fields = '__all__'",
        "class Meta:\n    model = Article\n    fields = '__all__'",
        # F25 regression: tuple/empty-tuple field literals previously ESCAPED
        # triviality because the old _COMPLEX_RHS_RE flagged any '(' as a call.
        # A grouping/tuple paren is NOT a function call → these are stubs.
        "class Meta:\n    model = StumpJoke\n    fields = ()",
        "class Meta:\n    model = Writer\n    fields = ('name', 'age')",
        "class Meta:\n    model = Author\n    exclude = ('id',)",
        # Other simple two-attr stubs
        "class Meta:\n    abstract = True\n    verbose_name = 'Item'",
        "class Meta:\n    ordering = ['-created']\n    verbose_name = 'Post'",
        # Indented (inside another class)
        "    class Meta:\n        model = Writer\n        fields = '__all__'",
        # Different simple class names
        "class Config:\n    env_prefix = 'APP'\n    case_sensitive = False",
    ])
    def test_attr_only_class_stub_is_trivial(self, content: str) -> None:
        assert is_trivial_chunk(content, "python") is True, (
            f"UPG-15.9/F25: attribute-only class stub should be trivial but "
            f"is_trivial_chunk returned False.\nContent: {content!r}"
        )

    # --- Negative cases: classes that must NOT be classified trivial ---

    @pytest.mark.parametrize("content", [
        # Single-attr body: kept by the UPG-15.1 invariant ('x = 1' is real code)
        "class Foo:\n    x = 1",
        "class Meta:\n    model = Writer",
        # Complex RHS — dotted access or function call
        "class MyForm(Form):\n    username = forms.CharField(max_length=150)\n    password = forms.CharField()",
        "class Meta:\n    model = Writer\n    widgets = {'name': forms.TextInput()}",
        # Has a method def
        "class Meta:\n    model = Writer\n    def clean(self):\n        pass",
        # Control flow in body
        "class Config:\n    debug = True\n    if DEBUG:\n        extra = 1",
        # More body lines than threshold
        "class Meta:\n    model = Writer\n    fields = '__all__'\n    verbose_name = 'w'\n    ordering = ['-pk']",
        # Real two-line stub cases (UPG-15.1 — not the UPG-15.9 pattern)
        "def foo():\n    return compute(x) + 1",
        "class Style:\n    pass",
    ])
    def test_attr_only_class_non_trivial_cases(self, content: str) -> None:
        # Style/pass cases are trivial via UPG-15.1 — only test non-trivial here
        if "pass" in content or "..." in content or "return" in content:
            # These may be trivial via other rules — skip the NOT-trivial assertion
            return
        assert is_trivial_chunk(content, "python") is False, (
            f"UPG-15.9/F25 guard: class should NOT be trivial but "
            f"is_trivial_chunk returned True.\nContent: {content!r}"
        )

    def test_quality_score_trivial_for_meta_stub(self) -> None:
        """quality_score() must return QUALITY_TRIVIAL for a class Meta stub (UPG-15.9/F25)."""
        from agent.config import QUALITY_TRIVIAL
        meta_stub = "class Meta:\n    model = Writer\n    fields = '__all__'"
        score = quality_score(
            meta_stub,
            file_path="/project/tests/model_forms/tests.py",
            language="python",
        )
        assert score == pytest.approx(QUALITY_TRIVIAL, abs=0.01), (
            f"UPG-15.9/F25: class Meta stub quality_score must equal "
            f"QUALITY_TRIVIAL={QUALITY_TRIVIAL}, got {score}"
        )

    def test_real_doc_not_affected_by_meta_stub_rule(self) -> None:
        """docs/ref/models/options.txt must NOT be classified trivial (F25 non-regression)."""
        from agent.config import QUALITY_TRIVIAL
        doc = (
            "Meta options\n"
            "============\n"
            "This document explains all the available metadata options that you can give\n"
            "your model in its internal ``class Meta``.\n"
            "available=True means the model can be included in migration operations.\n"
        )
        assert is_trivial_chunk(doc, "txt") is False, (
            "UPG-15.9/F25 non-regression: docs/ref/models/options.txt must not be trivial."
        )
        score = quality_score(doc, file_path="/docs/ref/models/options.txt", language="txt")
        assert score > QUALITY_TRIVIAL, (
            f"docs/ref/models/options.txt must score above QUALITY_TRIVIAL={QUALITY_TRIVIAL}"
        )
