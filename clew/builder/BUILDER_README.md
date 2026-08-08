# Clew Builder — Autonomous Self-Improvement Loop

> **v2.2.3** — A meta-agent that uses Clew to improve Clew.

Clew Builder reads a plain-text task list and autonomously implements each
task against Clew's own source code. It plans, edits, verifies, reviews,
commits, and reports — without human intervention between tasks.

This is the "real test of agent quality" the user asked for: hand Clew a
list of TODOs and watch it grind through them, learning from its own
failures within a single run.

---

## Why this is interesting

* **Eats its own dogfood.** The Builder uses Clew's existing
  `AgentRuntime` + `ToolEngine` + `Guardian` + `sandbox` + `audit trail`
  to modify Clew itself. Every safety mechanism still applies — the
  Builder is not a bypass, it's a *client* of the agent runtime.
* **Realistic test of agent quality.** Llama-3.1-70b on Nvidia NIM is a
  competent but not frontier model. Watching it succeed or fail on real
  engineering tasks (circuit-breaker fixes, AST chunking, plugin
  manifests) is a much harder benchmark than synthetic SWE-bench-style
  tasks — and the per-task markdown reports make the failure modes
  inspectable.
* **Rate-limit-safe by design.** NIM's free tier caps at 40 RPM. The
  pool underbudgets to 35 RPM with a sliding-window throttle, so the
  loop never trips the limit even with background traffic.
* **Resumable + observable.** State persists to
  `<workspace>/.clew/builder_state.json`. Kill the loop any time;
  re-run and it skips DONE tasks. Per-task reports land in
  `<workspace>/.clew/builder_reports/`. Final summary at
  `_summary.md`.

---

## Architecture

```
tasks.txt
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Builder Loop  (clew/builder/self_improvement_loop.py)  │
│                                                          │
│  for task in tasks:                                      │
│    1. BRANCH   git checkout -B builder/task-NN-<slug>    │
│    2. SNAPSHOT read relevant source files                │
│    3. PLAN     NimPool.chat(role="plan", 70b)            │
│    4. IMPLEMENT AgentRuntime.run(implementer_prompt)     │
│                ↑ uses NIM 70b via ProviderRegistry       │
│    5. VERIFY   Evaluator.verify()                        │
│                - import clew                             │
│                - python -m clew.cli status               │
│                - pytest clew/tests/ -x                   │
│                - success-criteria heuristic              │
│    6. REVIEW   NimPool.chat(role="review", 70b)          │
│                → VERDICT: PASS | FAIL + REASON           │
│    7. COMMIT   git commit -m "Clew Builder: <title>"     │
│    8. REPORT   builder_reports/NN-slug.md                │
│    9. RESTORE  git checkout <original branch>            │
│                                                          │
│  on failure: retry up to max_retries (default 2)         │
│  each retry feeds prior failure reasons into the planner │
└─────────────────────────────────────────────────────────┘
```

### Files in `clew/builder/`

| File | Purpose |
|------|---------|
| `__init__.py` | Public API: `run_builder(config) -> BuilderReport` |
| `task_list.py` | Parses `tasks.txt` → list of `Task(title, success_criteria, raw)` |
| `nim_pool.py` | Rate-limited (35 RPM) wrapper around `NvidiaNIMProvider` with role→model routing |
| `state.py` | JSON state file: task → attempts → status (PENDING/IN_PROGRESS/DONE/FAILED/SKIPPED) |
| `prompts.py` | System + user prompts for the PLANNER, IMPLEMENTER, REVIEWER roles |
| `git_workspace.py` | Per-task branch management: `begin_task_branch`, `commit_all`, `diff_since_branch_start` |
| `evaluator.py` | Mechanical verification: import smoke, CLI smoke, pytest, criteria heuristic |
| `reporter.py` | Writes per-task markdown + final `_summary.md` |
| `self_improvement_loop.py` | The orchestrator that ties it all together |

---

## Quick start

### 1. Set up

```bash
# Inside the clew_v2.2.3_builder workspace:
export NVIDIA_API_KEY="nvapi-..."

# Make sure the workspace is a git repo (the loop will init one if not)
cd /path/to/clew_v2.2.3_builder
git init && git add -A && git commit -m "init"
```

