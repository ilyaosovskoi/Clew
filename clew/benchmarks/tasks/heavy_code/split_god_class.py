"""Heavy_code task — split a god-class into 3 cohesive modules.

Category: heavy_code
Section: heavy_code
Difficulty: hard

Starting tree: ``god_class.py`` containing a ``UserManager`` that does
too many things (auth + storage + email). The agent must split it into
``auth.py``, ``user_store.py``, and ``email_service.py``, then update
the existing ``test_god_class.py`` (or create a new test file) to
import from the new locations and still pass.

Pass criteria:
1. ``auth.py``, ``user_store.py``, ``email_service.py`` all exist.
2. Each new module has at least one ``def`` or ``class`` in it.
3. The original behavior is preserved — ``test_split.py`` (provided
   by the harness) passes against the new modules.
4. The agent used subagents OR completed all the splits within one
   loop (informational).

This task multi-file refactor is exactly the heavy_code use case —
requires coordination across many files.
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


_GOD_CLASS = """\
\"\"\"UserManager — does too much.\"\"\"

class UserManager:
    def __init__(self):
        self._users = {}
        self._tokens = set()
        self._outbox = []

    def add_user(self, username, email):
        if username in self._users:
            return False
        self._users[username] = email
        return True

    def get_user(self, username):
        return self._users.get(username)

    def authenticate(self, username, token):
        if username not in self._users:
            return False
        if token not in self._tokens:
            return False
        return True

    def issue_token(self, username):
        if username not in self._users:
            return None
        token = f"token-{username}"
        self._tokens.add(token)
        return token

    def send_email(self, to_address, subject, body):
        self._outbox.append({"to": to_address, "subject": subject, "body": body})
        return True

    def outbox_count(self):
        return len(self._outbox)
"""


_SPLIT_TEST = """\
\"\"\"Tests the post-split API: each responsibility lives in its own module.\"\"\"
from auth import AuthService
from user_store import UserStore
from email_service import EmailService

def test_user_store():
    store = UserStore()
    assert store.add_user("alice", "alice@example.com") == True
    assert store.add_user("alice", "alice@example.com") == False  # dup
    assert store.get_user("alice") == "alice@example.com"
    assert store.get_user("bob") is None

def test_auth():
    store = UserStore()
    store.add_user("alice", "alice@example.com")
    auth = AuthService(store)
    token = auth.issue_token("alice")
    assert token is not None
    assert auth.authenticate("alice", token) == True
    assert auth.authenticate("alice", "wrong") == False
    assert auth.authenticate("bob", token) == False

def test_email():
    svc = EmailService()
    assert svc.send_email("alice@example.com", "hi", "hello") == True
    assert svc.outbox_count() == 1
"""


def setup(workspace: str) -> None:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / "god_class.py").write_text(_GOD_CLASS, encoding="utf-8")
    # The harness writes test_split.py — the agent doesn't need to
    # create it. The agent's job is to make the existing UserManager
    # API available via the three new modules so test_split.py passes.
    (root / "test_split.py").write_text(_SPLIT_TEST, encoding="utf-8")


def _run_pytest(workspace: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "test_split.py", "-v"],
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

    # 1. Three new modules exist.
    for name in ("auth.py", "user_store.py", "email_service.py"):
        f = root / name
        ok = f.is_file()
        # 2. Each module has at least one def or class.
        has_code = False
        if ok:
            text = f.read_text(encoding="utf-8", errors="replace")
            has_code = ("def " in text) or ("class " in text)
        criteria.append({
            "name": f"{name} exists with code",
            "passed": ok and has_code,
        })

    # 3. test_split.py passes.
    c3, test_output = _run_pytest(workspace)
    criteria.append({"name": "test_split.py passes", "passed": c3})

    # 4. (Informational) subagent usage.
    spawn_calls = [
        tc for tc in tool_calls
        if tc.get("name") in ("spawn_subagent", "spawn_multi_agents")
    ]
    c4 = len(spawn_calls) >= 1
    criteria.append({
        "name": "agent spawned subagent(s) (informational)",
        "passed": c4,
    })

    mandatory = [c["passed"] for c in criteria[:4]]
    passed = all(mandatory)
    return EvaluationReport(
        passed=passed,
        reason="all mandatory criteria met" if passed else "mandatory criteria failed",
        details=test_output if not c3 else "",
        checked_criteria=criteria,
    )


def build() -> TaskSpec:
    return TaskSpec(
        id="heavy_code_split_god_class",
        section=Section.HEAVY_CODE,
        difficulty=Difficulty.HARD,
        description=(
            "Split god_class.py (UserManager) into auth.py, user_store.py, "
            "email_service.py — test_split.py must pass against the new API."
        ),
        prompt=(
            "Refactor god_class.py: the UserManager class does too much. "
            "Split it into three cohesive modules: auth.py (AuthService), "
            "user_store.py (UserStore), email_service.py (EmailService). "
            "Each class should preserve the relevant methods from "
            "UserManager. The provided test_split.py MUST pass against "
            "the new modules."
        ),
        setup=setup,
        evaluate=evaluate,
        expected_duration_s=90.0,
        tags=["heavy_code", "refactor", "split", "verified_by_project_tests"],
        min_iterations=15,
        max_time_s=420.0,
    )
