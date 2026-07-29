#!/usr/bin/env python3
"""
G11 — GitHub-native automation.

Extends the existing git_service.py with PR/issue operations and provides
a GitHub Action template for running clew-cli on PR events.

The existing GitService (clew/git_service.py) only has local git operations:
  status, diff, stage, commit, log, branch.

This module adds:
  - GitHub API operations: create/list/get PRs, create/list issues, comment on PRs.
  - GitHub Action template generation.
  - Slash command integration: /github pr <num> implement, /github issue create, etc.

Uses the GitHub REST API v3 via urllib (no external dependency).
Authentication: GitHub token from ~/.clew/github_token or GITHUB_TOKEN env var.

Design:
  - GitHubAutomation is a thin client over the GitHub REST API.
  - All operations are synchronous (called from the agent background thread).
  - Rate limiting: respects GitHub's secondary rate limits (403 → backoff + retry).
  - Error handling: returns {ok: False, error: str} on failure.
  - No telemetry — all API calls are between the user's machine and GitHub.

Integration:
  - ClewBridge (TUI + GUI) exposes github_* methods.
  - /github slash command with subcommands.
  - Agent can call /github pr <num> implement to get PR context as a prompt.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_PER_PAGE = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds


def _clew_home() -> Path:
    p = Path.home() / ".clew"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _token_path() -> Path:
    return _clew_home() / "github_token"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class GitHubPR:
    """A pull request."""
    number: int
    title: str
    body: str
    state: str
    head_ref: str
    base_ref: str
    author: str
    url: str
    created_at: str
    updated_at: str
    mergeable: Optional[bool] = None
    draft: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "author": self.author,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "mergeable": self.mergeable,
            "draft": self.draft,
        }


@dataclass
class GitHubIssue:
    """An issue."""
    number: int
    title: str
    body: str
    state: str
    author: str
    url: str
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "author": self.author,
            "url": self.url,
            "labels": self.labels,
            "assignees": self.assignees,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class GitHubComment:
    """A comment on a PR or issue."""
    id: int
    author: str
    body: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at,
        }


# ── GitHubAutomation ────────────────────────────────────────────────────

class GitHubAutomation:
    """Thin client over the GitHub REST API for PR/issue automation.

    Uses urllib (no external dependency).  Authentication via GitHub token
    stored in ~/.clew/github_token or the GITHUB_TOKEN environment variable.
    """

    def __init__(self, token: Optional[str] = None):
        self._token = token or self._load_token()
        self._repo: Optional[str] = None  # "owner/repo"

    def _load_token(self) -> str:
        """Load GitHub token from file or environment."""
        # 1. Environment variable
        env_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if env_token:
            return env_token
        # 2. Token file
        tp = _token_path()
        if tp.exists():
            try:
                return tp.read_text().strip()
            except Exception:
                pass
        return ""

    def set_token(self, token: str) -> None:
        """Set and persist the GitHub token."""
        self._token = token.strip()
        try:
            _token_path().write_text(self._token)
        except Exception as e:
            logger.warning("[github] failed to save token: %s", e)

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def set_repo(self, owner: str, repo: str) -> None:
        """Set the repository (owner/repo)."""
        self._repo = f"{owner}/{repo}"

    def auto_detect_repo(self, workspace: Optional[str] = None) -> Optional[str]:
        """Auto-detect the GitHub repo from git remote origin URL.

        Returns "owner/repo" or None.
        """
        try:
            cwd = workspace or os.getcwd()
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=cwd, capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return None
            url = result.stdout.strip()
            # Parse: https://github.com/owner/repo.git  or  git@github.com:owner/repo.git
            m = re.match(r'(?:https?://github\.com/|git@github\.com:)([^/]+/[^/\s]+?)(?:\.git)?$', url)
            if m:
                self._repo = m.group(1)
                return self._repo
        except Exception:
            pass
        return None

    # ── Low-level API ───────────────────────────────────────────────

    def _api_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make a GitHub API request with retry logic.

        Returns the parsed JSON response or {ok: False, error: str}.
        """
        if not self._token:
            return {"ok": False, "error": "No GitHub token configured. Set GITHUB_TOKEN or run /github auth <token>"}
        # Repo-scoped endpoints require a repository. Global endpoints like /user don't.
        repo_scoped = path.startswith("/repos/") or path.startswith("/repos") or path.startswith("/pulls") or path.startswith("/issues") or path.startswith("/commits") or path.startswith("/contents") or path.startswith("/actions") or path.startswith("/hooks") or path.startswith("/branches") or path.startswith("/checks") or path.startswith("/deployments") or path.startswith("/releases") or path.startswith("/merge") or path.startswith("/comments")
        if not self._repo and repo_scoped:
            return {"ok": False, "error": "No repository configured. Run /github repo <owner/repo>"}

        url = f"{GITHUB_API_BASE}/repos/{self._repo}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "clew-github-automation/2.0",
        }
        body = json.dumps(data).encode("utf-8") if data else None

        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    response_data = json.loads(resp.read().decode("utf-8"))
                    return {"ok": True, "data": response_data, "status": resp.status}
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    # Rate limit — back off
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else RETRY_BACKOFF_BASE ** attempt
                    logger.warning("[github] rate limited, retrying in %.1fs", wait)
                    time.sleep(wait)
                    continue
                if e.code == 404:
                    return {"ok": False, "error": f"Not found: {path}"}
                if e.code == 401:
                    return {"ok": False, "error": "Authentication failed — check your GitHub token"}
                body_text = e.read().decode("utf-8", errors="replace")[:500]
                return {"ok": False, "error": f"HTTP {e.code}: {body_text}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return {"ok": False, "error": "Max retries exceeded (rate limited)"}

    # ── Pull Requests ───────────────────────────────────────────────

    def list_prs(
        self, state: str = "open", limit: int = 10,
    ) -> Dict[str, Any]:
        """List pull requests.

        Returns {ok, prs: [GitHubPR.to_dict()]}.
        """
        result = self._api_request(
            "GET", "/pulls",
            params={"state": state, "per_page": str(min(limit, DEFAULT_PER_PAGE))},
        )
        if not result.get("ok"):
            return result
        prs = []
        for item in result["data"]:
            prs.append(GitHubPR(
                number=item["number"],
                title=item["title"],
                body=item.get("body", "") or "",
                state=item["state"],
                head_ref=item["head"]["ref"],
                base_ref=item["base"]["ref"],
                author=item["user"]["login"],
                url=item["html_url"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                mergeable=item.get("mergeable"),
                draft=item.get("draft", False),
            ).to_dict())
        return {"ok": True, "prs": prs}

    def get_pr(self, number: int) -> Dict[str, Any]:
        """Get a single pull request."""
        result = self._api_request("GET", f"/pulls/{number}")
        if not result.get("ok"):
            return result
        item = result["data"]
        pr = GitHubPR(
            number=item["number"],
            title=item["title"],
            body=item.get("body", "") or "",
            state=item["state"],
            head_ref=item["head"]["ref"],
            base_ref=item["base"]["ref"],
            author=item["user"]["login"],
            url=item["html_url"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            mergeable=item.get("mergeable"),
            draft=item.get("draft", False),
        )
        return {"ok": True, "pr": pr.to_dict()}

    def create_pr(
        self,
        title: str,
        body: str = "",
        head: str = "",
        base: str = "main",
        draft: bool = False,
    ) -> Dict[str, Any]:
        """Create a pull request."""
        data = {"title": title, "body": body, "head": head, "base": base, "draft": draft}
        result = self._api_request("POST", "/pulls", data=data)
        if not result.get("ok"):
            return result
        item = result["data"]
        return {"ok": True, "pr_number": item["number"], "url": item["html_url"]}

    def get_pr_diff(self, number: int) -> Dict[str, Any]:
        """Get the diff of a pull request."""
        if not self._token or not self._repo:
            return {"ok": False, "error": "No token or repo configured"}
        url = f"{GITHUB_API_BASE}/repos/{self._repo}/pulls/{number}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3.diff",
            "User-Agent": "clew-github-automation/2.0",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                diff_text = resp.read().decode("utf-8", errors="replace")
                return {"ok": True, "diff": diff_text}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_pr_context(self, number: int) -> Dict[str, Any]:
        """Get full context for implementing a PR: title, body, diff, comments.

        This is the primary method for /github pr <num> implement.
        """
        pr_result = self.get_pr(number)
        if not pr_result.get("ok"):
            return pr_result

        diff_result = self.get_pr_diff(number)
        comments_result = self.list_pr_comments(number)

        context = {
            "ok": True,
            "pr": pr_result["pr"],
            "diff": diff_result.get("diff", "") if diff_result.get("ok") else "",
            "comments": comments_result.get("comments", []) if comments_result.get("ok") else [],
        }

        # Build a prompt for the agent
        pr = pr_result["pr"]
        prompt_parts = [
            f"## Pull Request #{pr['number']}: {pr['title']}",
            f"**Author:** {pr['author']}",
            f"**Branch:** {pr['head_ref']} → {pr['base_ref']}",
            f"**State:** {pr['state']}",
            f"**URL:** {pr['url']}",
            "",
            f"### Description\n{pr['body'] or '(no description)'}",
        ]
        if context["diff"]:
            prompt_parts.append(f"\n### Diff\n```diff\n{context['diff'][:8000]}\n```")
        if context["comments"]:
            prompt_parts.append("\n### Comments")
            for c in context["comments"][:20]:
                prompt_parts.append(f"- **@{c['author']}**: {c['body'][:500]}")

        context["implement_prompt"] = "\n".join(prompt_parts)
        return context

    def list_pr_comments(self, number: int) -> Dict[str, Any]:
        """List review comments on a PR."""
        result = self._api_request("GET", f"/pulls/{number}/comments")
        if not result.get("ok"):
            return result
        comments = []
        for item in result["data"]:
            comments.append(GitHubComment(
                id=item["id"],
                author=item["user"]["login"],
                body=item.get("body", "") or "",
                created_at=item["created_at"],
            ).to_dict())
        return {"ok": True, "comments": comments}

    def comment_on_pr(self, number: int, body: str) -> Dict[str, Any]:
        """Add a comment to a PR (issue comment)."""
        result = self._api_request("POST", f"/issues/{number}/comments", data={"body": body})
        if not result.get("ok"):
            return result
        return {"ok": True, "comment_id": result["data"].get("id")}

    # ── Issues ──────────────────────────────────────────────────────

    def list_issues(
        self, state: str = "open", limit: int = 10, labels: str = "",
    ) -> Dict[str, Any]:
        """List issues."""
        params: Dict[str, str] = {"state": state, "per_page": str(min(limit, DEFAULT_PER_PAGE))}
        if labels:
            params["labels"] = labels
        result = self._api_request("GET", "/issues", params=params)
        if not result.get("ok"):
            return result
        issues = []
        for item in result["data"]:
            # Skip PRs (GitHub returns PRs in the issues endpoint)
            if "pull_request" in item:
                continue
            issues.append(GitHubIssue(
                number=item["number"],
                title=item["title"],
                body=item.get("body", "") or "",
                state=item["state"],
                author=item["user"]["login"],
                url=item["html_url"],
                labels=[l["name"] for l in item.get("labels", [])],
                assignees=[a["login"] for a in item.get("assignees", [])],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
            ).to_dict())
        return {"ok": True, "issues": issues}

    def get_issue(self, number: int) -> Dict[str, Any]:
        """Get a single issue."""
        result = self._api_request("GET", f"/issues/{number}")
        if not result.get("ok"):
            return result
        item = result["data"]
        issue = GitHubIssue(
            number=item["number"],
            title=item["title"],
            body=item.get("body", "") or "",
            state=item["state"],
            author=item["user"]["login"],
            url=item["html_url"],
            labels=[l["name"] for l in item.get("labels", [])],
            assignees=[a["login"] for a in item.get("assignees", [])],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
        )
        return {"ok": True, "issue": issue.to_dict()}

    def create_issue(
        self,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create an issue."""
        data: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            data["labels"] = labels
        if assignees:
            data["assignees"] = assignees
        result = self._api_request("POST", "/issues", data=data)
        if not result.get("ok"):
            return result
        item = result["data"]
        return {"ok": True, "issue_number": item["number"], "url": item["html_url"]}

    def comment_on_issue(self, number: int, body: str) -> Dict[str, Any]:
        """Add a comment to an issue."""
        return self.comment_on_pr(number, body)  # Same API endpoint

    # ── GitHub Action template ──────────────────────────────────────

    def generate_action_template(
        self,
        trigger: str = "pull_request",
        clew_command: str = "clew-cli -p",
        run_on: str = "ubuntu-latest",
    ) -> Dict[str, Any]:
        """Generate a GitHub Action workflow YAML for running Clew on PRs.

        Returns {ok, yaml: str} with the workflow file content.
        """
        if trigger == "pull_request":
            yaml_content = f"""name: Clew PR Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  clew-review:
    runs-on: {run_on}
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - name: Install Clew
        run: pip install clew
      - name: Run Clew PR Review
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: |
          {clew_command} "Review the changes in this PR and provide feedback"
"""
        elif trigger == "push":
            yaml_content = f"""name: Clew Code Review
on:
  push:
    branches: [main]

jobs:
  clew-review:
    runs-on: {run_on}
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: Install Clew
        run: pip install clew
      - name: Run Clew Code Review
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: |
          {clew_command} "Review the latest changes for issues"
"""
        else:
            yaml_content = f"""name: Clew Automation
on: workflow_dispatch

jobs:
  clew-task:
    runs-on: {run_on}
    steps:
      - uses: actions/checkout@v4
      - name: Install Clew
        run: pip install clew
      - name: Run Clew
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: |
          {clew_command} "Perform the requested task"
"""
        return {"ok": True, "yaml": yaml_content}

    # ── Status ──────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return the current GitHub automation status."""
        return {
            "has_token": self.has_token,
            "repo": self._repo,
            "api_base": GITHUB_API_BASE,
        }


# ── Process-wide singleton ──────────────────────────────────────────────

_GITHUB_AUTOMATION: Optional[GitHubAutomation] = None


def get_github_automation() -> GitHubAutomation:
    """Return the process-wide GitHubAutomation singleton."""
    global _GITHUB_AUTOMATION
    if _GITHUB_AUTOMATION is None:
        _GITHUB_AUTOMATION = GitHubAutomation()
        # Try to auto-detect repo
        _GITHUB_AUTOMATION.auto_detect_repo()
    return _GITHUB_AUTOMATION


def reset_github_automation() -> None:
    """Reset the singleton (for testing)."""
    global _GITHUB_AUTOMATION
    _GITHUB_AUTOMATION = None
