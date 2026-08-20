from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.llm.auth import CredentialStore, ProviderAuthService
from kitt.router.router import TaskRouter
from kitt.security.credentials import atomic_write_secure


class _IsolatedAuthService(ProviderAuthService):
    auth_file: Path | None = None

    def __init__(self):
        if self.auth_file is None:
            raise RuntimeError("auth_file not configured")
        super().__init__(
            CredentialStore(auth_file=str(self.auth_file))
        )


class TestCustomProviderSecretHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auth_file = self.root / "auth-state" / "auth.json"
        _IsolatedAuthService.auth_file = self.auth_file

    def tearDown(self):
        _IsolatedAuthService.auth_file = None
        self.tmp.cleanup()

    def _patch_auth(self):
        return patch(
            "kitt.router.router.ProviderAuthService",
            _IsolatedAuthService,
        )

    def test_save_moves_literal_custom_provider_key_out_of_workspace(self):
        with self._patch_auth():
            router = TaskRouter(root_dir=str(self.root))
            router.config.custom_providers = [
                {
                    "name": "custom-gateway",
                    "base_url": "https://gateway.example/v1",
                    "backend": "openai",
                    "protocol": "openai-chat-completions",
                    "api_key": "sk-custom-plain-text-secret",
                }
            ]
            router.save_config(str(self.root))

        router_path = self.root / ".kitt-router.json"
        raw = router_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-custom-plain-text-secret", raw)

        data = json.loads(raw)
        custom = data["custom_providers"][0]
        self.assertEqual(
            custom["api_key"],
            "auth:custom-gateway",
        )
        self.assertEqual(
            custom["credential_ref"],
            "auth:custom-gateway",
        )

        store = ProviderAuthService(
            CredentialStore(auth_file=str(self.auth_file))
        )
        self.assertEqual(
            store.resolve("auth:custom-gateway"),
            "sk-custom-plain-text-secret",
        )
        self.assertNotIn(
            "sk-custom-plain-text-secret",
            json.dumps(router.config.custom_providers),
        )

    def test_load_migrates_legacy_custom_provider_key_immediately(self):
        router_path = self.root / ".kitt-router.json"
        legacy = {
            "profiles": {},
            "routing": {},
            "custom_providers": [
                {
                    "name": "legacy-proxy",
                    "base_url": "https://proxy.example/v1",
                    "backend": "openai",
                    "protocol": "openai-chat-completions",
                    "api_key": "legacy-raw-secret",
                }
            ],
        }
        router_path.write_text(
            json.dumps(legacy),
            encoding="utf-8",
        )
        if os.name == "posix":
            os.chmod(router_path, 0o600)

        with self._patch_auth():
            router = TaskRouter(root_dir=str(self.root))

        migrated_raw = router_path.read_text(encoding="utf-8")
        self.assertNotIn("legacy-raw-secret", migrated_raw)
        self.assertEqual(
            router.config.custom_providers[0]["api_key"],
            "auth:legacy-proxy",
        )
        auth = ProviderAuthService(
            CredentialStore(auth_file=str(self.auth_file))
        )
        self.assertEqual(
            auth.resolve("auth:legacy-proxy"),
            "legacy-raw-secret",
        )

    def test_safe_reference_is_not_materialized_or_rewritten(self):
        with self._patch_auth():
            router = TaskRouter(root_dir=str(self.root))
            router.config.custom_providers = [
                {
                    "name": "ref-proxy",
                    "base_url": "https://proxy.example/v1",
                    "backend": "openai",
                    "protocol": "openai-chat-completions",
                    "api_key": "env:REF_PROXY_KEY",
                }
            ]
            router.save_config(str(self.root))

        data = json.loads(
            (self.root / ".kitt-router.json").read_text(
                encoding="utf-8"
            )
        )
        custom = data["custom_providers"][0]
        self.assertEqual(
            custom["api_key"],
            "env:REF_PROXY_KEY",
        )
        self.assertEqual(
            custom["credential_ref"],
            "env:REF_PROXY_KEY",
        )


@unittest.skipIf(os.name != "posix", "POSIX special-file semantics")
class TestAtomicSecureWriteBoundary(unittest.TestCase):
    def test_final_symlink_is_rejected_and_target_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "victim.json"
            target.write_text("ORIGINAL", encoding="utf-8")
            link = root / "config.json"
            link.symlink_to(target)

            with self.assertRaises(PermissionError):
                atomic_write_secure(link, "ATTACKER")

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "ORIGINAL",
            )
            self.assertTrue(link.is_symlink())

    def test_fifo_target_is_rejected_without_opening_it(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            os.mkfifo(target, 0o600)

            with self.assertRaises(PermissionError):
                atomic_write_secure(target, "{}")

    def test_existing_workspace_parent_mode_is_not_forcibly_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chmod(root, 0o755)
            target = root / "config.json"

            atomic_write_secure(target, "{}")

            self.assertEqual(
                stat.S_IMODE(root.stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE(target.stat().st_mode),
                0o600,
            )


if __name__ == "__main__":
    unittest.main()
