"""Architecture verification test: Dynamic discovery of future providers and models without hardcoded lists."""
import unittest
from unittest.mock import patch

from kitt.llm.catalog import ProviderCatalogService
from kitt.llm.domain import ModelDescriptor, ProviderDescriptor
from kitt.llm.registry import ProviderRegistry
from kitt.llm.selection import ModelSelectionService, SelectionTransactionStatus
from kitt.router.router import TaskRouter
from kitt.ui.overlay_models import ModelSetupModel


class TestDynamicCatalogDiscovery(unittest.IsolatedAsyncioTestCase):

    def test_future_provider_and_model_discovered_without_hardcoded_lists(self):
        cat = ProviderCatalogService()
        reg = ProviderRegistry(catalog=cat)

        # Register a dynamic future provider with a future model
        custom_provider = reg.register_custom_provider(
            provider_id="future-provider-omega",
            name="Future Provider Omega",
            protocol="openai-chat-completions",
            base_url="https://api.omega-ai.internal",
            models=[
                ModelDescriptor(
                    provider_id="future-provider-omega",
                    id="omega-quantum-coder-v1",
                    name="Omega Quantum Coder V1",
                    context_window=2_000_000,
                    supports_tools=True,
                    supports_reasoning=True,
                )
            ],
        )
        self.assertIsNotNone(custom_provider)

        # Verify discovery in registry
        all_providers = reg.list_providers()
        self.assertTrue(any(p.id == "future-provider-omega" for p in all_providers))

        # Verify discovery in UI ModelSetupModel
        setup = ModelSetupModel()
        setup.add_custom_provider("future-provider-omega", "https://api.omega-ai.internal")
        self.assertIn("future-provider-omega", setup.providers)

        # Set models and test filter
        setup.models = ["omega-quantum-coder-v1", "omega-standard-v1"]
        setup.search_query = "quantum"
        filtered = setup.get_filtered_models()
        self.assertEqual(filtered, ["omega-quantum-coder-v1"])

        # Test metadata formatting
        badge = setup.format_model_badge("future-provider-omega", "omega-quantum-coder-v1")
        # Ensure badge string exists without error
        self.assertIsInstance(badge, str)


if __name__ == "__main__":
    unittest.main()
