from __future__ import annotations

import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from kitt.llm.auth import CredentialStore


@unittest.skipIf(os.name == "nt", "POSIX file-security semantics")
class TestProviderCredentialStoreHardening(unittest.TestCase):
    def test_final_auth_symlink_is_rejected_and_target_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text(
                '{"openai":{"type":"api_key","value_ref":"target-secret"}}',
                encoding="utf-8",
            )
            os.chmod(target, 0o600)

            link = root / "auth.json"
            link.symlink_to(target)
            store = CredentialStore(auth_file=str(link))

            with self.assertRaises(PermissionError):
                store.load()
            with self.assertRaises(PermissionError):
                store.save_credential(
                    "anthropic",
                    "api_key",
                    "new-secret",
                )

            self.assertIn(
                "target-secret",
                target.read_text(encoding="utf-8"),
            )

    def test_world_readable_auth_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text(
                '{"openai":{"type":"api_key","value_ref":"secret"}}',
                encoding="utf-8",
            )
            os.chmod(auth, 0o644)
            store = CredentialStore(auth_file=str(auth))

            with self.assertRaises(PermissionError):
                store.load()

    def test_fifo_auth_file_is_rejected_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            os.mkfifo(auth, 0o600)
            store = CredentialStore(auth_file=str(auth))

            with self.assertRaises(PermissionError):
                store.load()

    def test_oversized_auth_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            with auth.open("wb") as handle:
                handle.truncate(CredentialStore._MAX_AUTH_BYTES + 1)
            os.chmod(auth, 0o600)
            store = CredentialStore(auth_file=str(auth))

            with self.assertRaises(PermissionError):
                store.load()

    def test_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / "real"
            real.mkdir()
            os.chmod(real, 0o700)
            link = base / "credential-dir"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaises(PermissionError):
                CredentialStore(
                    auth_file=str(link / "auth.json")
                )

    def test_written_store_is_private_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            store = CredentialStore(auth_file=str(auth))
            store.save_credential(
                "openai",
                "api_key",
                "sk-secret",
            )

            st = auth.lstat()
            self.assertTrue(stat.S_ISREG(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(
                store.load()["openai"]["value_ref"],
                "sk-secret",
            )


class TestProviderCredentialStoreConcurrency(unittest.TestCase):
    def test_multiple_store_instances_do_not_lose_provider_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            errors: list[Exception] = []

            def worker(index: int) -> None:
                try:
                    store = CredentialStore(
                        auth_file=str(auth)
                    )
                    for iteration in range(5):
                        store.save_credential(
                            f"provider_{index}",
                            "api_key",
                            f"secret_{index}_{iteration}",
                        )
                except Exception as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(
                    target=worker,
                    args=(index,),
                )
                for index in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            loaded = CredentialStore(
                auth_file=str(auth)
            ).load()
            self.assertEqual(
                set(loaded),
                {f"provider_{index}" for index in range(8)},
            )


if __name__ == "__main__":
    unittest.main()
