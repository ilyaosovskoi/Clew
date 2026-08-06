# CHANGES — Update 12 (v2.2.0)

**Date:** 2026-08-05
**Theme:** Remove Qt / PySide6 — GUI is now served by a plain HTTP server (browser-based Web UI).

## Headline changes

### Qt / PySide6 removed

The legacy PySide6 / QWebEngineView desktop GUI has been removed. The
GUI is now served by **`clew.web_server.ClewWebServer`** — a plain
`http.server.HTTPServer` that:

- Serves the existing HTML/CSS/JS frontend from `clew/web/`.
- Serves the existing JSON REST API + SSE on `/api/*` (delegated to
  `clew.api_server.ClewAPIHandler`).
- Authenticates mutating endpoints with a process-local bearer token
  (same scheme as the legacy embedded server).
- Auto-opens the user's default browser on start (unless `--no-browser`).

Run it with:

```bash
python -m clew                      # http://127.0.0.1:18732
python -m clew --port 8000          # custom port
python -m clew --host 0.0.0.0       # share on LAN
python -m clew --project /path      # open a specific project
```

### What got rewritten

| File | Before (v2.1.0) | After (v2.2.0) |
|------|-----------------|----------------|
| `clew/__main__.py` | Routed to `clew.app.main` (Qt) | Routes to `clew.web_server.main` (HTTP) |
| `clew/app.py` | Constructed `QApplication`, `ClewMainWindow` | Thin shim → `clew.web_server.main` |
| `clew/main_window.py` | 608-line `QMainWindow` wrapping `QWebEngineView` | Stub that raises `ClewMainWindowRemovedError` |
| `clew/web_server.py` | **(new)** | HTTP server: static + REST + SSE in one process |
| `clew/web_bridge/__init__.py` | Re-exported `ClewBridge`, `GenerationWorker`, `OneShotWorker`, `TitleWorker` | Re-exports only path/config helpers; Qt classes are stubs |
| `clew/web_bridge/bridge.py` | 4400-line `QObject` with `@Slot` methods | Stub that raises `ClewBridgeRemovedError` |
| `clew/web_bridge/workers.py` | Three `QThread` subclasses | Stubs that raise `RuntimeError` |
| `clew/agent_runtime/worker.py` | `QThread` subclass | `threading.Thread` subclass with a `_Signal` shim that preserves `connect`/`emit` |
| `clew/code_viewer.py` | Used `QFileSystemWatcher` | Uses a polling watcher (`threading.Thread` + `os.walk` every 2 s) |
| `clew/auto_updater.py` | `QObject` + `Signal` + `QThread` | Plain Python class + callback-list `_Signal` shim + `threading.Thread` |
| `clew/lsp_client.py` | `QObject` + `Signal` + `QThread` reader | Plain Python class + callback-list `_Signal` shim + `threading.Thread` reader |
| `clew/web/index.html` | Loaded `qrc:///qtwebchannel/qwebchannel.js` | Removed the QWebChannel script tag; loads `bridge_shim.js` instead |
| `clew/web/bridge_shim.js` | **(new)** | Emulates `window.bridge` (legacy Qt signal/slot API) on top of HTTP + SSE so `app.js` keeps working unchanged |
| `pyproject.toml` | `PySide6`, `PySide6-Addons`, `py2app`, `pytest-qt` deps | All Qt deps removed; `Environment :: Web Environment` classifier added |
| `requirements.txt` | Listed `PySide6>=6.7.0`, `PySide6-Addons>=6.7.0` | Removed; added a comment explaining the Web UI |
| `clew/__init__.py` | `__version__ = "2.0.0"` | `__version__ = "2.2.0"` |

### Why a shim instead of a frontend rewrite?

The existing `clew/web/app.js` (5896 lines) was written against the
QWebChannel `window.bridge` API: `window.bridge.token_streamed.connect(fn)`,
`window.bridge.send_message({...})`, etc. Rather than rewrite 5800 lines
of battle-tested UI logic, `bridge_shim.js` emulates the same surface on
top of HTTP + SSE:

- Each Qt `Signal` becomes a callback-list `_Signal` shim with `connect`/`emit`.
- Each `@Slot` method becomes an async function that `fetch()`es the
  corresponding `/api/*` endpoint.
- SSE streams (`/api/chat/stream`, `/api/agent/stream`) are pumped by
  `_streamSSE()` and dispatched to the right signal.
- A 5-second poller for `/api/chat/list` replaces the Qt event loop's
  push-based `chat_list_changed` signal.

This means **every existing UI feature keeps working** without touching
`app.js`. The shim is a strict superset of the legacy bridge API — new
code should prefer calling the HTTP API directly.

## Tests added

