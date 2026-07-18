"""
Clew v1.0.5 — Main Window.

Window that wraps a single QWebEngineView loading clew/web/index.html.

Platform-specific chrome:
  • macOS   → a real (titled) window, but with a transparent titlebar and
              content extending underneath it (native Cocoa calls in
              _apply_mac_titlebar_style). This is what removes the gray
              strip WITHOUT losing macOS's native traffic lights, native
              rounded corners, or native drop shadow — all three of which
              a plain Qt::FramelessWindowHint window loses.
  • Windows → FramelessWindowHint; custom HTML window controls
              (min / max / close) rendered in the top-right of the topbar.
  • Linux   → Same frameless approach as Windows.

Window dragging uses Qt's native system move (``QWindow.startSystemMove``)
on all platforms, triggered from JS on [data-drag-region] elements — the
web content covers the whole window so the OS can't detect titlebar drags
on its own, even on macOS. Edge resizing on Windows/Linux similarly uses
``QWindow.startSystemResize`` from [data-resize] elements; on macOS the
window is still a normal resizable NSWindow, so native edge resizing just
works without any of our custom resize-edge handles.

Architecture:
    ┌──────────────────────────────────────────────────────────┐
    │  ClewMainWindow (QMainWindow)                            │
    │  ┌───────────────────────────────────────────────────┐   │
    │  │  QWebEngineView  ←  clew/web/index.html           │   │
    │  │       ↕  QWebChannel                              │   │
    │  │  ClewBridge (QObject)                             │   │
    │  │       ├─ ProviderRegistry                         │   │
    │  │       │     ├─ OllamaProvider (localhost:11434)    │   │
    │  │       │     ├─ LMStudioProvider (localhost:1234)   │   │
    │  │       │     ├─ OpenAIProvider                     │   │
    │  │       │     ├─ AnthropicProvider                  │   │
    │  │       │     ├─ OpenRouterProvider                  │   │
    │  │       │     └─ GroqProvider                       │   │
    │  │       ├─ CodeViewerService                        │   │
    │  │       └─ GenerationWorker (QThread)               │   │
    │  └───────────────────────────────────────────────────┘   │
    └──────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QMainWindow, QApplication, QFileDialog, QMessageBox

# WebEngine is shipped as part of the PySide6 wheel (the "addons" extra).
# If it's missing we want a clear, actionable error rather than a stack trace.
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import (
        QWebEngineProfile,
        QWebEngineUrlScheme,
        QWebEnginePage,
    )
    from PySide6.QtWebChannel import QWebChannel
    _WEBENGINE_AVAILABLE = True
except ImportError as _e:
    _WEBENGINE_AVAILABLE = False
    _WEBENGINE_IMPORT_ERROR = _e
    # Fallback: stub the names so module-level class definitions don't crash.
    # The ClewMainWindow __init__ will detect _WEBENGINE_AVAILABLE=False and
    # show an actionable error dialog before any of these are used.
    QWebEngineView = object
    QWebEngineProfile = object
    QWebEngineUrlScheme = object
    QWebEnginePage = object
    QWebChannel = object

from .web_bridge import ClewBridge
from .api_server import ClewAPIServer
from .auto_updater import AutoUpdater

logger = logging.getLogger(__name__)


# ── Platform detection ──────────────────────────────────────────────
def _platform_id() -> str:
    """Return 'darwin' / 'win32' / 'linux' — used by the frontend to decide
    where to render the window controls (left traffic lights vs. right min/max/close)."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def _is_macos() -> bool:
    """True on macOS — used to pick the window-chrome strategy."""
    return sys.platform == "darwin"


# Resize edge name → Qt edge flag mapping (passed from JS as a string)
_EDGE_MAP = {
    "top":         Qt.Edge.TopEdge,
    "bottom":      Qt.Edge.BottomEdge,
    "left":        Qt.Edge.LeftEdge,
    "right":       Qt.Edge.RightEdge,
    "top-left":    Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
    "top-right":   Qt.Edge.TopEdge | Qt.Edge.RightEdge,
    "bottom-left": Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
    "bottom-right": Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
}


