"""clew.benchmarks — Agent-quality benchmark suite (G22a, issue #16).

This package implements a runnable harness that spins up the Clew
AgentRuntime against a fixed set of self-contained tasks and records
pass/fail + cost + time. It is deliberately SEPARATE from the normal
``pytest clew/`` suite — running it costs real API money. The dry-run
mode (``clew-bench run --dry-run``) is the only path that runs in CI.

Public API:
    - :class:`TaskSpec` — a single benchmark task definition.
    - :class:`BenchmarkRunner` — runs TaskSpecs against the runtime.
    - :func:`load_all_tasks` — discovers every task module under
      ``clew/benchmarks/tasks/``.
    - :func:`run` — convenience entry point used by the CLI.

See ``clew/benchmarks/README.md`` for the design rationale and the
mapping back to issue #16.
"""

from __future__ import annotations

from ._base import (
    TaskSpec,
    TaskResult,
    EvaluationReport,
    Section,
    Difficulty,
    load_all_tasks,
    discover_task_modules,
)

from .runner import BenchmarkRunner, RunConfig, RunSummary, run

__all__ = [
    "TaskSpec",
    "TaskResult",
    "EvaluationReport",
    "Section",
    "Difficulty",
    "load_all_tasks",
    "discover_task_modules",
    "BenchmarkRunner",
    "RunConfig",
    "RunSummary",
    "run",
]

__version__ = "0.1.0"
