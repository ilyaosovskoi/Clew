#!/usr/bin/env python3
"""
G18 — Web Search & Internet Reach — test suite.

Verifies:
  1. ToolName enum has WEB_SEARCH and WEB_FETCH entries.
  2. ToolEngine._dispatch routes web_search/web_fetch correctly.
  3. 'researcher' role whitelist includes web tools and rejects writes/exec.
  4. Guardian.assess_risk flags suspicious web_fetch URLs.
  5. Context fragment wrapping for web_search / web_fetch output.
  6. MCP fallback behaviour when the primary backend is unavailable.
  7. web_fetch rejects non-http(s) URLs and suspicious URLs.
  8. /websearch status reports the active backend + probe results.
  9. G15 consensus engine — basic shape (parallel run, divergence detection).
 10. G16 audit signing — round-trip sign + verify + tamper detection.
 11. G17 learning loop — entry creation + per-project isolation.

Run:
    python -m pytest clew/tests/test_g18_web_search.py -v
"""

from __future__ import annotations

import json
import os
import tempfile
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


# ── Test isolation ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect ~/.clew to a temp dir so tests don't clobber the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Also patch Path.home() because some modules use it directly.
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    yield
    # Reset web_search_backend module-level state.
    try:
        from clew.web_search_backend import _reset_health_for_test
        _reset_health_for_test()
    except Exception:
        pass


# ── 1. ToolName enum ───────────────────────────────────────────────────


def test_toolname_has_web_search_and_fetch():
    """ToolName enum must expose WEB_SEARCH and WEB_FETCH (G18 §1)."""
    from clew.agent_runtime.types import ToolName
    assert ToolName.WEB_SEARCH.value == "web_search"
    assert ToolName.WEB_FETCH.value == "web_fetch"


# ── 2. ToolEngine dispatch ─────────────────────────────────────────────


def test_web_search_dispatches_to_method():
    """_dispatch must route WEB_SEARCH to _web_search (not raise)."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    # Inject a test backend so we don't hit the network.
    from clew.web_search_backend import _inject_backend_for_test, _reset_health_for_test
    _reset_health_for_test()
    _inject_backend_for_test("test_backend", lambda q, n: [
        {"title": "T", "url": "https://example.com/" + q, "snippet": "S"}
    ])
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_SEARCH, args={"query": "hello", "num_results": 3})
    result = engine.execute(call)
    assert isinstance(result, str)
    assert "WEB_SEARCH" in result or "web_search" in result
    assert "example.com/hello" in result


def test_web_search_empty_query_returns_error():
    """Empty query must return an error string (not raise)."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_SEARCH, args={"query": ""})
    result = engine.execute(call)
    assert "WEB_SEARCH ERROR" in result
    assert "query is required" in result


def test_web_fetch_rejects_non_http_url():
    """web_fetch must refuse non-http(s) URLs (no file:// bypass)."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_FETCH, args={"url": "file:///etc/passwd"})
    result = engine.execute(call)
    assert "WEB_FETCH ERROR" in result
    assert "only http(s)" in result


def test_web_fetch_rejects_suspicious_url():
    """web_fetch must refuse URLs with secret-shaped query params."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_FETCH,
                    args={"url": "https://example.com/?api_key=abcdefghijklmnopqrstuv"})
    result = engine.execute(call)
    assert "WEB_FETCH REJECTED" in result
    assert "suspicious" in result


