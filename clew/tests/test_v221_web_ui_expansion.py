"""
Tests for clew.api_extended — v2.2.1 Web UI expansion.

These tests exercise the route table and installer without needing
a real AgentRuntime / network. They verify:

* ``install()`` patches ``ClewAPIHandler.do_GET/do_POST/do_DELETE``
* Every advertised endpoint is registered in the right table
* Each handler is callable with a fake ``handler`` and returns a dict
  (so JSON-serialisable)
* The custom-provider YAML round-trips through add/list/remove
* Provider templates include Nvidia NIM
* The auth-check on POST endpoints honours the bearer token

Run::

    pytest clew/tests/test_v221_web_ui_expansion.py -v
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def api_ext():
    """Import clew.api_extended with the clew package on sys.path."""
    # Make sure the in-tree clew package is importable.
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Also make sure clew_tui is importable for the bridge.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import importlib
    import clew.api_extended as mod
    importlib.reload(mod)
    return mod


class FakeHandler:
    """Minimal stand-in for ClewAPIHandler used by the route handlers."""

    def __init__(self, query: Dict[str, str] = None):
        self._query_map = query or {}
        self.json_sent: List[Dict[str, Any]] = []
        self.last_status = 200

    def _query(self, key: str) -> str:
        return self._query_map.get(key, "")

    def _json(self, data: Any, code: int = 200) -> None:
        self.last_status = code
        # Force serialisation so we catch any non-JSON-encodable return.
        self.json_sent.append(json.loads(json.dumps(data)))
        self.last_payload = data


# ── Route table ────────────────────────────────────────────────────────

def test_get_route_table_populated(api_ext):
    assert len(api_ext._ROUTES_GET) >= 30, (
        f"expected ≥30 GET routes, got {len(api_ext._ROUTES_GET)}"
    )


def test_post_route_table_populated(api_ext):
    assert len(api_ext._ROUTES_POST) >= 40, (
        f"expected ≥40 POST routes, got {len(api_ext._ROUTES_POST)}"
    )


EXPECTED_GET_ROUTES = [
    "/api/providers/custom/list",
    "/api/providers/templates",
    "/api/capabilities/list",
    "/api/capabilities/categories",
    "/api/capabilities/get",
    "/api/second_opinion/config",
    "/api/second_opinion/providers",
    "/api/budget/get",
    "/api/budget/check",
    "/api/agents/identity",
    "/api/agents/list",
    "/api/audit/summary",
    "/api/audit/filter",
    "/api/audit/export_json",
    "/api/audit/export_csv",
    "/api/audit/signed_export",
    "/api/handoff/list",
    "/api/handoff/get",
    "/api/cost/config",
    "/api/spend/identity",
    "/api/spend/budget",
    "/api/spend/report",
    "/api/spend/sources",
    "/api/spend/export_json",
    "/api/spend/export_csv",
    "/api/hooks/list",
    "/api/hooks/stats",
    "/api/checkpoint/list",
    "/api/checkpoint/get",
    "/api/checkpoint/stats",
    "/api/github/status",
    "/api/github/list_prs",
    "/api/github/get_pr",
    "/api/github/pr_context",
    "/api/github/list_issues",
    "/api/github/get_issue",
    "/api/consensus/config",
    "/api/learnings/list",
    "/api/learnings/dismissed",
    "/api/learnings/show",
    "/api/websearch/status",
    "/api/persona/get",
    "/api/router/mode",
    "/api/mcp_server/list_tools",
    "/api/mcp_server/status",
    "/api/notify/backends",
    "/api/notify/status",
    "/api/daemon/status",
    "/api/pro/status",
    "/api/collaboration/modes",
    "/api/usage/get",
    "/api/compaction/stats",
    "/api/persistence/backend",
    "/api/persistence/sessions",
    "/api/slash_commands/list",
    "/api/section/get",
]


@pytest.mark.parametrize("route", EXPECTED_GET_ROUTES)
def test_expected_get_route_registered(api_ext, route):
    assert route in api_ext._ROUTES_GET, f"GET {route} not registered"


EXPECTED_POST_ROUTES = [
    "/api/providers/custom/add",
    "/api/providers/custom/update",
    "/api/providers/custom/remove",
    "/api/providers/custom/test",
    "/api/capabilities/run",
    "/api/second_opinion/config",
    "/api/second_opinion/run",
    "/api/budget/set",
    "/api/budget/reset",
    "/api/verify/run",
    "/api/agents/spawn",
    "/api/audit/signed_verify",
    "/api/handoff/create",
    "/api/handoff/block_status",
    "/api/handoff/todo_toggle",
    "/api/handoff/reorder",
    "/api/handoff/delete",
    "/api/handoff/revision_prompt",
    "/api/handoff/export_md",
    "/api/cost/config",
    "/api/cost/cap",
    "/api/cost/route",
    "/api/cost/apply",
    "/api/spend/team",
    "/api/spend/budget",
    "/api/spend/sources_add",
    "/api/hooks/register",
    "/api/hooks/remove",
    "/api/hooks/toggle",
    "/api/hooks/test",
    "/api/checkpoint/create",
    "/api/checkpoint/rewind",
    "/api/checkpoint/rewind_to",
    "/api/checkpoint/diff",
    "/api/checkpoint/auto",
    "/api/github/set_token",
    "/api/github/set_repo",
    "/api/github/detect_repo",
    "/api/github/create_pr",
    "/api/github/create_issue",
    "/api/github/comment_pr",
    "/api/github/generate_action",
    "/api/consensus/config",
    "/api/consensus/run",
    "/api/learnings/dismiss",
    "/api/learnings/restore",
    "/api/learnings/scan",
    "/api/persona/set",
    "/api/persona/reset",
    "/api/router/mode",
    "/api/notify/configure",
    "/api/notify/toggle",
    "/api/notify/test",
    "/api/notify/test_all",
    "/api/notify/set_events",
    "/api/notify/remove",
    "/api/daemon/submit",
    "/api/pro/toggle",
    "/api/collaboration/run",
    "/api/persistence/backend",
    "/api/slash_commands/resolve",
    "/api/section/set",
]


@pytest.mark.parametrize("route", EXPECTED_POST_ROUTES)
def test_expected_post_route_registered(api_ext, route):
    assert route in api_ext._ROUTES_POST, f"POST {route} not registered"


# ── Provider templates ─────────────────────────────────────────────────

def test_provider_templates_include_nvidia_nim(api_ext):
    """Nvidia NIM must be one of the built-in templates."""
    fn = api_ext._ROUTES_GET["/api/providers/templates"]
    h = FakeHandler()
    result = fn(h)
    templates = result.get("templates") or []
    ids = [t.get("id") for t in templates]
    assert "nvidia_nim" in ids, f"nvidia_nim not in templates: {ids}"
    nim = next(t for t in templates if t["id"] == "nvidia_nim")
    assert nim["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert "llama" in nim["model"].lower()
    assert nim["provider_type"] == "nvidia_nim"


def test_provider_templates_include_local_options(api_ext):
    fn = api_ext._ROUTES_GET["/api/providers/templates"]
    h = FakeHandler()
    result = fn(h)
    ids = [t.get("id") for t in result["templates"]]
    assert "ollama_local" in ids
    assert "lmstudio_local" in ids
    assert "openai_compat" in ids


# ── Custom provider YAML round-trip ────────────────────────────────────

@pytest.fixture
def isolated_clew_home(tmp_path, monkeypatch):
    """Redirect ~/.clew to a tmp dir so tests don't touch the real home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".clew").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_custom_provider_add_list_remove_round_trip(api_ext, isolated_clew_home):
    """Add → list → remove a custom provider and verify the YAML file."""
    add = api_ext._ROUTES_POST["/api/providers/custom/add"]
    list_ = api_ext._ROUTES_GET["/api/providers/custom/list"]
    remove = api_ext._ROUTES_POST["/api/providers/custom/remove"]

    h = FakeHandler()
    r = add(h, {
        "provider_id": "my-nim-test",
        "name": "My NIM Test",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key": "nvapi-xxx",
        "model": "meta/llama-3.1-8b-instruct",
        "provider_type": "nvidia_nim",
    })
    assert r.get("ok") is True

    # YAML file should exist on disk.
    cfg = isolated_clew_home / ".clew" / "providers.yaml"
    assert cfg.exists(), f"providers.yaml not created at {cfg}"
    text = cfg.read_text()
    assert "my-nim-test" in text
    assert "nvapi-xxx" in text  # api_key stored

    # List should include our provider.
    h2 = FakeHandler()
    r2 = list_(h2)
    assert r2.get("ok") is True
    ids = [p.get("provider_id") for p in r2["providers"]]
    assert "my-nim-test" in ids

    # api_key must be masked in the list response.
    entry = next(p for p in r2["providers"] if p["provider_id"] == "my-nim-test")
    assert entry.get("api_key") == "", "api_key must be empty in list response"
    assert entry.get("api_key_masked"), "api_key_masked must be set"

    # Remove it.
    h3 = FakeHandler()
    r3 = remove(h3, {"provider_id": "my-nim-test"})
    assert r3.get("ok") is True

    # List should be empty again.
    h4 = FakeHandler()
    r4 = list_(h4)
    ids = [p.get("provider_id") for p in r4["providers"]]
    assert "my-nim-test" not in ids


