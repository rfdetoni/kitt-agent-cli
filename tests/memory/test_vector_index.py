import os
import unittest
from unittest.mock import patch

from kitt.memory.vector_index import LocalVectorIndexer


class VectorIndexerTests(unittest.TestCase):
    def test_disabled_without_model(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(LocalVectorIndexer.from_environment())

    def test_rejects_remote_hosts(self):
        for url in ("http://example.com:11434", "http://192.168.1.2:11434",
                    "https://127.0.0.1:11434", "http://127.0.0.1:11434/path"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalVectorIndexer("test-model", url)

    def test_local_query_failure_degrades_to_none(self):
        idx = LocalVectorIndexer("model-v1", "http://127.0.0.1:11434")
        with patch.object(idx.provider, "embed", side_effect=RuntimeError("down")):
            self.assertIsNone(idx.query("safe lexical fallback"))

    def test_background_write_uses_original_content_hash(self):
        from threading import Event
        idx = LocalVectorIndexer("model-v1", "http://127.0.0.1:11434")
        idx.query = lambda content: [1.0, 0.0]
        done = Event()
        class Client:
            def manage(self, operation, data):
                assert operation == "vector.upsert"
                assert data["expected_content_hash"] == "a" * 64
                done.set()
        idx.schedule(Client(), "some-id", "content", "a" * 64)
        self.assertTrue(done.wait(3), "background vector write not invoked")
