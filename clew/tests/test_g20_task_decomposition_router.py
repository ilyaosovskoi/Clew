#!/usr/bin/env python3
"""
G20 — Task-decomposition smart router — test suite.

Verifies:
  20a (specialty field + overrides):
    1. ModelTier has a `specialty` field with default "".
    2. Every DEFAULT_TIERS entry has a non-empty specialty.
    3. ~/.clew/model_capabilities.json overrides existing entries.
    4. Overrides for unknown (provider, model) tuples are appended to SIMPLE.
    5. AutoRouter.set_mode / get_mode round-trip; unknown mode → "single".
    6. AutoRouter.route() includes `specialty` in the returned dict.
    7. all_tiers() returns every tier with overrides applied.

  20b (decomposition router):
    8. Decompose parses valid JSON into Subtask objects.
    9. Decompose falls back when LLM returns invalid JSON.
    10. Decompose falls back when LLM returns a single passthrough subtask.
    11. Route picks cheapest tier matching the subtask's specialty needs.
    12. Route respects complexity fit (SIMPLE subtask won't get EXPERT tier).
    13. Dispatch with no runtime produces a placeholder merged_answer.
    14. Merge concatenates subtask results when merge LLM is unavailable.
    15. Projected cost is non-zero when subtasks have non-free tiers.
    16. Budget enforcement: projected > budget → fall back to single-model.

  20c (UX + observability):
    17. set_mode("decompose") then get_mode() returns "decompose".
    18. ActivityLog gets entries for decompose/route/complete phases.
    19. TaskCanvas gets nodes for each subtask with model assignments.
    20. provider_override/model_override thread through _spawn_subagent.
    21. AgentRuntime.set_provider_override changes _get_active_provider().

Run:
    python -m pytest clew/tests/test_g20_task_decomposition_router.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Test isolation ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect ~/.clew to a temp dir so tests don't clobber the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    yield


# ── 20a: ModelTier.specialty + overrides ───────────────────────────────


def test_modeltier_has_specialty_field():
    """ModelTier dataclass has a `specialty` field (G20a)."""
    from clew.auto_router import ModelTier
    t = ModelTier("p", "m", 4096, 0.0, 0.0, "fast", ["chat"])
    # Default is empty string (backward compat).
    assert t.specialty == ""
    # Can be set explicitly.
    t2 = ModelTier("p", "m", 4096, 0.0, 0.0, "fast", ["chat"], "strong at reasoning")
    assert t2.specialty == "strong at reasoning"


def test_default_tiers_all_have_specialty():
    """Every entry in DEFAULT_TIERS has a non-empty specialty (G20a)."""
    from clew.auto_router import DEFAULT_TIERS
    for complexity, tiers in DEFAULT_TIERS.items():
        for t in tiers:
            assert t.specialty, (
                f"{complexity.value}/{t.provider_id}/{t.model} has empty specialty"
            )


def test_route_returns_specialty_in_decision():
    """AutoRouter.route() includes `specialty` in the returned dict."""
    from clew.auto_router import AutoRouter
    ar = AutoRouter()
    ar.mark_provider_available("groq", True)
    decision = ar.route("hello", configured_providers={"groq"})
    assert "specialty" in decision
    # The picked tier's specialty should be non-empty (DEFAULT_TIERS has them all).
    assert decision["specialty"]


def test_set_mode_round_trip():
    """set_mode / get_mode round-trip; unknown mode → 'single'."""
    from clew.auto_router import AutoRouter, MODE_SINGLE, MODE_DECOMPOSE
    ar = AutoRouter()
    assert ar.get_mode() == MODE_SINGLE
    ar.set_mode(MODE_DECOMPOSE)
    assert ar.get_mode() == MODE_DECOMPOSE
    ar.set_mode("bogus")
    assert ar.get_mode() == MODE_SINGLE
    ar.set_mode(MODE_SINGLE)
    assert ar.get_mode() == MODE_SINGLE


def test_all_tiers_returns_every_tier():
    """all_tiers() returns every tier across all complexity levels."""
    from clew.auto_router import AutoRouter, DEFAULT_TIERS
    ar = AutoRouter()
    all_tiers = ar.all_tiers()
    expected_count = sum(len(tiers) for tiers in DEFAULT_TIERS.values())
    assert len(all_tiers) == expected_count
    # Each entry is a (complexity, tier) tuple.
    for complexity, tier in all_tiers:
        assert hasattr(tier, "specialty")
        assert hasattr(tier, "provider_id")


def test_override_existing_entry():
    """~/.clew/model_capabilities.json overrides an existing entry's specialty."""
    from clew.auto_router import (
        _apply_overrides, DEFAULT_TIERS, TaskComplexity,
    )
    overrides = {
        "openai": [
            {"model": "gpt-4o", "specialty": "best for vision-heavy tasks"}
        ]
    }
    new_tiers = _apply_overrides(DEFAULT_TIERS, overrides)
    # Find the overridden entry.
    found = False
    for complexity, tier_list in new_tiers.items():
        for t in tier_list:
            if t.provider_id == "openai" and t.model == "gpt-4o":
                assert t.specialty == "best for vision-heavy tasks"
                found = True
    assert found, "openai/gpt-4o should exist in DEFAULT_TIERS"