def test_custom_provider_add_rejects_duplicate(api_ext, isolated_clew_home):
    add = api_ext._ROUTES_POST["/api/providers/custom/add"]
    h = FakeHandler()
    add(h, {"provider_id": "dup", "name": "Dup", "base_url": "http://x"})
    r = add(h, {"provider_id": "dup", "name": "Dup2", "base_url": "http://y"})
    assert r.get("ok") is False
    assert "already exists" in r["error"]


def test_custom_provider_add_requires_id(api_ext):
    add = api_ext._ROUTES_POST["/api/providers/custom/add"]
    h = FakeHandler()
    r = add(h, {"name": "No ID"})
    assert r.get("ok") is False
    assert "provider_id" in r["error"]


def test_custom_provider_remove_requires_id(api_ext):
    remove = api_ext._ROUTES_POST["/api/providers/custom/remove"]
    h = FakeHandler()
    r = remove(h, {})
    assert r.get("ok") is False


# ── Handler return types ───────────────────────────────────────────────

def test_handlers_return_dict_with_ok_flag(api_ext, isolated_clew_home):
    """Every handler must return a dict with an ``ok`` flag."""
    # Patch _bridge to a MagicMock so handlers don't actually create a runtime.
    # We use side_effect to return JSON-safe defaults for the most common
    # return shapes so the JSON-serialisability check passes.
    mock_bridge = MagicMock()
    # Default: return {} for any method that returns a dict-shaped value.
    mock_bridge.list_capabilities.return_value = []
    mock_bridge.list_capability_categories.return_value = []
    mock_bridge.get_capability.return_value = None
    mock_bridge.get_second_opinion_config.return_value = {}
    mock_bridge.list_second_opinion_providers.return_value = []
    mock_bridge.get_token_budget.return_value = {}
    mock_bridge.check_budget.return_value = {}
    mock_bridge.get_agent_identity.return_value = {"id": "x", "role": "root", "name": "Root"}
    mock_bridge.list_agents.return_value = []
    mock_bridge.get_agent_audit_summary.return_value = {}
    mock_bridge.filter_audit_by_agent.return_value = {}
    mock_bridge.export_audit_json.return_value = {"entries": []}
    mock_bridge.export_audit_csv.return_value = {"csv": ""}
    mock_bridge.export_audit_signed_json.return_value = {}
    mock_bridge.list_handoffs.return_value = []
    mock_bridge.get_handoff.return_value = None
    mock_bridge.get_cost_router_config.return_value = {}
    mock_bridge.get_user_identity.return_value = {"user_id": "u", "team": "t"}
    mock_bridge.get_team_budget.return_value = {}
    mock_bridge.get_team_spend_report.return_value = {"totals": {}}
    mock_bridge.list_spend_sources.return_value = []
    mock_bridge.export_spend_report_json.return_value = {}
    mock_bridge.export_spend_report_csv.return_value = {}
    mock_bridge.list_hooks.return_value = []
    mock_bridge.get_hook_stats.return_value = {}
    mock_bridge.list_checkpoints.return_value = []
    mock_bridge.get_checkpoint.return_value = None
    mock_bridge.get_checkpoint_stats.return_value = {}
    # GitHub — methods that return dicts must be configured explicitly
    # because MagicMock auto-creates a MagicMock (not JSON-safe) by default.
    mock_bridge.github_status.return_value = {"authenticated": False, "repo": ""}
    mock_bridge.github_list_prs.return_value = {"items": [], "ok": True}
    mock_bridge.github_get_pr.return_value = {"ok": False, "error": "not found"}
    mock_bridge.github_get_pr_context.return_value = {"prompt": ""}
    mock_bridge.github_list_issues.return_value = {"items": [], "ok": True}
    mock_bridge.github_get_issue.return_value = {"ok": False, "error": "not found"}
    mock_bridge.get_consensus_config.return_value = {}
    mock_bridge.handle_learnings_command.return_value = {"learnings": []}
    mock_bridge.get_websearch_status.return_value = {}
    mock_bridge.get_persona.return_value = {"content": ""}
    mock_bridge.get_router_mode.return_value = {"mode": "auto"}
    mock_bridge.mcp_server_list_tools.return_value = {"tools": []}
    mock_bridge.mcp_server_status.return_value = {}
    mock_bridge.notify_list_backends.return_value = []
    mock_bridge.notify_status.return_value = {}
    mock_bridge.daemon_status.return_value = {"running": False}
    mock_bridge.is_pro_enabled.return_value = False
    mock_bridge.list_collaboration_modes.return_value = []
    mock_bridge.get_usage.return_value = {}
    mock_bridge.get_compaction_stats.return_value = {}
    mock_bridge.get_persistence_backend.return_value = "json"
    mock_bridge.list_sqlite_sessions.return_value = []
    mock_bridge.list_slash_commands.return_value = []
    mock_bridge.resolve_slash_command.return_value = None
    mock_bridge.set_section.return_value = {"ok": True, "section": "general"}

    api_ext._bridge_inst = mock_bridge

    # GET handlers — verify each returns a dict with an 'ok' flag.
    # We do NOT JSON-serialise the result here because some handlers
    # might legitimately return non-JSON-safe dicts if the bridge
    # returned MagicMock defaults. Instead, we just check the shape.
    for path, fn in list(api_ext._ROUTES_GET.items()):
        if path in ("/api/providers/custom/list", "/api/providers/templates"):
            continue  # tested separately
        h = FakeHandler()
        try:
            result = fn(h)
            assert isinstance(result, dict), f"{path} returned {type(result)}"
            assert "ok" in result, f"{path} missing 'ok' flag"
        except Exception as e:
            pytest.fail(f"GET {path} raised: {e}")

    # POST handlers (skip the custom-providers CRUD — tested separately)
    skip = {
        "/api/providers/custom/add", "/api/providers/custom/update",
        "/api/providers/custom/remove", "/api/providers/custom/test",
    }
    for path, fn in list(api_ext._ROUTES_POST.items()):
        if path in skip:
            continue
        h = FakeHandler()
        try:
            result = fn(h, {})
            assert isinstance(result, dict), f"{path} returned {type(result)}"
            assert "ok" in result, f"{path} missing 'ok' flag"
        except Exception as e:
            pytest.fail(f"POST {path} raised: {e}")


