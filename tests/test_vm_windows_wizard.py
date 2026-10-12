"""Windows VM wizard tests: no Windows, Tiny11 or QEMU download is implied."""
from __future__ import annotations

from pathlib import Path
import json
import os
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.vm_supervisor import VMError
from kitt.vm_windows import _profile, _qemu_args, prepare, wizard


class WindowsWizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iso = self.root / "my-tiny11.iso"
        self.iso.write_bytes(b"owned user iso test fixture")
        self.code = self.root / "CODE.fd"
        self.code.write_bytes(b"uefi-code")
        self.vars = self.root / "VARS.fd"
        self.vars.write_bytes(b"uefi-variables")
        self.out = self.root / "windows-test"
        self.calls = []

    def fake_run(self, argv, **kwargs):
        self.calls.append(argv)
        if "create" in argv:
            Path(argv[-2]).write_bytes(b"created qcow2 fixture")
        if "info" in argv:
            return SimpleNamespace(returncode=0, stdout=b'{"format":"qcow2","virtual-size":68719476736}')
        return SimpleNamespace(returncode=0, stdout=b"")

    def test_create_vm_from_user_iso_does_not_modify_iso(self):
        original = self.iso.read_bytes()
        with patch("kitt.vm_windows._program", side_effect=lambda n: n), \
             patch("kitt.vm_windows.subprocess.run", side_effect=self.fake_run):
            path = prepare(self.iso, kind="iso", destination=self.out,
                           variant="tiny11", ovmf_code=self.code, ovmf_vars=self.vars)
        self.assertEqual(self.iso.read_bytes(), original)
        self.assertEqual(_profile(path)["variant"], "tiny11")
        self.assertEqual(_profile(path)["source_kind"], "iso")
        self.assertTrue((self.out / "windows.qcow2").exists())
        self.assertFalse((self.out / "seed.iso").exists())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch("kitt.vm_windows._program", side_effect=lambda n: n):
            with self.assertRaises(VMError):
                prepare(self.iso, kind="iso", destination=self.out,
                        ovmf_code=self.code, ovmf_vars=self.vars)

    def test_qcow2_import_requires_standalone_disk(self):
        src = self.root / "preinstalled.qcow2"
        src.write_bytes(b"source qcow2")
        with patch("kitt.vm_windows._program", side_effect=lambda n: n), \
             patch("kitt.vm_windows.subprocess.run", side_effect=self.fake_run):
            profile = prepare(src, kind="qcow2", destination=self.out,
                              ovmf_code=self.code, ovmf_vars=self.vars)
        self.assertEqual(_profile(profile)["source_kind"], "qcow2")
        self.assertEqual((self.out / "windows.qcow2").read_bytes(), src.read_bytes())
        with patch("kitt.vm_windows.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=b'{"format":"qcow2","backing-filename":"host-secret"}')), \
             patch("kitt.vm_windows._program", side_effect=lambda n: n):
            with self.assertRaises(VMError):
                prepare(src, kind="qcow2", destination=self.root / "rejected",
                        ovmf_code=self.code, ovmf_vars=self.vars)

    def test_offline_hardware_and_readonly_install_media(self):
        info = {
            "ovmf_code": str(self.code), "ovmf_vars": str(self.vars),
            "disk": str(self.root / "windows.qcow2"), "source": str(self.iso),
            "memory_mib": 4096, "cpus": 2,
        }
        cmd = _qemu_args(info, 1, True, self.root / "swtpm.sock", "qemu-system-x86_64")
        joined = " ".join(cmd)
        self.assertIn("-nic none", joined)
        self.assertIn("127.0.0.1:1", joined)
        self.assertIn("media=cdrom,readonly=on", joined)
        self.assertIn("tpm-tis", joined)
        self.assertIn("if=pflash", joined)
        self.assertNotIn("hostfwd", joined)
        self.assertNotIn("virtiofs", joined)
        self.assertNotIn("-fsdev", cmd)
        self.assertFalse(any("virtio-9p" in arg for arg in cmd))
        installed = _qemu_args(info, 1, False, self.root / "s", "qemu")
        self.assertNotIn("media=cdrom", " ".join(installed))
        with self.assertRaises(VMError):
            _qemu_args(info, 0, False, self.root / "s", "qemu")

    def test_refuse_image_overwrite_and_injection(self):
        with self.assertRaises(VMError):
            prepare(self.root / "a,if=none.iso", kind="iso", destination=self.out,
                    ovmf_code=self.code, ovmf_vars=self.vars)
        with self.assertRaises(VMError):
            prepare(self.iso, kind="iso", memory_mib=1024,
                    destination=self.out, ovmf_code=self.code, ovmf_vars=self.vars)
        linked = self.root / "linked.iso"
        linked.symlink_to(self.iso)
        with self.assertRaises(VMError):
            prepare(linked, kind="iso", destination=self.out,
                    ovmf_code=self.code, ovmf_vars=self.vars)

    def test_interactive_wizard_collects_resources_before_creation(self):
        answers = ["tiny11", "6144", "4", "80", str(self.out), "sim"]
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=answers), \
             patch("kitt.vm_windows.prepare", return_value=self.out / "profile.json") as make:
            profile = wizard(source=self.iso, kind="iso")
        self.assertEqual(profile, self.out / "profile.json")
        self.assertEqual(make.call_args.kwargs["memory_mib"], 6144)
        self.assertEqual(make.call_args.kwargs["cpus"], 4)
        self.assertEqual(make.call_args.kwargs["disk_gib"], 80)
        self.assertEqual(make.call_args.kwargs["variant"], "tiny11")
    def test_cli_is_opt_in_and_wizard_supports_scripts(self):
        parser = build_parser()
        normal = parser.parse_args(["-p", "Implement a REST endpoint"])
        self.assertNotEqual(normal.subcommand, "vm")
        selected = parser.parse_args(["vm", "windows", "prepare", "--source", str(self.iso),
                                      "--variant", "tiny11-core", "--yes"])
        self.assertEqual(selected.windows_action, "prepare")
        self.assertTrue(selected.yes)
        with patch("kitt.vm_windows.prepare", return_value=self.out / "profile.json") as fake:
            result = wizard(source=self.iso, kind="iso", yes=True, destination=self.out)
        self.assertEqual(result, self.out / "profile.json")
        fake.assert_called_once()


if __name__ == "__main__":
    unittest.main()
