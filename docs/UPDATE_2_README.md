# update_2 — Decomposition of agent_runtime.py and web_bridge.py

This package contains the **structural refactoring** of Clew's two
largest monolith files into proper Python packages. **No logic changes
— pure code reorganisation.**

## What's inside

```
update_2/
├── UPDATE_2_README.md                  ← this file
├── REFACTORING_NOTES.md                ← detailed refactoring notes
│                                         (strategy, file map, smoke
│                                         test results, migration guide)
├── scripts/                            ← the slicer + smoke tests
│   ├── slice_clew.py                   ← the script that did the split
│   ├── slice_smoke_test.py             ← 42-check smoke test
│   └── slice_deep_verify.py            ← 20-check deep per-class verify
└── clew/
    ├── agent_runtime.py                ← 23-line SHIM (re-exports)
    ├── agent_runtime/                  ← NEW PACKAGE
    │   ├── __init__.py
    │   ├── types.py                    ← enums + dataclasses
    │   ├── _helpers.py                 ← _sanitize_command + ALLOWED_COMMANDS
    │   ├── context_memory.py           ← ContextMemory
    │   ├── diff_utils.py               ← diff helpers
    │   ├── prompts.py                  ← system prompt + PromptBuilder
    │   ├── parser.py                   ← OutputParser
    │   ├── runtime.py                  ← AgentRuntime (the agent loop)
    │   ├── worker.py                   ← AgentWorker (QThread)
    │   └── tool_engine/
    │       ├── __init__.py
    │       └── _engine.py              ← ToolEngine (big dispatcher)
    ├── web_bridge.py                   ← 16-line SHIM (re-exports)
    └── web_bridge/                     ← NEW PACKAGE
        ├── __init__.py
        ├── _paths_config.py            ← ~/.clew/ paths + config + chat store
        ├── workers.py                  ← 3 QThread workers
        ├── bridge.py                   ← ClewBridge (the QObject)
        └── mixins/
            └── __init__.py             ← placeholder for future extraction
```

## What changed

| Before                                  | After                                       |
|-----------------------------------------|---------------------------------------------|
| `clew/agent_runtime.py` — 1 file, 6 218 lines | `clew/agent_runtime/` — 11 files + 23-line shim |
| `clew/web_bridge.py` — 1 file, 3 890 lines    | `clew/web_bridge/` — 5 files + 16-line shim     |

## Backwards compatibility

**100% preserved.** Every existing import keeps working:

```python
# All of these still work — the shim re-exports everything:
from clew.agent_runtime import AgentRuntime
from clew.agent_runtime import (
    AgentRuntime, AgentWorker, AgentEvent, Task, TaskType,
    ToolCall, ToolName, TaskResult, AgentStep,
    ConversationMessage, ContextMemory, ToolEngine,
    PromptBuilder, OutputParser,
    TOOL_SCHEMA, SYSTEM_PROMPT,
    GENERAL_SYSTEM_SUFFIX, HEAVY_CODE_SYSTEM_SUFFIX,
    ALLOWED_COMMANDS,
)
from clew.web_bridge import ClewBridge
from clew.web_bridge import _load_config  # used by main_window.py
```

The following consumer files were verified to require **zero changes**:

- `clew/cli.py`
- `clew/api_server.py`
- `clew/main_window.py` (2 imports)
- `clew/smoke_tests.py`
- `clew/agent_orchestrator.py` (3 imports)
- `clew/progressive_tools.py`
- `clew/agent/runtime.py` (2 imports)
- `clew/agent/legacy/__init__.py`
- `clew/agent/acp_server.py`
- `clew/session/subagent_host.py`
- `clew_tui/bridge.py` (2 imports)

## Smoke test results

```
=== slice_smoke_test.py ===
PASSED: 42   WARN: 0   FAILED: 0

=== slice_deep_verify.py ===
PASSED: 20   WARN: 0   FAILED: 0
```

The smoke test verifies:
1. Every `.py` file compiles (`py_compile`).
2. Every `.py` file parses cleanly (AST).
3. Total line count is within ~5% of the original (catches code drops).
4. Every public top-level symbol is preserved.
5. Both shim files use wildcard re-import.
6. **Per-class method coverage** — every class is preserved with all
   its methods (e.g. `ToolEngine` 56/56 methods, `ClewBridge` 144/144
   methods, `AgentRuntime` 38/38 methods).

## Installation

Unpack over the source tree:

```bash
unzip update_2.zip -d /path/to/clew/
```

This will:
- create the new `clew/agent_runtime/` package,
- create the new `clew/web_bridge/` package,
- replace the original `clew/agent_runtime.py` with the 23-line shim,
- replace the original `clew/web_bridge.py` with the 16-line shim.

After installation, run the smoke tests:

```bash
python scripts/slice_smoke_test.py     # 42 checks
python scripts/slice_deep_verify.py    # 20 checks
```

Both should print `PASSED: N   WARN: 0   FAILED: 0`.

## What's NOT in this refactoring

This is a **pure structural refactoring** — no logic changes, no bug
fixes, no behaviour alterations. The goal was to make the two largest
files navigable without breaking anything.

The following were deliberately left for follow-up passes (see
`REFACTORING_NOTES.md` for details):

1. **`ToolEngine` internal split** — `_engine.py` is still 134 KB.
   Splitting per-tool requires careful design (shared state, dispatch).
2. **`ClewBridge` mixin extraction** — `@Slot` methods must stay on
   the class (Qt MOC constraint), but non-`@Slot` helpers could move
   to `mixins/`.
3. **Circular import cleanup** — lazy imports inside `runtime.py`
   methods are preserved as-is.
4. **Bug fixes** — see `update/` and `update_1/` for those.

## Why this matters

- **Navigation:** finding a tool implementation goes from
  "scroll 6 218 lines" to "open `tool_engine/_engine.py`".
- **LLM context:** each module is now small enough to fit in a single
  context window, so an AI assistant can reason about one part without
  loading the whole monolith.
- **Future splits:** the new package structure makes it easy to do
  further per-tool or per-feature extraction in follow-up PRs, without
  touching the rest of the codebase.
- **No risk:** because the public API is preserved, this refactoring
  can be merged with confidence — if anything breaks, it's a
  mechanical issue (shim missing a symbol), not a logic issue.
