"""
Clew v1.0.3 — Auto-Updater.

Checks the GitHub Releases API for a newer version and emits a signal
if one is available. Uses stdlib urllib only — no external dependencies.

v1.1.5-fix (clew_bug_report.md bug #11): previously the default ``repo``
argument was the placeholder string ``"user/clew"``, and both call sites
in the codebase (``main_window.py:240`` and ``web_bridge.py:835``)
constructed ``AutoUpdater(parent=self)`` without ever passing a real
``repo``. The constructor left ``self._repo == "user/clew"`` forever,
and ``check_for_updates()`` explicitly skipped when it saw that string,
so the auto-update feature was effectively a no-op — every startup
silently did nothing despite the "Check for updates" button in the UI.

Fix:
* The default ``repo`` is now the real Clew repo on GitHub
  (``DEFAULT_REPO`` below).
* ``AutoUpdater(repo=None)`` lets the caller explicitly disable update
  checks (e.g. enterprise builds, air-gapped machines).
* ``set_repo(repo)`` allows the bridge to override the repo at runtime
  from the user config (``update_repo`` key in ``~/.clew/config.json``).
* ``check_for_updates()`` only skips when the repo is *falsy* (None /
  empty), not when it matches a placeholder.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal, QThread

logger = logging.getLogger(__name__)

__version__ = "1.0.12"

# v1.1.5-fix (bug #11): real default repo. The old placeholder
# "user/clew" caused every update check to be silently skipped.
# This constant can be overridden by the caller (e.g. config-driven
# ``update_repo``), but the out-of-the-box behaviour now actually
# hits the GitHub Releases API for the real project.
DEFAULT_REPO = "zai-shop/clew"


def _parse_version(version_str: str) -> tuple:
    """Parse 'v1.0.3' or '1.0.3' into (1, 0, 3)."""
    cleaned = version_str.strip().lstrip("vV")
    parts = re.split(r"[.\-]", cleaned)
    result = []
    for p in parts:
        m = re.match(r"(\d+)", p)
        if m:
            result.append(int(m.group(1)))
        else:
            break
    return tuple(result) if result else (0, 0, 0)


def get_current_version() -> str:
    """Return the current Clew version string."""
    try:
        from . import __version__ as pkg_version
        return pkg_version
    except (ImportError, AttributeError):
        pass
    return __version__


class _UpdateWorker(QThread):
    """Background thread that hits the GitHub API."""

    result = Signal(dict)  # {"update_available": bool, ...}

    def __init__(self, current_version: str, repo: str = DEFAULT_REPO, parent=None):
        super().__init__(parent)
        self._current = current_version
        self._repo = repo

    def run(self):
        url = f"https://api.github.com/repos/{self._repo}/releases/latest"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Clew-Updater/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            tag = data.get("tag_name", "")
            latest_parsed = _parse_version(tag)
            current_parsed = _parse_version(self._current)

            if latest_parsed > current_parsed:
                body = data.get("body", "") or ""
                self.result.emit({
                    "update_available": True,
                    "latest": tag,
                    "current": self._current,
                    "url": data.get("html_url", ""),
                    "body": body[:500],
                    "published_at": data.get("published_at", ""),
                })
            else:
                self.result.emit({"update_available": False})

        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.info("[updater] repo/releases not found (private or no releases): %s", self._repo)
            else:
                logger.warning("[updater] HTTP %s: %s", e.code, e.reason)
            self.result.emit({"update_available": False, "error": f"HTTP {e.code}"})
        except Exception as e:
            logger.warning("[updater] check failed: %s", e)
            self.result.emit({"update_available": False, "error": str(e)})


class AutoUpdater(QObject):
    """
    Checks GitHub for a newer Clew release.

    Usage::

        updater = AutoUpdater(parent=self)
        updater.update_available.connect(self._on_update)
        updater.check_for_updates()

    v1.1.5-fix (bug #11): the default ``repo`` is now the real Clew
    repo (``DEFAULT_REPO``). Pass ``repo=None`` (or empty string) to
    explicitly disable update checks for this instance — this is what
    air-gapped / enterprise builds should do.
    """

    update_available = Signal(dict)   # full release info
    no_update = Signal()

    def __init__(self, repo: Optional[str] = DEFAULT_REPO, parent=None):
        super().__init__(parent)
        # Normalise: empty string / "user/clew" (legacy placeholder)
        # → treat as None and skip the check, otherwise the GitHub API
        # returns 404 every time and clutters the log.
        # v1.1.5-fix (bug #11): the *default* is now the real repo, so
        # a plain ``AutoUpdater(parent=self)`` actually checks for
        # updates out of the box.
        self._repo: Optional[str] = self._normalise_repo(repo)
        self._worker: Optional[_UpdateWorker] = None

    @staticmethod
    def _normalise_repo(repo: Optional[str]) -> Optional[str]:
        """Return a clean repo slug, or *None* if updates are disabled.

        v1.1.5-fix (bug #11): we still treat the legacy placeholder
        ``"user/clew"`` as "disabled" so that old config files which
        explicitly stored that string don't suddenly start hitting the
        GitHub API for a repo that doesn't exist.
        """
        if not repo:
            return None
        repo = repo.strip()
        if not repo or repo == "user/clew":
            return None
        return repo

    def set_repo(self, repo: Optional[str]) -> None:
        """v1.1.5 — override the GitHub repo at runtime.

        Reads from ``config["update_repo"]`` in the bridge. Pass *None*
        or an empty string to disable update checks for this instance.
        The legacy placeholder ``"user/clew"`` is also treated as
        "disabled" so old config files don't 404 on every check.
        """
        self._repo = self._normalise_repo(repo)

    @property
    def repo(self) -> Optional[str]:
        """The current GitHub ``owner/name`` slug, or *None* if disabled."""
        return self._repo

    def check_for_updates(self, current_version: Optional[str] = None) -> None:
        """Start a background check. Results arrive via signals.

        v1.0.5-hotfix: skip the check entirely if the repo is *falsy*
        (None / empty / legacy placeholder). Previously every startup
        fired two HTTP requests to
        ``api.github.com/repos/user/clew/releases/latest`` which 404'd
        every time, wasting ~2 seconds and cluttering the log.

        v1.1.5-fix (bug #11): the placeholder check is now done in
        ``_normalise_repo`` at construction time, so by the time we
        get here ``self._repo`` is either a real slug or *None*. The
        same logic is also applied to ``set_repo()`` so callers can
        cleanly disable updates at runtime.
        """
        version = current_version or get_current_version()
        if self._worker and self._worker.isRunning():
            return

        # Skip if the repo is None / empty — checks are disabled for
        # this instance (e.g. air-gapped build or user opted out).
        if not self._repo:
            logger.debug("[updater] skipping check — repo is disabled/empty")
            return

        self._worker = _UpdateWorker(version, self._repo, parent=self)
        self._worker.result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, data: Dict[str, Any]) -> None:
        if data.get("update_available"):
            logger.info(
                "[updater] update available: %s → %s",
                data.get("current"),
                data.get("latest"),
            )
            self.update_available.emit(data)
        else:
            self.no_update.emit()