### 2. Write a task list

Use `builder_tasks.example.txt` as a template. Plain-list mode (one task
per non-empty line) is the fastest:

```
Fix the HALF_OPEN circuit breaker probe race
Add a PluginManifest dataclass to plugins/__init__.py
Make Guardian fail-closed when the LLM review errors out
```

Or rich mode with success criteria (separated by blank lines):

```
## Fix HALF_OPEN probe race
- circuit_breaker.py allows only ONE probe in HALF_OPEN
- second concurrent probe blocks until the first completes

## Add PluginManifest
- plugins/__init__.py defines PluginManifest dataclass
- includes name, version, signature fields
```

### 3. Run the loop

```bash
python -m clew.cli builder \
  --tasks builder_tasks.example.txt \
  --workspace . \
  --provider nvidia_nim \
  --rpm-limit 35 \
  --max-retries 2 \
  --skip-pytest          # optional: skip the slow pytest layer
```

### 4. Watch progress

The loop prints to stderr in real time:

```
[builder] [1/19] TASK: Virtual 1M+ Context Module
  [builder] attempt #1 on branch builder/task-01-virtual-1m-context-module
  [builder] planning with meta/llama-3.1-70b-instruct…
  [builder] plan: PLAN: 1. Add context_window field to ProviderConfig...
  [builder] implementing via AgentRuntime…
      [iter 1/20]
        → read_file
        ← ...
        → str_replace
        ← ok
  [builder] verifying (3 files changed)…
  [builder] verification: PASS (4.2s)
  [builder] reviewer: PASS — diff matches plan, all criteria met
  [builder] ✓ TASK DONE on attempt #1
```

### 5. Inspect results

Per-task reports:

```bash
ls .clew/builder_reports/
# 01-virtual-1m-context-module.md
# 02-inline-edit-cmd-k-analog.md
# ...
# _summary.md
```

Final summary:

```bash
cat .clew/builder_reports/_summary.md
```

