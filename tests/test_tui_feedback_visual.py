import unittest
from unittest.mock import patch
from kitt.ui.state import UIState, AgentTaskStep
from kitt.ui.reducer import reduce_ui_event
from kitt.core.turn_events import (
    TurnStarted, TurnCompleted, TurnFailed, ToolStarted, ToolCompleted,
    ThinkingStarted, ThinkingCompleted, ModelSelected,
    ChildAgentSpawned, ChildAgentFinished, EditApplied
)
from kitt.ui.components.status_bar import StatusBarComponent

class TestTUIVisualFeedback(unittest.TestCase):
    def test_turn_completed_visual_feedback(self):
        # Ordinary turn -> no toast popup
        state = UIState()
        reduce_ui_event(state, TurnCompleted())

        self.assertIn("✔ COMPLETED", state.status_text)
        self.assertFalse(any("PROCESSO CONCLUÍDO COM SUCESSO" in b.text for b in state.transcript))
        self.assertFalse(any("Processo concluído com sucesso" in t.text for t in state.active_toasts()))

        # Turn with child agent -> toast popup appears
        state_child = UIState()
        reduce_ui_event(state_child, ChildAgentSpawned(child_id="c1", name="sub", task="do task"))
        reduce_ui_event(state_child, TurnCompleted())
        self.assertTrue(any("Processo concluído com sucesso" in t.text for t in state_child.active_toasts()))

        # Turn with 3+ tools -> toast popup appears
        state_tools = UIState()
        for i in range(3):
            reduce_ui_event(state_tools, ToolStarted(tool_name=f"tool_{i}", call_id=f"id_{i}"))
        reduce_ui_event(state_tools, TurnCompleted())
        self.assertTrue(any("Processo concluído com sucesso" in t.text for t in state_tools.active_toasts()))

    def test_turn_failed_visual_feedback(self):
        state = UIState()
        reduce_ui_event(state, TurnFailed(error="SyntaxError on line 4"))

        self.assertIn("✖ FAILED", state.status_text)
        self.assertTrue(any("PROCESSO FALHOU" in b.text for b in state.transcript))
        self.assertTrue(any("Processo falhou" in t.text for t in state.active_toasts()))

        status_bar = StatusBarComponent().render(state, width=80)
        self.assertIn("✖ FALHA NO PROCESSO", status_bar)

    def test_child_agent_lifecycle_visual_feedback(self):
        state = UIState()
        reduce_ui_event(state, ChildAgentSpawned(child_id="c1", name="worker_1", task="Index repo"))

        self.assertTrue(any("SUBAGENTE INICIADO" in b.text for b in state.transcript))
        self.assertTrue(any("worker_1" in t.text for t in state.active_toasts()))

        reduce_ui_event(state, ChildAgentFinished(child_id="c1", status="COMPLETED"))

        self.assertTrue(any("SUBAGENTE CONCLUÍDO" in b.text for b in state.transcript))
        self.assertTrue(any("concluído" in t.text.lower() for t in state.active_toasts()))

    def test_thinking_started_and_completed_formatting(self):
        state = UIState()
        reduce_ui_event(state, TurnStarted(turn_id="t1", conversation_id="c1", prompt="Avalie o projeto"))
        reduce_ui_event(state, ThinkingStarted())
        self.assertEqual(state.transcript[-1].text, "▸ Pensando...")
        self.assertEqual(state.transcript[-1].status, "running")
        self.assertEqual(state.status_text, "THINKING")
        self.assertIn("aguardando primeira resposta visível", state.active_tasks[0].summary)

        reduce_ui_event(state, ThinkingCompleted(duration_ms=5000, tokens=161))
        self.assertEqual(state.transcript[-1].text, "▸ Thought for 5s, 161 tokens")
        self.assertEqual(state.transcript[-1].status, "done")

    def test_tool_duration_and_tokens_formatting(self):
        state = UIState()
        with patch("time.time") as mock_time:
            mock_time.return_value = 100.0
            reduce_ui_event(state, ToolStarted(tool_name="run_command", args={"command": "pytest"}, call_id="abc123"))
            mock_time.return_value = 102.1
            reduce_ui_event(state, ToolCompleted(tool_name="run_command", success=True, call_id="abc123", tokens=0))

        last_block = state.transcript[-1]
        self.assertIn("(2.1s)", last_block.text)
        self.assertTrue(last_block.text.endswith("✔"))
        self.assertNotIn(", 0 tok", last_block.text)

    def test_model_selected_preserves_context_and_principal_roles(self):
        state = UIState(small_model="context-model", large_model="principal-model")

        reduce_ui_event(state, ModelSelected(profile_name="context", model="new-context"))
        self.assertEqual(state.small_model, "new-context")
        self.assertEqual(state.large_model, "principal-model")

        reduce_ui_event(state, ModelSelected(profile_name="execute", model="new-principal"))
        self.assertEqual(state.small_model, "new-context")
        self.assertEqual(state.large_model, "new-principal")

    def test_simultaneous_tool_calls_call_id_isolation(self):
        state = UIState()
        reduce_ui_event(state, ToolStarted(tool_name="read_file", args={"path": "a.py"}, call_id="aaa"))
        block_a = state.transcript[-1]
        reduce_ui_event(state, ToolStarted(tool_name="read_file", args={"path": "b.py"}, call_id="bbb"))
        block_b = state.transcript[-1]

        # Complete bbb first
        reduce_ui_event(state, ToolCompleted(tool_name="read_file", success=True, output="b content", call_id="bbb"))

        self.assertEqual(block_a.status, "running")
        self.assertEqual(block_b.status, "done")
        self.assertIn("b.py", block_b.text)
        self.assertIn("a.py", block_a.text)

    def test_large_output_tool_collapsing(self):
        state = UIState()
        long_output = "x" * 500
        reduce_ui_event(state, ToolStarted(tool_name="run_command", args={"command": "cat long.txt"}, call_id="c1"))
        reduce_ui_event(state, ToolCompleted(tool_name="run_command", success=True, output=long_output, call_id="c1"))

        block = state.transcript[-1]
        self.assertTrue(block.collapsed)
        self.assertEqual(block.metadata["full_output"], long_output)

        state.toggle_last_tool_collapse()
        self.assertFalse(block.collapsed)

    def test_tool_call_proposed_progress_and_payload_tokens(self):
        from kitt.core.turn_events import ToolCallProposed

        state = UIState()
        reduce_ui_event(state, TurnStarted(turn_id="t1", conversation_id="c1", prompt="Crie arquivo"))
        reduce_ui_event(state, ThinkingCompleted(duration_ms=3000, tokens=50))
        self.assertEqual(state.status_text, "DEVELOPING")
        self.assertIn("Raciocínio concluído", state.active_tasks[0].summary)

        reduce_ui_event(state, ToolCallProposed(tool_name="write_file", args={"path": "apresenta2.html", "bytes": 2500}))
        self.assertIn("DEVELOPING: write_file", state.status_text)
        self.assertIn("apresenta2.html", state.active_tasks[0].summary)
        self.assertIn("2500 bytes", state.active_tasks[0].summary)

        reduce_ui_event(state, ToolStarted(tool_name="write_file", args={"path": "apresenta2.html"}, call_id="w1"))
        self.assertEqual(state.status_text, "TOOL: write_file")

        reduce_ui_event(state, ToolCompleted(tool_name="write_file", success=True, output="Successfully wrote 6000 bytes", call_id="w1", tokens=1450))
        last_block = state.transcript[-1]
        self.assertIn("1450 tok", last_block.text)
        self.assertTrue(last_block.text.endswith("✔"))


if __name__ == "__main__":
    unittest.main()
