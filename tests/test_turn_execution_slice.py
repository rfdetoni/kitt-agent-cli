from __future__ import annotations

import unittest

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_execution_slice import build_execution_slice
from kitt.domain.entities import ContextPlan, SemanticTask


class TurnExecutionSliceTests(unittest.TestCase):
    @staticmethod
    def _cmd(prompt: str, mode: str = "auto") -> TurnCommand:
        return TurnCommand(conversation_id="conv-slice", prompt=prompt, mode=mode, turn_id="turn-slice")

    def test_broad_implementation_requires_one_discovery_action_first(self):
        task = SemanticTask(
            original_prompt="Build the complete application",
            intent="IMPLEMENT",
            goal="Build a complete Angular marketplace with auth, users, services and ratings.",
            actions=["scaffold", "auth", "users", "services", "ratings", "tests"],
            technologies=["angular", "typescript"],
            confidence=0.95,
        )
        execution_slice = build_execution_slice(
            self._cmd(task.original_prompt), task, ContextPlan(enabled_tools=["repo", "process"])
        )
        self.assertIsNotNone(execution_slice)
        rendered = execution_slice.render()
        self.assertIn("FIRST ACTION RULE", rendered)
        self.assertIn("exactly one read-only", rendered)
        self.assertIn("DISCOVERY -> FOUNDATION -> DOMAIN -> APPLICATION -> VERIFY", rendered)
        self.assertIn("patch.apply", rendered)

    def test_small_targeted_edit_keeps_direct_execution(self):
        task = SemanticTask(
            original_prompt="Fix src/app.py", intent="IMPLEMENT",
            goal="Fix the null guard in src/app.py", actions=["edit null guard"],
            paths=["src/app.py"], technologies=["python"], confidence=0.98,
        )
        self.assertIsNone(build_execution_slice(
            self._cmd(task.original_prompt), task, ContextPlan(enabled_tools=["repo"])
        ))

    def test_plan_mode_never_forces_execution_slice(self):
        task = SemanticTask(
            original_prompt="Plan a large rewrite", intent="REFACTOR",
            goal="Plan a large rewrite", actions=["one", "two", "three", "four", "five"], confidence=0.9,
        )
        self.assertIsNone(build_execution_slice(
            self._cmd(task.original_prompt, mode="plan"), task, ContextPlan(enabled_tools=["repo"])
        ))


if __name__ == "__main__":
    unittest.main()
