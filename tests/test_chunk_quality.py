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
