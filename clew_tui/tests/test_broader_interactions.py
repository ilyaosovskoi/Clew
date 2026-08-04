"""Broader Pilot-driven interaction tests beyond the command palette.

Covers:
  1. Sending a chat message and seeing it appear in the chat log.
  2. Triggering an approval modal and responding via keyboard (y/n).
  3. Triggering a Guardian modal and responding via keyboard (a/r/u).
  4. Switching themes via /theme (status bar visual change is hard
     to assert in a Pilot test, but we can verify the app's
     ``_dark_theme`` flag flips).
  5. The inline section-switch ({office} prefix) actually changes the
     visible mode indicator in the status bar.
"""

from __future__ import annotations

import pytest

from clew_tui.widgets.approval_modal import ApprovalModal, GuardianModal
from clew_tui.widgets.chat_log import ChatLog
from clew_tui.widgets.input_box import InputBox
from clew_tui.widgets.status_bar import StatusBar

from ._helpers import TUIInteractionCase


# ── 1. Send a chat message ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_chat_message_appears_in_log():
    """Typing a message and pressing Enter adds it to the ChatLog."""
    async with TUIInteractionCase.run() as case:
        # Type a message.
        await case.type_input("hello world")
        # Submit it.
        await case.submit_input()
        # The message must appear in the ChatLog.
        log_text = case.chat_log_text()
        assert "hello world" in log_text, \
            f"message not in chat log: {log_text!r}"


@pytest.mark.asyncio
async def test_send_chat_message_clears_input_box():
    """After submitting, the InputBox is cleared."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("test message")
        await case.submit_input()
        box = case.app.query_one(InputBox)
        assert box.value == "", f"input box not cleared: {box.value!r}"


@pytest.mark.asyncio
async def test_send_chat_message_calls_bridge_run_prompt():
    """Submitting a message calls bridge.run_prompt()."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("do something")
        await case.submit_input()
        # The bridge's run_prompt must have been called.
        calls = case.bridge.calls_to("run_prompt")
        assert len(calls) >= 1
        assert calls[-1].args[0] == "do something"


@pytest.mark.asyncio
async def test_slash_command_clear_runs_without_error():
    """Typing /clear and pressing Enter runs the clear command without
    crashing. (We don't assert the log is empty because the worker
    thread's response can render AFTER the clear, depending on timing.)"""
    async with TUIInteractionCase.run() as case:
        # Add a message first.
        await case.type_input("first message")
        await case.submit_input()
        await case.pilot.pause()
        # Now type /clear.
        await case.type_input("/clear")
        await case.submit_input()
        await case.pilot.pause()
        # The app must not have crashed. The chat log should contain
        # the "Chat log cleared." system message.
        log_text = case.chat_log_text()
        assert "chat log cleared" in log_text or "cleared" in log_text, \
            f"clear command did not run: {log_text!r}"


# ── 2. Approval modal ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approval_modal_y_approves():
    """Pushing an ApprovalModal and pressing 'y' approves the action."""
    async with TUIInteractionCase.run() as case:
        # Use the app's _show_confirm method — it pushes the
        # ApprovalModal with the right callback wired to
        # bridge.answer_confirmation().
        case.app._show_confirm({"action": "execute_command", "summary": "rm -rf /tmp"})
        await case.pilot.pause()
        # Press 'y' to approve.
        await case.pilot.press("y")
        await case.pilot.pause()
        # The bridge.answer_confirmation must have been called with True.
        calls = case.bridge.calls_to("answer_confirmation")
        assert len(calls) >= 1
        assert calls[-1].args[0] is True


@pytest.mark.asyncio
async def test_approval_modal_n_denies():
    """Pushing an ApprovalModal and pressing 'n' denies the action."""
    async with TUIInteractionCase.run() as case:
        case.app._show_confirm({"action": "delete_file", "summary": "/tmp/important"})
        await case.pilot.pause()
        await case.pilot.press("n")
        await case.pilot.pause()
        calls = case.bridge.calls_to("answer_confirmation")
        assert len(calls) >= 1
        assert calls[-1].args[0] is False


