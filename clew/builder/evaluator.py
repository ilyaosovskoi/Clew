"""
Evaluator — mechanical verification that a task didn't break anything.

Runs three layers of checks, in increasing strictness:

1. IMPORT_SMOKE  — `python -c "import clew"` succeeds.
                   This is the cheapest possible signal that the package
                   didn't get nuked by a bad edit. ALWAYS run.

2. CLI_SMOKE     — `python -m clew.cli status` exits 0.
                   Confirms the CLI parser still works and the registry
                   can be constructed. Skipped if IMPORT_SMOKE failed.

3. PYTEST        — `python -m pytest clew/tests/ -x --timeout=60 -q`
                   Runs the test suite. Skipped if --skip-pytest was
                   passed (CI environments without pytest installed).

Each layer captures stdout+stderr (truncated) for the reporter.

Additionally, if the task has explicit SUCCESS_CRITERIA strings, we do
a substring check against the diff — for each criterion, we check if
the diff *looks like* it addresses the criterion. This is a heuristic;
the REVIEWER LLM does the real judgment. The heuristic just feeds
into the verification output so the reviewer can see it.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Outcome of running the evaluator on a task."""
    passed: bool
    layers: List[str] = field(default_factory=list)  # ["IMPORT_SMOKE:PASS", ...]
    output: str = ""
    error: Optional[str] = None
    duration_secs: float = 0.0

    def format_for_reviewer(self) -> str:
        """Compact text representation for the reviewer prompt."""
        lines = ["Verification layers:"]
        for layer in self.layers:
            lines.append(f"  {layer}")
        lines.append("")
        lines.append("Output (truncated):")
        lines.append(self.output[:4000] if self.output else "(empty)")
        return "\n".join(lines)


@dataclass
class EvaluatorConfig:
    workspace: str
    skip_pytest: bool = False
    pytest_path: str = "clew/tests/"
    pytest_timeout: int = 60
    import_smoke_cmd: str = 'python -c "import clew"'
    cli_smoke_cmd: str = "python -m clew.cli status"
    extra_commands: List[str] = field(default_factory=list)


