import unittest
from kitt.ui.state import UIState, AgentTaskStep
from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.components.agents_dashboard import AgentsDashboardComponent

class TestAgentsDashboard(unittest.TestCase):
    def test_render_multi_agent_dashboard(self):
        state = UIState()
        state.scanner_step = 2

        # 2 running child agents with distinct scanner_phases
        state.active_tasks = [
            AgentTaskStep(id="c1", name="Child Agent 1", role="child_agent", status="running",
                          kind="child_agent", child_id="c1", lane=1, scanner_phase=5, progress=25, summary="Indexing repo"),
            AgentTaskStep(id="c2", name="Child Agent 2", role="child_agent", status="running",
                          kind="child_agent", child_id="c2", lane=2, scanner_phase=10, progress=70, summary="Running tests"),
        ]

        rendered = AgentsDashboardComponent().render(state, width=88)

        # Assert rendering output contains 2 child entries
        self.assertEqual(rendered.count("[CHILD]"), 2)
        self.assertIn("Child Agent 1", rendered)
        self.assertIn("Child Agent 2", rendered)
        self.assertIn("AGENTES ATIVOS (2)", rendered)

        # Verify distinct scanner frames based on scanner_phase offset
        t = DEFAULT_THEME
        frame_c1 = t.scanner_frame(state.scanner_step + 5, 18)
        frame_c2 = t.scanner_frame(state.scanner_step + 10, 18)
        self.assertNotEqual(frame_c1, frame_c2)

if __name__ == "__main__":
    unittest.main()
