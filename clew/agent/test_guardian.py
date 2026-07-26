#!/usr/bin/env python3
"""Unit tests for the guardian module.

Covers:
- GuardianConfig defaults (Issue #3 baseline)
- Rule-based risk scoring for shell, write, delete, git (Issue #3)
- LLM verdict parsing (Issue #3)
- LLM review path with mocked provider (Issue #3)
- Subagent reviewer path (Issue #4)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clew.agent.guardian import (
    GuardianConfig,
    GuardianVerdict,
    RiskAssessment,
    _looks_like_rate_limit,
    _normalise_command,
    _parse_verdict,
    assess_risk,
    build_recent_context,
    review_with_llm,
    review_with_subagent,
)


# ── Config ────────────────────────────────────────────────────────────────


def test_guardian_config_defaults():
    """Test GuardianConfig default values."""
    config = GuardianConfig()
    assert config.level == "off"
    assert config.provider_id == "auto"
    assert config.model == "auto"
    assert config.use_subagent is False


def test_guardian_config_subagent_flag():
    """Issue #4: use_subagent flag must be settable."""
    config = GuardianConfig(level="all", use_subagent=True)
    assert config.use_subagent is True


# ── Risk Scoring ──────────────────────────────────────────────────────────


def test_assess_risk_safe_tool():
    """read_file is not in any dangerous category — should be low risk."""
    risk = assess_risk("read_file", {"path": "foo.txt"}, "/workspace")
    assert risk.level == "low"
    assert risk.reasons == []


def test_assess_risk_unknown_tool():
    """Unknown tools default to low risk (fail-open)."""
    risk = assess_risk("some_new_tool", {"x": 1}, "/workspace")
    assert risk.level == "low"
    assert risk.reasons == []


def test_assess_risk_write_file():
    """write_file inside workspace is medium risk."""
    risk = assess_risk(
        "write_file", {"path": "foo.txt", "content": "hello"}, "/workspace"
    )
    assert risk.level == "medium"
    assert any("write_file" in r.lower() for r in risk.reasons)


def test_assess_risk_write_file_critical_path():
    """write_file to /etc is high risk because /etc is in CRITICAL_PATHS."""
    risk = assess_risk(
        "write_file", {"path": "/etc/passwd", "content": "x"}, "/workspace"
    )
    assert risk.level == "high"
    assert any("critical path" in r.lower() for r in risk.reasons)


def test_assess_risk_write_file_critical_filename():
    """write_file to .env is high risk regardless of directory."""
    risk = assess_risk(
        "write_file", {"path": "config/.env", "content": "KEY=val"}, "/workspace"
    )
    assert risk.level == "high"
    assert any("critical file" in r.lower() for r in risk.reasons)


def test_assess_risk_write_file_outside_workspace():
    """write_file outside workspace is high risk."""
    risk = assess_risk(
        "write_file", {"path": "/tmp/../../etc/foo", "content": "x"}, "/workspace"
    )
    assert risk.level == "high"


def test_assess_risk_str_replace():
    """str_replace counts as a write operation."""
    risk = assess_risk(
        "str_replace",
        {"path": "foo.txt", "old_str": "a", "new_str": "b"},
        "/workspace",
    )
    assert risk.level == "medium"


def test_assess_risk_execute_command_safe():
    """ls -la is medium risk (any shell exec is at least medium)."""
    risk = assess_risk("execute_command", {"command": "ls -la"}, "/workspace")
    assert risk.level == "medium"
    assert any("execute_command" in r.lower() for r in risk.reasons)


def test_assess_risk_execute_command_dangerous_pattern():
    """rm -rf matches the dangerous_pattern rule."""
    risk = assess_risk("execute_command", {"command": "rm -rf /"}, "/workspace")
    assert risk.level == "high"
    assert any("dangerous pattern" in r.lower() for r in risk.reasons)
    assert any("rm -rf" in r for r in risk.reasons)


def test_assess_risk_execute_command_sudo():
    """sudo is flagged as a dangerous pattern."""
    risk = assess_risk(
        "execute_command", {"command": "sudo apt-get install foo"}, "/workspace"
    )
    assert risk.level == "high"
    assert any("sudo" in r for r in risk.reasons)


def test_assess_risk_execute_command_list_args():
    """command passed as a list must be normalised before pattern matching."""
    risk = assess_risk(
        "execute_command", {"command": ["rm", "-rf", "/tmp/x"]}, "/workspace"
    )
    assert risk.level == "high"
    assert any("rm -rf" in r for r in risk.reasons)


