# Clew v2.0 — Architecture

This document describes the v2.0 architecture of Clew after the major refactoring
that introduced Rust native acceleration, the new `clew.agent` v2 runtime package,
and features ported from Grok Build.

## 1. High-level diagram

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Clew v2.0 process                              │
│                                                                         │
│  ┌────────────────────────────┐      ┌────────────────────────────┐    │
│  │   Qt GUI (PySide6)         │      │   Headless CLI             │    │
│  │   - QWebEngineView         │      │   - clew-cli               │    │
│  │   - QWebChannel bridge     │      │   - clew-acp (ACP server)   │    │
│  │   - ClewMainWindow         │      │                            │    │
│  └─────────────┬──────────────┘      └─────────────┬──────────────┘    │
│                │                                   │                    │
│                └──────────────┬────────────────────┘                    │
│                               ▼                                          │
│                  ┌─────────────────────────────┐                         │
│                  │   clew.agent (v2)           │ ← opt-in                │
│                  │   ┌─────────────────────┐   │                         │
│                  │   │ AgentRuntimeV2      │   │                         │
│                  │   │  - asyncio loop    │   │                         │
│                  │   │  - ChatStateActor  │   │                         │
│                  │   │  - InterjectionBuf │   │                         │
│                  │   │  - Compaction v2   │   │                         │
│                  │   │  - CircuitBreaker  │   │                         │
│                  │   │  - SubagentV2      │   │                         │
│                  │   └──────────┬─────────┘   │                         │
│                  │              │ wraps       │                         │
│                  │   ┌──────────▼─────────┐   │                         │
│                  │   │ legacy AgentRuntime│   │ ← unchanged             │
│                  │   │  (5750 LOC)        │   │                         │
│                  │   │  - ToolEngine     │   │                         │
│                  │   │  - ContextMemory  │   │                         │
│                  │   │  - PromptBuilder   │   │                         │
│                  │   └──────────┬─────────┘   │                         │
│                  └──────────────┼─────────────┘                         │
│                                 │                                         │
│                                 ▼                                         │
│                  ┌─────────────────────────────┐                         │
│                  │   clew_native (Rust)         │ ← optional              │
│                  │   - sandbox (Landlock/Seat)  │                         │
│                  │   - circuit_breaker          │                         │
│                  │   - interjection             │                         │
│                  │   - compaction               │                         │
│                  │   - actor (CancelToken)      │                         │
│                  └─────────────────────────────┘                         │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │  Unchanged Clew-unique modules                              │       │
│  │  - providers/ (16 providers + AutoRouter)                   │       │
│  │  - office_worker.py (.docx/.xlsx/.pptx generation)          │       │
│  │  - slash_commands.py (Markdown-based)                       │       │
│  │  - memory_service.py (clew_memory.md with JSON metadata)   │       │
│  │  - mcp_client.py / mcp_manager.py                           │       │
│  │  - lsp_client.py / git_service.py / diff_service.py         │       │
│  │  - skill_loader.py / plugins/                               │       │
│  │  - quota.py / activity_log.py / token_tracker.py            │       │
│  │  - auto_updater.py / project_context.py / command_policy.py │       │
│  └──────────────────────────────────────────────────────────────┘       │
└────────────────────────────────────────────────────────────────────────┘
```

## 2. Directory layout (new and changed)

```
clew/
├── agent/                          # NEW in v2.0
│   ├── __init__.py                 # Public API exports
│   ├── runtime.py                  # AgentRuntimeV2 — main entry point
│   ├── actor.py                    # asyncio ChatStateActor + CancelToken
│   ├── interjection.py             # InterjectionBuffer wrapper
│   ├── sandbox.py                  # Sandbox wrapper
│   ├── circuit_breaker.py          # CircuitBreaker wrapper
│   ├── compaction_v2.py            # Three-tier compaction
│   ├── subagent_v2.py              # Built-in explore/plan/general-purpose
│   ├── encrypted_prompt.py         # ChaCha20-Poly1305 prompt encryption
│   ├── acp_server.py               # ACP endpoint (clew-acp command)
│   ├── native.py                   # Loader for clew_native (with fallback)
│   ├── legacy/
│   │   └── __init__.py              # Re-exports legacy AgentRuntime
│   ├── _fallback_sandbox.py        # Pure-Python sandbox fallback
│   ├── _fallback_circuit_breaker.py
│   ├── _fallback_interjection.py
│   ├── _fallback_compaction.py
│   └── _fallback_actor.py
├── agent_runtime.py                # CHANGED: legacy header added (file otherwise unchanged)
├── agent_orchestrator.py           # CHANGED: v2 pointer added
├── smoke_tests.py                  # FIXED: env var for API key + Task subscript bug
├── providers/                      # UNCHANGED — multi-provider support
├── office_worker.py                # UNCHANGED — Office Worker
├── slash_commands.py               # UNCHANGED — slash commands
├── memory_service.py               # UNCHANGED — Markdown memory
├── mcp_client.py / mcp_manager.py  # UNCHANGED — MCP integration
├── lsp_client.py                   # UNCHANGED
├── git_service.py / diff_service.py# UNCHANGED
├── skill_loader.py / plugins/      # UNCHANGED
├── quota.py / activity_log.py / token_tracker.py  # UNCHANGED
├── auto_updater.py / project_context.py / command_policy.py  # UNCHANGED
├── progressive_tools.py            # UNCHANGED — Kimi-style progressive disclosure
├── loop/  session/  swarm/         # UNCHANGED — v1.3 modular components
├── compaction/                     # UNCHANGED — v1.3 compaction manager
├── web/  web_bridge.py             # UNCHANGED — Qt/HTML bridge
├── api_server.py                   # UNCHANGED — localhost HTTP API
├── main_window.py / app.py         # UNCHANGED — Qt main window
└── __main__.py / cli.py            # UNCHANGED — entry points

