import unittest
from kitt.ui.state import UIState, AgentTaskStep
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.components.sidebar import SidebarComponent
from kitt.ui.components.status_bar import StatusBarComponent
from kitt.core.turn_events import (
    TurnStarted, BudgetApplied, ToolStarted, ToolCompleted, TextDelta, TurnCompleted
)

class TestTUITaskProgress(unittest.TestCase):
    def test_init_turn_tasks_and_progress(self):
        state = UIState()
        state.init_turn_tasks("Refatorar módulo dual-model")
        
        self.assertEqual(len(state.active_tasks), 1)
        self.assertEqual(state.active_tasks[0].id, "core")
        self.assertEqual(state.active_tasks[0].status, "running")
        self.assertGreaterEqual(state.overall_progress, 10)

    def test_reducer_updates_task_steps(self):
        state = UIState()
        
        # 1. TurnStarted
        reduce_ui_event(state, TurnStarted(turn_id="t1", conversation_id="c1", prompt="Exemplo"))
        self.assertEqual(len(state.active_tasks), 1)
        self.assertEqual(state.active_tasks[0].status, "running")

        # 2. BudgetApplied -> core updated
        reduce_ui_event(state, BudgetApplied(total_input_tokens=100))
        self.assertEqual(state.active_tasks[0].progress, 30)

        # 3. ToolStarted -> tool task dynamically added
        reduce_ui_event(state, ToolStarted(tool_name="python_compute"))
        self.assertEqual(len(state.active_tasks), 2)
        tool_task = state.active_tasks[1]
        self.assertEqual(tool_task.status, "running")
        self.assertIn("python_compute", tool_task.summary)

        # 4. ToolCompleted -> tool task done
        reduce_ui_event(state, ToolCompleted(tool_name="python_compute", success=True, output="100"))
        self.assertEqual(tool_task.status, "done")

        # 5. TextDelta -> core running
        reduce_ui_event(state, TextDelta(delta="Resposta..."))
        self.assertEqual(state.active_tasks[0].status, "running")

        # 6. TurnCompleted -> all done (100% progress)
        reduce_ui_event(state, TurnCompleted(response="Pronto."))
        self.assertEqual(state.overall_progress, 100)
        for task in state.active_tasks:
            self.assertEqual(task.status, "done")

    def test_sidebar_and_status_bar_rendering(self):
        state = UIState()
        state.init_turn_tasks("Testar renderização")
        
        sidebar_output = SidebarComponent().render(state, width=40)
        self.assertIn("AGENTES & TAREFAS", sidebar_output)
        self.assertIn("Agente Principal", sidebar_output)
        self.assertIn("Progresso", sidebar_output)

        status_output = StatusBarComponent().render(state, width=80)
        self.assertIn("Agente Principal", status_output)

if __name__ == "__main__":
    unittest.main()
