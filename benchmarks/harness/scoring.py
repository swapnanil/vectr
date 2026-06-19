#!/usr/bin/env python3
"""Execution scoring for eval v2 — primary success signal.

The regex structural checks in quality_check.py are a secondary signal; the
primary signal is whether the agent's implementation actually *runs and passes
a held-out test*. SWE-bench is execution-only for the same reason: a diff that
reads well but doesn't work is a failure.

Execution scoring only works against a *pinned contract*: an open-ended "write a
class" task can't be machine-graded because the API isn't fixed. So a scorable
task names an exact ``output_path`` the agent must write to and ships a held-out
test (never shown to the agent) that imports that path and asserts behaviour.

The framework is corpus-agnostic: ``run_cmd`` is whatever runs the test
(``pytest`` for Django, ``zig build test`` for TigerBeetle, ``mvn test`` for
CAMEL). Only the pytest summary parser is Python-specific; other runners get
their own parser as they're added.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecSpec:
    """A pinned, execution-scorable task contract."""
    task_id: str
    output_path: str           # repo-relative path the agent must write
    test_filename: str         # repo-relative path to drop the held-out test
    test_source: str           # held-out test source (kept out of the agent prompt)
    run_cmd: list[str]         # command to run the test (cwd = working_dir)
    setup_cmd: list[str] | None = None  # optional one-time build/install
    parser: str = "pytest"     # which summary parser to use

    def output_instruction(self) -> str:
        """Appended to the impl prompt so the artifact lands where the test looks."""
        return (
            f"\n\nWrite your complete implementation to the file `{self.output_path}` "
            f"(create it). Put the implementation there — not only in your chat answer."
        )


# ---------------------------------------------------------------------------
# Summary parsers (pure — unit tested, no subprocess)
# ---------------------------------------------------------------------------

def parse_pytest_summary(output: str) -> tuple[int, int]:
    """Return (passed, failed) from pytest stdout. Errors count as failed.
    'no tests ran' / collection failure → (0, 0)."""
    def _count(token: str) -> int:
        m = re.search(rf"(\d+) {token}\b", output)
        return int(m.group(1)) if m else 0
    passed = _count("passed")
    failed = _count("failed") + _count("error") + _count("errors")
    return passed, failed


PARSERS = {"pytest": parse_pytest_summary}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ExecScore:
    task_id: str
    ran: bool = False           # did the test command execute at all
    passed: int = 0
    failed: int = 0
    returncode: int | None = None
    log_tail: str = ""
    error: str | None = None

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def score(self) -> float:
        """Fraction of held-out assertions that pass. 0.0 if nothing ran."""
        return self.passed / self.total if self.total else 0.0

    @property
    def success(self) -> bool:
        """Execution success = all held-out tests pass and at least one ran."""
        return self.ran and self.total > 0 and self.failed == 0


def score_execution(
    spec: ExecSpec,
    working_dir: str,
    python_bin: str = "python",
    timeout_s: int = 900,
) -> ExecScore:
    """Drop the held-out test into the repo, run it, and score the result.

    ``python_bin`` substitutes a leading ``python`` token in run_cmd/setup_cmd
    so a corpus virtualenv can be used. Nothing here calls an LLM."""
    wd = Path(working_dir)
    test_path = wd / spec.test_filename
    score = ExecScore(task_id=spec.task_id)

    if not (wd / spec.output_path).exists():
        score.error = f"agent did not create {spec.output_path}"
        return score

    try:
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(spec.test_source)
    except OSError as e:
        score.error = f"could not write held-out test: {e}"
        return score

    def _resolve(cmd: list[str]) -> list[str]:
        return [python_bin if tok == "python" else tok for tok in cmd]

    try:
        if spec.setup_cmd:
            subprocess.run(_resolve(spec.setup_cmd), cwd=working_dir,
                           capture_output=True, text=True, timeout=timeout_s)
        proc = subprocess.run(_resolve(spec.run_cmd), cwd=working_dir,
                              capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        score.error = f"test command timed out after {timeout_s}s"
        return score
    except FileNotFoundError as e:
        score.error = f"test runner not found: {e}"
        return score
    finally:
        # leave no held-out test behind in the corpus checkout
        test_path.unlink(missing_ok=True)

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    score.ran = True
    score.returncode = proc.returncode
    score.log_tail = out[-2000:]
    score.passed, score.failed = PARSERS.get(spec.parser, parse_pytest_summary)(out)
    return score


# ---------------------------------------------------------------------------
# Concrete scorable tasks (pinned contracts)
# ---------------------------------------------------------------------------

# Django MoneyField — a pinned version of DJANGO_TASKS["custom_field"]. The held
# -out test exercises the integer-cents storage contract without touching a DB.
_MONEYFIELD_TEST = '''\
import decimal, importlib.util, pathlib, sys
import pytest

_OUT = pathlib.Path(__file__).resolve().parent / "money_field_impl.py"

def _load():
    spec = importlib.util.spec_from_file_location("money_field_impl", _OUT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["money_field_impl"] = mod
    spec.loader.exec_module(mod)
    return mod.MoneyField

def test_decimal_stored_as_integer_cents():
    MoneyField = _load()
    assert MoneyField().get_prep_value(decimal.Decimal("12.50")) == 1250

def test_integer_cents_back_to_decimal():
    MoneyField = _load()
    f = MoneyField()
    assert decimal.Decimal(f.from_db_value(1250, None, None)) == decimal.Decimal("12.50")

def test_currency_round_trips_through_deconstruct():
    MoneyField = _load()
    name, path, args, kwargs = MoneyField(currency="EUR").deconstruct()
    assert kwargs.get("currency") == "EUR"

def test_default_currency_is_usd():
    MoneyField = _load()
    assert MoneyField().currency == "USD"

def test_negative_value_rejected():
    # The spec requires non-negative validation but does not pin WHERE: accept
    # rejection at the prep-value layer OR via Django's idiomatic validation
    # path (validators / clean()), since the research prompt steers toward the
    # latter. Any of these raising on a negative value satisfies the contract.
    MoneyField = _load()
    f = MoneyField()
    neg = decimal.Decimal("-1.00")
    attempts = [
        lambda: f.get_prep_value(neg),
        lambda: f.run_validators(neg),
        lambda: f.clean(neg, None),
    ]
    rejected = False
    for attempt in attempts:
        try:
            attempt()
        except AttributeError:
            continue  # field didn't implement this hook; try the next
        except Exception:
            rejected = True
            break
    assert rejected, "negative value was not rejected at any validation layer"
'''

DJANGO_EXEC_SPECS: dict[str, ExecSpec] = {
    "custom_field": ExecSpec(
        task_id="custom_field",
        output_path="money_field_impl.py",
        test_filename="test_money_field_heldout.py",
        test_source=_MONEYFIELD_TEST,
        run_cmd=["python", "-m", "pytest", "-q", "--tb=short",
                 "test_money_field_heldout.py"],
        parser="pytest",
    ),
}
