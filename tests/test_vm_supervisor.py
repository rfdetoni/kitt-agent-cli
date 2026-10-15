"""VM autonomy must not turn guest capabilities into host authority."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kitt.vm_supervisor import VMError, _profile, _vm_args, _ssh_args, _check_image
from kitt.vm_worker import handle


class VMSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "vm.qcow2"
        self.image.write_bytes(b"mock qcow2 from approved operator")
        self.key = self.root / "guest_identity"
        self.key.write_text("fake private key fixture")
        self.hosts = self.root / "known_hosts"
        self.hosts.write_text("kitt-sandbox ssh-ed25519 AABASE64TEST\n")
        self.profile = self.root / "profile.json"
        self.data = {
            "version": 1, "image": str(self.image),
            "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
            "ssh_key": str(self.key), "known_hosts": str(self.hosts),
            "ssh_user": "kitt", "host_key_alias": "kitt-sandbox",
            "memory_mib": 2048, "cpus": 2, "timeout_seconds": 600,
            "boot_seconds": 80, "gateway_port": 0,
        }
        for path in (self.key, self.hosts):
            path.chmod(0o600)
        self._write()

    def _write(self):
        self.profile.write_text(json.dumps(self.data))
        self.profile.chmod(0o600)

    def test_pinned_image_and_private_profile(self):
        self.assertEqual(_profile(self.profile)["sha256"], self.data["sha256"])
        self.image.write_bytes(b"changed")
        def fake(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=b'{"format":"qcow2"}')
        with patch("subprocess.run", side_effect=fake):
            with self.assertRaises(VMError):
                _check_image(_profile(self.profile), "qemu-img")
        if os.name == "posix":
            self.profile.chmod(0o644)
            with self.assertRaises(VMError):
                _profile(self.profile)

    def test_isolated_network_and_no_host_mounts(self):
        info = _profile(self.profile)
        args = _vm_args(info, "/usr/bin/qemu-system-x86_64", 2222, "fixture")
        joined = " ".join(args)
        self.assertIn("restrict=on", joined)
        self.assertIn("ipv6=off", joined)
        self.assertIn("hostfwd=tcp:127.0.0.1:2222-:22", joined)
        self.assertIn("snapshot=on", joined)
        self.assertNotIn("guestfwd", joined)
        self.assertNotIn("virtiofs", joined)
        # Check QEMU mount options, not incidental characters in a random
        # temporary image pathname (which may contain "9p").
        self.assertFalse(any(
            arg in {"-virtfs", "-fsdev"}
            or arg.startswith(("virtio-9p", "9p,"))
            for arg in args
        ))
        self.assertNotIn("usb-host", joined)
        self.assertNotIn("tap,", joined)
        ssh = _ssh_args(info, 2222, "ssh")
        self.assertIn("StrictHostKeyChecking=yes", ssh)
        self.assertIn("BatchMode=yes", ssh)
        self.assertIn("ClearAllForwardings=yes", ssh)

    def test_only_explicit_model_gateway_forwarding(self):
        self.data["gateway_port"] = 3000
        self._write()
        args = _vm_args(_profile(self.profile), "qemu", 2222, "fixture")
        self.assertIn("guestfwd=tcp:10.0.2.100:3000-tcp:127.0.0.1:3000", " ".join(args))

    def test_vm_profile_denies_unbounded_or_unknown_fields(self):
        self.data["memory_mib"] = 32768
        self._write()
        with self.assertRaises(VMError):
            _profile(self.profile)
        self.data["memory_mib"] = 2048
        self.data["unsafe_bridge"] = "br0"
        self._write()
        with self.assertRaises(VMError):
            _profile(self.profile)


class VMWorkerTests(unittest.TestCase):
    def test_goal_is_single_argument_and_daemon_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            recorded = {}
            def runner(argv, **kwargs):
                recorded["argv"] = argv
                recorded["env"] = kwargs["env"]
                kwargs["stdout"].write(b"[contract] success")
                return SimpleNamespace(returncode=0)
            with patch("kitt.vm_worker.os.geteuid", return_value=1000), \
                 patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}):
                result = handle(
                    {"version": 1, "method": "agent.run",
                     "goal": "Build; do not accidentally execute host shell"},
                    Path(temp), runner=runner,
                )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(recorded["argv"][-1], "Build; do not accidentally execute host shell")
            self.assertEqual(recorded["env"]["KITT_DAEMON"], "0")
            self.assertIn("--contract-yes", recorded["argv"])
            self.assertEqual(hashlib.sha256(result["output"].encode()).hexdigest(), result["output_sha256"])

    def test_rejects_extra_shell_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                handle({"version": 1, "method": "agent.run", "goal": "test",
                        "shell": "rm -rf /"}, Path(temp))


if __name__ == "__main__":
    unittest.main()
