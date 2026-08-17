import tempfile
import unittest
from pathlib import Path
from prompt_toolkit.document import Document

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.skills.discovery import SkillDiscovery
from kitt.ui.app import KittUIApp
from prompt_toolkit.output import DummyOutput


class TestSkillCompletions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        
        # Create a mock skill directory with main skill and subskills
        skill_dir = self.root / ".kitt" / "skills" / "my-helper"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-helper\ndescription: A test helper skill\n---\n"
            "# My Helper\n"
            "Use /my-helper:fast for fast mode, or /my-helper-sub for sub task.\n"
            "intensity level: lite, full, ultra\n",
            encoding="utf-8"
        )

        self.runtime = KittRuntime.build(str(self.root), RuntimeConfig(history_enabled=False, persistence_enabled=True))
        self.ui = KittUIApp(self.runtime, "tui", output=DummyOutput(), no_animation=True)
        self.ui._build_controls()

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_skill_discovery_extracts_subskills_and_modes(self):
        discovery = SkillDiscovery()
        completions = dict(discovery.get_skill_completions([self.root / ".kitt" / "skills"]))
        
        self.assertIn("/my-helper", completions)
        self.assertIn("/my-helper:fast", completions)
        self.assertIn("/my-helper-sub", completions)
        self.assertIn("/my-helper:ultra", completions)

    def test_ui_completer_suggests_skills_and_subskills(self):
        completer = self.ui.prompt_buffer.completer
        
        # 1. Autocomplete for /my
        doc = Document("/my", 3)
        completions = [c.text for c in completer.get_completions(doc, None)]
        self.assertIn("/my-helper", completions)
        self.assertIn("/my-helper:fast", completions)
        self.assertIn("/my-helper-sub", completions)

        # 2. Autocomplete for @my
        doc_at = Document("@my", 3)
        at_completions = [c.text for c in completer.get_completions(doc_at, None)]
        self.assertIn("@my-helper", at_completions)


if __name__ == "__main__":
    unittest.main()
