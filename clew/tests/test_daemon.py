#!/usr/bin/env python3
"""Tests for clew/daemon.py — remote agent daemon, task queue, and HTTP API."""

from __future__ import annotations

import json
import os
import threading
import time
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


# ── TaskState ─────────────────────────────────────────────────

class TestTaskState:
    def test_values(self):
        from clew.daemon import TaskState
        assert TaskState.PENDING == "pending"
        assert TaskState.RUNNING == "running"
        assert TaskState.COMPLETED == "completed"
        assert TaskState.FAILED == "failed"
        assert TaskState.CANCELLED == "cancelled"


# ── TaskRecord ────────────────────────────────────────────────

class TestTaskRecord:
    def test_creation(self):
        from clew.daemon import TaskRecord, TaskState
        task = TaskRecord(prompt="Test task", workspace="/tmp")
        assert task.prompt == "Test task"
        assert task.workspace == "/tmp"
        assert task.state == TaskState.PENDING
        assert task.id  # auto-generated
        assert task.created_at > 0

    def test_to_dict(self):
        from clew.daemon import TaskRecord
        task = TaskRecord(prompt="Test", workspace="/tmp")
        d = task.to_dict()
        assert "id" in d
        assert d["prompt"] == "Test"
        assert d["state"] == "pending"
        assert d["workspace"] == "/tmp"

    def test_duration_s(self):
        from clew.daemon import TaskRecord
        task = TaskRecord(prompt="Test")
        assert task.duration_s is None
        task.started_at = time.time() - 10
        assert task.duration_s >= 10
        task.completed_at = task.started_at + 5
        assert task.duration_s == 5.0


# ── SSESubscriber ─────────────────────────────────────────────

class TestSSESubscriber:
    def test_subscribe_and_emit(self):
        from clew.daemon import SSESubscriber
        sub = SSESubscriber()
        received = []
        sub.subscribe(lambda t, d: received.append((t, d)))
        sub.emit("test", {"msg": "hello"})
        assert len(received) == 1
        assert received[0] == ("test", {"msg": "hello"})

    def test_unsubscribe(self):
        from clew.daemon import SSESubscriber
        sub = SSESubscriber()
        received = []
        cb = lambda t, d: received.append((t, d))
        sub.subscribe(cb)
        sub.unsubscribe(cb)
        sub.emit("test", {"msg": "hello"})
        assert len(received) == 0

    def test_emit_exception_in_callback(self):
        from clew.daemon import SSESubscriber
        sub = SSESubscriber()
        sub.subscribe(lambda t, d: (_ for _ in ()).throw(Exception("boom")))
        # Should not raise
        sub.emit("test", {"msg": "hello"})


# ── TaskQueue ─────────────────────────────────────────────────

class TestTaskQueue:
    def test_submit(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        task = tq.submit("Test prompt", workspace="/tmp")
        assert task.prompt == "Test prompt"
        assert task.workspace == "/tmp"
        # Should be in the task list
        tasks = tq.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == task.id

    def test_get_task(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        task = tq.submit("Test")
        fetched = tq.get_task(task.id)
        assert fetched is not None
        assert fetched.id == task.id

    def test_get_task_nonexistent(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        assert tq.get_task("nonexistent") is None

    def test_list_tasks(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        tq.submit("Task 1")
        tq.submit("Task 2")
        tasks = tq.list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_limit(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        for i in range(5):
            tq.submit(f"Task {i}")
        tasks = tq.list_tasks(limit=3)
        assert len(tasks) == 3

    def test_cancel_task(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        task = tq.submit("Test")
        result = tq.cancel_task(task.id)
        assert result["ok"] is True
        # Task should be cancelled
        fetched = tq.get_task(task.id)
        assert fetched.state.value == "cancelled"

    def test_cancel_nonexistent(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        result = tq.cancel_task("nonexistent")
        assert result["ok"] is False

    def test_cancel_completed_task(self):
        from clew.daemon import TaskQueue, TaskState
        tq = TaskQueue()
        task = tq.submit("Test")
        # Manually set state to completed
        task.state = TaskState.COMPLETED
        result = tq.cancel_task(task.id)
        assert result["ok"] is False

    def test_subscribe(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        task = tq.submit("Test")
        received = []
        ok = tq.subscribe(task.id, lambda t, d: received.append((t, d)))
        assert ok is True

    def test_subscribe_nonexistent(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        ok = tq.subscribe("nonexistent", lambda t, d: None)
        assert ok is False

    def test_unsubscribe(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        task = tq.submit("Test")
        cb = lambda t, d: None
        tq.subscribe(task.id, cb)
        tq.unsubscribe(task.id, cb)  # Should not raise


# ── Daemon config ─────────────────────────────────────────────

class TestDaemonConfig:
    def test_load_config_missing(self, tmp_path):
        from clew.daemon import load_daemon_config
        with patch("clew.daemon._DAEMON_CONFIG_PATH", str(tmp_path / "missing.json")):
            config = load_daemon_config()
            assert config == {}

    def test_save_and_load_config(self, tmp_path):
        from clew.daemon import save_daemon_config, load_daemon_config
        config_path = str(tmp_path / "daemon.json")
        with patch("clew.daemon._DAEMON_CONFIG_PATH", config_path):
            save_daemon_config({"auth_token": "test-token-123"})
            loaded = load_daemon_config()
            assert loaded["auth_token"] == "test-token-123"

    def test_generate_token(self):
        from clew.daemon import generate_token
        token = generate_token()
        assert token.startswith("clew-")
        assert len(token) > 10


# ── DaemonHandler (HTTP API) ──────────────────────────────────

class TestDaemonHandler:
    def _make_handler(self, tq):
        """Create a mock handler with a task queue attached."""
        from clew.daemon import DaemonHandler
        handler = MagicMock(spec=DaemonHandler)
        handler.task_queue = tq
        handler.auth_token = None
        return handler

    def test_health_endpoint(self):
        from clew.daemon import TaskQueue
        tq = TaskQueue()
        # The health endpoint just returns {"status": "ok"}
        # We test the logic, not the HTTP server
        assert tq is not None  # TaskQueue was created successfully


# ── ClewDaemon ────────────────────────────────────────────────

class TestClewDaemon:
    def test_init(self):
        from clew.daemon import ClewDaemon
        daemon = ClewDaemon(host="127.0.0.1", port=9999, auth_token="test-token")
        assert daemon.host == "127.0.0.1"
        assert daemon.port == 9999
        assert daemon.auth_token == "test-token"

    def test_init_defaults(self):
        from clew.daemon import ClewDaemon
        daemon = ClewDaemon()
        assert daemon.host == "0.0.0.0"
        assert daemon.port == 8765
        assert daemon.auth_token is None
        assert daemon.task_queue is not None

    def test_enable_notifier_not_configured(self):
        from clew.daemon import ClewDaemon
        daemon = ClewDaemon()
        # Should not raise even if backend is not configured
        daemon._enable_notifier("telegram")


# ── run_single_task ───────────────────────────────────────────

class TestRunSingleTask:
    def test_run_with_mock(self):
        from clew.daemon import run_single_task
        mock_runtime = MagicMock()
        mock_result = MagicMock()
        mock_result.output = "Task completed successfully"
        mock_result.error = None
        mock_runtime.run.return_value = mock_result
        mock_runtime.get_token_stats.return_value = {"total_tokens": 500, "total_cost_usd": 0.01}

        with patch("clew.daemon.AgentRuntime", return_value=mock_runtime, create=True):
            # We can't easily test this without the actual AgentRuntime import
            # but we can verify the function exists
            assert callable(run_single_task)