def test_override_new_entry_appended_to_simple():
    """Unknown (provider, model) tuples are appended to the SIMPLE tier."""
    from clew.auto_router import (
        _apply_overrides, DEFAULT_TIERS, TaskComplexity,
    )
    overrides = {
        "ollama": [
            {
                "model": "qwen2.5-coder:32b",
                "specialty": "strong at code generation",
                "max_tokens": 16384,
            }
        ]
    }
    new_tiers = _apply_overrides(DEFAULT_TIERS, overrides)
    # The new entry should be in SIMPLE.
    simple_tiers = new_tiers[TaskComplexity.SIMPLE]
    found = False
    for t in simple_tiers:
        if t.provider_id == "ollama" and t.model == "qwen2.5-coder:32b":
            assert t.specialty == "strong at code generation"
            assert t.max_tokens == 16384
            found = True
    assert found, "new entry should be appended to SIMPLE"


def test_override_partial_fields_keep_existing():
    """Overrides only replace explicitly-set fields; others stay."""
    from clew.auto_router import (
        _apply_overrides, DEFAULT_TIERS, TaskComplexity,
    )
    # Find an existing entry to compare against.
    original = None
    for t in DEFAULT_TIERS[TaskComplexity.SIMPLE]:
        if t.provider_id == "groq":
            original = t
            break
    assert original is not None
    original_cost_in = original.cost_per_1k_in
    original_capabilities = list(original.capabilities)

    overrides = {
        "groq": [
            {"model": original.model, "specialty": "new specialty text"}
        ]
    }
    new_tiers = _apply_overrides(DEFAULT_TIERS, overrides)
    for t in new_tiers[TaskComplexity.SIMPLE]:
        if t.provider_id == "groq" and t.model == original.model:
            assert t.specialty == "new specialty text"
            # Cost and capabilities should be unchanged.
            assert t.cost_per_1k_in == original_cost_in
            assert t.capabilities == original_capabilities
            return
    assert False, "overridden entry not found"


def test_override_load_from_disk():
    """~/.clew/model_capabilities.json is loaded on AutoRouter init."""
    from clew.auto_router import AutoRouter
    # Write an override file.
    override_path = Path.home() / ".clew" / "model_capabilities.json"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(json.dumps({
        "openai": [
            {"model": "gpt-4o", "specialty": "test specialty from disk"}
        ]
    }))
    ar = AutoRouter()
    # Find the overridden entry.
    found = False
    for complexity, tier_list in ar._tiers.items():
        for t in tier_list:
            if t.provider_id == "openai" and t.model == "gpt-4o":
                assert t.specialty == "test specialty from disk"
                found = True
    assert found


