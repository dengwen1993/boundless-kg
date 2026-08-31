"""Tests for ``src.agent.orchestrator`` status reporting.

These pin the contract that ``get_agent_status()`` exposes:
  * ``agent_available`` reflects whether the deepagents build succeeded
  * ``agent_error`` carries the captured exception when it did not
  * the cache survives across calls (re-running is idempotent)
  * ``get_agent_status()`` is non-blocking — it returns the cached
    state without triggering a build; use ``get_agent()`` or
    ``prebuild_agent()`` to trigger one.
"""

from __future__ import annotations

import pytest

from src.agent import orchestrator
from src.agent.orchestrator import (
    get_agent,
    get_agent_status,
    prebuild_agent,
    reset_agent_status,
)


def _force_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``_try_build_agent`` with one that captures a deterministic error."""
    def boom():
        return (None, "RuntimeError: synthetic build failure")

    monkeypatch.setattr(orchestrator, "_try_build_agent", boom)


def _force_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``_try_build_agent`` with one that returns a fake agent."""
    class _FakeAgent:
        pass

    def ok():
        return (_FakeAgent(), None)

    monkeypatch.setattr(orchestrator, "_try_build_agent", ok)


def test_status_reports_failure_when_build_errors(monkeypatch) -> None:
    reset_agent_status()
    _force_failure(monkeypatch)
    # get_agent_status() is non-blocking; trigger the build explicitly.
    get_agent()
    status = get_agent_status()
    assert status["agent_available"] is False
    assert "synthetic build failure" in (status["agent_error"] or "")
    # And get_agent() must hand back None so callers can short-circuit.
    assert get_agent() is None


def test_status_reports_success_when_build_passes(monkeypatch) -> None:
    reset_agent_status()
    _force_success(monkeypatch)
    get_agent()  # trigger build
    status = get_agent_status()
    assert status["agent_available"] is True
    assert status["agent_error"] is None


def test_status_is_cached_across_calls(monkeypatch) -> None:
    """First call should populate the cache; subsequent calls re-use it
    even if the underlying state would now fail."""
    reset_agent_status()
    _force_success(monkeypatch)
    get_agent()  # trigger build
    first = get_agent_status()

    # Swap the build function to one that would fail, but the cache
    # was already populated; the second call must still report success.
    _force_failure(monkeypatch)
    second = get_agent_status()

    assert second["agent_available"] is True
    assert second["agent_error"] is None
    assert first == second


def test_get_agent_returns_none_when_status_is_failure(monkeypatch) -> None:
    reset_agent_status()
    _force_failure(monkeypatch)
    assert get_agent() is None


def test_reset_clears_cache(monkeypatch) -> None:
    reset_agent_status()
    _force_success(monkeypatch)
    get_agent()  # trigger build
    assert get_agent_status()["agent_available"] is True

    reset_agent_status()
    _force_failure(monkeypatch)
    get_agent()  # trigger build after reset
    # After reset + new failure injection, the next status must show
    # the failure (cache cleared).
    assert get_agent_status()["agent_available"] is False


def test_prebuild_agent_triggers_build(monkeypatch) -> None:
    """prebuild_agent() should populate the cache just like get_agent()."""
    reset_agent_status()
    _force_success(monkeypatch)
    prebuild_agent()
    status = get_agent_status()
    assert status["agent_available"] is True
    assert status["agent_error"] is None


def test_get_agent_status_does_not_trigger_build(monkeypatch) -> None:
    """get_agent_status() must be non-blocking — it should NOT trigger
    a build, just return the current (unbuilt) state."""
    reset_agent_status()
    # Even with a success-mocked build, get_agent_status() should not
    # call _try_build_agent, so the status should show 'not built'.
    _force_success(monkeypatch)
    call_count = 0
    original = orchestrator._try_build_agent

    def counting_build():
        nonlocal call_count
        call_count += 1
        return original()

    monkeypatch.setattr(orchestrator, "_try_build_agent", counting_build)
    status = get_agent_status()
    assert status["agent_available"] is False  # not built yet
    assert call_count == 0  # build was NOT triggered
