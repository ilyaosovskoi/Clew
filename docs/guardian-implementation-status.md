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
| 10 | Tests (risk scorer unit tests + smoke test) — **Issue #3** | Done |
| 11 | ApprovalModal 3-button Guardian verdict | Done |
| 12 | Guardian config toggle `ClewBridge` + slash command | Done |
| 13 | Sub-reviewer delegation (`review_with_subagent`) — **Issue #4** | Done |

## Details

### Core Guardian Module (`clew/agent/guardian.py`)

- `GuardianConfig` dataclass (level: `off` | `dangerous_only` | `all`;
  plus `use_subagent` flag for Issue #4 sub-reviewer delegation).
- `assess_risk()` — pure rule-based scoring for `execute_command`,
  `write_file`/`str_replace`/`edit_file`, `delete_file`, `git_*`. The
  scorer accepts commands as `str`, `list[str]`, or `tuple[str, ...]`
  (via `_normalise_command`) and treats `/etc`, `/usr`, `/bin`,
  `/sbin`, `/boot`, `/sys`, `/proc` as critical system paths in
  addition to the home-directory paths (`~/.ssh`, `~/.aws`, etc.).
- `review_with_llm()` — async LLM call with circuit breaker, JSON
  parsing via `_parse_verdict`. Falls back to APPROVE when the
  response is unparseable. Records `ok=False, rate_limited=True` to
  the breaker on rate-limit exceptions.
- `review_with_subagent()` — **Issue #4**: delegates the review to a
  read-only `explore` subagent. Subagent has no `write_file`,
  `execute_command`, or `str_replace` in its toolset (read-only is
  enforced at toolset construction time, not at dispatch time).
  Falls back to APPROVE when the subagent response is unparseable or
  when the subagent module is unavailable.
- `build_recent_context()` — projects conversation history into the
  compact form the LLM reviewer expects.

### Hooks (in `ToolEngine.execute`, `clew/agent_runtime.py`)

- Risk assessment before tool dispatch.
- LLM review for calls above threshold (driven by `config.level`).
- MODIFY verdict → applies suggested args + emits event.
- REJECT verdict → raises `RuntimeError` to abort.

### TUI (`clew_tui/`)

- `GuardianModal` class with 3 buttons (Approve / Reject / Use Fix).
- Routing in `_show_confirm()` based on `guardian_verdict == "MODIFY"`.
- `/guardian <level>` slash command.
- Config persistence in `ClewBridge`.

### GUI web (`clew/web_bridge.py` + `clew/web/`)

- `guardian_review_requested` Qt signal.
- `respond_guardian_review(verdict)` slot.
- `showGuardianConfirm()` in app.js.
- Guardian modal in index.html (verdict badge, rationale, proposed fix, 3 buttons).

## Test coverage (`clew/agent/test_guardian.py`)

43 tests covering:

- `GuardianConfig` defaults + Issue #4 `use_subagent` flag.
- Risk scoring: safe/unknown tools, write_file (workspace, critical
  path, critical filename, outside workspace), str_replace,
  execute_command (safe, dangerous patterns, sudo, list args, args
  field), git (push --force via args / subcommand, reset --hard, safe
  ops), delete_file (default + critical).
- `_normalise_command` for str / list / None.
- `build_recent_context` truncation + compaction-summary inclusion.
- `_parse_verdict` for APPROVE / MODIFY / REJECT / markdown-fenced /
  invalid / missing fields / MODIFY-without-args.
- `_looks_like_rate_limit` for all known phrasings.
- `review_with_llm`: provider missing (no breaker.record call),
  success, MODIFY verdict, circuit-open REJECT, unparseable response,
  provider error records `ok=False`.
- `review_with_subagent`: happy path, unparseable, spawn failure,
  `.text` attribute on result, no-runtime fallback, and routing via
  `review_with_llm` when `use_subagent=True`.

Run with:

```
pytest clew/agent/test_guardian.py -v
```
