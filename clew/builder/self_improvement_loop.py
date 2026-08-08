"""
Self-Improvement Loop — the orchestrator that ties everything together.

Pipeline (per task):

    1. PRE-FLIGHT  — git status clean, NIM pool ready, state loaded
    2. PICK        — next PENDING task (skip DONE ones unless --rerun)
    3. BRANCH      — create builder/task-NN-<slug>
    4. SNAPSHOT    — read files the planner needs to see
    5. PLAN        — NimPool.chat(role="plan") with planner prompt
    6. IMPLEMENT   — AgentRuntime.run(implementer_prompt) on the workspace
    7. VERIFY      — Evaluator.verify() → mechanical checks
    8. REVIEW      — NimPool.chat(role="review") → LLM verdict
    9. COMMIT      — git commit -m "Clew Builder: <title>"
    10. REPORT     — per-task markdown
    11. RESTORE    — checkout original branch (task branch stays for review)

On failure: retry up to max_retries_per_task. Each retry feeds prior
failure reasons back into the planner so the loop doesn't repeat itself.

The whole thing is wrapped in try/except so a single task's failure
never aborts the entire run. The state file is the source of truth —
kill / resume any time with `--continue` (the default).
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .evaluator import Evaluator, EvaluatorConfig, VerificationResult
from .git_workspace import GitWorkspace
from .nim_pool import NimPool, NimPoolConfig, make_nim_pool
from .prompts import (
    PLANNER_SYSTEM,
    REVIEWER_SYSTEM,
    build_implementer_prompt,
    build_planner_prompt,
    build_reviewer_prompt,
    format_prior_failures,
)
from .reporter import Reporter, ReporterPaths
from .state import BuilderState, TaskAttempt, TaskStatus
from .task_list import Task, TaskList

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────

@dataclass
class BuilderConfig:
    """Top-level configuration for run_builder()."""
    # Required
    tasks_path: str
    workspace: str

    # NIM
    nim_api_key: Optional[str] = None
    nim_rpm_limit: int = 35
    nim_models: Dict[str, str] = field(default_factory=dict)

    # Loop behaviour
    max_tasks: Optional[int] = None          # cap total tasks processed
    max_retries_per_task: int = 2            # extra attempts after the first
    max_iterations_per_implement: int = 20   # AgentRuntime max_iterations
    continue_run: bool = True                # resume from state file
    fresh_state: bool = False                # delete state file first
    skip_pytest: bool = False
    dry_run: bool = False                    # plan only, no implementation

    # Where things go
    state_path: Optional[str] = None         # default: <workspace>/.clew/builder_state.json
    reports_dir: Optional[str] = None        # default: <workspace>/.clew/builder_reports/

    # Provider for the AgentRuntime (the implementer). Defaults to NIM.
    implementer_provider: str = "nvidia_nim"
    implementer_model: Optional[str] = None  # default: same as pool's "implement" role

    # Optional: which files to include in the planner snapshot. If None,
    # the loop reads a curated set of "core" clew files relevant to the task.
    snapshot_files: Optional[List[str]] = None

    # Verbose logging on stderr.
    verbose: bool = False


@dataclass
class BuilderReport:
    """Returned by run_builder()."""
    started_at: datetime.datetime
    ended_at: datetime.datetime
    tasks_total: int
    tasks_done: int
    tasks_failed: int
    tasks_skipped: int
    nim_stats: Dict[str, Any]
    summary_path: str
    state_summary: Dict[str, Any]


# ── File snapshot helper ─────────────────────────────────────────

# Curated map of "task keyword" → list of files the planner should see.
# This is a heuristic — the planner still gets to call search_project at
# implement time. The snapshot just primes it with the right context.
_KEYWORD_FILE_MAP: List[tuple] = [
    ("context",          ["clew/agent_runtime/context_memory.py",
                          "clew/agent_runtime/runtime.py",
                          "clew/context_manager.py"]),
    ("compaction",       ["clew/agent/compaction_v2.py",
                          "clew/agent/_fallback_compaction.py"]),
    ("autocomplete",     ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("fim",              ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("search",           ["clew/web_search_backend.py",
                          "clew/agent_runtime/tool_engine/_engine.py"]),
    ("guardian",         ["clew/agent/guardian.py",
                          "clew/agent_runtime/tool_engine/_engine.py"]),
    ("plugin",           ["clew/plugins/__init__.py",
                          "clew/api_server.py"]),
    ("manifest",         ["clew/plugins/__init__.py"]),
    ("signing",          ["clew/audit_signing.py",
                          "clew/plugins/__init__.py"]),
    ("sigkill",          ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("temp",             ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("path",             ["clew/agent_runtime/_helpers.py",
                          "clew/command_policy.py"]),
    ("circuit",          ["clew/agent/circuit_breaker.py",
                          "clew/agent/_fallback_circuit_breaker.py"]),
    ("half_open",        ["clew/agent/circuit_breaker.py",
                          "clew/agent/_fallback_circuit_breaker.py"]),
    ("head",             ["clew/web_server.py"]),
    ("mcp",              ["clew/mcp_manager.py",
                          "clew/mcp_server.py",
                          "clew/mcp_client.py"]),
    ("thread",           ["clew/mcp_manager.py"]),
    ("tab",              ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("ast",              ["clew/agent_runtime/tool_engine/_engine.py",
                          "clew/agent_runtime/parser.py"]),
    ("chunk",            ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("cache",            ["clew/web_search_backend.py"]),
    ("billing",          ["clew/spend_dashboard.py",
                          "clew/cost_router.py"]),
    ("landing",          ["clew/web/index.html",
                          "clew/web_server.py"]),
    ("waitlist",         ["clew/web/index.html"]),
    ("marketplace",      ["clew/plugins/__init__.py"]),
    ("audit",            ["clew/audit_signing.py",
                          "clew/web/index.html"]),
    ("dashboard",        ["clew/web/index.html",
                          "clew/spend_dashboard.py"]),
    ("inline",           ["clew/agent_runtime/tool_engine/_engine.py"]),
    ("edit",             ["clew/agent_runtime/tool_engine/_engine.py"]),
]


def _select_snapshot_files(task: Task, workspace: str) -> List[str]:
    """Pick which files to include in the planner snapshot for this task."""
    title_lower = task.title.lower() + " " + task.raw.lower()
    picked: List[str] = []
    seen = set()
    for keyword, files in _KEYWORD_FILE_MAP:
        if keyword in title_lower:
            for f in files:
                if f not in seen:
                    picked.append(f)
                    seen.add(f)
    # Always include the cli + tool_engine entry — almost every task
    # ends up touching one of them.
    for f in ["clew/cli.py", "clew/agent_runtime/tool_engine/_engine.py"]:
        if f not in seen:
            picked.append(f)
            seen.add(f)
    return picked


def _read_snapshot(files: List[str], workspace: str, max_chars_per_file: int = 8000) -> str:
    """Read up to max_chars_per_file of each file, concatenated."""
    parts: List[str] = []
    for rel in files:
        p = Path(workspace) / rel
        if not p.exists():
            parts.append(f"# --- {rel} (not found) ---")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            parts.append(f"# --- {rel} (read error: {e}) ---")
            continue
        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file] + f"\n... (truncated, {len(text) - max_chars_per_file} more chars)\n"
        parts.append(f"# --- {rel} ({len(text)} chars) ---\n{text}")
    return "\n\n".join(parts)


# ── Verdict parsing ───────────────────────────────────────────────

def _parse_reviewer_verdict(text: str) -> tuple[bool, str]:
    """Parse the reviewer's `VERDICT: PASS|FAIL` line. Returns (passed, reason)."""
    if not text:
        return False, "no reviewer output"
    # Find the verdict line.
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.IGNORECASE)
    if not m:
        # Fall back: treat as fail with the full text as reason.
        return False, text.strip().split("\n", 1)[0][:200]
    verdict = m.group(1).upper()
    # Extract reason (line after REASON:, or first line after verdict).
    reason = ""
    r = re.search(r"REASON:\s*(.+)", text, re.IGNORECASE)
    if r:
        reason = r.group(1).strip()[:300]
    return verdict == "PASS", reason


