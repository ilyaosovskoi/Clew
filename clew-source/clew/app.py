"""
Clew v1.0.1 — Application Entry Point.

Minimal: configures QApplication, loads the main window (which hosts
the HTML frontend via QWebEngineView), and runs the event loop.

All UI logic lives in clew/web/index.html; all backend logic is in
clew/web_bridge.py and clew/providers/.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtGui import QFontDatabase, QFont, QIcon, QPalette, QColor
from PySide6.QtWidgets import QApplication

from clew.main_window import ClewMainWindow
from clew.utils import setup_logging

logger = logging.getLogger(__name__)


def setup_macos_app() -> None:
    """Configure macOS-specific application settings."""
    QCoreApplication.setApplicationName("Clew")
    QCoreApplication.setOrganizationName("Clew")
    QCoreApplication.setApplicationVersion("1.0.1")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    os.environ["QT_MAC_WANTS_LAYER"] = "1"


def setup_fonts(app: QApplication) -> None:
    """Prefer Inter for UI, JetBrains Mono for code — fall back to system fonts."""
    families = QFontDatabase.families()
    ui_family = next(
        (f for f in ["Inter", ".SF NS", "SF Pro", "Helvetica Neue", "Arial"]
         if f in families),
        None,
    )
    if ui_family:
        app.setFont(QFont(ui_family, 13))

    mono_family = next(
        (f for f in ["JetBrains Mono", "SF Mono", "Menlo", "Monaco", "Courier New"]
         if f in families),
        None,
    )
    if mono_family:
        mono = QFont(mono_family, 12)
        # Apply to text widgets so any native fallback still looks right
        for cls in ("QPlainTextEdit", "QTextEdit", "QTextBrowser"):
            app.setFont(mono, cls)


def setup_dark_palette(app: QApplication) -> None:
    """Dark palette aligned with clew/web/index.html (#0B0B0D system)."""
    palette = QPalette()
    bg       = QColor("#0B0B0D")
    panel    = QColor("#111214")
    floating = QColor("#17181A")
    text     = QColor("#E6E7EB")
    text_sec = QColor("#9EA1A9")
    text_mut = QColor("#6D7078")
    accent   = QColor("#F4F4F5")

    palette.setColor(QPalette.ColorRole.Window,         bg)
    palette.setColor(QPalette.ColorRole.WindowText,     text)
    palette.setColor(QPalette.ColorRole.Base,           panel)
    palette.setColor(QPalette.ColorRole.AlternateBase,  floating)
    palette.setColor(QPalette.ColorRole.ToolTipBase,    floating)
    palette.setColor(QPalette.ColorRole.ToolTipText,    text)
    palette.setColor(QPalette.ColorRole.Text,           text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, text_mut)
    palette.setColor(QPalette.ColorRole.Button,         floating)
    palette.setColor(QPalette.ColorRole.ButtonText,     text)
    palette.setColor(QPalette.ColorRole.BrightText,     accent)
    palette.setColor(QPalette.ColorRole.Highlight,      accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0B0B0D"))
    palette.setColor(QPalette.ColorRole.Link,           accent)
    palette.setColor(QPalette.ColorRole.LinkVisited,    text_sec)

    palette.setColor(QPalette.ColorRole.Mid,   floating)
    palette.setColor(QPalette.ColorRole.Dark,  bg)
    palette.setColor(QPalette.ColorRole.Light, floating)
    palette.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0, 120))

    # Apply disabled-state palette entries so secondary/muted colors
    # actually render (previously `text_sec` and `text_mut` were
    # assigned but never used — pyflakes flagged them as dead code).
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,     text_mut)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,           text_mut)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,     text_sec)

    app.setPalette(palette)


def _resolve_logo_path() -> Path:
    package_root = Path(__file__).resolve().parent
    for p in [
        package_root / "assets" / "app_icon_1024.png",
        package_root / "assets" / "logo_small.png",
        package_root / "assets" / "logo.png",
    ]:
        if p.exists():
            return p
    return package_root / "assets" / "logo.png"


def load_application_logo(app: QApplication) -> None:
    logo_path = _resolve_logo_path()
    if logo_path.exists():
        try:
            icon = QIcon(str(logo_path))
            if not icon.isNull():
                app.setWindowIcon(icon)
        except Exception as e:
            logger.warning(f"Failed to load logo: {e}")


def main() -> None:
    """Clew v1.0.1 entry point."""
    setup_logging()
    setup_macos_app()

    # Parse simple CLI flags
    project_root: Optional[str] = None
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg in ("-p", "--project") and i + 1 < len(sys.argv):
            project_root = sys.argv[i + 1]

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    setup_fonts(app)
    setup_dark_palette(app)
    load_application_logo(app)

    window = ClewMainWindow(project_root=project_root)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
