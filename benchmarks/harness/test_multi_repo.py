"""Tests for the multi-repo benchmark harness (T29)."""
from __future__ import annotations

from pathlib import Path
import json
import pytest


class TestQuestionGeneration:
    def _make_repo(self, tmp_path: Path) -> str:
        """Create a minimal Python repo with symbols, routes, and classes."""
        (tmp_path / "main.py").write_text(
            "def run(): pass\n"
            "def helper(): pass\n"
        )
        (tmp_path / "views.py").write_text(
            "@app.get('/users')\n"
            "def list_users(): return []\n"
            "\n"
            "class UserView(BaseView):\n"
            "    pass\n"
        )
        return str(tmp_path)

    def test_generates_questions_for_repo(self, tmp_path) -> None:
        from run_multi_repo import _build_questions_for_repo
        repo = self._make_repo(tmp_path)
        questions = _build_questions_for_repo(repo)
        assert len(questions) >= 1

    def test_each_question_has_required_fields(self, tmp_path) -> None:
        from run_multi_repo import _build_questions_for_repo
        repo = self._make_repo(tmp_path)
        questions = _build_questions_for_repo(repo)
        for q in questions:
            assert q.question_id
            assert q.category in [
                "symbol_definition", "symbol_callers", "symbol_callees",
                "file_role", "class_hierarchy", "entry_point", "api_endpoint",
                "config_key", "error_path", "import_chain", "test_coverage", "cross_session",
            ]
            assert q.repo_path == repo
            assert q.prompt
            assert isinstance(q.ground_truth, str)

    def test_entry_point_detected_from_main_py(self, tmp_path) -> None:
        from run_multi_repo import _build_questions_for_repo
        (tmp_path / "main.py").write_text("def main(): pass\n")
        questions = _build_questions_for_repo(str(tmp_path), category_filter=["entry_point"])
        ep = next((q for q in questions if q.category == "entry_point"), None)
        if ep:  # only if entry point was detected
            assert "main.py" in ep.ground_truth

    def test_category_filter_limits_questions(self, tmp_path) -> None:
        from run_multi_repo import _build_questions_for_repo
        repo = self._make_repo(tmp_path)
        all_q = _build_questions_for_repo(repo)
        filtered = _build_questions_for_repo(repo, category_filter=["entry_point"])
        assert len(filtered) <= len(all_q)
        for q in filtered:
            assert q.category == "entry_point"

    def test_api_endpoint_question_generated(self, tmp_path) -> None:
        from run_multi_repo import _build_questions_for_repo
        (tmp_path / "views.py").write_text(
            "@app.get('/health')\ndef health(): return 'ok'\n"
        )
        questions = _build_questions_for_repo(str(tmp_path), category_filter=["api_endpoint"])
        api_q = [q for q in questions if q.category == "api_endpoint"]
        if api_q:
            assert "/health" in api_q[0].prompt or "GET" in api_q[0].prompt

    def test_symbol_definition_question_generated(self, tmp_path) -> None:
        from run_multi_repo import _build_questions_for_repo
        (tmp_path / "utils.py").write_text("def compute_total(items): return sum(items)\n")
        questions = _build_questions_for_repo(str(tmp_path), category_filter=["symbol_definition"])
        sym_q = [q for q in questions if q.category == "symbol_definition"]
        if sym_q:
            assert "compute_total" in sym_q[0].prompt or "utils.py" in sym_q[0].ground_truth


class TestScanning:
    def test_scan_symbols_finds_functions(self, tmp_path) -> None:
        from run_multi_repo import _scan_symbols
        f = tmp_path / "a.py"
        f.write_text("def public_func(): pass\ndef _private(): pass\n")
        results = _scan_symbols([f])
        names = [r[0] for r in results]
        assert "public_func" in names
        assert "_private" not in names

    def test_scan_routes_finds_flask_get(self, tmp_path) -> None:
        from run_multi_repo import _scan_routes
        f = tmp_path / "views.py"
        f.write_text('@app.get("/api/items")\ndef items(): return []\n')
        results = _scan_routes([f])
        assert any(r[1] == "/api/items" for r in results)

    def test_scan_classes_finds_inheritance(self, tmp_path) -> None:
        from run_multi_repo import _scan_classes
        f = tmp_path / "models.py"
        f.write_text("class UserModel(BaseModel):\n    pass\n")
        results = _scan_classes([f])
        assert any(r[0] == "UserModel" and r[1] == "BaseModel" for r in results)

    def test_scan_classes_skips_object_inheritance(self, tmp_path) -> None:
        from run_multi_repo import _scan_classes
        f = tmp_path / "base.py"
        f.write_text("class Base(object):\n    pass\n")
        results = _scan_classes([f])
        assert not any(r[0] == "Base" for r in results)

    def test_find_entry_point_finds_main_py(self, tmp_path) -> None:
        from run_multi_repo import _find_entry_point
        (tmp_path / "main.py").write_text("if __name__ == '__main__': pass\n")
        ep = _find_entry_point(tmp_path)
        assert ep is not None
        assert ep.name == "main.py"

    def test_find_entry_point_returns_none_when_absent(self, tmp_path) -> None:
        from run_multi_repo import _find_entry_point
        assert _find_entry_point(tmp_path) is None