@pytest.mark.asyncio
async def test_approval_modal_escape_denies():
    """Escape in the ApprovalModal also denies (same as 'n')."""
    async with TUIInteractionCase.run() as case:
        case.app._show_confirm({"action": "rename_file", "summary": "old -> new"})
        await case.pilot.pause()
        await case.pilot.press("escape")
        await case.pilot.pause()
        calls = case.bridge.calls_to("answer_confirmation")
        assert len(calls) >= 1
        assert calls[-1].args[0] is False


# ── 3. Guardian modal ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guardian_modal_a_approves():
    """Pressing 'a' in the GuardianModal returns 'approve'."""
    async with TUIInteractionCase.run() as case:
        # Use _show_confirm with guardian_verdict=MODIFY so it pushes
        # the GuardianModal (not the regular ApprovalModal).
        case.app._show_confirm({
            "action": "write_file",
            "summary": "/etc/passwd",
            "rationale": "writes to a critical path",
            "guardian_verdict": "MODIFY",
            "suggested_args": {"path": "/tmp/safe.txt"},
        })
        await case.pilot.pause()
        await case.pilot.press("a")
        await case.pilot.pause()
        calls = case.bridge.calls_to("answer_confirmation")
        assert len(calls) >= 1
        assert calls[-1].args[0] is True


@pytest.mark.asyncio
async def test_guardian_modal_r_rejects():
    """Pressing 'r' in the GuardianModal returns 'reject'."""
    async with TUIInteractionCase.run() as case:
        case.app._show_confirm({
            "action": "execute_command",
            "summary": "rm -rf /",
            "rationale": "dangerous pattern",
            "guardian_verdict": "MODIFY",
            "suggested_args": None,
        })
        await case.pilot.pause()
        await case.pilot.press("r")
        await case.pilot.pause()
        calls = case.bridge.calls_to("answer_confirmation")
        assert len(calls) >= 1
        assert calls[-1].args[0] is False


@pytest.mark.asyncio
async def test_guardian_modal_u_uses_fix():
    """Pressing 'u' in the GuardianModal returns 'use_fix'."""
    async with TUIInteractionCase.run() as case:
        case.app._show_confirm({
            "action": "write_file",
            "summary": "/etc/passwd",
            "rationale": "writes to a critical path",
            "guardian_verdict": "MODIFY",
            "suggested_args": {"path": "/tmp/safe.txt"},
        })
        await case.pilot.pause()
        await case.pilot.press("u")
        await case.pilot.pause()
        calls = case.bridge.calls_to("answer_guardian_verdict")
        assert len(calls) >= 1
        assert calls[-1].args[0] == "use_fix"


# ── 4. Theme switch ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_theme_toggle_flips_dark_theme_flag():
    """Ctrl+T toggles the app's _dark_theme flag."""
    async with TUIInteractionCase.run() as case:
        before = case.app._dark_theme
        await case.pilot.press("ctrl+t")
        await case.pilot.pause()
        after = case.app._dark_theme
        assert after is not before, \
            f"_dark_theme did not flip: before={before}, after={after}"


@pytest.mark.asyncio
async def test_theme_toggle_twice_returns_to_original():
    """Pressing Ctrl+T twice returns to the original theme."""
    async with TUIInteractionCase.run() as case:
        original = case.app._dark_theme
        await case.pilot.press("ctrl+t")
        await case.pilot.pause()
        await case.pilot.press("ctrl+t")
        await case.pilot.pause()
        assert case.app._dark_theme is original


# ── 5. Inline section switch ({office} prefix) ───────────────────────


