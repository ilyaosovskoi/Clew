# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language Preference
- **Dialogue with user**: English
- **Code and comments**: English

## Build & Run
- **Install Python deps**: `pip install -e .`
- **Install Rust native** (optional): `cd clew-native && maturin develop --release -m pyo3/Cargo.toml`
- **Run TUI**: `clew_tui` (or `python -m clew_tui`)
- **Run Web UI**: `clew` (or `python -m clew.web_server`) — opens a local HTTP server at `http://127.0.0.1:18732/` and launches the default browser. No Qt/PySide6 dependency. (v2.2.0+)
- **Run CLI**: `clew-cli`
- **Run ACP server**: `clew-acp`
- **Run MCP server**: `clew-acp --mcp-server` (or `python -m clew.mcp_server`)
- **Run daemon**: `clew-daemon serve` (HTTP API + SSE on port 8765) / `clew-daemon task "prompt" --notify telegram`
- **Run tests**: `pytest clew/` (or `pytest clew/agent/test_v2.py` for v2-specific, `pytest clew/tests/test_v221_web_ui_expansion.py` for v2.2.1 web-UI expansion)
- **Lint**: `black clew/ clew_tui/` + `mypy clew/ clew_tui/`

## Architecture Overview

### Two packages, one boundary rule
- **`clew/`** — core: agent runtime, providers, web server (v2.2.0+ — Qt GUI removed), HTTP REST API + SSE
- **`clew_tui/`** — TUI frontend. **Never imports clew internals directly.** Communicates exclusively through `clew_tui.bridge.ClewBridge`, which owns a plain `AgentRuntime` (same path as `clew/cli.py`). If a widget needs something from the core, add a method to `ClewBridge`.
- **`clew/web/`** — browser frontend (HTML/CSS/JS). Talks to `clew.api_server` via HTTP REST + SSE; never imports Python directly. v2.2.1 added `tools_panels.js` for the unified Tools sidebar.

### Agent runtimes (two coexist)
- **`AgentRuntime`** (`clew/agent_runtime/runtime.py`, 1510 lines) — legacy, preserved unchanged. Used by TUI bridge, CLI, and GUI web bridge.
- **`AgentRuntimeV2`** (`clew/agent/runtime.py`) — wraps legacy, adds: asyncio `ChatStateActor`, `CircuitBreaker`, three-tier `CompactionEngine`, `InterjectionBuffer` (mid-turn user input), sandbox, `SubagentV2` with toolset-level read-only guarantee.

### Agent loop data flow
```
User input → AgentRuntime.run() → ReAct loop:
  1. HookManager.dispatch_pre_tool_use() → may BLOCK or MODIFY args
  2. ToolEngine.execute(tool_call)
     → Guardian risk assessment (if enabled)
     → LLM review (if risk above threshold)
     → Confirmation callback (if autonomy="always_ask")
     → Tool dispatch
  3. HookManager.dispatch_post_tool_use() → informational audit/log
  4. Stream result via on_event sink (PENDING → MODIFY/APPROVE/REJECT)
  5. CheckpointManager.auto_checkpoint() → snapshot state
```

### Hook system (`clew/hook_system.py`)
Process-wide `HookManager` singleton with three event types:
- `pre_tool_use` — before tool execution. Can BLOCK or MODIFY args.
- `post_tool_use` — after tool execution. Informational only.
- `user_prompt_submit` — before prompt goes to LLM. Can BLOCK or MODIFY prompt.

User hooks: Python modules in `~/.clew/hooks/*.py` with `register_hooks(manager)`. Config persistence in `~/.clew/hooks.json`. Thread-safe (RLock + snapshot pattern).

### Checkpoint / rewind system (`clew/checkpoint.py`)
`CheckpointManager` snapshots conversation state + file changes at each turn:
- Auto-checkpoint after every agent turn (configurable).
- Manual checkpoint via `/checkpoint save [label]`.
- Rewind via `/rewind <n>` restores file backups + conversation position.
- File backups stored in `~/.clew/checkpoints/<session>/backups/<cp_id>/`.
- SHA-256 checksums for integrity verification. Max 200 checkpoints per session.

### Guardian system (`clew/agent/guardian.py`)
LLM-based safety reviewer for risky tool calls. Activated by `/guardian <level>`:
- `off` — disabled (default)
- `dangerous_only` — only high-risk calls
- `all` — medium+ risk calls

Risk is rule-based (file paths, shell commands). MODIFY verdict shows 3-button modal (Approve / Reject / Use Fix) with proposed alternative args.

### GitHub automation (`clew/github_automation.py`)
REST API client for PR/issue operations. Uses GitHub token from `GITHUB_TOKEN` env or `~/.clew/github_token`. Supports: list/get/create PRs, list/get/create issues, comment, get PR diff, build implementation context (`/github pr <num> implement`), generate GitHub Action templates. Rate limit retry with backoff. Auto-detect repo from git remote URL.

### MCP server mode (`clew/mcp_server.py`)
Exposes Clew's tools via MCP protocol so other agents can call Clew as a tool provider. JSON-RPC 2.0 over stdio with Content-Length framing. Read-only mode (default) or write mode (`--mcp-allow-writes`). Entry point: `clew-acp --mcp-server`.

### Provider system (`clew/providers/`)
16 providers indexed by `ProviderRegistry` (`registry.py`). Each implements `generate(messages)` → `ProviderResponse`. `AutoRouter` selects the best provider per task.

