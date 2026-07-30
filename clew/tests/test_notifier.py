#!/usr/bin/env python3
"""Tests for clew/notifier.py — notification backends and Notifier singleton."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from unittest.mock import patch, MagicMock

import pytest


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_notifier():
    """Reset the Notifier singleton before and after each test."""
    from clew.notifier import reset_notifier
    reset_notifier()
    yield
    reset_notifier()


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary notifiers.json config path."""
    config_path = str(tmp_path / "notifiers.json")
    return config_path


# ── EventKind ─────────────────────────────────────────────────

class TestEventKind:
    def test_values(self):
        from clew.notifier import EventKind
        assert EventKind.DONE == "done"
        assert EventKind.ERROR == "error"
        assert EventKind.CHECKPOINT == "checkpoint"
        assert EventKind.TOOL_CALL == "tool_call"
        assert EventKind.CUSTOM == "custom"


# ── NotificationEvent ─────────────────────────────────────────

class TestNotificationEvent:
    def test_creation(self):
        from clew.notifier import NotificationEvent, EventKind
        evt = NotificationEvent(
            event=EventKind.DONE,
            title="Task done",
            message="The task completed successfully",
            data={"tokens": 1000},
        )
        assert evt.event == EventKind.DONE
        assert evt.title == "Task done"
        assert evt.message == "The task completed successfully"
        assert evt.data["tokens"] == 1000
        assert evt.timestamp > 0

    def test_default_values(self):
        from clew.notifier import NotificationEvent, EventKind
        evt = NotificationEvent(event=EventKind.ERROR, title="Err", message="fail")
        assert evt.data == {}
        assert evt.timestamp > 0


# ── TelegramBackend ───────────────────────────────────────────

