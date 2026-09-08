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
    def test_kitt_runtime_humanized_bullets(self):
        # repo.search
        self.assertEqual(
            format_tool_bullet("kitt_runtime", {"operation": "repo.search", "arguments": {"query": "def", "path": "backend"}}),
            "● Buscar: 'def' em backend"
        )
        # process.run
        self.assertEqual(
            format_tool_bullet("kitt_runtime", {"operation": "process.run", "arguments": {"command": "rtk find . -maxdepth 3 -type f | sort"}}),
            "● Executar: rtk find . -maxdepth 3 -type f | sort"
        )
        # artifacts.read
        self.assertEqual(
            format_tool_bullet("kitt_runtime", {"operation": "artifacts.read", "arguments": {"artifact_id": "art_40acd79ad6b94f5e88916acd1449de43"}}),
            "● Ler artefato: art_40acd79ad6b94f5e88916acd1449de43"
        )
        # repo.read
        self.assertEqual(
            format_tool_bullet("kitt_runtime", {"operation": "repo.read", "arguments": {"path": "backend/server.py", "start_line": 1, "end_line": 50}}),
            "● Ler arquivo: backend/server.py:L1-50"
        )
        # patch.apply
        self.assertEqual(
            format_tool_bullet("kitt_runtime", {"operation": "patch.apply", "arguments": {"path": "main.py"}}),
            "● Editar: main.py"
        )

    def test_terminal_event_freezes_running_tool_timer(self):
        from kitt.core.turn_events import TurnCompleted, TurnFailed, TurnCancelled, TurnStarted, ApprovalRequired

        # 1. TurnCompleted closes uncompleted tool
        state = UIState()
        reduce_ui_event(state, ToolStarted(tool_name="kitt_runtime", args={"operation": "process.run", "arguments": {"command": "rtk find ."}}))
        self.assertEqual(state.transcript[0].status, "running")
        reduce_ui_event(state, TurnCompleted())
        self.assertEqual(state.transcript[0].status, "done")
        self.assertTrue(state.transcript[0].text.endswith("✔"))
        self.assertNotIn("...", state.transcript[0].text)

        # 2. TurnFailed closes uncompleted tool
        state2 = UIState()
        reduce_ui_event(state2, ToolStarted(tool_name="kitt_runtime", args={"operation": "process.run", "arguments": {"command": "rtk find ."}}))
        reduce_ui_event(state2, TurnFailed(error="Host tool call limit exceeded for this turn."))
        self.assertEqual(state2.transcript[0].status, "error")
        self.assertTrue(state2.transcript[0].text.endswith("✖"))

        # 3. TurnCancelled closes uncompleted tool
        state3 = UIState()
        reduce_ui_event(state3, ToolStarted(tool_name="kitt_runtime", args={"operation": "process.run", "arguments": {"command": "rtk find ."}}))
        reduce_ui_event(state3, TurnCancelled(reason="Aborted"))
        self.assertEqual(state3.transcript[0].status, "cancelled")
        self.assertTrue(state3.transcript[0].text.endswith("∅"))

        # 4. ApprovalRequired pauses uncompleted tool
        state4 = UIState()
        reduce_ui_event(state4, ToolStarted(tool_name="kitt_runtime", args={"operation": "process.run", "arguments": {"command": "rtk find ."}}))
        reduce_ui_event(state4, ApprovalRequired(turn_id="t1", conversation_id="c1", tool_name="process.run", args={"command": "rtk find ."}))
        self.assertEqual(state4.transcript[0].status, "waiting_approval")
        self.assertIn("⏸ (aguardando aprovação)", state4.transcript[0].text)

        # 5. Starting a new turn freezes any leftover running blocks
        state5 = UIState()
        state5.transcript.append(state5.transcript[0] if state5.transcript else None) # empty
        state5.transcript.clear()
        reduce_ui_event(state5, ToolStarted(tool_name="kitt_runtime", args={"operation": "process.run", "arguments": {"command": "rtk find ."}}))
        self.assertEqual(state5.transcript[0].status, "running")
        reduce_ui_event(state5, TurnStarted(turn_id="t2", conversation_id="c1", prompt="onde está o arquivo gerado?"))
        self.assertEqual(state5.transcript[0].status, "done")
        self.assertTrue(state5.transcript[0].text.endswith("✔"))


if __name__ == "__main__":
    unittest.main()

