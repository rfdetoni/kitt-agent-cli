import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.history.database import HistoryDatabase

class _RacingProcess:
    def __init__(self, *, timeout_on_wait=False):
        self.pid = 424242
        self.timeout_on_wait = timeout_on_wait
        self.kill_calls = 0

    def poll(self):
        return None

    def wait(self, timeout=None):
        if self.timeout_on_wait:
            raise subprocess.TimeoutExpired(cmd="child", timeout=timeout)
        return 0

    def kill(self):
        self.kill_calls += 1


class TestChildProcessTeardown(unittest.TestCase):
    def test_kill_tree_tolerates_process_group_exiting_before_sigterm(self):
        process = _RacingProcess()
        with (
            patch("kitt.children.lifecycle.sys.platform", "linux"),
            patch(
                "kitt.children.lifecycle.os.killpg",
                side_effect=ProcessLookupError(3, "No such process"),
                create=True,
            ),
        ):
            ChildAgentManager._kill_tree(process)

        self.assertEqual(process.kill_calls, 1)

    def test_kill_tree_tolerates_process_group_exiting_before_sigkill(self):
        process = _RacingProcess(timeout_on_wait=True)
        signals = []

        def killpg(_pid, sig):
            signals.append(sig)
            if len(signals) == 2:
                raise ProcessLookupError(3, "No such process")

        with (
            patch("kitt.children.lifecycle.sys.platform", "linux"),
            patch("kitt.children.lifecycle.os.killpg", side_effect=killpg, create=True),
            patch("kitt.children.lifecycle.signal.SIGKILL", 9, create=True),
        ):
            ChildAgentManager._kill_tree(process)

        self.assertEqual(len(signals), 2)
        self.assertEqual(process.kill_calls, 1)


class TestChildrenEvents(unittest.TestCase):
    def test_child_agent_events_published(self):
        events_received = []

        def mock_callback(name, payload):
            events_received.append((name, payload))

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            db_path = Path(tmp_dir) / "kitt.db"
            db = HistoryDatabase(str(db_path))
            repo = ChildRepository(db)
            artifacts = ArtifactStore(tmp_dir, db)

            from kitt.history.repository import HistoryRepository
            hist_repo = HistoryRepository(db)
            ws = hist_repo.get_or_create_workspace(tmp_dir)
            conv = hist_repo.create_conversation(ws["id"], "Parent Conv")

            manager = ChildAgentManager(
                root_dir=tmp_dir,
                repository=repo,
                artifacts=artifacts,
                workspace_id=ws["id"],
                event_callback=mock_callback,
            )

            child = manager.spawn(
                parent_conversation_id=conv["id"],
                parent_turn_id="turn-1",
                name="test_worker_child",
                task="test task execution",
                worker=lambda t: "result ok",
            )

            manager.wait(child.id, timeout=5.0)

            # Check that events were received in order
            event_names = [e[0] for e in events_received]
            self.assertIn("ChildAgentSpawned", event_names)
            self.assertIn("ChildAgentProgress", event_names)
            self.assertIn("ChildAgentFinished", event_names)

            spawned = next(e[1] for e in events_received if e[0] == "ChildAgentSpawned")
            self.assertEqual(spawned["child_id"], child.id)
            self.assertEqual(spawned["name"], "test_worker_child")

            finished = next(e[1] for e in events_received if e[0] == "ChildAgentFinished")
            self.assertEqual(finished["child_id"], child.id)
            self.assertEqual(finished["status"], "COMPLETED")
            self.assertIsNone(finished["error"])

            manager.close()



class _HeartbeatRepo:
    def get(self, _child_id):
        return SimpleNamespace(state="RUNNING")


class _HeartbeatCoordinator:
    def __init__(self, state_root):
        self.state_root = Path(state_root)
        self.refreshes = 0
        self.releases = 0

    def refresh_owner(self, _owner_id, ttl_seconds=180.0):
        self.refreshes += 1
        return 1

    def gc_expired_leases(self):
        return 0

    def release_owner(self, _owner_id):
        self.releases += 1
        return 1


class TestChildLeaseKeeper(unittest.TestCase):
    def test_running_child_renews_and_releases_coordination_lease(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            coordinator = _HeartbeatCoordinator(tmp_dir)
            manager = ChildAgentManager(
                root_dir=tmp_dir,
                repository=_HeartbeatRepo(),
                artifacts=None,
                workspace_id="ws",
                messaging_repo=object(),
            )
            manager.LEASE_RENEW_INTERVAL_SECONDS = 0.01
            manager.attach_coordinator(coordinator)
            try:
                manager._ensure_lease_keeper("child-heartbeat")
                deadline = time.time() + 0.5
                while coordinator.refreshes == 0 and time.time() < deadline:
                    time.sleep(0.01)
                self.assertGreaterEqual(coordinator.refreshes, 1)
            finally:
                manager.close()
            self.assertGreaterEqual(coordinator.releases, 1)


if __name__ == "__main__":
    unittest.main()
