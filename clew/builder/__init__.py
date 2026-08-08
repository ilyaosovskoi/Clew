"""
clew.builder — Autonomous Self-Improvement Loop (v2.2.3)

Clew Builder is a meta-agent: it uses Clew's own AgentRuntime + tool engine
to *modify Clew's own source code* in response to a plain-text task list.

Pipeline (per task):
    PENDING → PLANNING → IMPLEMENTING → VERIFYING → REPORTING → DONE
                                            ↓              ↓
                                         FAILED         FAILED
                                                  ↓
                                            retry (≤ N)

Key invariants:
    * Reuses AgentRuntime.run() — Guardian / sandbox / audit trail all apply.
    * Per-task git branch  builder/task-NN-<slug>  isolates changes.
    * Nvidia NIM is the default backend; NimPool enforces a 35 req/min cap
      (40 RPM limit − 5 margin) so the loop never blows the quota.
    * State persists to builder_state.json — kill / resume any time.
    * Never pushes to remote. All commits are local. The user reviews
      branches and merges manually with `git merge builder/task-NN-…`.

Public entrypoint:  run_builder(config: BuilderConfig) -> BuilderReport
CLI subcommand:     python -m clew.cli builder --tasks tasks.txt …
"""

from __future__ import annotations

from .self_improvement_loop import (
    BuilderConfig,
    BuilderReport,
    run_builder,
)
from .nim_pool import NimPool, NimPoolConfig
from .task_list import Task, TaskList, parse_task_file
from .state import BuilderState, TaskAttempt, TaskStatus

__all__ = [
    "BuilderConfig",
    "BuilderReport",
    "run_builder",
    "NimPool",
    "NimPoolConfig",
    "Task",
    "TaskList",
    "parse_task_file",
    "BuilderState",
    "TaskAttempt",
    "TaskStatus",
]

__version__ = "2.2.3"
