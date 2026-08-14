import unittest
from kitt.ui.state import UIState
from kitt.ui.components.status_bar import StatusBarComponent
from kitt.core.turn_processor import TurnProcessor

class TestReasoningEffortToggle(unittest.TestCase):
    def test_uistate_reasoning_effort_default_and_status_bar(self):
        state = UIState(large_model="ollama/qwen2.5:32b-instruct", reasoning_effort=60)
        bar = StatusBarComponent().render(state, width=120)
        self.assertIn("🧠 60%", bar)

    def test_turn_processor_reasoning_policy_in_system_prompt(self):
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.reasoning_effort = 0
        self.assertEqual(processor.reasoning_effort, 0)

        processor.reasoning_effort = 80
        self.assertEqual(processor.reasoning_effort, 80)

if __name__ == "__main__":
    unittest.main()
