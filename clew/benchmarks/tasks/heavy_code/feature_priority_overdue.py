"""Heavy_code task — feature addition across 3 files + tests.

Category: heavy_code
Section: heavy_code
Difficulty: hard

Starting tree: a small task-tracker with ``Task`` dataclass + ``TaskTracker``
class in ``tracker.py``. The agent must:
1. Add a ``priority`` field to ``Task`` (default MEDIUM).
2. Add a ``filter_by_priority()`` method to ``TaskTracker``.
3. Add an ``overdue()`` method (tasks whose due date is in the past).
4. Update ``test_tracker.py`` to cover the new functionality.

Pass criteria: new field + methods exist; tests pass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..._base import (
    Difficulty,
    EvaluationReport,
    Section,
    TaskSpec,
)


_TRACKER_PY = """\
\"\"\"A simple task tracker.\"\"\"

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

@dataclass
class Task:
    title: str
    due: datetime
    done: bool = False

class TaskTracker:
    def __init__(self):
        self._tasks: List[Task] = []

    def add(self, task: Task):
        self._tasks.append(task)

    def all(self) -> List[Task]:
        return list(self._tasks)

    def mark_done(self, title: str):
        for t in self._tasks:
            if t.title == title:
                t.done = True
                return True
        return False
"""


_TRACKER_TEST = """\
\"\"\"Tests for the task tracker.\"\"\"
from datetime import datetime, timedelta
from tracker import Task, TaskTracker

def test_add_and_all():
    t = Task(title="A", due=datetime(2026, 1, 1))
    tr = TaskTracker()
    tr.add(t)
    assert len(tr.all()) == 1

def test_mark_done():
    t = Task(title="A", due=datetime(2026, 1, 1))
    tr = TaskTracker()
    tr.add(t)
    assert tr.mark_done("A") == True
    assert tr.all()[0].done == True

def test_priority_field_exists():
    t = Task(title="A", due=datetime(2026, 1, 1))
    assert hasattr(t, "priority") == True

def test_filter_by_priority():
    tr = TaskTracker()
    tr.add(Task(title="A", due=datetime(2026, 1, 1), priority="HIGH"))
    tr.add(Task(title="B", due=datetime(2026, 1, 1), priority="LOW"))
    tr.add(Task(title="C", due=datetime(2026, 1, 1), priority="HIGH"))
    high = tr.filter_by_priority("HIGH")
    assert len(high) == 2

def test_overdue():
    tr = TaskTracker()
    tr.add(Task(title="past", due=datetime(2000, 1, 1)))
    tr.add(Task(title="future", due=datetime(2099, 1, 1)))
    overdue = tr.overdue()
    assert len(overdue) == 1
    assert overdue[0].title == "past"
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "tracker.py").write_text(_TRACKER_PY, encoding="utf-8")
    (root / "test_tracker.py").write_text(_TRACKER_TEST, encoding="utf-8")


def _run_pytest(workspace: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_tracker.py", "-v"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return (proc.returncode == 0), proc.stdout + proc.stderr
    except Exception as e:
        return False, f"pytest invocation failed: {e}"


def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    root = Path(workspace)
    tracker = root / "tracker.py"
    criteria = []

    text = tracker.read_text(encoding="utf-8", errors="replace") if tracker.is_file() else ""

    # 1. priority field added to Task.
    c1 = "priority" in text
    criteria.append({"name": "Task has priority field", "passed": c1})

    # 2. filter_by_priority method added.
    c2 = "def filter_by_priority" in text
    criteria.append({"name": "filter_by_priority() added", "passed": c2})

    # 3. overdue method added.
    c3 = "def overdue" in text
    criteria.append({"name": "overdue() added", "passed": c3})

    # 4. Tests pass.
    c4, test_output = _run_pytest(workspace)
    criteria.append({"name": "test_tracker.py passes", "passed": c4})

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        details=test_output if not c4 else "",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="heavy_code_feature_priority_overdue",
        section=Section.HEAVY_CODE,
        difficulty=Difficulty.HARD,
        description=(
            "Add priority field + filter_by_priority() + overdue() to "
            "the task tracker. test_tracker.py covers the new API."
        ),
        prompt=(
            "Extend tracker.py: (1) add a 'priority' field to the Task "
            "dataclass with default value 'MEDIUM'; (2) add a "
            "filter_by_priority(prio) method to TaskTracker that returns "
            "tasks matching that priority; (3) add an overdue() method "
            "that returns tasks whose due date is in the past. The "
            "provided test_tracker.py has new tests covering these — run "
            "pytest test_tracker.py to confirm they all pass."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=60.0,
        tags=["heavy_code", "feature", "multi_file", "verified_by_project_tests"],
        min_iterations=12,
        max_time_s=360.0,
    )
