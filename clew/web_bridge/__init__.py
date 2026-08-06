"""
clew.web_bridge — Qt-free path/config helpers.

v2.2.0: the legacy ``ClewBridge`` QObject (PySide6 / QWebChannel
adapter) has been removed. The browser frontend now talks to the
backend exclusively through the HTTP REST API + SSE in
:mod:`clew.api_server` (served by :mod:`clew.web_server`).

The path / config / chat-store helpers in ``_paths_config`` are kept
because ``clew.api_server`` and ``clew.cli`` import them. The Qt
QThread workers (``workers.py``) are removed — :mod:`clew.api_server`
uses :mod:`threading` directly for streaming.

Public API kept for backward compatibility:

    from clew.web_bridge import (
        _clew_home, _config_path, _chats_dir,
        _load_templates_from_disk, _load_skills_from_disk,
        _classify_user_intent,
        _load_config, _save_config,
        _chat_path, _load_chat, _save_chat,
    )

Removed (was Qt-only):

    ClewBridge, GenerationWorker, OneShotWorker, TitleWorker
"""

from ._paths_config import (
    _clew_home, _config_path, _chats_dir,
    _load_templates_from_disk, _load_skills_from_disk,
    _classify_user_intent,
    _load_config, _save_config,
    _chat_path, _load_chat, _save_chat,
)

__all__ = [
    "_clew_home", "_config_path", "_chats_dir",
    "_load_templates_from_disk", "_load_skills_from_disk",
    "_classify_user_intent",
    "_load_config", "_save_config",
    "_chat_path", "_load_chat", "_save_chat",
]
