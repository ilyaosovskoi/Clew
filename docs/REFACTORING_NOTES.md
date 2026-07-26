# REFACTORING NOTES — agent_runtime.py & web_bridge.py decomposition

## TL;DR

Two of Clew's largest monolithic files were split into packages without
breaking the public API. Old imports keep working via thin shim files
that re-export from the new package layout.

| File                 | Before                | After                          |
|----------------------|-----------------------|--------------------------------|
| `clew/agent_runtime` | 1 file, 6 218 lines   | 11 files in `agent_runtime/`   |
|                      |                       | package + 23-line shim         |
| `clew/web_bridge`    | 1 file, 3 890 lines   | 5 files in `web_bridge/`       |
|                      |                       | package + 16-line shim         |

The smoke test verifies:
- every `.py` file compiles,
- every public top-level symbol from the original is preserved,
- every class is preserved with all its methods (per-class AST diff),
- both shim files use wildcard re-import.

Result: **42/42 smoke checks pass**, **20/20 deep verification checks pass**.

## Why

`agent_runtime.py` and `web_bridge.py` had grown to the point where:

1. Loading them in an editor (or in an LLM context window) was painful.
2. Finding the implementation of a single tool required scrolling through
   2 700 lines of `ToolEngine._dispatch()`.
3. Any change to one part of the file risked accidentally touching an
   unrelated part (no isolation).
4. New contributors couldn't get a quick mental map of the codebase
   because the file structure didn't reflect the conceptual structure.

## Strategy: CONSERVATIVE THIN RE-EXPORT

The refactoring follows three rules:

1. **No semantic changes.** Code is moved verbatim. Imports at the top
   of each new module are added; the body is a byte-for-byte copy of
   the original lines.
2. **Public API is sacred.** Every name that was importable from
   `clew.agent_runtime` or `clew.web_bridge` is still importable from
   the same path. This is enforced by an AST-based smoke test.
3. **Shims, not deletions.** The original `clew/agent_runtime.py` and
   `clew/web_bridge.py` files still exist, but they are now ~20-line
   shims that re-export from the new package. This means every existing
   `from clew.agent_runtime import X` keeps working without changes.

This strategy was chosen over "full migration" (rewriting all imports
across the project) because:
- Clew has ~15 files that import from `agent_runtime` (cli.py,
  api_server.py, agent_orchestrator.py, smoke_tests.py, agent/runtime.py,
  agent/legacy/__init__.py, session/subagent_host.py, …) and ~2 that
  import from `web_bridge` (main_window.py). Migrating all of them in
  one pass risks introducing bugs that are hard to bisect.
- The shim approach lets us migrate consumers one at a time, in
  follow-up PRs, without breaking anything.

## New layout

### `clew/agent_runtime/` (package — was `agent_runtime.py`)

```
clew/agent_runtime/
├── __init__.py            ← re-exports the full public API
├── types.py               ← TaskType, ToolName, AgentEvent enums
│                            Task, ToolCall, AgentStep, TaskResult,
│                            ConversationMessage dataclasses
├── _helpers.py            ← ALLOWED_COMMANDS, _sanitize_command
├── context_memory.py      ← ContextMemory + _estimate_tokens
├── diff_utils.py          ← _split_multi_file_diff, _apply_unified_diff,
│                            _str_replace_hint, _compute_diff_text,
│                            _backup_file
├── prompts.py             ← TOOL_SCHEMA, SYSTEM_PROMPT,
│                            GENERAL_SYSTEM_SUFFIX,
│                            HEAVY_CODE_SYSTEM_SUFFIX, PromptBuilder
├── parser.py              ← OutputParser, _warn_unknown_tools
├── runtime.py             ← AgentRuntime (the agent loop)
├── worker.py              ← AgentWorker (QThread wrapper)
└── tool_engine/
    ├── __init__.py        ← re-exports ToolEngine
    └── _engine.py         ← ToolEngine (the big dispatcher, 134 KB)
```

**Class → file map** (for finding a class quickly):

| Class              | File                              |
|--------------------|-----------------------------------|
| `TaskType`         | `types.py`                        |
| `ToolName`         | `types.py`                        |
| `AgentEvent`       | `types.py`                        |
| `Task`             | `types.py`                        |
| `ToolCall`         | `types.py`                        |
| `AgentStep`        | `types.py`                        |
| `TaskResult`       | `types.py`                        |
| `ConversationMessage` | `types.py`                     |
| `ContextMemory`    | `context_memory.py`               |
| `ToolEngine`       | `tool_engine/_engine.py`          |
| `PromptBuilder`    | `prompts.py`                      |
| `OutputParser`     | `parser.py`                       |
| `AgentRuntime`     | `runtime.py`                      |
| `AgentWorker`      | `worker.py`                       |

