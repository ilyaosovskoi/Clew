"""DeepSeek provider — DeepSeek-V3, DeepSeek-R1, etc."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class DeepSeekProvider(OpenAICompatProvider):
    provider_id: str = "deepseek"
    label: str = "DeepSeek"
    default_model: str = "deepseek-chat"
    api_base: str = "https://api.deepseek.com/v1"
    env_var: str = "DEEPSEEK_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })