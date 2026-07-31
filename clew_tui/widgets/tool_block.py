"""tool_block.py — colored Unicode tool blocks with streaming.

Displays tool call/result blocks with Unicode single-line borders
(┌─┐│└─┘) and colored borders per tool type:
  - Hot pink (#fd5db1) for bash/execute tools
  - Lavender (#b1b9f9) for permission dialogs
  - Terracotta (#d77757) for thinking

Header: ToolName · path (bold + border color)
Content: syntax-highlighted output
Live streaming: append output lines as they arrive

v2.1.0 (Loop 3): TUI Visual Overhaul.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from rich.syntax import Syntax
from rich.text import Text
from textual.widgets import Static


# ── Border colors by tool type ───────────────────────────────────────
_TOOL_BORDER_COLORS = {
    "execute_command": "#fd5db1",
    "run_code": "#fd5db1",
    "bash": "#fd5db1",
    "write_file": "#fd5db1",
    "str_replace": "#fd5db1",
    "read_file": "#b1b9f9",
    "self_verify": "#4eba65",
    "default": "#fd5db1",
}

# ── Unicode box drawing characters ──────────────────────────────────
_TL = "┌"
_TR = "┐"
_BL = "└"
_BR = "┘"
_H = "─"
_V = "│"


class ToolBlock(Static):
    """A styled tool output block with Unicode borders.

    Features:
      - Colored border per tool type (hot pink default)
      - Header with tool name and optional path
      - Syntax-highlighted content
      - Live streaming support (append output lines)
      - Collapsible (click header to toggle)
    """

    def __init__(
        self,
        tool_name: str = "",
        tool_path: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._tool_path = tool_path
        self._content = content
        self._collapsed = False
        self._border_color = _TOOL_BORDER_COLORS.get(
            tool_name, _TOOL_BORDER_COLORS["default"]
        )

    def set_tool_info(self, tool_name: str, tool_path: str = "") -> None:
        """Set the tool name and optional path."""
        self._tool_name = tool_name
        self._tool_path = tool_path
        self._border_color = _TOOL_BORDER_COLORS.get(
            tool_name, _TOOL_BORDER_COLORS["default"]
        )
        self._render()

    def append_output(self, line: str) -> None:
        """Append a line of output (for streaming)."""
        if self._content:
            self._content += "\n" + line
        else:
            self._content = line
        self._render()

    def set_content(self, content: str) -> None:
        """Set the full content at once."""
        self._content = content
        self._render()

    def toggle_collapse(self) -> None:
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self._render()

    def _render(self) -> None:
        """Render the tool block with Unicode borders."""
        if not self._tool_name:
            self.update("")
            return

        # Build the header
        header = self._tool_name
        if self._tool_path:
            header = f"{self._tool_name} · {self._tool_path}"

        # Build the content
        if self._collapsed:
            body = ""
        else:
            body = self._content or ""

        # Render with Rich markup
        border = self._border_color
        header_markup = f"[bold {border}]{header}[/bold {border}]"

        if body:
            content_markup = f"[white]{body}[/white]"
            self.update(
                f"{header_markup}\n{content_markup}"
            )
        else:
            self.update(header_markup)

    def on_mount(self) -> None:
        self._render()

    def on_click(self) -> None:
        """Click to toggle collapse/expand."""
        self.toggle_collapse()
