# Clew v2.0 — Refactoring Notes

This document explains the rationale behind the v2.0 refactoring and how
each decision was made. It's intended for future maintainers and contributors.

## 1. Why refactor at all?

Clew v1.3 was a working agent IDE, but the agent runtime was a single
5750-line `agent_runtime.py` with several architectural issues:

1. **Two parallel code paths**. The legacy `_run_agent_loop` and the new
   `run_with_new_architecture` (Kimi-style) coexisted via monkey-patching
   (`agent_orchestrator.patch_runtime`). This made the code hard to reason
   about: which path was actually executing?

2. **String-matching rate-limit detection**. `SubagentBatch._is_rate_limit_error`
   grepped error messages for "rate limit", "429", "throttl" — fragile and
   provider-specific. No real sliding-window tracking.

3. **No kernel-level sandbox**. The workspace sandbox was app-level
   (`_resolve_path` rejects symlinks), easy to bypass via `subprocess.run`.

4. **No mid-turn user input**. The only way to "interrupt" was `stop_generation`,
   which killed the turn entirely.

5. **Broken smoke tests**. The committed `smoke_test_report.json` showed all
   12 tests failing with `TypeError: 'Task' object is not subscriptable`.
   Plus a hardcoded Gemini API key was committed in source.

6. **Single-threaded Python limitations**. Sub-agents used `ThreadPoolExecutor`
   with the explicit admission that *"Python threads can't be force-killed
   safely"* — stragglers kept running detached.

## 2. Why Rust + PyO3 (not full Rust rewrite)?

A full Rust rewrite would have been ideal but:
- The Qt GUI (PySide6) is Python-only. Rewriting the GUI in Rust (eg.
  `egui` or `slint`) would lose the HTML frontend and QWebChannel bridge
  — a hard regression.
- The Office Worker (`python-docx`, `openpyxl`, `python-pptx`) has no
  equivalent Rust crates of comparable quality.
- 16 provider integrations would need rewriting.
- The local-first philosophy (Ollama/LM Studio defaults) leans on Python
  ecosystem tooling.