class TestTelegramBackend:
    def test_init(self):
        from clew.notifier import TelegramBackend
        config = {"bot_token": "123:ABC", "chat_id": "456", "enabled": True, "events": ["done"]}
        backend = TelegramBackend(config)
        assert backend.name == "telegram"
        assert backend.bot_token == "123:ABC"
        assert backend.chat_id == "456"
        assert backend.enabled is True

    def test_send_disabled(self):
        from clew.notifier import TelegramBackend, NotificationEvent, EventKind
        backend = TelegramBackend({"enabled": False, "bot_token": "x", "chat_id": "y"})
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        assert backend.send(evt) is False

    def test_send_missing_token(self):
        from clew.notifier import TelegramBackend, NotificationEvent, EventKind
        backend = TelegramBackend({"enabled": True, "bot_token": "", "chat_id": "y"})
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        assert backend.send(evt) is False

    def test_send_event_filtered(self):
        from clew.notifier import TelegramBackend, NotificationEvent, EventKind
        backend = TelegramBackend({
            "enabled": True, "bot_token": "x", "chat_id": "y",
            "events": ["error"],
        })
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        assert backend.send(evt) is False

    def test_send_with_mock(self):
        from clew.notifier import TelegramBackend, NotificationEvent, EventKind
        backend = TelegramBackend({
            "enabled": True, "bot_token": "123:ABC", "chat_id": "456",
            "events": ["done"],
        })
        evt = NotificationEvent(event=EventKind.DONE, title="Task done", message="OK")
        with patch("clew.notifier.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            assert backend.send(evt) is True

    def test_send_network_error(self):
        from clew.notifier import TelegramBackend, NotificationEvent, EventKind
        backend = TelegramBackend({
            "enabled": True, "bot_token": "123:ABC", "chat_id": "456",
            "events": ["done"],
        })
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        with patch("clew.notifier.urllib.request.urlopen", side_effect=Exception("net error")):
            assert backend.send(evt) is False

    def test_format_message(self):
        from clew.notifier import TelegramBackend, NotificationEvent, EventKind
        backend = TelegramBackend({"enabled": True, "bot_token": "x", "chat_id": "y"})
        evt = NotificationEvent(
            event=EventKind.DONE, title="Done", message="All good",
            data={"tokens": "1,000"},
        )
        msg = backend._format_message(evt)
        assert "Done" in msg
        assert "All good" in msg
        assert "tokens" in msg


# ── DiscordBackend ────────────────────────────────────────────

class TestDiscordBackend:
    def test_init(self):
        from clew.notifier import DiscordBackend
        backend = DiscordBackend({"webhook_url": "https://discord.com/wh", "enabled": True})
        assert backend.name == "discord"
        assert backend.webhook_url == "https://discord.com/wh"

    def test_send_disabled(self):
        from clew.notifier import DiscordBackend, NotificationEvent, EventKind
        backend = DiscordBackend({"enabled": False, "webhook_url": "x"})
        assert backend.send(NotificationEvent(event=EventKind.DONE, title="T", message="M")) is False

    def test_send_with_mock(self):
        from clew.notifier import DiscordBackend, NotificationEvent, EventKind
        backend = DiscordBackend({
            "enabled": True, "webhook_url": "https://discord.com/wh",
            "events": ["done"],
        })
        evt = NotificationEvent(event=EventKind.DONE, title="Done", message="OK")
        with patch("clew.notifier.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 204
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            assert backend.send(evt) is True


# ── SlackBackend ──────────────────────────────────────────────

class TestSlackBackend:
    def test_init(self):
        from clew.notifier import SlackBackend
        backend = SlackBackend({"webhook_url": "https://hooks.slack.com/x", "enabled": True})
        assert backend.name == "slack"

    def test_send_disabled(self):
        from clew.notifier import SlackBackend, NotificationEvent, EventKind
        backend = SlackBackend({"enabled": False, "webhook_url": "x"})
        assert backend.send(NotificationEvent(event=EventKind.DONE, title="T", message="M")) is False

    def test_send_with_mock(self):
        from clew.notifier import SlackBackend, NotificationEvent, EventKind
        backend = SlackBackend({
            "enabled": True, "webhook_url": "https://hooks.slack.com/x",
            "events": ["done"],
        })
        evt = NotificationEvent(event=EventKind.DONE, title="Done", message="OK")
        with patch("clew.notifier.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            assert backend.send(evt) is True


# ── Notifier singleton ────────────────────────────────────────

class TestNotifier:
    def test_get_notifier_singleton(self):
        from clew.notifier import get_notifier, Notifier
        n1 = get_notifier()
        n2 = get_notifier()
        assert n1 is n2
        assert isinstance(n1, Notifier)

    def test_configure_backend(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        with patch.object(n, "_save_config"):
            result = n.configure_backend("telegram", {
                "enabled": True, "bot_token": "x", "chat_id": "y",
            })
        assert result["ok"] is True
        assert result["backend"] == "telegram"

    def test_configure_unknown_backend(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        result = n.configure_backend("unknown_backend", {"enabled": True})
        assert result["ok"] is False
        assert "Unknown backend" in result["error"]

    def test_set_backend_enabled(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        with patch.object(n, "_save_config"):
            n.configure_backend("telegram", {"enabled": True, "bot_token": "x", "chat_id": "y"})
            result = n.set_backend_enabled("telegram", False)
        assert result["ok"] is True
        assert result["enabled"] is False

    def test_set_enabled_nonexistent(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        result = n.set_backend_enabled("nonexistent", True)
        assert result["ok"] is False

    def test_list_backends_empty(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        assert n.list_backends() == []

    def test_list_backends_configured(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        with patch.object(n, "_save_config"):
            n.configure_backend("telegram", {"enabled": True, "bot_token": "x", "chat_id": "y"})
        backends = n.list_backends()
        assert len(backends) == 1
        assert backends[0]["name"] == "telegram"

    def test_status(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        status = n.status()
        assert "total_backends" in status
        assert "enabled_backends" in status

    def test_notify_no_backends(self):
        from clew.notifier import get_notifier, NotificationEvent, EventKind
        n = get_notifier()
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        result = n.notify(evt)
        assert result == {}

    def test_notify_with_backend(self):
        from clew.notifier import get_notifier, NotificationEvent, EventKind
        n = get_notifier()
        with patch.object(n, "_save_config"):
            n.configure_backend("telegram", {"enabled": True, "bot_token": "x", "chat_id": "y", "events": ["done"]})
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        with patch("clew.notifier.urllib.request.urlopen", side_effect=Exception("net error")):
            result = n.notify(evt)
        assert "telegram" in result

    def test_notify_async(self):
        from clew.notifier import get_notifier, NotificationEvent, EventKind
        n = get_notifier()
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        # Should not raise even with no backends
        n.notify_async(evt)

    def test_test_backend(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        result = n.test_backend("nonexistent")
        assert result["ok"] is False

    def test_remove_backend(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        with patch.object(n, "_save_config"):
            n.configure_backend("telegram", {"enabled": True, "bot_token": "x", "chat_id": "y"})
            result = n.remove_backend("telegram")
        assert result["ok"] is True

    def test_set_events(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        with patch.object(n, "_save_config"):
            n.configure_backend("telegram", {"enabled": True, "bot_token": "x", "chat_id": "y"})
            result = n.set_events("telegram", ["done", "error", "checkpoint"])
        assert result["ok"] is True
        assert result["events"] == ["done", "error", "checkpoint"]

    def test_get_history_empty(self):
        from clew.notifier import get_notifier
        n = get_notifier()
        assert n.get_history() == []

    def test_history_after_notify(self):
        from clew.notifier import get_notifier, NotificationEvent, EventKind
        n = get_notifier()
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        n.notify(evt)
        history = n.get_history()
        assert len(history) == 1
        assert history[0]["event"] == "done"

    def test_clear_history(self):
        from clew.notifier import get_notifier, NotificationEvent, EventKind
        n = get_notifier()
        evt = NotificationEvent(event=EventKind.DONE, title="T", message="M")
        n.notify(evt)
        result = n.clear_history()
        assert result["ok"] is True
        assert result["cleared"] == 1
        assert n.get_history() == []

    def test_config_persistence(self, tmp_path):
        from clew.notifier import Notifier
        config_path = str(tmp_path / "notifiers.json")
        with patch("clew.notifier._CONFIG_PATH", config_path):
            n = Notifier()
            n.configure_backend("telegram", {"enabled": True, "bot_token": "x", "chat_id": "y"})
            # Check file was written
            with open(config_path) as f:
                data = json.load(f)
            assert "telegram" in data
            assert data["telegram"]["bot_token"] == "x"