Git branches (each task's work is isolated):

```bash
git branch | grep builder/
# builder/task-01-virtual-1m-context-module
# builder/task-02-inline-edit-cmd-k-analog
# ...
```

### 6. Merge what you want to keep

```bash
git checkout main
git merge builder/task-01-virtual-1m-context-module
# review the diff first with: git log builder/task-01-... ^main
```

---

## CLI flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--tasks` | (required) | Path to the task list file |
| `--workspace` | `.` | Project root to modify |
| `--provider` | `nvidia_nim` | Provider for the implementer agent |
| `--model` | (NIM default) | Override the implementer model |
| `--api-key` | `NVIDIA_API_KEY` env | Override the API key |
| `--rpm-limit` | `35` | NIM requests-per-minute cap |
| `--max-tasks` | all | Cap total tasks processed |
| `--max-retries` | `2` | Extra attempts per task (3 total) |
| `--max-iterations` | `20` | AgentRuntime iterations per implement step |
| `--skip-pytest` | off | Skip the pytest verification layer |
| `--dry-run` | off | Plan only — don't implement |
| `--fresh-state` | off | Delete state file before starting |
| `--no-continue` | off | Don't resume from state file |
| `--state-path` | `<workspace>/.clew/builder_state.json` | Override state file |
| `--reports-dir` | `<workspace>/.clew/builder_reports/` | Override reports dir |
| `--verbose` `-v` | off | DEBUG logging |

---

## Safety model

The Builder does **not** weaken any of Clew's safety mechanisms:

* **Guardian** still runs on every tool call. If the implementer agent
  tries to do something risky (delete a file outside workspace, run a
  shell command not on the whitelist), Guardian will REJECT it.
* **Sandbox** (Rust Landlock/Seatbelt if available, Python fallback
  otherwise) restricts file writes to `--workspace`.
* **Audit trail** (Ed25519-signed hash chain) records every operation.
* **No remote push.** All commits are local. The user reviews branches
  and merges manually.

The Builder's only "extra" autonomy is `autonomy="never_ask"` on the
implementer AgentRuntime — meaning it doesn't block waiting for a
human to click "approve" on each diff. Guardian risk assessment still
runs; only the *interactive confirmation gate* is skipped (this is the
same mode used by Hermes / CI / cron use-cases).

---

## What to expect from a real run

Based on the difficulty distribution of the sample tasks:

* **Easy wins** (1-3 file edits, mechanical fixes): HALF_OPEN fix,
  HEAD method fix, MCP thread safety, search permission handling —
  these should mostly succeed on attempt #1.
* **Medium tasks** (new functions + wiring): FIM autocomplete stub,
  AST chunking, search caching, Pro Tier stub — should mostly succeed
  within 2 attempts.
* **Hard tasks** (architecture changes, new subsystems): Plugin
  Marketplace, Audit Dashboard UI, Landing Page + Waitlist — these
  will probably need multiple retries and may fail. The reports will
  show exactly why.

That's the point. The Builder is a **measurable** test of agent
quality: count tasks done on first attempt vs. tasks needing retries
vs. tasks that exhausted retries. The `_summary.md` file gives you
those numbers directly.

---

## Tuning

### Model routing

The default role → model map (in `nim_pool.py`):

```python
DEFAULT_MODEL_FOR_ROLE = {
    "plan":      "meta/llama-3.1-70b-instruct",
    "implement": "meta/llama-3.1-70b-instruct",
    "review":    "meta/llama-3.1-70b-instruct",
    "quick":     "meta/llama-3.1-8b-instruct",
}
```

Override per-run with `--model` (for the implementer only) or by
editing `BuilderConfig.nim_models` programmatically.

### Rate limit

The 35 RPM cap leaves 5 RPM of headroom under the 40 RPM NIM free-tier
limit. If you have a paid NIM tier with higher limits, raise it:

```bash
python -m clew.cli builder --tasks ... --rpm-limit 200
```

### Retries

`--max-retries 2` (3 attempts total) is the default. Each retry feeds
the prior failure reason back into the planner prompt, so the loop
doesn't repeat itself. Set `--max-retries 0` for "try once, move on".

### Snapshot size

The planner reads a curated set of source files based on task keywords
(see `_KEYWORD_FILE_MAP` in `self_improvement_loop.py`). Each file is
truncated to 8000 chars. If you want to give the planner more context,
edit `_KEYWORD_FILE_MAP` or pass `BuilderConfig.snapshot_files=[...]`
programmatically.

---

## Limitations / known sharp edges

1. **No automatic merge.** The Builder creates branches but never
   merges them. You review and merge manually. This is intentional —
   autonomous code merging without review is irresponsible.

2. **No cross-task context.** Each task is implemented in isolation.
   If task #5 depends on task #3 being merged, you must merge task #3
   to `main` before running task #5. Re-running the loop after a
   manual merge will pick up where it left off.

3. **The reviewer is the same model as the planner.** This is a
   self-evaluation bias. For production use, consider wiring the
   reviewer to a stronger model (e.g. Claude via Anthropic provider).

4. **Success-criteria heuristic is fuzzy.** The `_check_criteria_heuristic`
   in `evaluator.py` is a substring match — it gives the reviewer a
   hint, not a verdict. The reviewer LLM is the real judge.

5. **NIM context windows vary by model.** The pool uses each model's
   declared context window from `nvidia_nim.py::_MODEL_CONTEXT_WINDOWS`.
   If you use a model not in that dict, the default 131_072 is used.

6. **The implementer AgentRuntime uses its own provider** (also NIM by
   default), separate from the planner/reviewer pool. They share the
   same API key but have independent rate limits — NIM's quota is
   account-wide, so the loop's 35 RPM cap covers both.

---

## Integration with the existing Loop Engineering methodology

Clew already has a "Loop Engineering" methodology (see
`Loop_Engineering_Guide.md` and `Loops_Library.md`) — markdown files in
`loops/active/` track each engineering loop's success criteria.

The Builder is compatible with that methodology:

* Per-task reports land in `.clew/builder_reports/` — you can copy them
  to `loops/active/LOOP-FEAT-<date>-<slug>.md` if you want them in the
  loop archive.
* The `_summary.md` file is a ready-made input for the weekly system
  review (`reviews/<date>_weekly_review.md`).
- The Builder's `BuilderState` is conceptually equivalent to the Loop
  Engineering "active/archive" lifecycle, just in JSON instead of
  markdown.
