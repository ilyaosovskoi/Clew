"""bridge.py — the ONLY module in clew_tui that knows about clew internals.

Every widget talks to the agent through a ClewBridge instance. If a widget
needs something new from the core, add a method here instead of importing
`clew.agent_runtime` (or friends) directly into UI code. Keeping this boundary
thin is what stops the TUI from becoming a fourth parallel agent-loop path.

The bridge drives the proven production path: a plain `AgentRuntime` created
the same way `clew/cli.py` creates it, wired via `on_event`, `set_cancel_check`
and `set_confirm_callback`. It deliberately does NOT touch
`agent_orchestrator.patch_runtime` (unused in production, raises on a vanilla
runtime) nor the AgentRuntimeV2 path.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Event kinds are the string values of clew.agent_runtime.AgentEvent
# (plan_created, iteration_start, thought, tool_called, tool_result,
#  iteration_end, done, error). The sink receives (kind, data_dict).
EventSink = Callable[[str, Dict[str, Any]], None]
ConfirmHandler = Callable[[Dict[str, Any]], None]


@dataclass
class ProviderChoice:
    """Optional provider overrides. Anything left None falls back to the
    saved ~/.clew/config.json / environment defaults."""
    provider_id: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None


class ClewBridge:
    def __init__(
        self,
        workspace: Optional[str] = None,
        provider: Optional[ProviderChoice] = None,
        section: str = "general",
        max_iterations: int = 8,
        enable_planning: bool = False,
    ) -> None:
        self.workspace = workspace or os.getcwd()
        self.section = section
        self.max_iterations = max_iterations
        self.enable_planning = enable_planning
        self._provider = provider or ProviderChoice()

        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._event_sink: Optional[EventSink] = None
        self._confirm_handler: Optional[ConfirmHandler] = None
        self._guardian_handler: Optional[Callable[[Dict[str, Any]], None]] = None

        self._agent: Any = None
        self._registry: Any = None
        self._tracker: Any = None

        # Slash command manager — loaded from .claude/commands/ .md files
        self._slash_manager: Any = None

    # ------------------------------------------------------------------ setup
    def set_event_sink(self, sink: Optional[EventSink]) -> None:
        self._event_sink = sink

    def set_confirm_handler(self, handler: Optional[ConfirmHandler]) -> None:
        self._confirm_handler = handler

    def set_guardian_handler(self, handler: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        self._guardian_handler = handler

    def _build_registry(self):
        from clew.providers import get_registry, ProviderConfig

        registry = get_registry()
        if not registry.list_providers():
            registry.register_default()

        pid = self._provider.provider_id or registry.active_id or "ollama"
        p = self._provider
        # Only reconfigure when the caller actually overrode something,
        # otherwise trust the saved config for this provider.
        if p.model or p.api_key or p.api_base:
            cfg = ProviderConfig(
                provider_id=pid,
                model=p.model or "",
                api_key=(p.api_key or os.environ.get(f"{pid.upper()}_API_KEY") or None),
                api_base=(p.api_base or None),
            )
            registry.configure(pid, cfg)
        registry.set_active(pid)
        return registry

    def ensure_agent(self):
        if self._agent is not None:
            return self._agent

        from clew.agent_runtime import AgentRuntime
        from clew.token_tracker import get_token_tracker

        self._registry = self._build_registry()
        self._tracker = get_token_tracker()

        agent = AgentRuntime(
            registry=self._registry,
            workspace=self.workspace,
            max_iterations=self.max_iterations,
            enable_planning=self.enable_planning,
            on_event=self._on_agent_event,
            token_tracker=self._tracker,
            section=self.section,
            on_token_delta=self._on_token_delta_event,
        )
        agent.set_autonomy("always_ask")
        agent.set_confirm_callback(self._on_confirm_request)
        agent.set_cancel_check(lambda: self._stop.is_set())
        self._agent = agent
        return agent

    def _init_slash_manager(self) -> None:
        """Lazily initialize the slash command manager."""
        if self._slash_manager is not None:
            return
        try:
            from clew.slash_commands import SlashCommandManager
            self._slash_manager = SlashCommandManager()
            self._slash_manager.set_project_root(self.workspace)
        except Exception:
            self._slash_manager = None

    # --------------------------------------------------------------- callbacks
    def _on_agent_event(self, event: Any, data: Dict[str, Any]) -> None:
        sink = self._event_sink
        if sink is None:
            return
        kind = getattr(event, "value", str(event))
        try:
            sink(kind, dict(data))
        except Exception:
            # A UI error must never crash the agent loop.
            pass

    def _on_token_delta_event(self, chunk: str) -> None:
        """Relay a token delta chunk to the UI via the event sink.
        Called by AgentRuntime when streaming is enabled — each chunk
        of generated text is forwarded as a 'token_delta' event so the
        ChatLog can append it in real time."""
        sink = self._event_sink
        if sink is None:
            return
        try:
            sink("token_delta", {"delta": chunk})
        except Exception:
            pass

    def _on_confirm_request(self, info: Dict[str, Any]) -> None:
        # Check if this is a Guardian MODIFY verdict
        if info.get("guardian_verdict") == "MODIFY" or info.get("suggested_args") is not None:
            handler = self._guardian_handler
            if handler is None:
                # No UI wired — default to approve
                self.answer_guardian_verdict("approve")
                return
            try:
                handler(dict(info))
            except Exception:
                self.answer_guardian_verdict("reject")
            return

        # Regular confirmation
        handler = self._confirm_handler
        if handler is None:
            # No UI wired — approve so we do not deadlock the loop.
            self.answer_confirmation(True)
            return
        try:
            handler(dict(info))
        except Exception:
            self.answer_confirmation(False)

    def answer_confirmation(self, accepted: bool) -> None:
        """Called by the UI after the user answers an approval modal."""
        agent = self._agent
        if agent is None:
            return
        try:
            agent.tools._confirm_accepted = bool(accepted)
            agent.tools._confirm_event.set()
        except Exception:
            pass

    def answer_guardian(self, response: str) -> None:
        """Called by the UI after the user answers a Guardian modal.

        response: "approve", "reject", or "use_fix"
        """
        agent = self._agent
        if agent is None:
            return
        try:
            if response == "use_fix":
                # Apply the pending fixed args
                if hasattr(agent.tools, "_guardian_pending_args") and agent.tools._guardian_pending_args:
                    agent.tools._confirm_accepted = True
                    agent.tools._confirm_event.set()
                else:
                    # No fix available, treat as approve
                    agent.tools._confirm_accepted = True
                    agent.tools._confirm_event.set()
            elif response == "reject":
                agent.tools._confirm_accepted = False
                agent.tools._confirm_event.set()
            else:  # "approve"
                agent.tools._confirm_accepted = True
                agent.tools._confirm_event.set()
        except Exception:
            pass

    def answer_guardian_verdict(self, verdict: str) -> None:
        """Called by the UI after the user answers a Guardian modal.

        verdict: "approve" | "reject" | "use_fix"
        """
        agent = self._agent
        if agent is None:
            return
        try:
            if verdict == "use_fix":
                # Apply the suggested args
                agent.tools._guardian_suggested_args = getattr(agent.tools, "_guardian_pending_args", None)
                agent.tools._confirm_accepted = True
            elif verdict == "approve":
                agent.tools._confirm_accepted = True
            else:  # reject
                agent.tools._confirm_accepted = False
            agent.tools._confirm_event.set()
        except Exception:
            pass

    # ------------------------------------------------------------------- runtime
    def run_prompt(self, prompt: str, plan_approved: bool = False,
                   plan_feedback: str | None = None):
        """Run one full agentic turn. BLOCKING — call from a worker thread.

        Returns the legacy TaskResult (success, output, iterations, ...).

        When ``plan_approved`` is True, the runtime continues from a
        pending plan instead of starting fresh.  ``plan_feedback``
        lets the user reject a plan with textual feedback.
        """
        from clew.agent_runtime import TaskType
        with self._busy:
            self._stop.clear()
            agent = self.ensure_agent()
            gen_kwargs = {}
            if plan_approved:
                gen_kwargs["plan_approved"] = True
            if plan_feedback is not None:
                gen_kwargs["plan_feedback"] = plan_feedback
            return agent.run(prompt, task_type=TaskType.AGENTIC, **gen_kwargs)

    def request_stop(self) -> None:
        """Cooperatively interrupt the running turn (Ctrl+C). The loop checks
        the cancel flag between iterations and before every tool call; we also
        release any pending confirmation so a blocked approval unwinds."""
        self._stop.set()
        agent = self._agent
        if agent is not None:
            try:
                agent.tools._confirm_accepted = False
                agent.tools._confirm_event.set()
            except Exception:
                pass

    def is_busy(self) -> bool:
        return self._busy.locked()

    # -------------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        provider = None
        model = None
        if self._registry is not None:
            try:
                for p in self._registry.list_providers():
                    if p.get("active"):
                        provider = p.get("id")
                        model = p.get("model") or p.get("default_model")
                        break
            except Exception:
                pass

        tokens = 0
        cost = 0.0
        if self._tracker is not None:
            try:
                s = self._tracker.stats()
                tokens = int(s.get("total_tokens", 0) or 0)
                cost = float(s.get("total_cost", 0.0) or 0.0)
            except Exception:
                pass

        return {
            "provider": provider,
            "model": model,
            "tokens": tokens,
            "cost": cost,
            "busy": self.is_busy(),
        }

    # --------------------------------------------------------- slash commands
    def list_slash_commands(self) -> List[Dict[str, Any]]:
        """Return all user-defined slash commands from .claude/commands/.
        Each entry has {id, name, description, has_arguments}."""
        self._init_slash_manager()
        if self._slash_manager is None:
            return []
        try:
            return self._slash_manager.list_commands()
        except Exception:
            return []

    def resolve_slash_command(self, text: str) -> Optional[Dict[str, Any]]:
        """Try to resolve text as a slash command. Returns
        {command, arguments, expanded, description} or None."""
        self._init_slash_manager()
        if self._slash_manager is None:
            return None
        try:
            return self._slash_manager.resolve(text)
        except Exception:
            return None

    # --------------------------------------------------------- provider/model
    def list_providers(self) -> List[Dict[str, Any]]:
        """Return all available provider metadata for the model switcher.
        Each entry: {id, label, default_model, model, active, capabilities}."""
        if self._registry is None:
            self._registry = self._build_registry()
        try:
            return self._registry.list_providers()
        except Exception:
            return []

    def set_provider(self, provider_id: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Switch the active provider and optionally the model.
        Returns {ok: bool, provider: str, model: str}."""
        if self._registry is None:
            self._registry = self._build_registry()
        try:
            from clew.providers import ProviderConfig
            self._registry.set_active(provider_id)
            if model:
                cfg = ProviderConfig(
                    provider_id=provider_id,
                    model=model,
                )
                self._registry.configure(provider_id, cfg)
            # Persist to config
            self._save_provider_config(provider_id, model)
            # Force rebuild of the agent on next turn
            self._agent = None
            return {
                "ok": True,
                "provider": provider_id,
                "model": model or self._get_active_model(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _get_active_model(self) -> Optional[str]:
        """Get the model name of the currently active provider."""
        if self._registry is None:
            return None
        try:
            for p in self._registry.list_providers():
                if p.get("active"):
                    return p.get("model") or p.get("default_model")
        except Exception:
            pass
        return None

    def _save_provider_config(self, provider_id: str, model: Optional[str]) -> None:
        """Persist the provider selection to ~/.clew/config.json."""
        config_path = Path.home() / ".clew" / "config.json"
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            cfg["active_provider"] = provider_id
            if model and "providers" in cfg:
                providers = cfg["providers"]
                if provider_id in providers:
                    providers[provider_id]["model"] = model
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # --------------------------------------------------------- section
    def set_section(self, section_id: str) -> Dict[str, Any]:
        """Switch the runtime section. Forces agent rebuild on next turn."""
        valid = {"general", "heavy_code", "office"}
        if section_id not in valid:
            return {"ok": False, "error": f"Unknown section: {section_id}. Valid: {', '.join(valid)}"}
        self.section = section_id
        # Force agent rebuild with the new section
        self._agent = None
        return {"ok": True, "section": section_id}

    # --------------------------------------------------------- workspace/directory
    def change_workspace(self, new_path: str) -> Dict[str, Any]:
        """Change the workspace directory. Forces agent rebuild."""
        try:
            path = Path(new_path).resolve()
            if not path.is_dir():
                return {"ok": False, "error": f"Not a directory: {path}"}
            self.workspace = str(path)
            # Force agent rebuild
            self._agent = None
            # Update slash commands root
            if self._slash_manager is not None:
                try:
                    self._slash_manager.set_project_root(self.workspace)
                except Exception:
                    pass
            return {"ok": True, "workspace": self.workspace}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_workspace_files(self) -> List[str]:
        """Return just the filenames (no paths) in the workspace root.
        For a more detailed file tree, the agent runtime's file tools
        provide that — this is a quick overview for /files command."""
        try:
            root = Path(self.workspace)
            names = []
            for item in sorted(root.iterdir()):
                if item.name.startswith(".") and item.name not in (".env", ".gitignore"):
                    continue
                suffix = "/" if item.is_dir() else ""
                names.append(f"{item.name}{suffix}")
            return names
        except Exception:
            return []

    # --------------------------------------------------------- chats
    def list_chats(self) -> List[Dict[str, Any]]:
        """List all saved chats from ~/.clew/chats/*.json.
        Each entry: {id, title, updated_at, message_count, status}."""
        chats_dir = Path.home() / ".clew" / "chats"
        if not chats_dir.exists():
            return []
        chats = []
        for path in sorted(chats_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    chat = json.load(f)
                last_status = "idle"
                messages = chat.get("messages", [])
                for m in reversed(messages):
                    if m.get("role") == "assistant":
                        last_status = "error" if (m.get("error") or m.get("success") is False) else "done"
                        break
                chats.append({
                    "id": chat.get("id", path.stem),
                    "title": chat.get("title", "Untitled"),
                    "updated_at": chat.get("updated_at", chat.get("created_at", "")),
                    "message_count": len(messages),
                    "status": last_status,
                })
            except Exception:
                continue
        return chats

    # --------------------------------------------------------- planning toggle
    def toggle_planning(self) -> Dict[str, Any]:
        """Toggle planning mode on/off. Forces agent rebuild."""
        self.enable_planning = not self.enable_planning
        self._agent = None
        return {"ok": True, "planning": self.enable_planning}

    # --------------------------------------------------------- guardian
    def set_guardian_level(self, level: str) -> Dict[str, Any]:
        """Set Guardian safety review level.

        Levels:
          - "off": Guardian disabled (default)
          - "dangerous_only": Only review tool calls flagged as high-risk
          - "all": Review all tool calls with medium+ risk

        Returns {ok: bool, level: str} or error dict."""
        valid_levels = {"off", "dangerous_only", "all"}
        if level not in valid_levels:
            return {"ok": False, "error": f"Invalid level: {level}. Valid: {', '.join(valid_levels)}"}

        agent = self.ensure_agent()
        if not hasattr(agent.tools, "_guardian_config"):
            from clew.agent.guardian import GuardianConfig
            agent.tools._guardian_config = GuardianConfig(level=level)
        else:
            from clew.agent.guardian import GuardianConfig
            # Create new config with updated level, preserving provider settings
            old = agent.tools._guardian_config
            agent.tools._guardian_config = GuardianConfig(
                level=level,
                provider_id=old.provider_id,
                model=old.model,
            )

        # Persist to config
        self._save_guardian_config(level)
        return {"ok": True, "level": level}

    def get_guardian_level(self) -> Dict[str, Any]:
        """Get current Guardian level."""
        if self._agent is not None and hasattr(self._agent.tools, "_guardian_config") and self._agent.tools._guardian_config:
            return {"ok": True, "level": self._agent.tools._guardian_config.level}
        return {"ok": True, "level": "off"}

    def _save_guardian_config(self, level: str) -> None:
        """Persist guardian level to ~/.clew/config.json."""
        config_path = Path.home() / ".clew" / "config.json"
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            cfg["guardian_level"] = level
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _load_guardian_config(self) -> str:
        """Load guardian level from ~/.clew/config.json."""
        config_path = Path.home() / ".clew" / "config.json"
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                return cfg.get("guardian_level", "off")
        except Exception:
            pass
        return "off"

    # --------------------------------------------------------- usage
    def get_usage(self) -> Dict[str, Any]:
        """Return current session usage stats."""
        return self.status()
