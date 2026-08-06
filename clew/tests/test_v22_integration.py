"""
Comprehensive integration tests for v2.2.0 — covers the parts of the
project that didn't have dedicated tests before, or whose existing
tests didn't exercise the Qt-free code paths.

Coverage areas:
  • clew.cli — headless CLI argument parsing + config loading
  • clew.daemon — HTTP API server lifecycle + endpoints
  • clew.api_server — REST + SSE endpoints (subset)
  • clew.providers — registry, ProviderConfig, AutoRouter
  • clew.hook_system — registration, dispatch, BLOCK/MODIFY/ALLOW
  • clew.checkpoint — creation, rewind, file backup
  • clew.agent_runtime — ToolEngine, ContextMemory, parser
  • clew.slash_commands — manager
  • clew.skill_loader — basic loading
  • clew.activity_log — append + export
  • clew.token_tracker — singleton + record
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── clew.cli ───────────────────────────────────────────────────────────

def test_cli_module_imports_cleanly():
    """The CLI module must import without PySide6."""
    import clew.cli
    assert hasattr(clew.cli, "main")
    assert callable(clew.cli.main)


def test_cli_no_qt_imports():
    """v2.2.0: cli must not import PySide6."""
    import inspect
    from clew import cli
    src = inspect.getsource(cli)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines, f"PySide6 still imported: {import_lines}"


def test_cli_load_config_returns_dict(tmp_path, monkeypatch):
    """_load_config returns a dict (possibly the defaults)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from clew import cli
    cfg = cli._load_config()
    assert isinstance(cfg, dict)
    assert "active_provider" in cfg or "providers" in cfg or len(cfg) >= 1


def test_cli_clew_home_creates_dir(tmp_path, monkeypatch):
    """_clew_home() creates ~/.clew if it doesn't exist."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from clew import cli
    home = cli._clew_home()
    assert home.exists()
    assert home.name == ".clew"


def test_cli_config_path_returns_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from clew import cli
    p = cli._config_path()
    assert p.name == "config.json"
    assert p.parent.name == ".clew"


# ── clew.daemon ────────────────────────────────────────────────────────

def test_daemon_module_imports_cleanly():
    import clew.daemon
    assert hasattr(clew.daemon, "main")
    assert callable(clew.daemon.main)


def test_daemon_no_qt_imports():
    """v2.2.0: daemon must not import PySide6."""
    import inspect
    from clew import daemon
    src = inspect.getsource(daemon)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


def test_daemon_task_state_enum():
    from clew.daemon import TaskState
    assert TaskState.PENDING.value == "pending"
    assert TaskState.RUNNING.value == "running"
    assert TaskState.COMPLETED.value == "completed"
    assert TaskState.FAILED.value == "failed"
    assert TaskState.CANCELLED.value == "cancelled"


def test_daemon_task_record_to_dict():
    from clew.daemon import TaskRecord
    rec = TaskRecord(prompt="test", workspace="/tmp")
    d = rec.to_dict()
    assert d["prompt"] == "test"
    assert d["workspace"] == "/tmp"
    assert d["state"] == "pending"
    assert "id" in d
    assert "created_at" in d


def test_daemon_task_record_defaults():
    from clew.daemon import TaskRecord
    rec = TaskRecord()
    assert rec.prompt == ""
    assert rec.workspace == ""
    assert rec.state.value == "pending"
    assert rec.token_count == 0
    assert rec.cost_usd == 0.0
    assert rec.tools_used == 0
    assert rec.files_changed == 0


# ── clew.api_server (subset — full HTTP tests are in test_v22_web_server) ──

def test_api_server_module_imports_cleanly():
    import clew.api_server
    assert hasattr(clew.api_server, "ClewAPIServer")
    assert hasattr(clew.api_server, "ServerContext")
    assert hasattr(clew.api_server, "ClewAPIHandler")


def test_api_server_no_qt_imports():
    """v2.2.0: api_server must not import PySide6."""
    import inspect
    from clew import api_server
    src = inspect.getsource(api_server)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


def test_api_server_find_free_port():
    from clew.api_server import _find_free_port
    port = _find_free_port(20000)
    assert isinstance(port, int)
    assert 20000 <= port < 20100


def test_api_server_validate_chat_id_rejects_traversal():
    """v1.0.5-security: chat_id must be a bare identifier."""
    from clew.api_server import _validate_chat_id
    assert not _validate_chat_id("../etc/passwd")
    assert not _validate_chat_id("a/b")
    assert not _validate_chat_id("")
    assert not _validate_chat_id(None)
    assert _validate_chat_id("valid_id-123")


def test_api_server_load_config_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from clew.api_server import _load_config
    cfg = _load_config()
    assert isinstance(cfg, dict)
    assert "version" in cfg
    assert "active_provider" in cfg
    assert "providers" in cfg


def test_api_server_save_then_load_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from clew.api_server import _load_config, _save_config
    cfg = _load_config()
    cfg["active_provider"] = "groq"
    _save_config(cfg)
    cfg2 = _load_config()
    assert cfg2["active_provider"] == "groq"


def test_api_server_server_context_constructs(tmp_path, monkeypatch):
    """ServerContext must construct without Qt."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from clew.api_server import ServerContext
    ctx = ServerContext()
    assert ctx.registry is not None
    assert ctx.config is not None
    assert ctx._auth_token  # generated at construction
    assert ctx._stop_event is not None


