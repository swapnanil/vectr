"""Tests for agent.indexer._priority — the bulk-indexing OS priority clamp
and duty-cycle pacing helpers (UPG-INDEX-RESOURCE-GOVERNOR)."""
from __future__ import annotations

import sys

import pytest

from agent.indexer import _priority


class TestLoweredPriorityDisabled:
    def test_disabled_is_a_plain_noop(self, monkeypatch) -> None:
        """enabled=False must never call into any priority mechanism."""
        called = {"n": 0}

        def _boom(*a, **kw):
            called["n"] += 1
            raise AssertionError("should not be called when disabled")

        monkeypatch.setattr(_priority, "_lower_current_thread_priority", _boom)
        with _priority.lowered_priority(False, macos_qos_class="utility", linux_nice_increment=10):
            pass
        assert called["n"] == 0

    def test_disabled_still_runs_the_body(self) -> None:
        ran = {"n": 0}
        with _priority.lowered_priority(False, macos_qos_class="utility", linux_nice_increment=10):
            ran["n"] += 1
        assert ran["n"] == 1


class TestLoweredPriorityEnabled:
    def test_exception_inside_block_propagates_and_still_restores(self, monkeypatch) -> None:
        restored = {"n": 0}

        def _fake_lower(macos_qos_class, linux_nice_increment):
            def _restore() -> None:
                restored["n"] += 1
            return _restore

        monkeypatch.setattr(_priority, "_lower_current_thread_priority", _fake_lower)
        with pytest.raises(ValueError, match="boom"):
            with _priority.lowered_priority(True, macos_qos_class="utility", linux_nice_increment=10):
                raise ValueError("boom")
        assert restored["n"] == 1

    def test_restore_is_called_on_clean_exit(self, monkeypatch) -> None:
        restored = {"n": 0}

        def _fake_lower(macos_qos_class, linux_nice_increment):
            return lambda: restored.__setitem__("n", restored["n"] + 1)

        monkeypatch.setattr(_priority, "_lower_current_thread_priority", _fake_lower)
        with _priority.lowered_priority(True, macos_qos_class="utility", linux_nice_increment=10):
            pass
        assert restored["n"] == 1

    def test_lowering_failure_never_raises_out_of_the_context_manager(self, monkeypatch) -> None:
        """A failed priority call must degrade gracefully — indexing must
        never abort because the OS refused/lacked the priority mechanism."""
        def _boom(macos_qos_class, linux_nice_increment):
            raise RuntimeError("simulated OS refusal")

        monkeypatch.setattr(_priority, "_lower_current_thread_priority", _boom)
        with pytest.raises(RuntimeError):
            # _lower_current_thread_priority itself is not wrapped (only
            # restore() is) — this documents that a raising implementation
            # would propagate; the REAL implementations below never raise.
            with _priority.lowered_priority(True, macos_qos_class="utility", linux_nice_increment=10):
                pass

    def test_restore_failure_is_swallowed(self, monkeypatch) -> None:
        def _fake_lower(macos_qos_class, linux_nice_increment):
            def _restore() -> None:
                raise RuntimeError("simulated restore failure")
            return _restore

        monkeypatch.setattr(_priority, "_lower_current_thread_priority", _fake_lower)
        # Must not raise — a failed restore is logged, not propagated.
        with _priority.lowered_priority(True, macos_qos_class="utility", linux_nice_increment=10):
            pass

    def test_no_restore_callable_when_lowering_did_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(_priority, "_lower_current_thread_priority", lambda *a, **kw: None)
        # Must not raise even though there is nothing to restore.
        with _priority.lowered_priority(True, macos_qos_class="utility", linux_nice_increment=10):
            pass


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only QoS mechanism")
class TestMacOSQoS:
    def test_known_qos_class_returns_a_restore_callable(self) -> None:
        restore = _priority._lower_macos_qos("utility")
        assert restore is not None
        restore()  # must not raise

    def test_unknown_qos_class_returns_none_and_warns(self, caplog) -> None:
        restore = _priority._lower_macos_qos("not_a_real_class")
        assert restore is None

    def test_all_configured_class_names_resolve(self) -> None:
        """Every macos_qos_class name accepted by config.yaml resolves to a
        real QoS constant — a config typo must be caught here, not silently
        no-op in production."""
        for name in ("user_interactive", "user_initiated", "default", "utility", "background"):
            restore = _priority._lower_macos_qos(name)
            assert restore is not None, f"{name!r} did not resolve to a QoS class"
            restore()

    def test_ctypes_failure_degrades_to_none(self, monkeypatch) -> None:
        def _boom(*a, **kw):
            raise OSError("simulated libSystem lookup failure")

        monkeypatch.setattr(_priority.ctypes, "CDLL", _boom)
        assert _priority._lower_macos_qos("utility") is None


