from __future__ import annotations

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

