"""
Clew v1.0.1 — Code Viewer Service.

Backs the right-hand "Code" panel in the HTML frontend.
Reads files from disk, watches for changes, and exposes a small API
the web bridge can call:

    list_files(root) → [{path, name, section, status, lines}, ...]
    read_file(path)  → {path, content, language, lines}
    search(pattern)  → [{path, line, text}, ...]
    watch(root, cb)  → notifies on file changes

Designed to be safe: paths are sandboxed to the project root,
no symlinks above root, no writes from this module.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class FileEntry:
    path: str
    name: str
    section: str               # "App" | "Tests" | "Root" — derived from top dir
    status: str = ""           # "" | "created" | "modified" | "deleted"
    lines: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path":    self.path,
            "name":    self.name,
            "section": self.section,
            "status":  self.status,
            "lines":   self.lines,
        }


@dataclass
class FileContent:
    path: str
    content: str
    language: str
    lines: int
    exists: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path":     self.path,
            "content":  self.content,
            "language": self.language,
            "lines":    self.lines,
            "exists":   self.exists,
        }


@dataclass
class SearchResult:
    path: str
    line: int
    text: str
    match_start: int
    match_end: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path":        self.path,
            "line":        self.line,
            "text":        self.text,
            "match_start": self.match_start,
            "match_end":   self.match_end,
        }


# ── CodeViewer service ─────────────────────────────────────────────

# Files we never want to show in the tree (kept minimal — agent's job is to be transparent)
IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    "node_modules", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".eggs", ".tox", ".cache",
}
IGNORED_FILES = {
    ".DS_Store", "Thumbs.db",
}
MAX_FILE_SIZE = 256 * 1024    # 256 KB — anything bigger is shown as "binary/large"


class CodeViewerService:
    """Read-only file browser for the project root."""

    def __init__(self, root: Optional[str] = None):
        self._root: Optional[Path] = Path(root).resolve() if root else None
        self._watcher = None
        self._watch_callback: Optional[Callable[[str, str], None]] = None

    # ── Root management ───────────────────────────────────────────

    def set_root(self, root: str) -> None:
        new_root = Path(root).expanduser().resolve()
        if not new_root.exists():
            raise FileNotFoundError(f"Project root does not exist: {new_root}")
        self._root = new_root
        logger.info(f"[code_viewer] root = {self._root}")
        self._start_watcher()

    @property
    def root(self) -> Optional[Path]:
        return self._root

    # ── Listing ───────────────────────────────────────────────────

    def list_files(self) -> List[Dict[str, Any]]:
        """Return a flat list of files in the project root, grouped by section."""
        if not self._root:
            return []

        entries: List[FileEntry] = []

        # Walk top-level dirs first, then root files
        try:
            for entry in sorted(self._root.iterdir()):
                if entry.name in IGNORED_DIRS or entry.name in IGNORED_FILES:
                    continue
                if entry.is_dir():
                    entries.extend(self._scan_dir(entry, entry.name.capitalize()))
                elif entry.is_file():
                    entries.append(self._make_entry(entry, "Root"))
        except PermissionError as e:
            logger.warning(f"[code_viewer] permission error scanning root: {e}")

        return [e.to_dict() for e in entries]

    def _scan_dir(self, dir_path: Path, section: str) -> List[FileEntry]:
        out: List[FileEntry] = []
        try:
            for entry in sorted(dir_path.iterdir()):
                if entry.name in IGNORED_DIRS or entry.name in IGNORED_FILES:
                    continue
                if entry.is_dir():
                    out.extend(self._scan_dir(entry, section))
                elif entry.is_file():
                    out.append(self._make_entry(entry, section))
        except (PermissionError, OSError) as e:
            logger.warning(f"[code_viewer] error scanning {dir_path}: {e}")
        return out

    def _make_entry(self, file_path: Path, section: str) -> FileEntry:
        rel = str(file_path.relative_to(self._root))
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        # Quick line count — capped so we don't read 50 MB files just to count
        lines = 0
        if size < MAX_FILE_SIZE:
            try:
                with open(file_path, "rb") as f:
                    lines = sum(1 for _ in f)
            except (OSError, UnicodeDecodeError):
                lines = 0
        return FileEntry(
            path=rel,
            name=file_path.name,
            section=section,
            status="",
            lines=lines,
        )

    # ── Reading ───────────────────────────────────────────────────

    def read_file(self, rel_path: str) -> Dict[str, Any]:
        if not self._root:
            return FileContent(rel_path, "", "text", 0, exists=False).to_dict()

        abs_path = self._resolve_safe(rel_path)
        if not abs_path or not abs_path.exists():
            return FileContent(rel_path, "", "text", 0, exists=False).to_dict()

        try:
            size = abs_path.stat().st_size
            if size > MAX_FILE_SIZE:
                return FileContent(
                    rel_path,
                    f"# File too large to preview ({size // 1024} KB).\n# Open in external editor.\n",
                    "text", 2, exists=True,
                ).to_dict()

            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            logger.warning(f"[code_viewer] read error: {e}")
            return FileContent(rel_path, f"# Error reading file: {e}\n", "text", 1, exists=True).to_dict()

        lines = content.count("\n") + (0 if content.endswith("\n") else 1)
        return FileContent(
            rel_path,
            content,
            self._detect_language(abs_path.name),
            lines,
            exists=True,
        ).to_dict()

    def _resolve_safe(self, rel_path: str) -> Optional[Path]:
        """Resolve a relative path under root, blocking path traversal."""
        candidate = (self._root / rel_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            logger.warning(f"[code_viewer] path traversal blocked: {rel_path}")
            return None
        return candidate

    @staticmethod
    def _detect_language(filename: str) -> str:
        ext = Path(filename).suffix.lower()
        return {
            ".py":   "python",
            ".js":   "javascript",
            ".ts":   "typescript",
            ".tsx":  "tsx",
            ".jsx":  "jsx",
            ".md":   "markdown",
            ".markdown": "markdown",
            ".json": "json",
            ".toml": "toml",
            ".yaml": "yaml",
            ".yml":  "yaml",
            ".html": "html",
            ".css":  "css",
            ".scss": "scss",
            ".rs":   "rust",
            ".go":   "go",
            ".java": "java",
            ".kt":   "kotlin",
            ".swift":"swift",
            ".c":    "c",
            ".cpp":  "cpp",
            ".h":    "c",
            ".sh":   "bash",
            ".bash": "bash",
            ".zsh":  "bash",
            ".sql":  "sql",
            ".txt":  "text",
            ".env":  "ini",
            ".ini":  "ini",
            ".cfg":  "ini",
        }.get(ext, "text")

    # ── Search ────────────────────────────────────────────────────

    def search(self, pattern: str, *, regex: bool = False, max_results: int = 200) -> List[Dict[str, Any]]:
        """Grep through project files for `pattern`."""
        if not self._root or not pattern:
            return []

        results: List[SearchResult] = []
        try:
            compiled = re.compile(pattern) if regex else None
            needle = pattern.lower() if not regex else None
        except re.error as e:
            logger.warning(f"[code_viewer] bad regex: {e}")
            return []

        for path in self._iter_files():
            if len(results) >= max_results:
                break
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, start=1):
                        if regex:
                            m = compiled.search(line)
                            if m:
                                results.append(SearchResult(
                                    path=str(path.relative_to(self._root)),
                                    line=i,
                                    text=line.rstrip()[:300],
                                    match_start=m.start(),
                                    match_end=m.end(),
                                ))
                                if len(results) >= max_results:
                                    break
                        else:
                            idx = line.lower().find(needle)
                            if idx >= 0:
                                results.append(SearchResult(
                                    path=str(path.relative_to(self._root)),
                                    line=i,
                                    text=line.rstrip()[:300],
                                    match_start=idx,
                                    match_end=idx + len(pattern),
                                ))
                                if len(results) >= max_results:
                                    break
            except OSError:
                continue

        return [r.to_dict() for r in results]

    def _iter_files(self):
        for root, dirs, files in os.walk(self._root):
            # prune ignored dirs in-place
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in files:
                if name in IGNORED_FILES:
                    continue
                p = Path(root) / name
                # v1.0.6: catch OSError on stat() — file may have been
                # deleted between os.walk and stat (M-AUTO-4).
                try:
                    if p.stat().st_size < MAX_FILE_SIZE:
                        yield p
                except OSError:
                    continue

    # ── File watching ─────────────────────────────────────────────

    def watch(self, callback: Callable[[str, str], None]) -> None:
        """Register a callback(path, event_type) for file changes."""
        self._watch_callback = callback
        self._start_watcher()

    def _start_watcher(self) -> None:
        if not self._root or not self._watch_callback:
            return
        try:
            from PySide6.QtCore import QFileSystemWatcher
            if self._watcher is None:
                self._watcher = QFileSystemWatcher()
                self._watcher.directoryChanged.connect(
                    lambda p: self._on_watcher_directory_changed(p)
                )
                self._watcher.fileChanged.connect(
                    lambda p: self._watch_callback(p, "file")
                )

            # v1.1.5-fix (clew_bug_report.md bug #12): QFileSystemWatcher
            # is NOT recursive — it only fires for paths explicitly added
            # via addPaths(). The old code added the project root plus
            # its IMMEDIATE subdirectories only, so any file change inside
            # `src/components/`, `app/models/user/`, etc. was silently
            # ignored — meaning external edits (e.g. in VS Code) to any
            # file 2+ levels deep didn't refresh Clew's file tree.
            #
            # Fix: walk the entire tree (respecting IGNORED_DIRS) and
            # add every directory. We cap the number of watched dirs to
            # avoid exhausting OS file-descriptor limits on huge repos
            # (Linux default is 8192 inotify watches per user; macOS
            # ~256 per path by default; Windows has no hard per-path
            # limit but a sane cap is still a good idea).
            paths = self._collect_watched_dirs()
            if paths:
                self._watcher.addPaths(paths)
            logger.info(
                "[code_viewer] watching %d directories under %s",
                len(paths), self._root,
            )
        except ImportError:
            logger.warning("[code_viewer] PySide6 not available — watcher disabled")
        except Exception as e:
            logger.warning(f"[code_viewer] watcher setup failed: {e}")

    # Cap on the number of directories we'll add to QFileSystemWatcher.
    # Picked to stay comfortably under the default Linux inotify limit
    # (~8192 watches per user) while still covering realistic projects.
    # If a project has more dirs than this, deeper dirs simply won't
    # be watched — same behaviour as before the fix, but for a much
    # higher threshold.
    MAX_WATCHED_DIRS = 4096

    def _collect_watched_dirs(self) -> List[str]:
        """v1.1.5 — recursively collect all directories under ``_root``
        that should be watched, respecting ``IGNORED_DIRS`` and the
        ``MAX_WATCHED_DIRS`` cap.

        Used by ``_start_watcher`` to feed ``QFileSystemWatcher.addPaths``
        a complete list instead of just the top-level (which was bug #12:
        changes inside nested subdirectories were never observed).
        """
        if not self._root:
            return []
        out: List[str] = [str(self._root)]
        try:
            for root, dirs, _files in os.walk(self._root):
                # prune ignored dirs in-place (os.walk mutates `dirs`)
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
                for d in dirs:
                    out.append(os.path.join(root, d))
                    if len(out) >= self.MAX_WATCHED_DIRS:
                        return out
        except (PermissionError, OSError) as e:
            logger.warning(f"[code_viewer] partial walk failure during watch setup: {e}")
        return out

    def _on_watcher_directory_changed(self, path: str) -> None:
        """v1.1.5 — handle a directory change event.

        Forwards the event to the user callback (unchanged behaviour),
        but ALSO re-scans the directory: if a NEW subdirectory was just
        created inside it (e.g. the user ran `mkdir src/new_module`),
        QFileSystemWatcher didn't know about it before and won't fire
        for changes inside it. We add the new subdir to the watcher
        so future changes are picked up.
        """
        # Forward to the user-supplied callback first — same as before.
        try:
            self._watch_callback(path, "directory")
        except Exception:
            pass

        # v1.1.5-fix (bug #12): pick up newly-created subdirectories.
        if self._watcher is None:
            return
        try:
            from pathlib import Path as _P
            changed = _P(path)
            if not changed.is_dir():
                return
            # Build the set of paths the watcher already knows about
            # (QFileSystemWatcher.directories() returns a QStringList).
            try:
                already_watched = set(self._watcher.directories())
            except Exception:
                already_watched = set()
            new_paths: List[str] = []
            try:
                for entry in changed.iterdir():
                    if not entry.is_dir():
                        continue
                    if entry.name in IGNORED_DIRS:
                        continue
                    sp = str(entry)
                    if sp not in already_watched:
                        new_paths.append(sp)
            except (PermissionError, OSError):
                pass
            if new_paths:
                try:
                    self._watcher.addPaths(new_paths)
                    logger.debug(
                        "[code_viewer] watcher: added %d new subdir(s) under %s",
                        len(new_paths), path,
                    )
                except Exception as e:
                    logger.debug(f"[code_viewer] addPaths failed for new subdirs: {e}")
        except Exception as e:
            logger.debug(f"[code_viewer] watcher directory-changed handler error: {e}")

    def stop_watcher(self) -> None:
        if self._watcher:
            try:
                self._watcher.deleteLater()
            except Exception:
                pass
            self._watcher = None
