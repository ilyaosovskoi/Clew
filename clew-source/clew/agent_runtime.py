"""
Agent Runtime for Clew v1.0.4
ReAct-style autonomous agent with tool-use loop.
Security: shell=False + shlex.split() + command whitelist.
JSON-based tool calling (no XML regex).
Async: AgentWorker(QThread) for non-blocking UI.
Persistence: ContextMemory save/load via JSON.

v1.0.4: Diff-review before write_file — agent pauses and asks UI
for apply/reject instead of writing immediately.
"""

import base64
import difflib
import hashlib
import json
import logging
import os
import re
import subprocess
import shlex
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from clew.providers import (
    ProviderRegistry, ProviderMessage,
)
from clew.project_context import get_project_context
from clew.context_manager import get_context_manager
from clew.skill_loader import load_all_skills_with_builtins, build_skill_catalog

logger = logging.getLogger(__name__)


# ── Security: Command Whitelist ──────────────────────────────────────────

ALLOWED_COMMANDS = {
    "python3", "python", "node", "npm", "pip", "git", "ls", "cat", "head",
    "tail", "find", "grep", "wc", "mkdir", "touch", "cp", "mv", "rm",
    "pytest", "black", "isort", "flake8", "mypy", "ruff",
}


def _sanitize_command(command: str) -> Tuple[List[str], bool]:
    """
    Parse and validate a shell command.
    Returns (args_list, is_safe).
    Rejects shell=True, pipes, redirects, and disallowed binaries.
    """
    # Reject dangerous metacharacters
    dangerous = {";", "&&", "||", "|", ">", "<", "`", "$", "\n"}
    if any(d in command for d in dangerous):
        logger.warning(f"[security] Dangerous metacharacters in command: {command}")
        return [], False

    try:
        args = shlex.split(command)
    except ValueError as e:
        logger.warning(f"[security] Failed to parse command: {e}")
        return [], False

    if not args:
        return [], False

    # Check if the base command is in whitelist
    base_cmd = os.path.basename(args[0])
    if base_cmd not in ALLOWED_COMMANDS:
        logger.warning(f"[security] Command not in whitelist: {base_cmd}")
        return [], False

    # v1.0.6-security: block dangerous interpreter flags that allow
    # arbitrary code execution despite shell=False (C-RT-2).
    # python3 -c "..." / node -e "..." allow running arbitrary code
    # pip install / npm install download and execute arbitrary packages
    # git clone can exfiltrate data to remote repos
    _DANGEROUS_FLAGS = {
        "python3": {"-c", "-m"},
        "python": {"-c", "-m"},
        "node": {"-e", "-e", "--eval"},
        "pip": {"install", "uninstall"},
        "npm": {"install", "uninstall", "run"},
        "git": {"clone", "push", "pull", "fetch", "remote"},
    }
    dangerous_flags = _DANGEROUS_FLAGS.get(base_cmd)
    if dangerous_flags:
        for arg in args[1:]:
            if arg in dangerous_flags:
                logger.warning(
                    "[security] Dangerous flag %r for %r blocked: %s",
                    arg, base_cmd, command,
                )
                return [], False

    return args, True


# ── Enums & Dataclasses ──────────────────────────────────────────────────

class TaskType(Enum):
    WRITE = "write"
    EDIT = "edit"
    REFACTOR = "refactor"
    TEST = "test"
    ANALYZE = "analyze"
    DEBUG = "debug"
    PLAN = "plan"
    CHAT = "chat"
    AGENTIC = "agentic"


