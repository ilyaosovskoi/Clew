"""
Task-list parser for the Clew Builder.

Two formats are accepted:

1. Plain list (one task per non-empty line) — quick mode:

       Virtual 1M+ Context Module
       Inline Edit (Cmd+K Analog)
       Smart Real-time Search

2. Rich format with title + success criteria. Entries are separated by
   a blank line; the first line of each entry is the title; subsequent
   lines starting with '- ' are success criteria:

       ## Virtual 1M+ Context Module
       - import clew succeeds
       - context_window field plumbed through to AgentRuntime

       ## Inline Edit (Cmd+K Analog)
       - new function edit_selection() exists in tool_engine
       - round-trips through diff review

Lines starting with '#' (other than '## ') are comments and skipped.
Empty tasks (no title) are dropped. Duplicates are kept — the user may
intentionally run the same task multiple times.

Output: a TaskList of Task(title, success_criteria, raw, line_no).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class Task:
    """A single self-improvement task.

    Attributes:
        title: human-readable task name (one line).
        success_criteria: list of strings — observable pass conditions.
            Empty list means "agent decides when done".
        raw: original text block, useful for the planner prompt.
        line_no: 1-indexed line in the source file (for references in logs).
    """
    title: str
    success_criteria: List[str] = field(default_factory=list)
    raw: str = ""
    line_no: int = 0

    @property
    def slug(self) -> str:
        """URL-safe slug derived from the title (used in branch names + paths)."""
        s = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        s = re.sub(r"-{2,}", "-", s)
        return s[:50] or "task"


@dataclass
class TaskList:
    tasks: List[Task]

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks)

    def __getitem__(self, idx: int) -> Task:
        return self.tasks[idx]


_SECTION_HEADER = re.compile(r"^##\s+(.+)$")


def parse_task_file(path: str | Path) -> TaskList:
    """Parse a task file from disk.

    Raises FileNotFoundError if the file does not exist; ValueError if it
    contains no parseable tasks.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_task_text(text)


def parse_task_text(text: str) -> TaskList:
    """Parse tasks from a string. See module docstring for the grammar."""
    tasks: List[Task] = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip blank lines and comments (# but not ##).
        if not stripped:
            i += 1
            continue
        if stripped.startswith("#") and not stripped.startswith("## "):
            i += 1
            continue

        # Rich entry: starts with "## <title>".
        m = _SECTION_HEADER.match(stripped)
        if m:
            title = m.group(1).strip()
            start_line = i + 1
            i += 1
            criteria: List[str] = []
            raw_lines = [f"## {title}"]
            while i < len(lines):
                inner = lines[i]
                inner_s = inner.strip()
                if not inner_s:
                    # blank line ends the entry (but allow multiple blanks
                    # inside criteria by peeking ahead).
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("- "):
                        raw_lines.append(inner)
                        i += 1
                        continue
                    break
                if inner_s.startswith("## "):
                    break
                if inner_s.startswith("#"):
                    break
                if inner_s.startswith("- "):
                    criteria.append(inner_s[2:].strip())
                    raw_lines.append(inner)
                else:
                    # Non-bullet text inside an entry — fold into raw but
                    # don't treat as a criterion.
                    raw_lines.append(inner)
                i += 1
            tasks.append(Task(
                title=title,
                success_criteria=criteria,
                raw="\n".join(raw_lines),
                line_no=start_line,
            ))
            continue

        # Plain-list entry: the whole non-empty line is the title.
        # Skip leading list markers if the user wrote "- task".
        title = re.sub(r"^[-*]\s+", "", stripped)
        if title:
            tasks.append(Task(
                title=title,
                success_criteria=[],
                raw=title,
                line_no=i + 1,
            ))
        i += 1

    if not tasks:
        raise ValueError(
            "task file contains no parseable tasks — every non-empty, "
            "non-comment line is treated as a task title"
        )
    return TaskList(tasks=tasks)
