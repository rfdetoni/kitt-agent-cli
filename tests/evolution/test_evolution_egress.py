from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.evolution.egress import EvolutionEgressClient


class InnerClient:
    def __init__(
        self,
        *,
        backend="openai",
        base_url="https://api.example.test/v1",
    ):
        self.profile = SimpleNamespace(
            model="model",
            backend=backend,
            base_url=base_url,
        )
        self.last_messages = None
        self.last_system = None
        self.closed = False

    def close(self):
        self.closed = True

    def chat(self, messages, system_prompt=None, **kwargs):
        self.last_messages = messages
        self.last_system = system_prompt
        return "ok"


class EvolutionEgressTests(unittest.TestCase):
    def test_hybrid_redacts_sensitive_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = InnerClient()
            client = EvolutionEgressClient(
                inner,
                root_dir=Path(tmp),
                privacy_mode="hybrid_redacted",
            )
            self.assertEqual(
                client.chat(
                    [{"role": "user", "content": "API_KEY=supersecret123"}],
                    system_prompt="safe",
                ),
                "ok",
            )
            self.assertNotIn("supersecret123", inner.last_messages[0]["content"])
            self.assertIn("[REDACTED_ENV_SECRET]", inner.last_messages[0]["content"])
            client.close()
            self.assertTrue(inner.closed)

    def test_local_only_rejects_remote_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = EvolutionEgressClient(
                InnerClient(),
                root_dir=Path(tmp),
                privacy_mode="local_only",
            )
            with self.assertRaises(PermissionError):
                client.chat([{"role": "user", "content": "hello"}])

    def test_local_only_accepts_loopback_ollama(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = EvolutionEgressClient(
                InnerClient(
                    backend="ollama",
                    base_url="http://127.0.0.1:11434",
                ),
                root_dir=Path(tmp),
                privacy_mode="local_only",
            )
            self.assertEqual(
                client.chat([{"role": "user", "content": "hello"}]),
                "ok",
            )

    def test_offline_rejects_even_local_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = EvolutionEgressClient(
                InnerClient(
                    backend="ollama",
                    base_url="http://127.0.0.1:11434",
                ),
                root_dir=Path(tmp),
                privacy_mode="offline",
            )
            with self.assertRaises(PermissionError):
                client.chat([{"role": "user", "content": "hello"}])


if __name__ == "__main__":
    unittest.main()
