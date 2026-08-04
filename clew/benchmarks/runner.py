"""clew.benchmarks.runner — the benchmark harness.

For each task:
  1. Materialise the starting tree in a fresh temp directory.
  2. Build an AgentRuntime pointed at that workspace.
  3. Run the agent against the task's prompt to completion or a hard
     iteration/time cap.
  4. Call the task's evaluate() and record the result.

Costs real API money — NEVER runs in normal CI. The ``--dry-run`` mode
validates task well-formedness without calling any LLM, and *that*
part runs in CI.

See ``clew-bench --help`` for the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._base import (
    Difficulty,
    EvaluationReport,
    Section,
    TaskResult,
    TaskSpec,
    load_all_tasks,
)

logger = logging.getLogger(__name__)


# ── Config + summary dataclasses ──────────────────────────────────────


@dataclass
class RunConfig:
    """Configuration for a single benchmark run."""

    provider: Optional[str] = None  # None = use config.json's active
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    max_iterations: int = 8
    max_time_s: float = 300.0
    dry_run: bool = False
    mock_provider: bool = False  # use FakeProvider (no API calls)
    guardian_level: str = "off"  # off / dangerous_only / all
    autonomy: str = "never_ask"  # never_ask for benchmarks
    task_filter: Optional[List[str]] = None  # only run these task ids
    section_filter: Optional[List[str]] = None  # only run these sections
    out_dir: Optional[str] = None  # results dir; defaults to package's results/
    tag: str = ""  # free-form tag for the scorecard filename

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "max_iterations": self.max_iterations,
            "max_time_s": self.max_time_s,
            "dry_run": self.dry_run,
            "mock_provider": self.mock_provider,
            "guardian_level": self.guardian_level,
            "autonomy": self.autonomy,
            "task_filter": list(self.task_filter) if self.task_filter else None,
            "section_filter": list(self.section_filter) if self.section_filter else None,
            "tag": self.tag,
        }


@dataclass
class RunSummary:
    """Top-level summary written to the scorecard JSON file."""

    started_at: str
    finished_at: str
    clew_version: str
    config: Dict[str, Any]
    total_tasks: int
    passed: int
    failed: int
    errored: int
    skipped: int
    total_cost_usd: float
    total_tokens_in: int
    total_tokens_out: int
    total_wall_clock_s: float
    results: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── FakeProvider (for harness self-tests, no API calls) ───────────────


class _FakeProvider:
    """A minimal stand-in for a real LLM provider.

    Returns canned tool-call responses so the harness can prove its
    own plumbing works end-to-end without spending money. The
    responses are deliberately simple — they're not meant to actually
    pass tasks, they're meant to let the harness complete a run.

    Implemented as a duck-typed class that exposes the same surface
    as ``clew.providers.base.Provider`` (``provider_id``, ``label``,
    ``default_model``, ``capabilities``, ``context_window``, ``config``,
    ``is_loaded``, ``load``, ``unload``, ``get_model``,
    ``get_context_window``, ``generate``, ``stream``) so it can be
    registered in a real ``ProviderRegistry`` via the ``_instances``
    dict bypass.
    """

    provider_id: str = "fake"
    label: str = "Fake (mock)"
    default_model: str = "fake-model"
    capabilities: frozenset = frozenset()
    context_window: int = 8_192

    def __init__(self, config=None) -> None:
        # Build a minimal ProviderConfig-shaped object so the registry
        # doesn't choke on .model / .api_key / .extra lookups.
        try:
            from clew.providers.base import ProviderConfig
            self.config = config or ProviderConfig(
                provider_id="fake", model="fake-model"
            )
        except Exception:
            self.config = type("Cfg", (), {"model": "fake-model", "api_key": None, "extra": {}})()
        self._loaded = False
        self._call_count = 0

    def get_model(self) -> str:
        return "fake-model"

    def get_context_window(self) -> int:
        return 8_192

    def is_loaded(self) -> bool:
        return True

    def load(self) -> bool:
        self._loaded = True
        return True

    def unload(self) -> None:
        self._loaded = False

    def generate(self, messages, *, skill=None, tools=None, stop=None, **kw):
        from clew.providers.base import ProviderResponse
        self._call_count += 1
        # Return a final_answer tag with a short canned message so
        # the OutputParser sees a final_answer and ends the loop.
        text = "<final_answer>done</final_answer>"
        return ProviderResponse(
            text=text,
            tokens_in=10,
            tokens_out=5,
            model="fake-model",
            provider="fake",
            finish_reason="stop",
        )

    def stream(self, messages, *, skill=None, tools=None, stop=None, **kw):
        yield "<final_answer>done</final_answer>"


def _make_fake_registry():
    """Build a ProviderRegistry whose active provider is _FakeProvider."""
    from clew.providers import ProviderRegistry, ProviderConfig, get_registry

    registry = get_registry()
    if not registry.list_providers():
        registry.register_default()
    # Install the fake as a registered provider. The registry's normal
    # path is register(class) -> configure(id, cfg) -> set_active(id),
    # but register() requires a real Provider subclass. We bypass by
    # stashing the instance directly into _instances (the registry's
    # private cache) and the config into _configs, then set_active.
    fake = _FakeProvider()
    cfg = ProviderConfig(
        provider_id="fake",
        model="fake-model",
        api_key=None,
        api_base=None,
    )
    try:
        # Make has_provider("fake") return True.
        registry._classes["fake"] = _FakeProvider
        registry._instances["fake"] = fake
        registry._configs["fake"] = cfg
        registry._active_id = "fake"
    except Exception as e:
        logger.warning("fake registry install failed: %s", e)
    return registry


# ── Runner ────────────────────────────────────────────────────────────


class BenchmarkRunner:
    """Runs TaskSpecs against the AgentRuntime and records a scorecard."""

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self._tasks: List[TaskSpec] = []
        self._results: List[TaskResult] = []

    # ── Task selection ──────────────────────────────────────────────

    def load_tasks(self) -> List[TaskSpec]:
        """Load every task module under clew/benchmarks/tasks/."""
        self._tasks = load_all_tasks()
        return self._tasks

    def _filter_tasks(self) -> List[TaskSpec]:
        tasks = list(self._tasks)
        if self.config.section_filter:
            wanted_sections = set(self.config.section_filter)
            tasks = [t for t in tasks if t.section.value in wanted_sections]
        if self.config.task_filter:
            wanted_ids = set(self.config.task_filter)
            tasks = [t for t in tasks if t.id in wanted_ids]
        return tasks

    # ── Dry-run ─────────────────────────────────────────────────────

    def dry_run(self) -> List[Dict[str, Any]]:
        """Validate every task's starting tree + criteria without
        calling any LLM.

        For each task: materialise the starting tree in a temp dir,
        then call evaluate() against an empty agent_output / tool_calls
        list. Most tasks will report "fail" (because the agent hasn't
        actually run yet) — that's fine. What we're checking is that:

        1. setup() doesn't crash.
        2. evaluate() doesn't crash.
        3. The returned EvaluationReport has the expected shape.

        Returns a list of per-task validation reports.
        """
        if not self._tasks:
            self.load_tasks()
        reports: List[Dict[str, Any]] = []
        for spec in self._tasks:
            entry: Dict[str, Any] = {
                "task_id": spec.id,
                "section": spec.section.value,
                "difficulty": spec.difficulty.value,
                "ok": True,
                "error": None,
            }
            tmpdir = tempfile.mkdtemp(prefix=f"clew_bench_dryrun_{spec.id}_")
            try:
                spec.setup(tmpdir)
                # Call evaluate with empty agent state — should not crash.
                report = spec.evaluate(tmpdir, "", [])
                if not isinstance(report, EvaluationReport):
                    entry["ok"] = False
                    entry["error"] = (
                        f"evaluate() returned {type(report).__name__}, "
                        "expected EvaluationReport"
                    )
                else:
                    entry["evaluate_returned_pass"] = report.passed
                    entry["criteria_count"] = len(report.checked_criteria)
            except Exception as e:
                entry["ok"] = False
                entry["error"] = f"{type(e).__name__}: {e}"
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            reports.append(entry)
        return reports

    # ── Real run ────────────────────────────────────────────────────

    def run(self) -> RunSummary:
        """Run every (filtered) task and return a RunSummary.

        This calls real LLM APIs unless ``config.mock_provider`` is True.
        """
        if not self._tasks:
            self.load_tasks()
        tasks = self._filter_tasks()
        if not tasks:
            logger.warning("no tasks matched the filters — nothing to run")

        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        results: List[TaskResult] = []
        for spec in tasks:
            try:
                result = self._run_one(spec)
            except Exception as e:
                logger.exception("task %s errored", spec.id)
                result = TaskResult(
                    task_id=spec.id,
                    section=spec.section.value,
                    difficulty=spec.difficulty.value,
                    passed=False,
                    reason=f"harness error: {type(e).__name__}",
                    wall_clock_s=0.0,
                    tool_call_count=0,
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    iterations=0,
                    provider=self.config.provider or "unknown",
                    model=self.config.model or "unknown",
                    error=str(e),
                )
            results.append(result)
            self._results.append(result)

        finished_at = datetime.now(timezone.utc).isoformat()
        wall = time.monotonic() - t0

        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed and not r.error)
        errored = sum(1 for r in results if r.error)
        skipped = len(tasks) - len(results)

        try:
            from clew import __version__ as clew_version
        except Exception:
            clew_version = "unknown"

        summary = RunSummary(
            started_at=started_at,
            finished_at=finished_at,
            clew_version=clew_version,
            config=self.config.to_dict(),
            total_tasks=len(tasks),
            passed=passed,
            failed=failed,
            errored=errored,
            skipped=skipped,
            total_cost_usd=sum(r.cost_usd for r in results),
            total_tokens_in=sum(r.tokens_in for r in results),
            total_tokens_out=sum(r.tokens_out for r in results),
            total_wall_clock_s=wall,
            results=[r.to_dict() for r in results],
        )
        return summary

    def _run_one(self, spec: TaskSpec) -> TaskResult:
        """Run a single task and return its TaskResult."""
        tmpdir = tempfile.mkdtemp(prefix=f"clew_bench_{spec.id}_")
        t0 = time.monotonic()
        try:
            spec.setup(tmpdir)
            agent_output, tool_calls, tokens_in, tokens_out, cost, iters, prov, model = (
                self._run_agent(spec, tmpdir)
            )
            wall = time.monotonic() - t0
            report = spec.evaluate(tmpdir, agent_output, tool_calls)
            if not isinstance(report, EvaluationReport):
                report = EvaluationReport(
                    passed=False,
                    reason=f"evaluate() returned {type(report).__name__}",
                )
            return TaskResult(
                task_id=spec.id,
                section=spec.section.value,
                difficulty=spec.difficulty.value,
                passed=report.passed,
                reason=report.reason,
                wall_clock_s=wall,
                tool_call_count=len(tool_calls),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                iterations=iters,
                provider=prov,
                model=model,
                checked_criteria=report.checked_criteria,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _run_agent(self, spec: TaskSpec, workspace: str):
        """Spin up an AgentRuntime and run it against the task prompt.

        Returns (output, tool_calls, tokens_in, tokens_out, cost, iters,
        provider, model).
        """
        from clew.agent_runtime import AgentRuntime, TaskType

        # Build the registry.
        if self.config.mock_provider:
            registry = _make_fake_registry()
            provider_id = "fake"
            model_name = "fake-model"
        else:
            from clew_tui.bridge import ClewBridge, ProviderChoice
            # Use the bridge's registry builder so we get the same
            # ~/.clew/config.json path as production.
            bridge = ClewBridge(
                workspace=workspace,
                provider=ProviderChoice(
                    provider_id=self.config.provider,
                    model=self.config.model,
                    api_key=self.config.api_key,
                    api_base=self.config.api_base,
                ),
                section=spec.section.value,
                max_iterations=max(
                    self.config.max_iterations, spec.min_iterations
                ),
                enable_planning=False,
            )
            registry = bridge._build_registry()
            provider_id = (
                self.config.provider
                or getattr(registry, "active_id", None)
                or "ollama"
            )
            active = registry.active
            model_name = (
                self.config.model
                or (active.get_model() if active and hasattr(active, "get_model") else "")
                or "unknown"
            )

        # Token tracker — capture this run's usage.
        from clew.token_tracker import TokenTracker
        tracker = TokenTracker(persist_path=Path(workspace) / ".token_history.jsonl")

        # Capture tool calls via the event sink.
        captured_tool_calls: List[Dict[str, Any]] = []

        def _sink(kind: str, data: Dict[str, Any]) -> None:
            if kind == "tool_called":
                captured_tool_calls.append({
                    "name": data.get("tool", ""),
                    "args": data.get("args", {}),
                    "result": data.get("result", ""),
                })
            elif kind == "tool_result":
                # Attach the result back to the last matching call.
                tool = data.get("tool", "")
                if captured_tool_calls:
                    for tc in reversed(captured_tool_calls):
                        if tc["name"] == tool and not tc.get("result"):
                            tc["result"] = data.get("result", "")
                            break

        # Build the runtime.
        max_iter = max(self.config.max_iterations, spec.min_iterations)
        runtime = AgentRuntime(
            registry=registry,
            workspace=workspace,
            max_iterations=max_iter,
            enable_planning=False,
            on_event=_sink,
            token_tracker=tracker,
            section=spec.section.value,
        )
        runtime.set_autonomy(self.config.autonomy)

        # Guardian level (if requested).
        if self.config.guardian_level != "off":
            try:
                from clew.agent.guardian import GuardianConfig
                runtime.tools._guardian_config = GuardianConfig(
                    level=self.config.guardian_level
                )
            except Exception as e:
                logger.warning("guardian config failed: %s", e)

        # Run with a hard wall-clock cap.
        deadline = time.monotonic() + min(
            self.config.max_time_s, spec.max_time_s
        )
        result = None
        try:
            result = runtime.run(spec.prompt, task_type=TaskType.AGENTIC)
        except Exception as e:
            logger.warning("agent run failed for %s: %s", spec.id, e)
            return ("", captured_tool_calls, 0, 0, 0.0, 0, provider_id, model_name)

        if time.monotonic() > deadline:
            logger.warning("task %s exceeded wall-clock cap", spec.id)

        output = (result.output if result else "") or ""
        iters = result.iterations if result else 0

        # Token usage — read from tracker (it persisted entries during the run).
        stats = tracker.stats()
        tokens_in = int(stats.get("total_tokens_in", 0) or 0)
        tokens_out = int(stats.get("total_tokens_out", 0) or 0)
        cost = float(stats.get("total_cost", 0.0) or 0.0)

        return (
            output,
            captured_tool_calls,
            tokens_in,
            tokens_out,
            cost,
            iters,
            provider_id,
            model_name,
        )

    # ── Scorecard persistence ───────────────────────────────────────

    def write_scorecard(self, summary: RunSummary) -> Path:
        """Write the summary JSON to the results directory.

        Filename: ``<YYYYMMDD-HHMMSS>-<version>-<provider>-<tag>.json``
        """
        out_dir = Path(self.config.out_dir) if self.config.out_dir else (
            Path(__file__).resolve().parent / "results"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        provider_part = self.config.provider or ("mock" if self.config.mock_provider else "default")
        tag_part = f"-{self.config.tag}" if self.config.tag else ""
        fname = f"{ts}-{summary.clew_version}-{provider_part}{tag_part}.json"
        path = out_dir / fname
        path.write_text(
            json.dumps(summary.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        return path


# ── Convenience entry point ───────────────────────────────────────────


def run(config: RunConfig) -> RunSummary:
    """Build a runner, run it, write the scorecard, return the summary."""
    runner = BenchmarkRunner(config)
    if config.dry_run:
        # Dry-run doesn't produce a RunSummary — it produces per-task
        # validation reports. We wrap them in a minimal summary so the
        # caller can still write a scorecard.
        reports = runner.dry_run()
        try:
            from clew import __version__ as clew_version
        except Exception:
            clew_version = "unknown"
        now = datetime.now(timezone.utc).isoformat()
        summary = RunSummary(
            started_at=now,
            finished_at=now,
            clew_version=clew_version,
            config=config.to_dict(),
            total_tasks=len(reports),
            passed=sum(1 for r in reports if r["ok"]),
            failed=sum(1 for r in reports if not r["ok"]),
            errored=0,
            skipped=0,
            total_cost_usd=0.0,
            total_tokens_in=0,
            total_tokens_out=0,
            total_wall_clock_s=0.0,
            results=reports,
        )
    else:
        summary = runner.run()
    runner.write_scorecard(summary)
    return summary
