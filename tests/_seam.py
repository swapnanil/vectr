"""Drift-resistant assertions for internal service-seam mock calls.

UPG-TEST-SIGNATURE-DRIFT: twice in wave 2a a service-call signature gained a
kwarg while the corresponding test's literal `assert_called_with(...)` kwargs
dict silently went stale — a single signature change reddened ~24 unrelated
seam tests at once instead of producing one targeted failure.

`assert_seam_call` replaces the full-literal `assert_called_with` on those seams
with a signature-bound, subset assertion:

  * Every kwarg the test names is validated against the REAL callee's
    `inspect.signature`. A kwarg the callee renamed or removed fails with a
    targeted message naming the stale key — the exact drift caught precisely,
    not as a confusing whole-call mismatch.
  * Only the named kwargs are compared against the recorded call. Adding a NEW
    parameter to the callee (and forwarding it in production) does not break the
    assertion, so one signature change no longer reddens every seam test.

Pass the REAL callee (e.g. `VectrService.recall`) as `real_callee` — the mock
whose call is being checked carries no signature, so it cannot self-validate.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable
from unittest.mock import Mock


def assert_seam_call(mock_method: Mock, real_callee: Callable[..., Any], **expected: Any) -> None:
    """Assert `mock_method` was called with (at least) `expected`, every key of
    which is a real parameter of `real_callee`."""
    sig = inspect.signature(real_callee)
    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if not accepts_var_kw:
        unknown = [k for k in expected if k not in sig.parameters]
        assert not unknown, (
            f"{getattr(real_callee, '__qualname__', real_callee)} has no parameter(s) "
            f"{unknown} — this seam assertion has drifted from the real signature "
            f"(known params: {sorted(sig.parameters)})"
        )
    assert mock_method.called, "expected the seam to be called, but it was not"
    actual = mock_method.call_args.kwargs
    _missing = object()
    mismatched = {
        k: {"expected": v, "actual": actual.get(k, _missing)}
        for k, v in expected.items()
        if actual.get(k, _missing) != v
    }
    assert not mismatched, f"seam call kwargs differ from expected: {mismatched}"
