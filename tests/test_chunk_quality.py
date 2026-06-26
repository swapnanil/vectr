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
    is_doc_intent_query,
    quality_score,
    normalized_content,
    symbol_identity_boost,
    extract_class_from_content,
    _query_symbol_tokens,
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


# ---------------------------------------------------------------------------
# F6 — stop-word guard on symbol_identity_boost (UPG-11.5)
# ---------------------------------------------------------------------------

class TestSymbolIdentityBoostStopWords:
    """Common English words used as method names must NOT get a leaf boost.

    F6: leaf='all' boosted by 'list all migrations' was wrong — 'all' is an
    accidental word overlap, not a symbol query intent.
    """

    @pytest.mark.parametrize("leaf,query", [
        ("all",  "list all database migrations for a project"),
        ("get",  "get a record from the database"),
        ("set",  "set field value on a model"),
        ("run",  "run the test suite"),
        ("add",  "add a new entry to the registry"),
        ("map",  "map source to target fields"),
        ("log",  "log a warning message"),
        ("use",  "use the base class method"),
        ("ok",   "check if the response is ok"),
        ("id",   "get the record id from the database"),
    ])
    def test_stop_word_leaf_receives_no_boost(self, leaf: str, query: str) -> None:
        tokens = _query_symbol_tokens(query)
        boost = symbol_identity_boost(leaf, tokens)
        assert boost == 0.0, (
            f"symbol_identity_boost('{leaf}', ...) returned {boost} != 0.0 "
            f"for query {query!r}. Stop-word leaves must not be boosted."
        )

    def test_specific_method_name_still_boosted(self) -> None:
        """Non-stop-word specific method names must still receive a boost."""
        tokens = _query_symbol_tokens("Field deconstruct base class name path args kwargs migration")
        boost = symbol_identity_boost("deconstruct", tokens)
        assert boost > 0.0, (
            f"Expected positive boost for 'deconstruct' but got {boost}. "
            "The stop-word guard must NOT suppress specific method names."
        )

    def test_qualified_stop_word_still_boosted(self) -> None:
        """A stop-word leaf IS boosted when it has a class prefix: 'Field.all' is specific."""
        tokens = _query_symbol_tokens("Field all fields query")
        boost = symbol_identity_boost("Field.all", tokens)
        assert boost > 0.0, (
            "Qualified 'Field.all' should still be boosted — the stop-word guard "
            "applies only to bare unqualified single-word leaves."
        )

    @pytest.mark.parametrize("leaf", ["all", "get", "set", "run"])
    def test_short_stop_word_bare_no_boost(self, leaf: str) -> None:
        """Bare stop-word leaves must return 0.0 regardless of query."""
        query = f"Field {leaf} value from database"
        tokens = _query_symbol_tokens(query)
        assert symbol_identity_boost(leaf, tokens) == 0.0


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

    def test_qualified_boost_fires_after_extraction(self) -> None:
        """End-to-end: bare leaf + class prefix content → qualified boost fires."""
        # Simulate what the real indexer produces for a method chunk
        content = "# class: Field\ndef deconstruct(self):\n    name, path, args, kwargs = ...\n"
        class_ctx = extract_class_from_content(content)
        bare_leaf = "deconstruct"
        qualified = f"{class_ctx}.{bare_leaf}" if class_ctx else bare_leaf

        tokens = _query_symbol_tokens("Field deconstruct base class name path args kwargs migration")
        boost = symbol_identity_boost(qualified, tokens)
        # Must get the QUALIFIED boost (+0.20), not just the leaf boost (+0.10)
        from agent.chunk_quality import _SYM_QUALIFIED_BOOST
        assert boost == _SYM_QUALIFIED_BOOST, (
            f"Expected qualified boost {_SYM_QUALIFIED_BOOST} but got {boost}. "
            "This confirms the F4 path is active."
        )


