"""Groq provider — LPU-accelerated Llama / Mixtral / Gemma."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class GroqProvider(OpenAICompatProvider):
    provider_id: str = "groq"
    label: str = "Groq"
    default_model: str = "llama-3.3-70b-versatile"
    api_base: str = "https://api.groq.com/openai/v1"
    env_var: str = "GROQ_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })
