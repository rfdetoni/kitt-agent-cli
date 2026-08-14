import unittest
from kitt.ui.state import UIState
from kitt.ui.reducer import reduce_ui_event, format_tool_bullet
from kitt.core.turn_events import ToolStarted, ToolCompleted

class TestBulletToolVisualFeedback(unittest.TestCase):
    def test_format_tool_bullet_outputs(self):
        self.assertEqual(format_tool_bullet("search", {"pattern": "Grep run_turn", "path": "turn_processor.py"}), "● Search(Grep run_turn in turn_processor.py)")
        self.assertEqual(format_tool_bullet("read_file", {"path": "kitt/core/turn_processor.py"}), "● Read(kitt/core/turn_processor.py)")
        self.assertEqual(format_tool_bullet("write_file", {"path": "teste.py"}), "● Write(teste.py)")
        self.assertEqual(format_tool_bullet("run_command", {"command": "python3 -m unittest"}), "● Bash(python3 -m unittest)")

    def test_reducer_appends_bullet_lines(self):
        state = UIState()
        reduce_ui_event(state, ToolStarted(tool_name="search", args={"pattern": "Grep run_turn", "path": "turn_processor.py"}))

        self.assertEqual(len(state.transcript), 1)
        self.assertEqual(state.transcript[0].kind, "tool")
        self.assertEqual(state.transcript[0].text, "● Search(Grep run_turn in turn_processor.py)")

        reduce_ui_event(state, ToolCompleted(tool_name="search", success=True, output="10 matches"))
        self.assertEqual(state.transcript[0].status, "done")
        self.assertIn("● Search(Grep run_turn in turn_processor.py)", state.transcript[0].text)
        self.assertTrue(state.transcript[0].text.endswith("✔"))

if __name__ == "__main__":
    unittest.main()
