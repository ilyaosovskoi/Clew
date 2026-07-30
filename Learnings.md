# Learnings Log

## Purpose
Centralized, searchable repository of validated learnings from loops, incidents, experiments, and retrospectives. Each entry captures *what we learned*, *evidence*, and *how to apply it next time*.

**Golden Rule**: Only record learnings backed by evidence (test results, metrics, incident postmortems, A/B data). No hunches.

---

## Structure

```
learnings/
├── YYYY-MM-DD_slug.md          # Individual learning entries (one per file)
├── index.md                    # Auto-generated index (run `scripts/generate_learnings_index.py`)
└── tags/                       # Tag-based views (optional)
    ├── security.md
    ├── performance.md
    ├── architecture.md
    └── process.md
```

---

## Entry Template

Copy this template for each new learning:

```markdown
---
id: LEARN-YYYYMMDD-XXX
date: YYYY-MM-DD
tags: [tag1, tag2, tag3]
source: LOOP-XXXX | INCIDENT-XXXX | EXPERIMENT-XXXX | RETRO-XXXX
severity: low | medium | high | critical
status: validated | tentative | superseded
---

# Title: One-line Summary

## Context
What were we doing? What triggered this learning?

## What Happened
Factual description of the event/result. Include metrics, logs, test output.

## Root Cause / Insight
Why did this happen? What's the underlying principle?

## Evidence
- Link to test run: `pytest clew/agent/test_guardian.py::test_xxx -v`
- Link to PR: #123
- Metric dashboard: grafana.link/panel/123
- Log snippet: `error: connection pool exhausted after 100 req/s`

## Actionable Rule
**DO**: Specific behavior to adopt
**DON'T**: Specific behavior to avoid

## How to Apply Next Time
Concrete checklist or decision framework for future loops.

## Related Learnings
- LEARN-YYYYMMDD-XXX: Related topic
- LEARN-YYYYMMDD-XXX: Contradicted/superseded by this
```

---

## Initial Learnings (Seed Data)

### LEARN-20250725-001: CSS in Textual 8.x — Full Rewrite Beats Incremental Fixes
**Date**: 2025-07-25  
**Tags**: [frontend, textual, css, debugging]  
**Source**: LOOP-2025-07-25-003 (TUI Smoke Test)  
**Severity**: medium  
**Status**: validated  

**Context**: TUI failed to start due to CSS incompatibilities with Textual 8.x (`var(--*)`, `@keyframes`, `transition:` not supported).

**What Happened**: Incremental `Edit` fixes produced broken lines (comment + property remainder). Full `Write` of corrected CSS solved it in one step.

**Root Cause**: Textual 8.x CSS engine is stricter; legacy syntax fails silently or with cryptic errors. Patching line-by-line introduces syntax errors faster than it fixes them.

**Evidence**:
- `clew_tui/smoke_test.py` passed after full rewrite
- Before: 12 CSS errors on startup; After: 0 errors

**Actionable Rule**:
- **DO**: When >3 incompatible constructs in a CSS file, rewrite the entire file with validated syntax
- **DON'T**: Use incremental `Edit` for CSS migrations across major Textual versions

**How to Apply Next Time**:
1. Run smoke test → capture all CSS errors
2. If errors > 3 and involve unsupported syntax (`var()`, `@keyframes`, `transition`), write new file from scratch
3. Use Textual built-in variables (`$accent`, `$text`, `$text-muted`) only

---

### LEARN-20250725-002: Refactoring Monoliths — Shim Layer Prevents Big-Bang Migration Risk
**Date**: 2025-07-25  
**Tags**: [refactoring, architecture, python, packaging]  
**Source**: LOOP-2025-07-25-001 (agent_runtime/web_bridge split)  
**Severity**: high  
**Status**: validated  

**Context**: Split 2 monolithic files (6,218 + 3,890 lines) into packages with 11 + 5 modules.

**What Happened**: Original refactoring introduced import bugs (`@staticmethod` on module functions, missing imports). Fixed in follow-up loop (LOOP-2025-07-26-001).

**Root Cause**: 
1. Mechanical split didn't catch module-level functions incorrectly decorated as staticmethods
2. Lazy imports in original (`clew.providers` inside methods) not replicated in new modules
3. No automated verification of *behavioral* equivalence, only syntactic

**Evidence**:
- 42/42 smoke checks pass (syntax, AST, symbols)
- 20/20 deep verification pass (per-class method coverage)
- But: runtime import failed until 3 modules patched

**Actionable Rule**:
- **DO**: Use conservative thin-shim re-export strategy (`from .pkg import *` in original path)
- **DO**: Add runtime import test to smoke suite: `python -c "from clew.agent_runtime import AgentRuntime; print('OK')"`
- **DON'T**: Delete original file until all consumers migrated
- **DON'T**: Assume mechanical split preserves semantics — verify with *integration* tests