# ── AgentRuntime shim ─────────────────────────────────────────────

def _run_implementer(
    config: BuilderConfig,
    pool: NimPool,
    task: Task,
    plan: str,
) -> tuple[str, int, int, str]:
    """Run the AgentRuntime to implement the plan.

    Returns (final_text, tokens_in, tokens_out, model_used).
    """
    from ..cli import _build_registry  # reuse the CLI's registry builder
    from ..agent_runtime import AgentRuntime, TaskType
    import argparse

    # Build a fake argparse.Namespace so we can reuse _build_registry.
    implement_model = (
        config.implementer_model
        or pool._cfg.default_models.get("implement")
        or "meta/llama-3.1-70b-instruct"
    )
    ns = argparse.Namespace(
        provider=config.implementer_provider,
        model=implement_model,
        api_key=config.nim_api_key,
        api_base=None,
        temperature=0.2,
        max_tokens=8192,
        context_window=None,
        workspace=config.workspace,
    )
    registry = _build_registry(ns)

    prompt = build_implementer_prompt(task.title, plan, task.success_criteria)

    agent = AgentRuntime(
        registry=registry,
        workspace=config.workspace,
        max_iterations=config.max_iterations_per_implement,
        enable_planning=False,  # planner already produced the plan
        on_event=_make_event_sink(task),
        section="heavy_code",
    )
    # Autonomous — never ask the user mid-loop.
    try:
        agent.set_autonomy("never_ask")
    except Exception:
        pass

    result = agent.run(prompt, task_type=TaskType.AGENTIC)
    final_text = (result.output or "") if hasattr(result, "output") else str(result)
    tokens_in = getattr(result, "tokens_in", 0) or 0
    tokens_out = getattr(result, "tokens_out", 0) or 0
    return final_text, tokens_in, tokens_out, implement_model


