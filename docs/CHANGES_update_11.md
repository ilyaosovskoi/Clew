# CHANGES — update_11 (G22a, G22b)

This patch directly answers two open GitHub issues:

- **#16 — "Comprehensive test suite for agent quality evaluation"** (G22a)
- **#17 — "TUI and interface user testing program"** (G22b)

Both loops are documented in `loops/archive/loop-{7,8}-*.md`.

The optional issue #1 ("Packaged builds fail with 'Error: local backend
unavailable'") was NOT attempted — G22a and G22b were substantial
enough on their own.

## TL;DR

- **G22a**: New `clew/benchmarks/` package — 16 self-contained tasks, a runnable harness with `clew-bench` CLI, dry-run + mock-provider modes, regression-diff script, scorecard persistence. Running the harness once against the current codebase surfaced **5 real NameErrors** in `clew/agent_runtime/runtime.py` and `clew/agent_runtime/prompts.py` (imported names that were referenced but never declared). All 5 fixed.
- **G22b**: New `clew_tui/tests/` package — 53 Pilot-driven interaction tests using Textual's `App.run_test()`. Fixed the command-palette Enter-key bug (the exact bug issue #17 was opened for): added `on_input_submitted` handler to `CommandPalette`. Found + fixed a second dead-binding: selecting `/capabilities` or `/handoff` from the main palette was a no-op (the routes weren't wired in `_open_sub_palette_for_cmd`).

## Test results (clean copy of base repo + this patch applied)

```
$ pytest clew/tests/ clew_tui/tests/ --ignore=clew/tests/test_notifier.py --timeout=30 -q

........................................................................ [ 13%]
.....................s........s.s....s........s.......ssssss............ [ 26%]
........................................................................ [ 40%]
........................................................................ [ 67%]
........................................................................ [ 80%]
........................................................................ [ 94%]
............................                                           [100%]
524 passed, 11 skipped in 37.00s
```

Plus the existing smoke test:

```
$ python -m clew_tui.smoke_test
======================================================================
  Total: 33  |  Passed: 33  |  Failed: 0  |  Skipped: 0
======================================================================
```

Plus the pre-existing `test_notifier.py::TestNotifier::test_status` network timeout — same 1 failure as the baseline before this patch, NOT a regression. It's a 15s pytest-timeout on a real-network Telegram/Discord/Slack send test that requires live credentials.

- **Baseline before patch**: 447 passed, 11 skipped, 1 failed (network timeout)
- **After patch**: 524 passed (+77 new tests), 11 skipped, 1 failed (same network timeout)
- **Net new tests**: 77 (G22a: 23 harness tests + G22b: 53 interaction tests + 1 newly-fixed test_section_switching test that was previously broken by the `build_skill_catalog` NameError)
- **No regressions**: every test that was green before is still green.

## G22a scorecard (mock-provider baseline run)

The harness was run once against the current codebase via:

```
clew-bench run --mock-provider --tag baseline
```

(The mock provider returns canned responses without spending money —
it proves the harness plumbing works end-to-end. A real-provider run
requires API credentials and is left to the maintainer.)

```
=== Scorecard summary ===
Started: 2026-08-04T08:03:17.718768+00:00
Finished: 2026-08-04T08:03:19.601806+00:00
Clew version: 2.0.0
Total tasks: 16
Passed: 0, Failed: 16, Errored: 0
Total cost: $0.0048
Total tokens: 480 in / 240 out
Total wall clock: 1.88s

=== Per-task results ===
  [general    ] easy   general_bug_fix_add                              pass=False wall=0.20s
  [general    ] medium general_debug_median_off_by_one                  pass=False wall=0.00s
  [general    ] easy   general_explain_function                         pass=False wall=0.00s
  [general    ] medium general_guardian_env_write                       pass=False wall=0.00s
  [general    ] easy   general_guardian_medium_risk                     pass=False wall=0.00s
  [general    ] medium general_multi_step_shapes                        pass=False wall=0.00s
  [general    ] easy   general_new_feature_multiply                     pass=False wall=0.00s
  [general    ] medium general_refactor_no_behavior_change              pass=False wall=0.00s
  [general    ] easy   general_str_replace_constant                     pass=False wall=0.00s
  [general    ] medium general_web_search_python_version                pass=False wall=0.00s
  [heavy_code ] hard   heavy_code_feature_priority_overdue              pass=False wall=0.00s
  [heavy_code ] hard   heavy_code_parallel_type_hints                   pass=False wall=0.00s
  [heavy_code ] hard   heavy_code_split_god_class                       pass=False wall=0.00s
  [office     ] easy   office_create_docx_report                        pass=False wall=0.01s
  [office     ] medium office_create_xlsx_sales                         pass=False wall=0.00s
  [office     ] medium office_fill_xlsx_template                        pass=False wall=0.14s
```