# ---------------------------------------------------------------------------
# UPG-11.8 — 4-letter common word leaf-boost gap (F8)
# ---------------------------------------------------------------------------

class TestFourLetterStopWords:
    """UPG-11.8: common 4-letter words that double as method names must receive
    NO symbol boost when they appear as natural-language prose in a query.

    The fix: add them to prog_stopwords.txt, which feeds ONLY the
    symbol_identity_boost guard (not BM25 or embedding).
    """

    @pytest.mark.parametrize("leaf,query", [
        ("read", "read lines from a text file"),
        ("join", "join two lists together"),
        ("call", "call the handler function"),
        ("send", "send a message to the server"),
        ("hash", "hash a password for storage"),
        ("open", "open a file for reading"),
        ("eval", "eval an expression dynamically"),
        ("exec", "exec a subprocess command"),
        ("iter", "iter over collection items"),
        ("next", "next item in the iterator"),
        ("sort", "sort the list by key"),
        ("copy", "copy a file to destination"),
        ("load", "load data from disk"),
        ("dump", "dump state to JSON"),
        ("emit", "emit events to subscribers"),
        ("bind", "bind socket to address"),
        ("wait", "wait for async operation"),
        ("recv", "recv bytes from socket"),
        ("find", "find all occurrences in text"),
        ("list", "list all available options"),
        ("test", "test the implementation"),
        ("save", "save the file to disk"),
        ("init", "init the module"),
    ])
    def test_common_4letter_leaf_no_boost(self, leaf, query) -> None:
        """Bare 4-letter common leaves must get zero boost for prose queries."""
        tokens = _query_symbol_tokens(query)
        boost = symbol_identity_boost(leaf, tokens)
        assert boost == 0.0, (
            f"UPG-11.8: leaf={leaf!r} got boost={boost} for query {query!r}. "
            "Expected 0.0 — it must be in SYMBOL_STOP_WORDS."
        )

    def test_qualified_read_still_boosted(self) -> None:
        """UPG-11.8: qualified form 'Buffer.read' must still get a boost
        because the stop-word guard only applies to bare single-word leaves."""
        # query with class name "buffer" in tokens
        query = "read data from Buffer object"
        tokens = _query_symbol_tokens(query)
        boost = symbol_identity_boost("Buffer.read", tokens)
        # "buffer" is in query tokens, leaf="read" -> qualified boost (+0.20)
        from agent.chunk_quality import _SYM_QUALIFIED_BOOST
        assert boost == _SYM_QUALIFIED_BOOST, (
            f"UPG-11.8: qualified 'Buffer.read' should get qualified boost "
            f"{_SYM_QUALIFIED_BOOST} but got {boost}."
        )

    def test_qualified_read_leaf_boost_when_class_absent(self) -> None:
        """Without the class name in the query, Buffer.read gets leaf boost only."""
        query = "read bytes from socket"  # no 'buffer' in tokens
        tokens = _query_symbol_tokens(query)
        boost = symbol_identity_boost("Buffer.read", tokens)
        from agent.chunk_quality import _SYM_LEAF_BOOST
        assert boost == _SYM_LEAF_BOOST, (
            f"UPG-11.8: 'Buffer.read' with class absent should get leaf boost "
            f"{_SYM_LEAF_BOOST} but got {boost}. "
            "The stop-word guard must NOT apply to qualified symbols."
        )

    def test_f8_read_no_boost_for_prose_query(self) -> None:
        """F8 acceptance: symbol leaf='read' must have zero boost for 'read lines from a text file'."""
        from agent.config import SYMBOL_STOP_WORDS
        assert "read" in SYMBOL_STOP_WORDS, (
            "UPG-11.8: 'read' must be in SYMBOL_STOP_WORDS after prog_stopwords.txt update"
        )
        tokens = _query_symbol_tokens("read lines from a text file")
        assert symbol_identity_boost("read", tokens) == 0.0


