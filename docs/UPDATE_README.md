# Clew update — 2025-07-25

This folder contains every file added or modified while closing
Issues #3–#9 (Issue #8 was already done). Drop the files into the
corresponding locations in your Clew checkout:

## File placement

| File in this folder          | Destination in your Clew checkout            | Status  |
|------------------------------|----------------------------------------------|---------|
| `CLAUDE.md`                  | `CLAUDE.md`                                  | updated |
| `guardian-implementation-status.md` | `docs/guardian-implementation-status.md` | updated |
| `agent_runtime.py`           | `clew/agent_runtime.py`                      | updated (Issue #6 SQLite dispatch + pre-existing syntax fix) |
| `guardian.py`                | `clew/agent/guardian.py`                     | updated (Issues #3 + #4) |
| `test_guardian.py`           | `clew/agent/test_guardian.py`                | updated (Issue #3) |
| `context_fragments.py`       | `clew/agent/context_fragments.py`            | new (Issue #5) |
| `test_context_fragments.py`  | `clew/agent/test_context_fragments.py`       | new (Issue #5) |
| `sqlite_persistence.py`      | `clew/session/sqlite_persistence.py`         | new (Issue #6) |
| `test_sqlite_persistence.py` | `clew/session/test_sqlite_persistence.py`    | new (Issue #6) |
| `collaboration.py`           | `clew/collaboration.py`                      | new (Issue #7) |
| `test_collaboration.py`      | `clew/test_collaboration.py`                 | new (Issue #7) |
| `request_queue.py`           | `clew/request_queue.py`                      | new (Issue #9) |
| `test_request_queue.py`      | `clew/test_request_queue.py`                 | new (Issue #9) |

## Issue summary

- **#3 Guardian tests** — fixed 5 failing tests, expanded coverage
  from 13 → 43 tests. Bug fixes: removed duplicated
  `execute_command` block in `assess_risk`, added `/etc`, `/usr`,
  `/bin`, `/sbin`, `/boot`, `/sys`, `/proc` to `CRITICAL_PATHS`,
  added `_normalise_command` so list/tuple command args work.
- **#4 Guardian Agent sub-reviewer** — added `GuardianConfig.use_subagent`
  flag and `review_with_subagent()` that delegates the LLM review to
  a read-only `explore` subagent (read-only enforced at toolset
  construction time).
- **#5 Marker-based context fragments for compaction** — new
  `clew/agent/context_fragments.py` module. Tools emit
  `<context_fragment type="..." id="...">...</context_fragment>`
  blocks; the compactor preserves the latest per (type, id) and
  collapses older ones to a header + digest + closing tag.
- **#6 SQLite persistence** — new `clew/session/sqlite_persistence.py`
  adapter. `ContextMemory.save()` / `ContextMemory.load()` auto-dispatch
  to it on `*.db` / `*.sqlite` / `*.sqlite3` paths. JSON path is
  unchanged for backwards compatibility.
- **#7 Collaboration modes** — new `clew/collaboration.py` with four
  modes (Reviewer, Codegen, Pair, Observer) that compose on top of
  the existing `SwarmManager`. Orchestrator is runtime-agnostic.
- **#9 Request serialization queues** — new `clew/request_queue.py`
  with `RequestQueue` (per-provider concurrency cap + cooldown + retries),
  `QueueRegistry` singleton, and `wrap_provider()` / `unwrap_provider()`
  helpers.

Issue #8 (tool search meta-tool) was already done in the previous
session — see the `## Issue #8 Research` section in `CLAUDE.md`.

## Test results

All 126 new/updated tests pass:

```
pytest clew/agent/test_guardian.py clew/agent/test_context_fragments.py \
       clew/session/test_sqlite_persistence.py \
       clew/collaboration.py clew/test_collaboration.py \
       clew/request_queue.py clew/test_request_queue.py
# 126 passed in ~1s
```

## Pre-existing bug fixed as a side-effect

`clew/agent_runtime.py` had a syntax error in the `OFFICE_CREATE`
lambda (broken across two lines with a stray closing paren). Fixed
in passing — without this fix the module wouldn't import at all.