clew-native/                        # NEW in v2.0 — Cargo workspace
├── Cargo.toml                      # workspace manifest
├── sandbox/                        # Landlock (Linux) / Seatbelt (macOS)
│   ├── Cargo.toml
│   └── src/lib.rs
├── circuit_breaker/                # Sliding-window circuit breaker
│   ├── Cargo.toml
│   └── src/lib.rs
├── interjection/                   # Mid-turn user interjection buffer
│   ├── Cargo.toml
│   └── src/lib.rs
├── compaction/                     # Three-tier compaction engine
│   ├── Cargo.toml
│   └── src/lib.rs
├── actor/                          # CancelToken + Mailbox helpers
│   ├── Cargo.toml
│   └── src/lib.rs
└── pyo3/                           # PyO3 bindings (cdylib)
    ├── Cargo.toml
    └── src/
        ├── lib.rs                  # @pymodule clew_native
        ├── sandbox.rs              # clew_native.sandbox submodule
        ├── circuit_breaker.rs      # clew_native.circuit_breaker submodule
        ├── interjection.rs         # clew_native.interjection submodule
        ├── compaction.rs           # clew_native.compaction submodule
        └── actor.rs                # clew_native.actor submodule
```

## 3. v2 vs v1.3 — what changed

| Aspect | v1.3 | v2.0 |
|---|---|---|
| Agent runtime | single 5750 LOC `agent_runtime.py` | `clew.agent.AgentRuntimeV2` (wraps legacy, adds v2 features) |
| State ownership | `ContextMemory` (Python class, list+lock) | `ChatStateActor` (asyncio task, mpsc queue, no locks) |
| Cancellation | `threading.Event` | `CancelToken` (AbortSignal-pattern, native Rust or fallback) |
| Compaction | `FullCompaction` + `MicroCompaction` | + intra/inter/code from Grok Build |
| Rate-limit handling | string-matching heuristic | Real sliding-window circuit breaker per (provider, model) |
| Sandbox | app-level `_resolve_path` | + OS-level kernel sandbox (Landlock/Seatbelt, irreversible) |
| Sub-agents | role-based whitelist at dispatch time | + toolset-level read-only guarantee (`explore`/`plan` literally have no bash/write tools in toolset) |
| User mid-turn input | `stop_generation` only | + InterjectionBuffer with FIFO drain at safe points |
| IDE integration | None | ACP server endpoint (`clew-acp` command) |
| Enterprise | None | + EncryptedPromptStore (ChaCha20-Poly1305) |
| Multi-provider | 16 providers + AutoRouter | UNCHANGED |
| Office Worker | .docx/.xlsx/.pptx | UNCHANGED |
| Qt GUI + web bridge | PySide6 + QWebEngineView | UNCHANGED |
| Local-first | Ollama/LM Studio default | UNCHANGED |
| Smoke tests | Broken (Task subscript bug + hardcoded API key) | FIXED (env var + correct tool_calls access) |

## 4. Native extension build process

```bash
# 1. Install maturin
pip install maturin

# 2. Build & install the clew_native extension
cd clew-native
maturin develop --release -m pyo3/Cargo.toml

# 3. Verify
python -c "from clew.agent.native import NATIVE_AVAILABLE; print('native:', NATIVE_AVAILABLE)"
# Should print: native: True
```

Without this step, `NATIVE_AVAILABLE` is `False` and pure-Python fallbacks
are used. The fallbacks are functionally equivalent but slower for
high-throughput MCP / multi-provider scenarios.

## 5. Migration guide

### 5.1. For application code

Replace:
```python
from clew.agent_runtime import AgentRuntime

runtime = AgentRuntime(provider=..., workspace=...)
result = runtime.run(task)
```

With:
```python
import asyncio
from clew.agent_runtime import AgentRuntime  # legacy still works
from clew.agent import AgentRuntimeV2

legacy_runtime = AgentRuntime(provider=..., workspace=...)
runtime = AgentRuntimeV2.from_legacy(legacy_runtime)
result = asyncio.run(runtime.run_turn("your prompt"))
```

### 5.2. For sub-agent callers

Replace:
```python
runtime.tools._spawn_subagent(task=prompt, role="explore")
```

With:
```python
from clew.agent import spawn_subagent
handle = spawn_subagent(runtime, "explore", prompt)
# `explore` subagent has NO bash/write_file in its toolset — read-only is
# enforced at the toolset schema level, not at dispatch time.
```

### 5.3. For mid-turn user input

Replace:
```python
runtime.tools.cancel("user pressed stop")
```

With:
```python
runtime.push_interjection("please also consider edge case X")
# The next safe-point in the agent loop will drain the interjection and
# inject it as a synthetic user message. The model decides how to weigh it.
```

### 5.4. For sandboxed execution

Add to startup (before any untrusted code runs):
```python
from clew.agent import apply_sandbox, SandboxProfile

apply_sandbox(
    profile=SandboxProfile.WORKSPACE,
    workspace_root="/path/to/project",
    extra_readwrite_paths=["~/.clew"],  # for memory log
    allowed_egress=["api.openai.com:443"],
)
# Irreversible — kernel-level enforcement (Landlock on Linux, Seatbelt on macOS)
```
