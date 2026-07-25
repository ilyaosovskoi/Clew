# Guardian Review — Implementation Status

## Completed

| # | Task | Status |
|---|------|--------|
| 1 | Add `GUARDIAN_REVIEW` event + `AgentEvent` enum | Done |
| 2 | GUI web: 3-button modal (index.html + app.js) + bridge slots | Done |
| 3 | Hook Guardian into `ToolEngine.execute` | Done |
| 4 | Config persistence (save/load from `~/.clew/config.json`) | Done |
| 5 | Extend legacy confirm info dict + 3-state accept | Done |
| 6 | TUI: ApprovalModal → GuardianModal routing | Done |
| 7 | TUI: `/guardian` slash command handler | Done |
| 8 | Create `guardian.md` template | Done |
| 9 | Create `guardian.py` module | Done |
| 11 | ApprovalModal 3-button Guardian verdict | Done |
| 12 | Guardian config toggle `ClewBridge` + slash command | Done |

## Pending

| # | Task | Status |
|---|------|--------|
| 10 | Tests (risk scorer unit tests + smoke test) | Pending |

## Details

### Core Guardian Module (`clew/agent/guardian.py`)
- `GuardianConfig` dataclass (level: `off` | `dangerous_only` | `all`)
- `assess_risk()` — rule-based scoring for tool calls (execute_command, write_file, etc.)
- `review_with_llm()` — async LLM call with circuit breaker & JSON parsing
- `build_recent_context()` — projects conversation history
- `_parse_verdict()` — extracts JSON from LLM responses

### Hooks (in `ToolEngine.execute`, `clew/agent_runtime.py`)
- Risk assessment before tool dispatch
- LLM review for calls above threshold
- MODIFY verdict → applies suggested args + emits event
- REJECT verdict → raises `RuntimeError` to abort

### TUI (`clew_tui/`)
- `GuardianModal` class with 3 buttons (Approve / Reject / Use Fix)
- Routing in `_show_confirm()` based on `guardian_verdict == "MODIFY"`
- `/guardian <level>` slash command
- Config persistence in `ClewBridge`

### GUI web (`clew/web_bridge.py` + `clew/web/`)
- `guardian_review_requested` Qt signal
- `respond_guardian_review(verdict)` slot
- `showGuardianConfirm()` in app.js
- Guardian modal in index.html (verdict badge, rationale, proposed fix, 3 buttons)