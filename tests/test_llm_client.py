import json
import unittest
from unittest.mock import patch

from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient


class _Response:
    def __init__(self, lines): self.lines = lines
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def __iter__(self): return iter(self.lines)


class TestOllamaClient(unittest.TestCase):
    def test_completion_request_uses_generate(self):
        with patch(
            "kitt.llm.providers.ollama.secure_urlopen",
            return_value=_Response([b'{"response":"KITT OLLAMA OK","done":true}\n']),
        ) as open_url:
            output = LLMClient(ModelProfile(backend="ollama", model="local", max_output_tokens=17)).chat(
                [{"role": "user", "content": "hello"}]
            )
        self.assertEqual(output, "KITT OLLAMA OK")
        request = open_url.call_args.args[0]
        self.assertTrue(request.full_url.endswith(("/api/chat", "/api/generate")))
        payload = json.loads(request.data)
        self.assertEqual(payload["options"]["num_predict"], 17)

    def test_lfm_request_uses_its_chat_template(self):
        with patch(
            "kitt.llm.providers.ollama.secure_urlopen",
            return_value=_Response([b'{"response":"ok","done":true}\n']),
        ) as open_url:
            LLMClient(ModelProfile(backend="ollama", model="lfm2.5-local")).chat([{"role": "user", "content": "hello"}])
        payload = json.loads(open_url.call_args.args[0].data)
        self.assertIn("<|im_start|>user", payload["template"])
