#!/usr/bin/env python3
"""
Goal G5 — Agent Identity & Tool-call Audit — test suite.

Verifies:
  1. AgentIdentity value-object: construction, child derivation, serialization.
  2. Root identity is a process-wide singleton.
  3. AuditTrail.record() stores the agent identity in entry.meta.agent.
  4. AuditTrail.agent_summary() groups by agent id and counts tools/errors.
  5. AuditTrail.filter_by_agent() honours include_children (parent_chain).
  6. AuditTrail.export_audit_json() adds SHA-256 fingerprints per entry.
  7. AuditTrail.verify_fingerprint() detects tampering.
  8. AuditTrail.export_audit_csv() produces a valid CSV with headers.
  9. AuditTrail.list_agents() returns flat list with last_active timestamps.
 10. Bridge-level integration: ClewBridge.get_agent_identity() / list_agents() /
     filter_audit_by_agent() / export_audit_json() work end-to-end.

Run:
    python -m pytest clew/tests/test_g5_agent_identity.py -v
or:
    python clew/tests/test_g5_agent_identity.py
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from pathlib import Path

import pytest


# ── Test isolation: ensure each test starts with fresh singletons ────

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset the global ActivityLog, AuditTrail, and root identity before each test."""
    import clew.activity_log as _al
    import clew.agent_identity as _ai
    # Activity log
    _al._GLOBAL_LOG = None
    # Audit trail
    _ai._audit = None
    _ai._ROOT_IDENTITY = None
    yield
    _al._GLOBAL_LOG = None
    _ai._audit = None
    _ai._ROOT_IDENTITY = None


# ── 1. AgentIdentity value-object ───────────────────────────────────

def test_agent_identity_construction():
    from clew.agent_identity import AgentIdentity, ROLE_ROOT
    ident = AgentIdentity(id="agt_test", role=ROLE_ROOT, name="root")
    assert ident.id == "agt_test"
    assert ident.role == "root"
    assert ident.name == "root"
    assert ident.parent_chain == ()


def test_agent_identity_child_extends_chain():
    from clew.agent_identity import AgentIdentity, ROLE_PLANNER, ROLE_IMPLEMENTER
    root = AgentIdentity(id="agt_root", role="root", name="root")
    planner = root.child(role=ROLE_PLANNER, name="plan-1")
    impl = planner.child(role=ROLE_IMPLEMENTER, name="impl-3")

    assert planner.parent_chain == ("agt_root",)
    assert impl.parent_chain == ("agt_root", planner.id)
    assert planner.id != root.id
    assert impl.id != planner.id


def test_agent_identity_serialization_roundtrip():
    from clew.agent_identity import AgentIdentity
    orig = AgentIdentity(
        id="agt_x", role="subagent", name="explore-1",
        parent_chain=("agt_root", "agt_plan"),
    )
    d = orig.to_dict()
    assert d["id"] == "agt_x"
    assert d["role"] == "subagent"
    assert d["parent_chain"] == ["agt_root", "agt_plan"]

    restored = AgentIdentity.from_dict(d)
    assert restored == orig


# ── 2. Root identity singleton ──────────────────────────────────────

def test_root_identity_is_singleton():
    from clew.agent_identity import get_root_identity
    a = get_root_identity()
    b = get_root_identity()
    assert a is b
    assert a.role == "root"


def test_reset_root_identity_for_test_returns_new():
    from clew.agent_identity import get_root_identity, reset_root_identity_for_test
    a = get_root_identity()
    b = reset_root_identity_for_test()
    assert a.id != b.id


# ── 3. AuditTrail.record stores identity in entry.meta.agent ────────

def test_record_attaches_identity_to_entry():
    from clew.agent_identity import (
        AuditTrail, AgentIdentity, ROLE_ROOT, get_audit_trail,
    )
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_test_rec", role=ROLE_ROOT, name="test-root")
    entry_id = trail.record(
        identity=ident, category="shell", kind="execute_command",
        tool="execute_command", title="Run: ls",
    )
    entry = trail._log.get(entry_id)
    assert entry is not None
    agent_field = entry.get("meta", {}).get("agent")
    assert agent_field is not None
    assert agent_field["id"] == "agt_test_rec"
    assert agent_field["role"] == "root"


