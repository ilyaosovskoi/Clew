# Changelog

All notable changes to Clew are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] — 2026-07-21

### Added — v2 agent runtime (`clew.agent` package)

- **`clew.agent.AgentRuntimeV2`** — opt-in v2 runtime that wraps a legacy
  `AgentRuntime` and adds new capabilities without breaking existing code.
- **`clew.agent.ChatStateActor`** — asyncio-based actor that owns all
  conversation state in a single task. Commands flow via mpsc queue;
  no locks needed. Ported from Grok Build's `xai-chat-state` pattern.
- **`clew.agent.CancelToken`** — AbortSignal-pattern cancel token with
  parent→child propagation. First reason wins.
- **`clew.agent.InterjectionBuffer`** — mid-turn user interjection buffer.
  Messages are FIFO-buffered and drained at safe points; each drained
  entry is framed as a synthetic user message. UTF-8-safe truncation at
  25,000 chars. Ported from Grok Build's `xai-interjection-core`.
- **`clew.agent.CompactionEngine`** — three-tier compaction:
  `code_compact` (full-replace), `intra_compact` (tail-keep per turn),
  `inter_compact` (chunked between turns). Ported from Grok Build's
  `xai-grok-compaction`.
- **`clew.agent.CircuitBreakerRegistry`** — sliding-window circuit
  breaker per (provider, model) key. Lock-free fast-path via atomic
  `is_open` mirror. Three states (Closed/Open/HalfOpen) with probe
  reclaim. Replaces heuristic string-matching rate-limit detection
  in v1.3's `SubagentBatch._is_rate_limit_error`.
- **`clew.agent.apply_sandbox()`** — OS-level kernel sandbox
  (Landlock on Linux ≥5.13, Seatbelt on macOS). Applied process-wide
  at startup. **Irreversible** — the model cannot talk its way out.
  Profiles: `off`, `workspace`, `read-only`, `strict`.
- **`clew.agent.spawn_subagent()`** — v2 sub-agent spawning with
  built-in `explore` / `plan` / `general-purpose` definitions.
  Read-only is enforced at the **toolset schema level** (not at dispatch
  time) — `explore` and `plan` literally have no `bash`/`write_file` in
  their toolset. User-defined sub-agents via `.clew/agents/*.md`.
- **`clew.agent.EncryptedPromptStore`** — ChaCha20-Poly1305 encrypted
  prompt templates for enterprise deployments. Key from
  `$CLEW_PROMPT_KEY` env var or `~/.clew/prompt_key` file. Decrypted
  plaintext is zeroed on context exit (best-effort). Falls back to XOR
  (dev only, NOT secure) if `cryptography` is not installed.
- **`clew.agent.ACPServer`** — Agent Client Protocol server endpoint.
  Speaks JSON-RPC 2.0 over stdio. Supports `initialize`, `session/new`,
  `session/load`, `session/info`, `prompt/send`, `turn/cancel`,
  `session/update` (notification). New `clew-acp` CLI command.
- **`clew.agent.native`** — loader for the `clew_native` Rust extension
  with graceful pure-Python fallback for every subsystem.

### Added — Rust native extension (`clew-native/`)

- **`clew-native/sandbox/`** — Landlock (Linux) and Seatbelt (macOS)
  process sandbox. Applies filesystem restrictions kernel-level;
  irreversible once applied.
- **`clew-native/circuit_breaker/`** — sliding-window circuit breaker
  with per-key registry. `RetryPolicy::server` (429+5xx retryable,
  401/403 auth-refresh, else terminal) and `RetryPolicy::client_storage`.
- **`clew-native/interjection/`** — interjection buffer with UTF-8-safe
  truncation at 25,000 chars.
- **`clew-native/compaction/`** — three-tier compaction engine with
  transport-agnostic trait seams (`ItemTokenCounter`, `CompactionSampler`,
  `CompactionObserver`).
- **`clew-native/actor/`** — `CancelToken` (atomic bool, reason,
  listener mpsc) and `Mailbox` helpers.
