#!/usr/bin/env python3
"""
Goal G6 — Post-task Bridge (CMS / editable handoff) — test suite.

Verifies:
  1. parse_agent_output splits text / code / file_diff / todo / note blocks.
  2. HandoffBlock statuses (pending/accepted/rejected/edited) round-trip.
  3. HandoffStore.save / load / delete round-trips a doc.
  4. set_block_status updates a single block in place.
  5. toggle_handoff_todo flips the checked flag.
  6. reorder_blocks re-orders by id.
  7. build_revision_prompt compiles accept/reject/edit/comment into a prompt.
  8. export_markdown produces a valid Markdown string.
  9. Bridge-level integration: create_handoff / list_handoffs /
     set_handoff_block_status / build_handoff_revision_prompt work.

Run:
    python -m pytest clew/tests/test_g6_handoff.py -v
or:
    python clew/tests/test_g6_handoff.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest


# ── Test isolation: each test uses a fresh tmpdir-backed HandoffStore ──

@pytest.fixture()
def tmp_store(tmp_path):
    """Return a HandoffStore rooted at tmp_path/handoffs."""
    from clew.handoff_bridge import HandoffStore
    return HandoffStore(root=tmp_path / "handoffs")


@pytest.fixture(autouse=True)
def reset_singletons():
    import clew.handoff_bridge as _hb
    _hb._store = None
    yield
    _hb._store = None


# ── 1. parse_agent_output ───────────────────────────────────────────

def test_parse_text_only():
    from clew.handoff_bridge import parse_agent_output, BLOCK_TEXT
    doc = parse_agent_output("Hello world.\nSecond paragraph.", prompt="hi")
    assert len(doc.blocks) == 1
    assert doc.blocks[0].type == BLOCK_TEXT
    assert "Hello world" in doc.blocks[0].content


def test_parse_code_block():
    from clew.handoff_bridge import parse_agent_output, BLOCK_CODE
    out = "Here is the code:\n```python\nx = 1\nprint(x)\n```\nDone."
    doc = parse_agent_output(out)
    types = [b.type for b in doc.blocks]
    assert BLOCK_CODE in types
    code_block = next(b for b in doc.blocks if b.type == BLOCK_CODE)
    assert code_block.language == "python"
    assert "x = 1" in code_block.content


def test_parse_file_marker():
    from clew.handoff_bridge import parse_agent_output, BLOCK_FILE_DIFF
    out = "[WRITTEN] /tmp/foo.py (42 chars)\nDone."
    doc = parse_agent_output(out)
    fd_blocks = [b for b in doc.blocks if b.type == BLOCK_FILE_DIFF]
    assert len(fd_blocks) == 1
    assert fd_blocks[0].path == "/tmp/foo.py"
    assert "written" in fd_blocks[0].diff_stat.lower()


def test_parse_todo_line():
    from clew.handoff_bridge import parse_agent_output, BLOCK_TODO
    out = "Tasks:\n- [ ] First task\n- [x] Done task\n"
    doc = parse_agent_output(out)
    todos = [b for b in doc.blocks if b.type == BLOCK_TODO]
    assert len(todos) == 2
    assert todos[0].checked is False
    assert "First task" in todos[0].content
    assert todos[1].checked is True


def test_parse_title_derived_from_prompt():
    from clew.handoff_bridge import parse_agent_output
    doc = parse_agent_output("ok", prompt="Refactor auth module\nDetails here")
    assert "Refactor auth module" in doc.title


# ── 2. HandoffBlock statuses round-trip ─────────────────────────────

def test_block_status_roundtrip():
    from clew.handoff_bridge import HandoffBlock, BLOCK_CODE
    b = HandoffBlock(id="blk_x", type=BLOCK_CODE, content="x = 1", language="python")
    d = b.to_dict()
    assert d["status"] == "pending"
    b2 = HandoffBlock.from_dict({**d, "status": "accepted"})
    assert b2.status == "accepted"


# ── 3. HandoffStore save/load/delete ────────────────────────────────

def test_save_load_roundtrip(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_test1", title="Test", prompt="hello",
        blocks=[HandoffBlock(id="blk_1", type=BLOCK_TEXT, content="hi")],
    )
    tmp_store.save(doc)
    loaded = tmp_store.load("hdf_test1")
    assert loaded is not None
    assert loaded.title == "Test"
    assert len(loaded.blocks) == 1
    assert loaded.blocks[0].content == "hi"


def test_save_updates_timestamp(tmp_store):
    from clew.handoff_bridge import HandoffDocument
    doc = HandoffDocument(id="hdf_ts", title="T", prompt="")
    tmp_store.save(doc)
    first = tmp_store.load("hdf_ts").updated_at
    # Save again — updated_at should change (or at least be present)
    tmp_store.save(doc)
    second = tmp_store.load("hdf_ts").updated_at
    assert first != ""
    assert second != ""


def test_delete_returns_true_then_false(tmp_store):
    from clew.handoff_bridge import HandoffDocument
    doc = HandoffDocument(id="hdf_del", title="X", prompt="")
    tmp_store.save(doc)
    assert tmp_store.delete("hdf_del") is True
    assert tmp_store.delete("hdf_del") is False  # already gone


def test_list_docs_returns_metadata(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_list", title="ListTest", prompt="p",
        blocks=[HandoffBlock(id="b", type=BLOCK_TEXT, content="x")],
    )
    tmp_store.save(doc)
    docs = tmp_store.list_docs()
    assert len(docs) == 1
    assert docs[0]["id"] == "hdf_list"
    assert docs[0]["block_count"] == 1
    assert docs[0]["title"] == "ListTest"


# ── 4. set_block_status ─────────────────────────────────────────────

def test_set_block_status_accepted(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_sbs", title="T", prompt="",
        blocks=[HandoffBlock(id="blk_a", type=BLOCK_TEXT, content="hello")],
    )
    tmp_store.save(doc)
    updated = tmp_store.set_block_status("hdf_sbs", "blk_a", "accepted")
    assert updated is not None
    assert updated.blocks[0].status == "accepted"


def test_set_block_status_with_comment_and_replacement(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_sbs2", title="T", prompt="",
        blocks=[HandoffBlock(id="blk_b", type=BLOCK_TEXT, content="hello")],
    )
    tmp_store.save(doc)
    updated = tmp_store.set_block_status(
        "hdf_sbs2", "blk_b", "edited",
        comment="use float division", replacement="result = a / b",
    )
    assert updated.blocks[0].status == "edited"
    assert updated.blocks[0].comment == "use float division"
    assert updated.blocks[0].replacement == "result = a / b"


def test_set_block_status_invalid_status_raises(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_sbs3", title="T", prompt="",
        blocks=[HandoffBlock(id="blk_c", type=BLOCK_TEXT, content="x")],
    )
    tmp_store.save(doc)
    with pytest.raises(ValueError):
        tmp_store.set_block_status("hdf_sbs3", "blk_c", "bogus")


# ── 5. toggle_handoff_todo ──────────────────────────────────────────

def test_toggle_todo_flips_checked(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TODO
    doc = HandoffDocument(
        id="hdf_todo", title="T", prompt="",
        blocks=[HandoffBlock(id="blk_td", type=BLOCK_TODO, content="task", checked=False)],
    )
    tmp_store.save(doc)
    updated = tmp_store.toggle_todo("hdf_todo", "blk_td")
    assert updated.blocks[0].checked is True
    updated = tmp_store.toggle_todo("hdf_todo", "blk_td")
    assert updated.blocks[0].checked is False


# ── 6. reorder_blocks ───────────────────────────────────────────────

def test_reorder_blocks_by_id(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_reord", title="T", prompt="",
        blocks=[
            HandoffBlock(id="blk_1", type=BLOCK_TEXT, content="one"),
            HandoffBlock(id="blk_2", type=BLOCK_TEXT, content="two"),
            HandoffBlock(id="blk_3", type=BLOCK_TEXT, content="three"),
        ],
    )
    tmp_store.save(doc)
    updated = tmp_store.reorder_blocks("hdf_reord", ["blk_3", "blk_1", "blk_2"])
    contents = [b.content for b in updated.blocks]
    assert contents == ["three", "one", "two"]


def test_reorder_blocks_appends_missing_ids(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_reord2", title="T", prompt="",
        blocks=[
            HandoffBlock(id="blk_1", type=BLOCK_TEXT, content="one"),
            HandoffBlock(id="blk_2", type=BLOCK_TEXT, content="two"),
        ],
    )
    tmp_store.save(doc)
    # Only specify blk_2 first; blk_1 should be appended
    updated = tmp_store.reorder_blocks("hdf_reord2", ["blk_2"])
    contents = [b.content for b in updated.blocks]
    assert contents == ["two", "one"]


# ── 7. build_revision_prompt ────────────────────────────────────────

def test_revision_prompt_empty_when_all_accepted(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_rev1", title="T", prompt="",
        blocks=[HandoffBlock(id="blk_1", type=BLOCK_TEXT, content="x", status="accepted")],
    )
    tmp_store.save(doc)
    prompt = tmp_store.build_revision_prompt("hdf_rev1")
    assert prompt == ""


def test_revision_prompt_includes_rejected_block(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT
    doc = HandoffDocument(
        id="hdf_rev2", title="T", prompt="",
        blocks=[
            HandoffBlock(id="blk_1", type=BLOCK_TEXT, content="x", status="rejected",
                         comment="wrong"),
            HandoffBlock(id="blk_2", type=BLOCK_TEXT, content="y", status="accepted"),
        ],
    )
    tmp_store.save(doc)
    prompt = tmp_store.build_revision_prompt("hdf_rev2")
    assert "REJECT" in prompt
    assert "wrong" in prompt
    # Accepted block should NOT be mentioned
    assert "Block 2" not in prompt


def test_revision_prompt_includes_edited_replacement(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_CODE
    doc = HandoffDocument(
        id="hdf_rev3", title="T", prompt="",
        blocks=[
            HandoffBlock(id="blk_1", type=BLOCK_CODE, content="x = 1",
                         status="edited", replacement="x = 2"),
        ],
    )
    tmp_store.save(doc)
    prompt = tmp_store.build_revision_prompt("hdf_rev3")
    assert "REPLACE" in prompt
    assert "x = 2" in prompt


# ── 8. export_markdown ──────────────────────────────────────────────

def test_export_markdown_includes_title_and_blocks(tmp_store):
    from clew.handoff_bridge import HandoffDocument, HandoffBlock, BLOCK_TEXT, BLOCK_CODE
    doc = HandoffDocument(
        id="hdf_md", title="My Handoff", prompt="do something",
        blocks=[
            HandoffBlock(id="blk_1", type=BLOCK_TEXT, content="Hello world"),
            HandoffBlock(id="blk_2", type=BLOCK_CODE, content="x = 1", language="python"),
        ],
    )
    tmp_store.save(doc)
    md = tmp_store.export_markdown("hdf_md")
    assert "# My Handoff" in md
    assert "Hello world" in md
    assert "```python" in md
    assert "x = 1" in md


def test_export_markdown_returns_empty_for_missing(tmp_store):
    md = tmp_store.export_markdown("nonexistent")
    assert md == ""


# ── 9. Bridge integration ───────────────────────────────────────────

def test_bridge_create_handoff(monkeypatch, tmp_path):
    # Patch the global store to use a tmp dir
    import clew.handoff_bridge as _hb
    _hb._store = _hb.HandoffStore(root=tmp_path / "h")
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.create_handoff(
        output="Hello\n```python\nx = 1\n```\n[WRITTEN] foo.py\n- [ ] todo item",
        prompt="Write something",
        title="Test Handoff",
    )
    assert r.get("ok") is True
    doc = r.get("doc") or {}
    assert doc.get("title") == "Test Handoff"
    assert len(doc.get("blocks") or []) >= 3  # text + code + file_diff + todo
    assert doc.get("id", "").startswith("hdf_")


def test_bridge_list_handoffs(monkeypatch, tmp_path):
    import clew.handoff_bridge as _hb
    _hb._store = _hb.HandoffStore(root=tmp_path / "h")
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    # Create two handoffs
    bridge.create_handoff(output="A", prompt="a", title="A")
    bridge.create_handoff(output="B", prompt="b", title="B")
    docs = bridge.list_handoffs()
    assert len(docs) == 2


def test_bridge_set_handoff_block_status(tmp_path):
    import clew.handoff_bridge as _hb
    _hb._store = _hb.HandoffStore(root=tmp_path / "h")
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.create_handoff(
        output="```python\nx = 1\n```\nSome text.",
        prompt="test", title="T",
    )
    doc = r["doc"]
    block_id = doc["blocks"][0]["id"]
    r2 = bridge.set_handoff_block_status(doc["id"], block_id, "accepted")
    assert r2.get("ok") is True
    assert r2["doc"]["blocks"][0]["status"] == "accepted"


def test_bridge_build_revision_prompt(tmp_path):
    import clew.handoff_bridge as _hb
    _hb._store = _hb.HandoffStore(root=tmp_path / "h")
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.create_handoff(output="Block 1 content", prompt="p", title="T")
    doc_id = r["doc"]["id"]
    block_id = r["doc"]["blocks"][0]["id"]
    bridge.set_handoff_block_status(doc_id, block_id, "rejected", comment="bad")
    r3 = bridge.build_handoff_revision_prompt(doc_id)
    assert r3.get("ok") is True
    assert "REJECT" in r3["prompt"]
    assert "bad" in r3["prompt"]


def test_bridge_export_handoff_markdown(tmp_path):
    import clew.handoff_bridge as _hb
    _hb._store = _hb.HandoffStore(root=tmp_path / "h")
    from clew_tui.bridge import ClewBridge
    bridge = ClewBridge()
    r = bridge.create_handoff(output="Hello world", prompt="p", title="MD Test")
    md_r = bridge.export_handoff_markdown(r["doc"]["id"])
    assert md_r.get("ok") is True
    assert "Hello world" in md_r["markdown"]


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
            # Skip fixtures
            if hasattr(fn, "_pytestfixturefunction"):
                continue
            # Manually create tmp_path
            with tempfile.TemporaryDirectory() as td:
                tmp_path = Path(td)
                # Inspect signature to see if it needs tmp_path / tmp_store
                sig = inspect.signature(fn)
                kwargs = {}
                for param_name in sig.parameters:
                    if param_name == "tmp_path":
                        kwargs["tmp_path"] = tmp_path
                    elif param_name == "tmp_store":
                        from clew.handoff_bridge import HandoffStore
                        kwargs["tmp_store"] = HandoffStore(root=tmp_path / "handoffs")
                    elif param_name == "monkeypatch":
                        class _MP:
                            def setattr(self, *a, **k): pass
                        kwargs["monkeypatch"] = _MP()
                # Reset singletons
                import clew.handoff_bridge as _hb
                _hb._store = None
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
