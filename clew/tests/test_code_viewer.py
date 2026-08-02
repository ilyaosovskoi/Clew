#!/usr/bin/env python3
"""Tests for clew/code_viewer.py — .gitignore-aware file filtering.

Verifies the .gitignore layer added to ``CodeViewerService``:
  1. Files matching a .gitignore pattern are excluded from ``list_files()``
     and ``search()``.
  2. Directory patterns (``secrets/``) exclude the directory and its
     contents, while still allowing sibling dirs through.
  3. Negation patterns (``!important.log``) re-include matched files.
  4. With no .gitignore, the existing IGNORED_DIRS/IGNORED_FILES base
     layer still applies.
  5. When ``pathspec`` is not importable, the .gitignore layer is a
     no-op (graceful degradation — convention #4).
  6. Root-anchored patterns (``/out``) only match at the project root,
     not nested dirs of the same name.
  7. ``_collect_watched_dirs`` (which feeds ``QFileSystemWatcher``) does
     not watch .gitignore-ignored directories.
  8. A non-anchored pattern (``*.log``) also excludes NESTED files
     (``subdir/error.log``), guarding the rel-path normalization that
     ``_is_ignored`` / ``_iter_files`` feed to pathspec at depth.

HEADLESS CI NOTE:
  ``CodeViewerService.set_root()`` calls ``_start_watcher()``, which
  imports ``PySide6.QtCore.QFileSystemWatcher``. That import fails in
  headless CI without Qt installed. These tests monkeypatch
  ``_start_watcher`` to a no-op on the instance before calling
  ``set_root`` — this exercises the real ``set_root()`` code path
  (including the .gitignore matcher rebuild) without requiring Qt.
  ``list_files()`` and ``search()`` themselves never touch Qt.

Run:
    python -m pytest clew/tests/test_code_viewer.py -v
"""

from __future__ import annotations

import sys

import pytest

from clew.code_viewer import CodeViewerService

# ── Helpers ────────────────────────────────────────────────────────


def _make_service(monkeypatch, tmp_path) -> CodeViewerService:
    """Construct a CodeViewerService rooted at ``tmp_path`` without Qt.

    Monkeypatches ``_start_watcher`` to a no-op so ``set_root`` can run
    in headless CI. The .gitignore matcher is built inside ``set_root``,
    so the returned service reflects whatever .gitignore exists under
    ``tmp_path``.
    """
    svc = CodeViewerService()
    monkeypatch.setattr(svc, "_start_watcher", lambda: None)
    svc.set_root(str(tmp_path))
    return svc


def _paths(listing):
    """Extract the relative ``path`` field from a list_files() result."""
    return {e["path"] for e in listing}


# ── 1. .gitignore excludes files ───────────────────────────────────


