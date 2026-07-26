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