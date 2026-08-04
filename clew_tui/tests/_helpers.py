"""TUIInteractionCase — a small helper for Pilot-driven TUI tests.

Goal: a new command's palette test should be a few lines, not a new
Pilot setup each time. The helper:

1. Builds a ClewTUIApp with a FakeClewBridge (no real LLM creds).
2. Runs the app in test mode (``App.run_test()``) and yields the Pilot.
3. Provides convenience methods for the common interactions every
   palette test needs: open the main palette, open a sub-palette,
   type a filter string, press up/down, press enter, click an option.

Example:

    from clew_tui.tests._helpers import TUIInteractionCase

    async with TUIInteractionCase.run() as case:
        await case.open_sub_palette("section")
        await case.type_filter("office")
        await case.press_enter()
        # Assert the bridge's set_section was called with "office".
        assert case.bridge.calls_to("set_section")[-1].args == ("office",)

Usage:

    @pytest.mark.asyncio
    async def test_section_palette_enter_selects():
        async with TUIInteractionCase.run() as case:
            ...

Or, equivalently, use the ``interaction_case`` fixture which is just
``TUIInteractionCase.run()`` as an async context manager.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from textual.app import App
from textual.widgets import Input, OptionList

from clew_tui.app import ClewTUIApp
from clew_tui.widgets.command_palette import CommandPalette

from ._fake_bridge import FakeClewBridge


class TUIInteractionCase:
    """A running TUI app + Pilot, with convenience methods."""

    def __init__(self, app: ClewTUIApp, pilot) -> None:
        self.app = app
        self.pilot = pilot
        # Convenience: the fake bridge records every call.
        self.bridge: FakeClewBridge = app.bridge  # type: ignore[assignment]

    # ── Classmethod entry point ───────────────────────────────────

    @classmethod
    @asynccontextmanager
    async def run(
        cls,
        *,
        section: str = "general",
        workspace: Optional[str] = None,
    ) -> AsyncIterator["TUIInteractionCase"]:
        """Build a ClewTUIApp with a FakeClewBridge, run it in test
        mode, yield the case, then shut down cleanly."""
        bridge = FakeClewBridge(workspace=workspace, section=section)
        app = ClewTUIApp(bridge=bridge)
        async with app.run_test() as pilot:
            # Let the app finish mounting.
            await pilot.pause()
            yield cls(app, pilot)

    # ── Open palettes ─────────────────────────────────────────────

    async def open_main_palette(self) -> CommandPalette:
        """Open the clew command palette directly via the app's API.

        We don't press Ctrl+P because Textual 8.x ships a built-in
        ``ctrl+p`` binding that opens *its own* command palette
        (``textual.command.CommandPalette``) — which shadows the
        clew_tui one when both bindings are active. The clew_tui
        palette is the one we want to test, so we open it directly
        via ``app.open_command_palette()`` (same path the binding
        would take if it weren't shadowed).
        """
        self.app.open_command_palette()
        await self.pilot.pause()
        screen = self.app.screen
        if isinstance(screen, CommandPalette):
            return screen
        return screen.query_one(CommandPalette)

    async def open_sub_palette(self, cmd_id: str) -> Optional[CommandPalette]:
        """Open a sub-palette for the given command id (e.g. "section",
        "model", "guardian"). Uses the app's internal API to push the
        sub-palette directly — equivalent to selecting the command from
        the main palette, but more reliable in tests (doesn't depend
        on filter typing to find the right entry).

        Returns the CommandPalette that's now the active screen, or
        ``None`` if the command doesn't have any sub-options to show
        (e.g. ``/capabilities`` when the catalog is empty — the app
        prints a "no capabilities available" message instead of
        pushing a palette).
        """
        self.app._open_sub_palette_for_cmd(cmd_id)
        await self.pilot.pause()
        screen = self.app.screen
        if isinstance(screen, CommandPalette):
            return screen
        # Some commands (capabilities, handoff) print a chat-log
        # message instead of pushing a palette when there's nothing
        # to show. Return None so the caller can short-circuit.
        try:
            return screen.query_one(CommandPalette)
        except Exception:
            return None

    # ── Filter input ──────────────────────────────────────────────

    def filter_input(self) -> Input:
        """Return the palette's filter Input widget."""
        return self.app.screen.query_one("#palette-filter", Input)

    def option_list(self) -> OptionList:
        """Return the palette's OptionList widget."""
        return self.app.screen.query_one("#palette-list", OptionList)

    async def type_filter(self, text: str) -> None:
        """Simulate typing into the filter Input (real key presses, not
        setting .value directly)."""
        # First focus the filter input — it should already be focused
        # on palette open, but be defensive.
        self.filter_input().focus()
        await self.pilot.pause()
        # Clear any existing value, then type.
        self.filter_input().value = ""
        await self.pilot.pause()
        for ch in text:
            await self.pilot.press(ch)
        await self.pilot.pause()

    async def set_filter_value(self, text: str) -> None:
        """Set the filter Input's value directly (no key presses).

        Use this when you want to test the on_input_changed handler
        in isolation, without also testing the keyboard path. For
        realistic interaction tests, prefer ``type_filter``.
        """
        self.filter_input().value = text
        await self.pilot.pause()

    # ── Navigation ────────────────────────────────────────────────

    async def press_down(self, n: int = 1) -> None:
        for _ in range(n):
            await self.pilot.press("down")
        await self.pilot.pause()

    async def press_up(self, n: int = 1) -> None:
        for _ in range(n):
            await self.pilot.press("up")
        await self.pilot.pause()

    async def press_enter(self) -> None:
        """Press Enter while the filter Input still has focus — the
        exact scenario that was broken before issue #17's fix."""
        # Make sure the filter Input has focus (not the OptionList).
        self.filter_input().focus()
        await self.pilot.pause()
        await self.pilot.press("enter")
        await self.pilot.pause()

    async def press_escape(self) -> None:
        await self.pilot.press("escape")
        await self.pilot.pause()

    # ── Inspection ────────────────────────────────────────────────

    def highlighted_index(self) -> Optional[int]:
        """Return the index of the currently-highlighted option in the
        palette's OptionList, or None if nothing is highlighted."""
        return self.option_list().highlighted

    def highlighted_id(self) -> Optional[str]:
        """Return the command id (or sub-option id) of the currently-
        highlighted option, or None."""
        from clew_tui.widgets.command_palette import CommandPalette
        screen = self.app.screen
        palette = screen if isinstance(screen, CommandPalette) else screen.query_one(CommandPalette)
        idx = self.option_list().highlighted
        if idx is None or idx < 0 or idx >= len(palette._option_ids):
            return None
        return palette._option_ids[idx]

    def is_palette_open(self) -> bool:
        """True if a CommandPalette is currently the active screen."""
        from clew_tui.widgets.command_palette import CommandPalette as _CP
        return isinstance(self.app.screen, _CP)

    # ── Chat input ────────────────────────────────────────────────

    async def type_input(self, text: str) -> None:
        """Type into the main InputBox (the bottom input line)."""
        from clew_tui.widgets.input_box import InputBox
        box = self.app.query_one(InputBox)
        box.focus()
        await self.pilot.pause()
        box.value = ""
        for ch in text:
            await self.pilot.press(ch)
        await self.pilot.pause()

    async def submit_input(self) -> None:
        """Submit the current InputBox value.

        If inline suggestions are visible (they appear when "/" is
        typed), Enter is intercepted by the InputBox key handler and
        does NOT submit. We work around this by hiding suggestions
        first, then calling ``app._submit_prompt(value)`` directly —
        the same path the InputBox key handler would have taken.
        """
        from clew_tui.widgets.input_box import InputBox
        box = self.app.query_one(InputBox)
        value = box.value
        # Hide suggestions if visible (otherwise Enter is intercepted).
        if self.app._suggestions_active:
            self.app._hide_suggestions()
            await self.pilot.pause()
        # Clear the input box (matches what InputBox._on_key does on submit).
        box.value = ""
        # Call _submit_prompt directly — bypasses the InputBox key
        # handler so suggestions can't intercept.
        self.app._submit_prompt(value)
        await self.pilot.pause()

    # ── Status bar ────────────────────────────────────────────────

    def status_bar_text(self) -> str:
        """Return the current status bar text (lowercased).

        StatusBar is a Static widget — calling ``.render()`` returns
        a ``Content`` object whose ``plain`` attribute holds the
        plain-text version of the rendered content.
        """
        from clew_tui.widgets.status_bar import StatusBar
        try:
            sb = self.app.query_one(StatusBar)
            content = sb.render()
            # Content.plain gives the plain-text version.
            text = getattr(content, "plain", None)
            if text is None:
                text = str(content)
            return text.lower()
        except Exception:
            return ""

    # ── Chat log ──────────────────────────────────────────────────

    def chat_log_text(self) -> str:
        """Return all text currently in the ChatLog (lowercased)."""
        from clew_tui.widgets.chat_log import ChatLog
        try:
            cl = self.app.query_one(ChatLog)
            return self._rich_log_text(cl).lower()
        except Exception:
            return ""

    def _rich_log_text(self, widget) -> str:
        """Extract plain text from a RichLog widget's internal lines.

        RichLog stores its content as a list of ``Strip`` objects in
        ``widget.lines``. Each Strip contains a list of Segment
        objects, each with a ``.text`` attribute. We concatenate all
        of them across all lines to get a flat text blob.
        """
        parts = []
        lines = getattr(widget, "lines", None) or []
        for strip in lines:
            # Strip.text is a property that returns the concatenated
            # text of all its segments.
            try:
                # Strips expose .text since Textual 0.x — but the
                # attribute name has shifted over versions. Try the
                # most common ones.
                text = getattr(strip, "text", None)
                if text is None:
                    # Fall back to iterating segments.
                    seg_text = []
                    for seg in getattr(strip, "segments", []):
                        seg_text.append(getattr(seg, "text", ""))
                    text = "".join(seg_text)
                parts.append(text)
            except Exception:
                continue
        return "\n".join(parts)
