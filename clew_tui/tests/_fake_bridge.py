"""FakeClewBridge — a stand-in for ClewBridge that doesn't need real
LLM credentials or a live AgentRuntime.

Used by the Pilot-driven interaction tests (``clew_tui/tests/``) so
they can drive the TUI's keyboard/mouse paths without spending money
or requiring a running Ollama / OpenAI / etc.

Every method records its call (args + return value) in ``self.calls``
so tests can assert things like "set_section was called with 'office'"
after simulating a palette interaction. The recorded call list is the
test's source of truth — the fake's return values are deliberately
minimal canned data so the TUI has something to render.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class _Call:
    method: str
    args: tuple
    kwargs: dict
    return_value: Any


class FakeClewBridge:
    """Drop-in stand-in for ``clew_tui.bridge.ClewBridge``.

    Subclasses the real ClewBridge so isinstance() checks pass, but
    overrides every method that would touch the registry / runtime /
    filesystem with a no-op that records the call.

    The bridge's ``workspace`` and ``section`` attributes are real
    (mutable) so the TUI can read them. Everything else is faked.
    """

    # ── Construction ───────────────────────────────────────────────

    def __init__(self, workspace: Optional[str] = None, section: str = "general") -> None:
        # Don't call super().__init__() — we don't want the real bridge's
        # registry / agent / slash_manager initialisation. Just set the
        # attributes the TUI actually reads.
        self.workspace = workspace or os.getcwd()
        self.section = section
        self.max_iterations = 8
        self.enable_planning = False
        self._provider = None
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._event_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._confirm_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self._guardian_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self._agent = None  # never built
        self._registry = None
        self._tracker = None
        self._slash_manager = None
        self._guardian_level: str = "off"

        # Test inspection: list of every call made through the fake.
        self.calls: List[_Call] = []

    # ── Recording helper ──────────────────────────────────────────

    def _record(self, method: str, *args, return_value: Any = None, **kwargs) -> Any:
        self.calls.append(_Call(method, args, kwargs, return_value))
        return return_value

    # ── Real ClewBridge surface (selected methods) ────────────────

    # ----- setup / event sinks -----
    def set_event_sink(self, sink):
        self._event_sink = sink
        return self._record("set_event_sink", sink, return_value=None)

    def set_confirm_handler(self, handler):
        self._confirm_handler = handler
        return self._record("set_confirm_handler", handler, return_value=None)

    def set_guardian_handler(self, handler):
        self._guardian_handler = handler
        return self._record("set_guardian_handler", handler, return_value=None)

    def ensure_agent(self):
        return self._record("ensure_agent", return_value=None)

    # ----- runtime control -----
    def run_prompt(self, prompt, plan_approved=False, plan_feedback=None):
        # Simulate an instant successful turn that produces a canned
        # final answer. Tests that need a longer/failed turn can
        # override this method.
        self._record("run_prompt", prompt, plan_approved, plan_feedback,
                     return_value=None)
        # Emit a synthetic "done" event so the TUI's status bar
        # returns to "idle".
        if self._event_sink:
            try:
                self._event_sink("done", {"output": "[fake] task complete"})
            except Exception:
                pass
        # Return a minimal TaskResult-shaped object.
        return _FakeTaskResult(success=True, output="[fake] task complete", iterations=1)

    def request_stop(self):
        return self._record("request_stop", return_value=None)

    def is_busy(self):
        return False

    def status(self):
        return {
            "provider": "fake",
            "model": "fake-model",
            "tokens": 0,
            "cost": 0.0,
            "busy": False,
        }

    # ----- slash commands -----
    def list_slash_commands(self):
        return self._record("list_slash_commands", return_value=[])

    def resolve_slash_command(self, text):
        return self._record("resolve_slash_command", text, return_value=None)

    def _init_slash_manager(self):
        return self._record("_init_slash_manager", return_value=None)

    # ----- providers -----
    def list_providers(self):
        return self._record("list_providers", return_value=[
            {"id": "fake", "label": "Fake", "model": "fake-model",
             "default_model": "fake-model", "active": True, "configured": True},
            {"id": "ollama", "label": "Ollama", "model": "llama3",
             "default_model": "llama3", "active": False, "configured": False},
        ])

    def set_provider(self, provider_id, model=None):
        return self._record("set_provider", provider_id, model,
                            return_value={"ok": True, "provider": provider_id, "model": model})

    # ----- section -----
    def set_section(self, section_id):
        valid = {"general", "heavy_code", "office"}
        if section_id not in valid:
            return self._record("set_section", section_id,
                                return_value={"ok": False, "error": f"unknown: {section_id}"})
        self.section = section_id
        return self._record("set_section", section_id,
                            return_value={"ok": True, "section": section_id})

    # ----- workspace -----
    def change_workspace(self, new_path):
        self.workspace = new_path
        return self._record("change_workspace", new_path,
                            return_value={"ok": True, "workspace": new_path})

    def list_workspace_files(self):
        return self._record("list_workspace_files", return_value=[])

    # ----- chats -----
    def list_chats(self):
        return self._record("list_chats", return_value=[
            {"id": "chat1", "title": "Fake Chat 1",
             "updated_at": "2026-08-01", "message_count": 4, "status": "done"},
            {"id": "chat2", "title": "Fake Chat 2",
             "updated_at": "2026-08-02", "message_count": 2, "status": "idle"},
        ])

    # ----- planning -----
    def toggle_planning(self):
        self.enable_planning = not self.enable_planning
        return self._record("toggle_planning",
                            return_value={"ok": True, "planning": self.enable_planning})

    # ----- guardian -----
    def set_guardian_level(self, level):
        valid = {"off", "dangerous_only", "all"}
        if level not in valid:
            return self._record("set_guardian_level", level,
                                return_value={"ok": False, "error": f"invalid: {level}"})
        self._guardian_level = level
        return self._record("set_guardian_level", level,
                            return_value={"ok": True, "level": level})

    def get_guardian_level(self):
        return self._record("get_guardian_level",
                            return_value={"ok": True, "level": self._guardian_level})

    def _save_guardian_config(self, level):
        return self._record("_save_guardian_config", level, return_value=None)

    def _load_guardian_config(self):
        return self._record("_load_guardian_config",
                            return_value=self._guardian_level)

    # ----- usage -----
    def get_usage(self):
        return self._record("get_usage", return_value=self.status())

    # ----- collaboration -----
    def list_collaboration_modes(self):
        return self._record("list_collaboration_modes", return_value=[
            {"id": "single", "label": "Single (no collaboration)",
             "desc": "No collaboration — single agent"},
            {"id": "reviewer", "label": "Reviewer",
             "desc": "Reviewer reviews codegen output"},
            {"id": "codegen", "label": "Codegen",
             "desc": "Codegen writes code under reviewer oversight"},
            {"id": "pair", "label": "Pair",
             "desc": "Pair-programming with two agents"},
            {"id": "observer", "label": "Observer",
             "desc": "Observer watches and warns of risks"},
        ])

    # ----- persistence -----
    def get_persistence_backend(self):
        return self._record("get_persistence_backend", return_value="json")

    def set_persistence_backend(self, backend):
        valid = {"json", "sqlite"}
        if backend not in valid:
            return self._record("set_persistence_backend", backend,
                                return_value={"ok": False, "error": f"invalid: {backend}"})
        return self._record("set_persistence_backend", backend,
                            return_value={"ok": True, "backend": backend})

    def list_sqlite_sessions(self):
        return self._record("list_sqlite_sessions", return_value=[])

    # ----- compaction / context -----
    def get_compaction_stats(self):
        return self._record("get_compaction_stats", return_value=None)

    def get_tool_catalog_state(self):
        return self._record("get_tool_catalog_state", return_value={
            "loaded": 0, "available": 0, "prompt_chars_saved": 0,
        })

    # ----- cost router (M2) -----
    def get_cost_router_config(self):
        return self._record("get_cost_router_config", return_value={})

    def set_cost_router_config(self, cfg):
        return self._record("set_cost_router_config", cfg,
                            return_value={"ok": True})

    # ----- capabilities (G7) -----
    def list_capabilities(self):
        return self._record("list_capabilities", return_value=[])

    def get_capability(self, cap_id):
        return self._record("get_capability", cap_id, return_value=None)

    def run_capability(self, cap_id, **params):
        return self._record("run_capability", cap_id, return_value={
            "ok": True, "output": f"[fake] ran capability {cap_id}",
        })

    # ----- handoff (G6) -----
    def list_handoffs(self):
        return self._record("list_handoffs", return_value=[])

    def create_handoff(self, title, content):
        return self._record("create_handoff", title, content,
                            return_value={"ok": True, "id": "fake_handoff_1"})

    def get_handoff(self, handoff_id):
        return self._record("get_handoff", handoff_id, return_value=None)

    def accept_handoff_block(self, handoff_id, block_id):
        return self._record("accept_handoff_block", handoff_id, block_id,
                            return_value={"ok": True})

    def reject_handoff_block(self, handoff_id, block_id):
        return self._record("reject_handoff_block", handoff_id, block_id,
                            return_value={"ok": True})

    # ----- answer helpers (used by approval / guardian modals) -----
    def answer_confirmation(self, accepted):
        return self._record("answer_confirmation", accepted, return_value=None)

    def answer_guardian_verdict(self, verdict):
        return self._record("answer_guardian_verdict", verdict, return_value=None)

    # ----- test inspection helpers -----
    def calls_to(self, method: str) -> List[_Call]:
        """Return every recorded call to ``method``."""
        return [c for c in self.calls if c.method == method]

    def was_called(self, method: str) -> bool:
        return any(c.method == method for c in self.calls)

    def reset(self):
        self.calls.clear()


@dataclass
class _FakeTaskResult:
    """Minimal stand-in for clew.agent_runtime.TaskResult."""
    success: bool = True
    output: str = ""
    error: Optional[str] = None
    iterations: int = 0
    steps: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    plan: Optional[str] = None
    metadata: dict = field(default_factory=dict)
