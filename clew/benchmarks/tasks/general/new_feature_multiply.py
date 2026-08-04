"""New feature across 2+ files — add a multiply() function + its tests.

Category: new_feature
Section: general
Difficulty: easy

Starting tree: ``calc.py`` with ``add`` and ``sub``. The agent must
add ``multiply(a, b)`` AND create ``test_multiply.py`` with at least
one test for it. Verifies the agent can coordinate changes across
more than one file.
"""

from __future__ import annotations

from pathlib import Path

from ..._base import (
    Difficulty,
    EvaluationReport,
    Section,
    TaskSpec,
    function_exists,
)


_CALC_PY = """\
\"\"\"A tiny calculator module.\"\"\"

def add(a, b):
    return a + b

def sub(a, b):
    return a - b
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(_CALC_PY, encoding="utf-8")


def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    root = Path(workspace)
    calc = root / "calc.py"
    test_multiply = root / "test_multiply.py"
    criteria = []

    c1 = function_exists(calc, "multiply")
    criteria.append({"name": "multiply() exists in calc.py", "passed": c1})

    # 2. multiply() actually multiplies.
    c2 = False
    if c1:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("calc_mul", calc)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                c2 = (mod.multiply(3, 4) == 12)
        except Exception:
            c2 = False
    criteria.append({"name": "multiply(3, 4) == 12", "passed": c2})

    # 3. A test file was created for multiply.
    c3 = test_multiply.is_file()
    criteria.append({"name": "test_multiply.py created", "passed": c3})

    # 4. The test file imports multiply from calc and references it.
    c4 = False
    if c3:
        text = test_multiply.read_text(encoding="utf-8", errors="replace")
        c4 = ("multiply" in text) and ("calc" in text) and ("def test" in text)
    criteria.append({
        "name": "test_multiply.py imports multiply and has a test function",
        "passed": c4,
    })

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_new_feature_multiply",
        section=Section.GENERAL,
        difficulty=Difficulty.EASY,
        description="Add multiply() to calc.py and create test_multiply.py.",
        prompt=(
            "Add a multiply(a, b) function to calc.py that returns a*b. "
            "Then create test_multiply.py with at least one pytest test "
            "for it (covering 3*4 == 12). Don't change add() or sub()."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=25.0,
        tags=["new_feature", "python", "multi_file"],
        min_iterations=8,
        max_time_s=240.0,
    )
