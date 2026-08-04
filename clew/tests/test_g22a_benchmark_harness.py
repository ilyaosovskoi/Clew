#!/usr/bin/env python3
"""G22a — Benchmark harness test suite.

These tests verify the harness ITSELF works correctly — they do NOT
exercise agent quality (that's the harness's job, run separately via
``clew-bench run``). They cover:

  1. Task discovery finds every task module under
     ``clew/benchmarks/tasks/``.
  2. Every task's ``build()`` returns a well-formed ``TaskSpec``.
  3. Every task's ``setup()`` + ``evaluate()`` round-trip without
     crashing (the dry-run path).
  4. The ``BenchmarkRunner`` dry-run reports every task as well-formed.
  5. The CLI ``list`` and ``run --dry-run`` subcommands work.
  6. The ``diff_scorecards`` helper correctly detects pass→fail and
     fail→pass transitions between two scorecards.
  7. The ``--mock-provider`` mode produces a scorecard with the
     expected shape (a smoke test, not a quality measurement).
  8. Coverage requirement: at least one task in each of general,
     heavy_code, office sections.
  9. Coverage requirement: at least one task tagged with each of
     bug_fix, new_feature, refactor, web_search, guardian, office,
     heavy_code.

This file is the ONLY test file that touches the benchmarks package
from CI. It deliberately does NOT call ``clew-bench run`` without
``--dry-run`` or ``--mock-provider`` — that costs real money.

Run:
    pytest clew/tests/test_g22a_benchmark_harness.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


# ── Test isolation ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect ~/.clew to a temp dir so tests don't clobber the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    yield


# ── 1. Task discovery ─────────────────────────────────────────────────


def test_discover_task_modules_finds_at_least_12():
    """discover_task_modules() must find ≥12 task modules."""
    from clew.benchmarks import discover_task_modules
    modules = discover_task_modules()
    assert len(modules) >= 12, f"only found {len(modules)} task modules"
    # All must be under clew.benchmarks.tasks.
    for m in modules:
        assert m.startswith("clew.benchmarks.tasks."), m


def test_load_all_tasks_returns_well_formed_specs():
    """load_all_tasks() must return a list of TaskSpec objects."""
    from clew.benchmarks import load_all_tasks, TaskSpec
    tasks = load_all_tasks()
    assert len(tasks) >= 12
    for t in tasks:
        assert isinstance(t, TaskSpec)
        assert t.id, "task must have an id"
        assert isinstance(t.section, type(t.section))  # enum
        assert t.section.value in ("general", "heavy_code", "office")
        assert t.difficulty.value in ("easy", "medium", "hard")
        assert isinstance(t.prompt, str) and len(t.prompt) > 10
        assert callable(t.setup)
        assert callable(t.evaluate)


# ── 2. Section coverage ──────────────────────────────────────────────


def test_tasks_cover_all_three_sections():
    """At least one task must exist for each of general / heavy_code / office."""
    from clew.benchmarks import load_all_tasks
    tasks = load_all_tasks()
    sections = {t.section.value for t in tasks}
    assert "general" in sections
    assert "heavy_code" in sections
    assert "office" in sections


def test_tasks_cover_required_categories():
    """The task set must include at least one task per required category
    (tagged). Required tags come from issue #16:
    bug_fix, new_feature, refactor, web_search, guardian, office, heavy_code.
    """
    from clew.benchmarks import load_all_tasks
    tasks = load_all_tasks()
    all_tags = set()
    for t in tasks:
        all_tags.update(t.tags)
    # Required tag categories per the issue.
    required = {
        "bug_fix",
        "new_feature",
        "refactor",
        "web_search",
        "guardian",
        "office",
        "heavy_code",
    }
    missing = required - all_tags
    assert not missing, f"missing required tag categories: {missing}"


def test_tasks_include_guardian_env_write_probe():
    """The guardian_env_write task must exist (it's the explicit Guardian
    probe mentioned in issue #16)."""
    from clew.benchmarks import load_all_tasks
    tasks = load_all_tasks()
    ids = {t.id for t in tasks}
    assert "general_guardian_env_write" in ids


def test_tasks_include_web_search_task():
    """At least one task must require web_search / web_fetch (issue #16)."""
    from clew.benchmarks import load_all_tasks
    tasks = load_all_tasks()
    web_tasks = [t for t in tasks if "web_search" in t.tags]
    assert len(web_tasks) >= 1


# ── 3. Dry-run (validates setup() + evaluate() shape) ────────────────


def test_dry_run_validates_every_task():
    """BenchmarkRunner.dry_run() must report every task as OK."""
    from clew.benchmarks import BenchmarkRunner, RunConfig
    runner = BenchmarkRunner(RunConfig(dry_run=True))
    runner.load_tasks()
    reports = runner.dry_run()
    assert len(reports) >= 12
    broken = [r for r in reports if not r["ok"]]
    assert not broken, (
        "broken tasks: " + "; ".join(f"{r['task_id']}: {r['error']}" for r in broken)
    )


def test_dry_run_evaluates_to_evaluation_report():
    """Each dry-run report must include the criteria_count field (proves
    evaluate() returned a real EvaluationReport, not None)."""
    from clew.benchmarks import BenchmarkRunner, RunConfig
    runner = BenchmarkRunner(RunConfig(dry_run=True))
    runner.load_tasks()
    reports = runner.dry_run()
    for r in reports:
        assert "criteria_count" in r
        assert r["criteria_count"] >= 1, (
            f"{r['task_id']}: evaluate() returned 0 criteria"
        )


# ── 4. CLI smoke tests ───────────────────────────────────────────────


def test_cli_list_prints_every_task():
    """``clew-bench list`` must list every task without crashing."""
    result = subprocess.run(
        [sys.executable, "-m", "clew.benchmarks.cli", "list"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "general_bug_fix_add" in result.stdout
    assert "heavy_code" in result.stdout
    assert "office" in result.stdout


def test_cli_run_dry_run_returns_zero():
    """``clew-bench run --dry-run`` must exit 0 when all tasks are
    well-formed (which they are — see test_dry_run_validates_every_task)."""
    result = subprocess.run(
        [sys.executable, "-m", "clew.benchmarks.cli", "run", "--dry-run"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "OK" in result.stdout
    assert "BROKEN" not in result.stdout or "0 BROKEN" in result.stdout


def test_cli_run_dry_run_with_section_filter():
    """``--section general`` filter must restrict to just general tasks."""
    result = subprocess.run(
        [sys.executable, "-m", "clew.benchmarks.cli", "run",
         "--dry-run", "--section", "general"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0


# ── 5. Mock provider run (proves harness plumbing) ────────────────────


def test_cli_run_mock_provider_writes_scorecard(tmp_path):
    """``--mock-provider`` runs the full pipeline with a FakeProvider
    and writes a scorecard JSON. Tasks will mostly fail (the mock
    doesn't do real work) but the harness must complete without
    crashing and produce a well-formed scorecard."""
    out_dir = tmp_path / "results"
    result = subprocess.run(
        [sys.executable, "-m", "clew.benchmarks.cli", "run",
         "--mock-provider",
         "--task", "general_bug_fix_add",
         "--task", "general_explain_function",
         "--out-dir", str(out_dir),
         "--tag", "test"],
        capture_output=True, text=True, timeout=120,
    )
    # Mock provider means tasks fail — returncode is 1, but that's
    # expected. We just need the scorecard to be written.
    assert "Scorecard written to:" in result.stdout, (
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    scorecards = list(out_dir.glob("*.json"))
    assert len(scorecards) == 1
    data = json.loads(scorecards[0].read_text())
    assert data["total_tasks"] == 2
    assert data["passed"] == 0  # mock provider can't actually do work
    assert data["failed"] == 2
    assert data["total_tokens_in"] > 0  # tokens were tracked
    assert data["total_tokens_out"] > 0
    assert len(data["results"]) == 2
    for r in data["results"]:
        assert "task_id" in r
        assert "passed" in r
        assert "checked_criteria" in r
        assert "wall_clock_s" in r
        assert "tokens_in" in r


# ── 6. diff_scorecards ───────────────────────────────────────────────


def test_diff_scorecards_detects_regression():
    """A pass→fail flip must be reported as a regression."""
    from clew.benchmarks.diff_report import diff_scorecards
    baseline = {
        "results": [
            {"task_id": "T1", "passed": True, "cost_usd": 0.01,
             "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
             "tool_call_count": 3},
            {"task_id": "T2", "passed": False, "cost_usd": 0.02,
             "wall_clock_s": 20, "tokens_in": 200, "tokens_out": 100,
             "tool_call_count": 5},
        ]
    }
    new = {
        "results": [
            {"task_id": "T1", "passed": False, "cost_usd": 0.015,
             "wall_clock_s": 12, "tokens_in": 110, "tokens_out": 55,
             "tool_call_count": 4},
            {"task_id": "T2", "passed": True, "cost_usd": 0.018,
             "wall_clock_s": 18, "tokens_in": 190, "tokens_out": 95,
             "tool_call_count": 4},
        ]
    }
    diff = diff_scorecards(baseline, new)
    assert len(diff["regressions"]) == 1
    assert diff["regressions"][0]["task_id"] == "T1"
    assert len(diff["fixes"]) == 1
    assert diff["fixes"][0]["task_id"] == "T2"
    # Pass rate delta: baseline 50% -> new 50% (one regressed, one fixed).
    assert diff["summary"]["baseline_pass_rate"] == 0.5
    assert diff["summary"]["new_pass_rate"] == 0.5
    assert diff["summary"]["pass_rate_delta"] == 0.0


def test_diff_scorecards_detects_new_and_removed_tasks():
    """New tasks in `new` and missing tasks in `baseline` must be reported."""
    from clew.benchmarks.diff_report import diff_scorecards
    baseline = {
        "results": [
            {"task_id": "T1", "passed": True, "cost_usd": 0.01,
             "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
             "tool_call_count": 3},
        ]
    }
    new = {
        "results": [
            {"task_id": "T1", "passed": True, "cost_usd": 0.01,
             "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
             "tool_call_count": 3},
            {"task_id": "T2", "passed": False, "cost_usd": 0.02,
             "wall_clock_s": 20, "tokens_in": 200, "tokens_out": 100,
             "tool_call_count": 5},
        ]
    }
    diff = diff_scorecards(baseline, new)
    assert len(diff["new_tasks"]) == 1
    assert diff["new_tasks"][0]["task_id"] == "T2"
    assert len(diff["removed_tasks"]) == 0


def test_diff_scorecards_handles_no_changes():
    """Two identical scorecards produce zero regressions and zero fixes."""
    from clew.benchmarks.diff_report import diff_scorecards
    scorecard = {
        "results": [
            {"task_id": "T1", "passed": True, "cost_usd": 0.01,
             "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
             "tool_call_count": 3},
        ]
    }
    diff = diff_scorecards(scorecard, scorecard)
    assert diff["regressions"] == []
    assert diff["fixes"] == []
    assert diff["summary"]["pass_rate_delta"] == 0.0


# ── 7. Real task content (regression-guards for the bugs we found) ──


def test_runtime_imports_build_skill_catalog():
    """G22a-found regression: runtime.py used build_skill_catalog without
    importing it. Verify the import is present."""
    import clew.agent_runtime.runtime as rt
    # The name must be resolvable at module level.
    assert hasattr(rt, "build_skill_catalog"), (
        "build_skill_catalog must be imported in clew/agent_runtime/runtime.py"
    )


def test_runtime_imports_provider_message_and_response():
    """G22a-found regression: runtime.py used ProviderMessage and
    ProviderResponse without importing them."""
    import clew.agent_runtime.runtime as rt
    assert hasattr(rt, "ProviderMessage"), (
        "ProviderMessage must be imported in clew/agent_runtime/runtime.py"
    )
    assert hasattr(rt, "ProviderResponse"), (
        "ProviderResponse must be imported in clew/agent_runtime/runtime.py"
    )


def test_runtime_imports_agent_step():
    """G22a-found regression: runtime.py used AgentStep without importing it."""
    import clew.agent_runtime.runtime as rt
    assert hasattr(rt, "AgentStep"), (
        "AgentStep must be imported in clew/agent_runtime/runtime.py"
    )


def test_prompts_imports_tasktype():
    """G22a-found regression: prompts.py used TaskType without importing it."""
    import clew.agent_runtime.prompts as prompts
    assert hasattr(prompts, "TaskType"), (
        "TaskType must be imported in clew/agent_runtime/prompts.py"
    )


def test_prompts_lazily_loads_office_schema():
    """G22a-found regression: prompts.py referenced OFFICE_TOOL_SCHEMA
    and OFFICE_SYSTEM_SUFFIX without importing them. The lazy loader
    functions must exist."""
    import clew.agent_runtime.prompts as prompts
    assert hasattr(prompts, "_load_office_tool_schema")
    assert hasattr(prompts, "_load_office_system_suffix")
    # Calling them must not crash (returns "" if office_worker not importable,
    # otherwise the actual schema string).
    schema = prompts._load_office_tool_schema()
    suffix = prompts._load_office_system_suffix()
    assert isinstance(schema, str)
    assert isinstance(suffix, str)


def test_end_to_end_agent_loop_no_nameerror():
    """The most important regression test: a real AgentRuntime.run() call
    must not crash with NameError. Uses a FakeProvider-style stub to
    avoid spending money.

    Before the G22a fixes, runtime.run() crashed with:
      NameError: name 'build_skill_catalog' is not defined
    because of four separate missing imports in runtime.py and prompts.py.
    """
    import os, tempfile
    from clew.providers import get_registry, ProviderConfig
    from clew.agent_runtime.runtime import AgentRuntime
    from clew.agent_runtime.types import TaskType

    # Use a fake provider so we don't need real API creds.
    # We attach it directly to the registry's _instances dict.
    from clew.benchmarks.runner import _FakeProvider, _make_fake_registry
    registry = _make_fake_registry()

    ws = tempfile.mkdtemp()
    runtime = AgentRuntime(
        registry=registry, workspace=ws,
        max_iterations=1, enable_planning=False,
        section="general",
    )
    runtime.set_autonomy("never_ask")

    # The call must NOT raise NameError. It will likely fail with some
    # other error (the FakeProvider returns a final_answer immediately,
    # which ends the loop), but that's fine — we just want to confirm
    # the import-related NameErrors are gone.
    try:
        result = runtime.run("test prompt", task_type=TaskType.AGENTIC)
        # If we got here, no NameError. success may be True or False.
        assert result is not None
    except NameError as e:
        pytest.fail(f"NameError still present: {e}")
    except Exception:
        # Any other exception is OK for this test — we only care about
        # NameErrors (the class of bug the harness caught).
        pass


# ── 8. CLI diff subcommand ───────────────────────────────────────────


def test_cli_diff_returns_zero_when_no_regressions(tmp_path):
    """``clew-bench diff`` returns 0 when there are no regressions."""
    baseline = {"results": [
        {"task_id": "T1", "passed": True, "cost_usd": 0.01,
         "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
         "tool_call_count": 3}]}
    new = {"results": [
        {"task_id": "T1", "passed": True, "cost_usd": 0.01,
         "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
         "tool_call_count": 3}]}
    b_path = tmp_path / "baseline.json"
    n_path = tmp_path / "new.json"
    b_path.write_text(json.dumps(baseline))
    n_path.write_text(json.dumps(new))
    result = subprocess.run(
        [sys.executable, "-m", "clew.benchmarks.cli", "diff",
         str(b_path), str(n_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "REGRESSIONS: none" in result.stdout


def test_cli_diff_returns_one_when_regression_present(tmp_path):
    """``clew-bench diff`` returns 1 when there's a regression."""
    baseline = {"results": [
        {"task_id": "T1", "passed": True, "cost_usd": 0.01,
         "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
         "tool_call_count": 3}]}
    new = {"results": [
        {"task_id": "T1", "passed": False, "cost_usd": 0.01,
         "wall_clock_s": 10, "tokens_in": 100, "tokens_out": 50,
         "tool_call_count": 3}]}
    b_path = tmp_path / "baseline.json"
    n_path = tmp_path / "new.json"
    b_path.write_text(json.dumps(baseline))
    n_path.write_text(json.dumps(new))
    result = subprocess.run(
        [sys.executable, "-m", "clew.benchmarks.cli", "diff",
         str(b_path), str(n_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "T1" in result.stdout
    assert "REGRESSIONS" in result.stdout
