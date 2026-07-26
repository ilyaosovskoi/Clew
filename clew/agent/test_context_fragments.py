#!/usr/bin/env python3
"""Unit tests for marker-based context fragment compaction (Issue #5)."""

from __future__ import annotations

import pytest

from clew.agent.context_fragments import (
    ContextFragment,
    FragmentCompactionConfig,
    FragmentCompactionStats,
    build_fragment,
    compact_fragments,
    compact_message_content,
    parse_fragments,
    render_tombstone,
    stable_id,
)


# ── Parsing ───────────────────────────────────────────────────────────────


def test_parse_fragments_empty():
    assert parse_fragments("") == []
    assert parse_fragments("no fragments here") == []


def test_parse_fragments_single():
    text = (
        'before\n'
        '<context_fragment type="file_outline" id="auth.py">\n'
        'def login(): ...\n'
        '</context_fragment>\n'
        'after'
    )
    frags = parse_fragments(text)
    assert len(frags) == 1
    f = frags[0]
    assert f.type == "file_outline"
    assert f.id == "auth.py"
    assert f.body.strip() == "def login(): ..."
    # Offsets must reconstruct the original substring.
    assert text[f.start : f.end].startswith("<context_fragment")
    assert text[f.start : f.end].endswith("</context_fragment>")


def test_parse_fragments_multiple():
    text = (
        '<context_fragment type="a" id="1">body-a</context_fragment>'
        '<context_fragment type="b" id="2">body-b</context_fragment>'
    )
    frags = parse_fragments(text)
    assert len(frags) == 2
    assert frags[0].type == "a"
    assert frags[1].type == "b"


def test_parse_fragments_unclosed_skipped():
    """Malformed fragments are silently skipped."""
    text = (
        '<context_fragment type="a" id="1">body-a</context_fragment>'
        '<context_fragment type="b" id="2">body-b without close'
    )
    frags = parse_fragments(text)
    assert len(frags) == 1


def test_parse_fragments_case_insensitive_tag():
    """Tag matching is case-insensitive on the tag name but case-sensitive on attributes."""
    text = (
        '<CONTEXT_FRAGMENT type="a" id="1">body</CONTEXT_FRAGMENT>'
    )
    frags = parse_fragments(text)
    assert len(frags) == 1
    assert frags[0].type == "a"


# ── Tombstone rendering ───────────────────────────────────────────────────


def test_render_tombstone_includes_header_and_digest():
    text = build_fragment("file_outline", "auth.py", "def login(): ...")
    frag = parse_fragments(text)[0]
    ts = render_tombstone(frag)
    assert ts.startswith('<context_fragment type="file_outline" id="auth.py">')
    assert "[COMPACTED]" in ts
    assert "def login():" in ts
    assert ts.endswith("</context_fragment>")


def test_render_tombstone_truncates_long_digest():
    long_body = "x " * 200  # 400 chars
    text = build_fragment("big", "x", long_body)
    frag = parse_fragments(text)[0]
    ts = render_tombstone(frag, max_digest_chars=30)
    # The full body must NOT appear in the tombstone.
    assert long_body not in ts
    # The tombstone's digest line is "[COMPACTED] " + (digest up to 30 chars).
    # 30-char digest ⇒ max line length = len("[COMPACTED] ") + 30 = 43.
    digest_line = ts.split("\n")[1]
    assert len(digest_line) <= 50
    assert "…" in digest_line


# ── Compaction ────────────────────────────────────────────────────────────


def test_compact_fragments_no_fragments_returns_unchanged():
    text = "just plain text"
    assert compact_fragments(text) == text


def test_compact_fragments_keeps_latest_per_id():
    """With default config, latest occurrence per (type, id) is preserved."""
    body1 = build_fragment("file_outline", "auth.py", "OLD " + "x" * 200)
    body2 = build_fragment("file_outline", "auth.py", "NEW " + "y" * 200)
    text = f"{body1}\n---\n{body2}"
    result = compact_fragments(text)
    assert "NEW " + "y" * 200 in result
    assert "OLD " + "x" * 200 not in result
    assert "[COMPACTED]" in result


def test_compact_fragments_distinct_ids_both_preserved():
    """Fragments with different ids are independent — both preserved."""
    body1 = build_fragment("file_outline", "auth.py", "outline A")
    body2 = build_fragment("file_outline", "model.py", "outline B")
    text = f"{body1}\n{body2}"
    result = compact_fragments(text)
    assert "outline A" in result
    assert "outline B" in result
    assert "[COMPACTED]" not in result


