"""Task requiring web_search — find the current Python release version.

Category: web_search
Section: general
Difficulty: medium

The agent must use ``web_search`` (or ``web_fetch``) to look up the
current stable Python release version, then write the version string
to ``python_version.txt``.

Pass criteria:
1. ``python_version.txt`` exists.
2. The file contains a string that looks like ``3.X.Y`` (regex).
3. The agent called ``web_search`` or ``web_fetch`` (verified by
   inspecting the tool-call list).

This is a "did the agent reach outside the project" probe — exactly
the class of task that G18 added support for. If the agent produces
a version string WITHOUT calling a web tool, that's a fail (it's
hallucinating).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..._base import (
    Difficulty,
    EvaluationReport,
    Section,
    TaskSpec,
)


_VERSION_RE = re.compile(r"\b3\.\d{1,2}\.\d{1,3}\b")


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    # Starting tree is empty — the agent has no local source for the
    # current Python release version.
    (root / "README.txt").write_text(
        "This workspace is intentionally empty. Use web_search to find "
        "the current stable Python release version.\n",
        encoding="utf-8",
    )


def evaluate(workspace: str, agent_output: str, tool_calls: list) -> EvaluationReport:
    root = Path(workspace)
    out_file = root / "python_version.txt"
    criteria = []

    # 1. File was created.
    c1 = out_file.is_file()
    criteria.append({"name": "python_version.txt created", "passed": c1})

    # 2. File contains a 3.X.Y version string.
    c2 = False
    if c1:
        text = out_file.read_text(encoding="utf-8", errors="replace")
        c2 = bool(_VERSION_RE.search(text))
    criteria.append({"name": "file contains a 3.X.Y version string", "passed": c2})

    # 3. The agent actually called a web tool (web_search or web_fetch).
    web_tools_used = [
        tc for tc in tool_calls
        if tc.get("name") in ("web_search", "web_fetch", "WEB_SEARCH", "WEB_FETCH")
    ]
    c3 = len(web_tools_used) >= 1
    criteria.append({
        "name": "agent called web_search or web_fetch",
        "passed": c3,
    })

    passed = all(c["passed"] for c in criteria)
    return EvaluationReport(
        passed=passed,
        reason="all criteria met" if passed else "criteria failed",
        details=f"web tool calls observed: {len(web_tools_used)}",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="general_web_search_python_version",
        section=Section.GENERAL,
        difficulty=Difficulty.MEDIUM,
        description=(
            "Use web_search to find the current stable Python release "
            "version, write it to python_version.txt."
        ),
        prompt=(
            "Use the web_search tool to find the current stable release "
            "version of Python (e.g. from python.org). Then write just "
            "the version string (like '3.13.1') to a new file called "
            "python_version.txt in the workspace root."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=40.0,
        tags=["web_search", "internet_reach"],
        min_iterations=8,
        max_time_s=240.0,
    )
