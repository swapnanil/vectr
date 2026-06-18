"""Unit tests for execution scoring (no LLM; one real subprocess pytest run)."""
from __future__ import annotations

import sys

from scoring import ExecSpec, ExecScore, parse_pytest_summary, score_execution


def test_parse_pytest_summary_variants():
    assert parse_pytest_summary("5 passed in 0.3s") == (5, 0)
    assert parse_pytest_summary("3 passed, 1 failed in 0.2s") == (3, 1)
    assert parse_pytest_summary("2 failed, 1 error in 0.1s") == (0, 3)
    assert parse_pytest_summary("no tests ran in 0.0s") == (0, 0)
    assert parse_pytest_summary("") == (0, 0)


def test_exec_score_properties():
    s = ExecScore(task_id="t", ran=True, passed=4, failed=0)
    assert s.total == 4 and s.score == 1.0 and s.success is True
    s2 = ExecScore(task_id="t", ran=True, passed=3, failed=1)
    assert abs(s2.score - 0.75) < 1e-9 and s2.success is False
    s3 = ExecScore(task_id="t", ran=False)
    assert s3.score == 0.0 and s3.success is False


def test_score_execution_missing_output(tmp_path):
    spec = ExecSpec("t", "impl.py", "test_h.py", "def test_x():\n    assert True\n",
                    run_cmd=["python", "-m", "pytest", "-q", "test_h.py"])
    score = score_execution(spec, str(tmp_path), python_bin=sys.executable)
    assert not score.ran and "did not create" in score.error


def test_score_execution_runs_and_grades(tmp_path):
    # agent "wrote" impl.py exposing add(); held-out test checks two cases
    (tmp_path / "impl.py").write_text("def add(a, b):\n    return a + b\n")
    test_src = (
        "from impl import add\n"
        "def test_ok():\n    assert add(2, 3) == 5\n"
        "def test_bad():\n    assert add(2, 2) == 5\n"  # deliberately fails
    )
    spec = ExecSpec("t", "impl.py", "test_h.py", test_src,
                    run_cmd=["python", "-m", "pytest", "-q", "--tb=no", "test_h.py"])
    score = score_execution(spec, str(tmp_path), python_bin=sys.executable)
    assert score.ran
    assert score.passed == 1 and score.failed == 1
    assert score.success is False
    # held-out test file is cleaned up afterwards
    assert not (tmp_path / "test_h.py").exists()


def test_score_execution_all_pass(tmp_path):
    (tmp_path / "impl.py").write_text("VALUE = 42\n")
    spec = ExecSpec("t", "impl.py", "test_h.py",
                    "from impl import VALUE\ndef test_v():\n    assert VALUE == 42\n",
                    run_cmd=["python", "-m", "pytest", "-q", "--tb=no", "test_h.py"])
    score = score_execution(spec, str(tmp_path), python_bin=sys.executable)
    assert score.success and score.score == 1.0
