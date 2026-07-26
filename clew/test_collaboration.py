#!/usr/bin/env python3
"""Unit tests for collaboration modes (Issue #7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from clew.collaboration import (
    CodegenMode,
    CollaborationMode,
    CollaborationOrchestrator,
    CollaborationResult,
    ObserverMode,
    PairMode,
    ReviewerMode,
    _parse_review_verdict,
    get_mode,
)


# ── Test doubles ──────────────────────────────────────────────────────────


@dataclass
class FakeAgent:
    name: str
    goal: str
    role: str
    id: str = "fake-id"
    status: str = "idle"


@dataclass
class FakeSwarm:
    """Records spawn calls and assigns deterministic ids."""
    spawned: List[FakeAgent] = field(default_factory=list)

    def spawn(self, name: str, goal: str, role: str = "generalist") -> FakeAgent:
        agent = FakeAgent(name=name, goal=goal, role=role)
        self.spawned.append(agent)
        return agent


def make_orchestrator(scripted_responses: List[str]):
    """Build an orchestrator whose run_agent_fn returns scripted responses
    in order, regardless of which agent or task it's called with."""
    swarm = FakeSwarm()
    state = {"idx": 0}

    def run_agent_fn(agent, task):
        i = state["idx"]
        state["idx"] += 1
        if i >= len(scripted_responses):
            return ""
        return scripted_responses[i]

    return CollaborationOrchestrator(swarm, run_agent_fn), swarm


# ── Mode lookup ───────────────────────────────────────────────────────────


def test_get_mode_returns_implementation():
    assert isinstance(get_mode(CollaborationMode.REVIEWER), ReviewerMode)
    assert isinstance(get_mode(CollaborationMode.CODEGEN), CodegenMode)
    assert isinstance(get_mode(CollaborationMode.PAIR), PairMode)
    assert isinstance(get_mode(CollaborationMode.OBSERVER), ObserverMode)


def test_get_mode_unknown_raises():
    with pytest.raises(ValueError):
        get_mode("nonsense")  # type: ignore[arg-type]


# ── Reviewer mode ─────────────────────────────────────────────────────────


def test_reviewer_approves_on_first_round():
    # First response = implementer draft. Second response = reviewer APPROVE.
    orch, swarm = make_orchestrator([
        "draft v1",
        '{"verdict": "APPROVE", "feedback": "good"}',
    ])
    result = orch.run(CollaborationMode.REVIEWER, "do something")
    assert result.mode == CollaborationMode.REVIEWER
    assert result.output == "draft v1"
    assert result.metadata["verdict"] == "APPROVE"
    assert result.metadata["iterations"] == 1
    # Should have spawned exactly 2 agents.
    assert len(swarm.spawned) == 2
    assert swarm.spawned[0].role == "implementer"
    assert swarm.spawned[1].role == "reviewer"


def test_reviewer_rejects_immediately():
    orch, _ = make_orchestrator([
        "draft",
        '{"verdict": "REJECT", "feedback": "wrong"}',
    ])
    result = orch.run(CollaborationMode.REVIEWER, "do something")
    assert result.output == ""
    assert result.metadata["verdict"] == "REJECT"
    assert result.metadata["rejected"] is True
    assert result.metadata["feedback"] == "wrong"


def test_reviewer_modify_then_approve():
    orch, _ = make_orchestrator([
        "draft v1",                                       # impl round 0
        '{"verdict": "MODIFY", "feedback": "fix X"}',     # review round 0
        "draft v2",                                       # impl round 1
        '{"verdict": "APPROVE", "feedback": "good"}',     # review round 1
    ])
    result = orch.run(CollaborationMode.REVIEWER, "task")
    assert result.output == "draft v2"
    assert result.metadata["verdict"] == "APPROVE"
    assert result.metadata["iterations"] == 2


def test_reviewer_exhausts_iterations():
    """When max_iterations is reached without APPROVE, return EXHAUSTED."""
    orch, _ = make_orchestrator([
        "draft1", '{"verdict": "MODIFY", "feedback": "f1"}',
        "draft2", '{"verdict": "MODIFY", "feedback": "f2"}',
        "draft3", '{"verdict": "MODIFY", "feedback": "f3"}',
    ])
    # Override max_iterations via a custom impl instance.
    result = ReviewerMode(max_iterations=3).run(orch, "task")
    assert result.metadata["verdict"] == "EXHAUSTED"
    assert result.metadata["iterations"] == 3


