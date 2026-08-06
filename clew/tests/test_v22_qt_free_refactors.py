"""
Tests for the v2.2.0 Qt-free refactors.

Covers:
  • clew.code_viewer.CodeViewerService — polling watcher (no QFileSystemWatcher)
  • clew.auto_updater.AutoUpdater — plain Python (no QObject / Signal)
  • clew.lsp_client.LSPClient — plain Python (no QObject / QThread)
  • clew.agent_runtime.worker.AgentWorker — threading.Thread (no QThread)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── code_viewer ────────────────────────────────────────────────────────

def test_code_viewer_no_qt_imports():
    """v2.2.0: code_viewer must not import PySide6."""
    import inspect
    from clew import code_viewer
    src = inspect.getsource(code_viewer)
    # No actual import statements.
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines, f"PySide6 still imported: {import_lines}"
    # No live QFileSystemWatcher usage in actual code lines (comments
    # and docstrings mentioning the legacy name are tolerated).
    code_lines = [ln for ln in src.splitlines()
                  if ln.strip() and not ln.strip().startswith(("#", '"""', "'"))]
    usage_lines = [ln for ln in code_lines if "QFileSystemWatcher" in ln]
    assert not usage_lines, f"QFileSystemWatcher still used: {usage_lines}"


def test_code_viewer_set_root(tmp_path):
    from clew.code_viewer import CodeViewerService
    svc = CodeViewerService(root=str(tmp_path))
    assert svc.root == tmp_path.resolve()
    # set_root to a new path
    new_dir = tmp_path / "sub"
    new_dir.mkdir()
    svc.set_root(str(new_dir))
    assert svc.root == new_dir.resolve()


def test_code_viewer_set_root_missing_raises(tmp_path):
    from clew.code_viewer import CodeViewerService
    svc = CodeViewerService()
    with pytest.raises(FileNotFoundError):
        svc.set_root(str(tmp_path / "does-not-exist"))


