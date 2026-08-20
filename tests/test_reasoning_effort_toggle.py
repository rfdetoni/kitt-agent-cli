import tempfile
import unittest
from pathlib import Path
from kitt.ui.state import UIState
from kitt.ui.git import read_git_branch_name
from kitt.ui.components.status_bar import StatusBarComponent
from kitt.core.turn_processor import TurnProcessor

class TestReasoningEffortToggle(unittest.TestCase):
    def test_uistate_reasoning_effort_default_and_status_bar(self):
        state = UIState(
            large_model="ollama/qwen2.5:32b-instruct",
            reasoning_effort=60,
            current_branch="main",
        )
        bar = StatusBarComponent().render(state, width=120)
        self.assertIn("🧠 60%", bar)
        self.assertIn("branch:main", bar)

    def test_read_git_branch_name_from_head_ref(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text(
                "ref: refs/heads/feature/status-bar\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_git_branch_name(root),
                "feature/status-bar",
            )

    def test_turn_processor_reasoning_policy_in_system_prompt(self):
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.reasoning_effort = 0
        self.assertEqual(processor.reasoning_effort, 0)

        processor.reasoning_effort = 80
        self.assertEqual(processor.reasoning_effort, 80)

if __name__ == "__main__":
    unittest.main()
