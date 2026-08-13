import tempfile
import unittest
from pathlib import Path
from kitt.core.autonomy_store import AutonomyStore

class TestAutonomyStore(unittest.TestCase):
    def test_store_persistence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store1 = AutonomyStore(tmp_dir, persistence_enabled=True)
            self.assertEqual(store1.get().level, "supervised")

            store1.set_level("balanced")
            self.assertEqual(store1.get().level, "balanced")

            # Verify persisted file on disk
            config_file = Path(tmp_dir) / ".kitt" / "config" / "autonomy.json"
            self.assertTrue(config_file.exists())

            # Load new instance from disk
            store2 = AutonomyStore(tmp_dir, persistence_enabled=True)
            self.assertEqual(store2.get().level, "balanced")
            self.assertTrue(store2.get().allow_file_write_auto)

if __name__ == "__main__":
    unittest.main()
