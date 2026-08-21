"""Generic OpenAI-compatible protocol adapter."""
from __future__ import annotations

from typing import Iterator, Optional

from kitt.llm.domain import ModelDescriptor, ModelDiscoveryResult, ProviderHealth
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.openai_chat import OpenAIChatAdapter


class OpenAICompatibleAdapter(OpenAIChatAdapter):
    """Adapter for LM Studio, OpenRouter, Groq, DeepSeek and compatible endpoints."""

    def __init__(
        self,
        default_base_url: str = "http://localhost:1234",
        provider_id: str = "openai-compatible",
    ):
        self.default_base_url = default_base_url
        self.provider_id = provider_id

    def stream(self, request: LLMRequest) -> Iterator[str]:
        if not request.base_url:
            request.base_url = self.default_base_url
        return super().stream(request)

    def list_models(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
    ) -> ModelDiscoveryResult:
        base = base_url or self.default_base_url
        result = super().list_models(base, api_key, timeout)
        for idx, model in enumerate(result.models):
            result.models[idx] = ModelDescriptor(
                provider_id=self.provider_id,
                id=model.id,
                name=model.name,
                context_window=model.context_window,
                max_output_tokens=model.max_output_tokens,
                supports_tools=model.supports_tools,
                supports_reasoning=model.supports_reasoning,
                supports_temperature=model.supports_temperature,
                supports_attachments=model.supports_attachments,
                raw_metadata=model.raw_metadata,
            )
        return result

    def health(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
    ) -> ProviderHealth:
        return super().health(base_url or self.default_base_url, api_key, timeout)