class ClewMainWindow(QMainWindow):
    """Window that hosts the HTML frontend.

    • macOS         → a real (titled) window whose titlebar is made
                      transparent and whose content extends underneath it
                      (see _apply_mac_titlebar_style). This keeps macOS's
                      NATIVE traffic lights, native rounded corners, and
                      native drop shadow — a plain Qt::FramelessWindowHint
                      window loses all three, which is why that approach
                      was reverted.
    • Windows/Linux → FramelessWindowHint with custom HTML controls
                      (square min/max/close buttons, .win-controls).
    """

    def __init__(self, project_root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clew")
        # Medium default size — comfortable on a 13" laptop, not "almost full
        # screen". The user explicitly asked for a smaller starting size.
        self.resize(1120, 720)
        self.setMinimumSize(420, 320)

        # ── Platform-specific window chrome ──────────────────────
        if _is_macos():
            # Keep a REAL titled window (no FramelessWindowHint). We make the
            # titlebar transparent and extend the content view under it via
            # native Cocoa calls in _apply_mac_titlebar_style(), called from
            # showEvent() once the NSWindow actually exists. Because the
            # window is still a normal titled/resizable NSWindow under the
            # hood, macOS keeps drawing its native traffic lights, native
            # rounded corners, and native drop shadow for us — none of
            # which a borderless (FramelessWindowHint) window gets.
            self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            self._mac_style_applied = False
        else:
            # Windows / Linux: frameless look with custom HTML controls
            # (close / minimize / maximize) rendered in the topbar.
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)

        # Persisted geometry — restore last position/size (but NOT the
        # maximized state; the user wants a medium starting size).
        self._restore_geometry()

        # Tell the bridge who owns it so window-control slots can call back.
        self._api_server = ClewAPIServer()
        self._api_server.start()
        logger.info("[main_window] API server started on port %d", self._api_server.port)

        if not _WEBENGINE_AVAILABLE:
            # WebEngine binaries missing — show a clear dialog and exit.
            QMessageBox.critical(
                self,
                "Clew — WebEngine not available",
                (
                    "Clew needs the Qt WebEngine binaries to render its UI.\n\n"
                    "They ship with the standard PySide6 wheel — please reinstall:\n\n"
                    "    pip install --force-reinstall PySide6\n\n"
                    f"Underlying import error:\n{_WEBENGINE_IMPORT_ERROR}"
                ),
            )
            # Schedule exit on the next event loop tick
            QTimer.singleShot(0, QApplication.quit)
            return

        # ── Web engine ──────────────────────────────────────────
        self._profile = QWebEngineProfile("clew-profile", self)
        self._profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )

        # Allow local content (file://) to access remote URLs (http://127.0.0.1).
        # Without this, QWebEngineView blocks fetch() from file:// origin to the
        # local API server, causing "Failed to fetch" errors.
        self._profile.settings().setAttribute(
            self._profile.settings().WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        # Allow local content to access remote fonts (Google Fonts)
        self._profile.settings().setAttribute(
            self._profile.settings().WebAttribute.LocalContentCanAccessFileUrls, True
        )

        self.view = QWebEngineView(self)
        self.view.setPage(_ClewWebPage(self._profile, self))

        page = self.view.page()
        page.setBackgroundColor(Qt.GlobalColor.transparent)

        # ── Web channel ─────────────────────────────────────────
        self._channel = QWebChannel(self)
        self.bridge = ClewBridge(project_root=project_root, parent=self)
        # Back-reference so bridge slots can call minimize/maximize/close
        self.bridge._main_window = self
        self._channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self._channel)

        # ── Load the HTML ───────────────────────────────────────
        html_path = self._resolve_html_path()
        if html_path and html_path.exists():
            url = QUrl.fromLocalFile(str(html_path))
            self.view.setUrl(url)
            logger.info(f"[main_window] loaded UI from {html_path}")
        else:
            logger.error(f"[main_window] index.html not found at {html_path}")
            self._show_fallback_html()

        self.setCentralWidget(self.view)

        # ── Shortcuts ───────────────────────────────────────────
        # ⌘+O — open project folder
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._open_project_dialog)
        # ⌘+, — settings (handled in HTML, but we keep a system shortcut too)
        QShortcut(QKeySequence("Ctrl+Comma"), self, activated=lambda: self._eval_js("window.dispatchEvent(new KeyboardEvent('keydown',{key:',' ,metaKey:true}))"))
        # ⌘+W — close window (handled by Qt by default)
        # ⌘+Q — quit
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=QApplication.quit)
        # ⌘+M — minimize (macOS convention)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.showMinimized)

        # ── Sync project root to bridge after page load ────────
        self.view.loadFinished.connect(self._on_load_finished)

        # ── v1.0.3: Auto-update check on startup ─────────────
        # v1.1.5-fix (clew_bug_report.md bug #11): previously the
        # updater was constructed without a `repo` argument, so it
        # defaulted to the placeholder "user/clew" — and
        # `check_for_updates()` explicitly skipped the check when it
        # saw that placeholder. As a result the "Check for updates"
        # button and the startup auto-check were both no-ops.
        # We now load `update_repo` from ~/.clew/config.json (if set)
        # and fall back to AutoUpdater.DEFAULT_REPO (the real Clew
        # repo) when the key is missing, so updates actually work
        # out of the box.
        try:
            from .auto_updater import DEFAULT_REPO
            try:
                from .web_bridge import _load_config
                _mw_cfg = _load_config()
                _mw_repo = _mw_cfg.get("update_repo") or DEFAULT_REPO
            except Exception:
                _mw_repo = DEFAULT_REPO
        except Exception:
            _mw_repo = "user/clew"  # last-resort fallback; never actually used
        self._updater = AutoUpdater(repo=_mw_repo, parent=self)
        self._updater.update_available.connect(self._on_update_found)
        QTimer.singleShot(5000, self._updater.check_for_updates)

        # ── v1.1.0: Start MCP servers (configured in ~/.clew/mcp.json) ──
        # Run on a background thread so we don't block the UI while
        # subprocesses spawn and complete their initialize handshake.
        try:
            from .mcp_manager import get_mcp_manager
            from PySide6.QtCore import QThread
            class _MCPStarter(QThread):
                def run(self_inner):
                    try:
                        get_mcp_manager().start_all()
                    except Exception as e:
                        logger.warning("[main_window] MCP start_all failed: %s", e)
            self._mcp_starter = _MCPStarter(self)
            QTimer.singleShot(2000, self._mcp_starter.start)
        except Exception as e:
            logger.warning("[main_window] MCP manager init failed: %s", e)

    # ── Window geometry persistence ────────────────────────────────

    def _geometry_file(self) -> Path:
        """~/.clew/window_geometry.json — restored on next launch."""
        from pathlib import Path as _P
        home = _P.home() / ".clew"
        home.mkdir(parents=True, exist_ok=True)
        return home / "window_geometry.json"

    def _restore_geometry(self) -> None:
        """Restore the last window position/size.

        Notes:
          • We intentionally do NOT auto-maximize on launch — the user
            wants Clew to start at a comfortable medium size, not maximized.
          • We also reject persisted geometries that cover ≥85% of the
            available screen in BOTH dimensions — those are almost always
            leftovers from a previously maximized state.
        """
        try:
            f = self._geometry_file()
            if not f.exists():
                return
            data = json.loads(f.read_text(encoding="utf-8"))
            x, y, w, h = data.get("x"), data.get("y"), data.get("w"), data.get("h")
            if all(isinstance(v, int) for v in (x, y, w, h)):
                # Reject "almost fullscreen" persisted sizes — use the
                # default medium size instead.
                screen = QApplication.primaryScreen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    if (avail.width() > 0 and avail.height() > 0
                            and w >= avail.width() * 0.85
                            and h >= avail.height() * 0.85):
                        logger.info(
                            "[main_window] persisted geometry covers >=85%% of "
                            "the screen — using the default medium size instead"
                        )
                        return
                if w >= self.minimumWidth() and h >= self.minimumHeight():
                    self.setGeometry(x, y, w, h)
            # NOTE: deliberately NOT restoring the `maximized` flag — see
            # the docstring above.
        except Exception as e:
            logger.warning(f"[main_window] restore geometry failed: {e}")

    def _save_geometry(self) -> None:
        try:
            geo = self.geometry()
            data = {
                "x": geo.x(), "y": geo.y(),
                "w": geo.width(), "h": geo.height(),
                "maximized": self.isMaximized(),
            }
            self._geometry_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[main_window] save geometry failed: {e}")

    # ── Window control API — called by bridge slots from JS ────────

    def start_system_move(self) -> bool:
        """Begin a native OS window drag. Returns True if the underlying
        QWindow accepted the request. Called from JS on mousedown in a
        [data-drag-region] element, on every platform — even on macOS,
        where the titlebar is visually transparent but our own web content
        covers it, so the OS can't detect a "drag the titlebar" gesture on
        its own.
        """
        try:
            wh = self.windowHandle()
            if wh is None:
                return False
            wh.startSystemMove()
            return True
        except Exception as e:
            logger.warning(f"[main_window] startSystemMove failed: {e}")
            return False

    def start_system_resize(self, edge: str) -> bool:
        """Begin a native OS window resize from the given edge.
        edge is one of: top/bottom/left/right/top-left/top-right/
        bottom-left/bottom-right. Called from JS on mousedown in a
        [data-resize] element, on every platform.
        """
        try:
            wh = self.windowHandle()
            if wh is None:
                return False
            qt_edge = _EDGE_MAP.get(edge)
            if qt_edge is None:
                return False
            wh.startSystemResize(qt_edge)
            return True
        except Exception as e:
            logger.warning(f"[main_window] startSystemResize failed: {e}")
            return False

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def minimize(self) -> None:
        self.showMinimized()

    def close_window(self) -> None:
        self.close()

    # ── HTML resolution ─────────────────────────────────────────

    @staticmethod
    def _resolve_html_path() -> Optional[Path]:
        candidates = [
            Path(__file__).resolve().parent / "web" / "index.html",
            Path(__file__).resolve().parent.parent / "clew" / "web" / "index.html",
            Path(__file__).resolve().parent.parent / "web" / "index.html",
            Path.cwd() / "clew" / "web" / "index.html",
        ]
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    def _show_fallback_html(self) -> None:
        """Inline fallback so the window isn't blank if index.html is missing."""
        self.view.setHtml(
            """<!DOCTYPE html><html><head><style>
            body { background:#0B0B0D; color:#E6E7EB; font-family:Inter,sans-serif;
                   display:flex; align-items:center; justify-content:center; height:100vh;
                   flex-direction:column; gap:16px; margin:0; }
            h1 { font-weight:500; font-size:18px; }
            p { color:#9EA1A9; font-size:13px; max-width:480px; text-align:center; line-height:1.6;}
            code { background:#17181A; padding:2px 6px; border-radius:4px; font-family:monospace; }
            </style></head><body>
            <h1>UI not found</h1>
            <p>Could not locate <code>clew/web/index.html</code>.
            Reinstall Clew or run from the project root.</p>
            </body></html>""",
            QUrl("about:blank"),
        )

    # ── Load finished ───────────────────────────────────────────

    def _on_update_found(self, data):
        """Called when AutoUpdater finds a newer version on GitHub."""
        from PySide6.QtWidgets import QMessageBox
        logger.info("[main_window] update available: %s → %s", data.get("current"), data.get("latest"))
        reply = QMessageBox.information(
            self,
            "Clew — Update Available",
            f"A new version of Clew is available!\n\n"
            f"  Current:  {data.get('current', '?')}\n"
            f"  Latest:   {data.get('latest', '?')}\n\n"
            f"{data.get('body', '')[:200]}",
            QMessageBox.Open | QMessageBox.Ignore,
            QMessageBox.Ignore,
        )
        if reply == QMessageBox.Open:
            import webbrowser
            webbrowser.open(data.get("url", "https://github.com/user/clew/releases"))

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            logger.warning("[main_window] page load failed")
            return
        # Inject the QWebChannel bootstrap so the HTML can talk to our bridge
        self._eval_js(_BOOTSTRAP_JS)
        # Push initial status (includes API port and platform id)
        QTimer.singleShot(150, self._push_initial_state)

    def _push_initial_state(self) -> None:
        """Tell the frontend what providers, project, API port, and platform are configured."""
        status = self.bridge.get_status()
        status['api_port'] = self._api_server.port
        status['api_base'] = self._api_server.base_url
        # v1.0.5-security: expose the per-process bearer token so the frontend
        # can send `Authorization: Bearer <token>` on mutating HTTP requests
        # (CSRF-to-localhost defense, BUGS_REPORT C-API-1).
        status['api_token'] = self._api_server.auth_token
        status['platform'] = _platform_id()
        # Use json.dumps to avoid Python True/False/None leaking into JS
        safe_json = json.dumps(status, default=str)
        self._eval_js(f"window.__clewReady && window.__clewReady({safe_json});")

    def _eval_js(self, code: str) -> None:
        try:
            self.view.page().runJavaScript(code)
        except Exception as e:
            logger.warning(f"[main_window] JS eval failed: {e}")

    # ── Project dialog ──────────────────────────────────────────

    def _open_project_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Project Folder")
        if folder:
            result = self.bridge.open_project(folder)
            if result.get("ok"):
                self._eval_js(f"window.__clewProjectOpened && window.__clewProjectOpened({json.dumps(result, default=str)});")
            else:
                QMessageBox.warning(self, "Open Project", result.get("error", "Failed to open project"))

    # ── Cleanup ─────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Apply the transparent-titlebar Cocoa styling the first time the
        # window actually becomes visible — that's the earliest point at
        # which a real NSWindow is guaranteed to back this widget.
        if _is_macos() and not getattr(self, "_mac_style_applied", False):
            self._mac_style_applied = True
            self._apply_mac_titlebar_style()

    def _apply_mac_titlebar_style(self) -> None:
        """Make the native macOS titlebar transparent and let content extend
        underneath it (the "Cursor / VS Code" look): the traffic lights
        float over our own topbar/sidebar instead of sitting in a separate
        gray strip. Unlike Qt::FramelessWindowHint, this keeps the window a
        normal titled NSWindow under the hood, so macOS keeps drawing its
        native rounded corners and drop shadow too.

        Requires pyobjc (pyobjc-framework-Cocoa). If it isn't installed we
        just log a warning and keep the plain native titlebar — the app
        still works, it just won't get the transparent-titlebar look.
        """
        try:
            import objc
            from AppKit import NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden

            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = ns_view.window()
            if ns_window is None:
                logger.warning("[main_window] no NSWindow yet — skipping mac titlebar styling")
                return
            ns_window.setTitlebarAppearsTransparent_(True)
            ns_window.setTitleVisibility_(NSWindowTitleHidden)
            ns_window.setStyleMask_(ns_window.styleMask() | NSWindowStyleMaskFullSizeContentView)
            # Let our own JS-driven startSystemMove() keep doing the dragging
            # (see start_system_move below) — we deliberately do NOT set
            # movableByWindowBackground here to avoid the window moving on
            # every stray click inside the web content.
        except ImportError:
            logger.warning(
                "[main_window] pyobjc not installed — macOS will show the "
                "normal titlebar instead of the transparent Cursor-style one. "
                "Install with: pip install pyobjc-framework-Cocoa"
            )
        except Exception as e:
            logger.warning(f"[main_window] failed to apply mac titlebar style: {e}")

    def closeEvent(self, event) -> None:
        # Persist window position/size/maximized for next launch
        self._save_geometry()
        try:
            self.bridge.cleanup()
        except Exception as e:
            logger.warning(f"[main_window] cleanup error: {e}")
        try:
            self._api_server.stop()
        except Exception as e:
            logger.warning(f"[main_window] api_server stop error: {e}")
        # v1.1.0: stop all MCP server subprocesses
        try:
            from .mcp_manager import get_mcp_manager
            get_mcp_manager().stop_all()
        except Exception as e:
            logger.warning(f"[main_window] MCP stop_all error: {e}")
        super().closeEvent(event)