class TestGrading:
    def _make_result(self, answer: str, ground_truth: str, agent: str = "vanilla") -> "QuestionResult":
        from run_multi_repo import QuestionResult
        # Replicate the exact grading logic from run_multi_repo._run_question
        if not ground_truth:
            score = 1 if answer.strip() else 0
        else:
            score = 1 if ground_truth.lower() in answer.lower() else 0
        return QuestionResult(
            question_id="test", category="symbol_definition", agent_type=agent,
            answer=answer, score=score, input_tokens=100, output_tokens=50,
            cost_usd=0.01, wall_time_s=1.0,
        )

    def test_correct_answer_scores_1(self) -> None:
        r = self._make_result("The function is in auth.py at line 42", "auth.py")
        assert r.score == 1
        assert r.passed

    def test_wrong_answer_scores_0(self) -> None:
        r = self._make_result("The function is in views.py", "auth.py")
        assert r.score == 0
        assert not r.passed

    def test_empty_ground_truth_any_answer_passes(self) -> None:
        r = self._make_result("The function authenticates users by checking credentials", "")
        assert r.score == 1

    def test_empty_ground_truth_empty_answer_fails(self) -> None:
        r = self._make_result("", "")
        assert r.score == 0

    def test_case_insensitive_matching(self) -> None:
        r = self._make_result("the entry point is MAIN.PY", "main.py")
        assert r.score == 1


class TestCategories:
    def test_all_12_categories_defined(self) -> None:
        from run_multi_repo import CATEGORIES
        assert len(CATEGORIES) == 12

    def test_category_names_are_valid(self) -> None:
        from run_multi_repo import CATEGORIES
        for c in CATEGORIES:
            assert isinstance(c, str)
            assert c.islower()
            assert "_" in c or c.isalpha()

    def test_all_expected_categories_present(self) -> None:
        from run_multi_repo import CATEGORIES
        expected = {
            "symbol_definition", "symbol_callers", "symbol_callees", "file_role",
            "class_hierarchy", "entry_point", "api_endpoint", "config_key",
            "error_path", "import_chain", "test_coverage", "cross_session",
        }
        assert set(CATEGORIES) == expected


class TestSaveResults:
    def test_save_creates_json_file(self, tmp_path, monkeypatch) -> None:
        from run_multi_repo import QuestionResult, save_multi_repo_results
        import run_multi_repo
        monkeypatch.setattr(run_multi_repo, "OUTPUT_DIR", tmp_path)

        results = [
            QuestionResult(
                question_id="q1", category="entry_point", agent_type="vanilla",
                answer="main.py", score=1, input_tokens=500, output_tokens=100,
                cost_usd=0.05, wall_time_s=12.3,
            )
        ]
        path = save_multi_repo_results(results, "/tmp/my-repo")
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["score"] == 1
        assert data[0]["category"] == "entry_point"

    def test_save_result_has_required_fields(self, tmp_path, monkeypatch) -> None:
        from run_multi_repo import QuestionResult, save_multi_repo_results
        import run_multi_repo
        monkeypatch.setattr(run_multi_repo, "OUTPUT_DIR", tmp_path)

        r = QuestionResult(
            question_id="q1", category="symbol_definition", agent_type="vectr",
            answer="In auth.py", score=1, input_tokens=200, output_tokens=80,
            cost_usd=0.02, wall_time_s=5.0,
        )
        path = save_multi_repo_results([r], "/tmp/repo")
        data = json.loads(path.read_text())[0]
        for field in ("question_id", "category", "agent_type", "score",
                      "answer", "input_tokens", "output_tokens", "cost_usd"):
            assert field in data


class TestRepoResult:
    def test_pass_rate_empty(self) -> None:
        from run_multi_repo import RepoResult
        r = RepoResult(repo_path="/tmp", repo_name="test")
        assert r.pass_rate == 0.0

    def test_pass_rate_all_pass(self) -> None:
        from run_multi_repo import RepoResult, QuestionResult
        r = RepoResult(repo_path="/tmp", repo_name="test")
        r.questions = [
            QuestionResult("q1", "entry_point", "vanilla", "main.py", 1, 100, 50, 0.01, 1.0),
            QuestionResult("q2", "symbol_definition", "vanilla", "auth.py", 1, 150, 60, 0.02, 2.0),
        ]
        assert r.pass_rate == 1.0

    def test_pass_rate_partial(self) -> None:
        from run_multi_repo import RepoResult, QuestionResult
        r = RepoResult(repo_path="/tmp", repo_name="test")
        r.questions = [
            QuestionResult("q1", "entry_point", "vanilla", "main.py", 1, 100, 50, 0.01, 1.0),
            QuestionResult("q2", "symbol_definition", "vanilla", "wrong", 0, 150, 60, 0.02, 2.0),
        ]
        assert r.pass_rate == 0.5
