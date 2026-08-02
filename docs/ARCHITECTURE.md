# Clew v2.1.1 — Architecture

This document describes the architecture of Clew as of v2.1.1. It is synchronized
with the actual directory layout and module organization verified against the
codebase.

## 1. High-level diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Clew v2.1.1 process                              │
│                                                                           │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌─────────────────┐ │
│  │ Qt GUI (PySide6)     │  │ Textual TUI           │  │ Headless CLI    │ │
│  │ - QWebEngineView      │  │ - ClewTUIApp          │  │ - clew-cli      │ │
│  │ - QWebChannel bridge │  │ - ClewBridge          │  │ - clew-acp      │ │
│  │ - ClewMainWindow     │  │ - widgets/*            │  │ - clew-daemon   │ │
│  └─────────┬────────────┘  └─────────┬────────────┘  └────────┬────────┘ │
│            │                         │                        │           │
│            └─────────────────────────┼────────────────────────┘           │
│                                      ▼                                     │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                     clew/agent_runtime (legacy, 1511 LOC)           │   │
│  │  ┌──────────────┐  ┌─────────────┐  ┌──────────────────────────┐   │   │
│  │  │ AgentRuntime  │  │ ToolEngine  │  │ PromptBuilder             │   │   │
│  │  │ (runtime.py)  │  │ (_engine.py │  │ (prompts.py)              │   │   │
│  │  │ ReAct loop    │  │  3032 LOC)  │  │ Section-aware templates   │   │   │
│  │  │ - run()       │  │  21 tools   │  │ - general/heavy_code/      │   │   │
│  │  │ - chat()      │  │  + sandbox  │  │   office                  │   │   │
│  │  └──────┬───────┘  └──────┬───────┘  └──────────────────────────┘   │   │
│  │         │                 │                                           │   │
│  │         └────────┬────────┘                                           │   │
│  │                  ▼                                                     │   │
│  │  ┌──────────────────────────────────────────────────────────────┐     │   │
│  │  │  Agent loop data flow                                         │     │   │
│  │  │  1. HookManager.dispatch_pre_tool_use() → BLOCK/MODIFY       │     │   │
│  │  │  2. ToolEngine.execute(tool_call)                            │     │   │
│  │  │     → Guardian risk assessment (if enabled)                   │     │   │
│  │  │     → LLM review (if risk above threshold)                    │     │   │
│  │  │     → Confirmation callback (if autonomy="always_ask")       │     │   │
│  │  │     → Tool dispatch                                           │     │   │
│  │  │  3. HookManager.dispatch_post_tool_use() — informational     │     │   │
│  │  │  4. ActivityLog.record_tool_call() — audit trail              │     │   │
│  │  │  5. CheckpointManager.auto_checkpoint() — snapshot state     │     │   │
│  │  └──────────────────────────────────────────────────────────────┘     │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                    clew/agent (v2, 398 LOC)                         │   │
│  │  Wraps AgentRuntime, adds:                                          │   │
│  │  - AgentRuntimeV2 — asyncio ChatStateActor                          │   │
│  │  - CircuitBreaker — sliding-window rate-limit handling              │   │
│  │  - CompactionEngine — three-tier (intra/inter/code)                │   │
│  │  - InterjectionBuffer — mid-turn user input                         │   │
│  │  - SubagentV2 — read-only explore/plan sub-agents                   │   │
│  │  - Guardian — LLM-based safety reviewer                             │   │
│  │  - Sandbox — OS-level kernel isolation (Landlock/Seatbelt)          │   │
│  └────────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│  ┌──────────────────────────────┐  ┌────────────────────────────────┐    │
│  │ clew_native (Rust, optional) │  │ clew/providers/ (17 providers) │    │
│  │ - sandbox                    │  │ - Anthropic, OpenAI, Gemini     │    │
│  │ - circuit_breaker            │  │ - Groq, DeepSeek, Mistral       │    │
│  │ - interjection               │  │ - Cerebras, Together, Fireworks │    │
│  │ - compaction                 │  │ - xAI, z.ai, Nvidia NIM         │    │
│  │ - actor (CancelToken)        │  │ - SambaNova, OpenRouter         │    │
│  └──────────────────────────────┘  │ - Ollama, LM Studio (local)     │    │
│                                      │ - User-custom provider plugins  │    │
│                                      └────────────────────────────────┘    │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │                    clew_tui/ — Textual frontend                     │   │
│  │  ┌──────────────┐  ┌─────────────────┐  ┌────────────────────┐    │   │
│  │  │ ClewBridge    │  │ ClewTUIApp      │  │ widgets/            │    │   │
│  │  │ (bridge.py)   │  │ (app.py)        │  │ - chat_log.py       │    │   │
│  │  │ Owns AgentRT  │  │ Slash commands  │  │ - input_box.py      │    │   │
│  │  │ Event routing │  │ /mode, /theme,  │  │ - status_bar.py     │    │   │
│  │  └──────────────┘  │ /checkpoint, etc │  │ - thinking.py       │    │   │
│  │                      └─────────────────┘  │ - tool_block.py     │    │   │
│  │                                            │ - approval_modal.py │    │   │
│  │                                            │ - verification_modal│    │   │
│  └────────────────────────────────────────────┴────────────────────┘    │   │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  Feature modules (one file per feature)                             │   │
│  │  G5  agent_identity.py    — Agent identity + tool-call audit       │   │
│  │  G6  handoff_bridge.py    — Post-task CMS / editable handoff       │   │
│  │  G7  capability_catalog.py — Template catalog (20 templates)       │   │
│  │  G9  hook_system.py       — Process-wide HookManager              │   │
│  │  G10 checkpoint.py        — Conversation + file snapshot          │   │
│  │  G11 github_automation.py — REST API PR/issue automation          │   │
│  │  G13 mcp_server.py        — Clew as MCP server for other agents   │   │
│  │  G15 consensus_engine.py   — Multi-provider parallel comparison   │   │
│  │  G16 audit_signing.py     — Ed25519 signed hash-chain audit       │   │
│  │  G17 learning_loop.py     — Auto-detect failures → learnings      │   │
│  │  G18 web_search_backend.py — MCP-first search + direct fetch      │   │
│  │  M1  second_opinion.py    — Cross-model review before approval    │   │
│  │  M2  cost_router.py       — Budget-aware provider routing         │   │
│  │  M3  spend_dashboard.py   — Team spend report aggregation         │   │
│  │      daemon.py            — HTTP API + SSE remote agent server    │   │
│  │      notifier.py          — Telegram/Discord/Slack notifications  │   │
│  │      token_budget.py      — Predictable limits (4 knobs)          │   │
│  │      token_tracker.py     — Per-message token/cost tracking      │   │
│  │      activity_log.py      — First-class audit trail               │   │
│  │      section_parser.py    — Inline {section} / /mode switching    │   │
│  └────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

## 2. Directory layout (actual, v2.1.1)

```
clew/
├── agent_runtime/                  # Core ReAct agent (legacy, actively used)
│   ├── __init__.py                 # Public API exports
│   ├── runtime.py                  # AgentRuntime — ReAct loop, run/chat/write/edit
│   ├── types.py                    # AgentEvent, TaskResult, ToolName enum
│   ├── parser.py                   # OutputParser — extracts tool_call/final_answer from LLM output
│   ├── prompts.py                  # PromptBuilder — section-aware system prompts
│   ├── context_memory.py           # ContextMemory — conversation state
│   ├── section_parser.py           # Inline section switch parser ({office}, /mode heavy_code)
│   ├── diff_utils.py               # Diff operations for str_replace
│   ├── worker.py                   # Background worker thread
│   ├── _helpers.py                 # Internal helpers
│   └── tool_engine/
│       └── _engine.py              # ToolEngine — 30+ tools, sandbox dispatch
├── agent/                         # v2 wrap layer (optional, wraps legacy)
│   ├── __init__.py
│   ├── runtime.py                  # AgentRuntimeV2 — asyncio + SubagentV2
│   ├── guardian.py                 # Corpus-based risk assessment
│   ├── context_fragments.py        # Fragment-based context management
│   ├── actor.py                    # ChatStateActor + CancelToken
│   ├── circuit_breaker.py          # Sliding-window rate-limit protection
│   ├── compaction_v2.py            # Three-tier compaction (intra/inter/code)
│   ├── interjection.py             # Mid-turn user input buffer
│   ├── sandbox.py                  # OS-level sandbox wrapper
│   ├── subagent_v2.py              # Read-only explore/plan sub-agents
│   ├── encrypted_prompt.py         # ChaCha20-Poly1305 prompt encryption
│   ├── acp_server.py               # ACP protocol endpoint
│   ├── native.py                   # clew_native loader + fallback detection
│   └── _fallback_*.py              # Pure-Python fallbacks (5 files)
├── providers/                     # Multi-provider abstraction (16 built-in + custom)
│   ├── registry.py                 # ProviderRegistry — single source of truth
│   ├── base.py                     # Provider, ProviderConfig, ProviderResponse
│   ├── anthropic.py, openai_provider.py, gemini.py  # Major cloud
│   ├── groq.py, deepseek.py, mistral.py, xai.py     # Cloud
│   ├── cerebras.py, together.py, fireworks.py          # Cloud
│   ├── sambanova.py, zai.py, nvidia_nim.py            # Cloud
│   ├── openrouter.py                                      # 200+ gateway
│   ├── ollama.py, lmstudio.py               # Local models
│   ├── openai_compat.py                            # OpenAI-compatible adapter
│   └── custom_providers.py                         # User plugin loader
├── web_bridge/                    # Qt ↔ JS web channel
│   ├── __init__.py
│   ├── bridge.py                  # ClewBridge (QObject, signals → JS)
│   ├── workers.py                   # Background threads
│   └── _paths_config.py
├── web/                           # Embedded HTML5 frontend
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── apple-design.css
├── tests/                         # Test suite (18 files, ~396 test functions)
│   ├── test_g5_agent_identity.py       # Agent identity + audit
│   ├── test_g6_handoff.py              # Handoff bridge
│   ├── test_g9_hook_system.py          # Hook system
│   ├── test_g10_checkpoint.py          # Checkpoint/rewind
│   ├── test_g11_github_automation.py   # GitHub automation
│   ├── test_g13_mcp_server.py          # MCP server mode
│   ├── test_g14_agent_runtime.py       # AgentRuntime core
│   ├── test_g14_providers.py           # Provider system
│   ├── test_g14_sandbox_guardian.py    # Sandbox + Guardian
│   ├── test_g18_web_search.py          # Web search + fetch
│   ├── test_m2_cost_router.py          # Cost-aware routing
│   ├── test_m3_spend_dashboard.py      # Spend dashboard
│   ├── test_g5_agent_identity.py       # Agent identity
│   ├── test_section_switching.py       # Inline section switching
│   ├── test_timeout_and_verification.py # Timeout + verification
│   ├── test_tui_commands.py           # TUI slash commands
│   ├── test_notifier.py               # Notifier system
│   ├── test_daemon.py                  # Daemon HTTP API + SSE
│   └── __init__.py
├── assets/                        # App icons
│   ├── logo.png
│   ├── logo.ico
│   └── logo.icns
├── main_window.py                    # Qt main window
├── app.py                            # Qt application entry point
├── __main__.py                       # `python -m clew` entry point (routes GUI/CLI)
├── cli.py                            # Headless CLI (clew-cli)
├── daemon.py                         # HTTP API + SSE remote daemon (clew-daemon)
├── notifier.py                       # Telegram/Discord/Slack notification bot
├── mcp_server.py                     # MCP server (clew-acp --mcp-server)
├── agent_runtime.py                  # Legacy alias (re-exports clew/agent_runtime)
├── agent_orchestrator.py             # v2 pointer
├── api_server.py                     # Localhost HTTP API server
├── git_service.py / diff_service.py  # Git + diff operations
├── lsp_client.py                     # LSP client for Python (jedi)
├── mcp_client.py / mcp_manager.py    # MCP client infrastructure
├── skill_loader.py                   # SKILL.md loader
├── progressive_tools.py              # Kimi-style progressive tool disclosure
├── slash_commands.py                 # Markdown-based slash command framework
├── memory_service.py                 # Cross-session memory (SQLite)
├── quota.py                          # Daily quota per section
├── activity_log.py                   # First-class audit trail
├── token_tracker.py / token_budget.py # Token tracking + predictable limits
├── auto_router.py / cost_router.py    # Provider selection + cost routing
├── project_context.py / context_manager.py  # Smart context management
├── command_policy.py                 # Sandbox command whitelist
├── auto_updater.py                   # GitHub Releases auto-update check
├── office_worker.py                  # .docx/.xlsx/.pptx generation
├── collaboration.py                  # Team collaboration workspaces
├── code_viewer.py                     # Right-panel code viewer
├── swarm_manager.py                   # Swarm agent orchestration
├── second_opinion.py                 # M1: cross-model review
├── consensus_engine.py               # G15: multi-provider consensus
├── capability_catalog.py              # G7: template catalog
├── learning_loop.py                   # G17: automatic learning loop
├── handoff_bridge.py                  # G6: post-task CMS
├── github_automation.py                # G11: GitHub API integration
├── audit_signing.py                   # G16: Ed25519 signed audit
├── spend_dashboard.py                # M3: team spend aggregation
├── hook_system.py                     # G9: hook manager
├── checkpoint.py                       # G10: checkpoint/rewind
├── web_search_backend.py               # G18: web search
└── session/  loop/  swarm/  compaction/  # Legacy modular components

clew_tui/                             # Terminal UI (Textual) — separate package
├── __init__.py
├── __main__.py                         # python -m clew_tui entry point
├── app.py                              # ClewTUIApp — all slash commands (/mode, /theme, etc.)
├── bridge.py                           # ClewBridge — owns own AgentRuntime
├── smoke_test.py                       # Headless Pilot test for TUI
├── styles_dark.tcss                        # Warm terracotta dark theme
├── styles_light.tcss                       # Warm terracotta light theme
└── widgets/
    ├── __init__.py
    ├── chat_log.py                     # AI messages (pure white), user messages (dashed box), separators
    ├── input_box.py                    # `> ` prefix, dashed border
    ├── status_bar.py                   # Terracotta primary, right-aligned model/tokens/cost/time/mode
    ├── thinking.py                     # Animated spinner + 50 whimsical verbs (terracotta + shimmer)
    ├── tool_block.py                   # Unicode borders, hot pink for bash, lavender for permissions
    ├── approval_modal.py               # Tool execution approval dialog
    ├── verification_modal.py           # Cross-model verification results
    ├── command_palette.py              # Command palette popup
    ├── command_suggestions.py          # /slash auto-complete
    └── guardian_modal.py               # Guardian safety verdict display

clew-native/                            # Optional Rust native (PyO3)
├── Cargo.toml / Cargo.lock             # Cargo workspace
├── pyo3/Cargo.tomnt + src/             # @pymodule clew_native
├── sandbox/                            # Landlock (Linux) / Seatbelt (macOS)
├── circuit_breaker/                    # Sliding-window circuit breaker
├── interjection/                       # Mid-turn user input buffer
├── compaction/                         # Three-tier compaction engine
├── actor/                              # CancelToken + mailbox
├── src/                                # Rust workspace root
└── templates/                                # Rust templates

docs/
├── ARCHITECTURE.md                    # This file
├── CHANGELOG.md
├── CONTRIBUTING.md
├── REFACTORING.md
└── guardian-implementation-status.md
```

## 3. Three entry points (GUI / TUI / CLI)

| Entry | Command | Backend | UI style |
|-------|---------|---------|----------|
| GUI | `clew` or `python -m clew` | Qt PySide6 + QWebEngineView | Desktop native (macOS/Windows/Linux) |
| TUI | `clew_tui` or `python -m clew_tui` | Textual framework | Full-screen terminal |
| CLI | `clew-cli run "prompt"` | argparse + stdout | One-shot, pipeable |
| Daemon | `clew-daemon serve` / `clew-daemon task` | HTTP API + SSE | Remote/background |
| ACP | `clew-acp` | stdio JSON | IDE / LSP integration |
| MCP | `clew-acp --mcp-server` | stdio JSON-RPC 2.0 | Clew as MCP server |

All six entry points share the same `AgentRuntime` core (`clew/agent_runtime/runtime.py`).

## 4. Architecture boundary: `clew_tui` isolation

`clew_tui/` does NOT import from `clew/` directly. It communicates through
`clew_tui.bridge.ClewBridge`, which owns its own `AgentRuntime` instance.
The bridge pattern is:
```
ClewTUIApp → ClewBridge(bridge.py) → AgentRuntime(agent_runtime/runtime.py)
```

Every TUI feature (slash commands, section switching, theme switching, etc.)
has a corresponding method on `ClewBridge`. Widgets never touch the agent runtime.

The GUI web bridge does the same thing through Qt signals:
```
QWebChannel → CleoBridge(web_bridge/bridge.py) → AgentRuntime(agent_runtime/runtime.py)
                      |___ Qt Signals → JS callbacks
```

## 5. Provider system

17 providers registered in `ProviderRegistry`:
- `Anthropic`, `OpenAI`, `Google Gemini` — Tier 1 cloud
- `Groq`, `DeepSeek`, `Mistral`, `xAI Grok` — Fast cloud
- `Cerebras`, `Together`, `Fireworks`, `SambaNova` — CPU cloud
- `z.ai`, `Nvidia NIM` — GLM + NVIDIA
- `OpenRouter` — 200+ model gateway
- `Ollama`, `LM Studio` — local models (zero network)
- `my_local_llm` — user-defined custom provider (plugin system)

Each provider implements a single method: `generate(messages) → ProviderResponse`.
The `AutoRouter` selects the best provider per task based on complexity analysis.
The `CostRouter` (`cost_router.py`) re-scans the AutoRouter picks using budget pressure,
per-complexity USD caps, and provider health.

User-custom providers registered via `~/.clew/providers.yaml` and `.py` files in `~/.clew/ providers/`.

## 6. Tool system

`ToolEngine` (3032 LOC, `clew/agent_runtime/tool_engine/_engine.py`) dispatches ~30 tools:

```
read_file, write_file, write_binary_file, str_replace, apply_diff,  # File I/O
delete_file, rename_file, file_info, undo_write,                        # File ops
execute_command (timeout configurable, default 180s),                 # Shell
run_code (timeout configurable, default 180s),                        # Code eval
list_files, search_project, get_project_structure,                     # Project
git_status, git_diff, git_stage, git_commit, git_log, git_branch,    # Git
self_verify, final_answer,                                            # Control
web_search, web_fetch,                                               # Web (G18)
call_mcp_tool,                                                           # MCP gateway
get_skill, suggest_tools, select_tools,                              # Skills/tools
```

Key v2.1.1 improvements: configurable per-call `timeout` parameter (1-3600s, default 180s,
was 15s in 1.3), deadline-based polling with `[TIMEOUT]` result.

## 7. What changed since v1.3

| Aspect | v1.3 | v2.1.1 |
|-------|------|--------|
| Agent runtime | one 5750 LOC `agent_runtime.py` | Modular: `clew/agent_runtime/` package (runtime, tool_engine, prompts, parser, etc.) |
| Tool engine | `_execute_command()`, `_run_code()` with 15s hard timeout | 30+ tool dispatch, configurable 1-3600s per-call timeout |
| Providers | 15 native + OpenRouter | 16 built-in + user plugins from `~/.clech provers/` |
| Agent Runtime V2 | not exists | `clew/agent/runtime.py` — aio ChatStateActor, CircuitBreaker, Compaction |
| Hook system | not exists | Process-level `HookManager` (pre_tool_, post_tool_, user_prompt_) |
| Checkpoint/rewind | not exists | Auto-checkpoint every turn, manual `/checkpoint save`, `/rewind <n>` |
| Guardian | not exists | Corpus-based non-general safety review (off/dangerous/all) |
| Web search | not exists | `web_search` + `web_fetch` tools, `researcher` read-only sub-agent role |
| Learning loops | not exists | Auto-detect rollback/ CI → inject structured learnings |
| Audit trail | basic `activity_log.py` | + Ed25519 signed hash-chain (`audit_signing.py`) |
| Consensus | not exists |Run same prompt on 2–3 providers → structured divergence report |
| Second Opinion | not exists | Cross-model safety review before approvals |
| Handoff Bridge | not exists | Post-task structured document, editable with revision prompts |
| Cost routing | not exists | Budget-aware re-ranking on top of AutoRouter |
| Spend dashboard | not exists | Team-level aggregation of `token_history.jsonl` |
| MCP server | not exists |Clew as MCP server provider for other agents |
| Github automation | not exists | REST PR/issue/comment API, `. clew cli.' templates |
| Inline section switch | static session param | `{section}` or `/mode section` mid-message |
| TUI | `styles_dark.tcss`, 3 widgets | Full rewrite: warm terracotta palette, whimsical thinking indicator, Unicode tool blocks, `> ` prefix, dashed borders, /theme live switch |
| Tests | ~0% coverage | 18 test files, ~396 test functions across all G and M feature areas |
| CLI | `python -m clem.cli run` | + `status`, `approve-project`, `revoke-project`, chat/heavy-code/office modes |
| Daemon | not exists | `clew-daemon serve` — REST API + SSE, `clew-daemon task` |
| Notifications | not exists | Telegram/Discord/Slack webhook notifications |
| Loop engineering | not exists | Loops_Library.md, Learnings.md, 10 loop patterns, `loops/active/` / `loops /archive/` |

## 8. Graphs – line interactions

```
User input → AgentRuntime.run() → ReAct loop:
  1. section_parser.parse_section_switch() → strip {section} token
  2. PromptBuilder.build() → section-aware system prompt
  3. Provider.generate(messages) → LLM response
  4. OutputParser.extract() → tool_call or final_answer
  5. HookManager.dispatch_pre_tool_use() → may BLOCK or MODIFY
  6. ToolEngine.execute(tool_call)
     → Guardian risk assessment (if enabled)
     → LLM review (if risk above threshold)
     → Confirmation callback (if autonomy="always_ask")
     → Tool dispatch (sandbox → subprocess.Popen)
  7. HookManager.dispatch_post_tool_use() → informational
  8. ActivityLog.record_tool_call() → audit trail (hash chained + signed in GCP)
  9. on_event sink → UI stream (THOUGHT, TOOL_CALLED, TOOL_RESULT)
  10. If final_answer → break | else → back to step 2
```

## 9. Native extension (optional)

```bash
cd clew-native && maturin develop --release -m pyo3/Cargo.toml
```

When not installed, pure- Python fallbacks (`_fallback_*.py`) are functionally equivalent but slower.
The Rust extension accelerates: sandbox I/O, circuit breaker counters, compaction ops.

## 10. Key invariants

- **No telemetry** — ene analytics, crash reporting, usage stats.
- **No `shell=True`** — all `subprocess` uses `shlex.split()` + `shell=False`.
- **Local-first** — Oullama/LM Studio are default; cloud API are opt-in.
- **clew_tui never imports from clew** — all communication through `Clewbridge`.
- **Activity log** — every tool call is audited, SHA-256 fingerprint per entry.
- **Section token stripping** — `{section}` or `/mode section` at message start is parsed and removed before the LLM sees it.