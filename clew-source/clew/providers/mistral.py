"""Mistral AI provider — Mistral Large, Codestral, etc."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class MistralProvider(OpenAICompatProvider):
    provider_id: str = "mistral"
    label: str = "Mistral"
    default_model: str = "mistral-large-latest"
    api_base: str = "https://api.mistral.ai/v1"
    env_var: str = "MISTRAL_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })