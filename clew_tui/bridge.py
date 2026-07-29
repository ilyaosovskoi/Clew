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

        # v2.0.0 fix: restore the saved Guardian level so the user does
        # not start every session with Guardian silently OFF.
        try:
            saved_level = self._load_guardian_config()
            if saved_level and saved_level != "off":
                self.set_guardian_level(saved_level)
        except Exception:
            pass

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

    # ── v2.0.0 — Collaboration modes ───────────────────────────────

    def list_collaboration_modes(self) -> List[Dict[str, Any]]:
        """Return the four collaboration modes supported by the backend."""
        return [
            {"id": "single", "label": "Single (no collaboration)",
             "desc": "Run a single agent on the task."},
            {"id": "reviewer", "label": "Reviewer",
             "desc": "Implementer + reviewer loop with APPROVE/REJECT/MODIFY verdicts."},
            {"id": "codegen", "label": "Codegen",
             "desc": "Planner decomposes task, N parallel implementers, concatenated output."},
            {"id": "pair", "label": "Pair",
             "desc": "Two pair-programmer agents alternate turns on the same task."},
            {"id": "observer", "label": "Observer",
             "desc": "One worker + N read-only observers; warnings collected."},
        ]

    def run_collaboration(self, mode: str, task: str) -> Dict[str, Any]:
        """Run a task in the given collaboration mode.

        Returns {ok, mode, output, iterations, metadata} on success.
        """
        try:
            from clew.collaboration import (
                CollaborationOrchestrator, CollaborationMode,
            )
            agent = self.ensure_agent()
            orch = CollaborationOrchestrator(agent)
            try:
                mode_enum = CollaborationMode(mode)
            except ValueError:
                return {"ok": False, "error": f"Unknown mode: {mode}"}
            result = orch.run(mode_enum, task)
            return {
                "ok": True,
                "mode": mode,
                "output": result.output,
                "iterations": result.iterations,
                "metadata": result.metadata or {},
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── v2.0.0 — Request queue monitoring ──────────────────────────

    def get_queue_stats(self) -> Dict[str, Any]:
        """Return per-provider request-queue stats."""
        try:
            from clew.request_queue import get_queue_registry
            return get_queue_registry().stats()
        except Exception:
            return {}

    # ── v2.0.0 — Persistence backend selector ──────────────────────

    def get_persistence_backend(self) -> str:
        """Return the configured chat-persistence backend ('json' or 'sqlite')."""
        cfg_path = Path.home() / ".clew" / "config.json"
        try:
            if cfg_path.exists():
                with open(cfg_path, "r") as f:
                    cfg = json.load(f) or {}
                return cfg.get("persistence_backend", "json")
        except Exception:
            pass
        return "json"

    def set_persistence_backend(self, backend: str) -> Dict[str, Any]:
        """Switch chat persistence between 'json' and 'sqlite'."""
        valid = {"json", "sqlite"}
        if backend not in valid:
            return {"ok": False, "error": f"Invalid backend: {backend}"}
        try:
            cfg_path = Path.home() / ".clew" / "config.json"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg: dict = {}
            if cfg_path.exists():
                with open(cfg_path, "r") as f:
                    cfg = json.load(f) or {}
            cfg["persistence_backend"] = backend
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)
            return {"ok": True, "backend": backend}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_sqlite_sessions(self) -> List[Dict[str, Any]]:
        """List sessions stored in the SQLite backend (~/.clew/chats.sqlite3)."""
        try:
            from clew.session.sqlite_persistence import SQLitePersistence
            db_path = Path.home() / ".clew" / "chats.sqlite3"
            if not db_path.exists():
                return []
            store = SQLitePersistence(str(db_path))
            return store.list_sessions()
        except Exception:
            return []

    # ── v2.0.0 — Context fragments / compaction view ───────────────

    def get_compaction_stats(self) -> Optional[Dict[str, Any]]:
        """Return compaction stats from the most recent compaction pass."""
        try:
            agent = self._agent
            if agent is None:
                return None
            stats = getattr(agent, "_last_compaction_stats", None)
            if stats is None:
                return None
            if hasattr(stats, "to_dict"):
                return stats.to_dict()
            return dict(stats)
        except Exception:
            return None

    # ── v2.0.0 — Progressive tools catalog ─────────────────────────

    def get_tool_catalog_state(self) -> Dict[str, Any]:
        """Return the current progressive-tools catalog state."""
        try:
            from clew.progressive_tools import TOOL_CATALOG
            agent = self._agent
            if agent is None:
                return {"loaded": [], "available": list(TOOL_CATALOG.keys()),
                        "prompt_chars_saved": 0}
            engine = getattr(agent, "tools", None)
            try:
                loaded = sorted({str(t) for t in engine._tools.keys()})
            except Exception:
                loaded = []
            available = sorted(
                name for name in TOOL_CATALOG.keys() if name not in loaded
            )
            prompt_chars_saved = sum(
                len(name) + len(TOOL_CATALOG.get(name, ""))
                for name in available
            )
            return {
                "loaded": loaded,
                "available": available,
                "prompt_chars_saved": prompt_chars_saved,
            }
        except Exception as e:
            return {"loaded": [], "available": [], "prompt_chars_saved": 0,
                    "error": str(e)}

    # ── v2.0.1 (G7) — Capability catalog ───────────────────────────

    def list_capabilities(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Browse the capability catalog (built-in + user + project).

        Returns each capability's metadata (no body). Group by category
        in the UI. Use ``get_capability(id)`` to fetch the full body
        before filling placeholders.
        """
        try:
            from clew.capability_catalog import get_catalog
            catalog = get_catalog()
            if self.workspace and not catalog._project_root:
                catalog.set_project_root(self.workspace)
            return catalog.list_as_dicts(category=category, include_body=False)
        except Exception as e:
            return []

    def list_capability_categories(self) -> List[str]:
        """Distinct categories present in the catalog (for palette grouping)."""
        try:
            from clew.capability_catalog import get_catalog
            catalog = get_catalog()
            return catalog.list_categories()
        except Exception:
            return []

    def get_capability(self, cap_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the full capability (with body) by id."""
        try:
            from clew.capability_catalog import get_catalog
            catalog = get_catalog()
            cap = catalog.get(cap_id)
            if cap is None:
                return None
            return cap.to_dict(include_body=True)
        except Exception:
            return None

    def fill_capability_template(
        self,
        cap_id: str,
        values: Dict[str, str],
    ) -> Dict[str, Any]:
        """Substitute $placeholder$ values in the capability body.

        Returns {ok, prompt, capability, missing} on success,
        {ok=False, error, missing} if required placeholders are absent.
        """
        try:
            from clew.capability_catalog import get_catalog
            catalog = get_catalog()
            return catalog.fill_template(cap_id, values)
        except Exception as e:
            return {"ok": False, "error": str(e), "missing": []}

    # ── v2.0.1 (M1) — Second Opinion (Pro-gated) ───────────────────

    def is_pro_enabled(self) -> bool:
        """Return True if the ``clew_pro`` flag is on."""
        try:
            from clew.second_opinion import is_pro_enabled as _is_pro
            return _is_pro()
        except Exception:
            return False

    def set_pro_enabled(self, enabled: bool) -> Dict[str, Any]:
        """Toggle the ``clew_pro`` flag (env var takes priority on read)."""
        try:
            from clew.second_opinion import set_pro_enabled as _set_pro
            _set_pro(enabled)
            return {"ok": True, "pro": enabled}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_second_opinion_config(self) -> Dict[str, Any]:
        """Return the current Second Opinion configuration."""
        try:
            from clew.second_opinion import get_second_opinion_config as _get
            cfg = _get()
            return {
                "ok": True,
                "enabled": cfg.enabled,
                "provider_id": cfg.provider_id,
                "model": cfg.model,
                "min_risk_level": cfg.min_risk_level,
                "pro_enabled": self.is_pro_enabled(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_second_opinion_config(
        self,
        *,
        enabled: Optional[bool] = None,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        min_risk_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update the Second Opinion configuration. Only non-None
        fields are changed; the rest are preserved."""
        try:
            from clew.second_opinion import (
                get_second_opinion_config as _get,
                set_second_opinion_config as _set,
                SecondOpinionConfig,
            )
            cur = _get()
            new_cfg = SecondOpinionConfig(
                enabled=cur.enabled if enabled is None else bool(enabled),
                provider_id=cur.provider_id if provider_id is None else str(provider_id),
                model=cur.model if model is None else str(model),
                min_risk_level=(cur.min_risk_level if min_risk_level is None
                                else str(min_risk_level)),
            )
            _set(new_cfg)
            return {
                "ok": True,
                "enabled": new_cfg.enabled,
                "provider_id": new_cfg.provider_id,
                "model": new_cfg.model,
                "min_risk_level": new_cfg.min_risk_level,
                "pro_enabled": self.is_pro_enabled(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def run_second_opinion(
        self,
        tool_name: str,
        args: Dict[str, Any],
        risk_level: str,
        risk_reasons: List[str],
        recent_context: str = "",
    ) -> Dict[str, Any]:
        """Invoke a second model to review a proposed tool call.

        Returns the verdict dict (verdict, rationale, suggested_args,
        provider_id, model, elapsed_ms, error). Always returns APPROVE
        on any error so the feature fails OPEN.

        Requires Pro to be enabled. If Pro is off, returns a verdict
        with ``error='pro_required'`` so the UI can prompt the user.
        """
        try:
            from clew.second_opinion import (
                get_second_opinion_config,
                should_run_second_opinion,
                review_with_second_model,
                is_pro_enabled,
            )
            if not is_pro_enabled():
                return {
                    "verdict": "APPROVE",
                    "rationale": "Second Opinion requires Clew Pro.",
                    "error": "pro_required",
                    "provider_id": "",
                    "model": "",
                    "elapsed_ms": 0.0,
                }
            cfg = get_second_opinion_config()
            # Even if the user disabled it, the explicit run_second_opinion()
            # call from the UI should still go through — we only honour the
            # auto-trigger gating in should_run_second_opinion().
            if self._registry is None:
                self._registry = self._build_registry()
            active_pid = self._registry.active_id or "ollama"
            verdict = review_with_second_model(
                config=cfg,
                tool_name=tool_name,
                args=args,
                risk_level=risk_level,
                risk_reasons=risk_reasons,
                recent_context=recent_context,
                provider_registry=self._registry,
                active_provider_id=active_pid,
            )
            return verdict.to_dict()
        except Exception as e:
            return {
                "verdict": "APPROVE",
                "rationale": f"Second Opinion error: {e}",
                "error": str(e),
                "provider_id": "",
                "model": "",
                "elapsed_ms": 0.0,
            }

    def list_second_opinion_providers(self) -> List[Dict[str, Any]]:
        """Return providers eligible to be the 'second' model.

        Same shape as ``list_providers()`` but excludes the active
        provider (a second opinion from the same provider is pointless).
        """
        try:
            all_p = self.list_providers()
            active_pid = None
            for p in all_p:
                if p.get("active"):
                    active_pid = p.get("id")
                    break
            return [p for p in all_p if p.get("id") != active_pid]
        except Exception:
            return []

    # ── v2.0.1 (G3) — Token budget / efficiency ────────────────────

    def get_token_budget(self) -> Dict[str, Any]:
        """Return the current token budget + live usage against the caps."""
        try:
            from clew.token_budget import get_token_budget, check_budget
            budget = get_token_budget()
            # Run the check against the live tracker if available
            check = check_budget(budget=budget, token_tracker=self._tracker)
            return {
                "ok": True,
                **budget.to_dict(),
                "day_cost": check.daily_used,
                "month_cost": check.monthly_used,
                "day_used_pct": check.day_used_pct,
                "month_used_pct": check.month_used_pct,
                "exceeded": check.exceeded,
                "reason": check.reason,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_token_budget(
        self,
        *,
        daily_usd: Optional[float] = None,
        monthly_usd: Optional[float] = None,
        max_tokens_per_turn: Optional[int] = None,
        max_iterations: Optional[int] = None,
        compaction_threshold_pct: Optional[int] = None,
        prompt_caching: Optional[bool] = None,
        predictable_mode: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update token budget fields. Only non-None fields change.

        Forces an agent rebuild on next turn so settings take effect.
        """
        try:
            from clew.token_budget import set_token_budget as _set
            new_budget = _set(
                daily_usd=daily_usd,
                monthly_usd=monthly_usd,
                max_tokens_per_turn=max_tokens_per_turn,
                max_iterations=max_iterations,
                compaction_threshold_pct=compaction_threshold_pct,
                prompt_caching=prompt_caching,
                predictable_mode=predictable_mode,
            )
            # Force agent rebuild so max_iterations / max_tokens take effect
            self._agent = None
            return {"ok": True, **new_budget.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reset_token_budget(self) -> Dict[str, Any]:
        """Restore the default token budget."""
        try:
            from clew.token_budget import reset_token_budget as _reset
            budget = _reset()
            self._agent = None
            return {"ok": True, **budget.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def check_budget(self) -> Dict[str, Any]:
        """Convenience: check whether the budget has been exceeded.

        Returns {ok, exceeded, reason, daily_used, monthly_used, ...}.
        """
        try:
            from clew.token_budget import get_token_budget, check_budget
            budget = get_token_budget()
            check = check_budget(budget=budget, token_tracker=self._tracker)
            return {"ok": True, **check.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── v2.0.1 (G4) — Cross-model verification ─────────────────────

    def verify_last_response(
        self,
        verifier_provider_id: Optional[str] = None,
        verifier_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a cross-model verification of the most recent agent output.

        Picks a verifier from a different model family than the active
        provider (unless the user pinned one), then asks it to flag
        correctness / safety / completeness issues in the last response.

        Returns {ok, verification, verifier_provider, verifier_model,
        elapsed_ms, error}.
        """
        try:
            from clew.second_opinion import (
                is_pro_enabled, get_second_opinion_config,
                resolve_second_provider,
            )
            from clew.providers import ProviderMessage

            # Capture the last response BEFORE any UI work.
            last_output = ""
            if self._agent is not None:
                try:
                    msgs = self._agent.memory.messages
                    for m in reversed(msgs):
                        if getattr(m, "role", "") == "assistant" and getattr(m, "content", ""):
                            last_output = m.content
                            break
                except Exception:
                    pass

            if not last_output:
                return {
                    "ok": False,
                    "error": "No prior assistant response to verify.",
                }

            if self._registry is None:
                self._registry = self._build_registry()

            active_pid = self._registry.active_id or "ollama"

            # Resolve verifier
            if verifier_provider_id:
                v_pid = verifier_provider_id
                v_model = verifier_model or ""
            else:
                cfg = get_second_opinion_config()
                v_pid, v_model = resolve_second_provider(active_pid, cfg)

            if not v_model:
                try:
                    cls = self._registry._classes.get(v_pid)
                    v_model = cls.default_model if cls else ""
                except Exception:
                    v_model = ""

            provider = self._registry.get(v_pid)
            if not provider.is_loaded:
                provider.load()

            # Capture the user's last prompt for context
            last_user = ""
            if self._agent is not None:
                try:
                    for m in reversed(self._agent.memory.messages):
                        if getattr(m, "role", "") == "user" and getattr(m, "content", ""):
                            last_user = m.content
                            break
                except Exception:
                    pass

            system_prompt = (
                "You are an independent verifier reviewing another AI agent's response.\n"
                "The user asked a question; another model produced the answer below.\n"
                "Your job is to flag correctness, safety, and completeness issues — "
                "NOT to rewrite the answer.\n\n"
                "Return STRICT JSON:\n"
                "{\n"
                "  \"overall\": \"PASS\" | \"WARN\" | \"FAIL\",\n"
                "  \"correctness\": \"PASS\" | \"WARN\" | \"FAIL\",\n"
                "  \"safety\":      \"PASS\" | \"WARN\" | \"FAIL\",\n"
                "  \"completeness\": \"PASS\" | \"WARN\" | \"FAIL\",\n"
                "  \"issues\": [\"...\", ...],\n"
                "  \"suggestions\": [\"...\", ...],\n"
                "  \"summary\": \"<one or two sentences>\"\n"
                "}\n"
                "If the answer is fine, return PASS with empty issues.\n"
            )
            user_prompt = (
                f"## User's request\n{last_user[:2000]}\n\n"
                f"## Agent's response to verify\n{last_output[:6000]}\n\n"
                "Return your verdict JSON now."
            )
            messages = [
                ProviderMessage(role="system", content=system_prompt),
                ProviderMessage(role="user", content=user_prompt),
            ]

            import time as _t
            t0 = _t.time()
            resp = provider.generate(messages, model=v_model)
            raw = getattr(resp, "text", "") or ""
            elapsed = (_t.time() - t0) * 1000

            # Parse JSON
            import json as _json
            import re as _re
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, _re.DOTALL)
            if m:
                raw = m.group(1)
            else:
                m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                if m:
                    raw = m.group(0)
            try:
                verification = _json.loads(raw)
            except Exception:
                verification = {
                    "overall": "WARN",
                    "raw": raw[:2000],
                    "summary": "Verifier response was not valid JSON; raw text included.",
                }

            return {
                "ok": True,
                "verification": verification,
                "verifier_provider": v_pid,
                "verifier_model": v_model,
                "elapsed_ms": round(elapsed, 1),
                "original_chars": len(last_output),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── v2.0.2 (G5) — Agent identity + tool-call audit ────────────

    def get_agent_identity(self) -> Dict[str, Any]:
        """Return the root agent identity for this Clew process."""
        try:
            from clew.agent_identity import get_root_identity
            ident = get_root_identity()
            return {"ok": True, **ident.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_agents(self) -> List[Dict[str, Any]]:
        """Return every agent that has acted in this process, with stats."""
        try:
            from clew.agent_identity import get_audit_trail
            trail = get_audit_trail()
            return trail.list_agents()
        except Exception:
            return []

    def get_agent_audit_summary(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Per-agent breakdown of tool calls, errors, durations.

        If ``agent_id`` is None, returns the full per-agent summary
        (keyed by agent id). Otherwise returns only that agent's row.
        """
        try:
            from clew.agent_identity import get_audit_trail
            trail = get_audit_trail()
            summary = trail.agent_summary(agent_id=agent_id)
            return {"ok": True, "summary": summary}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def filter_audit_by_agent(
        self, agent_id: str, include_children: bool = True, limit: int = 200,
    ) -> Dict[str, Any]:
        """Return audit entries attributed to ``agent_id`` (and optionally
        its descendant agents)."""
        try:
            from clew.agent_identity import get_audit_trail
            trail = get_audit_trail()
            entries = trail.filter_by_agent(
                agent_id=agent_id,
                include_children=include_children,
                limit=limit,
            )
            return {"ok": True, "entries": entries, "count": len(entries)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_audit_json(self, with_fingerprints: bool = True) -> Dict[str, Any]:
        """Export the full audit trail as JSON (with optional SHA-256 fingerprints)."""
        try:
            from clew.agent_identity import get_audit_trail
            trail = get_audit_trail()
            return {
                "ok": True,
                "json": trail.export_audit_json(with_fingerprints=with_fingerprints),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_audit_csv(self) -> Dict[str, Any]:
        """Export the audit trail as CSV (compact, no large args)."""
        try:
            from clew.agent_identity import get_audit_trail
            trail = get_audit_trail()
            return {"ok": True, "csv": trail.export_audit_csv()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def spawn_subidentity(self, role: str, name: str = "") -> Dict[str, Any]:
        """Derive a child AgentIdentity from the root (for subagent attribution).

        Returns the new identity dict (does NOT spawn an actual agent —
        the runtime is responsible for using the returned identity when
        recording subsequent tool calls).
        """
        try:
            from clew.agent_identity import get_root_identity
            ident = get_root_identity().child(role=role, name=name)
            return {"ok": True, **ident.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── v2.0.2 (G6) — Post-task handoff (CMS / editable) ──────────

    def create_handoff(
        self,
        output: str,
        prompt: str = "",
        title: str = "",
        agent_identity: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Parse an agent output into an editable HandoffDocument and persist it.

        Returns {ok, doc} where ``doc`` is the full handoff dict
        (id, title, blocks, ...). Use ``set_handoff_block_status`` to
        edit individual blocks, and ``build_handoff_revision_prompt``
        to compile the user's edits into a follow-up agent prompt.
        """
        try:
            from clew.handoff_bridge import parse_agent_output, get_handoff_store
            # Default to the root agent identity if none given.
            if agent_identity is None:
                try:
                    from clew.agent_identity import get_root_identity
                    agent_identity = get_root_identity().to_dict()
                except Exception:
                    agent_identity = {}
            doc = parse_agent_output(
                output=output, prompt=prompt, agent=agent_identity, title=title,
            )
            get_handoff_store().save(doc)
            return {"ok": True, "doc": doc.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_handoffs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return metadata for saved handoff documents (no block contents)."""
        try:
            from clew.handoff_bridge import get_handoff_store
            return get_handoff_store().list_docs(limit=limit)
        except Exception:
            return []

    def get_handoff(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the full handoff document (with blocks) by id."""
        try:
            from clew.handoff_bridge import get_handoff_store
            doc = get_handoff_store().load(doc_id)
            return doc.to_dict() if doc else None
        except Exception:
            return None

    def set_handoff_block_status(
        self,
        doc_id: str,
        block_id: str,
        status: str,
        comment: str = "",
        replacement: str = "",
    ) -> Dict[str, Any]:
        """Update a single handoff block's status / comment / replacement."""
        try:
            from clew.handoff_bridge import get_handoff_store
            doc = get_handoff_store().set_block_status(
                doc_id=doc_id, block_id=block_id, status=status,
                comment=comment, replacement=replacement,
            )
            if doc is None:
                return {"ok": False, "error": f"Handoff {doc_id} not found"}
            return {"ok": True, "doc": doc.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def toggle_handoff_todo(self, doc_id: str, block_id: str) -> Dict[str, Any]:
        """Flip a todo block's checked state."""
        try:
            from clew.handoff_bridge import get_handoff_store
            doc = get_handoff_store().toggle_todo(doc_id, block_id)
            if doc is None:
                return {"ok": False, "error": f"Handoff or block not found"}
            return {"ok": True, "doc": doc.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reorder_handoff_blocks(
        self, doc_id: str, new_order: List[str],
    ) -> Dict[str, Any]:
        """Reorder blocks by id."""
        try:
            from clew.handoff_bridge import get_handoff_store
            doc = get_handoff_store().reorder_blocks(doc_id, new_order)
            if doc is None:
                return {"ok": False, "error": f"Handoff {doc_id} not found"}
            return {"ok": True, "doc": doc.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_handoff(self, doc_id: str) -> Dict[str, Any]:
        try:
            from clew.handoff_bridge import get_handoff_store
            ok = get_handoff_store().delete(doc_id)
            return {"ok": ok}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def build_handoff_revision_prompt(self, doc_id: str) -> Dict[str, Any]:
        """Compile the user's edits into a structured revision prompt.

        Returns {ok, prompt}. ``prompt`` is "" if there are no pending
        revisions. The caller (TUI/GUI) typically feeds this back to
        ``run_prompt`` so the agent addresses the user's edits.
        """
        try:
            from clew.handoff_bridge import get_handoff_store
            prompt = get_handoff_store().build_revision_prompt(doc_id)
            return {"ok": True, "prompt": prompt}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_handoff_markdown(self, doc_id: str) -> Dict[str, Any]:
        """Render a handoff document as a single Markdown string."""
        try:
            from clew.handoff_bridge import get_handoff_store
            md = get_handoff_store().export_markdown(doc_id)
            if not md:
                return {"ok": False, "error": f"Handoff {doc_id} not found"}
            return {"ok": True, "markdown": md}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── v2.0.2 (M2) — Smart cost-aware provider routing ───────────

    def get_cost_router_config(self) -> Dict[str, Any]:
        """Return the current cost-router configuration."""
        try:
            from clew.cost_router import get_cost_router
            cfg = get_cost_router().get_config()
            return {"ok": True, **cfg.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_cost_router_config(self, **kwargs: Any) -> Dict[str, Any]:
        """Patch one or more cost-router config fields."""
        try:
            from clew.cost_router import get_cost_router
            cfg = get_cost_router().update_config(**kwargs)
            return {"ok": True, **cfg.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_cost_cap(self, complexity: str, usd: float) -> Dict[str, Any]:
        """Set the USD cap for a single complexity tier."""
        try:
            from clew.cost_router import get_cost_router
            cfg = get_cost_router().set_cap(complexity, usd)
            return {"ok": True, **cfg.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def cost_route(
        self,
        prompt: str,
        configured_providers: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Run the cost-aware router on a prompt and return the decision."""
        try:
            from clew.cost_router import get_cost_router
            # Build configured_providers from the registry if not supplied.
            if configured_providers is None and self._registry is not None:
                configured_providers = {
                    p["id"] for p in self._registry.list_providers()
                    if p.get("configured") or p.get("id") in ("ollama", "lmstudio")
                }
            decision = get_cost_router().route(
                prompt=prompt, configured_providers=configured_providers,
            )
            return {"ok": True, **decision.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def apply_cost_route_decision(
        self,
        prompt: str,
        configured_providers: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Run cost routing AND apply the resulting provider/model selection.

        This is what the runtime should call BEFORE dispatching a prompt
        if cost-aware routing is enabled. It sets the active provider on
        the registry and returns the decision so the UI can show it.
        """
        try:
            decision = self.cost_route(prompt, configured_providers)
            if not decision.get("ok"):
                return decision
            final = decision.get("final_pick") or {}
            pid = final.get("provider_id")
            model = final.get("model")
            if pid:
                self.set_provider(pid, model or None)
            return decision
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── v2.0.2 (M3) — Team spend dashboard ────────────────────────

    def get_user_identity(self) -> Dict[str, Any]:
        """Return the local user identity (creates a default if absent)."""
        try:
            from clew.spend_dashboard import load_identity
            ident = load_identity()
            return {"ok": True, **ident.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_user_team(self, team: str) -> Dict[str, Any]:
        """Update the local user's team and persist."""
        try:
            from clew.spend_dashboard import set_team
            ident = set_team(team)
            return {"ok": True, **ident.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_team_budget(self, team: Optional[str] = None) -> Dict[str, Any]:
        """Return the team's monthly USD budget (0 = no cap)."""
        try:
            from clew.spend_dashboard import load_team_budget
            if team is None:
                from clew.spend_dashboard import load_identity
                team = load_identity().team
            budget = load_team_budget(team)
            return {"ok": True, **budget.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_team_budget(
        self, monthly_usd: float, team: Optional[str] = None, alert_pct: float = 80.0,
    ) -> Dict[str, Any]:
        """Set the team's monthly USD budget."""
        try:
            from clew.spend_dashboard import load_team_budget, save_team_budget, load_identity
            if team is None:
                team = load_identity().team
            budget = load_team_budget(team)
            budget.monthly_usd = float(monthly_usd)
            budget.alert_pct = float(alert_pct)
            save_team_budget(budget)
            return {"ok": True, **budget.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_team_spend_report(self, days: int = 30) -> Dict[str, Any]:
        """Aggregate the local token history into a team spend report."""
        try:
            from clew.spend_dashboard import get_spend_dashboard
            report = get_spend_dashboard().report(days=days)
            return {"ok": True, **report.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def add_spend_source(self, path: str) -> Dict[str, Any]:
        """Add a token_history.jsonl source (file or directory of *.jsonl)."""
        try:
            from clew.spend_dashboard import get_spend_dashboard
            from pathlib import Path as _P
            get_spend_dashboard().add_source(_P(path))
            return {"ok": True, "sources": get_spend_dashboard().list_sources()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_spend_sources(self) -> Dict[str, Any]:
        try:
            from clew.spend_dashboard import get_spend_dashboard
            return {"ok": True, "sources": get_spend_dashboard().list_sources()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_spend_report_json(self, days: int = 30) -> Dict[str, Any]:
        try:
            from clew.spend_dashboard import get_spend_dashboard
            return {"ok": True, "json": get_spend_dashboard().export_report_json(days=days)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_spend_report_csv(self, days: int = 30) -> Dict[str, Any]:
        try:
            from clew.spend_dashboard import get_spend_dashboard
            return {"ok": True, "csv": get_spend_dashboard().export_report_csv(days=days)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
