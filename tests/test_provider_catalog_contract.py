"""Contract tests for ProviderCatalogService, Models.dev parsing, and offline caching."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kitt.llm.catalog import BUILTIN_PROVIDERS, ProviderCatalogService
from kitt.llm.domain import ModelDescriptor, ProviderDescriptor


class TestProviderCatalogContract(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_builtin_providers_loaded_by_default(self):
        catalog = ProviderCatalogService(cache_dir=str(self.cache_dir))
        providers = {p.id: p for p in catalog.providers()}
        self.assertIn("ollama", providers)
        self.assertIn("lmstudio", providers)
        self.assertIn("openai", providers)
        self.assertIn("anthropic", providers)
        self.assertIn("gemini", providers)
        self.assertEqual(providers["ollama"].protocol, "ollama-chat")
        self.assertEqual(providers["gemini"].protocol, "gemini-generate-content")

    def test_parse_models_dev_payload_and_atomic_cache(self):
        catalog = ProviderCatalogService(cache_dir=str(self.cache_dir))
        mock_payload = {
            "version": 1,
            "providers": {
                "custom-corp": {
                    "name": "Corporate AI",
                    "protocol": "openai-chat-completions",
                    "baseUrl": "https://llm.corp.local/v1",
                    "envVars": ["CORP_KEY"],
                    "models": {
                        "corp-coder-v1": {
                            "name": "Corporate Coder v1",
                            "contextWindow": 32768,
                            "maxOutputTokens": 4096,
                            "supportsTools": True,
                            "supportsReasoning": True,
                            "cost_input": 0.001,
                            "cost_output": 0.002,
                            "future_unknown_field": "tolerated"
                        }
                    }
                }
            }
        }

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            success = catalog.refresh(force=True)
            self.assertTrue(success)

        # Verify provider and model descriptors
        provider = catalog.provider("custom-corp")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "Corporate AI")
        self.assertEqual(provider.base_url, "https://llm.corp.local/v1")

        models = catalog.models("custom-corp")
        self.assertEqual(len(models), 1)
        m = models[0]
        self.assertEqual(m.id, "corp-coder-v1")
        self.assertEqual(m.context_window, 32768)
        self.assertTrue(m.supports_tools)
        self.assertTrue(m.supports_reasoning)
        self.assertEqual(m.raw_metadata["future_unknown_field"], "tolerated")

        # Verify offline cache file was created atomically
        cache_file = self.cache_dir / "models.dev.json"
        self.assertTrue(cache_file.exists())

        # Test offline recovery from cache in new catalog instance
        catalog2 = ProviderCatalogService(cache_dir=str(self.cache_dir))
        self.assertIsNotNone(catalog2.provider("custom-corp"))
        self.assertEqual(len(catalog2.models("custom-corp")), 1)

    def test_failed_refresh_preserves_existing_cache(self):
        catalog = ProviderCatalogService(cache_dir=str(self.cache_dir))
        # Populate initial cache
        cache_file = self.cache_dir / "models.dev.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        initial_data = {
            "providers": {
                "cached-prov": {
                    "name": "Cached Provider",
                    "models": ["model-a"]
                }
            }
        }
        cache_file.write_text(json.dumps(initial_data), encoding="utf-8")

        catalog = ProviderCatalogService(cache_dir=str(self.cache_dir))
        self.assertIsNotNone(catalog.provider("cached-prov"))

        # Simulate network failure on refresh
        with patch("urllib.request.urlopen", side_effect=Exception("Network down")):
            success = catalog.refresh(force=True)
            self.assertFalse(success)

        # Existing cached provider is still present
        self.assertIsNotNone(catalog.provider("cached-prov"))


if __name__ == "__main__":
    unittest.main()
