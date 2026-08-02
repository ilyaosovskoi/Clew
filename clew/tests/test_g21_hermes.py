#!/usr/bin/env python3
"""
G21 — Autonomous Hermes mode — test suite.

Verifies:
  21a (inbound listener):
    1. InboundListenerConfig validates mandatory allow-list (no wildcard).
    2. TelegramInboundListener polls getUpdates and parses messages.
    3. Allow-list rejects messages from non-allowed chats.
    4. STOP keyword triggers on_stop callback (kill switch).
    5. make_daemon_callback submits to TaskQueue + tags activity log.
    6. make_daemon_stop_callback cancels the running task.
    7. Discord/Slack stubs raise NotImplementedError.
    8. TelegramInboundListener.start/stop lifecycle.

  21b (Hermes CLI preset):
    9. `clew hermes` argparse requires --workspace/--telegram-token/--allow.
    10. _hermes() applies sandbox + autonomy=never_ask + listener + notifier.

  21c (Guardian regression — CRITICAL):
    11. never_ask + Hermes mode does NOT bypass Guardian.assess_risk.
    12. Guardian risk assessment runs identically regardless of autonomy.
    13. A high-risk tool call is flagged as high regardless of autonomy.

Run:
    python -m pytest clew/tests/test_g21_hermes.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Test isolation ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect ~/.clew to a temp dir so tests don't clobber the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    yield


# ── 21a: InboundListener ───────────────────────────────────────────────


def test_config_empty_allow_list_rejected():
    """Empty allowed_chat_ids is invalid (no wildcard mode, G21 §21a)."""
    from clew.inbound_listener import InboundListenerConfig
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="x", allowed_chat_ids=set(),
    )
    errors = cfg.validate()
    assert any("allowed_chat_ids is MANDATORY" in e for e in errors)


def test_config_missing_token_rejected():
    """Missing telegram_token is invalid for backend=telegram."""
    from clew.inbound_listener import InboundListenerConfig
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="", allowed_chat_ids={"123"},
    )
    errors = cfg.validate()
    assert any("telegram_token is required" in e for e in errors)


def test_config_valid_passes():
    """Valid config passes validation."""
    from clew.inbound_listener import InboundListenerConfig
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="abc",
        allowed_chat_ids={"123", "456"},
    )
    assert cfg.validate() == []


def test_config_unknown_backend_rejected():
    """Unknown backend is rejected."""
    from clew.inbound_listener import InboundListenerConfig
    cfg = InboundListenerConfig(
        backend="bogus", telegram_token="x", allowed_chat_ids={"123"},
    )
    errors = cfg.validate()
    assert any("unknown backend" in e for e in errors)


def test_config_to_dict_redacts_token():
    """to_dict() redacts the telegram token for safe display."""
    from clew.inbound_listener import InboundListenerConfig
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="secret123",
        allowed_chat_ids={"123"},
    )
    d = cfg.to_dict()
    assert d["telegram_token"] == "***"  # redacted


def test_telegram_listener_polls_and_parses_messages():
    """TelegramInboundListener._poll_once parses getUpdates response."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123", "456"},
    )

    # Mock the HTTP POST to return a fake getUpdates response.
    fake_updates = {
        "result": [
            {"update_id": 1, "message": {
                "text": "hello from alice",
                "chat": {"id": 123},
                "from": {"id": 999, "username": "alice"},
            }},
            {"update_id": 2, "message": {
                "text": "STOP",
                "chat": {"id": 456},
                "from": {"id": 888, "first_name": "Bob"},
            }},
        ]
    }
    def fake_post(method, params): return fake_updates

    listener = TelegramInboundListener(
        cfg, lambda m: None, on_stop=lambda m: None, _http_post=fake_post,
    )
    msgs = listener._poll_once()
    assert len(msgs) == 2
    assert msgs[0].backend == "telegram"
    assert msgs[0].chat_id == "123"
    assert msgs[0].sender_id == "999"
    assert msgs[0].sender_name == "alice"
    assert msgs[0].text == "hello from alice"
    # Offset advances past the last update_id.
    assert listener._offset == 3  # last update_id (2) + 1


