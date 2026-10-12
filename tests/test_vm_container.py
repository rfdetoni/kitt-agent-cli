"""Host-owned Docker/Podman QEMU and RE image boundary regressions.

Mocks validate command contracts. Building images and running real guests
requires a separate Docker/Podman + KVM host integration gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.vm_container import (
    IMAGES, _read_profile, _run_args, build_image, doctor, prepare_vm, pull_image,
)
from kitt.vm_supervisor import VMError


class ContainerVMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.iso = self.root / "windows11.iso"
        self.iso.write_bytes(b"user owned ISO fixture")
        self.disk = self.root / "linux.qcow2"
        self.disk.write_bytes(b"QFI\xfb" + b"\0" * 128)
        self.output = self.root / "guest"
        self.executed = []

    def exec_fake(self, argv, **kw):
        self.executed.append(argv)
        if "image" in argv and "inspect" in argv:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if "info" in argv and "qemu-img" in argv:
            return SimpleNamespace(returncode=0, stdout=b'{"format":"qcow2"}', stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"fake-id", stderr=b"")

    def test_iso_preparation_no_host_qemu_dependency(self):
        with patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container.subprocess.run", side_effect=self.exec_fake):
            path = prepare_vm(
                runtime="podman", system="windows", source=self.iso,
                destination=self.output, kind="iso", memory_mib=4096,
            )
        loaded = _read_profile(path)
        self.assertEqual(loaded["os"], "windows")
        self.assertEqual(loaded["runtime"], "podman")
        self.assertEqual(loaded["kind"], "iso")
        self.assertFalse((self.output / "disk.qcow2").exists())
        self.assertEqual(self.iso.read_bytes(), b"user owned ISO fixture")
        with patch("kitt.vm_container._runtime", return_value="podman"):
            with self.assertRaises(VMError):
                prepare_vm(runtime="podman", system="windows", source=self.iso,
                           destination=self.output, kind="iso")

    def test_qcow2_import_rejects_backing_chain(self):
        with patch("kitt.vm_container._runtime", return_value="docker"), \
             patch("kitt.vm_container.subprocess.run", return_value=SimpleNamespace(
                 returncode=0,
                 stdout=b'{"format":"qcow2","backing-filename":"../../private"}',
                 stderr=b"")):
            with self.assertRaises(VMError):
                prepare_vm(runtime="docker", system="linux", source=self.disk,
                           destination=self.output, kind="qcow2")

    def test_qcow2_copy_persists_after_original_is_removed(self):
        with patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container.subprocess.run", side_effect=self.exec_fake):
            path = prepare_vm(runtime="podman", system="linux", source=self.disk,
                              destination=self.output, kind="qcow2", memory_mib=2048)
        self.disk.unlink()
        self.assertEqual(_read_profile(path)["kind"], "qcow2")
        self.assertTrue((self.output / "disk.qcow2").is_file())

    def test_qemu_args_are_bounded_localhost_only_and_drop_caps(self):
        with patch("kitt.vm_container.os.access", return_value=True), \
             patch("kitt.vm_container.Path.stat", wraps=Path.stat) as stat:
            # Mock group ID without mutating an actual /dev/kvm.
            with patch("kitt.vm_container.Path.stat", return_value=SimpleNamespace(st_gid=99)):
                args = _run_args(
                    {"os": "linux", "name": "kitt-vm-test", "kind": "iso",
                     "state": str(self.root), "runtime": "podman", "port": 18006,
                     "source": str(self.iso), "memory_mib": 2048,
                     "cpus": 2, "disk_gib": 32},
                    binary="podman", install=True, allow_tcg=False,
                )
        self.assertIn("127.0.0.1:18006:8006", args)
        self.assertIn("no-new-privileges", " ".join(args))
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--userns=keep-id", args)
        self.assertIn("--group-add=keep-groups", args)
        self.assertIn("/dev/kvm", args)
        self.assertIn("readonly", " ".join(args))
        self.assertNotIn("--privileged", args)
        self.assertNotIn("/var/run/docker.sock", " ".join(args))
        self.assertNotIn("--network=host", args)
        with patch("kitt.vm_container.os.access", return_value=False):
            with self.assertRaises(VMError):
                _run_args({"kind": "iso", "runtime": "podman"}, binary="podman",
                          install=False, allow_tcg=False)

    def test_image_build_has_fixed_context_and_profile(self):
        with patch("kitt.vm_container._runtime", return_value="docker"), \
             patch("kitt.vm_container.subprocess.run", side_effect=self.exec_fake):
            r = build_image(runtime="docker", image="toolbox", profile="wine")
        self.assertEqual(r["image"], IMAGES["wine"])
        cmd = self.executed[-1]
        self.assertIn("--target", cmd)
        self.assertIn("wine", cmd)
        self.assertNotIn("--privileged", cmd)
        with self.assertRaises(VMError):
            build_image(runtime="docker", image="toolbox", profile="arbitrary")
        with patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container.subprocess.run", side_effect=self.exec_fake):
            r = pull_image(runtime="podman", image="qemu")
        self.assertEqual(r["local_image"], IMAGES["qemu"])
        self.assertIn("ghcr.io/rfdetoni/", " ".join(self.executed[-2]))

    def test_vm_helper_backend_flags_leave_normal_agent_unchanged(self):
        p = build_parser()
        args = p.parse_args(["vm", "create", "--backend", "podman",
                             "--os", "linux", "--source", str(self.iso), "--yes"])
        self.assertEqual(args.backend, "podman")
        self.assertEqual(args.port, 18006)
        b = p.parse_args(["vm", "container", "build", "--image", "toolbox",
                          "--profile", "full", "--runtime", "docker"])
        self.assertEqual(b.container_action, "build")
        self.assertEqual(b.profile, "full")
        normal = p.parse_args(["-p", "Add a Spring Boot endpoint"])
        self.assertNotEqual(normal.subcommand, "vm")


if __name__ == "__main__":
    unittest.main()
