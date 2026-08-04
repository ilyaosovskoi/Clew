"""Multi-step task — add a new module that depends on existing one + tests.

Category: new_feature
Section: general
Difficulty: medium

Starting tree: ``shapes.py`` with a ``Circle`` class. The agent must:
1. Read shapes.py to understand the existing Circle API.
2. Add a ``Rectangle`` class to shapes.py (with area() + perimeter()).
3. Create ``test_shapes.py`` with tests for BOTH Circle and Rectangle.
4. Run pytest to confirm everything passes.

Pass criterion: Rectangle class exists with area() and perimeter();
test_shapes.py covers both classes; pytest passes.
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


_SHAPES_PY = """\
\"\"\"Geometric shapes.\"\"\"

import math

class Circle:
    def __init__(self, radius):
        self.radius = radius

    def area(self):
        return math.pi * self.radius ** 2

    def perimeter(self):
        return 2 * math.pi * self.radius
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "shapes.py").write_text(_SHAPES_PY, encoding="utf-8")


def _run_pytest(workspace: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_shapes.py", "-v"],
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
    shapes = root / "shapes.py"
    test_shapes = root / "test_shapes.py"
    criteria = []

    # 1. Rectangle class exists in shapes.py.
    text = shapes.read_text(encoding="utf-8", errors="replace") if shapes.is_file() else ""
    c1 = "class Rectangle" in text
    criteria.append({"name": "Rectangle class added to shapes.py", "passed": c1})

    # 2. Rectangle.area() and Rectangle.perimeter() exist.
    c2 = (
        "def area" in text
        and "def perimeter" in text
        and text.count("def area") >= 2  # Circle + Rectangle
    )
    criteria.append({
        "name": "Rectangle has area() and perimeter()",
        "passed": c2,
    })

    # 3. test_shapes.py exists and tests both Circle and Rectangle.
    c3 = False
    if test_shapes.is_file():
        ttext = test_shapes.read_text(encoding="utf-8", errors="replace")
        c3 = ("Circle" in ttext) and ("Rectangle" in ttext) and ("def test" in ttext)
    criteria.append({
        "name": "test_shapes.py covers both Circle and Rectangle",
        "passed": c3,
    })

    # 4. pytest passes.
    c4, test_output = _run_pytest(workspace)
    criteria.append({"name": "pytest passes", "passed": c4})

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        details=test_output if not c4 else "",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_multi_step_shapes",
        section=Section.GENERAL,
        difficulty=Difficulty.MEDIUM,
        description=(
            "Read shapes.py, add a Rectangle class with area()+perimeter(), "
            "create test_shapes.py covering both classes, run pytest."
        ),
        prompt=(
            "Add a Rectangle class to shapes.py (matching the existing "
            "Circle class's style) — it should have __init__(self, width, "
            "height), area(), and perimeter(). Then create test_shapes.py "
            "with pytest tests covering BOTH Circle and Rectangle. After "
            "creating the tests, run pytest to confirm they pass."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=45.0,
        tags=["new_feature", "multi_step", "multi_file", "verified_by_project_tests"],
        min_iterations=10,
        max_time_s=300.0,
    )