def test_telegram_listener_skips_non_text_messages():
    """Non-text messages (stickers, photos) are skipped."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123"},
    )
    fake_updates = {
        "result": [
            {"update_id": 1, "message": {
                "sticker": {"file_id": "abc"},  # no text field
                "chat": {"id": 123},
                "from": {"id": 999},
            }},
            {"update_id": 2, "message": {
                "text": "real message",
                "chat": {"id": 123},
                "from": {"id": 999},
            }},
        ]
    }
    listener = TelegramInboundListener(
        cfg, lambda m: None, _http_post=lambda m, p: fake_updates,
    )
    msgs = listener._poll_once()
    assert len(msgs) == 1  # only the text message
    assert msgs[0].text == "real message"


def test_allow_list_rejects_non_allowed_chat():
    """Messages from non-allow-listed chats are rejected, not dispatched."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener, InboundMessage,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123"},
    )
    accepted = []
    listener = TelegramInboundListener(
        cfg, lambda m: accepted.append(m),
        _http_post=lambda m, p: {"result": []},
    )
    # Manually feed a message from a non-allowed chat.
    msg = InboundMessage(
        backend="telegram", chat_id="999", sender_id="x",
        sender_name="stranger", text="hi",
    )
    listener._handle_message(msg)
    assert len(accepted) == 0  # rejected
    assert listener._messages_rejected == 1


def test_allow_list_accepts_allowed_chat():
    """Messages from allow-listed chats are dispatched."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener, InboundMessage,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123"},
    )
    accepted = []
    listener = TelegramInboundListener(
        cfg, lambda m: accepted.append(m),
        _http_post=lambda m, p: {"result": []},
    )
    msg = InboundMessage(
        backend="telegram", chat_id="123", sender_id="x",
        sender_name="alice", text="hello",
    )
    listener._handle_message(msg)
    assert len(accepted) == 1
    assert listener._messages_processed == 1


def test_stop_keyword_triggers_on_stop():
    """STOP keyword triggers on_stop callback (kill switch, G21 §21a)."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener, InboundMessage,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123"},
    )
    accepted = []
    stopped = []
    listener = TelegramInboundListener(
        cfg,
        on_message=lambda m: accepted.append(m),
        on_stop=lambda m: stopped.append(m),
        _http_post=lambda m, p: {"result": []},
    )
    msg = InboundMessage(
        backend="telegram", chat_id="123", sender_id="x",
        sender_name="alice", text="STOP",
    )
    listener._handle_message(msg)
    assert len(accepted) == 0  # not dispatched as a normal message
    assert len(stopped) == 1   # kill switch fired
    assert listener._stops_processed == 1