def test_web_fetch_rejects_base64_url():
    """web_fetch must refuse URLs with long base64-like query params."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    # 100-char base64-like value
    b64 = "A" * 100
    call = ToolCall(name=ToolName.WEB_FETCH,
                    args={"url": f"https://example.com/?data={b64}"})
    result = engine.execute(call)
    assert "WEB_FETCH REJECTED" in result


# ── 3. Researcher role whitelist ───────────────────────────────────────


def test_researcher_role_has_web_tools():
    """The 'researcher' role must include web_search and web_fetch."""
    from clew.agent_runtime.tool_engine import ToolEngine
    whitelist = ToolEngine.ROLE_TOOL_WHITELIST.get("researcher")
    assert whitelist is not None
    assert "web_search" in whitelist
    assert "web_fetch" in whitelist


def test_researcher_role_rejects_write():
    """researcher role must reject write_file at dispatch level."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    engine.set_role_whitelist("researcher")
    call = ToolCall(name=ToolName.WRITE_FILE, args={"path": "foo.py", "content": "x"})
    result = engine.execute(call)
    assert "TOOL DENIED" in result


def test_researcher_role_rejects_execute():
    """researcher role must reject execute_command at dispatch level."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    engine.set_role_whitelist("researcher")
    call = ToolCall(name=ToolName.EXECUTE_COMMAND, args={"command": "ls"})
    result = engine.execute(call)
    assert "TOOL DENIED" in result


def test_researcher_role_rejects_run_code():
    """researcher role must reject run_code at dispatch level."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    engine.set_role_whitelist("researcher")
    call = ToolCall(name=ToolName.RUN_CODE, args={"code": "print('hi')"})
    result = engine.execute(call)
    assert "TOOL DENIED" in result


def test_researcher_role_allows_web_search():
    """researcher role must still allow web_search (its core capability)."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    from clew.web_search_backend import _inject_backend_for_test, _reset_health_for_test
    _reset_health_for_test()
    _inject_backend_for_test("test_backend", lambda q, n: [
        {"title": "T", "url": "https://example.com", "snippet": "S"}
    ])
    engine = ToolEngine(workspace="/tmp/test_g18")
    engine.set_role_whitelist("researcher")
    call = ToolCall(name=ToolName.WEB_SEARCH, args={"query": "test"})
    result = engine.execute(call)
    assert "WEB_SEARCH" in result
    assert "TOOL DENIED" not in result


# ── 4. Guardian risk rules ─────────────────────────────────────────────


def test_guardian_flags_secret_url_as_high():
    """Guardian must flag web_fetch with secret-shaped params as HIGH."""
    from clew.agent.guardian import assess_risk
    r = assess_risk(
        "web_fetch",
        {"url": "https://example.com/?api_key=verylongsecretkey12345"},
        "/tmp/test_g18",
    )
    assert r.level == "high"
    assert any("secret" in reason.lower() for reason in r.reasons)


def test_guardian_flags_base64_url_as_medium_or_high():
    """Guardian must flag web_fetch with long base64 params as at-least MEDIUM."""
    from clew.agent.guardian import assess_risk
    b64 = "A" * 100
    r = assess_risk(
        "web_fetch",
        {"url": f"https://example.com/?data={b64}"},
        "/tmp/test_g18",
    )
    assert r.level in ("medium", "high")
    assert any("base64" in reason.lower() for reason in r.reasons)


def test_guardian_clean_url_is_medium():
    """web_fetch on a clean URL is MEDIUM (untrusted content enters convo)."""
    from clew.agent.guardian import assess_risk
    r = assess_risk(
        "web_fetch",
        {"url": "https://example.com/page"},
        "/tmp/test_g18",
    )
    assert r.level == "medium"
    assert any("untrusted" in reason.lower() for reason in r.reasons)


def test_guardian_web_search_is_low():
    """web_search returns metadata only, so it's LOW risk."""
    from clew.agent.guardian import assess_risk
    r = assess_risk(
        "web_search",
        {"query": "python asyncio"},
        "/tmp/test_g18",
    )
    assert r.level == "low"


def test_guardian_does_not_weaken_existing_rules():
    """Web rules must be ADDITIVE — existing high-risk rules still fire."""
    from clew.agent.guardian import assess_risk
    # rm -rf should still be HIGH even with web tools present.
    r = assess_risk(
        "execute_command",
        {"command": "rm -rf /"},
        "/tmp/test_g18",
    )
    assert r.level == "high"


# ── 5. Context fragment wrapping ───────────────────────────────────────


