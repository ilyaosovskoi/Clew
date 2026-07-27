# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language Preference
- **Dialogue with user**: Russian (Русский)
- **Code and comments**: English

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

## Loop Engineering Infrastructure

**Always read these files when starting any new task** — they define the loop engineering process used in this project:

| File | Purpose |
|------|---------|
| `Loops_Library.md` | Catalog of 10 reusable loop patterns (BUG, FEAT, REFACTOR, PERF, SEC, DEBT, INCIDENT, EXPERIMENT, DEPUP, DOCS) |
| `Loop_Engineering_Guide.md` | Quick start, workflows, file map, GitHub integration, anti-patterns |
| `Success_Criteria_Template.md` | Template for defining measurable success criteria at loop kickoff |
| `Learnings.md` | Institutional knowledge base with validated learnings from past loops |
| `Weekly_System_Review.md` | Weekly review template for system health, metrics, incidents, architecture |

**Directory structure** (created automatically):
- `loops/active/` — Current loop files (one per loop)
- `loops/archive/` — Closed loops (immutable)
- `learnings/` — Individual learning entries
- `reviews/` — Weekly review history

**Workflow reminder**: Before implementing, check `Loops_Library.md` to pick the right loop type, copy `Success_Criteria_Template.md` to `loops/active/`, fill criteria, get sign-off, then execute.

## Active Goals (Monetization & Growth Foundation)

**Monetization** — deferred until post-v2.0 stabilization, but foundation work starts now:

| Priority | Goal | Effort | Notes |
|----------|------|--------|-------|
| **M1** | **Cross-model "Second Opinion" before commit** ✅ | Low-Med | Guardian + 16 providers ready; 1 extra API call + UI toggle. Best effort/value ratio per `clew_monetization_strategy_v2.md` #16. Gate behind `clew_pro` flag. Implemented in `clew/second_opinion.py` + `clew_tui/bridge.py` + `clew/web_bridge/bridge.py`. |
| **M2** | **Smart cost-aware provider routing** | Medium | Build on `registry.py` + `token_tracker.py` data. Task complexity classifier → model tier catalog. |
| **M3** | **Team spend dashboard** | Low-Med | Aggregate `token_history.jsonl` by org. Useful for Enterprise gate. |
| **M4** | **Office ↔ Cloud accounts (OAuth: SharePoint/Drive/OneDrive)** | Medium | `office_worker.py` is local-only. OAuth infra + token storage = SaaS layer. |
| **M5** | **Audit trail export (hash-chain + signature)** | Medium | `activity_log.py` + `export_json()` exist. Add integrity for compliance. |

**Growth / User Acquisition Features** (from `gaps_ai_coding_agents.md` analysis):

| Priority | Feature | User Pain Addressed | Target Group |
|----------|---------|---------------------|--------------|
| **G1** | **User-custom providers (TUI + GUI)** ✅ | "Нетехнические пользователи не знают, как добавить свой ключ/провайдер; power users хотят свои endpoints" | Все группы |
| **G2** | **Add Nvidia NIM provider** ✅ | "Хочу бегать локальные модели через NIM без настройки Ollama" | Power users, Enterprise |
| **G3** | **Predictable limits / token efficiency** ✅ | "Лимиты ломают workflow, тратится 4x токенов vs Codex" | Опытные разработчики |
| **G4** | **Cross-model verification UI** (extends M1) ✅ | "Модель не может проверить себя объективно" | Опытные разработчики, Enterprise |
| **G5** | **Agent identity + tool-call audit** | "Нет видимости, какой агент что вызвал" | Enterprise/Безопасность |
| **G6** | **Post-task "bridge" (CMS/editable handoff)** | "После сдачи задачи нет интерфейса для правок без разработчика" | Vibe coders / Нетехнические |
| **G7** | **Capability catalog / templates** ✅ | "Не знаю, что можно попросить у агента" | Vibe coders / Нетехнические |
| **G8** | **Polished TUI/GUI, fast bug fixes** | "Долгоживущие баги интерфейса = неуважение" | Все группы |

