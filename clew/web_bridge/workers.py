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

