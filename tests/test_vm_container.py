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
    resolve_runtime,
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
            if "--format" in argv:
                return SimpleNamespace(returncode=0, stdout=b"sha256:" + b"a" * 64 + b"\n", stderr=b"")
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
        self.assertEqual(loaded["probe_port"], 0)
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
        self.assertEqual((self.output / "viewer-password").stat().st_mode & 0o777, 0o600)
        self.assertIn("kitt:{SHA}", (self.output / "vnc.htpasswd").read_text())

    def test_qemu_args_are_bounded_localhost_only_and_drop_caps(self):
        with patch("kitt.vm_container.os.access", return_value=True), \
             patch("kitt.vm_container.Path.stat", return_value=SimpleNamespace(st_gid=99)):
            args = _run_args(
                    {"os": "linux", "name": "kitt-vm-test", "kind": "iso",
                     "state": str(self.root), "runtime": "podman", "port": 18006,
                     "source": str(self.iso), "image_id": "sha256:" + "a" * 64, "probe_port": 0, "memory_mib": 2048,
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
        self.assertIn("dst=/run/secrets/kitt-vnc.htpasswd,readonly", " ".join(args))
        self.assertEqual(args[-1], "sha256:" + "a" * 64)
        self.assertNotIn("--rm", args)
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


    def test_autodetect_prefers_working_docker_without_falling_back_from_explicit(self):
        def run(argv, **kwargs):
            return SimpleNamespace(returncode=1 if "docker" in argv[0] else 0)
        with patch("kitt.vm_container.shutil.which", side_effect=lambda n: "/usr/bin/" + n), \
             patch("kitt.vm_container.subprocess.run", side_effect=run):
            self.assertEqual(resolve_runtime("auto"), "podman")
            with self.assertRaisesRegex(VMError, "not responding"):
                resolve_runtime("docker")

    def test_autodetect_uses_docker_when_healthy_and_reports_missing_engines(self):
        with patch("kitt.vm_container.shutil.which", side_effect=lambda n: "/usr/bin/" + n), \
             patch("kitt.vm_container.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(resolve_runtime("auto"), "docker")
        with patch("kitt.vm_container.shutil.which", return_value=None):
            with self.assertRaisesRegex(VMError, "No usable Docker or Podman"):
                resolve_runtime("auto")
        parser = build_parser()
        self.assertEqual(parser.parse_args(["vm", "container", "doctor"]).runtime, "auto")
        self.assertEqual(parser.parse_args(["re", "setup", "linux"]).runtime, "auto")
        self.assertEqual(parser.parse_args(["vm", "container", "doctor", "--runtime", "docker"]).runtime, "docker")


if __name__ == "__main__":
    unittest.main()



def test_dedicated_executor_build_and_pull_without_full_dependencies():
    from kitt.vm_container import IMAGES, RE_EXECUTOR_VERSION
    from unittest.mock import patch
    from types import SimpleNamespace
    from kitt.vm_container import build_image, pull_image
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"")

    with patch("kitt.vm_container._runtime", return_value="docker"), \
         patch("kitt.vm_container.subprocess.run", side_effect=fake_run):
        for tool in ("ghidra", "frida"):
            result = build_image(runtime="docker", image=tool)
            cmd = seen[-1]
            assert result["image"] == IMAGES[tool]
            assert cmd[cmd.index("--target") + 1] == tool
            result = pull_image(runtime="docker", image=tool)
            assert result["local_image"] == IMAGES[tool]
            assert ("ghcr.io/rfdetoni/kitt-re-" + tool + ":" +
                    RE_EXECUTOR_VERSION) in seen[-2]


def test_dedicated_frida_ptrace_requires_optin_in_guest_only(tmp_path):
    from unittest.mock import patch
    from types import SimpleNamespace
    from kitt import vm_container
    from kitt.vm_supervisor import VMError
    import pytest

    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0)

    with patch("kitt.vm_container._runtime", return_value="docker"), \
         patch("kitt.vm_container._image_id", return_value="sha256:" + "f" * 64), \
         patch("kitt.vm_container.sys.stdin.isatty", return_value=True), \
         patch("kitt.vm_container.subprocess.run", side_effect=run):
        assert vm_container.toolbox(
            runtime="docker", profile="frida", workspace=tmp_path) == 0
        default_argv = seen[-1]
        assert "--cap-add=SYS_PTRACE" not in default_argv
        assert f"type=bind,src={tmp_path},dst=/workspace,readonly" in default_argv
        assert "--privileged" not in default_argv and "--pid=host" not in default_argv
        assert vm_container.toolbox(
            runtime="docker", profile="frida", workspace=tmp_path,
            allow_ptrace=True) == 0
        assert "--cap-add=SYS_PTRACE" in seen[-1]
        assert "--network" in seen[-1] and "none" in seen[-1]
        with pytest.raises(VMError):
            vm_container.toolbox(
                runtime="docker", profile="ghidra", workspace=tmp_path,
                allow_ptrace=True)