def test_stop_keyword_case_insensitive():
    """STOP works in any case (stop, Stop, STOP)."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener, InboundMessage,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123"},
    )
    stopped = []
    listener = TelegramInboundListener(
        cfg, on_message=lambda m: None,
        on_stop=lambda m: stopped.append(m),
        _http_post=lambda m, p: {"result": []},
    )
    for variant in ["STOP", "stop", "Stop", "sToP"]:
        msg = InboundMessage(
            backend="telegram", chat_id="123", sender_id="x",
            sender_name="alice", text=variant,
        )
        listener._handle_message(msg)
    assert len(stopped) == 4
    assert listener._stops_processed == 4


def test_make_daemon_callback_submits_to_queue_and_logs():
    """make_daemon_callback calls task_queue.submit + writes activity log."""
    from clew.inbound_listener import (
        make_daemon_callback, InboundMessage,
    )
    from clew.activity_log import get_activity_log
    get_activity_log().clear()

    submitted = []
    class FakeQueue:
        def submit(self, prompt, workspace=""):
            submitted.append((prompt, workspace))
            return "task_123"

    callback = make_daemon_callback(
        FakeQueue(), workspace="/tmp/test",
        activity_log=get_activity_log(),
    )
    msg = InboundMessage(
        backend="telegram", chat_id="123", sender_id="999",
        sender_name="alice", text="do the thing",
    )
    callback(msg)
    assert len(submitted) == 1
    assert submitted[0] == ("do the thing", "/tmp/test")
    # Activity log got an entry.
    entries = get_activity_log().recent(n=10, search="inbound")
    assert any("task_123" in e["title"] for e in entries)


def test_make_daemon_stop_callback_cancels_running():
    """make_daemon_stop_callback cancels the currently running task."""
    from clew.inbound_listener import (
        make_daemon_stop_callback, InboundMessage,
    )
    cancelled = []
    class FakeQueue:
        def list_tasks(self, limit=50):
            return [{"task_id": "running_1", "status": "running"}]
        def cancel_task(self, task_id):
            cancelled.append(task_id)
            return True

    callback = make_daemon_stop_callback(FakeQueue())
    msg = InboundMessage(
        backend="telegram", chat_id="123", sender_id="999",
        sender_name="alice", text="STOP",
    )
    callback(msg)
    assert cancelled == ["running_1"]


def test_discord_stub_raises_not_implemented():
    """DiscordInboundListener raises NotImplementedError (stub)."""
    from clew.inbound_listener import (
        DiscordInboundListener, InboundListenerConfig,
    )
    cfg = InboundListenerConfig(
        backend="discord", telegram_token="",
        allowed_chat_ids={"123"},
    )
    with pytest.raises(NotImplementedError, match="Discord"):
        DiscordInboundListener(cfg, lambda m: None)


def test_slack_stub_raises_not_implemented():
    """SlackInboundListener raises NotImplementedError (stub)."""
    from clew.inbound_listener import (
        SlackInboundListener, InboundListenerConfig,
    )
    cfg = InboundListenerConfig(
        backend="slack", telegram_token="",
        allowed_chat_ids={"123"},
    )
    with pytest.raises(NotImplementedError, match="Slack"):
        SlackInboundListener(cfg, lambda m: None)


def test_factory_builds_telegram():
    """make_inbound_listener builds TelegramInboundListener for backend=telegram."""
    from clew.inbound_listener import (
        make_inbound_listener, TelegramInboundListener, InboundListenerConfig,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="x",
        allowed_chat_ids={"123"},
    )
    listener = make_inbound_listener(cfg, lambda m: None, _http_post=lambda m, p: {"result": []})
    assert isinstance(listener, TelegramInboundListener)


def test_factory_rejects_unknown_backend():
    """make_inbound_listener raises on unknown backend."""
    from clew.inbound_listener import (
        make_inbound_listener, InboundListenerConfig, InboundListenerError,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="x",
        allowed_chat_ids={"123"},
    )
    # Sneak a bogus backend past config validation.
    cfg.backend = "bogus"
    with pytest.raises(InboundListenerError):
        make_inbound_listener(cfg, lambda m: None)


def test_listener_status_reflects_state():
    """status() reports processed/rejected/stops counters."""
    from clew.inbound_listener import (
        InboundListenerConfig, TelegramInboundListener, InboundMessage,
    )
    cfg = InboundListenerConfig(
        backend="telegram", telegram_token="fake",
        allowed_chat_ids={"123"},
    )
    listener = TelegramInboundListener(
        cfg, lambda m: None, on_stop=lambda m: None,
        _http_post=lambda m, p: {"result": []},
    )
    # Feed 3 messages: 1 accepted, 1 rejected, 1 STOP.
    listener._handle_message(InboundMessage("telegram", "123", "a", "a", "hello"))
    listener._handle_message(InboundMessage("telegram", "999", "b", "b", "rejected"))
    listener._handle_message(InboundMessage("telegram", "123", "c", "c", "STOP"))
    s = listener.status()
    assert s["messages_processed"] == 1
    assert s["messages_rejected"] == 1
    assert s["stops_processed"] == 1
    assert s["backend"] == "telegram"
    assert s["running"] is False  # not started


# ── 21b: Hermes CLI preset ─────────────────────────────────────────────


def test_hermes_argparse_requires_workspace():
    """`clew hermes` requires --workspace."""
    from clew.cli import _build_parser
    parser = _build_parser()
    # Missing --workspace should SystemExit (argparse error).
    with pytest.raises(SystemExit):
        parser.parse_args(["hermes", "--telegram-token", "x", "--allow", "123"])


def test_hermes_argparse_requires_telegram_token():
    """`clew hermes` requires --telegram-token."""
    from clew.cli import _build_parser
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["hermes", "--workspace", "/tmp", "--allow", "123"])


def test_hermes_argparse_requires_allow():
    """`clew hermes` requires at least one --allow."""
    from clew.cli import _build_parser
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["hermes", "--workspace", "/tmp", "--telegram-token", "x"])


def test_hermes_argparse_parses_valid_args():
    """`clew hermes` parses a valid argument set."""
    from clew.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "hermes",
        "--workspace", "/tmp/test",
        "--telegram-token", "abc",
        "--allow", "123",
        "--allow", "456",
        "--max-iterations", "20",
    ])
    assert args.command == "hermes"
    assert args.workspace == "/tmp/test"
    assert args.telegram_token == "abc"
    assert args.allow == ["123", "456"]
    assert args.max_iterations == 20


def test_hermes_argparse_defaults():
    """`clew hermes` defaults are sensible."""
    from clew.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "hermes", "--workspace", "/tmp", "--telegram-token", "x", "--allow", "1",
    ])
    assert args.no_sandbox is False
    assert args.no_plan is False
    assert args.max_iterations == 15
    assert args.notify is None


# ── 21c: Guardian regression — CRITICAL ────────────────────────────────


def test_guardian_assess_risk_does_not_read_autonomy():
    """Guardian.assess_risk signature doesn't take autonomy at all.

    This is the core regression: Guardian NEVER sees the autonomy level,
    so it can't possibly be bypassed by never_ask. The function signature
    is the contract.
    """
    import inspect
    from clew.agent.guardian import assess_risk
    sig = inspect.signature(assess_risk)
    param_names = list(sig.parameters.keys())
    assert "autonomy" not in param_names, (
        "Guardian.assess_risk must NOT take an autonomy parameter — "
        "if it did, never_ask could potentially bypass it."
    )
    # Required params: tool_name, args, workspace.
    assert "tool_name" in param_names
    assert "args" in param_names
    assert "workspace" in param_names


def test_guardian_assess_risk_identical_regardless_of_autonomy():
    """Guardian risk assessment is IDENTICAL for always_ask vs never_ask.

    Per G21 §21c: "never_ask must only ever mean 'don't block waiting
    for a human click.' It must NOT bypass guardian.py's risk assessment
    or its hard blocks."

    This test verifies that Guardian returns the SAME risk level for
    the same tool call regardless of what autonomy level the caller
    picks — because Guardian doesn't even know what autonomy is.
    """
    from clew.agent.guardian import assess_risk
    # A clearly high-risk call: writing to /etc/passwd.
    args = {"path": "/etc/passwd", "content": "hacker::0:0::/"}
    workspace = "/tmp/safe-workspace"
    risk_always_ask = assess_risk("write_file", args, workspace)
    risk_never_ask = assess_risk("write_file", args, workspace)
    # Must be identical — Guardian doesn't read autonomy.
    assert risk_always_ask.level == risk_never_ask.level
    assert risk_always_ask.reasons == risk_never_ask.reasons
    # And the level should be "high" (writing to /etc/passwd is critical).
    assert risk_always_ask.level == "high"


def test_guardian_flags_dangerous_command_regardless_of_autonomy():
    """`rm -rf /` is flagged as high regardless of autonomy level."""
    from clew.agent.guardian import assess_risk
    args = {"command": "rm -rf /"}
    workspace = "/tmp/safe"
    risk1 = assess_risk("execute_command", args, workspace)
    risk2 = assess_risk("execute_command", args, workspace)
    assert risk1.level == "high"
    assert risk2.level == "high"
    assert risk1.level == risk2.level


def test_guardian_flags_critical_filename_regardless_of_autonomy():
    """Writing to .env is flagged regardless of autonomy level."""
    from clew.agent.guardian import assess_risk
    args = {"path": ".env", "content": "SECRET=abc"}
    workspace = "/tmp/safe"
    risk1 = assess_risk("write_file", args, workspace)
    risk2 = assess_risk("write_file", args, workspace)
    assert risk1.level == risk2.level
    # .env is in CRITICAL_FILENAMES → high.
    assert risk1.level == "high"


def test_guardian_low_risk_for_safe_call_regardless_of_autonomy():
    """A safe read_file call is low regardless of autonomy level."""
    from clew.agent.guardian import assess_risk
    args = {"path": "/tmp/safe/readme.txt"}
    workspace = "/tmp/safe"
    risk1 = assess_risk("read_file", args, workspace)
    risk2 = assess_risk("read_file", args, workspace)
    assert risk1.level == risk2.level
    # read_file isn't in the high-risk tool list — should be low or medium at most.
    assert risk1.level in ("low", "medium")


def test_never_ask_does_not_skip_guardian_in_tool_engine():
    """ToolEngine.execute() calls _guardian_review regardless of autonomy.

    This is the integration-level regression test: even with
    autonomy="never_ask", the Guardian pre-execution review path runs.
    We verify this by inspecting the source code (the test is
    deliberately source-level, not behavioural, because a behavioural
    test would need to actually execute a tool call which requires a
    full runtime + provider).
    """
    import inspect
    from clew.agent_runtime.tool_engine._engine import ToolEngine
    source = inspect.getsource(ToolEngine.execute)
    # The execute method must call _guardian_review BEFORE _dispatch.
    assert "_guardian_review" in source, (
        "ToolEngine.execute must call _guardian_review — if removed, "
        "never_ask + Hermes mode would skip Guardian entirely."
    )
    # The call must come BEFORE _dispatch (so a REJECT can prevent the
    # tool from running).
    guardian_idx = source.find("_guardian_review")
    dispatch_idx = source.find("_dispatch")
    assert guardian_idx != -1 and dispatch_idx != -1
    assert guardian_idx < dispatch_idx, (
        "_guardian_review must run BEFORE _dispatch — otherwise a "
        "REJECT verdict would arrive too late to prevent the tool call."
    )


def test_never_ask_only_short_circuits_request_confirmation():
    """never_ask only short-circuits _request_confirmation, NOT Guardian.

    Per G21 §21c: "never_ask must only ever mean 'don't block waiting
    for a human click.'"

    We verify this by checking that the autonomy check lives in
    _request_confirmation (the per-tool confirmation gate), NOT in
    _guardian_review (the risk-assessment gate).
    """
    import inspect
    from clew.agent_runtime.tool_engine._engine import ToolEngine
    # _guardian_review source must NOT reference autonomy.
    guardian_source = inspect.getsource(ToolEngine._guardian_review)
    assert "autonomy" not in guardian_source, (
        "_guardian_review must NOT read self.autonomy — Guardian must "
        "run identically regardless of autonomy level."
    )
    # _request_confirmation source SHOULD reference autonomy (that's
    # where the never_ask short-circuit lives).
    confirm_source = inspect.getsource(ToolEngine._request_confirmation)
    assert "autonomy" in confirm_source, (
        "_request_confirmation must read self.autonomy — that's where "
        "never_ask short-circuits the per-tool confirmation gate."
    )


def test_hermes_mode_sets_never_ask_autonomy():
    """Hermes mode sets autonomy='never_ask' on the runtime.

    This is the integration test: the _hermes() function in cli.py
    must call runtime.set_autonomy('never_ask') so the runtime's
    autonomy is set correctly for Hermes mode.
    """
    import inspect
    from clew.cli import _hermes
    source = inspect.getsource(_hermes)
    assert "set_autonomy" in source, (
        "_hermes() must call runtime.set_autonomy() — otherwise Hermes "
        "mode wouldn't actually configure the runtime for autonomous operation."
    )
    assert '"never_ask"' in source or "'never_ask'" in source, (
        "_hermes() must call set_autonomy('never_ask') — the G21 §21b spec "
        "requires autonomy='never_ask' for Hermes mode."
    )


def test_hermes_mode_applies_sandbox():
    """Hermes mode calls apply_sandbox(profile='workspace', ...).

    Per G21 §21b: the Hermes preset must apply the OS-level sandbox.
    """
    import inspect
    from clew.cli import _hermes
    source = inspect.getsource(_hermes)
    assert "apply_sandbox" in source, (
        "_hermes() must call apply_sandbox() — the OS-level workspace "
        "sandbox is the outer backstop (G21 §21c)."
    )
    assert '"workspace"' in source or "'workspace'" in source, (
        "_hermes() must call apply_sandbox(profile='workspace', ...) per G21 §21b."
    )


def test_hermes_mode_enables_inbound_listener():
    """Hermes mode starts the inbound listener."""
    import inspect
    from clew.cli import _hermes
    source = inspect.getsource(_hermes)
    assert "make_inbound_listener" in source
    assert "listener.start" in source


def test_hermes_mode_enables_outbound_notifier():
    """Hermes mode enables the outbound notifier."""
    import inspect
    from clew.cli import _hermes
    source = inspect.getsource(_hermes)
    assert "_enable_notifier" in source