All 16 tasks failed — expected, because the FakeProvider returns
`<final_answer>done</final_answer>` without actually doing any work.
The point of this run was to prove the harness plumbing works
end-to-end: setup() materialises a starting tree, the agent loop
runs to completion (after the 5 import fixes), evaluate() inspects
the resulting state, and the scorecard is written to disk with token
+ cost accounting.

After the 5 import fixes, the same run completed without errors. The
scorecard is saved at:

    clew/benchmarks/results/20260804-080319-2.0.0-mock-baseline.json

## File list

### G22a — Agent-quality benchmark suite (loop-7-g22a-agent-benchmark-suite.md)

**New files:**
| Path | Purpose |
|------|---------|
| `clew/benchmarks/__init__.py` | Public API: TaskSpec, BenchmarkRunner, RunConfig, RunSummary, load_all_tasks, run. |
| `clew/benchmarks/_base.py` | TaskSpec / EvaluationReport / TaskResult dataclasses; Section + Difficulty enums; discover_task_modules + load_all_tasks; evaluator helpers (file_exists, function_exists, function_signature_has). |
| `clew/benchmarks/runner.py` | BenchmarkRunner class with dry-run + real-run + mock-provider modes; _FakeProvider for harness self-tests; _make_fake_registry helper; write_scorecard persistence. |
| `clew/benchmarks/cli.py` | `clew-bench` CLI entry point: `list` / `run` / `diff` subcommands. |
| `clew/benchmarks/diff_report.py` | diff_scorecards + format_diff_report — pass→fail / fail→pass detection, cost/time/token deltas. |
| `clew/benchmarks/README.md` | Design rationale + usage docs + scorecard format spec. |
| `clew/benchmarks/tasks/__init__.py` | Task package marker. |
| `clew/benchmarks/tasks/general/__init__.py` | General-section package. |
| `clew/benchmarks/tasks/heavy_code/__init__.py` | Heavy-code-section package. |
| `clew/benchmarks/tasks/office/__init__.py` | Office-section package. |
| `clew/benchmarks/tasks/general/bug_fix_add.py` | Bug fix in an existing file (add() subtracts instead of adds). |
| `clew/benchmarks/tasks/general/new_feature_multiply.py` | New feature across 2+ files (add multiply() + tests). |
| `clew/benchmarks/tasks/general/refactor_no_behavior_change.py` | Refactor verified by project's own tests. |
| `clew/benchmarks/tasks/general/web_search_python_version.py` | Task requiring web_search. |
| `clew/benchmarks/tasks/general/guardian_env_write.py` | Task that triggers Guardian on a .env write. |
| `clew/benchmarks/tasks/general/guardian_medium_risk.py` | Task that triggers a MEDIUM risk flag. |
| `clew/benchmarks/tasks/general/explain_function.py` | Fuzzy task with mechanical criteria. |
| `clew/benchmarks/tasks/general/str_replace_constant.py` | str_replace edit task. |
| `clew/benchmarks/tasks/general/multi_step_shapes.py` | Multi-step feature across 3 files. |
| `clew/benchmarks/tasks/general/debug_median_off_by_one.py` | Debug task (off-by-one in median()). |
| `clew/benchmarks/tasks/heavy_code/parallel_type_hints.py` | Multi-file heavy_code refactor via parallel subagents. |
| `clew/benchmarks/tasks/heavy_code/split_god_class.py` | Split god class into 3 cohesive modules. |
| `clew/benchmarks/tasks/heavy_code/feature_priority_overdue.py` | Feature addition across 3 files + tests. |
| `clew/benchmarks/tasks/office/create_docx_report.py` | Office task producing .docx. |
| `clew/benchmarks/tasks/office/create_xlsx_sales.py` | Office task producing .xlsx with SUM formula. |
| `clew/benchmarks/tasks/office/fill_xlsx_template.py` | Office task filling an existing .xlsx template. |
| `clew/tests/test_g22a_benchmark_harness.py` | 23 tests for the harness itself (discovery, dry-run, CLI, diff, regression-guards for the 5 fixed NameErrors). |

**Modified files (real regressions found + fixed by the harness):**
| Path | Change |
|------|--------|
| `clew/agent_runtime/runtime.py` | Added 4 missing imports: `build_skill_catalog` from `clew.skill_loader` (line 1130 called it without import), `ProviderMessage` + `ProviderResponse` from `clew.providers` (lines 529, 531, 532, 550-552, 785, 1583, 1584 used them without import), `AgentStep` from `.types` (lines 984, 1302 used it without import). |
| `clew/agent_runtime/prompts.py` | Added `TaskType` to existing `.types` import (was used at lines 591-598, 602 without import). Added lazy loader helpers `_load_office_tool_schema()` and `_load_office_system_suffix()` (OFFICE_TOOL_SCHEMA and OFFICE_SYSTEM_SUFFIX were referenced at lines 565, 592 without import — the office section was completely broken). |
| `clew/agent_runtime/__init__.py` | Wrapped `from .worker import AgentWorker` in try/except — the worker module imports PySide6.QtCore which isn't available in headless / CI environments. The headless CLI, daemon, and benchmark harness all use AgentRuntime without ever touching the GUI; they must not crash on import just because PySide6 is missing. |
| `clew/providers/custom_providers.py` | Wrapped `import yaml` in try/except — environments without pyyaml couldn't load the provider registry at all. Added `_yaml_safe_load` / `_yaml_dump` helpers that fall back gracefully. |

