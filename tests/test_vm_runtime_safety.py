"""Regression tests for operator-owned VM lifecycle, auth and offline snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.vm_container import _viewer_auth, _run_args, _http_ready, start_vm, snapshot
from kitt.vm_supervisor import VMError
from kitt import vm_broker


class VMRuntimeSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _viewer_auth(self.root)
        self.profile = self.root / "profile.json"
        self.profile.write_text("{}")
        self.info = {
            "name": "kitt-vm-test", "kind": "qcow2", "runtime": "podman",
            "source": str(self.root / "no-image.qcow2"), "source_sha256": "a"*64,
            "state": str(self.root), "os": "linux", "port": 18006,
            "memory_mib": 2048, "cpus": 2, "disk_gib": 32,
            "image_id": "sha256:" + "a"*64, "probe_port": 0
        }

    def test_viewer_secret_is_private_and_never_put_in_runtime_env(self):
        self.assertEqual((self.root / "viewer-password").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "vnc.htpasswd").stat().st_mode & 0o777, 0o600)
        self.assertIn("{SHA}", (self.root / "vnc.htpasswd").read_text())
        with patch("kitt.vm_container.os.access", return_value=False):
            args = _run_args(self.info, binary="podman", install=False, allow_tcg=True)
        joined = " ".join(args)
        self.assertNotIn((self.root / "viewer-password").read_text().strip(), joined)
        self.assertIn("kitt-vnc.htpasswd,readonly", joined)
        self.assertIn("--read-only", args)
        self.assertEqual(args[-1], self.info["image_id"])

    def test_start_waits_for_authenticated_http_readiness(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=b"container-id", stderr=b"")
        with patch("kitt.vm_container._read_profile", return_value=self.info), \
             patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container._image_id", return_value=self.info["image_id"]), \
             patch("kitt.vm_container._status", side_effect=["missing", "running"]), \
             patch("kitt.vm_container._http_ready", return_value=True) as ready, \
             patch("kitt.vm_container._run_args", return_value=["podman", "run", "image"]), \
             patch("kitt.vm_container.subprocess.run", side_effect=fake_run):
            result = start_vm(self.profile)
        self.assertEqual(result["status"], "ready")
        ready.assert_called_once()
        self.assertEqual(result["viewer"], "http://127.0.0.1:18006/vnc.html")
        self.assertEqual(calls[0][:2], ["podman", "run"])

    def test_start_refuses_concurrent_instance(self):
        with patch("kitt.vm_container._read_profile", return_value=self.info), \
             patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container._image_id", return_value=self.info["image_id"]), \
             patch("kitt.vm_container._status", return_value="running"), \
             patch("kitt.vm_container.subprocess.run") as run:
            with self.assertRaises(VMError):
                start_vm(self.profile)
            run.assert_not_called()

    def test_snapshot_roundtrip_and_unsafe_restore_while_running(self):
        disk = self.root / "disk.qcow2"
        disk.write_bytes(b"guest disk initial state")
        (self.root / "OVMF_VARS.fd").write_bytes(b"initial uefi")
        tpm = self.root / "tpm"
        tpm.mkdir()
        (tpm / "data").write_text("initial tpm")
        with patch("kitt.vm_container._read_profile", return_value=self.info), \
             patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container._status", return_value="missing"):
            saved = snapshot(self.profile, action="save", name="clean")
            self.assertTrue(Path(saved["saved"]).is_dir())
            disk.write_bytes(b"modified")
            (self.root / "OVMF_VARS.fd").write_bytes(b"modified uefi")
            restored = snapshot(self.profile, action="restore", name="clean")
            self.assertEqual(restored["disk_sha256"], hashlib.sha256(b"guest disk initial state").hexdigest())
            self.assertEqual(disk.read_bytes(), b"guest disk initial state")
            self.assertEqual((self.root / "OVMF_VARS.fd").read_bytes(), b"initial uefi")
        with patch("kitt.vm_container._read_profile", return_value=self.info), \
             patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container._status", return_value="running"):
            with self.assertRaises(VMError):
                snapshot(self.profile, action="restore", name="clean")

    def test_snapshot_rejects_tpm_symlinks_without_copying_outside_files(self):
        (self.root / "disk.qcow2").write_bytes(b"guest disk")
        outside = self.root / "secret"
        outside.write_text("do not include in VM snapshot")
        tpm = self.root / "tpm"
        tpm.mkdir()
        (tpm / "redirect").symlink_to(outside)
        with patch("kitt.vm_container._read_profile", return_value=self.info), \
             patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container._status", return_value="missing"):
            with self.assertRaisesRegex(VMError, "Unsafe file"):
                snapshot(self.profile, action="save", name="unsafe")
        self.assertFalse((self.root / "snapshots" / "unsafe").exists())

    def test_snapshot_restore_rejects_tampered_state_before_disk_change(self):
        disk = self.root / "disk.qcow2"
        disk.write_bytes(b"original")
        tpm = self.root / "tpm"
        tpm.mkdir()
        (tpm / "data").write_text("original tpm")
        with patch("kitt.vm_container._read_profile", return_value=self.info), \
             patch("kitt.vm_container._runtime", return_value="podman"), \
             patch("kitt.vm_container._status", return_value="missing"):
            saved = snapshot(self.profile, action="save", name="clean")
            disk.write_bytes(b"changed")
            captured_tpm = Path(saved["saved"]) / "tpm"
            (captured_tpm / "data").unlink()
            (captured_tpm / "redirect").symlink_to(self.root / "disk.qcow2")
            with self.assertRaisesRegex(VMError, "Unsafe file"):
                snapshot(self.profile, action="restore", name="clean")
        self.assertEqual(disk.read_bytes(), b"changed")

    def test_cli_lab_broker_and_snapshots_stay_explicit(self):
        parser = build_parser()
        vm = parser.parse_args(["vm", "lab", "doctor", "--hosts", str(self.profile)])
        self.assertEqual(vm.lab_action, "doctor")
        snapshot_arg = parser.parse_args(["vm", "container", "snapshot",
                                         "--profile", str(self.profile),
                                         "--action", "restore", "--yes"])
        self.assertEqual(snapshot_arg.action, "restore")
        self.assertTrue(snapshot_arg.yes)
        cred = parser.parse_args(["vm", "container", "credentials", "--profile", str(self.profile)])
        self.assertEqual(cred.container_action, "credentials")
        ordinary = parser.parse_args(["-p", "Fix this Java class"])
        self.assertNotEqual(ordinary.subcommand, "vm")
        with patch("kitt.extensions.builtin_plugins.reverse_engineering.mission.run_mission",
                   return_value={"blocked": 0, "matched": 1}) as runner:
            result = vm_broker.run_lab_mission(workspace=self.root, mission=self.profile, hosts=self.profile)
        self.assertEqual(result["matched"], 1)
        runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
