"""
NimPool — rate-limited, model-routed wrapper around NvidiaNIMProvider.

The Nvidia NIM API (https://integrate.api.nvidia.com/v1) is OpenAI-
compatible and exposes many models under one key. The free tier caps
at 40 requests/minute. We deliberately underbudget to 35 RPM so that
background traffic (one-off UI calls, Guardian LLM reviews) doesn't
trip the limit.

Design choices:

* Sliding-window throttle (not token bucket) — simplest correct
  implementation. We keep the timestamps of the last 60s of requests
  and block (sleep) if there are already 35 in the window.

* Model routing: callers ask for a *role* ("plan", "implement",
  "review", "quick") and the pool picks the model. The mapping is
  configurable; the defaults are sane Llama 3.1 sizes that NIM hosts.

* Direct provider.generate() call — we do NOT go through AgentRuntime.
  The Builder needs cheap, fast LLM calls for planning/review without
  the full ReAct loop overhead. (Implementation of code edits DOES go
  through AgentRuntime, which uses its own throttled provider.)

* Thread-safe — a single pool can be shared across multiple worker
  threads if the Builder is ever parallelised.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Deque, List, Optional

from ..providers import ProviderConfig, ProviderMessage, ProviderResponse
from ..providers.nvidia_nim import NvidiaNIMProvider

logger = logging.getLogger(__name__)


# Default role → NIM model mapping. The user can override any of these
# via BuilderConfig.nim_models.
DEFAULT_MODEL_FOR_ROLE: Dict[str, str] = {
    "plan":       "meta/llama-3.1-70b-instruct",
    "implement":  "meta/llama-3.1-70b-instruct",
    "review":     "meta/llama-3.1-70b-instruct",
    "quick":      "meta/llama-3.1-8b-instruct",
}


@dataclass
class NimPoolConfig:
    """Configuration for the NIM rate-limited pool.

    Attributes:
        api_key: Nvidia API key. If None, falls back to NVIDIA_API_KEY env.
        rpm_limit: max requests per minute the pool will allow. Default 35
            (leaves 5 RPM of headroom under the 40 RPM NIM free-tier cap).
        window_seconds: size of the sliding window (default 60).
        default_models: role → model name. Override per-call with role=...
        temperature: default sampling temperature (0.2 — focused, low jitter).
        max_tokens: default output cap (4096).
        timeout: per-request HTTP timeout in seconds.
    """
    api_key: Optional[str] = None
    rpm_limit: int = 35
    window_seconds: int = 60
    default_models: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODEL_FOR_ROLE))
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 120.0


class NimPool:
    """Rate-limited, model-routed pool of NvidiaNIMProvider instances.

    One provider instance per model (NIM providers are stateless HTTP
    clients; reusing the same instance for repeated calls to the same
    model avoids re-creating the requests.Session).
    """

    def __init__(self, config: NimPoolConfig) -> None:
        self._cfg = config
        self._lock = threading.RLock()
        self._timestamps: Deque[float] = deque()
        self._providers: Dict[str, NvidiaNIMProvider] = {}
        self._total_requests = 0
        self._total_throttled_secs = 0.0
        self._total_tokens_in = 0
        self._total_tokens_out = 0

    # ── Public API ────────────────────────────────────────────────

    def chat(
        self,
        prompt: str,
        *,
        role: str = "quick",
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[List[str]] = None,
    ) -> ProviderResponse:
        """Blocking chat call. Throttles to rpm_limit.

        Args:
            prompt: the user message.
            role: which default model to use ("plan"/"implement"/"review"/"quick").
            system: optional system prompt.
            model: explicit model override (skips role lookup).
            temperature: override pool default.
            max_tokens: override pool default.
            stop: stop sequences.

        Returns:
            ProviderResponse from the NIM provider.
        """
        chosen_model = model or self._cfg.default_models.get(role) or self._cfg.default_models["quick"]

        messages: List[ProviderMessage] = []
        if system:
            messages.append(ProviderMessage(role="system", content=system))
        messages.append(ProviderMessage(role="user", content=prompt))

        provider = self._get_provider(chosen_model)
        self._throttle()

        cfg = ProviderConfig(
            provider_id="nvidia_nim",
            model=chosen_model,
            api_key=self._cfg.api_key,
            api_base=NvidiaNIMProvider.api_base,
            temperature=temperature if temperature is not None else self._cfg.temperature,
            max_tokens=max_tokens if max_tokens is not None else self._cfg.max_tokens,
            timeout=self._cfg.timeout,
        )
        # Reconfigure on the fly if needed (cheap — just sets attributes).
        provider.config = cfg

        try:
            resp = provider.generate(messages, stop=stop)
        except Exception:
            # Record the attempt so the throttle window stays accurate
            # even on failure.
            raise
        finally:
            self._total_requests += 1

        if resp.tokens_in:
            self._total_tokens_in += resp.tokens_in
        if resp.tokens_out:
            self._total_tokens_out += resp.tokens_out
        return resp

    def stats(self) -> Dict[str, Any]:
        """Pool statistics for the reporter."""
        with self._lock:
            return {
                "total_requests":   self._total_requests,
                "throttled_secs":   round(self._total_throttled_secs, 2),
                "tokens_in":        self._total_tokens_in,
                "tokens_out":       self._total_tokens_out,
                "rpm_limit":        self._cfg.rpm_limit,
                "window_seconds":   self._cfg.window_seconds,
            }

    # ── Internals ─────────────────────────────────────────────────

    def _get_provider(self, model: str) -> NvidiaNIMProvider:
        """Return a cached provider for the given model (or create one)."""
        with self._lock:
            p = self._providers.get(model)
            if p is None:
                cfg = ProviderConfig(
                    provider_id="nvidia_nim",
                    model=model,
                    api_key=self._cfg.api_key,
                    api_base=NvidiaNIMProvider.api_base,
                    temperature=self._cfg.temperature,
                    max_tokens=self._cfg.max_tokens,
                    timeout=self._cfg.timeout,
                )
                p = NvidiaNIMProvider(cfg)
                self._providers[model] = p
            return p

    def _throttle(self) -> None:
        """Block until a slot in the rate-limit window is free."""
        with self._lock:
            now = time.monotonic()
            # Drop timestamps older than the window.
            cutoff = now - self._cfg.window_seconds
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._cfg.rpm_limit:
                # Sleep until the oldest timestamp exits the window.
                wait = self._timestamps[0] + self._cfg.window_seconds - now + 0.05
                if wait > 0:
                    logger.info(
                        "[nim-pool] rate-limit reached (%d/%d rpm) — sleeping %.2fs",
                        len(self._timestamps), self._cfg.rpm_limit, wait,
                    )
                    self._total_throttled_secs += wait
                    # Release the lock while we sleep so other threads
                    # (if any) can also queue.
                else:
                    wait = 0.0
            else:
                wait = 0.0
            self._timestamps.append(time.monotonic())
        if wait > 0:
            time.sleep(wait)


# ── Convenience factory ───────────────────────────────────────────

def make_nim_pool(
    api_key: Optional[str] = None,
    *,
    rpm_limit: int = 35,
    model_overrides: Optional[Dict[str, str]] = None,
) -> NimPool:
    """Build a NimPool with optional per-role model overrides.

    Reads NVIDIA_API_KEY from the environment if api_key is None.
    """
    import os
    key = api_key or os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(
            "Nvidia NIM API key not found — pass api_key=... or set "
            "NVIDIA_API_KEY in the environment"
        )
    models = dict(DEFAULT_MODEL_FOR_ROLE)
    if model_overrides:
        models.update(model_overrides)
    return NimPool(NimPoolConfig(
        api_key=key,
        rpm_limit=rpm_limit,
        default_models=models,
    ))
