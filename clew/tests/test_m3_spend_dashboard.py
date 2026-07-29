#!/usr/bin/env python3
"""
Goal M3 — Team Spend Dashboard — test suite.

Verifies:
  1. UserIdentity round-trips and respects share_email flag.
  2. load_identity creates a default if absent.
  3. set_team persists the team field.
  4. TeamBudget round-trips and supports multi-team storage.
  5. TeamSpendDashboard.report() aggregates by_user / by_provider / by_model / by_day.
  6. Dashboard correctly sums cost / tokens / request_count.
  7. team_budget_used_pct is computed against the current month only.
  8. add_source / list_sources manage the source list.
  9. export_report_json / export_report_csv produce valid output.
 10. Bridge-level integration: get_user_identity / set_user_team /
     get_team_budget / set_team_budget / get_team_spend_report work.

Run:
    python -m pytest clew/tests/test_m3_spend_dashboard.py -v
or:
    python clew/tests/test_m3_spend_dashboard.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Test isolation ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singletons_and_paths(tmp_path, monkeypatch):
    import clew.spend_dashboard as _sd
    # Point IDENTITY_PATH and TEAM_BUDGET_PATH at tmp_path
    monkeypatch.setattr(_sd, "IDENTITY_PATH", tmp_path / "identity.json")
    monkeypatch.setattr(_sd, "TEAM_BUDGET_PATH", tmp_path / "team_budget.json")
    _sd._dashboard = None
    yield
    _sd._dashboard = None


# ── Helpers ─────────────────────────────────────────────────────────

def _write_token_history(path: Path, entries):
    """Write a list of token_history entries to a .jsonl file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _make_entry(provider, model, tokens_in, tokens_out, cost, ts=None):
    return {
        "ts": ts or time.time(),
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "chat_id": "chat_1",
        "session_id": "sess_1",
        "cost": cost,
    }


# ── 1. UserIdentity ─────────────────────────────────────────────────

def test_user_identity_roundtrip():
    from clew.spend_dashboard import UserIdentity
    ident = UserIdentity(
        user_id="usr_test", name="alice", email="alice@example.com",
        team="platform", share_email=True,
    )
    d = ident.to_dict()
    assert d["user_id"] == "usr_test"
    assert d["name"] == "alice"
    assert d["email"] == "alice@example.com"
    assert d["team"] == "platform"
    restored = UserIdentity.from_dict(d)
    assert restored.user_id == ident.user_id
    assert restored.name == ident.name
    assert restored.team == ident.team


def test_user_identity_hides_email_when_not_opted_in():
    from clew.spend_dashboard import UserIdentity
    ident = UserIdentity(
        user_id="usr_x", name="bob", email="bob@example.com",
        team="ops", share_email=False,
    )
    d = ident.to_dict()
    assert "email" not in d  # privacy default


def test_user_identity_shows_email_when_opted_in():
    from clew.spend_dashboard import UserIdentity
    ident = UserIdentity(
        user_id="usr_y", name="carol", email="carol@example.com",
        team="ops", share_email=True,
    )
    d = ident.to_dict()
    assert d.get("email") == "carol@example.com"


# ── 2. load_identity creates a default if absent ────────────────────

def test_load_identity_creates_default_when_absent():
    from clew.spend_dashboard import load_identity, IDENTITY_PATH
    # IDENTITY_PATH is patched to a tmp path that doesn't exist yet
    ident = load_identity()
    assert ident.user_id.startswith("usr_")
    assert ident.team == "default"
    # File should have been written
    assert IDENTITY_PATH.exists()


def test_load_identity_reads_existing():
    from clew.spend_dashboard import load_identity, IDENTITY_PATH
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(json.dumps({
        "user_id": "usr_preexisting", "name": "dave",
        "team": "infra", "share_email": False,
    }))
    ident = load_identity()
    assert ident.user_id == "usr_preexisting"
    assert ident.team == "infra"


# ── 3. set_team persists ────────────────────────────────────────────

def test_set_team_persists():
    from clew.spend_dashboard import set_team, load_identity
    set_team("data-team")
    ident = load_identity()
    assert ident.team == "data-team"


# ── 4. TeamBudget ───────────────────────────────────────────────────

def test_team_budget_roundtrip():
    from clew.spend_dashboard import TeamBudget
    b = TeamBudget(team="platform", monthly_usd=500.0, alert_pct=90.0)
    d = b.to_dict()
    restored = TeamBudget.from_dict(d)
    assert restored.team == "platform"
    assert restored.monthly_usd == 500.0
    assert restored.alert_pct == 90.0


