import unittest
import tempfile
from pathlib import Path
from kitt.context_engine.agents_reader import HierarchicalAgentsReader
from kitt.skills.skill_manager import SkillManager

class TestPhase5Extensibility(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.agents_reader = HierarchicalAgentsReader(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_hierarchical_agents_reader(self):
        (self.root_path / "AGENTS.md").write_text("# Root Policy\nAlways use strict typing.\n", encoding='utf-8')
        sub_dir = self.root_path / "kitt" / "cli"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "AGENTS.md").write_text("# CLI Policy\nUse prompt_toolkit completion.\n", encoding='utf-8')

        rules = self.agents_reader.get_merged_agents_rules("kitt/cli/repl.py")
        self.assertIn("Root Policy", rules)
        self.assertIn("CLI Policy", rules)
        self.assertTrue(rules.find("Root Policy") < rules.find("CLI Policy"))

if __name__ == '__main__':
    unittest.main()
