"""
Legacy shim for clew.web_bridge.

v2.2.0: the original 4126-line monolith (and the later refactored
``clew/web_bridge/`` package) exposed a PySide6 ``ClewBridge``
QObject to the in-process HTML frontend via QWebChannel. The Qt
GUI has been removed; the browser now talks to the backend via
the HTTP REST API + SSE in :mod:`clew.api_server`.

This file re-exports only the path/config helpers that
:mod:`clew.api_server` and :mod:`clew.cli` still depend on::

    from clew.web_bridge import _load_config
    from clew.web_bridge import _chat_path, _load_chat, _save_chat

The Qt-only names (``ClewBridge``, ``GenerationWorker``,
``OneShotWorker``, ``TitleWorker``) are kept as hard-error shims
so old code fails loudly instead of silently.
"""

from clew.web_bridge import (
    _clew_home, _config_path, _chats_dir,
    _load_templates_from_disk, _load_skills_from_disk,
    _classify_user_intent,
    _load_config, _save_config,
    _chat_path, _load_chat, _save_chat,
)
from clew.web_bridge.bridge import ClewBridge, ClewBridgeRemovedError

__all__ = [
    "ClewBridge", "ClewBridgeRemovedError",
    "_clew_home", "_config_path", "_chats_dir",
    "_load_templates_from_disk", "_load_skills_from_disk",
    "_classify_user_intent",
    "_load_config", "_save_config",
    "_chat_path", "_load_chat", "_save_chat",
]