class ToolName(Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_CODE = "run_code"
    SEARCH_PROJECT = "search_project"
    LIST_FILES = "list_files"
    APPLY_DIFF = "apply_diff"
    EXECUTE_COMMAND = "execute_command"
    GET_PROJECT_STRUCTURE = "get_project_structure"
    DELETE_FILE = "delete_file"
    RENAME_FILE = "rename_file"
    MKDIR = "mkdir"
    READ_BINARY_FILE = "read_binary_file"
    WRITE_BINARY_FILE = "write_binary_file"
    FILE_INFO = "file_info"
    UNDO_WRITE = "undo_write"
    # v1.0.5: targeted string replacement — preferred over full
    # write_file for edits (per качество_кода_llm.md §3.1). Forces the
    # model to localise the change instead of rewriting the whole file,
    # and gives a deterministic verification: either old_str is found
    # (patch applies cleanly) or it is not (model hallucinated context).
    STR_REPLACE = "str_replace"
    # v1.0.11: git tools — direct project access like Claude Code.
    # The agent can check git status, see diffs, stage files, and
    # commit. This makes Clew closer to an autonomous dev assistant
    # than a chat bot — the user says "commit my changes" and the
    # agent does it directly via the git_status / git_diff / git_commit
    # tools, without asking the user to run commands manually.
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    GIT_STAGE = "git_stage"
    GIT_COMMIT = "git_commit"
    # v1.0.11: skill tools — the agent can request the full body of a
    # skill by id. The skill catalog (id + name + description) is
    # injected into the system prompt so the agent knows what's
    # available. When it decides a skill fits the task, it calls
    # get_skill to pull the full instructions into context.
    GET_SKILL = "get_skill"
    # v1.1.0: MCP — call an external MCP server tool (filesystem,
    # github, browser, etc.). Available in ALL sections (general,
    # heavy_code, office) — the catalog is injected into the system
    # prompt dynamically by MCPManager.catalog_prompt().
    CALL_MCP_TOOL = "call_mcp_tool"
    # v1.1.0: Multi-agent — spawn a sub-agent for a sub-task. The
    # sub-agent runs in its own AgentRuntime instance with a narrower
    # scope (read-only by default) and returns its final answer as the
    # observation. Available in Heavy Code section.
    SPAWN_SUBAGENT = "spawn_subagent"
    # v1.1.0: Multi-agent parallel — spawn N sub-agents in parallel
    # for independent sub-tasks (e.g. "refactor these 3 files in
    # parallel"). Returns each sub-agent's result. Available in Heavy
    # Code section.
    SPAWN_MULTI_AGENTS = "spawn_multi_agents"


class AgentEvent(Enum):
    PLAN_CREATED = "plan_created"
    STEP_STARTED = "step_started"
    STEP_DONE = "step_done"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    ITERATION_START = "iteration_start"
    ITERATION_END = "iteration_end"
    THOUGHT = "thought"
    ERROR = "error"
    DONE = "done"


@dataclass
class Task:
    type: TaskType
    description: str
    context: Optional[str] = None
    file_path: Optional[str] = None
    language: str = "python"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    name: ToolName
    args: Dict[str, Any]
    result: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class AgentStep:
    thought: str
    action: Optional[ToolCall] = None
    observation: str = ""
    is_final: bool = False


@dataclass
class TaskResult:
    success: bool
    output: str
    error: Optional[str] = None
    iterations: int = 0
    steps: List[AgentStep] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    plan: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # v1.1.3-fix (bug 1.12): removed the unused code_blocks / primary_code
    # properties. They were defined but never read by _run_agent_loop or
    # any caller — dead code that just cluttered the class. If a future
    # UI wants to highlight code blocks in the result, it can parse them
    # from ``output`` directly.


@dataclass
class ConversationMessage:
    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "content": self.content, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationMessage":
        return cls(
            role=data["role"],
            content=data["content"],
            metadata=data.get("metadata", {}),
        )


# ── Context Memory (with Persistence) ──────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English/code,
    ~2 chars per token for CJK. We use a blended 3.5 chars/token
    approximation which is close enough for budgeting purposes
    without pulling in a full tokenizer."""
    if not text:
        return 0
    # Count CJK characters (they tokenize denser)
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or
              '\u0400' <= c <= '\u04ff')  # also Cyrillic
    non_cjk = len(text) - cjk
    return (cjk // 2) + (non_cjk // 4) + 1


class ContextMemory:
    """Sliding-window conversation memory with JSON save/load.

    v1.0.9: now tracks token estimates (not just char count) and supports
    explicit compaction via the /compact command. Auto-compaction kicks
    in when the context approaches the configured token budget, so the
    agent doesn't silently lose early details.
    """

    def __init__(self, max_messages: int = 20, max_chars: int = 12000,
                 max_tokens: int = 8000,
                 persist_path: Optional[str] = None):
        self.messages: List[ConversationMessage] = []
        self.max_messages = max_messages
        self.max_chars = max_chars
        self.max_tokens = max_tokens  # v1.0.9
        self.persist_path = Path(persist_path) if persist_path else None
        # v1.0.6: lock for thread-safe message list mutations (M-RT-1).
        self._lock = threading.Lock()
        # v1.0.9: compaction summary — if set, prepended to prompt history
        # so the agent retains key decisions after old messages are dropped.
        self.compaction_summary: str = ""

    def add(self, role: str, content: str, **meta):
        with self._lock:
            self.messages.append(ConversationMessage(role=role, content=content, metadata=meta))
        self._trim()
        self.save()

    def _trim(self):
        """Drop oldest messages until under all three limits.

        v1.0.9: the token limit is now the primary constraint. When we
        trim, we keep a rolling window of the most recent messages
        plus any compaction summary, so early decisions aren't lost
        without a trace.
        """
        with self._lock:
            while len(self.messages) > self.max_messages:
                self.messages.pop(0)
            while self._total_chars() > self.max_chars and len(self.messages) > 1:
                self.messages.pop(0)
            # v1.0.9: token-based trim
            while self._total_tokens() > self.max_tokens and len(self.messages) > 1:
                self.messages.pop(0)

    def _total_chars(self) -> int:
        return sum(len(m.content) for m in self.messages)

    def _total_tokens(self) -> int:
        """v1.0.9: estimated token count of all messages + summary."""
        total = _estimate_tokens(self.compaction_summary)
        for m in self.messages:
            total += _estimate_tokens(m.content)
        return total

    def token_breakdown(self) -> Dict[str, int]:
        """v1.0.9: per-message token counts for the /context command."""
        breakdown: Dict[str, int] = {}
        if self.compaction_summary:
            breakdown["__compaction_summary__"] = _estimate_tokens(self.compaction_summary)
        for i, m in enumerate(self.messages):
            label = f"{i:03d}_{m.role}"
            breakdown[label] = _estimate_tokens(m.content)
        return breakdown

    def should_compact(self, threshold: float = 0.85) -> bool:
        """v1.0.9: return True if context is over `threshold` of budget.

        Called by the agent loop before each LLM call. If True, the loop
        triggers auto-compaction (summarise old messages, keep only the
        most recent few + the summary).
        """
        if self.max_tokens <= 0:
            return False
        return self._total_tokens() > int(self.max_tokens * threshold)

    def compact(self, summary: str, keep_recent: int = 4) -> None:
        """v1.0.9: replace old messages with a summary, keep the most recent.

        Called by:
          - the /compact command (user-initiated)
          - auto-compaction in the agent loop (when should_compact() is True)

        The summary is prepended to to_prompt_history() so the agent still
        has access to the key decisions from the dropped messages.

        v1.0.5-correctness: cap the compaction_summary size. Previously
        every compaction prepended the previous summary verbatim, so
        after N auto-compactions in a long session the summary was N×
        the size of a single summary. ``_trim()`` only trims
        ``self.messages``, not ``compaction_summary`` — so the summary
        could blow the token budget with no recourse. We now keep only
        the most recent summary and cap its size (BUGS_REPORT M-RT-5).
        """
        if keep_recent < 0:
            keep_recent = 0
        # v1.0.5-correctness: don't accumulate summaries verbatim — each
        # compaction produces a fresh summary that already incorporates
        # the prior context (the summariser sees the previous summary
        # via `to_prompt_history()`). Only keep the latest, and cap it.
        # If the caller's summary is empty (rare), fall back to the
        # previous one so we don't lose context.
        with self._lock:
            new_summary = summary or self.compaction_summary
            # Cap at a generous 4000 chars (~1000 tokens) so a runaway
            # summariser can't blow the budget.
            _MAX_SUMMARY_CHARS = 4000
            if len(new_summary) > _MAX_SUMMARY_CHARS:
                new_summary = new_summary[-_MAX_SUMMARY_CHARS:]
                logger.warning(
                    "[memory] compaction summary truncated to %d chars",
                    _MAX_SUMMARY_CHARS,
                )
            self.compaction_summary = new_summary
            # Keep only the most recent `keep_recent` messages
            if keep_recent == 0:
                self.messages = []
            elif len(self.messages) > keep_recent:
                self.messages = self.messages[-keep_recent:]
        self.save()
        logger.info(
            "[memory] compacted: kept %d recent messages, summary=%d chars",
            len(self.messages), len(self.compaction_summary),
        )

    def to_prompt_history(self) -> str:
        parts: List[str] = []
        # v1.0.9: prepend compaction summary if present
        if self.compaction_summary:
            parts.append(f"[COMPACTION SUMMARY]\n{self.compaction_summary}")
        for m in self.messages[-10:]:
            role_label = {"user": "USER", "assistant": "CLEW", "tool": "TOOL"}.get(m.role, m.role.upper())
            parts.append(f"[{role_label}]\n{m.content}")
        return "\n".join(parts)

    def clear(self):
        """v1.0.9: clear messages AND compaction summary (full reset)."""
        with self._lock:
            self.messages.clear()
            self.compaction_summary = ""
        self.save()

    def status(self) -> Dict[str, Any]:
        """v1.0.9: status dict for /context command."""
        return {
            "message_count": len(self.messages),
            "total_chars": self._total_chars(),
            "total_tokens": self._total_tokens(),
            "max_messages": self.max_messages,
            "max_chars": self.max_chars,
            "max_tokens": self.max_tokens,
            "compaction_summary_chars": len(self.compaction_summary),
            "compaction_summary_tokens": _estimate_tokens(self.compaction_summary),
            "utilization": (
                self._total_tokens() / self.max_tokens if self.max_tokens > 0 else 0.0
            ),
        }

    def save(self):
        """Persist memory to JSON. v1.0.9: also saves compaction_summary.

        v1.0.5-security: atomic write via tempfile + os.replace, so a crash
        (OOM, kill, power loss) mid-write can't leave a truncated/garbled
        JSON file (BUGS_REPORT H-RT-2). Previously `open(..., "w")`
        truncated first, then `json.dump` wrote; a crash between truncate
        and the end of `json.dump` would have left the persist file in an
        unrecoverable state, losing the entire conversation history.
        """
        if not self.persist_path:
            return
        import os as _os
        import tempfile as _tf
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "messages": [m.to_dict() for m in self.messages],
                "compaction_summary": self.compaction_summary,
            }
            payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            fd, tmp_path = _tf.mkstemp(prefix='.mem_', suffix='.tmp',
                                       dir=str(self.persist_path.parent))
            try:
                with _os.fdopen(fd, 'wb') as f:
                    f.write(payload)
                _os.replace(tmp_path, self.persist_path)
            except Exception:
                try: _os.unlink(tmp_path)
                except OSError: pass
                raise
        except Exception as e:
            logger.warning(f"[memory] Failed to save: {e}")

    def load(self):
        """Load memory from JSON. v1.0.9: also loads compaction_summary."""
        if not self.persist_path or not self.persist_path.exists():
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # v1.0.9: handle both old format (list) and new format (dict)
            if isinstance(data, list):
                # Old format — just messages
                self.messages = [ConversationMessage.from_dict(d) for d in data]
                self.compaction_summary = ""
            elif isinstance(data, dict):
                self.messages = [ConversationMessage.from_dict(d) for d in data.get("messages", [])]
                self.compaction_summary = data.get("compaction_summary", "")
            else:
                self.messages = []
                self.compaction_summary = ""
            self._trim()
            logger.info(f"[memory] Loaded {len(self.messages)} messages "
                        f"(summary: {len(self.compaction_summary)} chars)")
        except Exception as e:
            logger.warning(f"[memory] Failed to load: {e}")


# ── Tool Engine (Secure) ─────────────────────────────────────────────────

class ToolEngine:
    """Executes agent tool calls in a sandboxed environment."""

    RUN_TIMEOUT = 15
    MAX_OUTPUT = 2000

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self._allowed_dirs: List[Path] = [self.workspace]
        self._backup_dir = Path(tempfile.gettempdir()) / "clew_backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        # v1.0.6: cap the number of backup files to prevent unbounded
        # growth (M-RT-4). Oldest backups are deleted first.
        self._MAX_BACKUPS = 50
        # v1.0.4: diff-review support (write_file / str_replace only)
        self.diff_review_enabled: bool = False
        self._diff_review_callback: Optional[Callable] = None  # called from agent thread
        self._diff_review_event = threading.Event()
        self._diff_review_accepted: Optional[bool] = None
        # v1.1.1: generic action-confirmation gate for tools that are NOT
        # covered by diff-review (execute_command, delete_file,
        # rename_file, apply_diff, write_binary_file, git_stage,
        # git_commit). Controlled by `autonomy`:
        #   'always_ask'     — confirm before every one of these
        #   'new_files_only' — auto-approve only actions that create a
        #                      brand-new path; everything else asks
        #   'never_ask'      — never ask (previous, implicit default)
        self.autonomy: str = "always_ask"
        self._confirm_callback: Optional[Callable] = None
        self._confirm_event = threading.Event()
        self._confirm_accepted: Optional[bool] = None
        # v1.1.1: cooperative cancellation. AgentWorker hands us a
        # zero-arg callable that returns True once the user has clicked
        # Stop — we poll it between iterations AND while blocked waiting
        # on a diff-review/confirmation response, so Stop actually
        # interrupts a running agent instead of just muting UI updates.
        self._cancel_check: Optional[Callable[[], bool]] = None
        # v1.0.11: skills list — populated by AgentRuntime, used by
        # _get_skill() to return the full body of a requested skill.
        self._skills: List[Any] = []  # List[Skill] from skill_loader
        # v1.0.6: lock for workspace/allowed_dirs atomicity (M-RT-1).
        # Without this, concurrent set_workspace during an agent iteration
        # can cause RuntimeError (list changed size during iteration).
        self._workspace_lock = threading.Lock()
        # v1.1.0: section mirror — AgentRuntime.set_section() propagates
        # here so _dispatch can reject section-gated tools (spawn_subagent,
        # spawn_multi_agents) even if the model hallucinates a call.
        self.section: str = "general"
        # v1.1.3-fix (bug 1.4): role-based tool whitelist. When set (via
        # set_role_whitelist), _dispatch rejects any tool NOT in the set
        # with a "[TOOL DENIED]" message — even if the model ignores the
        # system prompt and emits a write_file/str_replace/delete_file
        # call for a "read-only" sub-agent role. None means "all tools
        # allowed" (the default for the parent agent).
        self.allowed_tools: Optional[set] = None

    def set_workspace(self, workspace: str) -> None:
        with self._workspace_lock:
            self.workspace = Path(workspace).resolve()
            self._allowed_dirs = [self.workspace]

    def add_allowed_dir(self, path: str):
        with self._workspace_lock:
            self._allowed_dirs.append(Path(path).resolve())

    def set_skills(self, skills: List[Any]) -> None:
        """v1.0.11: inject the skill list so _get_skill can resolve ids."""
        self._skills = skills or []

    # v1.1.3-fix (bug 1.4): role-based tool whitelist.
    ROLE_TOOL_WHITELIST: Dict[str, set] = {
        "architect": {
            "read_file", "list_files", "search_project",
            "get_project_structure", "git_status", "git_diff", "get_skill",
            "file_info", "read_binary_file",
        },
        "reviewer": {
            "read_file", "list_files", "search_project",
            "git_diff", "get_skill", "file_info", "read_binary_file",
        },
        "tester": {
            "read_file", "write_file", "run_code",
            "git_status", "get_skill", "list_files", "search_project",
            "file_info",
        },
        "implementer": {
            "read_file", "write_file", "str_replace", "mkdir",
            "run_code", "git_status", "git_diff", "git_stage", "git_commit",
            "get_skill", "list_files", "search_project",
            "get_project_structure", "file_info", "undo_write",
        },
        "generalist": {
            "read_file", "list_files", "search_project",
            "get_skill", "file_info", "get_project_structure",
            "read_binary_file", "git_status", "git_diff",
        },
    }

    def set_role_whitelist(self, role: str) -> None:
        """v1.1.3-fix (bug 1.4): restrict the tools this engine can
        dispatch to those allowed for ``role``. Pass ``"parent"`` or
        ``"general"`` to clear the whitelist (all tools allowed).

        Without this, the "sub-agents are read-only by default" promise
        was enforced ONLY by the system prompt — if the model ignored
        the prompt and emitted write_file/str_replace/delete_file, the
        ToolEngine would happily execute it. Now the dispatch itself
        rejects the call with a clear error.
        """
        if role in ("parent", "general", ""):
            self.allowed_tools = None
            return
        whitelist = self.ROLE_TOOL_WHITELIST.get(role)
        if whitelist is None:
            # Unknown role — fail safe (allow all) but log loudly so the
            # developer notices the typo / unsupported role.
            logger.warning("[agent] unknown role %r — not enforcing whitelist", role)
            self.allowed_tools = None
            return
        self.allowed_tools = set(whitelist)

    def is_cancelled(self) -> bool:
        """True once the user has clicked Stop on the running agent task."""
        return bool(self._cancel_check and self._cancel_check())

    def _wait_interruptible(self, event: threading.Event, timeout: float) -> bool:
        """Like ``event.wait(timeout)``, but returns early (False) the
        moment ``is_cancelled()`` becomes true, instead of blocking the
        agent thread for the full timeout after the user hit Stop."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event.wait(timeout=0.25):
                return True
            if self.is_cancelled():
                return False
        return event.is_set()

    def respond_confirmation(self, accepted: bool) -> None:
        """Called from the main thread when the user clicks Allow/Deny on
        a non-file-write action-confirmation prompt (execute_command,
        delete_file, etc.)."""
        self._confirm_accepted = accepted
        self._confirm_event.set()

    def _request_confirmation(self, action: str, summary: str, is_new: bool = False) -> bool:
        """Ask the UI to confirm a side-effecting action that ISN'T
        already covered by diff-review, honoring the configured autonomy
        level. Returns True if the action should proceed.

        `is_new` should be True for actions that only create a brand-new
        path (e.g. writing a file that doesn't exist yet) — those are
        auto-approved under the 'new_files_only' autonomy level.
        """
        if self.autonomy == "never_ask":
            return True
        if self.autonomy == "new_files_only" and is_new:
            return True
        if self.is_cancelled():
            return False
        if not self._confirm_callback:
            # No UI wired up (e.g. headless use) — fail open so we don't
            # deadlock the caller, but log loudly so it's not silent.
            logger.warning("[agent] confirmation requested but no UI callback wired — allowing: %s", action)
            return True
        self._confirm_event.clear()
        self._confirm_accepted = None
        self._confirm_callback({"action": action, "summary": summary})
        ok = self._wait_interruptible(self._confirm_event, timeout=300)
        if not ok:
            return False
        return bool(self._confirm_accepted)

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path inside the workspace sandbox.

        SECURITY: the resolved path MUST live inside one of the allowed
        directories. We use `Path.is_relative_to()` (Python 3.9+) instead
        of string-prefix matching, because string-prefix matching lets
        `/home/user/work` succeed as a prefix of `/home/user/workbook`,
        which is a path-traversal vulnerability.

        v1.0.5-correctness: the old fallback for Python <3.9 used the
        vulnerable string-prefix match (`str(p).startswith(dp)`), which
        reintroduced exactly the bug the docstring warns about. We now
        use `os.path.commonpath()` which compares path COMPONENTS, not
        string characters — so `/home/user/work` is NOT considered a
        parent of `/home/user/workbook` (BUGS_REPORT M-RT-9).
        """
        p = (self.workspace / path).resolve()
        allowed = False
        for d in self._allowed_dirs:
            try:
                if p.is_relative_to(d):
                    allowed = True
                    break
            except AttributeError:
                # v1.0.6-security: removed vulnerable string-prefix fallback
                # (M-RT-9). Path.is_relative_to requires Python 3.9+.
                # If we get here, the runtime is too old — deny access.
                allowed = False
        if not allowed:
            raise PermissionError(f"Path outside workspace: {path}")
        return p

    def execute(self, call: ToolCall) -> str:
        start = time.monotonic()
        try:
            result = self._dispatch(call)
        except Exception as e:
            call.error = str(e)
            call.duration_ms = (time.monotonic() - start) * 1000
            return f"[TOOL ERROR] {e}"
        call.result = result[:self.MAX_OUTPUT]
        call.duration_ms = (time.monotonic() - start) * 1000
        return call.result

    def _dispatch(self, call: ToolCall) -> str:
        name = call.name
        args = call.args

        # v1.1.3-fix (bug 1.4): enforce role-based tool whitelist. When
        # `allowed_tools` is set (sub-agents), any tool NOT in the set is
        # rejected with a clear "[TOOL DENIED]" message. This makes the
        # "read-only by default" promise enforceable at the engine level
        # instead of relying solely on the system prompt.
        if self.allowed_tools is not None:
            tool_value = name.value if isinstance(name, ToolName) else str(name)
            if tool_value not in self.allowed_tools:
                logger.warning(
                    "[security] tool %r denied by role whitelist (allowed: %s)",
                    tool_value, sorted(self.allowed_tools),
                )
                return (
                    f"[TOOL DENIED] {tool_value} is not allowed for this "
                    f"sub-agent role. Allowed tools: "
                    f"{', '.join(sorted(self.allowed_tools))}"
                )

        # v1.1.0: defense in depth — even though PromptBuilder.system()
        # strips the spawn_* tools from the schema for non-heavy_code
        # sections, a model may still hallucinate a call. Reject it
        # explicitly with a clear error rather than executing.
        if name in (ToolName.SPAWN_SUBAGENT, ToolName.SPAWN_MULTI_AGENTS):
            if getattr(self, "section", "general") != "heavy_code":
                return (
                    f"[TOOL REJECTED] {name.value} is only available in "
                    f"Heavy Code mode. Switch to Heavy Code section to use "
                    f"multi-agent capabilities."
                )

        dispatch_map = {
            ToolName.READ_FILE: lambda: self._read_file(args.get("path", "")),
            ToolName.WRITE_FILE: lambda: self._write_file(args.get("path", ""), args.get("content", "")),
            ToolName.RUN_CODE: lambda: self._run_code(args.get("code", ""), args.get("language", "python")),
            ToolName.SEARCH_PROJECT: lambda: self._search_project(
                args.get("query", ""), args.get("directory", "."), args.get("file_pattern", "*.py")
            ),
            ToolName.LIST_FILES: lambda: self._list_files(args.get("directory", "."), args.get("pattern", "*")),
            ToolName.APPLY_DIFF: lambda: self._apply_diff(args.get("path", ""), args.get("diff", "")),
            ToolName.EXECUTE_COMMAND: lambda: self._execute_command(args.get("command", "")),
            ToolName.GET_PROJECT_STRUCTURE: lambda: self._get_project_structure(args.get("directory", ".")),
            ToolName.DELETE_FILE: lambda: self._delete_file(args.get("path", "")),
            ToolName.RENAME_FILE: lambda: self._rename_file(args.get("old_path", ""), args.get("new_path", "")),
            ToolName.MKDIR: lambda: self._mkdir(args.get("path", "")),
            ToolName.READ_BINARY_FILE: lambda: self._read_binary_file(args.get("path", "")),
            ToolName.WRITE_BINARY_FILE: lambda: self._write_binary_file(args.get("path", ""), args.get("content", "")),
            ToolName.FILE_INFO: lambda: self._file_info(args.get("path", "")),
            ToolName.UNDO_WRITE: lambda: self._undo_write(args.get("path", "")),
            ToolName.STR_REPLACE: lambda: self._str_replace(
                args.get("path", ""),
                args.get("old_str", ""),
                args.get("new_str", ""),
                args.get("replace_all", False),
            ),
            # v1.0.11: git tools
            ToolName.GIT_STATUS: lambda: self._git_status(),
            ToolName.GIT_DIFF: lambda: self._git_diff(
                args.get("staged", False),
                args.get("path", ""),
            ),
            ToolName.GIT_STAGE: lambda: self._git_stage(args.get("paths", [])),
            ToolName.GIT_COMMIT: lambda: self._git_commit(
                args.get("message", ""),
                args.get("paths", []),
            ),
            # v1.0.11: skill tool
            ToolName.GET_SKILL: lambda: self._get_skill(args.get("id", "")),
            # v1.1.0: MCP tool — proxy to the MCPManager
            ToolName.CALL_MCP_TOOL: lambda: self._call_mcp_tool(
                args.get("server", ""),
                args.get("tool", ""),
                args.get("args", {}) or {},
            ),
            # v1.1.0: subagent — single child agent for a sub-task
            ToolName.SPAWN_SUBAGENT: lambda: self._spawn_subagent(
                args.get("goal", ""),
                args.get("role", "generalist"),
                args.get("max_iterations", 4),
            ),
            # v1.1.0: multi-agent — N child agents in parallel
            ToolName.SPAWN_MULTI_AGENTS: lambda: self._spawn_multi_agents(
                args.get("tasks", []) or [],
            ),
        }

        if name not in dispatch_map:
            raise ValueError(f"Unknown tool: {name}")

        return dispatch_map[name]()

    def _read_file(self, path: str) -> str:
        p = self._resolve_path(path)
        if not p.exists():
            return f"[FILE NOT FOUND] {path}"
        self._mark_context_accessed(path)
        size = p.stat().st_size
        if size > 200_000:
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            preview = "\n".join(lines[:500])
            return f"[FILE LARGE: {size} bytes, {len(lines)} lines — showing first 500 lines]\n{preview}"
        return p.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _mark_context_accessed(path: str) -> None:
        """v1.1.4-fix (bug 4.2): boost a file's relevance score in
        ContextManager whenever the agent actually reads/writes it, so
        files the agent is actively working on stay auto-attached on
        later iterations. Best-effort — never let this break a tool call.
        """
        try:
            rel = path.lstrip("./").lstrip(".\\")
            get_context_manager().mark_accessed(rel)
        except Exception:
            pass

    def _write_file(self, path: str, content: str) -> str:
        p = self._resolve_path(path)

        # v1.1.5-fix (clew_bug_report.md bug #1): when diff review is
        # disabled, autonomy settings were ignored entirely — file
        # writes happened with no gate at all. The UI explicitly
        # promises "Diff review + autonomy settings still apply"
        # (web/index.html:481), so when diff_review is off we must
        # still honor autonomy via _request_confirmation, mirroring
        # write_binary_file's existing logic. Without this, a user
        # who disables the diff review popup (just to skip the modal)
        # but leaves autonomy on `always_ask` silently loses all
        # confirmation on every write.
        if not self.diff_review_enabled:
            is_new = not p.exists()
            if not self._request_confirmation(
                "write_file",
                f"{'Create' if is_new else 'Overwrite'} file: {path}",
                is_new=is_new,
            ):
                return f"[REJECTED BY USER] {path} — write cancelled"

        # v1.0.4: diff-review — pause and ask UI if enabled.
        # v1.0.5-correctness: fail-open when no UI callback is wired
        # (headless mode, test harness, CLI use). Previously the wait
        # would block for 300 s and then return [CANCELLED], silently
        # breaking every write in headless mode (BUGS_REPORT H-RT-4).
        if self.diff_review_enabled and p.exists() and p.is_file():
            original = p.read_text(encoding="utf-8", errors="replace")
            diff_text = self._compute_diff_text(path, original, content)
            if diff_text:  # only ask if there are actual changes
                if self._diff_review_callback is None:
                    # No UI wired — fail open (mirror _request_confirmation's
                    # behaviour at line ~516). Log loudly so it's not silent.
                    logger.warning(
                        "[agent] diff-review requested for %s but no UI callback "
                        "wired — applying write (headless mode)", path,
                    )
                else:
                    self._diff_review_event.clear()
                    self._diff_review_accepted = None
                    # Lines added/removed for summary
                    added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
                    removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))
                    self._diff_review_callback({
                        "path": path,
                        "diff": diff_text,
                        "original": original,
                        "proposed": content,
                        "lines_added": added,
                        "lines_removed": removed,
                    })
                    # Block agent thread until user responds (interruptible by Stop)
                    if not self._wait_interruptible(self._diff_review_event, timeout=300):
                        return f"[CANCELLED] {path} — write cancelled (agent stopped)"
                    if not self._diff_review_accepted:
                        return f"[REJECTED BY USER] {path} — write cancelled"

        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.is_file():
            self._backup_file(p)
        p.write_text(content, encoding="utf-8")
        self._mark_context_accessed(path)
        return f"[WRITTEN] {path} ({len(content)} chars)"

    def respond_diff_review(self, accepted: bool) -> None:
        """Called from the main thread when user clicks Apply/Reject."""
        self._diff_review_accepted = accepted
        self._diff_review_event.set()

    # ── v1.0.5: str_replace ────────────────────────────────────────
    # Implements §3.1 of качество_кода_llm.md ("правки, а не полная
    # перезапись файла"). The model must specify the *exact* unique
    # snippet to replace; if the snippet is not found or is ambiguous,
    # the tool returns an error and the model is forced to re-read the
    # file and try again — this is the deterministic verification the
    # document calls for.
    def _str_replace(self, path: str, old_str: str, new_str: str,
                     replace_all: bool = False) -> str:
        if not old_str:
            return "[STR_REPLACE ERROR] old_str is empty — refusing no-op"
        p = self._resolve_path(path)
        if not p.exists() or not p.is_file():
            return f"[FILE NOT FOUND] {path}"

        # v1.1.5-fix (clew_bug_report.md bug #1): same rationale as
        # _write_file — when diff review is disabled, autonomy must
        # still gate the edit. str_replace only operates on existing
        # files (we just checked), so is_new is always False here.
        # The autonomy levels then decide: `never_ask` auto-approves,
        # `new_files_only` asks (because this edits an existing file),
        # `always_ask` asks.
        if not self.diff_review_enabled:
            if not self._request_confirmation(
                "str_replace",
                f"Edit file: {path}",
                is_new=False,
            ):
                return f"[REJECTED BY USER] {path} — str_replace cancelled"

        original = p.read_text(encoding="utf-8", errors="replace")

        occurrences = original.count(old_str)
        if occurrences == 0:
            # The model is hallucinating the surrounding context —
            # return a clear, actionable error so it can re-read the
            # file and localise the change correctly.
            hint = self._str_replace_hint(original, old_str)
            return (
                f"[STR_REPLACE ERROR] old_str not found in {path}. "
                f"Re-read the file, then retry with a verbatim snippet. "
                f"{hint}"
            )
        if occurrences > 1 and not replace_all:
            return (
                f"[STR_REPLACE ERROR] old_str is not unique ({occurrences} matches) "
                f"in {path}. Either include more surrounding context to make it "
                f"unique, or pass replace_all=true to replace every match."
            )

        # Apply the replacement.
        if replace_all:
            patched = original.replace(old_str, new_str)
        else:
            # Replace only the first occurrence (str.replace would also
            # do all — we already verified uniqueness above).
            patched = original.replace(old_str, new_str, 1)

        # Diff-review gate — same path as write_file (only if changed).
        # v1.0.5-correctness: fail-open when no UI callback is wired
        # (BUGS_REPORT H-RT-4). Also: the return value of
        # _wait_interruptible was being discarded, so on a 5-minute
        # timeout (no cancel, no response) we'd fall through to
        # `if not self._diff_review_accepted` and return the misleading
        # "[REJECTED BY USER]" message even though the user never
        # rejected anything. We now honour the timeout explicitly.
        if self.diff_review_enabled and patched != original:
            diff_text = self._compute_diff_text(path, original, patched)
            if diff_text:
                if self._diff_review_callback is None:
                    # No UI wired — fail open (headless mode).
                    logger.warning(
                        "[agent] diff-review requested for %s but no UI callback "
                        "wired — applying str_replace (headless mode)", path,
                    )
                else:
                    self._diff_review_event.clear()
                    self._diff_review_accepted = None
                    added = sum(1 for l in diff_text.splitlines()
                                if l.startswith("+") and not l.startswith("+++"))
                    removed = sum(1 for l in diff_text.splitlines()
                                  if l.startswith("-") and not l.startswith("---"))
                    self._diff_review_callback({
                        "path": path,
                        "diff": diff_text,
                        "original": original,
                        "proposed": patched,
                        "lines_added": added,
                        "lines_removed": removed,
                    })
                    ok = self._wait_interruptible(self._diff_review_event, timeout=300)
                    if not ok or self.is_cancelled():
                        return f"[CANCELLED] {path} — str_replace cancelled (agent stopped)"
                    if self._diff_review_accepted is None:
                        # Timeout reached with no response and no cancel.
                        return f"[TIMEOUT] {path} — str_replace cancelled (no response within 300s)"
                    if not self._diff_review_accepted:
                        return f"[REJECTED BY USER] {path} — str_replace cancelled"

        # Backup + atomic write.
        self._backup_file(p)
        p.write_text(patched, encoding="utf-8")
        self._mark_context_accessed(path)
        n_replaced = occurrences if replace_all else 1
        return (
            f"[STR_REPLACE] {path} — replaced {n_replaced} occurrence(s), "
            f"{len(patched) - len(original):+d} chars"
        )

    @staticmethod
    def _str_replace_hint(original: str, old_str: str) -> str:
        """Best-effort hint: if the model was close (whitespace-only diff),
        point that out so it can self-correct."""
        # Normalize whitespace and try again
        norm = lambda s: " ".join(s.split())
        if norm(old_str) and norm(old_str) in norm(original):
            return ("Hint: the text matches up to whitespace — "
                    "copy the exact indentation/newlines from the file.")
        # Check if a unique fragment of old_str appears in original
        words = [w for w in old_str.split() if len(w) >= 4]
        if words:
            found = [w for w in words if w in original]
            if found:
                return (f"Hint: these tokens DO appear in the file: "
                        f"{', '.join(found[:5])}. Re-read the file around them.")
        return ""

    @staticmethod
    def _compute_diff_text(path: str, original: str, proposed: str) -> str:
        """Compute unified diff string."""
        diff = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        ))
        return "".join(diff)

    def _backup_file(self, p: Path) -> Path:
        """Create a timestamped backup of *path* in the backup directory.
        
        v1.0.6: enforces a maximum backup count (M-RT-4).
        """
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        # v1.0.6: prune old backups if over the cap (M-RT-4)
        try:
            existing = sorted(self._backup_dir.iterdir(),
                               key=lambda f: f.stat().st_mtime)
            while len(existing) > self._MAX_BACKUPS:
                oldest = existing.pop(0)
                try:
                    oldest.unlink()
                except OSError:
                    pass
        except OSError:
            pass
        ts = str(int(time.time()))
        h = hashlib.md5(str(p).encode()).hexdigest()[:8]
        backup_name = f"{h}_{ts}_{p.name}"
        backup_path = self._backup_dir / backup_name
        backup_path.write_bytes(p.read_bytes())
        return backup_path

    def _delete_file(self, path: str) -> str:
        p = self._resolve_path(path)
        # v1.1.1: never allow deleting the workspace root itself — a path
        # like "." or "" resolves to the workspace, which technically
        # passes _resolve_path's "inside the sandbox" check (a directory
        # is trivially "relative to" itself), but wiping the whole project
        # is never a reasonable single tool call.
        if p == self.workspace:
            return "[REFUSED] Refusing to delete the workspace root itself."
        if not p.exists():
            return f"[FILE NOT FOUND] {path}"
        kind = "directory" if p.is_dir() else "file"
        if not self._request_confirmation("delete_file", f"Delete {kind}: {path}"):
            return f"[REJECTED BY USER] {path} — delete cancelled"
        if p.is_dir():
            import shutil
            shutil.rmtree(p)
            return f"[DELETED DIR] {path}"
        p.unlink()
        return f"[DELETED] {path}"

    def _rename_file(self, old_path: str, new_path: str) -> str:
        old = self._resolve_path(old_path)
        new = self._resolve_path(new_path)
        if old == self.workspace or new == self.workspace:
            return "[REFUSED] Refusing to rename the workspace root itself."
        if not old.exists():
            return f"[FILE NOT FOUND] {old_path}"
        if not self._request_confirmation("rename_file", f"Rename: {old_path} → {new_path}"):
            return f"[REJECTED BY USER] {old_path} — rename cancelled"
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return f"[RENAMED] {old_path} → {new_path}"

    def _mkdir(self, path: str) -> str:
        p = self._resolve_path(path)
        p.mkdir(parents=True, exist_ok=True)
        return f"[MKDIR] {path}"

    def _read_binary_file(self, path: str) -> str:
        p = self._resolve_path(path)
        if not p.exists():
            return f"[FILE NOT FOUND] {path}"
        size = p.stat().st_size
        if size > 10_000_000:
            return f"[FILE TOO LARGE: {size} bytes — max 10MB for binary]"
        data = p.read_bytes()
        return base64.b64encode(data).decode("utf-8")

    def _write_binary_file(self, path: str, content: str) -> str:
        p = self._resolve_path(path)
        is_new = not p.exists()
        if not is_new and not self._request_confirmation("write_binary_file", f"Overwrite binary file: {path}"):
            return f"[REJECTED BY USER] {path} — write cancelled"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.is_file():
            self._backup_file(p)
        data = base64.b64decode(content)
        p.write_bytes(data)
        return f"[WRITTEN BINARY] {path} ({len(data)} bytes)"



    def _file_info(self, path: str) -> str:
        p = self._resolve_path(path)
        if not p.exists():
            return f"[FILE NOT FOUND] {path}"
        stat = p.stat()
        info = {
            "path": str(p),
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "created": stat.st_ctime,
            "is_file": p.is_file(),
            "is_dir": p.is_dir(),
        }
        return json.dumps(info, indent=2)

    def _undo_write(self, path: str) -> str:
        p = self._resolve_path(path)
        h = hashlib.md5(str(p).encode()).hexdigest()[:8]
        candidates = sorted(self._backup_dir.glob(f"{h}_*_{p.name}"), reverse=True)
        if not candidates:
            return f"[NO BACKUP] {path}"
        latest = candidates[0]
        p.write_bytes(latest.read_bytes())
        return f"[UNDO] {path} restored from {latest.name}"

    # ── v1.0.11: Git tools — direct project access like Claude Code ──
    # These give the agent the ability to inspect git state, see diffs,
    # stage files, and commit changes — without asking the user to run
    # git commands manually. The agent wraps the existing GitService
    # (clew/git_service.py), which itself shells out to the git CLI.
    # All git operations are sandboxed to the workspace root.

    def _get_git_service(self):
        """Lazily create a GitService for the current workspace.

        Returns None if the workspace is not a git repo (so the agent
        gets a clear error instead of a crash).
        """
        if not self.workspace or not self.workspace.is_dir():
            return None
        try:
            from .git_service import GitService
            git = GitService(str(self.workspace))
            if not git.is_available:
                return None
            return git
        except Exception as e:
            logger.warning(f"[git] failed to init GitService: {e}")
            return None

    def _git_status(self) -> str:
        """Show working tree status: branch, ahead/behind, modified files."""
        git = self._get_git_service()
        if not git:
            return "[GIT ERROR] not a git repository (or git not installed)"
        status = git.status()
        # Format as human-readable text for the agent
        lines = [
            f"Branch: {status.get('branch', 'unknown')}",
            f"Ahead: {status.get('ahead', 0)}, Behind: {status.get('behind', 0)}",
        ]
        files = status.get("files", [])
        if files:
            lines.append(f"Changed files ({len(files)}):")
            for f in files[:50]:  # cap to avoid huge output
                lines.append(f"  {f.get('status', '?')} {f.get('path', '')}")
        else:
            lines.append("Working tree clean.")
        return "\n".join(lines)

    def _git_diff(self, staged: bool = False, path: str = "") -> str:
        """Show git diff. If staged=True, show staged (cached) diff.
        If path is given, show diff for that file only."""
        git = self._get_git_service()
        if not git:
            return "[GIT ERROR] not a git repository"
        try:
            diff = git.diff(staged=staged, file_path=path if path else None)
            if not diff:
                return "[GIT] no changes (empty diff)"
            # Cap to MAX_OUTPUT chars to avoid blowing the context window
            if len(diff) > self.MAX_OUTPUT * 4:
                diff = diff[:self.MAX_OUTPUT * 4] + "\n... (diff truncated)"
            return diff
        except Exception as e:
            return f"[GIT ERROR] {e}"

    def _git_stage(self, paths) -> str:
        """Stage files. paths is a list of relative paths.
        If empty, stages all changes (git add -A)."""
        git = self._get_git_service()
        if not git:
            return "[GIT ERROR] not a git repository"
        try:
            if not paths:
                ok = git.stage_all()
                return "[GIT] staged all changes" if ok else "[GIT ERROR] stage_all failed"
            # Validate paths are inside workspace
            validated = []
            for p in paths:
                try:
                    resolved = self._resolve_path(p)
                    validated.append(str(resolved.relative_to(self.workspace)))
                except PermissionError:
                    return f"[GIT ERROR] path outside workspace: {p}"
            ok = git.stage(validated)
            if ok:
                return f"[GIT] staged {len(validated)} file(s): {', '.join(validated)}"
            return "[GIT ERROR] stage failed"
        except Exception as e:
            return f"[GIT ERROR] {e}"

    def _git_commit(self, message: str, paths=None) -> str:
        """Commit staged changes (or stage given paths first, then commit).
        message is required — never commit with an empty message."""
        git = self._get_git_service()
        if not git:
            return "[GIT ERROR] not a git repository"
        if not message or not message.strip():
            return "[GIT ERROR] commit message is required (never commit with empty message)"
        if not self._request_confirmation("git_commit", f"Commit: {message.strip()[:80]}"):
            return "[REJECTED BY USER] commit cancelled"
        try:
            # If paths given, stage them first
            if paths:
                validated = []
                for p in paths:
                    try:
                        resolved = self._resolve_path(p)
                        validated.append(str(resolved.relative_to(self.workspace)))
                    except PermissionError:
                        return f"[GIT ERROR] path outside workspace: {p}"
                git.stage(validated)
            result = git.commit(message.strip())
            if result.get("ok"):
                return f"[GIT COMMIT] {result.get('hash', '?')[:8]} — {message.strip()[:80]}"
            return f"[GIT ERROR] commit failed: {result.get('error', 'unknown')}"
        except Exception as e:
            return f"[GIT ERROR] {e}"

    # ── v1.0.11: Skill tool ──────────────────────────────────────────
    # The agent calls get_skill(id) to pull the full body of a skill
    # into context. The skill catalog (id + name + description) is
    # already in the system prompt, so the agent knows what's available
    # without consuming context tokens for the full bodies.

    def _get_skill(self, skill_id: str) -> str:
        """Return the full body of a skill by id."""
        if not skill_id:
            # List available skills if no id given
            if not self._skills:
                return "[SKILL] no skills available"
            lines = ["[SKILL] available skills:"]
            for s in self._skills:
                lines.append(f"  - {s.id}: {s.name} — {s.description[:80]}")
            return "\n".join(lines)
        for s in self._skills:
            if s.id == skill_id:
                return f"[SKILL: {s.id}]\n{s.body}"
        return (
            f"[SKILL ERROR] no skill with id {skill_id!r}. "
            f"Available: {', '.join(s.id for s in self._skills) or 'none'}"
        )

    # ── v1.1.0: MCP + multi-agent tools ────────────────────────────

    def _call_mcp_tool(self, server: str, tool: str,
                       args: Dict[str, Any]) -> str:
        """Invoke a tool on an MCP server via the MCPManager singleton.

        The MCP server must be configured in ~/.clew/mcp.json and running.
        The agent sees the available MCP tools in the system prompt
        (injected by MCPManager.catalog_prompt()) and calls this meta-tool
        with (server, tool, args). The result is returned as the
        observation.

        v1.1.3-fix (bug 1.3): MCP tools ARE subject to the autonomy
        confirmation gate. Previously the comment below said "we trust
        the user's MCP server config", but that ignored the reality
        that popular MCP servers (filesystem, github, browser) expose
        write_file/delete_file/create_pull_request/push/navigate — all
        side-effecting operations that bypassed the confirm dialog
        applied to native write_file/execute_command. A prompt-injection
        in any file the agent reads could trigger e.g.
        ``call_mcp_tool("filesystem", "write_file", {"path": "/etc/cron.d/...", "content": "..."})``
        with NO user prompt. We now route every call_mcp_tool through
        _request_confirmation(), unless the server is explicitly marked
        ``"trusted": true`` in mcp.json.
        """
        if not server or not tool:
            return (
                "[MCP ERROR] both 'server' and 'tool' are required. "
                "Use the catalog in the system prompt to pick a server+tool."
            )
        # v1.1.3-fix (bug 3.7): validate args type. JSON-RPC 2.0 allows
        # params as an array, but MCP requires `arguments` to be an object.
        # Most servers return "invalid params" without context; we surface
        # a clearer error before the round-trip.
        if not isinstance(args, dict):
            return (
                f"[MCP ERROR] args must be a JSON object, got "
                f"{type(args).__name__} — wrap arguments in {{}}."
            )
        # v1.1.3-fix (bug 1.3): confirmation gate. Check if the server is
        # explicitly trusted via mcp.json; if not, ask the user (subject
        # to the autonomy level).
        try:
            from .mcp_manager import get_mcp_manager
            manager = get_mcp_manager()
            trusted = manager.is_server_trusted(server)
        except Exception:
            trusted = False
        if not trusted:
            summary = f"MCP {server}.{tool}({json.dumps(args, default=str)[:120]})"
            if not self._request_confirmation("call_mcp_tool", summary):
                return f"[REJECTED BY USER] MCP {server}.{tool} cancelled"
        try:
            # Lazy import to avoid circular dependency at module load time
            from .mcp_manager import get_mcp_manager
            manager = get_mcp_manager()
            result = manager.call_tool(server, tool, args)
            # Truncate to MAX_OUTPUT for context budget
            if len(result) > self.MAX_OUTPUT:
                result = result[:self.MAX_OUTPUT] + f"\n... [truncated, {len(result)} total chars]"
            return f"[MCP {server}.{tool}]\n{result}"
        except Exception as e:
            return f"[MCP ERROR] {server}.{tool} failed: {e}"

    def _spawn_subagent(self, goal: str, role: str = "generalist",
                        max_iterations: int = 4) -> str:
        """Spawn a single sub-agent for a sub-task (orchestrator-worker
        pattern). The sub-agent runs in its own AgentRuntime instance
        with a narrower scope and returns its final answer as the
        observation.

        role: "generalist" | "architect" | "implementer" | "reviewer" | "tester"
        max_iterations: how many tool-call iterations the sub-agent gets
                       (default 4 — much less than the parent's 8-30)

        Sub-agents share the parent's workspace and provider registry.
        They are read-only by default (no write tools) to prevent
        uncontrolled side effects. Pass role="implementer" to allow
        writes (still subject to the parent's autonomy setting).
        """
        if not goal or not goal.strip():
            return "[SUBAGENT ERROR] goal is required"
        try:
            return self._run_subagent_internal(
                goal=goal.strip(),
                role=role or "generalist",
                max_iterations=int(max_iterations or 4),
                label="subagent",
            )
        except Exception as e:
            return f"[SUBAGENT ERROR] {e}"

    def _spawn_multi_agents(self, tasks: List[Any]) -> str:
        """Spawn N sub-agents in parallel for independent sub-tasks.

        tasks: list of {goal, role?, max_iterations?} dicts

        Each task runs in its own thread; results are joined and
        returned as a single observation. Tasks that fail are
        reported but don't abort the others.
        """
        if not tasks or not isinstance(tasks, list):
            return "[MULTI-AGENTS ERROR] tasks must be a non-empty list"
        if len(tasks) > 5:
            return (
                "[MULTI-AGENTS ERROR] too many tasks — max 5 parallel "
                "sub-agents (to avoid overwhelming the provider)."
            )
        import concurrent.futures
        results: List[str] = []
        # Run sub-agents in parallel using a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {}
            for i, task_spec in enumerate(tasks):
                if not isinstance(task_spec, dict):
                    results.append(f"[task {i+1}] invalid spec — must be a dict")
                    continue
                goal = task_spec.get("goal", "")
                role = task_spec.get("role", "generalist")
                mi = int(task_spec.get("max_iterations", 4))
                label = f"multi-agent #{i+1}"
                fut = pool.submit(
                    self._run_subagent_internal,
                    goal=goal, role=role, max_iterations=mi, label=label,
                )
                futures[fut] = i + 1
            for fut in concurrent.futures.as_completed(futures):
                idx = futures[fut]
                try:
                    res = fut.result()
                    results.append(f"[task {idx}]\n{res}")
                except Exception as e:
                    results.append(f"[task {idx}] FAILED: {e}")
        # Order results by task index for readability
        return "\n\n---\n\n".join(results)

    def _run_subagent_internal(self, goal: str, role: str,
                                max_iterations: int,
                                label: str) -> str:
        """Internal helper: spawn a child AgentRuntime and run it to
        completion. Used by both _spawn_subagent (single) and
        _spawn_multi_agents (parallel).

        Sub-agents:
          - Share the parent's ProviderRegistry (so they use the same
            active model/API key)
          - Share the parent's workspace
          - Have their own ContextMemory (fresh — no parent history)
          - Have their own ToolEngine (fresh — no parent's skills loaded)
          - Emit events to the PARENT's on_event callback with a
            `parent_label` field so the UI can nest them visually
          - Are read-only by default (role=generalist/architect/reviewer/tester)
            — role=implementer is the only one that gets write tools
        """
        if not self._registry:
            return f"[{label}] no provider registry available"
        # Role → system prompt suffix + tool whitelist
        role_prompts = {
            "architect": (
                "You are a sub-agent focused on PLANNING and DESIGN. "
                "Read files, analyze structure, propose a plan. Do NOT write "
                "or modify any files — return your plan as the final answer."
            ),
            "implementer": (
                "You are a sub-agent focused on IMPLEMENTATION. Make the "
                "requested changes precisely. Prefer str_replace over "
                "write_file. Verify your changes by re-reading the file."
            ),
            "reviewer": (
                "You are a sub-agent focused on CODE REVIEW. Read the "
                "specified files, identify bugs / style issues / risks. "
                "Do NOT modify files — return your review as the final answer."
            ),
            "tester": (
                "You are a sub-agent focused on TESTING. Generate test cases "
                "for the specified code. You may write test files but do NOT "
                "modify production code."
            ),
            "generalist": (
                "You are a sub-agent. Complete the assigned sub-task. "
                "Read files as needed, return your findings/changes as "
                "the final answer."
            ),
        }
        system_suffix = role_prompts.get(role, role_prompts["generalist"])
        # Build the child runtime — fresh ContextMemory, same registry
        import tempfile as _tf
        child_persist = _tf.NamedTemporaryFile(
            prefix=f"clew_subagent_{label.replace(' ', '_')}_",
            suffix=".json", delete=False,
        )
        child_persist.close()
        # v1.1.3-fix (bug 1.2): inherit the parent's section so quota is
        # accounted against the SAME counter (e.g. heavy_code). Without
        # this, sub-agents defaulted to section="general" (unlimited),
        # which made them a free bypass of the daily quota.
        child = AgentRuntime(
            registry=self._registry,
            workspace=str(self.workspace),
            max_iterations=max(1, min(max_iterations, 10)),
            enable_planning=False,  # sub-agents skip planning — parent already planned
            on_event=None,  # we'll forward events with a parent_label
            memory_persist_path=child_persist.name,
            token_tracker=getattr(self, "_token_tracker", None),
            section=getattr(self, "section", "general"),
        )
        # v1.1.3-fix (bug 1.1): propagate the parent's cancel-check so
        # Stop halts sub-agents too. Without this, child.tools._cancel_check
        # stays None and is_cancelled() always returns False — the parent
        # loop stops, but spawn_multi_agents children keep running LLM
        # calls and tools until they finish naturally.
        child.set_cancel_check(self.is_cancelled)
        # v1.1.3-fix (bug 1.2): propagate the quota tracker so sub-agent
        # LLM calls are counted against the parent's daily quota. Combined
        # with the section inheritance above, this closes the "orchestrator
        # spawns 5 implementers and bypasses the 10/day limit" hole.
        child.set_quota_tracker(getattr(self, "_quota_tracker", None))
        # Sub-agent inherits the parent's autonomy + diff-review settings
        child.tools.autonomy = self.autonomy
        child.tools.diff_review_enabled = self.diff_review_enabled
        # v1.1.3-fix (bug 1.4): apply role-based tool whitelist so the
        # "read-only" promise for non-implementer roles is ENFORCED at
        # the ToolEngine level, not just the system prompt. Even if the
        # model ignores the prompt and emits write_file/str_replace/
        # delete_file/etc., the dispatch will be rejected.
        child.tools.set_role_whitelist(role)
        # Note: sub-agent's diff-review/confirm callbacks are NOT wired
        # to the parent UI — they fail open (headless mode), which is
        # fine because sub-agents are read-only by default. For
        # role="implementer" we should ideally forward these to the
        # parent UI, but that's a v1.2 enhancement.
        # For now: implementer sub-agents run with autonomy="never_ask"
        # so they don't deadlock waiting for a UI they don't have.
        if role == "implementer":
            child.tools.autonomy = "never_ask"
            # v1.1.3-fix (bug 1.4): keep diff_review enabled — disabling
            # it was a separate hole that let implementers silently apply
            # writes. The child's diff-review callback is None (headless),
            # so it fails-open to "allow" anyway, but the flag stays True
            # so a future implementation that forwards the callback to
            # the parent UI would Just Work.
            child.tools.diff_review_enabled = self.diff_review_enabled
        # Forward child events to the parent's on_event (if any),
        # tagged with parent_label so the UI can nest them.
        parent_on_event = self.on_event
        if parent_on_event:
            def _child_forward(event, data):
                data = dict(data)
                data["parent_label"] = label
                data["subagent"] = True
                try:
                    parent_on_event(event, data)
                except Exception:
                    pass
            child.on_event = _child_forward
        # Build a focused task
        task = Task(
            type=TaskType.AGENTIC,
            description=(
                f"{system_suffix}\n\n"
                f"## Sub-task (assigned by parent agent)\n{goal}\n\n"
                f"Return your final answer concisely — the parent agent "
                f"will incorporate it into its own response."
            ),
            language="python",
        )
        try:
            result = child._run_agent_loop(task)
            if result.success:
                return (
                    f"[{label} OK in {result.iterations} iterations]\n"
                    f"{result.output}"
                )
            else:
                return (
                    f"[{label} FAILED after {result.iterations} iterations: "
                    f"{result.error or 'unknown'}]\n{result.output}"
                )
        finally:
            # Clean up the child's persist file
            try:
                import os as _os
                _os.unlink(child_persist.name)
            except OSError:
                pass

    def _run_code(self, code: str, language: str = "python") -> str:
        if not code.strip():
            return "[EMPTY CODE]"

        # v1.0.6-security: require user confirmation before running code
        # (C-RT-1). Without this, prompt injection in any file the agent
        # reads could trigger arbitrary code execution.
        if not self._request_confirmation("run_code", f"Run {language} code ({len(code)} chars)"):
            return "[REJECTED BY USER] run_code cancelled"

        with tempfile.TemporaryDirectory() as tmpdir:
            if language in ("python", "py"):
                fpath = os.path.join(tmpdir, "run.py")
                cmd = ["python3", fpath]
            elif language in ("javascript", "js", "node"):
                fpath = os.path.join(tmpdir, "run.js")
                cmd = ["node", fpath]
            elif language in ("bash", "sh", "shell"):
                fpath = os.path.join(tmpdir, "run.sh")
                cmd = ["bash", fpath]
            else:
                return f"[UNSUPPORTED LANGUAGE: {language}]"

            with open(fpath, "w", encoding="utf-8") as f:
                f.write(code)

            try:
                # v1.1.3-fix (bug 1.7): the sandbox environment previously
                # claimed to "block network access" via env vars, but:
                #   1. PYTHONHTTPSVERIFY=0 is a NO-OP — Python only honours
                #      the value when it's "1" (to ENABLE strict verify).
                #      Setting it to "0" is the same as not setting it.
                #   2. Empty http_proxy/https_proxy only disables the
                #      proxy — direct socket.create_connection still works.
                #   3. PATH was inherited fully, giving access to all
                #      system binaries (different code path from
                #      ALLOWED_COMMANDS in _execute_command).
                # We can't fix #2 and #3 from Python (real isolation
                # needs seccomp/network namespaces/firejail), but we
                # CAN remove the misleading no-op and the comment that
                # claimed network was blocked. The empty proxy vars
                # are kept because they DO help when a proxy is set
                # globally — they just don't block direct connections.
                sandbox_env = {
                    "HOME": tmpdir,
                    "TMPDIR": tmpdir,
                    "TEMP": tmpdir,
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    # Disable proxy use (does NOT block direct connections
                    # — see comment above). Real network isolation requires
                    # OS-level sandboxing (seccomp, firejail --net=none).
                    "http_proxy": "",
                    "https_proxy": "",
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "NO_PROXY": "*",
                    "no_proxy": "*",
                }

                # v1.0.6: use Popen + polling so Stop button can abort
                # subprocess execution (M-RT-7). subprocess.run blocks
                # for up to RUN_TIMEOUT with no cancellation.
                proc = subprocess.Popen(
                    cmd, shell=False,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=tmpdir, env=sandbox_env,
                )
                stdout, stderr = b"", b""
                try:
                    # Poll for completion, checking cancel every 0.5s
                    while proc.poll() is None:
                        if self.is_cancelled():
                            proc.kill()
                            proc.wait()
                            return f"[CANCELLED BY USER] run_code aborted"
                        time.sleep(0.5)
                    stdout = proc.stdout.read()
                    stderr = proc.stderr.read()
                except Exception:
                    proc.kill()
                    proc.wait()
                    raise

                parts = []
                out_text = stdout.decode("utf-8", errors="replace")
                err_text = stderr.decode("utf-8", errors="replace")
                if out_text:
                    parts.append(f"[STDOUT]\n{out_text[-self.MAX_OUTPUT//2:]}")
                if err_text:
                    parts.append(f"[STDERR]\n{err_text[-self.MAX_OUTPUT//2:]}")
                if proc.returncode != 0:
                    parts.append(f"[EXIT CODE] {proc.returncode}")
                return "\n".join(parts) if parts else "[NO OUTPUT]"
            except subprocess.TimeoutExpired:
                return f"[TIMEOUT] Exceeded {self.RUN_TIMEOUT}s"
            except FileNotFoundError as e:
                return f"[RUNTIME NOT FOUND] {e}"

    def _search_project(self, query: str, directory: str = ".", file_pattern: str = "*.py") -> str:
        try:
            base = self._resolve_path(directory)
        except PermissionError:
            base = self.workspace

        results: List[str] = []
        try:
            for fpath in sorted(base.rglob(file_pattern))[:50]:
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if query.lower() in line.lower():
                            rel = fpath.relative_to(self.workspace) if fpath.is_relative_to(self.workspace) else fpath
                            results.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(results) >= 40:
                                break
                except Exception:
                    continue
                if len(results) >= 40:
                    break
        except Exception as e:
            return f"[SEARCH ERROR] {e}"

        if not results:
            return f"[NO RESULTS] '{query}' not found"
        return "\n".join(results)

    def _list_files(self, directory: str = ".", pattern: str = "*") -> str:
        try:
            base = self._resolve_path(directory)
        except PermissionError:
            base = self.workspace
        try:
            files = sorted(base.rglob(pattern))[:100]
            lines = [str(f.relative_to(self.workspace)) if f.is_relative_to(self.workspace) else str(f) for f in files]
            return "\n".join(lines) if lines else "[NO FILES FOUND]"
        except Exception as e:
            return f"[LIST ERROR] {e}"

    def _apply_diff(self, path: str, diff: str) -> str:
        try:
            p = self._resolve_path(path)
            if not p.exists():
                return f"[FILE NOT FOUND] {path}"
            if not self._request_confirmation("apply_diff", f"Patch: {path}"):
                return f"[REJECTED BY USER] {path} — apply_diff cancelled"

            # v1.0.6: multi-file diff support (M-RT-3). If the diff
            # contains --- a/ / +++ b/ headers for MULTIPLE files, split
            # and apply each file's hunks separately. Otherwise apply
            # as a single-file diff (backward compat).
            files_diffs = _split_multi_file_diff(diff)
            if len(files_diffs) == 1:
                # Single-file diff (or no file headers at all)
                original = p.read_text(encoding="utf-8")
                patched = _apply_unified_diff(original, diff)
                if p.exists() and p.is_file():
                    self._backup_file(p)
                p.write_text(patched, encoding="utf-8")
                return f"[PATCHED] {path}"
            else:
                # Multi-file diff: apply each file's hunks to its own file
                results = []
                for file_path, file_diff in files_diffs:
                    try:
                        target = self._resolve_path(file_path)
                    except PermissionError:
                        results.append(f"[SECURITY ERROR] path outside workspace: {file_path}")
                        continue
                    if not target.exists():
                        # New file — just write it
                        patched = _apply_unified_diff("", file_diff)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(patched, encoding="utf-8")
                        results.append(f"[CREATED] {file_path}")
                    else:
                        original = target.read_text(encoding="utf-8")
                        patched = _apply_unified_diff(original, file_diff)
                        self._backup_file(target)
                        target.write_text(patched, encoding="utf-8")
                        results.append(f"[PATCHED] {file_path}")
                return "\n".join(results)
        except Exception as e:
            return f"[DIFF ERROR] {e}"

    # Commands whose arguments are file/directory paths that MUST be
    # validated against the workspace sandbox before we run them. A bare
    # binary whitelist (ALLOWED_COMMANDS) is not enough on its own: "rm",
    # "mv", "cp", and "find -exec" are all whitelisted (agents legitimately
    # need them), but without checking the paths they're given, the model
    # (or content it read that contains a prompt injection) could do
    # `rm -rf /some/path/outside/the/project` and the whitelist alone
    # would happily let it through.
    # v1.0.6-security: expanded to cover all commands that take file
    # paths as arguments (C-RT-3). Without this, `cat /etc/passwd`,
    # `head ~/.ssh/id_rsa`, `grep -r secret /` were all allowed.
    _PATH_ARG_COMMANDS = {
        "rm", "mv", "cp", "find", "cat", "head", "tail",
        "grep", "mkdir", "touch", "git",
    }

    def _validate_command_paths(self, args: List[str]) -> Optional[str]:
        """For commands that take file/dir paths as arguments, make sure
        every path-like argument resolves inside the workspace sandbox.
        Returns an error string if a path escapes the sandbox, else None.

        v1.0.6-security: also validates `git -C <path>` which bypasses
        the normal arg-based check since `-C` starts with `-` and the
        path follows it (C-RT-3).
        """
        base_cmd = os.path.basename(args[0])
        if base_cmd not in self._PATH_ARG_COMMANDS:
            return None
        # v1.0.6-security: git -C <path> changes the working directory
        # to <path> which is not the workspace — validate it.
        if base_cmd == "git":
            for i, arg in enumerate(args[1:], 1):
                if arg == "-C" and i + 1 < len(args):
                    git_c_path = args[i + 1]
                    try:
                        self._resolve_path(git_c_path)
                    except PermissionError:
                        return (
                            f"[SECURITY ERROR] 'git -C' argument escapes the "
                            f"workspace sandbox: {git_c_path!r}"
                        )
        for arg in args[1:]:
            # Skip flags (-rf, -name, etc.) and find's non-path predicates.
            if arg.startswith("-"):
                continue
            # find's "{}" placeholder and bare "." / "./" refer to the
            # search root or the current match — "." is fine (== workspace).
            if arg in ("{}", ".", "./"):
                continue
            try:
                self._resolve_path(arg)
            except PermissionError:
                return (
                    f"[SECURITY ERROR] '{base_cmd}' argument escapes the "
                    f"workspace sandbox: {arg!r}"
                )
        return None

    def _execute_command(self, command: str) -> str:
        """Execute command with shell=False security."""
        args, is_safe = _sanitize_command(command)
        if not is_safe:
            return f"[SECURITY ERROR] Command blocked: {command}"

        path_error = self._validate_command_paths(args)
        if path_error:
            logger.warning("[security] %s (full command: %s)", path_error, command)
            return path_error

        if not self._request_confirmation("execute_command", f"Run: {command}"):
            return f"[REJECTED BY USER] command cancelled: {command}"

        try:
            # v1.0.6: use Popen + polling so Stop can abort the
            # subprocess (M-RT-7). subprocess.run blocks up to
            # RUN_TIMEOUT with no cancellation path.
            proc = subprocess.Popen(
                args, shell=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=self.workspace,
            )
            stdout, stderr = b"", b""
            deadline = time.time() + self.RUN_TIMEOUT
            try:
                while proc.poll() is None:
                    if self.is_cancelled():
                        proc.kill()
                        proc.wait()
                        return "[CANCELLED BY USER] command aborted"
                    if time.time() > deadline:
                        proc.kill()
                        proc.wait()
                        return f"[TIMEOUT] Command exceeded {self.RUN_TIMEOUT}s"
                    time.sleep(0.25)
                stdout = proc.stdout.read()
                stderr = proc.stderr.read()
            except Exception:
                proc.kill()
                proc.wait()
                raise

            out_text = stdout.decode("utf-8", errors="replace")
            err_text = stderr.decode("utf-8", errors="replace")
            parts = []
            if out_text:
                parts.append(out_text[:self.MAX_OUTPUT])
            if err_text:
                parts.append(err_text[:self.MAX_OUTPUT])
            if proc.returncode != 0:
                parts.append(f"[EXIT CODE] {proc.returncode}")
            # v1.1.5-fix (clew_bug_report.md bug #9): if the agent just
            # ran `git init` / `git clone` (or any other command that
            # creates/removes a .git directory), invalidate the
            # GitService class-level cache so the next status poll
            # re-detects the repo instead of serving a stale "not a repo"
            # result. We do a cheap substring match on the first argv
            # rather than parsing the full command line.
            try:
                if args and isinstance(args, list) and len(args) >= 2:
                    bin_name = os.path.basename(str(args[0])).lower()
                    sub_cmd = str(args[1]).lower() if len(args) > 1 else ""
                    if bin_name == "git" and sub_cmd in {"init", "clone"}:
                        try:
                            from .git_service import GitService
                            GitService.invalidate_cache(str(self.workspace))
                        except Exception:
                            pass
            except Exception:
                pass
            return "\n".join(parts) if parts else "[NO OUTPUT]"
        except Exception as e:
            return f"[COMMAND ERROR] {e}"

    def _get_project_structure(self, directory: str = ".") -> str:
        try:
            base = self._resolve_path(directory)
            lines = []
            for root, dirs, files in os.walk(base):
                level = root.replace(str(base), "").count(os.sep)
                indent = "  " * level
                lines.append(f"{indent}{os.path.basename(root)}/")
                subindent = "  " * (level + 1)
                for file in sorted(files)[:20]:
                    lines.append(f"{subindent}{file}")
            return "\n".join(lines)
        except Exception as e:
            return f"[STRUCTURE ERROR] {e}"


def _split_multi_file_diff(diff: str) -> List[Tuple[str, str]]:
    """Split a multi-file unified diff into per-file (path, diff_text) tuples.

    v1.0.6: if the diff contains --- a/ / +++ b/ headers for multiple
    files, each file's hunks are separated and returned independently.
    Single-file diffs (or diffs without file headers) return a list
    with one entry using an empty path (M-RT-3).

    v1.1.3-fix (bug 1.11): the regex captured the entire remainder of
    the +++ line, including optional timestamp suffixes that some
    ``git diff`` modes and GUIs append (``+++ b/path.py\t2024-01-01
    12:34:56.000000000 +0000``). The captured target_path then included
    the timestamp, causing the write to go to the wrong file. We now
    strip anything after a tab or whitespace.
    """
    file_header_re = re.compile(r"^---\s+a/(.+)\s*\n\+\+\+\s+b/(.+)", re.MULTILINE)
    matches = list(file_header_re.finditer(diff))
    if len(matches) < 2:
        # Not a multi-file diff — return as-is
        return [("", diff)]
    splits: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        # Use the "new" path (+++ b/path) as the target.
        # v1.1.3-fix (bug 1.11): strip any timestamp suffix after a tab
        # or whitespace. ``git diff`` with --abbrev or some GUIs append
        # ``\t2024-01-01 12:34:56.000000000 +0000`` to the +++ line.
        raw_path = m.group(2).strip()
        target_path = raw_path.split("\t")[0].split()[0].strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff)
        file_diff = diff[start:end]
        splits.append((target_path, file_diff))
    return splits


def _apply_unified_diff(original: str, diff: str) -> str:
    """Apply a unified diff to *original*, returning the new content.

    v1.0.5-correctness: the old applicator never verified that the
    context lines in the diff actually matched the corresponding lines
    in *original*. If the diff was generated against a stale version of
    the file (line numbers shifted by even one line), the slice
    assignment would silently overwrite the wrong lines, and the running
    ``offset`` would accumulate the wrong correction for subsequent
    hunks — corrupting the file with no error (BUGS_REPORT H-RT-8).

    The new implementation:
      1. Verifies each hunk's context lines match the file at the
         expected position. If they don't, it raises ``ValueError``
         instead of silently corrupting the file.
      2. Handles new-file hunks (``@@ -0,0 +1,N @@``) by appending
         instead of slicing at index -1.
      3. Clamps ``orig_start`` to a valid range.
    """
    orig_lines = original.splitlines(keepends=True)
    result = list(orig_lines)
    offset = 0

    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    diff_lines = diff.splitlines(keepends=True)

    i = 0
    while i < len(diff_lines):
        m = hunk_re.match(diff_lines[i])
        if m:
            orig_start_raw = int(m.group(1))
            # v1.0.5-correctness: handle new-file hunks (`@@ -0,0 +1,N @@`).
            if orig_start_raw <= 0:
                orig_start = 0
            else:
                orig_start = orig_start_raw - 1  # 0-indexed
            i += 1
            hunk_orig, hunk_new = [], []
            while i < len(diff_lines) and not hunk_re.match(diff_lines[i]):
                line = diff_lines[i]
                if line.startswith("-"):
                    hunk_orig.append(line[1:])
                elif line.startswith("+"):
                    hunk_new.append(line[1:])
                elif line.startswith(" "):
                    hunk_orig.append(line[1:])
                    hunk_new.append(line[1:])
                # Lines not starting with -, +, or space (e.g. "\ No newline
                # at end of file") are ignored — they're meta-markers.
                i += 1

            start = orig_start + offset
            # v1.0.5-correctness: verify context+remove lines match the
            # file at the expected position. If they don't, refuse to
            # apply rather than corrupting the file silently.
            if hunk_orig:
                if start < 0:
                    raise ValueError(
                        f"diff apply failed: hunk starts before line 0 "
                        f"(orig_start={orig_start_raw}, offset={offset})"
                    )
                if start + len(hunk_orig) > len(result):
                    raise ValueError(
                        f"diff apply failed: hunk extends past end of file "
                        f"(need lines {start+1}..{start+len(hunk_orig)}, "
                        f"file has {len(result)} lines)"
                    )
                actual = result[start:start + len(hunk_orig)]
                if actual != hunk_orig:
                    # Show a short diagnostic so the caller (and the agent)
                    # can re-read the file and regenerate the diff.
                    preview_expected = "".join(hunk_orig[:3]).rstrip()
                    preview_actual = "".join(actual[:3]).rstrip()
                    raise ValueError(
                        f"diff apply failed: context mismatch at line {start+1}. "
                        f"Diff expected:\n  {preview_expected!r}\n"
                        f"File has:\n  {preview_actual!r}\n"
                        f"The diff was likely generated against a stale "
                        f"version of the file — re-read it and regenerate."
                    )
            # Apply the hunk.
            if start >= len(result) and not hunk_orig:
                # New-file hunk on empty original — append.
                result.extend(hunk_new)
            else:
                result[start:start + len(hunk_orig)] = hunk_new
            offset += len(hunk_new) - len(hunk_orig)
        else:
            i += 1

    return "".join(result)


# ── JSON Tool Schema ─────────────────────────────────────────────────────

TOOL_SCHEMA = """Available tools (call exactly ONE per step using JSON):

{"tool": "read_file", "args": {"path": "relative/or/absolute/path"}}
{"tool": "write_file", "args": {"path": "relative/path/to/file", "content": "full file content here"}}
{"tool": "str_replace", "args": {"path": "relative/path/to/file", "old_str": "exact unique snippet to find", "new_str": "replacement text", "replace_all": false}}
{"tool": "delete_file", "args": {"path": "relative/path/to/file"}}
{"tool": "rename_file", "args": {"old_path": "old/name", "new_path": "new/name"}}
{"tool": "mkdir", "args": {"path": "relative/path/to/dir"}}
{"tool": "read_binary_file", "args": {"path": "relative/path/to/file"}}
{"tool": "write_binary_file", "args": {"path": "relative/path/to/file", "content": "base64 encoded bytes"}}
{"tool": "file_info", "args": {"path": "relative/path/to/file"}}
{"tool": "undo_write", "args": {"path": "relative/path/to/file"}}
{"tool": "run_code", "args": {"code": "python code to execute", "language": "python"}}
{"tool": "search_project", "args": {"query": "search term", "directory": ".", "file_pattern": "*.py"}}
{"tool": "list_files", "args": {"directory": ".", "pattern": "**/*.py"}}
{"tool": "apply_diff", "args": {"path": "file/to/patch", "diff": "unified diff string"}}
{"tool": "execute_command", "args": {"command": "shell command to run"}}
{"tool": "get_project_structure", "args": {"directory": "."}}
{"tool": "git_status", "args": {}}
{"tool": "git_diff", "args": {"staged": false, "path": "optional/file/path"}}
{"tool": "git_stage", "args": {"paths": ["file1.py", "file2.py"]}}
{"tool": "git_commit", "args": {"message": "commit message", "paths": ["optional/file.py"]}}
{"tool": "get_skill", "args": {"id": "skill_id"}}

# v1.1.0: MCP (Model Context Protocol) — call external tools (filesystem,
# github, browser, databases, etc.) configured in Settings → MCP.
# Available in ALL sections. The catalog of available MCP tools is
# appended below — call them via this meta-tool.
{"tool": "call_mcp_tool", "args": {"server": "filesystem", "tool": "read_file", "args": {"path": "/tmp/foo.txt"}}}

# v1.1.0: Multi-agent (Heavy Code only) — spawn sub-agents for sub-tasks.
# Use spawn_subagent for a single focused sub-task, spawn_multi_agents
# for parallel independent sub-tasks. Roles: generalist | architect |
# implementer | reviewer | tester.
{"tool": "spawn_subagent", "args": {"goal": "Read auth.py and list all endpoints", "role": "architect", "max_iterations": 4}}
{"tool": "spawn_multi_agents", "args": {"tasks": [{"goal": "refactor foo.py", "role": "implementer"}, {"goal": "refactor bar.py", "role": "implementer"}]}}

PREFERENCE ORDER (very important — directly affects code quality):
  1. For EDITS to existing files, ALWAYS prefer str_replace over write_file.
     str_replace forces you to localise the change and is verifiable.
     write_file rewrites the whole file and is more error-prone.
  2. Use write_file ONLY for: brand-new files, or full rewrites that the
     user explicitly asked for.
  3. Before any write, you MUST have read the file in this session.

SPECIAL TOKEN — when you intend to write a file, signal it explicitly:

  [WRITE_FILE] path/to/file.py

The token MUST be on its own line, immediately followed by a JSON tool
call (write_file or str_replace) targeting that file. This token is what
the UI uses to (a) pause for human review of the diff, (b) snapshot the
file for undo, and (c) update the project tree. If you emit write_file
WITHOUT the [WRITE_FILE] token, the write will still happen but the UI
will not be able to show the review pane and the user will not be
prompted — which is almost always worse for the user.

When you have the final answer and no more tool calls are needed, output:
{"final_answer": "your complete answer / code here"}
"""

# Few-shot examples — proven to dramatically improve tool-call reliability
# on small (3B-12B) local models. v1.0.5: examples now demonstrate
# (a) the [WRITE_FILE] token before every write, and (b) str_replace as
# the preferred edit tool.
FEW_SHOT_EXAMPLES = """
CORRECT EXAMPLES — copy this exact format:

Thought: I need to see what's in main.py before I can refactor it.
{"tool": "read_file", "args": {"path": "main.py"}}

Thought: Now I'll write the new file. Signalling the write first.
[WRITE_FILE] main.py
{"tool": "write_file", "args": {"path": "main.py", "content": "import os\\n\\nprint('hi')\\n"}}

Thought: I need to change just the return value of hello(). Using str_replace so the change is targeted and verifiable.
[WRITE_FILE] src/hello.py
{"tool": "str_replace", "args": {"path": "src/hello.py", "old_str": "    return 'hi'\\n", "new_str": "    return 'world'\\n"}}

Thought: I need to create a directory for the new module.
{"tool": "mkdir", "args": {"path": "src/utils"}}

Thought: Remove the old temporary file.
{"tool": "delete_file", "args": {"path": "tmp/old.txt"}}

Thought: The file is in good shape, no further tool calls needed.
{"final_answer": "Refactored main.py to use os module and removed unused imports."}

WRONG (do NOT do any of these):
- {"tool": "read_file", "path": "main.py"}           ← "path" must be inside "args"
- ```json\\n{"tool": "read_file", ...}\\n```            ← do NOT wrap in markdown fence
- I'll read main.py. (no JSON at all)                  ← always emit JSON, not prose
- write_file to change one line of a 200-line file     ← use str_replace instead
- emit [WRITE_FILE] without a following tool call      ← token must precede the write tool call
- emit a write_file call without [WRITE_FILE]          ← always prefix with the token
"""

# v1.0.5: SYSTEM_PROMPT restructured to encode the principles from
# качество_кода_llm.md. Each section maps to a specific principle:
#   §2.1 — explicit planning phase  → WORKFLOW step 1
#   §2.3 — negative examples        → ANTI-PATTERNS
#   §2.5 — tests before/with code   → WORKFLOW step 4
#   §2.6 — self-review pass         → WORKFLOW step 5
#   §2.7 — role narrowing           → role line at the top
#   §2.8 — explicit output format   → OUTPUT FORMAT
#   §2.10 — match project conventions → RULES
#   §3.1 — patches not full rewrites → PREFERENCE ORDER in TOOL_SCHEMA
#   §3.3 — generate→execute→feedback → WORKFLOW step 3 + run_code tool
#   §3.5 — plan-of-changes before patches → PLAN_PROMPT
SYSTEM_PROMPT = """\
You are CLEW, an interactive coding agent embedded in the Clew IDE. \
You help users with software engineering tasks: explaining code, finding \
bugs, editing files, writing functions, tests, and documentation.

# How you communicate
- All text you output BEFORE a tool call is shown to the user in the chat. \
Use it to briefly state what you're about to do ("Reading main.py to find \
the auth handler…"). One short sentence is enough.
- All text you output as the final answer is shown to the user as the \
response. This is where you explain what you found, what you changed, \
or answer their question.
- You can mix text and tool calls in the same response: write a short \
sentence, then the JSON tool call on the next line.

# When to use tools (IMPORTANT — read carefully)
- If the user asks to EXPLAIN, FIND, DESCRIBE, or answer a question about \
  code → READ the relevant files with read_file / search_project, then \
  answer in your final_answer text. DO NOT write or edit any file.
- If the user asks to CREATE, WRITE, SAVE, EDIT, FIX, or REFACTOR a file \
  → use write_file (new files) or str_replace (edits to existing files). \
  DO NOT paste the code in chat and ask the user to save it themselves.
- If the user asks to RUN something → use run_code or execute_command.
- If you're unsure whether the user wants a file change or an explanation \
  → answer in text first, then ask "Want me to apply this change?" before \
  writing any file.

# Doing tasks
- Read a file before proposing changes to it. Never guess file contents.
- Don't create files unless absolutely necessary. Prefer editing existing \
  files over creating new ones.
- Don't add features, refactor, or "improve" code beyond what was asked. \
  A bug fix doesn't need surrounding code cleaned up.
- Don't add comments, docstrings, or type annotations to code you didn't \
  touch. Only comment where the WHY is non-obvious.
- If an approach fails, diagnose why before retrying. Don't brute-force \
  the same call.
- Match the project's existing style and conventions. Consistency with \
  surrounding code is part of correctness.

# Workflow
1. If the task involves code changes, write a 3–5 step plan naming the \
files you'll touch. Output the plan as plain text, no tool call yet.
2. READ the relevant files. Don't guess.
3. Make changes one tool call at a time. Prefer str_replace for edits, \
write_file for new files. After each change, briefly state what you did \
in one sentence before the next tool call.
4. For new code, write tests and run them with run_code.
5. Final answer: explain what you did, what was verified, and any \
follow-ups. Don't just say "Done" — give the user enough to understand \
the result.

# Tools (call ONE per response, as JSON, no markdown fence)

{tool_schema}

# Output format
- Write a short text sentence (shown to user), THEN the JSON tool call \
  on its own line. Example:
  Thought: I'll read main.py first.
  {{"tool": "read_file", "args": {{"path": "main.py"}}}}
- For file writes, prefix with `[WRITE_FILE] <path>` on its own line \
  before the JSON.
- Do NOT wrap JSON in ```json fences.
- When done, output {{"final_answer": "your summary text here"}} — the \
  text inside is shown to the user as the final response.

{few_shot_examples}
"""

PLAN_PROMPT = """You are CLEW AGENT. Break down the following coding task into a \
numbered step-by-step plan.

The plan MUST include, in this order:
1. Files to read for context (be specific — name paths).
2. Files to create or modify, and what changes in each.
3. At least 2 edge cases that the implementation must handle correctly \
   (e.g. empty input, boundary values, concurrent access, error paths).
4. How the change will be verified (test file path, command to run, \
   expected output).
5. Risks / regressions to watch for.

Keep the plan under 10 steps. Output ONLY the numbered plan, no prose, \
no code blocks, no tool calls.

Task: {task}
Context: {context}
"""


# ── Prompt Builder ───────────────────────────────────────────────────────

class PromptBuilder:
    @staticmethod
    def system(section: str = "general") -> str:
        """Build the system prompt for the given section.

        v1.1.0: in non-heavy_code sections we strip the spawn_subagent
        and spawn_multi_agents entries from TOOL_SCHEMA so the model
        doesn't try to call them. The tools still exist in ToolEngine
        (defense in depth — _dispatch will reject them), but advertising
        them in the prompt for general/office would just confuse the
        model.
        """
        schema = TOOL_SCHEMA
        if section != "heavy_code":
            # Strip the multi-agent tool descriptions (the comment block
            # and the two spawn_* tool JSON examples).
            lines = TOOL_SCHEMA.split("\n")
            stripped: List[str] = []
            skip_block = False
            for line in lines:
                if "Multi-agent (Heavy Code only)" in line:
                    skip_block = True
                if skip_block:
                    # End the skip after we've passed the spawn_multi_agents entry
                    if line.startswith('{"tool": "spawn_multi_agents"'):
                        skip_block = False
                    continue  # don't append
                stripped.append(line)
            schema = "\n".join(stripped)
        return SYSTEM_PROMPT.format(
            tool_schema=schema,
            few_shot_examples=FEW_SHOT_EXAMPLES,
        )

    @staticmethod
    def plan(task: str, context: str = "") -> str:
        return PLAN_PROMPT.format(task=task, context=context or "none")

    @staticmethod
    def task_prompt(task: Task, plan: str = "", history: str = "") -> str:
        parts = []
        if plan:
            parts.append(f"## Execution Plan\n{plan}\n")
        if history:
            parts.append(f"## Previous Steps\n{history}\n")

        type_prompts = {
            TaskType.WRITE: f"## Task: Write {task.language} code\n{task.description}\n",
            TaskType.EDIT: f"## Task: Edit code\nInstruction: {task.description}\n",
            TaskType.DEBUG: f"## Task: Debug\nError: {task.description}\n",
            TaskType.REFACTOR: f"## Task: Refactor\nGoal: {task.description}\n",
            TaskType.ANALYZE: "## Task: Analyze code\n",
            TaskType.TEST: f"## Task: Generate tests for {task.language} code\n",
            TaskType.CHAT: f"## Message\n{task.description}\n",
            TaskType.AGENTIC: f"## Autonomous Task\n{task.description}\n",
        }
        parts.append(type_prompts.get(task.type, f"## Task\n{task.description}\n"))

        if task.context and task.type not in (TaskType.CHAT, TaskType.AGENTIC):
            parts.append(f"```{task.language}\n{task.context}\n```")

        if task.file_path:
            parts.append(f"Target file: `{task.file_path}`")

        parts.append("\nProceed with the first tool call or final answer.")
        return "\n".join(parts)

    @staticmethod
    def continuation(observation: str, step_num: int) -> str:
        return (
            f"## Tool Result (step {step_num})\n"
            f"```\n{observation[:4000]}\n```\n\n"
            f"Continue: use another tool or output {{\"final_answer\": \"...\"}}."
        )


# ── JSON Output Parser ───────────────────────────────────────────────────

class OutputParser:
    """Parse JSON-based tool calls instead of fragile XML regex.

    Includes self-correction for common malformed-JSON patterns that small
    local models produce.
    """

    TOOL_ARG_HINTS = {
        "read_file":              ["path"],
        "write_file":             ["path", "content"],
        "str_replace":            ["path", "old_str", "new_str", "replace_all"],
        "apply_diff":             ["path", "diff"],
        "run_code":               ["code", "language"],
        "search_project":         ["query", "directory", "file_pattern"],
        "list_files":             ["directory", "pattern"],
        "execute_command":        ["command"],
        "get_project_structure":  ["directory"],
        "delete_file":            ["path"],
        "rename_file":            ["old_path", "new_path"],
        "mkdir":                  ["path"],
        "read_binary_file":       ["path"],
        "write_binary_file":      ["path", "content"],
        "file_info":              ["path"],
        "undo_write":             ["path"],
        # v1.0.11: git + skill tools
        "git_status":             [],
        "git_diff":               ["staged", "path"],
        "git_stage":              ["paths"],
        "git_commit":             ["message", "paths"],
        "get_skill":              ["id"],
        # v1.1.0: MCP + multi-agent tools
        "call_mcp_tool":          ["server", "tool", "args"],
        "spawn_subagent":         ["goal", "role", "max_iterations"],
        "spawn_multi_agents":     ["tasks"],
    }

    @classmethod
    def parse_tool_call(cls, text: str) -> Optional[ToolCall]:
        """Extract JSON tool call from text, with self-correction.

        v1.0.5-correctness: the old regex used ``[^{}]*`` and ``[^}]*``
        which exclude braces — so any tool call whose ``args`` contained
        a brace inside a string value (e.g.
        ``{"tool": "write_file", "args": {"path": "x.py",
        "content": "d = {'a': 1}"}}``) failed to match. We now extract
        the JSON object with a brace-balanced scan that ignores braces
        inside string literals, then try ``json.loads`` on the result
        (BUGS_REPORT M-RT-10).
        """
        cleaned = cls._strip_code_fence(text)

        # v1.0.5-correctness: brace-balanced scan that respects string
        # literals. Finds the first ``{...}`` block that contains
        # ``"tool"`` and is brace-balanced (ignoring braces inside
        # string literals). This handles tool calls whose args contain
        # braces in string values (e.g. Python dict literals in
        # `content`).
        raw = cls._extract_balanced_json(cleaned)
        if raw is None:
            # Fall back to the old simple regex for non-JSON cases.
            match = re.search(r'\{.*"tool"\s*:\s*"([^"]+)".*\}', cleaned, re.DOTALL)
            if not match:
                return None
            raw = match.group(0)

        data = cls._safe_json(raw)
        if data is None:
            data = cls._safe_json(raw)
            if data is None:
                tool_name_match = re.search(r'"tool"\s*:\s*"([^"]+)"', raw)
                if not tool_name_match:
                    logger.warning(f"[parser] No tool name in: {raw[:200]}")
                    return None
                tool_name_str = tool_name_match.group(1)
                args = cls._extract_args_by_name(raw, tool_name_str)
                if args is None:
                    return None
                data = {"tool": tool_name_str, "args": args}

        if "args" not in data or not isinstance(data["args"], dict):
            tool_name_str = data.get("tool", "")
            lifted = cls._lift_top_level_args(raw, tool_name_str)
            if lifted:
                data["args"] = lifted

        try:
            tool_name_str = data.get("tool", "")
            args = data.get("args", {}) or {}
            name = ToolName(tool_name_str)
            return ToolCall(name=name, args=args)
        except (ValueError, KeyError) as e:
            logger.warning(f"[parser] Tool name lookup failed: {e}")
            return None

    @classmethod
    def _extract_balanced_json(cls, text: str) -> Optional[str]:
        """Find the first brace-balanced ``{...}`` block containing ``"tool"``.

        Ignores braces inside string literals (single and double quoted,
        with backslash escapes). Returns the raw substring (including
        the outer braces) or ``None`` if no balanced block with
        ``"tool"`` is found.

        v1.0.5-perf: O(n) instead of O(n²). We first locate every
        occurrence of ``"tool"`` in the text, then for each we walk
        backwards to find the enclosing ``{`` and forwards to find the
        matching ``}`` (respecting string literals). The old code
        restarted a full forward scan from every ``{`` in the text,
        which was O(n²) and noticeably slow on long model responses.
        """
        n = len(text)
        # Find every occurrence of `"tool"` — these are cheap to locate
        # and almost always few (typically exactly one).
        search_from = 0
        tool_marker = '"tool"'
        marker_len = len(tool_marker)
        while True:
            idx = text.find(tool_marker, search_from)
            if idx == -1:
                return None
            # Walk backwards from idx to find the enclosing opening `{`.
            # We track string literals so braces inside strings don't
            # confuse the depth count.
            open_idx = -1
            depth = 0
            in_str: Optional[str] = None
            j = idx - 1
            while j >= 0:
                ch = text[j]
                # Note: when walking backwards, escape detection is
                # approximate (we'd need to count preceding backslashes
                # to know if a quote is escaped). For tool-call parsing
                # this is good enough — the model rarely emits escaped
                # quotes before a `"tool"` key.
                if in_str is not None:
                    if ch == in_str:
                        in_str = None
                    j -= 1
                    continue
                if ch in ('"', "'"):
                    in_str = ch
                    j -= 1
                    continue
                if ch == '}':
                    depth += 1
                elif ch == '{':
                    if depth == 0:
                        open_idx = j
                        break
                    depth -= 1
                j -= 1
            if open_idx < 0:
                search_from = idx + marker_len
                continue
            # Walk forwards from open_idx to find the matching `}`.
            depth = 0
            in_str = None
            i = open_idx
            while i < n:
                ch = text[i]
                if in_str is not None:
                    if ch == '\\':
                        i += 2
                        continue
                    if ch == in_str:
                        in_str = None
                    i += 1
                    continue
                if ch in ('"', "'"):
                    in_str = ch
                    i += 1
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return text[open_idx:i + 1]
                i += 1
            # Unbalanced — try the next `"tool"` occurrence.
            search_from = idx + marker_len
        return None

    @classmethod
    def _strip_code_fence(cls, text: str) -> str:
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if m:
            return text.replace(m.group(0), m.group(1))
        return text

    @classmethod
    def _safe_json(cls, raw: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(raw.replace("'", '"'))
        except json.JSONDecodeError:
            pass
        try:
            cleaned = re.sub(r',\s*([}\]])', r'\1', raw)
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.debug(f"[parser] JSON gave up: {e}")
            return None

    @classmethod
    def _lift_top_level_args(cls, raw: str, tool_name: str) -> Dict[str, Any]:
        hints = cls.TOOL_ARG_HINTS.get(tool_name)
        if not hints:
            return {}
        data = cls._safe_json(raw)
        if not data:
            return {}
        lifted = {}
        for k in hints:
            if k in data and k not in ("tool", "args"):
                lifted[k] = data[k]
        return lifted

    @classmethod
    def _extract_args_by_name(cls, raw: str,
                              tool_name: str) -> Optional[Dict[str, Any]]:
        """Last-resort arg extractor for malformed JSON tool calls.

        v1.0.5-correctness: the old implementation did
        ``m.group(1).encode("utf-8").decode("unicode_escape")`` which is
        a classic Python footgun — it encodes the str to UTF-8 bytes,
        then decodes those bytes as Latin-1 + unicode-escape. For any
        non-ASCII content this mangles the string: ``"你好"`` (6 UTF-8
        bytes) becomes ``"ä½ å¥½"`` (6 Latin-1 chars). Even
        ``codecs.decode(s, 'unicode_escape')`` has the same problem
        because it internally encodes the str to bytes first.

        The correct fix is a manual escape-sequence decoder that only
        transforms ``\\n``, ``\\"``, ``\\\\``, ``\\uXXXX`` etc. and
        leaves existing Unicode characters (CJK, emoji, etc.) untouched
        (BUGS_REPORT H-RT-6).

        v1.1.3-fix (bug 1.6): the non-string regex matched only the
        first non-comma/non-space char, so for ``"tasks": [{"goal": "x"}]``
        it captured ``[`` and then ``int("[")`` / ``float("[")`` raised
        ValueError — the except clause then stored the literal string
        ``"["`` in args. Downstream code saw ``tasks="["`` and returned
        a confusing "tasks must be a non-empty list" error. We now try
        JSON parsing on the captured token first (handles true/false/
        null/numbers), and skip the key entirely if the token is not a
        valid JSON literal (e.g. it's the start of an array/object).
        """
        hints = cls.TOOL_ARG_HINTS.get(tool_name)
        if not hints:
            return {}
        args: Dict[str, Any] = {}
        for key in hints:
            m = re.search(
                rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL
            )
            if m:
                # v1.0.5-correctness: decode only JSON/Python escape
                # sequences, preserving existing Unicode chars.
                raw_val = m.group(1)
                args[key] = cls._decode_escapes_preserving_unicode(raw_val)
                continue
            # v1.1.3-fix (bug 1.6): try to capture the full non-string
            # value (up to the next comma at the same brace level, or
            # the closing brace). The old regex ``([^",\s]+)`` only
            # captured one char for arrays/objects.
            m = re.search(rf'"{key}"\s*:\s*([^",\s]+)', raw)
            if m:
                val = m.group(1).rstrip(",}")
                # v1.1.3-fix (bug 1.6): only accept valid JSON literals
                # (true/false/null/number). Anything else (like ``[`` or
                # ``{``) means the value is a complex type we can't
                # extract with this regex — skip the key rather than
                # storing garbage.
                if val.lower() in ("true", "false", "null"):
                    if val.lower() == "true":
                        args[key] = True
                    elif val.lower() == "false":
                        args[key] = False
                    else:
                        args[key] = None
                    continue
                # Try int, then float. If both fail, skip (don't store
                # the raw string — that's what caused bug 1.6).
                try:
                    args[key] = int(val)
                    continue
                except ValueError:
                    pass
                try:
                    args[key] = float(val)
                    continue
                except ValueError:
                    pass
                # v1.1.3-fix (bug 1.6): not a valid JSON literal — skip
                # this key entirely. Storing the raw string caused
                # downstream type errors (e.g. ``tasks="["``).
                logger.debug(
                    "[parser] _extract_args_by_name: skipping key %r — "
                    "value %r is not a valid JSON literal", key, val,
                )
                continue
        return args if args else None

    @staticmethod
    def _decode_escapes_preserving_unicode(s: str) -> str:
        """Decode escape sequences in *s* without mangling Unicode.

        Handles: ``\\n``, ``\\r``, ``\\t``, ``\\b``, ``\\f``, ``\\"``,
        ``\\\\``, ``\\/``, ``\\uXXXX``. Leaves all other characters
        (including CJK and emoji) untouched.
        """
        out: List[str] = []
        i = 0
        n = len(s)
        simple_escapes = {
            'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f',
            '"': '"', '\\': '\\', '/': '/', "'": "'",
        }
        while i < n:
            ch = s[i]
            if ch == '\\' and i + 1 < n:
                nxt = s[i + 1]
                if nxt in simple_escapes:
                    out.append(simple_escapes[nxt])
                    i += 2
                    continue
                if nxt == 'u' and i + 5 < n:
                    hex_str = s[i + 2:i + 6]
                    try:
                        code = int(hex_str, 16)
                        out.append(chr(code))
                        i += 6
                        continue
                    except ValueError:
                        pass
                if nxt == 'U' and i + 9 < n:
                    hex_str = s[i + 2:i + 10]
                    try:
                        code = int(hex_str, 16)
                        out.append(chr(code))
                        i += 10
                        continue
                    except ValueError:
                        pass
                # Unknown escape — keep the backslash and the next char.
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
        return ''.join(out)

    @classmethod
    def parse_final_answer(cls, text: str) -> Optional[str]:
        """Extract the ``final_answer`` field from a model response.

        v1.0.5-correctness: the old regex used a non-greedy ``(.*?)``
        which stopped at the first ``"`` — so for input
        ``{"final_answer": "He said \\"hi\\" to me"}`` it returned just
        ``"He said \\"``, silently truncating the answer. The new
        implementation tries proper JSON parsing first, and only falls
        back to regex on JSON that won't parse (BUGS_REPORT H-RT-5).
        """
        # Try to find a JSON object containing "final_answer" and parse
        # it properly — this handles escaped quotes, nested objects, etc.
        # Search for the outermost {...} that contains final_answer.
        for candidate in re.finditer(r'\{[^{}]*"final_answer"[^{}]*\}', text, re.DOTALL):
            try:
                data = json.loads(candidate.group(0))
                if isinstance(data, dict) and "final_answer" in data:
                    val = data["final_answer"]
                    if isinstance(val, str):
                        return val.strip()
                    return str(val).strip()
            except json.JSONDecodeError:
                continue
        # Fallback: extract the value with an escape-aware regex.
        # ``"(?:[^"\\]|\\.)*"`` matches a JSON-style string literal
        # including escaped quotes.
        m = re.search(
            r'"final_answer"\s*:\s*"((?:[^"\\]|\\.)*)"',
            text, re.DOTALL,
        )
        if m:
            # Unescape JSON string escapes (\n, \", \\, \uXXXX, etc.).
            raw_val = m.group(1)
            try:
                return json.loads(f'"{raw_val}"')
            except json.JSONDecodeError:
                return raw_val.strip()
        return None

    @classmethod
    def is_final(cls, text: str) -> bool:
        return '"final_answer"' in text

    @classmethod
    def extract_thought(cls, text: str) -> str:
        """Extract the 'thought' portion of a model response.

        v1.0.5-hotfix: when the model's response is short prose with no
        JSON (the "no tool call" failure mode), the old code returned
        the full text — which is correct. But when the response starts
        with ``{`` (pure JSON, no prose preamble), the old code returned
        an empty string. We now return the full text in that case too,
        so the UI has something to show. The thought is only trimmed
        when there's actual prose before the JSON.
        """
        json_start = text.find("{")
        if json_start > 0:
            return text[:json_start].strip()
        # No JSON, or JSON at position 0 — return the full text.
        return text.strip()

    # ── v1.0.5: [WRITE_FILE] special-token parsing ─────────────────
    # The token tells the runtime "the next tool call is a file write
    # targeting <path>" so the UI can pre-fetch the original content
    # for diff review and warm up the project-tree watcher. The tool
    # call itself is still a normal JSON object — the token is a hint,
    # not a substitute for the call.
    _WRITE_TOKEN_RE = re.compile(
        r"^\s*\[WRITE_FILE\]\s*(\S+)\s*$",
        re.MULTILINE,
    )

    @classmethod
    def extract_write_intent(cls, text: str) -> Optional[Tuple[str, str]]:
        """Return (path, raw_token_line) if the model emitted a
        ``[WRITE_FILE] <path>`` token anywhere in the response, else None.

        The runtime uses this to:
          * pre-load the original file content for diff review
          * emit a TOOL_CALLED event with the target path before the
            agent thread blocks on the write
          * detect mismatches (token path != tool-arg path) and warn
        """
        m = cls._WRITE_TOKEN_RE.search(text)
        if not m:
            return None
        return (m.group(1), m.group(0).strip())

    @classmethod
    def strip_write_token(cls, text: str) -> str:
        """Remove the [WRITE_FILE] line so the remaining text can be
        parsed cleanly for the JSON tool call."""
        return cls._WRITE_TOKEN_RE.sub("", text)


# ── Agent Runtime ────────────────────────────────────────────────────────

EventCallback = Callable[[AgentEvent, Dict[str, Any]], None]


def _warn_unknown_tools(plan: str) -> None:
    """Log a warning if the plan references tool names that don't exist
    in ToolName (M-RT-8). This doesn't block execution — it just helps
    the developer notice when the model hallucinates tools."""
    valid_tools = {t.value for t in ToolName}
    # Match patterns like "use the X tool" or "call X" or "X tool"
    for word in re.findall(r'\b([a-z_]+)\b', plan.lower()):
        if word in ("the", "a", "an", "to", "for", "and", "or", "with",
                     "use", "call", "tool", "step", "file", "then",
                     "via", "using", "run", "check"):
            continue
        if word in valid_tools:
            continue
        # Only warn on words that look like tool names (underscored, or
        # common tool-like suffixes)
        if "_" in word or word.endswith("_file") or word.endswith("_code"):
            if word not in valid_tools:
                logger.debug("[agent] plan references unknown tool-like word: %s", word)


class AgentRuntime:
    """
    ReAct-style autonomous agent with tool-use loop.
    Thread-safe operations, JSON tool calling, secure command execution.

    v1.0.3: Uses ProviderRegistry instead of ModelEngine.
    Accepts a ProviderRegistry at init and calls provider.generate()
    with proper ProviderMessage objects.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        workspace: Optional[str] = None,
        max_iterations: int = 8,
        enable_planning: bool = True,
        on_event: Optional[EventCallback] = None,
        verbose: bool = False,
        memory_persist_path: Optional[str] = None,
        token_tracker: Optional[Any] = None,
        section: str = "general",
    ):
        self._registry = registry
        self.tools = ToolEngine(workspace)
        # v1.1.3-fix (bug 1.1/1.2): propagate runtime-level context down to
        # the ToolEngine so that _run_subagent_internal (which lives on
        # ToolEngine) can build child agents with the parent's registry,
        # event callback, token/quota trackers, and section. Without this,
        # spawn_subagent would crash with AttributeError on self._registry.
        self.tools._registry = registry
        self.tools.on_event = on_event
        self.tools._token_tracker = token_tracker
        self.tools._quota_tracker = None
        self.tools.section = section
        self.memory = ContextMemory(persist_path=memory_persist_path)
        self.memory.load()
        self.task_history: List[Task] = []
        self.max_iterations = max_iterations
        self.enable_planning = enable_planning
        self.on_event = on_event
        self.verbose = verbose
        # v1.0.5-correctness: token tracker for real usage accounting (H-RT-3).
        # If None, _generate_with_retry just skips the record() call.
        self._token_tracker = token_tracker
        # v1.1.1-fix: accumulate real token counts per run so the UI can
        # display them (previously _generate() discarded ProviderResponse
        # and only returned resp.text, losing tokens_in/out).
        self._run_tokens_in: int = 0
        self._run_tokens_out: int = 0
        # v1.1.0: section ("general" | "heavy_code" | "office") — controls:
        #   - which tools are advertised in the system prompt
        #     (subagent/multi-agent only in heavy_code)
        #   - which daily quota counter to bump (heavy_code = 10/day free)
        #   - the system prompt variant (Heavy Code gets a stronger one)
        self.section = section
        # v1.1.0: quota tracker — lazily wired via set_quota_tracker().
        # When set, _generate_with_retry calls quota.record() and
        # _run_agent_loop checks quota.exhausted() before each LLM call.
        self._quota_tracker: Optional[Any] = None
        # v1.0.9: project context (CLAUDE.md) — loaded lazily on first use
        self._project_context = get_project_context()
        # v1.1.4-fix (bug 4.2): ContextManager was fully implemented
        # (relevance scoring + token-budgeted file selection) but never
        # instantiated anywhere. Wired in the same way as ProjectContext
        # so relevant files get auto-attached to the prompt — see
        # execute_task() for where the selection is actually injected.
        self._context_manager = get_context_manager()
        if workspace:
            self._project_context.set_root(workspace)
            self._context_manager.set_root(workspace)
        # v1.0.11: skills — load from project + user-global + builtins
        self._skills: List[Any] = []
        self._reload_skills()

        logger.info("AgentRuntime initialized (Provider-backed)")

    def set_cancel_check(self, fn: Optional[Callable[[], bool]]) -> None:
        """Wire a zero-arg callable that returns True once the running
        task has been cancelled (Stop button). Passed straight through to
        the ToolEngine, which polls it while blocked on diff-review /
        confirmation prompts, and the agent loop polls it between
        iterations — so Stop actually halts further tool calls instead of
        just muting UI updates while the loop keeps running.
        """
        self.tools._cancel_check = fn

    def set_token_tracker(self, tracker: Optional[Any]) -> None:
        """Attach (or detach) a token tracker for real usage accounting.

        v1.0.5-correctness: the bridge/api_server create the AgentRuntime
        before they create the TokenTracker, so we expose a setter to
        wire the tracker in after the fact. When attached, every
        successful ``provider.generate()`` call records ``tokens_in`` /
        ``tokens_out`` (H-RT-3).
        """
        self._token_tracker = tracker
        # v1.1.3-fix (bug 1.2): mirror to ToolEngine so sub-agent
        # spawning (which lives on ToolEngine) can propagate the tracker
        # to child AgentRuntime instances.
        self.tools._token_tracker = tracker

    def set_quota_tracker(self, tracker: Optional[Any]) -> None:
        """v1.1.0: attach (or detach) the daily quota tracker.

        When attached, _run_agent_loop checks ``tracker.exhausted(section)``
        before the first LLM call and raises a friendly error if the
        section's daily limit is reached. _generate_with_retry calls
        ``tracker.record(section, provider, model)`` after each
        successful LLM call.
        """
        self._quota_tracker = tracker
        # v1.1.3-fix (bug 1.2): mirror to ToolEngine so sub-agent
        # spawning can propagate the quota tracker to child agents.
        self.tools._quota_tracker = tracker

    def set_section(self, section: str) -> None:
        """v1.1.0: switch the runtime's section ("general" | "heavy_code"
        | "office"). Affects which tools are advertised and which quota
        counter is bumped."""
        if section not in ("general", "heavy_code", "office"):
            section = "general"
        self.section = section
        # Propagate to ToolEngine so _dispatch can reject section-gated
        # tools (spawn_subagent, spawn_multi_agents).
        self.tools.section = section

    def set_autonomy(self, level: str) -> None:
        """'always_ask' | 'new_files_only' | 'never_ask' — see
        ToolEngine._request_confirmation for what each level gates."""
        if level not in ("always_ask", "new_files_only", "never_ask"):
            level = "always_ask"
        self.tools.autonomy = level

    def set_confirm_callback(self, fn: Optional[Callable]) -> None:
        """Wire the UI callback used for non-diff-review confirmations
        (execute_command, delete_file, rename_file, apply_diff,
        write_binary_file, git_commit)."""
        self.tools._confirm_callback = fn

    def _reload_skills(self) -> None:
        """v1.0.11: (re)load the skill list from disk.

        Called on init and after set_workspace (so opening a different
        project picks up its .clew/skills/). The skill catalog is
        injected into the system prompt on the next agent run.
        """
        ws = self.tools.workspace if self.tools and self.tools.workspace else None
        self._skills = load_all_skills_with_builtins(ws)
        # Inject the skill list into the ToolEngine so _get_skill works
        self.tools.set_skills(self._skills)
        logger.info("[agent] loaded %d skills", len(self._skills))

    # ── v1.0.9: Context management ────────────────────────────────────

    def context_status(self) -> Dict[str, Any]:
        """Return a status dict for the /context command.

        Combines:
          - ContextMemory status (messages, tokens, utilization)
          - ProjectContext status (CLAUDE.md sources, char count)
          - System prompt size
        """
        mem_status = self.memory.status()
        # v1.0.9: call instructions() first so the cache is populated
        # before we read status() — otherwise status() shows no sources
        # even when CLAUDE.md exists.
        self._project_context.instructions()
        proj_status = self._project_context.status()
        sys_prompt_chars = len(PromptBuilder.system())
        # v1.1.4-fix (bug 4.2): surface ContextManager's file selection
        # (what's actually auto-attached to the prompt) alongside
        # conversation memory — previously invisible even though it was
        # already being computed on every task once wired in.
        try:
            file_selection = self._context_manager.select_context()
        except Exception:
            file_selection = None
        return {
            "memory": mem_status,
            "project_context": proj_status,
            "files": file_selection,
            "system_prompt_chars": sys_prompt_chars,
            "system_prompt_tokens": _estimate_tokens(PromptBuilder.system()),
            "workspace": str(self.tools.workspace) if self.tools.workspace else None,
        }

    def clear_context(self) -> Dict[str, Any]:
        """v1.0.9: /clear command — wipe conversation memory + compaction.

        Does NOT touch the project's CLAUDE.md — that's persistent project
        instructions, not conversation history.
        """
        self.memory.clear()
        logger.info("[agent] context cleared by /clear command")
        return {"ok": True, "message": "Context cleared. Start fresh."}

    def compact_context(self) -> Dict[str, Any]:
        """v1.0.9: /compact command — summarise old messages, keep recent.

        Uses the active provider to generate a summary of the conversation
        so far, then replaces old messages with the summary. The most
        recent 4 messages are kept verbatim so the agent has immediate
        context for the next turn.
        """
        if not self.memory.messages:
            return {"ok": True, "message": "Nothing to compact — memory is empty."}
        if len(self.memory.messages) <= 4:
            return {"ok": True, "message": "Not enough messages to compact (need > 4)."}

        # Build a compaction prompt
        history = self.memory.to_prompt_history()
        compact_prompt = (
            "Summarise the following conversation, preserving:\n"
            "- Key decisions and their rationale\n"
            "- Files that were read, created, or modified (with paths)\n"
            "- Any errors encountered and how they were resolved\n"
            "- Open questions or TODOs\n\n"
            "Be concise but complete. Output a markdown summary, no preamble.\n\n"
            f"Conversation to summarise:\n{history}"
        )
        try:
            # v1.1.3-fix (bug 1.5): route through _generate_with_retry
            # (via _generate_with_explicit_system) instead of calling
            # provider.generate() directly. A transient 429/503 during
            # auto-compaction used to fail compact_context(), which then
            # fell back to force-trimming half the history WITHOUT a
            # summary — silently losing context. With retry, transient
            # errors recover; only persistent failures fall back.
            summary, _tok_in, _tok_out = self._generate_with_explicit_system(
                system_prompt="You are a conversation summarizer. Be concise and factual.",
                user_prompt=compact_prompt,
            )
            summary = summary.strip()
            self.memory.compact(summary, keep_recent=4)
            logger.info("[agent] context compacted by /compact command")
            return {"ok": True, "message": "Context compacted.",
                    "summary_chars": len(summary),
                    "kept_messages": len(self.memory.messages)}
        except Exception as e:
            logger.error("[agent] compaction failed: %s", e)
            return {"ok": False, "error": str(e)}

    def _maybe_auto_compact(self) -> bool:
        """v1.0.9: auto-compact if context is over 85% of budget.

        Called before each LLM call in _run_agent_loop. Returns True if
        compaction happened (so the loop can log it / notify the UI).

        v1.0.5-correctness: if ``compact_context()`` fails (provider
        error, network blip), the previous code returned ``False`` and
        the loop proceeded to call the provider with context that was
        already over 85% of budget — which may exceed the context
        window and fail with a more confusing error. We now fall back
        to force-trimming the oldest non-system messages so the next
        LLM call has at least a chance of fitting (BUGS_REPORT M-RT-2).
        """
        if not self.memory.should_compact(threshold=0.85):
            return False
        logger.info("[agent] auto-compacting context (over 85%% budget)")
        result = self.compact_context()
        if result.get("ok", False):
            return True
        # Graceful degradation: force-trim oldest messages so we don't
        # blow the context window on the next LLM call. Keep the most
        # recent half of the messages (or at least 4).
        try:
            msgs = self.memory.messages
            keep = max(4, len(msgs) // 2)
            if len(msgs) > keep:
                logger.warning(
                    "[agent] auto-compact failed (%s); force-trimming to last %d messages",
                    result.get("error", "unknown"), keep,
                )
                self.memory.messages = msgs[-keep:]
                self.memory.save()
        except Exception as trim_err:
            logger.error("[agent] force-trim also failed: %s", trim_err)
        return False

    def _emit(self, event: AgentEvent, **data):
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception as e:
                logger.debug(f"Event callback error: {e}")
        if self.verbose:
            logger.debug(f"[EVENT] {event.value}: {data}")

    def _generate(self, prompt: str, *, include_system: bool = True) -> Tuple[str, int, int]:
        """Call the active provider's generate() with a plain prompt.

        v1.0.6: when ``include_system`` is True, the system prompt is
        sent as a SEPARATE ``role="system"`` message — not concatenated
        into the user prompt. Many providers (Groq's llama-3.3-70b in
        particular) follow agent/tool-use instructions far more reliably
        when the system role is distinct from the user role. The old
        concatenated form was getting the agent's "use JSON tool calls"
        instruction treated as user content, which led to the model
        replying with prose ("I can't write files, here's the code…")
        instead of emitting a write_file tool call.

        Returns (text, tokens_in, tokens_out).
        """
        provider = self._registry.active
        if not provider.is_loaded:
            provider.load()
        messages: List[ProviderMessage] = []
        if include_system:
            messages.append(ProviderMessage(role="system", content=PromptBuilder.system()))
        messages.append(ProviderMessage(role="user", content=prompt))
        resp = self._generate_with_retry(provider, messages)
        return resp.text, int(getattr(resp, 'tokens_in', 0) or 0), int(getattr(resp, 'tokens_out', 0) or 0)

    def _generate_with_explicit_system(self, system_prompt: str,
                                        user_prompt: str) -> Tuple[str, int, int]:
        """v1.0.6 — call the provider with an EXPLICIT system message.

        Used by the agent loop so the tool-use instructions land in the
        system role (where models pay attention to them) instead of
        being concatenated into the user prompt (where they get treated
        as content to respond to).

        Returns (text, tokens_in, tokens_out).
        """
        provider = self._registry.active
        if not provider.is_loaded:
            provider.load()
        messages: List[ProviderMessage] = [
            ProviderMessage(role="system", content=system_prompt),
            ProviderMessage(role="user", content=user_prompt),
        ]
        resp = self._generate_with_retry(provider, messages)
        return resp.text, int(getattr(resp, 'tokens_in', 0) or 0), int(getattr(resp, 'tokens_out', 0) or 0)

    # ── v1.0.5-correctness: provider call with retry + token tracking ──

    # H-RT-1: a single transient 429/5xx/network blip used to abort the
    # entire agent task. We now retry with exponential backoff + jitter.
    # H-RT-3: real token usage from `ProviderResponse.tokens_in/out` was
    # being discarded; the only accounting was a char-count heuristic.
    # We now record actual usage to the shared `token_tracker` so the
    # UI's cost/burn-rate/budget features are accurate.

    _RETRY_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
    _RETRY_MAX_ATTEMPTS = 3
    _RETRY_BASE_DELAY = 1.0   # seconds
    _RETRY_MAX_DELAY = 16.0   # seconds

    def _is_retryable(self, exc: Exception) -> bool:
        """Return True if *exc* looks like a transient provider error."""
        msg = str(exc).lower()
        # ProviderError carries the upstream status code in its message
        # in a few well-known forms. Match the most common substrings.
        if any(s in msg for s in ("rate limit", "rate-limit", "too many requests",
                                   "service unavailable", "bad gateway",
                                   "gateway timeout", "temporarily unavailable",
                                   "connection reset", "timed out", "timeout",
                                   "read timeout", "connection aborted")):
            return True
        # Status code patterns like "HTTP 429" / "status 503" / "[429]"
        import re as _re
        m = _re.search(r'(?:http|status)?\s*[\[\(]?(\d{3})[\]\)]?', msg)
        if m:
            try:
                code = int(m.group(1))
                if code in self._RETRY_STATUS_CODES:
                    return True
            except ValueError:
                pass
        return False

    def _generate_with_retry(self, provider, messages):
        """Call provider.generate with exponential-backoff retry.

        Retries on transient errors (429, 5xx, timeouts, connection
        resets) up to ``_RETRY_MAX_ATTEMPTS`` times. Auth errors and
        other 4xx (non-transient) errors are NOT retried — they bubble
        out immediately so the caller can surface them to the user.

        Also records the actual token usage (tokens_in / tokens_out) to
        the agent's ``token_tracker`` if one is attached, so the UI's
        cost/burn-rate/budget features reflect real usage instead of
        a char-count heuristic.

        v1.0.5-hotfix: added INFO logging at call start/end so the user
        can see what's happening when a call is slow (the user reported
        "долго отвечает" — with this logging they'll see exactly which
        step is slow and how long it took).
        """
        import random as _random
        last_exc: Optional[Exception] = None
        call_start = time.time()
        model_name = getattr(getattr(provider, "config", None), "model", "?")
        logger.info("[agent] LLM call starting — provider=%s model=%s timeout=%.0fs",
                    provider.provider_id, model_name,
                    getattr(getattr(provider, "config", None), "timeout", 0))
        for attempt in range(1, self._RETRY_MAX_ATTEMPTS + 1):
            try:
                resp = provider.generate(messages)
                elapsed = time.time() - call_start
                logger.info("[agent] LLM call completed in %.1fs — provider=%s model=%s tokens_in=%d tokens_out=%d",
                            elapsed, provider.provider_id, model_name,
                            int(getattr(resp, "tokens_in", 0) or 0),
                            int(getattr(resp, "tokens_out", 0) or 0))
                # v1.0.5-correctness: record real token usage (H-RT-3).
                try:
                    tracker = getattr(self, "_token_tracker", None)
                    if tracker is not None:
                        tracker.record(
                            provider=provider.provider_id,
                            model=getattr(resp, "model", None) or provider.config.model,
                            tokens_in=int(getattr(resp, "tokens_in", 0) or 0),
                            tokens_out=int(getattr(resp, "tokens_out", 0) or 0),
                        )
                except Exception as track_err:
                    logger.debug("[agent] token_tracker.record failed: %s", track_err)
                # v1.1.0: record quota usage (per-section daily counter).
                # Only count SUCCESSFUL calls — failed retries don't
                # consume the user's daily quota.
                try:
                    quota = getattr(self, "_quota_tracker", None)
                    if quota is not None:
                        quota.record(
                            section=self.section,
                            provider=provider.provider_id,
                            model=getattr(resp, "model", None) or provider.config.model,
                        )
                except Exception as quota_err:
                    logger.debug("[agent] quota.record failed: %s", quota_err)
                return resp
            except Exception as exc:
                last_exc = exc
                elapsed = time.time() - call_start
                if attempt >= self._RETRY_MAX_ATTEMPTS:
                    logger.warning("[agent] LLM call FAILED after %d attempts (%.1fs): %s",
                                   attempt, elapsed, exc)
                    break
                if not self._is_retryable(exc):
                    # Non-transient (auth, bad request, etc.) — don't retry.
                    logger.warning("[agent] LLM call FAILED (%.1fs, non-retryable): %s",
                                   elapsed, exc)
                    break
                # Exponential backoff with jitter: 1s, 2s, 4s, ... capped at 16s.
                delay = min(self._RETRY_MAX_DELAY,
                            self._RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                delay = delay * (0.5 + 0.5 * _random.random())
                logger.warning(
                    "[agent] transient provider error (attempt %d/%d, %.1fs): %s — retrying in %.1fs",
                    attempt, self._RETRY_MAX_ATTEMPTS, elapsed, exc, delay,
                )
                # Sleep, but check cancellation every 0.25s so a Stop
                # click can still abort the wait.
                slept = 0.0
                while slept < delay:
                    if self.tools.is_cancelled():
                        raise exc
                    step = min(0.25, delay - slept)
                    time.sleep(step)
                    slept += step
        # All retries exhausted (or non-retryable) — re-raise the last error.
        raise last_exc if last_exc is not None else RuntimeError("generate failed")

    # ── High-level API ───────────────────────────────────────────────────

    def run(self, description: str, task_type: TaskType = TaskType.AGENTIC,
            language: str = "python", context: Optional[str] = None,
            file_path: Optional[str] = None, **gen_kwargs) -> TaskResult:
        task = Task(
            type=task_type,
            description=description,
            context=context,
            file_path=file_path,
            language=language,
        )
        return self._run_agent_loop(task, **gen_kwargs)

    def write(self, description: str, language: str = "python",
              context: Optional[str] = None, file_path: Optional[str] = None,
              **gen_kwargs) -> TaskResult:
        return self.run(description, TaskType.WRITE, language, context, file_path, **gen_kwargs)

    def edit(self, code: str, instruction: str, language: str = "python",
             file_path: Optional[str] = None, **gen_kwargs) -> TaskResult:
        return self.run(instruction, TaskType.EDIT, language, code, file_path, **gen_kwargs)

    def refactor(self, code: str, goal: str = "improve quality",
                 language: str = "python", file_path: Optional[str] = None,
                 **gen_kwargs) -> TaskResult:
        return self.run(goal, TaskType.REFACTOR, language, code, file_path, **gen_kwargs)

    def analyze(self, code: str, language: str = "python", **gen_kwargs) -> TaskResult:
        return self.run("Analyze this code", TaskType.ANALYZE, language, code, **gen_kwargs)

    def generate_test(self, code: str, language: str = "python", **gen_kwargs) -> TaskResult:
        return self.run("Generate comprehensive tests", TaskType.TEST, language, code, **gen_kwargs)

    def debug(self, code: str, error_message: str, language: str = "python",
              file_path: Optional[str] = None, **gen_kwargs) -> TaskResult:
        return self.run(error_message, TaskType.DEBUG, language, code, file_path, **gen_kwargs)

    def chat(self, message: str, **gen_kwargs) -> TaskResult:
        """Non-agent chat — single LLM round-trip with conversation history.

        v1.0.5-correctness: the old implementation concatenated the
        system prompt into the user content and called ``_generate``
        with ``include_system=False``, which sent tool-use instructions
        as user content (the same bug the v1.0.6 agent-loop refactor
        fixed but never propagated to ``chat()``). Models that treat
        system-role content as authoritative (Groq's llama-3.3-70b in
        particular) would reply with prose instead of following
        instructions. We now send the system prompt as a separate
        ``role="system"`` message (BUGS_REPORT H-RT-9).

        Also: ``self.memory.add("user", message)`` previously ran
        BEFORE the generate call, so if generate raised, the user
        message was orphaned in memory with no assistant reply. We now
        add it only after a successful generate.
        """
        task = Task(type=TaskType.CHAT, description=message)
        history = self.memory.to_prompt_history()
        user_prompt = ""
        if history:
            user_prompt += f"## Conversation so far\n{history}\n\n"
        # v1.1.3-fix (bug 1.10): removed the legacy "[USER]\n{message}\n\n[CLEW]"
        # markers. They were left over from the old concatenated-prompt
        # scheme (v1.0.5) where the system prompt was inlined into the
        # user content. Since v1.0.6 the system prompt is a separate
        # role="system" message, and the markers confuse llama-3 family
        # models into echoing them back ("[USER] I'm ready [CLEW] ...").
        # The model now sees just the user's message as user content,
        # which is what it expects.
        user_prompt += message

        try:
            # v1.0.6-style: system prompt as a separate role="system" message.
            output, tok_in, tok_out = self._generate(user_prompt, include_system=True)
            # Only persist to memory after a successful generate, so a
            # failed call doesn't leave an orphaned user message.
            self.memory.add("user", message)
            self.memory.add("assistant", output)
            self.task_history.append(task)
            return TaskResult(success=True, output=output, iterations=1,
                              metadata={"total_tokens_in": tok_in, "total_tokens_out": tok_out})
        except Exception as e:
            return TaskResult(success=False, output="", error=str(e))

    # ── Agent Loop ────────────────────────────────────────────────────────

    def _is_write_or_execute_tool(self, tool_call: Optional[ToolCall]) -> bool:
        """Check if a tool call would write files or execute commands.

        v1.0.5-correctness: ``undo_write`` was missing from this set,
        so under ``autonomy="always_ask"`` the agent could silently
        roll back a file the user just edited — ``undo_write``
        overwrites the current file with a backup, which is a write
        operation (BUGS_REPORT M-RT-6).
        """
        if tool_call is None:
            return False
        write_tools = {ToolName.WRITE_FILE, ToolName.EXECUTE_COMMAND, ToolName.RUN_CODE,
                       ToolName.DELETE_FILE, ToolName.RENAME_FILE, ToolName.APPLY_DIFF,
                       ToolName.WRITE_BINARY_FILE, ToolName.MKDIR, ToolName.STR_REPLACE,
                       # v1.0.11: git stage/commit modify repo state
                       ToolName.GIT_STAGE, ToolName.GIT_COMMIT,
                       # v1.0.5-correctness: undo_write overwrites the
                       # current file with a backup — treat as a write.
                       ToolName.UNDO_WRITE,
                       # v1.1.3-fix (bug 1.3): MCP tools can have side
                       # effects (filesystem write_file, github push, etc.)
                       # so they are subject to the autonomy gate. The
                       # actual confirmation is requested inside
                       # _call_mcp_tool, but listing it here keeps the
                       # metadata consistent for the UI.
                       ToolName.CALL_MCP_TOOL}
        return tool_call.name in write_tools

    def _create_plan_with_cancel_check(self, task: Task, autonomy: str = "always_ask") -> Tuple[str, bool]:
        """Create a plan and check if the user wants to cancel.
        Returns (plan, cancelled).
        autonomy: 'always_ask' | 'new_files_only' | 'never_ask'
        """
        plan = self._create_plan(task)
        self._emit(AgentEvent.PLAN_CREATED, plan=plan, task=task.description)
        return plan, False

    def _run_agent_loop(self, task: Task, **gen_kwargs) -> TaskResult:
        all_steps: List[AgentStep] = []
        autonomy = gen_kwargs.pop("autonomy", "always_ask")

        # v1.1.1-fix: reset per-run token accumulators
        self._run_tokens_in = 0
        self._run_tokens_out = 0

        # v1.1.0: enforce daily quota BEFORE doing any LLM work. The user
        # gets a clear, friendly error instead of burning a provider call
        # they'll be billed for but can't use.
        if self._quota_tracker and self._quota_tracker.exhausted(self.section):
            remaining = self._quota_tracker.remaining(self.section)
            limit = self._quota_tracker.get_daily_limit(self.section)
            err_msg = (
                f"Daily {self.section} limit reached ({limit} requests/day). "
                f"Limit resets at 00:00 UTC. "
                f"Future versions will offer paid tiers with higher limits."
            )
            self._emit(AgentEvent.ERROR, error=err_msg, iteration=0)
            return TaskResult(
                success=False, output=err_msg, iterations=0,
                error="quota_exhausted",
                metadata={"section": self.section, "limit": limit, "remaining": remaining},
            )

        plan = ""
        if self.enable_planning and task.type not in (TaskType.CHAT, TaskType.ANALYZE):
            plan, cancelled = self._create_plan_with_cancel_check(task, autonomy)
            if cancelled:
                return TaskResult(success=False, output="Plan cancelled by user.",
                                  iterations=0, error="cancelled")

        step_history: List[str] = []
        # v1.0.6: keep the SYSTEM_PROMPT and the task prompt SEPARATE.
        # The old code concatenated them into one user-prompt string and
        # called _generate with the system-inclusion flag turned OFF —
        # which meant the tool-use instructions were sent as user
        # content, not as a system message. Many providers (notably
        # Groq's llama-3.3-70b) treat "system" content as authoritative
        # instructions and "user" content as a request to respond to —
        # so the model was answering "I can't write files, here's the
        # code instead of writing them" instead of emitting a
        # write_file tool call.
        #
        # v1.0.9: append CLAUDE.md project instructions to the system
        # prompt so they're treated as authoritative project rules.
        system_prompt = PromptBuilder.system(section=self.section)
        # v1.0.9: inject CLAUDE.md project instructions
        proj_instructions = self._project_context.instructions()
        if proj_instructions:
            system_prompt = system_prompt + "\n\n" + proj_instructions
        # v1.1.4-fix (bug 4.2): auto-attach relevant project files, scored
        # by ContextManager (pinned files, recently-touched files, files
        # named in the task, config/entry files) within a token budget —
        # this is what "smart file selection" was supposed to do all
        # along; it was previously computed nowhere. Wrapped in try/except
        # since this must never break a task that has no project root yet
        # (e.g. chat-only mode with no workspace set).
        try:
            file_block = self._context_manager.build_context_block(
                query=task.description or "",
                mentioned_files=[task.file_path] if task.file_path else None,
            )
            if file_block:
                system_prompt = system_prompt + (
                    "\n\n# Relevant project files (auto-attached, "
                    "token-budgeted — not exhaustive; use read_file for "
                    "anything not shown here)\n\n" + file_block
                )
        except Exception as e:
            logger.debug("[agent] context file auto-attach failed: %s", e)
        # v1.0.11: inject skill catalog so the agent knows what skills
        # are available. Full skill bodies are NOT injected (saves
        # context tokens) — the agent calls get_skill(id) to pull the
        # full body when it decides a skill fits the task.
        if self._skills:
            skill_catalog = build_skill_catalog(self._skills)
            if skill_catalog:
                system_prompt = system_prompt + "\n\n" + skill_catalog
        # v1.1.0: inject MCP tool catalog (available in ALL sections).
        # If no MCP servers are configured/running, this is a no-op.
        try:
            from .mcp_manager import get_mcp_manager
            mcp_catalog = get_mcp_manager().catalog_prompt()
            if mcp_catalog:
                system_prompt = system_prompt + "\n\n" + mcp_catalog
        except Exception as e:
            logger.debug("[agent] MCP catalog injection failed: %s", e)
        # v1.1.0: Heavy Code section gets a stronger system prompt suffix
        # that tells the agent it has subagent/multi-agent capabilities
        # and should use them for complex tasks.
        if self.section == "heavy_code":
            system_prompt = system_prompt + "\n\n" + (
                "# Heavy Code Mode\n"
                "You are running in HEAVY CODE mode — you have access to "
                "spawn_subagent and spawn_multi_agents tools for complex, "
                "multi-step engineering tasks.\n\n"
                "When to use subagents:\n"
                "- For tasks with multiple independent parts (e.g. refactor "
                "  3 unrelated files) → use spawn_multi_agents to parallelize.\n"
                "- For deep analysis before a big change → spawn an architect "
                "  subagent to plan, then execute the plan yourself.\n"
                "- For code review after a change → spawn a reviewer subagent.\n"
                "- For test generation → spawn a tester subagent.\n\n"
                # v1.1.3-fix (bug 2.2): the old prompt claimed subagents
                # are "CHEAPER" — this was misleading. Subagents consume
                # the same LLM tokens (each spawn = 2-4 extra round-trips)
                # and now correctly inherit the parent's quota counter, so
                # they are NOT a way to bypass the daily limit. We now
                # explicitly tell the model when NOT to spawn (small tasks)
                # and clarify that subagents share the user's quota and
                # autonomy settings.
                "Important notes about subagents:\n"
                "- Subagents inherit your autonomy and quota settings — "
                "they do NOT bypass the daily request limit.\n"
                "- For 1-2 file edits, do the work directly. Spawning a "
                "subagent adds 2-4 LLM round-trips of overhead, which is "
                "wasteful for trivial tasks.\n"
                "- Subagents run with their own fresh context window, so "
                "they are useful when the parent context is getting large "
                "or when the sub-task requires a different system prompt.\n"
                "- All sub-agents honour the Stop button — if the user "
                "cancels, sub-agents halt too.\n"
            )
        initial_user_prompt = PromptBuilder.task_prompt(
            task, plan=plan, history=self.memory.to_prompt_history()
        )

        current_user_prompt = initial_user_prompt
        final_output = ""
        success = True
        error_msg = None

        for iteration in range(1, self.max_iterations + 1):
            # v1.1.1: honor Stop — check BEFORE starting another LLM call /
            # tool call, so cancelling actually halts further agent
            # activity instead of just muting UI updates while the loop
            # keeps running to completion in the background.
            if self.tools.is_cancelled():
                error_msg = "Cancelled by user"
                success = False
                self._emit(AgentEvent.ERROR, error=error_msg, iteration=iteration)
                break

            # v1.1.3-fix (bug 2.3): re-check quota INSIDE the loop, not
            # just before iteration 1. If another Clew process (or a
            # recursive sub-agent, see bug 1.2) exhausts the daily limit
            # mid-run, the previous code kept making LLM calls past the
            # quota. We now bail out as soon as the limit is hit.
            if self._quota_tracker and self._quota_tracker.exhausted(self.section):
                remaining = self._quota_tracker.remaining(self.section)
                limit = self._quota_tracker.get_daily_limit(self.section)
                error_msg = (
                    f"Daily {self.section} quota exhausted mid-run "
                    f"(limit={limit}/day, remaining={remaining}). "
                    f"Resets at 00:00 UTC."
                )
                success = False
                self._emit(AgentEvent.ERROR, error=error_msg, iteration=iteration)
                break

            self._emit(AgentEvent.ITERATION_START, iteration=iteration, max=self.max_iterations)

            # v1.0.9: auto-compact if context is over 85% of budget.
            # This prevents silent context loss in long conversations.
            if self._maybe_auto_compact():
                self._emit(AgentEvent.THOUGHT,
                           thought="[auto-compacted context to stay under token budget]",
                           iteration=iteration)
                # v1.1.3-fix (bug 1.9): rebuild the user prompt more
                # carefully. The previous code did
                # ``initial_user_prompt.split("## Previous Steps")[0]``
                # which corrupted the prompt if "## Previous Steps"
                # appeared in the user's task description (rare but
                # possible when discussing the agent itself). It also
                # dropped the "## Execution Plan" section on subsequent
                # iterations. We now rebuild from the structured parts:
                #   - everything before "## Previous Steps" (plan + task)
                #   - the new (compacted) history under "## Previous Steps"
                # If "## Previous Steps" is NOT in the initial prompt
                # (e.g. first iteration), we just append it.
                if "## Previous Steps" in initial_user_prompt:
                    pre_history = initial_user_prompt.split("## Previous Steps", 1)[0]
                else:
                    # No "## Previous Steps" section in the initial prompt
                    # — use the whole thing and append the section.
                    pre_history = initial_user_prompt.rstrip() + "\n\n"
                current_user_prompt = (
                    pre_history
                    + "## Previous Steps\n"
                    + self.memory.to_prompt_history()
                )

            try:
                # v1.0.6: explicit system + user — model now treats the
                # tool-use instructions as authoritative.
                raw, tok_in, tok_out = self._generate_with_explicit_system(
                    system_prompt, current_user_prompt
                )
                # v1.1.1-fix: accumulate real token counts for the UI
                self._run_tokens_in += tok_in
                self._run_tokens_out += tok_out
            except Exception as e:
                error_msg = str(e)
                self._emit(AgentEvent.ERROR, error=error_msg, iteration=iteration)
                success = False
                break

            thought = OutputParser.extract_thought(raw)
            # v1.0.5: detect the [WRITE_FILE] token. The token is a hint
            # — it does NOT replace the JSON tool call. We surface it as
            # part of the TOOL_CALLED event so the UI can pre-load the
            # original file for diff review and highlight the target
            # path in the project tree.
            write_intent = OutputParser.extract_write_intent(raw)
            if write_intent:
                intent_path, intent_line = write_intent
                # Strip the token line so it doesn't pollute the JSON parse.
                raw_for_parse = OutputParser.strip_write_token(raw)
            else:
                intent_path, raw_for_parse = None, raw
            tool_call = OutputParser.parse_tool_call(raw_for_parse)
            is_final = OutputParser.is_final(raw)
            final_text = OutputParser.parse_final_answer(raw)

            step = AgentStep(thought=thought, is_final=is_final)
            self._emit(AgentEvent.THOUGHT, thought=thought, iteration=iteration)

            # Sanity-check: if the model emitted [WRITE_FILE] X but the
            # tool call targets a different path, warn (don't fail — the
            # tool call is the source of truth, the token is a hint).
            if write_intent and tool_call is not None:
                tc_path = tool_call.args.get("path")
                if tc_path and intent_path and tc_path != intent_path:
                    logger.warning(
                        "[agent] [WRITE_FILE] token path %r does not match "
                        "tool call path %r — using tool call path",
                        intent_path, tc_path,
                    )

            if is_final and final_text is not None:
                final_output = final_text
                step.observation = "[DONE]"
                all_steps.append(step)
                self._emit(AgentEvent.DONE, output=final_output, iterations=iteration)
                break

            if tool_call is not None:
                # v1.0.5-security: re-check cancellation BEFORE executing the
                # tool. The LLM call can take 30–120 s; if the user clicked
                # Stop during that window, the LLM still returned and we
                # would have executed the parsed tool_call (writing/deleting
                # files, running commands) AFTER the user pressed Stop
                # (BUGS_REPORT H-RT-7). Bail out now instead.
                if self.tools.is_cancelled():
                    self._emit(AgentEvent.ITERATION_END,
                               iteration=iteration, reason="user_stop_before_tool")
                    logger.info("[agent] cancelled before tool execution: %s",
                                tool_call.name.value)
                    break

                step.action = tool_call
                # v1.0.5: include write_intent in the event payload so
                # the UI can show "[WRITE_FILE] path" before the write
                # lands, and pre-warm the diff-review pane.
                event_payload: Dict[str, Any] = {
                    "tool": tool_call.name.value,
                    "args": tool_call.args,
                }
                if write_intent:
                    event_payload["write_intent"] = intent_path
                self._emit(AgentEvent.TOOL_CALLED, **event_payload)

                observation = self.tools.execute(tool_call)

                step_summary = (
                    f"Step {iteration}: [{tool_call.name.value}] → "
                    + observation[:300].replace("\n", " ")
                )
                step_history.append(step_summary)
                if len(step_history) > 3:
                    step_history = step_history[-3:]

                step.observation = observation[:500] + " ... [truncated]" if len(observation) > 500 else observation

                if tool_call.result and len(tool_call.result) > 500:
                    tool_call.result = tool_call.result[:500] + " ... [truncated]"

                self._emit(AgentEvent.TOOL_RESULT, tool=tool_call.name.value, result=observation[:200])

                # v1.0.6: continuation prompt is built from the INITIAL
                # user prompt (task + plan + history) + the step
                # observations accumulated so far. The system prompt is
                # sent separately by _generate_with_explicit_system.
                current_user_prompt = (
                    initial_user_prompt
                    + "\n"
                    + "\n".join(
                        PromptBuilder.continuation(s, i + 1)
                        for i, s in enumerate(step_history)
                    )
                )
            else:
                # v1.0.6: model didn't emit a tool call OR a final_answer
                # marker — it just wrote prose. This is the failure mode
                # where the model says "I can't write files, here's the
                # code instead of writing them" because it didn't
                # internalise that it IS the agent.
                #
                # v1.0.5-hotfix: the old code retried ONLY on iteration 1
                # and then accepted prose on iteration 2. But the retry
                # condition was ``iteration == 1`` — so iteration 2's
                # prose was accepted as final. BUT if the model kept
                # emitting short non-JSON prose on every retry, the loop
                # would still spin to max_iterations=8 before giving up
                # (the user saw 5+ iterations of "no tool call" in the
                # logs). We now:
                #   1. Retry up to 2 times with the reminder (iterations 1-2).
                #   2. On iteration 3+, accept the prose as the final answer
                #      instead of looping to exhaustion — the model clearly
                #      isn't going to emit a tool call, and the user is
                #      waiting.
                #   3. Emit a THOUGHT event with the full raw text so the
                #      UI shows what the model actually said (the user
                #      reported the UI was stuck on "planning..." — this
                #      is because the THOUGHT event had an empty/truncated
                #      thought when the model's response was short prose).
                if iteration <= 2 and task.type == TaskType.AGENTIC:
                    logger.info(
                        "[agent] iter %d produced no tool call and no "
                        "final_answer — retrying with explicit reminder",
                        iteration,
                    )
                    # Re-emit the thought with the FULL raw text so the
                    # UI can show what the model actually said (not just
                    # the extracted thought which may be empty).
                    if not thought and raw.strip():
                        self._emit(AgentEvent.THOUGHT,
                                   thought=raw.strip()[:500],
                                   iteration=iteration,
                                   note="no_tool_call_retry")
                    current_user_prompt = (
                        initial_user_prompt
                        + "\n\n"
                        + "REMINDER: You are the agent. You have tools. "
                        "Do NOT write code in your reply and ask the "
                        "user to run it — call the write_file or "
                        "str_replace tool DIRECTLY. Output one JSON "
                        "tool call now, or {\"final_answer\": \"...\"} "
                        "if you truly have nothing to do."
                    )
                    all_steps.append(step)
                    self._emit(AgentEvent.ITERATION_END, iteration=iteration)
                    continue
                # Iteration 3+ with no tool call: accept the prose as
                # the final answer. The model isn't cooperating, and
                # looping further just wastes the user's time.
                logger.info(
                    "[agent] iter %d: accepting prose as final answer "
                    "(model not emitting tool calls)", iteration,
                )
                final_output = raw
                step.is_final = True
                all_steps.append(step)
                self._emit(AgentEvent.DONE, output=final_output, iterations=iteration)
                break

            all_steps.append(step)
            self._emit(AgentEvent.ITERATION_END, iteration=iteration)
        else:
            # for/else: loop completed without `break` — max iterations
            # exhausted. If `raw` was assigned (at least one iteration
            # ran before any potential break), use it as the final
            # output; otherwise (e.g. max_iterations=0) there's nothing
            # to surface.
            #
            # v1.0.5-correctness: previously ``success = bool(final_output)``
            # was True whenever the model emitted ANY text — but at this
            # point ``final_output`` is just the last raw LLM response
            # (a tool call or prose), NOT a final answer. Reporting
            # ``success=True`` misled the UI into thinking the task had
            # completed successfully when in fact the agent ran out of
            # steam mid-tool-call (BUGS_REPORT H-RT-10). We now report
            # ``success=False`` and surface ``error_msg`` so the caller
            # can distinguish "exhausted" from "done".
            if not final_output:
                # Use locals() instead of dir() — dir() returns the
                # module-level namespace when called at class scope,
                # which would falsely report `raw` as defined.
                final_output = locals().get("raw", "")
            error_msg = f"Max iterations ({self.max_iterations}) reached"
            success = False

        tool_calls = [s.action for s in all_steps if s.action]

        # v1.1.3-fix (bug 1.8): don't pollute ContextMemory with
        # cancelled/failed tasks. The previous code wrote
        # "[Task: ...] <description>" + empty/partial output unconditionally,
        # so the next conversation saw an orphaned user message with no
        # assistant reply. Auto-compaction would then bake that into the
        # summary, permanently corrupting the context. We now:
        #   - SKIP the memory write entirely if the task was cancelled
        #     (success=False and error_msg == "Cancelled by user")
        #   - For other failures, write the user message but mark it
        #     with metadata={"failed": True} so a future filter can
        #     skip it in to_prompt_history().
        was_cancelled = (
            not success
            and error_msg is not None
            and "cancel" in error_msg.lower()
        )
        if not was_cancelled:
            user_meta = {}
            if not success:
                user_meta["failed"] = True
                user_meta["error"] = (error_msg or "")[:200]
            self.memory.add("user", f"[Task: {task.type.value}] {task.description[:200]}", **user_meta)
            # Only write the assistant message if there's actual output.
            if final_output and final_output.strip():
                self.memory.add("assistant", final_output[:1000], failed=not success)

        task.metadata["iterations"] = len(all_steps)
        task.metadata["success"] = success
        self.task_history.append(task)

        return TaskResult(
            success=success,
            output=final_output,
            error=error_msg,
            iterations=len(all_steps),
            steps=all_steps,
            tool_calls=tool_calls,
            plan=plan,
            metadata={
                "language": task.language,
                "task_type": task.type.value,
                "total_tokens_in": self._run_tokens_in,
                "total_tokens_out": self._run_tokens_out,
            },
        )

    def _create_plan(self, task: Task) -> str:
        context = task.context[:500] if task.context else ""
        if task.file_path:
            context += f"\nFile: {task.file_path}"
        prompt = PromptBuilder.plan(task.description, context)
        import time as _time
        plan_start = _time.time()
        logger.info("[agent] planning step starting — task=%r", task.description[:80])
        try:
            plan, tok_in, tok_out = self._generate(prompt)
            self._run_tokens_in += tok_in
            self._run_tokens_out += tok_out
            plan = plan.strip()
            logger.info("[agent] planning step completed in %.1fs (%d chars)",
                        _time.time() - plan_start, len(plan))
            # v1.0.6: validate plan against available tools (M-RT-8).
            # If the plan references tools that don't exist, the agent
            # would waste iterations trying to call them.
            _warn_unknown_tools(plan)
            return plan
        except Exception as e:
            logger.warning("[agent] planning failed after %.1fs: %s",
                           _time.time() - plan_start, e)
            return ""

    def run_stream(self, description: str, task_type: TaskType = TaskType.AGENTIC,
                   language: str = "python", context: Optional[str] = None,
                   **gen_kwargs) -> Generator[str, None, None]:
        task = Task(type=task_type, description=description, context=context, language=language)
        try:
            provider = self._registry.active
            if not provider.is_loaded:
                provider.load()
            messages = [
                ProviderMessage(role="system", content=PromptBuilder.system()),
                ProviderMessage(role="user", content=PromptBuilder.task_prompt(task)),
            ]
            for chunk in provider.stream(messages, **gen_kwargs):
                yield chunk
        except Exception as e:
            yield f"\n[ERROR] {e}"

    def get_status(self) -> Dict[str, Any]:
        return {
            "tasks_completed": len(self.task_history),
            "memory_messages": len(self.memory.messages),
            "max_iterations": self.max_iterations,
            "planning_enabled": self.enable_planning,
            "workspace": str(self.tools.workspace),
        }

    def get_history(self) -> List[Task]:
        return self.task_history

    def clear_history(self):
        self.task_history.clear()
        self.memory.clear()
        logger.info("Agent history and memory cleared")

    def set_workspace(self, path: str):
        self.tools.set_workspace(path)
        # v1.0.9: update project context so CLEW.md is re-read for
        # the new project root.
        self._project_context.set_root(path)
        # v1.1.4-fix: re-index files for the new project root — without
        # this the ContextManager kept scoring files from the previous
        # project after switching folders.
        self._context_manager.set_root(path)
        # v1.0.11: reload skills for the new project root
        self._reload_skills()
        logger.info(f"Agent workspace set to: {path}")


# ── AgentWorker (QThread) — Non-blocking UI ──────────────────────────────

class AgentWorker(QThread):
    """Runs agent tasks in a background QThread — does NOT block UI."""

    result_ready = Signal(object)   # TaskResult
    step_update = Signal(str, str)  # event_type, data_json
    progress = Signal(int, str)  # percent, message
    error = Signal(str)

    def __init__(self, agent_runtime: AgentRuntime, task: Task, parent=None, **gen_kwargs):
        # v1.1.2-fix (bridge freeze): previously this called
        # super().__init__() with NO parent, and `parent=self` passed by
        # web_bridge.py's `AgentWorker(agent, task, parent=self)` was
        # silently swallowed into **gen_kwargs instead of being forwarded
        # to QThread. Every sibling worker (GenerationWorker, OneShotWorker,
        # TitleWorker) does `super().__init__(parent)` — this one didn't.
        #
        # Effect: the QThread had no Qt parent, so it was kept alive only
        # by the Python reference `self._agent_worker` on WebBridge. As
        # soon as `_on_agent_done` set `self._agent_worker = None` (right
        # after emitting agent_final/token_stats_updated), Python could
        # garbage-collect the QThread wrapper while Qt hadn't finished
        # tearing the native thread down yet — a classic "QThread:
        # Destroyed while thread is still running" hazard that can stall
        # the Qt event loop right at the moment the QWebChannel needs it
        # to flush the just-emitted signals to the JS side. Backend logs
        # showed everything completing normally (emit() returns
        # immediately, before delivery), but the browser side never saw
        # the update until the whole app was restarted (fresh event loop,
        # and the reloaded chat renders via the unrelated load_chat path).
        super().__init__(parent)
        self.agent = agent_runtime
        self.task = task
        self.gen_kwargs = gen_kwargs
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    def _on_event(self, event: AgentEvent, data: Dict[str, Any]):
        if self._cancelled:
            return
        self.step_update.emit(event.value, json.dumps(data, default=str))

    def run(self):
        try:
            original_callback = self.agent.on_event
            self.agent.on_event = self._on_event
            # v1.1.1: give the agent loop a way to see `cancel()` — without
            # this, Stop only silenced UI events while the loop kept
            # running writes/commands/deletes in the background.
            self.agent.set_cancel_check(self._is_cancelled)

            result = self.agent._run_agent_loop(self.task, **self.gen_kwargs)

            self.agent.on_event = original_callback
            self.result_ready.emit(result)

        except Exception as e:
            logger.error(f"AgentWorker failed: {e}")
            self.error.emit(str(e))
        finally:
            self.agent.set_cancel_check(None)