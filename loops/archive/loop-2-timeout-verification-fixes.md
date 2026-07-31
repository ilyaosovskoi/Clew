# Loop 2: BUG + PERF — Terminal-Bench Inspired Runtime Fixes

## Loop Identity
| Field | Value |
|-------|-------|
| **Loop ID** | `LOOP-2026-07-31-002` |
| **Title** | Terminal-Bench Inspired Runtime Fixes (Timeout + Verification) |
| **Owner** | @auto |
| **Start Date** | 2026-07-31 |
| **Target Close Date** | 2026-07-31 |
| **Related Issues/PRs** | IMPLEMENTATION_PROMPT_GLM.md Loop 2 |
| **Parent Loop** | None |

---

## Problem Statement
Cline ran 89 Terminal-Bench 2.0 tasks and found that the top failure causes map directly to Clew bugs:
1. `RUN_TIMEOUT = 15s` hardcoded for ALL commands (npm install, pytest, cargo build all need more)
2. `self_verify` tool exists but optional, not enforced — agents assume success without verifying
3. Long-running commands killed early by the 15s timeout

---

## Success Criteria (MUST be measurable)

| # | Criterion | Metric / Definition of Done | Target | Measurement Method | Weight |
|---|-----------|----------------------------|--------|-------------------|--------|
| 1 | `execute_command timeout=300` works | 5-min command completes | Working | pytest | High |
| 2 | Default timeout (no arg) | 180s (was 15s) | 180s | Code inspection | High |
| 3 | Timeout bounds enforced | 1–3600s, no crashes | Working | pytest | High |
| 4 | `run_code` timeout works | Same as execute_command | Working | pytest | High |
| 5 | System prompt has verification guidance | Visible in prompt dump | Present | pytest | High |
| 6 | G17 detects missing verification | Learning created in test scenario | Working | pytest | Medium |
| 7 | No regression | Existing tool calls without timeout still work (180s default) | Working | pytest | High |

---

## Implementation Summary

### Files Modified
- `clew/agent_runtime/tool_engine/_engine.py` — Replaced `RUN_TIMEOUT = 15` with `RUN_TIMEOUT = 180`, added `timeout` parameter to `_execute_command()` and `_run_code()`, added bounds enforcement (1–3600s)
- `clew/agent_runtime/prompts.py` — Added `timeout` parameter to `execute_command` and `run_code` tool schema entries, added Verification Protocol guidance to `GENERAL_SYSTEM_SUFFIX`
- `clew/learning_loop.py` — Added `MissingVerificationSignal` dataclass, `detect_missing_verification()` function, `create_learning_from_missing_verification()` function, wired into `scan_and_create_learnings()`

### Files Created
- `clew/tests/test_timeout_and_verification.py` — Test suite for timeout + verification fixes

---

## Status: CLOSED