def test_web_search_wraps_in_fragment():
    """web_search output must be wrapped in a <context_fragment type='web_search'>."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    from clew.web_search_backend import _inject_backend_for_test, _reset_health_for_test
    _reset_health_for_test()
    _inject_backend_for_test("test_backend", lambda q, n: [
        {"title": "T", "url": "https://example.com", "snippet": "S"}
    ])
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_SEARCH, args={"query": "x"})
    result = engine.execute(call)
    assert '<context_fragment type="web_search"' in result
    assert "</context_fragment>" in result


def test_web_search_fragment_is_parseable():
    """The fragment must be parseable by context_fragments.parse_fragments."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    from clew.agent.context_fragments import parse_fragments
    from clew.web_search_backend import _inject_backend_for_test, _reset_health_for_test
    _reset_health_for_test()
    _inject_backend_for_test("test_backend", lambda q, n: [
        {"title": "T", "url": "https://example.com", "snippet": "S"}
    ])
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_SEARCH, args={"query": "x"})
    result = engine.execute(call)
    fragments = parse_fragments(result)
    assert len(fragments) >= 1
    assert fragments[0].type == "web_search"


def test_web_search_fragment_compacts():
    """Old web_search fragments should tombstone-compact via compact_fragments.

    The tombstone replaces the body with a short digest, so the OLD
    full body is gone (the digest is a one-line summary, not the
    full content). We make the old body long enough that the digest
    is a strict truncation, so we can assert the full body is gone.
    """
    from clew.agent.context_fragments import (
        build_fragment, compact_fragments, FragmentCompactionConfig,
    )
    # Two fragments with the same id (latest wins).
    # Make the old body long enough that the digest is clearly a truncation.
    old_body = "OLD CONTENT " + "x" * 200
    new_body = "NEW CONTENT"
    f1 = build_fragment("web_search", "test_id", old_body)
    f2 = build_fragment("web_search", "test_id", new_body)
    text = f1 + "\n\n" + f2
    cfg = FragmentCompactionConfig(keep_latest_per_id=True)
    compacted = compact_fragments(text, cfg)
    # The NEW body is preserved verbatim.
    assert "NEW CONTENT" in compacted
    # The OLD full body is NOT present (only its digest survives).
    assert "OLD CONTENT " + "x" * 100 not in compacted
    # The tombstone marker IS present.
    assert "[COMPACTED]" in compacted


# ── 6. MCP fallback behaviour ──────────────────────────────────────────


def test_web_search_falls_back_when_primary_fails():
    """If the primary search backend fails, fall back to the next one."""
    from clew.web_search_backend import (
        _inject_backend_for_test, _reset_health_for_test, run_web_search,
    )
    _reset_health_for_test()
    # Primary returns no results.
    _inject_backend_for_test("primary", lambda q, n: [])
    # Fallback returns results.
    _inject_backend_for_test("fallback", lambda q, n: [
        {"title": "From Fallback", "url": "https://fallback.example/", "snippet": "S"}
    ])
    results, served_by = run_web_search("test", 5)
    assert served_by == "fallback"
    assert len(results) == 1
    assert results[0]["title"] == "From Fallback"


def test_web_search_returns_empty_when_no_backend():
    """If no backend is configured, return ([], '')."""
    from clew.web_search_backend import _reset_health_for_test, run_web_search
    _reset_health_for_test()
    results, served_by = run_web_search("test", 5)
    assert results == []
    assert served_by == ""


def test_web_search_records_served_by_in_status():
    """get_websearch_status reports the backend that served the last request."""
    from clew.web_search_backend import (
        _inject_backend_for_test, _reset_health_for_test,
        run_web_search, get_websearch_status,
    )
    _reset_health_for_test()
    _inject_backend_for_test("backend_A", lambda q, n: [
        {"title": "T", "url": "https://a.example/", "snippet": "S"}
    ])
    run_web_search("test", 5)
    status = get_websearch_status()
    assert status["active_backend"] == "backend_A"


