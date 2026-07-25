"""status_bar.py — status header with section indicator, provider info,
session tokens, keyboard shortcuts, and animated spinner.

The bar shows:
  LEFT: section badge + animated state indicator (idle/thinking/tool)
  CENTER: provider/model + tokens/cost
  RIGHT: keyboard shortcut hints

When thinking or running, a braille spinner animation plays.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from textual.widgets import Static


SECTION_LABELS = {
    "general": "General",
    "heavy_code": "Heavy Code",
    "office": "Office",
}

# Braille spinner frames for thinking/running animation
_SPINNER_FRAMES = [
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
]


class StatusBar(Static):
    """Top status bar with animated state indicators."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._provider: str = "?"
        self._model: str = "?"
        self._tokens: int = 0
        self._cost: float = 0.0
        self._section: str = "general"
        self._state: str = "idle"
        self._spinner_task: Optional[asyncio.Task] = None
        self._spinner_frame: int = 0
        # Set initial content so _render() never returns None
        self.update(
            " [cyan]General[/cyan]  "
            " [green]●[/green] idle  |  [b]?[/b]/[dim]?[/dim]  "
            " [dim]0 tok | $0.0000[/dim]\n"
            "[dim]Enter=send | /=cmds | Ctrl+C=stop | Ctrl+D=quit[/dim]"
        )

    def update_status(
        self,
        status: Dict[str, Any],
        state: str = "idle",
        section: str = "general",
    ) -> None:
        """Update all status fields and refresh the display."""
        self._provider = status.get("provider") or "?"
        self._model = status.get("model") or "?"
        self._tokens = int(status.get("tokens", 0) or 0)
        self._cost = float(status.get("cost", 0.0) or 0.0)
        self._section = section

        old_state = self._state
        self._state = state

        # Start/stop spinner animation based on state change
        if state in ("thinking", "running") and old_state not in ("thinking", "running"):
            self._start_spinner()
        elif state == "idle" and old_state in ("thinking", "running"):
            self._stop_spinner()

        self._refresh_display()

    def _refresh_display(self) -> None:
        """Rebuild and update the status bar text."""
        state = self._state

        # State indicator with spinner or static icon
        if state == "thinking":
            icon = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
            state_markup = f"[yellow]{icon} thinking[/yellow]"
        elif state == "running":
            icon = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
            state_markup = f"[yellow]{icon} tool running[/yellow]"
        else:
            state_markup = "[green]●[/green] idle"

        section_label = SECTION_LABELS.get(self._section, self._section.title())
        section_style = {
            "general": "cyan",
            "heavy_code": "magenta",
            "office": "yellow",
        }.get(self._section, "cyan")

        left = f" [{section_style}]{section_label}[/{section_style}] "
        center = f" {state_markup}  |  [b]{self._provider}[/b]/[dim]{self._model}[/dim] "
        right = f" [dim]{self._tokens:,} tok | ${self._cost:.4f}[/dim] "

        hints = "[dim]Enter=send | /=cmds | Ctrl+C=stop | Ctrl+D=quit[/dim]"

        self.update(f"{left}{center}{right}\n{hints}")

    # ---- spinner animation ----

    def _start_spinner(self) -> None:
        """Start the braille spinner animation loop."""
        self._stop_spinner()
        try:
            self._spinner_task = asyncio.create_task(self._spin_loop())
        except RuntimeError:
            pass  # no event loop yet

    def _stop_spinner(self) -> None:
        """Stop the spinner animation."""
        if self._spinner_task and not self._spinner_task.done():
            self._spinner_task.cancel()
        self._spinner_task = None
        self._spinner_frame = 0

    async def _spin_loop(self) -> None:
        """Animate the spinner — updates the status bar every 80ms."""
        try:
            while True:
                self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
                self._refresh_display()
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass

    def on_unmount(self) -> None:
        """Clean up spinner on widget removal."""
        self._stop_spinner()
