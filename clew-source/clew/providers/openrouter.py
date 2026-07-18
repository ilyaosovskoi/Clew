"""OpenRouter provider — routes to any model (Claude, Gemini, Mistral, …)."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class OpenRouterProvider(OpenAICompatProvider):
    provider_id: str = "openrouter"
    label: str = "OpenRouter"
    default_model: str = "anthropic/claude-3.5-sonnet"
    api_base: str = "https://openrouter.ai/api/v1"
    env_var: str = "OPENROUTER_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })

    def _headers(self):
        # OpenRouter wants extra headers for attribution
        headers = super()._headers()
        headers["HTTP-Referer"] = "https://clew.app"
        headers["X-Title"] = "Clew"
        return headers