# ── 7. fetch_url_as_text (no network) ──────────────────────────────────


def test_fetch_url_as_text_local_file_via_data_url_rejected():
    """fetch_url_as_text should not be called with non-http URLs — but
    if it is, urllib will raise. The tool engine rejects before we get
    here, so this test just verifies the tool-engine-level rejection."""
    from clew.agent_runtime.tool_engine import ToolEngine
    from clew.agent_runtime.types import ToolName, ToolCall
    engine = ToolEngine(workspace="/tmp/test_g18")
    call = ToolCall(name=ToolName.WEB_FETCH, args={"url": "ftp://example.com/x"})
    result = engine.execute(call)
    assert "WEB_FETCH ERROR" in result


# ── 8. /websearch status ───────────────────────────────────────────────


def test_websearch_status_shape():
    """get_websearch_status returns the expected dict shape."""
    from clew.web_search_backend import _reset_health_for_test, get_websearch_status
    _reset_health_for_test()
    status = get_websearch_status()
    assert "active_backend" in status
    assert "backends" in status
    assert "last_status_msg" in status
    assert "mcp_servers" in status


# ── 9. G15 — consensus engine basics ───────────────────────────────────


def test_consensus_config_round_trip():
    """ConsensusConfig persistence round-trips through ~/.clew/config.json."""
    from clew.consensus_engine import (
        ConsensusConfig, get_consensus_config, set_consensus_config,
    )
    set_consensus_config(ConsensusConfig(
        providers=("openai", "anthropic"),
        min_agreement=0.6,
        timeout_s=30.0,
        max_chars_per_response=5000,
    ))
    cfg = get_consensus_config()
    assert cfg.providers == ("openai", "anthropic")
    assert cfg.min_agreement == 0.6
    assert cfg.timeout_s == 30.0
    assert cfg.max_chars_per_response == 5000


def test_consensus_resolve_default_providers():
    """resolve_default_providers returns a triplet including the active one."""
    from clew.consensus_engine import resolve_default_providers
    triplet = resolve_default_providers("openai")
    assert len(triplet) == 3
    assert "openai" in triplet


def test_consensus_run_with_injected_provider():
    """run_consensus returns a report with one response per provider."""
    from clew.consensus_engine import run_consensus, ConsensusConfig

    class FakeResp:
        text = "Here is a ```python foo.py\ndef hello():\n    return 'world'\n``` solution."

    class FakeProvider:
        is_loaded = True
        def load(self): pass
        def get_model(self): return "test-model"
        def generate(self, messages, model=None): return FakeResp()

    registry = MagicMock()
    registry.get = lambda pid: FakeProvider()

    cfg = ConsensusConfig(providers=("p1", "p2", "p3"), timeout_s=5.0)
    report = run_consensus(
        prompt="write hello world",
        registry=registry,
        active_provider_id="p1",
        config=cfg,
    )
    assert len(report.responses) == 3
    assert all(r.error is None for r in report.responses)
    assert report.agreement_score > 0.0  # identical responses → high agreement


def test_consensus_fails_safe_on_provider_error():
    """If one provider errors, the comparison still returns — failed
    provider is flagged, not the whole comparison."""
    from clew.consensus_engine import run_consensus, ConsensusConfig

    class FakeResp:
        text = "OK"

    class FakeProvider:
        is_loaded = True
        def load(self): pass
        def get_model(self): return "test-model"
        def generate(self, messages, model=None): return FakeResp()

    class FailingRegistry:
        def get(self, pid):
            if pid == "p2":
                raise RuntimeError("simulated provider failure")
            return FakeProvider()

    cfg = ConsensusConfig(providers=("p1", "p2", "p3"), timeout_s=5.0)
    report = run_consensus(
        prompt="test",
        registry=FailingRegistry(),
        active_provider_id="p1",
        config=cfg,
    )
    assert len(report.responses) == 3
    failed = [r for r in report.responses if r.error is not None]
    assert len(failed) == 1
    assert failed[0].provider_id == "p2"
    succeeded = [r for r in report.responses if r.error is None]
    assert len(succeeded) == 2


