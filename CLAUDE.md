# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language Preference
- **Dialogue with user**: English
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

## Key Documentation in `docs/`
| File | Content |
|------|---------|
| `ARCHITECTURE.md` | Full architecture diagram and v2.0 design rationale |
| `CHANGELOG.md` | Release history |
| `CONTRIBUTING.md` | Contributor guidelines |
| `REFACTORING.md` | v2.0 refactoring decisions and rationale |
| `guardian-implementation-status.md` | Guardian feature development tracker |

## Loop Engineering Infrastructure
**Always read these files when starting any new task** — they define the loop engineering process:

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

### Monetization (deferred until post-v2.0 stabilization)
| Priority | Goal | Effort | Notes |
|----------|------|--------|-------|
| **M1** | **Cross-model "Second Opinion" before commit** ✅ | Low-Med | Guardian + 16 providers ready; 1 extra API call + UI toggle. Gate behind `clew_pro` flag. Implemented in `clew/second_opinion.py` + `clew_tui/bridge.py` + `clew/web_bridge/bridge.py`. |
| **M2** | **Smart cost-aware provider routing** ✅ | Medium | Build on `registry.py` + `token_tracker.py` data. Task complexity classifier → model tier catalog. Implemented in `clew/cost_router.py` (re-ranks AutoRouter output using budget pressure, per-complexity USD caps, provider health). TUI `/cost` slash command + GUI slots. |
| **M3** | **Team spend dashboard** ✅ | Low-Med | Aggregate `token_history.jsonl` by org. Useful for Enterprise gate. Implemented in `clew/spend_dashboard.py` (UserIdentity + TeamBudget + multi-source aggregation). TUI `/spend` slash command + GUI slots. |
| **M4** | **Office ↔ Cloud accounts (OAuth: SharePoint/Drive/OneDrive)** | Medium | `office_worker.py` is local-only. OAuth infra + token storage = SaaS layer. |
| **M5** | **Audit trail export (hash-chain + signature)** | Medium | `activity_log.py` + `export_json()` exist. Add integrity for compliance. |

### Growth / User Acquisition Features
| Priority | Feature | User Pain Addressed | Target Group |
|----------|---------|---------------------|--------------|
| **G1** | **User-custom providers (TUI + GUI)** ✅ | "Non-technical users don't know how to add their own key/provider; power users want their own endpoints" | All groups |
| **G2** | **Add Nvidia NIM provider** ✅ | "Want to run local models via NIM without Ollama setup" | Power users, Enterprise |
| **G3** | **Predictable limits / token efficiency** ✅ | "Limits break workflow, spending 4x tokens vs Codex" | Experienced developers |
| **G4** | **Cross-model verification UI** (extends M1) ✅ | "A model cannot verify itself objectively" | Experienced developers, Enterprise |
| **G5** | **Agent identity + tool-call audit** ✅ | "No visibility into which agent called what tool" | Enterprise/Security |
| **G6** | **Post-task "bridge" (CMS/editable handoff)** ✅ | "After task delivery, no interface for edits without a developer" | Vibe coders / Non-technical |
| **G7** | **Capability catalog / templates** ✅ | "Don't know what I can ask the agent to do" | Vibe coders / Non-technical |
| **G8** | **Polished TUI/GUI, fast bug fixes** | "Long-lived UI bugs = disrespect" | All groups |

### Provider System Enhancement Goals: ✅ **COMPLETED**
- **User-custom providers**: Config file (`~/.clew/providers.yaml`) + dynamic class loading from `~/.clew/providers/` (plugin-style). Auto-register in `ProviderRegistry` and `AutoRouter`. Works in both TUI (via `ClewBridge.list_providers()`/`set_provider()`) and GUI (via web bridge `list_providers`/`set_provider` slots).
- **Nvidia NIM**: Added `NvidiaNIMProvider` to `clew/providers/` implementing OpenAI-compatible chat completions. Supports `base_url` + `api_key` config. Registered by default.

## v2.0.1 (2026-07-27) — G7, M1, G3, G4 ✅ COMPLETED