def test_team_budget_default_zero():
    from clew.spend_dashboard import TeamBudget
    b = TeamBudget(team="x")
    assert b.monthly_usd == 0.0


def test_save_and_load_team_budget():
    from clew.spend_dashboard import TeamBudget, save_team_budget, load_team_budget
    save_team_budget(TeamBudget(team="platform", monthly_usd=300.0, alert_pct=85.0))
    loaded = load_team_budget("platform")
    assert loaded.monthly_usd == 300.0
    assert loaded.alert_pct == 85.0


def test_multi_team_budget_storage():
    from clew.spend_dashboard import TeamBudget, save_team_budget, load_team_budget
    save_team_budget(TeamBudget(team="alpha", monthly_usd=100.0))
    save_team_budget(TeamBudget(team="beta", monthly_usd=200.0))
    assert load_team_budget("alpha").monthly_usd == 100.0
    assert load_team_budget("beta").monthly_usd == 200.0


# ── 5. Dashboard.report() ───────────────────────────────────────────

def test_report_aggregates_totals(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05),
        _make_entry("anthropic", "claude-3-5-sonnet", 2000, 1000, 0.08),
        _make_entry("openai", "gpt-4o", 500, 250, 0.025),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="usr_r", name="r", team="test"),
        team_budget=TeamBudget(team="test", monthly_usd=0.0),
    )
    report = dash.report(days=30)
    assert report.total_cost_usd == round(0.05 + 0.08 + 0.025, 4)
    assert report.total_tokens_in == 3500
    assert report.total_tokens_out == 1750
    assert report.total_request_count == 3
    assert report.sources_scanned == 1
    assert report.entries_processed == 3