def test_record_defaults_to_root_identity():
    from clew.agent_identity import get_audit_trail, get_root_identity
    trail = get_audit_trail()
    root = get_root_identity()
    entry_id = trail.record(category="info", kind="test")
    entry = trail._log.get(entry_id)
    assert entry["meta"]["agent"]["id"] == root.id


# ── 4. agent_summary groups by agent id ─────────────────────────────

def test_agent_summary_counts_tools_and_errors():
    from clew.activity_log import STATUS_ERROR, STATUS_OK
    from clew.agent_identity import (
        AuditTrail, AgentIdentity, get_audit_trail,
    )
    trail = get_audit_trail()
    ident_a = AgentIdentity(id="agt_a", role="subagent", name="A")
    ident_b = AgentIdentity(id="agt_b", role="subagent", name="B")
    # 3 calls for A (1 error), 2 calls for B (all OK)
    trail.record(identity=ident_a, tool="write_file", status=STATUS_OK, args={"path": "a.py"})
    trail.record(identity=ident_a, tool="execute_command", status=STATUS_ERROR, args={})
    trail.record(identity=ident_a, tool="read_file", status=STATUS_OK, args={})
    trail.record(identity=ident_b, tool="write_file", status=STATUS_OK, args={"path": "b.py"})
    trail.record(identity=ident_b, tool="read_file", status=STATUS_OK, args={})

    summary = trail.agent_summary()
    assert "agt_a" in summary
    assert "agt_b" in summary
    assert summary["agt_a"]["tool_calls"] == 3
    assert summary["agt_a"]["errors"] == 1
    assert summary["agt_b"]["tool_calls"] == 2
    assert summary["agt_b"]["errors"] == 0
    assert "write_file" in summary["agt_a"]["tools_used"]
    assert "execute_command" in summary["agt_a"]["tools_used"]


# ── 5. filter_by_agent honours include_children ─────────────────────

def test_filter_by_agent_includes_children():
    from clew.agent_identity import (
        AgentIdentity, get_audit_trail, ROLE_PLANNER, ROLE_IMPLEMENTER,
    )
    trail = get_audit_trail()
    root = AgentIdentity(id="agt_root_x", role="root", name="r")
    planner = root.child(role=ROLE_PLANNER, name="p")
    impl = planner.child(role=ROLE_IMPLEMENTER, name="i")

    trail.record(identity=root, tool="read_file", args={})
    trail.record(identity=planner, tool="write_file", args={})
    trail.record(identity=impl, tool="execute_command", args={})

    # include_children=True → 3 entries
    entries = trail.filter_by_agent("agt_root_x", include_children=True)
    assert len(entries) == 3

    # include_children=False → only 1 entry (root's own)
    entries = trail.filter_by_agent("agt_root_x", include_children=False)
    assert len(entries) == 1
    assert entries[0]["meta"]["agent"]["id"] == "agt_root_x"

    # Filter by planner → includes planner + impl, but NOT root
    entries = trail.filter_by_agent(planner.id, include_children=True)
    assert len(entries) == 2


# ── 6. export_audit_json adds SHA-256 fingerprints ──────────────────

def test_export_audit_json_with_fingerprints():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_fp", role="root", name="fp-test")
    trail.record(identity=ident, tool="read_file", args={"path": "x.py"})

    json_str = trail.export_audit_json(with_fingerprints=True)
    data = json.loads(json_str)
    assert len(data) == 1
    entry = data[0]
    assert "fingerprint" in entry
    assert len(entry["fingerprint"]) == 64  # SHA-256 hex


def test_export_audit_json_without_fingerprints():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_nofp", role="root", name="nofp-test")
    trail.record(identity=ident, tool="read_file", args={})
    json_str = trail.export_audit_json(with_fingerprints=False)
    data = json.loads(json_str)
    assert "fingerprint" not in data[0]


# ── 7. verify_fingerprint detects tampering ─────────────────────────

def test_verify_fingerprint_detects_tampering():
    from clew.agent_identity import AgentIdentity, AuditTrail, get_audit_trail
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_tamper", role="root", name="t-test")
    trail.record(identity=ident, tool="read_file", args={"path": "x.py"})

    json_str = trail.export_audit_json(with_fingerprints=True)
    data = json.loads(json_str)
    entry = data[0]

    # Original should verify OK
    assert AuditTrail.verify_fingerprint(entry) is True

    # Tamper with the title and re-verify
    tampered = dict(entry)
    tampered["title"] = "TAMPERED TITLE"
    assert AuditTrail.verify_fingerprint(tampered) is False


