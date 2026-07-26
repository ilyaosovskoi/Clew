#!/usr/bin/env python3
"""Unit tests for SQLite persistence (Issue #6)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from clew.session.sqlite_persistence import (
    SCHEMA_VERSION,
    SQLitePersistence,
    is_sqlite_path,
)


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "session.db")


@pytest.fixture
def store(tmp_db):
    s = SQLitePersistence(tmp_db)
    yield s
    s.close()


# ── Path detection ────────────────────────────────────────────────────────


def test_is_sqlite_path_extensions():
    assert is_sqlite_path("/tmp/foo.db")
    assert is_sqlite_path("/tmp/foo.sqlite")
    assert is_sqlite_path("/tmp/foo.sqlite3")
    assert not is_sqlite_path("/tmp/foo.json")
    assert not is_sqlite_path("/tmp/foo.txt")


# ── Schema bootstrap ──────────────────────────────────────────────────────


def test_schema_created(tmp_db):
    store = SQLitePersistence(tmp_db)
    cur = store._conn.cursor()
    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION
    cur.close()
    store.close()


def test_wal_mode_enabled(tmp_db):
    store = SQLitePersistence(tmp_db)
    cur = store._conn.cursor()
    cur.execute("PRAGMA journal_mode")
    row = cur.fetchone()
    assert row[0].lower() == "wal"
    cur.close()
    store.close()


# ── Session lifecycle ─────────────────────────────────────────────────────


def test_create_and_list_session(store):
    sid = store.create_session(title="test session")
    assert isinstance(sid, str)
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["title"] == "test session"
    assert sessions[0]["message_count"] == 0


def test_delete_session(store):
    sid = store.create_session()
    store.append_message(sid, "user", "hello")
    assert store.delete_session(sid) is True
    # Deleting again should report False (already gone).
    assert store.delete_session(sid) is False
    assert store.list_sessions() == []


def test_list_sessions_newest_first(store):
    s1 = store.create_session("old")
    s2 = store.create_session("new")
    # Touch s2 so it has a strictly newer updated_at than s1.
    store.append_message(s2, "user", "ping")
    sessions = store.list_sessions()
    assert sessions[0]["id"] == s2
    assert sessions[1]["id"] == s1


# ── Append + load ─────────────────────────────────────────────────────────


def test_append_message(store):
    sid = store.create_session()
    seq0 = store.append_message(sid, "user", "hi")
    seq1 = store.append_message(sid, "assistant", "hello")
    assert seq0 == 0
    assert seq1 == 1
    messages, summary = store.load(sid)
    assert summary == ""
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hi"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "hello"


def test_append_message_with_metadata(store):
    sid = store.create_session()
    store.append_message(
        sid, "tool", "result", metadata={"tool": "read_file", "path": "foo.py"}
    )
    messages, _ = store.load(sid)
    assert messages[0]["metadata"]["tool"] == "read_file"
    assert messages[0]["metadata"]["path"] == "foo.py"


def test_load_nonexistent_session(store):
    messages, summary = store.load("does-not-exist")
    assert messages == []
    assert summary == ""


def test_message_count(store):
    sid = store.create_session()
    assert store.message_count(sid) == 0
    store.append_message(sid, "user", "a")
    store.append_message(sid, "user", "b")
    assert store.message_count(sid) == 2


# ── Full save (rewrite) ───────────────────────────────────────────────────


def test_save_replaces_messages(store):
    sid = store.create_session()
    store.append_message(sid, "user", "old")
    store.save(
        sid,
        [
            {"role": "user", "content": "new1"},
            {"role": "assistant", "content": "new2"},
        ],
        compaction_summary="summary text",
    )
    messages, summary = store.load(sid)
    assert len(messages) == 2
    assert messages[0]["content"] == "new1"
    assert messages[1]["content"] == "new2"
    assert summary == "summary text"


def test_save_creates_session_if_missing(store):
    sid = "invented-session-id"
    store.save(
        sid,
        [{"role": "user", "content": "hello"}],
        compaction_summary="",
    )
    messages, _ = store.load(sid)
    assert len(messages) == 1
    sessions = store.list_sessions()
    assert any(s["id"] == sid for s in sessions)


# ── Range queries ─────────────────────────────────────────────────────────


def test_load_range_with_limit(store):
    sid = store.create_session()
    for i in range(5):
        store.append_message(sid, "user", f"msg-{i}")
    page = store.load_range(sid, limit=2)
    assert len(page) == 2
    assert page[0]["content"] == "msg-0"
    assert page[1]["content"] == "msg-1"


def test_load_range_with_offset_and_limit(store):
    sid = store.create_session()
    for i in range(5):
        store.append_message(sid, "user", f"msg-{i}")
    page = store.load_range(sid, offset=2, limit=2)
    assert len(page) == 2
    assert page[0]["content"] == "msg-2"
    assert page[1]["content"] == "msg-3"


def test_load_range_offset_only(store):
    sid = store.create_session()
    for i in range(5):
        store.append_message(sid, "user", f"msg-{i}")
    page = store.load_range(sid, offset=3)
    assert len(page) == 2
    assert page[0]["content"] == "msg-3"
    assert page[1]["content"] == "msg-4"


# ── Compaction support ────────────────────────────────────────────────────


def test_trim_to_keep_recent(store):
    sid = store.create_session()
    for i in range(10):
        store.append_message(sid, "user", f"msg-{i}")
    deleted = store.trim_to_keep_recent(sid, keep_recent=3)
    assert deleted == 7
    messages, _ = store.load(sid)
    assert len(messages) == 3
    assert messages[0]["content"] == "msg-7"
    assert messages[-1]["content"] == "msg-9"


def test_trim_when_already_under_limit(store):
    sid = store.create_session()
    for i in range(3):
        store.append_message(sid, "user", f"msg-{i}")
    deleted = store.trim_to_keep_recent(sid, keep_recent=5)
    assert deleted == 0
    messages, _ = store.load(sid)
    assert len(messages) == 3


def test_trim_to_zero(store):
    sid = store.create_session()
    for i in range(3):
        store.append_message(sid, "user", f"msg-{i}")
    deleted = store.trim_to_keep_recent(sid, keep_recent=0)
    assert deleted == 3
    assert store.message_count(sid) == 0


def test_set_compaction_summary(store):
    sid = store.create_session()
    store.set_compaction_summary(sid, "compacted context")
    _, summary = store.load(sid)
    assert summary == "compacted context"


# ── Persistence across re-open ────────────────────────────────────────────


def test_reopen_preserves_data(tmp_db):
    store1 = SQLitePersistence(tmp_db)
    sid = store1.create_session("persistent")
    store1.append_message(sid, "user", "hello")
    store1.close()

    store2 = SQLitePersistence(tmp_db)
    sessions = store2.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "persistent"
    messages, _ = store2.load(sid)
    assert len(messages) == 1
    assert messages[0]["content"] == "hello"
    store2.close()


# ── Thread safety ─────────────────────────────────────────────────────────


def test_concurrent_appends(store):
    import threading

    sid = store.create_session()

    def writer(prefix: str, n: int):
        for i in range(n):
            store.append_message(sid, "user", f"{prefix}-{i}")

    threads = [threading.Thread(target=writer, args=(f"t{t}", 10)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.message_count(sid) == 40


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
