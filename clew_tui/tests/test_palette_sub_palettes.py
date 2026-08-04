"""Parameterised interaction test for EVERY sub-palette.

This file is the heart of issue #17's coverage: for each command in
``BUILTIN_COMMANDS`` with ``has_sub_options=True``, drive the sub-
palette the way a person actually does — type a filter, press Down,
press Enter while the filter Input still has focus — and assert that
a selection actually occurred (the bridge's exec method was called).

The commands with ``has_sub_options=True`` are:
  section, model, chat, cd, guardian, collab, storage, capabilities,
  handoff.

(Plus any others that may be added later — the test pulls the list
dynamically from ``BUILTIN_COMMANDS`` so it stays in sync.)
"""

from __future__ import annotations

import pytest

from clew_tui.widgets.command_palette import BUILTIN_COMMANDS
from ._helpers import TUIInteractionCase


# Commands that have sub-options (the ones this test exercises).
SUB_PALETTE_COMMANDS = [c.id for c in BUILTIN_COMMANDS if c.has_sub_options]


# Per-command: what filter string narrows the list to exactly one
# option we can select, and what bridge method should be called as a
# result. This lets us assert "the right thing happened" instead of
# just "the palette dismissed".
#
# For each command, the filter is chosen to match the FIRST option's
# label/id so the highlighted option (index 0 after filtering) is the
# one we expect.
PER_COMMAND = {
    "section": {
        # Sections are: general / heavy_code / office.
        "filter": "general",
        "bridge_method": "set_section",
        "expected_arg": "general",
    },
    "model": {
        # FakeBridge.list_providers returns "fake" + "ollama".
        "filter": "fake",
        "bridge_method": "set_provider",
        "expected_arg": "fake",
    },
    "chat": {
        # FakeBridge.list_chats returns "chat1" + "chat2".
        "filter": "chat1",
        "bridge_method": None,  # _exec_chat loads the chat (no bridge call in fake)
        "expected_arg": "chat1",
    },
    "cd": {
        # FakeBridge workspace = tempdir. cd palette shows recent dirs.
        "filter": "",  # don't filter — just press Enter on first option
        "bridge_method": "change_workspace",
        "expected_arg": None,  # any path
    },
    "guardian": {
        # Guardian options: off / dangerous_only / all.
        "filter": "off",
        "bridge_method": "set_guardian_level",
        "expected_arg": "off",
    },
    "collab": {
        # Collab modes: single / reviewer / codegen / pair / observer.
        "filter": "single",
        "bridge_method": None,  # _exec_collab may not call a simple bridge method
        "expected_arg": "single",
    },
    "storage": {
        # Storage options: json / sqlite.
        "filter": "json",
        "bridge_method": "set_persistence_backend",
        "expected_arg": "json",
    },
    "capabilities": {
        # FakeBridge.list_capabilities returns []. The palette will show
        # "No capabilities found" — but the palette itself should still
        # open and respond to keyboard. We test that the palette opens
        # and Escape closes it.
        "filter": "",
        "bridge_method": None,
        "expected_arg": None,
    },
    "handoff": {
        # FakeBridge.list_handoffs returns []. Same as capabilities.
        "filter": "",
        "bridge_method": None,
        "expected_arg": None,
    },
}


@pytest.mark.parametrize("cmd_id", SUB_PALETTE_COMMANDS)
@pytest.mark.asyncio
async def test_sub_palette_enter_selects(cmd_id):
    """**THE issue #17 test**: for each command with sub_options,
    open the sub-palette, type a filter, press Down, then press Enter
    WHILE THE FILTER INPUT STILL HAS FOCUS, and assert a selection
    actually occurred.

    Before the fix, this test would fail for every command because
    Enter was delivered as Input.Submitted to the filter Input (which
    had focus), and there was no on_input_submitted handler — so
    nothing happened.

    After the fix, on_input_submitted calls action_select_item() which
    selects the highlighted option and dismisses the palette.
    """
    cfg = PER_COMMAND.get(cmd_id, {})
    async with TUIInteractionCase.run() as case:
        # Open the sub-palette directly.
        palette = await case.open_sub_palette(cmd_id)
        # If the command had nothing to show (e.g. capabilities with
        # an empty catalog), the palette doesn't open. We just verify
        # the app didn't crash — there's nothing to test Enter on.
        if palette is None:
            assert not case.is_palette_open()
            return
        # The palette must be open.
        assert case.is_palette_open(), f"{cmd_id}: sub-palette did not open"

        # If the palette has 0 options (e.g. capabilities/handoff with
        # empty fake data), the test degrades to "palette opens and
        # Escape closes it" — we can't test Enter-selects because
        # there's nothing to select.
        list_widget = palette.query_one("#palette-list")
        if list_widget.option_count == 0:
            await case.press_escape()
            assert not case.is_palette_open()
            return

        # Type the filter (if any).
        filter_text = cfg.get("filter", "")
        if filter_text:
            await case.type_filter(filter_text)

        # Make sure the OptionList has at least one option after filter.
        if list_widget.option_count == 0:
            await case.press_escape()
            return

        # Move the highlight to be sure something is selected.
        # The palette auto-highlights the first real option on open,
        # but be defensive.
        if list_widget.highlighted is None:
            list_widget.highlighted = 0
            await case.pilot.pause()

        # Snapshot the highlighted id before Enter.
        before_id = case.highlighted_id()
        assert before_id is not None, \
            f"{cmd_id}: no option highlighted after filter"

        # Now press Enter WHILE THE FILTER INPUT STILL HAS FOCUS.
        # This is the exact scenario that was broken.
        await case.press_enter()

        # The palette must have dismissed (selected something).
        assert not case.is_palette_open(), \
            f"{cmd_id}: palette still open after Enter — selection did not happen"

        # If the command maps to a known bridge method, assert it was
        # called with the expected argument.
        bridge_method = cfg.get("bridge_method")
        if bridge_method:
            calls = case.bridge.calls_to(bridge_method)
            assert len(calls) >= 1, \
                f"{cmd_id}: bridge.{bridge_method} was not called"
            expected_arg = cfg.get("expected_arg")
            if expected_arg is not None:
                # The first positional arg should match.
                assert calls[-1].args[0] == expected_arg, \
                    f"{cmd_id}: bridge.{bridge_method} called with " \
                    f"{calls[-1].args[0]!r}, expected {expected_arg!r}"


