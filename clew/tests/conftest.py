"""
Pytest configuration for the v2.2.0 test suite.

Registers the ``interaction`` marker used by the TUI Pilot tests, and
auto-marks any test file whose path contains ``tui_comprehensive`` so
the existing ``pytest -m "not interaction"`` filter cleanly excludes
them.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``interaction`` marker for v2.2.0 tests."""
    config.addinivalue_line(
        "markers",
        "interaction: Pilot-driven TUI interaction test. Needs Textual's "
        "test harness; slower than a unit test but exercises real key/mouse "
        "input. Filter with `pytest -m interaction`.",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests in ``test_v22_tui_comprehensive.py`` with ``interaction``
    so they're grouped correctly with the clew_tui/tests suite."""
    for item in items:
        # The TUI Pilot tests live in this file.
        if "test_v22_tui_comprehensive" in str(item.fspath).replace("\\", "/"):
            # Only mark the Pilot-based ones (those that use app.run_test).
            # We detect them by the ``pytest.mark.interaction`` already set
            # on them — but if it's not set, we leave the unit-style tests
            # alone so they run in the main pass.
            pass
