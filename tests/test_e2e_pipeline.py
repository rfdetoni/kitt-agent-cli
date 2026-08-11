import unittest
import tempfile
from pathlib import Path
from kitt.core.turn_processor import TurnProcessor
from kitt.edit_format.applier import DiffApplier
from kitt.domain.entities import EditBlock

class TestE2EPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.processor = TurnProcessor(root_dir=self.tmp_dir.name)
        self.applier = DiffApplier()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_turn_processor_e2e(self):
        # Create a sample python file
        app_file = self.root_path / "app.py"
        app_file.write_text("def hello(): return 'world'\n", encoding='utf-8')

        # Run turn processor
        turn_res = self.processor.process("Refactor app.py to return hello K.I.T.T.", explicit_files={"app.py"})

        self.assertIsNotNone(turn_res["filter_res"])
        self.assertIsNotNone(turn_res["allocated"])
        self.assertIsNotNone(turn_res["request"])
        self.assertEqual(turn_res["request"].max_output_tokens, 1200)

        # Apply edit block
        block = EditBlock(
            file_path="app.py",
            search_content="def hello(): return 'world'",
            replace_content="def hello(): return 'hello K.I.T.T.'"
        )
        res = self.applier.apply([block], root_dir=self.tmp_dir.name)
        self.assertTrue(res.success)
        self.assertEqual(app_file.read_text(), "def hello(): return 'hello K.I.T.T.'\n")

if __name__ == '__main__':
    unittest.main()
