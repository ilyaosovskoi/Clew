# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

- **Install Python deps**: `pip install -e .`
- **Install Rust native** (optional): `cd clew-native && maturin develop --release -m pyo3/Cargo.toml`
- **Run TUI**: `clew_tui` (or `python -m clew_tui`)
- **Run GUI**: `clew` (or `clew-gui`)
- **Run CLI**: `clew-cli`
- **Run ACP server**: `clew-acp`
- **Run tests**: `pytest clew/` (or `pytest clew/agent/test_v2.py` for v2-specific)
- **Lint**: `black clew/ clew_tui/` + `mypy clew/ clew_tui/`

## Architecture Overview

### Two packages, one boundary rule

- **`clew/`** — core: agent runtime, providers, web bridge, Qt GUI
- **`clew_tui/`** — TUI frontend. **Never imports clew internals directly.** Communicates exclusively through `clew_tui.bridge.ClewBridge`, which owns a plain `AgentRuntime` (same path as `clew/cli.py`). If a widget needs something from the core, add a method to `ClewBridge`.

### Agent runtimes (two coexist)

- **`AgentRuntime`** (`clew/agent_runtime.py`, 5750 lines) — legacy, preserved unchanged. Used by TUI bridge, CLI, and GUI web bridge.
- **`AgentRuntimeV2`** (`clew/agent/runtime.py`) — wraps legacy, adds: asyncio `ChatStateActor`, `CircuitBreaker`, three-tier `CompactionEngine`, `InterjectionBuffer` (mid-turn user input), sandbox, `SubagentV2` with toolset-level read-only guarantee.

### Agent loop data flow

```
User input → AgentRuntime.run() → ReAct loop:
  1. ToolEngine.execute(tool_call)
     → Guardian risk assessment (if enabled)
     → LLM review (if risk above threshold)
     → Confirmation callback (if autonomy="always_ask")
     → Tool dispatch
  2. Stream result via on_event sink (PENDING → MODIFY/APPROVE/REJECT)
```

### Guardian system (`clew/agent/guardian.py`)

LLM-based safety reviewer for risky tool calls. Activated by `/guardian <level>`:
- `off` — disabled (default)
- `dangerous_only` — only high-risk calls
- `all` — medium+ risk calls

Risk is rule-based (file paths, shell commands). MODIFY verdict shows 3-button modal (Approve / Reject / Use Fix) with proposed alternative args.

### Provider system (`clew/providers/`)

16 providers indexed by `ProviderRegistry` (`registry.py`). Each implements `generate(messages)` → `ProviderResponse`. `AutoRouter` selects the best provider per task.

### Web bridge vs TUI bridge

- **Web bridge** (`clew/web_bridge.py`): QObject exposed via QWebChannel. Owns `AgentRuntime`. Emits Qt signals → JS callbacks.
- **TUI bridge** (`clew_tui/bridge.py`): plain Python. Owns `AgentRuntime`. Routes events via `EventSink` callback.

### Key architectural constraints

- **No telemetry** — no analytics, crash reporting, usage stats.
- **No `shell=True`** — all subprocesses use `shlex.split()` + `shell=False`.
- **Local-first** — Ollama/LM Studio are default; cloud APIs are opt-in.
- **Rust native** is optional — pure-Python fallbacks in `clew/agent/_fallback_*.py` when `clew_native` not installed.
- **Activity log** (`clew/activity_log.py`) — first-class audit trail for every tool call.

## docs/

Key reference documents in `docs/`:

| File | Content |
|------|---------|
| `ARCHITECTURE.md` | Full architecture diagram and v2.0 design rationale |
| `CHANGELOG.md` | Release history |
| `CONTRIBUTING.md` | Contributor guidelines |
| `REFACTORING.md` | v2.0 refactoring decisions and rationale |
| `guardian-implementation-status.md` | Guardian feature development tracker |

Remove `FIXES.md` — all bugs described there were already fixed, and the file was outdated.

## Smoke Test Findings (2025-07-25) — COMPLETED

### Что сделано
1. Изучил архитектуру TUI — `ClewBridge` как граница с core, набор виджетов, Command Palette
2. Запустил TUI, обнаружил и исправил CSS-несовместимость с Textual 8.x
3. TUI успешно стартует (status bar, chat area, input box — все отрисовываются)

