"""Pytest configuration for the clew_tui interaction test suite.

Registers the ``interaction`` marker so ``pytest -m interaction`` works
without warnings, and provides shared fixtures (a fake bridge so the
tests don't need real LLM credentials, an isolated HOME so tests don't
clobber the real ~/.clew).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Make sure the project root is on sys.path so `import clew_tui` works.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``interaction`` marker."""
    config.addinivalue_line(
        "markers",
        "interaction: Pilot-driven TUI interaction test. Needs Textual's "
        "test harness; slower than a unit test but exercises real key/mouse "
        "input. Filter with `pytest -m interaction`.",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests in this directory with ``interaction`` so users
    can run ``pytest clew_tui/tests/`` (without -m) and still get them
    grouped, and so ``pytest -m "not interaction"`` cleanly excludes them.
    """
    for item in items:
        # If the test file lives under clew_tui/tests/, auto-mark it.
        if "clew_tui/tests" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.interaction)


# ── Shared fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect ~/.clew to a temp dir so tests don't clobber the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    yield


@pytest.fixture
def fake_bridge():
    """A FakeClewBridge that doesn't need real LLM credentials.

    The real ClewBridge builds a ProviderRegistry + AgentRuntime on
    first use (ensure_agent()). For Pilot tests we never want that to
    happen — we just want to drive the TUI's keyboard/mouse paths and
    assert state changes. The fake records every call so tests can
    assert "set_section was called with 'office'" instead of needing
    a real provider.
    """
    from ._fake_bridge import FakeClewBridge
    return FakeClewBridge(workspace=str(tempfile.gettempdir()))