def test_gitignore_excludes_files(monkeypatch, tmp_path):
    """A ``*.log`` .gitignore hides .log files from list_files and search."""
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("debug = True  # a debug flag\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("debug info here\n", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    listing = _paths(svc.list_files())

    assert "app.py" in listing
    assert "debug.log" not in listing

    # search() must not return hits from debug.log, but should still hit
    # the line in app.py that contains "debug".
    results = svc.search("debug")
    result_paths = {r["path"] for r in results}
    assert "app.py" in result_paths
    assert "debug.log" not in result_paths


# ── 2. .gitignore excludes directories ─────────────────────────────


def test_gitignore_excludes_directories(monkeypatch, tmp_path):
    """A ``secrets/`` pattern excludes the dir AND its contents."""
    (tmp_path / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "secret.txt").write_text(
        "password=hunter2\n", encoding="utf-8"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    listing = _paths(svc.list_files())

    assert "app/main.py" in listing
    assert "secrets/secret.txt" not in listing
    # The secrets dir itself must not appear as a file entry either.
    assert not any(p.startswith("secrets") for p in listing)


# ── 3. .gitignore negation ─────────────────────────────────────────


def test_gitignore_negation(monkeypatch, tmp_path):
    """A ``!important.log`` negation re-includes a previously-ignored file."""
    (tmp_path / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("x\n", encoding="utf-8")
    (tmp_path / "important.log").write_text("y\n", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    listing = _paths(svc.list_files())

    assert "important.log" in listing
    assert "debug.log" not in listing


# ── 4. No .gitignore → base layer still works ──────────────────────


def test_no_gitignore_falls_back(monkeypatch, tmp_path):
    """With no .gitignore, IGNORED_DIRS/IGNORED_FILES still apply."""
    # No .gitignore created.
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-311.pyc").write_text("bytes\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_text("junk", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    listing = _paths(svc.list_files())

    assert "app.py" in listing
    # IGNORED_DIRS base layer — __pycache__ pruned, contents not listed.
    assert not any(p.startswith("__pycache__") for p in listing)
    # IGNORED_FILES base layer.
    assert ".DS_Store" not in listing
    # Matcher is None when there's no .gitignore.
    assert svc._ignore_matcher is None


# ── 5. pathspec missing → graceful no-op ───────────────────────────


def test_pathspec_missing_graceful(monkeypatch, tmp_path):
    """When pathspec is unimportable, .gitignore is a no-op (convention #4).

    A ``None`` entry in ``sys.modules`` causes ``import pathspec`` to raise
    ``ImportError``; ``_build_gitignore_matcher`` catches that and returns
    None, so the .gitignore layer is skipped but IGNORED_* still apply.
    """
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("debug\n", encoding="utf-8")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-311.pyc").write_text("bytes\n", encoding="utf-8")

    # Force `import pathspec` inside _build_gitignore_matcher to fail.
    monkeypatch.setitem(sys.modules, "pathspec", None)

    svc = _make_service(monkeypatch, tmp_path)
    # Matcher must be None — pathspec was "missing".
    assert svc._ignore_matcher is None

    listing = _paths(svc.list_files())
    # .gitignore NOT applied (pathspec missing) → debug.log IS listed.
    assert "app.py" in listing
    assert "debug.log" in listing
    # IGNORED_DIRS base layer still works.
    assert not any(p.startswith("__pycache__") for p in listing)


# ── 6. Root-anchored pattern ───────────────────────────────────────


def test_root_anchored_pattern(monkeypatch, tmp_path):
    """A leading-slash pattern (``/out``) only matches at the root.

    NOTE: the design spec suggested ``/build`` here, but ``build`` is in
    IGNORED_DIRS, so a nested ``sub/build/`` would be excluded by the
    base layer regardless of gitignore — making the anchoring assertion
    impossible. We use ``out`` (not in IGNORED_DIRS) to isolate the
    root-anchored gitignore semantics, which is the test's actual intent.
    """
    (tmp_path / ".gitignore").write_text("/out\n", encoding="utf-8")

    root_out = tmp_path / "out"
    root_out.mkdir()
    (root_out / "file.txt").write_text("root out\n", encoding="utf-8")

    sub = tmp_path / "sub"
    sub.mkdir()
    nested_out = sub / "out"
    nested_out.mkdir()
    (nested_out / "file.txt").write_text("nested out\n", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    listing = _paths(svc.list_files())

    # Root out/ is excluded by the anchored pattern.
    assert "out/file.txt" not in listing
    # Nested sub/out/ is NOT matched by a root-anchored pattern.
    assert "sub/out/file.txt" in listing


# ── 7. _collect_watched_dirs respects .gitignore ──────────────────


def test_collect_watched_dirs_respects_gitignore(monkeypatch, tmp_path):
    """The watcher must NOT add .gitignore-ignored directories.

    ``_collect_watched_dirs`` feeds ``QFileSystemWatcher.addPaths``. If
    an ignored directory (e.g. ``build/``-style) were watched, every
    change inside it would trigger a tree rescan — wasted work plus
    potential noise from generated files. This test exercises the
    .gitignore pruning in ``_collect_watched_dirs`` directly (it is
    otherwise only reachable through the Qt-dependent ``_start_watcher``).
    """
    # Use a dir name that is NOT in IGNORED_DIRS so the only thing
    # excluding it is the .gitignore layer.
    (tmp_path / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "out.txt").write_text("x\n", encoding="utf-8")
    # A nested dir inside generated/ — must also be absent from the
    # watched list (os.walk must not descend into ignored dirs).
    (generated / "deep").mkdir()
    (generated / "deep" / "f.txt").write_text("y\n", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    watched = set(svc._collect_watched_dirs())

    assert str(tmp_path) in watched
    assert str(tmp_path / "app") in watched
    # The ignored dir and anything beneath it must NOT be watched.
    assert str(generated) not in watched
    assert str(generated / "deep") not in watched
    assert not any(p.startswith(str(generated)) for p in watched)


# ── 8. Non-anchored pattern matches nested files ──────────────────


def test_gitignore_excludes_nested_files(monkeypatch, tmp_path):
    """A non-anchored ``*.log`` excludes .log files at ANY depth.

    The root-level case (test_gitignore_excludes_files) only exercises
    ``rel_path == "debug.log"``. This test puts the ignored file one
    level deep (``subdir/error.log``) so the rel-path normalization in
    ``_is_ignored`` (``os.sep``→``/``) and the ``os.path.relpath`` glue
    in ``_iter_files`` are validated at depth — guarding against a
    regression where a nested ignored file slips through because the
    matcher is fed a wrong (e.g. basename-only) path.
    """
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "app.py").write_text("debug = True  # a debug flag\n", encoding="utf-8")
    (sub / "error.log").write_text("ERROR debug\n", encoding="utf-8")

    svc = _make_service(monkeypatch, tmp_path)
    listing = _paths(svc.list_files())

    assert "subdir/app.py" in listing
    assert "subdir/error.log" not in listing

    # search() goes through _iter_files → must also skip the nested log.
    results = svc.search("debug")
    result_paths = {r["path"] for r in results}
    assert "subdir/app.py" in result_paths
    assert "subdir/error.log" not in result_paths
