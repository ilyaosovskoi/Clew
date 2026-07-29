#!/usr/bin/env python3
"""
Goal M2 — Smart Cost-Aware Provider Routing — test suite.

Verifies:
  1. CostRouterConfig round-trips through to_dict / from_dict.
  2. load_config / save_config persist to ~/.clew/cost_router.json.
  3. CostRouter.set_cap updates a single complexity tier.
  4. CostRouter.route() returns a CostRouteDecision with the required fields.
  5. When budget pressure is CRITICAL, the router prefers free providers.
  6. When cost-router is disabled, route() returns the AutoRouter pick unchanged.
  7. Per-complexity USD caps filter out over-budget candidates.
  8. update_config patches multiple fields at once.
  9. Bridge-level integration: get_cost_router_config / set_cost_cap /
     cost_route work.

Run:
    python -m pytest clew/tests/test_m2_cost_router.py -v
or:
    python clew/tests/test_m2_cost_router.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Test isolation ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singletons_and_config(tmp_path, monkeypatch):
    """Point cost_router._CONFIG_PATH at a tmpdir and reset the singleton."""
    import clew.cost_router as _cr
    monkeypatch.setattr(_cr, "_CONFIG_PATH", tmp_path / "cost_router.json")
    _cr._router = None
    yield
    _cr._router = None


# ── 1. CostRouterConfig round-trip ──────────────────────────────────

def test_config_roundtrip():
    from clew.cost_router import CostRouterConfig
    cfg = CostRouterConfig()
    cfg.caps_usd["simple"] = 0.0123
    cfg.budget_pressure_high = 0.7
    d = cfg.to_dict()
    restored = CostRouterConfig.from_dict(d)
    assert restored.caps_usd["simple"] == 0.0123
    assert restored.budget_pressure_high == 0.7


def test_config_defaults():
    from clew.cost_router import CostRouterConfig, DEFAULT_CAPS_USD
    cfg = CostRouterConfig()
    assert cfg.enabled is True
    assert cfg.budget_pressure_high == 0.80
    assert cfg.budget_pressure_critical == 0.95
    assert cfg.error_rate_threshold == 0.30
    assert cfg.caps_usd == DEFAULT_CAPS_USD


# ── 2. load/save config ─────────────────────────────────────────────

def test_load_config_returns_defaults_when_absent():
    from clew.cost_router import load_config, DEFAULT_CAPS_USD
    cfg = load_config()
    assert cfg.caps_usd == DEFAULT_CAPS_USD


def test_save_then_load_config():
    from clew.cost_router import CostRouterConfig, save_config, load_config
    cfg = CostRouterConfig()
    cfg.caps_usd["expert"] = 2.5
    cfg.enabled = False
    save_config(cfg)
    loaded = load_config()
    assert loaded.caps_usd["expert"] == 2.5
    assert loaded.enabled is False


# ── 3. set_cap ──────────────────────────────────────────────────────

def test_set_cap_updates_single_tier():
    from clew.cost_router import get_cost_router
    cr = get_cost_router()
    cfg = cr.set_cap("simple", 0.0075)
    assert cfg.caps_usd["simple"] == 0.0075
    # Other tiers unchanged
    assert cfg.caps_usd["trivial"] > 0
    assert cfg.caps_usd["expert"] > 0


def test_set_cap_persists():
    from clew.cost_router import get_cost_router, load_config
    cr = get_cost_router()
    cr.set_cap("moderate", 0.123)
    loaded = load_config()
    assert loaded.caps_usd["moderate"] == 0.123


# ── 4. route() returns a CostRouteDecision ──────────────────────────

def test_route_returns_decision_with_required_fields():
    from clew.cost_router import get_cost_router
    cr = get_cost_router()
    decision = cr.route(
        prompt="Write a Python function to double a number",
        configured_providers={"ollama", "openai"},
    )
    d = decision.to_dict()
    assert "auto_router_pick" in d
    assert "final_pick" in d
    assert "factors" in d
    assert "budget_pressure" in d
    assert "complexity" in d
    assert "estimated_cost_usd" in d
    assert isinstance(d["factors"], list)


def test_route_with_no_configured_providers():
    """When no providers are configured, route() should still return a
    decision (possibly with empty final_pick)."""
    from clew.cost_router import get_cost_router
    cr = get_cost_router()
    decision = cr.route(prompt="hello", configured_providers=set())
    d = decision.to_dict()
    # Even with no providers, we get a decision dict back.
    assert "final_pick" in d
    assert "factors" in d


# ── 5. CRITICAL budget pressure prefers free providers ──────────────

def test_critical_budget_pressure_prefers_free_providers():
    from clew.cost_router import get_cost_router, CostRouterConfig
    cr = get_cost_router()
    # Force critical budget pressure by mocking _budget_pressure
    cr._config = CostRouterConfig(
        enabled=True,
        prefer_free_under_pressure=True,
    )
    with patch.object(cr, "_budget_pressure", return_value=(0.97, 0.50)):
        decision = cr.route(
            prompt="Write a complex algorithm to balance a red-black tree",
            configured_providers={"ollama", "openai", "anthropic"},
        )
    # The router should have switched to a free provider (ollama).
    final_pick = decision.final_pick
    assert final_pick.get("provider_id") in {"ollama", "lmstudio", "nvidia_nim",
                                              "cerebras", "sambanova"}
    # And the factors should mention "Budget pressure CRITICAL"
    assert any("CRITICAL" in f for f in decision.factors)


def test_critical_pressure_without_free_providers_keeps_original():
    from clew.cost_router import get_cost_router, CostRouterConfig
    cr = get_cost_router()
    cr._config = CostRouterConfig(
        enabled=True, prefer_free_under_pressure=True,
    )
    with patch.object(cr, "_budget_pressure", return_value=(0.97, 0.50)):
        decision = cr.route(
            prompt="Write code",
            configured_providers={"openai", "anthropic"},  # no free providers
        )
    # Without a free provider to switch to, the final pick stays as-is.
    assert decision.final_pick.get("provider_id") in {"openai", "anthropic", ""}


# ── 6. Disabled router returns AutoRouter pick unchanged ────────────

def test_disabled_router_returns_auto_pick_unchanged():
    from clew.cost_router import get_cost_router, CostRouterConfig
    cr = get_cost_router()
    cr._config = CostRouterConfig(enabled=False)
    decision = cr.route(
        prompt="Write hello world",
        configured_providers={"ollama"},
    )
    assert decision.enabled is False
    assert any("cost_router disabled" in f for f in decision.factors)


# ── 7. USD caps filter over-budget candidates ───────────────────────

def test_cap_filters_out_expensive_candidates():
    from clew.cost_router import get_cost_router, CostRouterConfig
    cr = get_cost_router()
    # Set a very low cap for every tier so even cheap candidates get filtered.
    cr._config = CostRouterConfig(
        enabled=True,
        caps_usd={
            "trivial": 0.00001,
            "simple": 0.00001,
            "moderate": 0.00001,
            "complex": 0.00001,
            "expert": 0.00001,
        },
    )
    with patch.object(cr, "_budget_pressure", return_value=(0.0, 100.0)):
        decision = cr.route(
            prompt="Write a Python script",
            configured_providers={"ollama"},  # free provider, est cost 0
        )
    # With a $0.00001 cap and only ollama (free, $0) available, ollama
    # should still pass the cap filter.
    assert decision.final_pick.get("provider_id") == "ollama"


# ── 8. update_config patches multiple fields ────────────────────────

def test_update_config_patches_multiple_fields():
    from clew.cost_router import get_cost_router
    cr = get_cost_router()
    cfg = cr.update_config(
        enabled=False,
        budget_pressure_high=0.5,
        budget_pressure_critical=0.9,
        error_rate_threshold=0.5,
    )
    assert cfg.enabled is False
    assert cfg.budget_pressure_high == 0.5
    assert cfg.budget_pressure_critical == 0.9
    assert cfg.error_rate_threshold == 0.5


def test_update_config_caps_usd_dict():
    from clew.cost_router import get_cost_router
    cr = get_cost_router()
    cfg = cr.update_config(caps_usd={"trivial": 0.001, "expert": 5.0})
    assert cfg.caps_usd["trivial"] == 0.001
    assert cfg.caps_usd["expert"] == 5.0


# ── 9. Bridge integration ───────────────────────────────────────────

def test_bridge_get_cost_router_config():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.get_cost_router_config()
    assert r.get("ok") is True
    assert "caps_usd" in r
    assert "enabled" in r


def test_bridge_set_cost_cap():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.set_cost_cap("simple", 0.0099)
    assert r.get("ok") is True
    assert r["caps_usd"]["simple"] == 0.0099


def test_bridge_cost_route():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.cost_route("Write a Python hello world", configured_providers={"ollama"})
    assert r.get("ok") is True
    assert "final_pick" in r
    assert "factors" in r
    assert "complexity" in r


def test_bridge_set_cost_router_config():
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.set_cost_router_config(enabled=False)
    assert r.get("ok") is True
    assert r["enabled"] is False


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
            # Reset singletons + tmp config
            import clew.cost_router as _cr
            _cr._router = None
            with tempfile.TemporaryDirectory() as td:
                with patch.object(_cr, "_CONFIG_PATH", Path(td) / "cost_router.json"):
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
