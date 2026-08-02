<div align="center">

<img src="./clew/assets/logo.png" alt="Clew Logo" width="180"/>

<br/>

# Clew — Native AI Coding IDE & Agent Runtime

### A free, local-first AI coding tool. **16 providers** · **ReAct agents** · **MCP server** · **Guardian safety** · **Zero telemetry**

**Build with Claude, GPT, Gemini, DeepSeek, Groq, xAI, z.ai, Mistral, or run 100% offline with Ollama / LM Studio.**
<br/>

**Desktop GUI (Qt), terminal UI (Textual), headless CLI, HTTP daemon, and MCP server — your code never leaves your machine unless you choose otherwise.**

<br/>

<h3>

⭐ Star the repo if Clew helps you build better software with AI.

</h3>

[![Stars](https://img.shields.io/github/stars/ilyaosovskoi/Clew?style=social)](https://github.com/ilyaosovskoi/Clew)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-blue.svg)]()

</br>

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![Textual](https://img.shields.io/badge/TUI-Textual-purple?style=for-the-badge)](https://textual.textualize.io/)
[![Qt](https://img.shields.io/badge/Qt-PySide6-green?style=for-the-badge&logo=qt)](https://www.qt.io/)
[![Privacy](https://img.shields.io/badge/Privacy-Local--First-orange?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)]()

<br/>

**[Architecture](docs/ARCHITECTURE.md)** · **[CHANGELOG](docs/CHANGELOG.md)** · **[Contributing](docs/CONTRIBUTING.md)**

</div>

<br/>

<div align="center">

## The Promise

</div>

> **Clew is a full self-serve AI coding environment: desktop GUI, terminal TUI, headless CLI, and remote daemon — all sharing one agent runtime.**
> **16 built-in providers, plus user-defined custom providers and 200+ more via OpenRouter. No telemetry. Absolute privacy for local models, with the flexibility to plug in cloud APIs.**

<table>
  <tr>
    <td width="33%" valign="top"><b>Privacy-First</b><br/><sub>Local models run 100% on-device. No analytics, no crash reports, no cloud unless you opt in.</sub></td>
    <td width="33%" valign="top"><b>16 AI Providers</b><br/><sub>Anthropic, OpenAI, Gemini, DeepSeek, Groq, xAI, z.ai, Mistral, Nvidia NIM, and more, or run Llama/Mistral/Qwen locally via Ollama or LM Studio.</sub></td>
    <td width="33%" valign="top"><b>Multi-Frontend</b><br/><sub>Qt desktop app, Textual TUI, headless CLI, HTTP daemon with SSE, messenger control (Telegram/Discord/Slack), and MCP server.</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><b>ReAct Agent Runtime</b><br/><sub>Autonomous agents that plan, read, write, run code, test, and self-verify until the task is done.</sub></td>
    <td width="33%" valign="top"><b>MCP Protocol</b><br/><sub>Clew can BE an MCP server for other agents and CONSUME external MCP tools.</sub></td>
    <td width="33%" valign="top"><b>Smart Context</b><br/><sub>Git-aware filtering, relevance ranking, token budget, live project indexing, tombstone compaction.</sub></td>
  </tr>
</table>

<br/>

<div align="center">

# What Makes Clew Different

</div>

| Feature | Clew | Cursor / Cline | Windsurf | Claude Desktop |
|----------|------|----------------|----------|----------------|
| **Desktop GUI** | Qt/PySide6, offline-capable | Web/Electron | Electron | Electron |
| **Terminal UI (TUI)** | Textual, full-featured, warm terracotta theme | No | No | No |
| **Local-first privacy** | Zero telemetry, 100% offline local-model mode | Partial | Partial | Cloud-only |
| **Autonomous agents** | ReAct loop with structured JSON tool calling | Yes | Yes | Limited |
| **Provider count** | 16 built-in + user-custom + 200+ via OpenRouter | 5–10 | 35 | 1 (Anthropic) |
| **Guardian safety** | Rule-based risk assessment + optional LLM review | No | No | No |
| **MCP server** | Clew as an MCP tool provider for other agents | Plugin | No | No |
| **Hook system** | Per-tool pre/post hooks (block/modify/allow) | No | No | No |
| **Checkpoint/rewind** | Auto-snapshot conversations + files, rewind to any point | No | No | No |
| **Consensus engine** | Run the same task on 2–3 providers in parallel, diff output | No | No | No |
| **Signed audit trail** | Ed25519 signatures + hash chaining, zero-cloud | No | No | No |
| **Learning loop** | Auto-detects rollbacks/CI failures → creates learnings | No | No | No |
| **Web search** | `web_search` + `web_fetch`, read-only `researcher` sub-agent role | Yes | Yes | Yes (preview) |
| **Cost-aware routing** | Budget-aware provider/model selection | No | No | No |
| **Smart task decomposition** | Cheap model splits a task into subtasks, routes each to the best-fit model | No | No | No |
| **Handoff bridge** | Post-task review UI with editable blocks and revision prompts | No | No | No |
| **Daemon mode** | REST API + SSE, background task queue, inbound messenger control | No | No | No |
| **Autonomous "Hermes" mode** | Sandboxed workspace + plan-driven autonomy + Telegram control | No | No | No |
| **Inline section switch** | `{general}`, `{heavy_code}`, `{office}` message prefixes, or `/mode` | No | No | No |
| **Configurable timeouts** | Per-call `timeout` (1–3600s, default 180s) | No | No | No |
| **Rust native acceleration** | Sandbox, circuit breaker, compaction — via PyO3 (optional) | No | No | No |
| **Open source** | MIT | Proprietary | Proprietary | Proprietary |

<br/>

<div align="center">

# AI Providers — 16 Built-in · 200+ via OpenRouter

</div>

> **Any model, same UI. Switch providers mid-conversation, compare outputs, route by cost.**

### Cloud Providers — 16 built-in

| Anthropic | OpenAI | Google Gemini | DeepSeek | Groq | xAI (Grok) |
|-----------|--------|----------------|----------|------|------------|
| Mistral | Cerebras | Together | Fireworks | SambaNova | z.ai (GLM) |
| Nvidia NIM | OpenRouter (200+ models) | | | | |

### Local Models — 100% Private, Zero Network

- **Ollama** — run Llama 3.x, Mistral, Qwen 2.5, DeepSeek Coder locally
- **LM Studio** — GGUF model server with GPU acceleration

### Custom Providers

Users can register their own providers via:
- `~/.clew/providers.yaml` — config-file declaration
- `clew/providers/` — dynamic class loading (plugin-style)

Auto-registers in `ProviderRegistry` and appears in every frontend (GUI, TUI, CLI).

<br/>

<div align="center">

# Quick Start

</div>

**Download** the latest release for your platform from [Releases](https://github.com/ilyaosovskoi/Clew/releases) — `Clew.dmg` (macOS), `Clew-Setup.exe` (Windows), or `Clew.AppImage` (Linux). Requires Python 3.11+.

**Configure** — open Settings, add an API key (Anthropic / OpenAI / Groq / Gemini / DeepSeek / z.ai / etc.), or install [Ollama](https://ollama.ai) / [LM Studio](https://lmstudio.ai) for local models — Clew auto-detects them.

**Launch**:
```bash
clew                          # Desktop GUI (Qt)
clew_tui                      # Terminal UI (Textual)
clew-cli run "build an API"   # One-shot headless run
clew-daemon serve             # REST API + SSE daemon (port 8765)
clew-acp --mcp-server         # MCP server for other agents
```

**From source:**
```bash
git clone https://github.com/ilyaosovskoi/Clew.git
cd Clew                       # main is the current stable line
python3 -m venv .venv && source .venv/bin/activate
pip install -e .              # core + TUI
clew                          # Qt GUI (requires PySide6)
clew_tui                      # TUI (requires textual)
clew-cli run "prompt"         # CLI (zero extra deps)
```

> `main` tracks the current stable release. Newer, in-progress work (web UI, expanded benchmarks, upcoming features) lands on separate feature branches before merging back — check [branches](https://github.com/ilyaosovskoi/Clew/branches) if you want to try what's next.

**Install Rust (optional, for native acceleration):**
```bash
cd clew-native && maturin develop --release
# Python fallbacks provide all functionality; Rust adds native performance
# for the sandbox, circuit breaker, and context compaction.
```

<br/>

<div align="center">

# Features

</div>

### Autonomous Agent Runtime

The ReAct (Reasoning + Acting) agent loop:

1. **Plan** — analyze the task, decide what to read, what files are likely relevant
2. **Read** — open relevant files, gather context
3. **Write / Edit** — create or modify code using `str_replace`, `write_file`, `apply_diff`
4. **Run & Verify** — execute tests, check builds, call `self_verify`
5. **Finalize** — report what changed and what was verified

**Tool set (30+ tools), including:**
`read_file`, `write_file`, `str_replace`, `apply_diff`, `execute_command`, `run_code`,
`git_status`, `git_diff`, `git_stage`, `git_commit`, `search_project`, `grep`, `glob`, `list_files`,
`web_search`, `web_fetch`, `call_mcp_tool`, `spawn_subagent`, `spawn_multi_agents`, `self_verify`, `final_answer`

**Configurable autonomy:**
- `always_ask` — confirm every write/execute before it runs
- `new_files_only` (default) — only new files require confirmation
- `never_ask` — no interactive confirmation (Guardian's own risk checks still run regardless of this setting)

**Sandboxed execution:**
- Command allow-list (`python`, `git`, `npm`, `cargo`, `pytest`, etc.)
- Dangerous shell metacharacters and interpreter-escape flags are blocked
- Project-level `.clew/commands.json` for explicit, human-approved command allow-listing
- Per-call `timeout` parameter: 1–3600s (default 180s) — long builds/installs/tests are no longer killed early
- Optional OS-kernel-level workspace sandbox (Landlock on Linux, Seatbelt on macOS) restricting the agent to a single directory — used by Hermes mode (see below)

### Three Sections (Modes)

| Section | Description | Typical tools | Default max iterations |
|---------|-------------|---------------|------------------------|
| **General** (default) | Standard coding, bug fixing, feature implementation | All 30+ tools | 8 |
| **Heavy Code** | Multi-agent, large codebase refactors | Full set + `spawn_subagent` / `spawn_multi_agents` | 30 |
| **Office** | `.docx`, `.xlsx`, `.pptx` generation | File + office tools | 8 |

Switch modes inline without leaving the conversation: start a message with `{general}`, `{heavy_code}`, or `{office}`, or use the `/mode office` slash command — the choice persists as the session default until changed again.

### Guardian — Safety Review

Three levels:
- `off` — disabled (default)
- `dangerous_only` — high-risk calls get a provider-level review before execution
- `all` — medium+ risk calls are reviewed

Verdict: **ASSURE** / **MODIFY** / **REJECT** — MODIFY proposes safer alternative arguments. Risk assessment runs identically regardless of the agent's autonomy setting — `never_ask` only skips the interactive confirmation prompt, never the underlying safety check.

### Web Search & Internet Reach

- `web_search(query)` — MCP-first routing (no hardcoded paid API required), with ordered fallback across configured backends
- `web_fetch(url)` — safe HTML-to-text extraction; rejects non-http(s) URLs and URLs containing secret-shaped or suspiciously long encoded query parameters
- Read-only `researcher` sub-agent role — can search/fetch but never write, execute, or touch git, so content from an untrusted page can't be used to trigger unrelated actions
- Fetched content is tagged and compacted the same way large file reads are, so it doesn't permanently bloat the conversation

### Smart Task-Decomposition Router

Beyond simple cost-aware routing, Clew can split a task into subtasks and route each one to whichever configured model fits it best:

1. A cheap model analyzes the incoming task and proposes a subtask breakdown
2. Each subtask is matched against a per-model specialty description (e.g. "strong at algorithmic reasoning", "best for large-context refactors", "cheap and fast for boilerplate") plus your budget
3. Subtasks dispatch to sub-agents, each optionally running on a different provider/model, in parallel where independent
4. Results are merged into one coherent answer

Falls back to single-model routing automatically if decomposition isn't confident or would exceed budget. Override the built-in specialty catalog via `~/.clew/model_capabilities.json`.

### Hook System

Process-level `HookManager` singleton:
- `pre_tool_use` — BLOCK or MODIFY tool calls before execution
- `post_tool_use` — audit/log after execution
- `user_prompt_submit` — BLOCK or MODIFY prompts before they reach the LLM

Write hooks in `~/.clew/hooks/*.py` — auto-loaded at startup. Thread-safe (RLock + snapshot pattern). Config at `~/.clew/hooks.json`.

### Checkpoint / Rewind

- Auto-checkpoint after every agent turn (toggleable)
- Manual checkpoint via `/checkpoint save [label]`
- Rewind via `/rewind <n>` restores files from backup and rewinds conversation position
- File backups in `~/.clew/checkpoints/<session>/backups/<cp-id>/`, SHA-256 checksummed
- Max 200 checkpoints per session (oldest evicted)

### Multi-Provider Consensus

Run the same task on 2–3 providers in parallel (`ThreadPoolExecutor`) → structured divergence report:
- Similarity scoring between the approaches
- Files touched, code volume, and explanation-length differences per provider
- Configurable: which providers, minimum agreement threshold, per-provider timeout

### Signed Audit Trail

- Ed25519 key pair generated on first use (`~/.clew/audit_key`, `chmod 0600`)
- Every audit entry's signature covers its payload plus the previous entry's hash
- Tampering, reordering, or deletion is detectable — verification recomputes the hash chain from scratch
- Zero-cloud — keys never leave your machine

### Automatic Learning Loop

- Auto-detects: git rollbacks (`reset --hard`), force-pushes, `git revert`, abandoned branches, CI/test failures, and tasks completed without calling `self_verify`
- On trigger: auto-creates a `learnings/` entry following the project's `Learnings.md` template, scoped per repository
- Learnings are injected into the system prompt through the same compaction/fragment system as everything else, so they don't permanently bloat the context
- Dismissed learnings stop being injected

### Layered Memory

- **Task canvas** — a compact, always-fresh graph of the current task's subtasks/steps (status: pending/running/done/failed), rendered live in the TUI sidebar; full detail for any step stays reachable on disk without bloating the prompt
- **Persona memory** — a small, size-capped, cross-project profile (`~/.clew/persona.md`) of how *you* like to work, distinguished from the per-repository learning loop above; updated incrementally by a cheap model call, editable via `/persona`

### Post-Task Handoff

- Parses agent output into a structured document with typed blocks (text/code/file_diff/todo/note)
- Each block: stable ID, mutable status (pending/approved/rejected/edited)
- Mark blocks for revision → compiles your edits into a structured follow-up prompt
- Markdown export; persists to `~/.clew/handoffs/<id>.json`

### Capability Catalog / Templates

- Task templates across common categories, with placeholder substitution
- Managed via the `/capability` slash command
- User-global (`~/.clew/capabilities/`) and project-level (`.clew/capabilities/`) overrides

### Token Efficiency & Budget

- Hard cost caps — stop at a configured dollar amount
- Per-turn efficiency tracking
- Cost-aware provider routing (see below) automatically prefers cheaper or local models under budget pressure
- Aggregate spend reporting (see Team Spend below)

### Cost-Aware Provider Routing

- Re-ranks `AutoRouter`'s picks using budget pressure, per-call USD caps, and provider health
- Automatically prefers free/local providers (Ollama, LM Studio) as the budget fills up

### Team Spend

- Aggregates token usage into a spend report (total, by user, by provider, by model, by day)
- Local user identity: `~/.clew/identity.json`
- Team budget config: `~/.clew/team_budget.json`
- CSV/JSON export, zero-cloud

### Skills System (`SKILL.md`)

- YAML front-matter: name, description, tags, activation criteria
- Multi-location: project-level (`.clew/skills/`) and global (`~/.clew/skills/`)
- Agent requests the full skill text on demand via the `get_skill` tool, so skills don't bloat every prompt
- Ships with a built-in `web-research` skill

### Context

- Git-aware filtering: ignores `.git/`, `node_modules/`, `__pycache__/`, `venv/`, and other common noise directories
- Token budget: context is continuously fit to the active model's window
- Live indexing: file changes are detected and re-indexed as you work

### MCP (Model Context Protocol) Support

- Clew as **client**: connect any MCP server's tools — they appear directly in the agent's tool catalog
- Clew as **server**: other agents can call Clew as an MCP provider (JSON-RPC 2.0 over stdio)
  - Read-only mode (default): `clew-acp --mcp-server`
  - Write mode (additional tools): `clew-acp --mcp-server --mcp-server-writes`
  - Restrict the exposed tool set with `--tools`

### Terminal UI (TUI)

- Warm terracotta palette with a soft pink accent
- Animated "thinking" indicator with rotating status verbs
- Distinct styling for shell/execution output vs. permission prompts vs. plain chat
- Compact status bar: model, tokens, section, elapsed time
- Live `/theme dark|light` switching
- Live task-canvas sidebar (see Layered Memory above)

### Daemon Mode — Background Execution

- `clew-daemon serve` — REST API + SSE event stream (default port 8765)
- `clew-daemon task "prompt" --notify telegram` — one-shot run with outbound notification
- Bearer-token authentication, configurable queue limits
- Outbound Telegram/Discord/Slack notifications on task progress/completion

### Autonomous "Hermes" Mode

For hands-off, remote-controlled operation:
```bash
clew hermes --workspace ./my-project --telegram-token <token> --allow <chat_id>
```
Bundles, in one command:
- An OS-kernel-level sandbox restricting the agent to the given workspace directory
- `never_ask` autonomy with plan-driven execution (a plan is still produced for each task, it just isn't blocked on a GUI click)
- An inbound Telegram listener (mandatory allow-list — no wildcard "anyone can message it" mode) that turns incoming messages into queued tasks
- A `STOP` keyword that cancels the currently running task
- Outbound progress/completion messages back to the same chat
- Every action taken from a remote message is tagged in the signed audit trail

Guardian's safety checks are never bypassed by this mode — the workspace sandbox is the outer, kernel-enforced backstop; Guardian's own risk assessment is the inner one, and it runs unconditionally.

### Remote Control via Messengers

Beyond Hermes mode, the outbound notifier alone can be used to keep tabs on background/daemon tasks over Telegram, Discord, or Slack without adopting full autonomous mode.

### CLI — Headless

- `clew-cli run "prompt"` — non-interactive, no GUI dependencies
- `clew-cli chat "what does prime.py do?"` — single question
- `clew-cli heavy-code "refactor across 5 files..."` — multi-agent mode
- `clew-cli office "generate a quarterly report from data.xlsx"` — office-document mode
- Pipeable output: `clew-cli run "what is this project?" > spec.txt`

<br/>

<div align="center">

# Current Status

</div>

### Completed

- [x] 16 built-in providers + user-configured custom providers + OpenRouter (200+ models)
- [x] Four entry points sharing one agent runtime: GUI (Qt), TUI (Textual), CLI, daemon
- [x] 30+ agent tools: read/write/str_replace/apply_diff, execute, git, web, MCP
- [x] Guardian safety review (`dangerous_only`, `all` levels)
- [x] MCP client + MCP server modes
- [x] Multi-provider "Second Opinion" before commit
- [x] Multi-provider consensus engine (parallel run + divergence report)
- [x] Signed, hash-chained audit trail (Ed25519, zero-cloud)
- [x] Web search + web fetch, read-only `researcher` sub-agent role
- [x] Automatic learning loop (rollback/CI-failure detection → per-repo learnings)
- [x] Post-task handoff bridge
- [x] Hook system (pre/post tool use, prompt submit)
- [x] Checkpoint / rewind
- [x] GitHub integration
- [x] Capability catalog / templates
- [x] Cost-aware provider routing + team spend reporting
- [x] Smart task-decomposition router (subtask-level model routing)
- [x] Layered memory: live task canvas + cross-session persona profile
- [x] Autonomous "Hermes" mode (sandboxed workspace + inbound messenger control)
- [x] Inline section switching (`{general}` / `{heavy_code}` / `{office}` / `/mode`)
- [x] Configurable per-call timeouts (1–3600s, default 180s)
- [x] TUI visual overhaul
- [x] ~800+ automated tests

### Planned

- [ ] Browser-based web UI (localhost) as an alternative to the Qt desktop GUI
- [ ] Expanded internal benchmark suite for regression tracking across releases
- [ ] Monetization features (building on the existing cost-routing and team-spend groundwork) — deferred until the current feature set stabilizes and the user base grows

<br/>

<div align="center">

# Architecture

</div>

```
clew/                       Core package
├── agent_runtime/          ReAct agent loop: ToolEngine, prompt building, section parser
├── agent/                  Guardian, sandbox, checkpoints, task canvas, persona memory
├── providers/              16 built-in providers + registry + auto-router
├── web_bridge/             Qt <-> agent runtime bridge (@Slot methods)
└── tests/                  Test suite

clew_tui/                   Terminal UI (separate package, shares the agent runtime via a bridge)
├── widgets/                ChatLog, InputBox, StatusBar, ThinkingIndicator, ToolBlock,
│                           TaskCanvasView, Approval/Verification modals
└── styles_dark.tcss / styles_light.tcss

clew-native/                Optional Rust acceleration (PyO3): sandbox, circuit breaker, compaction
```

<br/>

# Technology Stack

- **Python 3.11+**
- **Qt / PySide6** — desktop GUI
- **Textual** — terminal UI
- **SQLite** — chat history, settings, memory, session state
- **Rust (PyO3)** — optional native acceleration for sandboxing, circuit breaking, and context compaction
- **Git** (subprocess) — status, diff, stage, commit, branch — no external dependency
- **MCP (Model Context Protocol)** — client and server support

**Zero required runtime dependencies beyond Python 3.11+.** A Rust toolchain is optional, for native-performance builds only.

<br/>

## Contributing

- **Fork** the repository
- **Clone**: `git clone https://github.com/ilyaosovskoi/Clew.git`
- **Branch**: `git checkout -b feature/amazing-feature`
- **Commit**: `git commit -m "Add amazing thing"`
- **Push**: `git push origin feature/amazing-feature`
- **Pull request** — see [Issues](https://github.com/ilyaosovskoi/Clew/issues) for open items

**Author:** Ilya Osovskoi

**Contact & links:**
- GitHub: [github.com/ilyaosovskoi/Clew](https://github.com/ilyaosovskoi/Clew)
- Issues: [github.com/ilyaosovskoi/Clew/issues](https://github.com/ilyaosovskoi/Clew/issues)
- Releases: [github.com/ilyaosovskoi/Clew/releases](https://github.com/ilyaosovskoi/Clew/releases)

## License

MIT — free to use in any project.

---
