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

    async def achat_stream(self, messages, system_prompt=None, response_format=None):
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

        from kitt.core.turn_command import TurnCommand
        from kitt.core.turn_events import ApprovalRequired, EditApplied, TurnCompleted, TurnFailed
        
        cmd = TurnCommand(conversation_id="conv-1", prompt="Please update app.py to return hello K.I.T.T.", explicit_files={"app.py"})
        
        edit_result = None
        for event in processor.run_turn(cmd):
            if isinstance(event, ApprovalRequired):
                # Simulate user approval
                grant = processor.registry.approval_manager.issue_grant(
                    cmd.turn_id, cmd.conversation_id, "local", event.action_hash
                )
                for sub_event in processor.continue_turn(cmd.turn_id, grant):
                    if isinstance(sub_event, EditApplied):
                        pass
                    elif isinstance(sub_event, TurnCompleted):
                        edit_result = sub_event.edit_result
                    elif isinstance(sub_event, TurnFailed):
                        print(f"Sub-event failed: {sub_event.error}")
            elif isinstance(event, TurnCompleted):
                edit_result = event.edit_result
            elif isinstance(event, TurnFailed):
                print(f"Event failed: {event.error}")

        self.assertIsNotNone(edit_result)
        self.assertTrue(edit_result.success)
        self.assertEqual(app_file.read_text(), "def hello(): return 'hello K.I.T.T.'\n")

        # Verify events emitted
        event_names = [e[0] for e in events_received]
        self.assertIn("FilterCompleted", event_names)
        self.assertIn("BudgetApplied", event_names)
        self.assertIn("ModelSelected", event_names)

        # Provas de Fase P0
        self.assertEqual(len(fake_context_llm.calls), 1, "Context LLM deve ser chamada exatamente uma vez.")
        self.assertEqual(len(fake_execution_llm.calls), 1, "Execution LLM deve ser chamada exatamente uma vez. A continuação via grant não pode rodar a LLM novamente.")

    def test_forged_grant_is_rejected(self):
        from kitt.tools.approval import ApprovalGrant
        from kitt.core.pending_action import PendingAction
        
        processor = TurnProcessor(root_dir=self.tmp_dir.name)
        processor.pending_actions["turn_x"] = PendingAction(
            id="1", approval_request_id="req_1", turn_id="turn_x", conversation_id="c1", workspace_id="ws1",
            tool_name="apply_patch", normalized_args={}, action_hash="real_hash", 
            source_response_sha256="abc", affected_paths=[], before_hashes={},
            created_at=100.0, expires_at=200.0, state="pending"
        )
        
        # Forged grant with fake action hash
        forged_grant = ApprovalGrant(
            approval_id="forged",
            turn_id="turn_x",
            conversation_id="c1",
            workspace_id="ws1",
            action_hash="fake_hash",
            granted_at=0,
            expires_at=9999999999,
            nonce="fake_nonce"
        )
        
        from kitt.core.turn_events import TurnFailed
        result_event = next(processor.continue_turn("turn_x", forged_grant))
        self.assertIsInstance(result_event, TurnFailed)
        self.assertIn("requires explicit user confirmation (ASK policy)", result_event.error)

    def test_user_rejection_does_not_modify_files(self):
        processor = TurnProcessor(root_dir=self.tmp_dir.name)
        processor.pending_actions["turn_y"] = "some_action"
        
        from kitt.core.turn_events import TurnCancelled
        result_event = next(processor.cancel_turn("turn_y", "User rejected"))
        
        self.assertIsInstance(result_event, TurnCancelled)
        self.assertEqual(result_event.reason, "User rejected")
        self.assertNotIn("turn_y", processor.pending_actions)

if __name__ == '__main__':
    unittest.main()
