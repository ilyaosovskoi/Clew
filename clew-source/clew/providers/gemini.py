"""Google Gemini provider — via OpenAI-compatible endpoint."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class GeminiProvider(OpenAICompatProvider):
    provider_id: str = "gemini"
    label: str = "Google Gemini"
    default_model: str = "gemini-2.5-pro"
    api_base: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    env_var: str = "GOOGLE_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.VISION,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })