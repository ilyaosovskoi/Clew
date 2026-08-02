#!/usr/bin/env python3
"""
G19 — Layered memory upgrade — test suite.

Verifies:
  19a (task canvas):
    1. TaskCanvas add_node / update_status / node_ids / to_compact_text
    2. depends_on validation rejects unknown parents.
    3. to_compact_text caps visible nodes with "+N more" summary.
    4. to_fragment() wraps in <context_fragment> with stable_id.
    5. Singleton get_task_canvas / reset_task_canvas_for_test isolation.
    6. CanvasNode.to_dict() round-trips all fields.

  19b (persona memory):
    7. PersonaMemory get/set/reset with file persistence.
    8. Hard cap enforcement (writes > SOFT_CAP get trimmed).
    9. to_fragment() wraps content with stable_id, returns None when empty.
    10. Maintenance LLM call updates persona (mocked provider).
    11. Maintenance call is best-effort (failure leaves existing persona).
    12. PersonaDigest.to_prompt_text() renders all fields.

Run:
    python -m pytest clew/tests/test_g19_layered_memory.py -v
"""

from __future__ import annotations

import os
import tempfile
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


# ── 19a: TaskCanvas ────────────────────────────────────────────────────


def test_canvas_add_node_basic():
    """add_node stores a node and node_ids() returns it."""
    from clew.agent.task_canvas import TaskCanvas, STATUS_PENDING
    c = TaskCanvas()
    c.add_node("s1", "first subtask")
    assert c.node_ids() == ["s1"]
    n = c.get("s1")
    assert n is not None
    assert n.label == "first subtask"
    assert n.status == STATUS_PENDING
    assert n.depends_on == []
    assert n.model is None
    assert n.note is None


def test_canvas_add_node_with_depends_on():
    """depends_on links must reference existing parents."""
    from clew.agent.task_canvas import TaskCanvas, STATUS_DONE
    c = TaskCanvas()
    c.add_node("s1", "first", status=STATUS_DONE)
    c.add_node("s2", "second", depends_on=["s1"])
    n = c.get("s2")
    assert n.depends_on == ["s1"]


def test_canvas_add_node_unknown_parent_rejected():
    """depends_on to a non-existent node id raises TaskCanvasError."""
    from clew.agent.task_canvas import TaskCanvas, TaskCanvasError
    c = TaskCanvas()
    with pytest.raises(TaskCanvasError):
        c.add_node("s2", "orphan", depends_on=["nonexistent"])


def test_canvas_add_node_duplicate_id_rejected():
    """Adding the same id twice raises TaskCanvasError."""
    from clew.agent.task_canvas import TaskCanvas, TaskCanvasError
    c = TaskCanvas()
    c.add_node("s1", "first")
    with pytest.raises(TaskCanvasError):
        c.add_node("s1", "duplicate")


def test_canvas_add_node_invalid_status_rejected():
    """Invalid status string is rejected."""
    from clew.agent.task_canvas import TaskCanvas, TaskCanvasError
    c = TaskCanvas()
    with pytest.raises(TaskCanvasError):
        c.add_node("s1", "first", status="bogus")


def test_canvas_update_status_changes_status():
    """update_status transitions a node's status."""
    from clew.agent.task_canvas import TaskCanvas, STATUS_RUNNING, STATUS_DONE
    c = TaskCanvas()
    c.add_node("s1", "first")
    c.update_status("s1", STATUS_RUNNING)
    assert c.get("s1").status == STATUS_RUNNING
    c.update_status("s1", STATUS_DONE, note="finished cleanly", model="openai/gpt-4o")
    n = c.get("s1")
    assert n.status == STATUS_DONE
    assert n.note == "finished cleanly"
    assert n.model == "openai/gpt-4o"


def test_canvas_update_status_unknown_node_rejected():
    """update_status on an unknown id raises TaskCanvasError."""
    from clew.agent.task_canvas import TaskCanvas, TaskCanvasError
    c = TaskCanvas()
    with pytest.raises(TaskCanvasError):
        c.update_status("bogus", "done")


def test_canvas_reset_clears_all_nodes():
    """reset() drops every node."""
    from clew.agent.task_canvas import TaskCanvas
    c = TaskCanvas()
    c.add_node("s1", "a")
    c.add_node("s2", "b")
    assert len(c) == 2
    c.reset()
    assert len(c) == 0
    assert c.node_ids() == []


