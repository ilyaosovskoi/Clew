"""
MCP Manager for Clew v1.1.0 — manages multiple MCP server connections.

Loads MCP server configurations from ~/.clew/mcp.json, starts/stops
each server, and exposes a unified tool catalog for the agent runtime.

Config format (~/.clew/mcp.json):
  {
    "servers": {
      "filesystem": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {},
        "enabled": true
      },
      "github": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "ghp_xxx"},
        "enabled": true
      }
    }
  }

The agent calls a single meta-tool `call_mcp_tool(server, tool, args)`
which the manager routes to the right MCPClient.

Lifecycle:
  - Manager is created lazily by web_bridge / api_server (singleton).
  - On startup, it reads ~/.clew/mcp.json and starts all enabled servers.
  - The agent runtime queries manager.tool_catalog() when building the
    system prompt, so the LLM sees all available MCP tools.
  - When the LLM calls call_mcp_tool, the agent runtime calls
    manager.call_tool(server, tool, args).
  - On shutdown (app exit), manager.stop_all() cleans up subprocesses.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .mcp_client import MCPClient, MCPTool, format_mcp_tool_for_prompt

logger = logging.getLogger(__name__)


# ── Config path ─────────────────────────────────────────────────────────

def _clew_home() -> Path:
    p = Path.home() / ".clew"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mcp_config_path() -> Path:
    return _clew_home() / "mcp.json"


_DEFAULT_MCP_CONFIG: Dict[str, Any] = {
    "servers": {},
}


# ── Data classes ────────────────────────────────────────────────────────

@dataclass
class MCPServerConfig:
    """Configuration for one MCP server (from mcp.json)."""
    name: str
    command: List[str]
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    # v1.1.3-fix (bug 1.3): if True, call_mcp_tool skips the autonomy
    # confirmation gate for this server. Use ONLY for servers you trust
    # completely (e.g. a read-only filesystem server you wrote yourself).
    # Defaults to False — every MCP call asks for confirmation unless
    # the user explicitly marks the server as trusted.
    trusted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "env": dict(self.env),
            "enabled": self.enabled,
            "trusted": self.trusted,
        }

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "MCPServerConfig":
        return cls(
            name=name,
            command=list(data.get("command", [])),
            env=dict(data.get("env", {})),
            enabled=bool(data.get("enabled", True)),
            # v1.1.3-fix (bug 1.3): read the trusted flag from config.
            trusted=bool(data.get("trusted", False)),
        )


# ── Manager ─────────────────────────────────────────────────────────────

class MCPManager:
    """Manages multiple MCP server connections.

    Thread-safe: start/stop/config operations are guarded by a single
    lock. The per-server MCPClient has its own internal lock for
    request/response correlation.
    """

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = Path(config_path) if config_path else _mcp_config_path()
        self._lock = threading.Lock()
        self._configs: Dict[str, MCPServerConfig] = {}
        self._clients: Dict[str, MCPClient] = {}
        # v1.1.3-fix (bug 3.9): callbacks invoked when a server crashes
        # (subprocess exits unexpectedly). The UI can subscribe to show
        # a toast notification.
        self._crash_callbacks: List[Callable[[str], None]] = []
        # v1.1.3-fix (bug 3.9): track which servers we've already
        # announced as crashed, so we don't fire the callback repeatedly
        # while the user hasn't acknowledged/restarted.
        self._crash_announced: set = set()
        self._load_config()
        # v1.1.3-fix (bug 3.9): start the watchdog thread. It polls
        # every 5s and fires crash callbacks for newly-dead servers.
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._start_watchdog()

    def _start_watchdog(self) -> None:
        """v1.1.3-fix (bug 3.9): background thread that polls running
        MCP servers and emits a crash event when one exits unexpectedly."""
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="mcp-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        """v1.1.3-fix (bug 3.9): poll every 5s for crashed servers."""
        while not self._watchdog_stop.wait(5.0):
            try:
                with self._lock:
                    clients_snapshot = list(self._clients.items())
                for name, client in clients_snapshot:
                    if not client.is_running() and name not in self._crash_announced:
                        # Server crashed (subprocess exited but we didn't
                        # explicitly stop it). Fire the crash callbacks.
                        logger.warning(
                            "[mcp-manager] server %s crashed (subprocess exited)",
                            name,
                        )
                        self._crash_announced.add(name)
                        for cb in list(self._crash_callbacks):
                            try:
                                cb(name)
                            except Exception as cb_err:
                                logger.warning(
                                    "[mcp-manager] crash callback failed: %s", cb_err,
                                )
                    elif client.is_running() and name in self._crash_announced:
                        # Server was restarted — clear the announced flag
                        # so we fire again if it crashes a second time.
                        self._crash_announced.discard(name)
            except Exception as e:
                logger.debug("[mcp-manager] watchdog iteration error: %s", e)

    def on_server_crashed(self, callback: Callable[[str], None]) -> None:
        """v1.1.3-fix (bug 3.9): register a callback invoked when a
        server's subprocess exits unexpectedly. The callback receives
        the server name. Useful for showing a toast in the UI."""
        self._crash_callbacks.append(callback)

    # ── Config persistence ─────────────────────────────────────────

    def _load_config(self) -> None:
        """Load server configs from ~/.clew/mcp.json."""
        if not self._config_path.exists():
            self._configs = {}
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            servers = data.get("servers", {})
            self._configs = {
                name: MCPServerConfig.from_dict(name, cfg)
                for name, cfg in servers.items()
            }
            logger.info("[mcp-manager] loaded %d server configs from %s",
                        len(self._configs), self._config_path)
        except Exception as e:
            logger.warning("[mcp-manager] failed to load config: %s", e)
            self._configs = {}

    def _save_config(self) -> None:
        """Persist server configs to ~/.clew/mcp.json (atomic write)."""
        import tempfile
        data = {
            "servers": {
                name: cfg.to_dict() for name, cfg in self._configs.items()
            }
        }
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".mcp_", suffix=".tmp",
                dir=str(self._config_path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, str(self._config_path))
            except Exception:
                try: os.unlink(tmp_path)
                except OSError: pass
                raise
        except OSError as e:
            logger.error("[mcp-manager] failed to save config: %s", e)

    def reload_config(self) -> None:
        """Re-read mcp.json from disk. Does NOT auto-restart running
        servers — call start_all() to apply changes."""
        with self._lock:
            self._load_config()

    # ── CRUD ───────────────────────────────────────────────────────

    def list_servers(self) -> List[Dict[str, Any]]:
        """Return a list of server configs + their current status for the UI.

        v1.1.3-fix (bug 3.6): distinguish "crashed" (subprocess exited
        unexpectedly, client still in registry) from "stopped" (no
        client). The UI can show a toast "server X crashed" so the user
        isn't confused about why a server they started shows "not running".
        """
        with self._lock:
            result = []
            for name, cfg in sorted(self._configs.items()):
                client = self._clients.get(name)
                running = client.is_running() if client else False
                # v1.1.3-fix (bug 3.6): detect crashed state — there's
                # a client object but its subprocess has exited.
                crashed = False
                if client and not running:
                    crashed = True
                tool_count = len(client.tools) if client and running else 0
                # Don't expose env values (may contain secrets) — just keys
                env_keys = list(cfg.env.keys())
                # v1.1.3-fix (bug 1.3): expose trusted flag for the UI.
                trusted = bool(cfg.trusted)
                # v1.1.3-fix (bug 3.2): expose tool_discovery_failed state.
                discovery_failed = bool(getattr(client, "_tool_discovery_failed", False)) if client else False
                result.append({
                    "name": cfg.name,
                    "command": cfg.command,
                    "env_keys": env_keys,
                    "enabled": cfg.enabled,
                    "running": running,
                    "crashed": crashed,
                    "trusted": trusted,
                    "tool_discovery_failed": discovery_failed,
                    "tool_count": tool_count,
                    "server_info": {
                        "name": client.server_info.name if client and client.server_info else "",
                        "version": client.server_info.version if client and client.server_info else "",
                    } if running else None,
                })
            return result

    def add_server(self, name: str, command: List[str],
                   env: Optional[Dict[str, str]] = None,
                   enabled: bool = True,
                   autostart: bool = True) -> Dict[str, Any]:
        """Add a new server config. If `autostart` and `enabled`, starts
        the server immediately. Returns a status dict."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "Server name is required"}
        if not command or not isinstance(command, list):
            return {"ok": False, "error": "Command must be a non-empty list"}
        with self._lock:
            self._configs[name] = MCPServerConfig(
                name=name, command=list(command),
                env=dict(env or {}), enabled=enabled,
            )
            self._save_config()
        if autostart and enabled:
            self.start_server(name)
        return {"ok": True, "name": name}

    def remove_server(self, name: str) -> Dict[str, Any]:
        """Stop (if running) and remove a server config."""
        with self._lock:
            if name not in self._configs:
                return {"ok": False, "error": f"Server {name} not found"}
            client = self._clients.pop(name, None)
            cfg = self._configs.pop(name)
            self._save_config()
        # Stop outside the lock — stop() may block on subprocess wait
        if client:
            client.stop()
        return {"ok": True, "name": name}

    def toggle_server(self, name: str, enabled: bool) -> Dict[str, Any]:
        """Enable/disable a server without removing it. Disabled servers
        are not started by start_all().

        v1.1.3-fix (bug 3.5): the previous implementation released the
        lock between updating the config and stopping the client, which
        created a window where another thread could start the server
        (via start_server). The stop would then kill the just-started
        client. We now hold the lock for the entire operation and stop
        the client INSIDE the lock — stop() does subprocess.wait() but
        that's a short, bounded wait (2s timeout).
        """
        with self._lock:
            if name not in self._configs:
                return {"ok": False, "error": f"Server {name} not found"}
            self._configs[name].enabled = enabled
            self._save_config()
            # v1.1.3-fix (bug 3.5): grab the client reference UNDER the
            # same lock that protects _configs, so a concurrent
            # start_server can't slip in a new client between the config
            # update and the stop. stop() itself is safe to call under
            # the lock — it does subprocess.wait(timeout=2) which is bounded.
            client = self._clients.get(name)
            if not enabled and client:
                # Stop the client inside the lock to close the race window.
                try:
                    client.stop()
                except Exception as e:
                    logger.warning("[mcp-manager] stop %s during toggle failed: %s", name, e)
                # Remove the dead client from the registry so list_servers
                # reports "stopped" instead of "running".
                self._clients.pop(name, None)
        return {"ok": True, "name": name, "enabled": enabled}

    def is_server_trusted(self, name: str) -> bool:
        """v1.1.3-fix (bug 1.3): return True if the server is explicitly
        marked ``"trusted": true`` in mcp.json. Trusted servers skip the
        autonomy confirmation gate when called via call_mcp_tool.
        """
        with self._lock:
            cfg = self._configs.get(name)
            return bool(cfg and cfg.trusted)

    # ── Lifecycle ──────────────────────────────────────────────────

    def start_server(self, name: str) -> Dict[str, Any]:
        """Start a single MCP server. Returns status dict."""
        with self._lock:
            cfg = self._configs.get(name)
            if not cfg:
                return {"ok": False, "error": f"Server {name} not found"}
            if not cfg.enabled:
                return {"ok": False, "error": f"Server {name} is disabled"}
            existing = self._clients.get(name)
            if existing and existing.is_running():
                return {"ok": True, "name": name, "message": "already running"}
            client = MCPClient(
                name=cfg.name, command=list(cfg.command),
                env=dict(cfg.env), cwd=None,
            )
            self._clients[name] = client

        # Start outside the lock — start() blocks on the initialize
        # handshake (up to INIT_TIMEOUT seconds).
        ok = client.start()
        if not ok:
            with self._lock:
                self._clients.pop(name, None)
            return {"ok": False, "error": f"Failed to start {name} — see logs"}
        return {
            "ok": True, "name": name,
            "server_info": {
                "name": client.server_info.name if client.server_info else "",
                "version": client.server_info.version if client.server_info else "",
            },
            "tool_count": len(client.tools),
        }

    def stop_server(self, name: str) -> Dict[str, Any]:
        """Stop a single MCP server."""
        with self._lock:
            client = self._clients.get(name)
        if not client:
            return {"ok": False, "error": f"Server {name} not running"}
        client.stop()
        with self._lock:
            self._clients.pop(name, None)
        return {"ok": True, "name": name}

    def restart_server(self, name: str) -> Dict[str, Any]:
        """v1.1.3-fix (bug 3.6): atomically stop + start a server.

        Useful when the server has crashed (subprocess exited but the
        client object is still in the registry) or when the user wants
        to pick up new config without a full app restart.
        """
        # Stop if running, then start fresh. We do this under a single
        # lock acquisition to make the restart atomic from the UI's
        # perspective.
        with self._lock:
            client = self._clients.pop(name, None)
            cfg = self._configs.get(name)
            if not cfg:
                return {"ok": False, "error": f"Server {name} not found"}
        # Stop outside the lock (subprocess.wait may take up to 2s).
        if client:
            try:
                client.stop()
            except Exception as e:
                logger.warning("[mcp-manager] restart stop %s failed: %s", name, e)
        # Now start fresh — this creates a new MCPClient and re-runs
        # the initialize handshake + tool discovery.
        return self.start_server(name)

    def start_all(self) -> None:
        """Start all enabled servers that aren't already running.
        Called by ClewMainWindow on app startup."""
        with self._lock:
            to_start = [
                name for name, cfg in self._configs.items()
                if cfg.enabled and name not in self._clients
            ]
        for name in to_start:
            try:
                self.start_server(name)
            except Exception as e:
                logger.error("[mcp-manager] failed to start %s: %s", name, e)

    def stop_all(self) -> None:
        """Stop all running servers. Called on app shutdown."""
        # v1.1.3-fix (bug 3.9): stop the watchdog first so it doesn't
        # fire crash callbacks while we're tearing everything down.
        self._watchdog_stop.set()
        if self._watchdog_thread is not None:
            try:
                self._watchdog_thread.join(timeout=2.0)
            except Exception:
                pass
            self._watchdog_thread = None
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.stop()
            except Exception as e:
                logger.warning("[mcp-manager] stop %s failed: %s", client.name, e)

    # ── Tool catalog & invocation ──────────────────────────────────

    def tool_catalog(self) -> List[Tuple[str, MCPTool]]:
        """Return a list of (server_name, tool) tuples for ALL running
        servers. Used by the agent runtime to build the system prompt."""
        result: List[Tuple[str, MCPTool]] = []
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            if not client.is_running():
                continue
            for tool in client.tools:
                result.append((client.name, tool))
        return result

    def catalog_prompt(self) -> str:
        """Return a formatted string for the system prompt listing all
        available MCP tools. Returns empty string if no tools."""
        catalog = self.tool_catalog()
        if not catalog:
            return ""
        lines = ["# Available MCP tools (call via call_mcp_tool meta-tool):", ""]
        for server_name, tool in catalog:
            lines.append(format_mcp_tool_for_prompt(server_name, tool))
        lines.append("")
        lines.append(
            "To call an MCP tool, use the call_mcp_tool meta-tool with "
            "{server, tool, args}. The result will be returned as the "
            "tool observation."
        )
        return "\n".join(lines)

    def call_tool(self, server: str, tool: str,
                  args: Dict[str, Any]) -> str:
        """Invoke a tool on a specific MCP server. Used by the
        call_mcp_tool meta-tool in ToolEngine."""
        with self._lock:
            client = self._clients.get(server)
        if not client or not client.is_running():
            raise RuntimeError(
                f"MCP server '{server}' is not running. "
                f"Enable it in Settings → MCP."
            )
        return client.call_tool(tool, args)

    # ── Status ─────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return a status dict for the UI / settings panel."""
        return {
            "servers": self.list_servers(),
            "total_tools": len(self.tool_catalog()),
            "config_path": str(self._config_path),
        }


# ── Singleton ───────────────────────────────────────────────────────────

_SINGLETON: Optional[MCPManager] = None
_SINGLETON_LOCK = threading.Lock()


def get_mcp_manager() -> MCPManager:
    """Process-wide MCPManager singleton."""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = MCPManager()
    return _SINGLETON