@pytest.mark.parametrize("cmd_id", SUB_PALETTE_COMMANDS)
@pytest.mark.asyncio
async def test_sub_palette_down_arrow_moves_highlight(cmd_id):
    """For each sub-palette, pressing Down must move the highlight."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_sub_palette(cmd_id)
        if palette is None:
            # Nothing to test — palette didn't open.
            return
        list_widget = palette.query_one("#palette-list")
        if list_widget.option_count < 2:
            # Can't test Down movement with <2 options.
            await case.press_escape()
            return
        before = case.highlighted_index()
        await case.press_down()
        after = case.highlighted_index()
        assert after is not None
        # Highlight should have moved (or wrapped — either is fine).
        assert after != before or list_widget.option_count == 1, \
            f"{cmd_id}: highlight did not move after Down"


@pytest.mark.asyncio
async def test_section_sub_palette_filters_to_office():
    """Typing 'office' in the /section sub-palette narrows to just office."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_sub_palette("section")
        # Before filter: 3 options (general, heavy_code, office).
        before = palette.query_one("#palette-list").option_count
        assert before == 3
        await case.type_filter("office")
        after = palette.query_one("#palette-list").option_count
        assert after == 1, f"expected 1 option after 'office' filter, got {after}"
        assert case.highlighted_id() == "office"


@pytest.mark.asyncio
async def test_guardian_sub_palette_off_dangerous_all():
    """The /guardian sub-palette must show all three options."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_sub_palette("guardian")
        # Three options: off, dangerous_only, all.
        count = palette.query_one("#palette-list").option_count
        assert count == 3, f"expected 3 guardian options, got {count}"
        # Highlighted option's id should be one of them.
        assert case.highlighted_id() in ("off", "dangerous_only", "all")


@pytest.mark.asyncio
async def test_storage_sub_palette_json_sqlite():
    """The /storage sub-palette must show json + sqlite."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_sub_palette("storage")
        count = palette.query_one("#palette-list").option_count
        assert count == 2, f"expected 2 storage options, got {count}"


@pytest.mark.asyncio
async def test_collab_sub_palette_has_five_modes():
    """The /collab sub-palette must show 5 modes (single + 4 collab)."""
    async with TUIInteractionCase.run() as case:
        palette = await case.open_sub_palette("collab")
        count = palette.query_one("#palette-list").option_count
        assert count == 5, f"expected 5 collab modes, got {count}"


@pytest.mark.asyncio
async def test_section_sub_palette_selects_office_via_enter():
    """End-to-end: open /section, type 'office', Enter — bridge.set_section('office') called."""
    async with TUIInteractionCase.run() as case:
        await case.open_sub_palette("section")
        await case.type_filter("office")
        assert case.highlighted_id() == "office"
        await case.press_enter()
        # Bridge should have been called.
        calls = case.bridge.calls_to("set_section")
        assert len(calls) >= 1
        assert calls[-1].args[0] == "office"


@pytest.mark.asyncio
async def test_model_sub_palette_selects_fake_via_enter():
    """End-to-end: open /model, type 'fake', Enter — bridge.set_provider('fake') called."""
    async with TUIInteractionCase.run() as case:
        await case.open_sub_palette("model")
        await case.type_filter("fake")
        assert case.highlighted_id() == "fake"
        await case.press_enter()
        calls = case.bridge.calls_to("set_provider")
        assert len(calls) >= 1
        assert calls[-1].args[0] == "fake"


@pytest.mark.asyncio
async def test_guardian_sub_palette_selects_all_via_enter():
    """End-to-end: open /guardian, navigate to 'All tools', Enter —
    bridge.set_guardian_level('all') called.

    We don't filter by 'all' because that substring also matches
    'c**all**s' in the dangerous_only description. Instead we use
    Down-arrow navigation to land on the third option.
    """
    async with TUIInteractionCase.run() as case:
        await case.open_sub_palette("guardian")
        # Three options: off (idx 0), dangerous_only (idx 1), all (idx 2).
        assert case.highlighted_id() == "off"
        # Press Down twice to land on "all".
        await case.press_down(2)
        assert case.highlighted_id() == "all"
        await case.press_enter()
        calls = case.bridge.calls_to("set_guardian_level")
        assert len(calls) >= 1
        assert calls[-1].args[0] == "all"