**How to Apply Next Time**:
1. Create shim first, verify all imports work
2. Split module by module, run full test suite after each
3. Keep shim indefinitely; migrate consumers in follow-up PRs
4. Add "import verification" to CI: `pytest tests/import_smoke_test.py`

---

### LEARN-20250725-003: Automated TUI Smoke Test Saves Hours
**Date**: 2025-07-25  
**Tags**: [testing, tui, automation, textual]  
**Source**: LOOP-2025-07-25-003  
**Severity**: medium  
**Status**: validated  

**Context**: Needed to verify TUI starts without manual interaction.

**What Happened**: Created `clew_tui/smoke_test.py` that launches app, waits 3s, sends SIGINT, checks stderr for crashes.

**Root Cause**: Manual TUI testing is slow, flaky, and easy to skip.

**Evidence**:
- Test catches CSS errors, import errors, widget init crashes
- Runs in 4s headless (CI-friendly with xvfb)
- Caught 3 regressions in 1 day

**Actionable Rule**:
- **DO**: Write headless smoke test for *every* UI entry point (TUI, GUI, CLI)
- **DO**: Run in CI on every PR
- **DON'T**: Rely on manual "run and look" for regression detection

**How to Apply Next Time**:
```python
# Template for any Textual app
async def test_tui_smoke():
    app = MyApp()
    async with app.run_test() as pilot:
        await pilot.pause(3.0)  # Let CSS render, widgets mount
        # Assert key widgets exist
        assert pilot.app.query_one("#chat-log")
        assert pilot.app.query_one("#input")
    # No exception = pass
```

---

### LEARN-20250726-001: Module-Level Functions with @staticmethod Break Imports
**Date**: 2025-07-26  
**Tags**: [python, refactoring, bug, import]  
**Source**: LOOP-2025-07-26-001 (agent_runtime diff_utils fix)  
**Severity**: high  
**Status**: validated  

**Context**: Refactored `diff_utils.py` from monolith; functions had `@staticmethod` and `self` params but were module-level.

**What Happened**: `from clew.agent_runtime import _backup_file` failed with `TypeError: _backup_file() missing 1 required positional argument: 'p'`

**Root Cause**: `@staticmethod` on module-level function makes it a plain function but `self` parameter remains. Callers passed `(self, p)` but function expected `(p)`.

**Evidence**:
- `python -c "from clew.agent_runtime import _backup_file"` → TypeError
- Fixed by removing `@staticmethod` and changing signature to `(backup_dir, max_backups, p)`

**Actionable Rule**:
- **DO**: Use module-level functions *without* decorators for utility modules
- **DO**: Use `def func(a, b):` not `@staticmethod def func(self, a, b):`
- **DON'T**: Copy-paste class methods to module level without removing `self` and decorators

**How to Apply Next Time**:
1. Grep for `@staticmethod` in non-class contexts after refactoring: `grep -rn "@staticmethod" clew/agent_runtime/ --include="*.py" | grep -v "class "`
2. Verify all imports work: `python -c "from clew.agent_runtime import *"`

---

### LEARN-20250726-002: ProviderRegistry Must Be Imported at Runtime, Not TYPE_CHECKING Only
**Date**: 2025-07-26  
**Tags**: [python, typing, circular-import, architecture]  
**Source**: LOOP-2025-07-26-001 (runtime.py fix)  
**Severity**: medium  
**Status**: validated  

**Context**: `AgentRuntime.__init__` takes `registry: ProviderRegistry` but import was in `TYPE_CHECKING` block.

**What Happened**: Runtime `NameError: name 'ProviderRegistry' is not defined` when instantiating `AgentRuntime`.

**Root Cause**: `TYPE_CHECKING` imports are stripped at runtime. The type annotation `registry: ProviderRegistry` is evaluated at class definition time in Python 3.12+ (PEP 563 postponed evaluation helps but not for base classes or certain positions).

**Evidence**:
- `python -c "from clew.agent_runtime import AgentRuntime"` → NameError
- Fixed by moving `from clew.providers import ProviderRegistry` to runtime imports

**Actionable Rule**:
- **DO**: Import runtime dependencies at module top level, even if only used in type hints
- **DO**: Use `from __future__ import annotations` (Python 3.7+) for forward refs
- **DON'T**: Put runtime-required types in `TYPE_CHECKING` blocks

**How to Apply Next Time**:
```python
# Good
from clew.providers import ProviderRegistry
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from clew.providers import ProviderMessage  # Only for type hints

# Bad
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from clew.providers import ProviderRegistry  # Runtime error!
```

---

### LEARN-20260730-001: MCP-First Beats Hardcoded API for Zero-Config Web Search
**Date**: 2026-07-30  
**Tags**: [architecture, mcp, web-search, g18, zero-config]  
**Source**: G18 (Web Search & Internet Reach)  
**Severity**: medium  
**Status**: validated