def test_assess_risk_execute_command_args_field():
    """Some tools pass the command under `args` instead of `command`."""
    risk = assess_risk("execute_command", {"args": ["sudo", "ls"]}, "/workspace")
    assert risk.level == "high"


def test_assess_risk_git_push_force():
    """git_commit with args containing push --force is high risk."""
    risk = assess_risk(
        "git_commit", {"args": ["push", "--force", "origin", "main"]}, "/workspace"
    )
    assert risk.level == "high"
    assert any("git push --force" in r.lower() for r in risk.reasons)


def test_assess_risk_git_push_force_via_subcommand():
    """git with subcommand field also triggers the rule."""
    risk = assess_risk(
        "git", {"subcommand": "push --force origin main"}, "/workspace"
    )
    assert risk.level == "high"
    assert any("git push --force" in r.lower() for r in risk.reasons)


def test_assess_risk_git_reset_hard():
    """git reset --hard is flagged as high risk."""
    risk = assess_risk("git", {"subcommand": "reset --hard HEAD~3"}, "/workspace")
    assert risk.level == "high"
    assert any("reset --hard" in r for r in risk.reasons)


def test_assess_risk_git_safe():
    """git status is medium risk (default for any git op)."""
    risk = assess_risk("git", {"subcommand": "status"}, "/workspace")
    assert risk.level == "medium"


def test_assess_risk_delete_file():
    """delete_file is medium risk by default."""
    risk = assess_risk("delete_file", {"path": "foo.txt"}, "/workspace")
    assert risk.level == "medium"


def test_assess_risk_delete_critical_file():
    """delete_file targeting .env is high risk."""
    risk = assess_risk("delete_file", {"path": ".env"}, "/workspace")
    assert risk.level == "high"


# ── Helpers ───────────────────────────────────────────────────────────────


def test_normalise_command_string():
    assert _normalise_command("ls -la") == "ls -la"


def test_normalise_command_list():
    assert _normalise_command(["rm", "-rf", "/"]) == "rm -rf /"


def test_normalise_command_none():
    assert _normalise_command(None) == ""


# ── Recent Context ────────────────────────────────────────────────────────


def test_build_recent_content():
    """build_recent_context truncates to max_messages and includes summary."""

    class MockMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class MockMemory:
        def __init__(self):
            self.compaction_summary = "Summary of earlier conversation"
            self.messages = [
                MockMessage("user", "Hello"),
                MockMessage("assistant", "Hi there!"),
                MockMessage("user", "How are you?"),
                MockMessage("assistant", "I'm good, thanks!"),
                MockMessage("user", "Bye!"),
            ]

    mem = MockMemory()
    ctx = build_recent_context(mem, max_messages=2, max_chars=100)
    assert "[PARENT CONTEXT SUMMARY]" in ctx
    assert "Summary of earlier conversation" in ctx
    assert "[ASSISTANT]" in ctx
    assert "I'm good, thanks!" in ctx
    assert "[USER]" in ctx
    assert "Bye!" in ctx
    # The oldest message (Hello) should not be present because max_messages=2
    assert "Hello" not in ctx
    # The second oldest message (Hi there!) should not be present
    assert "Hi there!" not in ctx
    # The third message (How are you?) should not be present
    assert "How are you?" not in ctx


def test_build_recent_context_truncates_long_content():
    """Long content is truncated to max_chars with a marker."""

    class MockMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class MockMemory:
        def __init__(self):
            self.compaction_summary = ""
            self.messages = [
                MockMessage("user", "x" * 500),
            ]

    mem = MockMemory()
    ctx = build_recent_context(mem, max_messages=1, max_chars=50)
    assert "total chars" in ctx
    assert "x" * 500 not in ctx


# ── Verdict Parsing ───────────────────────────────────────────────────────


def test_parse_verdict_valid_approve():
    raw = '{"verdict": "APPROVE", "rationale": "Looks good", "suggested_args": null}'
    verdict = _parse_verdict(raw)
    assert verdict is not None
    assert verdict.verdict == "APPROVE"
    assert verdict.rationale == "Looks good"
    assert verdict.suggested_args is None


def test_parse_verdict_valid_modify():
    raw = '{"verdict": "MODIFY", "rationale": "Too risky", "suggested_args": {"path": "/tmp/safe.txt"}}'
    verdict = _parse_verdict(raw)
    assert verdict is not None
    assert verdict.verdict == "MODIFY"
    assert verdict.rationale == "Too risky"
    assert verdict.suggested_args == {"path": "/tmp/safe.txt"}


