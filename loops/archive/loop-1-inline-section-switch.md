# Loop 1: FEAT — Inline Section Switching

## Loop Identity
| Field | Value |
|-------|-------|
| **Loop ID** | `LOOP-2026-07-31-001` |
| **Title** | Inline Section Switching (UX Improvement) |
| **Owner** | @auto |
| **Start Date** | 2026-07-31 |
| **Target Close Date** | 2026-07-31 |
| **Related Issues/PRs** | IMPLEMENTATION_PROMPT_GLM.md Loop 1 |
| **Parent Loop** | None |

---

## Problem Statement
Currently, section (general/heavy_code/office) is a static session parameter set at startup via CLI subcommand or GUI mode switch. The runtime supports `set_section()` but there's no UX to call it mid-session. Users must restart to switch modes.

---

## Success Criteria (MUST be measurable)

| # | Criterion | Metric / Definition of Done | Target | Measurement Method | Weight |
|---|-----------|----------------------------|--------|-------------------|--------|
| 1 | Parser accuracy | 100% on test cases (no false pos/neg) | 100% | pytest test_section_switching.py | High |
| 2 | TUI: section switch via `{office} msg` | Works, toast shows, cleaned msg sent | Working | Manual test | High |
| 3 | GUI: section switch via `{office} msg` | Works, mode indicator updates, cleaned msg sent | Working | Manual test | High |
| 4 | `/mode` slash command | All 4 variants work (/mode, /mode general, /mode heavy_code, /mode office) | Working | Manual test | High |
| 5 | No regression | Existing CLI `clew heavy-code` etc. still work | Working | Manual test | Medium |
| 6 | Token savings preserved | PromptBuilder still excludes other sections' schemas | Preserved | Code review | Medium |

---

## Anti-Criteria (What Failure Looks Like)
| # | Failure Signal | Threshold | Action if Triggered |
|---|---------------|-----------|-------------------|
| 1 | False positives in parser | JSON objects like `{"office": "val"}` parsed as section switch | Fix parser regex |
| 2 | Message content lost after section switch | Cleaned message is empty when it shouldn't be | Fix parser stripping logic |

---

## Implementation Summary

### Files Created
- `clew/agent_runtime/section_parser.py` — Section parser with `parse_section_switch()` function
- `clew/tests/test_section_switching.py` — Test suite for parser + TUI/GUI integration

### Files Modified
- `clew_tui/app.py` — Added inline section switch parsing in `_submit_prompt()`, added `/mode` slash command
- `clew/web_bridge/bridge.py` — Added `section_changed` signal, wired section parser into `send_message()` slot

---

## Status: CLOSED
