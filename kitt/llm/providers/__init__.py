"""Provider runtime adapters package."""
from dataclasses import replace

from kitt.llm.providers.base import LLMRequest, ProviderAdapter
from kitt.llm.providers.ollama import OllamaAdapter
from kitt.llm.providers.openai_chat import OpenAIChatAdapter
from kitt.llm.providers.openai_responses import OpenAIResponsesAdapter
from kitt.llm.providers.anthropic import AnthropicAdapter
from kitt.llm.providers.gemini import GeminiAdapter
from kitt.llm.providers.openai_compatible import OpenAICompatibleAdapter
from kitt.llm.providers.kitt_reverse_proxy import KittReverseProxyAdapter as _NativeKittReverseProxyAdapter


_REASONING_HEADER = "x-kitt-reasoning-effort"


class KittReverseProxyAdapter(_NativeKittReverseProxyAdapter):
    """Reverse-proxy adapter that leaves reasoning entirely under WebChat control."""

    def stream(self, request: LLMRequest):
        if any(name.lower() == _REASONING_HEADER for name in request.extra_headers):
            request = replace(
                request,
                extra_headers={
                    name: value
                    for name, value in request.extra_headers.items()
                    if name.lower() != _REASONING_HEADER
                },
            )
        yield from super().stream(request)


__all__ = [
    "LLMRequest",
    "ProviderAdapter",
    "OllamaAdapter",
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "OpenAICompatibleAdapter",
    "KittReverseProxyAdapter",
]