def test_canvas_to_compact_text_empty():
    """Empty canvas renders as empty string (skip injection)."""
    from clew.agent.task_canvas import TaskCanvas
    c = TaskCanvas()
    assert c.to_compact_text() == ""


def test_canvas_to_compact_text_nonempty():
    """A canvas with multiple nodes renders header + node lines."""
    from clew.agent.task_canvas import TaskCanvas, STATUS_PENDING, STATUS_RUNNING, STATUS_DONE
    c = TaskCanvas()
    c.add_node("s1", "first subtask", status=STATUS_DONE, model="openai/o1")
    c.add_node("s2", "second subtask", status=STATUS_RUNNING, depends_on=["s1"])
    c.add_node("s3", "third subtask", status=STATUS_PENDING, depends_on=["s2"])
    text = c.to_compact_text()
    # Header should mention total + per-status counts.
    assert "task_canvas" in text
    assert "3 nodes" in text
    assert "1 done" in text
    assert "1 running" in text
    assert "1 pending" in text
    # Running should appear before pending (display priority).
    assert text.index("[running]") < text.index("[pending]")
    # Model assignment is shown.
    assert "model:openai/o1" in text
    # Dependencies are shown.
    assert "(depends: s1)" in text


def test_canvas_to_compact_text_caps_visible_nodes():
    """More than MAX_VISIBLE_NODES triggers '+N more' summary."""
    from clew.agent.task_canvas import (
        TaskCanvas, STATUS_DONE, MAX_VISIBLE_NODES,
    )
    c = TaskCanvas()
    # Add 2x the visible cap, all done.
    for i in range(MAX_VISIBLE_NODES * 2):
        c.add_node(f"s{i+1}", f"subtask {i+1}", status=STATUS_DONE)
    text = c.to_compact_text()
    assert "+{} more".format(MAX_VISIBLE_NODES) in text
    assert "done" in text  # the summary lists the hidden bucket counts


def test_canvas_to_fragment_wraps_with_stable_id():
    """to_fragment() returns a <context_fragment> with a stable id."""
    from clew.agent.task_canvas import TaskCanvas, STATUS_PENDING
    from clew.agent.context_fragments import parse_fragments
    c = TaskCanvas()
    c.add_node("s1", "first", status=STATUS_PENDING)
    frag = c.to_fragment()
    assert frag is not None
    assert frag.startswith("<context_fragment")
    assert 'type="task_canvas"' in frag
    # Stable id means two calls produce the same fragment id.
    frag2 = c.to_fragment()
    # Extract ids and compare.
    fragments1 = parse_fragments(frag)
    fragments2 = parse_fragments(frag2)
    assert len(fragments1) == 1
    assert len(fragments2) == 1
    assert fragments1[0].id == fragments2[0].id


def test_canvas_to_fragment_empty_returns_none():
    """to_fragment() returns None when canvas is empty (skip injection)."""
    from clew.agent.task_canvas import TaskCanvas
    c = TaskCanvas()
    assert c.to_fragment() is None


def test_canvas_to_dict_round_trips():
    """to_dict() includes nodes, counts, total."""
    from clew.agent.task_canvas import TaskCanvas, STATUS_DONE, STATUS_PENDING
    c = TaskCanvas()
    c.add_node("s1", "first", status=STATUS_DONE, model="x/y")
    c.add_node("s2", "second", status=STATUS_PENDING)
    d = c.to_dict()
    assert d["total"] == 2
    assert d["counts"]["done"] == 1
    assert d["counts"]["pending"] == 1
    assert len(d["nodes"]) == 2
    assert d["nodes"][0]["id"] == "s1"
    assert d["nodes"][0]["model"] == "x/y"


def test_canvas_singleton_reset_for_test():
    """reset_task_canvas_for_test() returns a fresh empty singleton."""
    from clew.agent.task_canvas import (
        get_task_canvas, reset_task_canvas_for_test, STATUS_PENDING,
    )
    c = get_task_canvas()
    c.add_node("s1", "first")
    assert len(c) == 1
    c2 = reset_task_canvas_for_test()
    assert c2 is not c  # different instance
    assert len(c2) == 0
    # The singleton now points to the fresh instance.
    c3 = get_task_canvas()
    assert c3 is c2


