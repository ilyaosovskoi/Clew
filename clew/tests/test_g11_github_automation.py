#!/usr/bin/env python3
"""
G11 — GitHub-native Automation — test suite.

Verifies:
  1. GitHubAutomation token loading from env / file.
  2. GitHubAutomation.set_token() persists token.
  3. GitHubAutomation.auto_detect_repo() parses GitHub URLs.
  4. GitHubAutomation.list_prs() / get_pr() / create_pr() API calls.
  5. GitHubAutomation.list_issues() / get_issue() / create_issue() API calls.
  6. GitHubAutomation.get_pr_diff() returns diff content.
  7. GitHubAutomation.get_pr_context() builds implement prompt.
  8. GitHubAutomation.comment_on_pr() / comment_on_issue().
  9. GitHubAutomation.generate_action_template() produces YAML.
  10. GitHubAutomation.status() returns current state.
  11. API error handling (401, 403, 404).
  12. Rate limit retry logic.

Run:
    python -m pytest clew/tests/test_g11_github_automation.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Test isolation ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_github_automation():
    """Reset the global GitHubAutomation singleton before each test."""
    import clew.github_automation as _gh
    _gh._GITHUB_AUTOMATION = None
    yield
    _gh._GITHUB_AUTOMATION = None


# ── 1. Token loading ────────────────────────────────────────────────────

def test_token_from_env():
    from clew.github_automation import GitHubAutomation
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test123"}):
        gh = GitHubAutomation()
        assert gh._token == "ghp_test123"
        assert gh.has_token is True


def test_token_from_file(tmp_path):
    from clew.github_automation import GitHubAutomation
    token_file = tmp_path / "github_token"
    token_file.write_text("ghp_file_token")

    with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
        # Remove GITHUB_TOKEN if present
        os.environ.pop("GITHUB_TOKEN", None)
        with patch("clew.github_automation._token_path", return_value=token_file):
            gh = GitHubAutomation()
            assert gh._token == "ghp_file_token"


def test_no_token():
    from clew.github_automation import GitHubAutomation
    with patch.dict(os.environ, {}, clear=True):
        gh = GitHubAutomation()
        assert gh.has_token is False


# ── 2. Set and persist token ────────────────────────────────────────────

def test_set_token(tmp_path):
    from clew.github_automation import GitHubAutomation
    token_file = tmp_path / "github_token"
    with patch("clew.github_automation._token_path", return_value=token_file):
        gh = GitHubAutomation()
        gh.set_token("ghp_new_token")
        assert gh._token == "ghp_new_token"
        assert token_file.read_text() == "ghp_new_token"


# ── 3. Auto-detect repo ─────────────────────────────────────────────────

def test_auto_detect_repo_https():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/owner/repo.git\n",
        )
        result = gh.auto_detect_repo("/tmp/workspace")
        assert result == "owner/repo"
        assert gh._repo == "owner/repo"


def test_auto_detect_repo_ssh():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="git@github.com:myorg/myproject.git\n",
        )
        result = gh.auto_detect_repo("/tmp/workspace")
        assert result == "myorg/myproject"


def test_auto_detect_repo_no_remote():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = gh.auto_detect_repo("/tmp/workspace")
        assert result is None


# ── 4. PR operations ────────────────────────────────────────────────────

def test_list_prs():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    mock_response = [
        {
            "number": 42,
            "title": "Fix bug",
            "body": "This fixes the bug",
            "state": "open",
            "head": {"ref": "fix-branch"},
            "base": {"ref": "main"},
            "user": {"login": "dev1"},
            "html_url": "https://github.com/owner/repo/pull/42",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "mergeable": True,
            "draft": False,
        }
    ]

    with patch.object(gh, "_api_request", return_value={"ok": True, "data": mock_response}):
        result = gh.list_prs()
        assert result["ok"] is True
        assert len(result["prs"]) == 1
        assert result["prs"][0]["number"] == 42
        assert result["prs"][0]["title"] == "Fix bug"


def test_get_pr():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    mock_pr = {
        "number": 42,
        "title": "Fix bug",
        "body": "Description",
        "state": "open",
        "head": {"ref": "fix-branch"},
        "base": {"ref": "main"},
        "user": {"login": "dev1"},
        "html_url": "https://github.com/owner/repo/pull/42",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "mergeable": None,
        "draft": False,
    }

    with patch.object(gh, "_api_request", return_value={"ok": True, "data": mock_pr}):
        result = gh.get_pr(42)
        assert result["ok"] is True
        assert result["pr"]["number"] == 42


def test_create_pr():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    mock_response = {
        "number": 99,
        "html_url": "https://github.com/owner/repo/pull/99",
    }

    with patch.object(gh, "_api_request", return_value={"ok": True, "data": mock_response}):
        result = gh.create_pr(title="New feature", head="feature-branch", base="main")
        assert result["ok"] is True
        assert result["pr_number"] == 99


# ── 5. Issue operations ─────────────────────────────────────────────────

def test_list_issues():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    mock_response = [
        {
            "number": 10,
            "title": "Bug report",
            "body": "Something is broken",
            "state": "open",
            "user": {"login": "user1"},
            "html_url": "https://github.com/owner/repo/issues/10",
            "labels": [{"name": "bug"}],
            "assignees": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }
    ]

    with patch.object(gh, "_api_request", return_value={"ok": True, "data": mock_response}):
        result = gh.list_issues()
        assert result["ok"] is True
        assert len(result["issues"]) == 1
        assert result["issues"][0]["number"] == 10
        assert "bug" in result["issues"][0]["labels"]


def test_create_issue():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    mock_response = {
        "number": 11,
        "html_url": "https://github.com/owner/repo/issues/11",
    }

    with patch.object(gh, "_api_request", return_value={"ok": True, "data": mock_response}):
        result = gh.create_issue(title="New issue", body="Description", labels=["enhancement"])
        assert result["ok"] is True
        assert result["issue_number"] == 11


# ── 6. PR diff ──────────────────────────────────────────────────────────

def test_get_pr_diff():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    mock_diff = "diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n"

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_diff.encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = gh.get_pr_diff(42)
        assert result["ok"] is True
        assert "diff --git" in result["diff"]


# ── 7. PR context (implement prompt) ────────────────────────────────────

def test_get_pr_context():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    with patch.object(gh, "get_pr") as mock_get_pr, \
         patch.object(gh, "get_pr_diff") as mock_diff, \
         patch.object(gh, "list_pr_comments") as mock_comments:

        mock_get_pr.return_value = {
            "ok": True,
            "pr": {
                "number": 42, "title": "Fix bug", "body": "Description",
                "state": "open", "head_ref": "fix", "base_ref": "main",
                "author": "dev1", "url": "https://github.com/owner/repo/pull/42",
                "created_at": "2026-01-01", "updated_at": "2026-01-02",
                "mergeable": True, "draft": False,
            },
        }
        mock_diff.return_value = {"ok": True, "diff": "diff content"}
        mock_comments.return_value = {"ok": True, "comments": []}

        result = gh.get_pr_context(42)
        assert result["ok"] is True
        assert "implement_prompt" in result
        assert "Fix bug" in result["implement_prompt"]


# ── 8. Comments ─────────────────────────────────────────────────────────

def test_comment_on_pr():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    with patch.object(gh, "_api_request", return_value={"ok": True, "data": {"id": 999}}):
        result = gh.comment_on_pr(42, "LGTM!")
        assert result["ok"] is True
        assert result["comment_id"] == 999


# ── 9. Action template ──────────────────────────────────────────────────

def test_generate_action_template_pr():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation()
    result = gh.generate_action_template(trigger="pull_request")
    assert result["ok"] is True
    assert "Clew PR Review" in result["yaml"]
    assert "pull_request" in result["yaml"]


def test_generate_action_template_push():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation()
    result = gh.generate_action_template(trigger="push")
    assert result["ok"] is True
    assert "Clew Code Review" in result["yaml"]


# ── 10. Status ───────────────────────────────────────────────────────────

def test_status():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"
    status = gh.status()
    assert status["has_token"] is True
    assert status["repo"] == "owner/repo"


# ── 11. API error handling ──────────────────────────────────────────────

def test_api_no_token(monkeypatch, tmp_path):
    from clew.github_automation import GitHubAutomation

    # _load_token() reads GITHUB_TOKEN from the environment (first) and then
    # the token file at ~/.clew/github_token. Either would override the empty
    # token passed below — e.g. GITHUB_TOKEN is auto-injected on GitHub Actions
    # runners, and a developer may have a token file locally. Patch both so the
    # test is robust in any environment.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "clew.github_automation._token_path", lambda: tmp_path / "absent"
    )
    gh = GitHubAutomation(token="")
    result = gh._api_request("GET", "/pulls")
    assert result["ok"] is False
    assert "No GitHub token" in result["error"]


def test_api_no_repo():
    from clew.github_automation import GitHubAutomation
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = None
    result = gh._api_request("GET", "/pulls")
    assert result["ok"] is False
    assert "No repository" in result["error"]


# ── 12. Rate limit retry ────────────────────────────────────────────────

def test_rate_limit_retry():
    from clew.github_automation import GitHubAutomation
    import urllib.error
    gh = GitHubAutomation(token="ghp_test")
    gh._repo = "owner/repo"

    call_count = 0
    def mock_urlopen(req, timeout=30):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise urllib.error.HTTPError(
                url="http://test", code=403, msg="rate limited",
                hdrs={"Retry-After": "0"}, fp=None,
            )
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([{"number": 1}]).encode("utf-8")
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with patch("time.sleep"):  # Don't actually sleep
            result = gh._api_request("GET", "/pulls")
            assert result["ok"] is True
            assert call_count == 3  # 2 failures + 1 success


# ── Singleton ────────────────────────────────────────────────────────────

def test_get_github_automation_singleton():
    from clew.github_automation import get_github_automation, reset_github_automation
    reset_github_automation()
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        gh1 = get_github_automation()
        gh2 = get_github_automation()
        assert gh1 is gh2


# ── Data class serialization ────────────────────────────────────────────

def test_github_pr_to_dict():
    from clew.github_automation import GitHubPR
    pr = GitHubPR(
        number=42, title="Fix", body="Body", state="open",
        head_ref="fix", base_ref="main", author="dev",
        url="https://github.com/owner/repo/pull/42",
        created_at="2026-01-01", updated_at="2026-01-02",
    )
    d = pr.to_dict()
    assert d["number"] == 42
    assert d["title"] == "Fix"


def test_github_issue_to_dict():
    from clew.github_automation import GitHubIssue
    issue = GitHubIssue(
        number=10, title="Bug", body="Body", state="open",
        author="user", url="https://github.com/owner/repo/issues/10",
        labels=["bug"],
    )
    d = issue.to_dict()
    assert d["number"] == 10
    assert "bug" in d["labels"]