### G7 — Capability catalog / templates
**Files:** `clew/capability_catalog.py` (540+ lines, 20 templates, 9 categories), `clew_tui/bridge.py`, `clew_tui/app.py` (`/capabilities` slash command), `clew/web_bridge/bridge.py` (GUI slots). Users browse via `/capabilities` and run with placeholder substitution: `/capabilities write-new-feature language=python feature="double a number" file_path=doubler.py`. Supports user-global (`~/.clew/capabilities/`) and project-level (`.clew/capabilities/`) overrides.

### M1 — Cross-model "Second Opinion" (Pro-gated)
**Files:** `clew/second_opinion.py` (320+ lines), `clew_tui/bridge.py`, `clew_tui/app.py` (`/second_opinion` slash command), `clew/web_bridge/bridge.py` (GUI slots). Cross-family default table picks a sensible second model automatically. Config persistence via `~/.clew/config.json` under `second_opinion` key. Always fails OPEN: any error returns `APPROVE`.

### G3 — Predictable limits / token efficiency
**Files:** `clew/token_budget.py` (270+ lines), `clew/agent_runtime/runtime.py` (wired into `_generate_with_retry()`), `clew_tui/bridge.py`, `clew_tui/app.py` (`/budget` slash command), `clew/web_bridge/bridge.py`. Four orthogonal knobs: hard cost caps, per-turn efficiency knobs, predictable-mode flag, prompt-caching flag. `check_budget()` called BEFORE every LLM call.

### G4 — Cross-model verification UI (extends M1)
**Files:** `clew_tui/widgets/verification_modal.py` (110 lines), `clew_tui/app.py` (`/verify` slash command), `clew_tui/bridge.py`, `clew/web_bridge/bridge.py` (`@Slot verify_last_response`), CSS styles for both themes. Verifier sees user's request + agent's answer, returns structured JSON verdict (overall, correctness, safety, completeness, issues, suggestions, summary). Informational modal — dismisses without modifying conversation.

## v2.0.2 (2026-07-29) — G5, G6, M2, M3 ✅ COMPLETED

### G5 — Agent identity + tool-call audit
**Files:** `clew/agent_identity.py` (440+ lines), `clew_tui/bridge.py` (8 methods), `clew_tui/app.py` (`/agents` + `/audit` slash commands), `clew/web_bridge/bridge.py` (7 GUI slots). Identity-aware wrapper over the existing `ActivityLog` singleton. Every audit entry now carries an `agent` field with `{id, role, name, parent_chain}`. Subagents tracked via parent_chain (root → planner → implementer_3). SHA-256 fingerprint per export entry for tamper detection. CSV/JSON export. Local-only — no telemetry.

### G6 — Post-task bridge (CMS / editable handoff)
**Files:** `clew/handoff_bridge.py` (520+ lines), `clew_tui/bridge.py` (10 methods), `clew_tui/app.py` (`/handoff` slash command with 10 subcommands: create|show|accept|reject|edit|todo|revisions|markdown|delete), `clew/web_bridge/bridge.py` (8 GUI slots). Parses agent output into structured HandoffDocument with typed blocks (text/code/file_diff/todo/note), each with a stable id and a mutable status (pending/accepted/rejected/edited). User marks blocks for revision; `build_revision_prompt()` compiles edits into a structured agent follow-up prompt. Persists to `~/.clew/handoffs/<id>.json`. Markdown export.

### M2 — Smart cost-aware provider routing
**Files:** `clew/cost_router.py` (430+ lines), `clew_tui/bridge.py` (5 methods), `clew_tui/app.py` (`/cost` slash command), `clew/web_bridge/bridge.py` (4 GUI slots). Thin layer over `AutoRouter` + `TokenTracker` + `TokenBudget` that re-ranks routing decisions using: (1) budget pressure — demotes complexity tier when monthly spend >80%/95%; (2) per-complexity USD caps filter candidates by estimated cost; (3) provider health — deprioritises providers with high error rates; (4) prefer-free-under-pressure — switches to local Ollama/LM Studio when budget pressure is critical. Every decision returns a `CostRouteDecision` with the original AutoRouter pick + final pick + factors list (for UI explainability). Persists to `~/.clew/cost_router.json`.