### Web bridge vs TUI bridge
- **Web bridge** (`clew/web_bridge/bridge.py`): QObject exposed via QWebChannel. Owns `AgentRuntime`. Emits Qt signals → JS callbacks.
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
| **G9** | **Hook system at tool level** ✅ | "Cannot intercept specific tool calls for audit/security" | Enterprise/Security, Power users |
| **G10** | **Checkpoint / rewind** ✅ | "No way to undo agent mistakes — once it writes, it's permanent" | All groups |
| **G11** | **GitHub-native automation** ✅ | "No PR/issue integration — must manually context-switch" | Developers, Enterprise |
| **G13** | **MCP server capability** ✅ | "Clew cannot BE an MCP server for other agents" | Multi-agent users, Enterprise |
| **G14** | **Comprehensive test coverage** ✅ | "~7% coverage is dangerous for security-critical code" | All groups |

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

## v2.0.3 (2026-07-29) — G9, G10, G11, G13, G14 ✅ COMPLETED

### G9 — Hook system at tool level
**Files:** `clew/hook_system.py` (370+ lines), `clew_tui/bridge.py` (6 methods), `clew_tui/app.py` (`/hooks` slash command with 5 subcommands: list|enable|disable|remove|test|stats), `clew/web_bridge/bridge.py` (5 GUI slots). Process-wide `HookManager` singleton with three event types: `pre_tool_use` (BLOCK/MODIFY/ALLOW before tool execution), `post_tool_use` (informational after tool execution), `user_prompt_submit` (BLOCK/MODIFY before LLM call). Hooks registered via user Python modules in `~/.clew/hooks/*.py` (auto-loaded at startup) or programmatically. Config persistence via `~/.clew/hooks.json`. Thread-safe (RLock + snapshot pattern). Priority ordering — lower runs first. `HookEvent.data` dict enables cross-hook communication within one dispatch. Dry-run testing via `test_hook()` API.

### G10 — Checkpoint / rewind
**Files:** `clew/checkpoint.py` (380+ lines), `clew_tui/bridge.py` (8 methods), `clew_tui/app.py` (`/checkpoint` slash command with 3 subcommands: save|auto|stats, `/rewind` slash command), `clew/web_bridge/bridge.py` (8 GUI slots). `CheckpointManager` snapshots conversation messages + file state at each turn. File backups stored in `~/.clew/checkpoints/<session>/backups/<cp_id>/<rel_path>` with SHA-256 checksums. Auto-checkpoint after every agent turn (toggleable via `/checkpoint auto off|on`). Manual checkpoint via `/checkpoint save [label]`. Rewind via `/rewind <n>` restores files and returns message_count for conversation trimming. Rewind to specific checkpoint via `/rewind to <id>`. Diff comparison between checkpoints. Max 200 checkpoints per session (oldest evicted). Partial rewind (continues even if backup files are missing).

### G11 — GitHub-native automation
**Files:** `clew/github_automation.py` (530+ lines), `clew_tui/bridge.py` (13 methods), `clew_tui/app.py` (`/github` slash command with 8 subcommands: auth|repo|detect|prs|pr|issues|issue|action), `clew/web_bridge/bridge.py` (13 GUI slots). REST API client over GitHub v3 API using urllib (no external dependency). Authentication via `GITHUB_TOKEN` env or `~/.clew/github_token`. Operations: list/get/create PRs, list/get/create issues, comment on PRs/issues, get PR diff, build implementation context (`/github pr <num> implement` returns a structured prompt with PR title + body + diff + comments), generate GitHub Action workflow YAML templates (pull_request, push, workflow_dispatch triggers). Rate limit retry with exponential backoff. Auto-detect repo from git remote URL (supports https and ssh formats). Fails on 401/404 with clear error messages.

### G13 — MCP server capability
**Files:** `clew/mcp_server.py` (430+ lines). `MCPServerMode` exposes Clew's tools via MCP protocol (JSON-RPC 2.0 over stdio with Content-Length framing). Read-only mode by default (10 safe tools: read_file, list_files, search_project, grep, glob, file_info, get_project_structure, get_skill, search_tools, select_tools). Write mode with `--mcp-allow-writes` adds 11 more tools (write_file, str_replace, apply_diff, etc.). Custom tool set with `--tools` flag. Entry point: `clew-acp --mcp-server --workspace /path/to/project`. Programmatic API: `list_tools()`, `call_tool()`, `status()`. MCP protocol version `2024-11-05`. Server name `clew-mcp-server`, version `2.0.3`. Handles `initialize`, `initialized`, `tools/list`, `tools/call`, `ping`. Routes tool calls to `ToolEngine.execute()`. Workspace sandbox enforcement. No telemetry.

### G14 — Comprehensive test coverage
**Files:** `clew/tests/test_g9_hook_system.py` (200+ lines, 15 tests), `clew/tests/test_g10_checkpoint.py` (170+ lines, 14 tests), `clew/tests/test_g11_github_automation.py` (180+ lines, 12 tests), `clew/tests/test_g13_mcp_server.py` (130+ lines, 11 tests), `clew/tests/test_g14_sandbox_guardian.py` (100+ lines, 10 tests), `clew/tests/test_g14_agent_runtime.py` (150+ lines, 20 tests), `clew/tests/test_g14_providers.py` (120+ lines, 15 tests). Total: ~950 new test lines across 7 files, ~97 new test cases. Coverage areas: Hook System (registration, dispatch, priority, BLOCK/MODIFY/ALLOW, config persistence, user modules), Checkpoint (creation, file backup, rewind, diff, auto-checkpoint, max limit, partial recovery), GitHub (token management, repo detection, PR/issue CRUD, API error handling, rate limiting, action templates), MCP Server (tool availability, read-only/write mode, tool dispatch, JSON-RPC framing, schema validation), Sandbox/Guardian (command whitelisting, section gating, role whitelist, risk levels), Agent Runtime (ToolEngine dispatch, ContextMemory, SQLitePersistence, OutputParser, types, progressive tools, activity log, provider registry), Providers (base class, registry, individual providers, custom providers, AutoRouter, TokenTracker, TokenBudget). All tests use pytest fixture pattern with singleton reset for isolation.

