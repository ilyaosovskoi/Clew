"""
Reporter — writes per-task markdown reports + a final run summary.

Per-task reports go to  <workspace>/.clew/builder_reports/<NN>-<slug>.md
The final run summary goes to  <workspace>/.clew/builder_reports/_summary.md

The reports are intentionally detailed so a human can audit what the
autonomous loop did WITHOUT having to read git log + diff manually.
Each report contains:

  * Task title + status
  * Number of attempts + which one succeeded
  * The planner's plan
  * The git diff (truncated)
  * Verification output (truncated)
  * Reviewer verdict
  * Token usage + model used
  * Files changed

The summary report has a table of all tasks with their statuses +
aggregate token counts + time spent.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .state import TaskAttempt, TaskRecord, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class ReporterPaths:
    workspace: str
    reports_dir: str  # <workspace>/.clew/builder_reports/


class Reporter:
    """Markdown report writer."""

    def __init__(self, paths: ReporterPaths) -> None:
        self._paths = paths
        Path(self._paths.reports_dir).mkdir(parents=True, exist_ok=True)

    # ── Per-task report ───────────────────────────────────────────

    def write_task_report(
        self,
        task_num: int,
        record: TaskRecord,
        reviewer_verdict: Optional[str] = None,
    ) -> Path:
        """Write a per-task markdown report. Returns the path."""
        path = Path(self._paths.reports_dir) / f"{task_num:02d}-{record.slug}.md"
        successful_attempt = next(
            (a for a in reversed(record.attempts) if a.verification_passed),
            None,
        )
        last_attempt = record.attempts[-1] if record.attempts else None

        lines: List[str] = []
        lines.append(f"# Task {task_num:02d}: {record.title}")
        lines.append("")
        lines.append(f"**Status:** `{record.status.value}`")
        lines.append(f"**Attempts:** {len(record.attempts)}")
        if successful_attempt:
            lines.append(f"**Succeeded on attempt:** #{successful_attempt.attempt_number}")
        if last_attempt and last_attempt.model_used:
            lines.append(f"**Model:** `{last_attempt.model_used}`")
        if reviewer_verdict:
            lines.append(f"**Reviewer verdict:**")
            lines.append("```")
            lines.append(reviewer_verdict.strip())
            lines.append("```")
        lines.append("")
        lines.append("## Attempts")
        for a in record.attempts:
            lines.append("")
            lines.append(f"### Attempt #{a.attempt_number}")
            lines.append(f"- started: `{a.started_at}`  ended: `{a.ended_at}`")
            lines.append(f"- branch: `{a.branch}`")
            lines.append(f"- verification: {'PASS' if a.verification_passed else 'FAIL'}")
            if a.error:
                lines.append(f"- error: `{a.error}`")
            lines.append(f"- tokens: in={a.tokens_in} out={a.tokens_out}")
            if a.files_changed:
                lines.append("- files changed:")
                for f in a.files_changed:
                    lines.append(f"  - `{f}`")
            if a.plan:
                lines.append("")
                lines.append("#### Plan")
                lines.append("```")
                lines.append(a.plan)
                lines.append("```")
            if a.verification_output:
                lines.append("")
                lines.append("#### Verification output (truncated)")
                lines.append("```")
                lines.append(a.verification_output[:4000])
                lines.append("```")

        if last_attempt and last_attempt.files_changed:
            lines.append("")
            lines.append("## Files changed (last attempt)")
            for f in last_attempt.files_changed:
                lines.append(f"- `{f}`")

        if record.last_error and record.status == TaskStatus.FAILED:
            lines.append("")
            lines.append("## Final error")
            lines.append("```")
            lines.append(record.last_error)
            lines.append("```")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[builder-report] wrote %s", path)
        return path

    # ── Run summary ───────────────────────────────────────────────

    def write_summary(
        self,
        records: List[TaskRecord],
        nim_stats: dict,
        started_at: datetime.datetime,
        ended_at: datetime.datetime,
    ) -> Path:
        """Write the final run summary. Returns the path."""
        path = Path(self._paths.reports_dir) / "_summary.md"
        duration = (ended_at - started_at).total_seconds()

        by_status = {}
        for r in records:
            by_status[r.status.value] = by_status.get(r.status.value, 0) + 1

        total_tokens_in = sum(a.tokens_in for r in records for a in r.attempts)
        total_tokens_out = sum(a.tokens_out for r in records for a in r.attempts)
        total_attempts = sum(len(r.attempts) for r in records)

        lines: List[str] = []
        lines.append("# Clew Builder — Run Summary")
        lines.append("")
        lines.append(f"**Started:** {started_at.isoformat()}Z")
        lines.append(f"**Ended:** {ended_at.isoformat()}Z")
        lines.append(f"**Duration:** {duration:.0f}s ({duration/60:.1f} min)")
        lines.append(f"**Tasks:** {len(records)}  **Attempts:** {total_attempts}")
        lines.append("")
        lines.append("## Status breakdown")
        for status, count in sorted(by_status.items()):
            lines.append(f"- `{status}`: {count}")
        lines.append("")
        lines.append("## Tasks")
        lines.append("")
        lines.append("| # | Status | Title | Attempts | Last model |")
        lines.append("|---|--------|-------|----------|------------|")
        for i, r in enumerate(records, 1):
            last_model = r.attempts[-1].model_used if r.attempts else ""
            lines.append(
                f"| {i} | `{r.status.value}` | {r.title} | {len(r.attempts)} | `{last_model}` |"
            )
        lines.append("")
        lines.append("## NIM pool stats")
        lines.append(f"- total requests: `{nim_stats.get('total_requests', 0)}`")
        lines.append(f"- throttled secs: `{nim_stats.get('throttled_secs', 0)}`")
        lines.append(f"- tokens in: `{nim_stats.get('tokens_in', 0)}`")
        lines.append(f"- tokens out: `{nim_stats.get('tokens_out', 0)}`")
        lines.append(f"- rpm limit: `{nim_stats.get('rpm_limit', 0)}`")
        lines.append("")
        lines.append("## Aggregate token usage (all attempts)")
        lines.append(f"- tokens in: `{total_tokens_in}`")
        lines.append(f"- tokens out: `{total_tokens_out}`")
        lines.append(f"- total: `{total_tokens_in + total_tokens_out}`")
        lines.append("")
        lines.append("## Failed tasks (if any)")
        failed = [r for r in records if r.status == TaskStatus.FAILED]
        if not failed:
            lines.append("(none)")
        else:
            for r in failed:
                lines.append(f"- **{r.title}** — last error: `{r.last_error}`")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[builder-report] wrote summary %s", path)
        return path