def test_compact_fragments_keep_latest_per_type():
    """keep_latest_per_type collapses all but the latest of each type."""
    body1 = build_fragment("test_run", "run-1", "PASSED " + "x" * 200)
    body2 = build_fragment("test_run", "run-2", "FAILED " + "y" * 200)
    text = f"{body1}\n{body2}"
    cfg = FragmentCompactionConfig(
        keep_latest_per_id=False,
        keep_latest_per_type=True,
    )
    result = compact_fragments(text, cfg)
    assert "FAILED " + "y" * 200 in result
    assert "PASSED " + "x" * 200 not in result
    assert "[COMPACTED]" in result


def test_compact_fragments_collapse_types_filter():
    """collapse_types restricts which types are eligible for compaction."""
    body_a1 = build_fragment("file_outline", "a.py", "OLD-A " + "x" * 200)
    body_a2 = build_fragment("file_outline", "a.py", "NEW-A " + "x" * 200)
    body_b1 = build_fragment("test_run", "r1", "OLD-B " + "y" * 200)
    body_b2 = build_fragment("test_run", "r1", "NEW-B " + "y" * 200)
    text = f"{body_a1}\n{body_a2}\n{body_b1}\n{body_b2}"
    # Only test_run is collapsible → both file_outline bodies stay intact.
    cfg = FragmentCompactionConfig(collapse_types=("test_run",))
    result = compact_fragments(text, cfg)
    assert "OLD-A " + "x" * 200 in result
    assert "NEW-A " + "x" * 200 in result
    assert "NEW-B " + "y" * 200 in result
    assert "OLD-B " + "y" * 200 not in result  # collapsed
    assert "[COMPACTED]" in result


def test_compact_fragments_preserves_surrounding_text():
    body = build_fragment("outline", "x.py", "BODY")
    text = f"intro line\n{body}\noutro line"
    result = compact_fragments(text)
    assert result.startswith("intro line\n")
    assert result.endswith("\noutro line")


# ── Per-message wrapper ───────────────────────────────────────────────────


def test_compact_message_content_returns_stats():
    body1 = build_fragment("outline", "x.py", "OLD" * 100)
    body2 = build_fragment("outline", "x.py", "NEW")
    text = f"{body1}\n{body2}"
    new_text, stats = compact_message_content(text)
    assert isinstance(stats, FragmentCompactionStats)
    assert stats.fragments_total == 2
    assert stats.fragments_compacted == 1
    assert stats.fragments_preserved == 1
    assert stats.chars_before > stats.chars_after
    assert stats.chars_saved > 0


def test_compact_message_content_empty():
    new_text, stats = compact_message_content("")
    assert new_text == ""
    assert stats.fragments_total == 0


# ── build_fragment helper ─────────────────────────────────────────────────


def test_build_fragment_basic():
    frag = build_fragment("file_outline", "auth.py", "def login(): ...")
    assert frag.startswith('<context_fragment type="file_outline" id="auth.py">')
    assert "def login(): ..." in frag
    assert frag.endswith("</context_fragment>")


def test_build_fragment_round_trip():
    """Anything build_fragment produces must be parseable."""
    frag = build_fragment("test", "id-1", "body text")
    parsed = parse_fragments(frag)
    assert len(parsed) == 1
    assert parsed[0].type == "test"
    assert parsed[0].id == "id-1"
    assert parsed[0].body.strip() == "body text"


def test_build_fragment_rejects_empty_type():
    with pytest.raises(ValueError):
        build_fragment("", "id", "body")


def test_build_fragment_rejects_empty_id():
    with pytest.raises(ValueError):
        build_fragment("type", "", "body")


# ── stable_id ──────────────────────────────────────────────────────────────


def test_stable_id_is_deterministic():
    a = stable_id("foo", "bar")
    b = stable_id("foo", "bar")
    assert a == b
    assert len(a) == 12


def test_stable_id_differs_for_different_inputs():
    a = stable_id("foo", "bar")
    b = stable_id("foo", "baz")
    assert a != b


# ── Integration: matches progressive_tools announcement style ──────────────


def test_compact_with_mixed_content():
    """Mixed content: fragments interspersed with normal text/tool output."""
    frag1 = build_fragment("file_outline", "main.py", "OLD outline " + "x" * 200)
    frag2 = build_fragment("test_run", "r1", "PASSED " + "y" * 50)
    frag3 = build_fragment("file_outline", "main.py", "NEW outline " + "z" * 200)
    text = (
        "USER: please update main\n"
        "ASSISTANT: here's the outline:\n"
        f"{frag1}\n"
        "TOOL: ran tests\n"
        f"{frag2}\n"
        "ASSISTANT: applied changes\n"
        f"{frag3}\n"
    )
    result = compact_fragments(text)
    # Latest file_outline preserved, older collapsed
    assert "NEW outline " + "z" * 200 in result
    assert "OLD outline " + "x" * 200 not in result
    assert "[COMPACTED]" in result
    # Latest test_run preserved
    assert "PASSED " + "y" * 50 in result
    # All surrounding text intact
    assert "USER: please update main" in result
    assert "ASSISTANT: applied changes" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
