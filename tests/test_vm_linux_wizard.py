"""Linux RE guest wizard safety and provisioning contract."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.vm_linux import _profile, _qemu_args, bootstrap_script, prepare, wizard
from kitt.vm_supervisor import VMError


class LinuxREWizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iso = self.root / "debian-re.iso"
        self.iso.write_bytes(b"example user-provided image")
        self.out = self.root / "linux-re"

    def fake_run(self, argv, **kwargs):
        if "create" in argv:
            Path(argv[-2]).write_bytes(b"mock qcow2")
        if "info" in argv:
            return SimpleNamespace(returncode=0, stdout=b'{"format":"qcow2"}')
        return SimpleNamespace(returncode=0, stdout=b"")

    def test_prepare_from_user_iso_without_modifying_it(self):
        original = self.iso.read_bytes()
        with patch("kitt.vm_linux._program", side_effect=lambda name: name), \
             patch("kitt.vm_linux.subprocess.run", side_effect=self.fake_run):
            profile = prepare(self.iso, kind="iso", distro="debian", tools="wine",
                              destination=self.out, memory_mib=2048)
        self.assertEqual(self.iso.read_bytes(), original)
        self.assertEqual(_profile(profile)["distro"], "debian")
        self.assertEqual(_profile(profile)["tools"], "wine")
        self.assertTrue((self.out / "linux.qcow2").exists())
        script = (self.out / "setup-re-tools.sh").read_text()
        self.assertIn("apt-get install", script)
        self.assertIn("wine64", script)
        self.assertFalse((self.out / "seed.iso").exists())
        if os.name == "posix":
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
        with patch("kitt.vm_linux._program", side_effect=lambda name: name):
            with self.assertRaises(VMError):
                prepare(self.iso, kind="iso", destination=self.out)

    def test_offline_boot_and_opt_in_package_network(self):
        info = {"source": str(self.iso), "disk": str(self.root / "linux.qcow2"),
                "memory_mib": 2048, "cpus": 2}
        cmd = _qemu_args(info, 2, install=True, qemu="qemu", network=False)
        text = " ".join(cmd)
        self.assertIn("-nic none", text)
        self.assertIn("127.0.0.1:2", text)
        self.assertIn("media=cdrom,readonly=on", text)
        self.assertNotIn("hostfwd", text)
        self.assertNotIn("virtiofs", text)
        online = _qemu_args(info, 2, install=False, qemu="qemu", network=True)
        self.assertIn("user,id=re-labnet,ipv6=off", " ".join(online))
        self.assertNotIn("media=cdrom", " ".join(online))
        with self.assertRaises(VMError):
            _qemu_args(info, 0, install=False, qemu="qemu", network=False)

    def test_rejects_unsafe_source_and_resources(self):
        with self.assertRaises(VMError):
            prepare(self.root / "a,if=none.iso", kind="iso", destination=self.out)
        with self.assertRaises(VMError):
            prepare(self.iso, kind="iso", destination=self.out, cpus=0)
        with self.assertRaises(VMError):
            prepare(self.iso, kind="iso", destination=self.out, memory_mib=True)
        link = self.root / "link.iso"
        link.symlink_to(self.iso)
        with self.assertRaises(VMError):
            prepare(link, kind="iso", destination=self.out)

    def test_qcow2_with_backing_file_is_refused(self):
        qcow = self.root / "debian.qcow2"
        qcow.write_bytes(b"placeholder")
        with patch("kitt.vm_linux._program", side_effect=lambda name: name), \
             patch("kitt.vm_linux.subprocess.run", return_value=SimpleNamespace(
                 returncode=0, stdout=b'{"format":"qcow2","backing-filename":"secret"}')):
            with self.assertRaises(VMError):
                prepare(qcow, kind="qcow2", destination=self.out)

    def test_profiles_do_not_execute_on_host_and_cli_opt_in(self):
        script = bootstrap_script("fedora", "full")
        self.assertIn("dnf -y install", script)
        self.assertIn("wine", script)
        self.assertIn("Ghidra", script)
        with self.assertRaises(VMError):
            bootstrap_script("invalid", "full")
        args = build_parser().parse_args(["vm", "linux", "prepare", "--source", str(self.iso),
                                           "--distro", "kali", "--tools", "re", "--yes"])
        self.assertEqual(args.distro, "kali")
        self.assertTrue(args.yes)
        normal = build_parser().parse_args(["-p", "Fix this Java program"])
        self.assertNotEqual(normal.subcommand, "vm")

    def test_wizard_noninteractive_explicit_source(self):
        with patch("kitt.vm_linux.prepare", return_value=self.out / "profile.json") as action:
            value = wizard(source=self.iso, kind="iso", distro="ubuntu",
                           tools="re", destination=self.out, yes=True)
        self.assertEqual(value, self.out / "profile.json")
        self.assertEqual(action.call_args.kwargs["distro"], "ubuntu")

    def test_interactive_distro_and_resources(self):
        answers = ["kali", "wine", "3072", "2", "40", str(self.out), "sim"]
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=answers), \
             patch("kitt.vm_linux.prepare", return_value=self.out / "profile.json") as action:
            wizard(source=self.iso, kind="iso")
        self.assertEqual(action.call_args.kwargs["memory_mib"], 3072)
        self.assertEqual(action.call_args.kwargs["tools"], "wine")
        self.assertEqual(action.call_args.kwargs["distro"], "kali")


if __name__ == "__main__":
    unittest.main()
