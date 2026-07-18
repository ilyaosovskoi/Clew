"""OpenAI provider — GPT-4o, o1, etc."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class OpenAIProvider(OpenAICompatProvider):
    provider_id: str = "openai"
    label: str = "OpenAI"
    default_model: str = "gpt-4o"
    api_base: str = "https://api.openai.com/v1"
    env_var: str = "OPENAI_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.VISION,
        ProviderCapability.JSON_MODE,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })
