import unittest
from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient, UnsupportedProviderError
from kitt.ui.overlay_models import ModelSetupModel
from kitt.ui.app import KittUIApp

class TestAllProvidersSupport(unittest.TestCase):
    EXPECTED_PROVIDERS = [
        "ollama", "lmstudio", "openai", "anthropic", "gemini", "deepseek",
        "groq", "together", "mistral", "openrouter", "xai", "fireworks",
        "cohere", "azure", "antigravity"
    ]

    def test_overlay_model_contains_all_providers(self):
        for provider in self.EXPECTED_PROVIDERS:
            self.assertIn(provider, ModelSetupModel.providers)

    def test_provider_defaults_resolves_all_providers(self):
        for provider in self.EXPECTED_PROVIDERS:
            url, key = KittUIApp._provider_defaults(provider)
            self.assertTrue(url.startswith("http"), f"Base URL for {provider} must start with http")

    def test_llm_client_accepts_all_providers(self):
        for provider in self.EXPECTED_PROVIDERS:
            profile = ModelProfile(backend=provider, model="test-model", base_url="http://localhost:8000")
            client = LLMClient(profile)
            try:
                # chat_stream should attempt request or fail with connection error, NOT UnsupportedProviderError
                next(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))
            except UnsupportedProviderError:
                self.fail(f"LLMClient raised UnsupportedProviderError for provider: {provider}")
            except Exception:
                # Connection / network error is expected in mock environment
                pass

if __name__ == "__main__":
    unittest.main()
