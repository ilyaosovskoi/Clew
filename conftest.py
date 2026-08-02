"""Root-level pytest configuration.

Two purposes:

1. ``collect_ignore`` keeps pytest from collecting the standalone smoke-test
   scripts (``clew/smoke_tests.py`` and ``clew/web_smoke_test.py``) as test
   modules. ``web_smoke_test.py`` defines a top-level ``def test(name: str)``
   function, which pytest would otherwise try to collect and then error on a
   missing ``name`` fixture.
2. ``pytest_configure`` forces Qt into the offscreen platform plugin in CI
   environments so that pytest-qt / PySide6 tests can run on headless runners
   with no display. Gated on the ``CI`` env var (set by GitHub Actions and most
   other CI systems) so local macOS / Windows / Linux dev runs keep their
   native Qt platform.
"""

import os

# Standalone scripts that live under clew/ but are NOT pytest test modules.
collect_ignore = ["clew/smoke_tests.py", "clew/web_smoke_test.py"]


def pytest_configure(config):
    # Headless CI only: use the offscreen QPA platform so QtGui imports succeed
    # on runners with no display. The GitHub Actions workflow also sets this
    # directly, so this is belt-and-suspenders for other CI systems. Local dev
    # runs are left untouched (native cocoa/windows/xcb platform preserved).
    if os.environ.get("CI"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