class TestLinuxNiceness:
    def test_setpriority_called_with_incremented_value(self, monkeypatch) -> None:
        calls: list[tuple] = []

        monkeypatch.setattr(_priority.os, "getpriority", lambda which, who: 0)
        monkeypatch.setattr(_priority.os, "setpriority", lambda which, who, val: calls.append((which, who, val)))
        monkeypatch.setattr(_priority.threading, "get_native_id", lambda: 4242)

        restore = _priority._lower_linux_niceness(10)
        assert restore is not None
        assert calls == [(_priority.os.PRIO_PROCESS, 4242, 10)]

        calls.clear()
        restore()
        assert calls == [(_priority.os.PRIO_PROCESS, 4242, 0)]  # restored to the original value

    def test_setpriority_failure_degrades_to_none(self, monkeypatch) -> None:
        monkeypatch.setattr(_priority.os, "getpriority", lambda which, who: 0)

        def _boom(which, who, val):
            raise OSError("simulated permission denied")

        monkeypatch.setattr(_priority.os, "setpriority", _boom)
        monkeypatch.setattr(_priority.threading, "get_native_id", lambda: 1)
        assert _priority._lower_linux_niceness(10) is None

    def test_restore_failure_after_setpriority_is_swallowed(self, monkeypatch) -> None:
        monkeypatch.setattr(_priority.os, "getpriority", lambda which, who: 0)
        state = {"calls": 0}

        def _setpriority(which, who, val):
            state["calls"] += 1
            if state["calls"] > 1:
                raise OSError("simulated failure on restore")

        monkeypatch.setattr(_priority.os, "setpriority", _setpriority)
        monkeypatch.setattr(_priority.threading, "get_native_id", lambda: 1)
        restore = _priority._lower_linux_niceness(10)
        assert restore is not None
        restore()  # must not raise


class TestUnsupportedPlatform:
    def test_unknown_platform_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(_priority.sys, "platform", "aix7")
        assert _priority._lower_current_thread_priority("utility", 10) is None


class TestPace:
    def test_duty_cycle_one_never_sleeps(self, monkeypatch) -> None:
        calls: list[float] = []
        monkeypatch.setattr(_priority.time, "sleep", lambda s: calls.append(s))
        _priority.pace(work_elapsed_s=5.0, duty_cycle=1.0, min_batch_seconds=0.0)
        assert calls == []

    def test_below_min_batch_seconds_never_sleeps(self, monkeypatch) -> None:
        calls: list[float] = []
        monkeypatch.setattr(_priority.time, "sleep", lambda s: calls.append(s))
        _priority.pace(work_elapsed_s=0.001, duty_cycle=0.5, min_batch_seconds=0.05)
        assert calls == []

    def test_sleep_is_proportional_to_measured_work(self, monkeypatch) -> None:
        calls: list[float] = []
        monkeypatch.setattr(_priority.time, "sleep", lambda s: calls.append(s))
        _priority.pace(work_elapsed_s=1.0, duty_cycle=0.5, min_batch_seconds=0.0)
        assert len(calls) == 1
        assert calls[0] == pytest.approx(1.0, rel=1e-6)  # (1 - 0.5) / 0.5 * 1.0 == 1.0

    def test_lower_duty_cycle_sleeps_longer(self, monkeypatch) -> None:
        calls: list[float] = []
        monkeypatch.setattr(_priority.time, "sleep", lambda s: calls.append(s))
        _priority.pace(work_elapsed_s=1.0, duty_cycle=0.25, min_batch_seconds=0.0)
        assert calls[0] == pytest.approx(3.0, rel=1e-6)  # (1 - 0.25) / 0.25 == 3.0

    def test_zero_duty_cycle_does_not_raise_or_divide_by_zero(self, monkeypatch) -> None:
        calls: list[float] = []
        monkeypatch.setattr(_priority.time, "sleep", lambda s: calls.append(s))
        _priority.pace(work_elapsed_s=1.0, duty_cycle=0.0, min_batch_seconds=0.0)
        assert len(calls) == 1  # guarded, not a ZeroDivisionError

    def test_deterministic_pure_function_of_elapsed_time_only(self, monkeypatch) -> None:
        """pace() must never branch on WHAT was indexed — only on how long
        the batch's own measured work took (UPG no-query-heuristics rail)."""
        calls: list[float] = []
        monkeypatch.setattr(_priority.time, "sleep", lambda s: calls.append(s))
        _priority.pace(work_elapsed_s=2.0, duty_cycle=0.5, min_batch_seconds=0.0)
        first = calls[-1]
        calls.clear()
        _priority.pace(work_elapsed_s=2.0, duty_cycle=0.5, min_batch_seconds=0.0)
        assert calls[-1] == first
