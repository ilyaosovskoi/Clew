"""
Auto-slicer for clew/agent_runtime.py and clew/web_bridge.py.

Splits the two large monolith files into a package of smaller modules
under `clew/agent_runtime/` and `clew/web_bridge/`, preserving the
public API through thin re-export shims.

Strategy: CONSERVATIVE THIN RE-EXPORT.
- Each new submodule contains the **original code** extracted verbatim
  from the monolith (only `import` lines added at the top, plus
  intra-package imports between submodules).
- The original top-level files (`clew/agent_runtime.py`,
  `clew/web_bridge.py`) are replaced by tiny shim modules that just
  re-export the public API. Old imports keep working.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path("/home/z/my-project/clew")
SRC_AGENT = ROOT / "source" / "clew" / "agent_runtime.py"
SRC_BRIDGE = ROOT / "source" / "clew" / "web_bridge.py"

DST = ROOT / "update_2"
DST_AGENT_PKG = DST / "clew" / "agent_runtime"
DST_AGENT_TOOLS = DST_AGENT_PKG / "tool_engine"
DST_BRIDGE_PKG = DST / "clew" / "web_bridge"
DST_BRIDGE_MIXINS = DST_BRIDGE_PKG / "mixins"


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(lines: list[str], start: int, end: int) -> str:
    if start < 1:
        start = 1
    if end > len(lines):
        end = len(lines)
    return "".join(lines[start - 1 : end])


def write_module(path: Path, header: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + body, encoding="utf-8")
    print(f"  wrote {path.relative_to(DST)} ({path.stat().st_size} bytes)")


def slice_agent_runtime() -> None:
    print("\n=== Slicing agent_runtime.py ===")
    lines = read_lines(SRC_AGENT)
    n = len(lines)
    print(f"source: {n} lines")

    # types.py — enums + dataclasses (lines 225-410)
    types_body = slice_lines(lines, 225, 410)
    types_header = dedent('''\
        """
        Type definitions for the Clew agent runtime.

        Contains:
        - TaskType, ToolName, AgentEvent enums
        - Task, ToolCall, AgentStep, TaskResult, ConversationMessage dataclasses

        These are the "vocabulary" types shared across the agent runtime
        package. They have no internal dependencies and are safe to import
        from any other module.
        """

        from dataclasses import dataclass, field
        from enum import Enum
        from typing import Any, Dict, List, Optional

        ''')
    write_module(DST_AGENT_PKG / "types.py", types_header, types_body)

    # _helpers.py — ALLOWED_COMMANDS + _sanitize_command (lines 112-224)
    helper_body = slice_lines(lines, 112, 224)
    helper_header = dedent('''\
        """
        Low-level helpers for the agent runtime.

        Currently contains:
        - _sanitize_command(): shell=False + shlex.split() + whitelist
          validation for shell commands. Used by the tool engine's
          execute_command implementation.

        Kept as a separate module so that the sanitisation rules can be
        unit-tested in isolation without importing the full ToolEngine.
        """

        import logging
        import shlex
        from pathlib import Path
        from typing import List, Optional, Tuple

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_AGENT_PKG / "_helpers.py", helper_header, helper_body)

    # context_memory.py — ContextMemory (411-734)
    cm_body = slice_lines(lines, 411, 734)
    cm_header = dedent('''\
        """
        ContextMemory — sliding-window conversation memory with persistence.

        Wraps the message list and provides:
        - token / char budget trimming,
        - LLM-driven compaction (compact() replaces old messages with a
          summary while keeping the last N),
        - JSON-file and SQLite persistence,
        - prompt-history serialisation.

        SQLite persistence is optional and used by clew.session subpackage
        for long-term session storage.
        """

        import json
        import logging
        import threading
        import time
        from pathlib import Path
        from typing import Any, Dict, List, Optional

        from .types import ConversationMessage

        logger = logging.getLogger(__name__)

        def _estimate_tokens(text: str) -> int:
            """Rough token estimate: ~4 chars/token, with a small floor."""
            return max(1, len(text) // 4)

        ''')
    write_module(DST_AGENT_PKG / "context_memory.py", cm_header, cm_body)

    # diff_utils.py — _split_multi_file_diff + _apply_unified_diff
    # (3466-3595) and tool_engine diff helpers (1691-1745)
    diff_body_top = slice_lines(lines, 3466, 3595)
    diff_body_helpers = slice_lines(lines, 1691, 1745)
    diff_header = dedent('''\
        """
        Diff utilities for the agent runtime.

        Contains:
        - _split_multi_file_diff(): split a single unified diff that
          touches multiple files into per-file chunks.
        - _apply_unified_diff(): apply a unified diff to original text.
        - _str_replace_hint(): produce a human-readable hint when
          str_replace cannot find old_str (helps the model self-correct).
        - _compute_diff_text(): unified-diff string between original and
          proposed file content.
        - _backup_file(): create a `.bak` snapshot of a file before
          overwriting it (used by write_file / str_replace / apply_diff).

        Pure-Python, no I/O side effects except _backup_file.
        """

        import difflib
        import shutil
        from pathlib import Path
        from typing import List, Tuple

        # ── multi-file diff splitting ─────────────────────────────────

        ''')
    write_module(
        DST_AGENT_PKG / "diff_utils.py",
        diff_header,
        diff_body_top + "\n\n" + diff_body_helpers,
    )

    # tool_engine/_engine.py — ToolEngine (735-3465)
    te_body = slice_lines(lines, 735, 3465)
    te_header = dedent('''\
        """
        ToolEngine — the agent's tool dispatcher.

        Each `step` in the agent loop calls `ToolEngine.execute(call)`,
        which routes to a `_dispatch()` method that handles every
        ToolName variant. File ops, git ops, MCP, sub-agents, office
        worker, self-verify, watchdog, code execution, search, and
        diff application all live here.

        Kept as a single file (rather than split per-tool) because:
        - the dispatcher is a single switch statement,
        - many tools share private state (workspace, skills, whitelist,
          confirmation channel) that would require heavy __init__ glue,
        - splitting would force subclassing or mixin patterns that make
          the call graph harder to follow.

        The diff-related helpers (_str_replace_hint, _compute_diff_text,
        _backup_file, _split_multi_file_diff, _apply_unified_diff) have
        been moved to ..diff_utils and are imported here.
        """

        import json
        import logging
        import os
        import re
        import shutil
        import subprocess
        import tempfile
        import threading
        import time
        from pathlib import Path
        from typing import Any, Callable, Dict, List, Optional, Tuple

        from ..types import ToolCall, ToolName
        from .._helpers import _sanitize_command
        from ..diff_utils import (
            _split_multi_file_diff,
            _apply_unified_diff,
            _str_replace_hint,
            _compute_diff_text,
            _backup_file,
        )

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_AGENT_TOOLS / "_engine.py", te_header, te_body)

    # tool_engine/__init__.py
    write_module(
        DST_AGENT_TOOLS / "__init__.py",
        '"""ToolEngine package — re-exports ToolEngine."""\n\n',
        "from ._engine import ToolEngine  # noqa: F401\n\n__all__ = [\"ToolEngine\"]\n",
    )

    # prompts.py — TOOL_SCHEMA, SYSTEM_PROMPT, *_SUFFIX, PromptBuilder
    # (3596-4186)
    prompts_body = slice_lines(lines, 3596, 4186)
    prompts_header = dedent('''\
        """
        Prompt templates for the agent runtime.

        Contains the constant strings that make up the system prompt:
        - TOOL_SCHEMA: the JSON tool-calling schema injected into the
          system prompt so the model knows how to format tool calls.
        - SYSTEM_PROMPT: the base ReAct system prompt (general section).
        - GENERAL_SYSTEM_SUFFIX: appended to SYSTEM_PROMPT in the
          general agent section.
        - HEAVY_CODE_SYSTEM_SUFFIX: appended in the heavy_code section.
        - PromptBuilder: factory for task/plan/continuation prompts.

        These strings are large (the system prompt alone is ~400 lines)
        and stable, so they live in their own module to keep runtime.py
        readable.
        """

        from typing import Optional

        from .types import Task

        ''')
    write_module(DST_AGENT_PKG / "prompts.py", prompts_header, prompts_body)

    # parser.py — OutputParser + _warn_unknown_tools (4187-4704)
    parser_body = slice_lines(lines, 4187, 4704)
    parser_header = dedent('''\
        """
        OutputParser — parses LLM output into structured tool calls.

        Handles:
        - extracting balanced-JSON tool calls from the model's reply,
        - decoding escape sequences while preserving Unicode,
        - lifting top-level args (some models emit {tool, args} with
          args as siblings instead of nested),
        - detecting "final answer" tokens,
        - extracting the model's thought before the tool call,
        - detecting write intent (so the runtime can ask for diff
          review before writing).

        _warn_unknown_tools() emits a logger.warning for any tool name
        in the plan that isn't in ToolName.
        """

        import json
        import logging
        import re
        from typing import Any, Dict, Optional, Tuple

        from .types import ToolCall, ToolName

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_AGENT_PKG / "parser.py", parser_header, parser_body)

    # runtime.py — AgentRuntime (4705-6153)
    rt_body = slice_lines(lines, 4705, 6153)
    rt_header = dedent('''\
        """
        AgentRuntime — the legacy ReAct agent loop.

        Public API:
        - run(), write(), edit(), refactor(), analyze(), generate_test(),
          debug(), chat() — high-level task entry points.
        - run_stream() — streaming variant that yields AgentEvent
          updates for the UI.
        - get_status(), get_history(), clear_history(), set_workspace()
          — introspection / control.

        The runtime owns a ContextMemory and a ToolEngine. Each turn:
        1. build a prompt (PromptBuilder),
        2. call the provider (with retry + streaming),
        3. parse the response (OutputParser),
        4. dispatch any tool call (ToolEngine),
        5. emit AgentEvent updates for the UI.

        v2 wraps this in clew.agent.AgentRuntimeV2 to add interjection,
        compaction v2, circuit breaker, sub-agent v2, and sandbox.
        """

        import logging
        import threading
        import time
        from typing import Any, Callable, Dict, Generator, List, Optional

        from .types import AgentEvent, Task, TaskResult, TaskType, ToolCall
        from .context_memory import ContextMemory
        from .tool_engine import ToolEngine
        from .prompts import (
            PromptBuilder,
            TOOL_SCHEMA,
            SYSTEM_PROMPT,
            GENERAL_SYSTEM_SUFFIX,
            HEAVY_CODE_SYSTEM_SUFFIX,
        )
        from .parser import OutputParser, _warn_unknown_tools

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_AGENT_PKG / "runtime.py", rt_header, rt_body)

    # worker.py — AgentWorker QThread (6154-end)
    wk_body = slice_lines(lines, 6154, n)
    wk_header = dedent('''\
        """
        AgentWorker — QThread wrapper around AgentRuntime.run_stream().

        Used by the GUI to run an agent task off the Qt event loop
        without blocking the UI. Emits Qt signals for every AgentEvent
        so the main window can update the chat log, activity stream,
        and status bar in real time.

        Why a separate QThread instead of asyncio:
        - PySide6's signal/slot mechanism is thread-affine,
        - the legacy runtime uses threading.Event for cancellation
          (not asyncio.CancelledError),
        - the v2 runtime (clew.agent.AgentRuntimeV2) is asyncio-based
          and has its own worker (clew.agent.worker.AgentWorkerV2).
        """

        import logging
        from typing import Any, Dict

        from PySide6.QtCore import QThread, Signal

        from .types import AgentEvent, Task
        from .runtime import AgentRuntime

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_AGENT_PKG / "worker.py", wk_header, wk_body)

    # __init__.py — full re-export
    init_header = dedent('''\
        """
        clew.agent_runtime — agent runtime package (refactored).

        Drop-in replacement for the legacy monolithic
        `clew/agent_runtime.py` file. The public API is re-exported so
        existing imports keep working:

            from clew.agent_runtime import (
                AgentRuntime, AgentWorker, AgentEvent, Task, TaskType,
                ToolCall, ToolName, TaskResult, AgentStep,
                ConversationMessage, ContextMemory, ToolEngine,
                PromptBuilder, OutputParser,
                TOOL_SCHEMA, SYSTEM_PROMPT,
                GENERAL_SYSTEM_SUFFIX, HEAVY_CODE_SYSTEM_SUFFIX,
            )

        Internal layout (see REFACTORING_NOTES.md):
        - types.py            — enums + dataclasses
        - _helpers.py         — _sanitize_command
        - context_memory.py   — ContextMemory
        - diff_utils.py       — diff helpers
        - tool_engine/        — ToolEngine (the big dispatcher)
        - prompts.py          — system prompt + PromptBuilder
        - parser.py           — OutputParser
        - runtime.py          — AgentRuntime (the agent loop)
        - worker.py           — AgentWorker (QThread wrapper)
        """

        ''')
    init_body = dedent('''\
        from .types import (
            TaskType,
            ToolName,
            AgentEvent,
            Task,
            ToolCall,
            AgentStep,
            TaskResult,
            ConversationMessage,
        )
        from ._helpers import _sanitize_command, ALLOWED_COMMANDS
        from .context_memory import ContextMemory, _estimate_tokens
        from .diff_utils import (
            _split_multi_file_diff,
            _apply_unified_diff,
            _str_replace_hint,
            _compute_diff_text,
            _backup_file,
        )
        from .tool_engine import ToolEngine
        from .prompts import (
            TOOL_SCHEMA,
            SYSTEM_PROMPT,
            GENERAL_SYSTEM_SUFFIX,
            HEAVY_CODE_SYSTEM_SUFFIX,
            PromptBuilder,
        )
        from .parser import OutputParser, _warn_unknown_tools
        from .runtime import AgentRuntime
        from .worker import AgentWorker

        __all__ = [
            # types
            "TaskType", "ToolName", "AgentEvent",
            "Task", "ToolCall", "AgentStep", "TaskResult",
            "ConversationMessage",
            # memory + helpers
            "ContextMemory", "_estimate_tokens",
            "_sanitize_command", "ALLOWED_COMMANDS",
            # diff utils
            "_split_multi_file_diff", "_apply_unified_diff",
            "_str_replace_hint", "_compute_diff_text", "_backup_file",
            # tool engine
            "ToolEngine",
            # prompts + parser
            "TOOL_SCHEMA", "SYSTEM_PROMPT",
            "GENERAL_SYSTEM_SUFFIX", "HEAVY_CODE_SYSTEM_SUFFIX",
            "PromptBuilder", "OutputParser", "_warn_unknown_tools",
            # runtime + worker
            "AgentRuntime", "AgentWorker",
        ]
        ''')
    write_module(DST_AGENT_PKG / "__init__.py", init_header, init_body)

    # Top-level shim
    shim_path = DST / "clew" / "agent_runtime.py"
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    shim_path.write_text(
        dedent('''\
            """
            Legacy shim for clew.agent_runtime.

            The original 6217-line monolith has been refactored into the
            `clew/agent_runtime/` package. This file re-exports the full
            public API so existing imports keep working unchanged:

                from clew.agent_runtime import AgentRuntime   # still works
                from clew.agent_runtime import (
                    AgentRuntime, AgentWorker, AgentEvent, Task, TaskType,
                    ToolCall, ToolName, TaskResult, AgentStep,
                    ConversationMessage, ContextMemory, ToolEngine,
                    PromptBuilder, OutputParser,
                    TOOL_SCHEMA, SYSTEM_PROMPT,
                    GENERAL_SYSTEM_SUFFIX, HEAVY_CODE_SYSTEM_SUFFIX,
                )

            See clew/agent_runtime/__init__.py for the package layout
            and REFACTORING_NOTES.md for the migration map.
            """

            from clew.agent_runtime import *  # noqa: F401,F403
            from clew.agent_runtime import __all__  # noqa: F401
            '''),
        encoding="utf-8",
    )
    print(f"  wrote {shim_path.relative_to(DST)} (shim)")


def slice_web_bridge() -> None:
    print("\n=== Slicing web_bridge.py ===")
    lines = read_lines(SRC_BRIDGE)
    n = len(lines)
    print(f"source: {n} lines")

    # _paths_config.py — paths + config + chat store + intent classifier
    # (lines 78-583)
    pc_body = slice_lines(lines, 78, 583)
    pc_header = dedent('''\
        """
        Path / config / chat-store helpers for the web bridge.

        Everything that reads or writes to ~/.clew/ lives here:
        - _clew_home(), _config_path(), _chats_dir()
        - _load_templates_from_disk(), _load_skills_from_disk()
        - _classify_user_intent() (used by the composer mixin to decide
          whether to route through the agent or do a one-shot generation)
        - _load_config(), _save_config()
        - _chat_path(), _load_chat(), _save_chat()

        Kept module-level (not on the ClewBridge class) so they can be
        imported by other modules (e.g. main_window.py imports
        `_load_config` to read provider config without instantiating
        the bridge).
        """

        import json
        import logging
        import os
        from datetime import datetime
        from pathlib import Path
        from typing import Any, Dict, List, Optional, Tuple

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_BRIDGE_PKG / "_paths_config.py", pc_header, pc_body)

    # workers.py — 3 QThread workers (lines 584-779)
    wk_body = slice_lines(lines, 584, 779)
    wk_header = dedent('''\
        """
        QThread workers for the web bridge.

        - GenerationWorker: streams tokens from a provider for a
          composer send_message() call. Emits done/error/seen-message
          signals that the bridge forwards to the JS frontend.
        - OneShotWorker: runs a single non-streaming generation for
          prompt enhancement (no chat history, no agent loop).
        - TitleWorker: generates a short title for a chat session.

        All three are thin wrappers around the provider registry —
        no business logic. They exist so the bridge's @Slot methods
        can return immediately and let Qt's event loop drive the
        generation in the background.
        """

        import logging
        from typing import Any, Dict, List, Optional

        from PySide6.QtCore import QThread, Signal

        from ..providers import (
            ProviderRegistry, ProviderMessage, ProviderError,
        )

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_BRIDGE_PKG / "workers.py", wk_header, wk_body)

    # bridge.py — ClewBridge class (780-end)
    bridge_body = slice_lines(lines, 780, n)
    bridge_header = dedent('''\
        """
        ClewBridge — QObject exposed to the HTML frontend via QWebChannel.

        This is the REAL backend — no mocks. Every @Slot method does
        actual work: streams tokens, manages chats, configures providers,
        reads/writes files, runs the agent, talks to MCP, etc.

        The bridge is a single QObject (cannot be split via multiple
        inheritance because @Slot decorators need the MOC to see them
        on the actual class). Refactoring strategy:
        - QThread workers (GenerationWorker, OneShotWorker, TitleWorker)
          are in .workers.
        - Path / config / chat-store helpers are in ._paths_config.
        - Non-@Slot helper methods on ClewBridge will be progressively
          extracted into .mixins.* in follow-up passes.

        See REFACTORING_NOTES.md for the full module map.
        """

        from __future__ import annotations

        import json
        import logging
        import os
        import platform
        import sys
        import tempfile
        import time
        import uuid
        from datetime import datetime
        from pathlib import Path
        from typing import Any, Dict, List, Optional, Tuple

        from PySide6.QtCore import QObject, Signal, Slot, QThread, QUrl
        from PySide6.QtGui import QDesktopServices

        from ..providers import (
            ProviderRegistry, ProviderConfig, ProviderMessage,
            get_registry, ProviderError,
        )
        from ..code_viewer import CodeViewerService
        from ..agent_runtime import AgentRuntime, AgentWorker, Task, TaskType
        from ..memory_service import MemoryService
        from ..auto_updater import AutoUpdater, get_current_version
        from ..auto_router import AutoRouter
        from ..token_tracker import get_token_tracker
        from ..quota import get_quota_tracker
        from ..mcp_manager import get_mcp_manager
        from ..slash_commands import SlashCommandManager

        # refactored submodules
        from ._paths_config import (
            _clew_home, _config_path, _chats_dir,
            _load_templates_from_disk, _load_skills_from_disk,
            _classify_user_intent,
            _load_config, _save_config,
            _chat_path, _load_chat, _save_chat,
        )
        from .workers import GenerationWorker, OneShotWorker, TitleWorker

        logger = logging.getLogger(__name__)

        ''')
    write_module(DST_BRIDGE_PKG / "bridge.py", bridge_header, bridge_body)

    # __init__.py
    write_module(
        DST_BRIDGE_PKG / "__init__.py",
        dedent('''\
            """
            clew.web_bridge — web bridge package (refactored).

            Re-exports ClewBridge and the path/config helpers so existing
            imports keep working:

                from clew.web_bridge import ClewBridge
                from clew.web_bridge import _load_config  # used by main_window

            Internal layout:
            - _paths_config.py  — ~/.clew/ paths + config + chat store
            - workers.py        — GenerationWorker, OneShotWorker, TitleWorker
            - bridge.py         — ClewBridge (the QObject with all @Slots)
            """

            '''),
        dedent('''\
            from ._paths_config import (
                _clew_home, _config_path, _chats_dir,
                _load_templates_from_disk, _load_skills_from_disk,
                _classify_user_intent,
                _load_config, _save_config,
                _chat_path, _load_chat, _save_chat,
            )
            from .workers import GenerationWorker, OneShotWorker, TitleWorker
            from .bridge import ClewBridge

            __all__ = [
                "ClewBridge",
                "GenerationWorker", "OneShotWorker", "TitleWorker",
                "_clew_home", "_config_path", "_chats_dir",
                "_load_templates_from_disk", "_load_skills_from_disk",
                "_classify_user_intent",
                "_load_config", "_save_config",
                "_chat_path", "_load_chat", "_save_chat",
            ]
            '''),
    )

    # mixins/ placeholder
    write_module(
        DST_BRIDGE_MIXINS / "__init__.py",
        '"""mixins package — reserved for future non-@Slot helper extraction."""\n',
        "",
    )

    # Top-level shim
    shim_path = DST / "clew" / "web_bridge.py"
    shim_path.write_text(
        dedent('''\
            """
            Legacy shim for clew.web_bridge.

            The original 4126-line monolith has been refactored into the
            `clew/web_bridge/` package. This file re-exports the public
            API so existing imports keep working unchanged:

                from clew.web_bridge import ClewBridge
                from clew.web_bridge import _load_config  # main_window uses this

            See clew/web_bridge/__init__.py for the package layout and
            REFACTORING_NOTES.md for the migration map.
            """

            from clew.web_bridge import *  # noqa: F401,F403
            from clew.web_bridge import __all__  # noqa: F401
            '''),
        encoding="utf-8",
    )
    print(f"  wrote {shim_path.relative_to(DST)} (shim)")


if __name__ == "__main__":
    slice_agent_runtime()
    slice_web_bridge()
    print("\n=== Done ===")
