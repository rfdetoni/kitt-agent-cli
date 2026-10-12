"""Unified VM helper must request user ISO/QCOW2 and preserve coding mode."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.vm_helper import create_vm
from kitt.vm_supervisor import VMError


class VMHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.iso = self.root / "Windows 11.iso"
        self.iso.write_bytes(b"test fixture")
        self.disk = self.root / "linux.qcow2"
        self.disk.write_bytes(b"test qcow2 fixture")
        self.output = self.root / "vm-profile.json"

    def test_interactive_windows_prompts_os_then_iso_path(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["1", str(self.iso)]), \
             patch("kitt.vm_windows.wizard", return_value=self.output) as wizard:
            system, path = create_vm()
        self.assertEqual(system, "windows")
        self.assertEqual(path, self.output)
        self.assertEqual(wizard.call_args.kwargs["source"], self.iso)
        self.assertEqual(wizard.call_args.kwargs["kind"], "iso")

    def test_interactive_linux_prompts_os_then_qcow2_path(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["2", str(self.disk)]), \
             patch("kitt.vm_linux.wizard", return_value=self.output) as wizard:
            system, path = create_vm()
        self.assertEqual(system, "linux")
        self.assertEqual(path, self.output)
        self.assertEqual(wizard.call_args.kwargs["kind"], "qcow2")

    def test_scriptable_windows_and_linux_without_prompts(self):
        with patch("kitt.vm_windows.wizard", return_value=self.output) as win, \
             patch("kitt.vm_linux.wizard", return_value=self.output) as linux:
            self.assertEqual(
                create_vm(system="windows", source=self.iso, variant="tiny11", yes=True),
                ("windows", self.output),
            )
            self.assertEqual(
                create_vm(system="linux", source=self.disk, distro="debian",
                          tools="wine", yes=True),
                ("linux", self.output),
            )
        self.assertTrue(win.call_args.kwargs["yes"])
        self.assertEqual(linux.call_args.kwargs["tools"], "wine")

    def test_rejects_missing_media_invalid_format_and_mismatch(self):
        for source in (self.root / "missing.iso", self.root):
            with self.subTest(source=source), self.assertRaises(VMError):
                create_vm(system="windows", source=source, yes=True)
        other = self.root / "unknown.zip"
        other.write_bytes(b"data")
        with self.assertRaises(VMError):
            create_vm(system="linux", source=other, yes=True)
        with self.assertRaises(VMError):
            create_vm(system="linux", source=self.iso, kind="qcow2", yes=True)
        symlink = self.root / "linked.iso"
        symlink.symlink_to(self.iso)
        with self.assertRaises(VMError):
            create_vm(system="windows", source=symlink, yes=True)

    def test_os_specific_parameters_must_not_cross_boundary(self):
        with self.assertRaises(VMError):
            create_vm(system="windows", source=self.iso, distro="debian", yes=True)
        with self.assertRaises(VMError):
            create_vm(system="linux", source=self.disk, variant="official", yes=True)

    def test_unattended_requires_explicit_source_and_os(self):
        with self.assertRaises(VMError):
            create_vm(source=self.iso, yes=True)
        with self.assertRaises(VMError):
            create_vm(system="linux", yes=True)
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(VMError):
                create_vm()

    def test_cli_routes_and_default_coding_mode_unchanged(self):
        parser = build_parser()
        wizard = parser.parse_args(["vm", "create"])
        self.assertEqual(wizard.vm_action, "create")
        self.assertIsNone(wizard.vm_os)
        selected = parser.parse_args(["vm", "wizard", "--os", "linux",
                                      "--source", str(self.disk), "--yes"])
        self.assertEqual(selected.vm_action, "wizard")
        self.assertEqual(selected.vm_os, "linux")
        self.assertTrue(selected.yes)
        normal = parser.parse_args(["-p", "Write a Spring Boot endpoint"])
        self.assertNotEqual(normal.subcommand, "vm")


if __name__ == "__main__":
    unittest.main()
