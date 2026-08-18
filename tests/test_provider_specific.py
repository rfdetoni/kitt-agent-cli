"""Provider-specific contract and behavior tests (Ollama, LM Studio, OpenAI, Anthropic, Gemini)."""
import json
import unittest
from unittest.mock import MagicMock, patch

from kitt.llm.providers.anthropic import AnthropicAdapter
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.gemini import GeminiAdapter
from kitt.llm.providers.ollama import OllamaAdapter
from kitt.llm.providers.openai_chat import OpenAIChatAdapter
from kitt.llm.providers.openai_compatible import OpenAICompatibleAdapter
from kitt.llm.providers.openai_responses import OpenAIResponsesAdapter
from kitt.llm.registry import ProviderRegistry


class TestProviderSpecificBehaviors(unittest.TestCase):

    def test_ollama_endpoints_and_streaming(self):
        adapter = OllamaAdapter()
        req = LLMRequest(
            model="qwen2.5-coder:1.5b",
            messages=[{"role": "user", "content": "olá"}],
            base_url="http://localhost:11434",
        )

        mock_lines = [
            json.dumps({"message": {"thinking": "pensando...", "content": ""}}).encode("utf-8"),
            json.dumps({"message": {"thinking": "", "content": "Olá!"}}).encode("utf-8"),
        ]

        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(mock_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            chunks = list(adapter.stream(req))
            self.assertIn("<think>", chunks)
            self.assertIn("pensando...", chunks)
            self.assertIn("Olá!", chunks)

            # Assert correct URL was called
            called_req = mock_urlopen.call_args[0][0]
            self.assertEqual(called_req.full_url, "http://localhost:11434/api/chat")

    def test_lmstudio_uses_v1_models_never_api_tags(self):
        adapter = OpenAICompatibleAdapter(default_base_url="http://localhost:1234", provider_id="lmstudio")
        mock_models_data = {
            "data": [
                {"id": "qwen2.5-coder-7b", "object": "model"}
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_models_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            res = adapter.list_models(base_url="http://localhost:1234")
            self.assertEqual(len(res.models), 1)
            self.assertEqual(res.models[0].id, "qwen2.5-coder-7b")

            called_req = mock_urlopen.call_args[0][0]
            self.assertIn("/v1/models", called_req.full_url)
            self.assertNotIn("/api/tags", called_req.full_url)

    def test_anthropic_headers_and_payload(self):
        adapter = AnthropicAdapter()
        req = LLMRequest(
            model="claude-3-5-sonnet-20241022",
            messages=[{"role": "user", "content": "Hello"}],
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            system_prompt="You are helpful.",
        )

        mock_lines = [
            b'data: {"type": "content_block_delta", "delta": {"text": "Hi!"}}\n',
            b'data: [DONE]\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(mock_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            chunks = list(adapter.stream(req))
            self.assertEqual("".join(chunks), "Hi!")

            called_req = mock_urlopen.call_args[0][0]
            self.assertEqual(called_req.full_url, "https://api.anthropic.com/v1/messages")
            self.assertEqual(called_req.headers.get("X-api-key"), "sk-ant-test")
            self.assertEqual(called_req.headers.get("Anthropic-version"), "2023-06-01")

    def test_gemini_native_endpoint_and_headers(self):
        adapter = GeminiAdapter()
        req = LLMRequest(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": "Ping"}],
            api_key="gemini-secret-key",
            base_url="https://generativelanguage.googleapis.com",
        )

        mock_lines = [
            b'data: {"candidates": [{"content": {"parts": [{"text": "Pong"}]}}]}\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(mock_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            chunks = list(adapter.stream(req))
            self.assertEqual("".join(chunks), "Pong")

            called_req = mock_urlopen.call_args[0][0]
            self.assertIn("/v1beta/models/gemini-2.0-flash:streamGenerateContent?alt=sse", called_req.full_url)
            self.assertEqual(called_req.headers.get("X-goog-api-key"), "gemini-secret-key")
            self.assertNotIn("/v1/chat/completions", called_req.full_url)

    def test_openai_responses_api_format(self):
        adapter = OpenAIResponsesAdapter()
        req = LLMRequest(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Explain math"}],
            api_key="sk-openai-key",
            base_url="https://api.openai.com",
        )

        mock_lines = [
            b'data: {"output_text_delta": "Math is "}\n',
            b'data: {"output_text_delta": "cool."}\n',
            b'data: [DONE]\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(mock_lines)
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            chunks = list(adapter.stream(req))
            self.assertEqual("".join(chunks), "Math is cool.")

            called_req = mock_urlopen.call_args[0][0]
            self.assertEqual(called_req.full_url, "https://api.openai.com/v1/responses")


if __name__ == "__main__":
    unittest.main()
