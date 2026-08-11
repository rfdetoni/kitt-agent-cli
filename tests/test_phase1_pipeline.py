import unittest
import tempfile
from pathlib import Path
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    TurnStarted, FilterCompleted, ContextResolved, BudgetApplied,
    ModelSelected, TextDelta, TurnCompleted
)
from tests.test_fake_llm_e2e import FakeLLMClient

class TestPhase1Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()

        filter_json = '{"intent": "DEBUG", "confidence": 0.95, "constraints": [{"text": "no breaking changes", "kind": "MANDATORY"}]}'
        exe_response = "I analyzed the issue.\n"

        self.context_client = FakeLLMClient([filter_json])
        self.execution_client = FakeLLMClient([exe_response])

        self.processor = TurnProcessor(
            root_dir=self.tmp_dir.name,
            context_client=self.context_client,
            execution_client=self.execution_client
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_run_turn_generator_events(self):
        cmd = TurnCommand(conversation_id="conv-1", prompt="Fix bug in repl.py no breaking changes")
        events = list(self.processor.run_turn(cmd))

        event_types = [type(e) for e in events]
        self.assertIn(TurnStarted, event_types)
        self.assertIn(FilterCompleted, event_types)
        self.assertIn(ContextResolved, event_types)
        self.assertIn(BudgetApplied, event_types)
        self.assertIn(ModelSelected, event_types)
        self.assertIn(TurnCompleted, event_types)

if __name__ == '__main__':
    unittest.main()
