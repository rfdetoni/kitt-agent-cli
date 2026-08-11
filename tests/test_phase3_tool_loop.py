import unittest
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.agent_loop import AgentLoop

class TestPhase3ToolLoop(unittest.TestCase):
    def setUp(self):
        self.policy = PolicyEngine()
        self.loop = AgentLoop(max_steps=5, max_same_failures=2)

    def test_policy_engine_permissions(self):
        self.assertEqual(self.policy.evaluate_tool('list_files'), 'ALLOW')
        self.assertEqual(self.policy.evaluate_tool('apply_patch'), 'ASK')

        self.assertEqual(self.policy.evaluate_command('git status'), 'ALLOW')
        self.assertEqual(self.policy.evaluate_command('python3 -m unittest'), 'ASK')
        self.assertEqual(self.policy.evaluate_command('rm -rf /'), 'DENY')

    def test_agent_loop_state_transitions(self):
        self.assertEqual(self.loop.state, 'DISCOVER')
        self.assertTrue(self.loop.can_continue())

        s1 = self.loop.record_step('read_file', success=True, output="content")
        self.assertEqual(s1, 'EDIT')

        s2 = self.loop.record_step('apply_patch', success=True, output="applied")
        self.assertEqual(s2, 'VERIFY')

        s3 = self.loop.record_step('run_command', success=True, output="tests pass")
        self.assertEqual(s3, 'DONE')
        self.assertFalse(self.loop.can_continue())

    def test_repetition_failure_blocks_loop(self):
        s1 = self.loop.record_step('apply_patch', success=False, output="", error="SEARCH mismatch")
        self.assertEqual(s1, 'REPAIR')
        self.assertTrue(self.loop.can_continue())

        s2 = self.loop.record_step('apply_patch', success=False, output="", error="SEARCH mismatch")
        self.assertEqual(s2, 'BLOCKED')
        self.assertFalse(self.loop.can_continue())

if __name__ == '__main__':
    unittest.main()
