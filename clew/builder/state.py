"""
Builder state — persistent JSON record of which tasks have been attempted,
what happened, and what to resume from.

State file lives at <workspace>/.clew/builder_state.json by default.
Each task gets a list of TaskAttempt records (one per retry). The
outermost status field is the "current" status — PENDING / IN_PROGRESS /
DONE / FAILED / SKIPPED.

Resume semantics:

* `--continue` (default): load the state file, skip tasks already DONE,
  resume IN_PROGRESS from the start of that task (we don't try to
  resume mid-task — too fragile).
* `--restart`: ignore the state file, start fresh. Existing reports
  are NOT deleted — they're just overwritten if the same task runs
  again.
* `--fresh-state`: delete the state file before starting (equivalent
  to --restart but also clears IN_PROGRESS markers).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskAttempt:
    """A single attempt at a task. A task may have many (retries)."""
    attempt_number: int
    started_at: str  # ISO 8601
    ended_at: Optional[str] = None
    branch: str = ""
    plan: str = ""
    files_changed: List[str] = field(default_factory=list)
    verification_passed: bool = False
    verification_output: str = ""
    error: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    model_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskAttempt":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class TaskRecord:
    """All attempts at a single task, plus its current status."""
    title: str
    slug: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: List[TaskAttempt] = field(default_factory=list)
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "slug": self.slug,
            "status": self.status.value,
            "attempts": [a.to_dict() for a in self.attempts],
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskRecord":
        return cls(
            title=d["title"],
            slug=d["slug"],
            status=TaskStatus(d.get("status", "pending")),
            attempts=[TaskAttempt.from_dict(a) for a in d.get("attempts", [])],
            last_error=d.get("last_error"),
        )


class BuilderState:
    """Persistent state manager. Thread-safe; one per workspace."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._records: Dict[str, TaskRecord] = {}  # keyed by slug
        self._load()

    # ── Persistence ───────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for rec_dict in data.get("tasks", []):
                rec = TaskRecord.from_dict(rec_dict)
                self._records[rec.slug] = rec
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[builder-state] failed to load %s: %s — starting fresh", self._path, e)

    def save(self) -> None:
        """Atomically persist the state."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
                "tasks": [r.to_dict() for r in self._records.values()],
            }
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)

    # ── Task lifecycle ────────────────────────────────────────────

    def get_or_create(self, title: str, slug: str) -> TaskRecord:
        with self._lock:
            rec = self._records.get(slug)
            if rec is None:
                rec = TaskRecord(title=title, slug=slug)
                self._records[slug] = rec
            return rec

    def begin_attempt(self, slug: str, branch: str) -> TaskAttempt:
        """Mark a task as IN_PROGRESS and start a new attempt record."""
        with self._lock:
            rec = self._records[slug]
            rec.status = TaskStatus.IN_PROGRESS
            attempt = TaskAttempt(
                attempt_number=len(rec.attempts) + 1,
                started_at=datetime.datetime.utcnow().isoformat() + "Z",
                branch=branch,
            )
            rec.attempts.append(attempt)
            return attempt

    def finish_attempt(
        self,
        slug: str,
        attempt: TaskAttempt,
        *,
        success: bool,
        files_changed: Optional[List[str]] = None,
        verification_output: str = "",
        error: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        model_used: str = "",
    ) -> None:
        with self._lock:
            rec = self._records[slug]
            attempt.ended_at = datetime.datetime.utcnow().isoformat() + "Z"
            attempt.verification_passed = success
            attempt.verification_output = verification_output[:8000]  # cap
            attempt.error = error
            attempt.tokens_in = tokens_in
            attempt.tokens_out = tokens_out
            attempt.model_used = model_used
            if files_changed is not None:
                attempt.files_changed = files_changed
            rec.status = TaskStatus.DONE if success else TaskStatus.FAILED
            rec.last_error = error
            self.save()

    def mark_skipped(self, slug: str, reason: str) -> None:
        with self._lock:
            rec = self._records.get(slug)
            if rec is None:
                rec = TaskRecord(title=slug, slug=slug)
                self._records[slug] = rec
            rec.status = TaskStatus.SKIPPED
            rec.last_error = reason
            self.save()

    # ── Introspection ────────────────────────────────────────────

    def is_done(self, slug: str) -> bool:
        with self._lock:
            rec = self._records.get(slug)
            return rec is not None and rec.status == TaskStatus.DONE

    def attempts_used(self, slug: str) -> int:
        with self._lock:
            rec = self._records.get(slug)
            return len(rec.attempts) if rec else 0

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            counts: Dict[str, int] = {}
            for r in self._records.values():
                counts[r.status.value] = counts.get(r.status.value, 0) + 1
            return {
                "total_tasks": len(self._records),
                "by_status": counts,
                "state_path": str(self._path),
            }

    def failed_tasks(self) -> List[TaskRecord]:
        """Return all FAILED task records (for the final report)."""
        with self._lock:
            return [r for r in self._records.values() if r.status == TaskStatus.FAILED]

    def all_records(self) -> List[TaskRecord]:
        with self._lock:
            return list(self._records.values())