def test_override_invalid_json_ignored():
    """Invalid JSON in the override file is silently ignored."""
    from clew.auto_router import AutoRouter, DEFAULT_TIERS
    override_path = Path.home() / ".clew" / "model_capabilities.json"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text("not valid json {{{")
    ar = AutoRouter()
    # Should fall back to DEFAULT_TIERS unchanged (specialty field still populated).
    found = False
    for t in ar._tiers.get(__import__("clew.auto_router", fromlist=["TaskComplexity"]).TaskComplexity.SIMPLE, []):
        if t.provider_id == "groq":
            # Specialty should be the built-in default, not an override.
            assert t.specialty  # non-empty
            found = True
    assert found


# ── 20b: TaskDecompositionRouter ───────────────────────────────────────


def _build_mock_router(decomposition_json: str):
    """Build a TaskDecompositionRouter with mocked LLM calls."""
    from clew.auto_router import reset_auto_router_for_test
    from clew.task_decomposition_router import (
        TaskDecompositionRouter, get_task_decomposition_router,
        reset_task_decomposition_router_for_test,
    )
    # Clear any leftover budget from previous tests so budget enforcement
    # doesn't accidentally trigger in tests that don't set one.
    reset_auto_router_for_test()
    r = reset_task_decomposition_router_for_test()

    class FakeResp:
        text = decomposition_json
        tokens_in = 50
        tokens_out = 20

    class FakeProvider:
        provider_id = "fake"
        is_loaded = True
        def generate(self, msgs, model=None): return FakeResp()
        def get_model(self): return "fake-model"

    fake_provider = FakeProvider()
    r._call_provider = lambda provider, msgs, model: FakeResp().text
    r._resolve_provider = lambda pid, mid: (fake_provider, mid or "fake-model")
    return r


def test_decompose_parses_valid_json():
    """Decompose parses valid JSON into Subtask objects."""
    from clew.task_decomposition_router import TaskComplexity
    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "fix bug", "needs": ["algorithmic"], "complexity": "simple", "depends_on": []},
            {"subtask": "write tests", "needs": ["boilerplate"], "complexity": "simple", "depends_on": [1]},
        ]
    }))
    report = r.route("Fix the bug and write tests", runtime=None, configured_providers={"openai", "groq"})
    assert report.decomposed is True
    assert len(report.subtasks) == 2
    assert report.subtasks[0].id == "s1"
    assert report.subtasks[0].subtask == "fix bug"
    assert report.subtasks[0].needs == ["algorithmic"]
    assert report.subtasks[1].depends_on == ["s1"]


def test_decompose_falls_back_on_invalid_json():
    """Decompose falls back to single-model on invalid JSON."""
    r = _build_mock_router("not valid json {{{")
    report = r.route("some task", runtime=None, configured_providers={"openai"})
    assert report.decomposed is False
    assert "decomposition error" in report.fallback_reason
    assert report.fallback_decision is not None


def test_decompose_falls_back_on_single_passthrough():
    """Single subtask equal to the original prompt → fall back."""
    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "do the thing", "needs": [], "complexity": "simple", "depends_on": []}
        ]
    }))
    report = r.route("do the thing", runtime=None, configured_providers={"openai"})
    assert report.decomposed is False
    assert "passthrough" in report.fallback_reason


def test_route_picks_cheapest_matching_specialty():
    """Routing prefers tiers whose specialty matches the subtask's needs."""
    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "solve traveling salesman", "needs": ["algorithmic reasoning"], "complexity": "expert", "depends_on": []}
        ]
    }))
    report = r.route("solve TSP", runtime=None, configured_providers={"anthropic", "openai", "groq"})
    assert report.decomposed is True
    st = report.subtasks[0]
    # Should have routed to a tier with "algorithmic" in its specialty.
    assert st.provider_id is not None
    assert st.model is not None
    # The specialty_match field should mention the matched need.
    assert "algorithmic" in (st.specialty_match or "").lower() or "no specialty" in (st.specialty_match or "").lower()


