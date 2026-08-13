import tempfile
import unittest
from pathlib import Path
from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.history.database import HistoryDatabase

class TestChildrenEvents(unittest.TestCase):
    def test_child_agent_events_published(self):
        events_received = []

        def mock_callback(name, payload):
            events_received.append((name, payload))

        with tempfile.TemporaryDirectory() as tmp_dir:
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

if __name__ == "__main__":
    unittest.main()