**Method count per class** (preserved 1:1):

| Class            | Methods |
|------------------|---------|
| `AgentRuntime`   | 38      |
| `ToolEngine`     | 56      |
| `ContextMemory`  | 17      |
| `OutputParser`   | 12      |
| `PromptBuilder`  | 4       |
| `AgentWorker`    | 5       |
| `ConversationMessage` | 2  |

### `clew/web_bridge/` (package — was `web_bridge.py`)

```
clew/web_bridge/
├── __init__.py            ← re-exports ClewBridge + helpers
├── _paths_config.py       ← _clew_home, _config_path, _chats_dir,
│                            _load_config, _save_config,
│                            _chat_path, _load_chat, _save_chat,
│                            _load_templates_from_disk,
│                            _load_skills_from_disk,
│                            _classify_user_intent
├── workers.py             ← GenerationWorker, OneShotWorker, TitleWorker
├── bridge.py              ← ClewBridge (the QObject with all @Slots)
└── mixins/                ← (placeholder — reserved for future
                             extraction of non-@Slot helpers)
    └── __init__.py
```

**Why `ClewBridge` stays in a single `bridge.py`**:

`ClewBridge` is a `QObject` subclass with ~80 `@Slot`-decorated methods.
Qt's MOC (Meta-Object Compiler) needs every `@Slot` to live on the
actual class — you can't put `@Slot` methods on a mixin and inherit
them, because the MOC doesn't follow Python's MRO. So the bridge
**cannot** be split via multiple inheritance.

Instead, the bridge was slimmed by:
- pulling out the 3 `QThread` worker classes (→ `workers.py`),
- pulling out the 11 module-level helpers (→ `_paths_config.py`),
- leaving the bridge with only `@Slot` methods + the constructor +
  internal signal/slot wiring.

This reduced `bridge.py` from 3 890 lines (original monolith) to
~3 300 lines of pure `ClewBridge` body — a ~15% reduction that makes
the file noticeably easier to navigate.

The `mixins/` subdirectory is reserved for a future pass: extract
non-`@Slot` helper methods (e.g. `_get_or_create_agent_runtime`,
`_apply_title`, `_on_diff_review_requested`, `_create_pre_agent_snapshot`)
into mixins that `ClewBridge` can inherit without MOC issues.

## Backwards compatibility

### Shim file: `clew/agent_runtime.py` (23 lines)

```python
"""
Legacy shim for clew.agent_runtime.
...
"""
from clew.agent_runtime import *  # noqa: F401,F403
from clew.agent_runtime import __all__  # noqa: F401
```

### Shim file: `clew/web_bridge.py` (16 lines)

```python
"""
Legacy shim for clew.web_bridge.
...
"""
from clew.web_bridge import *  # noqa: F401,F403
from clew.web_bridge import __all__  # noqa: F401
```

### Verified consumers (no changes required)

The following files in Clew import from `agent_runtime` or `web_bridge`
and continue to work without modification:

| File                                  | Import                                                       |
|---------------------------------------|--------------------------------------------------------------|
| `clew/cli.py`                         | `from .agent_runtime import AgentEvent, AgentRuntime, TaskType` |
| `clew/api_server.py`                  | `from .agent_runtime import AgentRuntime, TaskType, AgentEvent` |
| `clew/main_window.py`                 | `from .web_bridge import ClewBridge`                          |
| `clew/main_window.py`                 | `from .web_bridge import _load_config`                        |
| `clew/smoke_tests.py`                 | `from clew.agent_runtime import AgentRuntime, Task, ...`      |
| `clew/agent_orchestrator.py`          | `from clew.agent_runtime import ToolName, PromptBuilder, ...` |
| `clew/progressive_tools.py`           | `from clew.agent_runtime import PromptBuilder`                |
| `clew/agent/runtime.py`               | `from clew.agent_runtime import OutputParser, ...`            |
| `clew/agent/legacy/__init__.py`       | `from clew.agent_runtime import AgentRuntime, ToolEngine, ...`|
| `clew/agent/acp_server.py`            | `from clew.agent_runtime import AgentRuntime`                 |
| `clew/session/subagent_host.py`       | `from ..agent_runtime import AgentRuntime, Task, TaskType`    |
| `clew_tui/bridge.py`                  | `from clew.agent_runtime import AgentRuntime, TaskType`       |

