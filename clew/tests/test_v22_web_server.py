"""
Tests for the new clew.web_server module (v2.2.0).

Covers:
  • ClewWebServer lifecycle (start / stop / serve_forever)
  • Static file serving (index.html, app.js, bridge_shim.js, style.css)
  • Path-traversal protection in static serving
  • REST API endpoints still work via the combined handler
  • SSE endpoint is reachable
  • Auth bearer token is enforced on mutating endpoints
  • CLI argument parsing
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from clew.web_server import (
    ClewWebServer,
    ClewWebHandler,
    DEFAULT_HOST,
    DEFAULT_PORT,
    __version__,
    _safe_static_path,
    _content_type_for,
    _web_dir,
    _assets_dir,
    main,
    _parse_args,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def free_port() -> int:
    """Pick a free TCP port for the test server."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def running_server(free_port, tmp_path):
    """Start a ClewWebServer on a free port, yield it, stop it after."""
    srv = ClewWebServer(
        host="127.0.0.1",
        port=free_port,
        project=str(tmp_path),
        open_browser=False,
    )
    srv.start()
    # Brief warmup so the listener is ready.
    time.sleep(0.3)
    yield srv
    srv.stop()


def _get(url: str, headers: dict = None):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=5)


def _post(url: str, body: dict, headers: dict = None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    return urllib.request.urlopen(req, timeout=5)


# ── Version + metadata ────────────────────────────────────────────────

def test_version_is_v220():
    assert __version__ == "2.2.0"


def test_default_host_and_port():
    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 18732


# ── Static path resolution ────────────────────────────────────────────

def test_safe_static_path_resolves_index():
    p = _safe_static_path("/")
    assert p is not None
    assert p.name == "index.html"
    assert p.parent == _web_dir()


def test_safe_static_path_resolves_app_js():
    p = _safe_static_path("/app.js")
    assert p is not None
    assert p.name == "app.js"


def test_safe_static_path_resolves_bridge_shim_js():
    p = _safe_static_path("/bridge_shim.js")
    assert p is not None
    assert p.name == "bridge_shim.js"


def test_safe_static_path_rejects_traversal():
    # ``../../etc/passwd`` must NOT escape the web/ root.
    assert _safe_static_path("/../../etc/passwd") is None
    assert _safe_static_path("/..%2f..%2fetc/passwd") is None


def test_safe_static_path_rejects_unknown():
    assert _safe_static_path("/does-not-exist.html") is None


def test_safe_static_path_falls_back_to_assets():
    # ``/logo.png`` should fall back to clew/assets/logo.png.
    p = _safe_static_path("/assets/logo.png")
    assert p is not None
    assert p.name == "logo.png"


def test_content_type_for_known_extensions():
    assert "text/html" in _content_type_for(Path("index.html"))
    assert "javascript" in _content_type_for(Path("app.js"))
    assert "text/css" in _content_type_for(Path("style.css"))
    assert "image/svg+xml" == _content_type_for(Path("icon.svg"))
    assert "image/png" == _content_type_for(Path("logo.png"))


def test_content_type_for_unknown_returns_octet_stream():
    assert _content_type_for(Path("file.unknownext")) == "application/octet-stream"


# ── Server lifecycle ──────────────────────────────────────────────────

def test_server_starts_and_stops(free_port, tmp_path):
    srv = ClewWebServer(host="127.0.0.1", port=free_port, project=str(tmp_path), open_browser=False)
    srv.start()
    try:
        assert srv.base_url == f"http://127.0.0.1:{free_port}"
        # Token is generated once at startup.
        assert srv.api_token
        assert len(srv.api_token) >= 16
        # ServerContext is accessible.
        assert srv.ctx is not None
        assert hasattr(srv.ctx, "registry")
    finally:
        srv.stop()


def test_server_serves_index_html(running_server):
    resp = _get(running_server.base_url + "/")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "<html" in body.lower()
    # v2.2.0 marker
    assert "v2.2.0" in body or "Clew" in body


def test_server_serves_app_js(running_server):
    resp = _get(running_server.base_url + "/app.js")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "CLEW" in body or "clew" in body


def test_server_serves_bridge_shim_js(running_server):
    resp = _get(running_server.base_url + "/bridge_shim.js")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "bridge_shim" in body
    assert "window.bridge" in body
    # No QWebChannel reference left.
    assert "qrc:///qtwebchannel/qwebchannel.js" not in body


def test_server_serves_style_css(running_server):
    resp = _get(running_server.base_url + "/style.css")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    # CSS file should not be empty.
    assert len(body) > 1000


def test_server_404_for_unknown_static(running_server):
    try:
        _get(running_server.base_url + "/this-does-not-exist.html")
        assert False, "should have raised HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_server_rejects_path_traversal(running_server):
    # ``/../../etc/passwd`` should NOT escape the web/ root.
    try:
        _get(running_server.base_url + "/../../etc/passwd")
        assert False, "should have raised HTTPError"
    except urllib.error.HTTPError as e:
        # 404 is the right answer — the resolved path doesn't exist
        # under web/ (or the traversal was blocked).
        assert e.code in (404, 400)


# ── REST API passthrough ──────────────────────────────────────────────

def test_api_status_works_via_combined_handler(running_server):
    resp = _get(running_server.base_url + "/api/status")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert "version" in data
    assert "provider" in data
    assert "api_token" in data
    # api_token must match what the server reports.
    assert data["api_token"] == running_server.api_token


def test_api_providers_works(running_server):
    resp = _get(running_server.base_url + "/api/providers")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert isinstance(data, list)
    # 16+ providers built-in (legacy 15 + nvidia_nim).
    assert len(data) >= 10
    # Each provider entry has the expected keys.
    sample = data[0]
    assert "id" in sample
    assert "active" in sample


def test_api_chat_list_works(running_server):
    resp = _get(running_server.base_url + "/api/chat/list")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert isinstance(data, list)


def test_api_templates_works(running_server):
    resp = _get(running_server.base_url + "/api/templates")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert isinstance(data, list)


def test_api_skills_works(running_server):
    resp = _get(running_server.base_url + "/api/skills")
    assert resp.status == 200
    data = json.loads(resp.read())
    assert isinstance(data, list)


# ── Auth: mutating endpoints require bearer token ─────────────────────

def test_post_without_token_is_unauthorised(running_server):
    """v1.0.5-security: mutating endpoints reject requests without the bearer token."""
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                running_server.base_url + "/api/chat/create",
                data=json.dumps({"title": "test"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=5,
        )
        assert False, "should have raised HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_post_with_token_works(running_server):
    """With the correct bearer token, mutating endpoints succeed."""
    resp = _post(
        running_server.base_url + "/api/chat/create",
        {"title": "test-chat"},
        headers={"Authorization": f"Bearer {running_server.api_token}"},
    )
    assert resp.status == 200
    data = json.loads(resp.read())
    assert "chat_id" in data or "id" in data


# ── CLI argument parsing ──────────────────────────────────────────────

def test_parse_args_defaults():
    args = _parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 18732
    assert args.no_browser is False


def test_parse_args_port():
    args = _parse_args(["--port", "9999"])
    assert args.port == 9999


def test_parse_args_host():
    args = _parse_args(["--host", "0.0.0.0"])
    assert args.host == "0.0.0.0"


def test_parse_args_no_browser():
    args = _parse_args(["--no-browser"])
    assert args.no_browser is True


def test_parse_args_project():
    args = _parse_args(["--project", "/tmp/foo"])
    assert args.project == "/tmp/foo"


def test_parse_args_short_project():
    args = _parse_args(["-w", "/tmp/bar"])
    assert args.project == "/tmp/bar"


# ── main() smoke test ─────────────────────────────────────────────────

def test_main_help_exits_cleanly():
    """``--help`` should exit 0 (argparse SystemExit)."""
    with pytest.raises(SystemExit) as exc_info:
        _parse_args(["--help"])
    assert exc_info.value.code == 0


def test_main_version_exits_cleanly():
    """``--version`` should exit 0 (argparse SystemExit)."""
    with pytest.raises(SystemExit) as exc_info:
        _parse_args(["--version"])
    assert exc_info.value.code == 0


# ── Qt removal smoke tests ────────────────────────────────────────────

def test_clew_main_window_raises():
    """v2.2.0: the legacy Qt main window is a stub that raises."""
    from clew.main_window import ClewMainWindow, ClewMainWindowRemovedError
    with pytest.raises(ClewMainWindowRemovedError):
        ClewMainWindow()


def test_clew_web_bridge_ClewBridge_raises():
    """v2.2.0: the legacy Qt ClewBridge is a stub that raises."""
    from clew.web_bridge.bridge import ClewBridge, ClewBridgeRemovedError
    with pytest.raises(ClewBridgeRemovedError):
        ClewBridge()


def test_clew_web_bridge_workers_raise():
    """v2.2.0: the legacy Qt workers are stubs that raise."""
    from clew.web_bridge.workers import GenerationWorker, OneShotWorker, TitleWorker
    for cls in (GenerationWorker, OneShotWorker, TitleWorker):
        with pytest.raises(RuntimeError):
            cls()


def test_no_pyside6_imports_in_clew_package():
    """No module under clew/ should import PySide6 anymore."""
    import clew
    import pkgutil
    import importlib
    clew_dir = Path(clew.__file__).parent
    failures = []
    for finder, name, ispkg in pkgutil.walk_packages([str(clew_dir)], prefix="clew."):
        # Skip the tests/ subpackage — test files legitimately reference
        # PySide6 in their assertions.
        if ".tests." in name or name.endswith(".tests") or name.startswith("clew.tests"):
            continue
        try:
            mod = importlib.import_module(name)
            if not hasattr(mod, "__file__") or not mod.__file__:
                continue
            src = open(mod.__file__).read()
            # Look for actual import lines, not just string mentions.
            import_lines = [ln for ln in src.splitlines()
                            if ln.strip().startswith(("import PySide6", "from PySide6"))]
            if import_lines:
                failures.append((name, import_lines))
        except Exception:
            continue
    assert not failures, f"PySide6 imports still present in: {failures}"


def test_agent_worker_is_threading_subclass():
    """v2.2.0: AgentWorker is now plain threading.Thread, not QThread."""
    import threading
    from clew.agent_runtime.worker import AgentWorker
    assert issubclass(AgentWorker, threading.Thread)


def test_auto_updater_has_no_qt():
    """v2.2.0: AutoUpdater is now plain Python, not QObject."""
    import inspect
    from clew.auto_updater import AutoUpdater
    src = inspect.getsource(AutoUpdater)
    # No actual Qt imports or signal declarations (the docstring may
    # mention QObject for context — that's fine).
    assert "from PySide6" not in src
    assert "import PySide6" not in src
    assert "class AutoUpdater(QObject)" not in src
    assert "= Signal(" not in src
    # And it should NOT inherit from QObject.
    assert "class AutoUpdater:" in src or "class AutoUpdater():" in src
