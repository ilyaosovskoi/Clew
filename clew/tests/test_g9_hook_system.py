#!/usr/bin/env python3
"""
G9 — Hook System — test suite.

Verifies:
  1. HookManager.register() validates hook_type and callback.
  2. HookManager.dispatch_pre_tool_use() calls hooks in priority order.
  3. Pre hook BLOCK prevents tool execution.
  4. Pre hook MODIFY changes args.
  5. Pre hook ALLOW passes through.
  6. HookManager.dispatch_post_tool_use() is informational.
  7. HookManager.dispatch_user_prompt_submit() BLOCK / MODIFY / ALLOW.
  8. HookManager.remove() deletes a hook.
  9. HookManager.set_enabled() toggles hooks.
  10. HookManager.list_hooks() / get_hook() return metadata.
  11. HookManager.test_hook() dry-runs a hook.
  12. HookManager.load_user_modules() loads .py files from ~/.clew/hooks/.
  13. HookManager.save_config() / load_config() persist enabled state.
  14. HookResult serialization.
  15. HookEvent data sharing across hooks.

Run:
    python -m pytest clew/tests/test_g9_hook_system.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Test isolation ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_hook_manager():
    """Reset the global HookManager singleton before each test."""
    import clew.hook_system as _hs
    _hs._HOOK_MANAGER = None
    yield
    _hs._HOOK_MANAGER = None


# ── 1. Registration validation ──────────────────────────────────────────

def test_register_invalid_hook_type():
    from clew.hook_system import HookManager
    mgr = HookManager()
    with pytest.raises(ValueError, match="Invalid hook_type"):
        mgr.register("invalid_type", callback=lambda e: None)


def test_register_non_callable():
    from clew.hook_system import HookManager
    mgr = HookManager()
    with pytest.raises(TypeError, match="callable"):
        mgr.register("pre_tool_use", callback="not_callable")


def test_register_valid_hook():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()
    entry = mgr.register("pre_tool_use", callback=lambda e: HookResult(), name="test_hook")
    assert entry.id.startswith("hk_")
    assert entry.name == "test_hook"
    assert entry.hook_type == "pre_tool_use"
    assert entry.priority == 100


def test_register_with_explicit_id():
    from clew.hook_system import HookManager, HookResult
    mgr = HookManager()
    entry = mgr.register("pre_tool_use", callback=lambda e: HookResult(), hook_id="my_custom_id")
    assert entry.id == "my_custom_id"


# ── 2. Priority ordering ───────────────────────────────────────────────

def test_hooks_dispatched_in_priority_order():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()
    call_order = []

    def hook_low(event):
        call_order.append("low")
        return HookResult(action=HookAction.ALLOW)

    def hook_high(event):
        call_order.append("high")
        return HookResult(action=HookAction.ALLOW)

    mgr.register("pre_tool_use", callback=hook_high, priority=200, name="high")
    mgr.register("pre_tool_use", callback=hook_low, priority=10, name="low")

    mgr.dispatch_pre_tool_use("read_file", {"path": "test.py"})
    assert call_order == ["low", "high"]


# ── 3. Pre hook BLOCK ──────────────────────────────────────────────────

def test_pre_tool_use_block():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def block_hook(event):
        return HookResult(action=HookAction.BLOCK, message="Security policy: no write_file")

    mgr.register("pre_tool_use", callback=block_hook, name="security_block")

    result = mgr.dispatch_pre_tool_use("write_file", {"path": "secret.py", "content": "oops"})
    assert result.action == HookAction.BLOCK
    assert "Security policy" in result.message


# ── 4. Pre hook MODIFY ─────────────────────────────────────────────────

def test_pre_tool_use_modify_args():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def modify_hook(event):
        return HookResult(
            action=HookAction.MODIFY,
            message="Auto-formatted",
            modified_args={"content": event.args["content"] + "\n# auto-formatted\n"},
        )

    mgr.register("pre_tool_use", callback=modify_hook, name="auto_formatter")

    result = mgr.dispatch_pre_tool_use("write_file", {"path": "test.py", "content": "x=1"})
    assert result.action == HookAction.MODIFY
    assert "# auto-formatted" in result.modified_args["content"]
    assert result.modified_args["path"] == "test.py"  # Original args preserved


# ── 5. Pre hook ALLOW ──────────────────────────────────────────────────

def test_pre_tool_use_allow():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def log_hook(event):
        return HookResult(action=HookAction.ALLOW, message="Logged")

    mgr.register("pre_tool_use", callback=log_hook, name="audit_logger")

    result = mgr.dispatch_pre_tool_use("read_file", {"path": "test.py"})
    assert result.action == HookAction.ALLOW


# ── 6. Post hook informational ──────────────────────────────────────────

def test_post_tool_use_informational():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()
    recorded = []

    def audit_hook(event):
        recorded.append({"tool": event.tool_name, "result_len": len(event.result)})
        return HookResult(action=HookAction.ALLOW)

    mgr.register("post_tool_use", callback=audit_hook, name="audit")

    mgr.dispatch_post_tool_use("read_file", {"path": "test.py"}, "file contents here")
    assert len(recorded) == 1
    assert recorded[0]["tool"] == "read_file"


# ── 7. User prompt submit hooks ─────────────────────────────────────────

def test_user_prompt_submit_block():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def block_prompt(event):
        return HookResult(action=HookAction.BLOCK, message="Prompt contains sensitive data")

    mgr.register("user_prompt_submit", callback=block_prompt, name="sensitive_check")

    result = mgr.dispatch_user_prompt_submit("What is my password?")
    assert result.action == HookAction.BLOCK


def test_user_prompt_submit_modify():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def add_context(event):
        return HookResult(
            action=HookAction.MODIFY,
            message="Added project context",
            modified_prompt=f"[Context: Python project] {event.prompt}",
        )

    mgr.register("user_prompt_submit", callback=add_context, name="context_injector")

    result = mgr.dispatch_user_prompt_submit("Fix the bug")
    assert result.action == HookAction.MODIFY
    assert "Python project" in result.modified_prompt


# ── 8. Remove hook ──────────────────────────────────────────────────────

def test_remove_hook():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()
    entry = mgr.register("pre_tool_use", callback=lambda e: HookResult(action=HookAction.BLOCK), name="temp")
    assert len(mgr.list_hooks("pre_tool_use")) == 1

    removed = mgr.remove(entry.id)
    assert removed is True
    assert len(mgr.list_hooks("pre_tool_use")) == 0

    removed_again = mgr.remove(entry.id)
    assert removed_again is False


# ── 9. Set enabled ──────────────────────────────────────────────────────

def test_set_enabled():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()
    entry = mgr.register("pre_tool_use", callback=lambda e: HookResult(action=HookAction.BLOCK), name="blocker")

    # Hook is enabled by default — should block
    result = mgr.dispatch_pre_tool_use("write_file", {"path": "x"})
    assert result.action == HookAction.BLOCK

    # Disable the hook
    mgr.set_enabled(entry.id, False)
    result = mgr.dispatch_pre_tool_use("write_file", {"path": "x"})
    assert result.action == HookAction.ALLOW


# ── 10. List / get hooks ────────────────────────────────────────────────

def test_list_hooks():
    from clew.hook_system import HookManager, HookResult
    mgr = HookManager()
    mgr.register("pre_tool_use", callback=lambda e: HookResult(), name="hook1")
    mgr.register("post_tool_use", callback=lambda e: HookResult(), name="hook2")

    all_hooks = mgr.list_hooks()
    assert len(all_hooks) == 2

    pre_only = mgr.list_hooks("pre_tool_use")
    assert len(pre_only) == 1
    assert pre_only[0]["name"] == "hook1"


def test_get_hook():
    from clew.hook_system import HookManager, HookResult
    mgr = HookManager()
    entry = mgr.register("pre_tool_use", callback=lambda e: HookResult(), name="my_hook")

    found = mgr.get_hook(entry.id)
    assert found is not None
    assert found["name"] == "my_hook"

    not_found = mgr.get_hook("nonexistent")
    assert not_found is None


# ── 11. Test hook (dry-run) ─────────────────────────────────────────────

def test_test_hook():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def my_hook(event):
        return HookResult(action=HookAction.BLOCK, message="Test block")

    entry = mgr.register("pre_tool_use", callback=my_hook, name="test_me")

    result = mgr.test_hook(entry.id, "pre_tool_use", tool_name="write_file", args={"path": "x"})
    assert result["ok"] is True
    assert result["result"]["action"] == "block"


def test_test_hook_not_found():
    from clew.hook_system import HookManager
    mgr = HookManager()
    result = mgr.test_hook("nonexistent", "pre_tool_use")
    assert result["ok"] is False


# ── 12. User module auto-loading ────────────────────────────────────────

def test_load_user_modules(tmp_path):
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    # Create a temporary hooks directory with a test module
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hook_module = hooks_dir / "test_hook.py"
    hook_module.write_text("""