def test_canvas_node_to_dict():
    """CanvasNode.to_dict() includes all fields."""
    from clew.agent.task_canvas import CanvasNode, STATUS_DONE
    n = CanvasNode(
        id="s1", label="first", status=STATUS_DONE,
        depends_on=["s0"], model="openai/o1", note="clean",
    )
    d = n.to_dict()
    assert d["id"] == "s1"
    assert d["label"] == "first"
    assert d["status"] == "done"
    assert d["depends_on"] == ["s0"]
    assert d["model"] == "openai/o1"
    assert d["note"] == "clean"


def test_canvas_long_label_truncated_in_compact():
    """Long labels are truncated with an ellipsis in the compact rendering."""
    from clew.agent.task_canvas import TaskCanvas, MAX_LABEL_CHARS
    c = TaskCanvas()
    long_label = "x" * (MAX_LABEL_CHARS + 50)
    c.add_node("s1", long_label)
    text = c.to_compact_text()
    # The truncated form should appear (ends with ellipsis char).
    assert "…" in text
    # The full label should NOT appear (it was truncated).
    assert long_label not in text


# ── 19b: PersonaMemory ─────────────────────────────────────────────────


def test_persona_get_returns_empty_when_no_file():
    """get() returns '' when the persona file doesn't exist yet."""
    from clew.agent.persona_memory import PersonaMemory
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    assert p.get() == ""


def test_persona_set_writes_file_and_get_reads_it():
    """set() writes the file; get() reads it back."""
    from clew.agent.persona_memory import PersonaMemory
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    p.set("I prefer tabs over spaces.\nI like short functions.")
    assert p.get() == "I prefer tabs over spaces.\nI like short functions."
    # File actually exists on disk.
    assert (Path.home() / ".clew" / "persona.md").exists()


def test_persona_reset_deletes_file():
    """reset() deletes the persona file."""
    from clew.agent.persona_memory import PersonaMemory
    path = Path.home() / ".clew" / "persona.md"
    p = PersonaMemory(path=path)
    p.set("some content")
    assert path.exists()
    p.reset()
    assert not path.exists()
    assert p.get() == ""


def test_persona_hard_cap_truncates_oversized_content():
    """Content > HARD_CAP_CHARS is truncated with a note."""
    from clew.agent.persona_memory import (
        PersonaMemory, HARD_CAP_CHARS,
    )
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    oversized = "x" * (HARD_CAP_CHARS + 500)
    p.set(oversized)
    content = p.get()
    assert len(content) <= HARD_CAP_CHARS + 100  # cap + the truncation note
    assert "[truncated by persona_memory]" in content


def test_persona_to_fragment_empty_returns_none():
    """to_fragment() returns None when persona is empty."""
    from clew.agent.persona_memory import PersonaMemory
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    assert p.to_fragment() is None


def test_persona_to_fragment_wraps_content():
    """to_fragment() wraps non-empty content in a <context_fragment>."""
    from clew.agent.persona_memory import PersonaMemory
    from clew.agent.context_fragments import parse_fragments
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    p.set("I like Python and short functions.")
    frag = p.to_fragment()
    assert frag is not None
    assert frag.startswith("<context_fragment")
    assert 'type="persona"' in frag
    # Stable id — two calls produce the same id.
    frag2 = p.to_fragment()
    assert parse_fragments(frag)[0].id == parse_fragments(frag2)[0].id


def test_persona_to_dict_includes_path_and_counts():
    """to_dict() includes path, content, chars, caps."""
    from clew.agent.persona_memory import (
        PersonaMemory, SOFT_CAP_CHARS, HARD_CAP_CHARS,
    )
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    p.set("hello world")
    d = p.to_dict()
    assert d["chars"] == 11
    assert d["soft_cap"] == SOFT_CAP_CHARS
    assert d["hard_cap"] == HARD_CAP_CHARS
    assert d["over_soft_cap"] is False
    assert "content" in d
    assert "path" in d


def test_persona_update_from_session_success():
    """update_from_session() picks a provider and writes the new persona."""
    from clew.agent.persona_memory import (
        PersonaMemory, PersonaDigest, SOFT_CAP_CHARS,
    )

    # Mock provider — returns a fixed new persona.
    class FakeResp:
        text = "Updated: user likes concise responses and Python."
        tokens_in = 50
        tokens_out = 20
    class FakeProvider:
        provider_id = "fake"
        is_loaded = True
        def generate(self, msgs, model=None): return FakeResp()
        def get_model(self): return "fake-model"
    fake_provider = FakeProvider()
    fake_registry = MagicMock()
    fake_registry.active = fake_provider
    fake_registry.get = lambda pid: fake_provider

    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    digest = PersonaDigest(
        summary="user accepted 3 short answers, rejected 2 verbose ones",
        coding_preferences_observed=["concise responses"],
    )
    result = p.update_from_session(digest, registry=fake_registry)
    assert result["ok"] is True
    assert "Updated:" in p.get()
    assert result["provider_id"] == "fake"
    assert result["model"] == "fake-model"
    assert result["unchanged"] is False


