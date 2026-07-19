"""Tests for the drift-resistant seam assertion (UPG-TEST-SIGNATURE-DRIFT)."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from tests._seam import assert_seam_call


def _callee_v1(a=None, b=None):  # the "real" callee, version 1
    ...


def _callee_v2(a=None, b=None, c=None):  # v1 + a new param `c` (signature grew)
    ...


def _make_mock_called_with(**kwargs) -> Mock:
    m = Mock()
    m(**kwargs)
    return m


def test_subset_match_passes() -> None:
    m = _make_mock_called_with(a=1, b=2)
    assert_seam_call(m, _callee_v1, a=1)  # asserting only a subset is fine


def test_value_mismatch_fails() -> None:
    m = _make_mock_called_with(a=1, b=2)
    with pytest.raises(AssertionError, match="differ from expected"):
        assert_seam_call(m, _callee_v1, a=999)


def test_stale_kwarg_not_on_callee_fails_precisely() -> None:
    """The exact drift: the test names a kwarg the real callee does not have."""
    m = _make_mock_called_with(a=1, zzz=2)
    with pytest.raises(AssertionError, match="has no parameter"):
        assert_seam_call(m, _callee_v1, zzz=2)


def test_new_callee_param_does_not_break_unrelated_subset() -> None:
    """The seam grew a param `c` and production now forwards it; a test that only
    asserts the kwargs it cares about must keep passing (no whole-call redness)."""
    m = _make_mock_called_with(a=1, b=2, c=3)
    assert_seam_call(m, _callee_v2, a=1)  # unaware of `c`, still green


def test_uncalled_mock_fails() -> None:
    with pytest.raises(AssertionError, match="was not"):
        assert_seam_call(Mock(), _callee_v1, a=1)


def test_var_keyword_callee_skips_unknown_check() -> None:
    def _kw_callee(**kwargs):
        ...

    m = _make_mock_called_with(anything=1)
    assert_seam_call(m, _kw_callee, anything=1)
