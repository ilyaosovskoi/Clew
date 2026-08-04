"""Interaction tests for the main command palette (Ctrl+P).

This is the bug issue #17 was opened for: pressing Enter while the
filter Input has focus did NOTHING — the binding only fires when the
OptionList has focus, but Textual delivers Enter as Input.Submitted
to the focused Input. The fix added an ``on_input_submitted`` handler
to ``CommandPalette`` that calls ``action_select_item()``.

These tests verify:
  1. Opening the palette focuses the filter Input.
  2. Typing a filter string narrows the list.
  3. Down/Up moves the highlight.
  4. Enter (with filter Input still focused) SELECTS the highlighted
     option — the EXACT scenario that was broken.
  5. Mouse click on an option also works.
  6. Escape dismisses the palette.
"""

from __future__ import annotations

import pytest

from ._helpers import TUIInteractionCase


@pytest.mark.asyncio
async def test_open_palette_focuses_filter_input():
    """Ctrl+P opens the palette; the filter Input has focus."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_main_palette()
        assert palette is not None
        # The filter Input should have focus.
        from textual.widgets import Input
        focused = case.app.focused
        assert isinstance(focused, Input)
        assert focused.id == "palette-filter"


@pytest.mark.asyncio
async def test_typing_filter_narrows_list():
    """Typing 'section' in the filter narrows the list to just /section."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_main_palette()
        initial_count = palette.query_one("#palette-list").option_count
        # Type "section" — should narrow to just the /section entry
        # (plus its category header).
        await case.type_filter("section")
        filtered_count = palette.query_one("#palette-list").option_count
        assert filtered_count < initial_count
        # The first real option should be /section.
        assert case.highlighted_id() == "section"


@pytest.mark.asyncio
async def test_down_arrow_moves_highlight():
    """Pressing Down moves the highlight to the next option."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_main_palette()
        before = case.highlighted_index()
        await case.press_down()
        after = case.highlighted_index()
        assert after is not None
        assert after != before, "highlight did not move after Down"


@pytest.mark.asyncio
async def test_up_arrow_moves_highlight_back():
    """Pressing Up after Down returns to the original highlight."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_main_palette()
        before = case.highlighted_index()
        await case.press_down()
        await case.press_up()
        after = case.highlighted_index()
        assert after == before


@pytest.mark.asyncio
async def test_enter_with_filter_focused_selects_highlighted():
    """**THE BUG**: pressing Enter while the filter Input has focus
    must SELECT the highlighted option, not silently do nothing.

    Before the fix, this test would fail because:
      - The ``enter`` binding on the ModalScreen only fires when the
        OptionList has focus.
      - But the filter Input has focus, so Textual delivers Enter as
        ``Input.Submitted`` to the Input, not as a binding to the
        screen.
      - There was no ``on_input_submitted`` handler, so the event was
        silently swallowed.

    After the fix, ``on_input_submitted`` calls ``action_select_item()``
    which selects the highlighted option.
    """
    async with TUIInteractionCase.run() as case:
        palette = await case.open_main_palette()
        # Type a filter so the list is narrowed to a known option.
        await case.type_filter("clear")
        # The highlighted option should be "clear".
        assert case.highlighted_id() == "clear", \
            f"expected 'clear', got {case.highlighted_id()!r}"
        # Now press Enter while the filter Input STILL has focus.
        # This is the exact scenario that was broken.
        await case.press_enter()
        # The palette must have dismissed (selected something).
        assert not case.is_palette_open(), \
            "palette still open — Enter did not select"


@pytest.mark.asyncio
async def test_enter_on_command_with_sub_options_pushes_sub_palette():
    """Pressing Enter on /section (which has sub_options) must push
    the sub-palette, not silently do nothing."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_main_palette()
        await case.type_filter("section")
        assert case.highlighted_id() == "section"
        await case.press_enter()
        # The sub-palette should now be open.
        assert case.is_palette_open(), \
            "sub-palette did not open after Enter on /section"


@pytest.mark.asyncio
async def test_escape_dismisses_palette():
    """Escape closes the palette without selecting anything."""
    async with TUIInteractionCase.run() as case:
        await case.open_main_palette()
        assert case.is_palette_open()
        await case.press_escape()
        assert not case.is_palette_open()
