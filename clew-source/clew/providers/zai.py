"""Z.ai provider — GLM models via z.ai API (OpenAI-compatible)."""

from .openai_compat import OpenAICompatProvider
from .base import ProviderCapability


class ZAIProvider(OpenAICompatProvider):
    provider_id: str = "zai"
    label: str = "Z.ai"
    default_model: str = "glm-4-plus"
    api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    env_var: str = "ZAI_API_KEY"
    capabilities: frozenset = frozenset({
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.SYSTEM_PROMPT,
        ProviderCapability.SKILLS,
    })