def test_parse_verdict_valid_reject_with_markdown_fence():
    raw = '''```json
    {"verdict": "REJECT", "rationale": "Dangerous", "suggested_args": null}
    ```'''
    verdict = _parse_verdict(raw)
    assert verdict is not None
    assert verdict.verdict == "REJECT"
    assert verdict.rationale == "Dangerous"
    assert verdict.suggested_args is None


def test_parse_verdict_modify_without_args_is_invalid():
    """MODIFY verdicts must include a dict of suggested args."""
    verdict = _parse_verdict(
        '{"verdict": "MODIFY", "rationale": "x", "suggested_args": null}'
    )
    assert verdict is None


def test_parse_verdict_invalid():
    """Test _parse_verdict returns None for invalid input."""
    assert _parse_verdict("not json") is None
    assert _parse_verdict('{"verdict": "MAYBE"}') is None  # invalid verdict
    # Missing required fields
    assert _parse_verdict('{"verdict": "APPROVE", "rationale": "r"}') is None
    assert _parse_verdict('{"verdict": "APPROVE", "suggested_args": {}}') is None
    assert _parse_verdict('{"rationale": "r", "suggested_args": {}}') is None


def test_looks_like_rate_limit():
    """Test _looks_like_rate_limit."""
    assert _looks_like_rate_limit(Exception("Rate limit exceeded"))
    assert _looks_like_rate_limit(Exception("rate_limit"))
    assert _looks_like_rate_limit(Exception("too many requests"))
    assert _looks_like_rate_limit(Exception("HTTP 429"))
    assert _looks_like_rate_limit(Exception("quota exceeded"))
    assert _looks_like_rate_limit(Exception("throttled"))
    assert not _looks_like_rate_limit(Exception("some other error"))


# ── LLM Review Path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_with_llm_provider_missing():
    """When the configured provider is missing, default to APPROVE and
    do NOT call the circuit breaker's record() (no LLM call happened)."""
    mock_provider_registry = MagicMock()
    mock_provider_registry.get = lambda x: None
    mock_provider_registry.active_id = "unknown"
    mock_provider_registry.active = None

    config = GuardianConfig(level="all", provider_id="unknown")
    risk = RiskAssessment(level="low", reasons=[])
    recent_context = "ctx"

    with patch("clew.agent.guardian.get_circuit_breaker_registry") as mock_breaker_registry:
        mock_breaker = MagicMock()
        mock_breaker.try_claim.return_value = True
        mock_breaker_registry.return_value.get.return_value = mock_breaker

        verdict = await review_with_llm(
            config=config,
            tool_name="read_file",
            args={"path": "foo.txt"},
            risk=risk,
            recent_context=recent_context,
            provider_registry=mock_provider_registry,
            workspace="/workspace",
        )

    assert verdict.verdict == "APPROVE"
    assert "provider 'unknown' not found" in verdict.rationale
    assert verdict.suggested_args is None
    # Provider missing → no LLM call → breaker.record must NOT fire
    mock_breaker.record.assert_not_called()


@pytest.mark.asyncio
async def test_review_with_llm_success():
    """Test review_with_llm with a successful LLM APPROVE response."""
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=MagicMock(
            text='{"verdict": "APPROVE", "rationale": "Safe operation", "suggested_args": null}'
        )
    )
    mock_provider_registry = MagicMock()
    mock_provider_registry.get = lambda x: mock_provider
    mock_provider_registry.active_id = "test-provider"
    mock_provider_registry.active = mock_provider

    config = GuardianConfig(level="all")
    risk = RiskAssessment(level="low", reasons=[])
    recent_context = "ctx"

    with patch("clew.agent.guardian.get_circuit_breaker_registry") as mock_breaker_registry:
        mock_breaker = MagicMock()
        mock_breaker.try_claim.return_value = True
        mock_breaker_registry.return_value.get.return_value = mock_breaker

        verdict = await review_with_llm(
            config=config,
            tool_name="read_file",
            args={"path": "foo.txt"},
            risk=risk,
            recent_context=recent_context,
            provider_registry=mock_provider_registry,
            workspace="/workspace",
        )

    assert verdict.verdict == "APPROVE"
    assert verdict.rationale == "Safe operation"
    assert verdict.suggested_args is None
    mock_breaker.record.assert_called_once_with(ok=True)


