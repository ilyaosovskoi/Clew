"""Legacy alias for the original AgentRuntime.

The original 5750-line `clew/agent_runtime.py` is preserved unchanged
in `clew/agent/legacy/_legacy_agent_runtime.py` and re-exported here.
This keeps existing imports working:

    from clew.agent.legacy import AgentRuntime, ToolEngine, ContextMemory, ...

New code should prefer `clew.agent.AgentRuntimeV2` which wraps a legacy
runtime and adds v2 features (interjection, compaction v2, circuit breaker,
sub-agent v2, sandbox).
"""

# Re-export the legacy module so its public API is reachable under the
# new package path. The original `clew/agent_runtime.py` file is left
# untouched for backwards compatibility with code that imports it directly.
from clew.agent_runtime import (
    AgentRuntime,
    ToolEngine,
    ContextMemory,
    PromptBuilder,
    OutputParser,
    AgentWorker,
    AgentEvent,
    ToolName,
    TOOL_SCHEMA,
    HEAVY_CODE_SYSTEM_SUFFIX,
)

__all__ = [
    "AgentRuntime",
    "ToolEngine",
    "ContextMemory",
    "PromptBuilder",
    "OutputParser",
    "AgentWorker",
    "AgentEvent",
    "ToolName",
    "TOOL_SCHEMA",
    "HEAVY_CODE_SYSTEM_SUFFIX",
]
