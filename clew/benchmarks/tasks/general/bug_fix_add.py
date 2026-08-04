"""Bug fix in an existing file — fix add() to actually add.

Category: bug_fix
Section: general
Difficulty: easy

The starting tree has a ``calc.py`` with a buggy ``add()`` that
returns ``a - b``. The agent must read the file, find the bug, and
fix it. Pass criterion: ``add(2, 3) == 5`` after the fix.

This is the canonical "smallest possible real coding task" — if the
agent can't do this, the rest of the suite is moot.
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
\"\"\"A tiny calculator module. There is a bug in add() — fix it.\"\"\"

def add(a, b):
    # BUG: this currently subtracts instead of adding.
    return a - b

def sub(a, b):
    return a - b
"""


_CALC_TEST = """\
\"\"\"Project's own tests — must still pass after the fix.\"\"\"
from calc import add, sub

def test_add():
    assert add(2, 3) == 5

def test_sub():
    assert sub(5, 2) == 3
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(_CALC_PY, encoding="utf-8")
    (root / "test_calc.py").write_text(_CALC_TEST, encoding="utf-8")


def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    root = Path(workspace)
    calc = root / "calc.py"
    criteria = []

    # 1. calc.py still exists.
    c1 = calc.is_file()
    criteria.append({"name": "calc.py exists", "passed": c1})

    # 2. add() still defined.
    c2 = function_exists(calc, "add")
    criteria.append({"name": "add() still defined", "passed": c2})

    # 3. The bug is fixed — "a - b" should no longer be the body of add.
    #    We check the file content for the tell-tale buggy return.
    text = calc.read_text(encoding="utf-8", errors="replace") if calc.is_file() else ""
    # Find the body of add() — look at lines between "def add(" and the
    # next "def " at the same indent level.
    has_bug = False
    in_add = False
    for line in text.splitlines():
        if line.startswith("def add("):
            in_add = True
            continue
        if in_add:
            if line.startswith("def "):
                # Next function — exit add().
                in_add = False
                break
            if "return a - b" in line:
                has_bug = True
                break
    c3 = not has_bug
    criteria.append({"name": "bug (return a - b) removed", "passed": c3})

    # 4. The fix is actually correct — import and call add(2, 3) == 5.
    c4 = False
    if c2 and c3:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("calc_under_test", calc)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                c4 = (mod.add(2, 3) == 5)
        except Exception:
            c4 = False
    criteria.append({"name": "add(2, 3) == 5", "passed": c4})

    passed = all(c["passed"] for c in criteria)
    reason = "all criteria met" if passed else "one or more criteria failed"
    return EvaluationReport(
        passed=passed, reason=reason, checked_criteria=criteria
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_bug_fix_add",
        section=Section.GENERAL,
        difficulty=Difficulty.EASY,
        description="Fix the buggy add() in calc.py (returns a-b instead of a+b).",
        prompt=(
            "There's a bug in calc.py — the add() function is subtracting "
            "instead of adding. Fix it so add(2, 3) returns 5. After fixing, "
            "run the project's tests (python -m pytest test_calc.py) to "
            "confirm both test_add and test_sub pass."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=20.0,
        tags=["bug_fix", "python"],
        min_iterations=6,
        max_time_s=180.0,
    )
