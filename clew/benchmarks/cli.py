"""clew.benchmarks.cli — `clew-bench` command-line entry point.

Usage:
    clew-bench list                       # list all available tasks
    clew-bench run [--dry-run]            # validate / run the suite
    clew-bench run --provider groq --model llama-3.3-70b-versatile
    clew-bench run --mock-provider        # use FakeProvider (no API calls)
    clew-bench run --task general_bug_fix_add --task general_new_feature_multiply
    clew-bench run --section general
    clew-bench diff <baseline.json> <new.json>

The ``run`` command writes a scorecard JSON to
``clew/benchmarks/results/``. The ``--dry-run`` mode is the only path
that runs in normal CI — it validates every task's starting tree +
criteria without calling any LLM.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from . import (
    BenchmarkRunner,
    RunConfig,
    Section,
    load_all_tasks,
)
from .diff_report import diff_scorecards, format_diff_report


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clew-bench",
        description=(
            "Clew agent-quality benchmark harness. Runs a fixed set of "
            "self-contained tasks against the AgentRuntime and records "
            "pass/fail + cost + time. Costs REAL API money unless "
            "--dry-run or --mock-provider is used."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="List all available benchmark tasks.")

    # run
    run_p = sub.add_parser("run", help="Run the benchmark suite.")
    run_p.add_argument(
        "--dry-run", action="store_true",
        help="Validate every task's starting tree + criteria without "
             "calling any LLM. Safe for CI.",
    )
    run_p.add_argument(
        "--mock-provider", action="store_true",
        help="Use a FakeProvider that returns canned responses. Proves "
             "the harness plumbing works end-to-end without spending "
             "money. Tasks will mostly FAIL — that's expected.",
    )
    run_p.add_argument("--provider", default=None, help="Provider id (e.g. groq).")
    run_p.add_argument("--model", default=None, help="Model name override.")
    run_p.add_argument("--api-key", default=None, help="API key override.")
    run_p.add_argument("--api-base", default=None, help="API base URL override.")
    run_p.add_argument(
        "--max-iterations", type=int, default=8,
        help="Hard cap on agent loop iterations per task.",
    )
    run_p.add_argument(
        "--max-time", type=float, default=300.0,
        help="Hard wall-clock cap per task (seconds).",
    )
    run_p.add_argument(
        "--guardian", choices=["off", "dangerous_only", "all"], default="off",
        help="Guardian level to run with (default: off).",
    )
    run_p.add_argument(
        "--autonomy", choices=["always_ask", "new_files_only", "never_ask"],
        default="never_ask",
        help="Autonomy level (default: never_ask for benchmarks).",
    )
    run_p.add_argument(
        "--task", action="append", default=[],
        help="Only run these task ids. Repeatable.",
    )
    run_p.add_argument(
        "--section", action="append", default=[],
        help="Only run tasks in these sections. Repeatable.",
    )
    run_p.add_argument(
        "--out-dir", default=None,
        help="Where to write the scorecard JSON. Defaults to "
             "clew/benchmarks/results/.",
    )
    run_p.add_argument(
        "--tag", default="",
        help="Free-form tag appended to the scorecard filename.",
    )
    run_p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")

    # diff
    diff_p = sub.add_parser("diff", help="Diff two scorecards for regression tracking.")
    diff_p.add_argument("baseline", help="Path to baseline scorecard JSON.")
    diff_p.add_argument("new", help="Path to new scorecard JSON.")

    return p


def _cmd_list() -> int:
    tasks = load_all_tasks()
    if not tasks:
        print("No benchmark tasks found.", file=sys.stderr)
        return 1
    print(f"Found {len(tasks)} benchmark tasks:\n")
    by_section: dict = {}
    for t in tasks:
        by_section.setdefault(t.section.value, []).append(t)
    for section in sorted(by_section.keys()):
        print(f"  [{section}]")
        for t in by_section[section]:
            print(
                f"    {t.id:<45}  ({t.difficulty.value}, "
                f"~{t.expected_duration_s:.0f}s)  {t.description}"
            )
        print()
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    config = RunConfig(
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        api_base=args.api_base,
        max_iterations=args.max_iterations,
        max_time_s=args.max_time,
        dry_run=args.dry_run,
        mock_provider=args.mock_provider,
        guardian_level=args.guardian,
        autonomy=args.autonomy,
        task_filter=args.task or None,
        section_filter=args.section or None,
        out_dir=args.out_dir,
        tag=args.tag,
    )

    runner = BenchmarkRunner(config)

    if config.dry_run:
        print("Running in DRY-RUN mode (no LLM calls)...")
        runner.load_tasks()
        reports = runner.dry_run()
        ok = sum(1 for r in reports if r["ok"])
        bad = sum(1 for r in reports if not r["ok"])
        print(f"\nValidated {len(reports)} tasks: {ok} OK, {bad} BROKEN.")
        if bad:
            print("\nBroken tasks:")
            for r in reports:
                if not r["ok"]:
                    print(f"  - {r['task_id']}: {r['error']}")
            return 1
        return 0

    if config.mock_provider:
        print("Running with MOCK provider — tasks will mostly FAIL.")
        print("This proves the harness plumbing works; it does NOT")
        print("measure agent quality.\n")

    print(f"Running {len(runner._tasks) if runner._tasks else '?'} tasks "
          f"with provider={config.provider or 'default'} "
          f"guardian={config.guardian_level} autonomy={config.autonomy}")

    summary = runner.run()
    path = runner.write_scorecard(summary)
    print(f"\nScorecard written to: {path}")
    print(
        f"Total: {summary.total_tasks} tasks  |  "
        f"Passed: {summary.passed}  Failed: {summary.failed}  "
        f"Errored: {summary.errored}"
    )
    print(
        f"Cost: ${summary.total_cost_usd:.4f}  "
        f"Tokens: {summary.total_tokens_in} in / {summary.total_tokens_out} out  "
        f"Wall: {summary.total_wall_clock_s:.1f}s"
    )
    # Exit code: 0 if all passed, 1 if any failed/errored.
    if summary.failed + summary.errored > 0:
        return 1
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    import json as _json
    from pathlib import Path
    baseline = _json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    new = _json.loads(Path(args.new).read_text(encoding="utf-8"))
    diff = diff_scorecards(baseline, new)
    print(format_diff_report(diff))
    # Write the diff JSON next to the new scorecard.
    out = Path(args.new).parent / (Path(args.new).stem + ".diff.json")
    out.write_text(_json.dumps(diff, indent=2, default=str), encoding="utf-8")
    print(f"\nDiff JSON written to: {out}")
    # Exit code: 0 if no regressions, 1 if any regressions.
    return 1 if diff["regressions"] else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.command == "list":
        return _cmd_list()
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "diff":
        return _cmd_diff(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
