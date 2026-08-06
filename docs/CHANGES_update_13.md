# CHANGES — update_13

**Version:** v2.2.1
**Date:** 2026-08-05
**Scope:** Web UI expansion — exposes every backend capability through the browser, removes the obsolete 3-section switcher, adds custom-provider management (including Nvidia NIM).

## What changed

### 1. Unified Tools sidebar (replaces 3-section switcher)

The General / Heavy Code / Office Worker section buttons were removed from the sidebar. All three sections already shared the same chat pipeline (post-v2.0 merge), so the switcher was misleading UI. In its place, a new **Tools** sidebar section lists 16 capabilities as nav items:

| Tool | What it does |
|------|--------------|
| Capabilities | Browse & run pre-built prompt templates |
| Hooks | Register / test / toggle pre/post-tool hooks |
| Checkpoints | Snapshot state and rewind agent mistakes |
| Handoffs | Editable post-task handoff documents |
| GitHub | PR/issue automation + implementation context |
| Audit | Agent identity + signed Ed25519 audit trail |
| Spend | Team token usage + cost dashboard |
| Consensus | Multi-provider parallel run with divergence diff |
| Second Opinion | Cross-model review of an agent response |
| Verify | Cross-model verification of the last response |
| Learnings | Auto-detected learnings (rollbacks, CI failures) |
| CI Templates | Generate GitHub Action workflow YAML |
| Notifications | Telegram / Discord / Slack configuration |
| Daemon | Background task queue + remote execution |
| MCP Server | Clew-as-MCP-server status + tool list |
| Persona | System-prompt persona editor |
| Providers | Custom provider wizard (Nvidia NIM, OpenAI-compat, …) |

Clicking any of them opens a full-height drawer on the right side of the screen, with rich per-feature UI (forms, tables, modals, exports).

### 2. Custom provider wizard (incl. Nvidia NIM)

A new "Providers" tool and a "Custom providers" card in the existing Settings → Providers tab both open the wizard. Built-in templates:

- **Nvidia NIM** (`https://integrate.api.nvidia.com/v1`) — pre-filled with the Llama 3.1 8B model and the correct env var (`NVIDIA_API_KEY`). Free key at https://build.nvidia.com/.
- **OpenAI-compatible (generic)** — any endpoint exposing `POST /v1/chat/completions`.
- **Ollama (local)** — `http://127.0.0.1:11434`, no API key needed.
- **LM Studio (local)** — `http://127.0.0.1:1234/v1`.

The wizard supports:
- Provider ID (unique slug)
- Display name
- Provider type (dropdown)
- Base URL
- Default model
- API key (password field, stored at `~/.clew/providers.yaml`)
- Env var name (alternative to hard-coding the key)
- Context window (tokens)
- **Test connection** button — pings the endpoint with a "ping" message before saving
- **Save** — writes to YAML and hot-reloads the registry

Listing existing custom providers masks the API key (`nvap…2345` format) — the full key never leaves the server.

### 3. 118 new REST API endpoints

A new module `clew/api_extended.py` (1715 lines) installs 56 GET + 62 POST endpoints by monkey-patching `ClewAPIHandler` at import time. Every endpoint group has its own family:

- Custom providers: `/api/providers/custom/*` + `/api/providers/templates`
- Capabilities: `/api/capabilities/*`
- Second Opinion: `/api/second_opinion/*`
- Token budget: `/api/budget/*`
- Cross-model verify: `/api/verify/run`
- Agents / Audit: `/api/agents/*` + `/api/audit/*`
- Handoffs: `/api/handoff/*`
- Cost router: `/api/cost/*`
- Spend dashboard: `/api/spend/*`
- Hooks: `/api/hooks/*`
- Checkpoints: `/api/checkpoint/*`
- GitHub: `/api/github/*`
- Consensus: `/api/consensus/*`
- Learnings: `/api/learnings/*`
- Web search status: `/api/websearch/status`
- Persona: `/api/persona/*`
- Router mode: `/api/router/mode`
- MCP server: `/api/mcp_server/*`
- Notifications: `/api/notify/*`
- Daemon: `/api/daemon/*`
- Pro toggle: `/api/pro/*`
- Collaboration: `/api/collaboration/*`
- Persistence: `/api/persistence/*` + `/api/compaction/stats` + `/api/usage/get`
- Slash commands: `/api/slash_commands/*`
- Section (legacy): `/api/section/*`