@pytest.mark.asyncio
async def test_inline_office_prefix_switches_section():
    """Typing '{office} hello' switches section to office and sends the
    cleaned message 'hello'."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("{office} hello")
        await case.submit_input()
        # The bridge's set_section must have been called with "office".
        section_calls = case.bridge.calls_to("set_section")
        assert len(section_calls) >= 1
        assert section_calls[-1].args[0] == "office"
        # The bridge's run_prompt must have been called with the
        # CLEANED message ("hello", not "{office} hello").
        run_calls = case.bridge.calls_to("run_prompt")
        assert len(run_calls) >= 1
        assert run_calls[-1].args[0] == "hello", \
            f"cleaned prompt was {run_calls[-1].args[0]!r}, expected 'hello'"


@pytest.mark.asyncio
async def test_inline_heavy_code_prefix_switches_section():
    """Typing '{heavy_code} refactor' switches section to heavy_code."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("{heavy_code} refactor this")
        await case.submit_input()
        section_calls = case.bridge.calls_to("set_section")
        assert len(section_calls) >= 1
        assert section_calls[-1].args[0] == "heavy_code"
        run_calls = case.bridge.calls_to("run_prompt")
        assert len(run_calls) >= 1
        assert run_calls[-1].args[0] == "refactor this"


@pytest.mark.asyncio
async def test_inline_office_alone_switches_without_sending():
    """Typing just '{office}' (no message) switches section but doesn't
    send a turn."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("{office}")
        await case.submit_input()
        section_calls = case.bridge.calls_to("set_section")
        assert len(section_calls) >= 1
        assert section_calls[-1].args[0] == "office"
        # run_prompt should NOT have been called.
        run_calls = case.bridge.calls_to("run_prompt")
        assert len(run_calls) == 0, \
            f"run_prompt was called unexpectedly: {run_calls}"


@pytest.mark.asyncio
async def test_inline_office_updates_status_bar_section_indicator():
    """After switching to office via {office}, the status bar text
    must reflect the new section."""
    async with TUIInteractionCase.run() as case:
        # Before: section is "general".
        assert case.bridge.section == "general"
        # Type {office} hello and submit.
        await case.type_input("{office} hello")
        await case.submit_input()
        await case.pilot.pause()
        # The bridge's section attribute should now be "office".
        assert case.bridge.section == "office"
        # The status bar text should mention "office".
        status_text = case.status_bar_text()
        assert "office" in status_text, \
            f"status bar doesn't mention office: {status_text!r}"


# ── 6. Slash command parsing ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_slash_section_office_command():
    """/section office (typed as a slash command) switches section."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("/section office")
        await case.submit_input()
        section_calls = case.bridge.calls_to("set_section")
        assert len(section_calls) >= 1
        assert section_calls[-1].args[0] == "office"


@pytest.mark.asyncio
async def test_slash_mode_office_command():
    """/mode office (the Loop 1 alias) switches section."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("/mode office")
        await case.submit_input()
        section_calls = case.bridge.calls_to("set_section")
        assert len(section_calls) >= 1
        assert section_calls[-1].args[0] == "office"


@pytest.mark.asyncio
async def test_slash_guardian_all_command():
    """/guardian all switches Guardian level."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("/guardian all")
        await case.submit_input()
        calls = case.bridge.calls_to("set_guardian_level")
        assert len(calls) >= 1
        assert calls[-1].args[0] == "all"


@pytest.mark.asyncio
async def test_slash_clear_command_runs():
    """/clear is handled (doesn't crash, doesn't send a turn)."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("/clear")
        await case.submit_input()
        # run_prompt should NOT have been called (it's a slash command,
        # not a chat message).
        run_calls = case.bridge.calls_to("run_prompt")
        assert len(run_calls) == 0


@pytest.mark.asyncio
async def test_unknown_slash_command_shows_error():
    """/nonexistent shows an error in the chat log."""
    async with TUIInteractionCase.run() as case:
        await case.type_input("/nonexistent")
        await case.submit_input()
        log_text = case.chat_log_text()
        assert "unknown command" in log_text, \
            f"expected 'unknown command' in log: {log_text!r}"
