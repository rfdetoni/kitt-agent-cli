from __future__ import annotations

from typing import Optional


PROVIDER_PATTERNS = [
    {
        "id": "ollama",
        "label": "Ollama (Servidor Local/Remoto - /api/tags)",
        "protocol": "ollama-chat",
        "default_backend": "ollama",
        "default_url": "http://localhost:11434",
    },
    {
        "id": "openai",
        "label": "OpenAI-Compatível (vLLM / LM Studio / LocalAI / LiteLLM)",
        "protocol": "openai-chat-completions",
        "default_backend": "openai",
        "default_url": "http://localhost:8000/v1",
    },
    {
        "id": "anthropic",
        "label": "Anthropic-Compatível (Claude Proxy / Bedrock)",
        "protocol": "anthropic-messages",
        "default_backend": "anthropic",
        "default_url": "https://api.anthropic.com",
    },
    {
        "id": "gemini",
        "label": "Gemini-Compatível (Google AI Proxy)",
        "protocol": "gemini-generate-content",
        "default_backend": "gemini",
        "default_url": "https://generativelanguage.googleapis.com",
    },
]


class _ProvidersProperty:
    def __get__(self, instance, owner):
        if instance is None:
            return owner.default_providers
        custom_names = [
            cp["name"]
            for cp in instance.custom_providers
            if cp["name"] not in instance.default_providers
        ]
        ordered: list[str] = []
        for provider in instance.favorite_providers:
            if provider not in ordered:
                ordered.append(provider)
        for provider in instance.default_providers:
            if provider not in ordered:
                ordered.append(provider)
        for provider in custom_names:
            if provider not in ordered:
                ordered.append(provider)
        return tuple(ordered)



class ProviderPatternBehavior:
    """Provider protocol-template selection only."""

    @property
    def selected_pattern(self) -> dict:
        idx = max(0, min(self.pattern_index, len(PROVIDER_PATTERNS) - 1))
        return PROVIDER_PATTERNS[idx]



    def cycle_pattern(self, delta: int = 1) -> dict:
        self.pattern_index = (self.pattern_index + delta) % len(PROVIDER_PATTERNS)
        return self.selected_pattern



    def set_pattern_by_id(self, pattern_id: str) -> bool:
        pid = (pattern_id or "").strip().lower()
        for idx, pat in enumerate(PROVIDER_PATTERNS):
            if pat["id"] == pid or pid in pat["label"].lower():
                self.pattern_index = idx
                return True
        return False


