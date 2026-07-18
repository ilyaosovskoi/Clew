"""
Clew v1.0.4 — Multi-Provider Auto-Router.

Automatically selects the best provider/model for each task
based on complexity analysis, cost constraints, and speed requirements.
Implements fallback chains for resilience.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TaskComplexity(str, Enum):
    TRIVIAL = "trivial"       # "fix typo", "what does this do?"
    SIMPLE = "simple"         # small edit, single-file change
    MODERATE = "moderate"     # multi-file edit, refactor
    COMPLEX = "complex"       # architecture, migration, multi-service
    EXPERT = "expert"         # novel algorithm, deep reasoning


# ── Model tiers: (provider_id, model, max_tokens, cost_category) ───

@dataclass
class ModelTier:
    provider_id: str
    model: str
    max_tokens: int
    cost_per_1k_in: float
    cost_per_1k_out: float
    speed: str  # "fast" | "medium" | "slow"
    capabilities: List[str]


# Default tier catalog — users can override via settings
# v1.1.4-fix (bug 5.1): previously only referenced 5 of the app's 15
# providers, plus a "local" provider_id that was never registered
# anywhere (has_provider("local") is always False — see the v1.0.6
# comment in route()). Anyone whose only configured provider was e.g.
# DeepSeek, Mistral, Fireworks, Cerebras, SambaNova, xAI, Together, or
# Z.ai would never get auto-routed to it, defeating the whole point of
# "don't make the person think about it". Every registered provider now
# has an entry somewhere in the tier catalog.
DEFAULT_TIERS = {
    TaskComplexity.TRIVIAL: [
        ModelTier("groq", "llama-3.1-8b-instant", 8192, 0.00005, 0.00008, "fast", ["chat"]),
        ModelTier("cerebras", "llama-4-scout-17b-16e-instruct", 8192, 0.0, 0.0, "fast", ["chat"]),
        ModelTier("ollama", "llama3.3", 4096, 0.0, 0.0, "medium", ["chat"]),
        ModelTier("lmstudio", "", 4096, 0.0, 0.0, "medium", ["chat"]),
        ModelTier("openrouter", "deepseek-chat", 8192, 0.00014, 0.00028, "fast", ["chat"]),
    ],
    TaskComplexity.SIMPLE: [
        ModelTier("groq", "llama-3.3-70b-versatile", 16384, 0.00059, 0.00079, "fast", ["chat", "tool_calling"]),
        ModelTier("deepseek", "deepseek-v4-pro", 16384, 0.00027, 0.0011, "fast", ["chat", "tool_calling"]),
        ModelTier("zai", "glm-5.1", 16384, 0.0002, 0.0008, "fast", ["chat", "tool_calling"]),
        ModelTier("sambanova", "Meta-Llama-4-Maverick-17B-128E-Instruct", 16384, 0.0, 0.0, "fast", ["chat", "tool_calling"]),
        ModelTier("openrouter", "deepseek-chat", 16384, 0.00014, 0.00028, "fast", ["chat", "tool_calling"]),
        ModelTier("openai", "gpt-4o-mini", 16384, 0.00015, 0.0006, "fast", ["chat", "tool_calling"]),
        ModelTier("together", "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", 16384, 0.0002, 0.0006, "fast", ["chat", "tool_calling"]),
        ModelTier("fireworks", "accounts/fireworks/models/llama4-maverick-instruct-basic", 16384, 0.00022, 0.00088, "fast", ["chat", "tool_calling"]),
        ModelTier("mistral", "mistral-large-latest", 16384, 0.002, 0.006, "medium", ["chat", "tool_calling"]),
    ],
    TaskComplexity.MODERATE: [
        ModelTier("anthropic", "claude-3-5-sonnet-20241022", 8192, 0.003, 0.015, "medium", ["chat", "tool_calling", "vision"]),
        ModelTier("openai", "gpt-4o", 8192, 0.0025, 0.01, "medium", ["chat", "tool_calling", "vision"]),
        ModelTier("gemini", "gemini-3.1-pro", 8192, 0.00125, 0.005, "medium", ["chat", "tool_calling", "vision"]),
        ModelTier("xai", "grok-4.3", 8192, 0.002, 0.01, "medium", ["chat", "tool_calling"]),
        ModelTier("openrouter", "anthropic/claude-3.5-sonnet", 8192, 0.003, 0.015, "medium", ["chat", "tool_calling"]),
    ],
    TaskComplexity.COMPLEX: [
        ModelTier("anthropic", "claude-sonnet-4-20250514", 16384, 0.003, 0.015, "medium", ["chat", "tool_calling", "vision"]),
        ModelTier("openai", "gpt-4o", 16384, 0.0025, 0.01, "medium", ["chat", "tool_calling", "vision"]),
        ModelTier("gemini", "gemini-3.1-pro", 16384, 0.00125, 0.005, "medium", ["chat", "tool_calling", "vision"]),
        ModelTier("anthropic", "claude-3-5-sonnet-20241022", 16384, 0.003, 0.015, "medium", ["chat", "tool_calling"]),
    ],
    TaskComplexity.EXPERT: [
        ModelTier("anthropic", "claude-opus-4-20250514", 16384, 0.015, 0.075, "slow", ["chat", "tool_calling", "vision"]),
        ModelTier("openai", "o1", 32768, 0.01, 0.04, "slow", ["chat"]),
        ModelTier("anthropic", "claude-3-opus-20240229", 4096, 0.015, 0.075, "slow", ["chat", "tool_calling"]),
    ],
}


class AutoRouter:
    """
    Analyzes the user's prompt and selects the optimal provider/model.

    Decision factors:
    1. Task complexity (estimated from prompt length, keywords, presence of code)
    2. Required capabilities (vision, tool_calling)
    3. Cost ceiling (per-request budget from user)
    4. Provider availability (API key present, not rate-limited)
    5. Fallback chain (if primary fails, try next)
    """

    def __init__(self):
        self._tiers = DEFAULT_TIERS
        self._per_request_budget: Optional[float] = None  # max $ per request
        self._force_provider: Optional[str] = None        # user override
        self._force_model: Optional[str] = None
        self._provider_available: Dict[str, bool] = {}    # track which providers work
        self._provider_available_ts: Dict[str, float] = {}  # timestamp of last mark
        self._provider_cache_ttl: float = 300.0  # 5 minutes (M-AUTO-2)

    # ── Configuration ──────────────────────────────────────────────

    def set_budget(self, max_usd: float) -> None:
        self._per_request_budget = max_usd

    def set_force_provider(self, provider_id: str) -> None:
        self._force_provider = provider_id

    def set_force_model(self, model: str) -> None:
        self._force_model = model

    def clear_overrides(self) -> None:
        self._force_provider = None
        self._force_model = None

    def mark_provider_available(self, provider_id: str, available: bool) -> None:
        import time as _time
        self._provider_available[provider_id] = available
        self._provider_available_ts[provider_id] = _time.time()

    # ── Routing ────────────────────────────────────────────────────

    def classify_task(self, prompt: str) -> TaskComplexity:
        """Classify task complexity. Returns the complexity enum."""
        complexity, _ = self._classify_task_impl(prompt)
        return complexity

    def classify_explain(self, prompt: str) -> Dict[str, Any]:
        """
        Classify task and return a human-readable explanation.
        Returns {complexity, explanation, signals} — designed for UI display.
        """
        complexity, signals = self._classify_task_impl(prompt)

        DESCRIPTIONS = {
            TaskComplexity.TRIVIAL: "Short question — a fast, cheap model is sufficient.",
            TaskComplexity.SIMPLE: "Single-file task — a capable mid-range model handles this.",
            TaskComplexity.MODERATE: "Multi-step feature — needs a strong model with tool support.",
            TaskComplexity.COMPLEX: "Cross-file work — routing to a top-tier reasoning model.",
            TaskComplexity.EXPERT: "Deep reasoning task — using the most powerful model available.",
        }

        return {
            "complexity": complexity.value,
            "explanation": DESCRIPTIONS.get(complexity, complexity.value),
            "signals": signals,
        }

    def _classify_task_impl(self, prompt: str) -> Tuple[TaskComplexity, List[str]]:
        """
        Internal: classify and return (complexity, signal_list).
        """
        text = prompt.lower()
        lines = prompt.split("\n")
        word_count = len(text.split())
        has_code = "```" in prompt or any(l.strip().startswith(("#", "//", "import ", "from ")) for l in lines[:5])

        # Count file references
        file_refs = len(re.findall(r"[\w./\-]+\.\w{1,5}", prompt))

        # Expert signals
        expert_keywords = [
            "architecture", "migration", "redesign", "rewrite from scratch",
            "optimization", "algorithm", "novel", "research", "prove",
            "complex reasoning", "multi-service", "distributed",
        ]
        expert_score = sum(1 for kw in expert_keywords if kw in text)

        # Complex signals
        complex_keywords = [
            "refactor", "restructure", "multi-file", "across files",
            "test suite", "all tests", "integration", "api design",
        ]
        complex_score = sum(1 for kw in complex_keywords if kw in text)

        # Moderate signals
        moderate_keywords = [
            "feature", "add", "implement", "create", "build",
            "function", "class", "component", "endpoint",
        ]
        moderate_score = sum(1 for kw in moderate_keywords if kw in text)

        # Decision tree
        if expert_score >= 2 or (word_count > 500 and file_refs > 5):
            complexity = TaskComplexity.EXPERT
        elif complex_score >= 2 or file_refs > 3 or (word_count > 200 and has_code):
            complexity = TaskComplexity.COMPLEX
        elif moderate_score >= 1 or (word_count > 50 and has_code):
            complexity = TaskComplexity.MODERATE
        elif word_count > 20 or has_code:
            complexity = TaskComplexity.SIMPLE
        else:
            complexity = TaskComplexity.TRIVIAL

        # Build explanation of why this complexity was chosen
        signals = []
        if has_code:
            signals.append("contains code")
        if file_refs > 0:
            signals.append(f"{file_refs} file reference(s)")
        if word_count > 200:
            signals.append(f"long prompt ({word_count} words)")
        elif word_count > 50:
            signals.append(f"medium prompt ({word_count} words)")
        if expert_score > 0:
            matched = [kw for kw in expert_keywords if kw in text]
            signals.append(f"expert keyword(s): {', '.join(matched)}")
        if complex_score > 0:
            matched = [kw for kw in complex_keywords if kw in text]
            signals.append(f"complex keyword(s): {', '.join(matched)}")
        if moderate_score > 0:
            matched = [kw for kw in moderate_keywords if kw in text]
            signals.append(f"action keyword(s): {', '.join(matched)}")

        return complexity, signals

    def route(
        self,
        prompt: str,
        required_capabilities: Optional[List[str]] = None,
        configured_providers: Optional[set] = None,
    ) -> Dict[str, Any]:
        """
        Select the best provider/model for this prompt.
        Returns {provider_id, model, max_tokens, complexity, cost_estimate, fallbacks, reasoning}.

        v1.1.4-fix (bug 5.1): ``configured_providers`` — the set of
        provider ids that actually have an API key / config saved right
        now (from ProviderRegistry.list_providers()) — used to be
        ignored entirely. ``mark_provider_available()`` exists but was
        never called anywhere in the app, so ``_is_available()`` always
        returned True and the router would happily pick a provider the
        person never configured, producing a confusing auth error on
        the very first message. Passing this set makes routing actually
        respect what's set up.
        """
        # If user forced a specific provider/model
        if self._force_provider:
            return {
                "provider_id": self._force_provider,
                "model": self._force_model or "",
                "max_tokens": 8192,
                "complexity": "forced",
                "cost_estimate": 0.0,
                "fallbacks": [],
                "reasoning": f"Forced to {self._force_provider}",
                "speed": "unknown",
            }

        complexity = self._classify_task_impl(prompt)[0]
        candidates = list(self._tiers.get(complexity, []))

        # Filter by required capabilities
        if required_capabilities:
            candidates = [
                t for t in candidates
                if all(cap in t.capabilities for cap in required_capabilities)
            ]

        # Filter by provider availability (with TTL — M-AUTO-2): a
        # provider that just failed a real request is skipped for a
        # while even if it's configured.
        import time as _time
        now = _time.time()
        def _is_available(pid: str) -> bool:
            ts = self._provider_available_ts.get(pid)
            if ts is None:
                return True  # never marked — assume available
            if now - ts > self._provider_cache_ttl:
                return True  # TTL expired — retry
            return self._provider_available.get(pid, True)
        candidates = [t for t in candidates if _is_available(t.provider_id)]

        # v1.1.4-fix: filter by whether the provider is actually
        # configured (has a key / is a no-key local provider). Skipped
        # only when the caller explicitly passes None, so existing
        # tests / other callers keep working unchanged.
        if configured_providers is not None:
            candidates = [t for t in candidates if t.provider_id in configured_providers]

        # Filter by budget
        if self._per_request_budget is not None:
            candidates = [
                t for t in candidates
                if self._estimate_cost(t, prompt) <= self._per_request_budget
            ]

        if not candidates:
            # v1.1.4-fix: search the *entire* tier catalog (every
            # complexity level), not just SIMPLE — this is what makes
            # "as long as you've configured one provider, it always
            # works" actually true, regardless of which tier that
            # provider happens to live in.
            logger.warning(f"[router] no candidates for {complexity.value}, searching full catalog")
            seen_pids: set = set()
            all_candidates: List[ModelTier] = []
            for tier_list in DEFAULT_TIERS.values():
                for t in tier_list:
                    if t.provider_id not in seen_pids:
                        seen_pids.add(t.provider_id)
                        all_candidates.append(t)
            candidates = [
                t for t in all_candidates
                if _is_available(t.provider_id)
                and (configured_providers is None or t.provider_id in configured_providers)
            ]

        if not candidates:
            logger.warning("[router] no providers available at all")
            return {
                "provider_id": "",
                "model": "",
                "max_tokens": 4096,
                "complexity": complexity.value,
                "cost_estimate": 0.0,
                "fallbacks": [],
                "reasoning": (
                    "No configured providers available. Open Settings → "
                    "Providers and add an API key (or use a local model "
                    "with Ollama / LM Studio — no key needed)."
                ),
                "speed": "unknown",
            }

        # Pick the first (best) candidate
        primary = candidates[0]
        fallbacks = [
            {"provider_id": t.provider_id, "model": t.model}
            for t in candidates[1:4]
        ]

        cost_est = self._estimate_cost(primary, prompt)

        return {
            "provider_id": primary.provider_id,
            "model": primary.model,
            "max_tokens": primary.max_tokens,
            "complexity": complexity.value,
            "cost_estimate": round(cost_est, 4),
            "fallbacks": fallbacks,
            "reasoning": (
                f"Classified as {complexity.value}. "
                f"Routed to {primary.provider_id}/{primary.model} "
                f"({primary.speed}, ~${cost_est:.4f} est.)"
            ),
            "speed": primary.speed,
        }

    def _estimate_cost(self, tier: ModelTier, prompt: str) -> float:
        """Rough cost estimate for a single request."""
        approx_tokens_in = len(prompt) // 4
        # Assume output is roughly 2x input for code tasks
        approx_tokens_out = approx_tokens_in * 2
        return (approx_tokens_in * tier.cost_per_1k_in + approx_tokens_out * tier.cost_per_1k_out) / 1000

    # ── Info ───────────────────────────────────────────────────────

    def get_tier_info(self) -> Dict[str, Any]:
        """Return the current routing configuration for the UI."""
        return {
            complexity.value: [
                {
                    "provider_id": t.provider_id,
                    "model": t.model,
                    "speed": t.speed,
                    "est_cost_in": t.cost_per_1k_in,
                    "est_cost_out": t.cost_per_1k_out,
                }
                for t in tiers
            ]
            for complexity, tiers in self._tiers.items()
        }