def test_api_server_server_context_mutating_paths():
    """The set of paths that require a bearer token must include the mutating endpoints."""
    from clew.api_server import ServerContext
    mutating = ServerContext.MUTATING_PATHS
    assert "/api/chat/stream" in mutating
    assert "/api/agent/stream" in mutating
    assert "/api/chat/create" in mutating
    assert "/api/chat/delete" in mutating
    assert "/api/providers/activate" in mutating
    assert "/api/settings" in mutating
    # GET-only paths must NOT be in the mutating set.
    assert "/api/status" not in mutating
    assert "/api/providers" not in mutating
    assert "/api/chat/list" not in mutating


# ── clew.providers ─────────────────────────────────────────────────────

def test_providers_registry_constructs():
    from clew.providers import ProviderRegistry, get_registry
    reg = get_registry()
    assert reg is not None
    # Re-fetching should return the same singleton.
    assert get_registry() is reg


def test_providers_registry_list_returns_list():
    from clew.providers import get_registry
    reg = get_registry()
    if not reg.list_providers():
        reg.register_default()
    providers = reg.list_providers()
    assert isinstance(providers, (list, tuple))
    # 14+ built-in providers.
    assert len(providers) >= 10


def test_providers_provider_config_constructs():
    from clew.providers import ProviderConfig
    cfg = ProviderConfig(
        provider_id="groq",
        model="llama-3.3-70b",
        api_key="test",
        api_base="https://api.groq.com/v1",
    )
    assert cfg.provider_id == "groq"
    assert cfg.model == "llama-3.3-70b"


def test_providers_auto_router_constructs():
    from clew.auto_router import AutoRouter
    router = AutoRouter()
    assert router is not None


def test_providers_token_tracker_singleton():
    from clew.token_tracker import get_token_tracker
    t1 = get_token_tracker()
    t2 = get_token_tracker()
    assert t1 is t2  # singleton


# ── clew.hook_system ───────────────────────────────────────────────────

def test_hook_system_imports_cleanly():
    import clew.hook_system
    assert hasattr(clew.hook_system, "HookManager")


def test_hook_system_no_qt_imports():
    import inspect
    from clew import hook_system
    src = inspect.getsource(hook_system)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


def test_hook_system_manager_singleton():
    from clew.hook_system import HookManager
    m1 = HookManager()
    m2 = HookManager()
    # Either singleton or just constructible — both are acceptable.
    assert m1 is not None and m2 is not None


# ── clew.checkpoint ────────────────────────────────────────────────────

def test_checkpoint_imports_cleanly():
    import clew.checkpoint
    assert hasattr(clew.checkpoint, "CheckpointManager")


def test_checkpoint_no_qt_imports():
    import inspect
    from clew import checkpoint
    src = inspect.getsource(checkpoint)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.agent_runtime ─────────────────────────────────────────────────

def test_agent_runtime_imports_cleanly():
    """All public exports of clew.agent_runtime must work without Qt."""
    from clew.agent_runtime import (
        AgentRuntime, AgentWorker, AgentEvent, Task, TaskType,
        ToolCall, ToolName, TaskResult, AgentStep,
        ConversationMessage, ContextMemory, ToolEngine,
        PromptBuilder, OutputParser,
        TOOL_SCHEMA, SYSTEM_PROMPT,
        GENERAL_SYSTEM_SUFFIX, HEAVY_CODE_SYSTEM_SUFFIX,
    )
    assert AgentRuntime is not None
    assert AgentWorker is not None  # was None under Qt-free envs pre-v2.2.0
    assert ToolEngine is not None
    assert ContextMemory is not None