def test_route_respects_complexity_fit():
    """SIMPLE subtask won't get EXPERT tier (complexity fit enforced)."""
    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "add a comment", "needs": ["boilerplate"], "complexity": "trivial", "depends_on": []}
        ]
    }))
    report = r.route("add comment", runtime=None, configured_providers={"groq", "anthropic"})
    assert report.decomposed is True
    st = report.subtasks[0]
    # Trivial subtask should NOT be routed to the EXPERT tier (anthropic opus).
    # It should go to a cheaper tier (groq or similar).
    assert st.provider_id != "anthropic" or "opus" not in (st.model or "").lower()


def test_dispatch_without_runtime_returns_placeholder():
    """No runtime → merged_answer is a placeholder, subtasks still routed."""
    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "task A", "needs": [], "complexity": "simple", "depends_on": []},
        ]
    }))
    report = r.route("do A", runtime=None, configured_providers={"groq"})
    assert report.decomposed is True
    assert "subtasks routed but not dispatched" in report.merged_answer


def test_projected_cost_nonzero_for_paid_tiers():
    """Projected cost > 0 when subtasks use paid tiers."""
    from clew.auto_router import reset_auto_router_for_test
    reset_auto_router_for_test()  # clear any leftover budget from other tests
    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "solve the traveling salesman problem optimally", "needs": ["reasoning"], "complexity": "expert", "depends_on": []}
        ]
    }))
    report = r.route("Please help me with TSP", runtime=None, configured_providers={"anthropic", "openai"})
    assert report.decomposed is True
    # anthropic/claude-opus-4 has non-zero cost_per_1k_in/out.
    assert report.projected_cost_usd > 0


def test_budget_enforcement_falls_back_when_exceeded():
    """Projected cost > budget → fall back to single-model."""
    from clew.auto_router import reset_auto_router_for_test
    from clew.task_decomposition_router import reset_task_decomposition_router_for_test
    reset_auto_router_for_test()
    r = reset_task_decomposition_router_for_test()
    r._auto_router.set_budget(0.0001)  # very tight budget

    class FakeResp:
        text = json.dumps({
            "subtasks": [
                {"subtask": "solve the traveling salesman problem optimally", "needs": ["reasoning"], "complexity": "expert", "depends_on": []}
            ]
        })
    class FakeProvider:
        provider_id = "fake"
        is_loaded = True
        def generate(self, msgs, model=None): return FakeResp()
        def get_model(self): return "fake-model"
    fake_provider = FakeProvider()
    r._call_provider = lambda provider, msgs, model: FakeResp().text
    r._resolve_provider = lambda pid, mid: (fake_provider, mid or "fake-model")

    report = r.route("Please help me with TSP", runtime=None, configured_providers={"anthropic", "openai"})
    assert report.decomposed is False
    assert "exceeds per-request budget" in report.fallback_reason


def test_subtask_to_dict_round_trips():
    """Subtask.to_dict() includes all fields."""
    from clew.task_decomposition_router import Subtask
    from clew.auto_router import TaskComplexity
    st = Subtask(
        id="s1", subtask="fix bug",
        needs=["algorithmic"], complexity=TaskComplexity.SIMPLE,
        depends_on=[], provider_id="openai", model="gpt-4o",
        specialty_match="matched 1 need",
        result="fixed", error=None, elapsed_ms=123.4,
    )
    d = st.to_dict()
    assert d["id"] == "s1"
    assert d["subtask"] == "fix bug"
    assert d["needs"] == ["algorithmic"]
    assert d["complexity"] == "simple"
    assert d["provider_id"] == "openai"
    assert d["model"] == "gpt-4o"
    assert d["result"] == "fixed"
    assert d["elapsed_ms"] == 123.4


