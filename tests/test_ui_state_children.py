import unittest
from kitt.ui.state import UIState

class TestUIStateChildren(unittest.TestCase):
    def test_upsert_child_task_and_active_count(self):
        state = UIState()
        
        # Upsert 3 distinct child tasks
        state.upsert_child_task("child-1", "Child One", "running", "Task 1 summary", 10)
        state.upsert_child_task("child-2", "Child Two", "pending", "Task 2 summary", 0)
        state.upsert_child_task("child-3", "Child Three", "running", "Task 3 summary", 50)

        child_tasks = [t for t in state.active_tasks if t.kind == "child_agent"]
        self.assertEqual(len(child_tasks), 3)

        # Check lanes and scanner_phase
        self.assertEqual(child_tasks[0].lane, 1)
        self.assertEqual(child_tasks[1].lane, 2)
        self.assertEqual(child_tasks[2].lane, 3)
        self.assertEqual(child_tasks[0].scanner_phase, 5)
        self.assertEqual(child_tasks[1].scanner_phase, 10)
        self.assertEqual(child_tasks[2].scanner_phase, 15)

        # In-place update (does not duplicate)
        state.upsert_child_task("child-1", "Child One", "done", "Task 1 complete", 100)
        child_tasks_after = [t for t in state.active_tasks if t.kind == "child_agent"]
        self.assertEqual(len(child_tasks_after), 3)
        self.assertEqual(child_tasks_after[0].status, "done")
        self.assertEqual(child_tasks_after[0].progress, 100)

        # Active count includes running & pending
        self.assertEqual(state.active_agent_count(), 2) # child-2 pending, child-3 running

if __name__ == "__main__":
    unittest.main()
