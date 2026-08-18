"""Generic OpenAI-compatible protocol runtime adapter (LM Studio, OpenRouter, Groq, DeepSeek, etc.)."""
from __future__ import annotations

from kitt.llm.domain import ModelDescriptor, ModelDiscoveryResult, ProviderHealth
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.openai_chat import OpenAIChatAdapter


class OpenAICompatibleAdapter(OpenAIChatAdapter):
    """Adapter for OpenAI-compatible providers ensuring strict /v1/models and /v1/chat/completions."""

    def __init__(self, default_base_url: str = "http://localhost:1234", provider_id: str = "openai-compatible"):
        self.default_base_url = default_base_url
        self.provider_id = provider_id

    def stream(self, request: LLMRequest) -> Iterator[str]:
        if not request.base_url:
            request.base_url = self.default_base_url
        return super().stream(request)

    def list_models(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ModelDiscoveryResult:
        base = base_url or self.default_base_url
        res = super().list_models(base, api_key, timeout)
        # Fix provider_id in returned descriptors
        for idx, m in enumerate(res.models):
            res.models[idx] = ModelDescriptor(
                provider_id=self.provider_id,
                id=m.id,
                name=m.name,
                context_window=m.context_window,
                max_output_tokens=m.max_output_tokens,
                supports_tools=m.supports_tools,
                supports_reasoning=m.supports_reasoning,
                supports_temperature=m.supports_temperature,
                supports_attachments=m.supports_attachments,
                raw_metadata=m.raw_metadata,
            )
        return res

    def health(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ProviderHealth:
        base = base_url or self.default_base_url
        return super().health(base, api_key, timeout)