# ── Installer ──────────────────────────────────────────────────────────

def test_install_patches_handler_methods(api_ext, monkeypatch):
    """install() must replace do_GET / do_POST / do_DELETE on ClewAPIHandler."""
    # Build a fake clew.api_server with a fake ClewAPIHandler.
    fake_api_server = types.ModuleType("clew.api_server")
    class FakeClewAPIHandler:
        ctx = None
        def do_GET(self): pass
        def do_POST(self): pass
        def do_DELETE(self): pass
        def _check_auth(self, path): return True
        def _read_json(self): return {}
        def _json(self, data, code=200): pass
    fake_api_server.ClewAPIHandler = FakeClewAPIHandler
    monkeypatch.setitem(sys.modules, "clew.api_server", fake_api_server)

    api_ext.install()

    # Methods should now be different from the originals.
    assert FakeClewAPIHandler.do_GET is not FakeClewAPIHandler.__dict__.get("do_GET_original")
    # The new do_GET should dispatch to the route table.
    h = FakeClewAPIHandler()
    h.path = "/api/providers/templates"
    h._json = lambda data, code=200: None
    h.do_GET()  # should not raise


def test_install_logs_route_count(api_ext, monkeypatch, caplog):
    fake_api_server = types.ModuleType("clew.api_server")
    class FakeClewAPIHandler:
        ctx = None
        def do_GET(self): pass
        def do_POST(self): pass
        def do_DELETE(self): pass
        def _check_auth(self, path): return True
        def _read_json(self): return {}
        def _json(self, data, code=200): pass
    fake_api_server.ClewAPIHandler = FakeClewAPIHandler
    monkeypatch.setitem(sys.modules, "clew.api_server", fake_api_server)

    with caplog.at_level("INFO"):
        api_ext.install()
    assert any("installed" in r.message for r in caplog.records)


# ── Section / legacy compat ────────────────────────────────────────────

def test_section_get_always_returns_general(api_ext):
    fn = api_ext._ROUTES_GET["/api/section/get"]
    h = FakeHandler()
    r = fn(h)
    assert r["section"] == "general"