@pytest.mark.asyncio
async def test_review_with_llm_modify_verdict():
    """MODIFY verdicts are propagated with the suggested args."""
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=MagicMock(
            text='{"verdict": "MODIFY", "rationale": "Use tmp", "suggested_args": {"path": "/tmp/safe.txt"}}'
        )
    )
    mock_provider_registry = MagicMock()
    mock_provider_registry.get = lambda x: mock_provider
    mock_provider_registry.active_id = "test-provider"
    mock_provider_registry.active = mock_provider

    config = GuardianConfig(level="all")
    risk = RiskAssessment(level="high", reasons=["dangerous pattern: rm -rf"])

    with patch("clew.agent.guardian.get_circuit_breaker_registry") as mock_breaker_registry:
        mock_breaker = MagicMock()
        mock_breaker.try_claim.return_value = True
        mock_breaker_registry.return_value.get.return_value = mock_breaker

        verdict = await review_with_llm(
            config=config,
            tool_name="execute_command",
            args={"command": "rm -rf /"},
            risk=risk,
            recent_context="",
            provider_registry=mock_provider_registry,
            workspace="/workspace",
        )

    assert verdict.verdict == "MODIFY"
    assert verdict.suggested_args == {"path": "/tmp/safe.txt"}
    mock_breaker.record.assert_called_once_with(ok=True)


@pytest.mark.asyncio
async def test_review_with_llm_circuit_open():
    """If the circuit breaker is open, the review is REJECT."""
    mock_provider_registry = MagicMock()
    mock_provider_registry.active_id = "test-provider"
    mock_provider_registry.active = MagicMock()

    config = GuardianConfig(level="all")
    risk = RiskAssessment(level="low", reasons=[])

    with patch("clew.agent.guardian.get_circuit_breaker_registry") as mock_breaker_registry:
        mock_breaker = MagicMock()
        mock_breaker.try_claim.return_value = False  # circuit open
        mock_breaker_registry.return_value.get.return_value = mock_breaker

        verdict = await review_with_llm(
            config=config,
            tool_name="read_file",
            args={"path": "foo.txt"},
            risk=risk,
            recent_context="",
            provider_registry=mock_provider_registry,
            workspace="/workspace",
        )

    assert verdict.verdict == "REJECT"
    assert "rate limited" in verdict.rationale.lower()


@pytest.mark.asyncio
async def test_review_with_llm_unparseable_response():
    """If the LLM response can't be parsed, default to APPROVE."""
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=MagicMock(text="the model went off the rails")
    )
    mock_provider_registry = MagicMock()
    mock_provider_registry.get = lambda x: mock_provider
    mock_provider_registry.active_id = "test-provider"
    mock_provider_registry.active = mock_provider

    config = GuardianConfig(level="all")
    risk = RiskAssessment(level="low", reasons=[])

    with patch("clew.agent.guardian.get_circuit_breaker_registry") as mock_breaker_registry:
        mock_breaker = MagicMock()
        mock_breaker.try_claim.return_value = True
        mock_breaker_registry.return_value.get.return_value = mock_breaker

        verdict = await review_with_llm(
            config=config,
            tool_name="read_file",
            args={"path": "foo.txt"},
            risk=risk,
            recent_context="",
            provider_registry=mock_provider_registry,
            workspace="/workspace",
        )

    assert verdict.verdict == "APPROVE"
    assert "unparseable" in verdict.rationale.lower()
    mock_breaker.record.assert_called_once_with(ok=True)


@pytest.mark.asyncio
async def test_review_with_llm_provider_error_records_failure():
    """If provider.generate raises, breaker.record is called with ok=False."""
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(side_effect=RuntimeError("rate limit exceeded"))
    mock_provider_registry = MagicMock()
    mock_provider_registry.get = lambda x: mock_provider
    mock_provider_registry.active_id = "test-provider"
    mock_provider_registry.active = mock_provider

    config = GuardianConfig(level="all")
    risk = RiskAssessment(level="low", reasons=[])

    with patch("clew.agent.guardian.get_circuit_breaker_registry") as mock_breaker_registry:
        mock_breaker = MagicMock()
        mock_breaker.try_claim.return_value = True
        mock_breaker_registry.return_value.get.return_value = mock_breaker

        verdict = await review_with_llm(
            config=config,
            tool_name="read_file",
            args={"path": "foo.txt"},
            risk=risk,
            recent_context="",
            provider_registry=mock_provider_registry,
            workspace="/workspace",
        )

    assert verdict.verdict == "APPROVE"
    mock_breaker.record.assert_called_once()
    _, kwargs = mock_breaker.record.call_args
    assert kwargs.get("ok") is False
    assert kwargs.get("rate_limited") is True


