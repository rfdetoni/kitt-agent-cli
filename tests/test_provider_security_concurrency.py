"""Security and concurrency tests for provider credentials and atomic file operations."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.llm.auth import CredentialStore, ProviderAuthService
from kitt.router.router import TaskRouter


class TestProviderSecurityConcurrency(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_legacy_router_json_secret_migration_is_idempotent(self):
        # 1. Create legacy router file containing plain text API keys
        router_file = self.root_dir / ".kitt-router.json"
        legacy_data = {
            "profiles": {
                "execute": {
                    "backend": "openai",
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com",
                    "api_key": "sk-legacy-secret-never-store-in-workspace-12345"
                },
                "context": {
                    "backend": "anthropic",
                    "model": "claude-3-5-sonnet",
                    "base_url": "https://api.anthropic.com",
                    "api_key": "sk-ant-legacy-secret-67890"
                }
            },
            "routing": {
                "chat": "execute",
                "summarize": "context"
            }
        }
        router_file.write_text(json.dumps(legacy_data, indent=2), encoding="utf-8")

        # 2. Initialize TaskRouter to trigger automatic migration
        router = TaskRouter(root_dir=str(self.root_dir))

        # 3. Read back .kitt-router.json content to verify secrets were removed
        migrated_text = router_file.read_text(encoding="utf-8")
        self.assertNotIn("sk-legacy-secret-never-store-in-workspace-12345", migrated_text)
        self.assertNotIn("sk-ant-legacy-secret-67890", migrated_text)

        migrated_data = json.loads(migrated_text)
        self.assertEqual(migrated_data["profiles"]["execute"]["credential_ref"], "auth:openai")
        self.assertEqual(migrated_data["profiles"]["context"]["credential_ref"], "auth:anthropic")

        # 4. Verify credentials were moved to CredentialStore
        auth_service = ProviderAuthService()
        self.assertEqual(auth_service.resolve("auth:openai"), "sk-legacy-secret-never-store-in-workspace-12345")
        self.assertEqual(auth_service.resolve("auth:anthropic"), "sk-ant-legacy-secret-67890")

        # 5. Verify re-loading is idempotent and clean
        router2 = TaskRouter(root_dir=str(self.root_dir))
        migrated_text2 = router_file.read_text(encoding="utf-8")
        self.assertEqual(migrated_text, migrated_text2)

    def test_concurrent_credential_store_writes(self):
        auth_file = self.root_dir / "auth.json"
        store = CredentialStore(auth_file=str(auth_file))
        errors = []

        def worker(provider_idx: int):
            try:
                for i in range(10):
                    store.save_credential(f"provider_{provider_idx}", "api_key", f"secret_{provider_idx}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        loaded = store.load()
        self.assertTrue(len(loaded) > 0)


if __name__ == "__main__":
    unittest.main()
