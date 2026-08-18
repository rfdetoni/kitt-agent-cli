"""Unit tests for transactional ModelSelectionService (OpenCode parity)."""
import unittest
from unittest.mock import AsyncMock, patch

from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.llm.auth import ProviderAuthService
from kitt.llm.registry import ProviderRegistry
from kitt.llm.selection import (
    ModelSelectionResult,
    ModelSelectionService,
    SelectionTransactionStatus,
)
from kitt.router.router import TaskRouter


class TestModelSelectionService(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.auth_service = ProviderAuthService()
        self.registry = ProviderRegistry(auth_service=self.auth_service)
        self.router = TaskRouter(root_dir="/tmp/kitt_test_selection")
        self.router.config = RouterConfig(
            profiles={"principal": ModelProfile(backend="ollama", model="qwen")},
            routing={"execute": "principal"},
        )
        self.service = ModelSelectionService(
            workspace_path="/tmp/kitt_test_selection",
            registry=self.registry,
            router=self.router,
        )

    async def test_select_local_provider_succeeds_without_auth_interaction(self):
        result = await self.service.select(
            role="principal",
            provider_id="ollama",
            model_id="deepseek-r1:8b",
            base_url="http://localhost:11434",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.status, SelectionTransactionStatus.SUCCESS)
        self.assertEqual(self.router.config.profiles["principal"].model, "deepseek-r1:8b")
        self.assertEqual(self.router.config.profiles["principal"].backend, "ollama")

    async def test_select_cloud_provider_triggers_auth_and_persists_on_success(self):
        self.auth_service.logout("anthropic")

        async def fake_auth_handler(provider_id: str):
            self.assertEqual(provider_id, "anthropic")
            return "sk-ant-test-token-12345"

        result = await self.service.select(
            role="principal",
            provider_id="anthropic",
            model_id="claude-3-7-sonnet",
            auth_interaction=fake_auth_handler,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.status, SelectionTransactionStatus.SUCCESS)
        self.assertEqual(self.router.config.profiles["principal"].backend, "anthropic")
        self.assertEqual(self.router.config.profiles["principal"].model, "claude-3-7-sonnet")
        
        # Verify credential was saved
        key = self.auth_service.resolve(None, "anthropic")
        self.assertEqual(key, "sk-ant-test-token-12345")

    async def test_select_cloud_provider_preserves_old_model_on_auth_cancel(self):
        self.auth_service.logout("openai")

        async def cancel_auth_handler(provider_id: str):
            return None  # Cancelled

        result = await self.service.select(
            role="principal",
            provider_id="openai",
            model_id="gpt-4.5-preview",
            auth_interaction=cancel_auth_handler,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, SelectionTransactionStatus.CANCELLED)
        # Old model and backend remain unchanged
        self.assertEqual(self.router.config.profiles["principal"].backend, "ollama")
        self.assertEqual(self.router.config.profiles["principal"].model, "qwen")

    async def test_select_cloud_provider_preserves_old_model_on_auth_failure(self):
        self.auth_service.logout("deepseek")

        async def failing_auth_handler(provider_id: str):
            raise RuntimeError("Network error reaching auth endpoint")

        result = await self.service.select(
            role="principal",
            provider_id="deepseek",
            model_id="deepseek-v3",
            auth_interaction=failing_auth_handler,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, SelectionTransactionStatus.FAILED)
        # Old model remains untouched
        self.assertEqual(self.router.config.profiles["principal"].backend, "ollama")
        self.assertEqual(self.router.config.profiles["principal"].model, "qwen")


if __name__ == "__main__":
    unittest.main()
