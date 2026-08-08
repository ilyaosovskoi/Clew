"""
System prompts for the Clew Builder loop.

Three roles:

  * PLANNER  — reads task + relevant source files, produces a concrete
    step-by-step plan (files to touch, functions to add, tests to add).
  * IMPLEMENTER — fed to AgentRuntime.run() as the user prompt. It
    includes the plan so the agent's ReAct loop follows the planner's
    outline instead of improvising.
  * REVIEWER — reads the diff + verification output and judges whether
    the task is actually done (used by the loop's verify step, in
    addition to mechanical success-criteria checks).

  * FAILURE_DIGEST — formats prior failure reasons for the NEXT
    planning attempt, so the loop doesn't repeat itself.

Prompts are deliberately short and instruction-dense. Llama-3.1-70b
on NIM is good at following structured instructions; long meandering
preamble just wastes tokens.
"""

from __future__ import annotations

from typing import List, Optional

# ── Planner ───────────────────────────────────────────────────────

PLANNER_SYSTEM = """\
You are the PLANNER sub-agent of the Clew Builder — an autonomous \
self-improvement loop that modifies Clew's own source code.

Your job: given a TASK and a SNAPSHOT of the relevant source files, \
produce a concrete, minimal, verifiable implementation plan.

Rules:
1. Read the existing code carefully. Match its style (typing, logging, \
   docstrings, naming).
2. Do NOT propose changes outside the scope of the TASK. Smaller is better.
3. Each step must name the exact file path and the change (new function, \
   modified method, new file, etc).
4. List the verifiable success criteria — concrete things a test or a \
   smoke check can confirm.
5. If the TASK is ambiguous, pick the most conservative interpretation \
   and note the assumption in the plan.
6. Output STRICTLY in this format:

   PLAN:
   1. <step>
   2. <step>
   ...

   FILES_TOUCHED:
   - <path>
   - <path>

   SUCCESS_CRITERIA:
   - <criterion>
   - <criterion>

   RISKS:
   - <risk or "none">

No prose outside this format. No markdown fences.\
"""


def build_planner_prompt(
    task_title: str,
    task_raw: str,
    success_criteria: List[str],
    file_snapshot: str,
    prior_failures: Optional[List[str]] = None,
) -> str:
    """Assemble the planner user-prompt."""
    parts = []
    parts.append(f"TASK:\n{task_title}\n")
    if task_raw and task_raw != task_title:
        parts.append(f"TASK DETAILS:\n{task_raw}\n")
    if success_criteria:
        parts.append("USER-SPECIFIED SUCCESS CRITERIA:\n" + "\n".join(f"- {c}" for c in success_criteria) + "\n")
    if prior_failures:
        parts.append("PRIOR FAILED ATTEMPTS — do NOT repeat these mistakes:")
        for i, f in enumerate(prior_failures, 1):
            parts.append(f"  {i}. {f}")
        parts.append("")
    parts.append("RELEVANT SOURCE SNAPSHOT:\n```")
    parts.append(file_snapshot if file_snapshot else "(no snapshot provided — explore with the agent)")
    parts.append("```")
    return "\n".join(parts)


# ── Implementer (fed to AgentRuntime.run) ─────────────────────────

IMPLEMENTER_SYSTEM = """\
You are the IMPLEMENTER sub-agent of the Clew Builder. You are running \
INSIDE Clew's own AgentRuntime — every tool call you make goes through \
the Guardian, sandbox, and audit trail.

Your job: execute the given PLAN exactly. Do not improvise scope. Do \
not refactor unrelated code. If a step is unclear, pick the most \
conservative interpretation and proceed — do NOT stop to ask.

Hard rules:
- Only edit files inside the workspace.
- After every edit, run the smoke check `python -c "import clew"` to \
  confirm the package still imports.
- If an edit breaks the import smoke check, revert it and try a \
  different approach.
- When you're done, output a final summary starting with `DONE:` \
  followed by a one-line description of what you changed.\
"""


def build_implementer_prompt(
    task_title: str,
    plan: str,
    success_criteria: List[str],
) -> str:
    """Assemble the prompt fed to AgentRuntime.run()."""
    parts = [
        f"TASK: {task_title}",
        "",
        "PLAN TO EXECUTE (follow exactly):",
        plan.strip(),
        "",
    ]
    if success_criteria:
        parts.append("SUCCESS CRITERIA (verify each before declaring DONE):")
        for c in success_criteria:
            parts.append(f"- {c}")
        parts.append("")
    parts.append(
        "Execute the plan using your tools. Use `search_project` to find "
        "the exact paths if the plan references a file you haven't read. "
        "After every file edit, run `run_code` with `python -c \"import clew\"` "
        "to confirm the package still imports. When finished, output a line "
        "starting with `DONE:` summarising what you changed."
    )
    return "\n".join(parts)


# ── Reviewer ──────────────────────────────────────────────────────

REVIEWER_SYSTEM = """\
You are the REVIEWER sub-agent of the Clew Builder.

Given:
  - the TASK
  - the PLAN
  - the GIT DIFF of what changed
  - the VERIFICATION OUTPUT (test results, smoke checks)
  - the SUCCESS CRITERIA

Your job: judge whether the task is actually DONE. Output a single \
line in this exact format:

   VERDICT: PASS
or
   VERDICT: FAIL
   REASON: <one short sentence>
   NEXT_STEP: <one short suggestion for the next retry>

Be strict. If any success criterion is not clearly met, output FAIL. \
If the diff is empty, output FAIL. If verification errored, output FAIL.\
"""


def build_reviewer_prompt(
    task_title: str,
    plan: str,
    diff: str,
    verification_output: str,
    success_criteria: List[str],
) -> str:
    parts = [
        f"TASK: {task_title}",
        "",
        "PLAN:",
        plan.strip() or "(no plan)",
        "",
        "SUCCESS CRITERIA:",
    ]
    if success_criteria:
        for c in success_criteria:
            parts.append(f"- {c}")
    else:
        parts.append("- (no explicit criteria — judge by the task title)")
    parts.append("")
    parts.append("GIT DIFF:")
    parts.append("```diff")
    parts.append(diff[:30_000] if diff else "(empty)")
    parts.append("```")
    parts.append("")
    parts.append("VERIFICATION OUTPUT:")
    parts.append("```")
    parts.append(verification_output[:8_000] if verification_output else "(no verification output)")
    parts.append("```")
    parts.append("")
    parts.append(
        "Output a single line in the exact format: "
        "`VERDICT: PASS` or `VERDICT: FAIL` (followed by REASON: and "
        "NEXT_STEP: on subsequent lines if FAIL)."
    )
    return "\n".join(parts)


# ── Failure digest (fed back into next planner attempt) ───────────

def format_prior_failures(attempts: List) -> List[str]:
    """Extract short failure descriptions from prior TaskAttempt records.

    Returns at most 3 strings — enough context without blowing the prompt.
    """
    out: List[str] = []
    # Most recent first (so the planner sees the freshest mistake last).
    for a in reversed(attempts):
        if a.verification_passed:
            continue
        reason = a.error or "verification failed (no explicit error)"
        # Truncate hard — the planner doesn't need the full traceback.
        reason = reason.split("\n", 1)[0][:200]
        out.append(f"attempt #{a.attempt_number}: {reason}")
        if len(out) >= 3:
            break
    return out
