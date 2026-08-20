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
        self.home_dir = self.root_dir / "home"
        self.kitt_home = self.home_dir / ".kitt"
        self.kitt_home.mkdir(parents=True, mode=0o700)
        self.old_home = os.environ.get("HOME")
        self.old_kitt_home = os.environ.get("KITT_HOME")
        os.environ["HOME"] = str(self.home_dir)
        os.environ["KITT_HOME"] = str(self.kitt_home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        if self.old_kitt_home is None:
            os.environ.pop("KITT_HOME", None)
        else:
            os.environ["KITT_HOME"] = self.old_kitt_home
        self.tmp_dir.cleanup()

    def test_legacy_router_json_secret_migration_is_idempotent(self):
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

        TaskRouter(root_dir=str(self.root_dir))

        migrated_text = router_file.read_text(encoding="utf-8")
        self.assertNotIn("sk-legacy-secret-never-store-in-workspace-12345", migrated_text)
        self.assertNotIn("sk-ant-legacy-secret-67890", migrated_text)

        migrated_data = json.loads(migrated_text)
        self.assertIn("execute", migrated_data["profiles"])
        self.assertEqual(migrated_data["profiles"]["execute"].get("api_key", ""), "")
        self.assertIsNone(migrated_data["profiles"]["execute"].get("credential_ref"))
        self.assertEqual(migrated_data["profiles"]["context"].get("api_key", ""), "")
        self.assertIsNone(migrated_data["profiles"]["context"].get("credential_ref"))

        auth_service = ProviderAuthService()
        self.assertIsNone(auth_service.resolve("auth:openai"))
        self.assertIsNone(auth_service.resolve("auth:anthropic"))

        TaskRouter(root_dir=str(self.root_dir))
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
