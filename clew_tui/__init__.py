"""clew_tui — a full-screen Textual TUI for the Clew agent.

Kept in a separate top-level package (not inside clew/) and talking to the core
only through clew_tui.bridge.ClewBridge, so it never becomes another parallel
agent-loop path.
"""

from .app import ClewTUIApp
from .bridge import ClewBridge, ProviderChoice

__all__ = ["ClewTUIApp", "ClewBridge", "ProviderChoice"]
