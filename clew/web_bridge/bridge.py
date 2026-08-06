"""
clew.web_bridge.bridge — Qt-free shim.

v2.2.0: the legacy ``ClewBridge`` QObject (a 4400-line PySide6 /
QWebChannel adapter that exposed Python methods to the in-process
HTML frontend) has been removed. The browser now talks to the
backend exclusively via the HTTP REST API + SSE in
:mod:`clew.api_server` (served by :mod:`clew.web_server`).

This module is kept as a marker so any external code or docs that
still reference ``clew.web_bridge.bridge.ClewBridge`` get a clear
error instead of a silent ImportError.

For the TUI bridge (plain Python, no Qt, still supported) see
:mod:`clew_tui.bridge`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ClewBridgeRemovedError(RuntimeError):
    """Raised when the legacy Qt ClewBridge is constructed."""


class ClewBridge:  # noqa: N801 - keep the legacy name
    """Legacy Qt bridge — now a hard error.

    The PySide6 ``ClewBridge`` QObject was removed in v2.2.0. Use
    one of these instead:

    * For an in-process Python bridge (e.g. tests, scripts, TUI):
      ``clew_tui.bridge.ClewBridge`` (plain Python, no Qt).
    * For the browser GUI: the HTTP API at ``/api/*`` served by
      :class:`clew.api_server.ClewAPIServer` /
      :class:`clew.web_server.ClewWebServer`.
    """

    def __init__(self, *args, **kwargs):
        raise ClewBridgeRemovedError(
            "clew.web_bridge.bridge.ClewBridge was removed in v2.2.0 — "
            "the Qt / PySide6 GUI is no longer maintained. Use "
            "`clew_tui.bridge.ClewBridge` for an in-process Python bridge, "
            "or `ClewWebServer` / `ClewAPIServer` for the HTTP API the "
            "browser frontend consumes."
        )


__all__ = ["ClewBridge", "ClewBridgeRemovedError"]