# ── Issue #4: Subagent Reviewer ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_with_subagent_happy_path():
    """Issue #4: subagent reviewer returns parsed verdict."""
    raw = '{"verdict": "REJECT", "rationale": "too dangerous", "suggested_args": null}'

    async def fake_spawn(runtime, subagent_type, prompt):
        return raw

    verdict = await review_with_subagent(
        config=GuardianConfig(level="all", use_subagent=True),
        tool_name="execute_command",
        args={"command": "rm -rf /"},
        risk=RiskAssessment(level="high", reasons=["dangerous"]),
        recent_context="",
        provider_registry=MagicMock(),
        workspace="/workspace",
        spawn_fn=fake_spawn,
    )
    assert verdict.verdict == "REJECT"
    assert "too dangerous" in verdict.rationale


@pytest.mark.asyncio
async def test_review_with_subagent_unparseable():
    """Issue #4: subagent returning garbage defaults to APPROVE."""

    async def fake_spawn(runtime, subagent_type, prompt):
        return "nope"

    verdict = await review_with_subagent(
        config=GuardianConfig(level="all", use_subagent=True),
        tool_name="read_file",
        args={"path": "x"},
        risk=RiskAssessment(level="low", reasons=[]),
        recent_context="",
        provider_registry=MagicMock(),
        workspace="/workspace",
        spawn_fn=fake_spawn,
    )
    assert verdict.verdict == "APPROVE"
    assert "unparseable" in verdict.rationale.lower()


@pytest.mark.asyncio
async def test_review_with_subagent_spawn_failure():
    """Issue #4: spawn_fn raising -> default APPROVE."""

    async def fake_spawn(runtime, subagent_type, prompt):
        raise RuntimeError("boom")

    verdict = await review_with_subagent(
        config=GuardianConfig(level="all", use_subagent=True),
        tool_name="read_file",
        args={"path": "x"},
        risk=RiskAssessment(level="low", reasons=[]),
        recent_context="",
        provider_registry=MagicMock(),
        workspace="/workspace",
        spawn_fn=fake_spawn,
    )
    assert verdict.verdict == "APPROVE"
    assert "spawn failed" in verdict.rationale.lower()


@pytest.mark.asyncio
async def test_review_with_subagent_text_object():
    """Issue #4: subagent may return an object with .text attribute."""

    class FakeResp:
        text = '{"verdict": "APPROVE", "rationale": "ok", "suggested_args": null}'

    async def fake_spawn(runtime, subagent_type, prompt):
        return FakeResp()

    verdict = await review_with_subagent(
        config=GuardianConfig(level="all", use_subagent=True),
        tool_name="read_file",
        args={"path": "x"},
        risk=RiskAssessment(level="low", reasons=[]),
        recent_context="",
        provider_registry=MagicMock(),
        workspace="/workspace",
        spawn_fn=fake_spawn,
    )
    assert verdict.verdict == "APPROVE"


@pytest.mark.asyncio
async def test_review_with_llm_delegates_to_subagent_when_configured():
    """Issue #4: review_with_llm routes to review_with_subagent when flag set."""
    raw = '{"verdict": "APPROVE", "rationale": "ok", "suggested_args": null}'

    call_count = {"n": 0}

    async def fake_spawn(runtime, subagent_type, prompt):
        call_count["n"] += 1
        return raw

    verdict = await review_with_llm(
        config=GuardianConfig(level="all", use_subagent=True),
        tool_name="read_file",
        args={"path": "x"},
        risk=RiskAssessment(level="low", reasons=[]),
        recent_context="",
        provider_registry=MagicMock(),
        workspace="/workspace",
        _spawn_fn_for_test=fake_spawn,
    )
    assert verdict.verdict == "APPROVE"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_review_with_subagent_no_runtime_no_spawn_fn():
    """Issue #4: without spawn_fn and runtime, defaults to APPROVE."""
    verdict = await review_with_subagent(
        config=GuardianConfig(level="all", use_subagent=True),
        tool_name="read_file",
        args={"path": "x"},
        risk=RiskAssessment(level="low", reasons=[]),
        recent_context="",
        provider_registry=MagicMock(),
        workspace="/workspace",
    )
    assert verdict.verdict == "APPROVE"
    assert "runtime" in verdict.rationale.lower() or "unavailable" in verdict.rationale.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