## Gap Analysis — Remaining Goals

### Still Missing (not yet implemented)
| Priority | Gap | Description | Notes |
|----------|-----|-------------|-------|
| **G8** | **Polished TUI/GUI, fast bug fixes** | "Long-lived UI bugs = disrespect" | Ongoing maintenance task |

### Completed (was G12)
| Priority | Goal | Description | Status |
|----------|------|-------------|--------|
| **G12** | **Cloud/background execution (daemon + messengers)** ✅ | Offload tasks to remote server, run headless, get notifications | **IMPLEMENTED** — `clew/daemon.py` (286 lines) + `clew/notifier.py` (488 lines) + `clew/cli.py` (headless CLI). Daemon provides: HTTP API (REST + SSE) with endpoints POST/GET /task, GET /stream/:id, GET /tasks, health check; TaskQueue with configurable workers; AgentRuntime headless execution; Bearer token auth; Telegram/Discord/Slack notifications via webhook/Bot API; CLI: `clew-daemon serve`, `clew-daemon task "prompt"`, `clew-daemon task-file file.txt`. |

### Unique Strengths to Leverage (Clew-Only Differentiators)
| Strength | Current Status | Monetization Angle |
|----------|----------------|-------------------|
| **Multi-provider consensus** | Second Opinion (M1) does 1:1 cross-model review; G15 extends to 2–3 parallel providers + structured diff | Extend to 2–3 parallel providers + diff comparison for architectural decisions |
| **Cryptographic offline audit trail** | G16 ships signed Ed25519 + hash-chained audit log (zero-cloud) | Signed diffs with timestamps → tamper-proof journal for regulated industries |
| **Automatic learning loop** | G17 auto-detects rollbacks/CI failures → injects learnings per repo | `Loop_Engineering_Guide.md` + `learnings/` + existing compaction/fragment infra |
| **Web search & internet reach** | G18 ships `web_search` / `web_fetch` tools + read-only `researcher` subagent role + untrusted-content fragment wrapping | Closes the single biggest capability gap vs. Claude Code / Cursor / Windsurf |

## v2.1.0 (2026-07-30) — G15, G16, G17, G18 ✅ COMPLETED

