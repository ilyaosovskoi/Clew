"""
Git workspace manager for the Clew Builder.

Each task runs on its own branch:  builder/task-<NN>-<slug>

Operations:
  * begin_task_branch(N, slug)  — checkout a fresh branch from current HEAD
  * commit_all(message)          — stage everything + commit
  * diff_since_branch_start()    — what changed on this branch
  * abort_task_branch()          — checkout the original branch and delete
                                   the task branch (used when the task
                                   fails AND we want to clean up — off by
                                   default; failed branches are kept for
                                   post-mortem)
  * files_changed_since_start()  — list of paths touched on the branch

Design notes:

* We shell out to `git` rather than use a Python git library — Clew
  already depends on subprocess for git_service.py and we don't want
  to add a new dependency.
* All commands run with cwd=workspace and capture stderr. We raise
  BuilderGitError with the captured stderr on failure so the loop
  can record it in the attempt's error field.
* We do NOT push to any remote. Local-only. The user reviews branches
  with `git log builder/task-NN-…` and merges manually.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class BuilderGitError(RuntimeError):
    """Raised when a git operation fails."""


@dataclass
class GitWorkspace:
    """Thin wrapper around `git` for the builder's per-task branching."""
    workspace: str
    original_branch: Optional[str] = None  # captured at begin_task_branch

    # ── Helpers ───────────────────────────────────────────────────

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as e:
            raise BuilderGitError(
                "`git` executable not found — Clew Builder requires git to be installed"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise BuilderGitError(f"git {' '.join(args)} timed out") from e

    def _check(self, result: subprocess.CompletedProcess, op: str) -> None:
        if result.returncode != 0:
            raise BuilderGitError(
                f"git {op} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    # ── Public API ───────────────────────────────────────────────

    def ensure_repo(self) -> None:
        """Verify the workspace is a git repo (init one if not)."""
        r = self._git("rev-parse", "--git-dir", check=False)
        if r.returncode != 0:
            logger.info("[builder-git] workspace is not a git repo — initialising")
            init = self._git("init", check=False)
            self._check(init, "init")
            self._git("add", "-A", check=False)
            self._git("commit", "-m", "Clew Builder: initial snapshot", check=False)
            # If there was nothing to commit (empty repo), that's fine.

    def current_branch(self) -> str:
        r = self._git("rev-parse", "--abbrev-ref", "HEAD")
        self._check(r, "rev-parse --abbrev-ref")
        return r.stdout.strip()

    def begin_task_branch(self, task_num: int, slug: str) -> str:
        """Capture current branch, then create + checkout a task branch."""
        self.original_branch = self.current_branch()
        # Sanitise slug for git branch name (no consecutive dots, no leading dot/dash).
        clean_slug = slug.replace("..", "-").lstrip(".-") or "task"
        branch = f"builder/task-{task_num:02d}-{clean_slug}"
        # Don't fail if the branch already exists (retry case) — just check it out.
        r = self._git("checkout", "-B", branch, check=False)
        if r.returncode != 0:
            # Fall back: try just checking it out if -B failed (e.g. uncommitted changes).
            r2 = self._git("checkout", branch, check=False)
            if r2.returncode != 0:
                raise BuilderGitError(
                    f"could not create/checkout branch {branch}: {r.stderr.strip()}"
                )
        logger.info("[builder-git] on branch %s (was %s)", branch, self.original_branch)
        return branch

    def commit_all(self, message: str) -> str:
        """Stage everything + commit. Returns the commit SHA."""
        self._git("add", "-A")
        # Check if there's anything to commit (git commit fails on empty).
        diff = self._git("diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            # No staged changes.
            logger.info("[builder-git] nothing to commit")
            return ""
        r = self._git("commit", "-m", message, check=False)
        if r.returncode != 0:
            raise BuilderGitError(f"git commit failed: {r.stderr.strip()}")
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        logger.info("[builder-git] committed %s", sha[:10])
        return sha

    def diff_since_branch_start(self, max_chars: int = 30_000) -> str:
        """Diff of all changes since the task branch was created."""
        if not self.original_branch:
            return ""
        r = self._git(
            "diff", self.original_branch + "...HEAD", "--stat", "-p",
            "--no-color", check=False,
        )
        if r.returncode != 0:
            return f"(diff failed: {r.stderr.strip()})"
        diff = r.stdout
        if len(diff) > max_chars:
            diff = diff[:max_chars] + f"\n... (truncated, {len(diff) - max_chars} more chars)"
        return diff

    def files_changed_since_start(self) -> List[str]:
        """List of file paths changed on the task branch."""
        if not self.original_branch:
            return []
        r = self._git(
            "diff", "--name-only", self.original_branch + "...HEAD",
            check=False,
        )
        if r.returncode != 0:
            return []
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    def restore_original_branch(self) -> None:
        """Checkout the branch we were on before begin_task_branch()."""
        if not self.original_branch:
            return
        r = self._git("checkout", self.original_branch, check=False)
        if r.returncode != 0:
            logger.warning(
                "[builder-git] could not restore original branch %s: %s",
                self.original_branch, r.stderr.strip(),
            )

    def delete_task_branch(self, branch: str) -> None:
        """Force-delete a task branch (used for cleanup)."""
        self._git("branch", "-D", branch, check=False)