### M3 — Team spend dashboard
**Files:** `clew/spend_dashboard.py` (480+ lines), `clew_tui/bridge.py` (8 methods), `clew_tui/app.py` (`/spend` slash command with 7 subcommands: team|budget|sources|add|json|csv|identity), `clew/web_bridge/bridge.py` (8 GUI slots). Aggregates `token_history.jsonl` entries into a `TeamSpendReport` with totals + by_user + by_provider + by_model + by_day breakdowns. Local user identity in `~/.clew/identity.json` (user_id, name, team). Team budget in `~/.clew/team_budget.json` (monthly_usd, alert_pct). Multi-source aggregation — accepts files or directories of `*.jsonl`. CSV/JSON export. Privacy: email only included if user opts in (`share_email: true`). Local-only — no network.

## Gap Analysis — New Goals from Clew_Gap_Analysis.md (2026-07-28)

### Missing Features (Gaps vs. Competitors)
| Priority | Gap | Description | Notes |
|----------|-----|-------------|-------|
| **G9** | **Hook system at tool level** | PreToolUse/PostToolUse/UserPromptSubmit hooks for audit and enforcement policies | Currently only plugin system for routes/providers/JS-CSS — cannot intercept specific tool calls |
| **G10** | **Checkpoint / rewind** | Rollback dialogue state and file changes | No `checkpoint`/`rewind` found in codebase |
| **G11** | **GitHub-native automation** | `@codex implement issue`, GitHub Action `claude -p` on every PR | `git_service.py` only has local `status/diff/stage/commit/branch/log` — no PR/issue automation |
| **G12** | **Cloud/background execution** | Offload tasks to containers, close laptop | Agent always runs locally in real-time |
| **G13** | **MCP server capability** | Clew connects to external MCP servers but cannot BE an MCP server | Would enable Clew as a tool provider for other agents |
| **G14** | **Comprehensive test coverage** | ~7% coverage (2,960 test lines / 43,200 source lines, 9 test files / 128 modules) | Thin for security-critical code (sandbox, guardian) — **need many more tests** |

### Unique Strengths to Leverage (Clew-Only Differentiators)
| Strength | Current Status | Monetization Angle |
|----------|----------------|-------------------|
| **Multi-provider consensus** | Second Opinion (M1) does 1:1 cross-model review | Extend to 2–3 parallel providers + diff comparison for architectural decisions |
| **Cryptographic offline audit trail** | Activity log + ChaCha20 key storage exist | Signed diffs with timestamps → tamper-proof journal for regulated industries |
| **Automatic learning loop** | `Loop_Engineering_Guide.md` + `learnings/` infrastructure exists | Auto-detect rollbacks/failed CI → inject learnings into prompts per repo |

### New Feature Ideas
| ID | Feature | Description | Leverages |
|----|---------|-------------|-----------|
| **G15** | **Multi-provider consensus engine** | Run same task on 2–3 providers in parallel, show diff between approaches, explain divergence | M1 + 16 providers + AutoRouter |
| **G16** | **Signed offline audit trail** | Each agent diff/decision signed with local key + timestamp → tamper-proof journal, zero cloud | `activity_log.py` + ChaCha20 storage + zero-telemetry architecture |
| **G17** | **Automatic learning loop** | Detect rollbacks/CI failures → auto-create `learnings/` entries → inject into future prompts per repo | `Loop_Engineering_Guide.md` + `learnings/` + existing compaction/fragment infra |

### Updated Upcoming Goals (Priority-Ordered)

**7. Hook System at Tool Level (G9)**
- PreToolUse / PostToolUse / UserPromptSubmit callbacks
- Enable audit policies, auto-formatters, security scanners
- Design: event bus in `clew/agent_runtime/` + registration API in `ClewBridge`