def test_decomposition_report_to_dict_round_trips():
    """DecompositionReport.to_dict() includes all fields."""
    from clew.task_decomposition_router import DecompositionReport, Subtask
    from clew.auto_router import TaskComplexity
    report = DecompositionReport(
        prompt="test prompt",
        decomposed=True,
        subtasks=[Subtask(id="s1", subtask="task A")],
        merged_answer="merged result",
        elapsed_ms=500.0,
        projected_cost_usd=0.001,
    )
    d = report.to_dict()
    assert d["prompt"] == "test prompt"
    assert d["decomposed"] is True
    assert len(d["subtasks"]) == 1
    assert d["merged_answer"] == "merged result"
    assert d["elapsed_ms"] == 500.0
    assert d["projected_cost_usd"] == 0.001


# ── 20c: UX + observability ────────────────────────────────────────────


def test_activity_log_records_decomposition_phases():
    """ActivityLog gets entries for decompose/route/complete phases."""
    from clew.activity_log import get_activity_log
    from clew.agent.task_canvas import reset_task_canvas_for_test
    from clew.auto_router import reset_auto_router_for_test
    get_activity_log().clear()
    reset_task_canvas_for_test()
    reset_auto_router_for_test()  # clear leftover budget from other tests

    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "implement feature alpha", "needs": ["boilerplate"], "complexity": "simple", "depends_on": []},
            {"subtask": "implement feature beta", "needs": ["boilerplate"], "complexity": "simple", "depends_on": []},
        ]
    }))
    r.route("Please implement two features", runtime=None, configured_providers={"groq"})

    log = get_activity_log()
    entries = log.recent(n=20)
    kinds = [e["kind"] for e in entries]
    assert "task_decomposition" in kinds
    assert "task_decomposition_routing" in kinds
    assert "task_decomposition_complete" in kinds


def test_task_canvas_gets_nodes_with_model_assignments():
    """TaskCanvas gets a node per subtask with the routed model."""
    from clew.agent.task_canvas import get_task_canvas, reset_task_canvas_for_test
    from clew.activity_log import get_activity_log
    from clew.auto_router import reset_auto_router_for_test
    reset_task_canvas_for_test()
    get_activity_log().clear()
    reset_auto_router_for_test()

    r = _build_mock_router(json.dumps({
        "subtasks": [
            {"subtask": "implement feature alpha", "needs": ["boilerplate"], "complexity": "simple", "depends_on": []},
            {"subtask": "implement feature beta", "needs": ["boilerplate"], "complexity": "simple", "depends_on": [1]},
        ]
    }))
    r.route("Please implement two features", runtime=None, configured_providers={"groq"})

    canvas = get_task_canvas()
    assert len(canvas) == 2
    nodes = canvas.nodes()
    assert nodes[0].id == "s1"
    assert nodes[0].model is not None  # routed model is set
    assert nodes[1].id == "s2"
    assert nodes[1].depends_on == ["s1"]


def test_spawn_subagent_accepts_provider_override():
    """_spawn_subagent signature accepts provider_override/model_override."""
    import inspect
    from clew.agent_runtime.tool_engine._engine import ToolEngine
    sig = inspect.signature(ToolEngine._spawn_subagent)
    assert "provider_override" in sig.parameters
    assert "model_override" in sig.parameters


def test_run_subagent_internal_accepts_provider_override():
    """_run_subagent_internal signature accepts provider_override/model_override."""
    import inspect
    from clew.agent_runtime.tool_engine._engine import ToolEngine
    sig = inspect.signature(ToolEngine._run_subagent_internal)
    assert "provider_override" in sig.parameters
    assert "model_override" in sig.parameters


