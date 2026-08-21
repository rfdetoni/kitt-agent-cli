from __future__ import annotations

import hashlib
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kitt.core.turn_execution_guard import TurnExecutionGuard
from kitt.core.turn_processor import TurnProcessor
from kitt.index.scanner import RepositoryScanner
from kitt.remote.auth import PairingAuth
from kitt.security.context import ExecutionSecurityContext
from kitt.security.workspace_fs import WorkspaceFileSystem


class Round2HardeningTests(unittest.TestCase):
    def test_scanner_rejects_intermediate_symlink_even_if_git_reports_child(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            root_path = Path(root)
            outside_path = Path(outside)
            (outside_path / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
            link_dir = root_path / "linked"
            try:
                link_dir.symlink_to(outside_path, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlink creation unavailable")

            scanner = RepositoryScanner(root_path)
            scanner._git_files = lambda timeout=5.0: ["linked/secret.py"]  # type: ignore[method-assign]
            self.assertNotIn("linked/secret.py", scanner.scan_relative_files())

    @unittest.skipIf(os.name == "nt", "POSIX dir_fd race test")
    def test_atomic_write_expected_exists_revalidates_target_before_replace(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "state.txt"
            target.write_text("old", encoding="utf-8")
            fs = WorkspaceFileSystem(root_path)
            expected = hashlib.sha256(b"old").hexdigest()
            real_stat = os.stat

            def fake_stat(path, *args, **kwargs):
                if (
                    path == "state.txt"
                    and kwargs.get("dir_fd") is not None
                    and kwargs.get("follow_symlinks") is False
                ):
                    raise FileNotFoundError(path)
                return real_stat(path, *args, **kwargs)

            with patch("kitt.security.workspace_fs.os.stat", side_effect=fake_stat):
                with self.assertRaises(ValueError):
                    fs.atomic_write(
                        "state.txt",
                        "new",
                        expected_exists=True,
                        expected_sha256=expected,
                    )
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    @unittest.skipIf(os.name == "nt", "POSIX inode race test")
    def test_atomic_write_rejects_changed_target_identity(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "state.txt"
            other = root_path / "other.txt"
            target.write_text("old", encoding="utf-8")
            other.write_text("other", encoding="utf-8")
            fs = WorkspaceFileSystem(root_path)
            expected = hashlib.sha256(b"old").hexdigest()
            real_stat = os.stat

            def fake_stat(path, *args, **kwargs):
                if (
                    path == "state.txt"
                    and kwargs.get("dir_fd") is not None
                    and kwargs.get("follow_symlinks") is False
                ):
                    return real_stat(other)
                return real_stat(path, *args, **kwargs)

            with patch("kitt.security.workspace_fs.os.stat", side_effect=fake_stat):
                with self.assertRaises(ValueError):
                    fs.atomic_write("state.txt", "new", expected_sha256=expected)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

    @unittest.skipIf(os.name == "nt", "POSIX inode race test")
    def test_unlink_rejects_changed_target_identity(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            target = root_path / "state.txt"
            other = root_path / "other.txt"
            target.write_text("old", encoding="utf-8")
            other.write_text("other", encoding="utf-8")
            fs = WorkspaceFileSystem(root_path)
            expected = hashlib.sha256(b"old").hexdigest()
            real_stat = os.stat

            def fake_stat(path, *args, **kwargs):
                if (
                    path == "state.txt"
                    and kwargs.get("dir_fd") is not None
                    and kwargs.get("follow_symlinks") is False
                ):
                    return real_stat(other)
                return real_stat(path, *args, **kwargs)

            with patch("kitt.security.workspace_fs.os.stat", side_effect=fake_stat):
                with self.assertRaises(ValueError):
                    fs.unlink("state.txt", expected_sha256=expected)
            self.assertTrue(target.exists())

    def test_remote_auth_treats_ipv4_mapped_ipv6_as_same_client(self):
        auth = PairingAuth(pairing_ttl_seconds=60, session_ttl_seconds=300)
        result = auth.pair(auth.pairing_code, "127.0.0.1")
        self.assertIsNotNone(result)
        token, csrf, _ = result
        self.assertIsNotNone(auth.authenticate(token, "::ffff:127.0.0.1"))
        self.assertTrue(auth.validate_csrf(token, csrf, "::ffff:127.0.0.1"))
        self.assertIsNone(auth.authenticate(token, "10.0.0.7"))

    def test_uv_lock_metadata_matches_pyproject(self):
        root = Path(__file__).resolve().parents[2]
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["requires-python"], pyproject["project"]["requires-python"])
        package = next(p for p in lock["package"] if p["name"] == "kitt-agent-cli")
        self.assertEqual(package["version"], pyproject["project"]["version"])
        metadata = package["metadata"]["requires-dist"]
        by_name = {item["name"]: item.get("specifier", "") for item in metadata}
        self.assertEqual(by_name["pytest"], ">=8.0")
        self.assertEqual(by_name["pytest-asyncio"], ">=0.23")

    def test_cancel_reports_inflight_state_atomically(self):
        guard = TurnExecutionGuard()
        self.assertFalse(guard.cancel("waiting"))
        self.assertFalse(guard.begin("waiting"))

        self.assertTrue(guard.begin("running"))
        self.assertTrue(guard.cancel("running"))
        guard.end("running")
        self.assertFalse(guard.begin("running"))

    def test_cancelled_approval_is_not_consumed_before_execution_barrier(self):
        class FakeRepo:
            def __init__(self, pending):
                self.pending = pending
                self.consume_calls = 0

            def get_valid_pending_action(self, _id, _workspace):
                return self.pending

            def consume_pending_action(self, _id):
                self.consume_calls += 1
                return True

        class FakeApprovalManager:
            @staticmethod
            def is_nonce_used(_nonce):
                return False

        turn_id = "turn-1"
        conversation_id = "conversation-1"
        workspace_id = "workspace-1"
        normalized_args = {}
        args_digest = TurnProcessor._args_digest(normalized_args)
        sec = ExecutionSecurityContext.create_user_context(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            capabilities=(),
        )
        pending = SimpleNamespace(
            id=f"pa_{turn_id}",
            state="pending",
            expires_at=10**12,
            approval_request_id="approval-1",
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            action_hash="hash-1",
            normalized_args=normalized_args,
            source_response_sha256=args_digest,
            security_context=sec.to_dict(),
            get_preconditions=lambda: [],
        )
        grant = SimpleNamespace(
            approval_id="approval-1",
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            action_hash="hash-1",
            expires_at=10**12,
            nonce="nonce-1",
        )
        repo = FakeRepo(pending)
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.pending_actions = {turn_id: pending}
        processor.history_service = SimpleNamespace(repo=repo)
        processor.registry = SimpleNamespace(
            approval_manager=FakeApprovalManager(),
        )
        processor.turn_guard = TurnExecutionGuard(set())
        processor.cancelled_turns = processor.turn_guard._cancelled
        processor.root_path = Path(".").resolve()
        processor._workspace_id = workspace_id
        processor.turn_guard.cancel(turn_id)

        events = list(processor.continue_turn(turn_id, grant))
        self.assertTrue(events)
        self.assertEqual(repo.consume_calls, 0)


if __name__ == "__main__":
    unittest.main()