| File | Tests | Purpose |
|------|-------|---------|
| `clew/tests/test_v22_web_server.py` | 38 | ClewWebServer lifecycle, static serving, REST + SSE passthrough, auth, CLI parsing, Qt removal smoke checks |
| `clew/tests/test_v22_qt_free_refactors.py` | 37 | `code_viewer` polling watcher, `auto_updater` plain Python, `lsp_client` plain Python, `agent_runtime/worker` threading.Thread, `_Signal` shim |
| `clew/tests/test_v22_tui_comprehensive.py` | 42 | TUI bridge, slash commands in `app.py`, section switching, headless Pilot mount tests, `__main__` arg parsing, app bindings, CSS files exist, all widgets import cleanly |
| `clew/tests/test_v22_integration.py` | 111 | CLI, daemon, api_server, providers, hook_system, checkpoint, agent_runtime, slash_commands, skill_loader, activity_log, token_tracker, consensus_engine (G15), audit_signing (G16), handoff_bridge (G6), cost_router (M2), github_automation (G11), mcp_server (G13), agent_identity (G5), capability_catalog (G7), second_opinion (M1), token_budget (G3), spend_dashboard (M3), learning_loop (G17), web_search_backend (G18), diff_service, git_service, office_worker, utils, project_context, context_manager, memory_service, quota, notifier, mcp_client, mcp_manager, auto_router, command_policy, inbound_listener, request_queue, collaboration, swarm_manager, agent_orchestrator, task_decomposition_router, benchmarks, agent v2 runtime |
| `clew/tests/conftest.py` | — | Registers the `interaction` pytest marker for the new TUI Pilot tests |
| **Total new** | **228** | |

Full results in `docs/TEST_RESULTS_update_12.md`.

## Backward compatibility

### Preserved

- All `clew_tui.*` entry points and bindings (`Ctrl+C`, `Ctrl+D`, `Ctrl+G`, `Ctrl+P`, `Ctrl+T`).
- All slash commands (`/checkpoint`, `/rewind`, `/hooks`, `/github`, `/audit`, `/handoff`, `/capabilities`, `/second_opinion`, `/budget`, `/verify`, `/agents`, `/cost`, `/spend`, `/consensus`, `/audit-signed`, `/learnings`, `/websearch`).
- All 16 providers (Anthropic, OpenAI, Groq, DeepSeek, Z.ai, Gemini, Mistral, Together, Fireworks, xAI, Cerebras, SambaNova, Ollama, LM Studio, OpenRouter, Nvidia NIM).
- All REST endpoints under `/api/*` (no path changes, no auth changes).
- All `clew.web_bridge` path/config helpers (`_load_config`, `_save_config`, `_chat_path`, `_load_chat`, `_save_chat`, etc.).
- `AgentRuntime`, `AgentWorker`, `ToolEngine`, `ContextMemory` public API.
- The `clew` / `clew-gui` / `clew_gui` / `clew-cli` / `clew-acp` / `clew-daemon` console-script entry points.

### Removed / stubbed

- `clew.main_window.ClewMainWindow` — was a `QMainWindow`. Now raises `ClewMainWindowRemovedError` on construction. Use `clew.web_server.ClewWebServer` instead.
- `clew.web_bridge.bridge.ClewBridge` — was a `QObject` exposed via QWebChannel. Now raises `ClewBridgeRemovedError`. Use the HTTP API at `/api/*` instead (or `clew_tui.bridge.ClewBridge` for an in-process Python bridge).
- `clew.web_bridge.workers.{GenerationWorker, OneShotWorker, TitleWorker}` — were `QThread` subclasses. Now raise `RuntimeError`. Use `POST /api/chat/stream` and `POST /api/chat/oneshot` instead.

## Migration guide

### For users

```bash
# Before (v2.1.0):
python -m clew                # opened a Qt window

# After (v2.2.0):
python -m clew                # starts an HTTP server and opens the default browser
python -m clew --port 8000    # custom port
python -m clew --no-browser   # don't auto-open the browser
```

The browser URL prints on startup. The same `~/.clew/config.json` and
`~/.clew/chats/*.json` files are used — no migration step needed.

### For developers

If your code imported `clew.web_bridge.ClewBridge`, switch to one of:

1. **For browser-facing code:** Use `fetch('/api/...')` against
   `ClewWebServer` (or `ClewAPIServer`).
2. **For in-process Python code:** Use `clew_tui.bridge.ClewBridge`,
   which is plain Python and was always Qt-free.
3. **For scripts / tests:** Use `clew.api_server.ClewAPIServer` directly
   (no UI, no Qt, no threads beyond the HTTP listener).

If your code imported `AgentWorker` from `clew.agent_runtime`, nothing
changes — the class signature is identical, it just no longer inherits
from `QThread`. The `result_ready`, `step_update`, `error` "signals"
are now a tiny callback-list `_Signal` shim with the same `connect` /
`emit` API.

If your code imported `LSPClient` or `AutoUpdater`, same story —
same constructor signature, same signal names, just plain Python
under the hood.
