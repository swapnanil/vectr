"""
Tests for strategy_selector — fingerprint() and select_strategy().

fingerprint() reads real files to detect frameworks, languages, and doc
coverage; these tests create minimal tmp_path fixtures rather than loading
the full Django source tree.

select_strategy() is pure logic over a CodebaseFingerprint dataclass,
so it can be tested without any filesystem access.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from agent.strategy_selector import (
    CodebaseFingerprint,
    RetrievalStrategy,
    fingerprint,
    select_strategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fp(**kwargs) -> CodebaseFingerprint:
    """Build a CodebaseFingerprint with sensible defaults, override via kwargs."""
    defaults = dict(
        total_files=50,
        language_dist={"python": 50},
        dominant_language="python",
        is_monorepo=False,
        size_class="medium",
        doc_coverage_ratio=0.4,
        detected_frameworks=[],
        domain_terms=[],
        complexity_class="moderate",
        has_grpc=False,
        is_legacy=False,
    )
    defaults.update(kwargs)
    return CodebaseFingerprint(**defaults)


# ---------------------------------------------------------------------------
# CodebaseFingerprint — construction
# ---------------------------------------------------------------------------

class TestCodebaseFingerprint:
    def test_fields_accessible(self) -> None:
        fp = _fp()
        assert fp.total_files == 50
        assert fp.dominant_language == "python"
        assert fp.size_class == "medium"
        assert fp.has_grpc is False
        assert fp.is_legacy is False

    def test_language_dist_stores_counts(self) -> None:
        fp = _fp(language_dist={"python": 30, "javascript": 20})
        assert fp.language_dist["python"] == 30
        assert fp.language_dist["javascript"] == 20


# ---------------------------------------------------------------------------
# RetrievalStrategy — validation
# ---------------------------------------------------------------------------

class TestRetrievalStrategy:
    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="must equal 1.0"):
            RetrievalStrategy(
                semantic_weight=0.6, bm25_weight=0.6,
                graph_first=False,
                recommended_embed_model="BAAI/bge-base-en-v1.5",
                rationale="bad",
            )

    def test_valid_weights_accepted(self) -> None:
        s = RetrievalStrategy(
            semantic_weight=0.70, bm25_weight=0.30,
            graph_first=False,
            recommended_embed_model="BAAI/bge-base-en-v1.5",
            rationale="ok",
        )
        assert abs(s.semantic_weight + s.bm25_weight - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# select_strategy — pure logic
# ---------------------------------------------------------------------------

class TestSelectStrategy:
    def test_small_codebase_bm25_higher(self) -> None:
        s = select_strategy(_fp(size_class="small", dominant_language=None))
        assert s.bm25_weight >= 0.40
        assert s.semantic_weight <= 0.60

    def test_large_codebase_semantic_higher(self) -> None:
        s = select_strategy(_fp(size_class="large", dominant_language=None))
        assert s.semantic_weight >= 0.70

    def test_medium_codebase_balanced(self) -> None:
        s = select_strategy(_fp(size_class="medium", dominant_language=None))
        assert 0.60 <= s.semantic_weight <= 0.80

    def test_static_language_enables_graph_first(self) -> None:
        for lang in ("go", "java", "rust"):
            s = select_strategy(_fp(dominant_language=lang))
            assert s.graph_first is True, f"Expected graph_first for {lang}"

    def test_dynamic_language_boosts_semantic(self) -> None:
        baseline = select_strategy(_fp(dominant_language=None, size_class="medium"))
        for lang in ("python", "javascript", "typescript"):
            s = select_strategy(_fp(dominant_language=lang, size_class="medium"))
            assert s.semantic_weight >= baseline.semantic_weight

    def test_monorepo_enables_graph_first(self) -> None:
        s = select_strategy(_fp(is_monorepo=True))
        assert s.graph_first is True

    def test_grpc_boosts_bm25_and_graph_first(self) -> None:
        without = select_strategy(_fp(has_grpc=False))
        with_grpc = select_strategy(_fp(has_grpc=True))
        assert with_grpc.bm25_weight > without.bm25_weight
        assert with_grpc.graph_first is True

    def test_legacy_java_boosts_bm25(self) -> None:
        s = select_strategy(_fp(
            is_legacy=True,
            dominant_language="java",
            size_class="large",
            doc_coverage_ratio=0.05,
        ))
        assert s.bm25_weight >= 0.40
        assert s.graph_first is True

    def test_high_doc_coverage_boosts_semantic(self) -> None:
        low = select_strategy(_fp(doc_coverage_ratio=0.30, is_legacy=False))
        high = select_strategy(_fp(doc_coverage_ratio=0.80, is_legacy=False))
        assert high.semantic_weight >= low.semantic_weight

    def test_complex_codebase_enables_graph_first(self) -> None:
        s = select_strategy(_fp(complexity_class="complex"))
        assert s.graph_first is True

    def test_simple_codebase_no_forced_graph(self) -> None:
        s = select_strategy(_fp(
            complexity_class="simple",
            dominant_language="python",
            is_monorepo=False,
            has_grpc=False,
            is_legacy=False,
        ))
        # graph_first not forced by complexity; may still be True from other factors
        # but dominant_language=python + no monorepo/grpc/legacy → should be False
        assert s.graph_first is False

    def test_code_heavy_recommends_voyage(self) -> None:
        for lang in ("go", "java", "rust"):
            s = select_strategy(_fp(dominant_language=lang))
            assert "voyage" in s.recommended_embed_model.lower(), f"Expected voyage for {lang}"

    def test_python_recommends_bge(self) -> None:
        s = select_strategy(_fp(dominant_language="python", size_class="small"))
        assert "bge" in s.recommended_embed_model.lower()

    def test_large_codebase_recommends_voyage(self) -> None:
        s = select_strategy(_fp(size_class="large", dominant_language=None))
        assert "voyage" in s.recommended_embed_model.lower()

    def test_rationale_non_empty(self) -> None:
        s = select_strategy(_fp())
        assert len(s.rationale) > 0

    def test_weights_always_sum_to_one(self) -> None:
        configs = [
            _fp(size_class="small"),
            _fp(size_class="medium"),
            _fp(size_class="large"),
            _fp(dominant_language="go"),
            _fp(is_monorepo=True),
            _fp(has_grpc=True),
            _fp(is_legacy=True, dominant_language="java", size_class="large", doc_coverage_ratio=0.05),
            _fp(doc_coverage_ratio=0.90),
            _fp(complexity_class="complex"),
        ]
        for fp in configs:
            s = select_strategy(fp)
            total = round(s.semantic_weight + s.bm25_weight, 6)
            assert total == 1.0, f"Weights {s.semantic_weight}+{s.bm25_weight}={total} != 1.0"


# ---------------------------------------------------------------------------
# fingerprint() — filesystem integration
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_empty_workspace_returns_zero_files(self, tmp_path) -> None:
        fp = fingerprint(str(tmp_path), [])
        assert fp.total_files == 0
        assert fp.dominant_language is None
        assert fp.size_class == "small"

    def test_python_files_detected_as_dominant(self, tmp_path) -> None:
        files = []
        for i in range(5):
            f = tmp_path / f"module_{i}.py"
            f.write_text(f"def fn_{i}(): pass\n")
            files.append(str(f))
        fp = fingerprint(str(tmp_path), files)
        assert fp.dominant_language == "python"
        assert fp.language_dist.get("python") == 5

    def test_mixed_language_finds_dominant(self, tmp_path) -> None:
        py_files = []
        for i in range(6):
            f = tmp_path / f"m{i}.py"
            f.write_text("def f(): pass\n")
            py_files.append(str(f))
        js_file = tmp_path / "app.js"
        js_file.write_text("function f() {}\n")
        files = py_files + [str(js_file)]
        fp = fingerprint(str(tmp_path), files)
        assert fp.dominant_language == "python"

    def test_size_class_small(self, tmp_path) -> None:
        files = [str(tmp_path / f"f{i}.py") for i in range(10)]
        for f in files:
            Path(f).write_text("pass\n")
        fp = fingerprint(str(tmp_path), files)
        assert fp.size_class == "small"

    def test_size_class_large(self, tmp_path) -> None:
        files = [str(tmp_path / f"f{i}.py") for i in range(1000)]
        for f in files:
            Path(f).write_text("pass\n")
        fp = fingerprint(str(tmp_path), files)
        assert fp.size_class == "large"

    def test_django_framework_detected(self, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("django==4.2\ncelery==5.3\n")
        fp = fingerprint(str(tmp_path), [])
        assert "django" in fp.detected_frameworks

    def test_fastapi_framework_detected(self, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn\n")
        fp = fingerprint(str(tmp_path), [])
        assert "fastapi" in fp.detected_frameworks

    def test_grpc_detected_from_proto_file(self, tmp_path) -> None:
        proto = tmp_path / "service.proto"
        proto.write_text('syntax = "proto3";\nservice Greeter { rpc SayHello (HelloRequest) returns (HelloReply); }\n')
        fp = fingerprint(str(tmp_path), [])
        assert fp.has_grpc is True
        assert "grpc" in fp.detected_frameworks

    def test_doc_coverage_ratio_zero_for_no_docs(self, tmp_path) -> None:
        files = []
        for i in range(3):
            f = tmp_path / f"nodoc_{i}.py"
            f.write_text(f"def fn_{i}(): x = 1\n")
            files.append(str(f))
        fp = fingerprint(str(tmp_path), files)
        assert fp.doc_coverage_ratio == 0.0

    def test_doc_coverage_ratio_for_commented_files(self, tmp_path) -> None:
        files = []
        for i in range(4):
            f = tmp_path / f"doc_{i}.py"
            f.write_text(f'"""Module {i}."""\ndef fn(): pass\n')
            files.append(str(f))
        fp = fingerprint(str(tmp_path), files)
        assert fp.doc_coverage_ratio == 1.0

    def test_domain_terms_extracted_from_readme(self, tmp_path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Auction Platform\n\n"
            "Auction handles Bidding. Bidding occurs in Segments. "
            "Segments contain AuctionItems. Bidding triggers Auction results. "
            "Auction, Bidding, Segments — these are core to AuctionPlatform.\n"
        )
        fp = fingerprint(str(tmp_path), [])
        # "Auction" and "Bidding" appear ≥3 times each
        assert any(t in ("Auction", "Bidding", "Segments") for t in fp.domain_terms)

    def test_no_readme_domain_terms_empty(self, tmp_path) -> None:
        fp = fingerprint(str(tmp_path), [])
        assert fp.domain_terms == []

    def test_monorepo_detected_from_go_work(self, tmp_path) -> None:
        (tmp_path / "go.work").write_text("go 1.21\n")
        fp = fingerprint(str(tmp_path), [])
        assert fp.is_monorepo is True

    def test_monorepo_detected_from_subdirectory_packages(self, tmp_path) -> None:
        for pkg in ("serviceA", "serviceB", "serviceC"):
            d = tmp_path / pkg
            d.mkdir()
            (d / "setup.py").write_text("from setuptools import setup; setup(name='x')\n")
        fp = fingerprint(str(tmp_path), [])
        assert fp.is_monorepo is True

    def test_non_monorepo_single_package(self, tmp_path) -> None:
        (tmp_path / "setup.py").write_text("from setuptools import setup; setup(name='x')\n")
        fp = fingerprint(str(tmp_path), [])
        assert fp.is_monorepo is False

    def test_complexity_simple_for_tiny_files(self, tmp_path) -> None:
        files = []
        for i in range(5):
            f = tmp_path / f"tiny_{i}.py"
            f.write_text("x = 1\n")
            files.append(str(f))
        fp = fingerprint(str(tmp_path), files)
        assert fp.complexity_class == "simple"

    def test_complexity_complex_for_large_files(self, tmp_path) -> None:
        files = []
        for i in range(3):
            f = tmp_path / f"big_{i}.py"
            # 500 lines each → avg > 400
            f.write_text("\n".join(f"x_{j} = {j}" for j in range(500)))
            files.append(str(f))
        fp = fingerprint(str(tmp_path), files)
        assert fp.complexity_class == "complex"

    def test_legacy_heuristic_java_low_doc_large(self, tmp_path) -> None:
        files = []
        for i in range(50):
            f = tmp_path / f"Class{i}.java"
            f.write_text(f"public class Class{i} {{}}\n")  # no javadoc
            files.append(str(f))
        # Simulate "large" by passing 1000 hypothetical paths (only 50 exist for coverage)
        fake_files = files + [str(tmp_path / f"Fake{i}.java") for i in range(950)]
        fp = fingerprint(str(tmp_path), fake_files)
        assert fp.dominant_language == "java"
        assert fp.size_class == "large"
        assert fp.doc_coverage_ratio < 0.15
        assert fp.is_legacy is True