All new POST routes are added to `ServerContext.MUTATING_PATHS` so the existing bearer-token auth guard protects them. Unauthenticated POSTs return HTTP 401.

### 4. Tests

`clew/tests/test_v221_web_ui_expansion.py` (425 lines, 130 tests):

- All 56 GET routes registered (parametrised test)
- All 62 POST routes registered (parametrised test)
- Provider templates include Nvidia NIM with correct `base_url`, `model`, `provider_type`
- Custom-provider YAML round-trip: add → list (with masking) → remove
- Duplicate-rejection and required-field validation
- Every handler returns a dict with an `ok` flag
- `install()` patches `ClewAPIHandler.do_GET/do_POST/do_DELETE`
- `/api/section/get` always returns "general"

```
$ pytest clew/tests/test_v221_web_ui_expansion.py -v
============================= 130 passed in 0.28s ==============================
```

### 5. Live HTTP smoke test

Verified against a running `ClewWebServer`:

- `GET /api/providers/templates` → 200, contains the `nvidia_nim` template
- `GET /api/capabilities/list` → 200
- `GET /api/checkpoint/list` → 200
- `GET /api/section/get` → `{ok: true, section: "general"}`
- `POST /api/providers/custom/add` (no auth) → **HTTP 401** (auth guard works)
- `POST /api/providers/custom/add` (with auth) → 200, `{ok: true, provider_id: "my-nim-test"}`
- `GET /api/providers/custom/list` → 200, lists the new provider with masked key
- `POST /api/providers/custom/remove` (with auth) → 200, `{ok: true}`

## Files in this update

### New files (4)
- `clew/api_extended.py` — 1715 lines, 118 endpoints + installer
- `clew/web/tools_panels.js` — 1371 lines, 16 panel renderers
- `clew/tests/test_v221_web_ui_expansion.py` — 425 lines, 130 tests
- `docs/CHANGES_update_13.md` — this file

### Modified files (5)
- `clew/api_server.py` — calls `api_extended.install()` at import; docstring expanded; `MUTATING_PATHS` extended at runtime
- `clew/web/index.html` — section switcher removed; Tools sidebar + Tools panel + Provider Wizard modal added; `tools_panels.js` script tag; version bumped to v2.2.1
- `clew/web/app.js` — added a "Custom providers" card to `renderProvidersTab` that opens the Tools panel
- `clew/web/style.css` — 560 lines of new styles for the Tools panel, tables, badges, cards, modals
- `CLAUDE.md` — v2.2.1 section added; Build & Run updated; architecture note about `clew/web/` package added

## Backward compatibility

- All existing endpoints (`/api/status`, `/api/chat/stream`, `/api/agent/stream`, etc.) work unchanged.
- The legacy `clew.web_bridge.ClewBridge` shim still raises `ClewBridgeRemovedError` — no Qt code is reintroduced.
- The `.section-switcher` HTML is removed but its click handlers in `app.js` are guarded by `if(!switcher) return;`, so they no-op cleanly.
- The `.hc-pane` and `.office-pane` divs are still in `index.html` as dead code (their `if(!hcPane || !switcher) return;` guards in `app.js` skip setup entirely when the switcher is gone).

## What's NOT in this update

- No changes to the TUI (`clew_tui/`) — TUI slash commands keep working as before.
- No changes to the agent runtime (`clew/agent_runtime/`, `clew/agent/`).
- No changes to providers themselves (`clew/providers/`) — the wizard writes to the existing `~/.clew/providers.yaml` schema that `clew.providers.custom_providers` already reads.
- No new dependencies — everything uses stdlib (`http.server`, `urllib`, `json`, `secrets`, `threading`).

## Install

```bash
# Unzip into your Clew source tree
cd /path/to/clew_v2.0.1
unzip update_13.zip   # overwrites existing files

# Reinstall the package (so clew.api_extended is importable)
pip install -e .

# Verify
pytest clew/tests/test_v221_web_ui_expansion.py -v

# Run the web UI
clew   # opens http://127.0.0.1:18732/
```