### Что исправлено
- `styles_dark.tcss` — переписан полностью: удалены `--variable`, `@keyframes`, `transition:`, объединены дублирующиеся `Screen {}` блоки
- `styles_light.tcss` — аналогично
- `command_palette.py` — `$panel` -> `#161b22`

### Лучшее решение (повторять в будущем)
**Полный перезапис файла вместо починки**: когда CSS файл содержит множество несовместимых конструкций (`var(--*)`, `transition:`, `@keyframes`), эффективнее записать файл с нуля, чем править по одной строке. Построчные `Edit` приводят к битым строкам (комментарий + остаток свойства). `Write` решил проблему за один шаг.

**Автоматизированные тесты запуска**: Команда для проверки TUI без ручного запуска (run TUI, wait 3s, SIGINT, check stderr) сэкономила время. Без неё пришлось бы запускать UI вручную после каждого изменения.

**CSS в Textual 8.x**: Использовать ТОЛЬКО буквальные значения цветов (#rrggbb) или Textual built-in переменные ($accent, $text, $text-muted). Не `var(--*)`, не `@keyframes`, не `transition: Xs ease`.

### Что предстоит протестировать
- Тема переключения (Ctrl+T)
- Базовый чат-взаимодействие
- Slash-команды (/help, /section, /model, /files)
- Command palette (Ctr+P)
- Inline подсказки (/...)
- Модалки (Approve/Guardian)
- GUI launch (Ctrl+G)

## Issue #8 Research (2025-07-25) — COMPLETED

### Progressive Tool Disclosure architecture

`clew/progressive_tools.py` (221 lines) implements the `select_tools` pattern ported from Kimi Code:

- **`TOOL_CATALOG`** — Dict of ~44 tool_name → one-line description. Sections: file ops, code execution, search, git, agent, knowledge, office, MCP.
- **`build_catalog_prompt()`** — formats catalog for system prompt ("Available tools: call select_tools to load...")
- **`build_select_tools_schema()`** — JSON schema for select_tools meta-tool (takes `tool_names` array)
- **`fold_announced_tool_names(history_text)`** — scans conversation for `<tools_added>`/`<tools_removed>` blocks, re-derives current loaded set. Self-healing on compaction/resume.
- **`render_loadable_tools_announcement(added, removed)`** — renders XML announcement blocks for history
- **`get_loadable_tools(loaded, section)`** — returns tools not yet loaded, excluding section-gated and always-loaded tools

Always-loaded tools (never in catalog, never need loading): `select_tools`, `call_mcp_tool`, `list_mcp_tools`, `search_tools`.

### Tool dispatch — two paths

**Path A — AgentRuntime + agent_orchestrator.py:**
- `patch_runtime()` calls `_patch_with_progressive_tools()` which monkey-patches `runtime.tools._dispatch`
- `_dispatch_with_select_tools(call)` intercepts `"select_tools"` and `"search_tools"` calls, routes to `_handle_select_tools()` and `_handle_search_tools()` respectively
- `_handle_select_tools()` validates names against TOOL_CATALOG, computes added/removed, records announcement in memory, builds tool definitions, returns results
- `_handle_search_tools()` validates query string, searches TOOL_CATALOG by keyword/substring in name and description, returns matches
- `_build_tool_definitions()` calls `PromptBuilder.build_tool_list()` to get full parameter schemas

**Path B — AgentRuntimeV2 (cleaner, no monkey-patching):**
- `clew/agent/runtime.py` wraps legacy AgentRuntime. Does NOT use the orchestrator patches by design (CLAUDE.md says "When clew.agent.AgentRuntimeV2 is used, this orchestrator is NOT needed").

### What Issue #8 implemented

Added `search_tools` meta-tool that allows LLM to search TOOL_CATALOG by keyword/substring. Previously the only way to discover tools was reading the full catalog prompt or calling `select_tools` with exact names. Now agents can discover tools via fuzzy/partial matching.

**Files modified:**
1. `clew/progressive_tools.py` — added `build_search_tools_schema()` and `search_tools()` handler; updated `get_loadable_tools()` to include "search_tools" in always-loaded set
2. `clew/agent_orchestrator.py` — wired `search_tools` into `_dispatch_with_select_tools` and added `_handle_search_tools()`
3. `clew/agent_runtime.py` — added `SEARCH_TOOLS` to `ToolName` enum and added lambda to `dispatch_map` calling `self._search_tools_handler(args)`; added `_search_tools_handler` method delegating to `clew.progressive_tools.search_tools`

**Design decisions:**
- `search_tools` is read-only (no side effects, doesn't load tools)
- Takes a `query` parameter (string) for keyword/substring matching against tool name and description
- Returns matching entries with their descriptions
- Added to "always loaded" set so it's available without being explicitly loaded
- `search_tools` does NOT auto-load — agent still calls `select_tools` after search to load full tool definitions

**Testing:**
- Verified `search_tools("git")` returns git_commit, git_diff, git_stage, git_status
- Verified `search_tools("file")` returns 16 file-related tools
- Verified `search_tools("office")` returns 16 office tools
- Verified `search_tools("unknown")` returns appropriate not found message
- Verified tool appears in progressive tools catalog and is available without explicit loading

## Upcoming Goals

Priority-ordered objectives for the `feature/v2.0.0-version` branch:

### 1. Smoke test the TUI
Run `clew_tui`, test all features end-to-end, document all crashes, freezes, or misbehavior in GitHub Issues.

### 2. Fix TUI issues + close open Issues
Work through GitHub Issues #3–#9 to bring Clew's agent quality to parity with Codex/Claude Code:
- Guardian tests (#3)
- Guardian Agent sub-reviewer (#4)
- Marker-based context fragments for compaction (#5)
- SQLite persistence (#6)
- Collaboration modes (#7)
- Tool search meta-tool (#8)
- Request serialization queues (#9)

### 3. User-custom providers
Add a mechanism for users to register their own AI provider without modifying source code. Should support:
- OpenAPI-compatible endpoints via config file
- Custom provider class loading from `~/.clew/providers/` (like the plugin system)
- Auto-registration in ProviderRegistry and AutoRouter

### 4. More agent tools
Add useful tools beyond the current set (read_file, write_file, str_replace, execute_command, search_project, git_*, etc.):
- `web_fetch` — fetch and summarize URL content (like Claude Code's WebFetch)
- `web_search` — search the web (like Claude Code's WebSearch)
- `notebook_edit` — edit Jupyter notebook cells
- `image_generation` — DALL-E / Stable Diffusion integration
- `terminal_interact` — interactive terminal sessions (not just fire-and-forget commands)
- `database_query` — SQL query execution with result formatting
- Keep the no-shell=True, no-telemetry, local-first constraints

### 5. Improve GUI/TUI switching
Currently both frontends exist but switching between them requires restart. Improvements:
- Shared session state file so switching preserves conversation
- Unified config that both frontends read from
- Optional: hot-reload from TUI to GUI and back
- Common event bus for cross-frontend notifications

### 6. Smart AutoRouter (cost-aware routing)
Redesign AutoRouter to automatically manage provider/model selection based on budget, token limits, and task complexity.

**Free mode:**
- User selects "free" once — system automatically rotates across all free API keys during the dialogue to avoid exhausting any single provider's limits
- No per-provider configuration needed — just add keys
- Tracks remaining quotas and switches mid-conversation when one provider is exhausted

**Paid mode:**
- System uses all available paid APIs, starting with cheapest
- A cheap evaluator LLM (in system prompt) assesses task difficulty before each turn
- Evaluator recommends: which models to use, budget allocation per step, quality vs cost tradeoff
- Router executes the plan: delegates simple tasks to cheap/fast models, complex tasks to expensive/capable models
- All within a user-defined total budget cap

**Architecture:**
- `TaskComplexity` classifier extended with real-time provider availability
- Model tier catalog expanded with per-provider pricing, speed, context window
- Evaluator prompt in `clew/agent/templates/` — injected into system prompt of a lightweight model
- Quota-aware routing — reads from existing `clew/quota.py` and `clew/token_tracker.py`