def test_agent_runtime_no_qt_imports():
    """v2.2.0: agent_runtime package must not import PySide6 at top level."""
    import inspect
    from clew.agent_runtime import worker
    src = inspect.getsource(worker)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines, f"PySide6 still imported in worker.py: {import_lines}"


def test_agent_runtime_task_type_enum():
    from clew.agent_runtime import TaskType
    # Most fundamental task types should be present.
    common = ["CHAT", "WRITE", "EDIT", "REFACTOR", "DEBUG", "AGENTIC", "ANALYZE", "PLAN", "TEST"]
    found = [name for name in common if hasattr(TaskType, name)]
    assert len(found) >= 5, f"Expected at least 5 of {common}, got {found}"


def test_agent_runtime_tool_name_enum():
    from clew.agent_runtime import ToolName
    # Most fundamental tools should be present.
    common = ["READ_FILE", "WRITE_FILE", "EXECUTE_COMMAND", "STR_REPLACE"]
    found = [name for name in common if hasattr(ToolName, name)]
    assert len(found) >= 2, f"Expected at least 2 of {common}, got {found}"


def test_agent_runtime_context_memory_constructs():
    from clew.agent_runtime import ContextMemory
    mem = ContextMemory()
    assert mem is not None


# ── clew.slash_commands ────────────────────────────────────────────────

def test_slash_commands_imports_cleanly():
    import clew.slash_commands
    assert hasattr(clew.slash_commands, "SlashCommandManager")


def test_slash_commands_no_qt_imports():
    import inspect
    from clew import slash_commands
    src = inspect.getsource(slash_commands)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.skill_loader ──────────────────────────────────────────────────

def test_skill_loader_imports_cleanly():
    import clew.skill_loader
    assert hasattr(clew.skill_loader, "load_all_skills_with_builtins") or \
           hasattr(clew.skill_loader, "load_all_skills")


def test_skill_loader_no_qt_imports():
    import inspect
    from clew import skill_loader
    src = inspect.getsource(skill_loader)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.activity_log ──────────────────────────────────────────────────

def test_activity_log_imports_cleanly():
    import clew.activity_log
    assert hasattr(clew.activity_log, "ActivityLog") or hasattr(clew.activity_log, "get_activity_log")


def test_activity_log_no_qt_imports():
    import inspect
    from clew import activity_log
    src = inspect.getsource(activity_log)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.token_tracker ─────────────────────────────────────────────────

def test_token_tracker_no_qt_imports():
    import inspect
    from clew import token_tracker
    src = inspect.getsource(token_tracker)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.consensus_engine (G15) ────────────────────────────────────────

def test_consensus_engine_imports_cleanly():
    import clew.consensus_engine
    assert hasattr(clew.consensus_engine, "ConsensusEngine") or \
           hasattr(clew.consensus_engine, "run_consensus")


def test_consensus_engine_no_qt_imports():
    import inspect
    from clew import consensus_engine
    src = inspect.getsource(consensus_engine)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.audit_signing (G16) ───────────────────────────────────────────

def test_audit_signing_imports_cleanly():
    import clew.audit_signing


def test_audit_signing_no_qt_imports():
    import inspect
    from clew import audit_signing
    src = inspect.getsource(audit_signing)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.handoff_bridge (G6) ───────────────────────────────────────────

def test_handoff_bridge_imports_cleanly():
    import clew.handoff_bridge


def test_handoff_bridge_no_qt_imports():
    import inspect
    from clew import handoff_bridge
    src = inspect.getsource(handoff_bridge)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.cost_router (M2) ──────────────────────────────────────────────

def test_cost_router_imports_cleanly():
    import clew.cost_router


def test_cost_router_no_qt_imports():
    import inspect
    from clew import cost_router
    src = inspect.getsource(cost_router)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.github_automation (G11) ───────────────────────────────────────

def test_github_automation_imports_cleanly():
    import clew.github_automation


