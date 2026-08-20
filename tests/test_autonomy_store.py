import os
import tempfile
import unittest
from pathlib import Path

from kitt.core.autonomy_store import AutonomyStore
from kitt.security.private_state import workspace_state_dir

class TestAutonomyStore(unittest.TestCase):
    def test_store_persistence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            kitt_home = Path(tmp_dir) / "private-home"
            kitt_home.mkdir(mode=0o700)
            old_home = os.environ.get("KITT_HOME")
            os.environ["KITT_HOME"] = str(kitt_home)
            self.addCleanup(
                lambda: (
                    os.environ.__setitem__("KITT_HOME", old_home)
                    if old_home is not None
                    else os.environ.pop("KITT_HOME", None)
                )
            )
            store1 = AutonomyStore(tmp_dir, persistence_enabled=True)
            self.assertEqual(store1.get().level, "supervised")

            store1.set_level("balanced")
            self.assertEqual(store1.get().level, "balanced")

            config_file = workspace_state_dir(tmp_dir, "config") / "autonomy.json"
            self.assertTrue(config_file.exists())
            self.assertFalse(str(config_file).startswith(str(Path(tmp_dir) / ".kitt")))

            store2 = AutonomyStore(tmp_dir, persistence_enabled=True)
            self.assertEqual(store2.get().level, "balanced")
            self.assertTrue(store2.get().allow_file_write_auto)

if __name__ == "__main__":
    unittest.main()
