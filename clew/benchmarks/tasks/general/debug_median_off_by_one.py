"""Debug task — find why a function returns the wrong result.

Category: bug_fix
Section: general
Difficulty: medium

Starting tree: ``stats.py`` with a ``median()`` function that has an
off-by-one bug — it sorts the list then returns the middle element
without checking whether the list has even length (in which case the
median is the average of the two middle elements).

The agent must:
1. Read stats.py.
2. Identify the bug.
3. Fix it.
4. Run the existing tests in test_stats.py to confirm.

Pass criterion: median([1, 2, 3, 4]) returns 2.5 (currently returns
2 due to the bug), and all existing tests still pass.
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
    function_exists,
)


_STATS_PY = """\
\"\"\"Statistics helpers.\"\"\"

def median(values):
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    # BUG: returns the middle element regardless of parity.
    return sorted_vals[n // 2]

def mean(values):
    return sum(values) / len(values) if values else 0
"""


_STATS_TEST = """\
\"\"\"Tests for stats helpers.\"\"\"
from stats import median, mean

def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5
    assert mean([]) == 0

def test_median_odd():
    # Odd-length list: middle element.
    assert median([1, 3, 2]) == 2
    assert median([5, 1, 3]) == 3

def test_median_even():
    # Even-length list: average of two middle elements.
    # THIS TEST WILL FAIL until the bug is fixed.
    assert median([1, 2, 3, 4]) == 2.5
    assert median([10, 20, 30, 40]) == 25.0
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "stats.py").write_text(_STATS_PY, encoding="utf-8")
    (root / "test_stats.py").write_text(_STATS_TEST, encoding="utf-8")


def _run_pytest(workspace: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_stats.py", "-v"],
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
    stats = root / "stats.py"
    criteria = []

    c1 = function_exists(stats, "median")
    criteria.append({"name": "median() still exists", "passed": c1})

    # 2. median([1, 2, 3, 4]) returns 2.5 (the bug fix).
    c2 = False
    if c1:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("stats_u", stats)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                c2 = (mod.median([1, 2, 3, 4]) == 2.5)
        except Exception:
            c2 = False
    criteria.append({"name": "median([1,2,3,4]) == 2.5", "passed": c2})

    # 3. median still works for odd-length lists.
    c3 = False
    if c1:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("stats_u2", stats)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                c3 = (mod.median([1, 3, 2]) == 2)
        except Exception:
            c3 = False
    criteria.append({"name": "median([1,3,2]) == 2 (odd case still works)", "passed": c3})

    # 4. Existing tests pass.
    c4, test_output = _run_pytest(workspace)
    criteria.append({"name": "test_stats.py passes", "passed": c4})

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        details=test_output if not c4 else "",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_debug_median_off_by_one",
        section=Section.GENERAL,
        difficulty=Difficulty.MEDIUM,
        description="Debug median() off-by-one — even-length lists return wrong value.",
        prompt=(
            "There's a bug in stats.py: median() returns the wrong value "
            "for even-length lists. The existing test_median_even test in "
            "test_stats.py is failing. Read the file, find the bug, fix it "
            "(median of [1,2,3,4] should be 2.5 = average of 2 and 3), then "
            "run pytest test_stats.py to confirm all tests pass."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=35.0,
        tags=["debug", "off_by_one", "verified_by_project_tests"],
        min_iterations=8,
        max_time_s=240.0,
    )
