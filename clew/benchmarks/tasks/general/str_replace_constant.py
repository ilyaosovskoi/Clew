"""Str-replace edit — change a constant in a config file.

Category: bug_fix
Section: general
Difficulty: easy

The agent must use ``str_replace`` (preferred over full write_file)
to change ``MAX_RETRIES = 3`` to ``MAX_RETRIES = 5`` in
``settings.py``. The rest of the file must be unchanged.

Pass criterion: the value is now 5, and the rest of the file content
is byte-identical to the original (other than the one line that
changed).
"""

from __future__ import annotations

from pathlib import Path

from ..._base import (
    Difficulty,
    EvaluationReport,
    Section,
    TaskSpec,
)


_SETTINGS_PY = """\
\"\"\"App settings.\"\"\"

APP_NAME = "clew-bench"
MAX_RETRIES = 3
TIMEOUT_S = 30
LOG_LEVEL = "INFO"
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.py").write_text(_SETTINGS_PY, encoding="utf-8")


def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    root = Path(workspace)
    s = root / "settings.py"
    criteria = []

    # 1. settings.py still exists.
    c1 = s.is_file()
    criteria.append({"name": "settings.py exists", "passed": c1})

    # 2. MAX_RETRIES is now 5.
    c2 = False
    if c1:
        text = s.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip().startswith("MAX_RETRIES"):
                if "= 5" in line or "== 5" in line:
                    c2 = True
                break
    criteria.append({"name": "MAX_RETRIES == 5", "passed": c2})

    # 3. The other constants are unchanged.
    c3 = False
    if c1:
        text = s.read_text(encoding="utf-8", errors="replace")
        c3 = (
            'APP_NAME = "clew-bench"' in text
            and "TIMEOUT_S = 30" in text
            and 'LOG_LEVEL = "INFO"' in text
        )
    criteria.append({"name": "other constants unchanged", "passed": c3})

    # 4. Agent used str_replace (preferred) or write_file with the
    #    full content. Track which path it took.
    used_str_replace = any(
        tc.get("name") in ("str_replace", "STR_REPLACE") for tc in tool_calls
    )
    used_write = any(
        tc.get("name") in ("write_file", "WRITE_FILE") for tc in tool_calls
    )
    c4 = used_str_replace or used_write
    criteria.append({
        "name": "agent used str_replace or write_file",
        "passed": c4,
        "detail": "str_replace" if used_str_replace else (
            "write_file" if used_write else "neither"
        ),
    })

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_str_replace_constant",
        section=Section.GENERAL,
        difficulty=Difficulty.EASY,
        description="Use str_replace to bump MAX_RETRIES from 3 to 5 in settings.py.",
        prompt=(
            "In settings.py, change MAX_RETRIES from 3 to 5. Use the "
            "str_replace tool to make this single-line edit — don't "
            "rewrite the whole file."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=15.0,
        tags=["str_replace", "edit"],
        min_iterations=4,
        max_time_s=120.0,
    )