**Context**: G18 needed a web search backend for Clew. The obvious approach was to hardcode a single search API (Exa, Tavily, Brave, etc.) — but every option required an API key, which broke the zero-config promise that's been Clew's differentiator since v1.0.

**What Happened**: Instead of hardcoding, we routed `web_search` through the existing `MCPManager` (same path `call_mcp_tool` already uses). This means:
- The user picks the search backend by editing `~/.clew/mcp.json` (one config toggle).
- A no-API-key backend (DuckDuckGo MCP via `npx ddg-mcp`) ships as a documented template (`docs/mcp_search_template.json`) — copy to `~/.clew/mcp.json` and search works immediately.
- Paid backends (Exa, Tavily, Brave) work too — just add their MCP server entry with `"role": "search"`.
- Ordered-fallback: if the primary backend is unavailable, Clew tries the next configured one, recording which backend actually served the request.

**Root Cause**: A hardcoded API would have:
1. Required every user to sign up for a key — friction at install time.
2. Locked Clew to one provider's quality / pricing / availability.
3. Duplicated the process lifecycle, config loading, and catalog logic that `MCPManager` already provides.
4. Violated the "zero-telemetry, zero-cloud" architecture rule — Clew has no servers of its own to proxy through.

The MCP-first approach reuses 100% of the existing MCP infrastructure (process lifecycle, `~/.clew/mcp.json` config, typed catalog, crash watchdog) and adds only a thin "discover search-capable servers + ordered fallback" layer on top.

**Evidence**:
- `clew/web_search_backend.py` (430 lines) — pure orchestration, no HTTP client of its own for search.
- `clew/tests/test_g18_web_search.py::test_web_search_falls_back_when_primary_fails` — verifies the fallback path.
- `web_fetch` IS implemented directly (urllib + HTML-to-text) because it doesn't need a search index — but still goes through URL validation + Guardian risk rules.
- Zero new dependencies added to `requirements.txt`.

**Actionable Rule**:
- **DO**: Route new external capabilities through MCP when an MCP server already does the job — you inherit config, lifecycle, and crash recovery for free.
- **DO**: Ship a no-API-key default as a documented template, not as a forced install — the user opts in by copying the file.
- **DO**: Use ordered-fallback so a single backend's outage doesn't break the feature.
- **DON'T**: Hardcode a paid API as the *only* path — it breaks zero-config and locks you to one vendor.
- **DON'T**: Reinvent process lifecycle / config loading when MCPManager already does it.

**How to Apply Next Time**:
1. Before adding a new external capability, check if an MCP server already exists for it.
2. If yes: route through `MCPManager.call_tool()` — write a thin wrapper that handles arg-shape variation and fallback, nothing more.
3. If no: implement directly with stdlib (urllib for HTTP), but still wrap output as a `<context_fragment>` so it participates in compaction + is tagged as untrusted external content.
4. Always ship a no-API-key template in `docs/` so a fresh install can enable the feature with one config toggle.

---

## Tags Index

| Tag | Count | Latest |
|-----|-------|--------|
| architecture | 2 | LEARN-20260730-001 |
| bug | 2 | LEARN-20250726-001 |
| circular-import | 1 | LEARN-20250726-002 |
| css | 1 | LEARN-20250725-001 |
| debugging | 1 | LEARN-20250725-001 |
| frontend | 1 | LEARN-20250725-001 |
| g18 | 1 | LEARN-20260730-001 |
| import | 1 | LEARN-20250726-001 |
| mcp | 1 | LEARN-20260730-001 |
| packaging | 1 | LEARN-20250725-002 |
| performance | 0 | — |
| process | 1 | LEARN-20250725-003 |
| python | 3 | LEARN-20250726-002 |
| refactoring | 2 | LEARN-20250726-001 |
| security | 0 | — |
| testing | 1 | LEARN-20250725-003 |
| textual | 1 | LEARN-20250725-001 |
| typing | 1 | LEARN-20250726-002 |
| web-search | 1 | LEARN-20260730-001 |
| zero-config | 1 | LEARN-20260730-001 |

---

## Maintenance

### Adding a Learning
1. Create `learnings/YYYY-MM-DD_slug.md` from template
2. Update this index (or run `scripts/generate_learnings_index.py`)
3. Link from relevant Loop Close Evaluation

### Review Cadence
- **Weekly**: Scan new entries during Weekly System Review
- **Monthly**: Prune `tentative` → `validated` or `superseded`
- **Quarterly**: Consolidate duplicate/related learnings

### Searching
```bash
# By tag
grep -l "tags:.*security" learnings/*.md

# By source loop
grep -l "source: LOOP-2025-07-26" learnings/*.md

# By keyword in content
grep -r "circuit breaker" learnings/
```