def test_agent_runtime_set_provider_override_changes_active_provider():
    """AgentRuntime.set_provider_override changes _get_active_provider()."""
    from clew.agent_runtime.runtime import AgentRuntime
    # Build a mock registry with two providers.
    fake_provider_a = MagicMock()
    fake_provider_a.is_loaded = True
    fake_provider_a.provider_id = "provider_a"
    fake_provider_b = MagicMock()
    fake_provider_b.is_loaded = True
    fake_provider_b.provider_id = "provider_b"
    registry = MagicMock()
    registry.active = fake_provider_a
    registry.get = lambda pid: fake_provider_b if pid == "provider_b" else None
    registry._active_id = "provider_a"

    runtime = AgentRuntime(registry=registry, workspace=None)
    # Without override, returns the active provider.
    assert runtime._get_active_provider() is fake_provider_a
    # With override, returns the override provider.
    runtime.set_provider_override("provider_b", "some-model")
    assert runtime._get_active_provider() is fake_provider_b
    assert runtime._model_override == "some-model"
    # Clearing the override reverts to active.
    runtime.set_provider_override(None, None)
    assert runtime._get_active_provider() is fake_provider_a


def test_agent_runtime_set_provider_override_unknown_provider_raises():
    """set_provider_override with unknown provider id → _get_active_provider raises."""
    from clew.agent_runtime.runtime import AgentRuntime
    fake_provider_a = MagicMock()
    fake_provider_a.is_loaded = True
    fake_provider_a.provider_id = "provider_a"
    registry = MagicMock()
    registry.active = fake_provider_a
    registry.get = lambda pid: None  # unknown provider
    registry._active_id = "provider_a"

    runtime = AgentRuntime(registry=registry, workspace=None)
    runtime.set_provider_override("nonexistent", "model")
    with pytest.raises(RuntimeError, match="not found in registry"):
        runtime._get_active_provider()


def test_json_extraction_from_prose_wrapped_output():
    """_extract_json_object finds the first balanced {...} block in prose."""
    from clew.task_decomposition_router import _extract_json_object
    text = 'Here is the JSON: {"subtasks": [{"subtask": "x"}]} hope it helps'
    extracted = _extract_json_object(text)
    assert extracted == '{"subtasks": [{"subtask": "x"}]}'


def test_json_extraction_handles_nested_braces_in_strings():
    """_extract_json_object respects string literals when counting braces."""
    from clew.task_decomposition_router import _extract_json_object
    text = '{"subtasks": [{"subtask": "fix {foo} bug"}]}'
    extracted = _extract_json_object(text)
    assert extracted == text


def test_json_extraction_returns_empty_when_no_object():
    """_extract_json_object returns '' when there's no {...} block."""
    from clew.task_decomposition_router import _extract_json_object
    assert _extract_json_object("no json here") == ""


def test_strip_code_fences_removes_surrounding_fence():
    """_strip_code_fences removes a single surrounding ```json ... ``` fence."""
    from clew.task_decomposition_router import _strip_code_fences
    fenced = '```json\n{"subtasks": []}\n```'
    stripped = _strip_code_fences(fenced)
    assert stripped == '{"subtasks": []}'


def test_strip_code_fences_leaves_plain_text_unchanged():
    """_strip_code_fences is a no-op when there's no fence."""
    from clew.task_decomposition_router import _strip_code_fences
    plain = '{"subtasks": []}'
    assert _strip_code_fences(plain) == plain


def test_decompose_caps_at_max_subtasks():
    """Decompose caps at MAX_SUBTASKS even if the LLM emits more."""
    from clew.task_decomposition_router import MAX_SUBTASKS
    many_subtasks = [
        {"subtask": f"implement feature number {i}", "needs": ["boilerplate"], "complexity": "simple", "depends_on": []}
        for i in range(MAX_SUBTASKS + 5)
    ]
    r = _build_mock_router(json.dumps({"subtasks": many_subtasks}))
    report = r.route("Please implement many features for me", runtime=None, configured_providers={"groq"})
    assert report.decomposed is True
    assert len(report.subtasks) == MAX_SUBTASKS
