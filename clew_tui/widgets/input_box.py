"""input_box.py — bottom input line with command history and slash trigger.

Up/Down cycle through previously submitted prompts, like a shell.
Typing "/" at the start opens inline suggestions above the input.
Enter is handled EXPLICITLY here (bypassing Input.Submitted which
doesn't fire in older Textual versions).
"""

from __future__ import annotations

from typing import Any, List

from textual import events
from textual.widgets import Input


class InputBox(Input):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            placeholder=" Ask clew... (Enter=send, / for commands) ",
            **kwargs,
        )
        self._history: List[str] = []
        self._hist_index: int | None = None
        self._suggestions_visible: bool = False

    def remember(self, text: str) -> None:
        text = text.strip()
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._hist_index = None

    def set_suggestions_visible(self, visible: bool) -> None:
        """Called by the app to tell us whether the suggestion bar is active."""
        self._suggestions_visible = visible

    async def _on_key(self, event: events.Key) -> None:
        # ---- Enter: submit the prompt directly ----
        if event.key == "enter":
            if self._suggestions_visible:
                # If suggestions are visible, Enter should not submit; let suggestion
                # handling decide what to do (e.g., Tab to select).
                event.prevent_default()
                event.stop()
                return
            # No suggestions: submit the input.
            value = self.value.strip()
            if value:
                self.value = ""
                # Call the app's submission handler directly.
                # We do NOT rely on Input.Submitted (broken in old Textual).
                app = self.app
                if hasattr(app, "_submit_prompt"):
                    app._submit_prompt(value)
            event.prevent_default()
            event.stop()
            return

        # ---- Up/Down: navigate suggestions or history ----
        if event.key == "up":
            if self._suggestions_visible:
                app = self.app
                if hasattr(app, "_move_suggestion_up"):
                    app._move_suggestion_up()
            else:
                self._history_prev()
            event.stop()
            event.prevent_default()
            return
        if event.key == "down":
            if self._suggestions_visible:
                app = self.app
                if hasattr(app, "_move_suggestion_down"):
                    app._move_suggestion_down()
            else:
                self._history_next()
            event.stop()
            event.prevent_default()
            return

        # Tab — select highlighted suggestion
        if event.key == "tab":
            if self._suggestions_visible:
                app = self.app
                if hasattr(app, "_select_suggestion"):
                    app._select_suggestion()
                event.stop()
                event.prevent_default()
                return

        # Escape — hide suggestions
        if event.key == "escape":
            if self._suggestions_visible:
                app = self.app
                if hasattr(app, "_hide_suggestions"):
                    app._hide_suggestions()
                event.stop()
                event.prevent_default()
                return

        # ALL OTHER KEYS — pass to base Input normally (typing, backspace, etc.)
        await super()._on_key(event)

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._hist_index is None:
            self._hist_index = len(self._history) - 1
        else:
            self._hist_index = max(0, self._hist_index - 1)
        self.value = self._history[self._hist_index]
        self.cursor_position = len(self.value)

    def _history_next(self) -> None:
        if not self._history or self._hist_index is None:
            return
        if self._hist_index >= len(self._history) - 1:
            self._hist_index = None
            self.value = ""
            return
        self._hist_index += 1
        self.value = self._history[self._hist_index]
        self.cursor_position = len(self.value)