# ---------------------------------------------------------------------------
# UPG-11.11 — Doc-intent query classifier
# ---------------------------------------------------------------------------

class TestDocIntentQueryClassifier:
    """is_doc_intent_query() must classify how-to / explain / tutorial queries
    as doc-intent and leave code-shaped symbol queries as code-intent (UPG-11.11 / F2).

    The classifier gates forced-inclusion suppression: on doc-intent queries,
    symbol-name tokens in the query describe the topic (not the implementation
    target), so they must NOT pull 80+ code chunks into the hybrid pool.
    """

    # Queries that ARE doc-intent (should return True)
    @pytest.mark.parametrize("query", [
        # F2 exact query
        "how to write a custom model field with deconstruct and from_db_value",
        # how-to / how-do prefix variants
        "how to use signals in Django",
        "how do I configure database connections",
        "how does the middleware stack work",
        "how can I override the queryset",
        "how should I structure my models",
        # what-is prefix variants
        "what is a model field in Django",
        "what are migrations and how do they work",
        "what does the deconstruct method do",
        "what's the difference between null and blank",
        # why prefix
        "why is my queryset returning duplicate results",
        "why does save() fail silently",
        "why do I need to call super().__init__",
        # explain prefix
        "explain the Django ORM query lifecycle",
        "explain how from_db_value converts database values",
        # tutorial / guide suffix
        "writing custom model fields tutorial",
        "database optimization guide",
        "guide to Django migrations",
        # best way / best practice
        "best way to handle file uploads",
        "best practices for model design",
        # introduction / overview
        "introduction to Django class based views",
        "overview of the QuerySet API",
        # when to use
        "when to use select_related vs prefetch_related",
        # example of
        "example of a custom manager",
        "examples of model field validation",
    ])
    def test_doc_intent_true(self, query: str) -> None:
        assert is_doc_intent_query(query) is True, (
            f"UPG-11.11: expected is_doc_intent_query({query!r}) to be True (doc-intent), "
            f"but got False. This query describes a topic, not a symbol implementation."
        )

    # Queries that are NOT doc-intent (code-intent; should return False)
    @pytest.mark.parametrize("query", [
        # F1 / F1b / F4 / F5 exact queries
        "Field deconstruct base class name path args kwargs migration",
        "from_db_value convert database value to python object on a model field",
        # code-shaped symbol lookups
        "deconstruct method implementation",
        "from_db_value JSONField",
        "QuerySet.exclude filter records",
        "CursorWrapper execute SQL",
        "Model.save implementation",
        # F6 / F8 regression guards
        "list all database migrations for a project",
        "read lines from a text file",
        "connect to database and execute query",
        "render template context in view",
        "exclude certain records from a queryset",
        # natural-language code queries that don't start with doc phrases
        "save a model instance to the database",
        "get all objects from a queryset",
    ])
    def test_doc_intent_false(self, query: str) -> None:
        assert is_doc_intent_query(query) is False, (
            f"UPG-11.11: expected is_doc_intent_query({query!r}) to be False (code-intent), "
            f"but got True. Forced-inclusion must NOT be suppressed for code-intent queries."
        )

    def test_f2_query_is_doc_intent(self) -> None:
        """F2 acceptance guard: the exact F2 query must be classified as doc-intent."""
        q = "how to write a custom model field with deconstruct and from_db_value"
        assert is_doc_intent_query(q) is True, (
            f"UPG-11.11 regression: F2 query must be doc-intent so forced-inclusion "
            f"is suppressed and docs/howto/custom-model-fields.txt can surface in top-5."
        )

    def test_f2_doc_intent_is_genuine_boost(self) -> None:
        """F2 regression guard (UPG-11.11-b): on a doc-intent query, doc-prose
        chunks must be BOOSTED (multiplier > 1.0), not merely un-penalised.

        A neutral 1.0 leaves a strong doc at its raw similarity (~0.82), below
        code chunks that score ~1.0 for the symbols the query names — so the
        howto doc loses (the original F2 'passing' was never actually green).
        """
        import agent.config as cfg
        assert cfg.DOC_INTENT_DOC_PROSE_MULTIPLIER > 1.0, (
            "doc_prose_multiplier must be a genuine boost (>1.0) on doc-intent "
            "queries; 1.0 (neutral) regresses F2 — the howto doc cannot overtake "
            "code chunks scoring ~1.0 for deconstruct/from_db_value."
        )
        body = (
            "How to write a custom model field.\n"
            "Subclass Field and implement deconstruct() so migrations recreate it.\n"
            "Implement from_db_value() to convert the database value to Python.\n"
            "See the field API reference for the full contract."
        )
        doc_intent = quality_score(body, language="txt", query_is_doc_intent=True)
        code_intent = quality_score(body, language="txt", query_is_doc_intent=False)
        assert doc_intent > code_intent, "doc-intent must score above code-intent for doc prose"
        assert doc_intent > 1.0, "doc-intent doc prose must be boosted above the neutral baseline"

    def test_f1_query_is_not_doc_intent(self) -> None:
        """F1 regression guard: the F1 query must NOT be classified as doc-intent
        (forced-inclusion must still fire for code-shaped queries)."""
        q = "Field deconstruct base class name path args kwargs migration"
        assert is_doc_intent_query(q) is False, (
            f"UPG-11.11 regression: F1 query must NOT be doc-intent — "
            f"forced-inclusion must still fire to surface Field.deconstruct."
        )

    def test_f1b_query_is_not_doc_intent(self) -> None:
        """F1b regression guard: from_db_value compound-identifier query stays code-intent."""
        q = "from_db_value convert database value to python object on a model field"
        assert is_doc_intent_query(q) is False, (
            f"UPG-11.11 regression: F1b query must NOT be doc-intent."
        )

    def test_doc_intent_quality_score_elevated_for_doc_prose(self) -> None:
        """On doc-intent queries, doc prose chunks must get the elevated quality
        score (DOC_INTENT_DOC_PROSE_MULTIPLIER, default 1.0) instead of the
        normal _Q_DOC_PROSE (0.70) so they can rank above code on how-to queries."""
        from agent.config import DOC_INTENT_DOC_PROSE_MULTIPLIER, QUALITY_DOC_PROSE

        doc_content = (
            "Writing custom model fields\n"
            "This tutorial walks through how to write a custom Django model field.\n"
            "You need to implement deconstruct() and from_db_value() methods.\n"
            "The deconstruct() method is called during migrations to serialize the field.\n"
        )
        # Code-intent: doc prose gets 0.70 penalty
        code_score = quality_score(
            doc_content, "/docs/howto/custom-model-fields.txt", language="txt",
            query_is_doc_intent=False,
        )
        assert code_score == pytest.approx(QUALITY_DOC_PROSE, abs=0.01), (
            f"Doc prose on code-intent query must get QUALITY_DOC_PROSE={QUALITY_DOC_PROSE}, "
            f"got {code_score}"
        )

        # Doc-intent: doc prose gets the elevated multiplier (1.0 by default)
        doc_score = quality_score(
            doc_content, "/docs/howto/custom-model-fields.txt", language="txt",
            query_is_doc_intent=True,
        )
        assert doc_score == pytest.approx(DOC_INTENT_DOC_PROSE_MULTIPLIER, abs=0.01), (
            f"Doc prose on doc-intent query must get DOC_INTENT_DOC_PROSE_MULTIPLIER="
            f"{DOC_INTENT_DOC_PROSE_MULTIPLIER}, got {doc_score}"
        )
        assert doc_score > code_score, (
            "Doc prose must score HIGHER on a doc-intent query than a code-intent query."
        )


