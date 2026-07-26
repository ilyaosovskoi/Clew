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
