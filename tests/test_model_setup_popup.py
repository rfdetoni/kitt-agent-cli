import asyncio
import tempfile
import unittest
from pathlib import Path
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.app import KittUIApp
from kitt.ui.overlay_models import ModelSetupModel


class TestModelSetupPopup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = KittRuntime.build(self.temp.name, RuntimeConfig(history_enabled=True, persistence_enabled=True))
        self.input_cm = create_pipe_input()
        self.pipe = self.input_cm.__enter__()
        self.ui = KittUIApp(self.runtime, "tui", input=self.pipe, output=DummyOutput(), no_animation=True)
        self.ui.build_application()
        self.task = asyncio.create_task(self.ui.run_async())
        await asyncio.sleep(0.05)

    async def asyncTearDown(self):
        self.ui.request_exit()
        await asyncio.wait_for(self.task, 2)
        self.runtime.close()
        self.input_cm.__exit__(None, None, None)
        self.temp.cleanup()

    def test_model_setup_model_favorites_and_popup_entries(self):
        model = ModelSetupModel()
        self.assertIn("ollama", model.favorite_providers)
        self.assertIn("openai", model.favorite_providers)
        
        # Toggle favorite
        is_fav = model.toggle_favorite("anthropic")
        self.assertFalse(is_fav)  # removed
        self.assertNotIn("anthropic", model.favorite_providers)
        
        is_fav = model.toggle_favorite("anthropic")
        self.assertTrue(is_fav)  # added back
        self.assertIn("anthropic", model.favorite_providers)

        # Custom provider
        model.add_custom_provider("vllm-local", "http://localhost:8000/v1")
        self.assertIn("vllm-local", model.providers)
        self.assertIn("vllm-local", model.favorite_providers)

        entries = model.get_popup_entries()
        kinds = [e["kind"] for e in entries]
        self.assertIn("header", kinds)
        self.assertIn("provider", kinds)
        self.assertIn("action", kinds)

        fav_entries = [e for e in entries if e.get("is_favorite")]
        self.assertTrue(any(e["name"] == "ollama" for e in fav_entries))
        self.assertTrue(any(e["name"] == "vllm-local" for e in fav_entries))

    async def test_provider_popup_overlay_and_selection(self):
        # Open model setup overlay
        await self.ui._open_model_setup_overlay()
        self.assertEqual(self.ui.state.active_overlay, "model_setup")
        
        # Open provider popup dropdown
        self.ui._open_provider_popup_overlay()
        self.assertEqual(self.ui.state.active_overlay, "provider_popup")
        
        popup_text = self.ui._provider_popup_text()
        self.assertIn("FAVORITOS", popup_text)
        self.assertIn("★", popup_text)
        self.assertIn("Adicionar Novo Provedor", popup_text)

        # Move selection and toggle favorite
        self.ui.model_setup_model.move_popup_selection(1)
        entry = self.ui.model_setup_model.get_selected_popup_entry()
        self.assertIsNotNone(entry)

    async def test_add_custom_provider_overlay_workflow(self):
        self.ui._open_add_provider_overlay()
        self.assertEqual(self.ui.state.active_overlay, "add_provider")
        
        self.ui.add_provider_name_buffer.text = "custom-gateway"
        self.ui.add_provider_url_buffer.text = "http://gateway.lan:11434"
        
        res = self.ui._accept_add_provider(self.ui.add_provider_url_buffer)
        self.assertTrue(res)
        self.assertIn("custom-gateway", self.ui.model_setup_model.providers)
        self.assertIn("custom-gateway", self.ui.model_setup_model.favorite_providers)

    async def test_switching_provider_updates_model_list_to_latest_models(self):
        await self.ui._open_model_setup_overlay()
        
        # Switch to anthropic
        await self.ui._prepare_model_setup(provider="anthropic")
        self.assertEqual(self.ui.model_setup_model.selected_provider, "anthropic")
        self.assertIn("claude-3-7-sonnet-latest", self.ui.model_setup_model.models)
        self.assertIn("claude-3-5-sonnet-latest", self.ui.model_setup_model.models)

        # Switch to openai
        await self.ui._prepare_model_setup(provider="openai")
        self.assertEqual(self.ui.model_setup_model.selected_provider, "openai")
        self.assertIn("gpt-4.5-preview", self.ui.model_setup_model.models)
        self.assertIn("gpt-4o", self.ui.model_setup_model.models)
        self.assertIn("o3-mini", self.ui.model_setup_model.models)

        # Switch to deepseek
        await self.ui._prepare_model_setup(provider="deepseek")
        self.assertEqual(self.ui.model_setup_model.selected_provider, "deepseek")
        self.assertIn("deepseek-r1", self.ui.model_setup_model.models)
        self.assertIn("deepseek-v3", self.ui.model_setup_model.models)

        # Switch to gemini
        await self.ui._prepare_model_setup(provider="gemini")
        self.assertEqual(self.ui.model_setup_model.selected_provider, "gemini")
        self.assertIn("gemini-2.5-pro", self.ui.model_setup_model.models)
        self.assertIn("gemini-2.5-flash", self.ui.model_setup_model.models)

    async def test_dynamic_get_model_discovery_from_api(self):
        from unittest.mock import patch, MagicMock
        import io
        import json
        from kitt.router.model_selector import fetch_provider_models

        # Mock OpenAI /v1/models response
        fake_openai_resp = json.dumps({"data": [{"id": "gpt-custom-finetuned-v9"}, {"id": "gpt-internal-test"}]}).encode("utf-8")
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = io.BytesIO(fake_openai_resp)

        with patch("urllib.request.urlopen", return_value=mock_cm):
            models = fetch_provider_models("openai", "https://api.openai.com", "sk-test-key")
            self.assertEqual(models, ["gpt-custom-finetuned-v9", "gpt-internal-test"])

        # Mock Ollama /api/tags response
        fake_ollama_resp = json.dumps({"models": [{"name": "deepseek-coder-v2:16b"}, {"name": "starlette:latest"}]}).encode("utf-8")
        mock_cm_ollama = MagicMock()
        mock_cm_ollama.__enter__.return_value = io.BytesIO(fake_ollama_resp)

        with patch("urllib.request.urlopen", return_value=mock_cm_ollama):
            models = fetch_provider_models("ollama", "http://localhost:11434")
            self.assertEqual(models, ["deepseek-coder-v2:16b", "starlette:latest"])


if __name__ == "__main__":
    unittest.main()