def test_verify_fingerprint_passes_when_no_fingerprint():
    from clew.agent_identity import AuditTrail
    # Entry without fingerprint → verify returns True (no check possible)
    assert AuditTrail.verify_fingerprint({"id": "x", "ts": 1.0}) is True


# ── 8. export_audit_csv produces valid CSV ──────────────────────────

def test_export_audit_csv_has_headers_and_rows():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_csv", role="root", name="csv-test")
    trail.record(
        identity=ident, tool="execute_command", args={"command": "ls"},
        title="Run: ls", path="/tmp", command="ls",
    )
    csv_str = trail.export_audit_csv()
    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agt_csv"
    assert rows[0]["tool"] == "execute_command"
    assert rows[0]["path"] == "/tmp"
    assert rows[0]["command"] == "ls"


# ── 9. list_agents returns flat list with last_active ───────────────

def test_list_agents_returns_flat_list():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    trail = get_audit_trail()
    a = AgentIdentity(id="agt_la1", role="subagent", name="A")
    b = AgentIdentity(id="agt_la2", role="subagent", name="B")
    trail.record(identity=a, tool="read_file", args={})
    trail.record(identity=b, tool="write_file", args={})
    trail.record(identity=b, tool="read_file", args={})

    agents = trail.list_agents()
    assert len(agents) == 2  # plus the root identity if it was used; here we only recorded a and b
    # B should be first (more tool_calls)
    assert agents[0]["id"] == "agt_la2"
    assert agents[0]["tool_calls"] == 2
    assert agents[0]["last_active_iso"] != ""


# ── 10. Bridge integration ──────────────────────────────────────────

def test_bridge_get_agent_identity():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.get_agent_identity()
    assert r.get("ok") is True
    assert r.get("id", "").startswith("agt_")
    assert r.get("role") == "root"


def test_bridge_list_agents_after_activity():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    from clew_tui.bridge import ClewBridge
    # Record some entries first
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_bridge_test", role="subagent", name="BT")
    trail.record(identity=ident, tool="read_file", args={})
    bridge = ClewBridge()
    agents = bridge.list_agents()
    assert any(a["id"] == "agt_bridge_test" for a in agents)


def test_bridge_export_audit_json():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    from clew_tui.bridge import ClewBridge
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_b_json", role="root", name="BJ")
    trail.record(identity=ident, tool="read_file", args={})
    bridge = ClewBridge()
    r = bridge.export_audit_json(with_fingerprints=True)
    assert r.get("ok") is True
    data = json.loads(r["json"])
    assert len(data) >= 1
    assert "fingerprint" in data[0]


def test_bridge_filter_audit_by_agent():
    from clew.agent_identity import AgentIdentity, get_audit_trail
    from clew_tui.bridge import ClewBridge
    trail = get_audit_trail()
    ident = AgentIdentity(id="agt_b_filter", role="root", name="BF")
    trail.record(identity=ident, tool="read_file", args={})
    bridge = ClewBridge()
    r = bridge.filter_audit_by_agent("agt_b_filter")
    assert r.get("ok") is True
    assert r.get("count", 0) >= 1


def test_bridge_spawn_subidentity():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.spawn_subidentity(role="subagent", name="explore-1")
    assert r.get("ok") is True
    assert r.get("role") == "subagent"
    assert r.get("name") == "explore-1"
    # parent_chain should contain the root id
    root_id = bridge.get_agent_identity().get("id")
    assert root_id in r.get("parent_chain", [])


# ── CLI entrypoint ──────────────────────────────────────────────────

if __name__ == "__main__":
    # Allow running without pytest for environments without it.
    import inspect
    mod = sys.modules[__name__]
    tests = [
        (name, obj) for name, obj in inspect.getmembers(mod, inspect.isfunction)
        if name.startswith("test_")
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            # Reset singletons manually
            import clew.activity_log as _al
            import clew.agent_identity as _ai
            _al._GLOBAL_LOG = None
            _ai._audit = None
            _ai._ROOT_IDENTITY = None
            fn()
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed.")
    sys.exit(1 if failed else 0)