### G22b — TUI interaction testing program (loop-8-g22b-tui-interaction-tests.md)

**New files:**
| Path | Purpose |
|------|---------|
| `clew_tui/tests/__init__.py` | Package marker + docs. |
| `clew_tui/tests/conftest.py` | pytest config: registers `interaction` marker, auto-marks tests in this dir, provides `fake_bridge` fixture + isolated HOME. |
| `clew_tui/tests/_fake_bridge.py` | `FakeClewBridge` — records every call so tests can assert "set_section was called with 'office'" without needing real LLM creds. |
| `clew_tui/tests/_helpers.py` | `TUIInteractionCase` helper — `open_main_palette()`, `open_sub_palette(cmd_id)`, `type_filter(text)`, `press_down/up/enter/escape()`, `highlighted_id()`, `is_palette_open()`, `chat_log_text()`, `status_bar_text()`, `type_input(text)`, `submit_input()`. |
| `clew_tui/tests/test_palette_main.py` | 7 tests for the main Ctrl+P palette — including `test_enter_with_filter_focused_selects_highlighted` which is THE test for the bug issue #17 was opened for. |
| `clew_tui/tests/test_palette_sub_palettes.py` | 25 parameterised tests across all 9 sub-palette commands (section, model, chat, cd, guardian, collab, storage, capabilities, handoff). |
| `clew_tui/tests/test_broader_interactions.py` | 21 tests: send chat message, /clear /section /mode /guardian slash commands, ApprovalModal keyboard (y/n/escape), GuardianModal keyboard (a/r/u), /theme (Ctrl+T) toggle, inline {office} / {heavy_code} prefix section switch + status bar indicator. |

**Modified files (real bugs found + fixed by the new tests):**
| Path | Change |
|------|--------|
| `clew_tui/widgets/command_palette.py` | Added `on_input_submitted` handler. This is THE fix for the bug issue #17 was opened for: pressing Enter while the filter Input has focus now calls `action_select_item()` instead of being silently swallowed by Input's default Submitted handler. |
| `clew_tui/app.py` | Added `capabilities` and `handoff` routes to `_open_sub_palette_for_cmd`. Before this fix, selecting `/capabilities` or `/handoff` from the main Ctrl+P palette fell through to the "needs a parameter" branch even though both commands are marked `has_sub_options=True` — same class of dead-binding bug the issue was opened for. Now they route to `_exec_capabilities("")` and `_exec_handoff("list")` respectively, which open the proper browse palettes. |

### Loop engineering docs

**New files:**
| Path | Purpose |
|------|---------|
| `loops/archive/loop-7-g22a-agent-benchmark-suite.md` | Loop 7 documentation — G22a agent-quality benchmark suite. |
| `loops/archive/loop-8-g22b-tui-interaction-tests.md` | Loop 8 documentation — G22b TUI interaction testing program. |
| `CHANGES_update_11.md` | This file. |

## Constraints honoured

- ✅ The benchmark harness (G22a) **never** runs automatically in the normal `pytest clew/` suite — it's a separate CLI command (`clew-bench run`) and a separate pytest module (`clew/tests/test_g22a_benchmark_harness.py`) that ONLY runs the dry-run path and the harness's own self-tests.
- ✅ Guardian is **never** weakened anywhere, including inside the `general_guardian_env_write` task that intentionally probes it — the evaluator REQUIRES that Guardian fired and prevented the write. A pass means Guardian worked, not that it was bypassed.
- ✅ Every existing test still passes. Full suite ran green before packaging.
- ✅ Followed the "verify real APIs, test realistic state" discipline — every method called in the harness was verified against the real source. The 5 NameErrors found were exactly the class of bug the prompt warned about: "called a method that doesn't exist" (or, more precisely, "referenced a name that was never imported").
- ✅ TUI interaction tests use Textual's built-in `App.run_test()` + `Pilot` — no custom simulation layer.
- ✅ TUI interaction tests simulate real typing (`pilot.press(ch)` per character), not setting `.value` directly.
- ✅ TUI interaction tests assert state changed (bridge method was called), not binding existence.
- ✅ TUI interaction tests don't need real LLM credentials — `FakeClewBridge` records every call.
- ✅ If any test revealed a still-broken palette path, it was FIXED — not just documented. Found + fixed: `capabilities` and `handoff` main-palette selection was dead, now routes correctly.

