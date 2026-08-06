# CHANGES — update_15 (v2.2.3)

**Date:** 2026-08-05
**Scope:** UX simplification — Tools moved from sidebar into Settings; provider catalog expanded from 15 to 30+ entries.

## What changed

### 1. Sidebar Tools section removed

The 17 nav buttons added in v2.2.1 (Capabilities, Hooks, Checkpoints, Handoffs, GitHub, Audit, Spend, Consensus, Second Opinion, Verify, Learnings, CI Templates, Notifications, Daemon, MCP Server, Persona, Providers) are gone from the sidebar. The rail now contains only navigation: New chat, Catalog, Command, Chats list, Open project, Settings, Usage, Files, Profile.

### 2. New "Tools" tab in Settings

A new tab sits between **Providers** and **MCP** in the Settings modal. Clicking it shows a categorized grid of all 17 backend capabilities:

| Group                      | Tools                                                            |
|----------------------------|------------------------------------------------------------------|
| Agent runtime              | Capabilities, Hooks, Checkpoints, Handoffs, Learnings, Persona  |
| Code & collaboration       | GitHub, GitHub Actions, Consensus, Second Opinion, Verify       |
| Operations                 | Audit, Spend, Notifications, Daemon                              |
| Extensions                 | MCP Server, Providers (custom provider wizard)                   |

Clicking any card renders that tool's content directly inside the Settings modal body, with a "← Back to Tools" button to return to the grid. The same `RENDERERS` map from `tools_panels.js` is reused — no logic was duplicated.

### 3. Provider catalog expanded (15 → 30)

`PROVIDER_META` in `app.js` now lists 30 providers, organized into 8 categories with labels that render between groups in the Providers tab:

- **Local (no API key needed)**: LM Studio, Ollama, vLLM, KoboldCpp, llamafile
- **Major cloud providers**: OpenAI, Anthropic, Google Gemini, DeepSeek, Z.ai, Mistral, xAI, Cohere, Perplexity, AI21 Labs
- **Fast inference (open models hosted)**: Groq, Cerebras, SambaNova
- **Open-model hosting & aggregators**: OpenRouter, Together AI, Fireworks, Novita AI, Hyperbolic, Lepton AI, SiliconFlow, Friendli AI
- **ML platforms / model hubs**: Hugging Face, Replicate
- **Enterprise cloud**: Azure OpenAI, Vertex AI, AWS Bedrock
- **Nvidia NIM**: Nvidia NIM
- **Generic / custom**: OpenAI-compatible (custom)

Each entry includes a `keyUrl` (where to get an API key) and a `keyHint` (what the user needs to know). All cards stay collapsed by default (accordion); only the active provider auto-expands.

### 4. Provider templates expanded (4 → 31)

The backend endpoint `/api/providers/templates` (used by the Custom Provider Wizard) now returns 31 templates instead of 4. Each pre-fills the wizard with sensible defaults (base URL, model, env var name, docs link). Users can clone any of these into `~/.clew/providers.yaml` with one click. New templates include: OpenAI, Anthropic, Gemini, DeepSeek, Z.ai, Mistral, xAI, Cohere, Perplexity, AI21, Groq, Cerebras, SambaNova, OpenRouter, Together, Fireworks, Novita, Hyperbolic, Lepton, SiliconFlow, Friendli, Hugging Face, Replicate, Azure OpenAI, vLLM, KoboldCpp, llamafile.

### 5. MCP tab (already there from v2.2.1)

The MCP tab keeps its existing UI: list of configured MCP servers with start/stop/toggle/remove buttons, an "Add server" form (name + command + env vars), and a "Popular MCP servers" reference panel showing preset commands for Filesystem, GitHub, and Playwright MCP servers.

## Files in this update

### Modified (5)
- `clew/web/index.html` — removed Tools sidebar section (~70 lines), added Tools tab to Settings modal tabs
- `clew/web/app.js` — expanded PROVIDER_META (15→30), added `renderToolsTab` + helpers, added provider category grouping, rewired "Manage custom providers" button
- `clew/web/tools_panels.js` — exported RENDERERS + TOOL_META for use from Settings
- `clew/web/style.css` — added CSS for `.settings-tools-*` and `.provider-category-label`
- `clew/api_extended.py` — expanded provider templates (4→31)

### Documentation (2)
- `CLAUDE.md` — v2.2.3 section added (scope, fix, files, verification, backward compat)
- `docs/CHANGES_update_15.md` — this file

## Install

```bash
# Unzip into your Clew source tree (overwrites existing files)
cd /path/to/clew_v2.0.1
unzip update_15.zip

# Run the web UI
clew   # opens http://127.0.0.1:18732/
```

No `pip install -e .` needed — only one Python file changed (api_extended.py), and the change is additive (more entries in an already-existing list).

## Verification

Playwright UI smoke test (`scripts/test_update_15_ui_v2.py`) — all 11 assertions pass:

```
✓ No .tools-nav in sidebar
✓ Tools tab present in Settings
✓ Settings modal opens
✓ Tools grid has >=15 cards
✓ Sub-view back button rendered
✓ Grid re-renders after back
✓ Providers tab has >=25 providers
✓ Providers tab has >=6 categories
✓ MCP tab renders
✓ PROVIDER_META has >=25 entries (via provider cards)
✓ No critical JS errors (404s OK)
```

VLM visual confirmation:
- Sidebar: "No, there are no 'Tools' nav buttons visible in the sidebar."
- Settings → Tools grid: "Yes, there is a grid of tool cards visible... organized into sections like 'Agent Runtime,' 'Code & Collaboration,' and 'Operations.' I count roughly 13 tool cards in total."
- Settings → MCP tab: "Yes, this is a Settings modal showing the MCP servers configuration UI... there are example MCP server commands visible under 'POPULAR MCP SERVERS'."

## Backward compatibility

- All existing `/api/*` endpoints unchanged.
- The 128 passing tests in `clew/tests/test_v221_web_ui_expansion.py` still pass (the 2 that fail are pre-existing environment issues — `clew.providers` module not present in this sandbox — unrelated to this update).
- The Tools panel drawer (`#toolsPanel`) and its CSS are kept for any code that still references it; the new Settings → Tools tab is the primary surface, but the drawer still works if opened programmatically.
- Existing user configurations in `~/.clew/config.json`, `~/.clew/providers.yaml`, and `~/.clew/mcp.json` are not touched.