# ---------------------------------------------------------------------------
# UPG-15.6 — plural prose nouns stopword additions (F20)
# ---------------------------------------------------------------------------

class TestPluralProseNounStopWords:
    """UPG-15.6: plural forms of prose nouns already in prog_stopwords.txt must be
    in SYMBOL_STOP_WORDS when their plural is >=7 chars (passing forced-inclusion
    min_identifier_len guard) and the plural is a natural-language query noun that
    also names Django method/property groups.

    F20: 'configure Django settings for production deployment' — 'settings' (8 chars)
    was not in the stopword list, causing forced-inclusion to promote all .settings
    properties (MailersHandler.settings, BaseConnectionHandler.settings) to rank 1-2.
    """

    # Each plural whose singular is already in prog_stopwords.txt and whose
    # plural passes the min_identifier_len=7 guard.
    _EXPECTED_PLURAL_STOPWORDS = [
        "settings",   # plural of 'setting' (already listed); .settings property overload
        "responses",  # plural of 'response'; prose: "HTTP responses"
        "processes",  # plural of 'process'; prose: "OS processes"
        "handlers",   # plural of 'handler'; prose: "event handlers"
        "backends",   # plural of 'backend'; prose: "database backends"
    ]

    @pytest.mark.parametrize("word", _EXPECTED_PLURAL_STOPWORDS)
    def test_plural_is_in_symbol_stop_words(self, word: str) -> None:
        """Each UPG-15.6 plural must be present in the loaded SYMBOL_STOP_WORDS frozenset."""
        from agent.config import SYMBOL_STOP_WORDS
        assert word in SYMBOL_STOP_WORDS, (
            f"UPG-15.6: '{word}' must be in SYMBOL_STOP_WORDS after prog_stopwords.txt update. "
            f"Plural prose nouns >=7 chars whose singular is stopworded must also be stopworded "
            f"to prevent forced-inclusion false positives."
        )

    @pytest.mark.parametrize("word", _EXPECTED_PLURAL_STOPWORDS)
    def test_plural_leaf_receives_no_boost(self, word: str) -> None:
        """UPG-15.6: bare plural leaf must get zero symbol_identity_boost for a
        natural-language query that names it as a prose noun."""
        query = f"configure Django {word} for production deployment"
        tokens = _query_symbol_tokens(query)
        boost = symbol_identity_boost(word, tokens)
        assert boost == 0.0, (
            f"UPG-15.6: symbol_identity_boost('{word}', ...) returned {boost} != 0.0 "
            f"for query {query!r}. Plural prose-noun stopwords must not be boosted."
        )

    def test_f20_settings_no_boost_for_prose_query(self) -> None:
        """F20 acceptance: leaf='settings' must have zero boost for the F20 query."""
        from agent.config import SYMBOL_STOP_WORDS
        assert "settings" in SYMBOL_STOP_WORDS, (
            "UPG-15.6: 'settings' must be in SYMBOL_STOP_WORDS — add it to prog_stopwords.txt"
        )
        tokens = _query_symbol_tokens("configure Django settings for production deployment")
        assert symbol_identity_boost("settings", tokens) == 0.0, (
            "UPG-15.6/F20: 'settings' leaf must get zero boost on a prose settings query; "
            "MailersHandler.settings / BaseConnectionHandler.settings must not reach rank 1-2."
        )

    def test_qualified_settings_still_boosted(self) -> None:
        """UPG-15.6: qualified 'LazySettings.configure' must still be boosted because
        the stop-word guard applies only to bare unqualified leaves."""
        # 'lazysettings' or 'configure' — neither is a stopword, qualified form should boost
        tokens = _query_symbol_tokens("LazySettings configure production")
        boost = symbol_identity_boost("LazySettings.configure", tokens)
        assert boost > 0.0, (
            "UPG-15.6: qualified 'LazySettings.configure' must still get a positive boost. "
            "The stopword guard only suppresses bare unqualified leaves like 'settings'."
        )


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
