"""Heavy_code task — refactor 3 files in parallel via subagents.

Category: heavy_code
Section: heavy_code
Difficulty: hard

The agent is asked to "refactor these 3 modules to use type hints"
and is expected to spawn 3 parallel subagents (one per module) using
``spawn_multi_agents``. Each module needs ``-> int`` etc. added to
function signatures.

Pass criteria:
1. All 3 modules now have type hints on at least one function
   signature.
2. The project's tests still pass.
3. The agent either called spawn_multi_agents OR completed all 3
   edits within a single agent loop (acceptable — heavy_code doesn't
   REQUIRE spawning subagents, but it's the canonical use case).

This is the canonical heavy_code task — exactly the kind of "fan out
to parallel subagents" workflow the heavy_code section exists for.
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


_MOD_A = """\
\"\"\"Module A.\"\"\"

def add(a, b):
    return a + b

def mul(a, b):
    return a * b
"""

_MOD_B = """\
\"\"\"Module B.\"\"\"

def sub(a, b):
    return a - b

def div(a, b):
    return a / b if b != 0 else 0
"""

_MOD_C = """\
\"\"\"Module C.\"\"\"

def is_even(n):
    return n % 2 == 0

def is_odd(n):
    return n % 2 != 0
"""

_TESTS = """\
\"\"\"Tests covering all three modules.\"\"\"
from mod_a import add, mul
from mod_b import sub, div
from mod_c import is_even, is_odd

def test_a():
    assert add(2, 3) == 5
    assert mul(4, 5) == 20

def test_b():
    assert sub(5, 2) == 3
    assert div(10, 2) == 5
    assert div(10, 0) == 0

def test_c():
    assert is_even(4) == True
    assert is_odd(5) == True
    assert is_even(5) == False
"""


def _has_return_hint(text: str, func_name: str) -> bool:
    """True if ``def <func_name>(...) -> <type>:`` appears in text."""
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("def " + func_name + "(") and "->" in s:
            return True
        if s.startswith("async def " + func_name + "(") and "->" in s:
            return True
    return False


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod_a.py").write_text(_MOD_A, encoding="utf-8")
    (root / "mod_b.py").write_text(_MOD_B, encoding="utf-8")
    (root / "mod_c.py").write_text(_MOD_C, encoding="utf-8")
    (root / "test_mods.py").write_text(_TESTS, encoding="utf-8")


def _run_pytest(workspace: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_mods.py", "-v"],
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
    criteria = []

    # 1. mod_a.py has at least one -> hint.
    text_a = (root / "mod_a.py").read_text(encoding="utf-8", errors="replace")
    c1 = _has_return_hint(text_a, "add") or _has_return_hint(text_a, "mul")
    criteria.append({"name": "mod_a.py has type hints", "passed": c1})

    # 2. mod_b.py.
    text_b = (root / "mod_b.py").read_text(encoding="utf-8", errors="replace")
    c2 = _has_return_hint(text_b, "sub") or _has_return_hint(text_b, "div")
    criteria.append({"name": "mod_b.py has type hints", "passed": c2})

    # 3. mod_c.py.
    text_c = (root / "mod_c.py").read_text(encoding="utf-8", errors="replace")
    c3 = _has_return_hint(text_c, "is_even") or _has_return_hint(text_c, "is_odd")
    criteria.append({"name": "mod_c.py has type hints", "passed": c3})

    # 4. Tests still pass.
    c4, test_output = _run_pytest(workspace)
    criteria.append({"name": "test_mods.py passes", "passed": c4})

    # 5. (Informational) Did the agent spawn subagents?
    spawn_calls = [
        tc for tc in tool_calls
        if tc.get("name") in ("spawn_subagent", "spawn_multi_agents",
                              "SPAWN_SUBAGENT", "SPAWN_MULTI_AGENTS")
    ]
    c5 = len(spawn_calls) >= 1
    criteria.append({
        "name": "agent spawned subagent(s) for parallel work",
        "passed": c5,
    })

    # Mandatory: 1-4. Subagent spawn is informational.
    mandatory = [c["passed"] for c in criteria[:4]]
    passed = all(mandatory)
    return EvaluationReport(
        passed=passed,
        reason="all mandatory criteria met" if passed else "mandatory criteria failed",
        details=test_output if not c4 else "",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="heavy_code_parallel_type_hints",
        section=Section.HEAVY_CODE,
        difficulty=Difficulty.HARD,
        description=(
            "Refactor 3 modules (mod_a/b/c.py) to add return type hints. "
            "Spawn parallel subagents (one per module) for the work."
        ),
        prompt=(
            "Refactor mod_a.py, mod_b.py, and mod_c.py to add return type "
            "hints (-> int, -> bool, -> float as appropriate) to every "
            "function. The existing tests in test_mods.py MUST still pass. "
            "Use spawn_multi_agents to do the three edits in parallel — "
            "one subagent per module."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=60.0,
        tags=["heavy_code", "refactor", "parallel", "subagents", "verified_by_project_tests"],
        min_iterations=15,
        max_time_s=420.0,
    )
