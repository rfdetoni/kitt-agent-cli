"""Contract tests for CredentialStore, ProviderAuthService, and credential resolution."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from kitt.llm.auth import CredentialStore, ProviderAuthService


class TestProviderAuthContract(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.auth_file = Path(self.tmp_dir.name) / "auth.json"
        self.store = CredentialStore(auth_file=str(self.auth_file))
        self.auth_service = ProviderAuthService(store=self.store)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_credential_store_permissions_and_save_load(self):
        self.store.save_credential("openai", "api_key", "sk-secret-test-key-1234")
        self.assertTrue(self.auth_file.exists())

        # Check POSIX 0600 file permissions on Unix
        if os.name == "posix":
            mode = stat.S_IMODE(self.auth_file.stat().st_mode)
            self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)

        loaded = self.store.load()
        self.assertIn("openai", loaded)
        self.assertEqual(loaded["openai"]["value_ref"], "sk-secret-test-key-1234")

    def test_provider_auth_service_login_logout_lifecycle(self):
        # 1. Login with API key
        state = self.auth_service.login("anthropic", "sk-ant-test-9999", method="api_key")
        self.assertEqual(state.provider_id, "anthropic")
        self.assertEqual(state.credential_ref, "auth:anthropic")

        # 2. Resolve credential reference
        resolved = self.auth_service.resolve("auth:anthropic")
        self.assertEqual(resolved, "sk-ant-test-9999")

        # 3. List authenticated providers
        auth_list = self.auth_service.authenticated()
        self.assertTrue(any(s.provider_id == "anthropic" for s in auth_list))

        # 4. Logout
        self.auth_service.logout("anthropic")
        self.assertIsNone(self.auth_service.resolve("auth:anthropic"))

    def test_env_var_and_session_credential_resolution(self):
        # Test env variable reference
        with patch_dict_env({"MY_TEST_GROQ_KEY": "gsk_abc123"}):
            resolved = self.auth_service.resolve("env:MY_TEST_GROQ_KEY")
            self.assertEqual(resolved, "gsk_abc123")

            # Default env var fallback for provider
            with patch_dict_env({"GROQ_API_KEY": "gsk_groq_default"}):
                fallback = self.auth_service.resolve(None, provider_id="groq")
                self.assertEqual(fallback, "gsk_groq_default")

        # Test session reference
        self.auth_service.login("deepseek", "session_secret_123", method="session")
        resolved_sess = self.auth_service.resolve("session:deepseek")
        self.assertEqual(resolved_sess, "session_secret_123")


def patch_dict_env(values):
    from unittest.mock import patch
    return patch.dict(os.environ, values)


if __name__ == "__main__":
    unittest.main()