def test_persona_update_from_session_unchanged_returns_unchanged_flag():
    """If the LLM returns the input unchanged, no write happens."""
    from clew.agent.persona_memory import PersonaMemory, PersonaDigest

    existing = "I prefer tabs over spaces."
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    p.set(existing)

    class FakeResp:
        text = existing  # byte-for-byte same
    class FakeProvider:
        provider_id = "fake"
        is_loaded = True
        def generate(self, msgs, model=None): return FakeResp()
        def get_model(self): return "fake-model"
    fake_provider = FakeProvider()
    fake_registry = MagicMock()
    fake_registry.active = fake_provider
    fake_registry.get = lambda pid: fake_provider

    digest = PersonaDigest(summary="nothing new")
    result = p.update_from_session(digest, registry=fake_registry)
    assert result["ok"] is True
    assert result["unchanged"] is True


def test_persona_update_from_session_failure_leaves_existing():
    """If the LLM call fails, the existing persona is untouched."""
    from clew.agent.persona_memory import PersonaMemory, PersonaDigest

    existing = "I prefer tabs over spaces."
    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    p.set(existing)

    class FakeProvider:
        provider_id = "fake"
        is_loaded = True
        def generate(self, msgs, model=None):
            raise RuntimeError("LLM exploded")
        def get_model(self): return "fake-model"
    fake_provider = FakeProvider()
    fake_registry = MagicMock()
    fake_registry.active = fake_provider
    fake_registry.get = lambda pid: fake_provider

    digest = PersonaDigest(summary="trigger failure")
    result = p.update_from_session(digest, registry=fake_registry)
    assert result["ok"] is False
    assert "error" in result
    # Existing persona untouched.
    assert p.get() == existing


def test_persona_update_from_session_no_provider_returns_error():
    """No provider available → ok=False, no crash."""
    from clew.agent.persona_memory import PersonaMemory, PersonaDigest

    p = PersonaMemory(path=Path.home() / ".clew" / "persona.md")
    p.set("existing")

    # Empty registry — no active provider.
    fake_registry = MagicMock()
    fake_registry.active = None
    fake_registry.get = lambda pid: None

    digest = PersonaDigest(summary="x")
    result = p.update_from_session(digest, registry=fake_registry)
    assert result["ok"] is False
    assert "no provider" in result["error"].lower()


def test_persona_digest_to_prompt_text_renders_all_fields():
    """PersonaDigest.to_prompt_text() includes every populated field."""
    from clew.agent.persona_memory import PersonaDigest
    d = PersonaDigest(
        summary="session summary",
        accepted_actions=["wrote file X", "ran tests"],
        rejected_actions=["deleted file Y"],
        tools_used=["write_file", "run_code"],
        coding_preferences_observed=["tabs", "4-wide"],
        communication_preferences_observed=["concise"],
        notes=["user explicitly asked for type hints"],
    )
    text = d.to_prompt_text()
    assert "session summary" in text
    assert "wrote file X" in text
    assert "deleted file Y" in text
    assert "write_file" in text
    assert "tabs" in text
    assert "concise" in text
    assert "type hints" in text


def test_persona_digest_empty_renders_placeholder():
    """Empty digest renders as '(empty digest)'."""
    from clew.agent.persona_memory import PersonaDigest
    d = PersonaDigest()
    assert d.to_prompt_text() == "(empty digest)"


def test_persona_singleton_reset_for_test():
    """reset_persona_memory_for_test() returns a fresh singleton."""
    from clew.agent.persona_memory import (
        get_persona_memory, reset_persona_memory_for_test,
    )
    p = get_persona_memory()
    p.set("first")
    # Reset with a fresh temp path so the new singleton doesn't read
    # the file the previous singleton wrote.
    fresh_path = Path.home() / ".clew" / "persona_test_reset.md"
    p2 = reset_persona_memory_for_test(path=fresh_path)
    assert p2 is not p
    assert p2.get() == ""
    # Singleton now points to the fresh instance.
    p3 = get_persona_memory()
    assert p3 is p2
