"""clew.benchmarks.diff_report — diff two scorecards for regression tracking.

Usage:
    python -m clew.benchmarks.diff_report <baseline.json> <new.json>
    clew-bench diff <baseline.json> <new.json>

Reports:
  - Which tasks flipped pass→fail (REGRESSION) and fail→pass (FIX).
  - Per-task cost/time/token deltas.
  - Aggregate pass-rate delta.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TaskDelta:
    task_id: str
    status_change: str  # "pass->fail", "fail->pass", "pass->pass", "fail->fail", "new", "removed"
    cost_delta: float
    time_delta_s: float
    tokens_delta: int
    tool_calls_delta: int
    baseline: Optional[Dict[str, Any]]
    new: Optional[Dict[str, Any]]


def _index_by_task(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["task_id"]: r for r in results}


def diff_scorecards(
    baseline: Dict[str, Any], new: Dict[str, Any]
) -> Dict[str, Any]:
    """Compute the diff between two scorecards.

    Returns a dict with:
      - summary: {baseline_pass_rate, new_pass_rate, delta}
      - regressions: list[TaskDelta] for pass→fail flips
      - fixes: list[TaskDelta] for fail→pass flips
      - new_tasks: tasks in new but not baseline
      - removed_tasks: tasks in baseline but not new
      - all_deltas: full per-task list
    """
    b_results = baseline.get("results", [])
    n_results = new.get("results", [])
    b_by = _index_by_task(b_results)
    n_by = _index_by_task(n_results)

    all_ids = set(b_by.keys()) | set(n_by.keys())
    deltas: List[TaskDelta] = []
    regressions: List[TaskDelta] = []
    fixes: List[TaskDelta] = []

    for tid in sorted(all_ids):
        b = b_by.get(tid)
        n = n_by.get(tid)
        if b is None and n is None:
            continue
        if b is None:
            d = TaskDelta(
                task_id=tid,
                status_change="new",
                cost_delta=n.get("cost_usd", 0.0),
                time_delta_s=n.get("wall_clock_s", 0.0),
                tokens_delta=n.get("tokens_in", 0) + n.get("tokens_out", 0),
                tool_calls_delta=n.get("tool_call_count", 0),
                baseline=None,
                new=n,
            )
            deltas.append(d)
            if not n.get("passed"):
                # A new task that fails isn't a regression per se, but
                # it's worth surfacing.
                pass
            continue
        if n is None:
            d = TaskDelta(
                task_id=tid,
                status_change="removed",
                cost_delta=-b.get("cost_usd", 0.0),
                time_delta_s=-b.get("wall_clock_s", 0.0),
                tokens_delta=-(b.get("tokens_in", 0) + b.get("tokens_out", 0)),
                tool_calls_delta=-b.get("tool_call_count", 0),
                baseline=b,
                new=None,
            )
            deltas.append(d)
            continue

        b_pass = bool(b.get("passed"))
        n_pass = bool(n.get("passed"))
        if b_pass and not n_pass:
            status = "pass->fail"
        elif not b_pass and n_pass:
            status = "fail->pass"
        elif b_pass and n_pass:
            status = "pass->pass"
        else:
            status = "fail->fail"

        d = TaskDelta(
            task_id=tid,
            status_change=status,
            cost_delta=n.get("cost_usd", 0.0) - b.get("cost_usd", 0.0),
            time_delta_s=n.get("wall_clock_s", 0.0) - b.get("wall_clock_s", 0.0),
            tokens_delta=(
                (n.get("tokens_in", 0) + n.get("tokens_out", 0))
                - (b.get("tokens_in", 0) + b.get("tokens_out", 0))
            ),
            tool_calls_delta=(
                n.get("tool_call_count", 0) - b.get("tool_call_count", 0)
            ),
            baseline=b,
            new=n,
        )
        deltas.append(d)
        if status == "pass->fail":
            regressions.append(d)
        elif status == "fail->pass":
            fixes.append(d)

    b_total = max(1, len(b_results))
    n_total = max(1, len(n_results))
    b_pass_rate = sum(1 for r in b_results if r.get("passed")) / b_total
    n_pass_rate = sum(1 for r in n_results if r.get("passed")) / n_total

    return {
        "summary": {
            "baseline_pass_rate": round(b_pass_rate, 4),
            "new_pass_rate": round(n_pass_rate, 4),
            "pass_rate_delta": round(n_pass_rate - b_pass_rate, 4),
            "baseline_total_cost": round(
                sum(r.get("cost_usd", 0) for r in b_results), 6
            ),
            "new_total_cost": round(
                sum(r.get("cost_usd", 0) for r in n_results), 6
            ),
            "baseline_total_time_s": round(
                sum(r.get("wall_clock_s", 0) for r in b_results), 3
            ),
            "new_total_time_s": round(
                sum(r.get("wall_clock_s", 0) for r in n_results), 3
            ),
        },
        "regressions": [d.__dict__ for d in regressions],
        "fixes": [d.__dict__ for d in fixes],
        "new_tasks": [d.__dict__ for d in deltas if d.status_change == "new"],
        "removed_tasks": [d.__dict__ for d in deltas if d.status_change == "removed"],
        "all_deltas": [d.__dict__ for d in deltas],
    }


def format_diff_report(diff: Dict[str, Any]) -> str:
    """Render a diff as a human-readable string."""
    lines: List[str] = []
    s = diff["summary"]
    lines.append("=" * 70)
    lines.append("BENCHMARK REGRESSION DIFF")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Pass rate:")
    lines.append(
        f"  baseline: {s['baseline_pass_rate']*100:.1f}%   "
        f"new: {s['new_pass_rate']*100:.1f}%   "
        f"delta: {s['pass_rate_delta']*100:+.1f}pp"
    )
    lines.append("")
    lines.append("Cost:")
    lines.append(
        f"  baseline: ${s['baseline_total_cost']:.4f}   "
        f"new: ${s['new_total_cost']:.4f}   "
        f"delta: ${s['new_total_cost'] - s['baseline_total_cost']:+.4f}"
    )
    lines.append("")
    lines.append("Wall-clock:")
    lines.append(
        f"  baseline: {s['baseline_total_time_s']:.1f}s   "
        f"new: {s['new_total_time_s']:.1f}s   "
        f"delta: {s['new_total_time_s'] - s['baseline_total_time_s']:+.1f}s"
    )
    lines.append("")

    regressions = diff["regressions"]
    if regressions:
        lines.append(f"REGRESSIONS ({len(regressions)}):")
        for d in regressions:
            n = d.get("new") or {}
            lines.append(
                f"  - {d['task_id']}: was pass, now FAIL"
                f"  (cost ${n.get('cost_usd', 0):.4f}, "
                f"{n.get('wall_clock_s', 0):.1f}s)"
            )
        lines.append("")
    else:
        lines.append("REGRESSIONS: none")
        lines.append("")

    fixes = diff["fixes"]
    if fixes:
        lines.append(f"FIXES ({len(fixes)}):")
        for d in fixes:
            lines.append(f"  + {d['task_id']}: was fail, now PASS")
        lines.append("")
    else:
        lines.append("FIXES: none")
        lines.append("")

    new_tasks = diff["new_tasks"]
    if new_tasks:
        lines.append(f"NEW TASKS ({len(new_tasks)}):")
        for d in new_tasks:
            n = d.get("new") or {}
            lines.append(
                f"  ? {d['task_id']}: pass={n.get('passed')} "
                f"(cost ${n.get('cost_usd', 0):.4f})"
            )
        lines.append("")

    removed = diff["removed_tasks"]
    if removed:
        lines.append(f"REMOVED TASKS ({len(removed)}):")
        for d in removed:
            lines.append(f"  - {d['task_id']}")
        lines.append("")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(
            "Usage: python -m clew.benchmarks.diff_report <baseline.json> <new.json>",
            file=sys.stderr,
        )
        return 2
    baseline_path = Path(argv[0])
    new_path = Path(argv[1])
    if not baseline_path.is_file():
        print(f"baseline not found: {baseline_path}", file=sys.stderr)
        return 1
    if not new_path.is_file():
        print(f"new not found: {new_path}", file=sys.stderr)
        return 1
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))
    diff = diff_scorecards(baseline, new)
    print(format_diff_report(diff))
    # Also write the diff as JSON next to the new scorecard.
    out = new_path.parent / (new_path.stem + ".diff.json")
    out.write_text(json.dumps(diff, indent=2, default=str), encoding="utf-8")
    print(f"\nDiff JSON written to: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