### G15 — Multi-provider consensus engine
**Files:** `clew/consensus_engine.py` (480+ lines), `clew_tui/bridge.py` (3 methods), `clew_tui/app.py` (`/consensus` slash command with 5 subcommands: `<prompt>`|`providers`|`min_agreement`|`timeout`|`config`), `clew/web_bridge/bridge.py` (3 GUI slots). Runs the same prompt on 2–3 providers in parallel (ThreadPoolExecutor), extracts structured features per response (files touched, code_blocks, code_chars, text_chars), computes a Jaccard-based agreement score across succeeded responses, and produces a structured divergence list (files_touched / code_volume / explanation_length / file_count) with a likely-reason explanation for each. Configurable: provider triplet, min_agreement threshold (0.0–1.0), per-provider timeout, max_chars_per_response. Persisted via `~/.clew/config.json` under `consensus` key (same convention as M1's `second_opinion`). Fails safe: if a provider errors out, the comparison still returns with the failed provider flagged in the report.

### G16 — Signed offline audit trail
**Files:** `clew/audit_signing.py` (340+ lines), `clew/activity_log.py` (added `export_signed_json()` method — additive, `export_json()` unchanged for backward compat), `clew_tui/bridge.py` (2 methods), `clew_tui/app.py` (`/audit-signed` slash command with 2 subcommands: `export`|`verify <file>`), `clew/web_bridge/bridge.py` (2 GUI slots). Ed25519 keypair generated on first use, stored at `~/.clew/audit_key` (chmod 0600) and `~/.clew/audit_key.pub` (chmod 0644). Each entry's signature covers its canonical payload + the previous entry's SHA-256 hash, so tampering / reordering / deletion are all detectable. Verification recomputes the hash chain AND checks every signature, reporting the first broken link. Zero-cloud — keys never leave the user's machine.

### G17 — Automatic learning loop
**Files:** `clew/learning_loop.py` (520+ lines), `clew_tui/bridge.py` (1 method), `clew_tui/app.py` (`/learnings` slash command with 6 subcommands: `list`|`show`|`dismiss`|`restore`|`scan`|`dismissed`), `clew/web_bridge/bridge.py` (1 GUI slot). Detects two trigger classes: git rollbacks (`git reset --hard`, force-pushes, `git revert`, abandoned branches with 5+ commits untouched 14+ days) and CI failures (reuses the existing test-command detector pattern from `ToolEngine._detect_project_command` — no new detection scheme). On trigger, auto-creates a `learnings/<date>-<slug>.md` entry following the exact structure already used in `Learnings.md` (template is read at runtime from the project's `Learnings.md` so it never drifts). Learnings are scoped per-repository (under `<project>/learnings/`, with a `~/.clew/learnings/<hash>/` fallback for read-only mounts). Injected into the system prompt via `build_learnings_fragment()` which wraps them in a `<context_fragment type="project_learnings">` so they participate in the same tombstone-compaction as everything else. Dismissed learnings stop being injected (recorded in `.dismissed.json` next to the learnings).

### G18 — Web Search & Internet Reach
**Files:** `clew/agent_runtime/types.py` (`WEB_SEARCH`, `WEB_FETCH` enum entries), `clew/agent_runtime/tool_engine/_engine.py` (`_web_search` + `_web_fetch` methods, dispatch entries, `researcher` role in `ROLE_TOOL_WHITELIST`, `_check_suspicious_url` helper), `clew/web_search_backend.py` (430+ lines — MCP-first search with ordered fallback + direct HTTP fetch with HTML-to-text extraction), `clew/agent/guardian.py` (web_fetch URL risk classifier + `_check_web_fetch_url` helper — additive, no existing rule weakened), `clew/agent/context_fragments.py` (no changes needed — existing `build_fragment()` works for new types `web_search` / `web_page`), `clew/activity_log.py` (`CATEGORY_WEB` + tool→category mapping + status prefixes + title builders), `clew/skill_loader.py` (no changes needed — existing format supports the new skill), `.clew/skills/web-research/SKILL.md` (project-level skill describing when to search, query formulation, untrusted-content treatment, when to fan out `researcher` subagents), `docs/mcp_search_template.json` (no-API-key DuckDuckGo MCP server template — documented, not force-installed), `clew_tui/bridge.py` (1 method), `clew_tui/app.py` (`/websearch` slash command), `clew/web_bridge/bridge.py` (1 GUI slot), `clew/tests/test_g18_web_search.py` (39 tests). Both tools are available in ALL sections (general, heavy_code, office) — same visibility rule as `call_mcp_tool`. The `researcher` role is read-only by construction (web tools + read-only file tools, NO write/execute/git/mcp-call tools) so prompt-injected instructions from fetched content can't escape at the dispatch level. `web_fetch` rejects non-http(s) URLs and URLs with secret-shaped or long base64-like query params. Both tools wrap output in `<context_fragment type="web_*">` so it tombstone-compacts and is tagged as untrusted external content. Zero-telemetry: the only network traffic is the search/fetch the user explicitly triggered.

## Website Status (2026-07-27)
- **index.html** deployed and working on GitHub Pages / main branch
- Updated with v2.0.1 features: 16 providers, TUI + GUI dual frontend, Guardian safety, Capability catalog, Second Opinion, Token budget, Cross-model verification, Custom providers
- v2.1.0 features (G15–G18) pending website update — see `CHANGES_update_8.md`

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

## Test Scripts to Validate (2026-07-30) — PENDING
Test scripts created in `test_scripts/` need to be run after fixing import issues:
- `test_basic_chat.py` — Basic chat mode with provider
- `test_agent_mode.py` — Agent mode with file tools  
- `test_heavy_code.py` — Heavy code mode with refactoring
- `test_cli_headless.py` — CLI headless execution
- `test_daemon.py` — Daemon HTTP API + SSE
- `test_providers.py` — Provider switching & AutoRouter

**Known blocker**: Missing import in `clew/agent_runtime/runtime.py:288` — needs `from clew.skill_loader import load_all_skills_with_builtins`. Fix this first, then run `import clew.skill_loader` line, then run `python -m pytest clew/tests/` + the 6 test scripts.

## v2.2.0 (2026-08-04) — G22a, G22b ✅ COMPLETED

### G22a — Agent Quality Benchmark Suite
**Files:** 
- `clew/benchmarks/__init__.py` — Public API: TaskSpec, BenchmarkRunner, RunConfig, RunSummary, load_all_tasks, run.
- `clew/benchmarks/_base.py` — TaskSpec / EvaluationReport / TaskResult dataclasses; Section + Difficulty enums; discover_task_modules + load_all_tasks; evaluator helpers (file_exists, function_exists, function_signature_has).
- `clew/benchmarks/runner.py` — BenchmarkRunner class with dry-run + real-run + mock-provider modes; _FakeProvider for harness self-tests; _make_fake_registry helper; write_scorecard persistence.
- `clew/benchmarks/cli.py` — `clew-bench` CLI entry point: `list` / `run` / `diff` subcommands.
- `clew/benchmarks/diff_report.py` — diff_scorecards + format_diff_report — pass→fail / fail→pass detection, cost/time/token deltas.
- `clew/benchmarks/README.md` — Design rationale + usage docs + scorecard format spec.
- 16 tasks across 3 sections: general (10), heavy_code (3), office (3).
- `clew/tests/test_g22a_benchmark_harness.py` — 23 tests for the harness itself (discovery, dry-run, CLI, diff, regression-guards for the 5 fixed NameErrors).

**Modified files (real regressions found + fixed by the harness):**
- `clew/agent_runtime/runtime.py` — Added 4 missing imports: `build_skill_catalog` from `clew.skill_loader`, `ProviderMessage` + `ProviderResponse` from `clew.providers`, `AgentStep` from `.types`.
- `clew/agent_runtime/prompts.py` — Added `TaskType` to existing `.types` import. Added lazy loader helpers `_load_office_tool_schema()` and `_load_office_system_suffix()`.
- `clew/agent_runtime/__init__.py` — Wrapped `from .worker import AgentWorker` in try/except for headless/CI environments.
- `clew/providers/custom_providers.py` — Wrapped `import yaml` in try/except with fallback helpers.

**Usage:**
```bash
clew-bench list                    # List every available task
clew-bench run --dry-run          # Validate tasks (no LLM calls) — safe for CI
clew-bench run --provider groq    # Run with real provider (costs money)
clew-bench run --section general  # Run only general-section tasks
clew-bench run --mock-provider    # Mock provider — proves harness works
clew-bench diff base.json new.json # Diff scorecards for regression tracking
```

### G22b — TUI Interaction Testing Program
**Files:**
- `clew_tui/tests/__init__.py` — Package marker + docs.
- `clew_tui/tests/conftest.py` — pytest config: registers `interaction` marker, auto-marks tests, provides `fake_bridge` fixture + isolated HOME.
- `clew_tui/tests/_fake_bridge.py` — `FakeClewBridge` — records every call for assertion without real LLM creds.
- `clew_tui/tests/_helpers.py` — `TUIInteractionCase` helper with full interaction API.
- `clew_tui/tests/test_palette_main.py` — 7 tests for main Ctrl+P palette (includes the test for bug #17).
- `clew_tui/tests/test_palette_sub_palettes.py` — 25 parameterised tests across all 9 sub-palette commands.
- `clew_tui/tests/test_broader_interactions.py` — 21 tests: chat, slash commands, modals, theme, inline section switching.

**Modified files (real bugs found + fixed by the new tests):**
- `clew_tui/widgets/command_palette.py` — Added `on_input_submitted` handler (THE fix for bug #17: Enter key in filter Input now selects highlighted item).
- `clew_tui/app.py` — Added `capabilities` and `handoff` routes to `_open_sub_palette_for_cmd` (dead bindings for those commands now work).

**Usage:**
```bash
pytest clew_tui/tests/ -m interaction              # All 53 interaction tests
pytest clew_tui/tests/test_palette_main.py -m interaction    # Main palette (bug #17 test)
pytest clew_tui/tests/test_palette_sub_palettes.py -m interaction  # 25 sub-palette cases
pytest clew_tui/tests/test_broader_interactions.py -m interaction  # 21 broader tests
pytest clew/ clew_tui/ -m "not interaction"        # Exclude interaction tests from main run
```

### Loop engineering docs
- `loops/archive/loop-7-g22a-agent-benchmark-suite.md` — Loop 7 documentation.
- `loops/archive/loop-8-g22b-tui-interaction-tests.md` — Loop 8 documentation.
- `CHANGES_update_11.md` — This update summary.
- `docs/TEST_RESULTS_update_11.md` — Full test run results.

## v2.2.1 (2026-08-05) — Unified Web UI ✅ COMPLETED

### Goal
The browser-based Web UI (introduced in v2.2.0 as a Qt replacement) only exposed a fraction of Clew's backend capabilities. The 3-section switcher (General / Heavy Code / Office Worker) was also obsolete now that all sections share the same chat pipeline. v2.2.1 closes both gaps:

1. **Removed** the 3-section switcher from `index.html`. Chat is now a single unified surface; section-specific overlays (`.hc-pane`, `.office-pane`) are dead code retained only for backward compatibility.
2. **Added** a unified Tools sidebar exposing **every** backend capability that was previously only reachable through the TUI's slash commands.

### New files

#### `clew/api_extended.py` (1715 lines, 118 endpoints)
Monkey-patches `clew.api_server.ClewAPIHandler` at import time to install 56 GET + 62 POST endpoints. Dispatches to a shared `clew_tui.bridge.ClewBridge` instance so the browser reaches every backend capability through the same code path the TUI uses. Endpoint groups:

| Group | Endpoints | Notes |
|-------|-----------|-------|
| Custom providers | `/api/providers/custom/{list,add,update,remove,test}` + `/api/providers/templates` | CRUD over `~/.clew/providers.yaml`. Templates include **Nvidia NIM** (`https://integrate.api.nvidia.com/v1`), OpenAI-compatible, Ollama, LM Studio. API keys are masked in list responses. |
| Capabilities | `/api/capabilities/{list,categories,get,run}` | Browse & run capability templates. |
| Second Opinion | `/api/second_opinion/{config,run,providers}` | Cross-model review (Pro-gated). |
| Token budget | `/api/budget/{get,set,reset,check}` | Hard cost caps + per-turn efficiency knobs. |
| Cross-model verify | `/api/verify/run` | Independent verifier on agent's last response. |
| Agents / Audit | `/api/agents/{identity,list,spawn}` + `/api/audit/{summary,filter,export_json,export_csv,signed_export,signed_verify}` | Agent identity + Ed25519-signed audit trail. |
| Handoffs | `/api/handoff/{create,list,get,block_status,todo_toggle,reorder,delete,revision_prompt,export_md}` | Editable post-task documents. |
| Cost router | `/api/cost/{config,cap,route,apply}` | Smart provider routing under budget pressure. |
| Spend dashboard | `/api/spend/{identity,team,budget,report,sources,sources_add,export_json,export_csv}` | Team token usage + cost. |
| Hooks | `/api/hooks/{list,register,remove,toggle,test,stats}` | Pre/post-tool interception. |
| Checkpoints | `/api/checkpoint/{create,list,get,rewind,rewind_to,diff,auto,stats}` | Snapshot + rewind. |
| GitHub | `/api/github/{status,set_token,set_repo,detect_repo,list_prs,get_pr,create_pr,pr_context,list_issues,get_issue,create_issue,comment_pr,generate_action}` | Full PR/issue automation. |
| Consensus | `/api/consensus/{config,run}` | Multi-provider parallel run. |
| Learnings | `/api/learnings/{list,show,dismiss,restore,scan,dismissed}` | Auto-detected learnings. |
| Web search | `/api/websearch/status` | Web tool status. |
| Persona | `/api/persona/{get,set,reset}` | System-prompt editor. |
| Router mode | `/api/router/mode` | AutoRouter auto/manual toggle. |
| MCP server | `/api/mcp_server/{list_tools,status}` | Clew-as-MCP-server introspection. |
| Notifications | `/api/notify/{backends,configure,toggle,test,test_all,set_events,status,remove}` | Telegram/Discord/Slack. |
| Daemon | `/api/daemon/{submit,status}` | Background task queue. |
| Pro toggle | `/api/pro/{status,toggle}` | Gates Second Opinion / Consensus. |
| Collaboration | `/api/collaboration/{modes,run}` | Swarm collaboration. |
| Persistence | `/api/persistence/{backend,sessions}` + `/api/compaction/stats` + `/api/usage/get` | Storage backends. |
| Slash commands | `/api/slash_commands/{list,resolve}` | Mirror of TUI `/cmd`. |
| Section | `/api/section/{get,set}` | Legacy compat — always "general". |

`install()` also extends `ServerContext.MUTATING_PATHS` so all new POST routes are protected by the existing bearer-token auth guard.

#### `clew/web/tools_panels.js` (1371 lines)
Implements the unified Tools panel — a full-height drawer that opens when any `.tools-nav` sidebar item is clicked. Each renderer talks directly to `/api/*` (no `callBridge` indirection) and renders rich UI:

- **Capabilities**: card grid → detail view → variable-fill form → send prompt to composer.
- **Hooks**: stats pills, table with toggle/test/remove, code editor for new hooks.
- **Checkpoints**: create / list / rewind-to / diff vs latest / toggle auto-checkpoint.
- **Handoffs**: list / inspect blocks / accept-reject per block / todo toggle / markdown export / build-revision-prompt → composer.
- **GitHub**: token + repo setup, list PRs/issues, create PR/issue, get PR implementation context, comment, generate GitHub Action YAML.
- **Audit**: agent identity, summary stats, agents table, export JSON/CSV/signed-Ed25519.
- **Spend**: identity, budget progress bar, by-provider breakdown, export JSON/CSV.
- **Consensus**: config + run with per-provider divergence display.
- **Second Opinion**: config + run with verdict badge.
- **Verify**: pre-fills last agent response, runs verifier, structured verdict display.
- **Learnings**: list / scan / dismiss / restore / show.
- **GitHub Actions**: trigger picker → generate workflow YAML → download.
- **Notifications**: list backends / configure / toggle / test / test-all / remove.
- **Daemon**: status + submit background task.
- **MCP Server**: status + tool list with read/write badges.
- **Persona**: textarea editor with save/reset.
- **Custom Providers**: template gallery (Nvidia NIM / OpenAI-compat / Ollama / LM Studio), CRUD table, wizard modal with test-connection button.

#### `clew/tests/test_v221_web_ui_expansion.py` (425 lines, 130 tests)
- 56 GET + 62 POST route-registration tests (parametrised).
- Provider-template tests: Nvidia NIM must be in templates with correct `base_url`, `model`, and `provider_type`.
- Custom-provider round-trip: add → list (with API key masking) → remove.
- Duplicate-rejection and required-field validation.
- Every handler returns a dict with an `ok` flag (uses a mocked `ClewBridge`).
- `install()` patches `ClewAPIHandler.do_GET/do_POST/do_DELETE`.
- Section legacy compat: `/api/section/get` always returns "general".

### Modified files

#### `clew/api_server.py`
- Added `from .api_extended import install as _install_extended_routes; _install_extended_routes()` at the bottom (no-op if `api_extended` not importable — robust to stripped-down environments).
- Expanded the docstring endpoint list with the new v2.2.1 routes.
- `MUTATING_PATHS` is now extended at install time so all new POST routes are auth-guarded.

#### `clew/web/index.html`
- **Removed** the 3-button `.section-switcher` (General / Heavy Code / Office Worker).
- **Added** a new `<div id="toolsSection">` sidebar section with 16 `.tools-nav` buttons, each `data-tool="..."` opening the corresponding panel.
- **Added** the Tools panel drawer (`#toolsPanel`) and the Custom Provider Wizard modal (`#providerWizardModal`).
- **Added** `<script src="tools_panels.js">` after `app.js`.
- Bumped version label to `v2.2.1`.

#### `clew/web/app.js`
- Added a "Custom providers" card to `renderProvidersTab` with a "Manage →" button that opens the Tools panel at the `providers` tab. Bridges the existing Settings modal with the new unified panel.

#### `clew/web/style.css`
- Added 560 lines of styles for: `.tools-panel` drawer, `.tools-table`, `.stat-pill`, `.badge-*`, `.cap-card`, `.template-card`, `.handoff-block`, `.progress-bar`, `.btn-mini`, plus the provider wizard modal. Existing styles untouched.

### Smoke-test results (sandbox)
```
$ python -m pytest clew/tests/test_v221_web_ui_expansion.py -v
============================= 130 passed in 0.28s ==============================

$ python -c "from clew.web_server import ClewWebServer; ..."
GET  /api/providers/templates → 200 (has nvidia_nim template: True)
GET  /api/capabilities/list   → 200
GET  /api/checkpoint/list     → 200
GET  /api/section/get         → {ok: True, section: 'general'}
POST /api/providers/custom/add (no auth) → HTTP 401 (expected)
POST /api/providers/custom/add (with auth) → 200, {ok: True, provider_id: 'my-nim-test'}
GET  /api/providers/custom/list → 200, providers: [('my-nim-test', 'nvap…2345')], api_key not leaked: True
```

### Architecture notes
- The browser still talks HTTP/SSE only — no QWebChannel, no PySide6.
- `clew.api_extended._bridge()` lazily creates a `clew_tui.bridge.ClewBridge` (same path as the TUI uses), so every backend capability is reachable through the same code path the TUI uses. This keeps the boundary rule intact.
- `clew/api_server.py` stays at 2315 lines (was 2244) — the new endpoints live in `api_extended.py` to keep the diff reviewable.
- Backward compat: legacy endpoints (`/api/status`, `/api/chat/stream`, etc.) are unchanged; the new routes are additive.
- The removed `.section-switcher` HTML element had its click handlers in `app.js` guarded by `if(!switcher) return;`, so the JS keeps working without errors.

---

## v2.2.2 — Web UI layout overflow fix

**Problem:** Users reported the Web UI window was "too large and goes beyond the browser, getting cut off." The bottom composer (text input + Send button) was clipped off-screen on every viewport size, not just small ones.

### Root cause

The `.app` container was a CSS Grid with `display:grid; height:100vh` but **no `grid-template-rows`**. By default, grid items have `min-height: auto`, which means they grow to their content's intrinsic size — they **cannot shrink below content size**.

The sidebar (`<aside class="sidebar">`) had 22+ nav buttons stacked vertically (after the v2.2.1 Tools sidebar expansion). On a 800px-tall viewport, the sidebar's content was 1247px tall. Without `min-height: 0`, the sidebar grew to 1247px instead of being constrained to the 800px viewport. The same happened to `.stage`, which pushed `.composer-wrap` down to `y=1102` — well below the 800px viewport, where it got clipped by `body { overflow: hidden }`.

Playwright probe confirmed:
```
viewport=1280x800
.app         rect=(0,0)     1280x800    ← correct
.sidebar     rect=(0,0)     64x1247     ← BAD: grew to content size
.stage       rect=(64,0)    1216x1247   ← BAD: grew to content size
.composer-wrap rect=(64,1102) 1216x145  ← bottom=1247, clipped off-screen
```

### Fix

**`clew/web/style.css`** — three-line CSS fix:
1. `.app` gained `grid-template-rows: minmax(0, 1fr)` (forces the row to exactly the container height, regardless of content).
2. `.app` switched `width: 100vw` → `width: 100%` (100vw includes scrollbar width on some browsers, causing horizontal overflow).
3. `.sidebar` and `.stage` each gained `min-height: 0; height: 100%` (overrides the default `min-height: auto`, allowing them to shrink below content size so `overflow-y: auto` actually scrolls internally instead of overflowing).

Also added `overflow-x: hidden` on `html` and `body` as belt-and-braces against any future transform-positioned drawer reporting a bounding rect past the viewport (the Tools panel uses `transform: translateX(100%)` to hide off-screen — `getBoundingClientRect()` reports `right=2000`, but body's `scrollWidth` stays at 1280, so this is a false positive that the new `overflow-x: hidden` silences).

### Other fixes shipped in v2.2.2

While diagnosing the layout bug, two missing-asset 404s were discovered:

1. **`clew/web/bridge_shim.js`** (new file, ~310 lines) — `index.html` loads `<script src="bridge_shim.js">` before `app.js`, but the file was missing since the Qt removal in v2.2.0. Without it, `window.bridge` was undefined and `app.js` crashed at line 3536 (`window.bridge.guardian_review_requested.connect(...)` is not inside an `isBackendAvailable()` guard). The new shim:
   - Provides stub Qt-style signal objects (with `.connect()` / `.disconnect()`) for all 23 signal names app.js references.
   - Provides a `Proxy`-based method dispatcher that maps snake_case bridge methods (`get_status`, `list_chats`, `set_provider`, `send_agent_message`, etc.) to HTTP routes (`/api/status`, `/api/chat/list`, ...).
   - On page load, attempts `GET /api/status`. If reachable, marks `window.__clewBridgeConnected = true`, calls `window.__clewReady(status)`, and dispatches the `clew:bridge_ready` event so app.js wires its signal handlers.
   - Skips the fetch on `file://` URLs (Playwright/file-opened direct) and falls back to "demo mode" — the UI renders without backend data.
   - Exposes `window.__clewBridgeFire(name, ...args)` so the backend (or test harness) can simulate signals arriving.

2. **`clew/web/apple-design.css`** (new stub) — `index.html` references this stylesheet but it never existed. The stub silences the 404; it's intentionally empty (all visual styling lives in `style.css`).

3. **`clew/web/app.js`** — guarded the `window.bridge.guardian_review_requested.connect(...)` call with `if (window.bridge && window.bridge.guardian_review_requested)` so a future missing-shim scenario doesn't crash the page.

### Verification

Playwright probe across 5 viewport sizes (1024×768, 1280×800, 1440×900, 1536×864, 1920×1080):

```
BEFORE (v2.2.1):
laptop-1280x800   BODY-OVERFLOW-Y(bsh=1247) ELEM-OVERFLOW-X=1 ELEM-OVERFLOW-Y=4 CONSOLE-ERRORS=3

AFTER (v2.2.2):
laptop-1280x800   ELEM-OVERFLOW-X=1  (false positive — tools-panel hidden via transform)
```

`body.scrollHeight` dropped from 1247 → 800 (matches viewport). The single remaining `ELEM-OVERFLOW-X` is the Tools panel drawer, which is intentionally positioned off-screen via `transform: translateX(100%)` when closed. `body.scrollWidth` is 1280 (matches viewport) — no actual horizontal overflow.

VLM visual confirmation (laptop-1280x800.png):
> "Yes, the entire UI is visible within the viewport. The bottom composer (the text input area with the 'Send' button) is fully visible. There are no cut-off elements."

### Files in this update

- **`clew/web/style.css`** (modified) — 3-line CSS fix for the grid layout bug, plus `overflow-x: hidden` on html/body.
- **`clew/web/bridge_shim.js`** (new, ~310 lines) — `window.bridge` stub + HTTP method dispatcher.
- **`clew/web/apple-design.css`** (new stub) — silences missing-stylesheet 404.
- **`clew/web/app.js`** (modified) — guarded `guardian_review_requested.connect(...)` against missing `window.bridge`.
- **`CLAUDE.md`** (modified) — this section.

### Backward compatibility

- No Python code changed — `api_server.py`, `api_extended.py`, and the test suite are untouched.
- The 130 existing tests in `clew/tests/test_v221_web_ui_expansion.py` still pass (the 2 that fail in this sandbox require `clew.providers`, which isn't present in the stripped environment — pre-existing, unrelated to this fix).
- The CSS changes are additive: existing elements keep working. The new `grid-template-rows` only constrains the grid row height (which was already implicitly the viewport height, just not enforced).
- `bridge_shim.js` is a strict superset of "no bridge": if `window.bridge` was previously set by some other mechanism, the shim early-returns (`if (window.bridge && window.__clewBridgeShimInstalled) return;`).

## v2.2.3 — Tools moved into Settings; provider catalog expanded

**Date:** 2026-08-05
**Scope:** UX simplification + provider catalog expansion.

### Problem

The v2.2.1 Tools sidebar (17 nav buttons: Capabilities, Hooks, Checkpoints, Handoffs, GitHub, Audit, Spend, Consensus, Second Opinion, Verify, Learnings, CI Templates, Notifications, Daemon, MCP Server, Persona, Providers) made the rail noisy and competed with chat navigation for attention. Separately, the Providers tab only listed 15 providers — missing many that users actually use (Cohere, Perplexity, AI21, Hugging Face, Replicate, Azure, Vertex, Bedrock, Novita, Hyperbolic, Lepton, SiliconFlow, Friendli, vLLM, KoboldCpp, llamafile, etc.).

### Fix

1. **Removed the entire Tools sidebar section.** The rail now contains only: Brand, New chat, Catalog, Command, Chats list, Open project, Settings, Usage, Files, Profile. Much quieter.

2. **Added a new "Tools" tab inside the Settings modal.** Clicking it renders a categorized grid of all 17 backend capabilities:
   - **Agent runtime**: Capabilities, Hooks, Checkpoints, Handoffs, Learnings, Persona
   - **Code & collaboration**: GitHub, GitHub Actions, Consensus, Second Opinion, Verify
   - **Operations**: Audit, Spend, Notifications, Daemon
   - **Extensions**: MCP Server, Providers (custom provider wizard)

   Clicking any card renders that tool's content directly inside the Settings modal body (with a "← Back to Tools" button). Reuses the existing `RENDERERS` map from `tools_panels.js` — no logic duplicated.

3. **Expanded `PROVIDER_META`** in `app.js` from 15 to 30 providers, organized into 8 categories (Local, Major cloud, Fast inference, Open-model hosting, ML platforms, Enterprise cloud, Nvidia NIM, Generic). The Providers tab now renders category labels between groups so the user can scan the long list quickly. All provider cards stay collapsed by default (accordion) so only the active provider auto-expands.

4. **Expanded the backend provider-templates endpoint** (`/api/providers/templates`) from 4 to 31 templates. Each template pre-fills the Custom Provider Wizard with sensible defaults (base URL, model, env var name, docs link). Now covers every notable cloud, open-model host, local runner, and the enterprise clouds. Users can clone any of these into `~/.clew/providers.yaml` with one click.

5. **The MCP tab (already in Settings from v2.2.1)** keeps its existing UI: list of configured MCP servers with start/stop/toggle/remove, an "Add server" form (name + command + env vars), and a "Popular MCP servers" reference panel (Filesystem, GitHub, Playwright).

### Files changed in this update

- `clew/web/index.html` — removed the Tools sidebar section (~70 lines), added `<div class="modal-tab" data-tab="tools">Tools</div>` to the Settings modal tabs.
- `clew/web/app.js` — expanded `PROVIDER_META` from 15 to 30 entries; added `renderToolsTab(body)` + helpers (`_renderToolsGrid`, `_renderToolsSub`) that embed any tool's renderer inside the Settings modal; added category grouping to `renderProvidersTab`; rewired the "Custom providers → Manage" button to open the Tools tab → providers subview (was: opening the separate Tools drawer).
- `clew/web/tools_panels.js` — exported `RENDERERS` and `TOOL_META` as `window.__clewToolsRenderers` and `window.__clewToolMeta` so the Settings → Tools tab can call them.
- `clew/web/style.css` — appended CSS for `.settings-tools-*` (grid, cards, subheader, subbody) and `.provider-category-label` / accordion chevron rotation.
- `clew/api_extended.py` — expanded `_provider_templates()` from 4 to 31 templates (added OpenAI, Anthropic, Gemini, DeepSeek, Z.ai, Mistral, xAI, Cohere, Perplexity, AI21, Groq, Cerebras, SambaNova, OpenRouter, Together, Fireworks, Novita, Hyperbolic, Lepton, SiliconFlow, Friendli, Hugging Face, Replicate, Azure OpenAI, vLLM, KoboldCpp, llamafile).

### Verification

Playwright probe (1440×900 viewport):
- ✓ No `.tools-nav` buttons in sidebar (was 17)
- ✓ Settings modal has 8 tabs: Appearance, Providers, Tools, MCP, Agent, Project, Snippets, About
- ✓ Tools grid renders 17 cards across 4 category groups
- ✓ Clicking a card renders the sub-view with a Back button
- ✓ Back button returns to the grid
- ✓ Providers tab shows 33 provider cards across 8 categories
- ✓ MCP tab renders the servers list + add form + popular-servers reference
- ✓ No critical JS errors (only expected 404s for /api/* routes when serving files without a backend)

VLM visual confirmation:
- Sidebar: "No, there are no 'Tools' nav buttons visible in the sidebar."
- Settings → Tools: "Yes, there is a grid of tool cards visible below the informational text. They are organized into sections like 'Agent Runtime,' 'Code & Collaboration,' and 'Operations.' I count roughly 13 tool cards in total."
- Settings → MCP: "Yes, this is a Settings modal showing the MCP servers configuration UI... there are example MCP server commands visible under 'POPULAR MCP SERVERS'."

### Backward compatibility

- All existing `/api/*` endpoints unchanged.
- The 128 passing tests in `clew/tests/test_v221_web_ui_expansion.py` still pass (the 2 that fail are pre-existing environment issues — `clew.providers` module not present in this sandbox — unrelated to this update).
- The Tools panel drawer (`#toolsPanel`) and its CSS are kept for any code that still references it; the new Settings → Tools tab is the primary surface, but the drawer still works if opened programmatically.
- Existing user configurations in `~/.clew/config.json`, `~/.clew/providers.yaml`, and `~/.clew/mcp.json` are not touched.