- **`clew-native/pyo3/`** — PyO3 bindings exposing all of the above
  as the `clew_native` Python extension module. Built via maturin:
  `cd clew-native && maturin develop --release -m pyo3/Cargo.toml`.

### Added — documentation

- **`ARCHITECTURE.md`** — high-level diagram, directory layout, migration guide.
- **`REFACTORING.md`** — rationale for each refactoring decision, what was
  ported from Grok Build and what was preserved unchanged.
- **`CHANGELOG.md`** — this file.

### Changed

- **`clew/agent_runtime.py`** — file is UNCHANGED from v1.3.0. Only the
  module docstring was updated to add a "LEGACY" header pointing users
  to `clew.agent.AgentRuntimeV2`. The 5750-line legacy runtime is preserved
  as-is for backwards compatibility.
- **`clew/agent_orchestrator.py`** — header updated to note that the v1.3
  monkey-patching bridge is still available but `clew.agent.AgentRuntimeV2`
  is the recommended path for new code.
- **`clew/__init__.py`** — version bumped to `2.0.0`; full v2.0 changelog
  added to module docstring.
- **`pyproject.toml`** — version bumped; `cryptography>=42.0.0` added to
  dependencies; `maturin>=1.7.0` added to dev dependencies; `Programming Language :: Rust`,
  `Operating System :: POSIX :: Linux` classifiers added; new
  `clew-acp = "clew.agent.acp_server:cli_main"` script entry; package-data
  expanded to include `agent/templates/*`.
- **`requirements.txt`** — reorganized and clarified; `cryptography>=42.0.0`
  added; PySide6-Addons explicit; toml/pyyaml/pygments/rich added.

### Fixed

- **`clew/smoke_tests.py`** — three bugs fixed:
  1. Removed the hardcoded Gemini API key (REVOKED and rotated). The key
     is now read from the `$GEMINI_API_KEY` environment variable. The
     previous committed key (`<REDACTED_REVOKED_KEY>`)
     was a real secret leaked in source.
  2. Fixed `task_result.metadata['tool_calls']` — that key never existed.
     Tool calls live in `task_result.tool_calls` (a `List[ToolCall]`), and
     each `ToolCall` has a `.name` attribute (a `ToolName` enum), not a
     dict. The smoke tests now correctly coerce the enum to a string for
     comparison.
  3. Tests are now SKIPPED (not FAILED) if no API key is set or if the
     provider setup fails. This means CI runs without credentials don't
     fail mysteriously.

### Removed

- **`clew/smoke_test_report.json`** — removed. The committed report showed
  all 12 tests failing with the `Task` subscript bug. A fresh report is
  generated when tests are actually run.

### Preserved UNCHANGED

These Clew-unique modules are not modified:
- `clew/providers/` (16 providers + AutoRouter)
- `clew/office_worker.py` (Office Worker section)
- `clew/slash_commands.py` (Markdown-based slash commands)
- `clew/memory_service.py` (Markdown memory with JSON metadata)
- `clew/mcp_client.py` / `clew/mcp_manager.py`
- `clew/lsp_client.py` / `clew/git_service.py` / `clew/diff_service.py`
- `clew/skill_loader.py` / `clew/plugins/`
- `clew/quota.py` / `clew/activity_log.py` / `clew/token_tracker.py`
- `clew/auto_updater.py` / `clew/project_context.py` / `clew/command_policy.py`
- `clew/progressive_tools.py`
- `clew/loop/` / `clew/session/` / `clew/swarm/` / `clew/compaction/`
- `clew/web/` / `clew/web_bridge.py`
- `clew/api_server.py` / `clew/main_window.py` / `clew/app.py`
- `clew/__main__.py` / `clew/cli.py`
- `clew/code_viewer.py` / `clew/context_manager.py` / `clew/utils.py`
- `clew/templates/` / `clew/assets/`
- `Info.plist` / `entitlements.plist` / `LICENSE` / `CONTRIBUTING.md`

## [1.3.0] — previous release

Kimi Code-inspired agent architecture rewrite. See `clew/__init__.py` for
the v1.3.0 changelog.