## Smoke test

The smoke test (`scripts/slice_smoke_test.py`) verifies:

1. **Syntax** — every `.py` file in the new packages passes
   `py_compile`.
2. **AST parse** — every `.py` file parses cleanly.
3. **Line count** — the new package's total line count is within
   ~5% of the original (overhead is from added module docstrings +
   import blocks). Catches accidental code drops.
4. **Public symbol coverage** — every top-level class/function/constant
   in the original is present in the new package.
5. **Shim re-export** — both shim files use wildcard re-import.

Result: **42/42 PASS**.

The deep verification (`scripts/slice_deep_verify.py`) goes further:

6. **Per-class method coverage** — for every class in the original,
   every method (by name) is present in the new package. Catches
   silent method drops inside a class that the symbol-level check
   would miss.

Result: **20/20 PASS** (14 classes in agent_runtime, 4 in web_bridge,
all with full method coverage).

## How to run the smoke tests

```bash
# From the project root after unpacking update_2.zip over the source tree:
python /path/to/slice_smoke_test.py     # 42 checks
python /path/to/slice_deep_verify.py    # 20 checks
```

Both scripts are also bundled in `update_2/scripts/` for convenience.

## Migration path for consumers (optional)

Consumers can keep using `from clew.agent_runtime import X` forever —
the shim will keep working. But for new code, prefer the explicit
package path:

```python
# Old (still works):
from clew.agent_runtime import AgentRuntime, TaskType

# New (preferred for new code):
from clew.agent_runtime.runtime import AgentRuntime
from clew.agent_runtime.types import TaskType
```

The new paths are more explicit about where each symbol lives, which
helps both humans and LLMs navigate the codebase.

A future PR can mechanically migrate all existing imports to the new
paths and remove the shims. Until then, both work.

## What's NOT in this refactoring

To keep the change reviewable and low-risk, the following were
deliberately left for follow-up passes:

1. **`ToolEngine` internal split.** `tool_engine/_engine.py` is still
   134 KB. Splitting it per-tool (files.py, git.py, mcp.py,
   subagent.py, office.py, code.py, search.py) would require either:
   - subclassing `ToolEngine` and overriding `_dispatch` (fragile), or
   - mixin pattern with shared state via `self` (works but ugly), or
   - extracting tool handlers into free functions that take the engine
     as their first arg (cleanest, but ~56 methods to migrate).
   The current refactoring preserves `ToolEngine` as a single class so
   the call graph is unchanged. A follow-up PR can do the per-tool
   split.

2. **`ClewBridge` mixin extraction.** As explained above, `@Slot`
   methods must stay on the class. Non-`@Slot` helpers could be
   extracted to `mixins/`, but identifying which methods are safe to
   move requires careful reading of each one. Left for a follow-up.

3. **Circular import cleanup.** `clew.agent_runtime.runtime` lazy-imports
   `clew.providers`, `clew.context_manager`, `clew.skill_loader`,
   `clew.office_worker`, `clew.activity_log` inside methods (not at
   module top) to avoid circular imports. This was already the case
   in the original monolith and is preserved as-is. A future
   refactoring could break these cycles properly (e.g. by extracting
   a `ProviderInterface` that `runtime` imports statically).

4. **No code changes.** No logic was modified, no bugs were fixed, no
   behaviour was altered. This is a pure structural refactoring. Any
   bug fixes are in `update/` and `update_1/`, not here.

## File sizes (after refactoring)

```
agent_runtime/__init__.py            2.4 KB
agent_runtime/types.py               7.6 KB
agent_runtime/_helpers.py            5.3 KB
agent_runtime/context_memory.py     15.1 KB
agent_runtime/diff_utils.py          9.3 KB
agent_runtime/prompts.py            28.2 KB
agent_runtime/parser.py             23.8 KB
agent_runtime/runtime.py            75.6 KB
agent_runtime/worker.py              3.8 KB
agent_runtime/tool_engine/_engine.py 134.5 KB
agent_runtime.py (shim)              1.1 KB

web_bridge/__init__.py               1.1 KB
web_bridge/_paths_config.py         20.8 KB
web_bridge/workers.py                8.6 KB
web_bridge/bridge.py               146.4 KB
web_bridge/mixins/__init__.py        0.1 KB
web_bridge.py (shim)                 0.9 KB
```

The biggest single file is still `tool_engine/_engine.py` (134 KB) —
this is the next candidate for splitting, but as noted above, it
requires more thought because of the shared-state constraints.