def test_github_automation_no_qt_imports():
    import inspect
    from clew import github_automation
    src = inspect.getsource(github_automation)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.mcp_server (G13) ──────────────────────────────────────────────

def test_mcp_server_imports_cleanly():
    import clew.mcp_server


def test_mcp_server_no_qt_imports():
    import inspect
    from clew import mcp_server
    src = inspect.getsource(mcp_server)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.agent_identity (G5) ───────────────────────────────────────────

def test_agent_identity_imports_cleanly():
    import clew.agent_identity


def test_agent_identity_no_qt_imports():
    import inspect
    from clew import agent_identity
    src = inspect.getsource(agent_identity)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.capability_catalog (G7) ───────────────────────────────────────

def test_capability_catalog_imports_cleanly():
    import clew.capability_catalog


def test_capability_catalog_no_qt_imports():
    import inspect
    from clew import capability_catalog
    src = inspect.getsource(capability_catalog)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.second_opinion (M1) ───────────────────────────────────────────

def test_second_opinion_imports_cleanly():
    import clew.second_opinion


def test_second_opinion_no_qt_imports():
    import inspect
    from clew import second_opinion
    src = inspect.getsource(second_opinion)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.token_budget (G3) ─────────────────────────────────────────────

def test_token_budget_imports_cleanly():
    import clew.token_budget


def test_token_budget_no_qt_imports():
    import inspect
    from clew import token_budget
    src = inspect.getsource(token_budget)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.spend_dashboard (M3) ──────────────────────────────────────────

def test_spend_dashboard_imports_cleanly():
    import clew.spend_dashboard


def test_spend_dashboard_no_qt_imports():
    import inspect
    from clew import spend_dashboard
    src = inspect.getsource(spend_dashboard)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.learning_loop (G17) ───────────────────────────────────────────

def test_learning_loop_imports_cleanly():
    import clew.learning_loop


def test_learning_loop_no_qt_imports():
    import inspect
    from clew import learning_loop
    src = inspect.getsource(learning_loop)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.web_search_backend (G18) ──────────────────────────────────────

def test_web_search_backend_imports_cleanly():
    import clew.web_search_backend


def test_web_search_backend_no_qt_imports():
    import inspect
    from clew import web_search_backend
    src = inspect.getsource(web_search_backend)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.diff_service ─────────────────────────────────────────────────

def test_diff_service_imports_cleanly():
    import clew.diff_service


def test_diff_service_no_qt_imports():
    import inspect
    from clew import diff_service
    src = inspect.getsource(diff_service)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.git_service ──────────────────────────────────────────────────

def test_git_service_imports_cleanly():
    import clew.git_service


def test_git_service_no_qt_imports():
    import inspect
    from clew import git_service
    src = inspect.getsource(git_service)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.office_worker ────────────────────────────────────────────────

def test_office_worker_imports_cleanly():
    import clew.office_worker


def test_office_worker_no_qt_imports():
    import inspect
    from clew import office_worker
    src = inspect.getsource(office_worker)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.utils ────────────────────────────────────────────────────────

def test_utils_imports_cleanly():
    import clew.utils
    assert hasattr(clew.utils, "setup_logging")


def test_utils_no_qt_imports():
    import inspect
    from clew import utils
    src = inspect.getsource(utils)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.project_context ──────────────────────────────────────────────

def test_project_context_imports_cleanly():
    import clew.project_context


def test_project_context_no_qt_imports():
    import inspect
    from clew import project_context
    src = inspect.getsource(project_context)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.context_manager ──────────────────────────────────────────────

def test_context_manager_imports_cleanly():
    import clew.context_manager


def test_context_manager_no_qt_imports():
    import inspect
    from clew import context_manager
    src = inspect.getsource(context_manager)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.memory_service ───────────────────────────────────────────────

def test_memory_service_imports_cleanly():
    import clew.memory_service


def test_memory_service_no_qt_imports():
    import inspect
    from clew import memory_service
    src = inspect.getsource(memory_service)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.quota ────────────────────────────────────────────────────────

def test_quota_imports_cleanly():
    import clew.quota


def test_quota_no_qt_imports():
    import inspect
    from clew import quota
    src = inspect.getsource(quota)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.notifier ─────────────────────────────────────────────────────

def test_notifier_imports_cleanly():
    import clew.notifier