# ── Web page subclass — devtools + transparent bg ──────────────────

class _ClewWebPage(QWebEnginePage):
    """Custom page: opens devtools on F12, logs console messages."""

    def __init__(self, profile, parent):
        super().__init__(profile, parent)

    def createWindow(self, _type):
        # Open links in the same view (no popup windows)
        return self

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        levels = {0: "DEBUG", 1: "INFO", 2: "WARNING", 3: "ERROR", 4: "LOG"}
        log_level = {
            0: logging.DEBUG,
            1: logging.INFO,
            2: logging.WARNING,
            3: logging.ERROR,
        }.get(level, logging.DEBUG)
        logger.log(
            log_level,
            f"[js:{levels.get(level, 'LOG')}] {message} ({source_id}:{line_number})",
        )


# ── JS bootstrap injected after page load ──────────────────────────

_BOOTSTRAP_JS = """
(function () {
    if (window.__clewBridgeReady) return;
    window.__clewBridgeReady = true;

    new QWebChannel(qt.webChannelTransport, function (channel) {
        window.bridge = channel.objects.bridge;

        // ── v1.1.3-fix: diagnostic — confirm signals reach JS ──
        // If these log lines appear when the agent runs, QWebChannel
        // signal delivery works and the problem is in app.js.
        // If they do NOT appear, the QWebChannel transport is broken
        // for push-based signals (RPC calls work fine).
        try {
            window.bridge.agent_step_signal.connect(function(step) {
                console.log('[clew:bootstrap] agent_step_signal RECEIVED: type=' + (step && step.type) + ' keys=' + (step ? Object.keys(step).join(',') : ''));
            });
            window.bridge.agent_final.connect(function(result) {
                console.log('[clew:bootstrap] agent_final RECEIVED: iterations=' + (result && result.iterations) + ' text_len=' + ((result && result.text) || '').length);
            });
        } catch (diagErr) {
            console.warn('[clew:bootstrap] diagnostic signal wiring failed:', diagErr.message);
        }

        // Mark ready — the frontend connects to bridge signals directly
        // via bridge.signalName.connect() in its own clew:bridge_ready handler.
        window.__clewBridgeConnected = true;
        window.dispatchEvent(new CustomEvent('clew:bridge_ready'));
    });
})();
"""