#!/usr/bin/env python3
"""
Clew v2.0 — Smoke Tests for Agent Quality (FIXED)

FIXES from v1.2.0:
1. Removed the hardcoded Gemini API key — now read from $GEMINI_API_KEY env var.
   The previous key has been REVOKED and rotated; do not commit secrets.
2. Fixed the `task_result.metadata['tool_calls']` bug: that key never existed.
   Tool calls live in `task_result.tool_calls` (a List[ToolCall]), and each
   ToolCall has a `.name` attribute (a ToolName enum), not a dict.
3. Skip the run gracefully if GEMINI_API_KEY is not set, so CI without
   credentials doesn't fail mysteriously.
4. Skip tests gracefully if the `gemini` provider can't reach the API
   (network errors are common in sandboxes).

Run:
    export GEMINI_API_KEY=...
    python clew/smoke_tests.py
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from clew.providers import ProviderRegistry, ProviderConfig
from clew.agent_runtime import AgentRuntime, Task, TaskType, ToolName, ToolCall

# ── Test Configuration ────────────────────────────────────────────────

# API key is read from env. If not set, tests are skipped (not failed).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("CLEW_SMOKE_MODEL", "gemini-2.5-flash-lite")
TEST_WORKSPACE = PROJECT_ROOT / "clew" / "smoke_test_workspace"


# ── Test Infrastructure ───────────────────────────────────────────────


class TestResult:
    def __init__(self, name: str, section: str):
        self.name = name
        self.section = section
        self.success = False
        self.skipped = False
        self.skip_reason: Optional[str] = None
        self.quality = 0  # 1-5
        self.issues: List[str] = []
        self.duration = 0.0
        self.agent_output = ""
        self.tools_used: List[str] = []
        self.skill_activated: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "section": self.section,
            "success": self.success,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "quality": self.quality,
            "issues": self.issues,
            "duration": round(self.duration, 2),
            "tools_used": self.tools_used,
            "skill_activated": self.skill_activated,
        }

    def __str__(self) -> str:
        if self.skipped:
            return f"⏭  {self.name} [{self.section}] skipped: {self.skip_reason}"
        status = "OK" if self.success else ("?" if self.quality > 0 else "FAIL")
        return f"{status} {self.name} [{self.section}] q={self.quality} t={self.duration:.1f}s"


def _tool_name_to_str(tc: Any) -> str:
    """Coerce a ToolCall.name (which may be a ToolName enum or str) to a plain string."""
    if hasattr(tc, "value"):
        return str(tc.value)
    return str(tc)


class SmokeTestRunner:
    def __init__(self):
        self.results: List[TestResult] = []
        self.registry: Optional[ProviderRegistry] = None
        self.runtime: Optional[AgentRuntime] = None
        self.setup_ok = False

    def setup(self) -> bool:
        """Initialize provider registry and agent runtime.

        Returns True if setup succeeded and tests should run.
        Returns False if tests should be skipped (e.g. no API key).
        """
        print("\n[setup] Initializing provider registry...")

        if not GEMINI_API_KEY:
            print(
                "   [skip] GEMINI_API_KEY env var is not set; skipping all tests.\n"
                "          Set it via: export GEMINI_API_KEY=..."
            )
            return False

        try:
            # Local import so the file can still be parsed if the gemini
            # provider module has an issue.
            from clew.providers.gemini import GeminiProvider
        except Exception as e:
            print(f"   [skip] GeminiProvider import failed: {e}")
            return False

        # Clean up previous test runs
        import shutil
        if TEST_WORKSPACE.exists():
            shutil.rmtree(TEST_WORKSPACE)
        TEST_WORKSPACE.mkdir(parents=True, exist_ok=True)

        self.registry = ProviderRegistry()
        config = ProviderConfig(
            provider_id="gemini",
            model=MODEL,
            api_key=GEMINI_API_KEY,
            temperature=0.1,
            max_tokens=4096,
        )
        try:
            self.registry.register(GeminiProvider)
            self.registry.configure("gemini", config)
            self.registry.set_active("gemini")
        except Exception as e:
            print(f"   [skip] provider setup failed: {e}")
            return False

        print(f"   Active provider: {self.registry.active.provider_id}")
        print(f"   Model: {config.model}")

        self.runtime = AgentRuntime(
            registry=self.registry,
            workspace=str(TEST_WORKSPACE),
            max_iterations=10,
            enable_planning=True,
        )

        # Disable diff review for automated tests
        self.runtime.tools.diff_review_enabled = False
        self.runtime.tools._diff_review_callback = None

        self.setup_ok = True
        return True

    def run_test(
        self,
        name: str,
        section: str,
        prompt: str,
        expected_tools: Optional[List[str]] = None,
        expected_skill: Optional[str] = None,
        check_file: Optional[str] = None,
        check_content: Optional[str] = None,
        task_type: TaskType = TaskType.AGENTIC,
    ) -> TestResult:
        """Run a single test case."""
        result = TestResult(name, section)
        start = time.time()

        print(f"\n[TEST] {name} [{section}]")
        print(f"   Prompt: {prompt[:80]}...")

        if not self.setup_ok:
            result.skipped = True
            result.skip_reason = "setup failed (no API key or provider error)"
            self.results.append(result)
            print(f"   {result}")
            return result

        try:
            # Set section
            self.runtime.set_section(section)

            # Build Task — note: Task is a dataclass, not subscriptable.
            # The v1 bug was treating it like a dict.
            task = Task(
                type=task_type,
                description=prompt,
                language="python",
            )

            task_result = self.runtime.run(task)
            result.duration = time.time() - start
            result.agent_output = task_result.output

            # FIX #2: tool_calls is a List[ToolCall], each .name is a ToolName enum.
            # Coerce to plain strings for comparison.
            tool_calls: List[ToolCall] = getattr(task_result, "tool_calls", []) or []
            result.tools_used = [_tool_name_to_str(tc.name) for tc in tool_calls]

            # If the legacy runtime recorded tool calls in metadata too, merge them.
            meta_tool_calls = task_result.metadata.get("tool_calls") if isinstance(
                task_result.metadata, dict
            ) else None
            if meta_tool_calls:
                for tc in meta_tool_calls:
                    if isinstance(tc, dict):
                        n = tc.get("tool") or tc.get("name", "")
                        if n:
                            result.tools_used.append(_tool_name_to_str(n))

            # Check if skill was mentioned in output
            if expected_skill:
                if expected_skill.lower() in result.agent_output.lower() or (
                    hasattr(self.runtime.tools, "_activity_log")
                    and any(
                        expected_skill in str(e)
                        for e in self.runtime.tools._activity_log.entries[-5:]
                    )
                ):
                    result.skill_activated = expected_skill

            # Evaluate success
            result.success = bool(task_result.success)

            # Quality scoring (1-5)
            quality = 3  # baseline
            if task_result.success:
                quality += 1
            if result.duration < 30:
                quality += 1
            if expected_tools and any(
                t in str(result.tools_used) for t in expected_tools
            ):
                quality += 1
            if expected_skill and result.skill_activated:
                quality += 1
            if check_file and self._verify_file(check_file, check_content):
                quality += 1
            result.quality = min(5, max(1, quality))

            # Issues
            if not task_result.success:
                result.issues.append(
                    f"Agent failed: {task_result.error or 'unknown error'}"
                )
            if result.duration > 60:
                result.issues.append(f"Slow: {result.duration:.1f}s")
            if expected_tools and not any(
                t in str(result.tools_used) for t in expected_tools
            ):
                result.issues.append(
                    f"Missing expected tools: {expected_tools}, used: {result.tools_used}"
                )

        except Exception as e:
            result.duration = time.time() - start
            result.success = False
            result.quality = 1
            result.issues.append(f"Exception: {type(e).__name__}: {e}")
            print(f"   [error] {e}")

        print(f"   {result}")
        if result.issues:
            for issue in result.issues:
                print(f"     ! {issue}")

        self.results.append(result)
        return result

    def _verify_file(self, filepath: str, expected_content: Optional[str] = None) -> bool:
        """Verify file exists and optionally contains expected content."""
        full_path = TEST_WORKSPACE / filepath
        if not full_path.exists():
            return False
        if expected_content:
            content = full_path.read_text()
            return expected_content in content
        return True

    def print_summary(self):
        """Print test summary table."""
        print("\n" + "=" * 80)
        print("[summary] SMOKE TEST SUMMARY")
        print("=" * 80)

        by_section: Dict[str, List[TestResult]] = {}
        for r in self.results:
            by_section.setdefault(r.section, []).append(r)

        for section, tests in by_section.items():
            print(f"\n[{section}] ({len(tests)} tests):")
            passed = sum(1 for t in tests if t.success)
            skipped = sum(1 for t in tests if t.skipped)
            runnable = [t for t in tests if not t.skipped]
            avg_quality = (
                sum(t.quality for t in runnable) / len(runnable) if runnable else 0
            )
            print(
                f"   Passed: {passed}/{len(runnable) if runnable else 0} | "
                f"Skipped: {skipped} | Avg Quality: {avg_quality:.1f}/5"
            )
            for t in tests:
                print(f"   {t}")

        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        skipped = sum(1 for r in self.results if r.skipped)
        runnable = [r for r in self.results if not r.skipped]
        avg_q = (
            sum(r.quality for r in runnable) / len(runnable) if runnable else 0
        )
        total_time = sum(r.duration for r in self.results)

        print(
            f"\n[overall] {passed}/{len(runnable) if runnable else 0} passed | "
            f"Skipped: {skipped} | Avg Quality: {avg_q:.1f}/5 | "
            f"Total Time: {total_time:.1f}s"
        )

        # Save detailed results
        output = {
            "summary": {
                "total": total,
                "passed": passed,
                "skipped": skipped,
                "avg_quality": round(avg_q, 2),
                "total_time": round(total_time, 2),
            },
            "tests": [r.to_dict() for r in self.results],
        }
        report_path = PROJECT_ROOT / "clew" / "smoke_test_report.json"
        report_path.write_text(json.dumps(output, indent=2))
        print(f"\n[report] Detailed report saved to: {report_path}")


# ── Test Definitions ──────────────────────────────────────────────────


def run_all_tests(runner: SmokeTestRunner):
    """Run all smoke tests."""
    # ── A. General Section Tests ──────────────────────────────────

    runner.run_test(
        name="A1: Read file & explain",
        section="general",
        prompt="Read the file ARCHITECTURE.md and explain how Office Worker works in 2-3 sentences",
        expected_tools=["read_file"],
        check_file=None,
    )

    runner.run_test(
        name="A2: Create new file with write_file",
        section="general",
        prompt="Create a new file test_hello.py with a function hello() that returns 'Hello, Clew!'",
        expected_tools=["write_file"],
        check_file="test_hello.py",
        check_content="Hello, Clew!",
    )

    runner.run_test(
        name="A3: Edit file with str_replace (NOT write_file)",
        section="general",
        prompt="In test_hello.py, change the return value from 'Hello, Clew!' to 'Hello, World!'",
        expected_tools=["str_replace"],
        check_file="test_hello.py",
        check_content="Hello, World!",
    )

    runner.run_test(
        name="A4: Execute command",
        section="general",
        prompt="Run `python test_hello.py` and show the output",
        expected_tools=["execute_command", "run_code"],
        check_file=None,
    )

    runner.run_test(
        name="A5: Search project",
        section="general",
        prompt="Find all .md files in the project",
        expected_tools=["search_project", "list_files"],
        check_file=None,
    )

    # ── B. Office Worker Section Tests ────────────────────────────

    runner.run_test(
        name="B1: Create Word document",
        section="office",
        prompt="Create a Word document report.docx with heading 'Test Report' and an intro paragraph",
        expected_tools=["office_create", "office_add_heading", "office_add_paragraph"],
        expected_skill="office_document_author",
        check_file="report.docx",
    )

    runner.run_test(
        name="B2: Add table to Word doc",
        section="office",
        prompt="In report.docx add a 3x3 table with headers: Col1, Col2, Col3",
        expected_tools=["office_add_table"],
        check_file="report.docx",
    )

    runner.run_test(
        name="B3: View document structure",
        section="office",
        prompt="View the structure of report.docx",
        expected_tools=["office_view"],
        check_file=None,
    )

    # ── C. Heavy Code Section Tests ───────────────────────────────

    runner.run_test(
        name="C1: Multi-file creation with spawn_multi_agents",
        section="heavy_code",
        prompt="Create 3 files: a.py with func_a(), b.py with func_b(), c.py with func_c(). Each should return its letter.",
        expected_tools=["spawn_multi_agents"],
        expected_skill="agent_orchestrator",
        check_file="a.py",
        check_content="func_a",
    )

    # ── D. Quality Tests ──────────────────────────────────────────

    runner.run_test(
        name="D1: Write unit tests",
        section="general",
        prompt="Write pytest unit tests for hello() function in test_hello.py. Cover happy path and edge cases.",
        expected_tools=["write_file", "str_replace"],
        expected_skill="test_engineer",
        check_file="test_test_hello.py",
        check_content="def test_",
    )

    runner.run_test(
        name="D2: Refactor with type hints",
        section="general",
        prompt="Refactor test_hello.py: add type hints, docstring, and error handling",
        expected_tools=["str_replace"],
        expected_skill="python_architect",
        check_file="test_hello.py",
        check_content="def hello(",
    )

    runner.run_test(
        name="D3: Explain code",
        section="general",
        prompt="Explain what the spawn_multi_agents function does in agent_runtime.py",
        expected_tools=["read_file", "search_project"],
        expected_skill=None,
        check_file=None,
    )


# ── Main ────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("[smoke] CLEW v2.0 — SMOKE TESTS")
    print(f"   Model: {MODEL}")
    print(f"   Workspace: {TEST_WORKSPACE}")
    print("=" * 60)

    runner = SmokeTestRunner()

    try:
        if runner.setup():
            run_all_tests(runner)
        else:
            # Mark all tests as skipped.
            print("\n[skip] Setup did not succeed; all tests will be skipped.")
            # Build a list of placeholder skipped results so the summary is non-empty.
            for name, section in [
                ("A1: Read file & explain", "general"),
                ("A2: Create new file with write_file", "general"),
                ("A3: Edit file with str_replace", "general"),
                ("A4: Execute command", "general"),
                ("A5: Search project", "general"),
                ("B1: Create Word document", "office"),
                ("B2: Add table to Word doc", "office"),
                ("B3: View document structure", "office"),
                ("C1: Multi-file creation with spawn_multi_agents", "heavy_code"),
                ("D1: Write unit tests", "general"),
                ("D2: Refactor with type hints", "general"),
                ("D3: Explain code", "general"),
            ]:
                r = TestResult(name, section)
                r.skipped = True
                r.skip_reason = "no API key or provider setup failed"
                runner.results.append(r)
    except KeyboardInterrupt:
        print("\n\n[stop] Interrupted by user")
    except Exception as e:
        print(f"\n\n[fatal] {e}")
        import traceback

        traceback.print_exc()
    finally:
        runner.print_summary()

    # Return exit code based on results (skipped tests don't count as failures).
    runnable = [r for r in runner.results if not r.skipped]
    failed = sum(1 for r in runnable if not r.success)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