def test_report_by_user_grouping(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    _write_token_history(history_path, [
        {**_make_entry("openai", "gpt-4o", 1000, 500, 0.05),
         "user_id": "usr_a", "user_name": "Alice"},
        {**_make_entry("openai", "gpt-4o", 2000, 1000, 0.10),
         "user_id": "usr_b", "user_name": "Bob"},
        {**_make_entry("openai", "gpt-4o", 500, 250, 0.025),
         "user_id": "usr_a", "user_name": "Alice"},
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="usr_root", name="root", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    report = dash.report(days=30)
    by_user = {u.user_id: u for u in report.by_user}
    assert "usr_a" in by_user
    assert "usr_b" in by_user
    assert abs(by_user["usr_a"].cost_usd - 0.075) < 1e-6
    assert by_user["usr_a"].request_count == 2
    assert by_user["usr_b"].request_count == 1
    # Top consumer is Bob (0.10 > 0.075)
    assert report.top_consumer_user_id == "usr_b"


def test_report_by_provider(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05),
        _make_entry("anthropic", "claude-3-5-sonnet", 2000, 1000, 0.10),
        _make_entry("openai", "gpt-4o", 500, 250, 0.025),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    report = dash.report(days=30)
    by_provider = {p.provider: p for p in report.by_provider}
    assert "openai" in by_provider
    assert "anthropic" in by_provider
    assert by_provider["openai"].request_count == 2
    assert by_provider["anthropic"].request_count == 1
    # Sorted by cost desc — anthropic (0.10) > openai (0.075)
    assert report.by_provider[0].provider == "anthropic"


def test_report_by_model(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05),
        _make_entry("openai", "gpt-4o-mini", 500, 250, 0.005),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    report = dash.report(days=30)
    by_model = {m.model: m for m in report.by_model}
    assert "gpt-4o" in by_model
    assert "gpt-4o-mini" in by_model


def test_report_by_day_filters_old_entries(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    # One recent entry, one entry from 60 days ago
    now = time.time()
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05, ts=now),
        _make_entry("openai", "gpt-4o", 2000, 1000, 0.10, ts=now - 60 * 86400),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    report = dash.report(days=30)
    # Totals include ALL entries (per design — totals are not filtered by days)
    assert report.total_request_count == 2
    # by_day only includes entries from the last 30 days
    assert sum(d.request_count for d in report.by_day) == 1


def test_report_handles_directory_source(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    sources_dir = tmp_path / "history_dir"
    sources_dir.mkdir()
    _write_token_history(sources_dir / "alice.jsonl", [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05),
    ])
    _write_token_history(sources_dir / "bob.jsonl", [
        _make_entry("anthropic", "claude-3-5-sonnet", 2000, 1000, 0.08),
    ])
    dash = TeamSpendDashboard(
        sources=[sources_dir],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    report = dash.report(days=30)
    assert report.total_request_count == 2
    assert report.sources_scanned == 2


def test_report_handles_missing_source_gracefully(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    dash = TeamSpendDashboard(
        sources=[tmp_path / "does_not_exist.jsonl"],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    report = dash.report(days=30)
    assert report.total_request_count == 0
    assert report.entries_processed == 0


# ── 7. team_budget_used_pct against current month ───────────────────

def test_team_budget_used_pct_against_current_month(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    import datetime as _dt
    history_path = tmp_path / "tokens.jsonl"
    now = time.time()
    # Entry from 60 days ago — should NOT count toward this month
    old_ts = now - 60 * 86400
    # Entry from today — SHOULD count
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05, ts=old_ts),
        _make_entry("openai", "gpt-4o", 1000, 500, 0.04, ts=now),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t", monthly_usd=10.0),  # $10/mo
    )
    report = dash.report(days=30)
    # This month's spend = $0.04; budget = $10 → 0.4%
    assert report.team_budget_used_pct == 0.4


# ── 8. add_source / list_sources ────────────────────────────────────

def test_add_and_list_sources(tmp_path):
    from clew.spend_dashboard import TeamSpendDashboard, UserIdentity, TeamBudget
    dash = TeamSpendDashboard(
        sources=[],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    assert dash.list_sources() == []
    p1 = tmp_path / "h1.jsonl"
    p1.write_text("")
    p2 = tmp_path / "h2.jsonl"
    p2.write_text("")
    dash.add_source(p1)
    dash.add_source(p2)
    sources = dash.list_sources()
    assert len(sources) == 2
    assert str(p1) in sources
    assert str(p2) in sources


# ── 9. Export ───────────────────────────────────────────────────────

def test_export_report_json_is_valid(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    json_str = dash.export_report_json(days=30)
    data = json.loads(json_str)
    assert data["team"] == "t"
    assert data["total_request_count"] == 1
    assert "by_user" in data
    assert "by_provider" in data


def test_export_report_csv_has_sections(tmp_path):
    from clew.spend_dashboard import (
        TeamSpendDashboard, UserIdentity, TeamBudget,
    )
    history_path = tmp_path / "tokens.jsonl"
    _write_token_history(history_path, [
        _make_entry("openai", "gpt-4o", 1000, 500, 0.05),
    ])
    dash = TeamSpendDashboard(
        sources=[history_path],
        identity=UserIdentity(user_id="u", name="n", team="t"),
        team_budget=TeamBudget(team="t"),
    )
    csv_str = dash.export_report_csv(days=30)
    assert "# Team Spend Report" in csv_str
    assert "# By User" in csv_str
    assert "# By Provider" in csv_str
    assert "# By Model" in csv_str
    assert "# By Day" in csv_str


# ── 10. Bridge integration ──────────────────────────────────────────

def test_bridge_get_user_identity():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.get_user_identity()
    assert r.get("ok") is True
    assert r.get("user_id", "").startswith("usr_")


def test_bridge_set_user_team():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.set_user_team("qa-team")
    assert r.get("ok") is True
    assert r["team"] == "qa-team"


def test_bridge_get_team_budget():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.get_team_budget("default")
    assert r.get("ok") is True
    assert "monthly_usd" in r


def test_bridge_set_team_budget():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.set_team_budget(250.0, team="default", alert_pct=85.0)
    assert r.get("ok") is True
    assert r["monthly_usd"] == 250.0
    assert r["alert_pct"] == 85.0


def test_bridge_get_team_spend_report():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.get_team_spend_report(days=30)
    assert r.get("ok") is True
    assert "total_cost_usd" in r
    assert "by_user" in r
    assert "by_provider" in r


def test_bridge_list_spend_sources():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.list_spend_sources()
    assert r.get("ok") is True
    assert isinstance(r.get("sources"), list)


# ── CLI entrypoint ──────────────────────────────────────────────────

if __name__ == "__main__":
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
            with tempfile.TemporaryDirectory() as td:
                tmp_path = Path(td)
                import clew.spend_dashboard as _sd
                with patch.object(_sd, "IDENTITY_PATH", tmp_path / "identity.json"), \
                     patch.object(_sd, "TEAM_BUDGET_PATH", tmp_path / "team_budget.json"):
                    _sd._dashboard = None
                    sig = inspect.signature(fn)
                    kwargs = {}
                    if "tmp_path" in sig.parameters:
                        kwargs["tmp_path"] = tmp_path
                    fn(**kwargs)
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed.")
    sys.exit(1 if failed else 0)
