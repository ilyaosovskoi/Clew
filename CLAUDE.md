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