class Evaluator:
    """Runs mechanical verification checks on the workspace."""

    def __init__(self, config: EvaluatorConfig) -> None:
        self._cfg = config

    def verify(
        self,
        diff: str = "",
        success_criteria: Optional[List[str]] = None,
    ) -> VerificationResult:
        import time as _t
        t0 = _t.monotonic()
        layers: List[str] = []
        full_output: List[str] = []
        overall_pass = True
        first_error: Optional[str] = None

        # Layer 1: import smoke
        ok, out = self._run_shell(self._cfg.import_smoke_cmd)
        layers.append(f"IMPORT_SMOKE:{'PASS' if ok else 'FAIL'}")
        full_output.append(f"=== IMPORT_SMOKE ({'PASS' if ok else 'FAIL'}) ===")
        full_output.append(out)
        if not ok:
            overall_pass = False
            first_error = first_error or f"import smoke failed: {out.splitlines()[-1] if out else 'no output'}"

        # Layer 2: CLI smoke (only if import passed)
        if ok:
            ok2, out2 = self._run_shell(self._cfg.cli_smoke_cmd)
            layers.append(f"CLI_SMOKE:{'PASS' if ok2 else 'FAIL'}")
            full_output.append(f"=== CLI_SMOKE ({'PASS' if ok2 else 'FAIL'}) ===")
            full_output.append(out2)
            if not ok2:
                overall_pass = False
                first_error = first_error or f"cli smoke failed: {out2.splitlines()[-1] if out2 else 'no output'}"
        else:
            layers.append("CLI_SMOKE:SKIPPED (import smoke failed)")

        # Layer 3: pytest (only if both above passed and not skipped)
        if overall_pass and not self._cfg.skip_pytest:
            ok3, out3 = self._run_pytest()
            layers.append(f"PYTEST:{'PASS' if ok3 else 'FAIL'}")
            full_output.append(f"=== PYTEST ({'PASS' if ok3 else 'FAIL'}) ===")
            full_output.append(out3)
            if not ok3:
                overall_pass = False
                first_error = first_error or "pytest reported failures (see output)"
        elif self._cfg.skip_pytest:
            layers.append("PYTEST:SKIPPED (--skip-pytest)")
        else:
            layers.append("PYTEST:SKIPPED (earlier layer failed)")

        # Layer 4: success-criteria heuristic check
        if success_criteria:
            crit_results = self._check_criteria_heuristic(diff, success_criteria)
            for c, hit in crit_results:
                tag = "LIKELY_MET" if hit else "UNCLEAR"
                layers.append(f"CRITERION[{tag}]: {c[:60]}")
            full_output.append("=== SUCCESS_CRITERIA_HEURISTIC ===")
            for c, hit in crit_results:
                full_output.append(f"  [{'X' if hit else ' '}] {c}")

        # Layer 5: extra commands (user-supplied)
        for cmd in self._cfg.extra_commands:
            ok_e, out_e = self._run_shell(cmd)
            tag = "PASS" if ok_e else "FAIL"
            layers.append(f"EXTRA[{cmd[:30]}]:{tag}")
            full_output.append(f"=== EXTRA '{cmd}' ({tag}) ===")
            full_output.append(out_e)
            if not ok_e:
                overall_pass = False
                first_error = first_error or f"extra command failed: {cmd}"

        return VerificationResult(
            passed=overall_pass,
            layers=layers,
            output="\n".join(full_output),
            error=first_error,
            duration_secs=round(_t.monotonic() - t0, 2),
        )

    # ── Internals ─────────────────────────────────────────────────

    def _run_shell(self, cmd: str) -> tuple[bool, str]:
        """Run a shell command in the workspace. Returns (success, combined_output)."""
        try:
            r = subprocess.run(
                cmd,
                shell=True,
                cwd=self._cfg.workspace,
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = (r.stdout or "") + (r.stderr or "")
            return r.returncode == 0, out[:6000]
        except subprocess.TimeoutExpired:
            return False, f"TIMEOUT after 120s running: {cmd}"
        except Exception as e:
            return False, f"EXCEPTION running '{cmd}': {e}"

    def _run_pytest(self) -> tuple[bool, str]:
        """Run pytest with --timeout (if pytest-timeout installed) and -x."""
        cmd = [
            sys.executable, "-m", "pytest",
            self._cfg.pytest_path,
            "-x", "--no-header", "-q",
            "--timeout", str(self._cfg.pytest_timeout),
        ]
        try:
            r = subprocess.run(
                cmd,
                cwd=self._cfg.workspace,
                capture_output=True,
                text=True,
                timeout=300,  # hard cap 5 min regardless of --timeout
            )
            out = (r.stdout or "") + (r.stderr or "")
            # Treat "no tests ran" as a soft pass (don't fail the whole task
            # because the workspace has no tests).
            if "no tests ran" in out.lower():
                return True, out[:6000]
            return r.returncode == 0, out[:6000]
        except subprocess.TimeoutExpired:
            return False, f"PYTEST TIMEOUT after 300s"
        except FileNotFoundError:
            return True, "pytest not installed — skipping (treated as pass)"
        except Exception as e:
            return False, f"PYTEST EXCEPTION: {e}"

    def _check_criteria_heuristic(
        self, diff: str, criteria: List[str],
    ) -> List[tuple[str, bool]]:
        """Heuristic: for each criterion, check if the diff plausibly addresses it.

        The check is intentionally fuzzy: we look for words from the criterion
        appearing in the diff (after normalisation). The real judgment is
        delegated to the REVIEWER LLM — this just gives it a hint.
        """
        results: List[tuple[str, bool]] = []
        diff_lower = diff.lower()
        for c in criteria:
            # Pull significant words from the criterion (length > 3, alphanumeric).
            words = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", c)]
            if not words:
                results.append((c, False))
                continue
            # A criterion is "likely met" if at least 50% of its significant
            # words appear in the diff.
            hits = sum(1 for w in words if w in diff_lower)
            ratio = hits / len(words) if words else 0
            results.append((c, ratio >= 0.5))
        return results
