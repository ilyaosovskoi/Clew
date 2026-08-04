"""Refactor that must NOT change behavior — verified by the project's tests.

Category: refactor
Section: general
Difficulty: medium

Starting tree: ``string_utils.py`` with three functions written in a
naive, repetitive style. The agent must refactor each function to use
a more idiomatic Python pattern (e.g. ``count_vowels`` should use a
generator + ``sum()``, ``reverse`` should use slicing, ``is_palindrome``
should compose the other two) WITHOUT changing the behavior.

Pass criterion: all existing tests in ``test_string_utils.py`` still
pass after the refactor, AND each function body has actually changed
(the agent didn't just leave the file untouched).
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


_STRING_UTILS_PY = """\
\"\"\"String utilities — naive style, ripe for refactoring.\"\"\"

def count_vowels(s):
    count = 0
    for ch in s:
        if ch == 'a' or ch == 'e' or ch == 'i' or ch == 'o' or ch == 'u':
            count = count + 1
        elif ch == 'A' or ch == 'E' or ch == 'I' or ch == 'O' or ch == 'U':
            count = count + 1
    return count

def reverse(s):
    result = ""
    i = len(s) - 1
    while i >= 0:
        result = result + s[i]
        i = i - 1
    return result

def is_palindrome(s):
    if s == reverse(s):
        return True
    else:
        return False
"""


_STRING_UTILS_TEST = """\
\"\"\"Project's own tests — must keep passing after the refactor.\"\"\"
from string_utils import count_vowels, reverse, is_palindrome

def test_count_vowels_basic():
    assert count_vowels("hello") == 2
    assert count_vowels("HELLO") == 2
    assert count_vowels("xyz") == 0
    assert count_vowels("aeiou") == 5
    assert count_vowels("") == 0

def test_reverse():
    assert reverse("hello") == "olleh"
    assert reverse("") == ""
    assert reverse("a") == "a"
    assert reverse("ab") == "ba"

def test_is_palindrome():
    assert is_palindrome("racecar") == True
    assert is_palindrome("hello") == False
    assert is_palindrome("") == True
    assert is_palindrome("a") == True
"""


_ORIGINAL_BODIES = {
    "count_vowels": "count = 0",
    "reverse": "i = len(s) - 1",
    "is_palindrome": "if s == reverse(s)",
}


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "string_utils.py").write_text(_STRING_UTILS_PY, encoding="utf-8")
    (root / "test_string_utils.py").write_text(_STRING_UTILS_TEST, encoding="utf-8")


def _run_pytest(workspace: str) -> tuple[bool, str]:
    """Run ``python -m pytest test_string_utils.py`` in ``workspace``.
    Returns (passed, output)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_string_utils.py", "-v"],
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
    su = root / "string_utils.py"
    criteria = []

    # 1. string_utils.py exists.
    c1 = su.is_file()
    criteria.append({"name": "string_utils.py exists", "passed": c1})

    # 2. All three functions still defined.
    c2 = (
        function_exists(su, "count_vowels")
        and function_exists(su, "reverse")
        and function_exists(su, "is_palindrome")
    )
    criteria.append({"name": "all three functions still defined", "passed": c2})

    # 3. Each function body has actually changed (agent didn't just leave file).
    text = su.read_text(encoding="utf-8", errors="replace") if su.is_file() else ""
    c3 = True
    for fname, sentinel in _ORIGINAL_BODIES.items():
        # The original body had this exact line. After refactor, it
        # should be GONE — the body should be different.
        if sentinel in text:
            c3 = False
            break
    criteria.append({"name": "all three function bodies changed", "passed": c3})

    # 4. The project's own tests still pass.
    c4, test_output = _run_pytest(workspace)
    criteria.append({"name": "test_string_utils.py passes", "passed": c4})

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        details=test_output if not c4 else "",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_refactor_no_behavior_change",
        section=Section.GENERAL,
        difficulty=Difficulty.MEDIUM,
        description=(
            "Refactor string_utils.py to use idiomatic Python "
            "(generators, slicing, composition) without breaking tests."
        ),
        prompt=(
            "Refactor string_utils.py to be more idiomatic Python: "
            "count_vowels() should use sum()+generator; reverse() should "
            "use slicing; is_palindrome() should compose the other two. "
            "Do NOT change the function signatures or behavior — the "
            "existing tests in test_string_utils.py MUST still pass. "
            "After refactoring, run pytest test_string_utils.py to verify."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=40.0,
        tags=["refactor", "python", "verified_by_project_tests"],
        min_iterations=10,
        max_time_s=300.0,
    )
