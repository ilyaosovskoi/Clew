# Clew Agent-Quality Benchmark Suite (G22a, issue #16)

A runnable harness that spins up the `AgentRuntime` against a fixed
set of self-contained tasks and records pass/fail + cost + time. This
is the "agent quality" layer that the existing ~800 structural tests
do not cover — those verify *code correctness*, this verifies *agent
competence*.

> ⚠️ **Costs real API money.** Never runs in normal `pytest clew/`
> CI. The `--dry-run` mode is the only path that runs in CI — it
> validates every task's starting tree + criteria without calling
> any LLM.

## Quick start

```bash
# List every available task
clew-bench list

# Validate tasks are well-formed (no LLM calls) — safe for CI
clew-bench run --dry-run

# Run with a real provider
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

## Task structure

Each task is a self-contained Python module under
`clew/benchmarks/tasks/<section>/<id>.py`. A task module must export:

```python
from clew.benchmarks import TaskSpec, Section, Difficulty, EvaluationReport

def setup(workspace: str) -> None:
    # Materialise the starting file tree in `workspace`.
    ...

def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    # Inspect the resulting state, return pass/fail.
    ...

def build() -> TaskSpec:
    return TaskSpec(
        id="...",
        section=Section.GENERAL,
        difficulty=Difficulty.EASY,
        prompt="...",
        setup=setup,
        evaluate=evaluate,
        ...
    )
```

The harness:

1. Calls `setup(workspace)` to materialise the starting tree in a
   fresh temp directory.
2. Spins up an `AgentRuntime` pointed at that workspace.
3. Runs the agent against `prompt` to completion or a hard
   iteration/time cap.
4. Calls `evaluate(workspace, agent_output, tool_calls)` and records
   the returned `EvaluationReport`.

## What's covered

The 12 tasks included in this first batch (target: 20-30, expansion
is trivial once the harness is in place) cover:

- **Bug fix in an existing file** (`general_bug_fix_add`)
- **New feature across 2+ files** (`general_new_feature_multiply`,
  `general_multi_step_shapes`)
- **Refactor verified by project tests**
  (`general_refactor_no_behavior_change`)
- **Task requiring `web_search`** (`general_web_search_python_version`)
- **Task that triggers Guardian on a `.env` write**
  (`general_guardian_env_write`)
- **Task that triggers a `medium` risk flag**
  (`general_guardian_medium_risk`)
- **Multi-file `heavy_code` task** (`heavy_code_parallel_type_hints`,
  `heavy_code_split_god_class`, `heavy_code_feature_priority_overdue`)
- **`office` task producing a `.docx`**
  (`office_create_docx_report`)
- **`office` task producing a `.xlsx`** (`office_create_xlsx_sales`,
  `office_fill_xlsx_template`)
- **Fuzzy task with mechanical criterion** (`general_explain_function`)
- **`str_replace` edit** (`general_str_replace_constant`)
- **Debug task** (`general_debug_median_off_by_one`)

The `section` tag (`general` / `heavy_code` / `office`) is mandatory
on every task, so the mix covers all three runtime sections. The
`difficulty` tag (`easy` / `medium` / `hard`) lets the regression
report weight "easy flipped to fail" differently from "hard flipped
to fail".

## Scorecard format

Each run writes a scorecard JSON to
`clew/benchmarks/results/<YYYYMMDD-HHMMSS>-<version>-<provider>-<tag>.json`:

```json
{
  "started_at": "2026-08-04T10:30:00+00:00",
  "finished_at": "2026-08-04T10:42:15+00:00",
  "clew_version": "2.1.0",
  "config": { "provider": "groq", "guardian_level": "off", ... },
  "total_tasks": 12,
  "passed": 9,
  "failed": 2,
  "errored": 1,
  "total_cost_usd": 0.0234,
  "total_tokens_in": 45230,
  "total_tokens_out": 8123,
  "total_wall_clock_s": 735.2,
  "results": [
    {
      "task_id": "general_bug_fix_add",
      "section": "general",
      "difficulty": "easy",
      "passed": true,
      "reason": "all criteria met",
      "wall_clock_s": 12.3,
      "tool_call_count": 4,
      "tokens_in": 820,
      "tokens_out": 145,
      "cost_usd": 0.0012,
      "iterations": 3,
      "provider": "groq",
      "model": "llama-3.3-70b-versatile",
      "checked_criteria": [
        {"name": "calc.py exists", "passed": true},
        ...
      ]
    },
    ...
  ]
}
```

## Regression tracking

`clew-bench diff` compares two scorecards and reports:

- Which tasks flipped **pass→fail** (REGRESSION) and **fail→pass** (FIX).
- Per-task cost/time/token deltas.
- Aggregate pass-rate delta.

Example output:

```
======================================================================
BENCHMARK REGRESSION DIFF
======================================================================

Pass rate:
  baseline: 75.0%   new: 83.3%   delta: +8.3pp

REGRESSIONS (1):
  - general_web_search_python_version: was pass, now FAIL  (cost $0.0023, 42.1s)

FIXES (2):
  + general_debug_median_off_by_one: was fail, now PASS
  + heavy_code_split_god_class: was fail, now PASS
```

The diff JSON is written next to the new scorecard as
`<new_scorecard_stem>.diff.json`.

## Design constraints honoured

- ✅ The harness **never** runs automatically in `pytest clew/`. It's
  a separate CLI command (`clew-bench run`) and a separate pytest
  module (`clew/tests/test_g22a_benchmark_harness.py`) that ONLY runs
  the dry-run path.
- ✅ Guardian is **never** weakened — even in the
  `general_guardian_env_write` task that intentionally probes
  Guardian, the pass criterion is "Guardian fired and prevented the
  write", NOT "Guardian was bypassed".
- ✅ Token / cost tracking reuses the existing `TokenTracker` — no
  second accounting system.
- ✅ Every task's `evaluate()` is **programmatic** — no second LLM
  grading the first one's output. The fuzzy case (`general_explain_function`)
  uses a cheap mechanical check ("did the agent `read_file` on the
  right file AND produce a non-empty `final_answer`").