# ── Codegen mode ──────────────────────────────────────────────────────────


def test_codegen_runs_planner_then_implementers():
    # Planner returns 2 sub-tasks. Then 2 implementer responses.
    orch, swarm = make_orchestrator([
        "sub-task A\nsub-task B",  # planner
        "impl A output",            # impl 0
        "impl B output",            # impl 1
    ])
    result = orch.run(CollaborationMode.CODEGEN, "build feature")
    assert "impl A output" in result.output
    assert "impl B output" in result.output
    assert result.metadata["sub_task_count"] == 2
    # 1 planner + 2 implementers = 3 spawned agents.
    assert len(swarm.spawned) == 3
    assert swarm.spawned[0].role == "planner"
    assert swarm.spawned[1].role == "implementer"
    assert swarm.spawned[2].role == "implementer"


def test_codegen_fallback_when_planner_returns_nothing():
    """If the planner returns no sub-tasks, fall back to the original task."""
    orch, _ = make_orchestrator([
        "",  # planner empty output
        "single impl output",
    ])
    result = orch.run(CollaborationMode.CODEGEN, "task")
    assert result.output == "single impl output"
    assert result.metadata["sub_task_count"] == 1


# ── Pair mode ─────────────────────────────────────────────────────────────


def test_pair_alternates_two_agents():
    orch, swarm = make_orchestrator([
        "A round 0",
        "B round 1",
        "A round 2",
        "B round 3",
    ])
    result = orch.run(CollaborationMode.PAIR, "task")
    assert result.output == "B round 3"
    assert result.metadata["rounds"] == 4
    # 2 unique agents (spawned once each).
    assert len(swarm.spawned) == 2
    assert {a.name for a in swarm.spawned} == {"pair-A", "pair-B"}


def test_pair_custom_rounds():
    orch, _ = make_orchestrator([
        "A0", "B0", "A1", "B1", "A2", "B2",
    ])
    result = PairMode(rounds=6).run(orch, "task")
    assert result.output == "B2"
    assert result.metadata["rounds"] == 6


# ── Observer mode ─────────────────────────────────────────────────────────


def test_observer_collects_warnings():
    orch, _ = make_orchestrator([
        "worker output",
        "warning: bad formatting",  # observer 0 — emits a warning
    ])
    result = orch.run(CollaborationMode.OBSERVER, "task")
    assert result.output == "worker output"
    assert len(result.metadata["warnings"]) == 1
    assert "bad formatting" in result.metadata["warnings"][0]


def test_observer_no_warnings_when_ok():
    orch, _ = make_orchestrator([
        "worker output",
        "OK",  # observer says everything is fine
    ])
    result = orch.run(CollaborationMode.OBSERVER, "task")
    assert result.metadata["warnings"] == []


def test_observer_multiple_observers():
    orch, _ = make_orchestrator([
        "worker output",
        "warning 1",
        "warning 2",
    ])
    result = ObserverMode(observer_count=2).run(orch, "task")
    assert len(result.metadata["warnings"]) == 2


# ── _parse_review_verdict ─────────────────────────────────────────────────


def test_parse_review_verdict_json_approve():
    v, f = _parse_review_verdict('{"verdict": "APPROVE", "feedback": "ok"}')
    assert v == "APPROVE"
    assert f == "ok"


def test_parse_review_verdict_json_reject():
    v, f = _parse_review_verdict('{"verdict": "REJECT", "feedback": "no"}')
    assert v == "REJECT"
    assert f == "no"


def test_parse_review_verdict_keyword_fallback():
    v, _ = _parse_review_verdict("I think this is APPROVE — looks great")
    assert v == "APPROVE"


def test_parse_review_verdict_default_modify():
    v, _ = _parse_review_verdict("the output is unclear")
    assert v == "MODIFY"


# ── Orchestrator error handling ───────────────────────────────────────────


def test_orchestrator_unknown_mode_raises():
    orch, _ = make_orchestrator([])
    with pytest.raises(ValueError):
        orch.run("not-a-mode", "task")  # type: ignore[arg-type]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
