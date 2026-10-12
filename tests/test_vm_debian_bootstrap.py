"""Fail-closed Debian bootstrap unit contracts; real QEMU boot is a separate gate."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kitt.vm_debian import (IMAGE, VMError, cloud_config, download_debian,
                           expected_sha512, prepare_debian)


class DebianBootstrapTests(unittest.TestCase):
    def test_cloud_init_installs_only_operator_chosen_agent(self):
        cfg = cloud_config("ssh-ed25519 AAAAE2VjZHNhLXNoYTItZXN0 fixture", "a" * 40)
        self.assertTrue(cfg.startswith("#cloud-config\n"))
        parsed = json.loads(cfg.split("\n", 1)[1])
        user = parsed["users"][0]
        self.assertTrue(user["lock_passwd"])
        self.assertIn('restrict,command=', user["ssh_authorized_keys"][0])
        self.assertIn("kitt.vm_worker", user["ssh_authorized_keys"][0])
        self.assertIn("uv python install 3.14", parsed["write_files"][0]["content"])
        self.assertIn("@" + "a" * 40, parsed["write_files"][0]["content"])
        self.assertNotIn("UserKnownHostsFile=/dev/null", cfg)
        with self.assertRaises(VMError):
            cloud_config("ssh-rsa bad", "a" * 40)
        with self.assertRaises(VMError):
            cloud_config("ssh-ed25519 AAAA", "main")

    def test_official_checksum_required_for_image(self):
        digest = "a" * 128
        text = f"{digest}  {IMAGE}\n" + "b" * 128 + "  other.qcow2\n"
        with patch("kitt.vm_debian._read_https", return_value=text.encode()):
            self.assertEqual(expected_sha512(), digest)
        with patch("kitt.vm_debian._read_https", return_value=(text + text).encode()):
            with self.assertRaises(VMError):
                expected_sha512()

    def test_corrupt_download_is_not_accepted(self):
        class Response:
            status = 200
            url = "https://cloud.debian.org/images/cloud/trixie/latest/" + IMAGE
            def __init__(self):
                self.parts = iter([b"not-the-image", b""])
            def read(self, _size):
                return next(self.parts)
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.qcow2"
            with patch("kitt.vm_debian.urllib.request.urlopen", return_value=Response()):
                with self.assertRaises(VMError):
                    download_debian(path, "0" * 128)

    def test_no_implicit_vm_creation_on_existing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "vm"
            folder.mkdir()
            (folder / "profile.json").write_text("{}")
            with patch("kitt.vm_debian._check_image"), patch("kitt.vm_debian._profile"), \
                 patch("kitt.vm_debian._program", return_value="qemu-img"):
                self.assertEqual(prepare_debian(folder), folder / "profile.json")
            self.assertFalse((folder / "seed.iso").exists())

    def test_existing_invalid_profile_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "vm"
            folder.mkdir()
            with self.assertRaises(VMError):
                prepare_debian(folder)


if __name__ == "__main__":
    unittest.main()
