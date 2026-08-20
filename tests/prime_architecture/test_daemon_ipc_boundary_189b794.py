from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kitt.daemon.transport import IPCTransport


@unittest.skipIf(os.name == "nt", "POSIX filesystem semantics")
class TestDaemonIPCBoundary(unittest.TestCase):
    def test_workspace_kitt_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            external = base / "external"
            workspace.mkdir()
            external.mkdir()
            marker = external / "marker.txt"
            marker.write_text("keep", encoding="utf-8")
            (workspace / ".kitt").symlink_to(
                external,
                target_is_directory=True,
            )

            with self.assertRaises(PermissionError):
                IPCTransport(workspace)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_secret_symlink_is_rejected_without_reading_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            external = Path(tmp) / "external-token"
            external.write_text("secret-value", encoding="utf-8")
            os.chmod(external, 0o600)
            transport.token_file.symlink_to(external)

            with self.assertRaises(PermissionError):
                transport.read_secret(transport.token_file)

    def test_secret_fifo_is_rejected_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            os.mkfifo(transport.token_file, 0o600)
            with self.assertRaises(PermissionError):
                transport.read_secret(transport.token_file)

    def test_secure_write_refuses_fifo_without_truncating_or_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            os.mkfifo(transport.pid_file, 0o600)
            with self.assertRaises(PermissionError):
                transport.secure_write(transport.pid_file, "123")

    def test_endpoint_metadata_world_readable_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            transport.endpoint_file.write_text(
                '{"transport_type":"tcp","address":"127.0.0.1",'
                '"port":12345,"pid":123}',
                encoding="utf-8",
            )
            os.chmod(transport.endpoint_file, 0o644)

            self.assertIsNone(transport.read_endpoint_metadata())

    def test_endpoint_metadata_rejects_non_loopback_tcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            transport.secure_write(
                transport.endpoint_file,
                '{"transport_type":"tcp","address":"10.0.0.7",'
                '"port":12345,"pid":123}',
            )
            self.assertIsNone(transport.read_endpoint_metadata())

    def test_cleanup_does_not_follow_replaced_kitt_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            original = workspace / ".kitt-real"
            transport.kitt_dir.rename(original)

            external = base / "external"
            external.mkdir()
            marker = external / "daemon.pid"
            marker.write_text("do-not-delete", encoding="utf-8")
            (workspace / ".kitt").symlink_to(
                external,
                target_is_directory=True,
            )

            transport.cleanup()

            self.assertEqual(
                marker.read_text(encoding="utf-8"),
                "do-not-delete",
            )

    def test_stale_lock_symlink_does_not_read_or_delete_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            workspace.mkdir()
            transport = IPCTransport(workspace)

            target = base / "external-lock-target"
            target.write_text("99999999", encoding="utf-8")
            os.chmod(target, 0o600)
            transport.lock_file.symlink_to(target)

            fd = transport.acquire_instance_lock()
            try:
                self.assertEqual(
                    target.read_text(encoding="utf-8"),
                    "99999999",
                )
                self.assertFalse(transport.lock_file.is_symlink())
            finally:
                transport.release_instance_lock(fd)


if __name__ == "__main__":
    unittest.main()