**8. Checkpoint / Rewind (G10)**
- Snapshot conversation + file state at each turn
- `/checkpoint` and `/rewind <n>` slash commands
- Leverage existing SQLite persistence (`clew/session/sqlite_persistence.py`)

**9. GitHub-Native Automation (G11)**
- Extend `git_service.py` with PR/issue operations
- GitHub Action template for `clew-cli` on PR events
- `/github pr <num> implement` style commands

**10. MCP Server Mode (G13)**
- Expose Clew's tools via MCP protocol
- Other agents (Claude Code, Codex) can call Clew as a tool provider
- Leverages existing tool definitions + `clew/progressive_tools.py`

**11. Comprehensive Test Coverage (G14) — HIGH PRIORITY**
- Goal: Increase test coverage from ~7% to >50% for security-critical modules
- **Sandbox/Guardian**: Property-based tests, fuzzing, integration tests with real providers
- **Agent runtime**: Scenario tests covering ReAct loop edge cases
- **Providers**: Contract tests for all 16 providers + custom provider loading
- **TUI/GUI**: Snapshot tests for widgets, E2E tests for slash commands
- **Collaboration modes**: Multi-agent scenario tests
- Target: `pytest clew/` with >80% coverage on `clew/agent/`, `clew/agent_runtime/`, `clew/providers/`

**12. Multi-Provider Consensus Engine (G15)**
- Parallel execution on 2–3 providers for complex tasks
- Diff visualization between approaches
- Configurable consensus threshold

**13. Cryptographic Audit Trail (G16)**
- Sign each tool call result with local Ed25519 key
- Hash-chain across session for tamper evidence
- Export verified audit logs for compliance

**14. Automatic Learning Loop (G17)**
- Hook into git history (detect `git reset --hard`, reverted commits)
- Hook into CI results (parse failed test output)
- Auto-generate `learnings/` entries with context
- Inject relevant learnings into system prompt per project

## Website Status (2026-07-27)
- **index.html** deployed and working on GitHub Pages / main branch
- Updated with v2.0.1 features: 16 providers, TUI + GUI dual frontend, Guardian safety, Capability catalog, Second Opinion, Token budget, Cross-model verification, Custom providers

## Key Bug Fixes

### TUI message send regression (2026-07-29)
**Bug:** In `clew_tui/app.py`, used `self._running` as a flag for "agent turn in progress". This collided with Textual's internal `MessagePump._running` (means "message pump is active"). Textual sets it `True` on mount, so the send handler silently returned early on every keystroke — messages appeared to vanish while input cleared.

**Fix:** Renamed the flag to `self._turn_running` in all 13 occurrences in `app.py`. Verified with headless Pilot test — messages now reach `ChatLog` correctly.

### Refactoring Fix (2026-07-26)
Fixed the `clew/agent_runtime/` and `clew/web_bridge/` package split that had import bugs:
1. `clew/agent_runtime/diff_utils.py` — rewritten as proper module-level functions
2. `clew/agent_runtime/tool_engine/_engine.py` — updated 7 call sites to use module function
3. `clew/agent_runtime/parser.py` — added missing `Callable` import and `AgentEvent` from `.types`
4. `clew/agent_runtime/runtime.py` — added missing imports: `ProviderRegistry`, `get_project_context`, `get_context_manager`, `EventCallback` type alias, `Tuple`
All 144 tests now pass and all imports work correctly.

## Smoke Test Findings (2026-07-25) — COMPLETED
TUI starts successfully (status bar, chat area, input box all render). Fixed CSS incompatibility with Textual 8.x: fully rewrote `styles_dark.tcss` and `styles_light.tcss` (removed `--variable`, `@keyframes`, `transition:`, merged duplicate `Screen {}` blocks); fixed `command_palette.py` (`$panel` → `#161b22`). **Best practice**: Full file rewrite instead of patching when CSS contains many incompatible constructs. Textual 8.x: use ONLY literal color values (#rrggbb) or built-in variables ($accent, $text, $text-muted). No `var(--*)`, no `@keyframes`, no `transition: Xs ease`.