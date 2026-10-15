"""Explicit semantic model preparation must never download by default."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.prepare_semantic_model import DEFAULT_DIGEST, DEFAULT_MODEL, main, prepare
from kitt.context.rag.embeddings import EmbeddingError


class PrepareSemanticModelTests(unittest.TestCase):
    def test_verify_only_does_not_start_pull(self):
        with patch("scripts.prepare_semantic_model.OllamaEmbeddingProvider") as model, \
             patch("scripts.prepare_semantic_model.subprocess.run") as run:
            model.return_value.cache_identity.return_value = (
                f"{DEFAULT_MODEL}|sha256:{DEFAULT_DIGEST}|e5=True|role-v1|truncate=false"
            )
            self.assertEqual(prepare(DEFAULT_MODEL, DEFAULT_DIGEST, pull=False), DEFAULT_DIGEST)
            run.assert_not_called()

    def test_pull_is_explicit_and_forced_to_local_daemon(self):
        with patch("scripts.prepare_semantic_model.OllamaEmbeddingProvider") as model, \
             patch("scripts.prepare_semantic_model.subprocess.run") as run:
            model.return_value.cache_identity.return_value = f"{DEFAULT_MODEL}|sha256:{DEFAULT_DIGEST}"
            prepare(DEFAULT_MODEL, DEFAULT_DIGEST, pull=True)
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["ollama", "pull", DEFAULT_MODEL])
            self.assertEqual(kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11434")

    def test_wrong_digest_fails_closed(self):
        with patch("scripts.prepare_semantic_model.OllamaEmbeddingProvider") as provider:
            provider.return_value.cache_identity.return_value = "some|sha256:" + "f"*64
            with self.assertRaisesRegex(EmbeddingError, "digest mismatch"):
                prepare(DEFAULT_MODEL, DEFAULT_DIGEST, pull=False)

    def test_custom_model_cannot_inherit_different_digest(self):
        with self.assertRaises(SystemExit):
            main(["--model", "other-model"])

if __name__ == "__main__":
    unittest.main()
