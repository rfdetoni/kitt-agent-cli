import unittest
import tempfile
from pathlib import Path
from kitt.domain.entities import ModelProfile
from kitt.core.turn_processor import TurnProcessor

class FakeLLMClient:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.last_resp = responses[0] if responses else ""
        self.calls = []

    def chat(self, messages, system_prompt=None, response_format=None):
        self.calls.append({"messages": messages, "system_prompt": system_prompt, "format": response_format})
        if self.responses:
            self.last_resp = self.responses.pop(0)
        return self.last_resp

    def chat_stream(self, messages, system_prompt=None, response_format=None):
        res = self.chat(messages, system_prompt, response_format)
        yield res

class TestFakeLLME2E(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_decoupled_full_turn_with_fake_llms(self):
        app_file = self.root_path / "app.py"
        app_file.write_text("def hello(): return 'world'\n", encoding='utf-8')

        context_json_response = """{
            "intent": "IMPLEMENT",
            "symbols": ["hello"],
            "paths": ["app.py"],
            "constraints": [
                {
                    "text": "return hello K.I.T.T.",
                    "kind": "MANDATORY"
                }
            ],
            "confidence": 0.95
        }"""

        execution_patch_response = """app.py
<<<<<<< SEARCH
def hello(): return 'world'
=======
def hello(): return 'hello K.I.T.T.'
>>>>>>> REPLACE
"""
        fake_context_llm = FakeLLMClient([context_json_response])
        fake_execution_llm = FakeLLMClient([execution_patch_response])

        events_received = []
        def event_cb(name, payload):
            events_received.append((name, payload))

        processor = TurnProcessor(
            root_dir=self.tmp_dir.name,
            context_client=fake_context_llm,
            execution_client=fake_execution_llm,
            event_callback=event_cb
        )

        res = processor.execute_full_turn("Please update app.py to return hello K.I.T.T.", explicit_files={"app.py"})

        self.assertIsNotNone(res["edit_result"])
        self.assertTrue(res["edit_result"].success)
        self.assertEqual(app_file.read_text(), "def hello(): return 'hello K.I.T.T.'\n")

        # Verify events emitted
        event_names = [e[0] for e in events_received]
        self.assertIn("FilterCompleted", event_names)
        self.assertIn("BudgetApplied", event_names)
        self.assertIn("ModelSelected", event_names)

if __name__ == '__main__':
    unittest.main()