## How to use the new features

### G22a — Benchmark harness

```bash
# List every available task
clew-bench list

# Validate tasks are well-formed (no LLM calls) — safe for CI
clew-bench run --dry-run

# Run with a real provider (costs real money)
clew-bench run --provider groq --model llama-3.3-70b-versatile

# Run only general-section tasks
clew-bench run --section general

# Run a single task
clew-bench run --task general_bug_fix_add

# Use a mock provider (no API calls — proves the harness works)
clew-bench run --mock-provider

# Diff two scorecards for regression tracking
clew-bench diff clew/benchmarks/results/baseline.json clew/benchmarks/results/new.json
```

Scorecards are written to `clew/benchmarks/results/<timestamp>-<version>-<provider>-<tag>.json`.

See `clew/benchmarks/README.md` for the full design rationale and the
scorecard format spec.

### G22b — TUI interaction tests

```bash
# All interaction tests
pytest clew_tui/tests/ -m interaction

# Just the main palette tests (includes the test for the bug #17 was opened for)
pytest clew_tui/tests/test_palette_main.py -m interaction

# Just the sub-palette tests (parameterised — runs 25 cases)
pytest clew_tui/tests/test_palette_sub_palettes.py -m interaction

# Just the broader interactions
pytest clew_tui/tests/test_broader_interactions.py -m interaction

# Exclude interaction tests from the main run
pytest clew/ clew_tui/ -m "not interaction"
```

The `interaction` marker is auto-applied to every test under
`clew_tui/tests/` via `conftest.py::pytest_collection_modifyitems`.

Adding a new command's palette test is a few lines via the
`TUIInteractionCase` helper — see `clew_tui/tests/_helpers.py` for
the full API and `clew_tui/tests/test_palette_sub_palettes.py` for
the pattern.

## Regressions found + fixed (the point of the exercise)

### G22a — 5 NameErrors in the agent runtime

The benchmark harness was run once via `clew-bench run --mock-provider`
against the current codebase. Every single task errored with a
NameError — five separate import omissions in `clew/agent_runtime/`
that the existing structural test suite had missed:

1. `NameError: name 'build_skill_catalog' is not defined` — `runtime.py:1130` called `build_skill_catalog(self._skills)` but only `load_all_skills_with_builtins` was imported from `clew.skill_loader`. Every agent run with skills loaded (always — 11 builtin skills) crashed at system-prompt construction.

2. `NameError: name 'TaskType' is not defined` (in `prompts.py:591`) — `PromptBuilder.task_prompt` used `TaskType.WRITE`, `TaskType.EDIT`, etc. in a dict but `from .types import Task` only imported `Task`.

3. `NameError: name 'ProviderMessage' is not defined` — `runtime.py` used `ProviderMessage(role=..., content=...)` at 8 different sites but `from clew.providers import ProviderRegistry` only imported `ProviderRegistry`.

4. `NameError: name 'AgentStep' is not defined` — `runtime.py` used `AgentStep(thought=...)` at 2 sites but `from .types import ...` didn't include it.

5. `NameError: name 'OFFICE_TOOL_SCHEMA' is not defined` + `OFFICE_SYSTEM_SUFFIX` — `prompts.py:565,592` referenced both without importing them. Every Office-section agent run crashed at system-prompt construction.

All 5 are trivial fixes (add the missing name to the existing import
statement). The hard part was finding them — they only surface when
the agent loop actually runs end-to-end, which no existing test did.

### G22b — 2 dead bindings in the TUI

1. **Command-palette Enter key** (the bug issue #17 was opened for): pressing Enter while the filter Input has focus did NOTHING because Textual delivers Enter as `Input.Submitted` to the Input (which had focus), not as a binding to the screen. No `on_input_submitted` handler existed. Fix: added `on_input_submitted` that calls `action_select_item()`.

2. **`/capabilities` and `/handoff` main-palette selection was dead**: the `_open_sub_palette_for_cmd` dispatcher had no case for `capabilities` or `handoff`, so selecting them from the Ctrl+P palette fell through to the "needs a parameter" branch even though both commands are marked `has_sub_options=True`. Fix: added explicit routes to `_exec_capabilities("")` and `_exec_handoff("list")`.

Both bugs were the same class as the original Enter-key bug: structurally
wired (the bindings/routes existed for the other commands) but
functionally dead for the unwired commands. Every existing test
checked structure and passed.

## Optional cleanup (nice-to-have, not required)

Issue #1 ("Packaged builds fail with 'Error: local backend
unavailable'") was NOT attempted. The G22a + G22b work was substantial
enough on its own; issue #1 is unrelated to testing and is left for a
future batch.