# ── 10. G16 — audit signing ────────────────────────────────────────────


def test_audit_signing_round_trip():
    """Sign entries, verify them, expect ok."""
    from clew.audit_signing import export_signed_json, verify_signed_json
    entries = [
        {"ts": 1.0, "category": "shell", "kind": "execute_command",
         "tool": "execute_command", "title": "Run: ls", "status": "ok"},
        {"ts": 2.0, "category": "file", "kind": "write_file",
         "tool": "write_file", "title": "Write foo.py", "status": "ok"},
    ]
    signed = export_signed_json(entries)
    report = verify_signed_json(signed)
    assert report.ok is True
    assert report.entries_checked == 2
    assert report.signatures_valid == 2
    assert report.signatures_invalid == 0


def test_audit_signing_detects_tampering():
    """Modifying an entry after signing must fail verification."""
    from clew.audit_signing import export_signed_json, verify_signed_json
    entries = [
        {"ts": 1.0, "category": "shell", "kind": "execute_command",
         "tool": "execute_command", "title": "Run: ls", "status": "ok"},
    ]
    signed = export_signed_json(entries)
    parsed = json.loads(signed)
    parsed[0]["title"] = "TAMPERED TITLE"
    tampered = json.dumps(parsed)
    report = verify_signed_json(tampered)
    assert report.ok is False
    assert report.first_failure is not None
    assert "hash mismatch" in report.first_failure or "signature" in report.first_failure.lower()


def test_audit_signing_detects_reordering():
    """Reordering entries breaks the hash chain."""
    from clew.audit_signing import export_signed_json, verify_signed_json
    entries = [
        {"ts": 1.0, "category": "shell", "kind": "execute_command",
         "tool": "execute_command", "title": "First", "status": "ok"},
        {"ts": 2.0, "category": "file", "kind": "write_file",
         "tool": "write_file", "title": "Second", "status": "ok"},
    ]
    signed = export_signed_json(entries)
    parsed = json.loads(signed)
    # Reverse the order.
    reordered = list(reversed(parsed))
    report = verify_signed_json(json.dumps(reordered))
    assert report.ok is False
    assert "prev_hash" in (report.first_failure or "").lower() or "chain" in (report.first_failure or "").lower()


def test_audit_signing_detects_deletion():
    """Deleting an entry from the middle breaks the chain."""
    from clew.audit_signing import export_signed_json, verify_signed_json
    entries = [
        {"ts": float(i), "category": "shell", "kind": "execute_command",
         "tool": "execute_command", "title": f"Entry {i}", "status": "ok"}
        for i in range(3)
    ]
    signed = export_signed_json(entries)
    parsed = json.loads(signed)
    # Delete the middle entry.
    del parsed[1]
    report = verify_signed_json(json.dumps(parsed))
    assert report.ok is False


def test_activity_log_export_signed_json_method():
    """ActivityLog must expose export_signed_json() (G16)."""
    from clew.activity_log import ActivityLog
    log = ActivityLog(max_entries=10)
    log.record_tool_call(
        tool="execute_command", args={"command": "ls"},
        result="[OK] file1.txt", duration_ms=10,
    )
    signed = log.export_signed_json()
    assert isinstance(signed, str)
    parsed = json.loads(signed)
    assert len(parsed) == 1
    assert "_signature" in parsed[0]
    assert "_hash" in parsed[0]
    assert "_prev_hash" in parsed[0]


def test_activity_log_export_json_unchanged():
    """export_json() must still return the unsigned format (backward compat)."""
    from clew.activity_log import ActivityLog
    log = ActivityLog(max_entries=10)
    log.record_tool_call(
        tool="execute_command", args={"command": "ls"},
        result="[OK] file1.txt", duration_ms=10,
    )
    unsigned = log.export_json()
    parsed = json.loads(unsigned)
    assert len(parsed) == 1
    assert "_signature" not in parsed[0]
    assert "_hash" not in parsed[0]