def _make_event_sink(task: Task) -> Callable[[Any, Dict[str, Any]], None]:
    """Stream AgentRuntime events to stderr so the user can watch progress."""
    def _sink(event, data):
        try:
            from ..agent_runtime import AgentEvent
            if event == AgentEvent.ITERATION_START:
                print(f"      [iter {data.get('iteration')}/{data.get('max')}]", file=sys.stderr)
            elif event == AgentEvent.TOOL_CALLED:
                tool = data.get("tool", "?")
                print(f"        → {tool}", file=sys.stderr)
            elif event == AgentEvent.TOOL_RESULT:
                r = str(data.get("result", ""))[:120].replace("\n", " ")
                print(f"        ← {r}", file=sys.stderr)
            elif event == AgentEvent.ERROR:
                print(f"        [ERR] {data.get('error', '')[:200]}", file=sys.stderr)
        except Exception:
            pass
    return _sink


# ── Main entry point ──────────────────────────────────────────────

def run_builder(config: BuilderConfig) -> BuilderReport:
    """Main entrypoint. Runs the autonomous self-improvement loop."""
    started_at = datetime.datetime.utcnow()

    # ── Resolve paths ─────────────────────────────────────────────
    workspace = str(Path(config.workspace).resolve())
    state_path = config.state_path or str(Path(workspace) / ".clew" / "builder_state.json")
    reports_dir = config.reports_dir or str(Path(workspace) / ".clew" / "builder_reports")

    if config.fresh_state and Path(state_path).exists():
        Path(state_path).unlink()
        print(f"[builder] fresh-state: deleted {state_path}", file=sys.stderr)

    # ── Load tasks ────────────────────────────────────────────────
    from .task_list import parse_task_file
    task_list = parse_task_file(config.tasks_path)
    print(f"[builder] loaded {len(task_list)} tasks from {config.tasks_path}", file=sys.stderr)

    # ── Init state + reporter + git + pool + evaluator ────────────
    state = BuilderState(state_path)
    reporter = Reporter(ReporterPaths(workspace=workspace, reports_dir=reports_dir))
    git = GitWorkspace(workspace=workspace)
    git.ensure_repo()
    pool = make_nim_pool(
        api_key=config.nim_api_key,
        rpm_limit=config.nim_rpm_limit,
        model_overrides=config.nim_models or None,
    )
    evaluator = Evaluator(EvaluatorConfig(
        workspace=workspace,
        skip_pytest=config.skip_pytest,
    ))

    # ── Iterate tasks ─────────────────────────────────────────────
    tasks_to_run = list(task_list)
    if config.max_tasks is not None:
        tasks_to_run = tasks_to_run[:config.max_tasks]

    for idx, task in enumerate(tasks_to_run, 1):
        # Skip DONE tasks on resume (unless --fresh-state already cleared).
        if config.continue_run and state.is_done(task.slug):
            print(f"[builder] [{idx}/{len(tasks_to_run)}] SKIP (already done): {task.title}", file=sys.stderr)
            state.mark_skipped(task.slug, "already done in prior run")
            continue

        attempts_used = state.attempts_used(task.slug)
        if attempts_used >= (config.max_retries_per_task + 1):
            print(f"[builder] [{idx}/{len(tasks_to_run)}] SKIP (exhausted retries): {task.title}", file=sys.stderr)
            continue

        print(f"\n[builder] [{idx}/{len(tasks_to_run)}] TASK: {task.title}", file=sys.stderr)
        _process_task(
            idx, task, config, state, pool, git, evaluator, reporter, workspace,
        )

    # ── Summary ──────────────────────────────────────────────────
    ended_at = datetime.datetime.utcnow()
    nim_stats = pool.stats()
    state_summary = state.summary()
    summary_path = reporter.write_summary(
        records=state.all_records(),
        nim_stats=nim_stats,
        started_at=started_at,
        ended_at=ended_at,
    )
    by_status = state_summary.get("by_status", {})
    print(f"\n[builder] done. summary → {summary_path}", file=sys.stderr)
    print(f"[builder]   done={by_status.get('done', 0)} "
          f"failed={by_status.get('failed', 0)} "
          f"skipped={by_status.get('skipped', 0)}", file=sys.stderr)

    return BuilderReport(
        started_at=started_at,
        ended_at=ended_at,
        tasks_total=len(tasks_to_run),
        tasks_done=by_status.get("done", 0),
        tasks_failed=by_status.get("failed", 0),
        tasks_skipped=by_status.get("skipped", 0),
        nim_stats=nim_stats,
        summary_path=str(summary_path),
        state_summary=state_summary,
    )


