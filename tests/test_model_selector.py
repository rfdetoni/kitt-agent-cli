import unittest
import tempfile
import json
from pathlib import Path
from kitt.router.model_selector import ModelConfigurator

class TestModelConfigurator(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.configurator = ModelConfigurator(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_assign_roles_mutually_exclusive_with_main_chat(self):
        model_a = "qwen2.5:7b-instruct"
        model_b = "qwen2.5:32b-instruct"

        # Model A gets main_chat, context, and commit
        config = self.configurator.assign_roles(
            model_a=model_a,
            model_b=model_b,
            model_a_roles=["main_chat", "context", "commit"]
        )

        # Check config persisted to disk
        config_path = Path(self.tmp_dir.name) / ".kitt-router.json"
        self.assertTrue(config_path.exists())

        saved = json.loads(config_path.read_text(encoding='utf-8'))

        # Model A profiles and routing
        self.assertEqual(saved["profiles"]["model_a"]["model"], model_a)
        self.assertEqual(saved["profiles"]["model_b"]["model"], model_b)

        self.assertEqual(saved["routing"]["chat"], "model_a")
        self.assertEqual(saved["routing"]["context-gather"], "model_a")
        self.assertEqual(saved["routing"]["summarize"], "model_a")
        self.assertEqual(saved["routing"]["validate-diff"], "model_a")

        # Model B automatically gets edit and code_generation (mutually exclusive)
        self.assertEqual(saved["routing"]["code-edit"], "model_b")
        self.assertEqual(saved["routing"]["code-generation"], "model_b")

if __name__ == '__main__':
    unittest.main()