**Compromise**: keep the GUI, providers, Office Worker, and tool engine
in Python. Move only the **performance-critical and security-critical**
subsystems to Rust:
- Sandbox (must be kernel-level — can't be done in Python)
- Circuit breaker (lock-free atomic fast path)
- Interjection buffer (high-frequency push/drain)
- Compaction engine (three-tier with template prompts)
- CancelToken (atomic propagation)

PyO3 lets the Rust code be called from Python transparently. Maturin
builds the cdylib and installs it as the `clew_native` Python package.
Pure-Python fallbacks exist for every subsystem so the codebase still
works without the native extension.

## 3. What was ported from Grok Build

After studying Grok Build's `crates/` (6046 files), these were identified as
worth porting:

| Grok Build module | Ported to Clew as | Why |
|---|---|---|
| `xai-circuit-breaker` | `clew-native/circuit_breaker/` + `clew/agent/circuit_breaker.py` | Replaces heuristic string-matching rate-limit detection with real sliding-window tracking per (provider, model) key |
| `xai-interjection-core` | `clew-native/interjection/` + `clew/agent/interjection.py` | Mid-turn user input — killer feature for real work |
| `xai-grok-compaction` (three-tier) | `clew-native/compaction/` + `clew/agent/compaction_v2.py` | `intra` / `inter` / `code` strategies, transport-agnostic |
| `xai-grok-workspace/src/sandbox/` | `clew-native/sandbox/` + `clew/agent/sandbox.py` | OS-level kernel sandbox (Landlock/Seatbelt) — irreversible, can't be bypassed by the model |
| `xai-chat-state` (actor) | `clew/agent/actor.py` | asyncio-based ChatStateActor with mpsc command queue, no locks |
| `xai-tool-types::task::BUILTIN_SUBAGENTS` | `clew/agent/subagent_v2.py` | Built-in `explore`/`plan`/`general-purpose` with **toolset-level** read-only guarantee (not dispatch-time) |
| `xai-grok-agent/src/prompt/prompt_encrypted.rs` | `clew/agent/encrypted_prompt.py` | ChaCha20-Poly1305 encrypted prompt templates for enterprise |
| `xai-acp-lib` | `clew/agent/acp_server.py` | ACP server endpoint for IDE integration (Zed, Cursor, etc.) |

## 4. What was NOT ported (and why)

| Grok Build module | Why not ported |
|---|---|
| `xai-computer-hub-*` | Custom JSON-RPC multiplexing hub for cloud topology. Clew is local-first; not needed. |
| `xai-grok-pager` (TUI) | Clew uses Qt GUI, not TUI. |
| `xai-grok-markdown` (5000+ LOC streaming renderer) | Clew renders Markdown in the HTML frontend via QWebEngine. |
| `xai-hunk-tracker` | Clew's diff_service.py already handles this for the GUI; can be revisited later. |
| `xai-grok-mcp` (OAuth + rmcp SDK) | Clew's mcp_client.py uses basic JSON-RPC; full OAuth flow is overkill for v2. |
| `xai-grok-pager-pty-harness` | TUI test harness; not applicable. |
| Vendored mermaid-to-svg | Clew renders diagrams in the HTML frontend. |
| `prod/mc/cli-chat-proxy-types` | Enterprise hosting layer; not part of local-first philosophy. |

## 5. What was preserved UNCHANGED

These are Clew's unique features that Grok Build does not have:
- 16-provider support with AutoRouter (`clew/providers/`)
- Office Worker section (`clew/office_worker.py`)
- Qt GUI + QWebChannel + HTML frontend (`clew/web/`, `clew/web_bridge.py`)
- Slash commands (Markdown-based, `clew/slash_commands.py`)
- Memory service (`clew/memory_service.py`) — human-readable Markdown
  with JSON metadata comments
- LSP client (`clew/lsp_client.py`)
- Git service (`clew/git_service.py`)
- Diff service with hunk accept/reject (`clew/diff_service.py`)
- Activity log as a first-class audit surface (`clew/activity_log.py`)
- Quota tracker (`clew/quota.py`) — per-section daily limits
- Token tracker (`clew/token_tracker.py`) — JSONL ledger with pricing
- Project context (`clew/project_context.py`) — CLAUDE.md/CLEW.md loader
- Command policy (`clew/command_policy.py`) — layered with project trust
- Progressive tools (`clew/progressive_tools.py`) — Kimi-style disclosure
- Auto updater (`clew/auto_updater.py`)
- Skills system (`clew/skill_loader.py`) + plugins (`clew/plugins/`)
- Loop, session, swarm modules (`clew/loop/`, `clew/session/`, `clew/swarm/`)
- Compaction manager (`clew/compaction/`) — v1.3 Full+Micro

The v1.3 `agent_orchestrator.py` monkey-patching bridge is also preserved
unchanged for incremental migration.

## 6. Legacy compat strategy

`clew/agent_runtime.py` is preserved UNCHANGED from v1.3.0 — only the
module docstring was updated to add a "LEGACY" header pointing users to
`clew.agent.AgentRuntimeV2`.

This means:
- `from clew.agent_runtime import AgentRuntime` — still works.
- `from clew.agent.legacy import AgentRuntime` — also works (explicit).
- `from clew.agent import AgentRuntimeV2` — new opt-in path.

The v2 runtime **wraps** a legacy runtime and delegates the actual LLM
call, tool execution, system prompt building, and provider interactions
to the legacy code. This keeps migration incremental: a single v2
runtime call exercises both legacy and v2 code paths.

## 7. Build & install

```bash
# Python-only (uses pure-Python fallbacks)
pip install -e .

# With native acceleration (recommended for production)
pip install -e .
pip install maturin
cd clew-native
maturin develop --release -m pyo3/Cargo.toml
```

To verify native is loaded:
```python
from clew.agent.native import NATIVE_AVAILABLE, native_version
print(NATIVE_AVAILABLE)  # True
print(native_version())   # "0.1.0"
```

## 8. Test status

- **`clew/smoke_tests.py`**: FIXED. Removed hardcoded API key (read from
  `$GEMINI_API_KEY` env var). Fixed `task_result.metadata['tool_calls']`
  bug — tool calls live in `task_result.tool_calls` (List[ToolCall]),
  each `.name` is a `ToolName` enum, not a dict. Tests are skipped (not
  failed) if no API key is set, so CI without credentials doesn't fail.

- **`clew-native/`**: Each Rust crate has unit tests (`cargo test`).
  Run from `clew-native/`:
  ```bash
  cargo test --workspace
  ```

- **v2 Python wrappers**: Have inline doctests and basic sanity checks.
  Full pytest suite is TODO.

## 9. Known limitations of v2.0

1. **`AgentRuntimeV2.run_turn` is not yet wired into the Qt GUI**. The
   legacy runtime remains the default for the desktop app. To use v2 in
   the GUI, modify `clew/main_window.py` to wrap the runtime with
   `AgentRuntimeV2.from_legacy()` at startup.

2. **`clew-acp` server has no reverse-request bridge**. Grok Build's
   `x.ai/mcp/sdk_call` MCP-over-ACP bridge is not implemented. In-process
   MCP servers (via `@tool` / `create_sdk_mcp_server` in the SDK-host
   process) are not supported.

3. **Sandbox seccomp filter for `strict` mode is a TODO**. Landlock
   filesystem restrictions are applied, but the seccomp-based network
   block for `strict` mode is currently advisory only.

4. **`Mailbox::clone` in `clew-actor` is a stub**. The intended usage is
   `Mailbox::unbounded_pair()` which returns (sender, receiver) separately.
   The `clone` impl is left as a placeholder for future use.

5. **Sub-agent v2 still delegates to legacy `_spawn_subagent`**. The v2
   `spawn_subagent()` function calls `runtime.tools._spawn_subagent()` —
   the legacy method — and just sets the role whitelist via
   `set_role_whitelist()`. A full v2 reimplementation of sub-agent
   spawning (with cancellation propagation through CancelToken.child())
   is TODO.

6. **Provider circuit breaker is per (provider, model) only**. MCP
   tool calls don't yet use the circuit breaker; they should use a
   per-(mcp_server, tool) key. TODO.