# ── Per-task processing ──────────────────────────────────────────

def _process_task(
    task_num: int,
    task: Task,
    config: BuilderConfig,
    state: BuilderState,
    pool: NimPool,
    git: GitWorkspace,
    evaluator: Evaluator,
    reporter: Reporter,
    workspace: str,
) -> None:
    """Process a single task with retries."""
    record = state.get_or_create(task.title, task.slug)
    max_attempts = config.max_retries_per_task + 1

    for attempt_idx in range(max_attempts):
        if state.is_done(task.slug):
            return
        if state.attempts_used(task.slug) >= max_attempts:
            break

        attempt = state.begin_attempt(task.slug, branch="")
        branch = git.begin_task_branch(task_num, task.slug)
        attempt.branch = branch

        print(f"  [builder] attempt #{attempt.attempt_number} on branch {branch}", file=sys.stderr)

        try:
            # ── 1. PLAN ────────────────────────────────────────────
            snapshot_files = config.snapshot_files or _select_snapshot_files(task, workspace)
            snapshot = _read_snapshot(snapshot_files, workspace)
            prior_failures = format_prior_failures(record.attempts[:-1])  # exclude current
            planner_prompt = build_planner_prompt(
                task_title=task.title,
                task_raw=task.raw,
                success_criteria=task.success_criteria,
                file_snapshot=snapshot,
                prior_failures=prior_failures,
            )
            print(f"  [builder] planning with {pool._cfg.default_models.get('plan', '?')}…", file=sys.stderr)
            plan_resp = pool.chat(
                planner_prompt,
                role="plan",
                system=PLANNER_SYSTEM,
                max_tokens=2048,
            )
            plan_text = plan_resp.text.strip()
            attempt.plan = plan_text
            attempt.tokens_in += plan_resp.tokens_in
            attempt.tokens_out += plan_resp.tokens_out
            attempt.model_used = plan_resp.model
            print(f"  [builder] plan: {plan_text.splitlines()[0][:100]}…", file=sys.stderr)

            if config.dry_run:
                print(f"  [builder] dry-run: skipping implementation", file=sys.stderr)
                state.finish_attempt(
                    task.slug, attempt,
                    success=False,
                    verification_output="dry-run: no implementation",
                    error="dry-run",
                    tokens_in=attempt.tokens_in,
                    tokens_out=attempt.tokens_out,
                    model_used=attempt.model_used,
                )
                git.restore_original_branch()
                return

            # ── 2. IMPLEMENT ───────────────────────────────────────
            print(f"  [builder] implementing via AgentRuntime…", file=sys.stderr)
            impl_text, impl_tin, impl_tout, impl_model = _run_implementer(
                config, pool, task, plan_text,
            )
            attempt.tokens_in += impl_tin
            attempt.tokens_out += impl_tout
            attempt.model_used = f"{attempt.model_used} + {impl_model}"
            print(f"  [builder] implementer done: {impl_text.splitlines()[-1][:100] if impl_text else '(empty)'}", file=sys.stderr)

            # ── 3. VERIFY (mechanical) ─────────────────────────────
            diff = git.diff_since_branch_start()
            print(f"  [builder] verifying ({len(git.files_changed_since_start())} files changed)…", file=sys.stderr)
            verification: VerificationResult = evaluator.verify(
                diff=diff,
                success_criteria=task.success_criteria,
            )
            print(f"  [builder] verification: {'PASS' if verification.passed else 'FAIL'} ({verification.duration_secs}s)", file=sys.stderr)

            # ── 4. REVIEW (LLM) ────────────────────────────────────
            reviewer_prompt = build_reviewer_prompt(
                task_title=task.title,
                plan=plan_text,
                diff=diff,
                verification_output=verification.format_for_reviewer(),
                success_criteria=task.success_criteria,
            )
            review_resp = pool.chat(
                reviewer_prompt,
                role="review",
                system=REVIEWER_SYSTEM,
                max_tokens=512,
            )
            attempt.tokens_in += review_resp.tokens_in
            attempt.tokens_out += review_resp.tokens_out
            verdict_passed, verdict_reason = _parse_reviewer_verdict(review_resp.text)
            print(f"  [builder] reviewer: {'PASS' if verdict_passed else 'FAIL'} — {verdict_reason[:80]}", file=sys.stderr)

            # ── 5. COMMIT (regardless of pass/fail — keep the work) ─
            files_changed = git.files_changed_since_start()
            attempt.files_changed = files_changed
            try:
                commit_msg = f"Clew Builder: {task.title}\n\nTask #{task_num:02d} attempt #{attempt.attempt_number}\nVerdict: {'PASS' if verdict_passed else 'FAIL'}"
                git.commit_all(commit_msg)
            except Exception as e:
                logger.warning("[builder] git commit failed: %s", e)

            # ── 6. DECIDE ──────────────────────────────────────────
            overall_pass = verification.passed and verdict_passed
            combined_output = (
                f"=== Plan ===\n{plan_text}\n\n"
                f"=== Implementer output ===\n{impl_text[:3000]}\n\n"
                f"=== Verification ===\n{verification.format_for_reviewer()}\n\n"
                f"=== Reviewer verdict ===\n{review_resp.text}\n"
            )

            state.finish_attempt(
                task.slug, attempt,
                success=overall_pass,
                files_changed=files_changed,
                verification_output=combined_output,
                error=None if overall_pass else (verdict_reason or verification.error or "verification failed"),
                tokens_in=attempt.tokens_in,
                tokens_out=attempt.tokens_out,
                model_used=attempt.model_used,
            )

            # Write per-task report.
            reporter.write_task_report(
                task_num=task_num,
                record=record,
                reviewer_verdict=review_resp.text,
            )

            if overall_pass:
                print(f"  [builder] ✓ TASK DONE on attempt #{attempt.attempt_number}", file=sys.stderr)
                git.restore_original_branch()
                return

            # Failed — retry.
            print(f"  [builder] ✗ attempt #{attempt.attempt_number} failed — retrying", file=sys.stderr)
            git.restore_original_branch()
            # Small delay to give the NIM pool breathing room.
            time.sleep(2.0)

        except KeyboardInterrupt:
            print(f"\n[builder] interrupted by user — state saved", file=sys.stderr)
            state.finish_attempt(
                task.slug, attempt,
                success=False,
                error="interrupted by user (KeyboardInterrupt)",
                tokens_in=attempt.tokens_in,
                tokens_out=attempt.tokens_out,
                model_used=attempt.model_used,
            )
            git.restore_original_branch()
            raise
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("[builder] task crashed: %s\n%s", e, tb)
            state.finish_attempt(
                task.slug, attempt,
                success=False,
                error=f"{type(e).__name__}: {e}",
                verification_output=tb[:4000],
                tokens_in=attempt.tokens_in,
                tokens_out=attempt.tokens_out,
                model_used=attempt.model_used,
            )
            reporter.write_task_report(
                task_num=task_num,
                record=record,
                reviewer_verdict=f"VERDICT: FAIL\nREASON: exception during execution\nNEXT_STEP: {e}",
            )
            try:
                git.restore_original_branch()
            except Exception:
                pass
            time.sleep(2.0)

    # Out of retries.
    print(f"  [builder] ✗ TASK FAILED after {max_attempts} attempts", file=sys.stderr)