**Provider System Enhancement Goals:** ✅ **COMPLETED**
- **User-custom providers**: Config file (`~/.clew/providers.yaml`) + dynamic class loading from `~/.clew/providers/` (plugin-style). Auto-register in `ProviderRegistry` and `AutoRouter`. Works in both TUI (via `ClewBridge.list_providers()`/`set_provider()`) and GUI (via web bridge `list_providers`/`set_provider` slots).
- **Nvidia NIM**: Added `NvidiaNIMProvider` to `clew/providers/` implementing OpenAI-compatible chat completions. Supports `base_url` + `api_key` config. Registered by default.

Remove `FIXES.md` — all bugs described there were already fixed, and the file was outdated.

## Smoke Test Findings (2026-07-25) — COMPLETED

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

## Issue #8 Research (2026-07-25) — COMPLETED

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
Run `clew_tui`, test all features end-to-end, document all crashes, freezes, or misbehavior in GitHub Issues. — **DONE (2026-07-25)**

### 2. Fix TUI issues + close open Issues
Work through GitHub Issues #3–#9 to bring Clew's agent quality to parity with Codex/Claude Code:
- Guardian tests (#3) — **DONE** (43 tests in `clew/agent/test_guardian.py`)
- Guardian Agent sub-reviewer (#4) — **DONE** (`review_with_subagent` in `clew/agent/guardian.py`)
- Marker-based context fragments for compaction (#5) — **DONE** (`clew/agent/context_fragments.py` + tests)
- SQLite persistence (#6) — **DONE** (`clew/session/sqlite_persistence.py` + tests; `ContextMemory.save`/`load` dispatch to it on `*.db` paths)
- Collaboration modes (#7) — **DONE** (`clew/collaboration.py` with Reviewer / Codegen / Pair / Observer modes + tests)
- Tool search meta-tool (#8) — **DONE** (see section above)
- Request serialization queues (#9) — **DONE** (`clew/request_queue.py` with `RequestQueue`, `QueueRegistry`, `wrap_provider` + tests)

### 3. User-custom providers ✅ — COMPLETED (2026-07-26)
Add a mechanism for users to register their own AI provider without modifying source code. Should support:
- OpenAPI-compatible endpoints via config file
- Custom provider class loading from `~/.clew/providers/` (like the plugin system)
- Auto-registration in ProviderRegistry and AutoRouter

**Implemented:**
- `clew/providers/custom_providers.py` — Complete implementation with YAML config, dynamic class creation, file/class loading, auto-discovery
- `clew/providers/registry.py` — Updated `get_registry()` to call `register_custom_providers()` and added `reload_registry()` for hot-reload
- `clew/providers/__init__.py` — Exported custom providers functions
- Example config at `~/.clew/providers.yaml.example`

### 4. Nvidia NIM provider ✅ — COMPLETED (2025-07-26)
Add Nvidia NIM provider as a built-in option.

**Implemented:**
- `clew/providers/nvidia_nim.py` — `NvidiaNIMProvider` extending `OpenAICompatProvider`
- API base: `https://integrate.api.nvidia.com/v1`
- Default model: `meta/llama-3.1-8b-instruct`
- Auth via `NVIDIA_API_KEY` environment variable
- Registered by default in `registry.py` and exported in `__init__.py`

### 5. More agent tools
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

## G1 + G2: User-Custom Providers + Nvidia NIM (2026-07-26) — COMPLETED

**G1 — User-custom providers mechanism:**
- **`clew/providers/custom_providers.py`** — Complete implementation:
  - YAML config loading from `~/.clew/providers.yaml` with three definition modes:
    1. **OpenAI-compatible (dynamic, no code)** — `type: "openai_compatible"` with `api_base`, `env_var`, `capabilities`, `context_window`, etc.
    2. **Python class import** — `class_path: "my_package.providers.MyProvider"`
    3. **Python file load** — `file_path: "~/.clew/providers/my_provider.py"`
  - Dynamic provider class creation for OpenAI-compatible endpoints (no Python code needed)
  - Auto-discovery from `~/.clew/providers/` directory (plugin-style)
  - `register_custom_providers(registry)` — called automatically on registry initialization
- **`clew/providers/registry.py`** — Updated `get_registry()` to call `register_custom_providers()` and added `reload_registry()` for hot-reload without restart
- **`clew/providers/__init__.py`** — Exported custom providers functions
- **Example config** at `~/.clew/providers.yaml.example` with documentation

**G2 — Nvidia NIM provider:**
- **`clew/providers/nvidia_nim.py`** — `NvidiaNIMProvider` extending `OpenAICompatProvider`
  - API base: `https://integrate.api.nvidia.com/v1`
  - Default model: `meta/llama-3.1-8b-instruct`
  - Auth via `NVIDIA_API_KEY` environment variable
  - Capabilities: chat, streaming, tool_calling, system_prompt, skills
- **`clew/providers/registry.py`** — Added import and registration in `register_default()`
- **`clew/providers/__init__.py`** — Exported `NvidiaNIMProvider`

**Frontend Integration (both TUI and GUI):**
- **TUI** (`clew_tui/app.py`): `_open_model_palette()` calls `bridge.list_providers()` — automatically includes custom providers
- **TUI** (`clew_tui/bridge.py`): `list_providers()` and `set_provider()` work with all registered providers including custom ones
- **GUI** (`clew/web_bridge/bridge.py`): `list_providers()` and `set_provider()` Slot methods automatically include custom providers from registry

**Verification:**
- Both providers appear in `registry.list_providers()` ✓
- TUI bridge `list_providers()` returns both ✓
- GUI bridge `list_providers()` returns both ✓
- TUI bridge `set_provider('nvidia_nim')` works ✓
- TUI bridge `set_provider('my_local_llm')` works ✓
- GUI bridge `set_provider('nvidia_nim')` works ✓
- GUI bridge `set_provider('my_local_llm')` works ✓
- Registry `reload_registry()` hot-reload works ✓
- GUI bridge `list_providers()` returns both ✓
- Provider switching works in both bridges ✓
- Registry hot-reload via `reload_registry()` works ✓
- All 144 tests pass ✓
- TUI launches successfully ✓

## Issue #3–#9 Implementation Notes (2026-07-25)

### Issue #3 — Guardian tests
**Files**: `clew/agent/guardian.py`, `clew/agent/test_guardian.py`

Fixed five failing tests in the original `test_guardian.py` and
extended coverage from 13 to 43 tests. The original `assess_risk`
had a duplicated `execute_command` block (lines 91–111 and 113–135
in the legacy file) that suppressed reason strings on the medium-risk
path, and `CRITICAL_PATHS` did not include system directories like
`/etc`, so `write_file("/etc/passwd")` was scored as medium instead
of high. Both bugs are fixed.

### Issue #4 — Guardian Agent sub-reviewer
**Files**: `clew/agent/guardian.py`

Added `GuardianConfig.use_subagent` flag and `review_with_subagent`
function. When the flag is set, `review_with_llm` delegates the LLM
call to a read-only `explore` subagent (read-only is enforced at
toolset construction time — the `explore` subagent has no
`write_file` / `execute_command` / `str_replace` tools advertised to
the LLM). The subagent's response is parsed with the same
`_parse_verdict` helper, so verdict semantics stay identical to the
direct-provider path.

### Issue #5 — Marker-based context fragments
**Files**: `clew/agent/context_fragments.py`, `clew/agent/test_context_fragments.py`

Tools can emit `<context_fragment type="..." id="...">...</context_fragment>`
blocks inside the conversation history. The compactor preserves the
latest occurrence per (type, id) and replaces older occurrences with
a tombstone: header + one-line digest + closing tag. Non-fragment
text is left untouched. Configurable via `FragmentCompactionConfig`
(keep per-id, keep per-type, collapse-type filter, digest length).

### Issue #6 — SQLite persistence
**Files**: `clew/session/sqlite_persistence.py`, `clew/session/test_sqlite_persistence.py`, `clew/agent_runtime.py`

`SQLitePersistence` adapter stores each message as a row, supports
multi-session databases, range queries (`load_range(offset, limit)`),
and O(log N) appends. `ContextMemory.save()` and `ContextMemory.load()`
auto-dispatch to the SQLite adapter when `persist_path` ends in
`.db` / `.sqlite` / `.sqlite3`; the JSON path is unchanged for
backwards compatibility.

### Issue #7 — Collaboration modes
**Files**: `clew/collaboration.py`, `clew/test_collaboration.py`

Four collaboration modes that compose on top of `SwarmManager`:

- **Reviewer** — implementer + reviewer loop with APPROVE / REJECT /
  MODIFY verdicts (up to `max_iterations` rounds).
- **Codegen** — planner decomposes task → N parallel implementers →
  results concatenated.
- **Pair** — two agents alternate turns on the same task
  (`rounds` total).
- **Observer** — one worker + N read-only observers; warnings collected
  into `metadata['warnings']` without blocking the worker.

The orchestrator is runtime-agnostic (takes a `run_agent_fn`
callable), so unit tests inject scripted responses without spinning
up a real provider.

### Issue #9 — Request serialization queues
**Files**: `clew/request_queue.py`, `clew/test_request_queue.py`

Per-provider `RequestQueue` with:

- `max_concurrency` semaphore (default 1 = strict serialization).
- `max_queue_size` cap (raises `QueueFullError` instead of blocking
  forever).
- Automatic cooldown after a 429 (default 5s).
- Exponential backoff retries (default 3, capped at 8s).
- Sync and async submit paths.
- `wrap_provider()` monkey-patches a `Provider` instance so
  `generate` / `stream` go through the queue. `unwrap_provider()`
  restores the originals.

`QueueRegistry` is a singleton keyed by `provider_id`, so each
provider gets its own queue.

## Refactoring Fix (2026-07-26)

Fixed the `clew/agent_runtime/` and `clew/web_bridge/` package split that was introduced in the v2.0.0 refactoring but had import bugs:

**Problem:** The refactored `diff_utils.py` module-level functions had `@staticmethod` decorators and `self` parameters incorrectly (they were module-level, not class methods). The `ToolEngine` in `tool_engine/_engine.py` still called `self._backup_file(p)` which no longer existed.

**Fixes applied:**
1. `clew/agent_runtime/diff_utils.py` — rewritten as proper module-level functions (removed `@staticmethod`, fixed `_backup_file` signature from `(self, p)` to `(backup_dir, max_backups, p)`)
2. `clew/agent_runtime/tool_engine/_engine.py` — updated all 7 call sites from `self._backup_file(p)` to `_backup_file_func(self._backup_dir, self._MAX_BACKUPS, p)`
3. `clew/agent_runtime/parser.py` — added missing `Callable` import and `AgentEvent` from `.types`
4. `clew/agent_runtime/runtime.py` — added missing imports: `ProviderRegistry`, `get_project_context`, `get_context_manager`, `EventCallback` type alias, `Tuple`

All 144 tests now pass and all imports work correctly.

## v2.0.1 (2026-07-27) — G7, M1, G3, G4 ✅ COMPLETED

Four new features shipped in this update, all wired into both TUI and
GUI frontends with `/slash-command` access in the TUI. Each lives in
its own module so it can be enabled / disabled / extended without
touching the others.

### G7 — Capability catalog / templates

**Goal:** "I don't know what I can ask the agent to do." Non-technical
users land in an empty chat and don't know how to phrase a request.

**Solution:** a curated catalog of pre-built capability templates
(`write-new-feature`, `fix-bug`, `refactor-extract-function`,
`write-unit-tests`, `dockerize-project`, `onboard-to-codebase`, etc.)
that users browse via `/capabilities` and run with placeholder
substitution: `/capabilities write-new-feature language=python
feature="double a number" file_path=doubler.py`.

**Files:**
- `clew/capability_catalog.py` — new module (540+ lines). Built-in
  catalog of 20 templates across 9 categories (code, refactor, test,
  debug, document, review, deploy, office, learn). Supports the same
  front-matter-driven file format as `skill_loader.py` so users can
  add their own at `~/.clew/capabilities/*.md` or
  `<project>/.clew/capabilities/*.md`. Project-level overrides
  user-global overrides built-in (keyed by id).
- `clew_tui/bridge.py` — added `list_capabilities()`,
  `get_capability()`, `fill_capability_template()`.
- `clew_tui/app.py` — added `/capabilities` slash-command with
  browse-palette + inline `k=v` placeholder fill + detail view for
  missing required placeholders.
- `clew/web_bridge/bridge.py` — added `@Slot` versions of the same
  methods (`list_capabilities`, `get_capability`,
  `fill_capability_template`) for the GUI frontend.

**Design decisions:**
- Placeholders use `$name$` (dollar-delimited) instead of `$name` to
  avoid colliding with shell-variable syntax inside bash code blocks.
- Auto-discovery: if a template body uses `$foo$` but `foo` isn't
  declared in the front-matter, it's auto-added as a required
  placeholder — so a minimal template (just front-matter + body) works.
- Built-ins ship with Clew but can be overridden by user-global and
  project-level files of the same id — same priority scheme as
  `skill_loader.py`.

### M1 — Cross-model "Second Opinion" (Pro-gated)

**Goal:** Before the agent commits to a risky action, ask a DIFFERENT
model for an independent verdict. The same model reviewing itself is
useless — it just confirms its own reasoning.

**Solution:** `clew/second_opinion.py` calls a different provider family
than the active one (Anthropic if OpenAI is active, Groq if Ollama is
active, etc.) and returns a JSON verdict: APPROVE / REJECT / MODIFY
with rationale. Gated behind `clew_pro` flag (env var `CLEW_PRO=1` or
`clew_pro: true` in `~/.clew/config.json`).

**Files:**
- `clew/second_opinion.py` — new module (320+ lines). Cross-family
  default table picks a sensible second model automatically. Config
  persistence via `~/.clew/config.json` under `second_opinion` key.
  Always fails OPEN: any error in the second-opinion path returns
  `APPROVE` so the feature never blocks the user because of a bug.
- `clew_tui/bridge.py` — added `is_pro_enabled()`, `set_pro_enabled()`,
  `get_second_opinion_config()`, `set_second_opinion_config()`,
  `run_second_opinion()`, `list_second_opinion_providers()`.
- `clew_tui/app.py` — added `/second_opinion` slash-command with
  subcommands: `on|off`, `pro on|off`, `provider <pid> [model]`,
  `risk low|medium|high`.
- `clew/web_bridge/bridge.py` — added `@Slot` versions of all the
  above for the GUI frontend.

**Design decisions:**
- Separate from `guardian.py` because: (1) Guardian is rule-based risk
  scoring + optional SAME-provider LLM review; Second Opinion is
  ALWAYS cross-model. (2) Second Opinion is Pro-gated; Guardian isn't.
  (3) The two features compose: Guardian flags risk, Second Opinion
  chimes in with a different model's take.
- Verdict schema mirrors Guardian's (APPROVE/REJECT/MODIFY + rationale
  + suggested_args) so the existing `_confirm_callback` path can route
  Second Opinion verdicts through the same Guardian modal UI.

### G3 — Predictable limits / token efficiency

**Goal:** "Limits break my workflow, and I'm burning 4x tokens vs
Codex." Token usage was unpredictable because of adaptive behaviours
(adaptive compaction, dynamic tool catalog, no hard caps).

**Solution:** `clew/token_budget.py` exposes four orthogonal knobs:
hard cost caps (daily_usd, monthly_usd), per-turn efficiency knobs
(max_tokens_per_turn, max_iterations, compaction_threshold_pct), a
predictable-mode flag (disables adaptive behaviours for
deterministic token usage), and a prompt-caching flag (marks stable
prompt parts as cacheable for providers that support it).

**Files:**
- `clew/token_budget.py` — new module (270+ lines). Config under
  `~/.clew/config.json` key `token_budget`. `check_budget()` is called
  by `AgentRuntime._generate_with_retry()` BEFORE every LLM call — if
  the cap is blown, the runtime short-circuits with a friendly error
  instead of letting the provider fail with a confusing 429.
- `clew/agent_runtime/runtime.py` — wired `check_budget()` call into
  `_generate_with_retry()` so the cap is enforced on every LLM call,
  not just agent-loop iterations.
- `clew_tui/bridge.py` — added `get_token_budget()`,
  `set_token_budget()`, `reset_token_budget()`, `check_budget()`.
  Force agent rebuild after `set_token_budget()` so `max_iterations`
  / `max_tokens_per_turn` take effect on the next turn.
- `clew_tui/app.py` — added `/budget` slash-command with subcommands:
  `daily|monthly <usd>`, `per_turn <tokens>`, `iterations <n>`,
  `compaction <50-95>`, `caching on|off`, `predictable on|off`,
  `reset`. The bare `/budget` command shows live usage against the
  caps (today / month / percentage).
- `clew/web_bridge/bridge.py` — added `@Slot` versions:
  `get_token_budget`, `set_token_budget` (JSON-arg), `reset_token_budget`,
  `check_budget`.

**Design decisions:**
- `predictable_mode` adds a 5% hysteresis band in normal mode (so the
  runtime doesn't re-compact at 86% right after compacting at 85%),
  but is a hard cutoff in predictable mode — every turn costs the
  same tokens.
- `check_budget()` reads the raw entries list with the tracker's lock
  held, so the cost calculation is consistent even if `record()` is
  happening concurrently.
- Per-turn output cap (`max_tokens_per_turn`) is passed to the provider
  via `get_max_tokens_for_provider()` so the model stops generating
  runaway text instead of always using the provider's 4096 default.

### G4 — Cross-model verification UI (extends M1)

**Goal:** "A model can't verify itself objectively." M1 covers
per-action second opinion BEFORE commit. G4 covers full-response
verification AFTER the agent answers — useful for catching
hallucinations, missing edge cases, or security issues the primary
model glossed over.

**Solution:** `bridge.verify_last_response()` picks a verifier from a
different model family (reusing M1's `resolve_second_provider`), sends
it the user's last prompt + the agent's last answer, and asks for a
structured JSON verdict: `overall`, `correctness`, `safety`,
`completeness`, `issues[]`, `suggestions[]`, `summary`. The TUI shows
the result in a colour-coded modal; the GUI exposes the same call as a
`@Slot` for a JS-side verification panel.

**Files:**
- `clew_tui/widgets/verification_modal.py` — new TUI widget (110
  lines). ModalScreen with PASS/WARN/FAIL colour coding (green /
  yellow / red), a 2x2 grid of sub-verdicts, issues + suggestions as
  bulleted lists, and a dismiss-on-Esc/Enter binding. Falls back to
  showing the raw verifier output if JSON parsing fails.
- `clew_tui/app.py` — added `/verify` slash-command (auto-picks a
  cross-family verifier, or accepts `/verify <provider_id> [model]`
  for manual selection). Worker thread runs
  `bridge.verify_last_response()` and pops the modal on completion.
- `clew_tui/bridge.py` — added `verify_last_response()` that captures
  the last assistant message + last user message from `agent.memory`
  and dispatches to the verifier provider.
- `clew/web_bridge/bridge.py` — added `@Slot(str, str)
  verify_last_response(verifier_provider_id, verifier_model)` for the
  GUI frontend. Empty strings mean "auto-pick a cross-family verifier".
- `clew_tui/styles_dark.tcss` + `styles_light.tcss` — added
  `#verify-box`, `#verify-title`, `#verify-grid`, `.verify-cell`,
  `#verify-meta`, `#verify-summary`, `#verify-issues`,
  `#verify-suggestions`, `#verify-raw`, `#verify-buttons` styles for
  both themes.

**Design decisions:**
- Verifier sees the user's request AND the agent's answer (not just
  the answer) so it can judge whether the answer actually addresses
  what the user asked.
- Verdict is informational — the modal dismisses without modifying the
  conversation. The user can act on the verifier's suggestions by
  typing a follow-up prompt.
- The verifier prompt asks for JSON with a fixed schema (overall,
  correctness, safety, completeness, issues, suggestions, summary) so
  the modal can render it consistently. If the verifier returns
  non-JSON, the modal shows the raw text in a `#verify-raw` section
  instead of crashing.
- No retry on the verifier call — verification is a nice-to-have; one
  transient failure should not block the user's workflow.

### Verification

- `python -c "from clew.capability_catalog import get_catalog;
  print(len(get_catalog().list_capabilities()))"` → 20 ✓
- `python -c "from clew.second_opinion import resolve_second_provider,
  SecondOpinionConfig; print(resolve_second_provider('ollama',
  SecondOpinionConfig()))"` → `('groq', 'llama-3.3-70b-versatile')` ✓
- `python -c "from clew.token_budget import check_budget;
  print(check_budget().exceeded)"` → `False` ✓
- All new files AST-parse cleanly.
- `clew_tui.bridge`, `clew_tui.app`, `clew_tui.widgets.verification_modal`
  import successfully under Textual 8.x.
- `clew.web_bridge.bridge` AST-parses (PySide6 not required for syntax).
- No existing tests broken — all 144 tests still pass on the legacy
  test suite (verified locally before packaging).