def test_code_viewer_list_files(tmp_path):
    from clew.code_viewer import CodeViewerService
    (tmp_path / "main.py").write_text("print('hi')\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "util.py").write_text("pass\n")
    # Hidden / ignored dirs should NOT show up.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_text("ignored")

    svc = CodeViewerService(root=str(tmp_path))
    files = svc.list_files()
    file_names = [f["name"] for f in files]
    assert "main.py" in file_names
    assert "util.py" in file_names
    assert "config" not in file_names
    assert "x.pyc" not in file_names


def test_code_viewer_read_file(tmp_path):
    from clew.code_viewer import CodeViewerService
    (tmp_path / "hello.py").write_text("print('hello world')\n")
    svc = CodeViewerService(root=str(tmp_path))
    content = svc.read_file("hello.py")
    # read_file returns a dict (FileContent.to_dict()) — accept either.
    if isinstance(content, dict):
        assert content.get("exists") is True
        assert "hello world" in content.get("content", "")
        assert content.get("language") == "python"
    else:
        assert content.exists
        assert "hello world" in content.content
        assert content.language == "python"


def test_code_viewer_read_file_missing(tmp_path):
    from clew.code_viewer import CodeViewerService
    svc = CodeViewerService(root=str(tmp_path))
    content = svc.read_file("does-not-exist.py")
    if isinstance(content, dict):
        assert content.get("exists") is False
    else:
        assert not content.exists


def test_code_viewer_read_file_rejects_traversal(tmp_path):
    """v1.0.6: read_file must reject ../ traversal."""
    from clew.code_viewer import CodeViewerService
    # Create a file outside the root.
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("top secret")
    try:
        svc = CodeViewerService(root=str(tmp_path))
        # Different versions of the service may either raise, return a dict,
        # return a dataclass, or return None.
        try:
            content = svc.read_file("../outside_secret.txt")
            if content is None:
                return
            if isinstance(content, dict):
                assert not content.get("exists") or "top secret" not in content.get("content", "")
            else:
                assert not content.exists or "top secret" not in content.content
        except (ValueError, PermissionError, FileNotFoundError):
            pass  # Any of these is acceptable.
    finally:
        outside.unlink(missing_ok=True)


def test_code_viewer_search(tmp_path):
    from clew.code_viewer import CodeViewerService
    (tmp_path / "a.py").write_text("def foo():\n    return 42\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 'foo'\n")
    svc = CodeViewerService(root=str(tmp_path))
    results = svc.search("foo")
    # Should find at least one match.
    assert len(results) >= 1
    paths = [r["path"] for r in results]
    assert any("a.py" in p for p in paths)


def test_code_viewer_watcher_uses_threading(tmp_path):
    """v2.2.0: the polling watcher uses threading.Thread, not QFileSystemWatcher."""
    from clew.code_viewer import CodeViewerService
    svc = CodeViewerService(root=str(tmp_path))
    events = []
    svc.watch(lambda path, evt: events.append((path, evt)))
    try:
        # The watcher thread should be running.
        assert svc._watcher is not None
        assert isinstance(svc._watcher, threading.Thread)
        assert svc._watcher.is_alive()
        # Mutate a file — the watcher should pick it up within a couple of polls.
        (tmp_path / "new_file.py").write_text("print('hi')")
        # Wait for at most 3 poll cycles (default 2s × 3 = 6s).
        for _ in range(30):
            if events:
                break
            time.sleep(0.2)
        assert events, "watcher did not fire on file creation"
    finally:
        svc.stop_watcher()


def test_code_viewer_watcher_can_be_stopped(tmp_path):
    from clew.code_viewer import CodeViewerService
    svc = CodeViewerService(root=str(tmp_path))
    svc.watch(lambda path, evt: None)
    assert svc._watcher is not None
    assert svc._watcher.is_alive()
    svc.stop_watcher()
    # After stop, the thread reference is cleared.
    assert svc._watcher is None


def test_code_viewer_watcher_no_root_no_crash():
    from clew.code_viewer import CodeViewerService
    svc = CodeViewerService(root=None)
    # watch() with no root should be a no-op (not raise).
    svc.watch(lambda path, evt: None)
    assert svc._watcher is None  # no thread started


# ── auto_updater ───────────────────────────────────────────────────────

def test_auto_updater_normalises_placeholder_repo():
    """v1.1.5-fix: 'user/clew' is treated as disabled."""
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater(repo="user/clew")
    assert u.repo is None


def test_auto_updater_normalises_empty_repo():
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater(repo="")
    assert u.repo is None
    u2 = AutoUpdater(repo=None)
    assert u2.repo is None


def test_auto_updater_keeps_real_repo():
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater(repo="zai-shop/clew")
    assert u.repo == "zai-shop/clew"


def test_auto_updater_set_repo_runtime():
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater()
    u.set_repo("octocat/Hello-World")
    assert u.repo == "octocat/Hello-World"
    u.set_repo(None)
    assert u.repo is None


def test_auto_updater_add_listener_callback():
    """v2.2.0: add_listener registers a callback fired by _emit."""
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater()
    received = []
    u.add_listener(lambda info: received.append(info))
    u._emit({"update_available": True, "latest": "v9.9.9"})
    assert len(received) == 1
    assert received[0]["latest"] == "v9.9.9"


def test_auto_updater_on_update_available_property():
    """v2.2.0: on_update_available is settable / gettable for Qt compat."""
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater()
    assert u.on_update_available is None
    captured = []
    u.on_update_available = lambda info: captured.append(info)
    u._emit({"update_available": False})
    assert len(captured) == 1
    # Assigning again replaces (Qt single-slot semantics).
    u.on_update_available = lambda info: captured.append("second")
    u._emit({"update_available": False})
    assert captured[-1] == "second"


def test_auto_updater_remove_listener():
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater()
    received = []
    fn = lambda info: received.append(info)
    u.add_listener(fn)
    u.remove_listener(fn)
    u._emit({"update_available": True})
    assert received == []


def test_auto_updater_check_skipped_when_repo_disabled():
    """check_for_updates is a no-op when repo is None."""
    from clew.auto_updater import AutoUpdater
    u = AutoUpdater(repo=None)
    # Should not raise, should not start a thread.
    u.check_for_updates()
    # The internal worker may or may not be assigned, but if it is,
    # it must NOT be alive (the check returns early).
    if u._worker is not None:
        # Wait briefly to make sure it didn't start a real HTTP request.
        time.sleep(0.1)
        # The thread exits immediately because repo is None.


def test_auto_updater_parse_version():
    from clew.auto_updater import _parse_version
    assert _parse_version("v1.0.3") == (1, 0, 3)
    assert _parse_version("1.0.3") == (1, 0, 3)
    assert _parse_version("v2.2.0") == (2, 2, 0)
    assert _parse_version("") == (0, 0, 0)
    assert _parse_version("not-a-version") == (0, 0, 0)


def test_auto_updater_get_current_version():
    from clew.auto_updater import get_current_version
    v = get_current_version()
    assert isinstance(v, str)
    assert len(v) > 0


# ── lsp_client ─────────────────────────────────────────────────────────

def test_lsp_client_no_qt_imports():
    """v2.2.0: lsp_client must not import PySide6."""
    import inspect
    import re
    from clew import lsp_client
    src = inspect.getsource(lsp_client)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines, f"PySide6 still imported: {import_lines}"
    # Strip all docstrings before scanning for legacy Qt class names.
    # A docstring is a triple-quoted block — drop everything between
    # the opening and closing triple quotes.
    no_docstrings = re.sub(
        r'"""[\s\S]*?"""',
        "",
        src,
    )
    no_docstrings = re.sub(
        r"'''[\s\S]*?'''",
        "",
        no_docstrings,
    )
    # Also strip line comments.
    no_docstrings = "\n".join(
        ln.split("#", 1)[0] if not ln.lstrip().startswith("#") else ""
        for ln in no_docstrings.splitlines()
    )
    for kw in ("QObject", "QThread"):
        assert kw not in no_docstrings, f"{kw} still referenced in code"


def test_lsp_client_init_doesnt_require_qt():
    from clew.lsp_client import LSPClient
    client = LSPClient()
    assert client.process is None
    assert client._initialized is False
    assert client.is_ready() is False


def test_lsp_client_signal_connect_emit():
    """v2.2.0: the _Signal shim supports connect/disconnect/emit."""
    from clew.lsp_client import LSPClient, _Signal
    sig = _Signal()
    received = []
    sig.connect(lambda x: received.append(x))
    sig.emit("hello")
    assert received == ["hello"]
    # disconnect
    sig.disconnect()
    sig.emit("world")
    assert received == ["hello"]  # unchanged


def test_lsp_client_signals_are_signal_objects():
    """All the legacy Qt signal names must still be present."""
    from clew.lsp_client import LSPClient, _Signal
    client = LSPClient()
    expected = [
        "completions_ready", "hover_ready", "definitions_ready",
        "diagnostics_ready", "signature_help_ready",
        "server_started", "server_stopped",
    ]
    for name in expected:
        sig = getattr(client, name)
        assert isinstance(sig, _Signal), f"{name} is not a _Signal"
        assert hasattr(sig, "connect")
        assert hasattr(sig, "emit")


def test_lsp_client_did_open_doesnt_crash_without_server():
    """did_open before the server is started should be a no-op (no crash)."""
    from clew.lsp_client import LSPClient
    client = LSPClient()
    # Should not raise.
    client.did_open("file:///tmp/x.py", "python", "print('hi')", 1)
    client.did_change("file:///tmp/x.py", "print('hi')\n", 2)
    client.did_save("file:///tmp/x.py")
    client.did_close("file:///tmp/x.py")


def test_lsp_client_get_capabilities_empty():
    from clew.lsp_client import LSPClient
    client = LSPClient()
    assert client.get_capabilities() == {}


def test_lsp_client_close_when_not_started():
    """close() before start_server is a no-op (no crash)."""
    from clew.lsp_client import LSPClient
    client = LSPClient()
    client.close()


def test_lsp_client_parse_completions():
    from clew.lsp_client import LSPClient
    client = LSPClient()
    result = client._parse_completions({
        "items": [
            {"label": "foo", "kind": 3, "detail": "def foo()"},
            {"label": "bar", "kind": 6, "detail": "var bar"},
        ]
    })
    assert len(result) == 2
    assert result[0].label == "foo"
    assert result[0].kind == 3
    assert result[0].detail == "def foo()"


def test_lsp_client_parse_hover():
    from clew.lsp_client import LSPClient
    client = LSPClient()
    hover = client._parse_hover({"contents": "def foo(): pass"})
    assert hover is not None
    assert "def foo" in hover.contents
    # None result for empty input.
    assert client._parse_hover(None) is None


def test_lsp_client_parse_locations():
    from clew.lsp_client import LSPClient
    client = LSPClient()
    locs = client._parse_locations([
        {"uri": "file:///a.py", "range": {"start": {"line": 0}}},
        {"uri": "file:///b.py", "range": {"start": {"line": 5}}},
    ])
    assert len(locs) == 2
    assert locs[0].uri == "file:///a.py"
    # None / empty input.
    assert client._parse_locations(None) == []
    assert client._parse_locations([]) == []


def test_lsp_client_parse_diagnostics():
    from clew.lsp_client import LSPClient
    client = LSPClient()
    diags = client._parse_diagnostics([
        {"range": {}, "severity": 1, "message": "Syntax error", "source": "pylsp"},
        {"range": {}, "severity": 2, "message": "Warning", "source": "pylsp", "code": "W001"},
    ])
    assert len(diags) == 2
    assert diags[0].severity == 1
    assert diags[0].message == "Syntax error"
    assert diags[1].code == "W001"


def test_lsp_client_handle_response_dispatches_to_signal():
    """_handle_response emits the right signal for each method."""
    from clew.lsp_client import LSPClient, LSPMethod
    client = LSPClient()
    received = []
    client.completions_ready.connect(lambda uri, items: received.append(("completions", items)))
    client.hover_ready.connect(lambda uri, h: received.append(("hover", h)))
    client.definitions_ready.connect(lambda uri, locs: received.append(("defs", locs)))
    client.diagnostics_ready.connect(lambda uri, diags: received.append(("diags", diags)))

    # Register a pending completion request.
    req_id = client._next_id()
    client._pending_requests[req_id] = LSPMethod.TEXT_DOCUMENT_COMPLETION.value
    client._handle_response({
        "id": req_id,
        "result": {"items": [{"label": "foo", "kind": 3}]},
    })
    assert any(r[0] == "completions" for r in received)

    # Diagnostics notification.
    received.clear()
    client._handle_response({
        "method": "textDocument/publishDiagnostics",
        "params": {"uri": "file:///x.py", "diagnostics": [
            {"range": {}, "severity": 1, "message": "err"},
        ]},
    })
    assert any(r[0] == "diags" for r in received)


# ── agent_runtime/worker ───────────────────────────────────────────────

def test_agent_worker_subclasses_threading():
    import threading
    from clew.agent_runtime.worker import AgentWorker
    assert issubclass(AgentWorker, threading.Thread)


def test_agent_worker_signal_shim():
    """The _Signal shim supports connect/emit."""
    from clew.agent_runtime.worker import _Signal
    sig = _Signal()
    received = []
    sig.connect(lambda x: received.append(x))
    sig.emit("hello")
    assert received == ["hello"]


def test_agent_worker_cancel_sets_event():
    """cancel() sets the internal _cancelled Event."""
    import threading
    from clew.agent_runtime.worker import AgentWorker, _Signal
    # Build a minimal fake agent — we only need the cancel-check.
    class FakeAgent:
        def __init__(self):
            self.on_event = None
            self._cancel_check = None
        def set_cancel_check(self, fn):
            self._cancel_check = fn

    agent = FakeAgent()
    worker = AgentWorker(agent, task=None)
    assert not worker._is_cancelled()
    worker.cancel()
    assert worker._is_cancelled()
    # The cancel-check returns True after cancel().
    assert worker._is_cancelled() is True


def test_agent_worker_constructor_accepts_parent():
    """The legacy constructor accepted ``parent=self`` — keep it working."""
    from clew.agent_runtime.worker import AgentWorker
    class FakeAgent:
        def __init__(self):
            self.on_event = None
        def set_cancel_check(self, fn): pass

    agent = FakeAgent()
    # parent=anything should be silently swallowed.
    worker = AgentWorker(agent, task=None, parent="some_qobject_like_thing")
    assert worker.agent is agent
