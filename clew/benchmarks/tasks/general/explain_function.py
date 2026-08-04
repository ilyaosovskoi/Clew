"""Fuzzy task — explain what a function does.

Category: explanation
Section: general
Difficulty: easy

The agent is asked to explain what ``mystery_function`` does. The pass
criterion is mechanical: did the agent call ``read_file`` on
``mystery.py`` AND produce a non-empty final answer?

This is the "inherently fuzzy" case the prompt explicitly calls out —
we don't grade the explanation with a second LLM, we just check the
agent did the minimum reasonable thing (read the file, then answered).
"""

from __future__ import annotations

from pathlib import Path

from ..._base import (
    Difficulty,
    EvaluationReport,
    Section,
    TaskSpec,
)


_MYSTERY_PY = """\
\"\"\"A module with one function.\"\"\"

def mystery_function(items, threshold=10):
    kept = []
    skipped = 0
    for x in items:
        if x > threshold:
            kept.append(x * 2)
        else:
            skipped += 1
    return kept, skipped
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "mystery.py").write_text(_MYSTERY_PY, encoding="utf-8")


def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    criteria = []

    # 1. Agent called read_file on mystery.py.
    read_calls = [
        tc for tc in tool_calls
        if tc.get("name") in ("read_file", "READ_FILE")
        and "mystery" in str(tc.get("args", {}))
    ]
    c1 = len(read_calls) >= 1
    criteria.append({
        "name": "agent called read_file on mystery.py",
        "passed": c1,
    })

    # 2. Final answer is non-empty and mentions at least one
    #    conceptually-correct keyword (filter, threshold, double, skip).
    keywords = ["filter", "threshold", "double", "skip", "kept", "items", "multiply"]
    out_lower = (agent_output or "").lower()
    c2 = bool(out_lower.strip()) and any(kw in out_lower for kw in keywords)
    criteria.append({
        "name": "final answer is non-empty and references a key concept",
        "passed": c2,
    })

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        details=f"read_calls: {len(read_calls)}, output_len: {len(agent_output or '')}",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_explain_function",
        section=Section.GENERAL,
        difficulty=Difficulty.EASY,
        description="Explain what mystery_function does (fuzzy criteria).",
        prompt=(
            "Read mystery.py and explain what mystery_function does in "
            "plain English. Be concise — a few sentences is fine."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=15.0,
        tags=["explanation", "fuzzy"],
        min_iterations=4,
        max_time_s=120.0,
    )
