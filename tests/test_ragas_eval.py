"""
T31 — RAGAS retrieval quality evaluation for vectr_search.

Run with: pytest -m ragas tests/test_ragas_eval.py

Requires:
  pip install vectr[ragas]     # ragas + LLM API key (OPENAI_API_KEY or ANTHROPIC_API_KEY)

ragas is NEVER imported at module level. The import happens only inside the
@pytest.mark.ragas-tagged test methods so that the regular test suite (which
excludes the ragas mark) never triggers nest_asyncio.apply() and doesn't
interfere with anyio-backed tests.

The TestDeterministicPrecisionRecall suite always runs and needs no ragas.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Minimal eval dataset
# ---------------------------------------------------------------------------

@dataclass
class EvalSample:
    question: str
    ground_truth: str       # substring expected in a relevant chunk
    expected_category: str  # informational label


def _make_eval_samples() -> list[EvalSample]:
    """Small in-repo eval dataset that doesn't require a large codebase."""
    return [
        EvalSample(
            question="How does vectr_search retrieve results?",
            ground_truth="search",
            expected_category="symbol_definition",
        ),
        EvalSample(
            question="What is the EvictionAdvisor used for?",
            ground_truth="evict",
            expected_category="symbol_callers",
        ),
        EvalSample(
            question="How are notes stored in WorkingContextStore?",
            ground_truth="remember",
            expected_category="cross_session",
        ),
        EvalSample(
            question="What does locate_l2 do when exact match fails?",
            ground_truth="resolution_strategy",
            expected_category="symbol_definition",
        ),
        EvalSample(
            question="How is the MCP turn-count trigger implemented?",
            ground_truth="_session_calls_since_save",
            expected_category="symbol_definition",
        ),
    ]


# ---------------------------------------------------------------------------
# Retrieval harness — real VectrService indexed against vectr source tree
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vectr_service(tmp_path_factory):
    """
    Real VectrService indexed against the vectr source tree with a dummy embedder.
    Uses the same _DummyEmbedProvider pattern as the integration fixtures so no
    model download is required. BM25 lexical search is sufficient for substring-
    based deterministic recall; semantic ranking uses deterministic dummy vectors.
    """
    from unittest.mock import patch
    from tests.conftest import _DummyEmbedProvider, _RealVectrService

    workspace = str(Path(__file__).parent.parent)
    db_dir = str(tmp_path_factory.mktemp("ragas_eval_db"))

    with patch("agent.indexer.get_embed_provider", return_value=_DummyEmbedProvider()), \
         patch("agent.indexer.CodeIndexer._load_mtime_cache", return_value={}), \
         patch.dict("os.environ", {"VECTR_DB_DIR": db_dir, "VECTR_EMBED_MODEL": "dummy"}):
        svc = _RealVectrService(workspace_root=workspace)
        svc.index(workspace)
        yield svc


def _retrieve_contexts(service, question: str, n: int = 5) -> list[str]:
    """Return top-n chunk content strings from vectr_search."""
    results, _ = service.search(question, n_results=n)
    return [r.content for r in results if hasattr(r, "content")]


# ---------------------------------------------------------------------------
# Deterministic precision/recall — no ragas, always runs in standard CI
# ---------------------------------------------------------------------------

class TestDeterministicPrecisionRecall:
    """
    Lightweight retrieval quality gate that runs in regular CI without ragas.

    deterministic_precision = fraction of retrieved chunks containing ground_truth
    deterministic_recall    = 1 if any retrieved chunk contains ground_truth else 0
    """

    @pytest.fixture(autouse=True)
    def _svc(self, vectr_service):
        self.svc = vectr_service

    def _evaluate(self, sample: EvalSample, n: int = 5) -> tuple[float, int]:
        contexts = _retrieve_contexts(self.svc, sample.question, n=n)
        if not contexts:
            return 0.0, 0
        hits = sum(1 for c in contexts if sample.ground_truth.lower() in c.lower())
        return hits / len(contexts), 1 if hits > 0 else 0

    def test_search_retrieval_precision_above_zero(self):
        """At least one sample must have non-zero deterministic precision."""
        assert any(
            self._evaluate(s)[0] > 0 for s in _make_eval_samples()
        ), "vectr_search returned 0 relevant chunks for ALL eval samples"

    def test_mean_recall_above_threshold(self):
        """Mean recall across all samples must be ≥ 0.4."""
        samples = _make_eval_samples()
        recalls = [self._evaluate(s)[1] for s in samples]
        mean_recall = sum(recalls) / len(recalls)
        assert mean_recall >= 0.4, (
            f"Mean deterministic recall {mean_recall:.2f} < 0.40 — "
            f"per-sample recalls: {recalls}"
        )

    def test_per_sample_results_logged(self):
        """Print a precision/recall table — reporting test, always passes."""
        print("\n--- Vectr retrieval quality (deterministic) ---")
        print(f"{'Category':<20} {'Question':<45} {'P':>5} {'R':>5}")
        print("-" * 80)
        for s in _make_eval_samples():
            p, r = self._evaluate(s)
            short_q = s.question[:43] + ".." if len(s.question) > 45 else s.question
            print(f"{s.expected_category:<20} {short_q:<45} {p:>5.2f} {r:>5}")


# ---------------------------------------------------------------------------
# RAGAS context_precision / context_recall — requires ragas + LLM API key
#
# ragas is imported INSIDE each test method (never at module level) to avoid
# the nest_asyncio.apply() call in ragas/executor.py from polluting the event
# loop used by anyio-backed tests in the same pytest session.
# ---------------------------------------------------------------------------

@pytest.mark.ragas
class TestRagasContextMetrics:
    """
    Full RAGAS evaluation using context_precision and context_recall.

    Requires: pip install vectr[ragas] + OPENAI_API_KEY or ANTHROPIC_API_KEY.
    """

    @pytest.fixture(autouse=True)
    def _svc(self, vectr_service):
        self.svc = vectr_service

    def _build_ragas_dataset(self, samples: list[EvalSample], n: int = 5):
        datasets = pytest.importorskip("datasets")
        rows = []
        for s in samples:
            contexts = _retrieve_contexts(self.svc, s.question, n=n)
            rows.append({
                "question": s.question,
                "contexts": contexts,
                "ground_truth": s.ground_truth,
                "answer": contexts[0] if contexts else "",
            })
        return datasets.Dataset.from_list(rows)

    def test_context_precision(self):
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
            pytest.skip("No LLM API key configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")
        ragas = pytest.importorskip("ragas")
        from ragas.metrics import context_precision  # noqa: PLC0415
        dataset = self._build_ragas_dataset(_make_eval_samples())
        result = ragas.evaluate(dataset, metrics=[context_precision])
        score = result["context_precision"]
        assert score >= 0.3, (
            f"RAGAS context_precision {score:.3f} < 0.30 — "
            "retrieved chunks are not sufficiently relevant to the questions"
        )

    def test_context_recall(self):
        if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
            pytest.skip("No LLM API key configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")
        ragas = pytest.importorskip("ragas")
        from ragas.metrics import context_recall  # noqa: PLC0415
        dataset = self._build_ragas_dataset(_make_eval_samples())
        result = ragas.evaluate(dataset, metrics=[context_recall])
        score = result["context_recall"]
        assert score >= 0.3, (
            f"RAGAS context_recall {score:.3f} < 0.30 — "
            "ground-truth information is not present in enough retrieved chunks"
        )
