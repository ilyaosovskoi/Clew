"""clew.benchmarks._base — task spec, evaluation primitives, discovery.

A benchmark task is a small Python module under
``clew/benchmarks/tasks/<section>/<id>.py`` that exports a
``build() -> TaskSpec`` function. The module IS the task — it carries
its own starting file tree (written by ``setup(workspace_path)``) and
its own pass/fail check (``evaluate(workspace_path)``).

Design choices:

* **Programmatic criteria, not LLM-judge.** Every task's pass/fail is
  checked by Python code that inspects the resulting file tree —
  does a named file exist, does a named function exist with the right
  signature, do the project's own tests pass, does a specific string
  appear/not appear. Where the task is inherently fuzzy (e.g.
  "explain what this function does"), the criterion is "did the
  agent call ``read_file`` on the right file AND produce a non-empty
  ``final_answer``" — cheap and mechanical.

* **Starting tree is materialised at run time**, not checked into
  the repo as a giant fixture tree. Each task's ``setup()`` writes
  the starting files via ``pathlib.Path.write_text``. This keeps the
  task self-contained (no separate ``fixtures/`` directory to keep
  in sync) and makes it easy to add new tasks.

* **Section tag (general / heavy_code / office) is mandatory** so
  the mix covers all three runtime sections, matching real usage.

* **Difficulty tag (easy / medium / hard)** lets the regression
  report weight "easy flipped to fail" differently from "hard
  flipped to fail".
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class Section(str, Enum):
    """The three runtime sections a benchmark task can target."""

    GENERAL = "general"
    HEAVY_CODE = "heavy_code"
    OFFICE = "office"


class Difficulty(str, Enum):
    """Rough difficulty tag — used to weight regressions."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class EvaluationReport:
    """Result of a task's ``evaluate()`` call.

    Attributes:
        passed: did the task's pass/fail criteria all succeed?
        reason: human-readable one-line summary of why it passed/failed.
        details: optional longer form (e.g. the diff of what was missing).
        checked_criteria: list of named criteria that were evaluated, each
            with a pass/fail flag. Lets the scorecard show "passed 3/4
            criteria" instead of a binary result, so partial regressions
            are visible across releases.
    """

    passed: bool
    reason: str = ""
    details: str = ""
    checked_criteria: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "details": self.details,
            "checked_criteria": list(self.checked_criteria),
        }

    @classmethod
    def pass_(cls, reason: str = "all criteria met", **kw) -> "EvaluationReport":
        return cls(passed=True, reason=reason, **kw)

    @classmethod
    def fail(cls, reason: str, details: str = "", **kw) -> "EvaluationReport":
        return cls(passed=False, reason=reason, details=details, **kw)


@dataclass
class TaskSpec:
    """A single benchmark task definition.

    A task module under ``clew/benchmarks/tasks/<section>/<id>.py``
    must export ``build() -> TaskSpec``. The harness:

    1. Calls ``setup(workspace_path)`` to materialise the starting
       file tree in a fresh temp directory.
    2. Spins up an ``AgentRuntime`` pointed at that workspace.
    3. Runs the agent against ``prompt`` to completion or a hard
       iteration/time cap.
    4. Calls ``evaluate(workspace_path, agent_result)`` and records
       the returned :class:`EvaluationReport`.

    Attributes:
        id: stable identifier (matches the module filename). Must be
            unique across all tasks.
        section: which runtime section to run the agent in.
        difficulty: rough difficulty tag for regression weighting.
        prompt: the natural-language prompt a user would type.
        setup: callable(workspace_path: str) -> None. Writes the
            starting file tree.
        evaluate: callable(workspace_path: str, agent_output: str,
            tool_calls: list) -> EvaluationReport. Inspects the
            resulting state and returns pass/fail.
        description: short human-readable summary for the scorecard.
        expected_duration_s: rough estimate; if a run takes >2x this,
            the harness flags it as a perf regression.
        tags: optional extra tags (e.g. "guardian", "web_search",
            "refactor") for filtering.
        min_iterations: hard cap — the harness uses
            max(spec.min_iterations, run_config.max_iterations).
        max_time_s: hard wall-clock cap for this task.
    """

    id: str
    section: Section
    difficulty: Difficulty
    prompt: str
    setup: Callable[[str], None]
    evaluate: Callable[[str, str, List[Dict[str, Any]]], EvaluationReport]
    description: str = ""
    expected_duration_s: float = 30.0
    tags: List[str] = field(default_factory=list)
    min_iterations: int = 8
    max_time_s: float = 300.0

    def to_metadata(self) -> Dict[str, Any]:
        """Serialise the static parts of the spec (for the scorecard)."""
        return {
            "id": self.id,
            "section": self.section.value,
            "difficulty": self.difficulty.value,
            "description": self.description,
            "prompt": self.prompt,
            "expected_duration_s": self.expected_duration_s,
            "tags": list(self.tags),
            "min_iterations": self.min_iterations,
            "max_time_s": self.max_time_s,
        }