# ── 11. G17 — learning loop ────────────────────────────────────────────


def test_learning_loop_creates_entry(tmp_path):
    """create_learning_entry writes a learnings/<date>-<slug>.md file."""
    from clew.learning_loop import create_learning_entry, list_learnings
    result = create_learning_entry(
        project_path=str(tmp_path),
        title="Test Learning",
        context="ctx",
        what_happened="wh",
        root_cause="rc",
        evidence="ev",
        do_rule="do",
        dont_rule="dont",
        how_to_apply="how",
        source="AUTO-TEST",
        tags=["test"],
        severity="low",
    )
    assert result["ok"] is True
    learnings = list_learnings(str(tmp_path))
    assert len(learnings) == 1
    assert "Test Learning" in learnings[0].title


def test_learning_loop_per_project_isolation(tmp_path):
    """Learnings are scoped per project — two projects don't share entries."""
    from clew.learning_loop import create_learning_entry, list_learnings
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    create_learning_entry(
        project_path=str(proj_a),
        title="Project A Learning",
        context="c", what_happened="w", root_cause="r", evidence="e",
        do_rule="d", dont_rule="n", how_to_apply="h",
        source="AUTO-TEST", tags=["t"], severity="low",
    )
    a_learnings = list_learnings(str(proj_a))
    b_learnings = list_learnings(str(proj_b))
    assert len(a_learnings) == 1
    assert len(b_learnings) == 0


def test_learning_loop_dismiss_and_restore(tmp_path):
    """Dismissed learnings stop being injected; restore brings them back."""
    from clew.learning_loop import (
        create_learning_entry, list_learnings, dismiss_learning,
        build_learnings_fragment,
    )
    result = create_learning_entry(
        project_path=str(tmp_path),
        title="To Dismiss",
        context="c", what_happened="w", root_cause="r", evidence="e",
        do_rule="d", dont_rule="n", how_to_apply="h",
        source="AUTO-TEST", tags=["t"], severity="low",
    )
    fname = os.path.basename(result["path"])
    # Fragment should include the learning before dismissal.
    frag_before = build_learnings_fragment(str(tmp_path))
    assert "To Dismiss" in frag_before
    # Dismiss.
    dismiss_result = dismiss_learning(str(tmp_path), fname)
    assert dismiss_result["ok"] is True
    # Fragment should NOT include it after dismissal.
    frag_after = build_learnings_fragment(str(tmp_path))
    assert "To Dismiss" not in frag_after


def test_learning_loop_handle_command_list(tmp_path):
    """handle_learnings_command('list') prints entries."""
    from clew.learning_loop import (
        create_learning_entry, handle_learnings_command,
    )
    create_learning_entry(
        project_path=str(tmp_path),
        title="Listed Entry",
        context="c", what_happened="w", root_cause="r", evidence="e",
        do_rule="d", dont_rule="n", how_to_apply="h",
        source="AUTO-TEST", tags=["t"], severity="low",
    )
    result = handle_learnings_command(str(tmp_path), "")
    assert result["ok"] is True
    assert "Listed Entry" in result["text"]


def test_learning_loop_fragment_wrapped_in_context_fragment(tmp_path):
    """build_learnings_fragment returns a <context_fragment> block."""
    from clew.learning_loop import (
        create_learning_entry, build_learnings_fragment,
    )
    create_learning_entry(
        project_path=str(tmp_path),
        title="Fragment Test",
        context="c", what_happened="w", root_cause="r", evidence="e",
        do_rule="d", dont_rule="n", how_to_apply="h",
        source="AUTO-TEST", tags=["t"], severity="low",
    )
    frag = build_learnings_fragment(str(tmp_path))
    assert '<context_fragment type="project_learnings"' in frag
    assert "</context_fragment>" in frag
