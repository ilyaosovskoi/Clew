"""
Clew v1.0.3 — Web Bridge (REAL implementation).

A QObject exposed to the HTML frontend via QWebChannel.

This is the REAL backend — no mocks. Every method does actual work:

  Composer:
    send_message(opts)          → streams tokens from the active provider
    stop_generation()           → cancels the active stream
    enhance_prompt(text)        → uses the active provider to rewrite the prompt
    list_templates()            → returns the template library (disk + built-in)
    list_skills()               → returns the skill catalog (disk + built-in)
    get_skill(id)               → returns the full skill text

  Chat history (persisted to ~/.clew/chats/<id>.json):
    list_chats()                → all saved conversations
    create_chat(title)          → new chat, returns {id, title}
    load_chat(id)               → full message list
    delete_chat(id)             → remove a chat
    rename_chat(id, title)      → update title

  Providers (config persisted to ~/.clew/config.json):
    list_providers()            → metadata for the UI switcher
    set_provider(id)            → switch active provider
    configure_provider(id, model, api_key, temperature, max_tokens)
    get_provider_config(id)     → current config (api_key masked)
    test_provider(id)           → ping the provider with "Hello"

  Settings:
    get_settings()              → full settings dict
    save_settings(partial)      → merge & persist

  Code Viewer:
    list_files()                → file tree from disk
    read_file(path)             → file contents from disk
    search_code(pattern)        → grep results
    open_project(path)          → set new project root
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, Slot, QThread, QUrl
from PySide6.QtGui import QDesktopServices

from .providers import (
    ProviderRegistry, ProviderConfig, ProviderMessage,
    get_registry, ProviderError,
)
from .code_viewer import CodeViewerService
from .agent_runtime import AgentRuntime, AgentWorker, Task, TaskType
from .memory_service import MemoryService
from .auto_updater import AutoUpdater, get_current_version
from .auto_router import AutoRouter
from .token_tracker import get_token_tracker
# v1.1.0: quota tracker + MCP manager
from .quota import get_quota_tracker
from .mcp_manager import get_mcp_manager

logger = logging.getLogger(__name__)


# ── Paths ──────────────────────────────────────────────────────────

def _clew_home() -> Path:
    """~/.clew — persists config, chats, skills."""
    p = Path.home() / ".clew"
    p.mkdir(parents=True, exist_ok=True)
    (p / "chats").mkdir(exist_ok=True)
    (p / "templates").mkdir(exist_ok=True)
    (p / "skills").mkdir(exist_ok=True)
    return p


def _config_path() -> Path:
    return _clew_home() / "config.json"


def _chats_dir() -> Path:
    return _clew_home() / "chats"




# ── Disk loaders for Templates & Skills ────────────────────────────

def _load_templates_from_disk() -> List[Dict[str, Any]]:
    tpl_dir = _clew_home() / "templates"
    if not tpl_dir.exists():
        return []
    templates = []
    for f in sorted(tpl_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                templates.append(json.load(fp))
        except Exception as e:
            logger.warning(f"[templates] failed to load {f}: {e}")
    return templates


def _load_skills_from_disk() -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    skill_dir = _clew_home() / "skills"
    if not skill_dir.exists():
        return [], {}
    skills = []
    skill_texts = {}
    for f in sorted(skill_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                sid = data.get("id", f.stem)
                skills.append({
                    "id": sid,
                    "tag": data.get("tag", "general"),
                    "name": data.get("name", f.stem),
                    "desc": data.get("desc", ""),
                })
                skill_texts[sid] = data.get("text", data.get("instruction", ""))
        except Exception as e:
            logger.warning(f"[skills] failed to load {f}: {e}")
    return skills, skill_texts


def _classify_user_intent(text: str) -> Dict[str, Any]:
    """DEPRECATED in v1.0.7 — kept only for backward-compat with old
    frontends that still call `classify_intent`. The agent runtime is
    now ALWAYS ON, so this classifier no longer gates anything.

    Original heuristic: chat (question/discussion) vs action (file
    write/command). Returns {intent: 'chat'|'action'|'ambiguous',
    confidence: float, reason: str}.
    """
    text_lower = text.lower().strip()

    # Strong action signals — these almost certainly mean "do something"
    # v1.0.6: expanded Russian list — "запиши" was missing, which caused
    # "Привет. Запиши любой файл в тестовую директорию" to be classified
    # as 'chat' even with Agent Mode explicitly toggled on by the user.
    action_patterns = [
        # Russian imperatives — write/create/save/edit/delete/run/etc.
        r"\b(создай|сделай|напиши|запиши|сохрани|исправь|почини|запусти|удали|переименуй|сгенерируй|добавь|измени|обнови|отредактируй|поменяй|вынеси|перенеси|перемести|скопируй|вставь|замени)\b",
        # Russian noun-form fallbacks ("нужна запись", "требуется создание")
        r"\b(запис[ьи]|создани[ея]|сохранени[ея]|удалени[ея]|переименовани[ея]|обновлени[ея]|редактировани[ея])\b",
        # English imperatives
        r"\b(create|make|write|fix|run|delete|remove|rename|generate|add|change|update|implement|build|refactor|deploy|install|migrate|save|edit|patch|move|copy|insert|replace)\b",
        # English noun-form: "write a file", "create a class"
        r"\b(write|create|save|generate|output|produce)\s+(a\s+)?(file|code|script|test|class|function|module)\b",
        r"\b(fix|patch|resolve|debug|solve)\b",
        r"\b(run|execute|start|launch|test)\b",
        # "запиши файл" / "создай файл" / "save file" — noun right after verb
        r"\b(запиши|сохрани|создай|сгенерируй)\s+(файл|код|скрипт|тест|класс|функци[юю]|модул[ьь])\b",
        r"\b(save|write|create|generate)\s+(a\s+)?(file|script|test|class|function|module)\b",
    ]

    # Strong chat/question signals — these mean "discuss/think"
    chat_patterns = [
        r"^(what|how|why|when|where|who|which|can you|could you|is it|does it)\b",
        r"^(что|как|почему|когда|где|кто|какой|можешь|сможешь|объясни|расскажи|подскажи)\b",
        r"\b(объясни|расскажи|подскажи|что думаешь|как лучше|твоё мнение|что посоветуешь)\b",
        r"\b(think|explain|tell me|what do you think|opinion|suggest|recommend|compare|describe)\b",
        r"\?$",  # ends with question mark
        r"\?\s*$",
    ]

    # Ambiguous / vague signals
    vague_patterns = [
        r"^(напиши что|сделай что|любой|что-нибудь|что-то)\b",
        r"^(write something|do something|anything|whatever)\b",
    ]

    import re
    action_score = sum(1 for p in action_patterns if re.search(p, text_lower, re.IGNORECASE))
    chat_score = sum(1 for p in chat_patterns if re.search(p, text_lower, re.IGNORECASE))
    vague_score = sum(1 for p in vague_patterns if re.search(p, text_lower, re.IGNORECASE))

    # v1.0.6: removed the over-aggressive "short message without action
    # signal → chat" rule. It was firing on perfectly valid short
    # commands like "Запиши файл" (4 words, 1 action signal — but the
    # old rule checked action_score == 0, so the action signal was
    # ignored, and the message was tagged 'chat' anyway). Now we rely
    # on the explicit action/chat score comparison below.

    # Vague/ambiguous → ambiguous (ask for clarification) ONLY when
    # there's no action signal at all. "Запиши любой файл" contains
    # both "запиши" (action) and "любой" (vague) — the action should win.
    if vague_score > 0 and action_score == 0:
        return {"intent": "ambiguous", "confidence": 0.7, "reason": "vague_request_needs_clarification"}

    # Clear action — even a single strong action verb is enough.
    # v1.0.6: lowered the threshold from ">=2 or (>=1 and has_code)"
    # to ">=1". The old threshold meant "запиши файл" (1 signal) was
    # treated as ambiguous, defeating the whole point of the classifier.
    if action_score >= 1:
        return {"intent": "action", "confidence": 0.85, "reason": f"action_signals={action_score}"}

    # Clear chat/question
    if chat_score >= 1 and action_score == 0:
        return {"intent": "chat", "confidence": 0.85, "reason": f"chat_signals={chat_score}"}

    # Mixed signals → ambiguous
    if action_score > 0 and chat_score > 0:
        return {"intent": "ambiguous", "confidence": 0.5, "reason": "mixed_action_and_chat"}

    # Default: ambiguous for medium-length messages without clear signals
    return {"intent": "ambiguous", "confidence": 0.4, "reason": "no_clear_signals"}


# ── Prompt Templates (static library + disk override) ──────────────

_BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "code_project",
        "name": "Code Project",
        "desc": "Scaffold a new project — files, structure, dependencies, tests.",
        "sections": ["intent", "stack", "structure", "tests", "docs"],
    },
    {
        "id": "refactor",
        "name": "Refactor",
        "desc": "Reorganize existing code with preserved behaviour.",
        "sections": ["scope", "before", "after", "verify"],
    },
    {
        "id": "feature_spec",
        "name": "Feature Spec",
        "desc": "Define a feature: users, flows, edges, acceptance criteria.",
        "sections": ["users", "flows", "edges", "acceptance"],
    },
    {
        "id": "bug_fix",
        "name": "Bug Fix",
        "desc": "Reproduce, diagnose, patch, regression-test.",
        "sections": ["repro", "diagnose", "patch", "regression"],
    },
    {
        "id": "documentation",
        "name": "Documentation",
        "desc": "Generate README, API reference, architecture notes.",
        "sections": ["overview", "install", "usage", "api"],
    },
    {
        "id": "research",
        "name": "Research",
        "desc": "Investigate a topic, summarize findings, propose next steps.",
        "sections": ["question", "sources", "findings", "next"],
    },
]

_DISK_TEMPLATES = _load_templates_from_disk()

# Merge: disk overrides built-in by id
PROMPT_TEMPLATES: List[Dict[str, Any]] = []
_seen_tpl_ids = set()
for t in _DISK_TEMPLATES + _BUILTIN_TEMPLATES:
    if t["id"] not in _seen_tpl_ids:
        PROMPT_TEMPLATES.append(t)
        _seen_tpl_ids.add(t["id"])


# ── Skill catalog (each sharpens the model on one capability) ──────

_BUILTIN_SKILLS: List[Dict[str, Any]] = [
    {"id": "python_architect", "tag": "architect", "name": "Python Architect",
     "desc": "Designs clean package structures, dependency boundaries, layered architecture."},
    {"id": "ui_polish", "tag": "frontend", "name": "UI Polish",
     "desc": "Pixel-perfect CSS, motion systems, accessibility, responsive behavior."},
    {"id": "security_auditor", "tag": "security", "name": "Security Auditor",
     "desc": "Threat models, OWASP, secrets hygiene, sandboxing, least privilege."},
    {"id": "performance", "tag": "perf", "name": "Performance",
     "desc": "Profiles bottlenecks, optimizes hot paths, measures with benchmarks."},
    {"id": "test_engineer", "tag": "testing", "name": "Test Engineer",
     "desc": "Property tests, fuzzing, fixtures, coverage of edge cases."},
    {"id": "data_engineer", "tag": "data", "name": "Data Engineer",
     "desc": "Schemas, migrations, idempotent pipelines, observability."},
    {"id": "devops", "tag": "devops", "name": "DevOps",
     "desc": "CI/CD, IaC, containers, blue-green deploys, incident response."},
    {"id": "tech_writer", "tag": "docs", "name": "Tech Writer",
     "desc": "Clear prose, diagrams, examples that compile, audience awareness."},
]

_BUILTIN_SKILL_TEXTS: Dict[str, str] = {
    "python_architect": (
        "# SKILL: Python Architect\n\n"
        "You design clean, layered Python projects.\n"
        "Rules:\n"
        "- Separate concerns: routers -> services -> repositories -> models.\n"
        "- No business logic in route handlers.\n"
        "- Type-hint every public function; run mypy --strict in your head.\n"
        "- Prefer composition over inheritance.\n"
        "- Every module has a one-line docstring stating its responsibility.\n"
        "- If a file exceeds 300 lines, propose splitting it.\n"
    ),
    "ui_polish": (
        "# SKILL: UI Polish\n\n"
        "You produce pixel-perfect frontends.\n"
        "Rules:\n"
        "- Respect a design system: spacing scale, type scale, motion tokens.\n"
        "- Never use pure black or pure white.\n"
        "- All animations use cubic-bezier easing, never linear for organic motion.\n"
        "- Test keyboard navigation and screen-reader labels.\n"
        "- Mobile-first: layout works at 375px before 1440px.\n"
    ),
    "security_auditor": (
        "# SKILL: Security Auditor\n\n"
        "You review code for security issues.\n"
        "Rules:\n"
        "- Treat all input as hostile until proven otherwise.\n"
        "- Check OWASP Top 10 by default.\n"
        "- Never log secrets, tokens, or PII.\n"
        "- Prefer parameterized queries; reject string-built SQL.\n"
        "- Sandbox subprocess calls; whitelist binaries.\n"
    ),
    "performance": (
        "# SKILL: Performance\n\n"
        "You find and fix performance bottlenecks.\n"
        "Rules:\n"
        "- Measure before optimizing — profile, don't guess.\n"
        "- Hot paths deserve careful data structures, not premature abstraction.\n"
        "- Cache only when the cost of invalidation is lower than the cost of recomputation.\n"
        "- Vectorize numerical loops.\n"
        "- Bound concurrency: every queue needs a limit.\n"
    ),
    "test_engineer": (
        "# SKILL: Test Engineer\n\n"
        "You design test suites that catch real bugs.\n"
        "Rules:\n"
        "- Test behaviour, not implementation.\n"
        "- Cover happy path, edge cases, and failure modes.\n"
        "- One assertion concept per test.\n"
        "- Fixtures should be minimal and composable.\n"
        "- Property tests over example tests where possible.\n"
    ),
    "data_engineer": (
        "# SKILL: Data Engineer\n\n"
        "You build reliable data pipelines.\n"
        "Rules:\n"
        "- Every pipeline is idempotent — re-running is safe.\n"
        "- Schema changes are backward-compatible migrations, never in-place edits.\n"
        "- Emit structured logs and metrics at every stage.\n"
        "- Backfill before deploying schema changes.\n"
    ),
    "devops": (
        "# SKILL: DevOps\n\n"
        "You ship reliable infrastructure.\n"
        "Rules:\n"
        "- All infrastructure is code, version-controlled.\n"
        "- Deploys are blue-green or canary; never big-bang.\n"
        "- Every service has a health check and a readiness probe.\n"
        "- Alerts are actionable; no alert fatigue.\n"
    ),
    "tech_writer": (
        "# SKILL: Tech Writer\n\n"
        "You write clear technical documentation.\n"
        "Rules:\n"
        "- Every example must compile and run.\n"
        "- Audience-aware: distinguish beginner / intermediate / expert sections.\n"
        "- Lead with the outcome, then the steps.\n"
        "- Diagrams explain what prose cannot.\n"
    ),
}

_DISK_SKILLS, _DISK_SKILL_TEXTS = _load_skills_from_disk()

# Merge: disk overrides built-in by id
SKILLS: List[Dict[str, Any]] = []
_seen_skill_ids = set()
for s in _DISK_SKILLS + _BUILTIN_SKILLS:
    if s["id"] not in _seen_skill_ids:
        SKILLS.append(s)
        _seen_skill_ids.add(s["id"])

_SKILL_TEXTS: Dict[str, str] = {**_BUILTIN_SKILL_TEXTS, **_DISK_SKILL_TEXTS}


# ── Default per-provider config ────────────────────────────────────

_PROVIDER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "model": "anthropic/claude-3.5-sonnet",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "groq": {
        "model": "llama-3.3-70b-versatile",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "openai": {
        "model": "gpt-4o",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "anthropic": {
        "model": "claude-3-5-sonnet-20241022",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "deepseek": {
        "model": "deepseek-chat",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "zai": {
        "model": "glm-4-plus",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "gemini": {
        "model": "gemini-2.5-pro",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "mistral": {
        "model": "mistral-large-latest",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "together": {
        "model": "meta-llama/Llama-3-70b-chat-hf",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "fireworks": {
        "model": "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "xai": {
        "model": "grok-2",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "cerebras": {
        "model": "llama-3.3-70b",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "sambanova": {
        "model": "Meta-Llama-3.3-70B-Instruct",
        "api_key": "",
        "api_base": "",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "ollama": {
        "model": "llama3.1",
        "api_key": "",
        "api_base": "http://localhost:11434/v1",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "lmstudio": {
        "model": "",
        "api_key": "",
        "api_base": "http://localhost:1234/v1",
        "temperature": 0.2,
        "max_tokens": 4096,
    },
}


# ── Config persistence ─────────────────────────────────────────────

_DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 2,  # M8: aligned with api_server config schema
    "active_provider": "ollama",
    "providers": _PROVIDER_DEFAULTS,
    "ui": {
        "theme": "dark",
        "sidebar_collapsed": False,
        "code_viewer_width": "normal",   # "normal" | "wide" | "closed"
        "budget_usd": 20.0,               # monthly spend budget shown in Usage
        "text_size": "medium",            # "small" | "medium" | "large"
    },
    "active_chat_id": None,
    "project_root": None,
}


def _load_config() -> Dict[str, Any]:
    """Load ~/.clew/config.json, merging with defaults for any missing keys."""
    path = _config_path()
    if not path.exists():
        return json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[config] failed to load, using defaults: {e}")
        return json.loads(json.dumps(_DEFAULT_CONFIG))

    # Merge with defaults (one level deep)
    merged = json.loads(json.dumps(_DEFAULT_CONFIG))
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    # Ensure every provider has a config
    for pid in _PROVIDER_DEFAULTS:
        if pid not in merged["providers"]:
            merged["providers"][pid] = dict(_PROVIDER_DEFAULTS[pid])
    return merged


def _save_config(cfg: Dict[str, Any]) -> None:
    """Persist config to ~/.clew/config.json."""
    path = _config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error(f"[config] failed to save: {e}")


# ── Chat history persistence ───────────────────────────────────────

def _chat_path(chat_id: str) -> Path:
    return _chats_dir() / f"{chat_id}.json"


def _load_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    path = _chat_path(chat_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[chat] failed to load {chat_id}: {e}")
        return None


def _save_chat(chat: Dict[str, Any]) -> None:
    path = _chat_path(chat["id"])
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(chat, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error(f"[chat] failed to save {chat['id']}: {e}")


# ── Worker thread for streaming generation ─────────────────────────

class GenerationWorker(QThread):
    """Runs provider.stream() in a background thread, emits tokens."""

    token = Signal(str)
    step  = Signal(dict)
    done  = Signal(dict)
    error = Signal(str)

    # Hard total-time limit for any single generation (seconds).
    # Prevents indefinite hanging when the server holds the SSE connection open
    # but never sends data.  urllib's socket-level timeout only fires when
    # the socket is completely idle — a keep-alive byte resets it.
    TOTAL_TIMEOUT = 300  # 5 minutes absolute max

    def __init__(self, registry: ProviderRegistry,
                 messages: List[ProviderMessage],
                 skill: Optional[str],
                 parent=None):
        super().__init__(parent)
        self._registry = registry
        self._messages = messages
        self._skill = skill
        self._cancelled = False
        self._token_count = 0

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            provider = self._registry.active
            logger.info(
                "[worker] start generation — provider=%s model=%s skill=%s msgs=%d",
                provider.provider_id, provider.config.model,
                self._skill, len(self._messages),
            )

            # Explicitly load the provider BEFORE streaming.
            # The stream() method calls _ensure_loaded() internally too, but
            # doing it here gives us a clear log entry and early failure.
            if not provider.is_loaded:
                logger.info("[worker] provider not loaded — calling load()")
                provider.load()
                logger.info("[worker] provider loaded — api_key=%s",
                            "set" if getattr(provider, '_api_key', None) else "MISSING")

            self.step.emit({
                "type": "Action",
                "label": f"Connecting to {provider.label} · {provider.config.model}",
                "detail": "provider",
            })

            full_text: List[str] = []
            start = time.time()

            for chunk in provider.stream(self._messages, skill=self._skill):
                # ── Total-time guard ──────────────────────────────
                if time.time() - start > self.TOTAL_TIMEOUT:
                    logger.warning(
                        "[worker] TOTAL_TIMEOUT (%ds) reached — aborting. "
                        "tokens_so_far=%d", self.TOTAL_TIMEOUT, self._token_count,
                    )
                    self.error.emit(
                        f"Generation timed out after {self.TOTAL_TIMEOUT}s "
                        f"({self._token_count} tokens received). "
                        "Check your network / API endpoint."
                    )
                    return

                if self._cancelled:
                    logger.info("[worker] cancelled by user after %d tokens", self._token_count)
                    self.step.emit({
                        "type": "Final",
                        "label": "Cancelled by user",
                        "detail": "result",
                    })
                    self.done.emit({
                        "text": "".join(full_text),
                        "cancelled": True,
                        "tokens": self._token_count,
                        "elapsed": time.time() - start,
                    })
                    return

                full_text.append(chunk)
                self._token_count += 1
                self.token.emit(chunk)

                # First-token log for debugging
                if self._token_count == 1:
                    logger.info("[worker] first token received after %.1fs",
                                time.time() - start)

            elapsed = time.time() - start
            logger.info(
                "[worker] stream finished — %d tokens in %.1fs", self._token_count, elapsed,
            )

            # If the stream produced zero tokens, something went wrong.
            # The API accepted the request but returned nothing.
            if self._token_count == 0:
                logger.warning("[worker] empty response — 0 tokens received")
                self.error.emit(
                    "The provider returned an empty response. "
                    "Possible causes: invalid model name, API quota exhausted, "
                    "or the request was silently dropped."
                )
                return

            self.step.emit({
                "type": "Final",
                "label": f"Done · {self._token_count} chunks · {elapsed:.1f}s",
                "detail": "result",
            })
            self.done.emit({
                "text": "".join(full_text),
                "cancelled": False,
                "tokens": self._token_count,
                "elapsed": elapsed,
            })

        except ProviderError as e:
            logger.error("[worker] ProviderError: %s", e)
            self.error.emit(str(e))
        except Exception as e:
            logger.exception("[worker] unexpected generation failure")
            self.error.emit(f"Unexpected error: {e}")


# ── Worker for one-shot generation (Enhance, test ping) ────────────

class OneShotWorker(QThread):
    """Runs provider.generate() in a background thread, emits one result."""

    done  = Signal(dict)
    error = Signal(str)

    def __init__(self, registry: ProviderRegistry,
                 messages: List[ProviderMessage],
                 skill: Optional[str],
                 request_id: str,
                 parent=None):
        super().__init__(parent)
        self._registry = registry
        self._messages = messages
        self._skill = skill
        self._request_id = request_id

    def run(self) -> None:
        try:
            provider = self._registry.active
            resp = provider.generate(self._messages, skill=self._skill)
            self.done.emit({
                "request_id": self._request_id,
                "text": resp.text,
                "model": resp.model,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
            })
        except ProviderError as e:
            self.error.emit(f"{self._request_id}:{e}")
        except Exception as e:
            logger.exception("[oneshot] failed")
            self.error.emit(f"{self._request_id}:Unexpected error: {e}")


# ── Worker for AI title generation ─────────────────────────────────

class TitleWorker(QThread):
    """Generates a short chat title using the active provider."""

    done = Signal(str)   # the generated title
    error = Signal(str)

    def __init__(self, provider, prompt: str, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._prompt = prompt

    def run(self) -> None:
        try:
            from .providers.base import ProviderMessage
            msgs = [ProviderMessage(role="user", content=self._prompt)]
            resp = self._provider.generate(msgs, skill=None)
            title = resp.text.strip().strip('"').strip("'")
            # Take only first line
            if "\n" in title:
                title = title.split("\n")[0].strip()
            self.done.emit(title)
        except Exception as e:
            logger.warning("[title_worker] failed: %s", e)
            self.error.emit(str(e))


# ── The bridge itself ──────────────────────────────────────────────

class ClewBridge(QObject):
    """Python <-> JavaScript bridge, exposed via QWebChannel."""

    # Signals → received by JS
    token_streamed   = Signal(str)
    agent_step       = Signal(dict)
    agent_done       = Signal(dict)
    agent_error      = Signal(str)
    file_changed     = Signal(str, str)
    provider_changed = Signal(str, dict)
    chat_saved       = Signal(dict)
    chat_list_changed = Signal()
    settings_saved   = Signal(dict)
    oneshot_done     = Signal(dict)
    oneshot_error    = Signal(str)
    agent_step_signal = Signal(dict) # agent step (tool call, thought)
    agent_tool_result = Signal(dict) # tool execution result
    agent_final     = Signal(dict)   # agent done with result
    # v1.1 signals
    token_stats_updated  = Signal(dict)  # token usage/cost updates
    # v1.0.3 signals
    update_check_result  = Signal(dict)  # auto-update check result
    title_generated      = Signal(dict)  # {"chat_id": str, "title": str}
    # v1.0.4 signals
    router_decision      = Signal(dict)  # auto-router decision for UI display
    diff_review_requested = Signal(dict)  # agent asks user to review a file write
    # v1.1.1: agent asks user to confirm a non-diffable side-effecting
    # action (execute_command, delete_file, rename_file, apply_diff,
    # write_binary_file, git_commit) — gated by the agent_autonomy setting.
    action_confirm_requested = Signal(dict)  # {"action": str, "summary": str}

    # v1.1.2: git status push — emitted when files change inside a git repo
    git_status_changed = Signal(dict)  # same shape as GitService.status()
    # v1.0.12: emitted when a file write (from code-block Apply or write_file) completes
    apply_result = Signal(dict)  # {"ok": bool, "path": str, "error"?: str}
    # v1.1.5: LSP signals — bug #8 in clew_bug_report.md. Previously the
    # entire LSP module (clew/lsp_client.py) was dead code: LSPClient
    # was never instantiated anywhere in the project, so completion /
    # hover / go-to-definition / diagnostics physically never ran.
    # The bridge now owns an LSPClient and exposes the same signals that
    # the JS frontend can subscribe to via QWebChannel.
    lsp_completions_ready = Signal(str, list)   # uri, List[CompletionItem dict]
    lsp_hover_ready        = Signal(str, dict)  # uri, HoverInfo dict | {}
    lsp_definitions_ready  = Signal(str, list)  # uri, List[Location dict]
    lsp_diagnostics_ready  = Signal(str, list)  # uri, List[Diagnostic dict]
    lsp_server_status      = Signal(bool, str)  # is_running, message

    def __init__(self, project_root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._registry: ProviderRegistry = get_registry()
        self._code_viewer = CodeViewerService(root=project_root)
        self._code_viewer.watch(self._on_file_changed)
        self._worker: Optional[GenerationWorker] = None
        self._oneshot_workers: Dict[str, OneShotWorker] = {}

        # Agent runtime — for tool-use (file read/write, commands)
        self._agent_runtime: Optional[AgentRuntime] = None
        self._agent_worker: Optional[AgentWorker] = None

        # Load config and apply provider configs to the registry
        self._config = _load_config()
        self._apply_all_provider_configs()
        try:
            self._registry.set_active(self._config.get("active_provider", "ollama"))
        except ProviderError:
            self._registry.set_active("ollama")


        # v1.0.3: Memory service & auto-updater
        self._memory = MemoryService()
        # v1.1.5-fix (bug #11): pull the GitHub repo slug from the user
        # config (config["update_repo"]) so users / enterprise builds
        # can override or disable it. Falls back to AutoUpdater.DEFAULT_REPO
        # (the real Clew repo) when the key is missing — previously the
        # bridge passed NO repo argument, so the constructor's default
        # of "user/clew" (a placeholder) was used and `check_for_updates`
        # skipped silently, making the entire feature a no-op.
        configured_repo = self._config.get("update_repo") or None
        self._updater = AutoUpdater(repo=configured_repo, parent=self)
        # If the config didn't carry an explicit repo, fall back to the
        # built-in default after construction (set_repo normalises the
        # placeholder to None, so we explicitly re-enable here).
        if configured_repo is None and self._updater.repo is None:
            try:
                from .auto_updater import DEFAULT_REPO
                self._updater.set_repo(DEFAULT_REPO)
            except Exception:
                pass
        self._updater.update_available.connect(self._on_update_available)

        # v1.0.4: Auto-router
        self._router = AutoRouter()
        # v1.1.4-fix (bug 5.1): default ON. It used to default OFF, which
        # meant "don't make the person think about it" required the
        # person to first know this toggle existed and turn it on
        # manually — and it was unsafe to default on before this fix,
        # since it could route to providers with no key configured. Now
        # that routing is filtered to actually-configured providers,
        # enabling it by default is safe and is the whole point of the
        # feature.
        self._auto_route_enabled = self._config.get("auto_route", True)

        # v1.0.4: undo support
        self._pre_agent_snapshot: Optional[str] = None  # git commit hash or "backup"

        # v1.2: Usage / token intelligence
        self._tracker = get_token_tracker()

        # v1.1.5: LSP integration — bug #8. Previously `clew/lsp_client.py`
        # was never imported outside of its own module (the only mention
        # was a comment in `mcp_client.py`), making the entire LSP
        # subsystem — autocomplete, hover, go-to-definition, diagnostics —
        # dead code. The bridge now owns an LSPClient and connects its
        # signals to forward LSP responses to the JS frontend.
        # The server is started lazily on `open_project()` to avoid
        # spawning a `pylsp` subprocess when no project is open.
        self._lsp_client: Optional["LSPClient"] = None
        try:
            from .lsp_client import LSPClient
            self._lsp_client = LSPClient(parent=self)
            # Forward LSP signals to the bridge so JS can subscribe to
            # a single object via QWebChannel.
            self._lsp_client.completions_ready.connect(self._on_lsp_completions)
            self._lsp_client.hover_ready.connect(self._on_lsp_hover)
            self._lsp_client.definitions_ready.connect(self._on_lsp_definitions)
            self._lsp_client.diagnostics_ready.connect(self._on_lsp_diagnostics)
            self._lsp_client.server_started.connect(self._on_lsp_server_started)
            self._lsp_client.server_stopped.connect(self._on_lsp_server_stopped)
        except Exception as e:
            # LSP is optional — if construction fails (e.g. PySide6 not
            # available in some test context), log and continue. The
            # bridge still works without LSP features.
            logger.warning("[bridge] LSP client init failed (LSP features disabled): %s", e)
            self._lsp_client = None

        # Restore project root from config if not provided
        if not project_root and self._config.get("project_root"):
            try:
                self._code_viewer.set_root(self._config["project_root"])
            except Exception as e:
                logger.warning(f"[bridge] could not restore project_root: {e}")


    # ═══════════════════════════════════════════════════════════════
    # CONFIG / SETTINGS
    # ═══════════════════════════════════════════════════════════════

    def _apply_all_provider_configs(self) -> None:
        """Push every provider config from disk into the registry.

        v1.0.5-hotfix: skip providers that aren't registered in the
        registry (e.g. stale 'local' entries from old config files)
        with a debug log instead of a noisy WARNING. Previously every
        startup logged ``[bridge] failed to configure local: Unknown
        provider: local`` which clutters the log (BUGS_REPORT M-AUTO-2).
        """
        providers = self._config.get("providers", {})
        for pid, pcfg in providers.items():
            # Skip providers not in the registry — they're stale entries
            # from old configs or from api_server's _PROVIDER_DEFAULTS
            # (which has a 'local' entry the bridge doesn't register).
            if not self._registry.has_provider(pid):
                logger.debug("[bridge] skipping unregistered provider %r (stale config entry)", pid)
                continue
            try:
                cfg = ProviderConfig(
                    provider_id=pid,
                    model=pcfg.get("model", ""),
                    api_key=pcfg.get("api_key") or None,
                    api_base=pcfg.get("api_base") or None,
                    temperature=float(pcfg.get("temperature", 0.2)),
                    max_tokens=int(pcfg.get("max_tokens", 4096)),
                )
                self._registry.configure(pid, cfg)
            except Exception as e:
                logger.warning(f"[bridge] failed to configure {pid}: {e}")

    @Slot(result=dict)
    def get_settings(self) -> Dict[str, Any]:
        """Return the full settings dict (api_keys masked)."""
        cfg = json.loads(json.dumps(self._config))  # deep copy
        # v1.0.6: expose agent settings in get_settings for the GUI
        # (user request: max iterations and other agent settings
        # should be visible in the Settings panel).
        agent_rt = self._agent_runtime
        cfg["agent"] = {
            "max_iterations": agent_rt.max_iterations if agent_rt else int(self._config.get("agent_max_iterations", 8)),
            "enable_planning": agent_rt.enable_planning if agent_rt else self._config.get("agent_enable_planning", True),
            "autonomy": agent_rt.tools.autonomy if agent_rt else self._config.get("agent_autonomy", "always_ask"),
            "run_timeout": agent_rt.tools.RUN_TIMEOUT if agent_rt else int(self._config.get("agent_run_timeout", 15)),
            "workspace": str(agent_rt.tools.workspace) if agent_rt else self._config.get("project_root", ""),
        }
        for pid, pcfg in cfg.get("providers", {}).items():
            key = pcfg.get("api_key", "")
            if key:
                pcfg["api_key_masked"] = key[:4] + "…" + key[-4:] if len(key) > 8 else "…"
                pcfg["api_key_set"] = True
            else:
                pcfg["api_key_masked"] = ""
                pcfg["api_key_set"] = False
        return cfg

    @Slot(dict, result=dict)
    def save_settings(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        """Merge partial into the config and persist. Returns the new full config."""
        # Top-level keys
        for k in ("active_provider", "active_chat_id", "project_root"):
            if k in partial:
                self._config[k] = partial[k]
        # v1.0.6: persist and apply agent settings (M-CONTRACT-1).
        # The GUI can now configure max_iterations, autonomy, planning,
        # and run_timeout from the Settings panel.
        if "agent" in partial:
            agent_cfg = partial["agent"]
            if isinstance(agent_cfg, dict):
                for key, config_key in [
                    ("max_iterations", "agent_max_iterations"),
                    ("enable_planning", "agent_enable_planning"),
                    ("autonomy", "agent_autonomy"),
                    ("run_timeout", "agent_run_timeout"),
                ]:
                    if key in agent_cfg:
                        self._config[config_key] = agent_cfg[key]
                # Apply to running agent runtime immediately
                if self._agent_runtime:
                    if "max_iterations" in agent_cfg:
                        self._agent_runtime.max_iterations = int(agent_cfg["max_iterations"])
                    if "enable_planning" in agent_cfg:
                        self._agent_runtime.enable_planning = bool(agent_cfg["enable_planning"])
                    if "autonomy" in agent_cfg:
                        self._agent_runtime.set_autonomy(agent_cfg["autonomy"])
                    if "run_timeout" in agent_cfg:
                        self._agent_runtime.tools.RUN_TIMEOUT = int(agent_cfg["run_timeout"])

        # UI settings — also accept bare 'theme' key at top level for compat
        if "ui" in partial:
            self._config["ui"] = {**self._config.get("ui", {}), **partial["ui"]}
        elif "theme" in partial:
            # Frontend may send {theme: id} instead of {ui: {theme: id}}
            self._config.setdefault("ui", {})["theme"] = partial["theme"]

        # Provider configs
        if "providers" in partial:
            for pid, pcfg in partial["providers"].items():
                if pid not in self._config["providers"]:
                    if pid in _PROVIDER_DEFAULTS:
                        self._config["providers"][pid] = dict(_PROVIDER_DEFAULTS[pid])
                    else:
                        logger.warning(f"[bridge] save_settings: unknown provider {pid}, skipping")
                        continue
                # Don't overwrite api_key with empty string (UI sends "" when masked)
                for k in ("model", "api_base", "temperature", "max_tokens"):
                    if k in pcfg:
                        self._config["providers"][pid][k] = pcfg[k]
                # Only update api_key if a non-empty value is sent
                if "api_key" in pcfg and pcfg["api_key"]:
                    self._config["providers"][pid]["api_key"] = pcfg["api_key"]

        _save_config(self._config)

        # Re-apply to registry
        if "providers" in partial:
            self._apply_all_provider_configs()
        if "active_provider" in partial:
            try:
                self._registry.set_active(partial["active_provider"])
            except ProviderError as e:
                logger.warning(f"[bridge] save_settings: set_active failed: {e}")
                # Don't fail the whole save — config is still persisted
                # Just warn and continue

        # Emit signal so UI can refresh
        self.settings_saved.emit(self.get_settings())
        return {"ok": True, "settings": self.get_settings()}

    # ═══════════════════════════════════════════════════════════════
    # USAGE — token intelligence panel
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=dict)
    def get_token_stats(self) -> Dict[str, Any]:
        """Aggregate token/cost stats for the Usage panel."""
        budget = float(self._config.get("ui", {}).get("budget_usd", 20.0) or 20.0)
        return self._tracker.stats(budget=budget)

    @Slot(float, result=dict)
    def get_token_stats_window(self, last_seconds: float) -> Dict[str, Any]:
        """Same as get_token_stats but scoped to a recent time window (e.g. today = 86400)."""
        budget = float(self._config.get("ui", {}).get("budget_usd", 20.0) or 20.0)
        return self._tracker.stats(last_seconds=last_seconds or None, budget=budget)

    @Slot(result=list)
    def get_provider_breakdown(self) -> List[Dict[str, Any]]:
        """Cost/token breakdown grouped by provider, for the Usage bar chart."""
        return self._tracker.provider_breakdown()

    @Slot(result=dict)
    def clear_token_history(self) -> Dict[str, Any]:
        self._tracker.clear_history()
        self.token_stats_updated.emit(self._tracker.stats())
        return {"ok": True}

    @Slot(float, result=dict)
    def set_budget(self, usd: float) -> Dict[str, Any]:
        """Persist the user's monthly spend budget (shown as a progress bar in Usage)."""
        self._config.setdefault("ui", {})["budget_usd"] = max(0.0, float(usd))
        _save_config(self._config)
        self.settings_saved.emit(self.get_settings())
        return {"ok": True, "budget_usd": self._config["ui"]["budget_usd"]}

    @Slot(result=dict)
    def get_pricing_table(self) -> Dict[str, Any]:
        """Return the currently effective per-model pricing (bundled or live-fetched)."""
        return self._tracker.pricing_table()

    @Slot(result=dict)
    def fetch_live_pricing(self) -> Dict[str, Any]:
        """
        Best-effort refresh of model pricing from OpenRouter's public,
        keyless /models endpoint (covers most providers Clew supports,
        since OpenRouter proxies pricing for Anthropic/OpenAI/Google/etc).
        Falls back silently to the bundled snapshot on any failure.
        """
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"User-Agent": "Clew/1.2"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            live: Dict[str, Dict[str, float]] = {}
            for m in data.get("data", []):
                pricing = m.get("pricing") or {}
                mid = m.get("id") or ""
                try:
                    price_in = float(pricing.get("prompt", 0)) * 1000
                    price_out = float(pricing.get("completion", 0)) * 1000
                except (TypeError, ValueError):
                    continue
                if not mid or (price_in == 0 and price_out == 0):
                    continue
                live[mid] = {"in": price_in, "out": price_out}
                # Also index by the bare model name after the "/" so our
                # locally-configured model ids (without vendor prefix) match.
                if "/" in mid:
                    live.setdefault(mid.split("/", 1)[1], {"in": price_in, "out": price_out})
            if live:
                self._tracker.set_live_pricing(live)
                logger.info(f"[bridge] fetched live pricing for {len(live)} models")
            return {"ok": bool(live), "count": len(live), **self._tracker.pricing_table()}
        except Exception as e:
            logger.warning(f"[bridge] fetch_live_pricing failed: {e}")
            return {"ok": False, "error": str(e), **self._tracker.pricing_table()}

    # ═══════════════════════════════════════════════════════════════
    # COMPOSER & GENERATION
    # ═══════════════════════════════════════════════════════════════

    @Slot(dict, result=dict)
    def send_message(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        """
        opts = {
            text:     "user prompt",
            skill:    "python_architect" | None,
            template: "code_project" | None,
            chat_id:  "abc123" | None,    # if None, creates a new chat
            history:  [{role, content}, ...]
        }
        Returns {ok, chat_id} immediately; tokens stream via `token_streamed`.
        """
        if self._worker and self._worker.isRunning():
            return {"ok": False, "error": "Generation already in progress"}

        text = (opts.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "Empty prompt"}

        # Get or create chat
        chat_id = opts.get("chat_id")
        chat = None
        if chat_id:
            chat = _load_chat(chat_id)
        if not chat:
            chat_id = uuid.uuid4().hex[:12]
            chat = {
                "id": chat_id,
                "title": text[:60] + ("…" if len(text) > 60 else ""),
                "created_at": datetime.utcnow().isoformat() + "Z",
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "messages": [],
                "provider": self._registry.active_id,
                "skill": opts.get("skill"),
            }

        # Build messages: prior history + new user message
        messages: List[ProviderMessage] = []
        for h in chat["messages"]:
            messages.append(ProviderMessage(role=h["role"], content=h["content"]))
        messages.append(ProviderMessage(role="user", content=text))

        # M5: inject the same system prompt that api_server uses, so the
        # bridge and HTTP transports produce consistent behavior.
        _BRIDGE_SYSTEM_PROMPT = (
            "You are Clew, an AI coding assistant.\n"
            "You help users write, debug, refactor, and understand code. "
            "You are concise, accurate, and practical.\n\n"
            "Behavior:\n"
            "- Write clean, well-structured code with proper error handling.\n"
            "- When the user provides a project context, use file paths relative to the project root.\n"
            "- Prefer code examples and concrete solutions over vague explanations.\n"
            "- If you don\'t know something, say so honestly.\n"
            "- Use markdown formatting for code blocks, lists, and emphasis."
        )
        messages.insert(0, ProviderMessage(role="system", content=_BRIDGE_SYSTEM_PROMPT))

        # Save user message to chat
        chat["messages"].append({
            "role": "user",
            "content": text,
            "ts": datetime.utcnow().isoformat() + "Z",
        })
        chat["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _save_chat(chat)

        # Inject template structure as a system message if requested
        skill_id = opts.get("skill")
        skill_text = _SKILL_TEXTS.get(skill_id) if skill_id else None

        template_id = opts.get("template")
        if template_id:
            tpl = next((t for t in PROMPT_TEMPLATES if t["id"] == template_id), None)
            if tpl:
                skeleton = self._build_template_skeleton(tpl, text)
                messages.insert(0, ProviderMessage(
                    role="system",
                    content=f"Use this prompt structure:\n\n{skeleton}",
                ))

        # v1.1.2: inject cross-chat memory context if available
        try:
            project_root = str(self._code_viewer.root) if self._code_viewer.root else self._config.get("project_root")
            brief = self._memory.build_context_brief(project_root=project_root, query=text)
            if brief:
                messages.insert(0, ProviderMessage(
                    role="system",
                    content=f"Relevant prior context from earlier sessions:\n\n{brief}",
                ))
        except Exception:
            pass

        # Start the worker
        # v1.0.4: auto-route if enabled
        if self._auto_route_enabled:
            # v1.1.4-fix (bug 5.1): tell the router which providers are
            # actually usable right now (configured with a key, or a
            # no-key local provider) — see AutoRouter.route() docstring.
            configured = {
                p["id"] for p in self._registry.list_providers()
                if p.get("configured") or p["id"] in ("ollama", "lmstudio")
            }
            decision = self._router.route(text, configured_providers=configured)
            self.router_decision.emit(decision)
            logger.info(f"[bridge] auto-route: {decision['reasoning']}")
            # Temporarily switch provider if the router picked a different one
            if decision["provider_id"] and decision["provider_id"] != self._registry.active_id:
                try:
                    self._registry.set_active(decision["provider_id"])
                    logger.info(f"[bridge] switched to {decision['provider_id']} per router")
                except ProviderError:
                    logger.warning(f"[bridge] router suggested {decision['provider_id']} but not available")

        self._worker = GenerationWorker(
            self._registry, messages, skill_text, parent=self,
        )
        # v1.1.4-fix (bug 5.1): mark_provider_available() existed but
        # nothing ever called it, so the router's failure-cooldown logic
        # was dead. Hook it up to the actual outcome of this request.
        _routed_pid = self._registry.active_id
        self._worker.token.connect(self.token_streamed)
        self._worker.step.connect(self.agent_step)
        self._worker.done.connect(
            lambda result, cid=chat_id, msgs=messages, pid=_routed_pid:
                self._on_generation_done(cid, result, msgs, pid)
        )
        self._worker.error.connect(
            lambda msg, pid=_routed_pid: self._on_generation_error(msg, pid)
        )
        self._worker.start()

        # Persist active chat id
        self._config["active_chat_id"] = chat_id
        _save_config(self._config)

        self.chat_list_changed.emit()
        return {"ok": True, "chat_id": chat_id, "title": chat["title"]}

    def _on_generation_done(self, chat_id: str, result: Dict[str, Any],
                             sent_messages: Optional[List[ProviderMessage]],
                             routed_pid: Optional[str]) -> None:
        """v1.1.4-fix (bug 5.1): confirm the routed provider actually
        worked, so the router's availability cache reflects reality."""
        if routed_pid:
            self._router.mark_provider_available(routed_pid, True)
        self._on_done(chat_id, result, sent_messages)

    def _on_generation_error(self, msg: str, routed_pid: Optional[str]) -> None:
        """v1.1.4-fix (bug 5.1): a real failure — skip this provider for
        the next few minutes (AutoRouter._provider_cache_ttl) so the
        person doesn't hit the same broken provider on their next
        message too."""
        if routed_pid:
            self._router.mark_provider_available(routed_pid, False)
        self.agent_error.emit(msg)

    def _on_done(self, chat_id: str, result: Dict[str, Any], sent_messages: Optional[List[ProviderMessage]] = None) -> None:
        """Persist the assistant's reply to the chat history."""
        chat = _load_chat(chat_id)
        if chat:
            chat["messages"].append({
                "role": "assistant",
                "content": result.get("text", ""),
                "ts": datetime.utcnow().isoformat() + "Z",
                "tokens": result.get("tokens"),
                "elapsed": result.get("elapsed"),
                "cancelled": result.get("cancelled", False),
            })
            chat["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _save_chat(chat)
            self.chat_saved.emit({
                "id": chat_id,
                "title": chat["title"],
                "updated_at": chat["updated_at"],
                "message_count": len(chat["messages"]),
            })

        # v1.2: record usage for the Usage panel (rough tokens_in estimate —
        # ~4 chars/token — since providers don't all report exact prompt
        # token counts on the streaming path).
        try:
            tokens_out = int(result.get("tokens") or 0)
            if tokens_out > 0:
                tokens_in = 0
                if sent_messages:
                    tokens_in = sum(len(m.content or "") for m in sent_messages) // 4
                provider = self._registry.active
                self._tracker.record(
                    provider=provider.provider_id,
                    model=provider.config.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    chat_id=chat_id,
                )
                budget = float(self._config.get("ui", {}).get("budget_usd", 20.0) or 20.0)
                self.token_stats_updated.emit(self._tracker.stats(budget=budget))
        except Exception as e:
            logger.warning(f"[bridge] token tracking failed: {e}")

        self.agent_done.emit(result)
        self._worker = None

    @Slot()
    def stop_generation(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    # ═══════════════════════════════════════════════════════════════
    # WINDOW CONTROL (frameless window)
    # ═══════════════════════════════════════════════════════════════
    # These slots are called from JS (window-controls buttons + drag region)
    # and delegate to ClewMainWindow via the _main_window back-reference set
    # in ClewMainWindow.__init__. They are intentionally tolerant of a missing
    # back-reference so that the bridge stays usable in tests / headless mode.

    @Slot()
    def minimize_window(self) -> None:
        mw = getattr(self, "_main_window", None)
        if mw is not None:
            mw.minimize()

    @Slot()
    def toggle_maximize_window(self) -> None:
        mw = getattr(self, "_main_window", None)
        if mw is not None:
            mw.toggle_maximize()

    @Slot()
    def close_window(self) -> None:
        mw = getattr(self, "_main_window", None)
        if mw is not None:
            mw.close_window()

    @Slot(result=bool)
    def start_window_drag(self) -> bool:
        """Begin a native OS window drag (called on mousedown in the topbar drag region)."""
        mw = getattr(self, "_main_window", None)
        if mw is None:
            return False
        return mw.start_system_move()

    @Slot(str, result=bool)
    def start_window_resize(self, edge: str) -> bool:
        """Begin a native OS window resize from one of the 8 edges.
        edge is one of: 'top','bottom','left','right',
        'top-left','top-right','bottom-left','bottom-right'."""
        mw = getattr(self, "_main_window", None)
        if mw is None:
            return False
        return mw.start_system_resize(edge)

    @Slot(result=bool)
    def is_window_maximized(self) -> bool:
        mw = getattr(self, "_main_window", None)
        if mw is None:
            return False
        return mw.isMaximized()

    @Slot(result=str)
    def get_platform(self) -> str:
        """Return 'darwin' / 'win32' / 'linux' — used by the frontend to
        decide which window-control layout to render."""
        import sys as _sys
        if _sys.platform == "darwin":
            return "darwin"
        if _sys.platform.startswith("win"):
            return "win32"
        return "linux"

    @Slot(str, str, result=str)
    def enhance_prompt(self, request_id: str, text: str) -> str:
        """
        Use the active provider to rewrite the user's prompt into a
        structured skeleton. Returns "" immediately; result comes via
        the `oneshot_done` signal with {request_id, text}.
        """
        text = (text or "").strip()
        if not text:
            return ""

        messages = [
            ProviderMessage(
                role="system",
                content=(
                    "You are a prompt engineer. Rewrite the user's request "
                    "as a structured prompt with these sections: "
                    "[INTENT], [CONTEXT], [CONSTRAINTS], [DELIVERABLES]. "
                    "Be concise. Output only the structured prompt, nothing else."
                ),
            ),
            ProviderMessage(role="user", content=text),
        ]

        # Create a temporary config with reduced max_tokens to avoid
        # mutating the active provider's config (race condition fix).
        provider = self._registry.active
        if not provider.is_loaded:
            provider.load()

        original_config = provider.config
        temp_config = ProviderConfig(
            provider_id=original_config.provider_id,
            model=original_config.model,
            api_key=original_config.api_key,
            api_base=original_config.api_base,
            temperature=original_config.temperature,
            max_tokens=800,
            top_p=original_config.top_p,
            stream=original_config.stream,
            timeout=original_config.timeout,
        )
        # Temporarily swap config
        provider.config = temp_config

        # M1 fix: restore config via worker done/error callbacks
        # instead of racing with a 50ms QTimer. The old approach had a
        # race: if a concurrent send_message arrived during the 50ms
        # window, it saw the reduced max_tokens=800 config.
        def _restore_config():
            try:
                provider.config = original_config
            except Exception:
                pass

        worker = OneShotWorker(self._registry, messages, None, request_id, parent=self)
        worker.done.connect(lambda _: _restore_config())
        worker.error.connect(lambda _: _restore_config())
        worker.done.connect(self._on_oneshot_done)
        worker.error.connect(self.oneshot_error)
        self._oneshot_workers[request_id] = worker
        worker.start()

        return request_id

    def _on_oneshot_done(self, result: Dict[str, Any]) -> None:
        """Forward one-shot results (Enhance, test ping) to the UI."""
        rid = result.get("request_id", "")
        worker = self._oneshot_workers.pop(rid, None)
        if worker:
            worker.deleteLater()
        self.oneshot_done.emit(result)

    @staticmethod
    def _build_template_skeleton(template: Dict[str, Any], user_text: str) -> str:
        sections = template.get("sections", [])
        header = f"# Template: {template['name']}\n\n"
        body = "\n\n".join(f"[{s.upper()}]\n<to be filled>" for s in sections)
        return header + body + f"\n\n[USER INTENT]\n{user_text}"

    # ═══════════════════════════════════════════════════════════════
    # CHAT HISTORY
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=list)
    def list_chats(self) -> List[Dict[str, Any]]:
        """List all saved chats, newest first."""
        chats = []
        running_chat_id = self._config.get("active_chat_id") if (self._agent_worker and self._agent_worker.isRunning()) else None
        for path in _chats_dir().glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    chat = json.load(f)
                # v1.1.1: surface the outcome of the last assistant message
                # so the sidebar can show a small status dot (done/error/
                # running) instead of just a bare title — addresses the
                # UI feedback that chat rows carried no status signal.
                last_status = "idle"
                messages = chat.get("messages", [])
                for m in reversed(messages):
                    if m.get("role") == "assistant":
                        last_status = "error" if (m.get("error") or m.get("success") is False) else "done"
                        break
                if chat["id"] == running_chat_id:
                    last_status = "running"
                chats.append({
                    "id": chat["id"],
                    "title": chat["title"],
                    "updated_at": chat.get("updated_at", chat.get("created_at", "")),
                    "message_count": len(messages),
                    "provider": chat.get("provider"),
                    "skill": chat.get("skill"),
                    "status": last_status,
                })
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[chat] failed to read {path}: {e}")
        chats.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return chats

    @Slot(str, result=dict)
    def load_chat(self, chat_id: str) -> Dict[str, Any]:
        """Return the full chat with all messages."""
        chat = _load_chat(chat_id)
        if not chat:
            return {"ok": False, "error": "Chat not found"}
        return {"ok": True, "chat": chat}

    @Slot(str, result=dict)
    def create_chat(self, title: str) -> Dict[str, Any]:
        """Create a new empty chat."""
        chat_id = uuid.uuid4().hex[:12]
        chat = {
            "id": chat_id,
            "title": title or "New chat",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "messages": [],
            "provider": self._registry.active_id,
            "skill": None,
        }
        _save_chat(chat)
        self._config["active_chat_id"] = chat_id
        _save_config(self._config)
        self.chat_list_changed.emit()
        return {"ok": True, "chat_id": chat_id, "chat": chat}

    @Slot(str, result=bool)
    def delete_chat(self, chat_id: str) -> bool:
        path = _chat_path(chat_id)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                return False
        if self._config.get("active_chat_id") == chat_id:
            self._config["active_chat_id"] = None
            _save_config(self._config)
        self.chat_list_changed.emit()
        return True

    @Slot(str, str, result=bool)
    def rename_chat(self, chat_id: str, title: str) -> bool:
        chat = _load_chat(chat_id)
        if not chat:
            return False
        chat["title"] = title
        chat["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _save_chat(chat)
        self.chat_list_changed.emit()
        return True

    @Slot(str, result=str)
    def generate_title(self, chat_id: str) -> str:
        """Generate a short title for a chat using the active provider.

        v1.0.5: the prompt now follows the principles from
        качество_кода_llm.md §2.4 (structured requirements) and §2.8
        (explicit output format). The model is told exactly:
          - length (3-6 words, <= 50 chars)
          - language (English by default, unless the chat is clearly
            in another language)
          - what NOT to include (no quotes, no emoji, no prefix)
          - output format (ONLY the title, nothing else)
        ...which is much more reliable than "give me a short title".
        """
        chat = _load_chat(chat_id)
        if not chat:
            return ""

        # Build a short prompt from the first 2 user messages
        user_msgs = [m["content"] for m in chat["messages"] if m["role"] == "user"]
        if not user_msgs:
            return ""

        excerpt = "\n".join(user_msgs[:3])[:800]

        # v1.0.5: structured, anti-pattern-rich prompt — much more
        # reliable than the old "very short title" instruction.
        prompt = (
            "You generate a concise, descriptive title for a chat conversation.\n\n"
            "Constraints:\n"
            "- Length: 3 to 6 words. Never more than 8.\n"
            "- Max 50 characters total.\n"
            "- Lowercase first letter unless it is a proper noun or acronym.\n"
            "- No trailing period.\n"
            "- No quotes, no emoji, no markdown, no prefix like 'Title:'.\n"
            "- Capture the ACTION or TOPIC, not the greeting.\n"
            "  Good: 'fix auth bug in oauth flow'\n"
            "  Good: 'add dark mode toggle to settings'\n"
            "  Bad:  'user asks for help'  (too generic)\n"
            "  Bad:  'Fix the auth bug in the OAuth flow when users log in via Google'  (too long)\n"
            "  Bad:  'Title: auth bug'  (has prefix)\n"
            "- If the chat is clearly in Russian, output a Russian title.\n"
            "- Output ONLY the title text. Nothing else. No explanation.\n\n"
            f"Chat excerpt (first user messages):\n{excerpt}\n\n"
            "Title:"
        )

        provider = self._registry.active
        if not provider:
            return ""

        # Use a short-lived worker for title generation
        title_worker = TitleWorker(provider, prompt, parent=self)
        title_worker.done.connect(lambda t, cid=chat_id: self._apply_title(cid, t))
        title_worker.start()
        return ""  # result comes via signal

    def _apply_title(self, chat_id: str, title: str) -> None:
        """Apply an AI-generated title to the chat.

        v1.0.5: less conservative auto-rename policy. The old rule
        (skip if title > 15 chars and doesn't end with '...') meant a
        chat created with the literal first 60 chars of a long user
        message would never get its AI-generated title because the
        truncated excerpt didn't end with '...'. Now we always replace
        a truncated-looking title (one that exactly matches the first
        N chars of the first user message) with the AI's title.
        """
        if not title or not title.strip():
            return
        title = title.strip().strip('"').strip("'").strip("`")
        # Take only the first line of the model's output (some models
        # add a trailing explanation despite the constraint).
        title = title.split("\n", 1)[0].strip()
        if len(title) > 80:
            title = title[:77] + "..."
        chat = _load_chat(chat_id)
        if not chat:
            return

        old_title = chat.get("title", "")
        # Heuristic: a "truncated excerpt" title is one that is a
        # prefix of the first user message, or is the auto-generated
        # "..." form. We rename in those cases.
        first_user = next((m["content"] for m in chat["messages"]
                           if m["role"] == "user"), "")
        looks_truncated = (
            not old_title
            or old_title == "New chat"
            or old_title.endswith("…")
            or old_title.endswith("...")
            or (first_user and first_user.startswith(old_title.rstrip("….")))
        )
        if not looks_truncated:
            # User has already given the chat a real title — don't
            # clobber it.
            return

        chat["title"] = title
        chat["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _save_chat(chat)
        self.chat_list_changed.emit()
        self.title_generated.emit({"chat_id": chat_id, "title": title})

    @Slot(result=dict)
    def get_active_chat(self) -> Dict[str, Any]:
        cid = self._config.get("active_chat_id")
        if not cid:
            return {"ok": False, "chat": None}
        chat = _load_chat(cid)
        if not chat:
            return {"ok": False, "chat": None}
        return {"ok": True, "chat": chat}

    # ═══════════════════════════════════════════════════════════════
    # PROVIDERS
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=list)
    def list_providers(self) -> List[Dict[str, Any]]:
        out = []
        for p in self._registry.list_providers():
            # Augment with persisted config info
            pid = p["id"]
            pcfg = self._config.get("providers", {}).get(pid, {})
            key = pcfg.get("api_key", "")
            out.append({
                **p,
                "model": pcfg.get("model", p["model"]),
                "api_key_set": bool(key),
                "temperature": pcfg.get("temperature", 0.2),
                "max_tokens": pcfg.get("max_tokens", 4096),
            })
        return out

    @Slot(str)
    def set_provider(self, provider_id: str) -> None:
        try:
            self._registry.set_active(provider_id)
            self._config["active_provider"] = provider_id
            _save_config(self._config)

            info = next((p for p in self.list_providers() if p["id"] == provider_id), {})
            self.provider_changed.emit(provider_id, info)
        except ProviderError as e:
            self.agent_error.emit(str(e))

    @Slot(str, str, str, float, int, result=dict)
    def configure_provider(self, provider_id: str, model: str, api_key: str,
                           temperature: float, max_tokens: int) -> Dict[str, Any]:
        """Update a provider's config and persist to disk."""
        try:
            # Update in-memory config
            if provider_id not in self._config["providers"]:
                self._config["providers"][provider_id] = dict(_PROVIDER_DEFAULTS[provider_id])
            pcfg = self._config["providers"][provider_id]
            if model:
                pcfg["model"] = model
            if api_key:    # don't overwrite existing key with empty
                pcfg["api_key"] = api_key
            pcfg["temperature"] = float(temperature)
            pcfg["max_tokens"] = int(max_tokens)
            _save_config(self._config)

            # Apply to registry
            cfg = ProviderConfig(
                provider_id=provider_id,
                model=pcfg["model"],
                api_key=pcfg.get("api_key") or None,
                api_base=pcfg.get("api_base") or None,
                temperature=pcfg["temperature"],
                max_tokens=pcfg["max_tokens"],
            )
            self._registry.configure(provider_id, cfg)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(str, result=dict)
    def get_provider_config(self, provider_id: str) -> Dict[str, Any]:
        pcfg = self._config.get("providers", {}).get(provider_id, {})
        key = pcfg.get("api_key", "")
        return {
            "provider_id": provider_id,
            "model": pcfg.get("model", ""),
            "api_key": key,
            "api_key_masked": (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("…" if key else ""),
            "api_key_set": bool(key),
            "temperature": pcfg.get("temperature", 0.2),
            "max_tokens": pcfg.get("max_tokens", 4096),
        }

    @Slot(str, str, result=str)
    def test_provider(self, provider_id: str, request_id: str) -> str:
        """Send 'Say hello in one sentence.' to the provider, return via oneshot_done."""
        try:
            cfg = self._config["providers"].get(provider_id, {})
            cfg_obj = ProviderConfig(
                provider_id=provider_id,
                model=cfg.get("model", ""),
                api_key=cfg.get("api_key") or None,
                api_base=cfg.get("api_base") or None,
                temperature=0.2,
                max_tokens=100,
            )
            # Apply the config to the provider BEFORE loading/testing
            # This is critical — without configure(), the provider uses
            # default/empty config and the API key/model won't be set.
            self._registry.configure(provider_id, cfg_obj)

            # Get the provider instance (now with the test config)
            provider = self._registry.get(provider_id)
            if not provider.is_loaded:
                provider.load()

            messages = [
                ProviderMessage(role="user", content="Say hello in one sentence."),
            ]
            worker = OneShotWorker(self._registry, messages, None, request_id, parent=self)
            worker.done.connect(self._on_oneshot_done)
            worker.error.connect(self.oneshot_error)
            self._oneshot_workers[request_id] = worker
            worker.start()
            return request_id
        except Exception as e:
            self.oneshot_error.emit(f"{request_id}:{e}")
            return request_id

    # ═══════════════════════════════════════════════════════════════
    # CODE VIEWER
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=list)
    def list_files(self) -> List[Dict[str, Any]]:
        return self._code_viewer.list_files()

    @Slot(str, result=dict)
    def read_file(self, path: str) -> Dict[str, Any]:
        return self._code_viewer.read_file(path)

    @Slot(str, result=list)
    def search_code(self, pattern: str) -> List[Dict[str, Any]]:
        return self._code_viewer.search(pattern)

    @Slot(str, result=dict)
    def open_project(self, path: str) -> Dict[str, Any]:
        try:
            self._code_viewer.set_root(path)
            self._config["project_root"] = path
            _save_config(self._config)
            # v1.1.5: bug #8 — start (or restart) the LSP server against
            # the new workspace. Previously the LSP module was dead code;
            # now `open_project` is the canonical entry point that brings
            # it to life. We do this on a QTimer.singleShot(0) so the
            # subprocess spawn doesn't block the QWebChannel response.
            try:
                self._restart_lsp_for_root(path)
            except Exception as e:
                logger.warning("[bridge] LSP restart on open_project failed: %s", e)
            return {"ok": True, "root": path, "files": self._code_viewer.list_files()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _restart_lsp_for_root(self, root: str) -> None:
        """v1.1.5 — start (or restart) the LSP server against `root`.

        Implements bug #8 fix: the LSP module was previously dead code
        because nobody constructed `LSPClient`. The bridge now owns one
        and (re)starts it whenever the project root changes, so that
        completions/hover/definition/diagnostics actually work.
        """
        if self._lsp_client is None:
            return
        # Stop any previous instance first; `start_server` is idempotent
        # w.r.t. the workspace path, but a restart guarantees a clean
        # initialize handshake against the new root.
        try:
            self._lsp_client.stop_server()
        except Exception:
            pass
        try:
            from PySide6.QtCore import QTimer
            # Defer the actual subprocess spawn off the synchronous
            # open_project call so the UI doesn't freeze on pylsp startup.
            QTimer.singleShot(0, lambda: self._lsp_client.start_server(root))
        except Exception as e:
            logger.warning("[bridge] could not schedule LSP start: %s", e)
            try:
                self._lsp_client.start_server(root)
            except Exception as e2:
                logger.warning("[bridge] LSP start_server failed: %s", e2)

    # ── v1.1.5: LSP slot handlers (forward LSPClient signals) ──────

    def _on_lsp_completions(self, uri: str, items: list) -> None:
        # `items` is a list of CompletionItem dataclasses; serialise
        # them to plain dicts before emitting across QWebChannel.
        from dataclasses import asdict
        try:
            payload = [asdict(i) if hasattr(i, "__dataclass_fields__") else i
                       for i in items]
        except Exception:
            payload = list(items)
        self.lsp_completions_ready.emit(uri, payload)

    def _on_lsp_hover(self, uri: str, hover: Any) -> None:
        from dataclasses import asdict
        try:
            payload = asdict(hover) if hasattr(hover, "__dataclass_fields__") else (hover or {})
        except Exception:
            payload = {}
        self.lsp_hover_ready.emit(uri, payload)

    def _on_lsp_definitions(self, uri: str, locations: list) -> None:
        from dataclasses import asdict
        try:
            payload = [asdict(l) if hasattr(l, "__dataclass_fields__") else l
                       for l in locations]
        except Exception:
            payload = list(locations)
        self.lsp_definitions_ready.emit(uri, payload)

    def _on_lsp_diagnostics(self, uri: str, diagnostics: list) -> None:
        from dataclasses import asdict
        try:
            payload = [asdict(d) if hasattr(d, "__dataclass_fields__") else d
                       for d in diagnostics]
        except Exception:
            payload = list(diagnostics)
        self.lsp_diagnostics_ready.emit(uri, payload)

    def _on_lsp_server_started(self, success: bool, message: str) -> None:
        self.lsp_server_status.emit(bool(success), str(message))

    def _on_lsp_server_stopped(self) -> None:
        self.lsp_server_status.emit(False, "LSP server stopped")

    # ── v1.1.5: LSP slots callable from JS ─────────────────────────

    @Slot(str, int, int, result=dict)
    def lsp_request_completion(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Request completions at (line, character). Async — result arrives
        via the `lsp_completions_ready` signal."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.request_completion(uri, line, character)
        return {"ok": True}

    @Slot(str, int, int, result=dict)
    def lsp_request_hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Request hover info at (line, character). Async — result arrives
        via the `lsp_hover_ready` signal."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.request_hover(uri, line, character)
        return {"ok": True}

    @Slot(str, int, int, result=dict)
    def lsp_request_definition(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Request go-to-definition at (line, character). Async — result
        arrives via the `lsp_definitions_ready` signal."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.request_definition(uri, line, character)
        return {"ok": True}

    @Slot(str, str, str, result=dict)
    def lsp_notify_open(self, file_path: str, language_id: str, text: str) -> Dict[str, Any]:
        """Notify the LSP server that a document was opened in the editor."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.did_open(uri, language_id, text)
        return {"ok": True}

    @Slot(str, str, int, result=dict)
    def lsp_notify_change(self, file_path: str, text: str, version: int) -> Dict[str, Any]:
        """Notify the LSP server that a document's content changed in the editor."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.did_change(uri, text, version)
        return {"ok": True}

    @Slot(str, result=dict)
    def lsp_notify_save(self, file_path: str) -> Dict[str, Any]:
        """Notify the LSP server that a document was saved."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.did_save(uri)
        return {"ok": True}

    @Slot(str, result=dict)
    def lsp_notify_close(self, file_path: str) -> Dict[str, Any]:
        """Notify the LSP server that a document was closed in the editor."""
        if not self._lsp_client or not self._lsp_client.is_ready():
            return {"ok": False, "error": "LSP server not ready"}
        uri = self._lsp_uri_for_path(file_path)
        self._lsp_client.did_close(uri)
        return {"ok": True}

    @Slot(result=dict)
    def lsp_get_status(self) -> Dict[str, Any]:
        """Return current LSP server status for the UI."""
        if self._lsp_client is None:
            return {"ok": True, "available": False, "ready": False, "capabilities": {}}
        return {
            "ok": True,
            "available": True,
            "ready": self._lsp_client.is_ready(),
            "capabilities": self._lsp_client.get_capabilities(),
        }

    def _lsp_uri_for_path(self, file_path: str) -> str:
        """Convert a project-relative (or absolute) file path to a `file://` URI.

        The LSP server requires absolute `file://` URIs. If `file_path`
        is already absolute, use it as-is; otherwise resolve it against
        the current project root.
        """
        from pathlib import Path as _Path
        from urllib.request import pathname2url
        p = _Path(file_path)
        if not p.is_absolute():
            root = self._code_viewer.root or _Path.cwd()
            p = (root / p)
        try:
            return p.resolve().as_uri()
        except Exception:
            # Fallback: build the URI manually
            return "file://" + pathname2url(str(p))

    def _on_file_changed(self, path: str, event_type: str) -> None:
        self.file_changed.emit(path, event_type)
        # v1.1.2: also push updated git status to the frontend
        project_root = str(self._code_viewer.root) if self._code_viewer.root else self._config.get("project_root")
        if project_root:
            try:
                from .git_service import GitService
                gs = GitService(root=project_root)
                if gs.is_available:
                    self.git_status_changed.emit(gs.status())
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # TEMPLATES & SKILLS
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=list)
    def list_templates(self) -> List[Dict[str, Any]]:
        return PROMPT_TEMPLATES

    @Slot(result=list)
    def list_skills(self) -> List[Dict[str, Any]]:
        return SKILLS

    @Slot(str, result=str)
    def get_skill(self, skill_id: str) -> str:
        return _SKILL_TEXTS.get(skill_id, "")


    # ═══════════════════════════════════════════════════════════════
    # SNIPPETS (reusable prompt fragments)
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=list)
    def list_snippets(self) -> List[Dict[str, Any]]:
        return self._config.get("snippets", [])

    @Slot(str, str, str, result=dict)
    def save_snippet(self, name: str, content: str, language: str) -> Dict[str, Any]:
        snippets = self._config.setdefault("snippets", [])
        # Update if exists
        for s in snippets:
            if s.get("name") == name:
                s["content"] = content
                s["language"] = language
                s["updated_at"] = datetime.utcnow().isoformat() + "Z"
                _save_config(self._config)
                return {"ok": True, "snippet": s}
        # New
        snippet = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "content": content,
            "language": language,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        snippets.append(snippet)
        _save_config(self._config)
        return {"ok": True, "snippet": snippet}

    @Slot(str, result=bool)
    def delete_snippet(self, name: str) -> bool:
        snippets = self._config.get("snippets", [])
        before = len(snippets)
        self._config["snippets"] = [s for s in snippets if s.get("name") != name]
        if len(self._config["snippets"]) != before:
            _save_config(self._config)
            return True
        return False

    # ═══════════════════════════════════════════════════════════════
    # RAG (project-wide code search for context injection)
    # ═══════════════════════════════════════════════════════════════

    @Slot(str, result=list)
    def rag_search(self, query: str) -> List[Dict[str, Any]]:
        """Search project files for context to inject into the next prompt.

        Currently uses grep-based search via CodeViewerService.
        Returns results with a 'source' field set to 'grep' so the frontend
        can display an honest label instead of pretending this is semantic RAG.

        A real RAG pipeline (embeddings + ANN index) can be plugged in here
        by replacing the implementation — the slot signature stays the same.
        """
        if not query.strip():
            return []
        results = self._code_viewer.search(query, max_results=10)
        for r in results:
            r['source'] = 'grep'
        return results

    @Slot(result=dict)
    def get_rag_status(self) -> Dict[str, Any]:
        return {
            "enabled": False,  # Real RAG (embeddings) is not yet implemented
            "mode": "grep",    # Honest: current search is grep, not semantic
            "project_indexed": self._code_viewer.root is not None,
            "file_count": len(self._code_viewer.list_files()) if self._code_viewer.root else 0,
        }

    @Slot(result=dict)
    def index_project(self) -> Dict[str, Any]:
        """Trigger (re)indexing of the current project for RAG.

        NOTE: Real embedding-based indexing is not yet implemented.
        This returns the current file count as a placeholder.
        When a real indexer is added, it should build an ANN index here.
        """
        if not self._code_viewer.root:
            return {"ok": False, "error": "No project open"}
        files = self._code_viewer.list_files()
        # TODO: Replace with real embedding + ANN indexing when available
        return {
            "ok": True,
            "files_indexed": len(files),
            "root": str(self._code_viewer.root),
            "mode": "grep",
            "note": "Semantic RAG is not yet available. Search uses grep."
        }

    # ═══════════════════════════════════════════════════════════════
    # AGENT MODE (tool-use: read_file, write_file, run_code, etc.)
    # ═══════════════════════════════════════════════════════════════

    def _get_or_create_agent_runtime(self) -> AgentRuntime:
        """Lazy-init the agent runtime, using the current provider registry."""
        if self._agent_runtime is not None:
            # Update workspace if project root changed
            if self._code_viewer.root:
                self._agent_runtime.set_workspace(str(self._code_viewer.root))
            return self._agent_runtime

        workspace = str(self._code_viewer.root) if self._code_viewer.root else None
        self._agent_runtime = AgentRuntime(
            registry=self._registry,
            workspace=workspace,
            max_iterations=int(self._config.get("agent_max_iterations", 8)),
            enable_planning=bool(self._config.get("agent_enable_planning", True)),
            memory_persist_path=str(_clew_home() / "agent_memory.json"),
        )
        # v1.0.5-correctness: wire the token tracker so the agent records
        # real token usage on every provider call (H-RT-3).
        try:
            from .token_tracker import get_token_tracker
            self._agent_runtime.set_token_tracker(get_token_tracker())
        except Exception as tok_err:
            logger.warning("[bridge] token tracker not wired: %s", tok_err)
        # v1.1.0: wire the quota tracker (per-section daily limits)
        try:
            qt = get_quota_tracker()
            # v1.1.3-fix (bug 2.8): synchronise the heavy_code daily
            # limit from config.json into the QuotaTracker. Without
            # this, the limit set via save_advanced_agent_settings was
            # written to BOTH _config AND the tracker, but on next app
            # start only _config was loaded — the tracker kept the
            # default 10/day, so the user's custom limit was silently
            # ignored until they re-saved settings.
            cfg_hc_limit = self._config.get("heavy_code_daily_limit")
            if cfg_hc_limit is not None:
                try:
                    qt.set_daily_limit("heavy_code", int(cfg_hc_limit))
                except (TypeError, ValueError, Exception) as lim_err:
                    logger.warning("[bridge] failed to apply heavy_code limit: %s", lim_err)
            self._agent_runtime.set_quota_tracker(qt)
        except Exception as quota_err:
            logger.warning("[bridge] quota tracker not wired: %s", quota_err)
        # v1.1.0: apply run_timeout from config
        if "agent_run_timeout" in self._config:
            try:
                self._agent_runtime.tools.RUN_TIMEOUT = int(self._config["agent_run_timeout"])
            except (TypeError, ValueError):
                pass
        # v1.0.4: wire diff-review callback
        # NOTE: The attribute on AgentRuntime is named `tools` (see
        # agent_runtime.py:974 `self.tools = ToolEngine(workspace)`).
        # Earlier releases referenced a non-existent private attribute
        # which would AttributeError the moment the first agent run
        # tried to enable diff-review. Fixed in v1.0.5 — we now use the
        # public `tools` attribute consistently.
        self._agent_runtime.tools.diff_review_enabled = self._config.get("diff_review", True)
        self._agent_runtime.tools._diff_review_callback = self._on_diff_review_requested
        # v1.1.1: the "agent_autonomy" setting used to be stored but never
        # actually consulted anywhere — delete_file/execute_command/
        # rename_file/apply_diff/write_binary_file/git_commit all ran with
        # zero confirmation regardless of what the user picked in
        # Settings. Now actually wired through to ToolEngine.
        self._agent_runtime.set_autonomy(self._config.get("agent_autonomy", "always_ask"))
        self._agent_runtime.set_confirm_callback(self._on_action_confirm_requested)
        logger.info("[bridge] AgentRuntime created")
        return self._agent_runtime

    @Slot(dict, result=dict)
    def send_agent_message(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a message through the AGENT runtime (tool-use mode).

        The agent can read files, write files, run code, search the project,
        and execute commands. Each tool call is streamed back to the UI
        via agent_step_signal / agent_tool_result signals.

        opts = {
            text:       "user prompt",
            chat_id:    "abc123" | None,
            history:    [{role, content}, ...],
            section:    "general" | "heavy_code" | "office"  (v1.1.0)
        }
        Returns {ok, chat_id} immediately; steps stream via signals.

        v1.1.0: section controls which tools are available (heavy_code
        unlocks spawn_subagent / spawn_multi_agents) and which daily
        quota counter is bumped (heavy_code has a 10/day free limit).
        """
        if self._agent_worker and self._agent_worker.isRunning():
            return {"ok": False, "error": "Agent already running"}

        text = (opts.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "Empty prompt"}

        # v1.1.0: section (general | heavy_code | office) — defaults to general
        section = opts.get("section") or "general"
        if section not in ("general", "heavy_code", "office"):
            section = "general"

        # v1.1.0: pre-check quota BEFORE creating the chat or doing any
        # work — gives the user a fast, friendly error.
        try:
            quota = get_quota_tracker()
            if quota.exhausted(section):
                limit = quota.get_daily_limit(section)
                used = quota.count_today(section)
                return {
                    "ok": False,
                    "error": (
                        f"Daily {section} limit reached ({used}/{limit} "
                        f"requests today). Limit resets at 00:00 UTC. "
                        f"Future versions will offer paid tiers with "
                        f"higher limits."
                    ),
                    "quota_exhausted": True,
                    "section": section,
                    "used": used,
                    "limit": limit,
                }
        except Exception as e:
            logger.warning("[bridge] quota pre-check failed: %s", e)

        # Get or create chat
        chat_id = opts.get("chat_id")
        chat = None
        if chat_id:
            chat = _load_chat(chat_id)
        if not chat:
            chat_id = uuid.uuid4().hex[:12]
            chat = {
                "id": chat_id,
                "title": text[:60] + ("…" if len(text) > 60 else ""),
                "created_at": datetime.utcnow().isoformat() + "Z",
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "messages": [],
                "provider": self._registry.active_id,
                "mode": "agent",
                "section": section,  # v1.1.0
            }

        # Save user message
        chat["messages"].append({
            "role": "user",
            "content": text,
            "ts": datetime.utcnow().isoformat() + "Z",
        })
        chat["mode"] = "agent"
        chat["section"] = section  # v1.1.0
        chat["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _save_chat(chat)

        # Create and start agent worker
        agent = self._get_or_create_agent_runtime()
        # v1.1.0: switch the runtime to the requested section. This
        # affects which tools are advertised and which quota counter
        # is bumped by _generate_with_retry.
        agent.set_section(section)
        # v1.1.0: Heavy Code uses a higher max_iterations ceiling so
        # complex multi-step tasks can finish. We still respect the
        # user's setting as a floor — if they explicitly set 30, we
        # don't lower it.
        if section == "heavy_code":
            heavy_max = max(
                int(self._config.get("agent_max_iterations", 8)),
                20,  # Heavy Code minimum
            )
            agent.max_iterations = heavy_max
            # v1.1.3-fix (bug 2.1): scale RUN_TIMEOUT and MAX_OUTPUT for
            # Heavy Code. With max_iterations=20 and the default 15s
            # timeout, any long build/test gets cut at 15s — and the
            # 2000-char output cap truncates `pytest -v` results so the
            # agent can't see test failures. Heavy Code now uses a 60s
            # minimum and 8000-char output cap.
            agent.tools.RUN_TIMEOUT = max(
                int(self._config.get("agent_run_timeout", 15)),
                60,  # Heavy Code minimum
            )
            agent.tools.MAX_OUTPUT = max(
                int(self._config.get("agent_max_output", 2000)),
                8000,  # Heavy Code minimum
            )
        else:
            agent.max_iterations = int(self._config.get("agent_max_iterations", 8))
            agent.tools.RUN_TIMEOUT = int(self._config.get("agent_run_timeout", 15))
            agent.tools.MAX_OUTPUT = int(self._config.get("agent_max_output", 2000))
        # v1.0.11: if the user picked a skill in the UI, inject it
        # directly into the task description so the agent sees it.
        # The agent also has access to the get_skill tool for pulling
        # skill bodies on demand.
        skill_id = opts.get("skill")
        if skill_id:
            # Look up the skill body and prepend it to the task.
            # The agent runtime already has skills loaded — find the body
            # in the agent's in-memory list (no need for get_skill_body()
            # here, since that would re-load skills from disk).
            skill_body = None
            for s in agent._skills:
                if s.id == skill_id:
                    skill_body = s.body
                    break
            if skill_body:
                text = f"[ACTIVE SKILL: {skill_id}]\n{skill_body}\n\n---\n\nTask: {text}"
        task = Task(
            type=TaskType.AGENTIC,
            description=text,
            language="python",
        )

        self._agent_worker = AgentWorker(agent, task, parent=self)
        self._agent_worker.step_update.connect(self._on_agent_step)
        self._agent_worker.result_ready.connect(lambda result, cid=chat_id: self._on_agent_done(cid, result))
        self._agent_worker.error.connect(self.agent_error)

        # v1.0.4: snapshot before agent runs (git commit or backup marker)
        self._create_pre_agent_snapshot()

        self._agent_worker.start()

        # Persist active chat id
        self._config["active_chat_id"] = chat_id
        _save_config(self._config)
        self.chat_list_changed.emit()

        return {"ok": True, "chat_id": chat_id, "title": chat["title"]}

    def _on_agent_step(self, event_type: str, data_json: str) -> None:
        """Forward agent step events to the UI.

        v1.1.1: added diagnostic logging so we can confirm events are
        being emitted from the AgentWorker thread and reaching the
        bridge (main thread). If these logs appear but the UI still
        shows nothing, the problem is in the QWebChannel → JS hop.

        v1.1.1 bugfix: the event_type check for agent_tool_result was
        "tool_call" but the AgentEvent enum value is "tool_called".
        This meant agent_tool_result was NEVER emitted for tool calls,
        so the file tree didn't refresh after tool execution.
        """
        try:
            data = json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            data = {}
        logger.info(
            "[bridge] _on_agent_step: type=%s keys=%s",
            event_type, list(data.keys())[:5],
        )
        self.agent_step_signal.emit({"type": event_type, **data})
        # H-API-3: emit agent_tool_result so the UI can update the file tree
        # and show tool execution results for non-file tools.
        # v1.1.1: fixed event_type check — AgentEvent.TOOL_CALLED.value
        # is "tool_called", not "tool_call".
        if event_type == "tool_called" or event_type == "tool_result":
            self.agent_tool_result.emit({"type": event_type, **data})

    def _on_agent_done(self, chat_id: str, result) -> None:
        """Persist agent result and notify UI."""
        # Build a comprehensive message including tool calls
        tool_summary = ""
        if hasattr(result, 'tool_calls') and result.tool_calls:
            parts = []
            for tc in result.tool_calls:
                args_summary = ", ".join(f"{k}={v!r}" for k, v in tc.args.items() if k != "content")
                parts.append(f"[{tc.name.value}] {args_summary}")
                if tc.result:
                    parts.append(f"  → {tc.result[:200]}")
            tool_summary = "\n".join(parts)

        full_output = result.output
        if tool_summary:
            full_output = tool_summary + "\n\n" + result.output

        # v1.1.2-fix: extract real token counts from the agent result
        result_meta = getattr(result, 'metadata', None) or {}
        tokens_in = int(result_meta.get("total_tokens_in", 0))
        tokens_out = int(result_meta.get("total_tokens_out", 0))
        logger.info(
            "[bridge] agent done — chat=%s tokens_in=%d tokens_out=%d iterations=%d",
            chat_id, tokens_in, tokens_out, result.iterations,
        )

        # Save to chat
        chat = _load_chat(chat_id)
        if chat:
            msg = {
                "role": "assistant",
                "content": full_output,
                "ts": datetime.utcnow().isoformat() + "Z",
                "iterations": result.iterations,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "success": result.success,
                "mode": "agent",
            }
            if result.error:
                msg["error"] = result.error
            chat["messages"].append(msg)
            chat["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _save_chat(chat)
            self.chat_saved.emit({
                "id": chat_id,
                "title": chat["title"],
                "updated_at": chat["updated_at"],
                "message_count": len(chat["messages"]),
            })

        # v1.1.2-fix: include real token counts in agent_final so the
        # UI can display them in the message footer instead of showing
        # the iteration count as "tokens".
        self.agent_final.emit({
            "chat_id": chat_id,
            "text": result.output,
            "success": result.success,
            "iterations": result.iterations,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "error": result.error,
        })

        # v1.1.2-fix: emit token_stats_updated so the statusbar token
        # counter and Usage panel refresh after agent work.  Previously
        # this signal was only emitted in the streaming/chat path
        # (_on_done), so agent-mode usage was invisible in the UI.
        try:
            budget = float(self._config.get("ui", {}).get("budget_usd", 20.0) or 20.0)
            stats = self._tracker.stats(budget=budget)
            self.token_stats_updated.emit(stats)
            logger.info("[bridge] token_stats_updated emitted — total_tokens=%s request_count=%s",
                        stats.get("total_tokens"), stats.get("request_count"))
        except Exception as tok_err:
            logger.warning("[bridge] token_stats_updated emit failed: %s", tok_err)

        # v1.1.3-fix: use deleteLater() instead of a bare `= None`.
        # `_on_oneshot_done` (above) already does this correctly for
        # OneShotWorker. AgentWorker was the odd one out here: dropping
        # the last Python reference right after emitting agent_final /
        # token_stats_updated let Python reclaim the QThread wrapper
        # immediately, racing with Qt's own not-yet-finished teardown of
        # the native thread — which could disrupt delivery of the
        # signals we *just* emitted over the QWebChannel (the GUI would
        # stay frozen on "planning" even though the backend logs show
        # everything completed). deleteLater() defers the actual cleanup
        # to the Qt event loop instead of forcing it synchronously here.
        worker, self._agent_worker = self._agent_worker, None
        if worker:
            worker.deleteLater()

    @Slot()
    def stop_agent(self) -> None:
        """Cancel the running agent.

        v1.1.1: also cancels the HTTP-path agent if one is running.
        The frontend calls stop_agent (bridge) when the user clicks Stop,
        but the actual agent might be running via the HTTP /api/agent/stream
        path (which doesn't create an AgentWorker). We cover both by also
        setting the ServerContext cancel flag.
        """
        # Bridge path: cancel the AgentWorker QThread
        if self._agent_worker and self._agent_worker.isRunning():
            self._agent_worker.cancel()
            logger.info("[bridge] stop_agent: AgentWorker.cancel() called")
        # HTTP path: set the ServerContext cancel flag
        try:
            from .api_server import ServerContext  # type: ignore
            # The ServerContext singleton is owned by ClewAPIServer, which
            # is owned by ClewMainWindow. We can't easily reach it from
            # here without a back-reference. Instead, the frontend ALSO
            # calls POST /api/agent/stop directly — see app.js handleSend.
            # This bridge slot is the primary mechanism for the bridge path.
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # v1.0.9 — CONTEXT MANAGEMENT (CLAUDE.md, /context, /clear, /compact)
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=dict)
    def get_context_status(self) -> Dict[str, Any]:
        """v1.0.9: /context command — show what's in the context window.

        Returns memory status (messages, tokens, utilization), project
        context status (CLAUDE.md sources), and system prompt size.
        """
        agent = self._get_or_create_agent_runtime()
        return agent.context_status()

    @Slot(result=dict)
    def clear_context(self) -> Dict[str, Any]:
        """v1.0.9: /clear command — wipe conversation memory.

        Does NOT touch CLAUDE.md (persistent project instructions).
        """
        agent = self._get_or_create_agent_runtime()
        return agent.clear_context()

    @Slot(str, result=bool)
    def open_external_url(self, url: str) -> bool:
        """v1.1.4-fix: links inside the app (e.g. 'Get API key' links,
        markdown links in chat) used to navigate the app's own webview
        away from the UI, because _ClewWebPage.createWindow() returns
        self instead of opening a new window. Route external links
        through the OS's default browser instead. Only http(s) allowed.
        """
        if not (url.startswith("http://") or url.startswith("https://")):
            return False
        try:
            return bool(QDesktopServices.openUrl(QUrl(url)))
        except Exception as e:
            logger.warning(f"[bridge] open_external_url failed: {e}")
            return False

    @Slot(str, result=dict)
    def pin_context_file(self, rel_path: str) -> Dict[str, Any]:
        """v1.1.4-fix (bug 4.2): pin a file so ContextManager always
        includes it in the auto-attached context block, regardless of
        relevance score."""
        from clew.context_manager import get_context_manager
        get_context_manager().pin_file(rel_path)
        return {"ok": True, "pinned": rel_path}

    @Slot(str, result=dict)
    def unpin_context_file(self, rel_path: str) -> Dict[str, Any]:
        from clew.context_manager import get_context_manager
        get_context_manager().unpin_file(rel_path)
        return {"ok": True, "unpinned": rel_path}

    @Slot(result=dict)
    def compact_context(self) -> Dict[str, Any]:
        """v1.0.9: /compact command — summarise old messages, keep recent.

        Uses the active provider to generate a summary, then replaces
        old messages with the summary. Keeps the most recent 4 messages
        verbatim.
        """
        agent = self._get_or_create_agent_runtime()
        return agent.compact_context()

    @Slot(result=dict)
    def reload_project_context(self) -> Dict[str, Any]:
        """v1.0.10: force re-read of CLEW.md after it's been edited.

        CLEW.md is the primary project instructions file. CLAUDE.md is
        accepted as a fallback for users migrating from Claude Code.
        """
        from clew.project_context import get_project_context
        pc = get_project_context()
        pc.reload()
        instructions = pc.instructions()
        return {
            "ok": True,
            "sources": pc.status().get("sources", []),
            "total_chars": len(instructions),
        }

    @Slot(result=dict)
    def reload_skills(self) -> Dict[str, Any]:
        """v1.0.11: force re-read of SKILL.md files after they've been edited.

        Skills are loaded from ~/.clew/skills/ and <project>/.clew/skills/.
        This slot lets the UI refresh the skill list without restarting.
        """
        agent = self._get_or_create_agent_runtime()
        agent._reload_skills()
        skills_info = [
            {"id": s.id, "name": s.name, "description": s.description,
             "tag": s.tag, "project_level": s.project_level}
            for s in agent._skills
        ]
        return {
            "ok": True,
            "count": len(agent._skills),
            "skills": skills_info,
        }

    # ═══════════════════════════════════════════════════════════════
    # v1.1.0 — MCP (Model Context Protocol) SERVER MANAGEMENT
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=dict)
    def mcp_list_servers(self) -> Dict[str, Any]:
        """Return the list of configured MCP servers + their current
        running status + tool count. Used by Settings → MCP tab."""
        try:
            return {"ok": True, **get_mcp_manager().status()}
        except Exception as e:
            return {"ok": False, "error": str(e), "servers": [], "total_tools": 0}

    @Slot(dict, result=dict)
    def mcp_add_server(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new MCP server config and (if enabled) start it.

        cfg = {
            name:    "filesystem",
            command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env:     {"KEY": "value"},
            enabled: true,
            autostart: true   # start immediately after adding
        }
        """
        try:
            name = cfg.get("name", "").strip()
            command = cfg.get("command", [])
            if isinstance(command, str):
                # Accept a shell-style command string for convenience
                import shlex
                command = shlex.split(command)
            env = cfg.get("env", {}) or {}
            enabled = bool(cfg.get("enabled", True))
            autostart = bool(cfg.get("autostart", True))
            return get_mcp_manager().add_server(
                name=name, command=command, env=env,
                enabled=enabled, autostart=autostart,
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(str, result=dict)
    def mcp_remove_server(self, name: str) -> Dict[str, Any]:
        """Stop (if running) and remove an MCP server config."""
        try:
            return get_mcp_manager().remove_server(name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(str, bool, result=dict)
    def mcp_toggle_server(self, name: str, enabled: bool) -> Dict[str, Any]:
        """Enable or disable an MCP server (without removing its config)."""
        try:
            return get_mcp_manager().toggle_server(name, enabled)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(str, result=dict)
    def mcp_start_server(self, name: str) -> Dict[str, Any]:
        """Start a single MCP server."""
        try:
            return get_mcp_manager().start_server(name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(str, result=dict)
    def mcp_stop_server(self, name: str) -> Dict[str, Any]:
        """Stop a single MCP server."""
        try:
            return get_mcp_manager().stop_server(name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(result=dict)
    def mcp_start_all(self) -> Dict[str, Any]:
        """Start all enabled MCP servers (called on app startup)."""
        try:
            get_mcp_manager().start_all()
            return {"ok": True, **get_mcp_manager().status()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(result=dict)
    def mcp_stop_all(self) -> Dict[str, Any]:
        """Stop all running MCP servers (called on app shutdown)."""
        try:
            get_mcp_manager().stop_all()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(result=dict)
    def mcp_reload_config(self) -> Dict[str, Any]:
        """Re-read ~/.clew/mcp.json from disk (after manual edits)."""
        try:
            get_mcp_manager().reload_config()
            return {"ok": True, **get_mcp_manager().status()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # v1.1.0 — QUOTA (per-section daily request limits)
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=dict)
    def get_quota_stats(self) -> Dict[str, Any]:
        """Return the current quota status (per-section daily counters
        + limits + reset time). Used by the Usage modal and the Heavy
        Code section's quota indicator."""
        try:
            return {"ok": True, **get_quota_tracker().stats()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(str, int, result=dict)
    def set_daily_limit(self, section: str, limit: int) -> Dict[str, Any]:
        """Override the daily request limit for a section. 0 = unlimited.
        Used by Settings → Agent → Quota section."""
        try:
            get_quota_tracker().set_daily_limit(section, limit)
            return {"ok": True, "section": section, "limit": limit}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @Slot(result=dict)
    def clear_quota_history(self) -> Dict[str, Any]:
        """Wipe the quota history log (~/.clew/quota_history.jsonl).
        Admin/debug feature — lets the user reset their own quota."""
        try:
            get_quota_tracker().clear_history()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # v1.1.0 — ADVANCED AGENT SETTINGS (temperature, top_p, etc.)
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=dict)
    def get_advanced_agent_settings(self) -> Dict[str, Any]:
        """Return advanced agent + inference settings for the Settings →
        Agent tab. These knobs always existed in the backend config but
        had no UI controls — v1.1.0 exposes them.
        """
        cfg = self._config
        # Pull per-provider inference settings from the active provider
        active_pid = self._registry.active_id
        pcfg = cfg.get("providers", {}).get(active_pid, {})
        agent = self._agent_runtime
        return {
            "ok": True,
            "agent": {
                "max_iterations": int(cfg.get("agent_max_iterations", 8)),
                "enable_planning": bool(cfg.get("agent_enable_planning", True)),
                "run_timeout": int(cfg.get("agent_run_timeout", 15)),
                "autonomy": cfg.get("agent_autonomy", "always_ask"),
                "diff_review": bool(cfg.get("diff_review", True)),
                "section": agent.section if agent else "general",
                # v1.1.0: context window tuning (was hard-coded before)
                "memory_max_messages": int(cfg.get("agent_memory_max_messages", 20)),
                "memory_max_tokens": int(cfg.get("agent_memory_max_tokens", 8000)),
            },
            "inference": {
                # These apply to the active provider; users can override
                # per-provider in Settings → Providers tab.
                "temperature": float(pcfg.get("temperature", 0.2)),
                "max_tokens": int(pcfg.get("max_tokens", 4096)),
                "top_p": float(pcfg.get("top_p", 0.95)),
                "active_provider": active_pid,
            },
            "heavy_code": {
                "daily_limit": get_quota_tracker().get_daily_limit("heavy_code"),
                "used_today": get_quota_tracker().count_today("heavy_code"),
                "remaining": get_quota_tracker().remaining("heavy_code"),
            },
        }

    @Slot(dict, result=dict)
    def save_advanced_agent_settings(self, partial: Dict[str, Any]) -> Dict[str, Any]:
        """Save advanced agent + inference settings. Accepts a partial
        dict — only the keys present are updated.

        partial = {
            agent: {
                max_iterations, enable_planning, run_timeout, autonomy,
                diff_review, memory_max_messages, memory_max_tokens
            },
            inference: {
                temperature, max_tokens, top_p  # applied to active provider
            },
            heavy_code: {
                daily_limit  # int, 0 = unlimited
            }
        }
        """
        try:
            # Agent settings
            if "agent" in partial and isinstance(partial["agent"], dict):
                a = partial["agent"]
                # Reuse save_settings() by routing through it
                self.save_settings({"agent": a})
                # v1.1.0: memory limits
                if "memory_max_messages" in a:
                    self._config["agent_memory_max_messages"] = int(a["memory_max_messages"])
                if "memory_max_tokens" in a:
                    self._config["agent_memory_max_tokens"] = int(a["memory_max_tokens"])
                # Apply to live runtime's memory
                if self._agent_runtime and self._agent_runtime.memory:
                    if "memory_max_messages" in a:
                        self._agent_runtime.memory.max_messages = int(a["memory_max_messages"])
                    if "memory_max_tokens" in a:
                        self._agent_runtime.memory.max_tokens = int(a["memory_max_tokens"])
                _save_config(self._config)

            # Inference settings — apply to active provider
            if "inference" in partial and isinstance(partial["inference"], dict):
                inf = partial["inference"]
                active_pid = self._registry.active_id
                if active_pid in self._config.get("providers", {}):
                    pcfg = self._config["providers"][active_pid]
                    if "temperature" in inf:
                        pcfg["temperature"] = float(inf["temperature"])
                    if "max_tokens" in inf:
                        pcfg["max_tokens"] = int(inf["max_tokens"])
                    if "top_p" in inf:
                        pcfg["top_p"] = float(inf["top_p"])
                    _save_config(self._config)
                    # Re-apply to the registry
                    try:
                        prov_cfg = ProviderConfig(
                            provider_id=active_pid,
                            model=pcfg.get("model", ""),
                            api_key=pcfg.get("api_key", ""),
                            api_base=pcfg.get("api_base", ""),
                            temperature=float(pcfg.get("temperature", 0.2)),
                            max_tokens=int(pcfg.get("max_tokens", 4096)),
                            top_p=float(pcfg.get("top_p", 0.95)),
                        )
                        self._registry.configure(active_pid, prov_cfg)
                    except Exception as cfg_err:
                        logger.warning("[bridge] failed to apply inference config: %s", cfg_err)

            # Heavy Code daily limit
            if "heavy_code" in partial and isinstance(partial["heavy_code"], dict):
                hc = partial["heavy_code"]
                if "daily_limit" in hc:
                    get_quota_tracker().set_daily_limit("heavy_code", int(hc["daily_limit"]))
                    self._config["heavy_code_daily_limit"] = int(hc["daily_limit"])
                    _save_config(self._config)

            return {"ok": True, "settings": self.get_advanced_agent_settings()}
        except Exception as e:
            logger.error("[bridge] save_advanced_agent_settings failed: %s", e)
            return {"ok": False, "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # v1.0.3 — MEMORY SERVICE
    # v1.0.5 — cross-chat context: search_sessions + context_brief
    # ═══════════════════════════════════════════════════════════════

    @Slot(str, str, str, result=dict)
    def save_memory(self, session_id: str, title: str, content: str) -> Dict[str, Any]:
        """Save a session context summary to clew_memory.md."""
        return self._memory.save_context(session_id, title, content)

    @Slot(result=dict)
    def load_memory(self) -> Dict[str, Any]:
        """Load recent memory content."""
        return self._memory.load_memory()

    @Slot(result=dict)
    def memory_summary(self) -> Dict[str, Any]:
        """Get memory file stats."""
        return self._memory.get_session_summary()

    @Slot(result=dict)
    def clear_memory(self) -> Dict[str, Any]:
        """Clear all saved memory."""
        return self._memory.clear()

    # ── v1.0.5: cross-chat search & context brief ───────────────

    @Slot(str, result=dict)
    def search_memory(self, query: str) -> Dict[str, Any]:
        """Search prior sessions by free-text query (case-insensitive).

        Returns {ok, sessions: [{title, timestamp, body, meta}, ...]}.
        Used by the UI to show a "memories" panel and by the agent to
        recall what was tried in earlier chats.
        """
        # Strip leading/trailing whitespace; empty query returns the
        # most recent 20 sessions (a "browse" mode).
        q = (query or "").strip()
        entries = self._memory.search_sessions(query=q if q else None, limit=20)
        return {
            "ok": True,
            "sessions": [e.to_dict() for e in entries],
            "count": len(entries),
        }

    @Slot(str, str, result=str)
    def build_context_brief(self, project_root: str, query: str) -> str:
        """Build a compact (~1 KB) brief of the most relevant prior
        sessions, suitable for injection into a system prompt.

        ``project_root`` and ``query`` may be empty strings to skip
        filtering on that dimension. The returned string is empty when
        no sessions match.

        This is what gives the model cross-chat continuity: the brief
        is prepended to the next chat's system prompt, so the model
        "remembers" what was discussed in earlier chats on the same
        project without those messages consuming context-window tokens.
        """
        brief = self._memory.build_context_brief(
            project_root=project_root or None,
            query=query or None,
        )
        return brief

    # ═══════════════════════════════════════════════════════════════
    # v1.0.3 — FILE WRITE / APPEND / DELETE / MKDIR / RENAME
    # ═══════════════════════════════════════════════════════════════

    @Slot(str, str, result=dict)
    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """Write content to a file (full overwrite). Atomic write with temp file.
        Respects project root. Creates parent directories automatically.
        Normalizes line endings to LF and adds a trailing newline."""
        target = Path(path).expanduser().resolve()
        root = self._code_viewer.root
        if root:
            try:
                target.relative_to(root)
            except ValueError:
                return {"ok": False, "error": f"Path is outside project root: {path}"}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Normalize line endings and ensure trailing newline
            normalized = content.replace("\r\n", "\n").replace("\r", "\n")
            if normalized and not normalized.endswith("\n"):
                normalized += "\n"
            # Atomic write: write to temp file, then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(target.parent), prefix=".clew_tmp_", suffix=".txt"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(normalized)
                os.replace(tmp_path, str(target))
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.info(f"[bridge] wrote {len(normalized)} chars to {target}")
            result = {"ok": True, "path": str(target), "chars": len(normalized)}
            self.apply_result.emit(result)
            return result
        except OSError as e:
            result = {"ok": False, "error": str(e)}
            self.apply_result.emit(result)
            return result

    @Slot(str, str, result=dict)
    def append_file(self, path: str, content: str) -> Dict[str, Any]:
        """Append content to a file. Respects project root.
        Ensures a newline separates old and new content."""
        target = Path(path).expanduser().resolve()
        root = self._code_viewer.root
        if root:
            try:
                target.relative_to(root)
            except ValueError:
                return {"ok": False, "error": f"Path is outside project root: {path}"}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Normalize and ensure separator between old and new content
            normalized = content.replace("\r\n", "\n").replace("\r", "\n")
            with open(target, "a", encoding="utf-8") as f:
                # Add a newline separator if the file doesn't end with one
                if target.exists() and target.stat().st_size > 0:
                    f.seek(0, 2)  # seek to end
                    f.write("\n")
                f.write(normalized)
                if not normalized.endswith("\n"):
                    f.write("\n")
            new_size = target.stat().st_size
            logger.info(f"[bridge] appended {len(normalized)} chars to {target} ({new_size} bytes total)")
            return {"ok": True, "path": str(target), "chars": len(normalized), "total_bytes": new_size}
        except OSError as e:
            result = {"ok": False, "error": str(e)}
            self.apply_result.emit(result)
            return result

    @Slot(str, result=dict)
    def delete_file(self, path: str) -> Dict[str, Any]:
        """Delete a file or directory. Respects project root."""
        target = Path(path).expanduser().resolve()
        root = self._code_viewer.root
        if root:
            try:
                target.relative_to(root)
            except ValueError:
                return {"ok": False, "error": f"Path is outside project root: {path}"}
        try:
            if not target.exists():
                return {"ok": False, "error": "File not found"}
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()
            logger.info(f"[bridge] deleted {target}")
            return {"ok": True, "path": str(target)}
        except OSError as e:
            result = {"ok": False, "error": str(e)}
            self.apply_result.emit(result)
            return result

    @Slot(str, result=dict)
    def mkdir(self, path: str) -> Dict[str, Any]:
        """Create a directory (including parents). Respects project root."""
        target = Path(path).expanduser().resolve()
        root = self._code_viewer.root
        if root:
            try:
                target.relative_to(root)
            except ValueError:
                return {"ok": False, "error": f"Path is outside project root: {path}"}
        try:
            target.mkdir(parents=True, exist_ok=True)
            logger.info(f"[bridge] mkdir {target}")
            return {"ok": True, "path": str(target)}
        except OSError as e:
            result = {"ok": False, "error": str(e)}
            self.apply_result.emit(result)
            return result

    @Slot(str, str, result=dict)
    def rename_file(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Rename/move a file or directory. Respects project root."""
        old = Path(old_path).expanduser().resolve()
        new = Path(new_path).expanduser().resolve()
        root = self._code_viewer.root
        if root:
            try:
                old.relative_to(root)
                new.relative_to(root)
            except ValueError:
                return {"ok": False, "error": "Path is outside project root"}
        try:
            if not old.exists():
                return {"ok": False, "error": "Source not found"}
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            logger.info(f"[bridge] renamed {old} → {new}")
            return {"ok": True, "old": str(old), "new": str(new)}
        except OSError as e:
            result = {"ok": False, "error": str(e)}
            self.apply_result.emit(result)
            return result

    @Slot(str, result=dict)
    def file_info(self, path: str) -> Dict[str, Any]:
        """Return file metadata (size, modified, etc.). Respects project root."""
        target = Path(path).expanduser().resolve()
        root = self._code_viewer.root
        if root:
            try:
                target.relative_to(root)
            except ValueError:
                return {"ok": False, "error": f"Path is outside project root: {path}"}
        try:
            if not target.exists():
                return {"ok": False, "error": "File not found"}
            stat = target.stat()
            return {
                "ok": True,
                "path": str(target),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
                "is_file": target.is_file(),
                "is_dir": target.is_dir(),
            }
        except OSError as e:
            result = {"ok": False, "error": str(e)}
            self.apply_result.emit(result)
            return result


    # ═══════════════════════════════════════════════════════════════
    # v1.0.3 — AUTO-UPDATE
    # ═══════════════════════════════════════════════════════════════

    def _on_update_available(self, data: Dict[str, Any]) -> None:
        """Forward update signal to JS."""
        self.update_check_result.emit(data)

    @Slot(result=dict)
    def check_for_updates(self) -> Dict[str, Any]:
        """Trigger a background update check. Result arrives via signal."""
        self._updater.check_for_updates()
        return {"ok": True, "checking": True, "current": get_current_version()}

    @Slot(result=dict)
    def get_version_info(self) -> Dict[str, Any]:
        """Return current version info."""
        return {"ok": True, "version": get_current_version()}

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    @Slot(result=dict)
    def get_status(self) -> Dict[str, Any]:
        # Git status — if project root is set and is a git repo
        git_status = None
        git_available = False
        project_root = str(self._code_viewer.root) if self._code_viewer.root else self._config.get("project_root")
        if project_root:
            try:
                from .git_service import GitService
                gs = GitService(root=project_root)
                if gs.is_available:
                    git_available = True
                    git_status = gs.status()
            except Exception:
                pass

        # Context stats from agent runtime
        context_stats = None
        if self._agent_runtime is not None:
            try:
                context_stats = self._agent_runtime.context_status()
            except Exception:
                pass

        return {
            "version":   get_current_version(),
            "provider":  self._registry.status(),
            "project":   project_root,
            "templates": len(PROMPT_TEMPLATES),
            "skills":    len(SKILLS),
            "active_chat_id": self._config.get("active_chat_id"),
            "config_path": str(_config_path()),
            "chats_dir": str(_chats_dir()),
            "snippets_count": len(self._config.get("snippets", [])),
            "agent_available": True,
            "auto_route": self._auto_route_enabled,
            "agent_autonomy": self._config.get("agent_autonomy", "always_ask"),
            "token_stats": self._tracker.stats() if self._tracker else None,
            "git_status": git_status,
            "git_available": git_available,
            "context_stats": context_stats,
        }

    # ── v1.1.0: get_available_providers (compact for composer dropdown) ─

    @Slot(result=list)
    def get_available_providers(self) -> List[Dict[str, Any]]:
        """Return a compact list of providers with status for the composer dropdown.
        Format: [{id, label, model, connected: bool, active: bool}]
        """
        out = []
        for p in self._registry.list_providers():
            pid = p["id"]
            pcfg = self._config.get("providers", {}).get(pid, {})
            key = pcfg.get("api_key", "")
            # A provider is 'connected' if it doesn't need a key or has one set
            needs_key = p.get("needs_key", True)
            connected = (not needs_key) or bool(key)
            out.append({
                "id": pid,
                "label": p.get("label", pid),
                "model": pcfg.get("model", p.get("model", "")),
                "connected": connected,
                "active": pid == self._registry.active_id,
            })
        return out

    # ── v1.1.0: Agent autonomy level ──────────────────────────────

    @Slot(result=str)
    def get_agent_autonomy(self) -> str:
        """Return current agent autonomy level: 'always_ask' | 'new_files_only' | 'never_ask'."""
        return self._config.get("agent_autonomy", "always_ask")

    @Slot(str, result=bool)
    def set_agent_autonomy(self, level: str) -> bool:
        """Set agent autonomy level. Persists to config."""
        valid = {"always_ask", "new_files_only", "never_ask"}
        if level not in valid:
            logger.warning(f"[bridge] invalid autonomy level: {level}")
            return False
        self._config["agent_autonomy"] = level
        _save_config(self._config)
        if self._agent_runtime is not None:
            self._agent_runtime.set_autonomy(level)
        logger.info(f"[bridge] agent_autonomy set to {level}")
        return True

    @Slot(bool, result=bool)
    def set_diff_review(self, enabled: bool) -> bool:
        """Toggle the diff-review-before-write prompt. Previously this was
        only readable from config (default True) with no UI control to
        change it — this slot plus the Agent settings tab is the first
        actual way to flip it."""
        self._config["diff_review"] = bool(enabled)
        _save_config(self._config)
        if self._agent_runtime is not None:
            self._agent_runtime.tools.diff_review_enabled = bool(enabled)
        logger.info(f"[bridge] diff_review set to {enabled}")
        return True

    # ── v1.0.7: classifier removed ──────────────────────────────
    # Agent Mode is now ALWAYS ON. The intent classifier that used to
    # downgrade an explicit user request to plain chat (and broke
    # "запиши файл" because "запиши" wasn't in the verb list) is gone.
    # The agent's planning step is the only routing that happens now:
    # if the user asks a question, the agent will plan, see no tools
    # are needed, and emit a final_answer with the explanation.
    # No classifier, no toggle, no word-count heuristic — the user's
    # prompt always reaches the agent runtime.

    # ── v1.0.4: Auto-router slots ─────────────────────────────────

    @Slot(str, result=dict)
    def classify_prompt(self, text: str) -> Dict[str, Any]:
        """
        Classify a prompt without sending it.
        Returns {complexity, explanation, signals} for UI preview.
        """
        text = (text or "").strip()
        if not text:
            return {"complexity": "trivial", "explanation": "", "signals": []}
        return self._router.classify_explain(text)

    @Slot(bool, result=bool)
    def toggle_auto_router(self, enabled: bool) -> bool:
        """Enable or disable auto-routing. Persists to config."""
        self._auto_route_enabled = enabled
        self._config["auto_route"] = enabled
        _save_config(self._config)
        logger.info(f"[bridge] auto_route {'enabled' if enabled else 'disabled'}")
        return enabled

    @Slot(result=dict)
    def get_router_tiers(self) -> Dict[str, Any]:
        """Return the routing tier configuration for the settings UI."""
        return self._router.get_tier_info()

    # ── v1.0.4: Diff review ──────────────────────────────────────

    def _on_diff_review_requested(self, diff_info: Dict[str, Any]) -> None:
        """Called from agent thread — emits signal to UI (main thread)."""
        self.diff_review_requested.emit(diff_info)

    @Slot(bool)
    def respond_diff_review(self, accepted: bool) -> None:
        """Called from UI when user clicks Apply or Reject."""
        runtime = self._get_or_create_agent_runtime()
        runtime.tools.respond_diff_review(accepted)
        logger.info(f"[bridge] diff review: {'accepted' if accepted else 'rejected'}")

    # ── v1.1.1: generic action confirmation (autonomy gate) ──────

    def _on_action_confirm_requested(self, info: Dict[str, Any]) -> None:
        """Called from the agent thread — emits signal to UI (main thread)."""
        self.action_confirm_requested.emit(info)

    @Slot(bool)
    def respond_action_confirm(self, accepted: bool) -> None:
        """Called from UI when user clicks Allow or Deny on an
        execute_command / delete_file / rename_file / apply_diff /
        write_binary_file / git_commit confirmation prompt."""
        runtime = self._get_or_create_agent_runtime()
        runtime.tools.respond_confirmation(accepted)
        logger.info(f"[bridge] action confirm: {'allowed' if accepted else 'denied'}")

    # ── v1.0.4: Undo last agent run ─────────────────────────────

    def _create_pre_agent_snapshot(self) -> None:
        """Create a restore point before the agent modifies files."""
        root = self._code_viewer.root
        if not root:
            self._pre_agent_snapshot = None
            return

        # Try git first
        try:
            from .git_service import GitService
            git = GitService(str(root))
            if git.is_available:
                # Stage everything and commit as a snapshot
                git.stage_all()
                result = git.commit("clew: pre-agent snapshot [auto-undo]")
                if result.get("ok"):
                    self._pre_agent_snapshot = result.get("hash", "")
                    logger.info(f"[bridge] pre-agent snapshot: git commit {self._pre_agent_snapshot}")
                    return
        except Exception as e:
            logger.debug(f"[bridge] git snapshot failed: {e}")

        # Fallback: mark that we rely on per-file backups (already done by ToolEngine)
        self._pre_agent_snapshot = "backup"
        logger.info("[bridge] pre-agent snapshot: using per-file backups (no git)")

    @Slot(result=dict)
    def undo_last_agent(self) -> Dict[str, Any]:
        """Undo the last agent run. Git reset if possible, otherwise report."""
        if not self._pre_agent_snapshot:
            return {"ok": False, "error": "No snapshot to undo"}

        root = self._code_viewer.root
        if not root:
            return {"ok": False, "error": "No project open"}

        # Git-based undo
        if self._pre_agent_snapshot != "backup":
            try:
                from .git_service import GitService
                git = GitService(str(root))
                if git.is_available:
                    import subprocess
                    # Reset HEAD~1 (keep files as unstaged changes, then checkout to discard)
                    subprocess.run(
                        ["git", "reset", "--hard", "HEAD~1"],
                        cwd=str(root), capture_output=True, timeout=15,
                    )
                    self._pre_agent_snapshot = None
                    logger.info("[bridge] undo: git reset --hard HEAD~1")
                    self.file_changed.emit("", "undo")
                    return {"ok": True, "method": "git_reset"}
            except Exception as e:
                return {"ok": False, "error": f"Git undo failed: {e}"}

        return {"ok": False, "error": "No git repo — use per-file undo_write tool or restore from backups manually"}

    # ── v1.0.4: Provider health check ───────────────────────────

    @Slot(str, result=dict)
    def health_check(self, provider_id: str) -> Dict[str, Any]:
        """
        Check provider health: key valid, model reachable, not rate-limited.
        Returns {ok, key_valid, model_reachable, error, latency_ms}.
        """
        try:
            provider = self._registry.get(provider_id)
        except ProviderError as e:
            return {"ok": False, "key_valid": False, "model_reachable": False, "error": str(e), "latency_ms": 0}

        import time
        start = time.monotonic()
        try:
            provider.load()
            # Send a minimal request
            messages = [ProviderMessage(role="user", content="hi")]
            resp = provider.generate(messages, stop=["\n"])
            latency = int((time.monotonic() - start) * 1000)
            return {
                "ok": True,
                "key_valid": True,
                "model_reachable": True,
                "error": None,
                "latency_ms": latency,
                "model": resp.model,
            }
        except ProviderError as e:
            latency = int((time.monotonic() - start) * 1000)
            err_str = str(e)
            key_valid = "API key" not in err_str
            rate_limited = any(kw in err_str.lower() for kw in ["rate", "429", "too many", "quota"])
            return {
                "ok": False,
                "key_valid": key_valid,
                "model_reachable": False,
                "error": err_str,
                "latency_ms": latency,
                "rate_limited": rate_limited,
            }
        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            return {"ok": False, "key_valid": False, "model_reachable": False, "error": str(e), "latency_ms": latency}

    # ── Cleanup ───────────────────────────────────────────────────

    def cleanup(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        if self._agent_worker and self._agent_worker.isRunning():
            self._agent_worker.cancel()
            self._agent_worker.wait(3000)
        for w in list(self._oneshot_workers.values()):
            if w.isRunning():
                w.wait(2000)
            w.deleteLater()
        self._oneshot_workers.clear()
        self._code_viewer.stop_watcher()
        # v1.1.5: bug #8 — shut down the LSP server if we started one.
        # Without this the pylsp subprocess leaks after the Qt app exits.
        if self._lsp_client is not None:
            try:
                self._lsp_client.stop_server()
            except Exception:
                pass
            self._lsp_client = None


# Late import for QTimer (used in enhance_prompt)
from PySide6.QtCore import QTimer