@dataclass
class TaskResult:
    """Per-task outcome recorded in the scorecard."""

    task_id: str
    section: str
    difficulty: str
    passed: bool
    reason: str
    wall_clock_s: float
    tool_call_count: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    iterations: int
    provider: str
    model: str
    checked_criteria: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "section": self.section,
            "difficulty": self.difficulty,
            "passed": self.passed,
            "reason": self.reason,
            "wall_clock_s": round(self.wall_clock_s, 3),
            "tool_call_count": self.tool_call_count,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "iterations": self.iterations,
            "provider": self.provider,
            "model": self.model,
            "checked_criteria": list(self.checked_criteria),
            "error": self.error,
        }


# ── Discovery ────────────────────────────────────────────────────────


def discover_task_modules() -> List[str]:
    """Return the dotted module paths of every task module under
    ``clew/benchmarks/tasks/``.

    A task module is any ``.py`` file whose name does not start with
    ``_`` and that lives in (or under) ``clew/benchmarks/tasks/``.
    Sub-packages are allowed (e.g.
    ``clew.benchmarks.tasks.general.bug_fix_add_validation``).
    """
    import clew.benchmarks.tasks as _pkg

    base_dir = Path(_pkg.__file__).resolve().parent
    found: List[str] = []
    for finder, name, ispkg in pkgutil.walk_packages(
        path=[str(base_dir)], prefix=_pkg.__name__ + "."
    ):
        if name.startswith("_"):
            continue
        if ispkg:
            continue
        if name.rsplit(".", 1)[-1].startswith("_"):
            continue
        found.append(name)
    found.sort()
    return found


def load_all_tasks() -> List[TaskSpec]:
    """Import every task module and call its ``build()`` function.

    Tasks whose ``build()`` raises are skipped (with a warning printed
    to stderr) rather than aborting the whole suite — a single broken
    task definition shouldn't prevent the other 25 from running.
    """
    import sys

    tasks: List[TaskSpec] = []
    for mod_path in discover_task_modules():
        try:
            mod = importlib.import_module(mod_path)
        except Exception as e:  # pragma: no cover - defensive
            print(f"[benchmarks] failed to import {mod_path}: {e}", file=sys.stderr)
            continue
        build = getattr(mod, "build", None)
        if not callable(build):
            print(
                f"[benchmarks] {mod_path} has no build() function — skipping",
                file=sys.stderr,
            )
            continue
        try:
            spec = build()
        except Exception as e:  # pragma: no cover - defensive
            print(
                f"[benchmarks] {mod_path}.build() raised: {e}",
                file=sys.stderr,
            )
            continue
        if not isinstance(spec, TaskSpec):
            print(
                f"[benchmarks] {mod_path}.build() returned non-TaskSpec: "
                f"{type(spec).__name__}",
                file=sys.stderr,
            )
            continue
        # Default the task id from the module name if not set.
        if not spec.id:
            spec.id = mod_path.rsplit(".", 1)[-1]
        tasks.append(spec)
    return tasks


# ── Evaluator helpers ────────────────────────────────────────────────


def file_contains(path: Path, needle: str) -> bool:
    """True if ``path`` exists and contains ``needle`` as a substring."""
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        return False


def file_exists(path: Path) -> bool:
    return path.is_file()


def function_exists(path: Path, func_name: str) -> bool:
    """Heuristic: does ``def <func_name>(`` appear in ``path``?

    This is intentionally a regex-free substring check — fast, no
    false negatives on legitimate Python. The check is loose enough
    to handle both ``def foo(...)`` and ``async def foo(...)``.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("def " + func_name + "("):
            return True
        if stripped.startswith("async def " + func_name + "("):
            return True
    return False


def function_signature_has(path: Path, func_name: str, *required_params: str) -> bool:
    """True if ``def <func_name>(...)`` exists in ``path`` AND every
    name in ``required_params`` appears in the signature line.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    for line in text.splitlines():
        stripped = line.lstrip()
        prefix = ""
        if stripped.startswith("def " + func_name + "("):
            prefix = "def " + func_name + "("
        elif stripped.startswith("async def " + func_name + "("):
            prefix = "async def " + func_name + "("
        else:
            continue
        # Find the matching close paren — handles signatures that span
        # multiple lines (rare in practice but possible).
        rest = text[text.index(prefix) + len(prefix):]
        depth = 1
        sig_chars = []
        for ch in rest:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            sig_chars.append(ch)
        sig = "".join(sig_chars)
        return all(p in sig for p in required_params)
    return False