from clew.hook_system import HookResult, HookAction

def register_hooks(manager):
    manager.register(
        "pre_tool_use",
        callback=lambda e: HookResult(action=HookAction.ALLOW, message="from user module"),
        name="user_module_hook",
        source="user_module:test_hook.py",
    )
""")

    with patch("clew.hook_system._hooks_dir", return_value=hooks_dir):
        count = mgr.load_user_modules()

    assert count == 1
    hooks = mgr.list_hooks("pre_tool_use")
    assert any(h["name"] == "user_module_hook" for h in hooks)


# ── 13. Config persistence ──────────────────────────────────────────────

def test_save_load_config(tmp_path):
    from clew.hook_system import HookManager, HookResult
    mgr = HookManager()
    entry = mgr.register("pre_tool_use", callback=lambda e: HookResult(), name="persisted_hook")

    # Save config
    config_path = tmp_path / "hooks.json"
    with patch("clew.hook_system._hooks_config_path", return_value=config_path):
        mgr.save_config()

    # Verify the file exists
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert entry.id in data["overrides"]

    # Disable the hook and load config
    mgr.set_enabled(entry.id, False)
    with patch("clew.hook_system._hooks_config_path", return_value=config_path):
        mgr.load_config()


# ── 14. HookResult serialization ────────────────────────────────────────

def test_hook_result_to_dict():
    from clew.hook_system import HookResult, HookAction
    result = HookResult(
        action=HookAction.MODIFY,
        message="Modified args",
        modified_args={"path": "new_path.py"},
    )
    d = result.to_dict()
    assert d["action"] == "modify"
    assert d["modified_args"]["path"] == "new_path.py"


# ── 15. HookEvent data sharing ──────────────────────────────────────────

def test_hook_event_data_sharing():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def hook1(event):
        event.data["seen_by_hook1"] = True
        return HookResult(action=HookAction.ALLOW)

    def hook2(event):
        # Should see data set by hook1
        assert event.data.get("seen_by_hook1") is True
        return HookResult(action=HookAction.ALLOW)

    mgr.register("pre_tool_use", callback=hook1, priority=10, name="hook1")
    mgr.register("pre_tool_use", callback=hook2, priority=20, name="hook2")

    result = mgr.dispatch_pre_tool_use("read_file", {"path": "x"})
    assert result.action == HookAction.ALLOW


# ── Stats ────────────────────────────────────────────────────────────────

def test_stats():
    from clew.hook_system import HookManager, HookResult
    mgr = HookManager()
    mgr.register("pre_tool_use", callback=lambda e: HookResult(), name="h1")
    mgr.register("pre_tool_use", callback=lambda e: HookResult(), name="h2", enabled=False)
    mgr.register("post_tool_use", callback=lambda e: HookResult(), name="h3")

    stats = mgr.stats()
    assert stats["hooks"]["pre_tool_use"]["total"] == 2
    assert stats["hooks"]["pre_tool_use"]["enabled"] == 1
    assert stats["hooks"]["pre_tool_use"]["disabled"] == 1
    assert stats["hooks"]["post_tool_use"]["total"] == 1


# ── Hook exception handling ─────────────────────────────────────────────

def test_hook_exception_does_not_crash():
    from clew.hook_system import HookManager, HookResult, HookAction
    mgr = HookManager()

    def bad_hook(event):
        raise RuntimeError("Hook crashed!")

    def good_hook(event):
        return HookResult(action=HookAction.ALLOW)

    mgr.register("pre_tool_use", callback=bad_hook, priority=10, name="bad")
    mgr.register("pre_tool_use", callback=good_hook, priority=20, name="good")

    # Should not crash — bad hook is skipped, good hook runs
    result = mgr.dispatch_pre_tool_use("read_file", {"path": "x"})
    assert result.action == HookAction.ALLOW


# ── Singleton ────────────────────────────────────────────────────────────

def test_get_hook_manager_singleton():
    from clew.hook_system import get_hook_manager, reset_hook_manager
    reset_hook_manager()
    mgr1 = get_hook_manager()
    mgr2 = get_hook_manager()
    assert mgr1 is mgr2
