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
- **Run MCP server**: `clew-acp --mcp-server` (or `python -m clew.mcp_server`)
- **Run daemon**: `clew-daemon serve` (HTTP API + SSE on port 8765) / `clew-daemon task "prompt" --notify telegram`
- **Run tests**: `pytest clew/` (or `pytest clew/agent/test_v2.py` for v2-specific)
- **Lint**: `black clew/ clew_tui/` + `mypy clew/ clew_tui/`

## Architecture Overview

### Two packages, one boundary rule
- **`clew/`** — core: agent runtime, providers, web bridge, Qt GUI
- **`clew_tui/`** — TUI frontend. **Never imports clew internals directly.** Communicates exclusively through `clew_tui.bridge.ClewBridge`, which owns a plain `AgentRuntime` (same path as `clew/cli.py`). If a widget needs something from the core, add a method to `ClewBridge`.

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
