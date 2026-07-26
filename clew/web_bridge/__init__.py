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