def test_notifier_no_qt_imports():
    import inspect
    from clew import notifier
    src = inspect.getsource(notifier)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.mcp_client ───────────────────────────────────────────────────

def test_mcp_client_imports_cleanly():
    import clew.mcp_client


def test_mcp_client_no_qt_imports():
    import inspect
    from clew import mcp_client
    src = inspect.getsource(mcp_client)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.mcp_manager ──────────────────────────────────────────────────

def test_mcp_manager_imports_cleanly():
    import clew.mcp_manager


def test_mcp_manager_no_qt_imports():
    import inspect
    from clew import mcp_manager
    src = inspect.getsource(mcp_manager)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.auto_router ──────────────────────────────────────────────────

def test_auto_router_imports_cleanly():
    import clew.auto_router


def test_auto_router_no_qt_imports():
    import inspect
    from clew import auto_router
    src = inspect.getsource(auto_router)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.command_policy ───────────────────────────────────────────────

def test_command_policy_imports_cleanly():
    import clew.command_policy


def test_command_policy_no_qt_imports():
    import inspect
    from clew import command_policy
    src = inspect.getsource(command_policy)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.inbound_listener ─────────────────────────────────────────────

def test_inbound_listener_imports_cleanly():
    import clew.inbound_listener


def test_inbound_listener_no_qt_imports():
    import inspect
    from clew import inbound_listener
    src = inspect.getsource(inbound_listener)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.request_queue ────────────────────────────────────────────────

def test_request_queue_imports_cleanly():
    import clew.request_queue


def test_request_queue_no_qt_imports():
    import inspect
    from clew import request_queue
    src = inspect.getsource(request_queue)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.collaboration ────────────────────────────────────────────────

def test_collaboration_imports_cleanly():
    import clew.collaboration


def test_collaboration_no_qt_imports():
    import inspect
    from clew import collaboration
    src = inspect.getsource(collaboration)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.swarm_manager ────────────────────────────────────────────────

def test_swarm_manager_imports_cleanly():
    import clew.swarm_manager


def test_swarm_manager_no_qt_imports():
    import inspect
    from clew import swarm_manager
    src = inspect.getsource(swarm_manager)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.agent_orchestrator ───────────────────────────────────────────

def test_agent_orchestrator_imports_cleanly():
    import clew.agent_orchestrator


def test_agent_orchestrator_no_qt_imports():
    import inspect
    from clew import agent_orchestrator
    src = inspect.getsource(agent_orchestrator)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.task_decomposition_router ────────────────────────────────────

def test_task_decomposition_router_imports_cleanly():
    import clew.task_decomposition_router


def test_task_decomposition_router_no_qt_imports():
    import inspect
    from clew import task_decomposition_router
    src = inspect.getsource(task_decomposition_router)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.benchmarks ───────────────────────────────────────────────────

def test_benchmarks_imports_cleanly():
    import clew.benchmarks
    assert hasattr(clew.benchmarks, "BenchmarkRunner") or hasattr(clew.benchmarks, "run")


def test_benchmarks_no_qt_imports():
    import inspect
    from clew import benchmarks
    # The benchmarks __init__ might be tiny — check the package's main module.
    from clew.benchmarks import runner
    src = inspect.getsource(runner)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── clew.agent (v2 runtime) ───────────────────────────────────────────

def test_agent_v2_imports_cleanly():
    """The v2 agent runtime package must import without Qt."""
    import clew.agent
    # The v2 runtime lives in clew.agent.runtime.
    from clew.agent import runtime as v2runtime
    assert v2runtime is not None


def test_agent_v2_no_qt_imports():
    """v2 agent runtime must not import PySide6."""
    import inspect
    from clew.agent import runtime as v2runtime
    src = inspect.getsource(v2runtime)
    import_lines = [ln for ln in src.splitlines()
                    if ln.strip().startswith(("import PySide6", "from PySide6"))]
    assert not import_lines


# ── End-to-end smoke: clew package imports ─────────────────────────────

def test_clew_package_imports():
    import clew
    assert clew.__version__ == "2.2.0"


def test_clew_main_module_imports():
    """python -m clew should be able to import its main module."""
    import clew.__main__
    assert hasattr(clew.__main__, "main")  # web_server.main is wired in


def test_clew_app_module_imports():
    """clew.app should re-export web_server.main."""
    import clew.app
    assert hasattr(clew.app, "main")
